/*
 * Tests for ci/check-refs.js, the static reference and packaging gate.
 *
 * The gate is only worth having if it FAILS when it should, so most of
 * these feed it a tiny synthetic tree with one defect and assert that
 * exactly that defect is named -- and a few assert that the things it must
 * leave alone (computed access, this.x, allowlisted sites, class members)
 * stay quiet.
 *
 * Run by ci/run-tests.js right after the gate itself, so a gate that has
 * stopped detecting anything cannot pass the real tree vacuously.
 * Standalone (adds a run on the real tree): node ci/check-refs.test.js
 */

const path = require( "path" );
const gate = require( "./check-refs.js" );

/*
 * A small, consistent tree: two libs, one entry point that includes both,
 * one that includes a guarded subset (as FrameSelector.js does), and a
 * suite that includes everything. It passes the gate as it stands; each
 * test breaks one thing.
 */
function baseTree()
{
   return {
      files: {
         "lib/A.js": [
            "var A = { lit: 1 };",
            "A.one = function() { return this.two + 1; };",
            "A.K = 3;",
            "" ].join( "\n" ),
         "lib/B.js": [
            "var B = {};",
            "B.two = function() { return A.one() + A.lit; };",
            "B.fromA = A.K;",
            "" ].join( "\n" ),
         "Main.js": [
            "#feature-id Main : Main",
            "#include <pjsr/StdButton.jsh>",
            "#include \"lib/A.js\"",
            "#include \"lib/B.js\"",
            "function main() { return B.two(); }",
            "main();",
            "" ].join( "\n" ),
         "Side.js": [
            "#feature-id Side : Side",
            "#ifndef LOOM_LIBS_INCLUDED",
            "#include \"lib/A.js\"",
            "#endif",
            "function Side() {}",
            "Side.prototype.run = function() { return A.one(); };",
            "Side.go = function() { return new Side().run(); };",
            "#ifndef SIDE_UNDER_TEST",
            "Side.go();",
            "#endif",
            "" ].join( "\n" ),
         "selftest.js": [
            "#feature-id T : T",
            "#include \"lib/A.js\"",
            "#include \"lib/B.js\"",
            "#define LOOM_LIBS_INCLUDED 1",
            "#define SIDE_UNDER_TEST 1",
            "#include \"Side.js\"",
            "function main()",
            "{",
            "   var LIB_FILES = [ \"A.js\", \"B.js\" ];",
            "   check( B.two(), 5 );",
            "   check( Side.go(), 4 );",
            "}",
            "main();",
            "" ].join( "\n" )
      },
      entryPoints: [ "Main.js", "Side.js", "selftest.js" ],
      testFiles: [ "selftest.js" ],
      runTestsLibs: [ "lib/A.js", "lib/B.js" ],
      repositoryEntries: [ "Main.js", "Side.js" ],
      repositoryCopiesLib: true,
      packageCopiesScript: true,
      allowlist: [],
      libFilesExceptions: []
   };
}

function edit( tree, file, from, to )
{
   const before = tree.files[file];
   tree.files[file] = before.replace( from, to );
   if ( tree.files[file] === before )
      throw new Error( "test setup: '" + from + "' not found in " + file );
   return tree;
}

function violations( tree, rule )
{
   const v = gate.check( tree ).violations;
   return rule ? v.filter( x => x.rule === rule ) : v;
}

const tests = [];
function test( name, a, b ) { tests.push( b ? Object.assign( { name, fn: b }, a ) : { name, fn: a } ); }

function expectOne( list, rule, file, line, mention )
{
   if ( list.length !== 1 )
      throw new Error( "expected exactly one " + rule + " violation, got " + list.length +
                       ": " + JSON.stringify( list ) );
   const v = list[0];
   if ( v.rule !== rule || v.file !== file || ( line !== null && v.line !== line ) ||
        !v.text.includes( mention ) )
      throw new Error( "expected " + rule + " " + file + ":" + line + " mentioning '" + mention +
                       "', got " + JSON.stringify( v ) );
}

