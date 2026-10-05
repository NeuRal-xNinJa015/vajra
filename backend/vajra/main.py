"""FastAPI application. Endpoints are added phase by phase against contracts.py."""

import asyncio
import json
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from vajra import __version__, alerts, auth, cell_store, cube, db, explain, feedback, timeline
from vajra.config import SOURCE_TYPES, get_settings
from vajra.contracts import (
    Alert,
    AlertDecision,
    AlertPreviewRequest,
    Alerts,
    CycleSummary,
    DataStatus,
    EventInfo,
    Explanation,
    Feedback,
    FeedbackIn,
    FeedbackList,
    FieldLayer,
    Forecast,
    GridInfo,
    Health,
    LoginRequest,
    LoginResponse,
    ModelInfo,
    ModelVersion,
    ReplaySeek,
    ReplaySpeed,
    ReplayState,
    SourceStatus,
    Status,
    StormCell,
    StormCells,
    SystemEvent,
    Timeline,
    UserInfo,
    Verification,
)
from vajra.cube import list_events
from vajra.grid import build_grid
from vajra.replay import ReplayEngine, cycle_summary

# Image URLs carry the data version, so a given URL never changes content.
IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.ensure_dirs()
    db.init_db(settings.db_path)
    db.log_event(settings.db_path, "info", "backend", f"VAJRA backend {__version__} started")
    # The app starts the backend, so a fresh start means the app was reopened:
    # nobody stays signed in across that.
    auth.end_all_sessions(settings)
    app.state.replay = ReplayEngine(settings)
    clock = asyncio.create_task(app.state.replay.run())
    # Build the timelines and load the ML library in the background, so the first
    # screen and the first explanation do not wait for them.
    def warm() -> None:
        timeline.warm()
        explain.warm()

    warmup = asyncio.create_task(asyncio.to_thread(warm))
    yield
    clock.cancel()
    warmup.cancel()


# With authentication on, every route needs a signed-in user (auth.OPEN_PATHS
# excepted) and the interactive API docs are not served.
_AUTH_ON = get_settings().auth.required
app = FastAPI(
    title="VAJRA",
    version=__version__,
    lifespan=lifespan,
    dependencies=[Depends(auth.current_user)],
    docs_url=None if _AUTH_ON else "/docs",
    redoc_url=None if _AUTH_ON else "/redoc",
    openapi_url=None if _AUTH_ON else "/openapi.json",
)
# Replay control, alert decisions and feedback need the forecaster role.
FORECASTER = Depends(auth.require_role("forecaster"))

# The React UI calls this API directly: Vite dev server, and the Tauri webview.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://tauri.localhost", "tauri://localhost"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---- system -------------------------------------------------------------

@app.get("/health", response_model=Health)
def health() -> Health:
    return Health(status="ok", version=__version__, auth_required=get_settings().auth.required)


# ---- sign-in ------------------------------------------------------------

def _info(user: auth.User) -> UserInfo:
    return UserInfo(username=user.username, display_name=user.display_name, role=user.role)


@app.post("/auth/login", response_model=LoginResponse)
def login(body: LoginRequest) -> LoginResponse:
    try:
        token, expires_at, user = auth.sign_in(get_settings(), body.username, body.password)
    except auth.AuthError as err:
        raise HTTPException(err.status, str(err)) from err
    return LoginResponse(token=token, expires_at=expires_at, user=_info(user))


@app.post("/auth/logout", status_code=204)
def logout(request: Request) -> Response:
    token = auth.bearer_token(request)
    if token:
        auth.sign_out(get_settings(), token)
    return Response(status_code=204)


@app.get("/auth/me", response_model=UserInfo)
def me(user: auth.User | None = Depends(auth.current_user)) -> UserInfo:
    if user is None:
        raise HTTPException(404, "Sign-in is not enabled on this installation")
    return _info(user)


