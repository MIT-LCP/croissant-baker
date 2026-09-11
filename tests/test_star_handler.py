"""STAR: what the handler makes of a cryo-EM metadata file.

Unit level throughout, ``extract`` and ``build_croissant``, never a bake. The
document description the handler builds on is covered in
``test_structural_cif.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from croissant_baker.handlers.base_handler import BuildResult
from croissant_baker.handlers.registry import select_handler
from croissant_baker.handlers.structural_biology.cif import Table
from croissant_baker.handlers.structural_biology.star_handler import STARHandler
from croissant_baker.sources import make_source

HANDLER = STARHandler()

#: A RELION particles file as ``relion_refine`` writes one, trimmed to three
#: particles and the columns whose types differ.
PARTICLES = """
data_optics

loop_
_rlnOpticsGroupName
_rlnOpticsGroup
_rlnVoltage
opticsGroup1 1 300.0

data_particles

loop_
_rlnImageName
_rlnCoordinateX
_rlnClassNumber
000001@stack.mrcs 1104.5 1
000002@stack.mrcs 998.0 2
000003@stack.mrcs 512.25 1
"""

#: The other shape RELION writes: a job's settings, one tag per line.
GENERAL = """
data_general

_rlnFinalResolution 3.2
_rlnNrClasses 4
_rlnJobName run1
"""


def write_star(directory: Path, name: str, text: str = PARTICLES) -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def extract(path: Path, relative: str | None = None) -> dict:
    return HANDLER.extract(make_source(path, Path(relative or path.name)))


def build(*paths: Path, root: Path | None = None) -> list:
    """Every record set the handler builds for ``paths``, as one batch.

    ``root`` gives each file the path a scan would have handed it, which is what
    the identifier tests turn on.
    """
    metas = []
    for path in paths:
        relative = str(path.relative_to(root)) if root else path.name
        meta = extract(path, relative)
        meta["relative_path"] = relative
        meta["stored_name"] = path.name
        metas.append(meta)
    ids = [f"file_{i}" for i in range(len(metas))]
    return HANDLER.build_croissant(metas, ids).record_sets


def one(record_sets: list, suffix: str):
    return next(rs for rs in record_sets if rs.id.endswith(suffix))


# Claiming


@pytest.mark.parametrize(
    "name",
    ["run_data.star", "RUN_DATA.STAR", "run_data.Star"],
    ids=["lower", "upper", "mixed"],
)
def test_a_star_file_is_claimed_whatever_its_case(name: str, dataset: Path) -> None:
    """``FileSource.suffix`` is lowercased, so one answer covers all spellings."""
    assert HANDLER.claims(make_source(write_star(dataset, name), Path(name)))


@pytest.mark.parametrize("name", ["structure.cif", "notes.txt"], ids=["cif", "text"])
def test_another_format_is_declined(name: str, dataset: Path) -> None:
    assert not HANDLER.claims(make_source(write_star(dataset, name), Path(name)))


def test_a_star_file_is_routed_to_this_handler(dataset: Path) -> None:
    """Registered in ``builtin_handlers``, so a bake reaches this handler at all."""
    path = write_star(dataset, "run_data.star")

    assert isinstance(select_handler(path).handler, STARHandler)


def test_the_handler_declares_what_the_docs_table_needs() -> None:
    assert HANDLER.FORMAT_NAME and HANDLER.FORMAT_DESCRIPTION
    assert HANDLER.EXTENSIONS
    assert all(ext.startswith(".") for ext in HANDLER.EXTENSIONS)


# Reading


def test_extract_reports_the_file_as_stored(dataset: Path) -> None:
    path = write_star(dataset, "run_data.star")

    meta = extract(path)

    assert meta["file_name"] == "run_data.star"
    assert meta["file_size"] == path.stat().st_size
    assert len(meta["sha256"]) == 64
    assert meta["encoding_format"] == "application/x-star"


def test_extract_describes_every_data_block(dataset: Path) -> None:
    meta = extract(write_star(dataset, "run_data.star"))

    assert [table.block for table in meta["tables"]] == ["optics", "particles"]


def test_a_missing_file_raises_file_not_found(tmp_path: Path) -> None:
    source = make_source(tmp_path / "gone" / "run_data.star", Path("run_data.star"))

    with pytest.raises(FileNotFoundError):
        HANDLER.extract(source)


def test_garbage_bytes_raise_a_value_error_naming_the_file(dataset: Path) -> None:
    """The message becomes the reason detail a user reads in ``--report``."""
    path = dataset / "run_data.star"
    path.write_bytes(b"\x00\xff not a real file \xfe\x00")

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "run_data.star" in str(caught.value)


@pytest.mark.parametrize(
    "text",
    ["", "   \n\n", "# a comment and nothing else\n"],
    ids=["empty", "blank", "comment"],
)
def test_a_file_with_no_data_block_raises_a_value_error(
    text: str, dataset: Path
) -> None:
    """Nothing to describe is a file the pipeline should report, not one that
    quietly contributes no record set."""
    path = write_star(dataset, "run_data.star", text)

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "run_data.star" in str(caught.value)


def test_the_reported_path_is_the_one_the_scan_used(dataset: Path) -> None:
    """A user looking for the failure needs the path they can find."""
    path = write_star(dataset, "job/run_data.star", "not a star file at all\n")

    with pytest.raises(ValueError) as caught:
        extract(path, "job/run_data.star")

    assert "job/run_data.star" in str(caught.value)


# Building


def test_an_empty_batch_describes_nothing() -> None:
    result = HANDLER.build_croissant([], [])

    assert isinstance(result, BuildResult)
    assert (result.file_sets, result.record_sets, result.declined) == ([], [], ())


def test_a_real_batch_returns_a_build_result(dataset: Path) -> None:
    meta = extract(write_star(dataset, "run_data.star"))
    meta["relative_path"] = "run_data.star"

    result = HANDLER.build_croissant([meta], ["file_0"])

    assert isinstance(result, BuildResult)
    assert result.record_sets and result.declined == ()


def test_each_data_block_becomes_one_record_set(dataset: Path) -> None:
    record_sets = build(write_star(dataset, "run_data.star"))

    assert [rs.id for rs in record_sets] == [
        "run_data_optics",
        "run_data_particles",
    ]


def test_a_record_set_is_named_by_its_own_identifier(dataset: Path) -> None:
    record_sets = build(write_star(dataset, "run_data.star"))

    assert all(rs.name == rs.id for rs in record_sets)


def test_every_column_becomes_a_typed_field(dataset: Path) -> None:
    particles = one(build(write_star(dataset, "run_data.star")), "_particles")

    assert [(f.name, str(f.data_types[0])) for f in particles.fields] == [
        ("rlnImageName", "sc:Text"),
        ("rlnCoordinateX", "sc:Float"),
        ("rlnClassNumber", "sc:Integer"),
    ]


def test_a_field_identifier_lives_under_its_record_set(dataset: Path) -> None:
    particles = one(build(write_star(dataset, "run_data.star")), "_particles")

    assert [f.id for f in particles.fields] == [
        "run_data_particles/rlnImageName",
        "run_data_particles/rlnCoordinateX",
        "run_data_particles/rlnClassNumber",
    ]


def test_a_field_points_at_the_file_and_promises_no_value(dataset: Path) -> None:
    """mlcroissant cannot read STAR, so an ``extract`` here would be a promise
    nobody can keep."""
    particles = one(build(write_star(dataset, "run_data.star")), "_particles")

    for field in particles.fields:
        assert field.source.file_object == "file_0"
        assert field.source.extract.column is None


def test_a_block_of_pairs_becomes_a_one_row_record_set(dataset: Path) -> None:
    general = one(build(write_star(dataset, "job.star", GENERAL)), "_general")

    assert [(f.name, str(f.data_types[0])) for f in general.fields] == [
        ("rlnFinalResolution", "sc:Float"),
        ("rlnNrClasses", "sc:Integer"),
        ("rlnJobName", "sc:Text"),
    ]
    assert "1 row" in general.description


def test_a_description_names_the_file_on_disk(dataset: Path) -> None:
    """Identifiers come from the logical name; prose names the file a reader
    can find."""
    meta = extract(write_star(dataset, "run_data.star"))
    meta["relative_path"] = "run_data.star"
    meta["stored_name"] = "run_data.star.gz"

    record_sets = HANDLER.build_croissant([meta], ["file_0"]).record_sets

    assert all("run_data.star.gz" in rs.description for rs in record_sets)


def test_a_description_counts_what_it_describes(dataset: Path) -> None:
    particles = one(build(write_star(dataset, "run_data.star")), "_particles")

    assert "particles" in particles.description
    assert "3 rows" in particles.description
    assert "3 columns" in particles.description


def test_two_files_of_one_name_get_identifiers_of_their_own(dataset: Path) -> None:
    """Two RELION jobs both write ``run_data.star``; one record set cannot
    stand for both."""
    record_sets = build(
        write_star(dataset, "job001/run_data.star"),
        write_star(dataset, "job002/run_data.star"),
        root=dataset,
    )

    assert len({rs.id for rs in record_sets}) == len(record_sets) == 4


def test_a_table_with_no_column_is_not_described() -> None:
    """mlcroissant validates a record set with no field, so this refuses to
    build one rather than emit a node nothing can read."""
    meta = {
        "file_name": "run_data.star",
        "relative_path": "run_data.star",
        "tables": [Table(block="empty", name="empty", kind="loop", columns=(), rows=0)],
    }

    assert HANDLER.build_croissant([meta], ["file_0"]).record_sets == []
