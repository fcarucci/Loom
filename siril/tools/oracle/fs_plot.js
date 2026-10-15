"use strict";
/*
 * Golden cases for loomsiril/frames_plot.py: the plot geometry and the table's value text of
 * pixinsight/script/lib/Frames.js (pointAt, spacedLabels, plotBounds, metricColumn, displayValue; 613-724).
 *
 * Each case records the arguments it was called with and what the JS returned. Every function here is pure, so
 * every case goes through pure(), which clones the arguments before the call.
 *
 * Undefined does not survive JSON. Outputs that are undefined are written as "__undefined__"; none of these
 * functions returns undefined. Arguments are passed explicitly (null where the JS would read a missing value),
 * except spacedLabels and plotBounds called with the optional argument left out, which is a shorter list.
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

   /* A constant of Frames, by name. */
   function constant( name )
   {
      cases["const/" + name] = { value: name, out: norm( F[name] ) };
   }

   /* ---- pointAt: a click's frame by its x alone -------------------------------------------- */
   const PA = ( key, x, left, width, count ) => pure( "point_at/" + key, "pointAt", [ x, left, width, count ] );
   PA( "inside_middle", 50, 10, 100, 10 );
   PA( "left_edge", 10, 10, 100, 10 );
   PA( "right_edge", 110, 10, 100, 10 );
   PA( "outside_left_margin", 5, 10, 100, 10 );
   PA( "just_outside_left", 9.999, 10, 100, 10 );
   PA( "outside_right", 111, 10, 100, 10 );
   PA( "count_zero", 50, 10, 100, 0 );
   PA( "count_negative", 50, 10, 100, -3 );
   PA( "count_one_inside", 50, 10, 100, 1 );
   PA( "count_one_outside", 150, 10, 100, 1 );
   PA( "count_one_left_edge", 10, 10, 100, 1 );
   PA( "count_two_left_part", 30, 10, 100, 2 );
   PA( "count_two_half_rounds_up", 60, 10, 100, 2 );
   PA( "count_two_just_below_half", 59.9, 10, 100, 2 );
   PA( "count_large_inside", 333.3, 0, 1000, 1000 );
   PA( "count_large_right_edge", 1000, 0, 1000, 1000 );
   PA( "count_large_near_right", 999.9999, 0, 1000, 1000 );
   PA( "plot_width_zero", 5, 5, 0, 10 );
   PA( "plot_width_negative", 5, 5, -40, 10 );
   PA( "fractional_x", 37.25, 0, 50, 7 );
   PA( "tie_at_half_step", 1, 0, 6, 4 );
   PA( "left_offset_negative", 0, -50, 100, 3 );
   PA( "click_on_last_column_exact", 100, 0, 100, 2 );
   PA( "x_not_a_number", NaN, 0, 100, 10 );

   /* ---- spacedLabels: the first to claim a position keeps it ------------------------------ */
   const E = ( y, text ) => ( { y: y, text: text } );
   const SL = ( key, entries, gap ) => pure( "spaced_labels/" + key,
      "spacedLabels", gap === undefined ? [ entries ] : [ entries, gap ] );
   SL( "empty_default_gap", [], null );
   SL( "one_label", [ E( 40, "only" ) ], null );
   SL( "narrow_default_gap", [ E( 100, "a" ), E( 105, "b" ), E( 110, "c" ), E( 200, "d" ) ], null );
   SL( "narrow_default_gap_no_argument", [ E( 100, "a" ), E( 105, "b" ), E( 110, "c" ), E( 200, "d" ) ] );
   SL( "narrow_gap_four", [ E( 100, "a" ), E( 105, "b" ), E( 110, "c" ), E( 200, "d" ) ], 4 );
   SL( "dense_every_three", Array.from( { length: 20 }, ( _, i ) => E( i * 3, "n" + i ) ), null );
   SL( "wide_spacing_all_kept", Array.from( { length: 20 }, ( _, i ) => E( i * 30, "w" + i ) ), null );
   SL( "gap_exactly_reached", [ E( 0, "a" ), E( 12, "b" ) ], null );
   SL( "gap_just_short", [ E( 0, "a" ), E( 11.999, "b" ) ], null );
   SL( "gap_zero_keeps_duplicates", [ E( 5, "a" ), E( 5, "b" ) ], 0 );
   SL( "duplicates_default_gap", [ E( 5, "a" ), E( 5, "b" ), E( 5, "c" ) ], null );
   SL( "threshold_claims_before_extreme", [ E( 99, "threshold" ), E( 100, "max" ), E( 0, "min" ) ], null );
   SL( "negative_values", [ E( -50, "a" ), E( -45, "b" ), E( -30, "c" ) ], null );
   SL( "not_a_number_kept", [ E( NaN, "a" ), E( 5, "b" ) ], null );
   SL( "zero_entries_with_gap", [], 12 );

   /* ---- plotBounds: the vertical extent with a margin ------------------------------------- */
   const PB = ( key, values, band ) => pure( "plot_bounds/" + key,
      "plotBounds", band === undefined ? [ values ] : [ values, band ] );
   PB( "normal_spread_with_band", [ 1, 2, 3, 4, 5 ], { lo: 2, hi: 4 } );
   PB( "normal_spread_no_band", [ 1, 2, 3, 4, 5 ], null );
   PB( "normal_spread_band_omitted", [ 1, 2, 3, 4, 5 ] );
   PB( "all_equal", [ 3, 3, 3 ], null );
   PB( "all_zero", [ 0, 0 ], null );
   PB( "all_equal_negative", [ -2, -2 ], null );
   PB( "one_value", [ 10 ], null );
   PB( "one_negative_value", [ -4 ], null );
   PB( "nan_and_null_among_values", [ NaN, null, 2, 8 ], null );
   PB( "only_nan_and_null", [ NaN, null ], null );
   PB( "no_values", [], null );
   PB( "no_values_band_only", [], { lo: -1, hi: 3 } );
   PB( "band_wider_than_data", [ 1, 2 ], { lo: -10, hi: 20 } );
   PB( "band_inside_data", [ 0, 10 ], { lo: 4, hi: 6 } );
   PB( "band_missing_lo", [ 1, 9 ], { hi: 5 } );
   PB( "band_missing_hi", [ 1, 9 ], { lo: -2 } );
   PB( "band_empty_object", [ 1, 9 ], {} );
   PB( "band_null_hi", [ 1, 9 ], { lo: null, hi: 3 } );
   PB( "band_equal_to_single_value", [ 5 ], { lo: 5, hi: 5 } );
   PB( "band_zero_no_values", [], { lo: 0, hi: 0 } );
   PB( "band_nan_end", [ 1, 9 ], { lo: 0, hi: NaN } );
   PB( "negative_values", [ -5, -3, -1 ], null );
   PB( "infinite_value_ignored", [ Infinity, 1, 2 ], null );
   PB( "large_spread", [ 0, 1000000 ], null );
   PB( "fractional_values", [ 0.001, 0.002, 0.0035 ], null );
   PB( "band_only_negative", [ -8, -6 ], { lo: -20, hi: -7 } );

   /* ---- metricColumn: the table column of a metric, or null ------------------------------- */
   for ( const m of F.DISPLAY_METRICS )
      pure( "metric_column/" + m, "metricColumn", [ m ] );
   pure( "metric_column/unknown", "metricColumn", [ "bogus" ] );
   pure( "metric_column/score_is_not_a_metric", "metricColumn", [ "score" ] );
   pure( "metric_column/null", "metricColumn", [ null ] );

   /* ---- displayValue: the table's cell text ------------------------------------------------ */
   const VALUES = [ null, NaN, Infinity, -Infinity, 0, 1, 3, 17, 42.5, 123.456, 0.0345, -2.75, -150, 2.5, -2.5 ];
   for ( const m of F.DISPLAY_METRICS )
      for ( const v of VALUES )
         pure( "display_value/" + m + "/" + String( v ), "displayValue", [ m, v ] );
   pure( "display_value/unknown_metric_fraction", "displayValue", [ "bogus", 0.5 ] );

   /* ---- the score column constant ----------------------------------------------------------- */
   constant( "SCORE_COLUMN" );

   return cases;
};
