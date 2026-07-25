#!/usr/bin/env python3
"""M0 smoke test — run from the kit root:  python scripts/smoke_test.py"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import config  # noqa: E402
from src.orchestrator import run_tier1, validate_critic_output  # noqa: E402


def main() -> None:
    print("KIT_ROOT:", config.KIT_ROOT)
    print("DB exists:", config.DB_PATH.exists())
    print("MOCK_LLM:", config.MOCK_MODE, "(no FUELIX_API_KEY)" if config.MOCK_MODE else "")
    print("LIVE_DATA:", config.LIVE_DATA_AVAILABLE,
          "(no GOOGLE_MAPS_API_KEY)" if not config.LIVE_DATA_AVAILABLE else "")
    print("DATA_BACKEND:", config.DATA_BACKEND)

    if not config.DB_PATH.exists():
        print("FAIL: run `python data/seed.py` first")
        raise SystemExit(1)

    S1 = {"city": "Calgary", "days": 2, "budget_total": 500, "party_size": 2,
          "cuisines": ["international"], "allergies": ["peanut"]}
    st = run_tier1(S1)
    leaked = []
    for slot, cands in st.candidates.items():
        for c in cands:
            if (c.get("dietary_flags") or {}).get("peanut_risk"):
                leaked.append(c["name"])

    print(f"tier1 elapsed={st.meta['elapsed_s']}s budget={st.budget['status']} meals={len(st.itinerary)}")
    print(f"allergen leaks: {leaked}")
    print("slot-guard good:", validate_critic_output(
        {"verdict": "revise", "issues": [{"slot": "day2.dinner"}]}))
    print("slot-guard bad :", validate_critic_output(
        {"verdict": "revise", "issues": [{"slot": "dinner day 2"}]}))

    if leaked:
        print("FAIL: allergen trap leaked into candidates")
        raise SystemExit(1)
    if st.budget["status"] == "exceeded":
        print("FAIL: budget exceeded on S1")
        raise SystemExit(1)

    if config.LIVE_DATA_AVAILABLE:
        print("\n-- live Places probe --")
        try:
            from src.tools import places_live
            rows = places_live.search_restaurants("Calgary", "lunch", limit=2)
            print(f"Places returned {len(rows)} rows:",
                  [r["name"] for r in rows])
        except Exception as exc:  # noqa: BLE001
            print("Places probe FAILED:", exc)
            print("(You can still hack offline with FOODIE_DATA_BACKEND=local)")
            raise SystemExit(2)
    else:
        print("\nSKIP live Places probe (set GOOGLE_MAPS_API_KEY to enable)")

    if not config.MOCK_MODE:
        print("\n-- Fuel iX probe --")
        try:
            from src.fuelix_client import FuelixClient
            c = FuelixClient()
            msg = c.chat(system="Reply exactly: OK", user="ping")
            print("Fuel iX:", (msg.get("content") or "")[:80], c.telemetry)
        except Exception as exc:  # noqa: BLE001
            print("Fuel iX probe FAILED:", exc)
            raise SystemExit(3)
    else:
        print("\nSKIP Fuel iX probe (set FUELIX_API_KEY to enable)")

    print("\nSMOKE OK")


if __name__ == "__main__":
    main()
