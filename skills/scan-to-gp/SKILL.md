---
name: scan-to-gp
description: Transcribe a SCANNED or photographed guitar/bass page (image-only PDF, PNG/JPG photo of tab) that has standard notation AND tablature into a verified Guitar Pro .gp5 file. Two independent agent reads (tab digits vs staff notes) must agree note-by-note before any file is written. Use when the user wants to turn a scanned tab / photo of tab / image PDF of guitar music into Guitar Pro, gp5, GP, or asks to "transcribe this page" of guitar music from an image.
---

# scan-to-gp

Turn scanned guitar pages (standard notation + TAB) into a `.gp5` you can trust, because two
independent reads -- one of the TAB digits, one of the staff notes -- must produce the same sounding
pitch for every note, and the staff rhythm must fill every bar. Scripts do everything deterministic
(extraction, deskew, staff detection, cropping, reconciling, building, verifying); agents only read
glyphs and adjudicate.

`${CLAUDE_SKILL_DIR}` is this skill's directory. All Python runs through `uv run --script`
(dependencies are declared inline; the scripts re-exec themselves with `python -I`).

## Scope (tell the user up front if their page falls outside it)

- Needs a notation staff with a TAB staff under it for every guitar part -- that pairing is what makes the
  result verifiable. Tab-only pages are refused unless the user explicitly accepts an UNVERIFIED file
  (`--unverified`; output name contains `UNVERIFIED`). Notation-only pages are refused.
- Handled layouts: stacked parts (Gtr. 1 over Gtr. 2, several staff+TAB pairs under one system line) --
  one track per part; two voices on one staff (stems up / stems down) -- GP5 voice 2; vocal / other
  instrument staves in a guitar system -- dropped with a warning; systems with no guitar notation (a
  vocal-only system where the guitar repeats an earlier figure) -- skipped, but their bars stay in the
  timeline as UNTRANSCRIBED (empty) bars, never collapsed; a part label naming another part (Gtr. 2
  after Gtr. 1) -- its own track on the common timeline, empty where it is not notated; chord-diagram
  rows -- ignored; dashed barlines; inverted (white-on-black) 1-bit scans; multi-page PDFs (`--pages`).
- Cross-checked by both reads: the pitch of every note (string+fret vs written note, both voices),
  chords, ties (staff tie <-> tab digit omitted or in parentheses), ghost notes, bar count, pickup/short
  bars, multi-bar rests, repeat signs, and for stacked parts the bars of each part.
- Read once, checked only by arithmetic: rhythm values and rest positions (staff reader; every bar must
  add up, voice by voice), key and time signatures (a time signature that gives the same bar length --
  6/8 vs 3/4, C vs cut -- is single-read).
- NOT cross-checkable, taken from the page legend + flags: tuning, capo, and whether the notation is
  printed transposed. Both reads shift together, so a wrong value gives "N/N agree" and a file that is a
  half/whole step off. The staff reader copies the legend; reconcile refuses flags that contradict it.
- NOT representable yet (reported in the summary, never silently dropped): bends (the staff prints the
  bend target as a note, the tab prints no digit), grace notes (recorded structurally), dead notes (x),
  techniques (written only as UNVERIFIED extras on request), voltas/D.S./Coda, 8va lines, dynamics,
  lyrics, chord names.
- Handwritten or very dense/low-resolution pages: warn the user before spending effort; results will
  need more adjudication.

## Procedure

### 0. Setup (once per machine)
`test -d "${CLAUDE_SKILL_DIR}/node_modules/@coderline/alphatab" || npm ci --prefix "${CLAUDE_SKILL_DIR}" --no-audit --no-fund`
(needs `uv` and `node`; if either is missing, tell the user how to install it and stop).

