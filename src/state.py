"""
Shared state, the closed slot-ID vocabulary, and OpenAI-format tool schemas.

WHY THE CLOSED SLOT VOCABULARY MATTERS
--------------------------------------
The Critic names slots when it requests revisions ("day2.dinner has an hours
conflict"). If a sloppy LLM emits "dinner day 2" instead, and the orchestrator
trusts it, you re-plan the wrong slots or loop forever - the failure mode most
likely to break a live demo. Every Critic message MUST pass
`validate_critic_output()` (src/agents/critic.py) before the Planner sees it.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

MEALS = ("breakfast", "lunch", "dinner")


def slot_ids(days: int = 2, meals=MEALS, attractions_per_day: int = 1) -> list[str]:
    ids: list[str] = []
    for d in range(1, days + 1):
        for m in meals:
            ids.append(f"day{d}.{m}")
        for a in range(1, attractions_per_day + 1):
            ids.append(f"day{d}.attraction{a}")
    return ids


SLOT_IDS = slot_ids()  # default 2-day vocabulary; regenerate for other trips


def day_scopes(days: int = 2) -> list[str]:
    """Non-revisable scopes the Critic may legitimately name.

    "day2" carries an issue that belongs to a whole day rather than one stop —
    a daily travel-time or budget total. "day2.origin" is where the day starts;
    route legs reference it, but no agent can re-plan it.
    """
    scopes: list[str] = []
    for d in range(1, days + 1):
        scopes.append(f"day{d}")
        scopes.append(f"day{d}.origin")
    return scopes


def is_valid_slot(slot: str, days: int = 2) -> bool:
    """Accept a revisable slot OR a day-level scope. Still a closed vocabulary —
    slot_ids() alone stays the set the Planner may fill and the Critic may ask
    to redo."""
    return slot in slot_ids(days) or slot in day_scopes(days)


@dataclass
class TripState:
    """The single shared context passed between agents. No long-term memory."""
    request: dict
    plan: dict = field(default_factory=dict)          # Planner output
    candidates: dict = field(default_factory=dict)    # slot -> [candidate, ...]
    routes: list = field(default_factory=list)        # Route agent output
    budget: dict = field(default_factory=dict)        # check_budget output
    critic: dict = field(default_factory=dict)        # last Critic verdict
    itinerary: list = field(default_factory=list)     # final ordered picks
    trace: list = field(default_factory=list)         # human-readable agent trace
    meta: dict = field(default_factory=dict)          # timing, tokens, backend

    def log(self, agent: str, message: str) -> None:
        self.trace.append({"agent": agent, "message": message})

    def to_json(self) -> dict:
        return asdict(self)


# ------------------------------------------------------- OpenAI tool schemas
# Passed to Fuel iX chat completions; implementations live in src/tools/
# (live Google backends with the local SQLite fallback behind one dispatcher).
TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "search_restaurants",
        "description": ("Find restaurants in a city for a meal, optionally filtered "
                        "by area, cuisine and max price level, excluding venues whose "
                        "dietary flags match exclude_flags (e.g. ['peanut_risk'])."),
        "parameters": {"type": "object", "properties": {
            "city": {"type": "string"},
            "meal": {"type": "string", "enum": ["breakfast", "lunch", "dinner"]},
            "area": {"type": "string"},
            "cuisine": {"type": "string"},
            "price_level_max": {"type": "integer", "minimum": 1, "maximum": 5},
            "exclude_flags": {"type": "array", "items": {"type": "string"}},
            "limit": {"type": "integer"},
            "near": {"type": "array", "items": {"type": "number"},
                     "minItems": 2, "maxItems": 2,
                     "description": "[lat, lon] anchor, usually the previous stop."},
            "within_km": {"type": "number",
                          "description": "Max distance from `near`."}},
            "required": ["city", "meal"]}}},
    {"type": "function", "function": {
        "name": "get_venue_details",
        "description": "Full venue details: opening hours, rating, dietary flags.",
        "parameters": {"type": "object", "properties": {
            "venue_id": {"type": "string"}}, "required": ["venue_id"]}}},
    {"type": "function", "function": {
        "name": "search_attractions",
        "description": "Find tourist attractions in a city, optionally by category.",
        "parameters": {"type": "object", "properties": {
            "city": {"type": "string"}, "category": {"type": "string"},
            "limit": {"type": "integer"},
            "near": {"type": "array", "items": {"type": "number"},
                     "minItems": 2, "maxItems": 2,
                     "description": "[lat, lon] anchor, usually the previous stop."},
            "within_km": {"type": "number",
                          "description": "Max distance from `near`."}},
            "required": ["city"]}}},
    {"type": "function", "function": {
        "name": "estimate_travel",
        "description": "Travel time and distance between two lat/lon points.",
        "parameters": {"type": "object", "properties": {
            "from_lat": {"type": "number"}, "from_lon": {"type": "number"},
            "to_lat": {"type": "number"}, "to_lon": {"type": "number"},
            "mode": {"type": "string",
                     "enum": ["walk", "drive", "transit", "bicycle"]}},
            "required": ["from_lat", "from_lon", "to_lat", "to_lon"]}}},
    {"type": "function", "function": {
        "name": "compute_day_route",
        "description": ("Route a whole day in one call: ordered stops, per-leg "
                        "distance/time/geometry, and day totals. Prefer this "
                        "over repeated estimate_travel calls for one day."),
        "parameters": {"type": "object", "properties": {
            "origin": {"type": "object",
                       "description": "Day start: {lat, lon, name, slot}."},
            "stops": {"type": "array", "items": {"type": "object"},
                      "description": "Stops to visit: [{lat, lon, name, slot}]."},
            "mode": {"type": "string",
                     "enum": ["WALK", "DRIVE", "TRANSIT", "BICYCLE"]},
            "optimize": {"type": "boolean",
                         "description": "Let the router reorder the stops."}},
            "required": ["stops"]}}},
    {"type": "function", "function": {
        "name": "check_budget",
        "description": "Sum item costs against the total budget limit (pure math).",
        "parameters": {"type": "object", "properties": {
            "items": {"type": "array", "items": {"type": "object"}},
            "limit": {"type": "number"}},
            "required": ["items", "limit"]}}},
]
