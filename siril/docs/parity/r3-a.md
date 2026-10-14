# R3-a parity rows (phase R loop 3: orchestration, package, Gaia, encode)

Signature-only changes; the JS behaviour each function ports is unchanged. Status: P = ported, V = vectorised,
R = replaced by a Python equivalent, D = not ported.

| JS | Python | Status | Test |
|---|---|---|---|
| FlyThrough.loopImage (511-521) | pipeline.loop_image(pr, i, opts, cache): the PresetRender carries scene, n, F, crop and the frame size | P | test_pipeline.py::test_loop_image_crossfades_into_the_pre_roll |
| FlyThrough.renderFinal (58-91) | pipeline.render_final(req: FinalRender, ctx: RunContext) | P | test_pipeline.py::test_frames_are_numbered_five_digit_16_bit_and_complete, test_cancel_after_two_frames_keeps_them_and_makes_no_video |
| frame signature sceneKey (FlyThrough.js scene key, no JS function name) | pipeline._scene_key(layers, SceneTerms) (sha256 of the planes and the JSON of the terms) | R | test_pipeline.py::test_scene_key_is_unchanged (digests from fly/final@23c0860) |
| Render.runProcess (pump, collect, deadline, cancel) | encode._collect(_Pipe, out, deadline_s, is_cancelled) | R | test_encode.py::test_run_process_* |
| Sky.queryOnline / Fly.gaiaOnlineUrl (centre input) | gaia.GaiaQuery.from_centre wraps a finite ra into [0, 360) (same sky point); GaiaQuery.__post_init__ refuses a NaN/inf ra or a dec outside [-90, 90] with a ValueError | R | test_gaia.py::test_centre_off_the_sky_is_a_value_error, test_centre_on_the_sky_is_accepted |
| (no JS; package listing) | loomsiril.__dir__ lists globals, __all__ and submodules | R | test_package.py::test_dir_lists_the_module_dunders_too |
| Sky.stallWatch, Sky.transferWatch limits (stall, first byte, total) | gaia._Limits (frozen: stall_s, first_byte_s, total_s) passed to _read_body and _check_gap | R | tests/test_gaia.py::test_fetch_text_* (fake clock; timing unchanged) |
