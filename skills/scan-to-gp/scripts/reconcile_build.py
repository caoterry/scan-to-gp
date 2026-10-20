#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyguitarpro>=0.9"]
# ///
"""reconcile_build.py -- cross-check two independent reads, then build a verified .gp5.

usage: uv run reconcile_build.py <workdir> <out.gp5> [--tempo N] [--tuning E2,A2,D3,G3,B3,E4]
                                 [--capo N] [--notation-transpose N] [--title T]
                                 [--write-techniques] [--tab-reprints-ties] [--unverified]
                                 [--legend-override REASON] [--gap-bars ID:N]

Inputs (in <workdir>):
  reads/tab*.json     TAB reader output   {"systems": {"p1_s1": {"bars": [...]}, ...}}
  reads/staff*.json   staff reader output {"systems": {"p1_s1": {"key_sig": ..., "bars": [...]}, ...},
                                           "legends": [{"page": 1, "kind": "tuning", "text": ...}, ...]}
                      Files whose name contains "reread" supersede earlier reads system by system.
  manifest.json       (optional) system order, stacked parts (group/part), tab line counts, from prepare.py
  adjudications.json  (optional) logged patches applied to the reads before reconciling

The oracle: for every note, the sounding MIDI pitch computed from (string, fret),
the tuning and capo must equal the sounding MIDI pitch computed from the written
note (key signature + printed accidentals carried through the bar by staff
position; guitar/bass notation sounds an octave below written). With two voices on
one staff (stems up / stems down), the notes of BOTH voices that start at the same
moment are compared with one TAB column (a unison may be printed once or twice).
Tied notes must be absent or parenthesized in the tab; parenthesized tab digits must
be tied or ghost notes in the staff. Both readers must agree on bar count, partial
(pickup/short) bars, multi-bar rests and repeat signs. Every bar's durations must
fill its time signature, voice by voice. Stacked parts (Gtr. 1 over Gtr. 2 in one
system) are reconciled part by part and must have the same bars; each becomes its
own track. Any disagreement => no file, every disagreement listed, exit code 1.

One timeline, never collapsed: the system groups in page order, and every system prepare.py
skipped (no guitar notation, e.g. a vocal-only system) keeps its bars where it is printed, as
UNTRANSCRIBED measures -- empty-status beats, never rests or notes -- marked "Bars a-b not
transcribed". Their count comes from prepare.py (barlines on that staff) or --gap-bars ID:N; if
neither gives it, the run is refused. One track per stacked part and printed part label: a system
labelled with another part (Gtr. 2 after Gtr. 1) becomes a track of its own on the common
timeline, empty where it is not notated; an unlabelled system continues the part above it; labels
that alternate (1, 2, 1) are refused as stacked parts read as consecutive systems.

NOT cross-checkable, so taken from flags and the page legend: tuning, capo, and
whether the notation is printed transposed (both reads shift together). If the
staff reader transcribed a tuning / capo legend that the flags contradict, the run
stops and prints the exact flags (override with --legend-override "reason").

Outputs: <out.gp5>, <workdir>/report.json, <workdir>/plan.json (per-beat plan, untranscribed
bars and markers included, that verify_alphatab.mjs --plan compares against alphaTab's own parse).

Exit codes: 0 ok (file written + read back exactly); 1 disagreements (no file);
2 bad input / flags contradict the page legend / unsupported notation / internal error (no file).
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
DEFAULT_TEMPO = 90
# structure markings that change the playback ORDER. Word boundaries / anchored forms only:
# "descending" is not an ending, "bar 1." is not a volta.
STRUCTURE_WORDS = re.compile(
    r"\bvoltas?\b|\b(?:1st|2nd|3rd|first|second|third)\s+(?:ending|time)s?\b|\bendings?\s+(?:bracket|[123])"
    r"|\b[123]\.\s*(?:and|&|/)\s*[123]\.|\b[123]\.\s+(?:ending|bracket|volta)|(?:bracket|volta)\s+[123]\.?(?!\d)"
    r"|\bd\.\s?s\.|\bd\.\s?c\.|\bdal\s+segno\b|\bda\s+capo\b|\bcoda\b|\bsegno\b|\bfine\b"
    r"|\bmulti.?(?:bar|measure)\s+rests?\b|\brepeat\s+(?:sign|bar|\d+\s+times|x\s?\d)",
    re.IGNORECASE)
# what the file does NOT play (report headline). Every number has ONE source: TAB-reader technique marks
# (one per mark; palm mute / let ring are spans) or the readers' structured grace / dead / bend records.
# Free-text "omitted" items are listed verbatim, never counted.
TECH_TYPES = {"h": "h", "p": "p", "sl": "sl", "/": "sl", "\\": "sl", "b": "b", "r": "r", "~": "~", "pm": "PM",
              "lr": "LR", "harm": "harm", "t": "t", "other": "other"}
TECH_NAMES = {"h": "hammer-ons", "p": "pull-offs", "sl": "slides", "r": "bend releases", "~": "vibrato",
              "PM": "palm-mute spans", "LR": "let-ring spans", "harm": "harmonics", "t": "taps"}
SPAN_TECHS = ("PM", "LR")  # one printed mark spans several notes; readers may tag every note under it
STRUCT_NAMES = {"grace": "grace notes", "dead": "dead notes (x)", "bend": "bends"}
ADJ_KINDS = ("glyph", "interpretation")


class InputError(Exception):
    pass


class LegendError(InputError):
    """The flags contradict a tuning / capo legend the staff reader transcribed."""

    def __init__(self, msg: str, flags: str):
        super().__init__(msg)
        self.flags = flags


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


def tuning_spec_of(tuning: dict[int, int]) -> str:
    return ",".join(midi_name(tuning[s]) for s in sorted(tuning, reverse=True))


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


# ------------------------------------------------------------------ legends (tuning / capo / tempo / feel)

_CIRCLED = {"①": "1", "②": "2", "③": "3", "④": "4", "⑤": "5", "⑥": "6", "⑦": "7"}
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10, "xi": 11,
          "xii": 12}
_ORDINAL = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
            "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12}
_NUMBER = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
_NAMED_TUNINGS = {  # low -> high; the key must not be followed by # / b ("drop c#" is not "drop c")
    "dadgad": "D2,A2,D3,G3,A3,D4", "drop d": "D2,A2,D3,G3,B3,E4", "drop c#": "C#2,G#2,C#3,F#3,A#3,D#4",
    "drop db": "C#2,G#2,C#3,F#3,A#3,D#4", "drop c": "C2,G2,C3,F3,A3,D4", "drop b": "B1,F#2,B2,E3,G#3,C#4",
    "open g": "D2,G2,D3,G3,B3,D4", "open d": "D2,A2,D3,F#3,A3,D4", "open e": "E2,B2,E3,G#3,B3,E4",
    "open a": "E2,A2,E3,A3,C#4,E4", "open c": "C2,G2,C3,G3,C4,E4",
}
# a shift is <amount> <unit>: "1/2 step" = 1 semitone, "1 1/2 steps" = 3, "a whole tone" = 2, "2 semitones" = 2
_AMT = r"(?P<amt>(?:\d+|an?|one|two|three|four|five)?\s*(?:and\s+(?:a\s+)?)?(?:1/2|half)?)"
_UNIT = r"(?P<unit>semitones?|half[- ]?(?:steps?|tones?)|whole[- ]?(?:steps?|tones?)|steps?|tones?)"
_SHIFT_DOWN = [  # (pattern, needs tuning context in the same legend)
    (re.compile(r"\bdown\s+(?:by\s+)?" + _AMT + r"\s*" + _UNIT + r"(?![a-z])"), True),
    (re.compile(r"(?<![a-z])" + _AMT + r"\s*" + _UNIT + r"\s+down\b"), True),
    (re.compile(r"\bdetune[ds]?\s+(?:by\s+)?" + _AMT + r"\s*" + _UNIT + r"(?![a-z])"), False),
]
_TUNING_CONTEXT = re.compile(r"\btun(?:e|es|ed|ing)\b|\bdetun|\bguitars?\b|\bstrings\b|\bstandard\b|\bgtrs?\b")
_TUNING_ISH = re.compile(r"\btun(?:e|es|ed|ing)\b|\bdetun|\bdrop\s+[a-g]\b")
_PAIR = re.compile(r"(?:^|[\s,;:(])\(?([1-7])\)?\s*=\s*([a-g])(#|b|flat|sharp)?(?![a-z])")
_LISTED = re.compile(r"\btuning\s*:?\s*((?:[a-g][#b]?[\s,/-]+){3,6}[a-g][#b]?)(?![a-z#])")
_KEY_STANDARD = re.compile(r"(?<!drop )(?<!open )\b([a-g])(b|#)?\s+(?:standard\s+)?tuning\b|\b([a-g])(b|#)?\s+standard\b")
_BEATS = [(r"dotted\s+half|half\s+note\s*\.|𝅗𝅥\s*\.|\bh\s*\.", Fraction(3)), (r"dotted\s+quarter|♩\s*\.|\bq\s*\.", Fraction(3, 2)),
          (r"dotted\s+eighth|♪\s*\.|\be\s*\.", Fraction(3, 4)), (r"\bhalf\b|𝅗𝅥|\bh\b", Fraction(2)),
          (r"\beighth\b|♪|\be\b", Fraction(1, 2)), (r"\bquarter\b|♩|\bq\b|\bcrotchet\b", Fraction(1))]


@dataclass
class Legends:
    texts: list[dict] = field(default_factory=list)  # as transcribed
    tune_down: int = 0  # semitones, from a half-step-down legend, "Eb tuning", ...
    tuning: list[int] | None = None  # MIDI low -> high, if a legend names the strings / a tuning
    tuning_text: str = ""
    capo: int | None = None
    capo_text: str = ""
    tempos: list[dict] = field(default_factory=list)  # {"system", "page", "bpm" (quarter notes), "text"}
    feels: list[dict] = field(default_factory=list)  # {"system", "page", "feel": "eighth"|"sixteenth"|None, "text"}
    notes: list[str] = field(default_factory=list)  # legends that could not be interpreted, or conflict

    @property
    def feel(self) -> str | None:  # the feel the piece starts with (for the summary)
        return next((f["feel"] for f in self.feels if f["feel"]), None)

    @property
    def feel_text(self) -> str:
        return next((f["text"] for f in self.feels if f["feel"]), "")


def _norm_text(s: str) -> str:
    for k, v in _CIRCLED.items():
        s = s.replace(k, f"({v})")
    s = re.sub(r"(\d)\s*½", r"\1 1/2", s).replace("½", "1/2")
    return s.replace("♭", "b").replace("♯", "#").replace("–", "-").replace("—", "-")


def _amount(s: str) -> Fraction | None:
    s = s.strip()
    if not s:
        return Fraction(1)
    m = re.fullmatch(r"(?:(?P<int>\d+|an?|one|two|three|four|five)\s*)?(?P<and>and\s+(?:a\s+)?)?(?P<frac>1/2|half)?", s)
    if not m:
        return None
    i, frac, conj = m.group("int"), m.group("frac"), m.group("and")
    if frac:
        if i is None or (i in ("a", "an", "one") and not conj):
            return Fraction(1, 2)  # "1/2", "a half", "one half"
        return Fraction(int(i) if i.isdigit() else _NUMBER[i]) + Fraction(1, 2)  # "1 1/2", "one and a half"
    return Fraction(int(i) if i.isdigit() else _NUMBER[i]) if i else Fraction(1)


def _unit_semis(u: str) -> int:
    return 1 if re.match(r"semitone|half", u) else 2


def _shift_down(low: str) -> tuple[int | None, bool]:
    """(semitones, seen): how far the legend says to tune down; seen=True with None = uninterpretable."""
    seen = False
    for pat, ctx in _SHIFT_DOWN:
        for m in pat.finditer(low):
            if ctx and not _TUNING_CONTEXT.search(low):
                continue
            seen = True
            amt = _amount(m.group("amt"))
            if amt is None:
                continue
            semis = amt * _unit_semis(m.group("unit"))
            if semis.denominator == 1 and 0 < semis <= 7:
                return int(semis), True
    if re.search(r"\b(?:tune[ds]?|tuned)\b[^.;]*\bdown\b|\bdown\b[^.;]*\btun(?:e|ing)\b", low):
        seen = True
    return None, seen


def parse_legends(items: list, n_strings: int = 6) -> Legends:
    """Interpret the legend texts the staff reader transcribed. Deterministic: the reader only
    copies what is printed; this decides what it means. Anything about tuning / capo that cannot
    be interpreted, or that contradicts itself, goes to `notes` (and stops reconcile)."""
    lg = Legends()
    std = [parse_pitch_name(x) for x in DEFAULT_TUNINGS.get(n_strings, DEFAULT_TUNINGS[6]).split(",")]
    claims: list[tuple[tuple[int, ...], str]] = []
    capos: list[tuple[int, str]] = []
    for it in items or []:
        if not isinstance(it, dict):
            raise InputError(f"legends: each entry must be an object, got {it!r}")
        text = _norm_text(str(it.get("text", "")).strip())
        kind = str(it.get("kind", "other")).lower()
        entry = {**it, "text": text}
        lg.texts.append(entry)
        low = text.lower()
        system, page = it.get("system"), it.get("page")
        # ---- tuning
        down, down_seen = _shift_down(low)
        km = _KEY_STANDARD.search(low)
        if down is None and km:
            letter, acc = (km.group(1), km.group(2)) if km.group(1) else (km.group(3), km.group(4))
            pc = (LETTER_PC[letter.upper()] + {"#": 1, "b": -1}.get(acc or "", 0)) % 12
            d = (4 - pc) % 12  # semitones below E
            if d <= 5:
                down, down_seen = d, True
        pairs = _PAIR.findall(" " + low)
        named_key = next((k for k in _NAMED_TUNINGS
                          if re.search(rf"\b{re.escape(k)}(?![#b\w])", low)
                          and (k.startswith(("drop", "dadgad")) or kind == "tuning" or re.search(r"\btuning\b", low))),
                         None)
        listed = _LISTED.search(low)
        tuning_here = None
        if pairs and (kind == "tuning" or _TUNING_ISH.search(low) or len(pairs) >= 3):
            pcs = {int(s): (LETTER_PC[letter.upper()] + {"#": 1, "sharp": 1, "b": -1, "flat": -1}.get(acc or "", 0)) % 12
                   for s, letter, acc in pairs}
            base = [parse_pitch_name(x) for x in _NAMED_TUNINGS[named_key].split(",")] if named_key else std
            tuning_here = _tuning_from_pcs(pcs, base, down or 0)
        elif listed:
            names = re.findall(r"[a-g][#b]?", listed.group(1))
            if len(names) == n_strings:
                pcs = {n_strings - i: (LETTER_PC[nm[0].upper()] + {"#": 1, "b": -1}.get(nm[1:], 0)) % 12
                       for i, nm in enumerate(names)}
                tuning_here = _tuning_from_pcs(pcs, std, down or 0)
            else:
                lg.notes.append(f"legend {text!r}: lists {len(names)} string notes for a {n_strings}-string tab")
        elif named_key:
            tuning_here = [parse_pitch_name(x) - (down or 0) for x in _NAMED_TUNINGS[named_key].split(",")]
            if len(tuning_here) != n_strings:
                tuning_here = None
        elif down:
            tuning_here = [p - down for p in std]
        elif re.search(r"\bstandard\s+tuning\b", low) or (kind == "tuning" and re.fullmatch(r"\s*standard\s*\.?\s*", low)):
            tuning_here = list(std)
        if down:
            if lg.tune_down and lg.tune_down != down:
                lg.notes.append(f"legends disagree on how far to tune down ({lg.tune_down} vs {down} semitones)")
            lg.tune_down = down
        if tuning_here is not None:
            claims.append((tuple(tuning_here), text))
            # several tunings in ONE legend ("Gtr. 1: standard. Gtr. 2: drop D") cannot be applied blindly
            others = [k for k in _NAMED_TUNINGS if re.search(rf"\b{re.escape(k)}(?![#b\w])", low)]
            if (re.search(r"\bstandard\b", low) and (named_key or pairs)) or len(others) > 1:
                lg.notes.append(f"legend {text!r} names more than one tuning (one per guitar?)")
        elif (kind == "tuning" or _TUNING_ISH.search(low)) and (down_seen or kind == "tuning"):
            lg.notes.append(f"legend {text!r}: could not interpret this tuning legend")
        # ---- capo
        if re.search(r"\bcapo\b", low) and not re.search(r"\bcapo\b[^.;]*\boptional\b|\bno capo\b", low):
            cm = re.search(r"\bcapo\b[^.;!?\n]*?(?:\b(\d{1,2})(?:st|nd|rd|th)?\b|\b([ivx]{1,4})\b|\b("
                           + "|".join(_ORDINAL) + r")\b)", low)
            if cm:
                v = int(cm.group(1)) if cm.group(1) else (_ROMAN[cm.group(2)] if cm.group(2) else _ORDINAL[cm.group(3)])
                capos.append((v, text))
            else:
                lg.notes.append(f"legend {text!r}: mentions a capo but no fret")
        # ---- tempo (converted to quarter notes per minute)
        bpm = it.get("bpm")
        mm = re.search(r"=\s*(\d{2,3})\b", text)
        if bpm is None and kind == "tempo" and mm:
            bpm = int(mm.group(1))
        if bpm is not None:
            try:
                bpm = int(bpm)
            except (TypeError, ValueError):
                raise InputError(f"legend {text!r}: bpm {bpm!r} is not a number") from None
            unit, beat_text = Fraction(1), str(it.get("beat") or "")
            if not beat_text and mm:
                beat_text = text[max(0, mm.start() - 25):mm.start()]
            for pat, val in _BEATS:
                if re.search(pat, beat_text.lower() if it.get("beat") else beat_text):
                    unit = val
                    break
            q_bpm = bpm * unit
            lg.tempos.append({"system": system, "page": page, "bpm": int(round(q_bpm)), "text": text,
                              "beat": str(unit) if unit != 1 else "quarter"})
        # ---- feel (a later "straight 8ths" ends it)
        if re.search(r"\bshuffle\b|\bswing\b|\bswung\b|triplet feel|\bstraight\b|\bno (?:swing|shuffle)\b", low) \
                or (kind == "feel" and re.search(r"triplet|=", low)):
            straight = re.search(r"\bstraight\b|\bno (?:swing|shuffle)\b|non-?swing", low)
            feel = None if straight else ("sixteenth" if re.search(r"16th|sixteenth", low) else "eighth")
            lg.feels.append({"system": system, "page": page, "feel": feel, "text": text})
    distinct = {c for c, _ in claims}
    if len(distinct) > 1:
        lg.notes.append("tuning legends disagree: " + " | ".join(t for _, t in claims))
    if claims:
        lg.tuning = list(claims[0][0])
        lg.tuning_text = claims[0][1]
    if len({c for c, _ in capos}) > 1:
        lg.notes.append("capo legends disagree: " + " | ".join(t for _, t in capos))
    if capos:
        lg.capo, lg.capo_text = capos[0]
    return lg


def _tuning_from_pcs(pcs: dict[int, int], base: list[int], tune_down: int) -> list[int]:
    """String pitch classes from a legend -> MIDI notes, each string's octave chosen nearest to its
    pitch in the base tuning shifted by the tune-down (ties go DOWN: guitars are retuned down far
    more often than up). Strings the legend does not name keep the shifted base pitch."""
    n = len(base)
    out = []
    for i, b in enumerate(base):
        string = n - i
        ref = b - tune_down
        if string not in pcs:
            out.append(ref)
            continue
        pc = pcs[string]
        cands = [o * 12 + pc for o in range(0, 10)]
        out.append(min(cands, key=lambda c: (abs(c - ref), c)))
    return out


FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _spell(m: int, flats: bool) -> str:
    return f"{(FLAT_NAMES if flats else SHARP_NAMES)[m % 12]}{m // 12 - 1}"


def legend_flags(lg: Legends, n_strings: int) -> tuple[str | None, int | None, int]:
    """(tuning spec or None, capo or None, notation transpose) the legend implies, using the
    Hal Leonard conventions: a 'tune down' book prints notation AND tab as if in the un-lowered
    tuning (so the notation must be transposed down too); a capo piece prints the notation in the
    fingered key with the tab counted from the capo (so the notation is transposed up by the capo).
    A non-uniform alternate tuning (DADGAD, drop D, open G) is printed at sounding pitch."""
    spec = None
    if lg.tuning is not None and len(lg.tuning) == n_strings:
        flats = lg.tune_down > 0 or bool(re.search(r"=\s*[A-Ga-g]\s*b|flat|\b[A-Ga-g]b\b", lg.tuning_text))
        spec = ",".join(_spell(p, flats) for p in lg.tuning)
    transpose = -lg.tune_down + (lg.capo or 0)
    return spec, lg.capo, transpose


PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
_PLACEHOLDER = re.compile(r"<[^<>\n]{3,}>")


def _canary_norm(s) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _norm_text(str(s)).lower()).strip()


def prompt_example_texts(prompts_dir: Path | None = None) -> dict[str, set[str]]:
    """The example / placeholder strings the reader prompts ship, normalised (lower case, punctuation
    and spacing ignored). 'placeholders': every <...> in any prompt. 'legend': the placeholders, every
    "text" value in the prompts' examples and every quoted phrase of two or more words in the staff
    prompt's Legends section. A transcribed legend equal to one of them was copied from the prompt,
    not read from the page."""
    ph: set[str] = set()
    legend: set[str] = set()
    for f in sorted((prompts_dir or PROMPTS_DIR).glob("*.md")):
        s = f.read_text()
        ph.update(_canary_norm(x) for x in _PLACEHOLDER.findall(s))
        legend.update(_canary_norm(x) for x in re.findall(r'"text":\s*"([^"\n]+)"', s))
        if f.name.startswith("staff"):
            m = re.search(r"^## Legends.*?(?=^## )", s, re.M | re.S)
            if m:
                legend.update(_canary_norm(x) for x in re.findall(r'"([^"\n]+)"', m.group(0))
                              if len(_canary_norm(x).split()) >= 2)
    ph = {x for x in ph if len(x.replace(" ", "")) >= 3}
    legend = {x for x in legend | ph if len(x.replace(" ", "")) >= 3}
    return {"placeholders": ph, "legend": legend}


def check_copied_from_prompt(legends_raw: list, reads: dict[str, dict]) -> None:
    """Canary: refuse legends, part labels and section names that are an example or placeholder of the
    reader prompts. A copied tuning / capo legend would make the legend check ENFORCE a wrong tuning --
    a silent wrong file the note-by-note cross-check cannot catch (both reads shift together)."""
    ex = prompt_example_texts()

    def copied(text, pool: set[str]) -> str | None:
        n = _canary_norm(text)
        if n and n in pool:
            return n
        return next((_canary_norm(x) for x in _PLACEHOLDER.findall(str(text)) if _canary_norm(x) in ex["placeholders"]),
                    None)

    for it in legends_raw:
        text = it.get("text", "") if isinstance(it, dict) else it
        hit = copied(text, ex["legend"])
        if hit:
            raise InputError(f"legend {str(text)!r} (page {it.get('page') if isinstance(it, dict) else '?'}) looks "
                             f"copied from the prompt, not read from the page: it is the prompt's example / "
                             f"placeholder {hit!r}. Tuning and capo are taken from the legends unchecked, so this "
                             "is never guessed around: re-dispatch the staff reader (do not hand-edit its output) "
                             "and check its legends against the page header / footer crops yourself.")
    for side, systems in reads.items():
        for sid, sysd in systems.items():
            for key in ("part_label", "section"):
                v = sysd.get(key) if isinstance(sysd, dict) else None
                if v and copied(v, ex["placeholders"]):
                    raise InputError(f"{side} read, {sid}: {key} {str(v)!r} looks copied from the prompt, not read "
                                     "from the page (it is one of the prompt's placeholders). Part labels decide the "
                                     f"tracks: re-dispatch the {side} reader.")


def load_legends(work: Path) -> list:
    """Legends from the staff reads, in the same order as load_reads (re-reads last): a later file
    that lists legends for a page replaces the earlier legends of that page."""
    files = sorted(glob.glob(str(work / "reads" / "staff*.json")),
                   key=lambda f: ("reread" in Path(f).name, _natural_key(f)))
    by_page: dict = {}
    order: list = []
    for f in files:
        try:
            data = json.loads(Path(f).read_text())
        except json.JSONDecodeError:
            continue  # load_reads reports it
        items = data.get("legends", []) if isinstance(data, dict) else []
        if not isinstance(items, list):
            raise InputError(f"{Path(f).name}: 'legends' must be a list")
        pages: dict = {}
        for it in items:
            pages.setdefault(it.get("page") if isinstance(it, dict) else None, []).append(it)
        for pg, lst in pages.items():
            if pg not in by_page:
                order.append(pg)
            by_page[pg] = lst
    return [it for pg in order for it in by_page[pg]]


# ------------------------------------------------------------------ model


@dataclass
class SNote:  # a written note from the staff read, resolved to sounding pitch
    midi: int
    tie: bool
    desc: str
    ghost: bool = False
    tab: "TNote | None" = None  # the TAB digit it was matched with (None: tied note not reprinted)
    origin: tuple[int, int] | None = None  # tied note: (string, fret) of the note it continues
    cross_voice: bool = False  # tied over from the OTHER voice (a GP tie cannot change voice)


@dataclass
class SEvent:
    rest: bool
    dur: int
    dots: int
    tuplet: int  # 1 = none
    notes: list[SNote]
    idx: int  # 1-based index in the bar's events
    voice: int = 1
    onset: Fraction = Fraction(0)  # in quarter notes from the start of the bar, within its voice

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
class OGroup:
    """Everything the staff attacks at one moment: one event per voice (one event when single-voice)."""
    onset: Fraction
    events: list[SEvent]

    @property
    def notes(self) -> list[SNote]:
        return [n for e in self.events for n in e.notes]

    def cross_voice_unisons(self) -> set[int]:
        seen: dict[int, set[int]] = {}
        for e in self.events:
            for n in e.notes:
                if not n.tie and not n.ghost:
                    seen.setdefault(n.midi, set()).add(e.voice)
        return {p for p, vs in seen.items() if len(vs) > 1}


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
    part: int = 1
    group: str = ""
    aligned: bool = False
    match: dict = field(default_factory=dict)  # event idx -> TCol or None (rest / omitted tie)

    def voice_events(self, v: int) -> list[SEvent]:
        return [e for e in self.events if e.voice == v]

    @property
    def voices(self) -> list[int]:
        return sorted({e.voice for e in self.events}) or [1]

    def voice_beats(self, v: int) -> Fraction:
        return sum((e.beats for e in self.voice_events(v)), Fraction(0))

    @property
    def beats(self) -> Fraction:  # the bar's length = its first voice's length
        return self.voice_beats(self.voices[0])

    @property
    def where(self) -> str:
        return f"{self.system} bar {self.bar}"

    def groups(self) -> list[OGroup]:
        """Pitched events grouped by onset (across voices), in time order."""
        by: dict[Fraction, list[SEvent]] = {}
        for e in self.events:
            if not e.rest:
                by.setdefault(e.onset, []).append(e)
        return [OGroup(on, sorted(evs, key=lambda e: e.voice)) for on, evs in sorted(by.items())]


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


def read_adjudications(work: Path) -> list[dict]:
    path = work / "adjudications.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    items = data.get("adjudications", data) if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise InputError("adjudications.json: want {\"adjudications\": [...]}")
    return items


def apply_adjudications(work: Path, tab: dict, staff: dict) -> list[dict]:
    items = read_adjudications(work)
    for k, a in enumerate(items, start=1):
        if not str(a.get("reason", "")).strip():
            raise InputError(f"adjudication #{k} has no reason -- every edit must say why")
        kind = a.get("kind")
        if kind is not None and kind not in ADJ_KINDS:
            raise InputError(f"adjudication #{k}: kind must be 'glyph' (a reader misread a glyph) or "
                             f"'interpretation' (the notation cannot be represented as printed), got {kind!r}")
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

    Events may belong to voice 1 (default) or voice 2 ("voice": 2, stems down); each voice's
    events are listed left to right and each voice fills the bar on its own.

    Accidentals: a printed accidental holds for the rest of the bar at that staff position
    (letter+octave), for both voices, in TIME order. A tied-over note with no printed accidental
    keeps the alteration of its origin in the same voice (prev_alter carries each voice's last
    alterations across the barline).
    """
    events = bar.get("events", [])
    if not isinstance(events, list):
        raise InputError(f"{sid} bar {bn}: 'events' must be a list")
    raw = []  # (event, [(letter, octave, acc, tie, ghost)], where)
    onset_of: dict[int, Fraction] = {}
    out: list[SEvent] = []
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
            raise InputError(f"{where}: grace notes are not supported -- leave them out of 'events' and record "
                             "them in the bar's 'grace' list")
        try:
            voice = int(ev.get("voice", 1) or 1)
        except (TypeError, ValueError):
            raise InputError(f"{where}: voice must be 1 or 2") from None
        if voice not in (1, 2):
            raise InputError(f"{where}: voice must be 1 or 2 (got {voice})")
        se = SEvent(bool(ev.get("rest")), DUR_VALUES[dur], dots, tup, [], i, voice)
        on = onset_of.get(voice, Fraction(0))
        se.onset = on
        onset_of[voice] = on + se.beats
        out.append(se)
        if se.rest:
            continue
        rn = ev.get("chord") or [ev]
        if not isinstance(rn, list):
            raise InputError(f"{where}: 'chord' must be a list of notes")
        notes = []
        for n in rn:
            if not isinstance(n, dict):
                raise InputError(f"{where}: chord note must be an object, got {n!r}")
            letter = str(n.get("letter", "")).upper()
            if letter not in LETTER_PC:
                raise InputError(f"{where}: bad letter {n.get('letter')!r}")
            try:
                octv = int(n["octave"])
            except (KeyError, TypeError, ValueError):
                raise InputError(f"{where}: bad or missing octave") from None
            acc = str(n.get("acc", "none")).strip().lower()
            if acc in ("none", "", "null"):
                acc = "none"
            if acc != "none" and acc not in ACC:
                raise InputError(f"{where}: bad acc {n.get('acc')!r} (sharp/flat/natural/double-sharp/double-flat/none)")
            notes.append((letter, octv, acc, bool(n.get("tie", False)), bool(n.get("ghost", False))))
        raw.append((se, notes))

    # resolve alterations in time order (same moment: the note with a printed accidental first)
    carried: dict[tuple[str, int], int] = {}
    last_alter = {v: dict(prev_alter.get(("voice", v), {})) for v in (1, 2)}
    at_start = {v: dict(a) for v, a in last_alter.items()}
    first_of_voice = {}
    for se in out:
        first_of_voice.setdefault(se.voice, se.idx)
    order = sorted(raw, key=lambda r: (r[0].onset, 0 if any(n[2] != "none" for n in r[1]) else 1, r[0].voice,
                                       r[0].idx))
    for se, notes in order:
        this_alter = {}
        other = 2 if se.voice == 1 else 1
        for letter, octv, acc, tie, ghost in notes:
            pos = (letter, octv)
            if acc != "none":
                alter = ACC[acc]
                carried[pos] = alter
            elif tie and pos in last_alter[se.voice]:
                alter = last_alter[se.voice][pos]
            elif tie and first_of_voice.get(se.voice) == se.idx and pos in at_start[other]:
                alter = at_start[other][pos]  # a held note that changes voice across the barline
            else:
                alter = carried.get(pos, keymap.get(letter, 0))
            this_alter[pos] = alter
            written = 12 * (octv + 1) + LETTER_PC[letter] + alter
            accs = "" if acc == "none" else f" ({acc})"
            se.notes.append(SNote(written + offset, tie,
                                  f"{letter}{octv}{accs}{' tied' if tie else ''}{' ghost' if ghost else ''}", ghost))
        last_alter[se.voice] = this_alter
    prev_alter.clear()
    present = {se.voice for se in out}
    for v, la in last_alter.items():  # a voice that is silent for a whole bar holds nothing into the next
        prev_alter[("voice", v)] = la if (v in present or not out) else {}
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
                raise InputError(f"{where}: fret {f!r} is not a number (dead notes 'x' go in the bar's 'dead' list)")
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