function expectNone( list )
{
   if ( list.length )
      throw new Error( "expected no violations, got " + JSON.stringify( list ) );
}

// ---------------------------------------------------------------------------

test( "the synthetic base tree passes", () =>
{
   expectNone( violations( baseTree() ) );
} );

// Rule 1: defined members --------------------------------------------------

test( "flags an undefined member read in a lib", () =>
{
   const t = edit( baseTree(), "lib/B.js", "A.one() + A.lit", "A.onee() + A.lit" );
   expectOne( violations( t ), "undefined-member", "lib/B.js", 2, "A.onee" );
} );

test( "flags an undefined member read in selftest.js", () =>
{
   const t = edit( baseTree(), "selftest.js", "check( B.two(), 5 );", "check( B.three(), 5 );" );
   expectOne( violations( t ), "undefined-member", "selftest.js", 10, "B.three" );
} );

test( "flags a production read that only the suite defines", () =>
{
   const t = edit( baseTree(), "lib/B.js", "A.one() + A.lit", "A.one() + A.stub" );
   edit( t, "selftest.js", "function main()", "A.stub = 0;\nfunction main()" );
   expectOne( violations( t ), "undefined-member", "lib/B.js", 2, "A.stub" );
} );

test( "a suite read of a member only the suite defines is fine", () =>
{
   const t = edit( baseTree(), "selftest.js", "function main()", "A.stub = 0;\nfunction main()" );
   edit( t, "selftest.js", "check( B.two(), 5 );", "check( A.stub, 0 );" );
   expectNone( violations( t ) );
} );

test( "flags a read of a member defined only in a dead preprocessor branch", () =>
{
   const t = edit( baseTree(), "lib/A.js", "A.K = 3;", "#ifdef NEVER_DEFINED\nA.K = 3;\n#endif" );
   const v = violations( t, "undefined-member" );
   if ( !v.length || !v.every( x => x.text.includes( "A.K" ) ) )
      throw new Error( "expected A.K flagged, got " + JSON.stringify( v ) );
} );

test( "checks both cores: a member defined only on 1.9.5 is flagged for 1.9.4", () =>
{
   const t = edit( baseTree(), "lib/A.js", "A.K = 3;", "#ifeq __PI_RELEASE__ 5\nA.K = 3;\n#endif" );
   const v = violations( t, "undefined-member" );
   if ( !v.length || !v.every( x => x.text.includes( "A.K" ) && x.text.includes( "1.9.4" ) ) )
      throw new Error( "expected A.K flagged on 1.9.4 only, got " + JSON.stringify( v ) );
} );

test( "checks both cores: and one defined only on 1.9.4 is flagged for 1.9.5", () =>
{
   const t = edit( baseTree(), "lib/A.js", "A.K = 3;", "#ifoneof __PI_RELEASE__ 4\nA.K = 3;\n#endif" );
   const v = violations( t, "undefined-member" );
   if ( !v.length || !v.every( x => x.text.includes( "A.K" ) && x.text.includes( "1.9.5" ) ) )
      throw new Error( "expected A.K flagged on 1.9.5 only, got " + JSON.stringify( v ) );
} );

test( "the checked cores do not depend on the environment the gate runs in", () =>
{
   const t = edit( baseTree(), "lib/A.js", "A.K = 3;", "#ifeq __PI_RELEASE__ 5\nA.K = 3;\n#endif" );
   const saved = process.env.LOOM_TEST_CORE_RELEASE;
   process.env.LOOM_TEST_CORE_RELEASE = "5";
   try { if ( !violations( t, "undefined-member" ).length ) throw new Error( "the 1.9.4 read was missed" ); }
   finally { if ( saved === undefined ) delete process.env.LOOM_TEST_CORE_RELEASE; else process.env.LOOM_TEST_CORE_RELEASE = saved; }
} );

