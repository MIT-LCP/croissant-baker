"""mmCIF and CIF: what the registry-wide sweeps cannot reach.

Unit level for claims and extraction, with one bake at the end, because the
end-to-end suite has no structure collection of its own. Four things the sweeps
cannot express carry most of the weight: the claim needs the extension and the
``data_`` line together, the tokenizer has to survive quoting and text fields,
the two dialects have to be told apart, and the read has to stop at the
coordinate table.
"""

from __future__ import annotations

import json
from pathlib import Path

import mlcroissant as mlc
import pytest

from croissant_baker.entries import Reason
from croissant_baker.handlers import cif_handler
from croissant_baker.handlers.cif_handler import CIFHandler
from croissant_baker.identifiers import serialize_datetime
from croissant_baker.sources import make_source

from tests.helpers import (
    CIF_ATOM_SITE_TEXT,
    CIF_HEADER_TEXT,
    SAMPLES,
    SMALL_MOLECULE_CIF,
    bake,
    bake_with_report,
    cut_gzip,
    file_objects,
    record_sets,
    write_wrapped,
)
from tests.test_pdb_handler import counting_source

HANDLER = CIFHandler()


def write(directory: Path, name: str, payload: bytes) -> Path:
    path = directory / name
    path.write_bytes(payload)
    return path


def source_for(path: Path, relative: str | None = None):
    return make_source(path, Path(relative or path.name))


def sample_cif(dataset: Path, name: str | None = None) -> Path:
    logical, payload = SAMPLES["CIFHandler"]()[0]
    return write(dataset, name or logical, payload)


def extract(path: Path, relative: str | None = None, **kwargs) -> dict:
    return HANDLER.extract(source_for(path, relative), **kwargs)


def without(*names: str) -> str:
    """The sample header, minus every ``#``-separated section naming one of these.

    The fixture separates its categories with comment lines, as a wwPDB entry
    does, so dropping a category is dropping a section rather than editing
    lines out of the middle of one.
    """
    sections = CIF_HEADER_TEXT.split("\n#\n")
    return "\n#\n".join(s for s in sections if not any(n in s for n in names))


def block(*items: str, name: str = "1ABC") -> bytes:
    """A minimal PDBx block carrying ``items`` and nothing else.

    ``_entry.id`` is always written, because it is one of the three items that
    say the block is PDBx and every variant here is meant to be one.
    """
    lines = "".join(f"{item}\n" for item in items)
    return f"data_{name}\n_entry.id   {name}\n{lines}".encode()


def test_an_entry_is_claimed_on_its_extension_and_its_data_line(dataset: Path) -> None:
    assert HANDLER.claims(source_for(sample_cif(dataset)))


def test_the_second_extension_is_claimed(dataset: Path) -> None:
    """``.mmcif`` is the spelling tools that also write core CIF reach for."""
    assert HANDLER.claims(source_for(sample_cif(dataset, "1abc.mmcif")))


def test_a_cif_with_no_data_block_is_not_claimed(dataset: Path) -> None:
    """``.cif`` is also a Windows compiled-installation file and a generic
    extension, so the data block is what says this one is a structure."""
    path = write(dataset, "setup.cif", b"# a comment\n\n_cell_length_a   10.0\n")

    assert not HANDLER.claims(source_for(path))


def test_the_same_bytes_under_another_extension_are_not_claimed(
    dataset: Path,
) -> None:
    """A line opening with ``data_`` is a shape any text file can wear, so the
    data block cannot own a file on its own either."""
    path = write(dataset, "entry.txt", CIF_HEADER_TEXT.encode())

    assert not HANDLER.claims(source_for(path))


def test_the_entry_id_is_read(dataset: Path) -> None:
    assert extract(sample_cif(dataset))["entry_id"] == "1ABC"


def test_the_deposition_date_is_kept_exactly_as_written(dataset: Path) -> None:
    assert extract(sample_cif(dataset))["deposition_date"] == "1998-01-12"


def test_a_multi_line_title_is_joined_with_single_spaces(dataset: Path) -> None:
    """A ``;`` text field is a paragraph the file wrapped for column width, and
    the column it was wrapped at is not part of what was written."""
    assert extract(sample_cif(dataset))["title"] == (
        "Crystal structure of a miniature hydrolase at 1.80 angstrom resolution"
    )


def test_both_experimental_methods_are_read_from_the_loop(dataset: Path) -> None:
    """A structure solved two ways writes ``_exptl`` as a loop rather than as a
    single item, and both spellings have to read the same."""
    assert extract(sample_cif(dataset))["experimental_methods"] == [
        "X-RAY DIFFRACTION",
        "NEUTRON DIFFRACTION",
    ]


