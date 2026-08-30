# Traveling Foodie Agent — Build & Deploy Runbook (TELUS environment)

**Classification:** TELUS Internal **For:** Emma (Xu Wang) — building and validating the reference implementation before the RF Design West Offsite Hackathon **Companion file:** `PROJECT_CONTEXT.md` (architecture, rubric, traps, kit API surface) **Scope decided:** local-first build → Tier 1 → Tier 2 → Streamlit UI → LangGraph spike on a branch → Cloud Run as an optional deploy target **Written:** 26 Aug 2026

---

## How to read this

Seven phases. Phases 0–4 are the critical path — finish those and you have a demo. Phase 5 (LangGraph) and Phase 6 (Cloud Run) are optional upside; do not start either until Phase 4 is green.

Every phase ends with a **GATE** — a command whose output you can paste into the 10:30 check-in with Teresa. If a gate fails, stop and fix it. Do not build the next layer on a red gate; that is how hackathon demos die on stage.

| Phase | What | Time | Gate |
| :---- | :---- | :---- | :---- |
| 0 | Fix the kit before you build on it | 45 min | `seed.py` prints 60/25 and the CSVs are unchanged |
| 0 | **Demo-day insurance** | 10 min | `python scripts\warm_cache.py --golden` on the demo machine, then rehearse the ladder: `auto` → `local` → `FOODIE_DEMO_MODE=on`. The cache is gitignored and does not travel, and an untested fallback is not a fallback. |
| 1 | Local environment \+ M0 | 60 min | `preflight.py` all green, one live Places call |
| 2 | Tier 1 — real Fuel iX agent | 3 h | ≥4 LLM calls, ≥1 live Places call, 0 allergen leaks, \<60 s |
| 3 | Tier 2 — parallel \+ Critic loop | 3 h | `acceptance.py` passes S1–S3, critic bounded at 2 |
| 4 | Streamlit UI — chat \+ trace \+ map | 2 h | Demo runs end-to-end in a browser |
| 5 | LangGraph branch (optional) | 2 h | Mermaid graph renders, S1 passes on the branch |
| 6 | Cloud Run (optional) | 2 h | Service responds, Fuel iX reachable from GCP |
| 7 | Demo-day insurance | 45 min | Warm cache \+ local mode \+ backup recording |

**Suggested calendar** (offsite is coming; today is Tue 26 Aug):

| Day | Target | Report at 10:30 |
| :---- | :---- | :---- |
| Tue 26 | Phase 0 \+ Phase 1 | "Kit fixed, M0 green, live Places \+ Routes both confirmed" |
| Wed 27 | Phase 2 (Tier 1 done) | "Tier 1 running on real Fuel iX, S1 clean" |
| Thu 28 | Phase 3 \+ start Phase 4 | "Tier 2 parallel \+ Critic loop bounded, under 60 s" |
| Fri 29 | Phase 4 \+ Phase 5 spike | "Streamlit chat UI done; LangGraph feasibility call" |
| Following week | Phase 6/7 \+ kickoff slides | "Deploy decision made, insurance in place" |

---

# PHASE 0 — Fix the kit before you build on it (45 min)

You are about to build on top of this kit for a week. Every hour you spend on a broken foundation you spend twice. The change list in `PROJECT_CONTEXT.md` §7 is the work; this is the order.

### 0.1 Get a clean working copy

```
cd $HOME\Desktop
# Download the Drive folder as a zip, or use the copy you already have.
# Rename it so it is obviously YOUR build, not the kit you hand out:
Rename-Item "Travel-Foodie-Agent-Hackathon-Starter" "Travel-Foodie-Agent-Reference"
cd Travel-Foodie-Agent-Reference
git init
git add -A
git commit -m "Baseline: kit as shipped"
```

> Committing the *unfixed* baseline first is deliberate — it gives you a diff to hand Teresa showing exactly what changed in the kit.

### 0.2 Apply the BLOCKER fix — `data/seed.py`

Drop in the rewritten `seed.py` (the one that treats the CSVs as source of truth and never writes to `data/csv/`). **Before you run it**, record the CSV fingerprints so you can prove they survived:

```
Get-FileHash data\csv\calgary_restaurants.csv -Algorithm MD5
Get-FileHash data\csv\calgary_attractions.csv -Algorithm MD5
```

Then:

```
python data\seed.py
Get-FileHash data\csv\calgary_restaurants.csv -Algorithm MD5   # must be identical
```

**Expected output** (the new `seed.py` is self-verifying):

- per-city counts: **60 restaurants / 25 attractions** for Calgary  
- peanut-risk count as computed by SQL (non-zero)  
- every `is_trap` row listed: r008, r005, a002, r003, r049

If you see 8 restaurants / 4 attractions, you are still running the old `seed.py`. Stop and fix it.

### 0.3 Repo hygiene

```
Remove-Item -Recurse -Force src\__pycache__, src\tools\__pycache__, eval\__pycache__ -ErrorAction SilentlyContinue
Remove-Item -Force data\foodie.sqlite -ErrorAction SilentlyContinue
```

Add to `.gitignore`:

```
.env
.venv/
__pycache__/
*.pyc
data/foodie.sqlite
data/api_cache.sqlite
```

`data/foodie.sqlite` is a build artifact. Shipping it lets a team skip `seed.py` and unknowingly run on a stale DB — exactly the failure you just fixed.

### 0.4 Fix the doc mismatches (these affect the kit you hand out)

| File | Change |
| :---- | :---- |
| `Travel_Foodie_Agent_Build_Guide.md` | **Do this first.** The in-document kit link points at folder `1UsibtjAxForO3DxdmUyouo6k2a65vt-l` but the folder being shared is `1q2x_WP4z2uwS-pKDKvALclln_nI4z5D7`. Pick one, make both match. Participants follow the in-document link. |
| `Travel_Foodie_Agent_Build_Guide.md` §0 | "party of two" → **party of ten** (matches the deck) |
| `README.md` / Build Guide §3.1 | "confirm coding assistant with Teresa (Cline vs Claude Code)" → resolved: **was Cline, now Claude** |
| `Travel_Foodie_Agent_Build_Guide.md` §7 | Replace the trap table — "Peanut Palace" and "Midnight Diner" do not exist in the staged data. Use the real table from `PROJECT_CONTEXT.md` §4 (r008 / r005 / a002 / r003 / r049). |
| `scripts/smoke_test.py` | The predicate `(c.get("dietary_flags") or {}).get("peanut_risk")` works unchanged — only the comments naming the old venues need fixing. |

### 0.5 Two deliberate decisions in `src/tools/local_catalog.py`

Do not let these happen by accident.

**(a) The soft-fallback branch.** There is a `# Soft fallback: ... so demos never go blank on sparse seed data` branch. It was a workaround for having 8 venues. With 60 it should go — silently returning the wrong cuisine is worse than returning few results, and a judge probing *"why is there a steakhouse in my Japanese slot"* will find it. **Recommendation: remove it.**

