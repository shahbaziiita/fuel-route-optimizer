"""Django settings for the fuel route optimizer."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "routing",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# In-process cache: routes and full trip plans are cached so repeated
# requests (and the /map view) never hit the routing API again.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "fuel-route-optimizer",
        "OPTIONS": {"MAX_ENTRIES": 2000},
    }
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Trip planner configuration
# ---------------------------------------------------------------------------
FUEL_PLANNER = {
    # Vehicle
    "MAX_RANGE_MILES": float(os.environ.get("MAX_RANGE_MILES", 500)),
    "MPG": float(os.environ.get("MPG", 10)),
    # A station counts as "on the route" if it is within this many miles of
    # the route line. Stations are geocoded to their city centre, so this is
    # deliberately a bit generous.
    "CORRIDOR_MILES": float(os.environ.get("CORRIDOR_MILES", 10)),
    # With an empty starting tank, the first fill-up must be within this many
    # route-miles of the origin.
    "ORIGIN_RADIUS_MILES": float(os.environ.get("ORIGIN_RADIUS_MILES", 30)),
    # Virtual cost (USD) of making a stop. Stops the optimiser from making
    # "buy 1 gallon to save 2 cents" detours. 0 = strictly cheapest fuel bill.
    "STOP_PENALTY_USD": float(os.environ.get("STOP_PENALTY_USD", 10)),
    # Routing: OSRM (free, no API key). Point this at a self-hosted OSRM in prod.
    "ROUTING_BASE_URL": os.environ.get(
        "ROUTING_BASE_URL", "https://router.project-osrm.org"
    ),
    "ROUTING_TIMEOUT_SECONDS": float(os.environ.get("ROUTING_TIMEOUT_SECONDS", 15)),
    # Geocoding: locations are resolved offline from the bundled US cities
    # table. Only if that fails do we fall back to Nominatim (free, no key).
    "GEOCODER_FALLBACK_ENABLED": os.environ.get("GEOCODER_FALLBACK", "1") == "1",
    "GEOCODER_URL": os.environ.get(
        "GEOCODER_URL", "https://nominatim.openstreetmap.org/search"
    ),
    "USER_AGENT": os.environ.get(
        "HTTP_USER_AGENT", "fuel-route-optimizer/1.0 (assessment project)"
    ),
    "CACHE_TTL_SECONDS": int(os.environ.get("CACHE_TTL_SECONDS", 60 * 60 * 24)),
}
