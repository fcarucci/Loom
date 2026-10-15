"use strict";
/*
 * Golden cases for loomsiril/night_dialog.py: the pure half of pixinsight/script/lib/NightDialog.js (lines 1-119):
 * clockOf, rowFor, flatSummary, surveyOf, RECENT, recentNights and flatsForNight.
 *
 * Each case records the arguments and what the JS returned, so the Python suite replays the same calls and compares.
 * Frame records are built here with the library's own stampKey, and a card is built from lights and flats in the same
 * shape the ASIAIR scan produces. Undefined is never passed as an argument: null stands for "not given".
 *
 * Undefined does not survive JSON; outputs that are undefined are written as "__undefined__", and object keys whose
 * value is undefined are dropped, as JSON.stringify drops them.
 */
module.exports = function( js )
{
   const A = js.AsiairNames;
   const ND = js.NightDialog;
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
      const out = ND[name].apply( ND, structuredClone( args ) );
      cases[key] = { fn: name, args: norm( args ), out: norm( out ) };
   }

   /* A constant of NightDialog, by name. */
   function value( name )
   {
      cases["const/" + name] = { value: name, out: norm( ND[name] ) };
   }

   /* ---- card builders: stamps are local clock strings as the ASIAIR writes them ------------------------- */
   function pad( n, w )
   {
      return String( n ).padStart( w || 2, "0" );
   }

   /* The stamp `offsetSec` seconds after a wall-clock time, computed in UTC so the arithmetic has no zone. */
   function stampAt( y, mo, d, h, mi, s, offsetSec )
   {
      const x = new Date( Date.UTC( y, mo - 1, d, h, mi, s ) + ( offsetSec || 0 ) * 1000 );
      return x.getUTCFullYear() + pad( x.getUTCMonth() + 1 ) + pad( x.getUTCDate() ) + "-" +
             pad( x.getUTCHours() ) + pad( x.getUTCMinutes() ) + pad( x.getUTCSeconds() );
   }

   function light( target, stamp, filter )
   {
      return { target: target, filter: filter, stamp: stamp, key: A.stampKey( stamp ) };
   }

   function flat( filter, stamp )
   {
      return { filter: filter, stamp: stamp, key: A.stampKey( stamp ) };
   }

   /* n lights of one target and filter, stepMin minutes apart, from a wall-clock start. */
   function run( target, filter, y, mo, d, h, mi, n, stepMin )
   {
      const out = [];
      for ( let i = 0; i < n; ++i ) out.push( light( target, stampAt( y, mo, d, h, mi, 0, i * stepMin * 60 ), filter ) );
      return out;
   }

   /* n flats of one filter, stepMin minutes apart, from a wall-clock start. */
   function sweep( filter, y, mo, d, h, mi, n, stepMin )
   {
      const out = [];
      for ( let i = 0; i < n; ++i ) out.push( flat( filter, stampAt( y, mo, d, h, mi, 0, i * stepMin * 60 ) ) );
      return out;
   }

   /* Four nights of M42 (H) and IC 1396A (Ha); the second target is seen on two of them. Lights on the 7th and the
      10th share a session with the other target because the gap between them is under four hours. */
   function fourNightCard()
   {
      return {
         lights: [].concat(
            run( "M42", "H", 2026, 8, 7, 21, 0, 6, 10 ),
            run( "IC 1396A", "Ha", 2026, 8, 7, 22, 30, 4, 15 ),
            run( "M42", "H", 2026, 8, 8, 21, 0, 6, 10 ),
            run( "M42", "H", 2026, 8, 9, 21, 0, 6, 10 ),
            run( "M42", "H", 2026, 8, 10, 21, 0, 6, 10 ),
            run( "IC 1396A", "Ha", 2026, 8, 10, 21, 30, 4, 15 ) ),
         flats: [].concat(
            sweep( "H", 2026, 8, 7, 20, 30, 3, 1 ),
            sweep( "Ha", 2026, 8, 8, 5, 0, 3, 1 ),
            sweep( "H", 2026, 8, 8, 20, 30, 3, 1 ),
            sweep( "H", 2026, 8, 10, 20, 30, 3, 1 ),
            sweep( "L", 2026, 8, 10, 20, 50, 2, 1 ) ),
         unparseable: [ "Light_M42_180s_Bin1_H_gain100_bad.fit", "._Light_M42_180s_Bin1_H_gain100_20260807-215716_-7.0C_0001.fit" ]
      };
   }

   const CARD = fourNightCard();
   const SURVEY = ND.surveyOf( structuredClone( CARD ), null );

   /* ---- clockOf: the local clock of a stamp key ----------------------------------------------------------- */
   pure( "clock_of/evening", "clockOf", [ "20260807-215716" ] );
   pure( "clock_of/midnight", "clockOf", [ "20260808-000000" ] );
   pure( "clock_of/after_midnight", "clockOf", [ "20260808-013045" ] );
   pure( "clock_of/empty", "clockOf", [ "" ] );

   /* ---- rowFor: what one night reads as in the tree -------------------------------------------------------- */
   function nightOf( frames, extra )
   {
      return Object.assign( { sessionIndex: 0, target: "M42", first: frames[0].key, last: frames[frames.length-1].key,
                              count: frames.length, filters: [ "H" ], date: "2026-08-07", frames: frames }, extra || {} );
   }
   const ONE = [ light( "M42", "20260807-215716", "H" ) ];
   const SPAN = [ light( "M42", "20260807-235500", "H" ), light( "M42", "20260808-000500", "H" ), light( "M42", "20260808-013000", "H" ) ];
   pure( "row_for/one_frame", "rowFor", [ nightOf( ONE, { count: 1 } ) ] );
   pure( "row_for/across_midnight", "rowFor", [ nightOf( SPAN, { count: 3, date: "2026-08-07" } ) ] );
   pure( "row_for/two_filters", "rowFor", [ nightOf( SPAN, { count: 3, filters: [ "Ha", "OIII" ] } ) ] );
   pure( "row_for/twelve_frames", "rowFor", [ nightOf( run( "M42", "L", 2026, 8, 7, 1, 0, 12, 5 ), { count: 12, filters: [ "L" ] } ) ] );

   /* ---- flatSummary: how the flats for a night read -------------------------------------------------------- */
   const FLATS3 = [ flat( "Ha", "20260808-050000" ), flat( "Ha", "20260808-050100" ), flat( "Ha", "20260808-050200" ) ];
   const FLATS2 = [ flat( "Ha", "20260808-050000" ), flat( "Ha", "20260808-050100" ) ];
   const FLAT1 = [ flat( "L", "20260808-050000" ) ];
   pure( "flat_summary/exact_plural", "flatSummary", [ [ { filter: "Ha", flats: FLATS3, strength: "exact" } ] ] );
   pure( "flat_summary/single_flat_singular", "flatSummary", [ [ { filter: "L", flats: FLAT1, strength: "exact" } ] ] );
   pure( "flat_summary/weak_plural", "flatSummary", [ [ { filter: "Ha", flats: FLATS2, strength: "weak" } ] ] );
   pure( "flat_summary/weak_singular", "flatSummary", [ [ { filter: "L", flats: FLAT1, strength: "weak" } ] ] );
   pure( "flat_summary/missing", "flatSummary", [ [ { filter: "OIII", flats: [], strength: "missing" } ] ] );
   pure( "flat_summary/missing_and_exact_in_order", "flatSummary", [ [
      { filter: "H", flats: FLATS2, strength: "exact" },
      { filter: "OIII", flats: [], strength: "missing" },
      { filter: "Ha", flats: FLATS3, strength: "weak" } ] ] );
   pure( "flat_summary/no_matches", "flatSummary", [ [] ] );

   /* ---- surveyOf: the card as plain data ------------------------------------------------------------------- */
   pure( "survey/four_nights_two_targets", "surveyOf", [ structuredClone( CARD ), null ] );
   pure( "survey/gap_half_hour_splits_the_evening", "surveyOf", [ structuredClone( CARD ), 0.5 ] );
   pure( "survey/gap_two_hours", "surveyOf", [ structuredClone( CARD ), 2 ] );
   pure( "survey/gap_zero_is_zero_not_default", "surveyOf", [ structuredClone( CARD ), 0 ] );
   pure( "survey/empty_card", "surveyOf", [ { lights: [], flats: [], unparseable: [] }, null ] );
   pure( "survey/flats_only_orphan_flats", "surveyOf", [ { lights: [], flats: sweep( "H", 2026, 8, 8, 5, 0, 3, 1 ), unparseable: [] }, null ] );
   pure( "survey/lights_only_no_flats", "surveyOf", [ { lights: run( "M42", "H", 2026, 8, 7, 21, 0, 4, 10 ), flats: [], unparseable: [] }, null ] );

   /* A batch lying exactly between two sessions goes to the earlier one. The sessions end at 21:05 on the 7th and
      start at 09:00 on the 8th: the midpoint is 03:02:30 on the 8th, and that flat is the batch. */
   const TIE = { lights: [].concat( run( "M42", "H", 2026, 8, 7, 21, 0, 2, 5 ), run( "M42", "H", 2026, 8, 8, 9, 0, 2, 5 ) ),
                 flats: [ flat( "H", "20260808-030230" ) ], unparseable: [] };
   pure( "survey/batch_on_midpoint_goes_to_earlier", "surveyOf", [ TIE, null ] );

   /* Two flat batches owned by one session are pooled in batch order, and a batch far from every session still goes to
      the nearest one (assignBatches has no distance limit). */
   const POOLED = { lights: run( "M42", "H", 2026, 8, 7, 21, 0, 3, 10 ),
                    flats: [].concat( sweep( "H", 2026, 8, 7, 19, 0, 2, 1 ), sweep( "H", 2026, 8, 20, 12, 0, 2, 1 ) ),
                    unparseable: [] };
   pure( "survey/batches_pooled_and_far_batch_to_nearest", "surveyOf", [ POOLED, null ] );

   /* Lights whose stamp cannot be read are excluded from sessions and nights, and do not count as unparseable here. */
   const BADSTAMP = { lights: [].concat( run( "M42", "H", 2026, 8, 7, 21, 0, 2, 10 ),
                                         [ light( "M42", "2026XXXX-000000", "H" ), light( "M42", "bogus", "H" ) ] ),
                      flats: [], unparseable: [ "IMG_0001.fit" ] };
   pure( "survey/unreadable_stamps_excluded", "surveyOf", [ BADSTAMP, null ] );

   /* Target names are carried through as written, including ones that name properties of a plain object. */
   const ODD = { lights: [].concat( run( "constructor", "H", 2026, 8, 7, 21, 0, 2, 10 ),
                                    run( "toString", "H", 2026, 8, 7, 23, 0, 2, 10 ),
                                    run( "M42", "H", 2026, 8, 8, 21, 0, 2, 10 ) ),
                 flats: [], unparseable: [] };
   pure( "survey/target_names_of_object_properties", "surveyOf", [ ODD, null ] );

   /* ---- recentNights: a target's newest nights, the picker's three ----------------------------------------- */
   pure( "recent_nights/default_three_newest_first", "recentNights", [ SURVEY, "M42", null ] );
   pure( "recent_nights/one", "recentNights", [ SURVEY, "M42", 1 ] );
   pure( "recent_nights/zero_falls_back_to_recent", "recentNights", [ SURVEY, "M42", 0 ] );
   pure( "recent_nights/two_of_two_targets_nights", "recentNights", [ SURVEY, "IC 1396A", null ] );
   pure( "recent_nights/larger_than_count", "recentNights", [ SURVEY, "IC 1396A", 9 ] );
   pure( "recent_nights/nan_k_falls_back", "recentNights", [ SURVEY, "M42", NaN ] );
   pure( "recent_nights/fractional_k_truncates", "recentNights", [ SURVEY, "M42", 2.5 ] );
   pure( "recent_nights/negative_fraction_truncates", "recentNights", [ SURVEY, "M42", -1.5 ] );
   pure( "recent_nights/infinite_k_takes_all", "recentNights", [ SURVEY, "M42", Infinity ] );
   pure( "recent_nights/absent_target", "recentNights", [ SURVEY, "NGC 7000", null ] );
   pure( "recent_nights/empty_survey", "recentNights", [ survey_empty(), "M42", null ] );

   /* Two nights on one date, told apart by their span: the later span is the newer. */
   const SAMEDAY = { lights: [].concat( run( "M42", "H", 2026, 8, 7, 1, 0, 2, 10 ), run( "M42", "H", 2026, 8, 7, 21, 0, 2, 10 ) ),
                     flats: [], unparseable: [] };
   pure( "recent_nights/same_date_by_span", "recentNights", [ ND.surveyOf( SAMEDAY, null ), "M42", null ] );

   /* A session running past midnight is dated by its first frame, the evening. */
   const PAST = { lights: run( "M42", "H", 2026, 8, 7, 23, 30, 6, 30 ), flats: [], unparseable: [] };
   pure( "recent_nights/past_midnight_dated_by_evening", "recentNights", [ ND.surveyOf( PAST, null ), "M42", null ] );

   /* Targets whose names are object properties are looked up by equality, not by key. */
   pure( "recent_nights/constructor_target", "recentNights", [ ND.surveyOf( ODD, null ), "constructor", null ] );

   /* ---- flatsForNight: the flats a night is offered -------------------------------------------------------- */
   const OWNED = SURVEY.nights.filter( n => n.target == "M42" )[0];
   pure( "flats_for_night/owned_by_session", "flatsForNight", [ SURVEY, OWNED ] );
   const ORPHAN = ND.surveyOf( { lights: run( "M42", "H", 2026, 8, 7, 21, 0, 2, 10 ), flats: [], unparseable: [] }, null );
   pure( "flats_for_night/no_flats_for_session", "flatsForNight", [ ORPHAN, ORPHAN.nights[0] ] );
   pure( "flats_for_night/session_index_out_of_range", "flatsForNight", [ SURVEY, Object.assign( {}, OWNED, { sessionIndex: 99 } ) ] );
   pure( "flats_for_night/second_night_of_card", "flatsForNight", [ SURVEY, SURVEY.nights[SURVEY.nights.length - 1] ] );

   /* ---- the picker's constant ------------------------------------------------------------------------------ */
   value( "RECENT" );

   return cases;
};

function survey_empty()
{
   return { sessions: [], nights: [], targets: [], flatsBySession: {}, unparseable: [] };
}
