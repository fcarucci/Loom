"use strict";
/*
 * Golden cases for loomsiril/master_flat_run.py: the checks, the deletion rules and the job runner of
 * pixinsight/script/lib/MasterFlat.js, lines 556-976 (verifyMaster, cleanPath, mayDelete, execute, report).
 *
 * The plan of each execute case is built here with MasterFlat.buildPlan (the JS side), then run by MasterFlat.execute
 * through a scripted fake engine. The fake logs every call it receives (in order, with its arguments and its answer)
 * and answers from the case's table, so the Python replay builds the same fake from the recorded table and must
 * produce the same call log and the same result.
 *
 * Fake table (recorded in each execute case as `fake`):
 *   fs              the paths that exist at the start
 *   masterDark      { groupKey: { ok, path, reason } }  answers for the raw dark groups of the plan
 *   calibrate       { firstPath: { ok, count, reason, throw } }  keyed by the first flat of a part
 *   integrate       { outPath: { ok, reason, throw, leaves } }  leaves: the partial stays after the failure
 *   verify          { partialPath: problem or null }
 *   publish         { finalPath: true | false | "throw" }  (false also when finalPath exists, as the JS contract)
 *   resolve         null (no resolve method) or { path: realPath | "throw" } (unlisted paths resolve to themselves)
 *   removeFail      [ paths whose remove() answers false ]
 *   removeThrow     { path: "gone" | "stays" }  remove() throws; "gone" deletes it first
 *   tempDirThrow    message of the error tempDir() throws, or null
 *   removePartials  true when the engine has removePartials
 *   stopAfter       shouldStop() answers progress > stopAfter (null: never stops)
 * Engine errors are thrown as Error( message ); a cancel is Error( Util.CANCELLED ). Undefined does not survive JSON:
 * an output that is undefined is written as "__undefined__" and an object key whose value is undefined is dropped.
 */
