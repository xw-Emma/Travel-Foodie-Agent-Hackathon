"""The restaurant executor directs its own tool use - and only its own.

This is the one place the model decides what to do next rather than filling a
slot in a script: it reads what a search returned and may search again with a
different strategy. That is worth having only if the line is drawn in the right
place, so this suite drives the real tool loop with a scripted model and checks
both halves of it.

  Strategy is the model's:   cuisine, area, how tight a ceiling, how many rows.
  Constraints are not:       city, meal, allergen exclusion, quality floor,
                             search anchor - re-applied on EVERY call whatever
                             the model passed, and the ceilings tighten only.

A model that can widen its own search is useful. One that can drop an allergy
filter is dangerous. Everything below exists to keep those apart.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import config, orchestrator
from src.fuelix_client import FuelixError

fails = []


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


# ------------------------------------------------------------- the stand-ins
class ScriptedClient:
    """A Fuel iX stand-in that plays a fixed sequence of model turns.

    The point is to drive the REAL run_tool_loop and the REAL search wrapper.
    Only the model's decisions are faked, because those are exactly what we
    need to control in order to test what happens when it makes a bad one.
    """

    def __init__(self, turns):
        self.turns = list(turns)
        self.rounds = 0

    def chat(self, model="", messages=None, tools=None, **kwargs):
        self.rounds += 1
        return self.turns.pop(0) if self.turns else {"content": "[]"}


def searches(**arguments):
    return {"role": "assistant", "tool_calls": [{
        "id": f"call{len(arguments)}", "type": "function",
        "function": {"name": "search_restaurants",
                     "arguments": json.dumps(arguments)}}]}


def answers(venue_id="r001"):
    return {"role": "assistant", "content": json.dumps(
        [{"venue_id": venue_id, "name": "scripted", "why_recommended": "test"}])}


REQUEST = {"city": "Calgary", "days": 2, "budget_total": 500, "party_size": 2,
           "cuisines": ["international"], "allergies": ["peanut"]}
TASK = {"agent": "restaurant", "slot": "day1.dinner", "meal": "dinner",
        "area_hint": "Beltline", "budget_per_person": 60.0,
        "constraints": {"allergies": ["peanut"], "cuisines": ["italian"]}}


def run(turns, task=None, request=None, anchor=None):
    """Drive the executor, recording every query that reached the tool."""
    recorded = []
    real = orchestrator.search_restaurants

    def recorder(**kwargs):
        recorded.append(dict(kwargs))
        return real(**kwargs)

    orchestrator.search_restaurants = recorder
    try:
        pick, pool = orchestrator._pick_restaurant_with_tool_loop(
            ScriptedClient(turns), dict(task or TASK), dict(request or REQUEST),
            set(), anchor)
        return pick, pool, recorded, None
    except Exception as error:  # noqa: BLE001 - the suite reports it as a result
        return None, [], recorded, error
    finally:
        orchestrator.search_restaurants = real


token = config.set_backend_override("local")

# ------------------------------------------------------- the autonomy is real
section("the model actually directs the search")

pick, pool, queries, error = run([
    searches(cuisine="italian"),
    searches(cuisine=None),          # explicit null = "drop the cuisine filter"
    answers(),
])
check("both searches ran", len(queries), 2)
check("the first used the cuisine the model named", queries[0]["cuisine"], "italian")
check("the second dropped it, because the model said so",
      queries[1]["cuisine"], None)
check_that("no exception", error is None, f"{error}")

pick, pool, queries, error = run([
    searches(cuisine="italian", area="Beltline"),
    searches(cuisine=None, area=None),
    answers(),
])
check("the model can drop the area hint too",
      [q["area"] for q in queries], ["Beltline", None])

section("an omitted argument still falls back to the task, as before")
pick, pool, queries, error = run([searches(), answers()])
check("no cuisine named means the task's cuisine", queries[0]["cuisine"], "italian")
check("no area named means the task's area hint", queries[0]["area"], "Beltline")

# --------------------------------------------------- the constraints are not
section("what the model may NOT relax")

# search_radius_km is what gives the trip a real anchor, so the "the model
# cannot move the search area" check has something to be wrong about.
ANCHORED = dict(REQUEST, search_radius_km=5.0)
hostile = searches(city="Paris", meal="breakfast", exclude_flags=[],
                   cuisine="italian", near=[0.0, 0.0])
pick, pool, queries, error = run([hostile, answers()], request=ANCHORED)
q = queries[0]
check("the city stays the trip's city", q["city"], "Calgary")
check("the meal stays the slot's meal", q["meal"], "dinner")
check("the allergen exclusion cannot be emptied",
      q["exclude_flags"], ["peanut_risk"])
anchor_expected = orchestrator._search_area(ANCHORED)[0]
check_that("the trip has a real anchor to move", anchor_expected is not None)
check("the search anchor is the code's, not the model's",
      q["near"], anchor_expected)
check_that("and the strategy it was allowed to set still went through",
           q["cuisine"] == "italian", f"{q['cuisine']}")

section("the quality floor is applied whatever the model passes")
gated = dict(REQUEST, min_rating=4.6, min_reviews=500)
pick, pool, queries, error = run([searches(cuisine=None), answers()],
                                 request=gated)
check("min_rating reaches the tool", queries[0]["min_rating"], 4.6)
check("min_reviews reaches the tool", queries[0]["min_reviews"], 500)

section("ceilings tighten, never loosen")
ceiling = orchestrator._max_price_level(TASK["budget_per_person"])
pick, pool, queries, error = run([
    searches(price_level_max=99),
    searches(price_level_max=1),
    answers(),
])
check(f"a ceiling above the budget's ({ceiling}) is clamped down",
      queries[0]["price_level_max"], ceiling)
check("a ceiling below it is the model's to choose",
      queries[1]["price_level_max"], 1)

_, trip_radius = orchestrator._search_area(ANCHORED)
pick, pool, queries, error = run([
    searches(within_km=9999),
    searches(within_km=0.5),
    answers(),
], request=ANCHORED)
check("a radius beyond the trip's is clamped down",
      queries[0]["within_km"], trip_radius)
check("a tighter radius is honoured", queries[1]["within_km"], 0.5)

# ------------------------------------------------------------ the hard bounds
section("the search count is bounded in code, not in the prompt")
pick, pool, queries, error = run([
    searches(cuisine="italian"),
    searches(cuisine=None),
    searches(cuisine="asian"),       # over the cap
    answers(),
])
check(f"at most EXECUTOR_MAX_SEARCHES ({config.EXECUTOR_MAX_SEARCHES}) hit the API",
      len(queries), config.EXECUTOR_MAX_SEARCHES)
check_that("and the run still produced a venue", bool(pick), f"{error}")

section("exhausting the rounds no longer costs the slot")
# Every turn asks for another tool, so run_tool_loop raises. Before this change
# that exception left the slot empty; the candidates it had already verified
# are still in hand and must still be usable.
pick, pool, queries, error = run([searches() for _ in range(6)])
check_that("no exception escaped", error is None, f"{error}")
check_that("a venue still came back", bool(pick), f"{pick}")
check_that("and it came from the verified pool",
           bool(pick) and pick["venue_id"] in {row["venue_id"] for row in pool})
check_that("the reason says the rounds ran out, not that the model answered badly",
           "tool rounds" in pick["why"], pick["why"])

section("an off-list answer is still overridden by code")
pick, pool, queries, error = run([searches(), answers(venue_id="not-a-real-id")])
check_that("the invented id was not accepted",
           pick["venue_id"] != "not-a-real-id", f"{pick['venue_id']}")
check_that("a real candidate was substituted",
           pick["venue_id"] in {row["venue_id"] for row in pool})
check("and the substitution is admitted in the reason",
      "off-list model response" in pick["why"], True)
check_that("which is a different reason from running out of rounds",
           "tool rounds" not in pick["why"], pick["why"])

section("nothing the model did could leak the allergen")
pick, pool, queries, error = run([
    searches(exclude_flags=[], cuisine=None),
    searches(exclude_flags=["nothing_risk"]),
    answers(),
])
leaked = [row["name"] for row in pool
          if (row.get("dietary_flags") or {}).get("peanut_risk") is True]
check("no peanut-risk venue reached the pool", leaked, [])
check_that("and the pool was not empty, so that meant something",
           len(pool) > 0, f"{len(pool)} candidates")
check("every search kept the exclusion",
      [q["exclude_flags"] for q in queries],
      [["peanut_risk"]] * len(queries))

# ------------------------------------------------------------- the off switch
section("FOODIE_EXECUTOR_TOOL_ROUNDS=2 restores the single forced search")
original = config.EXECUTOR_MAX_SEARCHES
config.EXECUTOR_MAX_SEARCHES = 1
try:
    pick, pool, queries, error = run([
        searches(cuisine="italian"),
        searches(cuisine=None),
        answers(),
    ])
    check("the model gets one tool-driven search, not two",
          len(pick.get("search_trace") or []), 1)
    check_that("and the run still produces a venue", bool(pick), f"{error}")
finally:
    config.EXECUTOR_MAX_SEARCHES = original

section("a repeated search costs nothing extra")
# No area_hint, so the separate "nothing found, retry without the area" fallback
# cannot fire and every recorded query is one the model asked for.
NO_AREA = dict(TASK, area_hint="")
pick, pool, queries, error = run([
    searches(cuisine="italian"),
    searches(cuisine="italian"),      # identical: same rows, no second API call
    answers(),
], task=NO_AREA)
check("the duplicate never reached the tool", len(queries), 1)
check("but it is recorded rather than hidden",
      [step.get("repeat") for step in pick["search_trace"]], [False, True])
check_that("and the venue still came back", bool(pick), f"{error}")

section("the trace shows what the model decided")
pick, pool, queries, error = run([
    searches(cuisine="italian"), searches(cuisine=None), answers()])
trace = pick.get("search_trace") or []
check("one trace entry per real search", len(trace), 2)
check("each entry records the strategy and what it returned",
      sorted(trace[0]),
      ["area", "cuisine", "price_level_max", "repeat", "results"])
check("a single search leaves nothing to report",
      len(run([searches(), answers()])[0]["search_trace"]), 1)

config._backend_override.reset(token)

print()
if fails:
    print(f"{len(fails)} FAILURE(S):")
    for f in fails:
        print("  -", f)
    raise SystemExit(1)
print("ALL EXECUTOR AGENCY CHECKS PASSED")
