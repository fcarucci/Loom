"use strict";
/*
 * Golden cases for loomsiril/asiair_names.py: the first half of pixinsight/script/lib/AsiairNames.js (lines 1-331).
 *
 * Each case records the arguments and what the JS returned, so the Python suite replays the same calls and compares.
 * Frame and session records are built here with the library itself and then recorded as plain data, so the replay
 * reads the same frames the JS saw. Undefined is never passed as an argument: null stands for "not given".
 *
 * Undefined does not survive JSON; outputs that are undefined are written as "__undefined__", and object keys whose
 * value is undefined are dropped, as JSON.stringify drops them.
 */
module.exports = function( js )
{
   const A = js.AsiairNames;
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
      const out = A[name].apply( A, structuredClone( args ) );
      cases[key] = { fn: name, args: norm( args ), out: norm( out ) };
   }

   /* A frame record as the importer builds it: the parsed name plus the comparison key of its stamp. */
   function frame( target, stamp, filter, extra )
   {
      return Object.assign( { target: target, filter: filter, stamp: stamp, key: A.stampKey( stamp ) }, extra || {} );
   }

   /* ---- parseName: the real name, the published grammar, the other frame types, and the refusals ---------- */
   const NAMES = {
      "real_name": "Light_IC 1396A_180.0s_Bin1_2600MM_H_gain100_20260807-215716_180deg_-7.0C_0001.fit",
      "real_name_fits_extension": "Light_IC 1396A_180.0s_Bin1_2600MM_H_gain100_20260807-215716_180deg_-7.0C_0001.fits",
      "real_name_upper_extension": "Light_IC 1396A_180.0s_Bin1_2600MM_H_gain100_20260807-215716_180deg_-7.0C_0001.FIT",
      "real_name_xisf_refused": "Light_IC 1396A_180.0s_Bin1_2600MM_H_gain100_20260807-215716_180deg_-7.0C_0001.xisf",
      "real_name_no_extension": "Light_IC 1396A_180.0s_Bin1_2600MM_H_gain100_20260807-215716_180deg_-7.0C_0001",
      "published_grammar_with_target": "Light_M42_180s_Bin1_H_gain100_20260807-215716_-7.0C_0001.fit",
      "published_grammar_no_target": "Light_180s_Bin1_L_gain100_20260807-215716_-7.0C_0001.fit",
      "flat_frame": "Flat_IC 1396A_0.1s_Bin1_2600MM_H_gain100_20260807-180000_30deg_-7.0C_0003.fit",
      "dark_frame": "Dark_180.0s_Bin1_2600MM_gain100_20260807-020000_-7.0C_0001.fit",
      "dark_with_filter_slot": "Dark_180.0s_Bin1_H_gain100_20260807-020000_-7.0C_0001.fit",
      "bias_with_exposure": "Bias_0.0s_Bin1_2600MM_gain100_20260807-020000_-7.0C_0001.fit",
      "bias_without_exposure_refused": "Bias_Bin1_2600MM_gain100_20260807-020000_-7.0C_0001.fit",
      "appledouble_sidecar_refused": "._Light_IC 1396A_180.0s_Bin1_2600MM_H_gain100_20260807-215716_180deg_-7.0C_0001.fit",
      "lowercase_type_accepted": "light_M42_180s_Bin1_H_gain100_20260807-215716_-7.0C_0001.fit",
      "type_plural_refused": "Lights_M42_180s_Bin1_H_gain100_20260807-215716_-7.0C_0001.fit",
      "ms_exposure": "Light_M42_500ms_Bin1_H_gain100_20260807-215716_-7.0C_0001.fit",
      "fractional_exposure": "Light_M42_180.5s_Bin1_H_gain100_20260807-215716_-7.0C_0001.fit",
      "target_contains_exposure_like_token": "Light_M42_30s_180.0s_Bin1_H_gain100_20260807-215716_-7.0C_0001.fit",
      "target_with_underscores_and_camera_underscore": "Light_M42_NB_180s_Bin1_ZWO_ASI2600_H_gain100_20260807-215716_-7.0C_0001.fit",
      "two_bin_tokens": "Light_M42_180s_Bin1_Bin2_H_gain100_20260807-215716_-7.0C_0001.fit",
      "missing_filter_refused": "Light_M42_180s_Bin1_gain100_20260807-215716_-7.0C_0001.fit",
      "missing_exposure_refused": "Light_M42_Bin1_H_gain100_20260807-215716_-7.0C_0001.fit",
      "missing_bin_refused": "Light_M42_180s_H_gain100_20260807-215716_-7.0C_0001.fit",
      "missing_gain_refused": "Light_M42_180s_Bin1_H_20260807-215716_-7.0C_0001.fit",
      "missing_temp_refused": "Light_M42_180s_Bin1_H_gain100_20260807-215716_0001.fit",
      "missing_sequence_refused": "Light_M42_180s_Bin1_H_gain100_20260807-215716_-7.0C.fit",
      "two_stamps_refused": "Light_M42_180s_Bin1_H_gain100_20260807-215716_20260808-010000_-7.0C_0001.fit",
      "two_rotations_refused": "Light_M42_180s_Bin1_H_gain100_20260807-215716_180deg_90deg_-7.0C_0001.fit",
      "two_temperatures_refused": "Light_M42_180s_Bin1_H_gain100_20260807-215716_-7.0C_5.0C_0001.fit",
      "positive_temperature": "Light_M42_180s_Bin1_H_gain100_20260807-215716_5.0C_0001.fit",
      "plus_sign_temperature_refused": "Light_M42_180s_Bin1_H_gain100_20260807-215716_+5.0C_0001.fit",
      "zero_rotation": "Light_M42_180s_Bin1_H_gain100_20260807-215716_0deg_-7.0C_0001.fit",
      "sequence_all_zeros": "Light_M42_180s_Bin1_H_gain100_20260807-215716_-7.0C_0000.fit",
      "generic_name_refused": "IMG_0001.fit",
      "empty_string_refused": "",
      "bare_extension_refused": ".fit",
      "null_input_refused": null,
      "xisf_sidecar_style_refused": "Light_M42_180s_Bin1_H_gain100_20260807-215716_-7.0C_0001.xisf.bak",
      "stamp_with_leading_space_refused": "Light_M42_180s_Bin1_H_gain100_ 20260807-215716_-7.0C_0001.fit",
      "midnight_stamp": "Light_M42_180s_Bin1_H_gain100_20260807-000000_-7.0C_0001.fit"
   };
   for ( const key of Object.keys( NAMES ) ) pure( "parseName/" + key, "parseName", [ NAMES[key] ] );

   /* ---- stampKey: calendar edges, rolled dates, year mapping, unreadable stamps --------------------------- */
   const STAMPS = {
      "real_stamp": "20260807-215716",
      "leap_day_2024": "20240229-000000",
      "leap_day_2026_rolled_refused": "20260229-120000",
      "31_february_refused": "20260231-120000",
      "30_february_refused": "20260230-000000",
      "31_april_refused": "20260431-000000",
      "last_second_of_day": "20260807-235959",
      "24_00_00_refused": "20260807-240000",
      "minute_60_refused": "20260807-236000",
      "second_60_refused": "20260807-230060",
      "month_13_refused": "20261301-000000",
      "month_00_refused": "20260000-000000",
      "day_00_refused": "20260100-000000",
      "day_32_refused": "20260132-000000",
      "unix_epoch": "19700101-000000",
      "one_second_before_epoch": "19691231-235959",
      "year_0050_refused_by_year_mapping": "00500101-000000",
      "year_0000_refused_by_year_mapping": "00000101-000000",
      "year_0100_is_not_mapped": "01000101-000000",
      "dst_spring_forward": "20260308-010000",
      "dst_spring_forward_0300": "20260308-030000",
      "iso_dashed_refused": "2026-08-07",
      "underscore_separator_refused": "20260807_215716",
      "too_long_refused": "2026080721571600",
      "leading_space_refused": " 20260807-215716",
      "empty_refused": ""
   };
   for ( const key of Object.keys( STAMPS ) ) pure( "stampKey/" + key, "stampKey", [ STAMPS[key] ] );
   pure( "stampKey/null_refused", "stampKey", [ null ] );

   /* ---- sessions: the four-hour gap, unsorted input, unreadable keys, midnight, interleaved targets -------- */
   const T0 = "20260807-200000";
   const H = 3600;
   pure( "sessions/empty", "sessions", [ [], null ] );
   pure( "sessions/single_run", "sessions", [ [
      frame( "M42", "20260807-200000", "H" ), frame( "M42", "20260807-203000", "H" ),
      frame( "M42", "20260807-210000", "L" ) ], null ] );
   pure( "sessions/gap_exactly_4h_one_session", "sessions", [ [
      frame( "M42", "20260807-200000", "H" ), frame( "M42", "20260808-000000", "H" ) ], null ] );
   pure( "sessions/gap_4h_plus_1s_splits", "sessions", [ [
      frame( "M42", "20260807-200000", "H" ), frame( "M42", "20260808-000001", "H" ) ], null ] );
   pure( "sessions/gap_4h_minus_1s_one_session", "sessions", [ [
      frame( "M42", "20260807-200000", "H" ), frame( "M42", "20260807-235959", "H" ) ], null ] );
   pure( "sessions/three_sessions_by_gap", "sessions", [ [
      frame( "M42", "20260807-200000", "H" ), frame( "M42", "20260807-210000", "H" ),
      frame( "M42", "20260808-120000", "H" ), frame( "M42", "20260809-130000", "H" ),
      frame( "M42", "20260809-140000", "H" ) ], null ] );
   pure( "sessions/unsorted_input", "sessions", [ [
      frame( "M42", "20260807-220000", "H" ), frame( "M42", "20260807-200000", "L" ),
      frame( "M42", "20260807-210000", "H" ) ], null ] );
   pure( "sessions/null_keys_excluded", "sessions", [ [
      frame( "M42", "20260807-200000", "H" ), frame( "M42", "20260231-120000", "H" ),
      frame( "M42", "20260807-210000", "H" ), frame( "M42", "bogus", "H" ) ], null ] );
   pure( "sessions/all_keys_null", "sessions", [ [ frame( "M42", "bogus", "H" ) ], null ] );
   pure( "sessions/midnight_spanning", "sessions", [ [
      frame( "M42", "20260807-220000", "H" ), frame( "M42", "20260807-235000", "H" ),
      frame( "M42", "20260808-010000", "H" ), frame( "M42", "20260808-030000", "H" ) ], null ] );
   pure( "sessions/interleaved_targets_one_session", "sessions", [ [
      frame( "M42", "20260807-200000", "H" ), frame( "NGC 7000", "20260807-200500", "H" ),
      frame( "M42", "20260807-201000", "L" ), frame( "NGC 7000", "20260807-201500", "O" ) ], null ] );
   pure( "sessions/identical_stamps", "sessions", [ [
      frame( "M42", "20260807-200000", "H" ), frame( "M42", "20260807-200000", "L" ) ], null ] );
   pure( "sessions/custom_gap_1h_splits", "sessions", [ [
      frame( "M42", "20260807-200000", "H" ), frame( "M42", "20260807-213000", "H" ) ], 1 ] );
   pure( "sessions/custom_gap_1h_exactly_one_session", "sessions", [ [
      frame( "M42", "20260807-200000", "H" ), frame( "M42", "20260807-210000", "H" ) ], 1 ] );
   pure( "sessions/custom_gap_fractional", "sessions", [ [
      frame( "M42", "20260807-200000", "H" ), frame( "M42", "20260807-201800", "H" ) ], 0.5 ] );
   pure( "sessions/gap_zero", "sessions", [ [
      frame( "M42", "20260807-200000", "H" ), frame( "M42", "20260807-200001", "H" ) ], 0 ] );
   pure( "sessions/single_frame", "sessions", [ [ frame( "M42", T0, "H" ) ], null ] );
   pure( "sessions/frame_without_key_field", "sessions", [ [
      { target: "M42", filter: "H", stamp: T0 }, frame( "M42", "20260807-210000", "H" ) ], null ] );

   /* ---- filtersOf and byTarget: the primitives nights is built from -------------------------------------- */
   pure( "filtersOf/empty", "filtersOf", [ [] ] );
   pure( "filtersOf/sorted_no_duplicates", "filtersOf", [ [
      { filter: "OIII" }, { filter: "H" }, { filter: "H" }, { filter: "L" }, { filter: "Ha" } ] ] );
   pure( "filtersOf/falsy_filters_skipped", "filtersOf", [ [
      { filter: "" }, { filter: null }, { filter: "R" }, {} ] ] );
   pure( "filtersOf/sort_is_by_code_unit", "filtersOf", [ [
      { filter: "b" }, { filter: "B" }, { filter: "a" }, { filter: "10" }, { filter: "9" } ] ] );
   pure( "byTarget/empty", "byTarget", [ [] ] );
   pure( "byTarget/first_appearance_order", "byTarget", [ [
      { target: "NGC 7000", n: 1 }, { target: "M42", n: 2 }, { target: "NGC 7000", n: 3 } ] ] );
   pure( "byTarget/space_and_underscore_targets", "byTarget", [ [
      { target: "M42_30s", n: 1 }, { target: "M42 30s", n: 2 } ] ] );
   pure( "dateOf/printed_date", "dateOf", [ "20260807-215716" ] );
   pure( "dateOf/first_of_year", "dateOf", [ "20260101-000000" ] );

   /* ---- nights: one per target per session, the evening date, the order, more than three sessions ---------- */
   const interleaved = [
      frame( "M42", "20260807-200000", "H" ), frame( "NGC 7000", "20260807-200500", "O" ),
      frame( "M42", "20260807-201000", "L" ), frame( "NGC 7000", "20260807-201500", "H" ) ];
   pure( "nights/empty", "nights", [ [] ] );
   pure( "nights/interleaved_two_targets", "nights", [ A.sessions( interleaved, null ) ] );
   pure( "nights/evening_date_for_midnight_session", "nights", [ A.sessions( [
      frame( "M42", "20260807-235000", "H" ), frame( "M42", "20260808-010000", "H" ) ], null ) ] );
   pure( "nights/same_first_key_sorted_by_target", "nights", [ A.sessions( [
      frame( "NGC 7000", "20260807-200000", "H" ), frame( "M42", "20260807-200000", "H" ) ], null ) ] );
   const five = [];
   for ( let n = 0; n < 5; ++n )
   {
      const day = "2026080" + ( 7 + n );
      five.push( frame( "M42", day + "-200000", "H" ) );
      five.push( frame( "NGC 7000", day + "-210000", n % 2 ? "O" : "L" ) );
   }
   pure( "nights/five_sessions_last_three", "nights", [ A.sessions( five, null ) ] );
   pure( "nights/session_index_after_split", "nights", [ A.sessions( [
      frame( "M42", "20260807-200000", "H" ), frame( "M42", "20260808-120000", "L" ),
      frame( "IC 1396", "20260808-130000", "H" ) ], null ) ] );
   pure( "nights/null_key_frames_not_in_nights", "nights", [ A.sessions( [
      frame( "M42", "20260807-200000", "H" ), frame( "M42", "20260807-24000", "H" ) ], null ) ] );
   pure( "nights/filters_sorted_per_night", "nights", [ A.sessions( [
      frame( "M42", "20260807-200000", "OIII" ), frame( "M42", "20260807-201000", "H" ),
      frame( "M42", "20260807-202000", "L" ) ], null ) ] );

   /* ---- a full run through parseName, stampKey, sessions and nights, as the importer uses them ------------- */
   const run = [
      "Light_IC 1396A_180.0s_Bin1_2600MM_H_gain100_20260807-215716_180deg_-7.0C_0001.fit",
      "Light_IC 1396A_180.0s_Bin1_2600MM_H_gain100_20260807-220016_180deg_-7.0C_0002.fit",
      "Light_M42_180s_Bin1_OIII_gain100_20260807-230000_-7.0C_0001.fit",
      "._Light_IC 1396A_180.0s_Bin1_2600MM_H_gain100_20260807-220216_180deg_-7.0C_0003.fit",
      "Light_IC 1396A_180.0s_Bin1_2600MM_H_gain100_20260808-020000_180deg_-7.0C_0004.fit",
      "Light_IC 1396A_180.0s_Bin1_2600MM_H_gain100_20260808-190000_180deg_-7.0C_0005.fit" ];
   const built = [];
   for ( const name of run )
   {
      const p = A.parseName( name );
      if ( p ) built.push( Object.assign( p, { key: A.stampKey( p.stamp ) } ) );
   }
   pure( "run/sessions_then_nights", "nights", [ A.sessions( built, null ) ] );
   pure( "run/sessions_by_gap_one", "sessions", [ built, 1 ] );

   return cases;
};
