"""Dropdown vocabulary, read from the dataset rather than retyped.

WHY THIS IS NOT A LIST OF CONSTANTS: every hardcoded copy of these values is a
chance to drift from the data. A misspelt allergen is the worst case - the
filter silently matches nothing and the venue it should have excluded is served
to someone with an allergy. The CSVs in data/csv/ are the single source of
truth (see data/seed.py), so the options are derived from them at import.
"""
from __future__ import annotations

import csv
import functools
import importlib.util

from . import config

CSV_DIR = config.DATA_DIR / "csv"


def _seed_module():
    """Import data/seed.py by path - data/ is not a package.

    seed.py guards its work behind __main__, so importing it only defines the
    constants and touches nothing on disk.
    """
    spec = importlib.util.spec_from_file_location(
        "foodie_seed", config.DATA_DIR / "seed.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

# Broad buckets the planner understands, defined in tools/local_catalog.py.
# They are not cuisines in the data, so they are offered alongside them.
CUISINE_BUCKETS = ("international", "asian")

# data/seed.py writes an explicit true/false for all nine, so a missing key can
# never read as "safe". Imported from the seeder rather than retyped: a typo
# here would silently disable an allergen filter, which is the one failure in
# this project that could actually hurt somebody.
CANONICAL_ALLERGENS = _seed_module().CANONICAL_ALLERGENS

TRANSPORT_MODES = ("WALK", "TRANSIT", "BICYCLE", "DRIVE")

MEAL_SLOTS = ("breakfast", "lunch", "dinner")

# Cuisines worth offering in LIVE mode. The offline list is whatever the Calgary
# CSVs happen to contain, which is the wrong vocabulary once the city is Lisbon:
# Google Places will happily search "portuguese restaurant in Lisbon", but
# "portuguese" is not in the dataset, so offering it offline would return
# nothing. Hence two lists, chosen by backend.
LIVE_CUISINES = (
    "portuguese", "spanish", "greek", "italian", "french", "turkish",
    "lebanese", "moroccan", "german", "british", "irish", "belgian",
    "scandinavian", "eastern_european", "georgian", "japanese", "korean",
    "chinese", "sichuan", "cantonese", "thai", "vietnamese", "malaysian",
    "indonesian", "filipino", "indian", "nepalese", "sri_lankan", "mexican",
    "peruvian", "brazilian", "argentinian", "caribbean", "ethiopian",
    "nigerian", "middle_eastern", "mediterranean", "american", "canadian",
    "seafood", "steakhouse", "bbq", "vegetarian", "vegan", "bakery", "cafe",
    "brunch", "dessert", "street_food", "tapas", "fine_dining", "bistro",
)


def _rows(path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _values(pattern: str, column: str) -> list[str]:
    found = set()
    for path in sorted(CSV_DIR.glob(pattern)):
        for row in _rows(path):
            value = (row.get(column) or "").strip()
            if value:
                found.add(value)
    return sorted(found)


@functools.cache
def dataset_cities() -> list[str]:
    """Cities the offline dataset actually covers.

    Derived from the `<city>_restaurants.csv` filenames the seeder globs, so
    dropping vancouver_restaurants.csv in needs no code change here either.
    """
    return sorted(path.name.rsplit("_", 1)[0].replace("_", " ").title()
                  for path in CSV_DIR.glob("*_restaurants.csv"))


@functools.cache
def dataset_cuisines() -> list[str]:
    """Every cuisine actually present in the offline CSVs."""
    return _values("*_restaurants.csv", "cuisine")


@functools.cache
def restaurant_types(backend: str = "local") -> list[str]:
    """Cuisine options for a backend.

    Offline can only offer what the dataset holds. Live can offer any cuisine,
    because the value goes into a Places text query - so an `auto` or `live` run
    gets the world list, with the dataset's own cuisines folded in so switching
    backends never silently drops a choice the user already made.
    """
    buckets = list(CUISINE_BUCKETS)
    if (backend or "local").lower() == "local":
        return buckets + dataset_cuisines()
    return buckets + sorted(set(LIVE_CUISINES) | set(dataset_cuisines()))


@functools.cache
def attraction_types() -> list[str]:
    return _values("*_attractions.csv", "category")


@functools.cache
def dietary_options() -> list[str]:
    found = set()
    for path in sorted(CSV_DIR.glob("*_restaurants.csv")):
        for row in _rows(path):
            found.update(part for part in (row.get("dietary_options") or "").split(";") if part)
    return sorted(found)


@functools.cache
def dataset_counts() -> dict[str, int]:
    """Row counts for the diagnostics panel."""
    return {
        "restaurants": sum(len(_rows(p)) for p in CSV_DIR.glob("*_restaurants.csv")),
        "attractions": sum(len(_rows(p)) for p in CSV_DIR.glob("*_attractions.csv")),
    }


def covers_city(city: str) -> bool:
    """Whether the offline dataset has anything for this city (see B10)."""
    return (city or "").strip().lower() in {c.lower() for c in dataset_cities()}
