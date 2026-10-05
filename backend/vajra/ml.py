"""Machine-learning nowcast: LightGBM probability of storm and of lightning.

    python -m vajra.ml --event <event_id>

Trained the way it would run in operation. For each cycle, the model is fitted
only on forecasts whose outcome had already been observed by that cycle, and
then predicts that cycle. So every stored probability, and every skill score,
is out-of-sample: the model never sees the answer to the forecast it is making.

A lead time is predicted at a cycle only once the model has seen outcomes for
that lead; before that the probability is left missing rather than guessed.

Outputs
    data/ml/<event_id>.zarr          ml_storm_probability, ml_lightning_probability
                                     (time, lead_min, lat, lon), plus what each cycle's model was trained on
    data/ml/<event_id>/<target>_<cycle>.txt   the model used at each cycle
    data/ml/<event_id>_verification.json      skill against persistence and extrapolation
"""

import argparse
import json
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import xarray as xr
from sklearn.metrics import roc_auc_score

from vajra import db
from vajra.config import Settings, get_settings
from vajra.cube import QUALITY_VALID
from vajra.features import FEATURES, open_features
from vajra.forecast import open_forecast
from vajra.replay import _utc, cycle_id

MODEL_VERSION = "lightgbm-rolling-1"
TARGETS = {"storm": "label_storm", "lightning": "label_lightning"}
INPUTS = [f.name for f in FEATURES] + ["lead_min"]
THRESHOLDS = np.round(np.arange(0.05, 0.96, 0.05), 2)


def ml_path(settings: Settings, event_id: str) -> Path:
    return settings.ml_dir / f"{event_id}.zarr"


def model_path(settings: Settings, event_id: str, target: str, cycle: str) -> Path:
    return settings.ml_dir / event_id / f"{target}_{cycle}.txt"


def verification_path(settings: Settings, event_id: str) -> Path:
    return settings.ml_dir / f"{event_id}_verification.json"


def open_ml(settings: Settings, event_id: str) -> xr.Dataset | None:
    path = ml_path(settings, event_id)
    return xr.open_zarr(path, consolidated=False) if path.exists() else None


def _inputs(store: xr.Dataset, t: int, li: int) -> np.ndarray:
    """Model inputs for every grid cell of one cycle and lead: (cells, inputs)."""
    columns = []
    for feature in FEATURES:
        values = store[feature.name].values
        columns.append((values[t, li] if feature.per_lead else values[t]).ravel())
    columns.append(np.full(columns[0].shape, float(store["lead_min"].values[li]), dtype="float32"))
    return np.column_stack(columns).astype("float32")


def _scores(predicted: np.ndarray, observed: np.ndarray) -> tuple[int, int, int]:
    hits = int((predicted & observed).sum())
    return hits, int((~predicted & observed).sum()), int((predicted & ~observed).sum())


def _best_threshold(probability: np.ndarray, observed: np.ndarray) -> float:
    """The probability threshold giving the best CSI on the rows the model was trained on."""
    best, best_csi = 0.5, -1.0
    for threshold in THRESHOLDS:
        hits, misses, false_alarms = _scores(probability >= threshold, observed)
        csi = hits / max(1, hits + misses + false_alarms)
        if csi > best_csi:
            best, best_csi = float(threshold), csi
    return best


