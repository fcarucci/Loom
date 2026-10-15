# Frame Selector: parity with the PixInsight original

This document records how the Siril port of the Frame Selector (`Loom/loom_frame_selector.py`, with the logic in
`loomsiril/`) relates to the PixInsight Frame Selector (`pixinsight/script/FrameSelector.js` and the libraries it
uses). It has three parts:

1. a table of every JavaScript function, constant and class member of `FrameSelector.js`, `lib/Frames.js`,
   `lib/Asiair.js`, `lib/AsiairNames.js`, `lib/MasterFlat.js` and `lib/NightDialog.js`, with the Python function that
   ports it;
2. the deviations: everything the Siril port does differently, with the reason and what was measured;
3. one problem found in the PixInsight original.

Behaviour that the PixInsight code and its README describe differently follows the code. The one case is which options
are remembered between runs (deviation 12).

## How to read the table

- **JS lines** are `file:first-last` in the PixInsight sources. A class member shows the line of its definition.
- **Python** is `module.name` in `loomsiril/` (a class member is `module.Class.method`; a name that starts with an
  underscore is a private helper of the module). The table is checked by importing each module and looking each name
  up, so every name below exists, except where the status says otherwise.
- **Status** is `ported` (same behaviour, JavaScript semantics kept), `replaced` (the Siril port does the same job
  another way: see the deviation named in the note), `PixInsight only` (nothing to port, the note says why),
  or `not ported` (a deliberate omission, see the note).

## Function table

### FrameSelector.js

