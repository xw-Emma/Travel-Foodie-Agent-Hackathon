# Travel Foodie Agent — Tier 2 Activation & Map UI Implementation Plan

**Target repo:** `xw-Emma/Travel-Foodie-Agent-Hackathon` (branch `master`, HEAD `921dd41`)
**Audience:** AI coding assistant / IDE agent
**Written:** 2026-08-26
**Status of analysis:** All findings below were verified by reading the repo at `921dd41` and by executing `run_tier1()` and `run_tier2()` locally against the seeded SQLite dataset. Line numbers refer to `921dd41`.

---

## 0. How to use this document

Each phase has three parts:

| Part | Meaning |
|---|---|
| **CAUSE** | Why this change is needed. The observable symptom and the code that produces it. |
| **EFFECT** | What breaks today because of the cause, and what will be true after the fix. |
| **CHANGES** | The concrete edits, file by file. |
| **ACCEPTANCE** | A command to run and the exact output that proves the phase is done. |

Do not skip the ACCEPTANCE step. Several bugs in this repo are invisible without an explicit check, which is how they survived to `HEAD`.

**Ground rules inherited from the project (`README.md`):**

- All LLM traffic goes through Fuel iX only.
- API keys live in `.env` only. Never in code, notebooks, or slides.
- Hallucinated venues score zero. Every pick must be grounded in API or dataset data.
- Budget math stays pure Python. No LLM arithmetic.
- The CSVs in `data/csv/` are the single source of truth. `data/seed.py` only ever reads them.

Add one more rule for this work:

- **Every change must keep the `live | local | auto` triple working.** The local dataset is demo-day insurance. If a new feature only works in `live` mode, it is not done.

---

## 1. Repo snapshot — what actually exists today

Understanding what is already built is essential, because the gap between the current state and the desired state is much smaller than it appears from the running UI.

### 1.1 Already working, do not rebuild

| Capability | Location | Note |
|---|---|---|
| Live/local/auto backend switch | `src/tools/__init__.py` | Clean facade. Agents only import from here. One-line mode switch. |
| Google Places (New) client | `src/tools/places_live.py` | `searchText` + place details, correct `X-Goog-FieldMask` usage. |
| Google Routes client | `src/tools/routes_live.py` | `computeRoutes`, point-to-point only. |
| Offline dataset backend | `src/tools/local_catalog.py` | Same tool signatures as live. Haversine travel estimate. |
| API response cache | `src/tools/cache.py` | SQLite-backed, `FOODIE_CACHE=on`. Prevents re-billing on iteration. |
| **`lat` / `lon` on every itinerary item** | `src/orchestrator.py` | **The prerequisite for any map. Already present.** |
| **A working `st.map` render** | `app/streamlit_app.py:64` `render_map()` | Already plots stops. |
| **Tier + backend selectors in UI** | `app/streamlit_app.py:106` sidebar | Tier defaults to 2, backend selector present. |
| **API health check script** | `scripts/preflight.py` | Tests proxy, Fuel iX `/models`, live Places call, live Routes call. |
| Allergen hard exclusion at the data layer | `src/tools/local_catalog.py` | `continue` on flag match — enforced in code, not just in the prompt. Correct design. |
| Closed slot-ID vocabulary + critic guard | `src/state.py`, `src/agents/critic.py` | Prevents malformed critic slots from triggering wrong re-plans. |
| Deterministic dataset ordering | `data/seed.py` `stable_review_count()` | md5-based, so itineraries are reproducible across runs and machines. |

### 1.2 There are TWO Streamlit apps and the wrong one is being run

| File | Contents | Verdict |
|---|---|---|
| `frontend/streamlit_app.py` | Chat input only. No map. No sidebar. Calls the FastAPI backend over HTTP. `tier` hardcoded to `1`. | **This is what produced the screenshot.** Feature-poor. |
| `app/streamlit_app.py` | Sidebar with city / days / budget / party_size / cuisine / allergy / max_walk / **tier** / **backend**. Has `st.map`, routes table, tool-backends panel, raw-state debug. Calls the orchestrator **in-process**. | **This is the one to develop.** |

Evidence that the screenshot is `frontend/streamlit_app.py`: the `Backend: http://127.0.0.1:8080` caption, the `- \`{slot}\`: {name} ${cost}` list format, and the raw-dict trace lines all exist only in that file. `app/streamlit_app.py` renders trace as `**{agent}**: {message}` and has a subtitle caption that the screenshot lacks.

**Consequence:** the map "missing from the UI" is not missing. It is in the other file.

---

## 2. Root cause chain (前因后果) — why Tier 1 and Tier 2 look identical

### 2.1 The evidence

The screenshot's `Backends` line is the system's own report of which data path executed:

```
Backends: {'restaurants': 'local_dataset', 'attractions': 'n/a', 'travel': 'n/a', 'fallback_events': 0}
```

Read field by field:

| Field | Value | What it proves |
|---|---|---|
| `restaurants` | `local_dataset` | Restaurants came from SQLite, not Places API. |
| `attractions` | `n/a` | `search_attractions()` was **never called**. |
| `travel` | `n/a` | `estimate_travel()` was **never called**. Routes API untouched. |
| `fallback_events` | `0` | No fallback was recorded. |

`attractions: n/a` and `travel: n/a` are decisive. In `_run_tier2_async()` both of those tools are always called. Therefore **the run was Tier 1, not Tier 2.**

Confirmed empirically: running `run_tier1()` with `FOODIE_DATA_BACKEND=local` and the S1 scenario reproduces the screenshot **byte for byte** — same six venues, same costs, `projected: 228.0`, same `Backends` dict.

### 2.2 The causal chain

```
frontend/streamlit_app.py:56   "tier": 1                                 (hardcoded)
        │
        └──> app/api.py:57     run_tier1() is always selected
                 │
                 └──> Tier 1 never calls the attraction or route agent
                          │
                          └──> EFFECT: attractions: 'n/a', travel: 'n/a'
                                       6 stops instead of 8
                                       0 route legs instead of 7
                                       no critic verdict

frontend/streamlit_app.py:57   "data_backend": "local" if allergies else "auto"
        │
        └──> demo prompt contains "peanut allergy"  →  allergies = ["peanut"]
                 │
                 └──> data_backend = "local"
                          │
                          └──> src/tools/__init__.py:25 _want_live() returns False
                                   │
                                   └──> EFFECT: zero Google API calls
                                                restaurants: 'local_dataset'
                                                fell_back=False so fallback_events stays 0
```

Two lines of code account for the entire symptom.

Secondary contributor: `app/api.py:23` sets `tier: int = Field(default=1, ...)`, so even an HTTP client that omits `tier` gets Tier 1.

### 2.3 An observability blind spot worth knowing

`FOODIE_DATA_BACKEND=auto` **with no `GOOGLE_MAPS_API_KEY`** produces an identical report to `FOODIE_DATA_BACKEND=local`:

```
{'restaurants': 'local_dataset', 'attractions': 'n/a', 'travel': 'n/a', 'fallback_events': 0}
```

Both paths make `_want_live()` return `False`, so `_record(..., fell_back=False)` runs and `fallback_events` never increments. **You cannot distinguish "key missing" from "local forced" by reading `Backends`.** Phase 0 fixes this by reporting the reason explicitly.

### 2.4 Verified Tier 1 vs Tier 2 difference

Tier 2 *is* substantively different. Measured on S1 (`Calgary / 2 days / $500 / party 2 / international / peanut / max_walk 2.0`), `FOODIE_DATA_BACKEND=local`:

| Dimension | Tier 1 | Tier 2 |
|---|---|---|
| Stops | 6 (meals only) | 8 (6 meals + 2 attractions) |
| Route legs | 0 | 7 |
| Critic | none | `verdict=revise`, 4 issues, `iteration=2` |
| `attractions` backend | `n/a` | `local_dataset` |
| `travel` backend | `n/a` | `haversine_fallback` |
| Budget projected | 228.0 / 500 | 174.0 / 500 |
| Concurrency | sequential | `asyncio.gather` |
| Venue selection rule | first by `rating DESC` | `min(avg_meal_cost)` |

