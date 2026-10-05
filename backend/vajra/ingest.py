"""Build the observation cube for a storm event from the raw files on disk.

    python -m vajra.ingest --event <event_id> [--source radar|lightning|satellite|model] [--radar <radar_id>]

Each cycle uses only data that existed by the cycle time: the newest radar
volume and satellite scan that had finished, the lightning flashes detected in
the interval before it, and the newest model run already available. Nothing
from after the cycle time is ever used.
"""

import argparse
import time
from datetime import timezone
from pathlib import Path

import numpy as np

from vajra import cube, db, satellite
from vajra.config import EventConfig, Settings, get_settings
from vajra.cube import QUALITY_NO_DATA, QUALITY_VALID
from vajra.grid import build_grid
from vajra.lightning import grid_cycle, read_files
from vajra.radar import NO_ECHO_DBZ, GridGeometry, GriddedVolume, grid_volume, read_volume


def to_datetime64(value) -> np.datetime64:
    """Timezone-aware datetime → naive UTC numpy time, as stored in the cube."""
    return np.datetime64(value.astimezone(timezone.utc).replace(tzinfo=None), "ns")


def cycle_times(settings: Settings, event: EventConfig) -> np.ndarray:
    step = np.timedelta64(settings.cycle.interval_min, "m")
    start, end = to_datetime64(event.start), to_datetime64(event.end)
    return np.arange(start, end + step, step)


def _pick_radar(settings: Settings, radar_id: str | None) -> Path:
    root = settings.source_dir("radar")
    folders = sorted(p for p in root.iterdir() if p.is_dir() and any(p.iterdir()))
    if radar_id:
        folder = root / radar_id
        if folder not in folders:
            raise SystemExit(f"no radar files found in {folder}")
        return folder
    if len(folders) != 1:
        names = [f.name for f in folders]
        raise SystemExit(f"expected one radar folder in {root}, found {names}; pass --radar")
    return folders[0]


def ingest_radar(settings: Settings, event_id: str, radar_id: str | None = None) -> dict:
    event = settings.event(event_id)
    folder = _pick_radar(settings, radar_id)
    grid = build_grid(settings)
    times = cycle_times(settings, event)
    max_age = np.timedelta64(settings.cycle.max_data_age_min, "m")

    volumes: list[GriddedVolume] = []
    unreadable: list[str] = []
    geometry: GridGeometry | None = None
    files = sorted(p for p in folder.iterdir() if p.is_file())
    for i, path in enumerate(files, 1):
        started = time.perf_counter()
        try:
            volume = read_volume(path, settings.radar)
        except Exception as err:  # a damaged volume must not stop the event
            # Its cycle falls back to the previous volume, or to "no data" if that is too old.
            unreadable.append(path.name)
            reason = str(err).splitlines()[0][:120]
            print(f"[{i}/{len(files)}] {path.name}: UNREADABLE, skipped ({type(err).__name__}: {reason})")
            continue
        # Skip volumes that cannot serve any cycle of this event.
        if volume.end_time > times[-1] or volume.start_time < times[0] - max_age:
            print(f"[{i}/{len(files)}] {path.name}: outside the event window, skipped")
            continue
        if geometry is None:
            geometry = GridGeometry.build(volume.site, grid)
        gridded = grid_volume(volume, grid, settings.radar, geometry)
        volumes.append(gridded)
        valid = gridded.quality == QUALITY_VALID
        print(
            f"[{i}/{len(files)}] {path.name}: {str(volume.start_time)[11:19]}-"
            f"{str(volume.end_time)[11:19]} UTC, valid {valid.mean():.0%}, "
            f"max {np.nanmax(gridded.values):.1f} dBZ, {time.perf_counter() - started:.1f}s"
        )
    if not volumes:
        raise SystemExit(f"no radar volume in {folder} falls inside event '{event_id}'")

    shape = (len(times), *grid.shape)
    values = np.full(shape, np.nan, dtype="float32")
    quality = np.full(shape, QUALITY_NO_DATA, dtype="uint8")
    obs_time = np.full(len(times), np.datetime64("NaT"), dtype="datetime64[ns]")
    for t, cycle_time in enumerate(times):
        available = [v for v in volumes if v.end_time <= cycle_time]
        if not available:
            continue
        latest = max(available, key=lambda v: v.start_time)
        if cycle_time - latest.start_time > max_age:
            continue
        values[t], quality[t], obs_time[t] = latest.values, latest.quality, latest.start_time

    _ensure_cube(settings, event_id, times)

    site = volumes[0].site
    cfg = settings.radar
    cube.write_variable(
        settings, event_id, "radar_reflectivity", values, quality, obs_time,
        attrs={
            "product": "column maximum reflectivity",
            "radar_id": site.radar_id,
            "radar_lat": site.lat,
            "radar_lon": site.lon,
            "radar_alt_m": site.alt_m,
            "no_echo_value": NO_ECHO_DBZ,
            "qc_rhohv_min": cfg.rhohv_min if cfg.correlation_field else "not applied",
            "qc_max_range_km": cfg.max_range_km,
            "qc_speckle_min_cells": cfg.speckle_min_cells,
        },
    )

    filled = int((~np.isnat(obs_time)).sum())
    summary = {
        "event_id": event_id,
        "radar_id": site.radar_id,
        "volumes_used": len(volumes),
        "volumes_unreadable": unreadable,
        "cycles": len(times),
        "cycles_with_data": filled,
    }
    db.init_db(settings.db_path)
    db.log_event(
        settings.db_path, "info", "ingest",
        f"radar {site.radar_id} → event {event_id}: {filled}/{len(times)} cycles with data",
    )
    for name in unreadable:
        db.log_event(settings.db_path, "warning", "ingest", f"radar volume {name} could not be decoded")
    return summary


