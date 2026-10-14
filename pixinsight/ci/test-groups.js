/*
 * Checks the selftest's test groups against the full suite.
 *
 * selftest.js wraps every section of checks in `if ( testGroup( "name" ) )`
 * so a coder can run only the groups a change touches. A filter is only
 * safe if it cannot lie, so this asserts, under node, that:
 *
 *   - the unfiltered run is unchanged: PASS, today's result text, and (with
 *     --expect N) exactly N checks;
 *   - EVERY group passes on its own (each group is self-contained);
 *   - the per-group counts add up to the full count (every check sits in
 *     exactly one group);
 *   - a namespace prefix runs all of its groups, a prefix that stops short
 *     of a dot runs nothing, and an unknown entry is a FAILURE, so a typo
 *     cannot produce an empty green run;
 *   - two entries run the union.
 *
 * Runs ci/run-tests.js once per group, so it takes a minute or two. It is
 * deliberately not part of run-tests.js, which stays a one-second full run.
 *
 * Usage: node [-r redirect.js] ci/test-groups.js [--expect N]
 * CI runs it without --expect after both suite runs (.github/workflows/ci.yml).
 * The core release is taken from the environment as run-tests.js takes it
 * (LOOM_TEST_CORE_RELEASE=4 for the 1.9.4 build).
 */

const fs = require( "fs" );
const path = require( "path" );
const { spawnSync } = require( "child_process" );

const root = path.resolve( __dirname, ".." );
const runner = path.join( __dirname, "run-tests.js" );

/*
 * `--expect N` pins the full run's check count. Without a count it is a
 * usage error rather than Number( undefined ): NaN never equals a count,
 * so a bare --expect used to report a false "expected NaN" after the whole
 * minute of group runs. Returns { expected } (null when absent) or { error }.
 */
function parseExpect( argv )
{
   const at = argv.indexOf( "--expect" );
   if ( at < 0 )
      return { expected: null };
   const value = argv[at + 1];
   if ( value === undefined || !/^\d+$/.test( value ) )
      return { error: "--expect needs the full run's check count, e.g. --expect 2293; got " +
                      ( value === undefined ? "nothing" : JSON.stringify( value ) ) };
   return { expected: Number( value ) };
}


const problems = [];
function fail( message ) { problems.push( message ); console.error( "FAIL  " + message ); }
function ok( message ) { console.error( "ok    " + message ); }

/* One run of the node suite, filtered by `only` (undefined = the full suite). */
function run( only )
{
   const env = Object.assign( {}, process.env );
   delete env.LOOM_TEST_ONLY;
   if ( only !== undefined )
      env.LOOM_TEST_ONLY = only;
   const r = spawnSync( process.execPath, process.execArgv.concat( [ runner ] ),
                        { env, encoding: "utf8", maxBuffer: 64 << 20 } );
   const out = ( r.stdout || "" ) + ( r.stderr || "" );
   const m = /^(PASS|FAIL)( FILTERED\[([^\]]*)\])?( ABORTED after \d+ checks)? (\d+) run, (\d+) failed$/m.exec( out );
   return { status: r.status, out,
            line: m ? m[0] : null,
            pass: !!m && m[1] == "PASS" && !m[4],
            filtered: m && m[2] ? m[3] : null,
            count: m ? Number( m[5] ) : NaN,
            failed: m ? Number( m[6] ) : NaN };
}

/* Group names as written in selftest.js; `prelude` always runs and holds no checks. */
function readGroups()
{
   const source = fs.readFileSync( path.join( root, "script", "selftest.js" ), "utf8" );
   const seams = [];
   // seam lines only: `if ( testGroup( "x" ) ) {` or `} if ( testGroup( "x" ) ) {`, not prose about them
   const seamRe = /^\s*(?:\}\s*)?if\s*\(\s*testGroup\(\s*"([^"]+)"\s*\)\s*\)\s*\{\s*$/gm;
   for ( let m; ( m = seamRe.exec( source ) ); )
      seams.push( m[1] );
   return Array.from( new Set( seams ) ).filter( g => g != "prelude" );
}

// 1. the full run, unchanged
function checkFullRun( expected )
{
   const full = run( undefined );
   if ( !full.line )
      fail( "full run printed no result line:\n" + full.out );
   else if ( full.filtered !== null )
      fail( "an unfiltered run says FILTERED: " + full.line );
   else if ( !full.pass || full.status !== 0 )
      fail( "full run: " + full.line );
   else if ( expected !== null && full.count !== expected )
      fail( "full run has " + full.count + " checks, expected " + expected );
   else
      ok( "full run: " + full.line );
   return full;
}

