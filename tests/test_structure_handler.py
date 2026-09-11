"""Tests for the macromolecular structure handler (PDB, mmCIF, small-molecule CIF)."""

from pathlib import Path

import mlcroissant as mlc
import pytest

from croissant_baker.handlers.base_handler import BuildResult
from croissant_baker.handlers.registry import select_handler
from croissant_baker.handlers.structural_biology.structure_handler import (
    StructureHandler,
)
from croissant_baker.sources import make_source


#: A hand-written entry: a header, two protein chains and one water, in a cell.
MINIMAL_PDB = """\
HEADER    HYDROLASE                               01-JAN-00   1ABC
TITLE     A SMALL TEST STRUCTURE
EXPDTA    X-RAY DIFFRACTION
REMARK   2 RESOLUTION.    1.80 ANGSTROMS.
CRYST1   40.000   50.000   60.000  90.00  90.00  90.00 P 21 21 21    4
ATOM      1  N   ALA A   1      11.104   6.134  -6.504  1.00 20.00           N
ATOM      2  CA  ALA A   1      11.639   6.071  -5.147  1.00 20.00           C
ATOM      3  C   ALA A   1      12.253   4.699  -4.914  1.00 20.00           C
HETATM    4  O   HOH A 100      20.000  20.000  20.000  1.00 30.00           O
ATOM      5  N   GLY B   1      15.104   6.134  -6.504  1.00 20.00           N
ATOM      6  CA  GLY B   1      15.639   6.071  -5.147  1.00 20.00           C
END
"""

#: The same entry with no header records at all, so nothing names or dates it.
HEADERLESS_PDB = """\
ATOM      1  N   ALA A   1      11.104   6.134  -6.504  1.00 20.00           N
ATOM      2  CA  ALA A   1      11.639   6.071  -5.147  1.00 20.00           C
END
"""

#: A small-molecule CIF: underscore-style tags, no dotted mmCIF category.
SMALL_MOLECULE_CIF = """\
data_glycine
_chemical_formula_sum             'C2 H5 N O2'
_cell_length_a                    5.1054
_cell_length_b                    11.9688
_cell_length_c                    5.4645
_cell_angle_alpha                 90.0
_cell_angle_beta                  111.78
_cell_angle_gamma                 90.0
_symmetry_space_group_name_H-M    'P 1 21/n 1'
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
N1 N 0.0800 0.1150 0.1250
C1 C 0.1500 0.2250 0.2500
C2 C 0.2500 0.3350 0.3750
O1 O 0.3500 0.4450 0.5000
O2 O 0.4500 0.5550 0.6250
"""

#: A syntactically valid CIF holding neither an entry nor a crystal structure:
#: a block of pairs and a loop, which is what a dictionary or a deposition log
#: is made of.
STRUCTURELESS_CIF = """\
data_notes
_audit_creation_method    'by hand'
_journal_name_full        'Journal of Nothing'
_audit_revision_count     2

loop_
_audit_author_name
_audit_author_address
'Rivera, K.'  'Cambridge, MA'
'Okafor, N.'  'Cambridge, MA'
"""

#: A wwPDB validation report: it names the entry it reports on, so it carries
#: ``_entry.id``, and it holds not one atom site.
VALIDATION_REPORT_CIF = """\
data_1ABC
_entry.id                       1ABC
_pdbx_audit.method              'wwPDB validation'
_pdbx_audit.software_version    2.42

loop_
_pdbx_validate_close_contact.id
_pdbx_validate_close_contact.dist
1 1.85
2 1.92
"""

#: An entry stripped of its coordinates: the ``_atom_site`` loop still declares
#: its columns, and no row follows them.
EMPTY_ATOM_SITE_CIF = """\
data_1ABC
_entry.id    1ABC

loop_
_atom_site.group_PDB
_atom_site.id
_atom_site.Cartn_x
"""

#: A block that declares nothing at all, which is not a table anyone can read.
EMPTY_BLOCK_CIF = "data_nothing\n"


def write_pdb(tmp_path: Path, name: str = "1abc.pdb", text: str = MINIMAL_PDB) -> Path:
    path = tmp_path / name
    path.write_text(text)
    return path


