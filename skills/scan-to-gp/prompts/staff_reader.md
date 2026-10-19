You are transcribing STANDARD NOTATION (the 5-line staff) for guitar/bass from scanned
pages. Your read will be checked note-by-note against an independent reading of the
tablature by another agent; any mismatch is investigated, so a careless read wastes
everyone's time and a confident wrong read is the worst outcome. Accuracy is everything.

Work DEFAULT-GUILTY: assume your first read of every bar is wrong until a second,
independent pass confirms it. Never fill a bar from a pattern, a previous bar, scale
theory or what "should" come next -- read every notehead from the pixels.

## Inputs (work dir: {{WORKDIR}})

Systems to read, in order: {{SYSTEMS}}

For each system id `<sys>` (manifest: `{{WORKDIR}}/manifest.json`, entry `systems[]`):
- `crops/<sys>_staff.png` -- the whole notation staff of that system, with the space above it
  (headings, tempo, tuplet brackets). It gets downscaled when you view it; use it for layout.
- `crops/<sys>_staff_seg1.png`, `_seg2.png`, ... -- overlapping segments at native resolution
  (x ranges in `segment_x_ranges`; consecutive segments overlap -- do not double count)
- `bars/<sys>_bNN_staff.png` -- one crop per DETECTED bar (upscaled; `bar_crop_scale`). Bar 1 of each
  system also shows the clef, key signature and any time signature. Bar boundaries come from automatic
  barline detection: confirm them against the segments; if detection merged or split bars, follow the
  printed barlines and say so.

**Independence rule: open ONLY staff images** (`*_staff*.png`). Do NOT open `*_tab*.png`, `*_full.png`,
the contact sheets, or any other agent's output. The tablature was cut off on purpose.

## Tools

- Zoom: `uv run --script "{{SKILL_DIR}}/scripts/inspect_crop.py" zoom "<img>" x0 y0 x1 y1 --scale 3 --out "{{WORKDIR}}/zoom/staff_<name>.png"`
  then Read the output. Write zoom files ONLY into `{{WORKDIR}}/zoom/`, names starting with `staff_`.
- Measure: `uv run --script "{{SKILL_DIR}}/scripts/inspect_crop.py" staff "<staff image>" --clef treble [--x0 X --x1 X] [--out "{{WORKDIR}}/zoom/staff_<name>_ann.png"]`
  finds the 5 lines and notehead candidates and prints each one's staff step (0 = bottom line, odd =
  space, negative = below, with ledger-line counts) and its written letter+octave. Evidence, not truth:
  accidentals or other symbols can show up as false noteheads, chords can merge, hollow heads can be
  missed. When it disagrees with your eyes, zoom and decide.

## What to record

Per system: `clef` ("treble", "treble_8vb" = treble with a small 8 below, "bass"), `key_sig` exactly as
printed at the start of that system ("3 sharps", "2 flats", "none"), `section` if a heading/label is
printed just above the system (e.g. "Example 7", "Verse"), `tempo` (number) if a metronome mark is printed.

Per bar: `time_sig` if one is printed at the start of THAT bar ("4/4", "3/4", "6/8", "C", "cut");
`key_sig` if a key change is printed inside the system at that bar; `partial: true` if the bar is
genuinely shorter than the time signature (a pickup before the first full bar, or a short final bar) --
NOT as a way to make a bar you could not add up "fit"; the tab reader judges this independently and a
mismatch is checked; `repeat_start: true` for `|:`; `repeat_end: 2` for `:|` (or the printed count);
`end_barline: "double"` or `"final"` for a double / thin-thick barline at the end of the bar;
`multirest: N` (with `"events": []`) for a multi-bar rest marked with a number N.
Number bars within each system from 1.

Events, left to right:
- note: `{"letter": "A", "octave": 3, "acc": "none", "dur": "8", "dots": 0, "tuplet": 3, "tie": false}`
- chord (noteheads on one stem): `{"chord": [{"letter": "A", "octave": 3, "acc": "none"}, {"letter": "E", "octave": 4, "acc": "flat"}], "dur": "4"}`
- rest: `{"rest": true, "dur": "2"}`
- In a chord, `tie` goes on each tied chord note (`{"letter": "E", "octave": 4, "acc": "none", "tie": true}`).
- A notehead printed in parentheses (ghost note): add `"ghost": true` to that note.

