/*
 * Recorder for the pixel-helper port (loomsiril/loom/imageops.py): valid rectangles, the common-area
 * crop decision, central rectangles and medians, rectangle intersection, keyword consistency, and the
 * sigma arithmetic. Plain numbers and arrays in, plain values out. The fake images below implement only
 * the members the Loom functions touch: width, height, selectedRect and median(). Their median() is the
 * ordinary median (mean of the two middle values for an even count), the definition numpy uses.
 */
"use strict";

function medianOf( values )
{
   const a = values.slice().sort( ( u, v ) => u - v );
   const n = a.length;
   return n % 2 ? a[( n - 1 ) / 2] : ( a[n / 2 - 1] + a[n / 2] ) / 2;
}

/* A frame with zero fill outside [top, H-bottom) x [left, W-right) and a deterministic pattern inside. */
function frame( W, H, top, bottom, left, right, seed )
{
   const rows = [];
   for ( let y = 0; y < H; ++y )
   {
      const row = [];
      for ( let x = 0; x < W; ++x )
      {
         const inside = y >= top && y < H - bottom && x >= left && x < W - right;
         row.push( inside ? 0.05 + ( ( x * 31 + y * 17 + seed * 7 ) % 23 ) / 100 : 0 );
      }
      rows.push( row );
   }
   return rows;
}

function fakeView( id, data )
{
   const H = data.length, W = data[0].length;
   const image = {
      width: W,
      height: H,
      data: data,
      selectedRect: null,
      median()
      {
         const r = this.selectedRect || { x0: 0, y0: 0, x1: W, y1: H };
         const vals = [];
         for ( let y = r.y0; y < r.y1; ++y )
            for ( let x = r.x0; x < r.x1; ++x )
               vals.push( data[y][x] );
         return medianOf( vals );
      }
   };
   return { id: id, image: image };
}

function asArray( r ) { return r == null ? null : [ r.x0, r.y0, r.x1, r.y1 ]; }

/* PJSR's Rect (x0, y0, x1, y1) for Steps.validRect; the shim has no honest node equivalent, so a plain one is installed for the run. */
function PlainRect( x0, y0, x1, y1 ) { this.x0 = x0; this.y0 = y0; this.x1 = x1; this.y1 = y1; }

module.exports = function( js )
{
   const savedRect = Object.getOwnPropertyDescriptor( global, "Rect" );
   Object.defineProperty( global, "Rect", { value: PlainRect, writable: true, configurable: true } );
   try { return record( js ); }
   finally
   {
      if ( savedRect ) Object.defineProperty( global, "Rect", savedRect ); else delete global.Rect;
   }
};