def test_a_single_experimental_method_is_read_from_the_item(dataset: Path) -> None:
    path = write(dataset, "single.cif", block("_exptl.method   'X-RAY DIFFRACTION'"))

    assert extract(path)["experimental_methods"] == ["X-RAY DIFFRACTION"]


def test_the_resolution_is_read_from_the_refinement(dataset: Path) -> None:
    assert extract(sample_cif(dataset))["resolution_angstrom"] == 1.80


def test_the_polymer_entities_are_counted_from_entity_poly(dataset: Path) -> None:
    assert extract(sample_cif(dataset))["polymer_entity_count"] == 2


def test_the_chains_are_counted_from_struct_asym(dataset: Path) -> None:
    assert extract(sample_cif(dataset))["chain_count"] == 3


def test_the_chains_are_counted_from_the_strand_ids_when_struct_asym_is_absent(
    dataset: Path,
) -> None:
    """One chain instance per row is the direct statement; a stripped entry that
    does not make it still names its strands on the polymer entities."""
    path = write(dataset, "strands.cif", without("_struct_asym").encode())

    assert extract(path)["chain_count"] == 3


def test_a_block_naming_no_chain_at_all_omits_the_count(dataset: Path) -> None:
    path = write(
        dataset,
        "bare.cif",
        without("_struct_asym", "_entity_poly").encode(),
    )

    assert "chain_count" not in extract(path)


def test_the_classification_and_keywords_are_read(dataset: Path) -> None:
    meta = extract(sample_cif(dataset))

    assert meta["classification"] == "HYDROLASE"
    assert meta["keywords"] == ["HYDROLASE", "SERINE PROTEASE"]


def test_the_dictionary_is_reported_with_its_version(dataset: Path) -> None:
    assert extract(sample_cif(dataset))["dictionary"] == "mmcif_pdbx.dic 5.279"


def test_a_cryo_em_reconstruction_reports_its_own_resolution(dataset: Path) -> None:
    """A structure determined by microscopy has no refinement resolution; what
    it states instead is the resolution of the reconstruction."""
    path = write(
        dataset,
        "em.cif",
        block(
            "_exptl.method   'ELECTRON MICROSCOPY'",
            "_em_3d_reconstruction.resolution   3.20",
        ),
    )

    assert extract(path)["resolution_angstrom"] == 3.20


def test_an_nmr_ensemble_reports_its_models_and_no_resolution(dataset: Path) -> None:
    """An NMR entry writes the refinement item with a ``?``, which states that
    the value is unknown; reporting a resolution anyway would invent one."""
    path = write(
        dataset,
        "nmr.cif",
        block(
            "_exptl.method   'SOLUTION NMR'",
            "_refine.ls_d_res_high   ?",
            "_pdbx_nmr_ensemble.conformers_submitted_total_number   20",
        ),
    )

    meta = extract(path)

    assert meta["model_count"] == 20
    assert "resolution_angstrom" not in meta


def test_a_double_quoted_value_keeps_its_spaces_and_apostrophes(
    dataset: Path,
) -> None:
    """The two quoting characters are interchangeable, which is how a value
    holding one of them is written."""
    path = write(
        dataset,
        "quoted.cif",
        block('_struct.title   "Structure of Doe\'s enzyme, at 1.8 A"'),
    )

    assert extract(path)["title"] == "Structure of Doe's enzyme, at 1.8 A"


def test_the_depositors_are_not_emitted(dataset: Path) -> None:
    """``_audit_author`` names people, which is bibliographic rather than
    structural; the dataset's own creator is a command-line input."""
    meta = extract(sample_cif(dataset))

    assert not [key for key in meta if "author" in key]
    assert "Doe" not in meta["description"]


def test_what_the_header_says_is_stated_in_a_description(dataset: Path) -> None:
    assert extract(sample_cif(dataset))["description"] == (
        "mmCIF structure 1abc.cif (1ABC, HYDROLASE, deposited 1998-01-12; "
        "X-RAY DIFFRACTION, NEUTRON DIFFRACTION at 1.80 A; 2 polymer entities, "
        "3 chains; title: Crystal structure of a miniature hydrolase at 1.80 "
        "angstrom resolution). Described from its header; no coordinate record "
        "was read."
    )


def test_a_pdbx_entry_reports_the_mmcif_media_type(dataset: Path) -> None:
    assert extract(sample_cif(dataset))["encoding_format"] == "chemical/x-mmcif"


