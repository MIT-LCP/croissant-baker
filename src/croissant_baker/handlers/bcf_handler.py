"""BCF handler: the VCF header a callset declares, in its binary container.

A BCF is a VCF whose records are packed into a binary encoding, behind a magic
and a length, and whose header is the same text the plain file opens with. The
schema a consumer needs is therefore the same schema, and so is the record set:
this handler finds the text and hands it to the VCF header reader, and nothing
below it is ever decoded.
"""

import gzip
import struct
from typing import BinaryIO

from croissant_baker.handlers.utils import (
    MAX_HEADER_BYTES,
    decompress_prefix,
    read_exactly,
)
from croissant_baker.handlers.vcf_handler import (
    VCFHandler,
    _Header,
    read_header_lines,
)
from croissant_baker.sources import UNREADABLE, FileSource

#: The first four bytes of a BCF 2.x payload. The fifth is the minor version,
#: which says how the records are encoded and so says nothing about the header
#: this handler reads: 2.1 and 2.2 are both accepted.
MAGIC_PREFIX = b"BCF\x02"

#: Magic and minor version together.
MAGIC_BYTES = len(MAGIC_PREFIX) + 1

#: The two bytes every member of a gzip stream opens with. BCF is BGZF, which
#: is gzip with an extra field Python's gzip module ignores.
COMPRESSED_MAGIC = b"\x1f\x8b"

#: BCF has no IANA registration. The ``x-`` form follows ``application/x-bam``,
#: already in the tree.
ENCODING_FORMAT = "application/x-bcf"

#: Enough of the head to decide a claim: one BGZF block is at most 64 KiB, and
#: the magic is the first five bytes of the first block's payload.
CLAIM_BYTES = 4096

#: ``l_text`` is a little-endian unsigned 32-bit integer.
UINT32 = "<I"
UINT32_BYTES = 4

#: The largest header text this handler will read. ``l_text`` is a length the
#: file chooses, so trusting it turns a header read into a read of the whole
#: file, which is the one thing this handler exists not to do. The cap is the
#: shared one, because every container in this family states its own header
#: length and none of them may be believed about it.
MAX_TEXT_BYTES = MAX_HEADER_BYTES


def _read_exactly(stream: BinaryIO, count: int, what: str, name: str) -> bytes:
    """``count`` bytes, or a refusal naming the file and what was missing."""
    return read_exactly(stream, count, what, name, "BCF")


class BCFHandler(VCFHandler):
    """Handler for BCF variant call files (``.bcf``).

    The compression layer does not strip ``.bcf``, so this handler is given the
    bytes as they sit on disk and decompresses them itself. What it reads is
    the magic, the declared header length and the header text; the record block
    behind it is never touched.

    Everything after the text is the VCF handler's: the columns the ``#CHROM``
    line declares, the ``INFO`` and ``FORMAT`` keys, the sample count and the
    gVCF flag, so one callset describes the same way in either container.
    """

    EXTENSIONS = (".bcf",)
    FORMAT_NAME = "BCF"
    ENCODING_FORMAT = ENCODING_FORMAT
    # The same header, so the same list of what is read out of it.
    FORMAT_DESCRIPTION = VCFHandler.FORMAT_DESCRIPTION

    def claims(self, source: FileSource) -> bool:
        """Claim a stream whose payload opens with the BCF magic.

        Two spellings, because the pipeline can hand over either. A ``.bcf`` on
        disk is compressed and reaches this handler as it is stored, so the
        magic is inside the wrapper. A ``.bcf`` that arrived under a second
        wrapper has had one layer taken off already, and the magic is the first
        thing in the stream.

        A file that cannot be read peeks as ``b""`` and is therefore not
        claimed; that is
        :meth:`~croissant_baker.sources.FileSource.peek`'s contract. The prefix
        this handler decompresses itself is its own to guard, and the types are
        the ones a refused or corrupt member raises.
        """
        head = source.peek(CLAIM_BYTES)
        if head.startswith(MAGIC_PREFIX):
            return True
        if not head.startswith(COMPRESSED_MAGIC):
            return False
        try:
            return decompress_prefix(head, len(MAGIC_PREFIX)) == MAGIC_PREFIX
        except UNREADABLE:
            return False

    def _read_header(self, source: FileSource) -> _Header:
        """The header text the container declares, and nothing after it.

        The bytes around the text are all this handler adds to the VCF one: a
        magic that says which generation of the format wrote them, and a length
        that says where the text ends.
        """
        name = str(source.relative_path)
        try:
            compressed = source.peek(len(COMPRESSED_MAGIC)) == COMPRESSED_MAGIC
            with source.open() as stored:
                if not compressed:
                    return self._read_payload(stored, name)
                with gzip.GzipFile(fileobj=stored, mode="rb") as payload:
                    return self._read_payload(payload, name)
        # A corrupt member raises its decompression library's own type, which
        # is not an OSError, and a file is owed a reason either way.
        except UNREADABLE as exc:
            raise ValueError(f"Failed to read BCF file {name}: {exc}") from exc

    def _read_payload(self, payload: BinaryIO, name: str) -> _Header:
        magic = _read_exactly(payload, MAGIC_BYTES, "the magic", name)
        if not magic.startswith(MAGIC_PREFIX):
            raise ValueError(
                f"Not a BCF file: {name} does not carry the BCF 2 magic at the "
                "start of its payload. BCF1 is samtools' own encoding and "
                "declares no VCF header text"
            )
        text_length = struct.unpack(
            UINT32, _read_exactly(payload, UINT32_BYTES, "l_text", name)
        )[0]
        # Checked before the read, not after: the point is not to read it.
        if text_length > MAX_TEXT_BYTES:
            raise ValueError(
                f"Not a BCF file: {name} declares a header of {text_length} "
                f"bytes, above the {MAX_TEXT_BYTES} a header can be"
            )
        text = _read_exactly(payload, text_length, "the header text", name)
        # NUL-terminated, and writers pad with more of them. Stripped rather
        # than split on, so a padded header reads as the text it holds.
        decoded = text.decode("utf-8", "replace").rstrip("\x00")
        return read_header_lines(decoded.splitlines())
