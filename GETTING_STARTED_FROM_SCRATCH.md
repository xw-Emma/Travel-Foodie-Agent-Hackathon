# Traveling Foodie Agent — Getting Started From Scratch

**For:** Emma (Xu Wang) · first agentic AI build **Assumes:** you can read code but have not built an LLM application before **Companions:** `PROJECT_CONTEXT.md` (facts) · `BUILD_AND_DEPLOY_RUNBOOK.md` (ops reference) · `DECK_REVIEW.md` (kickoff deck) **Classification:** TELUS Internal

> This document is the *learning path*. The runbook is the *reference manual*. Read this one first, front to back. Reach for the runbook when you need the exact command or the troubleshooting table.

---

# PART 0 — The five concepts you need (15 minutes)

Everything in this project is built from five ideas. None of them are hard. If these five click, the rest is plumbing.

### 1\. An LLM call is just an HTTP POST. The model has no memory.

Fuel iX is an HTTP endpoint. You send it a JSON body, it sends back JSON.

```
POST https://api.fuelix.ai/v1/chat/completions
Authorization: Bearer <your key>

{ "model": "claude-sonnet-4",
  "messages": [ {"role":"system", "content":"You are a trip planner."},
                {"role":"user",   "content":"Split this into meal slots: ..."} ] }
```

**The most important consequence:** the model remembers *nothing* between calls. Every call must carry everything it needs. When you see people say "the agent remembers the plan," what they mean is that *your code* passes the plan back in.

That is what `TripState` is for (concept 4).

### 2\. A "tool call" is the model asking your code to do something

Normally the model answers with text. But you can send it a list of *tools* — function descriptions. Then instead of answering, the model can reply:

> "Don't answer yet. Call `search_restaurants(city='Calgary', meal='dinner')` and tell me what it returns."

Your code runs the real function, sends the result back, and the model continues. That round trip is the **tool loop**, and it is already written for you in `src/fuelix_client.py::run_tool_loop`.

**Why this is the whole game:** the model never invents a restaurant. It asks your code, your code queries Google Places or SQLite, and the model chooses among *real rows*. That distinction is worth 40 of the 100 rubric points, and it is the difference between an agent and a chatbot.

```
        ┌─────────┐   "call search_restaurants"   ┌──────────────┐
        │  Model  │ ────────────────────────────► │  Your code   │
        │(Fuel iX)│ ◄──────────────────────────── │ (src/tools/) │
        └─────────┘   [60 real rows of data]      └──────┬───────┘
                                                         │
                                            Google Places API  or  SQLite
```

### 3\. "Agentic" means your code runs a loop, not the human

A chatbot: you ask → it answers → *you* notice the mistake → you ask again.

An agent: your code plans → executes → **checks its own work** → revises → ships.   
The human is not in the loop.

Concretely, in this project:

```
Planner    → split "2 days in Calgary" into 6 meal slots + constraints
Executors  → for each slot, find a real restaurant (tool calls)
Budget     → add up the money            (pure Python, no LLM)
Critic     → re-read the finished plan: any allergen leaks? over budget?
             anything closed that day?  → name the slots to redo
Formatter  → write it up with the reason behind every pick
```

The Critic is the part that makes it an agent. Everything else is a pipeline.

### 4\. `TripState` is a shared clipboard

One Python object gets passed to every stage. Each stage reads it, writes *its own part*, and hands it back.

```py
TripState(
    request    = {...},   # what the user asked for
    plan       = {...},   # Planner writes this
    candidates = {...},   # Executors write this: slot -> [real venues found]
    routes     = [...],   # Route agent writes this
    budget     = {...},   # check_budget writes this
    critic     = {...},   # Critic writes its verdict here
    itinerary  = [...],   # the final picks
    trace      = [...],   # every step, in order  <- this is your demo
    meta       = {...},   # timing, which backend actually ran
)
```

Two things this buys you:

- **You can add an agent without rewriting the pipeline.** The Route agent only touches `routes`. Nothing else needs to know it exists.  
- **`trace` is your proof.** It is a list of "who did what, in order." Putting it on screen is how you show a judge this is an agent and not a chat window.

