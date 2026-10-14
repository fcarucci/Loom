/*
 * Recorder for the progress-line parser and the progress milestone text of
 * the external process runner (lib/StepsSyqon.js:607-655). Each case stores
 * its input, so the Python test replays exactly what was recorded.
 */
"use strict";

module.exports = function( js )
{
   const { Steps } = js;

   const lines = [
      "[ 45%] Stage name (12/30)",
      "[45%] Stage (1/2)",
      "[ 0%] Starting (0/10)",
      "[100%] Done (30/30)",
      "[ 45%] Stage name",
      "[  7%]   Padded stage   ",
      "[ 45%] Stage name (12/30) extra",
      "[ 45%] Stage (12/30)  ",
      "[ 45%]",
      "[ 45%]Stage",
      "[CLI] Progress: 32%",
      "Progress: 32%",
      "[] Progress: 5%",
      "[Starless] progress:  99 %",
      "Progress: 32.5%",
      "model 42%",
      "syqon studio model 7%",
      "model-x_y 100%",
      "42%",
      "model 42",
      "model 4.2%",
      "model 42%%",
      "",
      "   ",
      null,
      "Loading libraries...",
      "﻿[ 12%] BOM stage (1/8)",
      " model 3% ",
      "[ 45%] Stage name (12/30)\r",
      "[ 45%] Stage (12/30) [ 46%] Other",
      "[ 25%] Stage (x/30)",
      "[\t45%]\tStage\t(1/2)",
      "Progress: 100%",
      "[CLI] Progress: 32 % ",
      "[ 12%] ",
      "[ 45%]Stage (1/2)"
   ];

   const parsed = lines.map( l => Steps.parseProgressLine( l ) );
   const cases = {};
   lines.forEach( ( l, i ) => {
      cases[ "parse_" + String( i ).padStart( 2, "0" ) ] = { line: l, result: parsed[i] };
   } );

   const milestones = [
      [ 25, "[ 25%] Stage (3/12)" ],
      [ 50, "[ 52%] Stage" ],
      [ 75, "Progress: 77%" ],
      [ 100, "[CLI] Progress: 100%" ],
      [ 25, "[ 30%] Stage (0/0)" ],
      [ 25, "model 26%" ]
   ];
   milestones.forEach( ( m, i ) => {
      const progress = Steps.parseProgressLine( m[1] );
      cases[ "milestone_" + i ] = { milestone: m[0], progress: progress,
                                    result: Steps.progressMilestoneText( m[0], progress ) };
   } );

   return cases;
};
