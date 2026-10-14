# R1-a: one home for JS semantics (loomsiril/jsmath.py)

Status: P = ported (same maths, moved), V = vectorised port, R = replaced by a Python equivalent, D = not ported.

| JS | Python | Status | Test |
|---|---|---|---|
| `x / 0` (IEEE result in a division) | `jsmath.js_div` (was geometry._div) | R | test_jsmath.py::test_js_div_gives_ieee_results_for_zero |
| `Math.max(a, b)` (NaN propagates) | `jsmath.js_max` (was geometry._jsmax) | R | test_jsmath.py::test_js_max_and_min_propagate_nan |
| `Math.min(a, b)` (NaN propagates) | `jsmath.js_min` (was geometry._jsmin) | R | test_jsmath.py::test_js_max_and_min_propagate_nan |
| `Math.sqrt` | `jsmath.js_sqrt` (was geometry._sqrt) | R | test_jsmath.py::test_js_sqrt_acos_sin_are_nan_where_js_is |
| `Math.acos` | `jsmath.js_acos` (was geometry._acos) | R | test_jsmath.py::test_js_sqrt_acos_sin_are_nan_where_js_is |
| `Math.sin` | `jsmath.js_sin` (was geometry._sin) | R | test_jsmath.py::test_js_sqrt_acos_sin_are_nan_where_js_is |
| `Math.floor` | `jsmath.js_floor` (was geometry._floor) | R | test_jsmath.py::test_js_floor_and_ceil_keep_non_finite_values |
| `Math.ceil` | `jsmath.js_ceil` (was geometry._ceil) | R | test_jsmath.py::test_js_floor_and_ceil_keep_non_finite_values |
| `Math.round` | `core.js_round`, re-exported by jsmath | P | test_jsmath.py::test_js_round_is_the_core_one_halves_up; test_core.py::test_js_round_against_js |
| `String(number)` / Number#toString | `jsmath.number_str` (was presets.js_num_str and gaia._js_number_str, one copy now; presets.js_num_str re-exported) | P | test_jsmath.py::test_number_str_is_js_string_of_number, ::test_presets_and_gaia_use_the_one_number_text |
| `parseFloat` (prefix; NaN when none) | `jsmath.parse_float` (was presets._js_parse_float) | P (gap: 'Infinity' gives NaN, JS gives Infinity; unchanged) | test_jsmath.py::test_parse_float_reads_the_prefix, ::test_parse_float_without_a_number_is_nan |
| `String(v)` on header values | `jsmath.js_string` (was presets._js_string) | P | test_jsmath.py::test_js_string_of_header_values |
| `JSON.stringify` of a string | `jsmath.json_string` (was presets._json_string) | P | test_jsmath.py::test_json_string_uses_the_short_escapes_and_u_for_controls; test_presets.py::test_js_number_text_matches_json_stringify |
| `Fly.smoothstep` over an array (Render bloom, sprites) | `core.smoothstep_arr` (replaces bloom._smoothstep) | V | test_core.py::test_smoothstep_arr_is_the_scalar_elementwise, ::test_smoothstep_arr_float64_stays_float64; test_bloom.py |
| `Fly.radialSource` over an array (sprites) | `geometry.radial_source_arr` | V | test_jsmath.py::test_radial_source_arr_is_the_scalar_elementwise, ::test_radial_source_arr_nan_seen_floors_to_0_05 (the sprite-copy test went with the copy in R1-c); test_render_characterization.py |
| `Fly.headroomBump` over an array (sprites) | `geometry.headroom_bump_arr` | V | test_jsmath.py::test_headroom_bump_arr_is_the_scalar_elementwise, ::test_headroom_bump_arr_centre_and_far_limits (the sprite-copy test went with the copy in R1-c); test_render_characterization.py |