def run_event(settings: Settings, event_id: str) -> dict:
    cfg = settings.ml
    store = open_features(settings, event_id)
    if store is None:
        raise SystemExit(f"features have not been built for event '{event_id}'; run vajra.features first")
    store = store.load()
    forecast = open_forecast(settings, event_id).load()
    times = store["time"].values
    leads = [int(x) for x in store["lead_min"].values]
    has_forecast = store["has_forecast"].values.astype(bool)
    shape = store["refl"].shape[1:]
    step = np.timedelta64(settings.cycle.interval_min, "m")
    rng = np.random.default_rng(cfg.seed)
    params = {
        "objective": "binary",
        "learning_rate": cfg.learning_rate,
        "num_leaves": 31,
        "min_data_in_leaf": 200,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 1,
        "lambda_l2": 1.0,
        "seed": cfg.seed,
        "deterministic": True,
        "force_row_wise": True,
        "num_threads": 4,
        "verbose": -1,
    }

    # Every (cycle, lead) block that has a nowcast: its inputs, and when its outcome became known.
    blocks = {
        (t, li): _inputs(store, t, li)
        for t in range(len(times)) if has_forecast[t] for li in range(len(leads))
    }
    (settings.ml_dir / event_id).mkdir(parents=True, exist_ok=True)
    out_shape = (len(times), len(leads), *shape)
    probability = {target: np.full(out_shape, np.nan, dtype="float32") for target in TARGETS}
    threshold = {target: np.full(len(times), np.nan) for target in TARGETS}
    trained_rows = {target: np.zeros(len(times), dtype="int64") for target in TARGETS}
    trained_leads = {target: np.zeros((len(times), len(leads)), dtype=bool) for target in TARGETS}
    started = time.perf_counter()

    for target, label_name in TARGETS.items():
        labels = store[label_name].values
        for now in range(len(times)):
            if not has_forecast[now]:
                continue
            # Training rows: forecasts issued earlier whose outcome had been observed
            # at least one cycle before now (a radar scan takes that long to arrive).
            x_parts, y_parts, seen = [], [], set()
            for (t, li), x in blocks.items():
                if times[t] + np.timedelta64(leads[li], "m") > times[now] - step:
                    continue
                y = labels[t, li].ravel()
                known = np.isfinite(y)
                if known.any():
                    x_parts.append(x[known])
                    y_parts.append(y[known])
                    seen.add(li)
            if not x_parts:
                continue
            x_train, y_train = np.concatenate(x_parts), np.concatenate(y_parts)
            if y_train.min() == y_train.max():
                continue  # only one outcome seen so far; nothing to learn from
            if len(y_train) > cfg.max_training_rows:
                keep = rng.choice(len(y_train), cfg.max_training_rows, replace=False)
                x_train, y_train = x_train[keep], y_train[keep]
            booster = lgb.train(
                params, lgb.Dataset(x_train, y_train, feature_name=INPUTS), num_boost_round=cfg.trees
            )
            cycle = cycle_id(event_id, times[now]).rsplit("_", 1)[1]
            booster.save_model(str(model_path(settings, event_id, target, cycle)))
            threshold[target][now] = _best_threshold(booster.predict(x_train), y_train == 1)
            trained_rows[target][now] = len(y_train)

            basis = forecast["forecast_quality"].values[now] == QUALITY_VALID
            for li in sorted(seen):
                predicted = booster.predict(blocks[(now, li)]).reshape(shape)
                # No probability where the nowcast itself has no observational basis.
                probability[target][now, li] = np.where(basis[li], predicted, np.nan)
                trained_leads[target][now, li] = True
        print(f"{target}: {int((trained_rows[target] > 0).sum())} cycles modelled, {time.perf_counter() - started:.0f}s elapsed")

    dims = ("time", "lead_min", "lat", "lon")
    ds = xr.Dataset(
        {
            "ml_storm_probability": (dims, probability["storm"]),
            "ml_lightning_probability": (dims, probability["lightning"]),
            "storm_threshold": (("time",), threshold["storm"]),
            "lightning_threshold": (("time",), threshold["lightning"]),
            "storm_trained_rows": (("time",), trained_rows["storm"]),
            "lightning_trained_rows": (("time",), trained_rows["lightning"]),
        },
        coords={"time": times, "lead_min": leads, "lat": store["lat"].values, "lon": store["lon"].values},
        attrs={"event_id": event_id, "model_version": MODEL_VERSION, "inputs": INPUTS, **{
            k: v for k, v in params.items() if k not in ("verbose", "num_threads")
        }, "trees": cfg.trees},
    )
    chunks = {"chunks": (1, 1, *shape)}
    ds.to_zarr(
        ml_path(settings, event_id), mode="w", consolidated=False,
        encoding={"ml_storm_probability": chunks, "ml_lightning_probability": chunks},
    )

    report = _verify(settings, event_id, store, forecast, probability, threshold)
    verification_path(settings, event_id).write_text(json.dumps(report, indent=2), encoding="utf-8")
    _record(settings, event_id, report)
    return report


