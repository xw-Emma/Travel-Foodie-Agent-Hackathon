"""
Build data/foodie.sqlite from the staged CSVs in data/csv/.

THE CSVs ARE THE SINGLE SOURCE OF TRUTH.
This script only ever READS data/csv/ and WRITES data/foodie.sqlite.
It never regenerates or overwrites a CSV - the Tier 0 knowledge base and the
Tier 1/2 offline fallback must always be the same dataset.

Cities are auto-discovered: drop <city>_restaurants.csv + <city>_attractions.csv
into data/csv/ and they are picked up with no code change.

Run:  python data/seed.py
"""
from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "foodie.sqlite"
CSV_DIR = ROOT / "csv"

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# ----------------------------------------------------------------- knobs
# The nine allergens the dataset uses. Every venue gets an explicit
# true/false for all nine, so a missing key can never read as "safe".
CANONICAL_ALLERGENS = ("peanut", "tree_nut", "shellfish", "fish", "soy",
                       "gluten", "dairy", "egg", "sesame")

# Derived-field rules (deterministic, adjust here if you want different logic)
INDOOR_CATEGORIES = {"museum"}
INDOOR_KEYWORDS = ("atrium", "observation deck", "indoor", "dome theatre")
KID_FRIENDLY_MAX_PRICE_LEVEL = 2
NON_KID_ATTRACTION_CATEGORIES = {"activity"}


def stable_review_count(venue_id: str, lo: int = 80, hi: int = 2400) -> int:
    """
    Deterministic stand-in for review volume.

    WHY md5 and not hash(): Python's built-in hash() is salted per process,
    so it returns a different value on every run and on every machine. The
    local backend orders by `rating DESC, review_count DESC`, so an unstable
    tiebreaker means the itinerary changes between runs. md5 is identical
    everywhere, forever.
    """
    digest = hashlib.md5(venue_id.encode("utf-8")).hexdigest()
    return lo + (int(digest[:8], 16) % (hi - lo))


def split_list(value: str) -> list[str]:
    return [v.strip() for v in (value or "").split(";") if v.strip()]


def price_level(band: str) -> int:
    """'$'->1 ... '$$$$$'->5. NOT capped, so a 5-band venue stays distinct."""
    return len((band or "").strip()) or 1


def build_hours(open_time: str, close_time: str, closed_days: str) -> str:
    closed = set(split_list(closed_days))
    return json.dumps({
        d: ({"open": None, "close": None} if d in closed
            else {"open": open_time, "close": close_time})
        for d in DAYS
    })


def build_flags(allergens_present: str, dietary_options: str) -> str:
    present = set(split_list(allergens_present))
    flags = {f"{a}_risk": (a in present) for a in CANONICAL_ALLERGENS}
    for opt in split_list(dietary_options):
        flags[f"{opt}_options"] = True
    return json.dumps(flags)


def is_indoor(category: str, description: str) -> int:
    if (category or "").lower() in INDOOR_CATEGORIES:
        return 1
    d = (description or "").lower()
    return 1 if any(k in d for k in INDOOR_KEYWORDS) else 0


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(
            f"FATAL: {path} is missing.\n"
            "The CSVs are the source of truth - restore them from the kit "
            "before seeding. This script will not invent a dataset."
        )
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


# ----------------------------------------------------------------- schema
def create_schema(con: sqlite3.Connection) -> None:
    con.executescript("""
    DROP TABLE IF EXISTS restaurants;
    DROP TABLE IF EXISTS attractions;
    CREATE TABLE restaurants(
        venue_id TEXT PRIMARY KEY, city TEXT, name TEXT, cuisine TEXT,
        price_level INT, avg_meal_cost REAL, rating REAL, review_count INT,
        address TEXT, area TEXT, lat REAL, lon REAL, hours TEXT,
        dietary_flags TEXT, kid_friendly INT,
        meal_types TEXT, is_trap TEXT, description TEXT);
    CREATE TABLE attractions(
        venue_id TEXT PRIMARY KEY, city TEXT, name TEXT, category TEXT,
        cost REAL, rating REAL, lat REAL, lon REAL, hours TEXT,
        visit_duration_min INT, indoor INT, kid_friendly INT,
        address TEXT, area TEXT, slot_types TEXT, is_trap TEXT, description TEXT);
    """)


