"""K3: every request field must actually be read somewhere.

Three fields have now shipped collected-but-unread - min_rating and
search_radius_km before Phase C and A, and attraction_types until Phase J. Each
one was offered in both UIs, validated into TripRequest, sent over HTTP, and
then ignored: the user set a control that changed nothing, which is worse than
not offering it. Asking for a museum returned the CN Tower for exactly this
reason.

This walks TripRequest's fields and asserts each is referenced under src/
outside the model definition itself. It cannot catch a field that is read and
then ignored, but it would have caught all three of these.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.request_model import TripRequest

fails = []


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {label}: {got!r}")
    if not ok:
        fails.append(f"{label}: got {got!r} want {want!r}")


# Fields whose only job is to be carried, with the reason each is exempt.
CARRIED_ONLY = {
    "tier": "selects run_tier1 vs run_tier2 at the entrypoint, not inside src/",
    "data_backend": "consumed by config.set_backend_override before planning",
    "budget_basis": "resolved into budget_total at the request-model boundary",
}

SOURCES = [path for path in (ROOT / "src").rglob("*.py")
           if path.name != "request_model.py"]
TEXT = {path: path.read_text(encoding="utf-8") for path in SOURCES}


def readers(field: str) -> list[str]:
    """Files that mention the field by name, however it is reached."""
    pattern = re.compile(rf"""["']{re.escape(field)}["']|\.{re.escape(field)}\b""")
    return sorted(path.relative_to(ROOT).as_posix()
                  for path, text in TEXT.items() if pattern.search(text))


print(f"walking {len(TripRequest.model_fields)} request fields across "
      f"{len(SOURCES)} source files\n")

dead = []
for field in sorted(TripRequest.model_fields):
    found = readers(field)
    if found:
        print(f"  read   {field:<26} {found[0]}"
              + (f" (+{len(found) - 1})" if len(found) > 1 else ""))
    elif field in CARRIED_ONLY:
        print(f"  exempt {field:<26} {CARRIED_ONLY[field]}")
    else:
        print(f"  DEAD   {field:<26} nothing under src/ reads it")
        dead.append(field)

check("no request field is collected and never read", dead, [])

# The three that shipped dead, now asserted individually so a regression names
# the specific field rather than only the count.
for field, note in (("min_rating", "quality gate (Phase C)"),
                    ("search_radius_km", "search anchor (Phase A)"),
                    ("attraction_types", "attraction category (Phase J)")):
    check(f"{field} is read - {note}", bool(readers(field)), True)

check("family_friendly is read", bool(readers("family_friendly")), True)
check("return_to_origin is read", bool(readers("return_to_origin")), True)

print()
if fails:
    print(f"{len(fails)} FAILURE(S):")
    for f in fails:
        print("  -", f)
    raise SystemExit(1)
print("ALL FIELD-USE CHECKS PASSED")
