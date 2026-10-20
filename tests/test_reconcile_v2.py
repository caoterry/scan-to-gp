"""reconcile_build.py v0.2: stacked parts, two voices, legends (tuning / capo / tempo / feel), honest summary."""

import json
from pathlib import Path

import guitarpro as gp
import pytest

import reconcile_build as rb
from conftest import needs_alphatab, run_alphatab
from test_reconcile import GOOD_STAFF, GOOD_TAB, make_work, q, rest, run


def write(work: Path, name: str, data) -> None:
    (work / name).parent.mkdir(parents=True, exist_ok=True)
    (work / name).write_text(json.dumps(data))


def num(bars):
    return [{"bar": i + 1, **b} for i, b in enumerate(bars)]


# ---- P0-1: stacked parts -> one track per part, never interleaved ---------------------------------

PART1 = {"tab": [{"notes": [[1, 0], [1, 0], [1, 0], [1, 0]]}],
         "staff": [{"time_sig": "4/4", "events": [q("E", 5)] * 4}]}
PART2 = {"tab": [{"notes": [[6, 0], [6, 0], [6, 0], [6, 0]]}],
         "staff": [{"time_sig": "4/4", "events": [q("E", 3)] * 4}]}
PART1B = {"tab": [{"notes": [[1, 3]] * 4}], "staff": [{"events": [q("G", 5)] * 4}]}
PART2B = {"tab": [{"notes": [[6, 3]] * 4}], "staff": [{"events": [q("G", 3)] * 4}]}


def stacked_work(tmp_path: Path, parts=(PART1, PART2, PART1B, PART2B), labels=("Gtr. 1", "Gtr. 2")) -> Path:
    work = tmp_path / "work"
    sids = ["p1_s1", "p1_s2", "p1_s3", "p1_s4"]
    groups = ["p1_g1", "p1_g1", "p1_g2", "p1_g2"]
    man = {"systems": [{"id": s, "kind": "staff+tab", "group": g, "part": 1 + i % 2, "parts_in_group": 2,
                        "tab_lines": 6} for i, (s, g) in enumerate(zip(sids, groups))]}
    write(work, "manifest.json", man)
    tab, staff = {}, {}
    for i, (sid, p) in enumerate(zip(sids, parts)):
        tab[sid] = {"bars": num(p["tab"])}
        staff[sid] = {"key_sig": "none", "bars": num(p["staff"])}
        if i < 2 and labels:
            staff[sid]["part_label"] = labels[i]
    write(work, "reads/tab_1.json", {"systems": tab})
    write(work, "reads/staff_1.json", {"systems": staff})
    return work


def test_stacked_parts_become_separate_tracks_in_one_timeline(tmp_path):
    code, rep, out = run(stacked_work(tmp_path), tmp_path, "--tempo", "100")
    assert code == 0, rep.get("disagreements") or rep.get("error")
    song = gp.parse(str(out))
    assert [t.name for t in song.tracks] == ["Gtr. 1", "Gtr. 2"]
    assert len(song.measureHeaders) == 2 and rep["bars_written"] == 2 and rep["parts"] == 2
    frets = [[[(n.string, n.value) for b in m.voices[0].beats for n in b.notes] for m in t.measures]
             for t in song.tracks]
    # part 1 = systems 1 and 3 in order, part 2 = systems 2 and 4: never interleaved
    assert frets[0] == [[(1, 0)] * 4, [(1, 3)] * 4]
    assert frets[1] == [[(6, 0)] * 4, [(6, 3)] * 4]
    assert song.tracks[0].channel.channel != song.tracks[1].channel.channel
    assert rep["tracks"] == ["Gtr. 1", "Gtr. 2"]


def test_stacked_parts_without_labels_are_named_by_number(tmp_path):
    code, rep, out = run(stacked_work(tmp_path, labels=()), tmp_path)
    assert code == 0
    assert [t.name for t in gp.parse(str(out)).tracks] == ["Part 1", "Part 2"]


