#engine v8

#feature-id    Loom : Loom > Loom
#feature-info  The mechanical first steps, from integrated masters to plates \
   ready for creative work: solve, flux-calibrate, remove gradients, correct \
   aberration, register to L (or the best channel without one), crop, \
   combine and colour-calibrate RGB and any \
   narrowband palette. Optionally sharpens, reduces noise, splits stars from \
   starless, stretches, and exports 16-bit TIFFs. Makes no artistic choices: \
   every value is measured from the data or fixed by a published convention. \
   Results are left as open windows; nothing is written unless you ask for \
   an export.

#include <pjsr/DataType.jsh>
#include <pjsr/StdButton.jsh>
#include <pjsr/StdCursor.jsh>
#include <pjsr/StdIcon.jsh>

/*
 * selftest.js includes this file to reach defaultConfig, loadConfig and
 * saveConfig, and it has already loaded the libraries. The preprocessor
 * does not dedupe an #include, so re-running them here would reset every
 * namespace the suite holds -- the same guard FrameSelector.js carries.
 */
#ifndef LOOM_LIBS_INCLUDED
#include "lib/Util.js"
#include "lib/Cache.js"
#include "lib/Psb.js"
#include "lib/Steps.js"
#include "lib/StepsSyqon.js"
#include "lib/StepsIcc.js"
#include "lib/Config.js"
#include "lib/Pipeline.js"
#include "lib/Update.js"
#include "lib/UI.js"
#endif

/*
 * The configuration lives in lib/Config.js; these names are kept for the
 * callers below and for the suite.
 */
function defaultConfig()
{
   return Config.defaults();
}

function loadConfig()
{
   return Config.load( Config.pixinsightStore() );
}

function saveConfig( config )
{
   Config.save( config, Config.pixinsightStore() );
}

/*
 * Runs every preflight check and executes nothing, regardless of the
 * validateOnly flag's own truth value -- this function is the single
 * validate-only code path and it is only ever reached after preflight
 * has already been run and found clean by main(), so by construction
 * nothing here opens a window or invokes a process.
 */
function reportValidateOnly( config )
{
   Util.log( "validate", "All checks passed. Nothing executed." );
   for ( var i = 0; i < Util.CHANNELS.length; ++i )
   {
      var k = Util.CHANNELS[i];
      if ( config.views[k] )
         Util.log( "validate", k + " <- view " + config.views[k] );
      else if ( config.paths[k] )
         Util.log( "validate", k + " <- " + config.paths[k] );
   }
   Util.log( "validate", "Results stay as open windows" );
}

/*
 * Loom's refusal on a core older than Util.MIN_CORE, checked first thing
 * in main() -- before the dialog, before the updater, before anything
 * touches a process. Loom names itself and says what it needs the
 * version FOR; see Util.checkCoreVersion.
 */
var LOOM_CORE_CHECK = {
   title: "Loom: PixInsight is too old",
   message: function( core )
   {
      return "Loom needs PixInsight " + Util.formatCoreVersion( Util.MIN_CORE ) +
             " or later.\n\n" +
             "This is PixInsight " + Util.formatCoreVersion( core ) +
             " (build " + core.build + ").\n\n" +
             "Loom uses the ImageSolver and AstrometricResiduals sources that " +
             "ship with " + Util.formatCoreVersion( Util.MIN_CORE ) + ", and " +
             "astrometric solutions written by it are not readable by earlier " +
             "versions. Please update PixInsight and run Loom again.";
   }
};

/*
 * The banner, so a run log opens with what it is and which build
 * produced it -- including a run that is about to be refused for the
 * core version, where knowing which Loom refused it is the whole
 * question. console.show() because a previous modal may have slid the
 * console away.
 */
