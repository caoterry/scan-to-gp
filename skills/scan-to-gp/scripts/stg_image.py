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
import re
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
    number: int = 0  # 1-based page number across all inputs, in reading order
    inverted: bool = False  # the input was white-on-black and has been inverted


def parse_page_spec(spec: str | None) -> set[int] | None:
    """'1,3-4' -> {1, 3, 4} (1-based, across all inputs in reading order); None/'' -> all pages."""
    if spec is None or not str(spec).strip():
        return None
    out: set[int] = set()
    for part in str(spec).split(","):
        part = part.strip()
        m = re.fullmatch(r"(\d+)\s*(?:-\s*(\d+))?", part)
        if not m:
            raise ValueError(f"bad page selector {part!r} (want e.g. 1,3-4)")
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        if a < 1 or b < a:
            raise ValueError(f"bad page range {part!r}")
        out.update(range(a, b + 1))
    return out


def count_pages(paths: list[str]) -> int:
    n = 0
    for p in paths:
        path = Path(p)
        if path.suffix.lower() == ".pdf":
            import pymupdf
            with pymupdf.open(path) as doc:
                n += len(doc)
        elif path.suffix.lower() in IMAGE_EXTS:
            with Image.open(path) as im:
                n += getattr(im, "n_frames", 1)
    return n


def load_pages(paths: list[str], render_dpi: int = 400, select: set[int] | None = None) -> list[Page]:
    """Load every page/frame of the inputs in reading order (only the `select`ed 1-based page
    numbers, counted across all inputs, when given). White-on-black pages are inverted."""
    pages: list[Page] = []
    number = 0
    for p in paths:
        path = Path(p)
        ext = path.suffix.lower()
        if ext == ".pdf":
            got, number = _load_pdf(path, render_dpi, number, select)
            pages.extend(got)
        elif ext in IMAGE_EXTS:
            im = Image.open(path)
            n = getattr(im, "n_frames", 1)
            for i in range(n):
                number += 1
                if select is not None and number not in select:
                    continue
                im.seek(i)
                fr = ImageOps.exif_transpose(im.copy()) or im.copy()
                pages.append(Page(path.name, i + 1, _to_gray(fr), "image-file", number))
        else:
            raise SystemExit(f"unsupported input type: {path.name} (want .pdf or an image: {sorted(IMAGE_EXTS)})")
    for pg in pages:
        _fix_inversion(pg)
    return pages


def looks_inverted(gray: Image.Image) -> bool:
    """White-on-black: a music page is mostly paper, so a dark median means the polarity is flipped
    (1-bit ImageMask / stencil scans often come out this way)."""
    small = gray.copy()
    small.thumbnail((800, 800))
    a = np.asarray(small)
    return float(np.median(a)) < 100 and float(a.mean()) < 128


def _fix_inversion(pg: Page) -> None:
    if looks_inverted(pg.image):
        pg.image = ImageOps.invert(pg.image)
        pg.inverted = True
        pg.extraction += "; inverted (input was white-on-black)"


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


def _load_pdf(path: Path, render_dpi: int, number: int = 0,
              select: set[int] | None = None) -> tuple[list[Page], int]:
    import pymupdf

    out: list[Page] = []
    doc = pymupdf.open(path)
    for pno, page in enumerate(doc):
        number += 1
        if select is not None and number not in select:
            continue
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
                out.append(Page(path.name, pno + 1, im, "embedded-image (native resolution)", number))
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
        out.append(Page(path.name, pno + 1, im, f"rendered at {dpi} dpi", number))
    return out, number


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

    @property
    def top_min(self) -> float:
        """Highest point of the top line anywhere along the staff (residual skew / warp)."""
        return float(min(self.y_top(self.x0), self.y_top(self.x1), self.top))

    @property
    def bottom_max(self) -> float:
        return float(max(self.y_bot(self.x0), self.y_bot(self.x1), self.bottom))


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
    """Indices of lines continuing y_i + k*sp (None = interpolated); allows one missing line.

    The next gap may drift from the previous one by 12% (scans and photos stretch a staff unevenly:
    47..57 px gaps inside one tab staff are real) but never beyond 0.8..1.3 x the first gap. A
    missing line (a double gap) must match twice the MEAN gap tightly, so a staff is not glued to
    the next staff across the white space between them.
    """
    members: list = [i, j]
    interp = 0
    last = j
    prev_gap = sp
    gaps_seen = [sp]

    def in_range(g: float) -> bool:
        return 0.8 * sp <= g <= 1.3 * sp

    while len(members) < 8:
        nxt = None
        for k in range(last + 1, len(lines)):
            if used[k]:
                continue
            g, ov = _gap(lines[last], lines[k])
            if g > 2.3 * prev_gap:
                break
            if ov >= 0.6 and abs(g - prev_gap) <= 0.12 * prev_gap and in_range(g):
                nxt = k
                break
        if nxt is None and interp == 0:
            mean = float(np.mean(gaps_seen))
            for k in range(last + 1, len(lines)):
                if used[k]:
                    continue
                g, ov = _gap(lines[last], lines[k])
                if g > 2.3 * mean:
                    break
                if ov >= 0.6 and abs(g - 2 * mean) <= 0.12 * mean and in_range(g / 2):
                    nxt = k
                    break
            if nxt is not None:
                members.append(None)
                interp = 1
        if nxt is None:
            break
        g, _ = _gap(lines[last], lines[nxt])
        if members[-1] is not None:
            prev_gap = g
            gaps_seen.append(g)
        members.append(nxt)
        last = nxt
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
    dashed: bool = False