def test_stacked_parts_with_different_bar_counts_disagree(tmp_path):
    p2 = {"tab": PART2["tab"] * 2, "staff": PART2["staff"] + [{"events": [q("E", 3)] * 4}]}
    code, rep, out = run(stacked_work(tmp_path, parts=(PART1, p2, PART1B, PART2B)), tmp_path)
    assert code == 1 and not out.exists()
    assert any("stacked parts have different bar counts" in d for d in rep["disagreements"])


def test_stacked_parts_must_agree_on_time_signature(tmp_path):
    p2 = {"tab": [{"notes": [[6, 0]] * 3}], "staff": [{"time_sig": "3/4", "events": [q("E", 3)] * 3}]}
    code, rep, _ = run(stacked_work(tmp_path, parts=(PART1, p2, PART1B, PART2B)), tmp_path)
    assert code == 1
    assert any("disagree on the time signature" in d for d in rep["disagreements"])


@needs_alphatab
def test_stacked_parts_load_in_alphatab_track_by_track(tmp_path):
    work = stacked_work(tmp_path)
    code, rep, out = run(work, tmp_path)
    assert code == 0
    p = run_alphatab(out, "--expect-bars", "2", "--expect-notes", "16", "--plan", str(work / "plan.json"))
    assert p.returncode == 0, p.stdout + p.stderr
    plan = json.loads((work / "plan.json").read_text())
    plan["more_tracks"][0]["bars"][1]["beats"][0]["notes"][0]["fret"] = 5
    bad = tmp_path / "bad_plan.json"
    bad.write_text(json.dumps(plan))
    p = run_alphatab(out, "--plan", str(bad))
    assert p.returncode == 1 and "track 2" in p.stderr


# ---- P0-2: two voices on one staff -------------------------------------------------------------------


def v2(letter, octave, dur, **kw):
    return q(letter, octave, dur=dur, voice=2, **kw)


# melody B3 D4 B3 A3 (quarters, voice 1) over a held bass E2 (half) A2 (half) in voice 2
TWO_VOICE_STAFF = [{"time_sig": "4/4", "events": [q("B", 4), q("D", 5), q("B", 4), q("A", 4),
                                                   v2("E", 3, "2"), v2("A", 3, "2")]}]
TWO_VOICE_TAB = [{"notes": [[[6, 0], [2, 0]], [2, 3], [[5, 0], [2, 0]], [3, 2]]}]


def test_two_voices_match_the_union_and_keep_held_notes(tmp_path):
    code, rep, out = run(make_work(tmp_path, TWO_VOICE_TAB, TWO_VOICE_STAFF), tmp_path)
    assert code == 0, rep.get("disagreements")
    meas = gp.parse(str(out)).tracks[0].measures[0]
    v1 = [(b.duration.value, [(n.string, n.value) for n in b.notes]) for b in meas.voices[0].beats]
    v2b = [(b.duration.value, [(n.string, n.value) for n in b.notes]) for b in meas.voices[1].beats]
    assert v1 == [(4, [(2, 0)]), (4, [(2, 3)]), (4, [(2, 0)]), (4, [(3, 2)])]
    assert v2b == [(2, [(6, 0)]), (2, [(5, 0)])]  # the bass keeps its half-note length
    assert rep["agreement"] == "6/6" and rep["notes_written"] == 6


def test_second_voice_is_not_silently_omitted(tmp_path):
    tab = [{"notes": [[[6, 0], [2, 0]], [2, 3], [[5, 0], [2, 0]], [3, 2]]}]
    staff = [{"time_sig": "4/4", "events": [q("B", 4), q("D", 5), q("B", 4), q("A", 4)]}]  # voice 2 left out
    code, rep, _ = run(make_work(tmp_path, tab, staff), tmp_path)
    assert code == 1  # the bass digits in the TAB have no staff note


