"""GEO SOFT handler: the names a deposit uses, and the columns of its tables.

The grammar lives in :mod:`croissant_baker.handlers.soft`, which knows nothing
about Croissant. This module turns its output into record sets. What is
described and why is in ``docs/user-guide/supported-formats.md``.
"""

import io
import logging
from dataclasses import dataclass
from typing import Optional

import mlcroissant as mlc
import pyarrow.csv as pa_csv

from croissant_baker.handlers import soft
from croissant_baker.handlers.base_handler import BuildResult, FileTypeHandler
from croissant_baker.handlers.utils import (
    ARRAY_SHAPE_UNKNOWN_1D,
    SCHEMA_SAMPLE,
    allocate_record_set_ids,
    display_name,
    infer_column_types_from_arrow_schema,
    make_field_id,
)
from croissant_baker.sources import FileSource

logger = logging.getLogger(__name__)

#: GEO SOFT has no IANA registration. The ``x-`` form follows
#: ``application/x-nifti``, already in the tree.
ENCODING_FORMAT = "text/x-geo-soft"

#: ``(entity kind, record-set suffix, noun)``. ``^DATABASE`` is absent: it is
#: GEO boilerplate, byte-identical across a 2004 deposit and a 2026 one.
ENTITY_RECORD_SETS = (
    ("SERIES", "series", "series"),
    ("SAMPLE", "samples", "sample"),
    ("PLATFORM", "platforms", "platform"),
)

CHARACTERISTICS_SUFFIX = "sample_characteristics"

#: Said on every record set this handler emits, table record sets included: a
#: consumer reading one in isolation cannot otherwise tell that the fields
#: carry no data, and a typed column is where the question is sharpest.
NO_VALUE_NOTICE = "No value is emitted."

_IRREGULAR_PLURALS = {"series": "series"}


def _plural(count: int, noun: str) -> str:
    """``1 series``, ``7 samples``, ``25 attribute names``."""
    if count == 1:
        return f"{count} {noun}"
    return f"{count} {_IRREGULAR_PLURALS.get(noun, noun + 's')}"


@dataclass(frozen=True)
class DescribedTable:
    """One table with the two things the handler derives from it.

    Held together rather than as parallel lists: their positional relationship
    would otherwise be implicit, and ``zip`` truncates in silence.
    """

    table: soft.Table
    #: Record-set suffix, unique among this file's tables.
    suffix: str
    #: Column name -> Croissant type.
    column_types: dict


def _described_tables(tables: list) -> list:
    """One :class:`DescribedTable` per column signature, in declaration order.

    Suffixes are numbered by first appearance rather than ranked by how many
    entities share a signature, so the naming implies nothing about which
    signature matters.
    """
    seen: dict = {}
    out = []
    for table in tables:
        base = f"{table.kind.lower()}_table"
        seen[base] = seen.get(base, 0) + 1
        suffix = base if seen[base] == 1 else f"{base}_{seen[base]}"
        types = _column_types(table)
        # The sample has done its only job. Kept, it would hold a MiB per
        # signature for the whole bake, and a buffer of cells inside the
        # extracted metadata is one careless edit from the document.
        table.sample.clear()
        out.append(DescribedTable(table, suffix, types))
    return out


def _column_types(table: soft.Table) -> dict:
    """Croissant types for one table's columns, from its buffered row sample.

    The same PyArrow path a ``.tsv`` takes, because a table column is thousands
    of values under a declared header — a different thing from the one
    free-text value per entity that an attribute is.

    Quoting is off: a SOFT table body is raw tab-separated text with no quoting
    rules, and a lone ``"`` inside a GO annotation would otherwise swallow the
    rest of the sample. Anything PyArrow still refuses costs the types and
    nothing else — every column falls back to text, which is what SOFT
    guarantees anyway.
    """
    fallback = {name: "sc:Text" for name in table.columns}
    if not table.sample:
        return fallback
    try:
        sampled = pa_csv.read_csv(
            io.BytesIO(table.sample),
            parse_options=pa_csv.ParseOptions(delimiter="\t", quote_char=False),
        )
    except Exception as exc:  # noqa: BLE001 — the types, not the description
        logger.debug("Could not type a %s table: %s", table.kind.lower(), exc)
        return fallback
    inferred = infer_column_types_from_arrow_schema(sampled.schema)
    return {name: inferred.get(name, "sc:Text") for name in table.columns}


