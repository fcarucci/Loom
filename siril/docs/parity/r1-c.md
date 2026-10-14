# R1-c: render path parameter objects (refactor loop 1)

Behaviour-preserving: every frame is bit-identical to the pre-loop code (tests/test_render_characterization.py,
72 frames, SHA-256 recorded on fly/final@ecc3f26). Status: P = ported (same maths, moved or re-signed),
V = vectorised port, R = replaced by a Python equivalent, D = not ported.

| JS | Python | Status | Test |
|---|---|---|---|
| `Fly.smoothstep` (sprites' scalar and array copies) | `core.smoothstep`, `core.smoothstep_arr` (sprites' copies deleted) | R | test_core.py::test_smoothstep_arr_is_the_scalar_elementwise; test_render_characterization.py |
| `Fly.radialSource` over an array (sprites' copy) | `geometry.radial_source_arr` (sprites' copy deleted; same body, NaN seen floors to 0.05) | V | test_jsmath.py::test_radial_source_arr_is_the_scalar_elementwise, ::test_radial_source_arr_nan_seen_floors_to_0_05 |
| `Fly.headroomBump` over an array (sprites' copy) | `geometry.headroom_bump_arr` (sprites' copy deleted) | V | test_jsmath.py::test_headroom_bump_arr_is_the_scalar_elementwise |
| `Render.spriteBounds` | `sprites.sprite_bounds(sp, cx, cy, g, target: FrameTarget)` | P | test_sprites.py::test_sprite_bounds_matches_js, ::test_sprite_bounds_clipped_matches_js |
| `Render.drawSprites` | `sprites.draw_sprites(accs, target: FrameTarget, d: SpriteDraw)` | V | test_sprites.py::test_draw_sprites_equals_per_pixel_reference, ::test_draw_sprites_footprint_equals_per_pixel_reference |
| `Render.spriteContext` | `sprites._sprite_context(d: SpriteDraw, cam)` | P | test_sprites.py (reference) |
| `Render.placeSprite` | `sprites.place_sprite(state: FrameState, e, j)` | P | test_sprites.py::test_place_sprite_matches_js |
| `Render.seenShares` | `sprites.seen_shares(placed, target: FrameTarget, ease)` | P | test_sprites.py::test_seen_shares_matches_js |
| `Render.drawPlaced` | `sprites.draw_placed(state: FrameState, T, q, target: FrameTarget)` | P | test_frame.py oracle (frame0, frame03, frame1, frameBlur) |
| `Render.drawPlacedSpikes` | `sprites.draw_placed_spikes(T, q, target: FrameTarget, ch, path)` (R2-c: opts dropped, coarse flag in ch) | P | test_frame.py oracle (spike sprite in the scene) |
| `Render.addSprites` | `sprites.add_sprites(state: FrameState, T, target: FrameTarget)` | P | test_sprites.py::test_add_sprites_into_zero_planes_matches_js |
| `Render.headroomMap` | `sprites.headroom_map(sc, placed, opts, target: FrameTarget)`; helpers `_HeadroomMap` (reach, paint), `_paint_moving_stars`, `_paint_catalogue_stars`, `_headroom_stars`, `_brightest_g` | V | test_frame.py::test_headroom_map_matches_js_oracle, ::test_headroom_map_core_radius_case_matches_js_oracle; test_render_characterization.py (HDR) |
| `Render.shutterPath` | `sprites.shutter_path(state: FrameState, q: PlacedSprite, target: FrameTarget)` (R2-c) | P | test_sprites.py::test_shutter_path_matches_js |
| `Render.frame` (the target and the state are built here) | `frame.frame` builds `FrameTarget(cam, tw, th)` and `FrameState(sc, s, opts_t)` | P | test_frame.py oracle; test_render_characterization.py |
| (no JS name) parameter objects | `sprites.FrameTarget(cam, out_w, out_h)`, `sprites.SpriteDraw(patches, sp, cx, cy, g, ks, k_outers, rc, how)`, `sprites.FrameState(sc, s, opts)` | R | test_render_characterization.py (every frame) |

Parameter objects: `FrameTarget` replaces `(cam, out_w, out_h)` in sprite_bounds, draw_sprites, draw_placed,
draw_placed_spikes, seen_shares, add_sprites and headroom_map. `SpriteDraw` replaces the nine sprite arguments of
draw_sprites. `FrameState` replaces `(sc, s, opts)` in place_sprite, draw_placed and add_sprites (shutter_path and
headroom_map take no travel s and keep their signatures; shutter_path takes (state, q, target) since R2-c).

Dropped test: the two `test_jsmath.py` tests that compared the array twins with sprites' private copies
(`*_equals_the_sprite_copy`) were removed with the copies; the scalar-vs-array checks are kept.
