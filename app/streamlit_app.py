"""Primary in-process Streamlit UI for the Travel Foodie Agent.

Use this entrypoint for local development and Tier 1/Tier 2 demonstrations.
The separate frontend/streamlit_app.py is the thin HTTP deployment client; both
render through app/ui_components.py so they cannot drift apart.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import ui_components as ui  # noqa: E402
from src import config, diagnostics, verification, vocabulary  # noqa: E402
from src.agents import conversation  # noqa: E402
from src.fuelix_client import FuelixClient  # noqa: E402
from src.orchestrator import run_tier1, run_tier2  # noqa: E402
from src.request_model import Origin, TripRequest  # noqa: E402
from src.tools import classify_city, resolve_origin  # noqa: E402

st.set_page_config(page_title="Travel Foodie Agent",
                   page_icon=":material/restaurant:", layout="wide")

CITY_SUGGESTIONS = ["Calgary", "Vancouver", "Montreal"]


@st.cache_data(show_spinner=False, ttl=60)
def _diagnostics(probe: bool, backend: str) -> dict:
    # Report what the NEXT run will do, not the process default. Without the
    # override the panel read "forced_local" from .env while the selector said
    # "auto" and the run went live - the one thing this panel exists to prevent.
    token = config.set_backend_override(backend)
    try:
        return diagnostics.snapshot(probe_apis=probe)
    finally:
        config._backend_override.reset(token)


def render_result(state: dict) -> None:
    meta = state.get("meta") or {}
    request = state.get("request") or {}
    budget = state.get("budget") or {}

    ui.render_banners(meta, request, budget)
    ui.render_budget(budget, request)

    # Verification first: what was asked for, and how each answer was reached,
    # before the plan that claims to satisfy it.
    st.subheader("Verification")
    ui.render_verification_panel(verification.verify(request, state))

    if meta.get("day_summary"):
        st.subheader("At a glance")
        ui.render_day_summary(meta["day_summary"])

    st.subheader("Itinerary")
    ui.render_day_tabs(state.get("itinerary") or [], state.get("routes") or [],
                       day_labels=state.get("day_labels"),
                       max_daily_minutes=float(
                           request.get("max_daily_travel_minutes") or 120.0),
                       enrichment=meta.get("enrichment"),
                       backups=meta.get("backups"))

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



def _city_options() -> list[str]:
    """Suggestions plus whatever the description proposed, so a drafted city is
    selectable rather than rejected as an unknown option."""
    drafted = st.session_state.get("f_city")
    extra = [drafted] if drafted and drafted not in CITY_SUGGESTIONS else []
    return CITY_SUGGESTIONS + extra


def _cuisine_options(backend: str) -> list[str]:
    known = vocabulary.restaurant_types(backend)
    drafted = [c for c in (st.session_state.get("f_cuisines") or [])
               if c not in known]
    return known + drafted


def _apply_draft(draft: dict) -> None:
    """Write the drafted values into the widgets' session state.

    Streamlit refuses a write to a widget's key once that widget exists in the
    current run, so this only ever runs BEFORE the form is built and is followed
    by a rerun. Nothing is submitted: the user reads what was understood, edits
    anything wrong, and presses Plan themselves.
    """
    fields = draft.get("fields") or {}
    simple = {"city": "f_city", "meals": "f_meals", "cuisines": "f_cuisines",
              "allergies": "f_allergies", "party_size": "f_party",
              "budget_amount": "f_budget", "budget_basis": "f_basis",
              "transport_mode": "f_transport", "min_rating": "f_min_rating",
              "min_reviews": "f_min_reviews", "search_radius_km": "f_radius",
              "max_leg_minutes": "f_max_leg", "days": "f_days",
              # Phase J: the three that used to be understood and then dropped
              # on the floor. attraction_types is the CN Tower fix - saying
              # "I like museums" has to reach the search that runs.
              "attraction_types": "f_attraction_types",
              "attractions_per_day": "f_attractions_per_day",
              "family_friendly": "f_family", "return_to_origin": "f_return",
              # The starting point the conversation asked for has to land in
              # the field the Route Agent reads, or answering the question
              # changes nothing.
              "origin_text": "f_origin"}
    for field, key in simple.items():
        if field in fields:
            st.session_state[key] = fields[field]
    if fields.get("allergies"):
        st.session_state["f_no_allergies"] = False
    # The count is authoritative; the old bool only decides the Food only gate
    # when no count came through. intent.validate keeps the two consistent.
    if "attractions_wanted" in fields:
        st.session_state["f_food_only"] = not fields["attractions_wanted"]
    if fields.get("start_date"):
        days = int(fields.get("days") or 1)
        start = fields["start_date"]
        st.session_state["f_dates"] = (start, start + timedelta(days=days - 1))
    st.session_state["intent_criteria"] = draft.get("other_criteria") or []


def _draft_summary(draft: dict) -> None:
    """What was understood, and what was thrown away. Shown every turn."""
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


def render_chat(backend: str) -> None:
    """A conversation that fills the form. It never plans and never picks a venue.

    Two decisions kept out of the model, deliberately: what is still missing
    (a constraint check, so it lives in code) and whether a value is usable
    (intent.validate, same path as Phase B). The model only reads sentences.

    Nothing is searched here. Every turn updates the form below; planning stays
    behind the button, which is the user's decision.
    """
    st.subheader("Tell me about your trip")
    history = st.session_state.setdefault("chat_history", [])

    for turn in history:
        with st.chat_message(turn["role"]):
            st.markdown(turn["content"])

    message = st.chat_input(
        "e.g. Full day in Lisbon, lunch and dinner only, about $100 per person, "
        "authentic Portuguese, rated 4.8+ with 1000+ reviews, no chains.",
        key="chat_in")
    if message:
        history.append({"role": "user", "content": message})
        client = None if config.MOCK_MODE else FuelixClient(timeout=30,
                                                            max_retries=1)
        # Feasibility comes from the LAST plan, so asking about an impossible
        # budget costs no extra search - it is something already measured.
        last = st.session_state.get("last_state") or {}
        feasibility = (last.get("meta") or {}).get("feasibility")
        with st.spinner("Reading what you said..."):
            turn = conversation.next_turn(
                client, history, feasibility, backend,
                asked=set(st.session_state.get("chat_asked") or ()))

        _apply_draft(turn)
        st.session_state["intent_draft"] = turn
        questions = turn.get("questions") or []
        if questions:
            reply = "\n\n".join(q["text"] for q in questions)
            st.session_state["chat_asked"] = sorted(
                set(st.session_state.get("chat_asked") or ()) |
                {q["id"] for q in questions})
        elif turn.get("fields"):
            reply = ("That is everything I need. Check the details below and "
                     "press **Plan my trip** when you are happy — nothing has "
                     "been searched yet.")
        else:
            reply = ("I could not read anything usable from that. Try naming "
                     "the city, how long, and roughly what you want to spend.")
        history.append({"role": "assistant", "content": reply})
        # Rerun so the form widgets are rebuilt from what was just written.
        st.rerun()

    draft = st.session_state.get("intent_draft")
    if draft:
        _draft_summary(draft)
    if history and st.button("Start over", key="chat_reset"):
        for key in ("chat_history", "chat_asked", "intent_draft",
                    "intent_criteria"):
            st.session_state.pop(key, None)
        st.rerun()


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
    ui.render_diagnostics(_diagnostics(probe, backend))
    if st.button("Refresh diagnostics", width="stretch"):
        _diagnostics.clear()
        st.rerun()

st.title("Travel Foodie Agent")
st.caption("Plan, verify, and inspect a grounded itinerary")

render_chat(backend)

# --------------------------------------------------------------------- form
# Everything lives in a form because Streamlit re-runs the whole script on every
# widget interaction. Without it, dragging a slider fires a fresh round of
# Places and Routes calls; the response cache absorbs repeats but not the first
# variation of each. Only the submit button triggers a run.
with st.expander("Fine-tune the details", expanded=True):
  # These two gate fields INSIDE the form, so they have to live outside it.
  # A form does not rerun on interaction, which means a `disabled=` depending on
  # a checkbox in the same form keeps the previous render's value until submit -
  # the control looks inert. Out here, ticking one reruns immediately.
  gate_left, gate_right = st.columns(2)
  with gate_left:
      food_only = st.checkbox(
          "Food only — no attractions", value=False, key="f_food_only",
          help="Plans meals and the routes between them, nothing else.")
  with gate_right:
      # A checkbox rather than a "none" entry in the list: "none" alongside
      # "peanut" would be a state with no sensible meaning.
      no_allergies = st.checkbox("No allergies", value=True,
                                 key="f_no_allergies")
      family_friendly = st.checkbox(
          "Travelling with kids", value=False, key="f_family",
          help="Drops places Google marks as not good for children. A place "
               "with no answer is kept - unknown is not a reason to exclude, "
               "and not a promise either.")
  with st.form("trip_form"):
      left, right = st.columns(2)
      with left:
          city = st.selectbox("City", _city_options(), index=0,
                              accept_new_options=True, key="f_city")
          trip_dates = st.date_input("Trip dates", value=(), min_value=date.today(),
                                     key="f_dates",
                                     help="Sets the weekday, so opening hours can be checked.")
          origin_text = st.text_input(
              "Starting point (address, hotel, or landmark)",
              placeholder="e.g. 120 9 Ave SE, Calgary", key="f_origin",
              help="Each day's route is planned from here.")
          transport = st.radio("Getting around", vocabulary.TRANSPORT_MODES,
                               horizontal=True, key="f_transport")
          meals = st.multiselect(
              "Meals to plan", vocabulary.MEAL_SLOTS,
              default=list(vocabulary.MEAL_SLOTS), key="f_meals",
              help="Deselect what you are not eating out. The budget is split "
                   "across the meals you keep.")
      with right:
          budget_amount = st.number_input("Budget (CAD)", min_value=1.0,
                                          value=500.0, step=25.0, key="f_budget")
          budget_basis = st.radio(
              "Budget is", ["total", "per_person"], horizontal=True,
              format_func=lambda value: "for the whole party" if value == "total"
              else "per person", key="f_basis",
              help="Say which you mean — the same number means very different "
                   "trips for a party of four.")
          party_size = st.number_input("Party size", 1, 20, 2, key="f_party")
          # Offline can only offer cuisines the dataset holds; live can search
          # the world. The list follows the backend the sidebar selected.
          cuisines = st.multiselect("Restaurant types",
                                    _cuisine_options(backend),
                                    default=["international"], key="f_cuisines")
          attraction_types = st.multiselect(
              "Attraction types", vocabulary.attraction_types(),
              disabled=food_only, key="f_attraction_types",
              help="Leave empty for any kind." if not food_only else
                   "Disabled while Food only is on.")
          allergies = st.multiselect(
              "Allergies (hard exclusion)", vocabulary.CANONICAL_ALLERGENS,
              disabled=no_allergies, key="f_allergies",
              help="Uncheck 'No allergies' above to pick from the nine the "
                   "dataset flags explicitly.")

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
              attractions_per_day = st.number_input(
                  "Attractions per day", 0, 3, 1, key="f_attractions_per_day",
                  disabled=food_only,
                  help="2 gives you one before lunch and one before dinner.")
              return_to_origin = st.checkbox(
                  "Return to the starting point each day", value=True,
                  key="f_return",
                  help="Adds the trip home, and counts it toward the daily "
                       "travel limit.")
          with far_right:
              max_leg = st.slider("Max travel between stops (min)", 5, 90, 25, 5,
                                  key="f_max_leg")
              max_daily_hours = st.slider(
                  "Max total travel per day (hours)", 0.5, 5.0, 2.0, 0.5,
                  key="f_max_daily_hours",
                  help="Converted to minutes for the planner. The per-leg limit "
                       "stays in minutes - hours would read badly there.")

      submitted = st.form_submit_button("Plan my trip", type="primary")

if not vocabulary.covers_city(city):
    ui.dataset_city_warning(city, backend, vocabulary.dataset_cities())

# Checked on submit only: it costs an API call, and a country here quietly
# poisons every text query ("restaurant dinner in Portugal").
if submitted:
    ui.city_scope_warning(classify_city(city))

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
                    origin=origin, budget_total=float(budget_amount),
                    budget_basis=budget_basis, party_size=int(party_size),
                    meals=meals or list(vocabulary.MEAL_SLOTS),
                    cuisines=cuisines or ["international"],
                    attraction_types=[] if food_only else attraction_types,
                    attractions_per_day=0 if food_only else int(attractions_per_day),
                    family_friendly=family_friendly,
                    return_to_origin=return_to_origin,
                    allergies=[] if no_allergies else allergies,
                    search_radius_km=float(search_radius),
                    min_rating=min_rating or None,
                    min_reviews=int(min_reviews) or None,
                    max_leg_minutes=float(max_leg),
                    max_daily_travel_minutes=float(max_daily_hours) * 60.0,
                    transport_mode=transport, tier=int(tier),
                    data_backend=backend)
                request = trip.to_request_dict()
                # Requirements the description asked for that no field can hold.
                # Passed through so the verification panel lists them as
                # unverifiable instead of letting them disappear.
                request["extra_criteria"] = st.session_state.get(
                    "intent_criteria") or []
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
