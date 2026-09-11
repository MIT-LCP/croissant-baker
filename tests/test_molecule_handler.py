"""Small molecules: what an SDF, a MOL or a MOL2 declares about its records.

Unit level throughout, ``extract`` and ``build_croissant``, never a bake. The
checks the registry-wide sweep makes are replicated here, so a failure names
this handler rather than one parametrised case of a sweep over all of them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from croissant_baker.handlers.base_handler import BuildResult
from croissant_baker.handlers.registry import select_handler
from croissant_baker.handlers.structural_biology.molecule_handler import (
    SmallMoleculeHandler,
)
from croissant_baker.sources import make_source

HANDLER = SmallMoleculeHandler()


def _v2000(name: str, atoms: int, bonds: int) -> str:
    """One V2000 record header and a connection table of the declared size.

    The atom and bond blocks are what a writer emits, trimmed of nothing: the
    counts line is fixed width, and reading it is the point.
    """
    atom_line = "    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0"
    bond_line = "  1  2  1  0  0  0  0"
    return (
        f"{name}\n"
        "  Baker  01012400002D\n"
        "\n"
        f"{atoms:3d}{bonds:3d}  0  0  0  0            999 V2000\n"
        + "".join(f"{atom_line}\n" for _ in range(atoms))
        + "".join(f"{bond_line}\n" for _ in range(bonds))
        + "M  END\n"
    )


#: Two molecules, five tags between them: an integer, a decimal, free text, a
#: tag only the first molecule carries, and one whose value runs over two lines.
SDF = (
    _v2000("benzene", 6, 6)
    + "> <MW>\n78.11\n\n"
    + "> <RING_COUNT>\n1\n\n"
    + "> <SOURCE>\nin-house synthesis\n\n"
    + "> <NOTES>\nrecrystallised twice\nstored at -20C\n\n"
    + "> <ASSAY_ID>\n4471\n\n"
    + "$$$$\n"
    + _v2000("ethanol", 9, 8)
    + "> <MW>\n46.07\n\n"
    + "> <RING_COUNT>\n0\n\n"
    + "> <SOURCE>\ncommercial\n\n"
    + "> <NOTES>\nreference standard\n\n"
    + "$$$$\n"
)

#: A single molfile: one record, and no ``$$$$`` to close it.
MOL = _v2000("benzene", 6, 6)

#: V3000 puts zeroes on the counts line and the real counts in the CTAB.
MOL_V3000 = """large
  Baker  01012400003D

  0  0  0     0  0            999 V3000
M  V30 BEGIN CTAB
M  V30 COUNTS 42 44 0 0 0
M  V30 BEGIN ATOM
M  V30 1 C 0.0 0.0 0.0 0
M  V30 END ATOM
M  V30 END CTAB
M  END
$$$$
"""

MOL2 = """@<TRIPOS>MOLECULE
benzene
 6 6 1 0 0
SMALL
GASTEIGER

@<TRIPOS>ATOM
      1 C1     0.0000   0.0000   0.0000 C.ar    1  BENZENE  -0.0620

@<TRIPOS>BOND
     1    1    2 ar

@<TRIPOS>MOLECULE
ethanol
 9 8 1 0 0
SMALL
USER_CHARGES

@<TRIPOS>ATOM
      1 C1     0.0000   0.0000   0.0000 C.3     1  ETHANOL  -0.0600
