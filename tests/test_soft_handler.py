"""GEO SOFT: what the registry-wide sweeps cannot reach.

Unit level throughout — ``extract`` and ``build_croissant``, never a bake. The
pipeline is covered once, end to end, in ``test_end_to_end.py``. Tests name the
success criterion they settle where there is one.
"""

from __future__ import annotations

import gzip
import logging
import re
import tracemalloc
from pathlib import Path

import pytest

from croissant_baker.handlers.registry import builtin_handlers
from croissant_baker.handlers.soft_handler import SOFTHandler
from croissant_baker.sources import FileSource, make_source

from tests.helpers import DATA, SAMPLES

#: The two modern deposits, as GEO serves them: metadata-only, their payload
#: supplementary and every ``!*_data_row_count`` 0. They share no
#: characteristic key, which is the reason to read the format at all.
GEO_EXPORT = DATA / "geo_soft" / "GSE327347_family.soft.gz"
SECOND_EXPORT = DATA / "geo_soft" / "GSE335275_family.soft.gz"

#: The classic shape, row-trimmed: ten samples over two column signatures, a
#: 16-column platform annotation, and ``!*_data_row_count`` declarations left
#: at 22 283 against bodies of ten rows. See the fixture directory's README.
TABLES = DATA / "geo_soft" / "GSE1000_family.soft"

HANDLER = SOFTHandler()


def text_of(path: Path) -> str:
    """The fixture's decompressed text, whichever way it is stored."""
    raw = path.read_bytes()
    if path.suffix == ".gz":
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", "replace")


def write_soft(dataset: Path, name: str, text: str) -> Path:
    path = dataset / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def extract(path: Path, relative: str | None = None) -> dict:
    return HANDLER.extract(make_source(path, Path(relative or path.name)))


