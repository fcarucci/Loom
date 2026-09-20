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

const fs = require( "fs" );
const path = require( "path" );

const root = path.resolve( __dirname, ".." );
const scriptDir = path.join( root, "script" );

/*
 * A small stand-in for PJSR's preprocessor.
 *
 * Stripping the directives is not enough: Util.js selects its platform
 * with #ifeq on __PI_PLATFORM__ and then uses the macro it defines, so a
 * stripped file does not even load. This handles #define, #ifdef,
 * #ifndef, #ifeq, #else and #endif, and substitutes defined tokens.
 *
 * The define table is shared across files, exactly as PixInsight's is,
 * because that sharing is not a detail -- Steps.js must `#define VERSION`
 * for the bundled ImageSolver, and that define silently rewrote a
 * property access in a different file, which cost hours to find. A
 * faithful stand-in can catch that class of bug here, for free, on every
 * push. A stripping one cannot.
 *
 * Directive lines are replaced with comments rather than removed, so
 * every line number matches the real file and a stack trace points at it.
 */
const defines = { __PI_PLATFORM__: "MACOSX" };

function valueOf( token )
{
   const t = token.trim().replace( /^"|"$/g, "" );
   return ( defines[t] !== undefined ) ? String( defines[t] ).replace( /^"|"$/g, "" ) : t;
}

function preprocess( text )
{
   const out = [];
   const stack = [];           // true while the enclosing branch is live
   const live = () => stack.every( Boolean );

   for ( const line of text.split( "\n" ) )
   {
      const d = /^\s*#\s*(\w+)\s*(.*)$/.exec( line );
      if ( d )
      {
         const [ , directive, rest ] = d;
         switch ( directive )
         {
         case "ifdef":  stack.push( defines[rest.trim()] !== undefined ); break;
         case "ifndef": stack.push( defines[rest.trim()] === undefined ); break;
         case "ifeq":
         {
            const parts = rest.trim().split( /\s+/ );
            stack.push( valueOf( parts[0] ) === valueOf( parts[1] ) );
            break;
         }
         case "ifoneof":
         {
            /*
             * #ifoneof NAME A B ... -- absent from the first version of
             * this stand-in, so its body ran unguarded and redefined the
             * platform the #ifeq above had just settled. A directive that
             * is not understood must still BALANCE, or every #endif after
             * it pops someone else's branch.
             */
            const parts = rest.trim().split( /\s+/ );
            const subject = valueOf( parts[0] );
            stack.push( parts.slice( 1 ).some( p => valueOf( p ) === subject ) );
            break;
         }
         case "else":   stack[stack.length - 1] = !stack[stack.length - 1]; break;
         case "endif":  stack.pop(); break;
         case "define":
         {
            if ( live() )
            {
               const m = /^(\w+)\s*(.*)$/.exec( rest.trim() );
               if ( m )
                  defines[m[1]] = m[2].trim();
            }
            break;
         }
         default: break;      // include, engine, feature-id, feature-info
         }
         out.push( "//" + line );
         continue;
      }
      // A line inside a dead branch must not run, but must still occupy
      // its line number.
      if ( !live() )
      {
         out.push( "//" + line );
         continue;
      }
      let code = line;
      for ( const name of Object.keys( defines ) )
         if ( name !== "__PI_PLATFORM__" )
            code = code.replace( new RegExp( "\\b" + name + "\\b", "g" ), defines[name] );
      out.push( code );
   }
   return out.join( "\n" );
}

function load( file )
{
   const full = path.join( scriptDir, file );
   const src = preprocess( fs.readFileSync( full, "utf8" ) )
      .replace( /#__FILE__/g, JSON.stringify( full ) );
   return { src, full };
}

const LIBS = [ "lib/Util.js", "lib/Cache.js", "lib/Psb.js", "lib/Steps.js",
               "lib/Frames.js",
               "lib/Pipeline.js", "lib/Update.js", "lib/UI.js" ];

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
 * The suite's own console. PJSR scripts write through a global `console`
 * whose methods node does not have; map the ones the suite uses onto
 * something quiet, so a passing run is not buried in output.
 */
global.console = Object.assign( Object.create( console ), global.console_pjsr );

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
const realFailures = lines.slice( 1 ).filter( l => l.trim() && !l.includes( "NotShimmed" ) );

/*
 * An aborted run is a FAILURE even with nothing in the failure list: the
 * assertions after the exception never ran, and reporting that as success
 * is how a suite silently shrinks to nothing. The first version of this
 * harness did exactly that.
 */
const aborted = lines[0].includes( "ABORTED" );

console.error( lines[0] );
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
