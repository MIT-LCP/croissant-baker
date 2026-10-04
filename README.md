# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/MIT-LCP/croissant-baker/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                        |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|------------------------------------------------------------ | -------: | -------: | -------: | -------: | ------: | --------: |
| src/croissant\_baker/\_\_init\_\_.py                        |        2 |        0 |        0 |        0 |    100% |           |
| src/croissant\_baker/\_\_main\_\_.py                        |      373 |       48 |      140 |       17 |     87% |87-88, 107-108, 168, 203, 274-275, 312, 314, 318, 347, 376, 399, 506-507, 515-\>exit, 946-948, 956-\>991, 992-1000, 1144, 1155-1158, 1186-1187, 1194-1202, 1207-1209, 1226-\>1232, 1232-\>exit, 1238-1244, 1248 |
| src/croissant\_baker/assembly.py                            |       65 |        0 |       26 |        2 |     98% |144-\>143, 154-\>156 |
| src/croissant\_baker/compression.py                         |       58 |        3 |       14 |        2 |     93% |69-70, 137 |
| src/croissant\_baker/duplicates.py                          |       56 |        4 |       24 |        0 |     95% |103-104, 112-113 |
| src/croissant\_baker/entries.py                             |       86 |        0 |        4 |        0 |    100% |           |
| src/croissant\_baker/files.py                               |       34 |        2 |       16 |        0 |     96% |     76-77 |
| src/croissant\_baker/handlers/\_\_init\_\_.py               |        0 |        0 |        0 |        0 |    100% |           |
| src/croissant\_baker/handlers/bam\_handler.py               |       69 |        3 |       16 |        0 |     96% |105-106, 165 |
| src/croissant\_baker/handlers/base\_handler.py              |       89 |       15 |       20 |        0 |     84% |172, 192, 203-204, 208-209, 235, 277-279, 284-289 |
| src/croissant\_baker/handlers/bcf\_handler.py               |       53 |        3 |       10 |        0 |     95% |115-116, 136 |
| src/croissant\_baker/handlers/cram\_handler.py              |      147 |        5 |       46 |        5 |     95% |149-\>151, 180, 288, 339, 408, 418 |
| src/croissant\_baker/handlers/csv\_handler.py               |      140 |       14 |       36 |        8 |     86% |91-\>137, 105, 112-\>117, 121-127, 138, 140, 192, 226-227, 245, 271, 338 |
| src/croissant\_baker/handlers/dicom\_handler.py             |      154 |        6 |       72 |       10 |     93% |35-36, 47-\>49, 49-\>52, 56-\>59, 60-\>63, 64-\>67, 75-76, 79-\>82, 95-\>101, 194, 280, 326-\>328 |
| src/croissant\_baker/handlers/fasta\_handler.py             |       34 |        0 |       10 |        0 |    100% |           |
| src/croissant\_baker/handlers/fastq\_handler.py             |       63 |        1 |       26 |        1 |     98% |        88 |
| src/croissant\_baker/handlers/fhir\_handler.py              |      194 |       24 |       84 |       14 |     86% |65-66, 77-80, 101-\>103, 140, 146-147, 199, 202-206, 208, 214-217, 224, 263-264, 269, 293, 296-\>290, 300, 347-\>342, 362-363 |
| src/croissant\_baker/handlers/hdf5\_handler/\_\_init\_\_.py |        2 |        0 |        0 |        0 |    100% |           |
| src/croissant\_baker/handlers/hdf5\_handler/handler.py      |       82 |        0 |       24 |        0 |    100% |           |
| src/croissant\_baker/handlers/hdf5\_handler/layouts.py      |      283 |       14 |      122 |       12 |     93% |230-234, 285, 377, 383, 397-398, 433, 454, 517-\>515, 593-\>609, 626, 628, 707-\>705 |
| src/croissant\_baker/handlers/hdf5\_handler/reading.py      |      106 |        1 |       42 |        1 |     99% |       255 |
| src/croissant\_baker/handlers/image\_handler.py             |      199 |       15 |       74 |       10 |     89% |97, 158-167, 170, 175, 591, 602-605, 613-\>615, 615-\>617, 617-\>619, 619-\>599 |
| src/croissant\_baker/handlers/json\_handler.py              |       93 |        7 |       32 |        6 |     90% |70-71, 123, 125-\>111, 132, 171, 176, 182 |
| src/croissant\_baker/handlers/nifti\_handler.py             |      131 |        4 |       62 |       18 |     89% |54-\>56, 56-\>58, 58-\>60, 67-\>69, 69-\>71, 71-\>75, 77-\>81, 83-84, 149-\>143, 162, 252, 262-\>264, 264-\>266, 266-\>268, 274-\>277, 283-\>285, 285-\>287, 287-\>289, 291-\>295 |
| src/croissant\_baker/handlers/ome.py                        |       88 |        1 |       14 |        1 |     98% |        94 |
| src/croissant\_baker/handlers/parquet\_handler.py           |      178 |        1 |       56 |        1 |     99% |        65 |
| src/croissant\_baker/handlers/registry.py                   |       94 |        3 |       24 |        1 |     97% |67, 79, 206 |
| src/croissant\_baker/handlers/sam\_handler.py               |       63 |        2 |       22 |        2 |     95% |  182, 199 |
| src/croissant\_baker/handlers/sam\_header.py                |       73 |        2 |       34 |        5 |     93% |31, 50-\>29, 65-\>63, 76-\>exit, 86 |
| src/croissant\_baker/handlers/soft.py                       |      241 |        4 |       72 |        3 |     98% |205-\>211, 207, 213, 450-451 |
| src/croissant\_baker/handlers/soft\_handler.py              |      124 |        6 |       38 |        1 |     96% |113, 119-121, 175-178 |
| src/croissant\_baker/handlers/spreadsheet\_handler.py       |      317 |       12 |      124 |        8 |     95% |89, 92, 148-\>150, 166-\>169, 206, 209, 211, 214-\>224, 254, 298-\>300, 362, 396-398, 466-467 |
| src/croissant\_baker/handlers/tsv\_handler.py               |       14 |        0 |        0 |        0 |    100% |           |
| src/croissant\_baker/handlers/utils.py                      |      290 |       17 |      142 |       13 |     93% |69, 333, 428, 432, 444, 454-455, 522, 525, 529-530, 552-\>554, 622, 647, 705, 709, 829-830 |
| src/croissant\_baker/handlers/vcf\_handler.py               |      245 |        5 |       98 |        4 |     97% |246-247, 249-250, 272, 330-\>exit |
| src/croissant\_baker/handlers/wfdb\_handler.py              |       65 |        3 |       26 |        9 |     87% |78-\>90, 113, 115, 117, 118-\>120, 120-\>122, 122-\>124, 124-\>126, 126-\>129 |
| src/croissant\_baker/identifiers.py                         |       58 |        6 |       32 |        5 |     86% |20-\>22, 38-\>40, 77, 87-89, 107, 109 |
| src/croissant\_baker/metadata\_generator.py                 |      392 |       17 |      168 |       10 |     95% |168-\>170, 174, 176, 180-\>179, 343, 524-\>523, 558-561, 576-578, 594-\>592, 604, 945-946, 977-978, 1035-1036 |
| src/croissant\_baker/rai/\_\_init\_\_.py                    |        4 |        0 |        0 |        0 |    100% |           |
| src/croissant\_baker/rai/injector.py                        |       78 |       11 |       48 |        5 |     83% |102, 163-\>165, 171-179, 188, 190-\>exit |
| src/croissant\_baker/rai/loader.py                          |      139 |        6 |       54 |        2 |     94% |55-59, 151 |
| src/croissant\_baker/rai/schema.py                          |       53 |        0 |        0 |        0 |    100% |           |
| src/croissant\_baker/references.py                          |       92 |        1 |       42 |        1 |     99% |       181 |
| src/croissant\_baker/report.py                              |       50 |        1 |        6 |        1 |     96% |       113 |
| src/croissant\_baker/scan.py                                |        9 |        0 |        0 |        0 |    100% |           |
| src/croissant\_baker/sources.py                             |       60 |        1 |        6 |        1 |     97% |       141 |
| **TOTAL**                                                   | **5240** |  **268** | **1906** |  **179** | **93%** |           |


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