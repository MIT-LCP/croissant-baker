# RAI Metadata

Croissant Baker supports the [Responsible AI (RAI) extension](https://github.com/mlcommons/croissant/blob/main/docs/croissant-rai-spec.md) for documenting data provenance, fairness considerations, and collection activities. RAI metadata is embedded directly in the `.jsonld` output.

There are two separate workflows — they cannot be combined in the same command:

| Workflow | When to use |
|----------|------------|
| Native `--rai-*` CLI flags | Quick, dataset-level fields already supported by `mlcroissant` |
| `--rai-config` YAML file | Richer documentation: provenance, activities, lineage, annotator info |

## Native CLI flags

Pass any combination of `--rai-*` flags directly on the generate command:

```bash
croissant-baker \
  --input ./dataset \
  --creator "Jane Smith" \
  --rai-data-collection "Retrospective EHR data collected 2010–2019 at Example Hospital" \
  --rai-data-collection-type "observational" \
  --rai-data-biases "Single-site cohort; skews toward English-speaking adults" \
  --rai-data-limitations "Adults only; not suitable for direct clinical decision-making" \
  --rai-data-social-impact "May improve clinical AI research but risks amplifying disparities" \
  --rai-personal-sensitive-information "De-identified patient records under HIPAA Safe Harbor" \
  --output dataset-croissant.jsonld
```

Flags that accept multiple values can be repeated:

```bash
--rai-data-preprocessing-protocol "Outlier removal" \
--rai-data-preprocessing-protocol "Unit normalization"
```

### Available flags

--8<-- "_generated/rai-flags-table.md"

## YAML config file

For richer RAI documentation, write a YAML config and pass it with `--rai-config`:

```bash
croissant-baker \
  --input ./dataset \
  --creator "Jane Smith" \
  --rai-config rai.yaml \
  --output dataset-croissant.jsonld
```

The YAML covers three sections:

### `ai_fairness`

```yaml
ai_fairness:
  data_limitations: >
    Single-site cohort from one academic medical centre.
    Findings may not generalise to other hospital systems.

  data_biases: >
    Skews toward English-speaking adults; paediatric patients under-represented.

  personal_sensitive_information: >
    De-identified patient records. Re-identification risk minimised via HIPAA Safe Harbor.

  data_use_cases: >
    Benchmarking clinical NLP and ML models. Not for direct clinical decision-making.

  data_social_impact: >
    May improve clinical AI research, but risks amplifying health disparities
    if deployed without careful evaluation.

  has_synthetic_data: false
```

### `lineage`

```yaml
lineage:
  source_datasets:
    - url: https://physionet.org/content/mimiciii/
      name: MIMIC-III
      organisation: PhysioNet
  models: []
```

### `activities`

```yaml
activities:
  - id: ACT-001
    type: data_collection
    description: >
      Retrospective EHR collected during routine clinical care.
    start_at: "2011-01-01"
    end_at: "2019-12-31"
    collection_types:
      - observations
      - existing_datasets
    agents:
      - name: Beth Israel Deaconess Medical Center
        url: https://www.bidmc.org
        is_synthetic: false

  - id: ACT-002
    type: data_preprocessing
    description: De-identification via HIPAA Safe Harbor procedures.
    agents:
      - name: MIT Laboratory for Computational Physiology
        url: https://lcp.mit.edu
```

`collection_types` is written out as `rai:dataCollectionType` on the dataset node, unioned across every activity in declaration order, because RAI 1.0 declares that property on the dataset rather than on an activity.

A complete working example is at [`tests/data/input/mimiciv_demo/physionet.org/mimiciv_demo-rai-example.yaml`](https://github.com/MIT-LCP/croissant-baker/blob/main/tests/data/input/mimiciv_demo/physionet.org/mimiciv_demo-rai-example.yaml).

### Unknown keys are rejected

Every key is checked against the keys its section accepts, at every level of the file. A key that is not recognised stops the run with an error naming the key by its path in the YAML (for example `activities[0].agents[1].nme`) and listing the keys that section does accept. A misspelled key is therefore never dropped in silence, so a field cannot go missing from the output without a word. The config is read before the dataset is scanned, so the error arrives immediately, and `--dry-run` checks it too.

## Apply RAI to an existing file

You can inject RAI into a `.jsonld` file that was already generated:

```bash
croissant-baker rai-apply dataset-croissant.jsonld \
  --rai-config rai.yaml \
  --output dataset-croissant-rai.jsonld
```

Omit `--output` to overwrite the input file in place.
