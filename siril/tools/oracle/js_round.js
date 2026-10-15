"use strict";
/*
 * Golden cases for loomsiril.core.js_round: the JavaScript Math.round of the
 * Fly-Through library set. Each case records its input ("in") and what
 * Math.round returned ("out"), so the Python suite replays the same values.
 *
 * The named cases are the edge values where a floor(x + 0.5) rounding goes wrong
 * (the sum rounds up, or the addition is inexact above 2^52) and the non-finite
 * values, which Math.round passes through. The seeded cases span every magnitude:
 * half are raw IEEE 754 bit patterns (finite ones only), half are 1 + u scaled by
 * a power of two in [2^-8, 2^56], with a random sign. The generator is a fixed
 * 32-bit PRNG (mulberry32), so the recorded inputs are the same on every run.
 */
module.exports = function( js )
{
   const cases = {};

   const named = [
      [ "half_below_one_ulp", 0.49999999999999994 ],
      [ "half", 0.5 ],
      [ "minus_half", -0.5 ],
      [ "one_and_half", 1.5 ],
      [ "two_and_half", 2.5 ],
      [ "minus_two_and_half", -2.5 ],
      [ "minus_one_and_half_above", -1.5000000000000002 ],
      [ "two_pow_52_plus_one", 2 ** 52 + 1 ],
      [ "two_pow_52_plus_half", 2 ** 52 + 0.5 ],
      [ "minus_two_pow_52_minus_one", -( 2 ** 52 ) - 1 ],
      [ "two_pow_53", 2 ** 53 ],
      [ "1e21", 1e21 ],
      [ "minus_1e21", -1e21 ],
      [ "zero", 0 ],
      [ "minus_zero", -0 ],
      [ "min_subnormal", 5e-324 ],
      [ "nan", NaN ],
      [ "infinity", Infinity ],
      [ "minus_infinity", -Infinity ]
   ];
   for ( const [ name, x ] of named ) cases[name] = { in: x, out: Math.round( x ) };

   /* mulberry32: a small seeded generator, identical on every run. */
   let state = 0x2545F491;
   function rnd()
   {
      state = ( state + 0x6D2B79F5 ) | 0;
      let t = Math.imul( state ^ ( state >>> 15 ), 1 | state );
      t = ( t + Math.imul( t ^ ( t >>> 7 ), 61 | t ) ) ^ t;
      return ( ( t ^ ( t >>> 14 ) ) >>> 0 ) / 4294967296;
   }

   const buf = new DataView( new ArrayBuffer( 8 ) );
   function bitPattern()
   {
      for ( ;; )
      {
         buf.setUint32( 0, ( rnd() * 4294967296 ) >>> 0 );
         buf.setUint32( 4, ( rnd() * 4294967296 ) >>> 0 );
         const x = buf.getFloat64( 0 );
         if ( isFinite( x ) ) return x;
      }
   }

   function scaled()
   {
      const e = Math.floor( rnd() * 65 ) - 8;
      const sign = rnd() < 0.5 ? -1 : 1;
      return sign * ( 1 + rnd() ) * Math.pow( 2, e );
   }

   for ( let i = 0; i < 100; i++ )
   {
      const x = bitPattern();
      cases["bits_" + String( i ).padStart( 3, "0" )] = { in: x, out: Math.round( x ) };
   }
   for ( let i = 0; i < 100; i++ )
   {
      const x = scaled();
      cases["scaled_" + String( i ).padStart( 3, "0" )] = { in: x, out: Math.round( x ) };
   }

   return cases;
};
