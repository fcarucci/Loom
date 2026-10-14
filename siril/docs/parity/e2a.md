# E2a parity: colour management (Fly.js 1334-1623, Sky.colourOf, Render.srgbIcc)

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
