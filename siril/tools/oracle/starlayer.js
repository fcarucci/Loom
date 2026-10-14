/*
 * Oracle for loomsiril/starlayer.py (E3d): Sky.sprites, Sky.js 683-731, run whole on synthetic stars
 * layers (96 x 64, 3 channels) with their placed Gaia stars and neighbours.
 *
 * Each case builds the light of its stars (Gaussian cores, a faint wing, four spikes on each spiked star)
 * plus selftest's synthRandom noise. The detector is the real Sky.detections, fed by a stub StarDetector
 * that returns the stars flagged detect. The INPUTS of each case are stored in the JSON (planes, detector
 * output, sources, placed records, neighbours as indices into the sources list); Python replays
 * starlayer.sprites from them. Nothing is rebuilt there with a formula.
 *
 * Cases:
 *  - "field": the main field. A bright spiked star with a faint placed star on the spike (its own spikes
 *    are fitted from its light), an undetected unplaced star inside the bright star's core (attach to
 *    cores), an undetected unplaced star on a bright glow (second pass: unexplained sources, add to sums),
 *    a bright detected star that is not placed with a faint placed star in its reach (drop inside brighter:
 *    the brighter star's detection counts, not its sprite), a blend pair, a placed star with no light
 *    under it (unmatched), an unplaced detection.
 *  - "spiked": a bright spiked star and a spiky faint placed star on its 45 degree spike (drop borrowed
 *    spikes). Kept apart from "field": a faint detection next to the bright star's spike rays makes the
 *    detector's spike directions come out empty (Sky.spikeDirections weights each star by 1/flux), which
 *    would leave the spike stages with nothing to do.
 */
"use strict";

