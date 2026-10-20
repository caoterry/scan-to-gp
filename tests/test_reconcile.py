"""reconcile_build.py on synthetic reads: every agreement and every disagreement type."""

import json
from pathlib import Path

import guitarpro as gp
import pytest

import reconcile_build as rb
from conftest import needs_alphatab, run_alphatab


def q(letter, octave, acc="none", dur="4", **kw):
    return {"letter": letter, "octave": octave, "acc": acc, "dur": dur, **kw}


def rest(dur="4", **kw):
    return {"rest": True, "dur": dur, **kw}


def make_work(tmp_path: Path, tab_bars, staff_bars, key="none", sid="p1_s1", tab_extra=None, staff_extra=None,
              more_systems=None, adjudications=None) -> Path:
    work = tmp_path / "work"
    (work / "reads").mkdir(parents=True)
    tab = {"systems": {sid: {"bars": [{"bar": i + 1, **b} for i, b in enumerate(tab_bars)], **(tab_extra or {})}}}
    staff = {"systems": {sid: {"key_sig": key, "clef": "treble",
                               "bars": [{"bar": i + 1, **b} for i, b in enumerate(staff_bars)], **(staff_extra or {})}}}
    for msid, (tb, sb, k) in (more_systems or {}).items():
        tab["systems"][msid] = {"bars": [{"bar": i + 1, **b} for i, b in enumerate(tb)]}
        staff["systems"][msid] = {"key_sig": k, "bars": [{"bar": i + 1, **b} for i, b in enumerate(sb)]}
    (work / "reads" / "tab_read.json").write_text(json.dumps(tab))
    (work / "reads" / "staff_read.json").write_text(json.dumps(staff))
    if adjudications is not None:
        (work / "adjudications.json").write_text(json.dumps({"adjudications": adjudications}))
    return work


def run(work: Path, tmp_path: Path, *args) -> tuple[int, dict, Path]:
    out = tmp_path / "out.gp5"
    code = rb.main([str(work), str(out), *args])
    rep = json.loads((work / "report.json").read_text())
    return code, rep, out


# Open strings + simple frets in standard tuning (written pitch = sounding + 12):
# string 1 fret 0 = E4 sounding = E5 written; string 2 fret 1 = C4 sounding = C5 written;
# string 3 fret 2 = A3 sounding = A4 written; string 4 fret 2 = E3 sounding = E4 written.
GOOD_TAB = [{"notes": [[1, 0], [2, 1], [3, 2], [4, 2]]}]
GOOD_STAFF = [{"time_sig": "4/4", "events": [q("E", 5), q("C", 5), q("A", 4), q("E", 4)]}]


def test_agree_writes_file_and_reads_back(tmp_path):
    code, rep, out = run(make_work(tmp_path, GOOD_TAB, GOOD_STAFF), tmp_path, "--tempo", "100")
    assert code == 0 and out.exists()
    assert rep["ok"] and rep["agreement"] == "4/4" and rep["notes_written"] == 4 and rep["bars_written"] == 1
    song = gp.parse(str(out))
    beats = song.tracks[0].measures[0].voices[0].beats
    assert [(n.string, n.value) for b in beats for n in b.notes] == [(1, 0), (2, 1), (3, 2), (4, 2)]
    assert song.tempo == 100


def test_wrong_fret_is_caught_and_no_file(tmp_path):
    tab = [{"notes": [[1, 0], [2, 1], [3, 3], [4, 2]]}]
    code, rep, out = run(make_work(tmp_path, tab, GOOD_STAFF), tmp_path)
    assert code == 1 and not out.exists() and not rep["ok"]
    assert len(rep["disagreements"]) == 1
    msg = rep["disagreements"][0]
    assert "tab entry #3" in msg and "staff event #3" in msg and "fret 3" in msg


