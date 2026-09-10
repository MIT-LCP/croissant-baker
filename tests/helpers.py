"""Shared test vocabulary: sample data, fixture writing, baking, navigation."""

from __future__ import annotations

import base64
import bz2
import gzip
import io
import lzma
import struct
import zlib
from pathlib import Path
from typing import Callable, Iterable, Optional

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import tifffile

from typer.testing import CliRunner

from croissant_baker import compression
from croissant_baker.__main__ import app
from croissant_baker.handlers.base_handler import FileTypeHandler
from croissant_baker.handlers.registry import HandlerRegistry, builtin_handlers
from croissant_baker.metadata_generator import MetadataGenerator
from croissant_baker.scan import ScanReport

DATA = Path(__file__).parent / "data" / "input"
_SPECT = DATA / "spect_demo"


def _csv() -> list:
    return [("data.csv", b"id,name,score\n1,Ada,9.5\n2,Grace,9.9\n")]


def _tsv() -> list:
    return [("data.tsv", b"id\tname\tscore\n1\tAda\t9.5\n2\tGrace\t9.9\n")]


def _jsonl() -> list:
    return [
        ("records.jsonl", b'{"id": 1, "name": "Ada"}\n{"id": 2, "name": "Grace"}\n')
    ]


def _ndjson() -> list:
    """Three bulk-export chunks, which is how FHIR data actually arrives."""
    return [
        (
            f"Patient.{i:03d}.ndjson",
            b'{"resourceType": "Patient", "id": "a", "gender": "female"}\n'
            b'{"resourceType": "Patient", "id": "b", "gender": "male"}\n',
        )
        for i in range(3)
    ]


def _parquet() -> list:
    buffer = io.BytesIO()
    pq.write_table(
        pa.table({"id": pa.array([1, 2]), "name": pa.array(["Ada", "Grace"])}), buffer
    )
    return [("table.parquet", buffer.getvalue())]


PNG_1X1 = base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQD"
    b"wAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


#: The namespace of the current OME schema. It is versioned, so nothing in
#: the source matches this constant — the version is read off the root element.
OME_NAMESPACE = "http://www.openmicroscopy.org/Schemas/OME/2016-06"


