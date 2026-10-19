#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy>=1.24", "scipy>=1.10", "pillow>=10.0"]
# ///
"""inspect_crop.py -- measuring aids for the reader agents. Evidence, not answers.

  zoom  <img> <x0> <y0> <x1> <y1> --out <png> [--scale 3]
        Crop a region (source pixel coords) and upscale it for a closer look.

  tab   <tab_crop.png> [--lines 6] [--x0 X --x1 X] [--out annotated.png]
        Finds the tab lines in the crop, then every ink blob sitting on a line
        (fret digits). For each blob: x, y, which STRING the blob sits on
        (1 = top line), its size, and its number of closed loops (holes) --
        0 typically has 1, 6 and 9 have 1, 8 has 2, 1/2/3/5/7 have 0, 4 has 0-1
        (unreliable when blur fills a loop or a tab line runs through the digit).
        Blobs closer than a digit-gap are merged (two-digit frets like 12).

  staff <staff_crop.png> [--clef treble|bass] [--x0 X --x1 X] [--out annotated.png]
        Finds the 5 staff lines and solid/hollow notehead candidates; reports for
        each: x, y, staff step (0 = bottom line, 1 = first space, -1 = space
        below, ...) and the WRITTEN letter+octave for the clef (no key signature,
        no accidentals applied). Beams/stems are suppressed but not perfectly:
        verify every candidate by eye.

All coordinates are pixels of the image you pass in.
"""

from __future__ import annotations

import os
import sys

if __name__ == "__main__" and not sys.flags.isolated:
    os.execv(sys.executable, [sys.executable, "-I", *sys.argv])
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import argparse  # noqa: E402

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from scipy import ndimage as ndi  # noqa: E402

import stg_image as si  # noqa: E402

TREBLE_STEP0 = ("E", 4)  # bottom line
BASS_STEP0 = ("G", 2)
LETTERS = "CDEFGAB"


def step_to_pitch(step: int, clef: str) -> str:
    letter, octv = TREBLE_STEP0 if clef == "treble" else BASS_STEP0
    idx = LETTERS.index(letter) + octv * 7 + step
    return f"{LETTERS[idx % 7]}{idx // 7}"


def find_lines_in_crop(ink: np.ndarray, want: int) -> list[si.Line]:
    d, t = si.estimate_spacing(ink)
    for dd in (d, d * 0.6, d * 1.6):
        lines = si.detect_lines(ink, dd, t, min_len=0.5 * ink.shape[1])  # crops can be one short bar
        staves = si.group_staves(lines, dd)
        cand = [s for s in staves if len(s.lines) == want] or [s for s in staves if len(s.lines) >= 4]
        if cand:
            best = max(cand, key=lambda s: s.x1 - s.x0)
            return best.lines
    return []


def cmd_zoom(a) -> int:
    im = Image.open(a.img).convert("L")
    x0, y0, x1, y1 = (int(float(v)) for v in (a.x0, a.y0, a.x1, a.y1))
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(im.width, x1), min(im.height, y1)
    if x1 <= x0 or y1 <= y0:
        print(f"empty region after clipping to the image ({im.width}x{im.height}); "
              f"coordinates are pixels of {a.img}")
        return 1
    c = im.crop((x0, y0, x1, y1))
    c = c.resize((max(1, int(c.width * a.scale)), max(1, int(c.height * a.scale))), Image.LANCZOS)
    c.save(a.out)
    print(f"wrote {a.out} ({c.width}x{c.height}, scale {a.scale})")
    return 0


def _xrange(a, w):
    return int(a.x0 or 0), int(a.x1 or w)


