# E3d parity: the star-layer driver (loomsiril/starlayer.py)

JS source: Loom v0.4.1, `script/lib/Sky.js` 683-731 (`Sky.sprites`) and `Sky.detections` (E3a).
Oracle: `tools/oracle/starlayer.js` records `tests/oracle/starlayer.json` from the real JS, two cases:
`field` (a bright spiked star, a faint star on its 45 degree spike, an undetected star in its core, an
undetected star on a glow, a bright detection that is not placed with a faint placed star in its reach,
a blend pair, an unmatched placed star) and `spiked` (the bright spiked star and its spiky neighbour on
the spike, for the borrowed-spike rule). Replayed in `tests/test_starlayer.py`: counts, stage texts,
rects, radii, centres, distances, masks and spike angles exact; residual and sprite planes within 1e-6.
Synthetic checks (tests/synth.py and small hand-made fields): residual + sprites == stars layer, >= 70%
of stars become sprites, each footprint holds its star, a faint placed star inside a bright star's reach
is absorbed, counts, stage order, the detections argument, the caller's detections unchanged, identity
of catalogue sources, runtime.

Status: P = ported, V = vectorised port, R = replaced by a Python equivalent, D = not ported.

| JS | Python | Status | Test |
|---|---|---|---|
| Sky.sprites | `starlayer.sprites` (returns `StarLayer`) | P | test_residual_plus_sprites_equals_stars_layer, test_most_injected_stars_become_sprites, test_each_footprint_contains_its_star_and_has_its_placed_distance, test_faint_star_inside_a_bright_stars_reach_is_not_a_sprite, test_counts_are_consistent_with_placed, test_stage_callback_gets_js_texts_in_order, test_runtime_one_run_under_five_seconds |
| Sky.sprites return `{sprites, residual, residualMask, blended, unmatched, detected}` | `StarLayer` dataclass (`residual_mask` uint8 ones, `detected = len(dets)`) | P | test_counts_are_consistent_with_placed, test_detections_argument_replaces_the_detector |
| Sky.sprites `px = Math.max(1, Math.round(fwhm \|\| FWHM_DEFAULT))` | `starfit.sprite_px` (called from `starlayer.sprites`) | P | test_sprite_px_is_js_round_with_default (E3c) |
| Sky.sprites halo callback (models each detection once, stage every 200) | `_Modeller.halo` (method of the halo callback, used by `starlayer.sprites`; R2-d) | P | test_stage_callback_gets_js_texts_in_order |
| Sky.sprites residual = copies of the stars planes | `np.array(..., float32, copy=True)` per channel | P | test_residual_plus_sprites_equals_stars_layer |
| Sky.sprites `k % 50 == 0` deblend stage | `starlayer.sprites` loop | P | test_stage_callback_gets_js_texts_in_order |
| Sky.detections | `detect.detect_stars` (E3a) | R | test_detect (E3a) |
| Fly.catalogueBlend (identity of sources, used by assign) | `assign.catalogue_blend` | P | test_catalogue_blend_is_by_source_identity, oracle replay |

Deviations from the JS, all deliberate:

- `detections` argument: an explicit list (even an empty one) replaces the detector. The plan's
  `detections or detect_stars(...)` would fall back to the detector on an empty Siril star list;
  the port uses `detections is not None`. Test: test_detections_argument_replaces_the_detector.
- `Placed.d` is `1000 / usable_parallax(source)` as `parallax.place_stars` computes it (the zero-point
  corrected parallax, the JS `Fly.usableParallax`), not the raw `plx`.
- `assign_sprites` returns `absorbed` too; `StarLayer` does not carry it, so the count check is an
  inequality `blended + unmatched + sprites <= placed`.

## Caller contracts

- `neighbours[k]["source"]` must be the SAME object as the `Placed.source` it belongs to (Fly.catalogueBlend
  compares sources by identity). A copy, for example after a JSON round trip, makes every placed star look
  blended. The Siril bridge must build neighbours from the placed records' own source dicts.
- `sprites()` does not change the detections passed in: it works on copies (index, model and halo are set
  on the copies). Test: test_passed_detections_are_not_changed.

## Mutation checks (scratch copies of the tree, nothing committed)

Each mutation replaces one line in a copy of `loomsiril/` and runs `tests/test_starlayer.py`:

| Mutation | Result on this tree |
|---|---|
| M1 skip drop_inside_brighter (assign.py) | 4 failed: oracle field counts/rects/planes, test_faint_placed_star_in_reach_of_an_unplaced_bright_detection_is_dropped |
| M2 skip the second pass (unexplained_sources) | 1 failed: oracle field planes |
| M3 skip add_to_sums | 1 failed: oracle field planes |
| M4 skip attach_to_cores | 1 failed: oracle field planes |
| M5 skip drop_borrowed_spikes | 1 failed: oracle spiked planes |
| M6 assign without the halo callback | 5 failed: oracle field counts/rects/planes, test_faint_placed_star_in_bright_reach_is_absorbed, test_faint_placed_star_in_reach_of_an_unplaced_bright_detection_is_dropped |

Why M1 needs the unplaced bright detection: when the bright star is itself a sprite candidate,
one_star_one_sprite drops the faint one as well, so skipping drop_inside_brighter alone changes nothing.
The test with an unplaced bright detection is the case where only drop_inside_brighter can drop it.

