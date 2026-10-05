"""Feature fusion: one store of ML features and labels per event, from all four sources.

    python -m vajra.features --event <event_id>

Features for a cycle use only data available at that cycle: the cycle's own
observations, the previous cycle's (for trends), and the nowcast issued at the
cycle. Labels come from what was observed at cycle time + lead time. The builder
is deterministic, so the same inputs always give the same store.

Layout of data/features/<event_id>.zarr
    coords   time (cycle), lead_min, lat, lon
    <feature>                 (time, lat, lon) or (time, lead_min, lat, lon)
    label_storm, label_lightning   (time, lead_min, lat, lon): 1, 0, or NaN if unknown
    has_forecast              (time): the cycle has a nowcast, so its lead features exist
"""

import argparse
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr
from scipy import ndimage

from vajra import cube, db
from vajra.config import Settings, get_settings
from vajra.cube import QUALITY_VALID
from vajra.forecast import open_forecast
from vajra.grid import build_grid

MINUTE = np.timedelta64(1, "m")
# Model storms are often tens of km out of place, so model storm fields are read
# over a wider neighbourhood than observations.
MODEL_NEIGHBOURHOOD_KM = 20.0
# A radar scan counts as the truth for a valid time only if measured this close to it.
LABEL_TOLERANCE_MIN = 4.0


@dataclass(frozen=True)
class Feature:
    name: str
    source: str  # radar, lightning, satellite, model or nowcast
    description: str
    per_lead: bool = False  # True if the value depends on the forecast lead time


FEATURES: list[Feature] = [
    Feature("refl", "radar", "Reflectivity at the cell"),
    Feature("refl_nbr_max", "radar", "Highest reflectivity within the neighbourhood"),
    Feature("refl_nbr_mean", "radar", "Mean reflectivity within the neighbourhood"),
    Feature("refl_trend", "radar", "Change in reflectivity since the previous cycle"),
    Feature("ltg_nbr", "lightning", "Flashes within the neighbourhood, last cycle"),
    Feature("ltg_nbr_30min", "lightning", "Flashes within the neighbourhood, last three cycles"),
    Feature("ltg_trend", "lightning", "Change in neighbourhood flashes since the previous cycle"),
    Feature("bt", "satellite", "Cloud-top brightness temperature at the cell"),
    Feature("bt_nbr_min", "satellite", "Coldest cloud top within the neighbourhood"),
    Feature("bt_trend", "satellite", "Change in cloud-top temperature since the previous cycle"),
    Feature("cape", "model", "Model CAPE (instability)"),
    Feature("cin", "model", "Model convective inhibition"),
    Feature("pwat", "model", "Model precipitable water"),
    Feature("shear", "model", "Model 0-6 km wind shear"),
    Feature("model_refl_nbr_max", "model", "Highest model forecast reflectivity nearby"),
    Feature("model_ltg_nbr_max", "model", "Highest model lightning threat nearby"),
    Feature("refl_extrap", "nowcast", "Reflectivity carried to the lead time by storm motion", True),
    Feature("steps_storm_prob", "nowcast", "Ensemble probability of storm-level reflectivity", True),
    Feature("steps_spread", "nowcast", "Ensemble spread of forecast reflectivity", True),
    Feature("ltg_extrap", "nowcast", "Neighbourhood flashes carried to the lead time by storm motion", True),
    Feature("bt_extrap", "nowcast", "Coldest nearby cloud top carried to the lead time by storm motion", True),
]
FEATURE_NAMES = [f.name for f in FEATURES]
LABELS = ["label_storm", "label_lightning"]


def features_path(settings: Settings, event_id: str) -> Path:
    return settings.features_dir / f"{event_id}.zarr"


def open_features(settings: Settings, event_id: str) -> xr.Dataset | None:
    path = features_path(settings, event_id)
    return xr.open_zarr(path, consolidated=False) if path.exists() else None


def _odd_cells(km: float, resolution_km: float) -> int:
    return max(1, round(km / resolution_km)) | 1


def _nan_filter(field: np.ndarray, size: int, how: str) -> np.ndarray:
    """Neighbourhood max / min / mean / sum that ignores NaN; NaN where nothing is valid."""
    valid = np.isfinite(field)
    if how == "max":
        out = ndimage.maximum_filter(np.where(valid, field, -np.inf), size=size, mode="nearest")
    elif how == "min":
        out = ndimage.minimum_filter(np.where(valid, field, np.inf), size=size, mode="nearest")
    else:
        total = ndimage.uniform_filter(np.where(valid, field, 0.0).astype("float64"), size=size, mode="constant")
        count = ndimage.uniform_filter(valid.astype("float64"), size=size, mode="constant")
        with np.errstate(invalid="ignore", divide="ignore"):
            out = total / count if how == "mean" else total * size * size
        out = np.where(count > 0, out, np.nan)
    return np.where(np.isfinite(out), out, np.nan).astype("float32")


