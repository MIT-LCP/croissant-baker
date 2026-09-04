# Supported Formats

croissant-baker detects file types automatically. Handlers are checked in the order listed below — the first match wins.

Nothing is skipped in silence, and nothing is reported one line per file by default. The closing coverage summary counts every file the document does not carry, under the reason it was passed over. The summary is a fixed size — a header plus at most one line per reason — so a directory with one undescribed file and one with ten thousand print the same shape. The full list is under `--verbose`, or in the machine-readable `--report` file:

```text
Scanned 8 file(s): 4 described, 1 linked, 1 referenced, 2 not described.
  no handler: 1
  archive, not opened: 1
```

A file reaches the document three ways, and the header names each one that
happened. `described` means a handler read the file and built its record set.
`linked` means the file is another described file in a different form — a
`.csv.gz` beside its `.csv` — so its bytes are described and its structure is
its twin's. `referenced` means another file's handler put it there: a WFDB
header is read together with its `.dat` and `.atr`, and each gets a FileObject
though nothing claims a `.dat` on its own. Only `not described` counts files the
document does not carry, and the reason lines account for exactly those.

`--report FILE` writes one JSON object per run. `outcome` and `reason` come from
fixed vocabularies, so a tool can branch on them without parsing English:

```json
{
  "total": 8,
  "described": 4,
  "linked": 1,
  "referenced": 1,
  "undescribed": 2,
  "by_reason": {"no_handler": 1, "archive": 1},
  "files": [
    {"path": "notes.txt", "outcome": "unclaimed", "reason": "no_handler",
     "detail": "no handler for this file type"},
    {"path": "sample.csv.gz", "outcome": "linked", "reason": "duplicate_by_name",
     "detail": "same logical name as sample.csv; linked by naming convention, content not verified",
     "duplicate_of": "sample.csv"},
    {"path": "100.dat", "outcome": "referenced",
     "detail": "described as part of 100.hea", "part_of": "100.hea"}
  ]
}
```

`described`, `linked`, `referenced` and `undescribed` sum to `total`, so a tool
can check coverage without reading the file list. `by_reason` accounts for the
`undescribed` alone.

`outcome` is one of `described`, `linked`, `referenced`, `unclaimed`, `failed`,
or `would_process` (`--dry-run` only). `reason` is one of `no_handler`,
`archive`, `unsupported_input`, `claim_failed`, `extract_failed`,
`build_failed`, `partition_schema_conflict`, `duplicate_by_name`, or
`probable_duplicate`, and is absent on `described` and `referenced`.

## File types

--8<-- "_generated/formats-table.md"

## Compression

`.gz`, `.bz2` and `.xz` are transport, not format. They are resolved before any handler is consulted, so every format in the table above is described the same way whether or not it arrived wrapped: `cells.parquet.gz` produces the record set that `cells.parquet` produces.

What the wrapper changes is the distribution entry, which addresses the bytes on disk. `encodingFormat` carries both media types, and `contentUrl`, `contentSize` and `sha256` are of the file as stored:

```json
{
  "@type": "cr:FileObject",
  "name": "cells.parquet.gz",
  "contentUrl": "cells.parquet.gz",
  "encodingFormat": ["application/vnd.apache.parquet", "application/gzip"]
}
```

Record set identifiers are derived from the logical file — `cells.parquet` — so a compressed file and its plain twin describe one table rather than two. Descriptions name the file as stored (`Records from cells.parquet.gz`), because a description has to name something you can find on disk. FileSet `includes` are resolved against the files actually present, so a compressed file is covered by the FileSet describing it.

WFDB is the exception. A WFDB record is a `.hea` header read together with its sibling `.dat` and `.atr` files, so no single stream carries it; a compressed `.hea` is reported with that as its reason rather than described.

`.zip`, `.tar` and `.tgz` hold several members. They are reported as archives and not opened.

Registering another compression is a library call and needs no handler change:

```python
import zstandard
from croissant_baker import compression

compression.register_compression(
    compression.Compression("zstd", ".zst", "application/zstd", zstandard.open)
)
```

## Duplicate files

Two files in one directory can describe the same data, which would otherwise put two record sets under one identifier and abort the bake. One is described and the others link to it with `sameAs`, keeping their own distribution entries — their bytes, sizes and checksums are their own.

| Shape | How it is decided |
|-------|-------------------|
| `sample.csv` and `sample.csv.gz` | Linked on the naming convention. Contents are not compared, and the reason says so |
| `sample.csv.gz` and `sample.csv.xz` | Linked only if the first 64 KiB decompress identically |
| `sample.csv` and `sample.tsv` | Two plain files of different size are rejected outright; otherwise the same 64 KiB comparison. If they differ, both are described under distinct identifiers (`sample_csv`, `sample_tsv`) |

The file that keeps its structure is chosen deterministically: uncompressed first, then the order compressions were registered in, then the path.