| JS lines | JS name | Python | Status | Note |
|---|---|---|---|---|
| FrameSelector.js:37-37 | FrameSelector | `Loom/loom_frame_selector.py` (`main`) | ported |  |
| FrameSelector.js:44-49 | FrameSelector.title | `frames_ui.frame_selector_title` | ported |  |
| FrameSelector.js:55-55 | FrameSelector.MEASURE_ROUTINE | `frames_measure.MEASURE_CONFIG` | replaced | deviation 1 |
| FrameSelector.js:61-69 | FrameSelector.newMeasureProcess | `frames_measure.measure_batch` | replaced | deviation 1 |
| FrameSelector.js:91-112 | FrameSelector.measure | `frames_measure.measure_batch` | replaced | deviation 1 |
| FrameSelector.js:118-156 | FrameSelector.collectRows | `frames_measure.parse_seq` | replaced | deviation 1 |
| FrameSelector.js:173-196 | FrameSelector.checkSchema | - | not ported | there is no SubframeSelector column schema; the batch check in `frames_measure.batch_problem` covers the same risk |
| FrameSelector.js:204-211 | FrameSelector.pathProblem | `frames_measure.batch_problem` | replaced | deviation 1 |
| FrameSelector.js:220-235 | FrameSelector.markRejected | `frames_ui.mark_icon` | ported |  |
| FrameSelector.js:238-244 | FrameSelector.detach | - | PixInsight only | releases PJSR objects; Tk widgets are released by their own methods |
| FrameSelector.js:251-251 | FrameSelector.STRETCH | `frames_render.STRETCH` | ported |  |
| FrameSelector.js:258-258 | FrameSelector.io | `frames_render.IO` | ported |  |
| FrameSelector.js:267-291 | FrameSelector.readQuietly | `frames_render.read_quietly` | ported |  |
| FrameSelector.js:303-307 | FrameSelector.readFrame | `frames_render.read_frame` | ported |  |
| FrameSelector.js:310-317 | FrameSelector.mtf | `frames_render.mtf` | ported |  |
| FrameSelector.js:320-327 | FrameSelector.stretchParams | `frames_render.stretch_params` | ported |  |
| FrameSelector.js:336-362 | FrameSelector.referenceStretch | `frames_render.reference_stretch` | ported |  |
| FrameSelector.js:364-374 | FrameSelector.readStretchMemo | `frames_render.read_stretch_memo` | ported |  |
| FrameSelector.js:376-386 | FrameSelector.writeStretchMemo | `frames_render.write_stretch_memo` | ported |  |
| FrameSelector.js:395-411 | FrameSelector.stretchInPlace | `frames_render.stretch_in_place` | ported |  |
| FrameSelector.js:419-436 | FrameSelector.stretchedImage | `frames_render.stretched_image` | ported |  |
| FrameSelector.js:439-448 | FrameSelector.eightBit | `frames_render.eight_bit` | ported |  |
| FrameSelector.js:451-465 | FrameSelector.renderParams | `frames_render.render_params` | ported |  |
| FrameSelector.js:472-497 | FrameSelector.cachedRender | `frames_render.cached_render` | ported |  |
| FrameSelector.js:504-504 | FrameSelector.PREVIEW_HINTS | `frames_render.PREVIEW_HINTS` | ported |  |
| FrameSelector.js:511-539 | FrameSelector.storeRender | `frames_render.store_render` | ported |  |
| FrameSelector.js:547-576 | FrameSelector.renderFrame | `frames_render.render_frame` | ported |  |
| FrameSelector.js:579-582 | FrameSelector.thumbnailOf | `frames_render.thumbnail_of` | ported |  |
| FrameSelector.js:589-593 | FrameSelector.closeAll | - | PixInsight only | closes PixInsight image windows; nothing is opened in Siril |
| FrameSelector.js:602-625 | FrameSelector.digest | `frames_cache.digest` | ported |  |
| FrameSelector.js:633-645 | FrameSelector.fileStat | `frames_cache.file_stat` | ported |  |
| FrameSelector.js:647-651 | FrameSelector.identityOf | `frames_cache.identity_of` | ported |  |
| FrameSelector.js:653-657 | FrameSelector.fileIdentity | `frames_cache.file_identity` | ported |  |
| FrameSelector.js:664-667 | FrameSelector.newHasher | `frames_cache.Hasher` | ported |  |
| FrameSelector.js:675-678 | FrameSelector.digestCachePath | `frames_cache._digest_cache_path` | ported |  |
| FrameSelector.js:680-689 | FrameSelector.loadDigestCache | `frames_cache._load_digest_cache` | ported |  |
| FrameSelector.js:691-699 | FrameSelector.saveDigestCache | `frames_cache._save_digest_cache` | ported |  |
| FrameSelector.js:714-721 | FrameSelector.Identities | `frames_cache.Identities.__init__` | ported |  |
| FrameSelector.js:723-733 | FrameSelector.Identities.prototype.request | `frames_cache.Identities.request` | ported |  |
| FrameSelector.js:735-738 | FrameSelector.Identities.prototype.poll | `frames_cache.Identities.poll` | ported |  |
| FrameSelector.js:740-743 | FrameSelector.Identities.prototype.kill | `frames_cache.Identities.kill` | ported |  |
| FrameSelector.js:746-750 | FrameSelector.Identities.prototype.abandon | `frames_cache.Identities.abandon` | ported |  |
| FrameSelector.js:752-760 | FrameSelector.Identities.prototype.finish | `frames_cache.Identities.finish` | ported |  |
| FrameSelector.js:766-779 | FrameSelector.Identities.prototype.remember | `frames_cache.Identities.remember` | ported |  |
| FrameSelector.js:790-798 | FrameSelector.quietHeader | `frames_headers.quiet_header` | ported |  |
| FrameSelector.js:806-836 | FrameSelector.entryFor | `frames_headers.entry_for` | ported |  |
| FrameSelector.js:838-843 | FrameSelector.cachePath | `frames_cache.TABLE_NAME` | ported |  |
| FrameSelector.js:845-858 | FrameSelector.loadTable | `frames_cache.MeasurementStore.load_table` | ported |  |
| FrameSelector.js:860-869 | FrameSelector.saveTable | `frames_cache.MeasurementStore.save_table` | ported |  |
| FrameSelector.js:879-884 | FrameSelector.configSignature | `frames_cache.config_signature` | ported |  |
| FrameSelector.js:892-896 | FrameSelector.measurementKey | `frames_cache.MeasurementStore.key` | ported |  |
| FrameSelector.js:898-903 | FrameSelector.cachedMeasurement | `frames_cache.MeasurementStore.cached` | ported |  |
| FrameSelector.js:911-916 | FrameSelector.storeMeasurement | `frames_cache.MeasurementStore.store` | ported |  |
| FrameSelector.js:918-918 | FrameSelector.tableChanged | `frames_cache.MeasurementStore.save` | ported |  |
| FrameSelector.js:920-926 | FrameSelector.saveMeasurements | `frames_cache.MeasurementStore.save` | ported |  |
| FrameSelector.js:928-934 | FrameSelector.frameFilesIn | `frames_headers.frame_files_in` | ported |  |
| FrameSelector.js:957-995 | FrameSelector.cohortFrom | `frames_scan.cohort_from` | ported |  |
| FrameSelector.js:1002-1030 | FrameSelector.readHeaders | `frames_scan.read_headers` | ported |  |
| FrameSelector.js:1043-1043 | FrameSelector.MEASURE_BATCH | `frames_measure.measure_batch` | ported |  |
| FrameSelector.js:1050-1057 | FrameSelector.measureBatched | `frames_scan.measure_batched` | ported |  |
| FrameSelector.js:1060-1072 | FrameSelector.cachedSplit | `frames_scan.cached_split` | ported |  |
| FrameSelector.js:1094-1124 | FrameSelector.measureGroup | `frames_scan.measure_group` | ported |  |
| FrameSelector.js:1132-1145 | FrameSelector.settler | `frames_scan.settler` | ported |  |
| FrameSelector.js:1166-1166 | FrameSelector.READING_EVERY_MS | `frames_scan.READING_EVERY_MS` | ported |  |
| FrameSelector.js:1168-1216 | FrameSelector.scanPaths | `frames_scan.scan_paths` | ported |  |
| FrameSelector.js:1223-1234 | FrameSelector.measuringReporter | `frames_scan.measuring_reporter` | ported |  |
| FrameSelector.js:1236-1239 | FrameSelector.scan | `frames_scan.scan` | ported |  |
| FrameSelector.js:1246-1249 | FrameSelector.logDir | `frames_delete.log_dir` | ported |  |
| FrameSelector.js:1259-1262 | FrameSelector.importLogPath | `asiair_import.import_log_path` | ported |  |
| FrameSelector.js:1264-1277 | FrameSelector.writeImportLog | `asiair_import.write_import_log` | ported |  |
| FrameSelector.js:1279-1307 | FrameSelector.writeManifestLog | `frames_delete.write_manifest_log` | ported |  |
| FrameSelector.js:1313-1336 | FrameSelector.appendOutcome | `frames_delete.append_outcome` | ported |  |
| FrameSelector.js:1350-1418 | FrameSelector.execute | `frames_delete.execute` | ported |  |
| FrameSelector.js:1441-1441 | FrameSelector.LABEL_GAP | `frames_ui.LABEL_GAP` | ported |  |
| FrameSelector.js:1448-1448 | FrameSelector.SCROLL_LINE | `frames_ui.SCROLL_LINE` | ported |  |
| FrameSelector.js:1454-1455 | FrameSelector.TAG_COLOURS | `frames_preview.tag_colours` | ported |  |
| FrameSelector.js:1456-1456 | FrameSelector.TAG | `frames_ui.TAG` | ported |  |
| FrameSelector.js:1459-1459 | FrameSelector.AUTO_STYLE | `frames_ui.AUTO_STYLE` | ported |  |
| FrameSelector.js:1462-1462 | FrameSelector.THUMB | `frames_ui.THUMB` | ported |  |
| FrameSelector.js:1463-1463 | FrameSelector.THUMB_CAP | `frames_ui.THUMB_CAP` | ported |  |
| FrameSelector.js:1466-1466 | FrameSelector.PICK_RADIUS | `frames_ui.PICK_RADIUS` | ported |  |
| FrameSelector.js:1491 | PreviewControl.constructor (method) | `frames_preview.PreviewControl.__init__` | ported |  |
| FrameSelector.js:1616 | PreviewControl.setFit (method) | `frames_preview.PreviewControl.set_fit` | ported |  |
| FrameSelector.js:1629 | PreviewControl.centreOn (method) | `frames_preview.PreviewControl.centre_on` | ported |  |
| FrameSelector.js:1653 | PreviewControl.fittedPixelAt (method) | `frames_preview.PreviewControl.fitted_pixel_at` | ported |  |
| FrameSelector.js:1673 | PreviewControl.layOutScroll (method) | `frames_preview.PreviewControl.lay_out_scroll` | ported |  |
| FrameSelector.js:1741 | PreviewControl.pan (method) | `frames_preview.PreviewControl.pan` | ported |  |
| FrameSelector.js:1754 | PreviewControl.paintViewport (method) | `frames_preview.PreviewControl.paint_viewport` | ported |  |
| FrameSelector.js:1807 | PreviewControl.dispose (method) | `frames_preview.PreviewControl.dispose` | ported |  |
| FrameSelector.js:1813 | PreviewControl.setTags (method) | `frames_preview.PreviewControl.set_tags` | ported |  |
| FrameSelector.js:1823 | PreviewControl.tagRects (method) | `frames_preview.View.tag_rects` | ported |  |
| FrameSelector.js:1835 | PreviewControl.paintTags (method) | `frames_preview.PreviewControl.paint_tags` | ported |  |
| FrameSelector.js:1848 | PreviewControl.thumbnail (method) | `frames_preview.PreviewControl.thumbnail` | ported |  |
| FrameSelector.js:1869 | PreviewControl.release (method) | `frames_preview.PreviewControl.release` | ported |  |
| FrameSelector.js:1893 | PreviewControl.load (method) | `frames_preview.PreviewControl.load` | ported |  |
| FrameSelector.js:1938-1964 | FrameSelector.emptyState | `frames_state.empty_state` | ported |  |
| FrameSelector.js:1973-1978 | FrameSelector.buildStateFrom | `frames_state.build_state_from` | ported |  |
| FrameSelector.js:1980-1995 | FrameSelector.stateFromScan | `frames_state.state_from_scan` | ported |  |
| FrameSelector.js:1997-2001 | FrameSelector.buildState | `frames_state.build_state` | ported |  |
| FrameSelector.js:2010-2025 | FrameSelector.approvedLightRecords | `frames_state.approved_light_records` | ported |  |
| FrameSelector.js:2034-2052 | FrameSelector.convertOne | `asiair_import.convert_one` | replaced | copy of the FITS bytes (deviation 8) |
| FrameSelector.js:2058-2058 | FrameSelector.REQUIRED_KEYWORDS | `asiair_import.REQUIRED_KEYWORDS` | ported |  |
| FrameSelector.js:2072-2104 | FrameSelector.verifyImported | `asiair_import.verify_imported` | replaced | digest comparison first (deviation 8) |
| FrameSelector.js:2110-2132 | FrameSelector.writeManifest | `asiair_import.write_manifest` | ported |  |
| FrameSelector.js:2140-2145 | FrameSelector.readCardDarks | `asiair_import.read_card_darks` | ported |  |
| FrameSelector.js:2148-2151 | FrameSelector.cardDarksFound | `asiair_import.card_darks_found` | ported |  |
| FrameSelector.js:2154-2157 | FrameSelector.cardDarksUsed | `asiair_import.card_darks_used` | ported |  |
| FrameSelector.js:2160-2163 | FrameSelector.darkSourceOf | `asiair_import.dark_source_of` | ported |  |
| FrameSelector.js:2171-2226 | FrameSelector.makeMasterFlats | `asiair_import.make_master_flats` | ported |  |
| FrameSelector.js:2233-2247 | FrameSelector.importOutcome | `asiair_import.import_outcome` | ported |  |
| FrameSelector.js:2259-2270 | FrameSelector.outputsAreSafe | `asiair_import.outputs_are_safe` | ported |  |
| FrameSelector.js:2277-2290 | FrameSelector.resolved | `asiair_import.resolved` | ported |  |
| FrameSelector.js:2294 | Dialog.constructor (method) | `frames_dialog.Dialog.__init__` | ported |  |
| FrameSelector.js:2338 | Dialog.buildChannelTree (method) | `frames_dialog.Dialog.build_channel_tree` | ported |  |
| FrameSelector.js:2367 | Dialog.buildPlotPane (method) | `frames_dialog.Dialog.build_plot_pane` | ported |  |
| FrameSelector.js:2404 | Dialog.selectedRowIndex (method) | `frames_dialog.Dialog.selected_row_index` | ported |  |
| FrameSelector.js:2423 | Dialog.selectRow (method) | `frames_dialog.Dialog.select_row` | ported |  |
| FrameSelector.js:2446 | Dialog.rowNode (method) | `frames_dialog.Dialog.row_iid` | ported |  |
| FrameSelector.js:2454 | Dialog.loadPreview (method) | `frames_dialog.Dialog.load_preview` | ported |  |
| FrameSelector.js:2474 | Dialog.showPreview (method) | `frames_dialog.Dialog.show_preview` | ported |  |
| FrameSelector.js:2482 | Dialog.channelStretch (method) | `frames_dialog.Dialog.channel_stretch` | ported |  |
| FrameSelector.js:2492 | Dialog.currentTags (method) | `frames_state.current_tags` | ported |  |
| FrameSelector.js:2506 | Dialog.chooseDestination (method) | `frames_dialog.Dialog.choose_destination` | ported |  |
| FrameSelector.js:2517 | Dialog.plotMetric (method) | `frames_dialog.Dialog.plot_metric` | ported |  |
| FrameSelector.js:2523 | Dialog.fillPlot (method) | `frames_dialog.Dialog.fill_plot` | ported |  |
| FrameSelector.js:2557 | Dialog.buildFrameTable (method) | `frames_dialog.Dialog.build_frame_table` | ported |  |
| FrameSelector.js:2592 | Dialog.buildPreviewPane (method) | `frames_dialog.Dialog.build_preview_pane` | ported |  |
| FrameSelector.js:2620 | Dialog.buildKnobPanel (method) | `frames_dialog.Dialog.build_knob_panel` | ported |  |
| FrameSelector.js:2744 | Dialog.applyEdit (method) | `frames_dialog.Dialog.apply_edit` | ported |  |
| FrameSelector.js:2756 | Dialog.commitEdit (method) | `frames_dialog.Dialog.commit_edit` | ported |  |
| FrameSelector.js:2767 | Dialog.commitPendingEdits (method) | `frames_dialog.Dialog.commit_pending_edits` | ported |  |
| FrameSelector.js:2781 | Dialog.buildFilmstrip (method) | `frames_dialog.Dialog.build_filmstrip` | ported |  |
| FrameSelector.js:2830 | Dialog.armLoader (method) | `frames_filmstrip.ThumbLoader.arm_loader` | ported |  |
| FrameSelector.js:2847 | Dialog.mayLoad (method) | `frames_filmstrip.ThumbLoader.may_load` | ported |  |
| FrameSelector.js:2853 | Dialog.rebuildQueue (method) | `frames_filmstrip.ThumbLoader.rebuild_queue` | ported |  |
| FrameSelector.js:2882 | Dialog.queueOtherChannels (method) | `frames_filmstrip.ThumbLoader.queue_other_channels` | ported |  |
| FrameSelector.js:2902 | Dialog.loaderTick (method) | `frames_filmstrip.ThumbLoader.loader_tick` | ported |  |
| FrameSelector.js:2949 | Dialog.storeThumb (method) | `frames_filmstrip.ThumbLoader.store_thumb` | ported |  |
| FrameSelector.js:2967 | Dialog.pushThumbs (method) | `frames_filmstrip.ThumbLoader.push_thumbs` | ported |  |
| FrameSelector.js:2987 | Dialog.syncStrip (method) | `frames_filmstrip.ThumbLoader.sync_strip` | ported |  |
| FrameSelector.js:3000 | Dialog.buildActionRow (method) | `frames_dialog.Dialog.build_action_row` | ported |  |
| FrameSelector.js:3072 | Dialog.editable (method) | `frames_state.editable` | ported |  |
| FrameSelector.js:3078 | Dialog.channel (method) | `frames_state.channel_of` | ported |  |
| FrameSelector.js:3083 | Dialog.toggleOverride (method) | `frames_dialog.Dialog.toggle_override` | ported |  |
| FrameSelector.js:3105 | Dialog.fillChannels (method) | `frames_dialog.Dialog.fill_channels` | ported |  |
| FrameSelector.js:3121 | Dialog.fillFrames (method) | `frames_dialog.Dialog.fill_frames` | ported |  |
| FrameSelector.js:3142 | Dialog.fillRow (method) | `frames_dialog.Dialog.fill_row` | ported |  |
| FrameSelector.js:3167 | Dialog.flagLine (method) | `frames_state.flag_line` | ported |  |
| FrameSelector.js:3182 | Dialog.syncCriteria (method) | `frames_dialog.Dialog.sync_criteria` | ported |  |
| FrameSelector.js:3198 | Dialog.syncKnobs (method) | `frames_dialog.Dialog.sync_knobs` | ported |  |
| FrameSelector.js:3237 | Dialog.committableRows (method) | `frames_state.committable_rows` | ported |  |
| FrameSelector.js:3255 | Dialog.refresh (method) | `frames_dialog.Dialog.refresh` | ported |  |
| FrameSelector.js:3286 | Dialog.recomputeAll (method) | `frames_state.recompute_all` | ported |  |
| FrameSelector.js:3306 | Dialog.summaryText (method) | `frames_state.summary_text` | ported |  |
| FrameSelector.js:3322 | Dialog.syncActions (method) | `frames_dialog.Dialog.sync_actions` | ported |  |
| FrameSelector.js:3337 | Dialog.destinationText (method) | `frames_state.destination_text` | ported |  |
| FrameSelector.js:3354 | Dialog.allPaths (method) | `frames_state.all_paths` | ported |  |
| FrameSelector.js:3370 | Dialog.destinationIsSource (method) | `frames_state.destination_is_source` | ported |  |
| FrameSelector.js:3380 | Dialog.release (method) | `frames_dialog.Dialog.release` | ported |  |
| FrameSelector.js:3394 | Dialog.stopLoader (method) | `frames_dialog.Dialog.stop_loader` | ported |  |
| FrameSelector.js:3411 | Dialog.detachHandlers (method) | `frames_dialog.Dialog.detach_handlers` | ported |  |
| FrameSelector.js:3433 | Dialog.copyingOut (method) | `frames_state.copying_out` | ported |  |
| FrameSelector.js:3439 | Dialog.approvedPaths (method) | `frames_state.approved_paths` | ported |  |
| FrameSelector.js:3461 | Dialog.reportOutcome (method) | `frames_dialog_actions.DialogActions.report_outcome` | ported |  |
| FrameSelector.js:3479 | Dialog.commitCopy (method) | `frames_dialog_actions.DialogActions.commit_copy` | ported |  |
| FrameSelector.js:3510 | Dialog.buildMasterFlatControls (method) | `frames_dialog.Dialog.build_master_flat_controls` | ported |  |
| FrameSelector.js:3569 | Dialog.setMasterFlatSettings (method) | `frames_dialog.Dialog.set_master_flat_settings` | ported |  |
| FrameSelector.js:3578 | Dialog.masterFlatsMode (method) | `frames_state.master_flats_mode` | ported |  |
| FrameSelector.js:3585 | Dialog.syncMasterFlatControls (method) | `frames_dialog.Dialog.sync_master_flat_controls` | ported |  |
| FrameSelector.js:3606 | Dialog.importing (method) | `frames_state.importing` | ported |  |
| FrameSelector.js:3615 | Dialog.commitImport (method) | `frames_dialog_actions.DialogActions.commit_import` | ported |  |
| FrameSelector.js:3732 | Dialog.matchedFlats (method) | `frames_state.matched_flats` | ported |  |
| FrameSelector.js:3762 | Dialog.commit (method) | `frames_dialog_actions.DialogActions.commit` | ported |  |
| FrameSelector.js:3829 | Dialog.layOutCriteria (method) | `frames_dialog.Dialog.lay_out_criteria` | ported |  |
| FrameSelector.js:3865 | Dialog.layOut (method) | `frames_dialog.Dialog.lay_out` | ported |  |
| FrameSelector.js:3954-3981 | FrameSelector.runOutputRoutine | `frames_copy.run_output_routine` | ported |  |
| FrameSelector.js:3994-4014 | FrameSelector.convertInPlace | `frames_copy.convert_in_place` | ported |  |
| FrameSelector.js:4020-4051 | FrameSelector.convertGroup | - | not ported | no conversion of kept frames after a delete (deviation 10) |
| FrameSelector.js:4057-4079 | FrameSelector.settleConverted | - | not ported | no conversion of kept frames after a delete (deviation 10) |
| FrameSelector.js:4092-4139 | FrameSelector.exportApproved | `frames_copy.export_approved` | ported |  |
| FrameSelector.js:4147-4157 | FrameSelector.emptyDirectory | `frames_copy.empty_directory` | ported |  |
| FrameSelector.js:4159-4180 | FrameSelector.removeEntry | `frames_copy.remove_entry` | ported |  |
| FrameSelector.js:4187-4195 | FrameSelector.exportRefusal | `frames_copy.export_refusal` | ported |  |
| FrameSelector.js:4201-4211 | FrameSelector.confirmWritten | `frames_copy.confirm_written` | ported |  |
| FrameSelector.js:4233-4233 | FrameSelector.REJECT_COLOUR | `frames_ui.REJECT_COLOUR` | ported |  |
| FrameSelector.js:4234-4234 | FrameSelector.MARK_SIZE | `frames_ui.MARK_SIZE` | ported |  |
| FrameSelector.js:4236-4262 | FrameSelector.rejectIcon | `frames_ui.reject_icon` | ported |  |
| FrameSelector.js:4272-4284 | FrameSelector.blankIcon | `frames_ui.blank_icon` | ported |  |
| FrameSelector.js:4287-4290 | FrameSelector.markIcon | `frames_ui.mark_icon` | ported |  |
| FrameSelector.js:4297-4316 | FrameSelector.PLOT_COLOURS | `frames_ui.PLOT_COLOURS` | ported |  |
| FrameSelector.js:4320 | Plot.constructor (method) | `frames_plot_widget.FramesPlot.__init__` | ported |  |
| FrameSelector.js:4353 | Plot.setSeries (method) | `frames_plot_widget.FramesPlot.set_series` | ported |  |
| FrameSelector.js:4362 | Plot.release (method) | `frames_plot_widget.FramesPlot.release` | ported |  |
| FrameSelector.js:4378 | Plot.setSelected (method) | `frames_plot_widget.FramesPlot.set_selected` | ported |  |
| FrameSelector.js:4387 | Plot.paint (method) | `frames_plot_widget.FramesPlot.paint` | ported |  |
| FrameSelector.js:4410 | Plot.paintOn (method) | `frames_plot_widget.FramesPlot._draw` | ported |  |
| FrameSelector.js:4429 | Plot.layout (method) | `frames_plot_widget.layout` | ported |  |
| FrameSelector.js:4454 | Plot.drawBand (method) | `frames_plot_widget._band_shapes` | ported |  |
| FrameSelector.js:4469 | Plot.drawAxisAndLabels (method) | `frames_plot_widget._axis_shapes` | ported |  |
| FrameSelector.js:4499 | Plot.drawLine (method) | `frames_plot_widget._line_shapes` | ported |  |
| FrameSelector.js:4522 | Plot.drawMarkers (method) | `frames_plot_widget._marker_shapes` | ported |  |
| FrameSelector.js:4547 | Plot.drawRing (method) | `frames_plot_widget._ring_shapes` | ported |  |
| FrameSelector.js:4567 | Filmstrip.constructor (method) | `frames_filmstrip.Filmstrip.__init__` | ported |  |
| FrameSelector.js:4596 | Filmstrip.setMetric (method) | `frames_filmstrip.Filmstrip.set_metric` | ported |  |
| FrameSelector.js:4602 | Filmstrip.setRows (method) | `frames_filmstrip.Filmstrip.set_rows` | ported |  |
| FrameSelector.js:4612 | Filmstrip.page (method) | `frames_filmstrip.Filmstrip.page` | ported |  |
| FrameSelector.js:4620 | Filmstrip.indexAt (method) | `frames_filmstrip.Filmstrip.index_at` | ported |  |
| FrameSelector.js:4625 | Filmstrip.paintTile (method) | `frames_filmstrip.Filmstrip._paint_tile` | ported |  |
| FrameSelector.js:4669 | Filmstrip.paint (method) | `frames_filmstrip.Filmstrip.paint` | ported |  |
| FrameSelector.js:4686 | Filmstrip.release (method) | `frames_filmstrip.Filmstrip.release` | ported |  |
| FrameSelector.js:4704-4704 | FrameSelector.SCAN_NAME_CHARS | `frames_ui.SCAN_NAME_CHARS` | ported |  |
| FrameSelector.js:4708 | ScanWindow.constructor (method) | `frames_ui.ScanWindow.__init__` | ported |  |
| FrameSelector.js:4809 | ScanWindow.report (method) | `frames_ui.ScanWindow.report` | ported |  |
| FrameSelector.js:4828 | ScanWindow.display (method) | `frames_ui.ScanWindow.display` | ported |  |
| FrameSelector.js:4851 | ScanWindow.announce (method) | `frames_ui.ScanWindow.announce` | ported |  |
| FrameSelector.js:4857 | ScanWindow.release (method) | `frames_ui.ScanWindow.release` | ported |  |
| FrameSelector.js:4868 | ScanWindow.callbacks (method) | `frames_ui.ScanWindow.callbacks` | ported |  |
| FrameSelector.js:4895-4898 | FrameSelector.tell | `frames_ui.tell` | ported |  |
| FrameSelector.js:4900-4904 | FrameSelector.ask | `frames_ui.ask` | ported |  |
| FrameSelector.js:4916-4936 | FrameSelector.withProgress | `frames_ui.with_progress` | ported |  |
| FrameSelector.js:4942-4961 | FrameSelector.detectCard | `frames_main.detect_card` | ported |  |
| FrameSelector.js:4967-4990 | FrameSelector.readCard | `frames_main.read_card` | ported |  |
| FrameSelector.js:4993-5002 | FrameSelector.pickNight | `frames_main.pick_night` | ported |  |
| FrameSelector.js:5011-5055 | FrameSelector.offerCard | `frames_main.offer_card` | ported |  |
| FrameSelector.js:5057-5097 | FrameSelector.main | `frames_main.main` | ported |  |
| FrameSelector.js:5106-5120 | FrameSelector.CORE_CHECK | `frames_main.check_siril` | replaced | Siril version check (deviation 14) |
| FrameSelector.js:5122-5140 | main | `Loom/loom_frame_selector.py` (`main`) | ported |  |

