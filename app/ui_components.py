"""Render functions shared by both Streamlit apps.

WHY SHARED: app/streamlit_app.py runs the orchestrator in-process and
frontend/streamlit_app.py calls the same pipeline over HTTP, but they show the
same plan. Two independent copies drifted immediately - the deployed UI kept a
hardcoded tier=1 for a week while the local one had a tier selector - so the
rendering lives here once and both import it.

Everything here takes plain dicts/lists, never a TripState, so it works
identically for an in-process result and a JSON response.
"""
from __future__ import annotations

import pandas as pd
import pydeck as pdk
import streamlit as st

from src.polyline import decode_polyline

DAY_COLORS = [[38, 112, 201], [221, 110, 36], [44, 145, 96], [141, 91, 170],
              [193, 66, 84], [86, 96, 176], [176, 143, 40]]
ORIGIN_COLOR = [30, 30, 30]
MEAL_COLOR = [38, 112, 201]
ATTRACTION_COLOR = [221, 110, 36]

SOURCE_LABELS = {
    "google_places": "live",
    "google_routes": "live",
    "local_dataset": "offline",
    "haversine_fallback": "offline",
    "city_centre": "estimated",
}


def _is_attraction(slot: str) -> bool:
    return ".attraction" in (slot or "")


def _day_of(slot: str) -> int:
    head = str(slot).split(".", 1)[0]
    return int(head[3:]) if head.startswith("day") and head[3:].isdigit() else 0


def source_badge(source: str | None) -> str:
    """`google_places` reads as live data; `local_dataset` as the offline copy.

    Showing this per row is the cheapest way to make Tier 1 vs Tier 2 and live
    vs offline visible - the complaint that "every run looks the same" was
    largely that nothing on screen said which path had run.
    """
    if not source:
        return "—"
    return f"`{source}` ({SOURCE_LABELS.get(source, 'unknown')})"


# ------------------------------------------------------------------ banners
def render_banners(meta: dict, request: dict, budget: dict) -> None:
    """Everything the user must be told before reading the plan."""
    if meta.get("demo_mode"):
        st.error(
            "**Demo mode — this is a replayed plan.** No API was called and no "
            f"agent ran for this request. Captured {meta.get('captured_at', 'earlier')}. "
            "Unset `FOODIE_DEMO_MODE` to plan for real.",
            icon=":material/videocam:")

    if meta.get("llm_fallback"):
        st.warning(
            "**Fuel iX was unreachable, so this plan was built without the LLM.** "
            "The tools, constraints and budget all still ran; the planner and "
            f"critic used their deterministic rules instead. ({meta['llm_fallback']})",
            icon=":material/cloud_off:")

    unresolved = meta.get("unresolved_issues") or []
    if unresolved:
        lines = "\n".join(
            f"- **{issue.get('slot')}** ({issue.get('type')}): {issue.get('detail')}"
            for issue in unresolved)
        st.warning(
            "**Shipped with constraints still unmet.** The critic could not "
            f"resolve these within its revision limit:\n\n{lines}",
            icon=":material/warning:")

    if str(budget.get("status")) == "exceeded":
        over = abs(float(budget.get("remaining") or 0))
        st.warning(
            f"**Over budget by ${over:,.2f}.** Raise the budget, reduce the "
            "party size, or shorten the trip.", icon=":material/payments:")

    allergies = request.get("allergies") or []
    if allergies:
        # What matters is where the venue data actually came from, not what was
        # requested: an auto run that fell back offline has explicit flags.
        live_data = (meta.get("tool_backends") or {}).get("restaurants") == "google_places"
        note = (
            " Google Places has no allergen fields, so live results infer risk "
            "from cuisine type only." if live_data else
            " The offline dataset carries explicit allergen flags for all nine "
            "canonical allergens.")
        st.info(
            f"**Filtered for: {', '.join(allergies)}.**{note} "
            "**Always confirm directly with the restaurant.**",
            icon=":material/health_and_safety:")


