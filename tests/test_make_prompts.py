"""make_prompts.py fills the templates; re-reads get unique names; unknown ids are refused."""

import make_prompts
import prepare
from conftest import FIXTURES

CLEAN = FIXTURES / "exercise1" / "exercise1_clean.png"


def test_prompts_rereads_and_unverified(tmp_path, capsys):
    work = tmp_path / "work"
    assert prepare.main([str(CLEAN), str(work)]) == 0
    assert make_prompts.main([str(work)]) == 0
    tab = (work / "prompts" / "tab_reader_1.md").read_text()
    staff = (work / "prompts" / "staff_reader_1.md").read_text()
    for text in (tab, staff):
        assert "{{" not in text and str(work) in text and "p1_s1 (4 bars detected)" in text
    assert str(work / "reads" / "tab_1.json") in tab and "UNVERIFIED" not in tab
    assert make_prompts.main([str(work), "--systems", "p1_s2"]) == 0
    assert make_prompts.main([str(work), "--systems", "p1_s2"]) == 0
    names = sorted(p.name for p in (work / "prompts").glob("*reread*"))
    assert names == ["staff_reader_1_reread1.md", "staff_reader_1_reread2.md",
                     "tab_reader_1_reread1.md", "tab_reader_1_reread2.md"]
    assert "p1_s1" not in (work / "prompts" / "tab_reader_1_reread1.md").read_text().split("Systems to read")[1][:80]
    assert make_prompts.main([str(work), "--systems", "p9_s9"]) == 2
    assert make_prompts.main([str(work), "--unverified"]) == 0
    assert "UNVERIFIED mode" in (work / "prompts" / "tab_reader_1.md").read_text()
    assert "UNVERIFIED mode" not in (work / "prompts" / "staff_reader_1.md").read_text()