## CSV and TSV

CSV and TSV files are read with PyArrow's streaming reader — memory is constant regardless of file size. Type inference runs in two passes: an initial sweep, then per-column promotion when the first pass hits a type conflict.

Row counts are omitted by default for speed. Pass `--count-csv-rows` to do a full scan for exact counts (slow on large datasets).

## FHIR (`.ndjson`, `.json` Bundle)

Two FHIR serialization formats are supported:

- **NDJSON bulk export** (`.ndjson`): one resource per line, all of the same `resourceType`. Produced by FHIR Bulk Data servers.
- **JSON Bundle** (`.json`): a FHIR Bundle whose `entry[]` may contain mixed resource types.

Field names and types are inferred from a sample of resources. `OperationOutcome` resources (error markers) are skipped.

!!! note
    FHIR `.json` files are detected by content — the handler looks for `"resourceType": "<UpperCase…"` before accepting. Plain JSON files that happen to use `.json` are handled by the JSON handler instead.

## JSON and JSONL

- **JSON** (`.json`): an array of objects (one object per record) or a single object (treated as one record).
- **JSONL** (`.jsonl`): newline-delimited JSON, one object per line.

Schema is inferred from a sample of records. FHIR `.json` files are excluded — they go to the FHIR handler.

## Parquet

Schema is read from Parquet metadata only — the file data is never loaded.

Spark and Arrow write one table as a directory of `part-*.parquet`, while a vendor export directory holds several unrelated tables. Directory membership cannot tell those apart, so two files are shards of one logical table — one `cr:FileSet` and one `cr:RecordSet` — only when both hold:

| Evidence | What counts |
|----------|-------------|
| A shard-shaped name | The names match once digit runs are masked, and at least one of those runs is a name component of its own: the whole stem (`0.parquet`) or introduced by `-`, `_` or `.` (`part-00000.parquet`). Digits fused to letters are part of a word, so `assay1.parquet` and `assay2.parquet` stay two tables |
| An identical Arrow schema | Compared as Arrow types, not as Croissant types, which collapse timestamp units, nullability, decimal precision and nested structure |

Anything else is described on its own, as CSV and JSON files are. Files at the dataset root never pair.

Where files agree on the name but not the schema, that is drift inside one table: the majority schema is described and the rest are reported with reason `partition_schema_conflict` rather than folded in. The `cr:FileSet` then lists its shards individually — a directory-wide glob would re-admit exactly the files that were kept out.

## WFDB

WFDB (WaveForm DataBase) is the standard physiological signal format on PhysioNet. The handler reads the `.hea` header file and records signal channel names, sampling frequency, number of samples, and duration. Associated `.dat` binary files are listed as related files.

Because a record spans several files located by path, this is the one handler that needs a real file on disk. A compressed `.hea` is reported rather than described.

## Images

Standard images are read with Pillow. Multi-band or scientific TIFFs fall back to `tifffile`. All images in a dataset are grouped into one `cr:FileSet` with a single summary `cr:RecordSet` covering width, height, color mode, and encoding format.

## DICOM

DICOM (`.dcm`, `.dicom`) is the standard format for medical imaging (CT, MRI, PET, etc.). The handler uses `pydicom` with `stop_before_pixels=True` — only the file header is read, so large pixel arrays are never loaded into memory.

Extracted metadata: image dimensions (rows, columns), number of frames, bits allocated per pixel, photometric interpretation, pixel spacing, slice thickness, modality, study/series description, manufacturer, and SOP class UID.

Files with no extension are also accepted if they carry the DICOM magic bytes (`DICM` at byte offset 128), which is common in PACS exports.

All DICOM files in a dataset are grouped into one `cr:FileSet` with a summary `cr:RecordSet` covering modality counts and dimension ranges.

## NIfTI

NIfTI (`.nii`) is the standard format for neuroimaging data (structural MRI, fMRI, CT). The handler uses `nibabel` and reads the header only — the voxel data array is never loaded.

Extracted metadata: spatial dimensions (x, y, z), number of timepoints for 4D volumes, voxel spacing in mm, stored data type, NIfTI version (1 or 2), and repetition time (TR) for fMRI data.

All NIfTI files in a dataset are grouped into one `cr:FileSet` with a summary `cr:RecordSet`. The `tr_seconds` field is only added when at least one 4D volume is present.

## GEO SOFT

SOFT is the native metadata export of the NCBI Gene Expression Omnibus, and a `.soft` family export is where a deposit's sample-level fields live: `!Sample_characteristics_ch1 = gender: female` is how GEO records sex, age, treatment, tissue and diagnosis, and there is no other machine-readable place in a deposit that holds them. The grammar is the same whatever the assay — microarray, RNA-seq, ChIP-seq, methylation, single-cell, spatial.