def col_matches(ev, col: TCol, plain_ties: bool = False) -> bool:
    """Plain tab digits <-> normal staff notes; parenthesized digits <-> tied or ghost staff notes.

    `ev` is one staff event or an OGroup (all voices attacking at one moment).
    - every normal (untied, non-ghost) staff note must be a PLAIN tab digit, and vice versa;
    - a unison between two voices (both voices on one pitch) may be one tab digit or two;
    - every ghost staff note must be a parenthesized tab digit;
    - a tied staff note may be absent from the tab or parenthesized (with --tab-reprints-ties
      it may also be a plain digit); a plain digit is otherwise a re-attack, not a tie.
    """
    notes = ev.notes
    normal = Counter(n.midi for n in notes if not n.tie and not n.ghost)
    tied = Counter(n.midi for n in notes if n.tie)
    ghost = Counter(n.midi for n in notes if n.ghost and not n.tie)
    plain = Counter(n.midi for n in col.notes if not n.paren)
    paren = Counter(n.midi for n in col.notes if n.paren)
    if isinstance(ev, OGroup):
        for p in ev.cross_voice_unisons():  # one shared notehead / two noteheads <-> one or two digits
            if 1 <= plain[p] < normal[p]:
                normal[p] = plain[p]
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


def align(events, cols: list[TCol], plain_ties: bool = False):
    """DP alignment: each pitched event (or onset group) matches one column, or (if fully tied) none.
    Returns {event idx: TCol | None} or None."""
    ev = [e for e in events if isinstance(e, OGroup) or not e.rest]
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
        members = ev[i].events if isinstance(ev[i], OGroup) else [ev[i]]
        if j < m and col_matches(ev[i], cols[j], plain_ties) and ok[i + 1][j + 1]:
            for e in members:
                match[e.idx] = cols[j]
            i, j = i + 1, j + 1
        else:
            for e in members:
                match[e.idx] = None
            i += 1
    return match


