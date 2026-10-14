/*
 * Oracle for loomsiril/bloom.py (E4): the stars-layer bloom and headroom maths as the JS reference
 * (Render.saturateStars, starsToHeadroomJs, whitenPastWhite, pastWhite, bloomSigmas, bloomJs, bloom).
 * The 32 x 24 x 3 stars layer is generated here and stored, with values past white.
 */
"use strict";

module.exports = function( js )
{
   const Render = js.Render;
   const W = 32, H = 24, N = W * H, NC = 3;
   const T = [];
   for ( let c = 0; c < NC; ++c )
   {
      const p = new Float32Array( N );
      for ( let y = 0; y < H; ++y ) for ( let x = 0; x < W; ++x )
      {
         const d1 = ( x - 10 ) * ( x - 10 ) + ( y - 8 ) * ( y - 8 ), d2 = ( x - 23 ) * ( x - 23 ) + ( y - 15 ) * ( y - 15 );
         p[y * W + x] = 0.05 + 0.02 * ( ( x + y + c ) % 3 ) + ( 2.4 - 0.5 * c ) * Math.exp( -d1 / 6 ) + ( 0.4 + 0.3 * c ) * Math.exp( -d2 / 20 );
      }
      T.push( p );
   }
   const copyT = ( src ) => src.map( p => Float32Array.from( p ) );
   const out = { constants: { BLOOM_TIGHT: Render.BLOOM_TIGHT, BLOOM_WIDE: Render.BLOOM_WIDE, BLOOM_GLARE: Render.BLOOM_GLARE,
                              BLOOM_GAINS: Render.BLOOM_GAINS, EXCESS_MAX: Render.EXCESS_MAX } };
   out.input = { w: W, h: H, nc: NC, T: T.map( p => Array.from( p ) ) };

   // saturateStars(T, n, sat)
   out.saturateStars = [ 0.6, 1.4 ].map( sat => { const t = copyT( T ); Render.saturateStars( t, N, sat ); return { sat: sat, out: t.map( p => Array.from( p ) ) }; } );

   // starsToHeadroomJs(T, n, factor): one number, then a per-pixel map
   const map = Float32Array.from( { length: N }, ( _, i ) => 1 + 0.5 * Math.exp( -( ( i % W ) - 10 ) * ( ( i % W ) - 10 ) / 8 ) * ( i < 4 * W ? 0 : 1 ) );
   out.starsToHeadroomJs = [ { factor: 1.7 }, { map: Array.from( map ) } ].map( c =>
   {
      const t = copyT( T ); Render.starsToHeadroomJs( t, N, c.factor !== undefined ? c.factor : map );
      return { factor: c.factor !== undefined ? c.factor : null, map: c.map || null, out: t.map( p => Array.from( p ) ) };
   } );

   // whitenPastWhite(T, n)
   { const t = copyT( T ); Render.whitenPastWhite( t, N ); out.whitenPastWhite = t.map( p => Array.from( p ) ); }

   // pastWhite(T, n): the light past white, or null; and null for a layer that never passes white
   out.pastWhite = { past: ( () => { const e = Render.pastWhite( T, N ); return e ? e.map( p => Array.from( p ) ) : null; } )(),
                     clipped: ( () => { const t = T.map( p => Float32Array.from( p, v => Math.min( v, 1 ) ) ); return Render.pastWhite( t, N ); } )() };

   // bloomSigmas(short, seconds)
   out.bloomSigmas = [ [ 24, 0.7 ], [ 24, 0 ], [ 13.4, 9.25 ], [ 24, undefined ] ].map( a => ( { args: a, out: Render.bloomSigmas( a[0], a[1] ) } ) );

   // bloomJs and bloom (the dispatcher routes to bloomJs outside PixInsight)
   const opts = [ { amount: 0.8, seconds: 1.5 }, { amount: 0, seconds: 1.5 }, { amount: 1, seconds: 0 } ];
   out.bloomJs = opts.map( o => { const t = copyT( T ); Render.bloomJs( t, W, H, o ); return { opts: o, out: t.map( p => Array.from( p ) ) }; } );
   out.bloom = opts.map( o => { const t = copyT( T ); Render.bloom( t, W, H, o ); return { opts: o, out: t.map( p => Array.from( p ) ) }; } );

   return out;
};
