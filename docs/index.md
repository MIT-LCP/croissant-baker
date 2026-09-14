<p align="center">
  <img src="assets/baker_logo.png" alt="croissant-baker" width="180">
</p>

# croissant-baker

**croissant-baker** automatically generates [Croissant](https://mlcommons.org/working-groups/data/croissant/) JSON-LD metadata for ML datasets. Point it at a dataset directory and it produces a standards-compliant `.jsonld` file — ready for submission to repositories like [PhysioNet](https://physionet.org/), [NeurIPS Datasets & Benchmarks](https://neurips.cc/Conferences/2026/EvaluationsDatasetsHosting), or any platform that benefits from standardized dataset metadata.

<p align="center">
  <a href="https://pypi.org/project/croissant-baker/"><img src="https://img.shields.io/pypi/v/croissant-baker?logo=pypi&logoColor=white" alt="PyPI"></a>&nbsp;&nbsp;
  <a href="https://github.com/MIT-LCP/croissant-baker"><img src="https://img.shields.io/badge/GitHub-Source_Code-blue?logo=github" alt="GitHub"></a>&nbsp;&nbsp;
  <a href="https://arxiv.org/abs/2605.15079"><img src="https://img.shields.io/badge/arXiv-2605.15079-b31b1b.svg" alt="arXiv"></a>
</p>

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
  --input ./my-dataset \
  --creator "Jane Smith,jane@example.com" \
  --description "My dataset" \
  --license "CC-BY-4.0"
```

See the [Getting Started](getting-started.md) guide for a full walkthrough.

## Features

- **Automatic type inference** — reads CSV/TSV, Parquet, spreadsheets, FHIR NDJSON, JSON/JSONL, WFDB, images, DICOM and NIfTI; maps column and field types to the Croissant schema
- **Metadata overrides** — sensible defaults for every field; use flags to set name, description, license, creators, citation, and more
- **RAI metadata** — document responsible AI fields (data collection, biases, limitations, sensitive information) via CLI flags or a YAML config
- **Validation built-in** — validates against the Croissant spec via `mlcroissant` before writing; use `--no-validate` to skip
- **Dry-run mode** — preview which files would be processed with `--dry-run` before committing
- **Include / exclude filters** — glob patterns to include or exclude files by name

## Links

- [:fontawesome-brands-github: Source code on GitHub](https://github.com/MIT-LCP/croissant-baker)
- [:fontawesome-brands-python: Package on PyPI](https://pypi.org/project/croissant-baker/)
- [:material-file-document-outline: arXiv preprint (2605.15079)](https://arxiv.org/abs/2605.15079)

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
