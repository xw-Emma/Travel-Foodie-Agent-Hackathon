# Phase I / J / K — Broken fields, attractions that match the ask, routing you can read

**Target repo:** `xw-Emma/Travel-Foodie-Agent-Hackathon` (branch `master`, HEAD `4a53259`)
**Audience:** AI coding assistant / IDE agent
**Written:** 2026-08-29
**Status of analysis:** Everything below was verified against the code or the
live API. Where a claim came from a real API call, the call is quoted.

Continues `TIER2_TO_MAP_UI_IMPLEMENTATION_PLAN.md` (phases 0–5, A–E) and
`PHASE_FGH_IMPLEMENTATION_PLAN.md` (F, G, H), all complete and pushed.

---

## 0. Ground rules carried forward

Unchanged, and every phase below inherits them:

1. **Tools only** — agents reach data through tool calls, never model memory.
2. **Constraints in code** — filters and arithmetic are enforced in Python.
3. **Bounded loop** — closed slot vocabulary, max 2 revisions.
4. **Never name a venue from a model** — structural, not a prompt instruction.
5. **Say what you could not do** — a silently dropped constraint is
   indistinguishable from a satisfied one.
6. **An agent, not a chatbot** — the conversation gathers; the pipeline plans.

Add one this round:

7. **Never state a fact the data does not support.** See I4: every live
   attraction is currently labelled kid-friendly because a literal `True` was
   typed into the row. That is the same category of failure as a hallucinated
   venue, and it is worse than having no field at all.

---

## 1. What was found

### 1.1 Reported and confirmed

| # | Report | Root cause |
|---|---|---|
| 1 | Allergies dropdown does nothing when "No allergies" is unchecked | Both widgets are inside `st.form`. A form does **not** rerun on interaction, so `disabled=no_allergies` keeps the value from the previous render until something else triggers a rerun. **"Food only — no attractions" has the identical bug** and was not reported. |
| 2 | Max daily travel in minutes is hard to reason about | Presentation only. |
| 3 | "Fine-tune the details" collapses after a chat turn | `expanded="intent_draft" not in st.session_state` — deliberate in Phase G, wrong in practice. |
| 4a | Asked for two attractions, got one | Three separate gaps, see J1. |
| 4b | Says "we love museums", gets CN Tower | `attraction_types` is a **dead field** (see 1.2). |
| 4c | Started at Union Station, no return leg | Never implemented; the origin is prepended to a day, never appended. |
| 4d | Can travel time be seen on the map? | It is already computed — just not shown there. See K2. |

### 1.2 Dead field: `attraction_types` is collected and never used

Traced end to end. It is offered in both UIs, validated into `TripRequest`, sent
over HTTP — and **no search ever reads it**. `_execute_attractions_tier2` takes a
`category` parameter that the orchestrator never passes, so every attraction
search is the literal string `"tourist attraction in <city>"`.

That is the whole of 4b, and it is provable. Measured live:

```
"tourist attraction in Toronto"  -> CN Tower, Ripley's, ...
"museum in Toronto"              -> Royal Ontario Museum, Casa Loma,
                                    Art Gallery of Ontario, Museum of Illusions
```

The category is the fix. The data was always there.

This is the third dead field of its kind (`search_radius_km` in Phase A,
`min_rating` before Phase C). **Worth a standing check** — see K3.

### 1.3 One attraction per day is structural, not a setting

`attractions_per_day` is honoured for 0 (Phase A) but never above 1:

- `intent`'s schema exposes only `attractions_wanted: bool` — a count cannot be
  expressed, so "one in the morning, another in the afternoon" is unsayable.
- Both UIs hardcode `attractions_per_day = 0 if food_only else 1`.
- `_execute_attractions_tier2` builds exactly one slot per day:
  `slot = f"day{day}.attraction1"`.

`DAY_ORDER` already interleaves `attraction1` before lunch and `attraction2`
before dinner, so morning/afternoon placement needs no new concept — only the
slots need to exist.

### 1.4 A fabricated fact, and the honest fix

`places_live.search_attractions` hardcodes `"kid_friendly": True` on every row.
It is not read from anywhere. Every live attraction is asserted to be
kid-friendly whether or not it is.

**Places does carry this, verified live:**

```
museum in Toronto          -> ROM  goodForChildren=True   menuForChildren=None
family restaurant Toronto  -> The Old Spaghetti Factory
                              goodForChildren=True  menuForChildren=True
```

So the answer to "does Google have kids information?" is **yes**:
`goodForChildren` and `menuForChildren` are real fields. The fix is to read them
and to stop asserting what was never checked.

