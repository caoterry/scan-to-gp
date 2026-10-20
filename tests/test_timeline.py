"""v0.2.1 timeline: a skipped system keeps its bars (untranscribed, empty -- never rests or notes), a part
label naming another part becomes its own track on the common timeline, and an unknown skipped-bar
count is refused with the flag that settles it. Never a collapsed timeline, never a silent merge."""

import json

import guitarpro as gp
import numpy as np
import pytest

import reconcile_build as rb
import stg_image as si
from conftest import needs_alphatab, run_alphatab
from test_reconcile import GOOD_STAFF, GOOD_TAB, make_work, run
from test_reconcile_v2 import write

EV = GOOD_STAFF[0]["events"]


def fill_after_a_gap(tmp_path, gap_bars=2, labels=("Gtr. 1", None, "Gtr. 2"), legends=None):
    """Three one-bar systems in three groups: Gtr. 1, (unlabelled), a skipped vocal-only system, Gtr. 2."""
    more = {"p1_s2": (GOOD_TAB, [{"events": EV}], "none"),
            "p1_s3": ([{"notes": [[1, 3]] * 4}], [{"events": [{"letter": "G", "octave": 5, "acc": "none", "dur": "4"}] * 4}],
                      "none")}
    work = make_work(tmp_path, GOOD_TAB, GOOD_STAFF, more_systems=more)
    data = json.loads((work / "reads" / "staff_read.json").read_text())
    for sid, lab in zip(("p1_s1", "p1_s2", "p1_s3"), labels):
        if lab:
            data["systems"][sid]["part_label"] = lab
    if legends:
        data["legends"] = legends
    (work / "reads" / "staff_read.json").write_text(json.dumps(data))
    systems = [{"id": f"p1_s{i}", "kind": "staff+tab", "group": f"p1_g{g}", "part": 1, "parts_in_group": 1,
                "tab_lines": 6} for i, g in ((1, 1), (2, 2), (3, 4))]
    gap = {"id": "p1_x1", "page": 1, "staves": ["p1_x1"], "group": "p1_g3", "after_group": "p1_g2",
           "bars": gap_bars, "how": "barlines on the staff-only staff (note stems excluded)",
           "why_unknown": None if gap_bars is not None else "p1_x1: a column at x=900 is neither a barline nor a stem"}
    write(work, "manifest.json", {"systems": systems, "gaps": [gap], "warnings": [],
                                  "dropped": [{"id": "p1_x1", "page": 1, "kind": "staff-only", "role": "skipped",
                                               "group": "p1_g3", "gap": "p1_x1"}]})
    return work


def measures(out):
    song = gp.parse(str(out))
    return song, [["empty" if all(b.status.name == "empty" for v in m.voices for b in v.beats)
                   else ",".join(str(n.value) for b in m.voices[0].beats for n in b.notes) for m in t.measures]
                  for t in song.tracks]


def test_skipped_system_keeps_its_bars_and_a_new_part_label_gets_its_own_track(tmp_path, capsys):
    work = fill_after_a_gap(tmp_path)
    capsys.readouterr()
    code, rep, out = run(work, tmp_path, "--tempo", "80")
    assert code == 0, rep.get("error") or rep.get("disagreements")
    song, bars = measures(out)
    # 1 + 1 transcribed bars, 2 untranscribed bars where the skipped system is printed, then Gtr. 2's bar
    assert rep["bars_written"] == 5 and rep["tracks"] == ["Gtr. 1", "Gtr. 2"]
    assert bars[0] == ["0,1,2,2", "0,1,2,2", "empty", "empty", "empty"]
    assert bars[1] == ["empty", "empty", "empty", "empty", "3,3,3,3"]
    assert rep["track_bars"] == [{"name": "Gtr. 1", "notated_bars": "1-2"}, {"name": "Gtr. 2", "notated_bars": "5"}]
    assert rep["untranscribed_bars"][0]["bars"] == "3-4" and rep["untranscribed_bars"][0]["skipped_system"] == "p1_x1"
    assert song.measureHeaders[2].marker.title == "Bars 3-4 not transcribed (no guitar notation on the page)"
    assert rep["notes_written"] == 12
    text = capsys.readouterr().out
    assert "UNTRANSCRIBED: bar(s) 3-4" in text and "'Gtr. 2' is written as its own track (bars 5)" in text


