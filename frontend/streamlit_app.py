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
from datetime import date, timedelta
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

def call_intent(text: str, backend: str) -> dict:
    """Read a description through the backend, so no key reaches the browser."""
    request = urllib.request.Request(
        BACKEND_URL + "/intent",
        data=json.dumps({"text": text, "data_backend": backend}).encode("utf-8"),
        method="POST",
        headers={**_auth_headers(), "User-Agent": "foodie-frontend/2.0"})
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.loads(response.read().decode("utf-8"))


def _city_options() -> list[str]:
    drafted = st.session_state.get("f_city")
    extra = [drafted] if drafted and drafted not in CITY_SUGGESTIONS else []
    return CITY_SUGGESTIONS + extra


def _cuisine_options(backend: str) -> list[str]:
    known = vocabulary.restaurant_types(backend)
    drafted = [c for c in (st.session_state.get("f_cuisines") or [])
               if c not in known]
    return known + drafted


def _apply_draft(draft: dict) -> None:
    """Write the draft into the widgets' state BEFORE they are built.

    Streamlit refuses a write to a widget key once that widget exists in the
    current run, so this is always followed by a rerun. Nothing is submitted -
    the user confirms the form themselves.
    """
    fields = draft.get("fields") or {}
    simple = {"city": "f_city", "meals": "f_meals", "cuisines": "f_cuisines",
              "allergies": "f_allergies", "party_size": "f_party",
              "budget_amount": "f_budget", "budget_basis": "f_basis",
              "transport_mode": "f_transport", "min_rating": "f_min_rating",
              "min_reviews": "f_min_reviews", "search_radius_km": "f_radius",
              "max_leg_minutes": "f_max_leg", "days": "f_days"}
    for field, key in simple.items():
        if field in fields:
            st.session_state[key] = fields[field]
    if fields.get("allergies"):
        st.session_state["f_no_allergies"] = False
    if "attractions_wanted" in fields:
        st.session_state["f_food_only"] = not fields["attractions_wanted"]
    if fields.get("start_date"):
        start = date.fromisoformat(str(fields["start_date"]))
        days = int(fields.get("days") or 1)
        st.session_state["f_dates"] = (start, start + timedelta(days=days - 1))
    st.session_state["intent_criteria"] = draft.get("other_criteria") or []


def render_intent_box(backend: str) -> None:
    """Same two-step flow as the in-process UI: read, confirm, then plan."""
    with st.expander("Describe your trip in your own words (optional)"):
        description = st.text_area(
            "Description", height=110, key="intent_text",
            label_visibility="collapsed",
            placeholder="e.g. Full day in Lisbon, lunch and dinner only, about "
                        "$100 per person, authentic Portuguese, rated 4.8+ with "
                        "1000+ reviews, no chains.")
        if st.button("Read this into the form", key="intent_go"):
            try:
                with st.spinner("Reading your description..."):
                    draft = call_intent(description, backend)
            except Exception as exc:  # noqa: BLE001
                st.error(f"Could not reach the backend: `{exc}`")
                return
            _apply_draft(draft)
            st.session_state["intent_draft"] = draft
            st.rerun()

        draft = st.session_state.get("intent_draft")
        if not draft:
            return
        fields = draft.get("fields") or {}
        if fields:
            st.success("Filled in: " + ", ".join(
                f"**{k.replace('_', ' ')}** = {v}" for k, v in fields.items()),
                icon=":material/edit_note:")
        for note in draft.get("notes") or []:
            st.info(note)
        if draft.get("rejected"):
            dropped = "\n".join(
                f"- `{r['field']}`"
                + (f" = {r['value']}" if r.get("value") else "")
                + f" — {r['reason']}"
                for r in draft["rejected"])
            st.warning(
                f"**Not used** — dropped rather than guessed at:\n\n{dropped}",
                icon=":material/filter_alt_off:")
        if draft.get("other_criteria"):
            carried = "\n".join(f"- {c}" for c in draft["other_criteria"])
            st.info(
                "**Carried over but not checkable** — these appear in the "
                f"verification panel as unverifiable:\n\n{carried}",
                icon=":material/help:")
        st.caption("Check the form below, change anything wrong, then press "
                   "Plan my trip. Nothing was searched yet.")


st.title("Travel Foodie Agent")
st.caption("Deployment UI — plans through the FastAPI backend")

render_intent_box(backend)

with st.form("trip_form"):
    left, right = st.columns(2)
    with left:
        city = st.selectbox("City", _city_options(), index=0,
                            accept_new_options=True, key="f_city")
        trip_dates = st.date_input("Trip dates", value=(), min_value=date.today(),
                                   key="f_dates")
        origin_text = st.text_input("Starting point (address, hotel, or landmark)",
                                    placeholder="e.g. 120 9 Ave SE, Calgary",
                                    key="f_origin")
        transport = st.radio("Getting around", vocabulary.TRANSPORT_MODES,
                             horizontal=True, key="f_transport")
        meals = st.multiselect("Meals to plan", vocabulary.MEAL_SLOTS,
                               default=list(vocabulary.MEAL_SLOTS), key="f_meals")
    with right:
        budget_amount = st.number_input("Budget (CAD)", min_value=1.0,
                                        value=500.0, step=25.0, key="f_budget")
        budget_basis = st.radio(
            "Budget is", ["total", "per_person"], horizontal=True,
            format_func=lambda value: "for the whole party" if value == "total"
            else "per person", key="f_basis")
        party_size = st.number_input("Party size", 1, 20, 2, key="f_party")
        cuisines = st.multiselect("Restaurant types", _cuisine_options(backend),
                                  default=["international"], key="f_cuisines")
        food_only = st.checkbox("Food only — no attractions", value=False,
                                key="f_food_only")
        attraction_types = st.multiselect("Attraction types",
                                          vocabulary.attraction_types(),
                                          disabled=food_only,
                                          key="f_attraction_types")
        no_allergies = st.checkbox("No allergies", value=True, key="f_no_allergies")
        allergies = st.multiselect("Allergies (hard exclusion)",
                                   vocabulary.CANONICAL_ALLERGENS,
                                   disabled=no_allergies, key="f_allergies")

    with st.expander("How far will you go?"):
        far_left, far_right = st.columns(2)
        with far_left:
            search_radius = st.slider("Search radius from the centre (km)",
                                      1.0, 25.0, 5.0, 0.5, key="f_radius")
            min_rating = st.slider(
                "Minimum Google rating", 0.0, 5.0, 0.0, 0.1, key="f_min_rating",
                help="0 = no minimum. Enforced in code against the rating "
                     "Google returns; a plan that cannot meet it says so "
                     "rather than quietly settling for less.")
            min_reviews = st.number_input(
                "Minimum review count", 0, 100000, 0, 50, key="f_min_reviews",
                help="0 = no minimum.")
            days_fallback = st.number_input(
                "Days (used when no dates are picked)", 1, 7, 2, key="f_days")
        with far_right:
            max_leg = st.slider("Max travel between stops (min)", 5, 90, 25, 5,
                                key="f_max_leg")
            max_daily = st.slider("Max total travel per day (min)", 30, 300, 120, 15,
                                  key="f_max_daily")

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
        # Requirements the description asked for that no field can hold, so the
        # verification panel lists them instead of letting them disappear.
        "extra_criteria": st.session_state.get("intent_criteria") or [],
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
