# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/MIT-LCP/croissant-baker/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                        |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|------------------------------------------------------------ | -------: | -------: | -------: | -------: | ------: | --------: |
| src/croissant\_baker/\_\_init\_\_.py                        |        2 |        0 |        0 |        0 |    100% |           |
| src/croissant\_baker/\_\_main\_\_.py                        |      330 |        9 |      120 |        5 |     97% |96, 134, 1013, 1107-1109, 1126-\>1132, 1132-\>exit, 1139-1141, 1148 |
| src/croissant\_baker/assembly.py                            |       65 |        0 |       26 |        2 |     98% |144-\>143, 154-\>156 |
| src/croissant\_baker/compression.py                         |       58 |        3 |       14 |        2 |     93% |69-70, 137 |
| src/croissant\_baker/duplicates.py                          |       56 |        4 |       24 |        0 |     95% |103-104, 112-113 |
| src/croissant\_baker/entries.py                             |       86 |        0 |        4 |        0 |    100% |           |
| src/croissant\_baker/files.py                               |       37 |        2 |       18 |        0 |     96% |     85-86 |
| src/croissant\_baker/handlers/\_\_init\_\_.py               |        0 |        0 |        0 |        0 |    100% |           |
| src/croissant\_baker/handlers/bam\_handler.py               |       69 |        3 |       16 |        0 |     96% |105-106, 165 |
| src/croissant\_baker/handlers/base\_handler.py              |       89 |        1 |       20 |        0 |     99% |       235 |
| src/croissant\_baker/handlers/bcf\_handler.py               |       53 |        3 |       10 |        0 |     95% |115-116, 136 |
| src/croissant\_baker/handlers/cram\_handler.py              |      147 |        5 |       46 |        5 |     95% |149-\>151, 180, 288, 339, 408, 418 |
| src/croissant\_baker/handlers/csv\_handler.py               |      175 |        9 |       50 |        6 |     93% |65-71, 91, 153-\>201, 202, 206, 313-314, 334, 427 |
| src/croissant\_baker/handlers/dicom\_handler.py             |      155 |        0 |       72 |        1 |     99% | 327-\>329 |
| src/croissant\_baker/handlers/fasta\_handler.py             |       34 |        0 |       10 |        0 |    100% |           |
| src/croissant\_baker/handlers/fastq\_handler.py             |       63 |        1 |       26 |        1 |     98% |        88 |
| src/croissant\_baker/handlers/fhir\_handler.py              |      196 |        2 |       86 |        4 |     98% |67-68, 117-\>119, 312-\>306, 363-\>358 |
| src/croissant\_baker/handlers/hdf5\_handler/\_\_init\_\_.py |        2 |        0 |        0 |        0 |    100% |           |
| src/croissant\_baker/handlers/hdf5\_handler/handler.py      |       82 |        0 |       24 |        0 |    100% |           |
| src/croissant\_baker/handlers/hdf5\_handler/layouts.py      |      283 |       14 |      122 |       12 |     93% |230-234, 285, 377, 383, 397-398, 433, 454, 517-\>515, 593-\>609, 626, 628, 707-\>705 |
| src/croissant\_baker/handlers/hdf5\_handler/reading.py      |      106 |        1 |       42 |        1 |     99% |       255 |
| src/croissant\_baker/handlers/image\_handler.py             |      199 |       12 |       74 |        4 |     92% |97, 158-167, 170, 175 |
| src/croissant\_baker/handlers/json\_handler.py              |       93 |        7 |       32 |        6 |     90% |70-71, 123, 125-\>111, 132, 171, 176, 182 |
| src/croissant\_baker/handlers/nifti\_handler.py             |      132 |        2 |       62 |        6 |     96% |55-\>57, 68-\>70, 84-85, 263-\>265, 275-\>278, 284-\>286, 292-\>296 |
| src/croissant\_baker/handlers/ome.py                        |       88 |        1 |       14 |        1 |     98% |        94 |
| src/croissant\_baker/handlers/parquet\_handler.py           |      178 |        1 |       56 |        1 |     99% |        65 |
| src/croissant\_baker/handlers/registry.py                   |       94 |        3 |       24 |        1 |     97% |67, 79, 206 |
| src/croissant\_baker/handlers/sam\_handler.py               |       63 |        2 |       22 |        2 |     95% |  182, 199 |
| src/croissant\_baker/handlers/sam\_header.py                |       73 |        0 |       34 |        0 |    100% |           |
| src/croissant\_baker/handlers/soft.py                       |      241 |        4 |       72 |        3 |     98% |205-\>211, 207, 213, 450-451 |
| src/croissant\_baker/handlers/soft\_handler.py              |      124 |        6 |       38 |        1 |     96% |113, 119-121, 175-178 |
| src/croissant\_baker/handlers/spreadsheet\_handler.py       |      317 |       12 |      124 |        8 |     95% |89, 92, 148-\>150, 166-\>169, 206, 209, 211, 214-\>224, 254, 298-\>300, 362, 396-398, 466-467 |
| src/croissant\_baker/handlers/tsv\_handler.py               |       14 |        0 |        0 |        0 |    100% |           |
| src/croissant\_baker/handlers/utils.py                      |      290 |        7 |      142 |        4 |     97% |69, 333, 428, 454-455, 529-530, 552-\>554 |
| src/croissant\_baker/handlers/vcf\_handler.py               |      245 |        0 |       98 |        0 |    100% |           |
| src/croissant\_baker/handlers/wfdb\_handler.py              |       65 |        0 |       26 |        5 |     95% |118-\>120, 120-\>122, 122-\>124, 124-\>126, 126-\>129 |
| src/croissant\_baker/identifiers.py                         |       58 |        0 |       32 |        2 |     98% |20-\>22, 38-\>40 |
| src/croissant\_baker/mcp\_server.py                         |       75 |        1 |        4 |        0 |     99% |       330 |
| src/croissant\_baker/metadata\_generator.py                 |      395 |       17 |      168 |       10 |     95% |206-\>208, 212, 214, 218-\>217, 385, 568-\>567, 602-605, 620-622, 638-\>636, 648, 989-990, 1021-1022, 1079-1080 |
| src/croissant\_baker/pipeline.py                            |       78 |        0 |       24 |        0 |    100% |           |
| src/croissant\_baker/rai/\_\_init\_\_.py                    |        4 |        0 |        0 |        0 |    100% |           |
| src/croissant\_baker/rai/injector.py                        |      127 |        1 |       70 |        1 |     99% |       265 |
| src/croissant\_baker/rai/loader.py                          |      139 |        6 |       54 |        2 |     94% |55-59, 151 |
| src/croissant\_baker/rai/schema.py                          |       53 |        0 |        0 |        0 |    100% |           |
| src/croissant\_baker/references.py                          |       92 |        1 |       42 |        1 |     99% |       181 |
| src/croissant\_baker/report.py                              |       50 |        1 |        6 |        1 |     96% |       113 |
| src/croissant\_baker/scan.py                                |       10 |        0 |        0 |        0 |    100% |           |
| src/croissant\_baker/sources.py                             |       60 |        1 |        6 |        1 |     97% |       141 |
| **TOTAL**                                                   | **5445** |  **142** | **1954** |   **99** | **97%** |           |


