"""Fast read path for the UI: cached stores, per-event timeline, rendered images.

Opening a Zarr store and rendering a PNG are slow relative to a UI interaction,
so each is done once and kept. Every cache is keyed by the store's version (the
time it was last written), so rebuilding an event invalidates it. The same
version is put in image URLs, which lets the UI cache images permanently.
"""

from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np
import xarray as xr

from vajra import cube
from vajra.config import get_settings
from vajra.contracts import FieldLayer, Forecast, Timeline, TimelineCycle
from vajra.forecast import forecast_path
from vajra.grid import build_grid
from vajra.render import (
    lightning_legend,
    lightning_png,
    probability_legend,
    probability_png,
    reflectivity_legend,
    reflectivity_png,
)
from vajra.replay import cycle_id

OBSERVED_VARIABLE = "radar_reflectivity"
LIGHTNING_VARIABLE = "lightning_density"
ML_VARIABLES = ("ml_storm_probability", "ml_lightning_probability")
FORECAST_VARIABLE = "forecast_reflectivity"


def utc(value: np.datetime64) -> datetime | None:
    if np.isnat(value):
        return None
    return value.astype("datetime64[s]").item().replace(tzinfo=timezone.utc)


def _version(path: Path) -> int:
    marker = path / "zarr.json"
    return marker.stat().st_mtime_ns if marker.exists() else 0


@lru_cache(maxsize=16)
def _open(path: str, version: int) -> xr.Dataset:
    return xr.open_zarr(path, consolidated=False)


def observations(event_id: str) -> xr.Dataset | None:
    path = cube.cube_path(get_settings(), event_id)
    version = _version(path)
    return _open(str(path), version) if version else None


def forecasts(event_id: str) -> xr.Dataset | None:
    path = forecast_path(get_settings(), event_id)
    version = _version(path)
    return _open(str(path), version) if version else None


def ml_probabilities(event_id: str) -> xr.Dataset | None:
    path = _ml_path(event_id)
    version = _version(path)
    return _open(str(path), version) if version else None


def _ml_path(event_id: str) -> Path:
    # Same location vajra.ml writes to; not imported from there so the API server
    # does not load the training library.
    return get_settings().ml_dir / f"{event_id}.zarr"


def data_version(event_id: str) -> str:
    """Changes whenever the event's observations, forecasts or ML output are rebuilt."""
    settings = get_settings()
    obs = _version(cube.cube_path(settings, event_id))
    fc = _version(forecast_path(settings, event_id))
    ml = _version(_ml_path(event_id))
    return f"{obs:x}-{fc:x}-{ml:x}"


def _observation_layer(event_id: str, ds: xr.Dataset, index: int, extent, version: str) -> FieldLayer:
    frame = ds.isel(time=index)
    valid_time = frame["time"].values
    obs_time = frame[f"{OBSERVED_VARIABLE}_obs_time"].values
    age = None if np.isnat(obs_time) else float((valid_time - obs_time) / np.timedelta64(1, "m"))
    quality = frame[f"{OBSERVED_VARIABLE}_quality"].values
    return FieldLayer(
        variable=OBSERVED_VARIABLE,
        units=cube.VARIABLES[OBSERVED_VARIABLE].units,
        lead_time_min=0,
        valid_time=utc(valid_time),
        image_url=f"/events/{event_id}/observations/{OBSERVED_VARIABLE}/{index}/image.png?v={version}",
        bounds=extent,
        obs_time=utc(obs_time),
        data_age_min=age,
        valid_fraction=float((quality == cube.QUALITY_VALID).mean()),
        legend=reflectivity_legend(),
    )


def _footprint_cells() -> int:
    """Sensor footprint as an odd number of grid cells."""
    settings = get_settings()
    return max(1, round(settings.lightning.footprint_km / settings.grid.resolution_km)) | 1


def _lightning_layer(event_id: str, ds: xr.Dataset, index: int, extent, version: str) -> FieldLayer | None:
    if LIGHTNING_VARIABLE not in ds:
        return None
    frame = ds.isel(time=index)
    valid_time = frame["time"].values
    obs_time = frame[f"{LIGHTNING_VARIABLE}_obs_time"].values
    age = None if np.isnat(obs_time) else float((valid_time - obs_time) / np.timedelta64(1, "m"))
    density = frame[LIGHTNING_VARIABLE].values
    quality = frame[f"{LIGHTNING_VARIABLE}_quality"].values
    observed = bool((quality == cube.QUALITY_VALID).any())
    return FieldLayer(
        variable=LIGHTNING_VARIABLE,
        units=f"flashes within {get_settings().lightning.footprint_km:g} km",
        lead_time_min=0,
        valid_time=utc(valid_time),
        image_url=f"/events/{event_id}/observations/{LIGHTNING_VARIABLE}/{index}/image.png?v={version}",
        bounds=extent,
        obs_time=utc(obs_time),
        data_age_min=age,
        valid_fraction=float((quality == cube.QUALITY_VALID).mean()),
        legend=lightning_legend(),
        stats={"flashes": float(np.nansum(density))} if observed else {},
    )


