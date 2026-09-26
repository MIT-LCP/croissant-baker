"""The coverage badge and report are wired together across four files.

``pyproject.toml`` configures ``coverage``, ``.github/workflows/test.yaml`` runs
it and hands the data to ``py-cov-action/python-coverage-comment-action``,
``.github/workflows/coverage-comment.yaml`` posts the comment for pull requests
from forks, and ``README.md`` points a shields.io endpoint badge at the data
branch the action writes. A change to any one of them can silently break the
badge, so the pieces are asserted against each other here.
"""

from pathlib import Path

import yaml

try:  # tomllib is stdlib from Python 3.11 onwards
    import tomllib
except ImportError:  # Python 3.10, where coverage[toml] brings tomli in
    import tomli as tomllib

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


def _uploads(workflow: dict) -> list[dict]:
    return [s for s in _steps(workflow) if "upload-artifact" in s.get("uses", "")]


def _coverage_data_upload(workflow: dict) -> dict:
    uploads = [s for s in _uploads(workflow) if s["with"]["path"] == ".coverage"]
    assert len(uploads) == 1, _uploads(workflow)
    return uploads[0]


def _triggers(workflow: dict) -> dict:
    # YAML 1.1 reads a bare ``on`` key as the boolean True.
    return workflow.get("on", workflow.get(True))


def _pytest_cov_requirement() -> str:
    test_group = _pyproject()["dependency-groups"]["test"]
    specs = [spec for spec in test_group if spec.startswith("pytest-cov")]
    assert len(specs) == 1, test_group
    return specs[0]


def test_pytest_cov_is_in_the_test_dependency_group() -> None:
    assert _pytest_cov_requirement()


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


def test_the_readme_badge_reads_the_data_branch_the_action_writes() -> None:
    step = _coverage_step(_workflow("test.yaml"))
    branch = step["with"].get("COVERAGE_DATA_BRANCH", DEFAULT_DATA_BRANCH)
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    endpoint = (
        "https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/"
        f"MIT-LCP/croissant-baker/{branch}/endpoint.json"
    )
    assert endpoint in readme


def test_the_readme_badge_links_to_the_html_report() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    report = (
        "https://github.com/MIT-LCP/croissant-baker/blob/"
        f"{DEFAULT_DATA_BRANCH}/htmlcov/index.html"
    )
    assert report in readme


def test_the_artifact_carries_the_name_the_action_looks_for() -> None:
    # The posting workflow asks the action for an artifact by name, and the
    # action treats a missing one as "nothing to post" and exits 0. Renaming
    # either half here would end comments on pull requests from forks silently.
    comment_uploads = [
        s
        for s in _uploads(_workflow("test.yaml"))
        if s["with"]["path"] == "python-coverage-comment-action.txt"
    ]
    assert len(comment_uploads) == 1, comment_uploads
    assert comment_uploads[0]["with"]["name"] == "python-coverage-comment-action"


def test_neither_workflow_renames_the_artifact_the_other_reads() -> None:
    for name in ("test.yaml", "coverage-comment.yaml"):
        inputs = _coverage_step(_workflow(name))["with"]
        assert "COMMENT_ARTIFACT_NAME" not in inputs, name
        assert "COMMENT_FILENAME" not in inputs, name


def test_only_one_matrix_leg_hands_its_coverage_on() -> None:
    # Two legs would upload the same artifact name, and the second upload fails.
    upload = _coverage_data_upload(_workflow("test.yaml"))
    assert "matrix.python-version ==" in upload["if"]


def test_the_coverage_data_survives_the_trip_between_jobs() -> None:
    # upload-artifact skips dotfiles unless told otherwise, and .coverage is one,
    # so without the flag the upload is empty and the coverage job finds nothing.
    workflow = _workflow("test.yaml")
    upload = _coverage_data_upload(workflow)
    assert upload["with"].get("include-hidden-files") is True, upload
    downloads = [
        s for s in _steps(workflow) if "download-artifact" in s.get("uses", "")
    ]
    assert [d["with"]["name"] for d in downloads] == [upload["with"]["name"]]


def test_no_job_that_runs_repository_code_can_write() -> None:
    # Shell steps run the pull request's own tests and uv sync build hooks, so
    # a job with any of them keeps a read-only token.
    for name in ("test.yaml", "coverage-comment.yaml"):
        workflow = _workflow(name)
        assert workflow["permissions"] == {}, name
        for job_id, job in workflow["jobs"].items():
            if any("run" in step for step in job["steps"]):
                scopes = job.get("permissions", {})
                assert "write" not in scopes.values(), (name, job_id, scopes)


def test_the_test_workflow_queues_runs_instead_of_cancelling_them() -> None:
    # A cancelled run is a lost coverage comment, and two runs pushing the data
    # branch at once is a lost commit, so runs queue and none is cancelled.
    concurrency = _workflow("test.yaml")["concurrency"]
    assert concurrency["group"] == "${{ github.workflow }}-${{ github.ref }}"
    assert "cancel-in-progress" not in concurrency


def test_both_workflows_colour_the_badge_with_the_same_threshold() -> None:
    # The action defaults MINIMUM_GREEN to 100, which paints a healthy repo
    # orange, so the threshold is set on purpose, and set once for both runs.
    thresholds = {
        name: _coverage_step(_workflow(name))["with"].get("MINIMUM_GREEN")
        for name in ("test.yaml", "coverage-comment.yaml")
    }
    assert None not in thresholds.values(), thresholds
    assert len(set(thresholds.values())) == 1, thresholds


def test_comment_runs_from_different_forks_never_share_a_concurrency_group() -> None:
    # On workflow_run, head_branch is the branch name inside the contributor's
    # fork, and names like patch-1 repeat across forks. With cancel-in-progress
    # a shared group lets one pull request cancel another's comment.
    group = _workflow("coverage-comment.yaml")["concurrency"]["group"]
    if "workflow_run.head_branch" in group:
        assert "workflow_run.head_repository.full_name" in group, group