def small_molecule(dataset: Path, name: str = "benzene.cif") -> Path:
    return write(dataset, name, SMALL_MOLECULE_CIF)


def test_a_small_molecule_block_reports_its_own_fields(dataset: Path) -> None:
    meta = extract(small_molecule(dataset))

    assert meta["data_block"] == "7101243"
    assert meta["chemical_name"] == "benzene"
    assert meta["formula"] == "C6 H6"
    assert meta["space_group"] == "P 21/c"
    assert meta["wavelength"] == 0.71073


def test_the_cell_is_read_with_its_uncertainties_stripped(dataset: Path) -> None:
    """``10.1234(4)`` is one number and its standard uncertainty. The number is
    the cell edge; the parenthesised digits are a second value about it."""
    assert extract(small_molecule(dataset))["cell"] == {
        "a": 10.1234,
        "b": 5.4321,
        "c": 7.6543,
        "alpha": 90.0,
        "beta": 95.123,
        "gamma": 90.0,
    }


def test_a_small_molecule_block_reports_the_plain_cif_media_type(
    dataset: Path,
) -> None:
    assert extract(small_molecule(dataset))["encoding_format"] == "chemical/x-cif"


def test_a_small_molecule_block_is_described_as_a_crystal_structure(
    dataset: Path,
) -> None:
    assert extract(small_molecule(dataset))["description"] == (
        "CIF crystal structure benzene.cif (data block 7101243; benzene; "
        "C6 H6; P 21/c; a=10.1234, b=5.4321, c=7.6543). Described from its "
        "header; no atom record was read."
    )


def test_the_older_symmetry_spelling_of_the_space_group_is_read(
    dataset: Path,
) -> None:
    """Files written before the space-group category was renamed carry the same
    value under the older item name."""
    path = write(
        dataset,
        "old.cif",
        b"data_x\n_cell_length_a   10.0\n_symmetry_space_group_name_H-M   'P 21/c'\n",
    )

    assert extract(path)["space_group"] == "P 21/c"


#: Comfortably above anything the handler needs for a header this size, and far
#: below the coordinate table the fixture puts behind it.
BOUNDED_PREFIX = 128 * 1024


def test_the_read_stops_at_the_atom_site_loop(dataset: Path) -> None:
    """What this handler exists not to do is read the coordinates.

    Nothing in front of the table says how long the header is, so what bounds
    the read is the stop at the loop whose first item names ``_atom_site``. Four
    megabytes of coordinates behind this header cost the header.
    """
    body = CIF_ATOM_SITE_TEXT + "ATOM 4 C -7.221 2.458 -1.897\n" * 150000
    path = write(dataset, "deep.cif", (CIF_HEADER_TEXT + body).encode())
    opened: list = []
    assert path.stat().st_size > 4 * 1024 * 1024

    meta = HANDLER.extract(counting_source(path, opened))

    assert meta["entry_id"] == "1ABC"
    assert sum(stream.read_bytes for stream in opened) < BOUNDED_PREFIX


def test_a_file_with_no_data_block_is_refused_with_a_reason(dataset: Path) -> None:
    """Claiming and extracting are separate questions: a file reaches extraction
    whenever a caller hands it over directly."""
    path = write(dataset, "notes.cif", b"just some notes about a structure\n")

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "notes.cif" in str(caught.value)


def test_a_text_field_that_never_closes_is_refused_with_a_reason(
    dataset: Path,
) -> None:
    """A ``;`` field runs to the next line opening with ``;``. Without one, the
    value has no end, and everything behind it would be read as part of it."""
    path = write(
        dataset,
        "unclosed.cif",
        b"data_1ABC\n_entry.id   1ABC\n_struct.title\n;a title with no end\n",
    )

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "unclosed.cif" in str(caught.value)


def test_a_block_of_neither_dialect_is_refused_with_a_reason(dataset: Path) -> None:
    """A CIF is a syntax, not a subject: crystallography, powder diffraction and
    dictionaries all share it. This handler describes two of its dialects, and
    says so rather than describing a third badly."""
    path = write(dataset, "other.cif", b"data_x\n_pd_meas.number_of_points   4000\n")

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "other.cif" in str(caught.value)