def assign_tab(b: Bar) -> None:
    """Give every matched staff note its TAB digit: attacks first (plain digits; ghost notes the
    parenthesized ones), then tied notes take what is left (a parenthesized / reprinted digit, or
    nothing). A unison between two voices printed as one digit serves both."""
    for g in b.groups():
        col = b.match.get(g.events[0].idx)
        if col is None:
            continue
        avail = list(col.notes)

        def take(sn, want_paren):
            cands = [x for x in avail if x.midi == sn.midi]
            cands.sort(key=lambda x: x.paren != want_paren)
            if cands:
                avail.remove(cands[0])
                return cands[0]
            return None

        attacks = [sn for e in g.events for sn in e.notes if not sn.tie]
        for sn in attacks:
            sn.tab = take(sn, sn.ghost)
        for sn in attacks:
            if sn.tab is None:  # cross-voice unison printed once
                sn.tab = next((x for x in col.notes if x.midi == sn.midi and not x.paren), None)
        for e in g.events:
            for sn in e.notes:
                if sn.tie:
                    sn.tab = take(sn, True)


def _fmt_tab(c: TCol) -> str:
    return "+".join(f"(string {n.string}, fret {n.fret}{', paren' if n.paren else ''})={midi_name(n.midi)}[{n.midi}]"
                    for n in c.notes)


def _fmt_staff(e: SEvent) -> str:
    return "+".join(f"{n.desc}={midi_name(n.midi)}[{n.midi}]" for n in e.notes)


def _fmt_group(g: OGroup) -> str:
    if len(g.events) == 1:
        e = g.events[0]
        return f"staff event #{e.idx} {_fmt_staff(e)}"
    return ("staff events " + "+".join(f"#{e.idx}" for e in g.events)
            + f" (voices {'+'.join(str(e.voice) for e in g.events)}) "
            + "  +  ".join(_fmt_staff(e) for e in g.events))


def _pair_offsets(g: OGroup, c: TCol) -> list[int]:
    """TAB minus staff pitch per note, when both sides have the same number of sounding notes."""
    s = sorted(n.midi for n in g.notes if not n.tie)
    t = sorted(n.midi for n in c.notes)
    return [b - a for a, b in zip(s, t)] if s and len(s) == len(t) else []


def describe_mismatch(b: Bar, plain_ties: bool, extra: str = "") -> tuple[list[str], list[int]]:
    groups = b.groups()
    out, offsets = [], []
    if len(groups) == len(b.cols):
        for g, c in zip(groups, b.cols):
            offsets.extend(_pair_offsets(g, c))
            if not col_matches(g, c, plain_ties):
                why = ""
                if Counter(n.midi for n in g.notes) == Counter(n.midi for n in c.notes):
                    why = "  (same pitches -- tie/paren markings disagree: a tied note must be absent or " \
                          "parenthesized in the tab, a parenthesized digit must be tied or ghost in the staff)"
                out.append(f"{b.where}: tab entry #{c.idx} {_fmt_tab(c)}  vs  {_fmt_group(g)}{why}{extra}")
        if out:
            return out, offsets
    tab_s = "  ".join(f"#{c.idx}:{_fmt_tab(c)}" for c in b.cols)
    stf_s = "  ".join(("#" + "+#".join(str(e.idx) for e in g.events) + ":" + "+".join(_fmt_staff(e) for e in g.events))
                      for g in groups)
    what = "pitched events" if len(b.voices) == 1 else "attack moments (both voices merged)"
    out.append(f"{b.where}: tab has {len(b.cols)} columns, staff has {len(groups)} {what} "
               f"(tied-only events may be omitted in tab) -- no consistent alignment{extra}\n"
               f"      tab   {tab_s}\n      staff {stf_s}")
    return out, offsets


def _parse_part(sids: list[str], tab: dict, staff: dict, info: dict, tuning: dict[int, int], capo: int,
                offset: int, unverified: bool, plain_ties: bool, ctx: dict) -> list[Bar]:
    """Reconcile the systems of one part (in order) into bars."""
    problems, warnings, omitted = ctx["problems"], ctx["warnings"], ctx["omitted"]
    bars: list[Bar] = []
    prev_alter: dict = {}
    cur_ts: tuple[int, int] | None = None
    cur_key = 0
    for sid in sids:
        t, s = tab.get(sid), staff.get(sid)
        meta = info.get(sid, {})
        kind = meta.get("kind", "staff+tab")
        part, group = int(meta.get("part") or 1), meta.get("group") or sid
        if t is None and s is None:
            problems.append(f"{sid}: missing from both reads")
            continue
        if t is None:
            problems.append(f"{sid}: missing from the tab read")
            continue
        for x in (list(t.get("omitted", []) or []) + list((s or {}).get("omitted", []) or [])):
            omitted.append(f"{sid}: {x}")
        _collect_p2(sid, t, s, ctx)
        label = (s or {}).get("part_label") or t.get("part_label")
        if (s or {}).get("part_label") and t.get("part_label") and \
                label_identity(s["part_label"]) != label_identity(t["part_label"]):
            warnings.append(f"{sid}: the readers copied different part labels (staff {s['part_label']!r}, TAB "
                            f"{t['part_label']!r}); the staff reader's decides the track")
        if label:
            ctx["system_labels"][sid] = str(label)
            ctx["part_labels"].setdefault(part, [])
            if str(label) not in ctx["part_labels"][part]:
                ctx["part_labels"][part].append(str(label))
        if s is not None and s.get("tempo"):
            try:
                ctx["system_tempos"].append((sid, int(s["tempo"]), f"{sid} staff read tempo"))
            except (TypeError, ValueError):
                pass
        if s is None:
            if not unverified:
                problems.append(f"{sid}: missing from the staff read")
                continue
            ctx["unverified_systems"].append(sid)
            for bi, tb in enumerate(t["bars"], start=1):
                cols = parse_tab_bar(sid, bi, tb, tuning, capo)
                b = Bar(sid, bi, [], cols, unverified=True, part=part, group=group)
                durs = tb.get("durs")
                if durs and len(durs) == len(cols):
                    b.events = [SEvent(False, DUR_VALUES[str(d)], 0, 1, [SNote(n.midi, False, "tab", tab=n)
                                                                        for n in c.notes], c.idx)
                                for d, c in zip(durs, cols)]
                else:
                    b.events = [SEvent(False, 4, 0, 1, [SNote(n.midi, False, "tab", tab=n) for n in c.notes], c.idx)
                                for c in cols]
                    warnings.append(f"{sid} bar {bi}: rhythm not available -- placeholder quarter notes (UNVERIFIED)")
                on = Fraction(0)
                for e in b.events:
                    e.onset = on
                    on += e.beats
                b.match = {c.idx: c for c in cols}
                ts = parse_time_sig(tb.get("time_sig"))
                cur_ts = ts or cur_ts
                b.ts = cur_ts or (4, 4)
                b.repeat_start = bool(tb.get("repeat_start"))
                b.repeat_end = int(tb.get("repeat_end") or 0)
                if bi == 1 and t.get("section"):
                    b.section = t["section"]
                _collect_techs(sid, bi, tb, ctx["techs"])
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
                    b = Bar(sid, bi, [SEvent(True, 1, 0, 1, [], 1)], [], key=cur_key, part=part, group=group,
                            aligned=True)
                    b.ts = cur_ts if cur_ts else (0, 0)
                    if r == 0 and bi == 1 and (s.get("section") or t.get("section")):
                        b.section = s.get("section") or t.get("section")
                    bars.append(b)
                continue

            events = parse_staff_bar(sid, bi, sb, keymap, offset, prev_alter)
            cols = parse_tab_bar(sid, bi, tb, tuning, capo)
            b = Bar(sid, bi, events, cols, key=cur_key, part=part, group=group)
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
            _collect_techs(sid, bi, tb, ctx["techs"])
            groups = b.groups()
            m = align(groups if len(b.voices) > 1 else events, cols, plain_ties)
            n_grace = len(tb.get("grace") or []) + len(sb.get("grace") or [])
            if m is None:
                extra = ""
                if n_grace:
                    extra = (f"  [this bar has {n_grace} grace note(s) recorded -- a grace digit printed in the same "
                             "column as a chord, or as its own column, is not representable yet]")
                msgs, offs = describe_mismatch(b, plain_ties, extra)
                problems.extend(msgs)
                ctx["offsets"].extend(offs)
                if offs:
                    ctx["bar_offsets"][where] = Counter(offs).most_common(1)[0][0]
            else:
                b.match = m
                b.aligned = True
                assign_tab(b)
                for g in groups:
                    c = m.get(g.events[0].idx)
                    if c is not None:
                        ctx["offsets"].extend(_pair_offsets(g, c))
            bars.append(b)
    return bars


