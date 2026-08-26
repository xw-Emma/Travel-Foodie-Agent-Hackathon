#!/usr/bin/env python3
"""Preflight checks for proxy, Fuel iX, Google Places (New), and Routes."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import config  # noqa: E402

OK = "  OK  "
FAIL = " FAIL "


def line(label: str, passed: bool, detail: str = "") -> bool:
    status = OK if passed else FAIL
    print(f"[{status}] {label:28s} {detail}")
    return passed


def fuelix_models() -> tuple[bool, list[str], str]:
    if not config.FUELIX_API_KEY:
        return False, [], "FUELIX_API_KEY is not set"
    request = urllib.request.Request(
        config.FUELIX_BASE_URL.rstrip("/") + "/models",
        headers={"Authorization": f"Bearer {config.FUELIX_API_KEY}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload: dict[str, Any] = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - report the failing gate
        return False, [], str(exc)[:180]

    models = payload.get("data") or []
    model_ids = [str(model.get("id")) for model in models if model.get("id")]
    return True, model_ids, f"{len(model_ids)} models"


def main() -> int:
    results: list[bool] = []

    proxies = urllib.request.getproxies()
    results.append(line(
        "proxy env",
        bool(proxies.get("https")),
        proxies.get("https", "no https proxy in environment"),
    ))
    results.append(line(
        "loopback no_proxy",
        "localhost" in proxies.get("no", "")
        and "127.0.0.1" in proxies.get("no", "")
        and "::1" in proxies.get("no", ""),
        proxies.get("no", "no loopback exclusions"),
    ))

    models_ok, model_ids, models_detail = fuelix_models()
    results.append(line("fuel ix /models", models_ok, models_detail))
    model_ok = models_ok and config.DEFAULT_MODEL in model_ids
    sample = ", ".join(model_ids[:5]) or "no models returned"
    results.append(line(
        f"model {config.DEFAULT_MODEL}",
        model_ok,
        "available" if model_ok else f"not available; sample: {sample}",
    ))

    try:
        from src.tools import places_live

        restaurants = places_live.search_restaurants("Calgary", "dinner", limit=2)
        results.append(line(
            "places (new) searchText",
            bool(restaurants),
            restaurants[0]["name"] if restaurants else "empty result",
        ))
    except Exception as exc:  # noqa: BLE001 - report the failing gate
        results.append(line("places (new) searchText", False, str(exc)[:180]))

    try:
        from src.tools import routes_live

        leg = routes_live.estimate_travel(
            51.0447, -114.0631, 51.0466, -114.0592, mode="walk"
        )
        results.append(line(
            "routes computeRoutes",
            True,
            f"{leg['km']} km / {leg['minutes']} min",
        ))
    except Exception as exc:  # noqa: BLE001 - report the failing gate
        results.append(line("routes computeRoutes", False, str(exc)[:180]))

    print()
    if all(results):
        print("PREFLIGHT GREEN — you can build.")
        return 0

    print("PREFLIGHT RED — fix the FAIL lines above before building.")
    print("Offline escape hatch: set FOODIE_DATA_BACKEND=local")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
