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
 *    node ci/check-signatures.js           check (exit 1 when stale or missing)
 *    node ci/check-signatures.js --write   record the hash of the current sources
 *
 * Run on tag builds only: between releases the sources move on and the
 * recorded hash is meant to be behind.
 */

const crypto = require( "crypto" );
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

function main( root, args )
{
   if ( ( args || [] ).indexOf( "--write" ) >= 0 )
   {
      fs.writeFileSync( path.join( root, HASH_FILE ), treeHash( root ) + "\n" );
      console.log( "wrote " + HASH_FILE );
      return 0;
   }
   const p = problems( root );
   p.forEach( s => console.error( "check-signatures: " + s ) );
   if ( p.length == 0 ) console.log( "check-signatures: OK, " + ENTRY_SCRIPTS.length + " scripts signed and current" );
   return p.length ? 1 : 0;
}

module.exports = { ENTRY_SCRIPTS, HASH_FILE, sourceFiles, treeHash, problems, main };

if ( require.main === module )
   process.exit( main( path.resolve( __dirname, ".." ), process.argv.slice( 2 ) ) );
