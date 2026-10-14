/*
 * Recorder for the registration reference: the fallback ranking, the usable
 * star measurement, the best measured channel and the reference itself
 * (lib/Pipeline.js:1973-2101). Inputs follow the selftest fixtures
 * (script/selftest.js:11703-11790) plus presence sets with nothing measured,
 * exact ties, NaN and infinite FWHM, and zero or sub-unit star counts.
 */
"use strict";

module.exports = function( js )
{
   const { Pipeline } = js;
   const sharpRich = { fwhm: 2.8, stars: 3000 };

   const ranking = [
      [],
      [ "O" ],
      [ "H", "O", "L" ],
      [ "Z", "A", "R", "G", "x" ],
      [ "R", "R", "Q", "Q" ],
      [ "O", "S", "H", "B", "R", "G" ],
      [ "B", "G", "R" ],
      [ "S", "H" ]
   ].map( present => ( { present, out: Pipeline.registrationFallbackRanking( present ) } ) );

   const usable = [
      null,
      {},
      { fwhm: 3.0, stars: 1 },
      { fwhm: 3.0, stars: 0.5 },
      { fwhm: 3.0, stars: 0 },
      { fwhm: 3.0, stars: -4 },
      { fwhm: 0, stars: 10 },
      { fwhm: -1, stars: 9000 },
      { fwhm: 1e-9, stars: 1 },
      { fwhm: NaN, stars: 5 },
      { fwhm: 3.0, stars: NaN },
      { fwhm: Infinity, stars: 5 },
      { fwhm: 3.0, stars: Infinity },
      { fwhm: null, stars: 4 },
      { fwhm: 3.0, stars: null },
      { fwhm: 3.0 },
      { stars: 4 },
      { fwhm: 3.2, stars: 3000 }
   ].map( q => ( { q, out: Pipeline.usableStarMeasure( q ) } ) );

   // Separating pair: FWHM/sqrt(stars) picks R (0.283 < 0.300); FWHM/stars would pick G (0.030 < 0.040).
   const sqrtSeparating = { G: { fwhm: 3.0, stars: 100 }, R: { fwhm: 2.0, stars: 50 } };

   const best = [
      { ranked: [ "G", "R" ], quality: sqrtSeparating },
      { ranked: [ "R", "G" ], quality: sqrtSeparating },
      { ranked: [ "B", "G", "R" ], quality: { ...sqrtSeparating, B: { fwhm: 2.5, stars: 40 } } },
      { ranked: [ "R", "G", "B" ], quality: {} },
      { ranked: [ "R", "G", "B" ], quality: null },
      { ranked: [ "R", "G" ], quality: { R: sharpRich, G: sharpRich } },
      { ranked: [ "G", "R" ], quality: { R: sharpRich, G: sharpRich } },
      { ranked: [ "R", "G", "B" ], quality: { R: { fwhm: 3.4, stars: 2400 }, G: { fwhm: 3.0, stars: 2600 }, B: { fwhm: 3.9, stars: 1900 } } },
      { ranked: [ "H", "S", "O" ], quality: { H: { fwhm: 3.2, stars: 3000 }, S: { fwhm: 3.4, stars: 900 }, O: { fwhm: 2.9, stars: 150 } } },
      { ranked: [ "R", "G", "B" ], quality: { R: { fwhm: 0, stars: 5000 }, G: { fwhm: NaN, stars: 5000 }, B: { fwhm: 3.0, stars: 0 } } },
      { ranked: [ "H", "O" ], quality: { H: { fwhm: -1, stars: 9000 }, O: { fwhm: 3.0, stars: 200 } } },
      { ranked: [ "R", "G", "B" ], quality: { R: { fwhm: 4.0, stars: 1000 }, G: null, B: { fwhm: 3.5, stars: 1200 } } },
      { ranked: [ "R", "G", "B", "H" ], quality: { R: { fwhm: 3.0, stars: 1000 }, G: { fwhm: 3.0, stars: 1000 }, B: { fwhm: 3.0, stars: 1000 }, H: { fwhm: 3.0, stars: 1000 } } }
   ].map( c => ( { ranked: c.ranked, quality: c.quality, out: Pipeline.bestMeasuredChannel( c.ranked, c.quality || {} ) } ) );

   const rr = ( present, quality ) => {
      const choice = Pipeline.registrationReference( present, quality );
      return { present, quality, out: choice.key, reason: choice.reason };
   };
   const reference = [
      rr( [ "G", "R" ], sqrtSeparating ),
      rr( [ "R", "G" ], sqrtSeparating ),
      rr( [ "L", "R", "G", "B" ], { L: { fwhm: 6.0, stars: 50 }, G: sharpRich } ),
      rr( [ "H", "O", "L" ], {} ),
      rr( [ "R", "G", "B" ], { R: { fwhm: 3.4, stars: 2400 }, G: { fwhm: 3.0, stars: 2600 }, B: { fwhm: 3.9, stars: 1900 } } ),
      rr( [ "H", "S", "O" ], { H: { fwhm: 3.2, stars: 3000 }, S: { fwhm: 3.4, stars: 900 }, O: { fwhm: 2.9, stars: 150 } } ),
      rr( [ "R", "G", "B" ], { R: { fwhm: 6.0, stars: 2000 }, G: { fwhm: 3.0, stars: 1800 }, B: { fwhm: 5.5, stars: 1900 } } ),
      rr( [ "R", "G", "B" ], {} ),
      rr( [ "R", "G", "B", "H" ], null ),
      rr( [ "O", "S", "H" ], {} ),
      rr( [ "O", "S" ], {} ),
      rr( [ "O" ], {} ),
      rr( [ "R", "G", "B" ], { R: { fwhm: 4.0, stars: 1000 }, G: null, B: { fwhm: 3.5, stars: 1200 } } ),
      rr( [ "R", "G", "B" ], { R: { fwhm: 0, stars: 5000 }, G: { fwhm: NaN, stars: 5000 }, B: { fwhm: 3.0, stars: 0 } } ),
      rr( [ "H", "O" ], { H: { fwhm: -1, stars: 9000 }, O: { fwhm: 3.0, stars: 200 } } ),
      rr( [ "R", "G" ], { R: sharpRich, G: sharpRich } ),
      rr( [ "S", "H" ], { S: sharpRich, H: sharpRich } ),
      rr( [ "B", "G", "R" ], { R: { fwhm: 3.4, stars: 2400 }, G: { fwhm: 3.0, stars: 2600 }, B: { fwhm: 3.9, stars: 1900 } } ),
      rr( [ "R", "G", "B" ], { R: { fwhm: 3.4, stars: 2400 }, G: { fwhm: 3.0, stars: 2600 }, B: { fwhm: 3.9, stars: 1900 } } ),
      rr( [], {} ),
      rr( [ "R", "G", "B" ], { R: { fwhm: 3.4, stars: 2400 }, G: { fwhm: 3.0, stars: 2600 }, B: { fwhm: 3.9, stars: 1900 } } ),
      rr( [ "H", "O", "Z", "A" ], { Z: { fwhm: 2.0, stars: 4000 }, A: { fwhm: 2.0, stars: 4000 } } ),
      rr( [ "R", "Q" ], { Q: { fwhm: 2.0, stars: 4000 }, R: { fwhm: 2.0, stars: 4000 } } ),
      rr( [ "R", "G" ], { R: { fwhm: 3.0, stars: 2000 }, G: { fwhm: 3.0, stars: 2000 } } )
   ];

   return { ranking, usable, best, reference };
};
