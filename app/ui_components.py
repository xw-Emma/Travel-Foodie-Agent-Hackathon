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
        headline = meta.get("llm_fallback_message") or (
            "The LLM step failed, so this plan was built without it.")
        st.warning(
            f"**{headline}** The tools, constraints and budget all still ran; "
            "the planner and critic used their deterministic rules instead. "
            f"({meta['llm_fallback']})", icon=":material/cloud_off:")

    unresolved = meta.get("unresolved_issues") or []
    if unresolved:
        lines = "\n".join(
            f"- **{issue.get('slot')}** ({issue.get('type')}): {issue.get('detail')}"
            for issue in unresolved)
        st.warning(
            "**Shipped with constraints still unmet.** The critic could not "
            f"resolve these within its revision limit:\n\n{lines}",
            icon=":material/warning:")

    # Shown BEFORE the over-budget banner: knowing the constraints cannot be met
    # at all explains the overage, and generic advice ("shorten the trip") is
    # worth less than the arithmetic that would actually make it fit.
    report = meta.get("feasibility") or {}
    if report.get("checked") and report.get("feasible") is False:
        options = "\n".join(
            f"- {item['text']}" for item in report.get("suggestions") or [])
        st.error(
            f"**These constraints cannot be met.** {report.get('reason', '')}\n\n"
            f"{options}", icon=":material/block:")
    elif str(budget.get("status")) == "exceeded":
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


def render_budget(budget: dict, request: dict | None = None) -> None:
    request = request or {}
    columns = st.columns(4)
    columns[0].metric("Projected", f"${float(budget.get('projected', 0)):,.2f}")
    columns[1].metric("Budget", f"${float(budget.get('limit', 0)):,.2f}")
    columns[2].metric("Remaining", f"${float(budget.get('remaining', 0)):,.2f}")
    limit = float(budget.get("limit") or 0)
    used = (float(budget.get("projected") or 0) / limit) if limit else 0
    columns[3].metric("Status", str(budget.get("status", "unknown")).title(),
                      f"{used:.0%} used")
    if request.get("budget_basis") == "per_person":
        party = int(request.get("party_size") or 1)
        entered = float(request.get("budget_entered") or 0)
        st.caption(f"${entered:,.2f} per person × {party} = "
                   f"${limit:,.2f} total for the party.")
    # Costs come from Google's price BAND, not a real menu price, so a figure
    # like $60.00 reads far more precise than it is.
    if str(request.get("data_backend")) != "local":
        st.caption("Live costs are estimated from Google's price level, not "
                   "actual menu prices — treat them as a band, not a quote.")


# --------------------------------------------------------------- day tabs
STATE_ICONS = {
    "verified": ("✅", "Verified against data"),
    "inferred": ("⚠️", "Met by inference, not verified"),
    "failed": ("❌", "Not met"),
    "unverifiable": ("🚫", "No data source exists to check this"),
    "not_requested": ("–", "Not requested"),
}


def render_verification_panel(report: dict) -> None:
    """Every stated requirement, and how each answer was actually arrived at.

    The four states matter more than the table. A green tick against live
    allergen filtering would claim a check that Google's data cannot support,
    so those read as inferred with the caveat attached; Michelin sits
    permanently at unverifiable rather than being quietly dropped, because an
    omitted requirement looks like a satisfied one.
    """
    summary = report.get("summary") or {}
    requirements = [r for r in (report.get("requirements") or [])
                    if r.get("state") != "not_requested"]
    headline = summary.get("headline", "")
    if summary.get("failed"):
        st.error(f"**{headline}**", icon=":material/rule:")
    elif summary.get("unverifiable") or summary.get("inferred"):
        st.warning(f"**{headline}**", icon=":material/rule:")
    else:
        st.success(f"**{headline}**", icon=":material/rule:")

    rows = []
    for item in requirements:
        icon, _ = STATE_ICONS.get(item.get("state"), ("?", ""))
        rows.append({
            "": icon,
            "Requirement": item.get("requirement"),
            "Expected": str(item.get("expected")),
            "Found": str(item.get("actual")),
            "Source": str(item.get("source") or "—"),
            "Checked": str(item.get("fetched_at") or "—"),
        })
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    # Anything not plainly verified needs its reason spelled out, not buried.
    for item in requirements:
        if item.get("state") == "verified" or not item.get("reason"):
            continue
        icon, label = STATE_ICONS.get(item.get("state"), ("?", ""))
        st.caption(f"{icon} **{item.get('requirement')}** — {label.lower()}: "
                   f"{item['reason']}")

    legend = " · ".join(f"{icon} {label}" for state, (icon, label)
                        in STATE_ICONS.items() if state != "not_requested")
    st.caption(legend)


