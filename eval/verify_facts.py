"""Phase C verification: verifiable filters, fact fields, and the facts/comment split."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import time

from src import config, enrich
from src.orchestrator import run_tier2
from src.request_model import TripRequest
from src.tools import cache, get_venue_details, is_open_at, search_restaurants

fails = []


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {label}: {got!r}")
    if not ok:
        fails.append(f"{label}: got {got!r} want {want!r}")


def section(title):
    print(f"\n=== {title} ===")


LOCAL = {"city": "Calgary", "days": 1, "budget_total": 400, "party_size": 2,
         "cuisines": ["international"], "allergies": [],
         "meals": ["lunch", "dinner"], "attractions_per_day": 0}

section("C0 cache hardening")
stats = cache.stats()
check("stats reports what the panel needs",
      sorted(k for k in stats if k in ("entries", "expired", "pinned",
                                       "oldest_hours", "ttl_hours")),
      ["entries", "expired", "oldest_hours", "pinned", "ttl_hours"])
cache.put("verify_normal", {"a": 1})
with cache.pin_writes():
    cache.put("verify_pinned", {"a": 1})
cache.put("verify_pinned", {"a": 2})   # an ordinary rewrite must not demote it
import sqlite3  # noqa: E402
con = sqlite3.connect(config.CACHE_DB_PATH)
check("warmed entries are pinned",
      con.execute("SELECT pinned FROM api_cache WHERE key='verify_pinned'").fetchone()[0], 1)
check("ordinary entries are not",
      con.execute("SELECT pinned FROM api_cache WHERE key='verify_normal'").fetchone()[0], 0)
check("WAL is on, so concurrent executors do not serialise",
      con.execute("PRAGMA journal_mode").fetchone()[0], "wal")
con.execute("DELETE FROM api_cache WHERE key LIKE 'verify_%'")
con.commit()
con.close()
check("pinned entries outlive ordinary ones",
      cache.PINNED_TTL_HOURS > cache.TTL_HOURS, True)
check("prune returns a count", isinstance(cache.prune(), int), True)

section("C1 quality gate is enforced, never quietly relaxed")
token = config.set_backend_override("local")
rows = search_restaurants("Calgary", "dinner", limit=50)
gated = search_restaurants("Calgary", "dinner", limit=50, min_rating=4.6,
                           min_reviews=200)
check("the gate narrows the pool", len(gated) < len(rows), True)
check("every survivor clears the bar",
      all(r["rating"] >= 4.6 and r["review_count"] >= 200 for r in gated), True)

ok_gate = run_tier2({**LOCAL, "min_rating": 4.5, "min_reviews": 100})
check("a satisfiable gate fills every slot", len(ok_gate.itinerary), 2)
check("and reports no shortfall", ok_gate.meta["quality_shortfall"], [])

impossible = run_tier2({**LOCAL, "min_rating": 4.99, "min_reviews": 99999})
check("an impossible gate is NOT met by relaxing it", impossible.itinerary, [])
check("it is reported instead of silently empty",
      len(impossible.meta["quality_shortfall"]), 2)
check("with a reason naming the threshold",
      "99999" in impossible.meta["quality_shortfall"][0]["detail"], True)

no_gate = run_tier2(dict(LOCAL))
check("no gate behaves exactly as before", len(no_gate.itinerary), 2)
check("and records no shortfall", no_gate.meta["quality_shortfall"], [])

section("C3 dish extraction is verified against its source, not trusted")
reviews = [{"text": "The grilled octopus was superb and the rice pudding fine."},
           {"text": "Service was slow but friendly."}]


class _Echo:
    """Stands in for the LLM so the guard can be tested without a network."""

    def __init__(self, payload):
        self.payload = payload

    def chat(self, **_):
        return {"content": self.payload}


# Each case needs its own venue name: the extraction is cached on
# (venue, review texts), so reusing one name would serve the first case's
# result to the others - which is exactly the caching behaviour we want in
# production, and a trap in a test.
honest = enrich.dishes_from_reviews(
    _Echo('{"dishes":[{"dish":"grilled octopus","review_index":0,'
          '"quote":"The grilled octopus was superb"}]}'), reviews, "Honest Cafe")
check("a dish that IS in the cited review survives",
      [d["dish"] for d in honest["dishes"]], ["grilled octopus"])

invented = enrich.dishes_from_reviews(
    _Echo('{"dishes":[{"dish":"truffle risotto","review_index":0,'
          '"quote":"The truffle risotto was superb"}]}'), reviews, "Invented Cafe")
check("a plausible invention is dropped", invented["dishes"], [])
check("and the drop is reported", "dropped" in (invented["note"] or ""), True)

miscited = enrich.dishes_from_reviews(
    _Echo('{"dishes":[{"dish":"grilled octopus","review_index":1,"quote":"x"}]}'),
    reviews, "Miscited Cafe")
check("a real dish cited to the WRONG review is dropped", miscited["dishes"], [])
check("no reviews means no dishes and a note",
      enrich.dishes_from_reviews(_Echo("{}"), [], "Empty Cafe")["dishes"], [])
check("no LLM is stated, not faked",
      "no LLM" in (enrich.dishes_from_reviews(None, reviews, "Offline Cafe")["note"] or ""), True)

section("C3 michelin is permanently unverifiable")
entry = enrich.enrich_stop({"slot": "day1.lunch"}, {"name": "X"}, {}, None)
check("michelin is listed as unverifiable", "michelin" in entry["unverifiable"], True)
check("with the reason stated",
      "no Michelin field" in entry["unverifiable"]["michelin"], True)
check("facts and comment are separate blocks",
      sorted(k for k in entry if k in ("facts", "comment", "unverifiable")),
      ["comment", "facts", "unverifiable"])

section("C2 fact fields carry provenance")
facts = enrich.venue_facts({"name": "X", "rating": 4.8, "review_count": 900,
                            "avg_meal_cost": 40.0, "source": "google_places"})
check("live cost is labelled an estimate, not a quote",
      facts["cost_per_person"]["basis"], "price_band_estimate")
check("offline cost is labelled a dataset value",
      enrich.venue_facts({"avg_meal_cost": 40.0,
                          "source": "local_dataset"})["cost_per_person"]["basis"],
      "dataset_value")
check("an unknown neighbourhood stays unknown",
      (facts["neighborhood"], facts["neighborhood_known"]), (None, False))
check("every fact block is timestamped", bool(facts["fetched_at"]), True)
check("booking advice with no contact facts says so",
      enrich.reservation_advice({})["grounded_in"], [])
check("and with a phone it cites the phone",
      enrich.reservation_advice({"phone": "123"})["grounded_in"], ["phone"])

section("C2 offline enrichment still works and stays offline")
enriched = run_tier2({**LOCAL, "start_date": "2026-09-07"})   # a Monday
check("every stop is enriched", len(enriched.meta["enrichment"]),
      len(enriched.itinerary))
check("offline stops are labelled offline",
      {e["facts"]["source"] for e in enriched.meta["enrichment"]}, {"local_dataset"})
check("no venue is scheduled on a day it is closed",
      [i for i in enriched.critic.get("issues", []) if i["type"] == "hours"], [])
config._backend_override.reset(token)

section("C2 live: hours are finally checked, and details stay affordable")
token = config.set_backend_override("live")
row = search_restaurants("Lisbon", "dinner", limit=1)[0]
details = get_venue_details(row["venue_id"])
check("live details carry the reviews already paid for",
      isinstance(details.get("reviews"), list), True)
check("live hours are readable now (was always None)",
      is_open_at(details, "sun", "19:00") in (True, False), True)
check("offline hours still read correctly",
      is_open_at(get_venue_details("r005"), "mon", "12:30"), False)

started = time.time()
live = run_tier2({"city": "Lisbon", "days": 1, "budget_total": 400,
                  "party_size": 2, "cuisines": ["portuguese"], "allergies": [],
                  "meals": ["lunch", "dinner"], "attractions_per_day": 0,
                  "search_radius_km": 3.0, "start_date": "2026-09-05",
                  "min_rating": 4.5, "min_reviews": 500})
elapsed = time.time() - started
print(f"    live run: {elapsed:.1f}s, {len(live.itinerary)} stops")
check("a live enriched run stays inside the latency budget",
      elapsed < config.LATENCY_BUDGET_S, True)
check("live stops are enriched", len(live.meta["enrichment"]), len(live.itinerary))
check("every live pick clears the stated gate",
      all(e["facts"]["rating"] >= 4.5 and e["facts"]["review_count"] >= 500
          for e in live.meta["enrichment"]), True)
config._backend_override.reset(token)

print()
if fails:
    print(f"{len(fails)} FAILURE(S):")
    for f in fails:
        print("  -", f)
    raise SystemExit(1)
print("ALL PHASE C CHECKS PASSED")
