"""Tests for the bundled Agent Skill.

The skill is prose, so these tests guard the parts a reader cannot check by
eye: that the frontmatter satisfies the Agent Skills specification
(https://agentskills.io/specification), that every supporting file the body
points at is really there, that the discovery symlinks still resolve, and that
the copy installed with the wheel is the copy in the repository.
"""

import importlib.resources
import re
from pathlib import Path
from typing import Tuple

import pytest
import yaml

ROOT = Path(__file__).parent.parent
SKILL_DIR = ROOT / "src" / "croissant_baker" / "skills" / "croissant-baker"
SKILL_FILE = SKILL_DIR / "SKILL.md"

#: The specification's rule for the ``name`` field: 1-64 lowercase alphanumeric
#: characters and hyphens, no leading, trailing or consecutive hyphen.
NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

#: Paths the body may point at, resolved relative to the skill root.
REFERENCE_PATTERN = re.compile(r"(?:assets|scripts|references)/[A-Za-z0-9._/-]+")


def read_skill() -> Tuple[dict, str]:
    """Split ``SKILL.md`` into its parsed frontmatter and its Markdown body."""
    text = SKILL_FILE.read_text(encoding="utf-8")
    opening, _, rest = text.partition("\n")
    assert opening == "---", "SKILL.md must open with the frontmatter delimiter"
    frontmatter, delimiter, body = rest.partition("\n---\n")
    assert delimiter, "SKILL.md must close its frontmatter"
    return yaml.safe_load(frontmatter), body


@pytest.fixture(scope="module")
def frontmatter() -> dict:
    return read_skill()[0]


@pytest.fixture(scope="module")
def body() -> str:
    return read_skill()[1]


def test_frontmatter_is_yaml_with_the_required_fields(frontmatter: dict) -> None:
    """The two required fields are present and are strings."""
    assert isinstance(frontmatter["name"], str)
    assert isinstance(frontmatter["description"], str)


def test_name_is_well_formed_and_matches_the_directory(frontmatter: dict) -> None:
    """The spec ties the ``name`` field to the directory that holds the skill."""
    name = frontmatter["name"]

    assert NAME_PATTERN.match(name), f"{name!r} is not a valid skill name"
    assert 1 <= len(name) <= 64
    assert name == SKILL_DIR.name


def test_description_fits_the_specified_length(frontmatter: dict) -> None:
    """An empty description never triggers; an over-long one is rejected."""
    assert 1 <= len(frontmatter["description"]) <= 1024


def test_optional_fields_stay_within_their_limits(frontmatter: dict) -> None:
    """``compatibility`` is capped, and ``metadata`` maps strings to strings."""
    assert len(frontmatter["compatibility"]) <= 500
    assert all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in frontmatter["metadata"].items()
    )


def test_body_stays_within_the_progressive_disclosure_budget(body: str) -> None:
    """The whole body loads at once, so the spec caps it at 500 lines."""
    assert len(body.splitlines()) < 500


def test_every_referenced_supporting_file_exists(body: str) -> None:
    """A skill that points at a file it does not ship sends the agent nowhere."""
    referenced = set(REFERENCE_PATTERN.findall(body))

    assert referenced, "expected the body to point at its bundled template"
    for relative in sorted(referenced):
        assert (SKILL_DIR / relative).is_file(), f"{relative} is missing"


def test_the_discovery_symlink_resolves_to_the_packaged_skill() -> None:
    """The cross-client discovery path is a link, never a second copy."""
    link = ROOT / ".agents" / "skills" / "croissant-baker"

    assert link.is_symlink(), ".agents/skills/croissant-baker should be a symlink"
    assert not Path(link.readlink()).is_absolute(), "the link must be relative"
    assert link.resolve() == SKILL_DIR.resolve()


def test_the_installed_package_carries_the_skill() -> None:
    """A pip user gets the skill, not just a repository checkout."""
    packaged = (
        importlib.resources.files("croissant_baker")
        .joinpath("skills", "croissant-baker", "SKILL.md")
        .read_text(encoding="utf-8")
    )

    assert packaged == SKILL_FILE.read_text(encoding="utf-8")
