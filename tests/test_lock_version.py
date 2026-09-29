"""Check that uv.lock records the same croissant-baker version as pyproject.toml.

release-please bumps pyproject.toml on each release and, through the
``extra-files`` entry in release-please-config.json, the croissant-baker
entry in uv.lock. This test catches the two drifting apart, which makes
``uv lock --check`` fail.

``[tool.uv] required-version`` makes a uv older than 0.8 refuse to run,
since uv 0.7 rewrites the lock to an older revision.
"""

import sys
from pathlib import Path

# pytest depends on packaging.
from packaging.specifiers import SpecifierSet
from packaging.version import Version

if sys.version_info >= (3, 11):
    import tomllib
else:
    # pytest pulls in tomli on Python older than 3.11.
    import tomli as tomllib

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_toml(name: str) -> dict:
    with open(REPO_ROOT / name, "rb") as f:
        return tomllib.load(f)


def test_uv_lock_version_matches_pyproject() -> None:
    project = _load_toml("pyproject.toml")["project"]
    locked = [
        package["version"]
        for package in _load_toml("uv.lock")["package"]
        if package["name"] == project["name"]
    ]
    assert locked == [project["version"]]


def test_pyproject_sets_a_uv_floor() -> None:
    required = _load_toml("pyproject.toml")["tool"]["uv"]["required-version"]
    accepted = SpecifierSet(required)
    assert Version("0.7.99") not in accepted
    # A floor, a compatible range or an exact pin: some 0.8+ release fits.
    assert any(Version(v) in accepted for v in ("0.8.0", "0.12.19"))

