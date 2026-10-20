"""v0.2.1 honest summary: every NOT-IN-THE-FILE number has one source and nothing is counted twice.

Sources: TAB-reader technique marks (one per mark, palm mute / let ring as spans), the readers' structured
grace / dead / bend records (matched per bar, counted once), the build (ties across voices). Free-text
'omitted' items are listed verbatim; interpretation adjudications are listed with what they dropped."""

import json

from test_reconcile import GOOD_STAFF, GOOD_TAB, make_work, q, run
from test_reconcile_v2 import legend_work, write

# open strings + simple frets, standard tuning (see test_reconcile.GOOD_*)
TAB_BAR = {"notes": [[1, 0], [2, 1], [3, 2], [4, 2]]}


def test_one_event_seen_by_every_source_is_counted_once(tmp_path):
    tab = [{**TAB_BAR,
            "tech": [{"note": 2, "type": "b", "text": "1/2"}, {"note": 3, "type": "h", "text": "H"},
                     {"note": 3, "type": "h", "text": "H (same arc, listed twice)"}],
            "grace": [{"before": 2, "string": 2, "fret": 0}],
            "dead": [{"after": 1, "strings": [4, 5]}]}]
    staff = [{"time_sig": "4/4",
              "events": [q("E", 5), {**q("C", 5), "bend_target": True}, q("A", 4), q("E", 4)],
              "grace": [{"before": 2, "voice": 1, "letter": "B", "octave": 4, "acc": "none"}],
              "dead": [{"at": 1, "text": "x chord after beat 1"}]}]
    omitted = ["bar 1: grace note into beat 2", "bar 1: dead-note strum after beat 1",
               "bar 1: bend on beat 2", "bar 1: hammer-on into beat 3", "bar 1: staccato dot on beat 4"]
    adj = [{"target": "staff", "system": "p1_s1", "bar": 1, "op": "set", "field": "partial", "value": False,
            "kind": "interpretation", "reason": "the grace, the dead strum, the bend and the hammer-on stay out",
            "drops": "grace note, dead-note strum, bend and hammer-on of bar 1"}]
    work = make_work(tmp_path, tab, staff, staff_extra={"omitted": omitted}, tab_extra={"omitted": [omitted[0]]},
                     adjudications=adj)
    code, rep, _ = run(work, tmp_path)
    assert code == 0, rep.get("error") or rep.get("disagreements")
    assert sorted(rep["dropped_playback"]) == ["1 bends", "1 dead notes (x)", "1 grace notes", "1 hammer-ons"]
    assert rep["dropped_playback_reader_differences"] == []
    # free text: listed verbatim, once each (the TAB reader's duplicate of the first item is merged)
    assert rep["other_items_not_in_file"] == [f"p1_s1: {o}" for o in omitted]
    # the adjudication's drops: listed, never added to a count
    assert [d["drops"] for d in rep["dropped_by_interpretation"]] == [adj[0]["drops"]]
    assert "dropped by interpretation" not in json.dumps(rep["omitted_by_readers"])


def test_written_second_voice_gets_no_category(tmp_path):
    staff = [{"time_sig": "4/4", "events": [q("B", 4), q("D", 5), q("B", 4), q("A", 4),
                                             q("E", 3, dur="2", voice=2), q("A", 3, dur="2", voice=2)]}]
    tab = [{"notes": [[[6, 0], [2, 0]], [2, 3], [[5, 0], [2, 0]], [3, 2]]}]
    work = make_work(tmp_path, tab, staff, staff_extra={"omitted": [
        "voice 2 (stems down) bar 1: held bass E3 then A3, recorded with \"voice\": 2",
        "second voice stems down in bar 1"]})
    code, rep, _ = run(work, tmp_path)
    assert code == 0
    assert rep["dropped_playback"] == []
    assert not any("voice" in d for d in rep["dropped_playback"])


def test_palm_mute_tagged_on_every_note_of_one_span_is_one_span(tmp_path):
    bars_t = [{**TAB_BAR, "tech": [{"note": n, "type": "PM", "text": "P.M. (cont.)"} for n in (1, 2, 3, 4)]},
              {**TAB_BAR, "tech": [{"note": 1, "type": "PM"}, {"note": 2, "type": "pm"}, {"note": 4, "type": "PM"},
                                   {"note": 1, "type": "LR"}]}]
    bars_s = [GOOD_STAFF[0], {"events": GOOD_STAFF[0]["events"]}]
    code, rep, _ = run(make_work(tmp_path, bars_t, bars_s), tmp_path)
    assert code == 0
    # bar 1 notes 1-4 + bar 2 notes 1-2 = one printed span (continues over the barline); bar 2 note 4 another
    assert sorted(rep["dropped_playback"]) == ["1 let-ring spans", "2 palm-mute spans"]