def build(*paths: Path, root: Path | None = None) -> list:
    """Every record set the handler builds for ``paths``, as one batch.

    ``root`` gives the files the paths a scan would have handed them, which is
    what the identifier tests turn on.
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


#: What each modern deposit calls its sample-level fields. The two sets are
#: disjoint, which is the harmonization problem and the reason to read the
#: format: thirteen keys across two accepted human-tumour deposits, no overlap.
CHARACTERISTIC_KEYS = {
    GEO_EXPORT: {
        "tissue",
        "tissue preservation method",
        "treatment",
        "dbgap_subject_id",
        "case_diagnosis",
        "genotype",
    },
    SECOND_EXPORT: {
        "gender",
        "age",
        "tumor location",
        "tumor size",
        "mgmt methylation",
        "egfr methylation",
        "patient id",
    },
}


@pytest.mark.parametrize("export", CHARACTERISTIC_KEYS, ids=lambda p: p.name)
def test_every_characteristic_key_becomes_a_field_of_its_own(export: Path) -> None:
    """Criterion 1. Exactly these, no more: a key not emitted is a target field
    nobody can map, and one invented is a claim the deposit does not make."""
    characteristics = one(build(export), "_sample_characteristics")

    assert {f.name for f in characteristics.fields} == CHARACTERISTIC_KEYS[export]


# ---------------------------------------------------------------------------
# 2 — no value is emitted
# ---------------------------------------------------------------------------
#
# Characterised rather than searched. A substring sweep has to pick a length
# threshold to dodge coincidences, and everything under it goes unchecked. These
# two tests instead pin every string the handler can emit: a field name must be
# a name the file declares, and a description must be built from nothing but
# counts, the stored filename, a field name, and a verbatim #COLUMN line. There
# is then nowhere for a value to go.

_ATTRIBUTE = re.compile(
    r"^!(?:Database|Series|Sample|Platform)_(?P<name>[^=]*?)\s*=", re.I | re.M
)
_CHARACTERISTIC = re.compile(
    r"^!Sample_characteristics(?:_ch(?P<channel>\w+))? = (?P<key>[^:]+):(?: |$)",
    re.I | re.M,
)
_TABLE_HEADER = re.compile(r"^![a-z]+_table_begin\n(?P<header>.*)$", re.I | re.M)
_COLUMN_LINE = re.compile(r"^#(?P<column>[^=]+)=(?P<description>.*)$", re.M)


def declared_names(path: Path) -> set:
    """Every name a SOFT file declares, re-derived without the parser.

    Deliberately independent of the implementation under test, and deliberately
    a superset on characteristics: both the bare key and its ``chN_`` form are
    admitted, since which is emitted depends on how many channels the file uses.
    """
    text = text_of(path)
    names = {m.group("name") for m in _ATTRIBUTE.finditer(text)}
    for match in _CHARACTERISTIC.finditer(text):
        key = match.group("key").strip()
        names.add(key)
        names.add(f"ch{match.group('channel') or ''}_{key}")
    for match in _TABLE_HEADER.finditer(text):
        names.update(match.group("header").split("\t"))
    return {name for name in names if name}


def column_lines(path: Path) -> set:
    return {
        m.group(0).strip()
        for m in _COLUMN_LINE.finditer(text_of(path))
        if m.group("description").strip()
    }


@pytest.fixture(params=[GEO_EXPORT, TABLES], ids=["tableless", "tables"])
def any_export(request) -> Path:
    """Both shapes, so the checks below see characteristics and tables alike."""
    return request.param


#: Every record set ends the same way, which is the guide's promise in code.
_TAIL = r"\. No value is emitted\.( Partial parse: [^.]+\.)?$"

_RECORD_SET_DESCRIPTIONS = [
    re.compile(head + _TAIL)
    for head in (
        r"^(Series|Sample|Platform)-level attributes in (?P<file>\S+) "
        r"\(\d+ (series|samples?|platforms?), \d+ attribute names?\)",
        r"^Submitter-defined sample characteristics in (?P<file>\S+) "
        r"\(\d+ samples?, \d+ keys?"
        r"(, and \d+ lines? that were not 'key: value')?\)",
        r"^Inline data table of \d+ (samples?|platforms?) in (?P<file>\S+) "
        r"\(\d+ columns?(, \d+ unnamed)?"
        r"(, \d+ rows declared|, row count not declared)\)",
    )
]


def _allowed_descriptions(name: str, documented: set) -> set:
    """Every description the handler is permitted to build for one field."""
    return (
        {f"{noun} attribute '{name}'" for noun in ("Series", "Sample", "Platform")}
        | {f"Characteristic '{name}'", f"Column '{name}'"}
        | {f"Column '{name}'. {line}" for line in documented}
    )


def test_no_value_is_emitted(any_export: Path) -> None:
    """Criterion 2, over every string the handler can produce.

    Every emitted string is pinned rather than searched for: a field name
    against the names the file declares, both kinds of description against a
    template, and the absence of a ``value`` structurally, since one would
    bypass the templates entirely. There is then nowhere for a value to go.
    """
    declared = declared_names(any_export)
    documented = column_lines(any_export)

    record_sets = build(any_export)

    assert record_sets, "nothing was described"
    for record_set in record_sets:
        matched = (p.match(record_set.description) for p in _RECORD_SET_DESCRIPTIONS)
        match = next((m for m in matched if m), None)
        assert match, record_set.description
        assert match.group("file") == any_export.name
        assert record_set.fields, record_set.id
        for field in record_set.fields:
            assert field.name in declared, field.name
            assert field.value is None, field.name
            assert field.description in _allowed_descriptions(field.name, documented)


def test_the_names_the_test_re_derives_are_not_the_values() -> None:
    """A guard on the guard. If ``declared_names`` admitted values, the two
    tests above would pass while values leaked."""
    declared = declared_names(GEO_EXPORT)

    assert "tissue" in declared
    assert "pleural mesothelioma" not in declared


def test_every_entity_field_is_text() -> None:
    """Criterion 3. Coercing before typing turns ``dbgap_subject_id: 27278``
    into a measurement."""
    entity_sets = [
        rs
        for rs in build(GEO_EXPORT)
        if rs.id.endswith(("_series", "_samples", "_platforms", "_characteristics"))
    ]

    assert len(entity_sets) == 4
    assert {str(t) for rs in entity_sets for f in rs.fields for t in f.data_types} == {
        "sc:Text"
    }


def test_no_source_carries_an_extract() -> None:
    """Criterion 3. What stops a later contributor "improving" the manifest
    into a promise mlcroissant cannot keep."""
    sources = [f.source for rs in build(GEO_EXPORT, TABLES) for f in rs.fields]

    assert sources
    assert all(s.extract == type(s.extract)() for s in sources)
    assert all(s.file_object for s in sources)


def test_a_repeated_attribute_is_one_array_field(dataset: Path) -> None:
    """Criterion 4."""
    path = write_soft(
        dataset,
        "GSE1_family.soft",
        "^SAMPLE = GSM1\n!Sample_description = one\n!Sample_description = two\n",
    )

    (field,) = one(build(path), "_samples").fields

    assert (field.name, field.is_array, field.array_shape) == (
        "description",
        True,
        "-1",
    )


def test_an_attribute_and_a_characteristic_may_share_a_name(dataset: Path) -> None:
    """Criterion 5, and the second reason the characteristics stand alone: one
    record set holding two fields called ``title`` is one no reader could use."""
    path = write_soft(
        dataset,
        "GSE1_family.soft",
        "^SAMPLE = GSM1\n"
        "!Sample_title = an attribute\n"
        "!Sample_characteristics_ch1 = title: a characteristic\n",
    )

    built = {rs.id: rs for rs in build(path)}
    attribute = built["GSE1_family_samples"].fields[0]
    characteristic = built["GSE1_family_sample_characteristics"].fields[0]

    assert attribute.name == characteristic.name == "title"
    assert attribute.id != characteristic.id


# ---------------------------------------------------------------------------
# 6 — identifiers come from paths, not from discovery order
# ---------------------------------------------------------------------------


def sibling_tree(root: Path) -> list:
    payload = SAMPLES["SOFTHandler"]()[0][1]
    paths = []
    for parent in ("a", "b"):
        (root / parent).mkdir(parents=True)
        target = root / parent / "GSE1_family.soft"
        target.write_bytes(payload)
        paths.append(target)
    return paths


def test_same_basename_in_two_directories_stays_apart(dataset: Path) -> None:
    """Criterion 6."""
    paths = sibling_tree(dataset)

    ids = [rs.id for rs in build(*paths, root=dataset)]

    assert len(ids) == len(set(ids)) == 12
    assert all(rs_id.startswith(("a__", "b__")) for rs_id in ids), ids


def test_identifiers_do_not_depend_on_discovery_order(dataset: Path) -> None:
    """Criterion 6, second half. Batch order is rglob order, fixed within a
    process, so no parallel-determinism test would catch this."""
    paths = sibling_tree(dataset)

    forward = {rs.id for rs in build(*paths, root=dataset)}
    backward = {rs.id for rs in build(*reversed(paths), root=dataset)}

    assert forward == backward


def test_each_column_signature_is_described_once() -> None:
    """Criterion 6b. Ten near-identical record sets would be noise; a
    nine-against-one split is information. GSE1000 declares ``SIG_LOG2`` on one
    of its ten samples and ``SIGNAL_Log2`` on the other nine."""
    described = {rs.id: rs.description for rs in build(TABLES) if "_table" in rs.id}

    assert set(described) == {
        "GSE1000_family_platform_table",
        "GSE1000_family_sample_table",
        "GSE1000_family_sample_table_2",
    }
    assert "1 platform" in described["GSE1000_family_platform_table"]
    assert "1 sample" in described["GSE1000_family_sample_table"]
    assert "9 samples" in described["GSE1000_family_sample_table_2"]


def test_a_table_column_is_typed_from_a_sample_of_its_rows() -> None:
    """Criterion 6c."""
    columns = {
        f.name: str(f.data_types[0]) for f in one(build(TABLES), "_sample_table").fields
    }

    assert columns == {
        "ID_REF": "sc:Text",
        "VALUE": "cr:Float64",
        "SIG_LOG2": "cr:Float64",
    }


def test_a_columns_description_carries_its_column_line_verbatim() -> None:
    """Criterion 6c. The provenance is the point: a reader has to be able to
    see the prose came from the deposit's own ``#COLUMN`` line."""
    columns = {
        f.name: f.description for f in one(build(TABLES), "_sample_table").fields
    }

    assert columns["VALUE"] == (
        "Column 'VALUE'. #VALUE = Intensity calculated by \"affy\" package in R"
    )
    assert columns["ID_REF"] == "Column 'ID_REF'"


