"""Primary in-process Streamlit UI for the Travel Foodie Agent.

Use this entrypoint for local development and Tier 1/Tier 2 demonstrations.
The separate frontend/streamlit_app.py is the thin HTTP deployment client.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import config  # noqa: E402
from src.orchestrator import run_tier1, run_tier2  # noqa: E402


st.set_page_config(page_title="Travel Foodie Agent", page_icon=":material/restaurant:", layout="wide")


def parse_prompt(text: str) -> dict:
    lower = text.lower()
    day_match = re.search(r"(\d+)\s*day", lower)
    budget_match = re.search(r"\$?\s*(\d{2,5})", lower)
    party_match = re.search(r"(\d+)\s*(?:people|person|guests?)", lower)
    city = next((name for name in ("Calgary", "Vancouver", "Montreal") if name.lower() in lower), "Calgary")
    cuisine = "asian" if "asian" in lower else "international"
    allergies = ["peanut"] if "peanut" in lower else []
    return {
        "city": city,
        "days": min(int(day_match.group(1)), 7) if day_match else 2,
        "budget_total": float(budget_match.group(1)) if budget_match else 500.0,
        "party_size": int(party_match.group(1)) if party_match else 2,
        "cuisines": [cuisine],
        "allergies": allergies,
    }


def render_itinerary(itinerary: list[dict]) -> None:
    meals = [item for item in itinerary if not item.get("slot", "").endswith("attraction1")]
    attractions = [item for item in itinerary if ".attraction" in item.get("slot", "")]
    st.subheader("Itinerary")
    if meals:
        meal_rows = [{
            "Slot": item.get("slot", ""),
            "Venue": item.get("name", ""),
            "Cost": f"${item.get('cost', 0):.2f}",
            "Source": item.get("source", ""),
        } for item in meals]
        st.dataframe(pd.DataFrame(meal_rows), width="stretch", hide_index=True)
    if attractions:
        st.subheader("Attractions")
        attraction_rows = [{
            "Slot": item.get("slot", ""),
            "Attraction": item.get("name", ""),
            "Cost": f"${item.get('cost', 0):.2f}",
            "Source": item.get("source", ""),
        } for item in attractions]
        st.dataframe(pd.DataFrame(attraction_rows), width="stretch", hide_index=True)


def render_map(itinerary: list[dict]) -> None:
    points = [{"lat": item["lat"], "lon": item["lon"], "name": item["name"]}
              for item in itinerary if item.get("lat") is not None and item.get("lon") is not None]
    st.subheader("Map")
    if points:
        st.map(pd.DataFrame(points), latitude="lat", longitude="lon", size=90, zoom=12)
        st.caption(f"{len(points)} verified stops plotted")
    else:
        st.info("No geocoded stops are available for this plan.")


def render_result(state) -> None:
    budget = state.budget or {}
    meta = state.meta or {}
    st.session_state.last_state = state.to_json()
    budget_columns = st.columns(4)
    budget_columns[0].metric("Projected", f"${budget.get('projected', 0):,.2f}")
    budget_columns[1].metric("Budget", f"${budget.get('limit', 0):,.2f}")
    budget_columns[2].metric("Remaining", f"${budget.get('remaining', 0):,.2f}")
    budget_columns[3].metric("Status", str(budget.get("status", "unknown")).title())

    render_itinerary(state.itinerary)
    render_map(state.itinerary)

    with st.expander("Agent trace", expanded=True):
        for entry in state.trace:
            st.write(f"**{entry.get('agent', 'agent')}**: {entry.get('message', '')}")

    with st.expander("Tool backends"):
        st.json(meta.get("tool_backends", {}))
        st.caption(f"Elapsed {meta.get('elapsed_s', 0)}s | LLM calls {meta.get('llm_calls', 0)}")

    with st.expander("Routes"):
        st.dataframe(pd.DataFrame(state.routes), width="stretch", hide_index=True)

    with st.expander("Raw state / debug"):
        st.json(state.to_json())


st.title("Travel Foodie Agent")
st.caption("Plan, verify, and inspect a grounded itinerary")

with st.sidebar:
    st.header("Plan settings")
    backend = st.selectbox("Data backend", ["local", "auto", "live"], index=0)
    tier = st.selectbox("Agent tier", [2, 1], index=0)
    city = st.text_input("City", "Calgary")
    days = st.number_input("Days", min_value=1, max_value=7, value=2)
    budget = st.number_input("Total budget", min_value=1.0, value=500.0, step=25.0)
    party_size = st.number_input("Party size", min_value=1, max_value=20, value=2)
    cuisine = st.selectbox("Cuisine", ["international", "asian"])
    allergy = st.selectbox("Allergy", ["None", "peanut", "shellfish", "gluten"])
    max_walk = st.number_input("Max walk (km)", min_value=0.1, value=2.0, step=0.5)
    st.caption(f"Configured backend: {config.DATA_BACKEND}")

if "messages" not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

prompt = st.chat_input("Try: 2 days in Calgary, $500, international, peanut allergy")
if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    parsed = parse_prompt(prompt)
    request = {
        "city": city or parsed["city"],
        "days": int(days) if days else parsed["days"],
        "budget_total": float(budget) if budget else parsed["budget_total"],
        "party_size": int(party_size) if party_size else parsed["party_size"],
        "cuisines": [cuisine or parsed["cuisines"][0]],
        "allergies": [] if allergy == "None" else [allergy],
        "max_walk_km": float(max_walk),
    }
    with st.chat_message("assistant", avatar=":material/restaurant:"):
        with st.spinner("Building and checking your itinerary..."):
            try:
                validated = TripRequest(**request, tier=tier, data_backend=backend)
                token = config.set_backend_override(backend)
                try:
                    state = run_tier2(validated.to_request_dict()) if tier == 2 else run_tier1(validated.to_request_dict())
                finally:
                    config._backend_override.reset(token)
                st.session_state.last_request = request
                st.session_state.last_state = state.to_json()
                st.markdown(f"Plan ready for **{request['city']}** with **{len(state.itinerary)}** verified stops.")
                render_result(state)
                assistant_text = f"Plan ready: {len(state.itinerary)} verified stops, budget status `{state.budget.get('status')}`."
            except Exception as exc:
                assistant_text = f"Backend error: `{exc}`"
                st.error(assistant_text)
    st.session_state.messages.append({"role": "assistant", "content": assistant_text})

if "last_state" in st.session_state and not prompt:
    st.divider()
    render_result(type("StateView", (), {
        "budget": st.session_state.last_state.get("budget", {}),
        "meta": st.session_state.last_state.get("meta", {}),
        "itinerary": st.session_state.last_state.get("itinerary", []),
        "routes": st.session_state.last_state.get("routes", []),
        "trace": st.session_state.last_state.get("trace", []),
        "to_json": lambda self: st.session_state.last_state,
    })())
