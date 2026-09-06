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

Standard images (`.png`, `.jpg`, `.gif`, `.bmp`, `.webp`, `.ico`) are read with Pillow. Every TIFF — `.tif`, `.tiff` and the BigTIFF spelling `.btf` — is read with `tifffile`. Pillow opens some TIFFs too, but reports one band for a three-channel image, decodes the ImageDescription tag as latin-1 so `µm` comes back mojibake, and opens none of the twelve-band rasters this repository carries.

BigTIFF is not a separate format but classic TIFF's 64-bit offset field, which any writer switches to at 4 GiB — the size whole-slide imaging, electron-microscopy volumes and large rasters all cross. `image_format` stays `TIFF` for a `.btf` and `encodingFormat` stays `image/tiff`: BigTIFF has no registration of its own.

Images are grouped into one `cr:FileSet` and one summary `cr:RecordSet` covering width, height, band count and encoding format. Only the header is read, at any file size.

!!! note
    Reading a wrapped TIFF costs more than reading a wrapped file of any other format. A backward seek on a compressed stream is a decompression from offset 0, and `tifffile` seeks to the end of the file once and rewinds two or three times — so a `.tif.gz` is decompressed two to three times over. `.bz2` and `.xz` are far dearer again. An uncompressed TIFF pays none of this.

### OME-TIFF

OME-TIFF is the interchange format of light microscopy: what Bio-Formats writes, and what OMERO and the Image Data Resource serve. Its TIFF header carries an OME-XML document describing the image, and those files get a collection of their own:

| Node | Content |
|------|---------|
| `cr:FileSet` `ome-image-files` | the OME files, listed individually |
| `cr:RecordSet` `ome_images` | one row per OME **file**, with the fields below |

`ome_version`, `ome_image_count`, `size_c`, `size_z`, `size_t`, `dimension_order`, `pixel_type`, `physical_size_x`, `physical_size_y`, `physical_size_unit`, and `channel_names` as one array field. A field whose OME attribute no file in the batch declares is not emitted.

**The OME files leave the `images` collection.** Ten OME fields on the shared record set would attribute a pixel size to every PNG in the same directory, so the two collections partition the batch instead: every image is in exactly one. A consumer reading only `images` stops seeing the OME files. The `ome-image-files` FileSet lists its files rather than globbing, because a `**/*.tif` pattern would re-admit the plain TIFFs beside them; for the same reason, `images` lists any file whose extension an OME file also uses, and keeps a glob for every other extension.

**Rows are files, not images.** One OME-XML document may declare several `<Image>` elements, and one logical image may be spread over several `.ome.tif` files linked by `TiffData/UUID/@FileName`. Neither is grouped here. So the Pixels fields describe `Image[0]` of each file, `ome_image_count` says how many the file declared, and the record-set description states both. `num_bands` keeps its meaning — TIFF `SamplesPerPixel`, genuinely 1 for a three-channel OME stored as three IFDs — and `size_c` is the channel count.

**No `Field.value` is emitted.** The per-file numbers stay in the files; what each field's description carries is the range or the set the batch was observed to hold. Channel names are emitted this way, because the OME schema defines `Channel/@Name` as the label of an acquisition channel — an antibody, a probe, a fluorophore — which is what makes an imaging dataset findable. `Image/@Name` is not, and neither are `Creator` or the file `UUID`: the schema says nothing about what they contain, and in practice they hold slide labels and operator notes. Where the format guarantees the semantics the vocabulary may be described; where it guarantees nothing it may not.

Only the `image` field carries `extract: {fileProperty: content}`, because its content *is* the file's content. mlcroissant reads `image/tiff` content as the decoded pixels, so the same extract on `size_c` would ask a consumer to cast an image to an integer. Every header field is sourced `{fileSet}` and nothing more.

The OME-XML is parsed with `xml.etree.ElementTree` and no new dependency. A document that declares a DTD or an entity is not parsed, and neither is one over 8 MiB. The file is still described, as a plain TIFF; the refusal is counted in the record-set description, which is the only place it can live: a described file has no entry in the scan report to carry a reason. A WARNING naming the file is logged as well, for an application that configures logging — croissant-baker owns no terminal and configures none itself.

A `BinaryOnly` document is a place-holder: the schema forbids it any content beyond a pointer to a companion `.companion.ome` file. The companion is named in the description and not read, and the place-holder contributes no header fields — not even the zero images it declares, which describes the stub rather than the image the file holds.

Not read: `Plane`, `Objective`, `TimeIncrement`, plate and well metadata, pyramid levels, and the vendor TIFF dialects (`.svs`, `.ndpi`, `.scn`, `.qptiff`).

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