### lib/Frames.js

| JS lines | JS name | Python | Status | Note |
|---|---|---|---|---|
| Frames.js:21-21 | Frames.MEASURE_VERSION | `frames.MEASURE_VERSION` | ported |  |
| Frames.js:42-43 | Frames.COL | `frames.COL` | ported |  |
| Frames.js:45-57 | Frames.metricsFromRow | `frames.metrics_from_row` | ported |  |
| Frames.js:64-64 | Frames.OPTIONAL | `frames.OPTIONAL` | ported |  |
| Frames.js:66-69 | Frames.optionalOk | `frames.optional_ok` | ported |  |
| Frames.js:72-79 | Frames.optionalProblems | `frames.optional_problems` | ported |  |
| Frames.js:85-94 | Frames.sanitizeOptional | `frames.sanitize_optional` | ported |  |
| Frames.js:100-101 | Frames.STORED_KEYS | `frames.STORED_KEYS` | ported |  |
| Frames.js:103-112 | Frames.storedMetrics | `frames.stored_metrics` | ported |  |
| Frames.js:119-127 | Frames.cacheEntryUsable | `frames.cache_entry_usable` | ported |  |
| Frames.js:155-160 | Frames.PLAUSIBLE | `frames.PLAUSIBLE` | ported |  |
| Frames.js:162-166 | Frames.metricInRange | `frames.metric_in_range` | ported |  |
| Frames.js:169-172 | Frames.isWholeCount | `frames.is_whole_count` | ported |  |
| Frames.js:175-178 | Frames.looksLikeCount | `frames.looks_like_count` | ported |  |
| Frames.js:199-213 | Frames.meaningProblems | `frames.meaning_problems` | ported |  |
| Frames.js:220-220 | Frames.METRICS | `frames.METRICS` | ported |  |
| Frames.js:222-224 | Frames.STATE | `frames.STATE` | ported |  |
| Frames.js:234-237 | Frames.metricValid | `frames.metric_valid` | ported |  |
| Frames.js:244-252 | Frames.frameValid | `frames.frame_valid` | ported |  |
| Frames.js:260-260 | Frames.MIN_FRAMES | `frames.MIN_FRAMES` | ported |  |
| Frames.js:268-268 | Frames.SIGMA_FLOOR_FRACTION | `frames.SIGMA_FLOOR_FRACTION` | ported |  |
| Frames.js:271-272 | Frames.WORSE_WHEN | `frames.WORSE_WHEN` | ported |  |
| Frames.js:274-281 | Frames.median | `frames.median` | ported |  |
| Frames.js:288-297 | Frames.sigma | `frames.sigma` | ported |  |
| Frames.js:304-318 | Frames.gate | `frames.gate` | ported |  |
| Frames.js:325-332 | Frames.validOnly | `frames.valid_only` | ported |  |
| Frames.js:334-340 | Frames.columnOf | `frames.column_of` | ported |  |
| Frames.js:353-373 | Frames.relativeGates | `frames.relative_gates` | ported |  |
| Frames.js:381-382 | Frames.DEFAULT_WEIGHTS | `frames.DEFAULT_WEIGHTS` | ported |  |
| Frames.js:407-408 | Frames.DEFAULT_GATING | `frames.DEFAULT_GATING` | ported |  |
| Frames.js:415-415 | Frames.CLAMP | `frames.CLAMP` | ported |  |
| Frames.js:417-426 | Frames.medians | `frames.medians` | ported |  |
| Frames.js:435-451 | Frames.score | `frames.score` | ported |  |
| Frames.js:458-458 | Frames.PRESETS | `frames.PRESETS` | ported |  |
| Frames.js:459-459 | Frames.PRESET_NAMES | `frames.PRESET_NAMES` | ported |  |
| Frames.js:460-460 | Frames.DEFAULT_PRESET | `frames.DEFAULT_PRESET` | ported |  |
| Frames.js:474-500 | Frames.defaultSettings | `frames.default_settings` | ported |  |
| Frames.js:502-508 | Frames.copyOf | `frames.copy_of` | ported |  |
| Frames.js:520-538 | Frames.applyPreset | `frames.apply_preset` | ported |  |
| Frames.js:540-542 | Frames.METRIC_LABEL | `frames.METRIC_LABEL` | ported |  |
| Frames.js:545-546 | Frames.METRIC_HEADING | `frames.METRIC_HEADING` | ported |  |
| Frames.js:553-553 | Frames.DISPLAY_METRICS | `frames.DISPLAY_METRICS` | ported |  |
| Frames.js:567-569 | Frames.FRAME_COLUMNS | `frames.FRAME_COLUMNS` | ported |  |
| Frames.js:579-588 | Frames.acceptedBand | `frames.accepted_band` | ported |  |
| Frames.js:591-598 | Frames.relativeBand | `frames.relative_band` | ported |  |
| Frames.js:601-611 | Frames.narrowBand | `frames.narrow_band` | ported |  |
| Frames.js:623-638 | Frames.pointAt | `frames_plot.point_at` | ported |  |
| Frames.js:650-670 | Frames.spacedLabels | `frames_plot.spaced_labels` | ported |  |
| Frames.js:678-706 | Frames.plotBounds | `frames_plot.plot_bounds` | ported |  |
| Frames.js:709-716 | Frames.metricColumn | `frames_plot.metric_column` | ported |  |
| Frames.js:719-724 | Frames.displayValue | `frames_plot.display_value` | ported |  |
| Frames.js:734-751 | Frames.relativeFailures | `frames.relative_failures` | ported |  |
| Frames.js:758-776 | Frames.absoluteFailures | `frames.absolute_failures` | ported |  |
| Frames.js:782-794 | Frames.autoGates | `frames.auto_gates` | ported |  |
| Frames.js:802-828 | Frames.verdict | `frames.verdict` | ported |  |
| Frames.js:832-832 | Frames.OPERATOR | `frames.OPERATOR` | ported |  |
| Frames.js:833-833 | Frames.LIMIT_DECIMALS | `frames.LIMIT_DECIMALS` | ported |  |
| Frames.js:835-835 | Frames.CRITERIA_ORDER | `frames.CRITERIA_ORDER` | ported |  |
| Frames.js:843-852 | Frames.parseLimit | `frames.parse_limit` | ported |  |
| Frames.js:854-860 | Frames.formatLimit | `frames.format_limit` | ported |  |
| Frames.js:863-870 | Frames.limitValue | `frames.limit_value` | ported |  |
| Frames.js:877-887 | Frames.withLimit | `frames.with_limit` | ported |  |
| Frames.js:894-901 | Frames.displayLimit | `frames.display_limit` | ported |  |
| Frames.js:904-912 | Frames.round | `frames_ui._round` | ported |  |
| Frames.js:932-933 | Frames.CALIBRATION_KINDS | `frames_groups.CALIBRATION_KINDS` | ported |  |
| Frames.js:935-942 | Frames.notSubframe | `frames_groups.not_subframe` | ported |  |
| Frames.js:945-953 | Frames.calibrationKind | `frames_groups.calibration_kind` | ported |  |
| Frames.js:956-969 | Frames.skippedCounts | `frames_groups.skipped_counts` | ported |  |
| Frames.js:972-980 | Frames.skippedLine | `frames_groups.skipped_line` | ported |  |
| Frames.js:983-989 | Frames.unreadableLine | `frames_groups.unreadable_line` | ported |  |
| Frames.js:992-999 | Frames.noSubframesMessage | `frames_groups.no_subframes_message` | ported |  |
| Frames.js:1001-1001 | Frames.NO_FILTER | `frames.NO_FILTER` | ported |  |
| Frames.js:1014-1032 | Frames.groupByFilter | `frames_groups.group_by_filter` | ported |  |
| Frames.js:1039-1042 | Frames.autoRejectAllowed | `frames.auto_reject_allowed` | ported |  |
| Frames.js:1058-1062 | Frames.normalImageType | `frames_groups.normal_image_type` | ported |  |
| Frames.js:1064-1108 | Frames.comparability | `frames_groups.comparability` | ported |  |
| Frames.js:1118-1118 | Frames.OVERRIDE | `frames.OVERRIDE` | ported |  |
| Frames.js:1120-1135 | Frames.finalState | `frames.final_state` | ported |  |
| Frames.js:1148-1152 | Frames.leftOut | `frames.left_out` | ported |  |
| Frames.js:1154-1161 | Frames.keepCount | `frames.keep_count` | ported |  |
| Frames.js:1163-1178 | Frames.counts | `frames.counts` | ported |  |
| Frames.js:1181-1189 | Frames.summaryLine | `frames.summary_line` | ported |  |
| Frames.js:1211-1212 | Frames.PHASE | `frames.PHASE` | ported |  |
| Frames.js:1214-1214 | Frames.canEdit | `frames.can_edit` | ported |  |
| Frames.js:1216-1227 | Frames.nextPhase | `frames.next_phase` | ported |  |
| Frames.js:1229-1259 | Frames.buildManifest | `frames.build_manifest` | ported |  |
| Frames.js:1261-1268 | Frames.manifestPending | `frames.manifest_pending` | ported |  |
| Frames.js:1270-1279 | Frames.recordOutcome | `frames.record_outcome` | ported |  |
| Frames.js:1289-1329 | Frames.identityMatches | `frames.identity_matches` | ported |  |
| Frames.js:1330-1330 | Frames.DIGEST_SETTLE_MS | `frames_files.DIGEST_SETTLE_MS` | ported |  |
| Frames.js:1333-1342 | Frames.digestCacheEntry | `frames_files.digest_cache_entry` | ported |  |
| Frames.js:1345-1350 | Frames.digestCacheStored | `frames_files.digest_cache_stored` | ported |  |
| Frames.js:1352-1360 | Frames.digestCacheHit | `frames_files.digest_cache_hit` | ported |  |
| Frames.js:1363-1367 | Frames.digestRacy | `frames_files.digest_racy` | ported |  |
| Frames.js:1370-1377 | Frames.digestCacheRecord | `frames_files.digest_cache_record` | ported |  |
| Frames.js:1396-1396 | Frames.SEPARATORS | `frames_files.SEPARATORS` | ported |  |
| Frames.js:1398-1414 | Frames.shortNames | `frames_files.short_names` | ported |  |
| Frames.js:1417-1428 | Frames.commonPrefix | `frames_files.common_prefix` | ported |  |
| Frames.js:1431-1438 | Frames.lastSeparatorIn | `frames_files.last_separator_in` | ported |  |
| Frames.js:1440-1444 | Frames.outputName | `frames_files.output_name` | ported |  |
| Frames.js:1473-1473 | Frames.XISF | `frames_files.needing_xisf` | ported |  |
| Frames.js:1483-1493 | Frames.needingXisf | `frames_files.needing_xisf` | ported |  |
| Frames.js:1496-1510 | Frames.byDirectory | `frames_files.by_directory` | ported |  |
| Frames.js:1512-1520 | Frames.destinationIsSource | `frames_files.destination_is_source` | ported |  |
| Frames.js:1531-1547 | Frames.emptyRefusal | `frames_files.empty_refusal` | ported |  |
| Frames.js:1549-1552 | Frames.withoutTrailingSlash | `frames_files.without_trailing_slash` | ported |  |
| Frames.js:1554-1570 | Frames.outputMapping | `frames_files.output_mapping` | ported |  |
| Frames.js:1581-1611 | Frames.newChannel | `frames.new_channel` | ported |  |
| Frames.js:1619-1644 | Frames.recompute | `frames.recompute` | ported |  |
| Frames.js:1647-1667 | Frames.judgeRow | `frames.judge_row` | ported |  |
| Frames.js:1691-1691 | Frames.FLAG_K | `frames_flags.FLAG_K` | ported |  |
| Frames.js:1692-1692 | Frames.FLAG_MIN_FRAMES | `frames_flags.FLAG_MIN_FRAMES` | ported |  |
| Frames.js:1693-1693 | Frames.DROPPED_FRACTION | `frames_flags.DROPPED_FRACTION` | ported |  |
| Frames.js:1695-1695 | Frames.BLUR_MARGIN | `frames_flags.BLUR_MARGIN` | ported |  |
| Frames.js:1697-1697 | Frames.SEEING_AIRMASS_POWER | `frames_flags.SEEING_AIRMASS_POWER` | ported |  |
| Frames.js:1699-1699 | Frames.EXTINCTION | `frames_flags.EXTINCTION` | ported |  |
| Frames.js:1701-1701 | Frames.CLOUD_DIMMING | `frames_flags.CLOUD_DIMMING` | ported |  |
| Frames.js:1708-1708 | Frames.CLOUD_BACKGROUND_RISE | `frames_flags.CLOUD_BACKGROUND_RISE` | ported |  |
| Frames.js:1709-1709 | Frames.FLAG_ORDER | `frames_flags.FLAG_ORDER` | ported |  |
| Frames.js:1710-1711 | Frames.FLAG_TAG | `frames_flags.FLAG_TAG` | ported |  |
| Frames.js:1712-1713 | Frames.FLAG_BADGE | `frames_flags.FLAG_BADGE` | ported |  |
| Frames.js:1715-1715 | Frames.FLAG_SUMMARY_ORDER | `frames_flags.FLAG_SUMMARY_ORDER` | ported |  |
| Frames.js:1718-1729 | Frames.obsTime | `frames_flags.obs_time` | ported |  |
| Frames.js:1732-1737 | Frames.airmass | `frames_flags.airmass` | ported |  |
| Frames.js:1739-1739 | Frames.isNumber | `frames_flags.is_number` | ported |  |
| Frames.js:1742-1746 | Frames.lowerQuartile | `frames_flags.lower_quartile` | ported |  |
| Frames.js:1754-1768 | Frames.flagBaseline | `frames_flags.flag_baseline` | ported |  |
| Frames.js:1771-1775 | Frames.flagHigh | `frames_flags.flag_high` | ported |  |
| Frames.js:1781-1790 | Frames.corrected | `frames_flags.corrected` | ported |  |
| Frames.js:1797-1811 | Frames.flagReferences | `frames_flags.flag_references` | ported |  |
| Frames.js:1819-1845 | Frames.blurCause | `frames_flags.blur_cause` | ported |  |
| Frames.js:1848-1855 | Frames.isCloud | `frames_flags.is_cloud` | ported |  |
| Frames.js:1858-1871 | Frames.timeOrder | `frames_flags.time_order` | ported |  |
| Frames.js:1873-1902 | Frames.anomalyFlags | `frames_flags.anomaly_flags` | ported |  |
| Frames.js:1905-1925 | Frames.flagSummary | `frames_flags.flag_summary` | ported |  |
| Frames.js:1928-1935 | Frames.airmassLine | `frames_flags.airmass_line` | ported |  |
| Frames.js:1938-1948 | Frames.rowTooltip | `frames_flags.row_tooltip` | ported |  |
| Frames.js:1951-1960 | Frames.frameTags | `frames_flags.frame_tags` | ported |  |
| Frames.js:1971-1981 | Frames.stripFirst | `frames_files.strip_first` | ported |  |
| Frames.js:1988-1997 | Frames.tileAt | `frames_files.tile_at` | ported |  |
| Frames.js:2004-2016 | Frames.thumbnailOrder | `frames_files.thumbnail_order` | ported |  |
| Frames.js:2027-2033 | Frames.batchesOf | `frames_files.batches_of` | ported |  |
| Frames.js:2062-2074 | Frames.measureInBatches | `frames_batches.measure_in_batches` | ported |  |
| Frames.js:2077-2107 | Frames.runBatches | `frames_batches.run_batches` | ported |  |
| Frames.js:2109-2109 | Frames.NO_SETTLER | `frames_batches.NO_SETTLER` | ported |  |
| Frames.js:2119-2134 | Frames.throttle | `frames_batches.throttle` | ported |  |
| Frames.js:2141-2155 | Frames.mergeBatch | `frames_files.merge_batch` | ported |  |
| Frames.js:2166-2179 | Frames.measuringLines | `frames_files.measuring_lines` | ported |  |

