"""Tests for Parquet handler."""

import io
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import croissant_baker.handlers.parquet_handler as parquet_handler_module
from croissant_baker import compression
from croissant_baker.scan import Reason
from croissant_baker.handlers.parquet_handler import ParquetHandler
from croissant_baker.sources import make_source

from tests.helpers import write_wrapped


@pytest.fixture
def handler() -> ParquetHandler:
    return ParquetHandler()


@pytest.fixture
def sample_parquet(tmp_path: Path) -> Path:
    """Create a minimal Parquet file for testing."""
    table = pa.table(
        {
            "id": pa.array([1, 2, 3], type=pa.int64()),
            "name": pa.array(["Alice", "Bob", "Charlie"], type=pa.string()),
            "score": pa.array([9.5, 8.3, 7.1], type=pa.float64()),
        }
    )
    path = tmp_path / "test.parquet"
    pq.write_table(table, str(path))
    return path


_PARQUET_LOGGER = "croissant_baker.handlers.parquet_handler"


@pytest.mark.parametrize(
    "payload, expected",
    [
        (b"not a parquet file at all", "PAR1 header"),
        (b"PAR1" + b"\x00" * 32, "footer"),
        (b"PAR", "too small"),
    ],
    ids=["wrong-magic", "truncated", "too-small"],
)
def test_a_file_that_is_not_parquet_is_declined_at_debug(
    handler: ParquetHandler,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    payload: bytes,
    expected: str,
) -> None:
    """The generator owns the user-facing warning, through a capped path, so
    the handler's own note is debug — asserted, or a regression to WARNING
    would double every skip in the terminal."""
    impostor = tmp_path / "fake.parquet"
    impostor.write_bytes(payload)

    with caplog.at_level("DEBUG", logger=_PARQUET_LOGGER):
        assert handler.claims(make_source(impostor)) is False

    assert [
        r
        for r in caplog.records
        if r.levelname == "DEBUG"
        and impostor.name in r.message
        and expected in r.message
    ], caplog.records


def test_can_handle_accepts_real_parquet(
    handler: ParquetHandler, sample_parquet: Path
) -> None:
    """A real Parquet file (PAR1 at start AND end) is accepted, including
    when the extension is uppercased."""
    assert handler.claims(make_source(sample_parquet)) is True

    upper = sample_parquet.with_name("test.PARQUET")
    upper.write_bytes(sample_parquet.read_bytes())
    assert handler.claims(make_source(upper)) is True


def test_extract_metadata(handler: ParquetHandler, sample_parquet: Path) -> None:
    """Test Parquet metadata extraction returns correct structure."""
    metadata = handler.extract(make_source(sample_parquet))

    assert metadata["file_name"] == "test.parquet"
    assert metadata["encoding_format"] == "application/vnd.apache.parquet"
    assert metadata["file_size"] > 0
    assert len(metadata["sha256"]) == 64
    assert metadata["num_rows"] == 3
    assert metadata["num_columns"] == 3
    assert metadata["columns"] == ["id", "name", "score"]

    column_types = metadata["column_types"]
    assert column_types["id"] == "cr:Int64"
    assert column_types["name"] == "sc:Text"
    assert column_types["score"] == "cr:Float64"


