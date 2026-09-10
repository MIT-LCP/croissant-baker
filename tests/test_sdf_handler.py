"""SDF: what the registry-wide sweeps cannot reach.

Unit level for claims, sampling and the record set, with one bake at the end.
The weight is on the two things the sweeps cannot express: the field names and
their types come from a bounded sample of records, and the read has to stop
where that sample ends rather than at the end of the file.
"""

from __future__ import annotations

import json
from pathlib import Path

import mlcroissant as mlc
import pytest

from croissant_baker.entries import Reason
from croissant_baker.handlers.sdf_handler import (
    SAMPLE_BYTES,
    SAMPLE_RECORDS,
    SDFHandler,
)
from croissant_baker.identifiers import serialize_datetime
from croissant_baker.sources import FileSource, make_source

from tests.helpers import (
    MOL_METHANE,
    MOL_V2000,
    MOL_V3000,
    SAMPLES,
    bake,
    bake_with_report,
    by_name,
    cut_gzip,
    record_sets,
    sdf_record,
    write_wrapped,
)

HANDLER = SDFHandler()

SDF_PAYLOAD = SAMPLES["SDFHandler"]()[0][1]


def write(directory: Path, name: str, payload: bytes) -> Path:
    path = directory / name
    path.write_bytes(payload)
    return path


def source_for(path: Path, relative: str | None = None):
    return make_source(path, Path(relative or path.name))


def extract(path: Path, relative: str | None = None, **kwargs) -> dict:
    return HANDLER.extract(source_for(path, relative), **kwargs)


@pytest.mark.parametrize("name", ["molecules.sdf", "molecules.sd"])
def test_every_declared_spelling_is_claimed(name: str, dataset: Path) -> None:
    """One format under two names, both of them common in a compound drop."""
    assert HANDLER.claims(source_for(write(dataset, name, SDF_PAYLOAD)))


def test_a_record_terminator_alone_is_enough_to_claim(dataset: Path) -> None:
    """A concatenation whose first block is not a molfile is still an SD file if
    it carries the terminator, which no other text format writes."""
    payload = b"a header this handler does not read\n\n\n\n$$$$\n"

    assert HANDLER.claims(source_for(write(dataset, "odd.sdf", payload)))


def test_a_file_named_sdf_that_is_neither_is_not_claimed(dataset: Path) -> None:
    """``.sdf`` is also a spatial data format and a session save file, so the
    extension alone is not evidence."""
    path = write(dataset, "scene.sdf", b"some other tool's file\nwith lines\n")

    assert not HANDLER.claims(source_for(path))


def test_the_same_bytes_under_another_extension_are_not_claimed(
    dataset: Path,
) -> None:
    path = write(dataset, "molecules.txt", SDF_PAYLOAD)

    assert not HANDLER.claims(source_for(path))


def test_the_data_items_are_named_in_first_seen_order_and_typed(
    dataset: Path,
) -> None:
    """The order records write their items in is the order the schema states
    them, so two runs over the same file name the fields the same way."""
    meta = extract(write(dataset, "molecules.sdf", SDF_PAYLOAD))

    assert meta["fields"] == [
        {"name": "ID", "type": "cr:Int64"},
        {"name": "LogP", "type": "cr:Float64"},
        {"name": "Name", "type": "sc:Text"},
        {"name": "Notes", "type": "sc:Text"},
    ]


def test_a_title_line_that_opens_like_a_data_item_is_not_read_as_one(
    dataset: Path,
) -> None:
    """The molfile block's first three lines are free text a depositor writes,
    and a title opening with ``>`` is a title, not a field header. The data
    items open under ``M  END``, which is where they are looked for."""
    block = MOL_V2000.replace(b"ethanol\n", b"> <Trap>\n", 1)

    meta = extract(write(dataset, "titled.sdf", sdf_record(block, [("ID", "1")])))

    assert meta["fields"] == [{"name": "ID", "type": "cr:Int64"}]


def test_a_data_item_naming_no_field_is_passed_over(dataset: Path) -> None:
    """``> <>`` names nothing, and a field with no name is not one to describe:
    it shipped as an id ending in a slash with no name beside it."""
    payload = sdf_record(MOL_V2000, [("", "orphan"), ("ID", "1")])

    meta = extract(write(dataset, "nameless_item.sdf", payload))

    assert meta["fields"] == [{"name": "ID", "type": "cr:Int64"}]


def test_a_record_set_omits_the_field_no_data_item_names(dataset: Path) -> None:
    write(
        dataset, "molecules.sdf", sdf_record(MOL_V2000, [("", "orphan"), ("ID", "1")])
    )

    (record_set,) = record_sets(bake(dataset))

    assert [f["name"] for f in record_set["field"]] == ["title", "molfile", "ID"]