def _advect(field: np.ndarray, east: np.ndarray, north: np.ndarray, minutes: float) -> np.ndarray:
    """Carry a field along the storm motion (grid cells per minute) for some minutes."""
    rows, cols = np.indices(field.shape, dtype="float64")
    source = [rows - north * minutes, cols - east * minutes]
    return ndimage.map_coordinates(field, source, order=1, mode="constant", cval=np.nan).astype("float32")


def _valid(obs: xr.Dataset, name: str, t: int) -> np.ndarray:
    """A cube variable at a cycle, NaN wherever it is not flagged valid."""
    values = obs[name].values[t]
    return np.where(obs[f"{name}_quality"].values[t] == QUALITY_VALID, values, np.nan)


def build_cycle(obs: xr.Dataset, forecast: xr.Dataset | None, t: int, settings: Settings) -> dict[str, np.ndarray]:
    """Every feature for cycle t. Reads only cycles t-2..t of the observations and
    the nowcast issued at t, never anything later."""
    res = settings.grid.resolution_km
    near = _odd_cells(settings.lightning.footprint_km, res)
    wide = _odd_cells(MODEL_NEIGHBOURHOOD_KM, res)
    leads = settings.cycle.lead_times_min
    shape = obs["radar_reflectivity"].shape[1:]
    out: dict[str, np.ndarray] = {}

    def previous(name: str) -> np.ndarray:
        return _valid(obs, name, t - 1) if t > 0 else np.full(shape, np.nan, dtype="float32")

    refl = _valid(obs, "radar_reflectivity", t)
    out["refl"] = refl
    out["refl_nbr_max"] = _nan_filter(refl, near, "max")
    out["refl_nbr_mean"] = _nan_filter(refl, near, "mean")
    out["refl_trend"] = refl - previous("radar_reflectivity")

    ltg = _nan_filter(_valid(obs, "lightning_density", t), near, "sum")
    ltg_before = _nan_filter(previous("lightning_density"), near, "sum")
    out["ltg_nbr"] = ltg
    out["ltg_trend"] = ltg - ltg_before
    recent = [_valid(obs, "lightning_density", i) for i in range(max(0, t - 2), t + 1)]
    out["ltg_nbr_30min"] = (
        _nan_filter(np.sum(recent, axis=0), near, "sum") if len(recent) == 3 else np.full(shape, np.nan, "float32")
    )

    bt = _valid(obs, "satellite_brightness_temp", t)
    bt_min = _nan_filter(bt, near, "min")
    out["bt"] = bt
    out["bt_nbr_min"] = bt_min
    out["bt_trend"] = bt - previous("satellite_brightness_temp")

    out["cape"] = _valid(obs, "model_cape", t)
    out["cin"] = _valid(obs, "model_cin", t)
    out["pwat"] = _valid(obs, "model_pwat", t)
    out["shear"] = _valid(obs, "model_shear_0_6km", t)
    out["model_refl_nbr_max"] = _nan_filter(_valid(obs, "model_reflectivity", t), wide, "max")
    out["model_ltg_nbr_max"] = _nan_filter(_valid(obs, "model_lightning_threat", t), wide, "max")

    per_lead = np.full((len(leads), *shape), np.nan, dtype="float32")
    for name in ("refl_extrap", "steps_storm_prob", "steps_spread", "ltg_extrap", "bt_extrap"):
        out[name] = per_lead.copy()
    if forecast is not None and bool(forecast["available"].values[t]):
        out["refl_extrap"] = forecast["forecast_reflectivity"].values[t]
        out["steps_storm_prob"] = forecast["storm_probability"].values[t]
        out["steps_spread"] = forecast["forecast_spread"].values[t]
        east, north = forecast["motion_east"].values[t], forecast["motion_north"].values[t]
        cycle_time = obs["time"].values[t]
        bt_age = float((cycle_time - obs["satellite_brightness_temp_obs_time"].values[t]) / MINUTE)
        for li, lead in enumerate(leads):
            # The flash window and the label window have the same length, so the
            # displacement between them is the lead time.
            out["ltg_extrap"][li] = _advect(ltg, east, north, lead)
            if np.isfinite(bt_age):
                out["bt_extrap"][li] = _advect(bt_min, east, north, lead + bt_age)
    return out


def build_labels(obs: xr.Dataset, t: int, settings: Settings) -> dict[str, np.ndarray]:
    """What was observed at cycle t + lead: 1 or 0 per cell, NaN where it is not known."""
    near = _odd_cells(settings.lightning.footprint_km, settings.grid.resolution_km)
    leads = settings.cycle.lead_times_min
    times = obs["time"].values
    radar_time = obs["radar_reflectivity_obs_time"].values
    shape = (len(leads), *obs["radar_reflectivity"].shape[1:])
    storm = np.full(shape, np.nan, dtype="float32")
    lightning = np.full(shape, np.nan, dtype="float32")
    for li, lead in enumerate(leads):
        valid_time = times[t] + np.timedelta64(int(lead), "m")
        # Storm truth: the radar scan measured closest to the valid time.
        gap = np.abs((radar_time - valid_time) / MINUTE)
        gap = np.where(np.isnat(radar_time), np.inf, gap)
        k = int(np.argmin(gap))
        if gap[k] <= LABEL_TOLERANCE_MIN:
            refl = _valid(obs, "radar_reflectivity", k)
            storm[li] = np.where(np.isfinite(refl), refl >= settings.nowcast.storm_threshold_dbz, np.nan)
        # Lightning truth: any flash within the neighbourhood in the cycle ending at the valid time.
        match = np.flatnonzero(times == valid_time)
        if match.size:
            flashes = _nan_filter(_valid(obs, "lightning_density", int(match[0])), near, "sum")
            lightning[li] = np.where(np.isfinite(flashes), flashes >= 1, np.nan)
    return {"label_storm": storm, "label_lightning": lightning}