def test_string_off_by_one_is_caught(tmp_path):
    tab = [{"notes": [[1, 0], [3, 1], [3, 2], [4, 2]]}]  # C on string 3 instead of 2
    code, rep, _ = run(make_work(tmp_path, tab, GOOD_STAFF), tmp_path)
    assert code == 1 and "tab entry #2" in rep["disagreements"][0]


def test_wrong_octave_in_staff_is_caught(tmp_path):
    staff = [{"time_sig": "4/4", "events": [q("E", 5), q("C", 5), q("A", 3), q("E", 4)]}]
    code, rep, _ = run(make_work(tmp_path, GOOD_TAB, staff), tmp_path)
    assert code == 1 and "staff event #3" in rep["disagreements"][0]


def test_note_count_mismatch(tmp_path):
    tab = [{"notes": [[1, 0], [2, 1], [3, 2]]}]
    code, rep, _ = run(make_work(tmp_path, tab, GOOD_STAFF), tmp_path)
    assert code == 1 and "tab has 3 columns, staff has 4" in rep["disagreements"][0]


def test_bar_count_mismatch(tmp_path):
    code, rep, _ = run(make_work(tmp_path, GOOD_TAB * 2, GOOD_STAFF), tmp_path)
    assert code == 1 and "bar count differs" in rep["disagreements"][0]


def test_durations_must_fill_the_bar(tmp_path):
    staff = [{"time_sig": "4/4", "events": [q("E", 5), q("C", 5), q("A", 4), q("E", 4, dur="8")]}]
    code, rep, _ = run(make_work(tmp_path, GOOD_TAB, staff), tmp_path)
    assert code == 1 and "sum to 7/2 beats" in rep["disagreements"][0]


def test_key_signature_and_accidental_carry(tmp_path):
    # key 1 sharp (F#). Bar 1: F (key -> F#), F natural printed, F again (carried natural), B.
    # Bar 2: F with no accidental -> back to the key's F#.
    # string 4 fret 3 = F3 sounding (F4 written), fret 4 = F#3.
    tab = [{"notes": [[4, 4], [4, 3], [4, 3], [3, 4]]}, {"notes": [[4, 4], [4, 4], [4, 4], [4, 4]]}]
    staff = [{"time_sig": "4/4", "events": [q("F", 4), q("F", 4, "natural"), q("F", 4), q("B", 4)]},
             {"events": [q("F", 4)] * 4}]
    code, rep, _ = run(make_work(tmp_path, tab, staff, key="1 sharp"), tmp_path)
    assert code == 0, rep["disagreements"]
    # a tab read that ignores the carried natural must fail
    tab_bad = [{"notes": [[4, 4], [4, 3], [4, 4], [3, 4]]}, tab[1]]
    code, rep, _ = run(make_work(tmp_path / "b", tab_bad, staff, key="1 sharp"), tmp_path / "b")
    assert code == 1 and "tab entry #3" in rep["disagreements"][0]


def test_flat_key_signature(tmp_path):
    # 2 flats: B -> Bb, E -> Eb. string 3 fret 3 = Bb3 sounding = Bb4 written; string 2 fret 4 = Eb4 -> Eb5 written
    tab = [{"notes": [[3, 3], [2, 4], [3, 3], [2, 4]]}]
    staff = [{"time_sig": "4/4", "events": [q("B", 4), q("E", 5), q("B", 4), q("E", 5)]}]
    code, rep, _ = run(make_work(tmp_path, tab, staff, key="2 flats"), tmp_path)
    assert code == 0, rep["disagreements"]


def test_chord_is_compared_as_multiset(tmp_path):
    tab = [{"notes": [[[2, 1], [3, 2], [4, 2]], [1, 0], [2, 1], [3, 2]]}]
    staff = [{"time_sig": "4/4", "events": [
        {"chord": [{"letter": "E", "octave": 4}, {"letter": "C", "octave": 5}, {"letter": "A", "octave": 4}], "dur": "4"},
        q("E", 5), q("C", 5), q("A", 4)]}]
    code, rep, out = run(make_work(tmp_path, tab, staff), tmp_path)
    assert code == 0, rep["disagreements"]
    assert len(gp.parse(str(out)).tracks[0].measures[0].voices[0].beats[0].notes) == 3
    staff[0]["events"][0]["chord"].pop()  # staff chord missing a note
    code, rep, _ = run(make_work(tmp_path / "b", tab, staff), tmp_path / "b")
    assert code == 1


