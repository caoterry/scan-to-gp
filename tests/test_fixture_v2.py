"""End to end on the v0.2 layout fixtures: prepare.py's systems + the hand-checked reads -> .gp5,
compared note by note with the LilyPond source's MIDI (the ground truth the page was engraved from)."""

import json
import shutil
from fractions import Fraction
from pathlib import Path

import guitarpro as gp
import mido
import pytest

import prepare
import reconcile_build as rb
from conftest import FIXTURES, needs_alphatab, run_alphatab

EX2 = FIXTURES / "exercise2"
EX3 = FIXTURES / "exercise3"
EB = "Eb2,Ab2,Db3,Gb3,Bb3,Eb4"


def midi_tracks(path: Path) -> list[list[tuple[Fraction, int, Fraction]]]:
    """(onset, pitch, duration) in quarter notes, per MIDI track that has notes."""
    mf = mido.MidiFile(str(path))
    ppq = mf.ticks_per_beat
    tracks = []
    for tr in mf.tracks:
        t, on, out = 0, {}, []
        for msg in tr:
            t += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                on.setdefault(msg.note, []).append(t)
            elif msg.type in ("note_off", "note_on") and on.get(msg.note):
                start = on[msg.note].pop(0)
                out.append((Fraction(start, ppq), msg.note, Fraction(t - start, ppq)))
        if out:
            tracks.append(sorted(out))
    return tracks


def gp_tracks(path: Path) -> list[list[tuple[Fraction, int, Fraction]]]:
    """(onset, sounding pitch, duration) per track, both voices, tied notes merged."""
    song = gp.parse(str(path))
    out = []
    for tr in song.tracks:
        notes, bar_start = [], Fraction(0)
        open_by_voice: dict = {}
        for meas in tr.measures:
            ts = meas.header.timeSignature
            for vi, voice in enumerate(meas.voices):
                t = bar_start
                for b in voice.beats:
                    if b.status.name == "empty":
                        continue
                    d = Fraction(4, b.duration.value) * (Fraction(3, 2) if b.duration.isDotted else 1)
                    if b.duration.tuplet.enters != 1:
                        d *= Fraction(b.duration.tuplet.times, b.duration.tuplet.enters)
                    for n in b.notes:
                        pitch = n.realValue + (tr.offset or 0)
                        key = (vi, n.string)
                        if n.type.name == "tie" and key in open_by_voice:
                            k = open_by_voice[key]
                            on, p, dd = notes[k]
                            notes[k] = (on, p, dd + d)
                        else:
                            open_by_voice[key] = len(notes)
                            notes.append((t, pitch, d))
                    t += d
            bar_start += Fraction(4 * ts.numerator, ts.denominator.value)
        out.append(sorted(notes))
    return out


def built(tmp_path: Path, ex: Path, name: str, *flags) -> tuple[int, dict, Path, Path]:
    work = tmp_path / "work"
    assert prepare.main([str(ex / f"{name}_scan.jpg"), str(work)]) == 0
    for f in (ex / "expected" / "reads").glob("*.json"):
        shutil.copy(f, work / "reads" / f.name)
    out = tmp_path / f"{name}.gp5"
    code = rb.main([str(work), str(out), *flags])
    return code, json.loads((work / "report.json").read_text()), out, work


@pytest.fixture(scope="module")
def ex2(tmp_path_factory):
    return built(tmp_path_factory.mktemp("ex2"), EX2, "exercise2", "--tuning", EB, "--notation-transpose", "-1")


@pytest.fixture(scope="module")
def ex3(tmp_path_factory):
    return built(tmp_path_factory.mktemp("ex3"), EX3, "exercise3", "--capo", "2", "--notation-transpose", "2")


def test_stacked_parts_end_to_end_match_the_source(ex2):
    code, rep, out, _ = ex2
    assert code == 0, rep.get("disagreements") or rep.get("error")
    assert rep["tracks"] == ["Gtr. 1", "Gtr. 2"] and rep["bars_written"] == 8
    assert rep["agreement"] == "56/56"
    # tuned down a half step: everything sounds one semitone below the engraved (fingered) pitch
    want = [[(on, p - 1, d) for on, p, d in tr] for tr in midi_tracks(EX2 / "exercise2.midi")]
    assert gp_tracks(out) == want
    assert rep["tempo"] == 120 and rep["tempo_changes"] == {"5": 140}
    assert gp.parse(str(out)).measureHeaders[4].marker.title == "Chorus"


def test_stacked_parts_refused_without_the_legend_flags(tmp_path):
    code, rep, out, _ = built(tmp_path, EX2, "exercise2")
    assert code == 2 and not out.exists()
    assert rep["legend_flags_required"] == f"--tuning {EB} --notation-transpose -1"


def test_two_voices_end_to_end_match_the_source(ex3):
    code, rep, out, _ = ex3
    assert code == 0, rep.get("disagreements") or rep.get("error")
    assert rep["bars_written"] == 12 and rep["agreement"] == "40/40"
    # the vocal-only system (bars 5-8 of the source) has no guitar notation: its four bars stay in the
    # timeline as untranscribed (empty) bars, so every guitar note lands where the source has it
    src = [n for tr in midi_tracks(EX3 / "exercise3.midi") for n in tr]
    want = sorted((on, p + 2, d) for on, p, d in src)  # capo 2: sounds a tone higher
    assert [n for tr in gp_tracks(out) for n in tr] == want
    song = gp.parse(str(out))
    assert rep["untranscribed_bars"] == [{"bars": "5-8", "skipped_system": "p1_x2", "page": 1,
                                          "count_source": "prepare.py: barlines on the staff-only staff (note stems "
                                                          "excluded)"}]
    for m in song.tracks[0].measures[4:8]:  # no fabricated rests or notes: empty-status beats only
        assert all(b.status.name == "empty" and not b.notes for v in m.voices for b in v.beats)
    assert song.measureHeaders[4].marker.title == "Bars 5-8 not transcribed (no guitar notation on the page)"
    meas = song.tracks[0].measures[1]
    assert [b.duration.value for b in meas.voices[1].beats] == [1]  # the held bass is a whole note
    assert rep["triplet_feel"] == "eighth" and rep["tempo"] == 100
    assert any("bends" in d for d in rep["dropped_playback"])


@needs_alphatab
@pytest.mark.parametrize("which", ["ex2", "ex3"])
def test_layout_fixtures_load_in_alphatab(request, which):
    code, rep, out, work = request.getfixturevalue(which)
    assert code == 0
    p = run_alphatab(out, "--expect-bars", str(rep["bars_written"]), "--expect-notes", str(rep["notes_written"]),
                     "--plan", str(work / "plan.json"))
    assert p.returncode == 0, p.stdout + p.stderr
