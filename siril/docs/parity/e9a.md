# E9a parity: the HDR frame path and E5's test gaps

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
