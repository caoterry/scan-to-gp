#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Private structural regression: prepare.py on real scanned pages that can NOT be shipped.

SCAN_TO_GP_PRIVATE_PAGES points at a JSON file (kept outside this repo) listing pages and the
structure a human checked on each one:

  {"cases": [
     {"name": "case-a", "input": "/abs/path/book.pdf", "pages": "1",
      "expect": {"exit": 0, "bars": [4, 4, 4, 4], "parts": 2, "groups": [2, 2],
                 "dropped": {"dropped": 0, "skipped": 0}, "gaps": [], "tab_lines": 6, "dashed": true}}
  ]}

Every key under "expect" is optional. Checks: exit code, detected bars per system (reading order),
stacked parts and systems per group, dropped / skipped staves, the bar count prepare gives each skipped
system ("gaps", reading order; null = left unknown), tab line counts, whether any dashed barline was found. Nothing about the pages is printed beyond the case names you chose.

usage: SCAN_TO_GP_PRIVATE_PAGES=/path/expect.json uv run --script tests/private_structure.py [name ...]
"""

from __future__ import annotations

import os
import sys

if __name__ == "__main__" and not sys.flags.isolated:
    os.execv(sys.executable, [sys.executable, "-I", *sys.argv])

import json  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
from collections import Counter  # noqa: E402
from pathlib import Path  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
PREPARE = REPO / "skills" / "scan-to-gp" / "scripts" / "prepare.py"


def check(case: dict, work: Path) -> list[str]:
    cmd = ["uv", "run", "-q", "--script", str(PREPARE), case["input"], str(work)]
    if case.get("pages"):
        cmd += ["--pages", str(case["pages"])]
    p = subprocess.run(cmd, capture_output=True, text=True)
    exp = case.get("expect", {})
    bad = []
    if p.returncode != exp.get("exit", 0):
        bad.append(f"exit {p.returncode} != {exp.get('exit', 0)}: {p.stderr.strip()[-400:]}")
    mpath = work / "manifest.json"
    if not mpath.exists():
        return bad + ["no manifest.json"]
    man = json.loads(mpath.read_text())
    systems = man["systems"]
    got = {
        "bars": [s["n_bars_detected"] for s in systems],
        "parts": man.get("parts", 1),
        "groups": [len(g["systems"]) for g in man.get("groups", [])],
        "dropped": dict(Counter(x["role"] for x in man.get("dropped", []))),
        "gaps": [g.get("bars") for g in man.get("gaps", [])],
        "tab_lines": sorted({s.get("tab_lines") for s in systems}),
        "dashed": any(s.get("barlines_dashed_x") for s in systems),
    }
    if "bars" in exp and got["bars"] != exp["bars"]:
        bad.append(f"bars per system {got['bars']} != {exp['bars']}")
    if "parts" in exp and got["parts"] != exp["parts"]:
        bad.append(f"stacked parts {got['parts']} != {exp['parts']}")
    if "groups" in exp and got["groups"] != exp["groups"]:
        bad.append(f"systems per group {got['groups']} != {exp['groups']}")
    if "dropped" in exp:
        want = {k: v for k, v in exp["dropped"].items() if v}
        if got["dropped"] != want:
            bad.append(f"dropped/skipped staves {got['dropped']} != {want}")
    if "gaps" in exp and got["gaps"] != exp["gaps"]:
        bad.append(f"skipped-system bar counts {got['gaps']} != {exp['gaps']}")
    if "tab_lines" in exp and got["tab_lines"] != [exp["tab_lines"]]:
        bad.append(f"tab line counts {got['tab_lines']} != [{exp['tab_lines']}]")
    if "dashed" in exp and got["dashed"] != exp["dashed"]:
        bad.append(f"dashed barlines found = {got['dashed']}, expected {exp['dashed']}")
    if exp.get("exit", 0) == 0 and not man.get("verifiable"):
        bad.append(f"not verifiable: {man.get('refused')}")
    print(f"  {case['name']}: bars {got['bars']} parts {got['parts']} groups {got['groups']} "
          f"dropped {got['dropped']} gaps {got['gaps']} tab lines {got['tab_lines']} dashed {got['dashed']}")
    return bad


def main() -> int:
    spec = os.environ.get("SCAN_TO_GP_PRIVATE_PAGES")
    if not spec:
        print("SCAN_TO_GP_PRIVATE_PAGES not set -- skipping private structural regression")
        return 0
    cases = json.loads(Path(spec).read_text())["cases"]
    only = set(sys.argv[1:])
    failures = {}
    with tempfile.TemporaryDirectory(prefix="stg_privstruct_") as td:
        for case in cases:
            if only and case["name"] not in only:
                continue
            bad = check(case, Path(td) / case["name"])
            if bad:
                failures[case["name"]] = bad
    if failures:
        print("\nPRIVATE STRUCTURE REGRESSION FAILED:")
        for name, bad in failures.items():
            for b in bad:
                print(f" - {name}: {b}")
        return 1
    print("\nPRIVATE STRUCTURE REGRESSION PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
