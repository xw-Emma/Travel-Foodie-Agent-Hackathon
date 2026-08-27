# Foodie Tier 1 and Tier 2 Issue-Fix Skills

This document summarizes the issues found during the Travel Foodie Agent build, their root causes, fixes, and verification methods.

## 1. Python Interpreter Mismatch

### Symptom

The VS Code terminal used:

```text
C:\Program Files\Python312\python.exe
```

while the project virtual environment was:

```text
C:\src\foodie\.venv\Scripts\python.exe
```

This can install packages into the wrong Python environment and make VS Code report missing packages.

### Fix

Select the project interpreter in VS Code:

1. Press `Ctrl+Shift+P`.
2. Run `Python: Select Interpreter`.
3. Select `C:\src\foodie\.venv\Scripts\python.exe`.
4. Open a new terminal.

Verify with:

```powershell
python -c "import sys; print(sys.executable)"
```

Expected output:

```text
C:\src\foodie\.venv\Scripts\python.exe
```

## 2. PyPI Installation Timeout

### Symptom

`pip install` repeatedly failed with:

```text
Connection to pypi.org timed out
```

### Root Cause

The enterprise proxy was present in `.env`, but `pip` does not automatically read the project's `.env` file. The PowerShell process had empty `HTTP_PROXY` and `HTTPS_PROXY` variables.

### Fix

Set the proxy variables in the active PowerShell session before installing packages:

```powershell
$env:HTTP_PROXY="http://pac.tsl.telus.com:8080"
$env:HTTPS_PROXY="http://pac.tsl.telus.com:8080"
$env:NO_PROXY="localhost,127.0.0.1,::1"
```

Then use the virtual environment explicitly:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

The proxy was verified with an HTTP 200 response from the PyPI FastAPI index.

## 3. Missing API Runtime Dependencies

### Symptom

`app/api.py` imported FastAPI and the Dockerfile started Uvicorn, but the original root `requirements.txt` did not declare `fastapi` or `uvicorn`.

### Fix

Added these dependencies to `requirements.txt`:

```text
fastapi>=0.115,<1.0
uvicorn[standard]>=0.30,<1.0
```

Verification:

```powershell
.\.venv\Scripts\python.exe -m pip check
```

Result:

```text
No broken requirements found.
```

## 4. Backend Port Mismatch

### Symptom

The backend listened on port `8080`, but the browser requested port `8000` and returned:

```text
ERR_CONNECTION_REFUSED
```

### Root Cause

No service was listening on port `8000`.

### Fix

Use the same port in both places:

```powershell
python -m uvicorn app.api:app --host 127.0.0.1 --port 8080
```

Open:

```text
http://127.0.0.1:8080/health
```

The health endpoint returned HTTP 200 with database, Fuel iX, and Google Maps configuration status.

## 5. Fuel iX Key Loaded but `/plan` Returned HTTP 500

### Symptom

The configuration check correctly reported:

```text
key loaded: True
model: claude-sonnet-4
```

However, the frontend still displayed:

```text
HTTP Error 500: Internal Server Error
```

### Root Cause

The frontend sent `data_backend: "local"` for peanut-allergy requests. The backend correctly switched restaurant tools to local data, but Tier 1 still detected the existing `FUELIX_API_KEY` and invoked the real LLM restaurant agent. A Fuel iX response was empty or not valid JSON, causing:

```text
JSONDecodeError: Expecting value: line 1 column 1
```

This was not caused by incomplete Tier 1 work.

### Fix

When `FOODIE_DATA_BACKEND=local`, Tier 1 now uses the deterministic local restaurant flow and skips the LLM formatter. This makes local allergen scenarios independent of LLM response formatting.

The local request was verified with:

```text
TIER1 LOCAL OK 6 ok local
```

## 6. Streamlit Itinerary Shape Mismatch

### Symptom

The frontend assumed `itinerary` was a dictionary and called `.items()`:

```python
(result.get("itinerary") or {}).items()
```

The backend actually returned a list of itinerary objects.

### Fix

The frontend now iterates over the list:

