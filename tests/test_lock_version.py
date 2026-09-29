"""Check that uv.lock records the same croissant-baker version as pyproject.toml.

release-please bumps pyproject.toml on each release and, through the
``extra-files`` entry in release-please-config.json, the croissant-baker
entry in uv.lock. This test catches the two drifting apart, which makes
``uv lock --check`` fail.
"""

import sys
from pathlib import Path

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
