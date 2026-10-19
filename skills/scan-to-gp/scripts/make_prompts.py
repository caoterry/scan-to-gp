#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""make_prompts.py -- fill the reader prompt templates for a prepared work dir.

usage: uv run make_prompts.py <workdir> [--batch-size 8]

Splits the systems (manifest order) into batches and writes, per batch K:
  <workdir>/prompts/tab_reader_K.md     -> reader writes <workdir>/reads/tab_K.json
  <workdir>/prompts/staff_reader_K.md   -> reader writes <workdir>/reads/staff_K.json
Prints one line per prompt file. Dispatch one TAB and one STAFF reader per batch, all in parallel.
"""

from __future__ import annotations

import os
import sys

if __name__ == "__main__" and not sys.flags.isolated:
    os.execv(sys.executable, [sys.executable, "-I", *sys.argv])

import argparse  # noqa: E402
import json  # noqa: E402
from pathlib import Path  # noqa: E402

SKILL_DIR = Path(__file__).resolve().parent.parent


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("workdir")
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--systems", default=None, help="comma-separated subset of system ids (re-reads)")
    ap.add_argument("--unverified", action="store_true",
                    help="tab-only pages: also ask the TAB reader for rhythm/time signatures printed in the tab")
    a = ap.parse_args(argv)
    work = Path(a.workdir).resolve()
    man = json.loads((work / "manifest.json").read_text())
    systems = [s for s in man["systems"] if s["kind"] in ("staff+tab", "tab-only")]
    if a.systems:
        want = [x.strip() for x in a.systems.split(",") if x.strip()]
        known = {s["id"] for s in systems}
        unknown = [x for x in want if x not in known]
        if unknown:
            print(f"unknown system id(s): {unknown}; known: {sorted(known)}", file=sys.stderr)
            return 2
        systems = [s for s in systems if s["id"] in set(want)]
    (work / "prompts").mkdir(exist_ok=True)
    (work / "reads").mkdir(exist_ok=True)
    (work / "zoom").mkdir(exist_ok=True)
    batches = [systems[i:i + a.batch_size] for i in range(0, len(systems), a.batch_size)]
    suffix = ""
    if a.systems:  # unique per round, so a later re-read never overwrites an earlier one
        n = 1
        while list((work / "prompts").glob(f"*_reread{n}.md")):
            n += 1
        suffix = f"_reread{n}"
    unverified_text = ""
    if a.unverified:
        unverified_text = (
            "\n## Tab-only page (UNVERIFIED mode)\n\nThere is no notation staff to check against. If the tab "
            "prints rhythm (stems/beams/flags under or above the tab staff), add per bar `\"durs\": [\"8\", \"8\", "
            "...]` -- one value per entry in `notes`, same spellings as standard durations (\"1\", \"2\", \"4\", "
            "\"8\", \"16\") -- and `\"time_sig\"` where one is printed. If no rhythm is printed, leave `durs` "
            "out; never invent rhythm.\n")
    for k, batch in enumerate(batches, start=1):
        for role in ("tab", "staff"):
            todo = [s for s in batch if role == "tab" or s["kind"] == "staff+tab"]
            if not todo:
                continue
            ids = ", ".join(f"{s['id']} ({s['n_bars_detected']} bars detected)" for s in todo)
            tpl = (SKILL_DIR / "prompts" / f"{role}_reader.md").read_text()
            out = work / "reads" / f"{role}_{k}{suffix}.json"
            text = (tpl.replace("{{WORKDIR}}", str(work)).replace("{{SYSTEMS}}", ids)
                    .replace("{{OUT}}", str(out)).replace("{{SKILL_DIR}}", str(SKILL_DIR))
                    .replace("{{UNVERIFIED_SECTION}}", unverified_text if role == "tab" else ""))
            pf = work / "prompts" / f"{role}_reader_{k}{suffix}.md"
            pf.write_text(text)
            print(f"{role:5s} batch {k}: {pf}  ->  {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
