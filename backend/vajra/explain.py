"""Explains an ML probability for a storm cell: which inputs moved it, and by how much.

Uses the model that actually made the prediction (the one trained for that
cycle) and its exact per-prediction contributions, averaged over the grid cells
under the storm cell's projected outline. The contributions describe how the
model arrived at its number. They are not evidence of physical cause.
"""

import json
from functools import lru_cache

import numpy as np
import shapely
from shapely.geometry import shape

from vajra import cube, db, timeline
from vajra.config import get_settings
from vajra.contracts import Explanation, FeatureContribution
from vajra.features import FEATURES, open_features
from vajra.grid import build_grid

_DESCRIBED = {f.name: f for f in FEATURES}


def _unavailable(cycle_id, cell_id, lead_min, target, reason) -> Explanation:
    return Explanation(
        cycle_id=cycle_id, cell_id=cell_id, lead_time_min=lead_min, target=target, available=False, reason=reason
    )


def _cells_under(polygon_geojson: str) -> tuple[np.ndarray, np.ndarray]:
    """Grid rows and columns whose centres fall inside a polygon."""
    grid = build_grid(get_settings())
    outline = shape(json.loads(polygon_geojson))
    lon2d, lat2d = np.meshgrid(grid.lons, grid.lats)
    return np.nonzero(shapely.contains_xy(outline, lon2d, lat2d))


@lru_cache(maxsize=4)
def _features(event_id: str, version: str):
    """The event's feature store, opened once per data version."""
    return open_features(get_settings(), event_id)


def warm() -> None:
    """Load what the first explanation would otherwise wait for: the ML library
    and each event's feature store."""
    import lightgbm  # noqa: F401

    for event_id in cube.list_events(get_settings()):
        if open_features(get_settings(), event_id) is not None:
            _features(event_id, timeline.data_version(event_id))


@lru_cache(maxsize=256)
def _explain(
    cycle_id: str, cell_id: str, lead_min: int, target: str, outline: str | None, version: str
) -> Explanation:
    """`outline` is the cell's projected polygon at the lead time. It is part of the
    cache key, so a rebuilt cell is explained afresh."""
    # Imported here so the API server only loads LightGBM when an explanation is asked for.
    import lightgbm as lgb

    settings = get_settings()
    event_id, t = timeline.locate_cycle(cycle_id)
    if outline is None:
        return _unavailable(cycle_id, cell_id, lead_min, target, "The cell has no projection at this lead time")

    ml = timeline.ml_probabilities(event_id)
    li = settings.cycle.lead_times_min.index(lead_min)
    variable = f"ml_{target}_probability"
    model_file = settings.ml_dir / event_id / f"{target}_{cycle_id.rsplit('_', 1)[1]}.txt"
    if ml is None or not model_file.exists() or not np.isfinite(ml[variable].isel(time=t, lead_min=li).values).any():
        return _unavailable(
            cycle_id, cell_id, lead_min, target,
            "The ML model made no prediction for this lead time at this cycle",
        )

    rows, cols = _cells_under(outline)
    if rows.size == 0:
        return _unavailable(cycle_id, cell_id, lead_min, target, "The projected cell lies outside the forecast domain")

    # The same inputs the model saw, for the grid cells under the projected outline.
    store = _features(event_id, version).isel(time=t)
    columns = []
    for feature in FEATURES:
        field = store[feature.name]
        values = (field.isel(lead_min=li) if feature.per_lead else field).values
        columns.append(values[rows, cols])
    columns.append(np.full(rows.size, float(lead_min), dtype="float32"))
    inputs = np.column_stack(columns).astype("float32")

    booster = lgb.Booster(model_file=str(model_file))
    # One row per grid cell: a contribution per input, then the model's base value.
    contributions = booster.predict(inputs, pred_contrib=True).mean(axis=0)
    with np.errstate(invalid="ignore"):
        means = np.nanmean(inputs, axis=0)
    names = booster.feature_name()
    items = []
    for name, value, contribution in zip(names, means, contributions[:-1]):
        described = _DESCRIBED.get(name)
        items.append(
            FeatureContribution(
                feature=name,
                description=described.description if described else "Forecast lead time",
                source=described.source if described else "context",
                value=None if np.isnan(value) else float(value),
                contribution=float(contribution),
            )
        )
    items.sort(key=lambda item: -abs(item.contribution))
    return Explanation(
        cycle_id=cycle_id,
        cell_id=cell_id,
        lead_time_min=lead_min,
        target=target,
        probability=float(booster.predict(inputs).mean()),
        base_probability=float(1.0 / (1.0 + np.exp(-contributions[-1]))),
        contributions=items,
    )


def explain(cycle_id: str, cell_id: str, lead_min: int, target: str) -> Explanation:
    """Raises KeyError if the cycle, cell or lead does not exist."""
    settings = get_settings()
    located = timeline.locate_cycle(cycle_id)
    if located is None:
        raise KeyError(f"cycle '{cycle_id}' does not exist")
    if lead_min not in settings.cycle.lead_times_min:
        raise KeyError(f"+{lead_min} min is not a forecast lead time")
    with db.connect(settings.db_path) as conn:
        cell = conn.execute(
            "SELECT track_id FROM storm_cells WHERE cell_id = ? AND cycle_id = ? AND lead_time_min = 0",
            (cell_id, cycle_id),
        ).fetchone()
        if cell is None:
            raise KeyError(f"storm cell '{cell_id}' is not part of cycle '{cycle_id}'")
        projected = conn.execute(
            "SELECT polygon_geojson FROM storm_cells WHERE cycle_id = ? AND track_id = ? AND lead_time_min = ?",
            (cycle_id, cell["track_id"], lead_min),
        ).fetchone()
    outline = projected["polygon_geojson"] if projected else None
    return _explain(cycle_id, cell_id, lead_min, target, outline, timeline.data_version(located[0]))
