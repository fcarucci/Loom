#!/usr/bin/env node
/*
 * Records golden numbers from Loom's JavaScript (the parity oracle) into
 * tests/oracle/<name>.json. Never run in CI: the JSON is committed, so the
 * Python suite needs neither node nor a Loom checkout.
 *
 * Usage (from the repo root):
 *   LOOM_JS_DIR=/path/to/Loom/script node tools/record_oracle.js tests/oracle/ [name ...]
 *
 * Loom is used READ-ONLY: its ci/pjsr-shim.js provides the few PJSR globals
 * the libraries touch at load time, then the libraries the chosen recorders
 * need are evaluated in this process's global scope, in the order Loom's own
 * node harness (ci/run-tests.js) uses. Nothing is written into Loom.
 *
 * Two library sets:
 *   - Fly-Through recorders (any name not starting with "loom_"):
 *     lib/Util.js, lib/Fly.js, lib/Render.js, lib/Sky.js.
 *   - Loom main recorders (names "loom_*"): lib/Util.js, lib/Hasher.js,
 *     lib/Cache.js, lib/Psb.js, lib/Steps.js, lib/StepsSyqon.js,
 *     lib/StepsIcc.js, lib/Config.js, lib/Pipeline.js.
 *   - Frame Selector recorders (names "fs_*"): lib/Util.js, lib/Hasher.js,
 *     lib/Cache.js, lib/AsiairNames.js, lib/Asiair.js, lib/MasterFlat.js,
 *     lib/NightDialog.js, lib/Frames.js (the order of Loom's ci/run-tests.js).
 * Only the sets the selected recorders need are loaded.
 *
 * Recorders: every tools/oracle/<name>.js exports
 *     module.exports = function( js ) { return { case: value, ... }; };
 * where js holds the loaded namespaces: { Util, Fly, Render, Sky } for the
 * Fly-Through set, { Util, Hasher, Cache, Psb, Steps, Config, Pipeline } for
 * the Loom main set (StepsSyqon.js and StepsIcc.js add to Steps), and
 * { Util, Hasher, Cache, AsiairNames, Asiair, MasterFlat, NightDialog, Frames } for the
 * Frame Selector set. Its result is written to <out>/<name>.json
 * as { about: {...}, cases: {...} }. Each task owns its own recorder file
 * (tools/oracle/geometry.js, colour.js, ...) so branches never collide here.
 *
 * Numbers: JSON.stringify writes the shortest string that round-trips a
 * double, and Python's json reads it back to the same double. NaN and
 * +/-Infinity are written as the bare tokens NaN / Infinity / -Infinity,
 * which Python's json accepts. Typed arrays become plain arrays.
 */
"use strict";

const fs = require( "fs" );
const path = require( "path" );

function fail( msg ) { console.error( "record_oracle: " + msg ); process.exit( 1 ); }

const jsDir = process.env.LOOM_JS_DIR;
if ( !jsDir ) fail( "set LOOM_JS_DIR to Loom's script/ folder (e.g. /path/to/Loom/script)" );
const scriptDir = path.resolve( jsDir.replace( /^~(?=$|\/)/, require( "os" ).homedir() ) );
const shim = path.join( scriptDir, "..", "ci", "pjsr-shim.js" );
const preprocessorJs = path.join( scriptDir, "..", "ci", "preprocess.js" );
if ( !fs.existsSync( path.join( scriptDir, "lib", "Fly.js" ) ) ) fail( "no lib/Fly.js under " + scriptDir );
if ( !fs.existsSync( shim ) ) fail( "no ci/pjsr-shim.js next to " + scriptDir );

const outDir = process.argv[2];
if ( !outDir ) fail( "usage: LOOM_JS_DIR=... node tools/record_oracle.js OUT_DIR [name ...]" );
const only = process.argv.slice( 3 );
const recDir = path.join( __dirname, "oracle" );
const names = fs.readdirSync( recDir ).filter( f => f.endsWith( ".js" ) ).map( f => f.slice( 0, -3 ) ).sort()
   .filter( n => only.length == 0 || only.includes( n ) );
if ( only.length && names.length != only.length ) fail( "unknown recorder in: " + only.join( ", " ) );

require( shim );
global.LOOM_NODE_HARNESS = true;
// Loom's own stand-in for PJSR's preprocessor (#include/#define), as ci/run-tests.js uses it
const { preprocess } = require( preprocessorJs ).createPreprocessor( process.env );

