"""XYZ: what the registry-wide sweeps cannot reach.

Unit level for the claim, the first-frame read and the extended-XYZ property
line, with one bake at the end, because the end-to-end suite has no molecular
dataset of its own.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import mlcroissant as mlc
import pytest

from croissant_baker.entries import Reason
from croissant_baker.handlers.xyz_handler import HEAD_BYTES, XYZHandler
from croissant_baker.identifiers import serialize_datetime
from croissant_baker.sources import FileSource, make_source

from tests.helpers import (
    SAMPLES,
    bake,
    bake_with_report,
    cut_gzip,
    file_objects,
    record_sets,
    write_wrapped,
)

HANDLER = XYZHandler()


def write(directory: Path, name: str, payload: bytes) -> Path:
    path = directory / name
    path.write_bytes(payload)
    return path


def source_for(path: Path, relative: str | None = None):
    return make_source(path, Path(relative or path.name))


def sample_bytes() -> bytes:
    return SAMPLES["XYZHandler"]()[0][1]


def sample_xyz(dataset: Path) -> Path:
    name, payload = SAMPLES["XYZHandler"]()[0]
    return write(dataset, name, payload)


def extract(path: Path, relative: str | None = None, **kwargs) -> dict:
    return HANDLER.extract(source_for(path, relative), **kwargs)


def test_an_xyz_is_claimed_on_its_extension_and_its_first_frame(dataset: Path) -> None:
    assert HANDLER.claims(source_for(sample_xyz(dataset)))


def test_a_first_line_that_is_not_a_count_is_not_claimed(dataset: Path) -> None:
    """The first line of an XYZ is the one line that says the file is one."""
    path = write(dataset, "notes.xyz", b"coordinates\nfor the run\nO 0.0 0.0 0.1\n")

    assert not HANDLER.claims(source_for(path))


def test_a_third_line_that_is_not_an_atom_line_is_not_claimed(dataset: Path) -> None:
    """A leading integer is also how a plain numbered list opens."""
    path = write(dataset, "list.xyz", b"3\nthings to do\nbuy milk\nwrite it up\n")

    assert not HANDLER.claims(source_for(path))


#: One frame whose atom lines carry a string column after the coordinates,
#: which is what an extended-XYZ ``tags`` or ``molecule`` property looks like on
#: the line. The coordinates sit where they always sit; only the tail differs.
TRAILING_COLUMN = (
    b"2\nlabelled\nO 0.000 0.000 0.117 solvent\nH 0.000 0.757 -0.469 solvent\n"
)


def test_an_atom_line_with_a_trailing_string_column_is_claimed(dataset: Path) -> None:
    """The coordinates are the columns that make a line an atom line, and they
    sit after the symbol. What a frame carries past them is the writer's own:
    an extended-XYZ file declaring ``tags:S:1`` ends every atom line in a word,
    and refusing it would refuse the dialect the format is mostly written in."""
    path = write(dataset, "labelled.xyz", TRAILING_COLUMN)

    assert HANDLER.claims(source_for(path))


def test_an_atom_line_with_a_trailing_string_column_is_described(
    dataset: Path,
) -> None:
    path = write(dataset, "labelled.xyz", TRAILING_COLUMN)

    meta = extract(path)

    assert meta["atom_count"] == 2
    assert meta["comment"] == "labelled"


def test_an_extended_frame_whose_last_property_is_a_string_is_described(
    dataset: Path,
) -> None:
    """The whole case, as an extended-XYZ writer emits it: the property list
    declares a string column last, and every atom line ends in one."""
    path = write(
        dataset,
        "tagged.xyz",
        b"2\nProperties=species:S:1:pos:R:3:tags:S:1\n"
        b"Si 0.000 0.000 0.000 bulk\n"
        b"Si 1.000 1.000 1.000 surface\n",
    )

    assert HANDLER.claims(source_for(path))
    meta = extract(path)

    assert meta["properties"] == ["species", "pos", "tags"]
    assert "extended XYZ, properties: species, pos, tags" in meta["description"]


def test_the_same_bytes_under_another_extension_are_not_claimed(dataset: Path) -> None:
    """Structure is not enough on its own: an XYZ is named like one."""
    path = write(dataset, "water.txt", sample_bytes())

    assert not HANDLER.claims(source_for(path))


def test_an_unreadable_file_is_not_claimed(dataset: Path) -> None:
    """``claims`` never raises: an empty peek is simply not a claim."""
    assert not HANDLER.claims(source_for(dataset / "gone.xyz"))


def test_the_atom_count_and_the_comment_are_read(dataset: Path) -> None:
    meta = extract(sample_xyz(dataset))

    assert meta["atom_count"] == 3
    assert meta["comment"] == "water molecule"
    assert meta["extended_xyz"] is False


def test_the_encoding_format_is_the_chemical_media_type(dataset: Path) -> None:
    meta = extract(sample_xyz(dataset))

    assert meta["encoding_format"] == "chemical/x-xyz"


def test_the_description_states_the_atom_count_and_the_comment(dataset: Path) -> None:
    described = extract(sample_xyz(dataset))["description"]

    assert "water.xyz" in described
    assert "3 atoms" in described
    assert '"water molecule"' in described


def test_a_blank_comment_is_left_out_of_the_description(dataset: Path) -> None:
    """Most writers leave the line empty, and a description quoting nothing
    would read as a description of an empty title rather than of no title."""
    path = write(dataset, "bare.xyz", b"1\n\nO 0.000 0.000 0.000\n")

    meta = extract(path)

    assert meta["comment"] == ""
    assert "comment" not in meta["description"]
    assert "1 atom" in meta["description"]


def test_an_extended_xyz_is_detected_with_its_property_names(dataset: Path) -> None:
    """``Properties=`` is the extended dialect's own declaration of the columns
    its atom lines carry, so the names in it are read off the bytes."""
    path = write(
        dataset,
        "crystal.xyz",
        b'2\nLattice="4.0 0.0 0.0 0.0 4.0 0.0 0.0 0.0 4.0" '
        b"Properties=species:S:1:pos:R:3 energy=-1.5\n"
        b"Si 0.000 0.000 0.000\n"
        b"Si 1.000 1.000 1.000\n",
    )

    meta = extract(path)

    assert meta["extended_xyz"] is True
    assert meta["properties"] == ["species", "pos"]
    assert "extended XYZ, properties: species, pos" in meta["description"]


def test_a_frame_of_no_atoms_is_described(dataset: Path) -> None:
    """A count of zero is a legal frame, not a malformed one: a trajectory
    writer emits it for an empty cell, and there is no atom line to want."""
    path = write(dataset, "empty_cell.xyz", b"0\nnothing here\n")

    assert extract(path)["atom_count"] == 0


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


def many_frames(count: int) -> bytes:
    """``count`` copies of the sample frame, which is what a trajectory is."""
    return sample_bytes() * count


def test_the_read_stops_after_the_first_frame(dataset: Path) -> None:
    """The read is bounded by the frame, not by the file.

    A molecular dynamics trajectory of a million frames costs the same read as
    a single structure, which is the whole reason this handler describes the
    first frame rather than the file.
    """
    path = write(dataset, "run.xyz", many_frames(60_000))
    opened: list = []

    meta = HANDLER.extract(counting_source(path, opened))

    assert meta["atom_count"] == 3
    assert path.stat().st_size > 4 * 1024 * 1024
    assert sum(stream.read_bytes for stream in opened) < HEAD_BYTES


def test_an_empty_file_is_refused_with_a_reason(dataset: Path) -> None:
    path = write(dataset, "empty.xyz", b"")

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "empty.xyz" in str(caught.value)


def test_a_first_line_that_is_not_a_count_is_refused(dataset: Path) -> None:
    path = write(dataset, "titled.xyz", b"water\nmolecule\nO 0.0 0.0 0.1\n")

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "titled.xyz" in str(caught.value)


def test_an_unparsable_first_atom_line_is_refused_naming_the_file(
    dataset: Path,
) -> None:
    """A count that promises atoms and a line that carries none is a frame this
    handler cannot describe truthfully, and a wrong description is worse than
    none."""
    path = write(dataset, "broken.xyz", b"3\nwater molecule\nO north east up\n")

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "broken.xyz" in str(caught.value)


def test_a_wrapper_ending_mid_stream_is_refused_naming_the_file(
    dataset: Path,
) -> None:
    """A member intact for its first bytes opens, and then ends where the
    download stopped. What that raises is not an ``OSError``, and a file is
    owed a reason naming it either way."""
    path = write(dataset, "cut.xyz.gz", cut_gzip(many_frames(400)))

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "cut.xyz" in str(caught.value)
    assert "XYZ" in str(caught.value)


def test_a_header_declaring_atoms_that_do_not_follow_is_refused(
    dataset: Path,
) -> None:
    """A count is a promise about the lines under it, and a file cut off above
    them keeps none of it."""
    path = write(dataset, "truncated.xyz", b"3\nwater molecule")

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "truncated.xyz" in str(caught.value)


def test_a_refusal_reaches_the_scan_report_through_a_bake(dataset: Path) -> None:
    """A file this handler claims and cannot read is reported by name, with the
    reason it was refused for, and the structure beside it is still described:
    the loss is per-file, never the run.

    A header with nothing under it, because that is a file the claim takes: it
    holds no third line to disagree with, so the refusal has to come from the
    read.
    """
    write(dataset, "truncated.xyz", b"3\nwater molecule")
    sample_xyz(dataset)

    document, report = bake_with_report(dataset)

    assert [o["name"] for o in file_objects(document)] == ["water.xyz"]
    (refused,) = report.undescribed
    assert refused.name == "truncated.xyz"
    assert refused.reason is Reason.EXTRACT_FAILED
    assert "truncated.xyz" in refused.detail


def test_no_frame_becomes_a_record_set(dataset: Path) -> None:
    """Atoms are records of a structure, not of a dataset schema: an XYZ is
    described as a file, and the description is all of it."""
    meta = extract(sample_xyz(dataset))
    meta["relative_path"] = "water.xyz"

    result = HANDLER.build_croissant([meta], ["file_0"])

    assert result.record_sets == []
    assert result.file_sets == []


def test_a_bake_carries_the_description_onto_the_file_object(
    dataset: Path, tmp_path: Path
) -> None:
    """The whole path, once: dispatch, extraction, the FileObject the generator
    owns, and construction under mlcroissant."""
    sample_xyz(dataset)

    document = bake(dataset)

    assert record_sets(document) == []
    (described,) = file_objects(document)
    assert described["encodingFormat"] == "chemical/x-xyz"
    assert "3 atoms" in described["description"]

    written = tmp_path / "croissant.jsonld"
    written.write_text(json.dumps(document, indent=2, default=serialize_datetime))
    mlc.Dataset(str(written))


def test_a_wrapped_xyz_is_described_like_the_plain_one(dataset: Path) -> None:
    name, payload = SAMPLES["XYZHandler"]()[0]
    wrapped = write_wrapped(dataset, name, payload, ".gz")

    meta = HANDLER.extract(make_source(wrapped, Path(name)))

    assert meta["file_name"] == name
    assert meta["atom_count"] == 3
    assert meta["comment"] == "water molecule"