**(b) `meal` is accepted and ignored.** `search_restaurants(city, meal, ...)` takes `meal` and does nothing with it. `meal_types` is now in the DB, so it is one line:

```sql
AND (meal_types = '' OR meal_types LIKE '%'||?||'%')
```

**Recommendation: fix it in your reference build, leave it as a documented TODO in the kit teams receive.** It is a good Tier 1 exercise and it costs them ten minutes, not an hour.

### 0.6 Add proxy config to the kit (your action item from the sync)

Teams should not have to discover the proxy. Add to `.env.example` at the top:

```
# --- TELUS network (required on a corporate laptop, VPN on) ---
HTTPS_PROXY=http://pac.tsl.telus.com:8080
HTTP_PROXY=http://pac.tsl.telus.com:8080
NO_PROXY=localhost,127.0.0.1,::1

# --- Fuel iX LLM gateway (required for real Tier 1/2 LLM calls) ---
FUELIX_API_KEY=
FUELIX_BASE_URL=https://api.fuelix.ai/v1
FOODIE_MODEL_DEFAULT=claude-sonnet-4

# --- Google Maps Platform: per-team restricted key on the hackathon GCP project
# Enable: Places API (New) + Routes API. Do NOT use Places (Legacy).
GOOGLE_MAPS_API_KEY=

# --- Data backend: auto (default) | live | local ---
FOODIE_DATA_BACKEND=auto

# --- Cache Google API responses (on|off) — keeps usage under control ---
FOODIE_CACHE=on

# --- Optional per-agent model overrides ---
# FOODIE_MODEL_PLANNER=claude-sonnet-4
# FOODIE_MODEL_CRITIC=claude-sonnet-4
```

> **Why `NO_PROXY` matters:** without `localhost,127.0.0.1,::1` in it, Streamlit's own loopback traffic gets routed at the corporate proxy and the browser hangs on a blank page. The current draft has `localhost` listed twice and no `::1` — fix both. Add `::1` because modern browsers resolve `localhost` to IPv6 first.

### ✅ GATE 0

```
python data\seed.py
```

- 60 restaurants / 25 attractions  
- CSV MD5 unchanged  
- all five traps listed  
- `git status` shows no `__pycache__`, no `.sqlite`

---

# PHASE 1 — Local environment and M0 (60 min)

### 1.1 Virtual environment

```
cd $HOME\Desktop\Travel-Foodie-Agent-Reference
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell refuses to run the activate script:

```
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Confirm you are on 3.10+:

```
python --version
```

The core kit is **standard library only** — there is nothing to install to run Tiers 1 and 2\. You only need pip for Streamlit (Phase 4\) and LangGraph (Phase 5).

### 1.2 Proxy — set it in two places

**In the shell** (authoritative, survives any import order):

```
$env:HTTP_PROXY  = "http://pac.tsl.telus.com:8080"
$env:HTTPS_PROXY = "http://pac.tsl.telus.com:8080"
$env:NO_PROXY    = "localhost,127.0.0.1,::1"
```

**In `.env`** (so the kit works for teams who forget the shell step). Note that `config.load_dotenv()` uses `os.environ.setdefault` — a real shell variable always wins, so setting both is safe.

To make it permanent for your user account:

```
[Environment]::SetEnvironmentVariable("HTTPS_PROXY","http://pac.tsl.telus.com:8080","User")
[Environment]::SetEnvironmentVariable("HTTP_PROXY","http://pac.tsl.telus.com:8080","User")
[Environment]::SetEnvironmentVariable("NO_PROXY","localhost,127.0.0.1,::1","User")
```

Then restart VS Code so it picks them up.

**pip through the proxy:**

```
pip install --proxy http://pac.tsl.telus.com:8080 `
  --trusted-host pypi.org --trusted-host files.pythonhosted.org `
  streamlit pandas
```

The `--trusted-host` flags are only needed if TLS inspection breaks certificate validation. Try without them first.

### 1.3 Fuel iX key

1. Go to `app.fuelix.ai` → app switcher (the dots, top right) → **API** → Dev Portal.  
2. Create a project (name it `Hackathon`).  
3. Copy the key into `.env` as `FUELIX_API_KEY=`.  
4. Check the **models** endpoint in the portal and confirm the exact model ID string. The kit defaults to `claude-sonnet-4`; the portal listing may show newer IDs. Whatever it says, put it in `FOODIE_MODEL_DEFAULT` verbatim — a wrong model ID surfaces as an unhelpful HTTP 400\.

Also configure VS Code \+ Claude against the same gateway:

```
$env:ANTHROPIC_BASE_URL  = "https://api.fuelix.ai"
$env:ANTHROPIC_AUTH_TOKEN = "<your fuel ix key>"
```

### 1.4 Google Maps key — and the restriction trap

Get the per-team restricted key from the GCP project (Lab 1/2/3, or `theresa-test-lab` for your own testing). Put it in `.env` as `GOOGLE_MAPS_API_KEY=`.

Then check three things in the console — **this is where most 403s come from**:

| Check | Where | Required setting |
| :---- | :---- | :---- |
| **APIs enabled** | Google Maps Platform → APIs & Services | **Places API (New)** and **Routes API** both showing "Disable" (i.e. currently enabled). Places *Legacy*, Directions, and Distance Matrix are **not** used by this kit. |
| **Application restriction** | Keys & Credentials → your key | Set to **None**, or IP addresses. **Never "HTTP referrers (websites)"** — server-side `urllib` calls send no referrer and get `API_KEY_HTTP_REFERRER_BLOCKED`. Note that behind `pac.tsl.telus.com` your requests egress from the proxy's IP, not your laptop's, so IP restriction is fragile for laptop dev. |
| **API restriction** | same page | Narrow from the default broad set (the key currently shows "35 APIs") down to **Places API (New) \+ Routes API** only. Cheap insurance against a stray key leaking into something expensive. |

Also confirm **billing is enabled** on the project — Places API (New) returns a `REQUEST_DENIED`\-style error without it.

> **Cost note:** the kit's field mask requests `rating`, `userRatingCount` and `priceLevel`, which puts Text Search into the **Pro** SKU band rather than Essentials. With `FOODIE_CACHE=on` a full dev day is a handful of dollars, but set a billing alert on each team project before the event.

### 1.5 Preflight script

Create `scripts/preflight.py`. This is the single command you and every team run when something is wrong — it tells you *which* of the four things is broken instead of making you guess.

