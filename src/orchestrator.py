"""
Orchestrator — tiered Live-API edition.

  run_tier1 : sequential Planner -> Restaurant(per meal) -> Budget -> Formatter
  run_tier2 : parallel executors + Attraction + Route + Critic revision loop

MOCK MODE: no FUELIX_API_KEY -> deterministic stand-ins (fully offline).
DATA MODE: FOODIE_DATA_BACKEND=auto|live|local (see src/config.py).
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import date

from . import config
from .fuelix_client import FuelixClient, parse_json_reply, run_tool_loop
from .state import TripState, MEALS, TOOL_SCHEMAS, is_valid_slot, slot_ids
from .tools import (TOOL_IMPLS, reset_backend_report, search_restaurants, get_venue_details,
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


def _local_restaurant_tasks(request: dict) -> list[dict]:
    days = int(request.get("days", 2))
    budget = _budget_per_person(request, days)
    return [{
        "agent": "restaurant",
        "slot": f"day{day}.{meal}",
        "meal": meal,
        "area_hint": "",
        "budget_per_person": budget,
        "constraints": {
            "allergies": request.get("allergies", []),
            "cuisines": request.get("cuisines", []),
        },
    } for day in range(1, days + 1) for meal in MEALS]


def _pick_local_task(task: dict, request: dict, used: set[str]) -> tuple[dict | None, list[dict]]:
    allergies = task["constraints"].get("allergies", [])
    cuisines = task["constraints"].get("cuisines") or []
    rows = search_restaurants(
        city=request["city"], meal=task["meal"], cuisine=cuisines[0] if cuisines else None,
        price_level_max=_max_price_level(task["budget_per_person"]),
        exclude_flags=[f"{allergy}_risk" for allergy in allergies], limit=20)
    if len([row for row in rows if row["venue_id"] not in used]) == 0 and cuisines:
        rows = search_restaurants(
            city=request["city"], meal=task["meal"], cuisine=None,
            price_level_max=_max_price_level(task["budget_per_person"]),
            exclude_flags=[f"{allergy}_risk" for allergy in allergies], limit=20)
    available = [row for row in rows if row["venue_id"] not in used]
    pick = min(available, key=lambda row: float(row.get("avg_meal_cost", 0)), default=None)
    return (dict(pick, why="Selected from verified local dataset.") if pick else None, rows)


def _pick_live_task(client: FuelixClient, task: dict, request: dict) -> tuple[dict | None, list[dict]]:
    try:
        pick, rows = _pick_restaurant_with_tool_loop(client, task, request, set())
        return pick, rows
    except Exception:
        return None, []


async def _execute_restaurants_tier2(
    tasks: list[dict], request: dict, client: FuelixClient | None,
    reserved: set[str] | None = None,
) -> tuple[dict[str, dict], dict[str, list[dict]], list[tuple[str, Exception]]]:
    """Run independent restaurant searches concurrently without fail-fast gather."""
    reserved = set(reserved or set())
    if client is None:
        worker = lambda task: _pick_local_task(task, request, reserved)
    else:
        worker = lambda task: _pick_live_task(client, task, request)
    results = await asyncio.gather(
        *(asyncio.to_thread(worker, task) for task in tasks),
        return_exceptions=True,
    )
    selected: dict[str, dict] = {}
    observed: dict[str, list[dict]] = {}
    failures: list[tuple[str, Exception]] = []
    used: set[str] = set(reserved)
    for task, result in zip(tasks, results):
        slot = task["slot"]
        if isinstance(result, Exception):
            failures.append((slot, result))
            continue
        pick, rows = result
        observed[slot] = rows
        replacement = pick if pick and pick["venue_id"] not in used else next(
            (row for row in rows if row["venue_id"] not in used), None)
        if replacement:
            selected[slot] = replacement
            used.add(replacement["venue_id"])
    for task in tasks:
        slot = task["slot"]
        if slot in selected:
            continue
        allergies = task["constraints"].get("allergies", [])
        rows = search_restaurants(
            city=request["city"], meal=task["meal"], cuisine=None,
            price_level_max=_max_price_level(task["budget_per_person"]),
            exclude_flags=[f"{allergy}_risk" for allergy in allergies], limit=20)
        observed[slot] = rows
        replacement = next((row for row in rows if row["venue_id"] not in used), None)
        if replacement:
            selected[slot] = dict(replacement, why="Selected from verified fallback candidates.")
            used.add(replacement["venue_id"])
    return selected, observed, failures


async def _execute_attractions_tier2(city: str, days: int) -> tuple[dict[str, dict], list[tuple[str, Exception]]]:
    results = await asyncio.gather(
        *(asyncio.to_thread(search_attractions, city, None, 2) for _ in range(days)),
        return_exceptions=True,
    )
    selected: dict[str, dict] = {}
    used: set[str] = set()
    failures: list[tuple[str, Exception]] = []
    for day, result in enumerate(results, 1):
        slot = f"day{day}.attraction1"
        if isinstance(result, Exception):
            failures.append((slot, result))
        elif result:
            attraction = next((item for item in result if item["venue_id"] not in used), None)
            if attraction:
                selected[slot] = attraction
                used.add(attraction["venue_id"])
    return selected, failures


DAY_ORDER = ("origin", "breakfast", "attraction1", "lunch",
             "attraction2", "dinner", "attraction3")


def _sort_day_stops(items: list[dict]) -> list[dict]:
    rank = {name: index for index, name in enumerate(DAY_ORDER)}
    return sorted(items, key=lambda item: rank.get(item["slot"].split(".", 1)[1], 99))


def _resolved_origin(request: dict, day: int) -> dict | None:
    origin = request.get("origin") or {}
    if origin.get("lat") is None or origin.get("lon") is None:
        return None
    return {"slot": f"day{day}.origin", "name": origin.get("label") or "Origin",
            "lat": origin["lat"], "lon": origin["lon"]}


def _day_label(request: dict, day: int) -> tuple[str | None, str | None]:
    start_date = request.get("start_date")
    if not start_date:
        return None, None
    if isinstance(start_date, str):
        start_date = date.fromisoformat(start_date)
    current = start_date.fromordinal(start_date.toordinal() + day - 1)
    return current.isoformat(), current.strftime("%a").lower()


async def _compute_routes_async(items: list[dict], request: dict) -> list[dict]:
    days = int(request.get("days", 2))
    mode = str(request.get("transport_mode", "WALK")).lower()
    grouped = {day: _sort_day_stops([item for item in items
                                     if item.get("slot", "").startswith(f"day{day}.")])
               for day in range(1, days + 1)}
    day_stops = []
    for day in range(1, days + 1):
        stops = grouped[day]
        origin = _resolved_origin(request, day)
        if origin:
            stops = [origin, *stops]
        day_stops.append((day, stops))

    calls = []
    call_pairs = []
    for day, stops in day_stops:
        for source, target in zip(stops, stops[1:]):
            call_pairs.append((day, source, target))
            calls.append(asyncio.to_thread(
                estimate_travel, source["lat"], source["lon"],
                target["lat"], target["lon"], mode))
    results = await asyncio.gather(*calls, return_exceptions=True)
    routes_by_day = {day: [] for day in range(1, days + 1)}
    for (day, source, target), result in zip(call_pairs, results):
        if isinstance(result, Exception):
            leg = {"from_slot": source["slot"], "to_slot": target["slot"],
                   "from": source["name"], "to": target["name"], "error": str(result)}
        else:
            leg = {**result, "from_slot": source["slot"], "to_slot": target["slot"],
                   "from": source["name"], "to": target["name"]}
        routes_by_day[day].append(leg)

    routes = []
    for day, stops in day_stops:
        legs = routes_by_day[day]
        totals = {"km": round(sum(float(leg.get("km", 0)) for leg in legs), 2),
                  "minutes": round(sum(float(leg.get("minutes", 0)) for leg in legs), 1)}
        trip_date, weekday = _day_label(request, day)
        routes.append({"day": day, "date": trip_date, "weekday": weekday,
                       "mode": mode.upper(), "legs": legs, "totals": totals,
                       "optimized": False})
    return routes


def _deterministic_critic(st: TripState, request: dict, days: int) -> dict:
    issues = []
    max_walk = float(request.get("max_walk_km", 2.0))
    for day_route in st.routes:
        for route in day_route.get("legs", []):
            if route.get("km", 0) > max_walk:
                target = route.get("to_slot", "")
                if target:
                    issues.append({"slot": target, "type": "travel",
                                   "detail": f"{route['km']} km exceeds {max_walk} km walking limit",
                                   "suggestion": "Choose a closer verified venue."})
    return {"verdict": "revise" if issues else "approved", "issues": issues}


def _critic_with_llm(client: FuelixClient, st: TripState, request: dict) -> dict:
    system = (config.PROMPTS_DIR / "critic.md").read_text(encoding="utf-8")
    user = ("Review this verified itinerary and return STRICT JSON only. "
            f"Original request: {json.dumps(request, sort_keys=True)}\n"
            f"Itinerary: {json.dumps(st.itinerary, default=str)}\n"
            f"Routes: {json.dumps(st.routes, default=str)}\n"
            f"Budget: {json.dumps(st.budget, default=str)}")
    message = client.chat(model=config.MODEL_ROUTING["critic"], system=system,
                          user=user, temperature=0.1, max_tokens=1600)
    return parse_json_reply(message.get("content", ""))


async def _run_tier2_async(request: dict) -> TripState:
    t0 = time.time()
    reset_backend_report()
    days = int(request.get("days", 2))
    party_size = max(1, int(request.get("party_size", 1)))
    st = TripState(request=request)
    client = None if config.MOCK_MODE or config.current_backend() == "local" else FuelixClient(timeout=30, max_retries=1)
    tasks = _local_restaurant_tasks(request) if client is None else _plan_with_llm(client, request)["tasks"]
    selected, observed, failures = await _execute_restaurants_tier2(tasks, request, client)
    for task in tasks:
        slot = task["slot"]
        st.candidates[slot] = observed.get(slot, [])
        pick = selected.get(slot)
        if pick:
            st.itinerary.append({"slot": slot, "venue_id": pick["venue_id"], "name": pick["name"],
                                 "cost": round(pick.get("avg_meal_cost", 0) * party_size, 2),
                                 "lat": pick.get("lat"), "lon": pick.get("lon"),
                                 "source": pick.get("source"), "why": pick.get("why", "Verified restaurant")})
            st.log("restaurant", f"{slot}: chose {pick['name']} via {pick.get('source')}")
        else:
            st.log("restaurant", f"{slot}: failed; continued without this slot")
    for slot, error in failures:
        st.log("restaurant", f"{slot}: {type(error).__name__}: {error}")

    attractions, attraction_failures = await _execute_attractions_tier2(request["city"], days)
    for day in range(1, days + 1):
        slot = f"day{day}.attraction1"
        attraction = attractions.get(slot)
        st.candidates[slot] = search_attractions(request["city"], limit=2) if attraction else []
        if attraction:
            st.itinerary.append({"slot": slot, "venue_id": attraction["venue_id"], "name": attraction["name"],
                                 "cost": round(attraction.get("cost", 0) * party_size, 2),
                                 "lat": attraction.get("lat"), "lon": attraction.get("lon"),
                                 "source": attraction.get("source"), "why": "Top verified attraction"})
            st.log("attraction", f"{slot}: {attraction['name']}")
    for slot, error in attraction_failures:
        st.log("attraction", f"{slot}: {type(error).__name__}: {error}")

    st.routes = await _compute_routes_async(st.itinerary, request)
    st.log("route", f"{sum(len(day.get('legs', [])) for day in st.routes)} travel legs computed")
    st.budget = check_budget(st.itinerary, float(request["budget_total"]))
    for iteration in range(1, config.CRITIC_MAX_ITERATIONS + 1):
        try:
            critic = _deterministic_critic(st, request, days) if client is None else _critic_with_llm(client, st, request)
        except Exception as error:
            critic = _deterministic_critic(st, request, days)
            st.log("critic", f"LLM failed ({type(error).__name__}); used deterministic check")
        ok, bad = validate_critic_output(critic, days=days)
        if not ok:
            st.log("critic", f"Rejected malformed slots {bad}; stopping revision")
            critic = {"verdict": "approved", "issues": [], "iteration": iteration}
        critic["iteration"] = iteration
        st.critic = critic
        st.log("critic", f"verdict={critic.get('verdict')} issues={len(critic.get('issues', []))}")
        if critic.get("verdict") != "revise" or not critic.get("issues") or iteration == config.CRITIC_MAX_ITERATIONS:
            break
        revise_slots = {issue["slot"] for issue in critic["issues"]}
        restaurant_tasks = [task for task in tasks if task["slot"] in revise_slots]
        if restaurant_tasks:
            reserved = {item["venue_id"] for item in st.itinerary
                         if item["slot"] not in revise_slots and item.get("venue_id")}
            replacement, replacement_candidates, _ = await _execute_restaurants_tier2(
                restaurant_tasks, request, client, reserved=reserved)
            for task in restaurant_tasks:
                slot = task["slot"]
                st.candidates[slot] = replacement_candidates.get(slot, st.candidates.get(slot, []))
                pick = replacement.get(slot)
                old = next((item for item in st.itinerary if item["slot"] == slot), None)
                if pick and old:
                    old.update({"venue_id": pick["venue_id"], "name": pick["name"],
                                "cost": round(pick.get("avg_meal_cost", 0) * party_size, 2),
                                "lat": pick.get("lat"), "lon": pick.get("lon"),
                                "source": pick.get("source"), "why": pick.get("why", "Verified replacement")})
            st.routes = await _compute_routes_async(st.itinerary, request)
            st.budget = check_budget(st.itinerary, float(request["budget_total"]))
            st.log("revise", f"reselected slots: {sorted(revise_slots)}")
        attraction_slots = [slot for slot in revise_slots if ".attraction" in slot]
        if attraction_slots:
            replacement_results = await asyncio.gather(
                *(asyncio.to_thread(search_attractions, request["city"], None, 2)
                  for _ in attraction_slots),
                return_exceptions=True,
            )
            for slot, result in zip(attraction_slots, replacement_results):
                reserved_attractions = {item["venue_id"] for item in st.itinerary
                                        if item["slot"] not in attraction_slots
                                        and ".attraction" in item["slot"]}
                attraction = next((item for item in (result if isinstance(result, list) else [])
                                   if item["venue_id"] not in reserved_attractions), None)
                current = next((item for item in st.itinerary if item["slot"] == slot), None)
                if attraction and current:
                    current.update({"venue_id": attraction["venue_id"], "name": attraction["name"],
                                    "cost": round(attraction.get("cost", 0) * party_size, 2),
                                    "lat": attraction.get("lat"), "lon": attraction.get("lon"),
                                    "source": attraction.get("source"), "why": "Verified replacement attraction"})
            st.routes = await _compute_routes_async(st.itinerary, request)
            st.budget = check_budget(st.itinerary, float(request["budget_total"]))
    st.meta = {"tier": 2, "elapsed_s": round(time.time() - t0, 2), "mock_llm": config.MOCK_MODE,
               "data_backend": config.current_backend(), "tool_backends": last_backend_report(),
               "latency_budget_s": config.LATENCY_BUDGET_S,
               "llm_calls": client.telemetry["llm_calls"] if client else 0,
               "tokens": dict(client.telemetry) if client else {
                   "llm_calls": 0, "input_tokens": 0, "output_tokens": 0,
               }}
    return st


# ------------------------------------------------------- TIER 1
def run_tier1(request: dict) -> TripState:
    """
    Sequential agentic pipeline. With FUELIX_API_KEY set, teams replace the
    mock Planner / Restaurant picker / Formatter with real Fuel iX calls
    (see prompts/ and BUILD guide). Allergen exclusion is enforced at the
    TOOL layer regardless.
    """
    t0 = time.time()
    reset_backend_report()
    st = TripState(request=request)
    days = int(request.get("days", 2))
    party_size = max(1, int(request.get("party_size", 1)))

    if config.MOCK_MODE or config.current_backend() == "local":
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
    if not config.MOCK_MODE and config.current_backend() != "local":
        formatted = _format_with_llm(client, st)
        telemetry = dict(client.telemetry)
        st.log("formatter", "Formatted the verified itinerary with Fuel iX")
    st.meta = {
        "tier": 1,
        "elapsed_s": round(time.time() - t0, 2),
        "mock_llm": config.MOCK_MODE,
        "data_backend": config.current_backend(),
        "tool_backends": last_backend_report(),
        "latency_budget_s": config.LATENCY_BUDGET_S,
        "llm_calls": telemetry["llm_calls"],
        "tokens": telemetry,
        "formatted": formatted,
    }
    return st


# ------------------------------------------------------- TIER 2
def run_tier2(request: dict) -> TripState:
    """Run the asynchronous Plan -> Execute -> Check -> Revise -> Ship flow."""
    return asyncio.run(_run_tier2_async(request))


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