Pitch = the WRITTEN note, named as printed (do not transpose -- the script knows guitar sounds an
octave lower):
- Treble clef (and treble_8vb, named the same way): lines bottom->top E4 G4 B4 D5 F5; spaces F4 A4 C5 E5.
  Below: D4 (space under the staff), C4 (1st ledger line), B3, A3 (2nd ledger), G3, F3 (3rd ledger), E3.
  Above: G5 (space over the staff), A5 (1st ledger), B5, C6 (2nd ledger), D6, E6 (3rd ledger).
- Bass clef: lines G2 B2 D3 F3 A3; spaces A2 C3 E3 G3; below F2, E2 (1st ledger); above B3, C4 (1st ledger).
- COUNT ledger lines one by one on a zoom for every note outside the staff.

`acc` = ONLY the accidental glyph printed immediately left of THIS notehead: "sharp", "flat", "natural",
"double-sharp", "double-flat", or "none". Do NOT apply the key signature and do NOT carry accidentals
through the bar -- the script does both. A note with no glyph is "none" even if you know it is sharp.

`dur`: "1" whole, "2" half (hollow + stem), "4" quarter (filled, no flag/beam), "8" (1 flag or beam),
"16" (2), "32" (3). `dots`: augmentation dots to the RIGHT of the notehead (not staccato dots
above/below). `tuplet`: the number printed over a bracket or beam group ("3", "5", "6") -- put it on
every note and rest inside the group; omit otherwise. `tie: true` when a tie arc ENDS on this note
(it connects from the previous note of the SAME pitch); an arc between DIFFERENT pitches is a slur --
not a tie; list slurs under `omitted` as "slur bar N notes i-j".

Rests: whole rest hangs below the 4th line (a lone whole rest = full-bar rest in any metre, report it
as `{"rest": true, "dur": "1"}`); half rest sits on the middle line; quarter = vertical squiggle;
eighth = "7"-like with one flag; sixteenth = two flags.

Grace notes (small, often slashed), cue notes, a second voice (stems in both directions with
independent rhythm), 8va/8vb lines, voltas, D.S./Coda: do NOT invent a representation -- list them in
the system's `omitted` array with bar and position.

## Procedure

1. Pass 1: read every bar from its bar crop: noteheads (line vs space, ledger count), accidentals,
   durations, dots, tuplets, ties, rests.
2. Run the `staff` measurement on every bar crop; reconcile every difference with pass 1 by zooming.
3. Check that every full bar's durations add up to the time signature (quarter = 1 beat, eighth = 1/2,
   triplet eighth = 1/3, dotted quarter = 3/2, sixteenth = 1/4). A bar that does not add up is a misread
   somewhere in that bar -- find it. (If no time signature is printed anywhere, still check that all full
   bars have the same length.)
4. Pass 2: re-read every bar independently from the segment images without looking at pass 1; compare;
   zoom on every difference. Double-check line vs space, ledger-line counts, small accidentals and
   which notehead they belong to, dots, and beam counts.
5. Write the JSON below to `{{OUT}}` (create/overwrite that file only).

```json
{
  "reader": "staff",
  "systems": {
    "p1_s1": {
      "clef": "treble", "key_sig": "3 sharps", "section": "Example 6",
      "bars": [
        {"bar": 1, "partial": true, "events": [{"letter": "A", "octave": 3, "acc": "none", "dur": "8"}]},
        {"bar": 2, "time_sig": "4/4", "events": [
          {"letter": "C", "octave": 4, "acc": "natural", "dur": "8", "tuplet": 3},
          {"rest": true, "dur": "4"}]}
      ],
      "omitted": []
    }
  }
}
```

## Reply (short)

Per system: number of bars and pitched notes, and the beat total of every bar. Then EVERY uncertain
spot: system, bar, event, the candidates, your pick, and why (what the pixels showed). List omitted
items. Do not say "all clear" unless every bar survived pass 2 unchanged; if it did, say how many bars
changed between passes.
