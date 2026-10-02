from django.core.management import call_command
from django.test import TestCase, override_settings
from django.conf import settings

from routing.services.geocoding import LocationError, city_key, geocode, split_city_state


class ParsingTests(TestCase):
    def test_split_city_state(self):
        self.assertEqual(split_city_state("Dallas, TX"), ("Dallas", "TX"))
        self.assertEqual(split_city_state("New York, New York, USA"), ("New York", "NY"))
        self.assertEqual(split_city_state("Salt Lake City UT"), ("Salt Lake City", "UT"))
        self.assertEqual(split_city_state("Kansas City North Carolina"), ("Kansas City", "NC"))
        self.assertIsNone(split_city_state("Toronto"))

    def test_city_key(self):
        self.assertEqual(city_key("St. Louis"), city_key("Saint Louis"))
        self.assertEqual(city_key("Brookpark"), city_key("Brook Park"))


@override_settings(FUEL_PLANNER={**settings.FUEL_PLANNER, "GEOCODER_FALLBACK_ENABLED": False})
class GeocodeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("load_fuel_data", stdout=open("/dev/null", "w"))

    def test_gazetteer(self):
        loc, calls = geocode("Saint Louis, Missouri")
        self.assertEqual(calls, 0)
        self.assertAlmostEqual(loc.lat, 38.63, delta=0.3)

    def test_coordinates(self):
        loc, calls = geocode(" 34.05, -118.24 ")
        self.assertEqual((loc.source, calls), ("coordinates", 0))

    def test_outside_usa(self):
        with self.assertRaises(LocationError):
            geocode("48.8566,2.3522")
        with self.assertRaises(LocationError):
            geocode("Toronto, ON")
