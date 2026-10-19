import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "scan-to-gp"
SCRIPTS = SKILL / "scripts"
FIXTURES = REPO / "tests" / "fixtures"
sys.path.insert(0, str(SCRIPTS))


def alphatab_available() -> bool:
    return shutil.which("node") is not None and (SKILL / "node_modules" / "@coderline" / "alphatab").exists()


def run_alphatab(gp5: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(["node", str(SCRIPTS / "verify_alphatab.mjs"), str(gp5), *extra],
                          capture_output=True, text=True)


needs_alphatab = pytest.mark.skipif(not alphatab_available(),
                                    reason="node + alphaTab not installed (npm ci --prefix skills/scan-to-gp)")
