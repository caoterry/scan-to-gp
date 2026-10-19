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

- Needs BOTH a notation staff and a tab staff in every system -- that pairing is what makes the result
  verifiable. Tab-only pages are refused unless the user explicitly accepts an UNVERIFIED file
  (`--unverified`; output name contains `UNVERIFIED`).
- Cross-checked by both reads: the pitch of every note (string+fret vs written note), chords, ties
  (staff tie <-> tab digit omitted or in parentheses), ghost notes, bar count, pickup/short bars,
  multi-bar rests, repeat signs.
- Read once, checked only by arithmetic: rhythm values and rest positions (staff reader; every bar must
  add up), key and time signatures (a time signature that gives the same bar length -- 6/8 vs 3/4, C vs
  cut -- is single-read).
- NOT checkable in v1 (reported, never silently dropped): techniques (h/p/slides/bends/vibrato/palm
  mute/harmonics), grace notes, dead notes, second voices, voltas/D.S./Coda, 8va lines, dynamics,
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
uv run --script "${CLAUDE_SKILL_DIR}/scripts/prepare.py" <input.pdf|.png|.jpg ...> <workdir>
```
- Exit 3 = refused (tab-only / staff-only / unrecognised staves): report what was found; offer
  `--unverified` only for tab-only pages, and say clearly what that gives up.
- Exit 2 = nothing found: say so; ask for a better scan.
- Always Read `<workdir>/contact/systems.png` (and `contact/pN.png` if anything looks off) and check:
  every system found (compare with the page), the magenta split line runs between staff and tab without
  cutting glyphs, the orange barlines are on real barlines. Relay every WARNING line to the user. If a
  system is missing or mis-split, stop and tell the user -- do not transcribe a partial page as if whole.
- Ask the user (or read from the page) the tuning, capo, and whether the book is "tuned down" with
  notation printed as if in standard tuning -- these change step 3's options.

### 2. Dispatch the readers (in parallel, independent)
```
uv run --script "${CLAUDE_SKILL_DIR}/scripts/make_prompts.py" <workdir>        # batches of <= 8 systems
```
(add `--unverified` for tab-only pages). For EVERY batch, launch one TAB reader and one STAFF reader
with the Agent tool -- all of them in a single message so they run concurrently (general-purpose
agents; they need Read, Bash, Write). Prompt each with exactly:
`Read <prompt file> and follow it exactly. Your work dir is <workdir>.`
Never paste one reader's output into another reader's prompt. Keep their replies: the uncertain spots
they list are the first places to look if reconcile disagrees.

### 3. Reconcile + build
```
uv run --script "${CLAUDE_SKILL_DIR}/scripts/reconcile_build.py" <workdir> <out.gp5> [--tempo N] [--title T] \
    [--tuning E2,A2,D3,G3,B3,E4] [--capo N] [--notation-transpose N] [--tab-reprints-ties]
```
- Tuning is low -> high; default by tab line count (6: standard, 7: B1 standard, 4: E1 bass).
  `--capo N`: tab frets are relative to the capo. `--notation-transpose -1`: "tune down 1/2 step" books
  whose notation is printed as if in standard tuning. `--tab-reprints-ties`: only if this book prints
  tied notes as plain digits in the tab (it weakens the tie check -- say so in the summary).
- Exit 0: agreed, written, read back exactly. Go to 5.
- Exit 1: disagreements listed (also `report.json` -> `disagreements`). No file exists. Go to 4.
- Exit 2: input/internal error (`report.json` -> `error`). Fix the cause; if a reader produced malformed
  output, re-dispatch that reader rather than hand-editing its file.

### 4. Adjudicate (default-guilty) -- repeat 3 -> 4 until clean
For each disagreement (it names system, bar, tab entry #i and staff event #j):
1. Open `bars/<sys>_bNN_tab.png` and `bars/<sys>_bNN_staff.png`; zoom on exactly the disputed note in
   BOTH with `uv run --script "${CLAUDE_SKILL_DIR}/scripts/inspect_crop.py" zoom <img> x0 y0 x1 y1 --scale 4 --out <workdir>/zoom/adj_<k>_tab.png`
   (and `..._staff.png`), and run `inspect_crop.py tab <img> --lines <tab_lines>` / `inspect_crop.py staff <img>`
   on the bar crops for measurements.
2. Assume BOTH reads may be wrong. Write down what the pixels show (digit shape, which line it sits on;
   notehead line/space and ledger count, accidental glyph, duration, tie arc) BEFORE deciding. Decide from
   the glyph, never from which answer is musically plausible or makes the two reads agree.
3. Record the fix as a patch in `<workdir>/adjudications.json` -- never edit `reads/*.json`:
   ```json
   {"adjudications": [
     {"target": "tab", "system": "p1_s2", "bar": 3, "op": "replace", "index": 5, "value": [3, 7],
      "reason": "zoom: the digit has no closed loop and a flat top -> 7, not 9; sits on line 3",
      "evidence": ["zoom/adj_1_tab.png", "zoom/adj_1_staff.png"]}
   ]}
   ```
   ops: `replace` / `insert` / `delete` (1-based `index` into that bar's tab `notes` or staff `events`),
   `set` (`field` + `value` on a bar, or on the system when `bar` is null -- e.g. `partial`, `key_sig`),
   `insert_bar` / `delete_bar`. Every entry needs a concrete `reason`; reconcile refuses entries without
   one and refuses out-of-range indices.
4. Bar-count or bar-boundary disagreements: look at the whole-system segments; fix the reader that
   miscounted (usually a missed/extra barline).
5. If a glyph is genuinely illegible, do NOT guess: show the user both zoom crops and ask.
6. If more than ~15% of bars disagree, the reads are unreliable: re-read the affected systems with fresh
   readers -- `make_prompts.py <workdir> --systems p1_s2,p1_s3` writes `*_rereadN` prompts whose output
   files supersede the earlier reads of those systems only (logged in `report.json` warnings) -- then
   reconcile again.

### 5. Verify in a real Guitar Pro engine
```
node "${CLAUDE_SKILL_DIR}/scripts/verify_alphatab.mjs" <out.gp5> --expect-bars <bars_written> --expect-notes <notes_written> --plan <workdir>/plan.json
```
This parses the file with alphaTab (independent of the library that wrote it) and compares every beat
with the reconciled plan. Non-zero exit = not done; report it.

### 6. Hand over -- honest summary
Give the user the file path and, from `report.json`: bars, notes, `agreement` (tab notes cross-checked,
plus `tied_continuations_not_in_tab`), number of adjudications with a one-line reason each, every
warning (inferred time signature, superseded reads, omitted structure markings), technique marks seen in
the tab and whether they were written (default: NOT written; offer a re-run with `--write-techniques`,
which writes them as UNVERIFIED extras), everything the readers listed as omitted, options that weaken
checks (`--tab-reprints-ties`, `--unverified`), and the alphaTab result. State what was single-read
(rhythm, rests). Never call the file "verified" if it came from `--unverified`, and never summarise
disagreements as "minor" -- report the numbers.

Only transcribe material the user owns or has the right to use (personal practice copies); do not
help redistribute copyrighted transcriptions.
