# Parity: E3a (detect + assign)

Source: Loom `script/lib/Sky.js` and `script/lib/Fly.js` (v0.4.1, read-only checkout).
Status: P = ported, V = vectorised port, R = replaced by a Python equivalent, D = not ported.
Oracle: `tests/oracle/assign.json` (recorded by `tools/oracle/assign.js`); the test column names the check.

| JS | Python | Status | Test |
|---|---|---|---|
| Sky.detections (Sky.js 575-583, PixInsight StarDetector) | loomsiril.detect.detect_stars | R | test_detect: 90% recall, no far detection, sorted, rect contains centre, empty image, noise 0.002 |
| (no JS: upper-half noise, R2-0) | loomsiril.detect._upper_half_sigma | R | test_detect: test_upper_half_sigma_recovers_a_clipped_gaussian, test_upper_half_sigma_is_gated_off_*, test_zero_fraction_gate_*, test_faint_stars_* |
| Sky.intensity (590-599) | loomsiril.detect.intensity | V | test_detect: test_intensity_is_the_channel_mean |
| Sky.noiseSigma (602-607) | loomsiril.detect.noise_sigma | P | test_detect: test_noise_sigma_* |
| Sky.brightMeasure (1131-1137) | loomsiril.detect.bright_measure | P | test_detect: test_bright_measure_matches_js (oracle brightMeasure) |
| Sky.eachInWindow (1140-1143) | loomsiril.detect.each_in_window | V | test_detect: test_each_in_window_matches_js (oracle eachInWindow) |
| Fly.MATCH_RADIUS (694-760) | loomsiril.assign.MATCH_RADIUS | P | test_assign: test_constants_match_js |
| Fly.GRID | loomsiril.assign.GRID | P | test_assign: test_constants_match_js |
| Fly.rectsIntersect | loomsiril.assign.rects_intersect | P | test_assign: test_rects_intersect_matches_js |
| Fly.detectionGrid | loomsiril.assign.detection_grid | P | test_assign: test_detection_grid_matches_js |
| Fly.detectionsIn | loomsiril.assign.detections_in | P | test_assign: test_detections_in_matches_js |
| Fly.matchRadius (734-737) | loomsiril.assign.match_radius | P | test_assign: test_match_radius_matches_js, test_match_radius_selftest_literals (selftest 15956-15958) |
| Fly.MATCH_SEARCH | loomsiril.assign.MATCH_SEARCH | P | test_assign: test_constants_match_js |
| Fly.matchPairs (746-760) | loomsiril.assign.match_pairs | P | test_assign: test_match_pairs_matches_js, test_placed_star_pairs_only_within_its_match_radius, test_brighter_gaia_star_claims_a_shared_detection |
| Fly.BLEND_COMPANION_MAG (779-797) | loomsiril.assign.BLEND_COMPANION_MAG | P | test_assign: test_constants_match_js |
| Fly.catalogueBlend (787-797) | loomsiril.assign.catalogue_blend | P | test_assign: test_catalogue_blend_matches_js; selftest 15081-15085 via assignTwoSources |
| Fly.isBlend (811-822) | loomsiril.assign.is_blend | P | test_assign: test_is_blend_matches_js, test_blends_are_counted |
| Fly.oneStarOneSprite (854-866) | loomsiril.assign.one_star_one_sprite | P | test_assign: test_one_star_one_sprite_matches_js, test_one_star_one_sprite_drops_a_star_on_a_brighter_stars_spike |
| Fly.spriteOwnership (868-895) | loomsiril.assign.sprite_ownership | V | test_assign: test_sprite_ownership_matches_js (both halo modes), test_owner_map_gives_each_sprite_its_core_and_reserves_blend_cores |
| Fly.assignSprites (897-927) | loomsiril.assign.assign_sprites | P | test_assign: test_assign_sprites_matches_js (6 cases), test_selftest_* (selftest 14676-14690, 15068-15115, 16060-16076) |
| Fly.SPIKE_WIDTH (1735) | loomsiril.assign.SPIKE_WIDTH | P | test_assign: test_constants_match_js |
| Fly.withinReach (1742-1753) | loomsiril.assign.within_reach | P | test_assign: test_within_reach_matches_js, test_within_reach_along_a_spike_versus_beside_it |
| Fly.BRIGHT_G | loomsiril.assign.BRIGHT_G | P | test_assign: test_constants_match_js |
| Fly.BRIGHT_MISS_RADIUS | loomsiril.assign.BRIGHT_MISS_RADIUS | P | test_assign: test_constants_match_js |
| Fly.addMissingBright (1765-1778) | loomsiril.assign.add_missing_bright | P | test_assign: test_add_missing_bright_matches_js, test_add_missing_bright_adds_exactly_the_missed_bright_stars |
| Fly.STILL_REACH_SEARCH | loomsiril.assign.STILL_REACH_SEARCH | P | test_assign: test_constants_match_js |
| Fly.dropInsideBrighter (1787-1802) | loomsiril.assign.drop_inside_brighter | P | test_assign: test_drop_inside_brighter_matches_js, test_selftest_star_on_a_bright_stars_spike_is_not_a_sprite |

