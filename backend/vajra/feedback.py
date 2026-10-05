"""Forecaster feedback on forecasts and alerts, stored for later verification and training.

Feedback is a record only. It never changes a model: retraining is a separate,
deliberate step that may choose to use it.
"""

import sqlite3

from vajra import db
from vajra.config import Settings
from vajra.contracts import Feedback, FeedbackIn


class FeedbackError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _feedback(row: sqlite3.Row) -> Feedback:
    return Feedback(
        feedback_id=row["feedback_id"],
        cycle_id=row["cycle_id"],
        alert_id=row["alert_id"],
        cell_id=row["cell_id"],
        forecaster=row["forecaster"],
        verdict=row["verdict"],
        comment=row["comment"],
        created_at=row["created_at"],
    )


def add(settings: Settings, item: FeedbackIn) -> Feedback:
    with db.connect(settings.db_path) as conn:
        # Whatever the feedback refers to must exist and belong to the same cycle.
        if conn.execute("SELECT 1 FROM forecast_cycles WHERE cycle_id = ?", (item.cycle_id,)).fetchone() is None:
            raise FeedbackError(404, f"cycle '{item.cycle_id}' does not exist")
        for table, key, value in (("alerts", "alert_id", item.alert_id), ("storm_cells", "cell_id", item.cell_id)):
            if value is None:
                continue
            row = conn.execute(f"SELECT cycle_id FROM {table} WHERE {key} = ?", (value,)).fetchone()
            if row is None:
                raise FeedbackError(404, f"{key} '{value}' does not exist")
            if row["cycle_id"] != item.cycle_id:
                raise FeedbackError(422, f"{key} '{value}' belongs to a different cycle")
        cursor = conn.execute(
            "INSERT INTO feedback (cycle_id, alert_id, cell_id, forecaster, verdict, comment, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                item.cycle_id, item.alert_id, item.cell_id, item.forecaster.strip(), item.verdict,
                (item.comment or "").strip() or None, db.utc_now(),
            ),
        )
        row = conn.execute("SELECT * FROM feedback WHERE feedback_id = ?", (cursor.lastrowid,)).fetchone()
    about = item.alert_id or item.cell_id or item.cycle_id
    db.log_event(settings.db_path, "info", "feedback", f"{item.forecaster.strip()} rated {about} as {item.verdict}")
    return _feedback(row)


def for_cycle(settings: Settings, cycle_id: str) -> list[Feedback]:
    with db.connect(settings.db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM feedback WHERE cycle_id = ? ORDER BY feedback_id DESC", (cycle_id,)
        ).fetchall()
    return [_feedback(row) for row in rows]