def test_extract_metadata_not_found(handler: ParquetHandler) -> None:
    """Test that missing file raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        handler.extract(make_source(Path("/nonexistent/data.parquet")))


def _pq_meta(name, rel_path, cols=None):
    return {
        "file_name": name,
        "relative_path": rel_path,
        "column_types": cols or {"id": "sc:Text", "value": "cr:Float64"},
        "num_rows": 10,
        "encoding_format": "application/vnd.apache.parquet",
    }


_BOUNDARIES = {"cell_id": "sc:Text", "vertex_x": "cr:Float32"}
_CELLS = {"cell_id": "sc:Text", "x_centroid": "cr:Float32", "counts": "cr:Int32"}


def test_distinct_names_are_distinct_tables_even_with_one_schema(
    handler: ParquetHandler,
) -> None:
    """Two 10x boundary tables share a schema but are not partitions of each other."""
    metas = [
        _pq_meta("cell_boundaries.parquet", "s1/cell_boundaries.parquet", _BOUNDARIES),
        _pq_meta(
            "nucleus_boundaries.parquet", "s1/nucleus_boundaries.parquet", _BOUNDARIES
        ),
    ]
    built = handler.build_croissant(metas, ["file_0", "file_1"])
    filesets, record_sets, conflicts = (
        built.file_sets,
        built.record_sets,
        built.declined,
    )

    assert filesets == []
    assert conflicts == ()
    assert sorted(r.name for r in record_sets) == [
        "cell_boundaries",
        "nucleus_boundaries",
    ]


def test_a_vendor_directory_keeps_every_schema(handler: ParquetHandler) -> None:
    """Four tables in one sample directory stay four, with their own columns."""
    metas = [
        _pq_meta("cell_boundaries.parquet", "s1/cell_boundaries.parquet", _BOUNDARIES),
        _pq_meta("cells.parquet", "s1/cells.parquet", _CELLS),
        _pq_meta(
            "nucleus_boundaries.parquet", "s1/nucleus_boundaries.parquet", _BOUNDARIES
        ),
    ]
    built = handler.build_croissant(metas, ["f0", "f1", "f2"])
    _, record_sets, _ = built.file_sets, built.record_sets, built.declined

    by_name = {r.name: [f.name for f in r.fields] for r in record_sets}
    assert set(by_name) == {"cell_boundaries", "cells", "nucleus_boundaries"}
    assert by_name["cells"] == ["cell_id", "x_centroid", "counts"]
    assert by_name["cell_boundaries"] == ["cell_id", "vertex_x"]


def test_a_shard_that_disagrees_on_schema_is_declined_and_left_out(
    handler: ParquetHandler,
) -> None:
    """Drift inside one partitioned table is declined, not folded into the
    first schema — and the FileSet must not glob the shard back in."""
    metas = [
        _pq_meta("part-00000.parquet", "events/part-00000.parquet", _BOUNDARIES),
        _pq_meta("part-00001.parquet", "events/part-00001.parquet", _BOUNDARIES),
        _pq_meta("part-00002.parquet", "events/part-00002.parquet", _CELLS),
    ]

    built = handler.build_croissant(metas, ["f0", "f1", "f2"])

    (record_set,) = built.record_sets
    assert [f.name for f in record_set.fields] == ["cell_id", "vertex_x"]

    (refusal,) = built.declined
    assert refusal.index == 2
    assert refusal.reason is Reason.PARTITION_SCHEMA_CONFLICT
    assert "3 columns, expected 2" in refusal.detail

    assert built.file_sets[0].includes == [
        "events/part-00000.parquet",
        "events/part-00001.parquet",
    ]


def test_same_named_directories_do_not_collide(handler: ParquetHandler) -> None:
    """Hive layouts repeat a directory name under different parents."""
    metas = [
        _pq_meta("part-00000.parquet", "a/events/part-00000.parquet"),
        _pq_meta("part-00001.parquet", "a/events/part-00001.parquet"),
        _pq_meta("part-00000.parquet", "b/events/part-00000.parquet"),
        _pq_meta("part-00001.parquet", "b/events/part-00001.parquet"),
    ]
    built = handler.build_croissant(metas, ["f0", "f1", "f2", "f3"])
    filesets, record_sets, _ = built.file_sets, built.record_sets, built.declined

    assert len({f.id for f in filesets}) == 2
    assert len({r.id for r in record_sets}) == 2
    # Each record set's fields must reach its own FileSet, not the first one.
    for record_set, fileset in zip(record_sets, filesets):
        assert record_set.fields[0].source.file_set == fileset.id


def test_a_shared_directory_names_tables_after_their_files(
    handler: ParquetHandler,
) -> None:
    """The parent directory only names a table while it is the directory's only one."""
    metas = [
        _pq_meta("part-00000.parquet", "events/part-00000.parquet", _BOUNDARIES),
        _pq_meta("part-00001.parquet", "events/part-00001.parquet", _BOUNDARIES),
        _pq_meta("lookup.parquet", "events/lookup.parquet", _CELLS),
    ]
    built = handler.build_croissant(metas, ["f0", "f1", "f2"])
    filesets, record_sets, _ = built.file_sets, built.record_sets, built.declined

    assert sorted(r.name for r in record_sets) == ["lookup", "part"]
    # The glob would reach the neighbouring table, so the shards are listed.
    assert filesets[0].includes == [
        "events/part-00000.parquet",
        "events/part-00001.parquet",
    ]