def test_the_row_sample_is_released_once_it_has_been_typed() -> None:
    """The sample is read and discarded, not read and kept. Held on, it would
    keep a MiB per signature alive for the whole bake — and a buffer of cells
    inside the extracted metadata is a cell one edit away from the document."""
    described = extract(TABLES)["tables"]

    assert [d.column_types["VALUE"] for d in described if "VALUE" in d.column_types]
    assert all(d.table.sample == b"" for d in described)


def test_a_row_count_comes_from_the_declaration() -> None:
    """Criterion 6d. Every table in this fixture declares 22 283 rows and was
    trimmed to ten, so a handler that counted would report 10 — and 90 for the
    shared signature, whose declarations are summed the way the Parquet handler
    sums shards."""
    described = {rs.id: rs.description for rs in build(TABLES)}

    assert "22283 rows declared" in described["GSE1000_family_sample_table"]
    assert "200547 rows declared" in described["GSE1000_family_sample_table_2"]


def test_an_entity_kind_with_no_fields_produces_no_record_set() -> None:
    """Criterion 7. GSE1000 carries no characteristics at all, and mlcroissant
    validates an empty record set, which is why the handler has to refuse one
    rather than emit it."""
    ids = {rs.id for rs in build(TABLES)}

    assert "GSE1000_family_sample_characteristics" not in ids
    assert "GSE1000_family_samples" in ids


