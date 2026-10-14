# E1b parity: presets, timing, file naming, encode (fly/e1b)

Columns: JS = Loom v0.4.1 name; Python = loomsiril name; Status: P ported, V vectorised, R replaced by a
Python equivalent, D not ported (reason). Oracle cases: `tests/oracle/presets.json`, `tests/oracle/encode.json`
(recorded by `tools/oracle/presets.js` and `tools/oracle/encode.js`). Replay tests: `test_oracle_replay[...]`.

## presets.py

| JS | Python | Status | Test |
|---|---|---|---|
| Fly.STAR_QUALITY | STAR_QUALITY | P | test_constants_match_js, test_star_quality_levels_pinned_from_selftest |
| Fly.starQuality | star_quality | P | test_oracle_replay[starQuality] |
| Fly.qualityScale | quality_scale | P | test_oracle_replay[qualityScale] |
| Fly.frameCount | frame_count | P | test_oracle_replay[frameCount], test_frame_count_ping_pong_doubles |
| Fly.timeAt | time_at | P | test_oracle_replay[timeAt], test_time_at_turns_and_returns |
| Fly.DRAFT_BYTES | DRAFT_BYTES | P | test_constants_match_js |
| Fly.DRAFT_LONG | DRAFT_LONG | P | test_constants_match_js |
| Fly.draftPlan | draft_plan | P | test_oracle_replay[draftPlan], test_draft_plan_budget_and_length |
| Fly.PRESETS | PRESETS | P | test_constants_match_js |
| Fly.presetCrop | preset_crop | P | test_oracle_replay[presetCrop], test_preset_crop_keeps_aspect_and_stays_inside |
| Fly.headerCentre | header_centre | P (input: mapping or (name, value) pairs; the JS takes [{name, value}]) | test_oracle_replay[headerCentre], test_header_centre_takes_pairs_or_mapping_and_rejects_nothing_useful |
| Fly.presetTurns | preset_turns | P | test_oracle_replay[presetTurns] |
| Fly.presetLoops | preset_loops | P | test_oracle_replay[presetLoops] |
| Fly.presetSpec | preset_spec | P (returns a dict with the JS keys) | test_oracle_replay[presetSpec], test_preset_spec_ids_and_loops |
| Fly.frameStep | frame_step | P | test_oracle_replay[frameStep] |
| Fly.RENDERER_VERSION | RENDERER_VERSION | P | test_constants_match_js |
| Fly.ENCODE_ONLY | ENCODE_ONLY | P | test_constants_match_js, test_frame_signature_ignores_encode_only_options |
| Fly.frameSignature | frame_signature (+ js_json, number_str; js_num_str re-exported from presets) | P (JSON.stringify and Number#toString rules) | test_oracle_replay[frameSignature], test_frame_signature_is_the_js_string_and_sorted, test_js_number_text_matches_json_stringify, test_frame_signature_changes_with_star_quality |
| Fly.CROSSFADE_SECONDS | CROSSFADE_SECONDS | P | test_constants_match_js |
| Fly.crossfadeFrames | crossfade_frames | P | test_oracle_replay[crossfadeFrames] |
| Fly.loopFrame | loop_frame | P | test_oracle_replay[loopFrame], test_loop_frame_crossfade_starts_clean |
| Fly.isFrameFile | is_frame_file | P | test_oracle_replay[isFrameFile], test_is_frame_file_rejects_other_names |
| Fly.framePath | frame_path | P (JS keeps the last five digits: frame 100000 is frame_00000.tif, as in the JS) | test_oracle_replay[framePath], test_frame_path_is_five_digits_and_is_a_frame_file |
| Fly.sequenceLength | sequence_length | P | test_oracle_replay[sequenceLength], test_sequence_plays_forward_then_back_once |
| Fly.sequenceFrame | sequence_frame | P | test_oracle_replay[sequenceFrame] |
| Fly.parseAngle | parse_angle | P | test_oracle_replay[parseAngle], test_parse_angle_units |
| Fly.formatElapsed | format_elapsed | P | test_oracle_replay[formatElapsed] |
| Fly.progressText | progress_text | P | test_oracle_replay[progressText], test_progress_text_with_kept_frames |
| Fly.progressFraction | progress_fraction | P | test_oracle_replay[progressFraction] |
| Fly.WORKING_WIDTH | WORKING_WIDTH | P | test_constants_match_js |
| Fly.WORKING_HEIGHT | WORKING_HEIGHT | P | test_constants_match_js |
| Fly.workingScale | working_scale | P | test_oracle_replay[workingScale], test_working_scale_and_rotation_examples |
| Fly.needsRotation | needs_rotation | P | test_oracle_replay[needsRotation] |
| Fly.LINEAR_SKY | LINEAR_SKY | P | test_constants_match_js |
| Fly.looksLinear | looks_linear | P | test_oracle_replay[looksLinear] |
| FlyThrough.PRESET_LABELS | PRESET_LABELS | P (FlyThrough.js 698-702) | test_flythrough_constants_pinned_from_source |
| FlyThrough.PRESET_ORDER | PRESET_ORDER | P | test_flythrough_constants_pinned_from_source |
| FlyThrough.FPS | FPS | P | test_flythrough_constants_pinned_from_source |
| FlyThrough.LOOPS | LOOPS | P (FlyThrough.js 774) | test_flythrough_constants_pinned_from_source |
| FlyThrough.STAR_QUALITIES | STAR_QUALITIES | P (FlyThrough.js 775) | test_flythrough_constants_pinned_from_source |
| FlyThrough.STAR_QUALITY_DEFAULT | STAR_QUALITY_DEFAULT | P (FlyThrough.js 776) | test_flythrough_constants_pinned_from_source |

## encode.py

| JS | Python | Status | Test |
|---|---|---|---|
| Fly.VIDEO_FORMATS | VIDEO_FORMATS | P | test_constants_match_js |
| Fly.availableFormats | available_formats | P | test_oracle_replay[availableFormats], test_hdr_offers_no_h264_and_defaults_to_hevc_at_1080 |
| Fly.defaultFormat | default_format | P | test_oracle_replay[defaultFormat], test_4k_defaults_to_hevc_and_1080_to_h264 |
| Fly.REC709_FILTER | REC709_FILTER | P | test_constants_match_js |
| Fly.isHdr | is_hdr | P | test_oracle_replay[isHdr] |
| Fly.HDR_TRC | HDR_TRC | P | test_constants_match_js |
| Fly.HDR_PEAK_DEFAULT (Fly.js 1457, used by x265Hdr10) | HDR_PEAK_DEFAULT | P (extra: not in the task's list) | test_constants_match_js |
| Fly.hdrFilter | hdr_filter | P | test_oracle_replay[hdrFilter] |
| Fly.x265Hdr10 | x265_hdr10 | P | test_oracle_replay[x265Hdr10], test_ffmpeg_args_hevc_tag_and_pq_metadata |
| Fly.codecArgs | codec_args | P (returns None for an unknown id, as the JS undefined) | test_oracle_replay[codecArgs] |
| Fly.ffmpegArgs | ffmpeg_args | P | test_oracle_replay[ffmpegArgs], test_ffmpeg_args_sdr_h264_high |
| Fly.VIDEO_ENCODING_TAGS | VIDEO_ENCODING_TAGS | P | test_constants_match_js |
| Fly.videoName | video_name | P | test_oracle_replay[videoName] |
| Fly.AUDIO_FADE | AUDIO_FADE | P | test_constants_match_js |
| Fly.AUDIO_LOOP_FADE | AUDIO_LOOP_FADE | P | test_constants_match_js |
| Fly.audioFade | audio_fade | P | test_oracle_replay[audioFade] |
| Fly.audioArgs | audio_args | P | test_oracle_replay[audioArgs] |
| Fly.loopAudio | loop_audio | P (toFixed(3) as Decimal half-up) | test_oracle_replay[loopAudio] |
| Fly.ffmpegCandidates | ffmpeg_candidates | P | test_oracle_replay[ffmpegCandidates], test_ffmpeg_candidates_order_macos, test_ffmpeg_candidates_order_windows |
| Fly.ffmpegInstallHint | ffmpeg_install_hint | P | test_oracle_replay[ffmpegInstallHint], test_install_hints |
| Fly.commandLine | command_line | P (JS quoting rule, not shlex) | test_oracle_replay[commandLine], test_command_line_quoting_macos_and_windows |
| Fly.ffmpegFrameProgress | ffmpeg_frame_progress | P | test_oracle_replay[ffmpegFrameProgress], test_frame_progress_is_the_last_count |
| Render.runProcess | run_process | R (subprocess.Popen, no shell; output read as it arrives; exit -1 on deadline or cancel) | test_run_process_* (fake ffmpeg) |
| Render.isWorkingFfmpeg | is_working_ffmpeg | R | test_is_working_ffmpeg_answers_version_only_for_ffmpeg, test_real_ffmpeg_answers_version (ffmpeg marker) |
| Render.findFfmpeg | find_ffmpeg | R (PATH and HOME from the environment; saved path passed in) | test_find_ffmpeg_* |
| Render.FFMPEG_SETTING, Settings read/write | (none) | D: the path is not stored in a settings file; the caller passes `saved` | test_find_ffmpeg_prefers_the_saved_path |
| Render.ffmpegEncoders | ffmpeg_encoders | R | test_ffmpeg_encoders_is_the_encoder_listing |
| Render.encode | encode | R (exit 0 and the last argument exists; no lastEncodeOutput) | test_encode_* (fake ffmpeg) |
| Util.platform | platform_name | R (sys.platform mapped to macos, windows, linux) | test_platform_names_map_sys_platform |

## Notes

- `number_str` (jsmath.py, re-exported as presets.js_num_str) and `js_json` (presets.py) reproduce `String(number)` and `JSON.stringify` for the frame signature and
  the ffmpeg text (whole floats print without `.0`, exponents as JS writes them). Pinned by
  `test_js_number_text_matches_json_stringify`.
- Tolerances: strings and ints exact; floats 1e-12 (absolute), as the task required.
- The oracle recorder loads Util, Fly, Render and Sky only, so the FlyThrough constants are pinned from source in
  `test_flythrough_constants_pinned_from_source`.
- The ffmpeg end-to-end run is in the Docker gate (ffmpeg present there); here `test_real_ffmpeg_answers_version`
  skips and every other encode test uses a fake ffmpeg script.
