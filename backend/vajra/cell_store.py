"""Reads storm cells from SQLite for the API. Kept apart from cells.py so the API
server does not load the detection libraries."""

import json
import sqlite3
from functools import lru_cache
from pathlib import Path

from vajra import db, risk
from vajra.config import Settings, get_settings
from vajra.contracts import StormCell, StormCells, StormCellTrack, TrackPoint


def _cell(row: sqlite3.Row) -> StormCell:
    return StormCell(
        cell_id=row["cell_id"],
        cycle_id=row["cycle_id"],
        track_id=row["track_id"],
        lead_time_min=row["lead_time_min"],
        centroid=(row["centroid_lon"], row["centroid_lat"]),
        polygon=json.loads(row["polygon_geojson"]),
        area_km2=row["area_km2"],
        severity=row["severity"],
        probability=row["probability"],
        motion_dir_deg=row["motion_dir_deg"],
        speed_kmh=row["speed_kmh"],
        uncertainty=row["uncertainty"],
        max_dbz=row["max_dbz"],
        mean_dbz=row["mean_dbz"],
        ml_storm_probability=row["ml_storm_probability"],
        ml_lightning_probability=row["ml_lightning_probability"],
    )


def cell(settings: Settings, cell_id: str) -> StormCell | None:
    with db.connect(settings.db_path) as conn:
        row = conn.execute("SELECT * FROM storm_cells WHERE cell_id = ?", (cell_id,)).fetchone()
    return _cell(row) if row else None


@lru_cache(maxsize=256)
def _cells_json(db_path: str, cycle_id: str, db_version: int) -> bytes:
    return _cells_for_cycle(Path(db_path), cycle_id).model_dump_json().encode()


def cells_json(settings: Settings, cycle_id: str) -> bytes:
    """`cells_for_cycle` as ready-to-send JSON, cached until the database next changes."""
    version = settings.db_path.stat().st_mtime_ns
    return _cells_json(str(settings.db_path), cycle_id, version)


def cells_for_cycle(settings: Settings, cycle_id: str) -> StormCells:
    """Every cell at a cycle with its projections and past track, most severe first."""
    return _cells_for_cycle(settings.db_path, cycle_id)


def _cells_for_cycle(db_path: Path, cycle_id: str) -> StormCells:
    with db.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM storm_cells WHERE cycle_id = ? ORDER BY lead_time_min", (cycle_id,)
        ).fetchall()
        history_rows = conn.execute(
            "SELECT s.track_id, c.cycle_time, s.centroid_lon, s.centroid_lat, s.max_dbz "
            "FROM storm_cells s JOIN forecast_cycles c ON c.cycle_id = s.cycle_id "
            "JOIN forecast_cycles now ON now.cycle_id = ? "
            "WHERE s.lead_time_min = 0 AND c.event_id = now.event_id AND c.cycle_time <= now.cycle_time "
            "AND s.track_id IN (SELECT track_id FROM storm_cells WHERE cycle_id = ?) "
            "ORDER BY c.cycle_time",
            (cycle_id, cycle_id),
        ).fetchall()

    history: dict[str, list[TrackPoint]] = {}
    for row in history_rows:
        history.setdefault(row["track_id"], []).append(
            TrackPoint(
                cycle_time=row["cycle_time"],
                centroid=(row["centroid_lon"], row["centroid_lat"]),
                max_dbz=row["max_dbz"],
            )
        )

    observed: dict[str, StormCell] = {}
    projections: dict[str, list[StormCell]] = {}
    for row in rows:
        item = _cell(row)
        if item.lead_time_min == 0:
            observed[item.track_id] = item
        else:
            projections.setdefault(item.track_id, []).append(item)

    settings = get_settings()
    tracks = [
        StormCellTrack(
            track_id=track_id,
            first_seen=history[track_id][0].cycle_time,
            cell=item,
            projections=projections.get(track_id, []),
            history=history[track_id],
            risk=risk.assess(item, projections.get(track_id, []), settings),
        )
        for track_id, item in observed.items()
    ]
    # Highest risk first, then most intense.
    tracks.sort(key=lambda t: (-risk.RISK_ORDER.index(t.risk.level), -(t.cell.max_dbz or 0)))
    return StormCells(cycle_id=cycle_id, cells=tracks)