### 5\. Money is arithmetic, not language. Never ask an LLM to do Math.

`check_budget()` is fifteen lines of pure Python. It stays that way. LLMs make arithmetic errors, and an arithmetic error in a budget is a wrong answer you cannot detect. Same principle for the allergen filter: it is a `WHERE` clause, not a polite request in a prompt.

### Vocabulary

| Term | What it means here |
| :---- | :---- |
| **Fuel iX** | TELUS's LLM gateway. The *only* approved way to reach an LLM. OpenAI-compatible HTTP. |
| **Places API (New)** | Google's restaurant/attraction search. Returns real venues with ratings and hours. |
| **Routes API** | Google's travel-time/distance between two points. Replaces the old Directions API. |
| **Orchestrator** | `src/orchestrator.py` — the code that runs the stages in order. Your "main". |
| **Executor** | An agent that fills one slot (one meal, one attraction). |
| **Critic** | The agent that reviews the finished plan and names what to redo. |
| **Slot** | One thing to fill: `day1.breakfast`, `day2.attraction1`. A **closed list** — see below. |
| **Tool / tool layer** | `src/tools/` — the only place that talks to Google or SQLite. |
| **Backend** | Where data came from: `live` (Google), `local` (SQLite), `auto` (try live, fall back). |
| **Trap** | A venue deliberately planted in the dataset to catch a lazy agent. |
| **Mock mode** | No `FUELIX_API_KEY` set → the kit runs with fake deterministic LLM output. Great for learning the shape before spending tokens. |

**On the closed slot list:** slot IDs come from `src/state.py::slot_ids()` and are exactly `day1.breakfast … day2.attraction1`. When the Critic says "fix `day2.dinner`", your code checks that string against that list before acting on it. If a sloppy model says `"dinner day 2"`, you reject it and re-ask. Skip this and your demo either re-plans the wrong slot or loops forever on stage.

---

# PART 1 — What you physically need

## The short answer

| Thing | Needed? | Why |
| :---- | :---- | :---- |
| Your TELUS laptop \+ VS Code | **Required** | where you build |
| Python 3.10+ | **Required** | the kit is stdlib-only at its core |
| The starter kit folder | **Required** | \~90% of the code is already written |
| Fuel iX API key | **Required** | the LLM. Self-serve, takes 2 minutes |
| Google Maps API key (from a Lab project) | **Required** | live venue data |
| Proxy env vars \+ VPN | **Required** | nothing reaches the internet without them |
| `git` (local only) | **Strongly recommended** | tag a working version so you can roll back |
| GitHub / remote repo | **Optional — and be careful** | see below |
| A GCP project | **You need one for the API key.** You do *not* need it for hosting. |  |
| Cloud Run / any server | **Optional** | the demo runs on your laptop |
| A separate frontend | **No** | see below |

## "Do I need a frontend and a backend?" — No. And this is the most important thing to understand.

You are building **one Python process**. Streamlit is not a frontend that talks to a backend; Streamlit *is* both. It serves the web page and runs your agent code in the same process, in the same memory.

```
   ┌────────────────────────────────────────────────────────┐
   │  ONE python process on your laptop                     │
   │                                                        │
   │   streamlit  ──calls──►  run_tier2(request)            │
   │   (the web UI)              │                          │
   │        ▲                    ├─► Fuel iX      (HTTPS)   │
   │        │                    ├─► Places API   (HTTPS)   │
   │        │                    ├─► Routes API   (HTTPS)   │
   │        │                    └─► data/foodie.sqlite     │
   │        │                              (local file)     │
   └────────┼───────────────────────────────────────────────┘
            │
      your browser at http://localhost:8501
```

There is no REST API to write, no React app, no database server, no Docker needed. `streamlit run app/streamlit_app.py` and you are done.

**When would you split them?** If this became a real product serving many users you would put the agent behind a FastAPI service and give it a proper web frontend. That is weeks of work and it earns **zero extra rubric points**. Do not do it for the hackathon. If someone asks at the demo, the honest answer is: "one process today; the tool facade in `src/tools/` is already the seam where you'd split it."

