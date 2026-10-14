/*
 * Oracle for loomsiril/sprites.py and loomsiril/frame.py (E5): Render.spriteBounds, coreRadius,
 * splitPatches, glowPatch, placeSprite, shutterPath, seenShares, addSprites (into zero planes) and
 * Render.frame itself on a tiny hand-made scene: 48 x 32, one channel, three sprites (a round
 * one with a core, one with spikes, one point model), a TAN projection, a target at D = 900 pc.
 *
 * Render.frame ends in Render.frameImage, which builds an Image. Under node there is no PixInsight
 * Image, so this recorder installs a minimal fake (getSamples/setSamples on plain arrays) and the
 * PixInsight-only paths (ImageOp_* globals) stay undefined, so the JS takes its per-pixel reference
 * paths (compositeJs, bloomJs, starsToHeadroomJs). Those are the paths the Python port follows.
 *
 * Every case that reads the scene reads it from the "scene" case, which stores the INPUTS
 * (sprite pixels, model, spike angles, source positions, the WCS) as plain numbers.
 */
"use strict";

module.exports = function( js )
{
   const Fly = js.Fly, Render = js.Render;

   // --- what PixInsight provides and node does not --------------------------------------------
   global.ColorSpace_Gray = 0; global.ColorSpace_RGB = 2; global.SampleType_Real = 1;
   global.Rect = function( x0, y0, x1, y1 ) { this.x0 = x0; this.y0 = y0; this.x1 = x1; this.y1 = y1; };
   global.Image = function( w, h, nc ) { this.width = w; this.height = h; this.numberOfChannels = nc; this.numberOfNominalChannels = nc; this.planes = []; };
   global.Image.prototype.setSamples = function( a, rect, c ) { this.planes[c] = Array.from( a ); };
   global.Image.prototype.getSamples = function( a, rect, c ) { for ( let i = 0; i < a.length; ++i ) a[i] = this.planes[c][i]; };

   // --- the scene -----------------------------------------------------------------------------
   const W = 48, H = 32;
   const wcs = { crval1: 324.745, crval2: 57.514, crpix1: 24.5, crpix2: 16.5, cd: [ -0.0005, 0.00001, 0.00002, 0.0005 ] };
   const project = ( ra, dec ) => Fly.tanProject( ra, dec, wcs );
   const target = Fly.tanUnproject( 16.25, 13.75, wcs );       // target: its pixel is near the image centre

   // hand-made sprites: each with its own Gaussian light (sigma 1.4 .. 2.2 px), fully inside its rect
   function gauss( x0, y0, rw, rh, cx, cy, sigma, peak )
   {
      const p = new Float32Array( rw*rh );
      for ( let y = 0; y < rh; ++y )
         for ( let x = 0; x < rw; ++x )
         {
            const dx = x0 + x - cx, dy = y0 + y - cy;
            p[y*rw + x] = peak*Math.exp( -( dx*dx + dy*dy )/( 2*sigma*sigma ) );
         }
      return p;
   }
   // a spike: a thin line along an axis from the centre (the spike model), width about 0.6 px
   function spikeLine( x0, y0, rw, rh, cx, cy, angle, peak )
   {
      const p = new Float32Array( rw*rh ), ax = Math.cos( angle ), ay = Math.sin( angle );
      for ( let y = 0; y < rh; ++y )
         for ( let x = 0; x < rw; ++x )
         {
            const dx = x0 + x - cx, dy = y0 + y - cy, along = dx*ax + dy*ay, lat = -dx*ay + dy*ax;
            if ( along > 0 ) p[y*rw + x] = peak*Math.exp( -lat*lat/0.36 )*Math.exp( -along/9 );
         }
      return p;
   }
   const rectOf = ( x0, y0, x1, y1 ) => ( { x0: x0, y0: y0, x1: x1, y1: y1 } );
   function sprite( d, det, rect, model, pixels, modelCore, modelSpikes, spikeAngles )
   {
      const src = tanUnprojectSource( det.x, det.y );
      return { source: { ra: src.ra, dec: src.dec, G: 9.5 }, d: d, pxScale: 1,
               det: { x: det.x, y: det.y, rect: rect, model: model }, rect: rect,
               pixels: [ pixels ], modelCore: [ modelCore ], modelSpikes: [ modelSpikes ], spikeAngles: spikeAngles };
   }
   function tanUnprojectSource( x, y ) { return Fly.tanUnproject( x, y, wcs ); }

   const sprites = [];
   {
      const r = rectOf( 6, 4, 23, 21 ), c = { x: 14.3, y: 12.6 };
      const core = gauss( r.x0, r.y0, 17, 17, c.x, c.y, 1.6, 0.9 ), outer = gauss( r.x0, r.y0, 17, 17, c.x, c.y, 2.8, 0.25 );
      const px = core.map( ( v, i ) => v + outer[i] );
      sprites.push( sprite( 300, c, r, { core: 2.5 }, px, core, new Float32Array( 17*17 ), [] ) );
   }
   {
      const r = rectOf( 27, 16, 42, 31 ), c = { x: 34.2, y: 22.7 };
      const model = gauss( r.x0, r.y0, 15, 15, c.x, c.y, 1.9, 0.8);
      const sp = spikeLine( r.x0, r.y0, 15, 15, c.x, c.y, 0, 0.12 ).map( ( v, i ) => v + spikeLine( r.x0, r.y0, 15, 15, c.x, c.y, Math.PI/2, 0.12 )[i] );
      const px = model.map( ( v, i ) => v + sp[i] );
      sprites.push( sprite( 450, c, r, { core: 2.0 }, px, model, sp, [ 0, Math.PI/2 ] ) );
   }
   {
      const r = rectOf( 3, 22, 10, 29 ), c = { x: 6.1, y: 25.4 };
      const pt = gauss( r.x0, r.y0, 7, 7, c.x, c.y, 1.4, 0.6 );
      sprites.push( sprite( 200, c, r, { core: 0 }, pt, pt, new Float32Array( 49 ), [] ) );
   }

   // the stars layer: the sprites' light outside them is the residual; the backdrop is a smooth gradient
   const S = new Float32Array( W*H ), R = new Float32Array( W*H );
   for ( let y = 0; y < H; ++y ) for ( let x = 0; x < W; ++x ) S[y*W + x] = 0.05 + 0.3*x/W + 0.1*y/H;
   const tp = project( target.ra, target.dec ), D = 900;
   const sc = { w: W, h: H, nc: 1, S: [ S ], R: [ R ], sprites: sprites.map( s => ( { s: s, p0: project( s.source.ra, s.source.dec ) } ) ),
                project: project, target: target, tp: tp, D: D, catalogue: null };

   const crop = { x: 0, y: 0, w: W, h: H };
   const opts = { travel: 40, easing: "smoothstep", growth: 0.15, brightening: true, twinkle: 0.03, duration: 8, frameDt: 1/30 };

   // --- recorders -----------------------------------------------------------------------------
   const out = {};
   out.scene = {
      w: W, h: H, nc: 1, S: [ Array.from( S ) ], R: [ Array.from( R ) ], wcs: wcs, target: target, tp: tp, D: D,
      sprites: sprites.map( s => ( { source: s.source, d: s.d, pxScale: s.pxScale, det: s.det, rect: s.rect,
                                     pixels: s.pixels.map( a => Array.from( a ) ), modelCore: s.modelCore.map( a => Array.from( a ) ),
                                     modelSpikes: s.modelSpikes.map( a => Array.from( a ) ), spikeAngles: s.spikeAngles } ) ),
      crop: crop, frameOpts: opts
   };

   out.spriteBounds = sc.sprites.map( ( e, j ) => ( {
      args: [ j, e.s.det.x + 1.5, e.s.det.y - 2.25, 1.7, { x: 0, y: 0, fx: 1.5, fy: 1.5 }, 32, 18 ],
      out: Render.spriteBounds( e.s, e.s.det.x + 1.5, e.s.det.y - 2.25, 1.7, { x: 0, y: 0, fx: 1.5, fy: 1.5 }, 32, 18 ) } ) );
   out.spriteBoundsClipped = sc.sprites.map( ( e, j ) => ( {
      args: [ j, e.s.det.x - 15, e.s.det.y + 14, 3.0, { x: 0, y: 0, fx: 1, fy: 1 }, 20, 12 ],
      out: Render.spriteBounds( e.s, e.s.det.x - 15, e.s.det.y + 14, 3.0, { x: 0, y: 0, fx: 1, fy: 1 }, 20, 12 ) } ) );
   out.coreRadius = sc.sprites.map( e => Render.coreRadius( e.s ) );

   // placed sprites at a few times (travel eased in by t), as Render.addSprites places them
   const times = [ 0, 0.3, 1 ];
   out.placeSprite = times.map( t =>
   {
      const s = opts.travel*Fly.ease( t, opts.easing ), o = Object.assign( {}, opts, { t: t, K: 1.0, coarseMips: false } );
      return sc.sprites.map( ( e, j ) =>
      {
         const q = Render.placeSprite( sc, e, j, s, o );
         if ( !q ) return null;
         return { t: t, j: j, g: q.g, kOuter: q.kOuter, kCore: q.kCore, rc: q.rc, cx: q.cx, cy: q.cy, alpha: q.alpha,
                  spikeLength: q.spikeLength, ratio: q.m.ratio, front: q.m.front };
      } );
   } );

   // shutter path: a moving star with motion blur, at t = 0.3 (a straight line of centres)
   {
      // a long clip step (frameDt 0.5) and a long travel, so the shutter covers several steps
      const t = 0.5, o = Object.assign( {}, opts, { t: t, K: 1.0, motionBlur: true, frameDt: 0.5, travel: 120, coarseMips: false } ), s = 120*Fly.ease( t, opts.easing );
      const e = sc.sprites[2], q = Render.placeSprite( sc, e, 2, s, o );
      const cam = { x: 0, y: 0, fx: 1.5, fy: 1.5 };
      out.shutterPath = { args: [ 2, t, 120, 0.5 ], out: Render.shutterPath( sc, e.s, e, o, cam, q.cx, q.cy ) };
   }

   // seen shares: two overlapping placed stars at t = 0.05 (the eased-in part of the clip)
   {
      const t = 0.05, o = Object.assign( {}, opts, { t: t, K: 1.0, coarseMips: false } ), s = opts.travel*Fly.ease( t, opts.easing );
      const placed = [];
      sc.sprites.forEach( ( e, j ) => { const q = Render.placeSprite( sc, e, j, s, o ); if ( q ) placed.push( q ); } );
      const cam = { x: 0, y: 0, fx: 1.5, fy: 1.5 }, ease = Fly.smoothstep( 0, 0.1, t );
      Render.seenShares( placed, 32, 18, cam, ease );
      out.seenShares = { args: { t: t, outW: 32, outH: 18, ease: ease }, out: placed.map( q => ( { j: q.j, seen: q.seen, u: q.u, v: q.v, r: q.r } ) ) };
   }

   // splitPatches and glowPatch of the round sprite (core 2.5) and the spike sprite
   {
      const e = sc.sprites[0].s, rc = Render.coreRadius( e );
      const sp = Render.splitPatches( Object.assign( {}, e ), 0, rc, e.pixels[0] );
      out.splitPatches = { args: { rc: rc, sprite: 0 }, out: { photoCore: sp.photoCore, modelCore: sp.modelCore, modelOuter: sp.modelOuter,
                                                              coreRect: sp.coreSprite.rect } };
      const es = sc.sprites[1].s;
      out.glowPatch = { args: { sprite: 1 }, out: Render.glowPatch( es, 0 ) };
   }

   // addSprites into zero planes (the sprites alone) at t = 0, 0.3, 1, then Render.frame itself
   const outW = 32, outH = 18, cam32 = { x: 0, y: 0, fx: W/outW, fy: H/outH };
   out.addSprites = times.map( t =>
   {
      const s = opts.travel*Fly.ease( t, opts.easing ), o = Object.assign( {}, opts, { t: t, K: 1.0, coarseMips: false } );
      const T = [ new Float32Array( W*H ) ];
      Render.addSprites( sc, T, s, o, W, H, { x: 0, y: 0, fx: 1, fy: 1 } );
      return { t: t, T: Array.from( T[0] ) };
   } );

   const cases = [ [ "frame0", 0, opts ], [ "frame03", 0.3, opts ], [ "frame1", 1, opts ] ];
   out.frame = cases.map( ( c ) =>
   {
      const img = Render.frame( sc, c[1], c[2], outW, outH, crop );
      return { name: c[0], t: c[1], outW: outW, outH: outH, out: img.planes[0] };
   } );
   // Medium quality (stars at half the video's resolution, coarse mips) and High (footprint samples), at t = 1
   out.frameMedium = { t: 1, out: Render.frame( sc, 1, Object.assign( {}, opts, { starQuality: "medium" } ), outW, outH, crop ).planes[0] };
   out.frameHigh = { t: 1, out: Render.frame( sc, 1, Object.assign( {}, opts, { starQuality: "high" } ), outW, outH, crop ).planes[0] };
   // motion blur (the shutter) at t = 0.3 with the frame's own clock
   out.frameBlur = { t: 0.3, out: Render.frame( sc, 0.3, Object.assign( {}, opts, { motionBlur: true } ), outW, outH, crop ).planes[0] };

   // HDR (E5 review L2): the headroom map (Render.headroomMap with starHdr and peak 1000) at t = 0.3 and 1, with
   // the frame's own backdrop zoom K and the stars placed as Render.frame places them (addSprites, zero planes)
   out.headroomMap = [ 0.3, 1 ].map( t =>
   {
      const s = opts.travel*Fly.ease( t, opts.easing ), K = Fly.backdropZoom( sc.D, s, Render.orDefault( opts.backdropMotion, Fly.BACKDROP_MOTION_DEFAULT ) );
      const hopts = Object.assign( {}, opts, { t: t, K: K, coarseMips: false, starHdr: true, peak: 1000 } );
      const placed = Render.addSprites( sc, [ new Float32Array( outW*outH ) ], s, hopts, outW, outH, cam32 );
      return { t: t, peak: 1000, K: K, out: Render.headroomMap( sc, placed, hopts, outW, outH, cam32 ) };
   } );
   // HDR headroom where a placed star's core radius, not its reach, sets the bump (Render.headroomMap's
   // max(reach, rc*g + 2 px)): faint sprites (G 15, 15, 13.5), growth 6, and one bright catalogue star off the frame
   // so that gBright is 9.5 and only the third sprite gets a gain. The sprites' G, the catalogue and gBright are restored.
   {
      const Gs = [ 15, 15, 13.5 ], keepG = sc.sprites.map( e => e.s.source.G ), catalogue = [ { x: -2000, y: -2000, G: 9.5 } ];
      sc.catalogue = catalogue; sc.gBright = null;
      sc.sprites.forEach( ( e, j ) => { e.s.source.G = Gs[j]; } );
      try
      {
         const o = Object.assign( {}, opts, { growth: 6 } ), t = 1, s = o.travel*Fly.ease( t, o.easing );
         const K = Fly.backdropZoom( sc.D, s, Render.orDefault( o.backdropMotion, Fly.BACKDROP_MOTION_DEFAULT ) );
         const hopts = Object.assign( {}, o, { t: t, K: K, coarseMips: false, starHdr: true, peak: 1000 } );
         const placed = Render.addSprites( sc, [ new Float32Array( outW*outH ) ], s, hopts, outW, outH, cam32 );
         out.headroomMapCore = { t: t, K: K, peak: 1000, growth: 6, G: Gs, catalogue: catalogue,
                                 out: Render.headroomMap( sc, placed, hopts, outW, outH, cam32 ) };
      }
      finally
      {
         sc.sprites.forEach( ( e, j ) => { e.s.source.G = keepG[j]; } ); sc.catalogue = null; sc.gBright = null;
      }
   }
   // PQ frames (Fly.outputTransform SRGB -> pq, peak 1000) at t = 0 and 1; the transform's stats (MaxCLL, MaxFALL in
   // nits) are recorded after each frame, the same transform carrying them on from frame 0 to frame 1
   const pq = Fly.outputTransform( Fly.SRGB_COLOUR, "pq", { peak: 1000 } );
   out.framePQ = [ 0, 1 ].map( t =>
   {
      const img = Render.frame( sc, t, Object.assign( {}, opts, { output: pq } ), outW, outH, crop );
      return { t: t, outW: outW, outH: outH, out: img.planes[0], stats: { maxCll: pq.stats.maxCll, maxFall: pq.stats.maxFall } };
   } );

   return out;
};