@app.get("/status", response_model=Status)
def status() -> Status:
    settings = get_settings()
    grid = build_grid(settings)
    height, width = grid.shape
    try:
        with db.connect(settings.db_path) as conn:
            conn.execute("SELECT 1 FROM system_events LIMIT 1")
        database_ok = True
    except sqlite3.Error:
        database_ok = False
    return Status(
        app="VAJRA",
        version=__version__,
        mode="replay",
        region_name=settings.region.name,
        bounds=(grid.west, grid.south, grid.east, grid.north),
        grid=GridInfo(resolution_km=grid.resolution_km, height=height, width=width),
        cycle_interval_min=settings.cycle.interval_min,
        lead_times_min=settings.cycle.lead_times_min,
        database_ok=database_ok,
        events=list_events(settings),
        server_time=datetime.now(timezone.utc),
    )


@app.get("/data-status", response_model=DataStatus)
def data_status() -> DataStatus:
    """Raw input files found on disk. Observation times are filled in by the ingestion phases."""
    settings = get_settings()
    sources: list[SourceStatus] = []
    for source_type in SOURCE_TYPES:
        root = settings.source_dir(source_type)
        if source_type == "radar":
            folders = sorted(p for p in root.iterdir() if p.is_dir()) if root.exists() else []
        else:
            folders = [root] if root.exists() else []
        for folder in folders:
            file_count = sum(1 for p in folder.rglob("*") if p.is_file())
            if file_count == 0:
                continue
            sources.append(
                SourceStatus(
                    source_type=source_type,
                    source_id=folder.name,
                    path=str(folder),
                    file_count=file_count,
                )
            )
    present = {s.source_type for s in sources}
    return DataStatus(sources=sources, missing=[t for t in SOURCE_TYPES if t not in present])


@app.get("/model-info", response_model=ModelInfo)
def model_info() -> ModelInfo:
    settings = get_settings()
    with db.connect(settings.db_path) as conn:
        rows = conn.execute("SELECT * FROM model_versions ORDER BY trained_at DESC").fetchall()
    versions = [
        ModelVersion(
            model_version=row["model_version"],
            kind=row["kind"],
            trained_at=row["trained_at"],
            train_events=json.loads(row["train_events"] or "[]"),
            metrics=json.loads(row["metrics_json"] or "{}"),
            is_active=bool(row["is_active"]),
        )
        for row in rows
    ]
    return ModelInfo(active=next((v for v in versions if v.is_active), None), versions=versions)


# ---- replay -------------------------------------------------------------

@app.get("/replay", response_model=ReplayState)
def replay_state() -> ReplayState:
    return app.state.replay.state()


@app.post("/replay/play", response_model=ReplayState, dependencies=[FORECASTER])
def replay_play() -> ReplayState:
    app.state.replay.play()
    return app.state.replay.state()


@app.post("/replay/pause", response_model=ReplayState, dependencies=[FORECASTER])
def replay_pause() -> ReplayState:
    app.state.replay.pause()
    return app.state.replay.state()


@app.post("/replay/reset", response_model=ReplayState, dependencies=[FORECASTER])
def replay_reset() -> ReplayState:
    app.state.replay.reset()
    return app.state.replay.state()


@app.post("/replay/seek", response_model=ReplayState, dependencies=[FORECASTER])
def replay_seek(body: ReplaySeek) -> ReplayState:
    app.state.replay.seek(body.index)
    return app.state.replay.state()


@app.post("/replay/speed", response_model=ReplayState, dependencies=[FORECASTER])
def replay_speed(body: ReplaySpeed) -> ReplayState:
    try:
        app.state.replay.set_speed(body.speed)
    except ValueError as err:
        raise HTTPException(422, str(err)) from err
    return app.state.replay.state()


# ---- cycles -------------------------------------------------------------

@app.get("/cycles/latest", response_model=CycleSummary)
def latest_cycle() -> CycleSummary:
    current = app.state.replay.current_cycle_id()
    summary = cycle_summary(get_settings(), current) if current else None
    if summary is None:
        raise HTTPException(404, "no cycle has been issued yet")
    return summary


@app.get("/cycles/{cycle_id}", response_model=CycleSummary)
def cycle(cycle_id: str) -> CycleSummary:
    summary = cycle_summary(get_settings(), cycle_id)
    if summary is None:
        raise HTTPException(404, f"cycle '{cycle_id}' has not been issued")
    return summary


# ---- observations and forecasts -----------------------------------------

def _timeline(event_id: str) -> Timeline:
    result = timeline.timeline(event_id)
    if result is None:
        raise HTTPException(404, f"event '{event_id}' has no observation cube")
    return result


