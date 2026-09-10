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
import logging
import struct
import zlib
from typing import BinaryIO

from croissant_baker.handlers.base_handler import BuildResult, FileTypeHandler
from croissant_baker.handlers.sam_header import describe_alignment, parse_sam_header
from croissant_baker.handlers.utils import (
    MAX_HEADER_BYTES,
    decompress_prefix,
    read_exactly,
)
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

#: The largest SAM text header this handler will read. ``l_text`` is a signed
#: 32-bit integer the file chooses, so trusting it turns a header read into a
#: read of the whole file, which is the one thing this handler exists not to
#: do. The cap is the shared one, because every container in this family states
#: its own header length and none of them may be believed about it.
MAX_TEXT_BYTES = MAX_HEADER_BYTES


def _read_exactly(stream: BinaryIO, count: int, what: str, name: str) -> bytes:
    """``count`` bytes, or a refusal naming the file and what was missing."""
    return read_exactly(stream, count, what, name, "BAM")


def _int32(stream: BinaryIO, what: str, name: str) -> int:
    return struct.unpack(INT32, _read_exactly(stream, INT32_BYTES, what, name))[0]


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

        A file that cannot be read peeks as ``b""`` and is therefore not
        claimed; that is
        :meth:`~croissant_baker.sources.FileSource.peek`'s contract. The
        prefix this handler decompresses itself is its own to guard, and the
        types are the ones a refused or corrupt member raises.
        """
        head = source.peek(CLAIM_BYTES)
        if head.startswith(MAGIC):
            return True
        if not head.startswith(COMPRESSED_MAGIC):
            return False
        try:
            return decompress_prefix(head, len(MAGIC)) == MAGIC
        except (OSError, EOFError, zlib.error):
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
        metadata["description"] = describe_alignment(
            self.FORMAT_NAME, header, reference_count, source.name, sample_ids
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
        # Checked before the read, not after: the point is not to read it.
        if not 0 <= text_length <= MAX_TEXT_BYTES:
            raise ValueError(
                f"Not a BAM file: {name} declares a SAM header of "
                f"{text_length} bytes, outside the 0 to {MAX_TEXT_BYTES} a "
                "header can be"
            )
        text = _read_exactly(payload, text_length, "the SAM header", name)
        header = parse_sam_header(text.decode("utf-8", "replace"))
        return header, _int32(payload, "n_ref", name)

    def build_croissant(self, file_metas: list, file_ids: list) -> tuple:
        """Nothing: a BAM is described as a file, by the description it carries.

        Aligned reads are records of a genome, not of a dataset schema, and a
        RecordSet naming columns no consumer can read through Croissant would
        be a promise nobody can keep.
        """
        return BuildResult([], [])
