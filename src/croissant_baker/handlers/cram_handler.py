"""CRAM handler: the SAM header of a reference-compressed alignment file.

A CRAM holds the same alignment a BAM does, encoded against the reference the
reads were placed on instead of storing their bases. That reference is not
needed here: the SAM text header sits in the first block of the first
container, and it is the only thing this handler reads, so a CRAM whose
reference is a URL nobody can reach is still described in full.

Getting to that block is the whole of the work. A container header is a run of
variable-width integers, so the block cannot be seeked to, only walked to, and
the widths change between CRAM 2 and CRAM 3.

No RecordSet, for the reason BAM emits none: aligned reads are records of a
genome, not of a dataset schema. What this handler produces is a described
FileObject, through the ``description`` key the generator honours.
"""

import bz2
import lzma
import struct
import zlib
from typing import BinaryIO, Tuple

from croissant_baker.handlers.base_handler import BuildResult, FileTypeHandler
from croissant_baker.handlers.sam_header import (
    SamHeader,
    describe_alignment,
    parse_sam_header,
)
from croissant_baker.handlers.utils import MAX_HEADER_BYTES, read_exactly
from croissant_baker.sources import FileSource

#: The four bytes a CRAM file definition opens with, and the whole claim. A
#: CRAM is not wrapped at file level, so unlike BAM's there is nothing in front
#: of it.
MAGIC = b"CRAM"

#: CRAM has no IANA registration. The ``x-`` form follows ``application/x-bam``,
#: already in the tree.
ENCODING_FORMAT = "application/x-cram"

#: Magic, major version, minor version, and a 20-byte file id.
FILE_ID_BYTES = 20

#: The major versions this handler walks. 1 is obsolete and lays its container
#: header out differently again; 4 changes the integer encodings the header is
#: written in, so a 2-and-3 reader walking one would find fields where there
#: are none.
READABLE_MAJOR_VERSIONS = (2, 3)

#: The block content type the SAM header is carried in.
FILE_HEADER = 0

#: The method number of a block written as it stands, with nothing to decode.
RAW = 0

#: How many bits of window ``zlib`` is given for a method 1 block: fifteen,
#: plus the thirty-two that mean "gzip or zlib header, whichever this is". The
#: same argument htslib inflates one with, and the reason both spellings are
#: read: CRAM's method 1 names the deflate family, not the gzip wrapper, and a
#: reader taking only gzip refuses files the reference implementation reads.
GZIP_OR_ZLIB_WINDOW = 15 + 32

#: The block compression methods with a decoder in the standard library, by the
#: method number a block declares, each as the incremental decompressor it is
#: read through. Incremental because a decoder handed a whole stream expands
#: all of it, and what a block declares it holds is the bound this handler
#: reads to. 4 is rANS, CRAM's own entropy coder, which has no stdlib decoder
#: and is refused by name.
DECOMPRESSORS = {
    1: lambda: zlib.decompressobj(GZIP_OR_ZLIB_WINDOW),
    2: bz2.BZ2Decompressor,
    3: lzma.LZMADecompressor,
}

#: What to call each method, so a refusal says which one was being read.
CODEC_NAMES = {RAW: "raw", 1: "gzip", 2: "bzip2", 3: "lzma", 4: "rANS"}

#: Every 32-bit field a container states is little-endian and signed.
INT32 = "<i"
INT32_BYTES = 4

#: The most landmarks a container header may declare before this handler stops
#: believing it. A landmark is the offset of one slice and a container holds
#: tens of them; a count in the millions is a corrupt header pointing the read
#: at the rest of the file.
MAX_LANDMARKS = 100_000


def _read_exactly(stream: BinaryIO, count: int, what: str, name: str) -> bytes:
    """``count`` bytes, or a refusal naming the file and what was missing."""
    return read_exactly(stream, count, what, name, "CRAM")


def _byte(stream: BinaryIO, what: str, name: str) -> int:
    return _read_exactly(stream, 1, what, name)[0]


