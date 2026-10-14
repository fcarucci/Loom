/*
 * Oracle for loomsiril/assign.py and detect.py's bright_measure / each_in_window (E3a).
 * Each case stores its INPUTS (plain data, built here) next to the JS OUTPUT, so the
 * Python test replays the same records. Rules the JS calls as callbacks are fixed:
 *   haloOf   d => 3 + Math.sqrt(d.flux)            (Python: 3 + math.sqrt(d.flux))
 *   measure  (x, y) => ({ flux: 10*x + y, area: 60 + Math.floor(x) })
 * Sprites are recorded as { pi, di, rect } (indices into placed and dets).
 */
"use strict";

module.exports = function( js )
{
   const Fly = js.Fly, Sky = js.Sky;

   // a detection record as data; rect comes from Fly.detectionRect (recorded, not recomputed in Python)
   function det( x, y, flux, o )
   {
      o = o || {};
      return { index: o.index || 0, x: x, y: y, nmax: o.nmax || 0, flux: flux,
               rect: o.rect || Fly.detectionRect( x, y, o.area || 12 ), halo: o.halo || null, fromGaia: !!o.fromGaia };
   }
   // a detection with a square rect of half-width r around (x, y) (the selftest's det())
   function box( x, y, r, nmax, flux )
   {
      return { index: 0, x: x, y: y, nmax: nmax || 1, flux: flux, rect: { x0: x - r, y0: y - r, x1: x + r + 1, y1: y + r + 1 }, halo: null, fromGaia: false };
   }
   function placed( source, x, y, d )
   {
      return { source: source, d: d || 100, x: x, y: y };
   }
   function sprites( dets, list )
   {
      return list.map( s => ( { pi: s.pi, di: s.di, rect: s.rect || dets[s.di].rect } ) );
   }
   function build( placedList, dets, list )
   {
      return list.map( s => ( { placed: placedList[s.pi], det: dets[s.di], rect: s.rect } ) );
   }
   // neighbours as data: `same` = index of the placed star whose source object IS this one (JS identity), else -1
   function nbRec( list, pl )
   {
      return ( list || [] ).map( n => ( { source: n.source, x: n.x, y: n.y,
                                          same: ( n.source !== undefined ) ? pl.findIndex( p => p.source === n.source ) : -1 } ) );
   }
   const haloOf = d => 3 + Math.sqrt( d.flux );
   const measure = ( x, y ) => ( { flux: 10*x + y, area: 60 + Math.floor( x ) } );
   function gridOut( grid )
   {
      return Object.keys( grid ).sort().map( k => [ +k.split( "," )[0], +k.split( "," )[1], grid[k] ] );
   }
   function detOut( list )
   {
      return list.map( d => ( { index: d.index, x: d.x, y: d.y, nmax: d.nmax, flux: d.flux, rect: d.rect, fromGaia: !!d.fromGaia } ) );
   }

   const out = {};

   // ---- 694-760: matching -------------------------------------------------
   out.MATCH = { MATCH_RADIUS: Fly.MATCH_RADIUS, GRID: Fly.GRID, MATCH_SEARCH: Fly.MATCH_SEARCH };

   const mrDets = [ box( 20, 20, 2 ), det( 10, 10, 3, { area: 3 } ), det( 50, 50, 120, { area: 220 } ) ];
   out.matchRadius = { args: mrDets.map( d => ( { rect: d.rect } ) ), out: mrDets.map( d => Fly.matchRadius( d ) ) };

   const riPairs = [ [ { x0: 0, y0: 0, x1: 5, y1: 5 }, { x0: 4, y0: 4, x1: 8, y1: 8 } ],
                     [ { x0: 0, y0: 0, x1: 5, y1: 5 }, { x0: 5, y0: 0, x1: 8, y1: 8 } ],
                     [ { x0: 0, y0: 0, x1: 5, y1: 5 }, { x0: -3, y0: 2, x1: 1, y1: 3 } ] ];
   out.rectsIntersect = { args: riPairs, out: riPairs.map( p => Fly.rectsIntersect( p[0], p[1] ) ) };

   // a small plate: a big core straddling cells, a small one, and a detection at the image edge
   const gDets = [ box( 15, 15, 3 ), box( 40, 7, 1 ), det( 2, 30, 4, { area: 9 } ), box( 16, 40, 5 ) ];
   out.detectionGrid = { args: gDets, out: gridOut( Fly.detectionGrid( gDets ) ) };
   const gGrid = Fly.detectionGrid( gDets );
   const gRects = [ { x0: 14, y0: 14, x1: 17, y1: 17 }, { x0: 0, y0: 0, x1: 1, y1: 1 }, { x0: 30, y0: 0, x1: 60, y1: 20 }, { x0: 16, y0: 35, x1: 17, y1: 50 } ];
   out.detectionsIn = { args: { dets: gDets, rects: gRects }, out: gRects.map( r => Fly.detectionsIn( gGrid, r ) ) };

   const gaiaA = { ra: 1, dec: 1, plx: 2, G: 12 }, gaiaB = { ra: 2, dec: 2, plx: 3, G: 9.5 }, gaiaC = { ra: 3, dec: 3, plx: 1, G: NaN }, gaiaD = { ra: 4, dec: 4, plx: 1, G: 14 };
   const mpPlaced = [ placed( gaiaA, 20.5, 20.4 ), placed( gaiaB, 50, 20 ), placed( gaiaC, 60, 80 ), placed( gaiaD, 20, 60 ), placed( undefined, 30, 60 ) ];
   const mpDets = [ box( 20, 20, 2 ), box( 50, 20, 2, 2 ), box( 60, 80, 1 ), box( 20, 60, 2 ), box( 30, 60, 2 ), box( 24, 20, 1 ) ];
   const mpGrid = Fly.detectionGrid( mpDets );
   out.matchPairs = { args: { dets: mpDets, placed: mpPlaced }, out: Fly.matchPairs( mpDets, mpPlaced, mpGrid ).map( m => ( { pi: m.pi, di: m.di, r2: m.r2, G: m.G } ) ) };

   // ---- 779-797: blends -----------------------------------------------------
   const cbDet = box( 20, 20, 3 ), cbPlaced = placed( { G: 10 }, 20, 20 );
   const cbCases = [
      [ cbDet, cbPlaced, [ { source: { G: 10 }, x: 20, y: 20 }, { source: { G: 10.2 }, x: 21, y: 20 } ] ],  // brighter companion in core
      [ cbDet, cbPlaced, [ { source: { G: 14 }, x: 21, y: 20 } ] ],          // much fainter: no blend
      [ cbDet, cbPlaced, [ { source: { G: 9 }, x: 40, y: 20 } ] ],           // outside the core
      [ cbDet, placed( undefined, 20, 20 ), [ { source: { G: 13 }, x: 21, y: 21 } ] ], // no G on placed: any companion counts
      [ cbDet, cbPlaced, [ { source: { G: NaN }, x: 22, y: 22 } ] ],         // NaN companion G counts (JS comparison false)
   ];
   out.catalogueBlend = { args: cbCases.map( c => ( { det: c[0], placed: c[1], neighbours: nbRec( c[2], [ c[1] ] ) } ) ),
                          out: cbCases.map( c => Fly.catalogueBlend( c[0], c[1], c[2] ) ) };
   out.BLEND_COMPANION_MAG = Fly.BLEND_COMPANION_MAG;

   const ibDets = [ box( 30, 30, 10, 1, 100 ), box( 33, 30, 4, 1, 80 ), box( 80, 80, 1, 2, 5 ), box( 30, 30, 2, 1, NaN ) ];
   const ibGrid = Fly.detectionGrid( ibDets );
   out.isBlend = { args: { dets: ibDets, cases: [ [ 0, { x0: 20, y0: 20, x1: 41, y1: 41 } ], [ 1, { x0: 26, y0: 26, x1: 39, y1: 39 } ],
                                                  [ 2, { x0: 78, y0: 78, x1: 83, y1: 83 } ], [ 3, { x0: 27, y0: 27, x1: 34, y1: 34 } ] ] },
                   out: [ [ 0, { x0: 20, y0: 20, x1: 41, y1: 41 } ], [ 1, { x0: 26, y0: 26, x1: 39, y1: 39 } ],
                          [ 2, { x0: 78, y0: 78, x1: 83, y1: 83 } ], [ 3, { x0: 27, y0: 27, x1: 34, y1: 34 } ] ]
                             .map( c => Fly.isBlend( ibDets, c[0], c[1], ibGrid ) ) };

   // ---- 806-927: sprites and ownership ----------------------------------
   const W = 60, H = 50;
   const pl1 = [ placed( { G: 12 }, 20, 20 ), placed( { G: 13 }, 40, 25 ), placed( { G: 15 }, 22, 30 ) ];
   const dl1 = [ box( 20, 20, 2, 1, 50 ), box( 40, 25, 3, 1, 20 ), box( 22, 30, 2, 1, 4 ) ];
   // sprites in input order; the second one carries its own (smaller) rect
   const sl1 = sprites( dl1, [ { pi: 0, di: 0 }, { pi: 1, di: 1, rect: { x0: 36, y0: 21, x1: 45, y1: 30 } }, { pi: 2, di: 2 } ] );
   out.oneStarOneSprite = { args: { placed: pl1, dets: dl1, sprites: sl1 },
                            out: Fly.oneStarOneSprite( build( pl1, dl1, sl1 ) ).map( s => [ pl1.indexOf( s.placed ), dl1.indexOf( s.det ) ] ) };
   out.spriteOwnership = { args: { placed: pl1, dets: dl1, sprites: sl1, w: W, h: H, haloMode: false },
                           out: Array.from( Fly.spriteOwnership( dl1, build( pl1, dl1, sl1 ), W, H, false ) ) };
   out.spriteOwnershipHalo = { args: { placed: pl1, dets: dl1, sprites: sl1, w: W, h: H, haloMode: true },
                               out: Array.from( Fly.spriteOwnership( dl1, build( pl1, dl1, sl1 ), W, H, true ) ) };

   // ---- within reach (1735-1802 region, Fly.withinReach) -------------------
   const spikeB = det( 100, 100, 120, { area: 5, halo: { radius: 80, halo: 15, pedestal: 0, feather: 12, outer: 92, spikes: [ { angle: 0, reach: 80 } ] } } );
   const plainB = det( 100, 100, 120, { area: 5 } );
   const reachCases = [ [ spikeB, det( 150, 100, 2, { area: 5 } ) ], [ spikeB, det( 100, 150, 2, { area: 5 } ) ],
                        [ spikeB, det( 110, 105, 2, { area: 5 } ) ], [ plainB, det( 130, 100, 2, { area: 5 } ) ],
                        [ plainB, det( 90, 100, 2, { area: 5 } ) ], [ spikeB, det( 300, 100, 2, { area: 5 } ) ] ];
   out.withinReach = { args: reachCases.map( c => ( { a: c[0], s: c[1] } ) ), out: reachCases.map( c => Fly.withinReach( c[0], c[1] ) ) };
   out.SPIKE_WIDTH = Fly.SPIKE_WIDTH;

   // ---- addMissingBright (1787-1802) ---------------------------------------
   const amDets = [ det( 20, 20, 5, { area: 5 } ) ];
   const amNb = [ { source: { G: 5.6 }, x: 60.4, y: 40.2 }, { source: { G: 14 }, x: 90, y: 90 }, { source: { G: 8 }, x: 20.5, y: 20.3 }, { source: { G: 9 }, x: 60.4, y: 40.9 } ];
   out.addMissingBright = { args: { dets: amDets, neighbours: nbRec( amNb, [] ) }, out: detOut( Fly.addMissingBright( amDets, amNb, measure ) ) };
   out.BRIGHT = { BRIGHT_G: Fly.BRIGHT_G, BRIGHT_MISS_RADIUS: Fly.BRIGHT_MISS_RADIUS, STILL_REACH_SEARCH: Fly.STILL_REACH_SEARCH };

   // dropInsideBrighter: a bright star with a halo, a faint star inside its reach, and one far off
   const diDets = [ det( 50, 50, 300, { area: 300, halo: { radius: 25, halo: 25, pedestal: 0, feather: 2, outer: 27, spikes: [] } } ),
                    det( 70, 50, 3, { area: 5 } ), det( 110, 110, 2, { area: 5 } ), det( 60, 50, 150, { area: 40 } ) ];
   const diPl = [ placed( { G: 15 }, 70, 50 ), placed( { G: 16 }, 110, 110 ), placed( { G: 13 }, 60, 50 ) ];
   const diSpr = sprites( diDets, [ { pi: 0, di: 1 }, { pi: 1, di: 2 }, { pi: 2, di: 3 } ] );
   out.dropInsideBrighter = { args: { placed: diPl, dets: diDets, sprites: diSpr, haloOf: "3 + sqrt(flux)" },
                              out: Fly.dropInsideBrighter( build( diPl, diDets, diSpr ), diDets, Fly.detectionGrid( diDets ), haloOf )
                                      .map( s => [ diPl.indexOf( s.placed ), diDets.indexOf( s.det ) ] ) };

   // assignSprites (695-927) on a few fields; each with its inputs
   function assignCase( dets, pl, w, h, grow, nb, useHalo )
   {
      const r = Fly.assignSprites( dets, pl, w, h, grow, nb || [], useHalo ? haloOf : undefined );
      return { args: { dets: dets, placed: pl, w: w, h: h, grow: grow, neighbours: nbRec( nb, pl ), haloOf: useHalo ? "3 + sqrt(flux)" : null },
               out: { sprites: r.sprites.map( s => [ pl.indexOf( s.placed ), dets.indexOf( s.det ), s.rect ] ),
                      owner: Array.from( r.owner ), blended: r.blended, absorbed: r.absorbed, unmatched: r.unmatched } };
   }
   // the selftest field of 14668-14690: isolated, a double, a blend by footprint, a pair near the edge
   const sfDets = [ box( 20, 20, 2, 1, NaN ), box( 50, 20, 2, 2, NaN ), box( 80, 20, 2, 1, NaN ), box( 86, 20, 1, 1, NaN ),
                    box( 20, 60, 2, 1, NaN ), box( 30, 60, 2, 1, NaN ) ];
   const sfPl = [ placed( { x: 20.5 }, 20.5, 20.4 ), placed( { x: 50 }, 50, 20 ), placed( { x: 80 }, 80, 20 ), placed( { x: 60 }, 60, 80 ),
                  placed( { x: 20 }, 20, 60 ), placed( { x: 30 }, 30, 60 ) ];
   out.assignSelftest = assignCase( sfDets, sfPl, 100, 100, 3, null, false );

   // the star-field cases with fluxes, with and without catalogue neighbours
   const bDets = [ box( 30, 30, 10, 1, 100 ), box( 36, 30, 1, 1, 3 ), box( 60, 30, 10, 1, 100 ), box( 66, 30, 6, 1, 50 ) ];
   const bSrc = {};
   const bPl = [ placed( bSrc, 30, 30 ), placed( bSrc, 60, 30 ) ];
   out.assignHaloRule = assignCase( bDets, bPl, 100, 100, 3, [], true );
   out.assignTwoSources = assignCase( [ box( 20, 20, 2, 1, 12 ) ], [ placed( { G: 10 }, 21, 20 ), placed( { G: 9.8 }, 20.2, 20 ) ], 100, 100, 3, [], false );

   const gA = { G: 5.6 }, gB = { G: 8.0 };
   const bigPair = [ det( 60, 60, 168, { area: 1400 } ), det( 72, 65, 161, { area: 1400 } ) ];
   out.assignCompanion = assignCase( bigPair, [ placed( gA, 60, 60, 700 ), placed( gB, 72, 65, 900 ) ], 140, 140, 3,
                                     [ { source: gA, x: 60, y: 60 }, { source: gB, x: 72, y: 65 } ], true );

   // spikes: a bright star with a spike-free halo, a faint star on its glow, another far off (16056-16077)
   const spDets = [ det( 50, 50, 120, { area: 150 } ), det( 64, 50, 2, { area: 5 } ), det( 90, 90, 3, { area: 5 } ) ];
   const spPl = [ placed( { G: 9 }, 50, 50, 400 ), placed( { G: 15 }, 64, 50, 150 ), placed( { G: 14 }, 90, 90, 700 ) ];
   out.assignSpike = assignCase( spDets, spPl, 120, 120, 3, [], true );

   // the glow case of 16144: a faint star in a still bright star's glow is not a sprite
   const glDets = [ det( 50, 50, 300, { area: 300 } ), det( 70, 50, 3, { area: 5 } ) ];
   out.assignGlow = assignCase( glDets, [ placed( { G: 15 }, 70, 50 ) ], 120, 120, 3, [], true );

   // ---- Sky.brightMeasure, Sky.eachInWindow ------------------------------
   const bw = 40, bh = 36;
   const bufArr = [];
   for ( let y = 0; y < bh; ++y ) for ( let x = 0; x < bw; ++x )
      bufArr.push( Math.round( 1000 * ( 0.02 + 0.9 * Math.pow( 1 + ( ( x - 20 ) * ( x - 20 ) + ( y - 18 ) * ( y - 18 ) ) / 6, -2.5 ) ) ) / 1000 );
   // a float32 buffer, as the image planes are
   const buf32 = Float32Array.from( bufArr );
   const bmCases = [ [ 20.4, 17.6 ], [ 2.2, 30.7 ], [ 39.5, 0.1 ] ];
   out.brightMeasure = { args: { w: bw, h: bh, buf: Array.from( buf32 ), cases: bmCases },
                         out: bmCases.map( c => Sky.brightMeasure( buf32, bw, bh, c[0], c[1] ) ) };
   const ewCases = [ [ 3, 2, 3 ], [ 10, 8, 2 ], [ 0, 0, 1 ] ];
   out.eachInWindow = { args: { w: 12, h: 9, cases: ewCases },
                        out: ewCases.map( c => { const acc = []; Sky.eachInWindow( 12, 9, c[0], c[1], c[2], ( i, dx, dy ) => acc.push( [ i, dx, dy ] ) ); return acc; } ) };
   return out;
};
