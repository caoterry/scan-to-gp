"""Regression tests for the default-guilty review of v0.2 (each one reproduced a silent wrong output
or an over-reported summary before its fix)."""

import json
from pathlib import Path

import guitarpro as gp
import numpy as np
import pytest
from PIL import Image, ImageDraw

import prepare
import reconcile_build as rb
from conftest import FIXTURES, needs_alphatab, run_alphatab
from test_reconcile import GOOD_STAFF, GOOD_TAB, make_work, q, rest, run
from test_reconcile_v2 import legend_work, num, stacked_work, write


# ---- legends: amounts, units, named tunings, capo phrasing, uninterpretable -> refuse --------------

@pytest.mark.parametrize("text,down", [
    ("Tune strings down 1/2 step", 1), ("Tune down a half step", 1), ("Tune down one semitone", 1),
    ("Tune down a semitone", 1), ("Tune down 2 semitones", 2), ("Tune down a whole step", 2),
    ("Tune down a tone", 2), ("Tune down a whole tone", 2), ("Tune down 1 step", 2),
    ("Tune down 1 1/2 steps", 3), ("Tune down 1½ steps", 3), ("Tune down 2 1/2 steps", 5),
    ("Tune guitar down 1/2 step", 1), ("Tune all strings down 1/2 step", 1), ("All guitars down 1/2 step", 1),
    ("Standard tuning, down a half step", 1), ("Detune 1/2 step", 1), ("Half-step down tuning", 1),
    ("Eb tuning", 1), ("D standard tuning", 2),
])
def test_tune_down_amounts(text, down):
    lg = rb.parse_legends([{"page": 1, "kind": "tuning", "text": text}])
    assert lg.tune_down == down, text
    spec, capo, nt = rb.legend_flags(lg, 6)
    assert nt == -down and spec is not None


@pytest.mark.parametrize("text,spec,nt", [
    ("Drop D tuning, tuned down 1/2 step", "Db2,Ab2,Db3,Gb3,Bb3,Eb4", -1),
    ("Drop D tuning, down 1/2 step", "Db2,Ab2,Db3,Gb3,Bb3,Eb4", -1),
    ("Drop C# tuning", "C#2,G#2,C#3,F#3,A#3,D#4", 0),
    ("Drop C tuning", "C2,G2,C3,F3,A3,D4", 0),
    ("Tuning: Eb Ab Db Gb Bb Eb", "Eb2,Ab2,Db3,Gb3,Bb3,Eb4", 0),
    ("6=D", "D2,A2,D3,G3,B3,E4", 0),
    ("Standard tuning", "E2,A2,D3,G3,B3,E4", 0),
])
def test_named_and_listed_tunings(text, spec, nt):
    lg = rb.parse_legends([{"page": 1, "kind": "tuning", "text": text}])
    s, capo, t = rb.legend_flags(lg, 6)
    assert [rb.parse_pitch_name(x) for x in s.split(",")] == [rb.parse_pitch_name(x) for x in spec.split(",")], (text, s)
    assert t == nt


@pytest.mark.parametrize("text,capo", [
    ("Capo on fret 3", 3), ("Capo fret 5", 5), ("Capo at fifth fret", 5), ("Capo 3rd fr.", 3),
    ("w/ capo at 2", 2), ("Capo II", 2), ("Capo 7th fret", 7),
])
def test_capo_phrasings(text, capo):
    assert rb.parse_legends([{"page": 1, "kind": "capo", "text": text}]).capo == capo


@pytest.mark.parametrize("kind,text", [
    ("other", "Gtr. 2 = Clean"),
    ("other", "Open strings let ring throughout; play in open G position"),
    ("other", "Capo is optional on the recording"),
])
def test_texts_that_are_not_tuning_legends(kind, text):
    lg = rb.parse_legends([{"page": 1, "kind": kind, "text": text}])
    assert lg.tuning is None and lg.capo is None and not lg.notes


def test_string_note_pairs_are_anchored():
    lg = rb.parse_legends([{"page": 1, "kind": "tuning", "text": "Gtr. 2 = Clean, (6)=D"}])
    assert rb.legend_flags(lg, 6)[0] == "D2,A2,D3,G3,B3,E4"


@pytest.mark.parametrize("text", ["Tune down slightly", "Capo is optional; tuned down 1/2 step",
                                  "Gtr. 1: Standard tuning. Gtr. 2: Drop D tuning: (6)=D"])
