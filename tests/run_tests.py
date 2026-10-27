#!/usr/bin/env -S uv run --locked --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "numpy==2.5.4",
#     "scipy==1.18.1",
#     "pillow==13.0.0",
#     "pymupdf==1.28.2",
#     "pyguitarpro==0.11",
#     "pytest==9.1.1",
#     "mido==1.3.3",
# ]
# ///
"""Run the test suite with the same pinned versions the skill's scripts are locked to.

usage: uv run --locked --script tests/run_tests.py [pytest args, e.g. -q -k reconcile]

Runs from the repository root (relative test paths are taken from there). The pins must match the
scripts' inline metadata; tests/test_pins.py checks that.
"""

from __future__ import annotations

import os
import sys

if __name__ == "__main__" and not sys.flags.isolated:  # re-exec isolated (-I): no PYTHONPATH / user site / cwd
    os.execv(sys.executable, [sys.executable, "-I", *sys.argv])

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

if __name__ == "__main__":
    os.chdir(Path(__file__).resolve().parent.parent)
    sys.exit(pytest.main(sys.argv[1:]))