def render_budget(budget: dict) -> None:
    columns = st.columns(4)
    columns[0].metric("Projected", f"${float(budget.get('projected', 0)):,.2f}")
    columns[1].metric("Budget", f"${float(budget.get('limit', 0)):,.2f}")
    columns[2].metric("Remaining", f"${float(budget.get('remaining', 0)):,.2f}")
    limit = float(budget.get("limit") or 0)
    used = (float(budget.get("projected") or 0) / limit) if limit else 0
    columns[3].metric("Status", str(budget.get("status", "unknown")).title(),
                      f"{used:.0%} used")


# --------------------------------------------------------------- day tabs
def render_day_tabs(itinerary: list[dict], routes: list[dict],
                    day_labels: list[str] | None = None,
                    max_daily_minutes: float = 120.0) -> None:
    """One tab per day: stops in visiting order, travel between them, running cost."""
    days = sorted({_day_of(item.get("slot", "")) for item in itinerary} - {0})
    if not days:
        st.info("No stops were planned.")
        return
    labels = day_labels or []
    tabs = st.tabs([labels[index] if index < len(labels) else f"Day {day}"
                    for index, day in enumerate(days)])
    routes_by_day = {route.get("day"): route for route in routes}

    for tab, day in zip(tabs, days):
        with tab:
            route = routes_by_day.get(day, {})
            order = route.get("stop_order") or []
            stops = [item for item in itinerary if _day_of(item.get("slot", "")) == day]
            if order:
                rank = {slot: index for index, slot in enumerate(order)}
                stops.sort(key=lambda item: rank.get(item.get("slot"), 99))
            legs = {leg.get("to_slot"): leg for leg in route.get("legs", [])}

            running = 0.0
            rows = []
            for stop in stops:
                leg = legs.get(stop.get("slot"))
                running += float(stop.get("cost") or 0)
                rows.append({
                    "Stop": stop.get("slot", "").split(".", 1)[-1],
                    "Venue": stop.get("name", ""),
                    "Travel to here": (f"{leg['minutes']:.0f} min · {leg['km']} km"
                                       if leg and leg.get("minutes") is not None else "start"),
                    "Cost": f"${float(stop.get('cost') or 0):,.2f}",
                    "Running total": f"${running:,.2f}",
                    "Source": source_badge(stop.get("source")),
                })
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

            totals = route.get("totals") or {}
            minutes = float(totals.get("minutes") or 0)
            summary = (f"Travel {minutes:.0f} min · {totals.get('km', 0)} km "
                       f"· {len(stops)} stops · ${running:,.2f}")
            if route.get("optimized"):
                summary += " · route order optimized"
            if route.get("optimize_rejected"):
                summary += f" · optimization declined ({route['optimize_rejected']})"
            if minutes > max_daily_minutes:
                st.error(f"{summary} — over the {max_daily_minutes:.0f} min daily limit.")
            else:
                st.caption(summary)
            for stop in stops:
                if stop.get("why"):
                    st.caption(f"**{stop.get('name')}** — {stop['why']}")


