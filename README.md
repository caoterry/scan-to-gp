# scan-to-gp

![Demo: the CC0 test page is split into notation and TAB crops, two blind readers each read one half, a script finds all 56 notes in agreement and writes the .gp5; one TAB digit changed on purpose makes the script refuse with no file; the .gp5 then plays in an alphaTab-based player](docs/demo.gif)

*49 s, silent: the repo's own test page `tests/fixtures/exercise2` (CC0), from scan to playback.*

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

- [Claude Code](https://code.claude.com). The skill dispatches subagents and runs local scripts, so it
  needs a shell with uv and Node; it is not meant for environments without them (such as claude.ai
  chat).
- [uv](https://docs.astral.sh/uv/) 0.5.23 or later (tested with 0.11.13). Each script pins its Python
  dependencies inline and is locked in `<script>.py.lock`; uv installs them on first run, no virtualenv
  to manage. The scripts need Python 3.10+ (3.12+ for the image scripts); uv can download one if none
  is installed.
- Node.js 18+ (for the alphaTab check; installed once into the skill directory with `npm ci`)

## What it runs and fetches

- **uv** runs the Python scripts in `skills/scan-to-gp/scripts/` with `uv run --locked --script`. On
  first run it downloads their dependencies from PyPI (numpy, scipy, pillow, pymupdf, pyguitarpro --
  the exact versions, with hashes, recorded in the lockfiles) and, if no suitable Python is installed,
  a Python interpreter.
- **`npm ci`** installs [alphaTab](https://alphatab.net) (pinned to 1.8.3 in `package-lock.json`) into
  the skill directory, once, for the final load check.
- **Subagents:** for each batch of up to 8 systems, two general-purpose subagents are dispatched as the
  independent readers (one sees only the TAB crops, the other only the staff crops). They look at the
  crops through your Claude Code session, like any image you show Claude.
- **Files:** it reads only the file you name, and writes to a local work folder (by default next to
  it, `<name>.scan2gp/`: page images, crops, the two reads, adjudications, report) plus the `.gp5`.
- No other network calls, no telemetry; the scripts contain no network code and upload nothing.
  scan-to-gp collects no personal data. Besides the work folder and the `.gp5`, the only things
  written are uv's package cache and the skill's `node_modules`.

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

In Claude Code, point it at your file.

### Examples

```
> transcribe ~/scans/lesson-12.pdf to Guitar Pro
> turn pages 3-4 of ~/scans/book.pdf into a .gp5
> this photo has two guitar parts stacked -- one track per part: ~/Desktop/riff.jpg
> transcribe ~/scans/solo.png and also write the hammer-ons and slides
> ~/scans/riff-tab.png is TAB only, no notation -- give me an unverified .gp5 anyway
```

Stacked parts become one track each without being asked. Techniques are written only on request and
labelled UNVERIFIED (they are read once, not cross-checked). A TAB-only page cannot be cross-checked,
so it is refused unless you accept an unverified file; its name then contains `UNVERIFIED`.

Whatever the prompt, the skill runs the steps above. On the test page in `tests/fixtures/exercise1/` (a degraded,
rotated, noisy render) the reconcile step printed:

```
systems 2  bars 7  staff notes 38  tab notes 37  adjudications applied 0
round 1 (as read, before adjudication): 0 disagreement(s); 37/37 tab notes in bars that agreed
  tempo 90 bpm (from --tempo); marked hidden: the page prints no metronome number at the start (alphaTab-based players still draw it)
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
uv run --locked --script $S/prepare.py book.pdf work/ --pages 3-4         # crops + manifest + contact sheets
uv run --locked --script $S/make_prompts.py work/                         # filled reader prompts
# ... run the two readers (any agent that writes the JSON schema in prompts/) ...
uv run --locked --script $S/reconcile_build.py work/ song.gp5 --tempo 96  # cross-check, build, read back
node $S/verify_alphatab.mjs song.gp5 --plan work/plan.json        # parse in alphaTab, compare beat by beat
```

`inspect_crop.py` gives the readers measuring aids (zoom; which string each tab digit sits on and how
many closed loops it has; staff step and written pitch of each notehead).

## What is verified, and what is not

| | |
|---|---|
| **Cross-checked by two independent reads** | pitch of every note (string + fret vs written note, both voices of a two-voice staff), chords (as pitch multisets; a unison between two voices may be one TAB digit or two), ties (a staff tie must be omitted or parenthesized in the tab -- a plain re-struck digit is a disagreement), ghost notes, bar count, pickup / short bars, multi-bar rests, repeat signs; for stacked parts, the bars of each part |
| **Read once, checked by arithmetic** | rhythm values and rest positions (staff reader only; every bar must add up to its time signature, voice by voice, so a swapped pair inside a bar would still pass), dotted notes, tuplets, key and time signatures (a time signature with the same bar length -- 6/8 vs 3/4 -- is single-read) |
| **Taken from the page legend, NOT cross-checkable** | tuning, capo, and whether the notation is printed transposed (a wrong value shifts both reads together). The staff reader copies the legend; flags that contradict it are refused with the exact flags to use. Tempo marks and shuffle / swing feel are copied from the page too; the tempo text over bar 1 is the mark's printed words, or none. Text in the file (title, part labels, section names, tempo text) is ASCII -- a GP5 string has no encoding (Guitar Pro reads cp1252, alphaTab UTF-8): quotes and dashes become ASCII, accents are dropped (Modéré -> Modere), and text in another script is not written (a warning names it) |
| **Checked against an independent parser** | the written `.gp5` is read back beat by beat, then parsed by alphaTab and compared beat by beat (duration, dots, tuplet, string, fret, tie, pitch -- every track, both voices, title, track names, markers, tempo changes, the tempo text, triplet feel, no dynamic written) with the reconciled plan |
| **Reported, not written by default** | techniques from the tab: hammer-ons, pull-offs, slides, bends, vibrato, palm mute, let ring, harmonics (`--write-techniques` writes the simple ones, labelled UNVERIFIED) |
| **Reported as NOT in the file** (named in the summary headline) | counted, one source per number and nothing twice: technique marks from the TAB read (one per printed mark), grace notes, dead notes and bends from both reads' structured records (once per event); listed verbatim, not counted: everything else the readers saw (articulations, dynamics, fermatas, slurs, voltas / D.S. / Coda -- a warning says playback order may be wrong); what each interpretation adjudication dropped; dropped vocal / other-instrument staves; untranscribed bars |
| **Kept in the timeline, untranscribed** | the bars of a system with no guitar notation (e.g. vocal-only): counted from the barlines on its staff (note stems excluded) or refused until you pass `--gap-bars`, written as empty measures (no rests, no notes) with a "Bars a-b not transcribed" marker in every track |

Known blind spot: a tab misread that lands on the same pitch on a different string (e.g. string 6
fret 10 instead of string 5 fret 5) passes the pitch check. It needs a string error *and* a matching
fret error at once, but it is possible.

## Field test

0.2 was run on 6 real scanned pages from published guitar books, page 1 of each: single and stacked
guitar parts, two voices on one staff, vocal and other staves to skip, a 1-bit scan, half-step-down
tuning, an alternate tuning, a capo. The PDF went in as is, the only flags used were ones the skill
itself asked for, and disagreements were settled only through its own adjudication step. (Before that,
the pipeline was developed against a scanned exercise book, which stays in the private regression
tests.)

- All 6 pages ran end to end (0.1 produced a correct file for none of them as shipped: 4 refused, 1
  silently wrong, 1 needed a hand-made patch). Every tuning / capo flag came from the skill's own
  legend check.
- First pass, before adjudication: 3 pages agreed on every note; the other 3 had 1, 3 and 4
  disagreements (a bend whose target has no TAB digit, an engraving error on the page, grace notes
  inside chords, dead-note strums), settled by 15 logged interpretation adjudications.
- Blind spot-checks by a separate agent (4 seeded random bars + the 2 densest bars per page) found
  **0 wrong notes and 0 rhythm errors in 504 checked notes**, and 2 non-note issues in those bars: a
  section marker one bar early, a pickup bar written as a bar of 1/4.
- Page structure was right on 5 of 6 pages at 0.2. The 6th (a skipped vocal-only system plus a change
  of part) was fixed in 0.2.1 and verified by replaying that page's existing reads through the new
  version.
- On one page the dead-note strums became rests (lossy; GP dead notes are not written yet).
- Each page took 14-30 minutes, with 3 subagents.

Caveats: this is a small sample, not a benchmark, and not a held-out one -- these are the pages whose
0.1 failures drove the 0.2 changes. The spot-checker is also an LLM, so its count is evidence, not
ground truth. The pages are copyrighted and are not included in this repository. What still needs
judgement or is dropped is reported in the summary: bends whose target has no TAB digit, grace notes
inside chords, dead notes, harmonics.

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
  Tuning / capo conventions of many published tab books: a half-step-down tuning legend -> `--tuning
  Eb2,Ab2,Db3,Gb3,Bb3,Eb4 --notation-transpose -1`; a capo legend (fret N) -> `--capo N --notation-transpose N`;
  alternate tunings (DADGAD, drop D, open G) -> `--tuning ...`. Reconcile prints these when the
  transcribed legend demands them, and names a constant semitone offset between the reads as a flags
  problem. `--tab-reprints-ties` for books that reprint tied notes as plain digits (weakens the tie check).
- Not representable yet: bends (the staff prints the bend target as a note with no TAB digit -- such a
  bar disagrees until it is adjudicated as an interpretation), grace notes (also a small digit inside a
  chord column), dead notes (x). The readers record them structurally and the summary names them.
- No metronome number at the start: 90 bpm is written as a placeholder (a loud warning; pass `--tempo
  N`). That tempo, or the `--tempo N` you pass, is marked hidden (GP5.1 hideTempo, meant for Guitar Pro;
  not checked there) and gets no tempo text. alphaTab (checked: 1.8.3) and players built on it ignore
  that flag and still draw "♩ = N" over bar 1 -- a mark the page does not print.
- A `--tempo N` for a page that prints a start tempo replaces that number and keeps the page's tempo
  words, so the file shows a number the page does not print (e.g. its "Slowly" with your 72).
- Dynamics are not transcribed, and the file states none (no note velocity). alphaTab (checked: 1.8.3)
  and players built on it still draw their own default "f" on a GP5 file that writes no velocity, like
  this one (at least at each track's first note); that mark is the player's, not the page's.
- Handwritten, very dense, heavily warped or low-resolution (< ~200 dpi) pages will need much more
  adjudication; the prepare step warns when lines are wavy or too thin.
- HEIC photos need converting to JPG/PNG first. GP5 cannot represent double-dotted notes.

## Copyright

This tool is meant for transcribing material **you own or have the right to use** -- e.g. turning
your own copy of a method book into a practice file. Don't use it to redistribute other people's
copyrighted transcriptions. The repository contains no third-party music: the test pages in
`tests/fixtures/` are original exercises written for this project (CC0), rendered with LilyPond and
artificially degraded to look scanned.

## Support

Questions and bug reports: [GitHub Issues](https://github.com/caoterry/scan-to-gp/issues). When a run
is refused or disagrees, the summary at the end names each reason (system / bar / note) -- that is the
first place to look. If you report a problem with a copyrighted page, describe the layout and paste the
summary; please don't attach the page.

## Development

```
npm ci --prefix skills/scan-to-gp
uv run --locked --script tests/run_tests.py -q      # the test suite, with the same pinned versions as the scripts
uv run --locked --script tests/fixtures/make_fixture.py tests/fixtures/exercise1/exercise1.ly   # regenerate a fixture (needs LilyPond; --inverted adds a 1-bit white-on-black variant)
SCAN_TO_GP_PRIVATE_FIXTURE=/path/to/private/fixture uv run --locked --script tests/private_regression.py
SCAN_TO_GP_PRIVATE_PAGES=/path/to/private/expect.json uv run --locked --script tests/private_structure.py
SCAN_TO_GP_PRIVATE_REPLAY=/path/to/private/replay.json uv run --locked --script tests/private_replay.py
```

Dependencies are exact pins in each script's `# /// script` block, locked with `uv lock --script <file>`;
`requirements.txt` / `requirements-dev.txt` repeat them for pip. After changing a pin, re-lock that
script -- `tests/test_pins.py` fails on a stale lock, a range instead of a pin, versions that disagree,
or a documented `uv run` without `--locked`.

`tests/private_regression.py` runs the whole pipeline against a real scanned book excerpt that cannot
be published, and `tests/private_structure.py` checks prepare's detected structure (systems, bars,
stacked parts, dropped staves, skipped systems' bar counts) on private pages against hand-checked
expectations, and `tests/private_replay.py` re-runs the pipeline on reads that real reader agents made of
private pages and compares the result with the earlier file beat for beat; all three read their inputs
from the environment so nothing private enters the repo.

## License

MIT -- see [LICENSE](LICENSE).

---
Made by [Terry Cao](https://github.com/caoterry) · see also: [woodshed](https://woodshed-web.fly.dev) — any tab you have, playing in seconds
