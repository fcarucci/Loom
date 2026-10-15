"use strict";
/*
 * Golden cases for loomsiril/frames.py: the decision core of pixinsight/script/lib/Frames.js.
 *
 * Each case records the arguments it was called with and what the JS returned, so the Python suite
 * replays the same calls and compares. Arguments are cloned before every call because several Frames
 * functions change what they are given (recompute, judgeRow, recordOutcome, sanitizeOptional).
 *
 * Out of this recorder, by design: anomaly flags and the grouping/files functions (loomsiril.frames_groups /
 * frames_flags / frames_files), plot geometry (not ported here), the digest cache, and the Tk-side prefill and relativeHint, which exist only in the design
 * specs and not in Frames.js. newChannel calls anomalyFlags, so its flags are dropped from the record.
 * buildManifest stamps the clock into `created`; only its entries are recorded.
 *
 * Undefined does not survive JSON. Outputs that are undefined are written as "__undefined__" (parseLimit's
 * invalid answer); object keys whose value is undefined are dropped, as JSON.stringify drops them.
 */
module.exports = function( js )
{
   const F = js.Frames;
   const cases = {};

   function norm( v )
   {
      if ( v === undefined ) return "__undefined__";
      if ( Array.isArray( v ) ) return v.map( norm );
      if ( v && typeof v === "object" )
      {
         const o = {};
         for ( const k of Object.keys( v ) ) if ( v[k] !== undefined ) o[k] = norm( v[k] );
         return o;
      }
      return v;
   }

   /* A pure call: the result is what the function returns. */
   function pure( key, name, args )
   {
      const out = F[name].apply( F, structuredClone( args ) );
      cases[key] = { fn: name, args: norm( args ), out: norm( out ) };
   }

   /* A call that changes its arguments: the result is the return value, or else the first argument. */
   function mut( key, name, args )
   {
      const work = structuredClone( args );
      const ret = F[name].apply( F, work );
      cases[key] = { fn: name, args: norm( args ), out: norm( ret === undefined ? work[0] : ret ) };
   }

   /* A constant of Frames, by name. */
   function value( name )
   {
      cases["const/" + name] = { value: name, out: norm( F[name] ) };
   }

   /* ---- the twenty-frame fixture: real spread, two genuine rejects (3: blur, 11: cloud) ---------- */
   const PSF = [ 38.2, 41.5, 39.9, 40.4, 37.8, 42.1, 40.0, 39.1, 41.0, 38.7,
                 40.6, 36.9, 41.8, 39.5, 40.2, 38.9, 41.3, 39.0, 40.8, 37.5 ];
   const FWHM = [ 3.41, 3.52, 3.38, 9.81, 3.47, 3.55, 3.44, 3.60, 3.39, 3.50,
                  3.46, 3.58, 3.42, 3.53, 3.36, 3.49, 3.57, 3.43, 3.51, 3.45 ];
   const ECC = [ 0.42, 0.45, 0.41, 0.44, 0.43, 0.46, 0.40, 0.44, 0.42, 0.45,
                 0.43, 0.41, 0.44, 0.42, 0.46, 0.43, 0.41, 0.45, 0.44, 0.42 ];
   const STARS = [ 1210, 1185, 1240, 1195, 1220, 1180, 1232, 1205, 1218, 1191,
                   1226, 150, 1187, 1214, 1236, 1199, 1222, 1208, 1193, 1229 ];
   const NOISE = [ 0.0012, 0.0013, 0.0011, 0.0012, 0.0014, 0.0012, 0.0011, 0.0013, 0.0012, 0.0013,
                   0.0012, 0.0012, 0.0011, 0.0013, 0.0012, 0.0014, 0.0012, 0.0011, 0.0013, 0.0012 ];
   const BG = [ 212, 215, 209, 230, 211, 214, 213, 216, 210, 212,
                214, 240, 211, 213, 215, 210, 212, 214, 213, 211 ];
   const ALT = [ 62, 58, 55, 51, 47, 44, 41, 38, 35, 33,
                 31, 30, 33, 37, 41, 45, 49, 53, 57, 61 ];
   const FRAMES = PSF.map( ( psfSNR, i ) => ( {
      path: "/subs/Ha/L_" + String( i + 1 ).padStart( 3, "0" ) + ".xisf",
      fwhm: FWHM[i], eccentricity: ECC[i], noise: NOISE[i], stars: STARS[i], psfSNR: psfSNR,
      background: BG[i], snrWeight: Math.round( psfSNR * 7.5 ) / 10, altitude: ALT[i], psfFlux: 5000 + 10 * i } ) );
   const PATHS = FRAMES.map( f => f.path );
   const ENTRIES = PATHS.map( ( p, i ) => ( { path: p, time: "2026-09-18T" + String( 20 + Math.floor( i / 6 ) ).padStart( 2, "0" ) + ":" + String( ( i * 7 ) % 60 ).padStart( 2, "0" ) + ":00",
                                             identity: { digest: "sha256:" + ( i + 1 ).toString( 16 ).padStart( 64, "0" ), size: 30000000 + i, mtime: 1700000000000 + i } } ) );
   const BY_PATH = {};
   FRAMES.forEach( f => { BY_PATH[f.path] = f; } );
   const with_ = ( base, patch ) => Object.assign( {}, base, patch );

   /* A channel built the way the dialog builds one; the flags come from loomsiril.frames_flags and are not recorded. */
   function channel( key, entries, metrics )
   {
      const ch = F.newChannel( key, entries, metrics, [] );
      delete ch.flags;
      return ch;
   }
   function channelOf( key, n )
   {
      return channel( key, ENTRIES.slice( 0, n ), BY_PATH );
   }

   /* ---- constants ------------------------------------------------------------------------------- */
   [ "MEASURE_VERSION", "COL", "OPTIONAL", "STORED_KEYS", "PLAUSIBLE", "STATE", "MIN_FRAMES",
     "SIGMA_FLOOR_FRACTION", "WORSE_WHEN", "METRICS", "DEFAULT_WEIGHTS", "DEFAULT_GATING", "CLAMP",
     "PRESETS", "PRESET_NAMES", "DEFAULT_PRESET", "METRIC_LABEL", "METRIC_HEADING", "DISPLAY_METRICS",
     "FRAME_COLUMNS", "OPERATOR", "LIMIT_DECIMALS", "CRITERIA_ORDER", "OVERRIDE", "PHASE" ].forEach( value );

   /* ---- metric dict handling -------------------------------------------------------------------- */
   const ROW = new Array( 29 ).fill( 0 );
   ROW[3] = "/subs/L1.xisf"; ROW[5] = 3.41; ROW[6] = 0.42; ROW[9] = 30.5; ROW[10] = 212;
   ROW[12] = 0.0012; ROW[14] = 1210; ROW[20] = 61.5; ROW[21] = 5400; ROW[28] = 39.9;
   pure( "metrics_from_row/full", "metricsFromRow", [ ROW ] );

   [ 5, 0, 1.5, -1, NaN, Infinity, "3", null ].forEach( ( v, i ) => pure( "optional_ok/" + i, "optionalOk", [ v ] ) );

   pure( "optional_problems/all_ok", "optionalProblems", [ { background: 1, snrWeight: 2, altitude: 3, psfFlux: 4 } ] );
   pure( "optional_problems/two_bad", "optionalProblems", [ { background: null, snrWeight: 2, altitude: NaN, psfFlux: 4 } ] );

   mut( "sanitize_optional/row_bad", "sanitizeOptional", [ { background: NaN, snrWeight: 2, altitude: -1, psfFlux: 4 }, null ] );
   mut( "sanitize_optional/schema_disabled", "sanitizeOptional", [ { background: 1, snrWeight: 2, altitude: 3, psfFlux: 4 }, { psfFlux: true } ] );

   pure( "stored_metrics/full", "storedMetrics", [ FRAMES[0] ] );
   pure( "stored_metrics/missing_keys", "storedMetrics", [ { fwhm: 3.2, psfSNR: 40 } ] );
   pure( "stored_metrics/path_dropped", "storedMetrics", [ with_( FRAMES[1], { path: "/elsewhere/L_002.xisf" } ) ] );

   const STORED = F.storedMetrics( FRAMES[0] );
   pure( "cache_entry_usable/full", "cacheEntryUsable", [ STORED ] );
   pure( "cache_entry_usable/null_optional_is_cached", "cacheEntryUsable", [ with_( STORED, { altitude: null, background: null } ) ] );
   const MISSING_KEY = with_( STORED, {} );
   delete MISSING_KEY.altitude;  // an own property set to undefined would still count as present
   pure( "cache_entry_usable/missing_key", "cacheEntryUsable", [ MISSING_KEY ] );
   pure( "cache_entry_usable/null", "cacheEntryUsable", [ null ] );
   pure( "cache_entry_usable/string", "cacheEntryUsable", [ "x" ] );

   [ [ "fwhm", 3.4 ], [ "fwhm", 0.5 ], [ "eccentricity", 0.42 ], [ "eccentricity", 1 ], [ "eccentricity", 0 ],
     [ "psfSNR", 8.8 ], [ "psfSNR", 35526.7 ], [ "stars", 7507 ], [ "stars", NaN ], [ "bogus", 1 ] ]
      .forEach( ( a, i ) => pure( "metric_in_range/" + i, "metricInRange", a ) );
   [ 0, 7, 7.5, -1, NaN, Infinity ].forEach( ( v, i ) => pure( "is_whole_count/" + i, "isWholeCount", [ v ] ) );
   [ 8.8, 7, NaN, Infinity, 0.5, 35526.7 ].forEach( ( v, i ) => pure( "looks_like_count/" + i, "looksLikeCount", [ v ] ) );

   pure( "meaning_problems/sane", "meaningProblems", [ FRAMES[0] ] );
   pure( "meaning_problems/path_empty", "meaningProblems", [ with_( FRAMES[0], { path: "" } ) ] );
   pure( "meaning_problems/ecc_one", "meaningProblems", [ with_( FRAMES[0], { eccentricity: 1 } ) ] );
   pure( "meaning_problems/stars_fraction", "meaningProblems", [ with_( FRAMES[0], { stars: 1210.5 } ) ] );
   pure( "meaning_problems/psfsnr_is_count", "meaningProblems", [ with_( FRAMES[0], { psfSNR: 1210 } ) ] );
   pure( "meaning_problems/fwhm_far", "meaningProblems", [ with_( FRAMES[0], { fwhm: 100 } ) ] );

   [ 1, 0, -1, NaN, Infinity, "3", null, 1e-300 ].forEach( ( v, i ) => pure( "metric_valid/" + i, "metricValid", [ v ] ) );
   pure( "frame_valid/sane", "frameValid", [ FRAMES[0] ] );
   pure( "frame_valid/ecc_zero", "frameValid", [ with_( FRAMES[0], { eccentricity: 0 } ) ] );
   pure( "frame_valid/null", "frameValid", [ null ] );
   pure( "frame_valid/missing_stars", "frameValid", [ { fwhm: 3, eccentricity: 0.4, psfSNR: 40 } ] );

   /* ---- statistics ------------------------------------------------------------------------------ */
   const FWHM_COL = FRAMES.map( f => f.fwhm );
   pure( "median/empty", "median", [ [] ] );
   pure( "median/null", "median", [ null ] );
   pure( "median/one", "median", [ [ 5 ] ] );
   pure( "median/even", "median", [ [ 1, 2 ] ] );
   pure( "median/odd_unsorted", "median", [ [ 3, 1, 2 ] ] );
   pure( "median/fixture_fwhm", "median", [ FWHM_COL ] );
   pure( "median/flat_near_four", "median", [ [ 4, 4, 4, 4, 4.000001 ] ] );
   pure( "sigma/fixture_fwhm", "sigma", [ FWHM_COL ] );
   pure( "sigma/degenerate_mad_zero", "sigma", [ [ 4, 4, 4, 4, 4.000001 ] ] );
   pure( "sigma/all_equal", "sigma", [ [ 5, 5, 5 ] ] );
   pure( "sigma/symmetric", "sigma", [ [ 1, 2, 3, 4, 100 ] ] );
   pure( "sigma/empty", "sigma", [ [] ] );

   pure( "gate/fixture_fwhm_k25", "gate", [ FWHM_COL, "fwhm", 2.5 ] );
   pure( "gate/fixture_fwhm_k3", "gate", [ FWHM_COL, "fwhm", 3.0 ] );
   pure( "gate/fixture_fwhm_k2", "gate", [ FWHM_COL, "fwhm", 2.0 ] );
   pure( "gate/fixture_psfsnr_lower_k25", "gate", [ F.columnOf( FRAMES, "psfSNR" ), "psfSNR", 2.5 ] );
   pure( "gate/fixture_ecc_k25", "gate", [ F.columnOf( FRAMES, "eccentricity" ), "eccentricity", 2.5 ] );
   pure( "gate/mad_zero_degenerate", "gate", [ [ 4, 4, 4, 4, 4.000001 ], "fwhm", 2.5 ] );
   pure( "gate/all_same_ecc", "gate", [ [ 0.42, 0.42, 0.42, 0.42, 0.42 ], "eccentricity", 2.5 ] );
   pure( "gate/floor_below_one_percent", "gate", [ [ 100, 100, 100, 100, 100.5, 99.5, 100.5, 100.5, 100.5, 99.5 ], "fwhm", 2.5 ] );
   pure( "gate/floor_above_one_percent", "gate", [ [ 100, 100, 100, 100, 101.5, 98.5, 101.5, 101.5, 101.5, 98.5 ], "fwhm", 2.5 ] );
   pure( "gate/lower_bound_not_positive", "gate", [ [ 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 1000, 2000 ], "stars", 2.5 ] );
   pure( "gate/nan_values", "gate", [ [ NaN, NaN ], "fwhm", 2.5 ] );
   pure( "gate/empty", "gate", [ [], "fwhm", 2.5 ] );

   /* ---- validity, frame selection, gates over a channel ------------------------------------------ */
   const BAD = with_( FRAMES[2], { eccentricity: 0 } );
   pure( "valid_only/one_invalid", "validOnly", [ FRAMES.slice( 0, 3 ).concat( [ BAD ], FRAMES.slice( 4, 6 ) ) ] );
   pure( "column_of/fwhm", "columnOf", [ FRAMES.slice( 0, 4 ), "fwhm" ] );

   pure( "relative_gates/fixture_k25", "relativeGates", [ FRAMES, 2.5, null ] );
   pure( "relative_gates/fixture_k25_default_gating", "relativeGates", [ FRAMES, 2.5, F.DEFAULT_GATING ] );
   pure( "relative_gates/fixture_k3_all_gating", "relativeGates", [ FRAMES, 3.0, { psfSNR: true, fwhm: true, eccentricity: true, stars: true } ] );
   pure( "relative_gates/fixture_k2_psfsnr_gated", "relativeGates", [ FRAMES, 2.0, { psfSNR: true, fwhm: true, eccentricity: true, stars: true } ] );
   pure( "relative_gates/nine_valid_no_gates", "relativeGates", [ FRAMES.slice( 0, 9 ), 2.5, null ] );
   pure( "relative_gates/ten_valid_gates", "relativeGates", [ FRAMES.slice( 0, 10 ), 2.5, null ] );
   pure( "relative_gates/ten_with_one_invalid_no_gates", "relativeGates", [ FRAMES.slice( 0, 9 ).concat( [ BAD ] ), 2.5, null ] );
   pure( "relative_gates/too_few_empty", "relativeGates", [ [], 2.5, null ] );

   pure( "medians/fixture", "medians", [ FRAMES ] );
   pure( "medians/one_invalid", "medians", [ FRAMES.slice( 0, 3 ).concat( [ BAD ] ) ] );

   /* ---- score -------------------------------------------------------------------------------- */
   const MEDS = F.medians( FRAMES );
   FRAMES.forEach( ( f, i ) => pure( "score/fixture_" + i, "score", [ f, MEDS, null ] ) );
   pure( "score/custom_weights", "score", [ FRAMES[0], MEDS, { psfSNR: 1, fwhm: 0, eccentricity: 0, stars: 0 } ] );
   pure( "score/zero_weights", "score", [ FRAMES[0], MEDS, {} ] );
   pure( "score/clamp_low_psfsnr", "score", [ with_( FRAMES[0], { psfSNR: 1e-6 } ), MEDS, null ] );
   pure( "score/clamp_high_ecc", "score", [ with_( FRAMES[0], { eccentricity: 1e-4 } ), MEDS, null ] );
   pure( "score/clamp_high_stars", "score", [ with_( FRAMES[0], { stars: 1e9 } ), MEDS, null ] );
   pure( "score/unmeasurable_is_null", "score", [ BAD, MEDS, null ] );
   pure( "score/median_invalid_is_null", "score", [ FRAMES[0], with_( MEDS, { psfSNR: NaN } ), null ] );

   /* ---- settings, presets, bands ----------------------------------------------------------- */
   pure( "default_settings", "defaultSettings", [] );
   pure( "copy_of/shallow", "copyOf", [ { a: 1, b: { c: 2 } } ] );
   pure( "copy_of/null", "copyOf", [ null ] );

   const DS = F.defaultSettings();
   pure( "apply_preset/strict_unedited", "applyPreset", [ DS, "strict" ] );
   pure( "apply_preset/lenient_unedited", "applyPreset", [ DS, "lenient" ] );
   pure( "apply_preset/k_edited_kept", "applyPreset", [ with_( DS, { kEdited: true, k: 2.7 } ), "strict" ] );
   pure( "apply_preset/unknown_preset_keeps_k", "applyPreset", [ DS, "noSuchPreset" ] );
   pure( "apply_preset/null_weights", "applyPreset", [ with_( DS, { weights: null } ), "strict" ] );
   pure( "apply_preset/limits_copied", "applyPreset", [ with_( DS, { limits: { fwhm: { hi: 3.7 } } } ), "lenient" ] );

   const GATES25 = F.relativeGates( FRAMES, 2.5, F.DEFAULT_GATING );
   [ "psfSNR", "fwhm", "eccentricity", "stars" ].forEach( m =>
      pure( "accepted_band/relative_" + m, "acceptedBand", [ m, GATES25, DS ] ) );
   pure( "accepted_band/unticked", "acceptedBand", [ "fwhm", GATES25, with_( DS, { gating: with_( DS.gating, { fwhm: false } ) } ) ] );
   pure( "accepted_band/typed_replaces_relative", "acceptedBand", [ "fwhm", GATES25, with_( DS, { limits: { fwhm: { hi: 3.8 } } } ) ] );
   pure( "accepted_band/typed_lo_stars", "acceptedBand", [ "stars", GATES25, with_( DS, { limits: { stars: { lo: 1200 } } } ) ] );
   pure( "accepted_band/no_gates", "acceptedBand", [ "fwhm", {}, DS ] );

   pure( "relative_band/fwhm", "relativeBand", [ "fwhm", GATES25 ] );
   pure( "relative_band/stars", "relativeBand", [ "stars", GATES25 ] );
   pure( "relative_band/psfsnr_unactive", "relativeBand", [ "psfSNR", GATES25 ] );
   pure( "relative_band/null_gates", "relativeBand", [ "fwhm", null ] );

   pure( "narrow_band/tighter_lo", "narrowBand", [ { lo: 1, hi: 5 }, { lo: 2 } ] );
   pure( "narrow_band/looser_lo_kept", "narrowBand", [ { lo: 1, hi: 5 }, { lo: 0.5 } ] );
   pure( "narrow_band/hi", "narrowBand", [ { lo: 1, hi: 5 }, { hi: 4 } ] );
   pure( "narrow_band/both_wider_ignored", "narrowBand", [ { lo: 1, hi: 5 }, { lo: 0.5, hi: 9 } ] );
   pure( "narrow_band/open_gets_limit", "narrowBand", [ { lo: null, hi: null }, { lo: 2, hi: 3 } ] );
   pure( "narrow_band/null_limit", "narrowBand", [ { lo: 1, hi: 5 }, null ] );

   /* ---- failures, verdicts ---------------------------------------------------------------- */
   const LIMIT_FWHM = GATES25.fwhm.limit;
   const LIMIT_STARS = GATES25.stars.limit;
   pure( "relative_failures/rejects_3", "relativeFailures", [ FRAMES[3], GATES25 ] );
   pure( "relative_failures/rejects_11", "relativeFailures", [ FRAMES[11], GATES25 ] );
   pure( "relative_failures/kept_0", "relativeFailures", [ FRAMES[0], GATES25 ] );
   pure( "relative_failures/fwhm_exactly_at_limit", "relativeFailures", [ with_( FRAMES[7], { fwhm: LIMIT_FWHM } ), GATES25 ] );
   pure( "relative_failures/stars_exactly_at_limit", "relativeFailures", [ with_( FRAMES[7], { stars: LIMIT_STARS } ), GATES25 ] );
   pure( "relative_failures/no_active_gates", "relativeFailures", [ FRAMES[3], {} ] );

   const LIM_FWHM = { fwhm: { hi: 3.5 }, stars: { lo: 1200 } };
   pure( "absolute_failures/fwhm_above", "absoluteFailures", [ FRAMES[7], LIM_FWHM, null ] );
   pure( "absolute_failures/kept", "absoluteFailures", [ FRAMES[0], LIM_FWHM, null ] );
   pure( "absolute_failures/stars_below", "absoluteFailures", [ FRAMES[11], LIM_FWHM, null ] );
   pure( "absolute_failures/unticked_skipped", "absoluteFailures", [ FRAMES[7], LIM_FWHM, { fwhm: false, stars: true } ] );
   pure( "absolute_failures/both_ends", "absoluteFailures", [ FRAMES[7], { fwhm: { lo: 3.5, hi: 3.55 } }, null ] );
   pure( "absolute_failures/below_lo", "absoluteFailures", [ FRAMES[0], { fwhm: { lo: 3.5, hi: 9 } }, null ] );
   pure( "absolute_failures/no_limits", "absoluteFailures", [ FRAMES[7], {}, null ] );

   pure( "auto_gates/typed_replaces", "autoGates", [ GATES25, LIM_FWHM ] );
   pure( "auto_gates/no_limits", "autoGates", [ GATES25, {} ] );
   pure( "auto_gates/null_gates", "autoGates", [ null, LIM_FWHM ] );

   pure( "verdict/kept_0", "verdict", [ FRAMES[0], GATES25, DS ] );
   pure( "verdict/rejects_3", "verdict", [ FRAMES[3], GATES25, DS ] );
   pure( "verdict/rejects_11", "verdict", [ FRAMES[11], GATES25, DS ] );
   pure( "verdict/fwhm_exactly_at_limit_kept", "verdict", [ with_( FRAMES[7], { fwhm: LIMIT_FWHM } ), GATES25, DS ] );
   pure( "verdict/stars_exactly_at_limit_kept", "verdict", [ with_( FRAMES[7], { stars: LIMIT_STARS } ), GATES25, DS ] );
   pure( "verdict/stars_just_below_limit_rejected", "verdict", [ with_( FRAMES[7], { stars: LIMIT_STARS - 1e-9 } ), GATES25, DS ] );
   pure( "verdict/unmeasurable_ecc_zero", "verdict", [ BAD, GATES25, DS ] );
   pure( "verdict/typed_fwhm_limit", "verdict", [ FRAMES[7], GATES25, with_( DS, { limits: LIM_FWHM } ) ] );
   pure( "verdict/typed_fwhm_limit_kept", "verdict", [ FRAMES[0], GATES25, with_( DS, { limits: LIM_FWHM } ) ] );
   pure( "verdict/unticked_fwhm_with_limit_kept", "verdict", [ FRAMES[3], GATES25,
         with_( DS, { limits: LIM_FWHM, gating: with_( DS.gating, { fwhm: false } ) } ) ] );
   pure( "verdict/absolute_only_no_gates", "verdict", [ FRAMES[11], {}, with_( DS, { limits: { stars: { lo: 1200 } } } ) ] );
   pure( "verdict/no_gates_no_limits", "verdict", [ FRAMES[3], {}, DS ] );
   pure( "verdict/two_metrics_fail", "verdict", [ with_( FRAMES[3], { stars: 150 } ), GATES25, DS ] );

   /* ---- criteria panel arithmetic --------------------------------------------------------- */
   [ "3", "2.5", " 3 ", ".5", "5.", "1e3", "1E-3", "-1", "0", "0.0", "", "   ", null, "2,5", "abc", "1e400", "NaN", "Infinity", "12abc", "7.25" ]
      .forEach( ( t, i ) => pure( "parse_limit/" + i, "parseLimit", [ t ] ) );
   pure( "parse_limit/number", "parseLimit", [ 7.25 ] );

   [ [ "fwhm", 7.9412 ], [ "eccentricity", 0.5 ], [ "stars", 1234.6 ], [ "psfSNR", 12.345 ], [ "fwhm", null ],
     [ "fwhm", NaN ], [ "unknown", 1.234 ], [ "fwhm", 0.125 ], [ "fwhm", 2.675 ], [ "fwhm", -0.001 ],
     [ "stars", 2.5 ], [ "eccentricity", 0.0005 ] ].forEach( ( a, i ) => pure( "format_limit/" + i, "formatLimit", a ) );
   pure( "roundtrip/format_parsed_fwhm", "formatLimit", [ "fwhm", F.parseLimit( "3.70" ) ] );
   pure( "roundtrip/format_parsed_ecc", "formatLimit", [ "eccentricity", F.parseLimit( "0.420" ) ] );

   pure( "limit_value/fwhm_hi", "limitValue", [ { limits: { fwhm: { hi: 3.5 } } }, "fwhm" ] );
   pure( "limit_value/stars_lo", "limitValue", [ { limits: { stars: { lo: 1200 } } }, "stars" ] );
   pure( "limit_value/none", "limitValue", [ DS, "psfSNR" ] );
   pure( "limit_value/limits_null", "limitValue", [ { limits: null }, "fwhm" ] );
   pure( "limit_value/wrong_side_null", "limitValue", [ { limits: { fwhm: { lo: 3 } } }, "fwhm" ] );

   pure( "with_limit/fwhm_set", "withLimit", [ DS, "fwhm", 3.5 ] );
   pure( "with_limit/stars_set", "withLimit", [ DS, "stars", 1200 ] );
   pure( "with_limit/cleared", "withLimit", [ { limits: { fwhm: { hi: 3.5 } } }, "fwhm", null ] );
   pure( "with_limit/replaces_other_side", "withLimit", [ { limits: { fwhm: { lo: 3 } } }, "fwhm", 3.5 ] );

   pure( "display_limit/typed", "displayLimit", [ "fwhm", GATES25, { limits: { fwhm: { hi: 3.5 } } } ] );
   pure( "display_limit/auto", "displayLimit", [ "fwhm", GATES25, DS ] );
   pure( "display_limit/auto_inactive", "displayLimit", [ "psfSNR", GATES25, DS ] );
   pure( "display_limit/no_gates", "displayLimit", [ "fwhm", null, DS ] );

   [ 0, 0.5, 1.005, 1.5, 2.675, 9.999, 99.99, 100, 123.456, -150.5, 0.000123456, 0.0000001234, -0.25, 1e-7, 0.9999, NaN, Infinity, -Infinity,
        1e21, -1e21, 123456789012345678901234, -0, -0.4 ]
      .forEach( ( v, i ) => pure( "round/" + i, "round", [ v ] ) );

   /* ---- outcomes, counts, phases -------------------------------------------------------------- */
   const STATES = [ "approved", "rejected", "unmeasurable" ];
   const OVERRIDES = [ null, "rescued", "condemned" ];
   STATES.forEach( s => OVERRIDES.forEach( o =>
      pure( "final_state/" + s + "/" + ( o || "none" ), "finalState", [ s, o ] ) ) );

   STATES.forEach( s => OVERRIDES.forEach( o => [ true, false ].forEach( en => [ true, false ].forEach( cp =>
      pure( "left_out/" + s + "/" + ( o || "none" ) + "/" + ( en ? "on" : "off" ) + "/" + ( cp ? "copy" : "cull" ),
            "leftOut", [ { state: s, override: o }, en, cp ] ) ) ) ) );

   /* A review: 20 frames with the two genuine rejects, one rescued and one condemned by hand. */
   const REVIEW = channelOf( "L", 20 );
   F.recompute( REVIEW );
   REVIEW.rows[3].override = "rescued";
   REVIEW.rows[0].override = "condemned";
   const ROWS = structuredClone( REVIEW.rows );
   pure( "keep_count/cull_on", "keepCount", [ ROWS, true, false ] );
   pure( "keep_count/copy_on", "keepCount", [ ROWS, true, true ] );
   pure( "keep_count/cull_off", "keepCount", [ ROWS, false, false ] );
   pure( "keep_count/copy_off", "keepCount", [ ROWS, false, true ] );
   pure( "counts/review", "counts", [ ROWS ] );
   pure( "counts/empty", "counts", [ [] ] );
   pure( "summary_line/review", "summaryLine", [ "H", F.counts( ROWS ) ] );
   pure( "summary_line/plain", "summaryLine", [ "L", F.counts( [ { state: "approved", override: null }, { state: "rejected", override: null } ] ) ] );
   pure( "summary_line/unmeasurable", "summaryLine", [ "S", F.counts( [ { state: "unmeasurable", override: null } ] ) ] );

   [ "review", "executing", "stopped", "done", "unknownPhase" ].forEach( p =>
      pure( "can_edit/" + p, "canEdit", [ p ] ) );
   const PHASES = [ "review", "executing", "stopped", "done", "unknownPhase" ];
   const EVENTS = [ "commit", "stop", "finish", "resume", "abandon", "review", "unknownEvent" ];
   PHASES.forEach( p => EVENTS.forEach( e => pure( "next_phase/" + p + "/" + e, "nextPhase", [ p, e ] ) ) );

   /* ---- channel, recompute, verdict -------------------------------------------------------------- */
   const NEW = channelOf( "L", 20 );
   cases["new_channel/basic"] = { fn: "newChannel", args: norm( [ "L", ENTRIES, BY_PATH, [] ] ), out: norm( NEW ) };

   const MISSING = BY_PATH;
   const PARTIAL = Object.assign( {}, BY_PATH );
   delete PARTIAL[PATHS[9]];
   const NEW_PARTIAL = F.newChannel( "L", ENTRIES, PARTIAL, [] );
   delete NEW_PARTIAL.flags;
   cases["new_channel/one_not_measured"] = { fn: "newChannel", args: norm( [ "L", ENTRIES, PARTIAL, [] ] ), out: norm( NEW_PARTIAL ) };

   const NO_ID = ENTRIES.slice( 0, 3 ).map( e => ( { path: e.path, time: null } ) );
   const NEW_NOID = F.newChannel( "H", NO_ID, MISSING, [] );
   delete NEW_NOID.flags;
   cases["new_channel/no_identity"] = { fn: "newChannel", args: norm( [ "H", NO_ID, MISSING, [] ] ), out: norm( NEW_NOID ) };

   const NEW_NOFILTER = F.newChannel( F.NO_FILTER, ENTRIES.slice( 0, 12 ), BY_PATH, [] );
   delete NEW_NOFILTER.flags;
   cases["new_channel/no_filter_key"] = { fn: "newChannel", args: norm( [ F.NO_FILTER, ENTRIES.slice( 0, 12 ), BY_PATH, [] ] ), out: norm( NEW_NOFILTER ) };

   const RECOMP = ( name, mk ) => { const ch = mk(); mut( "recompute/" + name, "recompute", [ ch ] ); };
   RECOMP( "balanced_partial", () => channel( "L", ENTRIES, PARTIAL ) );
   RECOMP( "strict_custom_weights", () => {
      const ch = channelOf( "L", 20 );
      ch.settings = with_( ch.settings, { preset: "strict", k: 2.0, kEdited: true,
                                          weights: { psfSNR: 0.2, fwhm: 0.5, eccentricity: 0.2, stars: 0.1 } } );
      return ch; } );
   RECOMP( "overrides_survive", () => {
      const ch = channelOf( "L", 20 );
      ch.rows[3].override = "rescued";
      ch.rows[0].override = "condemned";
      return ch; } );
   RECOMP( "typed_limit_and_gating", () => {
      const ch = channelOf( "L", 20 );
      ch.settings = with_( ch.settings, { limits: { stars: { lo: 1200 } }, gating: with_( ch.settings.gating, { eccentricity: false } ) } );
      return ch; } );
   RECOMP( "disabled_channel", () => {
      const ch = channelOf( "L", 20 );
      ch.settings = with_( ch.settings, { enabled: false } );
      return ch; } );
   RECOMP( "no_filter_never_rejects", () => channel( F.NO_FILTER, ENTRIES.slice( 0, 20 ), BY_PATH ) );
   RECOMP( "too_few_frames", () => channel( "S", ENTRIES.slice( 0, 8 ), BY_PATH ) );
   RECOMP( "unmeasurable_row", () => channel( "L", ENTRIES.slice( 0, 20 ), with_( BY_PATH, { [PATHS[2]]: BAD } ) ) );

   const JR = ( name, row, gates, settings, mayReject ) =>
      mut( "judge_row/" + name, "judgeRow", [ row, gates, F.medians( FRAMES ), settings, mayReject ] );
   const freshRow = m => ( { path: m ? m.path : "/none", channel: "L", metrics: m, time: null, digest: null, size: 0, mtime: 0,
                             state: "unmeasurable", reasons: [], override: null, score: null } );
   JR( "rejected_fwhm", freshRow( FRAMES[3] ), GATES25, DS, true );
   JR( "rejected_suppressed", freshRow( FRAMES[3] ), GATES25, DS, false );
   JR( "kept", freshRow( FRAMES[0] ), GATES25, DS, true );
   JR( "unmeasurable_no_measurement", freshRow( null ), GATES25, DS, true );

   /* ---- manifest lifecycle --------------------------------------------------------------- */
   const MAN = channelOf( "L", 20 );
   F.recompute( MAN );
   MAN.rows[3].override = "rescued";
   MAN.rows[0].override = "condemned";
   /* buildManifest stamps Date.now() into `created`: the clock is pinned while it runs, so the record is the same on
      every run. */
   function withClock( t, fn )
   {
      const now = Date.now;
      Date.now = () => t;
      try { return fn(); } finally { Date.now = now; }
   }
   const MANIFEST = withClock( 1700000000000, () => F.buildManifest( MAN.rows ) );
   cases["build_manifest/entries"] = { fn: "buildManifest", args: norm( [ MAN.rows ] ), out: norm( { entries: MANIFEST.entries } ) };
   mut( "manifest/record_first", "recordOutcome", [ MANIFEST, PATHS[0], "deleted", "" ] );
   const AFTER_ONE = structuredClone( MANIFEST );
   F.recordOutcome( AFTER_ONE, PATHS[0], "deleted", "" );
   pure( "manifest/pending_after_one", "manifestPending", [ AFTER_ONE ] );

   const ID = { digest: "abc", size: 10, mtime: 5 };
   pure( "identity_matches/same", "identityMatches", [ { digest: "abc", size: 10 }, ID ] );
   pure( "identity_matches/digest_differs", "identityMatches", [ { digest: "abd", size: 10 }, ID ] );
   pure( "identity_matches/size_differs", "identityMatches", [ { digest: "abc", size: 11 }, ID ] );
   pure( "identity_matches/no_current", "identityMatches", [ { digest: "abc", size: 10 }, null ] );
   pure( "identity_matches/no_entry", "identityMatches", [ null, ID ] );

   return cases;
};
