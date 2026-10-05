"""Runs the nowcast for every cycle of an event and stores the results.

    python -m vajra.forecast --event <event_id>

Layout of data/forecast/<event_id>.zarr
    coords   time (cycle times, UTC), lead_min, lat, lon
    forecast_reflectivity, storm_probability, forecast_spread   (time, lead_min, lat, lon)
    forecast_quality                                            QUALITY_* flag per cell
    motion_east, motion_north                                   (time, lat, lon) storm motion field
    available, reason, compute_seconds, motion_*                per cycle

Each cycle uses only radar scans from that cycle and earlier ones.
"""

import argparse
import json
import time
from pathlib import Path

import numpy as np
import xarray as xr

from vajra import cube, db
from vajra.config import Settings, get_settings
from vajra.cube import QUALITY_NO_DATA
from vajra.grid import build_grid
from vajra.nowcast import MINUTE, NowcastModel, PystepsNowcast, Scan
from vajra.replay import _utc, cycle_id

SCANS_NEEDED = 3
FORECAST_VARIABLES = {
    "forecast_reflectivity": ("dBZ", "Forecast reflectivity"),
    "storm_probability": ("1", "Probability of storm-level reflectivity"),
    "forecast_spread": ("dBZ", "Ensemble spread of forecast reflectivity"),
}


def forecast_path(settings: Settings, event_id: str) -> Path:
    return settings.forecast_dir / f"{event_id}.zarr"


def open_forecast(settings: Settings, event_id: str) -> xr.Dataset | None:
    path = forecast_path(settings, event_id)
    return xr.open_zarr(path, consolidated=False) if path.exists() else None


def _history(obs: xr.Dataset, index: int, max_age_min: int) -> tuple[list[Scan], str]:
    """The latest distinct radar scans available at a cycle, oldest first, or why not."""
    obs_time = obs["radar_reflectivity_obs_time"].values
    if np.isnat(obs_time[index]):
        return [], "No radar observation for this cycle"
    cycle_time = obs["time"].values[index]
    scans: list[Scan] = []
    for i in range(index, -1, -1):
        if np.isnat(obs_time[i]) or any(s.obs_time == obs_time[i] for s in scans):
            continue
        if (cycle_time - obs_time[i]) / MINUTE > max_age_min:
            break
        scans.append(
            Scan(
                obs["radar_reflectivity"].values[i],
                obs["radar_reflectivity_quality"].values[i],
                obs_time[i],
            )
        )
        if len(scans) == SCANS_NEEDED:
            return scans[::-1], ""
    return [], f"Needs {SCANS_NEEDED} recent radar scans; only {len(scans)} available"


