# Phase F / G / H — Feasibility, Hierarchy, Conversation

**Target repo:** `xw-Emma/Travel-Foodie-Agent-Hackathon` (branch `master`, HEAD `310d117`)
**Audience:** AI coding assistant / IDE agent
**Written:** 2026-08-29
**Status of analysis:** Every number below was measured against the live APIs on
this machine, not estimated. Where a claim comes from a single run it says so.

Follows the same shape as `TIER2_TO_MAP_UI_IMPLEMENTATION_PLAN.md`: each phase
has CAUSE, EFFECT, CHANGES, ACCEPTANCE. Phases 0–5 and A–E of that document are
complete and pushed; this picks up from there.

---

## 0. Ground rules carried forward

These are not negotiable and every phase below inherits them:

1. **Tools only.** Agents reach data through tool calls, never from model memory.
2. **Constraints in code.** Allergen filters, budget arithmetic and quality gates
   are enforced in Python and re-checked, never left to a prompt.
3. **Bounded loop.** Critic returns slot IDs from a closed list, max 2 revisions.
4. **Never name a venue from a model.** Phase B made this structural: the intent
   schema has no venue field and unknown keys are dropped and reported. Phase H
   inherits that intact.
5. **Say what you could not do.** A silently dropped constraint is
   indistinguishable from a satisfied one. Four verification states exist for
   this reason (`verified` / `inferred` / `failed` / `unverifiable`).
6. **An agent, not a chatbot.** The kickoff deck's whole framing. Phase H adds a
   conversation that *gathers requirements*; the pipeline still plans, and the
   chat never answers with an itinerary of its own.

---

## 1. What the last live run actually did

The trigger for this plan was Gerardo's Lisbon prompt returning a plan **240%
over budget**, and a trace line reading
`enrich: skipped reading reviews: 58s of the 60s budget already spent`.

### 1.1 The budget overrun is not a bug

Reproduced with the same settings (Lisbon, 2 days, lunch+dinner, $100/person,
`min_rating=4.8`, live):

| | |
|---|---|
| Per-slot allowance for the party | **$50** ($100/person ÷ 2 days ÷ 2 meals × 2 people) |
| Cheapest venue in *any* candidate pool | **$60** (band 2, $30/person) |
| Affordable options per slot | **0, 0, 0, 0** |

Every slot fell through `best_candidate`'s documented last resort — "nothing
fits, take the cheapest so the slot is still filled and the budget check reports
the overage". `_repair_budget` then had nothing cheaper to swap to, because
`min_rating=4.8` had cut each pool to 3–4 candidates.

**The arithmetic is right and the request is genuinely unsatisfiable.** Three
things combined:

- The prompt says "full-day" (1 day); the form ran **2 days**, halving the
  per-meal allowance. At 1 day it is $50/person/meal and band 2 fits.
- Live costs are Google **price-band estimates**, not menu prices.
- We chose, in Phase C, to fill the slot and report rather than return nothing.

That choice is defensible but it is the wrong default here: a 240% plan is worse
than being told up front that the budget cannot buy what was asked for.

### 1.2 Where the 45–58 seconds goes

Instrumented live run, same request:

```
TOTAL 45.3s   (budget 60s)
LLM calls: 17   summed 88.3s = 195% of wall time
slowest single call: 9.3s      typical: 6–7s
critic iterations: 2, re-selected 3 slots
enrich: skipped reading reviews: 45s of the 60s budget already spent
```

**Concurrency is already working** — 88 s of model time fits in 45 s of wall
clock. The cost is **serial depth**, not parallelism:

```
planner -> restaurant selection -> critic -> revision selection -> critic
   ~7s            ~7s (parallel)     ~7s          ~7s (parallel)     ~7s
```

Five sequential round trips at 6–9 s each is a **~35 s floor** before a single
Google call. Enrichment would add a sixth (~7 s), which is why the 32 s deadline
in `_enrich_itinerary` skips it on almost every live run with a quality gate.

Two consequences worth naming:

- The demo is one slow gateway response away from breaching 60 s.
- The review-reading feature built in Phase C effectively never runs live.

### 1.3 A misleading message, again

