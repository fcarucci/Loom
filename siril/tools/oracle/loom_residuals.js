/*
 * Recorder for the astrometric residual verdict (Loom's lib/Steps.js:656-783:
 * RESIDUALS_WARN_PX, RESIDUALS_BAD_PX and residualVerdict), read by
 * tests/loom/test_residuals.py through loomsiril/loom/residuals.py.
 *
 * Each case name encodes the input the Python test feeds to residual_verdict.
 * JSON cannot carry undefined, NaN or a string-typed number as a bare key, so
 * the inputs are named here and rebuilt on the Python side in the same order.
 */
"use strict";

module.exports = function( js )
{
   const { Steps } = js;
   return {
      warn_px: Steps.RESIDUALS_WARN_PX,
      bad_px: Steps.RESIDUALS_BAD_PX,
      published_recursive_spline: Steps.residualVerdict( 0.091 ),
      published_old_solver_bar: Steps.residualVerdict( 0.315 ),
      just_past_bar: Steps.residualVerdict( 0.316 ),
      well_past_bar: Steps.residualVerdict( 1.5 ),
      at_matching_tolerance: Steps.residualVerdict( 3.0 ),
      beyond_matching_tolerance: Steps.residualVerdict( 12 ),
      perfect: Steps.residualVerdict( 0 ),
      nan: Steps.residualVerdict( NaN ),
      undefined: Steps.residualVerdict( undefined ),
      null: Steps.residualVerdict( null ),
      string_0_1: Steps.residualVerdict( "0.1" ),
      negative_one: Steps.residualVerdict( -1 ),
      infinity: Steps.residualVerdict( Infinity ),
      negative_infinity: Steps.residualVerdict( -Infinity ),
      wbpp_master_real: Steps.residualVerdict( 0.0190 ),
      recursive_spline_master_real: Steps.residualVerdict( 0.0157 ),
      tiny_positive: Steps.residualVerdict( 1e-9 ),
      just_below_bar: Steps.residualVerdict( 0.31499999 ),
      just_below_bad: Steps.residualVerdict( 2.9999999 )
   };
};