const isLoomMain = n => n.startsWith( "loom_" );
const isFrameSelector = n => n.startsWith( "fs_" );
const FLY_LIBS = [ "lib/Util.js", "lib/Fly.js", "lib/Render.js", "lib/Sky.js" ];
const LOOM_LIBS = [ "lib/Util.js", "lib/Hasher.js", "lib/Cache.js", "lib/Psb.js", "lib/Steps.js", "lib/StepsSyqon.js",
                    "lib/StepsIcc.js", "lib/Config.js", "lib/Pipeline.js" ];
// Loom's harness load order (ci/run-tests.js), restricted to the libraries the chosen recorders need
const HARNESS_ORDER = [ "lib/Util.js", "lib/Hasher.js", "lib/Cache.js", "lib/AsiairNames.js", "lib/Asiair.js", "lib/MasterFlat.js",
                        "lib/NightDialog.js", "lib/Frames.js",
                        "lib/Psb.js", "lib/Steps.js",
                        "lib/StepsSyqon.js", "lib/StepsIcc.js", "lib/Config.js", "lib/Fly.js", "lib/Sky.js",
                        "lib/Render.js", "lib/Pipeline.js" ];
const FS_LIBS = [ "lib/Util.js", "lib/Hasher.js", "lib/Cache.js", "lib/AsiairNames.js", "lib/Asiair.js", "lib/MasterFlat.js",
                  "lib/NightDialog.js", "lib/Frames.js" ];
const wanted = new Set();
if ( names.some( n => !isLoomMain( n ) && !isFrameSelector( n ) ) ) FLY_LIBS.forEach( l => wanted.add( l ) );
if ( names.some( isLoomMain ) ) LOOM_LIBS.forEach( l => wanted.add( l ) );
if ( names.some( isFrameSelector ) ) FS_LIBS.forEach( l => wanted.add( l ) );
const LIBS = HARNESS_ORDER.filter( l => wanted.has( l ) );
for ( const lib of LIBS )
{
   const full = path.join( scriptDir, lib );
   const src = preprocess( fs.readFileSync( full, "utf8" ) ).replace( /#__FILE__/g, JSON.stringify( full ) );
   try { ( 0, eval )( src ); }
   catch ( e ) { fail( "cannot load " + lib + ": " + e.message ); }
}
const js = {};
for ( const ns of [ "Util", "Hasher", "Cache", "Psb", "Steps", "Config", "Pipeline", "Fly", "Render", "Sky",
                    "Frames", "AsiairNames", "Asiair", "MasterFlat", "NightDialog" ] )
   if ( typeof globalThis[ns] !== "undefined" ) js[ns] = globalThis[ns];

function loomVersion()
{
   try
   {
      const head = require( "child_process" ).execFileSync( "git", [ "-C", scriptDir, "describe", "--tags", "--always" ],
                                                           { encoding: "utf8", stdio: [ "ignore", "pipe", "ignore" ] } );
      return head.trim();
   }
   catch ( e ) { return "unknown"; }
}

/* Plain-data copy: typed arrays to arrays, non-finite numbers to marker strings. */
const MARK = "\u0000NUM:";
function plain( v )
{
   if ( typeof v === "number" )
      return isFinite( v ) ? v : MARK + ( isNaN( v ) ? "NaN" : v > 0 ? "Infinity" : "-Infinity" );
   if ( ArrayBuffer.isView( v ) ) return Array.from( v, plain );
   if ( Array.isArray( v ) ) return v.map( plain );
   if ( v && typeof v === "object" )
   {
      const o = {};
      for ( const k of Object.keys( v ) ) if ( typeof v[k] !== "function" ) o[k] = plain( v[k] );
      return o;
   }
   return v;
}

/* One line per case: diffs stay readable without a line per number. */
function toJson( doc )
{
   const line = v => JSON.stringify( plain( v ) );
   const body = "{\n \"about\": " + line( doc.about ) + ",\n \"cases\": {\n" +
      Object.keys( doc.cases ).map( k => "  " + JSON.stringify( k ) + ": " + line( doc.cases[k] ) ).join( ",\n" ) + "\n }\n}\n";
   return body.replace( new RegExp( "\"" + MARK.replace( "\u0000", "\\\\u0000" ) + "(NaN|Infinity|-Infinity)\"", "g" ), "$1" );
}

fs.mkdirSync( outDir, { recursive: true } );
const version = loomVersion();
for ( const name of names )
{
   const cases = require( path.join( recDir, name + ".js" ) )( js );
   const doc = { about: { recorder: "tools/oracle/" + name + ".js", loom: version, source: "Loom script/lib (read-only)" },
                 cases: cases };
   fs.writeFileSync( path.join( outDir, name + ".json" ), toJson( doc ) );
   console.log( "wrote " + path.join( outDir, name + ".json" ) + " (" + Object.keys( cases ).length + " cases)" );
}