```py
"""Preflight — verifies proxy, Fuel iX, Places (New) and Routes in one shot."""
import json, os, sys, urllib.error, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import config

OK, BAD = "  OK  ", " FAIL "

def line(label, ok, detail=""):
    print(f"[{OK if ok else BAD}] {label:28s} {detail}")
    return ok

def main():
    results = []

    # 1) proxy visible to urllib
    proxies = urllib.request.getproxies()
    results.append(line("proxy env", bool(proxies.get("https")),
                        proxies.get("https", "no https proxy in env")))

    # 2) Fuel iX reachable + key valid
    try:
        req = urllib.request.Request(
            config.FUELIX_BASE_URL.rstrip("/") + "/models",
            headers={"Authorization": f"Bearer {config.FUELIX_API_KEY}"})
        with urllib.request.urlopen(req, timeout=30) as r:
            models = json.loads(r.read().decode())
        ids = [m.get("id") for m in (models.get("data") or [])][:5]
        ok = config.DEFAULT_MODEL in [m.get("id") for m in (models.get("data") or [])]
        results.append(line("fuel ix /models", True, f"{len(models.get('data') or [])} models"))
        results.append(line(f"model {config.DEFAULT_MODEL}", ok,
                            "" if ok else f"not in list; sample: {ids}"))
    except Exception as e:
        results.append(line("fuel ix /models", False, str(e)[:160]))

    # 3) Places API (New) — text search
    try:
        from src.tools import places_live
        rows = places_live.search_restaurants("Calgary", "dinner", limit=2)
        results.append(line("places (new) searchText", bool(rows),
                            rows[0]["name"] if rows else "empty result"))
    except Exception as e:
        results.append(line("places (new) searchText", False, str(e)[:160]))

    # 4) Routes API — computeRoutes (Calgary Tower -> Studio Bell)
    try:
        from src.tools import routes_live
        leg = routes_live.estimate_travel(51.0447, -114.0631, 51.0466, -114.0592, mode="walk")
        results.append(line("routes computeRoutes", True,
                            f"{leg['km']} km / {leg['minutes']} min"))
    except Exception as e:
        results.append(line("routes computeRoutes", False, str(e)[:160]))

    print()
    if all(results):
        print("PREFLIGHT GREEN — you can build.")
        return 0
    print("PREFLIGHT RED — fix the FAIL lines above before building.")
    print("Offline escape hatch: set FOODIE_DATA_BACKEND=local")
    return 1

if __name__ == "__main__":
    raise SystemExit(main())
```

Run it:

```
python scripts\preflight.py
```

### 1.6 M0 gate — offline first, then live

```
# offline path must work with no keys at all
$env:FOODIE_DATA_BACKEND = "local"
python -m src.orchestrator
python scripts\smoke_test.py

# now live
$env:FOODIE_DATA_BACKEND = "auto"
python scripts\preflight.py
```

**Pass criteria for `python -m src.orchestrator`:**

- `allergen leaks in candidates (MUST be empty): []`  
- budget status `ok` or `warning`, never `exceeded` on S1  
- Critic slot-guard: `day2.dinner` accepted, `"dinner day 2"` rejected

### ✅ GATE 1

`python scripts\preflight.py` prints **PREFLIGHT GREEN**, including a live Routes API leg. That closes Teresa's open item "test the Routes API" — send her the output.

---

# PHASE 2 — Tier 1: replace the mocks with real Fuel iX calls (3 h)

`run_tier1()` already runs end-to-end with deterministic mocks. Your job is to replace three of the four stages with real LLM calls and **leave Budget alone**.

### What Tier 1 must satisfy

| Requirement | How it is proven |
| :---- | :---- |
| ≥ 4 real Fuel iX calls | `client.telemetry["llm_calls"] >= 4` in `TripState.meta` |
| Planner decomposes the request | `st.plan` has one entry per slot, each carrying budget \+ allergies |
| ≥ 1 live Google Places call | `st.meta["tool_backends"]["restaurants"] == "google_places"` |
| Allergen \+ budget enforced **in code** | `exclude_flags` passed at the tool layer; `check_budget` is pure Python |
| Full itinerary **\< 60 s** | `st.meta["elapsed_s"] < 60` |

### 2.1 Planner — strict JSON, no venue picking

The single most important rule: **the Planner does not choose restaurants.** It splits the request into slots and attaches constraints. If the Planner names a venue, you have built a chatbot with extra steps and you have thrown away the 40 agentic points.

```py
from .fuelix_client import FuelixClient, parse_json_reply
from . import config
from .state import slot_ids

def plan_with_llm(client, request):
    days = int(request.get("days", 2))
    system = (config.PROMPTS_DIR / "planner.md").read_text(encoding="utf-8")
    user = (
        "Split this request into slots. Return STRICT JSON only.\n"
        f"Valid slot IDs (use these EXACTLY): {slot_ids(days)}\n"
        f"Request: {json.dumps(request)}\n"
        "Schema: {\"days\": int, \"slots\": [{\"slot\": str, \"meal\": str, "
        "\"budget_cap\": number, \"cuisine_hint\": str, \"area_hint\": str, "
        "\"exclude_flags\": [str]}], \"budget_allocation\": {...}}\n"
        "Do NOT name any venue. Venue selection is a different agent's job."
    )
    msg = client.chat(model=config.MODEL_ROUTING["planner"],
                      system=system, user=user, temperature=0.1)
    plan = parse_json_reply(msg.get("content", ""))

    # Guard the Planner the same way you guard the Critic
    valid = set(slot_ids(days))
    plan["slots"] = [s for s in plan.get("slots", []) if s.get("slot") in valid]
    if not plan["slots"]:
        raise ValueError("Planner returned no valid slot IDs")
    return plan
```

> **Prompt the Planner with the closed slot list.** Do not hope it guesses the format. `slot_ids(days)` gives you `["day1.breakfast", ..., "day2.attraction1"]`.

### 2.2 Restaurant executor — use the tool loop, not the top row

The mock takes `cands[0]`. That is not agentic. Use `run_tool_loop` so the model calls `search_restaurants`, sees the candidates, and *reasons* about which one fits the slot's budget cap and cuisine hint.

```py
from .fuelix_client import run_tool_loop
from .state import TOOL_SCHEMAS
from .tools import TOOL_IMPLS

def pick_restaurant(client, slot_spec, city, allergies):
    system = (config.PROMPTS_DIR / "restaurant.md").read_text(encoding="utf-8")
    exclude = [f"{a}_risk" for a in allergies]
    user = (
        f"City: {city}. Slot: {slot_spec['slot']} ({slot_spec['meal']}).\n"
        f"Budget cap for this meal: ${slot_spec['budget_cap']}.\n"
        f"Cuisine hint: {slot_spec.get('cuisine_hint') or 'any'}.\n"
        f"MUST exclude venues with flags: {exclude}\n"
        "Call search_restaurants, then choose ONE candidate. Return STRICT JSON: "
        '{"venue_id": str, "name": str, "cost": number, "why": str}. '
        "The 'why' must cite rating, price and constraint fit — no invented facts."
    )
    return run_tool_loop(
        client, config.MODEL_ROUTING["restaurant"], system, user,
        tools=TOOL_SCHEMAS, tool_impls=TOOL_IMPLS)
```

**Belt and braces:** even though you tell the model to exclude flagged venues, `exclude_flags` is *also* applied inside `search_restaurants` at the tool layer. That is deliberate — the model never sees the peanut venue. Then validate the model's answer against the candidate list you actually returned:

```py
chosen_ids = {c["venue_id"] for c in st.candidates[slot]}
if pick["venue_id"] not in chosen_ids:
    st.log("restaurant", f"{slot}: model returned an off-list venue, falling back to top candidate")
    pick = st.candidates[slot][0]
```

This one check kills the entire class of "hallucinated venue" failures, which score zero.

### 2.3 Budget — do not touch it

```py
st.budget = check_budget(chosen, float(request["budget_total"]))
```

`check_budget` is pure Python and stays that way. This is an explicitly scored teaching point and one of the three non-negotiables. When a judge asks "how do you know the budget is right", the answer is "it is arithmetic, not language."

### 2.4 Formatter — one call, reasons attached

```py
def format_itinerary(client, st):
    system = (config.PROMPTS_DIR / "formatter.md").read_text(encoding="utf-8")
    user = (
        "Assemble a day-by-day itinerary from this state. "
        "Every pick must carry the reason already recorded in its 'why' field. "
        "Do not invent venues, hours, or prices.\n"
        f"{json.dumps({'plan': st.plan, 'itinerary': st.itinerary, 'budget': st.budget}, default=str)}"
    )
    msg = client.chat(model=config.MODEL_ROUTING["formatter"],
                      system=system, user=user, max_tokens=2000)
    return msg.get("content", "")
```

### 2.5 Record the telemetry the rubric asks about

```py
st.meta = {
    "tier": 1,
    "elapsed_s": round(time.time() - t0, 2),
    "mock_llm": config.MOCK_MODE,
    "data_backend": config.DATA_BACKEND,
    "tool_backends": last_backend_report(),   # <- show this on screen in the demo
    "llm_calls": client.telemetry["llm_calls"],
    "tokens": client.telemetry,
    "latency_budget_s": config.LATENCY_BUDGET_S,
}
```

`last_backend_report()` is your proof of live-API usage. Put it on the screen. 40 of the 100 points are "Dataset & API Mastery" — do not make the judges take your word for it.

### ✅ GATE 2

```
$env:FOODIE_DATA_BACKEND = "auto"
python -m src.orchestrator
```

- `llm_calls >= 4`  
- `tool_backends.restaurants == "google_places"`  
- `allergen leaks: []`  
- `elapsed_s < 60`

Then re-run the graded allergen scenario on the local dataset (Places has no allergen fields, so S1 grading must run local):

```
$env:FOODIE_DATA_BACKEND = "local"
python -m src.orchestrator
```

r008 Peanut Garden Thai must be absent from every candidate list.

---

# PHASE 3 — Tier 2: parallel executors \+ bounded Critic loop (3 h)

### 3.1 Parallelize with `asyncio.gather`

The tool functions are synchronous `urllib` calls. Wrap them with `asyncio.to_thread` rather than rewriting them — six meal slots that ran in sequence now run in one round trip's worth of wall clock.

```py
import asyncio

async def _execute_slots(client, slots, city, allergies):
    tasks = [
        asyncio.to_thread(pick_restaurant, client, spec, city, allergies)
        for spec in slots
    ]
    return await asyncio.gather(*tasks, return_exceptions=True)

def execute_slots(client, slots, city, allergies):
    results = asyncio.run(_execute_slots(client, slots, city, allergies))
    out = []
    for spec, res in zip(slots, results):
        if isinstance(res, Exception):
            # never let one slot kill the demo
            out.append({"slot": spec["slot"], "error": str(res)})
        else:
            out.append(res)
    return out
```

`return_exceptions=True` matters. One 429 on one slot should degrade that slot, not crash the itinerary.

> **Streamlit event-loop gotcha.** `asyncio.run()` fails with `RuntimeError: asyncio.run() cannot be called from a running event loop` if a loop is already active. Use this helper everywhere instead of calling `asyncio.run` directly:

```py
import asyncio, threading

def run_sync(coro):
    """Run a coroutine whether or not a loop is already running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    box = {}
    def _worker():
        box["out"] = asyncio.run(coro)
    t = threading.Thread(target=_worker)
    t.start(); t.join()
    return box["out"]
```

### 3.2 Attraction and Route agents

Attractions come from the same Places facade; routes from `estimate_travel`, which hits Routes API live and falls back to haversine.

```py
legs = []
geo = [it for it in st.itinerary if it.get("lat") is not None]
for a, b in zip(geo, geo[1:]):
    leg = estimate_travel(a["lat"], a["lon"], b["lat"], b["lon"], mode="walk")
    leg["from"], leg["to"] = a["name"], b["name"]
    legs.append(leg)
st.routes = legs
```

**This is the piece you described in the sync** — the reason the agent does not put a great restaurant 50 km outside downtown. Make the Route agent *act* on its own output, not just report it:

```py
MAX_WALK_KM = float(request.get("max_walk_km", 2.0))
over = [l for l in legs if l["mode"] == "walk" and l["km"] > MAX_WALK_KM]
for leg in over:
    st.log("route", f"{leg['from']} → {leg['to']} is {leg['km']} km — flagging for Critic")
```

Feed those into the Critic as issues. A Route agent that only measures is a report. A Route agent whose measurement changes the plan is an agent.

### 3.3 The bounded Critic loop — the highest-value component

```py
from .state import slot_ids

def run_critic_loop(client, st, request):
    days = int(request.get("days", 2))
    valid = slot_ids(days)

    for iteration in range(1, config.CRITIC_MAX_ITERATIONS + 1):
        critic = call_critic(client, st, valid, iteration)

        ok, bad = validate_critic_output(critic, days=days)
        if not ok:
            # ONE re-ask with the closed list. Never re-plan on bad slot IDs.
            st.log("critic", f"slot-guard rejected {bad}; re-asking with closed list")
            critic = call_critic(client, st, valid, iteration, retry=True)
            ok, bad = validate_critic_output(critic, days=days)
            if not ok:
                st.log("critic", f"slot-guard rejected {bad} twice — shipping current plan")
                critic = {"verdict": "approved", "issues": [], "iteration": iteration,
                          "note": "slot-guard bailout"}
                break

        st.critic = critic
        if critic.get("verdict") == "approved":
            st.log("critic", f"approved on iteration {iteration}")
            break

        redo = [i["slot"] for i in critic.get("issues", [])]
        st.log("critic", f"iteration {iteration}: revising {redo}")
        revise_slots(client, st, redo, request)   # re-plan ONLY the named slots
    else:
        st.log("critic", f"hit max {config.CRITIC_MAX_ITERATIONS} iterations — shipping")

    st.critic = critic
    return st
```

Three things a judge will probe, and the answers:

| Probe | Answer |
| :---- | :---- |
| "What if the Critic loops forever?" | `CRITIC_MAX_ITERATIONS = 2`, then it ships. The `for/else` is the bound. |
| "What if the Critic makes up a slot name?" | `validate_critic_output` rejects anything outside the closed vocabulary, re-asks once, then bails out to *approved*. It never re-plans on a name it does not recognise. |
| "Does it re-plan everything?" | No — only the slots the Critic names. That is what the shared `TripState` contract buys you. |

