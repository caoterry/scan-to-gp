#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyguitarpro>=0.9"]
# ///
"""Private replay: re-run prepare.py on real scanned pages that can NOT be shipped, then reconcile + build
+ verify on reads and adjudications that real reader agents produced for those pages earlier, and compare
the new file with the earlier one beat for beat.

SCAN_TO_GP_PRIVATE_REPLAY points at a JSON file (kept outside this repo):

  {"cases": [
     {"name": "case-a", "input": "/abs/book.pdf", "pages": "1",
      "workdir": "/abs/earlier/workdir",            # its reads/*.json, adjudications.json, plan.json, report.json
      "flags": ["--tuning", "D2,A2,D3,G3,A3,D4"],    # the reconcile flags used for the earlier file
      "reference_gp5": "/abs/earlier/out.gp5",
      "expect": {"identical": true,                  # same tracks / bars / beats as the reference
                 "tracks": 2, "bars": 10, "untranscribed": ["7-8"], "track_bars": ["1-6", "9-10"]},
      "tester_counts": {"grace notes": 13}}           # counts a human wrote down for the summary check
  ]}

Every bar of the new file that holds notes is compared with the reference bar of the same system and
bar (from the two plan.json files): time signature, key, every beat of both voices (status, duration,
dots, tuplet, string, fret, tie / ghost), tempo changes. Bars the new file leaves untranscribed must be
empty. With "identical", the track / bar layout and the headers (repeats, triplet feel, markers) must
match too. Prints only case names and numbers.

usage: SCAN_TO_GP_PRIVATE_REPLAY=/path/replay.json uv run --script tests/private_replay.py [name ...]
"""

from __future__ import annotations

import os
import sys

if __name__ == "__main__" and not sys.flags.isolated:
    os.execv(sys.executable, [sys.executable, "-I", *sys.argv])

import json  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

import guitarpro as gp  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "scan-to-gp" / "scripts"


def run(cmd: list) -> subprocess.CompletedProcess:
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True)


def beat_key(b) -> tuple:
    d = b.duration
    mt = b.effect.mixTableChange
    return (b.status.name, d.value, d.isDotted, d.tuplet.enters, d.tuplet.times,
            tuple(sorted((n.string, n.value, n.type.name, bool(n.effect.ghostNote)) for n in b.notes)),
            mt.tempo.value if mt and mt.tempo else None)


def measure_key(m) -> tuple:
    ts = m.header.timeSignature
    voices = tuple(tuple(beat_key(b) for b in v.beats if b.status.name != "empty") for v in m.voices)
    return (ts.numerator, ts.denominator.value, m.header.keySignature.value), voices


def header_key(h) -> tuple:
    return (h.isRepeatOpen, h.repeatClose, h.tripletFeel.name, h.marker.title if h.marker else None, h.hasDoubleBar)


def empty_measure(m) -> bool:
    return all(b.status.name == "empty" and not b.notes for v in m.voices for b in v.beats)


def plan_tracks(plan: dict) -> list[dict]:
    return [{"name": plan.get("name"), "bars": plan["bars"]}, *plan.get("more_tracks", [])]


def compare(new_gp: Path, new_plan: dict, ref_gp: Path, ref_plan: dict, identical: bool) -> tuple[list[str], int]:
    new, ref = gp.parse(str(new_gp)), gp.parse(str(ref_gp))
    bad = []
    ref_at = {}
    for ti, (tr, tp) in enumerate(zip(ref.tracks, plan_tracks(ref_plan))):
        for m, pb in zip(tr.measures, tp["bars"]):
            if not pb.get("untranscribed"):
                ref_at[(ti if identical else 0, pb["system"], pb["bar"])] = (m, tr)
    compared = 0
    for ti, (tr, tp) in enumerate(zip(new.tracks, plan_tracks(new_plan))):
        for i, (m, pb) in enumerate(zip(tr.measures, tp["bars"])):
            where = f"track {ti + 1} bar {i + 1}"
            if pb.get("untranscribed"):
                if not empty_measure(m):
                    bad.append(f"{where}: planned untranscribed but holds beats")
                continue
            hit = ref_at.get((ti if identical else 0, pb["system"], pb["bar"]))
            if hit is None:
                bad.append(f"{where}: no reference bar for {pb['system']} bar {pb['bar']}")
                continue
            rm, rtr = hit
            if measure_key(m) != measure_key(rm):
                bad.append(f"{where} ({pb['system']} bar {pb['bar']}): beats differ from the reference")
            if (tr.offset, [s.value for s in tr.strings]) != (rtr.offset, [s.value for s in rtr.strings]):
                bad.append(f"{where}: tuning / capo differ from the reference")
            compared += 1
    if identical:
        if [t.name for t in new.tracks] != [t.name for t in ref.tracks]:
            bad.append("track names differ from the reference")
        if len(new.measureHeaders) != len(ref.measureHeaders):
            bad.append(f"{len(new.measureHeaders)} bars vs reference {len(ref.measureHeaders)}")
        else:
            for i, (h, rh) in enumerate(zip(new.measureHeaders, ref.measureHeaders)):
                if header_key(h) != header_key(rh):
                    bad.append(f"bar {i + 1}: header (repeats / feel / marker / double bar) differs from the reference")
        if new.tempo != ref.tempo:
            bad.append(f"tempo {new.tempo} vs reference {ref.tempo}")
        ref_compared = sum(1 for _ in ref_at)
        if compared != ref_compared:
            bad.append(f"compared {compared} bars, the reference has {ref_compared} transcribed bars")
    return bad, compared