### 1. Prepare
Pick a work dir next to the user's file, e.g. `<name>.scan2gp/`. Then:
```
uv run --script "${CLAUDE_SKILL_DIR}/scripts/prepare.py" <input.pdf|.png|.jpg ...> <workdir> [--pages 1,3-4]
```
`--pages` selects pages (1-based across all inputs in order); use it for long PDFs instead of
extracting pages yourself.
- Exit 3 = refused: report each reason it prints (TAB without notation -> offer `--unverified` and say
  what that gives up; notation with no TAB anywhere; stacked parts whose barlines do not line up or whose
  number changes between systems -- never work around that by splitting crops yourself).
- Exit 2 = nothing found: say so; ask for a better scan.
- Always Read `<workdir>/contact/p<N>.png` and `contact/systems.png` and check: every system found
  (compare with the page), stacked parts labelled `part k/n` where the page shows Gtr. 1 over Gtr. 2,
  grey DROPPED/SKIPPED boxes only on staves without TAB, the magenta split between staff and tab without
  cutting notes, orange barlines on real barlines. Relay every WARNING line to the user -- especially a
  SKIPPED system: prepare counts its bars from the barlines on its staff (orange lines on the grey box;
  note stems excluded) and reconcile writes that many UNTRANSCRIBED (empty) bars in every track at its
  place. Check that count against the page; if it is wrong, or prepare says UNKNOWN (a barline it could
  not tell from a stem, a dashed barline, a multi-bar rest ...), count the bars on the page yourself and
  pass `--gap-bars <id>:<bars>` to reconcile (`0` if the staff is not a system of its own). If the page
  tells the guitar to play there (an instruction to repeat an earlier figure, a fill, a repeat), those
  notes are NOT in the file -- say so. If a system is missing or mis-split, stop and tell the user -- do
  not transcribe a partial page as if whole.
- `crops/p<N>_header.png` is everything above the first system (tuning / capo legend, tempo) and
  `crops/p<N>_footer.png` everything below the last one (footnotes); the staff reader copies their
  legends in step 2. Look at them yourself too.

### 2. Dispatch the readers (in parallel, independent)
```
uv run --script "${CLAUDE_SKILL_DIR}/scripts/make_prompts.py" <workdir>        # batches of <= 8 systems
```
(add `--unverified` for tab-only pages). For EVERY batch, launch one TAB reader and one STAFF reader
with the Agent tool -- all of them in a single message so they run concurrently (general-purpose
agents; they need Read, Bash, Write). Prompt each with exactly:
`Read <prompt file> and follow it exactly. Your work dir is <workdir>.`
Never paste one reader's output into another reader's prompt. Keep their replies: the uncertain spots
they list are the first places to look if reconcile disagrees. The staff reader records a second voice
with `"voice": 2` and copies the legends; check its reply lists them.

### 3. Reconcile + build
```
uv run --script "${CLAUDE_SKILL_DIR}/scripts/reconcile_build.py" <workdir> <out.gp5> [--tempo N] [--title T] \
    [--tuning E2,A2,D3,G3,B3,E4] [--capo N] [--notation-transpose N] [--tab-reprints-ties] [--gap-bars ID:N]
```
- Tuning is low -> high; default by tab line count (6: standard, 7: B1 standard, 4: E1 bass).
- Legends decide the tuning flags (Hal Leonard conventions -- reconcile prints the exact flags when the
  legend and the flags disagree, exit 2 `LegendError`):
  - Half-step-down (or whole-step-down) tuning legends: the book prints notation AND TAB as if in
    standard tuning: `--tuning Eb2,Ab2,Db3,Gb3,Bb3,Eb4 --notation-transpose -1` (both flags).
  - Capo pieces print the notation in the fingered key, TAB counted from the capo:
    `--capo N --notation-transpose N`.
  - Alternate tunings (DADGAD, drop D, open G) print sounding pitch: `--tuning ...` only.
  - If a legend does not apply (e.g. it is for the other guitar), or reconcile says it cannot interpret a
    legend with certainty (several tunings, a capo without a fret, an unclear amount), read the page, pass
    the flags yourself plus `--legend-override "<what you checked>"`, and say so in the summary.