def _resolve_ties(bars: list[Bar]) -> None:
    """Give every tied staff note the string/fret of the note it continues: the previous beat of the
    same voice, or -- for the first note of a voice in a bar -- a note the other voice held at the end
    of the previous bar (a held note that changes voice across the barline). A voice that is silent for
    a whole bar holds nothing. Raises InputError when a tie has no origin or contradicts its TAB digit."""
    last: dict[int, dict[int, tuple[int, int]]] = {1: {}, 2: {}}
    for b in bars:
        start = {v: dict(d) for v, d in last.items()}
        seen: set[int] = set()
        evs = sorted(b.events, key=lambda e: (e.onset, e.voice, e.idx))
        i = 0
        while i < len(evs):
            grp = [e for e in evs[i:] if e.onset == evs[i].onset]
            snap = {v: dict(d) for v, d in last.items()}
            for e in grp:
                first = e.voice not in seen
                seen.add(e.voice)
                if e.rest:
                    last[e.voice] = {}
                    continue
                this = {}
                for sn in e.notes:
                    if sn.tie:
                        origin = snap[e.voice].get(sn.midi)
                        if origin is None and first:
                            origin = start[2 if e.voice == 1 else 1].get(sn.midi)
                            sn.cross_voice = origin is not None
                        if origin is None:
                            raise InputError(f"{b.where} staff event #{e.idx}: tie into {midi_name(sn.midi)} has no "
                                             f"matching note in the previous beat of voice {e.voice}")
                        if sn.tab is not None and (sn.tab.string, sn.tab.fret) != origin:
                            # two digits of one pitch in the column (a reprinted tie + a new attack in the
                            # other voice): give the tie the digit on its own string
                            mate = next((x for g2 in grp for x in g2.notes if not x.tie and x.tab is not None
                                         and x.midi == sn.midi and (x.tab.string, x.tab.fret) == origin), None)
                            if mate is not None:
                                mate.tab, sn.tab = sn.tab, mate.tab
                        if sn.tab is not None and (sn.tab.string, sn.tab.fret) != origin:
                            raise InputError(f"{b.where} staff event #{e.idx}: tied note shown on string "
                                             f"{sn.tab.string} fret {sn.tab.fret} but its origin is string "
                                             f"{origin[0]} fret {origin[1]}")
                        sn.origin = origin
                        this[sn.midi] = origin
                    elif sn.tab is not None:
                        this[sn.midi] = (sn.tab.string, sn.tab.fret)
                last[e.voice] = this
            i += len(grp)
        present = {e.voice for e in b.events}
        for v in (1, 2):
            if v not in present:
                last[v] = {}


def _collect_p2(sid: str, t: dict, s: dict | None, ctx: dict) -> None:
    """Structured records of notation not representable yet (grace notes, dead notes, bend targets):
    counted for the summary, not used for matching."""
    for side, d in (("tab", t), ("staff", s or {})):
        for bi, bar in enumerate(d.get("bars", []) or [], start=1):
            if not isinstance(bar, dict):
                continue
            for key in ("grace", "dead"):
                for item in bar.get(key) or []:
                    ctx["p2"].append({"system": sid, "bar": bi, "side": side, "type": key, "item": item})
            if side == "staff":
                for ev in bar.get("events") or []:
                    if isinstance(ev, dict):
                        for n in ev.get("chord") or [ev]:
                            if isinstance(n, dict) and n.get("bend_target"):
                                ctx["p2"].append({"system": sid, "bar": bi, "side": side, "type": "bend_target",
                                                  "item": n})


def reconcile(work: Path, tuning_spec: str | None, unverified: bool, capo: int = 0, transpose: int = 0,
              plain_ties: bool = False, apply_adj: bool = True, legend_override: str | None = None,
              gap_bars: dict[str, int] | None = None):
    superseded: list[str] = []
    tab = load_reads(work, "tab", superseded)
    staff = load_reads(work, "staff", superseded)
    if not tab:
        raise InputError("no reads/tab*.json found")
    if not staff and not unverified:
        raise InputError("no reads/staff*.json found (a tab-only transcription needs --unverified)")
    adjs = apply_adjudications(work, tab, staff) if apply_adj else []
    order, info = system_order(work, tab, staff)
    offset = -12 + transpose

    mpath = work / "manifest.json"
    man = json.loads(mpath.read_text()) if mpath.exists() else {}
    prep_warn = [f"prepare: {w}" for w in man.get("warnings", [])]
    gaps = resolve_gaps(man, gap_bars or {}, prep_warn)
    ctx: dict = {"problems": [], "warnings": prep_warn + list(superseded), "omitted": [], "techs": [], "offsets": [],
                 "bar_offsets": {}, "unverified_systems": [], "part_labels": {}, "system_labels": {},
                 "system_tempos": [], "p2": []}
    problems, warnings = ctx["problems"], ctx["warnings"]

    part_of = {sid: int(info.get(sid, {}).get("part") or 1) for sid in order}
    parts = sorted(set(part_of.values())) or [1]
    lines_by_part = {p: {info[s].get("tab_lines") for s in order if part_of[s] == p and s in info
                         and info[s].get("tab_lines")} for p in parts}
    tunings: dict[int, dict[int, int]] = {}
    tuning_specs: dict[int, str] = {}
    for p in parts:
        lines = lines_by_part[p]
        if len(lines) > 1:
            raise InputError(f"part {p}: systems have different tab line counts {sorted(lines)}; pass --tuning")
        nl = next(iter(lines)) if lines else None
        if tuning_spec is not None and (nl is None or len(tuning_spec.split(",")) == nl):
            spec = tuning_spec
        elif tuning_spec is not None:
            raise InputError(f"--tuning has {len(tuning_spec.split(','))} strings but part {p}'s tab staves have "
                             f"{nl} lines")
        else:
            nl = nl or 6
            if nl not in DEFAULT_TUNINGS:
                raise InputError(f"no default tuning for {nl}-line tab; pass --tuning")
            spec = DEFAULT_TUNINGS[nl]
            if nl != 6:
                warnings.append(f"part {p}: {nl}-line tab: assumed tuning {spec} (pass --tuning if different)")
        tunings[p] = parse_tuning(spec)
        tuning_specs[p] = spec
        if lines and len(tunings[p]) not in lines:
            raise InputError(f"--tuning has {len(tunings[p])} strings but the tab staves have {sorted(lines)} lines")

    # tuning / capo legend: NOT cross-checkable (both reads shift together), so it must match the flags
    legends_raw = load_legends(work)
    check_copied_from_prompt(legends_raw, {"staff": staff, "tab": tab})
    n_main = len(tunings[parts[0]])
    lg = parse_legends(legends_raw, n_main)
    leg_spec, leg_capo, leg_nt = legend_flags(lg, n_main)
    legend_check = _legend_check(lg, leg_spec, leg_capo, leg_nt, tunings[parts[0]], tuning_spec, capo, transpose,
                                 legend_override)
    warnings.extend(legend_check["warnings"])

    part_bars: dict[int, list[Bar]] = {}
    for p in parts:
        sids = [s for s in order if part_of[s] == p]
        part_bars[p] = _parse_part(sids, tab, staff, info, tunings[p], capo, offset, unverified, plain_ties, ctx)
        _infer_time_sigs(part_bars[p], problems, warnings, f"part {p}: " if len(parts) > 1 else "")
    bounds = None
    if len(parts) > 1 and len({len(part_bars[p]) for p in parts}) == 1:
        # a section label is usually printed over the top part only: it starts a section for every part
        starts, ends = set(), set()
        for p in parts:
            st, en = _section_bounds(part_bars[p])
            starts |= st
            ends |= en
        bounds = (starts, ends)
    for p in parts:
        _check_durations(part_bars[p], problems, bounds)
    if len(parts) > 1:
        _check_parts_agree(part_bars, problems)
    idents = _part_identities(part_bars, ctx, problems, warnings)
    timeline, tracks = [], []
    if not problems:  # only an agreeing transcription gets built; ties must resolve before that
        for p in parts:
            _resolve_ties(part_bars[p])
        timeline, tracks = build_timeline(part_bars, parts, idents, ctx, gaps, tunings, tuning_specs)

    omitted = ctx["omitted"]
    for a in adjs:  # an adjudication may resolve an omitted item (what it drops is reported on its own)
        for r in a.get("resolves_omitted") or []:
            omitted[:] = [o for o in omitted if str(r) not in o]
    structural = [o for o in omitted + [f"{a.get('system')}: {a['drops']}" for a in adjs if a.get("drops")]
                  if STRUCTURE_WORDS.search(o)]
    if structural:
        warnings.append("readers omitted structure markings (voltas / D.S. / Coda / multi-bar rests): the bar order "
                        "in the file follows the page, not the performance -- playback may be wrong: "
                        + "; ".join(structural))
    bars = [b for p in parts for b in part_bars[p]]
    stats = {
        "systems": len(order),
        "bars": len(timeline) if timeline else len(part_bars[parts[0]]),
        "staff_notes": sum(len(e.notes) for b in bars for e in b.events if not e.rest and not b.unverified),
        "tab_notes": sum(len(c.notes) for b in bars for c in b.cols),
        "tab_notes_in_agreeing_bars": _agreeing_tab_notes(bars, problems),
        "tab_notes_raw": _raw_tab_notes(tab, order),
    }
    return {"bars": bars, "parts": [{"part": p, "bars": part_bars[p], "labels": ctx["part_labels"].get(p, []),
                                     "tuning": tunings[p], "tuning_spec": tuning_specs[p]} for p in parts],
            "problems": problems, "warnings": warnings, "techs": ctx["techs"], "omitted": omitted,
            "adjudications": adjs, "tuning": tunings[parts[0]], "tuning_spec": tuning_specs[parts[0]], "stats": stats,
            "unverified_systems": ctx["unverified_systems"], "tempo_read": _tempo(staff), "capo": capo,
            "legends": lg, "legend_check": legend_check, "offsets": ctx["offsets"], "bar_offsets": ctx["bar_offsets"],
            "system_tempos": ctx["system_tempos"], "p2": ctx["p2"], "order": order, "transpose": transpose,
            "timeline": timeline, "tracks": tracks, "gaps": gaps}


def _raw_tab_notes(tab: dict, order: list[str]) -> int:
    """Every digit the TAB reader recorded, whether or not its system could be reconciled."""
    n = 0
    for sid in order:
        for bar in (tab.get(sid) or {}).get("bars", []) or []:
            for entry in (bar.get("notes") or []) if isinstance(bar, dict) else []:
                n += len(entry) if (isinstance(entry, list) and entry and isinstance(entry[0], list)) else 1
    return n


def _agreeing_tab_notes(bars: list[Bar], problems: list[str]) -> int:
    """TAB digits in bars that reconciled with no problem of any kind (pitch, rhythm, partial, repeats,
    system- or group-level)."""
    keys: set[str] = set()
    global_problem = False
    for p in problems:
        head = p.split(":", 1)[0]
        items = [h.strip() for h in head.split(" / ")]
        if not all(re.match(r"(p\d+_[sg]\d+(?: bar \d+)?|part \d+)$", h) for h in items):
            global_problem = True
        keys.update(items)
    if global_problem:
        return 0
    n = 0
    for b in bars:
        if b.unverified or not b.aligned:
            continue
        if b.where in keys or b.system in keys or b.group in keys or f"part {b.part}" in keys:
            continue
        n += sum(len(c.notes) for c in b.cols)
    return n


def _legend_check(lg: Legends, leg_spec, leg_capo, leg_nt, tuning, tuning_spec, capo, transpose, override) -> dict:
    out = {"legend_tuning": leg_spec, "legend_capo": leg_capo, "legend_notation_transpose": leg_nt,
           "warnings": [], "override": override}
    if lg.notes:
        if not override:
            raise LegendError(
                "a tuning / capo legend could not be interpreted with certainty: " + "; ".join(lg.notes)
                + ". Tuning and capo cannot be cross-checked, so they are not guessed: read the legend on the page, "
                "pass --tuning / --capo / --notation-transpose yourself, and add --legend-override \"<what you "
                "checked>\".", "")
        out["warnings"].append("LEGEND OVERRIDDEN (" + override + "): " + "; ".join(lg.notes))
    if not (lg.tuning_text or lg.capo_text):
        return out
    want = []
    if leg_spec is not None:
        want.append(f"--tuning {leg_spec}")
    if leg_capo:
        want.append(f"--capo {leg_capo}")
    if leg_nt:
        want.append(f"--notation-transpose {leg_nt}")
    flags = " ".join(want)
    out["flags"] = flags
    have_spec = tuning_spec_of(tuning)
    bad = []
    if leg_spec is not None and [parse_pitch_name(x) for x in leg_spec.split(",")] != \
            [tuning[s] for s in sorted(tuning, reverse=True)]:
        bad.append(f"tuning {have_spec} (flags) vs {leg_spec} (legend {lg.tuning_text!r})")
    if (leg_capo or 0) != capo:
        bad.append(f"capo {capo} (flags) vs {leg_capo} (legend {lg.capo_text!r})")
    if bad and not override:
        raise LegendError(
            "the page legend contradicts the flags: " + "; ".join(bad) + ". Tuning and capo cannot be "
            "cross-checked (both reads shift together), so they must come from the legend. Re-run with:  "
            + flags + "  (Hal Leonard convention: a 'tune down' book prints notation and TAB as if in standard tuning, "
            "so the notation is transposed down with it; a capo piece prints the notation in the fingered key, so the "
            "notation is transposed up by the capo. If this book prints sounding pitch instead, use "
            "--notation-transpose 0 -- the cross-check will tell. If the legend does not apply to this part, pass "
            "--legend-override \"<why>\".)", flags)
    if bad:
        out["warnings"].append("LEGEND OVERRIDDEN (" + override + "): " + "; ".join(bad))
    elif leg_nt != transpose:
        out["warnings"].append(f"--notation-transpose {transpose} differs from the book convention for this legend "
                               f"({leg_nt}); the note-by-note cross-check decides which is right")
    return out


