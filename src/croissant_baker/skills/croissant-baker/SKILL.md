---
name: croissant-baker
description: Generate and validate Croissant 1.1 JSON-LD dataset metadata with the croissant-baker CLI, which walks a directory and infers FileObjects and RecordSets from CSV, TSV, spreadsheet, Parquet, FHIR, JSON, JSONL, WFDB, DICOM, NIfTI, images (OME-TIFF, BigTIFF), GEO SOFT, HDF5 (AnnData .h5ad), VCF, BCF, BAM, CRAM, SAM, FASTQ and FASTA files, and refuses to guess the semantic fields. Use this skill whenever the user wants dataset metadata, an mlcroissant or Croissant file, a NeurIPS Datasets and Benchmarks submission, a PhysioNet or other controlled-access clinical or biomedical release, RAI (Responsible AI) dataset documentation, or an answer about FileObject, RecordSet, distribution or conformsTo entries. Use it also when they only say "describe this data directory", "document these files", "make my dataset machine-readable" or "generate a data card", or call the croissant-baker MCP tools, even if nobody says the word Croissant. Not for documenting source code or APIs; only for dataset directories.
license: MIT
compatibility: Requires Python 3.10 or newer with croissant-baker installed (`pip install croissant-baker`, or `uv add croissant-baker`). Everything runs locally against files on disk; the tool makes no network request and uploads nothing. The MCP tools need the optional `mcp` extra (`pip install 'croissant-baker[mcp]'`).
metadata:
  author: MIT-LCP
  version: "1.0"
allowed-tools: Bash(croissant-baker:*) Bash(uv:*) Read
---

# Baking Croissant metadata for a dataset

`croissant-baker` reads a dataset directory and writes one Croissant 1.1
JSON-LD file describing it. It is deterministic and rule-based: every value it
emits is traceable either to bytes it read or to a flag someone typed.

## The invariant you must respect

The tool separates two layers, and so must you.

- **Structural** (paths, sizes, sha256, RecordSets, field names, field types):
  inferred from the files. Never hand-write these. If a field type looks wrong,
  fix the source file or the flags and re-bake; do not edit the JSON-LD.
- **Semantic** (`--name`, `--description`, `--license`, `--creator`,
  `--citation`, `--url`, `--publisher`, every `--rai-*` value): never inferred,
  by design. The tool will not guess them and neither should you.

So: **do not invent semantic values.** Ask the user for them. If they point you
at a README, a paper, a DUA or a landing page, you may propose values quoted
from that source, but show the user the exact flags you intend to run and get
explicit confirmation before baking. A plausible-looking wrong license or
citation is worse than a missing one, because it survives into a published
record that other people cite.

Omitting `--name` is the one safe default: it falls back to the directory name.

## Workflow

Treat this as a loop, not a single command.

### 1. Dry run first

```bash
croissant-baker --input /path/to/dataset --dry-run
```

This reads at most a small header per file, enough to pick a handler. It
prints what would be described and, under a second heading, every file that
would not be, each with a reason. Read both
lists before doing anything else. Add `--verbose` for the per-file reasons on a
real bake, or `--report FILE` for the same thing as JSON.

Narrow the scan with repeatable globs if the directory holds more than the
dataset:

```bash
croissant-baker --input ./data --dry-run --include '*.parquet' --exclude '*.tmp'
```

### 2. Resolve or accept every refusal

Each unclaimed file carries one of a fixed set of reasons. The terminal prints
the label; `report.json` and the MCP `by_reason` map carry the key. Decide, do
not skip:

| Key | Label | What it means | What to do |
|-----|-------|---------------|------------|
| `no_handler` | no handler | Nothing recognised the format | Expected for README, LICENSE, checksums, HTML and genomic indexes (`.bai`, `.crai`, `.csi`, `.fai`). If it is real data, say so plainly: the format is not supported. |
| `archive` | archive, not opened | A `.zip`, `.tar` or `.tgz` | Ask the user to extract it, then re-run on the extracted tree. |
| `unsupported_input` | handler needs an uncompressed file on disk | A path-only handler (WFDB) was offered a compressed file | Decompress that file, or accept it as a reported-only FileObject. |
| `claim_failed` | unreadable while selecting a handler | Usually a corrupt compression wrapper | Check the file; it is probably truncated. |
| `extract_failed` | extraction failed | The handler took it and failed to read it | Report the detail text to the user verbatim. This is a bug or a malformed file. |
| `build_failed` | could not be assembled | The handler read it and failed to build its nodes | Report the detail text to the user verbatim. This is a bug or a malformed file. |
| `duplicate_by_name` | duplicate by naming convention | A plain and compressed twin, matched by name | Nothing to fix. The secondary is linked with `sameAs`. |
| `probable_duplicate` | probable duplicate of another file | Two files with the same decompressed start | Nothing to fix. The secondary is linked with `sameAs`. |
| `partition_schema_conflict` | partition schema conflict | Parquet shards of one table disagree on schema | The source data is inconsistent. Surface it; do not paper over it. |

