/*
 * Steps, continued: the SyQon tools. Finding the CLI binaries, the
 * Parallax CLI round trip with its temporary stretch and the process
 * runner and progress parser every SyQon run shares, Prism's CLI and its
 * pre-stretch target, the Starless CLI, and SyQon Studio (entitlement,
 * discovery, arguments, runs).
 *
 * Not a namespace of its own: it adds to `Steps`, so it is included right
 * after Steps.js, and Steps.js's dispatchers reach these members at call
 * time. The section texts are the ones Steps.js carried, unchanged.
 */

/* SyQon stores its CLI path here; Loom reads it rather than asking again. */
/* ---------------------------------------------------------------------------
 * Finding the SyQon CLI binaries.
 *
 * These used to be discovered ONLY by reading the config CSV that SyQon's own
 * scripts write under File.systemTempDirectory. That directory is temporary in
 * the literal sense: a macOS update purged it on 2026-09-15 and took the
 * Parallax and Prism config files with it, so both tools silently vanished
 * from Loom's dropdowns while the binaries sat untouched in /Applications.
 * (Loom's own cache lives there too and went the same way.) A tool's
 * availability must not depend on a file the OS is free to delete.
 *
 * So discovery is layered, and it LEARNS:
 *
 *   1. Loom's own setting. Written by this function whenever a binary is
 *      found by any route, read back on later runs. PixInsight's settings
 *      survive reboots and temp purges, so the search below normally runs
 *      exactly once per installation.
 *   2. SyQon's config CSV, when it exists. Authoritative: it is the path the
 *      user chose with the wrench button, and it outranks anything guessed.
 *   3. A bounded scan of the application directories -- see
 *      Steps.applicationRoots and Steps.executableCandidates, which are
 *      the two places that know what "installed application" means on
 *      this platform. Depth two, no recursion, which is enough for all
 *      three real macOS layouts (ParallaxAI/parallax_cli,
 *      prism_cli/prism_cli, SyQonStarless.app/Contents/MacOS/SyQonStarless)
 *      without hardcoding one machine's paths as the only truth.
 */
Steps.executableSettingKey = function( name )
{
   return "Loom/exe_" + name;
};

Steps.rememberedExecutable = function( name )
{
   try
   {
      var v = Settings.read( Steps.executableSettingKey( name ), DataType_String );
      if ( v != null && String( v ).length > 0 && File.exists( String( v ) ) )
         return String( v );
   }
   catch ( e ) {}
   return null;
};

Steps.rememberExecutable = function( name, path )
{
   try { Settings.write( Steps.executableSettingKey( name ), DataType_String, String( path ) ); }
   catch ( e ) { /* remembering is an optimisation, not a requirement */ }
};

/*
 * Roots that hold installed applications on this platform.
 *
 * `platform` and `home` are arguments rather than reads of Util.PLATFORM
 * and File.homeDirectory so that the selftest can exercise the branch it
 * is not running on. Production callers pass nothing.
 *
 * The Windows list is the two Program Files trees plus the per-user
 * location that installers written without an elevation prompt use
 * (%LOCALAPPDATA%\Programs -- where, for instance, user-scope installs
 * land). It is the same bounded, depth-two idea as macOS: a list of
 * places applications live, not a filesystem walk.
 */
Steps.applicationRoots = function( platform, home )
{
   if ( home === undefined )
      try { home = File.homeDirectory; } catch ( e ) { home = ""; }
   var hasHome = ( home != null && String( home ).length > 0 );

   if ( Util.isWindows( platform ) )
   {
      var wroots = [ "C:/Program Files", "C:/Program Files (x86)" ];
      if ( hasHome )
         wroots.push( home + "/AppData/Local/Programs" );
      return wroots;
   }

   var roots = [ "/Applications" ];
   if ( hasHome )
      roots.push( home + "/Applications" );
   return roots;
};

/*
 * The paths under one application directory that could BE the executable
 * `name`.
 *
 * macOS has two shapes: a bare binary in the folder, and the binary
 * inside an .app bundle at Contents/MacOS. Windows has no bundles; what
 * it has instead is the .exe suffix and a conventional bin\ subfolder.
 * Loom never spells the suffix into a tool name, so it is added here --
 * the bare name is kept as a candidate too, since PJSR's File.exists is
 * happy with either and an extensionless helper is not impossible.
 */
Steps.executableCandidates = function( base, name, platform )
{
   if ( Util.isWindows( platform ) )
      return [ base + "/" + name + ".exe",
               base + "/bin/" + name + ".exe",
               base + "/" + name ];
   return [ base + "/" + name,
            base + "/Contents/MacOS/" + name ];
};

/*
 * The first of `paths` that exists, or null. A path that cannot even be
 * tested is treated as absent -- File.exists throws on some of the
 * stranger things an application directory can hold.
 */
Steps.firstExistingPath = function( paths )
{
   for ( var i = 0; i < paths.length; ++i )
      try { if ( File.exists( paths[i] ) ) return paths[i]; }
      catch ( e ) {}
   return null;
};

/*
 * Depth-two scan for an executable called `name`. Returns the first match, or
 * null. Deliberately not recursive: /Applications contains entire frameworks
 * and app bundles, and walking them to find a CLI binary would cost seconds
 * on every dialog open for no gain.
 */
Steps.scanForExecutable = function( name )
{
   var roots = Steps.applicationRoots();
   for ( var r = 0; r < roots.length; ++r )
   {
      if ( !File.directoryExists( roots[r] ) )
         continue;
      var entries = Util.directoryEntries( roots[r] );
      for ( var i = 0; i < entries.length; ++i )
      {
         var hit = Steps.firstExistingPath(
                      Steps.executableCandidates( roots[r] + "/" + entries[i], name ) );
         if ( hit != null )
            return hit;
      }
   }
   return null;
};

/*
 * Reads a SyQon config CSV -- a path per line, first usable one wins.
 * Returns null when the file is absent, which is the normal state after a
 * temp purge and not an error.
 */
Steps.executableFromConfig = function( configPath )
{
   try
   {
      if ( !configPath || !File.exists( configPath ) )
         return null;
      var lines = File.readTextFile( configPath ).split( /[\r\n]/ );
      for ( var i = 0; i < lines.length; ++i )
      {
         var q = lines[i].split( "," )[0].trim();
         if ( q.length > 0 && File.exists( q ) )
            return q;
      }
   }
   catch ( e ) {}
   return null;
};

/*
 * `name` is the binary's filename; `configPath` an optional SyQon config CSV.
 * Returns an absolute path or null.
 */
Steps.findExecutable = function( name, configPath )
{
   var found = Steps.rememberedExecutable( name );
   if ( found != null )
      return found;

   found = Steps.executableFromConfig( configPath );
   if ( found == null )
      found = Steps.scanForExecutable( name );

   if ( found != null )
   {
      Steps.rememberExecutable( name, found );
      Util.log( "tools", name + " found at " + found );
   }
   return found;
};

Steps.syqonConfigPath = function()
{
   return File.systemTempDirectory + "/SyQonParallaxCLI/syqon_parallax_config.csv";
};

Steps.syqonExecutable = function()
{
   return Steps.findExecutable( "parallax_cli", Steps.syqonConfigPath() );
};

/* ---------------------------------------------------------------------------
 * SyQon Parallax CLI integration.
 *
 * Faithfully replicates /Applications/PixInsight/src/scripts/SyQon_Parallax.js
 * (read in full before writing this): buildParallaxArgs() (~line 545),
 * executeParallaxOnWindow() (~line 595), createPIStretchedTempWindow()
 * (~line 328) and its reverse (~line 439), and the ExternalProcess
 * round-trip (~lines 655-806).
 *
 * Loom calls aberration correction, star reduction and detail sharpening as
 * three SEPARATE operations (Pipeline.js caches each as its own stage), so
 * each call below builds an args array with ONLY its own flag set -- never
 * all three at once, even though parallax_cli supports that.
 *
 * mode is pinned to "classic" and tileSize/overlap/pad stay at SyQon's own
 * defaults (512/128/512), per this task's requirements; Loom does not
 * expose SyQon's "linked stretch" checkbox, so the stretch always takes the
 * per-channel/unlinked path -- the reference script's own unchecked
 * default, and the only path Loom's per-channel mono views exercise.
 * ------------------------------------------------------------------------ */

Steps.SYQON_MTF_TARGET = 0.12;   // reference SyQonParallaxParameters.mtfTarget default
Steps.SYQON_TILE_SIZE  = 512;    // reference default; requirement 5 pins this
Steps.SYQON_OVERLAP    = 128;    // reference default; requirement 5 pins this
Steps.SYQON_PAD        = 512;    // reference default; requirement 5 pins this
Steps.SYQON_TIMEOUT_MS = 20 * 60 * 1000; // reference outputTimeoutMinutes default

Steps.syqonTempDir = function()
{
   return File.systemTempDirectory + "/SyQonParallaxCLI";
};

Steps.syqonEnsureTempDir = function()
{
   var dir = Steps.syqonTempDir();
   Util.ensureDirectory( dir );
   return dir;
};

Steps.syqonSanitizeFileName = function( name )
{
   return String( name ).replace( /[^A-Za-z0-9_\-]/g, "_" );
};

/* Per-run temp file paths, timestamped so concurrent stages never collide. */
Steps.syqonRunPaths = function( baseName )
{
   var dir = Steps.syqonEnsureTempDir();
   var tag = String( (new Date()).getTime() ) + "_" + Math.floor( Math.random() * 1e6 );
   var safe = Steps.syqonSanitizeFileName( baseName );
   return {
      inputFilePath:  dir + "/" + safe + "_" + tag + "_input.fits",
      outputFilePath: dir + "/" + safe + "_" + tag + "_parallax.fits",
      jsonInfoPath:   dir + "/" + safe + "_" + tag + "_parallax.json"
   };
};

Steps.syqonDeleteFileIfExists = function( filePath )
{
   try { if ( filePath && File.exists( filePath ) ) File.remove( filePath ); }
   catch ( e ) { Util.warn( "syqon", "could not delete " + filePath + ": " + e ); }
};

/* After a CLI round trip, success or not: the working window closed, the temp files gone. */
Steps.syqonCleanUp = function( window, paths )
{
   if ( window && !window.isNull )
      try { window.forceClose(); } catch ( e ) {}
   for ( var i = 0; i < paths.length; ++i )
      Steps.syqonDeleteFileIfExists( paths[i] );
};

/*
 * CLI argument construction, faithful to buildParallaxArgs() in the
 * reference script. Pure and side-effect-free so selftest.js can assert
 * flags (and their absence) without touching a view or a process.
 *
 * opts: { inputFilePath, outputFilePath, jsonInfoPath, correctAberration,
 *         starReduction, sharpen, tileSize, overlap, pad }
 * Callers pass only their own stage's field set to non-zero/true, which is
 * what keeps the three CLI calls separate.
 */
