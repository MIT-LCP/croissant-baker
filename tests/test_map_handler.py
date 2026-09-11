"""Tests for the MRC / CCP4 map handler."""

from __future__ import annotations

import struct
from pathlib import Path

import mlcroissant as mlc
import pytest

from croissant_baker.handlers.registry import select_handler
from croissant_baker.handlers.structural_biology.map_handler import MRCHandler
from croissant_baker.sources import make_source

#: Little-endian and big-endian machine stamps, as MRC2014 writes them.
LITTLE_STAMP = b"\x44\x44\x00\x00"
BIG_STAMP = b"\x11\x11\x00\x00"


def mrc_header(
    *,
    nx: int = 4,
    ny: int = 5,
    nz: int = 6,
    mode: int = 2,
    mx: int | None = None,
    my: int | None = None,
    mz: int | None = None,
    cell: tuple = (12.0, 15.0, 18.0),
    ispg: int = 1,
    nversion: int = 20140,
    densities: tuple = (-1.5, 2.5, 0.25),
    rms: float = 0.75,
    labels: tuple = ("a hand written map",),
    endian: str = "<",
    stamp: bytes | None = None,
) -> bytes:
    """The 1024 bytes an MRC2014 header is, written by hand."""
    mx = nx if mx is None else mx
    my = ny if my is None else my
    mz = nz if mz is None else mz
    stamp = (LITTLE_STAMP if endian == "<" else BIG_STAMP) if stamp is None else stamp

    header = struct.pack(f"{endian}4i", nx, ny, nz, mode)
    header += struct.pack(f"{endian}3i", 0, 0, 0)
    header += struct.pack(f"{endian}3i", mx, my, mz)
    header += struct.pack(f"{endian}3f", *cell)
    header += struct.pack(f"{endian}3f", 90.0, 90.0, 90.0)
    header += struct.pack(f"{endian}3i", 1, 2, 3)
    header += struct.pack(f"{endian}3f", *densities)
    header += struct.pack(f"{endian}2i", ispg, 0)
    # Words 25 to 49 are the extra block; nversion is word 28, twelve bytes in.
    extra = bytearray(100)
    extra[12:16] = struct.pack(f"{endian}i", nversion)
    header += bytes(extra)
    header += struct.pack(f"{endian}3f", 0.0, 0.0, 0.0)
    header += b"MAP "
    header += stamp
    header += struct.pack(f"{endian}f", rms)
    header += struct.pack(f"{endian}i", len(labels))
    label_block = bytearray(b" " * 800)
    for i, text in enumerate(labels):
        encoded = text.encode("ascii")[:80]
        label_block[i * 80 : i * 80 + len(encoded)] = encoded
    header += bytes(label_block)
    assert len(header) == 1024
    return header


def write_map(path: Path, payload: bytes) -> Path:
    path.write_bytes(payload)
    return path


@pytest.fixture
def handler() -> MRCHandler:
    return MRCHandler()


# claims


@pytest.mark.parametrize("suffix", [".mrc", ".mrcs", ".ccp4"])
def test_the_extension_alone_claims_the_specific_suffixes(
    handler: MRCHandler, tmp_path: Path, suffix: str
) -> None:
    path = write_map(tmp_path / f"probe{suffix}", mrc_header())
    assert handler.claims(make_source(path))


def test_a_map_is_claimed_when_it_carries_the_signature(
    handler: MRCHandler, tmp_path: Path
) -> None:
    path = write_map(tmp_path / "probe.map", mrc_header())
    assert handler.claims(make_source(path))


def test_a_map_is_routed_to_this_handler(tmp_path: Path) -> None:
    """Registered in ``builtin_handlers``, so a bake reaches this handler at all."""
    path = write_map(tmp_path / "volume.mrc", mrc_header())

    assert isinstance(select_handler(path).handler, MRCHandler)


def test_a_map_without_the_signature_is_not_claimed(
    handler: MRCHandler, tmp_path: Path
) -> None:
    """``.map`` is a generic suffix, so the signature has to settle it."""
    header = bytearray(mrc_header())
    header[208:212] = b"\x00\x00\x00\x00"
    path = write_map(tmp_path / "probe.map", bytes(header))
    assert not handler.claims(make_source(path))


def test_a_mrc_without_the_signature_is_still_claimed(
    handler: MRCHandler, tmp_path: Path
) -> None:
    """An older CCP4 writer may omit it, and unreadable beats unclaimed."""
    header = bytearray(mrc_header())
    header[208:212] = b"\x00\x00\x00\x00"
    path = write_map(tmp_path / "probe.mrc", bytes(header))
    assert handler.claims(make_source(path))