When enrichment is skipped for time, the venue card reads *"Review text is
available but no LLM was reachable to read it."* The LLM was reachable; we chose
to skip. Same class of defect as the "Fuel iX was unreachable" message that once
hid a `TypeError` in our own serialisation (commit `3f277f0`).

### 1.4 The intent box was never used

The screenshots show Gerardo's paragraph pasted into **Starting point**, not into
the description box added in Phase B. The evidence is in the output: the
verification panel lists 9 requirements with **no Michelin / locals / chains
criteria carried over**, and `min_reviews = 0` although the prompt asks for
1000+.

The feature built for exactly this input is a collapsed expander labelled
*optional* at the top of the page. **This is a discoverability failure, and it is
the strongest single argument for Phase G and H.** No amount of extraction
quality helps if the box is invisible.

---

## 2. Phase F — Feasibility preflight and the latency floor

**Effort: ~3 h. Do this first: G and H both consume its output.**

### CAUSE

`best_candidate` decides affordability per slot, at selection time, with no view
of whether *any* combination can fit the total. When nothing fits it fills the
slot anyway (§1.1). The critic then spends both revision iterations — roughly
14 s of the run (§1.2) — trying to fix a constraint that no reselection can
satisfy, because the pool has nothing cheaper.

### EFFECT

Before: a plan 240% over budget, arrived at after burning two doomed revision
rounds, with the review-reading step skipped for lack of time.
After: infeasibility is detected *before* planning and reported with the numbers
that would make it feasible; the doomed revision loop is skipped; the time that
frees is enough for enrichment to run.

### CHANGES

**F1 — `src/feasibility.py` (new).** A pure function, no I/O of its own:

```python
def preflight(request: dict, pools: dict[str, list[dict]]) -> dict:
    """Can these constraints be satisfied at all?

    Returns {"feasible": bool, "cheapest_total": float, "per_slot": [...],
             "blocking": [...], "suggestions": [...]}.
    """
```

- For each meal slot, the cheapest candidate that clears every hard gate
  (allergens, quality, cuisine, hours).
- Sum them. That is the **cheapest possible plan** under these constraints.
- `feasible = cheapest_total <= budget_total`.
- `suggestions` are concrete and arithmetic, never vague: the budget that would
  work, the rating floor that would work, the day count that would work. Compute
  each by re-running the same sum with that one constraint relaxed.

Keep it a pure function over pools that were already fetched — this must add
**zero** API calls.

**F2 — call it before the critic loop** in `_run_tier2_async`, once candidates
exist and before routes. Record `meta["feasibility"]`.

**F3 — skip the doomed revision loop.** When `preflight` says infeasible *and*
the critic's issues are all budget-type, do not revise: nothing in the pool can
fix it. Log why, and ship with the shortfall recorded. Saves ~14 s.

> Careful: only skip when the blocking constraint is budget. Travel and hours
> issues are still fixable by reselection and must keep their iterations.

**F4 — fix the misleading enrichment message.** `dishes_for_venues(client=None)`
currently says "no LLM was reachable". Distinguish the two cases: pass a reason
through so a time-budget skip says *"skipped to stay inside the latency budget"*
and only a genuine client failure says unreachable.

**F5 — re-tune `ENRICH_LLM_DEADLINE_S`.** It is 32 s against a measured 45 s
plan; with F3 freeing ~14 s the plan lands near 31 s and one batched call (~7 s)
fits comfortably. Raise to ~45 s and re-measure. Do not raise it blind — the
acceptance below asserts the total.

**F6 — investigate per-agent model routing (measurement, then decide).**
`config.MODEL_ROUTING` already supports `FOODIE_MODEL_PLANNER` /
`FOODIE_MODEL_CRITIC` overrides. The planner and critic are mechanical
JSON-shaped steps. Measure a faster model on those two and keep it only if the
graded scenarios still pass. Potentially 7 s → 2 s on two of the five serial
hops. **Report the numbers; do not silently change the default model.**

### ACCEPTANCE

```bash
python eval/verify_all.py
python eval/acceptance.py --live
```

Plus a new `eval/verify_feasibility.py` asserting:

1. The Lisbon request from §1.1 is reported **infeasible before planning**, with
   `cheapest_total` ≈ $240 against a $200 budget.
2. Its suggestions include a budget that works and a rating floor that works,
   and each suggestion is arithmetically correct when re-checked.
