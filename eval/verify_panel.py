"""Phase D + E verification: backups with explainable ranking, and the panel."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import config, verification
from src.orchestrator import run_tier2, score_breakdown

fails = []


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {label}: {got!r}")
    if not ok:
        fails.append(f"{label}: got {got!r} want {want!r}")


def section(title):
    print(f"\n=== {title} ===")


def by_name(report, name):
    return next(item for item in report["requirements"]
                if item["requirement"] == name)


BASE = {"city": "Calgary", "days": 2, "budget_total": 500, "party_size": 2,
        "cuisines": ["international"], "allergies": ["peanut"]}
token = config.set_backend_override("local")

section("D score breakdown explains itself")
near = {"venue_id": "a", "rating": 4.5, "avg_meal_cost": 20.0,
        "lat": 51.045, "lon": -114.07}
far = {"venue_id": "b", "rating": 4.9, "avg_meal_cost": 20.0,
       "lat": 51.20, "lon": -114.30}
anchor = (51.0447, -114.0719)
near_score = score_breakdown(near, budget_remaining=100, party_size=2,
                             anchor=anchor, max_leg_minutes=25.0)
far_score = score_breakdown(far, budget_remaining=100, party_size=2,
                            anchor=anchor, max_leg_minutes=25.0)
check("the components are itemised",
      sorted(k for k in near_score if k in ("rating_points", "distance_penalty",
                                            "travel_minutes", "cost", "total")),
      ["cost", "distance_penalty", "rating_points", "total", "travel_minutes"])
check("a nearby 4.5 beats a distant 4.9", near_score["total"] > far_score["total"], True)
check("and the reason is the travel penalty, not the rating",
      far_score["rating_points"] > near_score["rating_points"]
      and far_score["distance_penalty"] > near_score["distance_penalty"], True)
check("the total still matches score_candidate",
      score_breakdown(near, budget_remaining=100, party_size=2)["total"], 45.0)
check("unaffordable is -inf, not a low score",
      score_breakdown(near, budget_remaining=1, party_size=2)["total"], float("-inf"))
check("and is flagged as such",
      score_breakdown(near, budget_remaining=1, party_size=2)["affordable"], False)

section("D backups come from the pool that was already there")
state = run_tier2(dict(BASE))
backups = state.meta["backups"]
check("every filled slot offers runners-up",
      len(backups), len({item["slot"] for item in state.itinerary}))
check("attractions get backups too (pool was hardcoded at 2)",
      all(entry["alternatives"] for entry in backups if ".attraction" in entry["slot"]),
      True)
chosen_ids = {item["venue_id"] for item in state.itinerary}
check("no backup is a venue already in the plan",
      any(alt["facts"]["venue_id"] in chosen_ids
          for entry in backups for alt in entry["alternatives"]), False)
check("backups carry search-level facts only - no details call was made",
      all("website" not in alt["facts"] and "phone" not in alt["facts"]
          for entry in backups for alt in entry["alternatives"]), True)
check("each alternative carries its score breakdown",
      all("rating_points" in alt["score"]
          for entry in backups for alt in entry["alternatives"]), True)
check("alternatives are ordered best first",
      all([alt["score"]["total"] for alt in entry["alternatives"]]
          == sorted((alt["score"]["total"] for alt in entry["alternatives"]),
                    reverse=True)
          for entry in backups), True)

section("D day summary")
summary = state.meta["day_summary"]
check("one row per planned day", [row["day"] for row in summary], [1, 2])
check("costs sum to the projected budget",
      round(sum(row["cost"] for row in summary), 2), state.budget["projected"])
check("travel matches the routes",
      [row["travel_minutes"] for row in summary],
      [route["totals"]["minutes"] for route in state.routes])

section("E the panel is honest about HOW each answer was reached")
request = {**BASE, "days": 1, "meals": ["lunch", "dinner"],
           "attractions_per_day": 0, "start_date": "2026-09-07",
           "min_rating": 4.4, "min_reviews": 50}
offline = run_tier2(dict(request))
report = verification.verify(request, offline.to_json())
check("every requirement carries a state",
      all(item["state"] in (verification.VERIFIED, verification.INFERRED,
                            verification.FAILED, verification.UNVERIFIABLE,
                            verification.NOT_REQUESTED)
          for item in report["requirements"]), True)
check("offline allergen filtering is VERIFIED, not inferred",
      by_name(report, "Allergen exclusion")["state"], verification.VERIFIED)
check("offline budget is VERIFIED against dataset costs",
      by_name(report, "Total within budget")["state"], verification.VERIFIED)
check("michelin is permanently unverifiable",
      by_name(report, "Michelin listing")["state"], verification.UNVERIFIABLE)
check("with a reason, not a blank",
      bool(by_name(report, "Michelin listing")["reason"]), True)
check("the headline counts what was checked",
      "verified" in report["summary"]["headline"], True)
check("failures sort to the top",
      report["requirements"][0]["state"] != verification.VERIFIED, True)

section("E requirements that were never asked for are marked, not faked")
plain = verification.verify({**BASE, "days": 1}, run_tier2({**BASE, "days": 1}).to_json())
check("an unset rating floor is not_requested",
      by_name(plain, "Minimum rating")["state"], verification.NOT_REQUESTED)
check("and is excluded from the checked count",
      plain["summary"]["checked"] < plain["summary"]["total"], True)

section("E no dates means hours are UNVERIFIABLE, never a silent pass")
undated = verification.verify({**BASE, "days": 1},
                              run_tier2({**BASE, "days": 1}).to_json())
hours = by_name(undated, "Open when visited")
check("hours without dates are unverifiable", hours["state"], verification.UNVERIFIABLE)
check("and say why", "dates" in (hours["reason"] or ""), True)

section("E a broken check cannot hide the rest")
salvaged = verification.verify({**BASE}, {"meta": {}, "itinerary": None})
check("a malformed state still produces a full report",
      len(salvaged["requirements"]), len(verification.CHECKS))
config._backend_override.reset(token)

section("E live: inference is never dressed up as verification")
token = config.set_backend_override("live")
live_request = {"city": "Lisbon", "days": 1, "budget_total": 300, "party_size": 2,
                "cuisines": ["portuguese"], "allergies": ["peanut"],
                "meals": ["lunch", "dinner"], "attractions_per_day": 0,
                "search_radius_km": 3.0, "min_rating": 4.5, "min_reviews": 500,
                "start_date": "2026-09-05"}
live = run_tier2(dict(live_request))
live_report = verification.verify(live_request, live.to_json())
allergen = by_name(live_report, "Allergen exclusion")
check("live allergen filtering is INFERRED, never verified",
      allergen["state"], verification.INFERRED)
check("and says plainly that it cannot confirm safety",
      "CANNOT confirm" in (allergen["reason"] or ""), True)
check("live budget is INFERRED from a price band",
      by_name(live_report, "Total within budget")["state"]
      in (verification.INFERRED, verification.FAILED), True)
check("live rating IS verified - it is real data",
      by_name(live_report, "Minimum rating")["state"], verification.VERIFIED)
check("verified requirements carry a fetch timestamp",
      bool(by_name(live_report, "Minimum rating")["fetched_at"]), True)
config._backend_override.reset(token)

section("G the hierarchy keeps every honesty signal it had")
from app import ui_components as ui  # noqa: E402
check("a chip exists for each real state",
      sorted(ui.STATE_CHIPS), ["failed", "inferred", "unverifiable", "verified"])
check("the full-sentence labels survive for the legend and the rows",
      all(state in ui.STATE_ICONS for state in ui.STATE_CHIPS), True)
check("inferred is still its own state, not folded into verified",
      ui.STATE_CHIPS["inferred"] != ui.STATE_CHIPS["verified"], True)
check("unverifiable still says a data source is missing",
      "No data source" in ui.STATE_ICONS["unverifiable"][1], True)

live_report = verification.verify(live_request, live.to_json())
check("the summary still counts every state the panel shows",
      all(state in live_report["summary"] for state in ui.STATE_CHIPS), True)
check("and the counts add up to what is displayed",
      sum(live_report["summary"][state] for state in ui.STATE_CHIPS)
      + live_report["summary"]["not_requested"],
      len(live_report["requirements"]))

print()
if fails:
    print(f"{len(fails)} FAILURE(S):")
    for f in fails:
        print("  -", f)
    raise SystemExit(1)
print("ALL PHASE D + E CHECKS PASSED")
