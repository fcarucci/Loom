/*
 * Oracle for loomsiril/parallax.py (E1a): parallax quality, illuminator and
 * ionising-cluster finder, travel defaults and star placement from Fly.js
 * (v0.4.1). Each case is { args: [...], out: value }. Inputs are stored in the
 * JSON: the cluster sources are built here with the selftest's generator and
 * written out, so Python never rebuilds a float with a formula.
 */
"use strict";

module.exports = function( js )
{
   const Fly = js.Fly;
   const T = { ra: 100, dec: 20 };

   // selftest.js 14580-14595 style: a broad field plus a tight cluster, small enough to store
   function rng( seed ) { let a = seed >>> 0; return function() { a = ( a*1664525 + 1013904223 ) >>> 0; return a/4294967296; }; }
   function gauss( r ) { return Math.sqrt( -2*Math.log( Math.max( 1e-12, r() ) ) )*Math.cos( 2*Math.PI*r() ); }
   const r = rng( 7 ), src = [];
   for ( let i = 0; i < 300; ++i )
      src.push( { ra: 100 + ( r() - 0.5 )*6, dec: 20 + ( r() - 0.5 )*6, plx: 0.2 + 2.8*r(),
                  pmra: 6*gauss( r ), pmdec: 6*gauss( r ), G: 11 + 4*r() } );
   for ( let k = 0; k < 60; ++k )
      src.push( { ra: 100 + ( r() - 0.5 )*0.8, dec: 20 + ( r() - 0.5 )*0.8, plx: 1.1 + 0.03*gauss( r ),
                  pmra: -2.4 + 0.2*gauss( r ), pmdec: -4.6 + 0.2*gauss( r ), G: 11 + 4*r() } );
   const uniform = src.slice( 0, 300 );
   const tooFew = src.slice( 0, 200 ).concat( src.slice( 300, 320 ) );   // 20 inside, 'field' short of 30

   const S = Fly.clusterSamples( src, T, 0.5 );
   const sa = Fly.mad( S.field.map( x => x.a ) )*Fly.CLUSTER_PM_SCALE, sd = Fly.mad( S.field.map( x => x.d ) )*Fly.CLUSTER_PM_SCALE;

   const sigmaG = [ 12, 15, 16.5, 17, 17.3, 17.6, 19, NaN, 10 ];
   const plxCases = [ { plx: 1.0, G: 12 }, { plx: 0.30, G: 17 }, { plx: -0.5, G: 10 }, { plx: null, G: 10 },
                      { plx: NaN, G: 10 }, { plx: 0.2, G: 10 }, { plx: 0.05, G: 12 }, { plx: Infinity, G: 10 } ];
   const illTarget = { ra: 315.4, dec: 68.16, diameter: 18 };
   const star = ( dra, ddec, G, plx ) => ( { ra: illTarget.ra + dra, dec: illTarget.dec + ddec, G: G, plx: plx } );
   const illSources = [ star( 0.01, 0.005, 7.3, 2.8 ), star( 1.5, 0.2, 5.0, 9 ), star( -0.02, 0.01, 8.0, 0.01 ), star( 0.03, 0, 12.5, 1.1 ) ];

   // refineCluster seed far from every star: its neighbourhood is empty
   const seedFar = { a: 50, d: 50, p: 0.5, sp: 0.02 };

   // placeStars: the projection is recorded as a table, one entry per source
   const placeSrc = [ { ra: 1, dec: 1, plx: 2.0, G: 12 }, { ra: 2, dec: 2, plx: 0.01, G: 17 }, { ra: 3, dec: 3, plx: 1.0, G: 12 },
                      { ra: 4, dec: 4, plx: 0.5, G: 11 }, { ra: 5, dec: 5, plx: 4.0, G: 9 }, { ra: 6, dec: 6, plx: 2.5, G: 10 } ];
   const placePos = [ { x: 10, y: 10 }, { x: 20, y: 20 }, { x: -5, y: 10 }, null, { x: 99.5, y: 0 }, { x: 150, y: 3 } ];

   return {
      parallaxSigma: sigmaG.map( g => ( { args: [ g ], out: Fly.parallaxSigma( g ) } ) ),
      usableParallax: plxCases.map( s => ( { args: [ s ], out: Fly.usableParallax( s ) } ) ),
      illuminatorRadius: [ { diameter: 18 }, { diameter: 0 }, {}, { diameter: 2 }, { diameter: 240 } ]
         .map( t => ( { args: [ t ], out: Fly.illuminatorRadius( t ) } ) ),
      illuminatorDistance: [ [ illSources, illTarget ], [ [ star( 0.01, 0, 12.5, 1.1 ), star( 0.02, 0, 11, 2 ) ], illTarget ],
                             [ null, illTarget ] ]
         .map( a => ( { args: a, out: Fly.illuminatorDistance( ...a ) } ) ),
      clusterRadius: [ [ 0.2 ], [ 85/60 ], [ NaN ] ].map( a => ( { args: a, out: Fly.clusterRadius( ...a ) } ) ),
      clusterSamples: [ [ src, T, 0.5 ] ].map( a => ( { args: a, out: Fly.clusterSamples( ...a ) } ) ),
      findCluster: [ [ src, T, 0.5 ], [ uniform, T, 0.5 ], [ tooFew, T, 0.5 ] ]
         .map( a => ( { args: a, out: Fly.findCluster( ...a ) } ) ),
      refineCluster: [ [ S.inside, S.inside[0], sa, sd ], [ S.inside, seedFar, sa, sd ] ].map( a => {
         const near = ( c, k ) => x => {
            const da = ( x.a - c.a )/a[2], dd = ( x.d - c.d )/a[3],
                  dp = ( x.p - c.p )/( Fly.CLUSTER_PLX_SCALE*Math.max( c.sp, x.sp ) );
            return da*da + dd*dd + dp*dp <= k*k;
         };
         return { args: [ a[0], a[1], a[2], a[3] ], out: Fly.refineCluster( a[0], a[1], near ) };
      } ),
      defaultTravel: [ [ "nebula", 900 ], [ "galaxy", Infinity ], [ "nebula", 0 ], [ "other", 100 ], [ "nebula", NaN ] ]
         .map( a => ( { args: a, out: Fly.defaultTravel( ...a ) } ) ),
      clampTravel: [ [ 950, "nebula", 900 ], [ 950, "galaxy", Infinity ], [ 100, "nebula", 900 ], [ NaN, "nebula", 900 ],
                     [ 810, "nebula", 900 ] ].map( a => ( { args: a, out: Fly.clampTravel( ...a ) } ) ),
      placeStars: [ [ placeSrc, placePos, 100, 100 ], [ [], [], 100, 100 ], [ placeSrc.slice( 0, 2 ), placePos.slice( 0, 2 ), 100, 100 ] ]
         .map( a => ( { args: a, out: Fly.placeStars( a[0], s => a[1][a[0].findIndex( t => t.ra == s )] || null, a[2], a[3] ) } ) )
   };
};