def write_mmcif(tmp_path: Path, name: str = "1abc.cif") -> Path:
    """The mmCIF gemmi writes for :data:`MINIMAL_PDB`, so both describe one entry.

    The block is named for the entry rather than left as the reader's stream
    placeholder, which is what a deposited file looks like.
    """
    import gemmi

    st = gemmi.read_pdb_string(MINIMAL_PDB)
    st.name = "1ABC"
    st.setup_entities()
    path = tmp_path / name
    path.write_text(st.make_mmcif_document().as_string())
    return path


@pytest.fixture
def handler() -> StructureHandler:
    return StructureHandler()


@pytest.mark.parametrize("ext", [".pdb", ".ent", ".cif", ".mmcif"], ids=lambda e: e[1:])
def test_claims_every_declared_extension(
    handler: StructureHandler, tmp_path: Path, ext: str
) -> None:
    path = tmp_path / f"probe{ext}"
    path.write_bytes(b"payload")
    assert handler.claims(make_source(path))


@pytest.mark.parametrize("ext", [".pdb", ".ent", ".cif", ".mmcif"], ids=lambda e: e[1:])
def test_case_never_changes_a_claim(
    handler: StructureHandler, tmp_path: Path, ext: str
) -> None:
    """``X.PDB`` is the same file as ``x.pdb``: ``FileSource.suffix`` lowercases."""
    quiet = tmp_path / f"probe{ext}"
    shouted = tmp_path / f"probe{ext.upper()}"
    quiet.write_bytes(b"payload")
    shouted.write_bytes(b"payload")

    assert (
        handler.claims(make_source(shouted))
        is handler.claims(make_source(quiet))
        is True
    )


def test_declines_another_formats_extension(
    handler: StructureHandler, tmp_path: Path
) -> None:
    path = tmp_path / "scan.nii"
    path.write_bytes(b"payload")
    assert not handler.claims(make_source(path))


def test_a_structure_file_is_routed_to_this_handler(tmp_path: Path) -> None:
    """Registered in ``builtin_handlers``, so a bake reaches this handler at all."""
    assert isinstance(select_handler(write_pdb(tmp_path)).handler, StructureHandler)


def test_the_format_is_declared(handler: StructureHandler) -> None:
    """What the generated documentation table and the contract sweep read."""
    assert handler.EXTENSIONS
    assert all(ext.startswith(".") for ext in handler.EXTENSIONS)
    assert handler.FORMAT_NAME
    assert handler.FORMAT_DESCRIPTION


def test_pdb_header_fields(handler: StructureHandler, tmp_path: Path) -> None:
    meta = handler.extract(make_source(write_pdb(tmp_path)))

    assert meta["file_name"] == "1abc.pdb"
    assert meta["file_size"] > 0
    assert len(meta["sha256"]) == 64
    assert meta["encoding_format"] == "chemical/x-pdb"

    props = meta["structure_properties"]
    assert props["kind"] == "macromolecular"
    assert props["format"] == "PDB"
    assert props["entry_id"] == "1ABC"
    assert props["title"] == "A SMALL TEST STRUCTURE"
    assert props["experimental_method"] == "X-RAY DIFFRACTION"
    assert props["resolution_angstrom"] == pytest.approx(1.80)


def test_pdb_cell_and_space_group(handler: StructureHandler, tmp_path: Path) -> None:
    props = handler.extract(make_source(write_pdb(tmp_path)))["structure_properties"]

    assert props["space_group"] == "P 21 21 21"
    assert props["unit_cell"] == pytest.approx((40.0, 50.0, 60.0, 90.0, 90.0, 90.0))


def test_pdb_counts(handler: StructureHandler, tmp_path: Path) -> None:
    props = handler.extract(make_source(write_pdb(tmp_path)))["structure_properties"]

    assert props["n_models"] == 1
    assert props["n_chains"] == 2
    assert props["n_residues"] == 3
    assert props["n_atoms"] == 6


def test_a_headerless_pdb_reports_empty_header_fields(
    handler: StructureHandler, tmp_path: Path
) -> None:
    """No HEADER, TITLE or EXPDTA, and nothing invented in their place."""
    path = write_pdb(tmp_path, "fragment.pdb", HEADERLESS_PDB)
    props = handler.extract(make_source(path))["structure_properties"]

    assert props["entry_id"] == ""
    assert props["title"] == ""
    assert props["experimental_method"] == ""
    assert "resolution_angstrom" not in props
    assert "space_group" not in props
    assert "unit_cell" not in props
    assert props["n_atoms"] == 2


