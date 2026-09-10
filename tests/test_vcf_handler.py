"""VCF/gVCF: what the registry-wide sweeps cannot reach.

Unit level for claims and extraction, with one bake at the end, because the
end-to-end suite has no VCF dataset of its own.
"""

from __future__ import annotations

import json
from pathlib import Path

import mlcroissant as mlc
import pytest

from croissant_baker.entries import Reason
from croissant_baker.handlers.vcf_handler import VCFHandler
from croissant_baker.identifiers import serialize_datetime
from croissant_baker.sources import make_source

from tests.helpers import (
    SAMPLES,
    bake,
    bake_with_report,
    cli,
    cut_gzip,
    file_objects,
    record_sets,
    write_wrapped,
)

HANDLER = VCFHandler()

#: A vCard, which also spells its files ``.vcf``. The clash is the reason this
#: handler reads the first bytes rather than trusting the extension.
VCARD = (
    b"BEGIN:VCARD\r\n"
    b"VERSION:3.0\r\n"
    b"FN:Ada Lovelace\r\n"
    b"EMAIL:ada@example.org\r\n"
    b"END:VCARD\r\n"
)


def write(directory: Path, name: str, payload: bytes) -> Path:
    path = directory / name
    path.write_bytes(payload)
    return path


def source_for(path: Path, relative: str | None = None):
    return make_source(path, Path(relative or path.name))


def test_a_vcf_is_claimed_on_its_magic(dataset: Path) -> None:
    name, payload = SAMPLES["VCFHandler"]()[0]
    path = write(dataset, name, payload)

    assert HANDLER.claims(source_for(path))


def test_a_vcard_is_not_claimed(dataset: Path) -> None:
    """``.vcf`` is the vCard extension too; the magic is what separates them."""
    path = write(dataset, "contacts.vcf", VCARD)

    assert not HANDLER.claims(source_for(path))


def test_a_vcf_named_anything_else_is_still_claimed(dataset: Path) -> None:
    """The declaration is the claim, so a mis-suffixed export is still read."""
    _, payload = SAMPLES["VCFHandler"]()[0]
    path = write(dataset, "calls.txt", payload)

    assert HANDLER.claims(source_for(path))


def test_a_stream_that_cannot_be_read_is_not_claimed(dataset: Path) -> None:
    """This handler is the only one that reads bytes before looking at the
    name, so it is the one that meets a corrupt wrapper: a file it cannot open
    is a file it does not claim, not an exception out of dispatch."""
    path = write(dataset, "broken.vcf.xz", b"not really compressed")

    assert not HANDLER.claims(make_source(path, Path("broken.vcf.xz")))


def extract(path: Path, relative: str | None = None, **kwargs) -> dict:
    return HANDLER.extract(source_for(path, relative), **kwargs)


def sample_vcf(dataset: Path) -> Path:
    name, payload = SAMPLES["VCFHandler"]()[0]
    return write(dataset, name, payload)


def test_the_declared_format_and_reference_are_read(dataset: Path) -> None:
    meta = extract(sample_vcf(dataset))

    assert meta["fileformat"] == "VCFv4.2"
    assert meta["reference"] == "file:///ref/GRCh38.fa"


def test_contigs_are_counted_not_listed(dataset: Path) -> None:
    meta = extract(sample_vcf(dataset))

    assert meta["contig_count"] == 2


def test_a_header_with_no_reference_omits_the_key(dataset: Path) -> None:
    path = write(
        dataset,
        "noref.vcf",
        b"##fileformat=VCFv4.3\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n",
    )

    meta = extract(path)

    assert "reference" not in meta
    assert meta["fileformat"] == "VCFv4.3"


def test_info_declarations_keep_their_order_and_their_cardinality(
    dataset: Path,
) -> None:
    """``Number`` is the cardinality the producer declared: ``A`` is one value
    per alternate allele, and a ``Flag`` carries no value at all."""
    meta = extract(sample_vcf(dataset))

    assert [entry["id"] for entry in meta["info"]] == ["DP", "AF", "DB"]
    assert [entry["number"] for entry in meta["info"]] == ["1", "A", "0"]
    assert [entry["type"] for entry in meta["info"]] == ["Integer", "Float", "Flag"]


def test_a_quoted_description_survives_its_commas(dataset: Path) -> None:
    """``Description`` is quoted and routinely holds commas, so the ``<...>``
    list cannot be split on the comma alone."""
    meta = extract(sample_vcf(dataset))

    by_id = {entry["id"]: entry for entry in meta["info"]}

    assert by_id["AF"]["description"] == "Allele frequency, for each ALT allele"