def test_tie_continuation_omitted_or_parenthesized_in_tab(tmp_path):
    staff = [{"time_sig": "4/4", "events": [q("E", 4, dur="2"), q("E", 4, dur="2", tie=True)]},
             {"events": [q("E", 4, dur="1")]}]
    # tab omits the tied note
    tab = [{"notes": [[4, 2]]}, {"notes": [[4, 2]]}]
    code, rep, out = run(make_work(tmp_path, tab, staff), tmp_path)
    assert code == 0, rep["disagreements"]
    assert rep["tied_continuations_not_in_tab"] == 1
    beats = gp.parse(str(out)).tracks[0].measures[0].voices[0].beats
    assert beats[1].notes[0].type.name == "tie" and beats[1].notes[0].string == 4
    # tab shows "(2)" for the tied note
    tab_p = [{"notes": [[4, 2], [4, 2, "paren"]]}, {"notes": [[4, 2]]}]
    code, rep, _ = run(make_work(tmp_path / "b", tab_p, staff), tmp_path / "b")
    assert code == 0, rep["disagreements"]


def test_tie_across_barline_keeps_origin_accidental(tmp_path):
    # key none; F#4 printed sharp at the end of bar 1, tied into bar 2 with no accidental -> still F#
    staff = [{"time_sig": "4/4", "events": [q("E", 4, dur="2"), q("F", 4, "sharp", dur="2")]},
             {"events": [q("F", 4, dur="2", tie=True), q("F", 4, dur="2")]}]
    tab = [{"notes": [[4, 2], [4, 4]]}, {"notes": [[4, 3]]}]  # bar 2: only the new (natural) F
    code, rep, _ = run(make_work(tmp_path, tab, staff), tmp_path)
    assert code == 0, rep["disagreements"]


def test_tie_without_origin_is_an_error(tmp_path):
    staff = [{"time_sig": "4/4", "events": [q("E", 4, dur="2"), q("A", 4, dur="2", tie=True)]}]
    tab = [{"notes": [[4, 2]]}]
    code, rep, out = run(make_work(tmp_path, tab, staff), tmp_path)
    assert code == 2 and not out.exists() and "tie" in rep["error"]


def test_pickup_bar_gets_its_own_short_time_signature(tmp_path):
    tab = [{"partial": True, "notes": [[4, 2]]}] + GOOD_TAB
    staff = [{"time_sig": "4/4", "partial": True, "events": [q("E", 4, dur="8")]}, {"events": GOOD_STAFF[0]["events"]}]
    code, rep, out = run(make_work(tmp_path, tab, staff), tmp_path)
    assert code == 0, rep["disagreements"]
    hdrs = gp.parse(str(out)).measureHeaders
    assert (hdrs[0].timeSignature.numerator, hdrs[0].timeSignature.denominator.value) == (1, 8)
    assert (hdrs[1].timeSignature.numerator, hdrs[1].timeSignature.denominator.value) == (4, 4)
    beats = gp.parse(str(out)).tracks[0].measures[0].voices[0].beats
    assert len(beats) == 1 and beats[0].status.name == "normal"  # no fabricated rests


def test_short_bar_in_the_middle_is_rejected(tmp_path):
    tab = GOOD_TAB + [{"partial": True, "notes": [[4, 2]]}] + GOOD_TAB
    staff = [GOOD_STAFF[0], {"partial": True, "events": [q("E", 4)]}, {"events": GOOD_STAFF[0]["events"]}]
    code, rep, _ = run(make_work(tmp_path, tab, staff), tmp_path)
    assert code == 1 and "not the first or last" in rep["disagreements"][0]