### 1.5 A verification blind spot to know about

`AppTest` reruns the script on every interaction; a real `st.form` does not.
That means **AppTest cannot reproduce issue 1** — it reported the checkbox
working correctly. Any fix here has to be reasoned about from Streamlit's form
semantics and confirmed in a browser, not signed off by the suite alone. Say so
in the commit rather than claiming a green run proves it.

---

## 2. Phase I — Fix what is broken

**Effort: ~2 h. All small, all user-visible, no planning logic touched.**

### CAUSE / EFFECT

Two widgets are inert until a submit, a slider asks for arithmetic the user
should not have to do, a panel hides itself mid-task, and every live attraction
carries an unchecked claim.

### CHANGES

**I1 — Move the two gating checkboxes out of the form.** "No allergies" and
"Food only — no attractions" must sit *above* `st.form`, where interacting with
them reruns the script and `disabled=` is evaluated fresh. Keep them adjacent to
the fields they gate so the grouping still reads.

> Alternative considered and rejected: dropping `disabled=` and ignoring the
> value. That leaves an editable control whose input is silently discarded,
> which is worse than one that is visibly greyed out.

**I2 — Max total travel per day in hours.** Slider in hours (0.5–5.0, step 0.5),
converted to minutes at the boundary. `max_leg_minutes` stays in minutes — that
one is naturally a minutes-scale quantity and hours would read badly.

**I3 — "Fine-tune the details" always expanded.** `expanded=True`. The Phase G
reasoning (collapse once a draft exists) is wrong once the chat is iterative:
the panel is where you check the chat's work, so it must not hide exactly when
there is something to check.

**I4 — Stop asserting kid-friendliness.** Remove the hardcoded
`"kid_friendly": True` from `places_live.search_attractions`. Read
`goodForChildren` from the field mask instead, and leave it `None` when Google
does not say. `None` means unknown and must render as unknown — never as a tick.

### ACCEPTANCE

```bash
python eval/verify_all.py
python eval/acceptance.py --live
```

Plus, because the suite cannot see it (§1.5), a **browser check**: uncheck
"No allergies" and confirm the multiselect becomes usable **without** submitting;
same for "Food only".

New assertions in `eval/verify_facts.py`:

1. A live attraction with no `goodForChildren` from Google reports `None`, not
   `True`.
2. One that does report it carries the real value.
3. Nothing in the UI renders `None` as a positive claim.

---

## 3. Phase J — Attractions that match what was asked for

**Effort: ~3 h.**

### CAUSE / EFFECT

The planner can express "an attraction" but not "a museum", and not "two of
them". A request naming both gets one generic landmark.

### CHANGES

**J1 — More than one attraction per day.**

- `intent`: replace `attractions_wanted: bool` with `attractions_per_day: int`
  (0–3), keeping the bool accepted as a deprecated alias so existing callers and
  the Phase B suite keep working.
- Both UIs: a number input (0–3) instead of the food-only checkbox alone; the
  checkbox stays as the shortcut for 0.
- `_execute_attractions_tier2`: build `attraction1 … attractionN` per day rather
  than one, deduplicating across the whole trip as it already does.
- `_run_tier2_async`: iterate the same range when attaching candidates.
- `slot_ids` and `DAY_ORDER` already support up to `attraction3` — confirm, do
  not extend.

**J2 — Actually use the category.** Pass `attraction_types` through to
`search_attractions`. With several types, search each and interleave, so
"museum or outdoor" returns both rather than only the first. Offline, the
category already filters; live, it becomes the text query — which is the entire
fix for CN Tower.

**J3 — Family-friendly, from real fields.**

- Add `goodForChildren` (and `menuForChildren` for restaurants) to the field
  masks; both are already proven present.
- Add `family_friendly: bool` to `TripRequest`, and a `kids` question to the
  conversation's closed list.
- Filter in the tool layer like the quality gate: `goodForChildren is False`
  excludes; `None` does **not** — unknown is not a reason to exclude, and it is
  not a reason to promise either.
- Verification panel: `verified` when Google answered, `unverifiable` when it did
  not. Never `inferred` here — there is no heuristic worth trusting for whether
  a place suits a child.

### ACCEPTANCE

New `eval/verify_attractions.py`:

1. `attractions_per_day=2` produces `day1.attraction1` and `day1.attraction2`,
   both distinct venues.
2. They land either side of lunch per `DAY_ORDER`, so morning/afternoon holds.
3. `attraction_types=["museum"]` in Toronto returns museums — assert ROM or AGO
   appears, and CN Tower does not.