# ------------------------------------------------------------------- map
def render_map(itinerary: list[dict], routes: list[dict],
               visible_days: list[int] | None = None) -> None:
    shown = set(visible_days) if visible_days else None
    points = []
    for item in itinerary:
        if item.get("lat") is None or item.get("lon") is None:
            continue
        day = _day_of(item.get("slot", ""))
        if shown is not None and day not in shown:
            continue
        attraction = _is_attraction(item.get("slot", ""))
        points.append({
            "lat": item["lat"], "lon": item["lon"], "name": item.get("name", ""),
            "kind": "attraction" if attraction else "meal",
            "detail": f"day {day} · ${float(item.get('cost') or 0):,.2f} · "
                      f"{item.get('source') or 'unknown'}",
            "radius": 90,
            "color": ATTRACTION_COLOR if attraction else MEAL_COLOR,
        })

    origin_points = []
    path_rows = []
    for index, route in enumerate(routes):
        day = route.get("day")
        if shown is not None and day not in shown:
            continue
        origin = route.get("origin") or {}
        if origin.get("lat") is not None:
            origin_points.append({
                "lat": origin["lat"], "lon": origin["lon"],
                "name": origin.get("name") or "Start", "kind": "origin",
                "detail": f"day {day} start", "radius": 150, "color": ORIGIN_COLOR})
        path = []
        for leg in route.get("legs", []):
            if not leg.get("polyline"):
                continue
            decoded = decode_polyline(leg["polyline"])
            path.extend(decoded if not path else decoded[1:])
        if path:
            path_rows.append({"day": day, "path": path,
                              "color": DAY_COLORS[index % len(DAY_COLORS)]})

    all_points = points + origin_points
    if not all_points:
        st.info("No geocoded stops are available for this plan.")
        return

    layers = []
    if path_rows:
        layers.append(pdk.Layer("PathLayer", data=path_rows, get_path="path",
                                get_color="color", width_min_pixels=3, pickable=True))
    layers.append(pdk.Layer(
        "ScatterplotLayer", data=all_points, get_position="[lon, lat]",
        get_radius="radius", get_fill_color="color", pickable=True,
        stroked=True, get_line_color=[255, 255, 255], line_width_min_pixels=2))

    frame = pd.DataFrame(all_points)
    # Fit the bounding box of everything shown instead of a fixed zoom, so a
    # spread-out day is not cropped and a compact one is not lost in the map.
    view = pdk.data_utils.compute_view(frame[["lon", "lat"]].values.tolist(),
                                       view_proportion=0.9)
    st.pydeck_chart(
        pdk.Deck(layers=layers, initial_view_state=view,
                 tooltip={"text": "{name}\n{kind}\n{detail}"}),
        width="stretch")
    st.caption(f"{len(points)} stops · {len(origin_points)} start points · "
               f"{len(path_rows)} day routes")


# ----------------------------------------------------------- panels
def render_trace(trace: list[dict]) -> None:
    for entry in trace:
        if isinstance(entry, dict):
            st.write(f"**{entry.get('agent', 'agent')}**: {entry.get('message', '')}")
        else:
            st.write(f"- {entry}")


def render_routes_table(routes: list[dict]) -> None:
    rows = [{
        "Day": route.get("day"), "From": leg.get("from"), "To": leg.get("to"),
        "km": leg.get("km"), "Minutes": leg.get("minutes"),
        "Source": leg.get("source"),
    } for route in routes for leg in route.get("legs", [])]
    if rows:
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    else:
        st.caption("No route legs were computed.")


def render_diagnostics(report: dict) -> None:
    """Turns 'which backend actually ran, and why' into something visible."""
    decision = report.get("live_decision", "unknown")
    if decision in ("forced_live", "auto_key_present"):
        st.success(f"Live data: {decision}", icon=":material/cloud_done:")
    else:
        st.warning(f"Offline data: {decision}", icon=":material/cloud_off:")

    for name in ("places_api", "routes_api"):
        probe = report.get(name) or {}
        label = name.replace("_", " ").title()
        if probe.get("ok") is None:
            st.caption(f"{label}: not probed")
        elif probe["ok"]:
            st.caption(f"✅ {label}: {probe.get('latency_ms', '?')} ms")
        else:
            st.caption(f"❌ {label}: HTTP {probe.get('http_status', '?')} — "
                       f"{str(probe.get('reason', ''))[:80]}")

    rows = report.get("local_dataset_rows") or {}
    st.caption(f"Offline dataset: {rows.get('restaurants', 0)} restaurants, "
               f"{rows.get('attractions', 0)} attractions "
               f"({', '.join(report.get('local_dataset_cities') or ['none'])})")
    if not report.get("database_built"):
        st.error("data/foodie.sqlite is missing — run `python data/seed.py`.")
    st.caption(f"Cache {'on' if report.get('cache_enabled') else 'off'} · "
               f"LLM {'mock' if report.get('mock_llm') else 'Fuel iX'}")


def dataset_city_warning(city: str, backend: str, covered_cities: list[str]) -> None:
    """B10: the offline dataset is Calgary-only, but the UI offers other cities."""
    if backend == "local":
        st.warning(
            f"The offline dataset covers {', '.join(covered_cities)} only, so "
            f"**{city}** will return nothing in `local` mode. Switch to `live` "
            "or `auto` for other cities.", icon=":material/travel_explore:")
    else:
        st.info(
            f"**{city}** is not in the offline dataset ({', '.join(covered_cities)} "
            "only). This plan needs the live APIs; there is no offline fallback "
            "for it.", icon=":material/travel_explore:")