Steps.syqonBuildArgs = function( opts )
{
   var args = [];

   args.push( "--i" ); args.push( opts.inputFilePath );
   args.push( "--o" ); args.push( opts.outputFilePath );

   // mode is pinned to "classic" (Loom never changes it); the reference
   // script only emits --mode when non-default, so it is never emitted here.

   if ( opts.correctAberration )
      args.push( "--correct-aberration" );

   if ( opts.starReduction > 0 )
   {
      args.push( "--star-reduction" );
      args.push( String( opts.starReduction ) );
   }

   if ( opts.sharpen > 0.0 )
   {
      args.push( "--sharpen" );
      args.push( format( "%.2f", opts.sharpen ) );
   }

   args.push( "--tile" );    args.push( String( opts.tileSize ) );
   args.push( "--overlap" ); args.push( String( opts.overlap ) );
   args.push( "--pad" );     args.push( String( opts.pad ) );

   if ( opts.jsonInfoPath && opts.jsonInfoPath.length > 0 )
   {
      args.push( "--json-info" );
      args.push( opts.jsonInfoPath );
   }

   return args;
};

/* ---- PI-side temp stretch / destretch, ported from the reference ---- */

/*
 * PixelMath in place, truncated to [0,1] on a 64-bit working image. One
 * expression for every channel, or an array of three: one per channel
 * (expression/expression1/expression2), where $T is that channel's own
 * value. An array of one is the single expression.
 */
Steps.syqonApplyPixelMath = function( view, exprs )
{
   var e = [].concat( exprs );
   var pm = new PixelMath;
   pm.useSingleExpression    = ( e.length == 1 );
   pm.expression             = e[0];
   if ( e.length > 1 )
   {
      pm.expression1         = e[1];
      pm.expression2         = e[2];
   }
   pm.symbols                = "";
   pm.clearImageCacheAndExit = false;
   pm.cacheGeneratedImages   = false;
   pm.generateOutput         = true;
   pm.singleThreaded         = false;
   pm.optimization           = true;
   pm.use64BitWorkingImage   = true;
   pm.rescale                = false;
   pm.rescaleLower           = 0;
   pm.rescaleUpper           = 1;
   pm.truncate                = true;
   pm.truncateLower          = 0;
   pm.truncateUpper          = 1;
   pm.createNewImage         = false;
   pm.showNewImage           = false;
   pm.newImageColorSpace     = PixelMath.SameAsTarget;
   pm.newImageSampleFormat   = PixelMath.SameAsTarget;
   pm.executeOn( view );
};

Steps.syqonCloneWindowForProcessing = function( sourceWindow, newId )
{
   var src = sourceWindow.mainView.image;
   var w = new ImageWindow(
      src.width, src.height, src.numberOfChannels,
      src.bitsPerSample, src.isReal, src.isColor, newId );
   w.mainView.beginProcess( UndoFlag_NoSwapFile );
   w.mainView.image.assign( src );
   w.mainView.endProcess();
   return w;
};

/*
 * Records originalMin/originalMedian per channel at %.16f precision (the
 * reference's own precision for a bit-exact reverse) and stretches toward
 * targetMedian via MTF-style median transfer with no_black_clip -- i.e. the
 * blackpoint-normalize step never clips, matching stretch_color_image's
 * no_black_clip semantics that the reference script's comment calls out.
 */
Steps.syqonCreateStretchedTempWindow = function( sourceWindow, targetMedian, linked )
{
   var tempId = Steps.syqonSanitizeFileName( sourceWindow.mainView.id ) +
                "_ParallaxTemp_" + String( (new Date()).getTime() );
   var tempWindow = Steps.syqonCloneWindowForProcessing( sourceWindow, tempId );
   var img = tempWindow.mainView.image;

   var stretchInfo = {
      used: true,
      targetMedian: targetMedian,
      wasColor: img.isColor,
      originalMin: [],
      originalMedian: []
   };

   var nChannels = img.isColor ? 3 : 1;
   for ( var c = 0; c < nChannels; ++c )
      stretchInfo.originalMin.push( img.minimum( new Rect( 0, 0, img.width, img.height ), c, c ) );

   var sourceId = sourceWindow.mainView.id;
   if ( img.isColor && linked )
      Steps.syqonStretchLinked( tempWindow.mainView, stretchInfo, targetMedian, sourceId );
   else
      Steps.syqonStretchUnlinked( tempWindow.mainView, stretchInfo, targetMedian, sourceId );

   return { tempWindow: tempWindow, stretchInfo: stretchInfo };
};

// PixelMath rescaling $T so that `min`, a formatted number, becomes 0.
Steps.syqonNormalizeExpression = function( min )
{
   return "($T-" + min + ")/(1-" + min + ")";
};

// PixelMath for the MTF taking median `om` to `tm`, both formatted numbers.
Steps.syqonMtfExpression = function( om, tm )
{
   return "((" + om + "-1)*" + tm + "*$T)/(" + om + "*(" + tm + "+$T-1)-" + tm + "*$T)";
};

// The inverse of syqonMtfExpression: median `tm` back to `om`.
Steps.syqonInverseMtfExpression = function( om, tm )
{
   return "(" + om + "*$T*(" + tm + "-1))/(" + om + "*" + tm + "-" + om + "*$T+" + tm + "*$T-" + tm + ")";
};

// The inverse of syqonNormalizeExpression: 0 back to `min`.
Steps.syqonDenormalizeExpression = function( min )
{
   return "($T*(1-" + min + ")+" + min + ")";
};

// Numbers as the stretch writes them into PixelMath: 16 decimals, for a bit-exact reverse.
Steps.syqonFormatStretch = function( values )
{
   return values.map( function( v ) { return format( "%.16f", v ); } );
};

/*
 * Colour LINKED: one blackpoint and one midtone for all three
 * channels, so the transform cannot move colour. This is what makes
 * it safe to sharpen or denoise a CALIBRATED composite -- an unlinked
 * stretch applies a different curve per channel and undoes the
 * white balance SPCC just established.
 *
 * Mirrors linkedStretch in SyQon_Parallax.js:364. Note that it fills
 * originalMin and originalMedian with three IDENTICAL values, so the
 * existing per-channel reverse is already correct for this path and
 * needs no linked variant of its own.
 */
Steps.syqonStretchLinked = function( view, stretchInfo, targetMedian, sourceId )
{
   var allMin = Math.min( stretchInfo.originalMin[0],
                          Math.min( stretchInfo.originalMin[1],
                                    stretchInfo.originalMin[2] ) );
   Steps.syqonApplyPixelMath( view,
      Steps.syqonNormalizeExpression( format( "%.16f", allMin ) ) );

   var nImg = view.image;
   var nRect = new Rect( 0, 0, nImg.width, nImg.height );
   var omL = ( nImg.median( nRect, 0, 0 ) +
               nImg.median( nRect, 1, 1 ) +
               nImg.median( nRect, 2, 2 ) ) / 3.0;
   if ( !isFinite( omL ) || omL <= 0 || omL >= 1 )
      throw new Error( "SyQon: invalid linked normalized median " + omL +
                       " for " + sourceId );

   for ( var li = 0; li < 3; ++li )
   {
      stretchInfo.originalMedian.push( omL );
      stretchInfo.originalMin[li] = allMin;
   }

   Steps.syqonApplyPixelMath( view,
      Steps.syqonMtfExpression( format( "%.16f", omL ), format( "%.16f", targetMedian ) ) );
};

/*
 * Per-channel unlinked path -- matches the reference's own default
 * (linkedStretch = false), used for mono channels and anything
 * not yet colour-calibrated. One channel is the mono stretch.
 */
Steps.syqonStretchUnlinked = function( view, stretchInfo, targetMedian, sourceId )
{
   Steps.syqonApplyPixelMath( view,
      Steps.syqonFormatStretch( stretchInfo.originalMin ).map( Steps.syqonNormalizeExpression ) );

   var img = view.image, n = stretchInfo.originalMin.length;
   for ( var c = 0; c < n; ++c )
   {
      var om = img.median( new Rect( 0, 0, img.width, img.height ), c, c );
      if ( !isFinite( om ) || om <= 0 || om >= 1 )
         throw new Error( "SyQon Parallax: invalid normalized median " + om + " for " +
                          ( n > 1 ? "channel " + c + " of " : "" ) + sourceId );
      stretchInfo.originalMedian.push( om );
   }

   var tm = format( "%.16f", targetMedian );
   Steps.syqonApplyPixelMath( view, Steps.syqonFormatStretch( stretchInfo.originalMedian ).map(
      function( om ) { return Steps.syqonMtfExpression( om, tm ); } ) );
};

/* Exact algebraic inverse of syqonCreateStretchedTempWindow, applied in
 * reverse order (undo the median transfer, then undo the blackpoint). */
Steps.syqonReverseStretch = function( outputWindow, stretchInfo )
{
   if ( !stretchInfo || !stretchInfo.used )
      return;

   var tm = format( "%.16f", stretchInfo.targetMedian );
   Steps.syqonApplyPixelMath( outputWindow.mainView,
      Steps.syqonFormatStretch( stretchInfo.originalMedian ).map(
         function( om ) { return Steps.syqonInverseMtfExpression( om, tm ); } ) );
   Steps.syqonApplyPixelMath( outputWindow.mainView,
      Steps.syqonFormatStretch( stretchInfo.originalMin ).map( Steps.syqonDenormalizeExpression ) );
};

/* ---- FITS save / load / overwrite, ported from the reference ---- */

Steps.syqonSaveImageAsFits = function( filePath, view )
{
   var imgWindow = view.isMainView ? view.window : view.mainView.window;
   if ( !imgWindow )
      throw new Error( "SyQon Parallax: image window undefined for view" );
   if ( !imgWindow.saveAs( filePath, false, false, false, false ) )
      throw new Error( "SyQon Parallax: failed to save temp FITS: " + filePath );
};

/*
 * A CLI result as a PixelMath source for the target: a mono target takes
 * channel 0 of a colour result (the CLIs return three channels even for a
 * mono input).
 */
Steps.syqonResultSource = function( targetWindow, outputWindow )
{
   var id = outputWindow.mainView.id;
   return ( !targetWindow.mainView.image.isColor && outputWindow.mainView.image.isColor ) ? id + "[0]" : id;
};

/* The result written over the target as it is, untruncated. False when PixelMath refused. */
Steps.syqonOverwriteTargetWithOutput = function( targetWindow, outputWindow )
{
   var pm = new PixelMath;
   pm.useSingleExpression = true;
   pm.expression          = Steps.syqonResultSource( targetWindow, outputWindow );
   pm.createNewImage      = false;
   pm.rescale             = false;
   pm.truncate            = false;
   return pm.executeOn( targetWindow.mainView );
};

Steps.syqonProcessOutput = function( outputFilePath, targetWindow, stretchInfo )
{
   if ( !File.exists( outputFilePath ) )
      throw new Error( "SyQon Parallax output not found: " + outputFilePath );
   var opened = ImageWindow.open( outputFilePath );
   if ( !opened || opened.length < 1 )
      throw new Error( "SyQon Parallax: failed to open output image: " + outputFilePath );
   var outputWindow = opened[0];
   outputWindow.show();
   try
   {
      Steps.syqonReverseStretch( outputWindow, stretchInfo );
      Steps.syqonOverwriteTargetWithOutput( targetWindow, outputWindow );
   }
   finally
   {
      outputWindow.forceClose();
   }
};

/*
 * Blocking ExternalProcess round-trip. Tolerates onError firing with a
 * non-zero code -- the reference script's own comment ("continuing to wait
 * for output") notes parallax_cli can report a spurious error code while
 * still writing a good output file, so onError only logs here; the actual
 * success signal is the caller checking for the output file afterward.
 * A hard timeout is enforced (the reference's dialog-driven wait loop has
 * no real analogue in a headless batch pipeline), and Loom throws instead
 * of leaving the CLI running silently forever.
 */
