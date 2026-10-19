"""The public fixture's ground truth is itself verified: expected reads == the LilyPond source's MIDI."""

import json
from fractions import Fraction
from pathlib import Path

import guitarpro as gp
import mido

import reconcile_build as rb
from conftest import FIXTURES, needs_alphatab, run_alphatab

EX1 = FIXTURES / "exercise1"
READS = EX1 / "expected" / "reads"


def midi_notes(path: Path) -> list[tuple[Fraction, int, Fraction]]:
    """(onset, pitch, duration) in quarter notes, from the LilyPond MIDI."""
    mf = mido.MidiFile(str(path))
    ppq = mf.ticks_per_beat
    out, on = [], {}
    for tr in mf.tracks:
        t = 0
        for msg in tr:
            t += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                on[msg.note] = t
            elif msg.type in ("note_off", "note_on") and msg.note in on:
                start = on.pop(msg.note)
                out.append((Fraction(start, ppq), msg.note, Fraction(t - start, ppq)))
    return sorted(out)


def expected_notes_from_staff_read() -> list[tuple[Fraction, int, Fraction]]:
    staff = json.loads((READS / "staff_read.json").read_text())["systems"]
    t = Fraction(0)
    out = []
    open_ties: dict[int, int] = {}
    prev_alter: dict = {}
    for sid in ("p1_s1", "p1_s2"):
        key = rb.key_alter(rb.parse_key_sig(staff[sid]["key_sig"]))
        for bi, bar in enumerate(staff[sid]["bars"], start=1):
            for ev in rb.parse_staff_bar(sid, bi, bar, key, -12, prev_alter):
                for n in ev.notes:
                    if n.tie and n.midi in open_ties:
                        k = open_ties[n.midi]
                        on, p, d = out[k]
                        out[k] = (on, p, d + ev.beats)
                    else:
                        open_ties[n.midi] = len(out)
                        out.append((t, n.midi, ev.beats))
                t += ev.beats
    return sorted(out)


def test_expected_reads_match_the_lilypond_source():
    assert expected_notes_from_staff_read() == midi_notes(EX1 / "exercise1.midi")


def test_expected_gp5_is_reproducible_from_expected_reads(tmp_path):
    work = tmp_path / "work"
    (work / "reads").mkdir(parents=True)
    for f in READS.glob("*.json"):
        (work / "reads" / f.name).write_text(f.read_text())
    out = tmp_path / "rebuilt.gp5"
    assert rb.main([str(work), str(out), "--tempo", "90", "--title", "scan-to-gp test exercise 1"]) == 0
    rep = json.loads((work / "report.json").read_text())
    assert rep["agreement"] == "37/37" and rep["tied_continuations_not_in_tab"] == 1
    assert rep["bars_written"] == 7 and rep["notes_written"] == 38

    def sig(p):
        s = gp.parse(str(p))
        return [[(b.status.name, b.duration.value, b.duration.isDotted, b.duration.tuplet.enters,
                  sorted((n.string, n.value, n.type.name) for n in b.notes)) for b in m.voices[0].beats]
                for m in s.tracks[0].measures]

    assert sig(out) == sig(EX1 / "expected" / "expected.gp5")


@needs_alphatab
def test_expected_gp5_loads_in_alphatab():
    p = run_alphatab(EX1 / "expected" / "expected.gp5", "--expect-bars", "7", "--expect-notes", "38")
    assert p.returncode == 0, p.stdout + p.stderr
