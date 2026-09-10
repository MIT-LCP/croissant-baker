"""MOL: what the registry-wide sweeps cannot reach.

Unit level for claims and extraction, with one bake at the end, because the
end-to-end suite has no assembly of its own. Three things the sweeps cannot
express carry most of the weight: the claim needs the extension and the version
literal together, the counts come off fixed-width columns in one layout and off
a ``COUNTS`` line in the other, and the read has to stop inside a bounded
prefix.
"""

from __future__ import annotations

import json
from pathlib import Path

import mlcroissant as mlc
import pytest

from croissant_baker.entries import Reason
from croissant_baker.handlers.mol_handler import HEAD_BYTES, MOLHandler
from croissant_baker.identifiers import serialize_datetime
from croissant_baker.sources import make_source

from tests.helpers import (
    MOL_METHANE,
    MOL_V2000,
    MOL_V3000,
    bake,
    bake_with_report,
    cut_gzip,
    file_objects,
    record_sets,
    write_wrapped,
)

HANDLER = MOLHandler()


def write(directory: Path, name: str, payload: bytes) -> Path:
    path = directory / name
    path.write_bytes(payload)
    return path


def source_for(path: Path, relative: str | None = None):
    return make_source(path, Path(relative or path.name))


def extract(path: Path, relative: str | None = None, **kwargs) -> dict:
    return HANDLER.extract(source_for(path, relative), **kwargs)


@pytest.mark.parametrize(
    ("version", "payload"),
    [("V2000", MOL_V2000), ("V3000", MOL_V3000)],
)
def test_both_layouts_are_claimed(version: str, payload: bytes, dataset: Path) -> None:
    """The version literal on the fourth line is what says the file is a molfile,
    and both layouts write one."""
    assert HANDLER.claims(source_for(write(dataset, f"{version}.mol", payload)))


def test_a_file_named_mol_that_declares_no_version_is_not_claimed(
    dataset: Path,
) -> None:
    """``.mol`` is also a save file for several unrelated tools, so the extension
    alone is not evidence."""
    path = write(dataset, "level.mol", b"some other tool's save file\nwith lines\n")

    assert not HANDLER.claims(source_for(path))


def test_the_same_bytes_under_another_extension_are_not_claimed(
    dataset: Path,
) -> None:
    """A connection table is columns of numbers, which a text file can also be:
    the extension is what says this one is a molfile."""
    path = write(dataset, "ethanol.txt", MOL_V2000)

    assert not HANDLER.claims(source_for(path))


def test_the_v2000_counts_are_read_off_the_fixed_width_columns(
    dataset: Path,
) -> None:
    meta = extract(write(dataset, "ethanol.mol", MOL_V2000))

    assert meta["molfile_version"] == "V2000"
    assert meta["title"] == "ethanol"
    assert (meta["atom_count"], meta["bond_count"]) == (3, 2)


def test_a_zero_bond_count_is_read_as_zero(dataset: Path) -> None:
    """Methane has one atom and no bond, and no bond is a count like any other."""
    meta = extract(write(dataset, "methane.mol", MOL_METHANE))

    assert (meta["atom_count"], meta["bond_count"]) == (1, 0)


def test_the_v3000_counts_come_off_the_counts_line(dataset: Path) -> None:
    """A V3000 counts line holds zeros: the real counts are inside the CTAB."""
    meta = extract(write(dataset, "ethanol.mol", MOL_V3000))

    assert meta["molfile_version"] == "V3000"
    assert (meta["atom_count"], meta["bond_count"]) == (3, 2)


def test_the_description_states_the_version_the_counts_and_the_title(
    dataset: Path,
) -> None:
    described = extract(write(dataset, "ethanol.mol", MOL_V2000))["description"]

    assert "ethanol.mol" in described
    assert "V2000" in described
    assert "3 atoms, 2 bonds" in described
    assert "title: ethanol" in described


def test_a_blank_title_is_left_out_of_the_description(dataset: Path) -> None:
    """A molfile whose first line is blank names no molecule, and a description
    reading ``title:`` with nothing after it states less than saying nothing."""
    payload = b"\n" + MOL_V2000.split(b"\n", 1)[1]

    described = extract(write(dataset, "unnamed.mol", payload))["description"]

    assert "title" not in described
    assert "unnamed.mol" in described


