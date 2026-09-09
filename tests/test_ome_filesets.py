"""OME FileSet membership after compression and duplicate resolution."""

from pathlib import Path

import pytest

from croissant_baker import compression
from croissant_baker.scan import Outcome

from tests.helpers import (
    DATA,
    OME_TIFF,
    as_list,
    bake_with_report,
    file_set_members,
    file_sets,
    ome_bomb,
    tiff_bytes,
    write_wrapped,
)


@pytest.mark.parametrize(
    "filename,bigtiff",
    [
        ("multi-channel.ome.tif.gz", False),
        ("multi-channel.ome.btf.gz", True),
    ],
)
def test_published_bioformats_files_preserve_their_header(filename, bigtiff):
    import tifffile
    from croissant_baker.handlers.image_handler import ImageHandler
    from croissant_baker.sources import make_source

    source = make_source(DATA / "ome_tiff" / filename)
    with source.open() as stream, tifffile.TiffFile(stream) as tif:
        assert tif.is_bigtiff is bigtiff
    meta = ImageHandler().extract(source)
    assert meta["image_properties"] == dict(
        width=439, height=167, num_bands=1, image_format="TIFF"
    )
    header = meta["ome"]
    assert (header.size_c, header.size_z, header.size_t) == (3, 1, 1)
    assert (header.version, header.dimension_order, header.pixel_type) == (
        "2016-06",
        "XYZCT",
        "int8",
    )
    assert header.image_count == 1
    assert not header.refusal


def test_published_ome_samples_validate_with_plain_images(
    dataset: Path, tmp_path: Path
):
    import json
    from tests.helpers import runner
    from croissant_baker.__main__ import app

    fixtures = DATA / "ome_tiff"
    names = [
        "multi-channel.ome.tif.gz",
        "multi-channel.ome.btf.gz",
        "multifile-Z1.ome.tiff",
    ]
    for name in names:
        (dataset / name).write_bytes((fixtures / name).read_bytes())
    write_wrapped(dataset, "ordinary.tif", tiff_bytes())
    output = tmp_path / "ome.jsonld"
    result = runner.invoke(
        app, ["-i", str(dataset), "-o", str(output), "--creator", "Tester"]
    )
    assert result.exit_code == 0, result.output
    assert "4 described" in result.output
    document = json.loads(output.read_text())
    sets = {node["@id"]: node for node in file_sets(document)}
    assert file_set_members(sets["image-files"], dataset) == {"ordinary.tif"}
    assert file_set_members(sets["ome-image-files"], dataset) == set(names)
    record = next(r for r in document["recordSet"] if r["name"] == "ome_images")
    fields = {f["name"]: f for f in record["field"]}
    assert fields["size_c"]["description"].endswith("(3)")
    assert fields["ome_image_count"]["description"].endswith("(1)")
    assert "multifile.companion.ome" in record["description"]


@pytest.mark.parametrize(
    "plain_suffix,ome_suffix",
    [
        ("", ".gz"),
        (".gz", ""),
        (".gz", ".xz"),
        (".bz2", ".gz"),
    ],
)
def test_excluded_files_do_not_supply_compression_types_or_include_variants(
    dataset: Path,
    plain_suffix: str,
    ome_suffix: str,
) -> None:
    (dataset / "nested").mkdir()
    plain = write_wrapped(dataset, "tile.tif", tiff_bytes(), plain_suffix)
    microscopy = write_wrapped(
        dataset / "nested", "slide.ome.tif", OME_TIFF, ome_suffix
    )
    document, report = bake_with_report(dataset)
    sets = {node["@id"]: node for node in file_sets(document)}

    for identifier, member, suffix in [
        ("image-files", plain, plain_suffix),
        ("ome-image-files", microscopy, ome_suffix),
    ]:
        node = sets[identifier]
        assert file_set_members(node, dataset) == {str(member.relative_to(dataset))}
        wrapper = compression.compression_for("file" + suffix)
        expected = ["image/tiff"] + ([wrapper.media_type] if wrapper else [])
        assert as_list(node["encodingFormat"]) == expected

    expected = ["**/*.tif"] + ([f"**/*.tif{plain_suffix}"] if plain_suffix else [])
    assert as_list(sets["image-files"]["includes"]) == expected
    assert as_list(sets["image-files"]["cr:excludes"]) == [
        str(microscopy.relative_to(dataset))
    ]
    assert report.undescribed == []