def test_a_value_spanning_several_lines_is_text(dataset: Path) -> None:
    """A value the file writes across lines is not a number whatever those lines
    hold, and typing it as one would promise a reader something the file does
    not carry."""
    payload = sdf_record(MOL_V2000, [("Count", "1\n2")])

    meta = extract(write(dataset, "notes.sdf", payload))

    assert meta["fields"] == [{"name": "Count", "type": "sc:Text"}]


def test_a_value_of_more_digits_than_an_integer_holds_is_text(
    dataset: Path,
) -> None:
    """No integer type carries it, and past 4300 digits ``int`` refuses to
    parse it at all and raises a message with no file name in it. It is a run
    of digits the depositor wrote, which is text."""
    payload = sdf_record(MOL_V2000, [("Count", "9" * 5000)])

    meta = extract(write(dataset, "huge.sdf", payload))

    assert meta["fields"] == [{"name": "Count", "type": "sc:Text"}]


def test_a_field_typed_integer_by_one_record_and_text_by_another_is_text(
    dataset: Path,
) -> None:
    """Types are agreed across the sample, not taken from the first record."""
    payload = sdf_record(MOL_V2000, [("ID", "1")]) + sdf_record(
        MOL_METHANE, [("ID", "CHEMBL6329")]
    )

    meta = extract(write(dataset, "mixed.sdf", payload))

    assert meta["fields"] == [{"name": "ID", "type": "sc:Text"}]


def test_a_file_read_to_its_end_reports_the_exact_record_count(
    dataset: Path,
) -> None:
    meta = extract(write(dataset, "molecules.sdf", SDF_PAYLOAD))

    assert meta["sampled_records"] == 2
    assert meta["sample_exhausted"] is True
    assert meta["molfile_version"] == "V2000"


def test_a_file_whose_last_terminator_has_no_line_ending_keeps_its_last_record(
    dataset: Path,
) -> None:
    """The tail behind the last line ending is a line like any other. A writer
    that closes the file straight after the terminator is common enough that
    dropping the record under it silently miscounts a whole library."""
    meta = extract(write(dataset, "molecules.sdf", SDF_PAYLOAD.removesuffix(b"\n")))

    assert meta["sampled_records"] == 2
    assert meta["sample_exhausted"] is True


def test_the_record_count_is_the_same_with_and_without_the_last_line_ending(
    dataset: Path,
) -> None:
    """One byte at the end of the file says nothing about what is in it."""
    terminated = extract(write(dataset, "terminated.sdf", SDF_PAYLOAD))
    bare = extract(write(dataset, "bare.sdf", SDF_PAYLOAD.removesuffix(b"\n")))

    assert bare["sampled_records"] == terminated["sampled_records"]


def test_a_single_record_with_no_line_ending_is_described(dataset: Path) -> None:
    """It was refused with a reason that was not true: the file does carry the
    terminator, and the read simply stopped one line short of it."""
    payload = sdf_record(MOL_V2000, [("ID", "1")]).removesuffix(b"\n")

    meta = extract(write(dataset, "one.sdf", payload))

    assert meta["sampled_records"] == 1
    assert meta["fields"] == [{"name": "ID", "type": "cr:Int64"}]


def test_versions_that_differ_across_the_sample_are_reported_as_mixed(
    dataset: Path,
) -> None:
    """A concatenation is free to mix layouts, and naming one of them would be
    stating something about the file that is only true of part of it."""
    payload = sdf_record(MOL_V2000) + sdf_record(MOL_V3000)

    meta = extract(write(dataset, "both.sdf", payload))

    assert meta["molfile_version"] == "mixed"


class _Counted:
    """A stream that remembers how many bytes were pulled through it."""

    def __init__(self, stream) -> None:
        self._stream = stream
        self.read_bytes = 0

    def read(self, size=-1):
        data = self._stream.read(size)
        self.read_bytes += len(data)
        return data

    def readable(self) -> bool:
        return True

    def close(self) -> None:
        self._stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def counting_source(path: Path, opened: list):
    """A source over ``path`` whose every stream is counted in ``opened``."""

    def open_binary():
        stream = _Counted(path.open("rb"))
        opened.append(stream)
        return stream

    return FileSource(
        name=path.name,
        relative_path=Path(path.name),
        size=path.stat().st_size,
        exists=True,
        _open_binary=open_binary,
        _digest=lambda: "0" * 64,
    )


def test_a_file_longer_than_the_sample_stops_at_the_sample(dataset: Path) -> None:
    """The whole point of the bound. A screening library runs to millions of
    compounds, and the schema is the same after a hundred of them as after all
    of them."""
    payload = b"".join(
        sdf_record(MOL_V2000, [("ID", str(i))]) for i in range(SAMPLE_RECORDS * 3)
    )
    path = write(dataset, "library.sdf", payload)
    opened: list = []

    meta = HANDLER.extract(counting_source(path, opened))

    assert meta["sampled_records"] == SAMPLE_RECORDS
    assert meta["sample_exhausted"] is False
    read = sum(stream.read_bytes for stream in opened)
    assert read < SAMPLE_BYTES
    assert read < len(payload)