# ---------------------------------------------------------------------------
# 8 — one forward pass, bounded memory
# ---------------------------------------------------------------------------


def counting_source(path: Path) -> tuple:
    """A source that hands back every stream it opened, still inspectable."""
    opened: list = []

    def opener():
        stream = path.open("rb")
        opened.append(stream)
        return stream

    source = FileSource(
        name=path.name,
        relative_path=Path(path.name),
        size=path.stat().st_size,
        exists=True,
        _open_binary=opener,
        _digest=lambda: "0" * 64,
    )
    return source, opened


def test_the_stream_is_closed_once_the_export_has_been_read() -> None:
    """A bake of a directory of deposits must not leave one handle open per
    file. ``source.open()`` returns a plain stream rather than a context
    manager, so dropping the ``with`` around it costs nothing a bake can see.
    """
    source, opened = counting_source(TABLES)

    HANDLER.extract(source)

    assert opened, "the handler never opened the file"
    assert all(stream.closed for stream in opened)


def write_large_export(path: Path, samples: int = 20, wide_rows: int = 10_500) -> None:
    """A 22 MB export whose bulk is one wide platform table.

    Twenty-odd MB rather than the two hundred an earlier draft proposed, because
    ``@pytest.mark.slow`` skips nothing here: ``[tool.pytest.ini_options]``
    registers no markers and sets no ``addopts``.
    """
    wide = "\t".join(f"col{i}" for i in range(16))
    wide_row = "\t".join("x" * 120 for _ in range(16))
    with path.open("w", encoding="utf-8") as fh:
        fh.write("^SERIES = GSE1\n!Series_title = large\n")
        fh.write(f"^PLATFORM = GPL1\n!Platform_data_row_count = {wide_rows}\n")
        fh.write(f"!platform_table_begin\nID\t{wide}\n")
        for row in range(wide_rows):
            fh.write(f"{row}\t{wide_row}\n")
        fh.write("!platform_table_end\n")
        for sample in range(samples):
            fh.write(f"^SAMPLE = GSM{sample}\n!Sample_data_row_count = 5000\n")
            fh.write("!sample_table_begin\nID_REF\tVALUE\n")
            for row in range(5000):
                fh.write(f"probe_{row}\t{row}.5\n")
            fh.write("!sample_table_end\n")


def test_a_large_export_costs_one_pass_and_bounded_allocation(tmp_path: Path) -> None:
    """Criterion 8. Everything this handler holds is a Python object — the row
    sample is a ``bytearray``, and it is the only thing PyArrow is ever handed
    — so ``tracemalloc`` is exact about the peak rather than a proxy for it.
    """
    path = tmp_path / "GSE_large_family.soft"
    write_large_export(path)
    assert path.stat().st_size > 20_000_000
    source, opened = counting_source(path)

    tracemalloc.start()
    try:
        meta = HANDLER.extract(source)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert len(opened) == 1, "the file was read more than once"
    assert peak < 8 * 1024 * 1024, f"peaked at {peak} bytes on a 22 MB file"
    assert meta["soft"].kinds["SAMPLE"].entities == 20
    assert [d.table.rows for d in meta["tables"]] == [10_500, 100_000]


