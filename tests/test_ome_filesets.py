"""OME FileSet membership after compression and duplicate resolution."""

from pathlib import Path

import mlcroissant as mlc
import numpy as np
import pytest
import tifffile

from croissant_baker import compression
from croissant_baker.metadata_generator import MetadataGenerator
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


def _readable_dataset(directory: Path) -> mlc.Dataset:
    output = directory / "croissant.jsonld"
    MetadataGenerator(
        str(directory),
        name="images",
        description="Image reader regression fixture",
        creators=[{"name": "Tester"}],
        date_published="2024-01-01",
    ).save_metadata(str(output), validate=True)
    return mlc.Dataset(str(output))


@pytest.mark.parametrize("extension", ["tif", "tiff", "btf", "TIF"])
def test_mlcroissant_reads_images_at_the_root_and_in_subdirectories(dataset, extension):
    (dataset / "nested" / "deeper").mkdir(parents=True)
    expected = [(4, 4), (6, 6), (8, 8)]
    for directory, shape in zip(["", "nested", "nested/deeper"], expected):
        tifffile.imwrite(
            dataset / directory / f"tile.{extension}",
            np.zeros(shape, np.uint8),
            bigtiff=extension == "btf",
            metadata=None,
        )

    reader = _readable_dataset(dataset)
    rows = list(reader.records("images"))
    assert sorted(row["images/image_content"].size for row in rows) == expected


def test_mlcroissant_exclusions_still_need_upstream_support(dataset):
    """Pin actual reader behaviour, separately from spec membership checks.

    When upstream #772 is fixed, this test fails so the documented limitation
    can be removed and this assertion changed to the intended plain-only rows.
    uint8 avoids the reader's unrelated signed-TIFF pixel conversion failure.
    """
    from etils import epath
    from mlcroissant._src.core.path import Path as ReaderPath
    from mlcroissant._src.operation_graph.base_operation import Operations
    from mlcroissant._src.operation_graph.operations.filter import FilterFiles

    (dataset / "nested").mkdir()
    for name, size, is_ome in [
        ("top.tif", 4, False),
        ("nested/deep.tif", 6, False),
        ("nested/slide.ome.tif", 8, True),
    ]:
        tifffile.imwrite(
            dataset / name,
            np.zeros((size, size), np.uint8),
            ome=is_ome,
            metadata={"axes": "YX"} if is_ome else None,
        )

    reader = _readable_dataset(dataset)
    sets = {
        node.id: node
        for node in reader.metadata.distribution
        if isinstance(node, mlc.FileSet)
    }
    assert file_set_members(sets["image-files"].to_json(), dataset) == {
        "top.tif",
        "nested/deep.tif",
    }
    assert file_set_members(sets["ome-image-files"].to_json(), dataset) == {
        "nested/slide.ome.tif"
    }
    # Test the OME FileSet through the reader's own filter, without asking it
    # to extract the descriptive header fields (which it cannot do).
    ome_files = FilterFiles(Operations(), sets["ome-image-files"]).call(
        ReaderPath(epath.Path(dataset), Path("."))
    )
    assert [str(path.fullpath) for path in ome_files] == ["nested/slide.ome.tif"]
    rows = list(reader.records("images"))
    assert sorted(row["images/image_content"].size for row in rows) == [
        (4, 4),
        (6, 6),
        (8, 8),
    ], "Recheck upstream #772: the reader may now honor excludes."


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
    expected += ["*.tif"] + ([f"*.tif{plain_suffix}"] if plain_suffix else [])
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
