"""v0.2.1 prompt contamination: the reader prompts ship placeholders only, and reconcile refuses a
legend / part label that was copied from the prompt instead of read from the page (a copied tuning
legend would make the legend check ENFORCE a wrong tuning -- invisible to the note cross-check)."""

import json
import re

import pytest

import reconcile_build as rb
from conftest import SKILL
from test_reconcile import GOOD_STAFF, GOOD_TAB, make_work, run
from test_reconcile_v2 import legend_work

STAFF_PROMPT = SKILL / "prompts" / "staff_reader.md"
TAB_PROMPT = SKILL / "prompts" / "tab_reader.md"
# format words the prompts may quote (they are not legends / labels anyone could copy as one)
FORMAT_WORDS = {"dotted quarter", "x on the 3rd space eighth"}


def _legends_section(text: str) -> str:
    return re.search(r"^## Legends.*?(?=^## )", text, re.M | re.S).group(0)


def test_prompts_ship_placeholders_not_real_legends_or_labels():
    staff, tab = STAFF_PROMPT.read_text(), TAB_PROMPT.read_text()
    section = _legends_section(staff)
    quoted = [q for q in re.findall(r'"([^"\n]+)"', section) if len(rb._canary_norm(q).split()) >= 2]
    texts = re.findall(r'"text":\s*"([^"\n]+)"', staff)
    for q in quoted + texts:
        assert q.startswith("<") or rb._canary_norm(q) in FORMAT_WORDS, f"realistic example in the staff prompt: {q!r}"
    for key in ("part_label", "section"):
        for v in re.findall(rf'"{key}":\s*"([^"\n]+)"', staff + tab):
            assert v.startswith("<") and v.endswith(">"), f"{key} example {v!r} is not a placeholder"
    assert '"legends": []' in staff and "only text you can see" in staff.replace("\n", " ")
    assert "never carry" in staff and "never carry" in tab


@pytest.mark.parametrize("text", ["<the tuning legend, exactly as printed>",
                                  "THE TUNING LEGEND exactly as printed",
                                  "<copied from THIS page, character for character>",
                                  "Tuning: <the tuning legend, exactly as printed>"])
def test_legend_copied_from_a_placeholder_is_refused(tmp_path, text):
    work = legend_work(tmp_path, [{"page": 1, "kind": "tuning", "text": text}])
    code, rep, out = run(work, tmp_path)
    assert code == 2 and not out.exists()
    assert "looks copied from the prompt, not read from the page" in rep["error"]


def test_legend_identical_to_a_prompt_example_is_refused(tmp_path, monkeypatch):
    """If a realistic example ever slips back into a prompt, a reader that copies it is still caught."""
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "staff_reader.md").write_text(
        STAFF_PROMPT.read_text() + '\n```json\n{"legends": [{"page": 1, "kind": "tuning", '
        '"text": "Tune all six strings down 1/2 step"}]}\n```\n')
    monkeypatch.setattr(rb, "PROMPTS_DIR", prompts)
    work = legend_work(tmp_path / "a", [{"page": 1, "kind": "tuning", "text": "tune all six strings DOWN 1/2 step."}])
    code, rep, _ = run(work, tmp_path / "a")
    assert code == 2 and "looks copied from the prompt" in rep["error"]
    # the same legend with other words is read from the page: the usual legend check applies
    work = legend_work(tmp_path / "b", [{"page": 1, "kind": "tuning", "text": "Tune strings down 1/2 step"}])
    code, rep, _ = run(work, tmp_path / "b")
    assert code == 2 and "LegendError" in rep["error"] and "copied" not in rep["error"]


@pytest.mark.parametrize("side,key", [("staff", "part_label"), ("tab", "part_label"), ("staff", "section")])
def test_part_label_or_section_copied_from_a_placeholder_is_refused(tmp_path, side, key):
    extra = {key: "<part label printed at the start of this system>" if key == "part_label"
             else "<heading printed above this system>"}
    work = make_work(tmp_path, GOOD_TAB, GOOD_STAFF, **{f"{side}_extra": extra})
    code, rep, _ = run(work, tmp_path)
    assert code == 2 and "looks copied from the prompt" in rep["error"] and key in rep["error"]


def test_real_labels_and_legends_pass_the_canary(tmp_path):
    work = legend_work(tmp_path, [{"page": 1, "kind": "tempo", "text": "Steady (quarter = 100)", "bpm": 100}],
                       staff_extra={"part_label": "Gtr. 1", "section": "Intro"})
    code, rep, _ = run(work, tmp_path)
    assert code == 0, rep.get("error")
    assert json.loads((work / "report.json").read_text())["tempo"] == 100
