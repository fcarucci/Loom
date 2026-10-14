# Parity: E9b (small clean-ups from the E2c, E7 and E8 reviews)

E9b ports no new JS function. It renames one Python helper, adds tests that pin two behaviours, and fixes
docstrings, a comment and a shell expansion. The rows below record the JS behaviour the touched code follows.

| JS | Python | Status | Test |
|---|---|---|---|
| Sky.fetchText / Sky.transferWatch (chunked GET, stall timer measured between reads, Sky.js) | `gaia.fetch_text` picks `read1` over `read` (one recv per loop turn) | P | `tests/test_gaia.py::test_fetch_text_slow_trickle_with_read1_is_not_a_stall` (fake `read` raises AssertionError) |
| Fallback when no read1 (no JS counterpart; the JS reads one stream) | `gaia.fetch_text` falls back to `read` | R | `tests/test_gaia.py::test_fetch_text_read_fallback_still_works` |
| Header WCS conversion for Siril's PC/CDELT/CROTA2 form (no JS counterpart; Siril writes these keys) | `io_fits.materialise_cd` (renamed from `_materialise_cd`, now public); CROTA2 form pinned to the FITS/AIPS rotation | R | `tests/test_io.py::test_materialise_cd_from_crota2_is_the_fits_rotation`, `tests/test_io.py::test_fits_read_converts_crota2_cdelt_form` |
| Siril pixel convention used by the test stub (`pix2radec` / `radec2pix`; no JS counterpart) | tests/sirilpy_stub docstrings now state the pixel-edge convention, y from the top; code unchanged | R | `tests/test_siril_bridge.py` (existing stub-based tests) |
| Empty build-arg array in docker/run-tests.sh (shell only, no JS counterpart) | `${BUILD_ARGS[@]+"${BUILD_ARGS[@]}"}` works under `set -u` on bash 3.2 | R | `bash -n docker/run-tests.sh`, and a run with a stub `docker` on PATH (no build env set and one set) |
