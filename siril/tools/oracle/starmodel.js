/*
 * Oracle for loomsiril/starmodel.py (E3b): the star-model rules (Fly.js 1825-2035)
 * and the image helpers (Sky.js 585-770, 971-983).
 *
 * Every case stores its own inputs (profiles, buffers, detection records), so the
 * Python suite replays the JS on the same numbers and rebuilds nothing. Random
 * inputs follow selftest.js (synthRandom, the same seeds and loops) where it has
 * them. Image buffers are Float32Array, as the JS image buffers are; detection
 * records keep the JS field names (fromGaia, rect {x0, y0, x1, y1}).
 */
"use strict";

module.exports = function( js )
{
   const Fly = js.Fly, Sky = js.Sky;

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
   // selftest.js gauss(): Box-Muller on two draws
   const gauss = rnd => { const u = Math.max( 1e-12, rnd() ), v = rnd(); return Math.sqrt( -2*Math.log( u ) )*Math.cos( 2*Math.PI*v ); };
   const g2 = ( x, y, cx, cy, a, s ) => a*Math.exp( -( ( x - cx )*( x - cx ) + ( y - cy )*( y - cy ) )/( 2*s*s ) );
   const image = ( w, h, f ) => { const b = new Float32Array( w*h ); for ( let y = 0; y < h; ++y ) for ( let x = 0; x < w; ++x ) b[y*w + x] = f( x, y ); return b; };
   const det = ( x, y, flux, area, fromGaia ) => ( { x, y, flux, rect: Fly.detectionRect( x, y, area ), fromGaia: !!fromGaia } );
   const rec = ( args, fn ) => ( { args: args, out: fn.apply( null, args ) } );

   const cases = {};

   // ---- Fly.spikeAngles: a 1-degree profile with spikes at 45, 135, 225, 315 (selftest seed 3)
   let rnd = synthRandom( 3 ), prof = [];
   for ( let a = 0; a < 360; ++a )
   {
      let v = 0.002*( rnd() - 0.5 );
      for ( const s of [ 45, 135, 225, 315 ] ) { const d = Math.min( Math.abs( a - s ), 360 - Math.abs( a - s ) ); v += 0.05*Math.exp( -d*d/2 ); }
      prof.push( v );
   }
   const flat = prof.map( () => 0.002*( rnd() - 0.5 ) );
   const oneSided = flat.map( ( v, a ) => { const d = Math.min( Math.abs( a - 10 ), 360 - Math.abs( a - 10 ) ); return v + 0.05*Math.exp( -d*d/2 ); } );
   cases.spikeAngles = [
      rec( [ prof, 0.001 ], Fly.spikeAngles ),
      rec( [ flat, 0.001 ], Fly.spikeAngles ),
      rec( [ oneSided, 0.001 ], Fly.spikeAngles ),
      rec( [ prof, 0.5 ], Fly.spikeAngles ),          // noise gate: 6 sigma above a 0.5 noise is out of reach
      rec( [ [], 0.001 ], Fly.spikeAngles )            // empty profile
   ];

   // ---- Fly.localPeak, through Sky.reader (clamped) over a 41 x 41 buffer
   const W = 41, H = 41;
   const halo = image( W, H, ( x, y ) => g2( x, y, 20, 20, 1, 6 ) );
   const withFaint = image( W, H, ( x, y ) => g2( x, y, 20, 20, 1, 6 ) + g2( x, y, 32, 20, 0.05, 1.5 ) );
   const spike = image( W, H, ( x, y ) => g2( x, y, 20, 20, 1, 6 ) + ( x > 20 && Math.abs( y - 20 ) < 1 ? 0.3*Math.exp( -( x - 20 )/30 ) : 0 ) );
   cases.localPeak = [
      rec( [ halo, W, H, 20, 20, 4 ], ( b, w, h, x, y, r ) => Fly.localPeak( Sky.reader( b, w, h ), x, y, r ) ),   // a centre is a peak
      rec( [ halo, W, H, 28, 20, 4 ], ( b, w, h, x, y, r ) => Fly.localPeak( Sky.reader( b, w, h ), x, y, r ) ),   // a slope is not
      rec( [ withFaint, W, H, 32, 20, 4 ], ( b, w, h, x, y, r ) => Fly.localPeak( Sky.reader( b, w, h ), x, y, r ) ), // a faint star on the halo's edge is
      rec( [ spike, W, H, 27, 20, 4 ], ( b, w, h, x, y, r ) => Fly.localPeak( Sky.reader( b, w, h ), x, y, r ) ),   // a bead on a spike is not
      rec( [ halo, W, H, 0, 0, 2 ], ( b, w, h, x, y, r ) => Fly.localPeak( Sky.reader( b, w, h ), x, y, r ) )       // a corner, clamped reads
   ];

   // ---- Fly.isFragment(d, dets, value, fwhm): value is Sky.reader over the buffer
   const steep = image( W, H, ( x, y ) => { const r2 = ( x - 12 )*( x - 12 ) + ( y - 20 )*( y - 20 ); return 0.9*Math.pow( 1 + r2/36, -1.5 ) + g2( x, y, 27, 20, 0.1, 1 ); } );
   const sat = image( W, H, ( x, y ) => Math.min( 1, 3*g2( x, y, 20, 20, 1, 6 ) ) );
   const faintOnly = image( W, H, ( x, y ) => g2( x, y, 36, 20, 0.05, 1.5 ) );
   const frag = ( d, dets, b, w, h, px ) => Fly.isFragment( d, dets, Sky.reader( b, w, h ), px );
   const bigAt = det( 20, 20, 100, 64*Math.PI );
   cases.isFragment = [
      rec( [ det( 27, 20, 0.6, 4*Math.PI ), [ det( 12, 20, 50, 36*Math.PI ), det( 27, 20, 0.6, 4*Math.PI ) ], steep, W, H, 3 ], frag ), // real star on a steep glow
      rec( [ det( 25, 20, 20, 9*Math.PI ), [ bigAt, det( 25, 20, 20, 9*Math.PI ) ], sat, W, H, 3 ], frag ),                       // inside a flat core
      rec( [ bigAt, [ bigAt, det( 25, 20, 20, 9*Math.PI ) ], sat, W, H, 3 ], frag ),                                               // the bright star itself
      rec( [ det( 36, 20, 0.5, 4*Math.PI ), [ det( 4, 20, 100, 64*Math.PI ), det( 36, 20, 0.5, 4*Math.PI ) ], faintOnly, W, H, 3 ], frag ), // real faint neighbour, out of the bright core
      rec( [ det( 28, 20, 5, 4*Math.PI ), [ bigAt, det( 28, 20, 5, 4*Math.PI ) ], halo, W, H, 3 ], frag ),                          // on the slope
      rec( [ det( 28, 20, 5, 4*Math.PI, true ), [ bigAt, det( 28, 20, 5, 4*Math.PI, true ) ], halo, W, H, 3 ], frag )               // catalogue star: never a fragment
   ];

   // ---- Fly.attachToCores(sprites, extras): fresh records per call (the JS mutates model.parts)
   const attachCase = ( sprites, extras ) => {
      const mk = () => ( { sp: sprites.map( m => ( { det: { model: { x: m.x, y: m.y, core: m.core } } } ) ), ex: extras.map( e => ( { x: e.x, y: e.y } ) ) } );
      const s = mk(), kept = Fly.attachToCores( s.sp, s.ex );
      return { args: [ sprites, extras ],
               out: { kept: kept.map( e => s.ex.indexOf( e ) ),
                      parts: s.sp.map( q => q.det.model.parts ? q.det.model.parts.map( e => s.ex.indexOf( e ) ) : null ) } };
   };
   cases.attachToCores = [
      attachCase( [ { x: 100, y: 100, core: 6 } ], [ { x: 104.5, y: 100 }, { x: 112, y: 100 } ] ),
      attachCase( [ { x: 100, y: 100, core: 6 }, { x: 106, y: 100, core: 10 } ], [ { x: 104, y: 100 }, { x: 99, y: 100 }, { x: 300, y: 300 } ] ),
      attachCase( [], [ { x: 1, y: 1 } ] ),
      attachCase( [ { x: 0, y: 0, core: 3 } ], [] )
   ];

   // ---- Fly.lateralSigma(p, step): a Gaussian spike over a 0.3 baseline (selftest sigmas)
   const lat = ( sig, step, lo, hi ) => { const p = []; for ( let l = lo; l <= hi + 1e-9; l += step ) p.push( 0.3 + Math.exp( -l*l/( 2*sig*sig ) ) ); return p; };
   cases.lateralSigma = [
      rec( [ lat( 0.7, 0.5, -8, 8 ), 0.5 ], Fly.lateralSigma ),
      rec( [ lat( 1.6, 0.5, -8, 8 ), 0.5 ], Fly.lateralSigma ),
      rec( [ lat( 2.5, 0.5, -8, 8 ), 0.5 ], Fly.lateralSigma ),
      rec( [ lat( 1.0, 0.25, -4, 4 ), 0.25 ], Fly.lateralSigma ),
      rec( [ lat( 1e9, 0.5, -8, 8 ), 0.5 ], Fly.lateralSigma )                                    // flat: no peak -> null
   ];

   // ---- Fly.dropBorrowedSpikes(dets): fresh records per call; out summarises what it rewrote
   const sp = ( angle, start, reach ) => ( { angle, start, reach } );
   const summarize = d => ( {
      spikes: d.model.spikes.map( s => ( { angle: s.angle, reach: s.reach } ) ),
      support: d.model.support,
      halo: d.halo ? { radius: d.halo.radius, outer: d.halo.outer, spikes: d.halo.spikes.map( s => ( { angle: s.angle, reach: s.reach } ) ) } : null
   } );
   const borrowCase = make => {
      const args = make(), dets = make();
      Fly.dropBorrowedSpikes( dets );
      return { args: [ args ], out: dets.map( summarize ) };
   };
   const selftestTrio = () => {
      const model = ( x, y, spikes, rmax ) => ( { x, y, rmax, spikeSigma: 1.4, support: 0, spikes: spikes.map( a => sp( a[0], 5, a[1] ) ) } );
      return [
         { flux: 2, model: model( 50, 0.8, [ [ 0, 60 ], [ Math.PI, 60 ], [ Math.PI/2, 12 ] ], 8 ), halo: { radius: 60, halo: null, outer: 60, spikes: [ { angle: 0, reach: 60 }, { angle: Math.PI, reach: 60 }, { angle: Math.PI/2, reach: 12 } ] } },
         { flux: 100, model: model( 0, 0, [ [ 0, 120 ], [ Math.PI/2, 120 ] ], 40 ) },
         { flux: 2, model: model( 50, 12, [ [ 0, 30 ] ], 8 ), halo: { radius: 30, halo: null, outer: 30, spikes: [ { angle: 0, reach: 30 } ] } }
      ];
   };
   cases.dropBorrowedSpikes = [
      borrowCase( selftestTrio ),
      borrowCase( () => [
         { flux: 100, model: { x: 0, y: 0, rmax: 10, spikeSigma: 1.4, support: 0, spikes: [ sp( 0, 5, 50 ) ] } },
         { flux: 1, model: { x: 300, y: 300, rmax: 10, spikeSigma: 1.4, support: 0, spikes: [ sp( 0, 5, 50 ) ] } } ] ),
      borrowCase( () => [                                    // no spikeSigma: half-width 3
         { flux: 100, model: { x: 0, y: 0, rmax: 10, support: 0, spikes: [ sp( 0, 5, 120 ) ] } },
         { flux: 2, model: { x: 50, y: 2.5, rmax: 10, support: 0, spikes: [ sp( 0, 5, 40 ) ] } } ] ),
      borrowCase( () => [                                    // 4.5 px off a 1.4-sigma spike: outside 3*1.4
         { flux: 100, model: { x: 0, y: 0, rmax: 10, spikeSigma: 1.4, support: 0, spikes: [ sp( 0, 5, 120 ) ] } },
         { flux: 2, model: { x: 50, y: 4.5, rmax: 10, spikeSigma: 1.4, support: 0, spikes: [ sp( 0, 5, 40 ) ] } } ] )
   ];

   // ---- Fly.fitSpike / fitSpikePair: T is the falloff, e the excess (selftest seeds 9 and 13)
   const T = []; for ( let r = 0; r < 400; ++r ) T.push( r < 5 ? 1 : 25/( r*r ) );
   {
      const r9 = synthRandom( 9 ), e = [], none = [];
      for ( let k = 0; k < 300; ++k ) e.push( 0.2*T[10 + k] + ( k >= 80 && k < 150 ? 0.05 : 0 ) + 1.22*0.001*gauss( r9 ) );
      for ( let k = 0; k < 300; ++k ) none.push( 1.22*0.001*gauss( r9 ) );
      const T2 = T.slice(); T2[12] = 0;
      cases.fitSpike = [
         rec( [ e, T, 10, 0.001 ], Fly.fitSpike ),
         rec( [ none, T, 10, 0.001 ], Fly.fitSpike ),
         rec( [ e, T2, 10, 0.001 ], Fly.fitSpike ),         // a zero falloff sample is skipped
         rec( [ e, T, 390, 0.001 ], Fly.fitSpike )          // the spike runs off the falloff's end
      ];
   }
   {
      const r13 = synthRandom( 13 ), good = [], masked = [], none = [], none2 = [];
      for ( let k = 0; k < Fly.SPIKE_FIT; ++k )
      {
         good.push( 0.2*T[10 + k] + 1.22*0.001*gauss( r13 ) );
         masked.push( -0.01 + 1.22*0.001*gauss( r13 ) );
         none.push( 1.22*0.001*gauss( r13 ) ); none2.push( 1.22*0.001*gauss( r13 ) );
      }
      cases.fitSpikePair = [
         rec( [ good, masked, T, 10, 0.001 ], Fly.fitSpikePair ),
         rec( [ masked, good, T, 10, 0.001 ], Fly.fitSpikePair ),
         rec( [ none, none2, T, 10, 0.001 ], Fly.fitSpikePair )
      ];
   }

   // ---- Fly.localProfile(raw, minR, noise)
   const raw150 = []; for ( let r = 0; r < 150; ++r ) raw150.push( 0.003 + 0.2*Math.exp( -r*r/8 ) );
   cases.localProfile = [
      rec( [ raw150, 4, 0.0004 ], Fly.localProfile ),
      rec( [ raw150, 20, 0.0004 ], Fly.localProfile ),
      rec( [ Array.from( { length: 40 }, ( _, r ) => 0.5 - 0.001*r ), 4, 0.0004 ], Fly.localProfile ),   // never settles
      rec( [ Array.from( { length: 30 }, () => 0.01 ), 4, 0.0004 ], Fly.localProfile )                    // flat: settles at minR
   ];

   // ---- Fly.deblend(Mi, Msum, L, sigma)
   cases.deblend = [
      rec( [ 0.6, 0.8, 1.0, 0.004 ], Fly.deblend ),
      rec( [ 0.001, 0.001, 0.0045, 0.004 ], Fly.deblend ),
      rec( [ 0.3, 0.5, 0.7, 0.004 ], Fly.deblend ),
      rec( [ 0.2, 0.5, 0.7, 0.004 ], Fly.deblend ),
      rec( [ 0, 0, 0.5, 0.004 ], Fly.deblend ),
      rec( [ 0.01, 0.01, 1.0, 0.004 ], Fly.deblend ),
      rec( [ 0.5, 0.5, 0.4, 0 ], Fly.deblend )              // sigma 0: Msum/sigma is Infinity
   ];

   // ---- Sky.ringStart(lo, dy), Sky.otherStar(labels, i, self)
   cases.ringStart = [ [ 6.25, 0 ], [ 6.25, 2 ], [ 6.25, 3 ], [ 20.25, -4 ], [ 0.25, 5 ], [ 12.25, -2 ] ]
      .map( a => rec( a, Sky.ringStart ) );
   {
      const lw = 4, lh = 3, lab = Int32Array.from( [ -1, 0, 2, -1,  0, 0, -1, 1,  -1, -1, 0, 2 ] );
      cases.otherStar = [
         rec( [ lab, lw, 0, 2, 0 ], ( l, w, y, x, s ) => Sky.otherStar( l, y*w + x, s ) ),   // another star: true
         rec( [ lab, lw, 0, 1, 0 ], ( l, w, y, x, s ) => Sky.otherStar( l, y*w + x, s ) ),   // own pixel: false
         rec( [ lab, lw, 0, 0, 0 ], ( l, w, y, x, s ) => Sky.otherStar( l, y*w + x, s ) ),   // own pixel: false
         rec( [ lab, lw, 1, 0, 0 ], ( l, w, y, x, s ) => Sky.otherStar( l, y*w + x, s ) ),   // background (-1): false
         rec( [ null, lw, 1, 0, 0 ], ( l, w, y, x, s ) => Sky.otherStar( l, y*w + x, s ) )   // unlabelled: false
      ];
   }

   // ---- Sky.ringStats(buf, w, h, cx, cy, r, labels, self): a Gaussian star with noise (seed 21)
   {
      const r21 = synthRandom( 21 );
      const buf = image( W, H, ( x, y ) => g2( x, y, 20, 20, 1, 3 ) + 0.01*( r21() - 0.5 ) );
      const labels = new Int32Array( W*H ).fill( -1 );
      for ( let y = 17; y <= 23; ++y ) for ( let x = 25; x <= 31; ++x ) labels[y*W + x] = 7;     // another star's core
      const rs = ( b, w, h, cx, cy, r, l, s ) => Sky.ringStats( b, w, h, cx, cy, r, l, s );
      cases.ringStats = [
         rec( [ buf, W, H, 20, 20, 5, null, 0 ], rs ),
         rec( [ buf, W, H, 20, 20, 6, labels, 0 ], rs ),     // the other star's pixels are skipped
         rec( [ buf, W, H, 20, 20, 1, null, 0 ], rs ),
         rec( [ buf, W, H, -30, 20, 5, null, 0 ], rs ),      // off the image
         rec( [ buf, W, H, 20, 20, 60, null, 0 ], rs ),      // a ring around the image: no pixel
         rec( [ buf, W, H, 2, 20, 8, labels, 0 ], rs )       // half on the image
      ];
   }

   // ---- Sky.coreLabels(dets, w, h, fwhm): a 30 x 24 image, so w and h cannot be swapped
   const coreDets = [ det( 10, 10, 50, 9*Math.PI ), det( 12, 10, 5, 4*Math.PI ), det( 28, 22, 3, 9*Math.PI ) ];
   cases.coreLabels = [
      rec( [ coreDets, 30, 24, 3 ], ( d, w, h, f ) => Array.from( Sky.coreLabels( d, w, h, f ) ) ),
      rec( [ [], 30, 24, 3 ], ( d, w, h, f ) => Array.from( Sky.coreLabels( d, w, h, f ) ) ),
      rec( [ [ det( 0.5, 0.5, 1, Math.PI ) ], 30, 24, 2 ], ( d, w, h, f ) => Array.from( Sky.coreLabels( d, w, h, f ) ) )
   ];

   // ---- Sky.reader(buf, w, h)(x, y): clamped reads
   const rbuf = Float32Array.from( { length: 20 }, ( _, i ) => 0.05*i + 0.001*( ( i*7 ) % 5 ) );
   cases.reader = [ [ -3, 2 ], [ 7, 2 ], [ 2, -1 ], [ 2, 9 ], [ 4, 3 ], [ 0, 0 ] ]
      .map( q => rec( [ rbuf, 5, 4, q[0], q[1] ], ( b, w, h, x, y ) => Sky.reader( b, w, h )( x, y ) ) );

   // ---- Sky.sparseMedian(buf) (flat buffers; every 7th sample)
   {
      const r31 = synthRandom( 31 );
      const glow = Float32Array.from( { length: 1000 }, ( _, i ) => 0.2 + 0.01*( r31() - 0.5 ) + ( i >= 500 && i < 600 ? 0.5 : 0 ) );
      const busy = Float32Array.from( { length: 1000 }, ( _, i ) => ( i % 3 == 0 ? 0.8 : 0.1 + 0.01*( r31() - 0.5 ) ) );
      cases.sparseMedian = [
         rec( [ glow ], Sky.sparseMedian ),
         rec( [ busy ], Sky.sparseMedian ),
         rec( [ new Float32Array( 100 ).fill( 0.5 ) ], Sky.sparseMedian ),
         rec( [ Float32Array.from( [ 0.25, 0.9, 0.1 ] ) ], Sky.sparseMedian )
      ];
   }

   // ---- Sky.bilinear(buf, w, h, x, y)
   {
      const bw = 6, bh = 5, bb = Float32Array.from( { length: bw*bh }, ( _, i ) => 0.1*( ( i*13 ) % 11 ) );
      const bl = ( b, w, h, x, y ) => Sky.bilinear( b, w, h, x, y );
      cases.bilinear = [ [ 2.25, 1.5 ], [ 0, 0 ], [ -0.5, 2 ], [ 5.5, 3.5 ], [ -3, -3 ], [ 4, 4 ], [ NaN, 1 ] ]
         .map( q => rec( [ bb, bw, bh, q[0], q[1] ], bl ) );
   }

   // ---- Sky.smoothProfile(prof, from)
   {
      const r37 = synthRandom( 37 ), p = Array.from( { length: 30 }, () => r37() ).map( ( v, r ) => v*Math.exp( -r/10 ) );
      const sm = ( q, f ) => Sky.smoothProfile( q, f );
      cases.smoothProfile = [ rec( [ p, 0 ], sm ), rec( [ p, 5 ], sm ), rec( [ p, 28 ], sm ) ];
   }

   // ---- Sky.intensity(image): a fake image whose getSamples copies the plane (the JS reads it that way)
   {
      const r51 = synthRandom( 51 );
      const planes3 = [ 0, 1, 2 ].map( () => Float32Array.from( { length: 12*9 }, () => r51() ) );
      const planes1 = [ Float32Array.from( { length: 5*4 }, () => r51() ) ];
      const inten = ( planes, w, h ) => {
         const img = { width: w, height: h, numberOfChannels: planes.length,
                       getSamples: ( b, rect, c ) => { b.set( planes[c] ); } };
         // Sky.intensity only passes a Rect to getSamples, which ignores it: a plain record stands in for PJSR's Rect
         const shimmed = Object.getOwnPropertyDescriptor( global, "Rect" );
         global.Rect = function( x0, y0, x1, y1 ) { this.x0 = x0; this.y0 = y0; this.x1 = x1; this.y1 = y1; };
         try { return Sky.intensity( img ); }
         finally { if ( shimmed ) Object.defineProperty( global, "Rect", shimmed ); }
      };
      cases.intensity = [ rec( [ planes3, 12, 9 ], inten ), rec( [ planes1, 5, 4 ], inten ) ];
   }

   // ---- Sky.noiseSigma(buf): 1.4826 MAD of every 7th sample, 0 when the MAD is 0
   {
      const r41 = synthRandom( 41 );
      const noisy = Float32Array.from( { length: 1000 }, () => 0.2 + 0.05*gauss( r41 ) );
      cases.noiseSigma = [
         rec( [ noisy ], Sky.noiseSigma ),
         rec( [ new Float32Array( 500 ).fill( 0.2 ) ], Sky.noiseSigma ),
         rec( [ image( W, H, ( x, y ) => g2( x, y, 20, 20, 1, 3 ) ) ], Sky.noiseSigma )
      ];
   }

   // ---- constants
   cases.constants = [ { args: [], out: { FWHM_DEFAULT: Sky.FWHM_DEFAULT, HALO_MAX: Sky.HALO_MAX, SPIKE_MAX: Sky.SPIKE_MAX,
                                          SPIKE_WINDOW: Fly.SPIKE_WINDOW, SPIKE_FIT: Fly.SPIKE_FIT, SETTLE_WINDOW: Fly.SETTLE_WINDOW,
                                          DEBLEND_CAP: Fly.DEBLEND_CAP } } ];

   return cases;
};