@pytest.mark.parametrize("alias", ["slide.tif.gz", "slide.tiff.xz"])
@pytest.mark.parametrize("reverse_discovery", [False, True])
def test_ome_duplicates_stay_excluded_even_under_another_extension(
    dataset: Path,
    alias: str,
    reverse_discovery: bool,
    monkeypatch,
) -> None:
    write_wrapped(dataset, "slide.tif", OME_TIFF)
    path = Path(alias)
    write_wrapped(dataset, path.stem, OME_TIFF, path.suffix)
    write_wrapped(dataset, "plain.tif", tiff_bytes())
    write_wrapped(dataset, "plain.tiff", tiff_bytes(size=12))
    if reverse_discovery:
        from croissant_baker import scan

        discover = scan.discover_files
        monkeypatch.setattr(
            scan, "discover_files", lambda *a, **kw: list(reversed(discover(*a, **kw)))
        )
    document, report = bake_with_report(dataset)
    sets = {node["@id"]: node for node in file_sets(document)}

    assert file_set_members(sets["image-files"], dataset) == {"plain.tif", "plain.tiff"}
    assert file_set_members(sets["ome-image-files"], dataset) == {"slide.tif", alias}
    assert set(as_list(sets["image-files"]["cr:excludes"])) == {"slide.tif", alias}
    assert as_list(sets["image-files"]["encodingFormat"]) == ["image/tiff"]
    assert (
        next(e for e in report.entries if str(e.path) == alias).outcome
        is Outcome.LINKED
    )
    assert report.undescribed == []


@pytest.mark.parametrize(
    "filename",
    ["slide[1].ome.tif", "slide?.ome.tif", "slide*.ome.tif", "SLIDE.OME.TIF"],
)
def test_exact_ome_paths_preserve_literal_characters_and_case(
    dataset: Path, filename: str
):
    write_wrapped(dataset, filename, OME_TIFF, ".gz")
    write_wrapped(dataset, "tile.tif", tiff_bytes())
    write_wrapped(dataset, "TILE2.TIF", tiff_bytes(size=12))
    write_wrapped(dataset, "lower.tif", tiff_bytes(size=16), ".xz")
    document, _ = bake_with_report(dataset)
    sets = {node["@id"]: node for node in file_sets(document)}
    assert file_set_members(sets["image-files"], dataset) == {
        "tile.tif",
        "TILE2.TIF",
        "lower.tif.xz",
    }
    assert file_set_members(sets["ome-image-files"], dataset) == {filename + ".gz"}
    for pattern in as_list(sets["image-files"]["includes"]):
        assert list(dataset.glob(pattern)), f"{pattern} matches no stored file"


def test_refused_and_binary_only_headers_keep_their_distinct_outcomes(dataset: Path):
    from tests.helpers import ome_xml

    write_wrapped(dataset, "bad.tif", tiff_bytes(ome_bomb()), ".gz")
    write_wrapped(
        dataset,
        "stub.btf",
        tiff_bytes(
            ome_xml(
                '<BinaryOnly MetadataFile="unread.companion.ome" UUID="urn:uuid:1"/>'
            ),
            bigtiff=True,
        ),
    )
    document, report = bake_with_report(dataset)
    sets = {node["@id"]: node for node in file_sets(document)}
    records = {node["name"]: node for node in document["recordSet"]}
    assert file_set_members(sets["image-files"], dataset) == {"bad.tif.gz"}
    assert file_set_members(sets["ome-image-files"], dataset) == {"stub.btf"}
    assert "not parsed" in records["images"]["description"]
    assert "unread.companion.ome" in records["ome_images"]["description"]
    assert all(
        [f["name"] for f in record["field"]] == ["image"] for record in records.values()
    )
    assert report.undescribed == []