The Critic prompt must ask for allergens, budget, hours *and* the route flags from 3.2, and must be told the closed slot list explicitly:

```py
user = (
    f"Review this plan. Valid slot IDs (use these EXACTLY, no others): {valid}\n"
    "Check: (1) allergen violations, (2) total cost vs budget, "
    "(3) venue open on the scheduled day, (4) walking legs over the max.\n"
    'Return STRICT JSON: {"verdict": "approved"|"revise", '
    '"issues": [{"slot": <one of the valid IDs>, "type": str, "detail": str}], '
    f'"iteration": {iteration}}}\n'
    f"Plan: {json.dumps(st.to_json(), default=str)}"
)
```

### 3.4 Latency

With six meal slots plus two attractions plus routes, the sequential version will blow past 60 s once real LLM calls are in. After parallelizing, measure:

```py
assert st.meta["elapsed_s"] < config.LATENCY_BUDGET_S
```

If you are still over: reduce `TOOL_LOOP_MAX_ROUNDS` from 4 to 3, drop `max_tokens` on the executors, and confirm `FOODIE_CACHE=on`.

### ✅ GATE 3

```
$env:FOODIE_DATA_BACKEND = "local"
python eval\acceptance.py
```

S1, S2 and S3 all pass. Then run S1 in `auto` mode and confirm `elapsed_s < 60` with `tool_backends` showing `google_places` and `google_routes`.

Commit here. This is your fallback demo if anything later goes wrong.

```
git add -A
git commit -m "Tier 2 complete: parallel executors, Routes agent, bounded Critic loop"
git tag tier2-working
```

---

# PHASE 4 — Streamlit UI: chat window \+ agent trace \+ map (2 h)

Gerardo will expect a chat window. The rubric gives \~7 points for agent-trace visibility and \+2 for a map. `app/cli.py` is a fully accepted UI — this is upside, not a prerequisite.

**Primary UI decision:** develop and demo `app/streamlit_app.py`. It is the
full in-process browser UI for Tier 1 and Tier 2. The separate
`frontend/streamlit_app.py` is a thin HTTP client intended for the optional
FastAPI/Cloud Run deployment and is not the primary local demo entrypoint.

```
pip install --proxy http://pac.tsl.telus.com:8080 streamlit pandas
```

Create `app/streamlit_app.py`:

```py
import json, sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import streamlit as st
from src.orchestrator import run_tier2
from src import config

st.set_page_config(page_title="Traveling Foodie Agent", layout="wide")
st.title("Traveling Foodie Agent")
st.caption(f"Fuel iX · {config.DEFAULT_MODEL} · data backend: {config.DATA_BACKEND}")

if "history" not in st.session_state:
    st.session_state.history = []

with st.sidebar:
    st.header("Trip")
    city   = st.text_input("City", "Calgary")
    days   = st.slider("Days", 1, 3, 2)
    budget = st.number_input("Total budget (CAD)", 100, 2000, 500, step=50)
    party  = st.number_input("Party size", 1, 20, 10)
    cuisines  = st.multiselect("Cuisine", ["international","asian","italian","japanese","thai"],
                               default=["international"])
    allergies = st.multiselect("Allergies", ["peanut","tree_nut","shellfish","gluten","dairy"],
                               default=["peanut"])
    st.divider()
    st.write("**Backend**", config.DATA_BACKEND)

prompt = st.chat_input("Tell the agent what you want…")

for role, content in st.session_state.history:
    with st.chat_message(role):
        st.markdown(content)

if prompt:
    st.session_state.history.append(("user", prompt))
    with st.chat_message("user"):
        st.markdown(prompt)

    request = {"city": city, "days": days, "budget_total": budget,
               "party_size": party, "cuisines": cuisines,
               "allergies": allergies, "max_walk_km": 2.0,
               "free_text": prompt}

    with st.chat_message("assistant"):
        with st.status("Agents working…", expanded=True) as status:
            t0 = time.time()
            trip = run_tier2(request)
            status.update(label=f"Done in {time.time()-t0:.1f}s", state="complete")

        tab_plan, tab_trace, tab_map, tab_json = st.tabs(
            ["Itinerary", "Agent trace", "Map", "Raw state"])

        with tab_plan:
            b = trip.budget
            c1, c2, c3 = st.columns(3)
            c1.metric("Projected", f"${b.get('projected', 0):,.0f}", f"limit ${budget:,.0f}")
            c2.metric("Budget status", b.get("status", "?"))
            c3.metric("Elapsed", f"{trip.meta.get('elapsed_s', 0)}s")
            for item in trip.itinerary:
                st.markdown(
                    f"**{item['slot']}** — {item['name']}  ·  ${item.get('cost', 0)}  "
                    f"`{item.get('source')}`\n\n> {item.get('why','')}")

        with tab_trace:
            st.caption("Every agent step, in order. This is the audit trail.")
            for step in trip.trace:
                with st.expander(f"{step['agent']} — {step['message'][:70]}"):
                    st.write(step["message"])
            st.divider()
            st.write("**Tool backends actually used**")
            st.json(trip.meta.get("tool_backends", {}))

        with tab_map:
            pts = [{"lat": i["lat"], "lon": i["lon"], "name": i["name"]}
                   for i in trip.itinerary if i.get("lat") and i.get("lon")]
            if pts:
                st.map(pd.DataFrame(pts), size=40)
                if trip.routes:
                    st.dataframe(pd.DataFrame(trip.routes)[["from","to","mode","km","minutes"]],
                                 width="stretch")
            else:
                st.info("No geo-tagged stops in this plan.")

        with tab_json:
            st.json(trip.to_json())

    st.session_state.history.append(("assistant", "Itinerary generated — see the tabs above."))
```

Run it:

```
streamlit run app\streamlit_app.py
```

Opens on `http://localhost:8501`.

**Demo tips that map directly to the rubric:**

- Open on the **Agent trace** tab for thirty seconds before you show the itinerary. That tab *is* the difference between a chatbot and an agent, and it is where the points are.  
- Leave `tool_backends` visible. It proves the live API claim.  
- Run S1 with the peanut allergy on, then toggle it off, and show r008 Peanut Garden Thai appearing in the candidate list. The constraint is visibly a filter in code, not a polite request in a prompt.

### ✅ GATE 4

Browser demo: type a request, get an itinerary, trace populated, map rendered, under 60 s. Screen-record this now — it is your Phase 7 insurance.

---

# PHASE 5 — LangGraph branch (optional, 2 h) — **do this on a branch**

You raised LangGraph in the sync, and it is worth trying. Two honest caveats before you spend the time:

1. **It buys you visualization and checkpointing, not capability.** Your hand- rolled loop already satisfies every rubric line. LangGraph gives you a graph picture you can paste into the kickoff deck and free state checkpointing.  
2. **It introduces the one install risk the kit was designed to avoid.** The stdlib kit uses `urllib`, which uses the Windows certificate store. `langchain-openai` uses `httpx`, which uses `certifi`. Behind TLS inspection that difference shows up as `SSLCertVerificationError` on a call that worked fine a minute earlier.

