"""Zarr observation cube: one store per storm event, on the common grid.

Layout of data/cube/<event_id>.zarr
    coords   time (UTC cycle times), lat, lon
    <var>              float32 (time, lat, lon)  NaN where there is no valid value
    <var>_quality      uint8   (time, lat, lon)  QUALITY_* flag per cell
    <var>_obs_time     datetime64 (time)         time of the observation used for the
                                                 cycle; NaT if none. Data age = time - obs_time.

Missing or rejected data is never written as zero: the value stays NaN and the
quality flag says why.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import xarray as xr

from vajra.config import Settings
from vajra.grid import build_grid

QUALITY_NO_DATA = 0
QUALITY_VALID = 1
QUALITY_REJECTED = 2


@dataclass(frozen=True)
class Variable:
    source: str
    units: str
    long_name: str


# Nothing is listed here that we do not have data for.
VARIABLES: dict[str, Variable] = {
    "radar_reflectivity": Variable("radar", "dBZ", "Radar reflectivity"),
    "lightning_density": Variable(
        "lightning", "flashes per cell per cycle", "Lightning flash density"
    ),
    "satellite_brightness_temp": Variable(
        "satellite", "K", "Infrared brightness temperature (cloud-top temperature)"
    ),
    # Numerical weather prediction fields, valid at the cycle time, from the newest
    # model run that was available then.
    "model_cape": Variable("model", "J/kg", "Model surface-based CAPE"),
    "model_cin": Variable("model", "J/kg", "Model surface-based convective inhibition"),
    "model_pwat": Variable("model", "kg/m2", "Model precipitable water"),
    "model_shear_0_6km": Variable("model", "m/s", "Model 0-6 km bulk wind shear"),
    "model_reflectivity": Variable("model", "dBZ", "Model forecast composite reflectivity"),
    "model_lightning_threat": Variable("model", "1", "Model lightning threat index"),
    "lightning_density_change": Variable(
        "lightning", "flashes per cell per cycle", "Change in lightning flash density from the previous cycle"
    ),
}


def cube_path(settings: Settings, event_id: str) -> Path:
    return settings.cube_dir / f"{event_id}.zarr"


def list_events(settings: Settings) -> list[str]:
    if not settings.cube_dir.exists():
        return []
    return sorted(p.stem for p in settings.cube_dir.glob("*.zarr"))


def create_cube(settings: Settings, event_id: str, times: np.ndarray) -> Path:
    """Create an empty cube for an event. Fails if the event already exists."""
    grid = build_grid(settings)
    ds = xr.Dataset(
        coords={
            "time": np.asarray(times, dtype="datetime64[ns]"),
            "lat": grid.lats,
            "lon": grid.lons,
        },
        attrs={
            "event_id": event_id,
            "region": settings.region.name,
            "resolution_km": grid.resolution_km,
            "time_standard": "UTC",
        },
    )
    path = cube_path(settings, event_id)
    ds.to_zarr(path, mode="w-", consolidated=False)
    return path


def open_cube(settings: Settings, event_id: str) -> xr.Dataset:
    return xr.open_zarr(cube_path(settings, event_id), consolidated=False)


def write_variable(
    settings: Settings,
    event_id: str,
    name: str,
    values: np.ndarray,
    quality: np.ndarray,
    obs_time: np.ndarray,
    attrs: dict | None = None,
) -> None:
    """Write one variable (all cycles) with its quality flags and observation times.

    `attrs` records lineage (source ids, QC settings) on the variable. Writing a
    variable that already exists replaces it.
    """
    var = VARIABLES[name]
    cube = open_cube(settings, event_id)
    expected = (cube.sizes["time"], cube.sizes["lat"], cube.sizes["lon"])
    if values.shape != expected or quality.shape != expected:
        raise ValueError(f"{name}: expected shape {expected}, got {values.shape} / {quality.shape}")
    if obs_time.shape != expected[:1]:
        raise ValueError(f"{name}: obs_time must have one entry per cycle")

    dims = ("time", "lat", "lon")
    ds = xr.Dataset(
        {
            name: (dims, values.astype("float32"), {
                "units": var.units, "long_name": var.long_name, "source": var.source,
                **(attrs or {}),
            }),
            f"{name}_quality": (dims, quality.astype("uint8"), {
                "flag_values": [QUALITY_NO_DATA, QUALITY_VALID, QUALITY_REJECTED],
                "flag_meanings": "no_data valid qc_rejected",
            }),
            f"{name}_obs_time": (("time",), obs_time.astype("datetime64[ns]")),
        },
        coords=cube.coords,
        attrs=cube.attrs,  # mode="a" rewrites the store attributes, so carry them over
    )
    # Chunking is set when a variable is first written; a rewrite keeps what is there.
    chunks = {"chunks": (1, expected[1], expected[2])}
    encoding = {key: chunks for key in (name, f"{name}_quality") if key not in cube}
    ds.to_zarr(cube_path(settings, event_id), mode="a", consolidated=False, encoding=encoding)