/*
 * One progress line from a SyQon CLI, or null if it is not one.
 *
 * The three binaries do NOT agree on a format, which is why this is a
 * function with tests rather than a regex inline in the reader:
 *
 *   parallax_cli / prism_cli   "[  2%] [Sharpen/classic] tile 1/64... (1/64)"
 *   SyQon Starless             "[CLI] Progress: 32%"
 *   SyQon Studio (syqon-cli)   "model 42%"  (stderr, per SyQon_Studio.js)
 *
 * Both verified against real captured output, 2026-09-16. Only the first
 * was handled before, so star extraction -- the longest stage in a run --
 * showed no progress at all.
 *
 * Returns { percent, text, done, total }; done/total are null for formats
 * that carry no tile counter.
 */
Steps.parseProgressLine = function( line )
{
   if ( line == null )
      return null;
   var s = String( line ).trim();
   if ( s.length == 0 )
      return null;

   // [ 45%] Stage name (12/30)
   var m = s.match( /^\[\s*(\d+)\s*%\]\s+(.*?)\s+\((\d+)\/(\d+)\)$/ );
   if ( m != null )
      return { percent: parseInt( m[1], 10 ), text: m[2],
               done: parseInt( m[3], 10 ), total: parseInt( m[4], 10 ) };

   // [ 45%] Stage name          -- same shape, no tile counter
   m = s.match( /^\[\s*(\d+)\s*%\]\s+(.+)$/ );
   if ( m != null )
      return { percent: parseInt( m[1], 10 ), text: m[2].trim(),
               done: null, total: null };

   // [CLI] Progress: 32%        -- SyQon Starless
   m = s.match( /^(?:\[([^\]]*)\]\s*)?Progress:\s*(\d+)\s*%\s*$/i );
   if ( m != null )
      return { percent: parseInt( m[2], 10 ),
               text: ( m[1] ? m[1] : "working" ),
               done: null, total: null };

   // model 42%                  -- SyQon Studio's syqon-cli, on stderr
   m = s.match( /^([A-Za-z][A-Za-z _\-]*?)\s+(\d+)\s*%$/ );
   if ( m != null )
      return { percent: parseInt( m[2], 10 ), text: m[1],
               done: null, total: null };

   return null;
};

/*
 * The log line for a progress milestone. The tile counter is optional:
 * Starless reports a bare percentage with no n/m to put in parentheses.
 */
Steps.progressMilestoneText = function( milestone, progress )
{
   var counter = ( progress.total != null ) ? ( " (" + progress.done + "/" + progress.total + ")" ) : "";
   return milestone + "% - " + progress.text + counter;
};

/*
 * `wait`, optional: { text: function( elapsedMs, sawOutput ), stage } for a
 * CLI that can stop silently before its first line -- syqon-cli on a
 * Keychain prompt. text() is asked while the process runs; the first
 * non-null answer goes on the progress line once, and `stage` is put back
 * when output arrives.
 */
Steps.syqonRunProcessBlocking = function( exePath, args, timeoutMs, wait )
{
   var process = new ExternalProcess;
   var stdoutBuf = "", stderrBuf = "";
   var sawError = false, errorCodes = [];

   /*
    * Both parallax_cli and prism_cli report progress on stdout as
    *    [ 45%] Stage name (12/30)
    * (SyQon_Parallax.js:723, SyQon_Prism.js:531). Loom has no wait dialog --
    * it is a batch pipeline -- so the percentage goes to the console
    * instead, which is the only sign of life during a run that can take
    * minutes per channel.
    *
    * Reported only when the percentage CHANGES: the CLI emits a line per
    * tile, and a 12000x7800 frame at 512px tiles is hundreds of lines.
    */
   var lastPct = -1;
   var lastMilestone = -1;
   var partials = { out: "", err: "" };
   var sawOutput = false, waitShown = false;

   /*
    * One stream's new chunk, read for progress lines. Both streams come
    * through here: the standalone CLIs report on stdout, but SyQon Studio's
    * syqon-cli reports on STDERR ("model 42%"), so a reader of stdout alone
    * would show nothing for a whole Studio run.
    */
   function takeProgress( which, chunk )
   {
      if ( chunk.length > 0 && !sawOutput )
      {
         sawOutput = true;
         if ( waitShown && wait.stage )
            Util.reportStage( wait.stage );
      }
      var partial = partials[which] + chunk;
      /*
       * Split on CR as well as LF.
       *
       * SyQon Starless draws an in-place bar: it separates its updates with
       * CARRIAGE RETURNS and emits a single newline only when it finishes.
       * Splitting on "\n" alone left every update sitting in `partial`
       * until the process exited, so the longest stage in a run -- star
       * extraction -- reported no progress whatsoever.
       */
      var lines = partial.split( /\r\n|\r|\n/ );
      partials[which] = lines.pop();    // keep the incomplete tail
      for ( var i = 0; i < lines.length; ++i )
      {
         var m = Steps.parseProgressLine( lines[i] );
         if ( m == null )
            continue;
         var pct = m.percent;
         if ( pct == lastPct )
            continue;
         lastPct = pct;
         Util.reportProgress( pct, m.text );

         /*
          * Every 25%, and nothing in between.
          *
          * An in-place bar was tried first, using console.write with
          * <end><cbr>. PixInsight's console does NOT rewrite the current
          * line from a script -- it scrolls -- so that produced a hundred
          * bars instead of one. These CLIs emit a line per tile (782 of
          * them on a 12000x7800 frame), so anything finer than milestones
          * buries the rest of the log.
          */
         var milestone = Math.floor( pct / 25 ) * 25;
         if ( milestone > lastMilestone && milestone > 0 )
         {
            lastMilestone = milestone;
            Util.log( "cli", Steps.progressMilestoneText( milestone, m ) );
         }
      }
   }

   process.onStandardOutputDataAvailable = function()
   {
      var chunk = String( process.stdout );
      stdoutBuf += chunk;
      takeProgress( "out", chunk );
   };
   process.onStandardErrorDataAvailable = function()
   {
      var chunk = String( process.stderr );
      stderrBuf += chunk;
      takeProgress( "err", chunk );
   };
   process.onError = function( code )
   {
      sawError = true;
      errorCodes.push( code );
      Util.warn( "syqon", "ExternalProcess reported code " + code + " (continuing to wait for output)" );
   };

   var started = process.start( exePath, args );
   if ( !started )
      throw new Error( "SyQon Parallax CLI failed to start: " + exePath );

   /*
    * Pumping events here is what makes the Cancel button clickable -- but
    * the loop must also READ the flag, or the click sets it and nothing
    * happens until the CLI finishes on its own. These passes run for
    * minutes, so that was the difference between a Cancel button and a
    * decoration.
    *
    * The external process is terminated explicitly: abandoning the wait
    * would leave prism_cli/parallax_cli running, holding its temp files and
    * the GPU.
    */
   function stopIfCancelled()
   {
      if ( !Util.cancelRequested() )
         return;
      try { process.terminate(); } catch ( e ) {}
      throw new Error( Util.CANCELLED );
   }

   var startTime = (new Date()).getTime();
   while ( process.isStarting )
   {
      CoreApplication.processEvents();
      stopIfCancelled();
      if ( (new Date()).getTime() - startTime > timeoutMs )
         throw new Error( "SyQon Parallax CLI timed out while starting: " + exePath );
   }
   while ( process.isRunning )
   {
      CoreApplication.processEvents();
      stopIfCancelled();
      if ( wait && !waitShown && !sawOutput )
      {
         var waitText = wait.text( (new Date()).getTime() - startTime, sawOutput );
         if ( waitText != null )
         {
            waitShown = true;
            Util.reportStage( waitText );
            Util.log( "studio", waitText );
         }
      }
      if ( (new Date()).getTime() - startTime > timeoutMs )
         throw new Error( "SyQon Parallax CLI timed out after " +
                          Math.round( timeoutMs / 60000 ) + " minute(s): " + exePath );
   }

   /*
    * The exit code, for a CLI with a failure contract: syqon-cli's says
    * WHY there is no output (4 is the account, not the image). The other
    * CLIs are still judged by their output file alone.
    */
   var exitCode = null;
   try { exitCode = process.exitCode; } catch ( e ) {}

   return { stdout: stdoutBuf, stderr: stderrBuf, sawError: sawError, errorCodes: errorCodes,
            exitCode: ( typeof exitCode == "number" ) ? exitCode : null };
};

/*
 * One SyQon CLI stage: temp-stretch -> save FITS -> run parallax_cli with
 * ONLY this stage's flags -> reverse the stretch on the output -> overwrite
 * the target view -> clean up. Every failure path throws a message naming
 * both the operation (opLabel) and the channel/view (view.id), per
 * requirement 3 -- a channel silently skipped while others succeed is
 * treated as worse than a hard failure.
 *
 * stageOpts: { correctAberration, starReduction, sharpen } -- exactly one
 * of these three is ever non-zero/true per call; see Steps.aberration,
 * Steps.starReduction and Steps.sharpenDetail above.
 */
Steps.syqonExecuteStage = function( view, opLabel, stageOpts, linked )
{
   var exePath = Steps.syqonExecutable();
   if ( exePath == null )
      throw new Error( "SyQon Parallax " + opLabel + " failed on " + view.id +
                       ": parallax_cli executable not found (see Steps.syqonConfigPath())." );

   var targetWindow = Steps.syqonTargetWindow( view, opLabel );
   var runPaths = Steps.syqonRunPaths( view.id );
   var tempStretchWindow = null;

   try
   {
      var stretched = Steps.syqonCreateStretchedTempWindow( targetWindow,
                                                            Steps.SYQON_MTF_TARGET,
                                                            linked );
      tempStretchWindow = stretched.tempWindow;
      var stretchInfo = stretched.stretchInfo;

      Steps.syqonSaveImageAsFits( runPaths.inputFilePath, tempStretchWindow.mainView );

      var args = Steps.syqonBuildArgs( {
         inputFilePath:      runPaths.inputFilePath,
         outputFilePath:     runPaths.outputFilePath,
         jsonInfoPath:       runPaths.jsonInfoPath,
         correctAberration:  !!stageOpts.correctAberration,
         starReduction:      stageOpts.starReduction || 0,
         sharpen:            stageOpts.sharpen || 0.0,
         tileSize: Steps.SYQON_TILE_SIZE,
         overlap:  Steps.SYQON_OVERLAP,
         pad:      Steps.SYQON_PAD
      } );

      Util.log( "syqon", opLabel + " " + view.id + ": " + exePath + " " + args.join( " " ) );

      var result = Steps.syqonRunProcessBlocking( exePath, args, Steps.SYQON_TIMEOUT_MS );

      if ( !File.exists( runPaths.outputFilePath ) )
         throw new Error( "SyQon Parallax " + opLabel + " failed on " + view.id +
                          ": no output file was produced." + Steps.syqonFailureDetail( result ) );

      Steps.syqonImportWithRetry( runPaths.outputFilePath, targetWindow, stretchInfo,
                                  opLabel, view.id );

      Util.log( "syqon", opLabel + " " + view.id + " complete" );
   }
   finally
   {
      Steps.syqonCleanUp( tempStretchWindow,
         [ runPaths.inputFilePath, runPaths.outputFilePath, runPaths.jsonInfoPath ] );
   }
};

