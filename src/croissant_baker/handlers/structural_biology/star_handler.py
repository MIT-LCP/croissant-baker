"""STAR handler: the tables a cryo-EM metadata file declares, and their columns.

The document grammar lives in :mod:`croissant_baker.handlers.structural_biology.cif`,
which knows nothing about Croissant. This module turns its tables into record
sets.
"""

import logging

import gemmi
import mlcroissant as mlc

from croissant_baker.handlers.base_handler import BuildResult, FileTypeHandler
from croissant_baker.handlers.structural_biology import cif
from croissant_baker.handlers.utils import (
    allocate_record_set_ids,
    display_name,
    make_field_id,
    sanitize_id,
)
from croissant_baker.sources import FileSource

logger = logging.getLogger(__name__)

#: STAR has no IANA registration. The ``x-`` form follows ``application/x-nifti``,
#: already in the tree.
ENCODING_FORMAT = "application/x-star"

#: Said on every record set: the fields carry no ``extract``, and a consumer
#: reading one in isolation cannot otherwise tell that they hold no data.
NO_VALUE_NOTICE = "No value is emitted."


def _plural(count: int, noun: str) -> str:
    """``1 row``, ``3 rows``."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


class STARHandler(FileTypeHandler):
    """Handler for STAR metadata files (``.star``).

    RELION and the rest of the cryo-EM chain keep their bookkeeping in STAR: a
    particle stack's per-particle geometry, an optics group table, a job's
    settings. Each data block becomes a record set whose fields are the block's
    columns, typed from the values the file carries.

    Fields carry ``source: {fileObject: …}`` and no ``extract``: mlcroissant's
    reader dispatches on ``encodingFormat`` over a fixed list STAR is not on, so
    an ``extract`` here would be a promise nobody can keep. No value is emitted.
    """

    EXTENSIONS = (".star",)
    FORMAT_NAME = "STAR"
    FORMAT_DESCRIPTION = (
        "RELION and other cryo-EM STAR files: one table per data block, "
        "columns typed from values"
    )

    def claims(self, source: FileSource) -> bool:
        """Claim any ``.star``, wrapped or not: ``source.suffix`` is logical.

        On the extension alone: a ``.star`` that turns out not to be STAR is
        better reported as a file this handler could not read than as one
        nothing claimed.
        """
        return source.suffix == ".star"

    def extract(self, source: FileSource, **kwargs) -> dict:
        """Describe one STAR file's blocks and columns."""
        if not source.exists:
            raise FileNotFoundError(f"STAR file not found: {source.relative_path}")

        try:
            with source.open() as stream:
                # Replacing rather than refusing: a stray byte in a comment or a
                # micrograph name costs that one value, where a strict decode
                # would cost the whole description.
                text = stream.read().decode("utf-8", "replace")
            tables = cif.describe(gemmi.cif.read_string(text))
        except Exception as exc:  # noqa: BLE001 — one file's failure, named
            # gemmi reports a syntax error as ValueError and a structural one,
            # a repeated block name among them, as RuntimeError. The reason a
            # user reads has to name the file either way.
            raise ValueError(
                f"Failed to read STAR file {source.relative_path}: {exc}"
            ) from exc

        if not tables:
            raise ValueError(
                f"Not a STAR file: {source.relative_path} carries no data block"
            )

        return {
            "file_name": source.name,
            "file_size": source.size,
            "sha256": source.sha256,
            "encoding_format": ENCODING_FORMAT,
            "tables": tables,
        }

    def build_croissant(self, file_metas: list, file_ids: list) -> tuple:
        # Each file needs its own subset of suffixes, so the batch's union is
        # allocated for all of them; a reserved id nothing emits changes no
        # other id, because every candidate is prefixed by its own file's base.
        # Sorted, because allocation order decides which candidate is moved.
        suffixes = sorted(
            {sanitize_id(table.name) for meta in file_metas for table in meta["tables"]}
        )
        allocated = allocate_record_set_ids(file_metas, suffixes)

        record_sets = []
        for meta, file_id, ids in zip(file_metas, file_ids, allocated):
            record_sets.extend(self._record_sets(meta, file_id, ids))
        return BuildResult([], record_sets)

    # ------------------------------------------------------------------

    def _record_sets(self, meta: dict, file_id: str, ids: dict) -> list:
        shown = display_name(meta)
        out = []
        for table in meta["tables"]:
            # A block naming no column at all. mlcroissant validates a record
            # set with no field, which is why this has to refuse.
            if not table.columns:
                logger.warning(
                    "%s: data block '%s' declares no column; not described",
                    shown,
                    table.block,
                )
                continue
            out.append(
                self._record_set(ids[sanitize_id(table.name)], file_id, table, shown)
            )
        return out

    def _record_set(
        self, rs_id: str, file_id: str, table: cif.Table, shown: str
    ) -> mlc.RecordSet:
        """One record set per table: one row per loop row, fields by column."""
        used: set = set()
        fields = [
            mlc.Field(
                id=make_field_id(rs_id, name, used),
                name=name,
                description=f"Column '{name}'",
                data_types=[data_type],
                source=mlc.Source(file_object=file_id),
            )
            for name, data_type in table.columns
        ]
        return mlc.RecordSet(
            id=rs_id,
            name=rs_id,
            description=(
                f"Data block '{table.block}' of {shown} "
                f"({_plural(table.rows, 'row')}, "
                f"{_plural(len(table.columns), 'column')}). {NO_VALUE_NOTICE}"
            ),
            fields=fields,
        )
