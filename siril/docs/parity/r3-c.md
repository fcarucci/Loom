# R3-c parity: star code (starlayer, starmodel, assign, starfit)

Loop 3 changes only the shape of private helpers and adds one characterization test. Every JS
name below keeps its Python function and its oracle replay; the JS-shaped public signatures
(`starlayer.sprites`, `starmodel.ring_stats`, `starfit.spike_excess`, `assign.assign_sprites`,
`assign.sprite_ownership`, `starmodel.fit_spike_pair`, `starfit.point_model`) are unchanged.

Status: P = ported, V = vectorised port, R = replaced by a Python equivalent, D = not ported.

| JS | Python | Status | Test |
|---|---|---|---|
| Sky.sprites driver (683-731) | `starlayer.sprites` (6 params, public, called by `pipeline.build_scene`: signature kept) | P | test_residual_plus_sprites_equals_stars_layer, test_oracle_counts_and_stages_exact |
| Sky.sprites detections step (Sky.detections, Fly.addMissingBright, fragment filter) | `starlayer._detections(stars, px, neighbours, detections)` (computes `sm.intensity` itself; was 5 params) | P | test_detections_argument_replaces_the_detector, test_passed_detections_are_not_changed |
| Sky.ringStats | `starmodel.ring_stats` (6 params, JS-shaped, kept) over `starmodel._ring_pixels(shape, _Ring, labels, self_)` | P | test_ring_stats_oracle, test_ring_stats_skips_other_stars_and_is_none_off_image |
| Sky.spikeExcess (scalar) | `starfit.spike_excess` (6 params, JS-shaped, kept) | P | test_spike_excess_oracle |
| Sky.spikeExcess (grid, no JS name) | `starfit._excess_trig(env, d, (c, s), _Reads(rs, side))`, `_excess_grid(env, d, angs, _Reads)` | V | test_excess_trig_grid_equals_the_scalar_spike_excess_exactly |
| Sky.starSpikes | `starfit.star_spikes` (one `_excess_trig` grid over the directions) | V | test_star_spikes_equals_the_scalar_spike_excess_exactly, test_star_model_oracle |
| Sky.spikeDirections, Sky.spikeWidth, Sky.spikeFalloff | `spike_directions`, `spike_width`, `spike_falloff` (call `_excess_grid` with `_Reads`) | V | test_spike_directions_oracle, test_spike_width_oracle, test_spike_falloff_oracle |
| Sky.unexplainedSources scan window (x, y in 1..w-2, 1..h-2) | `starfit._window_pixels` | P | test_window_pixels_start_one_pixel_inside_the_image, test_unexplained_sources_window_bound_is_one_pixel_inside_the_image |
| Fly.assignSprites | `assign.assign_sprites` (7 params, JS-shaped, kept) over `_one_to_one`, `_Matching`, `_candidates(pairs, match, foot)`, `_Footprint` | P | test_assign_sprites_matches_js, test_assign_sprites_on_a_synthetic_field |
| Fly.spriteOwnership | `assign.sprite_ownership` (5 params, JS-shaped, kept) | P | test_sprite_ownership_matches_js |
