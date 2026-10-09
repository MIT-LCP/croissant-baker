"""BCF: the binary container, and the VCF record set it has to agree with.

Unit level for the claim and for the bytes around the header, then one
comparison against the VCF fixture, because the whole point of subclassing is
that the same header describes the same callset either way.
"""

from __future__ import annotations

import gzip
import json
import struct
from pathlib import Path

import mlcroissant as mlc
import pytest

from croissant_baker.entries import Reason
from croissant_baker.handlers.bcf_handler import BCFHandler
from croissant_baker.handlers.vcf_handler import VCFHandler
from croissant_baker.identifiers import serialize_datetime
from croissant_baker.sources import FileSource, make_source

from tests.helpers import (
    SAMPLES,
    VCF_HEADER_TEXT,
    bake,
    bake_with_report,
    bcf_payload,
    cli,
    file_objects,
    record_sets,
    write_wrapped,
)

HANDLER = BCFHandler()


def write(directory: Path, name: str, payload: bytes) -> Path:
    path = directory / name
    path.write_bytes(payload)
    return path


def source_for(path: Path, relative: str | None = None):
    return make_source(path, Path(relative or path.name))


def sample_bcf(dataset: Path) -> Path:
    name, payload = SAMPLES["BCFHandler"]()[0]
    return write(dataset, name, payload)


def extract(path: Path, relative: str | None = None, **kwargs) -> dict:
    return HANDLER.extract(source_for(path, relative), **kwargs)


def test_a_bcf_is_claimed_on_the_magic_inside_its_wrapper(dataset: Path) -> None:
    """A BCF on disk is a compressed container whose payload starts with the
    magic, and nothing outside it says so."""
    assert HANDLER.claims(source_for(sample_bcf(dataset)))


def test_a_decompressed_payload_is_claimed_too(dataset: Path) -> None:
    """The compression layer does not strip ``.bcf``, so a plain ``calls.bcf``
    arrives compressed. A ``calls.bcf.gz`` arrives with one layer already taken
    off, and it is the same file."""
    path = write(dataset, "unwrapped.bcf", bcf_payload())

    assert HANDLER.claims(source_for(path))


def test_a_compressed_file_that_is_not_a_bcf_is_not_claimed(dataset: Path) -> None:
    path = write(dataset, "notes.bcf", gzip.compress(b"plain text, gzipped\n"))

    assert not HANDLER.claims(source_for(path))


def test_bytes_that_are_neither_are_not_claimed(dataset: Path) -> None:
    path = write(dataset, "garbage.bcf", b"\x00\xff not a real file \xfe\x00")

    assert not HANDLER.claims(source_for(path))


def test_the_earlier_minor_version_is_read_the_same_way(dataset: Path) -> None:
    """BCF 2.1 and 2.2 differ in how records are encoded, not in the header
    this handler reads, so both are described."""
    path = write(dataset, "old.bcf", gzip.compress(bcf_payload(minor=1)))

    assert HANDLER.claims(source_for(path))
    assert extract(path)["fileformat"] == "VCFv4.2"


def legacy_bcf(dataset: Path) -> Path:
    """A BCF1: samtools' own first-generation encoding, header text and all."""
    return write(dataset, "legacy.bcf", gzip.compress(b"BCF\x01\x00" + b"\x00" * 16))


def test_the_obsolete_first_generation_is_claimed_so_that_it_can_be_refused(
    dataset: Path,
) -> None:
    """The claim is on ``BCF``, not on ``BCF\\2``.

    BCF1 carries no VCF text header, so there is nothing in it to read, and a
    reader that claimed only the generation it understands would leave the file
    to be reported as one nothing recognised. Claiming it is what lets it be
    reported as the format it is.
    """
    path = legacy_bcf(dataset)

    assert HANDLER.claims(source_for(path))

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "legacy.bcf" in str(caught.value)
    assert "BCF 2" in str(caught.value)


def test_the_obsolete_first_generation_is_reported_by_a_bake(dataset: Path) -> None:
    """Through the registry rather than the handler, because which handler is
    offered the file is half of what makes the refusal reachable. The readable
    callset beside it is there because the loss is per-file, never the run."""
    legacy_bcf(dataset)
    sample_bcf(dataset)

    document, report = bake_with_report(dataset)

    assert [o["name"] for o in file_objects(document)] == ["calls.bcf"]
    (refused,) = report.undescribed
    assert refused.name == "legacy.bcf"
    assert refused.reason is Reason.EXTRACT_FAILED
    assert "BCF 2" in refused.detail


def test_a_truncated_header_is_refused_with_a_reason(dataset: Path) -> None:
    path = write(
        dataset,
        "truncated.bcf",
        gzip.compress(b"BCF\x02\x02" + struct.pack("<I", 4096) + b"##file"),
    )

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "truncated.bcf" in str(caught.value)


def test_a_nul_padded_header_reads_as_the_text_it_holds(dataset: Path) -> None:
    """``l_text`` counts a terminating NUL, and writers pad with more of them.

    Padding is not header text: split on without stripping it, the last line
    the header declares carries a NUL and the column check refuses a file that
    is perfectly readable.
    """
    padded = (
        b"BCF\x02\x02"
        + struct.pack("<I", len(VCF_HEADER_TEXT) + 8)
        + VCF_HEADER_TEXT
        + b"\x00" * 8
    )
    path = write(dataset, "padded.bcf", gzip.compress(padded))

    meta = extract(path)

    assert meta["fileformat"] == "VCFv4.2"
    assert meta["sample_count"] == 2


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


