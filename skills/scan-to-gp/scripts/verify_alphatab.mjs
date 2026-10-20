#!/usr/bin/env node
// verify_alphatab.mjs -- load a .gp5 with alphaTab (a real Guitar Pro engine) and
// print what it parsed. Exits non-zero, loudly, if alphaTab throws or the file is empty.
//
// usage: node verify_alphatab.mjs <file.gp5> [--expect-bars N] [--expect-notes N] [--plan plan.json] [--json]
//
// --plan: compare alphaTab's own parse, beat by beat (rest, duration, dots, tuplet, string, fret,
// tie, sounding pitch), every track and both voices, bar count per track, untranscribed bars (empty
// beats only), markers, tempo and triplet feel, against the plan reconcile_build.py wrote --
// an independent check of the writer, since the read-back inside reconcile_build.py uses the same
// library that wrote the file. --expect-bars counts bars per track, --expect-notes all tracks.
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

let notes = 0;
const perTrack = [];
for (const tr of score.tracks) {
  let n = 0;
  const perBar = [];
  for (const st of tr.staves) {
    for (const bar of st.bars) {
      let nb = 0;
      for (const v of bar.voices) for (const b of v.beats) if (!b.isEmpty) nb += b.notes.length;
      n += nb;
      perBar.push(nb);
    }
  }
  notes += n;
  perTrack.push({ name: tr.name, strings: tr.staves[0].tuning.length, notes: n, perBar: perBar.join(",") });
}
const bars = score.tracks.length ? score.tracks[0].staves[0].bars.length : 0;
const summary = {
  version: at.meta ? at.meta.version : undefined,
  title: score.title,
  tempo: score.tempo,
  masterBars: score.masterBars.length,
  tracks: score.tracks.length,
  markers: score.masterBars.map((m, i) => (m.section ? `${i + 1}:${m.section.text}` : null)).filter(Boolean),
  timeSigs: score.masterBars.map((m) => `${m.timeSignatureNumerator}/${m.timeSignatureDenominator}`).join(" "),
  bars,
  notes,
  perTrack,
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
  console.error(`ALPHATAB: expected ${en} notes (all tracks), alphaTab sees ${notes}`);
  bad = true;
}
const planPath = optStr("--plan");
if (planPath) {
  const plan = JSON.parse(readFileSync(planPath, "utf8"));
  const tracks = [{ name: plan.name || "track 1", strings: plan.strings, bars: plan.bars }, ...(plan.more_tracks || [])];
  const diffs = [];
  if (tracks.length !== score.tracks.length) diffs.push(`plan has ${tracks.length} tracks, alphaTab ${score.tracks.length}`);
  tracks.forEach((pt, ti) => {
    const tr = score.tracks[ti];
    if (!tr) return;
    const st = tr.staves[0];
    const nStr = st.tuning.length;
    const capo = st.capo || 0;
    if (pt.bars.length !== st.bars.length) diffs.push(`track ${ti + 1}: plan has ${pt.bars.length} bars, alphaTab ${st.bars.length}`);
    pt.bars.forEach((pb, i) => {
      const bar = st.bars[i];
      if (!bar) return;
      const mb = score.masterBars[i];
      const where = `${tracks.length > 1 ? `track ${ti + 1} ` : ""}bar ${i + 1} (${pb.system} bar ${pb.bar})`;
      if (mb.timeSignatureNumerator !== pb.ts[0] || mb.timeSignatureDenominator !== pb.ts[1])
        diffs.push(`${where}: time signature ${mb.timeSignatureNumerator}/${mb.timeSignatureDenominator} != ${pb.ts.join("/")}`);
      if (pb.untranscribed) {
        // a bar this track does not notate (a skipped system, or another part's bars): it must exist and hold
        // nothing -- every beat of every voice empty, no rest and no note
        const stray = bar.voices.flatMap((v) => v.beats).filter((b) => !b.isEmpty || b.notes.length);
        if (!bar.voices.length || !bar.voices[0].beats.length) diffs.push(`${where}: untranscribed bar has no beat at all`);
        if (stray.length) diffs.push(`${where}: untranscribed bar holds ${stray.length} non-empty beat(s)`);
        return;
      }
      [["beats", 0], ["voice2", 1]].forEach(([key, vi]) => {
        const wantBeats = pb[key] || [];
        const voice = bar.voices[vi];
        const beats = voice ? voice.beats.filter((b) => !b.isEmpty) : [];
        const vw = `${where}${vi ? " voice 2" : ""}`;
        if (beats.length !== wantBeats.length) {
          diffs.push(`${vw}: ${beats.length} beats in alphaTab, plan has ${wantBeats.length}`);
          return;
        }
        beats.forEach((b, j) => {
          const want = wantBeats[j];
          const w = `${vw} beat ${j + 1}`;
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
    });
  });
  // every voice of every (verified) bar fills its time signature exactly
  tracks.forEach((pt, ti) => {
    const tr = score.tracks[ti];
    if (!tr) return;
    pt.bars.forEach((pb, i) => {
      const bar = tr.staves[0].bars[i];
      const mb = score.masterBars[i];
      if (!bar || !mb || pb.unverified) return;
      const want = mb.calculateDuration();
      bar.voices.forEach((v, vi) => {
        const beats = v.beats.filter((b) => !b.isEmpty);
        if (!beats.length) return;
        const got = beats.reduce((acc, b) => acc + b.displayDuration, 0);
        if (got !== want)
          diffs.push(`${tracks.length > 1 ? `track ${ti + 1} ` : ""}bar ${i + 1} voice ${vi + 1}: beats fill ${got} ticks, the bar needs ${want}`);
      });
    });
  });
  // tempo: the initial tempo and exactly the planned changes, nothing else
  if (plan.tempo !== undefined && score.tempo !== plan.tempo) diffs.push(`tempo ${score.tempo} != ${plan.tempo}`);
  if (plan.tempo !== undefined) {
    const changes = plan.tempo_changes || {};
    score.masterBars.forEach((mb, i) => {
      const ta = (mb.tempoAutomations || (mb.tempoAutomation ? [mb.tempoAutomation] : [])).map((a) => a.value);
      const want = i === 0 ? [plan.tempo] : changes[String(i + 1)] !== undefined ? [changes[String(i + 1)]] : [];
      const got = [...new Set(ta)];
      if (JSON.stringify(got) !== JSON.stringify(want)) diffs.push(`bar ${i + 1}: tempo automations ${got} != ${want}`);
    });
  }
  if (plan.markers) {
    // section markers and the "not transcribed" notes on untranscribed bars, exactly
    const got = {};
    score.masterBars.forEach((mb, i) => {
      if (mb.section) got[String(i + 1)] = mb.section.text;
    });
    if (JSON.stringify(got) !== JSON.stringify(plan.markers))
      diffs.push(`markers ${JSON.stringify(got)} != ${JSON.stringify(plan.markers)}`);
  }
  if (plan.feel) {
    const code = { eighth: 2, sixteenth: 1 };
    plan.feel.forEach((f, i) => {
      const mb = score.masterBars[i];
      const want = f ? code[f] : 0;
      if (mb && mb.tripletFeel !== want) diffs.push(`bar ${i + 1}: triplet feel ${mb.tripletFeel} != ${want} (${f || "straight"})`);
    });
  }
  if (diffs.length) {
    console.error(`ALPHATAB vs PLAN: ${diffs.length} difference(s):\n  ` + diffs.slice(0, 40).join("\n  "));
    bad = true;
  } else {
    const empty = tracks.reduce((n, t) => n + t.bars.filter((pb) => pb.untranscribed).length, 0);
    console.error(`alphaTab parse matches the plan beat for beat (${tracks.length} track(s), ${tracks[0].bars.length} bars` +
      (empty ? `, ${empty} untranscribed track-bar(s) empty as planned` : "") + ")");
  }
}
process.exit(bad ? 1 : 0);
