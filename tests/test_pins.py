"""Dependency pins: every PEP 723 script pins exact versions, has an up-to-date lockfile, and agrees
with the other scripts and requirements*.txt; every documented `uv run` uses `--locked`.

The skill runs its scripts with `uv run --locked --script`, so a pin changed without re-locking
(`uv lock --script <file>`) would make every run fail. Checked statically (no network).
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
from conftest import REPO

# PEP 723 reference regex
BLOCK = re.compile(r"(?m)^# /// (?P<type>[a-zA-Z0-9-]+)$\s(?P<content>(^#(| .*)$\s)+)^# ///$")
PIN = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[A-Za-z0-9.+!-]+)$")
SKIP_DIRS = {"node_modules", "__pycache__", ".venv", ".pytest_cache", ".git"}


def _files(*patterns: str) -> list[Path]:
    out = set()
    for pat in patterns:
        out.update(p for p in REPO.glob(pat) if p.is_file() and not SKIP_DIRS & set(p.relative_to(REPO).parts))
    return sorted(out)


def _metadata(path: Path) -> dict | None:
    for m in BLOCK.finditer(path.read_text()):
        if m.group("type") == "script":
            body = "".join(line[2:] if line.startswith("# ") else line[1:]
                           for line in m.group("content").splitlines(keepends=True))
            return tomllib.loads(body)
    return None


SCRIPTS = [p for p in _files("skills/**/*.py", "tests/**/*.py") if _metadata(p) is not None]


def _name(p: Path) -> str:
    return str(p.relative_to(REPO))


def test_the_skill_scripts_are_covered():
    names = {_name(p) for p in SCRIPTS}
    for s in ("prepare", "make_prompts", "reconcile_build", "inspect_crop"):
        assert f"skills/scan-to-gp/scripts/{s}.py" in names


@pytest.mark.parametrize("script", SCRIPTS, ids=_name)
def test_script_pins_exact_versions_and_is_locked(script: Path):
    meta = _metadata(script)
    deps = meta.get("dependencies", [])
    for dep in deps:
        assert PIN.match(dep), f"{_name(script)}: {dep!r} is not an exact '==' pin"
    lock_path = script.with_name(script.name + ".lock")
    assert lock_path.is_file(), f"missing {_name(lock_path)}: run `uv lock --script {_name(script)}`"
    lock = tomllib.loads(lock_path.read_text())
    stale = f"{_name(lock_path)} is out of date: run `uv lock --script {_name(script)}`"
    assert lock.get("requires-python") == meta.get("requires-python"), stale
    locked = {(r["name"], r.get("specifier", "")) for r in lock.get("manifest", {}).get("requirements", [])}
    declared = {(m["name"].lower(), "==" + m["version"]) for m in map(PIN.match, deps)}
    assert locked == declared, stale
    resolved = {p["name"]: p["version"] for p in lock.get("package", [])}
    for name, spec in declared:
        assert resolved.get(name) == spec[2:], stale


def test_pins_agree_across_scripts_and_requirements():
    seen: dict[str, dict[str, str]] = {}
    sources = [(_name(s), _metadata(s).get("dependencies", [])) for s in SCRIPTS]
    for req in ("requirements.txt", "requirements-dev.txt"):
        lines = [ln.strip() for ln in (REPO / req).read_text().splitlines()]
        sources.append((req, [ln for ln in lines if ln and not ln.startswith(("#", "-r"))]))
    for where, deps in sources:
        for dep in deps:
            m = PIN.match(dep)
            assert m, f"{where}: {dep!r} is not an exact '==' pin"
            seen.setdefault(m["name"].lower(), {})[where] = m["version"]
    for name, versions in seen.items():
        assert len(set(versions.values())) == 1, f"{name} pinned differently: {versions}"


def test_every_documented_uv_run_is_locked():
    shell = re.compile(r"\buv run\b")
    argv = re.compile(r'"uv",\s*"run"')
    bad = []
    for path in _files("*.md", "*.txt", "skills/**/*.md", "skills/**/*.py", "tests/**/*.py"):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if (shell.search(line) or argv.search(line)) and "--locked" not in line:
                bad.append(f"{_name(path)}:{n}: {line.strip()}")
    assert not bad, "uv run without --locked:\n" + "\n".join(bad)
