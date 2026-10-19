"""prepare.py: page extraction, deskew, staff/tab system detection, crops, refusal rules."""

import json
from pathlib import Path

import numpy as np
import pymupdf
import pytest
from PIL import Image

import prepare
from conftest import FIXTURES

EX1 = FIXTURES / "exercise1"
SCAN = EX1 / "exercise1_scan.jpg"
CLEAN = EX1 / "exercise1_clean.png"


def prep(tmp_path: Path, *inputs, flags=()) -> tuple[int, dict]:
    work = tmp_path / "work"
    code = prepare.main([*map(str, inputs), str(work), *flags])
    man = json.loads((work / "manifest.json").read_text()) if (work / "manifest.json").exists() else {}
    return code, man


def assert_exercise1_layout(man: dict) -> None:
    assert len(man["pages"]) == 1
    systems = man["systems"]
    assert [s["kind"] for s in systems] == ["staff+tab", "staff+tab"]
    assert [s["n_bars_detected"] for s in systems] == [4, 3]
    assert all(s["tab_lines"] == 6 for s in systems)
    for s in systems:
        assert s["staff_lines_y"][-1] < s["split_y"] < s["tab_lines_y"][0]


def test_degraded_scan(tmp_path):
    code, man = prep(tmp_path, SCAN)
    assert code == 0 and man["verifiable"]
    assert_exercise1_layout(man)
    assert abs(man["pages"][0]["deskew_deg"] - (-1.15)) < 0.1  # the fixture was rotated by +1.15 deg
    work = tmp_path / "work"
    s = man["systems"][0]
    for rel in [s["crops"]["staff"], s["crops"]["tab"], *s["segments"]["staff"], *s["segments"]["tab"],
                *(b["staff"] for b in s["bars"]), *(b["tab"] for b in s["bars"])]:
        assert (work / rel).exists(), rel
    assert len(s["segments"]["tab"]) >= 2
    assert (work / "contact" / "p1.png").exists() and (work / "contact" / "systems.png").exists()


def test_clean_render(tmp_path):
    code, man = prep(tmp_path, CLEAN)
    assert code == 0
    assert_exercise1_layout(man)
    assert abs(man["pages"][0]["deskew_deg"]) < 0.1


@pytest.mark.parametrize("angle", [-2.5, 0.6, 3.0])
def test_rotated_input(tmp_path, angle):
    img = Image.open(CLEAN).convert("L").rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=255)
    p = tmp_path / "rot.png"
    img.save(p)
    code, man = prep(tmp_path, p)
    assert code == 0
    assert_exercise1_layout(man)
    assert abs(man["pages"][0]["deskew_deg"] + angle) < 0.15


def test_image_only_pdf_uses_embedded_image(tmp_path):
    pdf = tmp_path / "scan.pdf"
    doc = pymupdf.open()
    w, h = Image.open(SCAN).size
    page = doc.new_page(width=w * 72 / 300, height=h * 72 / 300)
    page.insert_image(page.rect, filename=str(SCAN))
    doc.save(pdf)
    code, man = prep(tmp_path, pdf)
    assert code == 0
    assert man["pages"][0]["extraction"].startswith("embedded-image")
    assert man["pages"][0]["size"][0] >= w  # native resolution (deskew may expand the canvas)
    assert_exercise1_layout(man)


def test_multiple_inputs_are_pages_in_order(tmp_path):
    code, man = prep(tmp_path, CLEAN, SCAN)
    assert code == 0
    assert [p["source"] for p in man["pages"]] == [CLEAN.name, SCAN.name]
    assert [s["id"] for s in man["systems"]] == ["p1_s1", "p1_s2", "p2_s1", "p2_s2"]


def _blank_staves(tmp_path: Path) -> Path:
    """The clean render with the notation staves painted white -> a tab-only page."""
    _, man = prep(tmp_path / "probe", CLEAN)
    img = np.array(Image.open(CLEAN).convert("L"))
    for s in man["systems"]:
        top = int(s["staff_lines_y"][0] - 6 * s["staff_space_px"])
        img[max(0, top): s["split_y"], :] = 255
    p = tmp_path / "tab_only.png"
    Image.fromarray(img).save(p)
    return p


def test_tab_only_page_is_refused_unless_unverified(tmp_path):
    p = _blank_staves(tmp_path)
    code, man = prep(tmp_path / "a", p)
    assert code == 3 and not man["verifiable"]
    assert [s["kind"] for s in man["systems"]] == ["tab-only", "tab-only"]
    code, man = prep(tmp_path / "b", p, flags=["--unverified"])
    assert code == 0 and not man["verifiable"]


