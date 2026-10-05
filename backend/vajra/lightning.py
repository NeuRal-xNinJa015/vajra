"""Lightning flashes → flash density on the common grid, per cycle.

Each cycle counts the flashes detected in the interval leading up to it, which
is what a live system would have had. A cell with no flash holds 0, a real
observation; a cycle whose interval is not well enough covered by data holds
NaN with the NO_DATA flag.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import netCDF4
import numpy as np

from vajra.config import LightningConfig
from vajra.cube import QUALITY_NO_DATA, QUALITY_VALID
from vajra.grid import Grid

SECOND = np.timedelta64(1, "s")


@dataclass
class FlashFile:
    """The flashes from one source file that fall on the grid."""

    start: np.datetime64  # the period the file covers
    end: np.datetime64
    rows: np.ndarray  # grid cell of each flash
    cols: np.ndarray


@dataclass
class GriddedLightning:
    density: np.ndarray  # (lat, lon) float32 flash count, NaN where not VALID
    change: np.ndarray  # (lat, lon) float32 difference from the previous interval
    quality: np.ndarray  # (lat, lon) uint8
    change_quality: np.ndarray
    obs_time: np.datetime64  # end of the newest data used; NaT if none
    flash_count: int


def _on_grid(lat: np.ndarray, lon: np.ndarray, grid: Grid) -> tuple[np.ndarray, np.ndarray]:
    west, south, _, _ = grid.extent
    height, width = grid.shape
    rows = np.floor((lat - south) / grid.dlat).astype("int64")
    cols = np.floor((lon - west) / grid.dlon).astype("int64")
    inside = (rows >= 0) & (rows < height) & (cols >= 0) & (cols < width)
    return rows[inside], cols[inside]


def _read_glm_l2(path: Path, grid: Grid) -> FlashFile:
    """GOES GLM Level 2 file: one 20-second period of flashes over the whole disk.

    Read with netCDF4 directly; decoding the full file through xarray is ~200x slower.
    Only flashes the product marks as good quality are kept.
    """
    with netCDF4.Dataset(path) as ds:
        lat = np.asarray(ds["flash_lat"][:], dtype="float64")
        lon = np.asarray(ds["flash_lon"][:], dtype="float64")
        good = np.asarray(ds["flash_quality_flag"][:]) == 0
        start = np.datetime64(ds.time_coverage_start.rstrip("Z"), "ns")
        end = np.datetime64(ds.time_coverage_end.rstrip("Z"), "ns")
    rows, cols = _on_grid(lat[good], lon[good], grid)
    return FlashFile(start, end, rows, cols)


# format name → file reader. Add a line here for a new lightning data format.
READERS: dict[str, Callable[[Path, Grid], FlashFile]] = {
    "glm_l2": _read_glm_l2,
}


def read_files(folder: Path, cfg: LightningConfig, grid: Grid) -> tuple[list[FlashFile], list[str]]:
    """Read every lightning file in a folder. Returns the files and the names that failed."""
    if cfg.format not in READERS:
        raise ValueError(f"lightning format '{cfg.format}' has no reader; known: {sorted(READERS)}")
    reader = READERS[cfg.format]
    files: list[FlashFile] = []
    unreadable: list[str] = []
    for path in sorted(p for p in folder.iterdir() if p.is_file()):
        try:
            files.append(reader(path, grid))
        except Exception:  # a damaged file must not stop the event
            unreadable.append(path.name)
    return sorted(files, key=lambda f: f.start), unreadable


def _interval(files: list[FlashFile], start, end, grid: Grid) -> tuple[np.ndarray, float, np.datetime64]:
    """Flash counts for files wholly inside (start, end], and how much of it they cover."""
    counts = np.zeros(grid.shape, dtype="float32")
    covered = np.timedelta64(0, "ns")
    newest = np.datetime64("NaT")
    for f in files:
        if f.start < start or f.end > end:
            continue
        np.add.at(counts, (f.rows, f.cols), 1)
        covered += f.end - f.start
        newest = f.end if np.isnat(newest) else max(newest, f.end)
    return counts, float(covered / (end - start)), newest


def grid_cycle(
    files: list[FlashFile], cycle_time: np.datetime64, interval_min: int, cfg: LightningConfig, grid: Grid
) -> GriddedLightning:
    step = np.timedelta64(interval_min, "m")
    counts, coverage, newest = _interval(files, cycle_time - step, cycle_time, grid)
    before, before_coverage, _ = _interval(files, cycle_time - 2 * step, cycle_time - step, grid)

    nan = np.full(grid.shape, np.nan, dtype="float32")
    observed = coverage >= cfg.min_coverage
    both = observed and before_coverage >= cfg.min_coverage

    def flag(ok: bool) -> np.ndarray:
        return np.full(grid.shape, QUALITY_VALID if ok else QUALITY_NO_DATA, dtype="uint8")

    return GriddedLightning(
        density=counts if observed else nan,
        change=(counts - before) if both else nan,
        quality=flag(observed),
        change_quality=flag(both),
        obs_time=newest if observed else np.datetime64("NaT"),
        flash_count=int(counts.sum()) if observed else 0,
    )