def _locate(cycle_id: str) -> tuple[str, int]:
    found = timeline.locate_cycle(cycle_id)
    if found is None:
        raise HTTPException(404, f"cycle '{cycle_id}' does not exist")
    return found


@app.get("/events/{event_id}", response_model=EventInfo)
def event_info(event_id: str) -> EventInfo:
    cycles = _timeline(event_id).cycles
    ds = timeline.observations(event_id)
    return EventInfo(
        event_id=event_id,
        times=[c.cycle_time for c in cycles],
        variables=[name for name in cube.VARIABLES if name in ds],
    )


@app.get("/events/{event_id}/timeline", response_model=Timeline)
def event_timeline(event_id: str) -> Timeline:
    """Every cycle's observation and forecast layers in one response."""
    return _timeline(event_id)


def _observation_layer(event_id: str, variable: str, index: int) -> FieldLayer:
    cycles = _timeline(event_id).cycles
    if not 0 <= index < len(cycles):
        raise HTTPException(404, f"time index {index} is outside 0..{len(cycles) - 1}")
    layer = {
        timeline.OBSERVED_VARIABLE: cycles[index].observation,
        timeline.LIGHTNING_VARIABLE: cycles[index].lightning,
    }.get(variable)
    if layer is None:
        raise HTTPException(404, f"event '{event_id}' has no variable '{variable}'")
    return layer


@app.get("/events/{event_id}/observations/{variable}/{index}", response_model=FieldLayer)
def observation(event_id: str, variable: str, index: int) -> FieldLayer:
    return _observation_layer(event_id, variable, index)


@app.get("/events/{event_id}/observations/{variable}/{index}/image.png")
def observation_image(event_id: str, variable: str, index: int) -> Response:
    _observation_layer(event_id, variable, index)
    png = timeline.observation_png(event_id, variable, index)
    return Response(png, media_type="image/png", headers=IMMUTABLE)


@app.get("/forecast/{cycle_id}", response_model=Forecast)
def forecast(cycle_id: str) -> Forecast:
    event_id, index = _locate(cycle_id)
    return _timeline(event_id).cycles[index].forecast


@app.get("/forecast/{cycle_id}/{variable}/{lead_min}/image.png")
def forecast_image(cycle_id: str, variable: str, lead_min: int) -> Response:
    event_id, index = _locate(cycle_id)
    layers = _timeline(event_id).cycles[index].forecast.layers
    if not any(layer.variable == variable and layer.lead_time_min == lead_min for layer in layers):
        raise HTTPException(404, f"no '{variable}' for cycle '{cycle_id}' at +{lead_min} min")
    lead_index = get_settings().cycle.lead_times_min.index(lead_min)
    png = timeline.forecast_png(event_id, variable, index, lead_index)
    return Response(png, media_type="image/png", headers=IMMUTABLE)


@app.get("/verification/{event_id}", response_model=Verification)
def verification(event_id: str) -> Verification:
    """Measured, out-of-sample skill of the ML nowcast for an event."""
    path = get_settings().ml_dir / f"{event_id}_verification.json"
    if not path.exists():
        raise HTTPException(404, f"the ML nowcast has not been trained for event '{event_id}'")
    return Verification.model_validate_json(path.read_text(encoding="utf-8"))


# ---- storm cells --------------------------------------------------------

@app.get("/storm-cells", response_model=StormCells)
def storm_cells(cycle_id: str | None = None) -> Response:
    """Storm cells at a cycle (the replay's current cycle by default)."""
    cycle_id = cycle_id or app.state.replay.current_cycle_id()
    if cycle_id is None:
        raise HTTPException(404, "no cycle is loaded")
    _locate(cycle_id)
    # Sent as pre-built JSON: the response is cached per cycle.
    return Response(cell_store.cells_json(get_settings(), cycle_id), media_type="application/json")


# ---- alerts -------------------------------------------------------------
# The platform suggests; a forecaster decides. Nothing is ever disseminated.

