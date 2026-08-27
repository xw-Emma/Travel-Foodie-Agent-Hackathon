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

## Web UI entrypoints

The primary browser UI is `app/streamlit_app.py`. It runs the orchestrator
in-process and exposes Tier 1/Tier 2, backend selection, itinerary, routes,
map, trace, and debug state.

Start the primary UI with:

```powershell
streamlit run app/streamlit_app.py
```

The `frontend/` directory contains a separate thin HTTP client UI for the
FastAPI/Cloud Run deployment. It is not the primary local demo UI. For local
development and Tier 2 demonstrations, always use `app/streamlit_app.py`.

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