def column_coverage(ink: np.ndarray, csum: np.ndarray, st: Staff, x0: int, x1: int, margin: float) -> np.ndarray:
    xs = np.arange(x0, x1)
    yt = np.clip(np.round(st.y_top(xs) + margin).astype(int), 0, ink.shape[0])
    yb = np.clip(np.round(st.y_bot(xs) - margin).astype(int), 0, ink.shape[0])
    hgt = np.maximum(1, yb - yt)
    return (csum[yb, xs] - csum[yt, xs]) / hgt


def _line_rows(st: Staff, x: int, t: float) -> np.ndarray:
    """Row offsets (relative to the staff's top line at x, rounded) covered by the staff's own lines."""
    top = st.y_top(x)
    rows = []
    half = max(1.0, 0.5 * t + 1.0)
    for ln in st.lines:
        yc = ln.y_at(x) - top
        rows.extend(range(int(math.floor(yc - half)), int(math.ceil(yc + half)) + 1))
    return np.array(sorted(set(rows)), dtype=int)


def dashed_column(ink: np.ndarray, st: Staff, x: int, t: float) -> bool:
    """A dashed barline at column x: with the staff's own lines removed, the column (+-1 px) shows
    >= 3 dashes running from the top line to the bottom line, separated by short gaps (< 0.9 line
    spaces), no single dash covering most of it (a stem / solid line), and nothing else nearby
    (stacked digits, noteheads and parentheses are wide; a barline stands alone)."""
    d = st.spacing
    h, w = ink.shape
    side = int(math.ceil(0.45 * d))
    if not side + 2 <= x < w - side - 2:
        return False
    y0 = int(round(st.y_top(x)))
    y1 = int(round(st.y_bot(x)))
    if y0 < 0 or y1 >= h or y1 - y0 < 2 * d:
        return False
    keep = np.ones(y1 + 1 - y0, bool)
    lr = _line_rows(st, x, t)
    keep[lr[(lr >= 0) & (lr < len(keep))]] = False
    col = ink[y0:y1 + 1, x - 1:x + 2].any(axis=1)
    seq = col[keep]
    if len(seq) < 4 or seq.mean() < 0.3 or seq.mean() >= 0.92:
        return False
    dif = np.diff(np.concatenate(([0], seq.astype(np.int8), [0])))
    starts, ends = np.flatnonzero(dif == 1), np.flatnonzero(dif == -1)
    if len(starts) < 3:
        return False
    gaps = starts[1:] - ends[:-1]
    n = len(seq)
    if not (gaps.max() <= 0.9 * d and starts[0] <= 0.4 * d and n - ends[-1] <= 0.4 * d
            and (ends - starts).max() <= 0.6 * n):
        return False
    win = ink[y0:y1 + 1, x - side:x + side + 1][keep]
    sides = np.concatenate([win[:, :side - 2], win[:, side + 3:]], axis=1)
    return float(sides.mean()) < 0.12


