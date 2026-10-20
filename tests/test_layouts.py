"""prepare.py on the v0.2 layout fixtures: stacked parts, vocal staves, two-voice guitar staff,
chord-diagram grids, dashed barlines, 1-bit inverted scans, crop geometry."""

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import prepare
from conftest import FIXTURES

EX1 = FIXTURES / "exercise1"
EX2 = FIXTURES / "exercise2"
EX3 = FIXTURES / "exercise3"


def prep(work: Path, *inputs, flags=()) -> tuple[int, dict]:
    code = prepare.main([*map(str, inputs), str(work), *flags])
    man = json.loads((work / "manifest.json").read_text()) if (work / "manifest.json").exists() else {}
    return code, man


@pytest.fixture(scope="module")
def ex2_scan(tmp_path_factory):
    work = tmp_path_factory.mktemp("ex2") / "work"
    return work, *prep(work, EX2 / "exercise2_scan.jpg")


@pytest.fixture(scope="module")
def ex3_scan(tmp_path_factory):
    work = tmp_path_factory.mktemp("ex3") / "work"
    return work, *prep(work, EX3 / "exercise3_scan.jpg")


# ---- P0-1: stacked parts --------------------------------------------------------------------------


def assert_boxes_hold_every_line(man: dict) -> None:
    for s in man["systems"]:
        x0, y0, x1, y1 = s["bbox"]
        assert y0 <= s["staff_lines_y"][0] - 0.8 * s["staff_space_px"], s["id"]
        assert y1 >= s["tab_lines_y"][-1] + 0.8 * s["tab_space_px"], s["id"]
        assert s["staff_lines_y"][-1] < s["split_y"] < s["tab_lines_y"][0], s["id"]


def assert_two_stacked_parts(man: dict) -> None:
    assert_boxes_hold_every_line(man)
    systems = man["systems"]
    assert [s["kind"] for s in systems] == ["staff+tab"] * 4
    assert [(s["group"], s["part"], s["parts_in_group"]) for s in systems] == [
        ("p1_g1", 1, 2), ("p1_g1", 2, 2), ("p1_g2", 1, 2), ("p1_g2", 2, 2)]
    assert [s["n_bars_detected"] for s in systems] == [4, 4, 4, 4]
    assert man["parts"] == 2 and not man["layout_errors"] and man["verifiable"]
    assert [g["systems"] for g in man["groups"]] == [["p1_s1", "p1_s2"], ["p1_s3", "p1_s4"]]
    assert all(g["barlines_aligned"] for g in man["groups"])


def test_stacked_parts_stay_separate_scan(ex2_scan):
    work, code, man = ex2_scan
    assert code == 0, man["warnings"]
    assert_two_stacked_parts(man)
    assert any("stacked parts played together" in w for w in man["warnings"])


def test_stacked_parts_stay_separate_clean(tmp_path):
    code, man = prep(tmp_path / "w", EX2 / "exercise2_clean.png")
    assert code == 0, man["warnings"]
    assert_two_stacked_parts(man)


def test_header_crop_holds_the_legend(ex2_scan):
    work, code, man = ex2_scan
    hdr = man["pages"][0]["header"]
    assert hdr and (work / hdr).exists()
    first = man["systems"][0]
    assert Image.open(work / hdr).height < first["staff_lines_y"][0]
    # the legend is two text lines of real ink, so the header is not just white margin
    a = np.asarray(Image.open(work / hdr).convert("L")) < 128
    assert a.sum() > 2000


