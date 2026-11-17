"""Handlers for structural biology file formats.

Each module describes one family and is imported by
:func:`croissant_baker.handlers.registry.builtin_handlers`, so importing the
registry does not pull gemmi in.

gemmi itself is an optional extra, installed with
``pip install "croissant-baker[structural-biology]"``. Without it the modules
that parse through it still load and still claim their files, and refuse each
one with that hint; MRC, MTZ, mdoc, SDF, MOL and MOL2 are read either way.
"""
