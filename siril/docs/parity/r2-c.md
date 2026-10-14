# R2-c parity rows (refactor loop 2: render path and resampling interfaces)

Behaviour-preserving: every frame stays bit-identical (tests/test_render_characterization.py, unchanged hashes),
every oracle replay passes with its recorded JS inputs (the replays are retargeted 1:1 where a signature moved).
Status: P = ported (same maths, moved or re-signed), V = vectorised port, R = replaced by a Python equivalent,
D = not ported.

| JS | Python | Status | Test |
|---|---|---|---|
| `Render.frame` (R2-c: builds the output FrameTarget once and passes it to frame_image) | `frame.frame` | P | test_frame.py oracle; test_render_characterization.py |
| `Render.frameImage` (R2-c: `frame_image(S, T, opts, target: FrameTarget, clock)`, nc = len(S)) | `frame.frame_image` | P | test_frame.py::test_frame_image_without_output_is_the_composite; test_render_characterization.py |
| `Render.spriteBounds` (R2-c: to_u/to_v are `Camera` methods) | `sprites.sprite_bounds(sp, cx, cy, g, target)`, `Camera.to_u`, `Camera.to_v` | P | test_sprites.py::test_sprite_bounds_matches_js, ::test_sprite_bounds_clipped_matches_js |
| `Render.shutterPath` (R2-c: `shutter_path(state, q, target)`; helpers `_shutter_ends`, `_shutter_samples`) | `sprites.shutter_path` | P | test_sprites.py::test_shutter_path_matches_js, ::test_shutter_path_still_is_one_point |
| `Render.splitPatches` (R2-c: helpers `_core_share`, `_core_box`, `_outer_model`, `_core_parts`) | `sprites.split_patches` | V | test_sprites.py::test_split_patches_matches_js, ::test_split_patches_core_zero_has_no_core_part |
| `Render.placedChannels` (R2-c: `placed_channels(q, opts, seconds, nc, wc)`; dict carries `coarse`) | `sprites.placed_channels` | P | test_sprites.py::test_empty_model_spikes_takes_the_core_path, ::test_placed_channels_with_one_spikes_entry_for_two_channels |
| `Render.drawPlacedSpikes` (R2-c: `draw_placed_spikes(T, q, target, ch, path)`) | `sprites.draw_placed_spikes` | P | test_frame.py oracle (spike sprite in the scene) |
| `Render.axisWeights` (R2-c: `axis_weights(out_n, view: AxisView, kernel)`; helpers `_sample_positions`, `_tent_taps_all`, `_kernel_taps_all`, `_pack_axis`) | `resample.axis_weights`, `resample.AxisView` | V | test_resample.py::test_axis_weights_match_js (recorded args through `_axis`), ::test_resample_matches_js, ::test_one_to_one_weights_are_the_identity |
| `Render.upscaleStars` (R2-c: builds AxisView(0, tw, 0, 1, tw)) | `scene.upscale_stars` | P | test_scene.py::test_small_helpers |
| `Render.layerAxis` (unchanged in R2-c: its test is in test_scene.py, not owned by this task) | `scene.layer_axis(full, n, out_n, o, length, tp, K, src_n, kernel)` | P | test_scene.py::test_small_helpers |
| `Render.shrinkPatch` (left as is: JS-shaped signature pinned by the oracle; see the DONE note) | `resample.shrink_patch` | P | test_resample.py (oracle) |
| `Render.areaMean` (left as is: JS-shaped signature pinned by the oracle) | `resample.area_mean` | P | test_resample.py (oracle) |
| `Render.drawPlaced` (R2-c: `_pick` module helper; no JS-name change) | `sprites.draw_placed` | P | test_frame.py oracle (frame0, frame03, frame1, frameBlur) |