def test_an_upper_case_suffix_claims_identically(
    handler: MRCHandler, tmp_path: Path
) -> None:
    quiet = write_map(tmp_path / "probe.mrc", mrc_header())
    shouted = write_map(tmp_path / "PROBE.MRC", mrc_header())
    assert handler.claims(make_source(shouted)) is handler.claims(make_source(quiet))


def test_another_format_is_declined(handler: MRCHandler, tmp_path: Path) -> None:
    path = write_map(tmp_path / "probe.nii", mrc_header())
    assert not handler.claims(make_source(path))


# extract


def test_extract_reports_the_grid_and_the_file_identity(
    handler: MRCHandler, tmp_path: Path
) -> None:
    path = write_map(tmp_path / "grid.mrc", mrc_header(nx=4, ny=5, nz=6))
    meta = handler.extract(make_source(path))

    assert meta["file_name"] == "grid.mrc"
    assert meta["encoding_format"] == "application/x-mrc"
    assert meta["file_size"] == 1024
    assert len(meta["sha256"]) == 64

    props = meta["mrc_properties"]
    assert props["dim_x"] == 4
    assert props["dim_y"] == 5
    assert props["dim_z"] == 6


@pytest.mark.parametrize(
    ("mode", "dtype"),
    [
        (0, "int8"),
        (1, "int16"),
        (2, "float32"),
        (3, "complex int16"),
        (4, "complex float32"),
        (6, "uint16"),
        (12, "float16"),
        (101, "4-bit unsigned"),
    ],
)
def test_every_documented_mode_names_its_data_type(
    handler: MRCHandler, tmp_path: Path, mode: int, dtype: str
) -> None:
    path = write_map(tmp_path / "mode.mrc", mrc_header(mode=mode))
    props = handler.extract(make_source(path))["mrc_properties"]

    assert props["mode"] == mode
    assert props["data_dtype"] == dtype


def test_a_big_endian_header_reads_the_same_values(
    handler: MRCHandler, tmp_path: Path
) -> None:
    """The machine stamp, not the host, decides how the words are read."""
    path = write_map(tmp_path / "big.mrc", mrc_header(nx=4, ny=5, nz=6, endian=">"))
    props = handler.extract(make_source(path))["mrc_properties"]

    assert (props["dim_x"], props["dim_y"], props["dim_z"]) == (4, 5, 6)
    assert props["data_dtype"] == "float32"


def test_an_unrecognised_stamp_falls_back_to_little_endian(
    handler: MRCHandler, tmp_path: Path
) -> None:
    path = write_map(
        tmp_path / "odd.mrc", mrc_header(nx=4, ny=5, nz=6, stamp=b"\x00\x00\x00\x00")
    )
    props = handler.extract(make_source(path))["mrc_properties"]

    assert (props["dim_x"], props["dim_y"], props["dim_z"]) == (4, 5, 6)


def test_the_data_that_follows_the_header_is_never_read(
    handler: MRCHandler, tmp_path: Path
) -> None:
    """A map is gigabytes; only the first 1024 of them describe it."""
    payload = mrc_header(nx=4, ny=5, nz=6) + b"\x00" * (4 * 5 * 6 * 4)
    path = write_map(tmp_path / "withdata.mrc", payload)
    props = handler.extract(make_source(path))["mrc_properties"]

    assert props["dim_x"] == 4


def test_voxel_size_divides_the_cell_by_the_sampling(
    handler: MRCHandler, tmp_path: Path
) -> None:
    path = write_map(
        tmp_path / "voxel.mrc",
        mrc_header(nx=4, ny=5, nz=6, cell=(12.0, 15.0, 18.0)),
    )
    props = handler.extract(make_source(path))["mrc_properties"]

    assert props["voxel_size_x"] == pytest.approx(3.0)
    assert props["voxel_size_y"] == pytest.approx(3.0)
    assert props["voxel_size_z"] == pytest.approx(3.0)


def test_voxel_size_is_omitted_when_the_sampling_is_zero(
    handler: MRCHandler, tmp_path: Path
) -> None:
    """A map with no cell says nothing about the size of a voxel."""
    path = write_map(tmp_path / "nocell.mrc", mrc_header(mx=0, my=0, mz=0))
    props = handler.extract(make_source(path))["mrc_properties"]

    assert "voxel_size_x" not in props
    assert "voxel_size_y" not in props
    assert "voxel_size_z" not in props


