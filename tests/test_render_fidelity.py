"""What a player draws from the file must not say more than the page: the tempo text over bar 1
(Song.tempoName) is the printed words of the tempo mark or nothing, and no dynamic is written."""

import json

import guitarpro as gp
import pytest

import reconcile_build as rb
from conftest import needs_alphatab, run_alphatab
from test_reconcile import GOOD_STAFF, GOOD_TAB, run
from test_reconcile_v2 import legend_work

THREE = {f"p1_s{i}": (GOOD_TAB, [{"events": GOOD_STAFF[0]["events"]}], "none") for i in (2, 3)}


def tempo_name(out) -> str:
    return gp.parse(str(out)).tempoName


@pytest.mark.parametrize("text,words", [
    ("Moderately (quarter = 120)", "Moderately"),
    ("Easy Shuffle (quarter = 100)", "Easy Shuffle"),
    ("Moderately (dotted quarter = 60)", "Moderately"),
    ("Moderately ♩. = 60", "Moderately"),
    ("Fast (half note = 100)", "Fast"),
    ("Slowly q = 80", "Slowly"),
    ("a tempo q = 120", "a tempo"),
    ("Steady Pulse ♩ = 116", "Steady Pulse"),
    ("Driving Pulse ♩ = 152-160", "Driving Pulse"),
    ("Moderately (♩ = c. 108)", "Moderately"),
    ("Lazy Groove ♩ = 60 (eighth pair = long-short triplet)", "Lazy Groove"),
    ("Gently flowing", "Gently flowing"),
    ("q = 96", ""),
    ("♩ = 120", ""),
    ("(quarter = 72)", ""),
    ("", ""),
    # a feel equation is not a tempo word, bracketed or not, nor the words that introduce it
    ("Moderately ♩ = 120 [♫ = ♪³♪]", "Moderately"),
    ("Moderately ♩ = 120 ♫ = ♩♪", "Moderately"),
    ("Moderately ♩ = 120 Swing 8ths: ♫ = ♪³♪", "Moderately"),
    ("Lazy Groove, eighth pair = long-short triplet", "Lazy Groove"),  # a reader's own description
    ("Moderately (in 2) ♩ = 76", "Moderately (in 2)"),
    # M.M., approximate marks, a bpm unit: all part of the metronome mark
    ("M.M. ♩ = 120", ""),
    ("Moderately M.M. = 120", "Moderately"),
    ("Moderately ♩ ≈ 120", "Moderately"),
    ("Moderately ♩ ~ 120", "Moderately"),
    ("Moderately ♩ = 120 bpm", "Moderately"),
    # brackets: no half pair survives, a pair around the whole name goes
    ("(Moderately) ♩ = 120", "Moderately"),
    ("Moderately ♩ = 120)", "Moderately"),
    ("Moderately (♩ = 120", "Moderately"),
    ("Moderately [♫ = ♪³♪", "Moderately"),
    ("Rock ’n’ Roll (quarter = 160)", "Rock ’n’ Roll"),  # typography is the writer's business, not this one's
    ("Modéré (quarter = 100)", "Modéré"),
    ("中速 (♩ = 100)", "中速"),
])
def test_tempo_words_are_only_the_printed_words(text, words):
    assert rb.tempo_words(text) == words


@pytest.mark.parametrize("text,bpm", [("Moderately ♩ ≈ 120", 120), ("Moderately (♩ = c. 108)", 108),
                                      ("Slowly q ~ 66", 66)])
def test_metronome_number_without_a_bpm_field(text, bpm):
    """The reader is asked for `bpm`; when it leaves it out, the number printed in the mark is used --
    an approximate mark ('≈', 'c.') too, never the 90 bpm placeholder."""
    lg = rb.parse_legends([{"page": 1, "kind": "tempo", "text": text}])
    assert [t["bpm"] for t in lg.tempos] == [bpm]


def test_tempo_name_is_the_page_word(tmp_path):
    legends = [{"page": 1, "kind": "tempo", "text": "Moderately (quarter = 120)", "bpm": 120}]
    code, rep, out = run(legend_work(tmp_path, legends), tmp_path)
    assert code == 0 and rep["tempo"] == 120
    assert tempo_name(out) == "Moderately" and rep["tempo_name"] == "Moderately"


@pytest.mark.parametrize("legends,args", [
    ([{"page": 1, "kind": "tempo", "text": "q = 96", "bpm": 96}], ()),  # a bare metronome mark
    ([], ()),  # no tempo mark: the 90 bpm placeholder gets no word either
    ([], ("--tempo", "100")),
])
def test_no_printed_tempo_word_means_no_tempo_name(tmp_path, legends, args):
    code, rep, out = run(legend_work(tmp_path, legends), tmp_path, *args)
    assert code == 0
    assert tempo_name(out) == "" and rep["tempo_name"] == ""  # never pyguitarpro's default "Moderate"