test( "ignores reads in a dead preprocessor branch", () =>
{
   const t = edit( baseTree(), "lib/B.js", "B.fromA = A.K;", "#ifdef NEVER_DEFINED\nB.x = A.nope;\n#endif\nB.fromA = A.K;" );
   expectNone( violations( t ) );
} );

test( "does not flag computed access", () =>
{
   const t = edit( baseTree(), "lib/B.js", "A.one() + A.lit", "A[\"nope\"] + A[k] + A.lit" );
   expectNone( violations( t ) );
} );

test( "does not flag this.x", () =>
{
   const t = edit( baseTree(), "lib/A.js", "this.two + 1", "this.nope + 1" );
   expectNone( violations( t ) );
} );

test( "does not flag an allowlisted site, and says so in the stats", () =>
{
   const t = edit( baseTree(), "lib/B.js", "A.one() + A.lit", "A.one() + A.later" );
   t.allowlist = [ { rule: "undefined-member", file: "lib/B.js", ref: "A.later", why: "test" } ];
   const r = gate.check( t );
   expectNone( r.violations );
   if ( r.allowed !== 1 )
      throw new Error( "expected 1 allowlisted site, got " + r.allowed );
} );

test( "an allowlist entry for one file does not cover another", () =>
{
   const t = edit( baseTree(), "lib/B.js", "A.one() + A.lit", "A.one() + A.later" );
   t.allowlist = [ { rule: "undefined-member", file: "selftest.js", ref: "A.later", why: "test" } ];
   const v = violations( t );
   if ( !v.some( x => x.rule === "undefined-member" && x.file === "lib/B.js" ) )
      throw new Error( "expected lib/B.js still flagged, got " + JSON.stringify( v ) );
} );

test( "flags an allowlist entry that matches nothing", () =>
{
   const t = baseTree();
   t.allowlist = [ { rule: "undefined-member", file: "lib/B.js", ref: "A.gone", why: "test" } ];
   expectOne( violations( t ), "stale-allowlist", "lib/B.js", null, "A.gone" );
} );

test( "does not flag class members, prototype members or literal keys", () =>
{
   const t = baseTree();
   t.files["lib/C.js"] = [
      "class C {",
      "   static make() { return new C(); }",
      "   get size() { return 1; }",
      "}",
      "C.extra = 2;",
      "" ].join( "\n" );
   t.runTestsLibs.push( "lib/C.js" );
   edit( t, "selftest.js", "#include \"lib/B.js\"", "#include \"lib/B.js\"\n#include \"lib/C.js\"" );
   edit( t, "selftest.js", "\"B.js\" ]", "\"B.js\", \"C.js\" ]" );
   edit( t, "selftest.js", "check( B.two(), 5 );",
         "check( B.two(), 5 ); check( C.make(), C.extra ); check( A.lit, Side.prototype.run );" );
   expectNone( violations( t ) );
   edit( t, "selftest.js", "C.make()", "C.size" );
   expectOne( violations( t ), "undefined-member", "selftest.js", null, "C.size" );
} );

test( "an assignment through a member is a read of the member", () =>
{
   const t = edit( baseTree(), "lib/B.js", "B.fromA = A.K;", "B.fromA = A.K;\nA.cfg.x = 1;" );
   expectOne( violations( t ), "undefined-member", "lib/B.js", 4, "A.cfg" );
} );

// Rule 2: per-entry-point closure ------------------------------------------

test( "flags an entry point missing an include its libs need", () =>
{
   const t = edit( baseTree(), "Main.js", "#include \"lib/A.js\"\n", "" );
   expectOne( violations( t, "missing-include" ), "missing-include", "Main.js", null, "A" );
} );

test( "flags a guarded entry point missing an include its own code needs", () =>
{
   const t = edit( baseTree(), "Side.js", "#include \"lib/A.js\"\n", "" );
   expectOne( violations( t, "missing-include" ), "missing-include", "Side.js", null, "A" );
} );