### lib/Asiair.js

| JS lines | JS name | Python | Status | Note |
|---|---|---|---|---|
| Asiair.js:11-11 | Asiair | - | PixInsight only | namespace object |
| Asiair.js:13-13 | Asiair.MOUNTS | `asiair_card.MOUNT_ROOTS` | ported |  |
| Asiair.js:14-14 | Asiair.SOURCES | `asiair_card.SOURCES` | ported |  |
| Asiair.js:29-37 | Asiair.looksLikeCard | `asiair_card.looks_like_card` | ported |  |
| Asiair.js:47-66 | Asiair.detect | `asiair_card.detect` | ported |  |
| Asiair.js:75-79 | Asiair.entriesIn | `asiair_card.entries_in` | ported |  |
| Asiair.js:88-103 | Asiair.frameFrom | `asiair_card.frame_from` | ported |  |
| Asiair.js:113-221 | Asiair.scanCard | `asiair_card.scan_card` | ported |  |
| Asiair.js:113-221 | Asiair.scanCard (inner functions alive, processFrame, reportProgress, take, scanLights, scanFlats, scanDarks) | `asiair_card.scan_card` | ported |  |
| Asiair.js:246-255 | Asiair.describe | `asiair_card.describe` | ported |  |

### lib/AsiairNames.js