@pytest.mark.parametrize("legends", [
    [{"page": 1, "kind": "tempo", "text": "Gently flowing"}],  # words, no number
    [{"page": 1, "kind": "tempo", "text": "Loosely, gently"}],
    [],  # no tempo mark at all
])
def test_placeholder_tempo_is_hidden_and_named_nothing(tmp_path, legends):
    """No metronome number at the start: 90 bpm is a placeholder. The file sets GP5.1's song-level
    hideTempo (Guitar Pro hides the mark; alphaTab 1.8.3 reads and ignores the flag, so players built on it
    still draw '♩ = 90'), and writes no tempo text: the page's words beside an invented number ('Loosely,
    gently ♩ = 90') would read as the page's own marking. The warning names the printed words."""
    work = legend_work(tmp_path, legends)
    code, rep, out = run(work, tmp_path)
    assert code == 0 and rep["tempo"] == rb.DEFAULT_TEMPO
    song = gp.parse(str(out))
    assert song.hideTempo is True and song.tempoName == ""
    assert rep["tempo_hidden"] is True and rep["tempo_name"] == ""
    assert json.loads((work / "plan.json").read_text())["tempo_hidden"] is True
    warn = next(w for w in rep["warnings"] if "placeholder" in w)
    assert all(lg["text"] in warn for lg in legends) and "hidden" in warn and "alphaTab" in warn, warn


@pytest.mark.parametrize("legends,args", [
    ([{"page": 1, "kind": "tempo", "text": "Moderately (quarter = 120)", "bpm": 120}], ()),
    ([{"page": 1, "kind": "tempo", "text": "Slowly q = 80", "bpm": 80}], ("--tempo", "72")),  # an override
])
def test_a_printed_tempo_is_not_hidden(tmp_path, legends, args):
    work = legend_work(tmp_path, legends)
    code, rep, out = run(work, tmp_path, *args)
    assert code == 0 and gp.parse(str(out)).hideTempo is False and rep["tempo_hidden"] is False
    assert json.loads((work / "plan.json").read_text())["tempo_hidden"] is False


@pytest.mark.parametrize("legends,args,name", [
    ([{"page": 1, "kind": "tempo", "text": "Loosely, gently"}], ("--tempo", "60"), ""),  # words, no number
    ([], ("--tempo", "100"), ""),  # no tempo mark at all
])
def test_a_chosen_tempo_the_page_does_not_print_is_hidden(tmp_path, legends, args, name):
    """--tempo N where the page prints no metronome number at the start: N is the user's choice for
    playback, not a mark on the page -- hidden like the placeholder, and no printed word beside it
    ('Loosely, gently ♩ = 60' would read as the page's marking)."""
    work = legend_work(tmp_path, legends)
    code, rep, out = run(work, tmp_path, *args)
    assert code == 0 and rep["tempo"] == int(args[1]) and rep["tempo_source"] == "--tempo"
    song = gp.parse(str(out))
    assert song.hideTempo is True and rep["tempo_hidden"] is True
    assert song.tempoName == name and rep["tempo_name"] == name
    assert not any("placeholder" in w for w in rep["warnings"])  # the user chose it: no placeholder warning


def test_readback_checks_the_hidden_tempo(tmp_path):
    work = legend_work(tmp_path, [])
    code, _, out = run(work, tmp_path)
    assert code == 0
    pf = json.loads((work / "plan.json").read_text())
    plan = {**pf, "tracks": [{"name": pf["name"], "bars": pf["bars"]}, *pf["more_tracks"]]}
    rb.readback(out, plan)
    with pytest.raises(AssertionError, match="hide"):
        rb.readback(out, {**plan, "tempo_hidden": False})


def test_a_later_tempo_mark_does_not_name_the_start(tmp_path):
    legends = [{"page": 1, "kind": "tempo", "text": "Faster q = 132", "bpm": 132, "system": "p1_s3"}]
    code, rep, out = run(legend_work(tmp_path, legends, more_systems=THREE), tmp_path)
    assert code == 0 and rep["tempo_changes"] == {"3": 132}
    assert tempo_name(out) == ""


