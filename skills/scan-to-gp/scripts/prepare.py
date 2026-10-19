#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy>=1.24", "scipy>=1.10", "pillow>=10.0", "pymupdf>=1.23"]
# ///
"""prepare.py -- scanned pages -> deskewed pages -> staff+tab systems -> reader crops.

usage: uv run prepare.py <input.pdf|png|jpg ...> <workdir> [--unverified] [--dpi N]

Writes into <workdir>:
  pages/pN.png                 deskewed page (grayscale)
  crops/<sys>_full.png         whole system (staff + tab)
  crops/<sys>_staff.png        notation staff only (cut at the emptiest band above the tab)
  crops/<sys>_tab.png          tab staff only
  crops/<sys>_{staff,tab}_segK.png  overlapping horizontal segments at native resolution
  bars/<sys>_bNN_{staff,tab}.png    one crop per detected bar (upscaled for small scans)
  contact/pN.png               page overview with detected lines / systems / barlines drawn
  contact/systems.png          every system crop stacked with its id (quick visual check)
  manifest.json                pages, systems (reading order), crop paths, warnings

Exit codes: 0 ok; 2 nothing usable found; 3 refused (a page is not staff+tab and
--unverified was not given, or an unsupported layout was detected).
"""

from __future__ import annotations

import os
import sys

if __name__ == "__main__" and not sys.flags.isolated:  # re-exec isolated (-I): no PYTHONPATH / user site / cwd on sys.path
    os.execv(sys.executable, [sys.executable, "-I", *sys.argv])
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import argparse  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

import stg_image as si  # noqa: E402

SEG_MAX_W = 1400  # widest segment we hand to a reader (vision downsizes larger images)
BAR_TARGET_SPACE = 30.0  # bar crops are upscaled so line spacing is ~this many px


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", help="PDF and/or image files, in reading order, then the work dir")
    ap.add_argument("--unverified", action="store_true",
                    help="allow tab-only systems (output can NOT be cross-checked)")
    ap.add_argument("--dpi", type=int, default=400, help="render DPI for vector / mixed PDF pages")
    args = ap.parse_args(argv)
    if len(args.inputs) < 2:
        ap.error("need at least one input and a work dir")
    *inputs, workdir = args.inputs
    work = Path(workdir)
    for sub in ("pages", "crops", "bars", "contact", "reads", "zoom"):
        (work / sub).mkdir(parents=True, exist_ok=True)

    pages = si.load_pages(inputs, args.dpi)
    if not pages:
        print("no pages found", file=sys.stderr)
        return 2

    manifest = {"version": 1, "inputs": [Path(p).name for p in inputs], "pages": [], "systems": [], "warnings": []}
    sheet_rows = []
    for pi, pg in enumerate(pages, start=1):
        res = process_page(pi, pg, work, manifest)
        sheet_rows.extend(res)

    write_systems_sheet(work, sheet_rows)
    kinds = [s["kind"] for s in manifest["systems"]]
    refused = []
    for s in manifest["systems"]:
        if s["kind"] == "staff+tab":
            continue
        if s["kind"] == "tab-only" and args.unverified:
            continue
        refused.append(f'{s["id"]}: {s["kind"]}')
    manifest["verifiable"] = bool(kinds) and all(k == "staff+tab" for k in kinds)
    manifest["refused"] = refused
    (work / "manifest.json").write_text(json.dumps(manifest, indent=2))

    print(f"pages: {len(pages)}  systems: {len(manifest['systems'])}")
    for p in manifest["pages"]:
        print(f"  page {p['page']} ({p['source']} p{p['source_page']}, {p['extraction']}, deskew {p['deskew_deg']:+.2f} deg): "
              f"{len(p['systems'])} system(s)")
    for s in manifest["systems"]:
        print(f"    {s['id']}: {s['kind']}, {s['n_bars_detected']} bar(s) detected"
              + (f", tab lines {s['tab_lines']}" if s.get("tab_lines") else ""))
    for w in manifest["warnings"]:
        print("  WARNING:", w)
    print(f"manifest: {work / 'manifest.json'}")
    print(f"contact sheets: {work / 'contact'}")
    if not manifest["systems"]:
        print("REFUSED: no staff or tab systems were found.", file=sys.stderr)
        return 2
    if refused:
        print("REFUSED: v1 needs standard notation AND tab for every system (that is what makes the result verifiable):",
              file=sys.stderr)
        for r in refused:
            print("   ", r, file=sys.stderr)
        if any("tab-only" in r for r in refused):
            print("    (re-run with --unverified to transcribe tab-only systems without cross-checking)", file=sys.stderr)
        return 3
    return 0


