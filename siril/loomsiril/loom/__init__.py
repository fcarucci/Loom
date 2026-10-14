"""Loom for Siril: the Loom processing pipeline (integrated masters to finished plates).

A Python port of Loom's main script (PixInsight) for Siril 1.4. Three layers:

* pure modules (numpy/scipy only): Loom's maths, tables and decisions, pinned against values recorded from Loom's
  JavaScript (``tests/oracle/loom_*.json``);
* adapters: ``siril_ops`` (every Siril command the port uses, with a recording fake) and ``tools`` (one adapter per
  external command-line program);
* orchestration: the stages and ``pipeline.run``.

Importing this package has no side effects and never imports sirilpy or tkinter; only the Siril script
``Loom/loom_preprocess.py`` and the dialog module do.
"""

from __future__ import annotations

__all__: list[str] = []
