#engine v8

#feature-id    Loom : Batch Processing > Loom
#feature-info  The mechanical first steps, from integrated masters to plates \
   ready for creative work: solve, flux-calibrate, remove gradients, correct \
   aberration, register to L, crop, combine and colour-calibrate RGB and any \
   narrowband palette. Optionally sharpens, reduces noise, splits stars from \
   starless, stretches, and exports 16-bit TIFFs. Makes no artistic choices: \
   every value is measured from the data or fixed by a published convention. \
   Results are left as open windows; nothing is written unless you ask for \
   an export.

#include <pjsr/DataType.jsh>
#include <pjsr/StdButton.jsh>
#include <pjsr/StdIcon.jsh>

#include "lib/Util.js"
#include "lib/Cache.js"
#include "lib/Psb.js"
#include "lib/Steps.js"
#include "lib/Pipeline.js"
#include "lib/Update.js"
#include "lib/UI.js"

#define SETTINGS_KEY "Loom/"

// Update test marker, 2026-09-18. Harmless; remove whenever.
#define UPDATE_TEST_MARKER "2026-09-18"

function defaultConfig()
{
   return {
      paths: { L: "", R: "", G: "", B: "", H: "", S: "", O: "" },
      views: {},
      savedList: "",
      filters: {},
      useGraXpert: true,
      smoothing: 0.5,
      validateOnly: false,
      keepWindowsOnError: false,
      palettes: [],
      // nm. Filter-dependent, so it must be settable: SPCC's own default
      // is 3.0, Baader narrowband is commonly 3.5 or 6.5.
      narrowbandBandwidth: 3.0,
      // Default ON: this ran unconditionally before there was a switch, and
      // an upgrade must not silently change what a repeat run produces.
      narrowbandNormalize: true,
      reduceHalos: false,
      sharpenTool: "none",
      stretch: false,
      // Loom's own deterministic MTF stretch; MultiscaleAdaptiveStretch
      // is the alternative -- see Steps.STRETCH_METHOD_MAS.
      stretchMethod: Steps.STRETCH_METHOD_MTF,
      keepLinear: false,
      // Frequency-separate the L stars plate into _low/_high on export.
      separateLStars: false,
      // One layered .psb beside the TIFFs -- see Steps.buildPsbDocument.
      exportPsb: false,
      // Names the PSB. Defaulted in the dialog from the masters' folder,
      // since PJSR exposes nothing about the open PixInsight project.
      projectName: "",
      exportDir: "",
      marsPath: "",
      starTool: "none",
      noiseTool: "none",
      noiseLevel: "medium",
      starReduction: "none",
      detailLevel: "none",
      // Keep the installed copy current. See lib/Update.js: the update is
      // spawned detached and takes effect on the NEXT launch, so this can
      // never delay or block startup.
      autoUpdate: true,
      useCache: true,
      // Empty means the system temp dir -- see Cache.dir().
      cacheDir: "",
      // Not persisted -- "for this run" is exactly what it means; it must
      // not silently stay on across sessions.
      ignoreCache: false
   };
}

/*
 * Restores from a saved process instance when launched from one, and
 * from Settings otherwise. This is what makes the script draggable to
 * the workspace as a reusable icon.
 */
