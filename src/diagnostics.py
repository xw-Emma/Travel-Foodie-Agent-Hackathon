"""One health snapshot, shared by GET /diagnostics and the Streamlit sidebar.

WHY IT EXISTS: 'restaurants: local_dataset' alone never says whether local was
chosen, forced, or fallen back to after a 403. That ambiguity is the first thing
a judge asks about and the first thing that wastes debugging time, so the answer
is rendered where it can be seen instead of inferred from logs.
"""
from __future__ import annotations

import time

from . import config, vocabulary
from .tools import places_live, routes_live

# Calgary city hall, used purely as a fixed probe target.
PROBE_LAT, PROBE_LON = 51.0447, -114.0719


def _probe(call) -> dict:
    """Run one live call and report how it went, never raising."""
    started = time.time()
    try:
        rows = call()
    except Exception as exc:  # noqa: BLE001 - the failure IS the diagnostic
        message = str(exc)
        status = None
        for code in (400, 403, 429):
            if f"HTTP {code}" in message:
                status = code
        return {"ok": False, "http_status": status, "reason": message[:200],
                "latency_ms": int((time.time() - started) * 1000)}
    return {"ok": True, "results": len(rows) if isinstance(rows, list) else 1,
            "latency_ms": int((time.time() - started) * 1000)}


def live_decision() -> str:
    """Why live will or will not be used, in the same words as the tool report."""
    backend = config.current_backend()
    if backend == "local":
        return "forced_local"
    if backend == "live":
        return "forced_live"
    return "auto_key_present" if config.LIVE_DATA_AVAILABLE else "auto_no_api_key"


def snapshot(probe_apis: bool = True) -> dict:
    """Full picture. Set probe_apis=False to skip the two billed calls."""
    report = {
        "tier_default": 2,
        "live_decision": live_decision(),
        "data_backend": config.current_backend(),
        "maps_key_set": bool(config.GOOGLE_MAPS_API_KEY),
        "fuelix_key_set": bool(config.FUELIX_API_KEY),
        "mock_llm": config.MOCK_MODE,
        "cache_enabled": config.CACHE_ENABLED,
        "demo_mode": config.DEMO_MODE,
        "golden_plan_present": config.GOLDEN_PLAN_PATH.exists(),
        "local_dataset_rows": vocabulary.dataset_counts(),
        "local_dataset_cities": vocabulary.dataset_cities(),
        "database_built": config.DB_PATH.exists(),
    }
    if not probe_apis or not config.GOOGLE_MAPS_API_KEY:
        report["places_api"] = {"ok": None, "reason": "not probed"}
        report["routes_api"] = {"ok": None, "reason": "not probed"}
        return report
    report["places_api"] = _probe(
        lambda: places_live.search_restaurants("Calgary", "dinner", limit=1))
    report["routes_api"] = _probe(
        lambda: [routes_live.estimate_travel(
            PROBE_LAT, PROBE_LON, 51.0392, -114.0203, mode="walk")])
    return report
