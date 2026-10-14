/*
 * Oracle for loomsiril/core.py (E0): Fly.smoothstep, Fly.median, Fly.mad.
 * Each case is { args: [...], out: value } so the Python test can replay it.
 */
"use strict";

module.exports = function( js )
{
   const Fly = js.Fly;
   const smooth = [ [ 0, 1, -0.5 ], [ 0, 1, 0 ], [ 0, 1, 0.25 ], [ 0, 1, 0.5 ], [ 0, 1, 0.8 ], [ 0, 1, 1 ], [ 0, 1, 2 ],
                    [ 2, 6, 3.3 ], [ 1, 0, 0.3 ], [ 0, 1, NaN ] ];
   const sets = [ [ 3, 1, 2 ], [ 4, 1, 3, 2 ], [ 5 ], [ 1, 1, 1, 1 ], [ -2.5, 7, 0.125, 3, 3, 9.75 ],
                  [ 870, 910, 905, 1000, 880, 2400, 950 ] ];
   return {
      RAD: Fly.RAD,
      smoothstep: smooth.map( a => ( { args: a, out: Fly.smoothstep( a[0], a[1], a[2] ) } ) ),
      median: sets.map( v => ( { args: [ v ], out: Fly.median( v ) } ) ),
      mad: sets.map( v => ( { args: [ v ], out: Fly.mad( v ) } ) ),
      grownRect: [ [ { x0: 3, y0: 4, x1: 9, y1: 10 }, 3, 20, 12 ], [ { x0: 0, y0: 0, x1: 2, y1: 2 }, 5, 4, 4 ] ]
         .map( a => ( { args: a, out: Fly.grownRect( a[0], a[1], a[2], a[3] ) } ) ),
      detectionRect: [ [ 10.2, 7.8, 0 ], [ 10.5, 7.5, 12.566370614359172 ], [ 33.7, 2.1, 50 ], [ 5, 5, 3.5342917352885173 ] ]
         .map( a => ( { args: a, out: Fly.detectionRect( a[0], a[1], a[2] ) } ) ),
      squareRect: [ [ 10.4, 3.6, 4, 20, 12 ], [ 1.2, 11.5, 2.5, 20, 12 ] ]
         .map( a => ( { args: a, out: Fly.squareRect( a[0], a[1], a[2], a[3], a[4] ) } ) ),
      jsRound: [ 0.5, 1.5, 2.5, -0.5, -1.5, 2.4999 ].map( v => ( { args: [ v ], out: Math.round( v ) } ) ),
      vec: [ [ 324.745, 57.514 ], [ 0, 0 ], [ 359.9, -89.5 ] ].map( a => ( { args: a, out: Fly.vec( a[0], a[1] ) } ) ),
      radec: [ [ 0.3, -0.2, 0.9 ], [ -1, -1e-12, 0 ], [ 0.2, 0.4, -2 ] ].map( v => ( { args: [ v ], out: Fly.radec( v ) } ) ),
      separation: [ [ { ra: 324.745, dec: 57.514 }, { ra: 325.5, dec: 58.1 } ], [ { ra: 10, dec: 0 }, { ra: 10, dec: 0 } ],
                    [ { ra: 0, dec: 89 }, { ra: 180, dec: 89 } ] ].map( a => ( { args: a, out: Fly.separation( a[0], a[1] ) } ) ),
      tanProject: ( function()
      {
         const w = { crval1: 324.745, crval2: 57.514, crpix1: 200.5, crpix2: 150.5, cd: [ -0.0005, 0.00001, 0.00002, 0.0005 ] };
         return [ [ 324.745, 57.514 ], [ 324.745, 57.564 ], [ 324.9, 57.4 ], [ 324.5, 57.7 ] ]
            .map( a => ( { args: [ a[0], a[1], w ], out: Fly.tanProject( a[0], a[1], w ) } ) );
      } )(),
      tanUnproject: ( function()
      {
         const w = { crval1: 324.745, crval2: 57.514, crpix1: 200.5, crpix2: 150.5, cd: [ -0.0005, 0.00001, 0.00002, 0.0005 ] };
         return [ [ 199.5, 149.5 ], [ 0, 0 ], [ 399, 299 ], [ 250.25, 10.75 ] ]
            .map( a => ( { args: [ a[0], a[1], w ], out: Fly.tanUnproject( a[0], a[1], w ) } ) );
      } )(),
      ease: [ 0, 0.3, 1 ].map( t => ( { args: [ t, "smooth" ], out: Fly.ease( t, "smooth" ) } ) )
   };
};