def run_event(settings: Settings, event_id: str) -> dict:
    grid = build_grid(settings)
    obs = cube.open_cube(settings, event_id).load()
    forecast = open_forecast(settings, event_id)
    if forecast is not None:
        forecast = forecast.load()
    times = obs["time"].values
    leads = settings.cycle.lead_times_min
    flat = (len(times), *grid.shape)
    deep = (len(times), len(leads), *grid.shape)

    arrays = {
        f.name: np.full(deep if f.per_lead else flat, np.nan, dtype="float32") for f in FEATURES
    }
    arrays.update({name: np.full(deep, np.nan, dtype="float32") for name in LABELS})
    for t in range(len(times)):
        for name, values in {**build_cycle(obs, forecast, t, settings), **build_labels(obs, t, settings)}.items():
            arrays[name][t] = values

    has_forecast = (
        forecast["available"].values.astype(bool) if forecast is not None else np.zeros(len(times), dtype=bool)
    )
    described = {f.name: f for f in FEATURES}
    data = {}
    for name, values in arrays.items():
        dims = ("time", "lead_min", "lat", "lon") if values.ndim == 4 else ("time", "lat", "lon")
        attrs = {"source": described[name].source, "long_name": described[name].description} if name in described else {}
        data[name] = (dims, values, attrs)
    data["has_forecast"] = (("time",), has_forecast)
    ds = xr.Dataset(
        data,
        coords={"time": times, "lead_min": leads, "lat": grid.lats, "lon": grid.lons},
        attrs={
            "event_id": event_id,
            "neighbourhood_km": settings.lightning.footprint_km,
            "model_neighbourhood_km": MODEL_NEIGHBOURHOOD_KM,
            "storm_threshold_dbz": settings.nowcast.storm_threshold_dbz,
        },
    )
    encoding = {
        name: {"chunks": (1, 1, *grid.shape) if values.ndim == 4 else (1, *grid.shape)}
        for name, values in arrays.items()
    }
    ds.to_zarr(features_path(settings, event_id), mode="w", consolidated=False, encoding=encoding)

    summary = {"event_id": event_id, "features": len(FEATURES), "cycles": len(times), "by_lead": {}}
    for li, lead in enumerate(leads):
        usable = has_forecast[:, None, None]
        for label in LABELS:
            known = np.isfinite(arrays[label][:, li]) & usable
            summary["by_lead"].setdefault(int(lead), {})[label] = {
                "cycles": int(known.any(axis=(1, 2)).sum()),
                "cells": int(known.sum()),
                "positive_rate": round(float(arrays[label][:, li][known].mean()), 4) if known.any() else None,
            }
    db.init_db(settings.db_path)
    db.log_event(settings.db_path, "info", "features", f"event {event_id}: {len(FEATURES)} features built")
    return summary


def table(settings: Settings, event_id: str, lead_min: int) -> pd.DataFrame:
    """Training table for one lead time: one row per grid cell per cycle that has a
    nowcast, with every feature, both labels, and where and when the row is from."""
    ds = open_features(settings, event_id)
    if ds is None:
        raise FileNotFoundError(f"features have not been built for event '{event_id}'")
    ds = ds.sel(lead_min=lead_min).isel(time=ds["has_forecast"].values.astype(bool))
    columns = {name: ds[name].values.ravel() for name in [*FEATURE_NAMES, *LABELS]}
    time, lat, lon = np.meshgrid(ds["time"].values, ds["lat"].values, ds["lon"].values, indexing="ij")
    return pd.DataFrame({"cycle_time": time.ravel(), "lat": lat.ravel(), "lon": lon.ravel(), **columns})


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the ML feature store for a storm event.")
    parser.add_argument("--event", required=True, help="event id from config.yaml")
    args = parser.parse_args()
    settings = get_settings()
    settings.ensure_dirs()
    summary = run_event(settings, args.event)
    print(f"{summary['features']} features over {summary['cycles']} cycles for {summary['event_id']}")
    for lead, labels in summary["by_lead"].items():
        for label, stats in labels.items():
            print(f"  +{lead:3d} min  {label:16s} cycles {stats['cycles']:2d}  cells {stats['cells']:8d}  positive {stats['positive_rate']}")


if __name__ == "__main__":
    main()