def test_parquet_build_croissant_standalone(handler: ParquetHandler) -> None:
    built = handler.build_croissant(
        [_pq_meta("data.parquet", "data.parquet")], ["file_0"]
    )
    filesets, record_sets, _ = built.file_sets, built.record_sets, built.declined
    assert filesets == []
    assert record_sets[0].name == "data"


def test_parquet_build_croissant_single_file_in_subdir(handler: ParquetHandler) -> None:
    built = handler.build_croissant(
        [_pq_meta("part-00000.parquet", "events/part-00000.parquet")], ["file_0"]
    )
    filesets, record_sets, _ = built.file_sets, built.record_sets, built.declined
    assert filesets == []
    assert record_sets[0].name == "events"


def test_parquet_build_croissant_partitioned(handler: ParquetHandler) -> None:
    metas = [
        _pq_meta("part-00000.parquet", "events/part-00000.parquet"),
        _pq_meta("part-00001.parquet", "events/part-00001.parquet"),
    ]
    built = handler.build_croissant(metas, ["file_0", "file_1"])
    filesets, record_sets, _ = built.file_sets, built.record_sets, built.declined
    assert len(filesets) == 1
    assert len(record_sets) == 1
    assert record_sets[0].name == "events"
    assert "events/*.parquet" in filesets[0].includes


def test_parquet_array_shape_fixed_vs_variable(
    handler: ParquetHandler, tmp_path: Path
) -> None:
    """Fixed-size lists report exact dim; variable-length lists report -1."""
    schema = pa.schema(
        [
            ("embedding", pa.list_(pa.float32(), 384)),  # fixed-size: dim 384
            ("tags", pa.list_(pa.string())),  # variable-length
        ]
    )
    table = pa.table(
        {"embedding": [[0.0] * 384], "tags": [["a", "b"]]},
        schema=schema,
    )
    path = tmp_path / "vectors.parquet"
    pq.write_table(table, str(path))

    meta = handler.extract(make_source(path))
    meta["relative_path"] = "vectors.parquet"
    built = handler.build_croissant([meta], ["file_0"])
    _, record_sets, _ = built.file_sets, built.record_sets, built.declined

    fields = {f.name: f for f in record_sets[0].fields}
    assert fields["embedding"].is_array is True
    assert fields["embedding"].array_shape == "384"
    assert fields["tags"].is_array is True
    assert fields["tags"].array_shape == "-1"


def test_shards_are_grouped_on_the_arrow_schema_not_the_croissant_types(
    handler: ParquetHandler,
) -> None:
    """Two timestamp units are one Croissant type and two different tables."""
    metas = []
    for i, unit in enumerate(("s", "ns")):
        meta = _pq_meta(f"part-0000{i}.parquet", f"events/part-0000{i}.parquet")
        meta["column_types"] = {"t": "sc:DateTime"}
        meta["arrow_schema"] = pa.schema([pa.field("t", pa.timestamp(unit))])
        metas.append(meta)

    built = handler.build_croissant(metas, ["f0", "f1"])
    filesets, record_sets, conflicts = (
        built.file_sets,
        built.record_sets,
        built.declined,
    )

    assert filesets == []
    assert conflicts == ()
    assert len({r.id for r in record_sets}) == 2


def test_root_level_files_are_never_declined(handler: ParquetHandler) -> None:
    """Root files never pair, so a schema difference between them settles nothing."""
    metas = [
        _pq_meta("part-00000.parquet", "part-00000.parquet", _BOUNDARIES),
        _pq_meta("part-00001.parquet", "part-00001.parquet", _CELLS),
    ]
    built = handler.build_croissant(metas, ["f0", "f1"])
    filesets, record_sets, conflicts = (
        built.file_sets,
        built.record_sets,
        built.declined,
    )

    assert filesets == []
    assert conflicts == ()
    assert sorted(r.name for r in record_sets) == ["part-00000", "part-00001"]


