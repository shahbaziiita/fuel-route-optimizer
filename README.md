# Fuel Route Optimizer (Django 6.1)

An API that takes a start and finish location in the USA, returns the driving
route on a map, the most cost-effective places to refuel along it (500-mile
range, 10 mpg), and the total fuel cost.

- **One external API call per request** (OSRM route). Geocoding is done offline.
- **Fast:** ~40–80 ms of server-side compute for a coast-to-coast trip; repeat
  requests are served from cache in a few ms with **zero** external calls.
- **Provably optimal fuel plan:** dynamic programme, verified against a
  brute-force solver in the test suite.

---

## Quick start

Requires Python 3.12+. The provided fuel price file must be at
`data/fuel-prices-for-be-assessment.csv` (copy it there if it isn't already).

```bash
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py load_fuel_data      # loads 29k US cities + 6.6k stations (~3 s)
python manage.py runserver
```

On Windows, use `python` instead of `python3` in the first line.

```bash
curl "http://127.0.0.1:8000/api/route/?start=New%20York,%20NY&finish=Los%20Angeles,%20CA"
```

Open the `map_url` from the response in a browser to see the interactive map.

Run the tests (no network needed — routing responses are mocked):

```bash
python manage.py test routing
```

A Postman collection is included: `postman_collection.json`.

---

## API

### `GET /api/route/` or `POST /api/route/`

| Param            | Required | Description |
|------------------|----------|-------------|
| `start`          | yes      | `"City, ST"`, `"City, State Name"` or `"lat,lng"` (must be in the USA) |
| `finish`         | yes      | same formats |
| `start_tank`     | no       | `empty` (default) or `full` — see *Assumptions* |
| `stop_penalty`   | no       | USD "cost" of making a stop (default `10`). `0` = absolute cheapest fuel bill |
| `corridor_miles` | no       | max distance of a station from the route (default `10`, range 0.5–50) |

POST accepts the same fields as JSON:

```json
{ "start": "Chicago, IL", "finish": "Houston, TX" }
```

**Response (abridged):**

```json
{
  "start":  { "query": "New York, NY", "label": "New York, NY", "lat": 40.74838, "lng": -73.996705, "source": "gazetteer" },
  "finish": { "query": "Los Angeles, CA", "label": "Los Angeles, CA", "lat": 33.973093, "lng": -118.247896, "source": "gazetteer" },
  "route":  { "distance_miles": 2657.3, "duration_hours": 44.29 },
  "fuel_stops": [
    {
      "stop_number": 1,
      "opis_id": 1234, "name": "BOLLA MARKET", "address": "...", "city": "Elizabeth", "state": "NJ",
      "lat": 40.66, "lng": -74.19,
      "price_per_gallon": 3.099,
      "mile_marker": 12.2,
      "distance_from_route_miles": 3.2,
      "fuel_on_arrival_gallons": 0.0,
      "gallons_purchased": 50.0,
      "cost": 154.95
    }
  ],
  "summary": {
    "total_fuel_cost_usd": 808.51,
    "total_gallons": 265.73,
    "number_of_stops": 6,
    "average_price_per_gallon": 3.043,
    "vehicle_mpg": 10.0,
    "vehicle_max_range_miles": 500.0,
    "start_tank": "empty",
    "fuel_to_reach_first_stop": { "miles": 12.2, "gallons": 1.22, "cost": 3.78, "note": "..." }
  },
  "map": { "type": "FeatureCollection", "features": [ "route LineString, start/finish/fuel-stop Points" ] },
  "map_url": "http://127.0.0.1:8000/api/route/map/?start=New+York%2C+NY&finish=Los+Angeles%2C+CA&start_tank=empty",
  "meta": {
    "cached": false, "routing_provider": "OSRM",
    "routing_api_calls": 1, "external_api_calls": 1,
    "stations_within_corridor": 365, "corridor_miles": 10.0, "stop_penalty_usd": 10.0,
    "elapsed_ms": 812.4
  }
}
```

- `map` is standard GeoJSON — paste it into <https://geojson.io> or render it with any map library.
- `map_url` opens a ready-made Leaflet/OpenStreetMap page with the route, numbered
  fuel stops and a cost table. It is served from the cached plan, so it **does not
  call the routing API again**.

### `GET /api/route/map/`
Same parameters; returns the HTML map.

### Errors

| Status | `error`                  | When |
|--------|--------------------------|------|
| 400    | `bad_request`            | missing/invalid parameters |
| 400    | `location_not_found`     | location can't be resolved or is outside the USA |
| 422    | `no_feasible_fuel_plan`  | a stretch of the route has no station within 500 miles |
| 502    | `routing_failed`         | the routing provider failed / no drivable route |

---

## How it works

```
request ─► geocode start/finish   (offline US gazetteer, 0 API calls)
        ─► OSRM /route            (1 API call, cached 24h)
        ─► match stations to route (numpy, in-memory index)
        ─► optimise fuel stops     (numpy DP)
        ─► JSON + GeoJSON          (whole plan cached 24h)
```

### 1. Data loading (`manage.py load_fuel_data`)
- The fuel CSV has **no coordinates**, and geocoding 6.6k addresses through a
  free API at request time would be slow and blow the call budget. Instead,
  stations are geocoded **once, offline**, by matching `(City, State)` against a
  bundled public-domain US cities table (`data/us_cities.csv`, 29k places).
  Name normalisation (`St.`/`Saint`, `Ft`/`Fort`, spacing) plus a fuzzy
  same-state fallback resolves 100% of US rows; five towns missing from the
  gazetteer are in `data/city_overrides.csv`.
- Canadian rows (620) are skipped — the route is USA-only.
- Duplicate OPIS IDs are collapsed to their lowest price.

### 2. Routing — OSRM (free, no API key)
`router.project-osrm.org` is called **once** with `overview=full&geometries=geojson`,
returning the full road geometry and distance. The base URL is configurable
(`ROUTING_BASE_URL`), so a self-hosted OSRM can be dropped in for production.

### 3. Stations along the route (`services/station_index.py`)
All stations live in numpy arrays in memory (loaded once per process). For a route:
1. bounding-box filter,
2. coarse pass against route samples every 20 mi to discard far stations,
3. fine pass against samples every 1 mi → each station's **distance off the route**
   and its **mile marker** (distance along the route).

### 4. Optimisation (`services/optimizer.py`)
Stations become points on a line with a price. A dynamic programme over
`(station, fuel in tank)` (fuel in 1-mile / 0.1-gallon steps) minimises

```
Σ gallons × price  +  stop_penalty × number_of_stops
```

The buy-step at each station is a vectorised prefix-minimum, so the solve is
`O(stations × 500)` numpy work — milliseconds.

- With `stop_penalty=0` it returns the exact cheapest plan (tests compare it
  with a brute-force solver on 300 random instances).
- The default `$10` penalty stops the solver from making "buy 1 gallon to save
  2 cents" stops. On a NY → LA test route this cuts 17 stops down to 6 for ~1.7% more.

---

## Assumptions

- **Vehicle:** 500-mile range (50-gallon equivalent), 10 mpg — configurable via env
  (`MAX_RANGE_MILES`, `MPG`).
- **Starting tank:**
  - `empty` (default): the vehicle starts nearly empty and fills up at a station
    within 30 miles of the origin (the optimiser picks which one). The few miles
    driven to reach it are billed at that station's price, so **total cost = fuel
    for the whole trip** (`total_gallons == distance / 10`).
  - `full`: the vehicle starts with a full, already-paid tank; only fuel bought on
    the way is billed (trips under 500 miles cost $0).
- **Station locations** are their town's centroid (the dataset has no coordinates),
  so `distance_from_route_miles` is approximate and the corridor defaults to 10 miles.
- Detour distance to a station is not added to fuel consumption (stations are
  mostly interstate truck stops at exits).
- The vehicle may arrive at the destination with an empty tank.

---

## Project layout

```
config/                      Django settings & root URLs
routing/
  models.py                  City, FuelStation
  views.py / urls.py         /api/route/, /api/route/map/
  services/
    geocoding.py             "City, ST" / "lat,lng" parsing, offline gazetteer, Nominatim fallback
    routing_client.py        OSRM client (+ cache)
    station_index.py         in-memory numpy station index, route matching
    optimizer.py             fuel-stop DP
    planner.py               orchestration + response building (+ cache)
    geo.py                   haversine, polyline helpers
  management/commands/load_fuel_data.py
  templates/routing/map.html Leaflet map
  tests/                     optimizer, geocoding, API tests
data/                        fuel prices CSV, US cities gazetteer, overrides
postman_collection.json
```

## Configuration (environment variables)

| Variable | Default | |
|---|---|---|
| `MAX_RANGE_MILES` | 500 | vehicle range |
| `MPG` | 10 | fuel economy |
| `CORRIDOR_MILES` | 10 | default station search corridor |
| `ORIGIN_RADIUS_MILES` | 30 | first fill-up window for an empty start |
| `STOP_PENALTY_USD` | 10 | default per-stop penalty |
| `ROUTING_BASE_URL` | `https://router.project-osrm.org` | OSRM server |
| `GEOCODER_FALLBACK` | 1 | use Nominatim if a place isn't in the gazetteer |
| `CACHE_TTL_SECONDS` | 86400 | route/plan cache TTL |
| `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS` | dev values | Django basics |

For multi-process deployments, swap the local-memory cache for Redis in
`CACHES` so the route/plan cache is shared across workers.

## Data credits
- Fuel prices: provided with the assessment (OPIS data, used only for this exercise).
- US cities: [kelvins/US-Cities-Database](https://github.com/kelvins/US-Cities-Database), MIT license (see `data/us_cities.LICENSE`).
- Routing: [OSRM](https://project-osrm.org/) demo server, © OpenStreetMap contributors.
- Map tiles: © OpenStreetMap contributors, rendered with Leaflet.
