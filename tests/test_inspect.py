"""inspect_crop.py measuring aids, on the clean fixture render (evidence tools, so only basic guarantees)."""

import re

import inspect_crop
import prepare
from conftest import FIXTURES

CLEAN = FIXTURES / "exercise1" / "exercise1_clean.png"


def _prep(tmp_path):
    work = tmp_path / "work"
    assert prepare.main([str(CLEAN), str(work)]) == 0
    return work


def test_tab_digits_land_on_the_right_strings(tmp_path, capsys):
    work = _prep(tmp_path)
    capsys.readouterr()
    assert inspect_crop.main(["tab", str(work / "bars" / "p1_s1_b02_tab.png")]) == 0
    out = capsys.readouterr().out
    rows = re.findall(r"^\s+(\d+)\s+(\d+)\s+(\d)\s", out, re.M)
    strings = [int(s) for _, _, s in sorted(rows, key=lambda r: int(r[0]))]
    assert strings == [5, 5, 5, 6, 5, 5, 4, 5, 5, 6]  # 7-9-7-10-7-9 | 7 | 9-7-10


def test_short_bar_crop_still_finds_lines(tmp_path, capsys):
    work = _prep(tmp_path)
    capsys.readouterr()
    assert inspect_crop.main(["tab", str(work / "bars" / "p1_s1_b01_tab.png")]) == 0
    assert "6 tab lines" in capsys.readouterr().out


def test_staff_steps_for_the_blues_bar(tmp_path, capsys):
    work = _prep(tmp_path)
    capsys.readouterr()
    assert inspect_crop.main(["staff", str(work / "bars" / "p1_s1_b04_staff.png")]) == 0
    out = capsys.readouterr().out
    names = re.findall(r"^\s+\d+\s+\d+\s+-?\d+\s+([A-G]\d)", out, re.M)
    # E4 F4 F4 F4 A4 B4 A4 F4 must all be among the candidates, in order
    it = iter(names)
    assert all(any(n == want for n in it) for want in ["E4", "F4", "F4", "F4", "A4", "B4", "A4", "F4"]), names


def test_zoom_writes_an_upscaled_crop(tmp_path):
    out = tmp_path / "z.png"
    assert inspect_crop.main(["zoom", str(CLEAN), "100", "100", "200", "150", "--scale", "3", "--out", str(out)]) == 0
    from PIL import Image
    assert Image.open(out).size == (300, 150)
    assert inspect_crop.main(["zoom", str(CLEAN), "100", "100", "50", "50", "--out", str(out)]) == 1