def test_the_encoding_format_is_reported(dataset: Path) -> None:
    meta = extract(write(dataset, "ethanol.mol", MOL_V2000))

    assert meta["encoding_format"] == "chemical/x-mdl-molfile"


def test_a_header_that_does_not_complete_inside_the_bound_is_refused(
    dataset: Path,
) -> None:
    """A V3000 header states its counts further down the file, and how much
    further is the file's choice. The read is bounded anyway, and a header that
    does not finish inside the bound is reported rather than guessed at."""
    padding = b"M  V30 BEGIN ATOM\n" * (HEAD_BYTES // 8)
    payload = MOL_V3000.replace(b"M  V30 COUNTS 3 2 0 0 0\n", padding, 1)

    with pytest.raises(ValueError) as caught:
        extract(write(dataset, "huge.mol", payload))

    assert "huge.mol" in str(caught.value)
    assert str(HEAD_BYTES) in str(caught.value)


def test_a_counts_line_carrying_no_version_literal_is_refused(dataset: Path) -> None:
    """Report, never guess: a fourth line that could be read as counts but does
    not say which layout it is written in is a file this handler cannot describe.
    """
    payload = MOL_V2000.replace(b" V2000", b"      ", 1)

    with pytest.raises(ValueError) as caught:
        extract(write(dataset, "nameless.mol", payload))

    assert "nameless.mol" in str(caught.value)


def test_a_wrapper_ending_mid_stream_is_refused_naming_the_file(
    dataset: Path,
) -> None:
    """A member intact for its first bytes opens, and then ends where the
    download stopped. What that raises is not an ``OSError``, and a file is owed
    a reason naming it either way."""
    path = write(dataset, "cut.mol.gz", cut_gzip(MOL_V2000 * 200))

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "cut.mol" in str(caught.value)
    assert "MOL" in str(caught.value)


def test_a_refusal_reaches_the_scan_report_through_a_bake(dataset: Path) -> None:
    """A file this handler claims and cannot read is reported by name, with the
    reason it was refused for, and the molecule beside it is still described:
    the loss is per-file, never the run."""
    write(dataset, "broken.mol", MOL_V2000.replace(b"  3  2", b"  a  b", 1))
    write(dataset, "ethanol.mol", MOL_V2000)

    document, report = bake_with_report(dataset)

    assert [o["name"] for o in file_objects(document)] == ["ethanol.mol"]
    (refused,) = report.undescribed
    assert refused.name == "broken.mol"
    assert refused.reason is Reason.EXTRACT_FAILED
    assert "broken.mol" in refused.detail


def test_no_molecule_becomes_a_record_set(dataset: Path) -> None:
    """One molecule is a file, not a table: there is no second row for a record
    set to hold, and the file's own description says everything read."""
    meta = extract(write(dataset, "ethanol.mol", MOL_V2000))
    meta["relative_path"] = "ethanol.mol"

    result = HANDLER.build_croissant([meta], ["file_0"])

    assert result.record_sets == []
    assert result.file_sets == []


def test_a_bake_carries_the_description_onto_the_file_object(
    dataset: Path, tmp_path: Path
) -> None:
    """The whole path, once: dispatch, extraction, the FileObject the generator
    owns, and construction under mlcroissant."""
    write(dataset, "ethanol.mol", MOL_V2000)

    document = bake(dataset)

    assert record_sets(document) == []
    (described,) = file_objects(document)
    assert described["encodingFormat"] == "chemical/x-mdl-molfile"
    assert "ethanol.mol" in described["description"]

    written = tmp_path / "croissant.jsonld"
    written.write_text(json.dumps(document, indent=2, default=serialize_datetime))
    mlc.Dataset(str(written))


def test_a_wrapped_molfile_is_described_like_the_plain_one(dataset: Path) -> None:
    wrapped = write_wrapped(dataset, "ethanol.mol", MOL_V2000, ".gz")

    meta = HANDLER.extract(make_source(wrapped, Path("ethanol.mol")))

    assert meta["file_name"] == "ethanol.mol"
    assert meta["encoding_format"] == "chemical/x-mdl-molfile"
    assert (meta["atom_count"], meta["bond_count"]) == (3, 2)