## Deviations (by design)

- `assign_sprites` raises ValueError for a non-whole `grow`. Sky.js 688 always passes `px = max(1, round(fwhm))`.
  A fractional rect would make the JS ownership fill write nothing (non-integer typed-array index); Python slices need ints.
- `drop_inside_brighter` calls `halo_of(d)` for a detection with `halo is None` and relies on the production modeller to set
  `det.halo` as a side effect (as Sky.js does). The oracle replays a pure rule, so `halo` stays None there.
- Neighbour sources are compared by object identity (`is`), as the JS `!==`. Production builds neighbours and placed stars from the same Gaia record objects.
- `detect_stars` is a numpy detector, not PixInsight's. Its outputs are checked against synthetic truth (tests/synth.py),
  not against PixInsight numbers. Choices left open by the plan: candidates are tested on the smoothed image; the area
  threshold compares raw intensity minus sky with half of (smoothed peak minus sky); ties go to the first pixel in raster order.
- Noise floor (E3e). `detect_stars` uses `noise = max(noise_sigma(inten), 1/65535)`. Why: a stars layer with median 0
  and MAD 0 (a noiseless synthetic field, or a StarNet stars layer that is mostly exact zeros after unscreen) gave noise 0,
  so the threshold `sky + 5*noise` sat on the background: a 30-star 1920x1080 mono field produced 129,080 detections
  (30 are real), and the O(N^2) fragment filter then took ~26 min. One 16-bit quantisation step, 1/65535 (1.526e-5), is the
  smallest noise a 16-bit input can express, so it is the floor. The JS has no equivalent (the detector is Python-only).
