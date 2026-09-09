"""VCF handler: the declarations a callset makes about itself, and nothing else.

A VCF header declares its own schema: the reference it was called against, the
contigs it covers, and the ``INFO`` and ``FORMAT`` keys its records use, each
with a type and a cardinality. That is a RecordSet schema written down by the
producer, so this handler reads the header and stops at the first record.
"""

from typing import Dict, List, Optional

import mlcroissant as mlc

from croissant_baker.handlers.base_handler import BuildResult, FileTypeHandler
from croissant_baker.handlers.utils import (
    ARRAY_SHAPE_UNKNOWN_1D,
    allocate_record_set_ids,
    display_name,
    make_field_id,
)
from croissant_baker.sources import FileSource

#: The declaration every VCF and gVCF opens with, and the whole claim. The
#: extension cannot carry it: ``.vcf`` is also the vCard extension.
MAGIC = b"##fileformat=VCF"

#: VCF has no IANA registration. The ``x-`` form follows ``text/x-geo-soft``
#: and ``application/x-nifti``, already in the tree.
ENCODING_FORMAT = "text/x-vcf"

#: The columns VCF 4.x fixes, in the order the ``#CHROM`` line declares them.
#: The first eight are mandatory; ``FORMAT`` appears only when the file carries
#: genotypes, and the sample columns follow it.
FIXED_COLUMNS = (
    "CHROM",
    "POS",
    "ID",
    "REF",
    "ALT",
    "QUAL",
    "FILTER",
    "INFO",
    "FORMAT",
)

#: The eight of them a VCF must declare. A file naming fewer, or naming them
#: with spaces instead of tabs, declares a schema this handler cannot describe,
#: and the record set it would otherwise build is one the header never stated.
MANDATORY_COLUMNS = FIXED_COLUMNS[:8]

#: The ``Type`` values a ``##INFO`` or ``##FORMAT`` declaration may carry, and
#: the Croissant type each becomes. A ``Flag`` is presence or absence, which is
#: a boolean; ``Character`` is a one-character string, which Croissant has no
#: narrower type for than text.
VCF_TYPES = {
    "Integer": "cr:Int64",
    "Float": "cr:Float64",
    "Flag": "sc:Boolean",
    "String": "sc:Text",
    "Character": "sc:Text",
}

#: ``Number`` values that mean exactly one value, or none at all. Everything
#: else is a repeated field: ``A`` per alternate allele, ``R`` per allele,
#: ``G`` per genotype, ``.`` unbounded, or a literal count above one.
SINGULAR_NUMBERS = frozenset({"0", "1"})

#: The type and cardinality VCF 4.x fixes for each mandatory column, with the
#: sentence describing it. ``ALT`` is comma-separated and ``FILTER``
#: semicolon-separated, so both hold a list; the rest hold one value.
FIXED_FIELDS = (
    ("CHROM", "sc:Text", False, "Chromosome or contig the record sits on"),
    ("POS", "cr:Int64", False, "1-based position of the first base of REF"),
    ("ID", "sc:Text", False, "Identifier of the variant, or '.' when unnamed"),
    ("REF", "sc:Text", False, "Reference bases at this position"),
    ("ALT", "sc:Text", True, "Alternate alleles called at this position"),
    ("QUAL", "cr:Float64", False, "Phred-scaled quality of the ALT assertion"),
    ("FILTER", "sc:Text", True, "Filters the record failed, or PASS"),
)

#: The suffix the one record set per file is allocated under.
RECORD_SET_SUFFIX = "variants"

#: The single field standing for every genotype column. One field rather than
#: one per sample: the columns share a schema, and naming them one by one would
#: publish the cohort manifest that ``genomic_sample_ids`` gates.
SAMPLES_FIELD = "samples"

#: The alternate allele a gVCF uses to stand for "anything not called here".
GVCF_ALT_ID = "NON_REF"

#: The block declaration GATK writes into a gVCF header.
GVCF_BLOCK_PREFIX = "##GVCFBlock"


def map_vcf_type(declared: str) -> str:
    """The Croissant type for a declared VCF ``Type``.

    Anything unrecognised is text: a header this handler cannot type is still a
    header whose keys it can name, and text is what the file holds anyway.
    """
    return VCF_TYPES.get(declared, "sc:Text")


