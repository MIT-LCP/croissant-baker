"""FASTA handler: that the file is a FASTA, and nothing about what it holds.

A FASTA is a description line and then bases, repeated. The description line is
the only thing in the file that says what format it is in, so it is read; its
content is a name a depositor chose, and for a per-sample assembly that name is
the sample, so the name is not emitted. The bases below it are never touched:
a reference genome is gigabytes of them and none of them is metadata.

That leaves format and encoding, which is what this handler reports. No
RecordSet: bases are not records of a dataset schema. What it produces is a
described FileObject, through the ``description`` key the generator honours.
"""

from croissant_baker.handlers.base_handler import BuildResult, FileTypeHandler
from croissant_baker.sources import UNREADABLE, FileSource

#: The character a record description line opens with, and the whole of what
#: distinguishes a FASTA from any other text file.
RECORD_MARKER = ">"

#: FASTA has no IANA registration. The ``x-`` form follows ``text/x-vcf``,
#: already in the tree.
ENCODING_FORMAT = "text/x-fasta"

#: How much of the head is pulled to reach the end of the first line. Bounded
#: rather than a plain ``readline``, which on a file holding no newline at all
#: reads the whole of it, and reading the whole of it is the one thing this
#: handler exists not to do. Nothing past the first newline is looked at, and a
#: description line longer than this is still recognised by its opening bytes,
#: which is all that is asked of it.
HEAD_BYTES = 4096


class FASTAHandler(FileTypeHandler):
    """Handler for FASTA sequence files (``.fa``, ``.fasta``, ``.fna``).

    One line is read and then discarded. The output is the FileObject the
    generator builds, carrying the description this handler wrote: the file is
    a FASTA, and that is the claim it makes.
    """

    EXTENSIONS = (".fa", ".fasta", ".fna")
    FORMAT_NAME = "FASTA"
    FORMAT_DESCRIPTION = "Format and encoding; record names and sequences are not read"

    def claims(self, source: FileSource) -> bool:
        """Claim a declared FASTA extension whose first byte opens a record.

        Both halves are needed, and neither would do on its own. ``>`` is a
        single character that a quoted email, a shell transcript and a diff all
        begin with, which is too little to own a file on. The extension alone
        would claim any text a user happened to name ``.fa``, and the format
        has no other marker to fall back on: past that first byte a FASTA is
        letters, which is what an unrelated text file is too.

        A file that cannot be read peeks as ``b""`` and is therefore not
        claimed, corrupt wrappers included; that is
        :meth:`~croissant_baker.sources.FileSource.peek`'s contract, and no
        handler repeats it.
        """
        if source.suffix not in self.EXTENSIONS:
            return False
        return source.peek(len(RECORD_MARKER)) == RECORD_MARKER.encode()

    def extract(self, source: FileSource, **kwargs) -> dict:
        """Read the first record's description line, and stop there.

        The line is read to confirm the format declares a record and is then
        forgotten: nothing it carries reaches the metadata. What is emitted is
        the file's own identity, which the bytes on disk state.
        """
        if not source.exists:
            raise FileNotFoundError(
                f"{self.FORMAT_NAME} file not found: {source.relative_path}"
            )

        self._read_first_line(source)

        return {
            "file_name": source.name,
            "file_size": source.size,
            "sha256": source.sha256,
            "encoding_format": ENCODING_FORMAT,
            # The one thing this handler emits, built from the logical name,
            # which is the only name extraction is given.
            "description": (
                f"{self.FORMAT_NAME} sequence file {source.name}. Described "
                "from its first record line; record names and sequences are "
                "not read."
            ),
        }

    def _read_first_line(self, source: FileSource) -> None:
        """Refuse a head that does not open a record, naming the file and why.

        Decoded permissively: a description line is ASCII by convention and
        nothing here is emitted, so a stray byte in a comment is not a reason
        to refuse a file whose format is otherwise stated.
        """
        name = source.relative_path
        try:
            with source.open() as stream:
                head = stream.read(HEAD_BYTES)
        except UNREADABLE as exc:
            raise ValueError(
                f"Failed to read {self.FORMAT_NAME} file {name}: {exc}"
            ) from exc

        if not head:
            raise ValueError(
                f"Not a {self.FORMAT_NAME} file: {name} is empty, so it "
                "declares no record"
            )
        line = head.decode("utf-8", "replace").split("\n", 1)[0].rstrip("\r")
        if not line.startswith(RECORD_MARKER):
            raise ValueError(
                f"Not a {self.FORMAT_NAME} file: {name} does not open with the "
                f"'{RECORD_MARKER}' that starts a record description line"
            )
        if not line[len(RECORD_MARKER) :].strip():
            raise ValueError(
                f"Incomplete {self.FORMAT_NAME} header in {name}: the first "
                f"line is a bare '{RECORD_MARKER}', which names no record"
            )

    def build_croissant(self, file_metas: list, file_ids: list) -> tuple:
        """Nothing: a FASTA is described as a file, by the description it carries.

        Bases are records of a genome, not of a dataset schema, and a RecordSet
        naming fields no consumer can read through Croissant would be a promise
        nobody can keep. The same reasoning as BAM.
        """
        return BuildResult([], [])