Never close a gap by hand-writing metadata for a refused file. Reporting a file
with a reason is the designed behaviour.

### 3. Collect the semantic fields, then bake

```bash
croissant-baker \
  --input tests/data/input/mimiciv_demo/physionet.org/files/mimic-iv-demo/ \
  --name "MIMIC-IV Clinical Database Demo" \
  --description "Demo subset of MIMIC-IV containing 100 de-identified patients from Beth Israel Deaconess Medical Center" \
  --creator "Alistair Johnson,aewj@mit.edu,https://physionet.org/" \
  --creator "Tom Pollard,tpollard@mit.edu,https://physionet.org/" \
  --url "https://physionet.org/content/mimic-iv-demo/2.2/" \
  --license "https://opendatacommons.org/licenses/odbl/1-0/" \
  --date-published "2023-01-06" \
  --rai-data-biases "Single-site cohort from a US academic medical centre" \
  --rai-data-limitations "Demo subset limited to 100 patients" \
  --output mimic-iv-demo-croissant.jsonld
```

`--creator` is `Name[,Email[,URL]]` and repeats, once per creator. Prefer a
license URL over an SPDX string. Give `--url` and `--date-published`: the
validator warns for both, and the run ends by naming the fields that "are
required by the Croissant spec but were not provided".

### 4. Validate, and read what came back

```bash
croissant-baker validate mimic-iv-demo-croissant.jsonld
```

Valid means "constructs under `mlcroissant`". A bake already validates before
writing and refuses to write an invalid file, so this matters most for a
document that was edited afterwards or produced elsewhere.

### 5. Check the shape of the output

The written document is a `sc:Dataset` with `conformsTo`
`http://mlcommons.org/croissant/1.1` (plus the RAI 1.0 URI when RAI fields are
present), a `distribution` array of `cr:FileObject` nodes (`@id` `file_0`,
`file_1`, ..., each with `name`, `contentUrl`, `contentSize`, `encodingFormat`,
`sha256`) and a `recordSet` array of `cr:RecordSet` nodes, each holding
`cr:Field` entries with a `dataType` and a `source` pointing back at a
`fileObject` and a column.

Confirm the counts match what the dry run predicted. A RecordSet is a logical
record and may span many files, so RecordSets and FileObjects need not be equal
in number.

### 6. Iterate

Output is deterministic: the same input and flags give the same bytes. So when
a type or a schema looks wrong, fix the **source file** (or the flags), re-run
the same command, and diff. That loop is the intended way to work, and it is
why re-baking is cheap and safe.

## Governance and controlled access

For clinical or otherwise restricted data, do not silently produce a
document that reads as open:

- Put the real license URL in `--license`. The DUA landing page is usually the
  right answer for a credentialed PhysioNet-style release.
- `--usage-info` takes a URI for the consent or usage policy (any RFC 3986
  scheme, including a DUO term such as
  `http://purl.obolibrary.org/obo/DUO_0000042`).
- `--sd-license` licenses the metadata description itself, which is often more
  open than the data. Keep them distinct.
- Document the restriction in prose too, via
  `--rai-personal-sensitive-information` and `--rai-data-limitations`.
- `--same-as` records an equivalent landing page or DOI.


## RAI metadata

Two routes, and they cannot be combined in one command:

- **Flags** for a handful of dataset-level fields:
  `--rai-data-biases`, `--rai-data-limitations`, `--rai-data-use-cases`,
  `--rai-data-social-impact`, `--rai-personal-sensitive-information`,
  `--rai-data-collection` and friends. Several repeat.
- **`--rai-config rai.yaml`** for anything richer: provenance activities,
  lineage, agents, platforms.