def cmd_tab(a) -> int:
    im = Image.open(a.img).convert("L")
    g = np.asarray(im)
    ink = si.binarize(g)
    lines = find_lines_in_crop(ink, a.lines)
    if not lines:
        print("no tab lines found in this crop")
        return 1
    if len(lines) != a.lines:
        print(f"found {len(lines)} tab lines but expected {a.lines} (manifest tab_lines) -- a line is missing or "
              "extra, so string numbers would be wrong. Not reporting strings; zoom and count lines by eye.")
        return 1
    xs0, xs1 = _xrange(a, ink.shape[1])
    xm = (xs0 + xs1) / 2
    ys = [ln.y_at(xm) for ln in lines]
    sp = float(np.median(np.diff(ys)))
    print(f"{len(lines)} tab lines at y = {', '.join(f'{y:.0f}' for y in ys)} (spacing {sp:.1f}px); string 1 = top line")
    # remove the line pixels (thin rows along each fitted line) but keep any stroke that
    # crosses a line vertically, so digits survive and are not joined to each other by line pieces
    H, W = ink.shape
    _, t = si.estimate_spacing(ink)
    vlen = int(t) + 3
    vert = ndi.maximum_filter1d(ndi.minimum_filter1d(ink.astype(np.uint8), vlen, axis=0), vlen, axis=0).astype(bool)
    band = np.zeros_like(ink)
    cols = np.arange(W)
    for ln in lines:
        yy = np.round(ln.a + ln.b * cols).astype(int)
        for dy in range(-int(t) - 1, int(t) + 2):
            band[np.clip(yy + dy, 0, H - 1), cols] = True
    work = ink & ~(band & ~vert)
    work &= ~si.horizontal_opening(work, max(9, int(1.2 * sp)) | 1)  # leftover long horizontals
    work[:, :xs0] = False
    work[:, xs1:] = False
    lab, n = ndi.label(work)
    blobs = []
    for i, sl in enumerate(ndi.find_objects(lab), start=1):
        if sl is None:
            continue
        hgt, wid = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
        if hgt < 0.12 * sp or hgt > 1.3 * sp or wid > 1.6 * sp or (hgt < 0.3 * sp and wid < 0.3 * sp):
            continue  # specks, barlines, the "TAB" clef, text
        blobs.append([sl[1].start, sl[0].start, sl[1].stop, sl[0].stop, [i]])
    blobs.sort()
    merged = []
    for b in blobs:  # merge pieces of one digit, and the digits of one number (e.g. "12")
        hit = None
        for q in range(len(merged) - 1, max(-1, len(merged) - 4), -1):
            m = merged[q]
            xov = min(m[2], b[2]) - max(m[0], b[0])
            vgap = max(m[1], b[1]) - min(m[3], b[3])
            cy_m, cy_b = (m[1] + m[3]) / 2, (b[1] + b[3]) / 2
            same_digit = xov > 0.5 * min(m[2] - m[0], b[2] - b[0]) and vgap < 0.3 * sp
            next_digit = 0 <= b[0] - m[2] < 0.25 * sp and abs(cy_m - cy_b) < 0.3 * sp
            if same_digit or next_digit:
                hit = q
                break
        if hit is not None:
            m = merged[hit]
            merged[hit] = [min(m[0], b[0]), min(m[1], b[1]), max(m[2], b[2]), max(m[3], b[3]), m[4] + b[4]]
        else:
            merged.append(b)
    rows = []
    for x0, y0, x1, y1, ids in merged:
        if y1 - y0 < 0.35 * sp or y1 - y0 > 1.3 * sp or x1 - x0 < 0.2 * sp:
            continue  # a fragment that did not merge into a digit, or a barline piece
        cy = (y0 + y1) / 2
        dists = [abs(cy - ln.y_at((x0 + x1) / 2)) for ln in lines]
        k = int(np.argmin(dists))
        if dists[k] > 0.45 * sp:
            continue  # not sitting on a line (e.g. tuplet numbers, text)
        if x1 - x0 > 1.3 * sp:
            continue  # wider than a two-digit number: clef letters, text
        # count loops on the ORIGINAL ink of the digit (line pixels put back), not the line-stripped blob
        blob = np.isin(lab[y0:y1, x0:x1], ids)
        mask = ink[y0:y1, x0:x1] & ndi.binary_dilation(blob, iterations=int(t) + 2)
        holes = ndi.label(ndi.binary_fill_holes(mask) & ~mask)[1]
        rows.append((x0, y0, x1, y1, k + 1, dists[k], holes, len(ids)))
    print(f"{len(rows)} digit blob(s) on lines" + (f" in x {xs0}..{xs1}" if a.x0 or a.x1 else "") + ":")
    print("   x_center  y_center  string  off_line  w x h   holes  glyphs")
    for x0, y0, x1, y1, s, off, holes, ng in rows:
        print(f"   {(x0 + x1) / 2:8.0f}  {(y0 + y1) / 2:8.0f}  {s:6d}  {off:7.1f}  {x1 - x0:3d}x{y1 - y0:<3d} {holes:5d}  {ng:5d}")
    if a.out:
        rgb = im.convert("RGB")
        dr = ImageDraw.Draw(rgb)
        for ln in lines:
            dr.line([(0, ln.a), (W, ln.a + ln.b * W)], fill=(255, 160, 160), width=1)
        for x0, y0, x1, y1, s, off, holes, ng in rows:
            dr.rectangle([x0 - 1, y0 - 1, x1 + 1, y1 + 1], outline=(220, 0, 0))
            dr.text((x0, y1 + 2), f"s{s}", fill=(0, 0, 200))
        rgb.save(a.out)
        print(f"annotated: {a.out}")
    return 0


