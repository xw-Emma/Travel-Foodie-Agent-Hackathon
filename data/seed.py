"""
Build data/foodie.sqlite — the mandatory offline fallback + graded trap dataset.

Planted edge cases (Critic / acceptance must catch these):
  - r2 "Peanut Palace" — high rating, peanut_risk=true  (allergen trap)
  - r4 "Midnight Diner" — closed Saturdays               (hours trap)

Also writes/refreshes the Tier 0 CSV knowledge-base files.
"""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "foodie.sqlite"
CSV_DIR = ROOT / "csv"
CSV_DIR.mkdir(exist_ok=True)

if DB.exists():
    DB.unlink()

con = sqlite3.connect(DB)
c = con.cursor()
c.execute("""CREATE TABLE restaurants(
  venue_id TEXT PRIMARY KEY, city TEXT, name TEXT, cuisine TEXT,
  price_level INT, avg_meal_cost REAL, rating REAL, review_count INT,
  address TEXT, area TEXT, lat REAL, lon REAL, hours TEXT,
  dietary_flags TEXT, kid_friendly INT)""")
c.execute("""CREATE TABLE attractions(
  venue_id TEXT PRIMARY KEY, city TEXT, name TEXT, category TEXT,
  cost REAL, rating REAL, lat REAL, lon REAL, hours TEXT,
  visit_duration_min INT, indoor INT, kid_friendly INT)""")

ALLDAY = json.dumps({d: {"open": "08:00", "close": "22:00"}
                     for d in ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]})
CLOSED_SAT = json.dumps({**json.loads(ALLDAY),
                         "sat": {"open": None, "close": None}})

restaurants = [
    # id, name, cuisine, price, cost, rating, reviews, area, lat, lon, hours, flags, kid
    ("r1", "Anju", "korean", 3, 42, 4.7, 900, "Beltline", 51.038, -114.075, ALLDAY,
     {"peanut_risk": False, "vegan_options": True}, 0),
    ("r2", "Peanut Palace", "thai", 2, 30, 4.8, 1200, "Downtown", 51.045, -114.062, ALLDAY,
     {"peanut_risk": True, "vegan_options": True}, 1),          # ALLERGEN TRAP
    ("r3", "Ten Foot Henry", "international", 2, 34, 4.6, 1500, "Beltline", 51.040, -114.073, ALLDAY,
     {"peanut_risk": False, "vegan_options": True}, 1),
    ("r4", "Midnight Diner", "international", 2, 28, 4.4, 300, "Downtown", 51.047, -114.070, CLOSED_SAT,
     {"peanut_risk": False, "vegan_options": False}, 1),        # HOURS TRAP
    ("r5", "Pigeonhole", "international", 3, 48, 4.5, 800, "Beltline", 51.039, -114.078, ALLDAY,
     {"peanut_risk": False, "vegan_options": True}, 0),
    ("r6", "Alforno", "italian", 2, 32, 4.5, 650, "East Village", 51.046, -114.045, ALLDAY,
     {"peanut_risk": False, "vegan_options": True}, 1),
    ("r7", "Una Pizza", "italian", 2, 26, 4.4, 1100, "17th Ave", 51.037, -114.085, ALLDAY,
     {"peanut_risk": False, "vegan_options": True}, 1),
    ("r8", "Charbar", "steakhouse", 3, 55, 4.6, 700, "East Village", 51.045, -114.050, ALLDAY,
     {"peanut_risk": False, "vegan_options": False}, 0),
]

attractions = [
    ("a1", "Glenbow Museum", "museum", 20, 4.6, 51.045, -114.065, ALLDAY, 90, 1, 1),
    ("a2", "Peace Bridge", "landmark", 0, 4.7, 51.052, -114.081, ALLDAY, 30, 0, 1),
    ("a3", "Studio Bell", "museum", 25, 4.5, 51.046, -114.044, ALLDAY, 120, 1, 1),
    ("a4", "Prince's Island Park", "park", 0, 4.8, 51.055, -114.070, ALLDAY, 60, 0, 1),
]

rest_rows = []
for r in restaurants:
    vid, name, cuisine, price, cost, rating, rev, area, lat, lon, hours, flags, kid = r
    row = (vid, "Calgary", name, cuisine, price, cost, rating, rev,
           f"{area} address", area, lat, lon, hours, json.dumps(flags), kid)
    c.execute("INSERT INTO restaurants VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
    rest_rows.append(row)

attr_rows = []
for a in attractions:
    row = (a[0], "Calgary", a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8], a[9], a[10])
    c.execute("INSERT INTO attractions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", row)
    attr_rows.append(row)

con.commit()
con.close()

# Tier 0 knowledge-base CSVs
rest_header = ["venue_id", "city", "name", "cuisine", "price_level", "avg_meal_cost",
               "rating", "review_count", "address", "area", "lat", "lon", "hours",
               "dietary_flags", "kid_friendly"]
with (CSV_DIR / "calgary_restaurants.csv").open("w", newline="") as f:
    w = csv.writer(f)
    w.writerow(rest_header)
    w.writerows(rest_rows)

attr_header = ["venue_id", "city", "name", "category", "cost", "rating", "lat", "lon",
               "hours", "visit_duration_min", "indoor", "kid_friendly"]
with (CSV_DIR / "calgary_attractions.csv").open("w", newline="") as f:
    w = csv.writer(f)
    w.writerow(attr_header)
    w.writerows(attr_rows)

print(f"Seeded {DB}")
print(f"  restaurants={len(restaurants)}  attractions={len(attractions)}")
print(f"  CSVs written to {CSV_DIR}")
print("  Traps: r2 Peanut Palace (peanut_risk), r4 Midnight Diner (closed Sat)")
