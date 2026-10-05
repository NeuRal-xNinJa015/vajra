"""Alert workflow: suggested alert → forecaster approves, edits or rejects → CAP preview.

The platform only ever suggests. An alert is a draft until a named forecaster
decides on it, every decision is recorded, and the CAP message is a preview:
nothing here sends anything to anyone.
"""

import json
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

from shapely.geometry import mapping, shape
from shapely.ops import unary_union

from vajra import cell_store, db, risk
from vajra.config import Settings
from vajra.contracts import Alert, AlertDecision, StormCellTrack

CAP_NAMESPACE = "urn:oasis:names:tc:emergency:cap:1.2"
# Risk level → CAP severity.
_CAP_SEVERITY = {"low": "Minor", "moderate": "Moderate", "high": "Severe", "severe": "Extreme"}
_COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


class AlertError(Exception):
    """A request the workflow does not allow; `status` is the HTTP status to return."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _label(track_id: str) -> str:
    return f"T{int(track_id[1:])}"


def _threat_area(track: StormCellTrack, valid_min: int):
    """The cell now plus everywhere it is projected to be during the alert period."""
    shapes = [shape(track.cell.polygon)]
    shapes += [shape(p.polygon) for p in track.projections if p.lead_time_min <= valid_min]
    return unary_union(shapes).convex_hull


def _draft(track: StormCellTrack, cycle_time: datetime, settings: Settings) -> dict:
    cell, assessment = track.cell, track.risk
    name = _label(track.track_id)
    lon, lat = cell.centroid
    where = f"{abs(lat):.2f}°{'N' if lat >= 0 else 'S'} {abs(lon):.2f}°{'E' if lon >= 0 else 'W'}"
    moving = (
        f", moving {_COMPASS[round(cell.motion_dir_deg / 22.5) % 16]} at {cell.speed_kmh:.0f} km/h"
        if cell.speed_kmh is not None and cell.motion_dir_deg is not None
        else ""
    )
    valid_to = cycle_time + timedelta(minutes=settings.alerts.valid_min)
    description = (
        f"Storm cell {name} near {where}{moving}, covering {cell.area_km2:,.0f} km². "
        + ". ".join(assessment.factors)
        + f". The area shown covers the cell and its projected track until {valid_to:%H:%M} UTC."
    )
    return {
        "risk_level": assessment.level,
        "headline": f"{assessment.level.capitalize()} thunderstorm and lightning risk: storm cell {name}",
        "description": description,
        "polygon_geojson": json.dumps(mapping(_threat_area(track, settings.alerts.valid_min))),
        "valid_from": cycle_time.isoformat(),
        "valid_to": valid_to.isoformat(),
    }


def _cap_xml(row: sqlite3.Row, settings: Settings) -> str:
    """CAP 1.2 preview of an alert. Marked as an exercise: it is built from archived
    data and is never disseminated."""
    ET.register_namespace("", CAP_NAMESPACE)

    def add(parent, tag, text):
        element = ET.SubElement(parent, f"{{{CAP_NAMESPACE}}}{tag}")
        element.text = text
        return element

    alert = ET.Element(f"{{{CAP_NAMESPACE}}}alert")
    add(alert, "identifier", row["alert_id"])
    add(alert, "sender", settings.alerts.sender)
    add(alert, "sent", row["reviewed_at"] or row["created_at"])
    add(alert, "status", "Exercise")
    add(alert, "msgType", "Alert")
    add(alert, "scope", "Restricted")
    add(alert, "restriction", "Preview only. Generated from archived data; not disseminated.")
    add(alert, "note", f"Review status: {row['status']}" + (f" by {row['reviewed_by']}" if row["reviewed_by"] else ""))
    info = add(alert, "info", None)
    add(info, "language", "en")
    add(info, "category", "Met")
    add(info, "event", "Thunderstorm with lightning")
    add(info, "urgency", "Expected")
    add(info, "severity", _CAP_SEVERITY[row["risk_level"]])
    add(info, "certainty", "Likely" if risk.at_least(row["risk_level"], "high") else "Possible")
    add(info, "effective", row["valid_from"])
    add(info, "onset", row["valid_from"])
    add(info, "expires", row["valid_to"])
    add(info, "senderName", settings.alerts.sender_name)
    add(info, "headline", row["headline"])
    add(info, "description", row["description"])
    area = add(info, "area", None)
    add(area, "areaDesc", f"Projected track of storm cell {_label(row['track_id'])}")
    # CAP polygons are "lat,lon" pairs, first point repeated at the end.
    ring = shape(json.loads(row["polygon_geojson"])).exterior.coords
    add(area, "polygon", " ".join(f"{lat:.4f},{lon:.4f}" for lon, lat in ring))
    ET.indent(alert)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(alert, encoding="unicode")


_SELECT = (
    "SELECT a.*, s.track_id FROM alerts a LEFT JOIN storm_cells s ON s.cell_id = a.cell_id "
)


def _alert(row: sqlite3.Row, settings: Settings) -> Alert:
    return Alert(
        alert_id=row["alert_id"],
        cycle_id=row["cycle_id"],
        cell_id=row["cell_id"],
        track_id=row["track_id"],
        risk_level=row["risk_level"],
        headline=row["headline"],
        description=row["description"],
        polygon=json.loads(row["polygon_geojson"]),
        valid_from=row["valid_from"],
        valid_to=row["valid_to"],
        status=row["status"],
        reviewed_by=row["reviewed_by"],
        reviewed_at=row["reviewed_at"],
        created_at=row["created_at"],
        cap_xml=_cap_xml(row, settings),
    )


def _suggest(conn: sqlite3.Connection, track: StormCellTrack, cycle_id: str, cycle_time: datetime, settings: Settings) -> str:
    """Create the suggested alert for a cell if it does not exist yet. One alert per cell per cycle."""
    alert_id = f"{cycle_id}_{track.track_id}"
    draft = _draft(track, cycle_time, settings)
    conn.execute(
        "INSERT OR IGNORE INTO alerts (alert_id, cycle_id, cell_id, risk_level, headline, description, "
        "polygon_geojson, valid_from, valid_to, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'suggested', ?)",
        (
            alert_id, cycle_id, track.cell.cell_id, draft["risk_level"], draft["headline"], draft["description"],
            draft["polygon_geojson"], draft["valid_from"], draft["valid_to"], db.utc_now(),
        ),
    )
    return alert_id


def _cycle_time(conn: sqlite3.Connection, cycle_id: str) -> datetime:
    row = conn.execute("SELECT cycle_time FROM forecast_cycles WHERE cycle_id = ?", (cycle_id,)).fetchone()
    if row is None:
        raise AlertError(404, f"cycle '{cycle_id}' does not exist")
    return datetime.fromisoformat(row["cycle_time"])


def for_cycle(settings: Settings, cycle_id: str) -> list[Alert]:
    """Every alert at a cycle. Cells at or above the configured risk level get a
    suggested alert automatically the first time the cycle is looked at."""
    tracks = cell_store.cells_for_cycle(settings, cycle_id).cells
    with db.connect(settings.db_path) as conn:
        cycle_time = _cycle_time(conn, cycle_id)
        for track in tracks:
            if risk.at_least(track.risk.level, settings.alerts.suggest_from):
                _suggest(conn, track, cycle_id, cycle_time, settings)
        rows = conn.execute(_SELECT + "WHERE a.cycle_id = ? ORDER BY a.created_at, a.alert_id", (cycle_id,)).fetchall()
    alerts = [_alert(row, settings) for row in rows]
    return sorted(alerts, key=lambda a: -risk.RISK_ORDER.index(a.risk_level))


def preview(settings: Settings, cycle_id: str, cell_id: str | None) -> Alert:
    """The suggested alert for one cell (the highest-risk cell if none is named)."""
    tracks = cell_store.cells_for_cycle(settings, cycle_id).cells
    with db.connect(settings.db_path) as conn:
        cycle_time = _cycle_time(conn, cycle_id)
        if not tracks:
            raise AlertError(404, f"there are no storm cells at cycle '{cycle_id}'")
        track = tracks[0] if cell_id is None else next((t for t in tracks if t.cell.cell_id == cell_id), None)
        if track is None:
            raise AlertError(404, f"storm cell '{cell_id}' is not part of cycle '{cycle_id}'")
        alert_id = _suggest(conn, track, cycle_id, cycle_time, settings)
        row = conn.execute(_SELECT + "WHERE a.alert_id = ?", (alert_id,)).fetchone()
    return _alert(row, settings)


def decide(settings: Settings, alert_id: str, decision: AlertDecision, approve: bool) -> Alert:
    """Record a forecaster's decision. An alert can be decided once; edits made on
    approval are kept and the alert is marked as edited."""
    with db.connect(settings.db_path) as conn:
        row = conn.execute("SELECT * FROM alerts WHERE alert_id = ?", (alert_id,)).fetchone()
        if row is None:
            raise AlertError(404, f"alert '{alert_id}' does not exist")
        if row["status"] != "suggested":
            raise AlertError(409, f"alert '{alert_id}' was already {row['status']} by {row['reviewed_by']}")
        edits = {
            field: value
            for field in ("risk_level", "headline", "description")
            if approve and (value := getattr(decision, field)) is not None and value != row[field]
        }
        status = "rejected" if not approve else "edited" if edits else "approved"
        values = {**edits, "status": status, "reviewed_by": decision.forecaster, "reviewed_at": db.utc_now()}
        conn.execute(
            f"UPDATE alerts SET {', '.join(f'{name} = ?' for name in values)} WHERE alert_id = ?",
            (*values.values(), alert_id),
        )
        updated = conn.execute(_SELECT + "WHERE a.alert_id = ?", (alert_id,)).fetchone()
    changed = f" (changed: {', '.join(edits)})" if edits else ""
    db.log_event(settings.db_path, "info", "alerts", f"alert {alert_id} {status} by {decision.forecaster}{changed}")
    return _alert(updated, settings)