def cmd_staff(a) -> int:
    im = Image.open(a.img).convert("L")
    g = np.asarray(im)
    ink = si.binarize(g)
    lines = find_lines_in_crop(ink, 5)
    if len(lines) != 5:
        print(f"found {len(lines)} staff lines (need 5) -- crop a region that contains the whole staff")
        return 1
    H, W = ink.shape
    xs0, xs1 = _xrange(a, W)
    xm = (xs0 + xs1) / 2
    ys = [ln.y_at(xm) for ln in lines]
    d = float(np.median(np.diff(ys)))
    print(f"staff lines at y = {', '.join(f'{y:.0f}' for y in ys)} (spacing {d:.1f}px), clef {a.clef} "
          f"(bottom line = {step_to_pitch(0, a.clef)})")
    # remove staff lines + ledger-like thin horizontals, then keep blobs at least notehead-thick
    thick_len = max(3, int(0.45 * d))
    vert_thick = ndi.maximum_filter1d(ndi.minimum_filter1d(ink.astype(np.uint8), thick_len, axis=0),
                                      thick_len, axis=0).astype(bool)
    body = ink & vert_thick
    # fill small holes (hollow noteheads) only
    holes = ndi.binary_fill_holes(ink) & ~ink
    hl, hn = ndi.label(holes)
    if hn:
        sizes = ndi.sum(holes, hl, index=np.arange(1, hn + 1))
        small = np.isin(hl, np.flatnonzero(sizes <= 1.2 * d * d) + 1)
        body |= small
        body |= ink & ndi.binary_dilation(small, iterations=max(1, int(0.25 * d)))
    # an ellipse ~0.55d tall x 0.8d wide must fit: removes stems and most beams
    eh, ew = max(3, int(0.55 * d)), max(3, int(0.8 * d))
    yy, xx = np.mgrid[-eh // 2: eh // 2 + 1, -ew // 2: ew // 2 + 1]
    ell = (yy / (eh / 2)) ** 2 + (xx / (ew / 2)) ** 2 <= 1.0
    heads = ndi.binary_opening(body, structure=ell)
    heads[:, :xs0] = False
    heads[:, xs1:] = False
    lab, n = ndi.label(heads)
    rows = []
    accs = []
    bottom = lines[-1]
    # glyph components with the staff lines removed: accidentals are narrow and tall
    vlen = max(3, int(0.6 * d))
    vert = ndi.maximum_filter1d(ndi.minimum_filter1d(ink.astype(np.uint8), vlen, axis=0), vlen, axis=0).astype(bool)
    glyphs = ink & ~(si.horizontal_opening(ink, max(9, int(1.5 * d)) | 1) & ~vert)
    glab, _ = ndi.label(glyphs)
    gobj = ndi.find_objects(glab)
    for sl in ndi.find_objects(lab):
        if sl is None:
            continue
        hgt, wid = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
        if wid < 0.7 * d or hgt < 0.55 * d or wid > 3.6 * d or hgt > 4 * d:
            continue
        if wid > 2.2 * d:  # an accidental (or a neighbouring head) fused to the head: take the right end
            cx = sl[1].stop - 0.65 * d
            cy = (sl[0].start + sl[0].stop) / 2
            step = int(round((bottom.y_at(cx) - cy) / (d / 2)))
            rows.append((cx, cy, step, 1.3 * d, hgt, False, "fused with a glyph on its left (accidental?)"))
            continue
        ids = glab[sl][heads[sl]]
        ids = ids[ids > 0]
        if ids.size:
            comp = gobj[int(np.bincount(ids).argmax()) - 1]
            cw, ch = comp[1].stop - comp[1].start, comp[0].stop - comp[0].start
            if cw < 1.05 * d and ch > 1.8 * d:
                cy = (comp[0].start + comp[0].stop) / 2
                accs.append(((sl[1].start + sl[1].stop) / 2, cy))
                continue
        cx = (sl[1].start + sl[1].stop) / 2
        k = max(1, round(hgt / (0.95 * d)))  # stacked heads (chord seconds/thirds) -> several
        for j in range(k):
            cy = sl[0].start + (j + 0.5) * hgt / k
            step = int(round((bottom.y_at(cx) - cy) / (d / 2)))
            rows.append((cx, cy, step, wid, hgt / k, k > 1, ""))
    rows.sort()
    print(f"{len(rows)} notehead candidate(s)" + (f" in x {xs0}..{xs1}" if a.x0 or a.x1 else "") + ":")
    print("   x_center  y_center  step  written   (step 0 = bottom line; even = line, odd = space)")
    for cx, cy, step, wid, hh, stacked, note in rows:
        where = "line" if step % 2 == 0 else "space"
        led = ""
        if step <= -2:
            led = f", {(-step) // 2} ledger line(s) below"
        elif step >= 10:
            led = f", {(step - 8) // 2} ledger line(s) above"
        print(f"   {cx:8.0f}  {cy:8.0f}  {step:4d}  {step_to_pitch(step, a.clef):7s}   ({where}{led}"
              + (", part of a stacked blob" if stacked else "") + (f", {note}" if note else "") + ")")
    if accs:
        print("accidental-like glyphs (narrow + tall; identify the glyph by eye):")
        for ax, ay in sorted(set((round(x), round(y)) for x, y in accs)):
            step = int(round((bottom.y_at(ax) - ay) / (d / 2)))
            print(f"   x {ax:5d}  y {ay:5d}  centred near step {step} ({step_to_pitch(step, a.clef)})")
    if a.out:
        rgb = im.convert("RGB")
        dr = ImageDraw.Draw(rgb)
        for ax, ay in accs:
            dr.rectangle([ax - 0.4 * d, ay - 1.2 * d, ax + 0.4 * d, ay + 1.2 * d], outline=(0, 160, 0), width=2)
        for cx, cy, step, wid, hh, stacked, _note in rows:
            dr.ellipse([cx - wid / 2, cy - hh / 2, cx + wid / 2, cy + hh / 2], outline=(220, 0, 0), width=2)
            dr.text((cx - wid / 2, cy + hh / 2 + 2), step_to_pitch(step, a.clef), fill=(0, 0, 200))
        rgb.save(a.out)
        print(f"annotated: {a.out}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    z = sub.add_parser("zoom")
    for k in ("img", "x0", "y0", "x1", "y1"):
        z.add_argument(k)
    z.add_argument("--scale", type=float, default=3.0)
    z.add_argument("--out", required=True)
    for name in ("tab", "staff"):
        p = sub.add_parser(name)
        p.add_argument("img")
        p.add_argument("--x0", type=float, default=None)
        p.add_argument("--x1", type=float, default=None)
        p.add_argument("--out", default=None)
        if name == "staff":
            p.add_argument("--clef", choices=["treble", "bass"], default="treble")
        else:
            p.add_argument("--lines", type=int, default=6, help="expected tab line count (manifest tab_lines)")
    a = ap.parse_args(argv)
    return {"zoom": cmd_zoom, "tab": cmd_tab, "staff": cmd_staff}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