def is_repeated(number: str) -> bool:
    """Whether a declared ``Number`` means more than one value per record."""
    return number not in SINGULAR_NUMBERS


def split_declaration(body: str) -> List[str]:
    """Split a ``<key=value,...>`` list on its separating commas only.

    ``Description`` is quoted and routinely holds commas of its own, so the
    naive split loses half of every description that has one.
    """
    parts: List[str] = []
    current: List[str] = []
    quoted = False
    escaped = False
    for char in body:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\" and quoted:
            current.append(char)
            escaped = True
        elif char == '"':
            quoted = not quoted
            current.append(char)
        elif char == "," and not quoted:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return parts


def parse_declaration(line: str) -> Dict[str, str]:
    """The key-value pairs of one ``##KEY=<...>`` header line.

    Returns an empty mapping for a line carrying no ``<...>`` body, which is
    what a free-text ``##KEY=value`` line is.
    """
    start = line.find("<")
    end = line.rfind(">")
    if start < 0 or end < start:
        return {}
    pairs: Dict[str, str] = {}
    for part in split_declaration(line[start + 1 : end]):
        key, sep, value = part.partition("=")
        if not sep:
            continue
        value = value.strip()
        if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
            value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        pairs[key.strip()] = value
    return pairs


def _typed_key(line: str) -> Dict[str, str]:
    """One ``##INFO`` or ``##FORMAT`` declaration, in emission order."""
    pairs = parse_declaration(line)
    return {
        "id": pairs.get("ID", ""),
        "number": pairs.get("Number", ""),
        "type": pairs.get("Type", ""),
        "description": pairs.get("Description", ""),
    }


class _Header:
    """One VCF header, accumulated line by line.

    A class rather than a pile of locals because ``extract`` reads a single
    forward pass and the read has to stop at the first record: the state that
    survives that stop is exactly what is described.
    """

    def __init__(self) -> None:
        self.fileformat = ""
        self.reference: Optional[str] = None
        self.contig_count = 0
        self.info: List[Dict[str, str]] = []
        self.format: List[Dict[str, str]] = []
        self.columns: List[str] = []
        self.sample_ids: List[str] = []
        self.is_gvcf = False
        self.saw_columns = False

    def meta_line(self, line: str) -> None:
        """One ``##`` line."""
        if line.startswith("##fileformat="):
            self.fileformat = line[len("##fileformat=") :].strip()
        elif line.startswith("##reference="):
            self.reference = line[len("##reference=") :].strip()
        elif line.startswith("##contig="):
            self.contig_count += 1
        elif line.startswith("##INFO="):
            self.info.append(_typed_key(line))
        elif line.startswith("##FORMAT="):
            self.format.append(_typed_key(line))
        elif line.startswith("##ALT="):
            # A gVCF declares the symbolic allele standing for every position
            # it did not call; writers spell the id NON_REF or <NON_REF>.
            if parse_declaration(line).get("ID", "").strip("<>") == GVCF_ALT_ID:
                self.is_gvcf = True
        elif line.startswith(GVCF_BLOCK_PREFIX):
            self.is_gvcf = True

    def column_line(self, line: str) -> None:
        """The ``#CHROM`` line: the fixed columns, then the samples."""
        self.saw_columns = True
        fields = line.lstrip("#").split("\t")
        for field, expected in zip(fields, FIXED_COLUMNS):
            if field.strip() != expected:
                break
            self.columns.append(expected)
        if len(self.columns) == len(FIXED_COLUMNS):
            self.sample_ids = [f.strip() for f in fields[len(FIXED_COLUMNS) :]]


