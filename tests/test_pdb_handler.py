"""PDB: what the registry-wide sweeps cannot reach.

Unit level for claims and extraction, with one bake at the end, because the
end-to-end suite has no structure collection of its own. Three things the
sweeps cannot express carry most of the weight: the claim needs the extension
and the record name together, the fields sit at fixed columns, and the read has
to stop at the first coordinate record.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import mlcroissant as mlc
import pytest

from croissant_baker.entries import Reason
from croissant_baker.handlers import pdb_handler
from croissant_baker.handlers.pdb_handler import PDBHandler
from croissant_baker.identifiers import serialize_datetime
from croissant_baker.sources import FileSource, make_source

from tests.helpers import (
    PDB_COORDINATE_TEXT,
    PDB_HEADER_TEXT,
    PDB_TITLE_RECORDS,
    SAMPLES,
    bake,
    bake_with_report,
    cut_gzip,
    file_objects,
    pdb_record,
    record_sets,
    write_wrapped,
)

HANDLER = PDBHandler()


def write(directory: Path, name: str, payload: bytes) -> Path:
    path = directory / name
    path.write_bytes(payload)
    return path


def source_for(path: Path, relative: str | None = None):
    return make_source(path, Path(relative or path.name))


def sample_pdb(dataset: Path, name: str | None = None) -> Path:
    logical, payload = SAMPLES["PDBHandler"]()[0]
    return write(dataset, name or logical, payload)


def extract(path: Path, relative: str | None = None, **kwargs) -> dict:
    return HANDLER.extract(source_for(path, relative), **kwargs)


def variant(dataset: Path, records: tuple, name: str = "variant.pdb") -> Path:
    """A structure carrying ``records`` as its title section, coordinates after."""
    text = "".join(pdb_record(record) for record in records) + PDB_COORDINATE_TEXT
    return write(dataset, name, text.encode())


def without(*prefixes: str) -> tuple:
    """The sample's title records, minus every one opening with a prefix."""
    return tuple(r for r in PDB_TITLE_RECORDS if not r.startswith(prefixes))


def test_a_structure_is_claimed_on_its_extension_and_its_first_record_name(
    dataset: Path,
) -> None:
    assert HANDLER.claims(source_for(sample_pdb(dataset)))


def test_an_archive_copy_named_ent_is_claimed(dataset: Path) -> None:
    """``pdb1abc.ent`` is what the RCSB archive calls its own copy of an entry."""
    assert HANDLER.claims(source_for(sample_pdb(dataset, "pdb1abc.ent")))


def test_a_program_database_under_the_same_extension_is_not_claimed(
    dataset: Path,
) -> None:
    """``.pdb`` is also Microsoft's program database, a binary that carries
    debugging symbols and no structure at all."""
    path = write(dataset, "app.pdb", b"Microsoft C/C++ MSF 7.00\r\n\x1aDS\x00\x00\x00")

    assert not HANDLER.claims(source_for(path))


def test_the_same_records_under_another_extension_are_not_claimed(
    dataset: Path,
) -> None:
    """Six columns of upper-case letters are a shape any text file can wear, so
    the record name cannot own a file on its own either."""
    path = write(dataset, "header.txt", PDB_HEADER_TEXT.encode())

    assert not HANDLER.claims(source_for(path))


def test_the_id_code_and_classification_are_read_from_the_header_record(
    dataset: Path,
) -> None:
    meta = extract(sample_pdb(dataset))

    assert meta["id_code"] == "1ABC"
    assert meta["classification"] == "HYDROLASE"


def test_the_deposition_date_is_kept_exactly_as_written(dataset: Path) -> None:
    """``DD-MMM-YY`` is what the record holds, and converting it would be
    inventing a century the file does not state."""
    assert extract(sample_pdb(dataset))["deposition_date"] == "12-JAN-98"


def test_the_title_continuation_lines_are_joined(dataset: Path) -> None:
    meta = extract(sample_pdb(dataset))

    assert meta["title"] == (
        "CRYSTAL STRUCTURE OF A MINIATURE HYDROLASE AT 1.80 ANGSTROM RESOLUTION"
    )


def test_the_experimental_method_is_read(dataset: Path) -> None:
    assert extract(sample_pdb(dataset))["experimental_methods"] == ["X-RAY DIFFRACTION"]


def test_several_experimental_methods_are_split_on_semicolons(dataset: Path) -> None:
    """A structure solved two ways declares both in one EXPDTA record."""
    records = without("EXPDTA") + ("EXPDTA    X-RAY DIFFRACTION; NEUTRON DIFFRACTION",)

    assert extract(variant(dataset, records))["experimental_methods"] == [
        "X-RAY DIFFRACTION",
        "NEUTRON DIFFRACTION",
    ]


def test_the_resolution_is_read_from_remark_2(dataset: Path) -> None:
    assert extract(sample_pdb(dataset))["resolution_angstrom"] == 1.80