def test_uninterpretable_or_conflicting_tuning_legend_is_refused(tmp_path, text):
    work = legend_work(tmp_path, [{"page": 1, "kind": "tuning", "text": text}])
    code, rep, _ = run(work, tmp_path, "--tuning", "E2,A2,D3,G3,B3,E4")
    assert code == 2 and "LegendError" in rep["error"]
    code, rep, _ = run(work, tmp_path, "--legend-override", "checked by hand: standard tuning")
    assert code == 0


def test_following_the_refusal_gives_the_right_pitch(tmp_path):
    """'Tune down one semitone': the printed flags must build the file a semitone down, not a tone."""
    work = legend_work(tmp_path, [{"page": 1, "kind": "tuning", "text": "Tune down one semitone"}])
    code, rep, _ = run(work, tmp_path)
    assert code == 2 and rep["legend_flags_required"] == "--tuning Eb2,Ab2,Db3,Gb3,Bb3,Eb4 --notation-transpose -1"
    code, rep, out = run(work, tmp_path, *rep["legend_flags_required"].split())
    assert code == 0
    first = gp.parse(str(out)).tracks[0].measures[0].voices[0].beats[0].notes[0]
    assert first.realValue == 63  # E4 played on the Eb-tuned top string sounds Eb4


# ---- two voices: tie state across bars without that voice --------------------------------------------

def test_tie_after_a_bar_without_that_voice_takes_the_real_origin(tmp_path):
    staff = [{"time_sig": "4/4", "events": [q("E", 5, dur="1"), q("F", 4, dur="1", voice=2)]},
             {"events": [q("F", 4, acc="sharp", dur="1")]},
             {"events": [q("E", 5, dur="1"), q("F", 4, dur="1", voice=2, tie=True)]}]
    tab = [{"notes": [[[1, 0], [4, 3]]]}, {"notes": [[4, 4]]}, {"notes": [[1, 0]]}]
    code, rep, out = run(make_work(tmp_path, tab, staff), tmp_path)
    assert code == 0, rep.get("disagreements") or rep.get("error")
    note = gp.parse(str(out)).tracks[0].measures[2].voices[1].beats[0].notes[0]
    # the held F#3 (not a stale F3) -- GP5 cannot tie across voices, so it is a reported new attack
    assert (note.string, note.value, note.realValue) == (4, 4, 54)
    assert any("voice change" in d for d in rep["dropped_playback"])


def test_tie_that_changes_voice_across_the_barline(tmp_path):
    staff = [{"time_sig": "4/4", "events": [q("E", 3, dur="1")]},
             {"events": [q("B", 4), q("D", 5), q("B", 4), q("A", 4), q("E", 3, dur="1", voice=2, tie=True)]}]
    tab = [{"notes": [[6, 0]]}, {"notes": [[2, 0], [2, 3], [2, 0], [3, 2]]}]
    code, rep, out = run(make_work(tmp_path, tab, staff), tmp_path)
    assert code == 0, rep.get("disagreements") or rep.get("error")
    note = gp.parse(str(out)).tracks[0].measures[1].voices[1].beats[0].notes[0]
    assert (note.string, note.value) == (6, 0) and rep["tied_continuations_not_in_tab"] == 1


def test_voice2_whole_rest_fills_a_three_four_bar(tmp_path):
    staff = [{"time_sig": "3/4", "events": [q("E", 5), q("C", 5), q("A", 4), rest("1", voice=2)]}]
    code, rep, _ = run(make_work(tmp_path, [{"notes": [[1, 0], [2, 1], [3, 2]]}], staff), tmp_path)
    assert code == 0, rep.get("disagreements")


# ---- stacked parts: pickups by length, interleave guards ----------------------------------------------

def test_stacked_part_pickups_must_have_the_same_length(tmp_path):
    p1 = {"tab": [{"notes": [[1, 0]], "partial": True}, {"notes": [[1, 0]] * 4}],
          "staff": [{"time_sig": "4/4", "partial": True, "events": [q("E", 5)]}, {"events": [q("E", 5)] * 4}]}
    p2 = {"tab": [{"notes": [[6, 0]], "partial": True}, {"notes": [[6, 0]] * 4}],
          "staff": [{"time_sig": "4/4", "partial": True, "events": [q("E", 3, dur="2")]}, {"events": [q("E", 3)] * 4}]}
    p1b = {"tab": [{"notes": [[1, 3]] * 4}], "staff": [{"events": [q("G", 5)] * 4}]}
    p2b = {"tab": [{"notes": [[6, 3]] * 4}], "staff": [{"events": [q("G", 3)] * 4}]}
    code, rep, out = run(stacked_work(tmp_path, parts=(p1, p2, p1b, p2b)), tmp_path)
    assert code == 1 and not out.exists()
    assert any("different lengths" in d for d in rep["disagreements"])


