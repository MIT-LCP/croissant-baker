# Development Guide

We recommend using test-driven development as much as possible.

## Setup

This project uses [uv](https://docs.astral.sh/uv/) for environment and dependency management.

```bash
git clone https://github.com/MIT-LCP/croissant-baker.git
cd croissant-baker
uv sync --group dev
uv run pre-commit install
```

## Running

After `uv sync`, you can either activate the virtualenv or prefix commands with `uv run`:

```bash
# Option 1: activate the venv (once per terminal session)
source .venv/bin/activate
croissant-baker --help
croissant-baker --input ./my-dataset --creator "Jane Doe"

# Option 2: use uv run (no activation needed)
uv run croissant-baker --help
uv run croissant-baker --input ./my-dataset --creator "Jane Doe"
```

### Local MCP server

`croissant-baker mcp` serves three tools to a local agent over stdio: `dry_run`
(what a bake would describe and refuse, with reasons), `bake` (generate,
validate and write the metadata) and `validate` (construct an existing file
under `mlcroissant`). stdio is the only transport; there is no HTTP listener and
no outbound request, so the no-upload guarantee is unchanged. The SDK is an
optional dependency group:

```bash
uv sync --group mcp
uv run croissant-baker mcp
```

### Agent Skill

The [Agent Skill](https://agentskills.io/specification) that teaches an LLM how
to drive the tool lives at
`src/croissant_baker/skills/croissant-baker/SKILL.md`, inside the package, so
`pip install croissant-baker` delivers it. `.agents/skills/croissant-baker` is
a relative symlink to that one directory, the cross-client discovery
convention, so agent clients find it in a checkout; edit the canonical copy,
never the link. The MCP server publishes the same file as the resource
`croissant-baker://skill`.

Validate it against the specification with the reference library:

```bash
uvx --from "git+https://github.com/agentskills/agentskills#subdirectory=skills-ref" \
  skills-ref validate src/croissant_baker/skills/croissant-baker
```

`tests/test_skill.py` checks the frontmatter, the body length, the symlink and
that the installed package carries the file. Adding another symlink to the
skill needs a matching entry in `[tool.hatch.build] exclude` in
`pyproject.toml`, or the build will ship the skill under the link's path
instead of inside the package.

## Testing

```bash
uv run pytest -v                                          # all tests
uv run pytest tests/test_cli.py::test_creator_formats -v  # single test
uv run pytest --cov --cov-report=term-missing             # with coverage
```

The coverage badge in `README.md` and the browsable HTML report it links to are
produced by `test.yaml`: on every push to `main`, `python-coverage-comment-action`
commits a shields.io endpoint and an `htmlcov/` report to the
`python-coverage-comment-action-data` branch, and on pull requests it comments
the coverage delta. Until that first push to `main` creates the branch, the
endpoint URL is a 404 and the badge renders as an error rather than as nothing,
so on a pull request that adds the badge it looks broken until the merge.

End-to-end tests in `tests/test_end_to_end.py` run Croissant Baker on datasets under `tests/data/input/` and validate the generated Croissant metadata with `mlcroissant`. Covered datasets include MIMIC-IV, eICU, MIT-BIH, MEDS, OMOP, glaucoma fundus, satellite imagery, a synthetic partitioned-Parquet layout, and a committed subset of Open Targets (3 datasets, ~2 MB). JSON-LD outputs are written to `tests/data/output/`.

### External evaluation

The `eval/` directory holds full-scale datasets used to evaluate output quality against independently authored Croissant metadata (see [`eval/README.md`](eval/README.md)):

```bash
bash eval/open_targets/download.sh          # one-time, ~20-30 GB
uv run pytest eval/open_targets/ -v
```

## Pre-commit hooks

This project uses `pre-commit` with [Ruff](https://docs.astral.sh/ruff/) to lint and format Python code. After `uv run pre-commit install`, hooks fire automatically on every `git commit`. To run manually:

```bash
uv run pre-commit run --all-files
```

## Documentation

Docs use MkDocs with Material theme. Some pages are auto-generated from source code:

```bash
uv sync --group docs
uv run python docs/generate.py    # regenerate CLI reference, formats table, RAI flags
uv run --group docs mkdocs serve  # preview at http://127.0.0.1:8000
```

The `docs/generate.py` script produces:

- `docs/reference/cli.md` — from typer introspection
- `docs/_generated/formats-table.md` — from handler `EXTENSIONS`/`FORMAT_NAME`/`INPUT_KIND` and the compression registry
- `docs/_generated/rai-flags-table.md` — from typer parameter inspection

Re-run `uv run python docs/generate.py` after changing CLI flags or adding/modifying handlers.

### CI workflows

| Workflow | Trigger | What it does |
|----------|---------|-------------|
| `test.yaml` | Push/PR to `main` | Runs tests on Python 3.10 + 3.12, and reports coverage from the 3.12 leg |
| `coverage-comment.yaml` | `test.yaml` completing | Posts the coverage comment for pull requests from forks |
| `pre-commit.yaml` | Push/PR | Runs ruff lint + format checks |
| `release-please.yaml` | Push to `main` | Opens/updates Release PR; on release, runs `uv build` |
| `docs.yaml` | Push to `main` | Runs `generate.py` + `mkdocs gh-deploy --force` |

#### One-time: enable GitHub Pages

After the first `docs.yaml` run creates the `gh-pages` branch:

**Settings → Pages → Source: "Deploy from a branch" → Branch: `gh-pages` → `/ (root)` → Save**

## Releases

This project uses [release-please](https://github.com/googleapis/release-please) to automate versioning and GitHub Releases.

### How it works

Every push to `main` triggers the release-please GitHub Action, which opens or updates a **Release PR** that bumps the version in `pyproject.toml` and generates a `CHANGELOG.md` entry. When ready, merge the Release PR to:

1. Create a git tag (e.g. `v0.1.0`)
2. Create a GitHub Release with generated notes
3. Trigger the `publish` job (`uv build`)

### Commit message conventions

| Prefix | Effect | Example |
|--------|--------|---------|
| `feat:` | minor bump | `feat: add Parquet handler` |
| `fix:` | patch bump | `fix: handle empty CSV files` |
| `perf:` | patch bump | `perf: stream CSV in chunks` |
| `feat!:` / `BREAKING CHANGE:` | major bump | `feat!: remove legacy API` |
| `docs:`, `build:`, `chore:` | no bump | `docs: update README` |

### One-time repository setup

**Settings → Actions → General → Workflow permissions → "Read and write permissions"**

### Publishing to PyPI

The wheel build is already configured — `uv build` produces a clean wheel containing only the `croissant_baker/` package (no tests, eval, or docs). To enable PyPI upload:

1. Add a PyPI API token as a repository secret named `PYPI_API_TOKEN`
2. Uncomment the publish step in `.github/workflows/release-please.yaml`

## Adding a new file handler

1. Create `src/croissant_baker/handlers/your_handler.py`
2. Subclass `FileTypeHandler` and implement `claims`, `extract`, `build_croissant`
3. Set class attributes: `EXTENSIONS`, `FORMAT_NAME`, `FORMAT_DESCRIPTION`
4. Add the instance to `registry.py` → `builtin_handlers()`
5. Add a sample to `tests/helpers.py` → `SAMPLES`. A handler with neither a
   sample nor a recorded exemption fails the suite; that one entry drives the
   contract sweep (`tests/test_handler_contract.py`) and the compression matrix
   (`tests/test_compression_matrix.py`)
6. Add what the sweep cannot express — magic bytes, tolerant parsing, resource
   handling — to `tests/test_<format>_handler.py`
7. Run `python docs/generate.py` — the supported formats table updates automatically

Shared test vocabulary is in `tests/helpers.py` (`bake`, `write_wrapped`,
`record_sets`, …) and `tests/conftest.py`, whose autouse `clean_globals` fixture
restores every process-wide registry between tests.

`claims` and `extract` are given a `FileSource`, already decompressed. List
format suffixes only in `EXTENSIONS`, never `.csv.gz`. A handler needing a real
file on disk declares `INPUT_KIND = InputKind.PATH` and is never offered a
compressed one.

`can_handle(Path)` and `extract_metadata(Path)` are the previous contract. They
still work and warn once, so third-party handlers keep running; new handlers
should not use them.