def counts(report: dict) -> dict:
    out = {}
    for item in report.get("dropped_playback") or []:
        m = re.match(r"(\d+) (.+)", item)
        if m:
            out[m.group(2)] = int(m.group(1))
    return out


def replay(case: dict, work: Path) -> list[str]:
    src = Path(case["workdir"])
    cmd = ["uv", "run", "-q", "--script", SCRIPTS / "prepare.py", case["input"], work]
    if case.get("pages"):
        cmd += ["--pages", str(case["pages"])]
    p = run(cmd)
    if p.returncode != 0:
        return [f"prepare exit {p.returncode}: {p.stderr.strip()[-300:]}"]
    man = json.loads((work / "manifest.json").read_text())
    ref_man = json.loads((src / "manifest.json").read_text())
    ids = [(s["id"], s["n_bars_detected"]) for s in man["systems"]]
    ref_ids = [(s["id"], s["n_bars_detected"]) for s in ref_man["systems"]]
    bad = []
    if ids != ref_ids:
        return [f"system ids / bars changed: {ids} vs {ref_ids} -- map them before replaying"]
    for f in sorted((src / "reads").glob("*.json")):
        shutil.copy(f, work / "reads" / f.name)
    if (src / "adjudications.json").exists():
        shutil.copy(src / "adjudications.json", work / "adjudications.json")
    out = work / "replay.gp5"
    p = run(["uv", "run", "-q", "--script", SCRIPTS / "reconcile_build.py", work, out, *case.get("flags", [])])
    rep = json.loads((work / "report.json").read_text())
    if p.returncode != 0:
        return [f"reconcile exit {p.returncode}: {(rep.get('error') or '')[:300]}"]
    v = run(["node", SCRIPTS / "verify_alphatab.mjs", out, "--expect-bars", rep["bars_written"], "--expect-notes",
             rep["notes_written"], "--plan", work / "plan.json"])
    if v.returncode != 0:
        bad.append(f"alphaTab: {v.stderr.strip()[-300:]}")
    plan = json.loads((work / "plan.json").read_text())
    ref_plan = json.loads((src / "plan.json").read_text())
    exp = case.get("expect", {})
    diffs, compared = compare(out, plan, Path(case["reference_gp5"]), ref_plan, bool(exp.get("identical")))
    bad += diffs
    got = {"tracks": len(rep["tracks"]), "bars": rep["bars_written"], "notes": rep["notes_written"],
           "untranscribed": [u["bars"] for u in rep.get("untranscribed_bars", [])],
           "track_bars": [t["notated_bars"] for t in rep.get("track_bars", [])]}
    for k in ("tracks", "bars", "untranscribed", "track_bars"):
        if k in exp and got[k] != exp[k]:
            bad.append(f"{k} {got[k]} != expected {exp[k]}")
    ref_rep = json.loads((src / "report.json").read_text())
    before, after = counts(ref_rep), counts(rep)
    print(f"  {case['name']}: tracks {got['tracks']} bars {got['bars']} notes {got['notes']} "
          f"(reference: {len(ref_rep.get('tracks') or [])} tracks, {ref_rep.get('bars_written')} bars, "
          f"{ref_rep.get('notes_written')} notes); untranscribed {got['untranscribed']}; track bars {got['track_bars']}; "
          f"{compared} bars compared beat for beat, identical layout required: {bool(exp.get('identical'))}, "
          f"differences: {len(diffs)}; alphaTab {'ok' if v.returncode == 0 else 'FAILED'}")
    print(f"    NOT IN THE FILE before: {before}")
    print(f"    NOT IN THE FILE after:  {after}  (+ {len(rep.get('other_items_not_in_file') or [])} verbatim items, "
          f"{len(rep.get('dropped_by_interpretation') or [])} interpretation drops, "
          f"{len(rep.get('dropped_playback_reader_differences') or [])} reader differences)")
    if case.get("tester_counts"):
        def now(k: str):
            parts = [after.get(x.strip()) for x in k.split("+")]
            return "not counted" if all(v is None for v in parts) else sum(v or 0 for v in parts)
        print("    tester counts vs after: " + ", ".join(f"{k} {v} -> {now(k)}" for k, v in case["tester_counts"].items()))
    print(f"    round1 before {ref_rep.get('round1')} -> after {rep.get('round1')}")
    return bad


def main() -> int:
    spec = os.environ.get("SCAN_TO_GP_PRIVATE_REPLAY")
    if not spec:
        print("SCAN_TO_GP_PRIVATE_REPLAY not set -- skipping the private replay")
        return 0
    cases = json.loads(Path(spec).read_text())["cases"]
    only = set(sys.argv[1:])
    failures = {}
    with tempfile.TemporaryDirectory(prefix="stg_replay_") as td:
        for case in cases:
            if only and case["name"] not in only:
                continue
            bad = replay(case, Path(td) / case["name"])
            if bad:
                failures[case["name"]] = bad
    if failures:
        print("\nPRIVATE REPLAY FAILED:")
        for name, bad in failures.items():
            for b in bad[:20]:
                print(f" - {name}: {b}")
        return 1
    print("\nPRIVATE REPLAY PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
