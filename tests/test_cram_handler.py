"""CRAM: what the registry-wide sweeps cannot reach.

Unit level for claims and extraction, with one bake at the end, because the
end-to-end suite has no alignment dataset of its own. A CRAM states its lengths
in two variable-width integer encodings and its header behind one of four
codecs, so most of what is asserted here is that a container header is walked
correctly rather than guessed at.
"""

from __future__ import annotations

import json
import tracemalloc
import zlib
from pathlib import Path

import mlcroissant as mlc
import pytest

from croissant_baker.handlers.cram_handler import MAX_HEADER_BYTES, CRAMHandler
from croissant_baker.identifiers import serialize_datetime
from croissant_baker.sources import FileSource, make_source

from tests.helpers import (
    BAM_HEADER_TEXT,
    SAMPLES,
    bake,
    cli,
    cram_payload,
    file_objects,
    record_sets,
    write_wrapped,
)

HANDLER = CRAMHandler()


def write(directory: Path, name: str, payload: bytes) -> Path:
    path = directory / name
    path.write_bytes(payload)
    return path


def source_for(path: Path, relative: str | None = None):
    return make_source(path, Path(relative or path.name))


def sample_cram(dataset: Path) -> Path:
    name, payload = SAMPLES["CRAMHandler"]()[0]
    return write(dataset, name, payload)


def extract(path: Path, relative: str | None = None, **kwargs) -> dict:
    return HANDLER.extract(source_for(path, relative), **kwargs)


def test_a_cram_is_claimed_on_its_magic(dataset: Path) -> None:
    """A CRAM is not wrapped at file level, so the magic is simply the first
    four bytes of the stream."""
    assert HANDLER.claims(source_for(sample_cram(dataset)))


def test_bytes_that_are_not_a_cram_are_not_claimed(dataset: Path) -> None:
    path = write(dataset, "garbage.cram", b"\x00\xff not a real file \xfe\x00")

    assert not HANDLER.claims(source_for(path))


def test_a_raw_header_block_is_read(dataset: Path) -> None:
    meta = extract(sample_cram(dataset))

    assert meta["cram_version"] == "3.0"
    assert meta["sam_version"] == "1.6"


@pytest.mark.parametrize("method", [1, 2, 3], ids=["gzip", "bzip2", "lzma"])
def test_a_compressed_header_block_is_decoded(method: int, dataset: Path) -> None:
    """The header block may be written under any of the codecs CRAM defines,
    and the three with a stdlib decoder are read the same as a raw one."""
    path = write(dataset, f"coded{method}.cram", cram_payload(method=method))

    assert extract(path)["sort_order"] == "coordinate"


def test_a_version_2_container_is_read_through_its_narrower_counter(
    dataset: Path,
) -> None:
    """CRAM 2 writes the record counter as an ITF8 and stamps no CRC on either
    the container header or the block. Both change where the header block
    starts, so a reader that assumes version 3 finds nothing there."""
    path = write(dataset, "v21.cram", cram_payload(version=(2, 1)))

    meta = extract(path)

    assert meta["cram_version"] == "2.1"
    assert meta["sq_count"] == 2


@pytest.mark.parametrize("major", [1, 4])
def test_an_unreadable_major_version_is_refused_with_a_reason(
    major: int, dataset: Path
) -> None:
    """CRAM 1 is obsolete and CRAM 4 changes the integer encodings, so neither
    can be walked with the layout versions 2 and 3 share."""
    path = write(dataset, "elsewhere.cram", cram_payload(version=(major, 0)))

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "elsewhere.cram" in str(caught.value)
    assert str(major) in str(caught.value)


def test_a_header_block_under_an_undecodable_codec_is_refused(dataset: Path) -> None:
    """rANS is CRAM's own entropy coder and has no stdlib decoder. Reporting
    that is the whole of the answer; guessing at the bytes is not."""
    path = write(dataset, "rans.cram", cram_payload(method=4))

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "rans.cram" in str(caught.value)
    assert "rANS" in str(caught.value)


def test_a_first_block_that_is_not_the_file_header_is_refused(dataset: Path) -> None:
    """The SAM header is the first block of the first container by definition.
    A container opening with anything else is one this handler cannot place."""
    path = write(dataset, "misordered.cram", cram_payload(content_type=4))

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "misordered.cram" in str(caught.value)


@pytest.mark.parametrize("kept", [4, 20, 30, 44], ids=lambda n: f"{n}-bytes")
def test_a_truncated_container_is_refused_naming_the_file(
    kept: int, dataset: Path
) -> None:
    """Cut at the magic, inside the file id, inside the container header and
    inside the block: every one of them ends a read mid-field."""
    path = write(dataset, "truncated.cram", cram_payload()[:kept])

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "truncated.cram" in str(caught.value)