def _leading_ones(byte: int, limit: int) -> int:
    """How many one-bits the byte opens with, counting no further than ``limit``."""
    count = 0
    while count < limit and byte & (0x80 >> count):
        count += 1
    return count


def _signed(value: int, width: int) -> int:
    """``value`` read back as the signed integer of ``width`` bits it encodes."""
    return value - (1 << width) if value & (1 << (width - 1)) else value


def _read_itf8(stream: BinaryIO, what: str, name: str) -> int:
    """One ITF8: the variable-width signed 32-bit integer CRAM counts with.

    The leading one-bits of the first byte say how many bytes follow, and what
    is left of that byte holds the number's most significant bits. The widest
    form is the exception: there the first byte carries four bits at the top
    and the last carries only its own low four, so the five hold exactly 32.
    """
    first = _byte(stream, what, name)
    extra = _leading_ones(first, 4)
    if extra == 4:
        tail = _read_exactly(stream, 4, what, name)
        value = (
            ((first & 0x0F) << 28)
            | (tail[0] << 20)
            | (tail[1] << 12)
            | (tail[2] << 4)
            | (tail[3] & 0x0F)
        )
    else:
        tail = _read_exactly(stream, extra, what, name)
        value = ((first & (0xFF >> (extra + 1))) << (8 * extra)) | int.from_bytes(
            tail, "big"
        )
    return _signed(value, 32)


def _read_ltf8(stream: BinaryIO, what: str, name: str) -> int:
    """One LTF8: the same encoding widened to 64 bits.

    Regular where ITF8 is not, because it has a byte to spare: a first byte of
    all ones means the number is the whole of the eight that follow.
    """
    first = _byte(stream, what, name)
    extra = _leading_ones(first, 8)
    tail = _read_exactly(stream, extra, what, name)
    value = int.from_bytes(tail, "big")
    if extra < 8:
        value |= (first & (0xFF >> (extra + 1))) << (8 * extra)
    return _signed(value, 64)


def _decode_block(method: int, data: bytes, raw_size: int, name: str) -> bytes:
    """What a block holds, expanded no further than it says it holds.

    Bounded by the size the block declares rather than by the cap on it: a
    block may state a raw size of a hundred bytes and carry a stream of
    hundreds of megabytes, and a decoder handed the whole stream expands all of
    it before the two can be compared. One byte past the declared size is
    enough to see that it holds more, and no more than that is decoded.
    """
    if method == RAW:
        content = data
    else:
        decompressor = DECOMPRESSORS[method]()
        content = decompressor.decompress(data, max_length=raw_size + 1)
        if len(content) <= raw_size and not decompressor.eof:
            raise ValueError(
                f"Truncated CRAM header in {name}: its file header block ends "
                f"before the end of the {CODEC_NAMES[method]} stream it declares"
            )
    if len(content) > raw_size:
        raise ValueError(
            f"Not a readable CRAM file: the file header block of {name} "
            f"declares {raw_size} bytes of content and holds more, so it was "
            "refused rather than expanded to find out how much more"
        )
    if len(content) != raw_size:
        raise ValueError(
            f"Not a readable CRAM file: the file header block of {name} "
            f"declares {raw_size} bytes of content and holds {len(content)}"
        )
    return content


def _bounded(size: int, what: str, name: str) -> int:
    """A declared size, checked before a byte behind it is read."""
    if not 0 <= size <= MAX_HEADER_BYTES:
        raise ValueError(
            f"Not a readable CRAM file: {name} declares a {what} of {size} "
            f"bytes, outside the 0 to {MAX_HEADER_BYTES} a header block can be"
        )
    return size


