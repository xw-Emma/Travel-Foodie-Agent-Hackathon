"""Phase 4 (UI rebuild) + Phase 5 (demo hardening) verification."""
from __future__ import annotations
import os

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from src import config, demo_mode, diagnostics, vocabulary
from src.tools import resolve_origin

fails = []


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {label}: {got!r}")
    if not ok:
        fails.append(f"{label}: got {got!r} want {want!r}")


def section(title):
    print(f"\n=== {title} ===")


section("4.2 dropdowns come from the dataset, not retyped constants")
seed_path = config.DATA_DIR / "seed.py"
seed_text = seed_path.read_text(encoding="utf-8")
check("allergens match data/seed.py exactly",
      all(f'"{a}"' in seed_text for a in vocabulary.CANONICAL_ALLERGENS)
      and len(vocabulary.CANONICAL_ALLERGENS) == 9, True)
check("restaurant types include the buckets and real cuisines",
      vocabulary.restaurant_types()[:2] == ["international", "asian"]
      and "ethiopian" in vocabulary.restaurant_types(), True)
check("attraction types read from the CSV", len(vocabulary.attraction_types()), 8)
check("dataset counts are real", vocabulary.dataset_counts(),
      {"restaurants": 60, "attractions": 25})
check("dataset cities discovered from filenames", vocabulary.dataset_cities(), ["Calgary"])
check("covers_city drives the B10 guard",
      (vocabulary.covers_city("Calgary"), vocabulary.covers_city("Montreal")), (True, False))

section("4.3 origin resolution never fails the whole plan")
token = config.set_backend_override("local")
origin = resolve_origin("120 9 Ave SE", "Calgary")
check("offline falls back to the city centre", origin["source"], "city_centre")
check("a fallback origin still has coordinates",
      origin["lat"] is not None and origin["lon"] is not None, True)
check("and is honest that it was not pinned", origin["resolved"], False)
unknown = resolve_origin("somewhere", "Atlantis")
check("an unknown city yields no coordinates rather than wrong ones",
      unknown["lat"], None)
config._backend_override.reset(token)

token = config.set_backend_override("live")
live_origin = resolve_origin("Calgary Tower", "Calgary")
check("live resolves a real landmark", live_origin["source"], "google_places")
check("live origin is inside Calgary",
      51.0 < live_origin["lat"] < 51.1 and -114.1 < live_origin["lon"] < -114.0, True)
config._backend_override.reset(token)

section("4.6 diagnostics answer 'which backend, and why'")
token = config.set_backend_override("local")
report = diagnostics.snapshot(probe_apis=False)
check("live_decision explains the choice", report["live_decision"], "forced_local")
check("unprobed APIs are reported as unprobed", report["places_api"]["ok"], None)
check("dataset rows exposed for the panel", report["local_dataset_rows"]["restaurants"], 60)
check("database presence reported", report["database_built"], True)
config._backend_override.reset(token)
token = config.set_backend_override("auto")
check("auto with a key reads as live",
      diagnostics.snapshot(probe_apis=False)["live_decision"], "auto_key_present")
config._backend_override.reset(token)

section("5.3 demo mode is a real fallback and impossible to mistake")
check("golden plan exists", config.GOLDEN_PLAN_PATH.exists(), True)
golden = demo_mode.load_golden()
check("golden carries a full itinerary", len(golden["itinerary"]), 8)
check("golden carries drawable geometry",
      all(leg.get("polyline") for day in golden["routes"] for leg in day["legs"]), True)
check("golden records when it was captured", bool(golden.get("captured_at")), True)
state = demo_mode.replay({"city": "Calgary", "days": 2}, tier=2)
check("replay is flagged in meta", state.meta["demo_mode"], True)
check("replay is flagged in the backend report",
      state.meta["tool_backends"]["live_decision"], "demo_frozen_plan")
check("replay announces itself in the trace",
      any(entry["agent"] == "demo" for entry in state.trace), True)
raw = config.GOLDEN_PLAN_PATH.read_text(encoding="utf-8")
check("golden plan carries no API key",
      "AIza" not in raw and "X-Goog-Api-Key" not in raw, True)

section("5.5 key hygiene")
gitignore = (config.KIT_ROOT / ".gitignore").read_text(encoding="utf-8")
for entry in (".env", "data/foodie.sqlite", "data/api_cache.sqlite"):
    check(f"{entry} is gitignored", entry in gitignore, True)

section("5.7 docs no longer describe a system that does not exist")
readme = (config.KIT_ROOT / "README.md").read_text(encoding="utf-8")
check("README points at preflight first", "scripts\\preflight.py" in readme, True)
check("README documents the fallback ladder", "FOODIE_DEMO_MODE" in readme, True)
check("README says which Streamlit app to run",
      "not** `frontend/`" in readme or "NOT `frontend" in readme
      or "**not** `frontend/`" in readme, True)
context = (config.KIT_ROOT / "PROJECT_CONTEXT.md").read_text(encoding="utf-8")
check("PROJECT_CONTEXT no longer says Tier 2 is unbuilt",
      "Tier 2 not yet built" in context, False)
check("PROJECT_CONTEXT no longer says the UI is missing",
      "Chat-window UI not yet added" in context, False)

print()
if fails:
    print(f"{len(fails)} FAILURE(S):")
    for f in fails:
        print("  -", f)
    raise SystemExit(1)
print("ALL PHASE 4 + PHASE 5 CHECKS PASSED")