def test_a_structure_with_no_applicable_resolution_reports_none(
    dataset: Path,
) -> None:
    """An NMR structure writes the remark and no number, and a handler that
    reported one anyway would be inventing it."""
    records = without("REMARK") + ("REMARK   2 RESOLUTION. NOT APPLICABLE.",)

    assert "resolution_angstrom" not in extract(variant(dataset, records))


def test_the_chains_are_counted_from_the_compnd_records(dataset: Path) -> None:
    assert extract(sample_pdb(dataset))["chain_count"] == 2


def test_the_chains_are_counted_from_seqres_when_compnd_names_none(
    dataset: Path,
) -> None:
    """A file whose COMPND names no chain still says how many there are, one
    SEQRES chain column at a time."""
    records = tuple(r for r in PDB_TITLE_RECORDS if "CHAIN:" not in r)

    assert extract(variant(dataset, records))["chain_count"] == 2


def test_a_structure_naming_no_chain_at_all_omits_the_count(dataset: Path) -> None:
    records = without("COMPND", "SEQRES")

    assert "chain_count" not in extract(variant(dataset, records))


def test_the_model_count_is_read_from_nummdl(dataset: Path) -> None:
    records = PDB_TITLE_RECORDS + ("NUMMDL    2",)

    assert extract(variant(dataset, records))["model_count"] == 2


def test_a_structure_declaring_no_model_count_omits_it(dataset: Path) -> None:
    assert "model_count" not in extract(sample_pdb(dataset))


def test_the_keywords_are_split_on_commas(dataset: Path) -> None:
    assert extract(sample_pdb(dataset))["keywords"] == [
        "HYDROLASE",
        "SERINE PROTEASE",
    ]


def test_the_depositors_are_not_emitted(dataset: Path) -> None:
    """The AUTHOR record names people, which is bibliographic rather than
    structural; the dataset's own creator is a command-line input."""
    meta = extract(sample_pdb(dataset))

    assert not [key for key in meta if "author" in key]
    assert "J.DOE" not in meta["description"]


def test_what_the_header_says_is_stated_in_a_description(dataset: Path) -> None:
    described = extract(sample_pdb(dataset))["description"]

    assert described == (
        "PDB structure 1abc.pdb (1ABC, HYDROLASE, deposited 12-JAN-98; "
        "X-RAY DIFFRACTION at 1.80 A; 2 chains; title: CRYSTAL STRUCTURE OF A "
        "MINIATURE HYDROLASE AT 1.80 ANGSTROM RESOLUTION). Described from its "
        "header; no coordinate record was read."
    )


def test_a_fragment_with_no_header_record_is_claimed_and_described(
    dataset: Path,
) -> None:
    """A modelling tool writes coordinates and no title section at all. The file
    is still a PDB, and what it does not carry is what the description says."""
    path = write(dataset, "fragment.pdb", PDB_COORDINATE_TEXT.encode())

    assert HANDLER.claims(source_for(path))
    meta = extract(path)
    assert "id_code" not in meta
    assert meta["description"] == (
        "PDB structure fragment.pdb. Described from its header, which carries "
        "no ID code; no coordinate record was read."
    )


def test_a_file_whose_first_line_is_no_record_name_is_refused_with_a_reason(
    dataset: Path,
) -> None:
    """Claiming and extracting are separate questions: a file reaches extraction
    whenever a caller hands it over directly."""
    path = write(dataset, "notes.pdb", b"just some notes about a structure\n")

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "notes.pdb" in str(caught.value)


def test_an_empty_file_is_refused_with_a_reason(dataset: Path) -> None:
    path = write(dataset, "empty.pdb", b"")

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "empty.pdb" in str(caught.value)


class _Counted(io.RawIOBase):
    """A raw stream that remembers how many bytes were pulled through it.

    Raw rather than a plain delegate, because this handler reads the title
    section a chunk at a time and only a real buffered reader over a real raw
    stream reports the bytes that were actually taken off the disk.
    """

    def __init__(self, stream) -> None:
        self._stream = stream
        self.read_bytes = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        count = self._stream.readinto(buffer)
        self.read_bytes += count or 0
        return count

    def close(self) -> None:
        self._stream.close()
        super().close()


def counting_source(path: Path, opened: list):
    """A source over ``path`` whose every stream is counted in ``opened``."""

    def open_binary():
        raw = _Counted(path.open("rb"))
        opened.append(raw)
        return io.BufferedReader(raw)

    return FileSource(
        name=path.name,
        relative_path=Path(path.name),
        size=path.stat().st_size,
        exists=True,
        _open_binary=open_binary,
        _digest=lambda: "0" * 64,
    )


#: Comfortably above anything the handler needs to read a small title section,
#: and far below the coordinates the fixture puts behind it.
BOUNDED_PREFIX = 64 * 1024


