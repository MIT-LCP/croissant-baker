"""Check that uv.lock records the same croissant-baker version as pyproject.toml.

release-please bumps pyproject.toml on each release and, through the
``extra-files`` entry in release-please-config.json, the croissant-baker
entry in uv.lock. This test catches the two drifting apart, which makes
``uv lock --check`` fail.

``[tool.uv] required-version`` makes a uv older than 0.8 refuse to run,
since uv 0.7 rewrites the lock to an older revision. Every ``uv sync`` in
the CI workflows passes ``--locked`` so a stale lock fails the job.
"""

import re
import sys
from pathlib import Path

import pytest
import yaml

# pytest depends on packaging.
from packaging.specifiers import SpecifierSet
from packaging.version import Version

if sys.version_info >= (3, 11):
    import tomllib
else:
    # pytest pulls in tomli on Python older than 3.11.
    import tomli as tomllib

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

# ``uv``, then global options (a few take a value), then ``sync``.
_UV_SYNC = re.compile(
    r"(?:^|(?<=[\s(`$]))uv"
    r"(?:\s+(?:--(?:directory|project|config-file|cache-dir|color)(?:=|\s+)\S+|-\S+))*"
    r"\s+sync\b"
)
_COMMAND_END = re.compile(r";|&&|\|\||\||\)")


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


def _shell_lines(script: str) -> list[str]:
    """Join backslash continuations and drop whole line ``#`` comments."""
    joined = script.replace("\\\n", " ")
    return [line for line in joined.splitlines() if not line.lstrip().startswith("#")]


def _uv_sync_commands(workflow_text: str) -> list[str]:
    """Return each ``uv sync`` command, cut at the next separator or paren."""
    workflow = yaml.safe_load(workflow_text) or {}
    commands = []
    for job in (workflow.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            for line in _shell_lines(step.get("run") or ""):
                for match in _UV_SYNC.finditer(line):
                    end = _COMMAND_END.search(line, match.end())
                    command = line[match.start() : end.start() if end else len(line)]
                    commands.append(" ".join(command.split()))
    return commands


def _unlocked(commands: list[str]) -> list[str]:
    return [command for command in commands if "--locked" not in command.split()]


def _repo_uv_sync_commands() -> list[str]:
    return [
        f"{path.name}: {command}"
        for path in sorted(WORKFLOWS.glob("*.y*ml"))
        for command in _uv_sync_commands(path.read_text())
    ]


def test_workflows_run_uv_sync() -> None:
    assert _repo_uv_sync_commands()


def test_every_workflow_uv_sync_is_locked() -> None:
    assert _unlocked(_repo_uv_sync_commands()) == []


def _workflow(run_block: str) -> str:
    """Wrap an indented ``run:`` block in a one step workflow."""
    return (
        "jobs:\n"
        "  test:\n"
        "    steps:\n"
        "      - name: Install dependencies\n"
        f"        run: {run_block}\n"
    )


@pytest.mark.parametrize(
    ("run_block", "commands"),
    [
        ("uv sync --locked --group dev", ["uv sync --locked --group dev"]),
        (
            ">-\n          uv sync\n          --locked --group test",
            ["uv sync --locked --group test"],
        ),
        (
            "|\n          uv sync \\\n            --locked --group test",
            ["uv sync --locked --group test"],
        ),
        (
            "|\n          # uv sync build hooks run here\n          uv sync --locked",
            ["uv sync --locked"],
        ),
        (
            "uv python pin 3.12 && uv sync --group docs",
            ["uv sync --group docs"],
        ),
        ("uv -q sync --group a", ["uv -q sync --group a"]),
        (
            "uv --directory sub sync --locked",
            ["uv --directory sub sync --locked"],
        ),
        ("(uv sync --group a)", ["uv sync --group a"]),
        ("echo $(uv sync)", ["uv sync"]),
        # A block scalar, since `` #`` starts a YAML comment in a plain one.
        ("|\n          echo 'a #b' && uv sync", ["uv sync"]),
        ("uvx ruff check .", []),
        ("uv run pytest -v", []),
        ("uv pip sync requirements.txt", []),
    ],
    ids=[
        "plain",
        "folded",
        "continued",
        "comment",
        "chained",
        "global-flag",
        "global-option",
        "subshell",
        "substitution",
        "hash-in-quotes",
        "uvx",
        "uv-run",
        "uv-pip-sync",
    ],
)
def test_uv_sync_commands_are_found(run_block: str, commands: list[str]) -> None:
    assert _uv_sync_commands(_workflow(run_block)) == commands


def test_uv_sync_without_locked_is_flagged() -> None:
    commands = _uv_sync_commands(_workflow("uv sync --group dev"))
    assert _unlocked(commands) == ["uv sync --group dev"]