@pytest.mark.parametrize("tab_col", [[[1, 0]], [[1, 0], [2, 5]]])
def test_cross_voice_unison_one_digit_or_two(tmp_path, tab_col):
    # both voices start on E4 (one shared notehead, recorded in both voices): the TAB may print one
    # digit, or the unison on two strings
    staff = [{"time_sig": "4/4", "events": [q("E", 5), q("E", 5), q("E", 5, dur="2"), v2("E", 5, "1")]}]
    tab = [{"notes": [tab_col, [1, 0], [1, 0]]}]
    code, rep, out = run(make_work(tmp_path, tab, staff), tmp_path)
    assert code == 0, rep.get("disagreements")
    meas = gp.parse(str(out)).tracks[0].measures[0]
    assert [n.value for n in meas.voices[1].beats[0].notes] in ([0], [5])
    # three E4 digits for a two-voice unison is still a disagreement
    tab_bad = [{"notes": [[[1, 0], [2, 5], [3, 9]], [1, 0], [1, 0]]}]
    code, rep, _ = run(make_work(tmp_path / "b", tab_bad, staff), tmp_path / "b")
    assert code == 1


def test_each_voice_must_fill_the_bar(tmp_path):
    staff = [{"time_sig": "4/4", "events": [q("B", 4), q("D", 5), q("B", 4), q("A", 4),
                                            v2("E", 3, "2"), v2("A", 3, "4")]}]
    code, rep, _ = run(make_work(tmp_path, TWO_VOICE_TAB, staff), tmp_path)
    assert code == 1
    assert any("voice 2 durations sum to 3 beats" in d for d in rep["disagreements"])


def test_voice2_tie_across_the_barline(tmp_path):
    staff = [{"time_sig": "4/4", "events": [q("B", 4), q("D", 5), q("B", 4), q("A", 4), v2("E", 3, "1")]},
             {"events": [q("B", 4, dur="1"), v2("E", 3, "1", tie=True)]}]
    tab = [{"notes": [[[6, 0], [2, 0]], [2, 3], [2, 0], [3, 2]]}, {"notes": [[2, 0]]}]
    code, rep, out = run(make_work(tmp_path, tab, staff), tmp_path)
    assert code == 0, rep.get("disagreements")
    b = gp.parse(str(out)).tracks[0].measures[1].voices[1].beats[0]
    assert b.notes[0].type.name == "tie" and b.notes[0].string == 6
    assert rep["tied_continuations_not_in_tab"] == 1


def test_accidentals_carry_across_voices_in_time_order(tmp_path):
    # voice 2 prints F#4 on beat 1; voice 1's F4 on beat 2 (no accidental) is therefore F#4 too
    staff = [{"time_sig": "4/4", "events": [q("A", 4), q("F", 4), q("A", 4, dur="2"),
                                            v2("F", 4, "1", acc="sharp")]}]
    tab = [{"notes": [[[3, 2], [4, 4]], [4, 4], [3, 2]]}]
    code, rep, _ = run(make_work(tmp_path, tab, staff), tmp_path)
    assert code == 0, rep.get("disagreements")


@needs_alphatab
def test_voice2_checked_in_alphatab(tmp_path):
    work = make_work(tmp_path, TWO_VOICE_TAB, TWO_VOICE_STAFF)
    code, rep, out = run(work, tmp_path)
    assert code == 0
    p = run_alphatab(out, "--expect-notes", "6", "--plan", str(work / "plan.json"))
    assert p.returncode == 0, p.stdout + p.stderr
    plan = json.loads((work / "plan.json").read_text())
    plan["bars"][0]["voice2"][1]["dur"] = 4
    bad = tmp_path / "bad_plan.json"
    bad.write_text(json.dumps(plan))
    p = run_alphatab(out, "--plan", str(bad))
    assert p.returncode == 1 and "voice 2" in p.stderr


# ---- P0-3/4: legends -> flags, constant offsets, tempo, feel --------------------------------------

