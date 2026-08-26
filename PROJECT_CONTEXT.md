# 2026 RF Design West Offsite Hackathon — Project Context

**Classification:** TELUS Internal **Owner:** Emma (Xu Wang) · organizer / technical lead **Co-facilitator:** Teresa · GCP projects, API keys, kit validation **Context captured:** 26 Aug 2026 **Purpose:** Single reference file for this project. Load this before any future work on the hackathon so nothing has to be re-explained.

---

## 1\. The event

|  |  |
| :---- | :---- |
| **Event** | RF Design West Offsite Hackathon — "Traveling Foodie Agent" |
| **Theme** | Agentic AI application (multi-agent), **not** a chatbot |
| **Location** | Calgary offsite, most of the core team in person |
| **Format** | 4 days · 3 teams in parallel · 10-min demo \+ 5-min Q\&A per team |
| **Deck** | `2026 Hackathon_Kickoff` — Google Slides `1qOpank7MLncFwjzPie4WMdaCwBkyH3Is` |
| **Starter kit** | Drive folder `1UsibtjAxForO3DxdmUyouo6k2a65vt-l` |

### Agenda (from the FINAL deck)

| When | Block | Activity |
| :---- | :---- | :---- |
| Pre-week | Homework \+ M0 | VibeCoding 101 \+ Prompt Engineering. M0 gate: VS Code, Fuel iX access, assistant config. Team GCP key pre-issued (Theresa/Teresa) |
| Day 1 PM | Kickoff \+ Tier 0 | Use-case briefing; every team stands up the Tier 0 copilot |
| Day 2 AM+PM | Tier 1 → Tier 2 build | Move to the IDE; mentor check-in |
| Day 3 AM | Tier finalize | Finish the agent, build the presentation |
| Day 4 AM | Demos \+ judging | 10-min presentation \+ live demo, scored on the rubric |

### Teams

| Team 1 | Team 2 | Team 3 |
| :---- | :---- | :---- |
| Azim | Marc-Antoine | SK |
| Elham | Mehdi | Flori |
| Ryan | Linton | Shehryar |
| Wei | Ramsey |  |
| Mansi (remote) | Gaurav (remote) |  |

### People and attendance

- **Judges:** Kathleen ("Kat", Property & Program Manager — formerly Planning & GIS manager) and Rogelio ("Ro", implementation / construction side). Both are managers under **Osmond**, not part of the RF Design team, and **neither attended last year's hackathon** — so the chatbot-vs-agent distinction has to be spelled out for them at kickoff.  
- **Osmond** — Vancouver-based, joining the offsite virtually.  
- **Bernard** (VP) — attending a separate Toronto event on **Sept 10**, not Calgary.  
- **Gerardo** — asks for one-pager summaries; requested a single slide on "what makes this agentic". Team agreed 2–3 slides is acceptable.  
- **Remote / not attending:** Mansi and Gaurav (GTLPs sit under a different manager and cost centre — travel not approved). **Ryan** attends in person (travels down from Edmonton). Gaurav is a contractor.  
- Vendors met at the offsite: Ro Hampton (site visits), Red Oak, Dennis (AB prime).

---

## 2\. Last year vs this year — the framing that matters

**Last year (all four teams):** RAG chatbots. Local CSV knowledge base \+ Fuel iX for LLM calls \+ Streamlit UI. Some teams added a dashboard. Input → RAG query → LLM response. Nothing testable.

**This year:** multi-agent system on live data. Planner → parallel Executors → Critic → Formatter, over a shared `TripState`.

The deck already carries this as the **"An Agent — Not a Chatbot"** slide, five comparison rows:

|  | Chatbot | Agent |
| :---- | :---- | :---- |
| **Job** | Answers a question | Executes a task |
| **Loop** | You run the loop | It runs the loop — plan → execute → check → revise |
| **Hard rules** | "No peanuts" is a line in the prompt | "No peanuts" is a filter in code, re-checked by a Critic |
| **Output** | Text in a chat window | A deliverable \+ the trace behind every pick |
| **Proof** | Nothing you can test | 0 allergen violations · budget ≤ limit · \< 60 s |

**Agreed slide placement:** insert between slides 3 and 4 (before the detailed architecture slide), because the audience does not yet know what an agent is. Move the "example output" slide (slide 6\) to **backup** so teams are not boxed into one output format.

**For the panel:** Tier 0 is deliberately a chatbot — the safety net so every team demos something — but it **caps at 59/100**. Agentic architecture alone is 40 of the 100 points.

---

## 3\. Architecture (the fixed skeleton — every team builds this)