def test_text_between_systems_goes_to_the_system_below(ex2_scan):
    """'Chorus' + the tempo change sit between system 1 (part 2 TAB) and system 2 (part 1 staff)."""
    work, code, man = ex2_scan
    s2, s3 = man["systems"][1], man["systems"][2]
    page = np.asarray(Image.open(work / man["pages"][0]["contact"].replace("contact", "pages")).convert("L")) < 110
    lo = int(s2["tab_lines_y"][-1] + 1.0 * s2["tab_space_px"])
    hi = int(s3["staff_lines_y"][0] - 1.2 * s3["staff_space_px"])
    rows = np.flatnonzero(page[lo:hi, : page.shape[1] // 2].sum(axis=1) > 5) + lo
    assert len(rows) > 20  # the heading is there
    assert s3["bbox"][1] <= rows.min(), (s3["bbox"], rows.min())
    assert s3["split_y"] > rows.max()


def _blank(img: np.ndarray, y0: float, y1: float, x0: float = 0, x1: float | None = None) -> None:
    img[int(y0):int(y1), int(x0):(int(x1) if x1 is not None else img.shape[1])] = 255


def test_stacked_parts_with_misaligned_barlines_are_refused(tmp_path):
    _, man = prep(tmp_path / "probe", EX2 / "exercise2_clean.png")
    s2 = man["systems"][1]  # part 2 of the first system: erase its second barline in staff + TAB
    img = np.array(Image.open(EX2 / "exercise2_clean.png").convert("L"))
    xa, xb = s2["barlines_x"][1]
    _blank(img, s2["staff_lines_y"][0] - 5, s2["tab_lines_y"][-1] + 5, xa - 3, xb + 4)
    p = tmp_path / "misaligned.png"
    Image.fromarray(img).save(p)
    code, man = prep(tmp_path / "w", p)
    assert code == 3 and not man["verifiable"]
    assert any("do not line up" in e for e in man["layout_errors"]), man["layout_errors"]


def test_part_count_change_is_refused(tmp_path):
    _, man = prep(tmp_path / "probe", EX2 / "exercise2_clean.png")
    s4 = man["systems"][3]  # drop Gtr. 2 from the second system
    img = np.array(Image.open(EX2 / "exercise2_clean.png").convert("L"))
    _blank(img, s4["staff_lines_y"][0] - 3 * s4["staff_space_px"], s4["tab_lines_y"][-1] + 2 * s4["tab_space_px"])
    p = tmp_path / "one_part_later.png"
    Image.fromarray(img).save(p)
    code, man = prep(tmp_path / "w", p)
    assert code == 3
    assert any("changes between systems" in e for e in man["layout_errors"]), man["layout_errors"]


# ---- P1-6..10: vocal staves, vocal-only system, grids, dashed barlines, crop geometry ------------


def assert_vocal_guitar_layout(man: dict, grid_seen: bool = True) -> None:
    assert_boxes_hold_every_line(man)
    systems = man["systems"]
    assert [s["kind"] for s in systems] == ["staff+tab", "staff+tab"]
    assert [s["n_bars_detected"] for s in systems] == [4, 4]
    assert man["parts"] == 1 and man["verifiable"]
    assert [(x["role"], x["kind"]) for x in man["dropped"]] == [
        ("dropped", "staff-only"), ("skipped", "staff-only"), ("dropped", "staff-only")]
    skipped = [w for w in man["warnings"] if "p1_x2: skipped" in w]
    assert skipped and "earlier figure" in skipped[0] and "NOT in the file" in skipped[0]
    if grid_seen:  # a row of chord diagrams linked into 5 "lines" is recognised and ignored
        assert any("chord diagrams, not a staff" in w for w in man["warnings"])
    assert systems[0].get("barlines_dashed_x"), "the dashed barline in system 1 was missed"


@pytest.mark.parametrize("name", ["exercise3_scan.jpg", "exercise3_clean.png", "exercise3_1bit_inverted.png"])
def test_vocal_staves_dropped_vocal_only_system_skipped(tmp_path, name):
    code, man = prep(tmp_path / "w", EX3 / name)
    assert code == 0, man.get("refused")
    assert_vocal_guitar_layout(man, grid_seen="inverted" not in name)  # 1-bit loses the thin grid lines
    if "inverted" in name:
        assert "inverted" in man["pages"][0]["extraction"]


def test_split_keeps_ledger_notes_and_stems_and_tab_labels_whole(ex3_scan):
    work, code, man = ex3_scan
    page = np.asarray(Image.open(work / "pages" / "p1.png").convert("L")) < 110
    for s in man["systems"]:
        sp = s["staff_space_px"]
        # the bass notes sit three ledger lines under the staff and their stems hang lower still
        assert s["split_y"] >= s["staff_lines_y"][-1] + 4 * sp, s
        x0, _, x1, _ = s["bbox"]
        # nothing big is cut by the bottom edge of the staff crop ...
        assert page[s["split_y"] - 1, x0:x1].sum() <= 3 * sp
        # ... nor by the top edge of the TAB crop (the '1/2' bend label goes whole into the TAB crop)
        y = s.get("tab_crop_y0", s["split_y"])
        assert page[y, x0:x1].sum() <= 2 * sp
        assert y < s["tab_lines_y"][0] - 0.6 * s["tab_space_px"]
    assert man["systems"][0].get("tab_crop_y0", 10 ** 6) < man["systems"][0]["split_y"]


def test_staff_only_page_is_still_refused(tmp_path):
    _, man = prep(tmp_path / "probe", EX3 / "exercise3_clean.png")
    img = np.array(Image.open(EX3 / "exercise3_clean.png").convert("L"))
    for s in man["systems"]:  # erase every TAB staff
        _blank(img, s["tab_lines_y"][0] - 0.7 * s["tab_space_px"], s["tab_lines_y"][-1] + 0.7 * s["tab_space_px"])
    p = tmp_path / "no_tab.png"
    Image.fromarray(img).save(p)
    code, man = prep(tmp_path / "w", p)
    assert code == 3 and not man["verifiable"]
    assert all(s["kind"] == "staff-only" for s in man["systems"])
    assert all("no TAB anywhere" in r for r in man["refused"])
    assert not any("--unverified" in r for r in man["refused"])


def test_lines_drawn_past_the_opening_line_make_no_sliver_bar(tmp_path):
    """Staff/TAB lines that start a little left of the system's opening line must not turn the
    opening line into a barline that closes an empty first 'bar'."""
    _, man = prep(tmp_path / "probe", EX1 / "exercise1_clean.png")
    img = np.array(Image.open(EX1 / "exercise1_clean.png").convert("L"))
    for s in man["systems"]:
        x0 = s["bars"][0]["x"][0]
        for key, sp in (("staff_lines_y", s["staff_space_px"]), ("tab_lines_y", s["tab_space_px"])):
            for y in s[key]:
                img[int(round(y)) - 1:int(round(y)) + 2, int(x0 - 2.5 * sp):int(x0) + 2] = 0
    p = tmp_path / "overhang.png"
    Image.fromarray(img).save(p)
    code, man = prep(tmp_path / "w", p)
    assert code == 0
    assert [s["n_bars_detected"] for s in man["systems"]] == [4, 3]
