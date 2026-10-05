"""Radar nowcast: storm motion, extrapolation and a STEPS ensemble (pySTEPS).

A forecast is built only from radar scans that were available at the cycle time.
Scans are not evenly spaced and are several minutes old when a cycle runs, so
the model works in real time units: motion is measured in cells per minute from
the scans' own timestamps, and each scan is carried forward by its data age
plus the lead time.
"""

import contextlib
import io
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from vajra.config import NowcastConfig
from vajra.cube import QUALITY_NO_DATA, QUALITY_VALID
from vajra.grid import Grid
from vajra.radar import NO_ECHO_DBZ

MINUTE = np.timedelta64(1, "m")
# Coarse-to-fine block sizes for the variational echo tracking motion estimate.
VET_SECTORS = ((16, 8, 4), (16, 8, 4))
# Motion is summarised over cells with at least this much echo.
MOTION_SUMMARY_DBZ = 20.0


@dataclass
class Scan:
    """One observed field on the grid, with the time it was measured."""

    values: np.ndarray  # dBZ, NaN where not valid
    quality: np.ndarray
    obs_time: np.datetime64


@dataclass
class Nowcast:
    """Forecast fields for each lead time, shape (lead, lat, lon)."""

    reflectivity: np.ndarray  # dBZ; deterministic extrapolation
    storm_probability: np.ndarray  # fraction of members at or above the storm threshold
    spread: np.ndarray  # dBZ; standard deviation across members
    quality: np.ndarray  # QUALITY_VALID where the forecast is backed by observed coverage
    # Storm motion field, (lat, lon), in grid cells per minute: east and north components.
    motion_east: np.ndarray
    motion_north: np.ndarray
    motion_speed_kmh: float
    motion_toward_deg: float  # compass bearing the storms are moving toward


class NowcastModel(Protocol):
    """Interface every nowcast model implements, so models can be swapped."""

    name: str
    version: str

    def forecast(
        self, scans: list[Scan], cycle_time: np.datetime64, lead_times_min: list[int], seed: int
    ) -> Nowcast: ...


class PystepsNowcast:
    name = "pySTEPS extrapolation + STEPS ensemble"
    version = "pysteps-steps-1"

    def __init__(self, cfg: NowcastConfig, grid: Grid, cycle_interval_min: int) -> None:
        self._cfg = cfg
        self._grid = grid
        self._interval = cycle_interval_min
        # Fields below the echo threshold are set to this, as pySTEPS expects.
        self._zero = cfg.echo_threshold_dbz - 5.0

    def _echo_field(self, scan: Scan) -> np.ndarray:
        echo = np.isfinite(scan.values) & (scan.values >= self._cfg.echo_threshold_dbz)
        return np.where(echo, scan.values, self._zero).astype("float64")

    def _motion(self, scans: list[Scan]) -> np.ndarray:
        """Storm motion in grid cells per minute, averaged over consecutive scan pairs."""
        from pysteps import motion

        vet = motion.get_method("VET")
        fields = []
        for earlier, later in zip(scans[:-1], scans[1:]):
            minutes = float((later.obs_time - earlier.obs_time) / MINUTE)
            pair = np.stack([self._echo_field(earlier), self._echo_field(later)])
            fields.append(vet(pair, sectors=VET_SECTORS, verbose=False) / minutes)
        return np.mean(fields, axis=0)

    def forecast(
        self, scans: list[Scan], cycle_time: np.datetime64, lead_times_min: list[int], seed: int
    ) -> Nowcast:
        # pySTEPS is imported here so that only forecast runs pay its start-up cost,
        # not the API server.
        from pysteps import nowcasts
        from pysteps.extrapolation.semilagrangian import extrapolate

        cfg = self._cfg
        latest = scans[-1]
        age = float((cycle_time - latest.obs_time) / MINUTE)
        with contextlib.redirect_stdout(io.StringIO()):  # pySTEPS prints progress
            velocity = self._motion(scans)

            # Deterministic forecast: carry the latest scan forward by its age plus the lead.
            horizons = [age + lead for lead in lead_times_min]
            field = extrapolate(self._echo_field(latest), velocity, horizons, outval=np.nan)
            # Carry the radar coverage the same way, so the forecast knows where it has no basis.
            # A QC-rejected cell was still observed, so it counts as covered.
            covered = (latest.quality != QUALITY_NO_DATA).astype("float64")
            coverage = extrapolate(covered, velocity, horizons, outval=0.0)

            # Ensemble: STEPS needs evenly spaced inputs, so bring each scan to the cycle grid.
            aligned = []
            for steps_back, scan in zip(range(len(scans) - 1, -1, -1), scans):
                target = cycle_time - np.timedelta64(self._interval * steps_back, "m")
                advance = float((target - scan.obs_time) / MINUTE)
                aligned.append(
                    extrapolate(self._echo_field(scan), velocity, [advance], outval=self._zero)[0]
                )
            n_steps = max(lead_times_min) // self._interval
            members = nowcasts.get_method("steps")(
                np.stack(aligned),
                velocity * self._interval,
                n_steps,
                n_ens_members=cfg.ensemble_members,
                n_cascade_levels=cfg.cascade_levels,
                precip_thr=cfg.echo_threshold_dbz,
                kmperpixel=self._grid.resolution_km,
                timestep=self._interval,
                noise_method="nonparametric",
                vel_pert_method="bps",
                mask_method="incremental",
                probmatching_method="cdf",
                seed=seed,
                num_workers=4,
            )

        lead_steps = [lead // self._interval - 1 for lead in lead_times_min]
        members = members[:, lead_steps]  # (member, lead, lat, lon); NaN where advected from outside
        member_ok = np.isfinite(members)
        enough = member_ok.mean(axis=0) >= 0.5
        with np.errstate(invalid="ignore"):
            exceed = np.where(member_ok, members >= cfg.storm_threshold_dbz, np.nan)
            probability = np.nanmean(exceed, axis=0)
            spread = np.nanstd(members, axis=0)

        valid = (coverage >= 0.5) & np.isfinite(field)
        quality = np.where(valid, QUALITY_VALID, QUALITY_NO_DATA).astype("uint8")
        with np.errstate(invalid="ignore"):
            reflectivity = np.where(field >= cfg.echo_threshold_dbz, field, NO_ECHO_DBZ)
        reflectivity = np.where(valid, reflectivity, np.nan).astype("float32")
        ensemble_valid = valid & enough
        probability = np.where(ensemble_valid, probability, np.nan).astype("float32")
        spread = np.where(ensemble_valid, spread, np.nan).astype("float32")

        speed, toward = self._summarise_motion(velocity, latest)
        return Nowcast(
            reflectivity, probability, spread, quality,
            velocity[0].astype("float32"), velocity[1].astype("float32"), speed, toward,
        )

    def _summarise_motion(self, velocity: np.ndarray, latest: Scan) -> tuple[float, float]:
        """Mean motion over the echo: speed in km/h and the bearing it moves toward."""
        with np.errstate(invalid="ignore"):
            echo = latest.values >= MOTION_SUMMARY_DBZ
        if not echo.any():
            return 0.0, 0.0
        # velocity[0] is along longitude (east positive), velocity[1] along latitude (north positive).
        east = float(velocity[0][echo].mean())
        north = float(velocity[1][echo].mean())
        speed = float(np.hypot(east, north)) * self._grid.resolution_km * 60.0
        toward = float(np.degrees(np.arctan2(east, north)) % 360.0)
        return speed, toward