def test_an_ent_file_is_read_as_pdb(handler: StructureHandler, tmp_path: Path) -> None:
    """``.ent`` is the other spelling the PDB archive uses for the same records."""
    meta = handler.extract(make_source(write_pdb(tmp_path, "pdb1abc.ent")))

    assert meta["encoding_format"] == "chemical/x-pdb"
    assert meta["structure_properties"]["entry_id"] == "1ABC"


def test_mmcif_header_fields(handler: StructureHandler, tmp_path: Path) -> None:
    meta = handler.extract(make_source(write_mmcif(tmp_path)))

    assert meta["file_name"] == "1abc.cif"
    assert meta["encoding_format"] == "chemical/x-mmcif"

    props = meta["structure_properties"]
    assert props["kind"] == "macromolecular"
    assert props["format"] == "mmCIF"
    assert props["entry_id"] == "1ABC"
    assert props["title"] == "A SMALL TEST STRUCTURE"
    assert props["experimental_method"] == "X-RAY DIFFRACTION"
    assert props["space_group"] == "P 21 21 21"
    assert props["unit_cell"] == pytest.approx((40.0, 50.0, 60.0, 90.0, 90.0, 90.0))


def test_mmcif_counts(handler: StructureHandler, tmp_path: Path) -> None:
    """The same entry as the PDB fixture, so the counts have to agree with it."""
    props = handler.extract(make_source(write_mmcif(tmp_path)))["structure_properties"]

    assert props["n_models"] == 1
    assert props["n_chains"] == 2
    assert props["n_residues"] == 3
    assert props["n_atoms"] == 6


def test_a_resolution_nobody_stated_is_absent(
    handler: StructureHandler, tmp_path: Path
) -> None:
    """gemmi writes no ``_refine.ls_d_res_high``, and an unstated resolution is
    not a resolution of zero."""
    props = handler.extract(make_source(write_mmcif(tmp_path)))["structure_properties"]

    assert "resolution_angstrom" not in props


def test_an_mmcif_suffix_is_read_the_same_way(
    handler: StructureHandler, tmp_path: Path
) -> None:
    meta = handler.extract(make_source(write_mmcif(tmp_path, "1abc.mmcif")))

    assert meta["encoding_format"] == "chemical/x-mmcif"
    assert meta["structure_properties"]["format"] == "mmCIF"


def test_small_molecule_cif(handler: StructureHandler, tmp_path: Path) -> None:
    path = tmp_path / "glycine.cif"
    path.write_text(SMALL_MOLECULE_CIF)

    props = handler.extract(make_source(path))["structure_properties"]

    assert props["kind"] == "small molecule"
    assert props["format"] == "CIF"
    assert props["entry_id"] == "glycine"
    assert props["formula"] == "C2 H5 N O2"
    assert props["space_group"] == "P 1 21/n 1"
    assert props["unit_cell"] == pytest.approx(
        (5.1054, 11.9688, 5.4645, 90.0, 111.78, 90.0)
    )
    assert props["n_atoms"] == 5


def test_a_small_molecule_cif_is_not_called_mmcif(
    handler: StructureHandler, tmp_path: Path
) -> None:
    """mmCIF is the macromolecular dictionary. A crystal of one molecule is
    written in the core dictionary, and saying otherwise misnames the file."""
    path = tmp_path / "glycine.cif"
    path.write_text(SMALL_MOLECULE_CIF)

    assert handler.extract(make_source(path))["encoding_format"] == "chemical/x-cif"


def test_an_entry_written_in_mmcif_says_so(
    handler: StructureHandler, tmp_path: Path
) -> None:
    meta = handler.extract(make_source(write_mmcif(tmp_path)))

    assert meta["encoding_format"] == "chemical/x-mmcif"


def test_a_small_molecule_cif_reports_no_chains(
    handler: StructureHandler, tmp_path: Path
) -> None:
    """A crystal of one molecule has no models, chains or residues to count."""
    path = tmp_path / "glycine.cif"
    path.write_text(SMALL_MOLECULE_CIF)

    props = handler.extract(make_source(path))["structure_properties"]

    assert "n_models" not in props
    assert "n_chains" not in props
    assert "n_residues" not in props


