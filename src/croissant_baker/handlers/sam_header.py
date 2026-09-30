"""The SAM text header, shared by every alignment container that carries one.

BAM, CRAM and SAM itself all open with the same text: ``@HD`` for the sort
order, ``@SQ`` per reference sequence, ``@RG`` per read group and ``@PG`` per
program that touched the file. Reading it is the same job whichever container
it sits in, so it is done here, once, and each handler only has to find it.
The metadata and description each alignment handler returns are built here
too, from that header, so the three containers describe a file the same way.
"""

from typing import Dict, List, Optional

from croissant_baker.handlers.utils import plural
from croissant_baker.sources import FileSource


class SamHeader:
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


def program_chain(programs: List[Dict[str, str]]) -> str:
    """``bwa 0.7.17, samtools 1.19``, in the order the header declares."""
    named = []
    for program in programs:
        name = program["name"] or program["id"]
        if not name:
            continue
        named.append(f"{name} {program['version']}" if program["version"] else name)
    return ", ".join(named)


def describe_alignment(
    format_name: str,
    header: SamHeader,
    reference_count: int,
    name: str,
    sample_ids: List[str],
) -> str:
    """What the header says, in one deterministic sentence.

    Prose rather than new keys: sort order, assembly, sequencing platform and
    the program chain have no home in the Croissant or Schema.org vocabularies,
    and an invented JSON-LD key is one no consumer reads.
    """
    stated = []
    if header.sort_order:
        stated.append(f"{header.sort_order}-sorted")
    references = plural(reference_count, "reference sequence")
    stated.append(
        f"{references} ({header.assembly})" if header.assembly else references
    )
    stated.append(plural(header.read_group_count, "read group"))
    for label, values in (("platform", header.platforms), ("centre", header.centres)):
        if values:
            stated.append(f"{label}: {', '.join(values)}")
    chain = program_chain(header.programs)
    if chain:
        stated.append(f"aligned with {chain}")
    described = (
        f"{format_name} alignment file {name} ({'; '.join(stated)}). "
        "Described from its header; no alignment record was read."
    )
    if sample_ids:
        described += " Sample identifiers: " + ", ".join(sample_ids) + "."
    return described


def parse_sam_header(text: str) -> SamHeader:
    """The header ``text`` declares, line by line."""
    header = SamHeader()
    header.read(text)
    return header


#: Every key :func:`alignment_metadata` writes itself, and so the keys a
#: container's ``extra`` may not carry.
ALIGNMENT_KEYS = frozenset(
    {
        "file_name",
        "file_size",
        "sha256",
        "encoding_format",
        "sam_version",
        "sort_order",
        "sq_count",
        "read_group_count",
        "platforms",
        "centres",
        "programs",
        "assembly",
        "sample_ids",
        "description",
    }
)


def alignment_metadata(
    source: FileSource,
    encoding_format: str,
    described_as: str,
    header: SamHeader,
    reference_count: int,
    genomic_sample_ids: bool,
    extra: Optional[Dict[str, object]] = None,
) -> dict:
    """What an alignment handler returns for one file, whichever container.

    ``extra`` holds the keys only one container states, such as a BAM's own
    reference count or a CRAM's version. They go right after
    ``encoding_format``, and one that would overwrite a key this function
    writes is refused rather than lost. ``described_as`` is how the
    description names the format.
    """
    clashing = sorted(ALIGNMENT_KEYS.intersection(extra or {}))
    if clashing:
        raise ValueError(
            "alignment_metadata: extra repeats keys it writes itself: "
            + ", ".join(clashing)
        )
    metadata = {
        "file_name": source.name,
        "file_size": source.size,
        "sha256": source.sha256,
        "encoding_format": encoding_format,
        **(extra or {}),
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
    # The one thing an alignment handler emits. Built during extraction rather
    # than in build_croissant, which runs after the FileObject is staged, and
    # from the logical name, which is the only one extraction is given.
    metadata["description"] = describe_alignment(
        described_as, header, reference_count, source.name, sample_ids
    )
    return metadata
