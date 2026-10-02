"""Resolve user-supplied locations to coordinates.

Resolution order (cheapest first):
  1. "lat,lng" literal                -> no lookup
  2. "City, ST" / "City, State Name"   -> offline US gazetteer in the DB
  3. Nominatim (optional fallback)     -> 1 external call, restricted to the US
"""

import difflib
import re
from dataclasses import dataclass

import requests
from django.conf import settings
from django.core.cache import cache

from ..models import City

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}
STATE_NAME_TO_CODE = {v.lower(): k for k, v in US_STATES.items()}

_LATLNG_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")
_COUNTRY_SUFFIX_RE = re.compile(r",?\s*(usa|us|u\.s\.a\.|u\.s\.|united states(?: of america)?)\s*$", re.I)

# Rough bounding boxes covering the 50 states (+DC).
_US_BOXES = (
    (24.3, 49.5, -125.0, -66.8),   # contiguous US
    (51.0, 71.5, -179.2, -129.9),  # Alaska
    (18.8, 22.4, -160.5, -154.7),  # Hawaii
)


class LocationError(ValueError):
    """Raised when a location can't be resolved or is outside the USA."""


@dataclass(frozen=True)
class Location:
    query: str
    label: str
    lat: float
    lng: float
    source: str  # "coordinates" | "gazetteer" | "nominatim"

    def as_dict(self):
        return {
            "query": self.query,
            "label": self.label,
            "lat": round(self.lat, 6),
            "lng": round(self.lng, 6),
            "source": self.source,
        }


def city_key(name: str) -> str:
    """Normalise a city name so 'St. Louis', 'Saint Louis' and 'st louis'
    (and 'Brookpark' vs 'Brook Park') all collide."""
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\bsaint\b", "st", s)
    s = re.sub(r"\bfort\b", "ft", s)
    s = re.sub(r"\bmount\b", "mt", s)
    return re.sub(r"\s+", "", s)


def is_in_usa(lat: float, lng: float) -> bool:
    return any(a <= lat <= b and c <= lng <= d for a, b, c, d in _US_BOXES)


def normalise_state(token: str):
    token = token.strip().rstrip(".")
    if token.upper() in US_STATES:
        return token.upper()
    return STATE_NAME_TO_CODE.get(token.lower())


def split_city_state(text: str):
    """'Dallas, TX' / 'Dallas TX' / 'New York, New York' -> ('Dallas', 'TX')."""
    text = _COUNTRY_SUFFIX_RE.sub("", text.strip()).strip(" ,")
    if "," in text:
        city, _, state = text.rpartition(",")
        code = normalise_state(state)
        if code and city.strip():
            return city.strip(), code
    # no comma: try the last one or two words as the state
    words = text.split()
    for n in (2, 1):
        if len(words) > n:
            code = normalise_state(" ".join(words[-n:]))
            if code:
                return " ".join(words[:-n]), code
    return None


def lookup_city(city: str, state: str):
    """Return a City row (or None). Exact normalised match first, then a
    fuzzy match restricted to the same state."""
    key = city_key(city)
    row = City.objects.filter(state=state, name_key=key).first()
    if row:
        return row
    keys = list(City.objects.filter(state=state).values_list("name_key", flat=True))
    close = difflib.get_close_matches(key, keys, n=1, cutoff=0.88)
    if close:
        return City.objects.filter(state=state, name_key=close[0]).first()
    return None


def _nominatim(query: str):
    cfg = settings.FUEL_PLANNER
    try:
        resp = requests.get(
            cfg["GEOCODER_URL"],
            params={"q": query, "format": "json", "limit": 1, "countrycodes": "us"},
            headers={"User-Agent": cfg["USER_AGENT"]},
            timeout=cfg["ROUTING_TIMEOUT_SECONDS"],
        )
        resp.raise_for_status()
        results = resp.json()
    except (requests.RequestException, ValueError):
        return None
    if not results:
        return None
    r = results[0]
    return float(r["lat"]), float(r["lon"]), r.get("display_name", query)


def geocode(query: str) -> tuple[Location, int]:
    """Resolve `query` to a Location. Returns (location, external_calls_made)."""
    if not query or not query.strip():
        raise LocationError("Location must not be empty.")
    query = query.strip()

    m = _LATLNG_RE.match(query)
    if m:
        lat, lng = float(m.group(1)), float(m.group(2))
        if not is_in_usa(lat, lng):
            raise LocationError(f"'{query}' is not inside the USA (expected 'lat,lng').")
        return Location(query, f"{lat:.5f},{lng:.5f}", lat, lng, "coordinates"), 0

    parsed = split_city_state(query)
    if parsed:
        row = lookup_city(*parsed)
        if row:
            return Location(query, str(row), row.latitude, row.longitude, "gazetteer"), 0

    cfg = settings.FUEL_PLANNER
    if cfg["GEOCODER_FALLBACK_ENABLED"]:
        cache_key = f"geocode:{query.lower()}"
        hit = cache.get(cache_key)
        calls = 0
        if hit is None:
            hit = _nominatim(query) or False
            calls = 1
            cache.set(cache_key, hit, cfg["CACHE_TTL_SECONDS"])
        if hit and is_in_usa(hit[0], hit[1]):
            return Location(query, hit[2], hit[0], hit[1], "nominatim"), calls

    raise LocationError(
        f"Could not find '{query}' in the USA. Use 'City, ST' (e.g. 'Dallas, TX') "
        f"or 'lat,lng' (e.g. '32.7767,-96.7970')."
    )