@app.get("/alerts", response_model=Alerts)
def alerts_for_cycle(cycle_id: str | None = None) -> Alerts:
    """Alerts at a cycle (the replay's current cycle by default), highest risk first."""
    cycle_id = cycle_id or app.state.replay.current_cycle_id()
    if cycle_id is None:
        raise HTTPException(404, "no cycle is loaded")
    _locate(cycle_id)
    try:
        return Alerts(cycle_id=cycle_id, alerts=alerts.for_cycle(get_settings(), cycle_id))
    except alerts.AlertError as err:
        raise HTTPException(err.status, str(err)) from err


def _as(user: auth.User | None, body):
    """With sign-in on, a decision is recorded under the signed-in user's name,
    whatever name the request carried."""
    return body if user is None else body.model_copy(update={"forecaster": user.display_name})


@app.post("/alerts/preview", response_model=Alert, dependencies=[FORECASTER])
def alert_preview(body: AlertPreviewRequest) -> Alert:
    _locate(body.cycle_id)
    try:
        return alerts.preview(get_settings(), body.cycle_id, body.cell_id)
    except alerts.AlertError as err:
        raise HTTPException(err.status, str(err)) from err


@app.post("/alerts/{alert_id}/approve", response_model=Alert)
def alert_approve(alert_id: str, body: AlertDecision, user: auth.User | None = FORECASTER) -> Alert:
    try:
        return alerts.decide(get_settings(), alert_id, _as(user, body), approve=True)
    except alerts.AlertError as err:
        raise HTTPException(err.status, str(err)) from err


@app.post("/alerts/{alert_id}/reject", response_model=Alert)
def alert_reject(alert_id: str, body: AlertDecision, user: auth.User | None = FORECASTER) -> Alert:
    try:
        return alerts.decide(get_settings(), alert_id, _as(user, body), approve=False)
    except alerts.AlertError as err:
        raise HTTPException(err.status, str(err)) from err


# ---- feedback, explanation, audit log -----------------------------------

@app.post("/feedback", response_model=Feedback)
def feedback_add(body: FeedbackIn, user: auth.User | None = FORECASTER) -> Feedback:
    """Record a forecaster's verdict on a cycle, alert or storm cell. Stored only;
    it never changes a model."""
    try:
        return feedback.add(get_settings(), _as(user, body))
    except feedback.FeedbackError as err:
        raise HTTPException(err.status, str(err)) from err


@app.get("/feedback", response_model=FeedbackList)
def feedback_for_cycle(cycle_id: str | None = None) -> FeedbackList:
    cycle_id = cycle_id or app.state.replay.current_cycle_id()
    if cycle_id is None:
        raise HTTPException(404, "no cycle is loaded")
    _locate(cycle_id)
    return FeedbackList(cycle_id=cycle_id, feedback=feedback.for_cycle(get_settings(), cycle_id))


@app.get("/explanation/{cycle_id}", response_model=Explanation)
def explanation(
    cycle_id: str, cell_id: str, lead_min: int = 30, target: Literal["storm", "lightning"] = "lightning"
) -> Explanation:
    """Which inputs moved the ML probability for a storm cell, and by how much."""
    try:
        return explain.explain(cycle_id, cell_id, lead_min, target)
    except KeyError as err:
        raise HTTPException(404, str(err.args[0])) from err


@app.get("/performance/{event_id}")
def performance(event_id: str) -> dict:
    """Measured processing time per stage for an event, from its last full pipeline run."""
    path = get_settings().data_dir / "performance" / f"{event_id}.json"
    if not path.exists():
        raise HTTPException(404, f"no pipeline run has been timed for event '{event_id}'")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/system-events", response_model=list[SystemEvent])
def system_events(limit: int = 50) -> list[SystemEvent]:
    """Audit log, newest first: ingestion, forecasts, alert decisions and feedback."""
    with db.connect(get_settings().db_path) as conn:
        rows = conn.execute(
            "SELECT ts, level, component, message FROM system_events ORDER BY id DESC LIMIT ?",
            (max(1, min(limit, 500)),),
        ).fetchall()
    return [SystemEvent(**dict(row)) for row in rows]


@app.get("/storm-cells/{cell_id}", response_model=StormCell)
def storm_cell(cell_id: str) -> StormCell:
    found = cell_store.cell(get_settings(), cell_id)
    if found is None:
        raise HTTPException(404, f"storm cell '{cell_id}' does not exist")
    return found
