"""Reproducibility tests: the same dataset must always bake to the same bytes.

Regression guard for the old ``datetime.now()`` defaults in MetadataGenerator,
which stamped the metadata-generation time (down to the microsecond) into
``datePublished`` and the current year into the default citation — so baking the
same input twice produced two different files. See ``_resolve_date`` and
``_build_citation``.
"""

import json
from pathlib import Path

import pytest

from croissant_baker.metadata_generator import MetadataGenerator, serialize_datetime


@pytest.fixture
def mimic_demo_dir() -> Path:
    """Path to the bundled MIMIC-IV demo dataset."""
    path = (
        Path(__file__).parent
        / "data"
        / "input"
        / "mimiciv_demo"
        / "physionet.org"
        / "files"
        / "mimic-iv-demo"
        / "2.2"
    )
    if not path.exists():
        pytest.skip(f"MIMIC-IV demo dataset not found at {path}")
    return path


def _dump(meta: dict) -> str:
    return json.dumps(
        meta, indent=2, ensure_ascii=False, sort_keys=True, default=serialize_datetime
    )


def test_output_is_reproducible(mimic_demo_dir: Path) -> None:
    """Baking the same dataset twice yields byte-identical metadata."""
    common = dict(name="Repro DS", description="d", creators=[{"name": "A. Author"}])
    first = MetadataGenerator(str(mimic_demo_dir), **common).generate_metadata()
    second = MetadataGenerator(str(mimic_demo_dir), **common).generate_metadata()
    assert _dump(first) == _dump(second)


def test_date_published_omitted_when_not_supplied(mimic_demo_dir: Path) -> None:
    """No --date-published means no datePublished — not a wall-clock timestamp."""
    meta = MetadataGenerator(
        str(mimic_demo_dir),
        name="X",
        description="d",
        creators=[{"name": "A. Author"}],
    ).generate_metadata()
    assert "datePublished" not in meta


def test_default_citation_uses_real_creators_and_date_year(
    mimic_demo_dir: Path,
) -> None:
    """The default citation is built from supplied creators + date, deterministically."""
    meta = MetadataGenerator(
        str(mimic_demo_dir),
        name="My DS",
        description="d",
        creators=[{"name": "Ada Lovelace"}],
        date_published="2021-06-21",
    ).generate_metadata()
    assert meta["citeAs"] == "Ada Lovelace. (2021). My DS [Data set]."
    assert "Dataset Creator" not in meta["citeAs"]


def test_default_citation_creator_without_date(mimic_demo_dir: Path) -> None:
    """Common CLI path: --creator given, --date-published omitted → no year.

    --creator is required by the CLI but --date-published is optional, so the
    "creator, no date" case is the default citation most runs produce. It must
    be deterministic and carry no fabricated year.
    """
    meta = MetadataGenerator(
        str(mimic_demo_dir),
        name="My DS",
        description="d",
        creators=[{"name": "Ada Lovelace"}],
    ).generate_metadata()
    assert meta["citeAs"] == "Ada Lovelace. My DS [Data set]."


def test_default_citation_omitted_when_nothing_to_cite(mimic_demo_dir: Path) -> None:
    """With neither creators nor a date there is nothing real to cite, so omit it."""
    meta = MetadataGenerator(
        str(mimic_demo_dir), name="X", description="d"
    ).generate_metadata()
    assert "citeAs" not in meta


def test_validates_without_date_published(mimic_demo_dir: Path, tmp_path: Path) -> None:
    """Omitting datePublished still produces metadata that passes validation."""
    out = tmp_path / "croissant.jsonld"
    gen = MetadataGenerator(
        str(mimic_demo_dir),
        name="X",
        description="d",
        creators=[{"name": "A. Author"}],
    )
    gen.save_metadata(str(out), validate=True)  # must not raise
    meta = json.loads(out.read_text())
    assert "datePublished" not in meta
