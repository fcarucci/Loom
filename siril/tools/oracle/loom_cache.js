/*
 * Recorder for the stage-cache keys and cache decisions of Loom's pipeline
 * (lib/Cache.js, lib/Pipeline.js). Feeds tests/oracle/loom_cache.json.
 *
 * Cache lookups are replaced by fakes driven by a per-entry state while
 * Pipeline.resolveCacheHit and Pipeline.cacheEntryComplete run unchanged; the
 * originals are restored afterwards.
 */
"use strict";

module.exports = function( js )
{
   const { Cache, Pipeline, Util } = js;

   const out = {};

   /* ---- hashing and parameter strings ------------------------------ */
   out.hash_empty = Cache.hash( "" );
   out.hash_abc = Cache.hash( "abc" );
   out.hash_unicode = Cache.hash( "Ha ° é" );

   const params = {
      params_null: null,
      params_empty: {},
      params_sorted: { b: 2, a: "x" },
      params_nested: { z: { y: [ 1, 2, { q: "w" } ], x: null }, a: [ true, null, 1.5 ] },
      params_booleans: { t: true, f: false },
      params_null_value: { n: null },
      params_floats: { one: 1.0, half: 0.5, sum: 0.1 + 0.2, big: 1e21, small: 1e-7, negz: -0 },
      params_quotes: { s: 'he said "hi" \\ ok', n: { s: 'q"b\\' } },
      params_unicode: { u: "é ° 😀", n: { u: "é" } },
      params_numeric_keys: { b: 1, "10": 2, "2": 3 },
      params_nested_numeric_keys: { n: { b: 1, "10": 2, "2": 3 } },
      params_nested_numbers: { n: { z: -0, f: 1.0, e: 1e21, h: 0.5 } },
      params_nan_infinity: { nan: NaN, inf: Infinity, ninf: -Infinity, n: { x: NaN, y: Infinity } }
   };
   for ( const name of Object.keys( params ) )
      out[name] = Cache.paramsString( params[name] );
   out.params_string_json_nested = Cache.paramsString( { n: { z: -0, f: 1.0, e: 1e21 } } );

   /* ---- chain keys --------------------------------------------------- */
   out.chain_key_null_prev = Cache.chainKey( null, "solve", { a: 1 } );
   out.chain_key_empty_prev = Cache.chainKey( "", "solve", null );
   out.chain_key_prev_params = Cache.chainKey( "abc123", "mgc", { k: "v", n: 0.5 } );
   out.chain_key_keep_linear = Cache.chainKey( "abc", "stretchRGB", { keepLinear: true, strength: 1.0 } );
   out.chain_key_nested = Cache.chainKey( "0123", "graxpert", { model: { name: "bge", tiles: [ 256, 256 ] }, smooth: 0.1 } );

   /* ---- file source key ---------------------------------------------- */
   const filePath = "/data/M42/light_L.fits";
   const fileSize = 1234567;
   out.file_key_path = filePath;
   out.file_key_size = fileSize;
   out.file_key_cases = [ 1700000000123, 0, 1, 1700000000999, -1500, 1234 ].map( ms => ( {
      ms: ms,
      iso: new Date( ms ).toISOString(),
      key: Cache.hash( "file|" + filePath + "|" + fileSize + "|" + new Date( ms ).toISOString() )
   } ) );

   /* ---- stage tables ------------------------------------------------- */
   out.stage_order = Pipeline.STAGE_ORDER;
   out.stage_companions = Pipeline.STAGE_COMPANIONS;

   /* ---- buildStageKeys ----------------------------------------------- */
   const sourceL = Cache.hash( "source|L" );
   out.build_broadband = Pipeline.buildStageKeys( sourceL, {
      solve: { scale: 1.2, catalog: "gaia" },
      mgc: { strength: 0.5 },
      graxpert: { smoothing: 0.1, model: "bge" }
   } );
   out.build_rgb_tail_keep = Pipeline.buildStageKeys( "rgbsource", {
      extractRGB: { x: 1 },
      stretchRGB: { keepLinear: true, s: 1.5 },
      denoiseRGB: { d: 2 }
   } );
   out.build_rgb_tail_drop = Pipeline.buildStageKeys( "rgbsource", {
      extractRGB: { x: 1 },
      stretchRGB: { keepLinear: false, s: 1.5 },
      denoiseRGB: { d: 2 }
   } );
   out.build_ignores_unknown = Pipeline.buildStageKeys( "abc", {
      solve: { a: 1 }, bogus: { z: 9 }, mgc: {}
   } );
   out.build_null_params = Pipeline.buildStageKeys( "abc", null );
   out.build_null_stage_params = Pipeline.buildStageKeys( "abc", { solve: null, mgc: {} } );

   /* ---- crop, halos, white balance ----------------------------------- */
   const common = { x0: 10, y0: 20, x1: 4000, y1: 3000 };
   out.crop_tag = Pipeline.cropTag( common );
   out.crop_tag_float = Pipeline.cropTag( { x0: 12.0, y0: 0.5, x1: 1e21, y1: -0 } );
   out.crop_halos_true = Pipeline.cropHalosTag( common, { reduceHalos: true } );
   out.crop_halos_false = Pipeline.cropHalosTag( common, { reduceHalos: false } );
   out.crop_halos_unset = Pipeline.cropHalosTag( common, {} );

   const chansKeys = { R: { cleanKey: "kR" }, G: { cleanKey: "kG" }, B: { cleanKey: "kB" } };
   out.clean_wb_no_filters = Pipeline.cleanWhiteBalanceKey( chansKeys, { filters: {} }, common, "ZWO ASI2600MM Pro", "refK" );
   out.clean_wb_named_filters = Pipeline.cleanWhiteBalanceKey( chansKeys, { filters: { R: "Ha", L: "Lum" } }, common, null, "refK" );
   out.clean_wb_unset_filters = Pipeline.cleanWhiteBalanceKey( chansKeys, {}, common, null, "refK" );
   out.clean_wb_other_reference = Pipeline.cleanWhiteBalanceKey( chansKeys, { filters: {} }, common, "ZWO ASI2600MM Pro", "refL" );
   out.clean_wb_null_reference = Pipeline.cleanWhiteBalanceKey( chansKeys, { filters: {} }, common, "ZWO ASI2600MM Pro", null );
   const oscSensor = { R: "Sony Color Sensor R", G: "Sony Color Sensor G", B: "Sony Color Sensor B" };
   out.clean_wb_osc = Pipeline.cleanWhiteBalanceKey( chansKeys, { paths: { OSC: "/m/rgb.xisf" }, oscSensor: oscSensor },
                                                     common, null, "refK" );
   out.clean_wb_osc_other_reference = Pipeline.cleanWhiteBalanceKey( chansKeys, { paths: { OSC: "/m/rgb.xisf" },
                                                                    oscSensor: oscSensor }, common, null, "refL" );
   out.clean_wb_osc_no_sensor = Pipeline.cleanWhiteBalanceKey( chansKeys, { paths: { OSC: "/m/rgb.xisf" } }, common, null, "refK" );
   try
   {
      Pipeline.cleanWhiteBalanceKey( { R: { cleanKey: "kR" }, G: { cleanKey: "" }, B: { cleanKey: "kB" } },
                                     { filters: {} }, common, null, "refK" );
      out.clean_wb_missing_error = null;
   }
   catch ( e ) { out.clean_wb_missing_error = String( e.message ); }

   /* ---- derived source keys (the same formulas as the call sites) ---- */
   const cfgHalos = { reduceHalos: true };
   out.palette_source_key = Cache.hash( "palette|SHO|" + "k1" + "|" + "k2" + "|" + "k3" +
                                        Pipeline.cropHalosTag( common, cfgHalos ) );
   out.palette_source_key_hoo = Cache.hash( "palette|HOO|" + "k1" + "|" + "k2" + "|" + "k3" +
                                            Pipeline.cropHalosTag( common, { reduceHalos: false } ) );
   out.rgb_source_key = Cache.hash( "rgb|" + "rk" + "|" + "gk" + "|" + "bk" +
                                    Pipeline.cropHalosTag( common, cfgHalos ) );
   out.lstars_source_key = Cache.hash( "lstars|" + "lk" + Pipeline.cropHalosTag( common, { reduceHalos: false } ) );

   /* ---- resolveCacheHit over every combination of entry states -------- */
   /*
    * Per entry: miss (no entry), ok (entry, loads, companion present and loads),
    * unloadable (entry does not load), vanished (companion listed but will not
    * load), halfwritten (entry present, companion absent).
    */
   const STATES = [ "miss", "ok", "unloadable", "vanished", "halfwritten" ];
   const resolveChain = ( chain, states ) =>
   {
      const byKey = {};
      chain.forEach( ( e, i ) => { byKey[e.key] = states[i]; } );
      const saved = { lookup: Cache.lookup, load: Cache.load,
                      lookupCompanion: Cache.lookupCompanion, loadCompanion: Cache.loadCompanion,
                      warn: Util.warn };
      /* The shim has no console.warningln; a warning is only a log line here, so it is captured. */
      Util.warn = () => {};
      try
      {
         Cache.lookup = key => ( byKey[key] !== undefined && byKey[key] !== "miss" ) ? "/cache/" + key + ".xisf" : null;
         Cache.lookupCompanion = ( key, name ) =>
            ( [ "ok", "unloadable", "vanished" ].includes( byKey[key] ) ) ? "/cache/" + key + "." + name + ".xisf" : null;
         Cache.load = key =>
            ( [ "ok", "vanished", "halfwritten" ].includes( byKey[key] ) ) ? { key: key, forceClose() {} } : null;
         Cache.loadCompanion = ( key, name ) =>
            ( byKey[key] === "ok" ) ? { key: key, name: name, forceClose() {} } : null;
         const lookups = chain.map( e => byKey[e.key] !== "miss" );
         const r = Pipeline.resolveCacheHit( { key: "R" }, chain, lookups );
         return r.index;
      }
      finally
      {
         Cache.lookup = saved.lookup;
         Cache.load = saved.load;
         Cache.lookupCompanion = saved.lookupCompanion;
         Cache.loadCompanion = saved.loadCompanion;
         Util.warn = saved.warn;
      }
   };

   const enumerate = ( n ) =>
   {
      let combos = [ [] ];
      for ( let i = 0; i < n; ++i )
      {
         const next = [];
         for ( const c of combos ) for ( const s of STATES ) next.push( c.concat( [ s ] ) );
         combos = next;
      }
      return combos;
   };

   const chainA = Pipeline.buildStageKeys( Cache.hash( "source|A" ), {
      solve: { a: 1 }, mgc: { b: 2 }, graxpert: { c: "x" }
   } );
   const chainB = Pipeline.buildStageKeys( Cache.hash( "source|B" ), {
      solve: {}, stretchRGB: { keepLinear: true }, extractL: {}
   } );
   out.resolve_chain_a_keys = chainA.map( e => e.key );
   out.resolve_chain_b_keys = chainB.map( e => e.key );
   out.resolve_chain_b_companions = chainB.map( e => e.companion === undefined ? null : e.companion );
   out.resolve_states = STATES;
   out.resolve_chain_a = {};
   for ( const combo of enumerate( chainA.length ) )
      out.resolve_chain_a[ combo.join( "," ) ] = resolveChain( chainA, combo );
   out.resolve_chain_b = {};
   for ( const combo of enumerate( chainB.length ) )
      out.resolve_chain_b[ combo.join( "," ) ] = resolveChain( chainB, combo );

   return out;
};
