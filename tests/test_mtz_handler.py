"""Tests for the MTZ reflection handler."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from croissant_baker.handlers.registry import select_handler
from croissant_baker.handlers.structural_biology.mtz_handler import (
    MTZHandler,
    column_data_type,
)
from croissant_baker.sources import make_source

gemmi = pytest.importorskip("gemmi")
numpy = pytest.importorskip("numpy")

#: Byte 8 carries the number format. A high nibble of 4 is little-endian IEEE,
#: 1 is big-endian.
LITTLE_STAMP = b"\x44\x41\x00\x00"
BIG_STAMP = b"\x11\x11\x00\x00"

#: The first five words of an MTZ are reserved, so a header can start at word 21.
HEADER_WORD = 21


def mtz_bytes(
    records: list,
    *,
    endian: str = "<",
    stamp: bytes | None = None,
    header_word: int = HEADER_WORD,
    end: bool = True,
) -> bytes:
    """A minimal MTZ: the magic, an offset, and 80-character header records."""
    stamp = (LITTLE_STAMP if endian == "<" else BIG_STAMP) if stamp is None else stamp
    start = (header_word - 1) * 4
    preamble = b"MTZ " + struct.pack(f"{endian}i", header_word) + stamp
    preamble += b"\x00" * (start - len(preamble))

    body = b""
    for record in [*records, *(["END"] if end else [])]:
        body += record.encode("ascii").ljust(80)[:80]
    return preamble + body


def write_mtz(path: Path, payload: bytes) -> Path:
    path.write_bytes(payload)
    return path


def gemmi_mtz(path: Path, *, title: str = "demo") -> Path:
    """A file a real MTZ writer produced, to check the offsets against."""
    mtz = gemmi.Mtz(with_base=True)
    mtz.spacegroup = gemmi.find_spacegroup_by_name("P 21 21 21")
    mtz.set_cell_for_all(gemmi.UnitCell(50, 60, 70, 90, 90, 90))
    mtz.add_dataset("native")
    mtz.add_column("FP", "F")
    mtz.add_column("SIGFP", "Q")
    rows = [[h, 0, 0, 100.0 + h, 5.0] for h in range(1, 6)]
    mtz.set_data(numpy.array(rows, dtype=numpy.float32))
    mtz.title = title
    mtz.write_to_file(str(path))
    return path


@pytest.fixture
def handler() -> MTZHandler:
    return MTZHandler()


@pytest.fixture
def written(tmp_path: Path) -> Path:
    return gemmi_mtz(tmp_path / "native.mtz")


# claims


def test_an_mtz_carrying_the_signature_is_claimed(
    handler: MTZHandler, written: Path
) -> None:
    assert handler.claims(make_source(written))


def test_an_mtz_is_routed_to_this_handler(written: Path) -> None:
    """Registered in ``builtin_handlers``, so a bake reaches this handler at all."""
    assert isinstance(select_handler(written).handler, MTZHandler)


def test_an_mtz_without_the_signature_is_not_claimed(
    handler: MTZHandler, tmp_path: Path
) -> None:
    path = write_mtz(tmp_path / "impostor.mtz", b"NOPE" + b"\x00" * 200)
    assert not handler.claims(make_source(path))


def test_an_upper_case_suffix_claims_identically(
    handler: MTZHandler, tmp_path: Path
) -> None:
    quiet = gemmi_mtz(tmp_path / "quiet.mtz")
    shouted = write_mtz(tmp_path / "SHOUTED.MTZ", quiet.read_bytes())
    assert handler.claims(make_source(shouted)) is handler.claims(make_source(quiet))


def test_another_format_is_declined(handler: MTZHandler, written: Path) -> None:
    other = written.with_suffix(".mrc")
    other.write_bytes(written.read_bytes())
    assert not handler.claims(make_source(other))


# extract


def test_extract_reports_the_file_identity(handler: MTZHandler, written: Path) -> None:
    meta = handler.extract(make_source(written))

    assert meta["file_name"] == "native.mtz"
    assert meta["encoding_format"] == "application/x-mtz"
    assert meta["file_size"] > 0
    assert len(meta["sha256"]) == 64


def test_the_parsed_header_agrees_with_gemmi(
    handler: MTZHandler, written: Path
) -> None:
    """A hand-built file proves the offsets follow the spec; this proves they
    follow what a widely used writer emits, and what it reads back."""
    reference = gemmi.read_mtz_file(str(written), with_data=False)
    props = handler.extract(make_source(written))["mtz_properties"]

    assert props["title"] == reference.title
    assert props["n_reflections"] == reference.nreflections
    assert props["space_group"] == reference.spacegroup.hm
    assert props["unit_cell"] == pytest.approx(
        (
            reference.cell.a,
            reference.cell.b,
            reference.cell.c,
            reference.cell.alpha,
            reference.cell.beta,
            reference.cell.gamma,
        )
    )
    assert [(label, letter) for label, letter, _ in props["columns"]] == [
        (column.label, column.type) for column in reference.columns
    ]
    assert props["resolution_high_angstrom"] == pytest.approx(
        reference.resolution_high()
    )
    assert props["resolution_low_angstrom"] == pytest.approx(reference.resolution_low())


def test_the_columns_carry_their_dataset(handler: MTZHandler, written: Path) -> None:
    props = handler.extract(make_source(written))["mtz_properties"]

    assert props["columns"][0] == ("H", "H", 0)
    assert props["columns"][3] == ("FP", "F", 1)
    assert props["columns"][4] == ("SIGFP", "Q", 1)


def test_a_dataset_is_reported_with_its_name_and_wavelength(
    handler: MTZHandler, written: Path
) -> None:
    """The two things a column's description and the record set's are built
    from; the project and the crystal a dataset belongs to are read by nobody."""
    props = handler.extract(make_source(written))["mtz_properties"]
    native = next(d for d in props["datasets"] if d["id"] == 1)

    assert native["name"] == "native"
    assert native["wavelength"] == pytest.approx(0.0)


def test_a_big_endian_header_reads_the_same_values(
    handler: MTZHandler, tmp_path: Path
) -> None:
    """The machine stamp decides how the offset word is read, not the host."""
    records = [
        "VERS MTZ:V1.1",
        "TITLE hand built",
        "NCOL        4          120        0",
        "CELL    50.0000   60.0000   70.0000   90.0000   90.0000   90.0000",
        "SYMINF   4  4 P    19           'P 21 21 21' PG222",
        "RESO 0.000400000000       0.010000000000",
        "COLUMN H                              H       1.0       5.0    0",
        "COLUMN K                              H       0.0       0.0    0",
        "COLUMN L                              H       0.0       0.0    0",
        "COLUMN FP                             F     101.0     105.0    1",
        "DATASET       1 native",
    ]
    path = write_mtz(tmp_path / "big.mtz", mtz_bytes(records, endian=">"))
    props = handler.extract(make_source(path))["mtz_properties"]

    assert props["n_reflections"] == 120
    assert props["title"] == "hand built"
    assert props["space_group"] == "P 21 21 21"
    assert props["unit_cell"] == pytest.approx((50.0, 60.0, 70.0, 90.0, 90.0, 90.0))
    assert props["resolution_low_angstrom"] == pytest.approx(50.0)
    assert props["resolution_high_angstrom"] == pytest.approx(10.0)


def test_an_unrecognised_stamp_falls_back_to_little_endian(
    handler: MTZHandler, tmp_path: Path
) -> None:
    records = ["NCOL 1 7 0", "COLUMN FP F 0.0 1.0 0"]
    path = write_mtz(
        tmp_path / "odd.mtz", mtz_bytes(records, stamp=b"\x00\x00\x00\x00")
    )
    props = handler.extract(make_source(path))["mtz_properties"]

    assert props["n_reflections"] == 7


def test_a_zero_resolution_word_is_not_turned_into_a_distance(
    handler: MTZHandler, tmp_path: Path
) -> None:
    """1/sqrt(0) is not a resolution, it is a field the writer left unset."""
    records = ["NCOL 1 7 0", "RESO 0.000000 0.000000", "COLUMN FP F 0.0 1.0 0"]
    path = write_mtz(tmp_path / "nores.mtz", mtz_bytes(records))
    props = handler.extract(make_source(path))["mtz_properties"]

    assert "resolution_low_angstrom" not in props
    assert "resolution_high_angstrom" not in props


def test_records_after_end_are_not_read(handler: MTZHandler, tmp_path: Path) -> None:
    """Batch headers follow END, and they describe no column."""
    payload = mtz_bytes(["NCOL 1 7 0", "COLUMN FP F 0.0 1.0 0"])
    payload += "COLUMN LATER F 0.0 1.0 0".encode("ascii").ljust(80)
    path = write_mtz(tmp_path / "trailing.mtz", payload)
    props = handler.extract(make_source(path))["mtz_properties"]

    assert [label for label, _, _ in props["columns"]] == ["FP"]


def test_unknown_header_keywords_are_ignored(
    handler: MTZHandler, tmp_path: Path
) -> None:
    records = [
        "NCOL 1 7 0",
        "SORT    0   0   0   0   0",
        "SYMM X,Y,Z",
        "VALM NAN",
        "COLSRC FP  CREATED         2024-01-01",
        "MYSTERY whatever it says",
        "COLUMN FP F 0.0 1.0 0",
    ]
    path = write_mtz(tmp_path / "noisy.mtz", mtz_bytes(records))
    props = handler.extract(make_source(path))["mtz_properties"]

    assert [label for label, _, _ in props["columns"]] == ["FP"]


# refusals


def test_a_header_offset_past_the_end_raises_naming_the_file(
    handler: MTZHandler, tmp_path: Path
) -> None:
    path = write_mtz(
        tmp_path / "lost.mtz", mtz_bytes(["NCOL 1 7 0"], header_word=100000)
    )

    with pytest.raises(ValueError) as caught:
        handler.extract(make_source(path))

    assert "lost.mtz" in str(caught.value)


def test_a_truncated_file_raises_naming_the_file(
    handler: MTZHandler, tmp_path: Path
) -> None:
    path = write_mtz(tmp_path / "short.mtz", b"MTZ ")

    with pytest.raises(ValueError) as caught:
        handler.extract(make_source(path))

    assert "short.mtz" in str(caught.value)


def test_a_header_declaring_no_column_raises_naming_the_file(
    handler: MTZHandler, tmp_path: Path
) -> None:
    path = write_mtz(tmp_path / "empty.mtz", mtz_bytes(["NCOL 0 0 0"]))

    with pytest.raises(ValueError) as caught:
        handler.extract(make_source(path))

    assert "empty.mtz" in str(caught.value)


def test_a_missing_file_raises_file_not_found(
    handler: MTZHandler, tmp_path: Path
) -> None:
    with pytest.raises(FileNotFoundError):
        handler.extract(make_source(tmp_path / "gone" / "absent.mtz"))


def test_garbage_bytes_raise_a_value_error_naming_the_file(
    handler: MTZHandler, tmp_path: Path
) -> None:
    path = write_mtz(tmp_path / "junk.mtz", b"\x00\xff not a real file \xfe\x00")

    with pytest.raises(ValueError) as caught:
        handler.extract(make_source(path))

    assert "junk.mtz" in str(caught.value)


# column types


@pytest.mark.parametrize("letter", ["H", "I", "B", "Y"])
def test_the_counting_column_types_are_integers(letter: str) -> None:
    assert column_data_type(letter) == "sc:Integer"


@pytest.mark.parametrize("letter", list("FQJDPWAGLKMER"))
def test_every_other_declared_column_type_is_a_float(letter: str) -> None:
    assert column_data_type(letter) == "sc:Float"


def test_an_unknown_column_type_is_a_float(caplog: pytest.LogCaptureFixture) -> None:
    """MTZ gains column types, and a float is what the file stores anyway."""
    with caplog.at_level("DEBUG"):
        assert column_data_type("Z") == "sc:Float"

    assert "Z" in caplog.text


# build_croissant


def mtz_meta(name: str, path: str | None = None, **overrides) -> dict:
    props = {
        "title": "demo",
        "n_reflections": 4321,
        "unit_cell": (50.0, 60.0, 70.0, 90.0, 90.0, 90.0),
        "space_group": "P 21 21 21",
        "resolution_low_angstrom": 50.0,
        "resolution_high_angstrom": 1.8,
        "columns": [
            ("H", "H", 0),
            ("K", "H", 0),
            ("L", "H", 0),
            ("FP", "F", 1),
            ("SIGFP", "Q", 1),
        ],
        "datasets": [
            {"id": 0, "name": "HKL_base"},
            {"id": 1, "name": "native", "wavelength": 0.9795},
        ],
    }
    props.update(overrides)
    return {
        "file_name": name,
        "stored_name": name,
        "relative_path": path or name,
        "encoding_format": "application/x-mtz",
        "mtz_properties": props,
    }


def test_an_empty_batch_describes_nothing(handler: MTZHandler) -> None:
    result = handler.build_croissant([], [])

    assert result.file_sets == []
    assert result.record_sets == []
    assert result.declined == ()


def test_each_file_gets_its_own_record_set_and_no_file_set(
    handler: MTZHandler,
) -> None:
    """One MTZ is one table, and a FileSet spanning two would claim they share
    a column list."""
    result = handler.build_croissant(
        [mtz_meta("native.mtz"), mtz_meta("peak.mtz")], ["file_0", "file_1"]
    )

    assert result.file_sets == []
    assert [rs.id for rs in result.record_sets] == ["native", "peak"]


def test_two_files_with_one_basename_get_distinct_ids(handler: MTZHandler) -> None:
    metas = [
        mtz_meta("scaled.mtz", "xtal1/scaled.mtz"),
        mtz_meta("scaled.mtz", "xtal2/scaled.mtz"),
    ]

    _, record_sets = handler.build_croissant(metas, ["file_0", "file_1"])

    assert len({rs.id for rs in record_sets}) == 2


def test_one_field_per_column_named_by_its_label(handler: MTZHandler) -> None:
    _, record_sets = handler.build_croissant([mtz_meta("native.mtz")], ["file_0"])

    assert [f.name for f in record_sets[0].fields] == ["H", "K", "L", "FP", "SIGFP"]


def test_the_field_types_follow_the_column_type_letter(handler: MTZHandler) -> None:
    _, record_sets = handler.build_croissant([mtz_meta("native.mtz")], ["file_0"])
    types = {
        field.name: [str(t) for t in field.data_types]
        for field in record_sets[0].fields
    }

    assert types["H"] == ["sc:Integer"]
    assert types["FP"] == ["sc:Float"]
    assert types["SIGFP"] == ["sc:Float"]


def test_a_field_names_its_type_letter_and_its_dataset(handler: MTZHandler) -> None:
    _, record_sets = handler.build_croissant([mtz_meta("native.mtz")], ["file_0"])
    fp = next(f for f in record_sets[0].fields if f.name == "FP")

    assert "F" in fp.description
    assert "native" in fp.description


def test_every_field_reads_the_file_object_and_promises_no_extract(
    handler: MTZHandler,
) -> None:
    """mlcroissant cannot read MTZ, so an extract would be a promise nobody
    can keep."""
    _, record_sets = handler.build_croissant([mtz_meta("native.mtz")], ["file_0"])

    for field in record_sets[0].fields:
        assert field.source.file_object == "file_0"
        assert field.source.extract.column is None
        assert field.source.extract.file_property is None
        assert field.id.startswith("native/")


def test_columns_sharing_a_label_still_get_distinct_field_ids(
    handler: MTZHandler,
) -> None:
    metas = [mtz_meta("twin.mtz", columns=[("FP", "F", 1), ("FP", "F", 2)])]

    _, record_sets = handler.build_croissant(metas, ["file_0"])

    assert len({field.id for field in record_sets[0].fields}) == 2


def test_the_record_set_description_summarises_the_crystal(
    handler: MTZHandler,
) -> None:
    _, record_sets = handler.build_croissant([mtz_meta("native.mtz")], ["file_0"])
    description = record_sets[0].description

    assert "native.mtz" in description
    assert "4321" in description
    assert "P 21 21 21" in description
    assert "50" in description and "60" in description and "70" in description
    assert "1.8" in description
    assert "HKL_base" in description


def test_the_description_carries_the_title_the_writer_left(
    handler: MTZHandler,
) -> None:
    """The one line in the header that says what the file is for, in the
    depositor's own words."""
    _, record_sets = handler.build_croissant([mtz_meta("native.mtz")], ["file_0"])

    assert "Title: demo." in record_sets[0].description


