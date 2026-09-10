"""SAM: what the registry-wide sweeps cannot reach.

Unit level for claims and extraction, with one bake at the end, because the
end-to-end suite has no alignment dataset of its own.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import mlcroissant as mlc
import pytest

from croissant_baker.handlers.sam_handler import SAMHandler
from croissant_baker.identifiers import serialize_datetime
from croissant_baker.sources import FileSource, make_source

from tests.helpers import (
    BAM_HEADER_TEXT,
    SAM_ALIGNMENT_TEXT,
    SAMPLES,
    bake,
    cli,
    file_objects,
    record_sets,
    write_wrapped,
)

HANDLER = SAMHandler()


def write(directory: Path, name: str, payload: bytes) -> Path:
    path = directory / name
    path.write_bytes(payload)
    return path


def source_for(path: Path, relative: str | None = None):
    return make_source(path, Path(relative or path.name))


def sample_sam(dataset: Path) -> Path:
    name, payload = SAMPLES["SAMHandler"]()[0]
    return write(dataset, name, payload)


def extract(path: Path, relative: str | None = None, **kwargs) -> dict:
    return HANDLER.extract(source_for(path, relative), **kwargs)


def test_a_sam_is_claimed_on_its_extension_and_its_first_header_line(
    dataset: Path,
) -> None:
    assert HANDLER.claims(source_for(sample_sam(dataset)))


def test_a_sam_with_no_header_is_not_claimed(dataset: Path) -> None:
    """Alignment records alone declare nothing this handler can describe, and a
    file it would report as headerless is better left to say so as unclaimed."""
    path = write(dataset, "headerless.sam", SAM_ALIGNMENT_TEXT.encode())

    assert not HANDLER.claims(source_for(path))


def test_a_fastq_record_under_a_sam_name_is_not_claimed(dataset: Path) -> None:
    """A FASTQ opens with ``@`` too, so the leading character is not a claim."""
    path = write(
        dataset,
        "reads.sam",
        b"@read1\nACGTACGTAC\n+\nIIIIIIIIII\n",
    )

    assert not HANDLER.claims(source_for(path))


def test_the_sort_order_and_sam_version_are_read(dataset: Path) -> None:
    meta = extract(sample_sam(dataset))

    assert meta["sort_order"] == "coordinate"
    assert meta["sam_version"] == "1.6"


def test_the_reference_sequences_are_counted(dataset: Path) -> None:
    """The ``@SQ`` lines, and nothing else: a SAM carries no binary ``n_ref``."""
    meta = extract(sample_sam(dataset))

    assert meta["sq_count"] == 2
    assert "reference_count" not in meta


def test_the_assembly_is_read_from_the_first_reference(dataset: Path) -> None:
    meta = extract(sample_sam(dataset))

    assert meta["assembly"] == "GRCh38"


def test_a_header_declaring_no_assembly_omits_the_key(dataset: Path) -> None:
    path = write(dataset, "noassembly.sam", b"@HD\tVN:1.6\n@SQ\tSN:chr1\tLN:100\n")

    assert "assembly" not in extract(path)


def test_read_groups_are_counted_and_their_platforms_named(dataset: Path) -> None:
    meta = extract(sample_sam(dataset))

    assert meta["read_group_count"] == 1
    assert meta["platforms"] == ["ILLUMINA"]
    assert meta["centres"] == ["STJUDE"]


def test_the_program_chain_keeps_its_order(dataset: Path) -> None:
    meta = extract(sample_sam(dataset))

    assert meta["programs"] == [
        {"id": "bwa", "name": "bwa", "version": "0.7.17"},
        {"id": "samtools", "name": "samtools", "version": "1.19"},
    ]


def test_the_sample_tags_are_withheld_by_default(dataset: Path) -> None:
    """``@RG SM`` is the sample the read group came from: a cohort manifest,
    exactly as the VCF sample columns are."""
    meta = extract(sample_sam(dataset))

    assert "sample_ids" not in meta


def test_the_sample_tags_are_emitted_only_when_asked_for(dataset: Path) -> None:
    meta = extract(sample_sam(dataset), genomic_sample_ids=True)

    assert meta["sample_ids"] == ["NA00001"]


def test_what_the_header_says_is_stated_in_a_description(dataset: Path) -> None:
    described = extract(sample_sam(dataset))["description"]

    assert "coordinate" in described
    assert "GRCh38" in described
    assert "ILLUMINA" in described
    assert "bwa 0.7.17" in described
    assert "NA00001" not in described


def test_the_description_names_the_samples_when_asked_to(dataset: Path) -> None:
    described = extract(sample_sam(dataset), genomic_sample_ids=True)["description"]

    assert "NA00001" in described


def test_a_file_carrying_no_header_line_is_refused_with_a_reason(
    dataset: Path,
) -> None:
    """Claiming and extracting are separate questions: a headerless file reaches
    extraction whenever a caller hands it over directly."""
    path = write(dataset, "headerless.sam", SAM_ALIGNMENT_TEXT.encode())

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "headerless.sam" in str(caught.value)


class _Counted(io.RawIOBase):
    """A raw stream that remembers how many bytes were pulled through it.

    Raw rather than a plain delegate, because this handler reads the header a
    line at a time and only a real buffered reader over a real raw stream
    reports the bytes that were actually taken off the disk.
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


