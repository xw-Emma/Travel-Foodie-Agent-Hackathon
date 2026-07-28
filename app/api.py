"""Thin FastAPI backend wrapping the Foodie orchestrator. Cloud Run entrypoint."""
from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src import config
from src.orchestrator import run_tier1, run_tier2

app = FastAPI(title="Travel Foodie Agent API", version="1.0.0")


class PlanRequest(BaseModel):
    city: str = "Calgary"
    days: int = Field(default=2, ge=1, le=7)
    budget_total: float = Field(default=500, gt=0)
    party_size: int = Field(default=2, ge=1)
    cuisines: list[str] = Field(default_factory=lambda: ["international"])
    allergies: list[str] = Field(default_factory=list)
    tier: int = Field(default=1, ge=1, le=2)
    # Force local dataset for graded allergen scenarios (Places has no allergen fields)
    data_backend: str | None = Field(default=None, description="auto|live|local")


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
def plan(body: PlanRequest) -> dict[str, Any]:
    if body.data_backend:
        os.environ["FOODIE_DATA_BACKEND"] = body.data_backend
        config.DATA_BACKEND = body.data_backend.lower()

    request = body.model_dump(exclude={"tier", "data_backend"})
    try:
        st = run_tier2(request) if body.tier == 2 else run_tier1(request)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "itinerary": st.itinerary,
        "budget": st.budget,
        "trace": st.trace,
        "meta": st.meta,
        "tool_backends": (st.meta or {}).get("tool_backends"),
        "candidates_summary": {
            k: [c.get("name") for c in v] for k, v in (st.candidates or {}).items()
        },
    }