// The window a Parallax stage writes back into, refusing a view without one.
Steps.syqonTargetWindow = function( view, opLabel )
{
   var targetWindow = view.isMainView ? view.window : view.mainView.window;
   if ( !targetWindow || targetWindow.isNull )
      throw new Error( "SyQon Parallax " + opLabel + " failed on " + view.id +
                       ": no valid image window." );
   return targetWindow;
};

// What the CLI said about a run that left no output: stderr, else its error codes.
Steps.syqonFailureDetail = function( result )
{
   if ( result.stderr && result.stderr.length > 0 )
      return " stderr: " + result.stderr.trim();
   if ( result.sawError )
      return " (process reported error code(s) " + result.errorCodes.join( "," ) + ")";
   return "";
};

/*
 * The CLI process exiting does not guarantee the output file is fully
 * flushed to disk; retry the import briefly before giving up, same
 * tolerance the reference script's poll loop applies.
 */
Steps.syqonImportWithRetry = function( outputFilePath, targetWindow, stretchInfo, opLabel, viewId )
{
   var maxRetries = 5, retryDelayMs = 1000, lastErr = null;
   for ( var attempt = 0; attempt < maxRetries; ++attempt )
   {
      try
      {
         Steps.syqonProcessOutput( outputFilePath, targetWindow, stretchInfo );
         return;
      }
      catch ( e )
      {
         lastErr = e;
         Util.warn( "syqon", opLabel + " on " + viewId + ": output not ready yet (attempt " +
                            (attempt + 1) + "/" + maxRetries + "): " + e );
         try { System.msleep( retryDelayMs ); } catch ( e2 ) {}
      }
   }
   throw new Error( "SyQon Parallax " + opLabel + " failed on " + viewId +
                    " while importing output: " + ( lastErr ? lastErr.message : "unknown error" ) );
};

/*
 * One SyQon Prism run, mirroring Steps.syqonExecuteStage: temp-stretch ->
 * save FITS -> run prism_cli -> reverse the stretch -> overwrite the target.
 * Argument names read from SyQon_Prism.js on 2026-09-14.
 */
/* ---------------------------------------------------------------------------
 * Prism's pre-stretch target.
 *
 * SyQon's default is 0.15 and it is right for ordinary data. This only
 * departs from it when the image is measurably outside the domain the model
 * was trained on, which is a thing that can be checked rather than guessed.
 *
 * WHAT THE MODEL EXPECTS. Prism's paper publishes the input-domain
 * statistics of its 600,000-tile training corpus (section 2, Table 2):
 * BT.709 luminance with standard deviation between 0.0474 and 0.1288,
 * median 0.0842. A denoiser learns to separate noise from signal at a
 * particular CONTRAST; hand it something flatter than anything it ever saw
 * and it cannot tell them apart, so it flattens real structure into
 * patches.
 *
 * MEASURED on real composites, 2026-09-14:
 *
 *   RGB  std 0.0687 at the 0.15 default -- inside the corpus at every
 *        target. The default is fine and is left alone.
 *   HSO  std 0.0244 at 0.15, 0.0352 at 0.30, peaking at 0.0401 at 0.50 --
 *        BELOW the corpus minimum everywhere. Raising the target cannot
 *        fix it, only get closest, and 0.50 is where contrast peaks.
 *
 * That matches the observed behaviour exactly: patchy output at 0.30,
 * clean at 0.50, and no trouble at all on broadband.
 *
 * WHAT THIS IS NOT. It is not quantisation. Measured: prism_cli preserves
 * full 32-bit float through the FITS path (a 512-px smooth float ramp went
 * in and came back with 512 distinct values). The paper's "16-bit output"
 * describes its PNG/TIFF pipeline, not this one. An earlier version of this
 * code maximised the MTF slope to minimise a quantisation step that does
 * not exist; it was reverted.
 * ------------------------------------------------------------------------- */

Steps.PRISM_DEFAULT_TARGET = 0.15;    // SyQon's own
Steps.PRISM_CORPUS_STD_MIN = 0.0474;  // paper Table 2
Steps.PRISM_CORPUS_STD_MAX = 0.1288;
Steps.PRISM_HIGHLIGHT_CAP  = 0.98;    // p99.9 above this compresses the bright end
Steps.PRISM_TARGET_MAX     = 0.70;

Steps.mtfMidtoneFor = function( x0, target )
{
   return x0*( target - 1 ) / ( 2*target*x0 - target - x0 );
};

Steps.mtfApply = function( m, x )
{
   var d = ( ( 2*m - 1 )*x ) - m;
   return ( ( m - 1 )*x ) / d;
};

/*
 * Luminance statistics of `px` (an array of [r,g,b]) after a pre-stretch to
 * `target`, given the normalised background `x0`. Pure, so selftest can
 * exercise the decision without an image.
 */
Steps.prismStretchStats = function( px, x0, target )
{
   var m = Steps.mtfMidtoneFor( x0, target );
   var wt = [ 0.2126, 0.7152, 0.0722 ];      // BT.709, as the paper uses
   var sum = 0, sum2 = 0;
   for ( var i = 0; i < px.length; ++i )
   {
      var L = 0;
      for ( var k = 0; k < 3; ++k )
      {
         var xn = px[i][k];
         if ( xn < 0 ) xn = 0;
         L += wt[k] * Steps.mtfApply( m, xn );
      }
      sum += L; sum2 += L*L;
   }
   var mean = sum/px.length;
   return { mean: mean, std: Math.sqrt( Math.max( 0, sum2/px.length - mean*mean ) ) };
};

/*
 * The darkest level over all channels, and the mean of the channel
 * medians normalised against it: the background the stretch is built on.
 */
Steps.prismBackground = function( img, nch )
{
   var rect = new Rect( 0, 0, img.width, img.height );

   var allMin = img.minimum( rect, 0, 0 );
   for ( var c = 1; c < nch; ++c )
      allMin = Math.min( allMin, img.minimum( rect, c, c ) );

   var x0 = 0;
   for ( var c2 = 0; c2 < nch; ++c2 )
      x0 += ( img.median( rect, c2, c2 ) - allMin ) / ( 1 - allMin );
   return { allMin: allMin, x0: x0/nch };
};

/*
 * One normalised [r,g,b] sample every `step` pixels, reused for every
 * candidate target, and the 99.9th percentile of their brightest channel.
 */
Steps.prismSamples = function( img, nch, allMin, step )
{
   var px = [], bright = [];
   for ( var y = 0; y < img.height; y += step )
      for ( var x = 0; x < img.width; x += step )
      {
         var v = Steps.prismNormalisedSample( img, x, y, nch, allMin );
         px.push( v );
         bright.push( Math.max( v[0], Math.max( v[1], v[2] ) ) );
      }
   bright.sort( function( a, b ) { return a - b; } );
   return { px: px,
            p999: bright[ Math.min( bright.length-1, Math.floor( 0.999*bright.length ) ) ] };
};

// Mono images repeat their one channel, so every sample is [r,g,b].
Steps.prismNormalisedSample = function( img, x, y, nch, allMin )
{
   var v = [];
   for ( var k = 0; k < 3; ++k )
   {
      var raw = img.sample( x, y, ( nch > 1 ) ? k : 0 );
      v.push( ( raw - allMin )/( 1 - allMin ) );
   }
   return v;
};

/*
 * The best target from the default up, for an image out of domain there:
 * the least contrasty one that reaches the corpus minimum or, for an image
 * too flat ever to reach it, the one that gets closest. Stops at the
 * highlight cap. `stdAtDefault` seeds the search.
 */
Steps.prismRaisedTarget = function( px, x0, p999, stdAtDefault )
{
   var best = { target: Steps.PRISM_DEFAULT_TARGET, std: stdAtDefault, hi: 0 };
   var bestDist = Math.abs( stdAtDefault - Steps.PRISM_CORPUS_STD_MIN );
   for ( var T = Steps.PRISM_DEFAULT_TARGET; T <= Steps.PRISM_TARGET_MAX + 1e-9; T += 0.05 )
   {
      var m = Steps.mtfMidtoneFor( x0, T );
      var hi = Steps.mtfApply( m, p999 );
      if ( hi > Steps.PRISM_HIGHLIGHT_CAP )
         break;
      var st = Steps.prismStretchStats( px, x0, T );
      // aim at the middle of the corpus range, clamped to what is reachable
      var dist = ( st.std > Steps.PRISM_CORPUS_STD_MIN ) ? 0
               : Math.abs( st.std - Steps.PRISM_CORPUS_STD_MIN );
      if ( dist < bestDist || ( dist == 0 && st.std < best.std ) )
      {
         bestDist = dist;
         best = { target: T, std: st.std, hi: hi };
      }
   }
   return best;
};

Steps.prismMtfTarget = function( window, stride )
{
   var img = window.mainView.image;
   var nch = ( img.numberOfChannels >= 3 ) ? 3 : 1;
   var step = stride || 29;

   var bg = Steps.prismBackground( img, nch );
   var x0 = bg.x0;
   if ( !( x0 > 0 && x0 < 1 ) )
   {
      Util.warn( "prism", "could not characterise the image; using the default" );
      return Steps.PRISM_DEFAULT_TARGET;
   }

   var samples = Steps.prismSamples( img, nch, bg.allMin, step );
   var px = samples.px, p999 = samples.p999;

   var atDefault = Steps.prismStretchStats( px, x0, Steps.PRISM_DEFAULT_TARGET );
   if ( atDefault.std >= Steps.PRISM_CORPUS_STD_MIN &&
        atDefault.std <= Steps.PRISM_CORPUS_STD_MAX )
   {
      Util.log( "prism", "target " + Steps.PRISM_DEFAULT_TARGET.toFixed( 2 ) +
                         " (std " + atDefault.std.toFixed( 4 ) +
                         ", inside Prism's training range " +
                         Steps.PRISM_CORPUS_STD_MIN + "-" + Steps.PRISM_CORPUS_STD_MAX + ")" );
      return Steps.PRISM_DEFAULT_TARGET;
   }

   /*
    * Out of domain at the default. Raise the target towards the corpus
    * median, stopping at the highlight cap. For an image too flat to ever
    * reach the range this lands on the contrast peak, which is the closest
    * it can get.
    */
   var best = Steps.prismRaisedTarget( px, x0, p999, atDefault.std );

   Util.log( "prism", "target " + best.target.toFixed( 2 ) +
                      " (std " + best.std.toFixed( 4 ) +
                      ", highlights " + best.hi.toFixed( 3 ) +
                      "); raised from " + Steps.PRISM_DEFAULT_TARGET +
                      " where std was " + atDefault.std.toFixed( 4 ) );
   if ( best.std < Steps.PRISM_CORPUS_STD_MIN )
      Util.warn( "prism", "this image is flatter than anything in Prism's training " +
                          "corpus (std " + best.std.toFixed( 4 ) + " < " +
                          Steps.PRISM_CORPUS_STD_MIN + ") at every usable target; " +
                          "denoising may still over-smooth" );
   return best.target;
};