def test_the_read_stops_at_the_first_coordinate_record(dataset: Path) -> None:
    """What this handler exists not to do is read the atoms.

    Nothing in front of a title section says how long it is, so the only thing
    that bounds the read is the stop at the first coordinate record. Four
    megabytes of atoms behind a sixteen-record title section cost the title
    section.
    """
    body = PDB_COORDINATE_TEXT * 60000
    path = write(dataset, "deep.pdb", (PDB_HEADER_TEXT + body).encode())
    opened: list = []
    assert path.stat().st_size > 4 * 1024 * 1024

    meta = HANDLER.extract(counting_source(path, opened))

    assert meta["id_code"] == "1ABC"
    assert sum(stream.read_bytes for stream in opened) < BOUNDED_PREFIX


def test_a_title_section_above_the_cap_is_refused(
    dataset: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file of REMARK records and no coordinate record never reaches the stop,
    so the cap is what ends the read. The cap itself is moved rather than the
    fixture grown to sixty-four mebibytes: what is under test is that the read
    ends when the cap is passed, not the number.
    """
    monkeypatch.setattr(pdb_handler, "MAX_HEADER_BYTES", 4096)
    remarks = tuple("REMARK 999 A REMARK WITH NOTHING IN IT" for _ in range(200))
    path = write(
        dataset,
        "remarks.pdb",
        "".join(pdb_record(record) for record in remarks).encode(),
    )

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "remarks.pdb" in str(caught.value)


#: A body with no line ending anywhere in it, and the read a bounded handler may
#: spend before refusing it: the line cap, plus the chunk it was reached in.
NO_NEWLINE_BYTES = 1024 * 1024
BOUNDED_REFUSAL = 128 * 1024


def test_a_body_holding_no_line_ending_is_refused_after_a_bounded_read(
    dataset: Path,
) -> None:
    """A PDB record is eighty columns and then a line ending. A file whose first
    line runs to kilobytes is not a record, and reading on for the end of it is
    reading the file this handler exists not to read."""
    path = write(dataset, "unbroken.pdb", b"HEADER    " + b"x" * NO_NEWLINE_BYTES)
    opened: list = []

    with pytest.raises(ValueError) as caught:
        HANDLER.extract(counting_source(path, opened))

    assert "unbroken.pdb" in str(caught.value)
    assert sum(stream.read_bytes for stream in opened) < BOUNDED_REFUSAL


def test_a_wrapper_ending_mid_stream_is_refused_naming_the_file(
    dataset: Path,
) -> None:
    """A member intact for its first bytes opens, and then ends where the
    download stopped. What that raises is not an ``OSError``, and a file is owed
    a reason naming it either way."""
    path = write(dataset, "cut.pdb.gz", cut_gzip(PDB_HEADER_TEXT.encode()))

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "cut.pdb" in str(caught.value)
    assert "PDB" in str(caught.value)


def test_a_refusal_reaches_the_scan_report_through_a_bake(dataset: Path) -> None:
    """A file this handler claims and cannot read is reported by name, with the
    reason it was refused for, and the structure beside it is still described:
    the loss is per-file, never the run."""
    write(dataset, "cut.pdb.gz", cut_gzip(PDB_HEADER_TEXT.encode()))
    sample_pdb(dataset)

    document, report = bake_with_report(dataset)

    assert [o["name"] for o in file_objects(document)] == ["1abc.pdb"]
    (refused,) = report.undescribed
    assert refused.name == "cut.pdb.gz"
    assert refused.reason is Reason.EXTRACT_FAILED
    assert "cut.pdb" in refused.detail


def test_no_atom_record_becomes_a_record_set(dataset: Path) -> None:
    """A structure is a file: atoms are records of a molecule, not of a dataset
    schema, and the description is all of it."""
    meta = extract(sample_pdb(dataset))
    meta["relative_path"] = "1abc.pdb"

    result = HANDLER.build_croissant([meta], ["file_0"])

    assert result.record_sets == []
    assert result.file_sets == []


def test_a_bake_carries_the_description_onto_the_file_object(
    dataset: Path, tmp_path: Path
) -> None:
    """The whole path, once: dispatch, extraction, the FileObject the generator
    owns, and construction under mlcroissant."""
    sample_pdb(dataset)

    document = bake(dataset)

    assert record_sets(document) == []
    (described,) = file_objects(document)
    assert described["encodingFormat"] == "chemical/x-pdb"
    assert "1ABC" in described["description"]

    written = tmp_path / "croissant.jsonld"
    written.write_text(json.dumps(document, indent=2, default=serialize_datetime))
    mlc.Dataset(str(written))


def test_a_wrapped_archive_copy_is_described_like_the_plain_one(
    dataset: Path,
) -> None:
    """``pdb1abc.ent.gz`` is how the archive ships an entry, and it is the same
    structure as the file it unpacks to."""
    _, payload = SAMPLES["PDBHandler"]()[0]
    wrapped = write_wrapped(dataset, "pdb1abc.ent", payload, ".gz")

    meta = HANDLER.extract(make_source(wrapped, Path("pdb1abc.ent")))

    assert meta["file_name"] == "pdb1abc.ent"
    assert meta["id_code"] == "1ABC"
