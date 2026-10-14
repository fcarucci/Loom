/*
 * Recorder for the SyQon Studio adapter (loomsiril/loom/tools/syqon_studio.py): argument building,
 * exit messages, account refusal, probe problems, failure text and the Prism 2.0 flag. Pure functions only.
 */
"use strict";

module.exports = function( js )
{
   const { Steps } = js;
   const out = {};

   const models = [ "prism-essential", "prism-advanced", "prism-ultra", "prism-max", "parallax", "axiom", "deep-gradient" ];
   const domains = [ "linear", "nonlinear" ];
   const settingsSets = {
      plain:       { application: 1.0 },
      blend:       { application: 0.5 },
      correct:     { application: 1.0, parallax: { correction: true } },
      stages:      { application: 1.0, parallax: { correction: false, reduction: 3, deblur: 0.5, family: "aesthetics" } },
      fractional:  { application: 0.8125, parallax: { correction: true, reduction: 2.5, deblur: 0.25, family: "classic" } },
      badFamily:   { application: 1.0, parallax: { correction: true, reduction: 0, deblur: 0, family: "bogus" } }
   };

   for ( const model of models )
      for ( const domain of domains )
         for ( const name of Object.keys( settingsSets ) )
         {
            const s = settingsSets[name];
            const args = Steps.studioBuildArgs( { model: model, domain: domain, application: s.application,
                                                  parallax: s.parallax, input: "IN", output: "OUT" } );
            out[ "args_" + model + "_" + domain + "_" + name ] = args;
         }

   const codes = [ 0, 1, 2, 3, 4, 5, 6, 7, 9, 130 ];
   for ( const c of codes )
      out[ "exit_" + c ] = Steps.studioExitMessage( c );

   const refusals = {
      code4:        { exitCode: 4, stderr: "" },
      licence:      { exitCode: 5, stderr: "Secure license check failed for axiom" },
      session:      { exitCode: 0, stderr: "error: no secure SyQon session" },
      upperCase:    { exitCode: 1, stderr: "NO SECURE SYQON SESSION" },
      other:        { exitCode: 5, stderr: "inference failed" },
      nothing:      { exitCode: null, stderr: null }
   };
   for ( const name of Object.keys( refusals ) )
      out[ "refused_" + name ] = Steps.studioAccountRefused( refusals[name] );

   for ( const m of models )
   {
      out[ "role_" + m ] = Steps.studioModelRole( m );
      out[ "label_" + m ] = Steps.studioModelLabel( m );
      out[ "prism2_" + m ] = Steps.studioIsPrism2( m );
      out[ "prism_" + m ] = Steps.studioIsPrism( m );
   }

   const probes = {
      prismCode4:     { model: "prism-advanced", res: { exitCode: 4, stderr: "" } },
      prismStderr:    { model: "prism-max", res: { exitCode: 1, stderr: "secure license check failed" } },
      parallaxCode4:  { model: "parallax", res: { exitCode: 4, stderr: "" } },
      axiomStderr:    { model: "axiom", res: { exitCode: 5, stderr: "no secure SyQon session" } },
      deepNoCode:     { model: "deep-gradient", res: { exitCode: null, stderr: "no secure SyQon session" } },
      ok:             { model: "prism-essential", res: { exitCode: 0, stderr: "" } },
      inference:      { model: "prism-ultra", res: { exitCode: 5, stderr: "boom" } }
   };
   for ( const name of Object.keys( probes ) )
   {
      const p = probes[name];
      out[ "problem_" + name ] = Steps.studioProbeProblem( p.model, p.res );
   }

   const failures = {
      code5Stderr:   { model: "prism-max", res: { exitCode: 5, stderr: "  gpu error \n" } },
      noOutput:      { model: "axiom", res: { exitCode: 0, stderr: "" } },
      code3NoErr:    { model: "parallax", res: { exitCode: 3, stderr: null } },
      code9:         { model: "deep-gradient", res: { exitCode: 9, stderr: "x" } }
   };
   for ( const name of Object.keys( failures ) )
   {
      const f = failures[name];
      out[ "failure_" + name ] = Steps.studioFailureText( f.model, f.res );
   }

   out.flag_empty = Steps.prism2FlagAfterCheck( [] );
   out.flag_advanced_ran = Steps.prism2FlagAfterCheck( [ { model: "prism-advanced", refused: false } ] );
   out.flag_max_refused = Steps.prism2FlagAfterCheck( [ { model: "prism-max", refused: true },
                                                        { model: "prism-ultra", refused: false } ] );
   out.flag_ran_then_refused = Steps.prism2FlagAfterCheck( [ { model: "prism-ultra", refused: false },
                                                              { model: "prism-advanced", refused: true } ] );
   out.flag_parallax_only = Steps.prism2FlagAfterCheck( [ { model: "parallax", refused: true } ] );
   out.family_bogus = Steps.studioFamilyOf( "bogus" );
   out.family_aesthetics = Steps.studioFamilyOf( "aesthetics" );

   return out;
};