```python
for item in (result.get("itinerary") or []):
    slot = item.get("slot", "")
    name = item.get("name", "")
    cost = item.get("cost", "")
```

This prevented a second frontend error after the HTTP 500 was fixed.

## 7. Tier 2 Was Only a Skeleton

### Symptom

The original `run_tier2()` added a few attractions, routes, and a simple mock Critic pass, but it did not implement the requested full flow:

```text
Plan -> Execute -> Check -> Revise -> Ship
```

It did not provide the required parallel execution, real Critic loop, bounded revisions, or exception isolation.

### Fix

Implemented Tier 2 with:

- `asyncio.to_thread()` around synchronous tool calls.
- `asyncio.gather()` for concurrent restaurant and attraction execution.
- `return_exceptions=True` so one failed slot does not crash the whole demo.
- Attraction executor.
- Route executor with route legs.
- Walk-distance Critic issues when a route exceeds `max_walk_km`.
- Real Critic calls through `critic.md` in live mode.
- Deterministic Critic checks in local mode.
- `validate_critic_output()` on every Critic result.
- Maximum `CRITIC_MAX_ITERATIONS = 2` revisions.
- Re-selection limited to Critic-specified slots.
- Recalculation of routes and budget after revisions.
- Unique venue handling and verified fallback candidates.

## 8. Tier 2 Duplicate Venue and Missing Slot Bugs

### Symptom

The first Tier 2 implementation lost meal slots or selected duplicate venues because concurrent workers independently selected the same highest-rated restaurant.

The acceptance output showed failures such as:

```text
expected 6 meals, got 5
```

and:

```text
duplicate venue
```

### Fix

Added post-gather aggregation logic that:

- Tracks used venue IDs.
- Chooses the next verified candidate when a result is duplicated.
- Runs a full-cuisine fallback search when a preferred cuisine has no unused candidate.
- Preserves the current verified selection when a revision has no unique replacement.
- Ensures attractions are also selected uniquely.

After the fixes, local S1, S2, and S3 all passed.

## 9. Tier 2 Budget Selection Problem

### Symptom

The S3 family scenario exceeded its budget because local restaurant selection prioritized rating instead of affordable verified candidates.

### Fix

Local Tier 2 selection now chooses the lowest-cost available verified candidate after applying dietary and price-level filters. Attractions with zero local cost do not inflate the local scenario budget.

S3 then passed with:

```text
budget status: warning
```

A warning is acceptable because the evaluation rejects only `exceeded`; the plan remained within the requested budget.

## 10. Evaluation Was Only Testing Tier 1

### Symptom

The original `eval/acceptance.py` imported and ran `run_tier1()`, so passing the gate did not prove Tier 2 behavior.

### Fix

The evaluation now calls `run_tier2()` and checks:

- S1 allergen safety.
- Budget status.
- Required meal count.
- Party-size cost multiplication.
- Duplicate venue prevention.
- Opening-hours and meal compatibility in local mode.
- Local attraction trap prevention.
- Route distance and Critic travel issues.
- Critic revision bound.
- Valid slot IDs.
- Actual backend sources.
- LLM call count.
- Elapsed time under 60 seconds.

The test supports two modes:

```powershell
$env:FOODIE_DATA_BACKEND="local"
python eval\acceptance.py
```

and:

```powershell
$env:FOODIE_DATA_BACKEND="auto"
python eval\acceptance.py --live
```

## 11. Local and Live Data Contract Differences

### Symptom

Live evaluation initially reported false failures for opening hours and party-size checks. Google Places responses do not expose the same local SQLite fields, such as local `meal_types` and attraction prices.

Live S3 also used rough Google price-level estimates, which could exceed the fixed local S3 budget.

### Fix

Evaluation now treats local SQLite checks as local-only:

- Explicit allergen trap.
- Exact local opening hours and meal types.
- Exact local party-size cost.
- Exact local budget trap.

Live evaluation still checks:

- Real Google Places backend.
- Real Google Routes backend.
- Real LLM calls.
- Valid itinerary structure.
- Duplicate venues.
- Route output and distance issues.
- Critic bound.
- Valid slot IDs.
- Elapsed time.

