# E3b parity: star-model primitives

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
