"use strict";
/*
 * Golden cases for loomsiril/frames_batches.py: measureInBatches, runBatches, NO_SETTLER and throttle of
 * pixinsight/script/lib/Frames.js (2019-2134). batchesOf, mergeBatch and measuringLines are pinned by fs_flags
 * and fs_core.
 *
 * Every case stores its arguments as plain data. The callbacks (measure, onBatch, settler, the throttled
 * callback) are fakes built from that description, and each fake appends what it was called with to a log.
 * A case records the log and the result, so the Python suite rebuilds the same fakes, replays the same calls
 * and compares both. Arguments are cloned before every call.
 *
 * Undefined does not survive JSON: a fake's undefined return is written as null, and so is each direct
 * return of NO_SETTLER.
 */
module.exports = function( js )
{
   const F = js.Frames;
   const cases = {};

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

   const nul = v => v === undefined ? null : v;

   function paths( n )
   {
      return Array.from( { length: n }, ( _, i ) => "/subs/Ha/L_" + String( i + 1 ).padStart( 3, "0" ) + ".xisf" );
   }
   const P7 = paths( 7 ), P20 = paths( 20 );

   /* A measure fake: call c returns metrics for each path of batch c, except omit; throws at throwAt, returns
      null at nullAt, adds the path foreign to its result at foreignAt. Call indices count from 0. */
   function measureSpec( over )
   {
      return Object.assign( { throwAt: -1, nullAt: -1, omit: [], foreignAt: -1, foreign: "" }, over );
   }

   function fakeMeasure( spec, log )
   {
      let calls = 0;
      return function( batch )
      {
         const c = calls++;
         log.push( "measure " + c + " [" + batch.join( "," ) + "]" );
         if ( spec.throwAt === c ) throw new Error( "measure failed at call " + c );
         if ( spec.nullAt === c ) return null;
         const got = {};
         batch.forEach( ( p, i ) => { if ( !spec.omit.includes( p ) ) got[p] = { call: c, pos: i }; } );
         if ( spec.foreignAt === c ) got[spec.foreign] = { call: c, pos: -1 };
         return got;
      };
   }

   /* An onBatch fake: returns false at its call index falseAt (-1 never), true otherwise. */
   function fakeOnBatch( spec, log )
   {
      let calls = 0;
      return function( done )
      {
         const c = calls++;
         log.push( "onBatch " + c + " " + done );
         return c !== spec.falseAt;
      };
   }

   /* A settler fake: start returns the token "t<k>" (null when startNull); finish and kill log their token. */
   function fakeSettler( spec, log )
   {
      let starts = 0;
      return {
         start: function( batch, got )
         {
            const k = starts++;
            log.push( "start " + batch.join( "," ) + " got " + Object.keys( got ).join( "," ) );
            return spec.startNull ? null : "t" + k;
         },
         finish: function( token ) { log.push( "finish " + token ); },
         kill: function( token ) { log.push( "kill " + token ); }
      };
   }

   function measureCase( key, args )
   {
      const log = [];
      const measure = fakeMeasure( args.measure, log );
      const onBatch = args.onBatch === null ? null : fakeOnBatch( args.onBatch, log );
      const settler = args.settler === null ? null : fakeSettler( args.settler, log );
      let result;
      try { result = F.measureInBatches( structuredClone( args.paths ), args.size, measure, onBatch, settler ); }
      catch ( e ) { result = { threw: e.message }; }
      cases[key] = { args: norm( args ), log: log, result: norm( result ) };
   }

   const M = over => measureSpec( over );
   const OB = falseAt => ( { falseAt: falseAt } );
   const S = startNull => ( { startNull: startNull } );

   // batch sizes: 1, 3, 8 (20 paths in three batches), 0, 2.5, the text "3", and no paths at all
   measureCase( "measure/size1", { paths: P7, size: 1, measure: M( {} ), onBatch: OB( -1 ), settler: S( false ) } );
   measureCase( "measure/size3", { paths: P7, size: 3, measure: M( {} ), onBatch: OB( -1 ), settler: S( false ) } );
   measureCase( "measure/size8", { paths: P20, size: 8, measure: M( {} ), onBatch: OB( -1 ), settler: S( false ) } );
   measureCase( "measure/size0", { paths: P7, size: 0, measure: M( {} ), onBatch: null, settler: S( false ) } );
   measureCase( "measure/size2_5", { paths: P7, size: 2.5, measure: M( {} ), onBatch: OB( -1 ), settler: S( false ) } );
   measureCase( "measure/size_text", { paths: P7, size: "3", measure: M( {} ), onBatch: null, settler: null } );
   measureCase( "measure/empty", { paths: [], size: 8, measure: M( {} ), onBatch: OB( -1 ), settler: S( false ) } );
   measureCase( "measure/paths_null", { paths: null, size: 8, measure: M( {} ), onBatch: null, settler: null } );

   // a result for a path the batch did not ask for, or one already measured, abandons the channel
   measureCase( "measure/abandoned_null", { paths: P7, size: 3, measure: M( { nullAt: 1 } ), onBatch: OB( -1 ), settler: S( false ) } );
   measureCase( "measure/abandoned_foreign", { paths: P7, size: 3,
                  measure: M( { foreignAt: 1, foreign: "/subs/Ha/other.xisf" } ), onBatch: OB( -1 ), settler: S( false ) } );
   measureCase( "measure/abandoned_repeat", { paths: P7, size: 3,
                  measure: M( { foreignAt: 1, foreign: P7[0] } ), onBatch: OB( -1 ), settler: S( false ) } );
   measureCase( "measure/abandoned_first_batch", { paths: P7, size: 3,
                  measure: M( { foreignAt: 0, foreign: "/subs/Ha/other.xisf" } ), onBatch: null, settler: S( false ) } );

   // a path with no result is simply absent
   measureCase( "measure/omitted_path", { paths: P7, size: 3, measure: M( { omit: [ P7[4] ] } ), onBatch: OB( -1 ), settler: S( false ) } );

   // an exception propagates; the batch still open is killed, never finished
   measureCase( "measure/throws_first", { paths: P7, size: 3, measure: M( { throwAt: 0 } ), onBatch: OB( -1 ), settler: S( false ) } );
   measureCase( "measure/throws_mid", { paths: P7, size: 3, measure: M( { throwAt: 1 } ), onBatch: OB( -1 ), settler: S( false ) } );
   measureCase( "measure/throws_last", { paths: P7, size: 1, measure: M( { throwAt: 6 } ), onBatch: null, settler: S( false ) } );

   // cancel: onBatch returns false at the first, a middle and the last batch
   measureCase( "measure/cancel_first", { paths: P7, size: 3, measure: M( {} ), onBatch: OB( 0 ), settler: S( false ) } );
   measureCase( "measure/cancel_middle", { paths: P7, size: 3, measure: M( {} ), onBatch: OB( 1 ), settler: S( false ) } );
   measureCase( "measure/cancel_last", { paths: P7, size: 3, measure: M( {} ), onBatch: OB( 2 ), settler: S( false ) } );
   measureCase( "measure/cancel_no_settler", { paths: P7, size: 3, measure: M( {} ), onBatch: OB( 1 ), settler: null } );

   // settler: finish order, a start that returns no token, a kill on an exception, a settle before abandon
   measureCase( "measure/settler_start_null", { paths: P7, size: 3, measure: M( {} ), onBatch: OB( -1 ), settler: S( true ) } );
   measureCase( "measure/settler_kill_on_throw", { paths: P7, size: 3, measure: M( { throwAt: 2 } ), onBatch: OB( -1 ), settler: S( false ) } );
   measureCase( "measure/settler_settles_before_abandon", { paths: P7, size: 3, measure: M( { nullAt: 2 } ), onBatch: OB( -1 ), settler: S( false ) } );

   // no callback and no settler at all
   measureCase( "measure/no_callbacks", { paths: P7, size: 3, measure: M( {} ), onBatch: null, settler: null } );

   // NO_SETTLER itself: start yields no token, finish and kill do nothing
   cases["no_settler/methods"] = { args: {}, log: [],
      result: norm( { start: nul( F.NO_SETTLER.start( [ "a" ], {} ) ),
                      finish: nul( F.NO_SETTLER.finish( "x" ) ),
                      kill: nul( F.NO_SETTLER.kill( "x" ) ) } ) };

   /* throttle: the clock is a list stepped once per call; the callback logs and returns true unless it is call falseAt. */
   function throttleCase( key, args )
   {
      const log = [];
      const fn = args.fn === null ? null : ( function()
      {
         let c = 0;
         return function( done, total, name )
         {
            const k = c++;
            log.push( "fn " + k + " " + done + "/" + total + " " + name );
            return k !== args.fn.falseAt;
         };
      } )();
      const times = args.times.slice();
      let at = 0;
      const w = F.throttle( fn, args.ms, () => times[at++] );
      const returns = w == null ? null : args.calls.map( c => w( c[0], c[1], c[2] ) );
      cases[key] = { args: norm( args ), log: log,
                     result: norm( { wrapper: w == null ? null : typeof w, returns: returns } ) };
   }

   throttleCase( "throttle/null_fn", { fn: null, ms: 100, times: [], calls: [ [ 1, 2, "a" ] ] } );
   throttleCase( "throttle/basic", { fn: { falseAt: -1 }, ms: 100, times: [ 0, 50, 120, 130, 250, 260 ],
                 calls: [ [ 1, 6, "a" ], [ 2, 6, "b" ], [ 3, 6, "c" ], [ 4, 6, "d" ], [ 5, 6, "e" ], [ 6, 6, "f" ] ] } );
   throttleCase( "throttle/last_file_passes", { fn: { falseAt: -1 }, ms: 1000, times: [ 0, 10, 20, 30 ],
                 calls: [ [ 1, 4, "L" ], [ 2, 4, "L" ], [ 3, 4, "L" ], [ 4, 4, "L" ] ] } );
   throttleCase( "throttle/last_reported_once", { fn: { falseAt: -1 }, ms: 1000, times: [ 0, 5, 10, 15 ],
                 calls: [ [ 4, 4, "a" ], [ 4, 4, "b" ] ] } );
   throttleCase( "throttle/zero_ms", { fn: { falseAt: -1 }, ms: 0, times: [ 0, 0, 0 ],
                 calls: [ [ 1, 3, "x" ], [ 2, 3, "x" ], [ 3, 3, "x" ] ] } );
   throttleCase( "throttle/held_answers_go_on", { fn: { falseAt: 0 }, ms: 100, times: [ 0, 10, 200 ],
                 calls: [ [ 1, 3, "x" ], [ 2, 3, "x" ], [ 3, 3, "x" ] ] } );

   return cases;
};
