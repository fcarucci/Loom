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

/*
 * What a directive does to the conditional stack.
 *
 * A table rather than a switch: each entry is one rule, and adding a
 * directive is adding a line. An unknown directive falls through to the
 * default, which must still BALANCE -- #ifoneof was absent from the first
 * version of this stand-in, so its body ran unguarded and every #endif
 * after it popped someone else's branch.
 */
const DIRECTIVES = {
   ifdef:   ( rest, stack ) => stack.push( defines[rest.trim()] !== undefined ),
   ifndef:  ( rest, stack ) => stack.push( defines[rest.trim()] === undefined ),
   ifeq:    ( rest, stack ) => {
      const parts = rest.trim().split( /\s+/ );
      stack.push( valueOf( parts[0] ) === valueOf( parts[1] ) );
   },
   ifoneof: ( rest, stack ) => {
      const parts = rest.trim().split( /\s+/ );
      const subject = valueOf( parts[0] );
      stack.push( parts.slice( 1 ).some( p => valueOf( p ) === subject ) );
   },
   else:    ( rest, stack ) => { stack[stack.length-1] = !stack[stack.length-1]; },
   endif:   ( rest, stack ) => { stack.pop(); },
   define:  ( rest, stack, isLive ) => {
      if ( !isLive )
         return;
      const m = /^(\w+)\s*(.*)$/.exec( rest.trim() );
      if ( m )
         defines[m[1]] = m[2].trim();
   }
};

/* Substitute every #define into one line of code. */
function expand( line )
{
   let code = line;
   for ( const name of Object.keys( defines ) )
      if ( name !== "__PI_PLATFORM__" )
         code = code.replace( new RegExp( "\\b" + name + "\\b", "g" ), defines[name] );
   return code;
}

function preprocess( text )
{
   const out = [];
   const stack = [];           // true while the enclosing branch is live
   const live = () => stack.every( Boolean );

   /*
    * A directive may be CONTINUED onto the next line with a trailing
    * backslash -- #feature-info does exactly that at the top of
    * FrameSelector.js. Commenting out only the first line leaves the
    * continuation standing as bare JavaScript, which then fails to parse
    * for a reason that has nothing to do with the code.
    */
   let continuing = false;
   const continues = line => /\\\s*$/.test( line );

   for ( const line of text.split( "\n" ) )
   {
      if ( continuing )
      {
         continuing = continues( line );
         out.push( "//" + line );
         continue;
      }

      const d = /^\s*#\s*(\w+)\s*(.*)$/.exec( line );
      if ( d )
      {
         continuing = continues( line );
         const rule = DIRECTIVES[d[1]];
         if ( rule )
            rule( d[2], stack, live() );
         out.push( "//" + line );
         continue;
      }

      /*
       * A line inside a dead branch must not run, but must still occupy
       * its line number.
       */
      out.push( live() ? expand( line ) : "//" + line );
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
               "lib/AsiairNames.js", "lib/Asiair.js", "lib/NightDialog.js",
               "lib/Frames.js",
               "lib/Pipeline.js", "lib/Update.js", "lib/UI.js" ];

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
