/*
 * Recorder for the mgc slot of the Siril port (lib/Steps.js imageSignature and graxpertProblem, lib/Pipeline.js
 * buildStageKeys). Feeds tests/oracle/loom_mgc_slot.json.
 *
 * Images are plain fakes with chosen statistics: imageSignature touches nothing else of the image.
 */
"use strict";

module.exports = function( js )
{
   const { Steps, Pipeline } = js;

   const out = {};

   /* ---- image signature: mean | median | maximum, as JavaScript number strings --------------- */
   const fake = ( mean, median, maximum ) => ( {
      mean: () => mean,
      median: () => median,
      maximum: () => maximum
   } );
   const stats = [
      [ 1, 1, 1 ],
      [ 0, 0, 0 ],
      [ 0.1 + 0.2, 0.5, 1e21 ],
      [ 1e-7, 2.5e-5, 0.99 ],
      [ -0.25, 0, 1e-7 ],
      [ 123456789.125, 0.1, 3 ],
      [ 0.625, 0.625, 1 ]
   ];
   out.signature_stats = stats;
   out.signature_text = stats.map( s => Steps.imageSignature( fake( s[0], s[1], s[2] ) ) );

   /* ---- the silent-failure guard of the external tool ------------------------------------------ */
   const problems = [
      [ "M42", false, "1|2|3", "1|2|3" ],
      [ "L", true, "1|2|3", "1|2|3" ],
      [ "G", true, "1|2|3", "1|2|4" ],
      [ "Ha stars", true, "0.1|0.2|0.3", "0.1|0.2|0.3" ]
   ];
   out.problem_inputs = problems;
   out.problem_text = problems.map( p => Steps.graxpertProblem( p[0], p[1], p[2], p[3] ) );

   /* ---- stage keys of a broadband chain with the mgc parameters of Pipeline.broadbandStages ---- */
   const chain = ( mgc ) => ( {
      solve: {},
      spfc: { channel: "L", filter: "L", qe: null },
      mgc: mgc,
      graxpert: { enabled: false, smoothing: 0.5 }
   } );
   const withEmpty = chain( { marsFiles: [] } );
   const withOne = chain( { marsFiles: [ "a.xmars" ] } );
   out.key_source = "srckey";
   out.key_params_empty = withEmpty;
   out.key_params_one = withOne;
   const keyed = ( params ) => Pipeline.buildStageKeys( "srckey", params ).map( e => ( { stage: e.stage, key: e.key } ) );
   out.keys_empty = keyed( withEmpty );
   out.keys_one = keyed( withOne );

   return out;
};
