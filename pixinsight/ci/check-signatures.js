/*
 * The signed-release gate: the three entry scripts carry a signature file
 * (.xsgn), and the sources are the ones that were signed.
 *
 * A PixInsight script signature hashes the main script together with every
 * file it includes, so ANY later change to script/ -- the version bump
 * included -- makes the signature invalid, and PixInsight refuses a script
 * whose signature is invalid. Signing needs PixInsight and the developer's
 * keys, so it happens on the maintainer's Mac (ci/sign.sh), never in CI; CI
 * can only notice that it was forgotten. ci/sign.sh records a hash of the
 * signed sources in ci/signed-tree.sha256, and this gate recomputes it.
 *
 *    node ci/check-signatures.js           check the scripts (exit 1 when stale or missing)
 *    node ci/check-signatures.js --repo    also check the committed update repository (repo/)
 *    node ci/check-signatures.js --write   record the hash of the current sources
 *
 * The update repository (repo/updates.xri and its package zip) is built and
 * signed by ci/release.sh on the maintainer's Mac and committed; CI only
 * publishes it, after checking here that the manifest is signed, names the
 * package it ships and its checksum, is for this version, and that the
 * package holds exactly the committed sources and signatures.
 *
 * Run on tag builds only: between releases the sources move on and the
 * recorded hash is meant to be behind.
 */

const crypto = require( "crypto" );
const childProcess = require( "child_process" );
const fs = require( "fs" );
const path = require( "path" );

/* The entry points a user runs; each gets script/<name>.xsgn. selftest.js is never shipped, so is never signed. */
const ENTRY_SCRIPTS = [ "Loom.js", "FrameSelector.js", "FlyThrough.js" ];
const HASH_FILE = "ci/signed-tree.sha256";

/* Repo-relative paths of everything a signature depends on, in a fixed order. */
function sourceFiles( root )
{
   const lib = fs.readdirSync( path.join( root, "script", "lib" ) )
      .filter( f => f.endsWith( ".js" ) ).sort().map( f => "script/lib/" + f );
   return ENTRY_SCRIPTS.map( f => "script/" + f ).concat( lib );
}

/* One hex digest over the names and bytes of sourceFiles(root). */
function treeHash( root )
{
   const h = crypto.createHash( "sha256" );
   for ( const f of sourceFiles( root ) )
   {
      h.update( f + "\0" );
      h.update( crypto.createHash( "sha256" ).update( fs.readFileSync( path.join( root, f ) ) ).digest( "hex" ) );
      h.update( "\n" );
   }
   return h.digest( "hex" );
}

/* What is wrong, as sentences; empty when every entry script is signed and the sources are the signed ones. */
function problems( root )
{
   const out = [];
   for ( const f of ENTRY_SCRIPTS )
   {
      const sig = "script/" + f.replace( /\.js$/, ".xsgn" );
      if ( !fs.existsSync( path.join( root, sig ) ) )
         out.push( sig + " is missing: sign the scripts with ci/sign.sh" );
   }
   const recorded = fs.existsSync( path.join( root, HASH_FILE ) ) ? fs.readFileSync( path.join( root, HASH_FILE ), "utf8" ).trim() : "";
   if ( !recorded )
      out.push( HASH_FILE + " is missing: sign the scripts with ci/sign.sh" );
   else if ( recorded != treeHash( root ) )
      out.push( "the scripts changed after they were signed (" + HASH_FILE + " is stale): sign them again with ci/sign.sh" );
   return out;
}

const REPO_DIR = "repo";
const PACKAGE_ROOT = "src/scripts/Loom/";   // where the package deploys inside the PixInsight installation

/* A tag's attributes as an object. */
function attributes( tag )
{
   const out = {};
   tag.replace( /([A-Za-z0-9]+)="([^"]*)"/g, ( m, k, v ) => { out[k] = v; return m; } );
   return out;
}

