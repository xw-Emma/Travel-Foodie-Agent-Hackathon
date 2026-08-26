"""
Orchestrator — tiered Live-API edition.

  run_tier1 : sequential Planner -> Restaurant(per meal) -> Budget -> Formatter
  run_tier2 : parallel executors + Attraction + Route + Critic revision loop

MOCK MODE: no FUELIX_API_KEY -> deterministic stand-ins (fully offline).
DATA MODE: FOODIE_DATA_BACKEND=auto|live|local (see src/config.py).
"""
from __future__ import annotations

import time

from . import config
from .state import TripState, MEALS, is_valid_slot
from .tools import (TOOL_IMPLS, search_restaurants, get_venue_details,
                    search_attractions, estimate_travel, check_budget,
                    last_backend_report)


# ------------------------------------------------------- Critic slot guard
def validate_critic_output(critic_json: dict, days: int = 2) -> tuple[bool, list[str]]:
    """Reject off-vocabulary slots BEFORE they reach the Planner."""
    bad = [iss.get("slot") for iss in critic_json.get("issues", [])
           if not is_valid_slot(str(iss.get("slot", "")), days=days)]
    return (len(bad) == 0, bad)


# ------------------------------------------------------- TIER 1
def run_tier1(request: dict) -> TripState:
    """
    Sequential agentic pipeline. With FUELIX_API_KEY set, teams replace the
    mock Planner / Restaurant picker / Formatter with real Fuel iX calls
    (see prompts/ and BUILD guide). Allergen exclusion is enforced at the
    TOOL layer regardless.
    """
    t0 = time.time()
    st = TripState(request=request)
    days = int(request.get("days", 2))
    city = request["city"]
    party_size = max(1, int(request.get("party_size", 1)))

    # 1) PLAN
    per_day = float(request["budget_total"]) / days
    st.plan = {
        "days": days,
        "budget_allocation": {f"day{d}": round(per_day, 2) for d in range(1, days + 1)},
        "constraints": {
            "budget_total": request["budget_total"],
            "allergies": request.get("allergies", []),
            "cuisines": request.get("cuisines", []),
        },
    }
    st.log("planner", f"Allocated ${per_day:.0f}/day across {days} days (mock or LLM)")

    # 2) EXECUTE restaurants — allergen flags excluded IN CODE
    exclude = [f"{a}_risk" for a in request.get("allergies", [])]
    cuisine = (request.get("cuisines") or [None])[0]
    chosen = []
    used_venue_ids = set()
    for d in range(1, days + 1):
        for m in MEALS:
            slot = f"day{d}.{m}"
            cands = search_restaurants(
                city=city, meal=m, cuisine=cuisine,
                price_level_max=2, exclude_flags=exclude, limit=3)
            st.candidates[slot] = cands
            pick = next((candidate for candidate in cands
                         if candidate["venue_id"] not in used_venue_ids), None)
            if pick:
                used_venue_ids.add(pick["venue_id"])
                chosen.append({
                    "slot": slot,
                    "venue_id": pick["venue_id"],
                    "name": pick["name"],
                    "cost": round(pick.get("avg_meal_cost", 0) * party_size, 2),
                    "lat": pick.get("lat"), "lon": pick.get("lon"),
                    "source": pick.get("source"),
                    "why": f"Top-rated {pick.get('cuisine', '')} match under constraints",
                })
                st.log("restaurant", f"{slot}: chose {pick['name']} via {pick.get('source')}")

    # 3) BUDGET — pure Python
    st.budget = check_budget(chosen, float(request["budget_total"]))
    st.log("budget", f"status={st.budget['status']} projected={st.budget['projected']}")

    # 4) FORMAT (mock: itinerary = chosen list; real mode = LLM formatter)
    st.itinerary = chosen
    st.meta = {
        "tier": 1,
        "elapsed_s": round(time.time() - t0, 2),
        "mock_llm": config.MOCK_MODE,
        "data_backend": config.DATA_BACKEND,
        "tool_backends": last_backend_report(),
        "latency_budget_s": config.LATENCY_BUDGET_S,
    }
    return st


