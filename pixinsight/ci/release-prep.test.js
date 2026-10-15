/*
 * Tests for ci/release-prep.js: the dated changelog section and the Python
 * version line the release script sets for the Siril port.
 *
 * Run by ci/run-tests.js next to the other gate tests.
 * Standalone: node ci/release-prep.test.js
 */

const { finalizeChangelog, setPythonVersion } = require( "./release-prep.js" );

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