## Setup coverage badge

Below are examples of the badges you can use in your main branch `README` file.

### Direct image

[![Coverage badge](https://raw.githubusercontent.com/MIT-LCP/croissant-baker/python-coverage-comment-action-data/badge.svg)](https://htmlpreview.github.io/?https://github.com/MIT-LCP/croissant-baker/blob/python-coverage-comment-action-data/htmlcov/index.html)

This is the one to use if your repository is private or if you don't want to customize anything.

### [Shields.io](https://shields.io) Json Endpoint

[![Coverage badge](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/MIT-LCP/croissant-baker/python-coverage-comment-action-data/endpoint.json)](https://htmlpreview.github.io/?https://github.com/MIT-LCP/croissant-baker/blob/python-coverage-comment-action-data/htmlcov/index.html)

Using this one will allow you to [customize](https://shields.io/endpoint) the look of your badge.
It won't work with private repositories. It won't be refreshed more than once per five minutes.

### [Shields.io](https://shields.io) Dynamic Badge

[![Coverage badge](https://img.shields.io/badge/dynamic/json?color=brightgreen&label=coverage&query=%24.message&url=https%3A%2F%2Fraw.githubusercontent.com%2FMIT-LCP%2Fcroissant-baker%2Fpython-coverage-comment-action-data%2Fendpoint.json)](https://htmlpreview.github.io/?https://github.com/MIT-LCP/croissant-baker/blob/python-coverage-comment-action-data/htmlcov/index.html)

This one will always be the same color. It won't work for private repos. I'm not even sure why we included it.

## What is that?

This branch is part of the
[python-coverage-comment-action](https://github.com/marketplace/actions/python-coverage-comment)
GitHub Action. All the files in this branch are automatically generated and may be
overwritten at any moment.