**Keep this on a branch. Do not make it the demo path.**

```
git checkout -b langgraph
pip install --proxy http://pac.tsl.telus.com:8080 langgraph langchain-openai
```

### 5.1 Point LangChain at Fuel iX

```py
import os
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    model=os.environ.get("FOODIE_MODEL_DEFAULT", "claude-sonnet-4"),
    base_url=os.environ["FUELIX_BASE_URL"],      # https://api.fuelix.ai/v1
    api_key=os.environ["FUELIX_API_KEY"],
    temperature=0.2,
)
```

`httpx` honours `HTTP_PROXY` / `HTTPS_PROXY` automatically. If you get a TLS error, export the TELUS root CA from the Windows cert store to a `.pem` and:

```
$env:SSL_CERT_FILE      = "C:\certs\telus-root.pem"
$env:REQUESTS_CA_BUNDLE = "C:\certs\telus-root.pem"
```

If that fight takes more than twenty minutes, abandon the branch and go back to `main`. That is a legitimate outcome and worth reporting as a finding — it tells you what to warn teams about.

### 5.2 The graph

```py
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from src import config

class TripStateTD(TypedDict, total=False):
    request: dict; plan: dict; candidates: dict; routes: list
    budget: dict; critic: dict; itinerary: list; trace: list
    meta: dict; revisions: int

def route_after_critic(state: TripStateTD) -> str:
    if state.get("critic", {}).get("verdict") == "approved":
        return "ship"
    if state.get("revisions", 0) >= config.CRITIC_MAX_ITERATIONS:
        return "ship"        # the bound lives in the edge, not in a while loop
    return "revise"

g = StateGraph(TripStateTD)
g.add_node("planner",   planner_node)
g.add_node("executors", executors_node)     # parallel inside the node
g.add_node("critic",    critic_node)
g.add_node("formatter", formatter_node)

g.add_edge(START, "planner")
g.add_edge("planner", "executors")
g.add_edge("executors", "critic")
g.add_conditional_edges("critic", route_after_critic,
                        {"revise": "planner", "ship": "formatter"})
g.add_edge("formatter", END)

app = g.compile()
```

Reuse `src.tools` unchanged inside the nodes — the whole point of the facade is that the orchestration layer is swappable and the tool layer is not.

### 5.3 The payoff — a graph picture for the deck

```py
print(app.get_graph().draw_mermaid())
```

That returns **Mermaid text**, which is what you want. Do **not** call `draw_mermaid_png()` — it posts to `mermaid.ink` and the proxy will block it. Paste the Mermaid text into the deck, or render it in Streamlit:

````py
st.markdown(f"```mermaid\n{app.get_graph().draw_mermaid()}\n```")
````

### ✅ GATE 5

S1 produces the same itinerary shape on the `langgraph` branch as on `main`, and `draw_mermaid()` gives you a diagram. Then **merge nothing** — keep it as a parallel branch and decide after the kickoff whether teams should be pointed at it. Report the install experience to Teresa either way.

---

# PHASE 6 — Deploy (optional, 2 h)

## 6A. Local demo — the primary path

This is what Teresa validated and what you should plan to demo from.

```
.\.venv\Scripts\Activate.ps1
$env:FOODIE_DATA_BACKEND = "auto"
$env:FOODIE_CACHE = "on"
streamlit run app\streamlit_app.py
```

**On demo day, run it with the cache already warm** (Phase 7). Nothing else is required. VPN on.

## 6B. Cloud Run — only if you want the "it's deployed" story

### The gate that decides whether this is worth doing

Cloud Run egresses to the public internet, not the TELUS network. **Fuel iX may refuse a request that does not originate from TELUS.** Test that before you build anything:

```
gcloud config set project <YOUR_LAB_PROJECT_ID>
gcloud run deploy fuelix-probe `
  --source . `
  --region northamerica-northeast1 `
  --command python --args scripts/preflight.py `
  --no-allow-unauthenticated
```

Simpler still: deploy the Streamlit app with a `?probe=1` path, or just read the Cloud Run logs after one request. **If Fuel iX is unreachable from GCP, stop here** — a Cloud Run deployment that cannot call the LLM gateway is worse than no deployment. Record the finding; it is useful for the kickoff.

### 6B.1 Enable services

```
gcloud auth login
gcloud config set project <YOUR_LAB_PROJECT_ID>
gcloud services enable run.googleapis.com cloudbuild.googleapis.com `
  artifactregistry.googleapis.com secretmanager.googleapis.com
```

**Region:** Google Cloud has no Calgary region. Use `northamerica-northeast1` (Montréal) or `northamerica-northeast2` (Toronto), and confirm which one your org's data-residency policy allows.

### 6B.2 Secrets — never as `--set-env-vars`

Env vars set at deploy time are visible to anyone who can `describe` the service and are baked into revision metadata. Keys go in Secret Manager.

PowerShell writes UTF-16 with a BOM if you pipe naively, which corrupts the secret. Use this:

```
$tmp = Join-Path $env:TEMP "k.txt"

[System.IO.File]::WriteAllText($tmp, "<your fuel ix key>", [System.Text.UTF8Encoding]::new($false))
gcloud secrets create fuelix-api-key --data-file=$tmp

[System.IO.File]::WriteAllText($tmp, "<your google maps key>", [System.Text.UTF8Encoding]::new($false))
gcloud secrets create google-maps-api-key --data-file=$tmp

Remove-Item $tmp
```

Grant the runtime service account read access:

```
$PROJECT_NUMBER = gcloud projects describe (gcloud config get-value project) --format="value(projectNumber)"
$SA = "$PROJECT_NUMBER-compute@developer.gserviceaccount.com"

gcloud secrets add-iam-policy-binding fuelix-api-key `
  --member="serviceAccount:$SA" --role="roles/secretmanager.secretAccessor"
gcloud secrets add-iam-policy-binding google-maps-api-key `
  --member="serviceAccount:$SA" --role="roles/secretmanager.secretAccessor"
```

### 6B.3 Dockerfile

```
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Build the SQLite fallback into the image so local mode always works
RUN python data/seed.py

ENV PORT=8080 \
    PYTHONUNBUFFERED=1 \
    FOODIE_DATA_BACKEND=auto \
    FOODIE_CACHE=on

# shell form so $PORT expands at runtime
CMD streamlit run app/streamlit_app.py \
    --server.port=$PORT \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --server.enableCORS=false \
    --server.enableXsrfProtection=false \
    --browser.gatherUsageStats=false