TUNE_DOWN = {"page": 1, "kind": "tuning", "text": "Half-step down tuning: (1)=Eb (2)=Bb (3)=Gb (4)=Db (5)=Ab (6)=Eb"}
EB = "Eb2,Ab2,Db3,Gb3,Bb3,Eb4"


def legend_work(tmp_path, legends, tab=GOOD_TAB, staff=GOOD_STAFF, **kw) -> Path:
    work = make_work(tmp_path, tab, staff, **kw)
    data = json.loads((work / "reads" / "staff_read.json").read_text())
    data["legends"] = legends
    (work / "reads" / "staff_read.json").write_text(json.dumps(data))
    return work


def test_tune_down_legend_without_flags_is_refused_with_the_exact_flags(tmp_path):
    work = legend_work(tmp_path, [TUNE_DOWN])
    code, rep, out = run(work, tmp_path)
    assert code == 2 and not out.exists()
    assert rep["legend_flags_required"] == f"--tuning {EB} --notation-transpose -1"
    assert f"--tuning {EB} --notation-transpose -1" in rep["error"]
    code, rep, out = run(work, tmp_path, "--tuning", EB, "--notation-transpose", "-1")
    assert code == 0, rep.get("disagreements") or rep.get("error")
    assert "NOT cross-checkable" in rep["tuning_capo_note"]
    code, rep, out = run(work, tmp_path, "--legend-override", "the legend is for Gtr. 2 only")
    assert code == 0 and any("LEGEND OVERRIDDEN" in w for w in rep["warnings"])


@pytest.mark.parametrize("text,flags", [
    ("Capo 4th fret", "--capo 4 --notation-transpose 4"),
    ("Capo II", "--capo 2 --notation-transpose 2"),
    ("DADGAD tuning: (6)=D (5)=A (4)=D (3)=G (2)=A (1)=D", "--tuning D2,A2,D3,G3,A3,D4"),
    ("Drop D tuning: (6)=D", "--tuning D2,A2,D3,G3,B3,E4"),
    ("Tune down 1 step", "--tuning D2,G2,C3,F3,A3,D4 --notation-transpose -2"),
])
def test_legend_flags(tmp_path, text, flags):
    work = legend_work(tmp_path, [{"page": 1, "kind": "capo" if "Capo" in text else "tuning", "text": text}])
    code, rep, _ = run(work, tmp_path)
    assert code == 2 and rep["legend_flags_required"] == flags


def test_constant_offset_is_named_and_no_reread_is_recommended(tmp_path):
    # tuned down 1/2 step but only --tuning passed: every note is one semitone off -> a flags problem
    tab = [{"notes": [[1, 0], [2, 1], [3, 2], [4, 2]]}, {"notes": [[1, 0], [2, 1], [3, 2], [4, 2]]}]
    staff = [GOOD_STAFF[0], {"events": GOOD_STAFF[0]["events"]}]
    work = legend_work(tmp_path, [TUNE_DOWN], tab=tab, staff=staff)
    code, rep, _ = run(work, tmp_path, "--tuning", EB)
    assert code == 1
    assert rep["constant_offset"]["semitones"] == -1 and rep["constant_offset"]["notes"] == 8
    assert "--notation-transpose -1" in rep["constant_offset"]["advice"]
    assert "do not re-read" in rep["recommendation"]
    # without any legend the advice names both possible causes
    code, rep, _ = run(make_work(tmp_path / "b", tab, staff), tmp_path / "b", "--tuning", EB)
    assert code == 1 and "no tuning/capo legend" in rep["constant_offset"]["advice"]