def test_an_unbounded_number_is_read_verbatim(dataset: Path) -> None:
    path = write(
        dataset,
        "unbounded.vcf",
        b"##fileformat=VCFv4.2\n"
        b'##INFO=<ID=ANN,Number=.,Type=String,Description="Annotations">\n'
        b"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n",
    )

    meta = extract(path)

    assert meta["info"] == [
        {
            "id": "ANN",
            "number": ".",
            "type": "String",
            "description": "Annotations",
        }
    ]


def test_format_declarations_are_read(dataset: Path) -> None:
    meta = extract(sample_vcf(dataset))

    assert [entry["id"] for entry in meta["format"]] == ["GT", "AD"]
    assert [entry["number"] for entry in meta["format"]] == ["1", "R"]


def test_the_fixed_columns_present_are_recorded(dataset: Path) -> None:
    meta = extract(sample_vcf(dataset))

    assert meta["columns"] == [
        "CHROM",
        "POS",
        "ID",
        "REF",
        "ALT",
        "QUAL",
        "FILTER",
        "INFO",
        "FORMAT",
    ]


def test_a_genotype_free_header_declares_eight_columns(dataset: Path) -> None:
    """A sites-only VCF has no ``FORMAT`` column and no samples."""
    path = write(
        dataset,
        "sites.vcf",
        b"##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n",
    )

    meta = extract(path)

    assert meta["columns"] == [
        "CHROM",
        "POS",
        "ID",
        "REF",
        "ALT",
        "QUAL",
        "FILTER",
        "INFO",
    ]
    assert meta["sample_count"] == 0


def test_samples_are_counted_and_their_names_withheld(dataset: Path) -> None:
    """Sample columns are a manifest of the cohort; the count is not."""
    meta = extract(sample_vcf(dataset))

    assert meta["sample_count"] == 2
    assert "sample_ids" not in meta


def test_sample_names_are_emitted_only_when_asked_for(dataset: Path) -> None:
    meta = extract(sample_vcf(dataset), genomic_sample_ids=True)

    assert meta["sample_ids"] == ["NA00001", "NA00002"]


def test_a_plain_callset_is_not_a_gvcf(dataset: Path) -> None:
    assert extract(sample_vcf(dataset))["is_gvcf"] is False


@pytest.mark.parametrize(
    "declaration",
    [
        b"##GVCFBlock0-1=minGQ=0(inclusive),maxGQ=1(exclusive)\n",
        b'##ALT=<ID=NON_REF,Description="Represents any possible alt allele">\n',
    ],
)
def test_a_gvcf_is_recognised_from_its_header(
    declaration: bytes, dataset: Path
) -> None:
    path = write(
        dataset,
        "blocks.vcf",
        b"##fileformat=VCFv4.2\n"
        + declaration
        + b"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tNA00001\n",
    )

    assert extract(path)["is_gvcf"] is True


def test_a_wrapped_callset_reads_the_same(dataset: Path) -> None:
    """The handler is handed a decompressed stream, so it never sees the
    wrapper; this is the assertion that says so."""
    name, payload = SAMPLES["VCFHandler"]()[0]
    wrapped = write_wrapped(dataset, name, payload, ".gz")

    meta = HANDLER.extract(make_source(wrapped, Path(name)))

    assert meta["file_name"] == name
    assert meta["fileformat"] == "VCFv4.2"
    assert meta["sample_count"] == 2


def test_a_header_with_no_chrom_line_is_refused_with_a_reason(
    dataset: Path,
) -> None:
    """Without ``#CHROM`` there is no column list, so there is no schema."""
    path = write(
        dataset,
        "truncated.vcf",
        b"##fileformat=VCFv4.2\n##contig=<ID=chr1,length=248956422>\n",
    )

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "truncated.vcf" in str(caught.value)
    assert "#CHROM" in str(caught.value)


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


def fields_of(record_set) -> dict:
    return {field.name: field for field in record_set.fields}


def test_one_record_set_per_file(dataset: Path) -> None:
    first = sample_vcf(dataset)
    second = write(dataset, "other.vcf", first.read_bytes())

    record_sets = build(first, second)

    assert len(record_sets) == 2
    assert len({rs.id for rs in record_sets}) == 2


def test_the_fixed_columns_become_fields_in_declaration_order(
    dataset: Path,
) -> None:
    (record_set,) = build(sample_vcf(dataset))

    assert [field.name for field in record_set.fields] == [
        "CHROM",
        "POS",
        "ID",
        "REF",
        "ALT",
        "QUAL",
        "FILTER",
        "INFO",
        "FORMAT",
        "samples",
    ]