def _ensure_cube(settings: Settings, event_id: str, times: np.ndarray) -> None:
    """Create the event's cube, or check that an existing one has the same cycles."""
    path = cube.cube_path(settings, event_id)
    if not path.exists():
        cube.create_cube(settings, event_id, times)
    elif not np.array_equal(cube.open_cube(settings, event_id)["time"].values, times):
        raise SystemExit(f"{path} was built with different cycle times; delete it to rebuild the event")


def ingest_lightning(settings: Settings, event_id: str) -> dict:
    grid = build_grid(settings)
    times = cycle_times(settings, settings.event(event_id))
    folder = settings.source_dir("lightning")
    started = time.perf_counter()
    files, unreadable = read_files(folder, settings.lightning, grid)
    if not files:
        raise SystemExit(f"no readable lightning files found in {folder}")
    print(f"read {len(files)} lightning files in {time.perf_counter() - started:.1f}s")

    shape = (len(times), *grid.shape)
    density = np.full(shape, np.nan, dtype="float32")
    change = np.full(shape, np.nan, dtype="float32")
    quality = np.full(shape, QUALITY_NO_DATA, dtype="uint8")
    change_quality = np.full(shape, QUALITY_NO_DATA, dtype="uint8")
    obs_time = np.full(len(times), np.datetime64("NaT"), dtype="datetime64[ns]")
    flashes = []
    for t, cycle_time in enumerate(times):
        result = grid_cycle(files, cycle_time, settings.cycle.interval_min, settings.lightning, grid)
        density[t], change[t] = result.density, result.change
        quality[t], change_quality[t] = result.quality, result.change_quality
        obs_time[t] = result.obs_time
        flashes.append(result.flash_count)

    _ensure_cube(settings, event_id, times)
    attrs = {
        "format": settings.lightning.format,
        "window_min": settings.cycle.interval_min,
        "qc_min_coverage": settings.lightning.min_coverage,
        "footprint_km": settings.lightning.footprint_km,
    }
    cube.write_variable(settings, event_id, "lightning_density", density, quality, obs_time, attrs)
    cube.write_variable(
        settings, event_id, "lightning_density_change", change, change_quality, obs_time, attrs
    )

    filled = int((~np.isnat(obs_time)).sum())
    db.init_db(settings.db_path)
    db.log_event(
        settings.db_path, "info", "ingest",
        f"lightning → event {event_id}: {filled}/{len(times)} cycles with data, {sum(flashes)} flashes",
    )
    for name in unreadable:
        db.log_event(settings.db_path, "warning", "ingest", f"lightning file {name} could not be decoded")
    return {
        "event_id": event_id,
        "files_used": len(files),
        "files_unreadable": unreadable,
        "cycles": len(times),
        "cycles_with_data": filled,
        "flashes_per_cycle": flashes,
    }


