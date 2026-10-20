#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy>=1.24", "pillow>=10.0", "scipy>=1.10"]
# ///
"""Regenerate a fixture: render <name>.ly with LilyPond, then degrade it to look scanned.

usage: uv run tests/fixtures/make_fixture.py tests/fixtures/exercise1/exercise1.ly [--inverted]
Writes next to the .ly: <name>_clean.png, <name>.midi, <name>_scan.jpg (deterministic, seeded),
and with --inverted also <name>_1bit_inverted.png: the scan thresholded to 1 bit and stored
white-on-black, the way the raw samples of a 1-bit /ImageMask (stencil) PDF scan come out.
"""
import os
import sys

if __name__ == "__main__" and not sys.flags.isolated:
    os.execv(sys.executable, [sys.executable, "-I", *sys.argv])

import shutil  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
from PIL import Image, ImageFilter  # noqa: E402
from scipy import ndimage as ndi  # noqa: E402


def degrade(clean: Image.Image, seed: int = 7, angle: float = 1.15) -> Image.Image:
    rng = np.random.default_rng(seed)
    g = clean.convert("L")
    g = g.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=255)
    g = g.filter(ImageFilter.GaussianBlur(1.1))
    a = np.asarray(g).astype(np.float32)
    h, w = a.shape
    # uneven lighting: a soft diagonal gradient + vignette, like a phone shot / cheap scanner
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    light = 1.0 - 0.10 * (xx / w) - 0.06 * (yy / h) - 0.05 * (((xx - w / 2) / w) ** 2 + ((yy - h / 2) / h) ** 2)
    paper = 238 * light
    a = paper * (a / 255.0) + 18 * (1 - a / 255.0)  # ink is not pure black, paper not pure white
    a += rng.normal(0, 7, a.shape)  # sensor noise
    specks = ndi.binary_dilation(rng.random(a.shape) > 0.99985, iterations=1)
    a[specks] -= 90  # dust
    a = np.clip(a, 0, 255).astype(np.uint8)
    return Image.fromarray(a)


def one_bit_inverted(scan: Image.Image) -> Image.Image:
    a = np.asarray(scan.convert("L"))
    return Image.fromarray(np.where(a < 128, 255, 0).astype(np.uint8)).convert("1")


def main() -> int:
    ly = Path(sys.argv[1]).resolve()
    inverted = "--inverted" in sys.argv[2:]
    if not shutil.which("lilypond"):
        print("lilypond not found")
        return 2
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ly.stem
        subprocess.run(["lilypond", "--png", "-dresolution=300", "-o", str(base), str(ly)], check=True,
                       capture_output=True)
        clean = Image.open(f"{base}.png")
        clean.load()
        shutil.copy(f"{base}.midi", ly.with_suffix(".midi"))
    # trim the empty lower half of the page so the fixture stays small
    arr = np.asarray(clean.convert("L"))
    rows = np.flatnonzero((arr < 128).any(axis=1))
    bottom = min(arr.shape[0], int(rows[-1]) + 150)
    clean = clean.crop((0, 0, clean.width, bottom))
    clean.save(ly.with_name(ly.stem + "_clean.png"), optimize=True)
    scan = degrade(clean)
    scan.save(ly.with_name(ly.stem + "_scan.jpg"), quality=45)
    if inverted:
        one_bit_inverted(scan).save(ly.with_name(ly.stem + "_1bit_inverted.png"), optimize=True)
    print("wrote", ly.with_name(ly.stem + "_clean.png"), ly.with_name(ly.stem + "_scan.jpg"), ly.with_suffix(".midi"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
