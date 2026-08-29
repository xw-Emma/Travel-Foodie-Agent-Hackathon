"""Phase B verification: the description reader never picks venues, and never
lets a value through without checking it."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import config, verification
from src.agents import intent
from src.orchestrator import run_tier2

fails = []


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {label}: {got!r}")
    if not ok:
        fails.append(f"{label}: got {got!r} want {want!r}")


def section(title):
    print(f"\n=== {title} ===")


def rejected_fields(draft):
    return {r["field"] for r in draft["rejected"]}


class _Echo:
    """Stands in for the LLM so the guards can be tested without a network."""

    def __init__(self, payload):
        self.payload = payload

    def chat(self, **_):
        return {"content": self.payload}


token = config.set_backend_override("local")

section("B rule 1: it cannot name a venue")
draft = intent.validate({
    "city": "Calgary", "cuisines": ["italian"],
    "restaurants": ["Belcanto", "Time Out Market"],
    "venues": ["somewhere"], "itinerary": [{"lunch": "A Cevicheria"}],
}, "local")
check("venue keys never reach the form",
      set(draft["fields"]) & intent.VENUE_LIKE_KEYS, set())
check("all three are rejected by name",
      rejected_fields(draft) >= {"restaurants", "venues", "itinerary"}, True)
check("with a reason that says why",
      all("search tools choose venues" in r["reason"] for r in draft["rejected"]
          if r["field"] in intent.VENUE_LIKE_KEYS), True)
check("the legitimate fields still come through",
      sorted(draft["fields"]), ["city", "cuisines"])
check("the schema has no venue field at all",
      intent.ALLOWED_FIELDS & intent.VENUE_LIKE_KEYS, set())
check("an unknown key is dropped even if it is not venue-shaped",
      "surprise" in rejected_fields(intent.validate({"surprise": 1}, "local")), True)

section("B rule 2: values are re-derived, not trusted")
draft = intent.validate({
    "city": "Portugal", "cuisines": ["italian", "klingon"],
    "allergies": ["peanut", "glitter"], "meals": ["lunch", "brunch"],
    "transport_mode": "TELEPORT", "budget_basis": "vibes",
    "days": 99, "party_size": 0, "min_rating": 9, "start_date": "not-a-date",
}, "local")
# Offline, classify_city answers not_checked on purpose - a wrong "that is a
# country" warning is worse than none - so the country guard is asserted below
# against the live backend, where the classification is real.
check("offline does not guess at the city", "city" in rejected_fields(draft), False)
check("unknown cuisines are dropped, known ones kept",
      draft["fields"].get("cuisines"), ["italian"])
check("an invented allergen never becomes a filter",
      draft["fields"].get("allergies"), ["peanut"])
check("an unknown meal slot is dropped", draft["fields"].get("meals"), ["lunch"])
check("an unsupported transport mode is dropped",
      "transport_mode" in draft["fields"], False)
check("a nonsense budget basis is dropped",
      "budget_basis" in draft["fields"], False)
check("out-of-range numbers are rejected, not clamped",
      {"days", "party_size", "min_rating"} <= rejected_fields(draft), True)
check("a malformed date is rejected", "start_date" in rejected_fields(draft), True)
check("EVERY drop is reported", len(draft["rejected"]) >= 9, True)

section("B a valid city passes through")
good = intent.validate({"city": "Calgary", "days": 2, "party_size": 4,
                        "min_rating": 4.5, "start_date": "2026-09-05"}, "local")
check("the city is kept", good["fields"].get("city"), "Calgary")
check("in-range numbers are kept",
      (good["fields"].get("days"), good["fields"].get("party_size"),
       good["fields"].get("min_rating")), (2, 4, 4.5))
check("a real date is parsed", str(good["fields"].get("start_date")), "2026-09-05")
check("nothing was rejected", good["rejected"], [])

section("B extract never raises")
check("empty text is handled", intent.extract(None, "", "local")["ok"], False)
check("no client is stated, not faked",
      "No LLM" in intent.extract(None, "a trip", "local")["notes"][0], True)


class _Boom:
    def chat(self, **_):
        raise RuntimeError("gateway down")


check("an LLM failure degrades to an empty draft",
      intent.extract(_Boom(), "a trip", "local")["fields"], {})
check("and says so", "Could not read" in
      intent.extract(_Boom(), "a trip", "local")["notes"][0], True)
check("unparseable JSON yields no fields",
      intent.extract(_Echo("not json at all"), "a trip", "local")["fields"], {})

section("B unmappable criteria reach the panel instead of vanishing")
criteria = ["listed in the Michelin Guide", "popular with locals", "no chains"]
request = {"city": "Calgary", "days": 1, "budget_total": 400, "party_size": 2,
           "cuisines": ["international"], "allergies": [],
           "meals": ["lunch", "dinner"], "attractions_per_day": 0,
           "extra_criteria": criteria}
state = run_tier2(dict(request))
report = verification.verify(request, state.to_json())
carried = [item for item in report["requirements"]
           if any(c[:20] in str(item["requirement"]) for c in criteria)]
check("every carried criterion is listed", len(carried), len(criteria))
check("each as unverifiable, never a silent pass",
      {item["state"] for item in carried}, {verification.UNVERIFIABLE})
check("with a reason", all(item["reason"] for item in carried), True)
check("a plan with no criteria adds no rows",
      len(verification.verify({k: v for k, v in request.items()
                               if k != "extra_criteria"},
                              state.to_json())["requirements"]),
      len(report["requirements"]) - len(criteria))

section("B the quality gate judges restaurants, not landmarks")
gated = {"city": "Calgary", "days": 1, "budget_total": 400, "party_size": 2,
         "cuisines": ["international"], "allergies": [],
         "meals": ["lunch", "dinner"], "attractions_per_day": 1,
         "min_rating": 4.5, "min_reviews": 100}
gated_state = run_tier2(dict(gated))
gated_report = verification.verify(gated, gated_state.to_json())
rating = next(item for item in gated_report["requirements"]
              if item["requirement"] == "Minimum rating")
check("an attraction cannot fail the restaurant rating gate",
      rating["state"], verification.VERIFIED)
check("and the wording says what it counted",
      "restaurants" in str(rating["actual"]), True)
config._backend_override.reset(token)

section("B live: a country in the city box is caught")
token = config.set_backend_override("live")
live_draft = intent.validate({"city": "Portugal", "cuisines": ["portuguese"]},
                             "live")
check("a country is rejected", "city" in rejected_fields(live_draft), True)
check("and says to name a city instead",
      any("not a city" in r["reason"] for r in live_draft["rejected"]
          if r["field"] == "city"), True)
check("a real city passes",
      intent.validate({"city": "Lisbon"}, "live")["fields"].get("city"), "Lisbon")
config._backend_override.reset(token)

print()
if fails:
    print(f"{len(fails)} FAILURE(S):")
    for f in fails:
        print("  -", f)
    raise SystemExit(1)
print("ALL PHASE B CHECKS PASSED")
