"""Phase 2 (day routing) + Phase 3 (critic convergence, budget) verification."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from src import config
from src.orchestrator import (_hours_issues, _max_price_level, _repair_budget,
                              _travel_anchors, best_candidate, run_tier1,
                              run_tier2, score_candidate)
from src.polyline import decode_polyline
from src.state import TOOL_SCHEMAS, TripState
from src.tools import TOOL_IMPLS, compute_day_route, search_restaurants
from src.tools import local_catalog as local
from src.tools import routes_live

fails = []


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {label}: {got!r}")
    if not ok:
        fails.append(f"{label}: got {got!r} want {want!r}")


def section(title):
    print(f"\n=== {title} ===")


S1 = {"city": "Calgary", "days": 2, "budget_total": 500, "party_size": 2,
      "cuisines": ["international"], "allergies": ["peanut"], "max_walk_km": 2.0}

section("2.2/2.3 compute_day_route fills one contract in both backends")
ORIGIN = {"slot": "day1.origin", "name": "Hotel", "lat": 51.0450, "lon": -114.0630}
STOPS = [
    {"slot": "day1.breakfast", "name": "B", "lat": 51.0380, "lon": -114.0900},
    {"slot": "day1.attraction1", "name": "A", "lat": 51.0470, "lon": -114.0620},
    {"slot": "day1.lunch", "name": "L", "lat": 51.0400, "lon": -114.0500},
    {"slot": "day1.dinner", "name": "D", "lat": 51.0550, "lon": -114.0700},
]
for backend in ("local", "live"):
    token = config.set_backend_override(backend)
    r = compute_day_route(ORIGIN, STOPS, mode="WALK", optimize=True)
    keys = sorted(set(r) & {"order", "legs", "totals", "optimized", "source"})
    check(f"{backend}: returns the full contract", keys,
          ["legs", "optimized", "order", "source", "totals"])
    check(f"{backend}: one leg per hop from the origin", len(r["legs"]), len(STOPS))
    check(f"{backend}: order is a permutation of the stops",
          sorted(r["order"]), list(range(len(STOPS))))
    check(f"{backend}: every leg carries geometry",
          all(leg.get("polyline") for leg in r["legs"]), True)
    check(f"{backend}: every leg carries both slots",
          all(leg.get("from_slot") and leg.get("to_slot") for leg in r["legs"]), True)
    check(f"{backend}: totals are positive", r["totals"]["minutes"] > 0, True)
    pts = [p for leg in r["legs"] for p in decode_polyline(leg["polyline"])]
    inside = all(-114.3 < x < -113.8 and 50.8 < y < 51.3 for x, y in pts)
    check(f"{backend}: decoded geometry lands in Calgary ({len(pts)} pts)", inside, True)
    config._backend_override.reset(token)

section("2.2 optimization actually reorders, and is refused when it breaks meals")
token = config.set_backend_override("local")
r_opt = compute_day_route(ORIGIN, STOPS, mode="WALK", optimize=True)
r_raw = compute_day_route(ORIGIN, STOPS, mode="WALK", optimize=False)
check("optimize=True reports optimized", r_opt["optimized"], True)
check("optimize=False keeps the given order", r_raw["order"], [0, 1, 2, 3])
check("optimizing shortens the day",
      r_opt["totals"]["minutes"] < r_raw["totals"]["minutes"], True)
st = run_tier2(dict(S1))
reordered = [d for d in st.routes if d.get("optimized")]
rejected = [d for d in st.routes if d.get("optimize_rejected")]
print(f"    days optimized={len(reordered)} rejected={len(rejected)}")
meal_rank = {"breakfast": 0, "lunch": 1, "dinner": 2}
for day in st.routes:
    seen = [meal_rank[s.split(".")[1]] for s in day["stop_order"]
            if s.split(".")[1] in meal_rank]
    check(f"day{day['day']}: meals stay in time order", seen, sorted(seen))

section("2.5 regression: no leg crosses a day or runs backwards")
for day in st.routes:
    for leg in day["legs"]:
        check(f"leg {leg['from_slot']} -> {leg['to_slot']} stays in one day",
              leg["from_slot"].split(".")[0] == leg["to_slot"].split(".")[0], True)

section("2.4 facade + tool schemas expose the new capability")
check("compute_day_route is in TOOL_IMPLS", "compute_day_route" in TOOL_IMPLS, True)
names = {s["function"]["name"] for s in TOOL_SCHEMAS}
check("compute_day_route is in TOOL_SCHEMAS", "compute_day_route" in names, True)
check("estimate_travel advertises all four modes",
      sorted(next(s for s in TOOL_SCHEMAS
                  if s["function"]["name"] == "estimate_travel"
                  )["function"]["parameters"]["properties"]["mode"]["enum"]),
      ["bicycle", "drive", "transit", "walk"])

section("2.6 transport mode reaches both backends")
check("routes_live maps every mode, not just WALK/DRIVE",
      [routes_live._travel_mode(m) for m in ("walk", "drive", "transit", "bicycle")],
      ["WALK", "DRIVE", "TRANSIT", "BICYCLE"])
mins = {m: local.estimate_travel(51.04, -114.07, 51.06, -114.02, mode=m)["minutes"]
        for m in ("walk", "bicycle", "transit", "drive")}
print("    local minutes by mode:", mins)
check("local speeds are distinct and ordered",
      list(mins.values()) == sorted(mins.values(), reverse=True)
      and len(set(mins.values())) == 4, True)

section("3.1 near/within_km narrows the search")
anchor = (51.0447, -114.0719)
wide = search_restaurants("Calgary", "dinner", limit=50)
near = search_restaurants("Calgary", "dinner", limit=50, near=anchor, within_km=1.0)
check("anchored search returns fewer venues", len(near) < len(wide), True)
check("every anchored result is inside the radius",
      all(r["distance_km"] <= 1.0 for r in near), True)
check("anchored results are sorted nearest first",
      [r["distance_km"] for r in near] == sorted(r["distance_km"] for r in near), True)

section("3.3 score_candidate is one deterministic rule")
cheap_bad = {"venue_id": "x1", "rating": 3.0, "avg_meal_cost": 10.0,
             "lat": 51.045, "lon": -114.07}
good = {"venue_id": "x2", "rating": 4.8, "avg_meal_cost": 40.0,
        "lat": 51.045, "lon": -114.07}
unaffordable = {"venue_id": "x3", "rating": 5.0, "avg_meal_cost": 500.0,
                "lat": 51.045, "lon": -114.07}
check("unaffordable is hard-rejected",
      score_candidate(unaffordable, budget_remaining=100, party_size=2) == float("-inf"), True)
check("best-rated affordable wins over cheapest",
      best_candidate([cheap_bad, good, unaffordable], used=set(),
                     budget_remaining=100, party_size=2)["venue_id"], "x2")
far = {"venue_id": "x4", "rating": 4.9, "avg_meal_cost": 40.0,
       "lat": 51.20, "lon": -114.30}
check("a closer good venue beats a distant slightly-better one",
      best_candidate([good, far], used=set(), budget_remaining=100, party_size=2,
                     anchor=anchor, max_leg_minutes=25.0)["venue_id"], "x2")
check("used venues are excluded",
      best_candidate([good], used={"x2"}, budget_remaining=100, party_size=2), None)

section("3.4 price ceiling no longer strands whole bands")
check("a tiny budget still allows the cheapest band", _max_price_level(5.0) >= 1, True)
check("a mid budget reaches band 4", _max_price_level(41.67), 4)
check("a large budget reaches the uncapped $$$$$ band", _max_price_level(200.0), 5)

section("3.2/3.5 the revision converges, and admits it when it cannot")
tight = dict(S1, max_leg_minutes=12.0)
st_tight = run_tier2(tight)
anchors = _travel_anchors(st_tight, st_tight.critic)
print(f"    travel issues={len([i for i in st_tight.critic.get('issues', []) if i['type']=='travel'])}"
      f" anchors resolved={len(anchors)}")
check("meta always carries the unresolved-issue list",
      "unresolved_issues" in st_tight.meta, True)
check("unresolved list matches the shipped verdict",
      bool(st_tight.meta["unresolved_issues"]) ==
      (st_tight.critic.get("verdict") == "revise"), True)
if st_tight.meta["unresolved_issues"]:
    check("shipping with unresolved issues is logged",
          any(e["agent"] == "ship" for e in st_tight.trace), True)
base = run_tier2(dict(S1))
print(f"    S1 travel minutes/day: {[d['totals']['minutes'] for d in base.routes]},"
      f" issues={len(base.critic.get('issues', []))}")

section("3.6 opening hours catch the closed-Monday traps")
# r005 is a dim sum house: breakfast;lunch only, 09:00-15:00. Checking it at a
# 19:00 dinner would flag it every day of the week, so use its real meal slot.
for venue, name, slot in (("r005", "Jade Lantern Dim Sum", "day1.lunch"),
                          ("a002", "Prairie Heritage Museum", "day1.attraction1")):
    fake = TripState(request={})
    fake.itinerary = [{"slot": slot, "venue_id": venue, "name": name,
                       "source": "local_dataset"}]
    mon = _hours_issues(fake, {"days": 1, "start_date": "2026-09-07"})   # a Monday
    tue = _hours_issues(fake, {"days": 1, "start_date": "2026-09-08"})   # a Tuesday
    check(f"{venue} flagged on Monday", [i["type"] for i in mon], ["hours"])
    check(f"{venue} accepted on Tuesday", tue, [])
fake = TripState(request={})
fake.itinerary = [{"slot": "day1.lunch", "venue_id": "r005", "name": "x",
                   "source": "google_places"}]
# Phase C: the check follows the SHAPE of the hours, not the recorded source.
# This assertion used to require that live-sourced stops be skipped, which was
# the defect - live plans shipped with opening hours never checked at all.
check("a stop is checked whatever source it claims",
      [i["type"] for i in _hours_issues(fake, {"days": 1, "start_date": "2026-09-07"})],
      ["hours"])
check("no start_date means no weekday to check",
      _hours_issues(fake, {"days": 1}), [])

section("determinism: tier_diff stays meaningful")
a, b = run_tier2(dict(S1)), run_tier2(dict(S1))
check("same request gives the same plan",
      [i["venue_id"] for i in a.itinerary], [i["venue_id"] for i in b.itinerary])
check("tier 1 and tier 2 still differ",
      len(run_tier1(dict(S1)).itinerary) != len(a.itinerary), True)
config._backend_override.reset(token)

print()
if fails:
    print(f"{len(fails)} FAILURE(S):")
    for f in fails:
        print("  -", f)
    raise SystemExit(1)
print("ALL PHASE 2 + PHASE 3 CHECKS PASSED")