function loadConfig()
{
   var config = defaultConfig();

   for ( var i = 0; i < Util.CHANNELS.length; ++i )
   {
      var key = Util.CHANNELS[i];
      if ( Parameters.has( "path_" + key ) )
         config.paths[key] = Parameters.getString( "path_" + key );
      // Views are not saveable process-instance state -- an open view id
      // from a previous session/run has no guaranteed meaning now, so
      // only file paths round-trip through Parameters.
   }
   if ( Parameters.has( "savedList" ) )
      config.savedList = Parameters.getString( "savedList" );
   if ( Parameters.has( "smoothing" ) )
      config.smoothing = Parameters.getReal( "smoothing" );
   if ( Parameters.has( "useGraXpert" ) )
      config.useGraXpert = Parameters.getBoolean( "useGraXpert" );
   if ( Parameters.has( "useCache" ) )
      config.useCache = Parameters.getBoolean( "useCache" );
   if ( Parameters.has( "autoUpdate" ) )
      config.autoUpdate = Parameters.getBoolean( "autoUpdate" );

   if ( config.savedList.length == 0 )
   {
      var savedList = Settings.read( SETTINGS_KEY + "savedList", DataType_String );
      if ( savedList != null )
         config.savedList = savedList;
   }

   // Remembered filter choices. A FITS FILTER of L/R/G/B names the
   // channel, not the physical filter, so these are the only record of
   // which filter each channel was actually shot through.
   config.filters = {};
   var fkeys = [ "L", "R", "G", "B" ];
   for ( var fi = 0; fi < fkeys.length; ++fi )
   {
      var fv = Settings.read( SETTINGS_KEY + "filter_" + fkeys[fi], DataType_String );
      if ( fv != null && fv.length > 0 )
         config.filters[fkeys[fi]] = fv;
   }

   var pal = Settings.read( SETTINGS_KEY + "palettes", DataType_String );
   if ( pal != null && pal.length > 0 )
      config.palettes = pal.split( "," ).filter( function( x ) { return x.length > 0; } );

   var nbn = Settings.read( SETTINGS_KEY + "narrowbandNormalize", DataType_Boolean );
   if ( nbn != null )
      config.narrowbandNormalize = nbn;

   var nbw = Settings.read( SETTINGS_KEY + "narrowbandBandwidth", DataType_Double );
   if ( nbw != null && nbw > 0 )
      config.narrowbandBandwidth = nbw;

   var rh = Settings.read( SETTINGS_KEY + "reduceHalos", DataType_Boolean );
   if ( rh != null )
      config.reduceHalos = rh;

   var pn = Settings.read( SETTINGS_KEY + "projectName", DataType_String );
   if ( pn != null )
      config.projectName = pn;

   var epsb = Settings.read( SETTINGS_KEY + "exportPsb", DataType_Boolean );
   if ( epsb != null )
      config.exportPsb = epsb;

   var sls = Settings.read( SETTINGS_KEY + "separateLStars", DataType_Boolean );
   if ( sls != null )
      config.separateLStars = sls;

   var kl = Settings.read( SETTINGS_KEY + "keepLinear", DataType_Boolean );
   if ( kl != null )
      config.keepLinear = kl;

   var exd = Settings.read( SETTINGS_KEY + "exportDir", DataType_String );
   if ( exd != null )
      config.exportDir = exd;

   var sm2 = Settings.read( SETTINGS_KEY + "stretchMethod", DataType_String );
   if ( sm2 != null && sm2.length > 0 )
      config.stretchMethod = sm2;

   var strv = Settings.read( SETTINGS_KEY + "stretch", DataType_Boolean );
   if ( strv != null )
      config.stretch = strv;

   var mp = Settings.read( SETTINGS_KEY + "marsPath", DataType_String );
   if ( mp != null )
      config.marsPath = mp;

   var stl = Settings.read( SETTINGS_KEY + "starTool", DataType_String );
   if ( stl != null && stl.length > 0 )
      config.starTool = stl;

   var nt = Settings.read( SETTINGS_KEY + "noiseTool", DataType_String );
   if ( nt != null && nt.length > 0 )
      config.noiseTool = nt;

   var nl = Settings.read( SETTINGS_KEY + "noiseLevel", DataType_String );
   if ( nl != null && nl.length > 0 )
      config.noiseLevel = nl;

   var st = Settings.read( SETTINGS_KEY + "sharpenTool", DataType_String );
   if ( st != null && st.length > 0 )
      config.sharpenTool = st;
   var sr = Settings.read( SETTINGS_KEY + "starReduction", DataType_String );
   if ( sr != null && sr.length > 0 )
      config.starReduction = sr;
   var dl = Settings.read( SETTINGS_KEY + "detailLevel", DataType_String );
   if ( dl != null && dl.length > 0 )
      config.detailLevel = dl;

   var gx = Settings.read( SETTINGS_KEY + "useGraXpert", DataType_Boolean );
   if ( gx != null )
      config.useGraXpert = gx;

   var au = Settings.read( SETTINGS_KEY + "autoUpdate", DataType_Boolean );
   if ( au != null )
      config.autoUpdate = au;
   var uc = Settings.read( SETTINGS_KEY + "useCache", DataType_Boolean );
   if ( uc != null )
      config.useCache = uc;

   var sm = Settings.read( SETTINGS_KEY + "smoothing", DataType_Double );
   if ( sm != null )
      config.smoothing = sm;

   var cd = Settings.read( SETTINGS_KEY + "cacheDir", DataType_String );
   if ( cd != null )
      config.cacheDir = cd;

   /*
    * Point the cache at the restored folder before anything reads it --
    * the dialog shows the cache's size and entry count as soon as it is
    * constructed, and that readout has to describe the folder in use.
    */
   Cache.setDir( config.cacheDir );

   /*
    * A chosen cache folder that is not there disables the cache for this
    * launch, rather than being created.
    *
    * The folder lives on an external volume, and an unmounted volume is
    * indistinguishable from a missing folder. Creating it would put a
    * decoy cache on the boot disk, fill it with tens of gigabytes, and
    * leave the real one -- with all its entries -- ignored the next time
    * the drive appeared. Running uncached is slower; that is the cheaper
    * mistake by a wide margin.
    *
    * Not persisted: the setting still names the folder, so plugging the
    * drive back in and relaunching restores the cache with no clicking.
    */
   Cache.disableIfDirMissing( config );

   return config;
}