#: Comfortably above anything the handler should need to refuse a header, and
#: far below the body the fixture puts behind the declared length.
BOUNDED_PREFIX = 64 * 1024


def test_a_declared_header_larger_than_the_cap_is_refused_unread(
    dataset: Path,
) -> None:
    """``l_text`` is a 32-bit length a corrupt or hostile file chooses.

    Trusting it turns a header read into a read of the whole file, which is the
    one thing this handler exists not to do, so the length is refused before a
    byte of it is pulled.
    """
    body = b"\x00" * (4 * 1024 * 1024)
    path = write(
        dataset,
        "huge.bcf",
        b"BCF\x02\x02" + struct.pack("<I", 2**32 - 1) + body,
    )
    opened: list = []

    with pytest.raises(ValueError) as caught:
        HANDLER.extract(counting_source(path, opened))

    assert "huge.bcf" in str(caught.value)
    assert sum(stream.read_bytes for stream in opened) < BOUNDED_PREFIX


def build(handler, path: Path, **kwargs) -> list:
    """Every record set ``handler`` builds for ``path``, as a batch of one."""
    meta = handler.extract(source_for(path), **kwargs)
    meta["relative_path"] = path.name
    meta["stored_name"] = path.name
    return handler.build_croissant([meta], ["file_0"]).record_sets


def shape(field) -> tuple:
    """A field reduced to what the two containers must agree on."""
    return (
        field.name,
        [str(t) for t in field.data_types or []],
        field.is_array,
        tuple(shape(sub) for sub in field.sub_fields or []),
    )


def sample_vcf(dataset: Path) -> Path:
    name, payload = SAMPLES["VCFHandler"]()[0]
    return write(dataset, name, payload)


def test_the_record_set_is_the_one_the_same_header_builds_as_a_vcf(
    dataset: Path,
) -> None:
    """The same header, in the other container: the fields, their types and
    their cardinalities are the callset's, not the container's."""
    (from_bcf,) = build(HANDLER, sample_bcf(dataset))
    (from_vcf,) = build(VCFHandler(), sample_vcf(dataset))

    assert [shape(f) for f in from_bcf.fields] == [shape(f) for f in from_vcf.fields]


def test_the_container_is_named_in_the_description(dataset: Path) -> None:
    (record_set,) = build(HANDLER, sample_bcf(dataset))

    assert "calls.bcf" in record_set.description
    assert "VCFv4.2" in record_set.description
    assert "2 contigs" in record_set.description


def fields_of(record_set) -> dict:
    return {field.name: field for field in record_set.fields}


def test_the_sample_names_are_withheld_by_default(dataset: Path) -> None:
    meta = extract(sample_bcf(dataset))
    fields = fields_of(build(HANDLER, sample_bcf(dataset))[0])

    assert meta["sample_count"] == 2
    assert "sample_ids" not in meta
    assert "NA00001" not in fields["samples"].description


def test_the_sample_names_are_emitted_only_when_asked_for(dataset: Path) -> None:
    path = sample_bcf(dataset)
    fields = fields_of(build(HANDLER, path, genomic_sample_ids=True)[0])

    assert extract(path, genomic_sample_ids=True)["sample_ids"] == [
        "NA00001",
        "NA00002",
    ]
    assert "NA00001" in fields["samples"].description


def test_a_bake_over_a_binary_callset_validates(dataset: Path, tmp_path: Path) -> None:
    """The whole path, once: dispatch, extraction, assembly and construction
    under mlcroissant."""
    sample_bcf(dataset)

    document = bake(dataset)

    (record_set,) = record_sets(document)
    assert [f["name"] for f in record_set["field"]][:2] == ["CHROM", "POS"]
    assert [o["encodingFormat"] for o in file_objects(document)] == [
        "application/x-bcf"
    ]

    written = tmp_path / "croissant.jsonld"
    written.write_text(json.dumps(document, indent=2, default=serialize_datetime))
    mlc.Dataset(str(written))


def test_a_wrapped_callset_is_described_like_the_plain_one(dataset: Path) -> None:
    """A second wrapper is taken off on the way in, leaving the container this
    handler decompresses itself."""
    name, payload = SAMPLES["BCFHandler"]()[0]
    wrapped = write_wrapped(dataset, name, payload, ".gz")

    meta = HANDLER.extract(make_source(wrapped, Path(name)))

    assert meta["file_name"] == name
    assert meta["fileformat"] == "VCFv4.2"
    assert meta["sample_count"] == 2


def samples_description(document: dict) -> str:
    (record_set,) = record_sets(document)
    return next(f for f in record_set["field"] if f["name"] == "samples")["description"]


def test_the_flag_reaches_a_bake_from_the_command_line(
    dataset: Path, tmp_path: Path
) -> None:
    sample_bcf(dataset)
    output = tmp_path / "croissant.jsonld"

    result = cli(dataset, output, "--genomic-sample-ids")

    assert result.exit_code == 0, result.output
    assert "NA00001" in samples_description(json.loads(output.read_text()))
