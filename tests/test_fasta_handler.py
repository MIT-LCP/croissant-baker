"""FASTA: what the registry-wide sweeps cannot reach.

Unit level for claims and extraction, with one bake at the end, because the
end-to-end suite has no assembly of its own. Two things the sweeps cannot
express carry most of the weight: the claim needs the extension and the byte
together, and the read has to stop at the end of the first line.
"""

from __future__ import annotations

import json
from pathlib import Path

import mlcroissant as mlc
import pytest

from croissant_baker.entries import Reason
from croissant_baker.handlers.fasta_handler import FASTAHandler
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

HANDLER = FASTAHandler()


def write(directory: Path, name: str, payload: bytes) -> Path:
    path = directory / name
    path.write_bytes(payload)
    return path


def source_for(path: Path, relative: str | None = None):
    return make_source(path, Path(relative or path.name))


def sample_fasta(dataset: Path, name: str | None = None) -> Path:
    logical, payload = SAMPLES["FASTAHandler"]()[0]
    return write(dataset, name or logical, payload)


def extract(path: Path, relative: str | None = None, **kwargs) -> dict:
    return HANDLER.extract(source_for(path, relative), **kwargs)


@pytest.mark.parametrize("name", ["reference.fa", "reference.fasta", "reference.fna"])
def test_every_declared_spelling_is_claimed(name: str, dataset: Path) -> None:
    """One format under three names, all of them common in an assembly drop."""
    assert HANDLER.claims(source_for(sample_fasta(dataset, name)))


def test_a_file_named_fasta_that_is_not_one_is_not_claimed(dataset: Path) -> None:
    """The extension alone would claim any text a user happened to name ``.fa``."""
    path = write(dataset, "notes.fa", b"just some notes about the reference\n")

    assert not HANDLER.claims(source_for(path))


def test_the_same_bytes_under_another_extension_are_not_claimed(
    dataset: Path,
) -> None:
    """A leading ``>`` is one byte, which a quoted email and a shell log also
    open with. It is too little to own a file on by itself."""
    _, payload = SAMPLES["FASTAHandler"]()[0]
    path = write(dataset, "reference.txt", payload)

    assert not HANDLER.claims(source_for(path))


def test_the_description_names_the_file_and_not_the_record(dataset: Path) -> None:
    """A per-sample assembly names its sample on the description line, so the
    line is read to recognise the format and then discarded."""
    described = extract(sample_fasta(dataset))["description"]

    assert "reference.fa" in described
    assert "chr1" not in described
    assert "test contig" not in described


def test_the_encoding_format_is_reported(dataset: Path) -> None:
    assert extract(sample_fasta(dataset))["encoding_format"] == "text/x-fasta"


class _Counted:
    """A stream that remembers how many bytes were pulled through it."""

    def __init__(self, stream) -> None:
        self._stream = stream
        self.read_bytes = 0

    def read(self, size=-1):
        data = self._stream.read(size)
        self.read_bytes += len(data)
        return data

    def readable(self) -> bool:
        return True

    def close(self) -> None:
        self._stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def counting_source(path: Path, opened: list):
    """A source over ``path`` whose every stream is counted in ``opened``."""

    def open_binary():
        stream = _Counted(path.open("rb"))
        opened.append(stream)
        return stream

    return FileSource(
        name=path.name,
        relative_path=Path(path.name),
        size=path.stat().st_size,
        exists=True,
        _open_binary=open_binary,
        _digest=lambda: "0" * 64,
    )


#: Comfortably above anything the handler should need to read one description
#: line, and far below the sequence the fixture puts behind it.
BOUNDED_PREFIX = 64 * 1024


def test_the_sequence_behind_the_first_line_is_never_read(dataset: Path) -> None:
    """The whole point of the handler. A reference genome is gigabytes of bases
    and none of them is metadata, so the read stops where the first line does.
    """
    path = write(
        dataset,
        "genome.fa",
        b">chr1 test contig\n" + (b"ACGT" * 16 + b"\n") * 65536,
    )
    opened: list = []

    HANDLER.extract(counting_source(path, opened))

    assert sum(stream.read_bytes for stream in opened) < BOUNDED_PREFIX


def test_an_empty_file_is_refused_with_a_reason(dataset: Path) -> None:
    path = write(dataset, "empty.fa", b"")

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "empty.fa" in str(caught.value)


def test_a_description_line_naming_no_record_is_refused(dataset: Path) -> None:
    """``>`` on its own declares a record with no name, which is not a record.

    Nothing downstream reads the name, so this is not a check on content: a
    file whose first line is a bare marker is one whose first line the format
    does not describe, and reporting it beats describing it.
    """
    path = write(dataset, "bare.fa", b">\nACGTACGT\n")

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "bare.fa" in str(caught.value)


def test_a_wrapper_ending_mid_stream_is_refused_naming_the_file(
    dataset: Path,
) -> None:
    """A member intact for its first bytes opens, and then ends where the
    download stopped. What that raises is not an ``OSError``, and a file is
    owed a reason naming it either way."""
    path = write(dataset, "cut.fa.gz", cut_gzip(b">chr1 test contig\n" + b"ACGT" * 200))

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "cut.fa" in str(caught.value)
    assert "FASTA" in str(caught.value)


def test_a_refusal_reaches_the_scan_report_through_a_bake(dataset: Path) -> None:
    """A file this handler claims and cannot read is reported by name, with the
    reason it was refused for, and the assembly beside it is still described:
    the loss is per-file, never the run."""
    write(dataset, "bare.fa", b">\nACGTACGT\n")
    sample_fasta(dataset)

    document, report = bake_with_report(dataset)

    assert [o["name"] for o in file_objects(document)] == ["reference.fa"]
    (refused,) = report.undescribed
    assert refused.name == "bare.fa"
    assert refused.reason is Reason.EXTRACT_FAILED
    assert "bare.fa" in refused.detail


def test_no_sequence_becomes_a_record_set(dataset: Path) -> None:
    """Bases are not records of a dataset schema: a FASTA is described as a
    file, and the description is all of it."""
    meta = extract(sample_fasta(dataset))
    meta["relative_path"] = "reference.fa"

    result = HANDLER.build_croissant([meta], ["file_0"])

    assert result.record_sets == []
    assert result.file_sets == []


def test_a_bake_carries_the_description_onto_the_file_object(
    dataset: Path, tmp_path: Path
) -> None:
    """The whole path, once: dispatch, extraction, the FileObject the generator
    owns, and construction under mlcroissant."""
    sample_fasta(dataset)

    document = bake(dataset)

    assert record_sets(document) == []
    (described,) = file_objects(document)
    assert described["encodingFormat"] == "text/x-fasta"
    assert "reference.fa" in described["description"]

    written = tmp_path / "croissant.jsonld"
    written.write_text(json.dumps(document, indent=2, default=serialize_datetime))
    mlc.Dataset(str(written))


def test_a_wrapped_fasta_is_described_like_the_plain_one(dataset: Path) -> None:
    name, payload = SAMPLES["FASTAHandler"]()[0]
    wrapped = write_wrapped(dataset, name, payload, ".gz")

    meta = HANDLER.extract(make_source(wrapped, Path(name)))

    assert meta["file_name"] == name
    assert meta["encoding_format"] == "text/x-fasta"
