"use strict";
/*
 * Golden cases for loomsiril/frames_groups.py, frames_flags.py and frames_files.py: the grouping,
 * anomaly-flag and file-list functions of pixinsight/script/lib/Frames.js (the T1b half of the port).
 *
 * Each case records the arguments it was called with and what the JS returned, so the Python suite
 * replays the same calls and compares. Arguments are cloned before every call because some Frames
 * functions change what they are given (digestCacheRecord, mergeBatch).
 *
 * File.extractDirectory / extractName / extractExtension come from the node shim (pjsr-shim.js), which
 * is POSIX path semantics. The Python port is string-only and follows the same rules.
 *
 * Undefined does not survive JSON. Outputs that are undefined are written as "__undefined__"; object
 * keys whose value is undefined are dropped, as JSON.stringify drops them.
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

   /* A call that changes its first argument: the result is the first argument afterwards. */
   function mutated( key, name, args )
   {
      const work = structuredClone( args );
      F[name].apply( F, work );
      cases[key] = { fn: name, args: norm( args ), out: norm( work[0] ) };
   }

   /* mergeBatch both returns a flag and changes its `measured` argument: both are recorded. */
   function mergeCase( key, batch, got, measured )
   {
      const work = structuredClone( [ batch, got, measured ] );
      const ret = F.mergeBatch.apply( F, work );
      cases[key] = { fn: "mergeBatch", args: norm( [ batch, got, measured ] ),
                     out: { ret: norm( ret ), measured: norm( work[2] ) } };
   }

   /* A constant of Frames, by name. */
   function value( name )
   {
      cases["const/" + name] = { value: name, out: norm( F[name] ) };
   }

   /* ---- grouping: notSubframe, calibrationKind, skipped lines ------------------------------- */
   [
      [ "Light_001.xisf", "Light Frame" ], [ "masterFlat.xisf", "" ], [ "x.xisf", "MASTER DARK" ],
      [ "integration_x", "" ], [ "Flat_01.fit", "" ], [ "Flat_01.fit", "Flat Field" ],
      [ "Dark_120.fit", "" ], [ "Bias_0.fit", "" ], [ "Darkflat_1", "" ], [ "Offset_1", "" ],
      [ "Lights_Ha_1", "" ], [ "LightX.fit", "" ], [ "Light_1", "'LIGHT '" ], [ "Flat_1", "Light Frame" ],
      [ "Light_1", "Dark Frame" ], [ "x", "Zero Frame" ], [ null, null ], [ 0, 0 ], [ "Flat_1", "flat" ],
      [ "Flatten_1", "" ], [ "flat-1", "" ], [ "FLAT 3", "" ], [ "Dark_1", "Light" ],
      [ "Master_Light", "Light" ], [ "x", "Masterpiece" ], [ "Light_Ha", "Darkness" ]
   ].forEach( ( a, i ) => pure( "not_subframe/" + i, "notSubframe", a ) );

   [ [ "flat", false ], [ "flats", false ], [ "Dark", false ], [ "darkflat", false ], [ "LIGHT", true ],
     [ "Bias Frame", true ], [ "offset", false ], [ "", true ], [ "Zero", false ] ]
      .forEach( ( a, i ) => pure( "calibration_kind/" + i, "calibrationKind", a ) );

   const SK = [ { kind: "flat" }, { kind: "master" }, { kind: "flat" }, { kind: "bias" } ];
   pure( "skipped_counts/mixed", "skippedCounts", [ SK ] );
   pure( "skipped_counts/bias_twice", "skippedCounts", [ [ { kind: "bias" }, { kind: "bias" } ] ] );
   pure( "skipped_line/none", "skippedLine", [ [] ] );
   pure( "skipped_line/null", "skippedLine", [ null ] );
   pure( "skipped_line/one", "skippedLine", [ [ { kind: "dark" } ] ] );
   pure( "skipped_line/many", "skippedLine", [ SK ] );
   pure( "unreadable_line/none", "unreadableLine", [ [] ] );
   pure( "unreadable_line/one", "unreadableLine", [ [ "/a/x.fit" ] ] );
   pure( "unreadable_line/two", "unreadableLine", [ [ "/a/x.fit", "/a/y.fit" ] ] );
   pure( "no_subframes/none", "noSubframesMessage", [ [] ] );
   pure( "no_subframes/some", "noSubframesMessage", [ SK ] );

   /* ---- grouping: groupByFilter, autoRejectAllowed, normalImageType, comparability ----------- */
   const GE = [
      { path: "/s/a.xisf", filter: "Ha" }, { path: "/s/b.xisf", filter: " Ha " },
      { path: "/s/c.xisf", filter: "" }, { path: "/s/d.xisf", filter: null },
      { path: "/s/e.xisf" }, { path: "/s/f.xisf", filter: "constructor" },
      { path: "/s/g.xisf", filter: "OIII" }, { path: "/s/h.xisf", filter: "   " }
   ];
   pure( "group_by_filter/mixed", "groupByFilter", [ GE ] );
   pure( "group_by_filter/empty", "groupByFilter", [ [] ] );
   [ "Ha", "(no filter)", "constructor", "" ].forEach( ( k, i ) =>
      pure( "auto_reject_allowed/" + i, "autoRejectAllowed", [ k ] ) );

   [ "Light Frame", "'LIGHT '", "Flat Field", "", null, "Dark  Frame", "Bias Frame", "Light Frame Field",
     "LIGHT\tFRAME", "Masterpiece", "light" ].forEach( ( t, i ) => pure( "normal_image_type/" + i, "normalImageType", [ t ] ) );

   const LIGHT = { exposure: 300, binning: 1, binningY: 1, imageType: "Light Frame", width: 4656, height: 3520, calibrated: true };
   const with_ = ( patch ) => Object.assign( {}, LIGHT, patch );
   const without = ( key ) => { const o = Object.assign( {}, LIGHT ); delete o[key]; return o; };
   pure( "comparability/uniform", "comparability", [ [ LIGHT, with_( { imageType: "light" } ), with_( { imageType: "LIGHT FRAME" } ) ] ] );
   pure( "comparability/exposure_string_same", "comparability", [ [ LIGHT, with_( { exposure: "300" } ) ] ] );
   pure( "comparability/exposure", "comparability", [ [ LIGHT, with_( { exposure: 180 } ) ] ] );
   pure( "comparability/binning", "comparability", [ [ LIGHT, with_( { binning: 2 } ) ] ] );
   pure( "comparability/binning_y_only", "comparability", [ [ LIGHT, with_( { binningY: 2 } ) ] ] );
   pure( "comparability/binning_missing", "comparability", [ [ LIGHT, without( "binning" ) ] ] );
   pure( "comparability/image_type", "comparability", [ [ LIGHT, with_( { imageType: "Flat Field" } ) ] ] );
   pure( "comparability/geometry_width", "comparability", [ [ LIGHT, with_( { width: 2000 } ) ] ] );
   pure( "comparability/geometry_height", "comparability", [ [ LIGHT, with_( { height: 2000 } ) ] ] );
   pure( "comparability/calibration", "comparability", [ [ LIGHT, with_( { calibrated: false } ) ] ] );
   pure( "comparability/calibration_unknown_uniform", "comparability", [ [ without( "calibrated" ), without( "calibrated" ) ] ] );
   pure( "comparability/everything", "comparability", [ [ LIGHT, with_( { exposure: 60, binning: 2, imageType: "Dark", width: 1, height: 1, calibrated: false } ) ] ] );
   pure( "comparability/empty", "comparability", [ [] ] );
   pure( "comparability/null", "comparability", [ null ] );
   pure( "comparability/single", "comparability", [ [ LIGHT ] ] );

   /* ---- flags: constants --------------------------------------------------------------------- */
   [ "FLAG_K", "FLAG_MIN_FRAMES", "DROPPED_FRACTION", "BLUR_MARGIN", "SEEING_AIRMASS_POWER", "EXTINCTION",
     "CLOUD_DIMMING", "CLOUD_BACKGROUND_RISE", "FLAG_ORDER", "FLAG_TAG", "FLAG_BADGE", "FLAG_SUMMARY_ORDER" ]
      .forEach( value );

   /* ---- flags: obsTime, airmass, isNumber, lowerQuartile ------------------------------------- */
   [ null, "", "2026-09-18T20:00:00", "'2026-09-18T20:00:00'", "2026-09-18T20:00:00Z",
     "2026-09-18T22:00:00+02:00", "2026-09-18T15:00:00-0500", "2026-09-18T20:00:00.25",
     "2026-09-18T20:00:00.123456Z", "2026-09-18 20:00:00", "2026-09-18T20:00", "2026-09-18", "garbage",
     /* V8's rules: day 1-31 whatever the month (overflow rolls on), 24:00:00 is the next midnight, */
     /* and minutes, seconds and month or day out of range are NaN */
     "2024-02-30T10:00:00Z", "2023-02-29T10:00:00Z", "2024-04-31T00:00:00Z", "2026-09-18T24:00:00Z",
     "2026-09-18T24:00:00", "2026-09-18T24:00:00.000Z", "2026-09-18T24:00:01Z", "2026-09-18T24:30:00Z",
     "2026-09-18T23:60:00Z", "2026-09-18T20:00:60Z", "2026-09-00T10:00:00Z", "2026-13-01T00:00:00Z",
     "2026-00-10T00:00:00Z", "2026-09-18T20:00:00.5", "2026-09-18T20:00:00+14:00" ]
      .forEach( ( t, i ) => pure( "obs_time/" + i, "obsTime", [ t ] ) );

   [ 90, 30, 45, 1, 0.5, 91, null, "45", 25 ].forEach( ( a, i ) => pure( "airmass/" + i, "airmass", [ a ] ) );
   [ 1, "1", null, 0, -2.5, true ].forEach( ( v, i ) => pure( "is_number/" + i, "isNumber", [ v ] ) );

   pure( "lower_quartile/eight", "lowerQuartile", [ [ 5, 1, 3, 2, 4, 6, 7, 8 ] ] );
   pure( "lower_quartile/three", "lowerQuartile", [ [ 30, 10, 20 ] ] );
   pure( "lower_quartile/one", "lowerQuartile", [ [ 4 ] ] );

   /* ---- flags: flagBaseline, flagHigh, corrected, flagReferences ----------------------------- */
   const ECC = [ 0.40, 0.42, 0.44, 0.46, 0.48, 0.50 ];
   const ecc_list = ECC.map( e => ( { eccentricity: e, stars: 100 } ) );
   pure( "flag_baseline/eccentricity", "flagBaseline", [ ecc_list, "eccentricity", false ] );
   pure( "flag_baseline/too_few", "flagBaseline", [ ecc_list.slice( 0, 4 ), "eccentricity", false ] );
   pure( "flag_baseline/zero_excluded", "flagBaseline", [ [ { stars: 0 }, { stars: 100 }, { stars: 200 }, { stars: 300 }, { stars: 400 } ], "stars", false ] );
   pure( "flag_baseline/zero_allowed", "flagBaseline", [ [ { stars: 0 }, { stars: 100 }, { stars: 200 }, { stars: 300 }, { stars: 400 } ], "stars", true ] );
   pure( "flag_baseline/null_entries", "flagBaseline", [ [ null, ...ecc_list, null ], "eccentricity", false ] );
   pure( "flag_baseline/no_spread", "flagBaseline", [ [ 0.4, 0.4, 0.4, 0.4, 0.4, 0.4 ].map( e => ( { eccentricity: e } ) ), "eccentricity", false ] );
   pure( "flag_baseline/sigma_below_floor", "flagBaseline",
         [ [ 1000, 1000.1, 999.9, 1000.2, 999.8 ].map( e => ( { stars: e } ) ), "stars", false ] );

   const BASE = { median: 11, sigma: 0.5, values: [ 10, 11, 12 ], spread: true };
   /* the threshold is median + FLAG_K*sigma = 11 + 3*0.5 = 12.5 */
   pure( "flag_high/above", "flagHigh", [ BASE, 12.6 ] );
   pure( "flag_high/at_threshold", "flagHigh", [ BASE, 12.5 ] );
   pure( "flag_high/below_low_side", "flagHigh", [ BASE, 4 ] );
   pure( "flag_high/no_spread", "flagHigh", [ Object.assign( {}, BASE, { spread: false } ), 99 ] );
   pure( "flag_high/no_baseline", "flagHigh", [ null, 99 ] );
   pure( "flag_high/not_a_number", "flagHigh", [ BASE, null ] );

   const M_OK = { altitude: 30, fwhm: 3.0, psfFlux: 1000, background: 200, eccentricity: 0.4, stars: 1000 };
   pure( "corrected/altitude30", "corrected", [ M_OK ] );
   pure( "corrected/no_altitude", "corrected", [ { fwhm: 3.0, psfFlux: 1000 } ] );
   pure( "corrected/altitude_one", "corrected", [ { altitude: 1, fwhm: 2.5, psfFlux: 500 } ] );
   pure( "corrected/zero_flux", "corrected", [ { altitude: 45, fwhm: 2.5, psfFlux: 0 } ] );
   pure( "corrected/null_frame", "corrected", [ null ] );

   const RLIST = [ 0, 1, 2, 3, 4, 5, 6, 7 ].map( i => ( { altitude: 40 + i, fwhm: 3.0 + 0.1 * i,
                                                          psfFlux: 1000 + 10 * i, background: 200 + i,
                                                          eccentricity: 0.4 + 0.01 * i, stars: 1000 - 5 * i } ) );
   const RCORR = RLIST.map( F.corrected );
   pure( "flag_references/eight", "flagReferences", [ RLIST, RCORR ] );
   pure( "flag_references/four", "flagReferences", [ RLIST.slice( 0, 4 ), RCORR.slice( 0, 4 ) ] );
   const RLIST_NULL = RLIST.map( ( m, i ) => ( i == 2 ? null : m ) );
   pure( "flag_references/with_null", "flagReferences", [ RLIST_NULL, RLIST_NULL.map( F.corrected ) ] );

   /* ---- flags: blurCause and the whole channel (anomalyFlags) -------------------------------- */
   function frame( i, patch )
   {
      return Object.assign( {
         path: "/subs/L_" + String( i + 1 ).padStart( 3, "0" ) + ".xisf",
         fwhm: 3.0 + 0.01 * ( ( i * 7 ) % 5 ), eccentricity: 0.40 + 0.01 * ( ( i * 3 ) % 5 ),
         stars: 1200 + 10 * ( ( i * 11 ) % 7 ), psfFlux: 5000 + 20 * ( ( i * 5 ) % 9 ),
         background: 200 + ( ( i * 13 ) % 7 ), altitude: 60, psfSNR: 40, noise: 0.001 }, patch || {} );
   }
   /* n frames; patches[i] changes frame i, null makes it an unmeasured hole in the list */
   function night( n, patches )
   {
      const out = [];
      for ( let i = 0; i < n; ++i )
         out.push( patches && patches[i] === null ? null : frame( i, patches && patches[i] ) );
      return out;
   }
   /* every frame of a night gets the same patch, except those in `except` */
   function everyFrame( n, patch, except )
   {
      const p = {};
      for ( let i = 0; i < n; ++i )
         if ( !( except && except[i] ) ) p[i] = patch;
      return p;
   }
   const T0 = Date.UTC( 2026, 8, 18, 20, 0 );
   const TIMES = [ ...Array( 12 ).keys() ].map( i => T0 + i * 60000 );
   /* A frame at low altitude: its FWHM is wide raw, and near 3.0 once airmass is taken out. */
   const LOW = { altitude: 25, fwhm: 3.0 * Math.pow( 1 / Math.sin( 25 * Math.PI / 180 ), 0.6 ) };

   const scenarios = {
      quiet:                 [ night( 12 ), true, TIMES ],
      focus_run:             [ night( 12, { 4: { fwhm: 4.6 }, 5: { fwhm: 4.8 } } ), true, TIMES ],
      altitude_run:          [ night( 12, { 6: LOW, 7: LOW, 8: LOW } ), true, TIMES ],
      seeing_blip:           [ night( 12, { 4: { fwhm: 4.4 } } ), true, TIMES ],
      cloud_flux:            [ night( 12, { 7: { psfFlux: 2000 } } ), true, TIMES ],
      cloud_background:      [ night( 12, { 2: { background: 300 } } ), true, TIMES ],
      dropped_star_count:    [ night( 12, { 11: { stars: 1 } } ), true, TIMES ],
      dropped_zero_stars:    [ night( 12, { 11: { stars: 0 } } ), true, TIMES ],
      tracking_eccentricity: [ night( 12, { 5: { eccentricity: 0.9 } } ), true, TIMES ],
      too_few_frames:        [ night( 4, { 2: { fwhm: 4.6 }, 3: { stars: 1 } } ), true, TIMES.slice( 0, 4 ) ],
      five_frames:           [ night( 5, { 2: { fwhm: 4.6 } } ), true, TIMES.slice( 0, 5 ) ],
      sigma_floor_ecc:       [ night( 12, Object.assign( everyFrame( 12, { eccentricity: 0.40 } ), { 5: { eccentricity: 0.41 } } ) ), true, TIMES ],
      null_background:       [ night( 12, Object.assign( everyFrame( 12, { background: null } ), { 9: { psfFlux: 2000 } } ) ), true, TIMES ],
      uncomparable:          [ night( 12, { 4: { fwhm: 4.6 }, 7: { psfFlux: 2000 } } ), false, TIMES ],
      times_null:            [ night( 12, { 4: { fwhm: 4.4 }, 5: { fwhm: 4.6 } } ), true, null ],
      times_one_missing:     [ night( 12, { 4: { fwhm: 4.4 } } ), true, TIMES.map( ( t, i ) => ( i == 3 ? null : t ) ) ],
      no_altitude:           [ night( 12, Object.assign( everyFrame( 12, { altitude: null } ), { 4: { fwhm: 4.6 } } ) ), true, TIMES ],
      unmeasured_hole:       [ night( 12, { 6: null, 4: { fwhm: 4.6 } } ), true, TIMES ],
      altitude_run_no_times: [ night( 12, { 6: LOW, 7: LOW, 8: LOW } ), true, null ]
   };
   for ( const name of Object.keys( scenarios ) )
   {
      const [ list, comparable, times ] = scenarios[name];
      pure( "anomaly_flags/" + name, "anomalyFlags", [ list, comparable, times ] );
   }

   /* blurCause on one frame of a hand-built night, where the neighbours decide the cause */
   const BL = night( 12, { 4: { fwhm: 4.4 }, 5: { fwhm: 4.6 } } );
   const BLC = BL.map( F.corrected );
   const BLR = F.flagReferences( BL, BLC );
   const BLB = BLC.map( c => BLR.fwhmCorr != null && c.fwhm != null && c.fwhm > ( 1 + F.BLUR_MARGIN ) * BLR.fwhmCorr );
   const BLO = F.timeOrder( BL, TIMES );
   pure( "blur_cause/focus_run_frame", "blurCause", [ 4, BL, BLC, BLR, BLB, BLO ] );
   pure( "blur_cause/no_order", "blurCause", [ 4, BL, BLC, BLR, BLB, null ] );
   pure( "blur_cause/sharp_frame", "blurCause", [ 0, BL, BLC, BLR, BLB, BLO ] );
   pure( "blur_cause/no_references", "blurCause", [ 4, BL, BLC, { fwhm: null, fwhmCorr: null }, BLB, BLO ] );

   pure( "is_cloud/dimmed", "isCloud", [ { background: 200 }, { flux: 700 }, { flux: 1000, background: 200 } ] );
   pure( "is_cloud/bright_sky", "isCloud", [ { background: 230 }, { flux: 1000 }, { flux: 1000, background: 200 } ] );
   pure( "is_cloud/neither", "isCloud", [ { background: 204 }, { flux: 900 }, { flux: 1000, background: 200 } ] );
   pure( "is_cloud/no_reference", "isCloud", [ { background: 900 }, { flux: 1 }, { flux: null, background: null } ] );

   pure( "time_order/sorted", "timeOrder", [ [ { fwhm: 1 }, { fwhm: 2 }, { fwhm: 3 } ], [ 30, 10, 20 ] ] );
   pure( "time_order/hole", "timeOrder", [ [ { fwhm: 1 }, null, { fwhm: 3 } ], [ 30, null, 20 ] ] );
   pure( "time_order/missing_time", "timeOrder", [ [ { fwhm: 1 }, { fwhm: 3 } ], [ 30, null ] ] );
   pure( "time_order/no_times", "timeOrder", [ [ { fwhm: 1 } ], null ] );

   pure( "flag_summary/none", "flagSummary", [ [] ] );
   pure( "flag_summary/empty_lists", "flagSummary", [ [ [], [] ] ] );
   pure( "flag_summary/mixed", "flagSummary", [ [ [ "cloud" ], [ "focus", "seeing" ], [ "cloud" ], [] ] ] );
   pure( "flag_summary/all_kinds", "flagSummary", [ [ [ "dropped", "tracking", "altitude" ], [ "cloud", "focus", "seeing" ] ] ] );

   pure( "airmass_line/with_altitude", "airmassLine", [ { altitude: 41.2, fwhm: 3.81, psfFlux: 1000 } ] );
   pure( "airmass_line/tie", "airmassLine", [ { altitude: 2.25, fwhm: 3.125, psfFlux: 1000 } ] );
   pure( "airmass_line/no_altitude", "airmassLine", [ { fwhm: 3.0 } ] );
   pure( "airmass_line/null", "airmassLine", [ null ] );

   const ROW = { path: "/s/a.xisf", state: "approved", override: null, reasons: [ "FWHM high", "ecc high" ],
                 metrics: { altitude: 41.2, fwhm: 3.81, psfFlux: 1000 } };
   pure( "row_tooltip/flags", "rowTooltip", [ ROW, [ "focus", "cloud" ] ] );
   pure( "row_tooltip/rescued", "rowTooltip", [ Object.assign( {}, ROW, { override: "rescued", reasons: [] } ), [] ] );
   pure( "row_tooltip/condemned_unmeasurable", "rowTooltip",
         [ Object.assign( {}, ROW, { state: "unmeasurable", override: "condemned", metrics: null, reasons: [] } ), null ] );
   pure( "row_tooltip/plain", "rowTooltip", [ Object.assign( {}, ROW, { reasons: [], metrics: null } ), [] ] );

   const TROW = { path: "/s/a.xisf", state: "rejected", override: null, reasons: [], metrics: null };
   pure( "frame_tags/rejected_on", "frameTags", [ TROW, [], true, false ] );
   pure( "frame_tags/channel_off", "frameTags", [ Object.assign( {}, TROW, { state: "approved" } ), [ "cloud" ], false, false ] );
   pure( "frame_tags/rescued_flagged", "frameTags", [ Object.assign( {}, TROW, { override: "rescued" } ), [ "focus", "dropped" ], true, false ] );
   pure( "frame_tags/copying_approved", "frameTags", [ Object.assign( {}, TROW, { state: "approved" } ), [], true, true ] );

   /* ---- files: digest cache ------------------------------------------------------------------ */
   const HEX = "a".repeat( 40 );
   const STAT = { size: 10, mtime: 5, created: 7, at: 100000 };
   pure( "digest_cache_entry/full", "digestCacheEntry", [ STAT, HEX ] );
   pure( "digest_cache_entry/no_created", "digestCacheEntry", [ { size: 10, mtime: 5 }, HEX ] );
   pure( "digest_cache_entry/created_null", "digestCacheEntry", [ { size: 10, mtime: 5, created: null }, HEX ] );
   pure( "digest_cache_entry/upper_hex", "digestCacheEntry", [ STAT, HEX.toUpperCase() ] );
   pure( "digest_cache_entry/short_hex", "digestCacheEntry", [ STAT, "a".repeat( 39 ) ] );
   pure( "digest_cache_entry/newline_hex", "digestCacheEntry", [ STAT, HEX + "\n" ] );
   pure( "digest_cache_entry/not_string", "digestCacheEntry", [ STAT, 12 ] );
   pure( "digest_cache_entry/size_string", "digestCacheEntry", [ { size: "10", mtime: 5 }, HEX ] );
   pure( "digest_cache_entry/created_string", "digestCacheEntry", [ { size: 10, mtime: 5, created: "x" }, HEX ] );
   pure( "digest_cache_entry/null_stat", "digestCacheEntry", [ null, HEX ] );

   const STORED = { size: 10, mtime: 5, created: null, digest: HEX };
   pure( "digest_cache_stored/valid", "digestCacheStored", [ STORED ] );
   pure( "digest_cache_stored/no_created_key", "digestCacheStored", [ { size: 10, mtime: 5, digest: HEX } ] );
   pure( "digest_cache_stored/bad_digest", "digestCacheStored", [ Object.assign( {}, STORED, { digest: "zz" } ) ] );
   pure( "digest_cache_stored/null", "digestCacheStored", [ null ] );
   pure( "digest_cache_stored/string", "digestCacheStored", [ "nope" ] );

   const TABLE = { "/s/a.xisf": STORED, "/s/b.xisf": { size: 10, mtime: 5, digest: HEX } };
   pure( "digest_cache_hit/match", "digestCacheHit", [ TABLE, "/s/a.xisf", { size: 10, mtime: 5, created: null, at: 1 } ] );
   pure( "digest_cache_hit/size_changed", "digestCacheHit", [ TABLE, "/s/a.xisf", { size: 11, mtime: 5, created: null, at: 1 } ] );
   pure( "digest_cache_hit/created_changed", "digestCacheHit", [ TABLE, "/s/a.xisf", { size: 10, mtime: 5, created: 9, at: 1 } ] );
   pure( "digest_cache_hit/no_entry", "digestCacheHit", [ TABLE, "/s/c.xisf", { size: 10, mtime: 5, at: 1 } ] );
   pure( "digest_cache_hit/entry_without_created", "digestCacheHit", [ TABLE, "/s/b.xisf", { size: 10, mtime: 5, at: 1 } ] );
   pure( "digest_cache_hit/inherited_key", "digestCacheHit", [ TABLE, "constructor", { size: 10, mtime: 5, at: 1 } ] );
   pure( "digest_cache_hit/null_table", "digestCacheHit", [ null, "/s/a.xisf", { size: 10, mtime: 5, at: 1 } ] );

   pure( "digest_racy/settled", "digestRacy", [ { at: 10000 }, { mtime: 1000, created: null } ] );
   pure( "digest_racy/mtime_recent", "digestRacy", [ { at: 10000 }, { mtime: 9000, created: null } ] );
   pure( "digest_racy/created_recent", "digestRacy", [ { at: 10000 }, { mtime: 1000, created: 9500 } ] );
   pure( "digest_racy/at_boundary", "digestRacy", [ { at: 10000 }, { mtime: 8000, created: null } ] );
   pure( "digest_racy/no_clock", "digestRacy", [ { }, { mtime: 1000, created: null } ] );

   mutated( "digest_cache_record/store", "digestCacheRecord", [ {}, "/s/a.xisf", { size: 10, mtime: 5, at: 100000 }, HEX ] );
   mutated( "digest_cache_record/racy_removes", "digestCacheRecord",
            [ { "/s/a.xisf": STORED }, "/s/a.xisf", { size: 10, mtime: 99999, at: 100000 }, HEX ] );
   mutated( "digest_cache_record/bad_hex_removes", "digestCacheRecord",
            [ { "/s/a.xisf": STORED }, "/s/a.xisf", { size: 10, mtime: 5, at: 100000 }, "xyz" ] );
   mutated( "digest_cache_record/missing_clock_removes", "digestCacheRecord",
            [ { "/s/a.xisf": STORED }, "/s/a.xisf", { size: 10, mtime: 5 }, HEX ] );

   /* ---- files: names and prefixes ------------------------------------------------------------ */
   pure( "short_names/sequence", "shortNames", [ [ "/d/Light_Ha_001.xisf", "/d/Light_Ha_002.xisf" ] ] );
   pure( "short_names/single", "shortNames", [ [ "/a/x.fit" ] ] );
   pure( "short_names/no_shared_separator", "shortNames", [ [ "/a/Light_1.fit", "/a/Other.fit" ] ] );
   pure( "short_names/identical", "shortNames", [ [ "/a/Light_1_x.fit", "/a/Light_1_x.fit" ] ] );
   pure( "short_names/no_separator_prefix", "shortNames", [ [ "/a/abc", "/a/abd" ] ] );
   pure( "short_names/backslash", "shortNames", [ [ "C:\\data\\L_1.fit", "C:\\data\\L_2.fit" ] ] );
   pure( "common_prefix/two", "commonPrefix", [ [ "abc", "abd" ] ] );
   pure( "common_prefix/one", "commonPrefix", [ [ "x" ] ] );
   pure( "common_prefix/none", "commonPrefix", [ [ "a", "b" ] ] );
   pure( "last_separator_in/found", "lastSeparatorIn", [ "a_b-c.d" ] );
   pure( "last_separator_in/none", "lastSeparatorIn", [ "abc" ] );
   pure( "last_separator_in/empty", "lastSeparatorIn", [ "" ] );
   pure( "output_name/posix", "outputName", [ "/a/b.fit" ] );
   pure( "output_name/windows", "outputName", [ "C:\\x\\y.fit" ] );
   pure( "output_name/bare", "outputName", [ "plain.fit" ] );
   pure( "output_name/trailing_slash", "outputName", [ "/a/b/" ] );

   /* ---- files: needingXisf, byDirectory, destinationIsSource --------------------------------- */
   pure( "needing_xisf/mixed", "needingXisf", [ [ "/a/x.xisf", "/a/y.fit", "/a/z.XISF", "/a/w.fits", "/a/noext" ] ] );
   pure( "needing_xisf/hidden", "needingXisf", [ [ "/a/.xisf", "/a/q.xisf.fit" ] ] );
   pure( "by_directory/two", "byDirectory", [ [ "/a/x.fit", "/b/y.fit", "/a/z.fit" ] ] );
   pure( "by_directory/empty", "byDirectory", [ [] ] );
   pure( "destination_is_source/yes", "destinationIsSource", [ [ "/a/x.fit" ], "/a" ] );
   pure( "destination_is_source/trailing_slash", "destinationIsSource", [ [ "/a/x.fit" ], "/a/" ] );
   pure( "destination_is_source/no", "destinationIsSource", [ [ "/a/x.fit" ], "/b" ] );
   pure( "destination_is_source/null", "destinationIsSource", [ [ "/a/x.fit" ], null ] );

   /* ---- files: emptyRefusal, withoutTrailingSlash, outputMapping ----------------------------- */
   pure( "empty_refusal/null", "emptyRefusal", [ [ "/a/x.fit" ], null, "/home/u" ] );
   pure( "empty_refusal/blank", "emptyRefusal", [ [ "/a/x.fit" ], "   ", "/home/u" ] );
   pure( "empty_refusal/root", "emptyRefusal", [ [ "/a/x.fit" ], "/", "/home/u" ] );
   pure( "empty_refusal/root_slashes", "emptyRefusal", [ [ "/a/x.fit" ], "///", null ] );
   pure( "empty_refusal/home", "emptyRefusal", [ [ "/a/x.fit" ], "/home/u/", "/home/u" ] );
   pure( "empty_refusal/contains_sources", "emptyRefusal", [ [ "/data/a/x.fit" ], "/data", null ] );
   pure( "empty_refusal/is_source", "emptyRefusal", [ [ "/data/a/x.fit" ], "/data/a/", null ] );
   pure( "empty_refusal/prefix_sibling", "emptyRefusal", [ [ "/data/a/x.fit" ], "/data/ab", null ] );
   pure( "empty_refusal/inside_source", "emptyRefusal", [ [ "/data/a/x.fit" ], "/data/a/sub", null ] );
   pure( "empty_refusal/elsewhere", "emptyRefusal", [ [ "/x/y.fit" ], "/elsewhere", "/home/u" ] );
   pure( "without_trailing_slash/slashes", "withoutTrailingSlash", [ "/a/b//" ] );
   pure( "without_trailing_slash/root", "withoutTrailingSlash", [ "/" ] );
   pure( "without_trailing_slash/bare", "withoutTrailingSlash", [ "a" ] );

   pure( "output_mapping/plain", "outputMapping", [ [ "/s/a.fit", "/s/b.fit" ], "/out", ".xisf" ] );
   pure( "output_mapping/collision_after_convert", "outputMapping", [ [ "/s1/a.fit", "/s2/a.xisf" ], "/out", ".xisf" ] );
   pure( "output_mapping/no_convert_distinct", "outputMapping", [ [ "/s1/a.fit", "/s2/a.xisf" ], "/out", null ] );
   pure( "output_mapping/no_convert_collision", "outputMapping", [ [ "/s1/a.fit", "/s2/a.fit" ], "/out", null ] );
   pure( "output_mapping/aliased", "outputMapping", [ [ "/s/a.fit" ], "/s", ".xisf" ] );
   pure( "output_mapping/empty", "outputMapping", [ [], "/out", ".xisf" ] );

   /* ---- files: filmstrip arithmetic ---------------------------------------------------------- */
   pure( "strip_first/fits", "stripFirst", [ 5, 0, 10, null ] );
   pure( "strip_first/centre", "stripFirst", [ 100, 50, 10, null ] );
   pure( "strip_first/last_clamp", "stripFirst", [ 100, 99, 10, null ] );
   pure( "strip_first/first_clamp", "stripFirst", [ 100, 0, 10, null ] );
   pure( "strip_first/keeps_current", "stripFirst", [ 100, 50, 10, 45 ] );
   pure( "strip_first/recentres_when_selection_offscreen", "stripFirst", [ 100, 50, 10, 60 ] );
   pure( "strip_first/no_selection", "stripFirst", [ 100, -1, 10, null ] );
   pure( "strip_first/current_past_end", "stripFirst", [ 100, 95, 10, 95 ] );
   pure( "strip_first/current_negative", "stripFirst", [ 100, 50, 10, -1 ] );
   pure( "strip_first/selected_at_current_end", "stripFirst", [ 100, 95, 10, 90 ] );

   pure( "tile_at/first", "tileAt", [ 0, 10, 5, 0, 20 ] );
   pure( "tile_at/inside_tile", "tileAt", [ 9.9, 10, 5, 0, 20 ] );
   pure( "tile_at/second_tile", "tileAt", [ 10, 10, 5, 0, 20 ] );
   pure( "tile_at/last_tile", "tileAt", [ 49.9, 10, 5, 0, 20 ] );
   pure( "tile_at/past_visible", "tileAt", [ 50, 10, 5, 0, 20 ] );
   pure( "tile_at/negative_x", "tileAt", [ -1, 10, 5, 0, 20 ] );
   pure( "tile_at/offset_first", "tileAt", [ 25, 10, 5, 10, 20 ] );
   pure( "tile_at/past_count", "tileAt", [ 45, 10, 5, 17, 20 ] );

   pure( "thumbnail_order/middle_selection", "thumbnailOrder", [ 10, 5, 0, 3 ] );
   pure( "thumbnail_order/no_selection", "thumbnailOrder", [ 10, -1, 4, 2 ] );
   pure( "thumbnail_order/nothing_visible", "thumbnailOrder", [ 5, 2, 0, 0 ] );
   pure( "thumbnail_order/first_frame", "thumbnailOrder", [ 10, 0, 0, 4 ] );
   pure( "thumbnail_order/last_frame", "thumbnailOrder", [ 10, 9, 6, 4 ] );
   pure( "thumbnail_order/one", "thumbnailOrder", [ 1, 0, 0, 1 ] );
   pure( "thumbnail_order/empty", "thumbnailOrder", [ 0, -1, 0, 3 ] );

   pure( "batches_of/split", "batchesOf", [ [ 1, 2, 3, 4, 5 ], 2 ] );
   pure( "batches_of/zero_size", "batchesOf", [ [ 1, 2, 3, 4, 5 ], 0 ] );
   pure( "batches_of/fraction", "batchesOf", [ [ 1, 2, 3, 4, 5 ], 2.7 ] );
   pure( "batches_of/bigger_than_list", "batchesOf", [ [ 1, 2, 3 ], 10 ] );
   pure( "batches_of/empty", "batchesOf", [ [], 3 ] );
   pure( "batches_of/negative", "batchesOf", [ [ 1, 2, 3, 4 ], -3 ] );

   mergeCase( "merge_batch/all_asked", [ "a", "b" ], { a: { x: 1 }, b: { x: 2 } }, {} );
   mergeCase( "merge_batch/no_result", [ "a" ], null, {} );
   mergeCase( "merge_batch/not_asked", [ "a", "b" ], { a: { x: 1 }, c: { x: 3 } }, {} );
   mergeCase( "merge_batch/already_measured", [ "a" ], { a: { x: 9 } }, { a: { x: 1 } } );
   mergeCase( "merge_batch/null_is_unmeasured", [ "a" ], { a: { x: 9 } }, { a: null } );
   mergeCase( "merge_batch/partial_result", [ "a", "b" ], {}, {} );

   /* ---- files: measuringLines ---------------------------------------------------------------- */
   pure( "measuring_lines/channel_bar", "measuringLines", [ "Ha", 3, 10, null ] );
   pure( "measuring_lines/channel_none", "measuringLines", [ "Ha", 0, 0, null ] );
   pure( "measuring_lines/overall_bar", "measuringLines",
         [ "L", 5, 10, { done: 20, total: 100, channel: 2, channels: 3 } ] );
   pure( "measuring_lines/overall_all_cached", "measuringLines",
         [ "L", 5, 10, { done: 0, total: 0, channel: 1, channels: 2 } ] );

   /* ---- the File helpers the functions above call, pinned to the shim on edge inputs ---------- */
   [ "/a/b.fit", "/b.fit", "b.fit", "/a/b/", "a//b", "/", "", "/a/.b.c", "/a/b.c.fit", "/a/a.", "/a/.bashrc",
     "/a/..", "/a/.", "/a/x.xisf", "/a/Light_Ha.XISF" ]
      .forEach( ( p, i ) => {
         cases["file/extract_directory/" + i] = { fn: "File.extractDirectory", args: [ p ], out: norm( File.extractDirectory( p ) ) };
         cases["file/extract_extension/" + i] = { fn: "File.extractExtension", args: [ p ], out: norm( File.extractExtension( p ) ) };
         cases["file/extract_name/" + i] = { fn: "File.extractName", args: [ p ], out: norm( File.extractName( p ) ) };
      } );

   return cases;
};