def test_a_zero_space_group_is_a_stack_of_images(
    handler: MRCHandler, tmp_path: Path
) -> None:
    path = write_map(tmp_path / "stack.mrcs", mrc_header(nz=40, ispg=0))
    props = handler.extract(make_source(path))["mrc_properties"]

    assert props["kind"] == "image stack"
    assert props["n_images"] == 40


def test_a_single_image_is_still_a_stack_of_one(
    handler: MRCHandler, tmp_path: Path
) -> None:
    path = write_map(tmp_path / "one.mrc", mrc_header(nz=1, ispg=0))
    props = handler.extract(make_source(path))["mrc_properties"]

    assert props["kind"] == "image stack"
    assert props["n_images"] == 1


def test_a_crystallographic_space_group_is_a_volume(
    handler: MRCHandler, tmp_path: Path
) -> None:
    path = write_map(tmp_path / "volume.ccp4", mrc_header(ispg=19))
    props = handler.extract(make_source(path))["mrc_properties"]

    assert props["kind"] == "volume"
    assert props["space_group"] == 19
    assert "n_images" not in props


def test_the_reserved_range_is_a_stack_of_volumes(
    handler: MRCHandler, tmp_path: Path
) -> None:
    path = write_map(tmp_path / "sub.mrc", mrc_header(nz=24, mz=6, ispg=401))
    props = handler.extract(make_source(path))["mrc_properties"]

    assert props["kind"] == "volume stack"
    assert props["n_images"] == 4


def test_the_first_label_is_reported_stripped(
    handler: MRCHandler, tmp_path: Path
) -> None:
    path = write_map(
        tmp_path / "labelled.mrc", mrc_header(labels=("written by hand", "and again"))
    )
    props = handler.extract(make_source(path))["mrc_properties"]

    assert props["n_labels"] == 2
    assert props["first_label"] == "written by hand"


def test_a_map_with_no_labels_reports_none(handler: MRCHandler, tmp_path: Path) -> None:
    path = write_map(tmp_path / "bare.mrc", mrc_header(labels=()))
    props = handler.extract(make_source(path))["mrc_properties"]

    assert props["n_labels"] == 0
    assert "first_label" not in props


def test_the_density_statistics_are_kept_as_floats(
    handler: MRCHandler, tmp_path: Path
) -> None:
    path = write_map(tmp_path / "stats.mrc", mrc_header(densities=(-1.5, 2.5, 0.25)))
    props = handler.extract(make_source(path))["mrc_properties"]

    assert props["density_min"] == pytest.approx(-1.5)
    assert props["density_max"] == pytest.approx(2.5)
    assert props["density_mean"] == pytest.approx(0.25)


def test_the_format_version_is_reported_when_the_writer_set_it(
    handler: MRCHandler, tmp_path: Path
) -> None:
    path = write_map(tmp_path / "versioned.mrc", mrc_header(nversion=20140))
    props = handler.extract(make_source(path))["mrc_properties"]

    assert props["nversion"] == 20140


def test_an_unset_format_version_is_omitted(
    handler: MRCHandler, tmp_path: Path
) -> None:
    """Zero is what a pre-2014 writer leaves, not a version anyone declared."""
    path = write_map(tmp_path / "old.mrc", mrc_header(nversion=0))
    props = handler.extract(make_source(path))["mrc_properties"]

    assert "nversion" not in props


# refusals


def test_an_unknown_mode_raises_naming_the_file(
    handler: MRCHandler, tmp_path: Path
) -> None:
    path = write_map(tmp_path / "weird.mrc", mrc_header(mode=77))

    with pytest.raises(ValueError) as caught:
        handler.extract(make_source(path))

    assert "weird.mrc" in str(caught.value)
    assert "77" in str(caught.value)


def test_a_truncated_header_raises_naming_the_file(
    handler: MRCHandler, tmp_path: Path
) -> None:
    path = write_map(tmp_path / "short.mrc", mrc_header()[:512])

    with pytest.raises(ValueError) as caught:
        handler.extract(make_source(path))

    assert "short.mrc" in str(caught.value)


@pytest.mark.parametrize("dims", [(0, 5, 6), (4, -1, 6), (4, 5, 0)])
def test_a_non_positive_dimension_raises_naming_the_file(
    handler: MRCHandler, tmp_path: Path, dims: tuple
) -> None:
    nx, ny, nz = dims
    path = write_map(tmp_path / "empty.mrc", mrc_header(nx=nx, ny=ny, nz=nz))

    with pytest.raises(ValueError) as caught:
        handler.extract(make_source(path))

    assert "empty.mrc" in str(caught.value)


