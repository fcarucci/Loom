/*
 * Recorder for output naming: the output layout order (lib/Pipeline.js:2890-2908),
 * the window id scheme (lib/Util.js:342-353, lib/Pipeline.js:592-594), the project
 * base name (lib/Pipeline.js:204-208) and the file-name sanitiser used for exports
 * (lib/StepsSyqon.js:250-252).
 */
"use strict";

module.exports = function( js )
{
   const { Util, Pipeline, Steps } = js;

   const ordered = [
      [],
      [ "L" ],
      [ "zzz_custom", "L" ],
      [ "HSO_starless", "RGB_stars", "L_starless", "RGB_starless" ],
      [ "L_stars", "L_starless" ],
      [ "L_stars_high", "zz", "HOO_stars", "L", "RGB", "Zeta", "RGB_starless", "alpha", "L_stars_low" ],
      [ "HOO_stars", "HOO_starless", "HOO", "SHO_stars", "SHO", "SHO_starless", "HSO", "HSO_stars", "HSO_starless", "RGB_stars", "RGB", "RGB_starless", "L_stars_high", "L_stars_low", "L_stars", "L_starless", "L" ],
      [ "L_stars_low", "L_stars_high", "L_stars", "L_starless", "L", "RGB", "RGB_starless", "RGB_stars", "HSO", "HSO_starless", "HSO_stars", "SHO", "SHO_starless", "SHO_stars", "HOO", "HOO_starless", "HOO_stars" ],
      [ "Ha", "H", "L_1", "RGB_work", "L" ],
      [ "RGB", "RGB", "L", "L" ]
   ].map( input => ( { input, out: Pipeline.orderedOutputKeys( input ) } ) );

   const uniqueName = [
      { base: "RGB", taken: [] },
      { base: "RGB", taken: [ "RGB" ] },
      { base: "RGB", taken: [ "RGB", "RGB_1" ] },
      { base: "RGB", taken: [ "RGB_1" ] },
      { base: "RGB", taken: [ "RGB", "RGB_2" ] },
      { base: "L_stars", taken: [ "L_stars" ] },
      { base: "L_stars", taken: [ "L_stars", "L_stars_1", "L_stars_2", "L_stars_4" ] },
      { base: "SHO_starless", taken: [ "SHO" ] }
   ].map( c => ( { base: c.base, taken: c.taken,
                   out: Util.uniqueWindowId( c.base, id => c.taken.indexOf( id ) >= 0 ) } ) );

   const publishId = [
      { current: "L_stars", wanted: "L_stars", taken: [ "L_stars" ] },
      { current: "RGB_work", wanted: "RGB", taken: [ "RGB" ] },
      { current: "x", wanted: "RGB_starless", taken: [ "x" ] },
      { current: "RGB_work", wanted: "RGB", taken: [] },
      { current: "a", wanted: "RGB", taken: [ "RGB", "RGB_1" ] }
   ].map( c => ( { current: c.current, wanted: c.wanted, taken: c.taken,
                   out: Pipeline.publishId( c.current, c.wanted, id => c.taken.indexOf( id ) >= 0 ) } ) );

   const psbBaseName = [
      null,
      {},
      { projectName: "Rosette" },
      { projectName: "  Rosette  " },
      { projectName: "" },
      { projectName: "   " },
      { projectName: "Orion Nebula" }
   ].map( config => ( { config, out: Pipeline.psbBaseName( config ) } ) );

   const exportName = [
      "L_stars",
      "RGB starless",
      "M42 ° Orion",
      "a/b\\c:d*e",
      "été",
      "😀x",
      "x.tif",
      "L-1_2",
      "",
      "SHO_starless_2"
   ].map( name => ( { name, out: Steps.syqonSanitizeFileName( name ) } ) );

   return { ordered, uniqueName, publishId, psbBaseName, exportName };
};
