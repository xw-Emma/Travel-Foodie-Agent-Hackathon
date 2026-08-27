"""Lightweight acceptance checks for graded scenarios (run offline against local backend).

Run from the kit root:
  FOODIE_DATA_BACKEND=local python eval/acceptance.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.orchestrator import run_tier2  # noqa: E402
from src import config  # noqa: E402

SCENARIOS = Path(__file__).with_name("scenarios.json")


def check_no_allergen_leaks(st, request) -> list[str]:
    fails = []
    allergies = request.get("allergies") or []
    for slot, cands in st.candidates.items():
        for c in cands:
            flags = c.get("dietary_flags") or {}
            for a in allergies:
                if flags.get(f"{a}_risk"):
                    fails.append(f"{slot}:{c.get('name')}:{a}_risk")
    return fails


def check_budget_ok(st, request) -> list[str]:
    if st.budget.get("status") == "exceeded":
        return [f"budget exceeded: {st.budget}"]
    return []


def check_has_meals(st, request) -> list[str]:
    days = int(request.get("days", 2))
    expected = days * 3
    meals = [it for it in st.itinerary if any(m in it["slot"] for m in ("breakfast", "lunch", "dinner"))]
    if len(meals) < expected:
        return [f"expected {expected} meals, got {len(meals)}"]
    return []


CHECKS = {
    "no_allergen_leaks": check_no_allergen_leaks,
    "budget_ok": check_budget_ok,
    "has_meals": check_has_meals,
}


def main() -> None:
    # Force local backend so graded traps are deterministic
    config.DATA_BACKEND = "local"
    scenarios = json.loads(SCENARIOS.read_text())
    all_ok = True
    for sc in scenarios:
        st = run_tier2(sc["request"])
        print(f"\n== {sc['id']} ==")
        for name in sc.get("checks", []):
            fails = CHECKS[name](st, sc["request"])
            status = "PASS" if not fails else "FAIL"
            if fails:
                all_ok = False
            print(f"  {status} {name} {fails or ''}")
        print(f"  elapsed={st.meta['elapsed_s']}s budget={st.budget['status']}")
    raise SystemExit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
