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
import yaml

tomllib = pytest.importorskip(
    "tomllib", reason="tomllib is stdlib from Python 3.11 onwards"
)

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
ACTION = "py-cov-action/python-coverage-comment-action"
# The action's default, and the branch the README badge URLs point at.
DEFAULT_DATA_BRANCH = "python-coverage-comment-action-data"


def _pyproject() -> dict:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _steps(workflow: dict) -> list[dict]:
    return [step for job in workflow["jobs"].values() for step in job["steps"]]


def _coverage_step(workflow: dict) -> dict:
    steps = [s for s in _steps(workflow) if s.get("uses", "").startswith(ACTION)]
    assert len(steps) == 1, steps
    return steps[0]


def _triggers(workflow: dict) -> dict:
    # YAML 1.1 reads a bare ``on`` key as the boolean True.
    return workflow.get("on", workflow.get(True))


def test_pytest_cov_is_in_the_test_dependency_group() -> None:
    test_group = _pyproject()["dependency-groups"]["test"]
    assert any(spec.startswith("pytest-cov") for spec in test_group), test_group


def test_coverage_measures_the_package() -> None:
    assert _pyproject()["tool"]["coverage"]["run"]["source"] == ["croissant_baker"]


def test_coverage_records_relative_paths() -> None:
    # python-coverage-comment-action cannot map absolute runner paths back to
    # repository files, so relative_files is a requirement, not a preference.
    assert _pyproject()["tool"]["coverage"]["run"]["relative_files"] is True


def test_the_test_workflow_collects_coverage() -> None:
    commands = [step.get("run", "") for step in _steps(_workflow("test.yaml"))]
    pytest_runs = [c for c in commands if "pytest" in c]
    assert pytest_runs, commands
    assert all("--cov" in c for c in pytest_runs), pytest_runs


def test_the_test_workflow_hands_coverage_to_the_action() -> None:
    step = _coverage_step(_workflow("test.yaml"))
    assert step["with"]["GITHUB_TOKEN"]


def test_the_comment_workflow_follows_the_test_workflow() -> None:
    # Comments on pull requests from forks are posted from this second,
    # trusted workflow, which never checks the contributor's code out.
    comment = _workflow("coverage-comment.yaml")
    workflow_run = _triggers(comment)["workflow_run"]
    assert workflow_run["workflows"] == [_workflow("test.yaml")["name"]]
    assert "checkout" not in yaml.dump(comment)


def test_the_comment_workflow_reads_the_triggering_run() -> None:
    step = _coverage_step(_workflow("coverage-comment.yaml"))
    assert "workflow_run.id" in step["with"]["GITHUB_PR_RUN_ID"]