def test_a_wrong_crc_is_not_a_refusal(dataset: Path) -> None:
    """The block CRC is the last four bytes of a version 3 file. It is read
    past rather than checked: what is described is the header text, and a
    mismatch is a decoder's corruption report, not metadata."""
    path = write(dataset, "badcrc.cram", cram_payload()[:-4] + bytes(4))

    assert extract(path)["sort_order"] == "coordinate"


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


#: Comfortably above anything the handler should need to refuse a block, and
#: far below the body the fixture puts behind the declared size.
BOUNDED_PREFIX = 64 * 1024


@pytest.mark.parametrize(
    "declared",
    [{"compressed_size": MAX_HEADER_BYTES + 1}, {"raw_size": MAX_HEADER_BYTES + 1}],
    ids=["compressed", "raw"],
)
def test_a_declared_block_larger_than_the_cap_is_refused_unread(
    declared: dict, dataset: Path
) -> None:
    """Both sizes are ITF8, so both are numbers a corrupt or hostile file
    chooses. Trusting either turns a header read into a read of the whole
    file, which is the one thing this handler exists not to do, so they are
    refused before a byte behind them is pulled.
    """
    path = write(
        dataset,
        "huge.cram",
        cram_payload(**declared) + b"\x00" * (4 * 1024 * 1024),
    )
    opened: list = []

    with pytest.raises(ValueError) as caught:
        HANDLER.extract(counting_source(path, opened))

    assert "huge.cram" in str(caught.value)
    assert sum(stream.read_bytes for stream in opened) < BOUNDED_PREFIX


#: A block that decodes to far more than it declares, and the size it claims.
#: 32 MiB of NULs is a few hundred bytes of LZMA, so the file on disk is small
#: and the expansion is the whole of the attack.
BOMB_BYTES = 32 * 1024 * 1024
BOMB_DECLARED = 100

#: What a bounded decode may allocate. Far above the hundred bytes the block
#: declares, and far below what expanding it would take.
BOUNDED_PEAK = 16 * 1024 * 1024


def test_a_block_decoding_to_more_than_it_declares_is_refused_unexpanded(
    dataset: Path,
) -> None:
    """The declared raw size bounds the decode, not just the read behind it.

    A block may state a raw size of a hundred bytes and hold a stream of
    hundreds of megabytes, and a decoder given the whole stream expands all of
    it before anyone can compare the two. What is decoded is therefore one byte
    past what the block declares: enough to see that it holds more, and no more
    than that.
    """
    path = write(
        dataset,
        "bomb.cram",
        cram_payload("\x00" * BOMB_BYTES, method=3, raw_size=BOMB_DECLARED),
    )
    assert path.stat().st_size < BOUNDED_PEAK

    tracemalloc.start()
    try:
        with pytest.raises(ValueError) as caught:
            extract(path)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()

    assert "bomb.cram" in str(caught.value)
    assert str(BOMB_DECLARED) in str(caught.value)
    assert peak < BOUNDED_PEAK


@pytest.mark.parametrize("method", [0, 1, 2, 3], ids=["raw", "gzip", "bzip2", "lzma"])
def test_a_block_holding_other_than_its_declared_size_is_refused(
    method: int, dataset: Path
) -> None:
    """The raw size is the block's own statement of what it holds, so a block
    holding anything else is one this handler cannot describe truthfully."""
    path = write(dataset, "mismatch.cram", cram_payload(method=method, raw_size=5))

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "mismatch.cram" in str(caught.value)
    assert "5" in str(caught.value)


def test_a_header_block_written_as_a_bare_zlib_stream_is_read(dataset: Path) -> None:
    """CRAM's method 1 names the deflate family, not the gzip spelling of it.

    htslib inflates such a block with a window argument that takes either
    header, so a writer may emit a bare zlib stream and samtools reads it. A
    reader accepting only gzip would refuse a file the reference implementation
    describes.
    """
    path = write(dataset, "zlib.cram", cram_payload(method=1, compress=zlib.compress))

    assert extract(path)["sort_order"] == "coordinate"


def test_a_compressed_block_cut_short_of_its_end_is_refused(dataset: Path) -> None:
    """The compressed size says where the stream ends, so a size falling inside
    it hands the decoder a stream that stops mid-member."""
    path = write(dataset, "cutshort.cram", cram_payload(method=1, compressed_size=10))

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "cutshort.cram" in str(caught.value)


def test_a_negative_declared_block_size_is_refused_the_same_way(
    dataset: Path,
) -> None:
    """The five-byte ITF8 form spans the whole of a signed 32-bit integer, so
    a declared size can arrive negative."""
    path = write(dataset, "negative.cram", cram_payload(compressed_size=2**32 - 1))

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "negative.cram" in str(caught.value)


