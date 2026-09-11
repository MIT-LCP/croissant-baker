"""gemmi is an optional extra, so the baker has to work in an install without it.

Nothing here skips when gemmi is missing. What these tests describe is the
behaviour of an install that never had it, and that behaviour has to hold
whichever environment the suite runs in.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from croissant_baker.handlers.structural_biology import star_handler, structure_handler
from croissant_baker.sources import make_source

SRC = Path(__file__).resolve().parent.parent / "src"

#: Enough of a PDB entry to be claimed. It is never parsed here: the refusal
#: comes before any reading.
PDB = "ATOM      1  N   ALA A   1      11.104   6.134  -6.504  1.00 20.00           N\n"

#: One STAR data block, for the same reason.
STAR = "data_particles\n\nloop_\n_rlnImageName\n000001@stack.mrcs\n"

INSTALL_HINT = 'pip install "croissant-baker[structural-biology]"'


def run_without_gemmi(statements: str) -> subprocess.CompletedProcess:
    """Run ``statements`` in a fresh interpreter where ``import gemmi`` raises.

    A subprocess rather than a monkeypatch, because a module is imported once
    per process and this suite has already imported these ones. Setting the
    ``sys.modules`` entry to ``None`` is what makes the import fail, which is
    the state a user without the extra is in.
    """
    script = "import sys\nsys.modules['gemmi'] = None\n" + statements
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(SRC)},
    )


def test_the_default_registry_builds_without_gemmi() -> None:
    """Every handler is imported to fill the registry, so one unguarded import
    of gemmi would take the whole CLI down on a plain install."""
    result = run_without_gemmi(
        "from croissant_baker.handlers.registry import default_registry\n"
        "default_registry()\n"
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "name, text, expected",
    [
        ("1abc.pdb", PDB, "StructureHandler"),
        ("1abc.cif", PDB, "StructureHandler"),
        ("run_data.star", STAR, "STARHandler"),
    ],
)
def test_a_structural_file_is_still_claimed_without_gemmi(
    tmp_path: Path, name: str, text: str, expected: str
) -> None:
    """Claiming costs nothing but the suffix, so the handler keeps its files
    and reports why it cannot read them. Dropping the claim would leave a
    ``.pdb`` looking like a format the baker has never heard of."""
    path = tmp_path / name
    path.write_text(text, encoding="ascii")
    result = run_without_gemmi(
        "from pathlib import Path\n"
        "from croissant_baker.handlers.registry import select_handler\n"
        f"selection = select_handler(Path({str(path)!r}))\n"
        "print(type(selection.handler).__name__)\n"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


def test_a_structure_file_without_gemmi_says_how_to_install_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "1abc.pdb"
    path.write_text(PDB, encoding="ascii")
    monkeypatch.setattr(structure_handler, "gemmi", None)

    with pytest.raises(ValueError) as excinfo:
        structure_handler.StructureHandler().extract(
            make_source(path, Path("1abc.pdb"))
        )

    assert str(excinfo.value) == (
        "Reading PDB, mmCIF and CIF files needs gemmi, which is not "
        f"installed. Install it with: {INSTALL_HINT}"
    )


def test_a_star_file_without_gemmi_says_how_to_install_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "run_data.star"
    path.write_text(STAR, encoding="ascii")
    monkeypatch.setattr(star_handler, "gemmi", None)

    with pytest.raises(ValueError) as excinfo:
        star_handler.STARHandler().extract(make_source(path, Path("run_data.star")))

    assert str(excinfo.value) == (
        "Reading STAR files needs gemmi, which is not installed. "
        f"Install it with: {INSTALL_HINT}"
    )