def test_dotted_and_triplets_fill_the_bar(tmp_path):
    staff = [{"time_sig": "4/4", "events": [
        q("E", 4, dur="4", dots=1), q("E", 4, dur="8"),
        q("E", 4, dur="8", tuplet=3), q("E", 4, dur="8", tuplet=3), q("E", 4, dur="8", tuplet=3),
        rest("4")]}]
    tab = [{"notes": [[4, 2]] * 5}]
    code, rep, out = run(make_work(tmp_path, tab, staff), tmp_path)
    assert code == 0, rep["disagreements"]
    beats = gp.parse(str(out)).tracks[0].measures[0].voices[0].beats
    assert beats[0].duration.isDotted and beats[2].duration.tuplet.enters == 3 and beats[5].status.name == "rest"
    # legacy "triplet": true spelling is accepted too
    staff[0]["events"][2:5] = [q("E", 4, dur="8", triplet=True)] * 3
    code, rep, _ = run(make_work(tmp_path / "b", tab, staff), tmp_path / "b")
    assert code == 0


def test_time_signature_inferred_then_changed(tmp_path):
    tab = GOOD_TAB + [{"notes": [[1, 0], [2, 1], [3, 2]]}]
    staff = [{"events": GOOD_STAFF[0]["events"]},
             {"time_sig": "3/4", "events": [q("E", 5), q("C", 5), q("A", 4)]}]
    code, rep, out = run(make_work(tmp_path, tab, staff), tmp_path)
    assert code == 0, rep["disagreements"]
    assert any("inferred 4/4" in w for w in rep["warnings"])
    hdrs = gp.parse(str(out)).measureHeaders
    assert [(h.timeSignature.numerator, h.timeSignature.denominator.value) for h in hdrs] == [(4, 4), (3, 4)]


def test_repeat_signs_must_agree(tmp_path):
    tab = [{"repeat_start": True, "repeat_end": 2, **GOOD_TAB[0]}]
    staff = [{"repeat_start": True, **GOOD_STAFF[0]}]
    code, rep, _ = run(make_work(tmp_path, tab, staff), tmp_path)
    assert code == 1 and "repeat signs differ" in rep["disagreements"][0]
    staff = [{"repeat_start": True, "repeat_end": 2, **GOOD_STAFF[0]}]
    code, rep, out = run(make_work(tmp_path / "b", tab, staff), tmp_path / "b")
    assert code == 0
    h = gp.parse(str(out)).measureHeaders[0]
    assert h.isRepeatOpen and h.repeatClose == 1


def test_adjudication_patch_is_applied_and_counted(tmp_path):
    tab = [{"notes": [[1, 0], [2, 1], [3, 3], [4, 2]]}]
    adj = [{"target": "tab", "system": "p1_s1", "bar": 1, "op": "replace", "index": 3, "value": [3, 2],
            "reason": "zoom: the digit is a 2 (no loop at the top), staff A4 agrees"}]
    code, rep, _ = run(make_work(tmp_path, tab, GOOD_STAFF, adjudications=adj), tmp_path)
    assert code == 0 and rep["adjudications_applied"] == 1
    # the raw read file is untouched -- the log is the only edit
    raw = json.loads((tmp_path / "work" / "reads" / "tab_read.json").read_text())
    assert raw["systems"]["p1_s1"]["bars"][0]["notes"][2] == [3, 3]


def test_adjudication_without_reason_is_refused(tmp_path):
    adj = [{"target": "tab", "system": "p1_s1", "bar": 1, "op": "replace", "index": 3, "value": [3, 2], "reason": ""}]
    code, rep, _ = run(make_work(tmp_path, GOOD_TAB, GOOD_STAFF, adjudications=adj), tmp_path)
    assert code == 2 and "reason" in rep["error"]