```
USER INPUT ─→ PLANNER ─→ EXECUTORS (one per slot) ─→ CRITIC ─→ FORMATTER ─→ ITINERARY
                 ↑                                      │
                 └────── revise (max 2 iterations) ──────┘
```

| Stage | Job | Tier |
| :---- | :---- | :---- |
| **Planner** | Splits the request into slots. Every slot carries budget \+ allergies. Strict JSON out. **Does not choose venues** — if the Planner picks restaurants you have built a chatbot with extra steps. | 1 |
| **Restaurant executor** | Places API | 1 |
| **Attraction executor** | Places API | 2 |
| **Route executor** | Routes API | 2 |
| **Budget executor** | Pure Python, **no LLM** — money is arithmetic, not language | 1 |
| **Critic** | Re-reads the whole plan. Allergens, budget, hours. Names the slots to redo. | 2 |
| **Formatter** | Assembles the day-by-day plan with the reason behind every pick | 1 |

Sequential executors \= Tier 1\. `asyncio.gather` \= Tier 2\.

**Shared state — `TripState`:** `request · plan · candidates · routes · budget · critic · itinerary · trace · meta`. Every stage takes it in, changes its own part, hands it back. That one contract is how you bolt on a new agent without rewriting the pipeline.

### The three non-negotiables (where the 40 agentic points are won)

1. **Tools only** — agents reach data through tool calls, never from the model's memory.  
2. **Constraints in code** — allergen filter at the tool layer, budget arithmetic in pure Python.  
3. **Bounded loop** — Critic returns slot IDs from a closed list, max 2 revisions, then ship.

---

## 4\. Scoring rubric (100 points)

| Category | Weight | Notes |
| :---- | :---- | :---- |
| 1\. Dataset & API Mastery | **40** | Live Places/Routes \+ fallback dataset, single-source \+ cross-source integration, constraints satisfied |
| 2\. Solution Completeness | **25** | Functional prototype (10) \+ contextual insights (15) |
| 3\. User Experience & Interface | **20** | Design excellence (10) \+ interaction & visuals (10). Agent-trace visibility ≈ 7 pts; map view is a \+2 bonus |
| 4\. Collaboration & Presentation | **15** | 10-minute delivery, within time |

### Tiers

| Tier | What | Who |
| :---- | :---- | :---- |
| **0 Copilot** (floor) | Fuel iX Copilot GUI \+ city KB answering S1–S3 | every team |
| **1 Scripted agent** | Planner → Restaurant → Budget → Formatter \+ live Places | most teams |
| **2 Multi-agent** | Parallel executors \+ Attraction/Route \+ Critic loop | strong teams |

### Graded scenarios

| ID | Input | Must hold |
| :---- | :---- | :---- |
| **S1** | 2 days Calgary · $500 · international · **peanut allergy** | zero allergen leaks; budget ≤ 500 |
| **S2** | 2 days · $300 · Asian (live: Vancouver; local fallback: Calgary) | budget trade-offs |
| **S3** | 1 day · $150 · family (live: Montreal; local fallback: Calgary 1-day) | 1-day plan works |

### Planted traps in the staged data (richer than currently documented)

| Trap type | Venue | Must hold |
| :---- | :---- | :---- |
| `peanut_risk` | **r008 Peanut Garden Thai** | never in an S1 plan |
| `closed_monday` | **r005 Jade Lantern Dim Sum** | never scheduled Monday |
| `closed_monday` | **a002 Prairie Heritage Museum** | never scheduled Monday — *attraction-side trap* |
| `budget_buster` | **r003 Ember & Oak Steakhouse** ($$$$, $118 pp) | flips budget to warning/exceeded |
| `budget_buster` | **r049 Mount Royal Fine Dining** ($$$$$, $132 pp) | same, higher severity |

> The old docs reference "Peanut Palace" and "Midnight Diner" — those venues only existed in the deleted 8-row dataset. Correct the doc text; the *predicate* `(c.get("dietary_flags") or {}).get("peanut_risk")` still works unchanged.

---

## 5\. Technical environment (TELUS)

