# Changelog

## [0.3.2](https://github.com/MIT-LCP/croissant-baker/compare/croissant-baker-v0.3.1...croissant-baker-v0.3.2) (2026-07-05)


### Bug Fixes

* bound discovery memory and route generator warnings through logging ([84f925c](https://github.com/MIT-LCP/croissant-baker/commit/84f925ce2a07e585a8d7302cf1b024758b968eb0))
* bound discovery memory and route generator warnings through logging ([4f3d42e](https://github.com/MIT-LCP/croissant-baker/commit/4f3d42e70c962649c0c6124d6375404a0cd756ae))
* make generated metadata reproducible (drop wall-clock defaults) ([4691ae0](https://github.com/MIT-LCP/croissant-baker/commit/4691ae059a342cd9b0d3c105c9584f3078dcd786))
* make generated metadata reproducible (drop wall-clock defaults) ([8110a8a](https://github.com/MIT-LCP/croissant-baker/commit/8110a8aa9468b3d015401344dcb53b7fc0886437))


### Performance Improvements

* extract file metadata concurrently (thread pool) ([ebe3c58](https://github.com/MIT-LCP/croissant-baker/commit/ebe3c5838e4d31d628a3bdd6c3a2b2534f9c8358))
* extract file metadata concurrently (thread pool) ([17431be](https://github.com/MIT-LCP/croissant-baker/commit/17431bec542368633bcb51107cef22a82835ba49))


### Build System

* **deps:** bump actions/checkout from 6 to 7 ([261d32c](https://github.com/MIT-LCP/croissant-baker/commit/261d32c1c2a56b24b4214b122907ffaffe88dd2b))
* **deps:** bump actions/checkout from 6 to 7 ([b5ec2fa](https://github.com/MIT-LCP/croissant-baker/commit/b5ec2fa11f34e34e3948f99f7bc437b5292e7060))

## [0.3.1](https://github.com/MIT-LCP/croissant-baker/compare/croissant-baker-v0.3.0...croissant-baker-v0.3.1) (2026-05-18)


### Documentation

* add arXiv preprint citation ([ad8a5d3](https://github.com/MIT-LCP/croissant-baker/commit/ad8a5d3fbd23c8b6fc8b693ac9c9d04ac9564e92))
* add arXiv preprint citation ([2999a91](https://github.com/MIT-LCP/croissant-baker/commit/2999a91ceddd23506bbd764996d2c5988798a76f))

## [0.3.0](https://github.com/MIT-LCP/croissant-baker/compare/croissant-baker-v0.2.0...croissant-baker-v0.3.0) (2026-05-04)


### Features

* **eval:** add DICOM, NIfTI, and BIDS sidecar evaluations ([f228b61](https://github.com/MIT-LCP/croissant-baker/commit/f228b61407d391d0668bc2c57f3353c2d18b087a))
* **eval:** add DICOM, NIfTI, and BIDS sidecar evaluations ([ffc2b3b](https://github.com/MIT-LCP/croissant-baker/commit/ffc2b3b9368384237490146821e35eb0f5573901))
* **eval:** add NeurIPS 2025 D&B cross-domain evaluation ([3a27618](https://github.com/MIT-LCP/croissant-baker/commit/3a27618b379e39e02f1610835869b516168b2a6d))
* **eval:** add NeurIPS 2025 D&B cross-domain evaluation ([c52dca7](https://github.com/MIT-LCP/croissant-baker/commit/c52dca731c851730641d5d16f14047d18eb07a18))
* **eval:** add SMART FHIR bundle and bulk evaluations ([0eb7f58](https://github.com/MIT-LCP/croissant-baker/commit/0eb7f589b2ec01e7e4049539e351be5b321e5791))


### Bug Fixes

* close field-id and sub-field guard gaps ([3802036](https://github.com/MIT-LCP/croissant-baker/commit/3802036c029f234420fece644d2cb25d78903887))
* **eval:** show download errors instead of swallowing them ([c3bcfde](https://github.com/MIT-LCP/croissant-baker/commit/c3bcfdebd6704a320016a3b2b2d4f66ca51da84a))
* prevent JSON-LD [@id](https://github.com/id) collisions across all handlers ([5e0db5f](https://github.com/MIT-LCP/croissant-baker/commit/5e0db5fbe41147608eaa26c18e10655b7c7a098a))

## [0.2.0](https://github.com/MIT-LCP/croissant-baker/compare/croissant-baker-v0.1.2...croissant-baker-v0.2.0) (2026-04-29)


### Features

* add DICOM and NIfTI handlers ([a74ab41](https://github.com/MIT-LCP/croissant-baker/commit/a74ab41ddaed4f47340a5a6fc1c776f8df905fff))
* add DICOM and NIfTI handlers for medical imaging datasets ([9fdb034](https://github.com/MIT-LCP/croissant-baker/commit/9fdb034ba83fcc89a089ce031b8ba18249929af9))
* add progress bar. ref [#44](https://github.com/MIT-LCP/croissant-baker/issues/44). ([8c77c11](https://github.com/MIT-LCP/croissant-baker/commit/8c77c11a285a5f6e8f9d2c8ac04ac0fda188245e))
* add progress bar. ref [#44](https://github.com/MIT-LCP/croissant-baker/issues/44). ([6025575](https://github.com/MIT-LCP/croissant-baker/commit/60255755c98c62038b531ac3c5cab517f992b281))
* declare Croissant 1.1 conformance and expose optional spec flags ([5d0eb7e](https://github.com/MIT-LCP/croissant-baker/commit/5d0eb7e639fecc65875ce486bd36c4f807f5e003))
* declare Croissant 1.1 conformance and expose optional spec flags ([890af17](https://github.com/MIT-LCP/croissant-baker/commit/890af17bc8a213ef6215e070f1791e489894216c))
* **field-mapping, usage-info:** warn on multi-match, accept any URI scheme ([66d813e](https://github.com/MIT-LCP/croissant-baker/commit/66d813e9430a631be3f17bccc26d7863f2eefaec))


### Bug Fixes

* apply DICM preamble check uniformly in DICOMHandler.can_handle ([50e79d7](https://github.com/MIT-LCP/croissant-baker/commit/50e79d78cfb2e82f3eaf1b883c58586567d09299))
* count only cr:FileObject entries in CLI summary ([3571858](https://github.com/MIT-LCP/croissant-baker/commit/3571858b1ddded7f528c2c099ce3178021caec20))
* count only cr:FileObject entries in CLI summary ([c324d7f](https://github.com/MIT-LCP/croissant-baker/commit/c324d7fd70d8d7bfdf1d0dedb07ae981588a3806)), closes [#92](https://github.com/MIT-LCP/croissant-baker/issues/92)
* move pydicom and nibabel imports to module level ([15c9d2e](https://github.com/MIT-LCP/croissant-baker/commit/15c9d2e70f4054ff0073c7c1a6b6b790c058805b))
* surface DICOM skip count, capture PatientID, cite NIfTI header sources ([8834241](https://github.com/MIT-LCP/croissant-baker/commit/88342410dfcabeb2bd795afadd2464c97698707c))
* validate magic bytes in image and parquet can_handle ([#93](https://github.com/MIT-LCP/croissant-baker/issues/93)) ([03a332e](https://github.com/MIT-LCP/croissant-baker/commit/03a332e71676ffa07081a477952de5565aa12d3e))
* validate magic bytes in image and parquet can_handle ([#93](https://github.com/MIT-LCP/croissant-baker/issues/93)) ([4d4ee79](https://github.com/MIT-LCP/croissant-baker/commit/4d4ee796e8cae9461827be641271e6eff6e4d43b))


### Reverts

* restore E2E snapshot outputs to pre-PR state ([d98ade8](https://github.com/MIT-LCP/croissant-baker/commit/d98ade8d639e41c8fd9ba6e86a9ba28d1abad5a6))


### Build System

* **deps:** bump googleapis/release-please-action from 4 to 5 ([775e43f](https://github.com/MIT-LCP/croissant-baker/commit/775e43f6a9bf61e95e4bf75cad7be5b125ca1dbc))
* **deps:** bump googleapis/release-please-action from 4 to 5 ([1f47dd3](https://github.com/MIT-LCP/croissant-baker/commit/1f47dd30503d12a933719e570900c8f136cb9879))

## [0.1.2](https://github.com/MIT-LCP/croissant-baker/compare/croissant-baker-v0.1.1...croissant-baker-v0.1.2) (2026-04-16)


### Bug Fixes

* Add Absolute URLs ([860b99e](https://github.com/MIT-LCP/croissant-baker/commit/860b99e7cc28ee19ec6bd06ec0fb789b46491e86))
* use absolute URL for PyPI logo ([070465a](https://github.com/MIT-LCP/croissant-baker/commit/070465ab9e03d7913c8ac55506f17995084c5f9d))
* use absolute URL for PyPI logo and markdown docs ([e98df54](https://github.com/MIT-LCP/croissant-baker/commit/e98df54bf8489b8972f4d4bf3f18234c954e36fc))

## [0.1.1](https://github.com/MIT-LCP/croissant-baker/compare/croissant-baker-v0.1.0...croissant-baker-v0.1.1) (2026-04-16)


### Bug Fixes

* update README docs link for PyPI page ([af55746](https://github.com/MIT-LCP/croissant-baker/commit/af55746a09454120077ca62a1c95fc31ce8f35ee))
* update README for PyPI page rendering ([cb760ac](https://github.com/MIT-LCP/croissant-baker/commit/cb760acedcf48ea56be81fe2fe08c0c3de099633))

## [0.1.0](https://github.com/MIT-LCP/croissant-baker/compare/croissant-baker-v0.0.1...croissant-baker-v0.1.0) (2026-04-16)


### Features

* activate PyPI publishing ([d306be0](https://github.com/MIT-LCP/croissant-baker/commit/d306be021b751e5fe28067e49894621bd9f513f4))
* Activate release-please deployment to PyPI. ([d96c271](https://github.com/MIT-LCP/croissant-baker/commit/d96c271f22e7518b60b7c9141827af519c5f46e1))
* Add CLI metadata overrides with MIMIC-IV demo example ([b317fc0](https://github.com/MIT-LCP/croissant-baker/commit/b317fc0bdb43652773099726dc526f8fda8d34df))
* Add CLI metadata overrides with MIMIC-IV demo example ([f482c0d](https://github.com/MIT-LCP/croissant-baker/commit/f482c0db281de850d987dec5682003650b9a06d5))
* add FHIR NDJSON/Bundle handler ([f255b59](https://github.com/MIT-LCP/croissant-baker/commit/f255b5938c84060e66442146f28c41b696f72895))
* add FHIR NDJSON/Bundle handler and generic JSON/JSONL handler ([bc43ade](https://github.com/MIT-LCP/croissant-baker/commit/bc43adea44315494ed95b08bfef79731d3466646))
* add glob-based file inclusion and exclusion filtering with dry run support ([ada33ed](https://github.com/MIT-LCP/croissant-baker/commit/ada33edd086f521979ca8f09244b98782e517a75))
* add glob-based file inclusion and exclusion filtering with dry-run support ([f8cde9c](https://github.com/MIT-LCP/croissant-baker/commit/f8cde9cb6cd15834642e435c5577dfb105780d92))
* Add initial Croissant metadata generation CLI ([8b60763](https://github.com/MIT-LCP/croissant-baker/commit/8b60763f7a90149d6b367071be374f1d348fb88c))
* Add initial Croissant metadata generation CLI ([2e552c9](https://github.com/MIT-LCP/croissant-baker/commit/2e552c92783149eb53e10e43a68e7307e3510f1a))
* add MEDS Parquet support and test ([70865ac](https://github.com/MIT-LCP/croissant-baker/commit/70865ac633a5dad04effa13a3b875c973e3f8204))
* add MEDS Parquet support and test ([032a789](https://github.com/MIT-LCP/croissant-baker/commit/032a789cf86fe05ba5ca89c36d285f047d3efd39))
* add MIMIC-IV OMOP test support (closes [#13](https://github.com/MIT-LCP/croissant-baker/issues/13)) ([e7f9a5c](https://github.com/MIT-LCP/croissant-baker/commit/e7f9a5c8ecd5ac914e89c0dd0ad05afdf4064fdd))
* add MIMIC-IV OMOP test support (closes [#13](https://github.com/MIT-LCP/croissant-baker/issues/13)) ([505b27d](https://github.com/MIT-LCP/croissant-baker/commit/505b27d8be26db58823aae75973d9fac8eeb939e))
* add native RAI CLI flags alongside YAML workflow ([15e150a](https://github.com/MIT-LCP/croissant-baker/commit/15e150a6607ce41fb4bc5255fe5b10e9e470e292))
* add native RAI CLI flags alongside YAML workflow ([8c46646](https://github.com/MIT-LCP/croissant-baker/commit/8c46646b420accd2f0e251c4a349af3bf5bd8bae))
* add Open Targets evaluation framework ([f4d59f5](https://github.com/MIT-LCP/croissant-baker/commit/f4d59f57b6c6bafba282a707e1d38951efc89a8f))
* add RAI metadata support with PROV-O via YAML config ([4adbff9](https://github.com/MIT-LCP/croissant-baker/commit/4adbff9093d5e9d0990fe2ccf950231cac760dab))
* CSV streaming + type conflict handling for the full 2 datasets ([0ebed58](https://github.com/MIT-LCP/croissant-baker/commit/0ebed58d5fa7880438e3627dc9496dbbb7e0a027))
* CSV streaming + type conflict handling for the full 2 datasets ([e79370e](https://github.com/MIT-LCP/croissant-baker/commit/e79370e02a75d891868033f207f86be8f1e1d207))
* fix PyPI publishing, docs workflow, and site URLs ([89b94fe](https://github.com/MIT-LCP/croissant-baker/commit/89b94febb8063e6b969c27de3782b795dbab7aba))
* group partitioned Parquet tables into FileSet + RecordSet ([9f844e9](https://github.com/MIT-LCP/croissant-baker/commit/9f844e9aa1870920d9c5059d2aa84c0de07c62ca))
* Make --creator required and add automated testing infrastructure ([e29f9bb](https://github.com/MIT-LCP/croissant-baker/commit/e29f9bbf4de9f4a722f307f9fd7379c5822bb748))
* Make --creator required and add automated testing infrastructure ([8b8ca65](https://github.com/MIT-LCP/croissant-baker/commit/8b8ca651c2902ff2361e9d1a949681bae6e464dc))
* Open Targets external evaluation and committed test subset ([3640964](https://github.com/MIT-LCP/croissant-baker/commit/364096400537d4210c7b6d477d23ecc9f7849320))
* Parquet quality fixes and nested type support ([536adf4](https://github.com/MIT-LCP/croissant-baker/commit/536adf45c182a922f5fc1629b745434d4c7a32c6))
* rename package from croissant-maker to croissant-baker ([b8c0020](https://github.com/MIT-LCP/croissant-baker/commit/b8c00200e1ec9461a1cfc0c1012c7df6cf288b27))
* rename package from croissant-maker to croissant-baker ([1c306aa](https://github.com/MIT-LCP/croissant-baker/commit/1c306aa0498e0ceec1b360fe40bf2f1a21b8e158))
* Setup basic project structure ([#1](https://github.com/MIT-LCP/croissant-baker/issues/1)) ([864c853](https://github.com/MIT-LCP/croissant-baker/commit/864c853d06f32b7f5290210a14346416b7863b62))
* warn on missing Croissant spec-required fields and clarify dev … ([6b1e940](https://github.com/MIT-LCP/croissant-baker/commit/6b1e940bb01e75ecbf9e4e6459fb884a612be645))
* warn on missing Croissant spec-required fields and clarify dev workflow in README ([d65bc56](https://github.com/MIT-LCP/croissant-baker/commit/d65bc56b9df068bc82b82fd650d7d1c65d9c6e22))


### Bug Fixes

* add newline at the end of files ([17616b3](https://github.com/MIT-LCP/croissant-baker/commit/17616b3719d59cf38702793d4593353c677bb928))
* add warning log on skipping malformed image entries in summary ([87dc97e](https://github.com/MIT-LCP/croissant-baker/commit/87dc97e94892c9709b2173de797164a81b73ff68))
* adjust num_images to reflect processed items only in image summary ([7634337](https://github.com/MIT-LCP/croissant-baker/commit/7634337e2c5ff038a1895462356710f0e389e9d0))
* close ParquetFile handle to prevent resource leak ([#54](https://github.com/MIT-LCP/croissant-baker/issues/54)) ([b6240ea](https://github.com/MIT-LCP/croissant-baker/commit/b6240ea2871e658747f108748635ce89cd49212e))
* close ParquetFile handle to prevent resource leak ([#54](https://github.com/MIT-LCP/croissant-baker/issues/54)) ([c6e1caf](https://github.com/MIT-LCP/croissant-baker/commit/c6e1caf6d2ec7bc16f09c3013a203b166e3acd9a))
* correct handler sniffing and FileSet glob precision ([25a4058](https://github.com/MIT-LCP/croissant-baker/commit/25a4058f1c1281286b4b3ceee250eeb8b1af4554))
* filter docs workflow paths, build wheel-only, add pypi environment ([57bbda0](https://github.com/MIT-LCP/croissant-baker/commit/57bbda0684ae45cfbfaedf27ea40df28ab64df1f))
* **image:** read TIFF dimensions from tags to avoid swapped metadata ([aa9a80f](https://github.com/MIT-LCP/croissant-baker/commit/aa9a80fd92a80ff503d1d2f763966b471c785f6e))
* **image:** read TIFF dimensions from tags to avoid swapped metadata ([cfbd635](https://github.com/MIT-LCP/croissant-baker/commit/cfbd63575bf1a3857ddcc7d99cdb018a2f60700a))
* prevent auto-generation of output filename when dry-run is enabled ([0a7eb3e](https://github.com/MIT-LCP/croissant-baker/commit/0a7eb3ec39e9e2eb18ff05efd0de58a37e2e7bfd))
* rebase on main, wire include/exclude/dry-run into CLI and tests ([c42fb20](https://github.com/MIT-LCP/croissant-baker/commit/c42fb205febc0f4df23cfff50c409721c043cbe8))
* remove file:// url fallback to prevent local path leakage ([fc47ea2](https://github.com/MIT-LCP/croissant-baker/commit/fc47ea25b3e74edc60b3fda738ea6f53529e0162))
* remove file:// url fallback to prevent local path leakage ([07540de](https://github.com/MIT-LCP/croissant-baker/commit/07540dee2c01c07706075b01832431f8991a7f54))
* resolve KeyError in image_handler properties ([#38](https://github.com/MIT-LCP/croissant-baker/issues/38)) ([494baf2](https://github.com/MIT-LCP/croissant-baker/commit/494baf211fcaed6cf2255b926d1bf31b910d9171))
* resolve KeyError in image_handler properties ([#38](https://github.com/MIT-LCP/croissant-baker/issues/38)) ([ec93a4b](https://github.com/MIT-LCP/croissant-baker/commit/ec93a4b408c7598d2cc4498e031a0812b2351aa8))
* restore technical_overview.md and delete file_filtering.md as intended ([4295941](https://github.com/MIT-LCP/croissant-baker/commit/42959415051f6128513d818697a0c45ad6445dec))
* set PYTHONPATH in generate.py subprocess ([724fb6f](https://github.com/MIT-LCP/croissant-baker/commit/724fb6f1115b4e164507ae639dadb02600689648))
* update site URL to lcp.mit.edu, add PyPI badges and install sections ([7ef676f](https://github.com/MIT-LCP/croissant-baker/commit/7ef676fc8442293391de2e376c35b029b2d5c946))
* **wfdb:** handle header parse failures ([2b3ee52](https://github.com/MIT-LCP/croissant-baker/commit/2b3ee52b847b349353dce5da1aa82dbf7dfca2f4))
* **wfdb:** handle header parse failures ([afd69c7](https://github.com/MIT-LCP/croissant-baker/commit/afd69c726fe983416f4164fb19184eca7c4f29a4))


### Performance Improvements

* fast CSV hashing and opt-in row counting ([70bdfc6](https://github.com/MIT-LCP/croissant-baker/commit/70bdfc68f098b2986b2ef18d28532003af87bfe7))
* fast CSV hashing and opt-in row counting ([1a34e1a](https://github.com/MIT-LCP/croissant-baker/commit/1a34e1a123a5e8f0478f3e381ec2dbe97c951529))


### Documentation

* add --count-csv-rows to README ([725c6d3](https://github.com/MIT-LCP/croissant-baker/commit/725c6d3ea7368de6d12330d3d7cf48f177721145))
* add auto-generated comment to cli.md ([1017de0](https://github.com/MIT-LCP/croissant-baker/commit/1017de0203416a9d8727faff397712e7f39d0c97))
* add comprehensive technical overview of the project architecture, components, and handler system. ([8365bc4](https://github.com/MIT-LCP/croissant-baker/commit/8365bc4109920aa381a0af5de8eb14d32f09e751))
* add comprehensive technical overview of the project architecture, components, and handler system. ([3d8f87b](https://github.com/MIT-LCP/croissant-baker/commit/3d8f87b564456a49d5c4bfcc5e2c8502f597bce3))
* add content pages and remove technical_overview.md ([2ff228a](https://github.com/MIT-LCP/croissant-baker/commit/2ff228a660e7ebd79cad19f8567f382daf3897ef))
* add generate.py and auto-generated reference files ([f11f150](https://github.com/MIT-LCP/croissant-baker/commit/f11f150120510431c21210ef73c3657ab161a8cd))
* add handler metadata for docs automation ([d037ead](https://github.com/MIT-LCP/croissant-baker/commit/d037ead87881470e31551282fa77868f86039c06))
* add MkDocs documentation site with auto-generated reference ([32bba8f](https://github.com/MIT-LCP/croissant-baker/commit/32bba8f701d75fece8aae74a1d3df3faccfa37be))
* add mkdocs infrastructure, deps, and logo ([fdd9a11](https://github.com/MIT-LCP/croissant-baker/commit/fdd9a113b2be5b1cdc93ff0330782a3facf3fbde))
* add PR report for modern Python tooling ([bc5db4b](https://github.com/MIT-LCP/croissant-baker/commit/bc5db4bbf308e2152648b1f86fb4f8860f78138f))
* add PR report for Open Targets partitioned Parquet support ([22930eb](https://github.com/MIT-LCP/croissant-baker/commit/22930eb29be37df1b96157b3162f3ac830974ba9))
* document release-please workflow in README ([49bee84](https://github.com/MIT-LCP/croissant-baker/commit/49bee84c87152ad36feb31bf106434b8585c2720))
* remove technical overview documentation ([87bade5](https://github.com/MIT-LCP/croissant-baker/commit/87bade57676a7a6a6c6f3ab4a2a24adc1179132b))
* slim README, add DEVELOPMENT.md, docs CI workflow ([2bc647d](https://github.com/MIT-LCP/croissant-baker/commit/2bc647dd6636b13b41709fc18c41862530437fcf))
* update README for uv-based developer workflow ([65731f5](https://github.com/MIT-LCP/croissant-baker/commit/65731f5a611a69cab53e7e98057bbb8c8574f107))


### Build System

* **deps-dev:** update pre-commit requirement from &gt;=3.0.0 to &gt;=4.5.1 ([8c41078](https://github.com/MIT-LCP/croissant-baker/commit/8c410782dec5f9d89b3b946ad6fc8636637660b1))
* **deps-dev:** update pre-commit requirement from &gt;=3.0.0 to &gt;=4.5.1 ([e9ee313](https://github.com/MIT-LCP/croissant-baker/commit/e9ee313af82abbaca067a59854ea7e2e785ed308))
* **deps:** bump actions/checkout from 4 to 6 ([b71db40](https://github.com/MIT-LCP/croissant-baker/commit/b71db40b12b287c1ba81c72eeddeb842ff1326fb))
* **deps:** bump actions/checkout from 4 to 6 ([804f9aa](https://github.com/MIT-LCP/croissant-baker/commit/804f9aaebbfacf585525bb260a9f1dfbddefdddd))
* **deps:** bump astral-sh/setup-uv from 5 to 7 ([3427a4a](https://github.com/MIT-LCP/croissant-baker/commit/3427a4a2827649d9d7290555cb71f29d3284114a))
* **deps:** bump astral-sh/setup-uv from 5 to 7 ([a819966](https://github.com/MIT-LCP/croissant-baker/commit/a819966a68b2f9a649b3403f93809f13b5f3bf60))
* **deps:** update pillow requirement from &gt;=11.0.0 to &gt;=12.2.0 ([30e23d2](https://github.com/MIT-LCP/croissant-baker/commit/30e23d2b0598613e6fd4928fed0b8f879c72bfd3))
* **deps:** update pillow requirement from &gt;=11.0.0 to &gt;=12.2.0 ([5a70ff8](https://github.com/MIT-LCP/croissant-baker/commit/5a70ff8599a4c5fec74d7cfb7a44cc9a5e9b8884))
* **deps:** update pyarrow requirement from &gt;=15.0.0 to &gt;=23.0.1 ([7594dae](https://github.com/MIT-LCP/croissant-baker/commit/7594daed72ff600e488073d652c355ea76111dab))
* **deps:** update pyarrow requirement from &gt;=15.0.0 to &gt;=23.0.1 ([82e4b38](https://github.com/MIT-LCP/croissant-baker/commit/82e4b380ccc79256357d74f351dbd30793f9f4c3))
* **deps:** update rich requirement from &gt;=13.0.0 to &gt;=15.0.0 ([8c432cb](https://github.com/MIT-LCP/croissant-baker/commit/8c432cb272440a131a1af663118075b8bec2fcfa))
* **deps:** update rich requirement from &gt;=13.0.0 to &gt;=15.0.0 ([cbd5561](https://github.com/MIT-LCP/croissant-baker/commit/cbd5561272acd078f41fec08104f4d74ef3f96ad))
* **deps:** update wfdb requirement from &gt;=4.0.0 to &gt;=4.3.1 ([66613a8](https://github.com/MIT-LCP/croissant-baker/commit/66613a8c50feb721d04b2c3d2395a2ccc7a874aa))
* **deps:** update wfdb requirement from &gt;=4.0.0 to &gt;=4.3.1 ([739e681](https://github.com/MIT-LCP/croissant-baker/commit/739e681c71654faf0e9ff995b3c970f794ba8a7b))
* migrate to hatchling + uv with PEP 735 dependency groups ([ac1076d](https://github.com/MIT-LCP/croissant-baker/commit/ac1076d3f8ab2d7815aee3a06fc44176da902b69))
