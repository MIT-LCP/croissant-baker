# Whole-slide demo (synthetic pathology slides)

A small fixture used by `tests/test_end_to_end.py`. One slide per scanner
vendor beside two DICOM slide instances, so one bake exercises the whole-slide
handler and the DICOM handler's whole-slide awareness at the same time.

```
wsi_demo/
├── aperio.svs          Aperio: tag 270 header, AppMag and MPP, label and macro
├── hamamatsu.ndpi      Hamamatsu: private tags 65420 and 65421, Make = Hamamatsu
├── leica.scn           Leica: an SCN XML document ending in </scn>
├── ventana.bif         Ventana: an iScan XMP packet in tag 700, Ventana software
├── akoya.qptiff        Akoya: PerkinElmer-QPI software and QPI image description
├── dicom/slide.dcm     VL Whole Slide Microscopy Image, VOLUME flavor
├── dicom/label.dcm     the same glass slide's barcode label, LABEL flavor
└── README.md           this file, which no handler claims
```

Each vendor file carries the one signature `tifffile` identifies that make by,
which is what `croissant_baker.handlers.wsi` dispatches on. Both DICOM
instances carry the whole-slide SOP class, so they take the slide branch of the
DICOM handler rather than the cross-section one, and their two flavors are what
make the flavor list in the DICOM description more than one word long.

## Source

**Synthetic**, and zero-pixel: every image plane is a small array of zeros, so
a deflated slide costs a few kilobytes rather than the gigabytes a real
pyramid does. Nothing here came off a scanner, and no file carries patient
data, a patient identifier or an acquisition date: each DICOM instance states
a synthetic container barcode, synthetic study and series UIDs, and nothing
else that a real instance would carry about a person.

The five vendor slides are written by the builders in `tests/helpers.py`, the
same ones `tests/test_wsi.py` and `tests/test_wsi_handler.py` read, so the
fixture and the unit tests describe the same synthetic scanners. The DICOM
instances are built in `tests/wsi_fixtures.py` itself, with fixed UIDs rather
than generated ones, because a fixture whose identifiers changed on every run
would rewrite the golden document with them.

## Regenerating

The writer is `tests/wsi_fixtures.py`, beside `tests/hdf5_fixtures.py` and out
of the dataset itself: a baked dataset holds data and this README, and a
script inside it would be one more file for every bake to scan.

```python
from pathlib import Path
from tests import wsi_fixtures as fx

fx.write_demo(Path("tests/data/input/wsi_demo"))
```

The output is byte-identical between runs, so regenerating an unchanged
fixture leaves the working tree clean.

## The golden

`tests/data/output/wsi_demo_croissant.jsonld` is the document this dataset
bakes to, and `test_wsi_demo_generation` reads it rather than overwriting it.
A deliberate change to what either handler emits therefore shows up as a diff
in that file. To regenerate, point the CLI at this directory with the same
flags the test uses:

```
croissant-baker -i tests/data/input/wsi_demo \
  -o tests/data/output/wsi_demo_croissant.jsonld \
  --name "Whole-slide demo (synthetic pathology slides)" \
  --description "One synthetic slide per scanner vendor, and one DICOM whole-slide microscopy instance" \
  --url https://example.org/wsi-demo \
  --license https://creativecommons.org/licenses/by/4.0/ \
  --dataset-version 1.0.0 --date-published 2026-01-01 \
  --creator "croissant-baker test suite"
```
