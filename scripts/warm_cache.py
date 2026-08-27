#!/usr/bin/env python3
"""Pre-populate the API response cache, and optionally freeze a golden plan.

WHY THIS EXISTS: src/tools/cache.py persists Google responses to
data/api_cache.sqlite, so a request that has been made once still answers when
the venue Wi-Fi dies mid-demo. Run the EXACT demo request beforehand, on the
machine you will demo from - the cache file is gitignored and does not travel.

  python scripts/warm_cache.py                 # warm the cache
  python scripts/warm_cache.py --golden        # ...and freeze data/golden_plan.json
  python scripts/warm_cache.py --scenarios eval/scenarios.json

The golden plan is the last rung of the fallback ladder (live -> local -> demo),
replayed when FOODIE_DEMO_MODE=on. Capture it from a good LIVE run so the frozen
plan shows real venues.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import config  # noqa: E402
from src.orchestrator import run_tier2  # noqa: E402

DEMO_REQUEST = {
    "city": "Calgary", "days": 2, "budget_total": 500, "party_size": 2,
    "cuisines": ["international"], "allergies": ["peanut"],
    "max_leg_minutes": 25.0, "max_daily_travel_minutes": 120.0,
    "transport_mode": "WALK",
}


def load_requests(path: Path | None) -> list[dict]:
    if path is None:
        return [DEMO_REQUEST]
    scenarios = json.loads(path.read_text(encoding="utf-8"))
    return [scenario["request"] for scenario in scenarios]


def freeze(state, request: dict) -> dict:
    return {
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "request": request,
        "plan": state.plan,
        "itinerary": state.itinerary,
        "routes": state.routes,
        "budget": state.budget,
        "critic": state.critic,
        "candidates": state.candidates,
        "trace": state.trace,
        "meta": state.meta,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", action="store_true",
                        help="also write data/golden_plan.json from the first request")
    parser.add_argument("--scenarios", type=Path, default=None,
                        help="warm every request in a scenarios JSON file")
    parser.add_argument("--backend", default="live", choices=["live", "auto", "local"])
    args = parser.parse_args()

    if config.DEMO_MODE:
        print("REFUSING: FOODIE_DEMO_MODE=on would replay a frozen plan instead "
              "of calling the APIs. Unset it and run again.")
        return 2
    if args.backend != "local" and not config.GOOGLE_MAPS_API_KEY:
        print("GOOGLE_MAPS_API_KEY is not set — nothing live to warm.")
        return 2
    if not config.CACHE_ENABLED:
        print("WARNING: FOODIE_CACHE=off, so nothing will be cached.")

    requests = load_requests(args.scenarios)
    token = config.set_backend_override(args.backend)
    first_state = None
    try:
        for index, request in enumerate(requests, 1):
            started = time.time()
            state = run_tier2(dict(request))
            first_state = first_state or state
            backends = (state.meta or {}).get("tool_backends", {})
            print(f"[{index}/{len(requests)}] {request.get('city')} "
                  f"{request.get('days')}d ${request.get('budget_total')} "
                  f"-> {len(state.itinerary)} stops in {time.time() - started:.1f}s "
                  f"| {backends.get('restaurants')} / {backends.get('travel')}")
    finally:
        config._backend_override.reset(token)

    print(f"\nCache: {config.CACHE_DB_PATH} "
          f"({config.CACHE_DB_PATH.stat().st_size // 1024 if config.CACHE_DB_PATH.exists() else 0} KB)")

    if args.golden and first_state is not None:
        payload = freeze(first_state, requests[0])
        config.GOLDEN_PLAN_PATH.write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print(f"Golden plan: {config.GOLDEN_PLAN_PATH} "
              f"({len(first_state.itinerary)} stops). "
              "Replay it with FOODIE_DEMO_MODE=on.")
    print("\nRehearse the ladder before demo day:")
    print("  FOODIE_DATA_BACKEND=live  streamlit run app/streamlit_app.py")
    print("  FOODIE_DATA_BACKEND=local streamlit run app/streamlit_app.py")
    print("  FOODIE_DEMO_MODE=on       streamlit run app/streamlit_app.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
