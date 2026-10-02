import itertools
import random

from django.test import SimpleTestCase

from routing.services.optimizer import Candidate, NoFeasiblePlan, optimize


def brute_force_cost(stations, total, cap):
    """Exact DP over integer fuel levels. Vehicle starts empty at stations[0]
    (mile 0). Used to verify the greedy is optimal."""
    INF = float("inf")
    miles = [s[0] for s in stations] + [total]
    dp = [INF] * (cap + 1)
    dp[0] = 0.0
    for i, (m, price) in enumerate(stations):
        d = miles[i + 1] - m
        nxt = [INF] * (cap + 1)
        for f in range(cap + 1):
            if dp[f] == INF:
                continue
            for b in range(0, cap - f + 1):
                if f + b >= d:
                    nf = f + b - d
                    nxt[nf] = min(nxt[nf], dp[f] + b * price)
        dp = nxt
    return dp[0] if dp[0] < INF else None


class OptimizerTests(SimpleTestCase):
    def test_hand_worked_example(self):
        c = [Candidate(1, 0, 3.0), Candidate(2, 400, 2.0), Candidate(3, 800, 4.0)]
        plan = optimize(c, total_miles=1000, max_range=500, mpg=10, origin_radius=0)
        self.assertEqual([s.candidate.key for s in plan.stops], [1, 2, 3])
        self.assertEqual([round(s.gallons, 6) for s in plan.stops], [40, 50, 10])
        self.assertAlmostEqual(plan.total_cost, 40 * 3 + 50 * 2 + 10 * 4)
        self.assertAlmostEqual(plan.total_gallons_purchased, 100)

    def test_buys_just_enough_to_reach_cheaper_station(self):
        c = [Candidate(1, 0, 5.0), Candidate(2, 100, 2.0)]
        plan = optimize(c, total_miles=300, max_range=500, mpg=10, origin_radius=0)
        self.assertEqual([round(s.gallons, 6) for s in plan.stops], [10, 20])

    def test_matches_exact_dp_on_random_instances(self):
        rng = random.Random(42)
        for _ in range(300):
            cap = rng.randint(5, 12)
            total = rng.randint(10, 60)
            n = rng.randint(1, 12)
            miles = sorted({0, *rng.sample(range(1, total), min(n, total - 1))})
            stations = [(m, round(rng.uniform(2.5, 4.5), 2)) for m in miles]
            expected = brute_force_cost(stations, total, cap)
            cands = [Candidate(i, m, p) for i, (m, p) in enumerate(stations)]
            if expected is None:
                with self.assertRaises(NoFeasiblePlan):
                    optimize(cands, total, cap, mpg=1, origin_radius=0)
                continue
            plan = optimize(cands, total, cap, mpg=1, origin_radius=0)
            self.assertAlmostEqual(plan.total_cost, expected, places=6)

    def test_every_leg_within_range(self):
        rng = random.Random(7)
        cands = [Candidate(i, m, rng.uniform(3, 4)) for i, m in enumerate(range(0, 2800, 37))]
        plan = optimize(cands, 2800, 500, 10, origin_radius=30)
        pts = [0] + [s.candidate.mile for s in plan.stops] + [2800]
        self.assertTrue(all(b - a <= 500 + 1e-6 for a, b in zip(pts, pts[1:])))
        self.assertAlmostEqual(plan.total_gallons_purchased + plan.approach_gallons, 280, places=6)

    def test_gap_larger_than_range_is_infeasible(self):
        c = [Candidate(1, 0, 3), Candidate(2, 600, 3)]
        with self.assertRaises(NoFeasiblePlan) as ctx:
            optimize(c, 900, 500, 10)
        self.assertEqual(ctx.exception.stuck_at_mile, 0)

    def test_full_tank_short_trip_needs_no_stops(self):
        plan = optimize([Candidate(1, 100, 3)], 450, 500, 10, start_full=True)
        self.assertEqual(plan.stops, [])
        self.assertEqual(plan.total_cost, 0)

    def test_full_tank_long_trip(self):
        c = [Candidate(1, 300, 4.0), Candidate(2, 450, 3.0)]
        plan = optimize(c, 700, 500, 10, start_full=True)
        # skip the $4 station, top up at the $3 one with exactly what's needed
        self.assertEqual([s.candidate.key for s in plan.stops], [2])
        self.assertAlmostEqual(plan.stops[0].gallons, 20)

    def test_empty_start_picks_best_first_station(self):
        # the 2nd station near the origin is cheaper and should be the first stop
        c = [Candidate(1, 2, 4.5), Candidate(2, 20, 3.0), Candidate(3, 400, 3.5)]
        plan = optimize(c, 600, 500, 10, origin_radius=30)
        self.assertEqual(plan.stops[0].candidate.key, 2)
        self.assertAlmostEqual(plan.approach_miles, 20)

    def test_same_mile_keeps_cheapest(self):
        c = [Candidate(1, 0, 3.9), Candidate(2, 0, 3.1)]
        plan = optimize(c, 100, 500, 10, origin_radius=0)
        self.assertEqual(plan.stops[0].candidate.key, 2)

    def test_stop_penalty_removes_micro_stops(self):
        rng = random.Random(3)
        cands = [Candidate(i, m, round(rng.uniform(3.0, 3.3), 3)) for i, m in enumerate(range(0, 2500, 9))]
        cheap = optimize(cands, 2500, 500, 10, origin_radius=0, stop_penalty=0)
        tidy = optimize(cands, 2500, 500, 10, origin_radius=0, stop_penalty=10)
        self.assertLess(len(tidy.stops), len(cheap.stops))
        self.assertGreaterEqual(tidy.total_cost, cheap.total_cost - 1e-6)
        # still within stop_penalty * extra_stops of the absolute minimum
        self.assertLess(tidy.total_cost - cheap.total_cost, 10 * len(cheap.stops))
        pts = [0] + [s.candidate.mile for s in tidy.stops] + [2500]
        self.assertTrue(all(b - a <= 500 for a, b in zip(pts, pts[1:])))
