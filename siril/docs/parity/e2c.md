# Parity rows: E2c (row order, Gaia fetch fixes)

Source: Loom v0.4.1 `script/lib/Sky.js` 136-204 (fetchText, transferWatch), `script/lib/Render.js` (row order of
images: row 0 = top, as all of Loom). Columns as in docs/PARITY.md. Status: P ported, V vectorised, R replaced by a
Python equivalent, D not ported (reason given).

| JS | Python | Status | Test |
|---|---|---|---|
| (no JS flip: Loom is top-down everywhere; FITS and Siril are the only bottom-up sources) | loomsiril.io_fits.read_fits flips planes to top-down (`plane[::-1]`) and converts the header TAN keywords | R | tests/test_io.py::test_fits_read_puts_fits_row_one_at_the_bottom_and_maps_the_wcs, test_fits_row_order_is_top_down_after_read |
| (no JS flip) | loomsiril.io_fits.write_fits writes planes and header bottom-up (drops LOOMROWS) | R | tests/test_io.py::test_fits_written_file_is_bottom_up_on_disk, test_fits_write_then_read_is_identity |
| (no JS: PC/CDELT/CROTA2 header) | loomsiril.io_fits.materialise_cd (CDi_j = CDELTi * PCi_j; CROTA2 as the CD rotation) before the flip | R | tests/test_io.py::test_fits_read_converts_pc_cdelt_form |
| (no JS) | loomsiril.wcs.flip_rows (crpix2' = h + 1 - crpix2; cd[1], cd[3] negated; involution) | R | tests/test_wcs.py::test_flip_rows_is_an_involution, test_flip_rows_flips_the_sky_mapping_for_an_asymmetric_wcs, test_flip_rows_negating_cd2_1_instead_of_cd1_2_is_wrong |
| (FITS and TIFF alignment, Python only) | io_fits.read_fits + io_tiff.write_tiff16 / read_image | R | tests/test_io.py::test_fits_and_tiff_planes_are_pixel_aligned |
| Sky.fetchText / Sky.transferWatch (read per recv) | loomsiril.gaia.fetch_text: `resp.read1(_CHUNK)` when present, so each loop turn is one recv and the stall timer measures gaps; `read` is the fallback | R | tests/test_gaia.py::test_fetch_text_slow_trickle_with_read1_is_not_a_stall, test_fetch_text_read_fallback_still_works |
| Sky.fetchText error paths (HTTPException not in the JS: JS catches any error) | loomsiril.gaia.fetch_text maps http.client.HTTPException (IncompleteRead, bad status line) to GaiaError on urlopen and read | R | tests/test_gaia.py::test_fetch_text_incomplete_read_is_a_gaia_error, test_fetch_text_bad_status_line_on_urlopen_is_a_gaia_error |
| (cache key, Python only) | loomsiril.gaia.cache_key / sources: radius rounded UP to 1e-3 deg and the query uses the same radius | R | tests/test_gaia.py::test_cache_radius_rounds_up_and_the_query_uses_it, test_cache_key_is_stable_and_documented |
