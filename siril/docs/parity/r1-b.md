# R1-b parity rows (refactor loop 1: orchestration and layering)

The JS names below are the ones the pipeline, cli and package exports port. Rows name the Python
functions after this loop; the e6 rows keep their names (no private helper of e6 was renamed).

| JS | Python | Status | Test |
|---|---|---|---|
| FlyThrough.renderFinal (58-91) | pipeline.render_final (since R3-a: render_final(req: FinalRender, ctx: RunContext), see r3-a.md) | P | test_pipeline.py::test_frames_are_numbered_five_digit_16_bit_and_complete |
| FlyThrough.presetJob (137-143) | pipeline._preset_job (returns PresetRender) | P | test_pipeline.py::test_portrait_turns_a_landscape_preset_into_its_own_folder |
| FlyThrough.renderFrameRange (157-179) | pipeline._render_frames, _frame_planes | P | test_pipeline.py::test_cancel_after_two_frames_keeps_them_and_makes_no_video |
| FlyThrough.loopImage (511-521) | pipeline.loop_image (R1-b kept its signature; R3-a changed it to loop_image(pr, i, opts, cache), see r3-a.md) | P | test_pipeline.py::test_loop_image_crossfades_into_the_pre_roll |
| FlyThrough.finishPreset (231-248) | pipeline._finish_preset | P | test_pipeline.py::test_a_failed_ffmpeg_is_reported_with_its_output_and_command |
| FlyThrough.encodeArgs (250-256) | pipeline._encode_args (takes PresetRender) | P | test_pipeline.py::test_no_video_gives_the_exact_ffmpeg_command |
| FlyThrough.clearFrames (281-297) | pipeline.clear_frames, _preset_folder_names, _loom_entries, _remove_if_empty | P | test_pipeline.py::test_clear_frames_removes_a_folder_it_empties |
| FlyThrough.findDistance (367-375) | pipeline._resolve_distance | R (no NGC/IC catalogue; the target is given) | test_pipeline.py::test_distance_cluster_found_is_used |
| FlyThrough.clusterDistance (427-462), Sky.clusterSources (456-458) | pipeline._cluster_for_target, cluster_distance | P | test_pipeline.py::test_distance_cluster_found_is_used |
| Fly.illuminator search (reflection nebula) | pipeline._illuminator_for_target | P | test_pipeline.py::test_distance_illuminator_from_the_tsv_is_used_without_a_fetch |
| Fly.illuminator search, online fallback | pipeline._illuminator_for_target (kind online, star["online"]) | P | test_pipeline.py::test_distance_illuminator_from_the_injected_fetch_when_the_sample_has_none |
| Dialog defaults (1282-1380) and the travel clamp | pipeline._render_opts, DIALOG_DEFAULTS | P | test_pipeline.py::test_a_travel_past_nine_tenths_of_the_distance_is_capped_and_noted |
| Dialog.prepareScene refusals | pipeline._validate_options, _validate_presets, _validate_job | R (same messages) | test_pipeline.py::test_each_job_value_is_checked_before_any_render |
| Sky.workingCopy notes (1302-1338) | pipeline._working_inputs | P | test_pipeline.py::test_a_resampled_working_copy_is_noted_and_drops_detections |
| FlyThrough.headerCentre / headerObject (307-322) | cli._target_for, cli._prepare (OBJECT) | R | test_cli.py::test_no_target_takes_the_header_centre_or_the_image_centre |
| Loom package exports (no JS name) | loomsiril.__getattr__ (PEP 562) for run, FlyJob, Result | R (lazy, breaks the import cycle) | test_pipeline.py::test_importing_core_does_not_import_the_pipeline |
