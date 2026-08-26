"""
Agent helpers — Critic validation lives here so teams can grow this package
into planner.py / restaurant.py / etc. without touching the orchestrator core.
"""
from __future__ import annotations

from ..state import is_valid_slot


def validate_critic_output(critic_json: dict, days: int = 2) -> tuple[bool, list[str]]:
    """Return (ok, bad_slots). Bad slots must trigger a Critic re-ask."""
    bad = [iss.get("slot") for iss in critic_json.get("issues", [])
           if not is_valid_slot(str(iss.get("slot", "")), days=days)]
    return (len(bad) == 0, bad)
