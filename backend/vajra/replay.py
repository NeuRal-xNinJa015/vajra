"""Replay engine: plays a historical event back as simulated live cycles.

The cycle sequence comes from the event's cube, so a replay is deterministic:
the same event always produces the same cycles with the same ids. The engine
only decides which cycle is current. Every cycle it issues is recorded in SQLite.
"""

import asyncio
import json
import threading
import time
from datetime import datetime, timezone

import numpy as np

from vajra import cube, db
from vajra.config import Settings
from vajra.contracts import CycleSummary, ReplayState

# Time compression relative to real time: at 300x a 10-minute cycle lasts 2 seconds.
SPEEDS = (60, 150, 300, 600)
DEFAULT_SPEED = 300


def _utc(value: np.datetime64) -> datetime:
    return value.astype("datetime64[s]").item().replace(tzinfo=timezone.utc)


def cycle_id(event_id: str, cycle_time: np.datetime64) -> str:
    return f"{event_id}_{_utc(cycle_time):%Y%m%dT%H%MZ}"


class ReplayEngine:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = threading.Lock()
        self._event_id: str | None = None
        self._times: np.ndarray = np.array([], dtype="datetime64[ns]")
        self._has_observation: np.ndarray = np.array([], dtype=bool)
        self._index = 0
        self._playing = False
        self._speed = DEFAULT_SPEED
        self._next_advance = 0.0
        self.load()

    # ---- event ----------------------------------------------------------

    def load(self) -> None:
        """Load the first configured event that has data and rewind to its first cycle."""
        available = cube.list_events(self._settings)
        configured = [e.id for e in self._settings.events if e.id in available]
        with self._lock:
            self._playing = False
            self._index = 0
            if not configured:
                self._event_id = None
                self._times = np.array([], dtype="datetime64[ns]")
                self._has_observation = np.array([], dtype=bool)
                return
            self._event_id = configured[0]
            ds = cube.open_cube(self._settings, self._event_id)
            self._times = ds["time"].values
            if "radar_reflectivity_obs_time" in ds:
                self._has_observation = ~np.isnat(ds["radar_reflectivity_obs_time"].values)
            else:
                self._has_observation = np.zeros(len(self._times), dtype=bool)
            self._issue_current()

    # ---- commands -------------------------------------------------------

    def play(self) -> None:
        with self._lock:
            if self._event_id is None:
                return
            if self._index >= len(self._times) - 1:
                self._index = 0  # playing from the end starts the event again
                self._issue_current()
            self._playing = True
            self._schedule()

    def pause(self) -> None:
        with self._lock:
            self._playing = False

    def reset(self) -> None:
        with self._lock:
            self._playing = False
            self._index = 0
            if self._event_id is not None:
                self._issue_current()

    def seek(self, index: int) -> None:
        with self._lock:
            if self._event_id is None:
                return
            self._index = min(max(index, 0), len(self._times) - 1)
            self._issue_current()
            self._schedule()

    def set_speed(self, speed: int) -> None:
        if speed not in SPEEDS:
            raise ValueError(f"speed must be one of {SPEEDS}")
        with self._lock:
            self._speed = speed
            self._schedule()

    # ---- clock ----------------------------------------------------------

    def _seconds_per_cycle(self) -> float:
        return self._settings.cycle.interval_min * 60 / self._speed

    def _schedule(self) -> None:
        self._next_advance = time.monotonic() + self._seconds_per_cycle()

    def _issue_current(self) -> None:
        """Record the current cycle in SQLite. Re-issuing the same cycle changes nothing."""
        cycle_time = self._times[self._index]
        status = "observed" if self._has_observation[self._index] else "no_observation"
        with db.connect(self._settings.db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO forecast_cycles "
                "(cycle_id, event_id, cycle_time, mode, status, created_at) "
                "VALUES (?, ?, ?, 'replay', ?, ?)",
                (
                    cycle_id(self._event_id, cycle_time),
                    self._event_id,
                    _utc(cycle_time).isoformat(),
                    status,
                    db.utc_now(),
                ),
            )

    def _tick(self) -> None:
        with self._lock:
            if not self._playing or time.monotonic() < self._next_advance:
                return
            self._index += 1
            self._issue_current()
            if self._index >= len(self._times) - 1:
                self._playing = False
            else:
                self._schedule()

    async def run(self) -> None:
        while True:
            self._tick()
            await asyncio.sleep(0.1)

    # ---- state ----------------------------------------------------------

    def state(self) -> ReplayState:
        with self._lock:
            if self._event_id is None:
                return ReplayState(
                    status="no_event", cycle_index=0, cycle_count=0, speed=self._speed,
                    speeds=list(SPEEDS), seconds_per_cycle=self._seconds_per_cycle(),
                )
            last = len(self._times) - 1
            status = "playing" if self._playing else "finished" if self._index >= last else "paused"
            current = self._times[self._index]
            return ReplayState(
                status=status,
                event_id=self._event_id,
                cycle_id=cycle_id(self._event_id, current),
                cycle_index=self._index,
                cycle_count=len(self._times),
                cycle_time=_utc(current),
                cycle_times=[_utc(t) for t in self._times],
                cycle_has_observation=self._has_observation.tolist(),
                speed=self._speed,
                speeds=list(SPEEDS),
                seconds_per_cycle=self._seconds_per_cycle(),
            )

    def current_cycle_id(self) -> str | None:
        return self.state().cycle_id


def cycle_summary(settings: Settings, wanted_cycle_id: str) -> CycleSummary | None:
    with db.connect(settings.db_path) as conn:
        row = conn.execute(
            "SELECT * FROM forecast_cycles WHERE cycle_id = ?", (wanted_cycle_id,)
        ).fetchone()
    if row is None:
        return None
    return CycleSummary(
        cycle_id=row["cycle_id"],
        event_id=row["event_id"],
        cycle_time=row["cycle_time"],
        mode="replay",
        status=row["status"],
        model_version=row["model_version"],
        latency_ms=json.loads(row["latency_json"] or "{}"),
    )
