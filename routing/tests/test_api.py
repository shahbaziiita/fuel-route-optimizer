from unittest import mock

import numpy as np
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings

from routing.services.geocoding import lookup_city
from routing.services.geo import cumulative_miles
from routing.services.station_index import reset_station_cache

WAYPOINTS = [
    ("New York", "NY"), ("Philadelphia", "PA"), ("Harrisburg", "PA"), ("Pittsburgh", "PA"),
    ("Columbus", "OH"), ("Indianapolis", "IN"), ("St. Louis", "MO"), ("Kansas City", "MO"),
    ("Tulsa", "OK"), ("Oklahoma City", "OK"), ("Amarillo", "TX"), ("Albuquerque", "NM"),
    ("Flagstaff", "AZ"), ("Barstow", "CA"), ("Los Angeles", "CA"),
]


def synthetic_osrm_route():
    """Dense polyline through real city centres, shaped like an OSRM reply."""
    pts = []
    for name, st in WAYPOINTS:
        c = lookup_city(name, st)
        pts.append((c.longitude, c.latitude))
    dense = []
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        n = max(int(max(abs(x2 - x1), abs(y2 - y1)) / 0.005), 2)
        for t in np.linspace(0, 1, n, endpoint=False):
            dense.append((x1 + (x2 - x1) * t, y1 + (y2 - y1) * t))
    dense.append(pts[-1])
    coords = np.array(dense)
    miles = cumulative_miles(coords)[-1]
    return {
        "code": "Ok",
        "routes": [{
            "distance": miles * 1609.344,
            "duration": miles / 60 * 3600,
            "geometry": {"type": "LineString", "coordinates": coords.tolist()},
        }],
    }


def fake_response(payload, status=200):
    r = mock.Mock()
    r.status_code = status
    r.json.return_value = payload
    return r


@override_settings(FUEL_PLANNER={
    **__import__("django.conf").conf.settings.FUEL_PLANNER,
    "GEOCODER_FALLBACK_ENABLED": False,
})
class RouteApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("load_fuel_data", stdout=open("/dev/null", "w"))

    def setUp(self):
        cache.clear()
        reset_station_cache()
        self.osrm = synthetic_osrm_route()

    def _get(self, **params):
        with mock.patch("routing.services.routing_client.requests.get",
                        return_value=fake_response(self.osrm)) as m:
            resp = self.client.get("/api/route/", params)
        return resp, m

    def test_cross_country_plan(self):
        resp, m = self._get(start="New York, NY", finish="Los Angeles, CA")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(m.call_count, 1)  # exactly one routing API call
        data = resp.json()

        dist = data["route"]["distance_miles"]
        self.assertGreater(dist, 2000)
        stops = data["fuel_stops"]
        self.assertGreaterEqual(len(stops), int(dist // 500))

        # every leg (origin -> stops -> destination) fits in the tank
        marks = [0] + [s["mile_marker"] for s in stops] + [dist]
        self.assertTrue(all(b - a <= 500.5 for a, b in zip(marks, marks[1:])), marks)

        # 10 mpg: total gallons == distance / 10, cost == sum of parts
        s = data["summary"]
        self.assertAlmostEqual(s["total_gallons"], dist / 10, delta=0.1)
        parts = sum(x["cost"] for x in stops) + s["fuel_to_reach_first_stop"]["cost"]
        self.assertAlmostEqual(s["total_fuel_cost_usd"], parts, delta=0.05)
        self.assertTrue(all(x["distance_from_route_miles"] <= 10 for x in stops))

        kinds = [f["properties"]["kind"] for f in data["map"]["features"]]
        self.assertEqual(kinds.count("route"), 1)
        self.assertEqual(kinds.count("fuel_stop"), len(stops))
        self.assertIn("/api/route/map/", data["map_url"])
        self.assertEqual(data["meta"]["routing_api_calls"], 1)

    def test_plan_beats_naive_average_price(self):
        resp, _ = self._get(start="New York, NY", finish="Los Angeles, CA")
        data = resp.json()
        avg_corridor_price = 3.5  # dataset mean is ~$3.50
        self.assertLess(data["summary"]["average_price_per_gallon"], avg_corridor_price)

    def test_second_request_is_cached_and_map_makes_no_calls(self):
        self._get(start="New York, NY", finish="Los Angeles, CA")
        resp, m = self._get(start="new york, ny", finish="Los Angeles, California")
        self.assertEqual(m.call_count, 0)
        self.assertTrue(resp.json()["meta"]["cached"])
        with mock.patch("routing.services.routing_client.requests.get") as m2:
            page = self.client.get("/api/route/map/", {"start": "New York, NY", "finish": "Los Angeles, CA"})
        self.assertEqual(page.status_code, 200)
        self.assertEqual(m2.call_count, 0)
        self.assertContains(page, "leaflet")

    def test_post_json_and_full_tank(self):
        with mock.patch("routing.services.routing_client.requests.get",
                        return_value=fake_response(self.osrm)):
            resp = self.client.post(
                "/api/route/",
                {"start": "40.7128,-74.0060", "finish": "Los Angeles, CA", "start_tank": "full"},
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["summary"]["start_tank"], "full")
        self.assertIsNone(resp.json()["summary"]["fuel_to_reach_first_stop"])

    def test_zero_penalty_is_cheapest(self):
        a = self._get(start="New York, NY", finish="Los Angeles, CA", stop_penalty=0)[0].json()
        b = self._get(start="New York, NY", finish="Los Angeles, CA")[0].json()
        self.assertLessEqual(a["summary"]["total_fuel_cost_usd"], b["summary"]["total_fuel_cost_usd"])
        self.assertGreaterEqual(a["summary"]["number_of_stops"], b["summary"]["number_of_stops"])

    def test_validation_errors(self):
        self.assertEqual(self._get(start="New York, NY")[0].status_code, 400)
        r, m = self._get(start="Atlantis, ZZ", finish="Los Angeles, CA")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(m.call_count, 0)
        r, _ = self._get(start="51.5074,-0.1278", finish="Los Angeles, CA")  # London
        self.assertEqual(r.status_code, 400)
        r, _ = self._get(start="New York, NY", finish="Los Angeles, CA", start_tank="half")
        self.assertEqual(r.status_code, 400)

    def test_routing_failure_is_502(self):
        with mock.patch("routing.services.routing_client.requests.get",
                        return_value=fake_response({"code": "NoRoute", "message": "Impossible route"}, 400)):
            resp = self.client.get("/api/route/", {"start": "New York, NY", "finish": "Los Angeles, CA"})
        self.assertEqual(resp.status_code, 502)
        self.assertEqual(resp.json()["error"], "routing_failed")