def test_a_first_record_that_does_not_complete_within_the_bound_is_refused(
    dataset: Path,
) -> None:
    """A file with no record terminator inside the bound states no record, and
    reporting that beats describing a record nobody read to the end of."""
    payload = MOL_V2000 + b"> <Notes>\n" + b"padding\n" * (SAMPLE_BYTES // 8 + 1)

    with pytest.raises(ValueError) as caught:
        extract(write(dataset, "endless.sdf", payload))

    assert "endless.sdf" in str(caught.value)
    assert str(SAMPLE_BYTES) in str(caught.value)


def test_a_record_whose_molfile_header_cannot_be_parsed_is_refused(
    dataset: Path,
) -> None:
    payload = sdf_record(MOL_V2000.replace(b" V2000", b"      ", 1), [("ID", "1")])

    with pytest.raises(ValueError) as caught:
        extract(write(dataset, "nameless.sdf", payload))

    assert "nameless.sdf" in str(caught.value)


def test_a_wrapper_ending_mid_stream_is_refused_naming_the_file(
    dataset: Path,
) -> None:
    """A member intact for its first bytes opens, and then ends where the
    download stopped. What that raises is not an ``OSError``, and a file is owed
    a reason naming it either way.

    Fewer records than the sample takes, on purpose: a file cut off past the
    sample is one this handler finished reading before the damage, and
    describing it from what it read is right. The refusal is owed to a file
    whose stream ends while the sample is still being taken."""
    path = write(dataset, "cut.sdf.gz", cut_gzip(SDF_PAYLOAD * 20))

    with pytest.raises(ValueError) as caught:
        extract(path)

    assert "cut.sdf" in str(caught.value)
    assert "SDF" in str(caught.value)


def test_a_refusal_reaches_the_scan_report_through_a_bake(dataset: Path) -> None:
    """A file this handler claims and cannot read is reported by name, and the
    library beside it is still described: the loss is per-file, never the run."""
    write(dataset, "broken.sdf", sdf_record(MOL_V2000.replace(b"  3  2", b"  a  b", 1)))
    write(dataset, "molecules.sdf", SDF_PAYLOAD)

    document, report = bake_with_report(dataset)

    (described,) = record_sets(document)
    assert "molecules.sdf" in described["description"]
    (refused,) = report.undescribed
    assert refused.name == "broken.sdf"
    assert refused.reason is Reason.EXTRACT_FAILED
    assert "broken.sdf" in refused.detail


def test_the_record_set_carries_the_molecule_and_its_data_items(
    dataset: Path,
) -> None:
    """Two fields the file does not name as data items, the molecule's own
    name and its connection table, and then one per item the records carry."""
    meta = extract(write(dataset, "molecules.sdf", SDF_PAYLOAD))
    meta["relative_path"] = "molecules.sdf"

    (record_set,) = HANDLER.build_croissant([meta], ["file_0"]).record_sets

    fields = record_set.fields
    assert [f.name for f in fields] == [
        "title",
        "molfile",
        "ID",
        "LogP",
        "Name",
        "Notes",
    ]
    assert "4 data fields, from all 2 records" in record_set.description


def test_no_field_promises_an_extract_mlcroissant_cannot_run(dataset: Path) -> None:
    """Croissant 1.1's extract grammar addresses columns of a format
    mlcroissant's reader knows how to open, and SDF is not on that list, so a
    column reference here would be a promise nobody can keep. Every field is
    sourced from the file and nothing narrower."""
    write(dataset, "molecules.sdf", SDF_PAYLOAD)

    (record_set,) = record_sets(bake(dataset))

    for field in record_set["field"]:
        assert set(field["source"]) == {"fileObject"}


def test_a_bake_builds_a_record_set_that_constructs(
    dataset: Path, tmp_path: Path
) -> None:
    """The whole path, once: dispatch, extraction, assembly, and construction
    under mlcroissant."""
    write(dataset, "molecules.sdf", SDF_PAYLOAD)

    document = bake(dataset)

    (record_set,) = record_sets(document)
    assert set(by_name(record_set["field"])) == {
        "title",
        "molfile",
        "ID",
        "LogP",
        "Name",
        "Notes",
    }

    written = tmp_path / "croissant.jsonld"
    written.write_text(json.dumps(document, indent=2, default=serialize_datetime))
    mlc.Dataset(str(written))


def test_a_wrapped_sdf_is_described_like_the_plain_one(dataset: Path) -> None:
    wrapped = write_wrapped(dataset, "molecules.sdf", SDF_PAYLOAD, ".gz")

    meta = HANDLER.extract(make_source(wrapped, Path("molecules.sdf")))

    assert meta["file_name"] == "molecules.sdf"
    assert meta["encoding_format"] == "chemical/x-mdl-sdfile"
    assert [f["name"] for f in meta["fields"]] == ["ID", "LogP", "Name", "Notes"]
