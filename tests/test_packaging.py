"""Check what a wheel built from this checkout really ships.

The editable install the test suite runs under resolves the package back to
``src/``, so it cannot tell whether a file or an extra reaches someone who runs
``pip install croissant-baker``. These tests build the wheel with the project's
own build backend and read it the way pip would.
"""

import zipfile
from email.parser import Parser
from pathlib import Path

import pytest
from hatchling.build import build_wheel

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / "src" / "croissant_baker" / "skills" / "croissant-baker"


@pytest.fixture(scope="module")
def wheel(tmp_path_factory: pytest.TempPathFactory) -> zipfile.ZipFile:
    """A wheel built from the working tree, opened for reading.

    Built through the PEP 517 hook pip itself calls, which reads the project
    from the working directory.
    """
    out = tmp_path_factory.mktemp("wheel")
    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(REPO_ROOT)
        name = build_wheel(str(out))
    with zipfile.ZipFile(out / name) as archive:
        yield archive


def _metadata(wheel: zipfile.ZipFile):
    (name,) = [n for n in wheel.namelist() if n.endswith(".dist-info/METADATA")]
    return Parser().parsestr(wheel.read(name).decode("utf-8"))


def test_the_wheel_offers_the_mcp_extra(wheel: zipfile.ZipFile) -> None:
    """``pip install croissant-baker[mcp]`` installs the SDK the server needs.

    A dependency group is not written into the wheel, so an extra declared only
    there installs nothing and the server still fails to start.
    """
    metadata = _metadata(wheel)

    assert "mcp" in metadata.get_all("Provides-Extra", [])
    assert any(
        req.startswith("mcp>=") and "extra == 'mcp'" in req
        for req in metadata.get_all("Requires-Dist", [])
    )


def test_the_mcp_sdk_is_not_a_default_dependency(wheel: zipfile.ZipFile) -> None:
    """A plain install stays as it was: the server is opt in."""
    metadata = _metadata(wheel)

    unconditional = [
        req for req in metadata.get_all("Requires-Dist", []) if "extra ==" not in req
    ]
    assert not any(req.startswith("mcp") for req in unconditional)


@pytest.mark.parametrize(
    "relative", ["SKILL.md", "assets/rai-template.yaml"], ids=lambda p: p
)
def test_the_wheel_carries_the_skill_files(
    wheel: zipfile.ZipFile, relative: str
) -> None:
    """A pip user gets the skill and its template, byte for byte."""
    member = f"croissant_baker/skills/croissant-baker/{relative}"

    assert member in wheel.namelist()
    assert wheel.read(member) == (SKILL_DIR / relative).read_bytes()


def test_the_wheel_does_not_ship_the_discovery_symlink(
    wheel: zipfile.ZipFile,
) -> None:
    """The ``.agents`` link is for a checkout; the wheel holds one copy only."""
    assert not [n for n in wheel.namelist() if n.startswith(".agents/")]