def test_unverified_tab_only_output_is_labelled(tmp_path):
    work = tmp_path / "work"
    (work / "reads").mkdir(parents=True)
    (work / "reads" / "tab_read.json").write_text(json.dumps({"systems": {"p1_s1": {"bars": GOOD_TAB}}}))
    out = tmp_path / "song.gp5"
    assert rb.main([str(work), str(out)]) == 2  # refused without --unverified
    assert rb.main([str(work), str(out), "--unverified"]) == 0
    rep = json.loads((work / "report.json").read_text())
    assert "UNVERIFIED" in Path(rep["output"]).name and Path(rep["output"]).exists() and not out.exists()


def test_custom_tuning_drop_d(tmp_path):
    tab = [{"notes": [[6, 0], [6, 2], [6, 0], [6, 2]]}]  # drop D: string 6 open = D2 -> D3 written
    staff = [{"time_sig": "4/4", "events": [q("D", 3), q("E", 3), q("D", 3), q("E", 3)]}]
    code, rep, _ = run(make_work(tmp_path, tab, staff), tmp_path)
    assert code == 1  # standard tuning disagrees
    code, rep, out = run(make_work(tmp_path / "b", tab, staff), tmp_path / "b", "--tuning", "D2,A2,D3,G3,B3,E4")
    assert code == 0, rep["disagreements"]
    assert gp.parse(str(out)).tracks[0].strings[5].value == 38


def test_techniques_reported_not_written_by_default(tmp_path):
    tab = [{"notes": [[3, 2], [3, 4], [3, 2], [3, 4]], "tech": [{"note": 1, "type": "h", "to": 2}]}]
    staff = [{"time_sig": "4/4", "events": [q("A", 4), q("B", 4), q("A", 4), q("B", 4)]}]
    code, rep, out = run(make_work(tmp_path, tab, staff), tmp_path)
    assert code == 0 and len(rep["techniques_seen_in_tab"]) == 1 and rep["techniques_written"] == []
    assert not gp.parse(str(out)).tracks[0].measures[0].voices[0].beats[0].notes[0].effect.hammer
    code, rep, out = run(make_work(tmp_path / "b", tab, staff), tmp_path / "b", "--write-techniques")
    assert code == 0 and len(rep["techniques_written"]) == 1
    assert gp.parse(str(out)).tracks[0].measures[0].voices[0].beats[0].notes[0].effect.hammer


def test_systems_concatenate_with_sections(tmp_path):
    work = make_work(tmp_path, GOOD_TAB, GOOD_STAFF, staff_extra={"section": "Example 1"},
                     more_systems={"p1_s2": (GOOD_TAB, [{"events": GOOD_STAFF[0]["events"]}], "none")})
    code, rep, out = run(work, tmp_path)
    assert code == 0 and rep["bars_written"] == 2 and rep["notes_written"] == 8
    assert gp.parse(str(out)).measureHeaders[0].marker.title == "Example 1"


@pytest.mark.parametrize("bad", ["17 sharps", "lots"])
def test_bad_key_signature_is_input_error(tmp_path, bad):
    code, rep, _ = run(make_work(tmp_path, GOOD_TAB, GOOD_STAFF, key=bad), tmp_path)
    assert code == 2


@needs_alphatab
def test_alphatab_loads_built_file(tmp_path):
    tab = [{"partial": True, "notes": [[4, 2]]}, {"notes": [[[2, 1], [3, 2]], [1, 0], [2, 1], [3, 2]]}]
    staff = [{"time_sig": "4/4", "partial": True, "events": [q("E", 4, dur="8")]},
             {"events": [{"chord": [{"letter": "C", "octave": 5}, {"letter": "A", "octave": 4}], "dur": "4"},
                         q("E", 5), q("C", 5), q("A", 4, dur="8"), rest("8")]}]
    code, rep, out = run(make_work(tmp_path, tab, staff), tmp_path)
    assert code == 0, rep["disagreements"]
    p = run_alphatab(out, "--expect-bars", "2", "--expect-notes", "6")
    assert p.returncode == 0, p.stdout + p.stderr