def test_a_missing_file_raises_file_not_found(
    handler: MRCHandler, tmp_path: Path
) -> None:
    with pytest.raises(FileNotFoundError):
        handler.extract(make_source(tmp_path / "gone" / "absent.mrc"))


def test_garbage_bytes_raise_a_value_error_naming_the_file(
    handler: MRCHandler, tmp_path: Path
) -> None:
    path = write_map(tmp_path / "junk.mrc", b"\x00\xff not a real file \xfe\x00")

    with pytest.raises(ValueError) as caught:
        handler.extract(make_source(path))

    assert "junk.mrc" in str(caught.value)


# a map a real writer produced


def test_a_map_written_by_gemmi_is_read_correctly(
    handler: MRCHandler, tmp_path: Path
) -> None:
    """A hand-written header proves the offsets agree with the spec; this
    proves they agree with what a widely used writer actually emits."""
    gemmi = pytest.importorskip("gemmi")
    numpy = pytest.importorskip("numpy")

    written = gemmi.Ccp4Map()
    written.grid = gemmi.FloatGrid(numpy.zeros((6, 5, 4), dtype=numpy.float32))
    written.grid.unit_cell = gemmi.UnitCell(12.0, 15.0, 18.0, 90.0, 90.0, 90.0)
    written.grid.spacegroup = gemmi.find_spacegroup_by_name("P 1")
    written.update_ccp4_header()
    path = tmp_path / "gemmi.map"
    written.write_ccp4_map(str(path))

    assert handler.claims(make_source(path))
    props = handler.extract(make_source(path))["mrc_properties"]

    assert (props["dim_x"], props["dim_y"], props["dim_z"]) == (6, 5, 4)
    assert props["data_dtype"] == "float32"
    assert props["space_group"] == 1
    assert props["kind"] == "volume"
    assert props["voxel_size_x"] == pytest.approx(2.0)
    assert props["voxel_size_y"] == pytest.approx(3.0)
    assert props["voxel_size_z"] == pytest.approx(4.5)


# build_croissant


def mrc_meta(name: str, **overrides) -> dict:
    props = {
        "dim_x": 128,
        "dim_y": 128,
        "dim_z": 64,
        "mode": 2,
        "data_dtype": "float32",
        "voxel_size_x": 1.05,
        "voxel_size_y": 1.05,
        "voxel_size_z": 1.05,
        "space_group": 1,
        "kind": "volume",
        "n_labels": 1,
        "density_min": -1.0,
        "density_max": 1.0,
        "density_mean": 0.0,
    }
    props.update(overrides)
    return {
        "file_name": name,
        "stored_name": name,
        "relative_path": name,
        "encoding_format": "application/x-mrc",
        "mrc_properties": props,
    }


def test_an_empty_batch_describes_nothing(handler: MRCHandler) -> None:
    result = handler.build_croissant([], [])

    assert result.file_sets == []
    assert result.record_sets == []
    assert result.declined == ()


def test_a_batch_gives_one_file_set_and_one_record_set(handler: MRCHandler) -> None:
    result = handler.build_croissant(
        [mrc_meta("one.mrc"), mrc_meta("two.mrc")], ["file_0", "file_1"]
    )

    assert len(result.file_sets) == 1
    assert len(result.record_sets) == 1
    assert result.file_sets[0].id == "mrc-files"
    assert result.record_sets[0].id == "mrc_maps"


def test_the_file_set_includes_only_the_suffixes_the_batch_carries(
    handler: MRCHandler,
) -> None:
    """A glob for a suffix nothing in the batch uses would promise files that
    are not there."""
    file_sets, _ = handler.build_croissant(
        [mrc_meta("stack.mrcs"), mrc_meta("map.mrc")], ["file_0", "file_1"]
    )

    assert file_sets[0].includes == ["**/*.mrc", "**/*.mrcs"]


def test_the_file_set_carries_the_encoding_format(handler: MRCHandler) -> None:
    file_sets, _ = handler.build_croissant([mrc_meta("one.mrc")], ["file_0"])

    assert file_sets[0].encoding_formats == ["application/x-mrc"]


def test_the_record_set_describes_the_grid(handler: MRCHandler) -> None:
    _, record_sets = handler.build_croissant([mrc_meta("one.mrc")], ["file_0"])
    names = [field.name for field in record_sets[0].fields]

    assert names == [
        "dim_x",
        "dim_y",
        "dim_z",
        "data_dtype",
        "voxel_size",
        "space_group",
        "kind",
    ]


