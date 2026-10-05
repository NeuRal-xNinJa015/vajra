"""Numerical weather prediction output → model fields on the common grid, per cycle.

A cycle may only use a model run that would already have been available: the run
must have started at least `availability_delay_min` before the cycle time. From
the newest such run, the two forecast hours either side of the cycle time give
the fields valid at the cycle time.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject

from vajra.config import NwpConfig
from vajra.grid import Grid

MINUTE = np.timedelta64(1, "m")


@dataclass(frozen=True)
class Field:
    name: str  # cube variable
    # Smooth environment fields are interpolated in time. Storm-scale fields are
    # taken from the nearest forecast hour instead: blending two positions of a
    # moving storm would give two weakened copies of it.
    interpolate: bool


FIELDS = [
    Field("model_cape", True),
    Field("model_cin", True),
    Field("model_pwat", True),
    Field("model_shear_0_6km", True),
    Field("model_reflectivity", False),
    Field("model_lightning_threat", False),
]


@dataclass
class ModelForecast:
    """One forecast hour of one model run, on the common grid."""

    init_time: np.datetime64
    valid_time: np.datetime64
    fields: dict[str, np.ndarray]  # cube variable → (lat, lon) float32


@dataclass
class ModelAtCycle:
    fields: dict[str, np.ndarray]
    init_time: np.datetime64  # start of the model run used


# HRRR surface file: (GRIB element, level) for each message this reader needs.
_HRRR = {
    ("CAPE", "0-SFC"): "model_cape",
    ("CIN", "0-SFC"): "model_cin",
    ("PWAT", "0-EATM"): "model_pwat",
    ("REFC", "0-EATM"): "model_reflectivity",
    ("LTNG", "0-EATM"): "model_lightning_threat",
    ("VUCSH", "0-6000-HTGL"): "_shear_u",
    ("VVCSH", "0-6000-HTGL"): "_shear_v",
}


def _read_hrrr_grib2(path: Path, grid: Grid) -> ModelForecast:
    """NOAA HRRR GRIB2 output, read through GDAL (rasterio) and resampled to the grid."""
    west, south, _, north = grid.extent
    height, width = grid.shape
    # GDAL writes north-up, so resample to a north-up array and flip it afterwards.
    target = from_origin(west, north, grid.dlon, grid.dlat)
    out: dict[str, np.ndarray] = {}
    with rasterio.open(path) as ds:
        # Longitude/latitude on the model's own sphere, so no datum shift is applied.
        sphere = ds.crs.to_dict().get("R", 6371229)
        lonlat = f"+proj=longlat +R={sphere} +no_defs"
        init = valid = None
        for band in range(1, ds.count + 1):
            tags = ds.tags(band)
            name = _HRRR.get((tags.get("GRIB_ELEMENT"), tags.get("GRIB_SHORT_NAME")))
            if name is None:
                continue
            init = np.datetime64(int(tags["GRIB_REF_TIME"]), "s").astype("datetime64[ns]")
            valid = np.datetime64(int(tags["GRIB_VALID_TIME"]), "s").astype("datetime64[ns]")
            field = np.full((height, width), np.nan, dtype="float32")
            reproject(
                source=rasterio.band(ds, band),
                destination=field,
                dst_transform=target,
                dst_crs=lonlat,
                dst_nodata=np.nan,
                resampling=Resampling.bilinear,
            )
            out[name] = field[::-1].copy()
    missing = set(_HRRR.values()) - set(out)
    if missing:
        raise ValueError(f"{path.name}: missing fields {sorted(missing)}")
    # The file labels these components 1/s, but the values are the wind difference
    # across the layer in m/s.
    out["model_shear_0_6km"] = np.hypot(out.pop("_shear_u"), out.pop("_shear_v"))
    return ModelForecast(init, valid, out)


# format name → file reader. Add a line here for a new model product.
READERS: dict[str, Callable[[Path, Grid], ModelForecast]] = {
    "hrrr_grib2": _read_hrrr_grib2,
}


def read_files(folder: Path, cfg: NwpConfig, grid: Grid) -> tuple[list[ModelForecast], list[str]]:
    """Read every model file in a folder. Returns the forecasts and the names that failed."""
    if cfg.format not in READERS:
        raise ValueError(f"model format '{cfg.format}' has no reader; known: {sorted(READERS)}")
    reader = READERS[cfg.format]
    forecasts: list[ModelForecast] = []
    unreadable: list[str] = []
    for path in sorted(p for p in folder.iterdir() if p.is_file()):
        try:
            forecasts.append(reader(path, grid))
        except Exception:  # a damaged file must not stop the event
            unreadable.append(path.name)
    return forecasts, unreadable


def at_cycle(forecasts: list[ModelForecast], cycle_time: np.datetime64, cfg: NwpConfig) -> ModelAtCycle | None:
    """Model fields valid at a cycle, from the newest run available then, or None."""
    latest_start = cycle_time - np.timedelta64(cfg.availability_delay_min, "m")
    runs = sorted({f.init_time for f in forecasts if f.init_time <= latest_start}, reverse=True)
    for init_time in runs:
        hours = [f for f in forecasts if f.init_time == init_time]
        before = [f for f in hours if f.valid_time <= cycle_time]
        after = [f for f in hours if f.valid_time >= cycle_time]
        if not before or not after:
            continue  # this run does not span the cycle time; try an older one
        earlier = max(before, key=lambda f: f.valid_time)
        later = min(after, key=lambda f: f.valid_time)
        span = float((later.valid_time - earlier.valid_time) / MINUTE)
        weight = 0.0 if span == 0 else float((cycle_time - earlier.valid_time) / MINUTE) / span
        nearest = later if weight > 0.5 else earlier
        fields = {
            field.name: (
                (1 - weight) * earlier.fields[field.name] + weight * later.fields[field.name]
                if field.interpolate
                else nearest.fields[field.name]
            ).astype("float32")
            for field in FIELDS
        }
        return ModelAtCycle(fields, init_time)
    return None