## About GitHub — read this before you push anything

Your code is **TELUS Internal** and your `.env` contains live API keys.

- ✅ **`git init` locally.** Do this. It costs nothing and lets you `git tag tier1-working` and roll back when you break something at 11pm.  
- ⚠️ **A remote repo** — only on a TELUS-approved host (internal GitHub Enterprise / GitLab), and confirm with your team what's allowed. **Never public GitHub.**  
- 🚫 **Never commit `.env`.** The kit's `.gitignore` already covers it — verify with `git status` before your first commit that `.env` is not listed.

For a solo four-day build, local git with tags is genuinely enough.

## Your folder layout

Keep **two** folders. This matters more than it sounds.

```
Desktop\
├── Travel-Foodie-Agent-Reference\     ← YOUR build. Goes as far as you can push it.
└── Travel-Foodie-Agent-Starter\       ← the clean kit you hand to the 3 teams.
```

Why: you are the organizer. If your fully-built reference implementation leaks into the kit the teams receive, you have handed them the answers and destroyed the exercise. Two folders, and you only ever zip the second one.

---

# PART 2 — The build, phase by phase

Eight phases. Each has a **Target** (what exists at the end), a **Why** (what you will understand), **Do this**, **Test**, and **Done when**.

Do not skip phases. Each one isolates exactly one new thing that can break.

---

## Phase A — Run it with no keys at all (30 min)

**Target:** the pipeline runs end-to-end on your laptop with zero credentials. **Why:** you learn the shape of the thing before any network variable exists. If something breaks later, you know it is not the code.

**Do this**

```
cd $HOME\Desktop
# copy the kit here, then:
Rename-Item Travel-Foodie-Agent-Hackathon-Starter Travel-Foodie-Agent-Reference
cd Travel-Foodie-Agent-Reference

python -m venv .venv
.\.venv\Scripts\Activate.ps1     # if blocked: Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
python --version                  # must be 3.10+

git init
git add -A
git commit -m "Baseline: kit as shipped"
```

**⚠️ STOP — do not run `python data/seed.py` yet.** The `seed.py` currently in the kit destroys the dataset (see `PROJECT_CONTEXT.md` §7.1). Replace it with `seed_FIXED.py` first:

```
Copy-Item ..\seed_FIXED.py data\seed.py -Force
python data\seed.py
```

You should see `restaurants= 60  attractions= 25` and all five traps listed. If you see 8 and 4, you are still on the broken version.

Now run the pipeline:

```
python -m src.orchestrator
```

**Test / Done when** you see:

- `allergen leaks in candidates (MUST be empty): []`  
- a budget status line  
- `good -> (True, [])` and `bad -> (False, ['dinner day 2'])` from the slot guard

**What just happened:** no LLM was called. `config.MOCK_MODE` was `True` because you have no `FUELIX_API_KEY`, so the Planner and Formatter were deterministic stand-ins. The *data* was real (SQLite). You have proven the plumbing works.

**Spend ten minutes here reading `src/orchestrator.py` top to bottom.** It is \~150 lines and it is the whole program. Everything you do later is replacing one of its four steps with something smarter.

---

## Phase B — Turn on live Google data (45 min)

**Target:** real Calgary restaurants from Google, not from SQLite. **Why:** this is the network/proxy/credentials phase. Isolate it.

**Do this**

1. **Proxy** (nothing works without this):

```
$env:HTTP_PROXY  = "http://pac.tsl.telus.com:8080"
$env:HTTPS_PROXY = "http://pac.tsl.telus.com:8080"
$env:NO_PROXY    = "localhost,127.0.0.1,::1"
python -c "import urllib.request;print(urllib.request.getproxies())"
```

That last line must print the pac host. If it prints `{}`, nothing else in this phase will work.

2. **Get your Google Maps key** from a Lab project, put it in `.env`:

```
GOOGLE_MAPS_API_KEY=...
FOODIE_DATA_BACKEND=auto
FOODIE_CACHE=on
```

3. **Check the key restrictions in the GCP console** — this is where most 403s come from:  
     
   - Places API **(New)** and Routes API both enabled  
   - Application restriction \= **None** (not "HTTP referrers" — server-side calls send no referrer and get blocked)  
   - API restriction narrowed to those two APIs

   

4. Add `scripts/preflight.py` from the runbook (Phase 1.5) and run it.

**Test**

```
python scripts\preflight.py
```

Four green lines: proxy, Fuel iX reachable, Places search, Routes leg.

**Done when** `python -m src.orchestrator` shows `tool_backends: {'restaurants': 'google_places', ...}` and the restaurant names are ones you have not seen in the CSV.

**If it breaks:** the runbook's troubleshooting table maps every HTTP code to a cause. Do not guess — read the error string, the Places API tells you exactly what is wrong.

---

## Phase C — Your first real LLM call: the Planner (1 hour)

**Target:** one Fuel iX call that turns the request into a JSON plan. **Why:** you learn what an LLM call actually returns and how to make it give you strict JSON. One call, one stage, nothing else moving.

**Do this**

Get a Fuel iX key (`app.fuelix.ai` → dots → API → Dev Portal → create project), put it in `.env` as `FUELIX_API_KEY`, then write the Planner as shown in the runbook Phase 2.1.

The three rules that matter:

1. **Tell it the closed slot list in the prompt.** Do not hope it guesses the format. Pass `slot_ids(days)` in verbatim.  
2. **Tell it explicitly not to pick venues.** The Planner splits and assigns constraints. If it names a restaurant, you have built a chatbot with extra steps.  
3. **Validate the output before using it.** Filter `plan["slots"]` against the valid list. Never trust model output structurally.

**Test** — before wiring it in, run it alone in a scratch file and print the raw result:

```py
from src.fuelix_client import FuelixClient
c = FuelixClient()
msg = c.chat(system="Reply with JSON only.", user='Return {"hello": "world"}')
print(msg)
print(c.telemetry)     # {'llm_calls': 1, ...}
```

Seeing `llm_calls: 1` and a real response is the milestone. *Now* build the Planner.

**Done when** `st.plan` contains one entry per slot, each carrying `budget_cap` and `exclude_flags`, and no venue names appear anywhere in it.

**Expect this to be fiddly.** Models wrap JSON in ```` ```json ```` fences and add chatty preambles. `parse_json_reply()` already handles both. If you still get errors, print the raw `content` and look at what it actually said — nine times out of ten the prompt was ambiguous, not the parser.

---

## Phase D — Your first tool-calling agent (2 hours) ← the real milestone

**Target:** the model calls `search_restaurants` itself and *chooses* among the rows it gets back. **Why:** this is the concept that makes the whole thing agentic. Everything before was a warm-up.

**Do this** — use `run_tool_loop` as shown in the runbook Phase 2.2.

Then add the guard that matters:

```py
returned_ids = {c["venue_id"] for c in st.candidates[slot]}
if pick["venue_id"] not in returned_ids:
    st.log("restaurant", f"{slot}: model returned an off-list venue — falling back")
    pick = st.candidates[slot][0]
```

**This one check kills the entire class of hallucinated-venue failures, which score zero.** Write it the same hour you write the tool loop, not later.

**Test** — put a print inside the tool implementation so you can *see* the model calling it:

```py
def search_restaurants(city, meal, **kw):
    print(f"  [TOOL] search_restaurants(city={city!r}, meal={meal!r}, {kw})")
    ...