def _method_row(name, predicted, prob, observed) -> dict:
    hits, misses, false_alarms = _scores(predicted, observed)
    row = {
        "method": name,
        "pod": hits / max(1, hits + misses),
        "far": false_alarms / max(1, hits + false_alarms),
        "csi": hits / max(1, hits + misses + false_alarms),
        "brier": float(np.mean((prob - observed) ** 2)),
    }
    row["auc"] = float(roc_auc_score(observed, prob)) if 0 < observed.sum() < observed.size else None
    return row


def _verify(settings, event_id, store, forecast, probability, threshold) -> dict:
    """Skill of the ML probabilities against persistence and extrapolation, on the
    same cells and cycles, pooled over every cycle whose outcome is known."""
    storm_dbz = settings.nowcast.storm_threshold_dbz
    leads = [int(x) for x in store["lead_min"].values]
    rows = []
    for target, label_name in TARGETS.items():
        labels = store[label_name].values
        for li, lead in enumerate(leads):
            ml_p, ml_yes, persist, extrap, extrap_p, truth = [], [], [], [], [], []
            cycles = 0
            for t in range(len(store["time"])):
                p = probability[target][t, li]
                y = labels[t, li]
                # Compared only where all three methods and the outcome exist.
                if target == "storm":
                    now, moved = store["refl"].values[t], store["refl_extrap"].values[t, li]
                    moved_p = store["steps_storm_prob"].values[t, li]
                else:
                    now, moved = store["ltg_nbr"].values[t], store["ltg_extrap"].values[t, li]
                    moved_p = None
                ok = np.isfinite(p) & np.isfinite(y) & np.isfinite(now) & np.isfinite(moved)
                if moved_p is not None:
                    ok &= np.isfinite(moved_p)
                if not ok.any():
                    continue
                cycles += 1
                level = storm_dbz if target == "storm" else 0.5
                ml_p.append(p[ok]); ml_yes.append(p[ok] >= threshold[target][t])
                persist.append(now[ok] >= level); extrap.append(moved[ok] >= level)
                extrap_p.append(moved_p[ok] if moved_p is not None else (moved[ok] >= level).astype("float32"))
                truth.append(y[ok] == 1)
            entry = {"target": target, "lead_min": lead, "cycles": cycles, "cells": 0, "positive_rate": None, "methods": []}
            if cycles:
                obs = np.concatenate(truth)
                entry["cells"] = int(obs.size)
                entry["positive_rate"] = float(obs.mean())
                persisted = np.concatenate(persist)
                entry["methods"] = [
                    _method_row("ML (LightGBM)", np.concatenate(ml_yes), np.concatenate(ml_p), obs),
                    _method_row("Extrapolation", np.concatenate(extrap), np.concatenate(extrap_p), obs),
                    _method_row("Persistence", persisted, persisted.astype("float32"), obs),
                ]
            rows.append(entry)
    return {
        "event_id": event_id,
        "model_version": MODEL_VERSION,
        "reliability": _reliability(store, probability),
        "method": (
            "Rolling origin: each cycle's model is trained only on outcomes observed before that cycle. "
            "All scores are out-of-sample."
        ),
        "storm_threshold_dbz": storm_dbz,
        "lightning_neighbourhood_km": settings.lightning.footprint_km,
        "rows": rows,
    }