Steps.prismExecuteStage = function( view, strength, alreadyStretched )
{
   var exePath = Steps.prismExecutable();
   if ( exePath == null )
      throw new Error( "SyQon Prism denoise failed on " + view.id +
                       ": prism_cli executable not found (see Steps.prismConfigPath())." );

   var targetWindow = view.isMainView ? view.window : view.mainView.window;
   if ( !targetWindow || targetWindow.isNull )
      throw new Error( "SyQon Prism denoise failed on " + view.id + ": no valid image window." );

   var dir = File.systemTempDirectory + "/SyQonPrismCLI";
   Util.ensureDirectory( dir );
   var tag = String( (new Date()).getTime() ) + "_" + Math.round( Math.random()*1e6 );
   var safe = Steps.syqonSanitizeFileName( view.id );
   var inPath  = dir + "/" + safe + "_" + tag + "_input.fits";
   var outPath = dir + "/" + safe + "_" + tag + "_prism.fits";

   var tempStretchWindow = null;
   try
   {
      Util.log( "denoise", view.id + ": SyQon Prism, strength " + strength.toFixed( 2 ) );

      /*
       * ALREADY-STRETCHED INPUT GETS SYQON'S FIXED 0.15, NOT A MEASURED ONE.
       *
       * Steps.prismMtfTarget exists to lift LINEAR data into the contrast
       * range Prism was trained on -- it measures the image and departs from
       * SyQon's default when the image sits outside that range. Hand it data
       * that has already been stretched and the measurement means something
       * else entirely: on this project's HSO it chose 0.50, so Prism received
       * Loom's stretch with a second aggressive stretch on top.
       *
       * Measured consequence, on the cached HSO chain: noise relative to the
       * median went from 5.82% before the denoise stage to 7.15% after, and
       * the median shifted 12% (0.385 -> 0.431). The step whose job is to
       * remove noise added 23% of it.
       *
       * Running Prism by hand after the stretch -- which applies SyQon's own
       * fixed 0.15 -- produces a good result on the same data. So when the
       * input is already non-linear, use their number and not ours.
       */
      var mtfTarget = alreadyStretched ? Steps.PRISM_DEFAULT_TARGET
                                       : Steps.prismMtfTarget( targetWindow );
      Util.log( "denoise", view.id + ": pre-stretch target " + mtfTarget +
                           ( alreadyStretched ? " (SyQon default; input is already stretched)"
                                              : " (measured for linear input)" ) );
      var stretched = Steps.syqonCreateStretchedTempWindow( targetWindow, mtfTarget,
                        targetWindow.mainView.image.numberOfChannels >= 3 );
      tempStretchWindow = stretched.tempWindow;
      var stretchInfo = stretched.stretchInfo;

      Steps.syqonSaveImageAsFits( inPath, tempStretchWindow.mainView );

      var args = [ "--input", inPath, "--output", outPath,
                   "--model-kind", "prism_deep",
                   "--tile", "512", "--overlap", "128", "--pad", "512",
                   "--strength", format( "%.2f", strength ),
                   "--use-amp", "--amp-dtype", "fp16" ];
      Util.log( "denoise", exePath + " " + args.join( " " ) );

      var res = Steps.syqonRunProcessBlocking( exePath, args, Steps.SYQON_TIMEOUT_MS );
      if ( !File.exists( outPath ) )
         throw new Error( "SyQon Prism produced no output for " + view.id +
                          ( res.stderr ? ( " stderr: " + res.stderr.trim() ) : "" ) );

      Steps.syqonProcessOutput( outPath, targetWindow, stretchInfo );
      Util.log( "denoise", view.id + ": Prism complete" );
   }
   finally
   {
      Steps.syqonCleanUp( tempStretchWindow, [ inPath, outPath ] );
   }
};

/*
 * The SyQon Starless CLI will not locate its own model when run headless;
 * it looks for runs/axiom_v3/checkpoints/axiom3.mlmodelc relative to the
 * working directory and exits 1 with "Model path not specified or model not
 * found". The model ships inside the app bundle, so -m is passed explicitly.
 * Verified against the binary's own --help on 2026-09-15.
 */
Steps.starlessConfigPath = function()
{
   return File.systemTempDirectory + "/SyQonStarlessCLI/syqon_starless_config.csv";
};

Steps.starlessExecutable = function()
{
   return Steps.findExecutable( "SyQonStarless", Steps.starlessConfigPath() );
};

/*
 * Where the model could be, in preference order, given where the binary was
 * found.
 *
 * On macOS the model sits in the app bundle's Resources, beside the binary's
 * MacOS directory. Deriving it from the binary rather than assuming
 * /Applications is what makes a non-standard install work -- and it is also
 * what makes this correct on Windows, where there is no bundle and the model
 * can only be beside the executable or one level up.
 *
 * The bare /Applications path stays as a last resort on macOS only: on
 * Windows it is not merely useless but misleading, since "/Applications"
 * there is a path on the current drive.
 */
Steps.starlessModelCandidates = function( exe, platform )
{
   var candidates = [];
   if ( exe != null )
   {
      var exeDir = File.extractDirectory( exe );                 // .../Contents/MacOS
      var parent = File.extractDirectory( exeDir );              // .../Contents
      candidates.push( parent + "/Resources/axiom3.mlmodelc" );
      candidates.push( exeDir + "/axiom3.mlmodelc" );
   }
   if ( !Util.isWindows( platform ) )
      candidates.push( "/Applications/SyQonStarless.app/Contents/Resources/axiom3.mlmodelc" );
   return candidates;
};

Steps.starlessModelPath = function()
{
   var candidates = Steps.starlessModelCandidates( Steps.starlessExecutable() );
   for ( var i = 0; i < candidates.length; ++i )
      try
      {
         if ( File.directoryExists( candidates[i] ) || File.exists( candidates[i] ) )
            return candidates[i];
      }
      catch ( e ) {}
   return null;
};

/*
 * SyQon Starless CLI round trip. Unlike Prism and Parallax this binary
 * reads TIFF (its --help lists TIFF or PNG, not FITS) and applies its own
 * stretch internally -- the log prints the blackpoint and scale it computed
 * -- so Loom does no temporary stretch of its own here.
 */
Steps.syqonStarlessRun = function( window, label )
{
   var exePath = Steps.starlessExecutable();
   if ( exePath == null )
      throw new Error( "SyQon Starless executable not found" );
   var model = Steps.starlessModelPath();
   if ( model == null )
      throw new Error( "SyQon Starless model not found" );

   var dir = File.systemTempDirectory + "/SyQonStarlessCLI";
   Util.ensureDirectory( dir );
   var tag = String( (new Date()).getTime() ) + "_" + Math.round( Math.random()*1e6 );
   var safe = Steps.syqonSanitizeFileName( window.mainView.id );
   var inPath  = dir + "/" + safe + "_" + tag + "_in.tif";
   var outPath = dir + "/" + safe + "_" + tag + "_starless.tif";

   try
   {
      window.saveAs( inPath, false, false, false, false );

      /*
       * Device: Auto, always, and deliberately not a choice.
       *
       * SyQon Starless has a bug on the explicit device settings, so Auto is
       * the only one that can be relied on. The binary's own default is not
       * Auto but GPU/Metal, and that default is what fails: run headless as
       * a child process, a 512x512 probe exited 139 -- SIGSEGV -- with no
       * output at all (measured 2026-09-15). Passing -d is therefore
       * mandatory rather than optional, and the value is fixed here so no
       * caller can reintroduce the broken setting.
       */
      var args = [ "-i", inPath, "-o", outPath, "-c", "pixinsight",
                   "-m", model, "-d", "Auto" ];
      Util.log( "starless", exePath + " " + args.join( " " ) );
      var res = null;
      try { res = Steps.syqonRunProcessBlocking( exePath, args, Steps.SYQON_TIMEOUT_MS ); }
      catch ( e ) { res = null; }
      if ( !File.exists( outPath ) )
         throw new Error( "SyQon Starless produced no output for " +
                          ( label || window.mainView.id ) +
                          ( res && res.stderr ? ( " stderr: " + res.stderr.trim() ) : "" ) );

      var opened = ImageWindow.open( outPath );
      if ( !opened || opened.length < 1 )
         throw new Error( "SyQon Starless: could not open " + outPath );
      var ow = opened[0];
      try
      {
         /*
          * The CLI returns three channels even for a mono input (its log
          * reports Channel 0/1/2 on a 1-sample TIFF), so a mono target
          * takes channel 0 -- the same correction SyQon_Starless.js makes
          * in overwriteTargetWithStarless().
          */
         if ( !Steps.syqonOverwriteTargetWithOutput( window, ow ) )
            throw new Error( "SyQon Starless: could not apply the starless result" );
      }
      finally { try { ow.forceClose(); } catch ( e ) {} }
   }
   finally
   {
      Steps.syqonCleanUp( null, [ inPath, outPath ] );
   }
};

/* ---------------------------------------------------------------------------
 * SyQon Studio.
 *
 * Studio replaces the three standalone SyQon applications with one CLI,
 * syqon-cli, and one command contract for every model:
 *
 *    syqon-cli --model MODEL [OPTIONS] INPUT OUTPUT
 *
 * Read from /Applications/PixInsight/src/scripts/SyQon_Studio.js v1.0.1
 * (Franklin Marek): the model registry (~line 71), buildStudioArgs()
 * (~line 446), the pre-CLI sanitising (~line 519) and the result import
 * (~line 537). The models Loom uses, and the input contract Studio
 * publishes for each:
 *
 *    prism-essential  Denoise          Linear RGB or mono     Included
 *    prism-advanced   Denoise (2.0)    Linear RGB or mono     Licensed
 *    prism-ultra      Denoise (2.0)    Linear or non-linear   Licensed
 *    prism-max        Denoise (2.0)    Linear or non-linear   Licensed
 *    parallax         Correct/Reduce/  Linear or non-linear   Licensed
 *                     Deblur
 *    axiom            Starless         Linear (Axiom stretch) Licensed
 *                                      or non-linear
 *    deep-gradient    Gradient removal Linear RGB or mono     Included
 *
 * STUDIO IS OFFERED BESIDE the standalone Prism, Parallax and Starless,
 * each when it is found -- the maintainer's call. Studio's models are not
 * the standalone ones under a new name (Prism 2.0 is a different network,
 * and paid), so a choice saved with a standalone tool stays with it.
 * Studio adds Prism 2.0, a two-pass denoiser: Advanced on the linear
 * plate, then Ultra (Medium) or Max (High) after the stretch
 * (Steps.NOISE_LEVELS.studio2), and, with BlurXTerminator
 * chosen, runs the aberration pass in BXT's place with Parallax's
 * correction (Steps.aberrationCorrector) -- no dropdown entry.
 *
 * NO TEMPORARY STRETCH. The standalone CLIs were trained on stretched data,
 * which is why Loom wrapped them in syqonCreateStretchedTempWindow and its
 * inverse -- and why standalone Prism had to run after the stretch. Studio
 * takes linear input natively and is TOLD the domain (--domain), so each
 * model runs where Loom's pipeline already has the data it asks for:
 * Deep Gradient where GraXpert runs, Prism Essential and Advanced in the
 * linear denoise slot beside NXT, Ultra and Max in the post-stretch slot,
 * Parallax where BlurXTerminator runs. None of them round-trips linear
 * flux through a convex curve.
 *
 * Entitlement lives in the Studio account, not on the command line: a model
 * the account is not licensed for exits 4. There is no flag to pass and
 * nothing Loom can check without running the model -- so preflight runs
 * each Studio model the run will use once, on a tiny image
 * (Steps.studioCheckEntitlement), and a refusal stops the run before any
 * work rather than at the denoise stage an hour in. A run's own failure
 * still names the model, in Studio's words (Steps.studioFailureText).
 * ------------------------------------------------------------------------ */

