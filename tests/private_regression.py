#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyguitarpro>=0.9"]
# ///
"""Private regression against a real scanned book excerpt that can NOT be shipped.

The fixture directory (set SCAN_TO_GP_PRIVATE_FIXTURE) must contain:
  source.pdf        the scan (image-only PDF)
  tab_read.json     a verified TAB read, legacy per-example schema {"ex6": {"bars": [{"notes": [[s,f],..]}]}}
  staff_read.json   the matching staff read, legacy per-example schema
  expected.gp5      the reference file both reads produced
  expect.json       optional: {"systems_per_page": [...], "bars": N, "notes": N}

Checks: prepare.py finds the expected systems on every page; the legacy reads,
split into systems by prepare.py's own barline detection, reconcile cleanly;
the built .gp5 matches expected.gp5 note for note; alphaTab loads it.

usage: SCAN_TO_GP_PRIVATE_FIXTURE=/path/to/fixture uv run tests/private_regression.py
"""

from __future__ import annotations

import os
import sys

if __name__ == "__main__" and not sys.flags.isolated:
    os.execv(sys.executable, [sys.executable, "-I", *sys.argv])

import json  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

import guitarpro as gp  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "scan-to-gp"
# defaults = the excerpt this skill was first proven on (4 pages, examples 6-9)
DEFAULT_EXPECT = {"systems_per_page": [3, 3, 4, 4], "bars": 37, "notes": 326}


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    print("$", " ".join(str(c) for c in cmd))
    p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    print(p.stdout.rstrip())
    if p.stderr.strip():
        print(p.stderr.rstrip())
    return p


def convert_legacy(legacy_tab: dict, legacy_staff: dict, manifest: dict) -> tuple[dict, dict]:
    """Split per-example legacy reads into per-system reads using the detected bar counts.

    Examples map to pages in order (one example per page in this fixture)."""
    pages = manifest["pages"]
    exs = list(legacy_staff)
    if len(exs) != len(pages):
        raise SystemExit(f"fixture has {len(exs)} examples but prepare found {len(pages)} pages")
    sysinfo = {s["id"]: s for s in manifest["systems"]}
    tab_out, staff_out = {}, {}
    for ex, page in zip(exs, pages):
        tbars, sbars = legacy_tab[ex]["bars"], legacy_staff[ex]["bars"]
        counts = [sysinfo[sid]["n_bars_detected"] for sid in page["systems"]]
        if sum(counts) != len(sbars):
            raise SystemExit(f"{ex}: prepare detected {counts} bars per system (sum {sum(counts)}) but the "
                             f"verified read has {len(sbars)} bars")
        k = 0
        short = bool(legacy_staff[ex].get("short_first_bar"))
        ts = legacy_staff[ex].get("time_sig")
        for j, (sid, n) in enumerate(zip(page["systems"], counts)):
            tb = [{"bar": i + 1, "notes": b["notes"]} for i, b in enumerate(tbars[k:k + n])]
            if j == 0 and short:
                tb[0]["partial"] = True
            sb = []
            for i, b in enumerate(sbars[k:k + n]):
                bar = {"bar": i + 1, "events": b["events"]}
                if j == 0 and i == 0:
                    if short:
                        bar["partial"] = True
                    if ts and ts != "none printed":
                        bar["time_sig"] = ts
                sb.append(bar)
            sysd = {"key_sig": legacy_staff[ex]["key_sig"], "clef": "treble", "bars": sb}
            tsys = {"bars": tb}
            if j == 0:
                sysd["section"] = f"Example {ex[2:]}" if ex.startswith("ex") else ex
            staff_out[sid] = sysd
            tab_out[sid] = tsys
            k += n
    return {"reader": "tab", "systems": tab_out}, {"reader": "staff", "systems": staff_out}


def gp_signature(path: Path) -> list:
    song = gp.parse(str(path))
    sig = []
    for meas in song.tracks[0].measures:
        h = meas.header
        beats = []
        for b in meas.voices[0].beats:
            beats.append((b.status.name, b.duration.value, b.duration.isDotted,
                          (b.duration.tuplet.enters, b.duration.tuplet.times),
                          sorted((n.string, n.value, n.type.name) for n in b.notes)))
        sig.append(((h.timeSignature.numerator, h.timeSignature.denominator.value),
                    h.marker.title if h.marker else None, beats))
    return sig


def main() -> int:
    fix = os.environ.get("SCAN_TO_GP_PRIVATE_FIXTURE")
    if not fix:
        print("SCAN_TO_GP_PRIVATE_FIXTURE not set -- skipping private regression")
        return 0
    fix = Path(fix)
    expect = dict(DEFAULT_EXPECT)
    if (fix / "expect.json").exists():
        expect.update(json.loads((fix / "expect.json").read_text()))
    failures = []
    with tempfile.TemporaryDirectory(prefix="stg_privreg_") as td:
        work = Path(td) / "work"
        p = run(["uv", "run", "--script", SKILL / "scripts" / "prepare.py", fix / "source.pdf", work])
        if p.returncode != 0:
            failures.append(f"prepare.py exit {p.returncode}")
        man = json.loads((work / "manifest.json").read_text())
        per_page = [len(pg["systems"]) for pg in man["pages"]]
        kinds = {s["kind"] for s in man["systems"]}
        print("systems per page:", per_page, "kinds:", kinds)
        if per_page != expect["systems_per_page"]:
            failures.append(f"systems per page {per_page} != {expect['systems_per_page']}")
        if kinds != {"staff+tab"}:
            failures.append(f"system kinds {kinds}")

        tab, staff = convert_legacy(json.loads((fix / "tab_read.json").read_text()),
                                    json.loads((fix / "staff_read.json").read_text()), man)
        (work / "reads").mkdir(exist_ok=True)
        (work / "reads" / "tab_read.json").write_text(json.dumps(tab))
        (work / "reads" / "staff_read.json").write_text(json.dumps(staff))
        out = Path(td) / "out.gp5"
        p = run(["uv", "run", "--script", SKILL / "scripts" / "reconcile_build.py", work, out, "--tempo", "80"])
        if p.returncode != 0:
            failures.append(f"reconcile_build.py exit {p.returncode}")
        else:
            rep = json.loads((work / "report.json").read_text())
            print("report:", {k: rep[k] for k in ("bars_written", "notes_written", "agreement")})
            if (rep["bars_written"], rep["notes_written"]) != (expect["bars"], expect["notes"]):
                failures.append(f"built {rep['bars_written']} bars / {rep['notes_written']} notes, expected "
                                f"{expect['bars']} / {expect['notes']}")
            a, b = gp_signature(out), gp_signature(fix / "expected.gp5")
            diffs = [i + 1 for i, (x, y) in enumerate(zip(a, b)) if x != y]
            if len(a) != len(b) or diffs:
                failures.append(f"differs from expected.gp5: measures {len(a)} vs {len(b)}, differing bars {diffs[:10]}")
                for i in diffs[:3]:
                    print(f"  bar {i}:\n    built    {a[i - 1]}\n    expected {b[i - 1]}")
            else:
                print(f"note-for-note identical to expected.gp5 ({len(a)} measures)")
            p = run(["node", SKILL / "scripts" / "verify_alphatab.mjs", out,
                     "--expect-bars", str(expect["bars"]), "--expect-notes", str(expect["notes"]),
                     "--plan", work / "plan.json"])
            if p.returncode != 0:
                failures.append(f"alphaTab verify exit {p.returncode}")
    if failures:
        print("\nPRIVATE REGRESSION FAILED:")
        for f in failures:
            print(" -", f)
        return 1
    print("\nPRIVATE REGRESSION PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