function record( js )
{
   const { Util, Steps, Pipeline } = js;
   const out = {};

   /* validRect: a fill of zeros around the data, one hot pixel in the fill (the median ignores it). */
   const rectFrames = {
      a: ( () => { const d = frame( 24, 18, 3, 2, 1, 4, 1 ); d[0][5] = 0.9; return d; } )(),
      b: frame( 20, 16, 0, 0, 0, 0, 2 ),
      c: frame( 30, 22, 6, 5, 2, 3, 3 ),
      zero: frame( 9, 7, 7, 0, 0, 0, 0 )
   };
   const rectCases = [ [ "a", undefined ], [ "a", 0 ], [ "a", 2 ], [ "b", undefined ], [ "c", undefined ],
                       [ "c", 1 ], [ "zero", undefined ] ];
   out.valid_rect = rectCases.map( ( [ key, margin ] ) =>
   {
      const data = rectFrames[key];
      const view = fakeView( key, data );
      const rect = margin === undefined ? Steps.validRect( view ) : Steps.validRect( view, margin );
      return { data: data, margin: margin === undefined ? null : margin,
               rect: [ rect.x0, rect.y0, rect.x1, rect.y1 ] };
   } );

   out.intersect_rects = [
      { rects: [ [ 2, 3, 20, 18 ], [ 4, 1, 15, 16 ], [ 3, 4, 19, 12 ] ] },
      { rects: [ [ 0, 0, 10, 10 ], [ 10, 0, 20, 10 ] ] },
      { rects: [ [ 0, 0, 10, 10 ] ] },
      { rects: [] },
      { rects: [ [ 0, 0, 10, 10 ], [ 5, 5, 8, 9 ] ] }
   ].map( c => ( { rects: c.rects.map( r => ( { x0: r[0], y0: r[1], x1: r[2], y1: r[3] } ) ),
                    result: asArray( Util.intersectRects( c.rects.map( r => ( { x0: r[0], y0: r[1], x1: r[2], y1: r[3] } ) ) ) ) } ) );

   out.central_rect = [ [ 100, 80, 0.6 ], [ 101, 77, 0.6 ], [ 1200, 900, 0.5 ], [ 33, 19, 0.9 ], [ 10, 10, 1 ] ]
      .map( ( [ w, h, f ] ) => { const r = Util.centralRect( w, h, f ); return { w: w, h: h, fraction: f, rect: [ r.x0, r.y0, r.x1, r.y1 ] }; } );

   out.min_median_key = [
      { medians: { L: 0.3, R: 0.1, G: 0.2 }, key: Util.minMedianKey( { L: 0.3, R: 0.1, G: 0.2 } ) },
      { medians: { L: 0.1, R: 0.1, G: 0.2 }, key: Util.minMedianKey( { L: 0.1, R: 0.1, G: 0.2 } ) },
      { medians: { alpha: -2.5, beta: -7.25 }, key: Util.minMedianKey( { alpha: -2.5, beta: -7.25 } ) },
      { medians: {}, key: Util.minMedianKey( {} ) }
   ];

   out.sigma_to_reach = [ [ 3.2, 2.0 ], [ 2.0, 2.0 ], [ 1.5, 2.0 ], [ 4.0, 0.0 ], [ 0.75, 0.25 ] ]
      .map( ( [ t, c ] ) => ( { target: t, current: c, result: Util.sigmaToReach( t, c ) } ) );

   out.sigma_fwhm = [ 1.0, 2.5, 3.75 ].map( s => ( { sigma: s, fwhm: Util.sigmaToFWHM( s ), back: Util.fwhmToSigma( Util.sigmaToFWHM( s ) ) } ) );

   const keyLists = [
      [ [ { name: "FILTER", value: "Ha" }, { name: "EXPTIME", value: "300" }, { name: "DATE-OBS", value: " 2026-01-02 " } ],
        [ { name: "FILTER", value: "OIII" }, { name: "EXPTIME", value: "300" }, { name: "DATE-OBS", value: "2026-01-02" } ] ],
      [ [ { name: "A", value: "1" }, { name: "B", value: "2" } ],
        [ { name: "A", value: "1" } ],
        [ { name: "A", value: "1" }, { name: "C", value: "x" } ] ],
      [ [ { name: "ONLY", value: "same" } ] ],
      [ [ { name: "Z", value: "1" }, { name: "M", value: "2" } ], [ { name: "Z", value: "1" }, { name: "M", value: "2" } ] ]
   ];
   out.inconsistent_keywords = keyLists.map( lists => ( { lists: lists, result: Steps.inconsistentKeywords( lists ) } ) );

   /* cropToCommonArea decision: real validRect, real intersection and 50% rule; the crop itself is recorded, not run. */
   const savedCrop = Steps.cropTo, savedAbort = Pipeline.checkAbort, savedLog = Util.log;
   /* Each channel is a frame spec [W, H, top, bottom, left, right]; only its zero/non-zero pattern matters to the crop. */
   function commonCase( name, specs, refKey )
   {
      const crops = [];
      Steps.cropTo = function( view, rect ) { crops.push( [ view.id, [ rect.x0, rect.y0, rect.x1, rect.y1 ] ] ); };
      Pipeline.checkAbort = function() {};
      Util.log = function() {};
      const chans = {};
      for ( const [ key, sp ] of specs )
         chans[key] = { view: fakeView( key, frame( sp[0], sp[1], sp[2], sp[3], sp[4], sp[5], 1 ) ) };
      let res;
      try
      {
         const r = Pipeline.cropToCommonArea( chans, refKey );
         res = { ok: true, rect: [ r.x0, r.y0, r.x1, r.y1 ], crops: crops };
      }
      catch ( e ) { res = { ok: false, error: e.message, crops: crops }; }
      finally { Steps.cropTo = savedCrop; Pipeline.checkAbort = savedAbort; Util.log = savedLog; }
      return { name: name, specs: Object.fromEntries( specs ), refKey: refKey, result: res };
   }
   const A = [ 200, 150, 0, 0, 0, 0 ], B = [ 200, 150, 2, 1, 3, 0 ], C = [ 200, 150, 1, 2, 0, 5 ];
   const THIN = [ 200, 150, 0, 0, 0, 100 ], REF30 = [ 30, 30, 0, 0, 0, 0 ], OTHER30 = [ 30, 30, 0, 0, 0, 6 ];
   const DISJ_A = [ 20, 20, 0, 0, 0, 10 ], DISJ_B = [ 20, 20, 0, 0, 10, 0 ];
   out.common_area = [
      commonCase( "three_channels_overlap", [ [ "L", A ], [ "R", B ], [ "G", C ] ], "L" ),
      commonCase( "thin_channel_below_half", [ [ "L", A ], [ "R", THIN ] ], "L" ),
      commonCase( "reference_not_first", [ [ "R", B ], [ "L", A ] ], "L" ),
      commonCase( "single_channel_above_half", [ [ "L", REF30 ] ], "L" ),
      commonCase( "two_channels_below_half", [ [ "L", REF30 ], [ "R", OTHER30 ] ], "L" ),
      commonCase( "no_overlap", [ [ "L", DISJ_A ], [ "R", DISJ_B ] ], "L" )
   ];

   return out;
}
