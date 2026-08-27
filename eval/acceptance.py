"""Lightweight acceptance checks for graded scenarios (run offline against local backend).

Run from the kit root:
  FOODIE_DATA_BACKEND=local python eval/acceptance.py
"""
from __future__ import annotations

import json
import sys
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.orchestrator import run_tier2  # noqa: E402
from src import config  # noqa: E402
from src.state import slot_ids  # noqa: E402
from src.tools import get_venue_details  # noqa: E402

SCENARIOS = Path(__file__).with_name("scenarios.json")


def check_no_allergen_leaks(st, request) -> list[str]:
    fails = []
    allergies = request.get("allergies") or []
    for slot, cands in st.candidates.items():
        for c in cands:
            flags = c.get("dietary_flags") or {}
            for a in allergies:
                if flags.get(f"{a}_risk"):
                    fails.append(f"{slot}:{c.get('name')}:{a}_risk")
    return fails


def check_budget_ok(st, request) -> list[str]:
    if config.DATA_BACKEND != "local":
        return [] if isinstance(st.budget.get("projected"), (int, float)) else [
            "live budget has no numeric projected value"
        ]
    if st.budget.get("status") == "exceeded":
        return [f"budget exceeded: {st.budget}"]
    return []


def check_has_meals(st, request) -> list[str]:
    days = int(request.get("days", 2))
    expected = days * 3
    meals = [it for it in st.itinerary if any(m in it["slot"] for m in ("breakfast", "lunch", "dinner"))]
    if len(meals) < expected:
        return [f"expected {expected} meals, got {len(meals)}"]
    return []


def check_party_size(st, request) -> list[str]:
    if config.DATA_BACKEND != "local":
        return []
    party_size = int(request.get("party_size", 1))
    fails = []
    for item in st.itinerary:
        details = get_venue_details(item["venue_id"])
        per_person = details.get("avg_meal_cost", details.get("cost", 0))
        expected = round(float(per_person or 0) * party_size, 2)
        if round(float(item.get("cost", 0)), 2) != expected:
            fails.append(f"{item['slot']}: cost={item.get('cost')} expected={expected}")
    return fails


def check_no_duplicate_venues(st, request) -> list[str]:
    ids = [item.get("venue_id") for item in st.itinerary if item.get("venue_id")]
    duplicates = sorted({venue_id for venue_id in ids if ids.count(venue_id) > 1})
    return [f"duplicate venue: {venue_id}" for venue_id in duplicates]


def check_opening_hours(st, request) -> list[str]:
    if config.DATA_BACKEND != "local":
        return []
    fails = []
    for item in st.itinerary:
        details = get_venue_details(item["venue_id"])
        if item["slot"].split(".")[-1].startswith("attraction"):
            if not details.get("hours"):
                fails.append(f"{item['slot']}: missing opening hours")
            continue
        meal = item["slot"].split(".")[-1]
        meal_types = str(details.get("meal_types") or "").split(";")
        if meal not in meal_types:
            fails.append(f"{item['slot']}: {item['name']} does not serve {meal}")
        if not details.get("hours"):
            fails.append(f"{item['slot']}: missing opening hours")
    return fails


def check_attraction_trap(st, request) -> list[str]:
    if config.DATA_BACKEND != "local":
        return []
    return ["closed Monday attraction trap selected"] if any(
        item.get("venue_id") == "a002" for item in st.itinerary
    ) else []


def check_routes(st, request) -> list[str]:
    fails = []
    max_walk = float(request.get("max_walk_km", 2.0))
    travel_issues = {issue.get("slot") for issue in st.critic.get("issues", [])
                     if issue.get("type") == "travel"}
    for route in st.routes:
        if "km" not in route or "minutes" not in route:
            fails.append(f"invalid route result: {route}")
        if route.get("km", 0) > max_walk:
            target = next((item["slot"] for item in st.itinerary
                           if item.get("name") == route.get("to")), None)
            if target not in travel_issues:
                fails.append(f"missing travel Critic issue for {target}: {route}")
    return fails


