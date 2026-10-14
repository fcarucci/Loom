/*
 * Recorder for the decision tables and parameter builders of the noise,
 * sharpen, star, stretch, gradient and finishing stages (Loom's
 * lib/Steps.js, lib/StepsSyqon.js and lib/Pipeline.js), read by
 * loomsiril/loom/levels.py.
 *
 * Every value is Loom's own. Nothing here is edited or re-derived. The
 * only deliberate omissions are the MLDenoise tool, which the port drops
 * (its noise entry is recorded here and removed on the Python side), and
 * the Siril denoise tool, which Loom does not have.
 *
 * None of the functions recorded below reads a global that touches disk
 * (Steps.studioAvailable, Steps.studioPrism2Unavailable, Steps.moduleAvailable
 * are called only by the availability helpers, which are not recorded), so no
 * fake is installed. Steps.PRISM2_LINEAR_PASS is read at load time as false.
 */
"use strict";

module.exports = function( js )
{
   const { Config, Pipeline, Steps } = js;
   const out = {};

   const TOOLS = [ Steps.NR_TOOL_NXT, Steps.NR_TOOL_PRISM, Steps.NR_TOOL_STUDIO, Steps.NR_TOOL_STUDIO2,
                   "none", "toString", "Frobnicator" ];
   const LEVELS = [ "low", "medium", "high", "none", null, "extreme" ];
   const PASSES = [ undefined, "linear", "stretched", "all" ];
   const SHARPEN_TOOLS = [ Steps.SHARPEN_TOOL_BXT, Steps.SHARPEN_TOOL_SYQON, Steps.SHARPEN_TOOL_STUDIO,
                           Steps.SHARPEN_TOOL_STUDIO_CORRECT_OLD, "none" ];

   // ---- constants and tables as data
   out["constants"] = {
      NR_TOOL_NXT: Steps.NR_TOOL_NXT, NR_TOOL_PRISM: Steps.NR_TOOL_PRISM, NR_TOOL_STUDIO: Steps.NR_TOOL_STUDIO,
      NR_TOOL_STUDIO2: Steps.NR_TOOL_STUDIO2, NR_TOOL_STUDIO_OLD: Steps.NR_TOOL_STUDIO_OLD,
      SHARPEN_TOOL_BXT: Steps.SHARPEN_TOOL_BXT, SHARPEN_TOOL_SYQON: Steps.SHARPEN_TOOL_SYQON,
      SHARPEN_TOOL_STUDIO: Steps.SHARPEN_TOOL_STUDIO, SHARPEN_TOOL_STUDIO_CORRECT_OLD: Steps.SHARPEN_TOOL_STUDIO_CORRECT_OLD,
      STAR_TOOL_STARNET: Steps.STAR_TOOL_STARNET, STAR_TOOL_SXT: Steps.STAR_TOOL_SXT,
      STAR_TOOL_SYQON: Steps.STAR_TOOL_SYQON, STAR_TOOL_STUDIO: Steps.STAR_TOOL_STUDIO,
      GRADIENT_TOOL_NONE: Steps.GRADIENT_TOOL_NONE, GRADIENT_TOOL_GRAXPERT: Steps.GRADIENT_TOOL_GRAXPERT,
      GRADIENT_TOOL_STUDIO: Steps.GRADIENT_TOOL_STUDIO,
      STRETCH_METHOD_MTF: Steps.STRETCH_METHOD_MTF,
      STRETCH_SKY_TARGET: Steps.STRETCH_SKY_TARGET, STRETCH_STARS_TARGET: Steps.STRETCH_STARS_TARGET,
      STUDIO_PARALLAX_FAMILY: Steps.STUDIO_PARALLAX_FAMILY, STUDIO_PARALLAX_FAMILIES: Steps.STUDIO_PARALLAX_FAMILIES,
      STUDIO_MODEL_DENOISE: Steps.STUDIO_MODEL_DENOISE, STUDIO_MODEL_PARALLAX: Steps.STUDIO_MODEL_PARALLAX,
      STUDIO_MODEL_STARLESS: Steps.STUDIO_MODEL_STARLESS, STUDIO_MODEL_GRADIENT: Steps.STUDIO_MODEL_GRADIENT,
      PRISM2_LINEAR_PASS: Steps.PRISM2_LINEAR_PASS
   };
   out["tables"] = {
      NOISE_LEVELS: Steps.NOISE_LEVELS,
      SHARPEN_LEVELS: Steps.SHARPEN_LEVELS,
      PRISM2_LADDER_LINEAR: Steps.PRISM2_LADDER_LINEAR,
      PRISM2_LADDER_STRETCHED: Steps.PRISM2_LADDER_STRETCHED,
      PRISM2_LEVEL_NAMES: Steps.PRISM2_LEVEL_NAMES,
      RGB_FINISHING: Pipeline.RGB_FINISHING,
      PALETTE_FINISHING: Pipeline.PALETTE_FINISHING,
      L_FINISHING: Pipeline.L_FINISHING
   };

   // ---- tool lists for every availability combination (2^k)
   function combos( keys, fn, prefix )
   {
      for ( let m = 0; m < ( 1 << keys.length ); ++m )
      {
         const found = {};
         keys.forEach( ( k, i ) => { found[k] = !!( m & ( 1 << i ) ); } );
         out[prefix + "/" + m] = { found: found, tools: fn( found ) };
      }
   }
   combos( [ "mldenoise", "nxt", "prism", "studio", "prism2Unavailable" ], Steps.noiseToolsFrom, "noise_tools_from" );
   combos( [ "bxt", "parallax", "studio" ], Steps.sharpenToolsFrom, "sharpen_tools_from" );
   combos( [ "starnet", "sxt", "starless", "studio" ], Steps.starToolsFrom, "star_tools_from" );

   // ---- per tool tables: levels, amounts, linear flag, level names
   const stretchKeys = { "true": true, "false": false };
   TOOLS.forEach( ( tool, ti ) =>
   {
      const entry = { linear: Steps.denoiseIsLinear( tool ), levels: {}, amount: {}, supported: {}, level_name: {} };
      for ( const sk of Object.keys( stretchKeys ) )
         entry.levels[sk] = Steps.noiseLevelsFor( tool, stretchKeys[sk] );
      for ( const lv of LEVELS )
      {
         const key = String( lv );
         entry.amount[key] = PASSES.map( p => Steps.noiseAmountFor( tool, lv, p ) );
         entry.level_name[key] = Steps.noiseLevelName( tool, lv );
         entry.supported[key] = {
            "true": Steps.supportedNoiseLevel( tool, lv, true ),
            "false": Steps.supportedNoiseLevel( tool, lv, false ) };
      }
      out["noise_tool/" + tool] = entry;
   } );
   // the stretch argument of noiseLevelsFor: anything but false is "on"
   out["noise_levels_for/undefined_stretch"] = { tool: Steps.NR_TOOL_STUDIO2,
      levels: Steps.noiseLevelsFor( Steps.NR_TOOL_STUDIO2, undefined ) };

   // ---- sharpen and star amounts, aberration corrector, studio family
   SHARPEN_TOOLS.forEach( tool =>
   {
      const entry = { stars: {}, detail: {}, corrector: {} };
      for ( const lv of LEVELS )
      {
         entry.stars[String( lv )] = Steps.sharpenAmountFor( tool, "stars", lv );
         entry.detail[String( lv )] = Steps.sharpenAmountFor( tool, "detail", lv );
      }
      entry.corrector = { "true": Steps.aberrationCorrector( tool, true ),
                          "false": Steps.aberrationCorrector( tool, false ) };
      out["sharpen_tool/" + tool] = entry;
   } );
   out["studio_family_of"] = {};
   [ "classic", "aesthetics", "bogus" ].forEach( f => { out["studio_family_of"][f] = Steps.studioFamilyOf( f ); } );

   // ---- the selftest goldens ("noiseAmountFor golden", "denoise params golden"), re-recorded
   const goldenTools = [ Steps.NR_TOOL_NXT, Steps.NR_TOOL_PRISM, Steps.NR_TOOL_STUDIO, Steps.NR_TOOL_STUDIO2,
                         "none", "toString", "", "Frobnicator" ];
   out["golden/params"] = {};
   goldenTools.slice( 0, 4 ).forEach( tool =>
   {
      const rows = [];
      [ "low", "medium", "high" ].forEach( level =>
         [ true, false ].forEach( stretch =>
            [ undefined, "L" ].forEach( which =>
            {
               const cfg = { noiseTool: tool, noiseLevel: level, noiseLevelL: "low", stretch: stretch };
               rows.push( [ level, stretch, String( which ),
                            Pipeline.compositeDenoiseParams( cfg, which ),
                            Pipeline.linearDenoiseParams( cfg, which ),
                            Pipeline.stretchedDenoiseParams( cfg, which ) ] );
            } ) ) );
      out["golden/params"][tool] = rows;
   } );

   // ---- the configuration matrix. Each case is one configuration with every builder's output on it.
   const CFG_KEYS = [ "gradientTool", "smoothing", "stretch", "stretchMethod", "keepLinear", "sharpenTool",
                      "starReduction", "detailLevel", "parallaxFamily", "starTool", "noiseTool", "noiseLevel",
                      "noiseLevelL" ];
   const base = Config.defaults();
   function cfg( over )
   {
      const c = {};
      for ( const k of CFG_KEYS ) c[k] = base[k];
      return Object.assign( c, over );
   }
   function bundle( config )
   {
      const b = { config: config };
      b.composite_sharpen = Pipeline.compositeSharpenParams( config );
      b.aberration = { "true": Pipeline.aberrationParams( config, true ),
                       "false": Pipeline.aberrationParams( config, false ) };
      b.studio_family = Pipeline.studioFamily( config );
      b.star_extraction = Pipeline.starExtractionParams( config );
      b.stretch = { linked: Pipeline.stretchParams( config, true ),
                    unlinked: Pipeline.stretchParams( config, false ) };
      b.denoise_level = { colour: Pipeline.denoiseLevelFor( config, undefined ),
                          L: Pipeline.denoiseLevelFor( config, "L" ) };
      b.composite_denoise = { colour: Pipeline.compositeDenoiseParams( config, undefined ),
                              L: Pipeline.compositeDenoiseParams( config, "L" ) };
      b.linear_denoise = { colour: Pipeline.linearDenoiseParams( config, undefined ),
                           L: Pipeline.linearDenoiseParams( config, "L" ) };
      b.stretched_denoise = { colour: Pipeline.stretchedDenoiseParams( config, undefined ),
                              L: Pipeline.stretchedDenoiseParams( config, "L" ) };
      b.linear_denoise_pass = Pipeline.linearDenoisePass( config );
      b.studio_models = { "true": Steps.studioModelsFor( config, true ),
                          "false": Steps.studioModelsFor( config, false ) };
      b.gradient_tool = Steps.gradientToolOf( config );
      b.gradient_stage = Steps.gradientStageParams( config );
      b.finishing = {
         RGB: Pipeline.finishingStages( "RGB", null, Pipeline.RGB_FINISHING, config, null ).params,
         PALETTE: Pipeline.finishingStages( "palette", null, Pipeline.PALETTE_FINISHING, config, null ).params,
         L: Pipeline.finishingStages( "L", null, Pipeline.L_FINISHING, config, null ).params };
      return b;
   }
   const add = ( name, over ) => { out[name] = bundle( cfg( over ) ); };

   // sharpen: tool x starReduction x detailLevel; the Studio tool also by Parallax family
   const sharpenCases = [ [ "none", "none" ], [ Steps.SHARPEN_TOOL_BXT, "bxt" ], [ Steps.SHARPEN_TOOL_SYQON, "syqon" ],
                          [ Steps.SHARPEN_TOOL_STUDIO, "studio/classic" ],
                          [ Steps.SHARPEN_TOOL_STUDIO, "studio/aesthetics" ] ];
   sharpenCases.forEach( ( [ tool, tag ] ) =>
   {
      const family = tag.endsWith( "aesthetics" ) ? "aesthetics" : "classic";
      for ( const s of [ "none", "medium", "high" ] )
         for ( const d of [ "none", "medium", "high" ] )
            add( "sharpen/" + tag + "/stars=" + s + "/detail=" + d,
                 { sharpenTool: tool, starReduction: s, detailLevel: d, parallaxFamily: family } );
   } );

   // stars: starTool x stretch x keepLinear (MTF stretch)
   [ "none", Steps.STAR_TOOL_STARNET, Steps.STAR_TOOL_SXT, Steps.STAR_TOOL_SYQON, Steps.STAR_TOOL_STUDIO ]
      .forEach( tool =>
      {
         for ( const st of [ true, false ] )
            for ( const kl of [ true, false ] )
               add( "star/" + tool + "/stretch=" + st + "/keepLinear=" + kl,
                    { starTool: tool, stretch: st, keepLinear: kl, stretchMethod: Steps.STRETCH_METHOD_MTF } );
      } );

   // noise: noiseTool x noiseLevel x noiseLevelL x stretch
   [ "none", Steps.NR_TOOL_NXT, Steps.NR_TOOL_PRISM, Steps.NR_TOOL_STUDIO, Steps.NR_TOOL_STUDIO2 ]
      .forEach( tool =>
      {
         for ( const lv of [ "low", "medium", "high" ] )
            for ( const lvL of [ "", "high" ] )
               for ( const st of [ true, false ] )
                  add( "noise/" + tool + "/level=" + lv + "/levelL=" + lvL + "/stretch=" + st,
                       { noiseTool: tool, noiseLevel: lv, noiseLevelL: lvL, stretch: st } );
      } );

   // gradient: the dropdown value, and the legacy checkbox, present or absent
   [ Steps.GRADIENT_TOOL_NONE, Steps.GRADIENT_TOOL_GRAXPERT, Steps.GRADIENT_TOOL_STUDIO, "", undefined ]
      .forEach( gt =>
      {
         for ( const legacy of [ true, false, undefined ] )
            add( "gradient/" + String( gt ) + "/useGraXpert=" + String( legacy ),
                 { gradientTool: gt, useGraXpert: legacy } );
      } );

   // ---- Prism 2.0 with its linear Advanced pass on (Steps.setPrism2LinearPass), restored to off in finally
   function withLinearPass( fn )
   {
      Steps.setPrism2LinearPass( true );
      try { return fn(); }
      finally { Steps.setPrism2LinearPass( false ); }
   }
   out["prism2_linear_pass/flag"] = withLinearPass( () => Steps.PRISM2_LINEAR_PASS );
   out["prism2_linear_pass/denoise_is_linear"] = withLinearPass( () => Steps.denoiseIsLinear( Steps.NR_TOOL_STUDIO2 ) );
   out["prism2_linear_pass/levels"] = withLinearPass( () => (
      { "true": Steps.noiseLevelsFor( Steps.NR_TOOL_STUDIO2, true ),
        "false": Steps.noiseLevelsFor( Steps.NR_TOOL_STUDIO2, false ) } ) );
   out["prism2_linear_pass/amount"] = withLinearPass( () =>
   {
      const r = {};
      for ( const lv of LEVELS ) r[String( lv )] = PASSES.map( p => Steps.noiseAmountFor( Steps.NR_TOOL_STUDIO2, lv, p ) );
      return r;
   } );
   out["prism2_linear_pass/supported"] = withLinearPass( () =>
   {
      const r = {};
      for ( const lv of LEVELS )
         r[String( lv )] = { "true": Steps.supportedNoiseLevel( Steps.NR_TOOL_STUDIO2, lv, true ),
                             "false": Steps.supportedNoiseLevel( Steps.NR_TOOL_STUDIO2, lv, false ) };
      return r;
   } );
   // each entry carries the flag it was recorded with, and no top-level `config`
   function addLinear( name, over )
   {
      out["linear_pass/" + name] = { prism2_linear_pass: Steps.PRISM2_LINEAR_PASS, bundle: bundle( cfg( over ) ) };
   }
   withLinearPass( () =>
   {
      for ( const lv of [ "low", "medium", "high", "extreme" ] )
         for ( const st of [ true, false ] )
            addLinear( "noise/level=" + lv + "/stretch=" + st,
                       { noiseTool: Steps.NR_TOOL_STUDIO2, noiseLevel: lv, noiseLevelL: "", stretch: st } );
      addLinear( "noise/levelL=high/level=medium/stretch=true",
                 { noiseTool: Steps.NR_TOOL_STUDIO2, noiseLevel: "medium", noiseLevelL: "high", stretch: true } );
   } );

   // ---- a level outside low, medium and high (extreme), and "none" for the strength and the L strength
   [ Steps.NR_TOOL_NXT, Steps.NR_TOOL_PRISM, Steps.NR_TOOL_STUDIO, Steps.NR_TOOL_STUDIO2 ].forEach( tool =>
   {
      add( "noise/" + tool + "/level=extreme/levelL=extreme/stretch=true",
           { noiseTool: tool, noiseLevel: "extreme", noiseLevelL: "extreme", stretch: true } );
      for ( const st of [ true, false ] )
         add( "noise/" + tool + "/level=none/levelL=none/stretch=" + st,
              { noiseTool: tool, noiseLevel: "none", noiseLevelL: "none", stretch: st } );
   } );

   // ---- a configuration without `stretch` (undefined): Loom's `stretch !== false` reads it as on
   add( "stretch/absent/noise=studio2", { stretch: undefined, noiseTool: Steps.NR_TOOL_STUDIO2, noiseLevel: "medium",
                                          noiseLevelL: "" } );
   add( "stretch/absent/noise=prism", { stretch: undefined, noiseTool: Steps.NR_TOOL_PRISM, noiseLevel: "low",
                                        noiseLevelL: "" } );

   // ---- several Studio tools at once: the order of studioModelsFor
   add( "studio_models/all", { gradientTool: Steps.GRADIENT_TOOL_STUDIO, sharpenTool: Steps.SHARPEN_TOOL_BXT,
                               starTool: Steps.STAR_TOOL_STUDIO, noiseTool: Steps.NR_TOOL_STUDIO, noiseLevel: "high",
                               noiseLevelL: "low", stretch: true } );
   add( "studio_models/all_studio2", { gradientTool: Steps.GRADIENT_TOOL_STUDIO, sharpenTool: Steps.SHARPEN_TOOL_STUDIO,
                                       starTool: Steps.STAR_TOOL_STUDIO, noiseTool: Steps.NR_TOOL_STUDIO2,
                                       noiseLevel: "high", noiseLevelL: "medium", stretch: true } );

   // ---- the legacy gradient checkbox with truthy values that are not true: none, as Loom's === true says
   add( "gradient/legacy/string_true", { gradientTool: "", useGraXpert: "true" } );
   add( "gradient/legacy/one", { gradientTool: "", useGraXpert: 1 } );

   return out;
};