def test_only_the_first_block_of_a_cif_is_read(
    handler: StructureHandler, tmp_path: Path
) -> None:
    """A deposited mmCIF is often followed by chemical component blocks, and
    the entry is the first one."""
    path = tmp_path / "1abc.cif"
    path.write_text(
        write_mmcif(tmp_path, "source.cif").read_text() + "\ndata_comp_ALA\n_x 1\n"
    )

    props = handler.extract(make_source(path))["structure_properties"]

    assert props["entry_id"] == "1ABC"
    assert props["n_atoms"] == 6


def write_generic_cif(tmp_path: Path, name: str = "notes.cif") -> Path:
    path = tmp_path / name
    path.write_text(STRUCTURELESS_CIF)
    return path


def test_a_cif_without_a_structure_is_described_as_its_tables(
    handler: StructureHandler, tmp_path: Path
) -> None:
    """One suffix covers three dictionaries, and only two of them hold atoms.
    The third is still a CIF document, so it is described as the tables it is
    rather than refused for the structure it never claimed to have."""
    meta = handler.extract(make_source(write_generic_cif(tmp_path)))

    assert "structure_properties" not in meta
    assert [(table.block, table.kind) for table in meta["tables"]] == [
        ("notes", "pairs"),
        ("notes", "loop"),
    ]


def test_a_cif_document_says_it_is_not_a_structure(
    handler: StructureHandler, tmp_path: Path
) -> None:
    """Its own media type, because ``chemical/x-mmcif`` would claim an entry."""
    meta = handler.extract(make_source(write_generic_cif(tmp_path)))

    assert meta["encoding_format"] == "chemical/x-cif"


def test_a_validation_report_is_described_as_its_tables(
    handler: StructureHandler, tmp_path: Path
) -> None:
    """``_entry.id`` says which entry a document is about, not that it holds
    one. A validation report carries the tag and no atom site, so it takes the
    path a dictionary takes rather than being read as a structure with nothing
    in it."""
    path = tmp_path / "1abc_validation.cif"
    path.write_text(VALIDATION_REPORT_CIF)

    meta = handler.extract(make_source(path))

    assert "structure_properties" not in meta
    assert [(table.block, table.kind) for table in meta["tables"]] == [
        ("1ABC", "pairs"),
        ("1ABC", "loop"),
    ]
    assert meta["encoding_format"] == "chemical/x-cif"


def test_an_entry_whose_atom_sites_are_all_gone_is_described_as_its_tables(
    handler: StructureHandler, tmp_path: Path
) -> None:
    """A loop that declares its columns and no row builds a structure with no
    model. Counting chains in one raises, so the columns are what is left to
    describe."""
    path = tmp_path / "stripped.cif"
    path.write_text(EMPTY_ATOM_SITE_CIF)

    meta = handler.extract(make_source(path))

    assert "structure_properties" not in meta
    assert [table.kind for table in meta["tables"]] == ["pairs", "loop"]


def test_a_cif_block_declaring_nothing_is_refused(
    handler: StructureHandler, tmp_path: Path
) -> None:
    """No structure and no table either, so there is nothing to describe and
    the file has to be reported rather than silently contribute nothing."""
    path = tmp_path / "empty.cif"
    path.write_text(EMPTY_BLOCK_CIF)

    with pytest.raises(ValueError) as caught:
        handler.extract(make_source(path))

    assert "empty.cif" in str(caught.value)


def test_a_missing_file_raises_file_not_found(
    handler: StructureHandler, tmp_path: Path
) -> None:
    """One contract, so the pipeline reports one reason category."""
    with pytest.raises(FileNotFoundError):
        handler.extract(make_source(tmp_path / "gone" / "1abc.pdb"))


@pytest.mark.parametrize("name", ["1abc.pdb", "1abc.ent", "1abc.cif", "1abc.mmcif"])
def test_garbage_bytes_raise_a_value_error_naming_the_file(
    handler: StructureHandler, tmp_path: Path, name: str
) -> None:
    """The message becomes the reason detail a user reads in ``--report``, so
    the file name alone is not enough: a report ending in a bare colon tells
    nobody what went wrong."""
    path = tmp_path / name
    path.write_bytes(b"\x00\xff not a real file \xfe\x00")

    with pytest.raises(ValueError) as caught:
        handler.extract(make_source(path))

    message = str(caught.value)
    assert name in message
    assert message.partition(f"{name}:")[2].strip()


