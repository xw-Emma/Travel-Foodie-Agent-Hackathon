"""
Budget Tracker - deliberately LLM-free.

Teaching point: deterministic arithmetic must NOT be delegated to an LLM.
This pure function is also registered as the `check_budget` tool so agents
can call it during tool loops.
"""
from __future__ import annotations


def check_budget(items: list[dict], limit: float) -> dict:
    """
    items: [{"name": ..., "cost": <float>, ...}, ...]
    status: ok (<=90% of limit) / warning (<=100%) / exceeded (>100%)
    """
    projected = round(sum(float(i.get("cost", 0)) for i in items), 2)
    if projected <= 0.9 * float(limit):
        status = "ok"
    elif projected <= float(limit):
        status = "warning"
    else:
        status = "exceeded"
    return {"projected": projected, "limit": float(limit),
            "remaining": round(float(limit) - projected, 2), "status": status}