| Component | Value |
| :---- | :---- |
| **LLM gateway** | Fuel iX — `https://api.fuelix.ai/v1`, OpenAI-compatible chat completions. **All LLM traffic must go through Fuel iX.** No ChatGPT, no direct vendor endpoints. |
| **Default model** | `claude-sonnet-4` (`FOODIE_MODEL_DEFAULT`). Other models available in the Dev Portal — check the `models` endpoint. |
| **Fuel iX key** | app.fuelix.ai → app switcher (dots, top right) → **API** → Dev Portal → create project → copy key. Each person creates their own; takes seconds. |
| **Maps** | Google **Places API (New)** \+ **Routes API**. NOT Places Legacy / Directions / Distance Matrix. |
| **GCP projects** | Three team projects (Lab 1 / Lab 2 / Lab 3), one restricted API key each. Plus `theresa-test-lab` used for validation. Emma and Teresa have access to all. |
| **Proxy** | `HTTP_PROXY` / `HTTPS_PROXY` \= `http://pac.tsl.telus.com:8080`, `NO_PROXY=localhost,127.0.0.1` |
| **VPN** | Places API confirmed working **on VPN**. Off-VPN not yet tested. |
| **IDE** | VS Code (install from go/SSS) \+ Claude as the coding assistant. Last year was Cline — **this year resolved to Claude**. |
| **UI** | Streamlit (`localhost:8501`) or the kit's `app/cli.py` — CLI is a fully accepted UI. |

### Validated so far (Teresa, 25 Aug)

- ✅ Places API (New) working locally on VPN — real Calgary and Toronto restaurants returned, cuisine filter (Thai) works, results confirmed **not** from the local CSVs.  
- ⏳ Routes API enabled on all projects but **not yet tested**.  
- ⏳ Tier 2 not yet built.  
- ⏳ Chat-window UI not yet added (Gerardo will expect one).  
- Only change needed to make it work: **proxy setup**. Kit is otherwise sufficient.

---

## 6\. Starter kit — structure

Drive folder `1UsibtjAxForO3DxdmUyouo6k2a65vt-l` · `Travel-Foodie-Agent-Hackathon-Starter` (FINAL · Live-API edition). Core runtime is **Python 3.10+ standard library only**.

```
Travel-Foodie-Agent-Hackathon-Starter/
├── README.md
├── .env.example                 # copy to .env (gitignored)
├── .gitignore
├── requirements.txt             # optional extras only (core = stdlib)
├── Travel_Foodie_Agent_Build_Guide.md
├── data/
│   ├── seed.py                  # builds foodie.sqlite from the CSVs
│   └── csv/                     # Tier 0 knowledge-base uploads (60 restaurants / 25 attractions)
├── prompts/                     # tier0_copilot.md, planner.md, restaurant.md, critic.md, formatter.md
├── src/
│   ├── config.py                # env-driven modes (live|local|auto)
│   ├── fuelix_client.py         # stdlib Fuel iX client + run_tool_loop
│   ├── state.py                 # TripState + SLOT_IDS + TOOL_SCHEMAS
│   ├── orchestrator.py          # run_tier1 / run_tier2
│   ├── agents/                  # Critic helpers
│   └── tools/                   # __init__ facade + places_live + routes_live + local_catalog + cache + budget
├── app/cli.py
├── eval/                        # scenarios + acceptance.py
└── scripts/smoke_test.py        # M0 gate
```

### Key API surface (memorise these — agents import from `src.tools` ONLY)

```py
search_restaurants(city, meal, area=None, cuisine=None,
                   price_level_max=None, exclude_flags=None, limit=5) -> list[dict]
get_venue_details(venue_id) -> dict
search_attractions(city, category=None, limit=5) -> list[dict]
estimate_travel(from_lat, from_lon, to_lat, to_lon, mode="walk") -> dict
check_budget(items, limit) -> dict
last_backend_report() -> dict          # what actually ran — show this in the demo
TOOL_IMPLS, TOOL_SCHEMAS               # for the Fuel iX tool loop
```

````py
# src/fuelix_client.py
FuelixClient(api_key=None, base_url=None, timeout=60, max_retries=3)
  .chat(model, system, user, tools=None, temperature=0.2, max_tokens=1200, messages=None) -> dict
  .telemetry  # {"llm_calls", "input_tokens", "output_tokens"}
run_tool_loop(client, model, system, user, tools, tool_impls, max_rounds=None) -> dict
parse_json_reply(text) -> dict         # tolerant of ```json fences

# src/orchestrator.py
validate_critic_output(critic_json, days=2) -> (bool, list[str])   # ALWAYS call before re-planning
run_tier1(request) -> TripState
run_tier2(request) -> TripState

# src/state.py
MEALS = ("breakfast", "lunch", "dinner")
slot_ids(days=2, meals=MEALS, attractions_per_day=1)   # "day1.breakfast" … "day2.attraction1"
is_valid_slot(slot, days=2)
````

### Environment variables

