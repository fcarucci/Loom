# Parity: E1a geometry and parallax (Fly.js v0.4.1 -> Python)

Status: P = ported, V = vectorised port, R = replaced by a Python equivalent
(core.py or the language), D = not ported (reason given).

Oracle: `tests/oracle/geometry.json`, `tests/oracle/parallax.json` (recorded by
`tools/oracle/geometry.js`, `tools/oracle/parallax.js` from the read-only Loom
checkout). Tests: `tests/test_geometry.py`, `tests/test_parallax.py`.

## geometry.py

| JS | Python | Status | Test |
|---|---|---|---|
| Fly.RAD | core.RAD | R | test_geometry::test_constants_match_js (core) |
| Fly.vec, Fly.radec, Fly.dot, Fly.separation | core.vec, radec, dot, separation | R | test_geometry::test_vec_radec_round_trip |
| Fly.moved | geometry.moved | P | test_geometry::test_moved_matches_js; on/off axis, passed star |
| Fly.screenPosition | geometry.screen_position | P | test_geometry::test_screen_position_matches_js |
| Fly.BACKDROP_MOTION_DEFAULT | geometry.BACKDROP_MOTION_DEFAULT | P | test_geometry::test_constants_match_js; test_backdrop_k_and_galaxy |
| Fly.backdropZoom | geometry.backdrop_zoom | P | test_replays_oracle[backdropZoom] |
| Fly.backdropScale | geometry.backdrop_scale | P | test_replays_oracle[backdropScale] |
| Fly.radialSource | geometry.radial_source | P | test_replays_oracle[radialSource] |
| Fly.discVisible | geometry.disc_visible | P | test_replays_oracle[discVisible] |
| Fly.discGridCount | geometry.disc_grid_count | P | test_replays_oracle[discGridCount] (24 x 24 loop kept) |
| Fly.coverFraction | geometry.cover_fraction | P | test_replays_oracle[coverFraction] |
| Fly.HDR_MAG_RANGE, HEADROOM_RADIUS, HEADROOM_SIGMA, HDR_REFERENCE_WHITE | geometry.HDR_MAG_RANGE, HEADROOM_RADIUS, HEADROOM_SIGMA, HDR_REFERENCE_WHITE | P | test_constants_match_js |
| Fly.headroomBump | geometry.headroom_bump | P | test_replays_oracle[headroomBump] |
| Fly.magnitudeGain | geometry.magnitude_gain | P | test_replays_oracle[magnitudeGain] |
| Fly.SPIKE_LENGTH_MAX | geometry.SPIKE_LENGTH_MAX | P | test_constants_match_js |
| Fly.spikeLength | geometry.spike_length | P | test_replays_oracle[spikeLength] |
| Fly.SHUTTER | geometry.SHUTTER | P | test_constants_match_js |
| Fly.shutterSteps | geometry.shutter_steps | P | test_replays_oracle[shutterSteps] |
| Fly.TWINKLE_DEFAULT | geometry.TWINKLE_DEFAULT | P | test_constants_match_js |
| Fly.seeded | geometry.seeded | P | test_hash_and_twinkle_replay_oracle[seeded] (tolerance 1e-9, see notes) |
| Fly.twinkle | geometry.twinkle | P | test_hash_and_twinkle_replay_oracle[twinkle]; test_twinkle_deterministic_and_first_frame_untouched |
| Fly.GROWTH_DEFAULT | geometry.GROWTH_DEFAULT | P | test_constants_match_js |
| Fly.growth | geometry.growth (parameter `growth_amount`) | P | test_replays_oracle[growth] |
| Fly.pixelScale | geometry.pixel_scale | P | test_replays_oracle[pixelScale] |
| Fly.MODEL_BLEND | geometry.MODEL_BLEND | P | test_constants_match_js |
| Fly.CORE_MODEL_BLEND | geometry.CORE_MODEL_BLEND | P | test_constants_match_js |
| Fly.coreModelWeight | geometry.core_model_weight | P | test_replays_oracle[coreModelWeight] |
| Fly.modelWeight | geometry.model_weight | P | test_replays_oracle[modelWeight] |
| Fly.FADE_NEAR | geometry.FADE_NEAR | P | test_constants_match_js |
| Fly.opacity | geometry.opacity | P | test_replays_oracle[opacity]; test_opacity_monotone_and_zero_behind |
| Fly.smoothstep | core.smoothstep | R | tests/test_core.py |
| Fly.ease | geometry.ease | P | test_replays_oracle[ease]; test_ease_modes |

## parallax.py

