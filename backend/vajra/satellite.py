"""Satellite infrared imagery → brightness temperature on the common grid.

Each grid cell takes the satellite pixel nearest to it. Pixels the product
flags as unusable are REJECTED; cells outside the image are NO_DATA.

Known limitation: the satellite views the region at an angle, so high cloud
tops appear displaced from their true position (parallax). This is not corrected.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import netCDF4
import numpy as np
from pyproj import Proj

from vajra.config import SatelliteConfig
from vajra.cube import QUALITY_NO_DATA, QUALITY_REJECTED, QUALITY_VALID
from vajra.grid import Grid

# Physically plausible brightness temperatures; anything outside is rejected.
MIN_PLAUSIBLE_K = 150.0
MAX_PLAUSIBLE_K = 340.0


@dataclass
class GriddedImage:
    start_time: np.datetime64  # the scan period
    end_time: np.datetime64
    values: np.ndarray  # (lat, lon) float32 kelvin, NaN where not VALID
    quality: np.ndarray  # (lat, lon) uint8


def _read_abi_l2_cmip(path: Path, grid: Grid) -> GriddedImage:
    """GOES ABI Level 2 cloud and moisture imagery, on the satellite's fixed grid."""
    with netCDF4.Dataset(path) as ds:
        projection = ds["goes_imager_projection"]
        height = float(projection.perspective_point_height)
        geos = Proj(
            proj="geos",
            h=height,
            lon_0=float(projection.longitude_of_projection_origin),
            sweep=projection.sweep_angle_axis,
            a=float(projection.semi_major_axis),
            b=float(projection.semi_minor_axis),
        )
        # Image coordinates are scan angles in radians; projected metres / height gives the same.
        x = np.asarray(ds["x"][:], dtype="float64")
        y = np.asarray(ds["y"][:], dtype="float64")
        lon2d, lat2d = np.meshgrid(grid.lons, grid.lats)
        px, py = geos(lon2d, lat2d)
        col = np.rint((px / height - x[0]) / (x[1] - x[0])).astype("int64")
        row = np.rint((py / height - y[0]) / (y[1] - y[0])).astype("int64")
        inside = np.isfinite(px) & (row >= 0) & (row < len(y)) & (col >= 0) & (col < len(x))

        values = np.full(grid.shape, np.nan, dtype="float32")
        quality = np.full(grid.shape, QUALITY_NO_DATA, dtype="uint8")
        if inside.any():
            # Read only the part of the image that covers the grid.
            r0, r1 = int(row[inside].min()), int(row[inside].max()) + 1
            c0, c1 = int(col[inside].min()), int(col[inside].max()) + 1
            image = np.ma.filled(ds["CMI"][r0:r1, c0:c1].astype("float32"), np.nan)
            flags = np.asarray(ds["DQF"][r0:r1, c0:c1])
            pixel = image[row[inside] - r0, col[inside] - c0]
            flag = flags[row[inside] - r0, col[inside] - c0]
            # Flags 0 and 1 are "good" and "conditionally usable".
            good = np.isin(flag, (0, 1)) & (pixel >= MIN_PLAUSIBLE_K) & (pixel <= MAX_PLAUSIBLE_K)
            values[inside] = np.where(good, pixel, np.nan)
            quality[inside] = np.where(good, QUALITY_VALID, QUALITY_REJECTED)

        return GriddedImage(
            start_time=np.datetime64(ds.time_coverage_start.rstrip("Z"), "ns"),
            end_time=np.datetime64(ds.time_coverage_end.rstrip("Z"), "ns"),
            values=values,
            quality=quality,
        )


# format name → file reader. Add a line here for a new satellite product.
READERS: dict[str, Callable[[Path, Grid], GriddedImage]] = {
    "abi_l2_cmip": _read_abi_l2_cmip,
}


def read_files(folder: Path, cfg: SatelliteConfig, grid: Grid) -> tuple[list[GriddedImage], list[str]]:
    """Read every satellite file in a folder. Returns the images and the names that failed."""
    if cfg.format not in READERS:
        raise ValueError(f"satellite format '{cfg.format}' has no reader; known: {sorted(READERS)}")
    reader = READERS[cfg.format]
    images: list[GriddedImage] = []
    unreadable: list[str] = []
    for path in sorted(p for p in folder.iterdir() if p.is_file()):
        try:
            images.append(reader(path, grid))
        except Exception:  # a damaged file must not stop the event
            unreadable.append(path.name)
    return sorted(images, key=lambda image: image.start_time), unreadable
