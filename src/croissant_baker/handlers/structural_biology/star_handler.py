"""STAR handler: the tables a cryo-EM metadata file declares, and their columns.

The document grammar lives in :mod:`croissant_baker.handlers.structural_biology.cif`,
which knows nothing about Croissant, and the record sets it becomes are built by
:mod:`croissant_baker.handlers.structural_biology.tables`, which the structure
handler shares for a CIF that carries no structure.
"""

import logging

try:
    import gemmi
except ImportError:
    # An optional extra. The handler stays registered and keeps claiming
    # ``.star``, so a file it cannot read is refused with an install hint
    # rather than passed over as a format nobody recognises.
    gemmi = None

from croissant_baker.handlers.base_handler import BuildResult, FileTypeHandler
from croissant_baker.handlers.structural_biology import cif, tables
from croissant_baker.sources import FileSource

logger = logging.getLogger(__name__)

#: STAR has no IANA registration. The ``x-`` form follows ``application/x-nifti``,
#: already in the tree.
ENCODING_FORMAT = "application/x-star"


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
        cif.require_gemmi(gemmi, "STAR")
        if not source.exists:
            raise FileNotFoundError(f"STAR file not found: {source.relative_path}")

        try:
            with source.open() as stream:
                # Replacing rather than refusing: a stray byte in a comment or a
                # micrograph name costs that one value, where a strict decode
                # would cost the whole description.
                text = stream.read().decode("utf-8", "replace")
            described = cif.describe(gemmi.cif.read_string(text))
        except Exception as exc:  # noqa: BLE001, one file's failure, named
            # gemmi reports a syntax error as ValueError and a structural one,
            # a repeated block name among them, as RuntimeError. The reason a
            # user reads has to name the file either way.
            raise ValueError(
                f"Failed to read STAR file {source.relative_path}: {exc}"
            ) from exc

        if not described:
            raise ValueError(
                f"Not a STAR file: {source.relative_path} carries no data block"
            )

        return {
            "file_name": source.name,
            "file_size": source.size,
            "sha256": source.sha256,
            "encoding_format": ENCODING_FORMAT,
            tables.TABLES: described,
        }

    def build_croissant(self, file_metas: list, file_ids: list) -> tuple:
        """One record set per data block. No FileSet: two STAR files are two
        jobs, and a set spanning them would claim they share a schema."""
        return BuildResult([], tables.record_sets(file_metas, file_ids))