def _check_parts_agree(part_bars: dict[int, list[Bar]], problems: list[str]) -> None:
    """Stacked parts share one timeline: same bars per system group, same time signatures and
    pickups, same repeats."""
    parts = sorted(part_bars)
    ref = part_bars[parts[0]]
    for p in parts[1:]:
        other = part_bars[p]
        by_group_ref = Counter(b.group for b in ref)
        by_group = Counter(b.group for b in other)
        for g in sorted(set(by_group_ref) | set(by_group)):
            if by_group_ref.get(g, 0) != by_group.get(g, 0):
                problems.append(f"{g}: stacked parts have different bar counts (part {parts[0]}: "
                                f"{by_group_ref.get(g, 0)}, part {p}: {by_group.get(g, 0)}) -- a reader miscounted a "
                                "barline, or these staves are not parts played together")
        if len(ref) != len(other):
            continue
        for a, b in zip(ref, other):
            if a.beats != b.beats:
                problems.append(f"{a.where} / {b.where}: stacked parts' bars have different lengths ({a.beats} vs "
                                f"{b.beats} quarter notes) -- they cannot share one timeline")
            if a.ts != b.ts:
                problems.append(f"{a.where} / {b.where}: stacked parts disagree on the time signature "
                                f"({a.ts[0]}/{a.ts[1]} vs {b.ts[0]}/{b.ts[1]})")
            if a.partial != b.partial:
                problems.append(f"{a.where} / {b.where}: stacked parts disagree on whether this is a partial bar")
            if (a.repeat_start, a.repeat_end) != (b.repeat_start, b.repeat_end):
                problems.append(f"{a.where} / {b.where}: stacked parts disagree on repeat signs")


_ROMAN_NUM = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6}
_INSTRUMENT = r"\b(?:gtrs?|guitars?|bass(?:es)?|bs)\b\.?"


def label_identity(label) -> tuple | None:
    """Which part a printed part label names: (instrument, numbers) -- 'Gtr. 1', '*Elec. Gtr. 1 (w/ dist.)'
    and 'Gtr. I' are one part, 'Gtr. 2' another, 'Gtrs. 1 & 2' a third; without numbers, (instrument,
    qualifier words) -- 'Acous. Gtr.' is not 'Elec. Gtr.'. ('bare', instrument) for a bare instrument name
    ('Gtr.', '(Gtr.)'): it names no part of its own. Footnote marks and remarks in parentheses are ignored."""
    s = _norm_text(str(label)).lower()
    s = re.sub(r"[*†‡]", " ", s)
    m = re.fullmatch(r"\s*\((.*)\)\s*", s)
    if m:
        s = m.group(1)
    s = re.sub(r"\([^)]*\)", " ", s)
    family = "bass" if re.search(r"\bbass|\bbs\b", s) else ("gtr" if re.search(r"\bgtrs?\b|\bguitars?\b", s) else None)
    nums: set[int] = set()
    for mm in re.finditer(_INSTRUMENT + r"\s*((?:(?:\d{1,2}|[iv]{1,3})\b\s*(?:,|&|and|/|\+)?\s*(?:" + _INSTRUMENT
                          + r"\s*)?)+)", s):
        for tok in re.findall(r"\b(\d{1,2}|[iv]{1,3})\b", mm.group(1)):
            nums.add(int(tok) if tok.isdigit() else _ROMAN_NUM.get(tok, 0))
    if nums:
        return (family or "part", tuple(sorted(nums)))
    words = sorted(set(re.findall(r"[a-z][a-z0-9-]+", re.sub(_INSTRUMENT, " ", s))) - {"and", "w"})
    if not words:
        return ("bare", family) if family else ("label", s.strip())
    return (family or "part", tuple(words))


def _part_identities(part_bars: dict[int, list[Bar]], ctx: dict, problems: list[str],
                     warnings: list[str]) -> dict[int, dict[str, tuple]]:
    """The part each system of each stacked-part slot belongs to, from the printed part labels: a system
    without a label continues the part above it; a bare instrument name ('Gtr.') continues it too (with
    a warning); a label naming another part starts another track on the common timeline. Labels that
    ALTERNATE within one slot (1, 2, 1) are refused: that is how stacked parts printed one over the other
    look when they were read as consecutive systems, and they would be interleaved."""
    out: dict[int, dict[str, tuple]] = {}
    for p, bars in part_bars.items():
        cur: tuple | None = None
        first_label: dict[tuple, str] = {}
        seq = []
        out[p] = {}
        for sid in dict.fromkeys(b.system for b in bars):
            lab = ctx["system_labels"].get(sid)
            if not lab:
                out[p][sid] = cur if cur is not None else ("unlabelled",)
                cur = out[p][sid]
                continue
            idn = label_identity(lab)
            if idn[0] == "bare" and cur is not None and cur != ("unlabelled",) and cur != idn \
                    and cur[0] in (idn[1], "part", "label"):
                warnings.append(f"{sid}: part label {lab!r} names no part number -- read as a continuation of "
                                f"{first_label[cur]!r} (the same track)")
                idn = cur
            if cur == ("unlabelled",) and idn != cur:
                warnings.append(f"{sid}: part label {lab!r} is the first label of this part; the unlabelled systems "
                                "before it are written as a track of their own")
            first_label.setdefault(idn, lab)
            out[p][sid] = idn
            seq.append((sid, lab, idn))
            cur = idn
        ids = [i for _, _, i in seq]
        if any(ids[i] != ids[i + 1] and ids[i] in ids[i + 2:] for i in range(len(ids) - 1)):
            problems.append(f"part {p}: the systems' part labels alternate ({', '.join(f'{s} {lab!r}' for s, lab, _ in seq)})"
                            " -- stacked parts printed one over the other would be interleaved into one track. Run "
                            "prepare.py so manifest.json keeps the parts apart (check its contact sheet), or ask the user")
        ctx.setdefault("track_labels", {})[p] = first_label
    return out


def resolve_gaps(man: dict, gap_bars: dict[str, int], warnings: list[str]) -> list[dict]:
    """Skipped systems from prepare (no guitar notation, e.g. a vocal-only system): each keeps its bars in
    the timeline as untranscribed bars. The bar count is prepare's (from the barlines on its staff) or
    --gap-bars ID:N; when prepare could not read it and no flag gives it, refuse -- never guess."""
    if any(x.get("role") == "skipped" for x in man.get("dropped", [])) and "gaps" not in man:
        raise InputError("manifest.json lists skipped systems but no 'gaps': it was written by an older prepare.py "
                         "that dropped their bars from the timeline -- re-run prepare.py")
    gaps = [dict(g) for g in man.get("gaps", [])]
    known = {sid: g for g in gaps for sid in [g["id"], *g.get("staves", [])]}
    for gid_, n in gap_bars.items():
        if gid_ not in known:
            raise InputError(f"--gap-bars {gid_}:{n}: no skipped system {gid_!r} in manifest.json "
                             f"(skipped systems: {sorted(g['id'] for g in gaps) or 'none'})")
        if n < 0:
            raise InputError(f"--gap-bars {gid_}:{n}: the number of bars cannot be negative")
    for g in gaps:
        flag = next((gap_bars[k] for k in [g["id"], *g.get("staves", [])] if k in gap_bars), None)
        if flag is not None:
            if g.get("bars") is not None and g["bars"] != flag:
                warnings.append(f"--gap-bars {g['id']}:{flag} overrides the {g['bars']} bar(s) prepare counted for "
                                "that skipped system")
            g["bars"], g["count_source"] = flag, f"--gap-bars {g['id']}:{flag}"
        elif g.get("bars") is None:
            raise InputError(
                f"skipped system {g['id']} (page {g.get('page')}, after {g.get('after_group') or 'the start'}) has no "
                f"guitar notation and its bar count could not be read from the image ({g.get('why_unknown')}). Its bars "
                f"must stay in the timeline, so nothing is guessed: look at crops/{g['id']}_full.png and "
                f"contact/p{g.get('page')}.png, count the bars printed in that system and re-run with --gap-bars "
                f"{g['id']}:<bars> (0 if it is not a system of its own, e.g. a vocal staff that belongs to the "
                "system next to it).")
        else:
            g["count_source"] = f"prepare.py: {g.get('how') or 'barlines'}"
    return [g for g in gaps if g["bars"] > 0]


def _bar_range(idx: list[int]) -> str:
    """0-based master bar indices -> '1-6, 9-10' (1-based)."""
    out, run = [], []
    for i in idx:
        if run and i == run[-1] + 1:
            run.append(i)
        else:
            if run:
                out.append(f"{run[0] + 1}-{run[-1] + 1}" if len(run) > 1 else f"{run[0] + 1}")
            run = [i]
    if run:
        out.append(f"{run[0] + 1}-{run[-1] + 1}" if len(run) > 1 else f"{run[0] + 1}")
    return ", ".join(out)


def build_timeline(part_bars: dict[int, list[Bar]], parts: list[int], idents: dict[int, dict[str, tuple]], ctx: dict,
                   gaps: list[dict], tunings: dict, tuning_specs: dict) -> tuple[list[dict], list[dict]]:
    """One common timeline of master bars: the system groups in page order, with every skipped system's
    bars (untranscribed) where it is printed. One track per part slot and part label; a track has an
    empty (untranscribed) measure wherever it is not notated -- never another part's bars, never a
    collapsed timeline."""
    ref = part_bars[parts[0]]
    groups = list(dict.fromkeys(b.group for b in ref))
    by_group = {p: {} for p in parts}
    for p in parts:
        for b in part_bars[p]:
            by_group[p].setdefault(b.group, []).append(b)
    after: dict = {}
    for g in gaps:
        if g.get("after_group") is not None and g["after_group"] not in groups:
            raise InputError(f"skipped system {g['id']}: manifest.json places it after {g['after_group']}, which has no "
                             "transcribed systems -- re-run prepare.py")
        after.setdefault(g.get("after_group"), []).append(g)
    timeline: list[dict] = []

    def add_gap(g: dict) -> None:
        prev = next((e for e in reversed(timeline) if e["ref"] is not None), None)
        first = len(timeline)
        for k in range(g["bars"]):
            timeline.append({"ref": None, "gap": g, "k": k, "group": None})
        g["master_bars"] = list(range(first, len(timeline)))
        g["prev_ref"] = prev["ref"] if prev else None

    for g in after.get(None, []):
        add_gap(g)
    for grp in groups:
        for i, b in enumerate(by_group[parts[0]][grp]):
            timeline.append({"ref": b, "gap": None, "k": i, "group": grp})
        for g in after.get(grp, []):
            add_gap(g)
    first_real = next((e["ref"] for e in timeline if e["ref"] is not None), None)
    for g in gaps:  # an untranscribed bar keeps the time / key signature in force (it must have one in GP)
        src = g.get("prev_ref") or first_real
        g["ts"], g["key"] = (src.ts, src.key) if src is not None else ((4, 4), 0)
        bars = g["master_bars"]
        g["marker"] = (f"Bar{'s' if len(bars) > 1 else ''} {_bar_range(bars)} not transcribed "
                       "(no guitar notation on the page)")
    tracks: list[dict] = []
    for p in parts:
        order_ids = list(dict.fromkeys(idents[p].values()))
        for idn in order_ids:
            bars: list[Bar | None] = []
            for e in timeline:
                if e["ref"] is None:
                    bars.append(None)
                    continue
                pb = by_group[p][e["group"]][e["k"]]
                bars.append(pb if idents[p].get(pb.system) == idn else None)
            label = ctx.get("track_labels", {}).get(p, {}).get(idn)
            tracks.append({"part": p, "ident": idn, "label": label, "bars": bars, "tuning": tunings[p],
                           "tuning_spec": tuning_specs[p],
                           "notated": _bar_range([i for i, b in enumerate(bars) if b is not None])})
    multi_slot = len(parts) > 1
    for t in tracks:
        is_bass = len(t["tuning"]) == 4
        t["name"] = t["label"] or (("Bass" if is_bass else "Guitar") if not multi_slot else f"Part {t['part']}")
    return timeline, tracks


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


def _infer_time_sigs(bars: list[Bar], problems: list[str], warnings: list[str], prefix: str = "") -> None:
    """Bars read before any printed time signature: infer from the verified full bars' lengths."""
    todo = [b for b in bars if b.ts == (0, 0)]
    if not todo:
        return
    basis = [b for b in todo if not b.partial and not b.unverified and
             not (len(b.events) == 1 and b.events[0].rest and b.events[0].dur == 1)]
    full = Counter(b.beats for b in basis)
    ts = (4, 4)
    if len(full) > 1:
        problems.append(f"{prefix}no time signature printed and the bars before the first printed one have different "
                        f"lengths ({', '.join(f'{k} beats' for k in full)}); report the time signature in the staff read")
    elif len(full) == 1:
        beats = next(iter(full))
        if beats.denominator == 1:
            ts = (int(beats), 4)
        elif (beats * 2).denominator == 1:
            ts = (int(beats * 2), 8)
        else:
            problems.append(f"{prefix}cannot express an inferred bar length of {beats} beats as a time signature")
        warnings.append(f"{prefix}no time signature printed before {todo[0].where}; inferred {ts[0]}/{ts[1]} from the "
                        "bar lengths (6/8 vs 3/4 or C vs cut time cannot be told apart this way)")
    else:
        warnings.append(f"{prefix}no time signature printed before {todo[0].where} and no full bar to infer from; "
                        "assumed 4/4")
    for b in todo:
        b.ts = ts
        b.ts_inferred = True