function writeBanner()
{
   console.show();
   for ( var bl = 0; bl < Util.BANNER.length; ++bl )
      console.writeln( "<end><cbr>" + Util.BANNER[bl] );
   console.writeln( "<end><cbr>" +
                    Update.describeVersion( File.extractDirectory( #__FILE__ ) + "/..",
                                            Update.io ) );
   console.writeln( "<end><cbr>" );
}

/*
 * Any record left by an older asynchronous check, then the check for
 * this launch -- which BLOCKS, because "the result is reported at the
 * next launch" is not an answer to "is there a new version?".
 *
 * An update that lands cannot apply to this run: #include is resolved
 * when the script is parsed, and that has already happened. So Loom
 * restarts itself instead of continuing on code it has just
 * superseded, and the dialog opens in the relaunched copy. True when
 * it did, and this run must stop.
 */
function updateAndRelaunch( config )
{
   Update.reportLast();
   var updated = Update.checkNow( config );
   if ( updated == null || updated.status != "updated" )
      return false;
   Util.log( "update", "restarting Loom on " + updated.to + "..." );
   // Could not relaunch: carry on rather than leaving the user with
   // nothing. Update.relaunch has already said so.
   return Update.relaunch();
}

/*
 * Preflight runs unconditionally, whether or not Validate only is
 * ticked -- a validate-only run is exactly "run every preflight check
 * and execute nothing", not a separate, weaker check. False, having
 * said why, when the run must not go ahead.
 */
function preflightPassed( config )
{
   var problems = Pipeline.preflight( config );
   if ( problems.length == 0 )
      return true;
   new MessageBox( problems.join( "\n" ),
                   "Loom: preflight failed",
                   StdIcon_Error, StdButton_Ok ).execute();
   return false;
}

/*
 * Write the Process Console to a file for the duration of the run.
 *
 * PixInsight keeps no console log of its own, so when a run goes wrong
 * the only record is whatever is still scrolled into the dock -- gone
 * the moment it is cleared, and impossible to hand to anyone else. The
 * log lands in a logs/ subfolder of the cache -- a place the user has
 * already chosen and sized, without being counted in the cache's size
 * or deleted by "Clear cache", both of which skip directories.
 *
 * Named by start time so runs do not overwrite each other. Returns the
 * log's path, or null when it could not be started.
 */
function startRunLog()
{
   try
   {
      var stamp = ( new Date() ).toISOString()
                     .replace( /[:.]/g, "-" ).replace( "T", "_" ).substring( 0, 19 );
      var runLogPath = Cache.ensureLogDir() + "/loom-run-" + stamp + ".log";
      console.beginLog( runLogPath );
      Util.log( "log", "console log: " + runLogPath );
      return runLogPath;
   }
   catch ( e )
   {
      Util.warn( "log", "could not start the console log: " + e );
      return null;
   }
}

function endRunLog( runLogPath )
{
   if ( runLogPath == null )
      return;
   try
   {
      console.endLog();
      console.writeln( "<end><cbr>[log] run log written to " + runLogPath );
   }
   catch ( e ) { /* nothing useful to say once the log itself failed */ }
}

/*
 * A Cancel window for the duration of the run. Shown, never executed:
 * execute() would block this script, which is the opposite of what is
 * wanted. Pipeline.checkAbort pumps events so the button responds.
 * Returns the window, or null when it could not be shown.
 */
function showCancelWindow()
{
   var cancelWin = null;
   try
   {
      cancelWin = new UI.CancelWindow;
      cancelWin.show();
      Pipeline.cancelWindow = cancelWin;
      // the hook Steps consults inside its CLI wait loops
      Util.cancelRequested = function() { return cancelWin.cancelled; };
      Util.reportProgress = function( pct, text )
      {
         try
         {
            cancelWin.setProgress( pct, text );
            CoreApplication.processEvents();   // repaint, and keep Cancel live
         }
         catch ( e ) {}
      };
      /*
       * Most of a run reports no percentage: BXT, SXT, NXT and StarNet2 run
       * in-process and their progress goes to the Process Console, where no
       * script can reach it. The operation name is what the window can
       * always show, and it changes often enough to prove the run is alive.
       */
      Util.reportStage = function( text )
      {
         try
         {
            cancelWin.setStage( text );
            CoreApplication.processEvents();
         }
         catch ( e ) {}
      };

      /*
       * Bring the console back and keep the built-in abort live.
       *
       * The Process Console is usually docked in auto-hide, so opening any
       * dialog slides it away -- which is precisely when its output matters
       * most. console.show() at the top of main() happens before the
       * dialogs exist, so it does not survive them.
       */
      console.show();
      console.abortEnabled = true;
   }
   catch ( e )
   {
      Util.warn( "ui", "could not show the Cancel window: " + e );
      Pipeline.cancelWindow = null;
   }
   return cancelWin;
}

/* Undoes showCancelWindow's hooks, and closes the window if there is one. */
function closeCancelWindow( cancelWin )
{
   Pipeline.cancelWindow = null;
   Util.cancelRequested = function() { return false; };
   Util.reportProgress = function() {};
   Util.reportStage = function() {};
   if ( cancelWin != null )
      try { cancelWin.cancel(); } catch ( e ) {}
}

/*
 * Report whatever the pipeline actually returned, rather than probing for
 * a fixed set of names.
 *
 * The old version asked for results.RGB and results[<palette>] by name,
 * and star extraction renames those results to RGB_starless / RGB_stars /
 * <palette>_starless -- so a run that produced a perfectly good composite
 * announced "No RGB produced." Enumerating the results cannot go stale
 * the next time a key changes.
 */
function describeProduced( results )
{
   var made = [];
   for ( var k in results )
      if ( results[k] && results[k].mainView )
         try { made.push( results[k].mainView.id ); }
         catch ( e ) {}
   made.sort();
   return made.length ? ". Produced: " + made.join( ", " ) + "."
                      : ". Nothing produced.";
}

function main()
{
   writeBanner();

   // Before the updater, before the dialog, before anything is opened:
   // an unsupported core must name itself rather than fail obscurely
   // several minutes into a run.
   if ( !Util.checkCoreVersion( LOOM_CORE_CHECK ) )
      return;

   var config = loadConfig();

   /*
    * The updater, before anything else and before the dialog.
    *
    * reportLast() says what the PREVIOUS launch's update did; start()
    * spawns this launch's, detached. Neither waits: #include is resolved
    * at parse time, so an update cannot apply to the script already
    * running, and there is nothing to be gained by waiting for it.
    */
   Update.SCRIPT_DIR = File.extractDirectory( #__FILE__ );
   Update.SCRIPT_FILE = #__FILE__;
   if ( updateAndRelaunch( config ) )
      return;

   // Deliberately NOT defaulted: an empty output folder means "keep the
   // results as open windows in the current project".

   var dialog = new UI.SelectDialog( config );
   if ( !dialog.execute() )
      return;

   // the modal dialog slid an auto-hidden console away; bring it back
   console.show();

   // Persist the selection IMMEDIATELY, before preflight can abort the
   // run. Saving only after a successful preflight meant that any failed
   // run -- the exact case where the user will try again -- threw away
   // the list they had just assembled.
   saveConfig( config );

   if ( !preflightPassed( config ) )
      return;

   if ( config.validateOnly )
   {
      reportValidateOnly( config );
      return;
   }

   var t = new ElapsedTime;
   var runLogPath = startRunLog();
   var cancelWin = showCancelWindow();

   var results;
   /*
    * The log is closed in a finally that wraps everything after it is
    * opened, so a run that throws still leaves a complete file -- which is
    * precisely the run whose log anyone wants to read.
    */
   try
   {
      try { results = Pipeline.run( config ); }
      finally { closeCancelWindow( cancelWin ); }
      Util.log( "done", "Finished in " + t.text + describeProduced( results ) );
   }
   finally { endRunLog( runLogPath ); }
}

/*
 * selftest.js includes this file to test the config functions above, and
 * must not open the dialog while doing it.
 */
#ifndef LOOM_UNDER_TEST
main();
#endif
