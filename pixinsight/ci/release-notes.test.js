/*
 * Tests for ci/release-notes.sh: a release's notes are its CHANGELOG
 * section, then the "Installing" block for PixInsight and Siril with the
 * version substituted, and a version without a section is refused.
 *
 * Run by ci/run-tests.js next to the other gate tests.
 * Standalone: node ci/release-notes.test.js
 */

const fs = require( "fs" );
const os = require( "os" );
const path = require( "path" );
const { spawnSync } = require( "child_process" );

const script = path.join( __dirname, "release-notes.sh" );
const changelog = [
   "# Changelog", "", "## [Unreleased]", "",
   "## [1.2.3] — 2026-01-01", "", "- **Marker change.** Something happened.", "",
   "## [1.2.2] — 2025-12-31", "", "- An older change.", ""
].join( "\n" );

function notes( version )
{
   const dir = fs.mkdtempSync( path.join( os.tmpdir(), "loom-notes-" ) );
   try
   {
      fs.writeFileSync( path.join( dir, "CHANGELOG.md" ), changelog );
      const r = spawnSync( "sh", [ script, version ], { cwd: dir, encoding: "utf8" } );
      return { status: r.status, out: r.stdout || "", err: r.stderr || "" };
   }
   finally { fs.rmSync( dir, { recursive: true, force: true } ); }
}

function run( options )
{
   const quiet = options && options.quiet;
   const ok = notes( "1.2.3" );
   const out = ok.out;
   const at = s => out.indexOf( s );
   const cases = [
      [ "a version with a section succeeds", ok.status === 0 ],
      [ "the changelog section comes first", out.trimStart().startsWith( "- **Marker change.** Something happened." ) ],
      [ "another version's section is not included", at( "An older change" ) < 0 && at( "## [1.2.3]" ) < 0 ],
      [ "the Installing block follows the section", at( "## Installing" ) > at( "Marker change" ) && at( "Marker change" ) >= 0 ],
      [ "PixInsight subsection is present", at( "### PixInsight" ) > at( "## Installing" ) ],
      [ "Siril subsection follows PixInsight", at( "### Siril (experimental)" ) > at( "### PixInsight" ) ],
      [ "the update repository is given", at( "https://fcarucci.github.io/Loom/" ) > at( "### PixInsight" ) ],
      [ "the attached zip is named after the version", out.includes( "`Loom-1.2.3.zip`" ) ],
      [ "the Siril clone command uses the version tag",
        out.includes( "git clone --branch v1.2.3 https://github.com/fcarucci/Loom" ) ],
      [ "the Siril scripts and folder are named",
        out.includes( "siril/Loom" ) && out.includes( "loom_preprocess.py" ) && out.includes( "loom_fly_through.py" ) ],
      [ "Siril is said not to be in the zip or repository", /not part of the\s+attached zip or the update repository/.test( out ) ],
      [ "README links point at the tag",
        out.includes( "https://github.com/fcarucci/Loom/blob/v1.2.3/pixinsight/README.md" ) &&
        out.includes( "https://github.com/fcarucci/Loom/blob/v1.2.3/siril/README.md" ) ],
      [ "no version placeholder is left over", !/\$v|\$\{v\}/.test( out ) ],
      [ "a version without a section fails", notes( "9.9.9" ).status !== 0 ],
      [ "and says which version is missing", /no section for 9\.9\.9/.test( notes( "9.9.9" ).err ) ],
      [ "and prints no notes", notes( "9.9.9" ).out === "" ],
      [ "a version that is only a prefix of another is not matched", notes( "1.2" ).status !== 0 ]
   ];
   let failed = 0;
   for ( const [ name, pass ] of cases )
   {
      if ( pass ) { if ( !quiet ) console.log( "ok   " + name ); }
      else { ++failed; console.error( "FAIL release-notes.test: " + name ); }
   }
   const line = "release-notes.test: " + ( cases.length - failed ) + "/" + cases.length + " passed";
   if ( failed || !quiet ) console.error( line );
   return failed === 0;
}

module.exports = { run };

if ( require.main === module )
   process.exit( run() ? 0 : 1 );
