"""
Orchestrator — tiered Live-API edition.

  run_tier1 : sequential Planner -> Restaurant(per meal) -> Budget -> Formatter
  run_tier2 : parallel executors + Attraction + Route + Critic revision loop

MOCK MODE: no FUELIX_API_KEY -> deterministic stand-ins (fully offline).
DATA MODE: FOODIE_DATA_BACKEND=auto|live|local (see src/config.py).
"""
from __future__ import annotations

import json
import time

from . import config
from .fuelix_client import FuelixClient, parse_json_reply, run_tool_loop
from .state import TripState, MEALS, TOOL_SCHEMAS, is_valid_slot, slot_ids
from .tools import (TOOL_IMPLS, search_restaurants, get_venue_details,
                    search_attractions, estimate_travel, check_budget,
                    last_backend_report)


# ------------------------------------------------------- Critic slot guard
def validate_critic_output(critic_json: dict, days: int = 2) -> tuple[bool, list[str]]:
    """Reject off-vocabulary slots BEFORE they reach the Planner."""
    bad = [iss.get("slot") for iss in critic_json.get("issues", [])
           if not is_valid_slot(str(iss.get("slot", "")), days=days)]
    return (len(bad) == 0, bad)


def _budget_per_person(request: dict, days: int) -> float:
    party_size = max(1, int(request.get("party_size", 1)))
    return float(request["budget_total"]) / days / len(MEALS) / party_size


def _plan_with_llm(client: FuelixClient, request: dict) -> dict:
    days = int(request.get("days", 2))
    valid_slots = slot_ids(days, attractions_per_day=0)
    system = (config.PROMPTS_DIR / "planner.md").read_text(encoding="utf-8")
    user = (
        "Split this request into exactly one restaurant task for every valid slot. "
        "Return STRICT JSON only. Do not select or name any venue.\n"
        f"Valid slot IDs (use these EXACTLY): {valid_slots}\n"
        f"Request: {json.dumps(request, sort_keys=True)}\n"
        "Each task must contain: agent='restaurant', slot, meal, area_hint, "
        "budget_per_person, and constraints with allergies and cuisines. "
        "Schema: {\"days\": int, \"meals_per_day\": 3, "
        "\"budget_allocation\": {}, \"tasks\": [], \"constraints\": {}}"
    )
    message = client.chat(
        model=config.MODEL_ROUTING["planner"], system=system, user=user,
        temperature=0.1, max_tokens=2200)
    plan = parse_json_reply(message.get("content", ""))
    tasks = plan.get("tasks") or []
    normalized = []
    for task in tasks:
        slot = str(task.get("slot", ""))
        if slot not in valid_slots:
            day = task.get("day")
            meal = task.get("meal")
            if day and meal:
                slot = f"day{int(day)}.{meal}"
        if slot not in valid_slots:
            continue
        normalized.append({
            "agent": "restaurant",
            "slot": slot,
            "meal": task.get("meal") or slot.rsplit(".", 1)[1],
            "area_hint": task.get("area_hint") or "",
            "budget_per_person": float(
                task.get("budget_per_person", _budget_per_person(request, days))),
            "constraints": {
                "allergies": request.get("allergies", []),
                "cuisines": request.get("cuisines", []),
            },
        })
    by_slot = {task["slot"]: task for task in normalized}
    if set(by_slot) != set(valid_slots):
        missing = sorted(set(valid_slots) - set(by_slot))
        raise ValueError(f"Planner did not provide every valid slot: {missing}")
    plan["tasks"] = [by_slot[slot] for slot in valid_slots]
    plan["days"] = days
    return plan


def _max_price_level(budget_per_person: float) -> int:
    if budget_per_person < 45:
        return 2
    if budget_per_person < 70:
        return 3
    return 4