def find_barlines(ink: np.ndarray, csum: np.ndarray, primary: Staff, other: Staff | None, t: float) -> list[Barline]:
    """Barlines = thin columns that are (almost) fully inked across the primary staff's height.

    `primary` should be the tab staff when present (no stems cross a tab staff);
    `other` (the notation staff) is used only to confirm and to accept candidates
    in the clef zone. Dashed barlines (common in book scans for half-bar divisions or
    "free time" passages) are accepted only when BOTH staves show the dash pattern at the
    same x (a column of stacked tab digits can look dashed; the notation above it cannot).
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
            runs.append((a, b, False))
    if other is not None:
        cand = np.flatnonzero((cov >= 0.25) & ~on) + x0
        dashed = np.zeros(x1 - x0, bool)
        for x in cand:
            if dashed_column(ink, primary, int(x), t):
                dashed[x - x0] = True
        lab, n = ndi.label(dashed)
        for r in ndi.find_objects(lab):
            a, b = r[0].start + x0, r[0].stop + x0
            if (b - a) > max(3 * t, 0.35 * d) or a < primary.x0 + 4.0 * d:
                continue  # too wide for a dashed line, or in the clef zone (clef curls / "TAB" letters)
            xm = (a + b) // 2
            if any(dashed_column(ink, other, int(xx), t) for xx in range(int(xm - 0.4 * d), int(xm + 0.4 * d) + 1)):
                runs.append((a, b, True))
        runs.sort()
    clusters: list[list[tuple[int, int, bool]]] = []
    for a, b, dsh in runs:
        if clusters and a - clusters[-1][-1][1] <= 1.6 * d:
            clusters[-1].append((a, b, dsh))
        else:
            clusters.append([(a, b, dsh)])
    out = []
    ocov = None
    if other is not None:
        ocov_x0, ocov_x1 = x0, x1
        ocov = column_coverage(ink, csum, other, ocov_x0, ocov_x1, 0.2 * other.spacing)
    for cl in clusters:
        xa, xb = cl[0][0], cl[-1][1]
        is_dashed = all(c[2] for c in cl)
        conf = is_dashed  # a dashed barline was already confirmed in both staves
        if ocov is not None and not conf:
            lo, hi = int(max(0, xa - 0.5 * d - x0)), int(min(len(ocov), xb + 0.5 * d - x0))
            conf = bool(hi > lo and ocov[lo:hi].max() >= 0.9)
        if xb < primary.x0 + 1.2 * d:
            continue  # the system's opening line, not a bar boundary
        if xa < primary.x0 + 4.0 * d and (is_dashed or not conf):
            continue  # clef zone ("TAB" letters, clef curls look dashed) unless the notation staff agrees
        out.append(Barline(float(xa), float(xb), len(cl), conf, is_dashed))
    return out


def _run_through(col: np.ndarray, y: int, step: int, max_gap: int) -> int:
    """Last inked row reached walking from row y in direction step, bridging gaps of <= max_gap rows."""
    n = len(col)
    last, gap, yy = y, 0, y
    while 0 <= yy + step < n:
        yy += step
        if col[yy]:
            last, gap = yy, 0
        else:
            gap += 1
            if gap > max_gap:
                break
    return last


def _vertical_runs(col: np.ndarray) -> list[tuple[int, int]]:
    dif = np.diff(np.concatenate(([0], col.astype(np.int8), [0])))
    return list(zip(np.flatnonzero(dif == 1).tolist(), np.flatnonzero(dif == -1).tolist()))


def _multirest_bar(ink: np.ndarray, st: Staff, t: float) -> int | None:
    """x of a multi-bar rest symbol (its bar count is a printed number, so the barlines undercount): a thick
    (0.35-1.0 spaces), level bar centred on the staff's middle line over >= 2 spaces, with no stem rising
    or falling from its interior (a beam has stems; a whole / half rest is narrower). A level beam whose
    stems sit only at its ends is caught too, which only makes the bar count doubtful. None if absent."""
    d = st.spacing
    H, W = ink.shape
    mid_line = st.lines[len(st.lines) // 2]
    x_from, x_to = int(max(0, st.x0 + 2 * d)), int(min(W, st.x1))
    cols: list[tuple[int, int, int]] = []  # (x, top, bottom) of the thick run on the middle line, per column

    def flush() -> int | None:
        if cols and cols[-1][0] - cols[0][0] + 1 >= 2 * d:
            inner = [c for c in cols if cols[0][0] + 0.4 * d <= c[0] <= cols[-1][0] - 0.4 * d]
            stems = 0
            for x, top, bot in inner:
                col = ink[:, x]
                up = bool(col[int(max(0, top - 0.6 * d)):top].all()) if top > 0.6 * d else False
                dn = bool(col[bot:int(min(H, bot + 0.6 * d))].all())
                stems += up or dn
            if stems <= 1:
                return (cols[0][0] + cols[-1][0]) // 2
        return None

    for x in range(x_from, x_to):
        ym = mid_line.y_at(x)
        ya, yb = int(max(0, ym - 1.0 * d)), int(min(H, ym + 1.0 * d))
        hit = None
        for a, b in _vertical_runs(ink[ya:yb, x]):
            if 0.35 * d <= b - a <= 1.0 * d and abs(ya + (a + b) / 2 - ym) <= 0.25 * d:
                hit = (x, ya + a, ya + b)
                break
        if hit is not None and (not cols or abs((hit[1] + hit[2]) - (cols[0][1] + cols[0][2])) / 2 <= 0.15 * d):
            cols.append(hit)
            continue
        found = flush()
        if found is not None:
            return found
        cols = [hit] if hit is not None else []
    return flush()


def lone_staff_barlines(ink: np.ndarray, csum: np.ndarray, st: Staff, t: float, reach_up: float = 0.0,
                        reach_down: float = 0.0) -> tuple[list[Barline], list[tuple[int, str]]]:
    """Barlines of a notation staff WITHOUT a TAB under it (a skipped vocal-only system), where note stems
    can cross the whole staff too. Every thin column inked over the whole staff height is classified from
    what is written right beside it (staff lines removed): next to its two ends and along its middle.

    - barline: it ends at the outer lines (or runs on to another staff of the same system: reach_up /
      reach_down = the distance to it in px) and nothing solid touches it -- at most a thin tie / slur at
      an end;
    - stem: a solid blob (filled notehead, beam) right beside one of its ends and nothing beside its
      middle -- positive evidence, not just 'not a barline';
    - doubtful: anything else (a time / key signature or a note pressed against a barline, a hollow
      notehead, a dashed barline, a column that runs on past an outer line without a blob ...).

    Returns (barlines, doubtful) with doubtful = [(x, why)]; when anything is doubtful the caller must not
    decide the bar count from the image. Barline columns within 1.6 spaces are one (double / final) barline."""
    d = st.spacing
    H, W = ink.shape
    x0, x1 = int(max(0, st.x0 - d)), int(min(W, st.x1 + d))
    cov = column_coverage(ink, csum, st, x0, x1, 0.0)
    full = cov >= 0.97
    lab, _n = ndi.label(full)
    cands = []
    for r in ndi.find_objects(lab):
        a, b = r[0].start + x0, r[0].stop + x0
        if b >= st.x0 + 1.2 * d:  # not the system's opening line
            cands.append((a, b))
    doubtful: list[tuple[int, str]] = []
    for a, b in cands:
        if b - a > max(4 * t, 0.75 * d):
            doubtful.append(((a + b) // 2, f"a {b - a} px wide solid column at x={(a + b) // 2}"))
    cands = [(a, b) for a, b in cands if b - a <= max(4 * t, 0.75 * d)]
    # a dashed barline cannot be told from the notation alone (no TAB staff to confirm it): doubtful
    for x in np.flatnonzero((cov >= 0.25) & ~full) + x0:
        if x >= st.x0 + 4.0 * d and not any(a - 2 <= x <= b + 2 for a, b in cands) and dashed_column(ink, st, int(x), t):
            doubtful.append((int(x), f"a dashed column at x={int(x)} (a dashed barline?)"))
            break
    # a multi-bar rest (a thick bar on the middle line, its count printed as a number) holds several bars
    # between two barlines: the count cannot be read from the barlines
    hb = _multirest_bar(ink, st, t)
    if hb is not None:
        doubtful.append((hb, f"a thick horizontal bar on the middle line at x={hb} (a multi-bar rest? its bar count "
                             "is a printed number)"))
    side = int(math.ceil(1.3 * d))
    gap = int(max(2, 0.15 * d))
    bar_runs: list[tuple[int, int]] = []
    for a, b in cands:
        xm = (a + b) // 2
        yt, yb = st.y_top(xm), st.y_bot(xm)
        col = ink[:, a:b].any(axis=1)
        mid = int(round((yt + yb) / 2))
        top_end, bot_end = _run_through(col, mid, -1, gap), _run_through(col, mid, 1, gap)
        ext_up, ext_dn = (yt - top_end) / d, (bot_end - yb) / d

        def end_ok(ext: float, reach: float) -> bool:
            return -0.3 <= ext <= 0.6 or (reach > 0 and abs(ext - reach / d) <= 0.6)

        lines = np.zeros(H, bool)  # the staff's own lines, removed before measuring what is beside the column
        for ln in st.lines:
            yc = ln.y_at(xm)
            lines[int(max(0, math.floor(yc - 0.5 * t - 1))):int(min(H, math.ceil(yc + 0.5 * t + 1)) + 1)] = True

        def fill(r0: float, r1: float, c0: int, c1: int) -> float:
            r0i, r1i = int(max(0, r0)), int(min(H, r1))
            c0, c1 = max(x0, c0), min(x1, c1)
            if r1i <= r0i or c1 <= c0:
                return 0.0
            # other full-height columns (the second line of a double / final barline) are not 'beside' it
            box = ink[r0i:r1i, c0:c1][:, ~full[c0 - x0:c1 - x0]][~lines[r0i:r1i]]
            return float(box.mean()) if box.size else 0.0

        left, right = (a - 1 - side, a - 1), (b + 1, b + 1 + side)
        # where a barline runs on to another staff of the system, its end here is this staff's outer line
        y_top_end = yt if (reach_up > 0 and abs(ext_up - reach_up / d) <= 0.6) else top_end
        y_bot_end = yb if (reach_down > 0 and abs(ext_dn - reach_down / d) <= 0.6) else bot_end
        def solid(r0: float, r1: float, c0: int, c1: int) -> float:
            """Width (in spaces) of the columns beside an end whose ink is >= 0.45 spaces tall in one piece: a
            notehead (~0.9) or a beam (~0.5) is solid; a tie or slur touching a barline (+ a staff line) is not."""
            r0i, r1i = int(max(0, r0)), int(min(H, r1))
            c0, c1 = max(x0, c0), min(x1, c1)
            if r1i <= r0i or c1 <= c0:
                return 0.0
            box = ink[r0i:r1i, c0:c1][:, ~full[c0 - x0:c1 - x0]]
            return sum(1 for j in range(box.shape[1]) if any(b_ - a_ >= 0.45 * d for a_, b_ in
                                                            _vertical_runs(box[:, j]))) / d

        # a stem ends at about the centre of its notehead (or at its beam): something solid beside an end
        ends = [max(solid(y0_, y1_, *left), solid(y0_, y1_, *right))
                for y0_, y1_ in ((y_top_end - 0.6 * d, y_top_end + 0.8 * d), (y_bot_end - 0.8 * d, y_bot_end + 0.6 * d))]
        middle = max(fill(yt + 1.2 * d, yb - 1.2 * d, *left), fill(yt + 1.2 * d, yb - 1.2 * d, *right))
        if end_ok(ext_up, reach_up) and end_ok(ext_dn, reach_down) and max(ends) < 0.2 and middle < 0.1:
            bar_runs.append((a, b))
        elif max(ends) >= 0.5 and middle < 0.1:
            continue  # a stem: notehead / beam at an end, nothing beside its middle
        else:
            doubtful.append((xm, f"the column at x={xm} spans the staff but is neither clearly a barline nor a stem "
                                 f"(runs past the outer lines by {ext_up:.1f} / {ext_dn:.1f} spaces; solid ink beside "
                                 f"its ends over {ends[0]:.1f} / {ends[1]:.1f} spaces, ink beside its middle "
                                 f"{middle:.2f})"))
    out: list[Barline] = []
    for a, b in bar_runs:
        if out and a - out[-1].xb <= 1.6 * d:
            out[-1] = Barline(out[-1].xa, float(b), out[-1].runs + 1, True, False)
        else:
            out.append(Barline(float(a), float(b), 1, True, False))
    return out, sorted(doubtful)


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


# ------------------------------------------------------------ system grouping


def left_connector_coverage(ink: np.ndarray, upper: Staff, lower: Staff) -> float:
    """How completely ONE near-vertical stroke at the staves' left edge joins two staves: the fraction
    of the rows between them that a single connected, narrow ink component covers.

    A system's opening line (or bracket / brace) is one stroke from staff to staff; two separate
    systems have only unrelated glyphs there (a boxed rehearsal mark, a bar number, labels), which
    never form one component across the whole gap. Small breaks in a scanned line (< 0.3 spaces) are
    bridged. Returns 0 when the staves' left ends are far apart (different indentation)."""
    d = max(upper.spacing, lower.spacing)
    if abs(upper.x0 - lower.x0) > 4.0 * d:
        return 0.0
    # the line sits at the staves' left end, but noisy scans let the detected ends drift a space or two
    xl = int(max(0, min(upper.x0, lower.x0) - 2.5 * d))
    xr = int(min(ink.shape[1], max(upper.x0, lower.x0) + 2.0 * d))
    ya = int(math.ceil(max(upper.lines[-1].y_at(upper.x0), upper.lines[-1].y_at(upper.x0 + d)) + 0.5 * upper.spacing))
    yb = int(math.floor(min(lower.lines[0].y_at(lower.x0), lower.lines[0].y_at(lower.x0 + d)) - 0.5 * lower.spacing))
    if yb - ya < 2:
        return 1.0
    region = ink[ya:yb, xl:xr]
    bridge = max(1, int(0.3 * d))
    region = ndi.binary_closing(region, structure=np.ones((2 * bridge + 1, 1), bool)) | region
    lab, n = ndi.label(region)
    best = 0.0
    for k, sl in enumerate(ndi.find_objects(lab), start=1):
        if sl is None or sl[1].stop - sl[1].start > 1.5 * d:
            continue
        rows = (lab[sl] == k).any(axis=1).sum()
        best = max(best, rows / (yb - ya))
    return float(best)


