"""SerialEM mdoc: the schema a tilt series or a montage declares.

Unit level throughout, ``extract`` and ``build_croissant``, never a bake. The
checks the registry-wide sweep makes are replicated here, so a failure names
this handler rather than one parametrised case of a sweep over all of them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from croissant_baker.handlers.base_handler import BuildResult
from croissant_baker.handlers.registry import select_handler
from croissant_baker.handlers.structural_biology.mdoc_handler import MdocHandler
from croissant_baker.sources import make_source

HANDLER = MdocHandler()

#: A tilt series as SerialEM writes one: two title lines, global keys, then a
#: section per recorded image. Trimmed to three sections; nothing else about
#: the format changes with length.
TILT_SERIES = """[T = SerialEM: Digitized on a Titan Krios]
[T = Tilt axis angle = 85.30]

PixelSpacing = 1.35
Voltage = 300
ImageFile = tilt_series.mrc
ImageSize = 4096 4096
DataMode = 6

[ZValue = 0]
TiltAngle = -60.00
StagePosition = 12.5 -3.1
ExposureDose = 1.02
DateTime = 12-Mar-24  09:14:03
SubFramePath = X:\\data\\frames_0000.tif
NumSubFrames = 8

[ZValue = 1]
TiltAngle = -57.00
StagePosition = 12.6 -3.0
ExposureDose = 1.02
DateTime = 12-Mar-24  09:14:31
SubFramePath = X:\\data\\frames_0001.tif
NumSubFrames = 8

[ZValue = 2]
TiltAngle = -54.00
StagePosition = 12.7 -2.9
ExposureDose = 1.02
DateTime = 12-Mar-24  09:15:02
SubFramePath = X:\\data\\frames_0002.tif
NumSubFrames = 8
"""

#: A montage: the section header names a different kind, and the keys differ.
MONTAGE = """PixelSpacing = 2.70
ImageFile = montage.mrc

[MontSection = 0]
PieceCoordinates = 0 0 0
MinMaxMean = 12 4096 830.5