def _pick_restaurant_with_tool_loop(
    client: FuelixClient, task: dict, request: dict, used_venue_ids: set[str]
) -> tuple[dict, list[dict]]:
    allergies = task["constraints"].get("allergies", [])
    exclude = [f"{allergy}_risk" for allergy in allergies]
    cuisines = task["constraints"].get("cuisines") or []
    cuisine = cuisines[0] if cuisines else None
    observed_candidates: list[dict] = []
    tool_impls = dict(TOOL_IMPLS)

    def search_for_task(**kwargs):
        kwargs.update({
            "city": request["city"],
            "meal": task["meal"],
            "area": task.get("area_hint") or None,
            "cuisine": cuisine,
            "price_level_max": _max_price_level(task["budget_per_person"]),
            "exclude_flags": exclude,
            "limit": max(int(kwargs.get("limit") or 0), 8),
        })
        rows = search_restaurants(**kwargs)
        observed_candidates.extend(rows)
        return rows

    tool_impls["search_restaurants"] = search_for_task

    system = (config.PROMPTS_DIR / "restaurant.md").read_text(encoding="utf-8")
    user = (
        f"City: {request['city']}. Slot: {task['slot']} ({task['meal']}).\n"
        f"Budget per person: ${task['budget_per_person']:.2f}.\n"
        f"Cuisine: {cuisine or 'any'}. Allergies: {allergies}.\n"
        "Call search_restaurants with these constraints, then choose ONE venue "
        "from the tool response. Return STRICT JSON as an array with exactly one "
        "object: [{\"venue_id\": str, \"name\": str, \"why_recommended\": str}]. "
        "Never invent a venue or facts. Call search_restaurants exactly once; "
        "do not call any other tool in this Tier 1 task."
    )
    result = run_tool_loop(
        client, config.MODEL_ROUTING["restaurant"], system, user,
        tools=TOOL_SCHEMAS, tool_impls=tool_impls, max_rounds=3)
    candidates = observed_candidates
    if not candidates and task.get("area_hint"):
        candidates = search_restaurants(
            city=request["city"], meal=task["meal"], area=None,
            cuisine=cuisine, price_level_max=_max_price_level(task["budget_per_person"]),
            exclude_flags=exclude, limit=20)
    available = [item for item in candidates
                 if item["venue_id"] not in used_venue_ids]
    if not available:
        raise ValueError(f"No unused candidates for {task['slot']}")
    selections = result if isinstance(result, list) else [result]
    selection = selections[0] if selections else {}
    candidate_by_id = {item["venue_id"]: item for item in available}
    selected = candidate_by_id.get(selection.get("venue_id"))
    if selected is None:
        selected = available[0]
        why = "Selected from the verified tool results after an off-list model response."
    else:
        why = selection.get("why_recommended") or selection.get("why") or (
            f"Selected by the restaurant executor from verified {selected['cuisine']} results."
        )
    return {**selected, "why": why}, candidates


def _format_with_llm(client: FuelixClient, st: TripState) -> str:
    system = (config.PROMPTS_DIR / "formatter.md").read_text(encoding="utf-8")
    user = (
        "Compose the final itinerary from this verified state. Do not add, remove, "
        "or alter venues, costs, constraints, or facts. Return concise printable text.\n"
        f"{json.dumps({'plan': st.plan, 'itinerary': st.itinerary, 'budget': st.budget}, default=str)}"
    )
    message = client.chat(
        model=config.MODEL_ROUTING["formatter"], system=system, user=user,
        temperature=0.2, max_tokens=1600)
    return message.get("content", "")


