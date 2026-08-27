#!/usr/bin/env python3
"""Run the same request through Tier 1 and Tier 2 and diff the results.

WHY THIS EXISTS: the two tiers produced byte-identical output for the entire
first week of the project because the UI hardcoded tier=1. If this script
reports no difference, Tier 2 is not wired up. Run it after every change.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.orchestrator import run_tier1, run_tier2

REQUEST = {
    "city": "Calgary", "days": 2, "budget_total": 500, "party_size": 2,
    "cuisines": ["international"], "allergies": ["peanut"], "max_walk_km": 2.0,
}


def summarize(state) -> dict:
    return {
        "stops": len(state.itinerary),
        "route_legs": sum(len(day.get("legs", [])) for day in state.routes),
        "budget": state.budget,
        "tool_backends": state.meta.get("tool_backends"),
        "critic_verdict": (state.critic or {}).get("verdict"),
        "critic_issues": len((state.critic or {}).get("issues", [])),
        "venues": [(item["slot"], item["name"]) for item in state.itinerary],
    }


def main() -> int:
    one = summarize(run_tier1(dict(REQUEST)))
    two = summarize(run_tier2(dict(REQUEST)))
    print("TIER 1:", json.dumps(one, indent=2, default=str))
    print("TIER 2:", json.dumps(two, indent=2, default=str))
    if one == two:
        print("\nFAIL: Tier 1 and Tier 2 are identical. Tier 2 is not wired up.")
        return 1
    print(f"\nOK: tiers differ. stops {one['stops']} -> {two['stops']}, "
          f"legs {one['route_legs']} -> {two['route_legs']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