| JS lines | JS name | Python | Status | Note |
|---|---|---|---|---|
| AsiairNames.js:23-32 | AsiairNames | - | PixInsight only | namespace object |
| AsiairNames.js:43-43 | AsiairNames.TYPES | `asiair_names.TYPES` | ported |  |
| AsiairNames.js:56-74 | AsiairNames.tailFields | `asiair_names.tail_fields` | ported |  |
| AsiairNames.js:77-83 | AsiairNames.lastMatch | `asiair_names.last_match` | ported |  |
| AsiairNames.js:86-97 | AsiairNames.onlyMatch | `asiair_names.only_match` | ported |  |
| AsiairNames.js:99-105 | AsiairNames.isKnownType | `asiair_names.is_known_type` | ported |  |
| AsiairNames.js:118-161 | AsiairNames.parseName | `asiair_names.parse_name` | ported |  |
| AsiairNames.js:164-167 | AsiairNames.stampInRange | `asiair_names.stamp_in_range` | ported |  |
| AsiairNames.js:170-174 | AsiairNames.stampRolled | `asiair_names.stamp_rolled` | ported |  |
| AsiairNames.js:192-219 | AsiairNames.stampKey | `asiair_names.stamp_key` | ported |  |
| AsiairNames.js:221-221 | AsiairNames.GAP_HOURS | `asiair_names.GAP_HOURS` | ported |  |
| AsiairNames.js:237-261 | AsiairNames.sessions | `asiair_names.sessions` | ported |  |
| AsiairNames.js:277-288 | AsiairNames.filtersOf | `asiair_names.filters_of` | ported |  |
| AsiairNames.js:291-301 | AsiairNames.byTarget | `asiair_names.by_target` | ported |  |
| AsiairNames.js:303-326 | AsiairNames.nights | `asiair_names.nights` | ported |  |
| AsiairNames.js:328-331 | AsiairNames.dateOf | `asiair_names.date_of` | ported |  |
| AsiairNames.js:343-349 | AsiairNames.flatBatches | `asiair_names.flat_batches` | ported |  |
| AsiairNames.js:364-381 | AsiairNames.assignBatches | `asiair_names.assign_batches` | ported |  |
| AsiairNames.js:388-393 | AsiairNames.attributeFit | `asiair_names.attribute_fit` | ported |  |
| AsiairNames.js:416-433 | AsiairNames.flatSuits | `asiair_names.flat_suits` | ported |  |
| AsiairNames.js:436-439 | AsiairNames.mustMatch | `asiair_names.must_match` | ported |  |
| AsiairNames.js:441-463 | AsiairNames.matchFlats | `asiair_names.match_flats` | ported |  |
| AsiairNames.js:472-489 | AsiairNames.isInside | `asiair_names.is_inside` | ported |  |
| AsiairNames.js:499-507 | AsiairNames.importRoot | `asiair_names.import_root` | ported |  |
| AsiairNames.js:510-514 | AsiairNames.exposureList | `asiair_names.exposure_list` | ported |  |
| AsiairNames.js:521-527 | AsiairNames.darkSource | `asiair_names.dark_source` | ported |  |
| AsiairNames.js:537-555 | AsiairNames.masterFlatsProblem | `asiair_names.master_flats_problem` | ported |  |
| AsiairNames.js:562-579 | AsiairNames.importSummary | `asiair_names.import_summary` | ported |  |
| AsiairNames.js:590-608 | AsiairNames.skipFiltersWithFailedCopies | `asiair_names.skip_filters_with_failed_copies` | ported |  |
| AsiairNames.js:620-703 | AsiairNames.manifest | `asiair_names.manifest` | ported |  |

