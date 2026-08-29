"""Phase F verification: infeasibility is caught before planning, and the
revision loop is not spent on something reselection cannot fix."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import time

from src import config, enrich, feasibility, verification
from src.orchestrator import _revision_would_help, run_tier2

fails = []


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {label}: {got!r}")
    if not ok:
        fails.append(f"{label}: got {got!r} want {want!r}")


def section(title):
    print(f"\n=== {title} ===")


BASE = {"city": "Calgary", "days": 2, "budget_total": 500, "party_size": 2,
        "cuisines": ["international"], "allergies": ["peanut"]}

section("F1 preflight is pure arithmetic over pools already fetched")
POOLS = {
    "day1.lunch": [{"venue_id": "a", "name": "Cheap", "avg_meal_cost": 20.0},
                   {"venue_id": "b", "name": "Dear", "avg_meal_cost": 90.0}],
    "day1.dinner": [{"venue_id": "c", "name": "Mid", "avg_meal_cost": 30.0}],
    "day1.attraction1": [{"venue_id": "z", "name": "Park", "cost": 5.0}],
}
report = feasibility.preflight({"budget_total": 200, "party_size": 2, "days": 1},
                               POOLS)
check("it prices the cheapest option in each meal slot",
      report["cheapest_total"], 100.0)   # (20 + 30) x 2 people
check("attractions are not counted against the meal budget",
      report["slots_priced"], 2)
check("a fitting budget is feasible", report["feasible"], True)
check("and needs no suggestions", report["suggestions"], [])

tight = feasibility.preflight({"budget_total": 80, "party_size": 2, "days": 1},
                              POOLS)
check("a budget below the cheapest plan is infeasible", tight["feasible"], False)
check("the shortfall is stated in the reason",
      "$100.00" in tight["reason"] and "$80.00" in tight["reason"], True)
check("the budget suggestion is the exact cheapest total",
      next(s["to"] for s in tight["suggestions"]
           if s["change"] == "budget_total"), 100.0)
check("empty pools are reported as unchecked, not as feasible",
      feasibility.preflight({"budget_total": 1, "party_size": 1}, {})["feasible"],
      None)
check("a candidate with no price cannot make it look cheap",
      feasibility.preflight({"budget_total": 200, "party_size": 1},
                            {"day1.lunch": [{"venue_id": "x"}]})["checked"], False)

section("F1 suggestions are arithmetic, and say when they are not")
multi = {f"day{day}.{meal}": [{"venue_id": f"{day}{meal}", "name": "V",
                               "avg_meal_cost": 30.0}]
         for day in (1, 2) for meal in ("lunch", "dinner")}
report = feasibility.preflight(
    {"budget_total": 130, "party_size": 2, "days": 2, "min_rating": 4.8}, multi)
check("four slots at $60 for the party price at $240",
      report["cheapest_total"], 240.0)
fewer = next(s for s in report["suggestions"] if s["change"] == "days")
check("dropping to one day is suggested", fewer["to"], 1)
check("and its cost is stated", "$120.00" in fewer["text"], True)
check("the day suggestion is costed", fewer["costed"], True)
gate = next(s for s in report["suggestions"] if s["change"] == "quality_gate")
check("relaxing the quality gate is offered but NOT costed", gate["costed"], False)
check("and says why it cannot be costed",
      "unknown until it is searched" in gate["text"], True)
check("no quality gate means no such suggestion",
      any(s["change"] == "quality_gate" for s in feasibility.preflight(
          {"budget_total": 130, "party_size": 2, "days": 2}, multi)["suggestions"]),
      False)

section("F3 the revision loop is only skipped when nothing could fix it")
infeasible = {"checked": True, "feasible": False}
feasible = {"checked": True, "feasible": True}
unchecked = {"checked": False}
budget_only = {"issues": [{"type": "budget"}, {"type": "budget"}]}
mixed = {"issues": [{"type": "budget"}, {"type": "travel"}]}
travel_only = {"issues": [{"type": "travel"}]}
check("budget-only issues on an infeasible plan: do not revise",
      _revision_would_help(budget_only, infeasible), False)
check("a travel issue is still worth revising, even when infeasible",
      _revision_would_help(mixed, infeasible), True)
check("travel-only always revises", _revision_would_help(travel_only, infeasible), True)
check("a feasible plan always revises",
      _revision_would_help(budget_only, feasible), True)
check("an unchecked preflight never suppresses the loop",
      _revision_would_help(budget_only, unchecked), True)
check("no issues means nothing to revise",
      _revision_would_help({"issues": []}, infeasible), False)

section("F4 a skip for time does not blame the gateway")
reviews = [("V", [{"text": "The grilled octopus was superb."}])]
skipped = enrich.dishes_for_venues(None, reviews,
                                   reason="Skipped to stay inside the budget.")
check("the stated reason is used", skipped[0]["note"], "Skipped to stay inside the budget.")
check("and it does not claim the LLM was unreachable",
      "unreachable" in skipped[0]["note"], False)
check("a genuine absence still says unreachable",
      "no LLM was reachable" in enrich.dishes_for_venues(None, reviews)[0]["note"],
      True)

section("F2 end to end, offline")
token = config.set_backend_override("local")
ok_plan = run_tier2(dict(BASE))
check("a feasible run is reported feasible",
      ok_plan.meta["feasibility"]["feasible"], True)
check("and plans every slot", len(ok_plan.itinerary), 8)

broke = run_tier2({**BASE, "budget_total": 60})
report = broke.meta["feasibility"]
check("an impossible budget is caught", report["feasible"], False)
check("before planning - it is in the trace",
      any(entry["agent"] == "feasibility" for entry in broke.trace), True)
check("with actionable options logged",
      any("option:" in entry["message"] for entry in broke.trace), True)
verdict = next(item for item in
               verification.verify({**BASE, "budget_total": 60}, broke.to_json())["requirements"]
               if item["requirement"] == "Total within budget")
check("the panel explains it is unsatisfiable, not just over",
      "No choice of venues can fit" in (verdict["reason"] or ""), True)
check("and prices the cheapest qualifying plan in that reason",
      f"${report['cheapest_total']:,.2f}" in (verdict["reason"] or ""), True)
config._backend_override.reset(token)

section("F5 live: the freed time lets the review read run")
token = config.set_backend_override("live")
started = time.time()
live = run_tier2({"city": "Lisbon", "days": 2, "budget_total": 200.0,
                  "party_size": 2, "meals": ["lunch", "dinner"],
                  "cuisines": ["portuguese", "international"], "allergies": [],
                  "attractions_per_day": 0, "min_rating": 4.8,
                  "transport_mode": "DRIVE", "search_radius_km": 5.0,
                  "max_leg_minutes": 25.0, "max_daily_travel_minutes": 240.0})
elapsed = time.time() - started
print(f"    live run: {elapsed:.1f}s of a {config.LATENCY_BUDGET_S}s budget, "
      f"critic iterations {live.critic.get('iteration')}")
check("the Lisbon request is reported infeasible",
      live.meta["feasibility"]["feasible"], False)
check("the cheapest qualifying plan is priced",
      live.meta["feasibility"]["cheapest_total"] > 200, True)
check("the doomed loop is skipped", live.critic.get("iteration"), 1)
check("it stays inside the latency budget", elapsed < config.LATENCY_BUDGET_S, True)
check("and with real margin, not by a whisker",
      elapsed < config.LATENCY_BUDGET_S * 0.85, True)
check("reviews are now read instead of skipped",
      any("skipped reading reviews" in entry["message"] for entry in live.trace),
      False)
config._backend_override.reset(token)

print()
if fails:
    print(f"{len(fails)} FAILURE(S):")
    for f in fails:
        print("  -", f)
    raise SystemExit(1)
print("ALL PHASE F CHECKS PASSED")
