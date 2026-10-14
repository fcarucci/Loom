# R2-b parity rows (refactor loop 2: orchestration, scene inputs, Gaia query)

The JS names below are the ones the scene, gaia and pipeline modules port. Status as in r1-b: P ported,
V vectorised or split, R replaced by a Python equivalent, D not ported.

| JS | Python | Status | Test |
|---|---|---|---|
| Render.scene(a) (one object: starless, stars, sprites, project, target, D, residual, residualMask, catalogue) | scene.SceneInputs, scene.scene(a) | P | test_scene.py::test_scene_shapes_and_projection, test_scene_takes_the_residual_as_given_and_masks_the_stars |
| Render.scaledScene | scene.scaled_scene, split into _shrink_point, _shrink_whole, _shrink_detection, _shrink_sprite, _shrink_catalogue | V (named steps, output unchanged) | test_scene.py::test_scaled_scene_output_is_characterized, test_scaled_scene_positions_and_shapes |
| FlyThrough.build (463-509) | pipeline.SceneRequest, pipeline.build_scene(req, stage) | P | test_pipeline.py::test_build_scene_neighbours_share_the_placed_source_objects |
| Fly.gaiaOnlineUrl | gaia.GaiaQuery, gaia.online_url(query) | P | test_gaia.py::test_online_url_matches_js, test_characterized_urls_and_cache_keys_are_unchanged |
| Sky.queryOnline | gaia.query_online(query, fetch_text) | P | test_gaia.py::test_query_online_records_url_and_parses |
| Sky.querySources | gaia.sources(query, ...) via pipeline._gaia_sources(job, query, ctx) (R3-a) | R (the query object replaces three loose arguments) | test_gaia.py::test_sources_cache_round_trip, test_gaia_query_is_a_frozen_value_object |
| (cache key, Python only) | gaia.cache_key(query) | R | test_gaia.py::test_characterized_urls_and_cache_keys_are_unchanged |
| FlyThrough.run flow (identify, distance, build, render; Sky.js fetch) | pipeline.RunContext(progress, is_cancelled, fetch_text) shared by run, _resolve_distance, _cluster_for_target, _illuminator_for_target, _gaia_sources, _render_frames, _finish_preset | R (the three callbacks travel together) | test_pipeline.py::test_distance_cluster_found_is_used, test_online_uses_the_injected_fetch_only |
| (Loom package exports, Python only) | loomsiril.__dir__, loomsiril.__getattr__ (submodules and FlyJob, Result, run) | R (lazy) | tests/test_package.py::test_dir_lists_the_public_names_and_the_submodules_after_a_bare_import |
