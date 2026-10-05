"""Renders gridded fields to PNG overlays for the map."""

import io

import numpy as np
from PIL import Image
from scipy import ndimage

from vajra.cube import QUALITY_NO_DATA, QUALITY_REJECTED
from vajra.grid import Grid

# Reflectivity colour table: (lower bound in dBZ, RGB). Below the first bound is transparent.
REFLECTIVITY_COLORS: list[tuple[float, tuple[int, int, int]]] = [
    (5, (4, 233, 231)),
    (10, (1, 159, 244)),
    (15, (3, 0, 244)),
    (20, (2, 253, 2)),
    (25, (1, 197, 1)),
    (30, (0, 142, 0)),
    (35, (253, 248, 2)),
    (40, (229, 188, 0)),
    (45, (253, 149, 0)),
    (50, (253, 0, 0)),
    (55, (212, 0, 0)),
    (60, (188, 0, 0)),
    (65, (248, 0, 253)),
    (70, (152, 84, 198)),
]
NO_DATA_RGBA = (110, 116, 128, 90)
REJECTED_RGBA = (170, 110, 190, 110)


def reflectivity_legend() -> list[dict[str, str]]:
    entries = [
        {"label": f"{int(bound)}", "color": f"rgb({r}, {g}, {b})"}
        for bound, (r, g, b) in REFLECTIVITY_COLORS
    ]
    entries.append({"label": "No data", "color": "rgba({}, {}, {}, {:.2f})".format(*NO_DATA_RGBA[:3], NO_DATA_RGBA[3] / 255)})
    entries.append({"label": "QC rejected", "color": "rgba({}, {}, {}, {:.2f})".format(*REJECTED_RGBA[:3], REJECTED_RGBA[3] / 255)})
    return entries


# Lightning colour table: (lower bound in flashes within the sensor footprint, RGBA).
# Violet-to-white, chosen to stand apart from the reflectivity colours underneath.
LIGHTNING_COLORS: list[tuple[float, tuple[int, int, int, int]]] = [
    (1, (196, 140, 255, 235)),
    (5, (147, 51, 234, 240)),
    (15, (107, 33, 168, 245)),
    (30, (236, 72, 153, 250)),
    (60, (255, 255, 255, 255)),
]


# Probability colour table: (lower bound as a fraction, RGBA). Below the first bound is transparent.
PROBABILITY_COLORS: list[tuple[float, tuple[int, int, int, int]]] = [
    (0.1, (254, 240, 138, 170)),
    (0.3, (253, 186, 116, 205)),
    (0.5, (249, 115, 22, 230)),
    (0.7, (220, 38, 38, 245)),
    (0.9, (190, 24, 93, 255)),
]


def probability_legend() -> list[dict[str, str]]:
    entries = [
        {"label": f"{round(bound * 100)}", "color": "rgba({}, {}, {}, {:.2f})".format(r, g, b, a / 255)}
        for bound, (r, g, b, a) in PROBABILITY_COLORS
    ]
    entries.append({"label": "No data", "color": "rgba({}, {}, {}, {:.2f})".format(*NO_DATA_RGBA[:3], NO_DATA_RGBA[3] / 255)})
    return entries


def probability_png(probability: np.ndarray, grid: Grid) -> bytes:
    """A probability field; cells with no forecast basis (NaN) are shown as no data."""
    height, width = probability.shape
    rgba = np.zeros((height, width, 4), dtype="uint8")
    bounds = np.array([b for b, _ in PROBABILITY_COLORS])
    colors = np.array([c for _, c in PROBABILITY_COLORS], dtype="uint8")
    known = np.isfinite(probability)
    level = np.searchsorted(bounds, np.where(known, probability, 0.0), side="right") - 1
    shown = known & (level >= 0)
    rgba[shown] = colors[level[shown]]
    rgba[~known] = NO_DATA_RGBA

    image = Image.fromarray(rgba[_mercator_rows(grid, 2 * height)], mode="RGBA")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def lightning_legend() -> list[dict[str, str]]:
    return [
        {"label": f"{int(bound)}", "color": "rgba({}, {}, {}, {:.2f})".format(r, g, b, a / 255)}
        for bound, (r, g, b, a) in LIGHTNING_COLORS
    ]


def lightning_png(density: np.ndarray, quality: np.ndarray, grid: Grid, footprint_cells: int) -> bytes:
    """Flashes within the sensor footprint of each cell; cells with none stay transparent."""
    height, width = density.shape
    rgba = np.zeros((height, width, 4), dtype="uint8")
    counts = np.nan_to_num(density, nan=0.0)
    nearby = ndimage.uniform_filter(counts, size=footprint_cells, mode="constant") * footprint_cells**2
    nearby = np.rint(nearby)
    bounds = np.array([b for b, _ in LIGHTNING_COLORS])
    colors = np.array([c for _, c in LIGHTNING_COLORS], dtype="uint8")
    level = np.searchsorted(bounds, nearby, side="right") - 1
    shown = level >= 0
    rgba[shown] = colors[level[shown]]
    rgba[quality == QUALITY_NO_DATA] = NO_DATA_RGBA

    image = Image.fromarray(rgba[_mercator_rows(grid, 2 * height)], mode="RGBA")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _mercator_y(lat_deg: np.ndarray | float) -> np.ndarray:
    return np.log(np.tan(np.pi / 4 + np.deg2rad(lat_deg) / 2))


def _mercator_rows(grid: Grid, rows_out: int) -> np.ndarray:
    """Source row for each output row, north first, evenly spaced in Web Mercator.

    The map stretches an image linearly in Mercator, but the grid is linear in
    latitude; resampling the rows here keeps every cell at its true position.
    """
    _, south, _, north = grid.extent
    y_south, y_north = _mercator_y(south), _mercator_y(north)
    y = y_north - (np.arange(rows_out) + 0.5) / rows_out * (y_north - y_south)
    lat = np.rad2deg(2 * np.arctan(np.exp(y)) - np.pi / 2)
    return np.clip(np.floor((lat - south) / grid.dlat).astype(int), 0, grid.shape[0] - 1)


def reflectivity_png(values: np.ndarray, quality: np.ndarray, grid: Grid) -> bytes:
    height, width = values.shape
    rgba = np.zeros((height, width, 4), dtype="uint8")
    bounds = np.array([b for b, _ in REFLECTIVITY_COLORS])
    colors = np.array([c for _, c in REFLECTIVITY_COLORS], dtype="uint8")
    with np.errstate(invalid="ignore"):
        level = np.searchsorted(bounds, values, side="right") - 1
        shown = np.isfinite(values) & (level >= 0)
    rgba[shown, :3] = colors[level[shown]]
    rgba[shown, 3] = 255
    rgba[quality == QUALITY_NO_DATA] = NO_DATA_RGBA
    rgba[quality == QUALITY_REJECTED] = REJECTED_RGBA

    image = Image.fromarray(rgba[_mercator_rows(grid, 2 * height)], mode="RGBA")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