- Tempo: taken from the transcribed tempo mark(s), converted to quarter notes per minute (a dotted-quarter
  = 60 mark is 90), changes on later systems / pages written where they are printed. If no tempo is
  printed at the start it writes 90 bpm and prints a loud warning -- ask the user or read the page and
  pass `--tempo N`. A shuffle / swing marking is written as GP triplet feel from where it is printed until
  a "straight" marking.
- Stacked parts must have the same bars, with pickups of the same length; part labels that alternate
  between systems of one part (Gtr. 1, Gtr. 2, Gtr. 1) are refused as an interleave.
- Part labels decide the tracks: a system without a label continues the part above it; a label naming
  another part (another number, or another instrument word: Acous. vs Elec.) starts a track of its own on
  the common timeline -- e.g. Gtr. 1 in bars 1-6 and a Gtr. 2 fill in bars 9-10 become two tracks, each
  EMPTY (untranscribed) where it is not notated. A bare instrument name with no number ("Gtr.") is read as
  a continuation, with a warning -- check it against the page.
- Skipped systems (no guitar notation): their bars are written as untranscribed bars (empty-status beats,
  no rests, no notes, marker "Bars a-b not transcribed") in every track. If prepare could not count them,
  reconcile refuses with the exact `--gap-bars <id>:<bars>` to pass after you counted them on the page.
- A note tied over from the OTHER voice (a held bass that becomes voice 2 in the next bar) is written as
  a new attack -- GP5 cannot tie across voices -- and is listed under NOT IN THE FILE.
- `--tab-reprints-ties`: only if this book prints tied notes as plain digits in the tab (it weakens the
  tie check -- say so in the summary).
- Exit 0: agreed, written, read back exactly. Go to 5.
- Exit 1: disagreements listed (also `report.json` -> `disagreements`, `disagreement_types`,
  `recommendation`). No file exists. If it reports a CONSTANT OFFSET, fix the flags it names (tuning /
  capo / notation transpose) -- do not adjudicate or re-read. Otherwise go to 4.
- Exit 2: input/internal error (`report.json` -> `error`). Fix the cause; if a reader produced malformed
  output, re-dispatch that reader rather than hand-editing its file.