def test_tempo_from_legend_changes_and_loud_default(tmp_path, capsys):
    legends = [{"page": 1, "kind": "tempo", "text": "Moderately q = 96", "bpm": 96},
               {"page": 1, "kind": "tempo", "text": "Faster q = 132", "bpm": 132, "system": "p1_s2"}]
    work = legend_work(tmp_path, legends, more_systems={"p1_s2": (GOOD_TAB, [{"events": GOOD_STAFF[0]["events"]}],
                                                                  "none")})
    code, rep, out = run(work, tmp_path)
    assert code == 0 and rep["tempo"] == 96 and "legend" in rep["tempo_source"]
    song = gp.parse(str(out))
    assert song.tempo == 96
    assert song.tracks[0].measures[1].voices[0].beats[0].effect.mixTableChange.tempo.value == 132
    capsys.readouterr()
    code, rep, _ = run(make_work(tmp_path / "b", GOOD_TAB, GOOD_STAFF), tmp_path / "b")
    assert code == 0 and rep["tempo"] == 90 and rep["tempo_source"].startswith("DEFAULT")
    assert "NO TEMPO MARK" in capsys.readouterr().out


def test_shuffle_is_written_as_triplet_feel(tmp_path):
    work = legend_work(tmp_path, [{"page": 1, "kind": "tempo", "text": "Hard shuffle q = 126", "bpm": 126}])
    code, rep, out = run(work, tmp_path)
    assert code == 0 and rep["triplet_feel"] == "eighth"
    assert gp.parse(str(out)).measureHeaders[0].tripletFeel.name == "eighth"
    assert not any("feel" in d for d in rep["dropped_playback"])


# ---- P0-5: honest summary -----------------------------------------------------------------------------


def test_round1_counts_and_adjudication_kinds(tmp_path, capsys):
    tab = [{"notes": [[1, 0], [2, 1], [3, 3], [4, 2]]}]
    adj = [{"target": "tab", "system": "p1_s1", "bar": 1, "op": "replace", "index": 3, "value": [3, 2],
            "kind": "glyph", "reason": "zoom: the digit is a 2"}]
    code, rep, _ = run(make_work(tmp_path, tab, GOOD_STAFF, adjudications=adj), tmp_path)
    assert code == 0
    assert rep["round1"]["disagreements"] == 1 and rep["round1"]["agreement"] == "0/4"
    assert rep["adjudication_kinds"] == {"glyph": 1}
    assert "round 1, as read: 0/4" in capsys.readouterr().out


def test_interpretations_and_dropped_meaning_are_in_the_headline(tmp_path, capsys):
    staff = [{"time_sig": "4/4", "events": [q("E", 5), q("C", 5), q("A", 4), q("E", 4)]}]
    tab = [{"notes": [[1, 0], [2, 1], [3, 2], [4, 2]], "tech": [{"note": 2, "type": "b", "text": "1/2"}],
            "grace": [{"before": 1, "string": 1, "fret": 2}]}]
    adj = [{"target": "staff", "system": "p1_s1", "bar": 1, "op": "set", "field": "partial", "value": False,
            "kind": "interpretation", "reason": "bend target printed as a note; written as the plain note",
            "drops": "1/2-step bend on string 2"}]
    work = make_work(tmp_path, tab, staff, adjudications=adj,
                     staff_extra={"omitted": ["bar 1: two dead notes (x) before beat 3", "descending run label"]})
    capsys.readouterr()
    code, rep, _ = run(work, tmp_path)
    out = capsys.readouterr().out
    assert code == 0
    assert "INTERPRETATIONS" in out and "bend target printed as a note" in out
    # counts come from the structured records only: the bend mark + the grace record, each once (the
    # adjudication's "drops" names the same bend and is listed, not counted); free text is listed verbatim
    assert sorted(rep["dropped_playback"]) == ["1 bends", "1 grace notes"]
    assert "p1_s1: bar 1: two dead notes (x) before beat 3" in rep["other_items_not_in_file"]
    assert rep["dropped_by_interpretation"][0]["drops"] == "1/2-step bend on string 2"
    assert "NOT IN THE FILE" in out and "two dead notes (x) before beat 3" in out
    assert rep["interpretations"][0]["drops"] == "1/2-step bend on string 2"


