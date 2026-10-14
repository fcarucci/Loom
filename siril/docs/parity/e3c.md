# E3c parity: star-model fitting and deblending (loomsiril/starfit.py)

JS source: Loom v0.4.1, `script/lib/Sky.js` 683-731 (env part of `Sky.sprites`) and 771-1130.
Oracle: `tools/oracle/starfit.js` records `tests/oracle/starfit.json` from the real JS on one
synthetic 64 x 48, 3-channel stars layer (five Gaussian stars, the brightest with spikes, one
faint star the detector misses). Tests: `tests/test_starfit.py`.

Status: P = ported, V = vectorised port (same maths, numpy), R = replaced by a Python equivalent,
D = not ported.

| JS | Python | Status | Test |
|---|---|---|---|
| Sky.sprites env part (694-701) | `make_env` (+ `sprite_px` for the `Math.max(1, Math.round(fwhm \|\| FWHM_DEFAULT))` step) | P | test_make_env_matches_js, test_sprite_px_is_js_round_with_default |
| Sky.SPIKE_SIDE | `SPIKE_SIDE` | P | test_constants_match_js |
| Sky.SPIKE_SIGMA | `SPIKE_SIGMA` | P | test_constants_match_js |
| Sky.SPIKE_STARS | `SPIKE_STARS` | P | test_constants_match_js |
| Sky.SPIKE_LEVER | `SPIKE_LEVER` | P | test_constants_match_js |
| Sky.spikeExcess | `spike_excess` (scalar) and `_excess_grid` (V, grid of angles x radii; R3-c: `(env, d, angs, reads)` with `_Reads(rs, side)`) | P / V | test_spike_excess_oracle, test_spike_directions_oracle |
| Sky.spikeDirections | `spike_directions` | V (360-bin profile and the 0.05-degree refinement vectorised) | test_spike_directions_oracle |
| Sky.spikeSide | `spike_side` | P | test_spike_side_oracle |
| Sky.spikeWidth | `spike_width` | V (lateral samples vectorised) | test_spike_width_oracle |
| Sky.spikeFalloff | `spike_falloff` | V (ray per angle and star, fancy-index accumulate) | test_spike_falloff_oracle |
| Sky.smoothFalloff | `smooth_falloff` | P | test_smooth_falloff_oracle, test_smooth_falloff_never_rises_outward |
| Sky.starModel | `star_model` (sets `d.halo`; caller stores `d.model`) | P | test_star_model_oracle |
| Sky.starProfile | `star_profile` | P | test_star_model_oracle (via prof/rmax) |
| Sky.starSpikes | `star_spikes` (R2-d: one `_excess_trig` grid over all directions, V) | V | test_star_model_oracle, test_star_without_spikes_has_empty_spike_list |
| Sky.starColour | `star_colour` | V (core pixels by mask) | test_star_model_oracle (col) |
| Sky.modelAt | `model_at` (scalar) and `model_window` (V) | P / V | test_model_at_oracle, test_window_versions_equal_scalar |
| Sky.radialAt | `radial_at` (scalar) and `radial_window` (V) | P / V | test_model_at_oracle, test_window_versions_equal_scalar |
| Sky.spikeAt | `spike_at` (scalar) and `spike_window` (V) | P / V | test_model_at_oracle, test_window_versions_equal_scalar |
| Sky.modelSums | `model_sums` (via `_accumulate`, V) | V | test_model_sums_oracle, test_model_sums_of_one_star_is_its_window_times_colour |
| Sky.MISSED_SIGMA | `MISSED_SIGMA` | P | test_constants_match_js |
| Sky.MISSED_SHARE | `MISSED_SHARE` | P | test_constants_match_js |
| Sky.unexplainedSources | `unexplained_sources` (scalar scan of the sprite windows) | P | test_unexplained_sources_oracle |
| Sky.excessPeak | `excess_peak` | P | test_excess_peak_recorded |
| Sky.pointModel | `point_model` (core 0.0, spike_sigma None) | P | test_point_model_oracle, test_point_model_has_core_zero |
| Sky.addToSums | `add_to_sums` (in place, via `_accumulate`) | V | test_add_to_sums_oracle |
| Sky.deblendSprite | `deblend_sprite` (window arrays, residual updated in place) | V | test_deblend_sprite_oracle, test_pixels_plus_residual_equal_the_stars_layer, test_sprite_mask_and_spike_angles_shape |
| Fly.deblend (vectorised twin) | `_deblend_arrays` | V | test_deblend_arrays_equal_scalar, test_deblend_never_negative_where_light_is_positive |
| Fly.attachToCores (in the sequence) | `starmodel.attach_to_cores` (E3b), exercised by the parts case | P | test_parts_branch_replays_the_js_sequence |

Notes
- Sums over pixels or samples are numpy (pairwise) sums; the JS adds in order. The differences are
  in the last bits, far below the 1e-6 oracle tolerance on float32-derived values. Spike angle lists
  come out identical (the 0.05-degree refinement picks the same bins on the recorded field).
- JS Float32Array stores (sums, residual, pixels) are float64 here until the final cast; the
  residual subtraction rounds to float32 on store as the JS does.
- `Sky.sprites` itself (the driver: fragment filter, halo, assignSprites, second pass, the deblend
  loop) is E3d. The moving-star core case (`Fly.attachToCores`, the `parts` branch of
  `deblend_sprite`) is covered by the `parts` case of the oracle, replayed by
  test_parts_branch_replays_the_js_sequence.
- The recorder stands in a plain record for the PJSR `Rect` that `Sky.intensity`/`Render.channels`
  construct (the node shim has none); the fake image ignores the rect, so the values are unaffected.
