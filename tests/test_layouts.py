"""prepare.py on the v0.2 layout fixtures: stacked parts, vocal staves, two-voice guitar staff,
chord-diagram grids, dashed barlines, 1-bit inverted scans, crop geometry."""

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from scipy import ndimage as ndi

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


# ---- part labels printed in the margin ("Gtr. 1" left of an indented system) ----------------------


def margin_label_boxes(page_ink: np.ndarray, s: dict, min_x: int = 3) -> list[tuple[int, int, int, int]]:
    """(x0, y0, x1, y1) of every ink component left of the system's opening line whose middle lies beside
    its notation staff -- the part label printed in the margin. Deliberately simpler than prepare's own
    search (no chaining, no gap rule): anything there counts. Dust (< 15 px) and components starting left
    of min_x (the page edge) are ignored."""
    sp = s["staff_space_px"]
    left = s["bars"][0]["x"][0]  # the system's opening line
    ya, yb = s["staff_lines_y"][0] - 0.5 * sp, s["staff_lines_y"][-1] + 0.5 * sp
    lo, hi = int(ya - 3 * sp), int(yb + 3 * sp)
    lab, _ = ndi.label(page_ink[lo:hi, : int(left - 0.5 * sp)])
    out = []
    for k, sl in enumerate(ndi.find_objects(lab), start=1):
        y0, y1, x0, x1 = sl[0].start + lo, sl[0].stop + lo, sl[1].start, sl[1].stop
        if (lab[sl] == k).sum() >= 15 and ya <= (y0 + y1) / 2 <= yb and x0 >= min_x:
            out.append((x0, y0, x1, y1))
    return out


def assert_margin_labels_in_the_crops(work: Path, man: dict, labelled=("p1_s1", "p1_s2"), min_x: int = 3) -> None:
    page = np.asarray(Image.open(work / "pages" / "p1.png").convert("L"))
    for s in man["systems"]:
        boxes = margin_label_boxes(page < 128, s, min_x)
        if s["id"] not in labelled:
            assert boxes == [], (s["id"], boxes)
            continue
        assert len(boxes) >= 4, (s["id"], boxes)  # "Gtr. 1": G, t, r, 1 (+ the full stop)
        x0, y0, _, y1 = s["bbox"]
        sp = s["staff_space_px"]
        for part, bottom in (("full", y1), ("staff", s["split_y"])):
            crop = np.asarray(Image.open(work / s["crops"][part]).convert("L"))
            for bx0, by0, bx1, by1 in boxes:
                assert x0 <= bx0 and y0 <= by0 and by1 <= bottom, (s["id"], part, s["bbox"], (bx0, by0, bx1, by1))
                # whole and untouched: the crop holds the page's own pixels there
                assert np.array_equal(crop[by0 - y0:by1 - y0, bx0 - x0:bx1 - x0], page[by0:by1, bx0:bx1])
        assert x0 >= min(b[0] for b in boxes) - 1.5 * sp, s["bbox"]  # label-deep, not page-deep
        # the crop was widened because its old left edge cut the label; outside the label's rows the
        # widened strip shows nothing: no heading, page legend or other system's text comes in with it
        old_x0 = s["margin_label"]["widened_from_x"]
        assert old_x0 > min(b[0] for b in boxes), (s["id"], old_x0)
        crop = np.asarray(Image.open(work / s["crops"]["full"]).convert("L"))
        rows = np.ones(crop.shape[0], bool)
        rows[max(0, int(min(b[1] for b in boxes) - y0 - 0.5 * sp)):int(max(b[3] for b in boxes) - y0 + 0.5 * sp)] = False
        assert not (crop[rows, :old_x0 - x0] < 128).any(), s["id"]


@pytest.mark.parametrize("name", ["exercise2_scan.jpg", "exercise2_clean.png"])
def test_part_labels_are_inside_the_system_crops(tmp_path, name):
    """'Gtr. 1' / 'Gtr. 2' printed left of the indented first system were cut by the crop ('tr. 1')."""
    work = tmp_path / "w"
    code, man = prep(work, EX2 / name)
    assert code == 0, man["warnings"]
    assert_margin_labels_in_the_crops(work, man)
    assert [s.get("margin_label") is not None for s in man["systems"]] == [True, True, False, False]


