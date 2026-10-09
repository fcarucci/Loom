/*
 * Runs Loom's selftest under node, without PixInsight.
 *
 * Most of what the suite asserts is arithmetic, string handling, cache
 * keys, shell generation and document structure -- none of which needs an
 * image processing application. A handful of assertions genuinely do
 * (constructing a dialog, opening an ImageWindow); those raise NotShimmed
 * and are reported separately rather than being faked into passing.
 *
 * Exit status is 0 only if every runnable assertion passed.
 *
 * Usage: node ci/run-tests.js
 */

require( "./pjsr-shim.js" );

/*
 * Tells the suite it is not inside PixInsight, so the handful of
 * assertions that need the real thing skip rather than fail. They are
 * still run by the full suite in PixInsight; see IN_PIXINSIGHT there.
 */
global.LOOM_NODE_HARNESS = true;

/*
 * Run only some test groups: LOOM_TEST_ONLY=steps,cache (see testGroup in
 * selftest.js). Unset runs the full suite, as always; the result line of a
 * filtered run says FILTERED, so it cannot be mistaken for a full pass.
 */
if ( process.env.LOOM_TEST_ONLY !== undefined )
   global.LOOM_TEST_ONLY = process.env.LOOM_TEST_ONLY;

const fs = require( "fs" );
const path = require( "path" );

const root = path.resolve( __dirname, ".." );
const scriptDir = path.join( root, "script" );

/*
 * A faithful stand-in for PJSR's preprocessor lives in ci/preprocess.js,
 * shared with the reference gate. This instance's define table is shared
 * across every file loaded below, exactly as PixInsight's is.
 */
const { preprocess, defines } = require( "./preprocess.js" ).createPreprocessor( process.env );

/*
 * The static reference gate, before anything loads: every NS.member read
 * defined somewhere, every entry point's includes complete, load order and
 * packaging lists consistent (see ci/check-refs.js). Node runs a fraction
 * of the code; this reads all of it. The gate's own tests run right after,
 * so a gate that has stopped detecting anything cannot pass vacuously.
 */
{
   const gatePassed = require( "./check-refs.js" ).main( root ) === 0;
   const gateTestsPassed = require( "./check-refs.test.js" ).run( { quiet: true, inSuite: true } );
   const groupArgsPassed = require( "./test-groups.test.js" ).run( { quiet: true } );
   if ( !gatePassed || !gateTestsPassed || !groupArgsPassed )
      process.exit( 1 );
}