def _fit_whole_rests(b: Bar, want: Fraction, problems: list[str]) -> None:
    """A voice that is one lone whole rest is a full-bar rest in any metre: give it the bar's length."""
    for v in b.voices:
        evs = b.voice_events(v)
        if len(evs) != 1 or not evs[0].rest or evs[0].dur != 1 or evs[0].beats == want:
            continue
        ev = evs[0]
        for dur, dots in ((1, 0), (2, 1), (2, 0), (4, 1), (4, 0), (8, 1), (8, 0)):
            trial = SEvent(True, dur, dots, 1, [], ev.idx, ev.voice, ev.onset)
            if trial.beats == want:
                b.events[b.events.index(ev)] = trial
                break
        else:
            problems.append(f"{b.where}: full-bar rest in {b.ts[0]}/{b.ts[1]} cannot be written as one GP5 rest")


def _check_durations(bars: list[Bar], problems: list[str], bounds: tuple[set, set] | None = None) -> None:
    starts, ends = bounds if bounds is not None else _section_bounds(bars)
    for i, b in enumerate(bars):
        if b.unverified:
            continue
        want = Fraction(4 * b.ts[0], b.ts[1])
        if not b.partial:
            _fit_whole_rests(b, want, problems)
        got = b.beats
        voices = b.voices
        two = len(voices) > 1
        if b.partial:
            if not b.events:
                problems.append(f"{b.where}: flagged partial but has no events at all")
            elif got >= want:
                problems.append(f"{b.where}: flagged partial but its durations sum to {got} beats (time signature "
                                f"{b.ts[0]}/{b.ts[1]} needs {want}) -- not short")
            elif i not in starts and i not in ends:
                problems.append(f"{b.where}: flagged partial ({got} of {want} beats) but it is not the first or last "
                                "bar of the piece or of a section")
            for v in voices[1:]:
                if b.voice_beats(v) != got:
                    problems.append(f"{b.where}: voice {v} sums to {b.voice_beats(v)} beats but voice {voices[0]} "
                                    f"to {got} in this partial bar")
            continue
        for v in voices:
            gv = b.voice_beats(v)
            if gv != want:
                who = f"voice {v} " if two else ""
                problems.append(f"{b.where}: staff {who}durations sum to {gv} beats, time signature {b.ts[0]}/{b.ts[1]} "
                                f"needs {want}" + ("" if gv > want or two else " (if this is a pickup/final partial "
                                                   "bar, both readers must set \"partial\": true)"))


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


def bar_positions(res: dict):
    """Where a legend printed at a system / on a page takes effect: the index of the first master bar
    of that system (any part / track) or page. Returns pos(system, page) -> index | None (None = unplaceable)."""
    sys_bar: dict[str, int] = {}
    page_bar: dict[int, int] = {}
    for i, e in enumerate(res["timeline"]):
        if e["gap"] is not None:
            if e["gap"].get("page") is not None:
                page_bar.setdefault(int(e["gap"]["page"]), i)
            continue
        for t in res["tracks"]:
            b = t["bars"][i]
            if b is not None:
                sys_bar.setdefault(b.system, i)
        m = re.match(r"p(\d+)_", e["ref"].system)
        if m:
            page_bar.setdefault(int(m.group(1)), i)
    first_page = min(page_bar) if page_bar else None

    def pos(system, page):
        if system:
            if system in sys_bar:
                return sys_bar[system]
            m = re.match(r"p(\d+)_", str(system))
            page = page or (int(m.group(1)) if m else None)
            if page is None:
                return None
        if page is None or page == first_page:
            return 0
        try:
            return page_bar.get(int(page))
        except (TypeError, ValueError):
            return None
    return pos


def tempo_plan(res: dict, tempo_flag: int | None) -> dict:
    """Initial tempo + tempo changes (master bar index -> bpm), from --tempo, the legends and the staff
    read's per-system tempo. Never silently defaults or moves a mark: a missing initial tempo is
    reported loudly, a mark that cannot be placed or a conflicting one is a warning."""
    pos = bar_positions(res)
    warns: list[str] = []
    marks = [(t["system"], t["page"], t["bpm"], f"legend {t['text']!r}") for t in res["legends"].tempos]
    for sysid, bpm, text in res["system_tempos"]:
        if not any(mk[0] == sysid and mk[2] == bpm for mk in marks):
            marks.append((sysid, None, bpm, text))
    at: dict[int, list[tuple[int, str]]] = {}
    for sysid, page, bpm, src in marks:
        idx = pos(sysid, page)
        if idx is None:
            warns.append(f"tempo mark {src} (system {sysid}, page {page}) could not be placed in the file -- ignored")
            continue
        at.setdefault(idx, []).append((bpm, src))
    for idx, lst in at.items():
        if len({b for b, _ in lst}) > 1:
            warns.append(f"conflicting tempo marks at bar {idx + 1}: " + ", ".join(f"{b} ({s})" for b, s in lst)
                         + f" -- used {lst[0][0]}")
    changes = {idx: lst[0] for idx, lst in sorted(at.items()) if idx > 0}
    if tempo_flag:
        return {"tempo": tempo_flag, "source": "--tempo", "changes": changes, "default_used": False, "warnings": warns}
    if 0 not in at:
        return {"tempo": DEFAULT_TEMPO, "source": "DEFAULT (no tempo mark at the start was transcribed)",
                "changes": changes, "default_used": True, "warnings": warns}
    bpm, src = at[0][0]
    return {"tempo": bpm, "source": src, "changes": changes, "default_used": False, "warnings": warns}


def feel_plan(res: dict) -> tuple[list[str | None], list[str]]:
    """Triplet feel per master bar: a shuffle / swing legend applies from its system (or page) until a
    later 'straight' one."""
    pos = bar_positions(res)
    n = len(res["timeline"])
    events, warns = [], []
    for k, f in enumerate(res["legends"].feels):
        idx = pos(f["system"], f["page"])
        if idx is None:
            warns.append(f"feel legend {f['text']!r} (system {f['system']}) could not be placed -- ignored")
            continue
        events.append((idx, k, f["feel"]))
    out: list[str | None] = [None] * n
    for idx, _k, feel in sorted(events):
        for i in range(idx, n):
            out[i] = feel
    return out, warns


def _empty_voice(m, meas):
    """A voice holding nothing: one EMPTY-status beat (alphaTab: isEmpty, not drawn). GP5 stores two
    voices per measure; a zero-beat voice crashed alphaTab in practice, and a rest-status beat would
    draw (and claim) a rest that is not on the page."""
    v = m.Voice(meas)
    eb = m.Beat(v)
    eb.duration = m.Duration(value=4)
    eb.status = m.BeatStatus.empty
    v.beats = [eb]
    return v


def build(res: dict, out: Path, tempo: dict, title: str, write_techs: bool, instrument: int | None):
    import guitarpro as gp
    from guitarpro import models as m

    timeline, tracks = res["timeline"], res["tracks"]
    song = m.Song()
    song.title = title
    song.tempo = tempo["tempo"]
    song.measureHeaders = []
    plan_bars = []
    markers: dict[str, str] = {}
    feels, feel_warns = feel_plan(res)
    res["warnings"].extend(feel_warns + tempo.get("warnings", []))
    gp_feel = {"eighth": m.TripletFeel.eighth, "sixteenth": m.TripletFeel.sixteenth}
    for i, e in enumerate(timeline):
        hdr = m.MeasureHeader(number=i + 1)
        b = e["ref"]
        if b is None:  # a skipped system's bar: untranscribed, in the time / key signature in force
            num, den = e["gap"]["ts"]
            hdr.keySignature = m.KeySignature((max(-7, min(7, e["gap"]["key"])), 0))
            if e["k"] == 0:
                hdr.marker = m.Marker(title=e["gap"]["marker"])
                markers[str(i + 1)] = e["gap"]["marker"]
        else:
            num, den = b.ts
            if b.partial:
                pt = partial_ts(b.beats)
                if pt:
                    num, den = pt
                else:
                    res["warnings"].append(f"{b.where}: partial bar of {b.beats} beats cannot be a time signature; "
                                           "left under-filled in its own time signature")
            hdr.keySignature = m.KeySignature((max(-7, min(7, b.key)), 0))
            section = next((t["bars"][i].section for t in tracks if t["bars"][i] is not None and t["bars"][i].section),
                           None)
            if section:
                hdr.marker = m.Marker(title=str(section))
                markers[str(i + 1)] = str(section)
            hdr.hasDoubleBar = any(t["bars"][i].double_bar for t in tracks if t["bars"][i] is not None)
            hdr.isRepeatOpen = b.repeat_start
            if b.repeat_end:  # reader: total plays (2 = plain :|); GP model: number of repeats
                hdr.repeatClose = max(1, b.repeat_end - 1)
        hdr.timeSignature = m.TimeSignature(numerator=num, denominator=m.Duration(value=den))
        if feels[i]:
            hdr.tripletFeel = gp_feel[feels[i]]
        song.measureHeaders.append(hdr)
        plan_bars.append((num, den))
    tempo_at = {idx: bpm for idx, (bpm, _src) in tempo["changes"].items()}  # master bar index -> bpm
    # a tempo change goes on the first beat of the first track that is notated in that bar
    tempo_track = {i: next((ti for ti, t in enumerate(tracks) if t["bars"][i] is not None), 0) for i in tempo_at}

    song.tracks = []
    written_techs = []
    plan_tracks = []
    for ti, td in enumerate(tracks, start=1):
        tuning = td["tuning"]
        is_bass = len(tuning) == 4
        track = m.Track(song, number=ti)
        track.name = td["name"]
        track.strings = [m.GuitarString(n, v) for n, v in sorted(tuning.items())]
        track.offset = res.get("capo", 0)
        track.channel.instrument = instrument if instrument is not None else (33 if is_bass else 25)
        track.channel.channel = 2 * (ti - 1)
        track.channel.effectChannel = 2 * (ti - 1) + 1
        track.measures = []
        tplan = []
        for bi, (hdr, b) in enumerate(zip(song.measureHeaders, td["bars"])):
            meas = m.Measure(track, hdr)
            meas.voices = []
            if b is None:
                e = timeline[bi]
                meas.voices = [_empty_voice(m, meas), _empty_voice(m, meas)]
                if bi in tempo_at and tempo_track[bi] == ti - 1:
                    meas.voices[0].beats[0].effect.mixTableChange = m.MixTableChange(
                        tempo=m.MixTableItem(value=tempo_at[bi]), hideTempo=False)
                why = (f"skipped system {e['gap']['id']}: no guitar notation on the page" if e["gap"] is not None
                       else f"this part is not notated in {e['ref'].system}")
                tplan.append({"system": e["gap"]["id"] if e["gap"] is not None else e["ref"].system,
                              "bar": e["k"] + 1, "ts": list(plan_bars[bi]), "beats": [], "untranscribed": True,
                              "why": why})
                track.measures.append(meas)
                continue
            col_notes: dict[int, m.Note] = {}
            bplan = {"system": b.system, "bar": b.bar, "ts": list(plan_bars[bi]), "beats": [], "voice2": []}
            for vn in (1, 2):
                v = m.Voice(meas)
                v.beats = []
                evs = b.voice_events(vn)
                for k, ev in enumerate(evs):
                    beat = m.Beat(v)
                    beat.duration = gp_duration(m, ev)
                    if tempo_track.get(bi) == ti - 1 and vn == 1 and k == 0 and bi in tempo_at:
                        beat.effect.mixTableChange = m.MixTableChange(tempo=m.MixTableItem(value=tempo_at[bi]),
                                                                      hideTempo=False)
                    pnotes = []
                    if ev.rest:
                        beat.status = m.BeatStatus.rest
                    else:
                        beat.status = m.BeatStatus.normal
                        col = b.match.get(ev.idx)
                        for sn in ev.notes:
                            tn = sn.tab
                            if sn.tie and sn.cross_voice:
                                # GP ties stay within a voice: a note held over from the other voice is
                                # written as a new attack at the same string/fret (reported as such)
                                s_, f_ = sn.origin
                                note = m.Note(beat, value=f_, string=s_, type=m.NoteType.normal)
                                pnotes.append({"midi": sn.midi, "tie": False, "string": s_, "fret": f_})
                                res["warnings"].append(f"{b.where} staff event #{ev.idx}: {midi_name(sn.midi)} is "
                                                       "tied over from the other voice; GP5 cannot tie across voices, "
                                                       "so it is written as a new attack")
                                res.setdefault("cross_voice_ties", []).append(f"{b.where}")
                            elif sn.tie:
                                if sn.origin is None:
                                    raise InputError(f"{b.where} staff event #{ev.idx}: internal -- unresolved tie")
                                s_, f_ = sn.origin
                                note = m.Note(beat, value=f_, string=s_, type=m.NoteType.tie)
                                pnotes.append({"midi": sn.midi, "tie": True, "string": None, "fret": None})
                            else:
                                if tn is None:
                                    raise InputError(f"{b.where} staff event #{ev.idx}: internal -- no tab note for "
                                                     f"{sn.desc}")
                                s_, f_ = tn.string, tn.fret
                                note = m.Note(beat, value=f_, string=s_, type=m.NoteType.normal)
                                if sn.ghost:
                                    note.effect.ghostNote = True
                                if col:
                                    col_notes.setdefault(col.idx, note)
                                pnotes.append({"midi": sn.midi, "tie": False, "string": s_, "fret": f_})
                            beat.notes.append(note)
                        beat.notes.sort(key=lambda n: n.string)
                    v.beats.append(beat)
                    bplan["beats" if vn == 1 else "voice2"].append(
                        {"rest": ev.rest, "dur": ev.dur, "dots": 1 if ev.dots == 1 else 0,
                         "tuplet": [ev.tuplet, TUPLET_TIMES[ev.tuplet]] if ev.tuplet != 1 else None,
                         "notes": sorted(pnotes, key=lambda n: n["midi"])})
                meas.voices.append(v if v.beats else _empty_voice(m, meas))
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
            if not bplan["voice2"]:
                del bplan["voice2"]
            if b.unverified:
                bplan["unverified"] = True
            tplan.append(bplan)
            track.measures.append(meas)
        song.tracks.append(track)
        plan_tracks.append({"name": track.name, "strings": len(tuning), "capo": track.offset, "bars": tplan,
                            "notated_bars": td["notated"]})
    out.parent.mkdir(parents=True, exist_ok=True)
    gp.write(song, str(out), version=(5, 1, 0))
    plan = {"tracks": plan_tracks, "tempo": tempo["tempo"],
            "tempo_changes": {str(k + 1): v for k, v in sorted(tempo_at.items())}, "feel": feels, "markers": markers}
    return plan, written_techs