def test_the_fixed_columns_carry_the_types_the_format_fixes(
    dataset: Path,
) -> None:
    fields = fields_of(build(sample_vcf(dataset))[0])

    assert str(fields["CHROM"].data_types[0]) == "sc:Text"
    assert str(fields["POS"].data_types[0]) == "cr:Int64"
    assert str(fields["QUAL"].data_types[0]) == "cr:Float64"


def test_the_columns_holding_a_list_are_repeated(dataset: Path) -> None:
    """``ALT`` is comma-separated and ``FILTER`` semicolon-separated; both hold
    more than one value per record."""
    fields = fields_of(build(sample_vcf(dataset))[0])

    assert fields["ALT"].is_array is True
    assert fields["FILTER"].is_array is True
    assert fields["REF"].is_array is None


def test_info_keys_become_sub_fields_of_info(dataset: Path) -> None:
    fields = fields_of(build(sample_vcf(dataset))[0])
    sub = {field.name: field for field in fields["INFO"].sub_fields}

    assert list(sub) == ["DP", "AF", "DB"]
    assert str(sub["DP"].data_types[0]) == "cr:Int64"
    assert str(sub["AF"].data_types[0]) == "cr:Float64"
    assert str(sub["DB"].data_types[0]) == "sc:Boolean"


def test_a_sub_field_is_repeated_when_its_number_says_so(dataset: Path) -> None:
    fields = fields_of(build(sample_vcf(dataset))[0])
    info = {field.name: field for field in fields["INFO"].sub_fields}
    fmt = {field.name: field for field in fields["FORMAT"].sub_fields}

    assert info["AF"].is_array is True  # Number=A
    assert info["DP"].is_array is None  # Number=1
    assert info["DB"].is_array is None  # Number=0
    assert fmt["AD"].is_array is True  # Number=R
    assert fmt["GT"].is_array is None  # Number=1


def test_a_declared_description_becomes_the_sub_field_description(
    dataset: Path,
) -> None:
    fields = fields_of(build(sample_vcf(dataset))[0])
    sub = {field.name: field for field in fields["INFO"].sub_fields}

    assert "Approximate read depth" in sub["DP"].description


def test_the_samples_field_states_the_count_and_withholds_the_names(
    dataset: Path,
) -> None:
    fields = fields_of(build(sample_vcf(dataset))[0])

    assert str(fields["samples"].data_types[0]) == "sc:Text"
    assert fields["samples"].is_array is True
    assert "2" in fields["samples"].description
    assert "NA00001" not in fields["samples"].description


def test_the_samples_field_names_them_when_they_were_extracted(
    dataset: Path,
) -> None:
    fields = fields_of(build(sample_vcf(dataset), genomic_sample_ids=True)[0])

    assert "NA00001" in fields["samples"].description
    assert "NA00002" in fields["samples"].description


def test_a_sites_only_callset_has_neither_format_nor_samples(
    dataset: Path,
) -> None:
    path = write(
        dataset,
        "sites.vcf",
        b"##fileformat=VCFv4.2\n"
        b'##INFO=<ID=DP,Number=1,Type=Integer,Description="Read depth">\n'
        b"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n",
    )

    (record_set,) = build(path)

    assert [field.name for field in record_set.fields] == [
        "CHROM",
        "POS",
        "ID",
        "REF",
        "ALT",
        "QUAL",
        "FILTER",
        "INFO",
    ]


def test_the_header_properties_are_stated_in_the_description(
    dataset: Path,
) -> None:
    """The reference and the contig count are what a consumer harmonising two
    callsets asks first, so they are said in prose rather than in a key no
    Croissant vocabulary defines."""
    (record_set,) = build(sample_vcf(dataset))

    assert "VCFv4.2" in record_set.description
    assert "file:///ref/GRCh38.fa" in record_set.description
    assert "2 contigs" in record_set.description
    assert "calls.vcf" in record_set.description


def test_a_gvcf_says_so_in_the_description(dataset: Path) -> None:
    path = write(
        dataset,
        "blocks.vcf",
        b"##fileformat=VCFv4.2\n"
        b"##GVCFBlock0-1=minGQ=0(inclusive),maxGQ=1(exclusive)\n"
        b"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tNA00001\n",
    )

    (record_set,) = build(path)

    assert "gVCF" in record_set.description


def test_a_space_separated_column_line_is_refused_with_a_reason(
    dataset: Path,
) -> None:
    """The columns are tab-separated by specification. A line spelled with
    spaces declares one column named 'CHROM POS ID ...', and a record set of
    eight fields built over it would describe a schema the file never stated."""
    path = write(
        dataset,
        "spaced.vcf",
        b"##fileformat=VCFv4.2\n#CHROM POS ID REF ALT QUAL FILTER INFO\n",
    )

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "spaced.vcf" in str(caught.value)
    assert "CHROM" in str(caught.value)