def ingest_satellite(settings: Settings, event_id: str) -> dict:
    grid = build_grid(settings)
    times = cycle_times(settings, settings.event(event_id))
    folder = settings.source_dir("satellite")
    max_age = np.timedelta64(settings.cycle.max_data_age_min, "m")
    started = time.perf_counter()
    images, unreadable = satellite.read_files(folder, settings.satellite, grid)
    if not images:
        raise SystemExit(f"no readable satellite files found in {folder}")
    print(f"read {len(images)} satellite files in {time.perf_counter() - started:.1f}s")

    shape = (len(times), *grid.shape)
    values = np.full(shape, np.nan, dtype="float32")
    quality = np.full(shape, QUALITY_NO_DATA, dtype="uint8")
    obs_time = np.full(len(times), np.datetime64("NaT"), dtype="datetime64[ns]")
    for t, cycle_time in enumerate(times):
        # The newest scan that had finished by the cycle time.
        available = [image for image in images if image.end_time <= cycle_time]
        if not available:
            continue
        latest = max(available, key=lambda image: image.start_time)
        if cycle_time - latest.start_time > max_age:
            continue
        values[t], quality[t], obs_time[t] = latest.values, latest.quality, latest.start_time

    _ensure_cube(settings, event_id, times)
    cube.write_variable(
        settings, event_id, "satellite_brightness_temp", values, quality, obs_time,
        attrs={"format": settings.satellite.format, "parallax_corrected": "no"},
    )

    filled = int((~np.isnat(obs_time)).sum())
    db.init_db(settings.db_path)
    db.log_event(
        settings.db_path, "info", "ingest",
        f"satellite → event {event_id}: {filled}/{len(times)} cycles with data",
    )
    for name in unreadable:
        db.log_event(settings.db_path, "warning", "ingest", f"satellite file {name} could not be decoded")
    return {
        "event_id": event_id,
        "files_used": len(images),
        "files_unreadable": unreadable,
        "cycles": len(times),
        "cycles_with_data": filled,
    }


def ingest_model(settings: Settings, event_id: str) -> dict:
    # Imported here: only this source needs GDAL.
    from vajra import nwp

    grid = build_grid(settings)
    times = cycle_times(settings, settings.event(event_id))
    folder = settings.source_dir("model")
    started = time.perf_counter()
    forecasts, unreadable = nwp.read_files(folder, settings.nwp, grid)
    if not forecasts:
        raise SystemExit(f"no readable model files found in {folder}")
    print(f"read {len(forecasts)} model files in {time.perf_counter() - started:.1f}s")

    shape = (len(times), *grid.shape)
    values = {field.name: np.full(shape, np.nan, dtype="float32") for field in nwp.FIELDS}
    init_time = np.full(len(times), np.datetime64("NaT"), dtype="datetime64[ns]")
    for t, cycle_time in enumerate(times):
        result = nwp.at_cycle(forecasts, cycle_time, settings.nwp)
        if result is None:
            continue
        init_time[t] = result.init_time
        for name, field in result.fields.items():
            values[name][t] = field

    _ensure_cube(settings, event_id, times)
    attrs = {
        "format": settings.nwp.format,
        "availability_delay_min": settings.nwp.availability_delay_min,
        # For model data the "observation time" is the start of the model run used.
        "obs_time_meaning": "model run start time",
    }
    for name, field in values.items():
        quality = np.where(np.isfinite(field), QUALITY_VALID, QUALITY_NO_DATA).astype("uint8")
        cube.write_variable(settings, event_id, name, field, quality, init_time, attrs)

    filled = int((~np.isnat(init_time)).sum())
    db.init_db(settings.db_path)
    db.log_event(
        settings.db_path, "info", "ingest",
        f"model → event {event_id}: {filled}/{len(times)} cycles with data",
    )
    for name in unreadable:
        db.log_event(settings.db_path, "warning", "ingest", f"model file {name} could not be decoded")
    return {
        "event_id": event_id,
        "files_used": len(forecasts),
        "files_unreadable": unreadable,
        "cycles": len(times),
        "cycles_with_data": filled,
        "runs_used": sorted({str(t)[11:16] for t in init_time if not np.isnat(t)}),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the observation cube for a storm event.")
    parser.add_argument("--event", required=True, help="event id from config.yaml")
    parser.add_argument(
        "--source", choices=["radar", "lightning", "satellite", "model"], default="radar"
    )
    parser.add_argument("--radar", help="radar folder name; needed only if there are several")
    args = parser.parse_args()
    settings = get_settings()
    settings.ensure_dirs()
    if args.source == "lightning":
        print(ingest_lightning(settings, args.event))
    elif args.source == "satellite":
        print(ingest_satellite(settings, args.event))
    elif args.source == "model":
        print(ingest_model(settings, args.event))
    else:
        print(ingest_radar(settings, args.event, args.radar))


if __name__ == "__main__":
    main()
