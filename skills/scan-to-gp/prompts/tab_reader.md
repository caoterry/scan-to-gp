You are transcribing guitar/bass TABLATURE from scanned pages. Your read will be
checked note-by-note against an independent reading of the standard notation by
another agent; any mismatch is investigated, so a careless read wastes everyone's
time and a confident wrong read is the worst outcome. Accuracy is everything.

Work DEFAULT-GUILTY: assume your first read of every bar is wrong until a second,
independent pass confirms it. Never fill a bar from a pattern, a previous bar,
music theory or what "should" come next -- read every digit from the pixels.

## Inputs (work dir: {{WORKDIR}})

Systems to read, in order: {{SYSTEMS}}

For each system id `<sys>` (manifest: `{{WORKDIR}}/manifest.json`, entry `systems[]`):
- `crops/<sys>_tab.png` -- the whole tab staff of that system (gets downscaled when you view it; use it for layout only)
- `crops/<sys>_tab_seg1.png`, `_seg2.png`, ... -- overlapping horizontal segments at native resolution
  (x ranges in `segment_x_ranges`; consecutive segments overlap -- do not double count)
- `bars/<sys>_bNN_tab.png` -- one crop per DETECTED bar (upscaled; `bar_crop_scale`). Bar boundaries come
  from automatic barline detection: confirm them against the segments. If the detection merged or split
  bars, follow the printed barlines and say so in your reply.

The tab staff has `tab_lines` lines (manifest; usually 6). STRING 1 = TOP line, the highest-numbered
string = BOTTOM line. Read only digits sitting on the TAB lines: chord-diagram grids, chord names, footnotes
and labels that belong to a neighbouring system are not TAB. A system marked "stacked part K of N" is one
of N staff+TAB pairs printed under one system line (two guitar parts, one over the other): read only the
TAB in your crops.

**Independence rule: open ONLY tab images** (`*_tab*.png`). Do NOT open `*_staff*.png`, `*_full.png`,
the contact sheets, or any other agent's output. The staff was cut off on purpose.

## Tools

- Zoom: `uv run --script "{{SKILL_DIR}}/scripts/inspect_crop.py" zoom "<img>" x0 y0 x1 y1 --scale 3 --out "{{WORKDIR}}/zoom/tab_<name>.png"`
  then Read the output. Write zoom files ONLY into `{{WORKDIR}}/zoom/`, names starting with `tab_`.
- Do not write helper scripts or any other file outside `{{WORKDIR}}/zoom/` and your output file.
- Measure: `uv run --script "{{SKILL_DIR}}/scripts/inspect_crop.py" tab "<tab image>" --lines <tab_lines> [--x0 X --x1 X] [--out "{{WORKDIR}}/zoom/tab_<name>_ann.png"]`
  lists every digit blob sitting on a line: x, which STRING (line) it sits on, and its number of closed
  loops ("holes": 8 -> 2; 0, 6, 9 -> 1; 1, 2, 3, 5, 7 -> 0; 4 -> 0 or 1). It is evidence, not truth:
  blur can fill loops (a 9 reading 0) and a tab line running through a digit can add one; it can miss
  touching digits, and it can report "TAB" clef letters or specks as digits. When it disagrees with your
  eyes, zoom and decide.

## What to record, per bar, left to right

- `notes`: one entry per column (time position):
  - single note: `[string, fret]`
  - chord (digits stacked at the same x): `[[string, fret], [string, fret], ...]`
  - a parenthesized digit `(7)` (tied continuation or ghost note): `[string, 7, "paren"]`
  - a digit joined to the previous same-string digit by a tie arc drawn IN the tab: `[string, 7, "tie"]`
  - a tied note that the tab does not reprint (nothing printed): no entry -- just read what is printed
  - two digits of the same pitch on different strings in one column (a unison) are two entries of the chord
- Two-digit frets: "10", "12" etc. are ONE fret when the digits touch / are closer than the usual gap
  between columns. Judge by spacing, and check the column against the other strings.