### lib/MasterFlat.js

| JS lines | JS name | Python | Status | Note |
|---|---|---|---|---|
| MasterFlat.js:28-28 | MasterFlat.SETTINGS_DARKS | `master_flat.SETTINGS_DARKS` | ported |  |
| MasterFlat.js:29-29 | MasterFlat.SETTINGS_ONLY | `master_flat.SETTINGS_ONLY` | ported |  |
| MasterFlat.js:32-32 | MasterFlat.MIN_FLATS | `master_flat.MIN_FLATS` | ported |  |
| MasterFlat.js:33-33 | MasterFlat.MIN_DARKS | `master_flat.MIN_DARKS` | ported |  |
| MasterFlat.js:36-36 | MasterFlat.EXPOSURE_REL | `master_flat.EXPOSURE_REL` | ported |  |
| MasterFlat.js:37-37 | MasterFlat.EXPOSURE_ABS | `master_flat.EXPOSURE_ABS` | ported |  |
| MasterFlat.js:39-39 | MasterFlat.TEMP_TOLERANCE | `master_flat.TEMP_TOLERANCE` | ported |  |
| MasterFlat.js:41-41 | MasterFlat.GAIN_TOLERANCE | `master_flat.GAIN_TOLERANCE` | ported |  |
| MasterFlat.js:48-52 | MasterFlat.settingsStore | `master_flat.SettingsStore` | ported |  |
| MasterFlat.js:55-67 | MasterFlat.loadSettings | `master_flat.load_settings` | ported |  |
| MasterFlat.js:69-77 | MasterFlat.saveSettings | `master_flat.save_settings` | ported |  |
| MasterFlat.js:85-89 | MasterFlat.active | `master_flat.active` | ported |  |
| MasterFlat.js:95-101 | MasterFlat.number | `master_flat.number` | ported |  |
| MasterFlat.js:108-120 | MasterFlat.describe | `master_flat.describe` | ported |  |
| MasterFlat.js:130-135 | masterFlatDarkKindFromImagetyp | `master_flat.master_flat_dark_kind_from_imagetyp` | ported |  |
| MasterFlat.js:137-147 | MasterFlat.darkKind | `master_flat.dark_kind` | ported |  |
| MasterFlat.js:150-165 | MasterFlat.darksOf | `master_flat.darks_of` | ported |  |
| MasterFlat.js:180-201 | MasterFlat.cardDarks | `master_flat.card_darks` | ported |  |
| MasterFlat.js:210-234 | MasterFlat.cardDarkMatch | `master_flat.card_dark_match` | ported |  |
| MasterFlat.js:242-253 | MasterFlat.readCardDarks | `master_flat.read_card_darks` | ported |  |
| MasterFlat.js:260-265 | MasterFlat.darkSourceOf | `master_flat.dark_source_of` | ported |  |
| MasterFlat.js:274-284 | MasterFlat.cardDarksUi | `master_flat.card_darks_ui` | ported |  |
| MasterFlat.js:290-295 | MasterFlat.sameExposure | `master_flat.same_exposure` | ported |  |
| MasterFlat.js:298-301 | MasterFlat.compatible | `master_flat.compatible` | ported |  |
| MasterFlat.js:308-315 | MasterFlat.darkSuits | `master_flat.dark_suits` | ported |  |
| MasterFlat.js:317-320 | masterFlatClosest | `master_flat.master_flat_closest` | ported |  |
| MasterFlat.js:332-378 | MasterFlat.chooseDark | `master_flat.choose_dark` | ported |  |
| MasterFlat.js:386-390 | MasterFlat.rawGroupKey | `master_flat.raw_group_key` | ported |  |
| MasterFlat.js:396-400 | MasterFlat.safeName | `master_flat.safe_name` | ported |  |
| MasterFlat.js:402-406 | MasterFlat.fileName | `master_flat.file_name` | ported |  |
| MasterFlat.js:416-421 | MasterFlat.rejectionFor | `master_flat.rejection_for` | ported |  |
| MasterFlat.js:424-427 | MasterFlat.darkIntegrationSpec | `master_flat.dark_integration_spec` | ported |  |
| MasterFlat.js:430-434 | MasterFlat.flatIntegrationSpec | `master_flat.flat_integration_spec` | ported |  |
| MasterFlat.js:437-443 | MasterFlat.darkCachePath | `master_flat.dark_cache_path` | ported |  |
| MasterFlat.js:462-531 | MasterFlat.buildPlan | `master_flat.build_plan` | ported |  |
| MasterFlat.js:538-550 | MasterFlat.darkShortfall | `master_flat.dark_shortfall` | ported |  |
| MasterFlat.js:564-571 | masterFlatVerifyGeometry | `master_flat_run._verify_geometry` | ported |  |
| MasterFlat.js:574-580 | masterFlatVerifyFilter | `master_flat_run._verify_filter` | ported |  |
| MasterFlat.js:583-589 | masterFlatVerifyImagetyp | `master_flat_run._verify_imagetyp` | ported |  |
| MasterFlat.js:592-597 | masterFlatVerifyMean | `master_flat_run._verify_mean` | ported |  |
| MasterFlat.js:599-609 | MasterFlat.verifyMaster | `master_flat_run.verify_master` | ported |  |
| MasterFlat.js:612-616 | MasterFlat.cleanPath | `master_flat_run.clean_path` | ported |  |
| MasterFlat.js:619-623 | masterFlatPathInside | `master_flat_run._path_inside` | ported |  |
| MasterFlat.js:626-636 | masterFlatValidatePathStructure | `master_flat_run._validate_path_structure` | ported |  |
| MasterFlat.js:639-646 | masterFlatIsExcludedPath | `master_flat_run._is_excluded_path` | ported |  |
| MasterFlat.js:652-662 | masterFlatFindAvailableName | `master_flat_run._find_available_name` | ported |  |
| MasterFlat.js:668-684 | masterFlatDeleteRawFlats | `master_flat_run._delete_raw_flats` | ported |  |
| MasterFlat.js:690-700 | masterFlatCheckJobSkip | `master_flat_run._check_job_skip` | ported |  |
| MasterFlat.js:706-726 | masterFlatProcessParts | `master_flat_run._process_parts` | ported |  |
| MasterFlat.js:732-750 | masterFlatVerifyAndPublish | `master_flat_run._verify_and_publish` | ported |  |
| MasterFlat.js:757-767 | masterFlatRecordJobResult | `master_flat_run._record_job_result` | ported |  |
| MasterFlat.js:773-781 | masterFlatFailureOf | `master_flat_run._failure_of` | ported |  |
| MasterFlat.js:787-817 | masterFlatExecuteOneJob | `master_flat_run._execute_one_job` | ported |  |
| MasterFlat.js:823-840 | masterFlatProcessAllJobs | `master_flat_run._process_all_jobs` | ported |  |
| MasterFlat.js:847-859 | masterFlatProcessJobParts | `master_flat_run._process_job_parts` | ported |  |
| MasterFlat.js:862-877 | masterFlatVerifyResolvedPaths | `master_flat_run._verify_resolved_paths` | ported |  |
| MasterFlat.js:891-907 | MasterFlat.mayDelete | `master_flat_run.may_delete` | ported |  |
| MasterFlat.js:937-957 | MasterFlat.execute | `master_flat_run.execute` | ported |  |
| MasterFlat.js:960-976 | MasterFlat.report | `master_flat_run.report` | ported |  |
| MasterFlat.js:982-982 | masterFlatNum | `master_flat.master_flat_num` | ported |  |
| MasterFlat.js:985-991 | MasterFlat.frameLine | `master_flat.frame_line` | ported |  |
| MasterFlat.js:994-1015 | MasterFlat.darkSummaryLines | `master_flat.dark_summary_lines` | ported |  |
| MasterFlat.js:1017-1037 | MasterFlat.planLines | `master_flat.plan_lines` | ported |  |
| MasterFlat.js:1050-1050 | MasterFlat.SCAN_DEPTH | `master_flat.SCAN_DEPTH` | ported |  |
| MasterFlat.js:1052-1070 | masterFlatScanFolderEntry | `master_flat.master_flat_scan_folder_entry` | ported |  |
| MasterFlat.js:1072-1082 | MasterFlat.scanFolder | `master_flat.scan_folder` | ported |  |
| MasterFlat.js:1088-1398 | MasterFlat.engine | `master_flat_siril.SirilMasterFlatEngine` | replaced | built on Siril calibrate and stack (deviation 9) |

