"""Primary in-process Streamlit UI for the Travel Foodie Agent.

Use this entrypoint for local development and Tier 1/Tier 2 demonstrations.
The separate frontend/streamlit_app.py is the thin HTTP deployment client; both
render through app/ui_components.py so they cannot drift apart.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import ui_components as ui  # noqa: E402
from src import config, diagnostics, vocabulary  # noqa: E402
from src.orchestrator import run_tier1, run_tier2  # noqa: E402
from src.request_model import Origin, TripRequest  # noqa: E402
from src.tools import resolve_origin  # noqa: E402

st.set_page_config(page_title="Travel Foodie Agent",
                   page_icon=":material/restaurant:", layout="wide")

CITY_SUGGESTIONS = ["Calgary", "Vancouver", "Montreal"]


@st.cache_data(show_spinner=False, ttl=60)
def _diagnostics(probe: bool) -> dict:
    return diagnostics.snapshot(probe_apis=probe)


def render_result(state: dict) -> None:
    meta = state.get("meta") or {}
    request = state.get("request") or {}
    budget = state.get("budget") or {}

    ui.render_banners(meta, request, budget)
    ui.render_budget(budget)

    st.subheader("Itinerary")
    ui.render_day_tabs(state.get("itinerary") or [], state.get("routes") or [],
                       day_labels=state.get("day_labels"),
                       max_daily_minutes=float(
                           request.get("max_daily_travel_minutes") or 120.0))

    st.subheader("Map")
    routes = state.get("routes") or []
    day_numbers = [route.get("day") for route in routes if route.get("day")]
    visible = day_numbers
    if len(day_numbers) > 1:
        visible = st.multiselect("Days to show", day_numbers, default=day_numbers,
                                 key="map_days")
    ui.render_map(state.get("itinerary") or [], routes, visible)

    with st.expander("Agent trace", expanded=True):
        ui.render_trace(state.get("trace") or [])

    with st.expander("Routes"):
        ui.render_routes_table(routes)

    with st.expander("Tool backends"):
        st.json(meta.get("tool_backends", {}))
        st.caption(f"Elapsed {meta.get('elapsed_s', 0)}s · "
                   f"LLM calls {meta.get('llm_calls', 0)} · "
                   f"tier {meta.get('tier', '?')}")

    with st.expander("Raw state / debug"):
        st.json(state)


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.header("Run settings")
    backend = st.selectbox("Data backend", ["auto", "live", "local"], index=0,
                           help="auto tries Google first and falls back offline.")
    tier = st.selectbox("Agent tier", [2, 1], index=0,
                        help="Tier 2 adds attractions, routing and the critic loop.")
    st.divider()
    st.subheader("Diagnostics")
    probe = st.checkbox("Probe the Google APIs", value=False,
                        help="Makes two real calls. Leave off to avoid billing.")
    ui.render_diagnostics(_diagnostics(probe))
    if st.button("Refresh diagnostics", width="stretch"):
        _diagnostics.clear()
        st.rerun()

st.title("Travel Foodie Agent")
st.caption("Plan, verify, and inspect a grounded itinerary")

# --------------------------------------------------------------------- form
# Everything lives in a form because Streamlit re-runs the whole script on every
# widget interaction. Without it, dragging a slider fires a fresh round of
# Places and Routes calls; the response cache absorbs repeats but not the first
# variation of each. Only the submit button triggers a run.
with st.form("trip_form"):
    left, right = st.columns(2)
    with left:
        city = st.selectbox("City", CITY_SUGGESTIONS, index=0, accept_new_options=True)
        trip_dates = st.date_input("Trip dates", value=(), min_value=date.today(),
                                   help="Sets the weekday, so opening hours can be checked.")
        origin_text = st.text_input(
            "Starting point (address, hotel, or landmark)",
            placeholder="e.g. 120 9 Ave SE, Calgary",
            help="Each day's route is planned from here.")
        transport = st.radio("Getting around", vocabulary.TRANSPORT_MODES,
                             horizontal=True)
    with right:
        budget_total = st.number_input("Total budget (CAD)", min_value=1.0,
                                       value=500.0, step=25.0)
        party_size = st.number_input("Party size", 1, 20, 2)
        cuisines = st.multiselect("Restaurant types", vocabulary.restaurant_types(),
                                  default=["international"])
        attraction_types = st.multiselect("Attraction types",
                                          vocabulary.attraction_types())
        allergies = st.multiselect("Allergies (hard exclusion)",
                                   vocabulary.CANONICAL_ALLERGENS)

    with st.expander("How far will you go?"):
        far_left, far_right = st.columns(2)
        with far_left:
            search_radius = st.slider("Search radius from the centre (km)",
                                      1.0, 25.0, 5.0, 0.5)
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

    with st.spinner("Planning, checking, and revising your itinerary..."):
        try:
            token = config.set_backend_override(backend)
            try:
                origin = Origin()
                if origin_text.strip():
                    resolved = resolve_origin(origin_text, city)
                    origin = Origin(address=origin_text, lat=resolved["lat"],
                                    lon=resolved["lon"],
                                    label=resolved["label"] or "Your location")
                    if not resolved.get("resolved"):
                        st.info(f"Couldn't pin **{origin_text}** exactly — using "
                                f"{resolved['label']} instead.")
                trip = TripRequest(
                    city=city, start_date=start_date, days=min(max(days, 1), 7),
                    origin=origin, budget_total=float(budget_total),
                    party_size=int(party_size),
                    cuisines=cuisines or ["international"],
                    attraction_types=attraction_types, allergies=allergies,
                    search_radius_km=float(search_radius),
                    max_leg_minutes=float(max_leg),
                    max_daily_travel_minutes=float(max_daily),
                    transport_mode=transport, tier=int(tier),
                    data_backend=backend)
                request = trip.to_request_dict()
                state = (run_tier2(request) if tier == 2 else run_tier1(request))
            finally:
                config._backend_override.reset(token)

            payload = state.to_json()
            payload["request"] = request
            payload["day_labels"] = trip.day_labels
            st.session_state.last_state = payload
        except Exception as exc:  # noqa: BLE001 - surface, never stack-trace
            message = str(exc)
            if "foodie.sqlite" in message:
                st.error("The offline dataset has not been built yet. Run "
                         "`python data/seed.py`, then plan again.")
            elif "GOOGLE_MAPS_API_KEY" in message:
                st.error("No Google Maps key is set. Put one in `.env`, or "
                         "switch the data backend to `local`.")
            elif "HTTP 403" in message:
                st.error("Google refused the request (403). Check that Places "
                         "API (New) and Routes API are enabled with billing on, "
                         "or switch to `local`.")
            elif "HTTP 429" in message:
                st.error("Google quota exceeded (429). Switch to `local` for "
                         "the demo; cached responses still work.")
            elif "No unused candidates" in message or "No verified candidate" in message:
                st.error("No venues match these filters. Try widening the search "
                         "radius, removing a cuisine, or raising the budget.")
            else:
                st.error(f"Planning failed: `{message}`")

if "last_state" in st.session_state:
    render_result(st.session_state.last_state)
else:
    st.info("Set your trip up above and press **Plan my trip**.")