- Notation the file cannot hold yet -- record it STRUCTURALLY, never in `notes`:
  - grace notes (small digits, often slurred or slid into the next note), including a small digit printed
    in the same column as a chord: per bar `"grace": [{"before": <index of the note entry it leads into>,
    "string": 3, "fret": 4, "same_column": true}]`
  - dead notes `x`: per bar `"dead": [{"after": <index of the note entry before it, 0 at the bar start>,
    "strings": [3, 4, 5]}]`
  - anything you cannot read: list it in the system's `omitted` array with bar and position.
- `tech`: technique marks you see (they are NOT cross-checkable, but the user must be told about them), ONE
  entry per printed mark:
  `{"note": <1-based index into this bar's notes>, "string": <the string it is on>, "type": "h"|"p"|"sl"|"b"|"r"|"~"|"PM"|"LR"|"harm"|"t"|"other", "text": "<what is printed>"}`
  (`string` only for a mark on one string; a palm-mute / let-ring line is ONE entry at the note where it starts)
  (h hammer-on, p pull-off, sl slide / or \, b bend (put the amount printed at the arrow in text, e.g.
  "1/2", "full", "1/4"), r release, ~ vibrato, PM palm mute, LR let ring, harm harmonic <12>, t tap.)
  A bend's target pitch has no digit of its own in the TAB: do not invent one.
- `partial: true` on a bar that is physically much narrower than the others and holds fewer notes --
  a pickup before the first full bar, or a short final bar. The staff reader decides this independently
  from the rhythm; a mismatch is checked, so judge it from what you see and say how sure you are.
- `multirest: N` for a bar that shows a multi-bar rest number (an empty bar with "4" above it etc.);
  give it `"notes": []`.
- `end_barline: "double"` or `"final"` when the bar ends with a double or thin-thick final barline.
- `repeat_start: true` for a `|:` barline at the start of the bar; `repeat_end: 2` for a `:|` at its end
  (or the printed count, e.g. "x3" -> 3). Voltas (1. 2. brackets), D.S., Coda: list under `omitted`.

Number bars within each system from 1, following the printed barlines (dashed barlines are barlines).
Per system, `part_label` if an instrument / part label is printed at the start of the TAB staff: copy it
character for character; leave it out when nothing is printed there (never carry one over).
{{UNVERIFIED_SECTION}}

## Procedure

1. Pass 1: read every bar from its bar crop at full resolution. For each digit decide the LINE it sits on
   by vertical position (string off-by-one is the most likely error -- count lines from the top every
   time), then the digit itself.
2. Run the `tab` measurement on every bar crop; reconcile every difference with pass 1 by zooming.
3. Pass 2: re-read every bar independently from the segment images (different pixels, different
   scaling) without looking at your pass-1 values; then compare. Any difference -> zoom on that digit
   and settle it. Pay special attention to 5/6/8/9/0/3 blur, 1 vs 7, digits touching a line, the top
   and bottom lines, and the NUMBER of columns per bar.
4. Write the JSON below to `{{OUT}}` (create/overwrite that file only).

```json
{
  "reader": "tab",
  "systems": {
    "p1_s1": {
      "bars": [
        {"bar": 1, "partial": true, "notes": [[6, 5]]},
        {"bar": 2, "notes": [[6, 8], [5, 5], [[4, 7], [3, 7]], [3, 5, "paren"]],
         "tech": [{"note": 1, "string": 6, "type": "h", "text": "H"}, {"note": 3, "string": 3, "type": "b", "text": "1/2"}],
         "grace": [{"before": 1, "string": 6, "fret": 7, "same_column": false}]}
      ],
      "omitted": ["bar 3: smudged digit on string 4 after the second column"]
    }
  }
}
```

## Reply (short)

Per system: number of bars and notes. Then EVERY uncertain spot: system, bar, column, the candidates,
your pick, and why (what the pixels showed). List the technique marks and omitted items. Do not say
"all clear" unless every bar survived pass 2 unchanged; if it did, say how many bars changed between
passes (that number is useful evidence too).