### lib/NightDialog.js

| JS lines | JS name | Python | Status | Note |
|---|---|---|---|---|
| NightDialog.js:21-21 | NightDialog | - | PixInsight only | namespace object |
| NightDialog.js:24-27 | NightDialog.clockOf | `night_dialog.clock_of` | ported |  |
| NightDialog.js:33-41 | NightDialog.rowFor | `night_dialog.row_for` | ported |  |
| NightDialog.js:48-64 | NightDialog.flatSummary | `night_dialog.flat_summary` | ported |  |
| NightDialog.js:71-99 | NightDialog.surveyOf | `night_dialog.survey_of` | ported |  |
| NightDialog.js:106-106 | NightDialog.RECENT | `night_dialog.RECENT` | ported |  |
| NightDialog.js:107-113 | NightDialog.recentNights | `night_dialog.recent_nights` | ported |  |
| NightDialog.js:116-119 | NightDialog.flatsForNight | `night_dialog.flats_for_night` | ported |  |
| NightDialog.js:129 | NightDialog.Dialog.constructor (method) | `night_dialog_tk.NightDialog.__init__` | ported |  |
| NightDialog.js:214 | NightDialog.Dialog.fill (method) | `night_dialog_tk.NightDialog._fill` | ported |  |
| NightDialog.js:248 | NightDialog.Dialog.remember (method) | `night_dialog_tk.NightDialog._remember` | ported |  |
| NightDialog.js:262 | NightDialog.Dialog.describe (method) | `night_dialog_tk.describe` | ported |  |
| NightDialog.js:283 | NightDialog.Dialog.release (method) | `night_dialog_tk.NightDialog._release` | ported |  |

## Measurements: what Siril reports instead of SubframeSelector

PixInsight measures with SubframeSelector. The Siril port asks Siril itself, so every number is one Siril wrote or
fitted: its registration of a sequence of the frames (`register -2pass`), its per-image statistics, and its star lists.
Siril's numbers are the ones its Plot tab shows. Where PixInsight has a figure Siril does not, the port uses the closest
Siril figure or drops the metric, as below.

