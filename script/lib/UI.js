/*
 * lib/UI.js
 *
 * Loom's selection dialog. One list holds every master; each entry's
 * channel is read from its FITS FILTER keyword rather than its filename,
 * because filenames lie and metadata does not. Entries may be files on
 * disk or already-open views (the owner's masters live as views inside
 * .pxiproject bundles).
 */

#include <pjsr/FrameStyle.jsh>
#include <pjsr/StdButton.jsh>
#include <pjsr/StdIcon.jsh>
#include <pjsr/TextAlign.jsh>

var UI = {};

/*
 * The owner's camera reports INSTRUME = "ZWO ASI2600MM Air", not the bare
 * model string, so this normalizes both sides and checks containment.
 */
UI.CAMERA_MODEL_HINT = "asi2600mm";

/*
 * The one camera line shown under the master list.
 *
 * Says what will actually be used, not merely what the headers contain:
 * a camera nothing recognises resolves to the ideal QE curve, and SPFC
 * calibrating against an idealised response instead of the real one is
 * the failure this line exists to make visible.
 */
UI.cameraSummary = function( instrumes, curveName )
{
   var common = Util.commonInstrument( instrumes );
   var any = false;
   for ( var i = 0; i < instrumes.length; ++i )
      if ( instrumes[i] != null && instrumes[i] !== "" )
         { any = true; break; }

   if ( !any )
      return instrumes.length
         ? "<b>Camera:</b> not named in any header \u2014 calibration will " +
           "use the ideal QE curve"
         : "";
   if ( common == null )
      return "<b>Camera:</b> the masters name more than one camera \u2014 " +
             "check the list, they should all be from one session";
   var line = "<b>Camera:</b> " + common;
   if ( curveName )
      line += " \u2192 " + curveName;
   return line;
};

UI.instrumentMatches = function( instrume )
{
   if ( !instrume )
      return false;
   return instrume.toLowerCase().replace( /[^a-z0-9]/g, "" )
                  .indexOf( UI.CAMERA_MODEL_HINT ) >= 0;
};

/*
 * One Filter column. Where the filter name is not itself the channel
 * letter -- "Baader R" mapping to R -- the channel is appended, so nothing
 * is lost; for plain "R" it would just repeat itself and is omitted.
 */
UI.filterCellText = function( e )
{
   if ( e.unavailable )
      return "(not open)";
   if ( e.filter === null )
      return "(no FILTER)";
   if ( e.channel !== null && String( e.filter ).trim() != e.channel )
      return e.filter + "  (" + e.channel + ")";
   return e.filter;
};

/*
 * The four SubframeSelector figures for the masters list -- FWHM,
 * eccentricity, noise and stars -- each with its change against the
 * previous integration of the same channel. Shown separately rather than
 * combined because their senses differ: smaller FWHM, eccentricity and
 * noise are better, more stars are better.
 */
UI.qualityCells = function( q, dq )
{
   if ( !q )
      return [ "", "", "", "" ];
   function cell( value, delta, text )
   {
      if ( value == null )
         return "";
      var d = Util.formatDelta( delta );
      return text( value ) + ( d ? "  " + d : "" );
   }
   function fixed( digits ) { return function( v ) { return v.toFixed( digits ); }; }
   return [ cell( q.fwhm, dq && dq.fwhm, fixed( 2 ) ),
            cell( q.eccentricity, dq && dq.eccentricity, fixed( 3 ) ),
            cell( q.noise, dq && dq.noise, function( v ) { return v.toExponential( 2 ); } ),
            cell( q.stars, dq && dq.stars, fixed( 0 ) ) ];
};

/*
 * Colours one row of the masters list: a worse stack than the last, an
 * unavailable or unrecognised entry, a second master for a channel
 * (counted in `counts`), and a camera that is not the expected one.
 */
UI.colourRow = function( node, e, counts )
{
   /*
    * A worse stack is coloured, a better one is not: the point is to
    * catch the case where the newest is a step backwards. Softer
    * (psf up) or noisier (snr down) both count.
    */
   if ( e.delta )
   {
      // Smaller is better for the first three, larger for stars.
      var worse = [ e.delta.fwhm > 5, e.delta.eccentricity > 5,
                    e.delta.noise > 5, e.delta.stars < -5 ];
      for ( var w = 0; w < worse.length; ++w )
         if ( worse[w] )
            node.setTextColor( 5 + w, 0xffcc7722 );
   }

   if ( e.unavailable )
   {
      node.setTextColor( 0, 0xff888888 );
      node.setTextColor( 3, 0xff888888 );
   }
   else if ( e.channel === null )
      node.setTextColor( 0, 0xffff5555 );
   else
   {
      counts[e.channel] = ( counts[e.channel] || 0 ) + 1;
      if ( counts[e.channel] > 1 )
         node.setTextColor( 0, 0xffff5555 );
   }
   if ( e.instrume !== null && !UI.instrumentMatches( e.instrume ) )
      node.setTextColor( 4, 0xffd4a017 );
};

/*
 * Fills a tool dropdown: None, then each installed tool, with `current`
 * selected when it is one of them and None otherwise; hands the chosen
 * tool, or "none", to `pick`.
 */
UI.fillToolCombo = function( combo, tools, current, pick )
{
   [ "None" ].concat( tools ).forEach( function( t ) { combo.addItem( t ); } );
   combo.currentItem = tools.indexOf( current ) + 1;
   combo.onItemSelected = function( n ) { pick( ( n == 0 ) ? "none" : tools[n - 1] ); };
};

/*
 * Fills a strength dropdown from its lower-case levels, shown capitalised,
 * selects `current` (the first level if it is not one), and hands the
 * chosen level to `pick`.
 */
UI.fillLevelCombo = function( combo, levels, current, pick )
{
   for ( var i = 0; i < levels.length; ++i )
      combo.addItem( levels[i].charAt( 0 ).toUpperCase() + levels[i].slice( 1 ) );
   combo.currentItem = Math.max( 0, levels.indexOf( current ) );
   combo.onItemSelected = function( n ) { pick( levels[n] ); };
};

/*
 * A caption for the control beside it, right-aligned; `minWidth`, when
 * given, lines a column of them up.
 */
UI.label = function( parent, text, minWidth )
{
   var l = new Label( parent );
   l.text = text;
   if ( minWidth )
      l.minWidth = minWidth;
   l.textAlignment = TextAlign_Right | TextAlign_VertCenter;
   return l;
};

/*
 * What "Update Loom automatically" does, which is the checkout updater in
 * lib/Update.js and nothing else. The wait is read from the deadline the
 * check actually runs under, so the two cannot disagree.
 */
UI.autoUpdateToolTip = function()
{
   return "<p>Each time Loom starts, it checks this git checkout for a newer Loom " +
          "before the dialog opens, waiting up to " +
          Math.round( Update.CHECK_DEADLINE_MS / 1000 ) + " s for the answer.</p>" +
          "<p>If there is one, the checkout is fast-forwarded and Loom restarts itself " +
          "on the new version. A checkout with local changes is never touched, and a " +
          "failed update is reported in the Process console.</p>";
};

/*
 * The noise tool dropdown's tooltip: where each tool runs, the strength
 * ladder, and Prism 2.0's account check.
 */
UI.noiseToolToolTip = function()
{
   return "<p>Where each tool runs, on the finished RGB, L and palettes (never on " +
          "the stars plate or single channels):</p>" +
          "<p><b>NoiseXTerminator</b>, <b>MLDenoise</b>: linear, before the stretch.<br/>" +
          "<b>SyQon Prism</b>: after the stretch.<br/>" +
          "<b>SyQon Studio Prism Essential</b>: linear, before the stretch.<br/>" +
          ( Steps.PRISM2_LINEAR_PASS
            ? "<b>SyQon Studio Prism 2.0</b>: twice, Advanced before the stretch, then " +
              "Ultra (Medium) or Max (High) after it. Low is Advanced only; with the " +
              "stretch off, only Advanced runs. "
            : "<b>SyQon Studio Prism 2.0</b>: after the stretch, Ultra (Medium) or Max " +
              "(High); with the stretch off, Medium only, Ultra on the linear image. Its " +
              "Advanced pass before the stretch, and with it Low, is off until SyQon fixes " +
              "the tile seams it left in faint sky. " ) +
          "Max is very slow: about 20 minutes a " +
          "plate where Ultra took 30 seconds, on the same image.</p>" +
          "<p><b>Strength:</b> Medium is each tool\'s own default, Low backs off, " +
          "High pushes past it (Essential\'s High is its Medium).</p>" +
          "<p>Before a Prism 2.0 run, Loom checks that your SyQon account can run " +
          "the models your strengths will use; if it cannot, Loom offers Prism Essential " +
          "(included) instead until a check succeeds.</p>";
};

/*
 * What each strength runs for Prism 2.0, read from its ladder in
 * Steps.NOISE_LEVELS, and what High costs. Measured in the maintainer's
 * runs of 2026-09-27 on the same image (Advanced 33 s in both): Ultra
 * 32 s on the RGB, Max 22 min on the RGB and 20 min on the palette.
 */
UI.prism2LevelsToolTip = function()
{
   function name( p ) { return Steps.studioModelLabel( p.model ).replace( /^Prism Deep /, "" ); }
   var ladder = Steps.NOISE_LEVELS.studio2, parts = [];
   for ( var level in ladder )
   {
      var step = ladder[level];
      var label = level.charAt( 0 ).toUpperCase() + level.slice( 1 ) + ": ";
      if ( !step.linear )
         parts.push( label + name( step.stretched ) + " after the stretch" +
                     ( step.unstretched ? ", or on the linear image with the stretch off."
                                        : "; not offered with the stretch off." ) );
      else
         parts.push( label + name( step.linear ) +
                     ( step.stretched ? ", then " + name( step.stretched ) + " after the stretch" : " only" ) + "." );
   }
   return "<p>SyQon Studio Prism 2.0 at each strength: " + parts.join( " " ) +
          " High is very slow: Max takes about 40 times as long as Ultra.</p>";
};

/* The Colour ("colour") and L ("L") strength tooltips. */
UI.noiseLevelToolTip = function( which )
{
   var own = {
      colour: "<p>Strength for the RGB composite and the narrowband palettes.</p>" +
              "<p>Lighter than L is usually right: denoising colour costs saturation, " +
              "and a three-channel composite is already less noisy than any one " +
              "channel of it.</p>",
      L: "<p>Strength for the luminance plate, set separately from the colour " +
         "composites.</p>"
   };
   return own[which] + UI.prism2LevelsToolTip();
};

/*
 * A row of controls `spacing` apart. In `items` a number is a gap of that
 * many pixels, "stretch" a stretch, and [control, factor] a control with
 * a stretch factor.
 */
UI.row = function( spacing, items )
{
   var s = new HorizontalSizer;
   s.spacing = spacing;
   items.forEach( function( c )
   {
      if ( c === "stretch" )
         s.addStretch();
      else if ( typeof c == "number" )
         s.addSpacing( c );
      else
         s.add.apply( s, [].concat( c ) );
   } );
   return s;
};

/*
 * Asks for a folder, starting from `initial` when there is one. Returns
 * the folder chosen, or null if the dialog was cancelled.
 */
UI.chooseFolder = function( caption, initial )
{
   var d = new GetDirectoryDialog;
   d.caption = caption;
   if ( initial && initial.length > 0 )
      d.initialPath = initial;
   return d.execute() ? d.directory : null;
};

/*
 * The status line's note on entries that are not listed: remembered ones
 * no longer available, and views hidden for want of a FILTER.
 */
