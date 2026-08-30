"""Build the offline dataset when a deploy starts without one.

`data/foodie.sqlite` is gitignored because it is DERIVED - the CSVs in
`data/csv/` are the source of truth and they are tracked. On a laptop that is
fine: the runbook tells you to run `python data/seed.py` once. On a hosted
deploy nobody runs anything, so the first request finds no database.

That matters more than it sounds. The local catalogue is not just the offline
demo path; it is what `FOODIE_DATA_BACKEND=auto` falls back to when Google is
unreachable. A deploy without it has no fallback at all - which is precisely
the situation the fallback exists for, so losing it silently in the cloud is
the worst place to lose it.

Seeding is derived, deterministic and takes well under a second, so building it
on startup when it is absent is safe. Leaving an existing database strictly
alone is not optional: `data/seed.py` unlinks and rebuilds, so calling it on a
warm machine would throw away nothing important but would still be a surprise,
and this module must never surprise anyone.
"""
from __future__ import annotations

import importlib.util
import sys

from . import config

SEED_SCRIPT = config.KIT_ROOT / "data" / "seed.py"


def ensure_local_database() -> tuple[bool, str]:
    """Seed the offline database if it is missing. Returns (built, message).

    Never raises. A deploy that cannot seed should say so and carry on with the
    live backend, not fail to start - the message is the honest report of which
    of those happened.
    """
    if config.DB_PATH.exists():
        return False, f"{config.DB_PATH.name} already present"
    if not SEED_SCRIPT.exists():
        return False, f"cannot seed: {SEED_SCRIPT} is missing"
    try:
        spec = importlib.util.spec_from_file_location("foodie_seed", SEED_SCRIPT)
        if spec is None or spec.loader is None:
            return False, f"cannot seed: {SEED_SCRIPT.name} is not importable"
        module = importlib.util.module_from_spec(spec)
        # Registered before exec so the script's own `from __future__` and any
        # relative lookups behave exactly as they do under `python data/seed.py`.
        sys.modules["foodie_seed"] = module
        spec.loader.exec_module(module)
        module.main()
    except SystemExit as stop:
        # seed.py reports fatal input problems this way (missing CSV directory).
        return False, f"seeding stopped: {stop}"
    except Exception as error:  # noqa: BLE001 - startup must survive anything
        return False, f"seeding failed: {type(error).__name__}: {error}"
    if not config.DB_PATH.exists():
        return False, "seeding ran but produced no database"
    return True, f"seeded {config.DB_PATH.name} from data/csv/"
