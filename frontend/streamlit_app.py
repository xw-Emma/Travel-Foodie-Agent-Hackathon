"""Thin deployment UI that calls the FastAPI backend over HTTP.

This is not the primary local demo UI; use app/streamlit_app.py for the full
in-process Tier 1/Tier 2 experience.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

import streamlit as st

BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:8080").rstrip("/")
# For --no-allow-unauthenticated backends, set USE_ID_TOKEN=1 and run where gcloud works,
# or switch backend to allow unauthenticated for the hackathon demo only.
USE_ID_TOKEN = os.environ.get("USE_ID_TOKEN", "0") == "1"


def _auth_headers() -> dict:
    h = {"Content-Type": "application/json"}
    if USE_ID_TOKEN:
        import subprocess
        tok = subprocess.check_output(
            ["gcloud", "auth", "print-identity-token"], text=True).strip()
        h["Authorization"] = f"Bearer {tok}"
    return h


def parse_prefs(text: str) -> dict:
    """Lightweight NL → preference JSON (teams can replace with a Fuel iX call later)."""
    lower = text.lower()
    days = 2
    m = re.search(r"(\d+)\s*day", lower)
    if m:
        days = int(m.group(1))
    budget = 500.0
    m = re.search(r"\$?\s*(\d{2,5})", lower)
    if m:
        budget = float(m.group(1))
    allergies = []
    if "peanut" in lower:
        allergies.append("peanut")
    cuisines = ["international"]
    if "asian" in lower:
        cuisines = ["asian"]
    city = "Calgary"
    for c in ("calgary", "vancouver", "montreal"):
        if c in lower:
            city = c.title()
            break
    return {
        "city": city,
        "days": days,
        "budget_total": budget,
        "party_size": 2,
        "cuisines": cuisines,
        "allergies": allergies,
        "tier": 1,
        "data_backend": "local" if allergies else "auto",
    }


def call_plan(prefs: dict) -> dict:
    req = urllib.request.Request(
        BACKEND_URL + "/plan",
        data=json.dumps(prefs).encode("utf-8"),
        method="POST",
        headers={**_auth_headers(), "User-Agent": "foodie-frontend/1.0"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


st.set_page_config(page_title="Travel Foodie Agent", layout="wide")
st.title("Travel Foodie Agent - Deployment UI")
st.caption("Thin HTTP client for the FastAPI backend. For the full local Tier 2 UI, run app/streamlit_app.py.")
st.caption(f"Backend: {BACKEND_URL}")

if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

prompt = st.chat_input("e.g. 2 days in Calgary, $500, international, peanut allergy")
if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    prefs = parse_prefs(prompt)
    with st.chat_message("assistant"):
        with st.spinner("Planning…"):
            try:
                result = call_plan(prefs)
                lines = ["**Itinerary**"]
                for item in (result.get("itinerary") or []):
                    slot = item.get("slot", "") if isinstance(item, dict) else ""
                    name = item.get("name") if isinstance(item, dict) else item
                    cost = item.get("cost", "") if isinstance(item, dict) else ""
                    lines.append(f"- `{slot}`: {name}  ${cost}")
                lines.append(f"\n**Budget:** `{result.get('budget')}`")
                lines.append(f"\n**Backends:** `{result.get('tool_backends')}`")
                if result.get("trace"):
                    lines.append("\n**Trace**")
                    for t in result["trace"]:
                        lines.append(f"- {t}")
                reply = "\n".join(lines)
            except Exception as exc:  # noqa: BLE001
                reply = f"Error calling backend: `{exc}`"
        st.markdown(reply)
    st.session_state.messages.append({"role": "assistant", "content": reply})