function load( file )
{
   const full = path.join( scriptDir, file );
   const src = preprocess( fs.readFileSync( full, "utf8" ) )
      .replace( /#__FILE__/g, JSON.stringify( full ) );
   return { src, full };
}

const LIBS = [ "lib/Util.js", "lib/Hasher.js", "lib/Cache.js", "lib/Psb.js", "lib/Steps.js",
               "lib/StepsSyqon.js", "lib/StepsIcc.js",
               "lib/Config.js",
               "lib/AsiairNames.js", "lib/Asiair.js", "lib/MasterFlat.js", "lib/NightDialog.js",
               "lib/Frames.js", "lib/Fly.js", "lib/Solve.js", "lib/Sky.js",
               "lib/Render.js", "lib/Pipeline.js", "lib/Update.js", "lib/UI.js" ];

/*
 * Files that are NOT loaded above, but must still PARSE.
 *
 * FrameSelector.js is the second entry point. It is deliberately not in
 * LIBS -- it ends by calling main(), and the suite includes it itself --
 * so nothing here ever parsed it. A structural edit once left it at 7.6
 * MILLION lines, completely unloadable, and this suite reported "every
 * runnable assertion passed" because the file it had broken was invisible
 * to it. PixInsight then refused the script with no error anyone could
 * see, which is a slow and confusing way to learn about a typo.
 *
 * Parsing is not running: these are compiled and thrown away. That is
 * enough to catch the failure mode that actually happened.
 */
const PARSE_ONLY = [ "FrameSelector.js", "Loom.js", "selftest.js" ];

for ( const file of PARSE_ONLY )
{
   let src;
   try { ( { src } = load( file ) ); }
   catch ( e )
   {
      console.error( "CANNOT READ " + file + ": " + e.message );
      process.exit( 1 );
   }
   try { new Function( src ); }
   catch ( e )
   {
      /*
       * `new Function` reports the message but not the place. Narrowing by
       * prefix finds the first line that will not parse, which is what
       * anyone reading this actually needs.
       */
      const lines = src.split( "\n" );
      let at = -1;
      for ( let n = 1; n <= lines.length; ++n )
      {
         try { new Function( lines.slice( 0, n ).join( "\n" ) ); }
         catch ( inner )
         {
            if ( inner.message === e.message ) { at = n; break; }
         }
      }
      console.error( "SYNTAX ERROR in " + file + ": " + e.message );
      console.error( "  " + lines.length + " lines after preprocessing" );
      if ( at > 0 )
      {
         console.error( "  first unparseable at line " + at + ":" );
         for ( let i = Math.max( 0, at-4 ); i < Math.min( lines.length, at+1 ); ++i )
            console.error( "    " + String( i+1 ).padStart( 5 ) + "  " + lines[i] );
      }
      process.exit( 1 );
   }
}

let loaded = 0;
for ( const lib of LIBS )
{
   const { src, full } = load( lib );
   try { ( 0, eval )( src ); ++loaded; }
   catch ( e )
   {
      console.error( "FAILED TO LOAD " + lib + ": " + e.message );
      process.exit( 1 );
   }
}

/*
 * Loom.js, for the config functions the suite tests (defaultConfig,
 * loadConfig, saveConfig). selftest.js #includes it, which this loader
 * does not follow, so it is evaluated here under the two defines that
 * selftest.js sets before its include: LOOM_LIBS_INCLUDED keeps it from
 * re-running the libraries, LOOM_UNDER_TEST from calling main().
 */
defines.LOOM_LIBS_INCLUDED = "1";
defines.LOOM_UNDER_TEST = "1";
{
   const { src } = load( "Loom.js" );
   try { ( 0, eval )( src ); }
   catch ( e )
   {
      console.error( "FAILED TO LOAD Loom.js: " + e.message );
      process.exit( 1 );
   }
}

/*
 * The suite's own console. PJSR scripts write through a global `console`
 * whose methods node does not have; map the ones the suite uses onto
 * something quiet, so a passing run is not buried in output.
 */
global.console = Object.assign( Object.create( console ), global.console_pjsr );

/*
 * Every entry point must PARSE. PixInsight discards a script that does
 * not, silently -- no dialog, no result file -- and the suite includes
 * FrameSelector.js, so one stray brace there once stopped the whole
 * self-test from starting while every node assertion still passed.
 * Compiled with new Function, never run: FrameSelector.js and Loom.js end
 * by opening their dialogs.
 */
for ( const entry of [ "FrameSelector.js", "FlyThrough.js", "Loom.js", "selftest.js" ] )
{
   try { new Function( load( entry ).src ); }
   catch ( e )
   {
      console.error( entry + " does not parse: " + e.message );
      process.exit( 1 );
   }
}

const suite = load( "selftest.js" );

/*
 * The suite ends with main(), which runs everything and writes a result
 * file. Under node the file is written to the same place, so the output
 * of a CI run can be read exactly like a local one.
 */
try
{
   ( 0, eval )( suite.src );
}
catch ( e )
{
   console.error( "the suite threw: " + ( e && e.message ? e.message : e ) );
   if ( e && e.stack ) console.error( e.stack );
   process.exit( 1 );
}

const resultFile = "/tmp/agent-scratch/lhso-selftest.txt";
let summary = "";
try { summary = fs.readFileSync( resultFile, "utf8" ).trim(); }
catch ( e )
{
   console.error( "the suite produced no result file" );
   process.exit( 1 );
}

/*
 * Assertions that need PixInsight raise NotShimmed. They are counted and
 * named, never silently dropped: a suite that quietly shrinks is how
 * coverage evaporates.
 */
const lines = summary.split( "\n" );
const notShimmed = lines.filter( l => l.includes( "NotShimmed" ) );
// a filtered run names the groups it ran on the line after the result
const filtered = lines[0].includes( " FILTERED[" );
const realFailures = lines.slice( filtered ? 2 : 1 ).filter( l => l.trim() && !l.includes( "NotShimmed" ) );

/*
 * An aborted run is a FAILURE even with nothing in the failure list: the
 * assertions after the exception never ran, and reporting that as success
 * is how a suite silently shrinks to nothing. The first version of this
 * harness did exactly that.
 */
const aborted = lines[0].includes( "ABORTED" );

console.error( lines[0] );
if ( filtered )
   console.error( lines[1] );
if ( notShimmed.length )
{
   console.error( "\nneeds PixInsight, not run here (" + notShimmed.length + "):" );
   for ( const l of notShimmed )
      console.error( "  " + l.split( ":" )[0] );
}
if ( realFailures.length )
{
   console.error( "\nfailures:" );
   for ( const l of realFailures )
      console.error( "  " + l );
   process.exit( 1 );
}
if ( aborted )
{
   console.error( "\nthe run was cut short; the assertions after the " +
                  "exception never ran" );
   process.exit( 1 );
}
console.error( "\nloaded " + loaded + " libraries; every runnable assertion passed" );
process.exit( 0 );
