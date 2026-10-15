/*
 * Tests for ci/release-prep.js: the dated changelog section and the Python
 * version line the release script sets for the Siril port.
 *
 * Run by ci/run-tests.js next to the other gate tests.
 * Standalone: node ci/release-prep.test.js
 */

const fs = require( "fs" );
const path = require( "path" );
const { spawnSync } = require( "child_process" );
const { finalizeChangelog, setPythonVersion, localDate } = require( "./release-prep.js" );

const prep = path.join( __dirname, "release-prep.js" );

// The command line against a scratch tree laid out as pixinsight/ci beside siril/.
function cli( args, changelogText, initText )
{
   const base = fs.mkdtempSync( path.join( require( "os" ).tmpdir(), "loom-prep-" ) );
   try
   {
      fs.mkdirSync( path.join( base, "pixinsight", "ci" ), { recursive: true } );
      fs.mkdirSync( path.join( base, "siril", "loomsiril" ), { recursive: true } );
      fs.copyFileSync( prep, path.join( base, "pixinsight", "ci", "release-prep.js" ) );
      fs.writeFileSync( path.join( base, "siril", "CHANGELOG.md" ), changelogText );
      fs.writeFileSync( path.join( base, "siril", "loomsiril", "__init__.py" ), initText );
      const r = spawnSync( process.execPath, [ path.join( base, "pixinsight", "ci", "release-prep.js" ) ].concat( args ),
                           { encoding: "utf8" } );
      return { status: r.status, err: r.stderr || "",
               changelog: fs.readFileSync( path.join( base, "siril", "CHANGELOG.md" ), "utf8" ),
               init: fs.readFileSync( path.join( base, "siril", "loomsiril", "__init__.py" ), "utf8" ) };
   }
   finally { fs.rmSync( base, { recursive: true, force: true } ); }
}

function throws( fn, pattern )
{
   try { fn(); }
   catch ( e ) { return pattern.test( e.message ); }
   return false;
}

function run( options )
{
   const quiet = options && options.quiet;
   const log = [ "# Changelog", "", "## [Unreleased]", "", "### Added", "", "- A change.", "",
                 "## [0.5.1] — 2026-10-15", "", "- Older.", "" ].join( "\n" );
   const out = finalizeChangelog( log, "0.6.0", "2026-11-01" );
   const py = '"""Doc."""\n\n__version__ = "0.5.1"\n__all__ = []\n';
   const cases = [
      [ "the dated section is inserted after [Unreleased]",
        out.includes( "## [Unreleased]\n\n## [0.6.0] — 2026-11-01\n\n### Added\n\n- A change." ) ],
      [ "the older section is kept after it", out.indexOf( "## [0.6.0]" ) < out.indexOf( "## [0.5.1]" ) ],
      [ "[Unreleased] stays, now empty", out.indexOf( "## [Unreleased]" ) >= 0 && out.indexOf( "## [Unreleased]" ) < out.indexOf( "## [0.6.0]" ) ],
      [ "an empty [Unreleased] section is refused",
        throws( () => finalizeChangelog( "# C\n\n## [Unreleased]\n\n## [0.5.1] — 2026-10-15\n\n- x\n", "0.6.0", "d" ), /empty/ ) ],
      [ "an empty [Unreleased] at the end of the file is refused",
        throws( () => finalizeChangelog( "# C\n\n## [Unreleased]\n\n", "0.6.0", "d" ), /empty/ ) ],
      [ "a missing [Unreleased] heading is refused",
        throws( () => finalizeChangelog( "# C\n\n## [0.5.1] — 2026-10-15\n\n- x\n", "0.6.0", "d" ), /no \[Unreleased\]/ ) ],
      [ "the version line is replaced", setPythonVersion( py, "0.6.0" ) === py.replace( "0.5.1", "0.6.0" ) ],
      [ "a file without a version line is refused",
        throws( () => setPythonVersion( '"""Doc."""\n', "0.6.0" ), /found 0/ ) ],
      [ "two version lines are refused",
        throws( () => setPythonVersion( py + '__version__ = "0.1.0"\n', "0.6.0" ), /found 2/ ) ],
      [ "an indented mention of __version__ is not a version line",
        setPythonVersion( py + '    __version__ = "x"\n', "0.6.0" ).includes( '    __version__ = "x"' ) ]
   ];
   const okLog = "# C\n\n## [Unreleased]\n\n- x\n\n## [0.5.1] \u2014 2026-10-15\n\n- y\n";
   const emptyLog = "# C\n\n## [Unreleased]\n\n## [0.5.1] \u2014 2026-10-15\n\n- y\n";
   const w = cli( [ "--date", "2026-11-01", "0.6.0" ], okLog, py );
   const c = cli( [ "--check", "--date", "2026-11-01", "0.6.0" ], okLog, py );
   const e = cli( [ "--check", "--date", "2026-11-01", "0.6.0" ], emptyLog, py );
   const nd = cli( [ "0.6.0" ], okLog, py );
   cases.push(
      [ "the command line writes both files with the given date",
        w.status === 0 && w.changelog.includes( "## [0.6.0] \u2014 2026-11-01" ) && w.init.includes( '__version__ = "0.6.0"' ) ],
      [ "--check succeeds and writes nothing", c.status === 0 && c.changelog === okLog && c.init === py ],
      [ "--check refuses an empty section and writes nothing", e.status === 1 && /empty/.test( e.err ) && e.changelog === emptyLog && e.init === py ],
      [ "without --date the section is dated with the local date", nd.status === 0 && nd.changelog.includes( "\u2014 " + localDate() ) ],
      [ "localDate uses local calendar fields", localDate( new Date( 2026, 10, 1, 23, 30 ) ) === "2026-11-01" &&
        localDate( new Date( 2026, 0, 5, 0, 10 ) ) === "2026-01-05" ],
      [ "a malformed --date is refused", cli( [ "--date", "11/01", "0.6.0" ], okLog, py ).status === 2 ] );
   let failed = 0;
   for ( const [ name, pass ] of cases )
   {
      if ( pass ) { if ( !quiet ) console.log( "ok   " + name ); }
      else { ++failed; console.error( "FAIL release-prep.test: " + name ); }
   }
   const line = "release-prep.test: " + ( cases.length - failed ) + "/" + cases.length + " passed";
   if ( failed || !quiet ) console.error( line );
   return failed === 0;
}

module.exports = { run };

if ( require.main === module )
   process.exit( run() ? 0 : 1 );