// What Settings and process icons store; Studio's Prism names are in Steps.js.
Steps.SHARPEN_TOOL_STUDIO  = "SyQon Studio Parallax";
Steps.SHARPEN_TOOL_STUDIO_CORRECT_OLD = "SyQon Studio Parallax (correct only)";
Steps.STAR_TOOL_STUDIO     = "SyQon Studio Axiom";

Steps.GRADIENT_TOOL_NONE     = "none";
Steps.GRADIENT_TOOL_GRAXPERT = "GraXpert";
Steps.GRADIENT_TOOL_STUDIO   = "SyQon Studio Deep Gradient";

/* The CLI's stable model identifiers (`syqon-cli --list-models`). */
Steps.STUDIO_MODEL_DENOISE  = "prism-essential";
Steps.STUDIO_MODEL_PARALLAX = "parallax";
Steps.STUDIO_MODEL_STARLESS = "axiom";
Steps.STUDIO_MODEL_GRADIENT = "deep-gradient";

/*
 * Studio's own defaults, from the P object in SyQon_Studio.js. f32 is the
 * precision Studio recommends for the in-PixInsight round trip; 30 minutes
 * its output timeout.
 */
Steps.STUDIO_PRECISION  = "f32";
Steps.STUDIO_TILE_SIZE  = 512;
Steps.STUDIO_OVERLAP    = 64;
Steps.STUDIO_TIMEOUT_MS = 30 * 60 * 1000;

/*
 * Parallax family: "classic", NOT Studio's default "aesthetics".
 *
 * The standalone Parallax integration pins classic, and for the same
 * reason: Loom makes no artistic choices, and the aesthetics family is by
 * its name the one tuned for looks. Keeping classic also means switching
 * from standalone Parallax to Studio's changes the tool, not the intent.
 */
Steps.STUDIO_PARALLAX_FAMILY = "classic";

/*
 * The profiles Studio's Parallax offers through its command line, Classic
 * first. Aesthetics is Studio's own default and the one its UI shows; it
 * is the user's choice (Config.parallaxFamily), never Loom's.
 */
Steps.STUDIO_PARALLAX_FAMILIES = [ "classic", "aesthetics" ];

// A stored or passed profile as one Studio offers; anything else is Classic.
Steps.studioFamilyOf = function( family )
{
   return ( Steps.STUDIO_PARALLAX_FAMILIES.indexOf( family ) >= 0 ) ? family : Steps.STUDIO_PARALLAX_FAMILY;
};

/*
 * The dropdown's label for a gradient tool. MultiscaleGradientCorrection
 * always runs, so "none" is labelled for what does: MGC alone. The stored
 * value stays "none", so saved settings and process icons still load.
 */
Steps.gradientToolLabel = function( tool )
{
   return ( tool == Steps.GRADIENT_TOOL_NONE ) ? "Multi Gradient only" : tool;
};

/*
 * The gradient tool a configuration asks for.
 *
 * Gradient removal used to be a GraXpert checkbox, `useGraXpert`. Saved
 * process icons and Settings from then carry only that, and it must mean
 * what it meant: true is GraXpert, false is none. A configuration naming
 * neither removes nothing, which is what `!config.useGraXpert` said too.
 */
Steps.gradientToolOf = function( config )
{
   if ( config && typeof config.gradientTool == "string" && config.gradientTool.length > 0 )
      return config.gradientTool;
   return ( config && config.useGraXpert === true ) ? Steps.GRADIENT_TOOL_GRAXPERT
                                                    : Steps.GRADIENT_TOOL_NONE;
};

/*
 * The gradient stage's cache parameters.
 *
 * GraXpert's and none's are byte-for-byte what the checkbox produced --
 * { enabled, smoothing } -- so every channel cached before the dropdown
 * existed still hits. Studio keys on its model instead of on smoothing,
 * which it does not take: moving GraXpert's slider must not re-run a
 * Deep Gradient result that it cannot have changed.
 */
Steps.gradientStageParams = function( config )
{
   var tool = Steps.gradientToolOf( config );
   if ( tool == Steps.GRADIENT_TOOL_STUDIO )
      return { enabled: true, tool: tool, model: Steps.STUDIO_MODEL_GRADIENT };
   return { enabled: tool == Steps.GRADIENT_TOOL_GRAXPERT, smoothing: config.smoothing };
};

/* Background extraction with whichever tool is chosen; "none" does nothing. */
Steps.removeGradient = function( view, config )
{
   var tool = Steps.gradientToolOf( config );
   if ( tool == Steps.GRADIENT_TOOL_GRAXPERT )
      Steps.graxpert( view, config.smoothing );
   else if ( tool == Steps.GRADIENT_TOOL_STUDIO )
   {
      Util.reportStage( "SyQon Studio Deep Gradient → " + view.id );
      Steps.studioRun( view, "gradient removal",
                       { model: Steps.STUDIO_MODEL_GRADIENT, domain: "linear" } );
   }
   else if ( tool != Steps.GRADIENT_TOOL_NONE )
      throw new Error( "Unknown gradient removal tool: " + tool );
};

/*
 * Brings a loaded configuration up to date. Called by Loom.js after
 * Parameters and Settings are read; pure apart from the object it is
 * handed, so the selftest can drive it. Standalone SyQon choices are left
 * alone: Studio is offered beside those tools, not in their place.
 * `prism2Offered`: true when Studio is found and Prism 2.0 is offered,
 * false when Studio is found but Essential is offered in its place,
 * undefined when Studio is not found.
 */
Steps.migrateConfig = function( config, prism2Offered )
{
   config.gradientTool = Steps.gradientToolOf( config );
   // Essential's old name; the model, and so the pixels, are unchanged
   if ( config.noiseTool == Steps.NR_TOOL_STUDIO_OLD )
      config.noiseTool = Steps.NR_TOOL_STUDIO;
   /*
    * Studio's Prism is one entry, 2.0 or Essential (Steps.noiseToolsFrom):
    * a saved choice of the other loads as the one offered. Undefined when
    * Studio is not found, and then nothing is changed.
    */
   if ( prism2Offered === true && config.noiseTool == Steps.NR_TOOL_STUDIO )
      config.noiseTool = Steps.NR_TOOL_STUDIO2;
   else if ( prism2Offered === false && config.noiseTool == Steps.NR_TOOL_STUDIO2 )
      config.noiseTool = Steps.NR_TOOL_STUDIO;
   /*
    * A strength the tool does not offer loads as Medium: Prism 2.0's Low,
    * and its High when the stretch is off.
    */
   config.noiseLevel = Steps.supportedNoiseLevel( config.noiseTool, config.noiseLevel, config.stretch !== false );
   if ( config.noiseLevelL )
      config.noiseLevelL = Steps.supportedNoiseLevel( config.noiseTool, config.noiseLevelL,
                                                      config.stretch !== false );
   /*
    * Studio Parallax (correct only) was a dropdown entry for a while. It
    * is BlurXTerminator now, whose aberration pass Studio runs when found;
    * star reduction and detail were greyed out under it, so they stay off
    * rather than switching BXT's on from levels the user never saw.
    */
   if ( config.sharpenTool == Steps.SHARPEN_TOOL_STUDIO_CORRECT_OLD )
   {
      config.sharpenTool = Steps.SHARPEN_TOOL_BXT;
      config.starReduction = "none";
      config.detailLevel = "none";
   }
   return config;
};

/* ---- entitlement ---- */

/* Studio's names for the models Loom runs, from its model registry. */
Steps.STUDIO_MODEL_LABELS = {
   "prism-essential": "Prism Essential",
   "prism-advanced":  "Prism Deep Advanced",
   "prism-ultra":     "Prism Deep Ultra",
   "prism-max":       "Prism Deep Max",
   "parallax":        "Parallax",
   "axiom":           "Axiom V3",
   "deep-gradient":   "Deep Gradient"
};

Steps.studioModelLabel = function( model )
{
   return Steps.STUDIO_MODEL_LABELS[model] || model;
};

/*
 * Every Studio model a configuration will run, each once, in pipeline
 * order. Pure: preflight probes what this returns. `studioFound` matters
 * only for BlurXTerminator, whose aberration pass Studio then runs.
 */
Steps.studioModelsFor = function( config, studioFound )
{
   var out = [];
   function add( m ) { if ( out.indexOf( m ) < 0 ) out.push( m ); }
   if ( Steps.gradientToolOf( config ) == Steps.GRADIENT_TOOL_STUDIO )
      add( Steps.STUDIO_MODEL_GRADIENT );
   // Studio Parallax, or BXT's aberration pass run by Studio (studioFound)
   if ( Steps.aberrationCorrector( config.sharpenTool, !!studioFound ) == Steps.SHARPEN_TOOL_STUDIO )
      add( Steps.STUDIO_MODEL_PARALLAX );
   if ( config.starTool == Steps.STAR_TOOL_STUDIO )
      add( Steps.STUDIO_MODEL_STARLESS );
   if ( config.noiseTool == Steps.NR_TOOL_STUDIO )
      add( Steps.STUDIO_MODEL_DENOISE );
   /*
    * Prism 2.0: the models the chosen Colour and L strengths will run,
    * with the stretch as it is (maintainer, 2026-09-27; it used to check
    * all three whatever the level). With the stretch: the linear pass when
    * it is on (Steps.PRISM2_LINEAR_PASS), then Ultra or Max. Without: the
    * linear pass when on, else the level's unstretched model.
    */
   if ( config.noiseTool == Steps.NR_TOOL_STUDIO2 )
   {
      var stretch = config.stretch !== false;
      [ config.noiseLevel, config.noiseLevelL || config.noiseLevel ].forEach( function( lv )
      {
         var step = Steps.NOISE_LEVELS.studio2[Steps.supportedNoiseLevel( config.noiseTool, lv, stretch )];
         if ( step == null )
            return;
         if ( step.linear )
            add( step.linear.model );
         if ( stretch && step.stretched )
            add( step.stretched.model );
         else if ( !stretch && !step.linear && step.unstretched )
            add( step.unstretched.model );
      } );
   }
   return out;
};

/*
 * Whether syqon-cli's answer says the ACCOUNT refused the model: exit 4,
 * or -- whatever the code -- its "no secure SyQon session" message, which
 * is how a signed-out device answers (seen 2026-09-25 for axiom-mini).
 */
Steps.studioAccountRefused = function( res )
{
   if ( res.exitCode === 4 )
      return true;
   return /secure license check failed|no secure SyQon session/i.test( String( res.stderr || "" ) );
};

/* The dropdown a model is chosen from, for "choose another ..." */
Steps.studioModelRole = function( model )
{
   if ( Steps.studioIsPrism( model ) )
      return "noise reduction tool";
   if ( model == Steps.STUDIO_MODEL_PARALLAX )
      return "sharpening tool";
   if ( model == Steps.STUDIO_MODEL_STARLESS )
      return "star extraction tool";
   return "gradient removal tool";
};

/*
 * A preflight probe's result as a problem to report, or null. Only the
 * account is a problem here: any other failure on a 64x64 test image says
 * little about the real one, and the real run reports its own.
 */
Steps.studioProbeProblem = function( model, res )
{
   if ( !Steps.studioAccountRefused( res ) )
      return null;
   var msg = "SyQon Studio: " + Steps.studioModelLabel( model ) + " (" + model + ") is not " +
             "available to your SyQon account (syqon-cli exit " +
             ( res.exitCode != null ? res.exitCode : "?" ) + "). Sign in through SyQon " +
             "Studio, or choose another " + Steps.studioModelRole( model );
   msg += ".";
   if ( Steps.studioIsPrism2( model ) )
      msg += " Until a check succeeds, Loom offers " + Steps.NR_TOOL_STUDIO +
             " (included) in place of Prism 2.0.";
   return msg;
};

