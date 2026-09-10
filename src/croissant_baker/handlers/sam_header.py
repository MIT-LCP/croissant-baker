"""The SAM text header, shared by every alignment container that carries one.

BAM, CRAM and SAM itself all open with the same text: ``@HD`` for the sort
order, ``@SQ`` per reference sequence, ``@RG`` per read group and ``@PG`` per
program that touched the file. Reading it is the same job whichever container
it sits in, so it is done here, once, and each handler only has to find it.
"""

from typing import Dict, List, Optional

from croissant_baker.handlers.utils import plural


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
