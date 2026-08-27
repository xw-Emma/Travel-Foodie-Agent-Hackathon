"""Thin FastAPI backend wrapping the Foodie orchestrator. Cloud Run entrypoint."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from src import config
from src.orchestrator import run_tier1, run_tier2
from src.request_model import TripRequest

app = FastAPI(title="Travel Foodie Agent API", version="1.0.0")


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "db_exists": config.DB_PATH.exists(),
        "fuelix_key_set": bool(config.FUELIX_API_KEY),
        "maps_key_set": bool(config.GOOGLE_MAPS_API_KEY),
        "mock_llm": config.MOCK_MODE,
        "data_backend": config.DATA_BACKEND,
        "default_model": config.DEFAULT_MODEL,
    }


@app.post("/plan")
def plan(body: TripRequest) -> dict[str, Any]:
    token = config.set_backend_override(body.data_backend)
    try:
        request = body.to_request_dict()
        st = run_tier2(request) if body.tier == 2 else run_tier1(request)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        config._backend_override.reset(token)

    return {
        "request": body.model_dump(mode="json"),
        "itinerary": st.itinerary,
        "budget": st.budget,
        "trace": st.trace,
        "meta": st.meta,
        "tool_backends": (st.meta or {}).get("tool_backends"),
        "candidates_summary": {
            k: [c.get("name") for c in v] for k, v in (st.candidates or {}).items()
        },
    }