class VCFHandler(FileTypeHandler):
    """Handler for VCF and gVCF variant call files (``.vcf``).

    One RecordSet per file, whose fields are the eight or nine fixed columns
    the ``#CHROM`` line declares, with ``INFO`` and ``FORMAT`` carrying one
    sub-field per declared key. No record is ever read: everything emitted is a
    header byte, and the sample columns are counted rather than named unless
    ``genomic_sample_ids`` is passed.
    """

    EXTENSIONS = (".vcf",)
    FORMAT_NAME = "VCF"
    FORMAT_DESCRIPTION = (
        "Reference, contig count, typed INFO and FORMAT keys, sample count"
    )

    def claims(self, source: FileSource) -> bool:
        """Claim a stream that opens with ``##fileformat=VCF``.

        On the declaration alone, not on the extension: ``.vcf`` is shared with
        vCard, and a vCard described as a callset is worse than one nothing
        claims.

        Reading bytes before looking at the name means meeting every file in
        the dataset, corrupt wrappers among them. A decompression library
        raises its own exception type rather than ``OSError``, and a file this
        handler cannot open is one it does not claim, not one that fails
        dispatch for everybody behind it.
        """
        try:
            return source.peek(len(MAGIC)) == MAGIC
        except Exception:  # noqa: BLE001, an unreadable file is not a claim
            return False

    def extract(
        self, source: FileSource, genomic_sample_ids: bool = False, **kwargs
    ) -> dict:
        """Read one VCF header, stopping at the first record.

        Args:
            source: The file, already decompressed.
            genomic_sample_ids: If True, emit the sample column names. Off by
                default: for a controlled release the sample columns are a
                manifest of the cohort, and the count answers the structural
                question without publishing one.
        """
        if not source.exists:
            raise FileNotFoundError(f"VCF file not found: {source.relative_path}")

        header = self._read_header(source)

        if not header.fileformat.startswith("VCF"):
            raise ValueError(
                f"Not a VCF file: {source.relative_path} carries no "
                "'##fileformat=VCF' declaration"
            )
        if not header.saw_columns:
            raise ValueError(
                f"Incomplete VCF header in {source.relative_path}: no '#CHROM' "
                "line, so the file declares no columns"
            )
        if tuple(header.columns[: len(MANDATORY_COLUMNS)]) != MANDATORY_COLUMNS:
            raise ValueError(
                f"Incomplete VCF header in {source.relative_path}: the '#CHROM' "
                "line declares "
                + (", ".join(header.columns) if header.columns else "no column")
                + ", not the tab-separated "
                + " ".join(MANDATORY_COLUMNS)
                + " a VCF fixes"
            )

        metadata = {
            "file_name": source.name,
            "file_size": source.size,
            "sha256": source.sha256,
            "encoding_format": ENCODING_FORMAT,
            "fileformat": header.fileformat,
            "contig_count": header.contig_count,
            "info": header.info,
            "format": header.format,
            "columns": header.columns,
            "sample_count": len(header.sample_ids),
            "is_gvcf": header.is_gvcf,
        }
        if header.reference is not None:
            metadata["reference"] = header.reference
        if genomic_sample_ids and header.sample_ids:
            metadata["sample_ids"] = header.sample_ids
        return metadata

    def _read_header(self, source: FileSource) -> _Header:
        """Every line up to the first that is not a header line.

        What is read is bounded by the size of the header, not by the size of
        the file: the loop stops at the first line that does not start with
        ``#``, so a callset of a hundred million records costs the same read as
        one of ten. There is deliberately no cap on the number of header lines,
        because a cohort VCF legitimately declares thousands of contigs and
        keys, and every one of them is a field this handler emits.

        Decoded permissively: a header is ASCII by specification, and a stray
        byte in a description is not a reason to refuse a file whose structure
        is otherwise readable.
        """
        header = _Header()
        try:
            with source.open() as stream:
                for raw in stream:
                    line = raw.decode("utf-8", "replace").rstrip("\r\n")
                    if not line.startswith("#"):
                        break
                    if line.startswith("##"):
                        header.meta_line(line)
                    else:
                        header.column_line(line)
                        break
        except OSError as exc:
            raise ValueError(
                f"Failed to read VCF file {source.relative_path}: {exc}"
            ) from exc
        return header

    def build_croissant(self, file_metas: list, file_ids: list) -> tuple:
        """One record set per callset: its columns, as the header declares them."""
        allocated = allocate_record_set_ids(file_metas, [RECORD_SET_SUFFIX])
        record_sets = [
            self._record_set(meta, file_id, ids[RECORD_SET_SUFFIX])
            for meta, file_id, ids in zip(file_metas, file_ids, allocated)
        ]
        return BuildResult([], record_sets)

    # ------------------------------------------------------------------

    def _record_set(self, meta: dict, file_id: str, rs_id: str) -> mlc.RecordSet:
        used: set = set()
        fields = [
            self._field(rs_id, file_id, used, name, data_type, repeated, description)
            for name, data_type, repeated, description in FIXED_FIELDS
        ]
        fields.append(self._group("INFO", meta["info"], rs_id, file_id, used))
        if meta["sample_count"] > 0:
            fields.append(self._group("FORMAT", meta["format"], rs_id, file_id, used))
            fields.append(self._samples_field(meta, rs_id, file_id, used))
        return mlc.RecordSet(
            id=rs_id,
            name=rs_id,
            description=_description(meta),
            fields=fields,
        )

    def _field(
        self,
        parent_id: str,
        file_id: str,
        used: set,
        name: str,
        data_type: str,
        repeated: bool,
        description: str,
        sub_fields: Optional[list] = None,
    ) -> mlc.Field:
        """One field, sourced from the file and nothing narrower.

        No ``extract``: Croissant 1.1's extract grammar addresses columns of a
        format mlcroissant's reader knows how to open, and VCF is not on that
        list, so a column reference here would be a promise nobody can keep.
        """
        return mlc.Field(
            id=make_field_id(parent_id, name, used),
            name=name,
            description=description,
            data_types=[data_type] if data_type else None,
            is_array=True if repeated else None,
            array_shape=ARRAY_SHAPE_UNKNOWN_1D if repeated else None,
            source=mlc.Source(file_object=file_id),
            sub_fields=sub_fields or None,
        )

    def _group(
        self, name: str, declared: list, rs_id: str, file_id: str, used: set
    ) -> mlc.Field:
        """``INFO`` or ``FORMAT``: one sub-field per declared key.

        The keys are a per-record bag rather than columns of their own, so they
        are modelled as sub-fields of the column that carries them, which is
        where the header puts them.
        """
        field_id = make_field_id(rs_id, name, used)
        sub_used: set = set()
        sub_fields = [
            self._field(
                field_id,
                file_id,
                sub_used,
                key["id"],
                map_vcf_type(key["type"]),
                is_repeated(key["number"]),
                _key_description(name, key),
            )
            for key in declared
            if key["id"]
        ]
        return mlc.Field(
            id=field_id,
            name=name,
            description=(
                f"{name} column, holding the keys declared by the header's "
                f"'##{name}' lines ({_plural(len(sub_fields), 'key')})"
            ),
            source=mlc.Source(file_object=file_id),
            sub_fields=sub_fields or None,
            data_types=None if sub_fields else ["sc:Text"],
        )

    def _samples_field(
        self, meta: dict, rs_id: str, file_id: str, used: set
    ) -> mlc.Field:
        """The genotype columns, as one repeated field."""
        described = (
            "Genotype columns, one per sample, each holding the values the "
            f"FORMAT keys name ({_plural(meta['sample_count'], 'sample')})"
        )
        sample_ids = meta.get("sample_ids")
        if sample_ids:
            described += ". Sample identifiers: " + ", ".join(sample_ids)
        return self._field(
            rs_id,
            file_id,
            used,
            SAMPLES_FIELD,
            "sc:Text",
            True,
            described,
        )


def _plural(count: int, noun: str) -> str:
    """``1 contig``, ``2 samples``."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _key_description(group: str, key: dict) -> str:
    """One ``##INFO`` or ``##FORMAT`` key, described as the header describes it."""
    declared = f"{group} key '{key['id']}' (Number={key['number'] or '.'})"
    return f"{declared}. {key['description']}" if key["description"] else declared


def _description(meta: dict) -> str:
    """What the header says about the callset, in one deterministic sentence.

    Prose rather than new keys: ``fileformat``, ``reference`` and the contig
    count have no home in the Croissant or Schema.org vocabularies, and an
    invented JSON-LD key is one no consumer reads.
    """
    stated = [meta["fileformat"]]
    if meta.get("reference"):
        stated.append(f"reference {meta['reference']}")
    stated.append(_plural(meta["contig_count"], "contig"))
    stated.append(_plural(meta["sample_count"], "sample"))
    described = (
        f"Variant records in {display_name(meta)} ({', '.join(stated)}). "
        "One record per called variant."
    )
    if meta["is_gvcf"]:
        described += " gVCF: the header declares non-variant reference blocks."
    return described
