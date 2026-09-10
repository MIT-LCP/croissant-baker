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

Standard images (`.png`, `.jpg`, `.gif`, `.bmp`, `.webp`, `.ico`) are read with Pillow. TIFF and BigTIFF files (`.tif`, `.tiff`, `.btf`) are read with `tifffile`, which supports scientific TIFF layouts such as multiband rasters and reads the UTF-8 OME metadata used by microscopy writers. Both TIFF variants use `image/tiff`.

Ordinary images belong to the `image-files` FileSet and `images` RecordSet. Their descriptions summarize dimensions, band counts and formats. Include patterns cover both root-level files (`*.tif`, for example) and nested files (`**/*.tif`), including readers that require a directory for `**/`. Pixel arrays are not decoded during metadata extraction.

!!! note
    Wrapped TIFFs require additional decompression because TIFF readers seek within the file. A backward seek on a compressed stream can restart decompression from the beginning, so reading a `.tif.gz`, `.tif.bz2` or `.tif.xz` can decompress the pixel payload even though no pixel array is decoded. Uncompressed TIFFs can seek directly to their headers.

### OME-TIFF

An OME-TIFF carries OME-XML in its ImageDescription tag. The handler checks the content identified by `tifffile` and requires a versioned OME namespace; the filename's `.ome.` infix does not determine the format. Files with a readable OME header have their own collection:

| Node | Content |
|------|---------|
| `cr:FileSet` `ome-image-files` | the OME files, listed individually |
| `cr:RecordSet` `ome_images` | one row per OME file, with the fields below |

The header fields are `ome_version`, `ome_image_count`, `size_c`, `size_z`, `size_t`, `dimension_order`, `pixel_type`, `physical_size_x`, `physical_size_y`, `physical_size_x_unit`, `physical_size_y_unit`, and the array field `channel_names`. Fields with no observed values are omitted. Invalid dimension counts and physical sizes are omitted independently; physical sizes must be finite and positive.

**Physical measurements retain their original unit metadata.** X and Y have separate unit fields. An absent unit remains `None` in the parsed header, including for older OME schemas; schema defaults are not applied. Explicit unit strings are preserved and measurements are not converted. Batch ranges are grouped by unit, so `0.001 mm` and `0.2–0.4 µm` remain separate summaries. Measurements without a unit are reported separately as `unit unspecified`. A unit field is omitted when no file in the batch declares a nonempty unit for that axis.

**The manifest separates OME files from ordinary images.** The ordinary-image FileSet retains extension globs and excludes the OME paths using `cr:excludes`. This keeps its size proportional to the number of OME exceptions rather than the number of ordinary TIFF tiles. The generator resolves both includes and excludes to stored paths, including compression wrappers and linked duplicates. Collection counts and summaries describe this partition.

