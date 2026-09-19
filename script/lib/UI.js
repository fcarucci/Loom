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
      this.cancelButton.onClick = function()
      {
         self.cancelled = true;
         self.stageLabel.text = "cancelling at the next checkpoint...";
         self.cancelButton.enabled = false;
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

   var self = this;
   this.config = config;
   if ( !this.config.views )
      this.config.views = {};

   // entries: { source:"file"|"view", ref:<path or view id>, filter, channel, instrume }
   this.entries = [];

   /*
    * The title carries the version AND the commit: the updater installs
    * branch commits, and many commits share one Util.VERSION, so the
    * number alone does not say which code is running.
    */
   this.windowTitle = Update.describeVersion( Update.installDir(), Update.io );

   /*
    * The project name. Names the PSB, and is the one thing in this dialog
    * that identifies the image rather than the processing.
    *
    * Defaulted from where the masters live, because PJSR exposes nothing
    * about the open PixInsight project -- no name, no path, no title. The
    * default follows the file list until the moment it is typed in, after
    * which it is left alone.
    */
   this.projectLabel = new Label( this );
   this.projectLabel.text = "Project:";
   this.projectLabel.textAlignment = TextAlign_Right | TextAlign_VertCenter;

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

   var projectRow = new HorizontalSizer;
   projectRow.spacing = 6;
   projectRow.add( this.projectLabel );
   projectRow.add( this.projectEdit, 100 );

   this.info = new Label( this );
   this.info.useRichText = true;
   this.info.text = "<b>Add your masters below.</b> The channel is taken from " +
                    "each image's FITS <i>FILTER</i> keyword, not its filename.<br>" +
                    "Use <b>Add Open Views</b> or <b>Add Files</b>. Nothing is added automatically.<br>" +
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
   this.tree.numberOfColumns = 5;
   this.tree.setHeaderText( 0, "Filter" );
   this.tree.setHeaderText( 1, "Size" );
   this.tree.setHeaderText( 2, "Drizzle" );
   this.tree.setHeaderText( 3, "Source" );
   // When a folder holds several stacks of the same target, the creation
   // time is what tells them apart -- the names differ only by "(3)".
   this.tree.setHeaderText( 4, "Created" );
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
      var d = new GetDirectoryDialog;
      d.caption = "Select a masters folder";
      if ( d.execute() )
         self.addMastersFolder( d.directory );
   };

   this.addViewsButton = new PushButton( this );
   this.addViewsButton.text = "Add Open Views";
   this.addViewsButton.toolTip = "Add every open view that has a FILTER keyword.";
   this.addViewsButton.onClick = function() { self.addOpenViews(); };

   this.removeButton = new PushButton( this );
   this.removeButton.text = "Remove";
   this.removeButton.onClick = function() { self.removeSelected(); };

   this.clearButton = new PushButton( this );
   this.clearButton.text = "Clear";
   this.clearButton.onClick = function() { self.entries = []; self.rebuild(); };

   var listButtons = new HorizontalSizer;
   listButtons.spacing = 6;
   listButtons.add( this.addFilesButton );
   listButtons.add( this.addMastersButton );
   listButtons.add( this.addViewsButton );
   listButtons.addStretch();
   listButtons.add( this.removeButton );
   listButtons.add( this.clearButton );

   this.status = new Label( this );
   this.status.useRichText = true;
   this.status.wordWrapping = true;

   /*
    * The session's camera, and the QE curve it resolves to. This is the
    * number that actually matters: an unrecognised or absent camera means
    * SPFC calibrates against the ideal curve rather than the real device
    * response, and until now the only sign of that was a blank cell.
    */
   this.camera = new Label( this );
   this.camera.useRichText = true;
   this.camera.wordWrapping = true;

   // ---- options ----

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
      var flabel = new Label( this );
      flabel.text = fk + " filter:";
      flabel.minWidth = 100;
      flabel.textAlignment = TextAlign_Right | TextAlign_VertCenter;

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

      var frow = new HorizontalSizer;
      frow.spacing = 4;
      frow.add( flabel );
      frow.add( fcombo );
      frow.addStretch();
      filterRows.add( frow );
   }

   this.filterHeading = new Label( this );
   this.filterHeading.useRichText = true;
   this.filterHeading.text = "<b>Filters</b>";

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

   this.sharpenToolLabel = new Label( this.sharpenGroup );
   this.sharpenToolLabel.text = "Sharpening:";
   this.sharpenToolLabel.minWidth = 100;
   this.sharpenToolLabel.textAlignment = TextAlign_Right | TextAlign_VertCenter;

   this.sharpenToolCombo = new ComboBox( this.sharpenGroup );
   this.sharpenToolCombo.addItem( "None" );
   for ( var ti = 0; ti < tools.length; ++ti )
      this.sharpenToolCombo.addItem( tools[ti] );
   this.sharpenToolCombo.currentItem = 0;
   for ( var tj = 0; tj < tools.length; ++tj )
      if ( tools[tj] == config.sharpenTool )
         this.sharpenToolCombo.currentItem = tj + 1;
   this.sharpenToolCombo.enabled = tools.length > 0;
   this.sharpenToolCombo.toolTip = tools.length
      ? "Aberration correction always runs when a tool is selected."
      : "No sharpening tool installed (BlurXTerminator or SyQon Parallax).";
   this.sharpenToolCombo.onItemSelected = function( i )
   {
      self.config.sharpenTool = ( i == 0 ) ? "none" : self.sharpenToolCombo.itemText( i );
      self.updateSharpenEnabled();
   };

   this.starReductionLabel = new Label( this.sharpenGroup );
   this.starReductionLabel.text = "Star reduction:";
   this.starReductionLabel.minWidth = 100;
   this.starReductionLabel.textAlignment = TextAlign_Right | TextAlign_VertCenter;

   this.starReductionCombo = new ComboBox( this.sharpenGroup );
   var starLevels = [ "None", "Low", "Medium", "High" ];
   for ( var si = 0; si < starLevels.length; ++si )
      this.starReductionCombo.addItem( starLevels[si] );
   this.starReductionCombo.currentItem =
      Math.max( 0, [ "none", "low", "medium", "high" ].indexOf( config.starReduction || "none" ) );
   this.starReductionCombo.onItemSelected = function( i )
   {
      self.config.starReduction = [ "none", "low", "medium", "high" ][i];
   };

   this.detailLabel = new Label( this.sharpenGroup );
   this.detailLabel.text = "Detail:";
   this.detailLabel.minWidth = 100;
   this.detailLabel.textAlignment = TextAlign_Right | TextAlign_VertCenter;

   this.detailCombo = new ComboBox( this.sharpenGroup );
   var detailLevels = [ "None", "Low", "Medium", "High" ];
   for ( var di = 0; di < detailLevels.length; ++di )
      this.detailCombo.addItem( detailLevels[di] );
   this.detailCombo.currentItem =
      Math.max( 0, [ "none", "low", "medium", "high" ].indexOf( config.detailLevel || "none" ) );
   this.detailCombo.onItemSelected = function( i )
   {
      self.config.detailLevel = [ "none", "low", "medium", "high" ][i];
   };

   var sharpenRow = new HorizontalSizer;
   sharpenRow.spacing = 4;
   sharpenRow.add( this.sharpenToolLabel );
   sharpenRow.add( this.sharpenToolCombo );
   sharpenRow.addSpacing( 12 );
   sharpenRow.add( this.starReductionLabel );
   sharpenRow.add( this.starReductionCombo );
   sharpenRow.addSpacing( 12 );
   sharpenRow.add( this.detailLabel );
   sharpenRow.add( this.detailCombo );
   sharpenRow.addStretch();

   this.sharpenGroup.sizer = sharpenRow;
   if ( tools.length == 0 )
   {
      this.sharpenGroup.visible = false;
      // nothing can run, so make sure nothing is configured to
      this.config.sharpenTool = "none";
      this.config.starReduction = "none";
      this.config.detailLevel = "none";
   }

   /*
    * Hideable: palettes are meaningless without narrowband data, and the
    * check is dynamic because the list changes as entries are added and
    * removed. rebuild() calls updatePaletteVisibility().
    */
   this.paletteGroup = new Control( this );

   this.paletteLabel = new Label( this.paletteGroup );
   this.paletteLabel.text = "Narrowband:";
   this.paletteLabel.minWidth = 100;
   this.paletteLabel.textAlignment = TextAlign_Right | TextAlign_VertCenter;

   /*
    * Checkboxes, not a combo: more than one palette can be produced from
    * the same narrowband data in a single run, and comparing them
    * side by side is the usual reason to build any of them.
    */
   this.paletteChecks = {};
   var paletteRow = new HorizontalSizer;
   paletteRow.spacing = 4;
   paletteRow.add( this.paletteLabel );

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
      paletteRow.add( cb );
      paletteRow.addSpacing( 6 );
   }
   paletteRow.addStretch();
   this.paletteGroup.sizer = paletteRow;

   /*
    * Noise reduction on the finished composites. Only tools this
    * installation can actually run are offered, and the whole row is hidden
    * when there are none -- the same rule the sharpening controls follow.
    */
   this.noiseGroup = new Control( this );

   this.noiseLabel = new Label( this.noiseGroup );
   this.noiseLabel.text = "Noise reduction:";
   this.noiseLabel.textAlignment = TextAlign_Right | TextAlign_VertCenter;

   this.noiseCombo = new ComboBox( this.noiseGroup );
   this.noiseCombo.addItem( "None" );
   var noiseTools = [];
   try { noiseTools = Steps.availableNoiseTools(); } catch ( e ) { noiseTools = []; }
   for ( var nti = 0; nti < noiseTools.length; ++nti )
      this.noiseCombo.addItem( noiseTools[nti] );
   this.noiseCombo.currentItem = 0;
   for ( var ntj = 0; ntj < noiseTools.length; ++ntj )
      if ( noiseTools[ntj] == config.noiseTool )
         this.noiseCombo.currentItem = ntj + 1;
   this.noiseCombo.toolTip =
      "<p>Applied to the finished RGB and any narrowband palette, after " +
      "colour calibration -- never to the individual channels, and never " +
      "to the stars plate.</p>" +
      "<p><b>Where it runs is decided by the tool, not by you.</b> " +
      "NoiseXTerminator and MLDenoise run on the <i>linear</i> starless " +
      "plate, after star extraction and before the stretch, which is what " +
      "their authors ask for: noise reduced before the stretch amplifies " +
      "it. SyQon Prism runs <i>after</i> the stretch, which is the data it " +
      "is built for.</p>" +
      "<p><b>Strength</b> is the same ladder for all three: Medium is the " +
      "tool\'s own default, Low backs off, High pushes past it.</p>";
   this.noiseCombo.onItemSelected = function( i )
   {
      self.config.noiseTool = ( i == 0 ) ? "none" : noiseTools[i-1];
      self.updateNoiseEnabled();
   };

   this.noiseLevelLabel = new Label( this.noiseGroup );
   this.noiseLevelLabel.text = "Strength:";
   this.noiseLevelLabel.textAlignment = TextAlign_Right | TextAlign_VertCenter;

   this.noiseLevelCombo = new ComboBox( this.noiseGroup );
   var nlevels = [ "low", "medium", "high" ];
   for ( var nl2 = 0; nl2 < nlevels.length; ++nl2 )
      this.noiseLevelCombo.addItem( nlevels[nl2].charAt( 0 ).toUpperCase() +
                                    nlevels[nl2].slice( 1 ) );
   this.noiseLevelCombo.currentItem = Math.max( 0, nlevels.indexOf( config.noiseLevel || "medium" ) );
   this.noiseLevelCombo.onItemSelected = function( i ) { self.config.noiseLevel = nlevels[i]; };

   var noiseRow = new HorizontalSizer;
   noiseRow.spacing = 6;
   noiseRow.add( this.noiseLabel );
   noiseRow.add( this.noiseCombo );
   noiseRow.addSpacing( 12 );
   noiseRow.add( this.noiseLevelLabel );
   noiseRow.add( this.noiseLevelCombo );
   noiseRow.addStretch();
   this.noiseGroup.sizer = noiseRow;
   this.noiseGroup.visible = ( noiseTools.length > 0 );

   /*
    * Star extraction. Same rule as the sharpening and noise controls: only
    * tools this installation can run are offered, and the row disappears
    * when there are none.
    */
   this.starGroup = new Control( this );

   this.starLabel = new Label( this.starGroup );
   this.starLabel.text = "Star extraction:";
   this.starLabel.textAlignment = TextAlign_Right | TextAlign_VertCenter;

   this.starCombo = new ComboBox( this.starGroup );
   this.starCombo.addItem( "None" );
   var starTools = [];
   try { starTools = Steps.availableStarTools(); } catch ( e ) { starTools = []; }
   for ( var sti = 0; sti < starTools.length; ++sti )
      this.starCombo.addItem( starTools[sti] );
   this.starCombo.currentItem = 0;
   for ( var stj = 0; stj < starTools.length; ++stj )
      if ( starTools[stj] == config.starTool )
         this.starCombo.currentItem = stj + 1;
   this.starCombo.toolTip =
      "<p>Splits L, the RGB composite and any narrowband palette into a " +
      "starless frame and a stars frame, after sharpening and before noise " +
      "reduction -- so noise reduction never touches the stars.</p>" +
      "<p>The unsplit image is not kept: starless and stars screen back " +
      "together into it exactly. Narrowband stars are kept only when the " +
      "run produced no RGB composite.</p>";
   this.starCombo.onItemSelected = function( i )
   {
      self.config.starTool = ( i == 0 ) ? "none" : starTools[i-1];
   };

   var starRow = new HorizontalSizer;
   starRow.spacing = 6;
   starRow.add( this.starLabel );
   starRow.add( this.starCombo );
   starRow.addStretch();
   this.starGroup.sizer = starRow;
   this.starGroup.visible = ( starTools.length > 0 );

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
   };

   /*
    * A second method, for when the deterministic MTF stretch is not what
    * the image wants. MAS stretches adaptively and restores large-scale
    * contrast afterwards; the MTF stretch stays the default because it is
    * reproducible from three measured numbers and needs nothing set.
    */
   this.stretchMethodLabel = new Label( this );
   this.stretchMethodLabel.text = "Method:";
   this.stretchMethodLabel.textAlignment = TextAlign_Right | TextAlign_VertCenter;

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

   var stretchMethodRow = new HorizontalSizer;
   stretchMethodRow.spacing = 6;
   stretchMethodRow.addSpacing( 20 );
   stretchMethodRow.add( this.stretchMethodLabel );
   stretchMethodRow.add( this.stretchMethodCombo );
   stretchMethodRow.addStretch();

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

   /*
    * Export. Empty folder means no export -- one control, no separate
    * enable checkbox to fall out of step with it.
    */
   this.exportGroup = new Control( this );

   this.exportLabel = new Label( this.exportGroup );
   this.exportLabel.text = "Export 16-bit TIFFs to:";
   this.exportLabel.textAlignment = TextAlign_Right | TextAlign_VertCenter;

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
      var d = new GetDirectoryDialog;
      d.caption = "Select a folder for the exported TIFFs";
      if ( config.exportDir && config.exportDir.length > 0 )
         d.initialPath = config.exportDir;
      if ( d.execute() )
      {
         self.config.exportDir = d.directory;
         self.exportEdit.text = d.directory;
      }
   };

   var exportRow = new HorizontalSizer;
   exportRow.spacing = 6;
   exportRow.add( this.exportLabel );
   exportRow.add( this.exportEdit, 100 );
   exportRow.add( this.exportBrowse );
   this.exportGroup.sizer = exportRow;

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

   this.marsLabel = new Label( this.marsGroup );
   this.marsLabel.text = "MARS database folder:";
   this.marsLabel.textAlignment = TextAlign_Right | TextAlign_VertCenter;

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
      var d = new GetDirectoryDialog;
      d.caption = "Select the folder holding the MARS *.xmars databases";
      if ( config.marsPath && config.marsPath.length > 0 )
         d.initialPath = config.marsPath;
      if ( d.execute() )
      {
         self.config.marsPath = d.directory;
         self.marsEdit.text = d.directory;
         var n = 0;
         try { n = Steps.marsDatabasesInDirectory( d.directory ).length; }
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
            Util.warn( "mgc", "No .xmars files directly inside " + d.directory );
      }
   };

   var marsRow = new HorizontalSizer;
   marsRow.spacing = 6;
   marsRow.add( this.marsLabel );
   marsRow.add( this.marsEdit, 100 );
   marsRow.add( this.marsBrowse );
   this.marsGroup.sizer = marsRow;
   this.marsGroup.visible = !marsKnown;
   if ( marsKnown )
      Util.log( "mgc", "MARS databases known from " + marsSource +
                       "; not asking for a folder" );

   this.nbBandwidthLabel = new Label( this );
   this.nbBandwidthLabel.text = "Narrowband bandwidth (nm):";
   this.nbBandwidthLabel.textAlignment = TextAlign_Right | TextAlign_VertCenter;

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

   var nbRow = new HorizontalSizer;
   nbRow.spacing = 4;
   nbRow.add( this.nbBandwidthLabel );
   nbRow.add( this.nbBandwidth );
   nbRow.addSpacing( 12 );
   nbRow.add( this.nbNormalize );
   nbRow.addStretch();

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

   this.useGraXpert = new CheckBox( this );
   this.useGraXpert.text = "Run GraXpert background extraction";
   this.useGraXpert.checked = !!config.useGraXpert;
   this.useGraXpert.onCheck = function( c )
   {
      self.config.useGraXpert = c;
      self.smoothing.enabled = c;
   };

   this.smoothing = new NumericControl( this );
   this.smoothing.label.text = "GraXpert smoothing:";
   this.smoothing.setRange( 0, 1 );
   this.smoothing.setPrecision( 2 );
   this.smoothing.setValue( config.smoothing );
   this.smoothing.onValueUpdated = function( v ) { self.config.smoothing = v; };
   this.smoothing.enabled = config.useGraXpert;

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
    * Updating is opt-out. Off means nothing is spawned and nothing is
    * reported -- not a quieter updater, no updater.
    */
   this.autoUpdate = new CheckBox( this );
   this.autoUpdate.text = "Update Loom automatically";
   this.autoUpdate.toolTip =
      "<p>Checks for a newer Loom each time this dialog opens, in the " +
      "background. Nothing is waited on: the check runs while you work " +
      "and the new version is used the <i>next</i> time you start Loom.</p>" +
      "<p>A checkout with local changes is never touched, and a failed " +
      "update is reported in the Process console at the next launch.</p>";
   this.autoUpdate.checked = !!config.autoUpdate;
   this.autoUpdate.onCheck = function( c )
   {
      self.config.autoUpdate = c;
   };

   var cacheRow = new HorizontalSizer;
   cacheRow.spacing = 6;
   cacheRow.add( this.useCache );
   cacheRow.add( this.ignoreCache );
   cacheRow.addStretch();
   cacheRow.add( this.autoUpdate );
   cacheRow.add( this.clearCacheButton );
   cacheRow.addSpacing( 8 );
   cacheRow.add( this.cacheInfo );

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

   this.cacheDirLabel = new Label( this.cacheDirGroup );
   this.cacheDirLabel.text = "Cache folder:";
   this.cacheDirLabel.textAlignment = TextAlign_Right | TextAlign_VertCenter;

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
      var d = new GetDirectoryDialog;
      d.caption = "Select a folder for Loom's cache";
      if ( self.config.cacheDir && self.config.cacheDir.length > 0 )
         d.initialPath = self.config.cacheDir;
      if ( d.execute() )
      {
         self.config.cacheDir = d.directory;
         self.cacheDirEdit.text = d.directory;
         Cache.setDir( d.directory );
         self.updateClearCacheLabel();
      }
   };

   var cacheDirRow = new HorizontalSizer;
   cacheDirRow.spacing = 6;
   cacheDirRow.add( this.cacheDirLabel );
   cacheDirRow.add( this.cacheDirEdit, 100 );
   cacheDirRow.add( this.cacheDirBrowse );
   this.cacheDirGroup.sizer = cacheDirRow;

   this.runButton = new PushButton( this );
   this.runButton.text = "Run";
   this.runButton.onClick = function() { if ( self.commit() ) self.ok(); };

   this.cancelButton = new PushButton( this );
   this.cancelButton.text = "Cancel";
   this.cancelButton.onClick = function() { self.cancel(); };

   var buttons = new HorizontalSizer;
   buttons.spacing = 6;
   buttons.addStretch();
   buttons.add( this.runButton );
   buttons.add( this.cancelButton );

   this.sizer = new VerticalSizer;
   this.sizer.margin = 8;
   this.sizer.spacing = 6;
   this.sizer.add( projectRow );
   this.sizer.add( this.info );
   this.sizer.add( this.tree, 100 );
   this.sizer.add( listButtons );
   this.sizer.add( this.status );
   this.sizer.add( this.camera );
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
   this.sizer.add( this.useGraXpert );
   this.sizer.add( this.smoothing );
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
       *
       * Files whose names carry no FILTER token are deferred: they are only
       * opened if some channel ended up with no candidate at all, so an
       * oddly-named master is still found without paying for it every time.
       */
      var named = [], unnamed = [], total = 0, skipped = 0;
      var found = new FileFind;
      if ( !found.begin( dir + "/*" ) )
      {
         this.status.text = "<b>Nothing readable in that folder.</b>";
         return;
      }
      do
      {
         var name = found.name;
         if ( found.isDirectory || name == "." || name == ".." )
            continue;
         if ( !/\.(xisf|fits?|fit)$/i.test( name ) )
            continue;
         ++total;
         if ( /^master(Flat|Dark|Bias)/i.test( name ) )
         {
            ++skipped;
            continue;
         }

         var mtime = 0;
         try { mtime = found.lastModified.getTime(); } catch ( e ) { mtime = 0; }
         var ctime = 0;
         try { ctime = found.created.getTime(); } catch ( e ) { ctime = mtime; }
         var rec = { path: dir + "/" + name, name: name,
                     mtime: mtime, created: ctime };

         var parsed = Util.parseMasterName( name );
         if ( parsed != null && parsed.channel != null )
         {
            rec.channel  = parsed.channel;
            rec.drizzle  = parsed.drizzle;
            rec.autocrop = parsed.autocrop;
            named.push( rec );
         }
         else
            unnamed.push( rec );
      }
      while ( found.next() );

      var picks = Util.selectMasters( named );

      // Only fall back to opening unnamed files when a channel is missing.
      if ( Object.keys( picks ).length == 0 && unnamed.length > 0 )
      {
         for ( var u = 0; u < unnamed.length; ++u )
         {
            var ui = null;
            try { ui = Pipeline.readImageInfo( unnamed[u].path ); } catch ( e ) { ui = null; }
            if ( ui == null ) continue;
            var uf = Util.keywordValue( ui.keywords, "FILTER" );
            var uc = Util.channelFromFilter( uf );
            if ( uc == null ) continue;
            unnamed[u].channel  = uc;
            unnamed[u].drizzle  = Util.drizzleLabel( Util.keywordValue( ui.keywords, "XPIXSZ" ) );
            unnamed[u].autocrop = Util.isAutocropName( unnamed[u].name );
            named.push( unnamed[u] );
         }
         picks = Util.selectMasters( named );
      }

      // Phase 2: confirm the winners only.
      var confirmed = {}, opened = 0, corrected = [];
      var keys = Object.keys( picks );
      for ( var i = 0; i < keys.length; ++i )
      {
         var p = picks[keys[i]];
         var info = null;
         try { info = Pipeline.readImageInfo( p.path ); } catch ( e ) { info = null; }
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
            corrected.push( p.name + ": name says " + p.channel + ", header says " + channel );

         var entry = {
            source: "file",
            ref: p.path,
            label: File.extractName( p.path ) + File.extractExtension( p.path ),
            filter: filter,
            instrume: Util.keywordValue( kws, "INSTRUME" ),
            channel: channel,
            width: info.width,
            height: info.height,
            drizzle: Util.drizzleLabel( Util.keywordValue( kws, "XPIXSZ" ) ),
            autocrop: p.autocrop,
            mtime: p.mtime,
            created: p.created
         };
         // header wins; if two names collapse onto one real channel, rank decides
         var prev = confirmed[channel];
         if ( prev == null ||
              Util.masterVariantRank( entry.drizzle, entry.autocrop ) >
              Util.masterVariantRank( prev.drizzle, prev.autocrop ) )
            confirmed[channel] = entry;
      }

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

      this.rebuild();
      var detail = "<i>(" + total + " files, " + named.length + " named candidates, " +
                   opened + " opened, " + skipped + " skipped)</i>";
      this.status.text = added.length
         ? ( "<b>Loaded " + added.length + " master" + ( added.length == 1 ? "" : "s" ) +
             ":</b> " + added.sort().join( ", " ) + "  " + detail +
             ( corrected.length ? "<br/><b>Filter from header, not name:</b> " +
                                  corrected.join( "; " ) : "" ) )
         : ( "<b>No masters with a readable FILTER keyword in that folder.</b>  " + detail );
   }

   addFiles( paths )
   {
      for ( var i = 0; i < paths.length; ++i )
      {
         var info = null;
         try { info = Pipeline.readImageInfo( paths[i] ); } catch ( e ) { info = null; }
         var kws = info ? info.keywords : null;
         var filter = kws ? Util.keywordValue( kws, "FILTER" ) : null;
         this.entries.push( {
            source: "file",
            ref: paths[i],
            label: File.extractName( paths[i] ) + File.extractExtension( paths[i] ),
            filter: filter,
            instrume: kws ? Util.keywordValue( kws, "INSTRUME" ) : null,
            channel: Util.channelFromFilter( filter ),
            width: info ? info.width : 0,
            height: info ? info.height : 0,
            drizzle: kws ? Util.drizzleLabel( Util.keywordValue( kws, "XPIXSZ" ) ) : "",
            created: Util.fileCreatedMs( paths[i] )
         } );
      }
      this.rebuild();
   }

   /*
    * Adds one view, as dropped. Unlike addOpenViews this does NOT require
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

   /* The amount means nothing without a noise tool selected. */
   updateNoiseEnabled()
   {
      var on = !!( this.config.noiseTool && this.config.noiseTool != "none" );
      this.noiseLevelCombo.enabled = on;
      this.noiseLevelLabel.enabled = on;
   }

   /* The level combos mean nothing without a tool selected. */
   updateSharpenEnabled()
   {
      // !! matters: PJSR's Control.enabled rejects a non-Boolean, and
      // `config.sharpenTool && ...` yields the string itself when falsy.
      var on = !!( this.config.sharpenTool && this.config.sharpenTool != "none" );
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
   addOpenViews()
   {
      var wins = ImageWindow.windows;
      this.skipped = 0;
      for ( var i = 0; i < wins.length; ++i )
      {
         var v = wins[i].mainView;
         var dup = false;
         for ( var j = 0; j < this.entries.length; ++j )
            if ( this.entries[j].source == "view" && this.entries[j].ref == v.id )
               dup = true;
         if ( dup )
            continue;
         var kws = wins[i].keywords;
         var filter = Util.keywordValue( kws, "FILTER" );
         if ( filter === null )
         {
            this.skipped++;
            continue;
         }
         this.entries.push( {
            source: "view",
            ref: v.id,
            label: v.id,
            filter: filter,
            instrume: Util.keywordValue( kws, "INSTRUME" ),
            channel: Util.channelFromFilter( filter ),
            width: v.image.width,
            height: v.image.height,
            drizzle: Util.drizzleLabel( Util.keywordValue( kws, "XPIXSZ" ) )
         } );
      }
      this.rebuild();
   }

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
      this.tree.clear();

      /*
       * One session, one camera -- so a master whose header lost INSTRUME
       * (WBPP's autocrop rewrites it away) is shown with the camera its
       * siblings name, in parentheses to say it was inferred rather than
       * read. Blank here used to be the only sign of a channel that would
       * later be calibrated against the ideal QE curve instead of the real
       * one.
       */
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

      var counts = {};
      for ( var i = 0; i < this.entries.length; ++i )
      {
         var e = this.entries[i];
         var node = new TreeBoxNode( this.tree );
         /*
          * One Filter column. Where the filter name is not itself the
          * channel letter -- "Baader R" mapping to R -- the channel is
          * appended, so nothing is lost; for plain "R" it would just repeat
          * itself and is omitted.
          */
         var filterText = e.unavailable ? "(not open)"
                        : ( e.filter !== null ? e.filter : "(no FILTER)" );
         if ( !e.unavailable && e.channel !== null && e.filter !== null &&
              String( e.filter ).trim() != e.channel )
            filterText += "  (" + e.channel + ")";
         node.setText( 0, filterText );
         node.setText( 1, ( e.width && e.height ) ? ( e.width + " x " + e.height ) : "" );
         node.setText( 2, e.drizzle || "" );
         node.setText( 3, ( e.source == "view" ? "view: " : "" ) + e.label );
         node.setText( 4, Util.formatFileTime( e.created ) );


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
      }
      this.updateStatus( counts );
      this.updatePaletteVisibility();
   }

   updateStatus( counts )
   {
      var have = [], dupes = [], unknown = 0;
      for ( var i = 0; i < this.entries.length; ++i )
         if ( !this.entries[i].unavailable && this.entries[i].channel === null )
            unknown++;
      for ( var k = 0; k < Util.CHANNELS.length; ++k )
      {
         var key = Util.CHANNELS[k];
         if ( counts[key] )
            have.push( key );
         if ( counts[key] > 1 )
            dupes.push( key );
      }
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
      if ( this.missing )
         msg += "  <span style='color:#888888'>(" + this.missing +
                " remembered entr" + ( this.missing == 1 ? "y" : "ies" ) +
                " unavailable and not listed; the list is still saved, so " +
                "reopening the project and relaunching Loom will restore " +
                ( this.missing == 1 ? "it" : "them" ) + ")</span>";
      if ( this.skipped )
         msg += "  <span style='color:#888888'>(" + this.skipped +
                " view" + ( this.skipped == 1 ? "" : "s" ) + " without FILTER hidden)</span>";
      this.status.text = msg;
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
      var paths = {}, views = {}, seen = {}, problems = [];
      for ( var k = 0; k < Util.CHANNELS.length; ++k )
      {
         paths[Util.CHANNELS[k]] = "";
      }
      for ( var i = 0; i < this.entries.length; ++i )
      {
         var e = this.entries[i];
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
