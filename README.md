# scan-to-gp

A [Claude Code](https://code.claude.com) skill that turns a **scanned or photographed guitar page**
(standard notation + TAB, e.g. an image-only PDF from a book scan or a phone photo) into a
**Guitar Pro `.gp5`** file -- and refuses to hand you a file it could not verify.

## Why you can trust the output

Most guitar books print every passage twice: once as standard notation, once as tablature. The two
are independent descriptions of the same notes. scan-to-gp exploits that:

1. Scripts find every system on the page, deskew it, and cut it into a **staff-only** image and a
   **tab-only** image (plus per-bar crops at full resolution). Stacked parts stay separate; vocal and
   other notation-only staves are dropped with a warning.
2. Two Claude agents read **independently and in parallel**: one sees only the TAB (string + fret per
   note), the other sees only the staff (letter, octave, printed accidental, duration). Neither can see
   the other's half of the page.
3. A script reconciles them. For every note, the sounding pitch from `(string, fret)` + tuning must equal
   the sounding pitch from the written note (key signature and accidentals applied by the script, guitar
   sounds an octave below written). Every bar's rhythm must fill its time signature. **One disagreement
   = no file**; the disagreements are listed by system / bar / note.
4. Disagreements are adjudicated by looking at zoomed crops of exactly the disputed glyph in both
   images, default-guilty (both reads are suspects). Every fix is a logged patch with a reason; the raw
   reads are never edited.
5. The `.gp5` is written, read back and compared beat by beat, then loaded in
   [alphaTab](https://alphatab.net) (a real Guitar Pro engine) to prove it parses.

Two readers making the *same* mistake on the *same* note in *different* notations is unlikely; that is
the whole point. The report tells you exactly what agreed (e.g. `agreement 37/37 tab notes`), what was adjudicated and
why, and what was not checkable.

## Requirements

- Claude Code
- [uv](https://docs.astral.sh/uv/) (Python dependencies are declared inline in each script and
  installed automatically; no virtualenv to manage)
- Node.js 18+ (for the alphaTab check; installed once into the skill directory with `npm ci`)

## Install

As a plugin (this repo is also a single-plugin marketplace):

```
/plugin marketplace add caoterry/scan-to-gp
/plugin install scan-to-gp@scan-to-gp
```

Or as a plain skill -- copy or symlink the skill directory into your skills folder:

```
git clone https://github.com/caoterry/scan-to-gp
ln -s "$PWD/scan-to-gp/skills/scan-to-gp" ~/.claude/skills/scan-to-gp
npm ci --prefix ~/.claude/skills/scan-to-gp      # alphaTab, for the final load check
```

## Use

In Claude Code, point it at your file:

```
> transcribe ~/scans/lesson-12.pdf to Guitar Pro
```

The skill runs the steps above. On the test page in `tests/fixtures/exercise1/` (a degraded,
rotated, noisy render) the reconcile step printed:

```
systems 2  bars 7  staff notes 38  tab notes 37  adjudications applied 0
round 1 (as read, before adjudication): 0 disagreement(s); 37/37 tab notes in bars that agreed
  tempo 90 bpm (from --tempo)
  TUNING/CAPO (NOT cross-checkable -- from flags/legend): tuning E2,A2,D3,G3,B3,E4, capo 0, notation transpose +0; no tuning/capo legend transcribed

OK: tab and staff agree on every note; wrote exercise1.gp5 (7 bars, 38 notes), read back exactly;
agreement 37/37 tab notes (round 1, as read: 37/37 before 0 adjudication(s)) (+1 tied continuation(s)
the tab does not reprint)
```

and alphaTab parsed the file with 7 bars / 38 notes, matching the plan beat for beat. The output matched the hand-written ground
truth note for note. `tests/fixtures/exercise2` (two guitars stacked in one system, tuned down a half
step) and `exercise3` (vocal line over a two-voice guitar part, capo, shuffle, a vocal-only system whose
four bars stay in the file as untranscribed bars) are
checked the same way from hand-written reads against their LilyPond sources. Those are small synthetic
pages, not a benchmark.

You can also run the scripts yourself (each prints `--help`):

```
S=skills/scan-to-gp/scripts
uv run --script $S/prepare.py book.pdf work/ --pages 3-4         # crops + manifest + contact sheets
uv run --script $S/make_prompts.py work/                         # filled reader prompts
# ... run the two readers (any agent that writes the JSON schema in prompts/) ...
uv run --script $S/reconcile_build.py work/ song.gp5 --tempo 96  # cross-check, build, read back
node $S/verify_alphatab.mjs song.gp5 --plan work/plan.json        # parse in alphaTab, compare beat by beat
```

`inspect_crop.py` gives the readers measuring aids (zoom; which string each tab digit sits on and how
many closed loops it has; staff step and written pitch of each notehead).

## What is verified, and what is not

| | |
|---|---|
| **Cross-checked by two independent reads** | pitch of every note (string + fret vs written note, both voices of a two-voice staff), chords (as pitch multisets; a unison between two voices may be one TAB digit or two), ties (a staff tie must be omitted or parenthesized in the tab -- a plain re-struck digit is a disagreement), ghost notes, bar count, pickup / short bars, multi-bar rests, repeat signs; for stacked parts, the bars of each part |
| **Read once, checked by arithmetic** | rhythm values and rest positions (staff reader only; every bar must add up to its time signature, voice by voice, so a swapped pair inside a bar would still pass), dotted notes, tuplets, key and time signatures (a time signature with the same bar length -- 6/8 vs 3/4 -- is single-read) |
| **Taken from the page legend, NOT cross-checkable** | tuning, capo, and whether the notation is printed transposed (a wrong value shifts both reads together). The staff reader copies the legend; flags that contradict it are refused with the exact flags to use. Tempo marks and shuffle / swing feel are copied from the page too |
| **Checked against an independent parser** | the written `.gp5` is read back beat by beat, then parsed by alphaTab and compared beat by beat (duration, dots, tuplet, string, fret, tie, pitch -- every track, both voices, tempo changes, triplet feel) with the reconciled plan |
| **Reported, not written by default** | techniques from the tab: hammer-ons, pull-offs, slides, bends, vibrato, palm mute, let ring, harmonics (`--write-techniques` writes the simple ones, labelled UNVERIFIED) |
| **Reported as NOT in the file** (named in the summary headline) | counted, one source per number and nothing twice: technique marks from the TAB read (one per printed mark), grace notes, dead notes and bends from both reads' structured records (once per event); listed verbatim, not counted: everything else the readers saw (articulations, dynamics, fermatas, slurs, voltas / D.S. / Coda -- a warning says playback order may be wrong); what each interpretation adjudication dropped; dropped vocal / other-instrument staves; untranscribed bars |
| **Kept in the timeline, untranscribed** | the bars of a system with no guitar notation (e.g. vocal-only): counted from the barlines on its staff (note stems excluded) or refused until you pass `--gap-bars`, written as empty measures (no rests, no notes) with a "Bars a-b not transcribed" marker in every track |

Known blind spot: a tab misread that lands on the same pitch on a different string (e.g. string 6
fret 10 instead of string 5 fret 5) passes the pitch check. It needs a string error *and* a matching
fret error at once, but it is possible.

## Scope / limitations

- Every guitar part needs **both** notation and tab. Tab-only systems are refused by default;
  `--unverified` produces a file whose name contains `UNVERIFIED` (rhythm is a placeholder unless
  the tab prints rhythm). Pages with notation but no tab are refused.
- Layouts handled: several staff + TAB pairs under one system line (stacked parts, e.g. Gtr. 1 over
  Gtr. 2) become one track each -- if their barlines do not line up, or the number of parts changes
  between systems, the page is refused rather than guessed. A system labelled with another part (a
  Gtr. 2 fill after Gtr. 1 systems) becomes a track of its own on the same timeline, empty where it is
  not notated; part labels that alternate (1, 2, 1) are refused. Two voices on one staff are written as
  GP5 voice 2. Vocal and other notation-only staves inside a guitar system are dropped (with a
  warning); a system without any guitar staff + TAB (e.g. a vocal-only system where the guitar repeats
  an earlier figure) keeps its bars in the timeline as untranscribed bars -- the timeline is never
  collapsed. Rows of chord diagrams are ignored; dashed barlines are detected; white-on-black
  (inverted 1-bit) scans are inverted first. `--pages 1,3-4` selects pages.
- Standard 6-string guitar by default; 7-string and 4-string bass tab are detected (pass `--tuning`
  for anything else, low to high, e.g. `D2,A2,D3,G3,B3,E4`). Treble, treble-8vb and bass clefs.
  Tuning / capo conventions of Hal Leonard style books: a half-step-down tuning legend -> `--tuning
  Eb2,Ab2,Db3,Gb3,Bb3,Eb4 --notation-transpose -1`; a capo legend (fret N) -> `--capo N --notation-transpose N`;
  alternate tunings (DADGAD, drop D, open G) -> `--tuning ...`. Reconcile prints these when the
  transcribed legend demands them, and names a constant semitone offset between the reads as a flags
  problem. `--tab-reprints-ties` for books that reprint tied notes as plain digits (weakens the tie check).
- Not representable yet: bends (the staff prints the bend target as a note with no TAB digit -- such a
  bar disagrees until it is adjudicated as an interpretation), grace notes (also a small digit inside a
  chord column), dead notes (x). The readers record them structurally and the summary names them.
- Handwritten, very dense, heavily warped or low-resolution (< ~200 dpi) pages will need much more
  adjudication; the prepare step warns when lines are wavy or too thin.
- HEIC photos need converting to JPG/PNG first. GP5 cannot represent double-dotted notes.

## Copyright

This tool is meant for transcribing material **you own or have the right to use** -- e.g. turning
your own copy of a method book into a practice file. Don't use it to redistribute other people's
copyrighted transcriptions. The repository contains no third-party music: the test pages in
`tests/fixtures/` are original exercises written for this project (CC0), rendered with LilyPond and
artificially degraded to look scanned.

## Development

```
npm ci --prefix skills/scan-to-gp
uv run --with-requirements requirements-dev.txt python -I -m pytest -q
uv run --script tests/fixtures/make_fixture.py tests/fixtures/exercise1/exercise1.ly   # regenerate a fixture (needs LilyPond; --inverted adds a 1-bit white-on-black variant)
SCAN_TO_GP_PRIVATE_FIXTURE=/path/to/private/fixture uv run --script tests/private_regression.py
SCAN_TO_GP_PRIVATE_PAGES=/path/to/private/expect.json uv run --script tests/private_structure.py
SCAN_TO_GP_PRIVATE_REPLAY=/path/to/private/replay.json uv run --script tests/private_replay.py
```

`tests/private_regression.py` runs the whole pipeline against a real scanned book excerpt that cannot
be published, and `tests/private_structure.py` checks prepare's detected structure (systems, bars,
stacked parts, dropped staves, skipped systems' bar counts) on private pages against hand-checked
expectations, and `tests/private_replay.py` re-runs the pipeline on reads that real reader agents made of
private pages and compares the result with the earlier file beat for beat; all three read their inputs
from the environment so nothing private enters the repo.

## License

MIT -- see [LICENSE](LICENSE).