def line_continuity(ink: np.ndarray, st: Staff, t: float) -> float:
    """Median over the staff's lines of the fraction of [x0, x1] where the line has ink nearby.

    Real staff / tab lines are continuous (digits sitting on a tab line are ink too); a row of
    chord-diagram grids linked into "lines" is mostly white between the diagrams."""
    h, w = ink.shape
    xs = np.arange(int(max(0, st.x0)), int(min(w, st.x1)))
    if len(xs) < 2:
        return 0.0
    band = max(1, int(math.ceil(t)) + 1)
    fr = []
    for ln in st.lines:
        if ln.interpolated:
            continue
        ys = np.clip(np.round(ln.a + ln.b * xs).astype(int), 0, h - 1)
        hit = np.zeros(len(xs), bool)
        for dy in range(-band, band + 1):
            hit |= ink[np.clip(ys + dy, 0, h - 1), xs]
        fr.append(hit.mean())
    return float(np.median(fr)) if fr else 0.0


def vertical_line_density(ink: np.ndarray, csum: np.ndarray, st: Staff) -> float:
    """Thin columns fully inked across the staff, per 10 line spaces of staff length.

    A real staff has a barline every ~20 spaces; a chord-diagram grid has ~6 string lines
    every ~5 spaces."""
    d = st.spacing
    x0, x1 = int(max(0, st.x0)), int(min(ink.shape[1], st.x1))
    if x1 - x0 < 2:
        return 0.0
    cov = column_coverage(ink, csum, st, x0, x1, 0.1 * d)
    lab, n = ndi.label(cov >= 0.9)
    thin = sum(1 for r in ndi.find_objects(lab) if r[0].stop - r[0].start <= 0.5 * d)
    return 10.0 * d * thin / (x1 - x0)


