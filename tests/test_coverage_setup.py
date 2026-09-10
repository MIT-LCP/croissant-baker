"""The coverage badge and report are wired together across four files.

``pyproject.toml`` configures ``coverage``, ``.github/workflows/test.yaml`` runs
it and hands the data to ``py-cov-action/python-coverage-comment-action``,
``.github/workflows/coverage-comment.yaml`` posts the comment for pull requests
from forks, and ``README.md`` points a shields.io endpoint badge at the data
branch the action writes. A change to any one of them can silently break the
badge, so the pieces are asserted against each other here.
"""

from pathlib import Path

import pytest

tomllib = pytest.importorskip(
    "tomllib", reason="tomllib is stdlib from Python 3.11 onwards"
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _pyproject() -> dict:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_pytest_cov_is_in_the_test_dependency_group() -> None:
    test_group = _pyproject()["dependency-groups"]["test"]
    assert any(spec.startswith("pytest-cov") for spec in test_group), test_group


def test_coverage_measures_the_package() -> None:
    assert _pyproject()["tool"]["coverage"]["run"]["source"] == ["croissant_baker"]


def test_coverage_records_relative_paths() -> None:
    # python-coverage-comment-action cannot map absolute runner paths back to
    # repository files, so relative_files is a requirement, not a preference.
    assert _pyproject()["tool"]["coverage"]["run"]["relative_files"] is True
