"""Shared image utilities for scan-to-gp (imported by prepare.py and inspect.py).

Everything here is deterministic: page extraction, binarization, deskew,
staff/tab line detection, staff grouping and barline detection. The LLM
readers never see this code's guesses as truth -- they are crops and hints.

Coordinate conventions: images are numpy arrays indexed [y, x]; `ink` is a
boolean array (True = dark). `d` is the line spacing (distance between two
adjacent staff/tab lines, center to center) in pixels; `t` is line thickness.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
from scipy import ndimage as ndi

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif"}


# ---------------------------------------------------------------- page input


@dataclass
class Page:
    source: str  # input file basename
    source_page: int  # 1-based page/frame inside that file
    image: Image.Image  # grayscale "L"
    extraction: str  # how the pixels were obtained


def load_pages(paths: list[str], render_dpi: int = 400) -> list[Page]:
    pages: list[Page] = []
    for p in paths:
        path = Path(p)
        ext = path.suffix.lower()
        if ext == ".pdf":
            pages.extend(_load_pdf(path, render_dpi))
        elif ext in IMAGE_EXTS:
            im = Image.open(path)
            n = getattr(im, "n_frames", 1)
            for i in range(n):
                im.seek(i)
                fr = ImageOps.exif_transpose(im.copy()) or im.copy()
                pages.append(Page(path.name, i + 1, _to_gray(fr), "image-file"))
        else:
            raise SystemExit(f"unsupported input type: {path.name} (want .pdf or an image: {sorted(IMAGE_EXTS)})")
    return pages


def _to_gray(im: Image.Image) -> Image.Image:
    if im.mode in ("I;16", "I;16B", "I;16L", "I;16N", "I", "F"):
        a = np.asarray(im).astype(np.float64)
        top = 65535.0 if im.mode.startswith("I;16") else max(1.0, float(np.percentile(a, 99.9)))
        return Image.fromarray(np.clip(a / top * 255.0, 0, 255).astype(np.uint8), "L")
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, im)
    return im.convert("L")


def _load_pdf(path: Path, render_dpi: int) -> list[Page]:
    import pymupdf

    out: list[Page] = []
    doc = pymupdf.open(path)
    for pno, page in enumerate(doc):
        infos = page.get_image_info(xrefs=True)
        page_area = page.rect.width * page.rect.height
        big = [i for i in infos if _rect_area(i["bbox"]) >= 0.85 * page_area]
        has_text = bool(page.get_text().strip())
        if len(infos) == 1 and big and not has_text and page.rotation == 0 and _axis_aligned(big[0]["transform"]):
            xref = big[0]["xref"]
            try:
                pix = pymupdf.Pixmap(doc, xref)
                if pix.alpha:
                    pix = pymupdf.Pixmap(pix, 0)
                if pix.n != 1:
                    pix = pymupdf.Pixmap(pymupdf.csGRAY, pix)
                im = Image.frombytes("L", (pix.width, pix.height), pix.samples)
                tr = big[0]["transform"]  # PyMuPDF reports it in y-down page space: a, d > 0 = upright
                if tr[0] < 0:
                    im = ImageOps.mirror(im)
                if tr[3] < 0:
                    im = ImageOps.flip(im)
                out.append(Page(path.name, pno + 1, im, "embedded-image (native resolution)"))
                continue
            except Exception:  # noqa: BLE001 - fall back to rendering
                pass
        dpi = render_dpi
        if infos:
            # render at the native resolution of the largest image so we neither lose nor invent pixels
            biggest = max(infos, key=lambda i: _rect_area(i["bbox"]))
            bw_pt = max(1.0, biggest["bbox"][2] - biggest["bbox"][0])
            native = biggest["width"] / (bw_pt / 72.0)
            dpi = int(min(600, max(200, native)))
        pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
        im = Image.frombytes("L", (pix.width, pix.height), pix.samples)
        out.append(Page(path.name, pno + 1, im, f"rendered at {dpi} dpi"))
    return out


def _rect_area(b) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _axis_aligned(tr) -> bool:
    a, b, c, d = tr[:4]
    return abs(b) < 1e-6 and abs(c) < 1e-6 and abs(a) > 0 and abs(d) > 0


# --------------------------------------------------------------- binarize


def binarize(gray: np.ndarray, window: int | None = None, k: float = 0.2) -> np.ndarray:
    """Sauvola thresholding: tolerant of uneven lighting (photos) and faint lines."""
    g = gray.astype(np.float32)
    if window is None:
        window = max(25, (min(g.shape) // 40) | 1)
    mean = ndi.uniform_filter(g, window)
    sq = ndi.uniform_filter(g * g, window)
    std = np.sqrt(np.maximum(sq - mean * mean, 0))
    thr = mean * (1 + k * (std / 128.0 - 1))
    # never call near-white pixels ink, never call near-black pixels paper
    return ((g < thr) & (g < 235)) | (g < 60)


# ----------------------------------------------------------------- deskew


def estimate_skew(ink: np.ndarray, max_deg: float = 5.0) -> float:
    """Angle (degrees, PIL `rotate` convention) that makes horizontal lines horizontal.

    Maximizes the energy of the horizontal projection over candidate angles
    (coarse then fine grid). The projection for a candidate slope b is a
    bincount of y - b*x over ink pixels of a downscaled copy (a shear, which
    for a few degrees is indistinguishable from a rotation).
    """
    h, w = ink.shape
    scale = min(1.0, 1400.0 / w)
    small = np.asarray(Image.fromarray((ink * 255).astype(np.uint8)).resize(
        (max(1, int(w * scale)), max(1, int(h * scale))), Image.BOX)) > 64
    ys, xs = np.nonzero(small)
    if len(ys) < 100:
        return 0.0
    xs = xs - small.shape[1] / 2.0
    off = int(abs(math.tan(math.radians(max_deg + 1))) * small.shape[1]) + 2

    def score(a: float) -> float:
        idx = np.round(ys - math.tan(math.radians(a)) * xs).astype(np.int64) + off
        prof = np.bincount(idx).astype(np.float64)
        return float((prof * prof).sum())

    coarse = np.arange(-max_deg, max_deg + 1e-9, 0.1)
    best = max(coarse, key=score)
    fine = np.arange(best - 0.12, best + 0.12 + 1e-9, 0.01)
    return float(max(fine, key=score))


def rotate_gray(gray: Image.Image, angle: float) -> Image.Image:
    if abs(angle) < 0.01:
        return gray
    return gray.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=255)


# --------------------------------------------------------- spacing estimate


def estimate_spacing(ink: np.ndarray) -> tuple[float, float]:
    """(d, t): most common staff-line distance and line thickness from vertical run lengths."""
    h, w = ink.shape
    cols = ink[:, :: max(1, w // 600)].T.astype(np.int8)
    black_hist = np.zeros(h + 2, np.int64)
    white_hist = np.zeros(h + 2, np.int64)
    for col in cols:
        dif = np.diff(np.concatenate(([0], col, [0])))
        starts = np.flatnonzero(dif == 1)
        ends = np.flatnonzero(dif == -1)
        if len(starts) < 2:
            continue
        blen = ends - starts
        np.add.at(black_hist, np.minimum(blen, h + 1), 1)
        wlen = starts[1:] - ends[:-1]
        np.add.at(white_hist, np.minimum(wlen, h + 1), 1)
    t = int(np.argmax(black_hist[1:40])) + 1
    white_hist[: max(2, t)] = 0
    white_hist[200:] = 0
    s = int(np.argmax(white_hist))
    return float(s + t), float(t)


# ----------------------------------------------------------- line detection


@dataclass
class Line:
    xs: list[float]
    ys: list[float]
    thick: list[float]
    a: float = 0.0  # y = a + b*x
    b: float = 0.0
    x0: float = 0.0
    x1: float = 0.0
    interpolated: bool = False

    def y_at(self, x: float) -> float:
        return self.a + self.b * x

    @property
    def y_mid(self) -> float:
        return self.y_at((self.x0 + self.x1) / 2)

    def fit(self) -> None:
        xs, ys = np.asarray(self.xs), np.asarray(self.ys)
        if len(xs) >= 2 and np.ptp(xs) > 0:
            self.b, self.a = np.polyfit(xs, ys, 1)
        else:
            self.a, self.b = float(ys.mean()), 0.0

    def wobble(self) -> float:
        xs, ys = np.asarray(self.xs), np.asarray(self.ys)
        return float(np.max(np.abs(ys - (self.a + self.b * xs)))) if len(xs) else 0.0


def detect_lines(ink: np.ndarray, d: float, t: float, min_len: float | None = None) -> list[Line]:
    """Find long, thin, roughly horizontal lines via strip projections + linking.

    Works locally (per vertical strip) so residual skew, mild warping and
    short systems (lines that do not span the page) are all tolerated.
    """
    h, w = ink.shape
    run = max(9, int(round(2.5 * d))) | 1
    max_thick = min(2 * t + 2, max(t + 2, 0.45 * d))
    # keep only thin horizontal structure: drop pixels in vertical runs thicker than a
    # staff line (beams, noteheads, stems, text) ...
    thick_len = int(math.ceil(max_thick)) + 1
    thick = ndi.maximum_filter1d(ndi.minimum_filter1d(ink.astype(np.uint8), size=thick_len, axis=0),
                                 size=thick_len, axis=0).astype(bool)
    thin = (ink & ~thick).astype(np.uint8)
    # ... re-bridge the small gaps that removed stems leave in a line ...
    bridge = int(2 * t + 3) | 1
    thin = ndi.minimum_filter1d(ndi.maximum_filter1d(thin, size=bridge, axis=1), size=bridge, axis=1).astype(bool)
    # ... then keep only long horizontal runs
    horiz = horizontal_opening(thin, run)
    sw = max(40, int(round(8 * d)))
    n_strips = max(3, w // sw)
    edges = np.linspace(0, w, n_strips + 1).astype(int)
    cands: list[list[tuple[float, float]]] = []
    for k in range(n_strips):
        cov = horiz[:, edges[k] : edges[k + 1]].mean(axis=1)
        on = cov >= 0.22
        lab, n = ndi.label(on)
        found = []
        if n:
            idx = np.arange(h)
            for r in ndi.find_objects(lab):
                y0, y1 = r[0].start, r[0].stop
                th = y1 - y0
                if th > max_thick:
                    continue
                wts = cov[y0:y1]
                found.append((float((idx[y0:y1] * wts).sum() / wts.sum()), float(th)))
        cands.append(found)

    xc = [(edges[k] + edges[k + 1]) / 2 for k in range(n_strips)]
    tol = max(2.0, 0.3 * d)
    active: list[tuple[Line, int]] = []  # (line, last strip index)
    done: list[Line] = []
    for k in range(n_strips):
        still = []
        for ln, last in active:
            if k - last > 3:
                done.append(ln)
            else:
                still.append((ln, last))
        active = still
        pairs = []
        for ci, (y, th) in enumerate(cands[k]):
            for ai, (ln, last) in enumerate(active):
                if len(ln.xs) >= 2:
                    slope = (ln.ys[-1] - ln.ys[0]) / max(1.0, ln.xs[-1] - ln.xs[0])
                else:
                    slope = 0.0
                pred = ln.ys[-1] + slope * (xc[k] - ln.xs[-1])
                dist = abs(pred - y)
                if dist <= tol:
                    pairs.append((dist, ci, ai))
        pairs.sort()
        used_c, used_a = set(), set()
        for dist, ci, ai in pairs:
            if ci in used_c or ai in used_a:
                continue
            used_c.add(ci)
            used_a.add(ai)
            ln, _ = active[ai]
            ln.xs.append(xc[k])
            ln.ys.append(cands[k][ci][0])
            ln.thick.append(cands[k][ci][1])
            active[ai] = (ln, k)
        for ci, (y, th) in enumerate(cands[k]):
            if ci not in used_c:
                active.append((Line([xc[k]], [y], [th]), k))
    done.extend(ln for ln, _ in active)

    lines = [ln for ln in done if len(ln.xs) >= 3]
    for ln in lines:
        ln.fit()
        _refine_extent(ln, ink, d, t, edges)
    # drop lines shorter than ~25 line-spaces (ledger lines, brackets, beams) and
    # lines seen in too few of the strips they claim to cross (beam fragments
    # whose extent was inflated by neighbouring ink)
    sw_eff = w / n_strips
    min_len = 12 * d if min_len is None else min_len  # ~1-2 bars: ragged last systems are short
    lines = [ln for ln in lines
             if ln.x1 - ln.x0 >= min_len and len(ln.xs) >= 0.5 * max(1.0, (ln.x1 - ln.x0) / sw_eff - 1)]
    lines.sort(key=lambda ln: ln.y_mid)
    return lines


def horizontal_opening(ink: np.ndarray, run: int) -> np.ndarray:
    """Binary opening with a 1 x run horizontal element (O(N) min/max filters)."""
    u = ink.astype(np.uint8)
    er = ndi.minimum_filter1d(u, size=run, axis=1)
    return ndi.maximum_filter1d(er, size=run, axis=1).astype(bool)


def _refine_extent(ln: Line, ink: np.ndarray, d: float, t: float, edges) -> None:
    """Walk along the fitted line from its first/last strip to find where ink really stops."""
    h, w = ink.shape
    band = max(1, int(math.ceil(t)) + 1)
    xs = np.arange(w)
    ys = np.clip(np.round(ln.a + ln.b * xs).astype(int), 0, h - 1)
    hit = np.zeros(w, bool)
    for dy in range(-band, band + 1):
        hit |= ink[np.clip(ys + dy, 0, h - 1), xs]
    gap_tol = int(2.5 * d)
    start, end = int(min(ln.xs)), int(max(ln.xs))
    x = start
    gap = 0
    x0 = start
    while x > 0:
        x -= 1
        if hit[x]:
            x0, gap = x, 0
        else:
            gap += 1
            if gap > gap_tol:
                break
    x = end
    gap = 0
    x1 = end
    while x < w - 1:
        x += 1
        if hit[x]:
            x1, gap = x, 0
        else:
            gap += 1
            if gap > gap_tol:
                break
    ln.x0, ln.x1 = float(x0), float(x1)


# ----------------------------------------------------------------- staves


@dataclass
class Staff:
    lines: list[Line]
    kind: str  # "staff" (5 lines) | "tab" (4/6/7 lines) | "unknown"
    spacing: float
    x0: float
    x1: float
    notes: list[str] = field(default_factory=list)

    @property
    def top(self) -> float:
        return self.lines[0].y_at((self.x0 + self.x1) / 2)

    @property
    def bottom(self) -> float:
        return self.lines[-1].y_at((self.x0 + self.x1) / 2)

    def y_top(self, x):
        return self.lines[0].a + self.lines[0].b * x

    def y_bot(self, x):
        return self.lines[-1].a + self.lines[-1].b * x


def _gap(a: Line, b: Line) -> tuple[float, float]:
    """Vertical distance b - a over their common x-range, plus overlap as a fraction of the LONGER
    line (lines of one staff span the same x-range; a short bracket inside a staff does not)."""
    lo, hi = max(a.x0, b.x0), min(a.x1, b.x1)
    ov = max(0.0, hi - lo) / max(1.0, max(a.x1 - a.x0, b.x1 - b.x0))
    xm = (lo + hi) / 2 if hi > lo else (a.x0 + a.x1) / 2
    return b.y_at(xm) - a.y_at(xm), ov


def group_staves(lines: list[Line], d: float) -> list[Staff]:
    """Group lines into staves: maximal arithmetic progressions of equally spaced lines.

    For each unused line we try every plausible next line as the spacing and keep
    the progression that collects the most lines (one missing line may be
    interpolated). Stray lines inside a staff (beam fragments) are discarded.
    """
    n = len(lines)
    used = [False] * n
    staves: list[Staff] = []
    i = 0
    while i < n:
        if used[i]:
            i += 1
            continue
        best = None
        for j in range(i + 1, n):
            gap, ov = _gap(lines[i], lines[j])
            if gap > 2.8 * d:
                break
            if gap < 0.4 * d or ov < 0.6:
                continue
            members, interp = _progression(lines, used, i, j, gap)
            real = [m for m in members if m is not None]
            skipped = sum(1 for k in range(real[0] + 1, real[-1]) if k not in real and not used[k])
            # more real lines is better; skipping over unexplained lines between members is
            # heavily penalized (a too-wide spacing that straddles another staff);
            # then fewer interpolations; then the tighter spacing
            key = (len(real) - 1.5 * skipped, -interp, -gap)
            if best is None or key > best[0]:
                best = (key, members, interp, len(real))
        if best is None or best[3] < 4:
            used[i] = True
            i += 1
            continue
        _, members, interp, _nreal = best
        group = _materialize(lines, members)
        for m in members:
            if m is not None:
                used[m] = True
        lo, hi = members[0], [m for m in members if m is not None][-1]
        for k in range(lo, hi + 1):
            used[k] = True  # anything between the outer lines is part of this staff
        notes = ["one faint line was interpolated"] if interp else []
        sp = float(np.median([_gap(group[q], group[q + 1])[0] for q in range(len(group) - 1)]))
        x0 = float(np.median([ln.x0 for ln in group]))
        x1 = float(np.median([ln.x1 for ln in group]))
        nl = len(group)
        kind = "staff" if nl == 5 else ("tab" if nl in (4, 6, 7) else "unknown")
        staves.append(Staff(group, kind, sp, x0, x1, notes))
        i = hi + 1
    return staves


def _progression(lines: list[Line], used: list[bool], i: int, j: int, sp: float) -> tuple[list, int]:
    """Indices of lines continuing y_i + k*sp (None = interpolated); allows one missing line."""
    members: list = [i, j]
    interp = 0
    last = j
    while len(members) < 8:
        nxt = None
        want_gap = sp
        for k in range(last + 1, len(lines)):
            if used[k]:
                continue
            g, ov = _gap(lines[last], lines[k])
            if g > 2.3 * sp:
                break
            if ov >= 0.6 and abs(g - sp) <= 0.12 * sp:
                nxt = k
                break
        if nxt is None and interp == 0:
            for k in range(last + 1, len(lines)):
                if used[k]:
                    continue
                g, ov = _gap(lines[last], lines[k])
                if g > 2.3 * sp:
                    break
                if ov >= 0.6 and abs(g - 2 * sp) <= 0.12 * sp:
                    nxt = k
                    want_gap = 2 * sp
                    break
            if nxt is not None:
                members.append(None)
                interp = 1
        if nxt is None:
            break
        _ = want_gap
        members.append(nxt)
        last = nxt
    if members and members[-1] is None:
        members.pop()
    return members, interp


def _materialize(lines: list[Line], members: list) -> list[Line]:
    out: list[Line] = []
    for q, m in enumerate(members):
        if m is not None:
            out.append(lines[m])
        else:
            a, b = lines[members[q - 1]], lines[members[q + 1]]
            out.append(Line([], [], [], a=(a.a + b.a) / 2, b=(a.b + b.b) / 2,
                            x0=max(a.x0, b.x0), x1=min(a.x1, b.x1), interpolated=True))
    return out


# ---------------------------------------------------------------- barlines


@dataclass
class Barline:
    xa: float
    xb: float
    runs: int  # 1 = single thin/thick line, 2+ = double / final / repeat
    staff_confirmed: bool


def column_coverage(ink: np.ndarray, csum: np.ndarray, st: Staff, x0: int, x1: int, margin: float) -> np.ndarray:
    xs = np.arange(x0, x1)
    yt = np.clip(np.round(st.y_top(xs) + margin).astype(int), 0, ink.shape[0])
    yb = np.clip(np.round(st.y_bot(xs) - margin).astype(int), 0, ink.shape[0])
    hgt = np.maximum(1, yb - yt)
    return (csum[yb, xs] - csum[yt, xs]) / hgt


def find_barlines(ink: np.ndarray, csum: np.ndarray, primary: Staff, other: Staff | None, t: float) -> list[Barline]:
    """Barlines = thin columns that are (almost) fully inked across the primary staff's height.

    `primary` should be the tab staff when present (no stems cross a tab staff);
    `other` (the notation staff) is used only to confirm and to accept candidates
    in the clef zone.
    """
    d = primary.spacing
    x0, x1 = int(max(0, primary.x0 - d)), int(min(ink.shape[1], primary.x1 + d))
    cov = column_coverage(ink, csum, primary, x0, x1, 0.2 * d)
    on = cov >= 0.9
    lab, n = ndi.label(on)
    runs = []
    for r in ndi.find_objects(lab):
        a, b = r[0].start + x0, r[0].stop + x0
        if (b - a) <= max(4 * t, 0.75 * d):
            runs.append((a, b))
    clusters: list[list[tuple[int, int]]] = []
    for a, b in runs:
        if clusters and a - clusters[-1][-1][1] <= 1.6 * d:
            clusters[-1].append((a, b))
        else:
            clusters.append([(a, b)])
    out = []
    ocov = None
    if other is not None:
        ocov_x0, ocov_x1 = x0, x1
        ocov = column_coverage(ink, csum, other, ocov_x0, ocov_x1, 0.2 * other.spacing)
    for cl in clusters:
        xa, xb = cl[0][0], cl[-1][1]
        conf = False
        if ocov is not None:
            lo, hi = int(max(0, xa - 0.5 * d - x0)), int(min(len(ocov), xb + 0.5 * d - x0))
            conf = bool(hi > lo and ocov[lo:hi].max() >= 0.9)
        if xb < primary.x0 + 1.2 * d:
            continue  # the system's opening line, not a bar boundary
        if xa < primary.x0 + 4.0 * d and not conf:
            continue  # clef zone ("TAB" letters) unless the notation staff agrees
        out.append(Barline(float(xa), float(xb), len(cl), conf))
    return out


def bars_from_barlines(st: Staff, bls: list[Barline], d: float) -> list[tuple[float, float]]:
    bars = []
    prev = st.x0
    for bl in bls:
        if bl.xa - prev > 1.0 * d:
            bars.append((prev, bl.xa))
        prev = bl.xb
    if st.x1 - prev > 2.5 * d:
        bars.append((prev, st.x1))
    return bars


def emptiest_band(ink: np.ndarray, y0: int, y1: int, x0: int, x1: int) -> int:
    """Row in [y0, y1) at the centre of the widest band of minimum ink."""
    H = ink.shape[0]
    y0, y1 = max(0, min(H, y0)), max(0, min(H, y1))
    if y1 - y0 < 3:
        return max(0, min(H - 1, (y0 + y1) // 2))
    prof = ink[y0:y1, x0:x1].sum(axis=1).astype(float)
    prof = ndi.uniform_filter1d(prof, 3)
    thr = prof.min() + max(1.0, 0.002 * (x1 - x0))
    low = prof <= thr
    lab, n = ndi.label(low)
    best, best_len = (y0 + y1) // 2, -1
    for r in ndi.find_objects(lab):
        a, b = r[0].start, r[0].stop
        if b - a > best_len:
            best_len, best = b - a, y0 + (a + b) // 2
    return int(best)