def _execute_restaurant_batch(
    client: FuelixClient, tasks: list[dict], request: dict
) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    """Use one bounded tool loop for all independent restaurant tasks."""
    observed: dict[str, list[dict]] = {}
    task_by_slot = {task["slot"]: task for task in tasks}
    tool_impls = dict(TOOL_IMPLS)

    def search_batch(tasks: list[dict]):
        result = {}
        for request_task in tasks:
            slot = str(request_task.get("slot", ""))
            task = task_by_slot.get(slot)
            if task is None:
                continue
            allergies = task["constraints"].get("allergies", [])
            cuisine_values = task["constraints"].get("cuisines") or []
            rows = search_restaurants(
                city=request["city"], meal=task["meal"],
                area=task.get("area_hint") or None,
                cuisine=cuisine_values[0] if cuisine_values else None,
                price_level_max=_max_price_level(task["budget_per_person"]),
                exclude_flags=[f"{allergy}_risk" for allergy in allergies],
                limit=5,
            )
            observed[slot] = rows
            result[slot] = rows
        return result

    tool_impls["search_restaurants_batch"] = search_batch
    batch_tools = [{"type": "function", "function": {
        "name": "search_restaurants_batch",
        "description": "Search verified restaurants for every requested meal slot.",
        "parameters": {"type": "object", "properties": {
            "tasks": {"type": "array", "items": {"type": "object", "properties": {
                "slot": {"type": "string", "enum": list(task_by_slot)}},
                "required": ["slot"]}}},
            "required": ["tasks"]}}}]
    system = (config.PROMPTS_DIR / "restaurant.md").read_text(encoding="utf-8")
    task_lines = []
    for task in tasks:
        task_lines.append(
            f"{task['slot']}: meal={task['meal']}, "
            f"budget_per_person={task['budget_per_person']:.2f}, "
            f"area={task.get('area_hint') or 'any'}"
        )
    user = (
        "Complete every restaurant task below. Call search_restaurants_batch "
        "exactly once with every slot ID in its tasks argument. Then "
        "return STRICT JSON only as an array with one object per slot: "
        "[{\"slot\": str, \"venue_id\": str, \"why_recommended\": str}]. "
        "Choose only from the matching tool response; never invent venues.\n"
        + "\n".join(task_lines)
    )
    result = run_tool_loop(
        client, config.MODEL_ROUTING["restaurant"], system, user,
        tools=batch_tools, tool_impls=tool_impls, max_rounds=3)
    selections = result if isinstance(result, list) else [result]
    selected_by_slot: dict[str, dict] = {}
    used: set[str] = set()
    for selection in selections:
        slot = str(selection.get("slot", ""))
        candidates = observed.get(slot, [])
        candidate = next((item for item in candidates
                          if item["venue_id"] == selection.get("venue_id")
                          and item["venue_id"] not in used), None)
        if candidate is None:
            candidate = next((item for item in candidates
                              if item["venue_id"] not in used), None)
        if candidate is not None:
            selected_by_slot[slot] = {
                **candidate,
                "why": selection.get("why_recommended") or
                "Selected by the restaurant executor from verified tool results.",
            }
            used.add(candidate["venue_id"])
    for task in tasks:
        if task["slot"] in selected_by_slot:
            continue
        candidates = observed.get(task["slot"], [])
        candidate = next((item for item in candidates
                          if item["venue_id"] not in used), None)
        if candidate is not None:
            selected_by_slot[task["slot"]] = {
                **candidate,
                "why": "Selected as a verified fallback from tool results.",
            }
            used.add(candidate["venue_id"])
    return selected_by_slot, observed


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
    party_size = max(1, int(request.get("party_size", 1)))

    if config.MOCK_MODE:
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
        st.log("planner", f"Allocated ${per_day:.0f}/day across {days} days (mock)")
        exclude = [f"{a}_risk" for a in request.get("allergies", [])]
        cuisine = (request.get("cuisines") or [None])[0]
        tasks = [{"slot": f"day{d}.{m}", "meal": m,
                  "area_hint": "", "budget_per_person": _budget_per_person(request, days),
                  "constraints": {"allergies": request.get("allergies", []),
                                  "cuisines": request.get("cuisines", [])}}
                 for d in range(1, days + 1) for m in MEALS]
        chosen = []
        used_venue_ids = set()
        for task in tasks:
            cands = search_restaurants(
                city=request["city"], meal=task["meal"], cuisine=cuisine,
                price_level_max=2, exclude_flags=exclude, limit=20)
            st.candidates[task["slot"]] = cands
            pick = next((candidate for candidate in cands
                         if candidate["venue_id"] not in used_venue_ids), None)
            if pick:
                used_venue_ids.add(pick["venue_id"])
                chosen.append({
                    "slot": task["slot"], "venue_id": pick["venue_id"],
                    "name": pick["name"],
                    "cost": round(pick.get("avg_meal_cost", 0) * party_size, 2),
                    "lat": pick.get("lat"), "lon": pick.get("lon"),
                    "source": pick.get("source"),
                    "why": f"Top-rated {pick.get('cuisine', '')} match under constraints",
                })
                st.log("restaurant", f"{task['slot']}: chose {pick['name']} via {pick.get('source')}")
    else:
        client = FuelixClient(timeout=30, max_retries=1)
        st.plan = _plan_with_llm(client, request)
        st.log("planner", f"Created {len(st.plan['tasks'])} validated restaurant tasks")
        selected_by_slot, observed = _execute_restaurant_batch(
            client, st.plan["tasks"], request)
        chosen = []
        for task in st.plan["tasks"]:
            pick = selected_by_slot.get(task["slot"])
            candidates = observed.get(task["slot"], [])
            st.candidates[task["slot"]] = candidates
            if pick is None:
                raise ValueError(f"No verified candidate for {task['slot']}")
            chosen.append({
                "slot": task["slot"], "venue_id": pick["venue_id"],
                "name": pick["name"],
                "cost": round(pick.get("avg_meal_cost", 0) * party_size, 2),
                "lat": pick.get("lat"), "lon": pick.get("lon"),
                "source": pick.get("source"), "why": pick["why"],
            })
            st.log("restaurant", f"{task['slot']}: chose {pick['name']} via {pick.get('source')}")

    # 3) BUDGET — pure Python
    st.budget = check_budget(chosen, float(request["budget_total"]))
    st.log("budget", f"status={st.budget['status']} projected={st.budget['projected']}")

    # 4) FORMAT
    st.itinerary = chosen
    formatted = ""
    telemetry = {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0}
    if not config.MOCK_MODE:
        formatted = _format_with_llm(client, st)
        telemetry = dict(client.telemetry)
        st.log("formatter", "Formatted the verified itinerary with Fuel iX")
    st.meta = {
        "tier": 1,
        "elapsed_s": round(time.time() - t0, 2),
        "mock_llm": config.MOCK_MODE,
        "data_backend": config.DATA_BACKEND,
        "tool_backends": last_backend_report(),
        "latency_budget_s": config.LATENCY_BUDGET_S,
        "llm_calls": telemetry["llm_calls"],
        "tokens": telemetry,
        "formatted": formatted,
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