def test_a_file_with_no_title_is_described_without_one(handler: MTZHandler) -> None:
    meta = mtz_meta("untitled.mtz", title="")

    _, record_sets = handler.build_croissant([meta], ["file_0"])

    assert "Title" not in record_sets[0].description


def test_a_dataset_is_described_at_the_wavelength_it_was_measured(
    handler: MTZHandler,
) -> None:
    """Which edge a dataset was collected at is what separates two datasets of
    one crystal, and nothing else in the record set says it."""
    _, record_sets = handler.build_croissant([mtz_meta("native.mtz")], ["file_0"])

    assert "native at 0.9795 Angstrom" in record_sets[0].description


def test_a_wavelength_nobody_recorded_is_left_out(handler: MTZHandler) -> None:
    """Zero is what a writer leaves for a dataset with no beam behind it, and
    reporting it would claim a measurement at zero Angstrom."""
    meta = mtz_meta("native.mtz")
    meta["mtz_properties"]["datasets"] = [
        {"id": 1, "name": "native", "wavelength": 0.0}
    ]

    _, record_sets = handler.build_croissant([meta], ["file_0"])

    assert "datasets native)" in record_sets[0].description


def test_an_unknown_resolution_is_left_out_of_the_description(
    handler: MTZHandler,
) -> None:
    meta = mtz_meta("norange.mtz")
    del meta["mtz_properties"]["resolution_low_angstrom"]
    del meta["mtz_properties"]["resolution_high_angstrom"]

    _, record_sets = handler.build_croissant([meta], ["file_0"])

    assert "resolution" not in record_sets[0].description.lower()


# the contract the registry sweep will apply once this handler is registered


def test_the_handler_declares_what_the_docs_table_needs(handler: MTZHandler) -> None:
    assert handler.EXTENSIONS
    assert all(ext.startswith(".") for ext in handler.EXTENSIONS)
    assert handler.FORMAT_NAME
    assert handler.FORMAT_DESCRIPTION


def test_a_batch_read_from_disk_describes_something(
    handler: MTZHandler, tmp_path: Path
) -> None:
    """The path a bake takes: extract every file, then build over the batch."""
    metas, ids = [], []
    for i, folder in enumerate(("xtal1", "xtal2")):
        (tmp_path / folder).mkdir()
        relative = Path(folder) / "scaled.mtz"
        meta = handler.extract(make_source(gemmi_mtz(tmp_path / relative), relative))
        meta["relative_path"] = str(relative)
        metas.append(meta)
        ids.append(f"file_{i}")

    result = handler.build_croissant(metas, ids)

    assert result.record_sets and result.file_sets == []
    assert result.declined == ()
    assert len({rs.id for rs in result.record_sets}) == 2
    assert [f.name for f in result.record_sets[0].fields] == [
        "H",
        "K",
        "L",
        "FP",
        "SIGFP",
    ]