def test_every_field_reads_the_file_set(handler: MRCHandler) -> None:
    _, record_sets = handler.build_croissant([mrc_meta("one.mrc")], ["file_0"])

    for field in record_sets[0].fields:
        assert field.source.file_set == "mrc-files"
        assert field.source.extract.file_property is mlc.FileProperty.content
        assert field.id.startswith("mrc_maps/")


def test_the_field_types_follow_the_property_they_carry(handler: MRCHandler) -> None:
    _, record_sets = handler.build_croissant([mrc_meta("one.mrc")], ["file_0"])
    types = {
        field.name: [str(t) for t in field.data_types]
        for field in record_sets[0].fields
    }

    assert types["dim_x"] == ["sc:Integer"]
    assert types["space_group"] == ["sc:Integer"]
    assert types["data_dtype"] == ["sc:Text"]
    assert types["voxel_size"] == ["sc:Text"]
    assert types["kind"] == ["sc:Text"]


def test_the_voxel_size_field_names_its_unit(handler: MRCHandler) -> None:
    _, record_sets = handler.build_croissant([mrc_meta("one.mrc")], ["file_0"])
    voxel = next(f for f in record_sets[0].fields if f.name == "voxel_size")

    assert "x, y, z in Angstrom" in voxel.description


def test_an_image_count_is_described_only_when_the_batch_holds_a_stack(
    handler: MRCHandler,
) -> None:
    volumes = mrc_meta("volume.mrc")
    stack = mrc_meta("stack.mrcs", kind="image stack", space_group=0, n_images=40)

    _, without = handler.build_croissant([volumes], ["file_0"])
    _, with_stack = handler.build_croissant([volumes, stack], ["file_0", "file_1"])

    assert "n_images" not in {f.name for f in without[0].fields}
    assert "n_images" in {f.name for f in with_stack[0].fields}


def test_the_batch_reads_the_same_whichever_file_came_first(
    handler: MRCHandler,
) -> None:
    """Batch order is discovery order, which is the filesystem's. A summary
    that listed what it saw first would make two bakes of one directory differ."""
    volume = mrc_meta("volume.mrc")
    stack = mrc_meta("stack.mrcs", kind="image stack", space_group=0, n_images=40)

    forward = handler.build_croissant([volume, stack], ["file_0", "file_1"])
    reversed_ = handler.build_croissant([stack, volume], ["file_1", "file_0"])

    assert [rs.description for rs in forward.record_sets] == [
        rs.description for rs in reversed_.record_sets
    ]
    assert [fs.description for fs in forward.file_sets] == [
        fs.description for fs in reversed_.file_sets
    ]


def test_the_descriptions_summarise_the_batch(handler: MRCHandler) -> None:
    metas = [
        mrc_meta("volume.mrc"),
        mrc_meta("stack.mrcs", dim_z=200, kind="image stack", n_images=200),
        mrc_meta("other.mrc", dim_x=64, data_dtype="int16"),
    ]
    file_sets, record_sets = handler.build_croissant(
        metas, ["file_0", "file_1", "file_2"]
    )

    for description in (file_sets[0].description, record_sets[0].description):
        assert "3" in description
        assert "64-128" in description
    assert "float32" in record_sets[0].description
    assert "int16" in record_sets[0].description
    assert "image stack" in record_sets[0].description


# the contract the registry sweep will apply once this handler is registered


def test_the_handler_declares_what_the_docs_table_needs(handler: MRCHandler) -> None:
    assert handler.EXTENSIONS
    assert all(ext.startswith(".") for ext in handler.EXTENSIONS)
    assert handler.FORMAT_NAME
    assert handler.FORMAT_DESCRIPTION


def test_a_batch_read_from_disk_describes_something(
    handler: MRCHandler, tmp_path: Path
) -> None:
    """The path a bake takes: extract every file, then build over the batch."""
    files = [
        ("volume.mrc", mrc_header(ispg=19)),
        ("stack.mrcs", mrc_header(nz=40, ispg=0)),
    ]
    metas, ids = [], []
    for i, (name, payload) in enumerate(files):
        source = make_source(write_map(tmp_path / name, payload), Path(name))
        meta = handler.extract(source)
        meta["relative_path"] = name
        metas.append(meta)
        ids.append(f"file_{i}")

    result = handler.build_croissant(metas, ids)

    assert result.file_sets and result.record_sets
    assert result.declined == ()
    assert "n_images" in {field.name for field in result.record_sets[0].fields}
