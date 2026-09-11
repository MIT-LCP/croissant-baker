"""The genomic handlers against files an encoder wrote, not files a test wrote.

Every other genomic fixture in this suite is built in :mod:`tests.helpers` from
the same reading of the specification the handlers decode with. That makes them
excellent at pinning behaviour and useless as evidence: a field misread the
same way in both places agrees with itself. The files under
``tests/data/input/genomics_htslib`` were written by htslib through pysam and
have never been through this codebase, so they are the independent check. What
is asserted here is what the encoder put in them, taken from the recipe in
their ``PROVENANCE.txt``.

The five alignment containers carry one header between them, in three
encodings. That is the point of having all five: a SAM, a BAM and three CRAMs
that disagree about their own header disagree about how it is read, since the
bytes they were given said the same thing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, List, Tuple

import mlcroissant as mlc
import pytest

from croissant_baker.handlers.registry import select_handler
from croissant_baker.identifiers import serialize_datetime
from croissant_baker.scan import Outcome, Reason, ScanReport
from croissant_baker.sources import make_source

from tests.helpers import (
    DATA,
    bake_with_report,
    by_name,
    file_objects,
    record_sets,
)

DIRECTORY = DATA / "genomics_htslib"

#: The provenance note documents the directory; it is not a file of the
#: dataset, and every bake here leaves it out so the outcomes are the fixtures'.
PROVENANCE = "PROVENANCE.txt"

#: The data files, and the media type each is described with.
ENCODING_FORMATS = {
    "aln.sam": "text/x-sam",
    "aln.bam": "application/x-bam",
    "aln_v21.cram": "application/x-cram",
    "aln_v30.cram": "application/x-cram",
    "aln_v31.cram": "application/x-cram",
    "calls.vcf": "text/x-vcf",
    "calls.bcf": "application/x-bcf",
    "reads.fastq": "text/x-fastq",
    "reference.fasta": "text/x-fasta",
}

#: The index files htslib wrote alongside them. No handler claims an index: it
#: is an offset table into a file that is already described, and describing it
#: twice would say the dataset holds two things where it holds one.
INDEX_FILES = {
    "aln.bam.bai",
    "aln_v30.cram.crai",
    "aln_v31.cram.crai",
    "calls.bcf.csi",
    "reference.fasta.fai",
}

#: The CRAM 1.0 file definition, kept because a version the handler declines to
#: walk is worth having a real example of.
REFUSED = "v10.cram"

#: The five alignment containers, in the order the recipe wrote them.
ALIGNMENTS = ["aln.sam", "aln.bam", "aln_v21.cram", "aln_v30.cram", "aln_v31.cram"]

#: What the SAM header the recipe built says, key by key. Every container is
#: held to this, and to each other.
HEADER_TRUTH = {
    "sort_order": "coordinate",
    "sam_version": "1.6",
    "sq_count": 2,
    "assembly": "GRCh38",
    "read_group_count": 1,
    "platforms": ["ILLUMINA"],
    "centres": ["STJUDE"],
    "programs": [
        {"id": "bwa", "name": "bwa", "version": "0.7.17"},
        {"id": "samtools", "name": "samtools", "version": "1.19"},
    ],
}

#: The version each CRAM's file definition states.
CRAM_VERSIONS = {
    "aln_v21.cram": "2.1",
    "aln_v30.cram": "3.0",
    "aln_v31.cram": "3.1",
}

#: The read group's sample, and the two sample columns of the callset. Both are
#: withheld unless the bake is asked for them.
ALIGNMENT_SAMPLE = "NA00001"
CALLSET_SAMPLES = ["NA00001", "NA00002"]

#: The length of every read the recipe wrote.
READ_LENGTH = 50


def bake(**kwargs) -> Tuple[dict, ScanReport]:
    """Bake the fixture directory, leaving the provenance note out of it."""
    kwargs.setdefault("excludes", [PROVENANCE])
    return bake_with_report(DIRECTORY, **kwargs)


def extract(name: str, **kwargs) -> dict:
    """Ask the handler that owns ``name`` to describe it, outside a bake."""
    selection = select_handler(DIRECTORY / name, Path(name))
    assert selection.handler is not None, f"nothing claimed {name}"
    return selection.handler.extract(
        make_source(DIRECTORY / name, Path(name)), **kwargs
    )


def outcomes(report: ScanReport) -> dict:
    """Every scanned file by name, with the outcome and reason it ended on."""
    return {e.name: (e.outcome, e.reason) for e in report.entries}


def shape(fields: Iterable[dict]) -> List[tuple]:
    """The name, type and repeated flag of every field, sub-fields included.

    Everything a record set says about its columns except which file they came
    from, which is the one thing two containers of the same callset are
    entitled to disagree about.
    """
    described = []
    for field in fields:
        described.append(
            (
                field["name"],
                field.get("dataType"),
                bool(field.get("cr:isArray")),
                field.get("cr:arrayShape"),
            )
        )
        described.extend(shape(field.get("subField", [])))
    return described


def test_the_nine_data_files_are_described() -> None:
    document, report = bake()

    described = {e.name for e in report.described}
    assert described == set(ENCODING_FORMATS)
    assert set(by_name(file_objects(document))) == set(ENCODING_FORMATS)


def test_the_index_files_are_left_alone() -> None:
    """An index is not claimed, and is reported rather than passed over."""
    _, report = bake()

    unclaimed = {
        name: reason
        for name, (outcome, reason) in outcomes(report).items()
        if outcome is Outcome.UNCLAIMED
    }

    assert unclaimed == dict.fromkeys(INDEX_FILES, Reason.NO_HANDLER)


def test_a_cram_1_0_file_is_refused_by_version() -> None:
    """Reported, and reported as what it is: the version is in the sentence, so
    a reader is told which files the handler would have to grow to take."""
    _, report = bake()

    (refused,) = [e for e in report.entries if e.name == REFUSED]

    assert refused.outcome is Outcome.FAILED
    assert refused.reason is Reason.EXTRACT_FAILED
    assert "version 1" in refused.detail


def test_the_document_constructs_under_mlcroissant(tmp_path: Path) -> None:
    document, _ = bake()

    written = tmp_path / "croissant.jsonld"
    written.write_text(json.dumps(document, indent=2, default=serialize_datetime))

    mlc.Dataset(str(written))


def test_every_container_reads_the_same_header() -> None:
    """One header in three encodings. Compared against the recipe and against
    each other, because either alone would miss half of a shared misreading."""
    read = {name: extract(name) for name in ALIGNMENTS}

    for name, meta in read.items():
        stated = {key: meta.get(key) for key in HEADER_TRUTH}
        assert stated == HEADER_TRUTH, name


@pytest.mark.parametrize("name,version", sorted(CRAM_VERSIONS.items()))
def test_a_cram_states_the_version_it_was_written_at(name: str, version: str) -> None:
    assert extract(name)["cram_version"] == version


def test_a_callset_describes_the_same_in_either_container() -> None:
    """The BCF carries the VCF's header text verbatim, so the two record sets
    may differ in the file they point at and in nothing else."""
    document, _ = bake()
    sets = by_name(record_sets(document), "@id")

    vcf = sets["calls_variants_vcf"]
    bcf = sets["calls_variants_bcf"]

    assert shape(vcf["field"]) == shape(bcf["field"])


def test_sample_identifiers_are_withheld_by_default() -> None:
    """Not withheld from one node and left in another: the whole document is
    searched, because a cohort manifest leaks wherever it is written."""
    document, _ = bake()

    serialised = json.dumps(document, default=serialize_datetime)

    for sample in CALLSET_SAMPLES:
        assert sample not in serialised


def test_sample_identifiers_are_emitted_when_asked() -> None:
    document, _ = bake(genomic_sample_ids=True)
    described = by_name(file_objects(document))
    sets = by_name(record_sets(document), "@id")

    for name in ALIGNMENTS:
        assert ALIGNMENT_SAMPLE in described[name]["description"], name

    for record_set in (sets["calls_variants_vcf"], sets["calls_variants_bcf"]):
        serialised = json.dumps(record_set, default=serialize_datetime)
        for sample in CALLSET_SAMPLES:
            assert sample in serialised, record_set["@id"]


def test_the_first_read_length_is_the_one_the_reads_have() -> None:
    assert extract("reads.fastq")["first_read_length"] == READ_LENGTH


def test_a_fasta_names_none_of_its_records() -> None:
    """The description lines hold contig names, and a reference's contig names
    are sequence content the handler is not there to republish."""
    assert "chr1" not in extract("reference.fasta")["description"]


@pytest.mark.parametrize("name,encoding", sorted(ENCODING_FORMATS.items()))
def test_each_file_is_described_with_its_own_media_type(
    name: str, encoding: str
) -> None:
    document, _ = bake()

    assert by_name(file_objects(document))[name]["encodingFormat"] == encoding


def test_two_bakes_of_the_directory_agree() -> None:
    """Sixteen files over a thread pool, three of which are the same format:
    if handler batching or worker order reached the output, it would show here."""
    first, _ = bake()
    second, _ = bake()

    assert json.dumps(first, default=serialize_datetime) == json.dumps(
        second, default=serialize_datetime
    )