- Upper-half noise (R2-0). `detect_stars` uses `noise = max(noise_sigma(inten), _upper_half_sigma(inten), 1/65535)`.
  `_upper_half_sigma` is (P65 - P50) / 0.3853 over every 7th sample, GATED: it returns 0 unless at least 30% of the sampled
  pixels are exactly 0. Why: a one-channel stars layer is zero-inflated (unscreen clips the negative noise, about half its
  pixels are 0), so the median sits on the clip boundary and the MAD sees only the sliver around it: 3.7e-5 for a 1e-3 noise,
  and `detect_stars` returned 133,297 detections for 30 stars at 1920x1080 (3,638 for 20 stars at 320x180), which made
  `star_model` (~10 ms each) take 14.4 min. Upper-half estimate: a normal distribution clipped at zero keeps its upper half
  intact. Why the gate: without it P65 - P50 is above the MAD on unclipped layers too (three channels: 4.6e-4 against 3.9e-4),
  which raised the threshold and lost faint stars (R2-0 review: nc3 with 150 bright stars, recall of 5-sigma stars 65/84 ->
  0/84 with the ungated P84 estimate). Zero fractions measured: one channel with noise 0.45-0.50, three channels 0.10-0.17,
  noiseless 0.37-0.55 (estimate below the floor there, so the floor decides). Gate threshold 0.3 sits in the gap.
  Why P65 and not P84: star light in the upper tail biases the estimate high, and P84 is further into that tail.
  Measured on noisy one-channel fields (sigma 1e-3, 320x180, seeds 1-3, 100 faint stars per field, recall within 0.75 px;
  "ideal" = the true sigma as noise):

  | bright stars | faint peak | MAD only (BASE) recall / det per truth | P84 recall | P65 recall | ideal recall |
  |---|---|---|---|---|---|
  | 5 | 10 sigma | 0.94 / 31.4x | 0.75 | 0.86 | 0.91 |
  | 40 | 10 sigma | 0.88 / 13.5x | 0.27 | 0.53 | 0.83 |
  | 150 | 10 sigma | 0.69 / 0.85x | 0.02 | 0.04 | 0.60 |

  6 and 8 sigma faint stars are missed by every estimator, the ideal included: the smoothed (3x3) peak of a 6-8 sigma star
  stays below the 5-sigma threshold. Crowded fields: the P65 estimate remains biased high by star light (about 1.3x sigma
  on 150 bright stars and noisy mono), which costs recall; no estimator in this family recovers the ideal there. Detections
  stay at or below 1x truth for P65 in every table row (no noise flood). `noise_sigma` (the JS twin, which also feeds
  `env.noise`) is unchanged. Changed cases: one-channel noisy layers (sparse and crowded). Unchanged in practice:
  three-channel layers and noiseless layers, because their sampled zero fraction stays below the 0.3 gate
  (test_detection_list_is_unchanged_*, test_faint_stars_*). NOT unchanged by construction: any layer whose zero fraction
  reaches the gate takes the new estimate. A three-channel frame with an exactly-zero border of about 25% of the width or
  more trips it and loses faint stars near the threshold (5-sigma recall 59/60 -> 53/60 at a 25% border,
  26/60 at 35%; stars of 6 sigma and above unaffected). Known limit, listed in README and CHANGELOG.
- When the output changes. The output changes whenever the stars-layer `noise_sigma` is below 1/65535, not only when it is
  exactly 0: it is then raised to the floor, which moves the threshold from about `sky + 5*noise` to `sky + 7.6e-5`, and
  every smoothed tail pixel between the two is no longer a star. Comparison matrix (base 59d574b vs E3e, sprites and residual frames compared bit for bit): cases whose stars-layer
  noise is at or above the floor are bit-identical (nc1 320x180 with input noise 1e-3, nc1 960x540 with 1e-3, nc3 with
  noise 0 to 1e-3, sdr and pq). Cases below it change: nc1 320x180 with input noise 0, 1e-6, 3e-5, 1e-4 (3519, 3491, 3570,
  3590 detections in the base, 20 in E3e) and nc1 960x540 with input noise 3e-5 (33791 -> 20). The author's nc3 and 960x540
  cases had stars-layer noise near 1e-8, below the floor; they are identical only because no tail pixel fell between
  4e-8 and 7.6e-5. Every changed case is the same tail-pixel pathology, so the change is intended.
- Caveat, not covered by the tests. 32-bit linear data whose stars-layer noise is below 1.5e-5 and whose stars are fainter
  than 7.6e-5 (smoothed, above the sky) loses those stars: they fall below the floored threshold. Real 16-bit frames
  (noise well above one step) are unaffected. `noise_sigma` itself is unchanged (it still returns 0 for a flat buffer, as
  Sky.noiseSigma does), so the oracle is untouched. `starfit.make_env` (starfit.py:237) still uses the unfloored
  `sm.noise_sigma`; that is pre-existing and not changed by E3e.
- Test: test_zero_median_and_mad_mono_field_detects_exactly_the_injected_stars (noiseless 320x180 mono, 30 stars).
- Selftest 15080-15115 and 16064-16085 use JS stubs that set `d.halo` as a side effect; the literal checks replay them with the same stub shape in Python.