| Var | Meaning |
| :---- | :---- |
| `FUELIX_API_KEY` | Fuel iX Dev Portal key. **No key \= MOCK mode** (deterministic offline stand-ins) |
| `FUELIX_BASE_URL` | default `https://api.fuelix.ai/v1` |
| `FOODIE_MODEL_DEFAULT` | default `claude-sonnet-4` |
| `FOODIE_MODEL_{PLANNER,RESTAURANT,ATTRACTION,ROUTE,CRITIC,FORMATTER}` | per-agent overrides |
| `GOOGLE_MAPS_API_KEY` | per-team restricted GCP key |
| `FOODIE_DATA_BACKEND` | `auto` (default) | `live` | `local` |
| `FOODIE_CACHE` | `on` (default) | `off` — caches Google responses in `data/api_cache.sqlite` |

Constants worth knowing: `LATENCY_BUDGET_S = 60`, `CRITIC_MAX_ITERATIONS = 2`, `TOOL_LOOP_MAX_ROUNDS = 4`.

`config.load_dotenv()` runs on import and uses `os.environ.setdefault`, so a real shell variable always wins over `.env`.

---

## 7\. Starter kit — outstanding change list (before sharing)

**Status: 1 blocking fix supplied · 5 follow-ups (\~30 min total)**

### 7.1 BLOCKER — `data/seed.py`

**Defect:** `seed.py` generated its own 8-restaurant / 4-attraction dataset in an unrelated schema and **overwrote** `data/csv/calgary_restaurants.csv` and `calgary_attractions.csv`. Both README and Build Guide open with `python data/seed.py` — so every participant's first command destroyed the 60-restaurant / 25-attraction Tier 0 knowledge base and replaced it with 8 rows.

| Layer | Ran on | Rows |
| :---- | :---- | :---- |
| Tier 0 copilot KB (CSV upload) | staged CSVs | 60 \+ 25 |
| Tier 1/2 offline path (`local_catalog.py` → SQLite) | `seed.py` output | 8 \+ 4 |
| `scripts/smoke_test.py` | `seed.py` schema | 8 \+ 4 |

The schemas were also incompatible (`dietary_flags`/`price_level`/`avg_meal_cost` vs `allergens_present`/`price_band`/`cost_per_person`).

**Fix (supplied):** `seed.py` rewritten to treat the **CSVs as the single source of truth** and build the SQLite mirror from them. Never writes to `data/csv/`.