### 4. Adjudicate (default-guilty) -- repeat 3 -> 4 until clean
For each disagreement (it names system, bar, tab entry #i and staff event #j):
1. Open `bars/<sys>_bNN_tab.png` and `bars/<sys>_bNN_staff.png`; zoom on exactly the disputed note in
   BOTH with `uv run --script "${CLAUDE_SKILL_DIR}/scripts/inspect_crop.py" zoom <img> x0 y0 x1 y1 --scale 4 --out <workdir>/zoom/adj_<k>_tab.png`
   (and `..._staff.png`), and run `inspect_crop.py tab <img> --lines <tab_lines>` / `inspect_crop.py staff <img>`
   on the bar crops for measurements.
2. Assume BOTH reads may be wrong. Write down what the pixels show (digit shape, which line it sits on;
   notehead line/space and ledger count, accidental glyph, duration, tie arc, stem direction / voice)
   BEFORE deciding. Decide from the glyph, never from which answer is musically plausible or makes the
   two reads agree.
3. Record the fix as a patch in `<workdir>/adjudications.json` -- never edit `reads/*.json`:
   ```json
   {"adjudications": [
     {"target": "tab", "system": "p1_s2", "bar": 3, "op": "replace", "index": 5, "value": [3, 7],
      "kind": "glyph",
      "reason": "zoom: the digit has no closed loop and a flat top -> 7, not 9; sits on line 3",
      "evidence": ["zoom/adj_1_tab.png", "zoom/adj_1_staff.png"]}
   ]}
   ```
   ops: `replace` / `insert` / `delete` (1-based `index` into that bar's tab `notes` or staff `events`),
   `set` (`field` + `value` on a bar, or on the system when `bar` is null -- e.g. `partial`, `key_sig`,
   `omitted`), `insert_bar` / `delete_bar`. Every entry needs a concrete `reason` and a `kind`:
   `"glyph"` = a reader misread what is printed; `"interpretation"` = the page is read right but the
   file cannot hold it as printed (a bend target written as a held note, a grace note dropped, a dead
   note left out) -- add `"drops": "<what the file loses>"`. An adjudication that settles something a
   reader listed as omitted may add `"resolves_omitted": ["<text of that item>"]`. Reconcile refuses
   entries without a reason, with an unknown kind, or with out-of-range indices.
4. Bar-count or bar-boundary disagreements: look at the whole-system segments; fix the reader that
   miscounted (usually a missed/extra barline). For stacked parts, the parts must have the same bars.
5. If a glyph is genuinely illegible, do NOT guess: show the user both zoom crops and ask.
6. Re-reads: only for SCATTERED misreads in more than ~15% of bars (reconcile's `recommendation` says
   so) -- `make_prompts.py <workdir> --systems p1_s2,p1_s3` writes `*_rereadN` prompts whose output files
   supersede the earlier reads of those systems only (logged in `report.json` warnings). If the
   disagreements are SYSTEMATIC (the same kind repeating: a constant offset, grace digits in chord
   columns, bend targets) a re-read reproduces them -- fix the flags or adjudicate them as interpretations.

### 5. Verify in a real Guitar Pro engine
```
node "${CLAUDE_SKILL_DIR}/scripts/verify_alphatab.mjs" <out.gp5> --expect-bars <bars_written> --expect-notes <notes_written> --plan <workdir>/plan.json
```
This parses the file with alphaTab (independent of the library that wrote it) and compares every beat
of every track and voice, tempo changes and triplet feel with the reconciled plan. Non-zero exit = not
done; report it.

### 6. Hand over -- honest summary
Give the user the file path and, from `report.json`, in this order:
1. What is NOT in the file and changes how it sounds -- first, not buried:
   - `dropped_playback`: counts with ONE source each (`dropped_playback_sources`): technique marks from
     the TAB reader, one per printed mark (a palm-mute / let-ring line is one span); grace notes, dead
     notes and bends from the readers' structured records, counted once per event. Quote
     `dropped_playback_reader_differences` where the two readers' numbers differ.
   - `dropped_by_interpretation`: what each interpretation adjudication dropped and, where it applies,
     what the file holds instead ("-> written as a rest").
   - `other_items_not_in_file`: the readers' own notes, verbatim and NOT counted (articulations,
     dynamics, fermatas, slurs, chord names, ...). Do not turn them into numbers.
   - every untranscribed bar range (`untranscribed_bars`: a skipped system with no guitar notation -- if
     the page tells the guitar to play there, those notes are missing) and every DROPPED staff.
2. Every interpretation (`interpretations`, plus any unclassified adjudication) with its reason.
3. The numbers: bars, notes, tracks with the bars each one notates (`track_bars`, e.g. "Gtr. 2 written
   as its own track, bars 9-10"), `agreement` (tab notes cross-checked, plus
   `tied_continuations_not_in_tab`) AND `round1` (TAB digits in bars that agreed with no problem of any
   kind as read, out of every digit the TAB reader recorded, before adjudication; on a run without
   adjudications it equals the agreement) and the number of glyph adjudications. Never call
   disagreements "minor" and never report only "N/N".
4. Tuning / capo / notation transpose used and the legend they came from, stated as NOT cross-checkable.
5. Tempo and where it came from (say plainly if it is the 90 bpm placeholder), feel written.
6. Every warning in `report.json` `warnings` (prepare's warnings are repeated there with a "prepare:"
   prefix; inferred time signature, superseded reads, structure markings, legend override, tempo placeholder),
   technique marks seen and whether they were written (default: NOT written; offer a re-run with
   `--write-techniques`, which writes them as UNVERIFIED extras), options that weaken checks
   (`--tab-reprints-ties`, `--unverified`), and the alphaTab result. State what was single-read
   (rhythm, rests). Never call the file "verified" if it came from `--unverified`.

Only transcribe material the user owns or has the right to use (personal practice copies); do not
help redistribute copyrighted transcriptions.