def test_an_undecodable_byte_costs_that_line_and_nothing_more(
    dataset: Path, caplog
) -> None:
    """Criterion 9, on a synthetic fixture: the real deposits are all valid
    UTF-8, so a byte that does not decode has to be manufactured by rewriting
    ``\\xc2\\xba`` down to a bare ``\\xba``."""
    path = dataset / "GSE1_family.soft"
    path.write_bytes(
        b"^SAMPLE = GSM1\n!Sample_description = 37\xbaC\n!Sample_title = still here\n"
    )

    with caplog.at_level(logging.WARNING):
        samples = one(build(path), "_samples")

    assert [f.name for f in samples.fields] == ["description", "title"]
    assert "1 line(s) could not be decoded" in samples.description
    assert "1 sample" in samples.description
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_a_table_left_open_is_reported_on_every_record_set(
    dataset: Path, caplog
) -> None:
    """Criterion 12. A syntactically valid Croissant file that silently claims
    to be complete is worse than an explicit failure."""
    path = write_soft(
        dataset,
        "GSE1_family.soft",
        "^SERIES = GSE1\n!Series_title = a series\n"
        "^SAMPLE = GSM1\n!Sample_title = a sample\n"
        "!sample_table_begin\nID_REF\tVALUE\n1_at\t3\n",
    )

    with caplog.at_level(logging.WARNING):
        built = build(path)

    assert {rs.id for rs in built} == {
        "GSE1_family_series",
        "GSE1_family_samples",
        "GSE1_family_sample_table",
    }
    assert all("still open at end of file" in rs.description for rs in built)
    assert "1 series" in built[0].description
    assert any("as far as it goes" in r.message for r in caplog.records)


def test_a_file_that_simply_ends_is_not_reported_as_partial(dataset: Path) -> None:
    """Criterion 12's negative half. An attribute block has no closing marker,
    so ending after one is how every SOFT file ends."""
    path = write_soft(
        dataset, "GSE1_family.soft", "^SERIES = GSE1\n!Series_title = a\n"
    )

    assert "Partial parse" not in build(path)[0].description


# ---------------------------------------------------------------------------
# 10, 11 — what this handler is not
# ---------------------------------------------------------------------------


def test_a_soft_file_with_no_entity_line_raises_naming_the_file(
    dataset: Path,
) -> None:
    """Criterion 10. Reported as ``extract_failed``, which is accurate: the
    file really could not be read."""
    path = write_soft(dataset, "notes.soft", "this is prose, not a deposit\n")

    with pytest.raises(ValueError, match="notes.soft"):
        extract(path)


SERIES_MATRIX = (
    '!Series_title\t"A series"\n'
    '!Series_geo_accession\t"GSE1000"\n'
    '!Sample_title\t"first"\t"second"\n'
    "!series_matrix_table_begin\n"
    '"ID_REF"\t"GSM15785"\t"GSM15786"\n'
    '"1007_s_at"\t320.46\t305.37\n'
    "!series_matrix_table_end\n"
)


def test_a_series_matrix_is_a_different_grammar_and_is_refused(dataset: Path) -> None:
    """The most commonly downloaded GEO artifact for an expression study, and a
    different grammar that only looks similar: no ``^`` entity lines, tabs
    rather than ``` = ```, quoted values, ``!Sample_*`` transposed to one value
    per sample. Supporting it is a second reader."""
    matrix = write_soft(dataset, "GSE1000_series_matrix.txt", SERIES_MATRIX)
    assert HANDLER.claims(make_source(matrix, Path(matrix.name))) is False

    misnamed = write_soft(dataset, "GSE1000_series_matrix.soft", SERIES_MATRIX)
    with pytest.raises(ValueError, match="series_matrix"):
        extract(misnamed)


@pytest.mark.parametrize("handler", builtin_handlers(), ids=lambda h: type(h).__name__)
def test_only_this_handler_claims_a_bare_soft_file(handler, dataset: Path) -> None:
    """Criterion 11. The shared sweep does not cover this half: its negative
    case writes ``SAMPLES[owner]()[0]``, so it feeds other handlers this
    handler's own sample *filename* and never a bare ``.soft``."""
    path = write_soft(dataset, "probe.soft", "^SERIES = GSE1\n!Series_title = a\n")

    claimed = handler.claims(make_source(path, Path("probe.soft")))

    assert claimed is (type(handler) is SOFTHandler)


