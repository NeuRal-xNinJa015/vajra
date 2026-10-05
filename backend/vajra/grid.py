"""The common analysis grid: a regular lat/lon grid derived from the configured region."""

from dataclasses import dataclass
from functools import cached_property

import numpy as np

from vajra.config import Settings

KM_PER_DEG_LAT = 111.32


@dataclass(frozen=True)
class Grid:
    west: float
    south: float
    east: float
    north: float
    resolution_km: float

    @property
    def dlat(self) -> float:
        return self.resolution_km / KM_PER_DEG_LAT

    @property
    def dlon(self) -> float:
        centre_lat = np.deg2rad((self.south + self.north) / 2)
        return self.resolution_km / (KM_PER_DEG_LAT * float(np.cos(centre_lat)))

    @cached_property
    def lats(self) -> np.ndarray:
        """Cell-centre latitudes, south to north."""
        n = int(np.floor((self.north - self.south) / self.dlat))
        return self.south + (np.arange(n) + 0.5) * self.dlat

    @cached_property
    def lons(self) -> np.ndarray:
        """Cell-centre longitudes, west to east."""
        n = int(np.floor((self.east - self.west) / self.dlon))
        return self.west + (np.arange(n) + 0.5) * self.dlon

    @property
    def shape(self) -> tuple[int, int]:
        """(height, width) = (lat, lon)."""
        return len(self.lats), len(self.lons)

    @property
    def extent(self) -> tuple[float, float, float, float]:
        """Outer edges of the grid cells: (west, south, east, north).

        Slightly inside the configured bounds, because only whole cells are kept.
        """
        height, width = self.shape
        return (
            self.west,
            self.south,
            self.west + width * self.dlon,
            self.south + height * self.dlat,
        )


def build_grid(settings: Settings) -> Grid:
    b = settings.region.bounds
    return Grid(b.west, b.south, b.east, b.north, settings.grid.resolution_km)
