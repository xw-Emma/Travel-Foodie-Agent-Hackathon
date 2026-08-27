"""Thin FastAPI backend wrapping the Foodie orchestrator. Cloud Run entrypoint."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from src import config, diagnostics
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


@app.get("/diagnostics")
def diagnose(probe: bool = False, backend: str | None = None) -> dict[str, Any]:
    """Which backend will run, and whether the Google APIs actually answer.

    `probe=true` makes two real (billed) calls, so it is opt-in. `backend` asks
    what a run WOULD do with that setting, so a UI can report the backend its
    own selector will send rather than the server's default.
    """
    token = config.set_backend_override(backend) if backend else None
    try:
        return diagnostics.snapshot(probe_apis=probe)
    finally:
        if token is not None:
            config._backend_override.reset(token)


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
        "request": request,
        "day_labels": body.day_labels,
        "itinerary": st.itinerary,
        # Routes carry the per-leg geometry the map draws, so an HTTP client can
        # render exactly what the in-process UI renders.
        "routes": st.routes,
        "budget": st.budget,
        "critic": st.critic,
        "trace": st.trace,
        "meta": st.meta,
        "tool_backends": (st.meta or {}).get("tool_backends"),
        "candidates_summary": {
            k: [c.get("name") for c in v] for k, v in (st.candidates or {}).items()
        },
    }
