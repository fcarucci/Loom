/*
 * Tests for ci/check-signatures.js, the signed-release gate. Each test builds
 * a tiny tree in a temporary folder, breaks one thing, and asserts the gate
 * names exactly that. Run by ci/run-tests.js; standalone: node ci/check-signatures.test.js
 */

const fs = require( "fs" );
const os = require( "os" );
const path = require( "path" );
const gate = require( "./check-signatures.js" );

const tests = [];
function test( name, fn ) { tests.push( { name, fn } ); }

function makeTree( signed )
{
   const root = fs.mkdtempSync( path.join( os.tmpdir(), "loom-sig-" ) );
   fs.mkdirSync( path.join( root, "script", "lib" ), { recursive: true } );
   fs.mkdirSync( path.join( root, "ci" ) );
   for ( const f of gate.ENTRY_SCRIPTS )
      fs.writeFileSync( path.join( root, "script", f ), "#feature-id X : X\n" );
   fs.writeFileSync( path.join( root, "script", "lib", "Util.js" ), "var Util = {};\n" );
   fs.writeFileSync( path.join( root, "script", "lib", "Fly.js" ), "var Fly = {};\n" );
   fs.writeFileSync( path.join( root, "script", "selftest.js" ), "#feature-id T : T\n" );
   if ( signed )
   {
      for ( const f of gate.ENTRY_SCRIPTS )
         fs.writeFileSync( path.join( root, "script", f.replace( /\.js$/, ".xsgn" ) ), "<xsgn/>\n" );
      quiet( () => gate.main( root, [ "--write" ] ) );
   }
   return root;
}

function quiet( fn )
{
   const log = console.log, err = console.error;
   console.log = console.error = function() {};
   try { return fn(); } finally { console.log = log; console.error = err; }
}

test( "passes when every entry script is signed and the sources are the signed ones", () =>
{
   const root = makeTree( true );
   return gate.problems( root ).length == 0 ? null : "expected no problems, got " + gate.problems( root ).join( "; " );
} );

test( "names a missing signature file", () =>
{
   const root = makeTree( true );
   fs.unlinkSync( path.join( root, "script", "FlyThrough.xsgn" ) );
   const p = gate.problems( root );
   return ( p.length == 1 && /FlyThrough\.xsgn is missing/.test( p[0] ) ) ? null : "got " + JSON.stringify( p );
} );

test( "names sources changed after signing: an entry script, then a library", () =>
{
   const root = makeTree( true );
   fs.appendFileSync( path.join( root, "script", "Loom.js" ), "// edit\n" );
   if ( !/changed after they were signed/.test( gate.problems( root )[0] || "" ) ) return "an entry script edit was not noticed";
   const root2 = makeTree( true );
   fs.appendFileSync( path.join( root2, "script", "lib", "Util.js" ), "Util.v = 1;\n" );
   return /changed after they were signed/.test( gate.problems( root2 )[0] || "" ) ? null : "a library edit was not noticed";
} );

test( "ignores selftest.js and the signature files themselves", () =>
{
   const root = makeTree( true );
   fs.appendFileSync( path.join( root, "script", "selftest.js" ), "// edit\n" );
   fs.writeFileSync( path.join( root, "script", "Loom.xsgn" ), "<xsgn>re-signed</xsgn>\n" );
   return gate.problems( root ).length == 0 ? null : "unsigned files must not matter: " + gate.problems( root ).join( "; " );
} );

test( "names a missing hash record, and a new library file counts as a change", () =>
{
   const root = makeTree( true );
   fs.writeFileSync( path.join( root, "script", "lib", "New.js" ), "var New = {};\n" );
   if ( !/stale/.test( gate.problems( root )[0] || "" ) ) return "a new lib file was not noticed";
   fs.unlinkSync( path.join( root, gate.HASH_FILE ) );
   return /signed-tree\.sha256 is missing/.test( gate.problems( root )[0] || "" ) ? null : "a missing record was not named";
} );

test( "main exits 1 on a problem and 0 when clean", () =>
{
   const bad = makeTree( false ), good = makeTree( true );
   const a = quiet( () => gate.main( bad, [] ) ), b = quiet( () => gate.main( good, [] ) );
   return ( a === 1 && b === 0 ) ? null : "exit codes were " + a + " and " + b;
} );

function run( options )
{
   options = options || {};
   let failed = 0;
   for ( const t of tests )
   {
      let why;
      try { why = t.fn(); } catch ( e ) { why = "threw " + e.message; }
      if ( why ) { ++failed; console.error( "FAIL check-signatures: " + t.name + ": " + why ); }
      else if ( !options.quiet ) console.log( "ok   check-signatures: " + t.name );
   }
   return failed == 0;
}

module.exports = { run, count: tests.length };

if ( require.main === module )
   process.exit( run() ? 0 : 1 );
