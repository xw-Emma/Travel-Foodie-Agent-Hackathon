# Travel-Foodie-Agent-Hackathon-Starter

**FINAL · Live-API edition (GCP)** — standalone hackathon kit for the
*Traveling Foodie Agent*. Built on the **TELUS AI environment** (Fuel iX +
VS Code / AI coding assistant) with **live Google Places (New) + Routes APIs**
on the hackathon GCP project, and a pre-staged Calgary dataset as mandatory
offline fallback.

> No SAM · no CEVA · no internal RF systems. Public data only.

## 30-second start (offline — no API keys)

```bash
cd Travel-Foodie-Agent-Hackathon-Starter
python data/seed.py
python -m src.orchestrator
```

You should see: allergen leaks `[]`, budget `ok`, Critic slot-guard accepting a
valid slot and rejecting a malformed one.

## When something is wrong, run this first

```powershell
python scripts\preflight.py
```

It names *which* of proxy / Fuel iX / Places / Routes is broken instead of
leaving you to guess. The same picture is in the UI sidebar and at
`GET /diagnostics`.

## Web UI entrypoints

The primary browser UI is `app/streamlit_app.py` — **not** `frontend/`. It runs
the orchestrator in-process and exposes Tier 1/Tier 2, backend selection, a
day-by-day itinerary with travel times and `source` badges, a pydeck map with
one route line per day, the agent trace, and a diagnostics panel.

```powershell
python data\seed.py
streamlit run app\streamlit_app.py
```

`frontend/streamlit_app.py` is the thin HTTP client for the FastAPI/Cloud Run
deployment. It shows the same screens — both import `app/ui_components.py`, so
they cannot drift — but plans through the backend, keeping the API keys
server-side. Use `app/streamlit_app.py` for local development and demos.

Every itinerary row carries a `source` badge (`google_places` vs
`local_dataset`), which is the quickest way to see which data path actually ran.

## Demo-day fallback ladder

Three rungs, each one rehearsable. Do this before demo day, on the machine you
will demo from — the cache is gitignored and does not travel.

```powershell
python scripts\warm_cache.py --golden      # warm the cache + freeze a plan
```

| Rung | Command | Survives |
|---|---|---|
| Live | `FOODIE_DATA_BACKEND=live` | normal conditions |
| **Auto** | `FOODIE_DATA_BACKEND=auto` | a dead network: Google falls back to the offline dataset, and an unreachable Fuel iX falls back to the deterministic pipeline |
| Local | `FOODIE_DATA_BACKEND=local` | anything — no network at all, and no LLM call is made |
| Demo | `FOODIE_DEMO_MODE=on` | replays `data/golden_plan.json`, loudly labelled in the UI |

`live` deliberately fails loudly on an uncached call — that is what it is for.
For resilience on stage use `auto` or `local`.

Note the cache only covers **Google** responses; Fuel iX calls are not cached.
That is why `auto` falls back to the deterministic planner rather than relying
on a warm cache for the LLM.

## Three tiers

| Tier | What | How |
|---|---|---|
| **0 Copilot** (floor) | Fuel iX Copilot answers S1–S3 from the city KB | Zero code · paste `prompts/tier0_copilot.md` · upload CSVs · internet may be ON but grounded answers only |
| **1 Scripted agent** | Planner → Restaurant → Budget → Formatter | ~150 lines · Places API tool with local fallback · AI assistant fills TODOs |
| **2 Multi-agent** | Parallel executors + Attraction/Route + Critic loop | Extends Tier 1 · Routes API · max 2 revision iterations |

## Layout

```text
Travel-Foodie-Agent-Hackathon-Starter/
├── README.md                 # this file
├── .env.example              # copy to .env (gitignored)
├── .gitignore
├── requirements.txt          # optional extras only (core = stdlib)
├── data/
│   ├── seed.py               # builds foodie.sqlite + refreshes CSVs
│   └── csv/                  # Tier 0 knowledge-base uploads
├── prompts/                  # Tier 0 + agent system prompts
├── src/
│   ├── config.py             # env-driven modes (live|local|auto)
│   ├── fuelix_client.py      # stdlib Fuel iX client + tool loop
│   ├── state.py              # TripState + SLOT_IDS + TOOL_SCHEMAS
│   ├── orchestrator.py       # run_tier1 / run_tier2
│   ├── agents/               # Critic helpers (extend here)
│   └── tools/                # facade + places_live + routes_live + local
├── app/streamlit_app.py      # primary browser UI (Tier 1/Tier 2)
├── app/cli.py                # simple CLI UI fallback
├── frontend/                 # thin HTTP client UI for deployed backend
├── eval/                     # scenarios + acceptance checks
└── scripts/smoke_test.py     # M0 gate script
```

## M0 gate (before Day 1)

1. Copy `.env.example` → `.env` and fill `FUELIX_API_KEY` + `GOOGLE_MAPS_API_KEY`.
2. Confirm enabled Fuel iX model IDs (default `claude-sonnet-4`).
3. Confirm coding assistant path with Teresa (Cline vs Claude Code).
4. `python data/seed.py && python scripts/smoke_test.py`
5. One live Places call must succeed (or declare `FOODIE_DATA_BACKEND=local` for offline practice).

## Rules

- All LLM traffic through **Fuel iX** only.
- API keys only in `.env` — never in code, notebooks, or slides.
- Hallucinated venues score zero. Ground every pick in API or KB data.
- Budget math stays pure Python (no LLM).
