"""Phase J: attractions that match what was actually asked for.

Three separate defects met here. A request could not say "two attractions", so
it got one. It could say "museum" and the word was validated, stored, sent over
HTTP and then read by nothing - which is the whole reason Toronto kept coming
back with the CN Tower. And every live attraction was stamped kid_friendly=True
by a hardcoded literal, which is a fabricated fact, not a filter.

The live checks need a Google key. They are skipped, loudly, without one - a
skipped check must never look like a passing one.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import config
from src.agents import intent

from src.orchestrator import (DAY_ORDER, _execute_attractions_tier2, run_tier2)
from src.state import slot_ids
from src.tools import search_attractions, search_restaurants
from src.tools import local_catalog as local

fails = []
skipped = []


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {label}: {got!r}")
    if not ok:
        fails.append(f"{label}: got {got!r} want {want!r}")


def check_that(label, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f": {detail}" if detail else ""))
    if not ok:
        fails.append(f"{label} {detail}")


def section(title):
    print(f"\n=== {title} ===")


def skip(label, why):
    print(f"SKIP  {label}: {why}")
    skipped.append(label)


def attractions(request, days, per_day=None):
    """slot -> chosen venue. The executor is a coroutine returning
    (selected, failures), and its default count has to stay untouched."""
    extra = {} if per_day is None else {"attractions_per_day": per_day}
    selected, failures = asyncio.run(
        _execute_attractions_tier2(request, days, **extra))
    assert not failures, f"search failed: {failures}"
    return selected


LIVE = config.LIVE_DATA_AVAILABLE
token = config.set_backend_override("local")

# ------------------------------------------------------------------ J1: count
section("J1 N attractions per day, distinct venues, either side of lunch")

REQUEST = {"city": "Calgary", "days": 2, "budget_total": 600, "party_size": 2,
           "cuisines": ["international"]}

two = attractions(dict(REQUEST), 2, 2)
check("two per day fills attraction1 and attraction2 on every day",
      sorted(two), ["day1.attraction1", "day1.attraction2",
                    "day2.attraction1", "day2.attraction2"])

picked = [venue["venue_id"] for venue in two.values()]
check_that("all four are distinct venues", len(set(picked)) == len(picked) == 4,
           f"{picked}")

# DAY_ORDER is what the formatter walks, so morning/afternoon is decided there
# rather than by any label we could attach to a slot.
order = list(DAY_ORDER)
check_that("attraction1 falls before lunch and attraction2 after it",
           order.index("attraction1") < order.index("lunch") < order.index("attraction2"),
           f"{order}")

check("zero per day means no attraction slots at all",
      attractions(dict(REQUEST), 2, 0), {})
check("the default is still exactly one per day",
      sorted(attractions(dict(REQUEST), 1)),
      ["day1.attraction1"])
check("slot_ids already knew how to count - not extended, confirmed",
      slot_ids(days=1, meals=(), attractions_per_day=3),
      ["day1.attraction1", "day1.attraction2", "day1.attraction3"])

section("J1 the plan actually carries the extra stop end to end")
state = run_tier2(dict(REQUEST, attractions_per_day=2))
slots = [item["slot"] for item in state.itinerary]
check("both attractions survive into the itinerary",
      [s for s in slots if "attraction" in s],
      ["day1.attraction1", "day1.attraction2",
       "day2.attraction1", "day2.attraction2"])

# ------------------------------------------------------------- J2: categories
section("J2 the category reaches the search - the CN Tower fix")

offline_museums = search_attractions(city="Calgary", category="museum", limit=5)
check_that("offline, a category still filters",
           all(v.get("category") == "museum" for v in offline_museums),
           f"{[v.get('category') for v in offline_museums]}")

mixed = attractions(dict(REQUEST, attraction_types=["museum", "park"]), 1, 2)
kinds = {venue.get("category") for venue in mixed.values()}
check_that("two types return a mix, not two of the first",
           kinds == {"museum", "park"}, f"{kinds}")

if LIVE:
    config._backend_override.reset(token)
    token = config.set_backend_override("live")
    plain = [v["name"] for v in search_attractions(city="Toronto", limit=6)]
    museums = [v["name"] for v in
               search_attractions(city="Toronto", category="museum", limit=6)]
    print(f"      no category -> {plain}")
    print(f"      museum      -> {museums}")
    check_that("a museum search returns museums",
               any(("Royal Ontario" in n) or ("Art Gallery" in n) for n in museums),
               f"{museums}")
    check_that("and no longer the CN Tower",
               not any("CN Tower" in n for n in museums), f"{museums}")
    config._backend_override.reset(token)
    token = config.set_backend_override("local")
else:
    skip("live museum search in Toronto", "no Google API key configured")

# ---------------------------------------------------------- J3: kids, for real
section("J3 family_friendly filters on a real field, and only excludes")

# The whole eligible pool, not a top slice: the 14 Calgary dinner venues marked
# unsuitable all rank below the top 20, so limit=20 would have tested nothing.
everyone = search_restaurants(city="Calgary", meal="dinner", limit=200)
kids_ok = search_restaurants(city="Calgary", meal="dinner", limit=200,
                             family_friendly=True)
excluded = {v["venue_id"] for v in everyone} - {v["venue_id"] for v in kids_ok}
check_that("asking for family friendly narrows the pool",
           len(kids_ok) < len(everyone), f"{len(kids_ok)} of {len(everyone)}")
check_that("nothing marked unsuitable survives the filter",
           all(v.get("kid_friendly") is not False for v in kids_ok),
           f"{[v.get('kid_friendly') for v in kids_ok]}")
check_that("everything marked suitable is kept",
           {v["venue_id"] for v in everyone if v.get("kid_friendly") is True}
           <= {v["venue_id"] for v in kids_ok})
check("exactly the venues marked unsuitable are the ones dropped",
      sorted(excluded),
      sorted(v["venue_id"] for v in everyone if v.get("kid_friendly") is False))
check("not asking changes nothing",
      [v["venue_id"] for v in search_restaurants(city="Calgary", meal="dinner",
                                                 limit=200, family_friendly=False)],
      [v["venue_id"] for v in everyone])

section("J3 unknown is not excluded, and not promised either")
# Neither backend may turn "nobody recorded it" into "not suitable" - that is
# the same fabrication as the hardcoded True, pointing the other way. Forced
# here rather than waited for, because the sample data records every venue.
check("an unrecorded value survives as None, not False",
      (local._tri_state(None), local._tri_state(0), local._tri_state(1)),
      (None, False, True))
check("the SQL keeps unknowns rather than dropping them",
      "kid_friendly IS NULL OR kid_friendly = 1" in
      Path(local.__file__).read_text(encoding="utf-8"), True)
unknown = [v for v in kids_ok if v.get("kid_friendly") is None]
print(f"      {len(unknown)} of {len(kids_ok)} kept venues have no recorded answer")

if LIVE:
    config._backend_override.reset(token)
    token = config.set_backend_override("live")
    live_venues = search_restaurants(city="Toronto", meal="dinner", limit=5)
    flags = [(v["name"], v.get("kid_friendly")) for v in live_venues]
    print(f"      live kid_friendly -> {flags}")
    check_that("live results are no longer all hardcoded True",
               not all(flag is True for _, flag in flags), f"{flags}")
    check_that("the value is a real tri-state, never a fabricated default",
               all(flag in (True, False, None) for _, flag in flags), f"{flags}")
    config._backend_override.reset(token)
    token = config.set_backend_override("local")
else:
    skip("live goodForChildren round trip", "no Google API key configured")

# ------------------------------------------------------- the intent side of J
section("J the extractor can express all of it")
draft = intent.validate({"city": "Toronto", "attractions_per_day": 2,
                         "attraction_types": ["museum", "park"],
                         "family_friendly": True}, "local")
check("a count, a category and a kids flag all survive validation",
      {k: draft["fields"][k] for k in
       ("attractions_per_day", "attraction_types", "family_friendly")},
      {"attractions_per_day": 2, "attraction_types": ["museum", "park"],
       "family_friendly": True})
check("the deprecated bool still works and now sets the count",
      intent.validate({"attractions_wanted": True}, "local")["fields"],
      {"attractions_wanted": True, "attractions_per_day": 1})
check("an invented category is dropped, not searched for",
      [r["field"] for r in
       intent.validate({"attraction_types": ["klingon opera house"]},
                       "local")["rejected"]],
      ["attraction_types"])

config._backend_override.reset(token)

print()
if skipped:
    print(f"{len(skipped)} CHECK(S) SKIPPED (no live API): {', '.join(skipped)}")
if fails:
    print(f"{len(fails)} FAILURE(S):")
    for f in fails:
        print("  -", f)
    raise SystemExit(1)
print("ALL PHASE J ATTRACTION CHECKS PASSED")
