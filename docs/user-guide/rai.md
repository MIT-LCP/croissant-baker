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

`collection_types` is written out as `rai:dataCollectionType` on the dataset node, unioned across every activity in declaration order, because RAI 1.0 declares that property on the dataset rather than on an activity. Each key is translated on the way out.

| You write | The output holds |
|-----------|------------------|
| `surveys` | `Surveys` |
| `interviews` | `Interviews` |
| `observations` | `Passive Data Collection` |
| `experiments` | `Experiments` |
| `web_scraping` | `Web Scraping` |
| `crowdsourcing` | `Crowdsourcing` |
| `existing_datasets` | `Secondary Data analysis` |
| `simulations` | `Simulations` |
| `other` | `Others` |

Six of those terms are the ones RAI 1.0 recommends. `interviews`, `crowdsourcing` and `simulations` have no recommended term, so the key is written in title case: the property takes plain text and the recommended list is advice, so an honest term is better than a poor fit. Any other text you write is written out as given, so you can write a recommended term straight into the config, or a term of your own.

A complete working example is at [`tests/data/input/mimiciv_demo/physionet.org/mimiciv_demo-rai-example.yaml`](https://github.com/MIT-LCP/croissant-baker/blob/main/tests/data/input/mimiciv_demo/physionet.org/mimiciv_demo-rai-example.yaml).

### What a config is rejected for

Every key is checked against the keys its section accepts, at every level of the file. A key that is not recognised stops the run with an error naming the key by its path in the YAML (for example `activities[0].agents[1].nme`) and listing the keys that section does accept. Where the key came close to an accepted one, the error names it: `started_at` is answered with `Did you mean 'start_at' for 'started_at'?`. A message can list several unknown keys, so every hint names the key it answers.

Values are checked as well. `has_synthetic_data` and `is_synthetic` take a bare `true` or `false`. A quoted `"false"` or `"no"` stops the run, because a quoted word is text and any text would be read as true. A text field takes text, a number, or a date, so `start_at: 2011-01-01` is fine. A list or a mapping in a text field stops the run, because it would reach the output as a Python repr such as `"['sampling', 'labelling']"`.

An entry the output has nothing to point at stops the run too. A source dataset and a model each need a `url`. An agent and a platform each need a `name`. An activity needs an `id`, which becomes its `@id`, and a `type`, which becomes its `prov:label` and `prov:type`.

Together these checks mean a field cannot go missing from the output without a word. The config is read before the dataset is scanned, so the error arrives immediately, and `--dry-run` checks it too.

All three checks are new in this release, so a config written against an older one may now stop the run. The template used to spell the social impact key `social_impact`, which the loader never read; the key is `data_social_impact`, and a config still carrying the old spelling now fails with that suggestion, where the text used to be dropped. A quoted `has_synthetic_data: "false"` used to load as true and announce synthetic content the author had just denied. A list under `data_biases` used to reach the output as the string `"['sampling', 'labelling']"`. A source dataset or model with no `url`, and an agent or platform with no `name`, used to be dropped in silence, filled-in fields and all. An activity with no `id` or `type` was written out as a node with an empty `@id` and a blank `prov:label` and `prov:type`. Each of these now stops the run and names the field. Fill the field in, or delete the entry.

## Apply RAI to an existing file

You can inject RAI into a `.jsonld` file that was already generated:

```bash
croissant-baker rai-apply dataset-croissant.jsonld \
  --rai-config rai.yaml \
  --output dataset-croissant-rai.jsonld
```

Omit `--output` to overwrite the input file in place.