def is_grid(ink: np.ndarray, csum: np.ndarray, st: Staff, t: float) -> str | None:
    """Reason string if this 'staff' is really a row of chord diagrams (or another ruled grid)."""
    cont = line_continuity(ink, st, t)
    dens = vertical_line_density(ink, csum, st)
    if cont < 0.75:
        return f"its lines are broken ({cont:.0%} continuous)"
    if dens > 4.0:
        return f"{dens:.1f} full-height vertical lines per 10 line spaces"
    return None


def drop_specks(ink: np.ndarray, min_px: int) -> np.ndarray:
    """Remove connected ink components smaller than min_px pixels (scanner dust)."""
    lab, n = ndi.label(ink)
    if n == 0:
        return ink
    sizes = np.bincount(lab.ravel())
    keep = sizes >= min_px
    keep[0] = False
    return keep[lab]


def glyph_extent(ink: np.ndarray, y_from: float, y_limit: float, x0: int, x1: int, gap_min: float,
                 min_px: int = 4) -> tuple[int, int] | None:
    """Walk from y_from towards y_limit (either direction) through rows that have ink, until the
    first run of >= gap_min empty rows. Returns (start, end) of that empty run as row indices in
    walking order (start = first empty row reached), or None if no such gap exists before y_limit.

    "Empty" ignores dust (components < min_px). Contiguous ink from a staff -- noteheads on ledger
    lines, stems, beams -- never contains such a gap, so the first gap marks the end of the staff's
    own glyphs; free-floating text (headings, bend amounts) lies beyond it."""
    H, W = ink.shape
    step = 1 if y_limit >= y_from else -1
    a, b = sorted((int(round(y_from)), int(round(y_limit))))
    a, b = max(0, a), min(H, b + 1)
    x0, x1 = max(0, int(x0)), min(W, int(x1))
    if b - a < 2 or x1 - x0 < 2:
        return None
    region = drop_specks(ink[a:b, x0:x1], min_px)
    # vertical lines running through the whole band (a system's opening line, a bracket, barlines
    # drawn through the gap) belong to no glyph: they would make every row look non-empty
    full = region.mean(axis=0) >= 0.9
    if full.any():
        full = ndi.binary_dilation(full, iterations=1)
        region = region & ~full[None, :]
    thr = max(2.0, 0.0008 * (x1 - x0))
    empty = region.sum(axis=1) <= thr
    if step < 0:
        empty = empty[::-1]
    need = max(2, int(math.ceil(gap_min)))
    run = 0
    for i, e in enumerate(empty):
        if e:
            run += 1
            if run >= need:
                st = i - run + 1
                # extend to the end of this empty run
                j = i
                while j + 1 < len(empty) and empty[j + 1]:
                    j += 1
                if step > 0:
                    return a + st, a + j
                return b - 1 - st, b - 1 - j
        else:
            run = 0
    return None