test( "a guarded entry point is checked standalone: the suite's includes do not count", () =>
{
   // B is in selftest.js's include set, not in Side.js's own guarded block.
   const t = edit( baseTree(), "Side.js", "return A.one();", "return A.one() + B.two();" );
   expectOne( violations( t, "missing-include" ), "missing-include", "Side.js", null, "B" );
} );

test( "a guarded entry point is checked under the suite too: its own includes do not count there", () =>
{
   // With LOOM_LIBS_INCLUDED defined, Side.js's include of C is skipped,
   // so the suite must include C itself.
   const t = baseTree();
   t.files["lib/C.js"] = "var C = {};\nC.k = 1;\n";
   t.runTestsLibs.push( "lib/C.js" );
   edit( t, "selftest.js", "\"B.js\" ]", "\"B.js\", \"C.js\" ]" );
   edit( t, "Side.js", "#include \"lib/A.js\"", "#include \"lib/A.js\"\n#include \"lib/C.js\"" );
   edit( t, "Side.js", "return A.one();", "return A.one() + C.k;" );
   expectOne( violations( t, "missing-include" ), "missing-include", "selftest.js", null, "C" );
} );

test( "a typeof-guarded reference can be allowlisted per site", () =>
{
   const t = edit( baseTree(), "Side.js", "function Side() {}",
                   "function Side() {}\nSide.hasB = function() { return typeof B != \"undefined\" && B.two; };" );
   expectOne( violations( t, "missing-include" ), "missing-include", "Side.js", null, "B" );
   t.allowlist = [ { rule: "missing-include", file: "Side.js", ref: "B", why: "typeof-guarded" } ];
   expectNone( violations( t ) );
} );

// Rule 3: load order -------------------------------------------------------

test( "flags a top-level cross-namespace read before its file in LIBS", () =>
{
   const t = baseTree();
   t.runTestsLibs = [ "lib/B.js", "lib/A.js" ];
   expectOne( violations( t, "load-order" ), "load-order", "lib/B.js", 3, "A" );
} );

test( "flags it in an entry point's include order too", () =>
{
   const t = edit( baseTree(), "Main.js", "#include \"lib/A.js\"\n#include \"lib/B.js\"",
                   "#include \"lib/B.js\"\n#include \"lib/A.js\"" );
   expectOne( violations( t, "load-order" ), "load-order", "lib/B.js", 3, "Main.js" );
} );

test( "a read inside a function is not a load-order problem", () =>
{
   const t = edit( baseTree(), "lib/B.js", "B.fromA = A.K;", "B.fromA = function() { return A.K; };" );
   t.runTestsLibs = [ "lib/B.js", "lib/A.js" ];
   expectNone( violations( t ) );
} );

test( "an immediately invoked function runs at load time", () =>
{
   const t = edit( baseTree(), "lib/B.js", "B.fromA = A.K;", "B.fromA = ( function() { return A.K; } )();" );
   t.runTestsLibs = [ "lib/B.js", "lib/A.js" ];
   expectOne( violations( t, "load-order" ), "load-order", "lib/B.js", 3, "A" );
} );

// Rule 4: packaging --------------------------------------------------------

function withLibC( t )
{
   t.files["lib/C.js"] = "var C = {};\nC.k = 1;\n";
   return t;
}

test( "flags a lib file absent from run-tests.js LIBS", () =>
{
   const t = withLibC( baseTree() );
   edit( t, "selftest.js", "#include \"lib/B.js\"", "#include \"lib/B.js\"\n#include \"lib/C.js\"" );
   edit( t, "selftest.js", "\"B.js\" ]", "\"B.js\", \"C.js\" ]" );
   expectOne( violations( t ), "packaging", "ci/run-tests.js", null, "lib/C.js" );
} );

test( "flags a lib file absent from selftest.js's include block", () =>
{
   const t = withLibC( baseTree() );
   t.runTestsLibs.push( "lib/C.js" );
   edit( t, "selftest.js", "\"B.js\" ]", "\"B.js\", \"C.js\" ]" );
   expectOne( violations( t ), "packaging", "selftest.js", null, "lib/C.js" );
} );