/*
 * The Deep Prism models behind SyQon Studio Prism 2.0: every model of its
 * two-pass ladder, whether or not the linear pass is on.
 */
Steps.studioIsPrism2 = function( model )
{
   for ( var level in Steps.PRISM2_LADDER_LINEAR )
   {
      var step = Steps.PRISM2_LADDER_LINEAR[level];
      if ( ( step.linear && step.linear.model == model ) ||
           ( step.stretched && step.stretched.model == model ) )
         return true;
   }
   return false;
};

/*
 * The remembered refusal of Prism 2.0 after a check: true when a Prism 2.0
 * model was refused, false when one ran, null when the check tried none
 * (leave it as it was). `results` is [{ model, refused }].
 */
Steps.prism2FlagAfterCheck = function( results )
{
   var flag = null;
   for ( var i = 0; i < results.length; ++i )
   {
      if ( !Steps.studioIsPrism2( results[i].model ) )
         continue;
      if ( results[i].refused )
         return true;
      flag = false;
   }
   return flag;
};

/*
 * Settings: Prism 2.0 found refused by the account. Written by preflight,
 * cleared by a later check where a Prism 2.0 model runs; while set, the
 * dialog offers Prism Essential in 2.0's place.
 */
Steps.PRISM2_UNAVAILABLE_KEY = "Loom/studioPrism2Unavailable";

Steps.studioPrism2Unavailable = function()
{
   try { return Settings.read( Steps.PRISM2_UNAVAILABLE_KEY, DataType_Boolean ) === true; }
   catch ( e ) { return false; }
};

Steps.setStudioPrism2Unavailable = function( on )
{
   try { Settings.write( Steps.PRISM2_UNAVAILABLE_KEY, DataType_Boolean, !!on ); }
   catch ( e ) {}
};

/* Why a run produced nothing, naming the model. */
Steps.studioFailureText = function( model, res )
{
   var why = ( res.exitCode != null && res.exitCode != 0 )
             ? Steps.studioExitMessage( res.exitCode )
             : "no output was produced.";
   return Steps.studioModelLabel( model ) + " (" + model + "): " + why +
          ( res.stderr ? ( " stderr: " + String( res.stderr ).trim() ) : "" );
};

/*
 * The Keychain. syqon-cli restores its sign-in from the macOS Keychain,
 * and that read can stop on a password prompt which waits for the user,
 * however long. Choosing "Allow" rather than "Always Allow" brings it back
 * on every run. Loom never touches the Keychain; it says what is
 * happening: once a session before the first run, and on the progress
 * line when a run has shown nothing for 10 s at its start.
 */
Steps.STUDIO_KEYCHAIN_NOTE =
   "SyQon Studio may ask for your Mac password to reach its sign-in in the " +
   "Keychain: enter it and choose Always Allow, or it will ask again every run.";
Steps.STUDIO_SIGNIN_WAIT_MS = 10000;
Steps.STUDIO_SIGNIN_WAIT_TEXT =
   "Waiting for SyQon Studio's sign-in (check for a Keychain prompt)";

/* The note, or null once it has been said; `state` is { noted }. */
Steps.studioKeychainNote = function( state )
{
   return state.noted ? null : Steps.STUDIO_KEYCHAIN_NOTE;
};

Steps.studioWaitText = function( elapsedMs, sawOutput )
{
   return ( !sawOutput && elapsedMs > Steps.STUDIO_SIGNIN_WAIT_MS )
          ? Steps.STUDIO_SIGNIN_WAIT_TEXT : null;
};

/* Per PixInsight session: the note said, and the models that ran. */
Steps.studioSession = { noted: false, entitled: {} };

/* Says the Keychain note on the console and the progress line, once. */
Steps.studioNoteKeychain = function()
{
   var note = Steps.studioKeychainNote( Steps.studioSession );
   if ( note == null )
      return;
   Steps.studioSession.noted = true;
   Util.log( "studio", note );
   Util.reportStage( note );
};

/*
 * One syqon-cli run of `model` on a 64x64 linear image, for the account's
 * answer. Returns { exitCode, stderr }. A model that ran is remembered for
 * the session; a refusal is not, so signing in and running again works.
 */
Steps.studioProbe = function( model )
{
   if ( Steps.studioSession.entitled[model] )
      return { exitCode: 0, stderr: "" };
   var exePath = Steps.studioExecutable();
   if ( exePath == null )
      return { exitCode: null, stderr: "syqon-cli not found" };

   var dir = File.systemTempDirectory + "/SyQonStudioCLI";
   Util.ensureDirectory( dir );
   var stem = dir + "/loom_probe_" + model + "_" + String( (new Date()).getTime() );
   var inPath = stem + "_input.xisf", outPath = stem + "_output.xisf";
   var w = null;
   try
   {
      w = new ImageWindow( 64, 64, 1, 32, true, false, Util.freeWindowId( "loom_studio_probe" ) );
      w.mainView.beginProcess( UndoFlag_NoSwapFile );
      w.mainView.image.fill( 0.01 );
      w.mainView.endProcess();
      Steps.studioSaveXisf( inPath, w.mainView );
      w.forceClose();
      w = null;

      var args = Steps.studioBuildArgs( { model: model, domain: "linear", application: 1.0,
                                          parallax: { correction: true },
                                          input: inPath, output: outPath } );
      Steps.studioNoteKeychain();
      var res = Steps.syqonRunProcessBlocking( exePath, args, 5 * 60 * 1000,
                   { text: Steps.studioWaitText, stage: "Checking SyQon Studio " + model } );
      var ran = File.exists( outPath ) || File.exists( Steps.studioDeclaredOutput( res.stdout ) || "" );
      var out = { exitCode: ( res.exitCode != null ) ? res.exitCode : ( ran ? 0 : null ),
                  stderr: res.stderr };
      if ( ran && !Steps.studioAccountRefused( out ) )
         Steps.studioSession.entitled[model] = true;
      return out;
   }
   finally
   {
      Steps.syqonCleanUp( w, [ inPath, outPath ] );
   }
};

/* Preflight's check: the problems, one per model the account refuses. */
Steps.studioCheckEntitlement = function( config )
{
   var problems = [], results = [];
   var models = Steps.studioModelsFor( config, true );  // called only with Studio found
   for ( var i = 0; i < models.length; ++i )
   {
      Util.reportStage( "Checking SyQon Studio " + models[i] );
      var res = Steps.studioProbe( models[i] );
      var p = Steps.studioProbeProblem( models[i], res );
      /*
       * Only a clear answer counts towards the remembered flag: refused,
       * or ran (exit 0). An inconclusive test run changes nothing.
       */
      if ( p != null || res.exitCode === 0 )
         results.push( { model: models[i], refused: p != null } );
      if ( p != null )
         problems.push( p );
      else
         Util.log( "studio", models[i] + ": " + ( ( res.exitCode === 0 )
                   ? "available to this account" : "test run inconclusive (" +
                     Steps.studioExitMessage( res.exitCode ) + ")" ) );
   }
   var flag = Steps.prism2FlagAfterCheck( results );
   if ( flag != null && flag != Steps.studioPrism2Unavailable() )
   {
      Steps.setStudioPrism2Unavailable( flag );
      Util.log( "studio", flag
                ? "Prism 2.0 is not available to this account: Loom offers " +
                  Steps.NR_TOOL_STUDIO + " in its place until a check succeeds"
                : "Prism 2.0 is available to this account again" );
   }
   return problems;
};

/* ---- finding syqon-cli ---- */

/* Where SyQon_Studio.js keeps the path chosen with its wrench button. */
Steps.studioConfigPath = function()
{
   return File.systemTempDirectory + "/SyQonStudioCLI/syqon_studio_config.csv";
};

/*
 * A path handed over by the user may name the .app bundle rather than the
 * binary; SyQon_Studio.js's resolveBundle() accepts either, so Loom does.
 */
Steps.studioResolveBundle = function( p, platform )
{
   if ( !p )
      return p;
   var s = String( p );
   if ( !Util.isWindows( platform ) && /\.app$/.test( s ) )
      return s + "/Contents/MacOS/syqon-cli";
   return s;
};

/*
 * SYQON_CLI_PATH first, as in SyQon_Studio.js: it is the one route Studio
 * documents for Linux, which has no standard install location yet. Then
 * the same layered discovery every SyQon binary uses -- remembered path,
 * Studio's config CSV, the bounded application scan -- which finds
 * /Applications/SyQon Studio.app/Contents/MacOS/syqon-cli on macOS and
 * Program Files\SyQon Studio or %LOCALAPPDATA%\Programs\SyQon Studio on
 * Windows.
 */
Steps.studioExecutable = function()
{
   var env = "";
   try { env = String( System.getEnvironmentVariable( "SYQON_CLI_PATH" ) || "" ); }
   catch ( e ) { env = ""; }
   if ( env.length > 0 )
   {
      var p = Steps.studioResolveBundle( env );
      try { if ( File.exists( p ) ) return p; } catch ( e2 ) {}
   }
   var found = Steps.findExecutable( "syqon-cli", Steps.studioConfigPath() );
   return ( found == null ) ? null : Steps.studioResolveBundle( found );
};

Steps.studioAvailable = function()
{
   try { return Steps.studioExecutable() != null; }
   catch ( e ) { return false; }
};

/* Every Prism takes Prism's options: tiling and the application blend. */
Steps.studioIsPrism = function( model )
{
   return /^prism-/.test( String( model || "" ) );
};

/* ---- the command line ---- */

/*
 * Pure, so the selftest can assert every flag. Mirrors buildStudioArgs():
 * tiling only for the tiled models (Prism, Parallax), application only for
 * Prism, one set of stage switches for Parallax, the Axiom stretch for
 * Axiom, and nothing extra for Deep Gradient. --stars-output and
 * --gradient-output are never asked for: Loom derives its stars plate by
 * unscreen (Steps.extractStars) and has no use for the extracted gradient.
 *
 * opts: { model, domain, input, output, application,
 *         parallax: { correction, reduction, deblur } }
 * where reduction is a 0-10 level and deblur a 0-1 strength, each 0/absent
 * for off -- Loom runs one Parallax stage per call, exactly as it does
 * with standalone Parallax, so each stage caches on its own.
 */
Steps.studioBuildArgs = function( opts )
{
   var args = [ "--model", opts.model,
                "--domain", opts.domain,
                "--precision", Steps.STUDIO_PRECISION ];

   var prism = Steps.studioIsPrism( opts.model );
   if ( prism || opts.model == Steps.STUDIO_MODEL_PARALLAX )
      args.push( "--tile-size", String( Steps.STUDIO_TILE_SIZE ),
                 "--overlap", String( Steps.STUDIO_OVERLAP ) );

   args = args.concat( Steps.studioModelArgs( opts, prism ) );

   // a fresh per-run path Loom owns; progress is wanted, so no --quiet
   args.push( "--overwrite", opts.input, opts.output );
   return args;
};

