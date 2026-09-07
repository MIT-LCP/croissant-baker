"""Generic JSON and JSONL file handler.

Supports four file formats:
- ``.json``: a JSON array of objects (each object is one row)
  or a single JSON object (treated as one row).
- ``.jsonl``: newline-delimited JSON (one JSON object per line).

FHIR ``.json`` files (those containing ``"resourceType": "<UpperCase…"``)
are intentionally excluded — they are claimed by FHIRHandler instead.
"""

import json
import logging
import re

import mlcroissant as mlc

from croissant_baker.handlers.base_handler import BuildResult, FileTypeHandler
from croissant_baker.sources import FileSource
from croissant_baker.handlers.utils import (
    SCHEMA_SAMPLE,
    build_fields_from_json_schema,
    display_name,
    infer_json_schema,
    make_record_set_ids,
)

logger = logging.getLogger(__name__)

# Pattern that identifies a FHIR JSON file — resourceType value starts with uppercase.
_FHIR_PATTERN = re.compile(r'"resourceType"\s*:\s*"[A-Z]')


class JSONHandler(FileTypeHandler):
    """Handler for generic JSON and JSONL datasets.

    Detection strategy:
    - ``.jsonl``: always accepted (FHIR uses ``.ndjson``, not ``.jsonl``).
    - ``.json``: accepted only when the first 4 KB does NOT match the FHIR
      resourceType pattern and the content starts with ``[`` (array) or ``{`` (object).
    """

    EXTENSIONS = (".json", ".jsonl")
    FORMAT_NAME = "JSON / JSONL"
    FORMAT_DESCRIPTION = "Schema inferred from a sample of records"

    def claims(self, source: FileSource) -> bool:
        name = source.name.lower()
        if name.endswith(".jsonl"):
            return True
        if name.endswith(".json"):
            return self._sniff_json(source)
        return False

    def _sniff_json(self, source: FileSource) -> bool:
        """Peek at the first 4 KB to confirm the file is non-FHIR JSON.

        FHIR top-level objects always start with ``{``, so the FHIR exclusion
        check is only applied to ``{``-rooted content. Arrays are always
        accepted — a nested ``resourceType`` key inside an array element is
        not a FHIR document.
        """
        try:
            with source.open_text() as fh:
                head = fh.read(4096)
            head = head.strip()
            if head.startswith("{"):
                return not _FHIR_PATTERN.search(head)
            return head.startswith("[")
        except (OSError, UnicodeDecodeError):
            return False

    def extract(self, source: FileSource, **kwargs) -> dict:
        """Extract metadata from a JSON or JSONL file.

        Returns a dict with keys:
            file_name, file_size, sha256, encoding_format,
            column_types, columns, num_columns, num_rows.

        Raises:
            ValueError: If the file cannot be parsed or contains no records.
        """
        if not source.exists:
            raise FileNotFoundError(f"JSON file not found: {source.relative_path}")
        try:
            if source.suffix == ".jsonl":
                return self._extract_jsonl(source)
            return self._extract_json(source)
        except UnicodeDecodeError as exc:
            # Binary bytes behind a .json name. UnicodeDecodeError is a
            # ValueError subclass, so it reached the report as a raw codec
            # message; name the file and the format instead.
            raise ValueError(
                f"Failed to read JSON file {source.relative_path}: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _extract_jsonl(self, source: FileSource) -> dict:
        """Stream a JSONL file line-by-line.

        Collects up to ``SCHEMA_SAMPLE`` records for schema inference while
        counting all rows.
        """
        schema_samples: list = []
        num_rows = 0

        with source.open_text() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning(
                        "Skipping malformed JSON line in %s", source.relative_path
                    )
                    continue
                if not isinstance(obj, dict):
                    continue
                num_rows += 1
                if len(schema_samples) < SCHEMA_SAMPLE:
                    schema_samples.append(obj)

        if num_rows == 0:
            raise ValueError(f"No valid JSON objects found in {source.relative_path}")

        if num_rows > SCHEMA_SAMPLE:
            logger.warning(
                "Sampled %d of %d records for schema inference in %s — rare fields may be missing",
                SCHEMA_SAMPLE,
                num_rows,
                source.relative_path,
            )

        column_types = infer_json_schema(schema_samples, _top_level=False)
        encoding = "application/jsonl"
        return {
            "file_name": source.name,
            "file_size": source.size,
            "sha256": source.sha256,
            "encoding_format": encoding,
            "column_types": column_types,
            "columns": list(column_types.keys()),
            "num_columns": len(column_types),
            "num_rows": num_rows,
        }

    def _extract_json(self, source: FileSource) -> dict:
        """Load a ``.json`` file entirely.

        - JSON array  → each element is a row (only dicts are kept).
        - JSON object → treated as a single row.
        """
        with source.open_text() as fh:
            try:
                doc = json.load(fh)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Cannot parse {source.relative_path} as JSON: {exc}"
                ) from exc

        if isinstance(doc, list):
            rows = [r for r in doc if isinstance(r, dict)]
        elif isinstance(doc, dict):
            rows = [doc]
        else:
            raise ValueError(
                f"{source.relative_path} is not a JSON object or array of objects"
            )

        if not rows:
            raise ValueError(f"No valid JSON objects found in {source.relative_path}")

        num_rows = len(rows)
        schema_samples = rows[:SCHEMA_SAMPLE]

        if num_rows > SCHEMA_SAMPLE:
            logger.warning(
                "Sampled %d of %d records for schema inference in %s — rare fields may be missing",
                SCHEMA_SAMPLE,
                num_rows,
                source.relative_path,
            )

        column_types = infer_json_schema(schema_samples, _top_level=False)
        encoding = "application/json"
        return {
            "file_name": source.name,
            "file_size": source.size,
            "sha256": source.sha256,
            "encoding_format": encoding,
            "column_types": column_types,
            "columns": list(column_types.keys()),
            "num_columns": len(column_types),
            "num_rows": num_rows,
        }

    def build_croissant(self, file_metas: list, file_ids: list) -> tuple:
        """Build Croissant RecordSets for all JSON/JSONL files.

        One RecordSet per file; no additional FileSet distributions are
        created (each file becomes its own FileObject, owned by the generator).

        Returns:
            ([], record_sets) — no additional distributions.
        """
        record_sets: list = []
        rs_ids = make_record_set_ids(file_metas)

        for file_id, meta, rs_id in zip(file_ids, file_metas, rs_ids):
            shown = display_name(meta)
            num_rows = meta.get("num_rows")
            row_desc = f" ({num_rows} rows)" if num_rows is not None else ""
            record_sets.append(
                mlc.RecordSet(
                    id=rs_id,
                    name=rs_id,
                    description=f"Records from {shown}{row_desc}",
                    fields=build_fields_from_json_schema(
                        meta["column_types"],
                        rs_id,
                        source_ref={"file_object": file_id},
                        used_field_ids=set(),
                    ),
                )
            )

        return BuildResult([], record_sets)