def test_two_templates_reducing_to_one_stem_get_distinct_ids(
    handler: ParquetHandler,
) -> None:
    """``part-000`` and ``part_000`` share a directory, a stem, and every parent."""
    metas = [
        _pq_meta("part-000.parquet", "d/part-000.parquet", _BOUNDARIES),
        _pq_meta("part-001.parquet", "d/part-001.parquet", _BOUNDARIES),
        _pq_meta("part_000.parquet", "d/part_000.parquet", _CELLS),
        _pq_meta("part_001.parquet", "d/part_001.parquet", _CELLS),
    ]
    built = handler.build_croissant(metas, ["f0", "f1", "f2", "f3"])
    filesets, record_sets, _ = built.file_sets, built.record_sets, built.declined

    assert len({f.id for f in filesets}) == 2
    assert len({r.id for r in record_sets}) == 2


def test_a_number_inside_a_word_is_not_a_shard_index(handler: ParquetHandler) -> None:
    """``assay1`` and ``assay2`` are two tables; only a separated run is an index."""
    metas = [
        _pq_meta("assay1.parquet", "d/assay1.parquet", _BOUNDARIES),
        _pq_meta("assay2.parquet", "d/assay2.parquet", _BOUNDARIES),
    ]
    built = handler.build_croissant(metas, ["f0", "f1"])
    filesets, record_sets, conflicts = (
        built.file_sets,
        built.record_sets,
        built.declined,
    )

    assert filesets == []
    assert conflicts == ()
    assert sorted(r.name for r in record_sets) == ["assay1", "assay2"]


def test_differing_years_are_not_merged_or_declined(handler: ParquetHandler) -> None:
    """``report2024`` and ``report2025`` disagree on schema and both survive."""
    metas = [
        _pq_meta("report2024.parquet", "d/report2024.parquet", _BOUNDARIES),
        _pq_meta("report2025.parquet", "d/report2025.parquet", _CELLS),
    ]
    built = handler.build_croissant(metas, ["f0", "f1"])
    _, record_sets, conflicts = built.file_sets, built.record_sets, built.declined

    assert conflicts == ()
    assert sorted(r.name for r in record_sets) == ["report2024", "report2025"]


def test_a_schema_tie_declines_nothing_and_describes_both_groups(
    handler: ParquetHandler,
) -> None:
    """Two-versus-two is no majority, so neither half may be thrown away."""
    metas = [
        _pq_meta("part-000.parquet", "t/part-000.parquet", _CELLS),
        _pq_meta("part-001.parquet", "t/part-001.parquet", _CELLS),
        _pq_meta("part-002.parquet", "t/part-002.parquet", _BOUNDARIES),
        _pq_meta("part-003.parquet", "t/part-003.parquet", _BOUNDARIES),
    ]
    built = handler.build_croissant(metas, [f"f{i}" for i in range(4)])

    assert built.declined == ()
    assert len(built.record_sets) == 2


@pytest.mark.parametrize(
    "other, merges",
    [
        (
            pa.schema(
                [
                    pa.field("id", pa.int64(), metadata={b"note": b"shard 1"}),
                    pa.field("v", pa.float64()),
                ]
            ),
            True,
        ),
        (
            pa.schema(
                [
                    pa.field("id", pa.int64(), nullable=False),
                    pa.field("v", pa.float64()),
                ]
            ),
            False,
        ),
    ],
    ids=["field-metadata", "nullability"],
)
def test_grouping_agrees_with_arrow_on_schema_identity(
    handler: ParquetHandler, other, merges: bool
) -> None:
    plain = pa.schema([pa.field("id", pa.int64()), pa.field("v", pa.float64())])
    assert plain.equals(other) is merges

    metas = []
    for i, schema in enumerate((plain, other)):
        meta = _pq_meta(f"part-00{i}.parquet", f"t/part-00{i}.parquet")
        meta["arrow_schema"] = schema
        metas.append(meta)

    built = handler.build_croissant(metas, ["f0", "f1"])

    assert built.declined == ()
    assert len(built.record_sets) == (1 if merges else 2)
    assert len(built.file_sets) == (1 if merges else 0)