module.exports = function( js )
{
   const M = js.MasterFlat;
   const cases = {};
   const has = ( o, k ) => Object.prototype.hasOwnProperty.call( o, k );
   const DEST = "/work/Flat";
   const CARD = "/card";

   function norm( v )
   {
      if ( v === undefined ) return "__undefined__";
      if ( Array.isArray( v ) ) return v.map( norm );
      if ( v && typeof v === "object" )
      {
         const o = {};
         for ( const k of Object.keys( v ) ) if ( v[k] !== undefined ) o[k] = norm( v[k] );
         return o;
      }
      return v;
   }

   /* A described flat as the plan receives it (the XISF copy in the Flat folder). */
   function flat( filter, n, exposure, binning, dir )
   {
      const name = filter + "_" + n + ".xisf";
      const path = ( dir || DEST ) + "/" + name;
      return { path: path, name: name, imagetyp: "Flat", filter: filter, exposure: exposure == null ? 2 : exposure,
               binning: binning == null ? 1 : binning, gain: 120, temp: -10, raw: path };
   }

   /* A described raw dark (not yet grouped: buildPlan groups it by exposure, binning, gain and temperature). */
   function dark( n, exposure )
   {
      return { path: "/work/Dark/d" + n + ".xisf", name: "d" + n + ".xisf", imagetyp: "Dark", filter: null,
               exposure: exposure == null ? 2 : exposure, binning: 1, gain: 120, temp: -10 };
   }

   /* A master dark on the card, as darksOf gives it. */
   function masterDark( n )
   {
      return { path: "/d/m" + n + ".xisf", name: "m" + n + ".xisf", imagetyp: "Master Dark", filter: null,
               exposure: 2, binning: 1, gain: 120, temp: -10, kind: "master" };
   }

   function plan( flats, darks, where )
   {
      return M.buildPlan( structuredClone( flats ), structuredClone( darks ), DEST, where === undefined ? null : where );
   }

   /* The raw groups of a plan, each answered by `entry` (a masterDark result). */
   function darkTable( jobs, entry )
   {
      const t = {};
      for ( const j of jobs ) for ( const p of j.parts ) if ( p.dark.kind == "raw" ) t[p.dark.key] = entry;
      return t;
   }

   /* Build the fake engine from its table. Every call is logged; the answers come from the table. */
   function fakeEngine( t, log, progress )
   {
      const fs = new Set( t.fs || [] );
      const eng = {};
      const removeFail = t.removeFail || [];
      const removeThrow = t.removeThrow || {};

      eng.exists = function( p ) { const r = fs.has( p ); log.push( [ "exists", p, r ] ); return r; };

      eng.remove = function( p )
      {
         if ( has( removeThrow, p ) )
         {
            log.push( [ "remove", p, "throw" ] );
            if ( removeThrow[p] == "gone" ) fs.delete( p );
            throw new Error( "locked: " + p );
         }
         const r = removeFail.indexOf( p ) < 0;
         if ( r ) fs.delete( p );
         log.push( [ "remove", p, r ] );
         return r;
      };

      eng.removeDir = function( d ) { log.push( [ "removeDir", d ] ); return true; };

      eng.tempDir = function( job )
      {
         if ( t.tempDirThrow != null )
         {
            log.push( [ "tempDir", job.name, "throw" ] );
            throw new Error( t.tempDirThrow );
         }
         const d = "/work/tmp/" + job.name;
         log.push( [ "tempDir", job.name, d ] );
         return d;
      };

      eng.masterDark = function( group )
      {
         const r = has( t.masterDark, group.key ) ? t.masterDark[group.key] : { ok: false, reason: "not in table" };
         log.push( [ "masterDark", group.key ] );
         if ( r.throw ) throw new Error( r.throw );
         return { ok: r.ok, path: r.path, reason: r.reason };
      };

      eng.calibrate = function( paths, darkPath, outDir )
      {
         log.push( [ "calibrate", paths, darkPath, outDir ] );
         const r = has( t.calibrate || {}, paths[0] ) ? t.calibrate[paths[0]] : { ok: true };
         if ( r.throw ) throw new Error( r.throw );
         if ( !r.ok ) return { ok: false, reason: r.reason };
         const n = r.count == null ? paths.length : r.count;
         const out = paths.slice( 0, n ).map( function( p, i ) { return outDir + "/cal_" + i + ".xisf"; } );
         return { ok: true, paths: out };
      };

      eng.integrate = function( paths, spec, outPath, job )
      {
         log.push( [ "integrate", paths, spec, outPath, job.name ] );
         const r = has( t.integrate || {}, outPath ) ? t.integrate[outPath] : { ok: true };
         if ( r.throw ) throw new Error( r.throw );
         if ( r.ok ) fs.add( outPath );
         else if ( r.leaves ) fs.add( outPath );
         return r.ok ? { ok: true } : { ok: false, reason: r.reason };
      };

      eng.verify = function( partialPath, job )
      {
         log.push( [ "verify", partialPath ] );
         return has( t.verify || {}, partialPath ) ? t.verify[partialPath] : null;
      };

      eng.publish = function( partialPath, finalPath )
      {
         log.push( [ "publish", partialPath, finalPath ] );
         const mode = has( t.publish || {}, finalPath ) ? t.publish[finalPath] : true;
         if ( mode === "throw" ) throw new Error( "disk gone" );
         if ( fs.has( finalPath ) || mode !== true ) return false;
         fs.delete( partialPath );
         fs.add( finalPath );
         return true;
      };

      if ( t.resolve != null )
         eng.resolve = function( p )
         {
            if ( has( t.resolve, p ) && t.resolve[p] === "throw" ) throw new Error( "cannot resolve " + p );
            return has( t.resolve, p ) ? t.resolve[p] : p;
         };

      if ( t.removePartials === true )
         eng.removePartials = function( d ) { log.push( [ "removePartials", d ] ); };

      return eng;
   }

   /* Run a plan through the fake: the recorded case has the jobs, the options and the table. */
   function executeCase( key, jobs, fake, opts )
   {
      const log = [];
      let progress = 0;
      const engine = fakeEngine( fake, log );
      const o = { destFlatDir: opts.destFlatDir, cardRoot: opts.cardRoot };
      o.onProgress = function( done, total, text )
      {
         progress = done + 1;
         log.push( [ "onProgress", done, total, text ] );
      };
      if ( fake.stopAfter != null )
         o.shouldStop = function()
         {
            const r = progress > fake.stopAfter;
            log.push( [ "shouldStop", r ] );
            return r;
         };
      const out = M.execute( structuredClone( jobs ), engine, o );
      cases[key] = { fn: "execute", args: { jobs: norm( jobs ), opts: opts, fake: fake }, out: norm( out ),
                     report: M.report( out ), log: norm( log ) };
   }

   /* The four plans most cases share: Ha and OIII, two flats each, one raw dark group. */
   const HA = [ flat( "Ha", 1 ), flat( "Ha", 2 ) ];
   const OIII = [ flat( "OIII", 1 ), flat( "OIII", 2 ) ];
   const DARKS = [ dark( 1 ), dark( 2 ) ];
   const jobsHa = plan( HA.concat( OIII ), DARKS, "on the ASIAIR card" );
   const jobsBoth = plan( HA.concat( OIII ), DARKS, null );
   const ok = { ok: true, path: "/cache/master-darks/masterDark_raw.xisf", reason: null };

   function base( extra )
   {
      return Object.assign( { fs: HA.concat( OIII ).map( function( f ) { return f.path; } ),
                              masterDark: darkTable( jobsBoth, ok ), resolve: {} }, extra || {} );
   }
   const OPTS = { destFlatDir: DEST, cardRoot: CARD };

   /* ---- execute: the happy path and the dark answers ---- */
   executeCase( "run/both_filters_made", jobsBoth, base(), OPTS );
   executeCase( "run/master_dark_built", jobsBoth,
      base( { masterDark: darkTable( jobsBoth, { ok: true, path: "/work/Dark/masterDark_built.xisf", reason: null } ) } ), OPTS );
   executeCase( "run/master_dark_failing", jobsBoth,
      base( { masterDark: darkTable( jobsBoth, { ok: false, path: null, reason: "could not write the dark" } ) } ), OPTS );
   executeCase( "run/master_dark_throws", jobsBoth,
      base( { masterDark: darkTable( jobsBoth, { ok: false, throw: "dark store offline" } ) } ), OPTS );

   /* ---- execute: calibration, integration, verification, publishing ---- */
   executeCase( "run/calibrate_fails_for_one_filter", jobsBoth,
      base( { calibrate: { "/work/Flat/OIII_1.xisf": { ok: false, reason: "no matching light" } } } ), OPTS );
   executeCase( "run/calibrate_returns_fewer", jobsBoth,
      base( { calibrate: { "/work/Flat/OIII_1.xisf": { ok: true, count: 1 } } } ), OPTS );
   executeCase( "run/calibrate_throws_cancel", jobsBoth,
      base( { calibrate: { "/work/Flat/Ha_1.xisf": { throw: js.Util.CANCELLED } } } ), OPTS );
   executeCase( "run/integrate_fails", jobsBoth,
      base( { integrate: { "/work/Flat/.partial_masterFlat_Ha.xisf": { ok: false, reason: "out of memory" } } } ), OPTS );
   executeCase( "run/integrate_fails_partial_left", jobsBoth,
      base( { integrate: { "/work/Flat/.partial_masterFlat_Ha.xisf": { ok: false, reason: "disk full", leaves: true } } } ), OPTS );
   executeCase( "run/integrate_throws_cancel", jobsBoth,
      base( { integrate: { "/work/Flat/.partial_masterFlat_OIII.xisf": { throw: js.Util.CANCELLED } } } ), OPTS );
   executeCase( "run/integrate_throws_error", jobsBoth,
      base( { integrate: { "/work/Flat/.partial_masterFlat_Ha.xisf": { throw: "bad FITS" } } } ), OPTS );
   executeCase( "run/verify_problem_one_master", jobsBoth,
      base( { verify: { "/work/Flat/.partial_masterFlat_OIII.xisf": "geometry changed" } } ), OPTS );
   executeCase( "run/publish_refused", jobsBoth,
      base( { publish: { "/work/Flat/masterFlat_Ha.xisf": false } } ), OPTS );
   executeCase( "run/publish_throws", jobsBoth,
      base( { publish: { "/work/Flat/masterFlat_Ha.xisf": "throw" } } ), OPTS );

   /* ---- execute: names, deletion rules and the engine's removals ---- */
   executeCase( "run/master_name_taken", jobsBoth,
      base( { fs: base().fs.concat( [ "/work/Flat/masterFlat_Ha.xisf" ] ) } ), OPTS );
   executeCase( "run/master_name_taken_twice", jobsBoth,
      base( { fs: base().fs.concat( [ "/work/Flat/masterFlat_Ha.xisf", "/work/Flat/masterFlat_Ha_2.xisf" ] ) } ), OPTS );
   executeCase( "run/symlinked_raw_flat_refused", jobsBoth,
      base( { resolve: { "/work/Flat/Ha_2.xisf": "/card/Light/Ha_2.xisf" } } ), OPTS );
   executeCase( "run/flat_folder_is_link_onto_card", jobsBoth,
      base( { resolve: { "/work/Flat": "/card/Flat", "/work/Flat/Ha_1.xisf": "/card/Flat/Ha_1.xisf",
                         "/work/Flat/Ha_2.xisf": "/card/Flat/Ha_2.xisf" } } ), OPTS );
   {
      const withOutside = structuredClone( jobsBoth );
      const extra = flat( "Ha", 9, 2, 1, "/elsewhere" );
      withOutside[0].flats.push( extra );
      withOutside[0].parts[0].flats.push( extra );
      executeCase( "run/flat_outside_folder_kept", withOutside,
         base( { fs: base().fs.concat( [ "/elsewhere/Ha_9.xisf" ] ) } ), OPTS );
   }
   executeCase( "run/remove_refused_for_one_flat", jobsBoth, base( { removeFail: [ "/work/Flat/OIII_2.xisf" ] } ), OPTS );
   executeCase( "run/remove_throws_still_there", jobsBoth, base( { removeThrow: { "/work/Flat/Ha_1.xisf": "stays" } } ), OPTS );
   executeCase( "run/remove_throws_gone", jobsBoth, base( { removeThrow: { "/work/Flat/Ha_1.xisf": "gone" } } ), OPTS );
   executeCase( "run/no_resolve_nothing_deleted", jobsBoth, base( { resolve: null } ), OPTS );
   executeCase( "run/no_destination_folder", jobsBoth, base( { removePartials: true } ),
      { destFlatDir: null, cardRoot: CARD } );
   executeCase( "run/card_contains_flat_folder", jobsBoth, base(), { destFlatDir: DEST, cardRoot: "/work" } );
   executeCase( "run/temp_dir_throws", jobsBoth, base( { tempDirThrow: "no temporary space" } ), OPTS );

   /* ---- execute: the plan's own skips, the stop and the progress ---- */
   executeCase( "run/plan_skip_no_darks", plan( HA, [], "on the ASIAIR card" ), base(), OPTS );
   executeCase( "run/remove_partials_called", jobsBoth, base( { removePartials: true } ), OPTS );
   executeCase( "run/stop_after_first_job", jobsBoth, base( { stopAfter: 1 } ), OPTS );
   executeCase( "run/cancelled_before_first_job", jobsBoth, base( { stopAfter: -1 } ), OPTS );
   executeCase( "run/no_jobs", [], base(), OPTS );

   /* ---- pure: verifyMaster ---- */
   function verifyCase( key, got, want, filter )
   {
      const g = got == null ? null : Object.assign( {}, got, { keyword: keywordOf( got.keywords || {} ) } );
      if ( g ) delete g.keywords;
      const out = M.verifyMaster( g, want, filter );
      cases[key] = { fn: "verifyMaster", args: { got: got, want: want, filter: filter }, out: norm( out ) };
   }
   function keywordOf( kw ) { return function( name ) { return has( kw, name ) ? kw[name] : null; }; }
   const good = { width: 10, height: 20, mean: 1.5, keywords: { FILTER: "Ha", IMAGETYP: "Master Flat" } };
   const wantOk = { width: 10, height: 20 };
   verifyCase( "verify/ok", good, wantOk, "Ha" );
   verifyCase( "verify/no_filter_check", good, wantOk, null );
   verifyCase( "verify/unreadable", null, wantOk, "Ha" );
   verifyCase( "verify/geometry", Object.assign( {}, good, { width: 11 } ), wantOk, "Ha" );
   verifyCase( "verify/filter_wrong", Object.assign( {}, good, { keywords: { FILTER: "OIII", IMAGETYP: "Master Flat" } } ), wantOk, "Ha" );
   verifyCase( "verify/filter_missing", Object.assign( {}, good, { keywords: { IMAGETYP: "Master Flat" } } ), wantOk, "Ha" );
   verifyCase( "verify/imagetyp_not_flat", Object.assign( {}, good, { keywords: { FILTER: "Ha", IMAGETYP: "Light" } } ), wantOk, "Ha" );
   verifyCase( "verify/imagetyp_lower_case", Object.assign( {}, good, { keywords: { FILTER: "Ha", IMAGETYP: "master FLAT" } } ), wantOk, "Ha" );
   verifyCase( "verify/mean_zero", Object.assign( {}, good, { mean: 0 } ), wantOk, "Ha" );
   verifyCase( "verify/mean_nan", Object.assign( {}, good, { mean: NaN } ), wantOk, "Ha" );
   verifyCase( "verify/mean_infinite", Object.assign( {}, good, { mean: Infinity } ), wantOk, "Ha" );
   verifyCase( "verify/mean_string", Object.assign( {}, good, { mean: "1.5" } ), wantOk, "Ha" );

   /* ---- pure: cleanPath ---- */
   for ( const [ k, p ] of [ [ "double_and_trailing", "//a//b/" ], [ "root", "/" ], [ "only_slashes", "//" ],
                             [ "trailing", "a/" ], [ "empty", "" ], [ "plain", "/a/b" ], [ "drive_like", "C://x" ] ] )
      cases[ "clean/" + k ] = { fn: "cleanPath", args: [ p ], out: norm( M.cleanPath( p ) ) };

   /* ---- pure: mayDelete ---- */
   function mayCase( key, path, dest, master, card, resolve )
   {
      const r = resolve == null ? null : Object.assign( {}, resolve );
      const fn = r == null ? undefined : function( p )
      {
         if ( has( r, p ) && r[p] === "throw" ) throw new Error( "cannot resolve " + p );
         return has( r, p ) ? r[p] : p;
      };
      const out = M.mayDelete( path, dest, master, card, fn );
      cases[key] = { fn: "mayDelete", args: [ path, dest, master, card ], resolve: resolve, out: norm( out ) };
   }
   const MASTER = DEST + "/masterFlat_Ha.xisf";
   mayCase( "may/plain_flat", DEST + "/a.xisf", DEST, MASTER, CARD, null );
   mayCase( "may/plain_flat_resolver_same", DEST + "/a.xisf", DEST, MASTER, CARD, {} );
   mayCase( "may/master_name", DEST + "/masterFlat_Ha.xisf", DEST, MASTER, CARD, null );
   mayCase( "may/master_name_any_case", DEST + "/MASTERFLAT_x.xisf", DEST, MASTER, CARD, null );
   mayCase( "may/job_master_other_case", "/WORK/flat/MasterFlat_HA.xisf", DEST, MASTER, CARD, null );
   mayCase( "may/subfolder", DEST + "/sub/a.xisf", DEST, MASTER, CARD, null );
   mayCase( "may/parent_reference", DEST + "/../Flat/a.xisf", DEST, MASTER, CARD, null );
   mayCase( "may/parent_at_end", DEST + "/a/..", DEST, MASTER, CARD, null );
   mayCase( "may/dots_in_name", DEST + "/..a.xisf", DEST, MASTER, CARD, null );
   mayCase( "may/repeated_slashes", "/work//Flat//a.xisf", DEST + "/", MASTER, CARD, null );
   mayCase( "may/empty_name", DEST + "/", DEST, MASTER, CARD, null );
   mayCase( "may/no_path", null, DEST, MASTER, CARD, null );
   mayCase( "may/no_dest", DEST + "/a.xisf", null, MASTER, CARD, null );
   mayCase( "may/card_inside_flat_folder", DEST + "/a.xisf", DEST, MASTER, "/work", null );
   mayCase( "may/empty_card_root", DEST + "/a.xisf", DEST, MASTER, "", null );
   mayCase( "may/root_destination", "/a.xisf", "/", MASTER, CARD, null );
   mayCase( "may/resolves_elsewhere", DEST + "/a.xisf", DEST, MASTER, CARD, { [DEST + "/a.xisf"]: "/elsewhere/a.xisf" } );
   mayCase( "may/resolver_throws", DEST + "/a.xisf", DEST, MASTER, CARD, { [DEST + "/a.xisf"]: "throw" } );
   mayCase( "may/flat_folder_link_onto_card", DEST + "/a.xisf", DEST, MASTER, CARD,
      { [DEST]: "/card/Flat", [DEST + "/a.xisf"]: "/card/Flat/a.xisf" } );

   /* ---- pure: report ---- */
   const full = { made: [ { name: "masterFlat_Ha.xisf", filter: "Ha", binning: 1, flats: 2, removed: 1 } ],
                  kept: [ { filter: "OIII", binning: 2, reason: "calibration failed: no light", flats: 3 },
                          { filter: "L", binning: null, reason: "cancelled", flats: 1 } ],
                  cancelled: true, notes: [ "not deleted (not a plain file in the destination Flat folder): /x/y.xisf" ] };
   cases["report/full"] = { fn: "report", args: [ full ], out: norm( M.report( full ) ) };
   const none = { made: [], kept: [], cancelled: false, notes: [] };
   cases["report/empty"] = { fn: "report", args: [ none ], out: norm( M.report( none ) ) };

   return cases;
};
