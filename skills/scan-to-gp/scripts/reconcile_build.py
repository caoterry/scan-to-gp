#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyguitarpro>=0.9"]
# ///
"""reconcile_build.py -- cross-check two independent reads, then build a verified .gp5.

usage: uv run reconcile_build.py <workdir> <out.gp5> [--tempo N] [--tuning E2,A2,D3,G3,B3,E4]
                                 [--capo N] [--notation-transpose N] [--title T]
                                 [--write-techniques] [--tab-reprints-ties] [--unverified]

Inputs (in <workdir>):
  reads/tab*.json     TAB reader output   {"systems": {"p1_s1": {"bars": [...]}, ...}}
  reads/staff*.json   staff reader output {"systems": {"p1_s1": {"key_sig": ..., "bars": [...]}, ...}}
                      Files whose name contains "reread" supersede earlier reads system by system.
  manifest.json       (optional) system order + tab line count, from prepare.py
  adjudications.json  (optional) logged patches applied to the reads before reconciling

The oracle: for every note, the sounding MIDI pitch computed from (string, fret),
the tuning and capo must equal the sounding MIDI pitch computed from the written
note (key signature + printed accidentals carried through the bar by staff
position; guitar/bass notation sounds an octave below written). Tied notes must
be absent or parenthesized in the tab; parenthesized tab digits must be tied or
ghost notes in the staff. Both readers must agree on bar count, partial
(pickup/short) bars, multi-bar rests and repeat signs. Every bar's durations must
fill its time signature. Any disagreement => no file, every disagreement listed,
exit code 1.

Outputs: <out.gp5>, <workdir>/report.json, <workdir>/plan.json (per-beat plan that
verify_alphatab.mjs --plan compares against alphaTab's own parse).

Exit codes: 0 ok (file written + read back exactly); 1 disagreements (no file);
2 bad input / unsupported notation / internal error (no file).
"""

from __future__ import annotations

import os
import sys

if __name__ == "__main__" and not sys.flags.isolated:  # re-exec isolated (-I): no PYTHONPATH / user site / cwd
    os.execv(sys.executable, [sys.executable, "-I", *sys.argv])

import argparse  # noqa: E402
import glob  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import traceback  # noqa: E402
from collections import Counter  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from fractions import Fraction  # noqa: E402
from pathlib import Path  # noqa: E402

LETTER_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
SHARP_ORDER = "FCGDAEB"
FLAT_ORDER = "BEADGCF"
ACC = {"sharp": 1, "flat": -1, "natural": 0, "double-sharp": 2, "double-flat": -2,
       "#": 1, "b": -1, "n": 0, "x": 2, "bb": -2}
NOTE_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
DEFAULT_TUNINGS = {  # low -> high (as guitarists say it); string 1 = last entry
    6: "E2,A2,D3,G3,B3,E4",
    7: "B1,E2,A2,D3,G3,B3,E4",
    4: "E1,A1,D2,G2",
}
TUPLET_TIMES = {3: 2, 5: 4, 6: 4, 7: 4, 9: 8, 10: 8, 11: 8, 12: 8, 13: 8}
DUR_VALUES = {"1": 1, "2": 2, "4": 4, "8": 8, "16": 16, "32": 32, "64": 64}
TECH_WRITABLE = {"h", "p", "sl", "/", "\\", "~", "PM", "LR", "harm"}
STRUCTURE_WORDS = re.compile(r"volta|ending|1\.|2\.|d\.s\.|d\.c\.|coda|segno|fine|al coda|multi.?(bar|measure)",
                             re.IGNORECASE)


class InputError(Exception):
    pass


def midi_name(m: int) -> str:
    return f"{NOTE_NAMES[m % 12]}{m // 12 - 1}"


def parse_pitch_name(s: str) -> int:
    m = re.fullmatch(r"\s*([A-Ga-g])([#b]?)(-?\d)\s*", s)
    if not m:
        raise InputError(f"bad tuning note {s!r} (want e.g. E2, F#3, Bb1)")
    pc = LETTER_PC[m.group(1).upper()] + {"#": 1, "b": -1, "": 0}[m.group(2)]
    return 12 * (int(m.group(3)) + 1) + pc


def parse_tuning(spec: str) -> dict[int, int]:
    """'E2,A2,...,E4' (low -> high) -> {string_number: open MIDI}, string 1 = highest."""
    notes = [parse_pitch_name(x) for x in spec.split(",") if x.strip()]
    if len(notes) < 4:
        raise InputError("tuning needs at least 4 strings")
    n = len(notes)
    return {n - i: notes[i] for i in range(n)}


def parse_key_sig(v) -> int:
    """-> number of sharps (positive) or flats (negative)."""
    if v is None:
        return 0
    if isinstance(v, bool):
        raise InputError(f"cannot parse key signature {v!r}")
    if isinstance(v, int):
        if abs(v) > 7:
            raise InputError(f"key signature {v}: more than 7 accidentals")
        return v
    s = str(v).strip().lower()
    if s in ("", "none", "0", "c", "c major", "a minor", "no key signature", "natural"):
        return 0
    m = re.fullmatch(r"(\d+)\s*(sharps?|#|flats?|b)", s) or re.fullmatch(r"(sharps?|#|flats?|b)\s*(\d+)", s)
    if not m:
        raise InputError(f"cannot parse key signature {v!r} (want e.g. '3 sharps', '2 flats', 'none')")
    num = m.group(1) if m.group(1).isdigit() else m.group(2)
    kind = m.group(2) if m.group(1).isdigit() else m.group(1)
    n = int(num)
    if n > 7:
        raise InputError(f"key signature {v!r}: more than 7 accidentals")
    return n if kind.startswith(("sharp", "#")) else -n


def key_alter(nsharps: int) -> dict[str, int]:
    if nsharps > 0:
        return {c: 1 for c in SHARP_ORDER[:nsharps]}
    if nsharps < 0:
        return {c: -1 for c in FLAT_ORDER[:-nsharps]}
    return {}