def test_a_derived_fileset_id_cannot_collide_with_a_record_set(tmp_path: Path) -> None:
    """``foo`` partitioned yields FileSet ``foo-fileset``; a standalone file
    actually named ``foo-fileset`` yields a RecordSet of the same id. The
    RecordSet keeps the name it earned and the derived id is the one that moves."""
    from croissant_baker.metadata_generator import MetadataGenerator

    table = pa.table({"id": pa.array([1, 2]), "v": pa.array(["a", "b"])})
    (tmp_path / "foo").mkdir()
    for i in range(2):
        pq.write_table(table, str(tmp_path / "foo" / f"part-00{i}.parquet"))
    pq.write_table(table, str(tmp_path / "foo-fileset.parquet"))

    document = MetadataGenerator(
        dataset_path=str(tmp_path), name="c"
    ).generate_metadata()

    record_ids = {r["@id"] for r in document["recordSet"]}
    file_set_ids = {
        n["@id"] for n in document["distribution"] if n["@type"] == "cr:FileSet"
    }

    assert "foo-fileset" in record_ids
    assert "foo-fileset__2" in file_set_ids
    assert not record_ids & file_set_ids


@pytest.mark.parametrize("suffix", ["", ".gz", ".bz2", ".xz"])
def test_the_magic_check_reads_the_footer_through_any_wrapper(
    handler: ParquetHandler, sample_parquet: Path, tmp_path: Path, suffix: str
) -> None:
    """Every registered compression supports seeking from the end of a stream.

    Both directions, because the head of a Parquet file is also ``PAR1``: a
    check that read the head twice would accept the truncated file too, and
    the seek would never be exercised.
    """
    payload = sample_parquet.read_bytes()
    whole = write_wrapped(tmp_path, "whole.parquet", payload, suffix)
    footerless = write_wrapped(
        tmp_path, "footerless.parquet", payload[:-4] + b"\x00" * 4, suffix
    )

    assert handler.claims(make_source(whole)) is True
    assert handler.claims(make_source(footerless)) is False


def test_a_stream_that_refuses_to_seek_declines_rather_than_raising(
    handler: ParquetHandler, sample_parquet: Path, tmp_path: Path
) -> None:
    """A third-party compression is free to refuse SEEK_END; the claim is not.

    None of the built-in three do, so the guard is only reachable through
    register_compression — which is public, so it stays.
    """

    class _NoSeekEnd(io.BufferedReader):
        def seek(self, offset, whence=0):
            if whence == 2:
                raise ValueError("cannot seek from the end of this stream")
            return super().seek(offset, whence)

    def _opener(path, mode="rb", **kwargs):
        return _NoSeekEnd(open(path, "rb"))

    stubborn = compression.Compression(
        "stubborn", ".stub", "application/x-stubborn", _opener
    )
    compression.register_compression(stubborn)
    try:
        path = tmp_path / "table.parquet.stub"
        path.write_bytes(sample_parquet.read_bytes())

        assert handler.claims(make_source(path)) is False
    finally:
        compression._registry.remove(stubborn)


def test_the_parquet_reader_is_closed_after_extraction(
    handler: ParquetHandler, sample_parquet: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for #54: a bake of ten thousand shards must not hold ten
    thousand readers open. The reader is opened in a ``with``, and nothing but
    a test can see that it is."""
    opened = []

    class _Spy(parquet_handler_module.ParquetFile):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.was_closed = False
            opened.append(self)

        def close(self, *args, **kwargs):
            self.was_closed = True
            return super().close(*args, **kwargs)

    monkeypatch.setattr(parquet_handler_module, "ParquetFile", _Spy)

    handler.extract(make_source(sample_parquet))

    assert opened, "the handler never opened a ParquetFile"
    # Not reader.closed: the reader wraps the stream the outer `with` closes,
    # so it reads as closed either way. Only close() distinguishes them.
    assert all(pf.was_closed for pf in opened)