# ------------------------------------------------------- TIER 2
def run_tier2(request: dict) -> TripState:
    """
    Strong-team ceiling. This starter ships a WORKING skeleton:
      - Attraction picks (1 per day)
      - Route legs between consecutive stops
      - One Critic pass with slot-guard demo hooks

    Teams should: (a) parallelize with asyncio.gather, (b) replace mock Critic
    with a real Fuel iX call + revision loop (max 2), (c) add Streamlit UI.
    """
    st = run_tier1(request)
    days = int(request.get("days", 2))
    city = request["city"]
    party_size = max(1, int(request.get("party_size", 1)))

    # Attractions
    for d in range(1, days + 1):
        slot = f"day{d}.attraction1"
        attrs = search_attractions(city, limit=2)
        st.candidates[slot] = attrs
        if attrs:
            a = attrs[0]
            st.itinerary.append({
                "slot": slot, "venue_id": a["venue_id"], "name": a["name"],
                "cost": round(a.get("cost", 0) * party_size, 2),
                "lat": a.get("lat"), "lon": a.get("lon"),
                "source": a.get("source"),
                "why": "Top attraction near the food plan",
            })
            st.log("attraction", f"{slot}: {a['name']}")

    # Routes between consecutive geo-tagged stops
    geo = [it for it in st.itinerary if it.get("lat") is not None and it.get("lon") is not None]
    legs = []
    for i in range(len(geo) - 1):
        a, b = geo[i], geo[i + 1]
        leg = estimate_travel(a["lat"], a["lon"], b["lat"], b["lon"], mode="walk")
        leg["from"] = a["name"]
        leg["to"] = b["name"]
        legs.append(leg)
    st.routes = legs
    st.log("route", f"{len(legs)} travel legs computed")

    # Critic (mock pass — teams replace with LLM + revision loop)
    issues = []
    # Demo: if any chosen venue details say closed Saturday and trip implies weekend
    for it in st.itinerary:
        if not it.get("venue_id"):
            continue
        details = get_venue_details(it["venue_id"])
        hours = details.get("hours") or {}
        if isinstance(hours, dict) and hours.get("sat", {}).get("open") is None and "sat" in hours:
            # local-dataset closed-Saturday trap
            if details.get("source") == "local_dataset":
                issues.append({"slot": it["slot"], "type": "hours",
                               "detail": f"{it['name']} closed Saturday"})
    critic = {"verdict": "revise" if issues else "approved", "issues": issues, "iteration": 1}
    ok, bad = validate_critic_output(critic, days=days)
    if not ok:
        st.log("critic", f"Rejected malformed slots {bad}; would re-ask Critic")
        critic = {"verdict": "approved", "issues": [], "iteration": 1,
                  "note": "slot-guard rejected bad IDs"}
    st.critic = critic
    st.log("critic", f"verdict={critic['verdict']} issues={len(critic['issues'])}")

    # Recompute budget including attractions
    st.budget = check_budget(st.itinerary, float(request["budget_total"]))
    st.meta["tier"] = 2
    st.meta["tool_backends"] = last_backend_report()
    return st


# ------------------------------------------------------- demo entry
if __name__ == "__main__":
    S1 = {"city": "Calgary", "days": 2, "budget_total": 500, "party_size": 2,
          "cuisines": ["international"], "allergies": ["peanut"], "max_walk_km": 2.0}

    print("=== TIER 1 (S1) ===")
    st1 = run_tier1(S1)
    print(f"tier={st1.meta['tier']}  elapsed={st1.meta['elapsed_s']}s  "
          f"mock_llm={st1.meta['mock_llm']}  backend={st1.meta['data_backend']}")
    print(f"budget={st1.budget}")
    leaked = []
    for slot, cands in st1.candidates.items():
        for c in cands:
            flags = c.get("dietary_flags") or {}
            if flags.get("peanut_risk"):
                leaked.append((slot, c["name"]))
    print(f"allergen leaks in candidates (MUST be empty): {leaked}")
    print("chosen:")
    for it in st1.itinerary:
        print(f"  {it['slot']:18s} {it['name'][:28]:28s} ${it['cost']}  [{it.get('source')}]")

    print("\n=== TIER 2 hooks ===")
    st2 = run_tier2(S1)
    print(f"tier={st2.meta['tier']}  stops={len(st2.itinerary)}  legs={len(st2.routes)}")
    print(f"critic={st2.critic}")
    print(f"tool_backends={st2.meta['tool_backends']}")

    print("\n=== CRITIC SLOT-GUARD ===")
    good = {"verdict": "revise", "issues": [{"slot": "day2.dinner", "type": "hours"}]}
    bad = {"verdict": "revise", "issues": [{"slot": "dinner day 2", "type": "hours"}]}
    print("good ->", validate_critic_output(good))
    print("bad  ->", validate_critic_output(bad), "(re-ask Critic, do NOT re-plan)")