def test_the_wrapper_never_reaches_an_identifier() -> None:
    """``.gz`` is transport. It reaches the prose that names the file on disk —
    which criterion 2 asserts — and never the ``@id``."""
    assert one(build(GEO_EXPORT), "_series").id == "GSE327347_family_series"


# ---------------------------------------------------------------------------
# Headers a real deposit does not write, but a parser still meets
# ---------------------------------------------------------------------------


def test_an_unnamed_column_is_dropped_and_counted(dataset: Path) -> None:
    """A Field with no name is one mlcroissant cannot describe."""
    path = write_soft(
        dataset,
        "GSE1_family.soft",
        "^SAMPLE = GSM1\n!Sample_data_row_count = 1\n"
        "!sample_table_begin\nID_REF\t\tVALUE\n1_at\t2\t3\n!sample_table_end\n",
    )

    table = one(build(path), "_sample_table")

    assert [f.name for f in table.fields] == ["ID_REF", "VALUE"]
    assert "2 columns, 1 unnamed" in table.description


def test_a_header_naming_no_column_produces_no_table_record_set(
    dataset: Path, caplog
) -> None:
    """A Field with no name is one mlcroissant cannot describe and an empty
    record set is one it validates, so the table is dropped and said to be
    dropped. A deposit carrying nothing else then describes nothing at all —
    a list the caller has to tolerate, not a refusal this handler invents.
    """
    table = "!sample_table_begin\n\n1_at\n!sample_table_end\n"
    with_attributes = write_soft(
        dataset, "GSE1_family.soft", f"^SAMPLE = GSM1\n!Sample_title = a\n{table}"
    )
    bare = write_soft(dataset / "bare", "GSE1_family.soft", f"^SAMPLE = GSM1\n{table}")

    with caplog.at_level(logging.WARNING):
        described = build(with_attributes)
        nothing = build(bare)

    assert [rs.id for rs in described] == ["GSE1_family_samples"]
    assert nothing == []
    assert sum("naming no column" in r.message for r in caplog.records) == 2


def test_a_duplicate_column_name_survives_under_distinct_ids(dataset: Path) -> None:
    """PyArrow does not deduplicate a TSV header the way pandas does."""
    path = write_soft(
        dataset,
        "GSE1_family.soft",
        "^SAMPLE = GSM1\n!Sample_data_row_count = 1\n"
        "!sample_table_begin\nID_REF\tID_REF\n1_at\t2\n!sample_table_end\n",
    )

    table = one(build(path), "_sample_table")

    assert [f.name for f in table.fields] == ["ID_REF", "ID_REF"]
    assert len({f.id for f in table.fields}) == 2


def test_an_attribute_name_survives_into_the_field_but_not_into_the_id(
    dataset: Path,
) -> None:
    """``!Sample_contact_zip/postal_code`` is real, and a ``/`` is not legal in
    an ``@id``: the name keeps the deposit's spelling while the id is
    sanitized. ``! = nothing`` names nothing, and a Field needs a name.
    """
    path = write_soft(
        dataset,
        "GSE1_family.soft",
        "^SAMPLE = GSM1\n! = nothing\n!Sample_contact_zip/postal_code = 27710\n",
    )

    (field,) = one(build(path), "_samples").fields

    assert field.name == "contact_zip/postal_code"
    assert field.id == "GSE1_family_samples/contact_zip_postal_code"


def test_the_counts_read_as_prose(dataset: Path) -> None:
    """``1 attribute names`` and ``2 seriess`` are the sort of thing a reviewer
    finds and nobody else fixes."""
    one_each = write_soft(dataset, "one.soft", "^SERIES = GSE1\n!Series_title = a\n")
    two_each = write_soft(
        dataset,
        "two.soft",
        "^SERIES = GSE1\n!Series_title = a\n^SERIES = GSE2\n!Series_summary = b\n",
    )

    assert "1 series, 1 attribute name)" in build(one_each)[0].description
    assert "2 series, 2 attribute names)" in build(two_each)[0].description