"""


def write(directory: Path, name: str, text: str) -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def extract(path: Path, relative: str | None = None) -> dict:
    return HANDLER.extract(make_source(path, Path(relative or path.name)))


def build(*paths: Path, root: Path | None = None) -> list:
    """Every record set the handler builds for ``paths``, as one batch."""
    metas = []
    for path in paths:
        relative = str(path.relative_to(root)) if root else path.name
        meta = extract(path, relative)
        meta["relative_path"] = relative
        meta["stored_name"] = path.name
        metas.append(meta)
    ids = [f"file_{i}" for i in range(len(metas))]
    return HANDLER.build_croissant(metas, ids).record_sets


@pytest.fixture
def library(dataset: Path) -> Path:
    return write(dataset, "library.sdf", SDF)


# Claiming


@pytest.mark.parametrize("ext", [".sdf", ".mol", ".mol2"], ids=lambda e: e[1:])
def test_claims_every_declared_extension(dataset: Path, ext: str) -> None:
    path = write(dataset, f"probe{ext}", MOL)

    assert HANDLER.claims(make_source(path))


@pytest.mark.parametrize("ext", [".sdf", ".mol", ".mol2"], ids=lambda e: e[1:])
def test_case_never_changes_a_claim(dataset: Path, ext: str) -> None:
    """``LIB.SDF`` is the same file as ``lib.sdf``: the suffix lowercases."""
    quiet = write(dataset, f"probe{ext}", MOL)
    shouted = write(dataset, f"PROBE{ext.upper()}", MOL)

    assert (
        HANDLER.claims(make_source(shouted))
        is HANDLER.claims(make_source(quiet))
        is True
    )


def test_declines_another_formats_extension(dataset: Path) -> None:
    path = write(dataset, "model.pdb", "not a molfile")

    assert not HANDLER.claims(make_source(path))


def test_a_library_is_routed_to_this_handler(library: Path) -> None:
    """Registered in ``builtin_handlers``, so a bake reaches this handler at all."""
    assert isinstance(select_handler(library).handler, SmallMoleculeHandler)


def test_the_format_is_declared() -> None:
    """What the generated documentation table and the contract sweep read."""
    assert HANDLER.EXTENSIONS
    assert all(ext.startswith(".") for ext in HANDLER.EXTENSIONS)
    assert HANDLER.FORMAT_NAME
    assert HANDLER.FORMAT_DESCRIPTION


# Reading


def test_the_file_is_identified_by_name_size_and_digest(library: Path) -> None:
    meta = extract(library)

    assert meta["file_name"] == "library.sdf"
    assert meta["file_size"] == library.stat().st_size
    assert len(meta["sha256"]) == 64


@pytest.mark.parametrize(
    ("name", "text", "expected"),
    [
        ("library.sdf", SDF, "chemical/x-mdl-sdfile"),
        ("one.mol", MOL, "chemical/x-mdl-molfile"),
        ("pair.mol2", MOL2, "chemical/x-mol2"),
    ],
    ids=["sdf", "mol", "mol2"],
)
def test_each_extension_reports_its_own_media_type(
    dataset: Path, name: str, text: str, expected: str
) -> None:
    assert extract(write(dataset, name, text))["encoding_format"] == expected


def test_every_record_is_counted_and_its_atoms_and_bonds_ranged(
    library: Path,
) -> None:
    parsed = extract(library)["molecules"]

    assert parsed.n_molecules == 2
    assert parsed.atom_range == (6, 9)
    assert parsed.bond_range == (6, 8)
    assert parsed.kinds == ("V2000",)
    assert parsed.named is True


def test_property_tags_are_read_in_first_seen_order(library: Path) -> None:
    """The order the deposit wrote them in, so its own shape stays visible."""
    assert [name for name, _ in extract(library)["molecules"].tags] == [
        "MW",
        "RING_COUNT",
        "SOURCE",
        "NOTES",
        "ASSAY_ID",
    ]


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("MW", "sc:Float"),
        ("RING_COUNT", "sc:Integer"),
        ("SOURCE", "sc:Text"),
        ("NOTES", "sc:Text"),
        ("ASSAY_ID", "sc:Integer"),
    ],
)
def test_a_tag_is_typed_from_its_values(library: Path, tag: str, expected: str) -> None:
    """``NOTES`` runs over two lines in one molecule, which is text whatever
    the lines hold."""
    assert dict(extract(library)["molecules"].tags)[tag] == expected


def test_a_tag_only_one_molecule_carries_is_still_described(library: Path) -> None:
    """A sparse tag is a column of the library, not a defect."""
    assert "ASSAY_ID" in dict(extract(library)["molecules"].tags)


def test_a_lone_molfile_needs_no_terminator(dataset: Path) -> None:
    parsed = extract(write(dataset, "benzene.mol", MOL))["molecules"]

    assert parsed.n_molecules == 1
    assert parsed.atom_range == (6, 6)
    assert parsed.tags == ()


def test_a_v3000_record_takes_its_counts_from_the_ctab(dataset: Path) -> None:
    """The V3000 counts line is all zeroes; the real counts are a property."""
    parsed = extract(write(dataset, "big.sdf", MOL_V3000))["molecules"]

    assert parsed.n_molecules == 1
    assert parsed.atom_range == (42, 42)
    assert parsed.bond_range == (44, 44)
    assert parsed.kinds == ("V3000",)


def test_a_nameless_record_is_reported_as_nameless(dataset: Path) -> None:
    parsed = extract(write(dataset, "anonymous.mol", _v2000("", 6, 6)))["molecules"]

    assert parsed.named is False


def test_a_mol2_declares_its_molecules_counts_and_types(dataset: Path) -> None:
    parsed = extract(write(dataset, "pair.mol2", MOL2))["molecules"]

    assert parsed.n_molecules == 2
    assert parsed.atom_range == (6, 9)
    assert parsed.bond_range == (6, 8)
    assert parsed.kinds == ("SMALL",)
    assert parsed.tags == ()


# Refusals


def test_a_missing_file_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        extract(tmp_path / "gone" / "absent.sdf")


@pytest.mark.parametrize("name", ["empty.sdf", "empty.mol", "empty.mol2"])
def test_an_empty_file_is_refused_by_name(dataset: Path, name: str) -> None:
    path = write(dataset, name, "")

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert name in str(caught.value)


@pytest.mark.parametrize("name", ["garbage.sdf", "garbage.mol2"])
def test_garbage_bytes_are_refused_by_name(dataset: Path, name: str) -> None:
    """The message becomes the reason detail a user reads in ``--report``."""
    path = dataset / name
    path.write_bytes(b"\x00\xff not a real file \xfe\x00")

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert name in str(caught.value)


def test_text_that_declares_no_molecule_is_refused_by_name(dataset: Path) -> None:
    path = write(dataset, "prose.sdf", "a note someone left in the wrong directory\n")

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "prose.sdf" in str(caught.value)


# Building


def test_an_empty_batch_describes_nothing() -> None:
    result = HANDLER.build_croissant([], [])

    assert isinstance(result, BuildResult)
    assert result.file_sets == []
    assert result.record_sets == []


def test_one_record_set_per_file_with_a_field_per_tag(library: Path) -> None:
    (record_set,) = build(library)

    assert record_set.id == "library"
    assert [f.name for f in record_set.fields] == [
        "name",
        "n_atoms",
        "n_bonds",
        "MW",
        "RING_COUNT",
        "SOURCE",
        "NOTES",
        "ASSAY_ID",
    ]
    assert [str(f.data_types[0]) for f in record_set.fields] == [
        "sc:Text",
        "sc:Integer",
        "sc:Integer",
        "sc:Float",
        "sc:Integer",
        "sc:Text",
        "sc:Text",
        "sc:Integer",
    ]


def test_a_mol2_is_described_by_the_three_fields_it_declares(dataset: Path) -> None:
    (record_set,) = build(write(dataset, "pair.mol2", MOL2))

    assert [f.name for f in record_set.fields] == ["name", "n_atoms", "n_bonds"]


def test_every_field_names_the_file_object_and_carries_no_extract(
    library: Path,
) -> None:
    """An ``extract`` here would be a promise nobody can keep: mlcroissant
    cannot read this format."""
    sources = [f.source for rs in build(library) for f in rs.fields]

    assert sources
    assert all(s.file_object == "file_0" for s in sources)
    assert all(s.extract == type(s.extract)() for s in sources)


def test_a_field_description_names_the_tag_and_the_file(library: Path) -> None:
    described = {f.name: f.description for f in build(library)[0].fields}

    assert described["MW"] == "Property tag MW in library.sdf"


def test_the_description_names_the_file_the_format_and_the_counts(
    library: Path,
) -> None:
    (record_set,) = build(library)

    assert "library.sdf" in record_set.description
    assert "MDL SDF" in record_set.description
    assert "2 molecules" in record_set.description
    assert "6 to 9 atoms" in record_set.description
    assert "5 property tags" in record_set.description


def test_a_count_of_one_is_read_in_the_singular(dataset: Path) -> None:
    """A record of two atoms holds one bond, and ``1 bonds`` is what a count
    with no plural rule reads like."""
    (record_set,) = build(write(dataset, "pair.mol", _v2000("pair", 2, 1)))

    assert "2 atoms, 1 bond)" in record_set.description


def test_the_description_says_when_no_molecule_is_named(dataset: Path) -> None:
    (record_set,) = build(write(dataset, "anonymous.mol", _v2000("", 6, 6)))

    assert "no molecule declares a name" in record_set.description.lower()


def test_the_description_names_the_file_as_stored(dataset: Path) -> None:
    """Identifiers come from the logical name; prose names the file on disk."""
    path = write(dataset, "library.sdf", SDF)
    meta = extract(path)
    meta["relative_path"] = "library.sdf"
    meta["stored_name"] = "library.sdf.gz"

    (record_set,) = HANDLER.build_croissant([meta], ["file_0"]).record_sets

    assert record_set.id == "library"
    assert "library.sdf.gz" in record_set.description


def test_two_files_with_one_basename_get_distinct_identifiers(
    dataset: Path,
) -> None:
    first = write(dataset / "batch1", "library.sdf", SDF)
    second = write(dataset / "batch2", "library.sdf", SDF)

    ids = [rs.id for rs in build(first, second, root=dataset)]

    assert len(set(ids)) == 2


def test_a_v3000_record_whose_counts_are_unreadable_declares_no_range(
    dataset: Path,
) -> None:
    """A count nobody can read costs the range and nothing else: the record is
    still a molecule, and the description says the count was not declared."""
    text = MOL_V3000.replace("COUNTS 42 44", "COUNTS ? ?")

    (record_set,) = build(write(dataset, "unreadable.sdf", text))

    assert (
        extract(write(dataset, "unreadable.sdf", text))["molecules"].atom_range is None
    )
    assert "atom count not declared" in record_set.description
