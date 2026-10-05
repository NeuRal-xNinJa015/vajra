"""Loads config.yaml into typed settings. Region and grid live here, never in code."""

import os
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

PROJECT_ROOT = Path(__file__).resolve().parents[2]

SOURCE_TYPES = ("radar", "satellite", "lightning", "model")


class Bounds(BaseModel):
    west: float = Field(ge=-180, le=180)
    south: float = Field(ge=-90, le=90)
    east: float = Field(ge=-180, le=180)
    north: float = Field(ge=-90, le=90)

    @model_validator(mode="after")
    def _ordered(self) -> "Bounds":
        if self.west >= self.east or self.south >= self.north:
            raise ValueError("region bounds must satisfy west < east and south < north")
        return self


class Region(BaseModel):
    name: str
    bounds: Bounds


class GridConfig(BaseModel):
    resolution_km: float = Field(gt=0)


class CycleConfig(BaseModel):
    interval_min: int = Field(gt=0)
    max_data_age_min: int = Field(gt=0)
    lead_times_min: list[int] = Field(min_length=1)


class RadarConfig(BaseModel):
    format: str
    reflectivity_field: str
    correlation_field: str | None = None
    rhohv_min: float = Field(ge=0, le=1)
    max_range_km: float = Field(gt=0)
    speckle_min_cells: int = Field(ge=1)


class LightningConfig(BaseModel):
    format: str
    min_coverage: float = Field(gt=0, le=1)
    footprint_km: float = Field(gt=0)


class SatelliteConfig(BaseModel):
    format: str


class NwpConfig(BaseModel):
    format: str
    availability_delay_min: int = Field(ge=0)


class NowcastConfig(BaseModel):
    echo_threshold_dbz: float
    storm_threshold_dbz: float
    ensemble_members: int = Field(ge=2)
    cascade_levels: int = Field(ge=1)
    history_max_age_min: int = Field(gt=0)
    seed: int


class MlConfig(BaseModel):
    max_training_rows: int = Field(gt=0)
    trees: int = Field(gt=0)
    learning_rate: float = Field(gt=0)
    seed: int


class CellsConfig(BaseModel):
    core_threshold_dbz: float
    min_area_km2: float = Field(gt=0)
    max_match_km: float = Field(gt=0)
    strong_dbz: float
    severe_dbz: float


class RiskConfig(BaseModel):
    likelihood_medium: float = Field(gt=0, lt=1)
    likelihood_high: float = Field(gt=0, lt=1)


class AlertsConfig(BaseModel):
    valid_min: int = Field(gt=0)
    suggest_from: str
    sender: str
    sender_name: str


class AuthConfig(BaseModel):
    required: bool = False
    # Sign-in accepts any user ID and password. No password is checked.
    open_access: bool = False
    session_hours: float = Field(default=12, gt=0)
    max_failed_attempts: int = Field(default=5, ge=1)
    lockout_min: int = Field(default=15, ge=1)


class EventConfig(BaseModel):
    id: str
    start: datetime
    end: datetime

    @model_validator(mode="after")
    def _ordered(self) -> "EventConfig":
        if self.start >= self.end:
            raise ValueError(f"event {self.id}: start must be before end")
        return self


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8756


class PathsConfig(BaseModel):
    data_dir: Path


class SourcesConfig(BaseModel):
    radar: Path
    satellite: Path
    lightning: Path
    model: Path


class Settings(BaseModel):
    region: Region
    grid: GridConfig
    cycle: CycleConfig
    radar: RadarConfig
    lightning: LightningConfig
    satellite: SatelliteConfig
    nwp: NwpConfig
    nowcast: NowcastConfig
    ml: MlConfig
    cells: CellsConfig
    risk: RiskConfig
    alerts: AlertsConfig
    events: list[EventConfig] = []
    auth: AuthConfig = AuthConfig()
    server: ServerConfig = ServerConfig()
    paths: PathsConfig
    sources: SourcesConfig

    @property
    def data_dir(self) -> Path:
        path = self.paths.data_dir
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def db_path(self) -> Path:
        return self.data_dir / "vajra.db"

    @property
    def cube_dir(self) -> Path:
        return self.data_dir / "cube"

    @property
    def forecast_dir(self) -> Path:
        return self.data_dir / "forecast"

    @property
    def features_dir(self) -> Path:
        return self.data_dir / "features"

    @property
    def ml_dir(self) -> Path:
        return self.data_dir / "ml"

    def event(self, event_id: str) -> EventConfig:
        for event in self.events:
            if event.id == event_id:
                return event
        raise KeyError(f"event '{event_id}' is not defined in config.yaml")

    def source_dir(self, source_type: str) -> Path:
        path: Path = getattr(self.sources, source_type)
        return path if path.is_absolute() else self.data_dir / path

    def ensure_dirs(self) -> None:
        self.cube_dir.mkdir(parents=True, exist_ok=True)
        self.forecast_dir.mkdir(parents=True, exist_ok=True)
        self.features_dir.mkdir(parents=True, exist_ok=True)
        self.ml_dir.mkdir(parents=True, exist_ok=True)
        for source_type in SOURCE_TYPES:
            self.source_dir(source_type).mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    config_path = Path(os.environ.get("VAJRA_CONFIG", PROJECT_ROOT / "config.yaml"))
    with config_path.open(encoding="utf-8") as f:
        return Settings.model_validate(yaml.safe_load(f))