function saveConfig( config )
{
   for ( var i = 0; i < Util.CHANNELS.length; ++i )
   {
      var key = Util.CHANNELS[i];
      Parameters.set( "path_" + key, config.paths[key] );
   }
   Parameters.set( "savedList", config.savedList || "" );
   Parameters.set( "smoothing", config.smoothing );
   Parameters.set( "useGraXpert", config.useGraXpert );
   Parameters.set( "useCache", config.useCache );
   Parameters.set( "autoUpdate", config.autoUpdate );

   Settings.write( SETTINGS_KEY + "useGraXpert", DataType_Boolean, config.useGraXpert );
   Settings.write( SETTINGS_KEY + "narrowbandBandwidth", DataType_Double,
                   config.narrowbandBandwidth || 3.0 );
   Settings.write( SETTINGS_KEY + "narrowbandNormalize", DataType_Boolean,
                   !!config.narrowbandNormalize );
   Settings.write( SETTINGS_KEY + "reduceHalos", DataType_Boolean, !!config.reduceHalos );
   Settings.write( SETTINGS_KEY + "palettes", DataType_String,
                   ( config.palettes || [] ).join( "," ) );
   Settings.write( SETTINGS_KEY + "keepLinear", DataType_Boolean, !!config.keepLinear );
   Settings.write( SETTINGS_KEY + "separateLStars", DataType_Boolean,
                   !!config.separateLStars );
   Settings.write( SETTINGS_KEY + "exportPsb", DataType_Boolean, !!config.exportPsb );
   Settings.write( SETTINGS_KEY + "projectName", DataType_String,
                   config.projectName || "" );
   Settings.write( SETTINGS_KEY + "exportDir", DataType_String, config.exportDir || "" );
   Settings.write( SETTINGS_KEY + "stretch", DataType_Boolean, !!config.stretch );
   Settings.write( SETTINGS_KEY + "stretchMethod", DataType_String,
                   config.stretchMethod || Steps.STRETCH_METHOD_MTF );
   Settings.write( SETTINGS_KEY + "marsPath", DataType_String, config.marsPath || "" );
   Settings.write( SETTINGS_KEY + "starTool", DataType_String, config.starTool || "none" );
   Settings.write( SETTINGS_KEY + "noiseTool", DataType_String, config.noiseTool || "none" );
   Settings.write( SETTINGS_KEY + "noiseLevel", DataType_String, config.noiseLevel || "medium" );
   Settings.write( SETTINGS_KEY + "sharpenTool", DataType_String, config.sharpenTool || "none" );
   Settings.write( SETTINGS_KEY + "starReduction", DataType_String, config.starReduction || "none" );
   Settings.write( SETTINGS_KEY + "detailLevel", DataType_String, config.detailLevel || "none" );
   Settings.write( SETTINGS_KEY + "useCache", DataType_Boolean, config.useCache );
   Settings.write( SETTINGS_KEY + "autoUpdate", DataType_Boolean, config.autoUpdate );
   Settings.write( SETTINGS_KEY + "cacheDir", DataType_String, config.cacheDir || "" );
   Settings.write( SETTINGS_KEY + "smoothing", DataType_Double, config.smoothing );
   Settings.write( SETTINGS_KEY + "savedList", DataType_String, config.savedList || "" );
   var fk = [ "L", "R", "G", "B" ];
   for ( var i = 0; i < fk.length; ++i )
      Settings.write( SETTINGS_KEY + "filter_" + fk[i], DataType_String,
                      ( config.filters && config.filters[fk[i]] ) ? config.filters[fk[i]] : "" );
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

function main()
{
   console.show();

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
   /*
    * Any record left by an older asynchronous check, then the check for
    * this launch -- which BLOCKS, because "the result is reported at the
    * next launch" is not an answer to "is there a new version?".
    *
    * An update that lands cannot apply to this run: #include is resolved
    * when the script is parsed, and that has already happened. So Loom
    * restarts itself instead of continuing on code it has just
    * superseded, and the dialog opens in the relaunched copy.
    */
   Update.reportLast();
   var updated = Update.checkNow( config );
   if ( updated != null && updated.status == "updated" )
   {
      Util.log( "update", "restarting Loom on " + updated.to + "..." );
      if ( Update.relaunch() )
         return;
      // Could not relaunch: carry on rather than leaving the user with
      // nothing. Update.relaunch has already said so.
   }

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

   // Preflight runs unconditionally, whether or not Validate only is
   // ticked -- a validate-only run is exactly "run every preflight check
   // and execute nothing", not a separate, weaker check.
   var problems = Pipeline.preflight( config );
   if ( problems.length > 0 )
   {
      new MessageBox( problems.join( "\n" ),
                      "Loom: preflight failed",
                      StdIcon_Error, StdButton_Ok ).execute();
      return;
   }

   if ( config.validateOnly )
   {
      reportValidateOnly( config );
      return;
   }

   var t = new ElapsedTime;

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
    * Named by start time so runs do not overwrite each other.
    */
   var runLogPath = null;
   try
   {
      var stamp = ( new Date() ).toISOString()
                     .replace( /[:.]/g, "-" ).replace( "T", "_" ).substring( 0, 19 );
      runLogPath = Cache.ensureLogDir() + "/loom-run-" + stamp + ".log";
      console.beginLog( runLogPath );
      Util.log( "log", "console log: " + runLogPath );
   }
   catch ( e )
   {
      runLogPath = null;
      Util.warn( "log", "could not start the console log: " + e );
   }

   /*
    * A Cancel window for the duration of the run. Shown, never executed:
    * execute() would block this script, which is the opposite of what is
    * wanted. Pipeline.checkAbort pumps events so the button responds.
    */
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

   var results;
   /*
    * The log is closed in a finally that wraps everything after it is
    * opened, so a run that throws still leaves a complete file -- which is
    * precisely the run whose log anyone wants to read.
    */
   try
   {
   try { results = Pipeline.run( config ); }
   finally
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
   Util.log( "done", "Finished in " + t.text +
             ( function()
               {
                  var made = [];
                  for ( var k in results )
                     if ( results[k] && results[k].mainView )
                        try { made.push( results[k].mainView.id ); }
                        catch ( e ) {}
                  made.sort();
                  return made.length ? ". Produced: " + made.join( ", " ) + "."
                                     : ". Nothing produced.";
               } )() );
   }
   finally
   {
      if ( runLogPath != null )
      {
         try
         {
            console.endLog();
            console.writeln( "<end><cbr>[log] run log written to " + runLogPath );
         }
         catch ( e ) { /* nothing useful to say once the log itself failed */ }
      }
   }
}

main();
