"""Small, dependency-free (numpy only) geometry helpers."""

import numpy as np

EARTH_RADIUS_MILES = 3958.7613
MILES_PER_DEG_LAT = 69.0


def haversine_miles(lat1, lng1, lat2, lng2):
    """Great-circle distance in miles. Works on scalars or numpy arrays."""
    lat1, lng1, lat2, lng2 = map(np.radians, (lat1, lng1, lat2, lng2))
    a = (
        np.sin((lat2 - lat1) / 2) ** 2
        + np.cos(lat1) * np.cos(lat2) * np.sin((lng2 - lng1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def cumulative_miles(coords: np.ndarray) -> np.ndarray:
    """Cumulative distance along a polyline of (lng, lat) points."""
    if len(coords) < 2:
        return np.zeros(len(coords))
    seg = haversine_miles(coords[:-1, 1], coords[:-1, 0], coords[1:, 1], coords[1:, 0])
    return np.concatenate(([0.0], np.cumsum(seg)))


def sample_every(cum: np.ndarray, step_miles: float) -> np.ndarray:
    """Indices of polyline vertices spaced roughly `step_miles` apart (always
    includes the first and last vertex)."""
    if len(cum) == 0:
        return np.array([], dtype=int)
    targets = np.arange(0.0, cum[-1], step_miles)
    idx = np.searchsorted(cum, targets)
    idx = np.unique(np.concatenate((idx, [len(cum) - 1])))
    return idx[idx < len(cum)]


def simplify_for_output(coords: np.ndarray, cum: np.ndarray, max_points: int = 2000):
    """Down-sample a (possibly 50k-point) route so the JSON stays small."""
    if len(coords) <= max_points:
        return coords
    step = max(cum[-1] / max_points, 0.05)
    return coords[sample_every(cum, step)]
