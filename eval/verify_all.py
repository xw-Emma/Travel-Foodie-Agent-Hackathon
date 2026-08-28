#!/usr/bin/env python3
"""Run every verification suite in one command.

WHY THIS EXISTS: these suites used to live in a scratch directory and were
deleted between sessions, so each change was covered by a safety net that
existed only until the next cleanup. Two of them were already lost that way.
They live in the repo now, and this runs the lot.

  python eval/verify_all.py              # everything, including live API suites
  python eval/verify_all.py --offline    # skip anything that calls Google
  python eval/verify_all.py --only facts # one suite by name

Each suite is a separate process: a hard failure in one still lets the rest
report, which is what you want when you are deciding whether a change is safe.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (name, path, needs_live_apis)
SUITES = [
    ("inputs", "eval/verify_inputs.py", True),
    ("routing", "eval/verify_routing.py", True),
    ("facts", "eval/verify_facts.py", True),
    ("demo", "eval/verify_demo.py", True),
    ("acceptance-local", "eval/acceptance.py", False),
]


def run(path: str, env_backend: str | None = None) -> tuple[bool, str, float]:
    started = time.time()
    command = [sys.executable, str(ROOT / path)]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    output = (result.stdout or "") + (result.stderr or "")
    return result.returncode == 0, output, time.time() - started


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true",
                        help="skip suites that call the Google APIs")
    parser.add_argument("--only", help="run one suite by name")
    parser.add_argument("--verbose", action="store_true",
                        help="print full suite output, not just the tail")
    args = parser.parse_args()

    selected = [s for s in SUITES if not args.only or s[0] == args.only]
    if args.offline:
        selected = [s for s in selected if not s[2]]
    if not selected:
        print(f"no suite matches {args.only!r}; known: {[s[0] for s in SUITES]}")
        return 2

    failures = []
    for name, path, _ in selected:
        if not (ROOT / path).exists():
            print(f"SKIP  {name}: {path} not found")
            continue
        ok, output, elapsed = run(path)
        print(f"{'PASS' if ok else 'FAIL'}  {name:<18} {elapsed:5.1f}s  ({path})")
        if args.verbose or not ok:
            tail = output.strip().splitlines()
            for line in (tail if args.verbose else tail[-25:]):
                print("      " + line)
        if not ok:
            failures.append(name)

    print()
    if failures:
        print(f"{len(failures)} SUITE(S) FAILED: {', '.join(failures)}")
        return 1
    print(f"ALL {len(selected)} SUITES PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
