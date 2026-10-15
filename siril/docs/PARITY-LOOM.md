# Parity: Loom pipeline (JavaScript) -> loomsiril.loom (Python)

Source: Loom v0.4.1 (https://github.com/fcarucci/Loom), the libraries under `script/lib/` and the main script,
read-only. The JavaScript references in the tables are Loom's file and line numbers (`lib/Steps.js:2162-2199`).

Oracle: the recorders `tools/oracle/loom_*.js` evaluate Loom's JavaScript and write the golden values to
`tests/oracle/loom_*.json`. The Python tests replay those files; the test suite itself never needs Node.

Each public function or class of `loomsiril/loom/` has one row, in the module section where it is defined. The `Test`
column names one test file that calls it directly (`-` when no such test file exists). It is not a list of every test.
The `JavaScript (Loom)` column gives the Loom function of the same name (camelCase) with its file and line in Loom's
`script/lib/`, or the reference given in the Python module. `-` means Loom has no function of that name: the Python
code is the port's own structure, such as the Siril or tool layer.

Status:

- **P**: ported. Same maths as the JavaScript named in the row; the tests pin its results.
- **V**: ported and verified by oracle. The module's recorder in `tools/oracle/` calls the same Loom function, and a
  test replays that recorder and calls this function.
- **R**: replaced. A Python equivalent of a different mechanism: an external tool's own output, a numpy
  implementation of a PixInsight process, or a Siril command. See [Deviations](#deviations).
- **D**: dropped. Not ported; the reason is under [Deviations](#deviations).
- **S**: Siril layer or tool layer. Code with no single JavaScript counterpart; the JavaScript column names the Loom
  code it stands in for.

Conventions that differ from the JavaScript on purpose:

- Images are numpy arrays shaped `(h, w)`, one per channel. Row order is top-down, as in Loom.
- Records that Loom passes as plain objects are dataclasses (for example `RunContext` and `ChainResult`).
- Options are read and written through `loomsiril.loom.config` (Loom's option names and types).
- JavaScript number semantics come from `loomsiril.jsmath`.

## loomsiril/loom/cache.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `cache.js_json_stringify` | `Cache.js` | P | `tests/acceptance/test_loom_pr4_cache.py` |
| `cache.hash_str` | `Cache.hash` | V | `tests/loom/test_cache.py` |
| `cache.params_string` | `lib/Cache.js:121` | V | `tests/loom/test_cache.py` |
| `cache.chain_key` | `lib/Cache.js:141` | V | `tests/loom/test_cache.py` |
| `cache.iso_ms` | `Cache.js` | P | `tests/acceptance/test_loom_pr4_cache.py` |
| `cache.file_source_key` | `Cache.fingerprintFile` | P | `tests/acceptance/test_loom_pr4_cache.py` |
| `cache.crop_tag` | `lib/Pipeline.js:1103` | V | `tests/loom/test_cache.py` |
| `cache.crop_halos_tag` | `lib/Pipeline.js:1108` | V | `tests/loom/test_cache.py` |
| `cache.clean_white_balance_key` | `lib/Pipeline.js:1333` | V | `tests/loom/test_cache.py` |
| `cache.rgb_source_key` | - | P | `tests/acceptance/test_loom_pr4_cache.py` |
| `cache.palette_source_key` | - | P | `tests/acceptance/test_loom_pr4_cache.py` |
| `cache.lstars_source_key` | - | P | `tests/acceptance/test_loom_pr4_cache.py` |
| `cache.build_stage_keys` | `lib/Pipeline.js:764` | V | `tests/loom/test_cache.py` |
| `cache.resolve_cache_hit` | `lib/Pipeline.js:814` | V | `tests/loom/test_cache.py` |
| `cache.default_cache_dir` | `Cache.js` | R | `tests/acceptance/test_loom_pr4_cache.py` |
| `cache.folder_missing` | `Cache.js` | S | `tests/acceptance/test_loom_pr4_cache.py` |
| `cache.CacheStore` | `Pipeline.cacheEntryComplete` | P | `tests/acceptance/test_loom_pr4_cache.py` |

## loomsiril/loom/chain.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `chain.RunCancelled` | `lib/Pipeline.js:814-1093` | P | `tests/loom/test_entry.py` |
| `chain.ChainResult` | `lib/Pipeline.js:814-1093` | P | `tests/loom/test_chain.py` |
| `chain.process_chain` | `lib/Pipeline.js:1005` | P | `tests/loom/test_chain.py` |

## loomsiril/loom/channels.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `channels.is_broadband` | `lib/Util.js:440` | P | - |
| `channels.keyword_value` | `lib/Util.js:450` | P | `tests/loom/test_channels.py` |
| `channels.channel_supplied` | `lib/Util.js:614` | P | - |
| `channels.channel_group_problems` | `lib/Util.js:625` | P | - |
| `channels.duplicate_file_problems` | `lib/Util.js:645` | P | - |
| `channels.validate_selection` | `lib/Util.js:603` | P | `tests/loom/test_channels.py` |
| `channels.channel_from_filter` | `lib/Util.js:670` | P | `tests/loom/test_xisf.py` |
| `channels.serialize_entries` | `lib/Util.js:713` | P | `tests/loom/test_channels.py` |
| `channels.deserialize_entries` | `lib/Util.js:721` | P | `tests/loom/test_channels.py` |
| `channels.factor_label` | `lib/Util.js:752` | P | - |
| `channels.drizzle_label` | `lib/Util.js:761` | P | `tests/loom/test_channels.py` |
| `channels.unescape_history` | `lib/Util.js:770` | P | - |
| `channels.drizzle_scale_from_history` | `lib/Util.js:782` | P | `tests/loom/test_xisf.py` |
| `channels.integration_drizzle_label` | `lib/Util.js:806` | P | - |
| `channels.master_drizzle_label` | `lib/Util.js:821` | P | `tests/loom/test_channels.py` |
| `channels.qe_curve_name_for_camera` | `lib/Util.js:1417` | P | `tests/loom/test_channels.py` |
| `channels.spcc_sensor_for` | `Util.js` | P | `tests/loom/test_channels.py` |
| `channels.common_instrument` | `lib/Util.js:1401` | P | - |
| `channels.palette_names` | `lib/Util.js:1497` | P | `tests/loom/test_dialog.py` |
| `channels.palette_wavelengths` | `lib/Util.js:1481` | P | `tests/loom/test_channels.py` |
| `channels.palette_channels` | `lib/Util.js:1503` | P | - |
| `channels.palette_missing` | `lib/Util.js:1518` | P | - |
| `channels.is_autocrop_name` | `lib/Util.js:1558` | P | - |
| `channels.is_master_light` | `lib/Util.js:1567` | P | - |
| `channels.master_variant_rank` | `lib/Util.js:1575` | P | - |
| `channels.parse_master_name` | `lib/Util.js:1610` | P | - |
| `channels.select_masters` | `lib/Util.js:1625` | P | - |
| `channels.psb_base_name` | `lib/Pipeline.js:204` | P | `tests/loom/test_naming.py` |
| `channels.extract_directory` | `lib/Pipeline.js:232` | P | - |
| `channels.project_name_from_path` | `lib/Pipeline.js:227` | P | `tests/loom/test_channels.py` |
| `channels.project_name_for` | `lib/Pipeline.js:248` | P | `tests/loom/test_channels.py` |
| `channels.narrowband_supplied` | `lib/Pipeline.js:95` | P | - |
| `channels.wanted_palettes` | `lib/Pipeline.js:110` | P | - |
| `channels.report_skipped_palettes` | `lib/Pipeline.js:127` | P | - |
| `channels.inherit_instrument` | `lib/Pipeline.js:1780` | P | `tests/loom/test_channels.py` |
| `channels.read_header` | `lib/Util.js:468` | P | `tests/loom/test_channels.py` |
| `channels.scan_masters` | - | P | `tests/loom/test_channels.py` |

## loomsiril/loom/colour_load.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `colour_load.ColourGroupLoader` | `lib/Pipeline.js:2073` | P | `tests/loom/test_colour_load.py` |
| `colour_load.group_log_line` | `lib/Pipeline.js:2192` | P | `tests/loom/test_colour_load.py` |
| `colour_load.plane_statistics` | `Steps.planeStats` | S | `tests/loom/test_colour_load.py` |
| `colour_load.is_plain_fits` | - | S | `tests/loom/test_colour_load.py` |

`ColourGroupLoader` writes the `LOOMSRC` keyword of a plate differently from Loom when the master's name is long (see
[Deviations](#deviations)).

## loomsiril/loom/config.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `config.defaults` | `lib/Config.js:68-98` | V | `tests/loom/test_config.py` |
| `config.config_path` | `lib/Config.js:238-390` | S | `tests/loom/test_config.py` |
| `config.gradient_tool_of` | `lib/StepsSyqon.js:1483-1490` | R | `tests/loom/test_config.py` |
| `config.supported_noise_level` | `lib/Steps.js:2135-2140` | R | `tests/loom/test_config.py` |
| `config.migrate_config` | `lib/Config.js:238-390` | V | `tests/loom/test_config.py` |
| `config.apply_options` | `lib/Config.js:238-390` | P | - |
| `config.load` | `lib/Config.js:238-390` | S | `tests/loom/test_config.py` |
| `config.save` | `lib/Config.js:238-390` | S | `tests/loom/test_config.py` |

## loomsiril/loom/dialog.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `dialog.ToolRow` | `lib/UI.js` | S | - |
| `dialog.wheel_units` | - | S | `tests/loom/test_dialog.py` |
| `dialog.RunRequest` | `lib/UI.js` | S | `tests/loom/test_entry.py` |
| `dialog.RunBlocked` | `lib/UI.js` | S | - |
| `dialog.DialogModel` | `lib/UI.js` | S | - |
| `dialog.ProgressModel` | `lib/UI.js` | S | `tests/loom/test_dialog.py` |
| `dialog.ask_folder` | `lib/UI.js` | S | - |
| `dialog.show_dialog` | `lib/UI.js` | S | - |
| `dialog.LoomDialog` | `lib/UI.js` | S | `tests/loom/test_dialog.py` |
| `dialog.ProgressWindow` | `lib/UI.js` | S | `tests/loom/test_dialog.py` |

## loomsiril/loom/finishing.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `finishing.finishing_runners` | `Pipeline.js` | S | `tests/loom/integration/test_finishing.py` |
| `finishing.separate_l_stars` | `lib/Pipeline.js:2718` | P | `tests/loom/test_finishing.py` |

## loomsiril/loom/ghs.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `ghs.run_ghs_stretch` | `lib/Steps.js:3120` | R | `tests/loom/integration/test_ghs.py` |

## loomsiril/loom/icc.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `icc.icc_description` | `lib/StepsIcc.js:98` | R | `tests/loom/test_icc.py` |
| `icc.parse_icc_profile` | `lib/StepsIcc.js:78` | R | `tests/loom/test_icc.py` |
| `icc.icc_profile_directories` | `lib/StepsIcc.js:136` | R | `tests/loom/test_icc.py` |
| `icc.installed_icc_profiles` | `lib/StepsIcc.js:158` | R | `tests/loom/test_icc.py` |
| `icc.profiles_matching` | `lib/StepsIcc.js:194` | R | `tests/loom/test_icc.py` |
| `icc.profile_plan` | `lib/StepsIcc.js:225` | R | `tests/loom/test_icc.py` |
| `icc.Assignment` | `lib/StepsIcc.js:275-336` | R | - |
| `icc.assignment_for` | `lib/StepsIcc.js:275` | R | `tests/loom/test_icc.py` |
| `icc.assignment_log` | `lib/StepsIcc.js:275-336` | R | `tests/loom/test_icc.py` |
| `icc.is_prophoto_family` | `lib/StepsIcc.js:369` | P | `tests/loom/test_icc.py` |
| `icc.psb_profile_bytes` | `lib/StepsIcc.js:337` | P | `tests/loom/test_icc.py` |

## loomsiril/loom/imageops.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `imageops.valid_rect` | `lib/Steps.js:148` | V | `tests/loom/test_imageops.py` |
| `imageops.intersect_rects` | `lib/Util.js:1439` | V | `tests/loom/test_imageops.py` |
| `imageops.common_area` | `lib/Pipeline.js:2221-2272` | P | `tests/loom/test_imageops.py` |
| `imageops.central_rect` | `lib/Util.js:414` | V | `tests/loom/test_imageops.py` |
| `imageops.median_of_centre` | `lib/Steps.js:262` | P | `tests/loom/test_imageops.py` |
| `imageops.min_median_key` | `lib/Util.js:424` | V | `tests/loom/test_imageops.py` |
| `imageops.background_offset` | `lib/Steps.js:1506-1534` | P | `tests/loom/test_imageops.py` |
| `imageops.apply_white_balance` | `lib/Steps.js:1223` | P | `tests/loom/test_imageops.py` |
| `imageops.recover_wb_factors` | `Steps.js` | R | `tests/loom/test_imageops.py` |
| `imageops.inconsistent_keywords` | `lib/Steps.js:2200` | V | `tests/loom/test_imageops.py` |
| `imageops.without_inconsistent_keywords` | `lib/Steps.js:2229` | P | `tests/loom/test_imageops.py` |
| `imageops.sigma_to_reach` | `lib/Util.js:1646` | V | `tests/loom/test_imageops.py` |
| `imageops.sigma_to_fwhm` | `lib/Util.js:1655` | P | `tests/loom/test_imageops.py` |
| `imageops.fwhm_to_sigma` | `lib/Util.js:1656` | V | `tests/loom/test_imageops.py` |
| `imageops.psf_sigma_from_fwhm` | `Steps.js` | P | `tests/loom/test_imageops.py` |
| `imageops.gaussian_blur` | `lib/Steps.js:4038-4065` | P | `tests/loom/test_imageops.py` |
| `imageops.frequency_separate` | `lib/Steps.js:3668` | P | `tests/loom/test_imageops.py` |
| `imageops.unscreen_stars` | `Steps.js` | R | `tests/loom/test_imageops.py` |

## loomsiril/loom/levels.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `levels.tool_chosen` | `lib/Steps.js:1170` | R | - |
| `levels.studio_family_of` | `lib/StepsSyqon.js:1460` | R | `tests/loom/test_levels.py` |
| `levels.gradient_tool_of` | `lib/StepsSyqon.js:1483` | R | `tests/loom/test_levels.py` |
| `levels.studio_family` | `lib/Pipeline.js:381` | R | `tests/loom/test_levels.py` |
| `levels.noise_tools_from` | `lib/Steps.js:1681` | R | `tests/loom/test_levels.py` |
| `levels.denoise_is_linear` | `lib/Steps.js:1574` | V | `tests/loom/test_levels.py` |
| `levels.noise_levels_for` | `lib/Steps.js:2116` | R | `tests/loom/test_levels.py` |
| `levels.supported_noise_level` | `lib/Steps.js:2135` | R | `tests/loom/test_levels.py` |
| `levels.noise_level_name` | `lib/Steps.js:2102` | R | `tests/loom/test_levels.py` |
| `levels.noise_amount_for` | `lib/Steps.js:2464` | R | `tests/loom/test_levels.py` |
| `levels.aberration_corrector` | `lib/Steps.js:2488` | R | `tests/loom/test_levels.py` |
| `levels.sharpen_tools_from` | `lib/Steps.js:2377` | R | `tests/loom/test_levels.py` |
| `levels.sharpen_amount_for` | `lib/Steps.js:2447` | R | `tests/loom/test_levels.py` |
| `levels.star_tools_from` | `lib/Steps.js:3211` | R | `tests/loom/test_levels.py` |
| `levels.composite_sharpen_params` | `lib/Pipeline.js:338` | R | `tests/loom/test_levels.py` |
| `levels.aberration_params` | `lib/Pipeline.js:394` | R | `tests/loom/test_levels.py` |
| `levels.star_extraction_params` | `lib/Pipeline.js:413` | R | `tests/loom/test_levels.py` |
| `levels.stretch_params` | `lib/Pipeline.js:438` | R | `tests/loom/test_levels.py` |
| `levels.ghs_command` | `Steps.js` | R | `tests/loom/test_levels.py` |
| `levels.denoise_level_for` | `lib/Pipeline.js:474` | R | `tests/loom/test_levels.py` |
| `levels.composite_denoise_params` | `lib/Pipeline.js:482` | R | `tests/loom/test_levels.py` |
| `levels.prism2_pass_params` | `lib/Pipeline.js:522` | R | - |
| `levels.linear_denoise_params` | `lib/Pipeline.js:530` | R | `tests/loom/test_levels.py` |
| `levels.stretched_denoise_params` | `lib/Pipeline.js:551` | R | `tests/loom/test_levels.py` |
| `levels.linear_denoise_pass` | `lib/Pipeline.js:575` | R | `tests/loom/test_levels.py` |
| `levels.studio_models_for` | `lib/StepsSyqon.js:1594` | R | `tests/loom/test_levels.py` |
| `levels.gradient_stage_params` | `lib/StepsSyqon.js:1500` | R | `tests/loom/test_levels.py` |
| `levels.finishing_params` | `Pipeline.js` | R | `tests/loom/test_levels.py` |

## loomsiril/loom/naming.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `naming.ordered_output_keys` | `lib/Pipeline.js:2890-2908` | V | `tests/loom/test_naming.py` |
| `naming.unique_name` | `lib/Util.js:342-353` | P | `tests/loom/test_naming.py` |
| `naming.unique_file_name` | `lib/Util.js:342-353` | P | `tests/loom/test_naming.py` |
| `naming.publish_id` | `lib/Pipeline.js:592-594` | V | `tests/loom/test_naming.py` |
| `naming.psb_base_name` | `lib/Pipeline.js:204-208` | V | `tests/loom/test_naming.py` |
| `naming.export_file_name` | `lib/StepsSyqon.js:250-252` | P | `tests/loom/test_naming.py` |
| `naming.kept_results` | `lib/Pipeline.js:2452-2760` | P | `tests/loom/test_naming.py` |
| `naming.main_result` | `lib/Pipeline.js:2452-2760` | P | `tests/loom/test_naming.py` |

## loomsiril/loom/nbnorm.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `nbnorm.stage_params` | `lib/Steps.js:2162-2199` | R | `tests/loom/test_nbnorm.py` |
| `nbnorm.reference_index` | `lib/Steps.js:2162-2199` | R | `tests/loom/test_nbnorm.py` |
| `nbnorm.background_and_scale` | `lib/Steps.js:2162-2199` | R | `tests/loom/test_nbnorm.py` |
| `nbnorm.scale_to_reference` | `lib/Steps.js:2162-2199` | R | `tests/loom/test_nbnorm.py` |
| `nbnorm.normalize` | `lib/Steps.js:2162-2199` | R | `tests/loom/test_nbnorm.py` |
| `nbnorm.sky_level` | - | S | `tests/loom/test_nbnorm.py` |

## loomsiril/loom/osc.py

The colour-master (OSC) rules. Every row marked P is replayed against `tests/oracle/loom_osc.json` (recorded by
`tools/oracle/loom_osc.js`) in `tests/loom/test_osc.py`. `colour_run` and `js_config` are the port's own helpers.

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `osc.normalise_filter` | `lib/Util.js:831` | P | - |
| `osc.osc_model_name` | `lib/Util.js:870` | P | - |
| `osc.osc_filter_info` | `lib/Util.js:879` | P | - |
| `osc.parse_colour_master_name` | `lib/Util.js:1835` | P | - |
| `osc.channel_count_problem` | `lib/Util.js:1475` | P | - |
| `osc.channel_supplied` | `lib/Util.js:620` | P | - |
| `osc.colour_supplied` | `lib/Util.js:677` | P | - |
| `osc.mono_supplied` | `lib/Util.js:685` | P | - |
| `osc.supplied_channels` | `lib/Util.js:694` | P | - |
| `osc.colour_mix_problem` | `lib/Util.js:710` | P | - |
| `osc.osc_kind_key` | `lib/Util.js:733` | P | `tests/loom/test_osc.py` |
| `osc.effective_osc_kind` | `lib/Util.js:744` | P | - |
| `osc.entry_channel` | `lib/Util.js:1877` | P | - |
| `osc.select_colour_masters` | `lib/Util.js:1894` | P | - |
| `osc.colour_candidate_correction` | `lib/Util.js:1927` | P | - |
| `osc.osc_palette_problem` | `lib/Pipeline.js:208` | P | - |
| `osc.input_channel_problems` | `lib/Pipeline.js:223` | P | - |
| `osc.osc_kind_problems` | `lib/Pipeline.js:247` | P | `tests/loom/test_osc.py` |
| `osc.osc_group_identity` | `lib/Pipeline.js:963` | P | - |
| `osc.osc_sibling_key` | `lib/Pipeline.js:973` | P | - |
| `osc.osc_validate_name` | `lib/Pipeline.js:983` | P | - |
| `osc.osc_group_register_key` | `lib/Pipeline.js:994` | P | - |
| `osc.osc_registered_sibling_key` | `lib/Pipeline.js:1003` | P | - |
| `osc.osc_plane_problem` | `lib/Pipeline.js:2020` | P | - |
| `osc.osc_validation_record` | `lib/Pipeline.js:2029` | P | - |
| `osc.osc_narrowband_bandwidths` | `lib/Pipeline.js:2532` | P | - |
| `osc.narrowband_bandwidths` | `lib/Pipeline.js:2532` | P | - |
| `osc.osc_registration_plan` | `lib/Pipeline.js:2715` | P | `tests/loom/test_osc.py` |
| `osc.clean_registration_plan` | `lib/Pipeline.js:1454` | P | `tests/loom/test_osc.py` |
| `osc.osc_group_cache_action` | `lib/Pipeline.js:2752` | P | `tests/loom/test_osc.py` |
| `osc.osc_channel_groups` | `lib/Pipeline.js:2891` | P | `tests/loom/test_osc.py` |
| `osc.osc_sensor_problems` | `lib/Pipeline.js:327` | P | `tests/loom/test_osc.py` |
| `osc.osc_control_state` | `lib/Util.js:771` | P | `tests/loom/test_osc.py` |
| `osc.osc_palette_sources` | `lib/Pipeline.js:3537` | P | `tests/loom/test_osc.py` |
| `osc.osc_channel_source` | `lib/Pipeline.js:3581` | P | `tests/loom/test_osc.py` |
| `osc.osc_source_text` | `lib/Pipeline.js:3595` | P | `tests/loom/test_osc.py` |
| `osc.colour_run` | - | S | `tests/loom/test_osc.py` |
| `osc.js_config` | - | S | `tests/loom/test_osc.py` |

## loomsiril/loom/pipeline.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `pipeline.RunReport` | `lib/Pipeline.js:2762-2888` | P | - |
| `pipeline.run` | `lib/Pipeline.js:2762` | P | `tests/loom/integration/test_pipeline_offline.py` |

## loomsiril/loom/preflight.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `preflight.Preflight` | `lib/Pipeline.js:25-94` | S | `tests/loom/test_preflight.py` |
| `preflight.required_tools` | `lib/Pipeline.js:25-94` | S | `tests/loom/test_preflight.py` |
| `preflight.preflight` | `lib/Pipeline.js:58` | S | `tests/loom/test_preflight.py` |
| `preflight.validate_only_report` | - | S | `tests/loom/test_preflight.py` |

## loomsiril/loom/psb.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `psb.Buffer` | `lib/Psb.js:51` | P | `tests/loom/test_psb.py` |
| `psb.tagged_block` | `lib/Psb.js:166` | V | `tests/loom/test_psb.py` |
| `psb.curves_payload` | `lib/Psb.js:213` | V | `tests/loom/test_psb.py` |
| `psb.hue_saturation_payload` | `lib/Psb.js:278` | V | `tests/loom/test_psb.py` |
| `psb.vibrance_payload` | `lib/Psb.js:322` | V | `tests/loom/test_psb.py` |
| `psb.is_adjustment` | `lib/Psb.js:378` | P | - |
| `psb.has_pixels` | `lib/Psb.js:385` | P | - |
| `psb.layer_extra_blocks` | `lib/Psb.js:339` | P | - |
| `psb.composite_base_layer` | `lib/Psb.js:403` | V | `tests/loom/test_psb.py` |
| `psb.layer_record` | `lib/Psb.js:414` | V | `tests/loom/test_psb.py` |
| `psb.flatten_group` | `lib/Psb.js:519` | P | - |
| `psb.flatten` | `lib/Psb.js:504` | V | `tests/loom/test_psb.py` |
| `psb.declare_channels` | `lib/Psb.js:939` | V | `tests/loom/test_psb.py` |
| `psb.layer_records` | `lib/Psb.js:954` | V | `tests/loom/test_psb.py` |
| `psb.image_resource` | `lib/Psb.js:869` | V | `tests/loom/test_psb.py` |
| `psb.append_icc_resource` | `lib/Psb.js:992` | P | - |
| `psb.file_header` | `lib/Psb.js:967` | V | `tests/loom/test_psb.py` |
| `psb.write_psb` | `lib/Psb.js:881-932` | P | `tests/loom/test_psb.py` |
| `psb.psb_bytes` | `Psb.js` | P | `tests/loom/test_psb.py` |

## loomsiril/loom/psb_document.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `psb_document.curve_layer_name` | `lib/Steps.js:3728` | V | `tests/loom/test_psb_document.py` |
| `psb_document.dso_adjustments` | `lib/Steps.js:3806-3812` | P | `tests/loom/test_psb_document.py` |
| `psb_document.palette_names` | `lib/Util.js:1497` | P | `tests/loom/test_psb_document.py` |
| `psb_document.palette_name` | `lib/Steps.js:3817-3821` | P | `tests/loom/test_psb_document.py` |
| `psb_document.palette_channels_of` | `lib/Steps.js:3836` | V | `tests/loom/test_psb_document.py` |
| `psb_document.palette_group` | `lib/Steps.js:3856-3887` | P | `tests/loom/test_psb_document.py` |
| `psb_document.broadband_group` | `lib/Steps.js:3890-3903` | P | `tests/loom/test_psb_document.py` |
| `psb_document.l_stars_layer` | `lib/Steps.js:3934-3946` | P | `tests/loom/test_psb_document.py` |
| `psb_document.star_layers` | `lib/Steps.js:3905-3932` | P | `tests/loom/test_psb_document.py` |
| `psb_document.build_psb_document` | `lib/Steps.js:3763` | V | `tests/loom/test_psb_document.py` |
| `psb_document.to_psb_tree` | `Steps.js` | P | `tests/loom/test_psb_document.py` |
| `psb_document.assert_plates_share_size` | `lib/Steps.js:3948` | V | `tests/loom/test_psb_document.py` |
| `psb_document.psb_profile_or_none` | `lib/Steps.js:3960-3977` | P | `tests/loom/test_psb_document.py` |

## loomsiril/loom/publish.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `publish.publish` | `lib/Pipeline.js:592-626` | S | `tests/loom/integration/test_publish.py` |
| `publish.leave_main_result` | `lib/Pipeline.js:592-626` | S | `tests/loom/integration/test_publish.py` |
| `publish.export` | `lib/Pipeline.js:630-650` | S | `tests/loom/test_publish.py` |
| `publish.export_psb` | `lib/Pipeline.js:630-650` | S | - |

## loomsiril/loom/reference.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `reference.registration_fallback_ranking` | `lib/Pipeline.js:1979` | R | `tests/loom/test_reference.py` |
| `reference.usable_star_measure` | `lib/Pipeline.js:1993` | V | `tests/loom/test_reference.py` |
| `reference.best_measured_channel` | `lib/Pipeline.js:2002` | V | `tests/loom/test_reference.py` |
| `reference.registration_choice` | `lib/Pipeline.js:2056-2089` | R | `tests/loom/test_reference.py` |
| `reference.registration_reference` | `lib/Pipeline.js:2056` | R | `tests/loom/test_reference.py` |
| `reference.quality_from_analysis` | `lib/Pipeline.js:1973-2101` | P | `tests/loom/test_reference.py` |

## loomsiril/loom/residuals.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `residuals.residual_verdict` | `lib/Steps.js:716` | V | `tests/loom/test_residuals.py` |
| `residuals.measure_residual` | `lib/Steps.js:656-942` | R | `tests/loom/test_residuals.py` |
| `residuals.ResidualChecker` | `lib/Steps.js:656-942` | P | `tests/loom/test_residuals.py` |
| `residuals.gaia_catalogue_source` | `lib/Steps.js:656-942` | P | `tests/loom/test_residual_wiring.py` |

## loomsiril/loom/runctx.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `runctx.RunContext` | - | S | `tests/loom/test_runctx.py` |
| `runctx.StageOutput` | - | S | `tests/loom/test_runctx.py` |

## loomsiril/loom/siril_ops.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `siril_ops.SirilOpsError` | - | S | `tests/loom/test_pipeline.py` |
| `siril_ops.command_text` | - | S | `tests/loom/test_siril_ops.py` |
| `siril_ops.quote_path` | - | S | `tests/loom/test_siril_ops.py` |
| `siril_ops.SirilOps` | - | S | `tests/loom/test_stages_geometry.py` |
| `siril_ops.SirilCommandOps` | - | S | `tests/loom/integration/test_siril_ops_cli.py` |
| `siril_ops.RecordingOps` | - | S | `tests/loom/test_siril_ops.py` |
| `siril_ops.spcc_names` | - | S | `tests/loom/integration/test_siril_ops_cli.py` |
| `siril_ops.spcc_narrowband_names` | - | S | `tests/loom/test_siril_ops.py` |
| `siril_ops.spcc_database_candidates` | - | S | `tests/loom/integration/test_siril_ops_cli.py` |
| `siril_ops.siril_data_dir` | - | S | `tests/loom/test_siril_ops.py` |

## loomsiril/loom/stagekit.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `stagekit.StageFailed` | `Steps.js` | S | `tests/loom/test_finishing.py` |
| `stagekit.format_signature` | `Steps.imageSignature` | S | `tests/loom/test_stagekit.py` |
| `stagekit.image_signature` | `lib/Steps.js:1322` | S | `tests/loom/test_stagekit.py` |
| `stagekit.unchanged_problem` | `Steps.graxpertProblem` | S | `tests/loom/test_stagekit.py` |
| `stagekit.run_siril_stage` | `Steps.js` | S | `tests/loom/test_stagekit.py` |

## loomsiril/loom/stages_colour.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `stages_colour.calibration_identity` | `lib/Pipeline.js:1333` | S | `tests/loom/test_stages_colour.py` |
| `stages_colour.rgb_stage_params` | `lib/Pipeline.js:1453-1666` | P | `tests/loom/test_stages_colour.py` |
| `stages_colour.palette_stage_params` | `lib/Pipeline.js:1453-1666` | P | `tests/loom/test_stages_colour.py` |
| `stages_colour.rgb_spcc_names` | `lib/Pipeline.js:1453-1666` | S | `tests/loom/test_stages_colour.py` |
| `stages_colour.rgb_runners` | `lib/Pipeline.js:1453-1666` | S | `tests/loom/test_stages_colour.py` |
| `stages_colour.palette_runners` | `lib/Pipeline.js:1453-1666` | S | `tests/loom/test_stages_colour.py` |
| `stages_colour.measure_clean_white_balance` | `lib/Pipeline.js:1207` | R | `tests/loom/test_stages_colour.py` |

## loomsiril/loom/stages_correct.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `stages_correct.source_key` | `Pipeline.js` | P | `tests/loom/test_stages_correct.py` |
| `stages_correct.ingest_channel` | `Pipeline.js` | P | `tests/loom/test_stages_correct.py` |
| `stages_correct.broadband_stage_params` | `Pipeline.js` | P | `tests/loom/test_stages_correct.py` |
| `stages_correct.narrowband_stage_params` | `Pipeline.js` | P | `tests/loom/test_stages_correct.py` |
| `stages_correct.studio_found` | `Pipeline.js` | S | - |
| `stages_correct.has_astrometric_solution` | `lib/Steps.js:628` | P | `tests/loom/test_stages_correct.py` |
| `stages_correct.solve_runner` | `Pipeline.js` | S | - |
| `stages_correct.broadband_runners` | `lib/Pipeline.js:1879` | S | `tests/loom/test_stages_correct.py` |
| `stages_correct.narrowband_runners` | `Pipeline.js` | S | `tests/loom/test_stages_correct.py` |
| `stages_correct.master_quality_key` | `lib/Steps.js:3433` | P | `tests/loom/test_stages_correct.py` |
| `stages_correct.master_quality` | `Pipeline.js` | P | `tests/loom/test_stages_correct.py` |

## loomsiril/loom/stages_geometry.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `stages_geometry.register_stage_params` | `lib/Pipeline.js:2129-2215` | R | `tests/loom/test_stages_geometry.py` |
| `stages_geometry.register_channels` | `lib/Pipeline.js:2129-2388` | R | `tests/loom/test_stages_geometry.py` |
| `stages_geometry.common_crop_rect` | `lib/Pipeline.js:2221-2272` | P | `tests/loom/test_stages_geometry.py` |
| `stages_geometry.crop_plate` | `lib/Pipeline.js:2129-2388` | P | `tests/loom/test_stages_geometry.py` |
| `stages_geometry.measure_psf_sigma` | `lib/Steps.js:3572-3661` | S | `tests/loom/test_stages_geometry.py` |
| `stages_geometry.match_halos` | `lib/Pipeline.js:2274` | P | `tests/loom/test_stages_geometry.py` |
| `stages_geometry.balance_narrowband` | `lib/Pipeline.js:2337` | P | `tests/loom/test_stages_geometry.py` |
| `stages_geometry.group_register_params` | `lib/Pipeline.js:2928` | P | `tests/loom/test_stages_geometry.py` |
| `stages_geometry.member_split_header` | `lib/Pipeline.js:2911` | P | `tests/loom/test_stages_geometry.py` |
| `stages_geometry.register_group_image` | `lib/Pipeline.js:2928` | R | `tests/loom/test_stages_geometry.py` |

## loomsiril/loom/stages_subsky.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `stages_subsky.mgc_params` | `lib/Steps.js:1268` | R | `tests/loom/test_stages_subsky.py` |
| `stages_subsky.run_mgc` | `lib/Steps.js:1268` | R | `tests/loom/test_stages_subsky.py` |

## loomsiril/loom/stretch.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `stretch.mtf_midtone_for` | `lib/StepsSyqon.js:983` | V | `tests/loom/test_stretch.py` |
| `stretch.mtf_apply` | `lib/StepsSyqon.js:988` | V | `tests/loom/test_stretch.py` |
| `stretch.stretch_parameters_from` | `lib/Steps.js:2850` | V | `tests/loom/test_stretch.py` |
| `stretch.robust_minimum_per_channel` | `lib/Steps.js:2898` | P | `tests/loom/test_stretch.py` |
| `stretch.clip_safe_black_point` | `lib/Steps.js:2952` | V | `tests/loom/test_stretch.py` |
| `stretch.stretch_parameters_for_planes` | `lib/Steps.js:2975-3027` | P | `tests/loom/test_stretch.py` |
| `stretch.apply_mtf` | `lib/Steps.js:3029-3051` | P | `tests/loom/test_stretch.py` |
| `stretch.mtf_commands` | `lib/Steps.js:2820-3066` | R | `tests/loom/test_stretch.py` |

## loomsiril/loom/tools/exchange.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `tools.exchange.sanitize_basic` | `lib/StepsSyqon.js:2049-2063` | S | `tests/loom/tools/test_exchange.py` |
| `tools.exchange.sanitize_studio` | `lib/StepsSyqon.js:2049-2063` | S | `tests/loom/tools/test_exchange.py` |
| `tools.exchange.write_exchange_fits` | `lib/StepsSyqon.js:2049-2063` | S | `tests/loom/tools/test_exchange.py` |
| `tools.exchange.read_exchange_fits` | `lib/StepsSyqon.js:2049-2063` | S | `tests/loom/tools/test_exchange.py` |
| `tools.exchange.write_exchange_tiff` | `lib/StepsSyqon.js:2049-2063` | S | `tests/loom/tools/test_exchange.py` |
| `tools.exchange.read_exchange_tiff` | `lib/StepsSyqon.js:2049-2063` | S | `tests/loom/tools/test_exchange.py` |
| `tools.exchange.declared_output` | `lib/StepsSyqon.js:2068-2070` | S | `tests/loom/tools/test_exchange.py` |

## loomsiril/loom/tools/graxpert_ai.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `tools.graxpert_ai.bge_args` | `lib/Steps.js:1327` | R | `tests/loom/tools/test_graxpert_ai.py` |
| `tools.graxpert_ai.run_bge` | `lib/Steps.js:1327` | R | `tests/loom/integration/test_graxpert_ai.py` |
| `tools.graxpert_ai.script_version` | `lib/Steps.js:1310` | R | `tests/loom/tools/test_graxpert_ai.py` |
| `tools.graxpert_ai.readiness` | `lib/Steps.js:1310` | R | `tests/loom/tools/test_graxpert_ai.py` |

## loomsiril/loom/tools/rcastro.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `tools.rcastro.ToolContext` | `lib/Steps.js:2509-2518` | R | `tests/loom/tools/test_rcastro.py` |
| `tools.rcastro.format_number` | `lib/Steps.js:2509-2518` | R | `tests/loom/tools/test_rcastro.py` |
| `tools.rcastro.error_message` | `lib/Steps.js:2509-2518` | R | `tests/loom/tools/test_rcastro.py` |
| `tools.rcastro.build_argv` | `lib/Steps.js:2509-2518` | R | `tests/loom/tools/test_rcastro.py` |
| `tools.rcastro.RcAstro` | `lib/Steps.js:2509-2518` | R | `tests/loom/tools/test_rcastro.py` |

## loomsiril/loom/tools/resolver.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `tools.resolver.Found` | - | S | `tests/loom/tools/test_resolver.py` |
| `tools.resolver.pathmod` | - | S | - |
| `tools.resolver.map_path` | - | S | - |
| `tools.resolver.default_prober` | - | S | `tests/loom/tools/test_resolver.py` |
| `tools.resolver.winreg_registry` | - | S | `tests/loom/tools/test_resolver.py` |
| `tools.resolver.Resolver` | - | S | `tests/loom/tools/test_graxpert_ai.py` |

## loomsiril/loom/tools/runner.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `tools.runner.ToolError` | `lib/StepsSyqon.js:607-646` | S | - |
| `tools.runner.ToolFailed` | `lib/StepsSyqon.js:607-646` | S | - |
| `tools.runner.ToolTimeout` | `lib/StepsSyqon.js:607-646` | S | `tests/loom/tools/test_rcastro.py` |
| `tools.runner.ToolNotRunnable` | `lib/StepsSyqon.js:607-646` | S | - |
| `tools.runner.ToolCancelled` | `lib/StepsSyqon.js:607-646` | S | `tests/loom/test_finishing.py` |
| `tools.runner.RunResult` | `lib/StepsSyqon.js:607-646` | S | `tests/loom/tools/test_rcastro.py` |
| `tools.runner.Progress` | `lib/StepsSyqon.js:607-646` | S | `tests/loom/tools/test_runner.py` |
| `tools.runner.parse_progress` | `lib/StepsSyqon.js:607-646` | S | `tests/loom/tools/test_runner.py` |
| `tools.runner.parse_progress_line` | `lib/StepsSyqon.js:607` | S | `tests/loom/tools/test_runner.py` |
| `tools.runner.parse_ndjson_progress` | `lib/StepsSyqon.js:607-646` | S | `tests/loom/tools/test_runner.py` |
| `tools.runner.progress_milestone_text` | `lib/StepsSyqon.js:647` | S | `tests/loom/tools/test_runner.py` |
| `tools.runner.command_line` | `lib/Fly.js:1219` | S | - |
| `tools.runner.run_tool` | `lib/StepsSyqon.js:607-646` | S | `tests/loom/tools/test_runner.py` |
| `tools.runner.run_folder` | `lib/StepsSyqon.js:607-646` | S | `tests/loom/tools/test_runner.py` |
| `tools.runner.exchange_folder` | `lib/StepsSyqon.js:607-646` | S | `tests/loom/tools/test_runner.py` |

## loomsiril/loom/tools/specs.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `tools.specs.ProbeResult` | - | S | `tests/loom/tools/test_resolver.py` |
| `tools.specs.Verdict` | - | S | - |
| `tools.specs.passed` | - | S | - |
| `tools.specs.refused` | - | S | - |
| `tools.specs.Probe` | - | S | - |
| `tools.specs.ToolSpec` | - | S | - |
| `tools.specs.exit_zero` | - | S | `tests/loom/tools/test_resolver.py` |
| `tools.specs.exit_zero_with_output` | - | S | `tests/loom/tools/test_resolver.py` |
| `tools.specs.starnet_check` | - | S | `tests/loom/tools/test_resolver.py` |
| `tools.specs.rc_astro_json` | - | R | `tests/loom/tools/test_resolver.py` |
| `tools.specs.graxpert_text_check` | - | R | `tests/loom/tools/test_resolver.py` |

## loomsiril/loom/tools/starnet.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `tools.starnet.StarNetLicenceNotAccepted` | - | R | - |
| `tools.starnet.ToolContext` | - | R | `tests/loom/tools/test_starnet.py` |
| `tools.starnet.remove_stars` | `lib/Steps.js:3309` | R | `tests/loom/tools/test_starnet.py` |

## loomsiril/loom/tools/store.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `tools.store.ToolStore` | - | S | `tests/loom/tools/test_store.py` |

## loomsiril/loom/tools/syqon_legacy.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `tools.syqon_legacy.ToolNoOutput` | `lib/StepsSyqon.js:293-338` | R | - |
| `tools.syqon_legacy.ModelNotFound` | `lib/StepsSyqon.js:1262-1275` | R | - |
| `tools.syqon_legacy.ToolContext` | `lib/StepsSyqon.js:293-338` | R | `tests/loom/tools/test_syqon_legacy.py` |
| `tools.syqon_legacy.build_args` | `lib/StepsSyqon.js:1310-1322` | R | `tests/loom/tools/test_syqon_legacy.py` |
| `tools.syqon_legacy.starless_model_candidates` | `lib/StepsSyqon.js:1252` | R | `tests/loom/tools/test_syqon_legacy.py` |
| `tools.syqon_legacy.starless_model_path` | `lib/StepsSyqon.js:1267` | R | `tests/loom/tools/test_syqon_legacy.py` |
| `tools.syqon_legacy.starless_no_output_message` | `lib/StepsSyqon.js:1291` | R | `tests/loom/tools/test_syqon_legacy.py` |
| `tools.syqon_legacy.starless` | `selftest.js:22953` | R | `tests/loom/tools/test_syqon_legacy.py` |
| `tools.syqon_legacy.prism` | `selftest.js:22993` | R | `tests/loom/tools/test_syqon_legacy.py` |
| `tools.syqon_legacy.parallax` | `lib/StepsSyqon.js:838-870` | R | `tests/loom/tools/test_syqon_legacy.py` |

## loomsiril/loom/tools/syqon_studio.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `tools.syqon_studio.ToolContext` | `lib/StepsSyqon.js:1418-1470` | R | `tests/loom/tools/test_syqon_studio.py` |
| `tools.syqon_studio.StudioNoOutput` | `lib/StepsSyqon.js:1418-1470` | R | - |
| `tools.syqon_studio.exit_message` | `lib/StepsSyqon.js:1994-2014` | R | `tests/loom/tools/test_syqon_studio.py` |
| `tools.syqon_studio.account_refused` | `lib/StepsSyqon.js:1418-1470` | R | `tests/loom/tools/test_syqon_studio.py` |
| `tools.syqon_studio.is_prism` | `lib/StepsSyqon.js:1418-1470` | R | `tests/loom/tools/test_syqon_studio.py` |
| `tools.syqon_studio.is_prism2` | `lib/StepsSyqon.js:1418-1470` | R | `tests/loom/tools/test_syqon_studio.py` |
| `tools.syqon_studio.model_label` | `lib/StepsSyqon.js:1418-1470` | R | `tests/loom/tools/test_syqon_studio.py` |
| `tools.syqon_studio.model_role` | `lib/StepsSyqon.js:1648-1656` | R | `tests/loom/tools/test_syqon_studio.py` |
| `tools.syqon_studio.probe_problem` | `lib/StepsSyqon.js:1663-1680` | R | `tests/loom/tools/test_syqon_studio.py` |
| `tools.syqon_studio.failure_text` | `lib/StepsSyqon.js:1738-1746` | R | `tests/loom/tools/test_syqon_studio.py` |
| `tools.syqon_studio.prism2_flag_after_check` | `lib/StepsSyqon.js:1698` | R | `tests/loom/tools/test_syqon_studio.py` |
| `tools.syqon_studio.domain_for` | `lib/StepsSyqon.js:1518` | R | `tests/loom/tools/test_syqon_studio.py` |
| `tools.syqon_studio.build_args` | `lib/StepsSyqon.js:1937-1992` | R | `tests/loom/tools/test_syqon_studio.py` |
| `tools.syqon_studio.SyqonStudio` | `lib/StepsSyqon.js:1714-1748` | R | `tests/loom/tools/test_syqon_studio.py` |

## loomsiril/loom/tools/tempstretch.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `tools.tempstretch.syqon_normalize_expression` | `lib/StepsSyqon.js:418` | R | `tests/loom/tools/test_tempstretch.py` |
| `tools.tempstretch.syqon_mtf_expression` | `lib/StepsSyqon.js:424` | R | `tests/loom/tools/test_tempstretch.py` |
| `tools.tempstretch.syqon_inverse_mtf_expression` | `lib/StepsSyqon.js:430` | R | `tests/loom/tools/test_tempstretch.py` |
| `tools.tempstretch.syqon_denormalize_expression` | `lib/StepsSyqon.js:436` | R | `tests/loom/tools/test_tempstretch.py` |
| `tools.tempstretch.syqon_format_stretch` | `lib/StepsSyqon.js:442` | R | `tests/loom/tools/test_tempstretch.py` |
| `tools.tempstretch.StretchState` | `lib/StepsSyqon.js:339-486` | R | `tests/loom/tools/test_tempstretch.py` |
| `tools.tempstretch.stretch_forward` | `lib/StepsSyqon.js:339-486` | R | `tests/loom/tools/test_tempstretch.py` |
| `tools.tempstretch.stretch_inverse` | `lib/StepsSyqon.js:513-523` | R | `tests/loom/tools/test_tempstretch.py` |
| `tools.tempstretch.Background` | `lib/StepsSyqon.js:339-486` | R | - |
| `tools.tempstretch.Stats` | `lib/StepsSyqon.js:339-486` | R | - |
| `tools.tempstretch.RaisedTarget` | `lib/StepsSyqon.js:339-486` | R | - |
| `tools.tempstretch.prism_background` | `lib/StepsSyqon.js:1023` | R | `tests/loom/tools/test_tempstretch.py` |
| `tools.tempstretch.prism_normalised_sample` | `lib/StepsSyqon.js:1057` | R | - |
| `tools.tempstretch.prism_samples` | `lib/StepsSyqon.js:1041` | R | `tests/loom/tools/test_tempstretch.py` |
| `tools.tempstretch.prism_stretch_stats` | `lib/StepsSyqon.js:999` | R | `tests/loom/tools/test_tempstretch.py` |
| `tools.tempstretch.prism_raised_target` | `lib/StepsSyqon.js:1074` | R | `tests/loom/tools/test_tempstretch.py` |
| `tools.tempstretch.prism_mtf_target` | `lib/StepsSyqon.js:1097` | R | `tests/loom/tools/test_tempstretch.py` |

## loomsiril/loom/xisf.py

| Function | JavaScript (Loom) | Status | Test |
|---|---|---|---|
| `xisf.XisfHeader` | - | S | - |
| `xisf.read_xisf_header` | - | S | `tests/loom/test_xisf.py` |

## Loom functions not ported as a row

Loom functions with no Python function of the same name. Each one is either dropped (D) or replaced (R) by the
Python function in the last column, and the reason is under [Deviations](#deviations).

| Loom function | Location | Status | Python | Reason |
|---|---|---|---|---|
| `Steps.spfc` | lib/Steps.js:1077 | D | none | SPFC dropped (Spectrophotometric Flux Calibration; see Deviations). |
| `Steps.denoiseMLDenoise` | lib/Steps.js:1926 | D | none | MLDenoise dropped; Siril denoise added (see Deviations). |
| `Steps.keepFineScaleChange` | lib/StepsSyqon.js:2163-2190 | D | none | Not ported (see Deviations). |
| `Steps.mgc` | lib/Steps.js:1268 | R | `stages_subsky.run_mgc` | MGC replaced by Siril background extraction (see Deviations). |
| `Steps.multiscaleStretch` | lib/Steps.js:3120 | R | `ghs.run_ghs_stretch` | Replaced by Siril GHS (see Deviations). |
| `Steps.graxpert` | lib/Steps.js:1327 | R | `tools.graxpert_ai.run_bge` | Siril's GraXpert-AI script is run instead of the GraXpert application. |
| `Steps.extractStars` | lib/Steps.js:3309 | R | `tools.starnet.remove_stars` | StarNet2 run as a separate program (see Deviations). |

## Deviations

These are the places where the port does not do what Loom does, or does it another way. Each is tested or
documented where it is implemented.

- `LOOMSRC` of a colour plate: `io_fits` has no CONTINUE long strings, so a value longer than the 68 characters a card holds (measured after doubling apostrophes) keeps the end of the master's name behind `...`; the filter, suffix, extension and `|plane:n` survive. PixInsight writes the full name.
- SPFC dropped (Spectrophotometric Flux Calibration): it needs Gaia XP spectra, which are not available offline.
- MGC replaced by Siril subsky degree 2 when the gradient tool is none (never on H, S or O): each broadband channel runs `subsky 2` with 20 samples and tolerance 1.0 in place of Loom's gradient correction.
- MultiscaleAdaptiveStretch replaced by Siril GHS (autoghs, D 4.5, fixed values); saved configurations migrate to it.
- MLDenoise dropped; Siril denoise added (`denoise -mod 0.6/1.0/1.0`), in the linear slot.
- An unknown stretch method raises an error. Loom falls back to MTF; the migration of saved options prevents the case.
- Registration uses Siril's homography with lanczos4 interpolation, and keeps the reference frame bit-identical.
- A colour group is registered as one image like Loom's, but Siril cannot mix one- and three-channel images in a sequence: the reference plate is expanded to three identical channels with `rgbcomp`, the group image is `(R, G, B)` or `(H, O, O)`, and the stars are found on one layer (`-layer=1` for an `OSC` group, `-layer=0` for `OSCNB`). The temporary frames are removed by name afterwards.
- SPCC white-balance factors are recovered by fitting, not read from the colour calibration result.
- TIFF export goes through `io_tiff` (Siril's `savetif` converts colours).
- The fallback ICC profile for a mono image is Siril's built-in sRGB profile (Gray).
- A headless run reports its outcome in a status file (see `docs/loom-job.md`).
- No Siril `starnet` command route: StarNet2 is run directly as a separate program.
- With useCache off, the clean white balance falls back to direct SPCC. Loom would still read a warm cache folder for its plates.
- Narrowband normalisation is a numpy port written from the published description of the method. The description has no formulas, so its faithfulness is pending a comparison with a reference result from PixInsight.
- Results are files. The main result is left loaded in Siril as the current image; no other workspace windows are opened.
- The Prism keepFineScaleChange step (lib/StepsSyqon.js:2163-2190) is not ported. After a Prism 2.0 linear pass, Loom puts back the part of the change that is larger than the fine-scale sigma; the port keeps the whole change.
- A headless job run never saves options, and builds its configuration from the defaults plus the job file only. Dialog mode saves the options before the preflight checks.
- The StarNet2 licence acceptance is kept as a flag in `tools.json` (in Siril's configuration folder). It is compared with the current licence revision and asked again when that revision changes. It is not an option field.
- SyQon Studio Prism Essential's domain follows Loom: nonlinear when the image is already stretched.
- A detail-sharpening failure after a successful star reduction keeps the star-reduced plate, as Loom does.
