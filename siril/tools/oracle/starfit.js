/*
 * Oracle for loomsiril/starfit.py (E3c): the star-model fitting and deblending of
 * Sky.js 683-731 (the env part of Sky.sprites) and 771-1130.
 *
 * One synthetic stars layer (64 x 48, 3 channels, five Gaussian stars, the
 * brightest with four spikes along 45/135/225/315 degrees, plus selftest's
 * synthRandom noise) is built here and run through the same Sky.sprites steps
 * as the JS driver. The field's INPUT planes, detections and env are stored in
 * the JSON (case "field"); every later case is replayed from them in Python, so
 * nothing is rebuilt there with a formula.
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

   // Sky.intensity and Render.channels build `new Rect(...)` only to pass it to getSamples, which the
   // fake image below ignores. The node shim has no PJSR Rect, so a plain record stands in for it here.
   const PrevRect = globalThis.Rect;
   globalThis.Rect = function( x0, y0, x1, y1 ) { this.x0 = x0; this.y0 = y0; this.x1 = x1; this.y1 = y1; };

   const W = 64, H = 48, NC = 3, FWHM = 3;
   const rnd = synthRandom( 7 );
   const stars = [
      { x: 30.6, y: 22.4, flux: 1.0, col: [ 1.0, 0.8, 0.6 ], spikes: true },
      { x: 50.7, y: 36.1, flux: 0.6, col: [ 0.6, 0.8, 1.0 ], spikes: false },
      { x: 56.2, y: 9.3, flux: 0.5, col: [ 1.0, 1.0, 1.0 ], spikes: false },
      { x: 9.4, y: 8.1, flux: 0.45, col: [ 0.9, 0.9, 0.5 ], spikes: false },
      { x: 50.2, y: 14.3, flux: 0.15, col: [ 0.8, 0.7, 0.9 ], spikes: false, detect: false }
   ];
   const sig = FWHM/2.3548;
   const SPIKE_ANGLES = [ 45, 135, 225, 315 ].map( d => d*Math.PI/180 );

   // the stars layer: background + noise, Gaussian cores with a faint wing, spikes on the first star
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

   // Sky.sprites, Sky.js 683-701: the env part (no fragment filter: every star is a detection)
   const I = Sky.intensity( image ), T = Render.channels( image );
   // the faint fifth star is in the light but not detected: the second pass (unexplained sources) must find it
   const dets = stars.filter( s => s.detect !== false ).map( ( s, k ) => ( { index: k, x: s.x, y: s.y, flux: s.flux, rect: Fly.detectionRect( s.x, s.y, 30 ), nmax: 0, fromGaia: false } ) );
   const px = Math.max( 1, Math.round( FWHM ) );
   const env = { I: I, T: T, w: W, h: H, nc: NC, fwhm: px, labels: Sky.coreLabels( dets, W, H, px ), noise: Sky.noiseSigma( I ),
                 sky: T.map( Sky.sparseMedian ), skyI: Sky.sparseMedian( I ) };
   env.spikes = Sky.spikeDirections( env, dets );
   env.spikeSigma = Sky.spikeWidth( env, dets, env.spikes );
   env.spikeFalloff = Sky.spikeFalloff( env, dets, env.spikes );

   globalThis.Rect = PrevRect;
   const cases = {};
   const field = { w: W, h: H, nc: NC, fwhm: px, I: I, T: T, labels: env.labels, noise: env.noise, sky: env.sky, skyI: env.skyI,
                   spikes: env.spikes, spikeSigma: env.spikeSigma, spikeFalloff: env.spikeFalloff,
                   dets: dets.map( d => ( { index: d.index, x: d.x, y: d.y, flux: d.flux, rect: d.rect, nmax: d.nmax, fromGaia: d.fromGaia } ) ) };
   cases.field = field;
   // the stars layer as the caller holds it (make_env input) and the spike inputs of spikeDirections/spikeWidth
   cases.makeEnv = { planes: planes, dets: field.dets, fwhm: px,
                     out: { noise: env.noise, sky: env.sky, skyI: env.skyI, spikes: env.spikes, spikeSigma: env.spikeSigma,
                            spikeFalloff: env.spikeFalloff, labels: env.labels, I: I } };
   cases.spikeDirections = { out: Sky.spikeDirections( env, dets ) };
   cases.spikeWidth = { angles: env.spikes, out: Sky.spikeWidth( env, dets, env.spikes ) };
   cases.spikeFalloff = { angles: env.spikes, out: env.spikeFalloff };
   cases.spikeWidthNoAngles = { out: Sky.spikeWidth( env, dets, [] ) };

   // ---- Sky.spikeExcess over the intensity field, every argument shape the JS uses
   cases.spikeExcess = [
      [ 20.3, 15.6, 45*Math.PI/180, 9.5 ], [ 20.3, 15.6, 135*Math.PI/180, 14 ], [ 44.7, 30.2, 0.3, 4.2, 2.5 ],
      [ 10.2, 5.3, 1.9, 3 ], [ 62.8, 46.9, 0.0, 6 ], [ 30, 20, 0.7, 5.5, 1.2 ]
   ].map( a => ( { args: a, out: Sky.spikeExcess( I, W, H, a[0], a[1], a[2], a[3], a[4] ) } ) );

   cases.spikeSide = [ null, 0, 0.5, 1, 1.4, 2.5, undefined ].map( s => ( { args: [ s === undefined ? null : s ], out: Sky.spikeSide( s ) } ) );

   // Sky.smoothFalloff over a made-up averaged falloff (a rise, a dip, a long tail)
   const rawT = []; { const r2 = synthRandom( 11 ); for ( let r = 0; r < 60; ++r ) rawT.push( r < 4 ? 0 : 0.5*Math.exp( -r/9 ) + 0.02*( r2() - 0.5 ) ); }
   cases.smoothFalloff = [ [ rawT, 4 ], [ rawT, 0 ], [ rawT, 9 ] ].map( a => ( { args: [ a[0], a[1] ], out: Sky.smoothFalloff( a[0], a[1] ) } ) );

   // ---- Sky.starModel for every detection (sets d.halo), and the halo records
   const models = dets.map( d => Sky.starModel( env, d ) );
   dets.forEach( ( d, k ) => { d.model = models[k]; } );   // Sky.sprites' halo() stores d.model
   cases.starModel = dets.map( ( d, k ) => ( { det: k, out: models[k], halo: d.halo } ) );
   // a star with no spikes (its own spike list is empty) is star 2 (the faint, isolated one is still fitted)
   cases.starModelSpikeCount = models.map( m => m.spikes.length );

   // ---- model_at / radial_at / spike_at at chosen pixels, per model
   const pts = [ [ 20, 15 ], [ 21, 16 ], [ 25, 12 ], [ 30, 20 ], [ 44, 30 ], [ 10, 40 ], [ 2, 2 ], [ 40, 20 ], [ 52, 9 ], [ 20, 27 ] ];
   cases.modelAt = models.map( ( m, k ) => ( { det: k, pts: pts.map( p => ( {
      x: p[0], y: p[1], modelAt: Sky.modelAt( m, p[0], p[1] ), radialAt: Sky.radialAt( m, p[0], p[1] ), spikeAt: Sky.spikeAt( m, p[0], p[1] ) } ) ) } ) );

   // ---- Sky.modelSums over every detection's model, per channel
   // (the JS sums are Float32Arrays: stored as plain arrays)
   // ---- Sky.unexplainedSources: sprites are the detections (a SpriteMatch needs only det here)
   const sprites = dets.map( ( d, k ) => ( { det: d, placed: { source: { G: 10 + k }, d: 100 + k, x: d.x, y: d.y }, rect: d.rect } ) );
   const sums = Sky.modelSums( env, dets );
   cases.modelSums = { out: sums.map( p => p.slice() ) };   // copies: addToSums below changes sums
   cases.modelSumsOfOne = { det: 0, out: Sky.modelSums( env, [ dets[0] ] ) };

   const extra = Sky.unexplainedSources( env, sprites, sums );
   cases.unexplainedSources = { out: extra, sprites: sprites.map( s => s.det.index ) };
   if ( extra.length ) Sky.addToSums( env, extra, sums );
   cases.addToSums = { out: sums, extra: extra.length };

   // ---- Sky.pointModel for fixed peaks, over the sums before the second pass
   cases.pointModel = [ [ 20, 15, 0.8 ], [ 44, 30, 0.2 ], [ 3, 4, 0.05 ] ].map( a => ( {
      args: a, out: Sky.pointModel( env, cases.modelSums.out, a[0], a[1], a[2] ) } ) );

   // ---- Sky.excessPeak over a grid of excess values
   const exGrid = ( x, y ) => ( x === 5 && y === 5 ) ? 0.9 : ( x === 6 && y === 5 ) ? 0.2 : 0.1*( ( x + 2*y ) % 5 );
   cases.excessPeak = [ [ 5, 5, 0.9 ], [ 5, 5, 0.5 ], [ 6, 5, 0.2 ], [ 8, 8, 0.0 ] ].map( a => ( {
      args: a, out: Sky.excessPeak( exGrid, a[0], a[1], a[2] ) } ) );

   // ---- Sky.deblendSprite for each sprite, in the JS driver's order, from a fresh residual
   const residual = T.map( b => b.slice() );
   const sprOut = sprites.map( s => Sky.deblendSprite( env, s, sums, residual ) );
   cases.deblendSprite = { out: sprOut.map( s => ( {
      rect: { x0: s.rect.x0, y0: s.rect.y0, x1: s.rect.x1, y1: s.rect.y1 }, radius: s.radius, centre: s.centre,
      mask: s.mask, pixels: s.pixels, modelCore: s.modelCore, modelSpikes: s.modelSpikes, spikeAngles: s.spikeAngles } ) ),
      residual: residual, stars: sums.length };

   // ---- the moving-star core case (Fly.attachToCores): a sixth, undetected star inside star 0's core.
   // The whole Sky.sprites sequence runs in the JS order (make_env, star_model, model_sums,
   // unexplained_sources, add_to_sums, attach_to_cores, deblend_sprite), and the Python side replays it.
   cases.parts = ( function()
   {
      const PS = [
         { x: 30.6, y: 22.4, flux: 1.0, col: [ 1.0, 0.8, 0.6 ], spikes: true },
         { x: 50.7, y: 36.1, flux: 0.6, col: [ 0.6, 0.8, 1.0 ], spikes: false },
         { x: 56.2, y: 9.3, flux: 0.5, col: [ 1.0, 1.0, 1.0 ], spikes: false },
         { x: 9.4, y: 8.1, flux: 0.45, col: [ 0.9, 0.9, 0.5 ], spikes: false },
         { x: 50.2, y: 14.3, flux: 0.15, col: [ 0.8, 0.7, 0.9 ], spikes: false, detect: false },
         { x: 32.1, y: 23.6, flux: 0.5, col: [ 0.7, 1.0, 0.8 ], spikes: false, detect: false }
      ];
      const r2 = synthRandom( 7 ), pl = [];
      for ( let c = 0; c < NC; ++c )
      {
         const b = new Float32Array( W*H );
         for ( let y = 0; y < H; ++y )
            for ( let x = 0; x < W; ++x )
            {
               let v = 0.05;
               for ( const s of PS )
               {
                  const dx = x - s.x, dy = y - s.y, q2 = dx*dx + dy*dy;
                  v += s.flux*s.col[c]*( Math.exp( -q2/( 2*sig*sig ) ) + 0.01*Math.exp( -Math.sqrt( q2 )/4 ) );
                  if ( s.spikes )
                     for ( const ang of SPIKE_ANGLES )
                     {
                        const along = dx*Math.cos( ang ) + dy*Math.sin( ang ), lat = -dx*Math.sin( ang ) + dy*Math.cos( ang );
                        if ( along > 0 && along < 40 ) v += 0.4*s.flux*s.col[c]*Math.exp( -along/18 )*Math.exp( -lat*lat/( 2*0.9*0.9 ) );
                     }
               }
               const u = Math.max( 1e-12, r2() ), q = r2();
               v += 0.004*Math.sqrt( -2*Math.log( u ) )*Math.cos( 2*Math.PI*q );
               b[y*W + x] = v;
            }
         pl.push( b );
      }
      const img = { width: W, height: H, numberOfChannels: NC,
                    getSamples: function( buf, rect, c ) { for ( let i = 0; i < W*H; ++i ) buf[i] = pl[c][i]; } };
      const Prev = globalThis.Rect;
      globalThis.Rect = function( x0, y0, x1, y1 ) { this.x0 = x0; this.y0 = y0; this.x1 = x1; this.y1 = y1; };
      const In = Sky.intensity( img ), Tn = Render.channels( img );
      globalThis.Rect = Prev;
      const ds = PS.filter( s => s.detect !== false ).map( ( s, k ) => ( { index: k, x: s.x, y: s.y, flux: s.flux, rect: Fly.detectionRect( s.x, s.y, 30 ), nmax: 0, fromGaia: false } ) );
      const en = { I: In, T: Tn, w: W, h: H, nc: NC, fwhm: px, labels: Sky.coreLabels( ds, W, H, px ), noise: Sky.noiseSigma( In ),
                   sky: Tn.map( Sky.sparseMedian ), skyI: Sky.sparseMedian( In ) };
      en.spikes = Sky.spikeDirections( en, ds );
      en.spikeSigma = Sky.spikeWidth( en, ds, en.spikes );
      en.spikeFalloff = Sky.spikeFalloff( en, ds, en.spikes );
      const ms = ds.map( d => Sky.starModel( en, d ) ); ds.forEach( ( d, k ) => { d.model = ms[k]; } );
      const sp = ds.map( ( d, k ) => ( { det: d, placed: { source: { G: 10 + k }, d: 100 + k, x: d.x, y: d.y }, rect: d.rect } ) );
      const sm0 = Sky.modelSums( en, ds );
      const sumsBefore = sm0.map( p => p.slice() );
      const ex = Sky.unexplainedSources( en, sp, sm0 );
      if ( ex.length ) Sky.addToSums( en, ex, sm0 );
      const left = Fly.attachToCores( sp, ex );
      const res = Tn.map( b => b.slice() );
      const out = sp.map( s => Sky.deblendSprite( en, s, sm0, res ) );
      return {
         planes: pl,
         dets: ds.map( d => ( { index: d.index, x: d.x, y: d.y, flux: d.flux, rect: d.rect, nmax: d.nmax, fromGaia: d.fromGaia } ) ),
         fwhm: px,
         env: { noise: en.noise, sky: en.sky, skyI: en.skyI, spikes: en.spikes, spikeSigma: en.spikeSigma,
                spikeFalloff: en.spikeFalloff, labels: en.labels, I: In },
         models: ms.map( m => ( { x: m.x, y: m.y, core: m.core, prof: m.prof, rmax: m.rmax, col: m.col, support: m.support,
                                  spikes: m.spikes, spikeSigma: m.spikeSigma } ) ),
         sumsBefore: sumsBefore,
         extra: ex.map( e => ( { x: e.x, y: e.y, prof: e.prof, rmax: e.rmax, col: e.col, support: e.support } ) ),
         leftCount: left.length,
         partsCount: ds.map( d => d.model.parts ? d.model.parts.length : 0 ),
         partsPos: ds.map( d => d.model.parts ? d.model.parts.map( e => ( { x: e.x, y: e.y } ) ) : null ),
         sums: sm0,
         deblend: out.map( s => ( { rect: { x0: s.rect.x0, y0: s.rect.y0, x1: s.rect.x1, y1: s.rect.y1 }, mask: s.mask,
                                    pixels: s.pixels, modelCore: s.modelCore, modelSpikes: s.modelSpikes } ) ),
         residual: res
      };
   } )();

   return cases;
};