def test_the_flag_tempo_keeps_the_printed_word(tmp_path):
    legends = [{"page": 1, "kind": "tempo", "text": "Slowly q = 80", "bpm": 80}]
    code, rep, out = run(legend_work(tmp_path, legends), tmp_path, "--tempo", "72")
    assert code == 0 and rep["tempo"] == 72 and tempo_name(out) == "Slowly"


def test_no_note_carries_a_dynamic(tmp_path):
    """Guard, not a fix: dynamics are not transcribed, so every note keeps GP5's default velocity, which
    pyguitarpro writes as NO velocity byte (note flag 0x10 unset) -- the file states no dynamic at all."""
    legends = [{"page": 1, "kind": "tempo", "text": "Moderately (quarter = 120)", "bpm": 120}]
    code, _, out = run(legend_work(tmp_path, legends, more_systems=THREE), tmp_path)
    assert code == 0
    notes = [n for tr in gp.parse(str(out)).tracks for m in tr.measures for v in m.voices for b in v.beats
             for n in b.notes]
    assert notes and all(n.velocity == gp.Velocities.default for n in notes)


@needs_alphatab
def test_alphatab_checks_the_tempo_name(tmp_path):
    work = legend_work(tmp_path, [{"page": 1, "kind": "tempo", "text": "Moderately (quarter = 120)", "bpm": 120}])
    code, _, out = run(work, tmp_path)
    assert code == 0
    p = run_alphatab(out, "--plan", str(work / "plan.json"))
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["tempoName"] == "Moderately"
    plan = json.loads((work / "plan.json").read_text())
    plan["tempo_name"] = "Moderate"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(plan))
    p = run_alphatab(out, "--plan", str(bad))
    assert p.returncode == 1 and "tempo name" in p.stderr


@needs_alphatab
def test_alphatab_flags_a_written_dynamic(tmp_path):
    work = legend_work(tmp_path, [{"page": 1, "kind": "tempo", "text": "q = 96", "bpm": 96}])
    code, _, out = run(work, tmp_path)
    assert code == 0
    song = gp.parse(str(out))
    for tr in song.tracks:
        for m in tr.measures:
            for v in m.voices:
                for b in v.beats:
                    for n in b.notes:
                        n.velocity = gp.Velocities.mezzoForte
    with_dyn = tmp_path / "with_dynamic.gp5"
    gp.write(song, str(with_dyn), version=(5, 1, 0))
    p = run_alphatab(with_dyn, "--plan", str(work / "plan.json"))
    assert p.returncode == 1 and "dynamic" in p.stderr


# ---- text in the file is ASCII: GP5 strings carry no encoding -- pyguitarpro writes cp1252 (and cannot
# encode anything else), Guitar Pro reads cp1252, alphaTab 1.8.3 reads UTF-8. Only ASCII is the same in all.

@pytest.mark.parametrize("text,written,complete", [
    ("Rock ’n’ Roll", "Rock 'n' Roll", True),
    ("Modéré", "Modere", True),
    ("Für Elise – live", "Fur Elise - live", True),
    ("Straße", "Strasse", True),
    ("Gtr. ① (½ step down)", "Gtr. 1 (1/2 step down)", True),
    ("Coda ⊕", "Coda", True),
    ("“Quoted”…", '"Quoted"...', True),
    ("中速 Moderato", "Moderato", False),  # incomplete: the caller writes nothing of it
    ("", "", True),
])
def test_gp_text_is_ascii(text, written, complete):
    assert rb.gp_text(text) == (written, complete)


ASCII_TEMPO = [
    ("Rock ’n’ Roll (quarter = 160)", 160, "Rock 'n' Roll"),  # typographic quotes -> ASCII
    ("Modéré (quarter = 100)", 100, "Modere"),  # accents folded
    ("Moderately ♩ ≈ 120", 120, "Moderately"),  # '≈' is part of the metronome mark
    ("Easy Shuffle ♩ ≈ 100", 100, "Easy Shuffle"),  # crashed the write (UnicodeEncodeError on '≈')
]


@pytest.mark.parametrize("text,bpm,name", ASCII_TEMPO)
def test_tempo_name_is_written_as_ascii(tmp_path, text, bpm, name):
    work = legend_work(tmp_path, [{"page": 1, "kind": "tempo", "text": text, "bpm": bpm}])
    code, rep, out = run(work, tmp_path)
    assert code == 0, rep.get("error")
    assert rep["tempo"] == bpm and rep["tempo_name"] == name and tempo_name(out) == name


