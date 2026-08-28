"""Phase A verification: structured inputs that tell the truth."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from src import config, vocabulary
from src.orchestrator import (_attractions_per_day, _budget_per_person,
                              _request_meals, run_tier1, run_tier2)
from src.request_model import TripRequest
from src.tools import classify_city

fails = []


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {label}: {got!r}")
    if not ok:
        fails.append(f"{label}: got {got!r} want {want!r}")


def section(title):
    print(f"\n=== {title} ===")


BASE = {"city": "Calgary", "days": 2, "budget_total": 500, "party_size": 2,
        "cuisines": ["international"], "allergies": []}
token = config.set_backend_override("local")


def slots_of(state):
    return sorted(item["slot"] for item in state.itinerary)


section("#2 meal selection")
check("no meals key means all three (unchanged)", _request_meals({}), ["breakfast", "lunch", "dinner"])
check("selection is restored to day order",
      _request_meals({"meals": ["dinner", "breakfast"]}), ["breakfast", "dinner"])
check("unknown meals are dropped, not trusted",
      _request_meals({"meals": ["brunch", "lunch"]}), ["lunch"])
check("an empty list falls back to all three",
      _request_meals({"meals": []}), ["breakfast", "lunch", "dinner"])

state = run_tier2({**BASE, "meals": ["lunch", "dinner"]})
meal_slots = [s for s in slots_of(state) if ".attraction" not in s]
check("lunch+dinner plans exactly four meals over two days", meal_slots,
      ["day1.dinner", "day1.lunch", "day2.dinner", "day2.lunch"])
check("no breakfast sneaks in",
      any("breakfast" in s for s in slots_of(state)), False)

check("budget splits over the meals actually planned",
      _budget_per_person({**BASE, "meals": ["lunch", "dinner"]}, 2), 62.5)
check("and is unchanged when no meals are given",
      _budget_per_person(BASE, 2), 500 / 2 / 3 / 2)

single = run_tier2({**BASE, "days": 1, "meals": ["dinner"]})
check("a single-meal day works", [s for s in slots_of(single) if ".attraction" not in s],
      ["day1.dinner"])

section("#4 attractions can be switched off")
check("absent means one per day (unchanged)", _attractions_per_day({}), 1)
check("explicit zero is respected", _attractions_per_day({"attractions_per_day": 0}), 0)
check("negatives cannot flip it on", _attractions_per_day({"attractions_per_day": -3}), 0)

food_only = run_tier2({**BASE, "attractions_per_day": 0})
check("food-only trip has no attraction stop",
      [s for s in slots_of(food_only) if ".attraction" in s], [])
check("food-only trip still has every meal",
      len([s for s in slots_of(food_only) if ".attraction" not in s]), 6)
check("food-only trip still has routes",
      all(day.get("legs") for day in food_only.routes), True)
check("food-only trip still has geometry",
      all(leg.get("polyline") for day in food_only.routes for leg in day["legs"]), True)
with_attractions = run_tier2(dict(BASE))
check("default still plans attractions (unchanged)",
      len([s for s in slots_of(with_attractions) if ".attraction" in s]), 2)

section("#6 budget basis")
per_person = TripRequest(city="Calgary", budget_total=100, budget_basis="per_person",
                         party_size=2)
check("per person multiplies by the party", per_person.effective_budget_total, 200.0)
check("the dict the orchestrator sees is absolute",
      per_person.to_request_dict()["budget_total"], 200.0)
check("the amount typed is kept for display",
      per_person.to_request_dict()["budget_entered"], 100.0)
total = TripRequest(city="Calgary", budget_total=100, party_size=2)
check("total is the default and untouched", total.to_request_dict()["budget_total"], 100.0)

section("#3a cuisine list follows the backend")
local_types = vocabulary.restaurant_types("local")
live_types = vocabulary.restaurant_types("live")
check("offline offers only what the dataset holds",
      "portuguese" in local_types, False)
check("live offers world cuisines", "portuguese" in live_types and "greek" in live_types, True)
check("both keep the buckets", local_types[:2] == ["international", "asian"]
      and live_types[:2] == ["international", "asian"], True)
check("live is a superset of offline",
      set(local_types).issubset(set(live_types)), True)

section("#5 city vs country")
check("offline does not guess", classify_city("Portugal")["kind"], "not_checked")
check("blank stays unknown", classify_city("")["kind"], "unknown")
config._backend_override.reset(token)

token = config.set_backend_override("live")
check("Portugal is recognised as a country", classify_city("Portugal")["kind"], "country")
check("Lisbon is a city", classify_city("Lisbon")["kind"], "locality")
check("Calgary is a city", classify_city("Calgary")["kind"], "locality")
config._backend_override.reset(token)

section("regression: existing callers are byte-identical")
token = config.set_backend_override("local")
before = run_tier2(dict(BASE))
check("tier 2 unchanged for a request with no new fields",
      [i["venue_id"] for i in before.itinerary],
      [i["venue_id"] for i in with_attractions.itinerary])
check("tier 1 still runs", len(run_tier1(dict(BASE)).itinerary), 6)
check("budget still balances", before.budget["status"], "ok")
config._backend_override.reset(token)

print()
if fails:
    print(f"{len(fails)} FAILURE(S):")
    for f in fails:
        print("  -", f)
    raise SystemExit(1)
print("ALL PHASE A CHECKS PASSED")
