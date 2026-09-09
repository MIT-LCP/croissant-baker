# OME-TIFF regression fixtures

Sample OME-TIFF files © the OME Consortium, licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The source license is included as `COPYING`.

Downloaded on 2026-09-09 from the [OME sample collection](https://docs.openmicroscopy.org/ome-model/6.2.2/ome-tiff/data.html). The Bio-Formats artificial images have dimensions X=439, Y=167, Z=1, C=3, T=1 and pixel type `int8`. They exercise both classic TIFF and BigTIFF. The companion example is an 18 × 24 TIFF with a BinaryOnly header pointing to `multifile.companion.ome`; the companion is deliberately absent to verify that extraction does not open it.

| Stored fixture | Original source | SHA-256 of original TIFF bytes |
|---|---|---|
| `multi-channel.ome.tif.gz` | [multi-channel.ome.tif](https://downloads.openmicroscopy.org/images/OME-TIFF/2016-06/bioformats-artificial/multi-channel.ome.tif) | `ff98dcffeebc6ea7011ba3ece20b7f8841bed57949566eb2460e7da389be9fd3` |
| `multi-channel.ome.btf.gz` | [multi-channel.ome.btf](https://downloads.openmicroscopy.org/images/OME-TIFF/2016-06/bioformats-artificial/multi-channel.ome.btf) | `d3ac0550375f19ccf35e613870eee65d37daa18454c23d7d00ef40e18496021a` |
| `multifile-Z1.ome.tiff` | [multifile-Z1.ome.tiff](https://downloads.openmicroscopy.org/images/OME-TIFF/2016-06/companion/multifile-Z1.ome.tiff) | `1ed1bf3d8ed588f544636622c6615518bcb1825ad7cbb979f2b882bd39b19fa2` |

The two multi-channel files were gzip-compressed with `gzip.compress(original_bytes, mtime=0)` to keep the fixtures small. Decompression reproduces the source bytes exactly; neither pixels nor metadata were edited. The BinaryOnly TIFF is stored unchanged. Source checksums refer to the decompressed bytes and do not depend on the gzip implementation.

These files are exercised by `tests/test_ome_filesets.py`. Run with:

```sh
uv run pytest tests/test_ome_filesets.py
```
