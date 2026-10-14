/*
 * Recorder for the legacy SyQon command lines and messages: lib/StepsSyqon.js:232-237 (constants),
 * :293-338 (syqonBuildArgs), :1228-1417 (starless model candidates and no-output message).
 * The file arguments are fixed placeholder paths, so the Python side passes the same strings.
 */
"use strict";

module.exports = function( js )
{
   const { Steps } = js;
   const IN = "/in/plate.fits", OUT = "/out/plate_parallax.fits", INFO = "/info/parallax.json";
   const cases = {};

   const parallaxArgs = function( opts )
   {
      return Steps.syqonBuildArgs( Object.assign( {
         inputFilePath: IN, outputFilePath: OUT, jsonInfoPath: INFO,
         correctAberration: false, starReduction: 0, sharpen: 0.0,
         tileSize: Steps.SYQON_TILE_SIZE, overlap: Steps.SYQON_OVERLAP, pad: Steps.SYQON_PAD
      }, opts ) );
   };

   cases.constants = {
      mtf_target: Steps.SYQON_MTF_TARGET,
      tile_size: Steps.SYQON_TILE_SIZE,
      overlap: Steps.SYQON_OVERLAP,
      pad: Steps.SYQON_PAD,
      timeout_ms: Steps.SYQON_TIMEOUT_MS
   };

   cases.parallax_aberration = {
      stage: "parallax", operation: "aberration", level: null, amount: null,
      args: parallaxArgs( { correctAberration: true } )
   };

   const levels = [ "none", "low", "medium", "high" ];
   for ( const level of levels )
   {
      const stars = Steps.sharpenAmountFor( "SyQon Parallax", "stars", level );
      cases[ "parallax_star_reduction_" + level ] = {
         stage: "parallax", operation: "star_reduction", level: level, amount: stars,
         args: parallaxArgs( { starReduction: stars || 0 } )
      };
      const detail = Steps.sharpenAmountFor( "SyQon Parallax", "detail", level );
      cases[ "parallax_sharpen_" + level ] = {
         stage: "parallax", operation: "sharpen", level: level, amount: detail,
         args: parallaxArgs( { sharpen: detail || 0.0 } )
      };
   }

   cases.parallax_sharpen_amounts = [ 0.4, 0.8, 1.0, 0.25, 0.333333 ].map( a => ( {
      stage: "parallax", operation: "sharpen", level: null, amount: a,
      args: parallaxArgs( { sharpen: a } )
   } ) );

   cases.starless_candidates_darwin = Steps.starlessModelCandidates(
      "/Applications/SyQonStarless.app/Contents/MacOS/SyQonStarless", "darwin" );
   cases.starless_candidates_linux = Steps.starlessModelCandidates( "/opt/syqon/SyQonStarless", "linux" );

   cases.starless_message_stderr = Steps.starlessNoOutputMessage( "plate", { stderr: "  boom: no GPU \n" } );
   cases.starless_message_no_stderr = Steps.starlessNoOutputMessage( "plate", { stderr: "" } );
   cases.starless_message_null = Steps.starlessNoOutputMessage( "plate", null );

   return cases;
};