def load_restaurants(con, city: str, rows: list[dict]) -> int:
    out = []
    for r in rows:
        pl = price_level(r["price_band"])
        trap = (r.get("is_trap") or "").strip()
        out.append((
            r["venue_id"], city, r["name"], r["cuisine"],
            pl, float(r["cost_per_person"]), float(r["rating"]),
            stable_review_count(r["venue_id"]),
            r["neighbourhood"], r["neighbourhood"],
            float(r["lat"]), float(r["lon"]),
            build_hours(r["open_time"], r["close_time"], r["closed_days"]),
            build_flags(r["allergens_present"], r["dietary_options"]),
            1 if (pl <= KID_FRIENDLY_MAX_PRICE_LEVEL
                  and trap != "budget_buster") else 0,
            r["meal_types"], trap, r["description"],
        ))
    con.executemany(
        "INSERT INTO restaurants VALUES(" + ",".join("?" * 18) + ")", out)
    return len(out)


def load_attractions(con, city: str, rows: list[dict]) -> int:
    out = []
    for a in rows:
        cat = a["category"]
        out.append((
            a["venue_id"], city, a["name"], cat,
            float(a["cost_per_person"]), float(a["rating"]),
            float(a["lat"]), float(a["lon"]),
            build_hours(a["open_time"], a["close_time"], a["closed_days"]),
            int(a["duration_min"]),
            is_indoor(cat, a["description"]),
            0 if cat.lower() in NON_KID_ATTRACTION_CATEGORIES else 1,
            a["neighbourhood"], a["neighbourhood"],
            a["slot_types"], (a.get("is_trap") or "").strip(), a["description"],
        ))
    con.executemany(
        "INSERT INTO attractions VALUES(" + ",".join("?" * 17) + ")", out)
    return len(out)


def main() -> None:
    if not CSV_DIR.exists():
        raise SystemExit(f"FATAL: {CSV_DIR} not found.")

    cities = sorted(p.name[: -len("_restaurants.csv")]
                    for p in CSV_DIR.glob("*_restaurants.csv"))
    if not cities:
        raise SystemExit(f"FATAL: no *_restaurants.csv found in {CSV_DIR}.")

    if DB.exists():
        DB.unlink()
    con = sqlite3.connect(DB)
    create_schema(con)

    print(f"Seeding {DB.name} from {CSV_DIR}")
    totals = {}
    for slug in cities:
        city = slug.replace("_", " ").title()
        nr = load_restaurants(con, city, read_csv(CSV_DIR / f"{slug}_restaurants.csv"))
        na = load_attractions(con, city, read_csv(CSV_DIR / f"{slug}_attractions.csv"))
        totals[city] = (nr, na)
        print(f"  {city:12s} restaurants={nr:3d}  attractions={na:3d}")
    con.commit()

    # ---- self-verification: prove the graded traps survived seeding ----
    print("\nVerification (computed by SQL, not by this script's assumptions):")
    peanut = con.execute(
        "SELECT COUNT(*) FROM restaurants "
        "WHERE json_extract(dietary_flags, '$.peanut_risk') = 1").fetchone()[0]
    print(f"  peanut_risk = true : {peanut} restaurant(s)")

    print("  planted traps:")
    for table in ("restaurants", "attractions"):
        for row in con.execute(
                f"SELECT venue_id, name, is_trap FROM {table} "
                "WHERE is_trap != '' ORDER BY venue_id"):
            print(f"    {row[0]:6s} {row[1]:30s} {row[2]}")

    mondays = con.execute(
        "SELECT COUNT(*) FROM restaurants "
        "WHERE json_extract(hours, '$.mon.open') IS NULL").fetchone()[0]
    print(f"  closed Monday      : {mondays} restaurant(s)")
    print(f"  max price_level    : "
          f"{con.execute('SELECT MAX(price_level) FROM restaurants').fetchone()[0]}")
    con.close()
    print("\nDone. data/csv/ was not modified.")


if __name__ == "__main__":
    main()