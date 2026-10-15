/*
 * Oracle for loomsiril/logo.py: the logo layer's maths as the JS reference (Fly.logoFade, Fly.logoRect,
 * Render.logoLayer with Render.fitPlane, Render.addLogo). Render.logoLayer reads an Image; under node this recorder
 * gives it a minimal fake (getSamples on plain planes, an alpha as the extra channel) and the logo pictures are
 * generated here and stored with their inputs (an enlarged one, a shrunk one, grey and RGBA, with and without alpha).
 */
"use strict";

module.exports = function( js )
{
   const Fly = js.Fly, Render = js.Render;
   global.Rect = function( x0, y0, x1, y1 ) { this.x0 = x0; this.y0 = y0; this.x1 = x1; this.y1 = y1; };
   const plain = ( a ) => Array.from( a );

   // a fake PixInsight Image: planes[0..nominal-1] colour, an optional alpha plane after them
   function fake( w, h, colour, alpha )
   {
      const planes = colour.concat( alpha ? [ alpha ] : [] );
      return { width: w, height: h, numberOfChannels: planes.length, numberOfNominalChannels: colour.length,
               getSamples: function( a, rect, c ) { for ( let i = 0; i < a.length; ++i ) a[i] = planes[c][i]; } };
   }
   function picture( w, h, nc, seed )
   {
      const cs = [];
      for ( let c = 0; c < nc; ++c )
      {
         const p = new Float32Array( w*h );
         for ( let y = 0; y < h; ++y ) for ( let x = 0; x < w; ++x ) p[y*w + x] = ( ( x*7 + y*13 + c*5 + seed ) % 17 )/16;
         cs.push( p );
      }
      return cs;
   }
   function alphaOf( w, h )
   {
      const a = new Float32Array( w*h );
      for ( let y = 0; y < h; ++y ) for ( let x = 0; x < w; ++x ) a[y*w + x] = ( ( x - w/2 )*( x - w/2 ) + ( y - h/2 )*( y - h/2 ) < w*h/6 ) ? 1 : ( ( x + y ) % 5 )/8;
      return a;
   }

   const out = { constants: { LOGO_PLACES: Fly.LOGO_PLACES, LOGO_SIZE: Fly.LOGO_SIZE, LOGO_MARGIN: Fly.LOGO_MARGIN } };

   out.logoFade = [ [ 0, 0 ], [ 5, 0 ], [ 0, 3 ], [ 3, 3 ], [ 3.25, 3 ], [ 3.5, 3 ], [ 4, 3 ], [ 9, 3 ], [ 2, 0.5 ], [ 1.2, 0.5 ] ]
      .map( a => ( { seconds: a[0], delay: a[1], out: Fly.logoFade( a[0], a[1] ) } ) );

   const places = Fly.LOGO_PLACES.concat( [ "off", "nowhere" ] );
   out.logoRect = [];
   [ [ 1920, 1080, 400, 300 ], [ 1080, 1920, 300, 400 ], [ 1080, 1080, 33, 70 ], [ 480, 270, 1000, 250 ], [ 641, 359, 17, 5 ] ].forEach( c =>
      places.forEach( pl => out.logoRect.push( { W: c[0], H: c[1], lw: c[2], lh: c[3], place: pl, out: Fly.logoRect( c[0], c[1], c[2], c[3], pl ) } ) ) );

   // Render.logoLayer: the layer as numbers (x, y, w, h, the premultiplied planes and the alpha plane)
   const specs = [
      { name: "rgb-shrunk", w: 40, h: 28, nc: 3, alpha: false, W: 96, H: 54, place: "bottom-right", channels: 3, opacity: 1 },
      { name: "rgba-shrunk-half", w: 40, h: 28, nc: 3, alpha: true, W: 96, H: 54, place: "top-left", channels: 3, opacity: 0.5 },
      { name: "grey-enlarged", w: 7, h: 5, nc: 1, alpha: false, W: 120, H: 90, place: "center", channels: 3, opacity: 0.8 },
      { name: "grey-alpha-enlarged-mono", w: 6, h: 9, nc: 1, alpha: true, W: 100, H: 100, place: "top-mid", channels: 1, opacity: 1 },
      { name: "rgb-vertical-frame", w: 30, h: 30, nc: 3, alpha: true, W: 54, H: 96, place: "bottom-mid", channels: 3, opacity: 0.25 },
      { name: "opacity-clamped", w: 10, h: 10, nc: 3, alpha: false, W: 80, H: 60, place: "bottom-left", channels: 3, opacity: 1.7 },
      { name: "off", w: 10, h: 10, nc: 3, alpha: false, W: 80, H: 60, place: "off", channels: 3, opacity: 1 },
   ];
   out.logoLayer = specs.map( s =>
   {
      const cs = picture( s.w, s.h, s.nc, s.w ), al = s.alpha ? alphaOf( s.w, s.h ) : null;
      const L = Render.logoLayer( fake( s.w, s.h, cs, al ), s.W, s.H, s.place, s.channels, s.opacity );
      return { spec: s, colour: cs.map( plain ), alpha: al ? plain( al ) : null,
               out: L ? { x: L.x, y: L.y, w: L.w, h: L.h, p: L.p.map( plain ), a: plain( L.a ) } : null };
   } );

   // Render.addLogo on a 64 x 36 frame: SDR (no excess) and HDR (excess planes), a partial and a full fade
   const W = 64, H = 36, n = W*H;
   const cs = picture( 40, 28, 3, 40 ), al = alphaOf( 40, 28 );
   const L = Render.logoLayer( fake( 40, 28, cs, al ), W, H, "bottom-right", 3, 0.9 );
   const frame = [ 0, 1, 2 ].map( c => Float32Array.from( { length: n }, ( _, i ) => 0.1 + 0.2*c + 0.5*( ( i % 23 )/23 ) ) );
   const exc = [ 0, 1, 2 ].map( c => Float32Array.from( { length: n }, ( _, i ) => ( i % 11 ) ? 0 : 0.3 + 0.1*c ) );
   out.addLogo = { frame: { W: W, H: H, planes: frame.map( plain ), excess: exc.map( plain ) },
                   source: { w: 40, h: 28, colour: cs.map( plain ), alpha: plain( al ), place: "bottom-right", opacity: 0.9 },
                   cases: [ 0.6, 0, undefined ].map( f =>
                   {
                      const o = frame.map( p => Float32Array.from( p ) ), e = exc.map( p => Float32Array.from( p ) );
                      Render.addLogo( L, o, e, W, f );
                      return { fade: f === undefined ? null : f, out: o.map( plain ), excess: e.map( plain ) };
                   } ) };
   return out;
};
