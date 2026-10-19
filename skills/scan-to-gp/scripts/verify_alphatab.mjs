#!/usr/bin/env node
// verify_alphatab.mjs -- load a .gp5 with alphaTab (a real Guitar Pro engine) and
// print what it parsed. Exits non-zero, loudly, if alphaTab throws or the file is empty.
//
// usage: node verify_alphatab.mjs <file.gp5> [--expect-bars N] [--expect-notes N] [--plan plan.json] [--json]
//
// --plan: compare alphaTab's own parse, beat by beat (rest, duration, dots, tuplet, string, fret,
// tie, sounding pitch), against the plan reconcile_build.py wrote -- an independent check of the
// writer, since the read-back inside reconcile_build.py uses the same library that wrote the file.
//
// alphaTab is resolved from this skill's own node_modules (pinned in ../package.json).
// Install once with:  npm ci --prefix <skill dir>

import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const valued = new Set(["--expect-bars", "--expect-notes", "--plan"]);
const positional = args.filter((a, i) => !a.startsWith("--") && !valued.has(args[i - 1]));
const file = positional[0];
const optStr = (name) => {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : null;
};
const opt = (name) => (optStr(name) === null ? null : Number(optStr(name)));
if (!file) {
  console.error("usage: node verify_alphatab.mjs <file.gp5> [--expect-bars N] [--expect-notes N] [--plan plan.json] [--json]");
  process.exit(2);
}

let at;
try {
  const require = createRequire(join(here, "..", "package.json"));
  const entry = require.resolve("@coderline/alphatab").replace(/alphaTab\.js$/, "alphaTab.mjs");
  at = await import(pathToFileURL(entry).href);
} catch (e) {
  console.error(`alphaTab is not installed for this skill. Run:  npm ci --prefix "${join(here, "..")}"\n(${e.message})`);
  process.exit(3);
}

let score;
try {
  const bytes = new Uint8Array(readFileSync(file));
  score = at.importer.ScoreLoader.loadScoreFromBytes(bytes, new at.Settings());
} catch (e) {
  console.error(`ALPHATAB FAILED to load ${file}: ${e && e.stack ? e.stack : e}`);
  process.exit(1);
}

const tr = score.tracks[0];
let notes = 0;
let bars = 0;
const perBar = [];
for (const st of tr.staves) {
  for (const bar of st.bars) {
    bars++;
    let n = 0;
    for (const v of bar.voices) for (const b of v.beats) if (!b.isEmpty) n += b.notes.length;
    notes += n;
    perBar.push(n);
  }
}
const summary = {
  version: at.meta ? at.meta.version : undefined,
  title: score.title,
  tempo: score.tempo,
  masterBars: score.masterBars.length,
  tracks: score.tracks.length,
  strings: tr.staves[0].tuning.length,
  markers: score.masterBars.map((m, i) => (m.section ? `${i + 1}:${m.section.text}` : null)).filter(Boolean),
  timeSigs: score.masterBars.map((m) => `${m.timeSignatureNumerator}/${m.timeSignatureDenominator}`).join(" "),
  bars,
  notes,
  perBar: perBar.join(","),
};
console.log(JSON.stringify(summary, null, args.includes("--json") ? 2 : 0));

let bad = false;
if (bars === 0 || notes === 0) {
  console.error("ALPHATAB: the file loaded but contains no bars/notes");
  bad = true;
}
const eb = opt("--expect-bars");
const en = opt("--expect-notes");
if (eb !== null && eb !== bars) {
  console.error(`ALPHATAB: expected ${eb} bars, alphaTab sees ${bars}`);
  bad = true;
}
if (en !== null && en !== notes) {
  console.error(`ALPHATAB: expected ${en} notes, alphaTab sees ${notes}`);
  bad = true;
}
const planPath = optStr("--plan");
if (planPath) {
  const plan = JSON.parse(readFileSync(planPath, "utf8"));
  const st = tr.staves[0];
  const nStr = st.tuning.length;
  const capo = st.capo || 0;
  const diffs = [];
  if (plan.bars.length !== st.bars.length) diffs.push(`plan has ${plan.bars.length} bars, alphaTab ${st.bars.length}`);
  plan.bars.forEach((pb, i) => {
    const bar = st.bars[i];
    if (!bar) return;
    const mb = score.masterBars[i];
    const where = `bar ${i + 1} (${pb.system} bar ${pb.bar})`;
    if (mb.timeSignatureNumerator !== pb.ts[0] || mb.timeSignatureDenominator !== pb.ts[1])
      diffs.push(`${where}: time signature ${mb.timeSignatureNumerator}/${mb.timeSignatureDenominator} != ${pb.ts.join("/")}`);
    const beats = bar.voices[0].beats.filter((b) => !b.isEmpty);
    if (beats.length !== pb.beats.length) {
      diffs.push(`${where}: ${beats.length} beats in alphaTab, plan has ${pb.beats.length}`);
      return;
    }
    beats.forEach((b, j) => {
      const want = pb.beats[j];
      const w = `${where} beat ${j + 1}`;
      if (b.isRest !== want.rest) diffs.push(`${w}: rest ${b.isRest} != ${want.rest}`);
      if (Number(b.duration) !== want.dur) diffs.push(`${w}: duration ${b.duration} != ${want.dur}`);
      if (b.dots !== want.dots) diffs.push(`${w}: dots ${b.dots} != ${want.dots}`);
      const tup = b.hasTuplet ? [b.tupletNumerator, b.tupletDenominator] : null;
      if (JSON.stringify(tup) !== JSON.stringify(want.tuplet)) diffs.push(`${w}: tuplet ${tup} != ${want.tuplet}`);
      const got = b.notes
        .map((n) => ({
          string: nStr - n.string + 1,
          fret: n.fret,
          tie: n.isTieDestination,
          midi: st.tuning[nStr - n.string] + n.fret + capo,
        }))
        .sort((x, y) => x.midi - y.midi);
      if (got.length !== want.notes.length) {
        diffs.push(`${w}: ${got.length} notes != ${want.notes.length}`);
        return;
      }
      got.forEach((g, k) => {
        const e = want.notes[k];
        if (g.midi !== e.midi) diffs.push(`${w}: pitch ${g.midi} != ${e.midi}`);
        if (g.tie !== e.tie) diffs.push(`${w}: tie ${g.tie} != ${e.tie}`);
        if (e.string !== null && (g.string !== e.string || g.fret !== e.fret))
          diffs.push(`${w}: string/fret ${g.string}/${g.fret} != ${e.string}/${e.fret}`);
      });
    });
  });
  if (diffs.length) {
    console.error(`ALPHATAB vs PLAN: ${diffs.length} difference(s):\n  ` + diffs.slice(0, 40).join("\n  "));
    bad = true;
  } else {
    console.error(`alphaTab parse matches the plan beat for beat (${plan.bars.length} bars)`);
  }
}
process.exit(bad ? 1 : 0);
