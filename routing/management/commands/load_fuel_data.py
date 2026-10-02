"""Load the US city gazetteer and the fuel price CSV into the database.

    python manage.py load_fuel_data

Stations are geocoded offline by matching (city, state) against the
gazetteer, so no geocoding API is ever called for them. Duplicate OPIS IDs
are collapsed to the lowest listed price. Non-US (Canadian) rows are skipped.
"""

import csv
from collections import defaultdict
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from routing.models import City, FuelStation
from routing.services.geocoding import US_STATES, city_key, lookup_city
from routing.services.station_index import reset_station_cache


class Command(BaseCommand):
    help = "Load US cities + fuel prices into the DB (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--fuel-csv", default=str(settings.DATA_DIR / "fuel-prices-for-be-assessment.csv"))
        parser.add_argument("--cities-csv", default=str(settings.DATA_DIR / "us_cities.csv"))
        parser.add_argument("--city-overrides-csv", default=str(settings.DATA_DIR / "city_overrides.csv"),
                            help="Extra towns missing from the gazetteer (manually geocoded).")

    @transaction.atomic
    def handle(self, *args, **opts):
        self._load_cities(Path(opts["cities_csv"]), Path(opts["city_overrides_csv"]))
        self._load_stations(Path(opts["fuel_csv"]))
        reset_station_cache()

    def _load_cities(self, path, overrides_path):
        City.objects.all().delete()
        # average duplicate (name, state) rows into one centroid
        acc = defaultdict(lambda: [0.0, 0.0, 0, ""])
        rows = []
        for p in (path, overrides_path):
            if p.exists():
                with p.open(newline="", encoding="utf-8") as fh:
                    rows.extend(csv.DictReader(fh))
        for row in rows:
            state = row["STATE_CODE"].strip()
            if state not in US_STATES:
                continue
            name = row["CITY"].strip()
            a = acc[(city_key(name), state)]
            a[0] += float(row["LATITUDE"])
            a[1] += float(row["LONGITUDE"])
            a[2] += 1
            a[3] = name
        City.objects.bulk_create(
            [City(name=a[3], state=s, name_key=k, latitude=a[0] / a[2], longitude=a[1] / a[2])
             for (k, s), a in acc.items()],
            batch_size=2000,
        )
        self.stdout.write(f"Cities loaded: {len(acc)}")

    def _load_stations(self, path):
        FuelStation.objects.all().delete()
        best = {}
        skipped_non_us = 0
        with path.open(newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                state = row["State"].strip()
                if state not in US_STATES:
                    skipped_non_us += 1
                    continue
                sid = int(row["OPIS Truckstop ID"])
                price = float(row["Retail Price"])
                if sid not in best or price < best[sid]["price"]:
                    best[sid] = {
                        "name": row["Truckstop Name"].strip(),
                        "address": row["Address"].strip(),
                        "city": row["City"].strip(),
                        "state": state,
                        "rack": int(row["Rack ID"]) if row["Rack ID"].strip() else None,
                        "price": price,
                    }

        city_cache, unmatched, objs = {}, [], []
        for sid, r in best.items():
            ck = (r["city"], r["state"])
            if ck not in city_cache:
                city_cache[ck] = lookup_city(*ck)
            c = city_cache[ck]
            if c is None:
                unmatched.append(f"{r['city']}, {r['state']}")
                continue
            objs.append(FuelStation(
                opis_id=sid, name=r["name"], address=r["address"], city=r["city"],
                state=r["state"], rack_id=r["rack"], retail_price=r["price"],
                latitude=c.latitude, longitude=c.longitude,
            ))
        FuelStation.objects.bulk_create(objs, batch_size=2000)
        self.stdout.write(self.style.SUCCESS(
            f"Stations loaded: {len(objs)} (unique US stations: {len(best)}, "
            f"skipped non-US rows: {skipped_non_us}, not geocoded: {len(unmatched)})"
        ))
        if unmatched:
            self.stdout.write("Not geocoded: " + "; ".join(sorted(set(unmatched))))
