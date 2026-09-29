"""BCF handler: the VCF header a callset declares, in its binary container.

A BCF is a VCF whose records are packed into a binary encoding, behind a magic
and a length, and whose header is the same text the plain file opens with. The
schema a consumer needs is therefore the same schema, and so is the record set:
this handler finds the text and hands it to the VCF header reader, and nothing
below it is ever decoded.
"""

import struct
from typing import BinaryIO

from croissant_baker.handlers.utils import (
    MAX_HEADER_BYTES,
    bgzf_payload_starts_with,
    open_bgzf,
    read_exactly,
)
from croissant_baker.handlers.vcf_handler import (
    VCFHandler,
    _Header,
    read_header_lines,
)
from croissant_baker.sources import UNREADABLE, FileSource

#: The three bytes every generation of BCF opens with, and the whole claim. The
#: generation is the byte behind them, and a file of the wrong one is claimed so
#: that it can be reported as the BCF it is rather than as a file nothing
#: recognised.
MAGIC_PREFIX = b"BCF"

#: The four bytes of a BCF 2.x payload. The fifth is the minor version, which
#: says how the records are encoded and so says nothing about the header this
#: handler reads: 2.1 and 2.2 are both accepted.
BCF2_MAGIC = MAGIC_PREFIX + b"\x02"

#: Magic and minor version together.
MAGIC_BYTES = len(BCF2_MAGIC) + 1

#: BCF has no IANA registration. The ``x-`` form follows ``application/x-bam``,
#: already in the tree.
ENCODING_FORMAT = "application/x-bcf"

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

        Inside its BGZF wrapper as a ``.bcf`` is stored, or already unwrapped
        when it arrived under a second wrapper; see
        :func:`~croissant_baker.handlers.utils.bgzf_payload_starts_with`.

        On the three bytes every generation shares, not on the generation this
        handler reads: a BCF1 claimed here is reported as a BCF whose header
        cannot be read, and one left unclaimed is reported as a file nothing
        recognised, which says less about it than is known.
        """
        return bgzf_payload_starts_with(source, MAGIC_PREFIX)

    def _read_header(self, source: FileSource) -> _Header:
        """The header text the container declares, and nothing after it.

        The bytes around the text are all this handler adds to the VCF one: a
        magic that says which generation of the format wrote them, and a length
        that says where the text ends.
        """
        name = str(source.relative_path)
        try:
            with open_bgzf(source) as payload:
                return self._read_payload(payload, name)
        # A corrupt member raises its decompression library's own type, which
        # is not an OSError, and a file is owed a reason either way.
        except UNREADABLE as exc:
            raise ValueError(f"Failed to read BCF file {name}: {exc}") from exc

    def _read_payload(self, payload: BinaryIO, name: str) -> _Header:
        magic = _read_exactly(payload, MAGIC_BYTES, "the magic", name)
        if not magic.startswith(BCF2_MAGIC):
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
