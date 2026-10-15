"use strict";
/*
 * Golden cases for loomsiril/frames_preview_cache.py: the pure part of the preview cache in
 * pixinsight/script/lib/Cache.js (previewKeyOf and previewEvictions, 526-531 and 606-622, plus the
 * version and cap constants at 501 and 504).
 *
 * Each case records the arguments it was called with and what the JS returned. Both functions are pure, so
 * every case goes through pure(), which clones the arguments before the call. A parameter that is left out of
 * the call is recorded as a shorter argument list; a JS null is recorded as null.
 *
 * Undefined does not survive JSON. An entry field that the JS object does not have is left out of the record
 * (the key is dropped, as JSON.stringify drops it), so the replay sees the same missing field.
 */
module.exports = function( js )
{
   const C = js.Cache;
   const cases = {};

   function norm( v )
   {
      if ( v === undefined ) return "__undefined__";
      if ( Array.isArray( v ) ) return v.map( norm );
      if ( v && typeof v === "object" )
      {
         const o = {};
         for ( const k of Object.keys( v ) ) if ( v[k] !== undefined ) o[k] = norm( v[k] );
         return o;
      }
      return v;
   }

   /* A pure call: the result is what the function returns. */
   function pure( key, name, args )
   {
      const out = C[name].apply( C, structuredClone( args ) );
      cases[key] = { fn: name, args: norm( args ), out: norm( out ) };
   }

   /* A constant of Cache, by name. */
   function value( name )
   {
      cases["const/" + name] = { value: name, out: norm( C[name] ) };
   }

   value( "PREVIEW_VERSION" );
   value( "PREVIEW_CAP_BYTES" );

   /* ---- previewKeyOf: one key per render of one version of one file ----------------------------- */
   const MOD = "2026-03-14T21:05:09.123Z";
   pure( "key_of/plain", "previewKeyOf", [ "/data/light_0001.fit", 1048576, MOD, "screen", { width: 640 } ] );
   pure( "key_of/no_params", "previewKeyOf", [ "/data/light_0001.fit", 1048576, MOD, "thumb" ] );
   pure( "key_of/params_null", "previewKeyOf", [ "/data/light_0001.fit", 1048576, MOD, "thumb", null ] );
   pure( "key_of/params_empty", "previewKeyOf", [ "/data/light_0001.fit", 1048576, MOD, "thumb", {} ] );
   pure( "key_of/key_order_a", "previewKeyOf", [ "/x.fit", 10, MOD, "k", { b: 1, a: 2, c: 3 } ] );
   pure( "key_of/key_order_b", "previewKeyOf", [ "/x.fit", 10, MOD, "k", { c: 3, a: 2, b: 1 } ] );
   pure( "key_of/nested_object", "previewKeyOf",
         [ "/x.fit", 10, MOD, "k", { stretch: { midtone: 0.25, shadow: -2.8, clip: [ 0, 1 ] }, size: 512 } ] );
   pure( "key_of/nested_key_order", "previewKeyOf",
         [ "/x.fit", 10, MOD, "k", { s: { z: 1, a: 2 } } ] );
   pure( "key_of/nested_integer_keys", "previewKeyOf",
         [ "/x.fit", 10, MOD, "k", { m: { 10: "ten", 2: "two", x: "ex", 1: "one" } } ] );
   pure( "key_of/nested_array_of_objects", "previewKeyOf",
         [ "/x.fit", 10, MOD, "k", { boxes: [ { x: 1, y: 2 }, { y: 4, x: 3 } ] } ] );
   pure( "key_of/nested_string_quotes", "previewKeyOf",
         [ "/x.fit", 10, MOD, "k", { label: "say \"hi\"\n\tback\\slash" } ] );
   pure( "key_of/nested_unicode", "previewKeyOf",
         [ "/x.fit", 10, MOD, "k", { name: "M\u00f1ch \u2713 \ud83d\ude80" } ] );
   pure( "key_of/nested_null_and_bool", "previewKeyOf",
         [ "/x.fit", 10, MOD, "k", { a: null, b: true, c: false, d: "" } ] );
   pure( "key_of/nested_non_finite", "previewKeyOf",
         [ "/x.fit", 10, MOD, "k", { inf: Infinity, ninf: -Infinity, nan: NaN, nested: { v: NaN } } ] );
   pure( "key_of/top_level_numbers", "previewKeyOf",
         [ "/x.fit", 10, MOD, "k", { zero: 0, neg_zero: -0, one: 1, half: 0.5, tenth: 0.1, big: 1e21, small: 1e-7 } ] );
   pure( "key_of/top_level_nan", "previewKeyOf", [ "/x.fit", 10, MOD, "k", { v: NaN } ] );
   pure( "key_of/top_level_bool", "previewKeyOf", [ "/x.fit", 10, MOD, "k", { on: true, off: false } ] );
   pure( "key_of/top_level_null", "previewKeyOf", [ "/x.fit", 10, MOD, "k", { absent: null } ] );
   pure( "key_of/size_zero", "previewKeyOf", [ "/x.fit", 0, MOD, "k", {} ] );
   pure( "key_of/size_large", "previewKeyOf", [ "/x.fit", 26843545600, MOD, "k", {} ] );
   pure( "key_of/size_fractional", "previewKeyOf", [ "/x.fit", 1234.5, MOD, "k", {} ] );
   pure( "key_of/size_string", "previewKeyOf", [ "/x.fit", "1048576", MOD, "k", {} ] );
   pure( "key_of/modified_other_instant", "previewKeyOf", [ "/x.fit", 10, "2026-03-14T21:05:09.124Z", "k", {} ] );
   pure( "key_of/modified_no_ms", "previewKeyOf", [ "/x.fit", 10, "2026-03-14T21:05:09.000Z", "k", {} ] );
   pure( "key_of/path_with_spaces_unicode", "previewKeyOf",
         [ "/Users/me/Lights \u00e9t\u00e9/M 31 #2.fit", 10, MOD, "k", {} ] );
   pure( "key_of/path_windows_style", "previewKeyOf", [ "C:\\data\\light.fit", 10, MOD, "k", {} ] );
   pure( "key_of/kind_empty", "previewKeyOf", [ "/x.fit", 10, MOD, "", {} ] );
   pure( "key_of/kind_pipe", "previewKeyOf", [ "/x.fit", 10, MOD, "a|b", {} ] );
   pure( "key_of/field_boundary_ambiguity_a", "previewKeyOf", [ "/x|1", 2, MOD, "k", {} ] );
   pure( "key_of/field_boundary_ambiguity_b", "previewKeyOf", [ "/x", 1, "2|" + MOD, "k", {} ] );
   pure( "key_of/params_order_of_arrays", "previewKeyOf", [ "/x.fit", 10, MOD, "k", { list: [ 3, 1, 2 ] } ] );
   pure( "key_of/params_many_keys", "previewKeyOf",
         [ "/x.fit", 10, MOD, "k", { a: 1, b: "two", c: [ 3 ], d: { e: 4 }, f: null, g: true, h: 0.5, i: "" } ] );

   /* ---- previewEvictions: which keys to remove, least recently used first ------------------------ */
   const E = ( key, bytes, used ) => ( { key, bytes, used } );

   pure( "evictions/empty", "previewEvictions", [ [], 100, null ] );
   pure( "evictions/under_cap", "previewEvictions", [ [ E( "a", 40, 1 ), E( "b", 50, 2 ) ], 100, null ] );
   pure( "evictions/exactly_at_cap", "previewEvictions",
         [ [ E( "a", 40, 1 ), E( "b", 60, 2 ) ], 100, null ] );
   pure( "evictions/one_over_cap", "previewEvictions",
         [ [ E( "a", 40, 1 ), E( "b", 61, 2 ) ], 100, null ] );
   pure( "evictions/oldest_first", "previewEvictions",
         [ [ E( "new", 30, 900 ), E( "old", 30, 100 ), E( "mid", 30, 500 ) ], 60, null ] );
   pure( "evictions/evicts_until_fits", "previewEvictions",
         [ [ E( "a", 10, 1 ), E( "b", 10, 2 ), E( "c", 10, 3 ), E( "d", 10, 4 ) ], 25, null ] );
   pure( "evictions/cap_zero_removes_all", "previewEvictions",
         [ [ E( "a", 10, 1 ), E( "b", 10, 2 ) ], 0, null ] );
   pure( "evictions/keep_never_evicted", "previewEvictions",
         [ [ E( "keep", 50, 1 ), E( "b", 50, 2 ) ], 60, "keep" ] );
   pure( "evictions/keep_is_only_one_over_cap", "previewEvictions",
         [ [ E( "keep", 500, 1 ) ], 100, "keep" ] );
   pure( "evictions/keep_skipped_then_next_oldest", "previewEvictions",
         [ [ E( "x", 40, 1 ), E( "keep", 40, 2 ), E( "y", 40, 3 ) ], 50, "keep" ] );
   pure( "evictions/keep_absent_from_list", "previewEvictions",
         [ [ E( "a", 40, 1 ), E( "b", 40, 2 ) ], 50, "zzz" ] );
   pure( "evictions/keep_omitted", "previewEvictions", [ [ E( "a", 40, 1 ), E( "b", 40, 2 ) ], 50 ] );
   pure( "evictions/keep_null", "previewEvictions", [ [ E( "a", 40, 1 ), E( "b", 40, 2 ) ], 50, null ] );
   pure( "evictions/equal_used_keeps_input_order", "previewEvictions",
         [ [ E( "first", 40, 7 ), E( "second", 40, 7 ), E( "third", 40, 7 ) ], 50, null ] );
   pure( "evictions/used_zero_is_oldest", "previewEvictions",
         [ [ E( "never", 40, 0 ), E( "used", 40, 5 ) ], 50, null ] );
   pure( "evictions/large_times", "previewEvictions",
         [ [ E( "a", 1e9, 1767225600000 ), E( "b", 1e9, 1767225600001 ), E( "c", 1e9, 1767225599999 ) ], 2e9, null ] );
   pure( "evictions/large_sizes_exact", "previewEvictions",
         [ [ E( "a", 1073741824, 1 ), E( "b", 1073741824, 2 ) ], 1073741824, null ] );
   pure( "evictions/zero_byte_entries", "previewEvictions",
         [ [ E( "empty", 0, 1 ), E( "big", 100, 2 ) ], 50, null ] );
   pure( "evictions/input_not_mutated_order", "previewEvictions",
         [ [ E( "c", 30, 3 ), E( "a", 30, 1 ), E( "b", 30, 2 ) ], 50, null ] );
   pure( "evictions/fractional_bytes", "previewEvictions",
         [ [ E( "a", 10.5, 1 ), E( "b", 10.5, 2 ), E( "c", 10.5, 3 ) ], 21, null ] );
   pure( "evictions/many_entries", "previewEvictions",
         [ [ E( "e0", 7, 40 ), E( "e1", 7, 11 ), E( "e2", 7, 33 ), E( "e3", 7, 2 ), E( "e4", 7, 27 ),
             E( "e5", 7, 19 ), E( "e6", 7, 5 ), E( "e7", 7, 50 ), E( "e8", 7, 8 ), E( "e9", 7, 14 ) ], 35, "e7" ] );
   pure( "evictions/missing_used_is_not_evicted_first_alone", "previewEvictions",
         [ [ { key: "a", bytes: 40 }, E( "b", 40, 2 ) ], 50, null ] );
   pure( "evictions/missing_bytes_total_is_nan", "previewEvictions",
         [ [ { key: "a", used: 1 }, E( "b", 40, 2 ) ], 10, null ] );
   pure( "evictions/null_bytes_counts_as_zero", "previewEvictions",
         [ [ { key: "a", bytes: null, used: 1 }, E( "b", 40, 2 ) ], 30, null ] );
   pure( "evictions/nan_total_evicts_nothing", "previewEvictions",
         [ [ { key: "a", bytes: NaN, used: 1 }, E( "b", 10, 2 ) ], 5, null ] );

   return cases;
};
