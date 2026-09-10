"""SAM handler: the header of a text alignment file, and no alignment record.

A SAM is the text spelling of what a BAM holds in binary, and it opens with the
same header the aligner wrote: which assembly the reads were placed against,
which platform and centre produced them, and which programs touched them, in
order. That is provenance the file states about itself, so it is read and the
rest of the file is not.

Unlike a BAM, nothing in front of the header says how long it is. What bounds
the read is the stop at the first line that is not a header line, so the cost is
the size of the header rather than the size of the file. A file that never
reaches such a line must still stop somewhere, so the header is taken a chunk
at a time and both a single line and the header as a whole are capped.

No RecordSet: aligned reads are not records of a dataset schema. What this
handler produces is a described FileObject, through the ``description`` key the
generator honours.
"""

from typing import List

from croissant_baker.handlers.base_handler import BuildResult, FileTypeHandler
from croissant_baker.handlers.sam_header import describe_alignment, parse_sam_header
from croissant_baker.handlers.utils import MAX_HEADER_BYTES, read_prefix_chunks
from croissant_baker.sources import FileSource

#: SAM has no IANA registration. The ``x-`` form follows ``text/x-vcf`` and
#: ``text/x-geo-soft``, already in the tree.
ENCODING_FORMAT = "text/x-sam"

#: The character every header line opens with, and no alignment record does.
#: Bytes, because the header is read as bytes and decoded a line at a time.
HEADER_PREFIX = b"@"

#: The largest header this handler will accumulate, and the largest single line
#: inside it. The header cap is the one the containers carrying the same text
#: state their own length against; this format states none, so it is applied to
#: what has been read instead. The line cap is smaller because a header line is
#: a handful of tab-separated tags, the longest of which is a ``@PG`` command
#: line running to kilobytes: a megabyte with no line ending in it is a file
#: whose first line is not a header line at all, and reading further is reading
#: the alignment records.
MAX_LINE_BYTES = 1024 * 1024

#: The five record types a SAM header may declare, each with the tab that
#: separates the type from its first tag. The tab is half the claim: a FASTQ
#: read name is ``@`` followed by arbitrary text, and ``@HD`` is a plausible
#: enough read name that the three letters alone are not evidence.
HEADER_RECORDS = (b"@HD\t", b"@SQ\t", b"@RG\t", b"@PG\t", b"@CO\t")

#: Enough of the head to decide a claim: the longest of the above.
CLAIM_BYTES = max(len(record) for record in HEADER_RECORDS)


def _decode(line: bytes) -> str:
    """One header line as text, with the carriage return of a CRLF file gone."""
    return line.decode("utf-8", "replace").rstrip("\r")


