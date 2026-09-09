"""BAM handler: the SAM header of an alignment file, and no alignment record.

A BAM opens with a magic, a length, and the SAM text header the aligner wrote:
which assembly the reads were placed against, which platform and centre
produced them, and which programs touched them, in order. That is provenance
the file states about itself, so it is read and the rest of the file is not.

No RecordSet: aligned reads are not records of a dataset schema. What this
handler produces is a described FileObject, through the ``description`` key the
generator honours.
"""

import gzip
import io
import logging
import struct
from typing import BinaryIO, Dict, List, Optional

from croissant_baker.handlers.base_handler import BuildResult, FileTypeHandler
from croissant_baker.sources import FileSource

logger = logging.getLogger(__name__)

#: The four bytes a BAM's decompressed payload opens with.
MAGIC = b"BAM\x01"

#: The two bytes every member of a gzip stream opens with. BAM is BGZF, which
#: is gzip with an extra field Python's gzip module ignores.
COMPRESSED_MAGIC = b"\x1f\x8b"

#: BAM has no IANA registration. The ``x-`` form follows ``application/x-nifti``
#: and ``text/x-geo-soft``, already in the tree.
ENCODING_FORMAT = "application/x-bam"

#: Enough of the head to decide a claim: one BGZF block is at most 64 KiB, and
#: the magic is the first four bytes of the first block's payload.
CLAIM_BYTES = 4096

#: ``l_text`` and ``n_ref`` are both little-endian signed 32-bit integers.
INT32 = "<i"
INT32_BYTES = 4


class _SamHeader:
    """The SAM text header, read line by line into what is described."""

    def __init__(self) -> None:
        self.sam_version = ""
        self.sort_order = ""
        self.sq_count = 0
        self.assembly = ""
        self.read_group_count = 0
        self.platforms: List[str] = []
        self.centres: List[str] = []
        self.sample_ids: List[str] = []
        self.programs: List[Dict[str, str]] = []

    def read(self, text: str) -> None:
        for line in text.splitlines():
            if not line.startswith("@"):
                continue
            fields = line.split("\t")
            tags = _tags(fields[1:])
            record = fields[0]
            if record == "@HD":
                self.sam_version = tags.get("VN", "")
                self.sort_order = tags.get("SO", "")
            elif record == "@SQ":
                self.sq_count += 1
                # From the first reference only: an assembly is a property of
                # the header, and reading it off every line would say a
                # mixed-assembly file has one.
                if self.sq_count == 1:
                    self.assembly = tags.get("AS", "")
            elif record == "@RG":
                self.read_group_count += 1
                _collect(self.platforms, tags.get("PL"))
                _collect(self.centres, tags.get("CN"))
                _collect(self.sample_ids, tags.get("SM"))
            elif record == "@PG":
                self.programs.append(
                    {
                        "id": tags.get("ID", ""),
                        "name": tags.get("PN", ""),
                        "version": tags.get("VN", ""),
                    }
                )


def _tags(fields: List[str]) -> Dict[str, str]:
    """The ``TAG:value`` pairs of one header line, in declaration order."""
    pairs: Dict[str, str] = {}
    for field in fields:
        tag, sep, value = field.partition(":")
        if sep:
            pairs.setdefault(tag.strip(), value.strip())
    return pairs


def _collect(into: List[str], value: Optional[str]) -> None:
    """Add ``value`` once, keeping the order the header declared it in.

    Declaration order rather than sorted: read groups are written in the order
    the file was assembled, and that order is itself header content.
    """
    if value and value not in into:
        into.append(value)


def _read_exactly(stream: BinaryIO, count: int, what: str, name: str) -> bytes:
    """``count`` bytes, or a refusal naming the file and what was missing."""
    data = stream.read(count)
    if len(data) != count:
        raise ValueError(
            f"Truncated BAM header in {name}: {what} needs {count} bytes, "
            f"got {len(data)}"
        )
    return data


def _int32(stream: BinaryIO, what: str, name: str) -> int:
    return struct.unpack(INT32, _read_exactly(stream, INT32_BYTES, what, name))[0]


