"""SQLite persistence for cycles, storm cells, alerts, feedback, models and events.

Large arrays never go here; they live in Zarr. All timestamps are UTC ISO-8601 text.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS forecast_cycles (
    cycle_id        TEXT PRIMARY KEY,
    event_id        TEXT NOT NULL,
    cycle_time      TEXT NOT NULL,
    mode            TEXT NOT NULL DEFAULT 'replay',
    status          TEXT NOT NULL,
    model_version   TEXT,
    latency_json    TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS storm_cells (
    cell_id         TEXT PRIMARY KEY,
    cycle_id        TEXT NOT NULL REFERENCES forecast_cycles(cycle_id),
    track_id        TEXT NOT NULL,
    lead_time_min   INTEGER NOT NULL,
    centroid_lon    REAL NOT NULL,
    centroid_lat    REAL NOT NULL,
    polygon_geojson TEXT NOT NULL,
    area_km2        REAL NOT NULL,
    severity        TEXT,
    probability     REAL,
    motion_dir_deg  REAL,
    speed_kmh       REAL,
    uncertainty     REAL
);
CREATE INDEX IF NOT EXISTS idx_storm_cells_cycle ON storm_cells(cycle_id);
CREATE INDEX IF NOT EXISTS idx_storm_cells_track ON storm_cells(track_id);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id        TEXT PRIMARY KEY,
    cycle_id        TEXT NOT NULL REFERENCES forecast_cycles(cycle_id),
    cell_id         TEXT REFERENCES storm_cells(cell_id),
    risk_level      TEXT NOT NULL,
    headline        TEXT NOT NULL,
    description     TEXT NOT NULL,
    polygon_geojson TEXT NOT NULL,
    valid_from      TEXT NOT NULL,
    valid_to        TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'suggested',
    reviewed_by     TEXT,
    reviewed_at     TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerts_cycle ON alerts(cycle_id);

CREATE TABLE IF NOT EXISTS feedback (
    feedback_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id        TEXT NOT NULL REFERENCES forecast_cycles(cycle_id),
    alert_id        TEXT REFERENCES alerts(alert_id),
    cell_id         TEXT REFERENCES storm_cells(cell_id),
    forecaster      TEXT NOT NULL,
    verdict         TEXT NOT NULL,
    comment         TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_versions (
    model_version   TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,
    trained_at      TEXT,
    train_events    TEXT,
    metrics_json    TEXT,
    path            TEXT,
    is_active       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS users (
    username        TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    role            TEXT NOT NULL,
    password_salt   BLOB NOT NULL,
    password_hash   BLOB NOT NULL,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until    TEXT,
    created_at      TEXT NOT NULL,
    last_login_at   TEXT
);

-- Only a hash of each session token is stored, never the token itself.
CREATE TABLE IF NOT EXISTS sessions (
    token_hash      TEXT PRIMARY KEY,
    username        TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    created_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS system_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    level           TEXT NOT NULL,
    component       TEXT NOT NULL,
    message         TEXT NOT NULL
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Columns added after the first release; applied to databases created before them.
ADDED_COLUMNS = [
    ("storm_cells", "max_dbz", "REAL"),
    ("storm_cells", "mean_dbz", "REAL"),
    ("storm_cells", "ml_storm_probability", "REAL"),
    ("storm_cells", "ml_lightning_probability", "REAL"),
]


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        for table, column, kind in ADDED_COLUMNS:
            existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")


def log_event(db_path: Path, level: str, component: str, message: str) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO system_events (ts, level, component, message) VALUES (?, ?, ?, ?)",
            (utc_now(), level, component, message),
        )