def _forecast(event_id: str, ds: xr.Dataset | None, index: int, cycle_time, extent, version: str) -> Forecast:
    cid = cycle_id(event_id, cycle_time)
    if ds is None:
        return Forecast(
            cycle_id=cid, cycle_time=utc(cycle_time), available=False,
            reason="The nowcast has not been run for this event",
        )
    frame = ds.isel(time=index)
    common = dict(
        cycle_id=cid,
        cycle_time=utc(cycle_time),
        model_name=ds.attrs["model_name"],
        model_version=ds.attrs["model_version"],
        ensemble_members=ds.attrs["ensemble_members"],
    )
    if not bool(frame["available"].values):
        return Forecast(**common, available=False, reason=str(frame["reason"].values))
    quality = frame["forecast_quality"].values
    layers = [
        FieldLayer(
            variable=FORECAST_VARIABLE,
            units="dBZ",
            lead_time_min=int(lead),
            valid_time=utc(cycle_time + np.timedelta64(int(lead), "m")),
            image_url=f"/forecast/{cid}/{FORECAST_VARIABLE}/{int(lead)}/image.png?v={version}",
            bounds=extent,
            valid_fraction=float((quality[li] == cube.QUALITY_VALID).mean()),
            legend=reflectivity_legend(),
        )
        for li, lead in enumerate(ds["lead_min"].values)
    ]
    # ML probability layers, for the leads the model could predict at this cycle.
    ml = ml_probabilities(event_id)
    if ml is not None:
        for variable in ML_VARIABLES:
            fields = ml[variable].isel(time=index).values
            for li, lead in enumerate(ml["lead_min"].values):
                known = np.isfinite(fields[li])
                if not known.any():
                    continue
                layers.append(
                    FieldLayer(
                        variable=variable,
                        units="%",
                        lead_time_min=int(lead),
                        valid_time=utc(cycle_time + np.timedelta64(int(lead), "m")),
                        image_url=f"/forecast/{cid}/{variable}/{int(lead)}/image.png?v={version}",
                        bounds=extent,
                        valid_fraction=float(known.mean()),
                        legend=probability_legend(),
                    )
                )
    return Forecast(
        **common,
        compute_seconds=float(frame["compute_seconds"].values),
        motion_speed_kmh=float(frame["motion_speed_kmh"].values),
        motion_toward_deg=float(frame["motion_toward_deg"].values),
        layers=layers,
    )


@lru_cache(maxsize=8)
def _build(event_id: str, version: str) -> Timeline:
    obs = observations(event_id)
    fc = forecasts(event_id)
    extent = build_grid(get_settings()).extent
    cycles = [
        TimelineCycle(
            cycle_id=cycle_id(event_id, cycle_time),
            cycle_time=utc(cycle_time),
            observation=_observation_layer(event_id, obs, index, extent, version),
            lightning=_lightning_layer(event_id, obs, index, extent, version),
            forecast=_forecast(event_id, fc, index, cycle_time, extent, version),
        )
        for index, cycle_time in enumerate(obs["time"].values)
    ]
    return Timeline(event_id=event_id, cycles=cycles)


def timeline(event_id: str) -> Timeline | None:
    if observations(event_id) is None:
        return None
    return _build(event_id, data_version(event_id))


def locate_cycle(wanted_cycle_id: str) -> tuple[str, int] | None:
    """Which event and cycle index a cycle id refers to."""
    for event_id in cube.list_events(get_settings()):
        if not wanted_cycle_id.startswith(event_id):
            continue
        for index, cycle in enumerate(timeline(event_id).cycles):
            if cycle.cycle_id == wanted_cycle_id:
                return event_id, index
    return None


@lru_cache(maxsize=512)
def _observation_png(event_id: str, variable: str, index: int, version: str) -> bytes:
    frame = observations(event_id).isel(time=index)
    values, quality = frame[variable].values, frame[f"{variable}_quality"].values
    grid = build_grid(get_settings())
    if variable == LIGHTNING_VARIABLE:
        return lightning_png(values, quality, grid, _footprint_cells())
    return reflectivity_png(values, quality, grid)


def observation_png(event_id: str, variable: str, index: int) -> bytes:
    return _observation_png(event_id, variable, index, data_version(event_id))


@lru_cache(maxsize=1024)
def _forecast_png(event_id: str, variable: str, index: int, lead_index: int, version: str) -> bytes:
    grid = build_grid(get_settings())
    if variable in ML_VARIABLES:
        frame = ml_probabilities(event_id).isel(time=index, lead_min=lead_index)
        return probability_png(frame[variable].values, grid)
    frame = forecasts(event_id).isel(time=index, lead_min=lead_index)
    return reflectivity_png(frame[FORECAST_VARIABLE].values, frame["forecast_quality"].values, grid)


def forecast_png(event_id: str, variable: str, index: int, lead_index: int) -> bytes:
    return _forecast_png(event_id, variable, index, lead_index, data_version(event_id))


def warm() -> None:
    """Build every event's timeline ahead of the first request."""
    for event_id in cube.list_events(get_settings()):
        timeline(event_id)
