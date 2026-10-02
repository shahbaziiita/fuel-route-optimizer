"""In-memory, vectorised index of fuel stations.

All ~6.7k US stations are loaded once per process into numpy arrays
(~a few hundred KB). Matching stations to a route is then pure numpy:

  1. bounding-box filter around the route
  2. coarse pass against route samples every 20 mi (cheap reject)
  3. fine pass against route samples every 1 mi -> distance off-route and
     "mile marker" (distance along the route of the nearest route point)
"""

import threading
from dataclasses import dataclass

import numpy as np

from ..models import FuelStation
from .geo import MILES_PER_DEG_LAT, sample_every

_lock = threading.Lock()
_arrays = None

COARSE_STEP_MILES = 20.0
FINE_STEP_MILES = 1.0
_CHUNK = 512


@dataclass
class StationArrays:
    ids: np.ndarray
    lat: np.ndarray
    lng: np.ndarray
    price: np.ndarray
    info: dict  # id -> dict of display fields


@dataclass
class StationOnRoute:
    station_id: int
    mile: float        # distance along the route
    off_route: float   # straight-line miles from the route line
    price: float


def get_station_arrays() -> StationArrays:
    global _arrays
    if _arrays is None:
        with _lock:
            if _arrays is None:
                rows = list(
                    FuelStation.objects.values_list(
                        "opis_id", "name", "address", "city", "state",
                        "retail_price", "latitude", "longitude",
                    )
                )
                info = {
                    r[0]: {"opis_id": r[0], "name": r[1], "address": r[2],
                           "city": r[3], "state": r[4]}
                    for r in rows
                }
                _arrays = StationArrays(
                    ids=np.array([r[0] for r in rows], dtype=np.int64),
                    lat=np.array([r[6] for r in rows], dtype=float),
                    lng=np.array([r[7] for r in rows], dtype=float),
                    price=np.array([r[5] for r in rows], dtype=float),
                    info=info,
                )
    return _arrays


def reset_station_cache():
    global _arrays
    with _lock:
        _arrays = None


def _min_dist(s_lat, s_lng, r_lat, r_lng, r_cos):
    """Equirectangular distance (miles) from each station to its nearest
    route sample. Accurate to well under 1% at corridor scales."""
    best_d = np.empty(len(s_lat))
    best_i = np.empty(len(s_lat), dtype=int)
    for start in range(0, len(s_lat), _CHUNK):
        sl = slice(start, start + _CHUNK)
        dy = (s_lat[sl, None] - r_lat[None, :]) * MILES_PER_DEG_LAT
        dx = (s_lng[sl, None] - r_lng[None, :]) * MILES_PER_DEG_LAT * r_cos[None, :]
        d2 = dx * dx + dy * dy
        idx = d2.argmin(axis=1)
        best_i[sl] = idx
        best_d[sl] = np.sqrt(d2[np.arange(len(idx)), idx])
    return best_d, best_i


def stations_along_route(coords: np.ndarray, cum: np.ndarray, corridor_miles: float):
    st = get_station_arrays()
    if len(st.ids) == 0 or len(coords) == 0:
        return []

    lng, lat = coords[:, 0], coords[:, 1]
    margin_lat = corridor_miles / MILES_PER_DEG_LAT
    margin_lng = corridor_miles / (MILES_PER_DEG_LAT * max(np.cos(np.radians(np.abs(lat).max())), 0.1))
    in_box = (
        (st.lat >= lat.min() - margin_lat) & (st.lat <= lat.max() + margin_lat)
        & (st.lng >= lng.min() - margin_lng) & (st.lng <= lng.max() + margin_lng)
    )
    cand = np.nonzero(in_box)[0]
    if len(cand) == 0:
        return []

    # Coarse pass: every route point is within COARSE_STEP/2 route-miles of a
    # coarse sample, so anything farther than corridor + step/2 can't qualify.
    ci = sample_every(cum, COARSE_STEP_MILES)
    d, _ = _min_dist(st.lat[cand], st.lng[cand], lat[ci], lng[ci], np.cos(np.radians(lat[ci])))
    cand = cand[d <= corridor_miles + COARSE_STEP_MILES / 2 + 1]
    if len(cand) == 0:
        return []

    # Fine pass.
    fi = sample_every(cum, FINE_STEP_MILES)
    d, nearest = _min_dist(st.lat[cand], st.lng[cand], lat[fi], lng[fi], np.cos(np.radians(lat[fi])))
    keep = d <= corridor_miles
    miles = cum[fi][nearest]

    return [
        StationOnRoute(int(st.ids[c]), float(m), float(off), float(st.price[c]))
        for c, m, off in zip(cand[keep], miles[keep], d[keep])
    ]