@needs_alphatab
@pytest.mark.parametrize("text,bpm,name", ASCII_TEMPO)
def test_alphatab_reads_the_tempo_name_as_written(tmp_path, text, bpm, name):
    """alphaTab decodes GP5 text as UTF-8: a cp1252 byte above 127 came out as U+FFFD ('Rock �n� Roll')."""
    work = legend_work(tmp_path, [{"page": 1, "kind": "tempo", "text": text, "bpm": bpm}])
    code, rep, out = run(work, tmp_path)
    assert code == 0, rep.get("error")
    p = run_alphatab(out, "--plan", str(work / "plan.json"))
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["tempoName"] == name


def test_tempo_words_a_gp5_cannot_hold_are_not_written(tmp_path):
    """Letters outside Latin cannot be written: no tempo name (never half of one), and a warning that
    names the printed text."""
    text = "中速 Moderato (♩ = 100)"
    work = legend_work(tmp_path, [{"page": 1, "kind": "tempo", "text": text, "bpm": 100}])
    code, rep, out = run(work, tmp_path)
    assert code == 0, rep.get("error")
    assert rep["tempo"] == 100 and rep["tempo_name"] == "" and tempo_name(out) == ""
    assert any("中速 Moderato" in w for w in rep["warnings"]), rep["warnings"]


@pytest.mark.parametrize("stem,title,lost", [
    ("Für Elise – live", "Fur Elise - live", False),
    ("Rock ’n’ Roll", "Rock 'n' Roll", False),
    ("夜曲", "", True),
])
def test_title_is_written_as_ascii(tmp_path, stem, title, lost):
    """The title is the output file's name (or --title): the same exposure as the tempo name."""
    work = legend_work(tmp_path, [])
    for args, out in (([], tmp_path / f"{stem}.gp5"), (["--title", stem], tmp_path / "out.gp5")):
        code = rb.main([str(work), str(out), "--tempo", "100", *args])
        rep = json.loads((work / "report.json").read_text())
        assert code == 0, rep.get("error")
        assert gp.parse(str(out)).title == title
        assert any(stem in w and "title" in w for w in rep["warnings"]) == lost, rep["warnings"]


def test_part_label_and_section_are_written_as_ascii(tmp_path):
    from test_reconcile import make_work
    work = make_work(tmp_path, GOOD_TAB, GOOD_STAFF,
                     staff_extra={"part_label": "Gtr. 1 – Acoustique", "section": "Refrain – Coda ⊕"})
    code, rep, out = run(work, tmp_path, "--tempo", "100")
    assert code == 0, rep.get("error")
    song = gp.parse(str(out))
    assert song.tracks[0].name == "Gtr. 1 - Acoustique" and rep["tracks"] == ["Gtr. 1 - Acoustique"]
    assert song.measureHeaders[0].marker.title == "Refrain - Coda"


def test_part_label_and_section_a_gp5_cannot_hold(tmp_path):
    """A label in another script: the track gets the name it gets with no label, a section gets no marker
    -- each with a warning naming the printed text."""
    from test_reconcile import make_work
    work = make_work(tmp_path, GOOD_TAB, GOOD_STAFF, staff_extra={"part_label": "ギター 1", "section": "サビ"})
    code, rep, out = run(work, tmp_path, "--tempo", "100")
    assert code == 0, rep.get("error")
    song = gp.parse(str(out))
    assert song.tracks[0].name == "Guitar" and song.measureHeaders[0].marker is None
    assert any("ギター 1" in w for w in rep["warnings"]) and any("サビ" in w for w in rep["warnings"]), rep["warnings"]


@needs_alphatab
def test_alphatab_checks_title_track_names_and_markers(tmp_path):
    from test_reconcile import make_work
    work = make_work(tmp_path, GOOD_TAB, GOOD_STAFF,
                     staff_extra={"part_label": "Gtr. 1 – Acoustique", "section": "Refrain – Coda ⊕"})
    out = tmp_path / "Für Elise.gp5"
    assert rb.main([str(work), str(out), "--tempo", "100"]) == 0
    p = run_alphatab(out, "--plan", str(work / "plan.json"))
    assert p.returncode == 0, p.stderr
    got = json.loads(p.stdout)
    assert got["title"] == "Fur Elise" and got["perTrack"][0]["name"] == "Gtr. 1 - Acoustique"
    assert got["markers"] == ["1:Refrain - Coda"]
    plan = json.loads((work / "plan.json").read_text())
    for key, bad_value, msg in (("title", "Für Elise", "title"), ("name", "Gtr. 1 – Acoustique", "track 1 name")):
        bad = tmp_path / f"bad_{key}.json"
        bad.write_text(json.dumps({**plan, key: bad_value}))
        p = run_alphatab(out, "--plan", str(bad))
        assert p.returncode == 1 and msg in p.stderr, p.stderr