Use flags for two or three fields; switch to the YAML as soon as the user has
lineage or activities to record. When you need the YAML, read
`assets/rai-template.yaml` and copy it as the starting point; it carries the
exact key names the loader accepts and comments saying what belongs in each.
Over MCP, read the same file as the resource `croissant-baker://rai-template`.
Delete the keys the user cannot answer rather than filling them with plausible
text. Before baking, check for any remaining `REPLACE`: the loader accepts it
as ordinary text, so a forgotten placeholder ships in the published file.

To add RAI to a file that already exists:

```bash
croissant-baker rai-apply dataset-croissant.jsonld --rai-config rai.yaml
```

## Machine-readable results

```bash
croissant-baker --input ./data --output out.jsonld --report report.json
```

`report.json` holds `total`, `described`, `linked`, `referenced`,
`undescribed`, a `by_reason` tally, and a `files` array giving every discovered
file its `outcome`, `reason` and human-readable `detail`. Parse this rather
than scraping the terminal output.

## MCP mode

When a `croissant-baker` MCP server is connected, use its tools instead of the
shell; they run the same pipeline. The loop is unchanged.

1. `dry_run(input_dir, include?, exclude?)` returns `total`, `would_process`,
   `unclaimed`, a `by_reason` map and a `files` array listing each unclaimed
   file with its reason; claimed files are counted, not listed. Read
   `by_reason` first: it is the fastest read on whether the directory is ready.
   `dry_run` reads at most a small header per file and describes nothing, so
   it reports `would_process`, never `described`.
2. `bake(input_dir, output, name, description, license, creators, url?,
   citation?, date_published?, detect_references?, include?, exclude?)` writes
   the file and returns `output` and `report_path`, both absolute, with the
   completed bake's counters (`total`, `described`, `linked`, `referenced`,
   `undescribed`, `by_reason`), so `described` is meaningful there. The
   per-file list is not in the result; it is in the JSON file at
   `report_path`, next to the output. Read it only when a count needs
   explaining.
3. `validate(path)` returns `{"valid": true}`, or `{"valid": false, "error":
   ...}` when `mlcroissant` read the file and refused it. A path that is not a
   readable local file, including any URL, is a tool error instead: fix the
   path, not the document.

Pass absolute paths. A relative one resolves against the directory the server
was started in, which you may not know. `bake` replaces an existing output and
report, so the client may ask before running it; `dry_run` and `validate` only
read.

The `creators` list uses the same `"Name,email,url"` strings as `--creator`,
and must name at least one creator. The server is stdio-only and local: no HTTP
listener, no outbound request. Its surface is deliberately narrow, so anything
beyond these three tools (extra flags, RAI config) needs the CLI. Besides this
skill as `croissant-baker://skill`, it serves the RAI template as
`croissant-baker://rai-template`.

## Gotchas

- **Archives are reported, never opened.** `.zip`, `.tar` and `.tgz` become one
  FileObject with a reason. Extract them first if their contents matter.
- **Compressed inputs are described as their inner format.** `cells.parquet.gz`
  is described exactly as `cells.parquet` is. The exception is WFDB: a record
  spans several files located by path, so a compressed `.hea` is reported
  rather than described.
- **`--count-csv-rows` costs a full pass over every CSV.** Row counts are
  omitted by default for that reason. Do not turn it on for a large dataset
  unless the user asked for exact counts.
- **Genomic sample identifiers are withheld by default.** VCF and BCF sample
  columns and BAM, CRAM and SAM read-group `SM` tags are counted, not listed.
  `--genomic-sample-ids` emits them, and for a controlled release they are a
  manifest of the cohort, so pass it only when the user asks for it.
- **`datePublished` and `url` warnings are expected when you omit them.** The
  run still succeeds and the file is still valid. The closing warning that they
  "are required by the Croissant spec but were not provided" is a prompt to go
  back to the user, not an error to suppress.
- **Nothing is uploaded and nothing is fetched.** If a task needs a remote
  landing page or a DOI resolved, that is your job before the bake, not the
  tool's during it.
- **Do not hand-edit the generated JSON-LD.** Re-run instead. The output is
  deterministic, so re-running after fixing the input is always the shorter
  path, and hand edits are lost on the next bake.

Run `croissant-baker --help` for the full flag list, and
`croissant-baker <command> --help` for `validate` and `rai-apply`. Treat that
output as the source of truth: flags differ between versions, so never pass one
you have not seen there.
