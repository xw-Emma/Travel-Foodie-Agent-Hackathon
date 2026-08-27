"""Frozen-plan replay: the last rung of the demo-day fallback ladder.

live -> local -> demo. The first two need the network or the seeded database;
this one needs neither, so it still renders when everything else is gone.

It is deliberately impossible to mistake for a real run. Every replayed plan is
marked in meta, announced in the trace, and shown as a banner in the UI. A
frozen plan presented as a live one would misrepresent what the system did,
which is worse than having no fallback at all.
"""
from __future__ import annotations

import json

from . import config
from .state import TripState


class GoldenPlanMissing(RuntimeError):
    pass


def load_golden() -> dict:
    if not config.GOLDEN_PLAN_PATH.exists():
        raise GoldenPlanMissing(
            f"FOODIE_DEMO_MODE=on but {config.GOLDEN_PLAN_PATH} is missing. "
            "Capture one first:  python scripts/warm_cache.py --golden")
    return json.loads(config.GOLDEN_PLAN_PATH.read_text(encoding="utf-8"))


def replay(request: dict, tier: int) -> TripState:
    """Rebuild a TripState from the frozen plan, labelled as a replay."""
    golden = load_golden()
    st = TripState(request=request)
    st.plan = golden.get("plan") or {}
    st.candidates = golden.get("candidates") or {}
    st.routes = golden.get("routes") or []
    st.budget = golden.get("budget") or {}
    st.critic = golden.get("critic") or {}
    st.itinerary = golden.get("itinerary") or []
    st.trace = list(golden.get("trace") or [])
    st.log("demo", "DEMO MODE: replayed a frozen plan. No API call was made and "
                   "no agent ran for this request.")
    meta = dict(golden.get("meta") or {})
    meta.update({
        "tier": tier,
        "demo_mode": True,
        "captured_at": golden.get("captured_at"),
        "captured_request": golden.get("request"),
        "data_backend": "demo_frozen",
        "tool_backends": {**(meta.get("tool_backends") or {}),
                          "live_decision": "demo_frozen_plan"},
    })
    st.meta = meta
    return st