"GEO support" here means the **`.soft` family export** and nothing else. `GSE*_series_matrix.txt` is a different grammar that only looks similar — no `^` entity lines, tab-separated rather than ` = `, quoted values, and `!Sample_*` transposed to one value per sample — and is not read. Neither are `GSM*.txt` or `GPL*.annot`.

Three kinds of record set come out of one file:

| Record set | One row per | Fields |
|------------|-------------|--------|
| `<stem>_series`, `<stem>_samples`, `<stem>_platforms` | one entity of that kind | each attribute name the deposit uses, with its `Series_` / `Sample_` / `Platform_` prefix removed |
| `<stem>_sample_characteristics` | one sample | one field per distinct `!Sample_characteristics_ch*` key |
| `<stem>_sample_table`, `<stem>_platform_table` | one row of a data table | the table's columns |

`^DATABASE` is GEO boilerplate and is not described. An entity kind with no fields produces no record set rather than an empty one.

**The characteristics stand apart from the attributes** because they are different things: `!Sample_*` is a closed GEO vocabulary, while the characteristics are whatever the submitter typed — and it is the submitter's keys a mapping step has to reconcile. It also keeps `!Sample_title` and a characteristic `title:` from becoming two fields called `title` in one record set.

**Data tables group on their exact column signature**, one record set per signature, with the number of entities sharing it in the description. GEO's GSE1000, for example, yields three: the platform's 16-column annotation table, `(ID_REF, VALUE, SIG_LOG2)` for one of its ten samples, and `(ID_REF, VALUE, SIGNAL_Log2)` for the other nine. Ten near-identical record sets would be noise; a 9-against-1 split is information. Row counts come from `!*_data_row_count`, so no table body is counted, and only a bounded sample of rows is buffered — a 50 MB export is read in one forward pass at constant memory.

### Names, not values

**No value is emitted.** Every field is a *name*: an attribute name, a characteristic key, or a column header. Two deposits of the same assay family sharing no characteristic key is exactly what a mapping step has to reconcile, and the values are the deposit's own.

Table cells are the one thing that is *read*: a bounded sample of rows goes to PyArrow so the columns can be typed, and the buffer is released as soon as it has. Nothing from it reaches the document. Every record set — the table ones included, where the question is sharpest — ends its description with `No value is emitted.`, so a consumer reading one in isolation knows what it is holding.

Attribute and characteristic fields are all `sc:Text`. SOFT is untyped text and declares nothing, and coercing before typing turns `dbgap_subject_id: 27278` into a measurement a downstream step would then trust. Table columns *are* typed, through the same PyArrow path a `.csv` takes: a column is thousands of values under a declared header, which is a different thing from one free-text value per entity. A column's description carries the deposit's own `#COLUMN` line verbatim — `Column 'VALUE'. #VALUE = Intensity calculated by "affy" package in R` — so its provenance is visible.

Fields carry `source: {fileObject: …}` and **no `extract`**. Croissant 1.1's extract grammar offers `column`, `fileProperty` and `jsonPath`, none of which addresses a repeated `!Sample_characteristics_ch1` key, and `mlcroissant` cannot read this format at all — its reader dispatches on `encodingFormat` over a fixed list that SOFT is not on. An `extract` here would be a promise nobody can keep. The record sets are descriptive, as every Parquet, JSONL and DICOM record set already is.

The grammar a consumer needs in order to read the values itself is `!Sample_characteristics_ch1 = <key>: <value>`, scoped to the open `^SAMPLE` entity.

One file yields several record sets, so their identifiers are derived from its stem: `GSE1_family.soft` gives `GSE1_family_series`, `GSE1_family_samples` and so on. Where a derived identifier collides with one another *format* produces — a `GSE1_family_samples.csv` in the same directory — both are suffixed with their format, exactly as `sample.csv` and `sample.tsv` become `sample_csv` and `sample_tsv`. Neither side keeps the bare name.

`encodingFormat` is `text/x-geo-soft`. GEO SOFT has no IANA registration; the `x-` form follows `application/x-nifti`.

### When a file does not read cleanly

A `.soft` carrying no `^ENTITY` line is reported with reason `extract_failed`, which is accurate — the file really could not be read as SOFT.

A file that *does* parse but runs out part-way says so in every record set description it produced, and logs a warning. Two conditions can fire: a data table still open at end of file, and a line that could not be decoded. A file that simply ends after a complete attribute line is **not** partial — an attribute block has no closing marker, so that is how every SOFT file ends.

GEO declares no encoding. Every export tested decodes cleanly as strict UTF-8 — the `37ºC` in a 2004 deposit is `0xC2 0xBA`, and reading the file as latin-1 is what corrupts it to `37ÂºC` — but the format guarantees nothing, so a line that does not decode costs that line's fidelity and nothing more.

## Hidden files and directories

Files inside hidden directories (any path component starting with `.`) are always skipped, and do not appear in the coverage report. Use `--include` and `--exclude` glob patterns to further control which files are processed.
