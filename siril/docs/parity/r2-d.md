# Parity rows, loop 2 task R2-d (star code)

Status: P = ported, V = vectorised port, R = replaced by a Python equivalent, D = not ported (reason).

| JS | Python | Status | Test |
|---|---|---|---|
| Fly.findCluster | `parallax.find_cluster`; per-seed counts in `parallax._ClusterFinder` (methods `hits`, `within`, `counts`, `near`); `_quantile` for the quartiles | V | test_parallax.py::test_find_cluster_matches_js; test_constructed_cluster_is_found_at_its_distance; test_uniform_field_has_no_cluster |
| Fly.refineCluster | `parallax.refine_cluster(inside, seed, near)` (near factory = `_ClusterFinder.near`) | P | test_parallax.py::test_refine_cluster_matches_js |
| Sky.sprites (halo callback, the second pass, the deblend) | `starlayer.sprites` in steps: `_detections`, `_Modeller.halo`, `_missed_stars`, `_deblend_all` | P | test_starlayer.py oracle replays; test_stage_callback_gets_js_texts_in_order |
| Fly.assignSprites | `assign.assign_sprites` in steps: `_one_to_one`, `_Footprint`, `_candidates(pairs, _Matching, foot)` (R3-c) | P | test_assign.py::test_assign_sprites_matches_js |
| Sky.unexplainedSources | `starfit.unexplained_sources` over `starfit._MissedScan` (`sum_i`, `excess`, `is_point_peak`, `scan`) | P | test_starfit.py::test_unexplained_sources_oracle; test_unexplained_sources_window_bound_is_one_pixel_inside_the_image |
| Sky.ringStats | `starmodel.ring_stats` over `starmodel._ring_pixels` | P | test_starmodel.py::test_ring_stats_oracle |
| Sky.spikeExcess (scalar) | `starfit.spike_excess` (unchanged; JS signature kept for the oracle replay) | P | test_starfit.py::test_spike_excess_oracle |
| Sky.spikeExcess over a grid (no JS name) | `starfit._excess_trig(env, d, (c, s), reads)` / `_excess_grid(env, d, angs, reads)` (R3-c: `_Reads(rs, side)`) | V | test_starfit.py::test_excess_trig_grid_equals_the_scalar_spike_excess_exactly |
| Sky.starSpikes | `starfit.star_spikes`: all directions in one grid, same floats as the scalar reads | V | test_starfit.py::test_star_spikes_equals_the_scalar_spike_excess_exactly; test_star_model_oracle |
| Fly.dropBorrowedSpikes, parallel test (2 degrees) | `starmodel._parallel` | P | test_starmodel.py::test_parallel_two_degree_tolerance_on_both_sides; ::test_parallel_two_degree_tolerance_across_the_wrap |
| `v \|\| fallback` for a number (flux, spike sigma, MAD) | `jsmath.js_or` (only numbers or None reach it; NaN, 0, None tested for Python and numpy types) | R | test_jsmath.py::test_is_falsy_and_js_or_take_the_fallback; ::test_js_or_numpy_scalars_take_the_fallback |
| Sky.starModel | `starfit.star_model` (unchanged) | P | test_star_model_oracle |

Not changed: `detect.py` (is_fragment, fragment_filter untouched), `starfit.spike_window` / `point_model` (signatures already take a model or the env).