test( "flags a lib file absent from LIB_FILES unless excepted", () =>
{
   const t = withLibC( baseTree() );
   t.runTestsLibs.push( "lib/C.js" );
   edit( t, "selftest.js", "#include \"lib/B.js\"", "#include \"lib/B.js\"\n#include \"lib/C.js\"" );
   expectOne( violations( t ), "packaging", "selftest.js", null, "C.js" );
   t.libFilesExceptions = [ "C.js" ];
   expectNone( violations( t ) );
} );

test( "flags a LIB_FILES exception that is no longer needed", () =>
{
   const t = baseTree();
   t.libFilesExceptions = [ "B.js" ];
   expectOne( violations( t ), "stale-allowlist", "selftest.js", null, "B.js" );
} );

test( "flags a LIBS entry that names no file", () =>
{
   const t = baseTree();
   t.runTestsLibs.push( "lib/Gone.js" );
   expectOne( violations( t ), "packaging", "ci/run-tests.js", null, "lib/Gone.js" );
} );

test( "flags a repository entry point that does not exist", () =>
{
   const t = baseTree();
   t.repositoryEntries.push( "Gone.js" );
   const v = violations( t, "packaging" );
   if ( !v.some( x => x.file === "ci/repository.sh" && x.text.includes( "Gone.js" ) ) )
      throw new Error( "expected Gone.js flagged, got " + JSON.stringify( v ) );
} );

test( "flags an entry point the repository does not ship", () =>
{
   const t = baseTree();
   t.repositoryEntries = [ "Main.js" ];
   expectOne( violations( t ), "packaging", "ci/repository.sh", null, "Side.js" );
} );

test( "flags packaging scripts that stop copying lib/ and script/ whole", () =>
{
   const t = baseTree();
   t.repositoryCopiesLib = false;
   t.packageCopiesScript = false;
   const v = violations( t, "packaging" );
   if ( !v.some( x => x.file === "ci/repository.sh" ) || !v.some( x => x.file === "ci/package.sh" ) )
      throw new Error( "expected both scripts flagged, got " + JSON.stringify( v ) );
} );

// Output and the real tree -------------------------------------------------

test( "formats one line per violation, then a summary", () =>
{
   const t = edit( baseTree(), "lib/B.js", "A.one() + A.lit", "A.onee() + A.lit" );
   const lines = gate.format( gate.check( t ) );
   if ( lines.length !== 2 || !/^check-refs: undefined-member: lib\/B\.js:2 /.test( lines[0] ) ||
        !/^check-refs: /.test( lines[1] ) || !/1 violation/.test( lines[1] ) )
      throw new Error( "unexpected output: " + JSON.stringify( lines ) );
} );

// Standalone only: under run-tests.js the gate has just run on the real tree itself.
test( "the real tree passes", { standalone: true }, () =>
{
   const r = gate.check( gate.readTree( path.resolve( __dirname, ".." ) ) );
   expectNone( r.violations );
   if ( r.stats.reads < 1000 || r.stats.files < 19 )
      throw new Error( "the gate saw too little of the tree: " + JSON.stringify( r.stats ) );
} );

// ---------------------------------------------------------------------------

function run( options )
{
   const quiet = options && options.quiet;
   const inSuite = options && options.inSuite;
   let failed = 0, ran = 0;
   for ( const t of tests )
   {
      if ( inSuite && t.standalone ) continue;
      ++ran;
      try { t.fn(); if ( !quiet ) console.log( "ok   " + t.name ); }
      catch ( e ) { ++failed; console.error( "FAIL check-refs.test: " + t.name + "\n     " + e.message ); }
   }
   const line = "check-refs.test: " + ( ran - failed ) + "/" + ran + " passed";
   if ( failed || !quiet ) console.error( line );
   return failed === 0;
}

module.exports = { run, count: tests.length };

if ( require.main === module )
   process.exit( run() ? 0 : 1 );