| JS | Python | Status | Test |
|---|---|---|---|
| Fly.PARALLAX_ZERO_POINT | parallax.PARALLAX_ZERO_POINT | P | test_constants_match_js |
| Fly.SIGMA_TABLE | parallax.SIGMA_TABLE | P | test_constants_match_js; test_parallax_sigma_table_numbers |
| Fly.parallaxSigma | parallax.parallax_sigma | P | test_parallax_sigma_matches_js |
| Fly.usableParallax | parallax.usable_parallax | P | test_usable_parallax_matches_js; test_usable_parallax_rules |
| Fly.ILLUMINATOR_MAX_G | parallax.ILLUMINATOR_MAX_G | P | test_constants_match_js |
| Fly.ILLUMINATOR_MIN_RADIUS | parallax.ILLUMINATOR_MIN_RADIUS | P | test_constants_match_js |
| Fly.illuminatorRadius | parallax.illuminator_radius | P | test_scalar_replays_oracle[illuminatorRadius] |
| Fly.illuminatorDistance | parallax.illuminator_distance | P | test_scalar_replays_oracle[illuminatorDistance]; test_illuminator_picks_the_brightest_reliable_star |
| Fly.CLUSTER_MIN_MEMBERS | parallax.CLUSTER_MIN_MEMBERS | P | test_constants_match_js |
| Fly.CLUSTER_MIN_CONTRAST | parallax.CLUSTER_MIN_CONTRAST | P | test_constants_match_js |
| Fly.CLUSTER_MIN_SIGNIFICANCE | parallax.CLUSTER_MIN_SIGNIFICANCE | P | test_constants_match_js |
| Fly.CLUSTER_MAX_RADIUS | parallax.CLUSTER_MAX_RADIUS | P | test_constants_match_js |
| Fly.CLUSTER_PM_SCALE | parallax.CLUSTER_PM_SCALE | P | test_constants_match_js |
| Fly.CLUSTER_PLX_SCALE | parallax.CLUSTER_PLX_SCALE | P | test_constants_match_js |
| Fly.CLUSTER_GROW | parallax.CLUSTER_GROW | P | test_constants_match_js |
| Fly.median, Fly.mad | core.median, core.mad | R | tests/test_core.py |
| Fly.clusterRadius | parallax.cluster_radius | P | test_scalar_replays_oracle[clusterRadius]; test_cluster_radius_caps_big_nebulae |
| Fly.clusterSamples | parallax.cluster_samples | P | test_cluster_samples_matches_js |
| Fly.findCluster | parallax.find_cluster | V | test_find_cluster_matches_js; test_constructed_cluster_is_found_at_its_distance; test_uniform_field_has_no_cluster; test_ic1396_cluster_is_870_to_1000_pc |
| Fly.refineCluster | parallax.refine_cluster | P | test_refine_cluster_matches_js |
| Fly.defaultTravel | parallax.default_travel | P | test_scalar_replays_oracle[defaultTravel]; test_default_and_clamped_travel |
| Fly.clampTravel | parallax.clamp_travel | P | test_scalar_replays_oracle[clampTravel]; test_default_and_clamped_travel |
| Fly.placeStars | parallax.place_stars | P | test_place_stars_matches_js; test_place_stars_keeps_only_inside_usable_stars |

## Notes on the port

- `moved` on the camera (|p| = 0): core.radec divides by |p| and would raise in
  Python; `moved` takes the JS result instead (ra = atan2(0, 0), dec NaN, ratio Infinity).
- JS `x || d` and `Math.min/max` NaN rules, `Math.floor`/`ceil`/`sin` of non-finite
  values, and `x/0` are reproduced by small helpers in loomsiril/jsmath.py (`js_div`,
  `js_min`, `js_max`, `js_sqrt`, `js_acos`, `js_sin`, `js_floor`, `js_ceil`; moved from geometry in R1-a).
- `seeded` and `twinkle` are replayed at 1e-9, not 1e-12: the sine hash multiplies
  a sin by 43758, so a 1-ulp difference between V8 and libm shows as about 1e-12
  to 1e-11 in the fraction. The digits themselves are the JS ones (float64, same
  constants and operation order).
- `find_cluster` computes the per-seed counts with numpy over the star columns,
  in the same operation order as the JS (so the same numbers). `refine_cluster`
  keeps the JS `near(c, k)` predicate shape.
- Gaia blanks: a None proper motion counts as NaN (not finite). JS `isFinite(null)`
  is true, but Gaia records here carry NaN for blanks, never null.
- `usable_parallax` treats a Python bool as not a number (JS `typeof true` is
  "boolean", so the JS returns null for it too).
- Not ported here (other tasks): STAR_QUALITY, starQuality, qualityScale, frameCount,
  timeAt and all preset/encode code (E1b).
