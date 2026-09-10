"""FASTQ: what the registry-wide sweeps cannot reach.

Unit level for the claim and the bounded read, with one bake at the end,
because the end-to-end suite has no sequencing dataset of its own.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import mlcroissant as mlc
import pytest

from croissant_baker.handlers.fastq_handler import FASTQHandler
from croissant_baker.identifiers import serialize_datetime
from croissant_baker.sources import FileSource, make_source

from tests.helpers import (
    BAM_HEADER_TEXT,
    SAMPLES,
    bake,
    file_objects,
    record_sets,
    write_wrapped,
)

HANDLER = FASTQHandler()

#: The read name of the first record of the sample, and the instrument token
#: inside it. Neither may reach the metadata.
READ_NAME = "A00123:45:HVXXXDSXX:1:1101:1000:1000"
INSTRUMENT = "A00123"


def write(directory: Path, name: str, payload: bytes) -> Path:
    path = directory / name
    path.write_bytes(payload)
    return path


def source_for(path: Path, relative: str | None = None):
    return make_source(path, Path(relative or path.name))


def sample_bytes() -> bytes:
    return SAMPLES["FASTQHandler"]()[0][1]


def sample_fastq(dataset: Path) -> Path:
    name, payload = SAMPLES["FASTQHandler"]()[0]
    return write(dataset, name, payload)


def extract(path: Path, relative: str | None = None, **kwargs) -> dict:
    return HANDLER.extract(source_for(path, relative), **kwargs)


def test_a_fastq_is_claimed_on_its_extension_and_its_structure(dataset: Path) -> None:
    assert HANDLER.claims(source_for(sample_fastq(dataset)))


def test_the_short_extension_is_claimed_too(dataset: Path) -> None:
    """``.fq`` is the same format under the name half the tools write."""
    path = write(dataset, "reads.fq", sample_bytes())

    assert HANDLER.claims(source_for(path))


def test_a_file_whose_third_line_is_not_a_separator_is_not_claimed(
    dataset: Path,
) -> None:
    """The extension alone would claim any text a user named ``.fastq``."""
    path = write(dataset, "notes.fastq", b"@subject\nnotes about it\nand more\n")

    assert not HANDLER.claims(source_for(path))


def test_a_sam_header_under_a_fastq_extension_is_not_claimed(dataset: Path) -> None:
    """``@`` alone is also how a SAM header opens, which is why the third line
    is part of the claim."""
    path = write(dataset, "header.fq", BAM_HEADER_TEXT.encode())

    assert not HANDLER.claims(source_for(path))


def test_the_same_bytes_under_another_extension_are_not_claimed(
    dataset: Path,
) -> None:
    """Structure is not enough on its own: a FASTQ is named like one."""
    path = write(dataset, "reads.txt", sample_bytes())

    assert not HANDLER.claims(source_for(path))


def test_an_unreadable_file_is_not_claimed(dataset: Path) -> None:
    """``claims`` never raises: an empty peek is simply not a claim."""
    assert not HANDLER.claims(source_for(dataset / "gone.fastq"))


def test_the_length_of_the_first_read_is_reported(dataset: Path) -> None:
    meta = extract(sample_fastq(dataset))

    assert meta["first_read_length"] == 8


def test_the_encoding_format_is_the_fastq_media_type(dataset: Path) -> None:
    meta = extract(sample_fastq(dataset))

    assert meta["encoding_format"] == "text/x-fastq"


def test_the_description_states_the_read_length(dataset: Path) -> None:
    described = extract(sample_fastq(dataset))["description"]

    assert "8 bases" in described
    assert "reads.fastq" in described


def test_the_description_names_neither_the_read_nor_its_instrument(
    dataset: Path,
) -> None:
    """An Illumina read name carries instrument, run and flowcell identifiers.

    None of that is structure, and a description is the one place it could
    reach the output, so it is asserted absent rather than assumed absent.
    """
    meta = extract(sample_fastq(dataset))

    assert READ_NAME not in meta["description"]
    assert INSTRUMENT not in meta["description"]
    assert INSTRUMENT not in json.dumps(meta)


def test_a_single_record_file_is_described(dataset: Path) -> None:
    """A complete record is a complete record; there is no second one to want."""
    path = write(dataset, "one.fastq", b"@r1\nACGT\n+\nIIII\n")

    assert extract(path)["first_read_length"] == 4


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


#: Comfortably above the first record, and far below the body behind it.
BOUNDED_PREFIX = 64 * 1024


def many_records(count: int) -> bytes:
    """``count`` well-formed records, each on the same four lines."""
    record = b"@r\nACGTACGT\n+\nIIIIIIII\n"
    return record * count


def test_the_read_stops_after_the_first_record(dataset: Path) -> None:
    """The read is bounded by the record, not by the file.

    A run's worth of reads costs the same read as one of them, which is the
    whole reason this handler describes the first record rather than the file.
    """
    path = write(dataset, "run.fastq", many_records(200_000))
    opened: list = []

    meta = HANDLER.extract(counting_source(path, opened))

    assert meta["first_read_length"] == 8
    assert path.stat().st_size > 4 * 1024 * 1024
    assert sum(stream.read_bytes for stream in opened) < BOUNDED_PREFIX


def test_an_empty_file_is_refused_with_a_reason(dataset: Path) -> None:
    path = write(dataset, "empty.fastq", b"")

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "empty.fastq" in str(caught.value)


def test_a_record_missing_its_separator_is_refused(dataset: Path) -> None:
    """Multi-line FASTQ is deliberately unsupported: a record whose third line
    is not ``+`` is one this handler cannot read in four lines."""
    path = write(dataset, "wrapped.fastq", b"@r1\nACGT\nACGT\n+\nIIIIIIII\n")

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "wrapped.fastq" in str(caught.value)


def test_a_quality_line_of_a_different_length_is_refused(dataset: Path) -> None:
    """One quality character per base, or the record is not one to describe."""
    path = write(dataset, "ragged.fastq", b"@r1\nACGTACGT\n+\nIIII\n")

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "ragged.fastq" in str(caught.value)


def test_a_record_with_no_quality_line_is_refused(dataset: Path) -> None:
    """Three lines are a truncated record, not a shorter one: without the
    quality line there is no read to state a length for."""
    path = write(dataset, "short.fastq", b"@r1\nACGTACGT\n+\n")

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "short.fastq" in str(caught.value)


def test_no_read_becomes_a_record_set(dataset: Path) -> None:
    """Reads are records of a run, not of a dataset schema: a FASTQ is
    described as a file, and the description is all of it."""
    meta = extract(sample_fastq(dataset))
    meta["relative_path"] = "reads.fastq"

    result = HANDLER.build_croissant([meta], ["file_0"])

    assert result.record_sets == []
    assert result.file_sets == []


def test_a_bake_carries_the_description_onto_the_file_object(
    dataset: Path, tmp_path: Path
) -> None:
    """The whole path, once: dispatch, extraction, the FileObject the generator
    owns, and construction under mlcroissant."""
    sample_fastq(dataset)

    document = bake(dataset)

    assert record_sets(document) == []
    (described,) = file_objects(document)
    assert described["encodingFormat"] == "text/x-fastq"
    assert "8 bases" in described["description"]

    written = tmp_path / "croissant.jsonld"
    written.write_text(json.dumps(document, indent=2, default=serialize_datetime))
    mlc.Dataset(str(written))


def test_a_wrapped_fastq_is_described_like_the_plain_one(dataset: Path) -> None:
    name, payload = SAMPLES["FASTQHandler"]()[0]
    wrapped = write_wrapped(dataset, name, payload, ".gz")

    meta = HANDLER.extract(make_source(wrapped, Path(name)))

    assert meta["file_name"] == name
    assert meta["first_read_length"] == 8
