"""The preconditions a hosted deploy needs, checked before it is hosted.

A laptop and a deploy differ in one way that matters here: nobody runs
`python data/seed.py` on a deploy. `data/foodie.sqlite` is gitignored because
it is derived, so a fresh checkout has the CSVs and no database - and the
database is not only the offline demo, it is what FOODIE_DATA_BACKEND=auto
falls back to when Google is unreachable. Losing it in the cloud means losing
the fallback exactly where you cannot reach in and fix it.

The destructive check below removes the real database and restores it in a
finally block. It is safe to interrupt: the bootstrap rebuilds it on the next
start, and the rebuild is asserted here to be byte-identical.
"""
from __future__ import annotations

import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import bootstrap, config

fails = []


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {label}: {got!r}")
    if not ok:
        fails.append(f"{label}: got {got!r} want {want!r}")


def check_that(label, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f": {detail}" if detail else ""))
    if not ok:
        fails.append(f"{label} {detail}")


def section(title):
    print(f"\n=== {title} ===")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


section("the source the deploy actually gets")
tracked = {line.strip() for line in
           __import__("subprocess").run(
               ["git", "ls-files", "data"], cwd=ROOT, capture_output=True,
               text=True).stdout.splitlines() if line.strip()}
check_that("the CSVs a deploy seeds from are tracked",
           {"data/csv/calgary_restaurants.csv",
            "data/csv/calgary_attractions.csv"} <= tracked,
           f"{sorted(tracked)}")
check("the derived database is NOT tracked - it is rebuilt, not shipped",
      "data/foodie.sqlite" in tracked, False)
check_that("and the seed script that rebuilds it is tracked",
           "data/seed.py" in tracked)
check_that("requirements.txt is at the repo root where a host will look for it",
           (ROOT / "requirements.txt").exists())

section("a warm machine is left strictly alone")
check_that("this suite needs a database to protect", config.DB_PATH.exists(),
           "run python data/seed.py first")
before_hash = digest(config.DB_PATH)
before_mtime = config.DB_PATH.stat().st_mtime
built, message = bootstrap.ensure_local_database()
check("nothing was rebuilt", built, False)
check("and it says so", "already present" in message, True)
check("the file was not touched", config.DB_PATH.stat().st_mtime, before_mtime)

section("a cold deploy builds its own fallback")
backup = config.DB_PATH.with_suffix(".sqlite.verify-backup")
shutil.copy2(config.DB_PATH, backup)
try:
    config.DB_PATH.unlink()
    built, message = bootstrap.ensure_local_database()
    check("it seeded", built, True)
    check_that("and said what it did", "seeded" in message, message)
    check_that("the database exists now", config.DB_PATH.exists())
    check("the rebuild is byte-identical, so seeding is deterministic",
          digest(config.DB_PATH), before_hash)

    # The point of all of it: auto has something to fall back TO.
    token = config.set_backend_override("local")
    from src.tools import search_attractions, search_restaurants
    venues = search_restaurants(city="Calgary", meal="dinner", limit=5)
    places = search_attractions(city="Calgary", limit=5)
    config._backend_override.reset(token)
    check_that("the local backend answers after a cold seed",
               len(venues) == 5 and len(places) == 5,
               f"{len(venues)} restaurants, {len(places)} attractions")
finally:
    if not config.DB_PATH.exists():
        shutil.copy2(backup, config.DB_PATH)
    backup.unlink(missing_ok=True)

section("startup survives a broken seed rather than failing to boot")
original = bootstrap.SEED_SCRIPT
missing_db = config.DB_PATH.with_suffix(".sqlite.absent")
real_db = config.DB_PATH
try:
    bootstrap.SEED_SCRIPT = ROOT / "data" / "no_such_seed.py"
    config.DB_PATH = missing_db
    built, message = bootstrap.ensure_local_database()
    check("a missing seed script does not raise", built, False)
    check_that("it reports the reason instead", "cannot seed" in message, message)
finally:
    bootstrap.SEED_SCRIPT = original
    config.DB_PATH = real_db
check_that("and the real database is still where it was", config.DB_PATH.exists())

section("the deploy can be diagnosed from outside")
from app.api import health
report = health()
check_that("/health reports whether the bootstrap ran",
           "db_bootstrap" in report, f"{sorted(report)}")
check_that("/health reports whether the keys are set",
           {"fuelix_key_set", "maps_key_set"} <= set(report))

print()
if fails:
    print(f"{len(fails)} FAILURE(S):")
    for f in fails:
        print("  -", f)
    raise SystemExit(1)
print("ALL DEPLOY PRECONDITION CHECKS PASSED")
