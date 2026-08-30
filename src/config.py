"""
Central configuration for the Travel Foodie Agent starter kit.

Everything is environment-driven so teams never edit code to switch modes:

  FUELIX_API_KEY        Fuel iX Dev Portal key (LLM gateway). No key = MOCK mode.
  GOOGLE_MAPS_API_KEY   Per-team restricted GCP key (Places API New + Routes API).
  FOODIE_DATA_BACKEND   live | local | auto   (default: auto)
                          live  = Google APIs only (fails loudly if key/quota missing)
                          local = pre-staged SQLite dataset only (demo-day insurance)
                          auto  = try live, fall back to local, record the fallback
  FOODIE_CACHE          on | off (default: on) - caches Google API responses so dev
                        iterations do not re-bill and demos survive flaky Wi-Fi.
  FOODIE_MODEL_*        Optional per-agent model overrides (see MODEL_ROUTING).

A tiny stdlib .env loader runs on import: put keys in a gitignored .env at the
kit root and they are picked up automatically. NEVER hardcode keys in code,
notebooks, or slides.
"""
from __future__ import annotations

import contextvars
import os
from pathlib import Path

KIT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = KIT_ROOT / "data"
PROMPTS_DIR = KIT_ROOT / "prompts"
DB_PATH = DATA_DIR / "foodie.sqlite"
CACHE_DB_PATH = DATA_DIR / "api_cache.sqlite"
_backend_override: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "backend_override", default=None)


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env loader (stdlib only). Existing env vars are not overridden."""
    env_file = path or (KIT_ROOT / ".env")
    if not env_file.exists():
        return
    for raw in env_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv()

# ----------------------------------------------------------------- LLM gateway
FUELIX_BASE_URL = os.environ.get("FUELIX_BASE_URL", "https://api.fuelix.ai/v1")
FUELIX_API_KEY = os.environ.get("FUELIX_API_KEY", "")
MOCK_MODE = not FUELIX_API_KEY  # no key -> deterministic offline stand-ins

# Verified default from last year's deck. Confirm the enabled model IDs for our
# org in the Fuel iX Dev Portal profile and override via env if needed.
DEFAULT_MODEL = os.environ.get("FOODIE_MODEL_DEFAULT", "claude-sonnet-4")

MODEL_ROUTING = {
    "planner":    os.environ.get("FOODIE_MODEL_PLANNER", DEFAULT_MODEL),
    "restaurant": os.environ.get("FOODIE_MODEL_RESTAURANT", DEFAULT_MODEL),
    "attraction": os.environ.get("FOODIE_MODEL_ATTRACTION", DEFAULT_MODEL),
    "route":      os.environ.get("FOODIE_MODEL_ROUTE", DEFAULT_MODEL),
    "critic":     os.environ.get("FOODIE_MODEL_CRITIC", DEFAULT_MODEL),
    "formatter":  os.environ.get("FOODIE_MODEL_FORMATTER", DEFAULT_MODEL),
}

# -------------------------------------------------------------- Google APIs
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")
LIVE_DATA_AVAILABLE = bool(GOOGLE_MAPS_API_KEY)

PLACES_BASE_URL = "https://places.googleapis.com/v1"
ROUTES_BASE_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"

DATA_BACKEND = os.environ.get("FOODIE_DATA_BACKEND", "auto").lower()  # live|local|auto
CACHE_ENABLED = os.environ.get("FOODIE_CACHE", "on").lower() != "off"

# Last rung of the demo-day fallback ladder (live -> local -> demo). Replays a
# frozen plan captured from a good run, for when both the APIs and the local
# dataset are unusable on stage. It is deliberately loud: every replayed plan is
# marked in the trace, in meta, and in the UI, because a frozen plan presented
# as a live one would misrepresent what the system did.
DEMO_MODE = os.environ.get("FOODIE_DEMO_MODE", "off").lower() == "on"
GOLDEN_PLAN_PATH = DATA_DIR / "golden_plan.json"


def current_backend() -> str:
    """Return the request-local backend override, or the process default."""
    return (_backend_override.get() or DATA_BACKEND).lower()


def set_backend_override(value: str | None):
    """Set a request-local backend and return its reset token."""
    return _backend_override.set(value.lower() if value else None)

# --------------------------------------------------------------- heuristics
# Google Places has no allergen fields. In live mode we conservatively infer a
# risk flag from cuisine type; risky venues are EXCLUDED IN CODE and every live
# plan must carry a "verify with the restaurant" advisory (see prompts/).
# The graded allergen-trap scenario runs on the local dataset, where the flag
# is explicit ground truth.
ALLERGEN_RISK_CUISINES = {
    "peanut": ["thai", "vietnamese", "chinese", "indonesian", "malaysian",
               "sichuan", "szechuan", "asian", "asian_fusion"],
    "shellfish": ["seafood", "cajun", "sushi", "japanese"],
    "gluten": ["bakery", "pizza", "italian", "deli"],
}

# Rough CAD per-person meal cost by Google price level (for budget projection
# in live mode; the local dataset carries exact avg_meal_cost values).
PRICE_LEVEL_MEAL_COST = {0: 12.0, 1: 15.0, 2: 30.0, 3: 50.0, 4: 80.0}

# ------------------------------------------------------------------ budgets
LATENCY_BUDGET_S = 60          # full itinerary must land under this
CRITIC_MAX_ITERATIONS = 2      # bounded reflection loop (Tier 2)
TOOL_LOOP_MAX_ROUNDS = 4       # per-agent tool-calling loop bound

# The restaurant executor is the one place the model directs its own tool use:
# it reads what a search returned and decides whether to search again with a
# different strategy. Every extra round is one more serial LLM call - measured
# at roughly 6-9 s across the concurrent slots - so the bound is a knob rather
# than a constant. Set FOODIE_EXECUTOR_TOOL_ROUNDS=2 to go back to the single
# forced search if a demo needs the latency back.
EXECUTOR_TOOL_ROUNDS = max(2, int(os.environ.get("FOODIE_EXECUTOR_TOOL_ROUNDS", "3")))
# One round is spent on the final answer, so the searches are the rest.
EXECUTOR_MAX_SEARCHES = EXECUTOR_TOOL_ROUNDS - 1
