/*
 * Oracle for loomsiril/wcs.py (E2b): Sky.scaledWcs, and Fly.tanProject /
 * Fly.tanUnproject of a scaled wcs. Sky.keywordNumber and Sky.keywordWcs take a
 * PixInsight window, so their quote handling is pinned with literals in Python.
 */
"use strict";

module.exports = function( js )
{
   const Fly = js.Fly, Sky = js.Sky;
   const base = { crval1: 324.745, crval2: 57.514, crpix1: 200.5, crpix2: 150.5, cd: [ -0.0005, 0.00001, 0.00002, 0.0005 ] };
   const odd = { crval1: 10.684708, crval2: 41.26875, crpix1: 1.0, crpix2: 3000.25, cd: [ -0.000277778, 0, 0, 0.000277778 ] };
   const wcsIn = [ base, odd ];
   const factors = [ 1, 2, 0.5, 3, 0.37 ];
   const scaled = [];
   for ( const w of wcsIn ) for ( const f of factors ) scaled.push( { args: [ w, f ], out: Sky.scaledWcs( w, f ) } );
   const sky = [ [ 324.745, 57.514 ], [ 324.745, 57.564 ], [ 324.9, 57.4 ], [ 324.5, 57.7 ] ];
   const pix = [ [ 199.5, 149.5 ], [ 0, 0 ], [ 399, 299 ], [ 250.25, 10.75 ] ];
   const s2 = Sky.scaledWcs( base, 2 );
   const s05 = Sky.scaledWcs( odd, 0.5 );
   return {
      scaledWcs: scaled,
      tanProjectScaled: sky.map( a => ( { args: [ a[0], a[1], s2 ], out: Fly.tanProject( a[0], a[1], s2 ) } ) )
         .concat( sky.map( a => ( { args: [ a[0], a[1], s05 ], out: Fly.tanProject( a[0], a[1], s05 ) } ) ) ),
      tanUnprojectScaled: pix.map( a => ( { args: [ a[0], a[1], s2 ], out: Fly.tanUnproject( a[0], a[1], s2 ) } ) )
         .concat( pix.map( a => ( { args: [ a[0], a[1], s05 ], out: Fly.tanUnproject( a[0], a[1], s05 ) } ) ) )
   };
};
