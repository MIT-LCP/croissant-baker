"""BAM handler: the SAM header of an alignment file, and no alignment record.

A BAM opens with a magic, a length, and the SAM text header the aligner wrote:
which assembly the reads were placed against, which platform and centre
produced them, and which programs touched them, in order. That is provenance
the file states about itself, so it is read and the rest of the file is not.

No RecordSet: aligned reads are not records of a dataset schema. What this
handler produces is a described FileObject, through the ``description`` key the
generator honours.
"""

import logging
import struct
from typing import BinaryIO

from croissant_baker.handlers.base_handler import BuildResult, FileTypeHandler
from croissant_baker.handlers.sam_header import alignment_metadata, parse_sam_header
from croissant_baker.handlers.utils import (
    MAX_HEADER_BYTES,
    bgzf_payload_starts_with,
    open_bgzf,
    read_exactly,
)
from croissant_baker.sources import UNREADABLE, FileSource

logger = logging.getLogger(__name__)

#: The four bytes a BAM's decompressed payload opens with.
MAGIC = b"BAM\x01"

#: BAM has no IANA registration. The ``x-`` form follows ``application/x-nifti``
#: and ``text/x-geo-soft``, already in the tree.
ENCODING_FORMAT = "application/x-bam"

#: ``l_text`` and ``n_ref`` are both little-endian signed 32-bit integers.
INT32 = "<i"
INT32_BYTES = 4


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

        Inside its BGZF wrapper as a ``.bam`` is stored, or already unwrapped
        when it arrived under a second wrapper; see
        :func:`~croissant_baker.handlers.utils.bgzf_payload_starts_with`.
        """
        return bgzf_payload_starts_with(source, MAGIC)

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

        return alignment_metadata(
            source,
            encoding_format=ENCODING_FORMAT,
            described_as=self.FORMAT_NAME,
            header=header,
            reference_count=reference_count,
            genomic_sample_ids=genomic_sample_ids,
            extra={"reference_count": reference_count},
        )

    def _read_header(self, source: FileSource, name: str):
        """The SAM header and the reference count, and nothing after them."""
        try:
            with open_bgzf(source) as payload:
                return self._read_payload(payload, name)
        except (*UNREADABLE, struct.error) as exc:
            raise ValueError(f"Failed to read BAM file {name}: {exc}") from exc

    def _read_payload(self, payload: BinaryIO, name: str):
        magic = _read_exactly(payload, len(MAGIC), "the magic", name)
        if magic != MAGIC:
            raise ValueError(
                f"Not a BAM file: {name} does not carry the BAM magic at the "
                "start of its payload"
            )
        text_length = _int32(payload, "l_text", name)
        # A length the file chooses, so checked against the shared cap before
        # the read, not after: the point is not to read it.
        if not 0 <= text_length <= MAX_HEADER_BYTES:
            raise ValueError(
                f"Not a BAM file: {name} declares a SAM header of "
                f"{text_length} bytes, outside the 0 to {MAX_HEADER_BYTES} a "
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