def test_the_reference_sequences_are_counted(dataset: Path) -> None:
    meta = extract(sample_cram(dataset))

    assert meta["sq_count"] == 2


def test_the_assembly_is_read_from_the_first_reference(dataset: Path) -> None:
    meta = extract(sample_cram(dataset))

    assert meta["assembly"] == "GRCh38"


def test_a_header_declaring_no_assembly_omits_the_key(dataset: Path) -> None:
    path = write(
        dataset,
        "noassembly.cram",
        cram_payload("@HD\tVN:1.6\n@SQ\tSN:chr1\tLN:100\n"),
    )

    assert "assembly" not in extract(path)


def test_read_groups_are_counted_and_their_platforms_named(dataset: Path) -> None:
    meta = extract(sample_cram(dataset))

    assert meta["read_group_count"] == 1
    assert meta["platforms"] == ["ILLUMINA"]
    assert meta["centres"] == ["STJUDE"]


def test_the_program_chain_keeps_its_order(dataset: Path) -> None:
    meta = extract(sample_cram(dataset))

    assert meta["programs"] == [
        {"id": "bwa", "name": "bwa", "version": "0.7.17"},
        {"id": "samtools", "name": "samtools", "version": "1.19"},
    ]


def test_padding_behind_the_header_text_is_not_part_of_it(dataset: Path) -> None:
    """A CRAM header block may be written longer than the text it holds, so
    that a later reheader can be done in place. The NULs that fill it are
    padding, not a program name with an unreadable byte in it."""
    path = write(dataset, "padded.cram", cram_payload(BAM_HEADER_TEXT + "\x00" * 32))

    assert extract(path)["programs"][-1]["version"] == "1.19"


def test_the_sample_tags_are_withheld_by_default(dataset: Path) -> None:
    """``@RG SM`` is the sample the read group came from: a cohort manifest,
    exactly as the VCF sample columns are."""
    meta = extract(sample_cram(dataset))

    assert "sample_ids" not in meta


def test_the_sample_tags_are_emitted_only_when_asked_for(dataset: Path) -> None:
    meta = extract(sample_cram(dataset), genomic_sample_ids=True)

    assert meta["sample_ids"] == ["NA00001"]


def test_what_the_header_says_is_stated_in_a_description(dataset: Path) -> None:
    described = extract(sample_cram(dataset))["description"]

    assert "CRAM 3.0" in described
    assert "coordinate" in described
    assert "GRCh38" in described
    assert "ILLUMINA" in described
    assert "bwa 0.7.17" in described
    assert "NA00001" not in described


def test_the_description_names_the_samples_when_asked_to(dataset: Path) -> None:
    described = extract(sample_cram(dataset), genomic_sample_ids=True)["description"]

    assert "NA00001" in described


def test_no_alignment_record_becomes_a_record_set(dataset: Path) -> None:
    """Aligned reads are not records of a dataset schema: a CRAM is described
    as a file, and the description is all of it."""
    meta = extract(sample_cram(dataset))
    meta["relative_path"] = "sample.cram"

    result = HANDLER.build_croissant([meta], ["file_0"])

    assert result.record_sets == []
    assert result.file_sets == []


def test_a_bake_carries_the_description_onto_the_file_object(
    dataset: Path, tmp_path: Path
) -> None:
    """The whole path, once: dispatch, extraction, the FileObject the generator
    owns, and construction under mlcroissant."""
    sample_cram(dataset)

    document = bake(dataset)

    assert record_sets(document) == []
    (described,) = file_objects(document)
    assert described["encodingFormat"] == "application/x-cram"
    assert "coordinate" in described["description"]

    written = tmp_path / "croissant.jsonld"
    written.write_text(json.dumps(document, indent=2, default=serialize_datetime))
    mlc.Dataset(str(written))


def test_a_wrapped_cram_is_described_like_the_plain_one(dataset: Path) -> None:
    name, payload = SAMPLES["CRAMHandler"]()[0]
    wrapped = write_wrapped(dataset, name, payload, ".gz")

    meta = HANDLER.extract(make_source(wrapped, Path(name)))

    assert meta["file_name"] == name
    assert meta["sq_count"] == 2


def test_the_flag_reaches_a_bake_from_the_command_line(
    dataset: Path, tmp_path: Path
) -> None:
    sample_cram(dataset)
    output = tmp_path / "croissant.jsonld"

    result = cli(dataset, output, "--genomic-sample-ids")

    assert result.exit_code == 0, result.output
    document = json.loads(output.read_text())
    assert "NA00001" in file_objects(document)[0]["description"]