def ome_xml(body: str, *, namespace: str = OME_NAMESPACE, attrs: str = "") -> str:
    """An OME-XML document wrapping ``body``, shaped the way a writer emits one."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<OME xmlns="{namespace}"{attrs}>{body}</OME>'
    )


def tiff_bytes(
    description: Optional[str] = None, *, planes: int = 1, size: int = 8, **kwargs
) -> bytes:
    """A TIFF in memory, with ``description`` written verbatim to tag 270.

    Encoded as UTF-8 because tag 270 is nominally 7-bit ASCII and every OME
    writer puts ``µm`` in it regardless.
    """
    shape = (planes, size, size) if planes > 1 else (size, size)
    buffer = io.BytesIO()
    tifffile.imwrite(
        buffer,
        np.zeros(shape, np.uint16),
        photometric="minisblack",
        description=None if description is None else description.encode("utf-8"),
        # Otherwise tifffile writes its own shape note into tag 270, and a
        # fixture meant to carry no description carries one.
        metadata=None,
        **kwargs,
    )
    return buffer.getvalue()


#: The ``<Pixels>`` attributes a microscope writes, on an 8x8 fixture.
#:
#: Every pair the handler could confuse holds two different values: SizeZ
#: against SizeT, PhysicalSizeX against Y, and the X unit against the Y unit.
#: Each axis retains its own unit. With the pairs equal
#: — as a symmetric fixture makes them — a field reading its neighbour is
#: invisible, and three such swaps went unnoticed.
OME_PIXELS = (
    'DimensionOrder="XYCZT" Type="uint16" SizeX="8" SizeY="8"'
    ' SizeC="3" SizeZ="1" SizeT="5"'
    ' PhysicalSizeX="0.2125" PhysicalSizeXUnit="µm"'
    ' PhysicalSizeY="0.425" PhysicalSizeYUnit="mm"'
)


def ome_image(
    *,
    identifier: str = "Image:0",
    attrs: str = "",
    pixels: str = OME_PIXELS,
    channels: tuple = ("DAPI", "ATP1A1", "18S"),
    trailing: str = "",
) -> str:
    """One ``<Image>`` element, the shape Bio-Formats and tifffile both write."""
    inner = "".join(
        f'<Channel ID="Channel:0:{i}" SamplesPerPixel="1" Name="{name}"/>'
        for i, name in enumerate(channels)
    )
    return (
        f'<Image ID="{identifier}"{attrs}>'
        f'<Pixels ID="Pixels:0" {pixels}>{inner}{trailing}</Pixels>'
        "</Image>"
    )


def ome_bomb(levels: int = 6) -> str:
    """A billion-laughs OME-XML document of ``levels`` entity generations.

    Six is deliberate. Expat 2.4+ caps input amplification, so a nine-level
    bomb raises ``ParseError`` unaided — a parser with no declaration check at
    all would survive one and prove nothing. Six still expands.
    """
    entities = ['<!ENTITY a0 "lol">']
    entities += [f'<!ENTITY a{i} "{"&a%d;" % (i - 1) * 10}">' for i in range(1, levels)]
    return (
        '<?xml version="1.0"?>\n<!DOCTYPE OME [\n'
        + "\n".join(entities)
        + f']>\n<OME xmlns="{OME_NAMESPACE}"><Image ID="&a{levels - 1};"/></OME>'
    )


#: A three-channel OME-TIFF, written by hand rather than by ``imwrite(ome=True)``
#: so the bytes are the same on every run — that writer stamps a fresh UUID.
OME_TIFF = tiff_bytes(ome_xml(ome_image()), planes=3)


def _images() -> list:
    """A PNG and a three-channel OME-TIFF: the two collections the handler splits.

    The TIFF is appended rather than prepended, because ``probe_name()`` and the
    exclusive-format sweep both read element 0.
    """
    return [
        ("pixel.png", PNG_1X1),
        ("sample.ome.tif", OME_TIFF),
        ("plain.tif", tiff_bytes()),
    ]


def _dicom() -> list:
    source = next(_SPECT.rglob("*.dcm"), None)
    assert source is not None, f"tracked DICOM fixture missing under {_SPECT}"
    return [("scan.dcm", source.read_bytes())]


def _soft() -> list:
    """A miniature GEO family export: one series, one platform, two samples.

    Small, but not degenerate. The platform and both samples carry inline
    tables, the samples carry characteristics, and ``!Series_sample_id``
    repeats — so the sweep sees the shapes a real deposit has rather than an
    attribute block on its own.
    """
    return [
        (
            "GSE1_family.soft",
            b"^DATABASE = GeoMiame\n"
            b"!Database_name = Gene Expression Omnibus (GEO)\n"
            b"^SERIES = GSE1\n"
            b"!Series_title = A miniature series\n"
            b"!Series_sample_id = GSM1\n"
            b"!Series_sample_id = GSM2\n"
            b"^PLATFORM = GPL1\n"
            b"!Platform_title = A miniature platform\n"
            b"!Platform_data_row_count = 2\n"
            b"#ID = Probe set identifier\n"
            b"!platform_table_begin\n"
            b"ID\tGB_ACC\n"
            b"1_at\tU48705\n"
            b"2_at\tM87338\n"
            b"!platform_table_end\n"
            b"^SAMPLE = GSM1\n"
            b"!Sample_title = First sample\n"
            b"!Sample_characteristics_ch1 = tissue: liver\n"
            b"!Sample_data_row_count = 2\n"
            b"#VALUE = Intensity\n"
            b"!sample_table_begin\n"
            b"ID_REF\tVALUE\n"
            b"1_at\t320.5\n"
            b"2_at\t388.4\n"
            b"!sample_table_end\n"
            b"^SAMPLE = GSM2\n"
            b"!Sample_title = Second sample\n"
            b"!Sample_characteristics_ch1 = tissue: kidney\n"
            b"!Sample_data_row_count = 2\n"
            b"!sample_table_begin\n"
            b"ID_REF\tVALUE\n"
            b"1_at\t305.4\n"
            b"2_at\t339.2\n"
            b"!sample_table_end\n",
        )
    ]


def _hdf5() -> list:
    """A 10x feature matrix: the smallest sample that exercises a layout."""
    from tests.hdf5_fixtures import tenx_bytes

    return [("filtered_feature_bc_matrix.h5", tenx_bytes())]


#: The SAM text header of the sample BAM, which its own test also reads.
BAM_HEADER_TEXT = (
    "@HD\tVN:1.6\tSO:coordinate\n"
    "@SQ\tSN:chr1\tLN:248956422\tAS:GRCh38\n"
    "@SQ\tSN:chr2\tLN:242193529\tAS:GRCh38\n"
    "@RG\tID:rg1\tPL:ILLUMINA\tCN:STJUDE\tLB:lib1\tSM:NA00001\n"
    "@PG\tID:bwa\tPN:bwa\tVN:0.7.17\n"
    "@PG\tID:samtools\tPN:samtools\tVN:1.19\tPP:bwa\n"
)


def bam_payload(text: str = BAM_HEADER_TEXT, references: int = 2) -> bytes:
    """The uncompressed bytes of a header-only BAM.

    Built rather than committed: a BAM carries its lengths inside it, and a
    fixture nobody can read by eye is one nobody can change.
    """
    encoded = text.encode()
    payload = b"BAM\x01" + struct.pack("<i", len(encoded)) + encoded
    payload += struct.pack("<i", references)
    for i in range(references):
        name = f"chr{i + 1}".encode() + b"\x00"
        payload += struct.pack("<i", len(name)) + name + struct.pack("<i", 248956422)
    return payload


def _bam() -> list:
    """One header-only BAM: two references, one read group, a two-step @PG chain.

    Plain gzip rather than BGZF, and ``mtime=0`` so the same header is the same
    bytes on every call. Python's gzip module reads both spellings, and it is
    the header this handler describes.
    """
    return [("sample.bam", gzip.compress(bam_payload(), mtime=0))]


#: One alignment record, in the eleven mandatory SAM columns. Present so a test
#: proving the read stops at the first record has a record to stop at.
SAM_ALIGNMENT_TEXT = (
    "read1\t0\tchr1\t100\t60\t10M\t*\t0\t0\tACGTACGTAC\tIIIIIIIIII\n"
    "read2\t16\tchr2\t200\t60\t10M\t*\t0\t0\tTGCATGCATG\tIIIIIIIIII\n"
)


def _sam() -> list:
    """One SAM carrying the same header as the sample BAM, then two reads.

    The reads are the point: a header-only fixture cannot tell a handler that
    stops at the first alignment apart from one that reads to end of file.
    """
    return [("sample.sam", (BAM_HEADER_TEXT + SAM_ALIGNMENT_TEXT).encode())]


#: The header of the sample callset, which a BCF carries verbatim.
#:
#: Small, but not degenerate. ``AF`` is per-alternate-allele, ``DB`` is a flag
#: and ``AD`` is per-allele, so the sweep sees the cardinalities a real callset
#: has rather than one column of scalars. It is a constant because the binary
#: container declares the same text, and the two fixtures have to agree for the
#: record set built from either to be comparable.
VCF_HEADER_TEXT = (
    b"##fileformat=VCFv4.2\n"
    b'##FILTER=<ID=PASS,Description="All filters passed">\n'
    b"##reference=file:///ref/GRCh38.fa\n"
    b"##contig=<ID=chr1,length=248956422>\n"
    b"##contig=<ID=chr2,length=242193529>\n"
    b'##INFO=<ID=DP,Number=1,Type=Integer,Description="Approximate read depth">\n'
    b'##INFO=<ID=AF,Number=A,Type=Float,Description="Allele frequency, for each ALT allele">\n'
    b'##INFO=<ID=DB,Number=0,Type=Flag,Description="dbSNP membership">\n'
    b'##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
    b'##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allelic depths">\n'
    b"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tNA00001\tNA00002\n"
)


def _tf8(value: int, forms: int) -> bytes:
    """The shared body of ITF8 and LTF8, or ``b""`` when neither form fits.

    Both encodings spell a number the same way: the leading one-bits of the
    first byte count the bytes that follow, and the bits left over in that
    first byte are the number's most significant ones. Only the widest form of
    each differs, so only that is written out per encoding.
    """
    for extra in range(forms):
        if value < 1 << (7 + 7 * extra):
            prefix = (0xFF << (8 - extra)) & 0xFF
            tail = value & ((1 << (8 * extra)) - 1)
            return bytes([prefix | (value >> (8 * extra))]) + tail.to_bytes(
                extra, "big"
            )
    return b""


def _itf8(value: int) -> bytes:
    """One non-negative integer in CRAM's ITF8 encoding.

    Non-negative only: every field a header-only fixture writes is a count, an
    offset or an identifier, and the negative form exists for the unmapped
    reference id such a container never declares.

    The five-byte form is the odd one, and the reason this is not just
    ``_tf8``: the first byte carries the top four bits and the last carries
    only its own low four, so the five together hold exactly 32.
    """
    if value < 0:
        raise ValueError(f"ITF8 encodes no negative value; got {value}")
    return _tf8(value, 4) or bytes(
        [
            0xF0 | ((value >> 28) & 0x0F),
            (value >> 20) & 0xFF,
            (value >> 12) & 0xFF,
            (value >> 4) & 0xFF,
            value & 0x0F,
        ]
    )


def _ltf8(value: int) -> bytes:
    """One non-negative integer in CRAM's LTF8 encoding, the 64-bit ITF8.

    The widest form is regular where ITF8's is not: a first byte of all ones,
    then the whole number in the eight that follow.
    """
    if value < 0:
        raise ValueError(f"LTF8 encodes no negative value; got {value}")
    return _tf8(value, 8) or b"\xff" + value.to_bytes(8, "big")


#: The compressors CRAM's block methods name, by method number. 4 is rANS,
#: which has no stdlib codec and which the handler refuses; a fixture asking
#: for it declares the method over uncompressed bytes, because the refusal is
#: reached before anything is decoded.
_CRAM_COMPRESSORS = {
    0: lambda data: data,
    1: lambda data: gzip.compress(data, mtime=0),
    2: bz2.compress,
    3: lzma.compress,
}


def _cram_container_header(major: int, length: int) -> bytes:
    """The first container's header: every field zero but the block count.

    A header-only container spans no reference and holds no record, so the one
    field with anything to say is that a single block follows. Written in the
    major-2 layout below version 3, which is what the handler reads there; a
    version-1 fixture is given the same bytes, and is refused on its version
    long before the handler reaches them.
    """
    header = (
        struct.pack("<i", length)
        # Reference id, alignment start, alignment span, record count.
        + _itf8(0) * 4
        + (_ltf8(0) if major >= 3 else _itf8(0))
        + _ltf8(0)
        + _itf8(1)
        + _itf8(0)
    )
    if major >= 3:
        header += struct.pack("<I", zlib.crc32(header))
    return header


def cram_payload(
    text: str = BAM_HEADER_TEXT,
    version: tuple = (3, 0),
    method: int = 0,
    content_type: int = 0,
    compressed_size: Optional[int] = None,
    raw_size: Optional[int] = None,
    compress: Optional[Callable[[bytes], bytes]] = None,
) -> bytes:
    """The bytes of a header-only CRAM: file definition, container, one block.

    Built rather than committed, for the reason ``bam_payload`` is: a container
    states its own lengths, and a fixture nobody can read by eye is one nobody
    can change. ``compressed_size`` and ``raw_size`` override what the block
    declares, so a test can state a size the file does not hold, and
    ``compress`` overrides how the block is written, so a test can state a
    method over a spelling of it the default table does not use.
    """
    major, minor = version
    encoded = text.encode()
    content = struct.pack("<i", len(encoded)) + encoded
    compressor = compress or _CRAM_COMPRESSORS.get(method, _CRAM_COMPRESSORS[0])
    data = compressor(content)
    block = (
        bytes([method, content_type])
        + _itf8(0)
        + _itf8(len(data) if compressed_size is None else compressed_size)
        + _itf8(len(content) if raw_size is None else raw_size)
        + data
    )
    if major >= 3:
        block += struct.pack("<I", zlib.crc32(block))
    definition = b"CRAM" + bytes([major, minor]) + bytes(20)
    return definition + _cram_container_header(major, len(block)) + block


def _cram() -> list:
    """One header-only CRAM 3.0, carrying the SAM header the BAM sample carries.

    The same text on purpose: a difference between the two handlers is then a
    difference in what they read, not in what they were given.
    """
    return [("sample.cram", cram_payload())]


def _vcf() -> list:
    """A small multi-sample VCFv4.2 export: two samples, two variant records.

    The second record carries two ALTs, so the per-allele keys the header
    declares are exercised by data as well as by declaration.
    """
    return [
        (
            "calls.vcf",
            VCF_HEADER_TEXT
            + b"chr1\t100\trs1\tA\tG\t50.0\tPASS\tDP=14;AF=0.5;DB\tGT:AD\t0/1:7,7\t1/1:0,14\n"
            + b"chr1\t200\t.\tC\tT,A\t99.0\tPASS\tDP=20;AF=0.25,0.25\tGT:AD\t0/1:15,5,0\t0/0:20,0,0\n",
        )
    ]


def _fastq() -> list:
    """Two well-formed reads, named the way an Illumina sequencer names them.

    Two rather than one, so the sweep sees a file the handler has to stop part
    way through rather than one it happens to reach the end of.
    """
    return [
        (
            "reads.fastq",
            b"@A00123:45:HVXXXDSXX:1:1101:1000:1000 1:N:0:ATCACG\n"
            b"ACGTACGT\n"
            b"+\n"
            b"IIIIIIII\n"
            b"@A00123:45:HVXXXDSXX:1:1101:1000:2000 1:N:0:ATCACG\n"
            b"TTGGCCAA\n"
            b"+\n"
            b"IIIIFFFF\n",
        )
    ]


def _fasta() -> list:
    """Two records, so the sweep sees a file whose first line is not its only one.

    The description line carries a name and a comment, which is the shape a
    record name takes when it is a sample identifier, and nothing the handler
    emits may repeat either.
    """
    return [("reference.fa", b">chr1 test contig\nACGTACGTNN\n>chr2\nGGCCAATT\n")]


def _smiles() -> list:
    """Three molecules with a name beside each, which is the common layout.

    Three rather than one, so the sweep sees a file whose column count is
    agreed on by several lines rather than declared by the only one there is.
    """
    return [("molecules.smi", b"CCO\tethanol\nC\tmethane\nc1ccccc1\tbenzene\n")]


def bcf_payload(text: bytes = VCF_HEADER_TEXT, minor: int = 2) -> bytes:
    """The uncompressed bytes of a BCF 2.x container, header and no record.

    Built rather than committed, for the reason ``bam_payload`` is: the length
    lives inside the bytes, and a fixture nobody can read by eye is one nobody
    can change. ``l_text`` counts the terminating NUL, as the specification
    says it does.
    """
    return (
        b"BCF\x02" + bytes([minor]) + struct.pack("<I", len(text) + 1) + text + b"\x00"
    )


def _bcf() -> list:
    """The sample callset again, in its binary container.

    Plain gzip rather than BGZF, and ``mtime=0`` so the same header is the same
    bytes on every call. Python's gzip module reads both spellings, and it is
    the header this handler describes.
    """
    return [("calls.bcf", gzip.compress(bcf_payload(), mtime=0))]


def _nifti() -> list:
    source = next(_SPECT.rglob("*.nii.gz"), None)
    assert source is not None, f"tracked NIfTI fixture missing under {_SPECT}"
    return [("scan.nii", gzip.decompress(source.read_bytes()))]


#: Handler class name -> builder returning ``[(logical name, plain bytes)]``.
#: A list rather than one pair so a handler whose FileSets span several files
#: can say so: FHIR chunks are the shape that produced the phantom ``.gz.gz``
#: includes. This is the single place a new handler registers test data.
SAMPLES: dict[str, Callable[[], list]] = {
    "CSVHandler": _csv,
    "TSVHandler": _tsv,
    "JSONHandler": _jsonl,
    "FHIRHandler": _ndjson,
    "ParquetHandler": _parquet,
    "ImageHandler": _images,
    "DICOMHandler": _dicom,
    "NIfTIHandler": _nifti,
    "SOFTHandler": _soft,
    "HDF5Handler": _hdf5,
    "VCFHandler": _vcf,
    "BAMHandler": _bam,
    "SAMHandler": _sam,
    "FASTQHandler": _fastq,
    "FASTAHandler": _fasta,
    "SMILESHandler": _smiles,
    "BCFHandler": _bcf,
    "CRAMHandler": _cram,
}

#: Handlers with no sample, and why.
EXEMPT: dict[str, str] = {
    "WFDBHandler": (
        "a WFDB record is a header read with its sibling .dat and .atr files, "
        "so no single stream carries it; a compressed .hea is reported instead"
    )
}


def write_wrapped(directory: Path, name: str, payload: bytes, suffix: str = "") -> Path:
    """Write ``payload`` to ``directory/name+suffix``, compressing if asked."""
    target = directory / f"{name}{suffix}"
    if not suffix:
        target.write_bytes(payload)
        return target
    comp = compression.compression_for(target.name)
    assert comp is not None, f"{suffix!r} is not a registered compression"
    with comp.opener(target, "wb") as fh:
        fh.write(payload)
    return target


def cut_gzip(payload: bytes) -> bytes:
    """A gzip member of ``payload``, cut off part way through its stream.

    Past the ten-byte header and short of the trailer, so the member opens and
    then ends mid-stream: what a partly downloaded file looks like to a reader,
    and what makes a decompressor raise rather than return.
    """
    data = gzip.compress(payload, mtime=0)
    return data[: len(data) * 2 // 3]


def write_all(directory: Path, files: Iterable[tuple], suffix: str = "") -> None:
    for name, payload in files:
        write_wrapped(directory, name, payload, suffix)


def bake(directory: Path, **kwargs) -> dict:
    """Bake ``directory`` and return the document."""
    kwargs.setdefault("name", "test")
    return MetadataGenerator(dataset_path=str(directory), **kwargs).generate_metadata()


def bake_with_report(directory: Path, **kwargs) -> tuple[dict, ScanReport]:
    """Bake ``directory`` and return ``(document, scan report)``."""
    kwargs.setdefault("name", "test")
    generator = MetadataGenerator(dataset_path=str(directory), **kwargs)
    return generator.generate_metadata(), generator.scan_report


def bake_with(handlers: Iterable[FileTypeHandler], directory: Path, **kwargs):
    """Bake with ``handlers`` ahead of the built-ins. Returns ``(doc, report)``."""
    return bake_with_report(
        directory, handlers=HandlerRegistry([*handlers, *builtin_handlers()]), **kwargs
    )


runner = CliRunner()


def cli(dataset: Path, output: Path, *extra: str):
    """Invoke the CLI over ``dataset`` with the minimum viable flag set."""
    return runner.invoke(
        app,
        [
            "--input",
            str(dataset),
            "--output",
            str(output),
            "--creator",
            "Tester",
            "--no-validate",
            *extra,
        ],
    )


def _typed(doc: dict, node_type: str) -> list:
    return [n for n in doc.get("distribution", []) if n.get("@type") == node_type]


def file_objects(doc: dict) -> list:
    return _typed(doc, "cr:FileObject")


def file_sets(doc: dict) -> list:
    return _typed(doc, "cr:FileSet")


def record_sets(doc: dict) -> list:
    return doc.get("recordSet", [])


def as_list(value) -> list:
    """mlcroissant collapses a single-element list to a scalar; undo that."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def includes(file_set: dict) -> list:
    return as_list(file_set.get("includes"))