```

`requirements.txt` needs `streamlit` and `pandas` added — the kit's core is stdlib, but the UI is not.

Add a `.dockerignore`:

```
.venv/
.git/
.env
__pycache__/
*.pyc
data/api_cache.sqlite
```

> **Do not `COPY .env`.** That is how keys end up in a container image.

**Streamlit flags explained:** `--server.address=0.0.0.0` is mandatory (Cloud Run health-checks from outside the container); `--server.headless=true` stops it trying to open a browser; CORS and XSRF are disabled because Cloud Run terminates TLS in front of the app and Streamlit's XSRF check rejects the proxied origin.

### 6B.4 Deploy

```
gcloud run deploy foodie-agent `
  --source . `
  --region northamerica-northeast1 `
  --port 8080 `
  --memory 1Gi `
  --cpu 1 `
  --timeout 300 `
  --max-instances 3 `
  --no-allow-unauthenticated `
  --set-env-vars "FUELIX_BASE_URL=https://api.fuelix.ai/v1,FOODIE_MODEL_DEFAULT=claude-sonnet-4,FOODIE_DATA_BACKEND=auto,FOODIE_CACHE=on" `
  --set-secrets "FUELIX_API_KEY=fuelix-api-key:latest,GOOGLE_MAPS_API_KEY=google-maps-api-key:latest"
```

Notes:

- `--timeout 300` — the agent needs up to 60 s; the 300 s default request timeout gives headroom for a cold start plus a Critic revision.  
- `--max-instances 3` — a cheap guard against a runaway loop billing the project.  
- `--no-allow-unauthenticated` — most enterprise orgs block public Cloud Run services by org policy anyway, and a TELUS Internal app should not be public.

### 6B.5 Reaching an authenticated service

```
gcloud run services proxy foodie-agent --region northamerica-northeast1 --port 8080
```

Then open `http://localhost:8080`. This is the clean way to demo an internal Cloud Run service — no public URL, no IAP setup, works over VPN.

### 6B.6 API key restriction on Cloud Run

Your Google Maps key restriction changes once you deploy: requests now come from Google's egress ranges, not the TELUS proxy. If you set an IP restriction for laptop dev, it will break here. Either use **Application restriction \= None** with a tight **API restriction** (Places New \+ Routes only), or set up a static egress IP via a VPC connector \+ Cloud NAT — which is more infrastructure than a hackathon warrants.

### ✅ GATE 6

`gcloud run services proxy` reaches the app, a request returns a full itinerary, and the logs show `tool_backends` with `google_places`. If Fuel iX is blocked from GCP, record that and stay local — that is a finding, not a failure.

---

## 6C. Streamlit Community Cloud — the fastest path, and its four traps

Cloud Run is the right target for a TELUS-internal app: private by default, keys in Secret Manager, region controllable. Community Cloud is the right target when you want a URL in fifteen minutes and do not mind that it is public. Both are gated on the **same** question as 6B — whether Fuel iX answers a request that did not come from the TELUS network — so **do 6C.0 before anything else**.

### 6C.0 The gate, again

Community Cloud runs on AWS. You have no VPC, no egress control and no static IP, so if Fuel iX refuses non-TELUS origins there is no workaround at this layer.

The app will **not** crash if it is refused: `_describe_llm_failure()` catches it, logs the reason, and drops to the deterministic pipeline. That is the trap. You get a working-looking demo with **zero LLM calls** — no planner, no self-directed search, no critic judgement, no formatter. Everything that makes it an agent is gone and nothing on screen shouts about it.

**How to check in one look:** open the **Agent trace** panel after a plan. If Fuel iX was refused there is a `planner:` line saying so. `llm_calls` in the raw state is the other tell — it should be 13–19 for a Tier 2 live run, not 0.

If it is blocked: stop, record the finding for the kickoff, and demo locally with the Phase 7 warm cache. A deployment that cannot call the gateway is worse than no deployment.

### 6C.1 Publish the branch the deploy button is looking at

The "Unable to deploy — the app's code is not connected to a remote GitHub repository" dialog usually is not what it sounds like. Read the second half of it: *"publish the current branch."* Streamlit looks for **your current branch** on the remote, not for the repo.

This repo's default branch is `master`. If your local branch is called something else, the remote has no branch of that name and the button reports the repo as unconnected:

```
git branch -vv          # * main 31f6424 [origin/master]   <- mismatch
git ls-remote --heads origin
```

Fix it once by aligning the local name with the remote:

```
git branch -m main master
git branch --set-upstream-to=origin/master master
```

`git push` now works with no arguments, and the `git push origin main:master` dance goes away.

### 6C.2 Entry point — there is only one that works

| File | Community Cloud |
| :---- | :---- |
| `app/streamlit_app.py` | ✅ single process, calls the orchestrator in-process |
| `frontend/streamlit_app.py` | ❌ thin HTTP client; needs `app/api.py` running separately |

Set **Main file path** to `app/streamlit_app.py`. Python 3.11+. `requirements.txt` is already at the repo root where the host looks for it.

### 6C.3 Secrets — root level only

Paste this into **Advanced settings → Secrets**:

```
FUELIX_API_KEY = "..."
GOOGLE_MAPS_API_KEY = "..."
FOODIE_DATA_BACKEND = "auto"
FOODIE_CACHE = "on"
```

No code change is needed. Streamlit exposes **root-level** secrets as OS environment variables as well as through `st.secrets`, and `src/config.py` reads `os.environ`. Keys nested under a `[section]` are **not** exported as environment variables — put these at the top level or `config.py` will see an empty key and silently switch to `MOCK_MODE`.

`.env` is gitignored and must stay that way. The repo is public.

### 6C.4 The database that is not in the repo

`data/foodie.sqlite` is gitignored because it is derived; `data/csv/` is the source of truth and is tracked. A deploy therefore starts with the CSVs and **no database**, and the local catalogue is not only the offline demo — it is what `FOODIE_DATA_BACKEND=auto` falls back to when Google is unreachable.

`src/bootstrap.py` seeds it at startup when it is missing, from both entrypoints, and leaves an existing database strictly alone. Nothing to do — but if it ever fails, the sidebar says **"No offline fallback: …"** and `/health` carries `db_bootstrap`. `eval/verify_deploy.py` asserts the whole path, including that the rebuild is byte-identical.

### 6C.5 It is public, and so is your billing

A free Community Cloud app is public by default and this repo is public. Anyone with the URL can press **Plan my trip**, which spends **your Google Places quota** (billed) and your Fuel iX quota.

- Restrict viewers by email in the app's settings — Community Cloud supports an allow-list.
- Google Cloud Console → the Maps key → **Application restrictions**. An IP restriction set for laptop work will 403 from AWS. Use **None** plus a tight **API restriction** (Places API New + Routes only), and set a quota cap.
- The runbook header says **TELUS Internal**. Putting the reference implementation on a public URL is a classification call — make it deliberately, not by clicking Deploy.

### ✅ GATE 6C

The app loads, a plan returns a full itinerary, the **Agent trace** shows `restaurant:` lines with `google_places`, and `llm_calls` is non-zero. If `llm_calls` is 0, Fuel iX is blocked — that is GATE 6C failing, and the answer is 6A.