def process_page(pi: int, pg: si.Page, work: Path, manifest: dict) -> list[tuple[str, Image.Image]]:
    gray0 = np.asarray(pg.image)
    ink0 = si.binarize(gray0)
    angle = si.estimate_skew(ink0)
    gray_img = si.rotate_gray(pg.image, angle)
    gray = np.asarray(gray_img)
    ink = si.binarize(gray)
    d, t = si.estimate_spacing(ink)
    lines = si.detect_lines(ink, d, t)

    # second pass: residual skew measured from the fitted lines themselves
    if lines:
        wts = np.array([ln.x1 - ln.x0 for ln in lines])
        resid = float(np.degrees(np.arctan(np.average([ln.b for ln in lines], weights=wts))))
        if abs(resid) > 0.03:
            angle += resid
            gray_img = si.rotate_gray(pg.image, angle)
            gray = np.asarray(gray_img)
            ink = si.binarize(gray)
            d, t = si.estimate_spacing(ink)
            lines = si.detect_lines(ink, d, t)

    staves = si.group_staves(lines, d)
    if staves:  # re-run with the smallest real staff spacing if the run-length guess was off
        d2 = min(st.spacing for st in staves)
        if abs(d2 - d) / d > 0.2:
            d = d2
            lines = si.detect_lines(ink, d, t)
            staves = si.group_staves(lines, d)
    page_warn: list[str] = []
    H, W = ink.shape
    in_staff = {id(ln) for st in staves for ln in st.lines}
    stray = sorted((ln for ln in lines if id(ln) not in in_staff), key=lambda ln: ln.y_mid)
    # four unexplained, equally spaced lines look like a staff we failed to group (text rows can
    # produce stray lines too, but their spacing alternates between x-height and line gap)
    for k in range(len(stray) - 3):
        gaps = [stray[k + j + 1].y_mid - stray[k + j].y_mid for j in range(3)]
        if max(gaps) <= 3 * d and max(gaps) <= 1.15 * min(gaps):
            page_warn.append(f"page {pi}: equally spaced horizontal lines near y={stray[k].y_mid:.0f} are not part "
                             f"of any detected staff -- a staff may have been missed; check contact/p{pi}.png")
            break
    gray_img.save(work / "pages" / f"p{pi}.png")

    # pair notation staff + tab staff below it into systems
    systems: list[tuple[si.Staff | None, si.Staff | None, str]] = []
    i = 0
    while i < len(staves):
        st = staves[i]
        nxt = staves[i + 1] if i + 1 < len(staves) else None
        if st.kind == "tab" and len(st.lines) == 4 and nxt is not None and nxt.kind == "tab" and \
                len(nxt.lines) >= 6 and nxt.top - st.bottom < 16 * max(st.spacing, nxt.spacing):
            page_warn.append(f"page {pi}: a 4-line group sits directly above a {len(nxt.lines)}-line tab near "
                             f"y={st.top:.0f}; probably a notation staff with a line the detector missed. "
                             f"Not guessing -- check contact/p{pi}.png")
            st.kind = "unknown"
        if st.kind == "staff" and nxt is not None and nxt.kind == "tab" and \
                nxt.top - st.bottom < 16 * max(st.spacing, nxt.spacing) and _xoverlap(st, nxt) > 0.6:
            systems.append((st, nxt, "staff+tab"))
            i += 2
        elif st.kind == "staff":
            systems.append((st, None, "staff-only"))
            i += 1
        elif st.kind == "tab":
            systems.append((None, st, "tab-only"))
            i += 1
        else:
            page_warn.append(f"page {pi}: a {len(st.lines)}-line group near y={st.top:.0f} is neither a 5-line staff "
                             f"nor a 4/6/7-line tab; ignored (check contact/p{pi}.png)")
            i += 1

    csum = np.zeros((H + 1, W), np.int32)
    np.cumsum(ink, axis=0, out=csum[1:])

    ids = []
    sheet_rows = []
    sys_records = []
    for k, (staff, tab, kind) in enumerate(systems, start=1):
        sid = f"p{pi}_s{k}"
        ids.append(sid)
        parts = [s for s in (staff, tab) if s is not None]
        top_st, bot_st = parts[0], parts[-1]
        sys_records.append((sid, staff, tab, kind, top_st, bot_st))

    # vertical boundaries between consecutive systems: emptiest band in the gap
    bounds = []
    for k, (sid, staff, tab, kind, top_st, bot_st) in enumerate(sys_records):
        x0 = int(max(0, min(p.x0 for p in (staff, tab) if p) - 2.5 * top_st.spacing))
        x1 = int(min(W, max(p.x1 for p in (staff, tab) if p) + 2 * top_st.spacing))
        if k == 0:  # first system on the page: keep a heading if one sits close above
            y_top = si.emptiest_band(ink, int(top_st.top - 14 * top_st.spacing),
                                     int(top_st.top - 7 * top_st.spacing), x0, x1)
        else:
            prev_bot = sys_records[k - 1][5]
            y_top = si.emptiest_band(ink, int(prev_bot.bottom + 1.2 * prev_bot.spacing),
                                     int(top_st.top - 1.2 * top_st.spacing), x0, x1)
        bounds.append([y_top, None, x0, x1])
        if k > 0:
            bounds[k - 1][1] = y_top
    if bounds:
        last_bot = sys_records[-1][5]
        bounds[-1][1] = int(min(H, last_bot.bottom + 5 * last_bot.spacing))

    for (sid, staff, tab, kind, top_st, bot_st), (y0, y1, x0, x1) in zip(sys_records, bounds):
        warn: list[str] = []
        rec: dict = {"id": sid, "page": pi, "kind": kind, "bbox": [x0, y0, x1, y1]}
        if staff is not None:
            rec["staff_space_px"] = round(staff.spacing, 2)
            rec["staff_lines_y"] = [round(ln.y_at((staff.x0 + staff.x1) / 2), 1) for ln in staff.lines]
        if tab is not None:
            rec["tab_lines"] = len(tab.lines)
            rec["tab_space_px"] = round(tab.spacing, 2)
            rec["tab_lines_y"] = [round(ln.y_at((tab.x0 + tab.x1) / 2), 1) for ln in tab.lines]
            if len(tab.lines) == 4:
                warn.append(f"{sid}: 4-line tab (bass?) -- pass a 4-string --tuning to reconcile, e.g. E1,A1,D2,G2")
            if len(tab.lines) == 7:
                warn.append(f"{sid}: 7-line tab (7-string?) -- pass a 7-string --tuning to reconcile, e.g. B1,E2,A2,D3,G3,B3,E4")
        for st in (staff, tab):
            if st is None:
                continue
            for n in st.notes:
                warn.append(f"{sid}: {n}")
            wob = max(ln.wobble() for ln in st.lines if not ln.interpolated)
            if wob > 0.35 * st.spacing:
                warn.append(f"{sid}: staff lines are wavy (max deviation {wob:.1f}px vs spacing {st.spacing:.1f}px) -- "
                            "warped photo or handwritten? read with extra care")
            if st.spacing < 12:
                warn.append(f"{sid}: low resolution (line spacing {st.spacing:.1f}px); digits/accidentals may be "
                            "unreadable -- rescan at 300+ dpi if possible")

        # split staff / tab at the emptiest row band between them
        if staff is not None and tab is not None:
            split = si.emptiest_band(ink, int(staff.bottom + 0.6 * staff.spacing), int(tab.top - 0.6 * tab.spacing), x0, x1)
        elif staff is not None:
            split = y1
        else:
            split = y0
        rec["split_y"] = split

        # barlines (tab staff is primary: nothing but barlines spans a tab staff vertically)
        primary, other = (tab, staff) if tab is not None else (staff, None)
        bls = si.find_barlines(ink, csum, primary, other, t)
        bars = si.bars_from_barlines(primary, bls, primary.spacing)
        rec["barlines_x"] = [[round(b.xa), round(b.xb)] for b in bls]
        rec["n_bars_detected"] = len(bars)
        if not bls:
            warn.append(f"{sid}: no barlines detected -- readers must find bar boundaries themselves")
        unconf = [b for b in bls if other is not None and not b.staff_confirmed]
        if unconf:
            rec["barlines_unconfirmed_by_staff"] = [round(b.xa) for b in unconf]

        crops = {}
        full = gray_img.crop((x0, y0, x1, y1))
        crops["full"] = save(full, work / "crops" / f"{sid}_full.png", work)
        if staff is not None:
            crops["staff"] = save(gray_img.crop((x0, y0, x1, split)), work / "crops" / f"{sid}_staff.png", work)
        if tab is not None:
            crops["tab"] = save(gray_img.crop((x0, split, x1, y1)), work / "crops" / f"{sid}_tab.png", work)
        rec["crops"] = crops

        segs = segment_ranges(x1 - x0, primary.spacing)
        rec["segments"] = {}
        for part in ("staff", "tab"):
            if part not in crops:
                continue
            img = Image.open(work / crops[part])
            lst = []
            for j, (a, b) in enumerate(segs, start=1):
                lst.append(save(img.crop((a, 0, b, img.height)), work / "crops" / f"{sid}_{part}_seg{j}.png", work))
            rec["segments"][part] = lst
        rec["segment_x_ranges"] = [[x0 + a, x0 + b] for a, b in segs]

        scales = {part: max(1.0, min(4.0, BAR_TARGET_SPACE / st.spacing))
                  for part, st in (("staff", staff), ("tab", tab)) if st is not None}
        rec["bar_crop_scale"] = {k: round(v, 2) for k, v in scales.items()}
        bar_recs = []
        pad = 1.0 * primary.spacing
        for bn, (ba, bb) in enumerate(bars, start=1):
            bx0, bx1 = int(max(x0, ba - pad)), int(min(x1, bb + pad))
            br = {"bar": bn, "x": [round(ba), round(bb)]}
            for part, (ya, yb) in (("staff", (y0, split)), ("tab", (split, y1))):
                if part not in crops:
                    continue
                c = gray_img.crop((bx0, ya, bx1, yb))
                scale = scales[part]
                if scale > 1.01:
                    c = c.resize((round(c.width * scale), round(c.height * scale)), Image.LANCZOS)
                br[part] = save(c, work / "bars" / f"{sid}_b{bn:02d}_{part}.png", work)
            bar_recs.append(br)
        rec["bars"] = bar_recs
        rec["warnings"] = warn
        manifest["warnings"].extend(warn)
        manifest["systems"].append(rec)
        sheet_rows.append((f"{sid}  [{kind}, {len(bars)} bars]", full, split - y0))

    if not ids:
        page_warn.append(f"page {pi}: no staff or tab systems found (text page? too low resolution?) -- "
                         f"check contact/p{pi}.png")
    manifest["warnings"].extend(page_warn)
    manifest["pages"].append({
        "page": pi, "source": pg.source, "source_page": pg.source_page, "extraction": pg.extraction,
        "size": [W, H], "deskew_deg": round(angle, 3), "line_space_px": round(d, 2), "line_thick_px": round(t, 2),
        "systems": ids, "contact": f"contact/p{pi}.png",
    })
    draw_contact(gray_img, staves, manifest["systems"][-len(ids):] if ids else [], work / "contact" / f"p{pi}.png")
    return sheet_rows


