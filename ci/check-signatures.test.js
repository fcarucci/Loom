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

/* A tree with a committed update repository: a package zip holding the sources, and a manifest that names it. */
function makeRepo( options )
{
   options = options || {};
   const root = makeTree( true );
   fs.writeFileSync( path.join( root, "script", "lib", "Util.js" ), "var Util = {};\nUtil.LOOM_VERSION = \"1.2.3\";\n" );
   quiet( () => gate.main( root, [ "--write" ] ) );
   const stage = path.join( root, "stage", "src", "scripts", "Loom" );
   fs.mkdirSync( path.join( stage, "lib" ), { recursive: true } );
   for ( const f of gate.ENTRY_SCRIPTS )
   {
      fs.copyFileSync( path.join( root, "script", f ), path.join( stage, f ) );
      fs.copyFileSync( path.join( root, "script", f.replace( /\.js$/, ".xsgn" ) ), path.join( stage, f.replace( /\.js$/, ".xsgn" ) ) );
   }
   for ( const f of [ "Util.js", "Fly.js" ] )
      fs.copyFileSync( path.join( root, "script", "lib", f ), path.join( stage, "lib", f ) );
   fs.writeFileSync( path.join( stage, "RELEASE" ), "1.2.3\n" );
   if ( options.staleScript ) fs.appendFileSync( path.join( stage, "Loom.js" ), "// edited after packaging\n" );
   fs.mkdirSync( path.join( root, "repo" ) );
   const zip = path.join( root, "repo", "20261013-loom-1.2.3.zip" );
   require( "child_process" ).execFileSync( "zip", [ "-q", "-r", "-X", zip, "src" ], { cwd: path.join( root, "stage" ) } );
   const sha1 = require( "crypto" ).createHash( "sha1" ).update( fs.readFileSync( zip ) ).digest( "hex" );
   const signature = options.unsigned ? "" : "<Signature developerId=\"Francesco Carucci\" timestamp=\"2026-10-13T17:00:00.000Z\" encoding=\"Base64\">\n   AAAA\n</Signature>\n";
   fs.writeFileSync( path.join( root, "repo", "updates.xri" ),
      "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<xri version=\"1.0\">\n<platform os=\"all\" arch=\"noarch\" version=\"1.9.5:1.9.99\">\n" +
      "<package fileName=\"20261013-loom-1.2.3.zip\" sha1=\"" + ( options.badSha ? "0".repeat( 40 ) : sha1 ) + "\" type=\"script\" releaseDate=\"20261013\" remove=\"src/scripts/Loom\" title=\"Loom " + ( options.title || "1.2.3" ) + "\"/>\n</platform>\n</xri>\n" + signature );
   return root;
}

test( "repository: passes when signed, matching the sources and its own checksum", () =>
{
   const p = gate.repoProblems( makeRepo() );
   return p.length == 0 ? null : "expected no problems, got " + p.join( "; " );
} );

test( "repository: names an unsigned manifest, a wrong checksum and a wrong version", () =>
{
   const a = gate.repoProblems( makeRepo( { unsigned: true } ) ), b = gate.repoProblems( makeRepo( { badSha: true } ) ), c = gate.repoProblems( makeRepo( { title: "1.2.2" } ) );
   if ( !( a.length == 1 && /not signed/.test( a[0] ) ) ) return "unsigned: " + JSON.stringify( a );
   if ( !( b.length == 1 && /sha1/.test( b[0] ) ) ) return "checksum: " + JSON.stringify( b );
   return ( c.length == 1 && /not Loom 1\.2\.3/.test( c[0] ) ) ? null : "version: " + JSON.stringify( c );
} );

test( "repository: names a package that is not the committed sources", () =>
{
   const p = gate.repoProblems( makeRepo( { staleScript: true } ) );
   return ( p.length == 1 && /Loom\.js is not the committed one/.test( p[0] ) ) ? null : "got " + JSON.stringify( p );
} );

test( "repository: names a missing manifest, and --repo makes main check it", () =>
{
   const none = makeTree( true );
   if ( !/updates\.xri is missing/.test( gate.repoProblems( none )[0] || "" ) ) return "a missing manifest was not named";
   const a = quiet( () => gate.main( none, [ "--repo" ] ) ), b = quiet( () => gate.main( makeRepo(), [ "--repo" ] ) );
   return ( a === 1 && b === 0 ) ? null : "exit codes were " + a + " and " + b;
} );

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