!!! note "Reader support for exclusions"
    `mlcroissant` 1.1.0 serializes and validates `cr:excludes`, but its record reader does not apply those exclusions ([upstream issue #772](https://github.com/mlcommons/croissant/issues/772)). In a mixed dataset, `records("images")` can therefore return OME files as well as ordinary images, exceeding the count stated in the description. The same OME files also belong to `ome-image-files`. Consumers must honor includes minus excludes to obtain the intended partition. A regression test reads actual records to track this limitation; successful metadata validation alone does not establish correct record reading.

**Rows describe files.** One OME document may declare several `<Image>` elements, and an image may span several TIFF files. The handler does not group these files or resolve `TiffData` mappings. The Pixels fields describe the first `<Image>` in each file's XML document, which need not correspond to all pixels stored in that particular file. `ome_image_count` counts the document's Image elements. TIFF `SamplesPerPixel` supplies `num_bands`; it can be 1 for an OME image whose three channels occupy separate TIFF pages.

**Header fields are descriptive.** Their descriptions contain observed ranges or sets, and no `Field.value` is emitted. Only `image` has `extract: {fileProperty: content}`. Header fields retain their FileSet source without an extraction instruction, because reading the file content does not select an OME header attribute. `mlcroissant` 1.1.0 cannot extract these fields, so reading the complete `ome_images` RecordSet with `records("ome_images")` fails even when the metadata validates. Channel labels come from `Channel/@Name`; the list omits unnamed channels and is not a positional channel mapping. `Image/@Name`, `Creator`, and file UUIDs are not copied into field descriptions.

For descriptions identified as OME-XML, the parser refuses DTD/entity declarations, malformed XML and descriptions larger than 8 MiB. Declaration-like text inside comments, processing instructions or CDATA is allowed. Such text does not declare a DTD or expand entities. Refused files remain in the ordinary `images` collection with their TIFF properties. The refusal count and reasons appear in that RecordSet's description, and a warning naming the file is available to applications that configure logging. The size limit bounds XML parsing; `tifffile` may already have decoded the TIFF description tag before the check.

A `BinaryOnly` document is a placeholder pointing to a companion metadata file. It remains in `ome_images`, names the companion in its description, and contributes no header measurements or image count. The companion is not opened.

Not read: `Plane`, `Objective`, `TimeIncrement`, plate and well metadata, pyramid levels, and vendor TIFF extensions such as `.svs`, `.ndpi`, `.scn`, and `.qptiff`.

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

## VCF and gVCF

VCF (`.vcf`) is the variant call format. The handler reads the header and stops
at the first record: nothing below the `#CHROM` line is ever parsed.

A VCF is claimed on its opening `##fileformat=VCF` declaration rather than on
its extension, because `.vcf` is also the vCard extension. A vCard is therefore
reported as a file no handler claimed, and a callset saved under another name is
still described.

Each file produces one record set whose fields are the columns the `#CHROM` line
declares, in that order: `CHROM` (`sc:Text`), `POS` (`cr:Int64`), `ID`
(`sc:Text`), `REF` (`sc:Text`), `ALT` (`sc:Text`, repeated), `QUAL`
(`cr:Float64`), `FILTER` (`sc:Text`, repeated) and `INFO`. A file carrying
genotypes adds `FORMAT` and a single repeated `samples` field standing for the
genotype columns.

`INFO` and `FORMAT` are per-record key-value bags rather than columns of their
own, so each declared key becomes a sub-field of the column that carries it.
The declared `Type` gives the Croissant type (`Integer` to `cr:Int64`, `Float`
to `cr:Float64`, `Flag` to `sc:Boolean`, `String` and `Character` to `sc:Text`),
and any `Number` other than `0` or `1` marks the sub-field repeated, which
covers `A`, `R`, `G`, `.` and literal counts above one. The declared
`Description` becomes the sub-field description; it is a header byte, so
traceability holds.

The declared `##fileformat`, `##reference`, the number of `##contig`
declarations and the sample count are stated in the record set description
rather than in JSON-LD keys no Croissant vocabulary defines. A gVCF is the same
handler and the same shape: `##GVCFBlock` lines or a `NON_REF` alternate allele
are recorded, and the description says so.

Fields carry `source: {fileObject: …}` and **no `extract`**, for the reason
given under GEO SOFT: `mlcroissant` does not read VCF, so a column reference
would be a promise nothing can keep. `encodingFormat` is `text/x-vcf`, with the
compression media type added by the input layer.

A header with no `#CHROM` line declares no columns, and the file is reported
with that reason rather than described.

### Sample identifiers

Sample column names are a manifest of the cohort. They are withheld by default:
the record set states how many samples the file carries, not what they are
called. `--genomic-sample-ids` emits them, mirroring the opt-in shape of
`--count-csv-rows`.

## BCF

BCF (`.bcf`) is the binary form of a VCF: the same header text, followed by
records packed into a binary encoding. A `.bcf` is already a compressed
container, and the input layer does not treat it as one: it is a format, not a
transport wrapper, so the handler decompresses it itself, exactly as the BAM
handler does. It reads the magic, the declared header length and the header
text, then stops. No record is decoded.

A BCF is claimed on that magic, in either of the two forms the pipeline can hand
over: a stream whose payload starts with `BCF\2`, or one that already starts
with it because a second wrapper was taken off on the way in. Both minor
versions of BCF 2 are read, because they differ in how records are encoded and
not in the header. BCF1, samtools' own first-generation encoding, declares no
VCF header text at all and is reported rather than half-described.

The header is then the VCF handler's, so a callset describes the same way in
either container: one record set per file, whose fields are the columns the
`#CHROM` line declares, with `INFO` and `FORMAT` carrying one sub-field per
declared key, and the `##fileformat`, `##reference`, contig count and sample
count stated in the record set description. `encodingFormat` is
`application/x-bcf`, with the compression media type added by the input layer
when the file arrives under a further wrapper.

Sample column names are withheld under the same `--genomic-sample-ids` opt-in
that governs a plain VCF. Index files (`.csi`) are reported as unsupported;
nothing claims them.

## BAM

BAM (`.bam`) is the compressed binary form of a SAM alignment file. A `.bam` is
already a compressed container, and the input layer does not treat it as one: it
is a format, not a transport wrapper, so the handler decompresses it itself. It
reads the magic, the SAM text header and the reference count, then stops. No
alignment record is read.

A BAM is claimed on that magic, in either of the two forms the pipeline can hand
over: a stream whose payload starts with `BAM\1`, or one that already starts with
it because a second wrapper was taken off on the way in.

From the text header: `@HD` gives the SAM version and sort order, `@SQ` the
number of reference sequences and, from the first, the assembly name; `@RG` the
number of read groups with their sequencing platforms and centres; `@PG` the
program chain in declaration order. The binary `n_ref` that follows the text is
recorded alongside the `@SQ` count.

**No record set is emitted.** Aligned reads are records of a genome, not of a
dataset schema, so a BAM is described as a file: the properties above are stated
in the `description` of its `cr:FileObject`. That description is the one thing
this handler produces; `encodingFormat` is `application/x-bam`, with the
compression media type added by the input layer when the file arrives under one.

`@RG SM` names the sample a read group came from, and the tags together are a
cohort manifest, so they are withheld under the same `--genomic-sample-ids`
opt-in as the VCF sample columns. Index files (`.bai`, `.csi`, `.tbi`) are
reported as unsupported; nothing claims them.

## CRAM

CRAM (`.cram`) stores the same alignment a BAM does, encoded against the
reference the reads were placed on rather than storing their bases. That
reference is not needed to describe the file: the SAM text header sits in the
first block of the first container, and the header is all that is read, so a
CRAM whose reference is a URL nobody can reach is still described in full.

A CRAM is claimed on its magic, the four bytes `CRAM` a file definition opens
with. Reaching the header block after it means walking the first container
header field by field: those fields are written in CRAM's two variable-width
integer encodings, ITF8 and LTF8, so the block behind them cannot be seeked to.
The block is then decoded from raw, gzip, bzip2 or LZMA, whichever it declares.
The text it holds is read exactly as a BAM's is: `@HD` for the SAM version and
sort order, `@SQ` for the reference sequences and, from the first, the assembly
name, `@RG` for the read groups with their platforms and centres, and `@PG` for
the program chain in declaration order. The CRAM version itself is recorded
alongside them.

Three things are refused with a reason rather than guessed at. Major versions
other than 2 and 3: CRAM 1 is obsolete and CRAM 4 changes the integer encodings
a container header is written in, so neither can be walked with this layout. A
file header block coded with rANS, CRAM's own entropy coder, which has no
decoder in the standard library. And a block or header text whose declared size
is larger than any real header, which is refused before a byte behind it is
read. CRC32 values are read past rather than checked: what is described is the
header text, and a mismatch is a decoder's corruption report, not metadata.

**No record set is emitted**, for the reason a BAM emits none: aligned reads are
records of a genome, not of a dataset schema. The properties above are stated in
the `description` of the file's `cr:FileObject`, and `encodingFormat` is
`application/x-cram`, with the compression media type added by the input layer
when the file arrives under one. `@RG SM` sample tags are withheld under the
same `--genomic-sample-ids` opt-in as the BAM tags and the VCF sample columns.
Index files (`.crai`) are reported as unsupported; nothing claims them.

## SAM

SAM (`.sam`) is the text form of the same alignment file, and it opens with the
same header. `@HD` gives the SAM version and sort order, `@SQ` the number of
reference sequences and, from the first, the assembly name; `@RG` the number of
read groups with their sequencing platforms and centres; `@PG` the program chain
in declaration order. Nothing in front of the header says how long it is, so the
read is bounded by the stop at the first line that does not start with `@`: no
alignment record is read, whatever the size of the file.

A SAM is claimed on its extension **and** its first header line, and needs both.
A FASTQ opens with `@` as well, so the leading character alone would describe one
as an alignment it is not; and the header shape alone is not what makes a file a
SAM. A `.sam` carrying only alignment records is therefore reported as unclaimed,
which is the honest outcome: with no header it declares no sort order, no
assembly and no read group.

**No record set is emitted**, for the reason BAM emits none: the properties above
are stated in the `description` of the file's `cr:FileObject`. `encodingFormat`
is `text/x-sam`, with the compression media type added by the input layer when
the file arrives under one. `@RG SM` is withheld under the same
`--genomic-sample-ids` opt-in.
## FASTQ

FASTQ (`.fastq`, `.fq`) is the same four lines repeated until the run is
exhausted: a read name, the bases, a `+` separator, and one quality character
per base. The handler reads the first record and stops. Nothing behind it is
opened, so a run of a hundred million reads costs the same read as a run of
one.

The claim needs both the extension and the structure, because neither holds on
its own. `@` opens a record's name line, but it also opens every line of a SAM
header, so the first byte cannot decide; the extension cannot decide either,
because it would claim any text a user happened to name `.fq`. Together they
are the record's own shape: a name line, a sequence, and a separator on the
third line. A `.fastq` whose third line is not `+` is left to the other
handlers and reported with a reason.

What is extracted is the length of the first read. A record whose quality line
does not match its sequence in length, or that has no `+` on its third line, is
a record this handler cannot describe truthfully, and the file is reported with
that reason rather than described. Multi-line FASTQ, where a read is wrapped
across several lines, is deliberately unsupported for the same reason.

**Read names are never reported.** An Illumina read name spells out the
instrument, run, flowcell and lane the read came from, and none of that is
structure. It reaches neither the metadata nor the description, and there is no
flag to turn it on. Records are not counted either: counting them means reading
the whole file, which is the one thing this handler exists not to do.

**No record set is emitted.** Sequencing reads are records of a run, not of a
dataset schema, so a FASTQ is described as a file: the read length is stated in
the `description` of its `cr:FileObject`. `encodingFormat` is `text/x-fastq`,
with the compression media type added by the input layer when the file arrives
under one, so `reads.fastq.gz` is described exactly as `reads.fastq` is.
## FASTA

FASTA (`.fa`, `.fasta`, `.fna`) is a description line followed by sequence,
repeated. The handler reads the first description line and stops there. No
sequence line is ever read: a reference genome is gigabytes of bases and none of
them is metadata.

A FASTA is claimed on its extension **and** on its first byte, and neither half
would do alone. `>` is a single character that a quoted email, a shell
transcript and a diff all begin with, so it is too little to own a file on. The
extension alone would claim any text a user happened to name `.fa`, and past
that first byte the format has no other marker to fall back on: a FASTA is
letters, which is what an unrelated text file is too. A `.fa` that does not open
with `>` is therefore reported as a file no handler claimed, and an empty file
or a bare `>` line is reported with that as its reason rather than described.

What is reported is the format and the encoding. Deliberately not reported:

- **Record names.** A description line names the record, and for a per-sample
  assembly that name is the sample, so it is withheld the way `@RG SM` and the
  VCF sample columns are. Unlike those, it has no opt-in: one line is read, and
  which record it names is not a structural fact about the dataset.
- **The comment text** following the name on the same line, for the same reason.
- **The number of records**, and the sequence lengths. Counting either means
  reading the whole file, which is what header-only reading exists to avoid.

**No record set is emitted.** Bases are records of a genome, not of a dataset
schema, so a FASTA is described as a file: the statement above is carried in the
`description` of its `cr:FileObject`. `encodingFormat` is `text/x-fasta`, with
the compression media type added by the input layer when the file arrives under
one. FASTA has no IANA registration, so the `x-` form follows `text/x-vcf`.

Index and dictionary files (`.fai`, `.dict`, `.gzi`) are reported as unsupported;
nothing claims them.
## Hidden files and directories

Files inside hidden directories (any path component starting with `.`) are always skipped, and do not appear in the coverage report. Use `--include` and `--exclude` glob patterns to further control which files are processed.