| CSV (source of truth) | SQLite (what `local_catalog.py` reads) |
| :---- | :---- |
| `price_band` `$`…`$$$$$` | `price_level` 1…5 — **not capped**, so the `$$$$$` budget-buster stays distinguishable |
| `cost_per_person` | `avg_meal_cost` / `cost` |
| `allergens_present` `peanut;tree_nut;…` | `dietary_flags` JSON — explicit `true`/`false` for all 9 canonical allergens as `<allergen>_risk` |
| `dietary_options` | same JSON, as `<option>_options` |
| `open_time` \+ `close_time` \+ `closed_days` | `hours` JSON, 7 keys, closed days as `{"open": null, "close": null}` |
| `neighbourhood` | `area` (matches the Planner's `area_hint`) and `address` |
| `duration_min` | `visit_duration_min` |
| `is_trap`, `meal_types`, `slot_types`, `description` | carried through as extra columns |

Three derived fields (deterministic, adjustable at the top of the file):

- `review_count` — md5 of `venue_id` (**not** `hash()`, which is salted per process). Needed because `ORDER BY rating DESC, review_count DESC` requires a stable tiebreaker. Identical on every machine, every run.  
- `indoor` — `category == museum`, or description mentions atrium / observation deck / indoor / dome theatre.  
- `kid_friendly` — restaurants: `price_level <= 2` and not a budget-buster; attractions: any category except `activity`.

Also added: **city auto-discovery** (globs `*_restaurants.csv`, so dropping `vancouver_restaurants.csv` in for S2/S3 needs no code change); **self-verifying output** (per-city counts, SQL-computed peanut-risk count, every `is_trap` row); **hard failure** if a CSV is missing instead of silently regenerating one.

**Verified** against `local_catalog.py`'s exact query and filter logic: S1 peanut hard constraint (excluded when `exclude_flags` set, reachable without it), hours trap (r005 closed Mon / open Tue), budget-buster trap ($$$$ and $$$$$ filtered by `price_level_max=3`, r049 `price_level == 5` not capped), cuisine aliases \+ area filter \+ ordering \+ attractions, CSV md5 unchanged after seeding, identical DB across repeated runs. **Drop-in — no other file needs to change.**

### 7.2 Follow-ups

| \# | File | Action | Time |
| :---- | :---- | :---- | :---- |
| **2.3** | `Travel_Foodie_Agent_Build_Guide.md` | **Do this first.** In-document kit link says folder `1UsibtjAxForO3DxdmUyouo6k2a65vt-l` but the folder being shared is `1q2x_WP4z2uwS-pKDKvALclln_nI4z5D7`. Reconcile before sending or someone works from the wrong copy all week. | 5 min |
| **2.1** | `scripts/smoke_test.py` | Predicate works unchanged; fix any comment/doc naming "Peanut Palace" / "Midnight Diner" → use the real trap table in §4 | 5 min |
| **2.2** | `src/tools/local_catalog.py` | (a) Remove the `# Soft fallback: ... so demos never go blank on sparse seed data` branch — a workaround for 8 venues; with 60 it silently returns the wrong cuisine and a judge will find it. (b) `search_restaurants(city, meal, ...)` accepts `meal` and **ignores it**; `meal_types` is now in the DB, so this is one line: \`AND (meal\_types \= '' OR meal\_types LIKE '%' |  |
| **2.4** | repo | Delete `src/__pycache__/`, `src/tools/__pycache__/`, `eval/__pycache__/`, and `data/foodie.sqlite` (build artifact — shipping it lets a team skip `seed.py` and run on a stale DB). `.gitignore` it. | 5 min |
| **2.5** | docs | Build Guide §0 says "party of two" → deck says **party of ten**. README / Build Guide §3.1 says "confirm coding assistant with Teresa (Cline vs Claude Code)" → resolved: **was Cline / now Claude**. Already correct: `prompts/tier0_copilot.md` search-is-ON wording; `requirements.txt` core stdlib-only. | 5 min |

### 7.3 Not a defect — flag at kickoff

The deck's "WHAT RESULT LOOKS LIKE" screenshot shows a three-mode web UI with a Leaflet map and an agent-trace panel. The kit ships `app/cli.py` only. **That screenshot is the reference implementation, not the starting point.** Say so at kickoff or a team will assume it is already behind on day one — and note that agent-trace visibility is worth \~7 points and the map is a \+2 bonus, so the CLI is a floor, not a ceiling.

---

## 8\. Open action items (from the 25 Aug sync)

### Teresa

- [x] Ask Gerardo whether to give teams early access to Google Cloud Labs 1/2/3 before the event  
- [x] Add team members to their respective GCP projects and ping them in the team chat  
- [ ] Test the Routes API and confirm it works alongside Places  
- [ ] Build the full foodie agent (Tier 2\) — target: next morning  
- [ ] Add a chat-window UI to the demo  
- [ ] Try refactoring with LangChain / LangGraph  
- [ ] Share last year's hackathon presentation materials

### Emma

- [ ] Update the starter package to include proxy configuration so teams don't have to figure it out  
- [ ] Build and test the agent end-to-end herself (this runbook)  
- [ ] Prepare the "What Makes It Agentic?" kickoff slide(s), between slides 3 and 4  
- [ ] Move the example-output slide to backup  
- [ ] Apply the §7 starter-kit change list before re-sharing the kit

### Both

- [x] Daily 10:30 AM check-ins Tue–Fri (Thursday has an 11:30 conflict, so 10:30 works)  
- Teresa is off Friday but will still attend the 10:30

---

## 9\. Side context (not hackathon, but active)

- **Carto maps down** — after a workbench migration, a new DSC policy restricts workbench connections to Carto and pushes BI layers instead. The team's use case (temporary spatial layers, exploratory GIS) doesn't fit BI layers, and Google Data Studio has no real GIS capability. DSC is still pushing back; resolution is a few days *once approved*, but approval is still being negotiated.  
- **BigQuery workbench migration** — the old workbench (RF Design West project) is deleted. A new workbench with a one-to-one dataset migration is set up; note the new project name has **dashes** in `RF-Design-West`. Use the new workbench for KPI calculations. Tables were migrated automatically; saved queries had to be moved by each team member (downloaded as backup).

---

## 10\. Hard rules (repeat at kickoff)

1. All LLM traffic through **Fuel iX** only — a non-approved endpoint invalidates the demo.  
2. API keys only in `.env` — never in code, notebooks, or slides.  
3. Hallucinated venues score **zero**. Ground every pick in API or KB data. Search is ON.  
4. Budget math stays **pure Python** — no LLM.  
5. Public data only. No SAM, no CEVA, no internal RF systems.

