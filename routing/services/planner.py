"""Orchestrates one trip plan: geocode -> route (1 API call) -> stations -> optimise."""

import time

from django.conf import settings
from django.core.cache import cache

from .geo import cumulative_miles, simplify_for_output
from .geocoding import geocode
from .optimizer import Candidate, optimize
from .routing_client import fetch_route
from .station_index import get_station_arrays, stations_along_route

PLAN_CACHE_VERSION = 1


def _r(x, n=2):
    return round(float(x), n)


def plan_trip(start_query, finish_query, start_tank="empty", corridor_miles=None, stop_penalty=None):
    t0 = time.perf_counter()
    cfg = settings.FUEL_PLANNER
    corridor = float(corridor_miles or cfg["CORRIDOR_MILES"])
    start_full = start_tank == "full"
    penalty = float(cfg["STOP_PENALTY_USD"] if stop_penalty is None else stop_penalty)

    start, calls_a = geocode(start_query)
    finish, calls_b = geocode(finish_query)
    external_calls = calls_a + calls_b

    plan_key = "plan:v{}:{:.5f},{:.5f}:{:.5f},{:.5f}:{}:{}".format(
        PLAN_CACHE_VERSION, start.lat, start.lng, finish.lat, finish.lng, start_tank, corridor
    ) + f":{penalty}"
    cached = cache.get(plan_key)
    if cached is not None:
        result = dict(cached)
        result["meta"] = {
            **cached["meta"],
            "cached": True,
            "routing_api_calls": 0,
            "external_api_calls": external_calls,
            "elapsed_ms": _r((time.perf_counter() - t0) * 1000, 1),
        }
        return result

    route, route_calls = fetch_route((start.lat, start.lng), (finish.lat, finish.lng))
    external_calls += route_calls
    cum = cumulative_miles(route.coords)
    # Scale our polyline length to OSRM's official road distance.
    total_miles = route.distance_miles
    if cum[-1] > 0:
        cum = cum * (total_miles / cum[-1])

    on_route = stations_along_route(route.coords, cum, corridor)
    candidates = [Candidate(s.station_id, s.mile, s.price, s.off_route) for s in on_route]
    plan = optimize(
        candidates,
        total_miles=total_miles,
        max_range=cfg["MAX_RANGE_MILES"],
        mpg=cfg["MPG"],
        start_full=start_full,
        origin_radius=cfg["ORIGIN_RADIUS_MILES"],
        stop_penalty=penalty,
    )

    st = get_station_arrays()
    idx_of = {int(sid): i for i, sid in enumerate(st.ids)}
    stops = []
    for n, stop in enumerate(plan.stops, 1):
        c = stop.candidate
        i = idx_of[c.key]
        stops.append({
            "stop_number": n,
            **st.info[c.key],
            "lat": _r(st.lat[i], 6),
            "lng": _r(st.lng[i], 6),
            "price_per_gallon": _r(c.price, 3),
            "mile_marker": _r(c.mile, 1),
            "distance_from_route_miles": _r(c.off_route, 1),
            "fuel_on_arrival_gallons": _r(stop.fuel_on_arrival_gallons),
            "gallons_purchased": _r(stop.gallons),
            "cost": _r(stop.cost),
        })

    total_gallons = plan.total_gallons_purchased + plan.approach_gallons
    line = simplify_for_output(route.coords, cum)
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"kind": "route", "distance_miles": _r(total_miles, 1)},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[_r(x, 5), _r(y, 5)] for x, y in line],
                },
            },
            _point("start", start.label, start.lng, start.lat),
            _point("finish", finish.label, finish.lng, finish.lat),
        ] + [
            _point("fuel_stop", f"#{s['stop_number']} {s['name']} (${s['price_per_gallon']}/gal)",
                   s["lng"], s["lat"], stop_number=s["stop_number"])
            for s in stops
        ],
    }

    result = {
        "start": start.as_dict(),
        "finish": finish.as_dict(),
        "route": {
            "distance_miles": _r(total_miles, 1),
            "duration_hours": _r(route.duration_seconds / 3600, 2),
        },
        "fuel_stops": stops,
        "summary": {
            "total_fuel_cost_usd": _r(plan.total_cost),
            "total_gallons": _r(total_gallons),
            "number_of_stops": len(stops),
            "average_price_per_gallon": _r(plan.total_cost / total_gallons, 3) if total_gallons else None,
            "vehicle_mpg": cfg["MPG"],
            "vehicle_max_range_miles": cfg["MAX_RANGE_MILES"],
            "start_tank": "full" if start_full else "empty",
            "fuel_to_reach_first_stop": None if start_full else {
                "miles": _r(plan.approach_miles, 1),
                "gallons": _r(plan.approach_gallons),
                "cost": _r(plan.approach_cost),
                "note": "Fuel burned from the origin to the first stop, billed at that stop's price.",
            },
        },
        "map": geojson,
        "meta": {
            "cached": False,
            "routing_provider": "OSRM",
            "routing_api_calls": route_calls,
            "external_api_calls": external_calls,
            "stations_within_corridor": len(candidates),
            "corridor_miles": corridor,
            "stop_penalty_usd": penalty,
            "elapsed_ms": _r((time.perf_counter() - t0) * 1000, 1),
        },
    }
    cache.set(plan_key, result, cfg["CACHE_TTL_SECONDS"])
    return result


def _point(kind, label, lng, lat, **extra):
    return {
        "type": "Feature",
        "properties": {"kind": kind, "label": label, **extra},
        "geometry": {"type": "Point", "coordinates": [_r(lng, 6), _r(lat, 6)]},
    }
