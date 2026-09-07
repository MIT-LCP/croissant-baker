"""Parquet file handler for tabular event streams (e.g., MEDS)."""

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import NamedTuple, Optional
from pathlib import Path

import mlcroissant as mlc
from pyarrow.parquet import ParquetFile

from croissant_baker.handlers.base_handler import (
    BuildResult,
    Declined,
    FileTypeHandler,
)
from croissant_baker.scan import Reason
from croissant_baker.sources import FileSource
from croissant_baker.handlers.utils import (
    _build_fields,
    _disambiguate_ids,
    display_name,
    get_clean_record_name,
    infer_column_types_from_arrow_schema,
    make_field_id,
    DIGIT_MASK,
    sanitize_id,
    shard_template,
)

logger = logging.getLogger(__name__)

# Apache Parquet file format spec: every Parquet file begins AND ends with
# the 4-byte ASCII magic "PAR1" — the trailing copy is the footer marker.
# A valid file is therefore at least 8 bytes long.
# Reference: https://parquet.apache.org/docs/file-format/
_PARQUET_MAGIC = b"PAR1"
_PARQUET_MAGIC_LEN = len(_PARQUET_MAGIC)


def _tail(stream, size: int) -> bytes:
    """Return the last ``size`` bytes of ``stream``.

    gzip, bzip2 and xz all support ``SEEK_END`` in read mode — the seek costs a
    decompression pass, which is why the footer check is not free. A stream
    that refuses raises, and :func:`_has_parquet_magic` declines the file.
    """
    stream.seek(-size, 2)
    return stream.read(size)


def _has_parquet_magic(source: FileSource) -> bool:
    """Return True iff ``source`` has the Parquet magic at start and end.

    Files that fail any check (too small, missing PAR1 header, missing PAR1
    footer) are rejected, and the specific reason is logged at debug. Missing
    or unreadable files return False without logging (caller errors, not
    impostors). The footer check costs a full pass on a compressed file.

    A refusal at claim time is logged at debug, not warning: the generator
    already names every undescribed file once, through a capped path, and
    warning here both doubled that and escaped the cap.
    """
    if not source.exists:
        return False
    if source.size < _PARQUET_MAGIC_LEN * 2:
        logger.debug(
            "Skipping %s: file is too small (%d bytes) to be a valid Parquet file",
            source.relative_path,
            source.size,
        )
        return False
    try:
        with source.open() as f:
            if f.read(_PARQUET_MAGIC_LEN) != _PARQUET_MAGIC:
                logger.debug(
                    "Skipping %s: missing Parquet PAR1 header magic",
                    source.relative_path,
                )
                return False
            if _tail(f, _PARQUET_MAGIC_LEN) != _PARQUET_MAGIC:
                logger.debug(
                    "Skipping %s: missing Parquet PAR1 footer magic "
                    "(file may be truncated)",
                    source.relative_path,
                )
                return False
            return True
    except (OSError, ValueError):
        # ValueError: a compression whose stream refuses SEEK_END. None of the
        # built-in three do, but register_compression is public.
        return False


class _File(NamedTuple):
    index: int
    file_id: str
    meta: dict


@dataclass(frozen=True)
class _Table:
    """One logical table: the shards of a partition, or a standalone file."""

    dir_path: str
    files: list
    name: str
    stem: str
    disambig_parents: list
    is_partitioned: bool
    alone_in_dir: bool
    covers_dir: bool

    @property
    def first_meta(self) -> dict:
        return self.files[0].meta


def _schema_of(meta: dict) -> str:
    """The key two files must share to be shards of one table.

    The Arrow schema, not ``column_types``: Croissant types collapse timestamp
    units, nullability, decimal precision, nested structure and fixed-list
    dimensions, and the emitted fields come from one shard's Arrow schema, so a
    difference there misdescribes every other shard.
    """
    schema = meta.get("arrow_schema")
    if schema is None:
        return str(list(meta["column_types"].items()))
    # Exactly what ``Schema.equals`` compares by default: name, type and
    # nullability. Rendering the schema instead kept field-level metadata in
    # the key, which Arrow ignores; dropping nullability went the other way.
    return str([(f.name, str(f.type), f.nullable) for f in schema])


def _group_tables(file_metas: list, file_ids: list) -> tuple:
    """Split Parquet files into logical tables, and the shards that would not fit.

    Spark and Arrow write one table as a directory of ``part-*.parquet``; a
    vendor output directory holds several unrelated ones. Directory membership
    cannot tell those apart, so files group on evidence instead: a shard-shaped
    name that matches once its index is masked, and an identical Arrow schema.
    """
    by_dir: dict = defaultdict(list)
    for index, (file_id, meta) in enumerate(zip(file_ids, file_metas)):
        by_dir[str(Path(meta["relative_path"]).parent)].append(
            _File(index, file_id, meta)
        )

    tables: list = []
    conflicts: list = []
    for dir_path in sorted(by_dir):
        dir_tables, dir_conflicts = _tables_in_dir(dir_path, by_dir[dir_path])
        tables.extend(dir_tables)
        conflicts.extend(dir_conflicts)
    return tables, conflicts