def _xoverlap(a: si.Staff, b: si.Staff) -> float:
    lo, hi = max(a.x0, b.x0), min(a.x1, b.x1)
    return max(0.0, hi - lo) / max(1.0, min(a.x1 - a.x0, b.x1 - b.x0))


def segment_ranges(width: int, d: float) -> list[tuple[int, int]]:
    """Overlapping horizontal windows, each <= SEG_MAX_W wide, always at least 2."""
    ov = int(min(max(12 * d, 0.12 * width, 250), SEG_MAX_W / 3))
    n = max(2, math.ceil((width - ov) / max(1, SEG_MAX_W - ov)))
    step = (width - ov) / n
    return [(int(round(i * step)), int(round(min(width, i * step + step + ov)))) for i in range(n)]


def save(img: Image.Image, path: Path, work: Path) -> str:
    img.save(path)
    return str(path.relative_to(work))


def draw_contact(gray_img: Image.Image, staves: list[si.Staff], systems: list[dict], out: Path) -> None:
    W, H = gray_img.size
    sc = min(1.0, 1300 / W)
    im = gray_img.convert("RGB").resize((int(W * sc), int(H * sc)), Image.BILINEAR)
    dr = ImageDraw.Draw(im)
    for st in staves:
        col = (40, 90, 255) if st.kind == "staff" else ((230, 40, 40) if st.kind == "tab" else (160, 160, 0))
        for ln in st.lines:
            dr.line([(ln.x0 * sc, ln.y_at(ln.x0) * sc), (ln.x1 * sc, ln.y_at(ln.x1) * sc)], fill=col, width=2)
    for s in systems:
        x0, y0, x1, y1 = s["bbox"]
        dr.rectangle([x0 * sc, y0 * sc, x1 * sc, y1 * sc], outline=(0, 170, 0), width=3)
        dr.line([(x0 * sc, s["split_y"] * sc), (x1 * sc, s["split_y"] * sc)], fill=(200, 0, 200), width=2)
        for xa, xb in s["barlines_x"]:
            xm = (xa + xb) / 2 * sc
            dr.line([(xm, y0 * sc), (xm, y1 * sc)], fill=(255, 140, 0), width=2)
        dr.rectangle([x0 * sc, y0 * sc, x0 * sc + 260, y0 * sc + 22], fill=(0, 120, 0))
        dr.text((x0 * sc + 4, y0 * sc + 5), f"{s['id']} {s['kind']} {s['n_bars_detected']} bars", fill=(255, 255, 255))
    im.save(out)