def test_part2_pickup_at_a_section_start_is_accepted(tmp_path):
    full1 = {"tab": [{"notes": [[1, 0]] * 4}], "staff": [{"time_sig": "4/4", "events": [q("E", 5)] * 4}]}
    full2 = {"tab": [{"notes": [[6, 0]] * 4}], "staff": [{"time_sig": "4/4", "events": [q("E", 3)] * 4}]}
    pk1 = {"tab": [{"notes": [[1, 3]], "partial": True}], "staff": [{"partial": True, "events": [q("G", 5)]}]}
    pk2 = {"tab": [{"notes": [[6, 3]], "partial": True}], "staff": [{"partial": True, "events": [q("G", 3)]}]}
    work = stacked_work(tmp_path, parts=(full1, full2, pk1, pk2))
    data = json.loads((work / "reads" / "staff_1.json").read_text())
    data["systems"]["p1_s3"]["section"] = "Coda"  # labelled above part 1 only, as books print it
    (work / "reads" / "staff_1.json").write_text(json.dumps(data))
    code, rep, _ = run(work, tmp_path)
    assert code == 1 or code == 0
    assert not any("not the first or last bar" in d for d in rep.get("disagreements") or [])


def test_alternating_part_labels_in_one_part_are_refused(tmp_path):
    work = make_work(tmp_path, GOOD_TAB, GOOD_STAFF, staff_extra={"part_label": "Gtr. 1"},
                     more_systems={"p1_s2": (GOOD_TAB, [{"events": GOOD_STAFF[0]["events"]}], "none"),
                                   "p1_s3": (GOOD_TAB, [{"events": GOOD_STAFF[0]["events"]}], "none")})
    data = json.loads((work / "reads" / "staff_read.json").read_text())
    data["systems"]["p1_s2"]["part_label"] = "Gtr. 2"
    data["systems"]["p1_s3"]["part_label"] = "Gtr. 1"
    (work / "reads" / "staff_read.json").write_text(json.dumps(data))
    code, rep, _ = run(work, tmp_path)
    assert code == 1 and any("interleaved" in d for d in rep["disagreements"])


# ---- feel and tempo timelines ---------------------------------------------------------------------------

THREE = {f"p1_s{i}": (GOOD_TAB, [{"events": GOOD_STAFF[0]["events"]}], "none") for i in (2, 3)}


def _feels_and_tempos(out):
    song = gp.parse(str(out))
    feels = [h.tripletFeel.name for h in song.measureHeaders]
    tempos = []
    for m in song.tracks[0].measures:
        mt = m.voices[0].beats[0].effect.mixTableChange
        tempos.append(mt.tempo.value if mt and mt.tempo else None)
    return song.tempo, feels, tempos


def test_feel_changes_per_system(tmp_path):
    legends = [{"page": 1, "kind": "feel", "text": "Shuffle", "system": "p1_s1"},
               {"page": 1, "kind": "feel", "text": "Straight 8ths", "system": "p1_s3"}]
    code, rep, out = run(legend_work(tmp_path, legends, more_systems=THREE), tmp_path, "--tempo", "90")
    assert code == 0
    assert _feels_and_tempos(out)[1] == ["eighth", "eighth", "none"]
    legends[1] = {"page": 1, "kind": "feel", "text": "Swing 16ths", "system": "p1_s3"}
    code, rep, out = run(legend_work(tmp_path / "b", legends, more_systems=THREE), tmp_path / "b", "--tempo", "90")
    assert _feels_and_tempos(out)[1] == ["eighth", "eighth", "sixteenth"]