// The arguments only one model takes: Prism's blend, Parallax's stages, Axiom's stretch.
Steps.studioModelArgs = function( opts, prism )
{
   if ( prism )
      return [ "--application", format( "%.4f", opts.application ) ];
   if ( opts.model == Steps.STUDIO_MODEL_PARALLAX )
      return Steps.studioParallaxArgs( opts.parallax || {} );
   if ( opts.model == Steps.STUDIO_MODEL_STARLESS )
      /*
       * "auto" is Axiom's own stretch, which is what its contract asks for
       * on linear input; stretched input is declared "identity" so it is
       * not stretched a second time.
       */
      return [ "--axiom-stretch", ( opts.domain == "linear" ) ? "auto" : "identity" ];
   return [];
};

/*
 * Parallax's stages, each switched on or off explicitly; a level or a
 * strength follows only a stage that is on. `px.family` is the profile
 * (Config.parallaxFamily), Classic when absent.
 */
Steps.studioParallaxArgs = function( px )
{
   var reduction = px.reduction || 0;
   var deblur = px.deblur || 0;
   var args = [ "--family", Steps.studioFamilyOf( px.family ),
                "--correction", px.correction ? "true" : "false",
                "--reduction", ( reduction > 0 ) ? "true" : "false" ];
   if ( reduction > 0 )
      args.push( "--reduction-level", String( reduction ) );
   args.push( "--deblur", ( deblur > 0 ) ? "true" : "false" );
   if ( deblur > 0 )
      args.push( "--deblur-strength", format( "%.4f", deblur ) );
   return args;
};

/* Studio's failure contract, in its own words (exitCodeMessage()). */
Steps.studioExitMessage = function( code )
{
   switch ( code )
   {
   case 1: return "General CLI error.";
   case 2: return "Invalid option or incompatible input domain.";
   case 3: return "Input/output path error (existence, permissions, or overwrite policy).";
   case 4: return "Authentication or entitlement denied. Sign in through SyQon Studio " +
                  "and confirm this model is available to your account.";
   case 5: return "Inference failed; no output was produced.";
   case 6: return "Output encoding failed.";
   case 7: return "Output was saved, but the Studio handoff step failed.";
   case 130: return "Interrupted.";
   default: return "syqon-cli exited with code " + code + ".";
   }
};

/* ---- one run ---- */

/*
 * The input file: PixInsight's own XISF at 32-bit float, as SyQon_Studio.js
 * writes it. Its comment records why -- linear images written through
 * PixInsight's TIFF encoder came back black from the CLI -- and float is
 * what keeps linear samples exact through the round trip.
 */
Steps.studioSaveXisf = function( filePath, view )
{
   var F = new FileFormat( "XISF", false, true );
   if ( F.isNull )
      throw new Error( "SyQon Studio: the XISF format is not available" );
   var f = new FileFormatInstance( F );
   if ( f.isNull )
      throw new Error( "SyQon Studio: could not create an XISF writer" );
   var d = new ImageDescription;
   d.bitsPerSample = 32;
   d.ieeefpSampleFormat = true;
   if ( !f.create( filePath, "" ) )
      throw new Error( "SyQon Studio: could not create " + filePath );
   try
   {
      if ( !f.setOptions( d ) )
         throw new Error( "SyQon Studio: could not set 32-bit float options" );
      if ( !f.writeImage( view.image ) )
         throw new Error( "SyQon Studio: could not write " + filePath );
   }
   finally { f.close(); }
};

/*
 * sanitizeForStudio(), ported: non-finite samples to 0, the image scaled
 * into [0,1] if it exceeds it, then clipped. Studio's models are trained
 * on [0,1] and return BLACK for input carrying NaN or negatives. Returns
 * the scale, which the result is multiplied back by. Loom's plates are
 * normally already in range, so this is normally a no-op -- but it is
 * Studio's own precondition, and the cost of skipping it is a black plate.
 */
Steps.studioSanitize = function( view )
{
   Steps.syqonApplyPixelMath( view, "iif( $T == $T && abs( $T ) < 1e30, $T, 0 )" );
   var mx = view.image.maximum();
   var scale = ( isFinite( mx ) && mx > 1.01 ) ? mx : 1.0;
   if ( scale > 1.01 )
      Steps.syqonApplyPixelMath( view, "$T/" + format( "%.10f", scale ) );
   return scale;
};

/*
 * The absolute path syqon-cli prints on stdout at exit 0, or null. Loom
 * names the output itself, but the CLI's declaration is the authority on
 * where it actually wrote -- the reference prefers it for the same reason.
 */
Steps.studioDeclaredOutput = function( stdout )
{
   var lines = String( stdout || "" ).split( /\r\n|\r|\n/ );
   for ( var i = lines.length - 1; i >= 0; --i )
   {
      var s = lines[i].trim();
      if ( s.length > 3 && /\.xisf$/i.test( s ) )
         try { if ( File.exists( s ) ) return s; } catch ( e ) {}
   }
   return null;
};

/*
 * One syqon-cli run on `view`, in place: clone -> sanitise -> XISF ->
 * syqon-cli -> import. `opts` is studioBuildArgs' input without the file
 * paths. Every failure throws naming the operation and the view, the same
 * rule the standalone stages follow: a channel silently skipped is worse
 * than a hard failure.
 */
Steps.studioRun = function( view, opLabel, opts )
{
   var exePath = Steps.studioExecutable();
   if ( exePath == null )
      throw new Error( "SyQon Studio " + opLabel + " failed on " + view.id +
                       ": syqon-cli not found" );

   var targetWindow = view.isMainView ? view.window : view.mainView.window;
   if ( !targetWindow || targetWindow.isNull )
      throw new Error( "SyQon Studio " + opLabel + " failed on " + view.id +
                       ": no valid image window" );

   var stem = Steps.studioTempStem( view.id );
   var inPath = stem + "_input.xisf";
   var outPath = stem + "_output.xisf";
   var declared = null;

   var clone = null, before = null;
   try
   {
      // the plate as it was, when only the fine-scale change is to be kept
      if ( opts.fineScale > 0 )
         before = Steps.syqonCloneWindowForProcessing(
                     targetWindow, Util.freeWindowId(
                        Steps.syqonSanitizeFileName( view.id ) + "_before" ) );
      clone = Steps.syqonCloneWindowForProcessing(
                 targetWindow, Util.freeWindowId(
                    Steps.syqonSanitizeFileName( view.id ) + "_studio" ) );
      var scale = Steps.studioSanitize( clone.mainView );
      Steps.studioSaveXisf( inPath, clone.mainView );
      clone.forceClose();
      clone = null;

      var args = Steps.studioBuildArgs( Steps.withFiles( opts, inPath, outPath ) );
      Util.log( "studio", opLabel + " " + view.id + ": " + exePath + " " + args.join( " " ) );

      Steps.studioNoteKeychain();
      var res = Steps.syqonRunProcessBlocking( exePath, args, Steps.STUDIO_TIMEOUT_MS,
                   { text: Steps.studioWaitText, stage: "SyQon Studio " + opLabel + " → " + view.id } );
      declared = Steps.studioDeclaredOutput( res.stdout );
      var produced = ( declared != null ) ? declared : outPath;
      if ( !File.exists( produced ) )
         throw new Error( "SyQon Studio " + opLabel + " failed on " + view.id + ": " +
                          Steps.studioFailureText( opts.model, res ) );
      Steps.studioSession.entitled[opts.model] = true;

      Steps.studioApplyResult( produced, targetWindow, scale, opLabel, view.id );
      if ( before != null )
      {
         Steps.keepFineScaleChange( targetWindow.mainView, before.mainView, opts.fineScale );
         Util.log( "studio", opLabel + " " + view.id + ": kept only the change finer than " +
                             "sigma " + opts.fineScale + " px" );
      }
      Util.log( "studio", opLabel + " " + view.id + " complete" );
   }
   finally
   {
      if ( before != null )
         try { before.forceClose(); } catch ( e ) {}
      Steps.syqonCleanUp( clone, [ inPath, outPath, declared != outPath ? declared : null ] );
   }
};

/*
 * Keeps only the part of an edit finer than a Gaussian of `sigma` pixels:
 *
 *    view = before + ( change - gauss( change, sigma ) ),  change = view - before
 *
 * A denoiser's own work is at the scale of the noise, a pixel or two, and
 * survives whole; a level it shifted over a larger area -- the tile
 * offsets Prism 2.0 Advanced leaves on linear data, see
 * Steps.NOISE_LEVELS.studio2 -- is put back as it was. `before` must have
 * the same geometry as `view`. Truncated to [0,1], as the edit was.
 */
Steps.keepFineScaleChange = function( view, before, sigma )
{
   var img = view.image;
   var change = new ImageWindow( img.width, img.height, img.numberOfChannels,
                                 32, true, img.isColor,
                                 Util.freeWindowId( Steps.syqonSanitizeFileName( view.id ) + "_change" ) );
   try
   {
      change.mainView.beginProcess( UndoFlag_NoSwapFile );
      change.mainView.image.assign( img );
      change.mainView.endProcess();
      if ( !Steps.pixelMath( change.mainView, "$T - " + before.id ) )
         throw new Error( "could not measure the change on " + view.id );
      var P = new Convolution;
      P.mode = Convolution.Parametric;
      P.sigma = sigma;
      P.shape = 2.00;          // Gaussian
      P.aspectRatio = 1.00;
      P.rotationAngle = 0.00;
      if ( !P.executeOn( change.mainView ) )
         throw new Error( "could not smooth the change on " + view.id );
      if ( !Steps.pixelMath( view, "$T - " + change.mainView.id, true ) )
         throw new Error( "could not restore the large scales on " + view.id );
   }
   finally { change.forceClose(); }
};

// A fresh per-run path stem in the temp folder, made if it is missing.
Steps.studioTempStem = function( viewId )
{
   var dir = File.systemTempDirectory + "/SyQonStudioCLI";
   Util.ensureDirectory( dir );
   var tag = String( (new Date()).getTime() ) + "_" + Math.round( Math.random()*1e6 );
   return dir + "/" + Steps.syqonSanitizeFileName( viewId ) + "_" + tag;
};

// A copy of `opts` naming the run's input and output files.
Steps.withFiles = function( opts, inPath, outPath )
{
   var o = {};
   for ( var k in opts )
      o[k] = opts[k];
   o.input = inPath;
   o.output = outPath;
   return o;
};

/*
 * Opens the CLI's result and writes it over the target. A mono target
 * takes channel 0 of a colour result, and the input scale goes back on --
 * applyResultToTarget() in the reference.
 */
Steps.studioApplyResult = function( produced, targetWindow, scale, opLabel, viewId )
{
   var opened = ImageWindow.open( produced );
   if ( !opened || opened.length < 1 )
      throw new Error( "SyQon Studio " + opLabel + ": could not open " + produced );
   var ow = opened[0];
   try
   {
      var src = Steps.syqonResultSource( targetWindow, ow );
      var pm = new PixelMath;
      pm.useSingleExpression = true;
      pm.expression          = ( scale > 1.01 ) ? "(" + src + ")*" + format( "%.10f", scale )
                                                : src;
      pm.createNewImage      = false;
      pm.rescale             = false;
      pm.truncate            = true;
      pm.truncateLower       = 0;
      pm.truncateUpper       = 1;
      if ( !pm.executeOn( targetWindow.mainView ) )
         throw new Error( "SyQon Studio " + opLabel + ": could not apply the result to " +
                          viewId );
   }
   finally { try { ow.forceClose(); } catch ( e ) {} }
};