[MontSection = 1]
PieceCoordinates = 3840 0 0
MinMaxMean = 9 4096 812.0
"""

#: Everything a one-image acquisition declares sits above the first section.
GLOBALS_ONLY = """PixelSpacing = 0.86
Voltage = 200
ImageFile = single.mrc
ImageSize = 5760 4092
DataMode = 1
"""


def write(directory: Path, name: str, text: str) -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def extract(path: Path, relative: str | None = None) -> dict:
    return HANDLER.extract(make_source(path, Path(relative or path.name)))


def build(*paths: Path, root: Path | None = None) -> list:
    """Every record set the handler builds for ``paths``, as one batch."""
    metas = []
    for path in paths:
        relative = str(path.relative_to(root)) if root else path.name
        meta = extract(path, relative)
        meta["relative_path"] = relative
        meta["stored_name"] = path.name
        metas.append(meta)
    ids = [f"file_{i}" for i in range(len(metas))]
    return HANDLER.build_croissant(metas, ids).record_sets


@pytest.fixture
def tilt(dataset: Path) -> Path:
    return write(dataset, "tilt_series.mdoc", TILT_SERIES)


# Claiming


def test_claims_its_own_extension(dataset: Path) -> None:
    path = write(dataset, "probe.mdoc", TILT_SERIES)

    assert HANDLER.claims(make_source(path))


def test_case_never_changes_a_claim(dataset: Path) -> None:
    """``TILT.MDOC`` is the same file as ``tilt.mdoc``: the suffix lowercases."""
    quiet = write(dataset, "probe.mdoc", TILT_SERIES)
    shouted = write(dataset, "PROBE.MDOC", TILT_SERIES)

    assert (
        HANDLER.claims(make_source(shouted))
        is HANDLER.claims(make_source(quiet))
        is True
    )


def test_declines_another_formats_extension(dataset: Path) -> None:
    path = write(dataset, "map.mrc", "not an mdoc")

    assert not HANDLER.claims(make_source(path))


def test_an_mdoc_is_routed_to_this_handler(tilt: Path) -> None:
    """Registered in ``builtin_handlers``, so a bake reaches this handler at all."""
    assert isinstance(select_handler(tilt).handler, MdocHandler)


def test_the_format_is_declared() -> None:
    """What the generated documentation table and the contract sweep read."""
    assert HANDLER.EXTENSIONS
    assert all(ext.startswith(".") for ext in HANDLER.EXTENSIONS)
    assert HANDLER.FORMAT_NAME
    assert HANDLER.FORMAT_DESCRIPTION


# Reading


def test_the_file_is_identified_by_name_size_and_digest(tilt: Path) -> None:
    meta = extract(tilt)

    assert meta["file_name"] == "tilt_series.mdoc"
    assert meta["file_size"] == tilt.stat().st_size
    assert len(meta["sha256"]) == 64
    assert meta["encoding_format"] == "text/x-mdoc"


def test_global_keys_are_read_in_file_order(tilt: Path) -> None:
    """Order is the acquisition's own, so a reader sees the file's shape."""
    assert list(extract(tilt)["mdoc"].globals) == [
        "PixelSpacing",
        "Voltage",
        "ImageFile",
        "ImageSize",
        "DataMode",
    ]
    assert extract(tilt)["mdoc"].globals["ImageFile"] == "tilt_series.mrc"


def test_sections_are_counted_and_their_kind_named(tilt: Path) -> None:
    parsed = extract(tilt)["mdoc"]

    assert parsed.section_kind == "ZValue"
    assert parsed.n_sections == 3


def test_a_montage_declares_its_own_section_kind(dataset: Path) -> None:
    parsed = extract(write(dataset, "montage.mdoc", MONTAGE))["mdoc"]

    assert parsed.section_kind == "MontSection"
    assert parsed.n_sections == 2
    assert [name for name, _ in parsed.section_keys] == [
        "PieceCoordinates",
        "MinMaxMean",
    ]


def test_section_keys_are_the_union_in_first_seen_order(tilt: Path) -> None:
    assert [name for name, _ in extract(tilt)["mdoc"].section_keys] == [
        "TiltAngle",
        "StagePosition",
        "ExposureDose",
        "DateTime",
        "SubFramePath",
        "NumSubFrames",
    ]


def test_title_lines_are_kept_as_text(tilt: Path) -> None:
    """``[T = ...]`` looks like a section header and is not one."""
    parsed = extract(tilt)["mdoc"]

    assert len(parsed.titles) == 2
    assert parsed.titles[0] == "SerialEM: Digitized on a Titan Krios"
    assert "T" not in dict(parsed.section_keys)
    assert "T" not in parsed.globals


def test_a_key_seen_only_in_a_later_section_still_joins_the_union(
    dataset: Path,
) -> None:
    """SerialEM stops writing a key when the feature is off, so the union
    across sections is the schema, not the first section."""
    path = write(
        dataset,
        "partial.mdoc",
        "PixelSpacing = 1.0\n\n[ZValue = 0]\nTiltAngle = 0.0\n\n"
        "[ZValue = 1]\nTiltAngle = 3.0\nDefocus = -2.5\n",
    )

    assert [name for name, _ in extract(path)["mdoc"].section_keys] == [
        "TiltAngle",
        "Defocus",
    ]


# Typing


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("TiltAngle", "sc:Float"),
        ("ExposureDose", "sc:Float"),
        ("NumSubFrames", "sc:Integer"),
        ("StagePosition", "sc:Text"),
        ("SubFramePath", "sc:Text"),
        ("DateTime", "sc:Text"),
    ],
)
def test_a_section_key_is_typed_from_its_values(
    tilt: Path, key: str, expected: str
) -> None:
    """A multi-number value is text: Croissant types one value, not a vector."""
    assert dict(extract(tilt)["mdoc"].section_keys)[key] == expected


def test_an_integer_and_a_decimal_under_one_key_make_it_a_float(
    dataset: Path,
) -> None:
    """SerialEM writes ``0`` where it means ``0.0``, so the widest type wins."""
    path = write(
        dataset,
        "mixed.mdoc",
        "PixelSpacing = 1.0\n\n[ZValue = 0]\nDefocus = 0\n\n"
        "[ZValue = 1]\nDefocus = -2.5\n",
    )

    assert dict(extract(path)["mdoc"].section_keys)["Defocus"] == "sc:Float"


def test_an_empty_value_is_text(dataset: Path) -> None:
    path = write(
        dataset,
        "empty_value.mdoc",
        "PixelSpacing = 1.0\n\n[ZValue = 0]\nSubFramePath =\n",
    )

    assert dict(extract(path)["mdoc"].section_keys)["SubFramePath"] == "sc:Text"


# Refusals


def test_a_missing_file_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        extract(tmp_path / "gone" / "absent.mdoc")


def test_an_empty_file_is_refused_by_name(dataset: Path) -> None:
    path = write(dataset, "empty.mdoc", "")

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "empty.mdoc" in str(caught.value)


def test_garbage_bytes_are_refused_by_name(dataset: Path) -> None:
    """The message becomes the reason detail a user reads in ``--report``."""
    path = dataset / "garbage.mdoc"
    path.write_bytes(b"\x00\xff not a real file \xfe\x00")

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "garbage.mdoc" in str(caught.value)


# Building


def test_an_empty_batch_describes_nothing() -> None:
    result = HANDLER.build_croissant([], [])

    assert isinstance(result, BuildResult)
    assert result.file_sets == []
    assert result.record_sets == []


def test_one_record_set_per_file_with_a_field_per_section_key(tilt: Path) -> None:
    (record_set,) = build(tilt)

    assert record_set.id == "tilt_series"
    assert [f.name for f in record_set.fields] == [
        "TiltAngle",
        "StagePosition",
        "ExposureDose",
        "DateTime",
        "SubFramePath",
        "NumSubFrames",
    ]
    assert [str(f.data_types[0]) for f in record_set.fields][:3] == [
        "sc:Float",
        "sc:Text",
        "sc:Float",
    ]


def test_every_field_names_the_file_object_and_carries_no_extract(
    tilt: Path,
) -> None:
    """An ``extract`` here would be a promise nobody can keep: mlcroissant
    cannot read this format."""
    sources = [f.source for rs in build(tilt) for f in rs.fields]

    assert sources
    assert all(s.file_object == "file_0" for s in sources)
    assert all(s.extract == type(s.extract)() for s in sources)


def test_a_field_description_names_the_key_and_the_file(tilt: Path) -> None:
    (record_set,) = build(tilt)
    described = {f.name: f.description for f in record_set.fields}

    assert described["TiltAngle"] == "Section key TiltAngle in tilt_series.mdoc"


def test_the_description_names_the_file_the_sections_and_the_globals(
    tilt: Path,
) -> None:
    (record_set,) = build(tilt)

    assert "tilt_series.mdoc" in record_set.description
    assert "3 ZValue sections" in record_set.description
    assert "tilt_series.mrc" in record_set.description
    assert "1.35" in record_set.description
    assert "300" in record_set.description


def test_the_description_names_the_file_as_stored(dataset: Path) -> None:
    """Identifiers come from the logical name; prose names the file on disk."""
    path = write(dataset, "tilt_series.mdoc", TILT_SERIES)
    meta = extract(path)
    meta["relative_path"] = "tilt_series.mdoc"
    meta["stored_name"] = "tilt_series.mdoc.gz"

    (record_set,) = HANDLER.build_croissant([meta], ["file_0"]).record_sets

    assert record_set.id == "tilt_series"
    assert "tilt_series.mdoc.gz" in record_set.description


def test_a_file_without_sections_is_described_by_its_global_keys(
    dataset: Path,
) -> None:
    (record_set,) = build(write(dataset, "single.mdoc", GLOBALS_ONLY))

    assert [f.name for f in record_set.fields] == [
        "PixelSpacing",
        "Voltage",
        "ImageFile",
        "ImageSize",
        "DataMode",
    ]
    assert [str(f.data_types[0]) for f in record_set.fields] == [
        "sc:Float",
        "sc:Integer",
        "sc:Text",
        "sc:Text",
        "sc:Integer",
    ]
    assert "one row" in record_set.description


def test_sections_declaring_no_key_fall_back_to_the_global_keys(
    dataset: Path,
) -> None:
    path = write(dataset, "bare.mdoc", "PixelSpacing = 1.0\n\n[ZValue = 0]\n")

    (record_set,) = build(path)

    assert [f.name for f in record_set.fields] == ["PixelSpacing"]


def test_a_file_declaring_nothing_at_all_is_not_described(dataset: Path) -> None:
    """An empty record set is one mlcroissant validates, so it is never emitted."""
    path = write(dataset, "kindless.mdoc", "[ZValue = 0]\n")

    assert build(path) == []


def test_two_files_with_one_basename_get_distinct_identifiers(
    dataset: Path,
) -> None:
    first = write(dataset / "grid1", "tilt_series.mdoc", TILT_SERIES)
    second = write(dataset / "grid2", "tilt_series.mdoc", TILT_SERIES)

    ids = [rs.id for rs in build(first, second, root=dataset)]

    assert len(set(ids)) == 2