def test_unclassified_adjudication_is_flagged_and_bad_kind_refused(tmp_path):
    tab = [{"notes": [[1, 0], [2, 1], [3, 3], [4, 2]]}]
    adj = [{"target": "tab", "system": "p1_s1", "bar": 1, "op": "replace", "index": 3, "value": [3, 2],
            "reason": "zoom: 2"}]
    code, rep, _ = run(make_work(tmp_path, tab, GOOD_STAFF, adjudications=adj), tmp_path)
    assert code == 0 and rep["adjudication_kinds"] == {"unclassified": 1}
    assert any("have no 'kind'" in w for w in rep["warnings"])
    adj[0]["kind"] = "typo"
    code, rep, _ = run(make_work(tmp_path / "b", tab, GOOD_STAFF, adjudications=adj), tmp_path / "b")
    assert code == 2 and "kind must be" in rep["error"]


def test_adjudication_can_resolve_an_omitted_item(tmp_path):
    adj = [{"target": "staff", "system": "p1_s1", "bar": 1, "op": "set", "field": "time_sig", "value": "4/4",
            "kind": "glyph", "reason": "voice 2 merged", "resolves_omitted": ["voice 2 (stems down) bar 1"]}]
    work = make_work(tmp_path, GOOD_TAB, GOOD_STAFF, adjudications=adj,
                     staff_extra={"omitted": ["voice 2 (stems down) bar 1: beat 1 A3 quarter", "slur bar 1"]})
    code, rep, _ = run(work, tmp_path)
    assert code == 0 and rep["omitted_by_readers"] == ["p1_s1: slur bar 1"]


@pytest.mark.parametrize("item,structural", [
    ("bar 1. grace note before beat 2", False),
    ("descending line (slide-out) after event 13", False),
    ("f dynamic at bar 1.", False),
    ("1st ending bracket over bar 3", True),
    ("D.S. al Coda at the end", True),
    ("volta 2 over the last bar", True),
    ("bars 3-4: 1. and 2. endings", True),
])
def test_structure_markings_regex(tmp_path, item, structural):
    work = make_work(tmp_path, GOOD_TAB, GOOD_STAFF, staff_extra={"omitted": [item]})
    code, rep, _ = run(work, tmp_path)
    assert code == 0
    assert any("structure markings" in w for w in rep["warnings"]) == structural


def test_systematic_disagreements_do_not_recommend_a_reread(tmp_path):
    # every bar has one more TAB column than staff notes (grace digits in the TAB): a re-read reproduces it
    bars_t = [{"notes": [[1, 2], [1, 0], [2, 1], [3, 2], [4, 2]], "grace": [{"before": 1, "string": 1, "fret": 2}]}] * 4
    bars_s = [GOOD_STAFF[0]] + [{"events": GOOD_STAFF[0]["events"]}] * 3
    code, rep, _ = run(make_work(tmp_path, bars_t, bars_s), tmp_path)
    assert code == 1
    assert rep["recommendation"].startswith("systematic")
    assert "grace note(s) recorded" in rep["disagreements"][0]
    assert rep["disagreement_types"] == {"column count (grace notes in the bar)": 4}


@pytest.mark.parametrize("text,down,capo,feel", [
    ("Tuned Down 1/2 Step:", 1, None, None),
    ("Tune down one whole step", 2, None, None),
    ("Tune down 1 1/2 steps", 3, None, None),
    ("Capo III", 0, 3, None),
    ("Capo at the 5th fret", 0, 5, None),
    ("Hard Shuffle (quarter = 126)", 0, None, "eighth"),
    ("Swing 16ths", 0, None, "sixteenth"),
    ("Straight eighths, no swing", 0, None, None),
])
def test_legend_texts_are_interpreted_by_the_script(text, down, capo, feel):
    lg = rb.parse_legends([{"page": 1, "kind": "other", "text": text}])
    assert (lg.tune_down, lg.capo, lg.feel) == (down, capo, feel)
