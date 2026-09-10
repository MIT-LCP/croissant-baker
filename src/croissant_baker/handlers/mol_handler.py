"""MOL handler: the header of one connection table, and none of the table.

An MDL molfile holds a single molecule: a title, the layout the table is written
in, and how many atoms and bonds that table has. Those four facts are what the
file states about itself, and they sit in its first four lines, so that is what
is read. The atom block and the bond block below them are the molecule, not
metadata about it, and a screening deposit is millions of such blocks.

No RecordSet. A molfile is one molecule, so there is no second row for a record
set to hold, and a record set over a single record would state a schema the file
never declares. What this handler produces is a described FileObject, through
the ``description`` key the generator honours. The same reasoning as BAM.
"""

from typing import List, Tuple

from croissant_baker.handlers import molfile
from croissant_baker.handlers.base_handler import BuildResult, FileTypeHandler
from croissant_baker.handlers.utils import PrefixLines, plural
from croissant_baker.sources import UNREADABLE, FileSource

#: The chemical MIME family, which is not IANA-registered but is what chemistry
#: toolkits, journals and structure databases have served molfiles as for
#: decades. It is the media type a consumer of this metadata will recognise.
ENCODING_FORMAT = "chemical/x-mdl-molfile"

#: How much of the file is pulled to reach the counts. Four lines of 80
#: characters is the whole of a V2000 header; a V3000 header adds the ``COUNTS``
#: line and the block declaration above it, which is a few hundred bytes more.
#: 64 KiB is orders of magnitude above either and nothing next to a file, and
#: bounding the read is the point: a header whose counts do not arrive inside it
#: is reported rather than chased to the end of the file.
HEAD_BYTES = 64 * 1024


class MOLHandler(FileTypeHandler):
    """Handler for MDL molfiles (``.mol``).

    Reads the first four lines, and for a V3000 block the ``COUNTS`` line below
    them, then stops. No atom and no bond is ever read, and no RecordSet is
    emitted: the output is the FileObject the generator builds, carrying the
    description this handler wrote.
    """

    EXTENSIONS = (".mol",)
    FORMAT_NAME = "MOL"
    ENCODING_FORMAT = ENCODING_FORMAT
    FORMAT_DESCRIPTION = "Molfile version, title, atom and bond counts (header only)"

    def claims(self, source: FileSource) -> bool:
        """Claim a ``.mol`` whose fourth line declares a molfile version.

        Both halves, because neither would do alone. ``.mol`` is shared with
        several unrelated tools that write a save file under it, so the
        extension is not evidence on its own. The version literal is not either:
        it is five characters that a text file could carry anywhere, and what
        makes it a molfile declaration is being on the counts line, which is
        where the extension says to look.

        A file that cannot be read peeks as ``b""`` and is therefore not
        claimed, corrupt wrappers included; that is
        :meth:`~croissant_baker.sources.FileSource.peek`'s contract, and no
        handler repeats it.
        """
        if source.suffix not in self.EXTENSIONS:
            return False
        return molfile.head_declares_version(source.peek(molfile.CLAIM_BYTES))

    def extract(self, source: FileSource, **kwargs) -> dict:
        """Read one molfile header, stopping at the counts."""
        if not source.exists:
            raise FileNotFoundError(
                f"{self.FORMAT_NAME} file not found: {source.relative_path}"
            )

        header = self._read_header(source)

        return {
            "file_name": source.name,
            "file_size": source.size,
            "sha256": source.sha256,
            "encoding_format": self.ENCODING_FORMAT,
            "molfile_version": header.version,
            "title": header.title,
            "atom_count": header.atom_count,
            "bond_count": header.bond_count,
            # The one thing this handler emits into the document. Built here
            # rather than in build_croissant, which runs after the FileObject is
            # staged, and from the logical name, which is the only name
            # extraction is given.
            "description": _description(source.name, header),
        }

    # ------------------------------------------------------------------

    def _read_header(self, source: FileSource) -> molfile.MolfileHeader:
        """The header of the block, or a refusal naming the file and why."""
        name = str(source.relative_path)
        lines, truncated = self._read_lines(source, name)
        try:
            return molfile.parse_molfile_header(lines)
        except ValueError as exc:
            if truncated:
                raise ValueError(
                    f"Incomplete {self.FORMAT_NAME} header in {name}: the "
                    f"header does not complete within the first {HEAD_BYTES} "
                    f"bytes, which is as far as this handler reads ({exc})"
                ) from exc
            raise ValueError(f"Not a {self.FORMAT_NAME} file: {name} {exc}") from exc

    def _read_lines(self, source: FileSource, name: str) -> Tuple[List[str], bool]:
        """The lines of a bounded prefix, and whether the file ran past it.

        Read through :class:`~croissant_baker.handlers.utils.PrefixLines`,
        which is bounded in bytes and drops a tail the bound cut in half, since
        nothing may be read off half a line.
        """
        try:
            with source.open() as stream:
                reader = PrefixLines(stream, HEAD_BYTES)
                lines = list(reader)
        except UNREADABLE as exc:
            raise ValueError(
                f"Failed to read {self.FORMAT_NAME} file {name}: {exc}"
            ) from exc
        return lines, reader.bounded

    def build_croissant(self, file_metas: list, file_ids: list) -> tuple:
        """Nothing: a molfile is described as a file, by the description it carries.

        One molecule is a file, not a table. A record set needs rows that share
        a schema, and a molfile has exactly one of everything, so the schema and
        the record would be the same statement made twice.
        """
        return BuildResult([], [])


def _description(name: str, header: molfile.MolfileHeader) -> str:
    """What the header says about the molecule, in one deterministic sentence.

    Prose rather than new keys: the molfile version, the title and the two
    counts have no home in the Croissant or Schema.org vocabularies, and an
    invented JSON-LD key is one no consumer reads.
    """
    stated = [
        header.version,
        f"{plural(header.atom_count, 'atom')}, {plural(header.bond_count, 'bond')}",
    ]
    # Omitted rather than left empty: a molfile with a blank first line names no
    # molecule, and "title:" with nothing after it states less than silence.
    if header.title:
        stated.append(f"title: {header.title}")
    return (
        f"MDL molfile {name} ({'; '.join(stated)}). Described from its header; "
        "no atom or bond record was read."
    )
