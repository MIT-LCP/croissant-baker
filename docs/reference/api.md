# API Reference

Use `croissant-baker` as a Python library to generate Croissant metadata
programmatically — without the CLI.

## MetadataGenerator

::: croissant_baker.metadata_generator.MetadataGenerator
    options:
      members:
        - __init__
        - generate_metadata
        - save_metadata
        - scan_report

## Scan coverage

Every file the scan finds gets one entry carrying what became of it. The report
survives the "no supported files" error, so a bake that described nothing can
still explain itself.

::: croissant_baker.scan.ScanReport
    options:
      members:
        - total
        - described
        - linked
        - referenced
        - undescribed
        - counts
        - summary_lines
        - to_dict

::: croissant_baker.entries.Outcome

::: croissant_baker.entries.Reason

## File Discovery

::: croissant_baker.files.discover_files

## Handler Interface

A handler answers three questions about one format, none of them involving
compression: the pipeline resolves that first and hands over a
[`FileSource`](#croissant_baker.sources.FileSource).

`can_handle(path)` and `extract_metadata(path)` are the previous names for the
first two. They still work — in both directions — and warn once per handler
class. See [DEVELOPMENT.md](https://github.com/MIT-LCP/croissant-baker/blob/main/DEVELOPMENT.md)
for how to write a handler.

::: croissant_baker.handlers.base_handler.FileTypeHandler
    options:
      members:
        - claims
        - extract
        - build_croissant
        - can_handle
        - extract_metadata

::: croissant_baker.handlers.base_handler.InputKind

## Sources

::: croissant_baker.sources.FileSource
    options:
      members:
        - name
        - relative_path
        - suffix
        - size
        - sha256
        - open
        - open_text
        - peek

::: croissant_baker.sources.PathSource

## Handler registry

::: croissant_baker.handlers.registry.HandlerRegistry
    options:
      members:
        - __init__
        - register
        - handlers
        - select

::: croissant_baker.handlers.registry.builtin_handlers

::: croissant_baker.handlers.registry.HandlerSelection

## Compression

Adding a compression is one call: dispatch strips the new suffix, streams
decompress through it, `encodingFormat` gains its media type, and FileSet globs
expand to cover it.

::: croissant_baker.compression.Compression

::: croissant_baker.compression.register_compression

::: croissant_baker.compression.compressions