def ink_fraction(ink: np.ndarray, st: Staff, xa: float, xb: float, t: float) -> float:
    """Fraction of pixels inside the staff band between xa and xb that are ink, the staff's own
    lines excluded -- 'is there anything written in this stretch of staff?'"""
    h, w = ink.shape
    xs = range(int(max(0, xa)), int(min(w, xb)))
    tot = hit = 0
    for x in xs:
        y0 = int(round(st.y_top(x)))
        y1 = int(round(st.y_bot(x)))
        if y0 < 0 or y1 >= h or y1 <= y0:
            continue
        col = ink[y0:y1 + 1, x].copy()
        keep = np.ones(len(col), bool)
        lr = _line_rows(st, x, t)
        keep[lr[(lr >= 0) & (lr < len(col))]] = False
        tot += int(keep.sum())
        hit += int(col[keep].sum())
    return hit / tot if tot else 0.0


def ledger_reach(ink: np.ndarray, st: Staff, below: bool, x0: int, x1: int, t: float, max_k: int = 7) -> float | None:
    """How far ledger-line notes reach beyond the staff: the y of the outermost ledger line plus one
    space (a note can sit in the space beyond it), or None if there are no ledger lines.

    A ledger line is a short (1.1-3.5 spaces), thin, straight horizontal stroke exactly k spaces
    outside the staff; ledger lines stack, so level k only counts if level k-1 has one of the same
    width at the same x."""
    sp = st.spacing
    H, W = ink.shape
    x0, x1 = max(0, int(x0)), min(W, int(x1))
    h = int(max(1, math.ceil(t)))
    sign = 1 if below else -1
    edge = st.bottom_max if below else st.top_min
    prev: list[tuple[int, int]] | None = None
    reach = None
    for k in range(1, max_k + 1):
        yk = int(round(edge + sign * k * sp))
        if not h <= yk < H - h:
            break
        band = ink[yk - h:yk + h + 1, x0:x1].any(axis=0)
        dif = np.diff(np.concatenate(([0], band.astype(np.int8), [0])))
        starts, ends = np.flatnonzero(dif == 1), np.flatnonzero(dif == -1)
        found = []
        rows = ink[yk - h:yk + h + 1, x0:x1]
        win = int(math.ceil(t)) + 2
        for a, b in zip(starts, ends):
            if not 1.1 * sp <= b - a <= 3.5 * sp:
                continue
            # a straight horizontal stroke: one window of ~t rows is inked along the whole run
            # (an accent wedge or a slur crosses the band diagonally)
            seg = rows[:, a:b]
            if max(seg[r:r + win].any(axis=0).mean() for r in range(0, max(1, seg.shape[0] - win + 1))) < 0.9:
                continue
            thin = True
            for xe in (a + 1, b - 2):  # the ends of a ledger line stick out past the notehead: thin there
                c = ink[max(0, yk - 3 * h):min(H, yk + 3 * h + 1), x0 + xe]
                if c.sum() > 2.5 * t + 1:
                    thin = False
            if not thin:
                continue
            # stacked ledger lines are drawn the same width at the same x (a notehead or an accent
            # under the last ledger line is not another ledger line)
            if prev is not None and not any(abs(a - pa) <= 0.4 * sp and abs(b - pb) <= 0.4 * sp for pa, pb in prev):
                continue
            found.append((int(a), int(b)))
        if not found:
            break
        prev = found
        reach = edge + sign * (k + 1) * sp
    return reach