/* What is wrong with the committed update repository, as sentences; empty when it is signed, current and holds the committed sources. */
function repoProblems( root )
{
   const xriPath = path.join( root, REPO_DIR, "updates.xri" );
   if ( !fs.existsSync( xriPath ) )
      return [ REPO_DIR + "/updates.xri is missing: build and sign it with ci/release.sh" ];
   const out = [], xri = fs.readFileSync( xriPath, "utf8" );
   const sig = /<Signature\s[^>]*developerId="([^"]+)"/.exec( xri );
   if ( !sig ) out.push( REPO_DIR + "/updates.xri is not signed: sign it with ci/release.sh" );
   const tag = /<package\s[^>]*>/.exec( xri );
   if ( !tag ) return out.concat( [ REPO_DIR + "/updates.xri names no package" ] );
   const pkg = attributes( tag[0] );
   const version = ( /^Util\.LOOM_VERSION = "([^"]*)"/m.exec( fs.readFileSync( path.join( root, "script/lib/Util.js" ), "utf8" ) ) || [] )[1];
   if ( pkg.title != "Loom " + version )
      out.push( REPO_DIR + "/updates.xri is for \"" + pkg.title + "\", not Loom " + version );
   const zipPath = path.join( root, REPO_DIR, pkg.fileName || "" );
   if ( !pkg.fileName || !fs.existsSync( zipPath ) )
      return out.concat( [ REPO_DIR + "/" + pkg.fileName + " (the package the manifest names) is missing" ] );
   const sha1 = crypto.createHash( "sha1" ).update( fs.readFileSync( zipPath ) ).digest( "hex" );
   if ( sha1 != pkg.sha1 )
      out.push( "the package's sha1 is " + sha1 + " but the signed manifest says " + pkg.sha1 );
   const expected = {};
   for ( const f of ENTRY_SCRIPTS )
   {
      expected[f] = path.join( root, "script", f );
      expected[f.replace( /\.js$/, ".xsgn" )] = path.join( root, "script", f.replace( /\.js$/, ".xsgn" ) );
   }
   for ( const f of fs.readdirSync( path.join( root, "script", "lib" ) ).filter( f => f.endsWith( ".js" ) ) )
      expected["lib/" + f] = path.join( root, "script", "lib", f );
   expected["RELEASE"] = null;
   for ( const name of Object.keys( expected ).sort() )
   {
      let packaged = null;
      try { packaged = childProcess.execFileSync( "unzip", [ "-p", zipPath, PACKAGE_ROOT + name ], { maxBuffer: 1 << 27, stdio: [ "ignore", "pipe", "ignore" ] } ); }
      catch ( e ) { out.push( "the package has no " + name ); continue; }
      const want = expected[name] ? ( fs.existsSync( expected[name] ) ? fs.readFileSync( expected[name] ) : null ) : Buffer.from( version + "\n" );
      if ( !want || !packaged.equals( want ) )
         out.push( "the package's " + name + " is not the committed one: build and sign the repository again with ci/release.sh" );
   }
   return out;
}

function main( root, args )
{
   if ( ( args || [] ).indexOf( "--write" ) >= 0 )
   {
      fs.writeFileSync( path.join( root, HASH_FILE ), treeHash( root ) + "\n" );
      console.log( "wrote " + HASH_FILE );
      return 0;
   }
   const p = problems( root ).concat( ( args || [] ).indexOf( "--repo" ) >= 0 ? repoProblems( root ) : [] );
   p.forEach( s => console.error( "check-signatures: " + s ) );
   if ( p.length == 0 ) console.log( "check-signatures: OK, " + ENTRY_SCRIPTS.length + " scripts signed and current" + ( ( args || [] ).indexOf( "--repo" ) >= 0 ? ", update repository signed and matching" : "" ) );
   return p.length ? 1 : 0;
}

module.exports = { ENTRY_SCRIPTS, HASH_FILE, REPO_DIR, sourceFiles, treeHash, problems, repoProblems, main };

if ( require.main === module )
   process.exit( main( path.resolve( __dirname, ".." ), process.argv.slice( 2 ) ) );