def test_tempo_beat_unit_is_converted_to_quarters(tmp_path):
    legends = [{"page": 1, "kind": "tempo", "text": "Moderately (dotted quarter = 60)", "bpm": 60, "beat": "dotted quarter"}]
    code, rep, out = run(legend_work(tmp_path, legends), tmp_path)
    assert code == 0 and rep["tempo"] == 90
    legends = [{"page": 1, "kind": "tempo", "text": "Moderately ♩. = 60", "bpm": 60}]
    code, rep, out = run(legend_work(tmp_path / "b", legends), tmp_path / "b")
    assert code == 0 and rep["tempo"] == 90
    legends = [{"page": 1, "kind": "tempo", "text": "Fast (half note = 100)", "bpm": 100}]
    code, rep, out = run(legend_work(tmp_path / "c", legends), tmp_path / "c")
    assert code == 0 and rep["tempo"] == 200


def test_tempo_pages_and_later_marks(tmp_path, capsys):
    # page-2 'a tempo' listed first must not replace page 1's initial tempo
    legends = [{"page": 2, "kind": "tempo", "text": "a tempo q = 120", "bpm": 120},
               {"page": 1, "kind": "tempo", "text": "Slowly q = 80", "bpm": 80}]
    code, rep, out = run(legend_work(tmp_path, legends, more_systems=THREE), tmp_path)
    assert code == 0 and rep["tempo"] == 80
    # a tempo printed only over a later system is a change there; the start is the loud default
    legends = [{"page": 1, "kind": "tempo", "text": "Faster q = 132", "bpm": 132, "system": "p1_s3"}]
    capsys.readouterr()
    code, rep, out = run(legend_work(tmp_path / "b", legends, more_systems=THREE), tmp_path / "b")
    assert code == 0 and rep["tempo"] == 90 and "NO TEMPO MARK" in capsys.readouterr().out
    assert _feels_and_tempos(out)[2] == [None, None, 132]
    assert rep["tempo_changes"] == {"3": 132}


# ---- honest counts ---------------------------------------------------------------------------------------

def test_round1_counts_every_raw_tab_digit(tmp_path):
    ev = GOOD_STAFF[0]["events"]
    more = {"p1_s2": ([{"notes": GOOD_TAB[0]["notes"] * 2}], [{"events": ev}, {"events": ev}], "none")}
    adj = [{"target": "tab", "system": "p1_s2", "bar": 1, "op": "set", "field": "notes", "value": GOOD_TAB[0]["notes"],
            "kind": "glyph", "reason": "zoom: barline missed"},
           {"target": "tab", "system": "p1_s2", "bar": 2, "op": "insert_bar", "value": {"notes": GOOD_TAB[0]["notes"]},
            "kind": "glyph", "reason": "zoom: barline missed"}]
    code, rep, _ = run(make_work(tmp_path, GOOD_TAB, GOOD_STAFF, more_systems=more, adjudications=adj), tmp_path)
    assert code == 0
    assert rep["round1"]["agreement"] == "4/12"


def test_round1_does_not_count_bars_that_failed_the_rhythm_check(tmp_path):
    staff = [{"time_sig": "4/4", "events": [q("E", 5), q("C", 5), q("A", 4), q("E", 4, dur="8")]}]
    adj = [{"target": "staff", "system": "p1_s1", "bar": 1, "op": "replace", "index": 4, "value": q("E", 4),
            "kind": "glyph", "reason": "zoom: no flag"}]
    code, rep, _ = run(make_work(tmp_path, GOOD_TAB, staff, adjudications=adj), tmp_path)
    assert code == 0 and rep["round1"]["agreement"] == "0/4"


def test_grace_and_dead_notes_recorded_by_both_readers_count_once(tmp_path):
    tab = [{**GOOD_TAB[0], "grace": [{"before": 2, "string": 2, "fret": 0}], "dead": [{"after": 3, "strings": [4, 5]}]}]
    staff = [{**GOOD_STAFF[0], "grace": [{"before": 2, "voice": 1, "letter": "B", "octave": 4, "acc": "none"}],
              "dead": [{"at": 3, "text": "x chord"}]}]
    code, rep, _ = run(make_work(tmp_path, tab, staff), tmp_path)
    assert code == 0 and sorted(rep["dropped_playback"]) == ["1 dead notes (x)", "1 grace notes"]


# ---- prepare: a boxed rehearsal mark is not a system line ----------------------------------------------

