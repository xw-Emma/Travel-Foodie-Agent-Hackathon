"""Thin FastAPI backend wrapping the Foodie orchestrator. Cloud Run entrypoint."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src import config, diagnostics
from src.agents import conversation, intent
from src.fuelix_client import FuelixClient
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


class IntentRequest(BaseModel):
    text: str = ""
    data_backend: str = "auto"


@app.post("/intent")
def read_intent(body: IntentRequest) -> dict[str, Any]:
    """Read a free-text description into a validated form draft.

    Server-side so the deployed UI gets the same feature without the Fuel iX key
    ever reaching a browser. Returns fields, plus everything it refused to use -
    it never returns a venue, and the caller still confirms before planning.
    """
    token = config.set_backend_override(body.data_backend)
    try:
        client = None if config.MOCK_MODE else FuelixClient(timeout=30,
                                                            max_retries=1)
        draft = intent.extract(client, body.text, body.data_backend)
    finally:
        config._backend_override.reset(token)
    # start_date is a date object; the wire needs a string.
    fields = dict(draft.get("fields") or {})
    if fields.get("start_date") is not None:
        fields["start_date"] = str(fields["start_date"])
    return {**draft, "fields": fields}


class ChatRequest(BaseModel):
    history: list[dict] = []
    feasibility: dict | None = None
    asked: list[str] = []
    data_backend: str = "auto"


@app.post("/chat")
def chat(body: ChatRequest) -> dict[str, Any]:
    """One conversational turn: read what was said, then ask what is missing.

    Server-side so the Fuel iX key never reaches a browser, same as /intent.
    Returns fields and questions - never an itinerary and never a venue.
    """
    token = config.set_backend_override(body.data_backend)
    try:
        client = None if config.MOCK_MODE else FuelixClient(timeout=30,
                                                            max_retries=1)
        turn = conversation.next_turn(client, body.history, body.feasibility,
                                      body.data_backend, set(body.asked))
    finally:
        config._backend_override.reset(token)
    fields = dict(turn.get("fields") or {})
    if fields.get("start_date") is not None:
        fields["start_date"] = str(fields["start_date"])
    return {**turn, "fields": fields}


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
