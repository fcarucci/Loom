# Parity rows: E2b (Gaia, WCS, FITS, TIFF)

Source: Loom v0.4.1 `script/lib/Fly.js` 2039-2063, `script/lib/Sky.js` 78, 136-204, 461-517, 1232-1250, 1356-1363,
`script/lib/Render.js` 1326-1350. Columns as in docs/PARITY.md. Status: P ported, V vectorised, R replaced by a Python
equivalent, D not ported (reason given).

| JS | Python | Status | Test |
|---|---|---|---|
| Fly.GAIA_ONLINE_URL | loomsiril.gaia.GAIA_ONLINE_URL | P | tests/test_gaia.py::test_constants_match_js |
| Fly.GAIA_ONLINE_COLUMNS | loomsiril.gaia.GAIA_ONLINE_COLUMNS (tuple) | P | tests/test_gaia.py::test_constants_match_js |
| Fly.GAIA_ONLINE_MAX_ROWS | loomsiril.gaia.GAIA_ONLINE_MAX_ROWS | P | tests/test_gaia.py::test_constants_match_js |
| Fly.gaiaOnlineUrl | loomsiril.gaia.online_url (JS number text via jsmath.number_str; encodeURIComponent via quote) | P | tests/test_gaia.py::test_online_url_matches_js |
| Fly.parseGaiaCsv | loomsiril.gaia.parse_csv (None for a non-table; blank NaN; wrong field count skipped) | P | tests/test_gaia.py::test_parse_csv_matches_js, test_parse_csv_rules |
| Sky.GAIA_ONLINE_TIMEOUT (60 s) | loomsiril.gaia.GAIA_ONLINE_TIMEOUT (seconds; urlopen connect timeout) | P | tests/test_gaia.py::test_constants_match_js |
| Sky.GAIA_ONLINE_STALL (30000 ms) | loomsiril.gaia.GAIA_ONLINE_STALL = 30.0 s | P | tests/test_gaia.py::test_constants_match_js |
| Sky.GAIA_ONLINE_FIRST_BYTE (120000 ms) | loomsiril.gaia.GAIA_ONLINE_FIRST_BYTE = 120.0 s | P | tests/test_gaia.py::test_constants_match_js |
| Sky.GAIA_ONLINE_TOTAL (600000 ms) | loomsiril.gaia.GAIA_ONLINE_TOTAL = 600.0 s | P | tests/test_gaia.py::test_constants_match_js |
| Sky.stallWatch, Sky.transferWatch | loomsiril.gaia.fetch_text (stall, first byte and total checks in one loop; read1 per recv since E2c) | R | tests/test_gaia.py::test_fetch_text_* (fake clock and response; no socket) |
| Sky.fetchText | loomsiril.gaia.fetch_text (urllib; GaiaError instead of null) | R | tests/test_gaia.py::test_fetch_text_* |
| Sky.queryOnline | loomsiril.gaia.query_online (injected fetch_text; GaiaError for a non-table) | P | tests/test_gaia.py::test_query_online_* |
| Sky.queryOnline origin tag ("online") | not carried: sources() returns a plain list | D | the list has no attribute slot; provenance is not needed by the pipeline |
| Sky.querySources cache/online part (the local-catalogue search is not ported here) | loomsiril.gaia.sources (source "cache", "online", "tsv"; disk cache) | R | tests/test_gaia.py::test_sources_* |
| (no JS disk cache) | loomsiril.gaia.cache_key (radius rounded up to 1e-3 since E2c), default_cache_dir, _write_atomic (.part + os.replace) | R | tests/test_gaia.py::test_sources_cache_round_trip, test_cache_key_is_stable_and_documented, test_cache_radius_rounds_up_and_the_query_uses_it |
| (fixture reader, Python only) | loomsiril.gaia.read_tsv | R | tests/test_gaia.py::test_read_tsv_fixture, test_read_tsv_blank_is_nan |
| Sky.keywordNumber | loomsiril.wcs.keyword_number (header dict, not a window; parseFloat prefix) | R | tests/test_wcs.py::test_keyword_number_strips_quotes_like_js |
| Sky.keywordWcs | loomsiril.wcs.keyword_wcs | R | tests/test_wcs.py::test_keyword_wcs_from_header_and_missing_key |
| Sky.projector (non-solved branch) | loomsiril.wcs.projector (core.tan_project) | R | tests/test_wcs.py::test_projector_puts_crval_on_crpix, test_tan_project_of_scaled_wcs_matches_js |
| Sky.unprojector (non-solved branch) | loomsiril.wcs.unprojector (core.tan_unproject) | R | tests/test_wcs.py::test_tan_unproject_of_scaled_wcs_matches_js |
| Sky.field | loomsiril.wcs.field -> {centre, radius_deg} | R | tests/test_wcs.py::test_field_radius_reaches_every_corner |
| Sky.fieldCorners | loomsiril.wcs.field_corners | R | tests/test_wcs.py::test_field_corners_are_the_pixel_corners |
| Sky.solvedScale | loomsiril.wcs.solved_scale (arcsec/px across the width) | R | tests/test_wcs.py::test_solved_scale_of_synth_header, test_solved_scale_matches_formula |
| Sky.scaledWcs | loomsiril.wcs.scaled_wcs | P | tests/test_wcs.py::test_scaled_wcs_matches_js |
| Fly.tanProject / tanUnproject of a scaled wcs | loomsiril.core.tan_project / tan_unproject (E0) | P | tests/test_wcs.py::test_tan_project_of_scaled_wcs_matches_js, test_tan_unproject_of_scaled_wcs_matches_js |
| Sky.projector / unprojector (solved window branch: celestialToImage) | not ported: a PixInsight window is the only source | D | Siril gives a wcs dict, never a window; the solved branch has no meaning in the Python core |
| Sky.iccBytes, Sky.copyWindow (RGBA drop) | loomsiril.io_tiff.read_image (RGBA drops alpha) | R | tests/test_io.py::test_read_rgba_drops_alpha |
| Render.writeTiff | loomsiril.io_tiff.write_tiff16 (16-bit, ICC tag 34675, .part then os.replace) | R | tests/test_io.py::test_tiff16_round_trip_with_icc, test_tiff16_part_file_gone_after_write |
| (no JS FITS reader; Siril bridge input) | loomsiril.io_fits.read_fits / write_fits (BITPIX 8/16/32/-32/-64, BZERO/BSCALE; top-down rows since E2c: see docs/parity/e2c.md) | R | tests/test_io.py (FITS section) |
| (wcs from the CLI solve JSON, Python only) | loomsiril.wcs.wcs_from_solution (validated) | R | tests/test_wcs.py::test_wcs_from_solution_* |