def test_whole_rest_fills_a_three_four_bar(tmp_path):
    tab = [{"notes": [[1, 0], [2, 1], [3, 2]]}, {"notes": []}]
    staff = [{"time_sig": "3/4", "events": [q("E", 5), q("C", 5), q("A", 4)]}, {"events": [rest("1")]}]
    code, rep, out = run(make_work(tmp_path, tab, staff), tmp_path)
    assert code == 0, rep["disagreements"]
    b = gp.parse(str(out)).tracks[0].measures[1].voices[0].beats[0]
    assert b.status.name == "rest" and b.duration.value == 2 and b.duration.isDotted


# ---- review round 1: false-agreement paths and robustness -------------------------------------

TIED_STAFF = [{"time_sig": "4/4", "events": [q("E", 4, dur="2"), q("E", 4, dur="2", tie=True)]}]


def test_tied_staff_note_vs_plain_restruck_tab_digit_disagrees(tmp_path):
    tab = [{"notes": [[4, 2], [4, 2]]}]  # a plain digit = a new attack, not a tie
    code, rep, out = run(make_work(tmp_path, tab, TIED_STAFF), tmp_path)
    assert code == 1 and not out.exists()
    assert "tie/paren markings disagree" in rep["disagreements"][0]
    # books that reprint tied notes as plain digits: explicit opt-in
    code, rep, _ = run(make_work(tmp_path / "b", tab, TIED_STAFF), tmp_path / "b", "--tab-reprints-ties")
    assert code == 0 and rep["tab_reprints_ties"]


def test_paren_tab_digit_vs_untied_staff_note_disagrees(tmp_path):
    staff = [{"time_sig": "4/4", "events": [q("E", 4, dur="2"), q("E", 4, dur="2")]}]
    tab = [{"notes": [[4, 2], [4, 2, "paren"]]}]
    code, rep, _ = run(make_work(tmp_path, tab, staff), tmp_path)
    assert code == 1


def test_ghost_note_needs_paren_in_tab(tmp_path):
    staff = [{"time_sig": "4/4", "events": [q("E", 4, dur="2"), q("E", 4, dur="2", ghost=True)]}]
    code, rep, out = run(make_work(tmp_path, [{"notes": [[4, 2], [4, 2, "paren"]]}], staff), tmp_path)
    assert code == 0, rep["disagreements"]
    assert gp.parse(str(out)).tracks[0].measures[0].voices[0].beats[1].notes[0].effect.ghostNote
    code, rep, _ = run(make_work(tmp_path / "b", [{"notes": [[4, 2], [4, 2]]}], staff), tmp_path / "b")
    assert code == 1


def test_readers_must_agree_on_partial_bars(tmp_path):
    # staff misreads the last bar's second half note as a quarter and calls the bar partial;
    # the tab reader sees a normal full-width bar -> must be a disagreement, not a 3/4 bar
    tab = GOOD_TAB + [{"notes": [[4, 2], [4, 2]]}]
    staff = [GOOD_STAFF[0], {"partial": True, "events": [q("E", 4, dur="2"), q("E", 4, dur="4")]}]
    code, rep, out = run(make_work(tmp_path, tab, staff), tmp_path)
    assert code == 1 and not out.exists()
    assert any("partial" in d for d in rep["disagreements"])


def test_partial_bar_that_is_not_short_is_rejected(tmp_path):
    tab = [{"partial": True, **GOOD_TAB[0]}]
    staff = [{"partial": True, **GOOD_STAFF[0]}]
    code, rep, _ = run(make_work(tmp_path, tab, staff), tmp_path)
    assert code == 1 and "not short" in rep["disagreements"][0]


