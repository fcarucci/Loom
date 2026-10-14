/*
 * Oracle for loomsiril/resample.py, loomsiril/scene.py (rotateScene) (E4): Render's resampling
 * kernels, shrink, mips, the box blur, the area fit and the quarter turn. Every input buffer is
 * generated here and stored in the JSON, so Python replays the same floats.
 */
"use strict";

module.exports = function( js )
{
   const Render = js.Render;
   const out = {};

   // axisWeights: [outN, cropStart, cropLen, tp, K, srcN, kernel]
   const axes = {
      identity: [ 12, 0, 12, 5.5, 1, 12, "bicubic" ],
      zoomBicubic: [ 16, 2, 10, 6.0, 1.3, 12, "bicubic" ],
      zoomBilinear: [ 16, 2, 10, 6.0, 1.3, 12, "bilinear" ],
      minifyTent: [ 8, 0, 24, 11.5, 1, 24, "bicubic" ],
      minifyOffCentre: [ 5, 1, 20, 10.0, 0.9, 24, "bicubic" ],
      edgeClamp: [ 4, 0, 4, 0.5, 1, 4, "bicubic" ],
      wholeRounding: [ 7, 0, 7, 3.0000000001, 1, 7, "bicubic" ]
   };
   out.axisWeights = {};
   for ( const k of Object.keys( axes ) )
   {
      const a = axes[k], ax = Render.axisWeights( a[0], a[1], a[2], a[3], a[4], a[5], a[6] );
      out.axisWeights[k] = { args: a, out: { outN: ax.outN, start: ax.start, idx: ax.idx, w: ax.w } };
   }

   // kernelTaps / tentTaps: scalar taps
   out.kernelTaps = [ [ 4.25, "bicubic" ], [ 4.25, "bilinear" ], [ -0.5, "bicubic" ], [ 7, "bicubic" ] ]
      .map( a => ( { args: a, out: Render.kernelTaps( a[0], a[1] ) } ) );
   out.tentTaps = [ [ 5.3, 2.5 ], [ 0.2, 1.7 ], [ 9.99, 3.0 ] ].map( a => ( { args: a, out: Render.tentTaps( a[0], a[1] ) } ) );
   out.cubicWeights = [ 0, 0.25, 0.5, 0.9 ].map( f => ( { args: [ f ], out: Render.cubicWeights( f ) } ) );

   // resample / resampleRows of a stored 12 x 9 buffer
   const W = 12, H = 9, buf = new Float32Array( W * H );
   for ( let y = 0; y < H; ++y ) for ( let x = 0; x < W; ++x )
      buf[y * W + x] = ( Math.sin( x * 1.7 + y * 0.9 ) + 1 ) / 2 + ( ( x + 2 * y ) % 5 ) / 10;
   const input = { w: W, h: H, buf: Array.from( buf ) };
   const axA = [ 10, 1, 11, 5.5, 1.3, W, "bicubic" ], ayA = [ 7, 0, 9, 4.0, 1.3, H, "bicubic" ];
   const axT = [ 4, 0, 12, 6.0, 1, W, "bicubic" ];   // minified: the tent path
   const ax1 = Render.axisWeights( ...axA ), ay1 = Render.axisWeights( ...ayA ), ax2 = Render.axisWeights( ...axT ), ay2 = Render.axisWeights( 3, 0, 9, 4, 1, H, "bicubic" );
   out.resample = {
      input,
      cases: [
         { ax: axA, ay: ayA, out: Array.from( Render.resample( buf, W, ax1, ay1 ) ) },
         { ax: axT, ay: [ 3, 0, 9, 4, 1, H, "bicubic" ], out: Array.from( Render.resample( buf, W, ax2, ay2 ) ) },
         { ax: [ 12, 0, 12, 5.5, 1, W, "bicubic" ], ay: [ 9, 0, 9, 4.0, 1, H, "bicubic" ],
           out: Array.from( Render.resample( buf, W, Render.axisWeights( 12, 0, 12, 5.5, 1, W, "bicubic" ), Render.axisWeights( 9, 0, 9, 4.0, 1, H, "bicubic" ) ) ) }
      ]
   };
   out.resampleRows = { input, ax: axA, rmin: 2, rows: 4,
                        out: Array.from( Render.resampleRows( buf, W, ax1, 2, 4 ) ) };

   // shrinkAxis(i0, n, d, N) and normaliseWeights
   out.shrinkAxis = [ [ 3, 5, 0.5, 11 ], [ 7, 4, 0.5, 11 ], [ 0, 9, 0.6, 0 ], [ 2, 6, 1 / 3, 9 ], [ 8, 3, 0.5, 11 ] ]
      .map( a => ( { args: a, out: Render.shrinkAxis( a[0], a[1], a[2], a[3] ) } ) );
   out.normaliseWeights = [ { w: [ 0.2, 0.3, 0.5, 0.25, 0.75 ], from: 1, to: 3 } ].map( c =>
   {
      const w = c.w.slice(); Render.normaliseWeights( w, c.from, c.to );
      return { input: c.w, from: c.from, to: c.to, out: w };
   } );

   // shrinkPatch(buf, x0, y0, w, h, d, imgW, imgH) on a stored 11 x 9 image
   const IW = 11, IH = 9, img = new Float32Array( IW * IH );
   for ( let y = 0; y < IH; ++y ) for ( let x = 0; x < IW; ++x ) img[y * IW + x] = 0.1 + 0.8 * ( ( x * 7 + y * 3 ) % 11 ) / 10;
   const cutPatch = ( x0, y0, w, h ) => { const p = new Float32Array( w * h ); for ( let y = 0; y < h; ++y ) for ( let x = 0; x < w; ++x ) p[y * w + x] = img[( y0 + y ) * IW + x0 + x]; return p; };
   out.shrinkPatch = { image: { w: IW, h: IH, buf: Array.from( img ) }, cases: [
      { x0: 3, y0: 2, w: 5, h: 4, d: 0.5, imgW: IW, imgH: IH },
      { x0: 2, y0: 1, w: 5, h: 4, d: 0.6, imgW: IW, imgH: IH },
      { x0: 8, y0: 6, w: 3, h: 3, d: 0.5, imgW: IW, imgH: IH },
      { x0: 7, y0: 0, w: 4, h: 9, d: 0.5, imgW: IW, imgH: IH } ].map( c =>
   {
      const p = cutPatch( c.x0, c.y0, c.w, c.h );
      const r = Render.shrinkPatch( p, c.x0, c.y0, c.w, c.h, c.d, c.imgW, c.imgH );
      return { x0: c.x0, y0: c.y0, w: c.w, h: c.h, d: c.d, imgW: c.imgW, imgH: c.imgH, patch: Array.from( p ),
               out: { buf: Array.from( r.buf ), x0: r.x0, y0: r.y0, w: r.w, h: r.h } };
   } ) };

   // halve / mipOf on a stored 5 x 3 plane
   const hp = { w: 5, h: 3, d: Float32Array.from( [ 0.1, 0.4, 0.2, 0.9, 0.5, 0.3, 0.8, 0.6, 0.7, 0.2, 0.05, 0.95, 0.35, 0.65, 0.15 ] ) };
   out.halve = { input: { w: hp.w, h: hp.h, d: Array.from( hp.d ) }, out: Render.halve( hp ) };
   out.mipOf = [ 1, 2 ].map( L => ( { args: [ 5, 3, L], out: Render.mipOf( Float32Array.from( hp.d ), 5, 3, L ) } ) );

   // patchSample on a stored 4 x 3 patch, inside and outside
   const pp = Float32Array.from( [ 0.2, 0.5, 0.9, 0.1, 0.4, 0.8, 0.3, 0.6, 0.7, 0.25, 0.55, 0.85 ] );
   out.patchSample = { input: { rw: 4, rh: 3, patch: Array.from( pp ) },
      points: [ [ 1.25, 0.5 ], [ 0, 0 ], [ 3, 2 ], [ -0.5, 1 ], [ 3.5, 2.5 ], [ 2.2, -0.7 ], [ 4.2, 1 ], [ 3.9, 2.9 ] ]
         .map( p => ( { args: p, out: Render.patchSample( pp, 4, 3, p[0], p[1] ) } ) ) };

   // boxBlur of a stored 7 x 5 plane, r = 1, 2 and a no-op r = 0
   const BW = 7, BH = 5, bb = new Float32Array( BW * BH );
   for ( let i = 0; i < BW * BH; ++i ) bb[i] = ( ( i * 37 ) % 23 ) / 22;
   out.boxBlur = { input: { w: BW, h: BH, a: Array.from( bb ) }, cases: [ 0, 1, 2 ].map( r =>
   {
      const a = Float32Array.from( bb ); Render.boxBlur( a, BW, BH, r );
      return { r: r, out: Array.from( a ) };
   } ) };

   // fitPlane (enlarge: patchSample; minify: areaMean) and areaMean
   const fsrc = ( sw, sh, f ) => { const p = new Float32Array( sw * sh ); for ( let i = 0; i < sw * sh; ++i ) p[i] = f( i ); return p; };
   const big = fsrc( 10, 7, i => ( ( i * 13 ) % 17 ) / 16 ), small = fsrc( 4, 3, i => ( ( i * 5 ) % 7 ) / 6 ), odd = fsrc( 9, 7, i => ( ( i * 11 ) % 19 ) / 18 );
   out.fitPlane = [
      { src: { w: 4, h: 3, a: Array.from( small ) }, w: 6, h: 5, out: Array.from( Render.fitPlane( small, 4, 3, 6, 5 ) ) },
      { src: { w: 10, h: 7, a: Array.from( big ) }, w: 4, h: 3, out: Array.from( Render.fitPlane( big, 10, 7, 4, 3 ) ) },
      { src: { w: 9, h: 7, a: Array.from( odd ) }, w: 4, h: 3, out: Array.from( Render.fitPlane( odd, 9, 7, 4, 3 ) ) } ];
   out.areaMean = [ [ 1.5, 0.25, 2.25, 2.33 ], [ 8, 5, 3, 3 ], [ 0, 0, 10, 7 ] ]
      .map( a => ( { args: a, out: Render.areaMean( big, 10, 7, a[0], a[1], a[2], a[3] ) } ) );

   // boxRadius, mipLevel
   out.boxRadius = [ 0, 0.5, 1, 2.3, 7.7, 40 ].map( s => ( { args: [ s ], out: Render.boxRadius( s ) } ) );
   out.mipLevel = [ [ 1 / 3, 1, false ], [ 8, 1, false ], [ 8, 1, true ], [ 2.5, 0.5, true ], [ 1.5, 1, true ], [ 40, 2, false ] ]
      .map( a => ( { args: a, out: Render.mipLevel( a[0], a[1], a[2] ) } ) );

   // rotateScene of a 5 x 3 scene (two channels, no sprites), with a catalogue entry and a projection
   const RW = 5, RH = 3, S0 = new Float32Array( RW * RH ), S1 = new Float32Array( RW * RH ), R0 = new Float32Array( RW * RH );
   for ( let i = 0; i < RW * RH; ++i ) { S0[i] = i / 15; S1[i] = ( i % 4 ) / 4; R0[i] = ( i * 3 % 7 ) / 7; }
   const sc = { w: RW, h: RH, nc: 2, S: [ S0, S1 ], R: [ R0 ], sprites: [], project: ( ra, dec ) => ( { x: ra * 1.5 + 0.25, y: dec - 0.5 } ),
                target: { ra: 2, dec: 1 }, tp: { x: 1.5, y: 0.5 }, D: 100, catalogue: [ { x: 1, y: 2, source: { ra: 1, dec: 2 } } ] };
   const turned = Render.rotateScene( sc );
   out.rotateScene = { input: { w: RW, h: RH, nc: 2, S: [ Array.from( S0 ), Array.from( S1 ) ], R: [ Array.from( R0 ) ] },
      out: { w: turned.w, h: turned.h, S: turned.S.map( a => Array.from( a ) ), R: turned.R.map( a => Array.from( a ) ),
             tp: turned.tp, catalogue: turned.catalogue, projected: turned.project( 2.0, 1.25 ) } };

   return out;
};