module.exports = function( js )
{
   const Fly = js.Fly, Sky = js.Sky, Render = js.Render;

   // selftest.js synthRandom: a seeded uniform generator on [0, 1)
   function synthRandom( seed )
   {
      let a = seed >>> 0;
      return function()
      {
         a = ( a + 0x6D2B79F5 ) >>> 0;
         let t = Math.imul( a ^ ( a >>> 15 ), 1 | a );
         t = ( t + Math.imul( t ^ ( t >>> 7 ), 61 | t ) ) ^ t;
         return ( ( t ^ ( t >>> 14 ) ) >>> 0 )/4294967296;
      };
   }

   const W = 96, H = 64, NC = 3, FWHM = 3;
   const sig = FWHM/2.3548;
   const SPIKE_ANGLES = [ 45, 135, 225, 315 ].map( d => d*Math.PI/180 );

   // Rect stub: Sky.intensity, Render.channels and the sprite code build `new Rect(...)` for getSamples,
   // which the fake image ignores; the node shim has no PJSR Rect.
   function withRectStub( fn )
   {
      const PrevRect = globalThis.Rect, PrevDetector = globalThis.StarDetector;
      globalThis.Rect = function( x0, y0, x1, y1 ) { this.x0 = x0; this.y0 = y0; this.x1 = x1; this.y1 = y1; };
      try { return fn(); }
      finally { globalThis.Rect = PrevRect; globalThis.StarDetector = PrevDetector; }
   }

   // one run of Sky.sprites: stars = { x, y, flux, col, spikes, detect (default true), plx (Gaia parallax, absent = not placed) }
   function run( stars, rec )
   {
      const rnd = synthRandom( rec.seed );
      const planes = [];
      for ( let c = 0; c < NC; ++c )
      {
         const b = new Float32Array( W*H );
         for ( let y = 0; y < H; ++y )
            for ( let x = 0; x < W; ++x )
            {
               let v = 0.05;
               for ( const s of stars )
               {
                  const dx = x - s.x, dy = y - s.y, r2 = dx*dx + dy*dy;
                  v += s.flux*s.col[c]*( Math.exp( -r2/( 2*sig*sig ) ) + 0.01*Math.exp( -Math.sqrt( r2 )/4 ) );
                  if ( s.spikes )
                     for ( const ang of SPIKE_ANGLES )
                     {
                        const along = dx*Math.cos( ang ) + dy*Math.sin( ang ), lat = -dx*Math.sin( ang ) + dy*Math.cos( ang );
                        if ( along > 0 && along < 40 ) v += 0.4*s.flux*s.col[c]*Math.exp( -along/18 )*Math.exp( -lat*lat/( 2*0.9*0.9 ) );
                     }
               }
               const u = Math.max( 1e-12, rnd() ), q = rnd();
               v += 0.004*Math.sqrt( -2*Math.log( u ) )*Math.cos( 2*Math.PI*q );
               b[y*W + x] = v;
            }
         planes.push( b );
      }
      const image = {
         width: W, height: H, numberOfChannels: NC,
         getSamples: function( buf, rect, c ) { for ( let i = 0; i < W*H; ++i ) buf[i] = planes[c][i]; }
      };

      // the detector's raw output (StarDetector.stars): position, flux, size
      const raw = stars.filter( s => s.detect !== false ).map( s => ( { pos: { x: s.x, y: s.y }, flux: s.flux, size: 30, nmax: 0 } ) );

      // Gaia sources: one entry per catalogue source; Placed and neighbours index into it
      const sources = [];
      const placed = [];
      const neighbours = [];
      stars.forEach( ( s, k ) =>
      {
         const G = 15 - 3*s.flux;
         const plx = s.plx !== undefined ? s.plx : NaN;
         sources.push( { ra: 1 + k, dec: 2, plx: plx, pmra: 0, pmdec: 0, G: G, BP: G + 0.4, RP: G - 0.5 } );
         if ( s.plx !== undefined ) placed.push( { src: k, d: 1000/s.plx, x: s.x + 0.3, y: s.y - 0.2 } );
         neighbours.push( { src: k, x: s.x + ( s.plx !== undefined ? 0.3 : 0 ), y: s.y - ( s.plx !== undefined ? 0.2 : 0 ) } );
      } );
      for ( const extra of rec.extraPlaced || [] ) { sources.push( extra.source ); const k = sources.length - 1;
         placed.push( { src: k, d: extra.d, x: extra.x, y: extra.y } ); neighbours.push( { src: k, x: extra.x, y: extra.y } ); }
      for ( const extra of rec.extraNeighbours || [] ) { sources.push( extra.source ); neighbours.push( { src: sources.length - 1, x: extra.x, y: extra.y } ); }

      return withRectStub( () =>
      {
         globalThis.StarDetector = function() { this.stars = function() { return raw; }; };
         // Fly.catalogueBlend compares sources by identity: each Placed and neighbour gets the SAME object
         const placedJs = placed.map( p => ( { source: sources[p.src], d: p.d, x: p.x, y: p.y } ) );
         const neighboursJs = neighbours.map( n => ( { source: sources[n.src], x: n.x, y: n.y } ) );
         const dets = Sky.detections( image );
         const calls = [];
         const out = Sky.sprites( image, placedJs, FWHM, neighboursJs, function( t, a, b ) { calls.push( [ t, a, b ] ); } );
         return {
            field: { w: W, h: H, nc: NC, fwhm: FWHM, planes: planes,
                     dets: dets.map( d => ( { index: d.index, x: d.x, y: d.y, flux: d.flux, nmax: d.nmax,
                                              rect: { x0: d.rect.x0, y0: d.rect.y0, x1: d.rect.x1, y1: d.rect.y1 } } ) ),
                     sources: sources, placed: placed, neighbours: neighbours },
            out: {
               blended: out.blended, unmatched: out.unmatched, detected: out.detected, nspr: out.sprites.length,
               residual: out.residual,
               sprites: out.sprites.map( s => ( { rect: { x0: s.rect.x0, y0: s.rect.y0, x1: s.rect.x1, y1: s.rect.y1 }, radius: s.radius,
                        centre: s.centre, d: s.d, plx: s.placed.source.plx, mask: s.mask, pixels: s.pixels, modelCore: s.modelCore,
                        modelSpikes: s.modelSpikes, spikeAngles: s.spikeAngles } ) )
            },
            calls: calls
         };
      } );
   }

   const FIELD = [
      { x: 30.6, y: 22.4, flux: 1.0, col: [ 1.0, 0.8, 0.6 ], spikes: true, plx: 2.0 },        // bright, spiked, placed
      { x: 39.1, y: 30.9, flux: 0.3, col: [ 1.0, 0.9, 0.7 ], spikes: false, plx: 3.5 },       // faint, placed, on the 45 degree spike
      { x: 32.1, y: 23.6, flux: 0.5, col: [ 0.7, 1.0, 0.8 ], spikes: false, detect: false },   // undetected, in the bright star's core
      { x: 56.2, y: 9.3, flux: 0.5, col: [ 1.0, 1.0, 1.0 ], spikes: false, plx: 3.0 },
      { x: 50.2, y: 14.3, flux: 0.15, col: [ 0.8, 0.7, 0.9 ], spikes: false, detect: false },  // undetected, on the glow of the star above
      { x: 70.7, y: 40.1, flux: 0.6, col: [ 0.6, 0.8, 1.0 ], spikes: false, plx: 5.0 },
      { x: 9.4, y: 50.1, flux: 0.45, col: [ 0.9, 0.9, 0.5 ], spikes: false, plx: 8.0 },
      { x: 80.2, y: 9.3, flux: 0.5, col: [ 1.0, 1.0, 1.0 ], spikes: false, plx: 3.0 },        // blend pair with the next
      { x: 84.1, y: 12.4, flux: 0.25, col: [ 1.0, 1.0, 1.0 ], spikes: false, plx: 1.2 },
      { x: 20.0, y: 8.0, flux: 0.35, col: [ 1, 0.9, 0.8 ], spikes: false },                 // detected, not placed
      { x: 62.0, y: 54.0, flux: 1.0, col: [ 0.9, 0.8, 1.0 ], spikes: false },              // bright, detected, not placed
      { x: 52.0, y: 54.0, flux: 0.3, col: [ 1.0, 1.0, 0.8 ], spikes: false, plx: 4.0 }     // faint, placed, in that star's reach
   ];
   const field = run( FIELD, {
      seed: 9,
      // a placed star with no light under it (unmatched), and a catalogue source with no parallax near the blend pair
      extraPlaced: [ { source: { ra: 40, dec: 2, plx: 4, pmra: 0, pmdec: 0, G: 17, BP: NaN, RP: NaN }, d: 250, x: 40, y: 55 } ],
      extraNeighbours: [ { source: { ra: 88, dec: 3, plx: NaN, pmra: NaN, pmdec: NaN, G: 19.5, BP: NaN, RP: NaN }, x: 88.5, y: 30.5 } ]
   } );

   const SPIKED = [
      { x: 30.6, y: 22.4, flux: 1.0, col: [ 1.0, 0.8, 0.6 ], spikes: true, plx: 2.0 },
      { x: 39.1, y: 30.9, flux: 0.3, col: [ 1.0, 0.9, 0.7 ], spikes: true, plx: 3.5 }        // spiky too: its own spikes are fitted
   ];
   const spiked = run( SPIKED, { seed: 13 } );

   return { field: field.field, out: field.out, calls: field.calls,
            spiked: { field: spiked.field, out: spiked.out, calls: spiked.calls } };
};