---

# PHASE 7 — Demo-day insurance (45 min)

Do all four. Each one has saved a hackathon demo before.

### 7.1 Warm the cache

`FOODIE_CACHE=on` writes to `data/api_cache.sqlite`. Run every scenario you plan to show, at least twice, the day before:

```
$env:FOODIE_CACHE = "on"
$env:FOODIE_DATA_BACKEND = "auto"
python -m src.orchestrator          # S1
python eval\acceptance.py           # S1-S3
```

Then **do not delete `data/api_cache.sqlite`**. If the venue Wi-Fi is bad or you hit a quota, cached responses carry the demo.

### 7.2 Prove the local fallback works

```
$env:FOODIE_DATA_BACKEND = "local"
streamlit run app\streamlit_app.py
```

Full itinerary, no network at all. This is the insurance path in the Build Guide troubleshooting table, and you should have personally seen it work.

### 7.3 Record a backup

Screen-record a clean end-to-end run: request typed → agents working → itinerary → agent trace → map. Two to three minutes. If anything fails live, you play the recording and keep talking. Judges score the solution, not your luck with Wi-Fi.

### 7.4 Freeze and tag

```
git add -A
git commit -m "Demo-ready reference implementation"
git tag demo-ready
```

Freeze early. The Build Guide already tells teams to freeze code early afternoon on Day 2 and protect the 10-minute presentation clock — hold yourself to the same rule.

---

# Troubleshooting

| Symptom | Cause | Fix |
| :---- | :---- | :---- |
| `foodie.sqlite not found` | seed never ran | `python data\seed.py` |
| seed produces 8 restaurants | old `seed.py` | apply the Phase 0.2 fix |
| CSV MD5 changed after seeding | old `seed.py` overwrote the KB | restore CSVs from Drive, apply the fix |
| `FUELIX_API_KEY not set` | `.env` missing or unreadable | fill `.env` at the kit root; `config.load_dotenv()` reads it on import |
| Fuel iX HTTP 400 | wrong model ID | check the Dev Portal `models` list, set `FOODIE_MODEL_DEFAULT` verbatim |
| Fuel iX HTTP 401 | key not activated / wrong project | regenerate in the Dev Portal |
| Fuel iX times out, no HTTP code | proxy not visible to Python | `python -c "import urllib.request;print(urllib.request.getproxies())"` — must show the pac host |
| Places HTTP 403 `API_KEY_HTTP_REFERRER_BLOCKED` | key has a website restriction | set Application restriction to None (see Phase 1.4) |
| Places HTTP 403, other | Places API **(New)** not enabled, or billing off | enable Places API (New); check billing |
| Places HTTP 429 | team quota | `FOODIE_CACHE=on`, stagger calls, check quota on the project |
| Places returns results but no `rating` | field mask too narrow | the mask is in `places_live.py::search_restaurants` |
| Routes returns no routes | lat/lon swapped, or points unreachable on foot | check the payload order: `{"latitude": …, "longitude": …}` |
| `ModuleNotFoundError: src` | wrong working directory | run from the kit root: `python -m src.orchestrator` |
| Streamlit blank page / hangs | `NO_PROXY` missing loopback | `NO_PROXY=localhost,127.0.0.1,::1` |
| `TypeError` / deprecation on `use_container_width` | removed in the 2026 Streamlit releases | use `width="stretch"` (or `width="content"`); most examples on the web are still on the old kwarg |
| `asyncio.run() cannot be called from a running event loop` | Streamlit already has a loop | use the `run_sync` helper (Phase 3.1) |
| `SSLCertVerificationError` in langchain/httpx but not urllib | httpx uses certifi, urllib uses the Windows store | set `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE` to the TELUS root CA |
| Allergen trap not excluded | running S1 against live Places | Places has no allergen data — graded S1 runs on `FOODIE_DATA_BACKEND=local` |
| Itinerary over 60 s | sequential executors | parallelize (Phase 3.1); lower `TOOL_LOOP_MAX_ROUNDS` |
| Cloud Run: container failed to start | Streamlit not bound to `$PORT`/`0.0.0.0` | check the `CMD` is in shell form so `$PORT` expands |
| Cloud Run: 403 on every LLM call | Fuel iX rejects non-TELUS egress | stay local, or investigate a VPC connector |
| Streamlit install blocked | locked-down pip | use `app/cli.py` — a fully accepted UI per the rubric |

---

# Appendix A — Everything a judge will ask, and where the answer lives

| Question | Where you point |
| :---- | :---- |
| "How is this different from last year's chatbot?" | Agent trace tab \+ the "An Agent — Not a Chatbot" slide |
| "How do you know it's using live data?" | `st.meta["tool_backends"]` on screen |
| "What stops it recommending a peanut restaurant?" | `exclude_flags` at the tool layer — the model never sees the venue — plus the Critic re-check. Demo it by toggling the allergy off. |
| "How do you know the budget is right?" | `check_budget` is pure Python, no LLM |
| "What if the Critic loops forever?" | `CRITIC_MAX_ITERATIONS = 2`, bounded in the edge/loop |
| "What if the model makes up a slot name?" | `validate_critic_output` against the closed `SLOT_IDS` vocabulary |
| "What if it recommends a restaurant 50 km away?" | Route agent measures every leg; legs over `max_walk_km` become Critic issues |
| "What if the API is down?" | `FOODIE_DATA_BACKEND=local` \+ warm cache; `last_backend_report()` records the fallback honestly |

---

# Appendix B — Command cheat sheet

```
# activate
cd $HOME\Desktop\Travel-Foodie-Agent-Reference
.\.venv\Scripts\Activate.ps1

# proxy (session)
$env:HTTP_PROXY="http://pac.tsl.telus.com:8080"
$env:HTTPS_PROXY="http://pac.tsl.telus.com:8080"
$env:NO_PROXY="localhost,127.0.0.1,::1"

# health
python scripts\preflight.py
python -c "import urllib.request;print(urllib.request.getproxies())"

# build data
python data\seed.py

# run
python -m src.orchestrator                       # both tiers, console
python app\cli.py --tier 2 --json                # CLI UI
streamlit run app\streamlit_app.py               # web UI (NOT frontend\)
uvicorn app.api:app --port 8080                  # backend; GET /diagnostics

# force backends
$env:FOODIE_DATA_BACKEND="local"                 # offline insurance
$env:FOODIE_DATA_BACKEND="live"                  # fail loudly, no fallback
$env:FOODIE_DATA_BACKEND="auto"                  # try live, fall back

# demo-day insurance (run on the machine you will demo from)
python scripts\warm_cache.py --golden            # warm cache + freeze a plan
$env:FOODIE_DEMO_MODE="on"                       # replay data\golden_plan.json

# grade
python scripts\smoke_test.py
python eval\acceptance.py

# deploy
gcloud run deploy foodie-agent --source . --region northamerica-northeast1 ...
gcloud run services proxy foodie-agent --region northamerica-northeast1 --port 8080
```

