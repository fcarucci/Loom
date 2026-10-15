# Parity: Loom Fly-Through (JavaScript) -> loomsiril (Python)

Source: Loom v0.4.1, plus the 0.4.2 name resolver (commit a8252c3; unchanged up to v0.5.0) (https://github.com/fcarucci/Loom), `script/FlyThrough.js`, `script/lib/Fly.js`,
`script/lib/Render.js`, `script/lib/Sky.js`, read-only. Oracle: Loom's `script/selftest.js` groups and the golden
numbers recorded by `tools/record_oracle.js` (one recorder per module in `tools/oracle/`) into `tests/oracle/*.json`;
the Python tests replay them at the JS tolerances.

Status: **P** ported (same maths, tests pin the JS numbers) - **V** ported vectorised (numpy) - **R** replaced by a
Python equivalent (documented) - **D** not ported (reason given; summary in "Not ported, and why") - **S** Siril layer.

Conventions that differ from the JS on purpose (engineering decisions, each tested):
- Images are numpy arrays shaped (h, w), one per channel; the JS flat index `y*w + x` is `[y, x]`.
- Row order is TOP-DOWN everywhere inside loomsiril, as in the JS. FITS files and Siril's pixel data are bottom-up:
  they are flipped once on the way in, and their WCS converted with `wcs.flip_rows` (CRPIX2 -> h + 1 - CRPIX2,
  CD1_2 and CD2_2 negated). PSF stars from Siril are pixel-edge coordinates from the top (x - 0.5, y - 0.5).
- Records the JS passes as plain objects are dataclasses in `loomsiril/types.py`.
- The star detector (`detect.detect_stars`, Python-only: it replaces PixInsight's StarDetector) floors its noise at one
  16-bit step, 1/65535, so a noiseless (synthetic or heavily denoised) stars layer does not turn every tail pixel into a
  detection. Caveat: a stars layer whose noise is below that floor loses stars fainter than about 7.6e-5 above the sky;
  real 16-bit frames are not affected. Details in the E3A section ("Noise floor").
- On a noisy stars layer that is mostly exact zeros (a one-channel layer after star removal clips the negative noise),
  the detector takes its noise from the upper half of the distribution (loop 2, INV-2 fix). Known limits: in a crowded
  noisy one-channel field that estimate stays biased high by star light (about 1.3x sigma with 150 bright stars), which
  costs faint-star recall; and a three-channel frame with an exactly-zero border of about 25% of the width or more also
  takes this path and loses some 5-sigma stars (6 sigma and above unaffected). Details in the E3A section.
- JavaScript number semantics Python lacks (NaN-propagating Math.max/min, IEEE division, `||` defaults, Number#toString,
  JSON.stringify) live in one module, `loomsiril/jsmath.py` (phase R loop 1).

## Shared primitives (core.py, types.py; task E0)

| JS | Python | Status | Test |
|---|---|---|---|
| Fly.RAD, smoothstep, median, mad | loomsiril.core.RAD, smoothstep, median, mad | P | tests/test_core.py |
| Fly.vec, radec, dot, separation, tanProject, tanUnproject | loomsiril.core.vec, radec, dot, separation, tan_project, tan_unproject | P | tests/test_core.py |
| Fly.grownRect, detectionRect, squareRect | loomsiril.core.grown_rect, detection_rect, square_rect | P | tests/test_core.py |
| Math.round (JS halves) | loomsiril.core.js_round | P | tests/test_core.py |
| Steps.deriveStarsByUnscreen (PixelMath) | loomsiril.core.unscreen (and core.screen) | R | tests/test_core.py |
| (plain JS records: placed, detection, halo, model, sprite, scene) | loomsiril.types.* | R | tests/test_types.py |


## Task E1A: geometry.py, parallax.py
Status: P = ported, V = vectorised port, R = replaced by a Python equivalent
(core.py or the language), D = not ported (reason given).

Oracle: `tests/oracle/geometry.json`, `tests/oracle/parallax.json` (recorded by
`tools/oracle/geometry.js`, `tools/oracle/parallax.js` from the read-only Loom
checkout). Tests: `tests/test_geometry.py`, `tests/test_parallax.py`.

### geometry.py

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

### parallax.py

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

### Notes on the port

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


## Task E1B: presets.py, encode.py
Columns: JS = Loom v0.4.1 name; Python = loomsiril name; Status: P ported, V vectorised, R replaced by a
Python equivalent, D not ported (reason). Oracle cases: `tests/oracle/presets.json`, `tests/oracle/encode.json`
(recorded by `tools/oracle/presets.js` and `tools/oracle/encode.js`). Replay tests: `test_oracle_replay[...]`.

### presets.py

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

### encode.py

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

### Notes

- `number_str` (jsmath.py, re-exported as presets.js_num_str) and `js_json` (presets.py) reproduce `String(number)` and `JSON.stringify` for the frame signature and
  the ffmpeg text (whole floats print without `.0`, exponents as JS writes them). Pinned by
  `test_js_number_text_matches_json_stringify`.
- Tolerances: strings and ints exact; floats 1e-12 (absolute), as the task required.
- The oracle recorder loads Util, Fly, Render and Sky only, so the FlyThrough constants are pinned from source in
  `test_flythrough_constants_pinned_from_source`.
- The ffmpeg end-to-end run is in the Docker gate (ffmpeg present there); here `test_real_ffmpeg_answers_version`
  skips and every other encode test uses a fake ffmpeg script.


## Task E2A: colour.py
Source: Loom v0.4.1, `script/lib/Fly.js` lines 1334-1623 unless stated. Oracle: `tests/oracle/colour.json`
(recorded by `tools/oracle/colour.js`); literals from `selftest.js` fly.colour (15382-15501) and fly.hdr
(15502-15655). Status: P = ported, V = vectorised port, R = replaced by a Python equivalent,
D = not ported (reason given).

| JS | Python | Status | Test |
|---|---|---|---|
| Fly.matMul | `mat_mul` | P | test_mat_mul_matches_js |
| Fly.BRADFORD_D50_D65 | `BRADFORD_D50_D65` | P | test_constants_are_the_js_ones |
| Fly.XYZ_TO_RGB | `XYZ_TO_RGB` | P | test_constants_are_the_js_ones |
| Fly.SRGB_PROFILE_NAME | `SRGB_PROFILE_NAME` | P | (value only; used by srgb_icc_bytes desc tag) |
| Fly.SRGB_CURVE | `SRGB_CURVE` | P | test_parametric_type3_follows_its_formula |
| Fly.SRGB_COLOUR | `SRGB_COLOUR` | P | test_sdr_of_srgb_is_identity |
| Fly.trcDecode | `trc_decode` | P | test_scalar_against_js[trcDecode] |
| Fly.tableCurve | `table_curve` | P (NaN/inf x give NaN; JS indexes past the table) | test_scalar_against_js[tableCurve] |
| Fly.paraCurve | `para_curve` | P (JS divide/pow semantics via helpers) | test_scalar_against_js[paraCurve] |
| Fly.readCurve | `read_curve` | P (a 'curv' table running past the profile end returns None instead of zeros) | test_parse_icc_colour_matches_js |
| Fly.parseIccColour | `parse_icc_colour(data: bytes)` | P (out-of-range bytes read 0, as the JS) | test_parse_icc_colour_matches_js, test_no_matrix_profile_is_none |
| Fly.sourceToTarget | `source_to_target` | P | test_scalar_against_js[sourceToTarget], test_primaries_and_white |
| Fly.srgbEncode | `srgb_encode` | P | test_scalar_against_js[srgbEncode] |
| Fly.SDR_WHITE_NITS | `SDR_WHITE_NITS` | P | test_constants_are_the_js_ones |
| Fly.HDR_PEAK_DEFAULT | `HDR_PEAK_DEFAULT` | P | test_constants_are_the_js_ones |
| Fly.HLG_REFERENCE_PEAK | `HLG_REFERENCE_PEAK` | P | test_constants_are_the_js_ones |
| Fly.HDR_DEFAULT_TRANSFER | `HDR_DEFAULT_TRANSFER` | P | test_constants_are_the_js_ones |
| Fly.pqEncode | `pq_encode` | P | test_scalar_against_js[pqEncode], test_pq_and_hlg_literals |
| Fly.hlgEncode | `hlg_encode` | P | test_scalar_against_js[hlgEncode], test_pq_and_hlg_literals |
| Fly.hlgFromDisplay | `hlg_from_display` | P | test_scalar_against_js[hlgFromDisplay] |
| Fly.rolloff | `rolloff` | P | test_scalar_against_js[rolloff], test_rolloff_identity_slope_monotone |
| Fly.DECODE_LUT | `DECODE_LUT` | P | test_constants_are_the_js_ones |
| Fly.lutOf | `lut_of` | V (np.interp over the same 65537 samples, NaN and x<=0 read entry 0) | test_lut_is_clamped_and_nan_reads_the_first_entry |
| Fly.decodeLut | `decode_lut` | V (np.interp; 1e-6 of the JS LUT) | test_decode_lut_against_js_lut |
| Fly.outputTransform (SDR) | `output_transform` / `OutputTransform.apply` | V (vectorised, in place, float32 planes) | test_output_transform_matches_js[0,6,7,8] |
| Fly.hdrTransform (PQ, HLG, stats) | `OutputTransform` (HDR branch), `.stats` | V (MaxCLL/MaxFALL in `stats`) | test_output_transform_matches_js[1,2,4,5,7,8], test_hdr_stats_of_the_selftest_frame |
| Fly.isHdr | `is_hdr` | P (extra; needed by output_transform) | test_constants_are_the_js_ones |
| Fly.outputTransform .pixel / .pixelHdr | - | D: test helpers only; tests replay `apply` on 1-element planes | - |
| Sky.colourOf (Sky.js 1282-1300) | `colour_of(icc: bytes or None)` | P (returns {colour, note}) | test_colour_of_three_cases |
| Render.srgbIcc (Render.js 1352-1357) | `srgb_icc_bytes()` | R (generated ICC v2 profile, no PixInsight system profile) | test_srgb_icc_bytes_round_trip, test_srgb_icc_is_an_sdr_identity |

Notes
- `srgb_icc_bytes` writes the sRGB primaries to 7 digits (the same values as `SRGB_COLOUR.matrix`), not the
  4-digit 0.4361/0.2225/0.0139 values first proposed: at 4 digits the SDR identity misses 1e-4 (1.2e-4 at white).
- OutputTransform.apply takes one plane for gray and three for RGB; excess light is ignored in SDR, as in the JS.
  `stats` is always present (zeros for SDR).


## Task E2B: gaia.py, wcs.py, io_fits.py, io_tiff.py
Source: Loom v0.4.1 `script/lib/Fly.js` 2039-2063, `script/lib/Sky.js` 78, 136-204, 461-517, 1232-1250, 1356-1363,
`script/lib/Render.js` 1326-1350. Columns as in docs/PARITY.md. Status: P ported, V vectorised, R replaced by a Python
equivalent, D not ported (reason given).

| JS | Python | Status | Test |
|---|---|---|---|
| Fly.GAIA_ONLINE_URL | loomsiril.gaia.GAIA_ONLINE_URL | P | tests/test_gaia.py::test_constants_match_js |
| Fly.GAIA_ONLINE_COLUMNS | loomsiril.gaia.GAIA_ONLINE_COLUMNS (tuple) | P | tests/test_gaia.py::test_constants_match_js |
| Fly.GAIA_ONLINE_MAX_ROWS | loomsiril.gaia.GAIA_ONLINE_MAX_ROWS | P | tests/test_gaia.py::test_constants_match_js |
| Fly.gaiaOnlineUrl | loomsiril.gaia.online_url (JS number text via jsmath.number_str; encodeURIComponent via quote) | P | tests/test_gaia.py::test_online_url_matches_js |
| Fly.parseGaiaCsv | loomsiril.gaia.parse_csv (None for a non-table; blank NaN; wrong field count skipped) | P | tests/test_gaia.py::test_parse_csv_matches_js, test_parse_csv_rules |
| Sky.GAIA_ONLINE_TIMEOUT (60 s) | loomsiril.gaia.GAIA_ONLINE_TIMEOUT (seconds; urlopen connect timeout) | P | tests/test_gaia.py::test_constants_match_js |
| Sky.GAIA_ONLINE_STALL (30000 ms) | loomsiril.gaia.GAIA_ONLINE_STALL = 30.0 s | P | tests/test_gaia.py::test_constants_match_js |
| Sky.GAIA_ONLINE_FIRST_BYTE (120000 ms) | loomsiril.gaia.GAIA_ONLINE_FIRST_BYTE = 120.0 s | P | tests/test_gaia.py::test_constants_match_js |
| Sky.GAIA_ONLINE_TOTAL (600000 ms) | loomsiril.gaia.GAIA_ONLINE_TOTAL = 600.0 s | P | tests/test_gaia.py::test_constants_match_js |
| Sky.stallWatch, Sky.transferWatch | loomsiril.gaia.fetch_text (stall, first byte and total checks in one loop; read1 per recv since E2c) | R | tests/test_gaia.py::test_fetch_text_* (fake clock and response; no socket) |
| Sky.fetchText | loomsiril.gaia.fetch_text (urllib; GaiaError instead of null) | R | tests/test_gaia.py::test_fetch_text_* |
| Sky.queryOnline | loomsiril.gaia.query_online (injected fetch_text; GaiaError for a non-table) | P | tests/test_gaia.py::test_query_online_* |
| Sky.queryOnline origin tag ("online") | not carried: sources() returns a plain list | D | the list has no attribute slot; provenance is not needed by the pipeline |
| Sky.querySources cache/online part (the local-catalogue search is not ported here) | loomsiril.gaia.sources (source "cache", "online", "tsv"; disk cache) | R | tests/test_gaia.py::test_sources_* |
| (no JS disk cache) | loomsiril.gaia.cache_key (radius rounded up to 1e-3 since E2c), default_cache_dir, _write_atomic (.part + os.replace) | R | tests/test_gaia.py::test_sources_cache_round_trip, test_cache_key_is_stable_and_documented, test_cache_radius_rounds_up_and_the_query_uses_it |
| (fixture reader, Python only) | loomsiril.gaia.read_tsv | R | tests/test_gaia.py::test_read_tsv_fixture, test_read_tsv_blank_is_nan |
| Sky.keywordNumber | loomsiril.wcs.keyword_number (header dict, not a window; parseFloat prefix) | R | tests/test_wcs.py::test_keyword_number_strips_quotes_like_js |
| Sky.keywordWcs | loomsiril.wcs.keyword_wcs | R | tests/test_wcs.py::test_keyword_wcs_from_header_and_missing_key |
| Sky.projector (non-solved branch) | loomsiril.wcs.projector (core.tan_project) | R | tests/test_wcs.py::test_projector_puts_crval_on_crpix, test_tan_project_of_scaled_wcs_matches_js |
| Sky.unprojector (non-solved branch) | loomsiril.wcs.unprojector (core.tan_unproject) | R | tests/test_wcs.py::test_tan_unproject_of_scaled_wcs_matches_js |
| Sky.field | loomsiril.wcs.field -> {centre, radius_deg} | R | tests/test_wcs.py::test_field_radius_reaches_every_corner |
| Sky.fieldCorners | loomsiril.wcs.field_corners | R | tests/test_wcs.py::test_field_corners_are_the_pixel_corners |
| Sky.solvedScale | loomsiril.wcs.solved_scale (arcsec/px across the width) | R | tests/test_wcs.py::test_solved_scale_of_synth_header, test_solved_scale_matches_formula |
| Sky.scaledWcs | loomsiril.wcs.scaled_wcs | P | tests/test_wcs.py::test_scaled_wcs_matches_js |
| Fly.tanProject / tanUnproject of a scaled wcs | loomsiril.core.tan_project / tan_unproject (E0) | P | tests/test_wcs.py::test_tan_project_of_scaled_wcs_matches_js, test_tan_unproject_of_scaled_wcs_matches_js |
| Sky.projector / unprojector (solved window branch: celestialToImage) | not ported: a PixInsight window is the only source | D | Siril gives a wcs dict, never a window; the solved branch has no meaning in the Python core |
| Sky.iccBytes, Sky.copyWindow (RGBA drop) | loomsiril.io_tiff.read_image (RGBA drops alpha) | R | tests/test_io.py::test_read_rgba_drops_alpha |
| Render.writeTiff | loomsiril.io_tiff.write_tiff16 (16-bit, ICC tag 34675, .part then os.replace) | R | tests/test_io.py::test_tiff16_round_trip_with_icc, test_tiff16_part_file_gone_after_write |
| (no JS FITS reader; Siril bridge input) | loomsiril.io_fits.read_fits / write_fits (BITPIX 8/16/32/-32/-64, BZERO/BSCALE; top-down rows since E2c: see docs/parity/e2c.md) | R | tests/test_io.py (FITS section) |
| (wcs from the CLI solve JSON, Python only) | loomsiril.wcs.wcs_from_solution (validated) | R | tests/test_wcs.py::test_wcs_from_solution_* |


## Task E2C: row order (FITS ingest) and Gaia fetch fixes
Source: Loom v0.4.1 `script/lib/Sky.js` 136-204 (fetchText, transferWatch), `script/lib/Render.js` (row order of
images: row 0 = top, as all of Loom). Columns as in docs/PARITY.md. Status: P ported, V vectorised, R replaced by a
Python equivalent, D not ported (reason given).

| JS | Python | Status | Test |
|---|---|---|---|
| (no JS flip: Loom is top-down everywhere; FITS and Siril are the only bottom-up sources) | loomsiril.io_fits.read_fits flips planes to top-down (`plane[::-1]`) and converts the header TAN keywords | R | tests/test_io.py::test_fits_read_puts_fits_row_one_at_the_bottom_and_maps_the_wcs, test_fits_row_order_is_top_down_after_read |
| (no JS flip) | loomsiril.io_fits.write_fits writes planes and header bottom-up (drops LOOMROWS) | R | tests/test_io.py::test_fits_written_file_is_bottom_up_on_disk, test_fits_write_then_read_is_identity |
| (no JS: PC/CDELT/CROTA2 header) | loomsiril.io_fits.materialise_cd (CDi_j = CDELTi * PCi_j; CROTA2 as the CD rotation) before the flip | R | tests/test_io.py::test_fits_read_converts_pc_cdelt_form |
| (no JS) | loomsiril.wcs.flip_rows (crpix2' = h + 1 - crpix2; cd[1], cd[3] negated; involution) | R | tests/test_wcs.py::test_flip_rows_is_an_involution, test_flip_rows_flips_the_sky_mapping_for_an_asymmetric_wcs, test_flip_rows_negating_cd2_1_instead_of_cd1_2_is_wrong |
| (FITS and TIFF alignment, Python only) | io_fits.read_fits + io_tiff.write_tiff16 / read_image | R | tests/test_io.py::test_fits_and_tiff_planes_are_pixel_aligned |
| Sky.fetchText / Sky.transferWatch (read per recv) | loomsiril.gaia.fetch_text: `resp.read1(_CHUNK)` when present, so each loop turn is one recv and the stall timer measures gaps; `read` is the fallback | R | tests/test_gaia.py::test_fetch_text_slow_trickle_with_read1_is_not_a_stall, test_fetch_text_read_fallback_still_works |
| Sky.fetchText error paths (HTTPException not in the JS: JS catches any error) | loomsiril.gaia.fetch_text maps http.client.HTTPException (IncompleteRead, bad status line) to GaiaError on urlopen and read | R | tests/test_gaia.py::test_fetch_text_incomplete_read_is_a_gaia_error, test_fetch_text_bad_status_line_on_urlopen_is_a_gaia_error |
| (cache key, Python only) | loomsiril.gaia.cache_key / sources: radius rounded UP to 1e-3 deg and the query uses the same radius | R | tests/test_gaia.py::test_cache_radius_rounds_up_and_the_query_uses_it, test_cache_key_is_stable_and_documented |


## Task E3A: detect.py, assign.py
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

### Deviations (by design)

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


## Task E3B: starmodel.py
Source: Loom v0.4.1 (read only), `script/lib/Fly.js` 1825-2035 and `script/lib/Sky.js`
585-770 and 971-983. Oracle: `tools/oracle/starmodel.js` recorded to
`tests/oracle/starmodel.json` (inputs stored in each case). Tests: `tests/test_starmodel.py`.

Status: P = ported, V = vectorised port, R = replaced by a Python equivalent, D = not ported.

| JS | Python | Status | Test |
|---|---|---|---|
| Fly.spikeAngles | `spike_angles` (1-degree bins, returns ints) | P | test_spike_angles_oracle, test_spike_angles_selftest_literals |
| Fly.localPeak | `local_peak` (value is a callable `(x, y) -> float`) | P | test_local_peak_oracle, test_local_peak_ties_are_peaks |
| Fly.isFragment | `is_fragment(d, dets, value_at, px)` (plain port); `fragment_filter` (E3e) is the grid-indexed starlayer filter | P | test_is_fragment_oracle, test_is_fragment_expected_verdicts, test_fragment_filter_matches_the_old_quadratic_filter |
| Fly.attachToCores | `attach_to_cores(sprites, extras)` (creates `parts`) | P | test_attach_to_cores_oracle, test_attach_to_cores_selftest_pair |
| Fly.SPIKE_WINDOW | `SPIKE_WINDOW` | P | test_constants_match_js |
| Fly.lateralSigma | `lateral_sigma(p, step)` | P | test_lateral_sigma_oracle, test_lateral_sigma_widths_close_to_truth |
| Fly.dropBorrowedSpikes | `drop_borrowed_spikes(dets)` (in place) | P | test_drop_borrowed_spikes_oracle, test_drop_borrowed_spikes_selftest_case |
| Fly.SPIKE_FIT | `SPIKE_FIT` | P | test_constants_match_js |
| Fly.fitSpike | `fit_spike(e, T, start, sigma)` -> dict(scale, amp, reach) | P | test_fit_spike_oracle, test_fit_spike_selftest_literals |
| Fly.fitSpikePair | `fit_spike_pair(e1, e2, T, start, sigma)` | P | test_fit_spike_pair_oracle, test_fit_spike_pair_takes_stronger_side |
| Fly.SETTLE_WINDOW | `SETTLE_WINDOW` | P | test_constants_match_js |
| Fly.localProfile | `local_profile(raw, min_r, noise)` -> dict(prof, rmax, sky, settled) | P | test_local_profile_oracle, test_local_profile_selftest_literals |
| Fly.DEBLEND_CAP | `DEBLEND_CAP` | P | test_constants_match_js |
| Fly.deblend | `deblend(Mi, Msum, L, sigma)` (sigma 0 gives share a = 1) | P | test_deblend_oracle, test_deblend_selftest_rules |
| Sky.FWHM_DEFAULT | `FWHM_DEFAULT` | P | test_constants_match_js |
| Sky.HALO_MAX | `HALO_MAX` | P | test_constants_match_js |
| Sky.SPIKE_MAX | `SPIKE_MAX` | P | test_constants_match_js |
| Sky.intensity | `intensity(planes)` takes the channel planes instead of the PJSR image; float32 `out += plane / nc` | R | test_intensity_oracle |
| Sky.noiseSigma | `noise_sigma(buf)` (`ravel()[::7]`) | P | test_noise_sigma_oracle, test_noise_sigma_constant_is_zero |
| Sky.otherStar | `other_star(labels, (y, x), self_)` (pixel as a (y, x) pair; labels None = unlabelled) | R | test_other_star_oracle |
| Sky.ringStart | `ring_start(lo, dy)` | P | test_ring_start_oracle |
| Sky.ringStats | `ring_stats(buf, cx, cy, r, labels, self_)` (h, w from buf.shape since R1-d); offsets cached per r (lru_cache); `self` renamed `self_` | V | test_ring_stats_oracle, test_ring_stats_skips_other_stars_and_is_none_off_image, test_ring_stats_fast_on_200x200 |
| Sky.coreLabels | `core_labels(dets, w, h, fwhm)` -> (h, w) int32, first detection wins | V | test_core_labels_oracle, test_core_labels_first_detection_wins |
| Sky.reader | `reader(buf)` -> callable (h, w from buf.shape since R1-d), clamped; a fractional index is NaN (JS reads undefined) | P | test_reader_oracle |
| Sky.sparseMedian | `sparse_median(buf)` (core.median / core.mad on the sample) | P | test_sparse_median_oracle |
| Sky.bilinear | `bilinear(buf, x, y)` (h, w from buf.shape since R1-d); a non-finite coordinate gives 0 as the JS does | P | test_bilinear_oracle |
| Sky.smoothProfile | `smooth_profile(prof, from_)` | P | test_smooth_profile_oracle, test_smooth_profile_is_non_increasing_from_index_one |

Not in this module (other tasks): Sky.sprites, Sky 771-970 and 985-1130 (E3c); Sky.sprites
falloff helpers and detections (E3a/E3d).

Notes
- `ring_stats` takes an integer radius (the JS callers pass integers); any other value raises ValueError.
- `ring_stats` matches JS on NaN for `max` (NaN pixels never win); the median sorts with
  Python's ordering, so NaN pixels in a ring are not part of the parity claim.
- `is_fragment` stays the plain O(len(dets)) port. `fragment_filter(dets, value_at, px)` (E3e) is what
  `starlayer` calls: it returns `[d for d in dets if not is_fragment(d, dets, value_at, px)]` on the list as it is when
  the call starts. It builds a grid over the list once per call (cell = twice the largest core radius; the query checks
  the 3x3 cells about `d`, plus any detection with a non-finite position or core), applies the same predicate
  (strict `>` on flux, `b is not d`, `<= core`, `value_at(b) >= v0`, `from_gaia` first), and keeps no state between calls,
  so an in-place edit of the list is seen by the next call. Tests: test_fragment_filter_matches_the_old_quadratic_filter
  (60 random lists with ties, coincident positions, NaN positions and from_gaia), test_fragment_filter_is_right_for_each_px_on_one_list,
  test_fragment_rules_on_a_hand_made_pair, test_fragment_filter_follows_a_detection_moved_into_range_in_place,
  test_fragment_filter_follows_a_brighter_rect_grown_in_place.
- A NaN detection position raises (js_round) in `local_peak` both before and after E3e; the property test records that as a verdict of "raise".
- `spike_angles` returns degrees (bin indices), not radians, as the JS does.
- Tolerances: 1e-12 for float64 rules, 1e-6 for values computed from float32 buffers.


## Task E3C: starfit.py
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


## Task E3D: starlayer.py
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

### Caller contracts

- `neighbours[k]["source"]` must be the SAME object as the `Placed.source` it belongs to (Fly.catalogueBlend
  compares sources by identity). A copy, for example after a JSON round trip, makes every placed star look
  blended. The Siril bridge must build neighbours from the placed records' own source dicts.
- `sprites()` does not change the detections passed in: it works on copies (index, model and halo are set
  on the copies). Test: test_passed_detections_are_not_changed.

### Mutation checks (scratch copies of the tree, nothing committed)

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


## Task E4: resample.py, bloom.py, scene.py
Status: P = ported, V = vectorised port, R = replaced by a Python equivalent, D = not ported (one-line reason).
Oracle: `tests/oracle/render_axes.json` (tools/oracle/render_axes.js) and `tests/oracle/bloom.json`
(tools/oracle/bloom.js), recorded from the real JS with `LOOM_JS_DIR` pointing at Loom v0.4.1's script/ directory.

| JS | Python | Status | Test |
|---|---|---|---|
| Render.cubicWeights | resample.cubic_weights | V | test_resample::test_cubic_weights_match_js |
| Render.axisWeights | resample.axis_weights(out_n, view: AxisView, kernel) (AxisWeights; R2-c: view = the JS args after outN and kernel) | V | test_resample::test_axis_weights_match_js |
| Render.kernelTaps | resample.kernel_taps (Taps) | P | test_resample::test_kernel_taps_match_js |
| Render.tentTaps | resample.tent_taps (Taps) | P | test_resample::test_tent_taps_match_js |
| Render.resample | resample.resample | V | test_resample::test_resample_matches_js, test_one_to_one_weights_are_the_identity |
| Render.resampleRows | resample.resample_rows | V | test_resample::test_resample_rows_matches_js |
| Render.patchSample | resample.patch_sample (arrays ok) | V | test_resample::test_patch_sample_matches_js, test_patch_sample_is_zero_outside |
| Render.mipLevel | resample.mip_level | P | test_resample::test_box_radius_and_mip_level_match_js |
| Render.mipOf | resample.mip_of (cache list) | P | test_resample::test_mip_of_matches_js |
| Render.halve | resample.halve | V | test_resample::test_halve_matches_js, test_halve_and_mip_sizes |
| Render.fitPlane | resample.fit_plane | V | test_resample::test_fit_plane_matches_js |
| Render.areaMean | resample.area_mean | P | test_resample::test_area_mean_matches_js |
| Render.boxBlur | resample.box_blur (cumulative-sum box, in place) | V | test_resample::test_box_blur_matches_js, test_box_blur_holds_the_edge |
| Render.boxRadius | resample.box_radius | P | test_resample::test_box_radius_and_mip_level_match_js |
| Render.shrinkAxis | resample.shrink_axis (ShrinkAxis) | P | test_resample::test_shrink_axis_matches_js, test_shrink_weights_sum_to_one_over_the_whole_image |
| Render.normaliseWeights | resample.normalise_weights | P | test_resample::test_normalise_weights_matches_js |
| Render.shrinkPatch | resample.shrink_patch | V | test_resample::test_shrink_patch_matches_js, test_shrink_patch_inside_image_keeps_mean_level |
| Render.BLOOM_TIGHT | bloom.BLOOM_TIGHT | P | test_bloom::test_constants_match_js |
| Render.BLOOM_WIDE | bloom.BLOOM_WIDE | P | test_bloom::test_constants_match_js |
| Render.BLOOM_GLARE | bloom.BLOOM_GLARE | P | test_bloom::test_constants_match_js |
| Render.BLOOM_GAINS | bloom.BLOOM_GAINS (tuple) | P | test_bloom::test_constants_match_js |
| Render.saturateStars | bloom.saturate_stars (in place) | V | test_bloom::test_saturate_stars_matches_js |
| Render.starsToHeadroomJs | bloom.stars_to_headroom (in place, scalar or map) | V | test_bloom::test_stars_to_headroom_matches_js |
| Render.starsToHeadroom | - | D | PixInsight ImageOp path; the Python path is starsToHeadroomJs |
| Render.bloomSigmas | bloom.bloom_sigmas | P | test_bloom::test_bloom_sigmas_match_js |
| Render.whitenPastWhite | bloom.whiten_past_white (in place) | V | test_bloom::test_whiten_past_white_matches_js, test_whiten_changes_only_past_white_pixels |
| Render.pastWhite | bloom.past_white (None when none) | V | test_bloom::test_past_white_matches_js |
| Render.bloomWhiten | - | D | PixInsight ImageOp path (whiten inside Render.bloom) |
| Render.bloomJs | bloom.bloom (per-pixel maths, vectorised) | V | test_bloom::test_bloom_js_matches_js, test_nothing_past_white_means_bloom_changes_nothing |
| Render.bloom (dispatcher) | bloom.bloom (amount <= 0 or NaN: no-op) | R | test_bloom::test_bloom_dispatcher_matches_js, test_amount_zero_is_a_no_op |
| Render.blurImage, Render.box3Kernel | - | D | PixInsight separable convolution; the Python path is boxBlur |
| Render.channels | scene.channels | P | test_scene::test_scene_takes_the_residual_as_given_and_masks_the_stars |
| Render.scene | scene.scene(...) -> types.Scene | P | test_scene::test_scene_shapes_and_projection, test_scene_p0_is_none_off_projection |
| Render.blend | scene.blend (returns new planes) | V | test_scene::test_blend_is_alpha_mix_and_leaves_b_untouched |
| Render.blend (PixInsight path) | - | D | PixInsight ImageOp path; the JS-loop path is ported |
| Render.layerAxis | scene.layer_axis | P | test_scene::test_small_helpers |
| Render.orDefault | scene.or_default | P | test_scene::test_small_helpers |
| Render.upscaleStars | scene.upscale_stars | P | test_scene::test_small_helpers |
| Render.starSide | scene.star_side | P | test_scene::test_small_helpers |
| Render.EXCESS_MAX | scene.EXCESS_MAX | P | test_scene::test_composite_is_screen_and_excess_only_for_hdr |
| Render.compositeJs | scene.composite(Sc, Tc, hdr) | V | test_scene::test_composite_is_screen_and_excess_only_for_hdr |
| Render.composite (PixInsight path) | - | D | PixInsight ImageOp path; compositeJs is the Python path |
| Render.rotateScene | scene.rotate_scene (np.rot90 k=-1) | V | test_scene::test_rotate_scene_matches_js, test_rotate_scene_four_quarter_turns_are_the_identity |
| Render.scaledScene | scene.scaled_scene (cache in sc.cache["scaled"]) | V | test_scene::test_scaled_scene_positions_and_shapes, test_scaled_scene_is_cached_for_the_same_d |

### Deliberate differences

- Weights and stored pixels: the JS keeps axis weights and every pass in Float32Array. The port rounds at the same
  points to float32 (`axis_weights`, the two passes of `resample`, every box-blur pass, bloom planes), so the
  numbers match the recorded JS at the stated tolerances.
- Vectorisation: `resample` applies each axis as a sparse map over the tap slots (same taps, same weights, same
  summation order) rather than the planned dense (out_n, src_n) matrix product. A 1080p channel would otherwise cost
  about 13 GFLOP per pass. The results are the same taps and weights.
- `composite` caps the excess at `EXCESS_MAX` (1e30). The JS compositeJs has no cap; no pixel reaches it, and the
  cap matches the PixInsight path.
- `scaled_scene` and `rotate_scene` keep every field the JS copies unchanged (`centre`, `placed`, `d`, `source`), and
  rewrite only the fields the JS rewrites (the sprite radius is scaled as the JS does).
- `shrink_patch`, `resample` and `patch_sample` take numpy planes shaped (h, w); the JS flat index is `plane[y, x]`.


## Task E5: sprites.py, frame.py
Ported from Loom `script/lib/Render.js` (v0.4.1, read-only checkout). Modules: `loomsiril/sprites.py`,
`loomsiril/frame.py`. Oracle: `tools/oracle/frame.js` -> `tests/oracle/frame.json` (a 48 x 32 hand-made scene
with three sprites; its inputs are stored in the JSON). Status: P = ported, V = vectorised port, R = replaced by a
Python equivalent, D = not ported (reason given).

| JS | Python | Status | Test |
|---|---|---|---|
| Render.drawSprite | draw_sprites (called with one-element lists) | R | test_sprites.py::test_draw_sprites_equals_per_pixel_reference |
| Render.drawSprites | sprites.draw_sprites | V | test_sprites.py::test_draw_sprites_equals_per_pixel_reference, ::test_draw_sprites_footprint_equals_per_pixel_reference; test_frame.py oracle |
| Render.spriteRow | folded into sprites.draw_sprites (all pixels of the bounds at once) | V | test_sprites.py (reference) |
| Render.howSeen | sprites._how_seen | P | test_sprites.py (reference) |
| Render.howMipLevel | sprites._how_mip_level (uses resample.mip_level) | P | test_sprites.py (reference) |
| Render.spriteContext | sprites._sprite_context | P | test_sprites.py (reference) |
| Render.spikeSource | sprites._add_spike_sample (the nearest-axis choice, vectorised over samples) | V | test_sprites.py::test_draw_sprites_equals_per_pixel_reference[True] |
| Render.spriteBounds | sprites.sprite_bounds | P | test_sprites.py::test_sprite_bounds_matches_js, ::test_sprite_bounds_clipped_matches_js |
| Render.spritePixels | folded into sprites.draw_sprites (sample sums divided by nx*ny per pixel) | V | test_sprites.py (reference) |
| Render.spikeSums | sprites._sample_sums + _add_spike_sample | V | test_sprites.py::test_draw_sprites_equals_per_pixel_reference[True] |
| Render.glowSums | sprites._sample_sums + _add_glow_sample | V | test_sprites.py::test_draw_sprites_equals_per_pixel_reference[False] |
| Render.addGlowSample | sprites._add_glow_sample | V | test_sprites.py (reference) |
| Render.glowSamples | sprites.glow_samples (per-pixel count array, 0 = full) | V | test_sprites.py::test_draw_sprites_footprint_equals_per_pixel_reference |
| Render.addSprites | sprites.add_sprites | P | test_sprites.py::test_add_sprites_into_zero_planes_matches_js |
| Render.headroomMap | sprites.headroom_map (bump painted as a box, np.maximum) | V | test_frame.py::test_headroom_map_matches_js_oracle (E9a: oracle, t 0.3 and 1); test_frame.py::test_approaching_star_rises_above_sdr_white_in_pq_and_hlg |
| Render.starKey | sprites.star_key (ra, dec to 6 decimals) | P | test_frame.py (HDR) |
| Render.placeSprite | sprites.place_sprite (returns PlacedSprite or None) | P | test_sprites.py::test_place_sprite_matches_js |
| Render.seenShares | sprites.seen_shares | P | test_sprites.py::test_seen_shares_matches_js |
| Render.splitPatches | sprites.split_patches (cache key split:c on sprite.cache; CoreSprite) | V | test_sprites.py::test_split_patches_matches_js |
| Render.glowPatch | sprites.glow_patch (cache key glow:c) | P | test_sprites.py::test_glow_patch_matches_js |
| Render.drawPlaced | sprites.draw_placed | P | test_frame.py oracle (frame0, frame03, frame1, frameBlur) |
| Render.placedChannels | sprites.placed_channels | P | test_frame.py oracle; test_sprites.py::test_empty_model_spikes_takes_the_core_path (E9a: an empty modelSpikes) |
| Render.drawPlacedSpikes | sprites.draw_placed_spikes | P | test_frame.py oracle (spike sprite in the scene) |
| Render.coreRadius | sprites.core_radius (core 0: half the rect, at least 1) | P | test_sprites.py::test_core_radius_matches_js, ::test_core_radius_for_core_zero_uses_rect |
| Render.shutterPath | sprites.shutter_path | P | test_sprites.py::test_shutter_path_matches_js, ::test_shutter_path_still_is_one_point |
| Render.anyLit | not ported: a bounds test against the output frame does the same job | D | - |
| Render.reachTests | not ported: exact speed-up for a scalar interpreter; a numpy bounds mask does the same job (plan 7.1) | D | - |
| Render.footprintPays | sprites.footprint_pays. Planned as D, but kept: it decides whether High/Medium count samples per pixel, and the JS result depends on it | P | test_sprites.py::test_draw_sprites_footprint_equals_per_pixel_reference |
| Render.darkReach | not ported (plan 7.1 speed-up) | D | - |
| Render.darkMaps | not ported (plan 7.1 speed-up) | D | - |
| Render.SKIP_DARK | not ported (plan 7.1 speed-up) | D | - |
| Render.darkMap | not ported (plan 7.1 speed-up) | D | - |
| Render.darkRow | not ported (plan 7.1 speed-up) | D | - |
| Render.nearLight | not ported (plan 7.1 speed-up) | D | - |
| Render.patchReach | not ported (plan 7.1 speed-up) | D | - |
| Render.rayDistance | not ported (plan 7.1 speed-up) | D | - |
| Render.reachTest | not ported (plan 7.1 speed-up) | D | - |
| Render.RenderOptions (the opts.* fields) | frame.RenderOptions (defaults: see below) | P | test_frame.py oracle |
| Render.frame | frame.frame(sc, t, opts, out_w, out_h, crop) | P | test_frame.py::test_frame_matches_js_oracle, ::test_frame_zero_equals_screen_of_scene, ::test_frame_zero_equals_screen_of_scene_three_channels, ::test_frame_pq_matches_js_oracle (E9a: PQ frames at t 0, 1) |
| Render.frameImage | frame.frame_image (without the logo; output transform when set) | P | test_frame.py::test_frame_image_without_output_is_the_composite |
| Render.sceneFor | frame.scene_for (turned copy kept in sc.cache["turned"]) | P | test_frame.py::test_scene_for_turns_once_and_keeps_the_turned_copy |
| Render.logoLayer, Render.addLogo, Fly.logoRect, logoFade (the logo) | logo.logo_layer, logo.add_logo, logo.logo_rect, logo.logo_fade; frame_image paints it before the output transform | P | test_logo.py (oracle logo.json), ::test_frame_image_paints_the_logo_where_it_goes_and_nowhere_else |

### Defaults of RenderOptions

Where the JS reads an unset option it gets undefined, which gives NaN or the JS default of its test. The Python
defaults are:

- `travel` 0.0; `easing` "smoothstep" (any non-"linear" value is smoothstep in the JS too).
- `growth` GROWTH_DEFAULT (0.15). The JS has no default (1 + undefined gives NaN); every caller sets it.
- `twinkle` 0.0 (the JS: amount not above 0 gives exactly 1).
- `backdrop_motion` None -> BACKDROP_MOTION_DEFAULT; `kernel` "bicubic"; `star_quality` None -> "highest".
- `star_saturation` None -> 1; `bloom` None -> 0; `peak` None -> HDR_PEAK_DEFAULT (1000 nits); `K` None -> 1.
- `t`, `duration`, `frame_dt` 0.0; `motion_blur`, `brightening`, `star_hdr`, `coarse_mips` False.

### Notes on the vectorisation

- Every output pixel of a sprite's bounds is evaluated at once. The loops are over the sample slots (ny x nx, a
  handful) and over sprites, spikes and channels. There is no Python loop over pixels in sprites.py, except the
  headroom paint, which loops over stars and paints a box per star with numpy.
- High and Medium (footprint): the pixels are grouped by their (nx, ny) sample count (`glow_samples`, 0 = full count)
  by `sample_groups`: one counting pass and one stable argsort per draw call, one pass per group. This is exact: the
  same count per pixel as the JS. It replaces `np.unique(axis=0)` per draw (E5b, below); the old grouping is kept as a
  private reference in tests/test_sprites.py and the sums are bit-identical on random inputs.
- Highest: a single group with the full (nx, ny) grid.
- Sums are float64 and are added into the float32 planes once per draw, as the JS adds into Float32Arrays.

### Measurements

- sRGB -> sRGB identity (OutputTransform, output transform ON, SDR) over all 65536 16-bit values: max error
  4.97e-7 in [0, 1], i.e. 0.033 of a 16-bit step (zero values above 1/65535). Frame 0 of a 1:1 render with the sRGB
  transform on therefore stays within 1/65535, and E6 does not need to skip the identity transform.
- Perf: `tests/test_frame.py::test_perf_640x360`, one 640 x 360 frame of a 30-sprite synthetic scene at full
  scale, `star_quality` "highest", second call timed with time.perf_counter: **0.047 s** in the Docker image
  (python 3.12, LOOMSIRIL_PERF=1) in the Docker image, measured at commit 72a848f (the code commit of this branch; the later
  commit only changes this document). Budget 2.0 s. Local runs skip it without LOOMSIRIL_PERF=1.
  E9a (fly/e9a, commit d6d2ed9): the perf test also asserts the timed frame drew its sprites (the same frame of the
  scene with `sprites=[]` differs by more than 1e-3); re-measured in Docker at d6d2ed9: **0.031 s**, 51 passed in
  test_frame.py and test_sprites.py with LOOMSIRIL_PERF=1. A mutant with the sprites not drawn fails that assertion.

### E9a: what the tests pin since E5

- Oracle (tools/oracle/frame.js, re-recorded): `headroomMap` (Render.headroomMap at t 0.3 and 1, starHdr, peak 1000,
  with the stars placed by addSprites) and `framePQ` (Render.frame with Fly.outputTransform SRGB -> pq, peak 1000, at
  t 0 and 1, with the transform's MaxCLL and MaxFALL after each frame). The HDR path is pinned by numbers, not only
  by behaviour. The other cases are unchanged.
- The near-star test expects detection + (projection of the moved star - projection at frame 0), with Fly.moved and
  the scene projection, as selftest fly.render does; the drawn light's centroid is within 0.5 px.
- HLG: the star's peak exceeds the HLG code of SDR white (0.75 through this transform) and is at most 1.
- PQ: frame 0 equals the PQ transform of screen(S, T) within 1e-6; MaxCLL after an approaching star is above 203 nits
  and at most the peak (selftest fly.hdr).
- placed_channels: an empty modelSpikes array takes the JS path (no spikes for the channel, glow = the photograph),
  with no exception. The check is `model_spikes is not None` plus a length guard (the JS reads modelSpikes[cc], which
  is undefined past the end of an array).

### Perf note (E5b, sprite grouping)

- The hotspot was `np.unique(..., axis=0)` on the footprint (nx, ny) pairs, run once per `draw_sprites` call (852
  calls in the acceptance-test scene): the void-view sort was 11.3 s of a 14.3 s frame at t = 0.5. It is now `sample_groups`
  (one bincount and one stable argsort of packed integer keys); with a constant count there is no grouping at all.
  Output is unchanged: the same groups in the same order, and bit-identical sums (tests in test_sprites.py).
- Draw calls are still one per sprite per shutter-path point, and the (nx, ny) grid is the same as before, so the
  remaining cost is the sample sums (`draw_placed_spikes` included), not the grouping.
- Docker (image from the E6 tree e6b5700 with sprites.py of 3faf538 before, and with E5b's sprites.py after;
  python 3.12, 3 frames at t = 0 / 0.5 / 1, 1 frame each, per-frame seconds, no profiler; probe
  E6 perf probe without cProfile and with fps 3):

  | case | before | after |
  |---|---|---|
  | Acceptance-test CLI scene: 320x180 input x6, 20 stars, fwhm 2.5, D 1000, nc 3 | 1.38 / 17.07 / 8.57 s | 0.48 / 2.39 / 2.12 s |
  | native 1920x1080, 30 stars, fwhm 12, D 1000, nc 3 | 0.82 / 4.02 / 4.01 s | 0.44 / 1.07 / 1.70 s |

  The 1080p target (< 10 s per frame) is met in both cases. Timings are one run per case on a shared host, so treat
  them as order-of-magnitude.

### Not in this task

- The logo (status D above). Row-order and FITS handling are in E2c/E7/E6; this module takes top-down planes.


## Task E6: pipeline.py, cli.py
Base fly/final@0d67ba1. Source: Loom v0.4.1 `script/FlyThrough.js` (JS line numbers below), `lib/Sky.js`, `lib/Render.js`.
Status: P = ported, V = vectorised port, R = replaced by a Python equivalent, D = not ported (reason given).

| JS | Python | Status | Test |
|---|---|---|---|
| FlyThrough.renderFinal (58-91) | pipeline.render_final(req: FinalRender, ctx: RunContext) (signature since R3-a) | P | test_pipeline.py::test_frames_are_numbered_five_digit_16_bit_and_complete |
| FlyThrough.prepareFolder (37-48) | pipeline._prepare_folder | P | test_pipeline.py::test_stale_frames_are_cleared_and_other_files_kept |
| FlyThrough.FRAMES_RECORD (93) | pipeline.FRAMES_RECORD | P | test_pipeline.py::test_frames_json_records_the_signature |
| FlyThrough.PARTIAL_SUFFIX (95) | pipeline.PARTIAL_SUFFIX | P | test_pipeline.py::test_clear_frames_removes_only_loom_files |
| FlyThrough.isPartialFrame (97-102) | pipeline.is_partial_frame | P | test_pipeline.py::test_clear_frames_removes_only_loom_files |
| FlyThrough.recordMatches (105-110) | pipeline._record_matches | P | test_pipeline.py::test_second_run_reuses_then_only_a_missing_frame_is_written |
| FlyThrough.framesReady (113-119) | pipeline._frames_ready | P | test_pipeline.py::test_second_run_reuses_then_only_a_missing_frame_is_written |
| FlyThrough.qualityScene (125-135) | pipeline._quality_scene | P | test_pipeline.py::test_frames_are_numbered_five_digit_16_bit_and_complete |
| FlyThrough.presetJob (137-143) | pipeline._preset_job | P | test_pipeline.py::test_portrait_turns_a_landscape_preset_into_its_own_folder |
| FlyThrough.renderFrames (149-155) | pipeline._render_frames (icc chosen by _frame_icc) | P | test_pipeline.py::test_sdr_frames_carry_the_srgb_profile |
| FlyThrough.renderFrameRange (157-179) | pipeline._render_frames | P | test_pipeline.py::test_cancel_after_two_frames_keeps_them_and_makes_no_video |
| FlyThrough.loopImage (511-521) | pipeline.loop_image(pr, i, opts, cache) (signature since R3-a) | P | test_pipeline.py::test_loop_image_crossfades_into_the_pre_roll |
| FlyThrough.presetOptions (220-230) | pipeline._preset_options, pipeline._is_srgb | P (D1) | test_pipeline.py::test_hdr_frames_carry_no_profile |
| FlyThrough.finishPreset (231-248) | pipeline._finish_preset | P | test_pipeline.py::test_no_video_gives_the_exact_ffmpeg_command |
| FlyThrough.encodeArgs (250-256) | pipeline._encode_args | P | test_pipeline.py::test_no_video_gives_the_exact_ffmpeg_command |
| FlyThrough.encodeProgress (258-266) | pipeline._finish_preset (on_output closure) | P | test_pipeline.py::test_no_video_gives_the_exact_ffmpeg_command |
| FlyThrough.clearFrames (281-297) | pipeline.clear_frames | P | test_pipeline.py::test_clear_frames_removes_only_loom_files |
| FlyThrough.removeQuietly (299-302) | pipeline._remove_quietly | P | test_pipeline.py::test_clear_frames_removes_only_loom_files |
| FlyThrough.headerCentre (307-315) | presets.header_centre (E1b) + cli._prepare | R | test_cli.py::test_no_target_takes_the_header_centre_or_the_image_centre |
| FlyThrough.headerObject (317-322) | cli._prepare (FITS OBJECT keyword) | R | test_cli.py::test_wcs_fits_goes_through_read_fits |
| FlyThrough.clusterDistance (427-462) | pipeline.run (distance block) + pipeline.cluster_distance | P (D8 without a target) | test_pipeline.py::test_cluster_distance_on_ic1396 |
| Sky.clusterSources (456-458) | pipeline.run (Gaia sample, 3 cluster radii) | P | test_pipeline.py::test_no_distance_found_is_an_error_asking_for_the_distance |
| FlyThrough.identify (333-355), findDistance (357-375) | pipeline.run | R (no NGC/IC catalogue; the target is given) | test_pipeline.py::test_galaxy_is_a_fixed_backdrop |
| FlyThrough.build (463-509) | pipeline.build_scene (+ parallax.place_stars, starlayer.sprites, scene.scene) | P | test_pipeline.py::test_build_scene_neighbours_share_the_placed_source_objects |
| Sky.workingCopy (1302-1338) | pipeline.working_copy | P (D4) | test_pipeline.py::test_working_copy_shrinks_a_wide_image_and_scales_the_wcs |
| Sky.splitStars clamp (543-567) | pipeline.clamp_to_image | P (D2) | test_pipeline.py::test_clamp_to_image_takes_the_minimum |
| Sky.stretchIfLinear / Fly.looksLinear | cli._prepare (warning) + presets.looks_linear | R | test_cli.py::test_a_linear_input_is_warned_about |
| Sky.projector (488-494) | pipeline._projector | R (returns None when cos c <= 0) | test_pipeline.py::test_build_scene_neighbours_share_the_placed_source_objects |
| Sky.field (506-519) | pipeline._field | P | test_pipeline.py::test_build_scene_neighbours_share_the_placed_source_objects |
| Sky.requireSources (205-210) / Sky.querySources | pipeline._gaia_sources(job, query: GaiaQuery, ctx) -> gaia.sources | R (the error text is raised by the caller) | test_pipeline.py::test_online_uses_the_injected_fetch_only |
| Render.scaledScene / Render.sceneFor | scene.scaled_scene / frame.scene_for | P (E4, E5) | test_pipeline.py::test_frames_are_numbered_five_digit_16_bit_and_complete |
| Render.writeTiff (1326-1350) | io_tiff.write_tiff16 (.part then rename) | R | test_pipeline.py::test_frames_are_numbered_five_digit_16_bit_and_complete |
| Render.srgbIcc | colour.srgb_icc_bytes | R | test_pipeline.py::test_sdr_frames_carry_the_srgb_profile |
| Render.encode / Fly.ffmpegArgs / Fly.commandLine / Fly.videoName | encode.encode / ffmpeg_args / command_line / video_name (E1b) | P | test_e2e.py::test_e2e_frames_and_command |
| Fly.frameSignature | presets.frame_signature (E1b), keys without colour and sceneKey | P | test_pipeline.py::test_second_run_reuses_then_only_a_missing_frame_is_written |
| Fly.HDR_DEFAULT_TRANSFER (1459-1460) | pipeline.HDR_DEFAULT_TRANSFER | P | test_pipeline.py::test_hdr_frames_carry_no_profile |
| Fly.presetSpec / frameCount / timeAt / presetCrop / qualityScale / workingScale | presets (E1b) | P | test_pipeline.py::test_portrait_turns_a_landscape_preset_into_its_own_folder |
| Fly.defaultTravel / clampTravel | parallax.default_travel / clamp_travel (E1a) | P | test_pipeline.py::test_portrait_turns_a_landscape_preset_into_its_own_folder |
| Dialog defaults (1282-1380) | pipeline.DIALOG_DEFAULTS, cli defaults | P | test_cli.py::test_argument_errors_exit_2 |
| FlyThrough.paintLayer (1-bit preview bitmap) | -- | D: preview painting, not part of a frame file | -- |
| FlyThrough.readLogo (FlyThrough.js:198) | logo.read_logo (PNG, TIFF, FITS; read once, off the Tk thread) | R (a pure-Python reader instead of PixInsight's file formats) | test_logo.py::test_png_*, test_tiff_*, test_fits_is_read_top_down |
| Render.logoLayer / Render.addLogo / Fly.logoRect / Fly.logoFade (Render.js:579, 629; Fly.js:1226, 1220) | logo.logo_layer / logo.add_logo / logo.logo_rect / logo.logo_fade | P | test_logo.py::test_logo_layer_matches_the_js, test_add_logo_matches_the_js, test_logo_rect_matches_the_js, test_logo_fade_matches_the_js (tests/oracle/logo.json) |
| FlyThrough.draftOptions / draft preview | draft.py (the Draft button) | P (own module, tested in test_draft.py) | test_draft.py |
| FlyThrough.estimateMs (FlyThrough.js:719) / refreshEstimate (2006) | fly_video.estimate_ms / fly_video.estimate_text | P | test_fly_video.py::test_estimate_ms_is_frames_times_megapixels_times_speed, test_estimate_text_rounds_like_the_js_and_is_blank_without_a_measurement |
| FlyThrough.measureSpeed (FlyThrough.js:707) | pipeline.Result.ms_per_mp (the speed of the frames a render drew, kept as the `ms_per_mp` option) | R (no separate 1920x1080 timing frame) | test_fly_video.py::test_a_render_measures_its_speed_for_the_estimate |
| FlyThrough.solveUnsolved / solveByHints / Sky.solveBlind | -- | D: plate solving (plan 7.5); the CLI takes the WCS as a file | -- |
| FlyThrough.stageOf (JS progress wrapper) | progress callback argument of pipeline.run | R | test_pipeline.py::test_cancel_after_two_frames_keeps_them_and_makes_no_video |

### Deviations (documented; each one has a test)

- **D1, sRGB output transform skipped (harmless).** The JS always calls `Fly.outputTransform` (`presetOptions`). For SDR
  output from an sRGB source (no profile, or a parametric sRGB profile with the sRGB matrix and curve) this port skips
  it: the transform is the identity by definition, and skipping it avoids a 0.03-0.08 LSB LUT error at frame level
  (measured; the Rec.709 matrix is not an exact identity in float). The LUT does not cause the frame-0 offset: with the
  transform on, frame 0 moves by at most 0.001 LSB. Strict JS parity would cost nothing (the frame-0 acceptance check passes either way).
- **D2, `--image` clamps, `--stars` is used as given.** The JS `Sky.splitStars` clamps `starless := min(starless, image)`
  and then always derives `stars := unscreen(image, starless)` (Steps.deriveStarsByUnscreen); there is no stars input.
  This port keeps the `--stars` file as given and uses `--image` for the clamp and the colour profile. The cause of the
  difference is the 16-bit quantisation of three layers rounded separately: `image16` differs from `screen(S16, T16)` by
  up to 1.33 LSB on a noise-free field (also at 480 x 270). Deriving the stars from `image16` makes frame 0 equal
  `image16` exactly, and 1.30 LSB away from `screen(S16, T16)`, which the frame-0 acceptance check uses. The
  user-visible difference is at most about 1.3 LSB of 16-bit. The frame-0 check passes with the given stars only just (1.00 LSB).
  `pipeline.clamp_to_image` is the clamp; the derivation is not implemented.
- **D3, no default distance (matches the JS).** When no cluster and no illuminating star gives a distance and no
  `--distance` is given (not a galaxy), `run` raises `ValueError` ("... give --distance PC"); the CLI prints one error
  line and exits 1. The JS leaves `D` null (`findDistance`) and `Dialog.prepareScene` refuses to render ("Type the
  nebula's distance in parsecs first."). The acceptance tests' CLI helper passes `--distance` for the synthetic field, which has no
  cluster or bright star.
- **D4, working copy.** An image wider than the 16:9 working size is shrunk with `resample.shrink_patch` (area average,
  height and width rounded up, not Resample's own rounding); the WCS is scaled with `wcs.scaled_wcs`; a FWHM is scaled;
  detections passed in are dropped and found again on the working copy, as the JS does (it detects on its working copy).
- **D5, gaia "cache" may go online on a miss** (as Sky.querySources does). `gaia=online` is refused under pytest in the
  CLI only; the pipeline takes an injected `fetch_text` and never reaches the network in tests.
- **D6, the camera aims at the image centre** (the JS `aim = field.centre`). `target` (`--target`, `--target-pixel`, the
  header centre) sets the distance (cluster, illuminator), not the direction.
- **D7, MaxCLL/MaxFALL on resumed HDR frames** are measured only while frames are drawn; reused frames add nothing. The JS
  behaves the same way.
- **D8, a cluster is searched without a catalogued target.** With no `--target`, the CLI takes the header centre or the
  image centre, and `run` then searches for the cluster round it. The JS does no cluster search without a catalogued
  target (`FlyThrough.clusterDistance` runs only for the NGC/IC pick). This port searches so that a plain FITS or TIFF
  input can find its distance.

### Ranges and limits

- No distance and no `--distance`: exit 1 with one error line (D3).
- `--duration` at most 600 s (CLI argparse error); a clip of 100000 frames or more is refused (five-digit frame names).
- Frames are `frame_%05d.tif` in `<out>/<preset id>/` (`_vertical` for a turned preset); the video sits beside that folder.
- Without `--video`, or with `--video` and no ffmpeg on PATH, the frames are written and the exact command is printed
  on stdout as `ffmpeg command: ...`.

### Not run in this task

- The ffmpeg-marked test (`test_e2e.py::test_video_is_encoded_when_ffmpeg_exists`) is skipped where ffmpeg is absent;
  it runs in the Docker image.
- The full suite runs in the Docker image; the targeted files run locally.


## Task E7: siril_bridge.py, Loom/loom_fly_through.py
Status: P = ported, V = vectorised port, R = replaced by a Python equivalent, D = not ported (reason).

| JS | Python | Status | Test |
|---|---|---|---|
| Sky.splitStars (Sky.js 543-567) | siril_bridge.split_stars | P | test_siril_bridge.py::test_split_stars_recovers_the_stars_layer, test_split_stars_clamps_starless_to_image |
| Steps.deriveStarsByUnscreen (Steps.js 3226) | core.unscreen (E0), used by split_stars | P | test_split_stars_recovers_the_stars_layer |
| Sky.keywordWcs (Sky.js 471) | wcs.keyword_wcs via siril_bridge.wcs_from_header | R | test_wcs_from_header_reads_tan_keywords_and_none_when_missing |
| Fly.headerCentre (Fly.js 1116) | presets.header_centre via siril_bridge.target_from_header | R | test_target_from_header_is_header_centre_ra_dec |
| Sky.copyWindow (Sky.js 519) | D: no window copies in sirilpy; pixels come from get_image_pixeldata, layers are planes | D | - |
| Steps.removeStars (Steps.js 3252) | siril_bridge.run_starnet (StarNet2 CLI, subprocess) | R | test_run_starnet_* ; test_starnet_version_* ; test_orientation_asymmetric; starnet-marked test (Docker) |
| Sky.detections (Sky.js 575) | siril_bridge.detections_from_psf (Siril PSF stars) | R | test_detections_from_psf_order_rect_and_skip_non_finite |
| FlyThrough dialog (FlyThrough.js 698-776, PRESET_LABELS, PRESET_ORDER, FPS, LOOPS, STAR_QUALITIES) | Loom/loom_fly_through.py LoomFlyDialog, constants from presets | R | test_dialog_class_only_where_tk_works (skips without a display) |
| Fly-Through options (the last dialog values) | siril_bridge.save_options / load_options (JSON) | R | test_options_round_trip_in_tmp_path, test_options_missing_or_corrupt_give_empty |

### Findings (measured on siril-cli 1.4.4 in the Docker test image)

- **Pixel rows: bottom-up, confirmed.** `get_image_pixeldata` returns the file's order: array row 0 is the bottom of
  Siril's display (a bright star at FITS row 90 of 100 is array row 90). The bridge flips the planes once.
  `ROWORDER = TOP-DOWN` in the file is not applied by Siril to the array (the bridge's single flip plus `flip_rows`
  stays consistent for both).
- **PSF star coordinates: pixel-edge convention, y from the top.** `xpos = column + 0.5` and `ypos = distance from the
  top + 0.5` (star at array row 90 of 100 gives ypos 9.5005, xpos 30.4989). The bridge maps them to the top-down
  array as `x = xpos - 0.5`, `y = ypos - 0.5`, with no height flip (`SIRIL_PSF_ORIGIN = 0.5`).
  `pix2radec`/`radec2pix` use the same convention (`pix2radec(30, 90)` gives dec 19.96 for CRPIX2 50.5, CRVAL2 20).
- **The header has no CD keys.** After plate solving, Siril's header dict carries `CDELT1/2` and `PC1_1..PC2_2`
  (CD = CDELT_i * PC_ij), not `CD1_1..CD2_2`. `siril_bridge` materialises CD with `io_fits.materialise_cd` (the
  CROTA2 fallback included) before anything reads the WCS; `top_down_header` writes explicit CD keys and drops PC,
  CDELT and CROTA2. The CD-file case is still accepted unchanged.
- **connect()** raises `SirilConnectionError` on failure (the stub does too). `main()` prints to stderr without a
  message box when there is no connection.
- **StarNet2 CLI: checked against the real tool.** The bridge runs `starnet2 --input <in.fits> --output <starless.fits>`
  (`starnet_args`), after `starnet2 --version` (`starnet_version`). Input and output are float32 FITS through `io_fits`,
  so no TIFF library is needed. Loom's own unscreen makes the stars layer. The `starnet`-marked test runs the real
  binary on a synthetic 512 x 512 field (mono and RGB) and checks the raw output: shapes, FITS files removed, overshoot
  <= 2e-2, at least half of the star light removed, and removed light at the injected positions (median offset <= 1.5 px,
  not mirrored). No StarNet output is kept as a reference.
- **Stub fidelity.** tests/sirilpy_stub mirrors the names and signatures of the real package. It now follows the
  measured conventions: edge-convention `pix2radec`/`radec2pix` with y from the top, CDELT+PC headers, `connect()`
  raising. Its stars are whatever the test sets; their coordinates must be given in Siril's convention.
- **The script has NOT been run inside Siril.** siril-cli was used only for the
  probes above (headless, no GUI). The dialog and the script's own flow are unverified in a real run.

### Not in the bridge

- Header keywords: CRVAL1/2, CRPIX1/2 and either CD1_1..CD2_2 or CDELT1/2 with PC1_1..PC2_2 (Siril's form);
  CROTA2 is accepted through `materialise_cd`. Other PC/CDELT layouts (per-axis CDELT with PC on a non-standard axis
  order) are not checked beyond what io_fits does.


## Task E8: Docker, CI
E8 ports no JS function. These rows record how the JavaScript's ffmpeg discovery maps onto the test
environment; they are environment notes, not ports.

| JS | Python | Status | Test |
|---|---|---|---|
| Render.FFMPEG_SETTING (PJSR settings key for the ffmpeg path, Render.js ~1364) | no PJSR settings store: ffmpeg comes from `--ffmpeg PATH` or the PATH; the Docker image installs Debian's ffmpeg | R | tests marked `ffmpeg` (skip without ffmpeg on the PATH) |
| Render.ffmpegEncoders / Render.isFfmpeg (probe of `ffmpeg -encoders` and `ffmpeg -version`, Render.js ~1409-1432) | encoder probe belongs to the encode module (E1b); not re-done here | D | none in E8 |


## Task E9A: HDR frame oracle and frame tests
Test-only follow-up to E5 (`docs/parity/e5.md`, where the rows are). Behaviour is unchanged except one line in
`loomsiril/sprites.py` (placed_channels, L4), which keeps the JS result for an empty modelSpikes array. Status:
P = ported, V = vectorised port, R = replaced by a Python equivalent, D = not ported (reason given).

| JS | Python | Status | Test |
|---|---|---|---|
| Render.headroomMap | sprites.headroom_map | P | test_frame.py::test_headroom_map_matches_js_oracle[0], [1] (oracle, t 0.3 and 1; starHdr, peak 1000) |
| Render.headroomMap (core radius sets the bump: max(reach, rc*g + 2 px)) | sprites.headroom_map | P | test_frame.py::test_headroom_map_core_radius_case_matches_js_oracle (oracle, headroomMapCore, growth 6) |
| Render.frame (PQ output) | frame.frame with an OutputTransform in opts.output | P | test_frame.py::test_frame_pq_matches_js_oracle (oracle, t 0 and 1, to 1e-6) |
| Render.frame (PQ frame 0 is the image mapped to PQ) | frame.frame | P | test_frame.py::test_pq_frame_zero_is_the_image_mapped_into_pq (1e-6) |
| Fly.outputTransform (SRGB -> pq, peak 1000) | colour.output_transform / OutputTransform (pq) | P | test_frame.py::test_frame_pq_matches_js_oracle; ::test_pq_frame_zero_is_the_image_mapped_into_pq |
| Fly.outputTransform (SRGB -> hlg, applyGray) | colour.OutputTransform (hlg) | P | test_frame.py::test_hlg_star_peak_exceeds_sdr_white_and_stays_under_one (SDR white 0.75) |
| Fly.hdrTransform: stats.maxCll, stats.maxFall | colour.OutputTransform.stats (max_cll, max_fall) | P | test_frame.py::test_frame_pq_matches_js_oracle (oracle stats, rel 1e-6); ::test_pq_max_cll_of_approaching_star_is_above_sdr_white_and_under_the_peak |
| Fly.hlgEncode | colour.hlg_encode | P | test_frame.py::test_hlg_star_peak_exceeds_sdr_white_and_stays_under_one (via the HLG transform) |
| Fly.pqEncode | colour.pq_encode | P | test_frame.py::test_frame_pq_matches_js_oracle (through the frames) |
| Fly.moved | geometry.moved (ported by the geometry task; used by the test as the expectation) | P | test_frame.py::test_near_star_moves_stays_in_frame_and_lands_on_projection |
| Render.placedChannels (modelSpikes truthiness, L4) | sprites.placed_channels | P | test_sprites.py::test_empty_model_spikes_takes_the_core_path; ::test_placed_channels_with_one_spikes_entry_for_two_channels |
| selftest fly.render: near star lands on its projection | test only | P | test_frame.py::test_near_star_moves_stays_in_frame_and_lands_on_projection (centroid within 0.5 px of detection + moved - frame 0 projection) |
| selftest fly.hdr: PQ frame 0, top code above pq_encode(203) + 0.02, MaxCLL <= peak, HLG peak | test only | P | test_frame.py::test_pq_frame_zero_is_the_image_mapped_into_pq; ::test_pq_max_cll_of_approaching_star_is_above_sdr_white_and_under_the_peak; ::test_hlg_star_peak_exceeds_sdr_white_and_stays_under_one |
| Render.perf (L1: the timed frame draws its sprites) | test only | P | test_frame.py::test_perf_640x360 (Docker, LOOMSIRIL_PERF=1) |


## Task E9B: review clean-ups (materialise_cd public, stub docstrings, Docker script)
E9b ports no new JS function. It renames one Python helper, adds tests that pin two behaviours, and fixes
docstrings, a comment and a shell expansion. The rows below record the JS behaviour the touched code follows.

| JS | Python | Status | Test |
|---|---|---|---|
| Sky.fetchText / Sky.transferWatch (chunked GET, stall timer measured between reads, Sky.js) | `gaia.fetch_text` picks `read1` over `read` (one recv per loop turn) | P | `tests/test_gaia.py::test_fetch_text_slow_trickle_with_read1_is_not_a_stall` (fake `read` raises AssertionError) |
| Fallback when no read1 (no JS counterpart; the JS reads one stream) | `gaia.fetch_text` falls back to `read` | R | `tests/test_gaia.py::test_fetch_text_read_fallback_still_works` |
| Header WCS conversion for Siril's PC/CDELT/CROTA2 form (no JS counterpart; Siril writes these keys) | `io_fits.materialise_cd` (renamed from `_materialise_cd`, now public); CROTA2 form pinned to the FITS/AIPS rotation | R | `tests/test_io.py::test_materialise_cd_from_crota2_is_the_fits_rotation`, `tests/test_io.py::test_fits_read_converts_crota2_cdelt_form` |
| Siril pixel convention used by the test stub (`pix2radec` / `radec2pix`; no JS counterpart) | tests/sirilpy_stub docstrings now state the pixel-edge convention, y from the top; code unchanged | R | `tests/test_siril_bridge.py` (existing stub-based tests) |
| Empty build-arg array in docker/run-tests.sh (shell only, no JS counterpart) | `${BUILD_ARGS[@]+"${BUILD_ARGS[@]}"}` works under `set -u` on bash 3.2 | R | `bash -n docker/run-tests.sh`, and a run with a stub `docker` on PATH (no build env set and one set) |


## Task R1-A: refactor loop 1: jsmath
Status: P = ported (same maths, moved), V = vectorised port, R = replaced by a Python equivalent, D = not ported.

| JS | Python | Status | Test |
|---|---|---|---|
| `x / 0` (IEEE result in a division) | `jsmath.js_div` (was geometry._div) | R | test_jsmath.py::test_js_div_gives_ieee_results_for_zero |
| `Math.max(a, b)` (NaN propagates) | `jsmath.js_max` (was geometry._jsmax) | R | test_jsmath.py::test_js_max_and_min_propagate_nan |
| `Math.min(a, b)` (NaN propagates) | `jsmath.js_min` (was geometry._jsmin) | R | test_jsmath.py::test_js_max_and_min_propagate_nan |
| `Math.sqrt` | `jsmath.js_sqrt` (was geometry._sqrt) | R | test_jsmath.py::test_js_sqrt_acos_sin_are_nan_where_js_is |
| `Math.acos` | `jsmath.js_acos` (was geometry._acos) | R | test_jsmath.py::test_js_sqrt_acos_sin_are_nan_where_js_is |
| `Math.sin` | `jsmath.js_sin` (was geometry._sin) | R | test_jsmath.py::test_js_sqrt_acos_sin_are_nan_where_js_is |
| `Math.floor` | `jsmath.js_floor` (was geometry._floor) | R | test_jsmath.py::test_js_floor_and_ceil_keep_non_finite_values |
| `Math.ceil` | `jsmath.js_ceil` (was geometry._ceil) | R | test_jsmath.py::test_js_floor_and_ceil_keep_non_finite_values |
| `Math.round` | `core.js_round`, re-exported by jsmath | P | test_jsmath.py::test_js_round_is_the_core_one_halves_up; test_core.py::test_js_round_against_js |
| `String(number)` / Number#toString | `jsmath.number_str` (was presets.js_num_str and gaia._js_number_str, one copy now; presets.js_num_str re-exported) | P | test_jsmath.py::test_number_str_is_js_string_of_number, ::test_presets_and_gaia_use_the_one_number_text |
| `parseFloat` (prefix; NaN when none) | `jsmath.parse_float` (was presets._js_parse_float) | P (gap: 'Infinity' gives NaN, JS gives Infinity; unchanged) | test_jsmath.py::test_parse_float_reads_the_prefix, ::test_parse_float_without_a_number_is_nan |
| `String(v)` on header values | `jsmath.js_string` (was presets._js_string) | P | test_jsmath.py::test_js_string_of_header_values |
| `JSON.stringify` of a string | `jsmath.json_string` (was presets._json_string) | P | test_jsmath.py::test_json_string_uses_the_short_escapes_and_u_for_controls; test_presets.py::test_js_number_text_matches_json_stringify |
| `Fly.smoothstep` over an array (Render bloom, sprites) | `core.smoothstep_arr` (replaces bloom._smoothstep) | V | test_core.py::test_smoothstep_arr_is_the_scalar_elementwise, ::test_smoothstep_arr_float64_stays_float64; test_bloom.py |
| `Fly.radialSource` over an array (sprites) | `geometry.radial_source_arr` | V | test_jsmath.py::test_radial_source_arr_is_the_scalar_elementwise, ::test_radial_source_arr_nan_seen_floors_to_0_05 (the sprite-copy test went with the copy in R1-c); test_render_characterization.py |
| `Fly.headroomBump` over an array (sprites) | `geometry.headroom_bump_arr` | V | test_jsmath.py::test_headroom_bump_arr_is_the_scalar_elementwise, ::test_headroom_bump_arr_centre_and_far_limits (the sprite-copy test went with the copy in R1-c); test_render_characterization.py |


## Task R1-B: refactor loop 1: orchestration
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


## Task R1-C: refactor loop 1: render path
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


## Task R1-D: refactor loop 1: star code
Behaviour-preserving. Oracle replays unchanged; no tolerance changed. JS names unchanged; Python names
changed only where a parameter was dropped (no renames).

| JS | Python | Status | Test |
|---|---|---|---|
| Math.max / Math.min (JS helpers in Sky.js, Fly.js) | `jsmath.js_max`, `jsmath.js_min` (were `_js_max`, `_js_min` copies in starmodel and starfit) | P | tests/test_jsmath.py::test_js_max_and_min_propagate_nan |
| `v \|\| fallback`, falsiness of a number | `jsmath.js_or`, `jsmath.is_falsy` (were `_js_or`, `_is_js_falsy` copies) | P | tests/test_jsmath.py::test_is_falsy_and_js_or_take_the_fallback |
| `v \|\| 0` for a flux (assign) | `jsmath.js_or(v, 0.0)` (was `assign._js_or_zero`) | P | tests/test_assign.py::test_drop_inside_brighter_matches_js |
| Sky.ringStats(buf, w, h, cx, cy, r, labels, self) | `starmodel.ring_stats(buf, cx, cy, r, labels, self_)`: w, h from `buf.shape` | P | tests/test_starmodel.py::test_ring_stats_oracle |
| Sky.reader(buf, w, h) | `starmodel.reader(buf)`: w, h from `buf.shape` | P | tests/test_starmodel.py::test_reader_oracle |
| Sky.bilinear(buf, w, h, x, y) | `starmodel.bilinear(buf, x, y)`: w, h from `buf.shape`; neighbours in `_in_image_neighbours` | P | tests/test_starmodel.py::test_bilinear_oracle |
| Sky.bilinear over arrays (no JS name) | `starfit._bilinear_arr(buf, x, y)`: w, h from `buf.shape` | V | tests/test_starfit.py (spike oracles) |
| Sky.spikeExcess over a grid (no JS name) | `starfit._excess_grid(env, d, angs, rs, side)` (R2-d: was `(buf, cx, cy, ...)`; over `_excess_trig`) | V | tests/test_starfit.py (spike oracles) |
| Sky.spikeExcess(buf, w, h, cx, cy, ang, r, side) | `starfit.spike_excess(buf, cx, cy, ang, r, side)` | P | tests/test_starfit.py::test_spike_excess_oracle |
| Sky.brightMeasure(buf, w, h, x, y) | `detect.bright_measure(buf, x, y)`: w, h from `buf.shape` | P | tests/test_detect.py::test_bright_measure_matches_js |
| Fly.dropBorrowedSpikes | `starmodel.drop_borrowed_spikes` with `_borrowed_angles`, `_spikes_covering`, `_drop_spikes`, `_parallel` | P | tests/test_starmodel.py::test_drop_borrowed_spikes_oracle, ::test_drop_borrowed_spikes_all_borrowed_leaves_no_spike_and_the_rmax_support |
| Fly.isFragment, last test (brighter core) | `starmodel._brighter_core_by_grid` with `_grid_candidates`; 3x3 max `_window_max` | P | tests/test_starmodel.py::test_fragment_filter_matches_the_old_quadratic_filter |
| Sky.unexplainedSources | `starfit.unexplained_sources` with `_window_pixels`, `_MissedScan.is_point_peak` (R2-d: was `_is_point_peak`) | P | tests/test_starfit.py::test_unexplained_sources_oracle, ::test_unexplained_sources_finds_a_pixel_once_when_two_sprites_share_it |
| Fly.spriteOwnership | `assign.sprite_ownership` (unchanged; w, h kept: not a buffer-only function) | P | tests/test_assign.py::test_sprite_ownership_matches_js |
| Fly.dropInsideBrighter | `assign.drop_inside_brighter` with `_brighter_reaches` | P | tests/test_assign.py::test_drop_inside_brighter_matches_js, ::test_drop_inside_brighter_asks_for_the_halo_once_and_keeps_the_far_one |
| Sky.addToSums | `starfit.add_to_sums` (kept as the JS name; pass-through) | P | tests/test_starfit.py::test_add_to_sums_oracle |


## Task R1-E: refactor loop 1: IO and Siril edge
Columns: JS = Loom v0.4.1 name (or "none" for a Python-only IO helper); Python = loomsiril name; Status: P ported,
V vectorised, R replaced by a Python equivalent, D not ported (reason). No public name moved or was renamed in this
task, so the rows of docs/parity/e1b.md, e2b.md and e7.md are unchanged. The moves are internal helpers.

### encode.py, gaia.py, io_fits.py, siril_bridge.py, Loom/loom_fly_through.py

| JS | Python | Status | Test |
|---|---|---|---|
| Render.runProcess | encode.run_process (helpers _start_pump, _drain, _past_deadline_or_cancelled, _Output, _stop) | R (subprocess, no shell; same exit codes and chunking) | test_run_process_streams_stderr_and_returns_the_exit_code, test_run_process_deadline_gives_minus_one, test_run_process_cancel_terminates_and_gives_minus_one, test_run_process_flushes_an_incomplete_utf8_tail, test_run_process_deadline_keeps_the_output_so_far, test_run_process_callback_error_stops_the_process_and_propagates, test_stop_on_a_finished_process_does_nothing, test_stop_kills_a_process_that_ignores_terminate |
| Fly.ffmpegCandidates | encode.ffmpeg_candidates (table _FFMPEG_PLACES: _FfmpegPlace per platform) | P (same order, same de-duplication) | test_oracle_replay[ffmpegCandidates], test_ffmpeg_candidates_order_macos, test_ffmpeg_candidates_order_windows |
| Sky.fetchText | gaia.fetch_text (helpers _open, _read_body, _read_chunk, _check_gap) | R (urllib through an injected opener; the three limits are unchanged) | test_fetch_text_reads_chunks, test_fetch_text_gives_up_on_a_stall, test_fetch_text_gives_up_at_the_total_deadline, test_fetch_text_first_byte_limit, test_fetch_text_slow_trickle_with_read1_is_not_a_stall, test_fetch_text_empty_answer_is_empty_text, test_fetch_text_socket_timeout_before_the_first_byte_is_a_first_byte_error, test_fetch_text_socket_timeout_after_data_is_a_stall, test_fetch_text_urlopen_error_is_a_gaia_error, test_fetch_text_sets_the_socket_timeout_before_each_read, test_fetch_text_socket_timeout_is_capped_by_the_total_time_left, test_fetch_text_closes_the_response_once_on_success_and_on_error |
| Sky.transferWatch | gaia.fetch_text (read loop, see above) | R | test_fetch_text_reads_chunks, test_fetch_text_read_fallback_still_works |
| none (Python IO) | io_fits._parse_value (helpers _parse_string, _parse_scalar, _parse_float) | R (FITS header reader; no JS equivalent) | test_parse_value_blank_value_and_comment_only_is_none, test_parse_value_unparseable_text_is_returned_as_text, test_parse_value_number_forms, test_parse_value_string_forms, test_fits_string_and_comment_parsing |
| none (Python IO) | io_fits.write_fits (helpers _structural_cards, _header_cards, _header_block, _data_block) | R (FITS writer; no JS equivalent) | test_write_fits_bytes_are_pinned[-32], test_write_fits_bytes_are_pinned[-64], test_write_fits_bytes_are_pinned[16], test_write_fits_float64_round_trip_and_size, test_write_fits_without_planes_is_refused, test_write_fits_skips_structural_keywords_from_the_header, test_fits_write_then_read_is_identity |
| Fly.headerCentre (via FlyThrough.js target) | siril_bridge.target_from_header (pass-through to presets.header_centre; kept as the script's documented call) | P | test_target_from_header_is_header_centre_ra_dec |
| FlyThrough.js dialog (698-776) | Loom/loom_fly_through.py LoomFlyDialog(master, defaults: DialogValues, starnet_available, target), DialogValues, DialogValues.from_options, run_dialog | R (tkinter, Siril's theme; not run inside Siril) | test_dialog_values_keep_saved_options_and_fill_the_rest, test_dialog_values_defaults_come_from_presets_and_the_working_folder, test_dialog_class_only_where_tk_works (skips without tkinter or a display) |


## Task R2-B: loop 2: orchestration, scene inputs, Gaia query
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


## Task R2-C: loop 2: render path and resampling
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


## Task R2-D: loop 2: star code interfaces
Status: P = ported, V = vectorised port, R = replaced by a Python equivalent, D = not ported (reason).

| JS | Python | Status | Test |
|---|---|---|---|
| Fly.findCluster | `parallax.find_cluster`; per-seed counts in `parallax._ClusterFinder` (methods `hits`, `within`, `counts`, `near`); `_quantile` for the quartiles | V | test_parallax.py::test_find_cluster_matches_js; test_constructed_cluster_is_found_at_its_distance; test_uniform_field_has_no_cluster |
| Fly.refineCluster | `parallax.refine_cluster(inside, seed, near)` (near factory = `_ClusterFinder.near`) | P | test_parallax.py::test_refine_cluster_matches_js |
| Sky.sprites (halo callback, the second pass, the deblend) | `starlayer.sprites` in steps: `_detections`, `_Modeller.halo`, `_missed_stars`, `_deblend_all` | P | test_starlayer.py oracle replays; test_stage_callback_gets_js_texts_in_order |
| Fly.assignSprites | `assign.assign_sprites` in steps: `_one_to_one`, `_Footprint`, `_candidates(pairs, _Matching, foot)` (R3-c) | P | test_assign.py::test_assign_sprites_matches_js |
| Sky.unexplainedSources | `starfit.unexplained_sources` over `starfit._MissedScan` (`sum_i`, `excess`, `is_point_peak`, `scan`) | P | test_starfit.py::test_unexplained_sources_oracle; test_unexplained_sources_window_bound_is_one_pixel_inside_the_image |
| Sky.ringStats | `starmodel.ring_stats` over `starmodel._ring_pixels` | P | test_starmodel.py::test_ring_stats_oracle |
| Sky.spikeExcess (scalar) | `starfit.spike_excess` (unchanged; JS signature kept for the oracle replay) | P | test_starfit.py::test_spike_excess_oracle |
| Sky.spikeExcess over a grid (no JS name) | `starfit._excess_trig(env, d, (c, s), reads)` / `_excess_grid(env, d, angs, reads)` (R3-c: `_Reads(rs, side)`) | V | test_starfit.py::test_excess_trig_grid_equals_the_scalar_spike_excess_exactly |
| Sky.starSpikes | `starfit.star_spikes`: all directions in one grid, same floats as the scalar reads | V | test_starfit.py::test_star_spikes_equals_the_scalar_spike_excess_exactly; test_star_model_oracle |
| Fly.dropBorrowedSpikes, parallel test (2 degrees) | `starmodel._parallel` | P | test_starmodel.py::test_parallel_two_degree_tolerance_on_both_sides; ::test_parallel_two_degree_tolerance_across_the_wrap |
| `v \|\| fallback` for a number (flux, spike sigma, MAD) | `jsmath.js_or` (only numbers or None reach it; NaN, 0, None tested for Python and numpy types) | R | test_jsmath.py::test_is_falsy_and_js_or_take_the_fallback; ::test_js_or_numpy_scalars_take_the_fallback |
| Sky.starModel | `starfit.star_model` (unchanged) | P | test_star_model_oracle |

Not changed: `detect.py` (is_fragment, fragment_filter untouched), `starfit.spike_window` / `point_model` (signatures already take a model or the env).


## Task R3-A: loop 3: orchestration, package, Gaia, encode
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


## Task R3-B: loop 3: Siril dialog, colour, frame, resample
Behaviour-preserving: frames stay bit-identical (tests/test_render_characterization.py, unchanged hashes), the HDR
colour transform keeps its recorded bytes (tests/test_colour.py::test_hdr_apply_bytes_are_characterized, recorded
on fly/final@23c0860), every oracle replay is unchanged, and the dialog's widgets, order, starting values and result
dict are unchanged (tests/test_siril_script.py: the fake-tk and the real-Tk characterization).
Status: P = ported (same maths, moved or re-signed), V = vectorised port, R = replaced by a Python equivalent,
D = not ported.

| JS | Python | Status | Test |
|---|---|---|---|
| `Fly.hdrTransform` one() (R3-b: `_hdr_rgb(rgb, excess)`, the RGB and excess planes as two tuples) | `colour.OutputTransform._hdr_rgb` | P | test_colour.py::test_hdr_apply_bytes_are_characterized; test_output_transform_matches_js |
| `Fly.applyGray` HDR branch (R3-b: `_hdr_rgb((k, k, k), (x, x, x))`) | `colour.OutputTransform._apply_gray` | P | test_colour.py::test_hdr_apply_bytes_are_characterized (hlg-gray-excess, pq-srgb-gray-excess) |
| `Render.resampleRows` / `Render.resample` (R3-b: `_apply_taps(plane, taps, axis)`; out_n = len(start) - 1) | `resample._apply_taps`, `_Taps` | V | test_resample.py::test_resample_rows_matches_js, ::test_resample_matches_js |
| `Render.axisWeights` tap packing (R3-b: `_pack_axis(counts, iv, wv, src_n)`, out_n = counts.size) | `resample._pack_axis` | V | test_resample.py::test_axis_weights_match_js |
| `Render.areaMean` taps (R3-b: `_area_axis` returns `_Taps`) | `resample.area_mean`, `_area_axis` | V | test_resample.py::test_area_mean_matches_js |
| `Render.shrinkPatch` taps (R3-b: `_apply_taps` on the ShrinkAxis) | `resample.shrink_patch` | V | test_resample.py::test_shrink_patch_matches_js |
| `Render.frame` (R3-b: unchanged signature; see the note in the DONE line) | `frame.frame` | P | test_frame.py oracle; test_render_characterization.py |
| `Render.frameImage` (R3-b: unchanged) | `frame.frame_image` | P | test_frame.py; test_render_characterization.py |
| Siril dialog form (no JS: the Siril script's own tkinter form; R3-b: `_form_fields` table of `_Field` rows, `_entry(row, r)`, `_combo(row, r)`) | `Loom/loom_fly_through.py::LoomFlyDialog` | D (not a JS function; the Siril side is not in Loom's JS) | test_siril_script.py::test_dialog_layout_and_values_are_characterized_without_a_display; ::test_dialog_layout_and_values_under_a_real_display |


## Task R3-C: loop 3: star code privates
Loop 3 changes only the shape of private helpers and adds one characterization test. Every JS
name below keeps its Python function and its oracle replay; the JS-shaped public signatures
(`starlayer.sprites`, `starmodel.ring_stats`, `starfit.spike_excess`, `assign.assign_sprites`,
`assign.sprite_ownership`, `starmodel.fit_spike_pair`, `starfit.point_model`) are unchanged.

Status: P = ported, V = vectorised port, R = replaced by a Python equivalent, D = not ported.

| JS | Python | Status | Test |
|---|---|---|---|
| Sky.sprites driver (683-731) | `starlayer.sprites` (6 params, public, called by `pipeline.build_scene`: signature kept) | P | test_residual_plus_sprites_equals_stars_layer, test_oracle_counts_and_stages_exact |
| Sky.sprites detections step (Sky.detections, Fly.addMissingBright, fragment filter) | `starlayer._detections(stars, px, neighbours, detections)` (computes `sm.intensity` itself; was 5 params) | P | test_detections_argument_replaces_the_detector, test_passed_detections_are_not_changed |
| Sky.ringStats | `starmodel.ring_stats` (6 params, JS-shaped, kept) over `starmodel._ring_pixels(shape, _Ring, labels, self_)` | P | test_ring_stats_oracle, test_ring_stats_skips_other_stars_and_is_none_off_image |
| Sky.spikeExcess (scalar) | `starfit.spike_excess` (6 params, JS-shaped, kept) | P | test_spike_excess_oracle |
| Sky.spikeExcess (grid, no JS name) | `starfit._excess_trig(env, d, (c, s), _Reads(rs, side))`, `_excess_grid(env, d, angs, _Reads)` | V | test_excess_trig_grid_equals_the_scalar_spike_excess_exactly |
| Sky.starSpikes | `starfit.star_spikes` (one `_excess_trig` grid over the directions) | V | test_star_spikes_equals_the_scalar_spike_excess_exactly, test_star_model_oracle |
| Sky.spikeDirections, Sky.spikeWidth, Sky.spikeFalloff | `spike_directions`, `spike_width`, `spike_falloff` (call `_excess_grid` with `_Reads`) | V | test_spike_directions_oracle, test_spike_width_oracle, test_spike_falloff_oracle |
| Sky.unexplainedSources scan window (x, y in 1..w-2, 1..h-2) | `starfit._window_pixels` | P | test_window_pixels_start_one_pixel_inside_the_image, test_unexplained_sources_window_bound_is_one_pixel_inside_the_image |
| Fly.assignSprites | `assign.assign_sprites` (7 params, JS-shaped, kept) over `_one_to_one`, `_Matching`, `_candidates(pairs, match, foot)`, `_Footprint` | P | test_assign_sprites_matches_js, test_assign_sprites_on_a_synthetic_field |
| Fly.spriteOwnership | `assign.sprite_ownership` (5 params, JS-shaped, kept) | P | test_sprite_ownership_matches_js |


## Task N: skyname.py (Loom 0.4.2: objects outside NGC/IC found by name, online)

Port of commit a8252c3 (`Fly.js`, `Sky.js`, `FlyThrough.js`). `git diff v0.4.1 v0.4.4` of the Fly-Through files shows
nothing else changed. The Siril port has no NGC/IC catalogue, so the names go to CDS Sesame only (it knows NGC and IC
too); `named_in_field` keeps a `local` hook for the catalogue-first step. Tests: `tests/test_skyname.py` (the JS
selftest checks with the same inputs and values) and `tests/test_fly_names.py` (the dialog, no window, no network).
The lookup was only ever run against a stub, never against the live service from the suite (the suite blocks sockets).

| JS | Python | Status | Test |
|---|---|---|---|
| Fly.NAME_RESOLVER_URL | loomsiril.skyname.NAME_RESOLVER_URL | P | tests/test_skyname.py::test_the_url_is_sesames_and_the_name_is_encoded |
| Fly.NAME_QUERY_MAX | loomsiril.skyname.NAME_QUERY_MAX | P | tests/test_skyname.py::test_name_queries_folders_follow_the_file_and_the_cap_holds |
| Fly.parseSesame | loomsiril.skyname.parse_sesame (JS parseFloat prefix rule) | P | tests/test_skyname.py::test_parse_sesame_id_and_position, test_parse_sesame_an_unknown_name |
| Fly.nameQueries | loomsiril.skyname.name_queries | P | tests/test_skyname.py::test_name_queries_catalogue_like_word_first_then_the_names_words |
| Fly.viewIdFromPath | loomsiril.skyname.view_id_from_path (no caller: Siril views have no ids) | P | tests/test_skyname.py::test_view_id_from_path |
| Fly.imageTitle | loomsiril.skyname.image_title (no caller: Siril has no Image<number> views; the dialog and progress window title use the file name, fly_model.window_title) | P | tests/test_skyname.py::test_image_title_the_files_name_for_image_number_else_the_view_id |
| Sky.fetchText (the `what` label) | loomsiril.skyname.fetch_text(url, what): urllib, 8 s limit, None on any failure | R | tests/test_skyname.py::test_fetch_text_returns_none_on_any_failure |
| Sky.resolvedNames | loomsiril.skyname.resolved_names (a failed request is NOT kept, unlike the JS) | R | tests/test_skyname.py::test_offline_is_no_answer_and_is_not_kept, test_resolve_name_a_name_is_asked_once_the_answer_kept |
| Sky.resolveName | loomsiril.skyname.resolve_name | P | tests/test_skyname.py::test_resolve_name_unknown_is_none_and_the_miss_is_kept |
| Sky.namedInField | loomsiril.skyname.named_in_field (optional `local` catalogue hook) | P | tests/test_skyname.py::test_named_in_field_the_resolved_object_in_the_field, test_named_in_field_one_outside_the_field_is_not_the_target, test_named_in_field_names_the_target_from_the_users_path_not_the_working_copys |
| Sky.field | loomsiril.skyname.sky_field(wcs, width, height) | P | tests/test_skyname.py::test_sky_field_is_the_middle_and_the_farthest_corner |
| FlyThrough.headerObject | loomsiril.siril_bridge.header_object (the OBJECT keyword; no XISF property in Siril) | R | tests/test_skyname.py::test_bridge_header_object |
| FlyThrough.identify (`choices.imagePath`, target = typed or picked or named) | loomsiril.fly_dialog.LoomFlyDialog._start_names / _on_named (a hit becomes the target RA, Dec only while the boxes are untouched and Object name is empty or names that same object) | R | tests/test_fly_names.py::test_opening_with_a_named_file_asks_a_worker_and_fills_an_empty_object_name |
| (Object box, `lookUpObject` + `Sky.resolveName`) | loomsiril.fly_dialog.LoomFlyDialog._find_typed, skyname.check_typed (Siril also refuses a name outside the solved field) | R | tests/test_fly_names.py::test_find_resolves_a_typed_name_inside_the_field_and_fills_the_target, test_find_refuses_a_name_outside_the_field_with_the_reason |
| (new, no JS) | loomsiril.skyname.find_named, NameContext, NameResult, check_typed; fly_model.name_sources, found_note, outside_note, window_title, OFFLINE_NOTE | S | tests/test_skyname.py, tests/test_fly_names.py |

Distance is unchanged: it is the Gaia-based automatic one (the target's cluster, else the bright star that lights it)
that Loom also takes from `id.target`; the found object only moves the target RA, Dec the distance is looked up round
(`FlyThrough.findDistance`), as in Loom. No catalogue distance is invented.

## The distance shown in the dialog (Siril-only)

Loom shows the distance after Analyse (`FlyThrough.showDistance`, `describeDistance`); the Siril dialog finds it as
soon as the target is known and shows it in a grey line and as the Distance box's placeholder. The lookup is the
render's own (`FlyThrough.findDistance`), so both agree. Tested with a stubbed Gaia (`tests/test_fly_distance.py`).

Distance parity with Loom on a real field (Sh2-132): `tools/oracle/distance.js` runs `Fly.findCluster`,
`Fly.clusterSamples`, `Fly.illuminatorDistance`, `Fly.gaiaOnlineUrl` and `Sky.field`'s rule on the frozen Gaia rows
`tests/fixtures/sh2-132-gaia.json.gz` for the image centre and the Sesame position and records `tests/oracle/distance.json`;
`tests/test_distance_parity.py` replays it through `fly_distance.resolve` / `pipeline._find_distance` (query URLs byte
for byte, the cone radii, the limits, cluster and lighting-star results, the counts the failure line prints). When
nothing is found, `pipeline.search_text` words what was searched (Siril-only).

| JS | Python | Status | Test |
|---|---|---|---|
| FlyThrough.findDistance | loomsiril.pipeline._find_distance (records and label; `_resolve_distance` keeps its 3-tuple), DistanceFound | P | tests/test_pipeline.py::test_distance_cluster_found_is_used, tests/test_fly_distance.py::test_the_cluster_comes_from_the_same_lookup_the_render_uses |
| FlyThrough.describeDistance | loomsiril.pipeline.distance_label, distance_text ("cluster of 37 stars, Gaia", "illuminating star G 7.0, Gaia online", "galaxy (fixed backdrop)", "typed") | R | tests/test_fly_distance.py::test_the_render_uses_the_dialogs_distance_and_names_its_source |
| FlyThrough.showDistance (`distanceEdit`, `distanceNote`) | loomsiril.fly_distance.resolve, immediate, parse_typed, DistanceResult, CACHE_EMPTY_NOTE, DEFAULT_FIELD_RADIUS (no JS: the dialog resolves it on a worker; cache-empty falls back to online) | R | tests/test_fly_distance.py |
| (Distance box and note) | loomsiril.fly_model.distance_line, distance_hint, FINDING_DISTANCE; fly_dialog.LoomFlyDialog._start_distance, _apply_distance, _found_distance; siril_bridge._given_distance (`found_distance` reaches the job) | R | tests/test_fly_distance.py::test_draft_and_render_options_carry_the_distance_the_dialog_found |

Unlike Loom, a Gaia "cache" with no data for the field asks Gaia online once and says so; the box is never filled in
(the placeholder shows the value), so an empty box stays "auto".

## Video details (fly_video.py; Fly-Through parity job)

The Video group of the PixInsight dialog (`FlyThrough.js` 1473-1523) and the Fly.js / Render.js code behind it. Tests:
`tests/test_fly_video.py` (functions, fake ffmpeg script, no real encode) and `tests/test_fly_video_dialog.py` (the section on
fake widgets). Nothing here was run inside Siril, on a real display, or with a real ffmpeg encode.

| JS | Python | Status | Test |
|---|---|---|---|
| Fly.availableFormats via Render.ffmpegEncoders (FlyThrough.js:940, 1512-1516) | fly_video.probe (`-version` and `-encoders` once per binary, cached by real path, size and mtime), fly_video.formats_for, fly_video.FfmpegProbe | R (a cache the JS lacks; the format filter is encode.available_formats) | test_fly_video.py::test_probe_lists_what_this_ffmpeg_can_encode, ::test_probe_runs_encoders_once_per_binary_and_again_when_the_binary_changes, ::test_probe_of_a_binary_that_is_not_ffmpeg_or_missing_offers_nothing |
| Render.isWorkingFfmpeg (Render.js:1405-1410), browseFfmpeg (FlyThrough.js:1495) | fly_video.validate_ffmpeg (with the reason), fly_checks.validate kind `ffmpeg` (worker) | R (a typed path, checked on the validation worker, not an OpenFileDialog) | test_fly_video.py::test_validate_runs_version_and_says_why_not, test_fly_video_dialog.py::test_the_validation_worker_runs_the_typed_ffmpeg |
| FlyThrough.refreshFormats (1512-1523), Fly.defaultFormat | fly_video.choose_format, fly_video.widest_side, fly_video.format_label | P (extra: a saved or chosen format is kept while offered; the JS always resets to the default) | test_fly_video.py::test_choose_format_keeps_the_saved_one_else_the_default, ::test_widest_side_follows_the_orientation |
| FlyThrough.encodeArgs (FlyThrough.js:250-259: `opts.format \|\| (hdr ? "hevc" : "h264")`, `opts.quality \|\| "high"`) | fly_video.encode_format, fly_video.quality_id, pipeline._encode_args, FlyJob.format | P (H.264 is never used for HDR; a format this ffmpeg cannot encode falls back with a note: Siril-only) | test_fly_video.py::test_the_encode_format_is_opts_format_or_h264_and_hevc_for_hdr, ::test_a_render_encodes_with_the_chosen_format_and_quality, ::test_a_format_this_ffmpeg_cannot_encode_falls_back_with_a_note |
| qualityCombo High / Standard (1484-1485) | fly_video.QUALITIES | P | test_fly_video.py::test_quality_ids |
| FlyThrough.estimateMs (719) | fly_video.estimate_ms, fly_video.ESTIMATE_HDR_FACTOR | P | test_fly_video.py::test_estimate_ms_is_frames_times_megapixels_times_speed |
| FlyThrough.refreshEstimate (2006-2012) | fly_video.estimate_text | P (empty until a speed is measured, as the JS) | test_fly_video.py::test_estimate_text_rounds_like_the_js_and_is_blank_without_a_measurement |
| FlyThrough.measureSpeed (707) | fly_video.ms_per_mp, pipeline.Result.ms_per_mp, loom_fly_through.remember_speed | R (the speed of the frames the last render drew, SDR-normalised, kept as `ms_per_mp`; no 1920x1080 timing frame) | test_fly_video.py::test_ms_per_mp, ::test_a_render_measures_its_speed_for_the_estimate, test_fly_video_dialog.py::test_the_render_speed_is_kept_and_starts_the_next_estimate |
| FlyThrough.clearFrames (281) | fly_video.clear_frames (calls pipeline.clear_frames; none for a missing folder) | P | test_fly_video.py::test_clear_frames_deletes_only_loom_frames_and_records_and_counts_them, ::test_clear_frames_on_no_folder_is_zero |
| clearRenderedFrames texts (1718-1730) | fly_video.confirm_text, counted_text, cleared_text, nothing_text | P | test_fly_video.py::test_the_clear_texts, test_fly_video_dialog.py::test_clear_rendered_frames_confirms_deletes_only_ours_and_counts |
| buildVideo controls and tooltips (1473-1492) | fly_dialog.LoomFlyDialog._build_video, fly_model groups `encode`, FlyDialogModel.ffmpeg_ok, ffmpeg_path | R (a section of the Siril dialog; hover tips adapted from the JS tooltips) | test_fly_video_dialog.py (section, formats, greying, ffmpeg checks, tips) |
| (none) | fly_video.forget_probes | extra: empties the probe cache (the suite) | test_fly_video.py (autouse fixture) |

## Logo layer (logo.py; Fly-Through parity job)

The Logo box of the PixInsight dialog (`FlyThrough.js` 1399-1426) and the Fly.js / Render.js code behind it. Tests:
`tests/test_logo.py` (maths against `tests/oracle/logo.json`, readers on synthetic PNG/TIFF/FITS, frame, signatures, Draft)
and the logo tests in `tests/test_siril_script.py` (the section on fake widgets, and real Tk where a display exists). Nothing
here was run inside Siril, with a real ffmpeg encode, or on a real display (the hover tips and the look were not seen).

| JS | Python | Status | Test |
|---|---|---|---|
| Fly.LOGO_PLACES, LOGO_SIZE, LOGO_MARGIN (Fly.js:1215-1217) | logo.LOGO_PLACES, LOGO_SIZE, LOGO_MARGIN | P | test_logo.py::test_the_constants_are_the_js |
| Fly.logoFade (1220) | logo.logo_fade | P | test_logo.py::test_logo_fade_matches_the_js |
| Fly.logoRect (1226) | logo.logo_rect | P | test_logo.py::test_logo_rect_matches_the_js |
| Render.logoLayer, Render.fitPlane (Render.js:575-598) | logo.logo_layer (fit_plane from resample) | P | test_logo.py::test_logo_layer_matches_the_js |
| Render.addLogo (628-641), its call in Render.frameImage (1020) | logo.add_logo, frame.frame_image (before the output transform) | P (the rectangle is clipped to the frame; the JS never meets one outside) | test_logo.py::test_add_logo_matches_the_js, ::test_the_logo_goes_in_before_the_output_transform |
| FlyThrough.readLogo (197-212: any PixInsight file format) | logo.read_logo (PNG, TIFF, FITS by content) | R (JPEG and other formats are refused with a message; PixInsight reads whatever it has a reader for) | test_logo.py::test_png_*, ::test_tiff_*, ::test_fits_is_read_top_down, ::test_the_errors_say_what_is_wrong |
| FlyThrough.presetJob (138), draftOptions (528) | pipeline._preset_job, draft.render_draft (logo layer at the frame or draft size) | P | test_logo.py::test_the_final_frames_have_the_logo_and_reuse_follows_it, ::test_the_draft_has_the_logo_at_its_size_and_fades_it_in |
| Fly.frameSignature (logoPath, logoPlace, logoOpacity, logoDelay in; logoImage out) | logo.attach (logoStamp: path, mtime, size), logo.file_stamp, draft.draft_signature | R (stricter: the file's mtime and size key it; with placement Off no logo value is in the options, signature or draft key) | test_logo.py::test_the_frame_signature_follows_the_logo, ::test_the_draft_key_follows_the_logo; test_siril_script.py::test_with_placement_off_no_logo_value_keys_the_draft_or_the_frames |
| FlyThrough.useLogo (2137) | fly_dialog.LoomFlyDialog._browse_logo | P | test_siril_script.py::test_browsing_a_logo_places_it_bottom_right_when_the_placement_was_off |
| buildLogo controls and tooltips (1399-1426) | fly_dialog.LoomFlyDialog._build_logo, fly_model groups `logo` and `logo_look`, FlyDialogModel.logo_note, fly_checks.check_logo (kind `logo`, on the worker), siril_bridge `logo_*` keys, FlyJob.logo_*, logo.logo_settings, place_id, place_label | R (a section of the Siril dialog; hover tips adapted from the JS tooltips; the file is read on the validation worker) | test_siril_script.py logo tests |
| (none) | logo.attach, logo.layer_for, logo.is_on | extra: the options' logo read once, its layer for a frame size, and whether one is drawn | test_logo.py |

## Stars and motion and HDR (fly_options.py, fly_option_rows.py, fly_tooltip.py; Fly-Through parity job)

The Stars and motion and HDR rows of the PixInsight dialog (`FlyThrough.js` `buildMotion` 1281-1338 and the HDR part of `buildOutput`
1340-1400) as one table, the rows built from it, and a hover tip. Tests: `tests/test_fly_options.py` (table, parsing, engine
defaults, job options, frame signature, Draft key, the JS constants and tooltips when the PixInsight sources are checked out),
`tests/test_siril_script.py` (rows on fake widgets and real Tk where a display exists, greying and the grey reason in each row,
typed and bad values, Reset to defaults, tips bound) and `tests/test_fly_sections.py` (the section API the rows use). Nothing here was
run inside Siril, and the tips were never shown on a real display.

| JS | Python | Status | Test |
|---|---|---|---|
| travelEdit, travelNote (1284-1289, 1913-1917), Fly.defaultTravel, clampTravel | fly_options.OPTIONS (`travel`), fly_options.travel_note, pipeline._render_opts (typed travel capped at 0.9 D) | R (empty means automatic, shown as "automatic: N pc", where PixInsight fills the box; not remembered, as in the JS) | test_fly_options.py::test_travel_note_names_the_cap_and_the_automatic_travel, ::test_a_typed_travel_above_the_cap_is_capped_and_the_note_is_true |
| easingCombo (1290-1292) | fly_options.OPTIONS (`easing`) | P | test_fly_options.py::test_defaults_are_the_engines, ::test_parse_accepts |
| growthEdit, Fly.GROWTH_DEFAULT (1293; Fly.js:240) | fly_options.OPTIONS (`growth`) | R (limited to 0 to 1; the JS takes any number) | test_fly_options.py::test_defaults_and_ranges_are_the_js, ::test_parse_rejects_naming_the_control |
| nebulaSpin, Fly.BACKDROP_MOTION_DEFAULT (1294-1299; Fly.js:58) | fly_options.OPTIONS (`backdropMotion`) | R (greyed for a galaxy, where the JS leaves it on: its backdrop is fixed) | test_fly_options.py::test_defaults_and_ranges_are_the_js, ::test_nebula_motion_is_grey_for_a_galaxy |
| brightCheck (1300-1302) | fly_options.OPTIONS (`brightening`) | P | test_fly_options.py::test_the_values_reach_the_renderer |
| twinkleSpin, Fly.TWINKLE_DEFAULT (1303-1307; Fly.js:210) | fly_options.OPTIONS (`twinkle`) | P | test_fly_options.py::test_defaults_and_ranges_are_the_js |
| saturationSlider, saturationLabel (1308-1315) | fly_options.OPTIONS (`starSaturation`), fly_option_rows (slider and its "100%" label) | P | test_fly_options.py::test_defaults_and_ranges_are_the_js, ::test_format_value |
| bloomSpin (1316-1319) | fly_options.OPTIONS (`bloom`) | P | test_fly_options.py::test_defaults_and_ranges_are_the_js |
| blurCheck (1320-1323) | fly_options.OPTIONS (`motionBlur`) | P | test_fly_options.py::test_the_values_reach_the_renderer |
| peakSpin, Fly.HDR_PEAK_DEFAULT (1388-1391; Fly.js:1506) | fly_options.OPTIONS (`peak`) | P | test_fly_options.py::test_defaults_and_ranges_are_the_js |
| starHdrCheck (1393-1397) | fly_options.OPTIONS (`starHdr`) | P | test_fly_options.py::test_the_values_reach_the_renderer |
| transferCombos, Fly.HDR_DEFAULT_TRANSFER, presetOptions (1370-1380; Fly.js:1508; FlyThrough.js:225) | fly_options.OPTIONS (`hdrTransfer`, one per output size), fly_options.transfer_default, pipeline._preset_options (honours `hdrTransfer`) | P (the Siril renderer used each preset's default before this job) | test_fly_options.py::test_per_preset_transfer_reaches_the_preset_options, ::test_defaults_and_ranges_are_the_js |
| refreshHdr (1462-1467) | fly_options.reason, fly_options.enabled, fly_options.Context, fly_model.FlyDialogModel (`opt:<key>` groups, `dynamic`, `target_type`) | R (greyed with a reason in the row, where the JS hides the transfer combos) | test_fly_options.py::test_hdr_controls_are_grey_in_sdr_with_a_reason, test_siril_script.py::test_every_greyed_row_shows_its_reason_and_clears_it_when_enabled |
| tooltips (1289, 1297, 1306, 1312, 1318, 1323, 1391, 1396, 1378) | fly_options.Option.tooltip, fly_tooltip.Tooltip, fly_tooltip.DELAY_MS | R (the text is the JS's; Growth, Easing and Brighten, which have none in the JS, got one; shown by a small window under the pointer) | test_fly_options.py::test_tooltips_are_the_js_tooltips, test_siril_script.py::test_motion_and_hdr_rows_are_generated_from_the_table |
| options() (1766-1790), optionControls (2043-2062) | fly_options.job_options, fly_options.options_from_values, fly_options.saved_keys, fly_options.load_values, siril_bridge.job_from_dialog, OPTION_KEYS | R (HDR-only values are left out in SDR; Travel is not remembered) | test_fly_options.py::test_job_options_in_sdr_leave_out_the_hdr_only_ones, ::test_job_from_dialog_carries_the_controls_into_the_job_options, ::test_the_saved_keys_are_all_kept_between_runs, ::test_load_values_falls_back_to_the_default_for_a_bad_saved_value |
| Fly.frameSignature, draftOptions | presets.frame_signature, draft.draft_signature (`hdr_peak`, `hdr_transfer_*` are not in the draft's key: a draft is SDR) | P | test_fly_options.py::test_changing_a_value_changes_the_signature_and_an_equal_one_does_not, ::test_draft_key_follows_the_picture_options_only |
| (none) | fly_options.OPTIONS, SECTIONS, Option, OptionError | extra: the table, its section titles, one control, the error a bad value raises (names the control) | test_fly_options.py::test_table_is_consistent |
| (none) | fly_options.options_in, by_saved, defaults | extra: a section's controls, a control by its saved key, the defaults by saved key (Reset to defaults) | test_fly_options.py::test_table_is_consistent, ::test_load_values_falls_back_to_the_default_for_a_bad_saved_value |
| (none) | fly_options.parse, shown, format_value, problems | extra: widget value to engine value, back, the text a control shows, the messages of every bad value | test_fly_options.py::test_parse_accepts, ::test_parse_rejects_naming_the_control, ::test_problems_names_every_bad_value_and_never_raises, ::test_format_value |
| (none) | fly_option_rows.build, reset_section, update_notes | extra: the rows of a section from the table, its Reset to defaults, the grey note beside Travel | test_siril_script.py::test_motion_and_hdr_rows_are_generated_from_the_table, ::test_saved_values_come_back_and_reset_to_defaults_restores_them, ::test_travel_note_follows_the_distance_and_the_typed_travel |
| (none) | fly_tooltip.Tooltip | extra: a hover tip bound to a widget (Tk has none); never raises | test_siril_script.py::test_motion_and_hdr_rows_are_generated_from_the_table (bindings run) |

## Not ported, and why

1. **PixInsight-only accelerations** (`Render.bloom`/`composite`/`blend`/`starsToHeadroom`/`blurImage`/`box3Kernel`/
   `bloomWhiten` ImageOp paths): numpy is the vectorised engine; the JS reference versions (`bloomJs`,
   `compositeJs`, `starsToHeadroomJs`, the blend loop) are what is ported. The exact dark-pixel skips for a scalar
   interpreter (`anyLit`, `reachTests`, `reachTest`, `darkReach`, `darkMaps`, `darkMap`, `darkRow`, `nearLight`,
   `patchReach`, `rayDistance`, `SKIP_DARK`) only skip zero contributions; a numpy bounds test does the same job.
   (`footprintPays` IS ported: it changes the result, see the E5 rows.)
2. **PJSR user interface**: `FlyThrough.Dialog`, `Player`, `PREVIEW_EVERY`, `Render.dueFrame`, `paintLayer`, `still`, `STILL_LONG`, `describeTarget`,
   `blindObject`, `describeDistance`: the Siril script has its own small tkinter dialog
   (`Loom/loom_fly_through.py`). Not in this list any more: `estimateMs` (`fly_video.estimate_ms`), `measureSpeed`
   (replaced by the measured `ms_per_mp` of the last render: a documented deviation, see the Video details section) and
   `renderDraft` / `draftOptions` (`draft.render_draft`, `draft.draft_spec`, `draft.draft_plan_for`; the draft's options come from
   `pipeline._render_opts` with the dialog's values, SDR, 480 px: ported, with the draft's own signature and cache).
3. **NGC/IC object lookup** (`Fly.parseNgcIc`, `OBJECT_ALIASES`, `OBJECT_STOPWORDS`, `OBJECT_MIN_SCORE`,
   `objectWords`, `levenshtein`, `nameScore`, `messierHits`, `nameHits`, `aliasHits`, `findObject`, `pickTarget`,
   `targetType`, `objectFromFileName`, `objectFromPath`, `FILE_NAME_SCORE`, `Sky.readNgcIc`, `NGC_IC_RELATIVE`,
   `NGC_IC_CANDIDATES`, `installDir`, `ngcIcMissingWarned`, `FlyThrough.identify`, `headerObject`, `findDistance`,
   `stageOf`): needs PixInsight's NGC-IC catalogue file. v1 takes `--target ra,dec` / `--target-pixel` (or the header
   centre) with `--type` and `--distance` or the Gaia cluster/illuminator finder.
4. **Logo overlay**: ported since the Fly-Through parity job (`logo.py`: `read_logo`, `logo_layer`, `add_logo`, `logo_rect`, `logo_fade`; rows in the Task E5 and E6 tables above). The remembered file and placement
   (`FlyThrough.LOGO_SETTING`, `FlyThrough.LOGO_PLACE_SETTING`) are the saved options `logo_path` and `logo_place`.
5. **Image cache and settings** (`Fly.CACHE_FORMAT`, `hashKey`, `imageCacheKey`, `cacheToPrune`,
   `Render.packScene`, `unpackScene`, `Sky.writeArrays`, `readArrays`, `FlyThrough.CACHE_KEEP`, `cacheRoot`,
   `cacheDir`, `saveWork`, `loadWork`, `starsName`, `saveBuilt`, `loadBuilt`, `saveDraft`, `loadDraft`, `touch`,
   `prune`, `removeTree`, `OPTIONS_SETTING`, `OBJECTS_SETTING`, `TOOL_SETTING`, `FOCAL_SETTING`, `PIXEL_SETTING`,
   `Render.FFMPEG_SETTING`, `cancel`, `isCancel`): PixInsight-session conveniences. The frames.json signature and
   resume ARE ported; the Siril script keeps its last options in one JSON file.
6. **Plate solving** (`Sky.solverIndexDir` ... `solveBlind`, `catalogueTile`, `regionDir`, `regionCatalogue`,
   `readCachedRegion`, `cacheRegion`, `historyPath`, `readHistory`, `remember`, `blindCandidates`, `solveOnce`,
   `solveAtPixel`, `solveWithHints`, `solutionHints`, `clearSolution`, `writeTanKeywords`, `WCS_KEYWORD`,
   `BLIND_RETRY_PAUSE`, `BLIND_OUTAGE`, `NO_CATALOGUE`, `forgetRegions`, `_regions`, `now`, `pause`, `cancelled`,
   `FlyThrough.solveUnsolved`, `solveByHints`, `Fly.solveFailure`, `SCALE_TOLERANCE`, `plausibleScale`): Siril
   plate-solves; the CLI takes a solved FITS header or a JSON solution.
7. **Linear-image stretch** (`Sky.stretchIfLinear`): `presets.looks_linear` is ported; the CLI warns, and in Siril
   the user stretches first. Porting Loom's stretch is a separate module.
8. **Gaia through PixInsight's Gaia process** (`Sky.querySources`, `gaiaRelease`, `GAIA_DR2`, `GAIA_EDR3`,
   `GAIA_DR3`, `GAIA_DR3SP`, `GAIA_NAMES`, `GAIA_ORDER`, `GAIA_MAG_LIMIT`, `resetGaia`, `requireSources`,
   `GAIA_ONLINE_TIMEOUT`): no equivalent in Siril's Python API; Gaia DR3 comes from VizieR online (cached) or a TSV.
9. **PixInsight window plumbing** (`Sky.copyWindow`, `iccBytes`, `Render.channels` as an Image reader,
   `Render.writeTiff` via FileFormat, `Render.srgbIcc` via the system profile, `Render.runProcess` via
   ExternalProcess): replaced by numpy planes, tifffile and subprocess (rows marked R above).
10. **Small UI helpers**: `Fly.describeStars` (the dialog's star-count sentence; the CLI and the Siril script print
    pipeline.Result.counts instead) and `Sky.transferCancelled` (the dialog's Cancel during a Gaia download;
    gaia.fetch_text stops on its stall/total deadlines and has no cancel button to watch).