// 2a. the names: lower-case dotted, and none a prefix of another
function checkGroupNames( groups )
{
   if ( groups.length == 0 )
      fail( "selftest.js has no testGroup( \"...\" ) seams" );
   for ( const g of groups )
      if ( !/^[a-z0-9]+(\.[a-z0-9]+)*$/.test( g ) )
         fail( "group name is not lower-case dotted: \"" + g + "\"" );
   const leaves = groups.filter( g => !groups.some( h => h.startsWith( g + "." ) ) );
   if ( leaves.length != groups.length )
      fail( "groups nest (" + groups.filter( g => !leaves.includes( g ) ).join( ", " ) +
            "); a group must not be a prefix of another" );
}

// 2b. every group on its own, and the counts add up; returns each group's count
function checkEachGroup( groups, full )
{
   let sum = 0;
   const counts = {};
   for ( const g of groups )
   {
      const r = run( g );
      counts[g] = r.count;
      // a group can be all PixInsight-only checks, which node skips: 0 here is not a failure
      if ( r.pass && r.status === 0 && r.filtered == g && r.count >= 0 )
      {
         ok( g + ": " + r.count + ( r.count == 0 ? " (PixInsight only)" : "" ) );
         sum += r.count;
      }
      else
         fail( g + ": " + ( r.line || "no result line" ) + ( r.pass ? "" : "\n" + r.out.split( "\n" ).slice( 0, 12 ).join( "\n" ) ) );
   }
   if ( groups.length == 0 )
      return counts;
   if ( sum === full.count )
      ok( "per-group counts sum to the full count: " + sum );
   else
      fail( "per-group counts sum to " + sum + ", the full run has " + full.count );
   return counts;
}

// 3. prefixes: a namespace runs all its groups; a prefix not ending at a dot is unknown
function checkPrefixes( groups, counts )
{
   const dotted = groups.filter( g => g.includes( "." ) );
   if ( !dotted.length )
      return;
   const ns = dotted[0].split( "." )[0];
   const members = groups.filter( g => g == ns || g.startsWith( ns + "." ) );
   const want = members.reduce( ( s, g ) => s + counts[g], 0 );
   const r = run( ns );
   if ( r.pass && r.filtered == ns && r.count === want )
      ok( "prefix \"" + ns + "\" runs its " + members.length + " groups: " + r.count );
   else
      fail( "prefix \"" + ns + "\" ran " + ( r.line || "nothing" ) + ", expected " + want + " checks" );
   const short = ns.slice( 0, -1 );
   if ( short && !groups.some( g => g == short || g.startsWith( short + "." ) ) )
      checkUnknown( short, "\"" + short + "\" does not match \"" + ns + ".*\"",
                    "\"" + short + "\" should be an unknown group: " );
}

// 4. an unknown entry is a failure, not an empty green run
function checkUnknown( entry, passMessage, failMessage )
{
   const r = run( entry );
   if ( !r.pass && r.status !== 0 && r.out.includes( "unknown test group: " + entry ) )
      ok( passMessage || "unknown entry fails: " + r.line );
   else
      fail( ( failMessage || "unknown entry did not fail: " ) + ( r.line || r.out.slice( 0, 400 ) ) );
}

// 5. two entries run the union
function checkUnion( groups, counts )
{
   if ( groups.length < 2 )
      return;
   const a = groups[0], b = groups[groups.length - 1];
   const r = run( a + "," + b );
   if ( r.pass && r.count === counts[a] + counts[b] && r.filtered == a + "," + b )
      ok( "\"" + a + "," + b + "\" runs both: " + r.count );
   else
      fail( "\"" + a + "," + b + "\" ran " + ( r.line || "nothing" ) + ", expected " + ( counts[a] + counts[b] ) );
}

function main( argv )
{
   const args = parseExpect( argv );
   if ( args.error )
   {
      console.error( "test-groups: " + args.error );
      process.exit( 2 );
   }
   const groups = readGroups();
   const full = checkFullRun( args.expected );
   checkGroupNames( groups );
   const counts = checkEachGroup( groups, full );
   checkPrefixes( groups, counts );
   checkUnknown( "no.such.group" );
   checkUnion( groups, counts );

   console.error( "\n" + groups.length + " groups; " +
                  ( problems.length ? problems.length + " problem(s)" : "all group checks passed" ) );
   process.exit( problems.length ? 1 : 0 );
}

module.exports = { parseExpect };

if ( require.main === module )
   main( process.argv.slice( 2 ) );