def test_an_empty_batch_describes_nothing(handler: StructureHandler) -> None:
    result = handler.build_croissant([], [])

    assert isinstance(result, BuildResult)
    assert result.file_sets == []
    assert result.record_sets == []


def macro_meta(file_name: str, **overrides) -> dict:
    """One extracted macromolecular file, as the generator hands it back."""
    props = {
        "kind": "macromolecular",
        "format": "PDB" if file_name.endswith((".pdb", ".ent")) else "mmCIF",
        "entry_id": "1ABC",
        "title": "A small test structure",
        "experimental_method": "X-RAY DIFFRACTION",
        "resolution_angstrom": 1.8,
        "space_group": "P 21 21 21",
        "unit_cell": (40.0, 50.0, 60.0, 90.0, 90.0, 90.0),
        "n_models": 1,
        "n_chains": 2,
        "n_residues": 3,
        "n_atoms": 6,
    }
    props.update(overrides)
    return {
        "file_name": file_name,
        "stored_name": file_name,
        "encoding_format": "chemical/x-pdb"
        if file_name.endswith((".pdb", ".ent"))
        else "chemical/x-mmcif",
        "structure_properties": props,
    }


def test_build_returns_one_fileset_and_one_recordset(
    handler: StructureHandler,
) -> None:
    metas = [macro_meta("1abc.pdb"), macro_meta("2xyz.cif")]

    file_sets, record_sets = handler.build_croissant(metas, ["file_0", "file_1"])

    assert len(file_sets) == 1
    assert len(record_sets) == 1


def test_the_fileset_globs_only_the_suffixes_the_batch_carries(
    handler: StructureHandler,
) -> None:
    """In the order the handler declares them, and with no compression: the
    generator widens each glob to cover every registered wrapper."""
    metas = [macro_meta("2xyz.cif"), macro_meta("1abc.pdb")]

    file_sets, _ = handler.build_croissant(metas, ["file_0", "file_1"])

    assert file_sets[0].includes == ["**/*.pdb", "**/*.cif"]


def test_the_fileset_carries_the_media_types_of_the_batch(
    handler: StructureHandler,
) -> None:
    metas = [macro_meta("1abc.pdb"), macro_meta("2xyz.cif")]

    file_sets, _ = handler.build_croissant(metas, ["file_0", "file_1"])

    assert file_sets[0].encoding_formats == ["chemical/x-mmcif", "chemical/x-pdb"]


def test_the_recordset_is_named_for_the_structures(handler: StructureHandler) -> None:
    _, record_sets = handler.build_croissant([macro_meta("1abc.pdb")], ["file_0"])

    assert record_sets[0].id == "structures"
    assert record_sets[0].name == "structures"


def test_the_recordset_fields(handler: StructureHandler) -> None:
    _, record_sets = handler.build_croissant([macro_meta("1abc.pdb")], ["file_0"])

    names = [field.name for field in record_sets[0].fields]
    assert names == [
        "entry_id",
        "title",
        "experimental_method",
        "resolution_angstrom",
        "space_group",
        "unit_cell",
        "n_models",
        "n_chains",
        "n_residues",
        "n_atoms",
        "format",
    ]


def test_every_field_reads_the_content_of_the_fileset(
    handler: StructureHandler,
) -> None:
    file_sets, record_sets = handler.build_croissant([macro_meta("1abc.pdb")], ["f0"])

    for field in record_sets[0].fields:
        assert field.id == f"structures/{field.name}"
        assert field.source.file_set == file_sets[0].id
        assert field.source.extract.file_property is mlc.FileProperty.content
        assert field.data_types


def test_a_batch_with_no_resolution_drops_the_field(
    handler: StructureHandler,
) -> None:
    """Cryo-EM and NMR entries state none, and a field nothing fills would
    promise a column that is empty in every row."""
    metas = [macro_meta("1abc.cif", resolution_angstrom=None)]
    del metas[0]["structure_properties"]["resolution_angstrom"]

    _, record_sets = handler.build_croissant(metas, ["file_0"])

    assert "resolution_angstrom" not in {f.name for f in record_sets[0].fields}


def test_a_single_file_batch_is_named_in_the_descriptions(
    handler: StructureHandler,
) -> None:
    """The stored name, so a reader can find the file the description is about."""
    meta = macro_meta("1abc.pdb")
    meta["stored_name"] = "1abc.pdb.gz"

    file_sets, record_sets = handler.build_croissant([meta], ["file_0"])

    assert "1abc.pdb.gz" in file_sets[0].description
    assert "1abc.pdb.gz" in record_sets[0].description


