/*
 * Release preparation for the Siril port, which is released with the
 * PixInsight scripts under one tag: the dated changelog section and the
 * Python package version.
 *
 *   finalizeChangelog( text, version, date ) -> text
 *   setPythonVersion( text, version ) -> text
 *
 * Both throw an Error with a message that starts with "release:" when the
 * text cannot be prepared.
 *
 * Command line (run from the pixinsight folder by ci/release.sh):
 *   node ci/release-prep.js [--check] [--date YYYY-MM-DD] <version>
 * rewrites ../siril/CHANGELOG.md and ../siril/loomsiril/__init__.py. With
 * --check it only validates them and writes nothing. --date is the release
 * date (default: today, local time, as release.sh dates the PixInsight section).
 */

const fs = require( "fs" );
const path = require( "path" );

const HEADING = "## [Unreleased]";

function finalizeChangelog( text, version, date )
{
   const start = text.indexOf( HEADING );
   if ( start < 0 )
      throw new Error( "release: the changelog has no [Unreleased] section" );
   const i = start + HEADING.length;
   const j = text.indexOf( "\n## [", i );
   const body = text.slice( i, j >= 0 ? j : text.length );
   if ( body.trim() === "" )
      throw new Error( "release: the [Unreleased] section is empty" );
   return text.slice( 0, i ) + "\n\n## [" + version + "] — " + date + text.slice( i );
}

function setPythonVersion( text, version )
{
   const re = /^__version__ = "[^"]*"/gm;
   const found = text.match( re );
   if ( !found || found.length !== 1 )
      throw new Error( "release: expected exactly one __version__ line, found " + ( found ? found.length : 0 ) );
   return text.replace( re, () => '__version__ = "' + version + '"' );
}

function localDate( now )
{
   const d = now || new Date();
   const p = n => ( n < 10 ? "0" : "" ) + n;
   return d.getFullYear() + "-" + p( d.getMonth() + 1 ) + "-" + p( d.getDate() );
}

function main( args )
{
   let check = false, date = null, version = null;
   for ( let k = 0; k < args.length; ++k )
   {
      if ( args[ k ] === "--check" ) check = true;
      else if ( args[ k ] === "--date" ) date = args[ ++k ];
      else version = args[ k ];
   }
   if ( !/^\d+\.\d+\.\d+$/.test( version || "" ) || ( date !== null && !/^\d{4}-\d{2}-\d{2}$/.test( date ) ) )
   {
      console.error( "usage: node ci/release-prep.js [--check] [--date YYYY-MM-DD] <version>" );
      return 2;
   }
   const siril = path.join( __dirname, "..", "..", "siril" );
   const changelog = path.join( siril, "CHANGELOG.md" );
   const init = path.join( siril, "loomsiril", "__init__.py" );
   try
   {
      // Both are computed before either is written, so a refusal changes nothing.
      const c = finalizeChangelog( fs.readFileSync( changelog, "utf8" ), version, date || localDate() );
      const p = setPythonVersion( fs.readFileSync( init, "utf8" ), version );
      if ( !check )
      {
         fs.writeFileSync( changelog, c );
         fs.writeFileSync( init, p );
      }
   }
   catch ( e )
   {
      console.error( e.message );
      return 1;
   }
   return 0;
}

module.exports = { finalizeChangelog, setPythonVersion, localDate, main };

if ( require.main === module )
   process.exit( main( process.argv.slice( 2 ) ) );
