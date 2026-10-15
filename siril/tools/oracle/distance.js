/*
 * Oracle for the distance lookup (loomsiril.pipeline._find_distance, parallax.find_cluster /
 * illuminator_distance, gaia.online_url) on a FROZEN real Gaia star list: the Sh2-132 field of the user's image,
 * fetched live once (VizieR I/355/gaiadr3, G < 17.6, 1.35 deg round the image centre and round the Sesame
 * position), trimmed and stored as tests/fixtures/sh2-132-gaia.json.gz (columns ra, dec, plx, pmra, pmdec, G).
 *
 * What FlyThrough.clusterDistance does, step by step, with Fly.js and Sky.js as they are:
 *   cluster:     Fly.findCluster( Sky.clusterSources( target, r ), target, Fly.clusterRadius( r ) ), the sample being
 *                the Gaia stars within 3 * Fly.clusterRadius( r ) of the TARGET; r = diameter/120, else the field radius.
 *   illuminator: Fly.illuminatorDistance( id.sources, target ): id.sources are the stars of the image's field
 *                (centre, Sky.field radius), the star taken within Fly.illuminatorRadius( target ) of the TARGET.
 * FlyThrough.js itself is a dialog file and is not loaded here: its two steps are written out with the same calls.
 * The recorded targets are the image centre (what the Siril dialog held) and the Sesame position of Sh2-132.
 */
"use strict";

const fs = require( "fs" ), zlib = require( "zlib" ), path = require( "path" );

module.exports = function( js )
{
   const Fly = js.Fly, Sky = js.Sky;
   const doc = JSON.parse( zlib.gunzipSync( fs.readFileSync( path.join( __dirname, "..", "..", "tests", "fixtures", "sh2-132-gaia.json.gz" ) ) ).toString() );
   const c = doc.cols, nan = v => v === null ? NaN : v;
   const rows = c.ra.map( ( ra, i ) => ( { ra: ra, dec: c.dec[i], plx: nan( c.plx[i] ), pmra: nan( c.pmra[i] ), pmdec: nan( c.pmdec[i] ), G: nan( c.G[i] ) } ) );

   // the image's TAN WCS (top-down rows, CD from CDELT*PC of the FITS header) and Sky.field's rule: centre, farthest corner
   const wcs = { crval1: 334.855821518434, crval2: 56.027845769519, crpix1: 5899.5, crpix2: 3606.5,
                 cd: [ -0.00013412373202529825, -1.2586843696298023e-06, 1.2760243881540134e-06, -0.00013412612977424236 ] };
   const W = 11799, H = 7211;
   const centre = Fly.tanUnproject( W/2, H/2, wcs );
   let fieldRadius = 0;
   [ [ 0, 0 ], [ W, 0 ], [ 0, H ], [ W, H ] ].forEach( p => { fieldRadius = Math.max( fieldRadius, Fly.separation( centre, Fly.tanUnproject( p[0], p[1], wcs ) ) ); } );

   const within = ( cen, r, list ) => list.filter( s => Fly.separation( s, cen ) <= r );
   const imageCentre = { ra: wcs.crval1, dec: wcs.crval2 }, sesame = { ra: 334.7875, dec: 56.0791 };

   // FlyThrough.clusterDistance's two searches for one target, on a star list standing for Gaia
   function distance( target, list )
   {
      const r = ( target.diameter > 0 ? target.diameter/120 : fieldRadius );
      const clusterRadius = Fly.clusterRadius( r );
      const clusterSample = within( target, 3*clusterRadius, list );
      const S = Fly.clusterSamples( clusterSample, target, clusterRadius );
      const fieldSources = within( centre, fieldRadius, list );
      const ill = Fly.illuminatorDistance( fieldSources, target );
      return { target: target, r: r, clusterRadius: clusterRadius, illuminatorRadius: Fly.illuminatorRadius( target ),
               clusterSources: clusterSample.length, usable: clusterSample.filter( s => Fly.usableParallax( s ) != null ).length,
               inside: S.inside.length, ring: S.field.length, fieldSources: fieldSources.length,
               cluster: Fly.findCluster( clusterSample, target, clusterRadius ), illuminator: ill,
               illuminatorCandidates: within( target, Fly.illuminatorRadius( target ), list ).filter( s => s.G < Fly.ILLUMINATOR_MAX_G ).length };
   }

   // a 45-star cluster 4.1 kpc away round the Sesame position, drawn from a fixed generator (stored with the case)
   function rng( seed ) { let a = seed >>> 0; return function() { a = ( a*1664525 + 1013904223 ) >>> 0; return a/4294967296; }; }
   function gauss( r ) { return Math.sqrt( -2*Math.log( Math.max( 1e-12, r() ) ) )*Math.cos( 2*Math.PI*r() ); }
   const rg = rng( 132 ), injected = [];
   for ( let i = 0; i < 45; ++i )
      injected.push( { ra: +( sesame.ra + ( rg() - 0.5 )*0.5 ).toFixed( 6 ), dec: +( sesame.dec + ( rg() - 0.5 )*0.5 ).toFixed( 6 ),
                       plx: +( 0.245 + 0.015*gauss( rg ) ).toFixed( 4 ), pmra: +( -7.9 + 0.15*gauss( rg ) ).toFixed( 3 ),
                       pmdec: +( 3.3 + 0.15*gauss( rg ) ).toFixed( 3 ), G: +( 11 + 4*rg() ).toFixed( 3 ) } );

   return {
      field: { width: W, height: H, wcs: wcs, centre: centre, radiusDeg: fieldRadius, rows: rows.length },
      limits: { gaiaMagLimit: Sky.GAIA_MAG_LIMIT, illuminatorMaxG: Fly.ILLUMINATOR_MAX_G, illuminatorMinRadius: Fly.ILLUMINATOR_MIN_RADIUS,
                clusterMaxRadius: Fly.CLUSTER_MAX_RADIUS, clusterMinMembers: Fly.CLUSTER_MIN_MEMBERS,
                clusterMinContrast: Fly.CLUSTER_MIN_CONTRAST, clusterMinSignificance: Fly.CLUSTER_MIN_SIGNIFICANCE,
                onlineMaxRows: Fly.GAIA_ONLINE_MAX_ROWS, onlineUrl: Fly.GAIA_ONLINE_URL, onlineColumns: Fly.GAIA_ONLINE_COLUMNS },
      urls: [ imageCentre, sesame ].map( t => ( { target: t,
         cluster: Fly.gaiaOnlineUrl( t, 3*Fly.clusterRadius( fieldRadius ), Sky.GAIA_MAG_LIMIT ),
         illuminator: Fly.gaiaOnlineUrl( t, Fly.illuminatorRadius( t ), Fly.ILLUMINATOR_MAX_G ),
         field: Fly.gaiaOnlineUrl( centre, fieldRadius, Sky.GAIA_MAG_LIMIT ) } ) ),
      imageCentre: distance( imageCentre, rows ),
      sesame: distance( sesame, rows ),
      sesameWithCluster: { injected: injected, out: distance( sesame, rows.concat( injected ) ) }
   };
};