def _reliability(store, probability) -> list[dict]:
    """Calibration: of the cells given a probability in each band, how many had the
    event? A well-calibrated model's observed rate matches its predicted probability."""
    edges = np.linspace(0.0, 1.0, 6)
    out = []
    for target, label_name in TARGETS.items():
        p, y = probability[target], store[label_name].values
        known = np.isfinite(p) & np.isfinite(y)
        bins = []
        for low, high in zip(edges[:-1], edges[1:]):
            inside = known & (p >= low) & ((p < high) | (high == 1.0))
            if inside.any():
                bins.append({
                    "from": float(low), "to": float(high), "cells": int(inside.sum()),
                    "mean_predicted": float(p[inside].mean()), "observed_rate": float(y[inside].mean()),
                })
        out.append({"target": target, "bins": bins})
    return out


def verify_only(settings: Settings, event_id: str) -> dict:
    """Recompute the verification report from the stored probabilities, without retraining."""
    stored = open_ml(settings, event_id)
    if stored is None:
        raise SystemExit(f"the ML nowcast has not been trained for event '{event_id}'")
    stored = stored.load()
    store = open_features(settings, event_id).load()
    forecast = open_forecast(settings, event_id).load()
    probability = {target: stored[f"ml_{target}_probability"].values for target in TARGETS}
    threshold = {target: stored[f"{target}_threshold"].values for target in TARGETS}
    report = _verify(settings, event_id, store, forecast, probability, threshold)
    verification_path(settings, event_id).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def _record(settings: Settings, event_id: str, report: dict) -> None:
    headline = {}
    for row in report["rows"]:
        for method in row["methods"]:
            if method["method"].startswith("ML"):
                headline[f"{row['target']}_{row['lead_min']}min_csi"] = round(method["csi"], 4)
                headline[f"{row['target']}_{row['lead_min']}min_brier"] = round(method["brier"], 4)
    db.init_db(settings.db_path)
    with db.connect(settings.db_path) as conn:
        conn.execute("UPDATE model_versions SET is_active = 0 WHERE kind = 'ml'")
        conn.execute(
            "INSERT INTO model_versions (model_version, kind, trained_at, train_events, metrics_json, is_active) "
            "VALUES (?, 'ml', ?, ?, ?, 1) ON CONFLICT(model_version) DO UPDATE SET "
            "trained_at = excluded.trained_at, train_events = excluded.train_events, "
            "metrics_json = excluded.metrics_json, is_active = 1",
            (MODEL_VERSION, db.utc_now(), json.dumps([event_id]), json.dumps(headline)),
        )
    db.log_event(settings.db_path, "info", "ml", f"{MODEL_VERSION} trained and verified on event {event_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Train and verify the ML nowcast for a storm event.")
    parser.add_argument("--event", required=True, help="event id from config.yaml")
    parser.add_argument(
        "--verify-only", action="store_true", help="recompute the skill report without retraining"
    )
    args = parser.parse_args()
    settings = get_settings()
    settings.ensure_dirs()
    report = verify_only(settings, args.event) if args.verify_only else run_event(settings, args.event)
    print(report["method"])
    for item in report["reliability"]:
        print(f"calibration, {item['target']}: predicted -> observed  " + "  ".join(
            f"{b['mean_predicted']:.2f}->{b['observed_rate']:.2f}" for b in item["bins"]
        ))
    print(f"{'target':10s} {'lead':>5s} {'cycles':>6s} {'base':>6s}   method           POD    FAR    CSI   Brier    AUC")
    for row in report["rows"]:
        if not row["methods"]:
            print(f"{row['target']:10s} +{row['lead_min']:3d}   {'0':>5s}         not verifiable: no cycle has both a prediction and an observed outcome")
            continue
        for m in row["methods"]:
            auc = f"{m['auc']:.3f}" if m["auc"] is not None else "  -  "
            print(f"{row['target']:10s} +{row['lead_min']:3d}   {row['cycles']:5d} {row['positive_rate']:6.3f}   {m['method']:15s} {m['pod']:.3f}  {m['far']:.3f}  {m['csi']:.3f}  {m['brier']:.4f}  {auc}")


if __name__ == "__main__":
    main()