def test_a_header_above_the_cap_is_refused(
    dataset: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file that never reaches a coordinate table never reaches the stop, so
    the cap is what ends the read. The cap itself is moved rather than the
    fixture grown to sixty-four mebibytes: what is under test is that the read
    ends when the cap is passed, not the number.
    """
    monkeypatch.setattr(cif_handler, "MAX_HEADER_BYTES", 4096)
    padding = "".join(f"_pdbx_note.text{i}   note{i}\n" for i in range(500))
    path = write(dataset, "endless.cif", block() + padding.encode())

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "endless.cif" in str(caught.value)


#: A body with no line ending anywhere in it, and the read a bounded handler
#: may spend before refusing it: the line cap, plus the chunk it was reached in.
#: Four megabytes rather than the eighty a real one runs to, because a reader
#: that accumulates the line re-copies what it holds on every chunk, and the
#: cost of proving that is quadratic in the fixture.
NO_NEWLINE_BYTES = 4 * 1024 * 1024
BOUNDED_REFUSAL = 2 * 1024 * 1024


def test_a_body_holding_no_line_ending_is_refused_after_a_bounded_read(
    dataset: Path,
) -> None:
    """A CIF is a line-oriented format, and the header cap alone does not bound
    a file that holds no line ending: the reader accumulates the line it is
    assembling, so sixty-four mebibytes of it is read, held and re-copied a
    chunk at a time before anything refuses it."""
    path = write(dataset, "unbroken.cif", b"data_1ABC\n" + b"x" * NO_NEWLINE_BYTES)
    opened: list = []

    with pytest.raises(ValueError) as caught:
        HANDLER.extract(counting_source(path, opened))

    assert "unbroken.cif" in str(caught.value)
    assert sum(stream.read_bytes for stream in opened) < BOUNDED_REFUSAL


def long_block(items: int = 1000) -> bytes:
    """One data block long enough that a truncated copy still fills a peek.

    The claim reads a few kilobytes, so a fixture small enough to be cut inside
    them would be refused by the registry rather than by this handler, and the
    refusal under test would never be reached.
    """
    padding = "".join(f"_pdbx_note.text{i}   note{i}\n" for i in range(items))
    return block() + padding.encode()


def test_a_wrapper_ending_mid_stream_is_refused_naming_the_file(
    dataset: Path,
) -> None:
    """A member intact for its first bytes opens, and then ends where the
    download stopped. What that raises is not an ``OSError``, and a file is owed
    a reason naming it either way."""
    path = write(dataset, "cut.cif.gz", cut_gzip(long_block()))

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "cut.cif" in str(caught.value)
    assert "mmCIF" in str(caught.value)


def test_a_refusal_reaches_the_scan_report_through_a_bake(dataset: Path) -> None:
    """A file this handler claims and cannot read is reported by name, with the
    reason it was refused for, and the entry beside it is still described: the
    loss is per-file, never the run."""
    write(dataset, "cut.cif.gz", cut_gzip(long_block()))
    sample_cif(dataset)

    document, report = bake_with_report(dataset)

    assert [o["name"] for o in file_objects(document)] == ["1abc.cif"]
    (refused,) = report.undescribed
    assert refused.name == "cut.cif.gz"
    assert refused.reason is Reason.EXTRACT_FAILED
    assert "cut.cif" in refused.detail


def test_no_coordinate_row_becomes_a_record_set(dataset: Path) -> None:
    """A structure is a file: atoms are records of a molecule, not of a dataset
    schema, and the description is all of it."""
    meta = extract(sample_cif(dataset))
    meta["relative_path"] = "1abc.cif"

    result = HANDLER.build_croissant([meta], ["file_0"])

    assert result.record_sets == []
    assert result.file_sets == []


def test_a_bake_carries_the_description_onto_the_file_object(
    dataset: Path, tmp_path: Path
) -> None:
    """The whole path, once: dispatch, extraction, the FileObject the generator
    owns, and construction under mlcroissant."""
    sample_cif(dataset)

    document = bake(dataset)

    assert record_sets(document) == []
    (described,) = file_objects(document)
    assert described["encodingFormat"] == "chemical/x-mmcif"
    assert "1ABC" in described["description"]

    written = tmp_path / "croissant.jsonld"
    written.write_text(json.dumps(document, indent=2, default=serialize_datetime))
    mlc.Dataset(str(written))


def test_a_wrapped_entry_is_described_like_the_plain_one(dataset: Path) -> None:
    """``1abc.cif.gz`` is how the archive ships an entry, and it is the same
    structure as the file it unpacks to."""
    _, payload = SAMPLES["CIFHandler"]()[0]
    wrapped = write_wrapped(dataset, "1abc.cif", payload, ".gz")

    meta = HANDLER.extract(make_source(wrapped, Path("1abc.cif")))

    assert meta["file_name"] == "1abc.cif"
    assert meta["entry_id"] == "1ABC"