def test_failed_run_removes_previous_outputs(tmp_path):
    work = make_work(tmp_path, GOOD_TAB, GOOD_STAFF)
    out = tmp_path / "out.gp5"
    assert rb.main([str(work), str(out)]) == 0 and out.exists() and (work / "plan.json").exists()
    bad = json.loads((work / "reads" / "tab_read.json").read_text())
    bad["systems"]["p1_s1"]["bars"][0]["notes"][0] = [1, 1]
    (work / "reads" / "tab_read.json").write_text(json.dumps(bad))
    assert rb.main([str(work), str(out)]) == 1
    assert not out.exists() and not (work / "plan.json").exists()
    assert json.loads((work / "report.json").read_text())["ok"] is False


@pytest.mark.parametrize("event", ["E5", 5, ["E", 5]])
def test_malformed_staff_event_is_an_input_error_not_a_disagreement(tmp_path, event):
    staff = [{"time_sig": "4/4", "events": [event, q("C", 5), q("A", 4), q("E", 4)]}]
    code, rep, _ = run(make_work(tmp_path, GOOD_TAB, staff), tmp_path)
    assert code == 2 and rep["error"]


@pytest.mark.parametrize("adj", [
    {"op": "replace", "index": 0, "value": [3, 2]},
    {"op": "replace", "index": 9, "value": [3, 2]},
    {"op": "insert", "index": 7, "value": [3, 2]},
    {"op": "delete", "index": -1},
    {"op": "delete_bar", "bar": 0},
    {"op": "frobnicate", "index": 1},
])
def test_adjudication_bounds_are_checked(tmp_path, adj):
    a = {"target": "tab", "system": "p1_s1", "bar": 1, "reason": "test", **adj}
    code, rep, _ = run(make_work(tmp_path, GOOD_TAB, GOOD_STAFF, adjudications=[a]), tmp_path)
    assert code == 2, rep


def test_mixed_unverified_and_verified_without_time_signature(tmp_path):
    work = tmp_path / "work"
    (work / "reads").mkdir(parents=True)
    tab = {"systems": {"p1_s1": {"bars": GOOD_TAB}, "p1_s2": {"bars": GOOD_TAB}}}
    staff = {"systems": {"p1_s2": {"key_sig": "none", "bars": [{"events": GOOD_STAFF[0]["events"]}]}}}
    (work / "reads" / "tab_read.json").write_text(json.dumps(tab))
    (work / "reads" / "staff_read.json").write_text(json.dumps(staff))
    out = tmp_path / "mix.gp5"
    assert rb.main([str(work), str(out), "--unverified"]) == 0
    rep = json.loads((work / "report.json").read_text())
    assert rep["unverified_systems"] == ["p1_s1"] and "UNVERIFIED" in rep["output"]


def test_multirest_must_agree_and_expands(tmp_path):
    tab = GOOD_TAB + [{"multirest": 3, "notes": []}] + GOOD_TAB
    staff = [GOOD_STAFF[0], {"multirest": 3, "events": []}, {"events": GOOD_STAFF[0]["events"]}]
    code, rep, out = run(make_work(tmp_path, tab, staff), tmp_path)
    assert code == 0, rep["disagreements"]
    assert rep["bars_written"] == 5
    assert all(m.voices[0].beats[0].status.name == "rest" for m in gp.parse(str(out)).tracks[0].measures[1:4])
    tab[1]["multirest"] = 4
    code, rep, _ = run(make_work(tmp_path / "b", tab, staff), tmp_path / "b")
    assert code == 1 and "multi-bar rest differs" in rep["disagreements"][0]


def test_double_barline_only_when_read(tmp_path):
    work = make_work(tmp_path, GOOD_TAB, [{"end_barline": "double", **GOOD_STAFF[0]}], staff_extra={"section": "A"},
                     more_systems={"p1_s2": (GOOD_TAB, [{"events": GOOD_STAFF[0]["events"]}], "none")})
    code, rep, out = run(work, tmp_path)
    assert code == 0
    hdrs = gp.parse(str(out)).measureHeaders
    assert hdrs[0].hasDoubleBar and not hdrs[1].hasDoubleBar


