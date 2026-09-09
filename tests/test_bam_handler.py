"""BAM: what the registry-wide sweeps cannot reach.

Unit level for claims and extraction, with one bake at the end, because the
end-to-end suite has no alignment dataset of its own.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import mlcroissant as mlc
import pytest

from croissant_baker.handlers.bam_handler import BAMHandler
from croissant_baker.identifiers import serialize_datetime
from croissant_baker.sources import make_source

from tests.helpers import (
    SAMPLES,
    bake,
    bam_payload,
    cli,
    file_objects,
    record_sets,
    write_wrapped,
)

HANDLER = BAMHandler()


def write(directory: Path, name: str, payload: bytes) -> Path:
    path = directory / name
    path.write_bytes(payload)
    return path


def source_for(path: Path, relative: str | None = None):
    return make_source(path, Path(relative or path.name))


def sample_bam(dataset: Path) -> Path:
    name, payload = SAMPLES["BAMHandler"]()[0]
    return write(dataset, name, payload)


def extract(path: Path, relative: str | None = None, **kwargs) -> dict:
    return HANDLER.extract(source_for(path, relative), **kwargs)


def test_a_bam_is_claimed_on_the_magic_inside_its_wrapper(dataset: Path) -> None:
    """A BAM on disk is a compressed stream whose payload starts with the
    magic, and nothing outside it says so."""
    assert HANDLER.claims(source_for(sample_bam(dataset)))


def test_a_decompressed_payload_is_claimed_too(dataset: Path) -> None:
    """The compression layer does not strip ``.bam``, so a plain ``sample.bam``
    arrives compressed. A ``sample.bam.gz`` arrives with one layer already
    taken off, and it is the same file."""
    path = write(dataset, "unwrapped.bam", bam_payload())

    assert HANDLER.claims(source_for(path))


def test_a_compressed_file_that_is_not_a_bam_is_not_claimed(dataset: Path) -> None:
    path = write(dataset, "notes.bam", gzip.compress(b"plain text, gzipped\n"))

    assert not HANDLER.claims(source_for(path))


def test_bytes_that_are_neither_are_not_claimed(dataset: Path) -> None:
    path = write(dataset, "garbage.bam", b"\x00\xff not a real file \xfe\x00")

    assert not HANDLER.claims(source_for(path))


def test_the_sort_order_and_sam_version_are_read(dataset: Path) -> None:
    meta = extract(sample_bam(dataset))

    assert meta["sort_order"] == "coordinate"
    assert meta["sam_version"] == "1.6"


def test_the_reference_sequences_are_counted(dataset: Path) -> None:
    """Twice over: the ``@SQ`` lines of the text header, and the ``n_ref`` the
    binary header declares straight after it."""
    meta = extract(sample_bam(dataset))

    assert meta["sq_count"] == 2
    assert meta["reference_count"] == 2


def test_the_assembly_is_read_from_the_first_reference(dataset: Path) -> None:
    meta = extract(sample_bam(dataset))

    assert meta["assembly"] == "GRCh38"


def test_a_header_declaring_no_assembly_omits_the_key(dataset: Path) -> None:
    path = write(
        dataset,
        "noassembly.bam",
        gzip.compress(bam_payload("@HD\tVN:1.6\n@SQ\tSN:chr1\tLN:100\n", 1)),
    )

    assert "assembly" not in extract(path)


def test_read_groups_are_counted_and_their_platforms_named(dataset: Path) -> None:
    meta = extract(sample_bam(dataset))

    assert meta["read_group_count"] == 1
    assert meta["platforms"] == ["ILLUMINA"]
    assert meta["centres"] == ["STJUDE"]


def test_the_program_chain_keeps_its_order(dataset: Path) -> None:
    meta = extract(sample_bam(dataset))

    assert meta["programs"] == [
        {"id": "bwa", "name": "bwa", "version": "0.7.17"},
        {"id": "samtools", "name": "samtools", "version": "1.19"},
    ]


def test_the_sample_tags_are_withheld_by_default(dataset: Path) -> None:
    """``@RG SM`` is the sample the read group came from: a cohort manifest,
    exactly as the VCF sample columns are."""
    meta = extract(sample_bam(dataset))

    assert "sample_ids" not in meta


def test_the_sample_tags_are_emitted_only_when_asked_for(dataset: Path) -> None:
    meta = extract(sample_bam(dataset), genomic_sample_ids=True)

    assert meta["sample_ids"] == ["NA00001"]


def test_what_the_header_says_is_stated_in_a_description(dataset: Path) -> None:
    described = extract(sample_bam(dataset))["description"]

    assert "coordinate" in described
    assert "GRCh38" in described
    assert "ILLUMINA" in described
    assert "bwa 0.7.17" in described
    assert "NA00001" not in described


def test_the_description_names_the_samples_when_asked_to(dataset: Path) -> None:
    described = extract(sample_bam(dataset), genomic_sample_ids=True)["description"]

    assert "NA00001" in described


def test_a_truncated_header_is_refused_with_a_reason(dataset: Path) -> None:
    path = write(dataset, "truncated.bam", gzip.compress(b"BAM\x01\x10"))

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "truncated.bam" in str(caught.value)


def test_no_alignment_record_becomes_a_record_set(dataset: Path) -> None:
    """Aligned reads are not records of a dataset schema: a BAM is described as
    a file, and the description is all of it."""
    meta = extract(sample_bam(dataset))
    meta["relative_path"] = "sample.bam"

    result = HANDLER.build_croissant([meta], ["file_0"])

    assert result.record_sets == []
    assert result.file_sets == []


def test_a_bake_carries_the_description_onto_the_file_object(
    dataset: Path, tmp_path: Path
) -> None:
    """The whole path, once: dispatch, extraction, the FileObject the generator
    owns, and construction under mlcroissant."""
    sample_bam(dataset)

    document = bake(dataset)

    assert record_sets(document) == []
    (described,) = file_objects(document)
    assert described["encodingFormat"] == "application/x-bam"
    assert "coordinate" in described["description"]

    written = tmp_path / "croissant.jsonld"
    written.write_text(json.dumps(document, indent=2, default=serialize_datetime))
    mlc.Dataset(str(written))


def test_a_wrapped_bam_is_described_like_the_plain_one(dataset: Path) -> None:
    name, payload = SAMPLES["BAMHandler"]()[0]
    wrapped = write_wrapped(dataset, name, payload, ".gz")

    meta = HANDLER.extract(make_source(wrapped, Path(name)))

    assert meta["file_name"] == name
    assert meta["sq_count"] == 2


def test_the_flag_reaches_a_bake_from_the_command_line(
    dataset: Path, tmp_path: Path
) -> None:
    sample_bam(dataset)
    output = tmp_path / "croissant.jsonld"

    result = cli(dataset, output, "--genomic-sample-ids")

    assert result.exit_code == 0, result.output
    document = json.loads(output.read_text())
    assert "NA00001" in file_objects(document)[0]["description"]