def _tables_in_dir(dir_path: str, files: list) -> tuple:
    """The tables one directory holds, and the shards it had to decline."""
    ordered = sorted(files, key=lambda f: str(f.meta["relative_path"]))

    # The dataset root is not a table, so its own files never pair — and with
    # no merge to protect, a schema difference between them decides nothing.
    if dir_path == ".":
        return [_make_table(dir_path, [f], None, False, False) for f in ordered], []

    by_template: dict = defaultdict(list)
    loners: list = []
    for file in ordered:
        template = shard_template(file.meta["file_name"])
        if template is None:
            loners.append(file)
        else:
            by_template[template].append(file)

    groups: list = []
    conflicts: list = []
    for template in sorted(by_template):
        by_schema: dict = defaultdict(list)
        for file in by_template[template]:
            by_schema[_schema_of(file.meta)].append(file)

        kept, declined = _majority_schema(by_schema)
        # Only a table that would really have merged can turn a shard away.
        # Where the majority is itself a single file, nothing was pairing.
        if declined and len(kept) >= 2:
            conflicts.extend(_describe_conflicts(kept, declined))
            schema_groups = [kept]
        else:
            schema_groups = list(by_schema.values())

        for shards in schema_groups:
            if len(shards) >= 2:
                groups.append((shards, template))
            else:
                groups.extend(([file], None) for file in shards)

    groups.extend(([file], None) for file in loners)

    alone = len(groups) == 1
    # Naming and globbing ask different questions. A directory holding one
    # table still names it, even having declined a shard; a glob standing in
    # for that table's files would match the declined shard right back.
    covers_dir = alone and not conflicts
    return [
        _make_table(dir_path, g, t, alone, covers_dir) for g, t in groups
    ], conflicts


def _file_set_ids(tables: list, rs_ids: list) -> dict:
    """A FileSet id per partitioned table, unique against every record set too.

    ``@id`` is unique document-wide in Croissant, not per node kind. Deriving
    the FileSet id from its record set's is not enough on its own: a file
    genuinely named ``sales-fileset.parquet`` produces a record set that claims
    the derived id first, and the document then carries two nodes under one id.
    """
    taken = set(rs_ids)
    out: dict = {}
    for table, rs_id in zip(tables, rs_ids):
        if not table.is_partitioned:
            continue
        candidate = f"{rs_id}-fileset"
        n = 2
        while candidate in taken:
            candidate = f"{rs_id}-fileset__{n}"
            n += 1
        taken.add(candidate)
        out[rs_id] = candidate
    return out


def _majority_schema(by_schema: dict) -> tuple:
    """The strictly-largest shard group, and every file outside it.

    A tie is not a majority. Two shards of each of two schemas gives no reason
    to prefer either, and breaking it on filename order would discard half the
    directory on the strength of alphabetical accident — so ``([], [])`` is
    returned and the caller describes every group instead.
    """
    ordered = sorted(
        by_schema.values(),
        key=lambda g: (-len(g), min(str(f.meta["relative_path"]) for f in g)),
    )
    if len(ordered) > 1 and len(ordered[0]) == len(ordered[1]):
        return [], []
    return ordered[0], [file for group in ordered[1:] for file in group]


def _describe_conflicts(kept: list, declined: list) -> list:
    expected = len(kept[0].meta["column_types"])
    reference = kept[0].meta["file_name"]
    return [
        Declined(
            file.index,
            Reason.PARTITION_SCHEMA_CONFLICT,
            f"{len(file.meta['column_types'])} columns, "
            f"expected {expected} from {reference}",
        )
        for file in declined
    ]


def _make_table(
    dir_path: str,
    files: list,
    template: Optional[str],
    alone: bool,
    covers_dir: bool,
) -> _Table:
    meta = files[0].meta
    parents = list(Path(meta["relative_path"]).parts[:-1])
    partitioned = len(files) >= 2

    # Parquet basenames are usually partition labels, so a directory holding one
    # table names it better than its own files do. Once it holds several, the
    # basenames are what tell them apart.
    if alone and parents:
        name, disambig_parents = parents[-1], parents[:-1]
    elif partitioned:
        name = _shared_stem(template) or get_clean_record_name(meta["file_name"])
        disambig_parents = parents
    else:
        name, disambig_parents = get_clean_record_name(meta["file_name"]), parents

    return _Table(
        dir_path=dir_path,
        files=files,
        name=name,
        stem=sanitize_id(name),
        disambig_parents=disambig_parents,
        is_partitioned=partitioned,
        alone_in_dir=alone,
        covers_dir=covers_dir,
    )


def _shared_stem(template: str) -> str:
    """What a table's shards have in common, so two do not become ``part-00000``."""
    return get_clean_record_name(template).replace(DIGIT_MASK, "").rstrip("-_.")