UI.hiddenEntriesNote = function( missing, skipped )
{
   var note = "";
   if ( missing )
      note += "  <span style='color:#888888'>(" + missing +
              " remembered entr" + ( missing == 1 ? "y" : "ies" ) +
              " unavailable and not listed; the list is still saved, so " +
              "reopening the project and relaunching Loom will restore " +
              ( missing == 1 ? "it" : "them" ) + ")</span>";
   if ( skipped )
      note += "  <span style='color:#888888'>(" + skipped +
              " view" + ( skipped == 1 ? "" : "s" ) + " without FILTER hidden)</span>";
   return note;
};

/*
 * Folds the masters list into config.paths / config.views, with the
 * problems that refuse a run: duplicate channels and unrecognised
 * filters. Unavailable entries are ignored.
 */
UI.foldEntries = function( entries )
{
   var paths = {}, views = {}, seen = {}, problems = [];
   for ( var k = 0; k < Util.CHANNELS.length; ++k )
   {
      paths[Util.CHANNELS[k]] = "";
   }
   for ( var i = 0; i < entries.length; ++i )
   {
      var e = entries[i];
      if ( e.unavailable )
         continue;   // remembered but not currently available; simply ignored
      if ( e.channel === null )
      {
         problems.push( "Unrecognised FILTER " +
                        ( e.filter === null ? "(missing)" : "'" + e.filter + "'" ) +
                        " for " + e.label );
         continue;
      }
      if ( seen[e.channel] )
      {
         problems.push( "Two images map to channel " + e.channel + ": " +
                        seen[e.channel] + " and " + e.label );
         continue;
      }
      seen[e.channel] = e.label;
      if ( e.source == "view" )
         views[e.channel] = e.ref;
      else
         paths[e.channel] = e.ref;
   }
   return { paths: paths, views: views, problems: problems };
};

/*
 * One entry of a folder scan into `scan`: anything that is not a master
 * image is ignored, calibration masters are counted and skipped, and the
 * rest are ranked from their filename alone -- named when it carries a
 * FILTER token, deferred to `unnamed` when it does not.
 */
UI.classifyMasterFile = function( scan, dir, found )
{
   var name = found.name;
   if ( found.isDirectory || name == "." || name == ".." )
      return;
   if ( !/\.(xisf|fits?|fit)$/i.test( name ) )
      return;
   ++scan.total;
   if ( /^master(Flat|Dark|Bias)/i.test( name ) )
   {
      ++scan.skipped;
      return;
   }

   var rec = UI.masterFileRecord( dir, found );
   var parsed = Util.parseMasterName( name );
   if ( parsed != null && parsed.channel != null )
   {
      rec.channel  = parsed.channel;
      rec.drizzle  = parsed.drizzle;
      rec.autocrop = parsed.autocrop;
      scan.named.push( rec );
   }
   else
      scan.unnamed.push( rec );
};

/* A scanned file's record, with its times where the file system reports them. */
UI.masterFileRecord = function( dir, found )
{
   var mtime = 0;
   try { mtime = found.lastModified.getTime(); } catch ( e ) { mtime = 0; }
   var ctime = 0;
   try { ctime = found.created.getTime(); } catch ( e ) { ctime = mtime; }
   return { path: dir + "/" + found.name, name: found.name,
            mtime: mtime, created: ctime };
};

/* The masters list's name for a file: its name and extension, no folder. */
UI.fileLabel = function( path )
{
   return File.extractName( path ) + File.extractExtension( path );
};

/*
 * A masters-list entry for a file, from its header. An unreadable file is
 * still listed -- with no filter, so the list shows it as a problem.
 */
UI.fileEntry = function( path )
{
   var info = null;
   try { info = Util.readImageInfo( path ); } catch ( e ) { info = null; }
   var kws = info ? info.keywords : null;
   var filter = kws ? Util.keywordValue( kws, "FILTER" ) : null;
   return {
      source: "file",
      ref: path,
      label: UI.fileLabel( path ),
      filter: filter,
      instrume: kws ? Util.keywordValue( kws, "INSTRUME" ) : null,
      channel: Util.channelFromFilter( filter ),
      width: info ? info.width : 0,
      height: info ? info.height : 0,
      drizzle: kws ? Util.drizzleLabel( Util.keywordValue( kws, "XPIXSZ" ) ) : "",
      created: Util.fileCreatedMs( path )
   };
};

/*
 * A modal Dialog. A top-level Control reports isModal = false, but a
 * script owns PixInsight's main thread while it runs, so pumping
 * processEvents() in a loop still blocks the application -- the window is
 * technically non-modal and practically not. A genuinely modeless panel
 * needs the script to end while the window survives, which PJSR does not
 * allow. Modal it is.
 */

/*
 * A small always-on-top window with a Cancel button, shown for the duration
 * of a run.
 *
 * NOT modal. Dialog.execute() blocks the calling script, which is precisely
 * what must not happen here -- the pipeline has to keep running while the
 * window is up. So it is show()n instead, and the button becomes clickable
 * whenever the pipeline pumps events, which Pipeline.checkAbort does at
 * every stage boundary and Steps.syqonRunProcessBlocking does continuously
 * while a CLI runs.
 *
 * HONEST LIMIT: a single PixInsight process -- StarAlignment, GraXpert,
 * SPCC -- cannot be interrupted from a script once it has started. Cancel
 * takes effect at the next checkpoint, so during a long registration it
 * will sit pressed until that finishes. It is not a kill switch; it stops
 * the run at the next opportunity, which is the most any PJSR script can
 * offer.
 */
UI.CancelWindow = class extends Dialog
{
   constructor()
   {
      super();
      var self = this;
      this.cancelled = false;

      this.windowTitle = "Loom - running";

      // No "Loom is running" line: the title bar already says so.
      this.stageLabel = new Label( this );
      this.stageLabel.useRichText = true;
      this.stageLabel.text = "starting...";
      this.stageLabel.wordWrapping = true;
      /*
       * Sized for the longest line this can actually show, not for
       * "starting...".
       *
       * The window is fixed-size, so it is built once at construction and
       * whatever does not fit is simply clipped. A real line is
       * "noise reduction: SyQon Prism (medium) -> RGB  10% SyQon Prism
       * Core ML tiles", which at the old 320px wrapped to two lines and
       * lost the second half of the second one.
       */
      this.stageLabel.setScaledMinWidth( 480 );
      this.stageLabel.setScaledMinHeight( 48 );   // three lines at worst

      this.cancelButton = new PushButton( this );
      this.cancelButton.text = "Cancel";
      this.cancelButton.icon = this.scaledResource( ":/icons/cancel.png" );
      this.cancelButton.toolTip =
         "<p>Stop the run at the next checkpoint.</p>" +
         "<p>A PixInsight process already under way cannot be interrupted, " +
         "so this takes effect when the current step finishes.</p>";
      /*
       * NOT the default button, explicitly.
       *
       * This is a modeless dialog with one PushButton, and Qt promotes a
       * lone button to the dialog's default -- so Return, or Space while
       * it holds focus, activates it. Loom re-asserts this window at every
       * checkpoint (processEvents, console.show), so it collects focus
       * repeatedly during a run. A stray keystroke threw away fifteen
       * minutes of a real run that way, with nothing in the log to say
       * what had triggered it.
       */
      this.cancelButton.defaultButton = false;

      /*
       * And confirm. Cancelling is not undoable -- the stages already
       * cached survive, but whatever was mid-flight does not -- so it is
       * worth one question. Deliberately defaulting to No.
       */
      this.cancelButton.onClick = function()
      {
         var answer = StdButton_No;
         try
         {
            answer = ( new MessageBox(
               "<p>Stop this run at the next checkpoint?</p>" +
               "<p>Stages already finished stay in the cache and will be " +
               "reused, but the step now running is lost.</p>",
               "Loom - cancel the run?",
               StdIcon_Question, StdButton_No, StdButton_Yes ) ).execute();
         }
         catch ( e )
         {
            // No MessageBox (a headless run): take the click at face value.
            answer = StdButton_Yes;
         }
         if ( answer != StdButton_Yes )
            return;

         self.cancelled = true;
         self.stageLabel.text = "cancelling at the next checkpoint...";
         self.cancelButton.enabled = false;
         // In the run log, so a cancelled run says so for itself rather
         // than surfacing only as an exception with no cause.
         try { Util.log( "pipeline", "cancel requested from the run window during: " +
                                     ( self.lastStage || "unknown stage" ) ); }
         catch ( e2 ) {}
      };

      var row = new HorizontalSizer;
      row.addStretch();
      row.add( this.cancelButton );

      this.sizer = new VerticalSizer;
      this.sizer.margin = 8;
      this.sizer.spacing = 6;
      this.sizer.add( this.stageLabel );
      this.sizer.addSpacing( 4 );
      this.sizer.add( row );

      this.adjustToContents();
      this.setFixedSize();
   }

   setStage( text )
   {
      try
      {
         this.lastStage = String( text );
         this.stageLabel.text = this.lastStage;
      }
      catch ( e ) {}
   }

   /*
    * Percentage from an external CLI, at 1% granularity. The log only
    * records every 25% -- these tools emit a line per tile, hundreds per
    * frame, and finer than that buries everything else -- but a window can
    * update in place as often as it likes.
    */
   setProgress( percent, text )
   {
      try
      {
         /*
          * Percentage on its own line. Appended to the operation name it
          * pushed the line past the window's width and the tail was cut
          * off mid-word; the tool's own stage text is the least important
          * part, so it goes last where clipping costs least.
          */
         var base = this.lastStage || "running";
         this.stageLabel.text = base + "<br><b>" + Math.round( percent ) + "%</b>" +
                                ( text ? "&nbsp;&nbsp;" + text : "" );
      }
      catch ( e ) {}
   }
};

