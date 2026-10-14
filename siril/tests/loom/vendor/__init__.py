"""Vendor-tool tests: they run the real resolver and the real tools on the machine they are run on.

Every test here is marked ``vendor`` and is skipped unless ``LOOMSIRIL_VENDOR=1`` is set (tests/conftest.py).
They read their inputs from environment variables documented in the hand-run checklist, never from the repository,
and they write only under pytest's ``tmp_path``. No vendor output is committed.
"""