def test_boxed_mark_between_two_systems_is_not_a_stacked_part(tmp_path):
    src = np.asarray(Image.open(FIXTURES / "exercise1" / "exercise1_clean.png").convert("L"))
    sp, x0 = 21.0, 176
    gap = int(4.0 * sp)
    lower_off = max(0, 390 - gap + 14)
    img = Image.fromarray(src[lower_off:710].copy())
    dr = ImageDraw.Draw(img)
    staff_top = 390 - lower_off
    dr.rectangle([x0 - 10, staff_top - int(3.3 * sp), x0 + 40, staff_top - int(1.5 * sp)], outline=0, width=3)
    dr.text((x0 + 8, staff_top - int(3.3 * sp) + 8), "A", fill=0)
    page = np.vstack([src[0:710], np.asarray(img), np.full((200, src.shape[1]), 255, np.uint8)])
    p = tmp_path / "boxed.png"
    Image.fromarray(page).save(p)
    work = tmp_path / "w"
    assert prepare.main([str(p), str(work)]) == 0
    man = json.loads((work / "manifest.json").read_text())
    assert man["parts"] == 1 and all(s["parts_in_group"] == 1 for s in man["systems"])


def test_footer_crop_is_written_for_the_staff_reader(tmp_path):
    work = tmp_path / "w"
    assert prepare.main([str(FIXTURES / "exercise2" / "exercise2_scan.jpg"), str(work)]) == 0
    man = json.loads((work / "manifest.json").read_text())
    ft = man["pages"][0].get("footer")
    assert ft and (work / ft).exists()
    assert Image.open(work / ft).height > 0


@needs_alphatab
def test_alphatab_checks_measure_fill_and_tempo(tmp_path):
    work = legend_work(tmp_path, [{"page": 1, "kind": "tempo", "text": "q = 96", "bpm": 96}])
    code, rep, out = run(work, tmp_path)
    assert code == 0
    plan = json.loads((work / "plan.json").read_text())
    plan["tempo"] = 120
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(plan))
    p = run_alphatab(out, "--plan", str(bad))
    assert p.returncode == 1 and "tempo" in p.stderr


def test_staff_whose_tab_was_missed_is_refused_not_skipped(tmp_path):
    """System 2's TAB lost two lines (so it is not recognised): its staff must not be skipped as a
    vocal line while prepare exits 0."""
    clean = FIXTURES / "exercise1" / "exercise1_clean.png"
    _w = tmp_path / "probe"
    assert prepare.main([str(clean), str(_w)]) == 0
    s2 = json.loads((_w / "manifest.json").read_text())["systems"][1]
    img = np.array(Image.open(clean).convert("L"))
    for y in (s2["tab_lines_y"][1], s2["tab_lines_y"][3]):
        img[int(y) - 3:int(y) + 4, :] = 255
    p = tmp_path / "missed_tab.png"
    Image.fromarray(img).save(p)
    work = tmp_path / "w"
    code = prepare.main([str(p), str(work)])
    man = json.loads((work / "manifest.json").read_text())
    assert code == 3, man["warnings"]
    assert any("missed" in r for r in man["refused"])


def test_reprinted_tie_and_new_attack_of_one_pitch_in_two_voices(tmp_path):
    staff = [{"time_sig": "4/4", "events": [q("E", 4, dur="1")]},
             {"events": [q("E", 4, dur="1", tie=True), q("E", 4, dur="1", voice=2)]}]
    tab = [{"notes": [[4, 2]]}, {"notes": [[[4, 2], [5, 7]]]}]  # the tie reprinted + the attack on string 5
    code, rep, out = run(make_work(tmp_path, tab, staff), tmp_path, "--tab-reprints-ties")
    assert code == 0, rep.get("error") or rep.get("disagreements")
    m = gp.parse(str(out)).tracks[0].measures[1]
    assert [(n.type.name, n.string, n.value) for n in m.voices[0].beats[0].notes] == [("tie", 4, 2)]
    assert [(n.type.name, n.string, n.value) for n in m.voices[1].beats[0].notes] == [("normal", 5, 7)]


def test_reread_legends_replace_the_superseded_ones(tmp_path):
    work = legend_work(tmp_path, [{"page": 1, "kind": "tuning", "text": "Tuned down 1/2 step"}])
    write(work, "reads/staff_1_reread1.json", {"legends": [{"page": 1, "kind": "tempo", "text": "q = 100", "bpm": 100}],
                                               "systems": {}})
    code, rep, _ = run(work, tmp_path)
    assert code == 0 and rep["tempo"] == 100  # the re-read's legends for page 1 replaced the tune-down one