def parse_time_sig(v) -> tuple[int, int] | None:
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("", "none", "none printed", "not printed", "-"):
        return None
    if s in ("c", "common"):
        return (4, 4)
    if s in ("cut", "alla breve", "c|"):
        return (2, 2)
    m = re.fullmatch(r"(\d+)\s*/\s*(\d+)", s)
    if not m or int(m.group(2)) not in (1, 2, 4, 8, 16, 32) or int(m.group(1)) < 1:
        raise InputError(f"cannot parse time signature {v!r}")
    return int(m.group(1)), int(m.group(2))


def _natural_key(path: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", Path(path).name)]


# ------------------------------------------------------------------ model


@dataclass
class SNote:  # a written note from the staff read, resolved to sounding pitch
    midi: int
    tie: bool
    desc: str
    ghost: bool = False


@dataclass
class SEvent:
    rest: bool
    dur: int
    dots: int
    tuplet: int  # 1 = none
    notes: list[SNote]
    idx: int  # 1-based index in the bar's events

    @property
    def beats(self) -> Fraction:  # in quarter notes
        b = Fraction(4, self.dur)
        if self.dots == 1:
            b *= Fraction(3, 2)
        elif self.dots == 2:
            b *= Fraction(7, 4)
        if self.tuplet != 1:
            b *= Fraction(TUPLET_TIMES[self.tuplet], self.tuplet)
        return b


@dataclass
class TNote:
    string: int
    fret: int
    paren: bool
    midi: int


@dataclass
class TCol:
    notes: list[TNote]
    idx: int  # 1-based entry index in the bar's "notes"


@dataclass
class Bar:
    system: str
    bar: int  # 1-based within its system (as read)
    events: list[SEvent]
    cols: list[TCol]
    ts: tuple[int, int] = (4, 4)
    ts_inferred: bool = False
    key: int = 0
    partial: bool = False
    repeat_start: bool = False
    repeat_end: int = 0
    double_bar: bool = False
    section: str | None = None
    unverified: bool = False
    match: dict = field(default_factory=dict)  # event idx -> TCol or None (rest / omitted tie)

    @property
    def beats(self) -> Fraction:
        return sum((e.beats for e in self.events), Fraction(0))

    @property
    def where(self) -> str:
        return f"{self.system} bar {self.bar}"


# ------------------------------------------------------------- loading


def load_reads(work: Path, kind: str, superseded: list[str]) -> dict:
    """Merge reads/<kind>*.json. A file whose name contains 'reread' replaces earlier reads of the
    systems it contains (logged); any other duplicate is an error."""
    files = sorted(glob.glob(str(work / "reads" / f"{kind}*.json")), key=lambda f: ("reread" in Path(f).name,
                                                                                       _natural_key(f)))
    merged: dict = {}
    for f in files:
        try:
            data = json.loads(Path(f).read_text())
        except json.JSONDecodeError as e:
            raise InputError(f"{Path(f).name} is not valid JSON: {e}") from e
        systems = data.get("systems", data) if isinstance(data, dict) else None
        if not isinstance(systems, dict):
            raise InputError(f"{Path(f).name}: want {{\"systems\": {{<id>: {{...}}}}}}")
        for sid, v in systems.items():
            if not isinstance(v, dict) or not isinstance(v.get("bars", []), list):
                raise InputError(f"{Path(f).name}: system {sid} must be an object with a 'bars' list")
            if sid in merged:
                if "reread" not in Path(f).name:
                    raise InputError(f"system {sid} appears in more than one {kind} read file "
                                     "(re-reads must have 'reread' in the file name)")
                superseded.append(f"{kind} read of {sid} replaced by {Path(f).name}")
            merged[sid] = v
    return merged


def _index(k: int, a: dict, n: int, allow_end: bool = False) -> int:
    try:
        i = int(a["index"])
    except (KeyError, TypeError, ValueError):
        raise InputError(f"adjudication #{k}: needs an integer 'index' (1-based)") from None
    hi = n + 1 if allow_end else n
    if not 1 <= i <= hi:
        raise InputError(f"adjudication #{k}: index {i} out of range 1..{hi}")
    return i - 1


def apply_adjudications(work: Path, tab: dict, staff: dict) -> list[dict]:
    path = work / "adjudications.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    items = data.get("adjudications", data) if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise InputError("adjudications.json: want {\"adjudications\": [...]}")
    for k, a in enumerate(items, start=1):
        if not str(a.get("reason", "")).strip():
            raise InputError(f"adjudication #{k} has no reason -- every edit must say why")
        tgt = {"tab": tab, "staff": staff}.get(a.get("target"))
        if tgt is None:
            raise InputError(f"adjudication #{k}: target must be 'tab' or 'staff'")
        sysd = tgt.get(a.get("system"))
        if sysd is None:
            raise InputError(f"adjudication #{k}: no system {a.get('system')} in the {a.get('target')} read")
        op = a.get("op")
        if a.get("bar") is None:
            if op != "set":
                raise InputError(f"adjudication #{k}: system-level edits must use op 'set'")
            sysd[a["field"]] = a["value"]
            continue
        bars = sysd.setdefault("bars", [])
        try:
            bn = int(a["bar"])
        except (TypeError, ValueError):
            raise InputError(f"adjudication #{k}: 'bar' must be an integer (1-based)") from None
        if op == "insert_bar":
            if not 1 <= bn <= len(bars) + 1:
                raise InputError(f"adjudication #{k}: insert_bar position {bn} out of range 1..{len(bars) + 1}")
            bars.insert(bn - 1, a["value"])
            _renumber(bars)
            continue
        if not 1 <= bn <= len(bars):
            raise InputError(f"adjudication #{k}: {a['system']} has no bar {bn} in the {a['target']} read")
        if op == "delete_bar":
            del bars[bn - 1]
            _renumber(bars)
            continue
        bar = bars[bn - 1]
        if op == "set":
            bar[a["field"]] = a["value"]
            continue
        seq = bar.setdefault("notes" if a["target"] == "tab" else "events", [])
        if op == "replace":
            seq[_index(k, a, len(seq))] = a["value"]
        elif op == "insert":
            seq.insert(_index(k, a, len(seq), allow_end=True), a["value"])
        elif op == "delete":
            del seq[_index(k, a, len(seq))]
        else:
            raise InputError(f"adjudication #{k}: unknown op {op!r} (replace/insert/delete/set/insert_bar/delete_bar)")
    return items


def _renumber(bars: list) -> None:
    for i, b in enumerate(bars, start=1):
        if isinstance(b, dict):
            b["bar"] = i


def system_order(work: Path, tab: dict, staff: dict) -> tuple[list[str], dict]:
    mpath = work / "manifest.json"
    if mpath.exists():
        man = json.loads(mpath.read_text())
        order = [s["id"] for s in man["systems"]]
        info = {s["id"]: s for s in man["systems"]}
        extra = [s for s in list(tab) + list(staff) if s not in info]
        if extra:
            raise InputError(f"reads mention systems not in manifest.json: {sorted(set(extra))}")
        return order, info
    order = list(dict.fromkeys(list(staff) + list(tab)))
    return order, {}


# ------------------------------------------------------------- parsing reads


def _tuplet(ev: dict, where: str) -> int:
    tup = ev.get("tuplet")
    if isinstance(tup, bool):
        tup = 3 if tup else None
    if tup in (None, 0, 1):
        tup = 3 if ev.get("triplet") else 1
    try:
        tup = int(tup)
    except (TypeError, ValueError):
        raise InputError(f"{where}: tuplet must be a number like 3") from None
    if tup != 1 and tup not in TUPLET_TIMES:
        raise InputError(f"{where}: tuplet {tup} not supported (supported: {sorted(TUPLET_TIMES)})")
    return tup


def parse_staff_bar(sid: str, bn: int, bar: dict, keymap: dict[str, int], offset: int,
                    prev_alter: dict) -> list[SEvent]:
    """Resolve written notes to sounding MIDI.

    Accidentals: a printed accidental holds for the rest of the bar at that staff
    position (letter+octave). A tied-over note with no printed accidental keeps the
    alteration of its origin (prev_alter carries the previous event's positions).
    """
    carried: dict[tuple[str, int], int] = {}
    out: list[SEvent] = []
    last_alter: dict[tuple[str, int], int] = dict(prev_alter)
    events = bar.get("events", [])
    if not isinstance(events, list):
        raise InputError(f"{sid} bar {bn}: 'events' must be a list")
    for i, ev in enumerate(events, start=1):
        where = f"{sid} bar {bn} staff event #{i}"
        if not isinstance(ev, dict):
            raise InputError(f"{where}: want an object, got {ev!r}")
        dur = str(ev.get("dur", "")).strip()
        if dur not in DUR_VALUES:
            raise InputError(f"{where}: bad dur {ev.get('dur')!r} (want 1/2/4/8/16/32/64)")
        dots = int(ev.get("dots", 1 if ev.get("dotted") else 0) or 0)
        if dots not in (0, 1, 2):
            raise InputError(f"{where}: dots must be 0, 1 or 2")
        tup = _tuplet(ev, where)
        if ev.get("grace"):
            raise InputError(f"{where}: grace notes are not supported in v1 -- leave them out of both reads "
                             "and list them under 'omitted'")
        if ev.get("rest"):
            out.append(SEvent(True, DUR_VALUES[dur], dots, tup, [], i))
            continue
        raw = ev.get("chord") or [ev]
        if not isinstance(raw, list):
            raise InputError(f"{where}: 'chord' must be a list of notes")
        notes = []
        this_alter = {}
        for n in raw:
            if not isinstance(n, dict):
                raise InputError(f"{where}: chord note must be an object, got {n!r}")
            letter = str(n.get("letter", "")).upper()
            if letter not in LETTER_PC:
                raise InputError(f"{where}: bad letter {n.get('letter')!r}")
            try:
                octv = int(n["octave"])
            except (KeyError, TypeError, ValueError):
                raise InputError(f"{where}: bad or missing octave") from None
            pos = (letter, octv)
            acc = str(n.get("acc", "none")).strip().lower()
            if acc in ("none", "", "null"):
                acc = "none"
            tie = bool(n.get("tie", False))
            ghost = bool(n.get("ghost", False))
            if acc != "none" and acc not in ACC:
                raise InputError(f"{where}: bad acc {n.get('acc')!r} (sharp/flat/natural/double-sharp/double-flat/none)")
            if acc != "none":
                alter = ACC[acc]
                carried[pos] = alter
            elif tie and pos in last_alter:
                alter = last_alter[pos]
            else:
                alter = carried.get(pos, keymap.get(letter, 0))
            this_alter[pos] = alter
            written = 12 * (octv + 1) + LETTER_PC[letter] + alter
            accs = "" if acc == "none" else f" ({acc})"
            notes.append(SNote(written + offset, tie,
                               f"{letter}{octv}{accs}{' tied' if tie else ''}{' ghost' if ghost else ''}", ghost))
        last_alter = this_alter
        out.append(SEvent(False, DUR_VALUES[dur], dots, tup, notes, i))
    prev_alter.clear()
    prev_alter.update(last_alter)
    return out


def parse_tab_bar(sid: str, bn: int, bar: dict, tuning: dict[int, int], capo: int) -> list[TCol]:
    cols = []
    entries = bar.get("notes", [])
    if not isinstance(entries, list):
        raise InputError(f"{sid} bar {bn}: tab 'notes' must be a list")
    for i, entry in enumerate(entries, start=1):
        where = f"{sid} bar {bn} tab entry #{i}"
        group = entry if (isinstance(entry, list) and entry and isinstance(entry[0], list)) else [entry]
        notes = []
        for n in group:
            if not isinstance(n, list) or len(n) < 2:
                raise InputError(f"{where}: want [string, fret] or a list of them, got {n!r}")
            s, f = n[0], n[1]
            extra = n[2:]
            if any(not isinstance(x, str) for x in extra):
                raise InputError(f"{where}: after [string, fret] only flag strings like \"paren\" are allowed: {n!r}")
            flags = [x.lower() for x in extra]
            unknown = [x for x in flags if x not in ("paren", "tie", "ghost")]
            if unknown:
                raise InputError(f"{where}: unknown flag(s) {unknown} (allowed: paren, tie)")
            if isinstance(f, bool) or not isinstance(f, int):
                raise InputError(f"{where}: fret {f!r} is not a number (dead notes 'x' are not supported in v1)")
            if isinstance(s, bool) or s not in tuning:
                raise InputError(f"{where}: string {s!r} does not exist with a {len(tuning)}-string tuning")
            if not 0 <= f <= 30:
                raise InputError(f"{where}: fret {f} out of range")
            notes.append(TNote(s, f, bool(flags), tuning[s] + capo + f))
        strings = [n.string for n in notes]
        if len(set(strings)) != len(strings):
            raise InputError(f"{where}: two notes on the same string in one column: {group}")
        cols.append(TCol(notes, i))
    return cols


# ------------------------------------------------------------- reconcile


def col_matches(ev: SEvent, col: TCol, plain_ties: bool = False) -> bool:
    """Plain tab digits <-> normal staff notes; parenthesized digits <-> tied or ghost staff notes.

    - every normal (untied, non-ghost) staff note must be a PLAIN tab digit, and vice versa;
    - every ghost staff note must be a parenthesized tab digit;
    - a tied staff note may be absent from the tab or parenthesized (with --tab-reprints-ties
      it may also be a plain digit); a plain digit is otherwise a re-attack, not a tie.
    """
    normal = Counter(n.midi for n in ev.notes if not n.tie and not n.ghost)
    tied = Counter(n.midi for n in ev.notes if n.tie)
    ghost = Counter(n.midi for n in ev.notes if n.ghost and not n.tie)
    plain = Counter(n.midi for n in col.notes if not n.paren)
    paren = Counter(n.midi for n in col.notes if n.paren)
    if plain_ties:
        extra_plain = plain - normal
        if normal - plain or extra_plain - tied:
            return False
        tied = tied - extra_plain
    elif plain != normal:
        return False
    if ghost - paren:
        return False
    return not (paren - (tied + ghost))


def align(events: list[SEvent], cols: list[TCol], plain_ties: bool = False):
    """DP alignment: each pitched event matches one column, or (if fully tied) none."""
    ev = [e for e in events if not e.rest]
    n, m = len(ev), len(cols)
    ok = [[False] * (m + 1) for _ in range(n + 1)]
    ok[n][m] = True
    for i in range(n, -1, -1):
        for j in range(m, -1, -1):
            if i == n and j == m:
                continue
            r = False
            if i < n and j < m and col_matches(ev[i], cols[j], plain_ties) and ok[i + 1][j + 1]:
                r = True
            elif i < n and all(x.tie for x in ev[i].notes) and ok[i + 1][j]:
                r = True
            ok[i][j] = r
    if not ok[0][0]:
        return None
    i = j = 0
    match = {}
    while i < n:
        if j < m and col_matches(ev[i], cols[j], plain_ties) and ok[i + 1][j + 1]:
            match[ev[i].idx] = cols[j]
            i, j = i + 1, j + 1
        else:
            match[ev[i].idx] = None
            i += 1
    return match


def _fmt_tab(c: TCol) -> str:
    return "+".join(f"(string {n.string}, fret {n.fret}{', paren' if n.paren else ''})={midi_name(n.midi)}[{n.midi}]"
                    for n in c.notes)


def _fmt_staff(e: SEvent) -> str:
    return "+".join(f"{n.desc}={midi_name(n.midi)}[{n.midi}]" for n in e.notes)


def describe_mismatch(b: Bar, plain_ties: bool) -> list[str]:
    ev = [e for e in b.events if not e.rest]
    out = []
    if len(ev) == len(b.cols):
        for e, c in zip(ev, b.cols):
            if not col_matches(e, c, plain_ties):
                why = ""
                if Counter(n.midi for n in e.notes) == Counter(n.midi for n in c.notes):
                    why = "  (same pitches -- tie/paren markings disagree: a tied note must be absent or " \
                          "parenthesized in the tab, a parenthesized digit must be tied or ghost in the staff)"
                out.append(f"{b.where}: tab entry #{c.idx} {_fmt_tab(c)}  vs  staff event #{e.idx} {_fmt_staff(e)}{why}")
        if out:
            return out
    tab_s = "  ".join(f"#{c.idx}:{_fmt_tab(c)}" for c in b.cols)
    stf_s = "  ".join(f"#{e.idx}:{_fmt_staff(e)}" for e in ev)
    out.append(f"{b.where}: tab has {len(b.cols)} columns, staff has {len(ev)} pitched events "
               f"(tied-only events may be omitted in tab) -- no consistent alignment\n"
               f"      tab   {tab_s}\n      staff {stf_s}")
    return out


def reconcile(work: Path, tuning_spec: str | None, unverified: bool, capo: int = 0, transpose: int = 0,
              plain_ties: bool = False):
    superseded: list[str] = []
    tab = load_reads(work, "tab", superseded)
    staff = load_reads(work, "staff", superseded)
    if not tab:
        raise InputError("no reads/tab*.json found")
    if not staff and not unverified:
        raise InputError("no reads/staff*.json found (a tab-only transcription needs --unverified)")
    adjs = apply_adjudications(work, tab, staff)
    order, info = system_order(work, tab, staff)
    offset = -12 + transpose

    problems: list[str] = []
    warnings: list[str] = list(superseded)
    techs_seen: list[dict] = []
    omitted: list[str] = []
    bars: list[Bar] = []

    lines = {info[s].get("tab_lines") for s in order if s in info and info[s].get("tab_lines")}
    if tuning_spec is None:
        if len(lines) > 1:
            raise InputError(f"systems have different tab line counts {sorted(lines)}; pass --tuning")
        nl = next(iter(lines)) if lines else 6
        if nl not in DEFAULT_TUNINGS:
            raise InputError(f"no default tuning for {nl}-line tab; pass --tuning")
        tuning_spec = DEFAULT_TUNINGS[nl]
        if nl != 6:
            warnings.append(f"{nl}-line tab: assumed tuning {tuning_spec} (pass --tuning if different)")
    tuning = parse_tuning(tuning_spec)
    if lines and len(tuning) not in lines:
        raise InputError(f"--tuning has {len(tuning)} strings but the tab staves have {sorted(lines)} lines")

    unverified_systems = []
    prev_alter: dict = {}
    cur_ts: tuple[int, int] | None = None
    cur_key = 0
    for sid in order:
        t, s = tab.get(sid), staff.get(sid)
        kind = info.get(sid, {}).get("kind", "staff+tab")
        if t is None and s is None:
            problems.append(f"{sid}: missing from both reads")
            continue
        if t is None:
            problems.append(f"{sid}: missing from the tab read")
            continue
        for x in (list(t.get("omitted", []) or []) + list((s or {}).get("omitted", []) or [])):
            omitted.append(f"{sid}: {x}")
        if s is None:
            if not unverified:
                problems.append(f"{sid}: missing from the staff read")
                continue
            unverified_systems.append(sid)
            for bi, tb in enumerate(t["bars"], start=1):
                cols = parse_tab_bar(sid, bi, tb, tuning, capo)
                b = Bar(sid, bi, [], cols, unverified=True)
                durs = tb.get("durs")
                if durs and len(durs) == len(cols):
                    b.events = [SEvent(False, DUR_VALUES[str(d)], 0, 1, [SNote(n.midi, False, "tab") for n in c.notes],
                                       c.idx) for d, c in zip(durs, cols)]
                else:
                    b.events = [SEvent(False, 4, 0, 1, [SNote(n.midi, False, "tab") for n in c.notes], c.idx)
                                for c in cols]
                    warnings.append(f"{sid} bar {bi}: rhythm not available -- placeholder quarter notes (UNVERIFIED)")
                b.match = {c.idx: c for c in cols}
                ts = parse_time_sig(tb.get("time_sig"))
                cur_ts = ts or cur_ts
                b.ts = cur_ts or (4, 4)
                b.repeat_start = bool(tb.get("repeat_start"))
                b.repeat_end = int(tb.get("repeat_end") or 0)
                if bi == 1 and t.get("section"):
                    b.section = t["section"]
                _collect_techs(sid, bi, tb, techs_seen)
                bars.append(b)
            continue
        if kind != "staff+tab" and not unverified:
            problems.append(f"{sid}: prepare.py classified this system as {kind}")

        if "key_sig" in s:
            cur_key = parse_key_sig(s["key_sig"])
        tb_list, sb_list = t.get("bars", []), s.get("bars", [])
        if len(tb_list) != len(sb_list):
            problems.append(f"{sid}: bar count differs -- tab read has {len(tb_list)}, staff read has {len(sb_list)}")
            continue
        clef = str(s.get("clef", "treble")).lower()
        if clef not in ("treble", "treble_8vb", "treble8", "bass", "bass_8vb"):
            raise InputError(f"{sid}: clef {clef!r} not supported (treble / treble_8vb / bass)")
        for bi, (tb, sb) in enumerate(zip(tb_list, sb_list), start=1):
            if not isinstance(tb, dict) or not isinstance(sb, dict):
                raise InputError(f"{sid} bar {bi}: each bar must be an object")
            where = f"{sid} bar {bi}"
            if "key_sig" in sb:
                cur_key = parse_key_sig(sb["key_sig"])
            keymap = key_alter(cur_key)
            ts = parse_time_sig(sb.get("time_sig"))
            if ts:
                cur_ts = ts

            mr_t, mr_s = int(tb.get("multirest") or 0), int(sb.get("multirest") or 0)
            if mr_t or mr_s:
                if mr_t != mr_s:
                    problems.append(f"{where}: multi-bar rest differs -- tab {mr_t or 'none'} vs staff {mr_s or 'none'}")
                    continue
                if tb.get("notes") or sb.get("events"):
                    problems.append(f"{where}: a multi-bar rest must have no notes/events")
                    continue
                for r in range(mr_s):
                    b = Bar(sid, bi, [SEvent(True, 1, 0, 1, [], 1)], [], key=cur_key)
                    b.ts = cur_ts if cur_ts else (0, 0)
                    if r == 0 and bi == 1 and (s.get("section") or t.get("section")):
                        b.section = s.get("section") or t.get("section")
                    bars.append(b)
                continue

            events = parse_staff_bar(sid, bi, sb, keymap, offset, prev_alter)
            cols = parse_tab_bar(sid, bi, tb, tuning, capo)
            b = Bar(sid, bi, events, cols, key=cur_key)
            b.ts = cur_ts if cur_ts else (0, 0)  # (0,0) = to be inferred
            sp = bool(sb.get("partial") or (bi == 1 and s.get("short_first_bar")))
            tp = bool(tb.get("partial") or (bi == 1 and t.get("short_first_bar")))
            if sp != tp:
                problems.append(f"{where}: readers disagree on whether this is a partial (pickup / short) bar -- "
                                f"tab {'partial' if tp else 'full'}, staff {'partial' if sp else 'full'}")
            b.partial = sp
            b.repeat_start = bool(sb.get("repeat_start"))
            b.repeat_end = int(sb.get("repeat_end") or 0)
            if (bool(tb.get("repeat_start")), int(tb.get("repeat_end") or 0)) != (b.repeat_start, b.repeat_end):
                problems.append(f"{where}: repeat signs differ -- tab start={bool(tb.get('repeat_start'))} "
                                f"end={int(tb.get('repeat_end') or 0)} vs staff start={b.repeat_start} end={b.repeat_end}")
            b.double_bar = str(sb.get("end_barline", "")).lower() in ("double", "final") or \
                str(tb.get("end_barline", "")).lower() in ("double", "final")
            if bi == 1 and (s.get("section") or t.get("section")):
                b.section = s.get("section") or t.get("section")
            _collect_techs(sid, bi, tb, techs_seen)
            m = align(events, cols, plain_ties)
            if m is None:
                problems.extend(describe_mismatch(b, plain_ties))
            else:
                b.match = m
            bars.append(b)

    structural = [o for o in omitted if STRUCTURE_WORDS.search(o)]
    if structural:
        warnings.append("readers omitted structure markings (voltas / D.S. / Coda / multi-bar rests): the bar order "
                        "in the file follows the page, not the performance -- playback may be wrong: "
                        + "; ".join(structural))
    _infer_time_sigs(bars, problems, warnings)
    _check_durations(bars, problems)
    stats = {
        "systems": len(order),
        "bars": len(bars),
        "staff_notes": sum(len(e.notes) for b in bars for e in b.events if not e.rest and not b.unverified),
        "tab_notes": sum(len(c.notes) for b in bars for c in b.cols),
    }
    return {"bars": bars, "problems": problems, "warnings": warnings, "techs": techs_seen, "omitted": omitted,
            "adjudications": adjs, "tuning": tuning, "tuning_spec": tuning_spec, "stats": stats,
            "unverified_systems": unverified_systems, "tempo_read": _tempo(staff), "capo": capo}


def _tempo(staff: dict):
    for s in staff.values():
        if s.get("tempo"):
            try:
                return int(s["tempo"])
            except (TypeError, ValueError):
                return None
    return None


def _collect_techs(sid, bi, tb, out):
    for tq in tb.get("tech", []) or []:
        if isinstance(tq, dict):
            out.append({"system": sid, "bar": bi, **tq})


def _section_bounds(bars: list[Bar]) -> tuple[set, set]:
    starts, ends = {0}, {len(bars) - 1}
    for i, b in enumerate(bars):
        if b.section and i > 0:
            starts.add(i)
            ends.add(i - 1)
    return starts, ends


def _infer_time_sigs(bars: list[Bar], problems: list[str], warnings: list[str]) -> None:
    """Bars read before any printed time signature: infer from the verified full bars' lengths."""
    todo = [b for b in bars if b.ts == (0, 0)]
    if not todo:
        return
    basis = [b for b in todo if not b.partial and not b.unverified and
             not (len(b.events) == 1 and b.events[0].rest and b.events[0].dur == 1)]
    full = Counter(b.beats for b in basis)
    ts = (4, 4)
    if len(full) > 1:
        problems.append("no time signature printed and the bars before the first printed one have different lengths "
                        f"({', '.join(f'{k} beats' for k in full)}); report the time signature in the staff read")
    elif len(full) == 1:
        beats = next(iter(full))
        if beats.denominator == 1:
            ts = (int(beats), 4)
        elif (beats * 2).denominator == 1:
            ts = (int(beats * 2), 8)
        else:
            problems.append(f"cannot express an inferred bar length of {beats} beats as a time signature")
        warnings.append(f"no time signature printed before {todo[0].where}; inferred {ts[0]}/{ts[1]} from the bar lengths "
                        "(6/8 vs 3/4 or C vs cut time cannot be told apart this way)")
    else:
        warnings.append(f"no time signature printed before {todo[0].where} and no full bar to infer from; assumed 4/4")
    for b in todo:
        b.ts = ts
        b.ts_inferred = True


def _check_durations(bars: list[Bar], problems: list[str]) -> None:
    starts, ends = _section_bounds(bars)
    for i, b in enumerate(bars):
        if b.unverified:
            continue
        want = Fraction(4 * b.ts[0], b.ts[1])
        got = b.beats
        if len(b.events) == 1 and b.events[0].rest and b.events[0].dur == 1 and not b.partial and got != want:
            # a lone whole rest is a full-bar rest in any metre: write it with the bar's real length
            ev = b.events[0]
            for dur, dots in ((1, 0), (2, 1), (2, 0), (4, 1), (4, 0), (8, 1), (8, 0)):
                trial = SEvent(True, dur, dots, 1, [], ev.idx)
                if trial.beats == want:
                    b.events[0] = trial
                    break
            else:
                problems.append(f"{b.where}: full-bar rest in {b.ts[0]}/{b.ts[1]} cannot be written as one GP5 rest")
            continue
        if b.partial:
            if not b.events:
                problems.append(f"{b.where}: flagged partial but has no events at all")
            elif got >= want:
                problems.append(f"{b.where}: flagged partial but its durations sum to {got} beats (time signature "
                                f"{b.ts[0]}/{b.ts[1]} needs {want}) -- not short")
            elif i not in starts and i not in ends:
                problems.append(f"{b.where}: flagged partial ({got} of {want} beats) but it is not the first or last "
                                "bar of the piece or of a section")
            continue
        if got != want:
            problems.append(f"{b.where}: staff durations sum to {got} beats, time signature {b.ts[0]}/{b.ts[1]} "
                            f"needs {want}" + ("" if got > want else " (if this is a pickup/final partial bar, both "
                                               "readers must set \"partial\": true)"))


# ------------------------------------------------------------- build


def gp_duration(m, ev: SEvent):
    if ev.dots == 2:
        raise InputError(f"double-dotted note (event #{ev.idx}) cannot be written to GP5")
    d = m.Duration(value=ev.dur, isDotted=ev.dots == 1)
    if ev.tuplet != 1:
        d.tuplet = m.Tuplet(enters=ev.tuplet, times=TUPLET_TIMES[ev.tuplet])
    return d


def partial_ts(beats: Fraction) -> tuple[int, int] | None:
    for den in (4, 8, 16, 32):
        num = beats * den / 4
        if num.denominator == 1 and num >= 1:
            return int(num), den
    return None


def build(res: dict, out: Path, tempo: int, title: str, write_techs: bool, track_name: str, instrument: int):
    import guitarpro as gp
    from guitarpro import models as m

    bars: list[Bar] = res["bars"]
    tuning = res["tuning"]
    song = m.Song()
    song.title = title
    song.tempo = tempo
    song.measureHeaders = []
    plan = []
    for i, b in enumerate(bars):
        hdr = m.MeasureHeader(number=i + 1)
        num, den = b.ts
        if b.partial:
            pt = partial_ts(b.beats)
            if pt:
                num, den = pt
            else:
                res["warnings"].append(f"{b.where}: partial bar of {b.beats} beats cannot be a time signature; "
                                       "left under-filled in its own time signature")
        hdr.timeSignature = m.TimeSignature(numerator=num, denominator=m.Duration(value=den))
        hdr.keySignature = m.KeySignature((max(-7, min(7, b.key)), 0))
        if b.section:
            hdr.marker = m.Marker(title=str(b.section))
        hdr.hasDoubleBar = b.double_bar
        hdr.isRepeatOpen = b.repeat_start
        if b.repeat_end:  # reader: total plays (2 = plain :|); GP model: number of repeats
            hdr.repeatClose = max(1, b.repeat_end - 1)
        song.measureHeaders.append(hdr)
        plan.append((b, num, den))

    track = m.Track(song, number=1)
    track.name = track_name
    track.strings = [m.GuitarString(n, v) for n, v in sorted(tuning.items())]
    track.offset = res.get("capo", 0)
    track.channel.instrument = instrument
    track.measures = []
    written_techs = []
    last_pos: dict[int, tuple[int, int]] = {}  # midi -> (string, fret) of the previous beat's notes
    for hdr, (b, num, den) in zip(song.measureHeaders, plan):
        meas = m.Measure(track, hdr)
        meas.voices = []
        v = m.Voice(meas)
        v.beats = []
        col_notes: dict[int, m.Note] = {}
        for ev in b.events:
            beat = m.Beat(v)
            beat.duration = gp_duration(m, ev)
            if ev.rest:
                beat.status = m.BeatStatus.rest
                last_pos = {}
                v.beats.append(beat)
                continue
            beat.status = m.BeatStatus.normal
            col = b.match.get(ev.idx)
            avail = list(col.notes) if col else []
            this_pos = {}
            for sn in ev.notes:
                tn = next((x for x in avail if x.midi == sn.midi), None)
                if tn is not None:
                    avail.remove(tn)
                if sn.tie:
                    origin = last_pos.get(sn.midi)
                    if origin is None:
                        raise InputError(f"{b.where} staff event #{ev.idx}: tie into {midi_name(sn.midi)} has no "
                                         "matching note in the previous beat")
                    if tn is not None and (tn.string, tn.fret) != origin:
                        raise InputError(f"{b.where} staff event #{ev.idx}: tied note shown on string {tn.string} "
                                         f"fret {tn.fret} but its origin is string {origin[0]} fret {origin[1]}")
                    s_, f_ = origin
                    note = m.Note(beat, value=f_, string=s_, type=m.NoteType.tie)
                else:
                    if tn is None:
                        raise InputError(f"{b.where} staff event #{ev.idx}: internal -- no tab note for {sn.desc}")
                    s_, f_ = tn.string, tn.fret
                    note = m.Note(beat, value=f_, string=s_, type=m.NoteType.normal)
                    if sn.ghost:
                        note.effect.ghostNote = True
                    if col:
                        col_notes.setdefault(col.idx, note)
                this_pos[sn.midi] = (s_, f_)
                beat.notes.append(note)
            beat.notes.sort(key=lambda n: n.string)
            last_pos = this_pos
            v.beats.append(beat)
        if write_techs:
            for tq in [x for x in res["techs"] if x["system"] == b.system and x["bar"] == b.bar]:
                try:
                    note = col_notes.get(int(tq.get("note", 0)))
                except (TypeError, ValueError):
                    note = None
                typ = str(tq.get("type", ""))
                if note is None or typ not in TECH_WRITABLE:
                    continue
                eff = note.effect
                if typ in ("h", "p"):
                    eff.hammer = True
                elif typ in ("sl", "/", "\\"):
                    eff.slides = [m.SlideType.shiftSlideTo]
                elif typ == "~":
                    eff.vibrato = True
                elif typ == "PM":
                    eff.palmMute = True
                elif typ == "LR":
                    eff.letRing = True
                elif typ == "harm":
                    eff.harmonic = m.NaturalHarmonic()
                written_techs.append({**tq})
        meas.voices.append(v)
        # GP5 stores 2 voices per measure. An empty (zero-beat) second voice crashed alphaTab in
        # practice, so fill it with one EMPTY-status beat (alphaTab: isEmpty, not drawn).
        # A rest-status beat would draw a stray rest glyph on every bar.
        for _ in range(m.Measure.maxVoices - 1):
            v2 = m.Voice(meas)
            eb = m.Beat(v2)
            eb.duration = m.Duration(value=4)
            eb.status = m.BeatStatus.empty
            v2.beats = [eb]
            meas.voices.append(v2)
        track.measures.append(meas)
    song.tracks = [track]
    out.parent.mkdir(parents=True, exist_ok=True)
    gp.write(song, str(out), version=(5, 1, 0))
    return plan, written_techs


def plan_json(plan, n_strings: int) -> list[dict]:
    """Per-beat expectation, consumed by verify_alphatab.mjs --plan (an independent GP parser)."""
    out = []
    for b, num, den in plan:
        beats = []
        last_pos: dict[int, tuple[int, int]] = {}
        for ev in b.events:
            notes = []
            if not ev.rest:
                col = b.match.get(ev.idx)
                avail = list(col.notes) if col else []
                this = {}
                for sn in ev.notes:
                    tn = next((x for x in avail if x.midi == sn.midi), None)
                    if tn is not None:
                        avail.remove(tn)
                    pos = (tn.string, tn.fret) if tn is not None and not sn.tie else None
                    notes.append({"midi": sn.midi, "tie": sn.tie, "string": pos[0] if pos else None,
                                  "fret": pos[1] if pos else None})
                    this[sn.midi] = pos
                last_pos = this
            beats.append({"rest": ev.rest, "dur": ev.dur, "dots": 1 if ev.dots == 1 else 0,
                          "tuplet": [ev.tuplet, TUPLET_TIMES[ev.tuplet]] if ev.tuplet != 1 else None,
                          "notes": sorted(notes, key=lambda n: n["midi"])})
        out.append({"system": b.system, "bar": b.bar, "ts": [num, den], "beats": beats})
    return out


def readback(out: Path, plan) -> tuple[int, int]:
    """Parse the written file and compare every beat with the plan."""
    import guitarpro as gp
    from guitarpro import models as m

    song = gp.parse(str(out))
    tr = song.tracks[0]
    if len(tr.measures) != len(plan):
        raise AssertionError(f"read-back: {len(tr.measures)} measures, expected {len(plan)}")
    n_notes = 0
    for meas, (b, num, den) in zip(tr.measures, plan):
        ts = meas.header.timeSignature
        if (ts.numerator, ts.denominator.value) != (num, den):
            raise AssertionError(f"read-back {b.where}: time signature {ts.numerator}/{ts.denominator.value} != {num}/{den}")
        got = []
        for bt in meas.voices[0].beats:
            got.append((bt.status == m.BeatStatus.rest, bt.duration.value, bt.duration.isDotted,
                        (bt.duration.tuplet.enters, bt.duration.tuplet.times),
                        sorted(n.realValue for n in bt.notes)))
        want = []
        capo = tr.offset or 0  # GP's realValue = open string + fret, without the capo
        for ev in b.events:
            tup = (ev.tuplet, TUPLET_TIMES[ev.tuplet]) if ev.tuplet != 1 else (1, 1)
            want.append((ev.rest, ev.dur, ev.dots == 1, tup, sorted(n.midi - capo for n in ev.notes)))
        if got != want:
            raise AssertionError(f"read-back {b.where}:\n  got  {got}\n  want {want}")
        n_notes += sum(len(g[4]) for g in got)
    return len(plan), n_notes


# ------------------------------------------------------------- main


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("workdir")
    ap.add_argument("out")
    ap.add_argument("--tempo", type=int, default=None, help="BPM (default: printed tempo if the staff reader saw one, else 90)")
    ap.add_argument("--tuning", default=None, help="comma-separated, LOW to HIGH, e.g. D2,A2,D3,G3,B3,E4")
    ap.add_argument("--capo", type=int, default=0, help="capo fret; tab frets are read relative to the capo")
    ap.add_argument("--notation-transpose", type=int, default=0,
                    help="semitones to add to the staff's sounding pitch (e.g. -1 for 'tune down 1/2 step' books "
                         "whose notation is printed as if in standard tuning)")
    ap.add_argument("--title", default=None)
    ap.add_argument("--write-techniques", action="store_true",
                    help="also write tab-only technique marks (h/p/slides/vibrato/PM/let ring/harmonics) -- UNVERIFIED")
    ap.add_argument("--tab-reprints-ties", action="store_true",
                    help="this book reprints tied notes as plain digits in the tab (weakens the tie check)")
    ap.add_argument("--unverified", action="store_true",
                    help="allow systems that only have tab; output name gets 'UNVERIFIED'")
    ap.add_argument("--report", default=None, help="report path (default <workdir>/report.json)")
    ap.add_argument("--instrument", type=int, default=None, help="MIDI program (default 25 guitar / 33 bass)")
    args = ap.parse_args(argv)

    work = Path(args.workdir)
    out = Path(args.out)
    if args.unverified and "UNVERIFIED" not in out.name:
        unverified_out = out.with_name(out.stem + ".UNVERIFIED" + out.suffix)
    else:
        unverified_out = out
    report_path = Path(args.report) if args.report else work / "report.json"
    plan_path = work / "plan.json"
    # never leave a previous run's results looking current
    for stale in {out, unverified_out, report_path, plan_path}:
        if stale.exists():
            stale.unlink()
    report: dict = {"ok": False, "output": None}

    def fail(msg: str, code: int = 2) -> int:
        report["error"] = msg
        report_path.write_text(json.dumps(report, indent=2))
        print(f"ERROR -- no file written: {msg}", file=sys.stderr)
        return code

    try:
        res = reconcile(work, args.tuning, args.unverified, args.capo, args.notation_transpose,
                        args.tab_reprints_ties)
    except InputError as e:
        return fail(f"InputError: {e}")
    except Exception as e:  # noqa: BLE001 -- any crash must read as an error, never as "disagreements"
        return fail(f"internal error {type(e).__name__}: {e}\n{traceback.format_exc()}")

    st = res["stats"]
    report.update({
        "systems": st["systems"], "bars": st["bars"], "staff_notes": st["staff_notes"], "tab_notes": st["tab_notes"],
        "disagreements": res["problems"], "warnings": res["warnings"], "omitted_by_readers": res["omitted"],
        "techniques_seen_in_tab": res["techs"], "adjudications_applied": len(res["adjudications"]),
        "adjudications": res["adjudications"], "tuning": res["tuning_spec"], "capo": args.capo,
        "notation_transpose": args.notation_transpose, "tab_reprints_ties": args.tab_reprints_ties,
        "unverified_systems": res["unverified_systems"],
    })
    print(f"systems {st['systems']}  bars {st['bars']}  staff notes {st['staff_notes']}  tab notes {st['tab_notes']}"
          f"  adjudications applied {len(res['adjudications'])}")
    if res["problems"]:
        report["agreement"] = None
        report_path.write_text(json.dumps(report, indent=2))
        print(f"\n{len(res['problems'])} DISAGREEMENT(S) -- no file written:")
        for p in res["problems"]:
            print(" -", p)
        return 1

    if res["unverified_systems"]:
        out = unverified_out
    tempo = args.tempo or res["tempo_read"] or 90
    title = args.title or out.stem
    is_bass = len(res["tuning"]) == 4
    instrument = args.instrument if args.instrument is not None else (33 if is_bass else 25)
    try:
        plan, written = build(res, out, tempo, title, args.write_techniques, "Bass" if is_bass else "Guitar", instrument)
        nb, nn = readback(out, plan)
        plan_path.write_text(json.dumps({"strings": len(res["tuning"]), "bars": plan_json(plan, len(res["tuning"]))}))
    except Exception as e:  # noqa: BLE001
        if out.exists():
            out.unlink()
        detail = str(e) if isinstance(e, (InputError, AssertionError)) else f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        return fail(f"build/read-back failed: {detail}")
    compared = omitted_ties = 0
    for b in res["bars"]:
        if b.unverified:
            continue
        for e in b.events:
            if e.rest:
                continue
            col = b.match.get(e.idx)
            n_tab = len(col.notes) if col else 0
            compared += n_tab
            omitted_ties += len(e.notes) - n_tab
    report.update({"ok": True, "output": str(out), "notes_written": nn, "bars_written": nb,
                   "notes_cross_checked": compared,
                   "agreement": f"{compared}/{compared}" if compared else None,
                   "tied_continuations_not_in_tab": omitted_ties, "tempo": tempo,
                   "techniques_written": written, "plan": str(plan_path)})
    report_path.write_text(json.dumps(report, indent=2))
    for w in res["warnings"]:
        print("  warning:", w)
    if res["techs"]:
        print(f"  {len(res['techs'])} technique mark(s) seen in the tab (NOT cross-checkable): "
              + ("written as UNVERIFIED extras" if args.write_techniques else "NOT written (use --write-techniques)"))
    if res["omitted"]:
        print(f"  {len(res['omitted'])} item(s) the readers left out: see report.json omitted_by_readers")
    verdict = "UNVERIFIED (tab-only systems present)" if res["unverified_systems"] else \
        "tab and staff agree on every note"
    print(f"\nOK: {verdict}; wrote {out} ({nb} bars, {nn} notes), read back exactly"
          + (f"; agreement {compared}/{compared} tab notes" if compared else "")
          + (f" (+{omitted_ties} tied continuation(s) the tab does not reprint)" if omitted_ties else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