class CRAMHandler(FileTypeHandler):
    """Handler for CRAM alignment files (``.cram``).

    Reads the file definition, walks the first container header, decodes the
    file header block behind it, and stops. No slice, no record and no
    reference sequence is ever touched.

    No RecordSet is emitted. The output is the FileObject the generator builds,
    carrying the description this handler wrote.
    """

    EXTENSIONS = (".cram",)
    FORMAT_NAME = "CRAM"
    FORMAT_DESCRIPTION = (
        "CRAM version, sort order, reference count and assembly, read groups, "
        "program chain"
    )

    def claims(self, source: FileSource) -> bool:
        """Claim a stream opening with the CRAM magic.

        On the magic alone, as BAM is claimed: the extension says nothing the
        first four bytes do not.

        A file that cannot be read peeks as ``b""`` and is therefore not
        claimed, corrupt wrappers included; that is
        :meth:`~croissant_baker.sources.FileSource.peek`'s contract, and no
        handler repeats it.
        """
        return source.peek(len(MAGIC)) == MAGIC

    def extract(
        self, source: FileSource, genomic_sample_ids: bool = False, **kwargs
    ) -> dict:
        """Read one CRAM header, stopping at the end of the first block.

        Args:
            source: The file, already decompressed.
            genomic_sample_ids: If True, emit the ``@RG SM`` sample tags. Off
                by default: together they are a manifest of the cohort, and the
                read-group count answers the structural question without
                publishing one.
        """
        if not source.exists:
            raise FileNotFoundError(f"CRAM file not found: {source.relative_path}")

        name = str(source.relative_path)
        major, minor, header = self._read_header(source, name)
        version = f"{major}.{minor}"

        metadata = {
            "file_name": source.name,
            "file_size": source.size,
            "sha256": source.sha256,
            "encoding_format": ENCODING_FORMAT,
            "cram_version": version,
            "sam_version": header.sam_version,
            "sort_order": header.sort_order,
            "sq_count": header.sq_count,
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
        # The reference count is the number of @SQ lines and nothing else: a
        # CRAM states no second count for them the way a BAM's n_ref does.
        metadata["description"] = describe_alignment(
            f"{self.FORMAT_NAME} {version}",
            header,
            header.sq_count,
            source.name,
            sample_ids,
        )
        return metadata

    def _read_header(self, source: FileSource, name: str) -> Tuple[int, int, SamHeader]:
        """The version and the SAM header, and nothing after the first block."""
        try:
            with source.open() as stream:
                major, minor = self._read_file_definition(stream, name)
                self._walk_container_header(stream, major, name)
                text = self._read_file_header_block(stream, major, name)
                return major, minor, parse_sam_header(text)
        except (OSError, EOFError, lzma.LZMAError, zlib.error, struct.error) as exc:
            raise ValueError(f"Failed to read CRAM file {name}: {exc}") from exc

    def _read_file_definition(self, stream: BinaryIO, name: str) -> Tuple[int, int]:
        """The 26 bytes a CRAM opens with: magic, version, file id."""
        magic = _read_exactly(stream, len(MAGIC), "the magic", name)
        if magic != MAGIC:
            raise ValueError(
                f"Not a CRAM file: {name} does not open with the CRAM magic"
            )
        major = _byte(stream, "the major version", name)
        minor = _byte(stream, "the minor version", name)
        # Read past rather than described: the file id is whatever string the
        # writer padded to 20 bytes, and it names nothing a consumer resolves.
        _read_exactly(stream, FILE_ID_BYTES, "the file id", name)
        if major not in READABLE_MAJOR_VERSIONS:
            raise ValueError(
                f"Unsupported CRAM version in {name}: major version {major}. "
                "This handler reads major versions "
                + " and ".join(str(v) for v in READABLE_MAJOR_VERSIONS)
                + "; 1 is obsolete and 4 changes the integer encodings a "
                "container header is written in, so neither can be walked "
                "with this layout"
            )
        return major, minor

    def _walk_container_header(self, stream: BinaryIO, major: int, name: str) -> None:
        """Step over the first container's header to reach the block behind it.

        Nothing in it is described: every field says where the slices are, and
        the only thing this handler wants is the block. They are decoded rather
        than skipped because each is variable width, so the block cannot be
        found any other way.
        """
        _read_exactly(stream, INT32_BYTES, "the container length", name)
        for what in (
            "the reference sequence id",
            "the alignment start",
            "the alignment span",
            "the record count",
        ):
            _read_itf8(stream, what, name)
        # The one field whose width the version decides.
        if major >= 3:
            _read_ltf8(stream, "the record counter", name)
        else:
            _read_itf8(stream, "the record counter", name)
        _read_ltf8(stream, "the base count", name)
        _read_itf8(stream, "the block count", name)

        landmarks = _read_itf8(stream, "the landmark count", name)
        if not 0 <= landmarks <= MAX_LANDMARKS:
            raise ValueError(
                f"Not a readable CRAM file: the first container of {name} "
                f"declares {landmarks} landmarks, outside the 0 to "
                f"{MAX_LANDMARKS} a container holds"
            )
        for _ in range(landmarks):
            _read_itf8(stream, "a landmark", name)

        if major >= 3:
            self._read_crc(stream, "the container header CRC", name)

    def _read_file_header_block(self, stream: BinaryIO, major: int, name: str) -> str:
        """The SAM text of the first block, decoded and unpadded."""
        method = _byte(stream, "the block compression method", name)
        content_type = _byte(stream, "the block content type", name)
        if content_type != FILE_HEADER:
            raise ValueError(
                f"Not a readable CRAM file: the first block of {name} is "
                f"content type {content_type}, not the {FILE_HEADER} a file "
                "header block declares"
            )
        if method != RAW and method not in DECOMPRESSORS:
            codec = CODEC_NAMES.get(method, f"method {method}")
            raise ValueError(
                f"Cannot read the CRAM header of {name}: its file header block "
                f"is written with {codec}, a codec this handler does not decode"
            )

        _read_itf8(stream, "the block content id", name)
        compressed = _bounded(
            _read_itf8(stream, "the compressed block size", name),
            "compressed block size",
            name,
        )
        raw_size = _bounded(
            _read_itf8(stream, "the raw block size", name), "raw block size", name
        )
        content = _decode_block(
            method,
            _read_exactly(stream, compressed, "the header block", name),
            raw_size,
            name,
        )

        if major >= 3:
            self._read_crc(stream, "the block CRC", name)
        return self._header_text(content, name)

    def _read_crc(self, stream: BinaryIO, what: str, name: str) -> None:
        """Step over a CRC32 without checking it.

        Read past rather than verified: what this handler describes is the
        header text, and a CRC mismatch is a decoder's corruption report, not
        metadata. Refusing a header that is otherwise readable would report the
        wrong thing about the file.
        """
        _read_exactly(stream, INT32_BYTES, what, name)

    def _header_text(self, content: bytes, name: str) -> str:
        """The SAM text a decoded file header block holds.

        The block states the text length itself, and may be written longer than
        the text so that a later reheader fits in place; the NULs filling it
        are padding, not header.

        Decoded permissively, as the BAM header is: a stray byte in a program
        name is not a reason to refuse a file whose structure is readable.
        """
        if len(content) < INT32_BYTES:
            raise ValueError(
                f"Truncated CRAM header in {name}: its file header block holds "
                f"{len(content)} bytes, too few to state the length of the "
                "header text"
            )
        length = _bounded(
            struct.unpack(INT32, content[:INT32_BYTES])[0], "header text length", name
        )
        text = content[INT32_BYTES : INT32_BYTES + length]
        if len(text) != length:
            raise ValueError(
                f"Truncated CRAM header in {name}: its file header block "
                f"states {length} bytes of header text and holds {len(text)}"
            )
        return text.rstrip(b"\x00").decode("utf-8", "replace")

    def build_croissant(self, file_metas: list, file_ids: list) -> tuple:
        """Nothing: a CRAM is described as a file, by the description it carries.

        Aligned reads are records of a genome, not of a dataset schema, and a
        RecordSet naming columns no consumer can read through Croissant would
        be a promise nobody can keep.
        """
        return BuildResult([], [])
