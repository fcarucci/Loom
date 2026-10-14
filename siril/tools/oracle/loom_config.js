/*
 * Recorder for the options module: Config.defaults(), the Config.FIELDS
 * table, and Steps.migrateConfig over hand-written inputs that cover every
 * branch of the function. Loom's JavaScript is read-only; nothing here
 * changes it.
 */
"use strict";

const TYPE_NAMES = { 1: "bool", 2: "string", 3: "double" };

function copy( v ) { return JSON.parse( JSON.stringify( v ) ); }

module.exports = function( js )
{
   const { Config, Steps } = js;

   const fields = {};
   for ( const name of Object.keys( Config.FIELDS ) )
   {
      const f = Config.FIELDS[name];
      fields[name] = { type: TYPE_NAMES[f.type], load: f.load, save: f.save };
   }

   const base = over => Object.assign( {
      paths: { L: "", R: "", G: "", B: "", H: "", S: "", O: "" },
      savedList: "",
      filters: {},
      gradientTool: "GraXpert",
      noiseTool: "none",
      noiseLevel: "medium",
      noiseLevelL: "",
      sharpenTool: "none",
      starReduction: "none",
      detailLevel: "none",
      stretch: false
   }, over );

   // [name, config, prism2Offered]: true / false / undefined (Studio not found)
   const inputs = [
      [ "gradient_empty_graxpert_true", base( { gradientTool: "", useGraXpert: true } ), undefined ],
      [ "gradient_empty_graxpert_false", base( { gradientTool: "", useGraXpert: false } ), undefined ],
      [ "gradient_empty_no_flag", base( { gradientTool: "" } ), undefined ],
      [ "gradient_studio_kept", base( { gradientTool: "SyQon Studio Deep Gradient", useGraXpert: true } ), true ],
      [ "gradient_none_kept", base( { gradientTool: "none", useGraXpert: true } ), undefined ],
      [ "studio_old_name_prism2_true", base( { noiseTool: "SyQon Studio Prism", noiseLevel: "high", stretch: true } ), true ],
      [ "studio_old_name_prism2_false", base( { noiseTool: "SyQon Studio Prism", noiseLevel: "high" } ), false ],
      [ "studio_essential_prism2_true", base( { noiseTool: "SyQon Studio Prism Essential", noiseLevel: "low", stretch: true } ), true ],
      [ "studio_essential_prism2_false", base( { noiseTool: "SyQon Studio Prism Essential", noiseLevel: "low" } ), false ],
      [ "studio2_prism2_false", base( { noiseTool: "SyQon Studio Prism 2.0", noiseLevel: "low" } ), false ],
      [ "studio2_prism2_undefined", base( { noiseTool: "SyQon Studio Prism 2.0", noiseLevel: "low", stretch: true } ), undefined ],
      [ "studio2_stretch_on_high", base( { noiseTool: "SyQon Studio Prism 2.0", noiseLevel: "high", stretch: true } ), true ],
      [ "studio2_stretch_off_high", base( { noiseTool: "SyQon Studio Prism 2.0", noiseLevel: "high", stretch: false } ), true ],
      [ "studio2_stretch_off_medium", base( { noiseTool: "SyQon Studio Prism 2.0", noiseLevel: "medium", stretch: false } ), true ],
      [ "studio2_stretch_absent_high", base( { noiseTool: "SyQon Studio Prism 2.0", noiseLevel: "high", stretch: undefined } ), true ],
      [ "studio2_stretch_on_low", base( { noiseTool: "SyQon Studio Prism 2.0", noiseLevel: "low", stretch: true } ), true ],
      [ "studio2_unknown_level", base( { noiseTool: "SyQon Studio Prism 2.0", noiseLevel: "extreme", stretch: true } ), true ],
      [ "studio2_level_none", base( { noiseTool: "SyQon Studio Prism 2.0", noiseLevel: "none", stretch: true } ), true ],
      [ "nxt_low_kept", base( { noiseTool: "NoiseXTerminator", noiseLevel: "low" } ), true ],
      [ "prism_high_kept", base( { noiseTool: "SyQon Prism", noiseLevel: "high" } ), false ],
      [ "none_tool_low_kept", base( { noiseTool: "none", noiseLevel: "low" } ), undefined ],
      [ "noiseLevelL_low_studio2_stretch_off", base( { noiseTool: "SyQon Studio Prism 2.0", noiseLevel: "medium", noiseLevelL: "low", stretch: false } ), true ],
      [ "noiseLevelL_empty_kept", base( { noiseTool: "SyQon Studio Prism 2.0", noiseLevel: "medium", noiseLevelL: "", stretch: true } ), true ],
      [ "noiseLevelL_high_stretch_on", base( { noiseTool: "SyQon Studio Prism 2.0", noiseLevel: "medium", noiseLevelL: "high", stretch: true } ), true ],
      [ "noiseLevelL_absent_nxt", base( { noiseTool: "NoiseXTerminator", noiseLevel: "low", noiseLevelL: undefined } ), true ],
      [ "sharpen_correct_old", base( { sharpenTool: "SyQon Studio Parallax (correct only)", starReduction: "medium", detailLevel: "high" } ), true ],
      [ "sharpen_syqon_kept", base( { sharpenTool: "SyQon Parallax", starReduction: "high", detailLevel: "low" } ), true ],
      [ "sharpen_studio_kept", base( { sharpenTool: "SyQon Studio Parallax", starReduction: "high", detailLevel: "low" } ), true ]
   ];

   const cases = { defaults: Config.defaults(), fields: fields };
   for ( const [ name, config, prism2 ] of inputs )
   {
      const input = copy( config );
      const out = Steps.migrateConfig( copy( config ), prism2 );
      cases[ "migrate_" + name ] = {
         input: input,
         prism2Offered: ( prism2 === undefined ) ? null : prism2,
         output: copy( out )
      };
   }

   return cases;
};