def test_capo_and_notation_transpose(tmp_path):
    # capo 2: tab fret 0 on string 1 sounds F#4 -> written F#5
    tab = [{"notes": [[1, 0], [1, 0], [1, 0], [1, 0]]}]
    staff = [{"time_sig": "4/4", "events": [q("F", 5, "sharp")] + [q("F", 5)] * 3}]
    code, rep, out = run(make_work(tmp_path, tab, staff), tmp_path, "--capo", "2")
    assert code == 0, rep["disagreements"]
    assert gp.parse(str(out)).tracks[0].offset == 2
    # half-step-down book: notation printed as if standard; real tuning a half step lower
    tab = [{"notes": [[1, 0], [1, 0], [1, 0], [1, 0]]}]
    staff = [{"time_sig": "4/4", "events": [q("E", 5)] * 4}]
    eb = "Eb2,Ab2,Db3,Gb3,Bb3,Eb4"
    code, rep, _ = run(make_work(tmp_path / "b", tab, staff), tmp_path / "b", "--tuning", eb)
    assert code == 1
    code, rep, _ = run(make_work(tmp_path / "c", tab, staff), tmp_path / "c", "--tuning", eb,
                       "--notation-transpose", "-1")
    assert code == 0, rep["disagreements"]


def test_reread_supersedes_per_system(tmp_path):
    work = make_work(tmp_path, [{"notes": [[1, 0], [2, 1], [3, 3], [4, 2]]}], GOOD_STAFF,
                     more_systems={"p1_s2": (GOOD_TAB, [{"events": GOOD_STAFF[0]["events"]}], "none")})
    assert rb.main([str(work), str(tmp_path / "o.gp5")]) == 1
    (work / "reads" / "tab_1_reread1.json").write_text(json.dumps({"systems": {"p1_s1": {"bars": GOOD_TAB}}}))
    assert rb.main([str(work), str(tmp_path / "o.gp5")]) == 0
    rep = json.loads((work / "report.json").read_text())
    assert any("replaced by tab_1_reread1.json" in w for w in rep["warnings"]) and rep["bars_written"] == 2
    # a plain duplicate (not a re-read) is still an error
    (work / "reads" / "tab_2.json").write_text(json.dumps({"systems": {"p1_s1": {"bars": GOOD_TAB}}}))
    assert rb.main([str(work), str(tmp_path / "o.gp5")]) == 2


def test_tuplet_true_means_triplet(tmp_path):
    staff = [{"time_sig": "4/4", "events": [q("E", 4, dur="8", tuplet=True)] * 3 + [q("E", 4, dur="2", dots=1)]}]
    code, rep, _ = run(make_work(tmp_path, [{"notes": [[4, 2]] * 4}], staff), tmp_path)
    assert code == 0, rep


def test_extra_numbers_in_a_tab_entry_are_rejected(tmp_path):
    code, rep, _ = run(make_work(tmp_path, [{"notes": [[1, 0, 2, 5], [2, 1], [3, 2], [4, 2]]}], GOOD_STAFF), tmp_path)
    assert code == 2


@needs_alphatab
def test_alphatab_plan_comparison_catches_a_difference(tmp_path):
    work = make_work(tmp_path, GOOD_TAB, GOOD_STAFF)
    out = tmp_path / "out.gp5"
    assert rb.main([str(work), str(out)]) == 0
    assert run_alphatab(out, "--plan", str(work / "plan.json")).returncode == 0
    plan = json.loads((work / "plan.json").read_text())
    plan["bars"][0]["beats"][2]["notes"][0]["fret"] = 3
    bad = tmp_path / "bad_plan.json"
    bad.write_text(json.dumps(plan))
    p = run_alphatab(out, "--plan", str(bad))
    assert p.returncode == 1 and "string/fret" in p.stderr