def test_the_descriptions_summarise_the_batch(handler: StructureHandler) -> None:
    metas = [
        macro_meta("1abc.pdb"),
        macro_meta("2xyz.cif", experimental_method="ELECTRON MICROSCOPY"),
        macro_meta("3def.cif", resolution_angstrom=3.4),
    ]

    file_sets, record_sets = handler.build_croissant(metas, ["f0", "f1", "f2"])

    assert "3 structure file" in file_sets[0].description
    description = record_sets[0].description
    assert "PDB" in description and "mmCIF" in description
    assert "X-RAY DIFFRACTION" in description
    assert "ELECTRON MICROSCOPY" in description
    assert "1.8" in description and "3.4" in description


def test_a_small_molecule_batch_describes_its_formula(
    handler: StructureHandler,
) -> None:
    """Nothing else in the record set carries it, so dropping the field would
    lose the one thing a core-dictionary CIF is read for."""
    meta = {
        "file_name": "glycine.cif",
        "stored_name": "glycine.cif",
        "encoding_format": "chemical/x-mmcif",
        "structure_properties": {
            "kind": "small molecule",
            "format": "CIF",
            "entry_id": "glycine",
            "formula": "C2 H5 N O2",
            "space_group": "P 1 21/n 1",
            "unit_cell": (5.1054, 11.9688, 5.4645, 90.0, 111.78, 90.0),
            "n_atoms": 5,
        },
    }

    _, record_sets = handler.build_croissant([meta], ["file_0"])

    assert "formula" in {f.name for f in record_sets[0].fields}


def test_a_macromolecular_batch_carries_no_formula_field(
    handler: StructureHandler,
) -> None:
    _, record_sets = handler.build_croissant([macro_meta("1abc.pdb")], ["file_0"])

    assert "formula" not in {f.name for f in record_sets[0].fields}


def document_metas(handler: StructureHandler, tmp_path: Path, *names: str) -> list:
    """Extracted generic CIF documents, as the generator hands them back."""
    metas = []
    for name in names:
        meta = handler.extract(make_source(write_generic_cif(tmp_path, name)))
        meta["relative_path"] = name
        meta["stored_name"] = name
        metas.append(meta)
    return metas


def test_a_generic_cif_becomes_one_record_set_per_table(
    handler: StructureHandler, tmp_path: Path
) -> None:
    """The same shape the STAR handler gives a document, because it is the same
    grammar: a record set per table, named for the file and the block."""
    metas = document_metas(handler, tmp_path, "notes.cif")

    file_sets, record_sets = handler.build_croissant(metas, ["file_0"])

    assert file_sets == []
    assert [rs.id for rs in record_sets] == ["notes_notes_1", "notes_notes_2"]
    assert all(rs.name == rs.id for rs in record_sets)


def test_a_generic_cifs_columns_are_typed_fields_reading_no_value(
    handler: StructureHandler, tmp_path: Path
) -> None:
    metas = document_metas(handler, tmp_path, "notes.cif")

    _, record_sets = handler.build_croissant(metas, ["file_0"])

    pairs = record_sets[0]
    assert [(f.name, str(f.data_types[0])) for f in pairs.fields] == [
        ("audit_creation_method", "sc:Text"),
        ("journal_name_full", "sc:Text"),
        ("audit_revision_count", "sc:Integer"),
    ]
    assert all(f.source.extract.column is None for f in pairs.fields)
    assert "No value is emitted." in pairs.description


