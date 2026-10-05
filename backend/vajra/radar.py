"""Radar volume → quality-controlled column-maximum reflectivity on the common grid.

Decode (xradar) → gate QC → range-average to the grid resolution → polar-to-grid
lookup per sweep → column maximum → speckle filter → values + quality flags.

"No echo" is an observation, not missing data: where the radar looked and saw
nothing, the cell is VALID with the value NO_ECHO_DBZ. Cells the radar did not
cover are NO_DATA, and cells whose only echo failed QC are REJECTED; both hold NaN.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import xarray as xr
import xradar as xd
from pyproj import Geod
from scipy import ndimage

from vajra.config import RadarConfig
from vajra.cube import QUALITY_NO_DATA, QUALITY_REJECTED, QUALITY_VALID
from vajra.grid import Grid

NO_ECHO_DBZ = -32.0
MAX_PLAUSIBLE_DBZ = 80.0
# Standard 4/3 effective earth radius for beam propagation.
EFFECTIVE_EARTH_RADIUS_M = 6_371_000.0 * 4.0 / 3.0


@dataclass(frozen=True)
class RadarSite:
    radar_id: str
    lat: float
    lon: float
    alt_m: float


@dataclass
class Sweep:
    """One elevation scan, normalised across file formats."""

    elevation_deg: float
    azimuth_deg: np.ndarray  # (rays,), ascending
    range_m: np.ndarray  # (gates,), evenly spaced
    dbz: np.ndarray  # (rays, gates)
    no_echo: np.ndarray  # radar observed nothing above its detection threshold
    unusable: np.ndarray  # the file marks the gate as not measurable
    rhohv: np.ndarray | None  # NaN where not available


@dataclass
class Volume:
    site: RadarSite
    start_time: np.datetime64
    end_time: np.datetime64
    sweeps: list[Sweep]


@dataclass
class GriddedVolume:
    site: RadarSite
    start_time: np.datetime64
    end_time: np.datetime64
    values: np.ndarray  # (lat, lon) float32 dBZ, NaN where not VALID
    quality: np.ndarray  # (lat, lon) uint8 QUALITY_* flags


# ---- format-specific decoding -------------------------------------------

def _nexrad_codes(field: xr.DataArray) -> tuple[np.ndarray, np.ndarray]:
    """NEXRAD Level II reserves raw code 0 for "below threshold" and 1 for "range folded".

    xradar scales those codes like data, so recover them from the field's own encoding.
    """
    values = field.values
    scale = field.encoding["scale_factor"]
    offset = field.encoding["add_offset"]
    below_threshold = values < offset + 0.5 * scale
    range_folded = ~below_threshold & (values < offset + 1.5 * scale)
    return below_threshold, range_folded


def _decode_nexrad_sweep(ds: xr.Dataset, cfg: RadarConfig) -> Sweep:
    dbz_field = ds[cfg.reflectivity_field]
    no_echo, unusable = _nexrad_codes(dbz_field)
    rhohv = None
    if cfg.correlation_field and cfg.correlation_field in ds:
        rho_field = ds[cfg.correlation_field]
        below, folded = _nexrad_codes(rho_field)
        rhohv = np.where(below | folded, np.nan, rho_field.values).astype("float32")
    return Sweep(
        elevation_deg=float(ds["sweep_fixed_angle"]),
        azimuth_deg=ds["azimuth"].values.astype("float64"),
        range_m=ds["range"].values.astype("float64"),
        dbz=dbz_field.values.astype("float32"),
        no_echo=no_echo,
        unusable=unusable,
        rhohv=rhohv,
    )


# format name → (xradar opener, sweep decoder). Add a line here for a new radar format.
READERS: dict[str, tuple[Callable, Callable[[xr.Dataset, RadarConfig], Sweep]]] = {
    "nexradlevel2": (xd.io.open_nexradlevel2_datatree, _decode_nexrad_sweep),
}


def _parse_time(value) -> np.datetime64:
    return np.datetime64(str(np.asarray(value).item()).rstrip("Z"), "ns")


def read_volume(path: Path, cfg: RadarConfig) -> Volume:
    if cfg.format not in READERS:
        raise ValueError(f"radar format '{cfg.format}' has no reader; known: {sorted(READERS)}")
    opener, decode_sweep = READERS[cfg.format]
    tree = opener(path)
    try:
        root = tree.ds
        site = RadarSite(
            radar_id=str(root.attrs.get("instrument_name") or path.parent.name),
            lat=float(root["latitude"]),
            lon=float(root["longitude"]),
            alt_m=float(root["altitude"]),
        )
        names = sorted(
            (n for n in tree.children if n.startswith("sweep_")),
            key=lambda n: int(n.split("_")[1]),
        )
        datasets = [tree[n].ds for n in names]
        datasets = [ds for ds in datasets if cfg.reflectivity_field in ds]
        # Where some sweeps carry the QC field, use only those: the others repeat
        # the same elevations and would let unchecked echo through.
        if cfg.correlation_field:
            checked = [ds for ds in datasets if cfg.correlation_field in ds]
            datasets = checked or datasets
        # Volumes may revisit low elevations; keep the first scan of each so the
        # field matches the volume start time.
        sweeps: list[Sweep] = []
        seen: set[float] = set()
        for ds in datasets:
            elevation = round(float(ds["sweep_fixed_angle"]), 1)
            if elevation in seen:
                continue
            seen.add(elevation)
            sweeps.append(decode_sweep(ds, cfg))
        if not sweeps:
            raise ValueError(f"{path.name}: no sweep contains '{cfg.reflectivity_field}'")
        return Volume(
            site=site,
            start_time=_parse_time(root["time_coverage_start"].values),
            end_time=_parse_time(root["time_coverage_end"].values),
            sweeps=sweeps,
        )
    finally:
        tree.close()


# ---- quality control and gridding ---------------------------------------

@dataclass
class GridGeometry:
    """Azimuth and ground distance from one radar to every grid cell."""

    azimuth_deg: np.ndarray
    ground_range_m: np.ndarray

    @classmethod
    def build(cls, site: RadarSite, grid: Grid) -> "GridGeometry":
        lon2d, lat2d = np.meshgrid(grid.lons, grid.lats)
        azimuth, _, distance = Geod(ellps="WGS84").inv(
            np.full_like(lon2d, site.lon), np.full_like(lat2d, site.lat), lon2d, lat2d
        )
        return cls(azimuth_deg=np.mod(azimuth, 360.0), ground_range_m=distance)


def _range_average(sweep: Sweep, cfg: RadarConfig, window: int):
    """QC the gates, then average reflectivity along range to the grid resolution.

    Averaging is done on linear reflectivity, with accepted no-echo gates counted
    as zero and rejected gates left out. Returns (dBZ, observed, rejected) per gate.
    """
    echo = ~sweep.no_echo & ~sweep.unusable & np.isfinite(sweep.dbz)
    rejected = sweep.unusable | (echo & ((sweep.dbz < NO_ECHO_DBZ) | (sweep.dbz > MAX_PLAUSIBLE_DBZ)))
    if sweep.rhohv is not None:
        with np.errstate(invalid="ignore"):
            rejected |= echo & (sweep.rhohv < cfg.rhohv_min)
    accepted_echo = echo & ~rejected
    observed = accepted_echo | (sweep.no_echo & ~rejected)

    linear = np.where(accepted_echo, 10.0 ** (sweep.dbz / 10.0), 0.0)

    def mean(a: np.ndarray) -> np.ndarray:
        return ndimage.uniform_filter1d(a.astype("float64"), window, axis=1, mode="constant")

    observed_fraction = mean(observed)
    rejected_fraction = mean(rejected)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean_linear = mean(linear) / observed_fraction
        dbz = 10.0 * np.log10(mean_linear)
    is_observed = observed_fraction >= 0.5
    dbz = np.where(is_observed & np.isfinite(dbz), np.maximum(dbz, NO_ECHO_DBZ), NO_ECHO_DBZ)
    is_rejected = ~is_observed & (rejected_fraction > 0)
    return dbz.astype("float32"), is_observed, is_rejected


def _nearest_ray(azimuth_deg: np.ndarray, target_deg: np.ndarray):
    """Index of the ray closest in azimuth to each target, and the angular distance."""
    n = len(azimuth_deg)
    upper = np.searchsorted(azimuth_deg, target_deg) % n
    lower = (upper - 1) % n

    def separation(index: np.ndarray) -> np.ndarray:
        return np.abs((target_deg - azimuth_deg[index] + 180.0) % 360.0 - 180.0)

    d_upper, d_lower = separation(upper), separation(lower)
    use_lower = d_lower < d_upper
    return np.where(use_lower, lower, upper), np.where(use_lower, d_lower, d_upper)


def grid_volume(volume: Volume, grid: Grid, cfg: RadarConfig, geometry: GridGeometry) -> GriddedVolume:
    shape = grid.shape
    best = np.full(shape, -np.inf, dtype="float32")
    any_observed = np.zeros(shape, dtype=bool)
    any_rejected = np.zeros(shape, dtype=bool)
    in_range = geometry.ground_range_m <= cfg.max_range_km * 1000.0
    arc = geometry.ground_range_m / EFFECTIVE_EARTH_RADIUS_M

    for sweep in volume.sweeps:
        gate_m = float(sweep.range_m[1] - sweep.range_m[0])
        window = max(1, round(grid.resolution_km * 1000.0 / gate_m))
        dbz, observed, rejected = _range_average(sweep, cfg, window)

        # Slant range at which this elevation's beam is above each cell (4/3 earth).
        beam_angle = np.deg2rad(sweep.elevation_deg) + arc
        with np.errstate(divide="ignore", invalid="ignore"):
            slant = EFFECTIVE_EARTH_RADIUS_M * np.sin(arc) / np.cos(beam_angle)
        gate = np.rint((slant - sweep.range_m[0]) / gate_m).astype("int64")
        ray, separation = _nearest_ray(sweep.azimuth_deg, geometry.azimuth_deg)
        ray_spacing = float(np.median(np.diff(sweep.azimuth_deg)))
        covered = (
            in_range
            & (beam_angle < np.pi / 2)
            & (gate >= 0)
            & (gate < len(sweep.range_m))
            & (separation <= 1.5 * ray_spacing)
        )
        gate = np.where(covered, gate, 0)

        cell_observed = covered & observed[ray, gate]
        best = np.where(cell_observed, np.maximum(best, dbz[ray, gate]), best)
        any_observed |= cell_observed
        any_rejected |= covered & rejected[ray, gate]

    quality = np.full(shape, QUALITY_NO_DATA, dtype="uint8")
    quality[any_rejected] = QUALITY_REJECTED
    quality[any_observed] = QUALITY_VALID
    values = np.where(any_observed, best, np.nan).astype("float32")

    # Speckle filter: isolated echo smaller than a few cells is not a storm.
    echo = any_observed & (values > NO_ECHO_DBZ)
    labels, count = ndimage.label(echo, structure=np.ones((3, 3), dtype=int))
    if count:
        sizes = np.bincount(labels.ravel())
        speckle = echo & (sizes[labels] < cfg.speckle_min_cells)
        values[speckle] = np.nan
        quality[speckle] = QUALITY_REJECTED

    return GriddedVolume(volume.site, volume.start_time, volume.end_time, values, quality)
