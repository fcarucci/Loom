/*
 * Oracle for loomsiril/geometry.py (E1a): the motion, growth, opacity, twinkle
 * and easing maths of Fly.js (v0.4.1). Each case is { args: [...], out: value }
 * so the Python test can replay it; constants are recorded as one case each.
 */
"use strict";

module.exports = function( js )
{
   const Fly = js.Fly;
   const T = { ra: 324.745, dec: 57.514 };

   const moved = [
      [ 324.745, 57.514, 500, T, 100 ],        // on axis: direction kept, ratio d/(d-s)
      [ 324.745, 58.514, 100, T, 50 ],         // off axis, 1 degree away
      [ 324.745, 57.6, 40, T, 60 ],            // passed: behind the camera
      [ T.ra, T.dec, 50, T, 50 ],              // on the camera: |p| = 0, ratio Infinity
      [ NaN, 57.514, 100, T, 50 ]              // NaN field
   ];

   const screenPosition = [
      [ { x: 200, y: 100 }, { x: 150, y: 100 }, { x: 150, y: 100 }, 1.2 ],   // no p0 offset: r0 = 0, unchanged
      [ { x: 110, y: 100 }, { x: 120, y: 100 }, { x: 100, y: 100 }, 1.5 ],   // pulled out to K r0 = 30
      [ { x: 100, y: 100 }, { x: 120, y: 100 }, { x: 100, y: 100 }, 1.5 ],   // on the target: r = 0 branch
      [ { x: 110, y: 100 }, { x: 120, y: 100 }, { x: 100, y: 100 }, 1 ],     // K = 1: unchanged
      [ { x: 110, y: 100 }, { x: 120, y: 100 }, { x: 100, y: 100 }, NaN ]    // NaN K: unchanged
   ];

   const radialSource = [ [ 3, 5, 2 ], [ 25, 5, 2 ], [ 25, 5, 1 ], [ 25, 5, 2, 1 ], [ 25, 5, 2, 0.5 ], [ 25, 5, 2, 0 ],
                          [ 25, 5, 2, NaN ] ];
   const discVisible = [ [ 50, 50, 5, 100, 100 ], [ 0, 50, 5, 100, 100 ], [ 100, 100, 5, 100, 100 ],
                         [ -10, 50, 5, 100, 100 ], [ 3, 3, 5, 100, 100 ], [ 50, 50, NaN, 100, 100 ] ];
   const discGridCount = [ [ 50, 50, 5, 100, 100 ], [ 0, 50, 5, 100, 100 ], [ -10, 50, 5, 100, 100 ],
                           [ 50, 50, NaN, 100, 100 ] ];
   const coverFraction = [ [ 5, 5, 11 ], [ 3, 10, 4 ], [ 5, 5, 5 ], [ 10, 3, 2 ], [ 5, 3, 4 ], [ 5, 5, 0 ], [ 5, 5, NaN ] ];

   return {
      constants: [ { args: [], out: { BACKDROP_MOTION_DEFAULT: Fly.BACKDROP_MOTION_DEFAULT, HDR_MAG_RANGE: Fly.HDR_MAG_RANGE,
                                       HEADROOM_RADIUS: Fly.HEADROOM_RADIUS, HEADROOM_SIGMA: Fly.HEADROOM_SIGMA,
                                       HDR_REFERENCE_WHITE: Fly.HDR_REFERENCE_WHITE, SPIKE_LENGTH_MAX: Fly.SPIKE_LENGTH_MAX,
                                       SHUTTER: Fly.SHUTTER, TWINKLE_DEFAULT: Fly.TWINKLE_DEFAULT, GROWTH_DEFAULT: Fly.GROWTH_DEFAULT,
                                       MODEL_BLEND: Fly.MODEL_BLEND, CORE_MODEL_BLEND: Fly.CORE_MODEL_BLEND, FADE_NEAR: Fly.FADE_NEAR } } ],
      moved: moved.map( a => ( { args: a, out: Fly.moved( ...a ) } ) ),
      screenPosition: screenPosition.map( a => ( { args: a, out: Fly.screenPosition( ...a ) } ) ),
      backdropZoom: [ [ 900, 180, 1 ], [ 900, 180, 0.4 ], [ 900, 180, 0 ], [ Infinity, 180, 1 ], [ 900, 900, 0.4 ] ]
         .map( a => ( { args: a, out: Fly.backdropZoom( ...a ) } ) ),
      backdropScale: [ [ 900, 180 ], [ Infinity, 180 ], [ 900, 0 ], [ 900, 900 ] ].map( a => ( { args: a, out: Fly.backdropScale( ...a ) } ) ),
      radialSource: radialSource.map( a => ( { args: a, out: Fly.radialSource( ...a ) } ) ),
      discVisible: discVisible.map( a => ( { args: a, out: Fly.discVisible( ...a ) } ) ),
      discGridCount: discGridCount.map( a => ( { args: a, out: Fly.discGridCount( ...a ) } ) ),
      coverFraction: coverFraction.map( a => ( { args: a, out: Fly.coverFraction( ...a ) } ) ),
      headroomBump: [ [ 0, 2, 4 ], [ 2, 2, 4 ], [ 20, 2, 4 ], [ 3, 0, 4 ], [ 0, 0, 4 ] ]
         .map( a => ( { args: a, out: Fly.headroomBump( ...a ) } ) ),
      magnitudeGain: [ [ 5, 5, 4 ], [ 7.5, 5, 4 ], [ 10, 5, 4 ], [ 12, 5, 4 ], [ NaN, 5, 4 ] ]
         .map( a => ( { args: a, out: Fly.magnitudeGain( ...a ) } ) ),
      spikeLength: [ [ 1, true, 1 ], [ 3, true, 1.3 ], [ 9, true, 2.2 ], [ 3, false, 1.3 ], [ 0.5, true, 1 ], [ NaN, true, 1 ] ]
         .map( a => ( { args: a, out: Fly.spikeLength( ...a ) } ) ),
      shutterSteps: [ [ 0.2 ], [ 6 ], [ 100 ], [ 0 ], [ Infinity ], [ NaN ] ].map( a => ( { args: a, out: Fly.shutterSteps( ...a ) } ) ),
      seeded: [ [ 17, 0 ], [ 17, 5 ], [ 1, 0.5 ], [ 2.25, 8 ], [ -3.5, 1 ] ].map( a => ( { args: a, out: Fly.seeded( ...a ) } ) ),
      twinkle: [ [ 17, 1.0, 0.03, 0 ], [ 17, 2.5, 0.03, 0 ], [ 1, 2.5, 0.03, 0 ], [ 1, 2.5, 0.03, 2 ], [ 17, 2.5, 0, 0 ],
                 [ 17, 2.5, 0.03, NaN ], [ 1, 0, 0.03, 0 ] ]
         .map( a => ( { args: a, out: Fly.twinkle( ...a ) } ) ),
      growth: [ [ 1, 0.15 ], [ 3, 0.15 ], [ 2.5, 0.2 ], [ 0, 0.15 ] ].map( a => ( { args: a, out: Fly.growth( ...a ) } ) ),
      pixelScale: [ [ 2.5, 0.2, true ], [ 2.5, 0.2, false ], [ 3, 0.15, true ], [ 0, 0.15, true ], [ 2, -1, true ] ]
         .map( a => ( { args: a, out: Fly.pixelScale( ...a ) } ) ),
      coreModelWeight: [ [ 0.5 ], [ 1 ], [ 1.25 ], [ 1.5 ], [ 2 ] ].map( a => ( { args: a, out: Fly.coreModelWeight( ...a ) } ) ),
      modelWeight: [ [ 1 ], [ 2 ], [ 3 ], [ 4 ], [ 10 ] ].map( a => ( { args: a, out: Fly.modelWeight( ...a ) } ) ),
      opacity: [ [ 2, true ], [ 25, true ], [ 10, true ], [ 1.5, false ], [ 6, true ], [ 14, true ] ]
         .map( a => ( { args: a, out: Fly.opacity( ...a ) } ) ),
      ease: [ [ 0.3, "smooth" ], [ 0.3, "linear" ], [ 0.001, "smoothstep" ], [ 0, "smooth" ], [ NaN, "smooth" ] ]
         .map( a => ( { args: a, out: Fly.ease( ...a ) } ) )
   };
};