def _plural(count: int, noun: str) -> str:
    """``1 read group``, ``2 reference sequences``."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _program_chain(programs: List[Dict[str, str]]) -> str:
    """``bwa 0.7.17, samtools 1.19``, in the order the header declares."""
    named = []
    for program in programs:
        name = program["name"] or program["id"]
        if not name:
            continue
        named.append(f"{name} {program['version']}" if program["version"] else name)
    return ", ".join(named)


def _description(
    header: _SamHeader, reference_count: int, name: str, sample_ids: List[str]
) -> str:
    """What the header says, in one deterministic sentence.

    Prose rather than new keys: sort order, assembly, sequencing platform and
    the program chain have no home in the Croissant or Schema.org vocabularies,
    and an invented JSON-LD key is one no consumer reads.
    """
    stated = []
    if header.sort_order:
        stated.append(f"{header.sort_order}-sorted")
    references = _plural(reference_count, "reference sequence")
    stated.append(
        f"{references} ({header.assembly})" if header.assembly else references
    )
    stated.append(_plural(header.read_group_count, "read group"))
    for label, values in (("platform", header.platforms), ("centre", header.centres)):
        if values:
            stated.append(f"{label}: {', '.join(values)}")
    chain = _program_chain(header.programs)
    if chain:
        stated.append(f"aligned with {chain}")
    described = (
        f"BAM alignment file {name} ({'; '.join(stated)}). "
        "Described from its header; no alignment record was read."
    )
    if sample_ids:
        described += " Sample identifiers: " + ", ".join(sample_ids) + "."
    return described


class BAMHandler(FileTypeHandler):
    """Handler for BAM alignment files (``.bam``).

    The compression layer does not strip ``.bam``, so this handler is given the
    bytes as they sit on disk and decompresses them itself. It reads the magic,
    the SAM text header and the reference count, and stops: no alignment record
    is ever touched.

    No RecordSet is emitted. The output is the FileObject the generator builds,
    carrying the description this handler wrote.
    """

    EXTENSIONS = (".bam",)
    FORMAT_NAME = "BAM"
    FORMAT_DESCRIPTION = (
        "Sort order, reference count and assembly, read groups, program chain"
    )

    def claims(self, source: FileSource) -> bool:
        """Claim a stream whose payload opens with the BAM magic.

        Two spellings, because the pipeline can hand over either. A ``.bam`` on
        disk is compressed and reaches this handler as it is stored, so the
        magic is inside the wrapper. A ``.bam`` that arrived under a second
        wrapper has had one layer taken off already, and the magic is the first
        thing in the stream.

        A file this handler cannot open is one it does not claim: a
        decompression library raises its own exception type rather than
        ``OSError``, and that must not end dispatch for the handlers behind it.
        """
        try:
            head = source.peek(CLAIM_BYTES)
        except Exception:  # noqa: BLE001, an unreadable file is not a claim
            return False
        if head.startswith(MAGIC):
            return True
        if not head.startswith(COMPRESSED_MAGIC):
            return False
        try:
            return _decompress_prefix(head, len(MAGIC)) == MAGIC
        except Exception:  # noqa: BLE001, nor is an undecodable one
            return False

    def extract(
        self, source: FileSource, genomic_sample_ids: bool = False, **kwargs
    ) -> dict:
        """Read one BAM header, stopping at the first alignment record.

        Args:
            source: The file, as it is stored.
            genomic_sample_ids: If True, emit the ``@RG SM`` sample tags. Off
                by default: together they are a manifest of the cohort, and the
                read-group count answers the structural question without
                publishing one.
        """
        if not source.exists:
            raise FileNotFoundError(f"BAM file not found: {source.relative_path}")

        name = str(source.relative_path)
        header, reference_count = self._read_header(source, name)

        metadata = {
            "file_name": source.name,
            "file_size": source.size,
            "sha256": source.sha256,
            "encoding_format": ENCODING_FORMAT,
            "sam_version": header.sam_version,
            "sort_order": header.sort_order,
            "sq_count": header.sq_count,
            "reference_count": reference_count,
            "read_group_count": header.read_group_count,
            "platforms": header.platforms,
            "centres": header.centres,
            "programs": header.programs,
        }
        if header.assembly:
            metadata["assembly"] = header.assembly
        # Withheld before anything is written, so the description cannot leak
        # what the metadata withholds.
        sample_ids = header.sample_ids if genomic_sample_ids else []
        if sample_ids:
            metadata["sample_ids"] = sample_ids
        # The one thing this handler emits. Built here rather than in
        # build_croissant, which runs after the FileObject is staged, and from
        # the logical name, which is the only one extraction is given.
        metadata["description"] = _description(
            header, reference_count, source.name, sample_ids
        )
        return metadata

    def _read_header(self, source: FileSource, name: str):
        """The SAM header and the reference count, and nothing after them."""
        try:
            compressed = source.peek(len(COMPRESSED_MAGIC)) == COMPRESSED_MAGIC
            with source.open() as stored:
                if not compressed:
                    return self._read_payload(stored, name)
                with gzip.GzipFile(fileobj=stored, mode="rb") as payload:
                    return self._read_payload(payload, name)
        except (OSError, EOFError, struct.error) as exc:
            raise ValueError(f"Failed to read BAM file {name}: {exc}") from exc

    def _read_payload(self, payload: BinaryIO, name: str):
        magic = _read_exactly(payload, len(MAGIC), "the magic", name)
        if magic != MAGIC:
            raise ValueError(
                f"Not a BAM file: {name} does not carry the BAM magic at the "
                "start of its payload"
            )
        text_length = _int32(payload, "l_text", name)
        if text_length < 0:
            raise ValueError(f"Not a BAM file: {name} declares l_text {text_length}")
        text = _read_exactly(payload, text_length, "the SAM header", name)
        header = _SamHeader()
        header.read(text.decode("utf-8", "replace"))
        return header, _int32(payload, "n_ref", name)

    def build_croissant(self, file_metas: list, file_ids: list) -> tuple:
        """Nothing: a BAM is described as a file, by the description it carries.

        Aligned reads are records of a genome, not of a dataset schema, and a
        RecordSet naming columns no consumer can read through Croissant would
        be a promise nobody can keep.
        """
        return BuildResult([], [])


def _decompress_prefix(head: bytes, count: int) -> bytes:
    """The first ``count`` bytes inside a compressed prefix.

    A prefix, so the stream ends mid-member; that is expected, and the bytes
    already produced are the answer.
    """
    with gzip.GzipFile(fileobj=io.BytesIO(head), mode="rb") as payload:
        return payload.read(count)
