"""Runs every processing stage for an event, in order, and times each one.

    python -m vajra.pipeline --event <event_id>

Stages: ingest the four sources → nowcast → features → ML → storm cells.
The timings are written to data/performance/<event_id>.json so that latency is
something measured, not assumed.
"""

import argparse
import json
import time
from pathlib import Path

from vajra import cells, db, features, forecast, ingest, ml
from vajra.config import Settings, get_settings

# (stage, what it counts as in the latency breakdown, function)
STAGES = [
    ("ingest_radar", "ingestion", lambda s, e: ingest.ingest_radar(s, e)),
    ("ingest_lightning", "ingestion", lambda s, e: ingest.ingest_lightning(s, e)),
    ("ingest_satellite", "ingestion", lambda s, e: ingest.ingest_satellite(s, e)),
    ("ingest_model", "ingestion", lambda s, e: ingest.ingest_model(s, e)),
    ("nowcast", "inference", lambda s, e: forecast.run_event(s, e)),
    ("features", "preprocessing", lambda s, e: features.run_event(s, e)),
    ("ml", "inference", lambda s, e: ml.run_event(s, e)),
    ("cells", "tracking", lambda s, e: cells.run_event(s, e)),
]


def performance_path(settings: Settings, event_id: str) -> Path:
    return settings.data_dir / "performance" / f"{event_id}.json"


def run_event(settings: Settings, event_id: str) -> dict:
    settings.ensure_dirs()
    cycles = len(ingest.cycle_times(settings, settings.event(event_id)))
    stages = []
    started = time.perf_counter()
    for name, group, run in STAGES:
        print(f"--- {name} ---", flush=True)
        stage_started = time.perf_counter()
        run(settings, event_id)
        seconds = time.perf_counter() - stage_started
        stages.append({"stage": name, "group": group, "seconds": round(seconds, 1)})
        print(f"--- {name} done in {seconds:.1f}s ---", flush=True)
    total = time.perf_counter() - started

    groups: dict[str, float] = {}
    for stage in stages:
        groups[stage["group"]] = round(groups.get(stage["group"], 0.0) + stage["seconds"], 1)
    report = {
        "event_id": event_id,
        "measured_at": db.utc_now(),
        "cycles": cycles,
        "stages": stages,
        "seconds_by_group": groups,
        "total_seconds": round(total, 1),
        # The whole event is processed in one batch, so this is an average, not
        # the time any single cycle took end to end.
        "mean_seconds_per_cycle": round(total / cycles, 1),
    }
    path = performance_path(settings, event_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    db.log_event(
        settings.db_path, "info", "pipeline",
        f"event {event_id} processed end to end in {total:.0f}s ({total / cycles:.1f}s per cycle)",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Process a storm event end to end and time each stage.")
    parser.add_argument("--event", required=True, help="event id from config.yaml")
    args = parser.parse_args()
    report = run_event(get_settings(), args.event)
    print(f"\n{report['cycles']} cycles processed in {report['total_seconds']}s "
          f"({report['mean_seconds_per_cycle']}s per cycle on average)")
    for stage in report["stages"]:
        print(f"  {stage['stage']:18s} {stage['seconds']:7.1f}s   {stage['group']}")
    print("  by group:", report["seconds_by_group"])


if __name__ == "__main__":
    main()
