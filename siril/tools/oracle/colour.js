/*
 * Oracle for loomsiril/colour.py (E2a): Fly's colour pipeline (lib/Fly.js
 * 1334-1623) and the profile parser's synthetic profiles (selftest.js
 * fly.colour, built here byte for byte as the selftest builds them).
 * Each scalar case is { args: [...], out: value }; the output transforms are
 * recorded on stored input triples (Float32 values, as the JS stores them).
 */
"use strict";

module.exports = function( js )
{
   const Fly = js.Fly;
   const PROPHOTO = [ 0.7976749, 0.1351917, 0.0313534, 0.2880402, 0.7118741, 0.0000857, 0, 0, 0.8252100 ];
   const G18 = { type: "gamma", g: 1.8 };
   const curves = [
      { type: "identity" },
      G18,
      { type: "table", t: [ 0, 0.25, 1 ] },
      { type: "table", t: [ 0, 0.02, 0.1, 0.3, 0.7, 1 ] },
      Fly.SRGB_CURVE,
      { type: "para", fn: 0, p: [ 2.2 ] },
      { type: "para", fn: 1, p: [ 2.4, 0.9, 0.05 ] },
      { type: "para", fn: 2, p: [ 2.0, 1.1, -0.05, 0.02 ] },
      { type: "para", fn: 4, p: [ 2.6, 0.92, 0.07, 0.08, 0.04, 0.01, -0.02 ] } ];
   const xs = [ -0.5, 0, 0.0001, 0.02, 0.04045, 0.3, 0.5, 0.75, 1, 1.5 ];
   const paraCases = [ 0, 1, 2, 3, 4 ].map( fn => [ fn, [ 2.4, 0.9, 0.05, 0.08, 0.04, 0.02, -0.01 ] ] );
   const mats = [ Fly.SRGB_COLOUR.matrix, PROPHOTO ];

   // ---- the synthetic profiles, as selftest.js fly.colour builds them -------
   function be32( n ) { return [ ( n >>> 24 ) & 255, ( n >>> 16 ) & 255, ( n >>> 8 ) & 255, n & 255 ]; }
   function ascii( t ) { return t.split( "" ).map( c => c.charCodeAt( 0 ) ); }
   function s15( v ) { return be32( Math.round( v*65536 ) >>> 0 ); }
   function xyz( a, b, c ) { return ascii( "XYZ " ).concat( [ 0, 0, 0, 0 ], s15( a ), s15( b ), s15( c ) ); }
   const curv18 = ascii( "curv" ).concat( [ 0, 0, 0, 0 ], be32( 1 ), [ 1, 204, 0, 0 ] );
   function profile( tags )
   {
      const head = []; for ( let i = 0; i < 128; ++i ) head.push( 0 );
      ascii( "mntrRGB " ).forEach( ( b, i ) => { head[12 + i] = b; } );
      let table = be32( tags.length ), body = [];
      const off = 128 + 4 + 12*tags.length;
      tags.forEach( t => { table = table.concat( ascii( t[0] ), be32( off + body.length ), be32( t[1].length ) ); body = body.concat( t[1] ); } );
      return head.concat( table, body );
   }
   const matrixCurve = profile( [ [ "rXYZ", xyz( 0.7976749, 0.2880402, 0 ) ], [ "gXYZ", xyz( 0.1351917, 0.7118741, 0 ) ],
                                  [ "bXYZ", xyz( 0.0313534, 0.0000857, 0.82521 ) ], [ "rTRC", curv18 ], [ "gTRC", curv18 ],
                                  [ "bTRC", curv18 ] ] );
   const grayCurve = profile( [ [ "kTRC", curv18 ] ] );
   const noMatrix = ( function() { const head = []; for ( let i = 0; i < 128; ++i ) head.push( 0 ); return head.concat( be32( 0 ) ); } )();
   const lutOnly = profile( [ [ "A2B0", ascii( "mft2" ).concat( [ 0, 0, 0, 0 ], [ 3, 3, 0, 0 ] ) ] ] );
   const profiles = { matrixCurve, grayCurve, noMatrix, lutOnly };

   // ---- the output-transform input set: 16 triples, incl. 0, 1, >1 excess ----
   const trip = [
      [ 0, 0, 0, 0, 0, 0 ], [ 1, 1, 1, 0, 0, 0 ], [ 0.5, 0.5, 0.5, 0, 0, 0 ], [ 0.2, 0.8, 0.2, 0, 0, 0 ],
      [ 0.25, 0.5, 0.75, 0, 0, 0 ], [ 1, 0.5, 0.25, 20, 10, 5 ], [ 1, 1, 1, 2, 2, 2 ], [ 1, 1, 1, 50, 50, 50 ],
      [ 0.9, 0.3, 0.1, 0.5, 0, 0 ], [ 0.05, 0.01, 0.6, 0, 0.3, 0 ], [ 0.7, 0.7, 0.2, 3.5, 3.5, 0.1 ],
      [ 0.02, 0.4, 0.99, 0, 0, 1.2 ], [ 0.33, 0.66, 0.99, 7, 0, 0 ], [ 0.0031, 0.0404, 0.6, 0, 0, 0 ],
      [ 1, 0, 0, 0.2, 0.2, 0.2 ], [ 0, 1, 0, 0, 0, 0 ] ];
   const f32 = v => Array.from( new Float32Array( v ) );
   const col = trip.map( t => t[0] ), cog = trip.map( t => t[1] ), cob = trip.map( t => t[2] );
   const xr = trip.map( t => t[3] ), xg = trip.map( t => t[4] ), xb = trip.map( t => t[5] );
   const colours = { srgb: Fly.SRGB_COLOUR, prophoto: { matrix: PROPHOTO, trc: [ G18, G18, G18 ], name: "ProPhoto" },
                     gray: { gray: true, matrix: null, trc: [ G18, G18, G18 ] } };
   function run( colour, mode, opts, gray )
   {
      const t = Fly.outputTransform( colour, mode, opts || {} );
      if ( gray )
      {
         const K = new Float32Array( col ), X = new Float32Array( xr );
         t.applyGray( K, trip.length, X );
         return { rgb: Array.from( K ), stats: t.stats || null };
      }
      const R = new Float32Array( col ), G = new Float32Array( cog ), B = new Float32Array( cob );
      t.apply( R, G, B, trip.length, new Float32Array( xr ), new Float32Array( xg ), new Float32Array( xb ) );
      return { rgb: [ Array.from( R ), Array.from( G ), Array.from( B ) ], stats: t.stats || null };
   }
   const transforms = [];
   [ [ "srgb", "sdr", {} ], [ "srgb", "pq", { peak: 1000 } ], [ "srgb", "hlg", {} ], [ "prophoto", "sdr", {} ],
     [ "prophoto", "pq", { peak: 4000 } ], [ "prophoto", "hlg", {} ], [ "gray", "sdr", {} ], [ "gray", "pq", { peak: 1000 } ],
     [ "gray", "hlg", {} ] ].forEach( c =>
   {
      transforms.push( { colour: c[0], mode: c[1], opts: c[2], input: { r: f32( col ), g: f32( cog ), b: f32( cob ), xr: f32( xr ),
                         xg: f32( xg ), xb: f32( xb ) }, out: run( colours[c[0]], c[1], c[2], c[0] == "gray" ) } );
   } );

   return {
      matMul: [ [ Fly.SRGB_COLOUR.matrix, PROPHOTO ], [ PROPHOTO, Fly.BRADFORD_D50_D65 ] ]
         .map( a => ( { args: a, out: Fly.matMul( a[0], a[1] ) } ) ),
      trcDecode: curves.flatMap( c => xs.map( x => ( { args: [ c, x ], out: Fly.trcDecode( c, x ) } ) ) ),
      tableCurve: [ [ [ 0, 0.25, 1 ], 0.75 ], [ [ 0, 0.02, 0.1, 0.3, 0.7, 1 ], 0.33 ], [ [ 0, 0.25, 1 ], 1 ], [ [ 0, 0.25, 1 ], 0 ] ]
         .map( a => ( { args: a, out: Fly.tableCurve( a[0], a[1] ) } ) ),
      paraCurve: paraCases.flatMap( pc => xs.map( x => ( { args: [ pc[0], pc[1], x ], out: Fly.paraCurve( pc[0], pc[1], x ) } ) ) ),
      srgbEncode: xs.map( x => ( { args: [ x ], out: Fly.srgbEncode( x ) } ) ),
      pqEncode: [ 0, 0.01, 1, 5, 100, 203, 1000, 4000, 10000, 12000 ].map( x => ( { args: [ x ], out: Fly.pqEncode( x ) } ) ),
      hlgEncode: [ -0.1, 0, 1/12, 0.05, 0.2, 0.5, 1, 1.5 ].map( x => ( { args: [ x ], out: Fly.hlgEncode( x ) } ) ),
      hlgFromDisplay: [ [ 0.203, 0.203, 0.203 ], [ 0.5, 0.1, 0.02 ], [ 0, 0, 0 ], [ 2, 1, 0.5 ] ]
         .map( a => ( { args: [ a ], out: Fly.hlgFromDisplay( a ) } ) ),
      rolloff: [ [ 0.6, 1000/203 ], [ 1, 1000/203 ], [ 1.5, 1000/203 ], [ 3, 1000/203 ], [ 20, 1000/203 ], [ 1e6, 1000/203 ],
                 [ 2.5, 4000/203 ], [ 0, 4.9 ] ].map( a => ( { args: a, out: Fly.rolloff( a[0], a[1] ) } ) ),
      decodeLut: curves.flatMap( c => [ 0, 0.0001, 0.02, 0.3, 0.5, 0.9999, 1 ].map( x => ( { args: [ c, x ], out: Fly.decodeLut( c )( x ) } ) ) ),
      sourceToTarget: [ [ Fly.SRGB_COLOUR.matrix, "rec709" ], [ Fly.SRGB_COLOUR.matrix, "rec2020" ], [ PROPHOTO, "rec709" ],
                        [ PROPHOTO, "rec2020" ] ].map( a => ( { args: a, out: Fly.sourceToTarget( a[0], a[1] ) } ) ),
      parseIccColour: Object.keys( profiles ).map( k => ( { args: [ profiles[k] ], name: k, bytes: profiles[k],
                                                           out: Fly.parseIccColour( ( i => profiles[k][i] ), profiles[k].length ) } ) ),
      stats: ( function()
      {
         // selftest fly.hdr: MaxCLL / MaxFALL of a two-pixel PQ frame (white, black), zero excess
         const t = Fly.outputTransform( Fly.SRGB_COLOUR, "pq", { peak: 1000 } );
         const R = new Float32Array( [ 1, 0 ] ), G = new Float32Array( [ 1, 0 ] ), B = new Float32Array( [ 1, 0 ] );
         t.apply( R, G, B, 2, new Float32Array( 2 ), new Float32Array( 2 ), new Float32Array( 2 ) );
         return { maxCll: t.stats.maxCll, maxFall: t.stats.maxFall };
      } )(),
      transforms: transforms
   };
};
