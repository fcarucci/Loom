# R3-b parity rows (refactor loop 3: Siril dialog, colour, frame and resample privates)

Behaviour-preserving: frames stay bit-identical (tests/test_render_characterization.py, unchanged hashes), the HDR
colour transform keeps its recorded bytes (tests/test_colour.py::test_hdr_apply_bytes_are_characterized, recorded
on fly/final@23c0860), every oracle replay is unchanged, and the dialog's widgets, order, starting values and result
dict are unchanged (tests/test_siril_script.py: the fake-tk and the real-Tk characterization).
Status: P = ported (same maths, moved or re-signed), V = vectorised port, R = replaced by a Python equivalent,
D = not ported.

| JS | Python | Status | Test |
|---|---|---|---|
| `Fly.hdrTransform` one() (R3-b: `_hdr_rgb(rgb, excess)`, the RGB and excess planes as two tuples) | `colour.OutputTransform._hdr_rgb` | P | test_colour.py::test_hdr_apply_bytes_are_characterized; test_output_transform_matches_js |
| `Fly.applyGray` HDR branch (R3-b: `_hdr_rgb((k, k, k), (x, x, x))`) | `colour.OutputTransform._apply_gray` | P | test_colour.py::test_hdr_apply_bytes_are_characterized (hlg-gray-excess, pq-srgb-gray-excess) |
| `Render.resampleRows` / `Render.resample` (R3-b: `_apply_taps(plane, taps, axis)`; out_n = len(start) - 1) | `resample._apply_taps`, `_Taps` | V | test_resample.py::test_resample_rows_matches_js, ::test_resample_matches_js |
| `Render.axisWeights` tap packing (R3-b: `_pack_axis(counts, iv, wv, src_n)`, out_n = counts.size) | `resample._pack_axis` | V | test_resample.py::test_axis_weights_match_js |
| `Render.areaMean` taps (R3-b: `_area_axis` returns `_Taps`) | `resample.area_mean`, `_area_axis` | V | test_resample.py::test_area_mean_matches_js |
| `Render.shrinkPatch` taps (R3-b: `_apply_taps` on the ShrinkAxis) | `resample.shrink_patch` | V | test_resample.py::test_shrink_patch_matches_js |
| `Render.frame` (R3-b: unchanged signature; see the note in the DONE line) | `frame.frame` | P | test_frame.py oracle; test_render_characterization.py |
| `Render.frameImage` (R3-b: unchanged) | `frame.frame_image` | P | test_frame.py; test_render_characterization.py |
| Siril dialog form (no JS: the Siril script's own tkinter form; R3-b: `_form_fields` table of `_Field` rows, `_entry(row, r)`, `_combo(row, r)`) | `Loom/loom_fly_through.py::LoomFlyDialog` | D (not a JS function; the Siril side is not in Loom's JS) | test_siril_script.py::test_dialog_layout_and_values_are_characterized_without_a_display; ::test_dialog_layout_and_values_under_a_real_display |
