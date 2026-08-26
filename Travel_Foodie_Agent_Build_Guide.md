# Traveling Foodie Agent — Build & Implementation Guide

*How to use the* `Travel-Foodie-Agent-Hackathon-Starter` *kit for the RF Design
West Offsite Hackathon (FINAL · Live-API edition).*


|                    |                                                                                                                                                                                |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Audience**       | Participants (all skill levels) + facilitators                                                                                                                                 |
| **Kit path**       | [https://drive.google.com/drive/folders/1UsibtjAxForO3DxdmUyouo6k2a65vt-l?usp=sharing](https://drive.google.com/drive/folders/1UsibtjAxForO3DxdmUyouo6k2a65vt-l?usp=sharing)   |
| **Companion**      | [https://drive.google.com/file/d/13vvyXHo8QxG9rr8l0MRiXM0eh6TerHoi/view?usp=drive_link](https://drive.google.com/file/d/13vvyXHo8QxG9rr8l0MRiXM0eh6TerHoi/view?usp=drive_link) |
| **Reference**      | [https://drive.google.com/file/d/1TWtGeAZrnoQwKJMve4Xl4AZbH-WU_xZR/view?usp=drive_link](https://drive.google.com/file/d/1TWtGeAZrnoQwKJMve4Xl4AZbH-WU_xZR/view?usp=drive_link) |
| **Core runtime**   | Python 3.10+ standard library (optional: Streamlit / pandas)                                                                                                                   |
| **Classification** | TELUS Internal                                                                                                                                                                 |


---



## 0. What you are building

An **agentic AI travel concierge** that turns:

> “2 days in Calgary, $500, international food, peanut allergy, party of two”

into a complete 2-day food itinerary — meals, attractions, routes, running
budget, with every pick reasoned and hard constraints respected.

You climb three tiers. **Clear the floor first.**


| Tier                 | Deliverable                                                    | Timebox                 |
| -------------------- | -------------------------------------------------------------- | ----------------------- |
| **0 Copilot**        | Fuel iX Copilot + city KB answering S1–S3                      | Day 1 AM (every team)   |
| **1 Scripted agent** | Python Planner → Restaurant → Budget → Formatter + live Places | Day 1 PM (most teams)   |
| **2 Multi-agent**    | Parallel executors + Routes + Critic loop                      | Day 2 AM (strong teams) |


---



## 1. Open the kit and run offline (5 minutes)

```bash
cd ".../Travel-Foodie-Agent-Hackathon-Starter"
python data/seed.py
python -m src.orchestrator
python scripts/smoke_test.py
```

**Pass criteria:**

- `allergen leaks: []` (Peanut Palace excluded)
- budget status `ok` or `warning` (never `exceeded` on S1)
- Critic slot-guard: valid `day2.dinner` accepted; `"dinner day 2"` rejected

No API keys required for this step. If it fails, fix seeding / Python path
before anything else.

**CLI UI (optional):**

```bash
python app/cli.py --tier 1
python app/cli.py --tier 2 --json
```

---



## 2. Kit map (what each folder is for)

```text
Travel-Foodie-Agent-Hackathon-Starter/
├── prompts/          ← edit these (main hacking surface for LLM behaviour)
├── src/orchestrator.py ← Tier 1 works now; Tier 2 skeleton ready to extend
├── src/tools/        ← live Google backends + local fallback behind one facade
├── src/fuelix_client.py ← Fuel iX chat + tool loop (stdlib only)
├── src/config.py     ← FOODIE_DATA_BACKEND=auto|live|local
├── data/             ← SQLite fallback + Tier 0 CSVs (from seed.py)
├── app/cli.py        ← demo UI without Streamlit
├── eval/             ← scenarios + acceptance script
└── scripts/smoke_test.py ← M0 gate
```

**Do not** call Google HTTP or SQLite from agent prompts. Always go through
`src.tools` (`search_restaurants`, `get_venue_details`, `search_attractions`,
`estimate_travel`, `check_budget`).

---



## 3. M0 gate — before Day 1 (mandatory)



### 3.1 Fuel iX

1. app.fuelix.ai → app switcher → **API** → Dev Portal → create project → copy key.
2. Copy `.env.example` → `.env` and set `FUELIX_API_KEY=...`
3. Confirm enabled model IDs (kit default: `claude-sonnet-4`).
4. Coding assistant (confirm with Teresa):
  - **Cline:** OpenAI Compatible · Base URL `https://api.fuelix.ai` · your key · model `claude-sonnet-4`
  - **Claude Code:** `ANTHROPIC_BASE_URL=https://api.fuelix.ai` + `ANTHROPIC_AUTH_TOKEN=<key>`



### 3.2 Google Maps (hackathon GCP project)

1. Collect your **per-team restricted** API key (issued by facilitators).
2. Put it in `.env` as `GOOGLE_MAPS_API_KEY=...`
3. APIs enabled on the project must include **Places API (New)** and **Routes API**
  (not Places Legacy / Directions / Distance Matrix).
4. `python scripts/smoke_test.py` must print a live Places probe success
  **or** you temporarily set `FOODIE_DATA_BACKEND=local` for offline practice.



### 3.3 Pre-work (from FINAL agenda)

- VibeCoding 101
- Prompt Engineering
- Install VS Code from go/SSS **early**

---



## 4. Tier 0 — Copilot (zero code)

1. Create a Fuel iX Copilot named `Foodie Concierge`.
2. Paste `prompts/tier0_copilot.md` into **instructions**.
3. Upload `data/csv/calgary_restaurants.csv` + `calgary_attractions.csv` as the knowledge base.
4. Internet search may be **ON** this year — still ground graded answers in the KB.
5. Test S1–S3. Refuse Peanut Palace. Stay under budget.

**Done when:** live chat demo answers all three scenarios correctly.

---



## 5. Tier 1 — Scripted agent (the real agentic entry)

`run_tier1()` already runs end-to-end offline. Your job: replace mock Planner /
Restaurant selection / Formatter with **real Fuel iX calls**.

### Build order

1. **Planner** — `FuelixClient.chat` + `prompts/planner.md` → STRICT JSON plan.
2. **Restaurant executor** — `run_tool_loop(...)` with `TOOL_SCHEMAS` +
  `TOOL_IMPLS`; let the LLM choose among tool results (not just top row).
3. **Formatter** — one LLM call using `prompts/formatter.md`.
4. **Keep Budget as pure Python** (`check_budget`) — scored teaching point.



### Technical requirements (from FINAL guideline)

- ≥ 4 real Fuel iX calls
- Planner decomposes the request
- ≥ 1 live Google Places call **or** declared fallback mode in output/meta
- Allergen + budget enforced **in code**
- Full itinerary **< 60 s**



### Wiring live Places

Default `FOODIE_DATA_BACKEND=auto`:

- With `GOOGLE_MAPS_API_KEY` → tries Places first, falls back to local on error.
- `TripState.meta["tool_backends"]` records what actually ran — show this in the demo.

```bash
# force modes while developing
FOODIE_DATA_BACKEND=local python -m src.orchestrator
FOODIE_DATA_BACKEND=live  python -m src.orchestrator
```

---



## 6. Tier 2 — Multi-agent ceiling

`run_tier2()` already adds Attraction picks, Route legs, and a Critic pass with
slot-guard hooks. Extend it:

1. **Parallelize** per-slot work with `asyncio.gather` (watch the Streamlit
  event-loop gotcha — wrap in one `asyncio.run()` helper).
2. **Route agent** should prefer Google Routes (`estimate_travel` live backend).
3. **Critic revision loop** (max 2):
  - Critic LLM using `prompts/critic.md`
  - Always `validate_critic_output(...)` before re-planning
  - Planner revises **only** named slots
4. Optional UI: Streamlit agent-trace + map view (CLI already counts).

---



## 7. Standard scenarios & planted traps


| ID     | Input (graded)                                                        | Must hold                         |
| ------ | --------------------------------------------------------------------- | --------------------------------- |
| **S1** | 2 days Calgary · $500 · international · **peanut allergy**            | zero allergen leaks; budget ≤ 500 |
| **S2** | 2 days · $300 · Asian (live: Vancouver; local fallback: Calgary)      | budget trade-offs                 |
| **S3** | 1 day · $150 · family (live: Montreal; local fallback: Calgary 1-day) | 1-day plan works                  |



| Trap        | Venue                                   | Expected behaviour                        |
| ----------- | --------------------------------------- | ----------------------------------------- |
| Allergen    | `r2` Peanut Palace (`peanut_risk=true`) | never in S1 plan                          |
| Hours       | `r4` Midnight Diner (closed Sat)        | never scheduled Saturday                  |
| Budget edge | expensive combos near limit             | `check_budget` → warning/exceeded + adapt |


```bash
FOODIE_DATA_BACKEND=local python eval/acceptance.py
```

---



## 8. Submission checklist

1. **Working demo** — at least Tier 0 answering S1–S3 live.
2. **UI** — Tier 0 chat counts; Tier 1/2: CLI or Streamlit with agent trace (+ map is a plus).
3. **10-minute presentation** — problem → live demo → how agentic patterns + APIs were used → Q&A.



### Scoring (100 pts — FINAL)


| Category                     | Weight |
| ---------------------------- | ------ |
| Dataset & API Mastery        | 40     |
| Solution Completeness        | 25     |
| User Experience & Interface  | 20     |
| Collaboration & Presentation | 15     |


---



## 9. Golden rules

1. Clarify scope early — get mentor feedback fast.
2. Time-box tasks — don't get stuck on minor issues.
3. Expect to build 30–50% of your ambition.
4. **MVP over perfect** — finish something functional.
5. Vibe coding is your friend.
6. Ground every pick in API or dataset facts — search is ON, hallucinations score zero.
7. Tell a story.

---



## 10. Troubleshooting


| Symptom                       | Fix                                                                                                |
| ----------------------------- | -------------------------------------------------------------------------------------------------- |
| `foodie.sqlite not found`     | `python data/seed.py`                                                                              |
| `FUELIX_API_KEY not set`      | Fill `.env` (kit auto-loads it via `config.load_dotenv`)                                           |
| `GOOGLE_MAPS_API_KEY not set` | Fill `.env` or set `FOODIE_DATA_BACKEND=local`                                                     |
| Places HTTP 403               | Key restrictions / API not enabled (Places **New**)                                                |
| Places HTTP 429               | Cache should help; stagger calls; check team quota                                                 |
| VPN blocks Google             | Use `FOODIE_DATA_BACKEND=local` for the demo insurance path                                        |
| `ModuleNotFoundError: src`    | Run from kit root (`python -m src.orchestrator`, `python app/cli.py`, `python eval/acceptance.py`) |
| Streamlit install blocked     | Use `app/cli.py` — fully accepted UI                                                               |
| Allergen trap not excluded    | Ensure local backend for graded S1; check `exclude_flags=["peanut_risk"]`                          |


---



## 11. Facilitator notes (short)

- Issue **per-team restricted** GCP keys at M0; enable billing alerts on the project.
- Keep a warmed API cache / local mode as demo-day insurance.
- Graded allergen checks should run on the **local** dataset (Places has no allergen fields).
- Freeze code early afternoon Day 2; protect the 10-minute presentation clock.