def run_event(settings: Settings, event_id: str, model: NowcastModel | None = None) -> dict:
    grid = build_grid(settings)
    obs = cube.open_cube(settings, event_id).load()
    times = obs["time"].values
    leads = settings.cycle.lead_times_min
    model = model or PystepsNowcast(settings.nowcast, grid, settings.cycle.interval_min)

    shape = (len(times), len(leads), *grid.shape)
    fields = {name: np.full(shape, np.nan, dtype="float32") for name in FORECAST_VARIABLES}
    quality = np.full(shape, QUALITY_NO_DATA, dtype="uint8")
    motion = np.full((2, len(times), *grid.shape), np.nan, dtype="float32")
    available = np.zeros(len(times), dtype=bool)
    reasons = [""] * len(times)
    seconds = np.full(len(times), np.nan)
    speed = np.full(len(times), np.nan)
    toward = np.full(len(times), np.nan)

    for t, cycle_time in enumerate(times):
        label = str(cycle_time)[11:16]
        scans, reason = _history(obs, t, settings.nowcast.history_max_age_min)
        if not scans:
            reasons[t] = reason
            print(f"[{t + 1}/{len(times)}] {label} UTC: no forecast ({reason})")
            continue
        started = time.perf_counter()
        # A fixed seed per cycle keeps every replay identical.
        result = model.forecast(scans, cycle_time, leads, seed=settings.nowcast.seed + t)
        seconds[t] = time.perf_counter() - started
        fields["forecast_reflectivity"][t] = result.reflectivity
        fields["storm_probability"][t] = result.storm_probability
        fields["forecast_spread"][t] = result.spread
        quality[t] = result.quality
        motion[0, t], motion[1, t] = result.motion_east, result.motion_north
        available[t] = True
        speed[t], toward[t] = result.motion_speed_kmh, result.motion_toward_deg
        print(
            f"[{t + 1}/{len(times)}] {label} UTC: forecast in {seconds[t]:.1f}s, "
            f"motion {speed[t]:.0f} km/h toward {toward[t]:.0f} deg"
        )

    dims = ("time", "lead_min", "lat", "lon")
    data = {
        name: (dims, fields[name], {"units": units, "long_name": long_name})
        for name, (units, long_name) in FORECAST_VARIABLES.items()
    }
    data["forecast_quality"] = (dims, quality)
    field_dims = ("time", "lat", "lon")
    motion_attrs = {"units": "grid cells per minute"}
    data["motion_east"] = (field_dims, motion[0], motion_attrs)
    data["motion_north"] = (field_dims, motion[1], motion_attrs)
    data["available"] = (("time",), available)
    data["reason"] = (("time",), np.array(reasons, dtype=str))
    data["compute_seconds"] = (("time",), seconds)
    data["motion_speed_kmh"] = (("time",), speed)
    data["motion_toward_deg"] = (("time",), toward)
    ds = xr.Dataset(
        data,
        coords={"time": times, "lead_min": leads, "lat": grid.lats, "lon": grid.lons},
        attrs={
            "event_id": event_id,
            "model_name": model.name,
            "model_version": model.version,
            "ensemble_members": settings.nowcast.ensemble_members,
            "echo_threshold_dbz": settings.nowcast.echo_threshold_dbz,
            "storm_threshold_dbz": settings.nowcast.storm_threshold_dbz,
            "seed": settings.nowcast.seed,
        },
    )
    chunks = {"chunks": (1, 1, *grid.shape)}
    ds.to_zarr(
        forecast_path(settings, event_id),
        mode="w",
        consolidated=False,
        encoding={name: chunks for name in [*FORECAST_VARIABLES, "forecast_quality"]},
    )

    _record(settings, event_id, model, times, available, seconds, obs)
    return {
        "event_id": event_id,
        "model": model.version,
        "cycles": len(times),
        "cycles_with_forecast": int(available.sum()),
        "mean_seconds_per_forecast": round(float(np.nanmean(seconds)), 1) if available.any() else None,
    }


def _record(settings, event_id, model, times, available, seconds, obs) -> None:
    """Register the model and attach it and its latency to each forecast cycle in SQLite."""
    db.init_db(settings.db_path)
    has_obs = ~np.isnat(obs["radar_reflectivity_obs_time"].values)
    with db.connect(settings.db_path) as conn:
        conn.execute("UPDATE model_versions SET is_active = 0 WHERE kind = 'nowcast'")
        conn.execute(
            "INSERT INTO model_versions (model_version, kind, trained_at, train_events, metrics_json, is_active) "
            "VALUES (?, 'nowcast', NULL, '[]', '{}', 1) "
            "ON CONFLICT(model_version) DO UPDATE SET is_active = 1",
            (model.version,),
        )
        for t in np.flatnonzero(available):
            conn.execute(
                "INSERT INTO forecast_cycles "
                "(cycle_id, event_id, cycle_time, mode, status, model_version, latency_json, created_at) "
                "VALUES (?, ?, ?, 'replay', ?, ?, ?, ?) "
                "ON CONFLICT(cycle_id) DO UPDATE SET "
                "model_version = excluded.model_version, latency_json = excluded.latency_json",
                (
                    cycle_id(event_id, times[t]),
                    event_id,
                    _utc(times[t]).isoformat(),
                    "observed" if has_obs[t] else "no_observation",
                    model.version,
                    json.dumps({"nowcast_ms": round(float(seconds[t]) * 1000)}),
                    db.utc_now(),
                ),
            )
    db.log_event(
        settings.db_path, "info", "forecast",
        f"{model.version} → event {event_id}: {int(available.sum())}/{len(times)} cycles forecast",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the nowcast for every cycle of a storm event.")
    parser.add_argument("--event", required=True, help="event id from config.yaml")
    args = parser.parse_args()
    settings = get_settings()
    settings.ensure_dirs()
    print(run_event(settings, args.event))


if __name__ == "__main__":
    main()
