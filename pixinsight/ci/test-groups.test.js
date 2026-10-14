/*
 * Tests for ci/test-groups.js's argument handling. The group checks
 * themselves take a minute of suite runs and are exercised by running the
 * script (CI does, on every push); what is tested here is that a malformed
 * --expect is refused up front instead of failing a whole run with
 * "expected NaN".
 *
 * Run by ci/run-tests.js next to the reference gate's tests.
 * Standalone: node ci/test-groups.test.js
 */

const { parseExpect } = require( "./test-groups.js" );

const cases = [
   [ "no --expect pins nothing", [], { expected: null } ],
   [ "no --expect, other arguments", [ "--verbose" ], { expected: null } ],
   [ "a count is taken", [ "--expect", "2293" ], { expected: 2293 } ],
   [ "zero is a count", [ "--expect", "0" ], { expected: 0 } ],
   [ "a bare --expect is an error", [ "--expect" ], "error" ],
   [ "an empty count is an error", [ "--expect", "" ], "error" ],
   [ "a word is an error", [ "--expect", "all" ], "error" ],
   [ "a negative count is an error", [ "--expect", "-1" ], "error" ],
   [ "a fraction is an error", [ "--expect", "2293.5" ], "error" ]
];

function run( options )
{
   const quiet = options && options.quiet;
   let failed = 0;
   for ( const [ name, argv, want ] of cases )
   {
      const got = parseExpect( argv );
      const pass = want === "error"
         ? typeof got.error == "string" && /--expect needs/.test( got.error ) && !( "expected" in got )
         : JSON.stringify( got ) === JSON.stringify( want );
      if ( pass )
      {
         if ( !quiet ) console.log( "ok   " + name );
      }
      else
      {
         ++failed;
         console.error( "FAIL test-groups.test: " + name + "\n     got " + JSON.stringify( got ) );
      }
   }
   const line = "test-groups.test: " + ( cases.length - failed ) + "/" + cases.length + " passed";
   if ( failed || !quiet ) console.error( line );
   return failed === 0;
}

module.exports = { run, count: cases.length };

if ( require.main === module )
   process.exit( run() ? 0 : 1 );