def write_systems_sheet(work: Path, rows: list[tuple[str, Image.Image, int]]) -> None:
    if not rows:
        return
    target_w = 1300
    tiles = []
    for label, img, split in rows:
        sc = target_w / img.width
        tile = img.convert("RGB").resize((target_w, max(1, int(img.height * sc))), Image.BILINEAR)
        dr = ImageDraw.Draw(tile)
        dr.line([(0, split * sc), (target_w, split * sc)], fill=(200, 0, 200), width=2)
        head = Image.new("RGB", (target_w, 24), (0, 110, 0))
        ImageDraw.Draw(head).text((6, 6), label, fill=(255, 255, 255))
        tiles.extend([head, tile])
    # chunk so no single sheet gets absurdly tall
    chunk, h, n = [], 0, 1
    for tl in tiles + [None]:
        if tl is None or (h + tl.height > 6000 and chunk):
            sheet = Image.new("RGB", (target_w, h), (255, 255, 255))
            y = 0
            for c in chunk:
                sheet.paste(c, (0, y))
                y += c.height
            sheet.save(work / "contact" / ("systems.png" if n == 1 else f"systems_{n}.png"))
            chunk, h, n = [], 0, n + 1
            if tl is None:
                break
        chunk.append(tl)
        h += tl.height


if __name__ == "__main__":
    sys.exit(main())