SOFT is a metadata and data export format of the NCBI Gene Expression Omnibus.
The handler reads `.soft` family exports and curated DataSet exports such as
`GDS10.soft.gz`. Compression is handled by the shared input layer.

The following record sets describe each file:

| Record set suffix | One row per | Fields |
|-------------------|-------------|--------|
| `_series`, `_samples`, `_platforms`, `_datasets`, `_subsets` | distinct entity of that kind | declared attribute names, with the entity prefix removed |
| `_sample_characteristics` | sample | sample accession and submitter-defined characteristic keys |
| `_series_table`, `_sample_table`, `_platform_table`, `_dataset_table`, etc. | table row | declared table columns |

The `^DATABASE` block is shared GEO boilerplate and is not described. An entity
kind with no attributes produces no attribute record set. Repeated
`^DATASET` declarations for the same accession, separated by `^SUBSET` blocks,
count as one dataset. Unsupported entity kinds cause extraction to fail with a
reason; their metadata is not silently omitted.

Sample characteristics have a separate record set because their keys are
submitter-defined. This also keeps a `title:` characteristic distinct from the
standard `!Sample_title` attribute. Characteristics split on the **first colon**,
with or without whitespace: `tissue:liver` and `tissue: liver` both name `tissue`.
An empty value still names a key. Lines without a nonempty key and colon are
counted and represented by fallback fields such as `characteristics_ch1`.
Channel 1 and channel-less keys keep their bare names; channels 2 and above
always use `ch2_`, `ch3_`, etc. An unrelated sample cannot rename channel 1's
fields. Name collisions receive `__2`, `__3`, etc., preserving each field.

The characteristics record set also includes a scalar text **`geo_accession`**
field. It describes the accession of the enclosing `^SAMPLE = ACCESSION`
declaration, also commonly recorded in `!Sample_geo_accession`. A consumer can
therefore associate that sample with donor keys such as `dbgap_subject_id`
(GSE327347) or `patient id` (GSE335275). This works even when the redundant
`!Sample_geo_accession` attribute is absent. If a submitter uses `geo_accession`
as a characteristic key, that key is preserved and the added sample identifier
uses the next available `geo_accession__N` name; its description identifies its
source. Donor values and accession values are not copied into the manifest.

Tables group by entity kind, optional table title, and exact column signature.
Named series tables, including GSE2034's patient clinical parameters, are
included. Differently titled tables remain separate even with identical
columns. Titles and the deposit's `#COLUMN` descriptions are retained as schema
metadata. Row counts come from `!*_data_row_count` declarations; absent or
invalid counts are reported as unknown. Counts describe the declarations, not
an independent count of the table body.

### Schema metadata

**No data values are emitted.** Attribute and characteristic fields are
`sc:Text`, including numeric-looking donor identifiers. Table columns are typed
using PyArrow and the shared Croissant type mapping. The parser samples up to
500 rows and 1 MiB per table signature, then discards the sample after typing.
Empty trailing cells beyond the header width are ignored; duplicate column
names retain separate positions, types and field identifiers. Type inference
uses sampled rows, so later rows may contain types absent from that sample.
Memory grows with entity metadata and distinct table signatures, not the full
table bodies.

Fields carry `source: {fileObject: …}` and **no `extract`**. Croissant 1.1's
extraction grammar cannot address SOFT entity attributes, and `mlcroissant`
does not read SOFT data. These record sets describe the schema; consumers
reading sample–donor values must parse the source SOFT file within each
`^SAMPLE` block. The sample accession field supplies the schema needed for
that association, without inventing a donor relationship from field names.

Identifiers derive from the logical filename and dataset-relative path.
Compression does not change them. For example, `GSE1_family.soft.gz` produces
`GSE1_family_series` and `GSE1_family_sample_characteristics`. Cross-format
identifier collisions follow the shared generator rules. `encodingFormat` is
`text/x-geo-soft`, with the compression media type added by the input layer.

### Unsupported and incomplete inputs

`GSE*_series_matrix.txt` uses a different grammar and is not supported by this
handler. Neither are `GSM*.txt` or `GPL*.annot`. Renaming a series matrix to
`.soft` does not make it a valid SOFT entity export.

Files without valid `^ENTITY = ACCESSION` declarations, unknown entity kinds,
and mismatched or nested table markers fail extraction with a reason. A table
still open at end of file or undecodable UTF-8 produces a partial-parse warning
on every emitted record set. A complete attribute line at end of file is valid:
attribute blocks have no closing marker. A leading UTF-8 byte order mark is
accepted.

## Hidden files and directories

Files inside hidden directories (any path component starting with `.`) are always skipped, and do not appear in the coverage report. Use `--include` and `--exclude` glob patterns to further control which files are processed.