def check_valid_slots(st, request) -> list[str]:
    days = int(request.get("days", 2))
    valid = set(slot_ids(days, attractions_per_day=1))
    actual = {item.get("slot") for item in st.itinerary}
    actual.update(issue.get("slot") for issue in st.critic.get("issues", []))
    return [f"invalid slot: {slot}" for slot in sorted(actual - valid) if slot]


def check_critic_bound(st, request) -> list[str]:
    critic_count = sum(1 for entry in st.trace if entry.get("agent") == "critic")
    iteration = int(st.critic.get("iteration", 0))
    return ([f"critic calls={critic_count}, limit={config.CRITIC_MAX_ITERATIONS}"]
            if critic_count > config.CRITIC_MAX_ITERATIONS else []) + (
                [f"critic iteration={iteration}"]
                if iteration > config.CRITIC_MAX_ITERATIONS else [])


def check_real_backend(st, request) -> list[str]:
    backends = st.meta.get("tool_backends") or {}
    if config.DATA_BACKEND == "local":
        expected = "local_dataset"
        return [f"restaurants backend={backends.get('restaurants')} expected={expected}"] \
            if backends.get("restaurants") != expected else []
    required = {"restaurants": "google_places", "attractions": "google_places",
                "travel": "google_routes"}
    return [f"{key} backend={backends.get(key)} expected={value}"
            for key, value in required.items() if backends.get(key) != value]


def check_llm_calls(st, request) -> list[str]:
    calls = int(st.meta.get("llm_calls", -1))
    if config.DATA_BACKEND == "local":
        return [f"local mode made {calls} LLM calls"] if calls != 0 else []
    return ["real backend made no LLM calls"] if calls <= 0 else []


def check_elapsed(st, request) -> list[str]:
    elapsed = float(st.meta.get("elapsed_s", 0))
    return [f"elapsed={elapsed}s exceeds {config.LATENCY_BUDGET_S}s"] \
        if elapsed >= config.LATENCY_BUDGET_S else []


CHECKS = {
    "no_allergen_leaks": check_no_allergen_leaks,
    "budget_ok": check_budget_ok,
    "has_meals": check_has_meals,
    "party_size": check_party_size,
    "no_duplicate_venues": check_no_duplicate_venues,
    "opening_hours": check_opening_hours,
    "attraction_trap": check_attraction_trap,
    "route_distance": check_routes,
    "critic_revision_bound": check_critic_bound,
    "valid_slot_ids": check_valid_slots,
    "real_backend": check_real_backend,
    "llm_call_count": check_llm_calls,
    "elapsed_under_60": check_elapsed,
}

LIVE_ONLY_CHECKS = {"no_allergen_leaks", "has_meals", "no_duplicate_venues",
                    "route_distance", "critic_revision_bound", "valid_slot_ids",
                    "real_backend", "llm_call_count", "elapsed_under_60"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true",
                        help="run against live APIs instead of the local gate")
    args = parser.parse_args()
    config.DATA_BACKEND = "auto" if args.live else "local"
    if args.live and not config.LIVE_DATA_AVAILABLE:
        print("SKIP: --live requires GOOGLE_MAPS_API_KEY")
        return
    scenarios = json.loads(SCENARIOS.read_text())
    all_ok = True
    for sc in scenarios:
        st = run_tier2(sc["request"])
        print(f"\n== {sc['id']} ==")
        for name in sc.get("checks", []):
            fails = CHECKS[name](st, sc["request"])
            status = "PASS" if not fails else "FAIL"
            if fails:
                all_ok = False
            print(f"  {status} {name} {fails or ''}")
        follow_up_checks = ("party_size", "no_duplicate_venues", "opening_hours",
                     "attraction_trap", "route_distance", "critic_revision_bound",
                     "valid_slot_ids", "real_backend", "llm_call_count",
                     "elapsed_under_60")
        if args.live:
            follow_up_checks = tuple(name for name in follow_up_checks
                                     if name in LIVE_ONLY_CHECKS)
        for name in follow_up_checks:
            fails = CHECKS[name](st, sc["request"])
            status = "PASS" if not fails else "FAIL"
            if fails:
                all_ok = False
            print(f"  {status} {name} {fails or ''}")
        print(f"  elapsed={st.meta['elapsed_s']}s budget={st.budget['status']} "
              f"llm_calls={st.meta.get('llm_calls')}")
    raise SystemExit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
