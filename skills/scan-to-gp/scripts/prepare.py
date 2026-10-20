#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy>=1.24", "scipy>=1.10", "pillow>=10.0", "pymupdf>=1.23"]
# ///
"""prepare.py -- scanned pages -> deskewed pages -> staff+tab systems -> reader crops.

usage: uv run prepare.py <input.pdf|png|jpg ...> <workdir> [--pages 1,3-4] [--unverified] [--dpi N]

Writes into <workdir>:
  pages/pN.png                 deskewed page (grayscale)
  crops/pN_header.png          everything above the page's first system (title, tuning / capo
                               legend, tempo) -- read by the staff reader, never cross-checked
  crops/pN_footer.png          everything below the last system (footnotes, copyright)
  crops/<sys>_full.png         whole system (staff + tab)
  crops/<sys>_staff.png        notation staff only (cut in the first true gap below the staff's
                               own glyphs; text between systems belongs to the system below)
  crops/<sys>_tab.png          tab staff only
  crops/<sys>_{staff,tab}_segK.png  overlapping horizontal segments at native resolution
  bars/<sys>_bNN_{staff,tab}.png    one crop per detected bar (upscaled for small scans)
  crops/<id>_full.png          dropped / skipped staves (vocal, other instruments), for the record
  contact/pN.png               page overview with detected lines / systems / barlines drawn
  contact/systems.png          every system crop stacked with its id (quick visual check)
  manifest.json                pages, systems (reading order), groups / parts, dropped staves,
                               gaps (skipped systems: position + bar count), crop paths, warnings

Layout rules:
  - A guitar system is a notation staff with a TAB staff right below it ("staff+tab").
  - Staves joined at the left edge by one system line / bracket form one system group. Several
    staff+tab pairs in one group are stacked PARTS played together (Gtr. 1 over Gtr. 2): each
    pair stays a separate system with `group` and `part`, and becomes its own track. If the
    pairs' barlines do not line up, or the number of parts changes between groups, the page is
    refused rather than guessed (stacked parts must never be interleaved).
  - A notation staff without TAB inside a group that has a guitar staff+TAB (vocal line,
    glockenspiel, ...) is dropped with a warning. A whole system without any guitar staff+TAB
    (e.g. a vocal-only system where the guitar repeats an earlier figure) is skipped with a warning
    when the page has guitar systems, and recorded as a timeline gap: its bars are counted from the
    barlines on its staff (note stems excluded) -- or marked unknown, never guessed -- so reconcile
    keeps them as untranscribed bars. A page with notation but no TAB at all is refused.
  - Rows of chord diagrams are recognised (broken lines / dense vertical lines) and ignored.

Exit codes: 0 ok; 2 nothing usable found; 3 refused (tab-only systems without --unverified,
notation without any TAB, or an ambiguous stacked-part layout).
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
from dataclasses import dataclass  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

import stg_image as si  # noqa: E402

SEG_MAX_W = 1400  # widest segment we hand to a reader (vision downsizes larger images)
BAR_TARGET_SPACE = 30.0  # bar crops are upscaled so line spacing is ~this many px
CONNECTOR_MIN = 0.9  # fraction of rows between two staves inked at the left edge = same system


@dataclass
class Unit:
    """A staff+tab pair, or a lone staff / tab, in vertical page order."""
    staff: si.Staff | None
    tab: si.Staff | None
    kind: str  # "staff+tab" | "staff-only" | "tab-only"
    role: str = "system"  # "system" (transcribed) | "dropped" | "skipped"
    reason: str = ""
    gid: str = ""
    part: int = 0
    id: str = ""
    y0: int = 0
    y1: int = 0
    x0: int = 0
    x1: int = 0

    @property
    def staves(self) -> list[si.Staff]:
        return [s for s in (self.staff, self.tab) if s is not None]

    @property
    def top(self) -> si.Staff:
        return self.staves[0]

    @property
    def bot(self) -> si.Staff:
        return self.staves[-1]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", help="PDF and/or image files, in reading order, then the work dir")
    ap.add_argument("--unverified", action="store_true",
                    help="allow tab-only systems (output can NOT be cross-checked)")
    ap.add_argument("--dpi", type=int, default=400, help="render DPI for vector / mixed PDF pages")
    ap.add_argument("--pages", default=None,
                    help="only these pages, 1-based across all inputs in order, e.g. 1,3-4 (default: all)")
    args = ap.parse_args(argv)
    if len(args.inputs) < 2:
        ap.error("need at least one input and a work dir")
    *inputs, workdir = args.inputs
    missing_inputs = [p for p in inputs if not Path(p).is_file()]
    if missing_inputs:
        print(f"input file(s) not found: {missing_inputs}", file=sys.stderr)
        return 2
    try:
        select = si.parse_page_spec(args.pages)
    except ValueError as e:
        ap.error(str(e))
    if select is not None:
        total = si.count_pages(inputs)
        missing = sorted(p for p in select if p > total)
        if missing:
            print(f"--pages {args.pages}: the input has only {total} page(s); no page(s) {missing}", file=sys.stderr)
            return 2
    work = Path(workdir)
    for sub in ("pages", "crops", "bars", "contact", "reads", "zoom"):
        (work / sub).mkdir(parents=True, exist_ok=True)

    pages = si.load_pages(inputs, args.dpi, select)
    if not pages:
        print("no pages found", file=sys.stderr)
        return 2

    manifest = {"version": 3, "inputs": [Path(p).name for p in inputs], "pages": [], "systems": [], "groups": [],
                "dropped": [], "gaps": [], "warnings": [], "layout_errors": []}
    if select is not None:
        manifest["page_selection"] = sorted(select)
    sheet_rows = []
    for pg in pages:
        if pg.inverted:
            manifest["warnings"].append(f"page {pg.number}: the image was white-on-black (inverted 1-bit scan?); "
                                        "inverted it before analysis")
        res = process_page(pg.number, pg, work, manifest)
        sheet_rows.extend(res)

    write_systems_sheet(work, sheet_rows)
    _check_part_counts(manifest)
    refused = []
    for s in manifest["systems"]:
        if s["kind"] == "staff+tab":
            continue
        if s["kind"] == "tab-only":
            if not args.unverified:
                refused.append(f'{s["id"]}: TAB without a notation staff -- nothing to cross-check it against '
                               "(re-run with --unverified to transcribe it UNCHECKED)")
            continue
        refused.append(f'{s["id"]}: {s["kind"]} -- ' + (s.get("reason") or f'a notation staff with no TAB anywhere on '
                       f'page {s["page"]}; this skill cross-checks notation against TAB and needs both'))
    refused.extend(manifest["layout_errors"])
    kinds = [s["kind"] for s in manifest["systems"]]
    manifest["verifiable"] = bool(kinds) and all(k == "staff+tab" for k in kinds) and not manifest["layout_errors"]
    manifest["parts"] = max((g["parts"] for g in manifest["groups"]), default=1)
    manifest["refused"] = refused
    (work / "manifest.json").write_text(json.dumps(manifest, indent=2))

    print(f"pages: {len(pages)}  systems: {len(manifest['systems'])}"
          + (f"  stacked parts: {manifest['parts']}" if manifest["parts"] > 1 else "")
          + (f"  dropped/skipped staves: {len(manifest['dropped'])}" if manifest["dropped"] else ""))
    for p in manifest["pages"]:
        print(f"  page {p['page']} ({p['source']} p{p['source_page']}, {p['extraction']}, deskew {p['deskew_deg']:+.2f} deg): "
              f"{len(p['systems'])} system(s)" + (f"; header crop {p['header']}" if p.get("header") else ""))
    for s in manifest["systems"]:
        part = f" [{s['group']} part {s['part']}/{s['parts_in_group']}]" if s.get("parts_in_group", 1) > 1 else ""
        print(f"    {s['id']}{part}: {s['kind']}, {s['n_bars_detected']} bar(s) detected"
              + (f", tab lines {s['tab_lines']}" if s.get("tab_lines") else ""))
    for g in manifest["gaps"]:
        print(f"    {g['id']}: skipped system after {g['after_group'] or 'the start'}, "
              + (f"{g['bars']} bar(s) -> untranscribed bars" if g["bars"] is not None
                 else f"bar count UNKNOWN -> reconcile needs --gap-bars {g['id']}:<bars>"))
    for w in manifest["warnings"]:
        print("  WARNING:", w)
    print(f"manifest: {work / 'manifest.json'}")
    print(f"contact sheets: {work / 'contact'}")
    if not manifest["systems"]:
        if manifest["dropped"]:
            print("REFUSED: no guitar staff+TAB system was found (only staves without TAB).", file=sys.stderr)
        else:
            print("REFUSED: no staff or tab systems were found.", file=sys.stderr)
        return 2
    if refused:
        print("REFUSED:", file=sys.stderr)
        for r in refused:
            print("   ", r, file=sys.stderr)
        return 3
    return 0


def _check_part_counts(manifest: dict) -> None:
    counts = {g["id"]: g["parts"] for g in manifest["groups"]}
    if len(set(counts.values())) > 1:
        manifest["layout_errors"].append(
            "the number of stacked staff+TAB parts changes between systems ("
            + ", ".join(f"{k}: {v}" for k, v in counts.items())
            + ") -- cannot tell which part continues where; not guessing (stacked parts must never be "
              "interleaved). Split the input with --pages so every run has the same parts, or ask the user.")


def process_page(pi: int, pg: si.Page, work: Path, manifest: dict) -> list[tuple[str, Image.Image, int]]:
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
    csum = np.zeros((H + 1, W), np.int32)
    np.cumsum(ink, axis=0, out=csum[1:])

    grids = []
    for st in staves:
        why = si.is_grid(ink, csum, st, t)
        if why:
            st.kind = "grid"
            grids.append(st)
            page_warn.append(f"page {pi}: ignored a {len(st.lines)}-line grid near y={st.top:.0f} "
                             f"(x {st.x0:.0f}-{st.x1:.0f}): {why} -- chord diagrams, not a staff")
    staves = [st for st in staves if st.kind != "grid"]

    in_staff = {id(ln) for st in staves + grids for ln in st.lines}
    # broken "lines" (rows of chord-diagram frets, text baselines) are not a missed staff
    stray = sorted((ln for ln in lines if id(ln) not in in_staff
                    and si.line_continuity(ink, si.Staff([ln], "line", d, ln.x0, ln.x1), t) >= 0.75),
                   key=lambda ln: ln.y_mid)
    # four unexplained, equally spaced lines look like a staff we failed to group (text rows can
    # produce stray lines too, but their spacing alternates between x-height and line gap)
    for k in range(len(stray) - 3):
        gaps = [stray[k + j + 1].y_mid - stray[k + j].y_mid for j in range(3)]
        if max(gaps) <= 3 * d and max(gaps) <= 1.15 * min(gaps):
            page_warn.append(f"page {pi}: equally spaced horizontal lines near y={stray[k].y_mid:.0f} are not part "
                             f"of any detected staff -- a staff may have been missed; check contact/p{pi}.png")
            break
    gray_img.save(work / "pages" / f"p{pi}.png")

    units = _pair_units(staves, pi, page_warn)
    # lines / line groups that belong to no staff: a TAB the detector failed to group looks like this
    unexplained = [ln.y_mid for ln in stray] + [st.top for st in staves if st.kind == "unknown"]
    groups = _group_units(ink, units)
    page_has_pair = any(u.kind == "staff+tab" for u in units)
    n_sys = n_drop = 0
    page_groups = []
    for gi, grp in enumerate(groups, start=1):
        gid = f"p{pi}_g{gi}"
        guitar = [u for u in grp if u.kind in ("staff+tab", "tab-only")]
        for u in grp:
            u.gid = gid
            if u.kind == "staff-only" and guitar:
                u.role, u.reason = "dropped", ("notation staff without TAB in the same system as a guitar staff+TAB "
                                               "(vocal line / other instrument?) -- not transcribed")
            elif u.kind == "staff-only" and page_has_pair and any(
                    u.staff.bottom < y < u.staff.bottom + 16 * u.staff.spacing for y in unexplained):
                u.reason = ("notation staff with unexplained horizontal lines below it -- its TAB staff may have "
                            "been missed by the detector; not skipping it silently")
                page_warn.append(f"page {pi}: a notation staff near y={u.staff.top:.0f} has no TAB but unexplained "
                                 "lines below it (a missed TAB staff?) -- refused rather than skipped")
            elif u.kind == "staff-only" and page_has_pair:
                u.role, u.reason = "skipped", (
                    "notation staff without TAB, not joined to a guitar staff+TAB (a vocal line, or a vocal-only "
                    "system?) -- not transcribed. Its bars stay in the timeline as UNTRANSCRIBED (empty) bars; if "
                    "the page tells the guitar to play there (an instruction to repeat an earlier figure, a fill, a "
                    "repeat), those notes are NOT in the file: tell the user")
        for k, u in enumerate(guitar, start=1):
            u.part = k
        for u in grp:
            if u.role == "system":
                n_sys += 1
                u.id = f"p{pi}_s{n_sys}"
            else:
                n_drop += 1
                u.id = f"p{pi}_x{n_drop}"
        if guitar:
            page_groups.append({"id": gid, "page": pi, "systems": [u.id for u in guitar], "parts": len(guitar),
                                "dropped": [u.id for u in grp if u.role != "system"]})
            if len(guitar) > 1:
                page_warn.append(f"{gid}: {len(guitar)} staff+TAB pairs share one system line -- stacked parts played "
                                 f"together ({', '.join(u.id for u in guitar)}); each part becomes its own track")
        elif all(u.kind == "staff-only" for u in grp) and not page_has_pair:
            pass  # a page of notation without TAB: kept as staff-only systems -> refused

    _unit_geometry(ink, units, W, H, t)

    ids = []
    sheet_rows = []
    sys_records = []
    for u in units:
        if u.role != "system":
            rec = {"id": u.id, "page": pi, "kind": u.kind, "role": u.role, "group": u.gid,
                   "bbox": [u.x0, u.y0, u.x1, u.y1], "reason": u.reason}
            full = gray_img.crop((u.x0, u.y0, u.x1, u.y1))
            rec["crop"] = save(full, work / "crops" / f"{u.id}_full.png", work)
            manifest["dropped"].append(rec)
            page_warn.append(f"{u.id}: {u.role} -- {u.reason}")
            sheet_rows.append((f"{u.id}  [{u.role.upper()}: {u.kind}]", full, -1))
            continue
        rec, row = _system_record(u, pi, ink, csum, t, gray_img, work)
        ids.append(u.id)
        sys_records.append(rec)
        sheet_rows.append(row)

    by_id = {r["id"]: r for r in sys_records}
    prev_group = manifest["groups"][-1]["id"] if manifest["groups"] else None
    page_gaps = _page_gaps(ink, csum, t, groups, by_id, prev_group, pi)
    for gp_ in page_gaps:
        for x in manifest["dropped"]:
            if x["id"] in gp_["staves"]:
                x["gap"] = gp_["id"]
                x["gap_barlines_x"] = gp_["barlines_x"].get(x["id"], [])
        after = gp_["after_group"] or "the start"
        if gp_["bars"] is not None:
            page_warn.append(f"{gp_['id']}: skipped system (no guitar notation) after {after}: {gp_['bars']} bar(s) "
                             "counted from its barlines -- reconcile writes them as UNTRANSCRIBED (empty) bars in every "
                             "track so the timeline stays whole")
        else:
            page_warn.append(f"{gp_['id']}: skipped system (no guitar notation) after {after}: its bar count could not "
                             f"be read from the image ({gp_['why_unknown']}) -- reconcile refuses until you look at "
                             f"crops/{gp_['id']}_full.png and pass --gap-bars {gp_['id']}:<bars>")
    manifest.setdefault("gaps", []).extend(page_gaps)
    pending: list[tuple[str, str]] = []
    for u in units:  # notation-only crops of dropped staves right above a system in the same group:
        if u.role == "dropped":  # headings / tempo printed over a vocal line belong to the guitar too
            pending.append((u.gid, f"crops/{u.id}_full.png"))
        elif u.role == "skipped":
            pending = []
        else:
            ctx = [c for g, c in pending if g == u.gid]
            if ctx and u.id in by_id:
                by_id[u.id]["context_crops"] = ctx
            pending = []
    for g in page_groups:
        for sid in g["systems"]:
            by_id[sid]["parts_in_group"] = g["parts"]
        if g["parts"] > 1:
            _check_part_alignment(g, by_id, manifest)
    for r in sys_records:
        manifest["warnings"].extend(r["warnings"])
        manifest["systems"].append(r)
    manifest["groups"].extend(page_groups)

    header = None
    if units:  # everything above the first staff's own glyphs: title, tuning / capo legend, tempo
        top = units[0].top
        y_hdr = top.top_min - 0.6 * top.spacing
        for r in (si.attached_reach(ink, top, False, 0, units[0].x0, units[0].x1),
                  si.ledger_reach(ink, top, False, units[0].x0, units[0].x1, t) if top.kind == "staff" else None):
            if r is not None:
                y_hdr = min(y_hdr, r - 0.15 * top.spacing)
        y_hdr = int(y_hdr)
        if y_hdr > 2 * d:
            header = save(gray_img.crop((0, 0, W, y_hdr)), work / "crops" / f"p{pi}_header.png", work)
    footer = None
    if units:  # everything below the last system: footnotes (tuning / capo notes marked *) live here
        y_ft = int(units[-1].y1)
        if H - y_ft > 2 * d:
            footer = save(gray_img.crop((0, y_ft, W, H)), work / "crops" / f"p{pi}_footer.png", work)
    if not ids:
        page_warn.append(f"page {pi}: no guitar staff+TAB systems found (text page? too low resolution?) -- "
                         f"check contact/p{pi}.png")
    manifest["warnings"].extend(page_warn)
    manifest["pages"].append({
        "page": pi, "source": pg.source, "source_page": pg.source_page, "extraction": pg.extraction,
        "size": [W, H], "deskew_deg": round(angle, 3), "line_space_px": round(d, 2), "line_thick_px": round(t, 2),
        "systems": ids, "dropped": [u.id for u in units if u.role != "system"], "header": header, "footer": footer,
        "contact": f"contact/p{pi}.png",
    })
    draw_contact(gray_img, staves, grids, sys_records, [x for x in manifest["dropped"] if x["page"] == pi],
                 work / "contact" / f"p{pi}.png")
    return sheet_rows


def _page_gaps(ink: np.ndarray, csum: np.ndarray, t: float, groups: list[list[Unit]], by_id: dict,
               prev_group: str | None, pi: int) -> list[dict]:
    """Skipped systems (no guitar staff+TAB, e.g. a vocal-only system) keep their bars in the timeline as
    untranscribed bars, so they must be counted: barlines on the staff-only staff, stems excluded. The
    count is left UNKNOWN (never guessed) when a column is doubtful, the closing barline is missing, a bar
    is implausibly narrow, the staves of the system disagree, or its barlines line up with a guitar
    system next to it (then it may be a staff of that system whose system line was not detected)."""
    gaps = []
    last_guitar = prev_group
    for gi, grp in enumerate(groups):
        if any(u.role == "system" and u.kind in ("staff+tab", "tab-only") for u in grp):
            last_guitar = grp[0].gid
            continue
        skipped = [u for u in grp if u.role == "skipped"]
        if not skipped:
            continue
        counts, why, xs = [], [], {}
        for k, u in enumerate(skipped):
            st = u.staff
            up = st.top - skipped[k - 1].staff.bottom if k > 0 else 0.0
            dn = skipped[k + 1].staff.top - st.bottom if k + 1 < len(skipped) else 0.0
            bls, doubt = si.lone_staff_barlines(ink, csum, st, t, up, dn)
            bars = si.bars_from_barlines(st, bls, st.spacing)
            xs[u.id] = [[round(b.xa), round(b.xb)] for b in bls]
            if doubt:
                why.append(f"{u.id}: " + "; ".join(w for _, w in doubt[:3]))
            elif not bls or bls[-1].xb < st.x1 - 1.5 * st.spacing:
                why.append(f"{u.id}: no closing barline found at the end of the staff")
            elif any(b - a < 2.0 * st.spacing for a, b in bars):
                why.append(f"{u.id}: a bar narrower than two line spaces (a stem taken for a barline?)")
            counts.append(len(bars))
        if len(set(counts)) > 1:
            why.append(f"its staves show different bar counts {counts}")
        mids = [(a + b) / 2 for a, b in xs[skipped[0].id]]
        tol = 1.5 * skipped[0].staff.spacing
        for nb in (groups[gi - 1] if gi > 0 else [], groups[gi + 1] if gi + 1 < len(groups) else []):
            for u in nb:
                rec = by_id.get(u.id)
                if rec is None or u.kind != "staff+tab":
                    continue
                theirs = [(a + b) / 2 for a, b in rec["barlines_x"]]
                if mids and len(theirs) == len(mids) and all(abs(p - q) <= tol for p, q in zip(mids, theirs)):
                    why.append(f"its barlines line up with {u.id}'s: it may be a staff of that system whose system "
                               f"line was not detected (then it has no bars of its own: --gap-bars {skipped[0].id}:0)")
        gaps.append({"id": skipped[0].id, "page": pi, "staves": [u.id for u in skipped], "group": grp[0].gid,
                     "after_group": last_guitar, "bars": None if why else counts[0],
                     "how": None if why else "barlines on the staff-only staff (note stems excluded)",
                     "why_unknown": "; ".join(why) or None, "barlines_x": xs})
    return gaps


def _pair_units(staves: list[si.Staff], pi: int, page_warn: list[str]) -> list[Unit]:
    """Pair each notation staff with the TAB staff right below it."""
    units: list[Unit] = []
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
            units.append(Unit(st, nxt, "staff+tab"))
            i += 2
        elif st.kind == "staff":
            units.append(Unit(st, None, "staff-only"))
            i += 1
        elif st.kind == "tab":
            units.append(Unit(None, st, "tab-only"))
            i += 1
        else:
            page_warn.append(f"page {pi}: a {len(st.lines)}-line group near y={st.top:.0f} is neither a 5-line staff "
                             f"nor a 4/6/7-line tab; ignored (check contact/p{pi}.png)")
            i += 1
    return units


def _group_units(ink: np.ndarray, units: list[Unit]) -> list[list[Unit]]:
    """Consecutive units joined by a system line / bracket at the left edge form one group."""
    groups: list[list[Unit]] = []
    for u in units:
        if groups and si.left_connector_coverage(ink, groups[-1][-1].bot, u.top) >= CONNECTOR_MIN:
            groups[-1].append(u)
        else:
            groups.append([u])
    return groups


def _unit_geometry(ink: np.ndarray, units: list[Unit], W: int, H: int, t: float) -> None:
    """Crop boxes. Between two units, the upper one keeps the glyphs attached to its last staff
    (ledger notes, digits, labels right under the TAB) and the lower one gets everything else in
    the gap -- headings, tempo marks and section labels belong to the system they precede."""
    for u in units:
        top = u.top
        u.x0 = int(max(0, min(p.x0 for p in u.staves) - 2.5 * top.spacing))
        u.x1 = int(min(W, max(p.x1 for p in u.staves) + 2 * top.spacing))
    for k, u in enumerate(units):
        a = u.top
        if k == 0:  # first system on the page: keep a heading if one sits close above
            y_top = si.emptiest_band(ink, int(a.top_min - 14 * a.spacing), int(a.top_min - 7 * a.spacing), u.x0, u.x1)
            u.y0 = int(min(y_top, a.top_min - 1.0 * a.spacing))
        if k + 1 < len(units):
            v = units[k + 1]
            lo, hi = u.bot, v.top
            x0, x1 = min(u.x0, v.x0), max(u.x1, v.x1)
            # end of the upper unit's own glyphs (digits, ledger notes, stems) and start of the lower's
            a_end = lo.bottom_max + 0.6 * lo.spacing
            b_start = hi.top_min - 0.6 * hi.spacing
            for st, below, lim in ((lo, True, b_start), (hi, False, a_end)):
                led = si.ledger_reach(ink, st, below, x0, x1, t) if st.kind == "staff" else None
                extra = 0.0 if led is None else (led - st.bottom_max if below else st.top_min - led)
                reach = [led, si.attached_reach(ink, st, below, lim, x0, x1, body_extra=extra)]
                for r in reach:
                    if r is None:
                        continue
                    if below:
                        a_end = max(a_end, r + 0.15 * lo.spacing)
                    else:
                        b_start = min(b_start, r - 0.15 * hi.spacing)
            if a_end < b_start:
                # labels right under the upper unit (P, H, P.M. under a TAB) stay with it if they sit
                # closer than 0.75 spaces; everything further down belongs to the system below
                g = si.glyph_extent(ink, a_end, b_start, x0, x1, 0.75 * lo.spacing)
                if g:
                    y = g[0] + max(1, min((g[1] - g[0]) // 2, int(0.3 * lo.spacing)))
                else:
                    y = si.emptiest_band(ink, int(a_end), int(min(b_start, a_end + 1.0 * lo.spacing)), x0, x1)
                u.y1 = v.y0 = int(min(max(y, a_end), b_start))
            else:  # the two touch: best effort, never cut the staff/TAB lines themselves
                y = si.emptiest_band(ink, int(lo.bottom_max + 0.6 * lo.spacing), int(hi.top_min - 0.6 * hi.spacing),
                                     x0, x1)
                u.y1 = v.y0 = y
            if a_end < b_start:  # text between systems belongs to the system below (too): the lower
                v.y0 = int(min(v.y0, a_end + 0.1 * lo.spacing))  # crop starts right after the upper's glyphs
            u.y1 = int(math.ceil(max(u.y1, lo.bottom_max + 0.8 * lo.spacing)))
            v.y0 = int(math.floor(min(v.y0, hi.top_min - 0.8 * hi.spacing)))
        else:
            lo = u.bot
            down = si.glyph_extent(ink, lo.bottom_max + 0.3 * lo.spacing, lo.bottom_max + 5 * lo.spacing, u.x0, u.x1,
                                   0.75 * lo.spacing)
            end = down[0] + 0.3 * lo.spacing if down else lo.bottom_max + 5 * lo.spacing
            u.y1 = int(min(H, max(end, lo.bottom_max + 1.5 * lo.spacing)))
        u.y0 = int(max(0, u.y0))


def _staff_tab_split(ink: np.ndarray, staff: si.Staff, tab: si.Staff, x0: int, x1: int, t: float) -> tuple[int, str]:
    """Split row between the notation staff and its TAB, just below the staff's own glyphs:
    never through a ledger-line note or a stem/beam/flag attached to a note, and as close to those
    as possible so labels printed over the TAB (bend amounts, P.M., let ring) go with the TAB."""
    sp = staff.spacing
    lim = tab.top_min - 0.6 * tab.spacing
    floor = staff.bottom_max + 0.6 * sp
    led = si.ledger_reach(ink, staff, True, x0, x1, t)
    if led:
        floor = max(floor, led + 0.15 * sp)
    # glyphs touching the staff or its ledger-line zone (stems of low notes hang below the ledgers)
    att = si.attached_reach(ink, staff, True, lim, x0, x1, body_extra=(led - staff.bottom_max) if led else 0.0)
    if att and att + 0.2 * sp < lim:
        floor = max(floor, att + 0.2 * sp)
    if floor >= lim:  # staff glyphs reach the TAB's labels: keep the staff glyphs whole
        return int(lim), "as low as the TAB allows (staff glyphs reach the TAB -- check the split)"
    return si.emptiest_band(ink, int(floor), int(min(lim, floor + 1.0 * sp)), x0, x1), "just below the staff glyphs"


def _system_record(u: Unit, pi: int, ink: np.ndarray, csum: np.ndarray, t: float, gray_img: Image.Image,
                   work: Path) -> tuple[dict, tuple]:
    sid, staff, tab, kind = u.id, u.staff, u.tab, u.kind
    x0, x1, y0, y1 = u.x0, u.x1, u.y0, u.y1
    warn: list[str] = []
    rec: dict = {"id": sid, "page": pi, "kind": kind, "group": u.gid, "part": u.part, "parts_in_group": 1,
                 "bbox": [x0, y0, x1, y1]}
    if u.reason:
        rec["reason"] = u.reason
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

    tab_y0 = None
    if staff is not None and tab is not None:
        split, how = _staff_tab_split(ink, staff, tab, x0, x1, t)
        rec["split_method"] = how
        # a label printed over the TAB (bend amount, P.M.) that the split would cut goes whole into
        # the TAB crop: the TAB crop may then start a little above the split (crops can overlap)
        led = si.ledger_reach(ink, staff, True, x0, x1, t)
        tab_y0 = si.floating_top_across(ink, staff, split, x0, x1, (led - staff.bottom_max) if led else 0.0,
                                        tab.top_min - split)
        tab_y0 = int(max(tab_y0 - 2, staff.bottom_max + 0.6 * staff.spacing))
        if tab_y0 < split:
            rec["tab_crop_y0"] = tab_y0
        else:
            tab_y0 = split
    elif staff is not None:
        split = y1
    else:
        split = y0
    rec["split_y"] = split
    if tab_y0 is None:
        tab_y0 = split

    # barlines (tab staff is primary: nothing but barlines spans a tab staff vertically); a lone
    # notation staff gets none -- its note stems would be counted as barlines
    primary, other = (tab, staff) if tab is not None else (staff, None)
    if tab is not None:
        bls = si.find_barlines(ink, csum, primary, other, t)
        bars = si.bars_from_barlines(primary, bls, primary.spacing)
        bls, bars = _drop_opening_sliver(ink, u, bls, bars, t)
    else:
        bls, bars = [], []
    rec["barlines_x"] = [[round(b.xa), round(b.xb)] for b in bls]
    if any(b.dashed for b in bls):
        rec["barlines_dashed_x"] = [round(b.xa) for b in bls if b.dashed]
    rec["n_bars_detected"] = len(bars)
    if tab is not None and not bls:
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
        crops["tab"] = save(gray_img.crop((x0, tab_y0, x1, y1)), work / "crops" / f"{sid}_tab.png", work)
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
        for part, (ya, yb) in (("staff", (y0, split)), ("tab", (tab_y0, y1))):
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
    label = f"{sid}  [{kind}, {len(bars)} bars, {u.gid} part {u.part}]"
    return rec, (label, full, split - y0)


def _drop_opening_sliver(ink, u: Unit, bls, bars, t):
    """A first 'bar' that is only the margin left of the system's opening line (lines drawn a little
    past it, or a bracket) is not a bar: drop it together with the line that closes it."""
    if len(bars) < 2 or not bls:
        return bls, bars
    a, b = bars[0]
    d = u.bot.spacing
    if b - a >= 3.5 * d:
        return bls, bars
    if all(si.ink_fraction(ink, st, a + 0.3 * st.spacing, b - 0.3 * st.spacing, t) < 0.03 for st in u.staves):
        return bls[1:], bars[1:]
    return bls, bars


def _check_part_alignment(g: dict, by_id: dict, manifest: dict) -> None:
    """Stacked parts played together have their barlines at the same x. If not, refuse."""
    recs = [by_id[s] for s in g["systems"]]
    bl = [[(xa + xb) / 2 for xa, xb in r["barlines_x"]] for r in recs]
    tol = 1.5 * max(r.get("tab_space_px") or r.get("staff_space_px") or 10 for r in recs)
    counts = [len(x) for x in bl]
    aligned = len(set(counts)) == 1 and all(
        abs(p - q) <= tol for other in bl[1:] for p, q in zip(bl[0], other))
    g["barlines_aligned"] = aligned
    if not aligned:
        manifest["layout_errors"].append(
            f"{g['id']}: {g['parts']} staff+TAB pairs ({', '.join(g['systems'])}) are joined by one system line "
            f"(stacked parts played together?) but their barlines do not line up (detected bars: "
            f"{', '.join(str(len(r['bars'])) for r in recs)}) -- not guessing how they relate; check "
            f"contact/p{g['page']}.png and tell the user")


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


def draw_contact(gray_img: Image.Image, staves: list[si.Staff], grids: list[si.Staff], systems: list[dict],
                 dropped: list[dict], out: Path) -> None:
    W, H = gray_img.size
    sc = min(1.0, 1300 / W)
    im = gray_img.convert("RGB").resize((int(W * sc), int(H * sc)), Image.BILINEAR)
    dr = ImageDraw.Draw(im)
    for st in staves + grids:
        col = {"staff": (40, 90, 255), "tab": (230, 40, 40), "grid": (150, 150, 150)}.get(st.kind, (160, 160, 0))
        for ln in st.lines:
            dr.line([(ln.x0 * sc, ln.y_at(ln.x0) * sc), (ln.x1 * sc, ln.y_at(ln.x1) * sc)], fill=col, width=2)
    for x in dropped:
        x0, y0, x1, y1 = x["bbox"]
        dr.rectangle([x0 * sc, y0 * sc, x1 * sc, y1 * sc], outline=(130, 130, 130), width=3)
        for xa, xb in x.get("gap_barlines_x", []):  # the barlines that give a skipped system its bar count
            xm = (xa + xb) / 2 * sc
            dr.line([(xm, y0 * sc), (xm, y1 * sc)], fill=(255, 140, 0), width=2)
        dr.rectangle([x0 * sc, y0 * sc, x0 * sc + 260, y0 * sc + 22], fill=(110, 110, 110))
        dr.text((x0 * sc + 4, y0 * sc + 5), f"{x['id']} {x['role'].upper()} ({x['kind']})", fill=(255, 255, 255))
    for s in systems:
        x0, y0, x1, y1 = s["bbox"]
        dr.rectangle([x0 * sc, y0 * sc, x1 * sc, y1 * sc], outline=(0, 170, 0), width=3)
        dr.line([(x0 * sc, s["split_y"] * sc), (x1 * sc, s["split_y"] * sc)], fill=(200, 0, 200), width=2)
        for xa, xb in s["barlines_x"]:
            xm = (xa + xb) / 2 * sc
            dr.line([(xm, y0 * sc), (xm, y1 * sc)], fill=(255, 140, 0), width=2)
        dr.rectangle([x0 * sc, y0 * sc, x0 * sc + 300, y0 * sc + 22], fill=(0, 120, 0))
        part = f" part {s['part']}/{s['parts_in_group']}" if s.get("parts_in_group", 1) > 1 else ""
        dr.text((x0 * sc + 4, y0 * sc + 5), f"{s['id']}{part} {s['kind']} {s['n_bars_detected']} bars",
                fill=(255, 255, 255))
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
        if split >= 0:
            dr.line([(0, split * sc), (target_w, split * sc)], fill=(200, 0, 200), width=2)
        head = Image.new("RGB", (target_w, 24), (0, 110, 0) if split >= 0 else (110, 110, 110))
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
