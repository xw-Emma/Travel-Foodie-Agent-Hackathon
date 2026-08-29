"""Thin deployment UI that calls the FastAPI backend over HTTP.

Same form and same rendering as app/streamlit_app.py - both import
app/ui_components.py - but the planning happens in the backend, so the Fuel iX
and Google keys stay server-side and never reach the browser.

Use app/streamlit_app.py for local development; this is the Cloud Run client.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import ui_components as ui  # noqa: E402
from src import verification, vocabulary  # noqa: E402

BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:8080").rstrip("/")
# For --no-allow-unauthenticated backends, set USE_ID_TOKEN=1 and run where gcloud works,
# or switch backend to allow unauthenticated for the hackathon demo only.
USE_ID_TOKEN = os.environ.get("USE_ID_TOKEN", "0") == "1"

CITY_SUGGESTIONS = ["Calgary", "Vancouver", "Montreal"]


def _auth_headers() -> dict:
    headers = {"Content-Type": "application/json"}
    if USE_ID_TOKEN:
        import subprocess
        token = subprocess.check_output(
            ["gcloud", "auth", "print-identity-token"], text=True).strip()
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _get(path: str, timeout: int = 20) -> dict:
    request = urllib.request.Request(
        BACKEND_URL + path, method="GET",
        headers={**_auth_headers(), "User-Agent": "foodie-frontend/2.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def call_plan(payload: dict) -> dict:
    """POST the same TripRequest shape the in-process UI builds."""
    request = urllib.request.Request(
        BACKEND_URL + "/plan", data=json.dumps(payload, default=str).encode("utf-8"),
        method="POST", headers={**_auth_headers(), "User-Agent": "foodie-frontend/2.0"})
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:400]
        raise RuntimeError(f"Backend HTTP {exc.code}: {detail}") from exc


st.set_page_config(page_title="Travel Foodie Agent",
                   page_icon=":material/restaurant:", layout="wide")


@st.cache_data(show_spinner=False, ttl=60)
def _diagnostics(backend: str) -> dict:
    """Ask the backend what IT would do with the backend this UI will send."""
    return _get(f"/diagnostics?backend={backend}")


with st.sidebar:
    st.header("Run settings")
    backend = st.selectbox("Data backend", ["auto", "live", "local"], index=0)
    tier = st.selectbox("Agent tier", [2, 1], index=0)
    st.caption(f"Backend: {BACKEND_URL}")
    st.divider()
    st.subheader("Diagnostics")
    try:
        ui.render_diagnostics(_diagnostics(backend))
    except Exception as exc:  # noqa: BLE001
        st.error(f"Cannot reach the backend: `{exc}`")
    if st.button("Refresh diagnostics", width="stretch"):
        _diagnostics.clear()
        st.rerun()

st.title("Travel Foodie Agent")
st.caption("Deployment UI — plans through the FastAPI backend")

with st.form("trip_form"):
    left, right = st.columns(2)
    with left:
        city = st.selectbox("City", CITY_SUGGESTIONS, index=0, accept_new_options=True)
        trip_dates = st.date_input("Trip dates", value=(), min_value=date.today())
        origin_text = st.text_input("Starting point (address, hotel, or landmark)",
                                    placeholder="e.g. 120 9 Ave SE, Calgary")
        transport = st.radio("Getting around", vocabulary.TRANSPORT_MODES,
                             horizontal=True)
        meals = st.multiselect("Meals to plan", vocabulary.MEAL_SLOTS,
                               default=list(vocabulary.MEAL_SLOTS))
    with right:
        budget_amount = st.number_input("Budget (CAD)", min_value=1.0,
                                        value=500.0, step=25.0)
        budget_basis = st.radio(
            "Budget is", ["total", "per_person"], horizontal=True,
            format_func=lambda value: "for the whole party" if value == "total"
            else "per person")
        party_size = st.number_input("Party size", 1, 20, 2)
        cuisines = st.multiselect("Restaurant types",
                                  vocabulary.restaurant_types(backend),
                                  default=["international"])
        food_only = st.checkbox("Food only — no attractions", value=False)
        attraction_types = st.multiselect("Attraction types",
                                          vocabulary.attraction_types(),
                                          disabled=food_only)
        no_allergies = st.checkbox("No allergies", value=True)
        allergies = st.multiselect("Allergies (hard exclusion)",
                                   vocabulary.CANONICAL_ALLERGENS,
                                   disabled=no_allergies)

    with st.expander("How far will you go?"):
        far_left, far_right = st.columns(2)
        with far_left:
            search_radius = st.slider("Search radius from the centre (km)",
                                      1.0, 25.0, 5.0, 0.5)
            min_rating = st.slider(
                "Minimum Google rating", 0.0, 5.0, 0.0, 0.1,
                help="0 = no minimum. Enforced in code against the rating "
                     "Google returns; a plan that cannot meet it says so "
                     "rather than quietly settling for less.")
            min_reviews = st.number_input(
                "Minimum review count", 0, 100000, 0, 50,
                help="0 = no minimum.")
            days_fallback = st.number_input(
                "Days (used when no dates are picked)", 1, 7, 2)
        with far_right:
            max_leg = st.slider("Max travel between stops (min)", 5, 90, 25, 5)
            max_daily = st.slider("Max total travel per day (min)", 30, 300, 120, 15)

    submitted = st.form_submit_button("Plan my trip", type="primary")

if not vocabulary.covers_city(city):
    ui.dataset_city_warning(city, backend, vocabulary.dataset_cities())

if submitted:
    start_date = trip_dates[0] if isinstance(trip_dates, (list, tuple)) and trip_dates else None
    if isinstance(trip_dates, (list, tuple)) and len(trip_dates) == 2:
        days = (trip_dates[1] - trip_dates[0]).days + 1
    else:
        days = int(days_fallback)

    payload = {
        "city": city,
        "start_date": start_date.isoformat() if start_date else None,
        "days": min(max(days, 1), 7),
        "origin": {"address": origin_text or None, "label": origin_text or "Your location"},
        "budget_total": float(budget_amount),
        "budget_basis": budget_basis,
        "party_size": int(party_size),
        "meals": meals or list(vocabulary.MEAL_SLOTS),
        "cuisines": cuisines or ["international"],
        "attraction_types": [] if food_only else attraction_types,
        "attractions_per_day": 0 if food_only else 1,
        "allergies": [] if no_allergies else allergies,
        "search_radius_km": float(search_radius),
        "min_rating": min_rating or None,
        "min_reviews": int(min_reviews) or None,
        "max_leg_minutes": float(max_leg),
        "max_daily_travel_minutes": float(max_daily),
        "transport_mode": transport,
        # Driven by the sidebar, never hardcoded: a fixed tier=1 here made
        # Tier 2 unreachable from the deployed UI, and forcing data_backend to
        # local whenever an allergy was mentioned silently disabled every
        # Google call.
        "tier": int(tier),
        "data_backend": backend,
    }

    with st.spinner("Planning through the backend..."):
        try:
            result = call_plan(payload)
            st.session_state.last_state = {
                "itinerary": result.get("itinerary") or [],
                "routes": result.get("routes") or [],
                "budget": result.get("budget") or {},
                "trace": result.get("trace") or [],
                "meta": result.get("meta") or {},
                "request": result.get("request") or payload,
                "day_labels": result.get("day_labels") or [],
            }
        except Exception as exc:  # noqa: BLE001
            st.error(f"Planning failed: `{exc}`")

if "last_state" in st.session_state:
    state = st.session_state.last_state
    meta = state["meta"]
    ui.render_banners(meta, state["request"], state["budget"])
    ui.render_budget(state["budget"], state["request"])
    # verification.verify is a pure function over plain dicts, so the deployed
    # UI reaches the same verdicts from the JSON response - no extra endpoint.
    st.subheader("Verification")
    ui.render_verification_panel(verification.verify(state["request"], state))
    if meta.get("day_summary"):
        st.subheader("At a glance")
        ui.render_day_summary(meta["day_summary"])
    st.subheader("Itinerary")
    ui.render_day_tabs(state["itinerary"], state["routes"],
                       day_labels=state.get("day_labels"),
                       max_daily_minutes=float(
                           state["request"].get("max_daily_travel_minutes") or 120.0),
                       enrichment=meta.get("enrichment"),
                       backups=meta.get("backups"))
    st.subheader("Map")
    ui.render_map(state["itinerary"], state["routes"])
    with st.expander("Agent trace", expanded=True):
        ui.render_trace(state["trace"])
    with st.expander("Routes"):
        ui.render_routes_table(state["routes"])
    with st.expander("Tool backends"):
        st.json(meta.get("tool_backends", {}))
else:
    st.info("Set your trip up above and press **Plan my trip**.")
