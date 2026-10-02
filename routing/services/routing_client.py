"""Thin client for the free OSRM routing API (one HTTP call per route).

Docs: https://project-osrm.org/docs/v5.24.0/api/#route-service
"""

from dataclasses import dataclass

import numpy as np
import requests
from django.conf import settings
from django.core.cache import cache

METERS_PER_MILE = 1609.344


class RoutingError(RuntimeError):
    """The routing provider failed or found no drivable route."""


@dataclass
class Route:
    coords: np.ndarray        # shape (N, 2) as (lng, lat) -- GeoJSON order
    distance_miles: float
    duration_seconds: float


def _cache_key(start, end):
    return "route:{:.5f},{:.5f}:{:.5f},{:.5f}".format(*start, *end)


def fetch_route(start: tuple[float, float], end: tuple[float, float]) -> tuple[Route, int]:
    """start/end are (lat, lng). Returns (route, external_calls_made)."""
    cfg = settings.FUEL_PLANNER
    key = _cache_key(start, end)
    cached = cache.get(key)
    if cached is not None:
        return cached, 0

    url = "{base}/route/v1/driving/{slng},{slat};{elng},{elat}".format(
        base=cfg["ROUTING_BASE_URL"].rstrip("/"),
        slat=start[0], slng=start[1], elat=end[0], elng=end[1],
    )
    try:
        resp = requests.get(
            url,
            params={"overview": "full", "geometries": "geojson", "steps": "false"},
            headers={"User-Agent": cfg["USER_AGENT"]},
            timeout=cfg["ROUTING_TIMEOUT_SECONDS"],
        )
    except requests.RequestException as exc:
        raise RoutingError(f"Routing service unreachable: {exc}") from exc

    try:
        data = resp.json()
    except ValueError as exc:
        raise RoutingError(f"Routing service returned HTTP {resp.status_code}.") from exc

    if resp.status_code != 200 or data.get("code") != "Ok" or not data.get("routes"):
        raise RoutingError(data.get("message") or f"No route found (code={data.get('code')}).")

    r = data["routes"][0]
    route = Route(
        coords=np.asarray(r["geometry"]["coordinates"], dtype=float),
        distance_miles=r["distance"] / METERS_PER_MILE,
        duration_seconds=float(r["duration"]),
    )
    cache.set(key, route, cfg["CACHE_TTL_SECONDS"])
    return route, 1