class ParquetHandler(FileTypeHandler):
    """
    Handler for Parquet files (.parquet) with schema-based type inference.

    - Uses pyarrow to read schema and row count without loading full data
    - Emits Croissant-compatible column types via shared map_arrow_type()
    - Computes SHA256 for reproducibility
    - Keeps memory usage minimal (schema-only)
    """

    EXTENSIONS = (".parquet",)
    FORMAT_NAME = "Parquet"
    FORMAT_DESCRIPTION = "Arrow schema, column names and types, row count"

    def claims(self, source: FileSource) -> bool:
        if source.suffix != ".parquet":
            return False
        return _has_parquet_magic(source)

    def extract(self, source: FileSource, **kwargs) -> dict:
        """Extract metadata from a Parquet file via pyarrow schema inspection."""
        if not source.exists:
            raise FileNotFoundError(f"Parquet file not found: {source.relative_path}")

        try:
            with source.open() as stream, ParquetFile(stream) as pq:
                schema = pq.schema_arrow
                num_rows = pq.metadata.num_rows if pq.metadata is not None else 0

                # Use the shared Arrow type mapper (same as CSV handler)
                column_types = infer_column_types_from_arrow_schema(schema)
                columns = [field.name for field in schema]

            return {
                "file_name": source.name,
                "file_size": source.size,
                "sha256": source.sha256,
                "encoding_format": "application/vnd.apache.parquet",
                "column_types": column_types,
                "arrow_schema": schema,
                "num_rows": num_rows,
                "num_columns": len(columns),
                "columns": columns,
            }
        except Exception as e:
            raise ValueError(
                f"Failed to process Parquet file {source.relative_path}: {e}"
            ) from e

    def build_croissant(self, file_metas: list, file_ids: list) -> tuple:
        """Build FileSets and RecordSets for all Parquet files in this dataset.

        Returns ``(file_sets, record_sets, conflicts)``, the last being
        ``(index, Reason, detail)`` per file this declined to describe.
        """
        tables, conflicts = _group_tables(file_metas, file_ids)
        rs_ids = _disambiguate_ids([(t.stem, t.disambig_parents) for t in tables])
        fs_ids = _file_set_ids(tables, rs_ids)

        file_sets = []
        record_sets = []
        for table, rs_id in zip(tables, rs_ids):
            if table.is_partitioned:
                file_sets.append(self._partition_file_set(table, rs_id, fs_ids[rs_id]))
                record_sets.append(
                    self._partition_record_set(table, rs_id, fs_ids[rs_id])
                )
            else:
                record_sets.append(self._standalone_record_set(table, rs_id))

        return BuildResult(file_sets, record_sets, tuple(conflicts))

    def _partition_file_set(self, table: _Table, rs_id: str, fs_id: str) -> mlc.FileSet:
        """One FileSet over a table's shards."""
        if table.covers_dir:
            suffix = "".join(Path(table.first_meta["file_name"]).suffixes)
            includes = [f"{table.dir_path}/*{suffix}"]
        else:
            # A directory-wide glob would reach the neighbouring tables, and any
            # shard whose schema kept it out of this one.
            includes = [f.meta["relative_path"] for f in table.files]

        return mlc.FileSet(
            id=fs_id,
            name=f"{table.name} partition files",
            description=f"{len(table.files)} Parquet partition files for table '{table.name}'",
            encoding_formats=["application/vnd.apache.parquet"],
            includes=includes,
        )

    def _partition_record_set(
        self, table: _Table, rs_id: str, fs_id: str
    ) -> mlc.RecordSet:
        fields = self._fields(
            table.first_meta,
            rs_id,
            {"file_set": fs_id},
            f"table '{table.name}'",
        )
        num_rows = sum(f.meta.get("num_rows", 0) for f in table.files)
        return mlc.RecordSet(
            id=rs_id,
            name=table.name,
            description=(
                f"Partitioned table '{table.name}' "
                f"({len(table.files)} Parquet files, {num_rows} total rows)"
            ),
            fields=fields,
        )

    def _standalone_record_set(self, table: _Table, rs_id: str) -> mlc.RecordSet:
        file = table.files[0]
        fields = self._fields(
            file.meta, rs_id, {"file_object": file.file_id}, display_name(file.meta)
        )
        num_rows = file.meta.get("num_rows")
        row_desc = f" ({num_rows} rows)" if num_rows is not None else ""
        return mlc.RecordSet(
            id=rs_id,
            name=table.name,
            description=f"Records from {display_name(file.meta)}{row_desc}",
            fields=fields,
        )

    def _fields(self, meta: dict, rs_id: str, source: dict, origin: str) -> list:
        if "arrow_schema" in meta:
            return _build_fields(meta["arrow_schema"], rs_id, source)

        used_field_ids: set = set()
        fields = []
        for col_name, col_type in meta["column_types"].items():
            fields.append(
                mlc.Field(
                    id=make_field_id(rs_id, col_name, used_field_ids),
                    name=col_name,
                    description=f"Column '{col_name}' from {origin}",
                    data_types=[col_type],
                    source=mlc.Source(**source, extract=mlc.Extract(column=col_name)),
                )
            )
        return fields