```

Watching that line appear, unprompted, because the model decided it needed data — that is the moment the concept lands. Keep the print; make it a `st.log()` and it becomes your demo trace.

**Done when** you see tool calls in the console, the chosen venue is always one that was actually returned, and `r008 Peanut Garden Thai` never appears in an S1 plan.

---

## Phase E — Close the loop: Tier 1 done (1 hour)

**Target:** Formatter \+ telemetry. Tier 1 complete.

**Do this** — add the Formatter (runbook 2.4) and fill in `st.meta` (runbook 2.5).

**Test against the actual Tier 1 bar:**

| Requirement | Check |
| :---- | :---- |
| ≥ 4 real Fuel iX calls | `st.meta["llm_calls"] >= 4` |
| ≥ 1 live Places call | `st.meta["tool_backends"]["restaurants"] == "google_places"` |
| allergen enforced in code | `exclude_flags` passed at the tool layer |
| budget in pure Python | `check_budget` untouched |
| under 60 s | `st.meta["elapsed_s"] < 60` |

**Done when** all five pass. Then:

```
git add -A; git commit -m "Tier 1 complete"; git tag tier1-working
```

**Tag it.** This is your fallback demo. Everything after this point is upside.

---

## Phase F — Tier 2: parallel \+ Critic (3 hours)

**Target:** slots filled in parallel, plus Attraction and Route agents, plus a bounded Critic revision loop. **Why:** the Critic is the single highest-value component in the project.

**Do this** — runbook Phase 3, in this order:

1. **Parallelize** with `asyncio.to_thread` \+ `asyncio.gather`. Six sequential LLM calls will blow the 60-second budget; six parallel ones will not. Use `return_exceptions=True` so one bad slot degrades instead of crashing.  
2. **Attraction \+ Route agents.** Make the Route agent *act*: legs over `max_walk_km` become Critic issues. A Route agent that only measures is a report; one whose measurement changes the plan is an agent.  
3. **Critic loop, max 2 iterations**, always through `validate_critic_output()`.

**Test**

```
$env:FOODIE_DATA_BACKEND = "local"
python eval\acceptance.py
```

**Done when** S1–S3 pass, the Critic approves or bails within 2 iterations, and `elapsed_s < 60` in `auto` mode.

**Be able to answer these four out loud** — a judge will ask all of them:

| Question | Your answer |
| :---- | :---- |
| What if the Critic loops forever? | `CRITIC_MAX_ITERATIONS = 2`, then it ships |
| What if it invents a slot name? | `validate_critic_output` rejects it against the closed list, re-asks once, then bails to *approved* |
| Does it re-plan everything? | No — only the slots the Critic named. That's what `TripState` buys |
| What if the API is down? | `FOODIE_DATA_BACKEND=local` \+ warm cache, and `tool_backends` records the fallback honestly |

---

## Phase G — Streamlit UI (2 hours)

**Target:** a chat window, an agent-trace panel, a map, a budget meter. **Why:** 20 rubric points, and Gerardo will expect a chat window.

```
pip install --proxy http://pac.tsl.telus.com:8080 streamlit pandas
streamlit run app\streamlit_app.py
```

Full app code is in the runbook Phase 4\. Two things that are not obvious:

- **Open your demo on the Agent Trace tab, not the itinerary.** The itinerary looks like a chatbot output. The trace is the thesis of the entire event.  
- **Leave `tool_backends` visible on screen.** It is your proof of live-API usage and 40 points ride on that claim.

**Done when** you can type a request in a browser and get an itinerary, trace and map in under a minute.

---

## Phase H — Rehearse and insure (1 hour)

**Target:** you can demo this with no internet and no luck.

1. **Warm the cache** — run every scenario twice with `FOODIE_CACHE=on`, then do not delete `data/api_cache.sqlite`.  
2. **Prove local mode** — `$env:FOODIE_DATA_BACKEND="local"` and run the full UI. Personally watch it work with no network.  
3. **Record a 2–3 minute screen capture** of a clean run. If anything fails live, you play it and keep talking.  
4. **Tag it** — `git tag demo-ready`.

---

# PART 3 — Using Claude in VS Code (this is a real skill)

"Vibe coding is your friend" is one of your golden rules. Here is how to actually do it well on this project.

**Give it the contract, not the vibe.** Bad: *"add a critic agent."* Good:

> Read `src/state.py`, `src/orchestrator.py` and `src/fuelix_client.py` first. Add a `run_critic_loop(client, st, request)` function to `src/orchestrator.py`. It must call the Critic via `FuelixClient.chat` using `prompts/critic.md`, pass every response through the existing `validate_critic_output()` before acting on it, cap at `config.CRITIC_MAX_ITERATIONS`, and re-plan only the slots the Critic names. Do not change any function signature in `src/tools/`.

**Always tell it to read the existing files first.** Otherwise it invents a parallel set of helpers that duplicate `src/tools/`.

**Three standing rules to paste at the start of a session:**

1. All LLM traffic goes through `src/fuelix_client.py`. Never add an SDK or a new HTTP client.  
2. All data access goes through `src/tools/` (the facade). Never call Google or SQLite directly from an agent.  
3. Never put a key in code. Everything comes from `.env` via `src/config.py`.

**Make it show you the tool calls.** When something behaves oddly, ask it to add `st.log()` lines rather than explain — then run it and read the trace. On this project the trace is almost always the answer.

**Do not let it install packages to solve a problem.** The kit is stdlib-only on purpose: on a locked-down laptop behind TLS inspection, a new dependency is a new failure mode. If Claude suggests `requests` or `langchain` to fix something, push back — `urllib` already works.

---

# PART 4 — What will bite you

### The three defects still in the kit (verified, not theoretical)

I ran these against the real dataset. All three are live right now:

1. **`data/seed.py` destroys the dataset on first run.** Replace it with `seed_FIXED.py` before anything else.  
2. **`cuisine="international"` matches zero rows.** The dataset has 30 cuisines; none of them is "international" — which is the cuisine S1 and your deck both specify. It silently falls back to unfiltered results.  
3. **No de-duplication.** `run_tier1` takes `cands[0]` per slot, and the ranking is `rating DESC`. Result on the real data: **Mount Royal Fine Dining for breakfast, lunch and dinner, twice — $792 against a $500 budget.** That is what the kit does today, out of the box.

Fixes for 2 and 3 are in the chat summary and take about 20 minutes. Note that your change-list item §2.2a ("remove the soft fallback") is **unsafe on its own** — removing it without fixing the cuisine map makes S1 return nothing at all.

### Cost and quota

- The kit's field mask requests `rating`, `userRatingCount`, `priceLevel`, which puts Places Text Search into the **Pro** SKU band. `FOODIE_CACHE=on` is doing real work — leave it on.  
- **Set a billing alert on each Lab project before the event.** Three teams iterating with caching off can move real money.  
- Fuel iX tokens: the Critic loop can triple your call count. `CRITIC_MAX_ITERATIONS = 2` is a cost bound as well as a demo-safety bound.

### Security

- Keys live only in `.env`. Never in code, never in a notebook, never on a slide, never in a screenshot. Check your screen-share before you demo.  
- Public data only. No SAM, no CEVA, no internal RF systems.  
- Before your first commit: `git status` and confirm `.env` is not listed.

### Time

- **Golden rule \#3 applies to you too.** Expect 30–50% of your ambition. Phases A–E are the deliverable; F–H are the upside.  
- **Tag a working version at the end of every phase.** The worst hackathon failure mode is having something that worked two hours ago and no way back.  
- Freeze early. You are also running the event — you cannot afford to be debugging on Day 3\.

### Two things specific to your position

**You are the organizer, not a competitor.** Two consequences:

- Keep your reference build in a separate folder and never zip it into the kit.  
- If your build ends up better than every team's, do not show it as a benchmark at the demo. Show it at kickoff as proof it is possible, then get out of the way.

**Your judges are business-side managers.** Kathleen (Property & Program) and Rogelio (implementation) were not at last year's hackathon and will not evaluate an architecture diagram. What lands with them is the **PROOF** row: zero allergen violations, under budget, under 60 seconds. Build the demo around that, and coach the teams to do the same.

### The one technical trap that catches everyone

The descriptions in the staged CSVs contain commas. If you (or Claude) ever write a custom loader using `line.split(",")` instead of the `csv` module, you will get silently shifted columns and spend an hour wondering why the ratings are wrong. Always use `csv.DictReader`.  