def plan_file(plan: dict) -> dict:
    """plan.json for verify_alphatab.mjs: track 1 at the top level (as in v0.1), further tracks under
    more_tracks; bars a track does not notate are {"untranscribed": true, "beats": []}."""
    t0 = plan["tracks"][0]
    return {"name": t0["name"], "strings": t0["strings"], "capo": t0["capo"], "bars": t0["bars"],
            "more_tracks": plan["tracks"][1:], "tempo": plan["tempo"], "tempo_changes": plan["tempo_changes"],
            "feel": plan["feel"], "markers": plan["markers"]}


def readback(out: Path, plan: dict) -> tuple[int, int]:
    """Parse the written file and compare every beat of every track and voice with the plan; an
    untranscribed measure must hold nothing but empty-status beats."""
    import guitarpro as gp
    from guitarpro import models as m

    song = gp.parse(str(out))
    if len(song.tracks) != len(plan["tracks"]):
        raise AssertionError(f"read-back: {len(song.tracks)} tracks, expected {len(plan['tracks'])}")
    n_notes = 0
    for tr, tp in zip(song.tracks, plan["tracks"]):
        if len(tr.measures) != len(tp["bars"]):
            raise AssertionError(f"read-back: {len(tr.measures)} measures, expected {len(tp['bars'])}")
        capo = tr.offset or 0  # GP's realValue = open string + fret, without the capo
        for meas, pb in zip(tr.measures, tp["bars"]):
            where = f"{tp['name']} {pb['system']} bar {pb['bar']}"
            ts = meas.header.timeSignature
            if [ts.numerator, ts.denominator.value] != pb["ts"]:
                raise AssertionError(f"read-back {where}: time signature {ts.numerator}/{ts.denominator.value} != {pb['ts']}")
            if pb.get("untranscribed"):
                if any(bt.status != m.BeatStatus.empty or bt.notes for v in meas.voices for bt in v.beats):
                    raise AssertionError(f"read-back {where}: an untranscribed measure holds a rest or a note")
                continue
            for vi, key in ((0, "beats"), (1, "voice2")):
                want = [(bt["rest"], bt["dur"], bool(bt["dots"]), tuple(bt["tuplet"]) if bt["tuplet"] else (1, 1),
                         sorted(n["midi"] - capo for n in bt["notes"])) for bt in pb.get(key, [])]
                got = [(bt.status == m.BeatStatus.rest, bt.duration.value, bt.duration.isDotted,
                        (bt.duration.tuplet.enters, bt.duration.tuplet.times), sorted(n.realValue for n in bt.notes))
                       for bt in meas.voices[vi].beats if bt.status != m.BeatStatus.empty]
                if got != want:
                    raise AssertionError(f"read-back {where} voice {vi + 1}:\n  got  {got}\n  want {want}")
                n_notes += sum(len(g[4]) for g in got)
                if got and not pb.get("unverified"):  # each voice fills the bar exactly
                    fill = sum((Fraction(4, g[1]) * (Fraction(3, 2) if g[2] else 1) * Fraction(g[3][1], g[3][0])
                                for g in got), Fraction(0))
                    need = Fraction(4 * pb["ts"][0], pb["ts"][1])
                    if fill != need:
                        raise AssertionError(f"read-back {where} voice {vi + 1}: beats fill {fill} quarters, the bar "
                                             f"needs {need}")
    markers = {str(i + 1): h.marker.title for i, h in enumerate(song.measureHeaders) if h.marker}
    if markers != plan.get("markers", markers):
        raise AssertionError(f"read-back: markers {markers} != {plan['markers']}")
    return len(plan["tracks"][0]["bars"]), n_notes


# ------------------------------------------------------------- summary helpers


def classify_problem(p: str, bar_offsets: dict) -> str:
    where = p.split(":", 1)[0]
    if "tie/paren markings disagree" in p:
        return "tie/paren marking"
    if " vs  staff event" in p:
        k = bar_offsets.get(where)
        return f"pitch, TAB {k:+d} semitones vs staff" if k else "pitch"
    if "columns, staff has" in p:
        if "grace note(s) recorded" in p:
            return "column count (grace notes in the bar)"
        return "column count"
    if "durations sum to" in p:
        return "rhythm sum"
    if "partial" in p:
        return "partial bar"
    if "bar count" in p:
        return "bar count"
    if "repeat" in p:
        return "repeat signs"
    if "multi-bar rest" in p:
        return "multi-bar rest"
    if "missing from" in p:
        return "missing system"
    if "time signature" in p:
        return "time signature"
    return "other"


def constant_offset(offsets: list[int]) -> tuple[int, int, int] | None:
    """(k, notes off by k, notes compared) when most compared notes are off by one constant k != 0."""
    if len(offsets) < 3:
        return None
    c = Counter(offsets)
    k, n = c.most_common(1)[0]
    if k != 0 and n >= 0.7 * len(offsets) and n >= 3:
        return k, n, len(offsets)
    return None


def offset_advice(k: int, res: dict, args) -> str:
    lg: Legends = res["legends"]
    nt = args.notation_transpose
    lines = [f"the TAB sounds {k:+d} semitone(s) from the staff on most compared notes -- a constant offset is a "
             "tuning / capo / transposition CONVENTION, not misreads: do NOT re-read, fix the flags."]
    leg_spec, leg_capo, leg_nt = legend_flags(lg, len(res["tuning"]))
    if lg.tuning_text or lg.capo_text:
        want = []
        if leg_spec:
            want.append(f"--tuning {leg_spec}")
        if leg_capo:
            want.append(f"--capo {leg_capo}")
        want.append(f"--notation-transpose {nt + k}")
        lines.append(f"legend: {lg.tuning_text or ''} {lg.capo_text or ''}".strip()
                     + f" -> try: {' '.join(want)}")
    else:
        shifted = ",".join(midi_name(res["tuning"][s] - k) for s in sorted(res["tuning"], reverse=True))
        lines.append("no tuning/capo legend was transcribed -- look at the page header. Either the notation is "
                     f"printed transposed (--notation-transpose {nt + k}; capo pieces in Hal Leonard books: "
                     f"--capo N --notation-transpose N), or the guitar is retuned (--tuning {shifted}).")
    return " ".join(lines)


def _tech_type(tq: dict) -> str:
    return TECH_TYPES.get(str(tq.get("type", "other")).strip().lower(), "other")


def _tech_key(tq: dict) -> tuple:
    """(system, bar, column, string, type): one printed mark. Two marks of one type in one column (a
    pull-off on each string of a double stop) differ by string -- the mark's `string` field, or the
    string its text names; a mark recorded twice without a string is one mark."""
    try:
        note = int(tq.get("note", 0))
    except (TypeError, ValueError):
        note = 0
    string = tq.get("string")
    if not isinstance(string, int) or isinstance(string, bool):
        m = re.search(r"\bstring\s*(\d)\b", str(tq.get("text", "")))
        string = int(m.group(1)) if m else None
    return (tq.get("system"), int(tq.get("bar") or 0), note, string, _tech_type(tq))


def playback_summary(res: dict, written_techs: list[dict]) -> dict:
    """What the file does NOT play that the page prints, with ONE authoritative source per number:

    - technique marks: the TAB reader's `tech` marks, one per mark, de-duplicated by (system, bar,
      column, string, type); palm mute / let ring marks on consecutive columns are one printed span; marks
      written with --write-techniques are in the file and not counted; bends are counted below;
      'other' marks are listed verbatim;
    - grace notes, dead notes, bends: the readers' structured records (TAB grace / dead lists and bend
      marks; staff grace / dead lists and bend targets), matched per bar in reading order -- when both
      readers recorded a bar's events they are counted once; when their numbers differ the longer
      list is counted and the difference is named;
    - ties across a voice change: from the build (written as new attacks);
    - free-text items the readers listed as omitted: verbatim (de-duplicated), never counted;
    - interpretation adjudications: listed with what they dropped, never added to a count."""
    counts: Counter = Counter()
    sources: dict[str, str] = {}
    differences: list[str] = []
    written = {_tech_key(t) for t in written_techs}
    ncols = {(b.system, b.bar): len(b.cols) for b in res["bars"]}
    marks: dict[tuple, dict] = {}
    for tq in res["techs"]:
        marks.setdefault(_tech_key(tq), tq)
    other_items: list[str] = []
    bend_marks: Counter = Counter()
    spans: dict[tuple, list[tuple[int, int]]] = {}
    for key, tq in marks.items():
        sid, bar, note, _string, typ = key
        if key in written:
            continue
        if typ == "b":
            bend_marks[(sid, bar)] += 1
        elif typ == "other":
            other_items.append(f"{sid} bar {bar}: {tq.get('text') or 'unnamed technique mark'} (TAB mark)")
        elif typ in SPAN_TECHS:
            spans.setdefault((sid, typ), []).append((bar, note))
        else:
            counts[TECH_NAMES[typ]] += 1
            sources[TECH_NAMES[typ]] = "TAB reader's technique marks, one per printed mark"
    for (sid, typ), pos in spans.items():
        pos.sort()
        n, prev = 0, None
        for bar, note in pos:
            cont = prev is not None and ((bar == prev[0] and note == prev[1] + 1) or
                                         (bar == prev[0] + 1 and note == 1 and prev[1] >= ncols.get((sid, prev[0]), 10 ** 6)))
            n += 0 if cont else 1
            prev = (bar, note)
        counts[TECH_NAMES[typ]] += n
        sources[TECH_NAMES[typ]] = ("TAB reader's marks; marks on consecutive columns are one printed span")
    per: dict[tuple, dict[str, int]] = {}
    for item in res["p2"]:
        typ = "bend" if item["type"] == "bend_target" else item["type"]
        per.setdefault((item["system"], item["bar"], typ), {"tab": 0, "staff": 0})[item["side"]] += 1
    for (sid, bar), n in bend_marks.items():
        per.setdefault((sid, bar, "bend"), {"tab": 0, "staff": 0})["tab"] += n
    side_name = {"tab": "TAB reader", "staff": "staff reader"}
    for (sid, bar, typ), sides in sorted(per.items(), key=lambda kv: (str(kv[0][0]), kv[0][1], kv[0][2])):
        name = STRUCT_NAMES[typ]
        n = max(sides.values())
        counts[name] += n
        sources[name] = ("both readers' structured records, matched per bar in reading order (counted once); "
                         "where they differ, the longer list")
        if sides["tab"] != sides["staff"]:
            used = "tab" if sides["tab"] > sides["staff"] else "staff"
            differences.append(f"{sid} bar {bar}: the TAB reader recorded {sides['tab']} {name}, the staff reader "
                               f"{sides['staff']} -- counted {n} (the {side_name[used]}'s list)")
    if res.get("cross_voice_ties"):
        name = "ties across a voice change (written as new attacks)"
        counts[name] += len(res["cross_voice_ties"])
        sources[name] = "the build: GP5 cannot tie across voices"
    seen: set[str] = set()
    verbatim = []
    for o in res["omitted"] + other_items:
        k = _canary_norm(o)
        if k not in seen:
            seen.add(k)
            verbatim.append(o)
    dropped_by_adj = []
    for a in res["adjudications"]:
        if not a.get("drops"):
            continue
        val = a.get("value")
        written_as = "a rest" if isinstance(val, dict) and val.get("rest") else None
        dropped_by_adj.append({"system": a.get("system"), "bar": a.get("bar"), "drops": a["drops"],
                               "kind": a.get("kind") or "unclassified", "written_as": written_as})
    order = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return {"dropped_playback": [f"{n} {name}" for name, n in order if n],
            "sources": {name: sources[name] for name, n in order if n},
            "reader_differences": differences,
            "dropped_by_interpretation": dropped_by_adj,
            "other_items": verbatim}


def describe_drop(d: dict) -> str:
    where = f"{d['system']} bar {d['bar']}" if d.get("bar") is not None else str(d["system"])
    return f"{where}: {d['drops']}" + (f" -> written as {d['written_as']}" if d.get("written_as") else "")


# ------------------------------------------------------------- main


def tempo_default_warning(tempo: dict, lg: "Legends") -> str:
    texts = [t["text"] for t in lg.texts if str(t.get("kind", "")).lower() == "tempo" and t.get("bpm") is None
             and not re.search(r"=\s*\d", t["text"])]
    if texts:
        return (f"NO METRONOME NUMBER: the tempo mark {texts[0]!r} prints no number -- wrote {tempo['tempo']} bpm as a "
                "placeholder. Ask the user or re-run with --tempo N.")
    return (f"NO TEMPO MARK was transcribed -- wrote {tempo['tempo']} bpm as a placeholder. Check the page and re-run "
            "with --tempo N.")