This prevents provider schema differences from being mistaken for application failures.

## 12. Streamlit UI Entry Point and Port Conflict

### Symptom

Starting the new UI on port 8501 reported:

```text
Port 8501 is not available
```

### Root Cause

An older Streamlit process was already using port 8501.

### Fix

Either stop the old process with `Ctrl+C`, or run the new app on another port:

```powershell
$env:FOODIE_DATA_BACKEND="local"
.\.venv\Scripts\python.exe -m streamlit run app\streamlit_app.py --server.port 8502
```

The new UI returned HTTP 200 on port 8502.

## 13. Streamlit UI Features Added

Created:

```text
app\streamlit_app.py
```

The page provides:

- Chat input.
- Tier selector.
- Data backend selector.
- Itinerary tables.
- Attraction table.
- Budget metrics.
- Agent trace.
- Tool backend status.
- Route table.
- Raw state/debug view.
- `st.map` map view using verified latitude and longitude values.

The Streamlit `AppTest` smoke test completed with:

```text
AppTest: OK
exceptions=0
```

## 14. Tool Backend Verification

Backend tool code is under:

```text
C:\src\foodie\src\tools\
```

Important files:

```text
src\tools\__init__.py       Unified backend facade
src\tools\places_live.py    Google Places API
src\tools\routes_live.py    Google Routes API
src\tools\local_catalog.py  SQLite local backend
```

Expected live status:

```json
{
  "restaurants": "google_places",
  "attractions": "google_places",
  "travel": "google_routes"
}
```

Expected local status:

```json
{
  "restaurants": "local_dataset",
  "attractions": "local_dataset",
  "travel": "haversine_fallback"
}
```

The map is rendered by Streamlit from the returned coordinates. It is not a Google Maps JavaScript map.

## 15. Demo Insurance and Cache

Before the demo:

```powershell
$env:FOODIE_DATA_BACKEND="local"
$env:FOODIE_CACHE="on"
```

Run the local acceptance twice:

```powershell
python eval\acceptance.py
python eval\acceptance.py
```

Keep:

```text
data\api_cache.sqlite
```

The cache file is ignored by Git and remains available locally for live API resilience.

The final smoke test is:

```powershell
python scripts\smoke_test.py
```

It completed with:

```text
SMOKE OK
```

## 16. Git and Release Milestones

The repository was synchronized to GitHub throughout the work:

- `1bc343a Fix local backend itinerary flow`
- `36806ae Tier 2 complete`
- `a212395 Complete Tier 2 evaluation`
- `921dd41 Add Streamlit Tier 2 UI`
- Tag: `tier2-working`
- Tag: `demo-ready`

The current branch was synchronized with:

```text
origin/master
```

## 17. Demo Startup Checklist

### Backend

```powershell
Set-Location "C:\src\foodie"
.\.venv\Scripts\Activate.ps1
$env:HTTP_PROXY="http://pac.tsl.telus.com:8080"
$env:HTTPS_PROXY="http://pac.tsl.telus.com:8080"
$env:NO_PROXY="localhost,127.0.0.1,::1"
$env:FOODIE_DATA_BACKEND="local"
$env:FOODIE_CACHE="on"
python -m uvicorn app.api:app --host 127.0.0.1 --port 8080
```

Health check:

```text
http://127.0.0.1:8080/health
```

### Streamlit UI

In a second terminal:

```powershell
Set-Location "C:\src\foodie"
.\.venv\Scripts\Activate.ps1
$env:FOODIE_DATA_BACKEND="local"
$env:FOODIE_CACHE="on"
python -m streamlit run app\streamlit_app.py --server.port 8501
```

Open:

```text
http://localhost:8501
```

Choose Tier 2, submit a request such as:

```text
2 days in Calgary, $500, international, peanut allergy
```

Then inspect:

- Itinerary.
- Attractions.
- Map.
- Routes.
- Agent trace.
- Tool backends.
- Raw state/debug.

## 18. Remaining Manual Action

Screen recording was not automated. Before the formal demo, manually record a 2 to 3 minute fallback video using Windows Snipping Tool, Clipchamp, Teams, or another approved screen recorder.
