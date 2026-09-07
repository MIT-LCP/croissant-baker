"""Croissant metadata generation for scientific datasets.

A library owns no terminal. The package logger carries a ``NullHandler`` so
importing ``croissant_baker`` never writes anything anywhere: without one,
``logging.lastResort`` sends every WARNING to stderr of whatever process
imported us. An application that wants these records configures logging and
gets them; one that does not, does not.

What a bake found is not carried on the log. It is
:class:`croissant_baker.report.ScanReport` in process, and the ``--report``
JSON across a process boundary — both typed, both complete.
"""

import logging

logging.getLogger(__name__).addHandler(logging.NullHandler())
