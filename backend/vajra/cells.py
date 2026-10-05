"""Storm cells: detect convective cores, track them across cycles, project them ahead.

    python -m vajra.cells --event <event_id>

Detection: connected areas of reflectivity at or above the core threshold.
Tracking: each cell is moved to where the storm motion says it should be by the
next scan, then matched to the nearest new cell; a match keeps the track id.
Projection: a cell's outline is carried along its own motion to each lead time,
and the forecast ensemble and ML probabilities are read under that outline. Run
this after vajra.ml so the ML probabilities are included.

Results go to SQLite `storm_cells`: one row per cell per cycle for the observed
position (lead 0) and one per forecast lead.
"""

import argparse
import json
from dataclasses import dataclass, field

import numpy as np
import xarray as xr
from scipy import ndimage
from scipy.optimize import linear_sum_assignment
from shapely import affinity
from shapely.geometry import Polygon, mapping
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from skimage import measure

from vajra import cube, db
from vajra.config import CellsConfig, Settings, get_settings
from vajra.forecast import open_forecast
from vajra.grid import KM_PER_DEG_LAT, Grid, build_grid
from vajra.nowcast import MINUTE
from vajra.replay import _utc, cycle_id

EIGHT_CONNECTED = np.ones((3, 3), dtype=int)


@dataclass
class Cell:
    rows: np.ndarray  # grid indices of the cells that make up the storm cell
    cols: np.ndarray
    centroid: tuple[float, float]  # (lon, lat)
    polygon: BaseGeometry  # Polygon or MultiPolygon in lon/lat
    area_km2: float
    max_dbz: float
    mean_dbz: float
    track_id: str = ""
    # Motion in km/h (east, north); None when the cycle has no motion estimate.
    motion_kmh: tuple[float, float] | None = None
    projections: list[dict] = field(default_factory=list)


def _outline(mask: np.ndarray, row0: int, col0: int, grid: Grid) -> BaseGeometry | None:
    """Outline of a cell mask in lon/lat. Parts joined only diagonally give a multi-polygon."""
    west, south, _, _ = grid.extent
    parts = []
    for contour in measure.find_contours(np.pad(mask, 1).astype(float), 0.5):
        if len(contour) < 4:
            continue
        # Contour coordinates are in padded array indices; index i is the centre of cell i - 1.
        lon = west + (contour[:, 1] - 1 + col0 + 0.5) * grid.dlon
        lat = south + (contour[:, 0] - 1 + row0 + 0.5) * grid.dlat
        parts.append(Polygon(np.column_stack([lon, lat])).buffer(0))
    if not parts:
        return None
    # The union also absorbs the contours of any holes, leaving a solid outline.
    outline = unary_union(parts).simplify(grid.dlat * 0.3)
    return outline if not outline.is_empty else None


def detect(values: np.ndarray, grid: Grid, cfg: CellsConfig) -> list[Cell]:
    """Find the storm cells in one reflectivity field, strongest first."""
    echo = np.nan_to_num(values, nan=-np.inf)
    labels, count = ndimage.label(echo >= cfg.core_threshold_dbz, structure=EIGHT_CONNECTED)
    cell_area = grid.resolution_km**2
    cells: list[Cell] = []
    for label, box in enumerate(ndimage.find_objects(labels), start=1):
        mask = labels[box] == label
        area = float(mask.sum()) * cell_area
        if area < cfg.min_area_km2:
            continue
        rows, cols = np.nonzero(mask)
        rows, cols = rows + box[0].start, cols + box[1].start
        polygon = _outline(mask, box[0].start, box[1].start, grid)
        if polygon is None:
            continue
        inside = values[rows, cols]
        cells.append(
            Cell(
                rows=rows,
                cols=cols,
                centroid=(float(grid.lons[cols].mean()), float(grid.lats[rows].mean())),
                polygon=polygon,
                area_km2=area,
                max_dbz=float(inside.max()),
                mean_dbz=float(inside.mean()),
            )
        )
    return sorted(cells, key=lambda c: (-c.max_dbz, -c.area_km2))


def _offset_deg(east_km: float, north_km: float, lat: float) -> tuple[float, float]:
    return east_km / (KM_PER_DEG_LAT * np.cos(np.deg2rad(lat))), north_km / KM_PER_DEG_LAT


def _distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat = np.deg2rad((a[1] + b[1]) / 2)
    return float(np.hypot((a[0] - b[0]) * np.cos(lat), a[1] - b[1]) * KM_PER_DEG_LAT)