def _partial_note(parsed: soft.SoftFile) -> str:
    """What to append to every description when the read did not complete.

    A syntactically valid Croissant file that silently claims to be complete is
    worse than an explicit failure: a consumer cannot tell best-effort
    extraction from full extraction.
    """
    if not parsed.incomplete:
        return ""
    return " Partial parse: " + "; ".join(parsed.incomplete) + "."


class SOFTHandler(FileTypeHandler):
    """Handler for GEO SOFT family exports (``.soft``).

    Entities become record sets whose fields are the attribute and
    characteristic *names* the deposit uses, all ``sc:Text``; data tables become
    record sets whose fields are their columns, typed from a bounded row sample
    that is then discarded. No value is ever emitted.

    Fields carry ``source: {fileObject: …}`` and no ``extract``: Croissant
    1.1's extract grammar cannot address a repeated
    ``!Sample_characteristics_ch1`` key, and mlcroissant's reader dispatches on
    ``encodingFormat`` over a fixed list SOFT is not on, so an ``extract`` here
    would be a promise nobody can keep.
    """

    EXTENSIONS = (".soft",)
    FORMAT_NAME = "GEO SOFT"
    FORMAT_DESCRIPTION = (
        "Entity attribute names, sample characteristic keys, data table columns"
    )

    def claims(self, source: FileSource) -> bool:
        """Claim any ``.soft``, wrapped or not — ``source.suffix`` is logical.

        On the extension alone: a ``.soft`` that turns out not to be SOFT is
        better reported as a file this handler could not read than as one
        nothing claimed.
        """
        return source.suffix == ".soft"

    def extract(self, source: FileSource, **kwargs) -> dict:
        """Read one SOFT export in a single forward pass."""
        if not source.exists:
            raise FileNotFoundError(f"GEO SOFT file not found: {source.relative_path}")

        try:
            with source.open() as stream:
                parsed = soft.parse(stream, sample_rows=SCHEMA_SAMPLE)
        except Exception as exc:  # noqa: BLE001 — one file's failure, named
            # A truncated wrapper raises from three libraries under three base
            # classes, and the reason a user reads has to name the file.
            raise ValueError(
                f"Failed to read GEO SOFT file {source.relative_path}: {exc}"
            ) from exc

        if not parsed.kinds:
            raise ValueError(
                f"Not a GEO SOFT file: {source.relative_path} carries no "
                "'^ENTITY = ACCESSION' line"
            )

        if parsed.incomplete:
            logger.warning(
                "%s was parsed as far as it goes: %s",
                source.relative_path,
                "; ".join(parsed.incomplete),
            )

        return {
            "file_name": source.name,
            "file_size": source.size,
            "sha256": source.sha256,
            "encoding_format": ENCODING_FORMAT,
            "soft": parsed,
            "tables": _described_tables(parsed.tables),
        }

    def build_croissant(self, file_metas: list, file_ids: list) -> tuple:
        # Each file needs its own subset of suffixes, so the batch's union is
        # allocated for all of them; a reserved id nothing emits changes no
        # other id, because every candidate is prefixed by its own file's base.
        # Sorted, because allocation order decides which candidate is moved.
        suffixes = sorted(
            {
                *(suffix for _, suffix, _ in ENTITY_RECORD_SETS),
                CHARACTERISTICS_SUFFIX,
                *(t.suffix for meta in file_metas for t in meta["tables"]),
            }
        )
        allocated = allocate_record_set_ids(file_metas, suffixes)

        record_sets = []
        for meta, file_id, ids in zip(file_metas, file_ids, allocated):
            record_sets.extend(self._record_sets(meta, file_id, ids))
        return BuildResult([], record_sets)

    # ------------------------------------------------------------------

    def _record_sets(self, meta: dict, file_id: str, ids: dict) -> list:
        parsed: soft.SoftFile = meta["soft"]
        shown = display_name(meta)
        note = _partial_note(parsed)
        out = []

        for kind, suffix, noun in ENTITY_RECORD_SETS:
            group = parsed.kinds.get(kind)
            # An entity kind with no fields gets no record set. mlcroissant
            # validates an empty one, which is why this has to refuse.
            if group is None or not group.names:
                continue
            out.append(
                self._names_record_set(
                    ids[suffix],
                    file_id,
                    group,
                    f"{noun.capitalize()} attribute",
                    f"{noun.capitalize()}-level attributes in {shown} "
                    f"({_plural(group.entities, noun)}, "
                    f"{_plural(len(group.names), 'attribute name')}). "
                    f"{NO_VALUE_NOTICE}{note}",
                )
            )

        characteristics = parsed.characteristics
        if characteristics.names:
            unparsed = (
                f", and {_plural(parsed.unparsed, 'line')} that were not 'key: value'"
                if parsed.unparsed
                else ""
            )
            out.append(
                self._names_record_set(
                    ids[CHARACTERISTICS_SUFFIX],
                    file_id,
                    characteristics,
                    "Characteristic",
                    f"Submitter-defined sample characteristics in {shown} "
                    f"({_plural(characteristics.entities, 'sample')}, "
                    f"{_plural(len(characteristics.names), 'key')}{unparsed}). "
                    f"{NO_VALUE_NOTICE}{note}",
                )
            )

        for described in meta["tables"]:
            built = self._table_record_set(
                ids[described.suffix], file_id, described, shown, note
            )
            if built is not None:
                out.append(built)

        return out

    def _names_record_set(
        self,
        rs_id: str,
        file_id: str,
        group: soft.FieldGroup,
        label: str,
        description: str,
    ) -> mlc.RecordSet:
        """One record set per entity kind: one row per entity, fields by name.

        Every field is ``sc:Text``. SOFT is untyped text and declares nothing,
        and coercing before typing turns ``dbgap_subject_id: 27278`` into a
        measurement a mapping step would then trust.
        """
        used: set = set()
        fields = [
            mlc.Field(
                id=make_field_id(rs_id, name, used),
                name=name,
                description=f"{label} '{name}'",
                data_types=["sc:Text"],
                is_array=True if repeated else None,
                array_shape=ARRAY_SHAPE_UNKNOWN_1D if repeated else None,
                source=mlc.Source(file_object=file_id),
            )
            for name, repeated in group.names.items()
        ]
        return mlc.RecordSet(
            id=rs_id, name=rs_id, description=description, fields=fields
        )

    def _table_record_set(
        self,
        rs_id: str,
        file_id: str,
        described: DescribedTable,
        shown: str,
        note: str,
    ) -> Optional[mlc.RecordSet]:
        """One record set per column signature, one row per table row.

        ``None`` for a header naming no column at all — a row of bare tabs. A
        Field with no name is one mlcroissant cannot describe, and an empty
        record set is one it validates, so this refuses rather than emit either.
        """
        table = described.table
        named = [name for name in table.columns if name.strip()]
        if not named:
            logger.warning(
                "%s: a %s table declares a header naming no column; not described",
                shown,
                table.kind.lower(),
            )
            return None

        used: set = set()
        fields = []
        for name in named:
            # The deposit's own ``#COLUMN`` line, verbatim, so its provenance is
            # visible. It is format-guaranteed to describe the column.
            documented = table.column_lines.get(name)
            fields.append(
                mlc.Field(
                    id=make_field_id(rs_id, name, used),
                    name=name,
                    description=(
                        f"Column '{name}'. {documented}"
                        if documented
                        else f"Column '{name}'"
                    ),
                    data_types=[described.column_types.get(name, "sc:Text")],
                    source=mlc.Source(file_object=file_id),
                )
            )

        rows = (
            f", {table.rows} rows declared"
            if table.rows_known
            else ", row count not declared"
        )
        unnamed = len(table.columns) - len(named)
        columns = _plural(len(named), "column") + (
            f", {unnamed} unnamed" if unnamed else ""
        )
        return mlc.RecordSet(
            id=rs_id,
            name=rs_id,
            description=(
                f"Inline data table of {_plural(table.entities, table.kind.lower())} "
                f"in {shown} ({columns}{rows}). {NO_VALUE_NOTICE}{note}"
            ),
            fields=fields,
        )