UI.SelectDialog = class extends Dialog
{
   constructor( config )
   {
   super();

   this.config = config;
   if ( !this.config.views )
      this.config.views = {};

   // entries: { source:"file"|"view", ref:<path or view id>, filter, channel, instrume }
   this.entries = [];
   /* No scan is running yet; Run's state is decided by updateRunEnabled. */
   this.busy = false;

   /*
    * The title carries the version AND the commit: the updater installs
    * branch commits, and many commits share one Util.VERSION, so the
    * number alone does not say which code is running.
    */
   this.windowTitle = Update.describeVersion( Update.installDir(), Update.io );

   var projectRow = this.buildProjectRow( config );
   var listButtons = this.buildMasterList();

   // ---- options ----

   var filterRows = this.buildFilterRows( config );
   /*
    * The processing options in the order they run: gradient removal
    * (per channel, before combining), then sharpening, noise reduction,
    * star extraction and the stretch.
    */
   this.buildGradientControls( config );
   this.buildSharpenGroup( config );
   this.buildPaletteGroup( config );
   this.buildNoiseGroup( config );
   this.buildStarGroup( config );
   var stretchMethodRow = this.buildStretchControls( config );
   this.buildExportGroup( config );
   this.buildMarsGroup( config );
   var nbRow = this.buildNarrowbandRow( config );
   var cacheRow = this.buildCacheRow( config );
   this.buildCacheDirGroup( config );

   var buttons = this.buildButtons();

   this.sizer = new VerticalSizer;
   this.sizer.margin = 8;
   this.sizer.spacing = 6;
   this.sizer.add( projectRow );
   this.sizer.add( this.info );
   this.sizer.add( this.tree, 100 );
   this.sizer.add( listButtons );
   this.sizer.add( this.progress );
   this.sizer.add( this.status );
   this.sizer.add( this.camera );
   this.sizer.add( this.gradientRow );
   this.sizer.add( this.graxpertNarrowband );
   this.sizer.add( this.smoothing );
   this.sizer.add( this.sharpenGroup );
   this.sizer.add( this.paletteGroup );
   this.sizer.add( nbRow );
   this.sizer.add( this.noiseGroup );
   this.sizer.add( this.starGroup );
   this.sizer.add( this.stretchCheck );
   this.sizer.add( stretchMethodRow );
   this.sizer.add( this.keepLinearCheck );
   this.sizer.add( this.separateLStarsCheck );
   this.sizer.add( this.exportPsbCheck );
   this.sizer.add( this.exportGroup );
   this.sizer.add( this.marsGroup );
   this.sizer.add( this.reduceHalos );
   this.sizer.add( this.validateOnly );
   this.sizer.add( cacheRow );
   this.sizer.add( this.cacheDirGroup );
   /*
    * Filters last, in their own section: they are set once for a rig and
    * then left alone, unlike the per-run options above.
    */
   this.filterGroup = new Control( this );
   var filterBox = new VerticalSizer;
   filterBox.spacing = 4;
   filterBox.add( this.filterHeading );
   filterBox.add( filterRows );
   this.filterGroup.sizer = filterBox;
   this.sizer.add( this.filterGroup );

   this.sizer.add( buttons );

   this.updateSharpenEnabled();
   this.updateNoiseEnabled();
   this.updatePaletteVisibility();
   // the stretch-dependent controls start out matching the checkbox
   this.updateStretchEnabled();

   this.adjustToContents();

   // Restore the previous selection. Entries whose view has been closed or
   // whose file has moved are dropped silently -- a stale list must not
   // block the dialog from opening.
   this.restore( config.savedList );
   }

   /* The project name, which names the PSB; returns its row. */
   buildProjectRow( config )
   {
      var self = this;
      /*
       * The project name. Names the PSB, and is the one thing in this dialog
       * that identifies the image rather than the processing.
       *
       * Defaulted from where the masters live, because PJSR exposes nothing
       * about the open PixInsight project -- no name, no path, no title. The
       * default follows the file list until the moment it is typed in, after
       * which it is left alone.
       */
      this.projectLabel = UI.label( this, "Project:" );

      this.projectEdit = new Edit( this );
      this.projectEdit.text = config.projectName || "";
      this.projectEdit.toolTip =
         "<p>Names the layered PSB — <i>&lt;project&gt;.psb</i> in the " +
         "export folder.</p>" +
         "<p>Filled in from the folder your masters came from, and kept in " +
         "step with the file list until you type something of your own.</p>";
      this.projectEdit.onEditCompleted = function()
      {
         self.config.projectName = this.text.trim();
         // from here on it is the user's, not a guess to be overwritten
         self.projectNameIsAuto = false;
         self.updatePsbCheckText();
      };

      /*
       * True while the box still holds a derived name. Adding or removing
       * masters then re-derives it; once the field has been edited it never
       * changes underneath.
       */
      this.projectNameIsAuto = ( ( config.projectName || "" ).length == 0 );

      /*
       * The PSB checkbox names the file it will write. Called from both places
       * the project name can change: the edit box, and the re-derivation that
       * follows the file list.
       *
       * Guarded because it runs during construction too, before the checkbox
       * further down this constructor exists.
       */
      this.updatePsbCheckText = function()
      {
         if ( this.exportPsbCheck )
            this.exportPsbCheck.text =
               "Also write one layered " + Pipeline.psbBaseName( this.config ) + ".psb";
      };

      this.updateProjectName = function()
      {
         if ( !this.projectNameIsAuto )
            return;
         /*
          * From the LIST, not from config.paths: the config is only filled in
          * at commit(), so during editing it still describes the previous run.
          */
         var guess = "";
         try
         {
            for ( var i = 0; i < this.entries.length; ++i )
            {
               var e = this.entries[i];
               if ( e.source != "file" || !e.ref )
                  continue;
               guess = Pipeline.projectNameFromPath( e.ref );
               if ( guess.length > 0 )
                  break;
            }
            if ( guess.length == 0 )
               guess = Pipeline.projectNameFor( this.config );
         }
         catch ( e2 ) {}
         if ( guess.length > 0 && guess != this.projectEdit.text )
         {
            this.projectEdit.text = guess;
            this.config.projectName = guess;
            this.updatePsbCheckText();
         }
      };

      return UI.row( 6, [ this.projectLabel, [ this.projectEdit, 100 ] ] );
   }

   /* The masters list, its buttons and drop targets, and the status lines. */
   buildMasterList()
   {
      var self = this;
      this.info = new Label( this );
      this.info.useRichText = true;
      this.info.text = "<b>Add your masters below.</b> The channel is taken from " +
                       "each image's FITS <i>FILTER</i> keyword, not its filename.<br>" +
                       "Use <b>Scan Masters Folder</b> or <b>Add Files</b>. Nothing is added automatically.<br>" +
                       "Results are left as open windows in the current project.";
      this.info.wordWrapping = true;

      this.tree = new TreeBox( this );
      /*
       * No Channel column: the channel is DERIVED from FILTER, so for every
       * ordinary master the two columns held the same letter twice. An
       * unrecognised filter is reported in the status line instead.
       */
      /*
       * No Camera column. One session comes off one camera, so repeating it
       * on every row said nothing per file -- and the rows that could differ
       * were the ones whose header had simply lost INSTRUME, which read as a
       * meaningful blank when it was not one. The camera is reported once,
       * for the session, below the list.
       */
      this.tree.numberOfColumns = 9;
      this.tree.setHeaderText( 0, "Filter" );
      this.tree.setHeaderText( 1, "Size" );
      this.tree.setHeaderText( 2, "Drizzle" );
      this.tree.setHeaderText( 3, "Source" );
      // When a folder holds several stacks of the same target, the creation
      // time is what tells them apart -- the names differ only by "(3)".
      this.tree.setHeaderText( 4, "Created" );
      /*
       * FWHM, as SubframeSelector measures it, with its change against the
       * previous stack of the SAME channel. Loom always uses the newest --
       * that is what a re-stack is for -- but a newer stack is not
       * automatically a better one, and the difference belongs on screen
       * before the run rather than in the stars afterwards.
       */
      this.tree.setHeaderText( 5, "FWHM" );
      this.tree.setHeaderText( 6, "Ecc" );
      this.tree.setHeaderText( 7, "Noise" );
      this.tree.setHeaderText( 8, "Stars" );
      this.tree.headerVisible = true;
      this.tree.rootDecoration = false;
      this.tree.alternateRowColor = true;
      this.tree.multipleSelection = true;
      this.tree.setScaledMinSize( 640, 240 );

      // Drop targets. View drops are the primary way to load masters:
      // PCL's Control exposes OnViewDrag/OnViewDrop, so dragging a view from
      // the workspace onto this list adds it. File drops are accepted too.
      this.tree.onViewDrag = function( x, y, view, modifiers ) { return !view.isNull; };
      this.tree.onViewDrop = function( x, y, view, modifiers ) { self.addView( view ); };
      this.onViewDrag = function( x, y, view, modifiers ) { return !view.isNull; };
      this.onViewDrop = function( x, y, view, modifiers ) { self.addView( view ); };

      this.tree.onFileDrag = function( x, y, files ) { return files.length > 0; };
      this.tree.onFileDrop = function( x, y, files ) { self.addFiles( files ); };
      this.onFileDrag = function( x, y, files ) { return files.length > 0; };
      this.onFileDrop = function( x, y, files ) { self.addFiles( files ); };

      this.addFilesButton = new PushButton( this );
      this.addFilesButton.text = "Add Files...";
      this.addFilesButton.onClick = function()
      {
         var d = new OpenFileDialog;
         d.caption = "Select masters";
         d.multipleSelections = true;
         if ( d.execute() )
            self.addFiles( d.fileNames );
      };

      this.addMastersButton = new PushButton( this );
      this.addMastersButton.text = "Scan Masters Folder...";
      this.addMastersButton.toolTip =
         "<p>Scan a folder of WBPP masters and load one per filter.</p>" +
         "<p>Prefers drizzled + autocropped, then drizzled, then autocropped, " +
         "then plain; among equals the most recent wins. Calibration masters " +
         "(flats, darks, bias) are skipped.</p>";
      this.addMastersButton.onClick = function()
      {
         var dir = UI.chooseFolder( "Select a masters folder" );
         if ( dir !== null )
            self.addMastersFolder( dir );
      };

      this.removeButton = new PushButton( this );
      this.removeButton.text = "Remove";
      this.removeButton.onClick = function() { self.removeSelected(); };

      this.clearButton = new PushButton( this );
      this.clearButton.text = "Clear";
      this.clearButton.onClick = function() { self.entries = []; self.rebuild(); };

      this.status = new Label( this );
      this.status.useRichText = true;
      this.status.wordWrapping = true;
      /*
       * Over the status line while masters are read or measured, hidden
       * otherwise. The line says which master; the bar says how far
       * through the folder, and marks the master under way with a moving
       * block (Util.ProgressBar says when that block can move).
       */
      this.progress = new Util.ProgressBar( this );
      this.progress.setScaledFixedHeight( 14 );
      this.progress.visible = false;

      /*
       * The session's camera, and the QE curve it resolves to. This is the
       * number that actually matters: an unrecognised or absent camera means
       * SPFC calibrates against the ideal curve rather than the real device
       * response, and until now the only sign of that was a blank cell.
       */
      this.camera = new Label( this );
      this.camera.useRichText = true;
      this.camera.wordWrapping = true;
      /*
       * Scanning a folder first, because it is what a run actually starts
       * with: WBPP writes a masters folder and Loom picks the best variant
       * per filter out of it. Adding files by hand is the exception.
       */
      return UI.row( 6, [ this.addMastersButton, this.addFilesButton, "stretch",
                          this.removeButton, this.clearButton ] );
   }

   /* The filter selectors, seeded from a saved SPFC icon; the rows are returned. */
   buildFilterRows( config )
   {
      var self = this;
      /*
       * Filter selectors. A FITS FILTER of "L"/"R"/"G"/"B" names the channel,
       * not the physical filter, and SPFC fails outright without a
       * transmission curve -- so the real filter has to be chosen here.
       */
      this.filterCombos = {};
      var filterRows = new VerticalSizer;
      filterRows.spacing = 4;

      // Seed the selectors from a saved SPFC process icon, if there is one.
      // A remembered Loom choice still wins -- the user's last explicit pick
      // in this dialog beats an inferred default.
      var spfcCfg = null;
      try { spfcCfg = Steps.configuredSPFC(); } catch ( e ) { spfcCfg = null; }
      var seeded = {};
      if ( spfcCfg != null )
      {
         seeded.L = spfcCfg.grayFilterName;
         seeded.R = spfcCfg.redFilterName;
         seeded.G = spfcCfg.greenFilterName;
         seeded.B = spfcCfg.blueFilterName;
      }
      var bb = [ "L", "R", "G", "B" ];
      for ( var fi = 0; fi < bb.length; ++fi )
      {
         var fk = bb[fi];
         var flabel = UI.label( this, fk + " filter:", 100 );

         var fcombo = new ComboBox( this );
         fcombo.setScaledMinWidth( 260 );
         var curves = [];
         try { curves = Steps.listFilterCurves( fk ); } catch ( e ) { curves = []; }
         for ( var ci = 0; ci < curves.length; ++ci )
            fcombo.addItem( curves[ci].name );
         var chosen = ( config.filters && config.filters[fk] ) ? config.filters[fk]
                                                               : ( seeded[fk] || "" );
         for ( var ck = 0; ck < curves.length; ++ck )
            if ( curves[ck].name == chosen )
               fcombo.currentItem = ck;
         ( function( key, combo )
         {
            combo.onItemSelected = function( i )
            {
               if ( !self.config.filters ) self.config.filters = {};
               self.config.filters[key] = combo.itemText( i );
            };
            // record the initial selection so a never-touched combo still counts
            if ( combo.numberOfItems > 0 )
            {
               if ( !self.config.filters ) self.config.filters = {};
               if ( !self.config.filters[key] )
                  self.config.filters[key] = combo.itemText( combo.currentItem );
            }
         } )( fk, fcombo );
            this.filterCombos[fk] = fcombo;

         filterRows.add( UI.row( 4, [ flabel, fcombo, "stretch" ] ) );
      }

      this.filterHeading = new Label( this );
      this.filterHeading.useRichText = true;
      this.filterHeading.text = "<b>Filters</b>";
      return filterRows;
   }

   /* The sharpening row, hidden with nothing installed to run it. */
   buildSharpenGroup( config )
   {
      var self = this;
      /*
       * Sharpening. Only tools actually installed are offered; if none are,
       * the whole group is disabled and the pipeline skips those stages.
       */
      var tools = [];
      try { tools = Steps.availableSharpenTools(); } catch ( e ) { tools = []; }

      /*
       * The whole group lives in a container so it can be HIDDEN, not merely
       * disabled, when neither BlurXTerminator nor SyQon Parallax is
       * installed. Children are parented to the group, not the dialog, or
       * hiding it would leave them visible.
       */
      this.sharpenGroup = new Control( this );

      this.sharpenToolLabel = UI.label( this.sharpenGroup, "Sharpening:", 100 );

      this.sharpenToolCombo = new ComboBox( this.sharpenGroup );
      UI.fillToolCombo( this.sharpenToolCombo, tools, config.sharpenTool, function( tool )
      {
         self.config.sharpenTool = tool;
         self.updateSharpenEnabled();
      } );
      this.sharpenToolCombo.enabled = tools.length > 0;
      this.sharpenToolCombo.toolTip = tools.length
         ? "<p>Aberration correction always runs when a tool is selected.</p>" +
           "<p>With SyQon Studio installed, the aberration pass uses Studio " +
           "Parallax\'s correction automatically. BlurXTerminator then does " +
           "star reduction and detail on the composite.</p>"
         : "No sharpening tool installed (BlurXTerminator, SyQon Studio or SyQon Parallax).";

      this.starReductionLabel = UI.label( this.sharpenGroup, "Star reduction:", 100 );

      this.starReductionCombo = new ComboBox( this.sharpenGroup );
      UI.fillLevelCombo( this.starReductionCombo, [ "none", "low", "medium", "high" ],
                         config.starReduction || "none",
                         function( level ) { self.config.starReduction = level; } );

      this.detailLabel = UI.label( this.sharpenGroup, "Detail:", 100 );

      this.detailCombo = new ComboBox( this.sharpenGroup );
      UI.fillLevelCombo( this.detailCombo, [ "none", "low", "medium", "high" ],
                         config.detailLevel || "none",
                         function( level ) { self.config.detailLevel = level; } );

      this.sharpenGroup.sizer = UI.row( 4, [ this.sharpenToolLabel, this.sharpenToolCombo, 12,
                                             this.starReductionLabel, this.starReductionCombo, 12,
                                             this.detailLabel, this.detailCombo, "stretch" ] );
      if ( tools.length == 0 )
      {
         this.sharpenGroup.visible = false;
         // nothing can run, so make sure nothing is configured to
         this.config.sharpenTool = "none";
         this.config.starReduction = "none";
         this.config.detailLevel = "none";
      }
   }

   /* One checkbox per palette, hidden without narrowband data. */
   buildPaletteGroup( config )
   {
      var self = this;
      /*
       * Hideable: palettes are meaningless without narrowband data, and the
       * check is dynamic because the list changes as entries are added and
       * removed. rebuild() calls updatePaletteVisibility().
       */
      this.paletteGroup = new Control( this );

      this.paletteLabel = UI.label( this.paletteGroup, "Narrowband:", 100 );

      /*
       * Checkboxes, not a combo: more than one palette can be produced from
       * the same narrowband data in a single run, and comparing them
       * side by side is the usual reason to build any of them.
       */
      this.paletteChecks = {};
      var paletteItems = [ this.paletteLabel ];

      var palettes = Util.paletteNames();
      for ( var pi = 0; pi < palettes.length; ++pi )
      {
         var pname = palettes[pi];
         var cb = new CheckBox( this.paletteGroup );
         cb.text = pname;
         cb.checked = ( config.palettes || [] ).indexOf( pname ) >= 0;
         cb.toolTip = pname + ": R=" + Util.PALETTES[pname][0] +
                      " G=" + Util.PALETTES[pname][1] +
                      " B=" + Util.PALETTES[pname][2] +
                      ". Built alongside the RGB composite, and not flux- or " +
                      "colour-calibrated: a palette is an aesthetic mapping, " +
                      "not a photometric rendition.";
         ( function( name, box )
         {
            box.onCheck = function( checked )
            {
               var list = self.config.palettes || [];
               var at = list.indexOf( name );
               if ( checked && at < 0 ) list.push( name );
               else if ( !checked && at >= 0 ) list.splice( at, 1 );
               self.config.palettes = list;
            };
         } )( pname, cb );
         this.paletteChecks[pname] = cb;
         paletteItems.push( cb, 6 );
      }
      this.paletteGroup.sizer = UI.row( 4, paletteItems.concat( "stretch" ) );
   }

   /* The noise-reduction row: tool, then colour and L strengths. */
   buildNoiseGroup( config )
   {
      var self = this;
      /*
       * Noise reduction on the finished composites. Only tools this
       * installation can actually run are offered, and the whole row is hidden
       * when there are none -- the same rule the sharpening controls follow.
       */
      this.noiseGroup = new Control( this );

      this.noiseLabel = UI.label( this.noiseGroup, "Noise reduction:" );

      this.noiseCombo = new ComboBox( this.noiseGroup );
      var noiseTools = [];
      try { noiseTools = Steps.availableNoiseTools(); } catch ( e ) { noiseTools = []; }
      UI.fillToolCombo( this.noiseCombo, noiseTools, config.noiseTool, function( tool )
      {
         self.config.noiseTool = tool;
         self.fillNoiseLevels();
         self.updateNoiseEnabled();
      } );
      this.noiseCombo.toolTip = UI.noiseToolToolTip();

      this.noiseLevelLabel = UI.label( this.noiseGroup, "Colour:" );

      this.noiseLevelCombo = new ComboBox( this.noiseGroup );
      this.noiseLevelCombo.toolTip = UI.noiseLevelToolTip( "colour" );

      /*
       * L gets its own strength. It is one channel, usually the shortest
       * integration of the set, and it carries the detail everything else is
       * blended against -- so it is both the noisiest plate and the one that
       * can take the most denoising without costing colour.
       */
      this.noiseLevelLLabel = UI.label( this.noiseGroup, "L:" );

      this.noiseLevelLCombo = new ComboBox( this.noiseGroup );
      this.noiseLevelLCombo.toolTip = UI.noiseLevelToolTip( "L" );
      this.fillNoiseLevels();

      this.noiseGroup.sizer = UI.row( 6, [ this.noiseLabel, this.noiseCombo, 12,
                                           this.noiseLevelLabel, this.noiseLevelCombo, 8,
                                           this.noiseLevelLLabel, this.noiseLevelLCombo, "stretch" ] );
      this.noiseGroup.visible = ( noiseTools.length > 0 );
   }

   /* The star-extraction row. */
   buildStarGroup( config )
   {
      var self = this;
      /*
       * Star extraction. Same rule as the sharpening and noise controls: only
       * tools this installation can run are offered, and the row disappears
       * when there are none.
       */
      this.starGroup = new Control( this );

      this.starLabel = UI.label( this.starGroup, "Star extraction:" );

      this.starCombo = new ComboBox( this.starGroup );
      var starTools = [];
      try { starTools = Steps.availableStarTools(); } catch ( e ) { starTools = []; }
      UI.fillToolCombo( this.starCombo, starTools, config.starTool,
                        function( tool ) { self.config.starTool = tool; } );
      this.starCombo.toolTip =
         "<p>Splits L, the RGB composite and any narrowband palette into a " +
         "starless frame and a stars frame, after sharpening and before noise " +
         "reduction -- so noise reduction never touches the stars.</p>" +
         "<p>The unsplit image is not kept: starless and stars screen back " +
         "together into it exactly. Narrowband stars are kept only when the " +
         "run produced no RGB composite.</p>";

      this.starGroup.sizer = UI.row( 6, [ this.starLabel, this.starCombo, "stretch" ] );
      this.starGroup.visible = ( starTools.length > 0 );
   }

   /* The stretch checkbox, its method, and the options that follow it. */
   buildStretchControls( config )
   {
      var self = this;
      /*
       * Stretch. One checkbox, because the rule takes nothing from the user:
       * the black point is the plate's own defect-robust minimum and the midtone
       * sends its own sky median to a fixed target. See
       * docs/superpowers/specs/2026-09-15-stretch-design.md.
       */
      this.stretchCheck = new CheckBox( this );
      this.stretchCheck.text = "Stretch the results (non-linear output)";
      this.stretchCheck.checked = !!config.stretch;
      this.stretchCheck.toolTip =
         "<p>Applies one HistogramTransformation per plate, computed from that " +
         "plate alone: black point at its darkest level that is not a " +
         "single-pixel defect, midtone placing its sky median at " +
         Steps.STRETCH_SKY_TARGET + ".</p>" +
         "<p>Nothing to set and nothing per-image to tune. Starless plates are " +
         "stretched after extraction; the stars plate is stretched before it, so " +
         "the two carry independent transforms and each looks right on its own. " +
         "They will NOT screen back together into the original.</p>" +
         "<p>Off leaves every result linear, as before.</p>";
      this.stretchCheck.onCheck = function( checked )
      {
         self.config.stretch = checked;
         self.updateStretchEnabled();
         if ( self.noiseLevelCombo )
            self.fillNoiseLevels();   // Prism 2.0 offers High only with a stretch
      };

      /*
       * A second method, for when the deterministic MTF stretch is not what
       * the image wants. MAS stretches adaptively and restores large-scale
       * contrast afterwards; the MTF stretch stays the default because it is
       * reproducible from three measured numbers and needs nothing set.
       */
      this.stretchMethodLabel = UI.label( this, "Method:" );

      this.stretchMethodCombo = new ComboBox( this );
      this.stretchMethodCombo.addItem( "Histogram (deterministic MTF)" );
      this.stretchMethodCombo.addItem( "MultiscaleAdaptiveStretch" );
      this.stretchMethodCombo.currentItem =
         ( config.stretchMethod == Steps.STRETCH_METHOD_MAS ) ? 1 : 0;
      this.stretchMethodCombo.toolTip =
         "<p><b>Histogram</b>: one HistogramTransformation per plate, computed " +
         "from that plate alone. Deterministic and reproducible.</p>" +
         "<p><b>MultiscaleAdaptiveStretch</b>: target background " +
         Steps.MAS_PARAMETERS.targetBackground + ", aggressiveness " +
         Steps.MAS_PARAMETERS.aggressiveness + ", dynamic range compression " +
         Steps.MAS_PARAMETERS.dynamicRangeCompression + ", contrast recovery on " +
         "at intensity " + Steps.MAS_PARAMETERS.contrastRecoveryIntensity +
         ", colour saturation off.</p>" +
         "<p><b>Scale separation is not set by Loom</b> — it is an enum " +
         "whose values a script cannot read, so the process's own default is " +
         "used and the run logs which. To pin it, save a " +
         "MultiscaleAdaptiveStretch process icon: Loom then uses that icon's " +
         "settings verbatim.</p>" +
         "<p><b>This applies to the starless plates only</b> — HSO, RGB " +
         "and L. Star extraction always uses the histogram stretch, whichever " +
         "method is chosen here.</p>" +
         "<p>That is not a preference: the stars plate is the unscreen " +
         "difference taken inside the extraction, in the domain of the stretch " +
         "applied there, and it is never stretched again. Changing it would " +
         "change what extraction produces, not just how it looks.</p>";
      this.stretchMethodCombo.onItemSelected = function( i )
      {
         self.config.stretchMethod = ( i == 1 ) ? Steps.STRETCH_METHOD_MAS
                                                : Steps.STRETCH_METHOD_MTF;
      };

      this.keepLinearCheck = new CheckBox( this );
      this.keepLinearCheck.text = "Also keep the unstretched RGB and palette";
      this.keepLinearCheck.checked = !!config.keepLinear;
      this.keepLinearCheck.toolTip =
         "<p>The stretch overwrites each composite in place. With this on, the " +
         "linear version is kept too, as <i>RGB_linear</i> and " +
         "<i>&lt;palette&gt;_linear</i>, so you can go back to it without " +
         "re-running.</p>" +
         "<p>It is stored beside the stretch in the cache, so it survives a " +
         "cached run. That costs a second full-size image per composite, which " +
         "is why it is off by default.</p>" +
         "<p>L is not included \u2014 only the colour composites.</p>";
      this.keepLinearCheck.onCheck = function( checked )
      {
         self.config.keepLinear = checked;
      };

      /*
       * Frequency separation of the L stars plate. A retouching aid for the one
       * plate whose halos get worked on by hand, so it is opt-in and applies to
       * nothing else.
       */
      this.separateLStarsCheck = new CheckBox( this );
      this.separateLStarsCheck.text = "Frequency-separate the L stars plate";
      this.separateLStarsCheck.checked = !!config.separateLStars;
      this.separateLStarsCheck.toolTip =
         "<p>Splits <i>L_stars</i> into <i>L_stars_low</i> and " +
         "<i>L_stars_high</i>, so star cores and halos can be retouched " +
         "separately.</p>" +
         "<p>The blur radius is measured from the plate's own stars — " +
         "PSF sigma × " + Steps.FS_SIGMA_FACTOR + " — so it follows " +
         "the seeing of the night rather than a number carried over from " +
         "another image. The run logs the value it used.</p>" +
         "<p><b>To recombine in Photoshop</b>, put <i>L_stars_high</i> over " +
         "<i>L_stars_low</i> in <b>Linear Light</b> blend mode. That returns " +
         "the original exactly.</p>" +
         "<p>The high layer is <i>(original &minus; low)/2 + 0.5</i> — the " +
         "same thing Photoshop's Apply Image produces with Subtract, Scale 2, " +
         "Offset 128 (8-bit) or Add, Scale 2, Invert (16-bit). The 0.5 is " +
         "there because the difference is signed; the halving is there because " +
         "Linear Light computes <i>base + 2&times;blend &minus; 1</i>. Any " +
         "other blend mode gives the wrong image.</p>";
      this.separateLStarsCheck.onCheck = function( checked )
      {
         self.config.separateLStars = checked;
      };

      /*
       * One layered document, assembled the way the plates are meant to be
       * used. Written into the same export folder as the TIFFs.
       */
      this.exportPsbCheck = new CheckBox( this );
      /*
       * The label names the file it will actually write, so the project name
       * typed above can be read back without opening the export folder. Kept
       * in step by updatePsbCheckText below.
       */
      this.exportPsbCheck.text =
         "Also write one layered " + Pipeline.psbBaseName( config ) + ".psb";
      this.exportPsbCheck.checked = !!config.exportPsb;
      this.exportPsbCheck.toolTip =
         "<p>Assembles every plate into a single Photoshop Large Document, " +
         "bottom to top:</p>" +
         "<p><b>HSO</b> group → HSO starless<br/>" +
         "<b>RGB</b> group → RGB starless <i>(hidden)</i><br/>" +
         "<b>Stars</b> group, <i>Screen</i> → RGB stars, then L stars in " +
         "<i>Luminosity</i></p>" +
         "<p>With the L stars plate frequency-separated, that last becomes an " +
         "<b>L Stars</b> group in Luminosity holding the low layer and the " +
         "high layer in <i>Linear Light</i>.</p>" +
         "<p>PSB rather than PSD: uncompressed 16-bit layers of a frame this " +
         "size run to about 3 GB, and PSD stops at 2. Expect the write to " +
         "take a minute and the file to be large.</p>";
      this.exportPsbCheck.onCheck = function( checked )
      {
         self.config.exportPsb = checked;
      };

      /*
       * Keeping an unstretched copy is meaningless without a stretch, so the
       * control follows the stretch checkbox rather than sitting there doing
       * nothing. Called on every toggle AND once at construction, so the initial
       * state matches instead of only becoming correct after the first click.
       */
      this.updateStretchEnabled = function()
      {
         var on = !!this.config.stretch;
         this.keepLinearCheck.enabled = on;
         this.stretchMethodLabel.enabled = on;
         this.stretchMethodCombo.enabled = on;
         /*
          * Export follows the stretch, because Pipeline.exportResults refuses
          * to run without it: 16 bits cannot hold linear data without
          * posterising it. The controls were live and simply did nothing,
          * which reads as a broken export rather than a refused one.
          */
         this.exportLabel.enabled = on;
         this.exportEdit.enabled = on;
         this.exportBrowse.enabled = on;
         this.exportPsbCheck.enabled = on;
      };
      return UI.row( 6, [ 20, this.stretchMethodLabel, this.stretchMethodCombo, "stretch" ] );
   }

   /* The TIFF export folder row. */
   buildExportGroup( config )
   {
      var self = this;
      /*
       * Export. Empty folder means no export -- one control, no separate
       * enable checkbox to fall out of step with it.
       */
      this.exportGroup = new Control( this );

      this.exportLabel = UI.label( this.exportGroup, "Export 16-bit TIFFs to:" );

      this.exportEdit = new Edit( this.exportGroup );
      this.exportEdit.text = config.exportDir || "";
      this.exportEdit.toolTip =
         "<p>Writes every result as a 16-bit TIFF into this folder, named after " +
         "the window it came from. Leave empty to export nothing.</p>" +
         "<p>The plates stay 32-bit float in the workspace; the conversion " +
         "happens on a throwaway copy.</p>" +
         "<p>Requires the stretch: exporting linear data to 16 bits would " +
         "posterise it, so Loom refuses rather than writing a ruined file.</p>";
      this.exportEdit.onEditCompleted = function()
      {
         self.config.exportDir = this.text.trim();
      };

      this.exportBrowse = new PushButton( this.exportGroup );
      this.exportBrowse.text = "Browse...";
      this.exportBrowse.onClick = function()
      {
         var dir = UI.chooseFolder( "Select a folder for the exported TIFFs", config.exportDir );
         if ( dir !== null )
         {
            self.config.exportDir = dir;
            self.exportEdit.text = dir;
         }
      };

      this.exportGroup.sizer = UI.row( 6, [ this.exportLabel, [ this.exportEdit, 100 ], this.exportBrowse ] );
   }

   /* The MARS folder row, shown only when PixInsight cannot find the databases itself. */
   buildMarsGroup( config )
   {
      var self = this;
      /*
       * MARS database folder.
       *
       * Shown ONLY when PixInsight cannot tell us where the databases are --
       * i.e. there is no MGC process icon and nothing persisted in the MGC
       * interface. When PixInsight already knows, asking again is a question
       * with a right answer already on file, and a second place to keep the
       * same setting in sync.
       *
       * The probe is deliberately Steps.configuredMGC() with NO argument: it
       * asks what the automatic routes alone would find, independently of
       * whatever this dialog has stored.
       */
      var marsKnown = false;
      var marsSource = "";
      try
      {
         var autoMgc = Steps.configuredMGC();
         marsKnown = ( autoMgc != null && autoMgc.marsDatabaseFiles &&
                       autoMgc.marsDatabaseFiles.length > 0 );
         if ( marsKnown )
            marsSource = autoMgc.source || "PixInsight";
      }
      catch ( e ) { marsKnown = false; }

      this.marsGroup = new Control( this );

      this.marsLabel = UI.label( this.marsGroup, "MARS database folder:" );

      this.marsEdit = new Edit( this.marsGroup );
      this.marsEdit.text = config.marsPath || "";
      this.marsEdit.toolTip =
         "<p>Folder holding the MARS gradient-reference databases (*.xmars).</p>" +
         "<p>Only asked for because PixInsight has no MARS configuration of its " +
         "own -- no MultiscaleGradientCorrection process icon, and nothing saved " +
         "in the MGC interface. Setting either of those instead makes this row " +
         "disappear.</p>";
      this.marsEdit.onEditCompleted = function()
      {
         self.config.marsPath = this.text.trim();
      };

      this.marsBrowse = new PushButton( this.marsGroup );
      this.marsBrowse.text = "Browse...";
      this.marsBrowse.onClick = function()
      {
         var dir = UI.chooseFolder( "Select the folder holding the MARS *.xmars databases",
                                    config.marsPath );
         if ( dir === null )
            return;
         self.config.marsPath = dir;
         self.marsEdit.text = dir;
         var n = 0;
         try { n = Steps.marsDatabasesInDirectory( dir ).length; }
         catch ( e ) { n = 0; }
         /*
          * Say what was found straight away. A folder with no .xmars in it
          * is silently ignored at run time (it falls through to the
          * automatic routes), so without this the user would not learn
          * until MGC failed.
          */
         if ( n > 0 )
            Util.log( "mgc", "MARS folder set: " + n + " database file(s) found" );
         else
            Util.warn( "mgc", "No .xmars files directly inside " + dir );
      };

      this.marsGroup.sizer = UI.row( 6, [ this.marsLabel, [ this.marsEdit, 100 ], this.marsBrowse ] );
      this.marsGroup.visible = !marsKnown;
      if ( marsKnown )
         Util.log( "mgc", "MARS databases known from " + marsSource +
                          "; not asking for a folder" );
   }

   /* Narrowband bandwidth and normalisation, then the halo option. */
   buildNarrowbandRow( config )
   {
      var self = this;
      this.nbBandwidthLabel = UI.label( this, "Narrowband bandwidth (nm):" );

      this.nbBandwidth = new NumericEdit( this );
      this.nbBandwidth.label.text = "";
      this.nbBandwidth.setRange( 0.5, 30.0 );
      this.nbBandwidth.setPrecision( 2 );
      this.nbBandwidth.setValue( config.narrowbandBandwidth || 3.0 );
      this.nbBandwidth.toolTip =
         "<p>The bandwidth of your narrowband filters, used by SPCC's " +
         "narrowband mode when calibrating a palette.</p>" +
         "<p>The emission-line wavelengths are physical constants and are set " +
         "automatically (Ha 656.28, SII 671.60, OIII 500.70 nm); only the " +
         "bandwidth depends on which filters you own.</p>";
      this.nbBandwidth.onValueUpdated = function( v ) { self.config.narrowbandBandwidth = v; };

      /*
       * NarrowbandNormalization used to run unconditionally, with nothing in
       * the dialog to say so or to stop it. It is a colour operation applied
       * AFTER the palette has been calibrated by SPCC, so "SPCC and combine,
       * nothing else" was not reachable from this dialog.
       */
      this.nbNormalize = new CheckBox( this );
      this.nbNormalize.text = "Normalise the palette";
      this.nbNormalize.toolTip =
         "<p>Runs NarrowbandNormalization on the palette, after SPCC has " +
         "calibrated it.</p>" +
         "<p>Turn it off to keep the emission-line ratios exactly as SPCC " +
         "left them -- the palette then carries the calibration and nothing " +
         "else.</p>";
      this.nbNormalize.checked = !!config.narrowbandNormalize;
      this.nbNormalize.onCheck = function( c ) { self.config.narrowbandNormalize = c; };

      this.reduceHalos = new CheckBox( this );
      this.reduceHalos.text = "Reduce halos (match channel PSFs)";
      this.reduceHalos.checked = !!config.reduceHalos;
      this.reduceHalos.toolTip =
         "Colour halos come from the channels having different star sizes. " +
         "This measures each channel and blurs the sharper ones up to the " +
         "widest, removing the halos at their source. DESTRUCTIVE: the " +
         "sharpest channel loses resolution, which is acceptable in LRGB " +
         "because L carries the detail and is left untouched.";
      this.reduceHalos.onCheck = function( c ) { self.config.reduceHalos = c; };
      return UI.row( 4, [ this.nbBandwidthLabel, this.nbBandwidth, 12, this.nbNormalize, "stretch" ] );
   }

   /* Gradient removal: the tool, its narrowband extension and GraXpert's smoothing. */
   buildGradientControls( config )
   {
      var self = this;
      /*
       * Gradient removal: a dropdown like the other tools, offering only what
       * this installation can run -- GraXpert when its module is present,
       * SyQon Studio's Deep Gradient when syqon-cli is found. It used to be a
       * GraXpert checkbox; Steps.migrateConfig maps the old setting.
       */
      var gradientTools = [ Steps.GRADIENT_TOOL_NONE ];
      try { if ( Steps.moduleAvailable( "GraXpert" ) ) gradientTools.push( Steps.GRADIENT_TOOL_GRAXPERT ); }
      catch ( e ) {}
      if ( Steps.studioAvailable() )
         gradientTools.push( Steps.GRADIENT_TOOL_STUDIO );

      /*
       * A saved tool that is not installed here shows as None AND becomes
       * none. Leaving the config on it would show None while preflight
       * refused the run for a tool the dialog does not display.
       */
      if ( gradientTools.indexOf( Steps.gradientToolOf( config ) ) < 0 )
         config.gradientTool = Steps.GRADIENT_TOOL_NONE;
      else
         config.gradientTool = Steps.gradientToolOf( config );

      this.gradientLabel = UI.label( this, "Gradient removal:" );

      this.gradientCombo = new ComboBox( this );
      for ( var gti = 0; gti < gradientTools.length; ++gti )
         this.gradientCombo.addItem( Steps.gradientToolLabel( gradientTools[gti] ) );
      this.gradientCombo.currentItem = Math.max( 0, gradientTools.indexOf( config.gradientTool ) );
      this.gradientCombo.toolTip =
         "<p>MultiscaleGradientCorrection always runs on L, R, G and B. This " +
         "adds a second gradient pass after it, on the linear channels " +
         "before registration; <b>Multi Gradient only</b> adds none.</p>" +
         "<p><b>GraXpert</b> takes the smoothing below. <b>SyQon Studio Deep " +
         "Gradient</b> takes no settings: it runs on the same linear data, which " +
         "is what its input contract asks for.</p>";
      this.gradientCombo.onItemSelected = function( i )
      {
         self.config.gradientTool = gradientTools[i];
         self.updateGradientEnabled();
      };

      this.gradientRow = UI.row( 6, [ this.gradientLabel, this.gradientCombo, "stretch" ] );

      /*
       * Nested under gradient removal, and only live while a tool is chosen:
       * this extends that option to H, S and O rather than standing alone.
       * The config key stays graxpertNarrowband so saved settings keep it.
       */
      this.graxpertNarrowband = new CheckBox( this );
      this.graxpertNarrowband.text = "Also remove gradients from H, S and O";
      this.graxpertNarrowband.checked = !!config.graxpertNarrowband;
      this.graxpertNarrowband.toolTip =
         "<p>Background extraction on the narrowband channels as well, before " +
         "registration, with the same tool and settings as the broadband channels.</p>" +
         "<p>Off by default. Narrowband data usually has little gradient to " +
         "remove, and on faint emission a gradient tool can take nebulosity for " +
         "background. Turn it on when H, S or O show a real gradient.</p>" +
         "<p>MGC is not offered here: it needs an astrometric solution and " +
         "SPFC, and narrowband channels are not solved.</p>";
      this.graxpertNarrowband.onCheck = function( c ) { self.config.graxpertNarrowband = c; };

      this.smoothing = new NumericControl( this );
      this.smoothing.label.text = "GraXpert smoothing:";
      this.smoothing.setRange( 0, 1 );
      this.smoothing.setPrecision( 2 );
      this.smoothing.setValue( config.smoothing );
      this.smoothing.onValueUpdated = function( v ) { self.config.smoothing = v; };
      this.updateGradientEnabled();
   }

   /* Validate-only, the cache switches and the updater. */
   buildCacheRow( config )
   {
      var self = this;
      this.validateOnly = new CheckBox( this );
      this.validateOnly.text = "Validate only (check everything, run nothing)";
      this.validateOnly.checked = !!config.validateOnly;
      this.validateOnly.onCheck = function( c ) { self.config.validateOnly = c; };

      /*
       * Stage-result cache. Keys chain through solve/SPFC/MGC/GraXpert/
       * register, so this is safe to leave on by default: any parameter that
       * changes a stage's result invalidates that stage and everything after
       * it automatically (see lib/Cache.js, lib/Pipeline.js).
       */
      this.useCache = new CheckBox( this );
      this.useCache.text = "Use cache";
      this.useCache.toolTip = "Reuse cached stage results (solve/SPFC/MGC/GraXpert/" +
                              "register) when nothing that affects them has changed. " +
                              "A different filter, QE curve, MARS database, GraXpert " +
                              "setting or reference frame invalidates only the stages " +
                              "it affects.";
      this.useCache.checked = !!config.useCache;
      this.useCache.onCheck = function( c )
      {
         self.config.useCache = c;
         self.ignoreCache.enabled = c;
      };

      this.ignoreCache = new CheckBox( this );
      this.ignoreCache.text = "Ignore cache for this run";
      this.ignoreCache.toolTip = "Recompute every stage this run instead of reusing " +
                                 "cached results, but still write fresh cache entries " +
                                 "so later runs can reuse them.";
      this.ignoreCache.checked = !!config.ignoreCache;
      this.ignoreCache.enabled = config.useCache;
      this.ignoreCache.onCheck = function( c ) { self.config.ignoreCache = c; };

      this.cacheInfo = new Label( this );
      this.cacheInfo.textAlignment = TextAlign_Left | TextAlign_VertCenter;

      this.clearCacheButton = new PushButton( this );
      this.clearCacheButton.text = "Clear cache";
      this.clearCacheButton.toolTip = "Delete every cached stage result.";
      this.clearCacheButton.onClick = function() { self.clearCache(); };
      this.updateClearCacheLabel();

      /*
       * Updating is opt-out, and offered only in a git checkout: that is
       * the only install Loom updates itself. A release install is kept
       * current by PixInsight's update repository, so a checkbox there
       * would promise something that never happens. Off means nothing is
       * run and nothing is reported -- not a quieter updater, no updater.
       */
      var items = [ this.useCache, this.ignoreCache, "stretch" ];
      if ( Update.isCheckout() )
      {
         this.autoUpdate = new CheckBox( this );
         this.autoUpdate.text = "Update Loom automatically";
         this.autoUpdate.toolTip = UI.autoUpdateToolTip();
         this.autoUpdate.checked = !!config.autoUpdate;
         this.autoUpdate.onCheck = function( c )
         {
            self.config.autoUpdate = c;
         };
         items.push( this.autoUpdate );
      }

      return UI.row( 6, items.concat( [ this.clearCacheButton, 8, this.cacheInfo ] ) );
   }

   /* The cache folder row. */
   buildCacheDirGroup( config )
   {
      var self = this;
      /*
       * Where the cache lives. Empty means the system temp dir, so an
       * upgrading user sees no change and nothing has to be migrated.
       *
       * Changing this moves nothing: the previous folder keeps its entries,
       * the new one starts cold, and "Clear cache" only ever empties the one
       * selected here. Both controls re-point Cache and refresh the readout
       * beside them, so the size and entry count always describe the folder
       * named in the edit.
       */
      this.cacheDirGroup = new Control( this );

      this.cacheDirLabel = UI.label( this.cacheDirGroup, "Cache folder:" );

      this.cacheDirEdit = new Edit( this.cacheDirGroup );
      this.cacheDirEdit.text = config.cacheDir || "";
      this.cacheDirEdit.toolTip =
         "<p>Folder for cached stage results. Leave empty to use the system " +
         "temporary folder.</p>" +
         "<p>A few full runs amount to tens of gigabytes, so a volume with " +
         "room to spare -- or a fast scratch disk -- is worth choosing.</p>" +
         "<p>Changing this does not move existing entries: the old folder " +
         "keeps them and the new one starts empty.</p>";
      this.cacheDirEdit.onEditCompleted = function()
      {
         self.config.cacheDir = this.text.trim();
         Cache.setDir( self.config.cacheDir );
         self.updateClearCacheLabel();
      };

      this.cacheDirBrowse = new PushButton( this.cacheDirGroup );
      this.cacheDirBrowse.text = "Browse...";
      this.cacheDirBrowse.onClick = function()
      {
         var dir = UI.chooseFolder( "Select a folder for Loom's cache", self.config.cacheDir );
         if ( dir !== null )
         {
            self.config.cacheDir = dir;
            self.cacheDirEdit.text = dir;
            Cache.setDir( dir );
            self.updateClearCacheLabel();
         }
      };

      this.cacheDirGroup.sizer = UI.row( 6, [ this.cacheDirLabel, [ this.cacheDirEdit, 100 ], this.cacheDirBrowse ] );
   }

   /* Run and Cancel. */
   buildButtons()
   {
      var self = this;
      this.runButton = new PushButton( this );
      this.runButton.text = "Run";
      this.runButton.onClick = function() { if ( self.commit() ) self.ok(); };

      this.cancelButton = new PushButton( this );
      this.cancelButton.text = "Cancel";
      this.cancelButton.onClick = function() { self.cancel(); };

      return UI.row( 6, [ "stretch", this.runButton, this.cancelButton ] );
   }

   /*
    * Rebuilds the list from a serialized selection, keeping only entries
    * that still resolve. Metadata is re-read, so a view whose FILTER
    * changed since last time is re-classified rather than trusted.
    */
   restore( savedList )
   {
      var saved = Util.deserializeEntries( savedList );
      this.missing = 0;
      for ( var i = 0; i < saved.length; ++i )
      {
         if ( saved[i].source == "view" )
         {
            // View.viewById returns null (not a null-View object) when the
            // id does not exist -- unlike ImageWindow.windowById.
            var v = View.viewById( saved[i].ref );
            if ( v == null || v.isNull )
            {
               // DROP it. A remembered view that is no longer open cannot be
               // used and cannot be fixed from this dialog, so listing it
               // just puts unusable rows above the real ones. The count is
               // still reported in the status line, so the restore is not
               // silent -- an empty list stays empty.
               this.missing++;
               continue;
            }
            this.addView( v );
         }
         else
         {
            if ( !File.exists( saved[i].ref ) )
            {
               // Same reasoning as the view case above: a path that no longer
               // exists is reported in the status line, not listed as a row.
               this.missing++;
               continue;
            }
            this.addFiles( [ saved[i].ref ] );
         }
      }
      this.rebuild();
   }

   /*
    * Marks the dialog busy while a scan measures masters, and says what it
    * is doing.
    *
    * processEvents() is the whole point. A script owns PixInsight's main
    * thread, so setting status.text during a minutes-long loop assigns the
    * string and never paints it: the dialog shows the message it had when
    * the scan started and looks like it has stopped responding.
    *
    * Run is disabled along with the list buttons because a scan leaves the
    * list half built, and a run started over it silently uses whichever
    * masters happened to be in place.
    *
    * A null message means no longer busy, and deliberately leaves
    * status.text alone -- the caller replaces it with its own summary, and
    * blanking it here would flash the label empty in between.
    *
    * `progress` places the bar (Util.stepProgress); without one it pulses.
    */
   setBusy( message, progress )
   {
      var busy = message != null;
      this.busy = busy;
      var buttons = [ this.addMastersButton, this.addFilesButton,
                      this.removeButton, this.clearButton ];
      for ( var i = 0; i < buttons.length; ++i )
         if ( buttons[i] != null )
            buttons[i].enabled = !busy;
      /*
       * Run is not simply the inverse of busy: an empty list is also
       * nothing to run. Deciding it in one place stops the two rules from
       * fighting -- un-busying after a scan that added nothing must not
       * re-enable Run.
       */
      this.updateRunEnabled();

      /*
       * Underscores, and from a header -- the exception to this engine's
       * dot-form constants. There is no StdCursor object at all: these are
       * preprocessor macros in pjsr/StdCursor.jsh, which the entry points
       * include. Probed, because the dot form was the assumption and it
       * throws through the Cursor( Bitmap ) overload rather than saying so.
       */
      this.cursor = new Cursor( busy ? StdCursor_Wait : StdCursor_Arrow );

      if ( busy )
         this.status.text = "<b>" + message + "</b>";
      this.showProgress( busy ? ( progress || Util.stepProgress() ) : null );
      CoreApplication.processEvents();
   }

   /* The bar at `p` ({ fraction, span }), or hidden and still for null. */
   showProgress( p )
   {
      if ( this.progress == null )
         return;
      this.progress.visible = ( p != null );
      if ( p != null )
         this.progress.set( p.fraction, "", p.span );
      else
         this.progress.set( 0, "" );
   }

   /*
    * Run is enabled only when there is something to run and nothing in
    * progress. Pressing it with an empty list produced a validation box
    * saying what the greyed-out button now says by itself.
    */
   updateRunEnabled()
   {
      if ( this.runButton == null )
         return;
      var n = Util.runnableEntryCount( this.entries );
      this.runButton.enabled = !this.busy && n > 0;
      this.runButton.toolTip = ( n > 0 )
         ? "<p>Process the masters listed above.</p>"
         : "<p>Add at least one master first \u2014 " +
           "<b>Scan Masters Folder</b> or <b>Add Files</b>.</p>";
   }

   /* Reads FILTER/INSTRUME for a path and appends an entry. */
   /*
    * Scans a folder of WBPP masters and adds the best one per filter.
    *
    * Selection is Util.selectMasters: variant first (drizzled+autocropped >
    * drizzled > autocropped > plain), most recent within a variant. See the
    * comment there for why variant outranks recency.
    *
    * The channel comes from the FILTER keyword, never the filename -- a
    * master folder is full of names that merely look like filters. Drizzle
    * likewise comes from XPIXSZ. Autocrop is the one thing not recorded in
    * metadata, so it is read from the name.
    *
    * Calibration masters are skipped by name BEFORE opening them, because a
    * real folder holds dozens of flats and darks and reading every header
    * just to discard it is the difference between instant and slow.
    */
   addMastersFolder( dir )
   {
      if ( dir == null || dir.length == 0 )
         return;

      /*
       * Two phases, and the split is the whole point of the performance.
       *
       * Phase 1 ranks candidates from their FILENAMES alone -- no file is
       * opened. A WBPP master folder holds ~114 files; opening every one to
       * read a header, even a header-only read, is wasted work when the name
       * already states the filter and variant.
       *
       * Phase 2 opens ONLY the winners (at most one per channel) to confirm
       * the filter against the FILTER keyword, which remains authoritative.
       * If a header disagrees with its filename the header wins -- the name
       * was a hint that got us to the right file quickly, nothing more.
       */
      var scan = this.scanMasterFolder( dir );
      if ( scan == null )
      {
         this.status.text = "<b>Nothing readable in that folder.</b>";
         return;
      }

      var picks = Util.selectMasters( scan.named );
      // Only fall back to opening unnamed files when a channel is missing.
      if ( Object.keys( picks ).length == 0 && scan.unnamed.length > 0 )
         picks = this.resolveUnnamedMasters( scan );

      var confirm = this.confirmMasters( picks, scan.named );
      var added = this.adoptMasters( confirm.confirmed );

      this.rebuild();
      this.reportScan( scan, confirm, added );
   }

   /*
    * Phase 1: rank every candidate from its filename alone. Returns null
    * when the folder holds nothing readable at all.
    */
   scanMasterFolder( dir )
   {
      var scan = { named: [], unnamed: [], total: 0, skipped: 0 };
      if ( !File.directoryExists( dir ) )
         return null;
      // Util.findEntries: hidden files (macOS's "._name" twins) are never candidates
      Util.findEntries( dir + "/*" ).forEach( function( e ) { UI.classifyMasterFile( scan, dir, e ); } );

      return scan;
   }

   /*
    * Files whose names carry no FILTER token are deferred: they are only
    * opened if some channel ended up with no candidate at all, so an
    * oddly-named master is still found without paying for it every time.
    * Promotes whatever it can identify into `scan.named` and re-picks.
    */
   resolveUnnamedMasters( scan )
   {
      for ( var u = 0; u < scan.unnamed.length; ++u )
      {
         var rec = scan.unnamed[u];
         var ui = null;
         try { ui = Util.readImageInfo( rec.path ); } catch ( e ) { ui = null; }
         if ( ui == null ) continue;
         var uc = Util.channelFromFilter( Util.keywordValue( ui.keywords, "FILTER" ) );
         if ( uc == null ) continue;
         rec.channel  = uc;
         rec.drizzle  = Util.drizzleLabel( Util.keywordValue( ui.keywords, "XPIXSZ" ) );
         rec.autocrop = Util.isAutocropName( rec.name );
         scan.named.push( rec );
      }
      return Util.selectMasters( scan.named );
   }

   /*
    * Phase 2: open the winners only, and confirm each against its FILTER
    * keyword. The header is authoritative; a disagreement is reported.
    */
   confirmMasters( picks, named )
   {
      var confirmed = {}, opened = 0, skipped = 0, corrected = [];
      var keys = Object.keys( picks );
      /*
       * The candidate list is known before any measuring starts, so the
       * wait can be counted out rather than merely spun. The finally is
       * what keeps a failed read from leaving the dialog permanently
       * disabled.
       */
      this.setBusy( Util.scanProgressMessage( "Measuring masters", null, null, null ),
                    Util.stepProgress( 1, keys.length, 0, 1 ) );
      try
      {
         for ( var i = 0; i < keys.length; ++i )
         {
            var p = picks[keys[i]];
            var info = null;
            try { info = Util.readImageInfo( p.path ); } catch ( e ) { info = null; }
            ++opened;
            if ( info == null )
               continue;

            var kws = info.keywords;
            if ( !Util.isMasterLight( p.name, Util.keywordValue( kws, "IMAGETYP" ) ) )
            {
               ++skipped;
               continue;
            }

            var filter = Util.keywordValue( kws, "FILTER" );
            var channel = Util.channelFromFilter( filter );
            if ( channel == null )
               continue;
            if ( channel != p.channel )
               corrected.push( p.name + ": name says " + p.channel +
                               ", header says " + channel );

            var entry = this.measureMaster( p, info, channel, filter, named,
                                            i + 1, keys.length );

            // header wins; if two names collapse onto one real channel, rank decides
            var prev = confirmed[channel];
            if ( prev == null ||
                 Util.masterVariantRank( entry.drizzle, entry.autocrop ) >
                 Util.masterVariantRank( prev.drizzle, prev.autocrop ) )
            {
               confirmed[channel] = entry;
               this.showMaster( entry );
            }
         }
      }
      finally
      {
         this.setBusy( null );
      }

      return { confirmed: confirmed, opened: opened,
               skipped: skipped, corrected: corrected };
   }

   /*
    * Build one confirmed master's entry, including its measured quality and
    * the delta against the stack this file displaces.
    */
   measureMaster( p, info, channel, filter, named, index, count )
   {
      // one step per SubframeSelector call: this stack, and the one it displaces
      var others = Util.sameChannelAlternatives( named, p, 1 );
      var steps = others.length ? 2 : 1;
      this.setBusy( Util.scanProgressMessage( "Measuring masters", channel,
                                              index, count ),
                    Util.stepProgress( index, count, 0, steps ) );
      var entry = {
         source: "file",
         ref: p.path,
         label: File.extractName( p.path ) + File.extractExtension( p.path ),
         filter: filter,
         instrume: Util.keywordValue( info.keywords, "INSTRUME" ),
         channel: channel,
         width: info.width,
         height: info.height,
         drizzle: Util.drizzleLabel( Util.keywordValue( info.keywords, "XPIXSZ" ) ),
         autocrop: p.autocrop,
         mtime: p.mtime,
         created: p.created,
         /*
          * Measured here, while the rejected variants of this channel
          * are still known -- the comparison is against the stack this
          * one displaced, and nothing downstream remembers there was
          * one. Cached per file, so a folder is slow once.
          */
         quality: Steps.measureMasterFWHM( p.path ),
         delta: null
      };

      if ( others.length == 0 )
         return entry;

      // Its own message: this is a second full measurement, so the
      // line would otherwise sit unchanged for twice as long as the
      // count implies.
      this.setBusy( Util.scanProgressMessage( "Measuring masters",
                       channel + " vs the previous stack", index, count ),
                    Util.stepProgress( index, count, 1, steps ) );
      var prev = Steps.measureMasterFWHM( others[0].path );
      entry.delta = Util.qualityDelta( entry.quality, prev );
      if ( entry.delta != null )
         Util.log( "quality", p.channel + ": FWHM " +
            ( entry.quality ? entry.quality.fwhm.toFixed( 2 ) : "?" ) +
            " px (" + ( Util.formatDelta( entry.delta.fwhm ) || "no change" ) +
            " vs " + File.extractName( others[0].path ) + ")" );
      return entry;
   }

   /*
    * A master in the table as soon as it is measured, rather than all of
    * them at the end of a two-minute scan. adoptMasters takes the whole
    * set again afterwards, which changes nothing for the ones shown here.
    */
   showMaster( entry )
   {
      var one = {};
      one[entry.channel] = entry;
      this.adoptMasters( one );
      this.rebuild();
   }

   /*
    * Take the confirmed masters into the entry list, one per channel,
    * displacing whatever held that channel before.
    */
   adoptMasters( confirmed )
   {
      var added = [], ckeys = Object.keys( confirmed );
      for ( var c = 0; c < ckeys.length; ++c )
      {
         var pick = confirmed[ckeys[c]];
         var keep = [];
         for ( var k = 0; k < this.entries.length; ++k )
            if ( this.entries[k].channel != pick.channel )
               keep.push( this.entries[k] );
         this.entries = keep;
         this.entries.push( pick );
         added.push( ckeys[c] + " (" + ( pick.drizzle ? pick.drizzle + " " : "" ) +
                     ( pick.autocrop ? "autocrop" : "full" ) + ")" );
      }
      return added;
   }

   reportScan( scan, confirm, added )
   {
      var detail = "<i>(" + scan.total + " files, " + scan.named.length +
                   " named candidates, " + confirm.opened + " opened, " +
                   ( scan.skipped + confirm.skipped ) + " skipped)</i>";
      this.status.text = added.length
         ? ( "<b>Loaded " + added.length + " master" + ( added.length == 1 ? "" : "s" ) +
             ":</b> " + added.sort().join( ", " ) + "  " + detail +
             ( confirm.corrected.length ? "<br/><b>Filter from header, not name:</b> " +
                                          confirm.corrected.join( "; " ) : "" ) )
         : ( "<b>No masters with a readable FILTER keyword in that folder.</b>  " + detail );
   }

   addFiles( paths )
   {
      /*
       * A single file is not a wait, and restore() adds them one at a time
       * while the dialog is still being built -- pumping events there would
       * paint a half-constructed window.
       */
      var report = paths.length > 1;
      try
      {
         for ( var i = 0; i < paths.length; ++i )
         {
            if ( report )
               this.setBusy( Util.scanProgressMessage( "Reading masters", UI.fileLabel( paths[i] ),
                                                       i+1, paths.length ),
                             Util.stepProgress( i+1, paths.length, 0, 1 ) );
            this.entries.push( UI.fileEntry( paths[i] ) );
            // each row as it is read, not all of them at the end
            if ( report )
               this.rebuild();
         }
      }
      finally
      {
         if ( report )
            this.setBusy( null );
      }
      this.rebuild();
   }

   /*
    * Adds one view, as dropped. This does NOT require
    * a FILTER keyword: the user dropped this deliberately, so an
    * unreadable filter is shown as a problem rather than silently ignored.
    */
   addView( view )
   {
      if ( view == null || view.isNull )
         return;
      for ( var j = 0; j < this.entries.length; ++j )
         if ( this.entries[j].source == "view" && this.entries[j].ref == view.id )
            return;   // already listed

      var kws = view.window.keywords;
      var filter = Util.keywordValue( kws, "FILTER" );
      this.entries.push( {
         source: "view",
         ref: view.id,
         label: view.id,
         filter: filter,
         instrume: Util.keywordValue( kws, "INSTRUME" ),
         channel: Util.channelFromFilter( filter ),
         width: view.image.width,
         height: view.image.height,
         drizzle: Util.drizzleLabel( Util.keywordValue( kws, "XPIXSZ" ) )
      } );
      this.rebuild();
   }

   /*
    * The Colour and L strengths the chosen tool offers with the Stretch
    * setting as it is (Steps.noiseLevelsFor: Prism 2.0 has no Low, and no
    * High without a stretch). A strength the tool does not offer becomes
    * Medium, in the configuration too. Refilled when either changes.
    */
   fillNoiseLevels()
   {
      var self = this, c = this.config, stretch = !!c.stretch;
      var levels = Steps.noiseLevelsFor( c.noiseTool, stretch );
      c.noiseLevel = Steps.supportedNoiseLevel( c.noiseTool, c.noiseLevel || "medium", stretch );
      if ( c.noiseLevelL )
         c.noiseLevelL = Steps.supportedNoiseLevel( c.noiseTool, c.noiseLevelL, stretch );
      this.noiseLevelCombo.clear();
      UI.fillLevelCombo( this.noiseLevelCombo, levels, c.noiseLevel,
                         function( level ) { self.config.noiseLevel = level; } );
      this.noiseLevelLCombo.clear();
      UI.fillLevelCombo( this.noiseLevelLCombo, levels, c.noiseLevelL || c.noiseLevel,
                         function( level ) { self.config.noiseLevelL = level; } );
   }

   /* The amount means nothing without a noise tool selected. */
   updateNoiseEnabled()
   {
      var on = Steps.toolChosen( this.config.noiseTool );
      this.noiseLevelCombo.enabled = on;
      this.noiseLevelLabel.enabled = on;
      this.noiseLevelLCombo.enabled = on;
      this.noiseLevelLLabel.enabled = on;
   }

   /*
    * "Also on H, S and O" means nothing without a gradient tool, and the
    * smoothing is GraXpert's alone.
    */
   updateGradientEnabled()
   {
      var tool = Steps.gradientToolOf( this.config );
      this.graxpertNarrowband.enabled = ( tool != Steps.GRADIENT_TOOL_NONE );
      this.smoothing.enabled = ( tool == Steps.GRADIENT_TOOL_GRAXPERT );
   }

   /* The level combos mean nothing without a tool selected. */
   updateSharpenEnabled()
   {
      // !! matters: PJSR's Control.enabled rejects a non-Boolean, and
      // `config.sharpenTool && ...` yields the string itself when falsy.
      var on = Steps.toolChosen( this.config.sharpenTool );
      this.starReductionCombo.enabled = on;
      this.detailCombo.enabled = on;
   }

   /* Shows the cache's current size and entry count. */
   updateClearCacheLabel()
   {
      var bytes = 0;
      var entries = 0;
      try { bytes = Cache.totalBytes(); entries = Cache.entryCount(); }
      catch ( e ) { bytes = 0; entries = 0; }
      this.cacheInfo.text = ( entries == 0 )
         ? "Cache: empty"
         : "Cache: " + Cache.formatBytes( bytes ) + " in " + entries +
           " entr" + ( entries == 1 ? "y" : "ies" );
      // The folder can change while the dialog is open, so the tooltip is
      // rebuilt here rather than fixed at construction.
      this.cacheInfo.toolTip = "Cached stage results live in " + Cache.dir();
   }

   /*
    * Deletes every cached stage result.
    *
    * No confirmation and no report: the label beside the button already
    * shows the size, and it drops to "empty" the moment this returns --
    * which says everything a dialog would have, without a click.
    */
   clearCache()
   {
      try { Cache.clear(); } catch ( e ) {}
      this.updateClearCacheLabel();
   }

   removeSelected()
   {
      var keep = [];
      for ( var i = 0; i < this.entries.length; ++i )
      {
         var node = ( i < this.tree.numberOfChildren ) ? this.tree.child( i ) : null;
         if ( !node || !node.selected )
            keep.push( this.entries[i] );
      }
      this.entries = keep;
      this.rebuild();
   }

   /* Adds every open view that carries a FILTER keyword, skipping duplicates. */

   /*
    * Palettes only make sense when narrowband data is present, and the
    * list changes as entries are added and removed -- so this is called
    * from rebuild(), not just at construction.
    */
   updatePaletteVisibility()
   {
      var hasNarrowband = false;
      for ( var i = 0; i < this.entries.length; ++i )
      {
         var e = this.entries[i];
         if ( !e.unavailable && e.channel != null &&
              Util.NARROWBAND.indexOf( e.channel ) >= 0 )
         {
            hasNarrowband = true;
            break;
         }
      }
      this.paletteGroup.visible = hasNarrowband;
      // The bandwidth only matters when a palette can be built, so it
      // follows the palette controls rather than sitting there unexplained.
      this.nbBandwidthLabel.visible = hasNarrowband;
      this.nbBandwidth.visible = hasNarrowband;
      this.nbNormalize.visible = hasNarrowband;
      // returned so the decision can be asserted without showing the
      // dialog: Control.visible reports EFFECTIVE visibility, which is
      // false for any child while the dialog itself is not on screen.
      return hasNarrowband;
   }

   /* Repaints the list and the status line. */
   rebuild()
   {
      // the derived project name follows the list, until it is typed in
      try { this.updateProjectName(); } catch ( e ) {}
      // every path that changes the list ends here, so this is the one
      // place Run's state has to be refreshed
      try { this.updateRunEnabled(); } catch ( e ) {}
      this.tree.clear();

      this.updateCamera();

      var counts = {};
      for ( var i = 0; i < this.entries.length; ++i )
      {
         var e = this.entries[i];
         var node = new TreeBoxNode( this.tree );
         node.setText( 0, UI.filterCellText( e ) );
         node.setText( 1, ( e.width && e.height ) ? ( e.width + " x " + e.height ) : "" );
         node.setText( 2, e.drizzle || "" );
         node.setText( 3, ( e.source == "view" ? "view: " : "" ) + e.label );
         node.setText( 4, Util.formatFileTime( e.created ) );
         var cells = UI.qualityCells( e.quality, e.delta );
         for ( var c = 0; c < cells.length; ++c )
            node.setText( 5 + c, cells[c] );
         UI.colourRow( node, e, counts );
      }
      // a scan's progress line stays until the scan writes its own summary
      if ( !this.busy )
         this.updateStatus( counts );
      this.updatePaletteVisibility();
   }

   /*
    * One session, one camera -- so a master whose header lost INSTRUME
    * (WBPP's autocrop rewrites it away) is shown with the camera its
    * siblings name, in parentheses to say it was inferred rather than
    * read. Blank here used to be the only sign of a channel that would
    * later be calibrated against the ideal QE curve instead of the real
    * one.
    */
   updateCamera()
   {
      var known = [];
      for ( var ki = 0; ki < this.entries.length; ++ki )
         known.push( this.entries[ki].instrume );
      var qe = null;
      try
      {
         var cam = Util.commonInstrument( known );
         qe = cam ? Steps.deviceCurveForImage( cam ) : null;
      }
      catch ( eq ) { qe = null; }
      this.camera.text = UI.cameraSummary( known, qe ? qe.name : null );
   }

   updateStatus( counts )
   {
      var dupes = [], unknown = 0;
      for ( var i = 0; i < this.entries.length; ++i )
         if ( !this.entries[i].unavailable && this.entries[i].channel === null )
            unknown++;
      for ( var k = 0; k < Util.CHANNELS.length; ++k )
         if ( counts[Util.CHANNELS[k]] > 1 )
            dupes.push( Util.CHANNELS[k] );
      /*
       * The channel list is not restated here: the table above already shows
       * one row per channel, so repeating it is noise. Only PROBLEMS are
       * reported -- duplicates, unrecognised filters, entries that could not
       * be restored -- plus whatever the last action wrote.
       */
      var msg = "";
      if ( dupes.length )
         msg += "<span style='color:#ff5555'>duplicate: " + dupes.join( " " ) + "</span>  ";
      if ( unknown )
         msg += "<span style='color:#ff5555'>" + unknown +
                " unrecognised FILTER</span>  ";
      this.status.text = msg + UI.hiddenEntriesNote( this.missing, this.skipped );
   }

   /*
    * Folds the list into config.paths / config.views. Refuses on
    * duplicates or unrecognised filters rather than silently dropping an
    * image the user deliberately added.
    */
   commit()
   {
      /*
       * Taken from the control, not from whatever onEditCompleted last
       * stored: a field typed into and then committed with the Run button
       * never fires that handler.
       */
      try { this.config.projectName = this.projectEdit.text.trim(); }
      catch ( e ) {}
      var folded = UI.foldEntries( this.entries );
      var paths = folded.paths, views = folded.views, problems = folded.problems;

      if ( problems.length )
      {
         new MessageBox( problems.join( "\n" ), "Loom", StdIcon_Error, StdButton_Ok ).execute();
         return false;
      }

      this.config.paths = paths;
      this.config.views = views;
      this.config.savedList = Util.serializeEntries( this.entries );
      return true;
   }
};