def file_set_members(file_set: dict, directory: Path) -> set[str]:
    """Spec membership using filesystem globs, not mlcroissant's record reader.

    Reader compatibility is checked separately in ``test_ome_filesets.py``;
    mlcroissant 1.1.0 currently ignores exclusions.
    """

    def matched(key):
        return {
            str(path.relative_to(directory))
            for pattern in as_list(file_set.get(key))
            for path in directory.glob(pattern)
            if path.is_file()
        }

    return matched("includes") - matched("cr:excludes")


def by_name(nodes: Iterable[dict], key: str = "name") -> dict:
    return {n[key]: n for n in nodes}


#: The built-in wrapper suffixes tests parametrise over.
WRAPPER_SUFFIXES = [c.suffix for c in compression.BUILTIN_COMPRESSIONS]

__all__ = [
    "DATA",
    "EXEMPT",
    "OME_NAMESPACE",
    "OME_PIXELS",
    "OME_TIFF",
    "PNG_1X1",
    "SAMPLES",
    "VCF_HEADER_TEXT",
    "WRAPPER_SUFFIXES",
    "bake",
    "bake_with",
    "bake_with_report",
    "bcf_payload",
    "by_name",
    "cli",
    "cram_payload",
    "cut_gzip",
    "file_objects",
    "file_sets",
    "file_set_members",
    "includes",
    "ome_bomb",
    "ome_image",
    "ome_xml",
    "record_sets",
    "tiff_bytes",
    "runner",
    "write_all",
    "write_wrapped",
]
