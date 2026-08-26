"""Simple CLI UI — shows itinerary + agent trace. Safe UI fallback if Streamlit is blocked.

Run from the kit root:
  python app/cli.py --tier 1
  python app/cli.py --tier 2
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.orchestrator import run_tier1, run_tier2  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="Traveling Foodie Agent CLI")
    p.add_argument("--tier", type=int, choices=[1, 2], default=1)
    p.add_argument("--city", default="Calgary")
    p.add_argument("--days", type=int, default=2)
    p.add_argument("--budget", type=float, default=500)
    p.add_argument("--cuisine", default="international")
    p.add_argument("--allergy", action="append", default=["peanut"])
    p.add_argument("--party", type=int, default=2)
    p.add_argument("--json", action="store_true", help="dump full TripState JSON")
    args = p.parse_args()

    request = {
        "city": args.city, "days": args.days, "budget_total": args.budget,
        "party_size": args.party, "cuisines": [args.cuisine],
        "allergies": args.allergy or [],
    }
    st = run_tier2(request) if args.tier == 2 else run_tier1(request)

    if args.json:
        print(json.dumps(st.to_json(), indent=2, default=str))
        return

    print(f"\n=== Foodie Agent · Tier {st.meta.get('tier')} · {args.city} ===")
    print(f"budget: {st.budget}")
    print(f"backend: {st.meta.get('tool_backends')}")
    print("\nItinerary:")
    for it in st.itinerary:
        print(f"  {it['slot']:18s} {it['name'][:32]:32s} ${it.get('cost', 0)}")
    if st.routes:
        print("\nRoutes:")
        for leg in st.routes:
            print(f"  {leg.get('from')} → {leg.get('to')}: "
                  f"{leg.get('minutes')} min ({leg.get('source')})")
    if st.critic:
        print(f"\nCritic: {st.critic.get('verdict')} ({len(st.critic.get('issues', []))} issues)")
    print("\nTrace:")
    for t in st.trace:
        print(f"  [{t['agent']}] {t['message']}")


if __name__ == "__main__":
    main()
