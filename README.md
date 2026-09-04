<p align="center">
  <img src="https://raw.githubusercontent.com/MIT-LCP/croissant-baker/main/docs/assets/baker_logo.png" alt="croissant-baker" width="200">
</p>

<h1 align="center">Croissant Baker</h1>

<p align="center">
  <a href="https://github.com/MIT-LCP/croissant-baker/actions/workflows/test.yaml"><img src="https://github.com/MIT-LCP/croissant-baker/actions/workflows/test.yaml/badge.svg" alt="CI"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+"></a>
  <a href="https://docs.astral.sh/uv/"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json" alt="uv"></a>
  <a href="https://github.com/MIT-LCP/croissant-baker/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
  <a href="https://pypi.org/project/croissant-baker/"><img src="https://img.shields.io/pypi/v/croissant-baker?logo=pypi" alt="PyPI"></a>
  <a href="https://arxiv.org/abs/2605.15079"><img src="https://img.shields.io/badge/arXiv-2605.15079-b31b1b.svg" alt="arXiv"></a>
  <a href="https://github.com/MIT-LCP/croissant-baker/pulls"><img src="https://img.shields.io/badge/PRs-welcome-brightgreen.svg" alt="PRs Welcome"></a>
</p>

<p align="center">
  Automatically generate <a href="https://mlcommons.org/working-groups/data/croissant/">Croissant</a> JSON-LD metadata for ML datasets — e.g. for <a href="https://physionet.org/">PhysioNet</a>, <a href="https://neurips.cc/Conferences/2026/EvaluationsDatasetsHosting">NeurIPS Datasets &amp; Benchmarks</a> submissions, or any platform that benefits from standardized dataset metadata.
</p>

<p align="center">
  <a href="https://lcp.mit.edu/croissant-baker/"><strong>📖 Documentation</strong></a>
</p>

---

## Installation

```bash
pip install croissant-baker
```

or with [uv](https://docs.astral.sh/uv/):

```bash
uv add croissant-baker
```

## Quick start

```bash
croissant-baker \
  --input /path/to/dataset \
  --creator "Your Name,you@example.com" \
  --description "My ML dataset" \
  --license "CC-BY-4.0" \
  --output my-dataset-croissant.jsonld
```

Or try with the bundled MIMIC-IV Demo test data:

```bash
git clone https://github.com/MIT-LCP/croissant-baker.git && cd croissant-baker
uv sync --group dev
croissant-baker \
  --input tests/data/input/mimiciv_demo/physionet.org/files/mimic-iv-demo/ \
  --creator "Alistair Johnson,aewj@mit.edu,https://physionet.org/" \
  --creator "Tom Pollard,tpollard@mit.edu,https://physionet.org/" \
  --name "MIMIC-IV Clinical Database Demo" \
  --description "Demo subset of MIMIC-IV containing 100 de-identified patients from Beth Israel Deaconess Medical Center" \
  --url "https://physionet.org/content/mimic-iv-demo/2.2/" \
  --license "https://opendatacommons.org/licenses/odbl/1-0/" \
  --rai-data-biases "Single-site cohort from a US academic medical centre" \
  --rai-data-limitations "Demo subset limited to 100 patients" \
  --output mimic-iv-demo-croissant.jsonld
croissant-baker validate mimic-iv-demo-croissant.jsonld
```

## Supported formats

| Format | Extensions | Notes |
|--------|------------|-------|
| CSV / TSV | `.csv`, `.tsv` | Streaming with automatic type inference |
| Parquet | `.parquet` | Partitioned datasets supported |
| FHIR | `.ndjson`, `.json` (Bundle) | NDJSON bulk export and JSON Bundle |
| JSON / JSONL | `.json`, `.jsonl` | Arrays, single objects, and JSON Lines |
| WFDB | `.hea` + `.dat` / `.atr` | PhysioNet waveform data |
| Images | `.png`, `.jpg`, `.tiff`, `.bmp`, `.gif`, `.webp` | Dimensions and format via Pillow |
| DICOM | `.dcm`, `.dicom` | Modality, geometry, study/series UIDs via pydicom (header only) |
| NIfTI | `.nii` | Spatial dims, voxel spacing, TR for fMRI via nibabel (header only) |
| GEO SOFT | `.soft` | NCBI GEO family exports: attribute names, sample characteristic keys, data table columns |

Any of these may arrive wrapped in `.gz`, `.bz2` or `.xz` — compression is
resolved before the format is read, so `cells.parquet.gz` is described exactly
as `cells.parquet` is. WFDB is the exception: a record spans several files
located by path, so a compressed `.hea` is reported rather than described.
`.zip`, `.tar` and `.tgz` are reported as archives, not opened. Files nothing
describes are reported with a reason rather than skipped in silence.

## Key features

- **Automatic type inference** for all supported formats
- **RAI metadata** via `--rai-*` CLI flags or `--rai-config rai.yaml`
- **Validation** against the Croissant spec via `mlcroissant`
- **Dry-run mode**, include/exclude glob filters, multiple creators

See the [documentation](https://lcp.mit.edu/croissant-baker/) for full CLI reference, examples, and RAI configuration.

## Contributing

See [CONTRIBUTING.md](https://raw.githubusercontent.com/MIT-LCP/croissant-baker/main/CONTRIBUTING.md) for guidelines and [DEVELOPMENT.md](https://raw.githubusercontent.com/MIT-LCP/croissant-baker/main/DEVELOPMENT.md) for setup, testing, releases, and how to add new file handlers.

## Citation

If you use Croissant Baker in your research, please cite our [arXiv preprint](https://arxiv.org/abs/2605.15079):

```bibtex
@misc{attrach2026croissantbakermetadatageneration,
      title={Croissant Baker: Metadata Generation for Discoverable, Governable, and Reusable ML Datasets},
      author={Rafi Al Attrach and Rajna Fani and Sebastian Lobentanzer and Joan Giner-Miguelez and Debanshu Das and Varuni H. K. and Nobin Sarwar and Rajat Ghosh and Anwai Archit and Surbhi Motghare and Christina Conrad Parry and Luis Oala and Lara Grosso and Joaquin Vanschoren and Steffen Vogler and Sujata Goswami and Eric S. Rosenthal and Marzyeh Ghassemi and Matthew McDermott and Tom Pollard},
      year={2026},
      eprint={2605.15079},
      archivePrefix={arXiv},
      primaryClass={cs.LG},
      url={https://arxiv.org/abs/2605.15079},
}
```

## License

MIT License - see [LICENSE](https://raw.githubusercontent.com/MIT-LCP/croissant-baker/main/LICENSE) file.