def test_blank_page_finds_nothing(tmp_path):
    p = tmp_path / "blank.png"
    Image.new("L", (1700, 2200), 250).save(p)
    code, man = prep(tmp_path, p)
    assert code == 2 and man["systems"] == []


def _keystone(img: Image.Image, frac: float = 0.03) -> Image.Image:
    w, h = img.size
    dx = int(frac * w)
    src = [(0, 0), (w, 0), (w, h), (0, h)]
    dst = [(dx, 0), (w - dx, int(0.01 * h)), (w, h), (0, h)]
    a = []
    for (x, y), (u, v) in zip(src, dst):
        a.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        a.append([0, 0, 0, x, y, 1, -v * x, -v * y])
    coeffs = np.linalg.solve(np.array(a, float), np.array(dst, float).reshape(8)).tolist()
    return img.transform((w, h), Image.PERSPECTIVE, coeffs, Image.BICUBIC, fillcolor=235)


@pytest.mark.parametrize("variant", ["keystone", "200dpi"])
def test_phone_like_distortions(tmp_path, variant):
    img = Image.open(SCAN).convert("L")
    if variant == "keystone":
        img = _keystone(img)
    else:
        img = img.resize((img.width * 2 // 3, img.height * 2 // 3), Image.LANCZOS)
    p = tmp_path / f"{variant}.jpg"
    img.save(p, quality=60)
    code, man = prep(tmp_path, p)
    assert code == 0
    assert_exercise1_layout(man)


def test_ragged_short_last_system_is_kept(tmp_path):
    """A last system only ~1.5 bars wide must still be found (not silently dropped)."""
    _, man = prep(tmp_path / "probe", CLEAN)
    s2 = man["systems"][1]
    img = np.array(Image.open(CLEAN).convert("L"))
    x0 = s2["bbox"][0]
    cut = int(s2["barlines_x"][0][1] + 4)
    img[s2["bbox"][1]:s2["bbox"][3], cut:] = 255  # keep only the first bar of system 2
    assert cut - x0 < 0.45 * (s2["bbox"][2] - x0)
    p = tmp_path / "ragged.png"
    Image.fromarray(img).save(p)
    code, man = prep(tmp_path / "r", p)
    assert len(man["systems"]) == 2, man["warnings"]
    assert [s["kind"] for s in man["systems"]] == ["staff+tab", "staff+tab"]


def test_16bit_grayscale_input(tmp_path):
    a = np.asarray(Image.open(SCAN).convert("L")).astype(np.uint16) * 257
    p = tmp_path / "scan16.png"
    Image.fromarray(a).save(p)
    assert Image.open(p).mode.startswith("I")
    code, man = prep(tmp_path, p)
    assert code == 0
    assert_exercise1_layout(man)


def test_pdf_with_vertically_flipped_image(tmp_path):
    pdf = tmp_path / "flipped.pdf"
    doc = pymupdf.open()
    w, h = Image.open(SCAN).size
    page = doc.new_page(width=w * 72 / 300, height=h * 72 / 300)
    # store the image upside down and place it flipped, so the page itself looks upright
    flipped = tmp_path / "flipped.png"
    Image.open(SCAN).transpose(Image.FLIP_TOP_BOTTOM).save(flipped)
    xref = page.insert_image(page.rect, filename=str(flipped))
    page.clean_contents()
    cont = page.read_contents().decode()
    r = page.rect
    # replace the placement matrix with one that mirrors vertically (a d -> -d, f -> +height)
    import re as _re
    m = _re.search(r"([\d.]+) 0 0 ([\d.]+) ([\d.]+) ([\d.]+) cm", cont)
    a, d = float(m.group(1)), float(m.group(2))
    cont = cont.replace(m.group(0), f"{a} 0 0 {-d} {m.group(3)} {float(m.group(4)) + d} cm")
    doc.update_stream(page.get_contents()[0], cont.encode())
    doc.save(pdf)
    doc = pymupdf.open(pdf)
    info = doc[0].get_image_info(xrefs=True)[0]
    assert info["transform"][3] < 0 and xref
    code, man = prep(tmp_path, pdf)
    assert man["pages"][0]["extraction"].startswith("embedded-image") and r.width > 0
    assert code == 0
    assert_exercise1_layout(man)
