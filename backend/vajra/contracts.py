"""Typed API contracts shared by the backend and the frontend.

Endpoints are implemented phase by phase; the shapes are fixed here so both
sides build against the same thing. All datetimes are UTC.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SourceType = Literal["radar", "satellite", "lightning", "model"]
RiskLevel = Literal["low", "moderate", "high", "severe"]
AlertStatus = Literal["suggested", "approved", "edited", "rejected"]
FeedbackVerdict = Literal["correct", "partly_correct", "incorrect"]

# [west, south, east, north] in degrees
BoundsList = tuple[float, float, float, float]


# ---- system -------------------------------------------------------------

class Health(BaseModel):
    status: Literal["ok"]
    version: str
    # Whether the interface must sign a user in before it can load anything else.
    auth_required: bool = False


Role = Literal["viewer", "forecaster", "admin"]


class UserInfo(BaseModel):
    username: str
    display_name: str
    role: Role


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class LoginResponse(BaseModel):
    # Sent as "Authorization: Bearer <token>" on every later request.
    token: str
    expires_at: datetime
    user: UserInfo


class GridInfo(BaseModel):
    resolution_km: float
    height: int
    width: int


class Status(BaseModel):
    app: str
    version: str
    mode: Literal["replay"]  # V1 never runs live; the UI must show DEMO / REPLAY
    region_name: str
    bounds: BoundsList
    grid: GridInfo
    cycle_interval_min: int
    lead_times_min: list[int]
    database_ok: bool
    events: list[str]
    server_time: datetime


class SourceStatus(BaseModel):
    source_type: SourceType
    source_id: str
    path: str
    file_count: int
    latest_obs_time: datetime | None = None
    data_age_min: float | None = None


class DataStatus(BaseModel):
    sources: list[SourceStatus]
    missing: list[SourceType]


class ModelVersion(BaseModel):
    model_version: str
    kind: str
    trained_at: datetime | None = None
    train_events: list[str] = []
    metrics: dict[str, float] = {}
    is_active: bool = False


class ModelInfo(BaseModel):
    active: ModelVersion | None
    versions: list[ModelVersion]


# ---- replay -------------------------------------------------------------

ReplayStatus = Literal["no_event", "paused", "playing", "finished"]


class ReplayState(BaseModel):
    mode: Literal["replay"] = "replay"
    status: ReplayStatus
    event_id: str | None = None
    cycle_id: str | None = None
    cycle_index: int
    cycle_count: int
    cycle_time: datetime | None = None
    cycle_times: list[datetime] = []
    cycle_has_observation: list[bool] = []
    speed: int  # time compression relative to real time
    speeds: list[int]
    seconds_per_cycle: float


class ReplaySeek(BaseModel):
    index: int = Field(ge=0)


class ReplaySpeed(BaseModel):
    speed: int


# ---- cycles and forecasts -----------------------------------------------

class CycleSummary(BaseModel):
    cycle_id: str
    event_id: str
    cycle_time: datetime
    mode: Literal["replay"]
    status: str
    model_version: str | None = None
    latency_ms: dict[str, float] = {}


class LegendEntry(BaseModel):
    label: str
    color: str  # CSS colour


class FieldLayer(BaseModel):
    """A gridded field rendered by the backend for the map."""

    variable: str
    units: str
    lead_time_min: int
    valid_time: datetime
    image_url: str
    bounds: BoundsList
    # Observations only: when the data was measured, and how old it was at valid_time.
    obs_time: datetime | None = None
    data_age_min: float | None = None
    valid_fraction: float | None = None
    legend: list[LegendEntry] = []
    # Headline numbers for the layer, e.g. {"flashes": 1428}.
    stats: dict[str, float] = {}


class EventInfo(BaseModel):
    event_id: str
    times: list[datetime]
    variables: list[str]


class Forecast(BaseModel):
    cycle_id: str
    cycle_time: datetime
    # A cycle may have no forecast (e.g. too few recent radar scans); `reason` says why.
    available: bool = True
    reason: str | None = None
    model_name: str | None = None
    model_version: str | None = None
    ensemble_members: int | None = None
    compute_seconds: float | None = None
    motion_speed_kmh: float | None = None
    motion_toward_deg: float | None = None  # compass bearing the storms move toward
    layers: list[FieldLayer] = []


class TimelineCycle(BaseModel):
    cycle_id: str
    cycle_time: datetime
    observation: FieldLayer
    # Observed lightning for the interval before the cycle; None if the event has none.
    lightning: FieldLayer | None = None
    forecast: Forecast


class Timeline(BaseModel):
    """Everything the map needs for every cycle of an event, in one response."""

    event_id: str
    cycles: list[TimelineCycle]


class SkillMethod(BaseModel):
    method: str
    pod: float
    far: float
    csi: float
    brier: float
    auc: float | None = None


class SkillRow(BaseModel):
    target: str  # "storm" or "lightning"
    lead_min: int
    cycles: int  # cycles with both a prediction and an observed outcome
    cells: int
    positive_rate: float | None = None
    methods: list[SkillMethod] = []  # empty when the lead could not be verified


class Verification(BaseModel):
    """Measured skill of the ML nowcast against simpler methods on the same cells."""

    event_id: str
    model_version: str
    method: str
    storm_threshold_dbz: float
    lightning_neighbourhood_km: float
    rows: list[SkillRow]
    # Calibration per target: for each probability band, the mean predicted
    # probability against how often the event was observed.
    reliability: list[dict] = []


class Uncertainty(BaseModel):
    """Forecast spread. This is not a probability and not data quality."""

    cycle_id: str
    layers: list[FieldLayer]


class FeatureContribution(BaseModel):
    feature: str
    description: str
    source: str  # radar, lightning, satellite, model, nowcast or context
    value: float | None  # the input's mean value under the cell
    # How far this input moved the model's output for this prediction, in log-odds:
    # positive raised the probability, negative lowered it. This describes the
    # model's arithmetic; it is not evidence of physical cause.
    contribution: float


class Explanation(BaseModel):
    cycle_id: str
    cell_id: str | None = None
    lead_time_min: int
    target: Literal["storm", "lightning"]
    available: bool = True
    reason: str | None = None
    probability: float | None = None  # the model's output for this cell
    base_probability: float | None = None  # its output before any input is considered
    contributions: list[FeatureContribution] = []  # largest effect first


# ---- storm cells --------------------------------------------------------

class StormCell(BaseModel):
    cell_id: str
    cycle_id: str
    track_id: str
    lead_time_min: int
    centroid: tuple[float, float]  # (lon, lat)
    polygon: dict  # GeoJSON geometry
    area_km2: float
    severity: str | None = None
    probability: float | None = Field(default=None, ge=0, le=1)
    motion_dir_deg: float | None = None  # compass bearing the cell moves toward
    speed_kmh: float | None = None
    uncertainty: float | None = None
    max_dbz: float | None = None
    mean_dbz: float | None = None
    # ML probabilities under the cell's projected outline; None where the model
    # could not yet predict that lead.
    ml_storm_probability: float | None = Field(default=None, ge=0, le=1)
    ml_lightning_probability: float | None = Field(default=None, ge=0, le=1)


class RiskAssessment(BaseModel):
    level: RiskLevel
    # Highest storm or lightning probability over the alert period, and where it came from.
    likelihood: float | None = None
    likelihood_source: Literal["ml", "ensemble", "none"]
    # The facts the level was derived from, in plain words.
    factors: list[str]


class TrackPoint(BaseModel):
    cycle_time: datetime
    centroid: tuple[float, float]  # (lon, lat)
    max_dbz: float | None = None


class StormCellTrack(BaseModel):
    """One tracked cell at a cycle: where it is, where it has been, where it is heading."""

    track_id: str
    first_seen: datetime
    cell: StormCell  # observed position (lead 0)
    projections: list[StormCell]  # one per forecast lead, nearest first
    history: list[TrackPoint]  # past positions up to and including this cycle
    risk: RiskAssessment


class StormCells(BaseModel):
    cycle_id: str
    cells: list[StormCellTrack]


# ---- alerts and feedback ------------------------------------------------

class AlertPreviewRequest(BaseModel):
    cycle_id: str
    cell_id: str | None = None


class Alert(BaseModel):
    alert_id: str
    cycle_id: str
    cell_id: str | None = None
    risk_level: RiskLevel
    headline: str
    description: str
    polygon: dict  # GeoJSON geometry
    valid_from: datetime
    valid_to: datetime
    status: AlertStatus
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    created_at: datetime | None = None
    track_id: str | None = None
    cap_xml: str | None = None  # preview only; V1 never disseminates


class Alerts(BaseModel):
    cycle_id: str
    alerts: list[Alert]


class AlertDecision(BaseModel):
    forecaster: str = Field(min_length=1)
    # Optional edits applied on approval; status becomes "edited" if any are set.
    risk_level: RiskLevel | None = None
    headline: str | None = None
    description: str | None = None


class FeedbackIn(BaseModel):
    cycle_id: str
    alert_id: str | None = None
    cell_id: str | None = None
    forecaster: str = Field(min_length=1)
    verdict: FeedbackVerdict
    comment: str | None = None


class Feedback(FeedbackIn):
    feedback_id: int
    created_at: datetime


class FeedbackList(BaseModel):
    cycle_id: str
    feedback: list[Feedback]


class SystemEvent(BaseModel):
    ts: datetime
    level: str
    component: str
    message: str