def test_unknown_skipped_bar_count_is_refused_with_the_flag_that_settles_it(tmp_path):
    work = fill_after_a_gap(tmp_path, gap_bars=None)
    code, rep, out = run(work, tmp_path)
    assert code == 2 and not out.exists()
    assert "--gap-bars p1_x1:<bars>" in rep["error"] and "nothing is guessed" in rep["error"]
    code, rep, out = run(work, tmp_path, "--gap-bars", "p1_x1:3")
    assert code == 0 and rep["bars_written"] == 6
    assert rep["untranscribed_bars"][0]["count_source"] == "--gap-bars p1_x1:3"
    code, rep, _ = run(work, tmp_path, "--gap-bars", "p1_x1:0")  # not a system of its own
    assert code == 0 and rep["bars_written"] == 3 and rep["untranscribed_bars"] == []
    code, rep, _ = run(work, tmp_path, "--gap-bars", "p1_x9:2")
    assert code == 2 and "no skipped system 'p1_x9'" in rep["error"]


def test_flag_overriding_a_counted_gap_is_reported(tmp_path):
    code, rep, _ = run(fill_after_a_gap(tmp_path), tmp_path, "--gap-bars", "p1_x1:1")
    assert code == 0 and rep["bars_written"] == 4
    assert any("overrides the 2 bar(s) prepare counted" in w for w in rep["warnings"])


def test_manifest_from_an_older_prepare_is_refused(tmp_path):
    work = fill_after_a_gap(tmp_path)
    man = json.loads((work / "manifest.json").read_text())
    del man["gaps"]
    write(work, "manifest.json", man)
    code, rep, _ = run(work, tmp_path)
    assert code == 2 and "older prepare.py" in rep["error"]


def test_bare_instrument_label_continues_the_part_with_a_warning(tmp_path):
    work = fill_after_a_gap(tmp_path, labels=("Gtrs. I & II", None, "(Gtr.)"))
    code, rep, out = run(work, tmp_path)
    assert code == 0 and rep["tracks"] == ["Gtrs. I & II"]
    assert measures(out)[1][0] == ["0,1,2,2", "0,1,2,2", "empty", "empty", "3,3,3,3"]
    assert any("names no part number" in w and "continuation" in w for w in rep["warnings"])


@pytest.mark.parametrize("a,b,same", [
    ("Gtr. 1", "*Elec. Gtr. 1 (w/ dist.)", True), ("Gtr. I", "Gtr. 1", True), ("Gtr. 1", "Gtr. 2", False),
    ("Gtrs. 1 & 2", "Gtr. 1", False), ("Gtrs. I & II", "Gtrs. 1 and 2", True),
    ("Acous. Gtr.", "Elec. Gtr.", False), ("Bass", "Gtr.", False),
])
def test_part_label_identity(a, b, same):
    assert (rb.label_identity(a) == rb.label_identity(b)) == same


def test_unlabelled_systems_before_the_first_label_are_their_own_track(tmp_path):
    code, rep, out = run(fill_after_a_gap(tmp_path, labels=(None, None, "Gtr. 2")), tmp_path)
    assert code == 0 and rep["tracks"] == ["Guitar", "Gtr. 2"]
    assert any("first label of this part" in w for w in rep["warnings"])


@needs_alphatab
def test_untranscribed_bars_and_extra_tracks_pass_the_alphatab_plan_check(tmp_path):
    legends = [{"page": 1, "kind": "tempo", "text": "q = 80", "bpm": 80},
               {"page": 1, "kind": "tempo", "text": "q = 120", "bpm": 120, "system": "p1_s3"}]
    work = fill_after_a_gap(tmp_path, legends=legends)
    code, rep, out = run(work, tmp_path)
    assert code == 0, rep.get("error")
    assert rep["tempo_changes"] == {"5": 120}  # on Gtr. 2's bar, where track 1 is empty
    p = run_alphatab(out, "--expect-bars", "5", "--expect-notes", "12", "--plan", str(work / "plan.json"))
    assert p.returncode == 0, p.stdout + p.stderr
    assert "untranscribed track-bar(s) empty as planned" in p.stderr
    plan = json.loads((work / "plan.json").read_text())
    assert plan["bars"][2] == {"system": "p1_x1", "bar": 1, "ts": [4, 4], "beats": [], "untranscribed": True,
                               "why": "skipped system p1_x1: no guitar notation on the page"}
    # the verifier must notice a planned empty bar that is not empty, and a missing marker
    bad = json.loads(json.dumps(plan))
    bad["more_tracks"][0]["bars"][4] = {**bad["more_tracks"][0]["bars"][4], "untranscribed": True, "beats": []}
    (tmp_path / "bad1.json").write_text(json.dumps(bad))
    p = run_alphatab(out, "--plan", str(tmp_path / "bad1.json"))
    assert p.returncode == 1 and "untranscribed bar holds" in p.stderr
    bad = json.loads(json.dumps(plan))
    bad["markers"] = {}
    (tmp_path / "bad2.json").write_text(json.dumps(bad))
    p = run_alphatab(out, "--plan", str(tmp_path / "bad2.json"))
    assert p.returncode == 1 and "markers" in p.stderr