class Tracker:
    """Gives each cell a track id that persists while the cell can be followed."""

    def __init__(self, cfg: CellsConfig) -> None:
        self._cfg = cfg
        self._previous: list[Cell] = []
        self._previous_time: np.datetime64 | None = None
        self._tracks = 0

    def _new_track(self) -> str:
        self._tracks += 1
        return f"T{self._tracks:03d}"

    def update(self, cells: list[Cell], obs_time: np.datetime64) -> None:
        matched: dict[int, str] = {}
        if self._previous and cells:
            hours = float((obs_time - self._previous_time) / MINUTE) / 60.0
            expected = []
            for old in self._previous:
                east, north = old.motion_kmh or (0.0, 0.0)
                dlon, dlat = _offset_deg(east * hours, north * hours, old.centroid[1])
                expected.append((old.centroid[0] + dlon, old.centroid[1] + dlat))
            distance = np.array([[_distance_km(e, c.centroid) for c in cells] for e in expected])
            # Best one-to-one pairing by distance; pairs beyond the limit are not matches.
            gated = np.where(distance <= self._cfg.max_match_km, distance, 1e6)
            for i, j in zip(*linear_sum_assignment(gated)):
                if distance[i, j] <= self._cfg.max_match_km:
                    matched[j] = self._previous[i].track_id
        for j, cell in enumerate(cells):
            cell.track_id = matched.get(j) or self._new_track()
        self._previous, self._previous_time = cells, obs_time


def _severity(max_dbz: float, cfg: CellsConfig) -> str:
    if max_dbz >= cfg.severe_dbz:
        return "severe"
    return "strong" if max_dbz >= cfg.strong_dbz else "moderate"


def _toward_deg(east: float, north: float) -> float:
    return float(np.degrees(np.arctan2(east, north)) % 360.0)


def _project(cell: Cell, grid: Grid, age_min: float, leads, fields: dict[str, np.ndarray | None]) -> None:
    """Carry the cell along its motion to each lead time and read the forecast
    fields under its outline there. `fields` maps a name to a (lead, lat, lon) array."""
    east, north = cell.motion_kmh
    height, width = grid.shape
    for li, lead in enumerate(leads):
        hours = (age_min + float(lead)) / 60.0
        dlon, dlat = _offset_deg(east * hours, north * hours, cell.centroid[1])
        rows = cell.rows + int(round(north * hours / grid.resolution_km))
        cols = cell.cols + int(round(east * hours / grid.resolution_km))
        inside = (rows >= 0) & (rows < height) & (cols >= 0) & (cols < width)
        under: dict[str, float | None] = {}
        for name, field in fields.items():
            values = field[li][rows[inside], cols[inside]] if field is not None and inside.any() else np.array([])
            under[name] = float(np.nanmean(values)) if np.isfinite(values).any() else None
        cell.projections.append(
            {
                "lead": int(lead),
                "centroid": (cell.centroid[0] + dlon, cell.centroid[1] + dlat),
                "polygon": affinity.translate(cell.polygon, xoff=dlon, yoff=dlat),
                **under,
            }
        )


def _alerts_exist(settings: Settings, event_id: str) -> int:
    with db.connect(settings.db_path) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM alerts WHERE cycle_id IN "
            "(SELECT cycle_id FROM forecast_cycles WHERE event_id = ?)",
            (event_id,),
        ).fetchone()[0]