def test_margin_label_search_stops_at_page_edge_artefacts(tmp_path):
    """The label close to the page edge, glyph-sized shadow fragments along the edge in its rows (a
    book's gutter shadow, a few pixels in from the border): the label goes into the crop, the shadow
    does not."""
    img = np.array(Image.open(EX2 / "exercise2_clean.png").convert("L"))[:, 150:].copy()
    for y in range(480, 1240, 40):  # dark fragments near the left edge beside both labelled staves
        img[y:y + 22, 4:18] = 40
    p = tmp_path / "edge.png"
    Image.fromarray(img).save(p)
    work = tmp_path / "w"
    code, man = prep(work, p)
    assert code == 0 and [s["n_bars_detected"] for s in man["systems"]] == [4, 4, 4, 4]
    assert_margin_labels_in_the_crops(work, man, min_x=20)
    for s in man["systems"][:2]:
        assert s["bbox"][0] >= 18, s["bbox"]


@pytest.fixture(scope="module")
def ex2_clean(tmp_path_factory):
    work = tmp_path_factory.mktemp("ex2c") / "w"
    return work, *prep(work, EX2 / "exercise2_clean.png")


def crop_pixels(work: Path, s: dict) -> dict[str, np.ndarray]:
    return {part: np.asarray(Image.open(work / path).convert("L")) for part, path in s["crops"].items()}


@pytest.mark.parametrize("junk", ["disc", "speckle", "both"])
def test_margin_junk_is_not_a_label(tmp_path, ex2_clean, junk):
    """Beside the non-indented systems p1_s3 / p1_s4, 2-5% of the page width in from the edge (outside
    the page-edge zone): a filled disc 2.5 spaces across beside p1_s3 (a punch hole) and 60 small dark
    fragments beside p1_s4's staff and TAB (scanner speckle). Neither is text: no margin_label, and those
    systems' bboxes and crops are exactly the clean page's."""
    work0, code0, man0 = ex2_clean
    assert code0 == 0
    img = np.array(Image.open(EX2 / "exercise2_clean.png").convert("L"))
    H, W = img.shape
    s3, s4 = man0["systems"][2], man0["systems"][3]
    sp = s3["staff_space_px"]
    if junk in ("disc", "both"):
        cx, cy, r = 0.035 * W, (s3["staff_lines_y"][0] + s3["staff_lines_y"][-1]) / 2, 1.25 * sp
        yy, xx = np.ogrid[:H, :W]
        img[(xx - cx) ** 2 + (yy - cy) ** 2 <= r * r] = 30
        assert cx + r < s3["bbox"][0]  # outside the crop
    if junk in ("speckle", "both"):
        rng = np.random.default_rng(7)
        lo, hi = s4["staff_lines_y"][0] - sp, s4["tab_lines_y"][-1] + sp
        right = min(int(0.05 * W), s4["bbox"][0] - 2)
        for _ in range(60):
            w, h = (int(v) for v in rng.integers(4, 10, size=2))
            x, y = int(rng.integers(int(0.02 * W), right - w)), int(rng.integers(int(lo), int(hi) - h))
            img[y:y + h, x:x + w] = 35
    p = tmp_path / "junk.png"
    Image.fromarray(img).save(p)
    work = tmp_path / "w"
    code, man = prep(work, p)
    assert code == 0, man["warnings"]
    assert [s.get("margin_label") for s in man["systems"][2:]] == [None, None]
    assert [s.get("margin_label") is not None for s in man["systems"][:2]] == [True, True]  # the real labels
    for a, b in zip(man0["systems"], man["systems"]):
        assert a["bbox"] == b["bbox"], (a["id"], a["bbox"], b["bbox"])
        ca, cb = crop_pixels(work0, a), crop_pixels(work, b)
        assert ca.keys() == cb.keys() and all(np.array_equal(ca[k], cb[k]) for k in ca), a["id"]


def test_a_two_line_label_is_taken_whole(tmp_path):
    """A label printed on two lines ('Gtr. 1' over 'Gtr. 1' here, half a space apart): both lines go into
    the crop, each in its own rows."""
    img = np.array(Image.open(EX2 / "exercise2_clean.png").convert("L"))
    img[610:645, 198:315] = img[565:600, 198:315]  # p1_s1's label, again under itself
    p = tmp_path / "two_lines.png"
    Image.fromarray(img).save(p)
    work = tmp_path / "w"
    code, man = prep(work, p)
    assert code == 0, man["warnings"]
    assert_margin_labels_in_the_crops(work, man)
    ml = man["systems"][0]["margin_label"]
    assert ml["box"][1] <= 566 and ml["box"][3] >= 644 and len(ml["lines"]) == 2, ml


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