| Frame Selector field | PixInsight (SubframeSelector) | Siril (this port) | Difference |
|---|---|---|---|
| FWHM | FWHM, pixels | registration FWHM, pixels | Siril's star fit, not SubframeSelector's |
| eccentricity | Eccentricity | sqrt(1 - roundness^2), roundness being Siril's minor/major FWHM | exact for an elliptical Gaussian; a synthetic frame with true eccentricity 0.831 measured 0.832 |
| star count | Stars | registration star count | Siril stops at 2000 stars (see deviation 2) |
| PSF SNR | PSF SNR | median star amplitude divided by the background noise, both from Siril | Siril has no PSF SNR; only the ratio to the channel's median is ever used, and this ratio falls with cloud, blur and a brighter sky, as PSF SNR does |
| star flux | PSF flux | median over the stars of 10^(-0.4 x magnitude), Siril's fitted Gaussian flux, divided by the frame's normalisation value | an aggregate of Siril's own fits |
| background | Median | image median | none in meaning |
| noise | Noise | Siril's background noise | same units as the background |
| SNR estimate | SNR (shown for reading only) | none | Siril has no SNR estimate; the SNR column is not shown |
| altitude | Altitude (computed by SubframeSelector) | from the header, see deviation 4 | |
| time | `DATE-OBS` | `DATE-OBS` | none |

## Deviations

Each entry says what differs, why, and what was checked.

1. **Measurements come from Siril.** The numbers in the table above replace SubframeSelector's. The reason is that Siril
   has no SubframeSelector: its star detection and registration are what it offers, and they are what its own Plot tab
   plots. They were checked on synthetic frames with known truth: twelve 400 x 300 frames with 120 Gaussian stars, of
   which one was blurred (FWHM 5.0 against 3.0), one elongated, one thinned to 40 % of the flux with a brighter
   background (cloud), one with only 12 stars. Siril measured the blur, the elongation, the dimmer stars with the
   brighter background and the star loss as the matching metrics move. The scores and clip rules that use these numbers
   are unchanged and are checked against the JavaScript's own results.
2. **The star count is capped near 2000.** Siril accepts at most 2000 stars per image (`-maxstars` takes 100 to 2000).
   A synthetic field of 5000 stars reported about 1900. A frame in a rich field therefore cannot score above that, and
   the DROPPED tag, which compares a frame's star count with its channel's median, sees the cap on both sides.
3. **A frame Siril cannot register is measured on its own, or not at all.** With very few stars (12 in the test) the
   registration returns zeros for that frame. The port then measures it alone for FWHM, roundness and star count; a
   frame with no usable result is "unmeasurable" and is never rejected automatically. Results are matched to files by
   index into the batch, never by position in a listing, and a sequence whose frame count differs from the batch
   abandons the channel with a "measurement failed" message.
4. **Altitude comes from the header.** SubframeSelector computes an altitude; Siril's own airmass is not reachable from
   a script. The port reads `AIRMASS` (altitude is `asin(1 / airmass)`), else `CENTALT`, else `OBJCTALT`, else it
   computes the altitude from `RA`/`DEC` (or `OBJCTRA`/`OBJCTDEC`), `DATE-OBS`, `SITELAT` and `SITELONG`, else there is no
   altitude and the airmass correction of the tags is skipped, as in PixInsight when it has none.
5. **No plausibility bounds on the measurements.** PixInsight discards values outside fixed bounds because
   SubframeSelector can return nonsense. Siril's numbers do not need them, so the bounds are not applied.
6. **The star finder is reset for every measurement.** The port runs `setfindstar reset` so the numbers do not depend
   on what the user set in the session, as PixInsight fixes SubframeSelector's parameters. Siril exposes only the focal
   length and pixel size of the star finder, so only those two are put back afterwards (if Siril cannot report them, no
   reset is sent: the measurement uses the current settings and a warning is logged); the other star-finder settings
   (sigma, radius, roundness, profile, relax, magnitude limits, convergence) stay at their defaults for the rest of the
   session, and the log says so. A measurement also changes Siril's working directory while it runs and sets it back.
   It must not run while another script is using the same Siril.
7. **No "equalize fluxes" normalisation for master flats.** The calibrated flats are stacked with multiplicative
   normalisation, as PixInsight does, but PixInsight also equalises fluxes for its rejection step and Siril has no
   separate switch for that. The master is checked (see deviation 9) and on synthetic flats the master's
   deviation from the true vignetting was a coefficient of variation of 0.0046 to 0.0135 with calibration, against 0.018
   uncalibrated.
8. **Copies and imports are FITS, not XISF.** A FITS frame is copied byte for byte; an XISF frame is read by Siril and
   written as FITS. PixInsight writes XISF through SubframeSelector's output step. An import is therefore verified by
   comparing digests of source and copy, and then still checks geometry and the required header keywords; PixInsight
   reopens the XISF and checks geometry and keywords only. The import summary and the import log say "FITS copies".
9. **Master flats are built by Siril, and named `masterFlat_<filter>.fit`.** Siril's `calibrate` applies the dark and
   `stack` integrates, with rejection chosen by the frame count (winsorized for many, percentile for a handful, none
   for two) and multiplicative normalisation for flats. The result has the extension `.fit`, with `_bin1` or `_bin2`
   when one filter was shot at two binnings and `_2` instead of overwriting an older master. Siril keeps the header of
   the first frame, so the port rewrites it after stacking as PixInsight does: `FILTER`, `IMAGETYP` (Master Flat or
   Master Dark), `XBINNING`, `YBINNING`, `EXPTIME` (only when every flat shares one), `GAIN`, `CCD-TEMP`, and `DATE-OBS`
   removed. This is done on the file, never on the image open in Siril. Siril's file compression is switched off for
   the run and restored. The master is checked (geometry, `FILTER`, `IMAGETYP`, a real image) before any raw flat copy
   is deleted.
10. **Delete in place does not convert the kept frames.** After deleting rejected frames, PixInsight converts the frames
    it kept to XISF. The Siril port leaves kept frames exactly as they are; only rejected frames are removed.
11. **Stricter safety checks than the original.** Beyond PixInsight's refusals (the destination is or contains a source
    folder, the filesystem root, or the home folder), the Siril port resolves both sides to real paths before comparing,
    compares case-insensitively on macOS and Windows, refuses a destination that is a symbolic link or junction, and
    never follows a link. The deletion log is created exclusively (an existing log is never overwritten), flushed to disk
    before the first file is removed, and file names that are not valid text are written back as their original bytes.
12. **More options are remembered.** PixInsight remembers only the darks folder and "Combine flats to masters". The Siril
    port also remembers the source and destination folders, the preset, `k`, the score weights and the criteria (which
    metrics reject, and any typed limits). They live in one JSON file in Siril's configuration folder. Preset and `k` are
    remembered once and applied to every channel that is opened next; PixInsight keeps them per channel for the session
    only. A channel's `k` still counts as edited only when it was typed by hand.
13. **Logs and cache.** Deletion logs go to `~/Siril/Loom-frame-selector/` (PixInsight: `~/PixInsight/Loom-frame-selector/`).
    The measurement and digest tables, previews, work folders and cached master darks live in a `frame-selector` folder inside
    the Loom cache folder of the Siril scripts. Import logs go to the system temporary folder under `Loom-import-logs`, as in PixInsight. The
    measurement table is keyed by version, measurement configuration (which includes Siril's version) and content
    digest, with size and modification time as the quick check, as in PixInsight; its version string is that of the
    Siril port, so a PixInsight cache is never reused.
14. **Siril version check.** The script requires Siril 1.4 with Python scripting and stops with a message otherwise;
    PixInsight checks its core version.
15. **The interface is Tk, not PJSR controls.** The dialog, plot, filmstrip, preview and the night picker are Tk
    widgets themed like the other Loom scripts. The drawing is equivalent, not identical. Digests are computed with
    Python's SHA-1 on a small thread pool, not with an external system tool.
16. **ASIAIR cards are looked for on more systems.** The card is recognised by the same `Plan/Light` and
    `Autorun/Light` folders. PixInsight walks one mount folder; the port also walks the usual removable-media folders
    on Linux and the drive letters on Windows.
17. **The night picker lists every target name.** The JavaScript tracks seen targets in a plain object, so a target
    named like an inherited property (`constructor`, `toString`) would vanish from the list and take its nights with
    it. The port lists them.

## Known issue in the PixInsight original

The refusal that stops the copy step from emptying a source folder (`FrameSelector.js`, the export refusal) compares
path strings only. A destination that is a symbolic link to a source folder, a path such as `out/../frames`, or, on
macOS, a different spelling of the same folder in another case, passes the refusal, and the "empty the output folder
first" step then deletes the original frames. The Siril port resolves paths (deviation 11).