def test_two_documents_of_one_name_get_identifiers_of_their_own(
    handler: StructureHandler, tmp_path: Path
) -> None:
    """The same disambiguation the STAR handler does, because a CIF dictionary
    is as likely to sit one per directory as a RELION job is."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    metas = document_metas(handler, tmp_path, "a/notes.cif", "b/notes.cif")

    _, record_sets = handler.build_croissant(metas, ["file_0", "file_1"])

    assert len({rs.id for rs in record_sets}) == len(record_sets) == 4


def test_a_mixed_batch_describes_structures_and_documents_apart(
    handler: StructureHandler, tmp_path: Path
) -> None:
    """One bake reaches this handler with both, and neither may swallow the
    other: the structures share a FileSet and a record set, and each document
    gets record sets of its own."""
    structure = handler.extract(make_source(write_pdb(tmp_path)))
    structure["relative_path"] = structure["stored_name"] = "1abc.pdb"
    metas = [structure, *document_metas(handler, tmp_path, "notes.cif")]

    file_sets, record_sets = handler.build_croissant(metas, ["file_0", "file_1"])

    assert [fs.includes for fs in file_sets] == [["**/*.pdb"]]
    assert [rs.id for rs in record_sets] == [
        "structures",
        "notes_notes_1",
        "notes_notes_2",
    ]
    assert "1 structure file(s), 1abc.pdb" in file_sets[0].description


def test_the_fileset_excludes_a_document_its_glob_would_reach(
    handler: StructureHandler, tmp_path: Path
) -> None:
    """``**/*.cif`` matches a CIF dictionary as readily as an entry, and the
    record set reading this FileSet says one record per structure file. A
    document caught by the glob would be counted as a structure it is not."""
    structure = handler.extract(make_source(write_mmcif(tmp_path)))
    structure["relative_path"] = structure["stored_name"] = "1abc.cif"
    metas = [structure, *document_metas(handler, tmp_path, "notes.cif")]

    file_sets, _ = handler.build_croissant(metas, ["file_0", "file_1"])

    assert file_sets[0].includes == ["**/*.cif"]
    assert file_sets[0].excludes == ["notes.cif"]


def test_an_exclusion_is_rendered_as_a_glob_with_forward_slashes(
    handler: StructureHandler, tmp_path: Path
) -> None:
    """An exclude is a glob, and a glob separates its components with ``/``
    wherever it is read. A path built from its parts separates them with the
    host's separator, which on Windows is not that."""
    structure = handler.extract(make_source(write_mmcif(tmp_path)))
    structure["relative_path"] = structure["stored_name"] = "1abc.cif"
    document = handler.extract(make_source(write_generic_cif(tmp_path)))
    document["relative_path"] = Path("dictionaries") / "audit.cif"

    file_sets, _ = handler.build_croissant([structure, document], ["file_0", "file_1"])

    assert file_sets[0].excludes == ["dictionaries/audit.cif"]


def test_a_batch_of_structures_alone_excludes_nothing(
    handler: StructureHandler, tmp_path: Path
) -> None:
    """An exclusion naming no file would be a claim about a file that is not
    there."""
    file_sets, _ = handler.build_croissant([macro_meta("1abc.pdb")], ["file_0"])

    assert not file_sets[0].excludes


def test_a_documents_fields_point_at_that_document(
    handler: StructureHandler, tmp_path: Path
) -> None:
    """The identifier the pipeline gave *that* file, not the batch's first: a
    mixed batch is where a positional slip would go unnoticed."""
    structure = handler.extract(make_source(write_pdb(tmp_path)))
    structure["relative_path"] = structure["stored_name"] = "1abc.pdb"
    metas = [structure, *document_metas(handler, tmp_path, "notes.cif")]

    _, record_sets = handler.build_croissant(metas, ["file_0", "file_1"])

    documents = [rs for rs in record_sets if rs.id != "structures"]
    assert documents
    for record_set in documents:
        assert all(f.source.file_object == "file_1" for f in record_set.fields)
        assert "notes.cif" in record_set.description


def test_a_batch_of_documents_alone_builds_no_fileset(
    handler: StructureHandler, tmp_path: Path
) -> None:
    """A FileSet over documents would say they share a schema, and two CIF
    dictionaries share nothing but their grammar."""
    metas = document_metas(handler, tmp_path, "notes.cif")

    result = handler.build_croissant(metas, ["file_0"])

    assert result.file_sets == []
    assert result.record_sets


def test_a_real_batch_returns_a_build_result(
    handler: StructureHandler, tmp_path: Path
) -> None:
    """The path a bake actually takes: extracted metadata, not hand-built."""
    metas, ids = [], []
    for i, path in enumerate([write_pdb(tmp_path), write_mmcif(tmp_path)]):
        meta = handler.extract(make_source(path))
        meta["relative_path"] = path.name
        metas.append(meta)
        ids.append(f"file_{i}")

    result = handler.build_croissant(metas, ids)

    assert isinstance(result, BuildResult)
    assert result.file_sets and result.record_sets
    assert result.declined == ()