def parse_gap_bars(items: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in ",".join(items).split(","):
        if not item.strip():
            continue
        m = re.fullmatch(r"\s*([A-Za-z0-9_]+)\s*:\s*(\d+)\s*", item)
        if not m:
            raise InputError(f"--gap-bars {item!r}: want <system id>:<bars>, e.g. p1_x1:2")
        out[m.group(1)] = int(m.group(2))
    return out


def print_tracks(plan: dict, res: dict) -> None:
    if len(plan["tracks"]) < 2 and not res["gaps"]:
        return
    parts = []
    for t in plan["tracks"]:
        parts.append(f"{t['name']!r} notated in bar(s) {t['notated_bars'] or 'none'}")
    print("  TRACKS (one common timeline; each track is EMPTY -- untranscribed -- outside its notated bars): "
          + "; ".join(parts))
    for td in res["tracks"][1:]:
        if td["part"] == res["tracks"][0]["part"]:
            print(f"    {td['name']!r} is written as its own track (bars {td['notated']}): its part label names "
                  "another part than the systems before it")


def print_not_in_file(summary: dict) -> None:
    for u in summary.get("untranscribed", []):
        print(f"  UNTRANSCRIBED: bar(s) {u['bars']} -- skipped system {u['skipped_system']} has no guitar notation on "
              f"the page (bar count from {u['count_source']}); empty in every track. If the page tells the guitar "
              "to play there, those notes are NOT in the file.")
    if not (summary["dropped_playback"] or summary["dropped_by_interpretation"] or summary["other_items"]):
        return
    print("  NOT IN THE FILE (changes how it sounds; each count has one source, see report.json "
          "dropped_playback_sources): " + (", ".join(summary["dropped_playback"]) or "nothing counted"))
    for d in summary["reader_differences"]:
        print(f"    readers differ: {d}")
    if summary["dropped_by_interpretation"]:
        print("    dropped by interpretation adjudications (not in the counts above):")
        for d in summary["dropped_by_interpretation"]:
            print(f"      - {describe_drop(d)}")
    if summary["other_items"]:
        print(f"    other printed items not in the file ({len(summary['other_items'])}, as the readers described "
              "them, not counted):")
        for o in summary["other_items"]:
            print(f"      - {o}")


def round1_stats(work: Path, args) -> dict:
    """The reads as read, before any adjudication: how much agreed on the first pass (computed on clean
    runs too -- then it equals the final agreement)."""
    try:
        r = reconcile(work, args.tuning, args.unverified, args.capo, args.notation_transpose, args.tab_reprints_ties,
                      apply_adj=False, legend_override=args.legend_override or "round-1 statistics only",
                      gap_bars=args.gap_bars)
    except InputError as e:
        return {"error": f"the raw reads do not even parse without the adjudications: {e}"}
    st = r["stats"]
    return {"disagreements": len(r["problems"]),
            "tab_notes_agreed": st["tab_notes_in_agreeing_bars"], "tab_notes": st["tab_notes_raw"],
            "agreement": f"{st['tab_notes_in_agreeing_bars']}/{st['tab_notes_raw']}"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("workdir")
    ap.add_argument("out")
    ap.add_argument("--tempo", type=int, default=None,
                    help=f"BPM (default: the tempo mark the staff reader transcribed; {DEFAULT_TEMPO} with a loud "
                         "warning if there is none)")
    ap.add_argument("--tuning", default=None, help="comma-separated, LOW to HIGH, e.g. D2,A2,D3,G3,B3,E4")
    ap.add_argument("--capo", type=int, default=0, help="capo fret; tab frets are read relative to the capo")
    ap.add_argument("--notation-transpose", type=int, default=0,
                    help="semitones to add to the staff's sounding pitch (e.g. -1 for half-step-down books "
                         "whose notation is printed as if in standard tuning; +N for capo-N pieces whose notation "
                         "is printed in the fingered key)")
    ap.add_argument("--legend-override", default=None, metavar="REASON",
                    help="proceed although the flags contradict the transcribed tuning/capo legend (reported)")
    ap.add_argument("--title", default=None)
    ap.add_argument("--write-techniques", action="store_true",
                    help="also write tab-only technique marks (h/p/slides/vibrato/PM/let ring/harmonics) -- UNVERIFIED")
    ap.add_argument("--tab-reprints-ties", action="store_true",
                    help="this book reprints tied notes as plain digits in the tab (weakens the tie check)")
    ap.add_argument("--unverified", action="store_true",
                    help="allow systems that only have tab; output name gets 'UNVERIFIED'")
    ap.add_argument("--report", default=None, help="report path (default <workdir>/report.json)")
    ap.add_argument("--instrument", type=int, default=None, help="MIDI program (default 25 guitar / 33 bass)")
    ap.add_argument("--gap-bars", action="append", default=[], metavar="ID:N",
                    help="bar count of a skipped system (no guitar notation) whose bars prepare.py could not count, "
                         "e.g. p1_x1:2 (0 = it is not a system of its own); repeatable or comma-separated")
    args = ap.parse_args(argv)
    try:
        args.gap_bars = parse_gap_bars(args.gap_bars)
    except InputError as e:
        ap.error(str(e))

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
                        args.tab_reprints_ties, legend_override=args.legend_override, gap_bars=args.gap_bars)
    except LegendError as e:
        report["legend_flags_required"] = e.flags
        return fail(f"LegendError: {e}")
    except InputError as e:
        return fail(f"InputError: {e}")
    except Exception as e:  # noqa: BLE001 -- any crash must read as an error, never as "disagreements"
        return fail(f"internal error {type(e).__name__}: {e}\n{traceback.format_exc()}")

    st = res["stats"]
    adjs = res["adjudications"]
    adj_kinds = Counter(a.get("kind") or "unclassified" for a in adjs)
    interpretations = [a for a in adjs if a.get("kind") == "interpretation"]
    if adj_kinds.get("unclassified"):
        res["warnings"].append(f"{adj_kinds['unclassified']} adjudication(s) have no 'kind' -- mark each 'glyph' (a reader "
                               "misread a glyph) or 'interpretation' (a representation gap); unclassified ones are "
                               "reported as possible interpretations")
    lg: Legends = res["legends"]
    try:
        round1 = round1_stats(work, args)
    except Exception as e:  # noqa: BLE001 -- statistics must never block the run
        round1 = {"error": f"{type(e).__name__}: {e}"}
    report.update({
        "systems": st["systems"], "bars": st["bars"], "staff_notes": st["staff_notes"], "tab_notes": st["tab_notes"],
        "parts": len(res["parts"]), "disagreements": res["problems"], "warnings": res["warnings"],
        "omitted_by_readers": res["omitted"], "techniques_seen_in_tab": res["techs"],
        "not_representable_seen": res["p2"],
        "adjudications_applied": len(adjs), "adjudication_kinds": dict(adj_kinds), "adjudications": adjs,
        "round1": round1, "tuning": res["tuning_spec"], "capo": args.capo,
        "notation_transpose": args.notation_transpose, "tab_reprints_ties": args.tab_reprints_ties,
        "unverified_systems": res["unverified_systems"],
        "legends": lg.texts, "legend_check": {k: v for k, v in res["legend_check"].items() if k != "warnings"},
        "tuning_capo_note": "tuning, capo and notation transposition come from the flags / page legend and are NOT "
                            "cross-checkable: a wrong value shifts both reads together",
    })
    print(f"systems {st['systems']}  bars {st['bars']}  staff notes {st['staff_notes']}  tab notes {st['tab_notes']}"
          + (f"  stacked parts {len(res['parts'])}" if len(res["parts"]) > 1 else "")
          + f"  adjudications applied {len(adjs)}"
          + (" (" + ", ".join(f"{k} {v}" for k, v in sorted(adj_kinds.items())) + ")" if adjs else ""))
    if round1:
        if "error" in round1:
            print(f"round 1 (as read, before adjudication): {round1['error']}")
        else:
            print(f"round 1 (as read, before adjudication): {round1['disagreements']} disagreement(s); "
                  f"{round1['agreement']} tab notes in bars that agreed")
    if res["problems"]:
        report["agreement"] = None
        types = Counter(classify_problem(p, res["bar_offsets"]) for p in res["problems"])
        report["disagreement_types"] = dict(types)
        off = constant_offset(res["offsets"])
        top, n_top = types.most_common(1)[0]
        n_bars = max(1, st["bars"])
        if off:
            advice = offset_advice(off[0], res, args)
            report["constant_offset"] = {"semitones": off[0], "notes": off[1], "compared": off[2], "advice": advice}
            report["recommendation"] = "fix flags (constant pitch offset), do not re-read"
        elif len(res["problems"]) >= 3 and n_top >= 0.7 * len(res["problems"]):
            report["recommendation"] = (f"systematic: {n_top} of {len(res['problems'])} disagreements are the same kind "
                                        f"({top}) -- a re-read will reproduce them; look for a convention or "
                                        "representation cause (legend, two voices, grace notes in chords, bends) and "
                                        "adjudicate as 'interpretation' if the page is right")
        elif len(res["problems"]) > 0.15 * n_bars:
            report["recommendation"] = "scattered disagreements in more than 15% of bars: re-read the affected systems"
        else:
            report["recommendation"] = "adjudicate each disagreement on the zoomed glyphs"
        report_path.write_text(json.dumps(report, indent=2))
        print(f"\n{len(res['problems'])} DISAGREEMENT(S) -- no file written:")
        for p in res["problems"]:
            print(" -", p)
        print("\nkinds: " + ", ".join(f"{k} x{v}" for k, v in types.most_common()))
        if off:
            print(f"CONSTANT OFFSET: {off[1]} of {off[2]} compared notes are off by {off[0]:+d} semitone(s). "
                  + report["constant_offset"]["advice"])
        print("recommendation:", report["recommendation"])
        return 1

    if res["unverified_systems"]:
        out = unverified_out
    tempo = tempo_plan(res, args.tempo)
    title = args.title or out.stem
    try:
        plan, written = build(res, out, tempo, title, args.write_techniques, args.instrument)
        nb, nn = readback(out, plan)
        plan_path.write_text(json.dumps(plan_file(plan)))
    except Exception as e:  # noqa: BLE001
        if out.exists():
            out.unlink()
        detail = str(e) if isinstance(e, (InputError, AssertionError)) else f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        return fail(f"build/read-back failed: {detail}")
    # every TAB digit of an agreeing bar was matched to a staff note (a unison printed once in the TAB
    # serves two voices, so count digits, not staff notes)
    compared = sum(len(c.notes) for b in res["bars"] if not b.unverified and b.aligned for c in b.cols)
    omitted_ties = sum(1 for b in res["bars"] if not b.unverified for e in b.events if not e.rest
                       for sn in e.notes if sn.tie and sn.tab is None)
    summary = playback_summary(res, written)
    dropped = summary["dropped_playback"]
    untranscribed = [{"bars": _bar_range(g["master_bars"]), "skipped_system": g["id"], "page": g.get("page"),
                      "count_source": g["count_source"]} for g in res["gaps"]]
    summary["untranscribed"] = untranscribed
    if tempo["default_used"]:
        res["warnings"].append(tempo_default_warning(tempo, lg))
    report.update({"ok": True, "output": str(out), "notes_written": nn, "bars_written": nb,
                   "notes_cross_checked": compared,
                   "agreement": f"{compared}/{compared}" if compared else None,
                   "tied_continuations_not_in_tab": omitted_ties, "tempo": tempo["tempo"],
                   "tempo_source": tempo["source"], "tempo_changes": plan["tempo_changes"],
                   "triplet_feel": lg.feel, "feel_per_bar": plan["feel"], "dropped_playback": dropped,
                   "dropped_playback_sources": summary["sources"],
                   "dropped_playback_reader_differences": summary["reader_differences"],
                   "dropped_by_interpretation": summary["dropped_by_interpretation"],
                   "other_items_not_in_file": summary["other_items"],
                   "interpretations": [{"system": a.get("system"), "bar": a.get("bar"), "reason": a.get("reason"),
                                        "drops": a.get("drops")} for a in interpretations],
                   "techniques_written": written, "plan": str(plan_path),
                   "tracks": [t["name"] for t in plan["tracks"]],
                   "track_bars": [{"name": t["name"], "notated_bars": t["notated_bars"]} for t in plan["tracks"]],
                   "untranscribed_bars": untranscribed})
    report_path.write_text(json.dumps(report, indent=2))
    print_not_in_file(summary)
    print_tracks(plan, res)
    for w in res["warnings"]:
        print("  warning:", w)
    if tempo["default_used"]:
        print(f"  WARNING: {tempo_default_warning(tempo, lg)}")
    else:
        print(f"  tempo {tempo['tempo']} bpm (from {tempo['source']})")
    if plan["tempo_changes"]:
        print("  tempo changes written: " + ", ".join(f"bar {k} -> {v}" for k, v in plan["tempo_changes"].items()))
    if any(plan["feel"]):
        runs = []
        for i, f in enumerate(plan["feel"]):
            if i == 0 or f != plan["feel"][i - 1]:
                runs.append(f"bar {i + 1}: {f or 'straight'}")
        print("  feel written as GP triplet feel: " + ", ".join(runs))
    print(f"  TUNING/CAPO (NOT cross-checkable -- from flags/legend): tuning {res['tuning_spec']}, capo {args.capo}, "
          f"notation transpose {args.notation_transpose:+d}"
          + (f"; legend: {' | '.join(t['text'] for t in lg.texts if t.get('kind') in ('tuning', 'capo'))}"
             if any(t.get("kind") in ("tuning", "capo") for t in lg.texts) else "; no tuning/capo legend transcribed"))
    if res["techs"]:
        print(f"  {len(res['techs'])} technique mark(s) seen in the tab (NOT cross-checkable): "
              + ("written as UNVERIFIED extras" if args.write_techniques else "NOT written (use --write-techniques)"))
    if interpretations or adj_kinds.get("unclassified"):
        print("  INTERPRETATIONS (the file differs from the printed page by a judgement, not a misread):")
        for a in interpretations:
            print(f"    - {a.get('system')} bar {a.get('bar')}: {a.get('reason')}"
                  + (f" [drops: {a['drops']}]" if a.get("drops") else ""))
        for a in adjs:
            if not a.get("kind"):
                print(f"    - (unclassified) {a.get('system')} bar {a.get('bar')}: {a.get('reason')}")
    verdict = "UNVERIFIED (tab-only systems present)" if res["unverified_systems"] else \
        "tab and staff agree on every note"
    tracks = f", {len(plan['tracks'])} tracks: {', '.join(plan_t['name'] for plan_t in plan['tracks'])}" \
        if len(plan["tracks"]) > 1 else ""
    r1 = ""
    if round1 and "error" not in round1:
        r1 = f" (round 1, as read: {round1['agreement']} before {len(adjs)} adjudication(s))"
    print(f"\nOK: {verdict}; wrote {out} ({nb} bars, {nn} notes{tracks}), read back exactly"
          + (f"; agreement {compared}/{compared} tab notes{r1}" if compared else "")
          + (f" (+{omitted_ties} tied continuation(s) the tab does not reprint)" if omitted_ties else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
