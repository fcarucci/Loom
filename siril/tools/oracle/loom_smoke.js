/*
 * Smoke recorder for the Loom main library set: proves the libraries load under
 * node and records a few values the port's first modules depend on.
 */
"use strict";

module.exports = function( js )
{
   const { Util, Cache, Config, Pipeline, Steps } = js;
   return {
      hash_abc: Cache.hash( "abc" ),
      hash_utf8: Cache.hash( "M42 ° Orion" ),
      params_string: Cache.paramsString( { b: 2, a: "x", c: { z: 1, y: [ 1, 2 ] } } ),
      chain_key: Cache.chainKey( "root", "solve", { enabled: true } ),
      stage_order: Pipeline.STAGE_ORDER,
      default_keys: Object.keys( Config.defaults() ).sort(),
      channel_from_filter: [ "L", "Ha", "SII", "OIII", "Red", "green", "Lum", "xyz" ].map( f => [ f, Util.channelFromFilter( f ) ] ),
      mtf_midtone_for: Steps.mtfMidtoneFor( 0.05, 0.25 )
   };
};