The venue selection rule differing between tiers is itself a defect — see B7.

### 2.5 A correction to an earlier assumption

An earlier read of the screenshot suggested that meal-type filtering was broken, because a "Coffee House" was scheduled for lunch. **That was wrong.** Checked against `data/csv/calgary_restaurants.csv`:

- `r030, Ethiopian Coffee House, ethiopian, meal_types=lunch;dinner` — an Ethiopian restaurant whose *name* contains "Coffee House". Correctly placed at lunch.
- `r044, Inglewood Coffee Roasters, cafe, meal_types=breakfast` — correctly placed at breakfast only.

The `meal_types LIKE '%;meal;%'` filter in `local_catalog.search_restaurants()` works correctly. **Do not "fix" it.**

---

## 3. Bug inventory

Ordered by severity. Each row: what is wrong, where, why it matters.

| ID | Severity | Location | Cause | Effect |
|---|---|---|---|---|
| **B0a** | Blocker | `frontend/streamlit_app.py:56` | `"tier": 1` hardcoded | Tier 2 is unreachable from that UI |
| **B0b** | Blocker | `frontend/streamlit_app.py:57` | `"local" if allergies else "auto"` | Any allergy in the prompt silently disables all Google APIs |
| **B0c** | High | `app/api.py:23` | `tier` default is `1` | HTTP clients omitting `tier` get Tier 1 |
| **B1** | Critical | `src/orchestrator.py:376` `_compute_routes_async` + `:429`/`:445` append order | Restaurants are appended to `st.itinerary` first, then **all** attractions. Routes are built by zipping consecutive items across the whole flat list. | Route legs cross days, including **backwards**. Verified output: `Sunalta Shawarma (day1.dinner) -> Beltline Bakery (day2.breakfast)`, then `East Village Ramen (day2.dinner) -> Central Library (day1.attraction1)`. Geographically and chronologically meaningless. |
| **B2** | Critical | `src/orchestrator.py:391` `_deterministic_critic` + revision loop at `:456` | Critic detects `max_walk_km` violations, but replacement selection (`min(avg_meal_cost)` / first-unused) is **distance-blind**, so it cannot converge. | Verified: at `iteration=2` (the `CRITIC_MAX_ITERATIONS` cap) verdict is still `revise` with 4 unresolved travel issues — and the plan ships anyway with **no warning in the UI**. Constraint silently violated. |
| **B3** | Critical | `src/tools/routes_live.py:44` | FieldMask is `"routes.duration,routes.distanceMeters"` — no polyline requested. No `intermediates`, no `optimizeWaypointOrder`. | **No route geometry exists**, so no route can ever be drawn on a map. And there is **zero route optimization** in the project. |
| **B4** | High | `src/tools/__init__.py:17` | `_LAST_BACKEND` is a module-level mutable global, never reset per run. | Races under Tier 2's `asyncio.to_thread` parallelism (last-write-wins). `fallback_events` accumulates forever across requests in a long-lived server. |
| **B5** | High | `app/api.py:45` | `config.DATA_BACKEND = body.data_backend.lower()` mutates global config per request and never restores it. | A request omitting `data_backend` inherits the previous request's setting. Cross-request contamination. |
| **B6** | High | `app/api.py:16` `PlanRequest` | No `max_walk_km` field. Pydantic v2 ignores extra fields by default. | `max_walk_km` sent over HTTP is **silently dropped**; the critic always uses the 2.0 default. Only `app/streamlit_app.py` (in-process) can pass it. This is exactly the "how far will the user go" requirement. |
| **B7** | High | `src/orchestrator.py:562` (`price_level_max=2` hardcoded) and `:292` (`min(avg_meal_cost)`) | Tier 1 local branch caps price level at 2, excluding 14 of 60 venues (`$$$`, `$$$$`). Tier 2 picks the cheapest candidate. | Chronic underspend: 228/500 (Tier 1), 174/500 (Tier 2). Budget appears non-functional. Also the two tiers use **different** selection rules. |
| **B8** | Medium | `src/orchestrator.py:353` | `search_attractions(city, None, 2)` — `limit=2` hardcoded, and `days` identical queries are issued in parallel. | For `days >= 3`, day 3 onward gets no attraction (both results already consumed by dedup). |
| **B9** | Medium | `frontend/streamlit_app.py:35` and `app/streamlit_app.py:23` | Budget regex `r"\$?\s*(\d{2,5})"` matches the first 2–5 digit run. | `"10 days in Calgary, $500"` → `budget_total = 10.0`. |
| **B10** | Medium | `data/csv/*.csv` | CSVs have **no `city` column**. Dataset is Calgary-only (60 restaurants, 25 attractions). | `local` mode with `city="Vancouver"` or `"Montreal"` returns empty. Already noted in `eval/scenarios.json`. UI offers all three cities. |
| **B11** | Low | `src/orchestrator.py:378` | `mode="walk"` hardcoded in the route call. | Transport mode is not user-selectable and `max_walk_km` semantics can't extend to driving/transit. |

---

## 4. Phase 0 — Stop the bleeding

**Effort: ~30 minutes. Highest return per minute in this entire plan.**

### CAUSE

Two hardcoded values (B0a, B0b) make Tier 2 and all Google API calls unreachable from the UI being demoed. Nothing about the agent architecture is broken.

### EFFECT

Before: Tier 1 + local dataset, 6 stops, no routes, no attractions, no critic.
After: Tier 2 + live APIs selectable, 8 stops, 7 route legs, critic verdict, `google_places` / `google_routes` in the backend report.

### CHANGES

**0.1 — Verify the Google APIs first. Do not write code before this passes.**

```bash
python scripts/preflight.py
```

This script already exists and already tests everything needed: proxy env, loopback `no_proxy`, Fuel iX `/models`, model availability, a real `places_live.search_restaurants("Calgary","dinner")`, and a real `routes_live.estimate_travel()` printing `km / min`.

Interpreting failures:

| Symptom | Cause | Fix |
|---|---|---|
| `GOOGLE_MAPS_API_KEY not set` | `.env` missing or not at `KIT_ROOT` | `config.load_dotenv()` reads `KIT_ROOT/.env` only. Confirm the file location. |
| HTTP 403 | API not enabled, or billing off, or key restriction blocking | Enable **Places API (New)** — *not* legacy "Places API" — and **Routes API**. Both need billing. |
| HTTP 400 | Malformed body or FieldMask | Routes API **requires** `X-Goog-FieldMask`. Omitting it returns 400. |
| HTTP 429 | Quota | Rely on `FOODIE_CACHE=on`. |
| Proxy lines FAIL | Corporate network | See `.env.example` — `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY` are pre-filled for the TELUS network. |

**0.2 — `frontend/streamlit_app.py`: remove the two hardcoded values**

In `parse_prefs()` (line 27), delete lines 56–57 from the returned dict. Replace with values driven by new sidebar widgets:

```python
# Add above the chat input:
with st.sidebar:
    tier = st.selectbox("Agent tier", [2, 1], index=0)
    backend = st.selectbox("Data backend", ["auto", "live", "local"], index=0)
    if backend != "local":
        st.warning(
            "Live mode infers allergen risk from cuisine type only "
            "(Places API has no allergen fields). Use 'local' for graded "
            "allergen scenarios, where flags are explicit ground truth."
        )
```

Then `parse_prefs()` returns `"tier": tier, "data_backend": backend`.