#: Comfortably above anything the handler needs to read a small header, and far
#: below the body the fixture puts behind it.
BOUNDED_PREFIX = 64 * 1024


def test_the_read_stops_at_the_first_alignment_record(dataset: Path) -> None:
    """What this handler exists not to do is read the reads.

    A SAM is plain text with no length in front of it, so the only thing that
    bounds the read is the stop at the first line that is not a header line.
    Four megabytes of alignments behind a six-line header cost the header.
    """
    body = SAM_ALIGNMENT_TEXT * 40000
    path = write(dataset, "deep.sam", (BAM_HEADER_TEXT + body).encode())
    opened: list = []
    assert path.stat().st_size > 4 * 1024 * 1024

    meta = HANDLER.extract(counting_source(path, opened))

    assert meta["sq_count"] == 2
    assert sum(stream.read_bytes for stream in opened) < BOUNDED_PREFIX


#: A body with no line ending anywhere in it, and the read a bounded handler
#: may spend before refusing it: the line cap, plus the chunk it was reached in.
NO_NEWLINE_BYTES = 4 * 1024 * 1024
BOUNDED_REFUSAL = 2 * 1024 * 1024


def test_a_body_holding_no_line_ending_is_refused_after_a_bounded_read(
    dataset: Path,
) -> None:
    """Nothing in front of a SAM header says how long it is, so a reader taking
    it a line at a time takes the whole file as one line when the file holds no
    line ending. A header line is a handful of tab-separated tags; a megabyte
    without one is not a header line, and the file is reported as such."""
    body = b"@HD\tVN:1.6\t" + b"x" * NO_NEWLINE_BYTES
    path = write(dataset, "unbroken.sam", body)
    opened: list = []

    with pytest.raises(ValueError) as caught:
        HANDLER.extract(counting_source(path, opened))

    assert "unbroken.sam" in str(caught.value)
    assert sum(stream.read_bytes for stream in opened) < BOUNDED_REFUSAL


def test_a_header_spanning_many_chunks_is_read_whole(dataset: Path) -> None:
    """Bounded is not truncated. A reference per contig of a fragmented
    assembly runs to hundreds of kilobytes of ``@SQ`` lines, and every one of
    them is a reference this handler counts."""
    references = 5000
    header = "@HD\tVN:1.6\tSO:coordinate\n" + "".join(
        f"@SQ\tSN:scaffold{i}\tLN:100000\tAS:GRCh38\n" for i in range(references)
    )
    path = write(dataset, "many.sam", (header + SAM_ALIGNMENT_TEXT).encode())
    assert path.stat().st_size > 128 * 1024

    assert extract(path)["sq_count"] == references


def test_no_alignment_record_becomes_a_record_set(dataset: Path) -> None:
    """Aligned reads are records of a genome, not of a dataset schema: a SAM is
    described as a file, and the description is all of it."""
    meta = extract(sample_sam(dataset))
    meta["relative_path"] = "sample.sam"

    result = HANDLER.build_croissant([meta], ["file_0"])

    assert result.record_sets == []
    assert result.file_sets == []


def test_a_bake_carries_the_description_onto_the_file_object(
    dataset: Path, tmp_path: Path
) -> None:
    """The whole path, once: dispatch, extraction, the FileObject the generator
    owns, and construction under mlcroissant."""
    sample_sam(dataset)

    document = bake(dataset)

    assert record_sets(document) == []
    (described,) = file_objects(document)
    assert described["encodingFormat"] == "text/x-sam"
    assert "coordinate" in described["description"]

    written = tmp_path / "croissant.jsonld"
    written.write_text(json.dumps(document, indent=2, default=serialize_datetime))
    mlc.Dataset(str(written))


def test_a_wrapped_sam_is_described_like_the_plain_one(dataset: Path) -> None:
    name, payload = SAMPLES["SAMHandler"]()[0]
    wrapped = write_wrapped(dataset, name, payload, ".gz")

    meta = HANDLER.extract(make_source(wrapped, Path(name)))

    assert meta["file_name"] == name
    assert meta["sq_count"] == 2


def test_the_flag_reaches_a_bake_from_the_command_line(
    dataset: Path, tmp_path: Path
) -> None:
    sample_sam(dataset)
    output = tmp_path / "croissant.jsonld"

    result = cli(dataset, output, "--genomic-sample-ids")

    assert result.exit_code == 0, result.output
    document = json.loads(output.read_text())
    assert "NA00001" in file_objects(document)[0]["description"]