def render_day_summary(summary: list[dict]) -> None:
    """Whole-trip shape at a glance, and which day comes out best."""
    if not summary:
        return
    st.dataframe(pd.DataFrame([{
        "Day": row["day"], "Stops": row["stops"],
        "Cost": f"${row['cost']:,.2f}",
        "Travel": f"{row['travel_minutes']:.0f} min · {row['travel_km']} km",
        "Avg rating": row["average_rating"] if row["average_rating"] else "—",
    } for row in summary]), width="stretch", hide_index=True)
    if len(summary) > 1:
        # Best = highest average rating, least travel as the tie-break.
        best = max(summary, key=lambda row: (row["average_rating"] or 0,
                                             -row["travel_minutes"]))
        st.success(
            f"**Best overall day: Day {best['day']}** — {best['stops']} stops, "
            f"${best['cost']:,.2f}, {best['travel_minutes']:.0f} min of travel"
            + (f", averaging {best['average_rating']} stars"
               if best["average_rating"] else ""),
            icon=":material/star:")


def render_backups(entry: dict) -> None:
    """Runners-up, with the arithmetic that put them second.

    An unexplained ranking is a black box. These carry search-level facts only:
    fetching details for venues nobody chose would triple the billed calls.
    """
    chosen = entry.get("chosen") or {}
    rows = []
    for label, option in ([("Chosen", chosen)]
                          + [("Backup", alt) for alt in entry.get("alternatives") or []]):
        facts, score = option.get("facts") or {}, option.get("score") or {}
        travel = score.get("travel_minutes")
        rows.append({
            "": label,
            "Venue": facts.get("name"),
            "Rating": (f"{facts.get('rating')} ({facts.get('review_count')})"
                       if facts.get("rating") is not None else "—"),
            "Cost": f"${float(score.get('cost') or 0):,.2f}",
            "Travel": (f"{travel:.0f} min" if travel is not None
                       else "start of day"),
            "Score": ("over the slot's budget" if not score.get("affordable")
                      or score.get("total") is None
                      else f"{score.get('total'):.1f}"),
        })
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    score = chosen.get("score") or {}
    st.caption(
        f"Score = rating x 10 ({score.get('rating_points', 0)}) minus a penalty "
        f"for travel beyond the limit ({score.get('distance_penalty', 0)}). "
        f"Anything costing more than the slot's ${score.get('budget_remaining', 0):,.2f} "
        f"allowance is rejected outright. Chosen from {entry.get('pool_size', 0)} "
        "verified candidates.")


