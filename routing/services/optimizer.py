"""Minimum-cost refuelling along a fixed route.

Pure Python + numpy, no Django -- easy to unit-test.

Model
-----
Every station within the corridor is projected onto the route and gets a
"mile marker". The vehicle drives the route in order; at each station it
may stop and buy any amount of fuel (up to the tank's range). We minimise

    sum(fuel bought * price)  +  stop_penalty * number_of_stops

with a dynamic programme over (station, fuel-in-tank) where fuel is
measured in whole miles of range (0..500, i.e. 0.1-gallon resolution at
10 mpg). For each station the "buy" step is a vectorised prefix-minimum, so
the whole solve is O(stations x range) numpy work -- a few milliseconds for
a coast-to-coast trip with hundreds of candidate stations.

With stop_penalty = 0 this is the exact cheapest plan (verified against a
brute-force solver in the tests). A small penalty (default $10, configurable
per request) removes pointless "buy 1 gallon to save 2 cents" stops while
staying within cents of the minimum.

Starting-tank modes
-------------------
empty (default)  The vehicle starts (nearly) empty, so its first fill-up must be
                 at a station within `origin_radius` route-miles of the origin.
                 Fuel burned getting there is billed at that station's price, so
                 the total covers every mile of the trip.
full             The vehicle starts with a full tank (already paid for); only
                 fuel bought on the way is billed.
"""

import math
from dataclasses import dataclass, field

import numpy as np

EPS = 1e-9


class NoFeasiblePlan(RuntimeError):
    def __init__(self, message, stuck_at_mile=None):
        super().__init__(message)
        self.stuck_at_mile = stuck_at_mile


@dataclass(frozen=True)
class Candidate:
    key: int          # caller's identifier (station id)
    mile: float       # distance along route
    price: float      # $/gallon
    off_route: float = 0.0


@dataclass
class Stop:
    candidate: Candidate
    gallons: float
    cost: float
    fuel_on_arrival_gallons: float


@dataclass
class FuelPlan:
    stops: list = field(default_factory=list)
    total_gallons_purchased: float = 0.0
    total_cost: float = 0.0
    approach_miles: float = 0.0       # empty mode: miles driven before 1st stop
    approach_gallons: float = 0.0
    approach_cost: float = 0.0


def _dedupe(candidates, total_miles):
    """Sort by mile; stations at the same mile marker (e.g. same town) are
    interchangeable, so keep only the cheapest."""
    best = {}
    for c in candidates:
        if c.mile > total_miles + EPS:
            continue
        k = round(c.mile, 2)
        if k not in best or c.price < best[k].price:
            best[k] = c
    return sorted(best.values(), key=lambda c: c.mile)


def optimize(candidates, total_miles, max_range, mpg, start_full=False,
             origin_radius=30.0, stop_penalty=0.0):
    if total_miles <= 0:
        return FuelPlan()

    stations = _dedupe(candidates, total_miles)
    cap = int(math.floor(max_range + EPS))
    T = int(round(total_miles))
    pos = [min(int(round(c.mile)), T) for c in stations]
    levels = np.arange(cap + 1)
    INF = np.inf

    if not start_full:
        if not stations:
            raise NoFeasiblePlan("No fuel stations found along this route.", stuck_at_mile=0.0)
        # first stop may be any station near the origin (or the nearest one)
        start_ok = [c.mile <= origin_radius + EPS for c in stations]
        if not any(start_ok):
            start_ok[0] = True
        if stations[0].mile > max_range:
            raise NoFeasiblePlan("No fuel station reachable from the origin.", stuck_at_mile=0.0)

    dp = np.full(cap + 1, INF)          # cost-so-far, indexed by fuel level (miles)
    if start_full:
        dp[cap] = 0.0
    here = 0
    frm, injected, arrival_levels = [], [], []
    last_ok_mile = 0.0

    for i, c in enumerate(stations):
        dp = _drive(dp, pos[i] - here)
        here = pos[i]
        inj = False
        if not start_full and start_ok[i]:
            approach = c.mile / mpg * c.price
            if approach < dp[0] - EPS:
                dp[0] = approach
                inj = True
        if not np.isfinite(dp).any():
            if start_full or any(injected) or inj:
                raise NoFeasiblePlan(
                    f"No fuel station within {max_range:.0f} miles after mile {last_ok_mile:.0f} of the route.",
                    stuck_at_mile=last_ok_mile,
                )
        else:
            last_ok_mile = c.mile

        # Buy step: dep[L] = min(dp[L], min_{f<L} dp[f] + (L-f)*ppm + penalty)
        ppm = c.price / mpg
        g = dp - ppm * levels
        pm = np.minimum.accumulate(g)
        argf = np.maximum.accumulate(np.where(g <= pm + EPS, levels, 0))
        buy = pm + ppm * levels + stop_penalty
        better = buy < dp - EPS
        frm.append(np.where(better, argf, -1))
        injected.append(inj)
        dp = np.where(better, buy, dp)

    dp = _drive(dp, T - here)
    if not np.isfinite(dp).any():
        raise NoFeasiblePlan(
            f"No fuel station within {max_range:.0f} miles of the destination.",
            stuck_at_mile=last_ok_mile,
        )

    # Backtrack.
    level = int(np.argmin(dp))
    nxt = T
    stops_rev = []
    approach_idx = None
    for i in range(len(stations) - 1, -1, -1):
        dep = level + (nxt - pos[i])
        f = int(frm[i][dep])
        arr = f if f >= 0 else dep
        if f >= 0:
            stops_rev.append((i, dep - f, arr))
        level, nxt = arr, pos[i]
        if not start_full and arr == 0 and injected[i]:
            approach_idx = i
            break

    plan = FuelPlan()
    for i, miles_bought, arr in reversed(stops_rev):
        c = stations[i]
        gallons = miles_bought / mpg
        cost = gallons * c.price
        plan.stops.append(Stop(c, gallons, cost, arr / mpg))
        plan.total_gallons_purchased += gallons
        plan.total_cost += cost
    if approach_idx is not None:
        c = stations[approach_idx]
        plan.approach_miles = c.mile
        plan.approach_gallons = c.mile / mpg
        plan.approach_cost = plan.approach_gallons * c.price
        plan.total_cost += plan.approach_cost
    return plan


def _drive(dp, miles):
    """Shift fuel levels down by `miles` (fuel consumed)."""
    if miles <= 0:
        return dp
    out = np.full_like(dp, np.inf)
    if miles < len(dp):
        out[: len(dp) - miles] = dp[miles:]
    return out