def attached_reach(ink: np.ndarray, st: Staff, below: bool, limit: float, x0: int, x1: int,
                   body_extra: float = 0.0) -> float | None:
    """How far the glyphs physically attached to a staff reach beyond it (towards `limit`).

    Connected components touching the staff body (its lines +- body_extra) are its glyphs:
    noteheads, stems, beams and flags of notes written inside the staff, chord stacks, ties. Their
    outermost row is returned (None if nothing sticks out). Vertical lines that run through the
    whole gap (system opening line, barlines drawn through to the next staff) are cut first, so
    they do not glue the next staff on."""
    H, W = ink.shape
    x0, x1 = max(0, int(x0)), min(W, int(x1))
    if below:
        ya, yb = int(max(0, st.top_min)), int(min(H, limit))
        body_a, body_b = 0, int(st.bottom_max + body_extra) - ya
        gap_a = int(st.bottom_max + 0.6 * st.spacing) - ya
        gap_b = yb - ya
    else:
        ya, yb = int(max(0, limit)), int(min(H, st.bottom_max + 1))
        body_a, body_b = int(st.top_min - body_extra) - ya, yb - ya
        gap_a = 0
        gap_b = int(st.top_min - 0.6 * st.spacing) - ya
    if yb - ya < 3 or x1 - x0 < 3:
        return None
    region = ink[ya:yb, x0:x1].copy()
    if gap_b - gap_a >= 3:
        full = region[max(0, gap_a):gap_b].mean(axis=0) >= 0.9
        if full.any():
            region[:, ndi.binary_dilation(full, iterations=1)] = False
    lab, n = ndi.label(region)
    if n == 0:
        return None
    body = np.unique(lab[max(0, body_a):max(0, body_b)])
    body = body[body > 0]
    if len(body) == 0:
        return None
    objs = ndi.find_objects(lab)
    if below:
        far = max(objs[i - 1][0].stop for i in body) - 1 + ya
        return float(far) if far > st.bottom_max + 0.5 * st.spacing else None
    far = min(objs[i - 1][0].start for i in body) + ya
    return float(far) if far < st.top_min - 0.5 * st.spacing else None


def floating_top_across(ink: np.ndarray, st: Staff, y: int, x0: int, x1: int, body_extra: float, depth: float) -> int:
    """Topmost row of the ink components that are NOT attached to the staff (labels, bend amounts,
    'P.M.') and straddle row y; returns y if none. Lets a TAB crop start above a label that the
    staff/TAB split would otherwise cut in half."""
    H, W = ink.shape
    x0, x1 = max(0, int(x0)), min(W, int(x1))
    ya = int(max(0, st.top_min))
    yb = int(min(H, y + depth))
    if yb - ya < 3 or not ya < y < yb:
        return y
    region = ink[ya:yb, x0:x1]
    lab, n = ndi.label(region)
    if n == 0:
        return y
    body = set(np.unique(lab[: int(st.bottom_max + body_extra) - ya + 1]).tolist()) - {0}
    cross = set(np.unique(lab[y - ya]).tolist()) - {0} - body
    if not cross:
        return y
    objs = ndi.find_objects(lab)
    return int(min(objs[i - 1][0].start for i in cross) + ya)