def render_venue_detail(entry: dict, backups: dict | None = None) -> None:
    """One stop, with verifiable facts kept visibly apart from model commentary.

    The separation is the point. Facts go in a table with their source; anything
    the model wrote is quoted and shown with what it was drawn from; claims the
    data cannot support are listed as unverifiable rather than quietly omitted,
    because an omitted limitation reads as a satisfied requirement.
    """
    facts = entry.get("facts") or {}
    comment = entry.get("comment") or {}

    st.markdown("**Verified facts**")
    rows = [
        ("Name", facts.get("name")),
        ("Neighbourhood", facts.get("neighborhood")
         or "unknown — Google lists no district for this venue"),
        ("Rating", f"{facts.get('rating')} ({facts.get('review_count')} reviews)"
         if facts.get("rating") is not None else "unknown"),
        ("Price level", facts.get("price_level")),
        ("Address", facts.get("address")),
        ("Website", facts.get("website") or "not published"),
        ("Phone", facts.get("phone") or "not published"),
    ]
    cost = facts.get("cost_per_person") or {}
    if cost:
        label = ("estimated from Google's price band"
                 if cost.get("basis") == "price_band_estimate" else "dataset value")
        rows.append(("Cost per person", f"${cost.get('value'):,.2f} — {label}"))
    # Every value is stringified: the column mixes text, numbers and None, and
    # Arrow refuses a mixed-type column outright.
    st.dataframe(pd.DataFrame([{"Field": key, "Value": str(value)}
                               for key, value in rows if value is not None]),
                 width="stretch", hide_index=True)
    st.caption(f"Source: `{facts.get('source')}` · fetched {facts.get('fetched_at')}")

    dishes = comment.get("dishes_mentioned_in_reviews") or {}
    st.markdown("**Mentioned in reviews**")
    if dishes.get("dishes"):
        for item in dishes["dishes"]:
            st.markdown(f"- **{item['dish']}**")
            if item.get("quote"):
                # Shown as a quotation because it is somebody's review, not a
                # claim this system is making.
                st.caption(f"> …{item['quote']}…  — review #{item['review_index'] + 1}")
        st.caption(f"Drawn from the {dishes.get('review_count', 0)} reviews Google "
                   "returns. Not a recommendation — just what reviewers named.")
    else:
        st.caption(dishes.get("note") or "No dishes were named in the reviews.")

    reservation = comment.get("reservation") or {}
    if reservation.get("text"):
        st.markdown(f"**Booking** — {reservation['text']}")

    if backups and backups.get("alternatives"):
        st.markdown("**Runners-up**")
        render_backups(backups)

    unverifiable = entry.get("unverifiable") or {}
    if unverifiable:
        for claim, reason in unverifiable.items():
            st.warning(f"**{claim.title()}: UNVERIFIABLE.** {reason}",
                       icon=":material/help:")


def render_day_tabs(itinerary: list[dict], routes: list[dict],
                    day_labels: list[str] | None = None,
                    max_daily_minutes: float = 120.0,
                    enrichment: list[dict] | None = None,
                    backups: list[dict] | None = None) -> None:
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
            detail_by_slot = {entry.get("slot"): entry
                              for entry in (enrichment or [])}
            backup_by_slot = {entry.get("slot"): entry
                              for entry in (backups or [])}
            for stop in stops:
                entry = detail_by_slot.get(stop.get("slot"))
                if entry:
                    with st.expander(f"{stop.get('name')} — facts, sources and alternatives"):
                        render_venue_detail(entry, backup_by_slot.get(stop.get("slot")))
                elif stop.get("why"):
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


def city_scope_warning(classification: dict) -> None:
    """Warn when the city box holds something that is not a city.

    A country there poisons every text query - "restaurant dinner in Portugal"
    returns whatever Google picks across a nation, which is how a venue called
    "Restaurant International" ended up standing in for Lisbon dining.
    """
    kind = (classification or {}).get("kind")
    name = (classification or {}).get("name") or "that"
    if kind in ("locality", "not_checked", "unknown", None):
        return
    if kind == "country":
        st.warning(
            f"**{name} is a country, not a city.** Every search is phrased "
            f"\"…in {name}\", so results can come from anywhere in it. Name a "
            "city — Lisbon, Porto — for results that hang together.",
            icon=":material/public:")
    elif kind.startswith("administrative_area"):
        st.warning(
            f"**{name} is a region, not a city.** Naming the city itself gives "
            "tighter, more walkable results.", icon=":material/public:")
    else:
        st.info(
            f"**{name}** did not resolve to a city. If results look scattered, "
            "try the city name on its own.", icon=":material/public:")


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