3. A satisfiable request is reported feasible and behaves exactly as today.
4. An infeasible budget-only case does **not** consume both critic iterations.
5. A travel-constraint failure still does.
6. A live run with a quality gate completes enrichment rather than skipping it.
7. Total live time stays under `LATENCY_BUDGET_S` with margin — record the
   measured number in the suite output.

---

## 3. Phase G — Information hierarchy

**Effort: ~4 h. All in `app/ui_components.py` and the two Streamlit entrypoints.**

### CAUSE

Everything on the page carries the same visual weight: five tables in a row, the
most important of them (verification) the densest at six columns. Venue detail
is a table inside an expander inside a tab — three levels of nesting before a
fact. And the description box, the one input built for the prompt people
actually paste, is a collapsed expander marked *optional* (§1.4).

### EFFECT

Before: a correct plan that takes minutes to read, and an input people miss.
After: the plan's shape is legible in seconds, and the description box is the
obvious way in.

### CHANGES

**G1 — description first.** Move the description box out of its expander to the
top of the page as the primary input. Collapse the form beneath it as
*"Fine-tune the details"*, open by default only when there is no draft yet. This
is the fix for §1.4 and it is worth more than any styling change.

**G2 — a day at a glance.** One hero line per day above the table: stops, total
cost, total travel, average rating — data `meta["day_summary"]` already carries.

**G3 — verification as status, not a spreadsheet.** Replace the six-column table
with four counters (verified / inferred / failed / unverifiable) and a compact
list where only the non-verified rows carry their reason inline. The full table
moves behind *"Show every check"*.

**G4 — venue cards.** Replace table-in-expander-in-tab with
`st.container(border=True)`: name and the three facts that matter on the face
(rating + reviews, cost, neighbourhood-or-unknown), everything else — full facts,
review mentions, booking, runners-up — behind one expander per card.

**G5 — keep the honesty visible.** The inferred/unverifiable distinction must
survive the redesign. It is the most defensible thing in the UI and the easiest
to lose while making things look cleaner. Michelin stays visible as
UNVERIFIABLE, and live allergen filtering stays `inferred` with its caveat.

**G6 — both UIs.** Everything lands in `app/ui_components.py` so
`frontend/streamlit_app.py` gets it too. Phase 4.9 removed the drift between
them; do not reintroduce it.

> Scope note: this is a hierarchy and density problem, not a framework problem.
> A React rewrite is out of scope for the hackathon and the rubric does not ask
> for one. Streamlit with `st.container(border=True)`, columns and metrics is
> enough.

### ACCEPTANCE

Driven headlessly with `streamlit.testing.v1.AppTest`, plus one human pass:

1. The description box is visible without expanding anything.
2. A first-time user can state, from the top of the page and without expanding
   anything: how many stops, what it costs, whether it fits the budget, and how
   many requirements were not met.
3. Every state in `verification.STATE_ICONS` still renders, including
   `unverifiable`.
4. Both Streamlit apps and `FOODIE_DEMO_MODE=on` render with zero exceptions.

---

## 4. Phase H — Conversational requirements gathering

**Effort: ~5 h. Highest risk in this plan.**

### CAUSE

Phase B reads a description once and fills the form. It cannot ask about what is
missing: no starting point, a country where a city belongs, an ambiguous budget
basis, or a budget that F has just proved cannot buy what was asked for. The
user finds out only after a plan comes back wrong.

### EFFECT

Before: one-shot extraction, and every ambiguity resolved by a silent default.
After: a short conversation that resolves ambiguity before anything is searched,
and hands the pipeline a request that can actually be satisfied.

### CHANGES

**H1 — `src/agents/conversation.py` (new).** Multi-turn, but the same contract as
Phase B and the same two hard rules:

- It returns **structured fields plus questions**, never venue names.
- Everything it returns is re-validated in code (`intent.validate`), and every
  rejection is reported.

```python
def next_turn(client, history: list[dict], draft: dict,
              feasibility: dict | None) -> dict:
    """-> {"fields": {...}, "questions": [...], "ready": bool, "rejected": [...]}"""
```

**H2 — the question list is closed, and code decides when to ask.** Do not let
the model decide what is missing; that is a constraint check, and constraint
checks live in code (ground rule 2). Ask when:

| Trigger | Question |
|---|---|
| no origin | "Where are you starting from each day?" |
| `classify_city` returns `country` | "Portugal is a country — did you mean Lisbon?" |
| budget with no basis stated | "Is that per person or for the whole party?" |
| `preflight` says infeasible | "$25 per person per meal will not buy a 4.8-rated meal in Lisbon. Raise the budget to $X, drop the rating floor to 4.5, or plan one day instead?" |
| dates missing but hours matter | "Which dates? It lets me check opening hours." |

The last two are the reason F comes first.

**H3 — the chat gathers, the pipeline plans.** The conversation's only output is
a filled form plus a requirement list. It never returns an itinerary, never names
a venue, and planning still runs through `run_tier2`. **This is the line that
keeps the deck's "an agent, not a chatbot" framing true**, and it is worth saying
out loud in the demo.

**H4 — the user stays in control.** Every answer updates the visible form.
Nothing is planned until the user presses the button. Reuse the Phase B
back-fill discipline exactly: every widget keyed, `st.session_state` written
*before* the widget is built, then `st.rerun()`.

**H5 — degrade honestly.** No LLM reachable means the chat says so and the form
still works, exactly as `intent.extract` already does.

**H6 — `POST /chat` for the deployed UI**, mirroring `POST /intent`, so the key
stays server-side and the two UIs do not drift.

### ACCEPTANCE

New `eval/verify_conversation.py`:

1. A description with no origin produces an origin question.
2. "Portugal" produces a city confirmation question, not a silent acceptance.
3. An infeasible budget produces F's numbers in the question text.
4. A complete description produces `ready: True` and no questions.
5. Venue names in a model reply are dropped and reported (inherited from B).
6. Answering a question updates the form and asks nothing further about it.
7. With no LLM, the chat says so and the form still submits.
8. The chat never returns an itinerary key.

---

## 5. Sequencing

| Order | Phase | Why here |
|---|---|---|
| 1 | **F** | Fixes the reported defect, is the cheapest, and both G and H consume its output. Also buys back the ~14 s that lets enrichment run. |
| 2 | **G** | Highest rubric return (UX is 20 points; agent-trace visibility ≈ 7). Fixes the discoverability failure that made Phase B invisible. |
| 3 | **H** | Highest risk, and needs both F's feasibility numbers and G's layout to land well. |

**Minimum cut line.** If time runs short, F alone stops Gerardo's prompt from
producing an absurd result, and G alone makes the existing work legible. H is the
most impressive and the most likely to drift into the anti-pattern the deck warns
about — build it last, and only with H3 held firmly.

---

## Appendix A — Measured numbers (2026-08-29, live, this machine)

| Measurement | Value |
|---|---|
| Live run, Lisbon 2 days + `min_rating=4.8` | 45.3 s of a 60 s budget |
| LLM calls in that run | 17, summing 88.3 s of model time |
| Concurrency factor | ~2× (88 s of calls in 45 s wall) |
| Slowest single call | 9.3 s (typical 6–7 s) |
| Serial LLM round trips | 5 (plan → select → critic → revise → critic) |
| Estimated serial floor | ~35 s before any Google call |
| Per-slot allowance, that request | $50 for the party |
| Cheapest candidate in any pool | $60 |
| Affordable options per slot | 0 of 4 slots |
| Places details call | ~280 ms cold, ~1 ms cached |
| Cache after prune | 122 entries, 500 KB |

## Appendix B — Things not to "fix"

Carried forward from the previous plan's Appendix E, plus what this round added:

1. **Meal-type filtering** in `local_catalog.search_restaurants` is correct.
2. **`stable_review_count()`** must stay md5 — `hash()` is salted per process.
3. **`_plan_with_llm` passing `attractions_per_day=0`** is deliberate: that
   planner emits restaurant tasks only.
4. **Filling a slot when nothing is affordable** is deliberate — but F changes
   *when the user finds out*, not the fallback itself.
5. **`classify_city` returning `not_checked` offline** is deliberate: a wrong
   "that is a country" warning is worse than none.
6. **The four verification states.** Collapsing `inferred` into `verified` would
   put a green tick on live allergen filtering, which Google's data cannot
   support. Never do this.
