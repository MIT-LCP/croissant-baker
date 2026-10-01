"""SMILES: what the registry-wide sweeps cannot reach.

Unit level for the claim, the layout the sample is read off, and the bound the
read stops at, with one bake at the end, because the end-to-end suite has no
chemistry dataset of its own.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import mlcroissant as mlc
import pytest

from croissant_baker.entries import Reason
from croissant_baker.handlers.smiles_handler import (
    MAX_FIELDS,
    SAMPLE_LINES,
    SMILESHandler,
)
from croissant_baker.identifiers import serialize_datetime
from croissant_baker.sources import FileSource, make_source

from tests.helpers import (
    SAMPLES,
    bake,
    bake_with_report,
    by_name,
    cut_gzip,
    file_objects,
    record_sets,
    write_wrapped,
)

HANDLER = SMILESHandler()


def write(directory: Path, name: str, payload: bytes) -> Path:
    path = directory / name
    path.write_bytes(payload)
    return path


def source_for(path: Path, relative: str | None = None):
    return make_source(path, Path(relative or path.name))


def sample_bytes() -> bytes:
    return SAMPLES["SMILESHandler"]()[0][1]


def sample_smiles(dataset: Path) -> Path:
    name, payload = SAMPLES["SMILESHandler"]()[0]
    return write(dataset, name, payload)


def extract(path: Path, relative: str | None = None, **kwargs) -> dict:
    return HANDLER.extract(source_for(path, relative), **kwargs)


# The claim


def test_a_smiles_file_is_claimed_on_its_extension_and_its_first_line(
    dataset: Path,
) -> None:
    assert HANDLER.claims(source_for(sample_smiles(dataset)))


def test_the_long_extension_is_claimed_too(dataset: Path) -> None:
    """``.smiles`` is the same format under the name half the tools write."""
    path = write(dataset, "molecules.smiles", sample_bytes())

    assert HANDLER.claims(source_for(path))


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("prose.smi", b"Notes, written by hand, about this deposit\n"),
        ("quoted.smi", b'"CCO","ethanol"\n'),
        ("named.smi", b"ethanol\tCCO\n"),
        ("indented.smi", b"\tCCO\tethanol\n"),
    ],
    ids=["prose", "quoted", "name-first", "empty-first-field"],
)
def test_a_first_field_that_is_not_a_structure_is_not_claimed(
    name: str, payload: bytes, dataset: Path
) -> None:
    """The extension alone would claim any text a user named ``.smi``.

    A comma, a quote and a leading tab are all outside what a SMILES is written
    with, and ``ethanol`` is inside the alphabet but spells no atom, which is
    why the claim reads the letters as symbols rather than as characters.
    """
    path = write(dataset, name, payload)

    assert not HANDLER.claims(source_for(path))


def test_the_same_bytes_under_another_extension_are_not_claimed(
    dataset: Path,
) -> None:
    """Structure is not enough on its own: a SMILES file is named like one."""
    path = write(dataset, "molecules.txt", sample_bytes())

    assert not HANDLER.claims(source_for(path))


def test_comment_and_blank_lines_are_skipped_before_the_claim(
    dataset: Path,
) -> None:
    """``#`` opens a comment in the dialects that have one, and it cannot open a
    SMILES: a structure never begins with a bond."""
    path = write(dataset, "commented.smi", b"# a deposit\n\nCCO\tethanol\n")

    assert HANDLER.claims(source_for(path))


def test_a_header_line_before_the_data_is_claimed(dataset: Path) -> None:
    """RDKit's ``SmilesWriter`` opens a file with ``SMILES Name``, and the
    ChEMBL, ZINC and Enamine drops do the same. ``SMILES`` spells no structure,
    so what says the file is a library is the line under it, which is where the
    molecules start."""
    path = write(dataset, "headed.smi", b"SMILES\tName\nCCO\tethanol\n")

    assert HANDLER.claims(source_for(path))


def test_two_lines_that_are_neither_header_nor_structure_are_not_claimed(
    dataset: Path,
) -> None:
    """A header is a line the molecules follow. Prose is followed by more
    prose, and neither of the two lines spells a structure."""
    path = write(dataset, "prose.smi", b"Notes about this deposit\nand the next\n")

    assert not HANDLER.claims(source_for(path))


def test_a_headed_file_is_described_by_a_bake(dataset: Path) -> None:
    """The whole way through: a file the claim used to leave to no handler at
    all is now dispatched, read and written out as a described file object."""
    write(dataset, "headed.smi", b"SMILES\tName\nCCO\tethanol\n")

    document, report = bake_with_report(dataset)

    assert [o["name"] for o in file_objects(document)] == ["headed.smi"]
    assert report.undescribed == []


def test_an_unreadable_file_is_not_claimed(dataset: Path) -> None:
    """``claims`` never raises: an empty peek is simply not a claim."""
    assert not HANDLER.claims(source_for(dataset / "gone.smi"))


# The layout


def test_the_encoding_format_is_the_chemical_media_type(dataset: Path) -> None:
    meta = extract(sample_smiles(dataset))

    assert meta["encoding_format"] == "chemical/x-daylight-smiles"


def test_a_tab_separated_file_reports_its_delimiter_and_its_columns(
    dataset: Path,
) -> None:
    meta = extract(sample_smiles(dataset))

    assert meta["delimiter"] == "tab"
    assert meta["columns"] == ["smiles", "name"]


def test_a_space_separated_file_reports_whitespace(dataset: Path) -> None:
    """Runs of spaces are the other spelling, and the only other one."""
    path = write(dataset, "spaced.smi", b"CCO   ethanol\nC methane\n")

    meta = extract(path)

    assert meta["delimiter"] == "whitespace"
    assert meta["columns"] == ["smiles", "name"]


def test_a_header_line_is_detected_and_its_names_are_used(dataset: Path) -> None:
    """A header is the one thing in the file that names its columns, so when
    one is there the names come from it rather than from their position."""
    path = write(dataset, "headed.smi", b"SMILES\tName\nCCO\tethanol\n")

    meta = extract(path)

    assert meta["has_header"] is True
    assert meta["columns"] == ["SMILES", "Name"]


def test_a_file_with_no_header_says_so(dataset: Path) -> None:
    meta = extract(sample_smiles(dataset))

    assert meta["has_header"] is False


def test_a_single_column_file_names_only_the_structure(dataset: Path) -> None:
    path = write(dataset, "structures.smi", b"CCO\nC\nc1ccccc1\n")

    meta = extract(path)

    assert meta["columns"] == ["smiles"]


def test_a_third_column_is_named_by_its_position(dataset: Path) -> None:
    """The file names nothing past the structure, so the position is all there
    is to name a column by."""
    path = write(dataset, "extra.smi", b"CCO\tethanol\t64-17-5\nC\tmethane\t74-82-8\n")

    meta = extract(path)

    assert meta["columns"] == ["smiles", "name", "column_3"]


def test_the_column_count_is_the_widest_line_of_the_sample(dataset: Path) -> None:
    """A line carrying fewer fields has simply left the trailing ones off."""
    path = write(dataset, "ragged.smi", b"CCO\tethanol\nC\n")

    meta = extract(path)

    assert meta["columns"] == ["smiles", "name"]


#: A line carrying far more fields than any molecule table has columns, which
#: is what a file of another format named ``.smi`` looks like from here.
WIDE_LINE = b"CCO" + b"\tx" * (MAX_FIELDS + 50) + b"\n"
WIDE_COLUMNS = MAX_FIELDS + 51


def test_a_line_wider_than_the_cap_states_the_cap(dataset: Path) -> None:
    """One record set field per column, so a line of three hundred thousand
    fields is three hundred thousand nodes in the output and the minutes and
    gigabytes it takes to build them."""
    meta = extract(write(dataset, "wide.smi", WIDE_LINE))

    assert len(meta["columns"]) == MAX_FIELDS
    assert meta["column_count"] == WIDE_COLUMNS


def test_the_description_says_which_columns_it_left_out(dataset: Path) -> None:
    """Capped in silence is the one thing it must not be."""
    (record_set,) = build(write(dataset, "wide.smi", WIDE_LINE))

    assert f"the first {MAX_FIELDS} of {WIDE_COLUMNS} columns" in record_set.description
    assert len(record_set.fields) == MAX_FIELDS


# The bound


def test_a_short_file_reports_every_line_it_holds(dataset: Path) -> None:
    meta = extract(sample_smiles(dataset))

    assert meta["sampled_lines"] == 3
    assert meta["sample_exhausted"] is True


def test_a_long_file_stops_at_the_line_bound(dataset: Path) -> None:
    path = write(dataset, "library.smi", b"CCO\tethanol\n" * (SAMPLE_LINES + 500))

    meta = extract(path)

    assert meta["sampled_lines"] == SAMPLE_LINES
    assert meta["sample_exhausted"] is False


class _Counted(io.RawIOBase):
    """A raw stream that remembers how many bytes were pulled through it."""

    def __init__(self, stream) -> None:
        self._stream = stream
        self.read_bytes = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        data = self._stream.read(len(buffer))
        self.read_bytes += len(data)
        buffer[: len(data)] = data
        return len(data)

    def close(self) -> None:
        self._stream.close()
        super().close()


def counting_source(path: Path, opened: list):
    """A source over ``path`` whose every stream is counted in ``opened``."""

    def open_binary():
        counted = _Counted(path.open("rb"))
        opened.append(counted)
        return io.BufferedReader(counted)

    return FileSource(
        name=path.name,
        relative_path=Path(path.name),
        size=path.stat().st_size,
        exists=True,
        _open_binary=open_binary,
        _digest=lambda: "0" * 64,
    )


#: Comfortably above the sampled lines, and far below the library behind them.
BOUNDED_PREFIX = 64 * 1024


def test_the_read_stops_inside_the_sample(dataset: Path) -> None:
    """A library of a million molecules costs the same read as one of a
    thousand, which is the whole reason the layout is read off a sample."""
    path = write(dataset, "library.smi", b"CCO\tethanol\n" * 200_000)
    opened: list = []

    meta = HANDLER.extract(counting_source(path, opened))

    assert meta["sampled_lines"] == SAMPLE_LINES
    assert path.stat().st_size > 2 * 1024 * 1024
    assert sum(stream.read_bytes for stream in opened) < BOUNDED_PREFIX


# The refusals


def test_an_empty_file_is_refused_with_a_reason(dataset: Path) -> None:
    path = write(dataset, "empty.smi", b"")

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "empty.smi" in str(caught.value)


def test_a_file_of_comments_alone_is_refused_with_a_reason(dataset: Path) -> None:
    """Claimed on its extension, because the comments may run past the peek,
    and then reported for what the read actually found."""
    path = write(dataset, "notes.smi", b"# nothing but a note\n#\n")

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "notes.smi" in str(caught.value)


def test_a_first_line_that_is_not_a_molecule_is_refused_naming_the_file(
    dataset: Path,
) -> None:
    """Two lines of names is a file whose first column is not a structure, and
    describing it as one would name a column the file never wrote."""
    path = write(dataset, "names.smi", b"ethanol\tCCO\nmethane\tC\n")

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "names.smi" in str(caught.value)


#: A line long enough that the truncated sample below holds far fewer than
#: ``SAMPLE_LINES`` of them, so the read reaches the cut rather than the bound.
LONG_LINE = b"C" * 3000 + b"\tpolymer\n"
LONG_LINES = 400


def test_a_wrapper_ending_mid_stream_is_refused_naming_the_file(
    dataset: Path,
) -> None:
    """A member intact for its first bytes opens, and then ends where the
    download stopped. What that raises is not an ``OSError``, and a file is
    owed a reason naming it either way."""
    path = write(dataset, "cut.smi.gz", cut_gzip(LONG_LINE * LONG_LINES))

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "cut.smi" in str(caught.value)
    assert "SMILES" in str(caught.value)


def test_a_refusal_reaches_the_scan_report_through_a_bake(dataset: Path) -> None:
    """A file this handler claims and cannot read is reported by name, with the
    reason it was refused for, and the library beside it is still described:
    the loss is per-file, never the run."""
    write(dataset, "notes.smi", b"# nothing but a note\n#\n")
    sample_smiles(dataset)

    document, report = bake_with_report(dataset)

    assert [o["name"] for o in file_objects(document)] == ["molecules.smi"]
    (refused,) = report.undescribed
    assert refused.name == "notes.smi"
    assert refused.reason is Reason.EXTRACT_FAILED
    assert "notes.smi" in refused.detail


# The record set


def build(*paths: Path, root: Path | None = None, **kwargs) -> list:
    """Every record set the handler builds for ``paths``, as one batch."""
    metas = []
    for path in paths:
        relative = str(path.relative_to(root)) if root else path.name
        meta = extract(path, relative, **kwargs)
        meta["relative_path"] = relative
        meta["stored_name"] = path.name
        metas.append(meta)
    ids = [f"file_{i}" for i in range(len(metas))]
    return HANDLER.build_croissant(metas, ids).record_sets


def test_one_record_set_per_file(dataset: Path) -> None:
    first = sample_smiles(dataset)
    second = write(dataset, "other.smi", first.read_bytes())

    built = build(first, second)

    assert len(built) == 2
    assert len({rs.id for rs in built}) == 2


def test_every_column_becomes_a_text_field_sourced_from_the_file(
    dataset: Path,
) -> None:
    (record_set,) = build(sample_smiles(dataset))

    assert [field.name for field in record_set.fields] == ["smiles", "name"]
    for field in record_set.fields:
        assert str(field.data_types[0]) == "sc:Text"
        assert field.source.file_object == "file_0"


def test_the_description_states_the_layout_and_the_sample(dataset: Path) -> None:
    (record_set,) = build(sample_smiles(dataset))

    assert "molecules.smi" in record_set.description
    assert "tab-separated" in record_set.description
    assert "2 columns" in record_set.description
    assert "all 3 lines" in record_set.description


def test_the_description_says_when_a_header_line_was_found(dataset: Path) -> None:
    path = write(dataset, "headed.smi", b"SMILES\tName\nCCO\tethanol\n")

    (record_set,) = build(path)

    assert "header line" in record_set.description


# The whole path


def test_a_bake_over_a_smiles_file_validates(dataset: Path, tmp_path: Path) -> None:
    """The whole path, once: dispatch, extraction, assembly and construction
    under mlcroissant."""
    sample_smiles(dataset)

    document = bake(dataset)

    (record_set,) = record_sets(document)
    assert record_set["@id"].endswith("_molecules")
    fields = by_name(record_set["field"])
    assert list(fields) == ["smiles", "name"]
    assert [o["encodingFormat"] for o in file_objects(document)] == [
        "chemical/x-daylight-smiles"
    ]

    written = tmp_path / "croissant.jsonld"
    written.write_text(json.dumps(document, indent=2, default=serialize_datetime))
    mlc.Dataset(str(written))


def test_no_field_claims_an_extract_mlcroissant_cannot_perform(
    dataset: Path,
) -> None:
    """Croissant's extract grammar addresses columns of a format mlcroissant
    knows how to open, and SMILES is not on that list, so a column reference
    here would be a promise nobody can keep."""
    sample_smiles(dataset)

    (record_set,) = record_sets(bake(dataset))

    for field in record_set["field"]:
        assert "extract" not in field["source"]
        assert "fileObject" in field["source"]


def test_a_wrapped_file_is_described_like_the_plain_one(dataset: Path) -> None:
    name, payload = SAMPLES["SMILESHandler"]()[0]
    wrapped = write_wrapped(dataset, name, payload, ".gz")

    meta = HANDLER.extract(make_source(wrapped, Path(name)))

    assert meta["file_name"] == name
    assert meta["columns"] == ["smiles", "name"]
    assert meta["sample_exhausted"] is True
