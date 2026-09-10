"""FASTQ handler: the first read, and nothing the file says about anyone.

A FASTQ is the same four lines repeated until the run is exhausted, and those
four lines carry no schema the rest of the file does not already repeat. The
one structural fact worth stating is how long a read is, and the first record
states it, so the first record is what is read and the file behind it is not.

What the record also carries is a read name, and on an Illumina run that name
is instrument, run and flowcell identifiers spelled out. None of that is
structure, so none of it is emitted: not in the metadata, and not in the
description either.

No RecordSet: sequencing reads are records of a run, not of a dataset schema.
What this handler produces is a described FileObject, through the
``description`` key the generator honours, exactly as the BAM handler does.
"""

import itertools
from typing import List

from croissant_baker.handlers.base_handler import BuildResult, FileTypeHandler
from croissant_baker.handlers.utils import plural
from croissant_baker.sources import FileSource

#: FASTQ has no IANA registration. The ``x-`` form follows ``text/x-vcf`` and
#: ``text/x-geo-soft``, already in the tree.
ENCODING_FORMAT = "text/x-fastq"

#: The four lines of one record: name, sequence, separator, qualities.
RECORD_LINES = 4

#: The character a record's name line opens with, and a separator line's own.
NAME_PREFIX = "@"
SEPARATOR_PREFIX = "+"

#: Enough of the head to see the first record's shape. A read is tens to a few
#: hundred bases and a name is shorter still, so three lines of any real record
#: fit many times over; a peek that stops short of the third line simply leaves
#: the decision to the extension, and ``extract`` reports what it then finds.
CLAIM_BYTES = 4096


class FASTQHandler(FileTypeHandler):
    """Handler for FASTQ sequencing read files (``.fastq``, ``.fq``).

    The first record is read and the rest of the file is not, so a run of a
    hundred million reads costs the same read as a run of one.

    No RecordSet is emitted. The output is the FileObject the generator builds,
    carrying the description this handler wrote.
    """

    EXTENSIONS = (".fastq", ".fq")
    FORMAT_NAME = "FASTQ"
    ENCODING_FORMAT = ENCODING_FORMAT
    FORMAT_DESCRIPTION = "Read length of the first record; no read name or record count"

    def claims(self, source: FileSource) -> bool:
        """Claim a file named like a FASTQ whose head is shaped like one.

        Both conditions, because neither survives on its own. ``@`` is how a
        record's name line opens, and also how every line of a SAM header
        opens, so the first byte cannot decide alone; the extension cannot
        either, because it would claim any text a user happened to name
        ``.fq``. Together they are the record's own shape: a name line, then a
        sequence, then a separator on the third line.

        A file that cannot be read peeks as ``b""`` and is therefore not
        claimed; that is
        :meth:`~croissant_baker.sources.FileSource.peek`'s contract, and no
        handler repeats it.
        """
        if source.suffix not in self.EXTENSIONS:
            return False
        head = source.peek(CLAIM_BYTES)
        if not head.startswith(NAME_PREFIX.encode()):
            return False
        lines = head.splitlines()
        if len(lines) < 3:
            return True
        return lines[2].startswith(SEPARATOR_PREFIX.encode())

    def extract(self, source: FileSource, **kwargs) -> dict:
        """Read the first record of a FASTQ, and stop there.

        Args:
            source: The file, already decompressed.
        """
        if not source.exists:
            raise FileNotFoundError(
                f"{self.FORMAT_NAME} file not found: {source.relative_path}"
            )

        name = str(source.relative_path)
        lines = self._read_first_record(source, name)
        read_length = self._first_read_length(lines, name)

        return {
            "file_name": source.name,
            "file_size": source.size,
            "sha256": source.sha256,
            "encoding_format": self.ENCODING_FORMAT,
            "first_read_length": read_length,
            # The one thing this handler emits. Built here rather than in
            # build_croissant, which runs after the FileObject is staged, and
            # from the logical name, which is the only one extraction is given.
            "description": (
                f"{self.FORMAT_NAME} sequencing reads {source.name} "
                f"(first read {plural(read_length, 'base')}). Described from "
                "its first record; no other record was read, and read names "
                "are not reported."
            ),
        }

    def _read_first_record(self, source: FileSource, name: str) -> List[str]:
        """The first four lines, decoded and stripped of their endings.

        Permissively decoded: a stray byte in a read name is not a reason to
        refuse a file whose structure is otherwise readable, and the name is
        the one part of the record nothing is emitted from anyway.
        """
        try:
            with source.open() as stream:
                raw = list(itertools.islice(stream, RECORD_LINES))
        except OSError as exc:
            raise ValueError(
                f"Failed to read {self.FORMAT_NAME} file {name}: {exc}"
            ) from exc
        return [line.decode("utf-8", "replace").rstrip("\r\n") for line in raw]

    def _first_read_length(self, lines: List[str], name: str) -> int:
        """The length of the first read, or a refusal saying what is wrong.

        Every refusal here is a record this handler cannot describe truthfully,
        and a wrong read length is worse than none: the file is reported with
        the reason instead.
        """
        if not lines:
            raise ValueError(
                f"Empty {self.FORMAT_NAME} file: {name} holds no record to describe"
            )
        if not lines[0].startswith(NAME_PREFIX):
            raise ValueError(
                f"Not a {self.FORMAT_NAME} file: the first line of {name} does "
                f"not open with '{NAME_PREFIX}'"
            )
        if len(lines) < 3 or not lines[2].startswith(SEPARATOR_PREFIX):
            raise ValueError(
                f"Malformed {self.FORMAT_NAME} record in {name}: the third line "
                f"does not open with '{SEPARATOR_PREFIX}', so the first record "
                "does not fit the four lines this handler reads"
            )
        sequence = lines[1]
        if len(lines) < RECORD_LINES:
            raise ValueError(
                f"Malformed {self.FORMAT_NAME} record in {name}: the first read "
                "ends before its quality line, so it is not a record to describe"
            )
        if len(lines[3]) != len(sequence):
            raise ValueError(
                f"Malformed {self.FORMAT_NAME} record in {name}: the first read "
                f"is {len(sequence)} bases and carries {len(lines[3])} quality "
                "scores"
            )
        return len(sequence)

    def build_croissant(self, file_metas: list, file_ids: list) -> tuple:
        """Nothing: a FASTQ is described as a file, by the description it carries.

        Sequencing reads are records of a run, not of a dataset schema, and a
        RecordSet naming columns no consumer can read through Croissant would
        be a promise nobody can keep.
        """
        return BuildResult([], [])
