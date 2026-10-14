/*
 * Recorder for the MTF histogram stretch: the parameter rule, the midtone transfer, and the clip-safe black point.
 *
 * The clip-safe black point builds its sample buffer through `new Rect(...)`, which the PJSR shim makes throw; a
 * plain stand-in is installed for the duration of each call and the previous global is restored afterwards.
 * The fake image's samples are recorded as { head, fill }: the pixels are head followed by `fill` repeated up to
 * width*height, so the recording stays small while the samples the function sees are exactly those.
 */
"use strict";

module.exports = function( js )
{
   const { Steps } = js;
   const out = {};

   out.constants = {
      sky_target: Steps.STRETCH_SKY_TARGET,
      stars_target: Steps.STRETCH_STARS_TARGET,
      clip_budget: Steps.STRETCH_CLIP_BUDGET
   };

   /* stretchParametersFrom: a throw is recorded as its message, so the Python side can compare the exact text. */
   function params( median, minimum, target )
   {
      try { return Steps.stretchParametersFrom( median, minimum, target ); }
      catch ( e ) { return { error: e.message }; }
   }
   out.sp_typical = params( 0.05, 0.01 );
   out.sp_target_0_10 = params( 0.05, 0.01, 0.10 );
   out.sp_negative_minimum = params( 0.05, -0.002 );
   out.sp_median_equals_minimum = params( 0.01, 0.01 );
   out.sp_median_below_minimum = params( 0.005, 0.01 );
   out.sp_very_small_x = params( 0.0100001, 0.01 );
   out.sp_loom_selftest_pair = params( 1.44898040e-3, 8.43324524e-4 );
   out.sp_loom_selftest_pair_target_0_10 = params( 1.5398e-3, 8.4332e-4, 0.10 );

   /* The midtone transfer and its application, on grids. */
   const X0 = [ 1e-6, 1e-4, 0.001, 0.01, 0.05, 0.2, 0.5, 0.9 ];
   const TARGETS = [ 0.10, 0.25, 0.5, 0.7 ];
   out.mtf_midtone_grid = [];
   for ( const x0 of X0 )
      for ( const t of TARGETS )
         out.mtf_midtone_grid.push( [ x0, t, Steps.mtfMidtoneFor( x0, t ) ] );

   const M = [ 0.01, 0.1, 0.25, 0.5, 0.8 ];
   const XS = [ 0, 1e-6, 0.001, 0.05, 0.25, 0.5, 1 ];
   out.mtf_apply_grid = [];
   for ( const m of M )
      for ( const x of XS )
         out.mtf_apply_grid.push( [ m, x, Steps.mtfApply( m, x ) ] );

   /* clipSafeBlackPoint on fake images of 200 x 150 = 30000 pixels, so the budget is floor(1e-4 * 30000) = 3. */
   const W = 200, H = 150, N = W*H;
   function clipCase( head, fill, robustMin, w = W, h = H )
   {
      const n = w*h;
      const data = head.concat( new Array( n - head.length ).fill( fill ) );
      const img = { width: w, height: h, getSamples: function( buf, rect, c ) { buf.set( data ); } };
      const oldRect = globalThis.Rect, hadRect = Object.prototype.hasOwnProperty.call( globalThis, "Rect" );
      globalThis.Rect = function( x0, y0, x1, y1 ) { this.x0 = x0; this.y0 = y0; this.x1 = x1; this.y1 = y1; };
      let result;
      try { result = Steps.clipSafeBlackPoint( img, 0, robustMin ); }
      finally
      {
         if ( hadRect ) globalThis.Rect = oldRect; else delete globalThis.Rect;
      }
      return { width: w, height: h, head: head, fill: fill, robust_min: robustMin, result: result };
   }

   /* No pixel below the robust minimum: returned unchanged. */
   out.clip_none_below = clipCase( [ 0.02, 0.03, 0.05 ], 0.04, 0.01 );
   /* Below count within the budget (3 pixels): returned unchanged. */
   out.clip_below_within_budget = clipCase( [ 0.004, 0.002, 0.009 ], 0.03, 0.01 );
   /* Five distinct values below: the highest value with at most 3 at or below it. */
   out.clip_over_budget_distinct = clipCase( [ 0.001, 0.002, 0.003, 0.004, 0.005 ], 0.03, 0.01 );
   /* Over budget with ties at the cut: stepping down past the tie at 0.003 lands on 0.001. */
   out.clip_over_budget_ties_at_cut = clipCase( [ 0.001, 0.003, 0.003, 0.003, 0.003, 0.005 ], 0.03, 0.01 );
   /* Every below value equal and over budget: 0, since those pixels are clipped to zero anyway. */
   out.clip_all_below_equal = clipCase( new Array( 10 ).fill( 0.004 ), 0.03, 0.01 );
   /* The lowest value alone is over budget: min(0, lowest). */
   out.clip_lowest_alone_over_budget = clipCase( [ 0.0007, 0.0007, 0.0007, 0.0007, 0.0007, 0.002, 0.003 ], 0.03, 0.01 );
   /* The same with a negative lowest value: min(0, lowest) is the lowest value itself. */
   out.clip_lowest_alone_negative = clipCase( [ -0.002, -0.002, -0.002, -0.002, -0.002, 0.001 ], 0.03, 0.01 );
   /* 150 x 135 = 20250 pixels: 1e-4 * n = 2.025, so the budget is 2 (floor), not 3 (ceil). Three values sit below. */
   out.clip_budget_floor_150x135 = clipCase( [ 0.002, 0.003, 0.004 ], 0.03, 0.01, 150, 135 );

   return out;
};