4. Two types return a mix of both.
5. `family_friendly=True` excludes a venue Google marks `goodForChildren=False`
   and keeps one it marks `True`.
6. A venue with no answer is kept, and the panel calls it unverifiable.
7. `attractions_per_day=0` and the absent case behave exactly as today.

---

## 4. Phase K — Routing you can read

**Effort: ~3 h.**

### CAUSE / EFFECT

A day that starts at Union Station never returns to it, and the travel figures
the system already computes are invisible on the map.

### CHANGES

**K1 — Close the loop.** `return_to_origin: bool` on `TripRequest`, defaulting
to **True whenever an origin is resolved** — arriving somewhere by train and not
going home is the unusual case, not the usual one.

- `_compute_routes_async`: append the origin as `day{N}.return` after the last
  stop when enabled.
- `is_valid_slot`: accept `day{N}.return` alongside `day{N}.origin` — both are
  scopes no agent may re-plan.
- The return leg **counts** toward `max_daily_travel_minutes`. Getting home is
  travel, and a limit that ignores it is not a limit.
- The conversation can infer it ("I'll take the GO train home"), but the
  checkbox stays authoritative.

> Watch: the critic must not try to reselect a venue for `day{N}.return`. The
> existing day-scope guard covers `origin`; extend the same treatment rather than
> writing a second rule.

**K2 — Per-leg detail on the map.** Today `path_rows` carries only
`{day, path, color}` — one merged polyline per day, so there is nothing to hover.
Emit **one row per leg** carrying `mode`, `km`, `minutes`, `from`, `to`, and set
the deck tooltip to show them. The data is already in `st.routes[].legs[]`; only
the shape changes.

Keep the per-day colour so a day still reads as one route.

**K3 — A standing check against dead fields.** Three request fields have now
shipped collected-but-unread (`min_rating`, `search_radius_km`,
`attraction_types`). Add a test that walks `TripRequest`'s fields and asserts
each is referenced somewhere under `src/` outside the model definition. It will
not catch a field that is read but ignored, but it would have caught all three
of these.

### ACCEPTANCE

Extend `eval/verify_routing.py`:

1. With an origin and `return_to_origin=True`, the last leg of each day ends at
   the origin's coordinates.
2. That leg's minutes are included in the day total.
3. With `return_to_origin=False`, behaviour is exactly as today.
4. The critic never emits a revision slot for `day{N}.return`.
5. Every map path row carries `mode`, `km` and `minutes`.
6. The dead-field walk passes for every field in `TripRequest`.

---

## 5. Sequencing

| Order | Phase | Why |
|---|---|---|
| 1 | **I** | All four are small and two are visible in the first ten seconds of a demo. I4 is a correctness fix, not cosmetics. |
| 2 | **J** | The largest behavioural gain: it is the difference between "an attraction" and "the museum I asked for". |
| 3 | **K** | Routing and map polish; K1 changes route totals, so it lands after J's slots are settled. |

**Minimum cut line.** I and J2 alone fix everything visible in the reported
session: the controls respond, and asking for museums returns museums.

---

## Appendix A — Answers to the direct questions

**"Does the agent calculate time between stops?"** Yes, already. Every leg in
`st.routes[].legs[]` carries `minutes`, `km`, `mode` and `source`, computed by
the Routes API live or haversine offline, and honouring the selected transport
mode. It is shown in the Routes table and in each venue card; it is simply not
on the map yet. K2 puts it there.

**"Does Google Places have kids information?"** Yes — `goodForChildren` on
places, `menuForChildren` on restaurants, both verified live above. What it does
*not* have is any allergen data, which is why live allergen filtering remains
`inferred` and must stay that way.

**"Why CN Tower instead of ROM or AGO?"** Because the category you picked was
never sent. The search string was `"tourist attraction in Toronto"`. Sending
`"museum in Toronto"` returns ROM, Casa Loma and AGO — measured.

## Appendix B — Things not to "fix"

Carried forward, plus this round's:

1. Meal-type filtering in `local_catalog.search_restaurants` is correct.
2. `stable_review_count()` must stay md5 — `hash()` is salted per process.
3. `_plan_with_llm` passing `attractions_per_day=0` is deliberate.
4. Filling a slot when nothing is affordable is deliberate; F changed *when* the
   user is told, not the fallback.
5. `classify_city` returning `not_checked` offline is deliberate.
6. The four verification states. Collapsing `inferred` into `verified` would put
   a green tick on live allergen filtering. Never do this.
7. **`max_leg_minutes` stays in minutes.** Only the daily total moves to hours;
   a per-leg limit in hours would read worse, not better.