def test_readers_that_differ_are_named_and_the_longer_list_counted(tmp_path):
    tab = [{**TAB_BAR, "grace": [{"before": 1, "string": 1, "fret": 2}, {"before": 3, "string": 3, "fret": 1}]}]
    staff = [{**GOOD_STAFF[0], "grace": [{"before": 1, "voice": 1, "letter": "F", "octave": 5, "acc": "sharp"}]}]
    code, rep, _ = run(make_work(tmp_path, tab, staff), tmp_path)
    assert code == 0 and rep["dropped_playback"] == ["2 grace notes"]
    diff = rep["dropped_playback_reader_differences"]
    assert len(diff) == 1 and "p1_s1 bar 1" in diff[0] and "the TAB reader's list" in diff[0]


def test_techniques_written_with_the_flag_are_not_counted(tmp_path):
    tab = [{**TAB_BAR, "tech": [{"note": 1, "type": "h"}, {"note": 2, "type": "t"}]}]
    code, rep, _ = run(make_work(tmp_path, tab, GOOD_STAFF), tmp_path, "--write-techniques")
    assert code == 0 and rep["dropped_playback"] == ["1 taps"]  # the hammer-on is in the file


def test_interpretation_that_writes_a_rest_says_so(tmp_path, capsys):
    staff = [{"time_sig": "4/4", "events": [q("E", 5), q("C", 5), q("A", 4, dur="8"), q("E", 4)]}]
    adj = [{"target": "staff", "system": "p1_s1", "bar": 1, "op": "insert", "index": 4, "value": {"rest": True, "dur": "8"},
            "kind": "interpretation", "reason": "x-note strum: the file cannot hold a dead note",
            "drops": "dead-note strum on beat 3&"}]
    work = make_work(tmp_path, GOOD_TAB, staff, adjudications=adj)
    capsys.readouterr()
    code, rep, _ = run(work, tmp_path)
    assert code == 0, rep.get("disagreements")
    assert rep["dropped_by_interpretation"][0]["written_as"] == "a rest"
    assert "p1_s1 bar 1: dead-note strum on beat 3& -> written as a rest" in capsys.readouterr().out


def test_round1_is_reported_on_clean_runs(tmp_path):
    code, rep, _ = run(make_work(tmp_path, GOOD_TAB, GOOD_STAFF), tmp_path)
    assert code == 0 and rep["round1"] == {"disagreements": 0, "tab_notes_agreed": 4, "tab_notes": 4,
                                           "agreement": "4/4"}


def test_prepare_and_tempo_warnings_reach_the_report(tmp_path):
    work = make_work(tmp_path, GOOD_TAB, GOOD_STAFF)
    write(work, "manifest.json", {"systems": [{"id": "p1_s1", "kind": "staff+tab", "tab_lines": 6}],
                                  "warnings": ["p1_s1: low resolution (line spacing 9.0px)"]})
    code, rep, _ = run(work, tmp_path)
    assert code == 0
    assert "prepare: p1_s1: low resolution (line spacing 9.0px)" in rep["warnings"]
    assert any(w.startswith("NO TEMPO MARK") for w in rep["warnings"])


def test_tempo_text_without_a_number_is_named(tmp_path, capsys):
    work = legend_work(tmp_path, [{"page": 1, "kind": "tempo", "text": "Rubato"}])
    capsys.readouterr()
    code, rep, _ = run(work, tmp_path)
    assert code == 0 and rep["tempo"] == 90 and rep["tempo_source"].startswith("DEFAULT")
    assert any("NO METRONOME NUMBER" in w and "'Rubato'" in w for w in rep["warnings"])
    assert "NO METRONOME NUMBER" in capsys.readouterr().out


def test_two_marks_in_one_column_on_different_strings_are_two(tmp_path):
    tab = [{"notes": [[1, 0], [[2, 1], [3, 2]], [3, 2], [4, 2]],
            "tech": [{"note": 2, "type": "p", "text": "P above (string 2)"}, {"note": 2, "type": "p", "string": 3},
                     {"note": 2, "type": "p", "string": 3, "text": "the same arc again"}]}]
    staff = [{"time_sig": "4/4", "events": [q("E", 5), {"chord": [{"letter": "C", "octave": 5},
                                                                  {"letter": "A", "octave": 4}], "dur": "4"},
                                             q("A", 4), q("E", 4)]}]
    code, rep, _ = run(make_work(tmp_path, tab, staff), tmp_path)
    assert code == 0, rep.get("disagreements")
    assert rep["dropped_playback"] == ["2 pull-offs"]