# ---- prepare's classifier for barlines on a staff without TAB ------------------------------------------

def _staff_image(w=900, d=20, t=2):
    img = np.zeros((300, w), bool)
    top = 100
    for k in range(5):
        img[top + k * d - t // 2: top + k * d + t // 2 + 1, 50:w - 50] = True
    return img, top


def _lone(img):
    d, t = si.estimate_spacing(img)
    st = si.group_staves(si.detect_lines(img, d, t), d)[0]
    csum = np.zeros((img.shape[0] + 1, img.shape[1]), np.int32)
    np.cumsum(img, axis=0, out=csum[1:])
    return si.lone_staff_barlines(img, csum, st, t)


def test_lone_staff_barlines_ignore_stems_and_doubt_what_is_unclear():
    img, top = _staff_image()
    for x in (300, 848):  # two barlines: the bar line and the closing line
        img[top:top + 81, x:x + 3] = True
    # a stem from a filled notehead on the bottom line up past the top line to a beam: not a barline
    img[top:top + 81, 500:502] = True
    yy, xx = np.mgrid[0:300, 0:900]
    img |= ((xx - 489) / 13.0) ** 2 + ((yy - (top + 80)) / 10.0) ** 2 <= 1.0
    img[top - 4:top + 4, 500:560] = True
    bls, doubt = _lone(img)
    assert [round(b.xa) for b in bls] == [300, 848] and doubt == []
    # a time-signature-like glyph pressed against the barline: no longer clear -> doubtful, never dropped
    img2 = img.copy()
    img2[top + 10:top + 70, 304:316] = True
    bls, doubt = _lone(img2)
    assert [round(b.xa) for b in bls] == [848] and len(doubt) == 1 and "x=301" in doubt[0][1]


def test_a_multi_bar_rest_makes_the_count_doubtful():
    img, top = _staff_image()
    for x in (300, 848):
        img[top:top + 81, x:x + 3] = True
    img[top + 35:top + 46, 450:560] = True  # thick level bar on the middle line: a multi-bar rest
    img[top + 20:top + 61, 450:453] = True
    img[top + 20:top + 61, 557:560] = True
    bls, doubt = _lone(img)
    assert [round(b.xa) for b in bls] == [300, 848] and any("multi-bar rest" in w for _, w in doubt)


# ---- prepare on the CC0 fixture: the vocal-only system's bars are counted, or left unknown ----------------

def test_prepare_counts_the_vocal_only_systems_bars(tmp_path):
    import prepare
    from conftest import FIXTURES
    for name in ("exercise3_scan.jpg", "exercise3_clean.png", "exercise3_1bit_inverted.png"):
        work = tmp_path / name
        assert prepare.main([str(FIXTURES / "exercise3" / name), str(work)]) == 0
        man = json.loads((work / "manifest.json").read_text())
        assert [(g["id"], g["after_group"], g["bars"]) for g in man["gaps"]] == [("p1_x2", "p1_g1", 4)], name
        assert [x.get("gap") for x in man["dropped"]] == [None, "p1_x2", None]
        assert len(man["dropped"][1]["gap_barlines_x"]) == 4


def test_prepare_leaves_an_unclear_count_unknown_and_names_the_flag(tmp_path):
    import prepare
    from PIL import Image
    from conftest import FIXTURES
    src = FIXTURES / "exercise3" / "exercise3_clean.png"
    probe = tmp_path / "probe"
    assert prepare.main([str(src), str(probe)]) == 0
    man = json.loads((probe / "manifest.json").read_text())
    xa, xb = man["dropped"][1]["gap_barlines_x"][0]
    _, y0, _, y1 = man["dropped"][1]["bbox"]
    img = np.array(Image.open(src).convert("L"))
    # a glyph pressed against the vocal-only system's first barline (like a time signature) over its staff
    rows = np.flatnonzero((img[y0:y1, xa - 40:xa - 10] < 128).sum(axis=1) > 25) + y0  # its staff lines
    img[rows.min() + 6:rows.max() - 6, xb + 1:xb + 14] = 0
    bad = tmp_path / "pressed.png"
    Image.fromarray(img).save(bad)
    work = tmp_path / "w"
    assert prepare.main([str(bad), str(work)]) == 0
    man = json.loads((work / "manifest.json").read_text())
    assert man["gaps"][0]["bars"] is None and "neither clearly a barline nor a stem" in man["gaps"][0]["why_unknown"]
    assert any("--gap-bars p1_x2:<bars>" in w for w in man["warnings"])