**Do not** keep the `"local" if allergies else "auto"` coupling. The intent behind it was correct (Places has no allergen data, documented in `places_live.py`'s docstring and `config.ALLERGEN_RISK_CUISINES`), but forcing the backend silently is the wrong mechanism. Make it a visible warning plus an explicit user choice.

**0.3 — `app/api.py`: fix the tier default**

Line 23: `tier: int = Field(default=1, ge=1, le=2)` → `default=2`.

**0.4 — Make the backend report self-explaining (closes the §2.3 blind spot)**

In `src/tools/__init__.py`, extend `_record()` and `_want_live()` so the report distinguishes *why* live was skipped:

```python
def _live_decision() -> tuple[bool, str]:
    if config.DATA_BACKEND == "local":
        return False, "forced_local"
    if config.DATA_BACKEND == "live":
        return True, "forced_live"
    if not config.LIVE_DATA_AVAILABLE:
        return False, "auto_no_api_key"
    return True, "auto_key_present"
```

Add a `"live_decision"` key to the report. After this, `Backends` will read e.g.
`{'restaurants': 'local_dataset', 'live_decision': 'auto_no_api_key', ...}` instead of being ambiguous.

**0.5 — Add an A/B regression script**

Create `scripts/tier_diff.py`:

```python
#!/usr/bin/env python3
"""Run the same request through Tier 1 and Tier 2 and diff the results.

WHY THIS EXISTS: the two tiers produced byte-identical output for the entire
first week of the project because the UI hardcoded tier=1. If this script
reports no difference, Tier 2 is not wired up. Run it after every change.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.orchestrator import run_tier1, run_tier2

REQUEST = {
    "city": "Calgary", "days": 2, "budget_total": 500, "party_size": 2,
    "cuisines": ["international"], "allergies": ["peanut"], "max_walk_km": 2.0,
}


def summarize(state) -> dict:
    return {
        "stops": len(state.itinerary),
        "route_legs": len(state.routes),
        "budget": state.budget,
        "tool_backends": state.meta.get("tool_backends"),
        "critic_verdict": (state.critic or {}).get("verdict"),
        "critic_issues": len((state.critic or {}).get("issues", [])),
        "venues": [(item["slot"], item["name"]) for item in state.itinerary],
    }


def main() -> int:
    one = summarize(run_tier1(dict(REQUEST)))
    two = summarize(run_tier2(dict(REQUEST)))
    print("TIER 1:", json.dumps(one, indent=2, default=str))
    print("TIER 2:", json.dumps(two, indent=2, default=str))
    if one == two:
        print("\nFAIL: Tier 1 and Tier 2 are identical. Tier 2 is not wired up.")
        return 1
    print(f"\nOK: tiers differ. stops {one['stops']} -> {two['stops']}, "
          f"legs {one['route_legs']} -> {two['route_legs']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

**0.6 — Run the right UI**

```bash
python data/seed.py                       # build data/foodie.sqlite (gitignored)
streamlit run app/streamlit_app.py        # NOT frontend/streamlit_app.py
```

In the sidebar set backend to `live` and tier to `2`.

### ACCEPTANCE

```bash
python scripts/preflight.py        # → "PREFLIGHT GREEN"
python scripts/tier_diff.py        # → "OK: tiers differ. stops 6 -> 8, legs 0 -> 7"
```

And in `app/streamlit_app.py` with backend `live`, the Tool backends panel shows:

```json
{"restaurants": "google_places", "attractions": "google_places",
 "travel": "google_routes", "live_decision": "forced_live", "fallback_events": 0}
```

---

## 5. Phase 1 — Unified request model

**Effort: ~1 hour. This is the foundation for every later phase.**

### CAUSE

The request shape is defined three times and inconsistently: `PlanRequest` in `app/api.py:16`, `parse_prefs()` in `frontend/streamlit_app.py:27`, and an inline dict in `app/streamlit_app.py:135`. `max_walk_km` exists in only one of them (B6). There are no date fields at all — only a `days` integer — so weekday-aware planning is impossible. There is no concept of a trip origin, so no route can start from the user's location.

### EFFECT

Before: `max_walk_km` silently dropped over HTTP; no dates; no origin; attraction type and search radius not expressible; global config mutated per request (B5).
After: one validated schema shared by API and both UIs; dates and weekdays derived; origin is first-class; all user constraints reach the agents.

### CHANGES

**1.1 — Create `src/request_model.py`**

```python
"""Single source of truth for the trip request shape.

WHY: the request was previously defined three times (app/api.py, both
Streamlit apps) with different fields. max_walk_km existed in only one of
them, so it was silently dropped over HTTP (Pydantic ignores extra fields).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, Field, computed_field

TransportMode = Literal["WALK", "DRIVE", "TRANSIT", "BICYCLE"]


class Origin(BaseModel):
    """Where the trip starts each day. Drives route optimization."""
    address: str | None = None
    lat: float | None = None
    lon: float | None = None
    label: str = "Your location"

    @property
    def is_resolved(self) -> bool:
        return self.lat is not None and self.lon is not None


class TripRequest(BaseModel):
    city: str = "Calgary"
    start_date: date | None = None
    days: int = Field(default=2, ge=1, le=7)

    origin: Origin = Field(default_factory=Origin)

    budget_total: float = Field(default=500, gt=0)
    party_size: int = Field(default=2, ge=1)

    cuisines: list[str] = Field(default_factory=lambda: ["international"])
    attraction_types: list[str] = Field(default_factory=list)
    allergies: list[str] = Field(default_factory=list)

    # "How far will the user go" — three DISTINCT constraints. See §5.2.
    search_radius_km: float = Field(default=5.0, gt=0)
    max_leg_minutes: float = Field(default=25.0, gt=0)
    max_daily_travel_minutes: float = Field(default=120.0, gt=0)

    transport_mode: TransportMode = "WALK"
    attractions_per_day: int = Field(default=1, ge=0, le=3)

    tier: int = Field(default=2, ge=1, le=2)
    data_backend: Literal["auto", "live", "local"] = "auto"

    @computed_field
    @property
    def dates(self) -> list[date]:
        if self.start_date is None:
            return []
        return [self.start_date + timedelta(days=i) for i in range(self.days)]

    @computed_field
    @property
    def weekdays(self) -> list[str]:
        """['sat', 'sun'] — lowercase 3-letter keys matching data/seed.py DAYS."""
        return [d.strftime("%a").lower() for d in self.dates]

    @computed_field
    @property
    def day_labels(self) -> list[str]:
        """['Day 1 · Sat Sep 5', ...] for UI display."""
        if not self.dates:
            return [f"Day {i + 1}" for i in range(self.days)]
        return [f"Day {i + 1} · {d.strftime('%a %b %-d')}"
                for i, d in enumerate(self.dates)]
```

Note: `weekdays` deliberately emits lowercase 3-letter keys because `data/seed.py:26` `DAYS` uses `("mon", "tue", ...)` and `local_catalog.is_open_at()` does `hours.get(weekday[:3].lower())`. This makes opening-hours checks work without a translation layer.

**1.2 — `app/api.py`: replace `PlanRequest` with `TripRequest`**

- Delete the local `PlanRequest` class (lines 16–27).
- Import and use `TripRequest`.
- **Fix B5**: stop mutating `config.DATA_BACKEND`. Delete lines 44–46. Instead pass the backend down explicitly. Simplest correct approach — a `contextvars.ContextVar` in `src/config.py` read by `_live_decision()`:

```python
# src/config.py
import contextvars
_backend_override: contextvars.ContextVar[str | None] = \
    contextvars.ContextVar("backend_override", default=None)

def current_backend() -> str:
    return (_backend_override.get() or DATA_BACKEND).lower()

def set_backend_override(value: str | None):
    return _backend_override.set(value)
```

Then `src/tools/__init__.py:_live_decision()` reads `config.current_backend()` instead of `config.DATA_BACKEND`. `app/api.py` sets the override per request and resets the token in a `finally` block. This is `asyncio`-safe, which matters because Tier 2 runs concurrently.

**1.3 — Thread the new fields through `src/orchestrator.py`**

- `_deterministic_critic()` (line 391): replace `request.get("max_walk_km", 2.0)` with `max_leg_minutes` from the request, and compare against `leg["minutes"]` rather than `leg["km"]`. Minutes are mode-independent; km are not. Keep accepting `max_walk_km` as a deprecated alias for one release so `eval/scenarios.json` keeps passing.
- Add a `max_daily_travel_minutes` check: sum each day's leg minutes and raise an issue on the day, not on a single slot. This needs a new issue type — see 1.4.
- `_execute_attractions_tier2()` (line 351): accept `attraction_types` and `attractions_per_day`; replace hardcoded `limit=2` with `limit=max(2, days * attractions_per_day + 2)` (**fix B8**).

**1.4 — `src/state.py`: extend the slot vocabulary**

`slot_ids()` already supports `attractions_per_day`, but `_plan_with_llm()` (line 40) calls `slot_ids(days, attractions_per_day=0)`, so attraction slots are excluded from the planner's valid set. Update it to pass the real value.

Add `origin` as a recognized non-revisable pseudo-slot (`day{d}.origin`) so route legs can reference it without `is_valid_slot()` rejecting critic output.

For the day-level budget/travel issues from 1.3, allow a `day{d}` scope in `is_valid_slot()`.

**1.5 — `frontend/streamlit_app.py`: fix the budget regex (B9)**

```python
# BEFORE: r"\$?\s*(\d{2,5})"  → "10 days ... $500" yields 10
# AFTER: require a currency marker, and ignore digits followed by a unit word
m = re.search(r"(?:\$|\bbudget\b\D{0,10})\s*(\d{2,6})", lower)
```

Apply the same fix in `app/streamlit_app.py:23` `parse_prompt()`. Add a unit test with the case `"10 days in Calgary, $500"` → `budget_total == 500.0`.

### ACCEPTANCE

```bash
python - <<'EOF'
from datetime import date
from src.request_model import TripRequest
r = TripRequest(city="Calgary", start_date=date(2026, 9, 5), days=3)
assert r.weekdays == ["sat", "sun", "mon"], r.weekdays
assert len(r.day_labels) == 3
print(r.day_labels)
EOF

curl -s -X POST localhost:8080/plan -H 'Content-Type: application/json' \
  -d '{"city":"Calgary","days":2,"max_leg_minutes":15,"tier":2}' | python -m json.tool
```

`max_leg_minutes` must appear in the echoed request and must influence the critic's issue list. Previously it was dropped.

---

## 6. Phase 2 — Route geometry and per-day routing

**Effort: 1–2 hours. This is what makes a map possible.**

### CAUSE

Two independent defects:

1. **B3** — `src/tools/routes_live.py:44` requests only `routes.duration,routes.distanceMeters`. Google will happily return the encoded polyline, but it is never asked for. **Without geometry, no route line can be drawn, ever.**
2. **B1** — `src/orchestrator.py:376` `_compute_routes_async()` zips consecutive items of a flat `st.itinerary`. Because attractions are appended after **all** restaurants (`:429` then `:445`), legs cross days and even run backwards.

Verified broken output:

```
Inglewood Coffee Roasters (day1.breakfast) -> Downtown Falafel Cart (day1.lunch)     2.95 km   ok
Downtown Falafel Cart     (day1.lunch)     -> Sunalta Shawarma      (day1.dinner)    2.64 km   ok
Sunalta Shawarma          (day1.dinner)    -> Beltline Bakery       (day2.breakfast) 1.73 km   CROSSES DAY
Beltline Bakery           (day2.breakfast) -> Ethiopian Coffee House(day2.lunch)     0.49 km   ok
Ethiopian Coffee House    (day2.lunch)     -> East Village Ramen    (day2.dinner)    3.71 km   ok
East Village Ramen        (day2.dinner)    -> Central Library       (day1.attraction1) 0.21 km  GOES BACKWARDS
Central Library           (day1.attraction1) -> Island Park Loop    (day2.attraction1) 2.90 km  CROSSES DAY
```

### EFFECT

Before: no polyline; 7 legs of which 3 are nonsensical; no optimization; attractions not in the day timeline.
After: per-day routes starting at the user's origin, chronologically ordered, optionally waypoint-optimized, each leg carrying geometry that a map can render.

### CHANGES

**2.1 — `src/tools/routes_live.py`: add polyline to the FieldMask**

Line 44:

```python
# BEFORE
"X-Goog-FieldMask": "routes.duration,routes.distanceMeters",
# AFTER
"X-Goog-FieldMask": (
    "routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline"
),
```

Add to the result dict (line 64): `"polyline": r0.get("polyline", {}).get("encodedPolyline")`.

**This is a one-line change that is the difference between having and not having a drawable map.**

**2.2 — `src/tools/routes_live.py`: add a multi-stop optimized route function**

```python
def compute_day_route(origin: dict, stops: list[dict], mode: str = "WALK",
                      optimize: bool = True) -> dict:
    """One Routes API call for a whole day, optionally reordering the stops.

    origin / stops entries need 'lat', 'lon', and 'name'.
    Returns {"order": [...], "legs": [...], "totals": {...}, "source": ...}

    'order' is the index list into `stops` in visiting order. When
    optimize=True this comes from Google's optimizedIntermediateWaypointIndex,
    so we never hand-roll a TSP solver.
    """
```

Request body shape:

```python
payload = {
    "origin":      {"location": {"latLng": {"latitude": origin["lat"],
                                            "longitude": origin["lon"]}}},
    "destination": {"location": {"latLng": {"latitude": stops[-1]["lat"],
                                            "longitude": stops[-1]["lon"]}}},
    "intermediates": [
        {"location": {"latLng": {"latitude": s["lat"], "longitude": s["lon"]}}}
        for s in stops[:-1]
    ],
    "travelMode": mode,
    "optimizeWaypointOrder": optimize,
    "computeAlternativeRoutes": False,
    "languageCode": "en-US",
    "units": "METRIC",
}
```

Required FieldMask for this call:

```
routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline,
routes.optimizedIntermediateWaypointIndex,
routes.legs.duration,routes.legs.distanceMeters,
routes.legs.polyline.encodedPolyline
```

Constraints and cost — **verify against current Google documentation before relying on these**, since limits and pricing change:

- `optimizeWaypointOrder` is billed on a higher SKU tier than a basic route. Keep `FOODIE_CACHE=on` and only call it on explicit form submit.
- `optimizeWaypointOrder` is not compatible with `via` waypoints and has its own waypoint-count ceiling, lower than the general intermediates limit.
- With `attractions_per_day=1` and 3 meals, a day has ~4–5 stops plus the origin. Well inside any limit.

**2.3 — `src/tools/local_catalog.py`: keep offline mode drawable**

Add a matching `compute_day_route()` so the offline path fills the same contract (this mirrors the existing design where `estimate_travel()` uses haversine):

- Order stops with greedy nearest-neighbour from the origin (adequate for 4–6 stops; do not add OR-Tools).
- Synthesize a straight-line "polyline" as a two-point list per leg, and set `"source": "haversine_fallback"`.
- Encode it in the same Google polyline format so the UI decoder has one code path. A ~30-line pure-Python encoder keeps the project's stdlib-only core intact (see `README.md`).

**2.4 — `src/tools/__init__.py`: expose `compute_day_route` through the facade**

Follow the existing pattern exactly: try live, record backend, fall back to local, record `fell_back`. Register it in `TOOL_IMPLS` and add a matching entry to `TOOL_SCHEMAS` in `src/state.py` so the LLM tool loop can call it.

**2.5 — `src/orchestrator.py`: restructure routing (fixes B1)**

Replace `_compute_routes_async()` (line 376) with a day-grouped version:

```python
DAY_ORDER = ("origin", "breakfast", "attraction1", "lunch",
             "attraction2", "dinner", "attraction3")

def _sort_day_stops(items: list[dict]) -> list[dict]:
    """Chronological order within a day. Attractions interleave between meals
    instead of being appended after every restaurant (the B1 bug)."""
    rank = {name: i for i, name in enumerate(DAY_ORDER)}
    return sorted(items, key=lambda it: rank.get(it["slot"].split(".", 1)[1], 99))
```

New routing flow:

1. Group `st.itinerary` by the `dayN` prefix.
2. Sort each day with `_sort_day_stops()`.
3. Prepend the resolved `origin` as `day{N}.origin` when `request.origin.is_resolved`.
4. Call `compute_day_route()` **once per day**.
5. If `optimize=True`, reorder that day's `st.itinerary` entries to match the returned `order`, but **only within a meal category** — do not let the optimizer move dinner before breakfast. Optimize the *attraction* positions and the travel path, not the meal sequence.
6. Store `st.routes` as a list of day objects:

```python
st.routes = [{
    "day": 1,
    "date": "2026-09-05",
    "weekday": "sat",
    "mode": "WALK",
    "legs": [{"from_slot": ..., "to_slot": ..., "from": ..., "to": ...,
              "km": ..., "minutes": ..., "polyline": ..., "source": ...}],
    "totals": {"km": ..., "minutes": ...},
    "optimized": True,
}]
```

**This changes the shape of `st.routes`.** Update every consumer:
- `app/streamlit_app.py:render_result()` "Routes" expander (currently `pd.DataFrame(state.routes)`).
- `_deterministic_critic()` (line 391), which iterates `st.routes` flat.
- `eval/acceptance.py` if it touches routes.

**2.6 — Fix the transport mode (B11)**

Remove the hardcoded `"walk"` at line 378. Read `request.transport_mode`. Map to Routes API values: `WALK`, `DRIVE`, `TRANSIT`, `BICYCLE`. `local_catalog.estimate_travel()` currently knows only `walk` (4.5 km/h) and `drive` (25 km/h) — add `transit` (~18 km/h effective) and `bicycle` (~15 km/h).

### ACCEPTANCE

```bash
python - <<'EOF'
from src.orchestrator import run_tier2
st = run_tier2({"city": "Calgary", "days": 2, "budget_total": 500,
                "party_size": 2, "cuisines": ["international"],
                "allergies": ["peanut"], "max_leg_minutes": 25,
                "transport_mode": "WALK"})

assert isinstance(st.routes, list) and st.routes and "legs" in st.routes[0], \
    "st.routes must be day-grouped"

for day in st.routes:
    for leg in day["legs"]:
        # No leg may cross days.
        assert leg["from_slot"].split(".")[0] == leg["to_slot"].split(".")[0], \
            f"cross-day leg: {leg['from_slot']} -> {leg['to_slot']}"
        assert leg.get("polyline"), f"missing geometry: {leg}"
print("OK:", [(d["day"], len(d["legs"]), d["totals"]) for d in st.routes])
EOF
```

Must pass in **both** `FOODIE_DATA_BACKEND=live` and `=local`.

---

## 7. Phase 3 — Make the critic converge, and use the budget

**Effort: 1–2 hours.**

### CAUSE

**B2** — The critic correctly identifies `max_walk_km` violations, but the replacement search is distance-blind. `_pick_local_task()` (line 279) picks `min(avg_meal_cost)`; the Tier-1 path picks the first by rating. Neither considers where the previous stop is. So the revision loop cannot fix a travel issue, burns both iterations, and **ships a plan that violates the user's stated constraint with no warning**.

**B7** — `run_tier1()` line 562 hardcodes `price_level_max=2`, excluding 14 of 60 venues. Combined with cheapest-first selection this produces 174–228 of a 500 budget. The `_max_price_level()` helper (line 129) already exists and is correct — it is simply bypassed in the local branch.

### EFFECT

Before: `verdict=revise` with 4 unresolved issues at `iteration=2`, shipped silently; 35–46% of budget used.
After: critic converges or the UI shows an explicit unresolved-constraint banner; budget utilisation reflects the user's actual limit.

### CHANGES

**3.1 — Make candidate search distance-aware**

Add a `near` parameter to the facade's `search_restaurants()` / `search_attractions()`:

```python
near: tuple[float, float] | None = None   # (lat, lon) anchor
within_km: float | None = None
```

- **Live path** (`places_live.py`): add `locationRestriction` to the `searchText` body:
  ```python
  "locationRestriction": {"circle": {
      "center": {"latitude": lat, "longitude": lon},
      "radius": within_km * 1000,
  }}
  ```
  This narrows candidates at the API level — cheaper and more relevant than filtering after the fact.
- **Local path** (`local_catalog.py`): filter with the existing haversine maths already in `estimate_travel()`, then sort by distance ascending as a secondary key after rating.

**3.2 — Rewrite the revision step to pass the anchor**

In `_run_tier2_async()` around line 456: when a critic issue has `type == "travel"`, resolve the **previous** stop in that day's sorted order and pass it as `near=(prev.lat, prev.lon)` with `within_km` derived from `max_leg_minutes` and the mode's speed. Without this the loop cannot converge — this is the core of B2.

**3.3 — Unify venue selection across tiers**

Create one scoring function used by Tier 1, Tier 2, and revisions:

```python
def score_candidate(cand: dict, *, budget_remaining: float, party_size: int,
                    anchor: tuple[float, float] | None) -> float:
    """Higher is better. Replaces three inconsistent selection rules:
      - run_tier1 local branch: first by rating DESC
      - _pick_local_task:       min(avg_meal_cost)
      - revision fallback:      first unused
    Prefers the best-rated venue that FITS the remaining budget, penalised by
    distance from the anchor. Fixes the chronic underspend (B7).
    """
```

Weighting guidance: rating dominant; hard-reject if `avg_meal_cost * party_size > budget_remaining`; subtract a distance penalty scaled by `max_leg_minutes`. Keep it deterministic — no randomness, or `scripts/tier_diff.py` becomes useless.

**3.4 — Un-hardcode the price ceiling (B7)**

`src/orchestrator.py:562`: replace `price_level_max=2` with `price_level_max=_max_price_level(task["budget_per_person"])`. Note `data/seed.py:price_level()` returns `len(band)` **uncapped**, so a `$$$$$` venue is level 5 while `_max_price_level()` maxes at 4 — either cap in `seed.py` or extend `_max_price_level()`. Pick one and comment it.

**3.5 — Never ship an unresolved constraint silently**

At the end of the critic loop:

```python
if critic.get("verdict") == "revise" and critic.get("issues"):
    st.meta["unresolved_issues"] = critic["issues"]
    st.log("critic", f"SHIPPED WITH {len(critic['issues'])} UNRESOLVED ISSUES "
                     f"after {config.CRITIC_MAX_ITERATIONS} iterations")
```

The UI renders this as a warning banner (Phase 4). A judge finding a violated constraint that the system did not flag is much worse than the system admitting it.

**3.6 — Add opening-hours validation (now possible)**

`local_catalog.is_open_at(details, weekday, hhmm)` already exists but is never called. With `TripRequest.weekdays` from Phase 1, wire it into the critic: check each stop against its day's weekday and a nominal meal time (breakfast 08:00, lunch 12:30, dinner 19:00). The dataset has `is_trap` fixtures for exactly this — `r005 Jade Lantern Dim Sum` and `a002 Prairie Heritage Museum` are both `closed_monday`. It returns `None` when hours are unknown; treat `None` as pass, `False` as an issue.

### ACCEPTANCE

```bash
python - <<'EOF'
from datetime import date
from src.orchestrator import run_tier2

st = run_tier2({"city": "Calgary", "days": 2, "start_date": date(2026, 9, 5),
                "budget_total": 500, "party_size": 2,
                "cuisines": ["international"], "allergies": ["peanut"],
                "max_leg_minutes": 25, "transport_mode": "WALK"})

# 1. Critic converged, or the violation is explicitly recorded.
unresolved = st.meta.get("unresolved_issues", [])
assert st.critic.get("verdict") == "approved" or unresolved, \
    "critic must either approve or record unresolved issues"

# 2. No allergen leak anywhere in the candidate pool (the graded check).
leaks = [(s, c["name"]) for s, cands in st.candidates.items() for c in cands
         if (c.get("dietary_flags") or {}).get("peanut_risk")]
assert not leaks, f"ALLERGEN LEAK: {leaks}"

# 3. Budget is actually being used.
util = st.budget["projected"] / st.budget["limit"]
assert util > 0.60, f"underspending: {util:.0%} of budget used"
print(f"OK: verdict={st.critic.get('verdict')} util={util:.0%} "
      f"unresolved={len(unresolved)}")
EOF

python eval/acceptance.py    # existing scenario suite must still pass
```

---

## 8. Phase 4 — The UI rebuild

**Effort: 2–3 hours. All work in `app/streamlit_app.py`.**

### CAUSE

Three separate problems:

1. **Free-text-only input.** `frontend/streamlit_app.py` parses a sentence with regex, so the user cannot express dates, attraction type, radius, or origin at all. `app/streamlit_app.py` has a sidebar but keeps `st.chat_input` as the trigger.
2. **`st.map` cannot draw lines.** It is a thin `ScatterplotLayer` wrapper — points only. Route geometry from Phase 2 has nowhere to render.
3. **No `st.form`, which costs real money.** Streamlit re-runs the entire script on every widget interaction. Without a form, dragging a slider fires a fresh Places + Routes round trip. `src/tools/cache.py` absorbs repeats but not first-time variations.

### EFFECT

Before: no structured input; points-only map; API calls on every widget twitch.
After: one submit → one planning run; routes drawn per day; origin visible; `source` provenance visible per row so live-vs-local and Tier 1-vs-2 differences are obvious at a glance.

### CHANGES

**4.1 — Wrap all inputs in `st.form`**

Non-negotiable, for cost as much as UX. Widgets inside a form do not trigger reruns; only `st.form_submit_button` does.

```python
with st.form("trip_form"):
    col_a, col_b = st.columns(2)
    with col_a:
        city = st.text_input("City", "Calgary")
        date_range = st.date_input("Trip dates", value=(), min_value=date.today())
        origin_text = st.text_input(
            "Starting point (address, hotel, or landmark)",
            placeholder="e.g. 120 9 Ave SE, Calgary",
            help="Routes are planned from here each day.")
        transport = st.radio("Getting around",
                             ["WALK", "TRANSIT", "BICYCLE", "DRIVE"],
                             horizontal=True)
    with col_b:
        budget = st.number_input("Total budget (CAD)", min_value=1.0,
                                 value=500.0, step=25.0)
        party = st.number_input("Party size", 1, 20, 2)
        cuisines = st.multiselect("Restaurant types", RESTAURANT_TYPES,
                                  default=["international"])
        attraction_types = st.multiselect("Attraction types", ATTRACTION_TYPES)
        allergies = st.multiselect("Allergies (hard exclusion)",
                                   CANONICAL_ALLERGENS)

    with st.expander("How far will you go?"):
        search_radius = st.slider("Search radius from city centre (km)",
                                  1.0, 25.0, 5.0, 0.5)
        max_leg = st.slider("Max travel between stops (min)", 5, 90, 25, 5)
        max_daily = st.slider("Max total travel per day (min)", 30, 300, 120, 15)

    submitted = st.form_submit_button("Plan my trip", type="primary")
```

Derive `days` from `date_range`, and render the weekday labels read-only so the user sees which days those are — this answers "what day are those days" without asking them to type it:

```python
if len(date_range) == 2:
    days = (date_range[1] - date_range[0]).days + 1
    req = TripRequest(start_date=date_range[0], days=days, ...)
    st.caption(" · ".join(req.day_labels))   # "Day 1 · Sat Sep 5 · Day 2 · Sun Sep 6"
```

**4.2 — Populate the dropdowns from the dataset, not hardcoded lists**

Read the vocabulary at import time so the options always match reality (verified values in Appendix A):

- `RESTAURANT_TYPES` — the 30 distinct `cuisine` values plus the `CUISINE_ALIASES` bucket keys (`asian`, `international`) from `local_catalog.py:29`.
- `ATTRACTION_TYPES` — the 8 distinct `category` values.
- `CANONICAL_ALLERGENS` — import directly from `data/seed.py` (9 values). Do not retype it; a typo here silently disables an allergen filter.

**4.3 — Resolve the origin without adding an API**

Use the Places `searchText` client already in `places_live.py` — same endpoint, same key, no new enablement:

```python
def resolve_origin(address: str, city: str) -> Origin:
    """Address -> lat/lon via the Places searchText endpoint we already use.

    WHY NOT browser geolocation as the primary path: Streamlit has no built-in
    geolocation; third-party components need HTTPS (localhost is exempt) and
    raise a browser permission dialog. A denied or hung dialog is a common way
    to lose a live demo. Text + geocode always works.
    """
```

Offer browser geolocation as an **optional** "Use my location" button only, and always leave the text field as a fallback. If the origin cannot be resolved, fall back to the city centre and say so in the UI — do not fail the whole plan.

**4.4 — Replace `st.map` with `st.pydeck_chart`**

`render_map()` at line 64. `pydeck` ships with Streamlit, so no new dependency for the chart itself.

Layers:

| Layer | Data | Styling |
|---|---|---|
| `PathLayer` | one path per day, from the decoded polylines in `st.routes[i]["legs"]` | distinct colour per day, `width_min_pixels=3` |
| `ScatterplotLayer` | restaurants | colour by meal slot (breakfast / lunch / dinner) |
| `ScatterplotLayer` | attractions | separate colour |
| `IconLayer` or large-radius `ScatterplotLayer` | **origin** | visually dominant, clearly distinct |

Tooltip must include `name`, `cost`, `rating`, and **`source`**.

Polyline decoding: add a `decode_polyline()` helper. Either add `polyline` to `requirements.txt`, or write the ~30-line pure-Python decoder — the latter fits the project's stated "core = stdlib" principle in `README.md`. Whichever you choose, the local backend must emit the same encoding (Phase 2.3) so there is exactly one decode path.

Set the initial view to the bounding box of all stops plus the origin, not a hardcoded zoom (`st.map(..., zoom=12)` today).

Add a day filter (`st.multiselect` or per-day toggles) so a 5-day trip is still readable.

**4.5 — Day tabs with a real timeline**

Replace the flat `render_itinerary()` dataframe (line 41) with `st.tabs(req.day_labels)`. Per day:

- Ordered stop list using the Phase 2 chronological sort.
- Travel time and distance **between** consecutive stops, from `st.routes[i]["legs"]`.
- Running cost total.
- A `source` badge per row (`google_places` vs `local_dataset`). **This is the single cheapest way to make Tier 1 vs Tier 2 and live vs local visible** — the original complaint that "it all looks the same" was largely a display problem.
- Day totals from `st.routes[i]["totals"]`, coloured red if over `max_daily_travel_minutes`.

**4.6 — Diagnostics panel in the sidebar**

Add `GET /diagnostics` to `app/api.py` reusing the `scripts/preflight.py` logic:

```json
{
  "tier_default": 2,
  "live_decision": "forced_live",
  "maps_key_set": true,
  "places_api": {"ok": true, "latency_ms": 180},
  "routes_api": {"ok": false, "http_status": 403, "reason": "API not enabled"},
  "local_dataset_rows": {"restaurants": 60, "attractions": 25},
  "cache_enabled": true
}
```

Render it in the sidebar. This turns the ambiguity of §2.3 into a visible signal and is what a judge will ask about.

**4.7 — Unresolved-constraint banner**

If `st.meta["unresolved_issues"]` is non-empty, render `st.warning()` naming each violated constraint. Pairs with Phase 3.5.

**4.8 — Allergen advisory**

When any allergy is selected, always render:

> Filtered N venues for: peanut. Google Places has no allergen data, so live-mode results infer risk from cuisine type only. **Always confirm directly with the restaurant.**

Report the actual excluded count. `places_live.py` already sets `verify_with_restaurant=True` on every live result — surface it rather than leaving it in the JSON. This is a safety-relevant claim and must not be implied as a guarantee.

**4.9 — Decide the fate of `frontend/streamlit_app.py`**

Two maintained UIs will drift. Recommended: keep `frontend/` as the thin Cloud Run deployable (it correctly avoids putting the Fuel iX key in the browser) but have it POST the same `TripRequest` and render the same components. Extract the shared render functions into `app/ui_components.py` and import from both. Otherwise, delete `frontend/streamlit_app.py` and adjust `frontend/Dockerfile`.

### ACCEPTANCE

Manual, with `FOODIE_DATA_BACKEND=live`, tier 2, a real origin address, a 2-day date range:

1. Map shows the origin marker, restaurant markers, attraction markers, and **one coloured path per day**.
2. Day tabs show weekday labels derived from the date picker.
3. Every itinerary row shows a `source` badge reading `google_places`.
4. Sidebar diagnostics shows `places_api.ok = true` and `routes_api.ok = true`.
5. Changing a slider **without** pressing submit produces **no** new API call — verify via `data/api_cache.sqlite` row count or a print in `cache.py`.
6. Repeat with `FOODIE_DATA_BACKEND=local`: map still draws paths (straight-line), badges read `local_dataset`, no crash.

---

## 9. Phase 5 — Demo hardening

**Effort: ~1 hour. Do not skip; several items protect against total demo failure.**

### CAUSE

The demo depends on network, VPN/proxy, a valid key, unexpired quota, and a dataset that only covers Calgary (B10). Any one of these failing on stage takes the whole demo down.

### EFFECT

Before: single points of failure with no rehearsed fallback.
After: cache-warmed live demo, a one-env-var offline escape hatch, and a frozen golden response as the last resort.

### CHANGES

**5.1 — Warm the cache before demoing**

`src/tools/cache.py` persists to `data/api_cache.sqlite`. Run the **exact** demo request once with live keys beforehand; the responses persist and the demo survives dead Wi-Fi. Add to the runbook:

```bash
FOODIE_DATA_BACKEND=live FOODIE_CACHE=on python scripts/warm_cache.py
```

Note `data/api_cache.sqlite` is gitignored — warm on the machine you will demo from.

**5.2 — Rehearse the offline escape hatch**

`FOODIE_DATA_BACKEND=local` is already built and already works. Verify it end-to-end **after** the Phase 4 UI changes, because the local path must also produce polylines (Phase 2.3). An untested fallback is not a fallback.

**5.3 — Add `DEMO_MODE`**

Add `FOODIE_DEMO_MODE=on` to `src/config.py`. When set, `/plan` returns a frozen known-good response from `data/golden_plan.json`. Generate it once from a good live run. This is the last resort if both live and local break.

**5.4 — Handle the Calgary-only dataset in the UI (B10)**

The CSVs have no `city` column; the dataset is Calgary-only. `eval/scenarios.json` already documents that S2 (Vancouver) and S3 (Montreal) need live mode.

In the city selector, when the backend resolves to `local` and the city is not Calgary, either disable submit or show:

> The offline dataset covers Calgary only. Vancouver and Montreal need live mode (`FOODIE_DATA_BACKEND=live`).

Better long-term: add a `city` column to the CSVs and let `data/seed.py` auto-discover — the seeder already supports `<city>_restaurants.csv` / `<city>_attractions.csv` per its docstring.

**5.5 — Key hygiene**

`.env` is already in `.gitignore` — confirmed, along with `data/foodie.sqlite` and `data/api_cache.sqlite`. Neither SQLite file is committed. Additionally:

- Restrict the API key by IP or HTTP referrer in the GCP console.
- Restrict it to **Places API (New)** and **Routes API** only.
- Never put the key in slides or screenshots — check your demo screenshots before submitting.

**5.6 — Empty and error states**

Each needs a specific message, not a stack trace:

| Condition | Message |
|---|---|
| No candidates after filters | "No venues match. Try widening the radius or removing a cuisine filter." |
| Routes API returns no route | "No route found between two stops. Try a different transport mode." |
| Over budget | Show the overage; suggest raising the budget or reducing party size. |
| Origin unresolved | "Couldn't find that address. Using city centre instead." |
| Live API 403/429 | Name the status and that it fell back to the local dataset. |

**5.7 — Update the docs**

- `README.md`: add the map screenshot, `streamlit run app/streamlit_app.py` (explicitly noting it is **not** `frontend/`), and `python scripts/preflight.py` as the first debugging step.
- `BUILD_AND_DEPLOY_RUNBOOK.md`: add the cache-warming step and the demo-day fallback ladder (live → local → DEMO_MODE).
- `PROJECT_CONTEXT.md`: lines 158–159 say "Tier 2 not yet built" and "Chat-window UI not yet added". Both are now stale.

### ACCEPTANCE

```bash
# Ladder test — all three must produce a renderable plan
FOODIE_DATA_BACKEND=live  python scripts/tier_diff.py
FOODIE_DATA_BACKEND=local python scripts/tier_diff.py
FOODIE_DEMO_MODE=on       python scripts/tier_diff.py

# Offline simulation: warm the cache, then pull the network
FOODIE_DATA_BACKEND=live python scripts/warm_cache.py
# disconnect network
FOODIE_DATA_BACKEND=live streamlit run app/streamlit_app.py   # must still render
```

---

## 10. Priority and sequencing

| Priority | Item | Effort | Why this rank |
|---|---|---|---|
| **P0** | Phase 0 (entire) | 30 min | Recovers all of Tier 2's behaviour by deleting two hardcoded values |
| **P0** | Phase 2.1 (polyline FieldMask) | 5 min | One line; the difference between a drawable and undrawable map |
| **P0** | Phase 2.5 (per-day routing, B1) | 1 h | Current routes cross days backwards — visibly wrong to any judge |
| **P1** | Phase 1 (request model) | 1 h | Foundation for every later phase; also fixes B5, B6, B9 |
| **P1** | Phase 4.1 / 4.4 / 4.5 (form + pydeck + day tabs) | 2–3 h | Largest visible improvement; makes tier differences legible |
| **P2** | Phase 3 (critic convergence + budget) | 1–2 h | Fixes B2 and B7; quality score |
| **P2** | Phase 2.2 (`optimizeWaypointOrder`) | 1 h | Differentiator, not a baseline requirement |
| **P2** | Phase 5.1 / 5.2 (cache warming, offline rehearsal) | 30 min | Cheap insurance against total demo failure |
| **P3** | Phase 4.3 browser geolocation | 1 h | Highest failure risk on stage; text + geocode covers the requirement |
| **P3** | B8, B10, B11 | 1 h | Only matter for 3+ day trips or non-Calgary cities |

**Total: roughly 1.5–2 focused days.**

### Minimum demo-critical cut line

If time runs short, ship exactly this:

- Phase 0 (all)
- Phase 2.1 + 2.5 (geometry + per-day routes)
- Phase 4.1 + 4.4 + 4.5 (form + pydeck map + day tabs with `source` badges)
- Phase 5.1 + 5.2 (warm cache, rehearsed offline fallback)

Deferrable without hurting the demo: waypoint optimization, browser geolocation, opening-hours validation, multi-city dataset.

---

## Appendix A — Dataset vocabulary (verified at `921dd41`)

Use these to populate UI dropdowns. Read them from the CSVs at runtime rather than hardcoding, so they cannot drift.

**Restaurant `cuisine` (30 distinct):**
`bakery, bbq, breakfast, brunch, burgers, cafe, canadian, chinese, dessert, eastern_european, ethiopian, french, hawaiian, indian, italian, japanese, korean, latin, mediterranean, mexican, middle_eastern, nepalese, pub, sandwiches, seafood, spanish, steakhouse, thai, vegetarian, vietnamese`

Plus the alias buckets defined in `local_catalog.py:29` `CUISINE_ALIASES`: `asian`, `international`.

**Attraction `category` (8):**
`activity, attraction, landmark, market, museum, park, shopping, walking_tour`

**Attraction `slot_types`:** `am`, `pm`, `am;pm` — currently unused by the orchestrator. Phase 2's day sorting could honour this so morning-only attractions are not scheduled after dinner.

**`CANONICAL_ALLERGENS`** (`data/seed.py:30`, 9 values):
`peanut, tree_nut, shellfish, fish, soy, gluten, dairy, egg, sesame`

Every venue gets an explicit true/false for all nine, so a missing key can never read as "safe". Import this constant; do not retype it.

**`dietary_options` tokens:** `gluten_free, halal, vegan, vegetarian`

**Price bands:** `$` ×29, `$$` ×17, `$$$` ×12, `$$$$` ×2 (60 restaurants total). `seed.py:price_level()` returns `len(band)`, uncapped.

**Test fixtures (`is_trap` column) — use these as regression cases:**

| venue_id | Name | Trap | Should be caught by |
|---|---|---|---|
| `r003` | Ember & Oak Steakhouse | `budget_buster` | budget check |
| `r005` | Jade Lantern Dim Sum | `closed_monday` | opening-hours check (Phase 3.6) |
| `r008` | Peanut Garden Thai | `peanut_risk` | allergen hard exclusion |
| `r049` | Mount Royal Fine Dining | `budget_buster` | budget check |
| `a002` | Prairie Heritage Museum | `closed_monday` | opening-hours check (Phase 3.6) |

Dataset size: 60 restaurants, 25 attractions, Calgary only.

---

## Appendix B — Google API reference

Verify shapes, limits, and pricing against current Google documentation before relying on them.

**Places API (New) — Text Search**

```bash
curl -X POST 'https://places.googleapis.com/v1/places:searchText' \
  -H 'Content-Type: application/json' \
  -H "X-Goog-Api-Key: $GOOGLE_MAPS_API_KEY" \
  -H 'X-Goog-FieldMask: places.id,places.displayName,places.location,places.priceLevel,places.rating,places.formattedAddress' \
  -d '{"textQuery":"ramen in Calgary","maxResultCount":5}'
```

**Routes API — computeRoutes with waypoint optimization**

```bash
curl -X POST 'https://routes.googleapis.com/directions/v2:computeRoutes' \
  -H 'Content-Type: application/json' \
  -H "X-Goog-Api-Key: $GOOGLE_MAPS_API_KEY" \
  -H 'X-Goog-FieldMask: routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline,routes.optimizedIntermediateWaypointIndex,routes.legs.duration,routes.legs.distanceMeters,routes.legs.polyline.encodedPolyline' \
  -d '{
    "origin":{"location":{"latLng":{"latitude":51.0447,"longitude":-114.0719}}},
    "destination":{"location":{"latLng":{"latitude":51.0392,"longitude":-114.0203}}},
    "intermediates":[{"location":{"latLng":{"latitude":51.0455,"longitude":-114.0631}}}],
    "travelMode":"WALK",
    "optimizeWaypointOrder":true
  }'
```

**Gotchas:**

- `X-Goog-FieldMask` is **mandatory** on both APIs. Omitting it returns HTTP 400.
- Enable **Places API (New)** — a separate product from legacy "Places API". Having only the legacy one enabled returns 403 against `places.googleapis.com/v1/...`.
- Both require billing enabled on the GCP project.
- `duration` is a **string** like `"1234s"`. `routes_live.py:62` already strips the `s` correctly — preserve that when adding leg parsing.
- `optimizeWaypointOrder` is billed on a higher SKU tier and has its own waypoint-count and travel-mode restrictions, tighter than the general intermediates limit.
- Places API has **no allergen fields**. This is why `config.ALLERGEN_RISK_CUISINES` exists and why graded allergen scenarios must run on the local dataset.

**Status code triage:**

| Code | Meaning | Action |
|---|---|---|
| 200 | Working | Problem is in application code, not config |
| 400 | Malformed body or FieldMask | Check FieldMask first |
| 403 | API not enabled / billing off / key restricted | GCP console |
| 429 | Quota exceeded | Rely on `FOODIE_CACHE=on` |

---

## Appendix C — Command reference

```bash
# Setup
cp .env.example .env                  # then fill FUELIX_API_KEY + GOOGLE_MAPS_API_KEY
python data/seed.py                   # build data/foodie.sqlite from data/csv/

# Diagnose (run this FIRST, before any code change)
python scripts/preflight.py           # proxy, Fuel iX, Places, Routes
python scripts/smoke_test.py          # M0 gate
python scripts/tier_diff.py           # NEW: Tier 1 vs Tier 2 A/B

# Run
streamlit run app/streamlit_app.py    # the full UI — NOT frontend/
uvicorn app.api:app --port 8080       # FastAPI backend
python -m src.orchestrator            # headless demo of both tiers
python app/cli.py                     # CLI fallback

# Evaluate
python eval/acceptance.py             # scenario suite S1-S3

# Mode switches
FOODIE_DATA_BACKEND=live   ...        # Google only, fails loudly
FOODIE_DATA_BACKEND=local  ...        # SQLite only, offline insurance
FOODIE_DATA_BACKEND=auto   ...        # try live, fall back, record it
FOODIE_CACHE=off           ...        # bypass the API response cache
```

---

## Appendix D — Change summary by file

| File | Phase | Change |
|---|---|---|
| `scripts/preflight.py` | 0 | Run as-is. No change needed. |
| `scripts/tier_diff.py` | 0 | **New file.** Tier 1 vs Tier 2 A/B regression. |
| `scripts/warm_cache.py` | 5 | **New file.** Pre-populate `data/api_cache.sqlite`. |
| `src/request_model.py` | 1 | **New file.** `TripRequest`, `Origin`, derived dates/weekdays/labels. |
| `app/ui_components.py` | 4 | **New file.** Shared render functions for both Streamlit apps. |
| `frontend/streamlit_app.py` | 0, 1 | Remove hardcoded `tier: 1` (:56) and the allergy→local force (:57). Add tier/backend selectors. Fix budget regex (:35). |
| `app/api.py` | 0, 1, 4 | `tier` default 1→2 (:23). Replace `PlanRequest` with `TripRequest`. Stop mutating `config.DATA_BACKEND` (:45). Add `GET /diagnostics`. |
| `src/config.py` | 1, 5 | Add `contextvars` backend override + `current_backend()`. Add `FOODIE_DEMO_MODE`. |
| `src/tools/__init__.py` | 0, 2, 3 | Add `_live_decision()` reason reporting. Move `_LAST_BACKEND` off module globals into `TripState.meta` (B4). Expose `compute_day_route`. Add `near`/`within_km` passthrough. |
| `src/tools/routes_live.py` | 2 | **Add polyline to FieldMask (:44).** Add `compute_day_route()` with `intermediates` + `optimizeWaypointOrder`. |
| `src/tools/places_live.py` | 3 | Add `locationRestriction` circle for `near`/`within_km`. |
| `src/tools/local_catalog.py` | 2, 3 | Add `compute_day_route()` (greedy NN + synthetic polyline). Add distance filter/sort. Add transit/bicycle speeds. |
| `src/orchestrator.py` | 1, 2, 3 | Rewrite `_compute_routes_async` day-grouped (:376, B1). Fix append order (:429/:445). `_deterministic_critic` uses minutes + day totals (:391). Distance-aware revision. Unified `score_candidate()`. Un-hardcode `price_level_max=2` (:562, B7) and `mode="walk"` (:378, B11). Attraction `limit` from days (:353, B8). Record `unresolved_issues`. |
| `src/state.py` | 1, 2 | Extend slot vocabulary (origin, day-level scope). `_plan_with_llm` must pass real `attractions_per_day`. Add `compute_day_route` to `TOOL_SCHEMAS`. |
| `app/streamlit_app.py` | 4 | `st.form` inputs. Dataset-driven dropdowns. Origin resolution. `st.map` → `st.pydeck_chart` with `PathLayer` (:64). Day tabs with timeline + `source` badges (:41). Diagnostics sidebar. Unresolved-issue banner. Allergen advisory. Fix budget regex (:23). |
| `requirements.txt` | 4 | Add `polyline` **only if** not writing the stdlib decoder. `pydeck` ships with Streamlit. |
| `data/csv/*.csv` | 5 | Optional: add a `city` column to lift the Calgary-only limit (B10). |
| `README.md`, `BUILD_AND_DEPLOY_RUNBOOK.md`, `PROJECT_CONTEXT.md` | 5 | Correct the run command, add preflight as step 1, update stale Tier 2 status (`PROJECT_CONTEXT.md:158-159`). |

---

## Appendix E — Two things not to "fix"

Verified working. Changing them will introduce bugs.

1. **Meal-type filtering.** `local_catalog.search_restaurants()`'s `meal_types LIKE '%;meal;%'` is correct. `Ethiopian Coffee House` (`r030`) is legitimately tagged `lunch;dinner` — it is an Ethiopian restaurant whose name happens to contain "Coffee House". `Inglewood Coffee Roasters` (`r044`) is tagged `breakfast` only and is correctly scheduled at breakfast.

2. **`stable_review_count()` in `data/seed.py`.** The md5 hash is deliberate. Python's built-in `hash()` is salted per process, so it returns different values on every run and machine. Since the local backend orders by `rating DESC, review_count DESC`, an unstable tiebreaker would make itineraries change between runs — which would make `scripts/tier_diff.py` useless. Keep md5.
