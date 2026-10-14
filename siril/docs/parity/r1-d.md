# R1-d: star code clean-up (refactor loop 1)

Behaviour-preserving. Oracle replays unchanged; no tolerance changed. JS names unchanged; Python names
changed only where a parameter was dropped (no renames).

| JS | Python | Status | Test |
|---|---|---|---|
| Math.max / Math.min (JS helpers in Sky.js, Fly.js) | `jsmath.js_max`, `jsmath.js_min` (were `_js_max`, `_js_min` copies in starmodel and starfit) | P | tests/test_jsmath.py::test_js_max_and_min_propagate_nan |
| `v \|\| fallback`, falsiness of a number | `jsmath.js_or`, `jsmath.is_falsy` (were `_js_or`, `_is_js_falsy` copies) | P | tests/test_jsmath.py::test_is_falsy_and_js_or_take_the_fallback |
| `v \|\| 0` for a flux (assign) | `jsmath.js_or(v, 0.0)` (was `assign._js_or_zero`) | P | tests/test_assign.py::test_drop_inside_brighter_matches_js |
| Sky.ringStats(buf, w, h, cx, cy, r, labels, self) | `starmodel.ring_stats(buf, cx, cy, r, labels, self_)`: w, h from `buf.shape` | P | tests/test_starmodel.py::test_ring_stats_oracle |
| Sky.reader(buf, w, h) | `starmodel.reader(buf)`: w, h from `buf.shape` | P | tests/test_starmodel.py::test_reader_oracle |
| Sky.bilinear(buf, w, h, x, y) | `starmodel.bilinear(buf, x, y)`: w, h from `buf.shape`; neighbours in `_in_image_neighbours` | P | tests/test_starmodel.py::test_bilinear_oracle |
| Sky.bilinear over arrays (no JS name) | `starfit._bilinear_arr(buf, x, y)`: w, h from `buf.shape` | V | tests/test_starfit.py (spike oracles) |
| Sky.spikeExcess over a grid (no JS name) | `starfit._excess_grid(env, d, angs, rs, side)` (R2-d: was `(buf, cx, cy, ...)`; over `_excess_trig`) | V | tests/test_starfit.py (spike oracles) |
| Sky.spikeExcess(buf, w, h, cx, cy, ang, r, side) | `starfit.spike_excess(buf, cx, cy, ang, r, side)` | P | tests/test_starfit.py::test_spike_excess_oracle |
| Sky.brightMeasure(buf, w, h, x, y) | `detect.bright_measure(buf, x, y)`: w, h from `buf.shape` | P | tests/test_detect.py::test_bright_measure_matches_js |
| Fly.dropBorrowedSpikes | `starmodel.drop_borrowed_spikes` with `_borrowed_angles`, `_spikes_covering`, `_drop_spikes`, `_parallel` | P | tests/test_starmodel.py::test_drop_borrowed_spikes_oracle, ::test_drop_borrowed_spikes_all_borrowed_leaves_no_spike_and_the_rmax_support |
| Fly.isFragment, last test (brighter core) | `starmodel._brighter_core_by_grid` with `_grid_candidates`; 3x3 max `_window_max` | P | tests/test_starmodel.py::test_fragment_filter_matches_the_old_quadratic_filter |
| Sky.unexplainedSources | `starfit.unexplained_sources` with `_window_pixels`, `_MissedScan.is_point_peak` (R2-d: was `_is_point_peak`) | P | tests/test_starfit.py::test_unexplained_sources_oracle, ::test_unexplained_sources_finds_a_pixel_once_when_two_sprites_share_it |
| Fly.spriteOwnership | `assign.sprite_ownership` (unchanged; w, h kept: not a buffer-only function) | P | tests/test_assign.py::test_sprite_ownership_matches_js |
| Fly.dropInsideBrighter | `assign.drop_inside_brighter` with `_brighter_reaches` | P | tests/test_assign.py::test_drop_inside_brighter_matches_js, ::test_drop_inside_brighter_asks_for_the_halo_once_and_keeps_the_far_one |
| Sky.addToSums | `starfit.add_to_sums` (kept as the JS name; pass-through) | P | tests/test_starfit.py::test_add_to_sums_oracle |