def test_a_truncated_column_line_is_refused_with_a_reason(dataset: Path) -> None:
    """All eight fixed columns are mandatory; three of them are not a VCF."""
    path = write(
        dataset,
        "short.vcf",
        b"##fileformat=VCFv4.2\n#CHROM\tPOS\tID\n",
    )

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "short.vcf" in str(caught.value)


def test_every_field_built_is_a_column_the_header_declared(dataset: Path) -> None:
    """The guarantee the refusals above buy: the record set names the columns
    of the #CHROM line, in that order, and never a column it invented."""
    (record_set,) = build(sample_vcf(dataset))
    declared = extract(sample_vcf(dataset))["columns"]

    built = [field.name for field in record_set.fields]

    assert built[: len(declared)] == declared
    assert built[len(declared) :] == ["samples"]


def test_no_record_is_read(dataset: Path) -> None:
    """The header ends the read; whatever follows it is never parsed."""
    path = write(
        dataset,
        "onebadrecord.vcf",
        b"##fileformat=VCFv4.2\n"
        b"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        b"this line is not a record and is never parsed\n",
    )

    assert extract(path)["sample_count"] == 0


def test_a_wrapper_ending_mid_stream_is_refused_naming_the_file(
    dataset: Path,
) -> None:
    """A member intact for its first bytes opens, and then ends where the
    download stopped. What that raises is not an ``OSError``, and a file is
    owed a reason naming it either way."""
    contigs = b"".join(b"##contig=<ID=chr%d,length=100000>\n" % i for i in range(20000))
    path = write(dataset, "cut.vcf.gz", cut_gzip(b"##fileformat=VCFv4.2\n" + contigs))

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "cut.vcf" in str(caught.value)
    assert "VCF" in str(caught.value)


def test_a_refusal_reaches_the_scan_report_through_a_bake(dataset: Path) -> None:
    """A file this handler claims and cannot read is reported by name, with the
    reason it was refused for, and the callset beside it is still described:
    the loss is per-file, never the run."""
    write(dataset, "nocolumns.vcf", b"##fileformat=VCFv4.2\n##contig=<ID=chr1>\n")
    sample_vcf(dataset)

    document, report = bake_with_report(dataset)

    assert [o["name"] for o in file_objects(document)] == ["calls.vcf"]
    (refused,) = report.undescribed
    assert refused.name == "nocolumns.vcf"
    assert refused.reason is Reason.EXTRACT_FAILED
    assert "nocolumns.vcf" in refused.detail


def test_a_bake_over_a_callset_validates(dataset: Path, tmp_path: Path) -> None:
    """The whole path, once: dispatch, extraction, assembly and construction
    under mlcroissant."""
    sample_vcf(dataset)
    write(dataset, "contacts.vcf", VCARD)

    document, report = bake_with_report(dataset)

    (record_set,) = record_sets(document)
    assert record_set["@id"].endswith("_variants")
    assert [f["name"] for f in record_set["field"]][:2] == ["CHROM", "POS"]
    assert [o["encodingFormat"] for o in file_objects(document)] == ["text/x-vcf"]
    # The vCard is a .vcf nothing described, and that is the reported outcome.
    assert [e.name for e in report.undescribed] == ["contacts.vcf"]

    written = tmp_path / "croissant.jsonld"
    written.write_text(json.dumps(document, indent=2, default=serialize_datetime))
    mlc.Dataset(str(written))


def samples_description(document: dict) -> str:
    (record_set,) = record_sets(document)
    return next(f for f in record_set["field"] if f["name"] == "samples")["description"]


def test_a_bake_withholds_the_sample_names(dataset: Path) -> None:
    sample_vcf(dataset)

    assert "NA00001" not in samples_description(bake(dataset))


def test_the_generator_passes_the_opt_in_through_to_the_handler(
    dataset: Path,
) -> None:
    """The same plumbing ``count_csv_rows`` takes: one generator argument,
    forwarded to every handler, and named by the handler that reads it."""
    sample_vcf(dataset)

    assert "NA00001" in samples_description(bake(dataset, genomic_sample_ids=True))


def test_the_flag_reaches_a_bake_from_the_command_line(
    dataset: Path, tmp_path: Path
) -> None:
    sample_vcf(dataset)
    output = tmp_path / "croissant.jsonld"

    result = cli(dataset, output, "--genomic-sample-ids")

    assert result.exit_code == 0, result.output
    assert "NA00001" in samples_description(json.loads(output.read_text()))