def run_event(settings: Settings, event_id: str, reset_alerts: bool = False) -> dict:
    db.init_db(settings.db_path)
    # Alerts and feedback refer to cells. Rebuilding cells would orphan them, so
    # that is only done when explicitly asked for.
    existing = _alerts_exist(settings, event_id)
    if existing and not reset_alerts:
        raise SystemExit(
            f"{existing} alerts refer to this event's storm cells. Rebuilding the cells would "
            "delete them and their feedback; pass --reset-alerts to do that."
        )
    grid = build_grid(settings)
    cfg = settings.cells
    obs = cube.open_cube(settings, event_id).load()
    forecast = open_forecast(settings, event_id)
    if forecast is not None:
        forecast = forecast.load()
    # ML probabilities, if the model has been trained for this event.
    ml_store = settings.ml_dir / f"{event_id}.zarr"
    ml = xr.open_zarr(ml_store, consolidated=False).load() if ml_store.exists() else None
    times = obs["time"].values
    obs_time = obs["radar_reflectivity_obs_time"].values
    cells_per_minute_to_kmh = grid.resolution_km * 60.0

    tracker = Tracker(cfg)
    rows: list[tuple] = []
    per_cycle: list[int] = []
    for t, cycle_time in enumerate(times):
        if np.isnat(obs_time[t]):
            per_cycle.append(0)
            continue
        cells = detect(obs["radar_reflectivity"].values[t], grid, cfg)
        has_forecast = forecast is not None and bool(forecast["available"].values[t])
        if has_forecast:
            east_field = forecast["motion_east"].values[t]
            north_field = forecast["motion_north"].values[t]
            for cell in cells:
                cell.motion_kmh = (
                    float(east_field[cell.rows, cell.cols].mean()) * cells_per_minute_to_kmh,
                    float(north_field[cell.rows, cell.cols].mean()) * cells_per_minute_to_kmh,
                )
        tracker.update(cells, obs_time[t])
        if has_forecast:
            age = float((cycle_time - obs_time[t]) / MINUTE)
            fields = {
                "probability": forecast["storm_probability"].values[t],
                "uncertainty": forecast["forecast_spread"].values[t],
                "ml_storm": ml["ml_storm_probability"].values[t] if ml is not None else None,
                "ml_lightning": ml["ml_lightning_probability"].values[t] if ml is not None else None,
            }
            for cell in cells:
                _project(cell, grid, age, forecast["lead_min"].values, fields)
        per_cycle.append(len(cells))

        cid = cycle_id(event_id, cycle_time)
        for cell in cells:
            speed = direction = None
            if cell.motion_kmh is not None:
                speed = float(np.hypot(*cell.motion_kmh))
                direction = _toward_deg(*cell.motion_kmh)
            shared = (
                cell.area_km2, _severity(cell.max_dbz, cfg), direction, speed, cell.max_dbz, cell.mean_dbz,
            )
            rows.append((
                f"{cid}_{cell.track_id}_L000", cid, cell.track_id, 0, *cell.centroid,
                json.dumps(mapping(cell.polygon)), *shared, None, None, None, None,
            ))
            for p in cell.projections:
                rows.append((
                    f"{cid}_{cell.track_id}_L{p['lead']:03d}", cid, cell.track_id, p["lead"], *p["centroid"],
                    json.dumps(mapping(p["polygon"])), *shared,
                    p["probability"], p["uncertainty"], p["ml_storm"], p["ml_lightning"],
                ))

    with db.connect(settings.db_path) as conn:
        if existing:
            in_event = "(SELECT cycle_id FROM forecast_cycles WHERE event_id = ?)"
            conn.execute(f"DELETE FROM feedback WHERE cycle_id IN {in_event}", (event_id,))
            conn.execute(f"DELETE FROM alerts WHERE cycle_id IN {in_event}", (event_id,))
        # Cells belong to cycles, so make sure every cycle of the event exists first.
        for t, cycle_time in enumerate(times):
            conn.execute(
                "INSERT OR IGNORE INTO forecast_cycles "
                "(cycle_id, event_id, cycle_time, mode, status, created_at) VALUES (?, ?, ?, 'replay', ?, ?)",
                (
                    cycle_id(event_id, cycle_time), event_id, _utc(cycle_time).isoformat(),
                    "no_observation" if np.isnat(obs_time[t]) else "observed", db.utc_now(),
                ),
            )
        conn.execute(
            "DELETE FROM storm_cells WHERE cycle_id IN "
            "(SELECT cycle_id FROM forecast_cycles WHERE event_id = ?)",
            (event_id,),
        )
        conn.executemany(
            "INSERT INTO storm_cells (cell_id, cycle_id, track_id, lead_time_min, centroid_lon, centroid_lat, "
            "polygon_geojson, area_km2, severity, motion_dir_deg, speed_kmh, max_dbz, mean_dbz, "
            "probability, uncertainty, ml_storm_probability, ml_lightning_probability) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    tracks = len({row[2] for row in rows})
    db.log_event(
        settings.db_path, "info", "cells",
        f"event {event_id}: {sum(per_cycle)} cells in {tracks} tracks",
    )
    return {"event_id": event_id, "cells_per_cycle": per_cycle, "tracks": tracks, "rows": len(rows)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Detect and track storm cells for a storm event.")
    parser.add_argument("--event", required=True, help="event id from config.yaml")
    parser.add_argument(
        "--reset-alerts", action="store_true",
        help="also delete this event's alerts and feedback, which refer to the cells being rebuilt",
    )
    args = parser.parse_args()
    settings = get_settings()
    print(run_event(settings, args.event, args.reset_alerts))


if __name__ == "__main__":
    main()