class SAMHandler(FileTypeHandler):
    """Handler for SAM alignment files (``.sam``).

    Reads the ``@`` lines and stops at the first line that is not one. No
    alignment record is ever parsed, and no RecordSet is emitted: the output is
    the FileObject the generator builds, carrying the description this handler
    wrote.
    """

    EXTENSIONS = (".sam",)
    FORMAT_NAME = "SAM"
    FORMAT_DESCRIPTION = (
        "Sort order, reference count and assembly, read groups, program chain"
    )

    def claims(self, source: FileSource) -> bool:
        """Claim a ``.sam`` whose first line is a SAM header line.

        Both conditions, because neither is sufficient on its own. The
        extension is not, since a FASTQ also opens with ``@`` and a FASTQ named
        ``reads.sam`` would be described as an alignment it is not. The opening
        bytes are not either, because the SAM header is a shape other text
        formats can wear and the extension is what says this one is a SAM.

        A ``.sam`` carrying only alignment records therefore goes unclaimed.
        That is the honest outcome: without a header the file states no sort
        order, no assembly and no read group, and there is nothing left to
        describe that the record set this handler does not emit would carry.

        A file that cannot be read peeks as ``b""`` and is therefore not
        claimed; that is
        :meth:`~croissant_baker.sources.FileSource.peek`'s contract, and no
        handler repeats it.
        """
        if source.suffix not in self.EXTENSIONS:
            return False
        return source.peek(CLAIM_BYTES).startswith(HEADER_RECORDS)

    def extract(
        self, source: FileSource, genomic_sample_ids: bool = False, **kwargs
    ) -> dict:
        """Read one SAM header, stopping at the first alignment record.

        Args:
            source: The file, already decompressed.
            genomic_sample_ids: If True, emit the ``@RG SM`` sample tags. Off
                by default: together they are a manifest of the cohort, and the
                read-group count answers the structural question without
                publishing one.
        """
        if not source.exists:
            raise FileNotFoundError(f"SAM file not found: {source.relative_path}")

        name = str(source.relative_path)
        lines = self._read_header_lines(source, name)
        if not lines:
            raise ValueError(
                f"Not a SAM file: {name} opens with no '@' header line, so it "
                "declares no sort order, assembly or read group to describe"
            )
        header = parse_sam_header("\n".join(lines))

        metadata = {
            "file_name": source.name,
            "file_size": source.size,
            "sha256": source.sha256,
            "encoding_format": ENCODING_FORMAT,
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
        # The one thing this handler emits. Built here rather than in
        # build_croissant, which runs after the FileObject is staged, and from
        # the logical name, which is the only one extraction is given. The
        # ``@SQ`` count is the reference count: a SAM has no second, binary
        # spelling of it for the two to disagree over.
        metadata["description"] = describe_alignment(
            self.FORMAT_NAME, header, header.sq_count, source.name, sample_ids
        )
        return metadata

    def _read_header_lines(self, source: FileSource, name: str) -> List[str]:
        """Every line up to the first that is not a header line.

        Taken a chunk at a time rather than a line at a time: a stream iterated
        by line hands back the whole file as one line when the file holds no
        line ending, and reading the whole file is the one thing this handler
        exists not to do.

        Decoded permissively: a SAM header is printable ASCII by specification,
        and a stray byte in a ``@CO`` comment is not a reason to refuse a file
        whose structure is otherwise readable.
        """
        lines: List[str] = []
        pending = b""
        read = 0
        try:
            with source.open() as stream:
                for chunk in read_prefix_chunks(stream, MAX_HEADER_BYTES + 1):
                    read += len(chunk)
                    complete = (pending + chunk).split(b"\n")
                    # The tail after the last line ending is not yet a line.
                    pending = complete.pop()
                    for raw in complete:
                        if not raw.startswith(HEADER_PREFIX):
                            return lines
                        lines.append(_decode(raw))
                    self._still_a_header(len(pending), read, name)
                # End of file inside the header: what is left of it is the last
                # line, written without an ending.
                if pending.startswith(HEADER_PREFIX):
                    lines.append(_decode(pending))
        except OSError as exc:
            raise ValueError(f"Failed to read SAM file {name}: {exc}") from exc
        return lines

    def _still_a_header(self, line_bytes: int, header_bytes: int, name: str) -> None:
        """Refuse a read that has gone past what a header can be, saying which.

        Two caps rather than one: a header of a million references is legitimately
        tens of megabytes, and a single line of that size is not a header line.
        """
        if line_bytes > MAX_LINE_BYTES:
            raise ValueError(
                f"Not a SAM file: {name} runs to {line_bytes} bytes with no "
                f"line ending, past the {MAX_LINE_BYTES} a header line can be"
            )
        if header_bytes > MAX_HEADER_BYTES:
            raise ValueError(
                f"Not a SAM file: the header of {name} runs past "
                f"{MAX_HEADER_BYTES} bytes without reaching a line that is not "
                "a header line"
            )

    def build_croissant(self, file_metas: list, file_ids: list) -> tuple:
        """Nothing: a SAM is described as a file, by the description it carries.

        Aligned reads are records of a genome, not of a dataset schema, and a
        RecordSet naming columns no consumer can read through Croissant would
        be a promise nobody can keep.
        """
        return BuildResult([], [])
