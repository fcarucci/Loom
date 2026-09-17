/*
 * lib/Pipeline.js
 *
 * Orchestrates the Loom run: preflight validation, then load / correct /
 * register / linear-fit / combine / calibrate / save, using the wrappers
 * in Steps.js and the helpers in Util.js.
 */

#include <pjsr/UndoFlag.jsh>

var Pipeline = {};

Pipeline.REQUIRED_PROCESSES = [
   "SpectrophotometricFluxCalibration",
   "SpectrophotometricColorCalibration",
   "MultiscaleGradientCorrection",
   "StarAlignment",
   "LinearFit",
   "ChannelCombination"
];

// No hardcoded locations: where the MARS databases live is the user's
// business. Ask MultiscaleGradientCorrection what PixInsight has
// registered, exactly as the Gaia lookup asks the Gaia process.

/*
 * MARS availability cannot be determined from a freshly constructed
 * MultiscaleGradientCorrection: a new instance carries default parameters,
 * not the databases PixInsight has configured. So this does not guess.
 * Steps.mgc runs with useMARSDatabase = true and MGC itself reports the
 * problem if no database is registered -- a real error from the process
 * that owns the setting, rather than a preflight refusal based on a value
 * we cannot actually read.
 */

/*
 * A wall: nothing opens or processes until every check passes, so a
 * missing FITS keyword or a missing MARS database costs a dialog box
 * rather than a half-finished run.
 */
Pipeline.preflight = function( config )
{
   var problems = Util.validateSelection( config.paths, config.views );

   for ( var i = 0; i < Pipeline.REQUIRED_PROCESSES.length; ++i )
   {
      var p = Pipeline.REQUIRED_PROCESSES[i];
      if ( !Steps.moduleAvailable( p ) )
         problems.push( "Process not installed: " + p );
   }

   // GraXpert is only required when it is enabled.
   if ( config.useGraXpert && !Steps.moduleAvailable( "GraXpert" ) )
      problems.push( "Process not installed: GraXpert (disable it to proceed without)" );

   // MGC always runs on the broadband channels present in this selection,
   // so the MARS database is never optional. Fail loudly here rather than
   // discovering the absence mid-run or silently correcting without it.

   for ( var j = 0; j < Util.CHANNELS.length; ++j )
   {
      var key = Util.CHANNELS[j];
      var hasView = config.views && config.views[key];
      var hasPath = config.paths[key];
      if ( !hasView && !hasPath )
         continue;

      if ( hasView )
      {
         var vw = ImageWindow.windowById( config.views[key] );
         if ( vw == null )
         {
            problems.push( "View no longer open for " + key + ": " + config.views[key] );
            continue;
         }
         if ( vw.isNull )
         {
            problems.push( "Selected view no longer open for " + key + ": " + config.views[key] );
            continue;
         }
         if ( Util.isBroadband( key ) && Util.keywordValue( vw.keywords, "FILTER" ) === null )
            problems.push( "No FILTER keyword in " + key + " (view " + config.views[key] + ")" +
                           " (SPFC cannot proceed; the script will not guess a filter)" );
      }
      else
      {
         var path = config.paths[key];
         if ( !File.exists( path ) )
         {
            problems.push( "File not found for " + key + ": " + path );
            continue;
         }
         if ( Util.isBroadband( key ) )
         {
            // header only: this used to be a full ImageWindow.open of every
            // broadband master, on every run, to read one keyword
            var kws = Pipeline.readImageInfo( path ).keywords;
            if ( Util.keywordValue( kws, "FILTER" ) === null )
               problems.push( "No FILTER keyword in " + key + ": " + path +
                              " (SPFC cannot proceed; the script will not guess a filter)" );
         }
      }
   }

   // An empty output folder is valid and means "do not write anything":
   // results are left as open windows in the current project. Only a
   // folder that was actually specified has to exist.

   return problems;
};

/* Keywords plus geometry from a single open, so the dialog can show size. */
/*
 * Reads keywords and geometry WITHOUT decoding pixel data.
 *
 * The obvious implementation -- ImageWindow.open() then read .keywords --
 * loads and decompresses the whole image to extract a few header fields.
 * For a master folder that is ruinous: ~51 masterLight files at 300-850 MB
 * each is tens of gigabytes read off an external drive to learn FILTER,
 * XPIXSZ and the dimensions.
 *
 * FileFormatInstance.open() returns the ImageDescription array from the
 * header alone; pixels are only read by the separate readImage() call,
 * which is never made here. Keywords come from the instance directly.
 *
 * Falls back to the old whole-image path if the format cannot be
 * instantiated, so an exotic format still works, just slowly.
 */
/*
 * Within one run the same file is asked about more than once -- preflight
 * checks FILTER, the load loop wants FILTER and INSTRUME, the dialog lists
 * geometry -- and re-reading a header off an external drive for each is
 * waste. Keyed on identity, not just path, so a file replaced under the
 * same name is not served from a stale entry.
 */
Pipeline.imageInfoCache = {};

Pipeline.imageInfoCacheKey = function( path )
{
   try
   {
      var fi = new FileInfo( path );
      if ( !fi.exists )
         return null;
      return path + "|" + fi.size + "|" + fi.lastModified.toISOString();
   }
   catch ( e ) { return null; }
};

/*
 * What the PSB will be called, without the extension.
 *
 * The project name if there is one; "Loom" only when the box was left
 * empty and nothing could be derived from the paths. The dialog shows the
 * same string on its checkbox, so the two go through here rather than each
 * having its own idea of the fallback.
 */
Pipeline.psbBaseName = function( config )
{
   var n = ( config && config.projectName ) ? String( config.projectName ).trim() : "";
   return ( n.length > 0 ) ? n : "Loom";
};

/*
 * A project name guessed from where the masters live.
 *
 * PJSR exposes NOTHING about the open PixInsight project -- there is no
 * project name, path or title anywhere in the object reference -- so the
 * name of "the current project" cannot be read. The next most useful thing
 * is the folder the masters came out of, which is how these are organised
 * in practice: .../Elephant Trunk/master/masterLight_...xisf
 *
 * Generic container folders are skipped so the answer is the target's name
 * rather than the layout's.
 */
Pipeline.GENERIC_FOLDERS = [ "master", "masters", "integration", "integrations",
                             "calibrated", "registered", "drizzle", "autocrop",
                             "light", "lights", "output", "outputs", "tiff",
                             "cache", "tmp", "temp" ];

Pipeline.projectNameFromPath = function( path )
{
   if ( !path || String( path ).length == 0 )
      return "";
   var dir = "";
   try { dir = File.extractDirectory( path ); }
   catch ( e ) { return ""; }
   var parts = String( dir ).split( "/" );
   for ( var i = parts.length - 1; i >= 0; --i )
   {
      var p = parts[i];
      if ( p.length == 0 )
         continue;
      if ( Pipeline.GENERIC_FOLDERS.indexOf( p.toLowerCase() ) >= 0 )
         continue;
      return p;
   }
   return "";
};

/* The first source path a run has, whichever channel it belongs to. */
Pipeline.projectNameFor = function( config )
{
   if ( !config || !config.paths )
      return "";
   for ( var i = 0; i < Util.CHANNELS.length; ++i )
   {
      var p = config.paths[ Util.CHANNELS[i] ];
      if ( p && String( p ).length > 0 )
      {
         var n = Pipeline.projectNameFromPath( p );
         if ( n.length > 0 )
            return n;
      }
   }
   return "";
};

/*
 * Reads geometry and keywords from `path`'s header alone, without decoding
 * a single pixel.
 *
 * Returns { info, why }: `info` is the header fields on success and null on
 * failure, and `why` then carries the diagnostic string the caller prints
 * before it falls back to a full read. Every failure point has its own
 * reason -- the reason is the whole point of this probe, because the
 * fallback is expensive enough that "it did not work" is not a useful
 * thing to read in a log.
 */
Pipeline.tryHeaderRead = function( path )
{
   try
   {
      var ext = File.extractExtension( path );
      var F = new FileFormat( ext, true /*toRead*/, false /*toWrite*/ );
      if ( F.isNull )
         return { info: null, why: "no reader for extension '" + ext + "'" };

      var f = new FileFormatInstance( F );
      if ( f.isNull )
         return { info: null, why: "could not instantiate the " + ext + " reader" };

      var d = f.open( path, "verbosity 0" );
      if ( d == null || d.length < 1 )
      {
         try { f.close(); } catch ( e ) {}
         return { info: null, why: "the reader returned no image description" };
      }

      var info = {
         keywords: F.canStoreKeywords ? f.keywords : [],
         width: d[0].width,
         height: d[0].height
      };
      // The header is already in hand; a close that fails now costs
      // nothing and must not send the caller down the full-read path.
      try { f.close(); } catch ( e1 ) {}
      return { info: info, why: null };
   }
   catch ( e2 )
   {
      return { info: null, why: String( e2 ) };
   }
};

Pipeline.readImageInfo = function( path )
{
   var ck = Pipeline.imageInfoCacheKey( path );
   if ( ck != null && Pipeline.imageInfoCache[ck] != null )
      return Pipeline.imageInfoCache[ck];

   var probe = Pipeline.tryHeaderRead( path );
   var info = probe.info;

   /*
    * The fallback reads and decodes the ENTIRE image to recover a few
    * header fields -- roughly a gigabyte per master here. It used to be
    * reached silently whenever the reader could not be instantiated, so a
    * run could be doing full reads on every file with nothing in the log
    * to say so. It is always announced now.
    */
   if ( info == null )
   {
      Util.warn( "read", "header-only read unavailable for " + path +
                         " (" + ( probe.why || "unknown reason" ) + ")" +
                         " -- falling back to a FULL read of the image" );
      var w = ImageWindow.open( path );
      if ( w.length == 0 )
         info = { keywords: [], width: 0, height: 0 };
      else
      {
         info = { keywords: w[0].keywords,
                  width: w[0].mainView.image.width,
                  height: w[0].mainView.image.height };
         w[0].forceClose();
      }
   }

   if ( ck != null )
      Pipeline.imageInfoCache[ck] = info;
   return info;
};

/*
 * Stage cache wiring.
 *
 * Each broadband channel (L, R, G, B) runs solve -> spfc -> mgc ->
 * graxpert -> register, in that order; narrowband channels (H, S, O) skip
 * straight to register. Cache.chainKey threads a running key through
 * whichever of these stages actually apply to a channel, so a change to
 * any stage's parameters invalidates that stage and everything after it,
 * with no separate dependency bookkeeping.
 *
 * Pipeline.buildStageKeys is pure -- it only hashes strings -- so the
 * invalidation behaviour is unit-tested directly in selftest.js without a
 * running PixInsight instance.
 */
Pipeline.STAGE_ORDER = [ "solve", "spfc", "mgc", "graxpert",
                         "aberration", "register",
                         "combine", "solveRGB", "spfcRGB", "spccRGB",
                         "sharpenRGB", "extractRGB", "stretchRGB", "denoiseRGB",
                         "paletteCombine", "paletteSpcc", "paletteNorm",
                         "paletteSharpen", "paletteExtract", "paletteStretch",
                         "paletteDenoise",
                         "extractL", "stretchL" ];

/*
 * Star reduction, detail sharpening and noise reduction USED to be excluded
 * from this list, on the reasoning that changing one must not invalidate the
 * expensive upstream work. That reasoning was wrong, and measurably so: keys
 * CHAIN, so a stage at the END of a chain cannot invalidate anything before
 * it -- changing the star-reduction level rehashes only its own stage and
 * everything upstream still hits. Leaving them out bought nothing and cost a
 * full re-run of every one of them on every single run.
 *
 * Confirmed 2026-09-15 by replaying the stored chains against the masters'
 * current fingerprints: every cached stage through spccRGB reproduced
 * exactly, so the upstream cache was working perfectly and the entire
 * observed "no caching" was this uncached tail -- a SyQon Parallax star
 * reduction at "high" on the composite, plus the whole HSO palette, redone
 * from scratch each time.
 */

/*
 * The clean-reference branch (Pipeline.measureCleanWhiteBalance) is
 * deliberately NOT a set of stages here. It produces three numbers, not
 * pixels, so it carries its own few-bytes JSON cache entry instead of a
 * gigabyte-per-stage image chain.
 */

/*
 * Builds the ordered chain of cache keys for one channel, starting from
 * `sourceKey` (a Cache.fingerprintFile/fingerprintView result, or a prior
 * stage's key when continuing a chain -- e.g. register continues from the
 * channel's own graxpert key). `params` supplies one entry per stage this
 * channel actually goes through; a stage absent from `params` is skipped
 * entirely rather than hashed with empty parameters, so narrowband
 * channels (register only) and L (no register) produce chains of
 * different lengths from R/G/B without special-casing here.
 *
 * Returns an array of { stage, key, params }, in pipeline order.
 */
/*
 * Parameters for the two composite-level stages, or null when the stage
 * would do nothing. Shared by the RGB composite and every palette so the
 * two paths cannot drift apart -- they run the identical operations and
 * must therefore key on the identical things.
 *
 * These mirror the no-op tests inside Steps.correctComposite and
 * Steps.denoise; a stage that no-ops still hashes, so an always-present
 * stage would shift every downstream key for a step that changes nothing.
 */
Pipeline.compositeSharpenParams = function( config )
{
   var tool = config.sharpenTool;
   if ( !tool || tool == "none" )
      return null;
   var stars  = ( config.starReduction && config.starReduction != "none" ) ?
                config.starReduction : null;
   var detail = ( config.detailLevel && config.detailLevel != "none" ) ?
                config.detailLevel : null;
   if ( stars == null && detail == null )
      return null;
   /*
    * The amounts, not just the labels. "medium" is a name for a number
    * that lives in Steps.SHARPEN_LEVELS, and remapping that ladder changes
    * the pixels without changing the name -- so a key built from the label
    * alone would go on serving the old result as current. See
    * Steps.sharpenAmountFor.
    */
   return { tool: tool, stars: stars, detail: detail,
            starsAmount:  Steps.sharpenAmountFor( tool, "stars", stars ),
            detailAmount: Steps.sharpenAmountFor( tool, "detail", detail ) };
};

/*
 * Star extraction parameters, or null when no tool is selected. Only the
 * tool matters: Loom exposes no per-tool settings, so two runs with the
 * same tool produce the same split.
 */
Pipeline.starExtractionParams = function( config )
{
   var tool = config.starTool;
   if ( !tool || tool == "none" )
      return null;
   /*
    * The stars target and the colour recovery both change the pixels the
    * extraction stage produces, so both belong in its key. Without them a
    * cached entry from a previous setting would be served unchanged -- which
    * is exactly what happened when the stars target moved and the old plates
    * came straight back out of cache.
    */
   return { tool: tool,
            starsTarget: Steps.STRETCH_STARS_TARGET,
            stretchStars: !!config.stretch };
};

/*
 * Stretch parameters, or null when the user has it off.
 *
 * The stretch itself is computed FROM THE IMAGE -- black point at its own
 * minimum, midtone sending its own sky median to the target -- so nothing
 * image-specific belongs in the cache key. What belongs is the target and
 * whether the stretch is linked, because changing either changes the pixels.
 */
Pipeline.stretchParams = function( config, linked )
{
   if ( !config.stretch )
      return null;
   /*
    * keepLinear belongs in the key because it decides whether the stage stores
    * its linear companion at all; a cached entry from the opposite setting
    * would otherwise be served with the companion missing.
    */
   var method = config.stretchMethod || Steps.STRETCH_METHOD_MTF;
   if ( method == Steps.STRETCH_METHOD_MAS )
      /*
       * The method AND the numbers it runs with: changing any of them
       * changes the pixels, and a key naming only the method would serve a
       * plate stretched with the old values. scaleSeparation is not among
       * them because Loom does not set it -- see Steps.MAS_PARAMETERS.
       */
      return { method: method, linked: !!linked,
               keepLinear: !!config.keepLinear,
               mas: Steps.MAS_PARAMETERS };
   return { target: Steps.STRETCH_SKY_TARGET, linked: !!linked,
            keepLinear: !!config.keepLinear };
};

Pipeline.compositeDenoiseParams = function( config )
{
   var tool = config.noiseTool;
   if ( !tool || tool == "none" )
      return null;
   var level = config.noiseLevel;
   if ( !level || level == "none" )
      return null;
   /*
    * Whether the input is already stretched changes Prism's pre-stretch
    * target, so it changes the pixels and belongs in the key.
    */
   // The amount as well as the label, for the same reason the sharpen
   // params carry theirs -- see Steps.noiseAmountFor.
   return { tool: tool, level: level, stretched: !!config.stretch,
            amount: Steps.noiseAmountFor( tool, level ) };
};

Pipeline.SKIP_CACHE = "loom-skip-cache";

/*
 * Names, marks, shows and records one result window. Every deliverable goes
 * through here so a starless frame, a stars frame and an unsplit composite
 * are all published identically.
 */
Pipeline.publish = function( win, id, reg, keepIds, results, resultKey )
{
   /*
    * Loud, not silent. A missing window here means something closed a
    * result before it could be named, and the first time that happened the
    * only symptom was an output quietly not being there.
    */
   if ( !Pipeline.windowIsUsable( win ) )
   {
      Util.error( "output", id + ": its window is gone before it could be " +
                            "named, so this result is lost; the rest of the " +
                            "run is unaffected" );
      return null;
   }
   var freeId = Util.freeWindowId( id );
   win = Pipeline.detachIfCached( win, freeId, reg );
   try { win.mainView.id = freeId; }
   catch ( e ) { Util.warn( "output", "could not rename " + id + ": " + e ); }
   try { Steps.markAsOutput( win, id ); }
   catch ( e ) { Util.warn( "output", "could not mark " + id + ": " + e ); }
   try { win.show(); } catch ( e ) {}
   try { keepIds.push( win.mainView.id ); } catch ( e ) {}
   if ( results != null && resultKey )
      results[resultKey] = win;
   return win;
};

/*
 * Writes every result as a 16-bit TIFF into config.exportDir, when one is set.
 *
 * Runs LAST, after the results have their final names, so the files are named
 * the way the windows are. An export failure is reported and does not cost the
 * run: the windows are the deliverable, the files are a convenience.
 */
Pipeline.exportResults = function( results, config )
{
   var dir = config.exportDir;
   if ( !dir || String( dir ).length == 0 )
      return;

   /*
    * Exporting a LINEAR plate to 16 bits would posterise it -- the whole
    * signal sits in the bottom percent of the range. Refuse rather than write
    * a quietly ruined file.
    */
   if ( !config.stretch )
   {
      Util.warn( "export", "not exporting: the results are linear, and 16-bit " +
                           "TIFF would posterise them. Enable the stretch, or " +
                           "save from PixInsight in a format that holds " +
                           "floating point." );
      return;
   }

   try
   {
      if ( !File.directoryExists( dir ) )
         File.createDirectory( dir, true );
   }
   catch ( e )
   {
      Util.error( "export", "could not create " + dir + ": " + e );
      return;
   }

   /*
    * The layered PSB, when asked for. Written before the individual TIFFs
    * so that a failure here still leaves the plates on disk -- the PSB is
    * the convenience, the plates are the deliverable.
    */
   if ( config.exportPsb )
   {
      try
      {
         Steps.exportPsb( results, dir, Pipeline.psbBaseName( config ) );
      }
      catch ( e )
      {
         Util.error( "export", "the layered PSB was not written (" + e +
                               "); the individual TIFFs are unaffected" );
      }
   }

   var names = [];
   for ( var k in results )
      if ( results[k] && Pipeline.windowIsUsable( results[k] ) )
         names.push( k );
   names.sort();

   var written = 0;
   for ( var i = 0; i < names.length; ++i )
   {
      var id = names[i];
      try
      {
         var path = Steps.exportTiff16( results[id], dir, id );
         Util.operation( "export", "16-bit TIFF", null, id );
         Util.log( "export", path );
         ++written;
      }
      catch ( e2 )
      {
         Util.warn( "export", id + " could not be exported (" + e2 + ")" );
      }
   }
   Util.log( "export", written + " of " + names.length + " result(s) written to " + dir );
};

/*
 * Stages that produce a SECOND image alongside their own result, stored beside
 * the stage's key as a companion. Both cases exist because the extra image
 * cannot be recomputed from the stage's output: the stars frame is what the
 * extraction removed, and the linear composite is what the stretch overwrote.
 *
 * Without this they would vanish on a cache hit, which is precisely how the
 * stars frames were lost once already.
 */
Pipeline.STAGE_COMPANIONS = {
   extractL:       "stars",
   extractRGB:     "stars",
   paletteExtract: "stars",
   stretchRGB:     "linear",
   paletteStretch: "linear"
};

/*
 * Opens a channel's source file, if it has one and it is not open yet.
 *
 * A channel record is built WITHOUT opening anything: the cache key needs
 * only a stat (Cache.fingerprintFile) and the FITS keywords come from the
 * header alone (Pipeline.readImageInfo). The pixels are needed only when a
 * stage actually has to run, and processChain knows when that is -- see
 * the hitIndex logic below. A fully cached run therefore never reads a
 * master at all, which on a seven-channel set is ~8 GB it used to read to
 * produce results that were already on disk.
 *
 * Channels sourced from an open view are duplicated up front and have no
 * loader, as do chains whose first stage CREATES its window (the RGB
 * composite's combine): for both, chan.window is already set and this is a
 * no-op.
 */
Pipeline.ensureLoaded = function( chan )
{
   if ( chan != null && chan.window == null && typeof chan.load == "function" )
      chan.load();
   return chan;
};

Pipeline.buildStageKeys = function( sourceKey, params )
{
   var chain = [];
   var prev = sourceKey;
   for ( var i = 0; i < Pipeline.STAGE_ORDER.length; ++i )
   {
      var stage = Pipeline.STAGE_ORDER[i];
      if ( !params || !( stage in params ) )
         continue;
      var p = params[stage];
      var key = Cache.chainKey( prev, stage, p );
      /*
       * Extraction stages produce a second image (the stars) that shares
       * this key -- see Cache.storeCompanion. Marked here so processChain
       * knows to store and reload it with the stage.
       */
      var entry = { stage: stage, key: key, params: p };
      /*
       * The linear companion is only produced when the user asked to keep it;
       * otherwise every run would store a second full-size image nobody wants.
       */
      var comp = Pipeline.STAGE_COMPANIONS[stage];
      if ( comp == "linear" && !( p && p.keepLinear ) )
         comp = null;
      if ( comp )
         entry.companion = comp;
      chain.push( entry );
      prev = key;
   }
   return chain;
};

/*
 * Is a cached entry usable as a whole?
 *
 * An extraction stage's entry has two halves: the stage result under the
 * stage key, and a companion (the stars frame) alongside it. A stage key
 * with a missing companion is a half-written entry -- it must be treated
 * as a miss and re-extracted, rather than continued from with no stars
 * image.
 *
 * This is a query and nothing else, so processChain can settle which
 * stage it is reusing BEFORE it starts executing. The loop used to
 * discover a half-written entry mid-flight and demote its own controlling
 * variable to say so, which meant the store-on-success path existed twice.
 */
Pipeline.cacheEntryComplete = function( entry )
{
   if ( entry == null || Cache.lookup( entry.key ) == null )
      return false;
   if ( entry.companion == null )
      return true;
   return Cache.lookupCompanion( entry.key, entry.companion ) != null;
};

/*
 * A stage that a LATER cached stage has superseded: it is not run and its
 * result is not loaded, because the later entry already holds it.
 */
Pipeline.skipSupersededStage = function( chan, entry, found, hitStage, label, reg )
{
   if ( found )
      Util.log( "cache", label + " HIT " + entry.key.substring( 0, 12 ) +
                "... (superseded by later cached stage " + hitStage + ")" );
   else
      Util.log( "cache", label + " MISS (no entry; skipped -- " +
                hitStage + " is cached)" );
   /*
    * A superseded stage is skipped because a later one already holds
    * its result -- but an extraction stage ALSO produced a stars
    * frame, and no later stage carries that. Skipping past it without
    * loading the companion silently loses the stars image whenever
    * anything downstream (noise reduction) is cached. So the
    * companion is picked up even when the stage itself is superseded.
    */
   if ( entry.companion != null && found )
   {
      var scomp = entry.companion;
      var superseded = Cache.loadCompanion( entry.key, scomp,
                          Util.freeWindowId( chan.key + "_" + scomp ) );
      if ( superseded != null )
      {
         reg.add( superseded );
         chan[scomp] = superseded;
      }
      else
         Util.warn( "cache", label + " superseded and its " + scomp +
                             " companion is missing; this run produces no " +
                             scomp + " frame for " + chan.key );
   }
};

/*
 * Puts a cached stage result in place of running the stage: the window
 * becomes the channel's current window, and `loadedStars` -- already
 * opened by the caller, because whether it opened at all decides whether
 * this is attempted -- becomes its companion.
 *
 * Returns false when the entry is present but will not load, having said
 * so; the caller then recomputes the stage.
 */
Pipeline.reuseCachedStage = function( chan, entry, loadedStars, label, reg )
{
   var loaded = Cache.load( entry.key, Util.freeWindowId( chan.key + "_" + entry.stage ) );
   if ( !loaded )
   {
      if ( loadedStars != null )
         try { loadedStars.forceClose(); } catch ( e3 ) {}
      Util.warn( "cache", label + " cache entry present but failed to load -- recomputing" );
      return false;
   }

   if ( loadedStars != null )
   {
      reg.add( loadedStars );
      chan[ entry.companion ] = loadedStars;
   }
   reg.add( loaded );
   var old = chan.window;
   chan.window = loaded;
   chan.view = loaded.mainView;
   /*
    * `old` is null when the chain's first stage CREATES its window
    * rather than transforming one -- the RGB composite's `combine`
    * stage. Guard both the close and the error message, or a hit on
    * that stage throws from inside the catch handler.
    */
   if ( old != null )
   {
      reg.forget( old );
      var oldId = "?";
      try { oldId = old.mainView.id; } catch ( e0 ) {}
      try { old.forceClose(); }
      catch ( e ) { Util.warn( "cache", "could not close " + oldId + ": " + e ); }
   }
   try
   {
      if ( !loaded.hasAstrometricSolution )
         Util.warn( "cache", label + " loaded window has no astrometric solution" );
   }
   catch ( e2 ) { /* property not readable on this window; not fatal */ }
   Util.log( "cache", label + " HIT " + entry.key.substring( 0, 12 ) + "..." );
   return true;
};

/*
 * Runs a stage for real and caches what it produced. Returns false when
 * the runner reported SKIP_CACHE, in which case nothing was stored.
 *
 * A runner returning SKIP_CACHE ran but did not produce the result
 * this stage is supposed to represent -- the tolerated-failure path,
 * where the composite is kept as it is and a warning is logged.
 * Storing that under the stage's key would cache the FAILURE and
 * serve it forever after, so it is deliberately not stored and the
 * stage simply retries on the next run.
 */
Pipeline.runAndStoreStage = function( chan, entry, runners )
{
   if ( runners[ entry.stage ]( Pipeline.ensureLoaded( chan ) ) === Pipeline.SKIP_CACHE )
      return false;
   Cache.store( entry.key, chan.window,
                { channel: chan.key, stage: entry.stage, params: entry.params } );
   if ( entry.companion != null && chan[ entry.companion ] != null )
      Cache.storeCompanion( entry.key, entry.companion, chan[ entry.companion ] );
   return true;
};

/*
 * Cache-aware execution of a channel's stage chain. `chan` is the entry
 * from Pipeline.run's `chans` map ({ key, window, view, ... }); `runners`
 * maps each stage name in `chain` to a function( chan ) that performs the
 * real work, mutating chan.window/chan.view in place exactly as the
 * uncached pipeline did.
 *
 * With caching off this just runs every stage. With caching on it first
 * looks up every key in the chain (cheap -- Cache.lookup is a file-exists
 * check) and finds the LAST stage with a cached entry: everything up to
 * and including that stage is skipped, its window loaded in its place,
 * and only the stages after it actually run. Every stage still gets a
 * HIT/MISS log line, including ones skipped because a later stage was
 * cached, so a wrong reuse is never silent.
 *
 * Three kinds of stage come out of that: before the hit (superseded),
 * the hit itself (reused, unless its entry turns out to be unusable),
 * and after the hit (run and stored). A hit whose entry is unusable
 * joins the third kind.
 */
Pipeline.processChain = function( chan, chain, config, reg, runners )
{
   if ( !config.useCache )
   {
      for ( var i = 0; i < chain.length; ++i )
         runners[ chain[i].stage ]( Pipeline.ensureLoaded( chan ) );
      return;
   }

   var lookups = [], hitIndex = -1;
   for ( var i = 0; i < chain.length; ++i )
   {
      var found = config.ignoreCache ? null : Cache.lookup( chain[i].key );
      lookups.push( found );
      if ( found )
         hitIndex = i;
   }
   var hitComplete = ( hitIndex >= 0 ) && Pipeline.cacheEntryComplete( chain[hitIndex] );

   for ( var i = 0; i < chain.length; ++i )
   {
      var entry = chain[i];
      var label = chan.key + " " + entry.stage;

      if ( i < hitIndex )
      {
         Pipeline.skipSupersededStage( chan, entry, lookups[i],
                                       chain[hitIndex].stage, label, reg );
         continue;
      }

      var reason = config.ignoreCache ? "cache ignored for this run" : "no entry";
      if ( i == hitIndex )
      {
         var loadedStars = ( hitComplete && entry.companion != null )
                         ? Cache.loadCompanion( entry.key, entry.companion,
                              Util.freeWindowId( chan.key + "_" + entry.companion ) )
                         : null;
         if ( entry.companion != null && loadedStars == null )
         {
            Util.warn( "cache", label + " has no " + entry.companion +
                                " companion; recomputing" );
            reason = "incomplete entry";
         }
         else if ( Pipeline.reuseCachedStage( chan, entry, loadedStars, label, reg ) )
            continue;
      }

      Util.log( "cache", label + " MISS (" + reason + ")" );
      if ( !Pipeline.runAndStoreStage( chan, entry, runners ) )
         Util.log( "cache", label + " not cached (the step did not complete)" );
   }
};


/*
 * Measures an SPCC white balance on a composite built from the channels as
 * they were BEFORE any aberration correction.
 *
 * Returns [r,g,b] gains, or throws if the pre-correction pixels cannot be
 * recovered. See Steps.applyWhiteBalance for the measurements that make this
 * necessary -- in short, SPCC reads stellar photometry, and a per-channel
 * aberration correction changes that photometry by a different amount in
 * every channel, so SPCC run on corrected channels returns a white balance
 * that is simply wrong (red halved, on real data).
 *
 * The whole composite is thrown away afterwards; only the three gains
 * survive, and they are cached as a few bytes of JSON rather than another
 * gigabyte of pixels.
 */
Pipeline.cleanWhiteBalanceCachePath = function( key )
{
   return Cache.dir() + "/" + key + ".wb.json";
};

Pipeline.measureCleanWhiteBalance = function( chans, config, reg, common,
                                              lumInstrume, refView, refFingerprint )
{
   var trio = [ "R", "G", "B" ];

   for ( var t = 0; t < trio.length; ++t )
      if ( !chans[trio[t]].cleanKey )
         throw new Error( "no pre-correction cache key for " + trio[t] );

   var cacheKey = Cache.hash( "cleanwb|" + chans.R.cleanKey + "|" +
                              chans.G.cleanKey + "|" + chans.B.cleanKey +
                              "|crop:" + common.x0 + "," + common.y0 + "," +
                              common.x1 + "," + common.y1 +
                              "|" + JSON.stringify( config.filters || {} ) +
                              "|" + String( lumInstrume ) );
   var cachePath = Pipeline.cleanWhiteBalanceCachePath( cacheKey );

   if ( config.useCache && !config.ignoreCache && File.exists( cachePath ) )
   {
      try
      {
         var cached = JSON.parse( File.readTextFile( cachePath ) );
         if ( cached && cached.length == 3 )
         {
            Util.log( "cache", "clean white balance HIT " +
                               cacheKey.substring( 0, 12 ) + "..." );
            return cached;
         }
      }
      catch ( e ) { Util.warn( "cache", "unreadable clean white balance: " + e ); }
   }
   Util.log( "cache", "clean white balance MISS " + cacheKey.substring( 0, 12 ) + "..." );

   var temps = [], cleanViews = {};
   try
   {
      for ( var i = 0; i < trio.length; ++i )
      {
         var k = trio[i];
         /*
          * Cache the REGISTERED clean reference, not just the three numbers
          * this branch ultimately produces.
          *
          * Without this, any change to the white-balance key -- a different
          * filter choice, a new crop -- repeats three full registrations
          * (~12s each) plus a combine, a plate solve, SPFC and SPCC, about
          * two minutes, to rederive three numbers. The registrations depend
          * only on the source channel and the reference, so they survive
          * changes that invalidate the measurement itself.
          */
         var regKey = Cache.chainKey( chans[k].cleanKey, "cleanRegister",
                                      { ref: refFingerprint } );
         var registeredWin = null;
         if ( config.useCache && !config.ignoreCache )
         {
            registeredWin = Cache.load( regKey, Util.freeWindowId( k + "_cleanreg" ) );
            if ( registeredWin != null )
               Util.log( "cache", k + " cleanRegister HIT " +
                                  regKey.substring( 0, 12 ) + "..." );
         }
         if ( registeredWin != null )
         {
            temps.push( registeredWin );
            cleanViews[k] = registeredWin.mainView;
            continue;
         }
         Util.log( "cache", k + " cleanRegister MISS" );

         var w = Cache.load( chans[k].cleanKey,
                             Util.freeWindowId( k + "_cleanref" ) );
         if ( w == null )
            throw new Error( "the pre-correction result for " + k +
                             " is not in the cache" );
         temps.push( w );

         /*
          * NOTE: refView is L, and L has ALREADY been cropped to `common` by
          * the time this runs -- the crop is applied in place, so the same
          * view object is now the cropped one. Registering against it
          * therefore lands these channels directly in the cropped frame, the
          * same size as the corrected channels. Cropping again here would
          * apply `common` twice, since those coordinates are relative to the
          * UNcropped frame.
          */
         var registered = Steps.register( w.mainView, refView );
         temps.push( registered );
         if ( config.useCache )
            Cache.store( regKey, registered,
                         { channel: k, stage: "cleanRegister",
                           params: { ref: refFingerprint } } );
         cleanViews[k] = registered.mainView;
      }

      var refId = Util.freeWindowId( "RGB_cleanref" );
      var refWin = Steps.combineRGB( cleanViews.R, cleanViews.G,
                                     cleanViews.B, refId );
      temps.push( refWin );

      Steps.solve( refWin.mainView );
      Steps.spfc( refWin.mainView, null, "RGB", lumInstrume, config.filters || {} );
      Steps.spccRGB( refWin.mainView, lumInstrume, config.filters || {} );

      var factors = Steps.readWhiteBalanceFactors( refWin.mainView );
      if ( factors == null )
         throw new Error( "SPCC did not record white balance factors" );

      Util.log( "whitebalance", "measured on uncorrected channels: " +
                                factors[0].toFixed( 6 ) + " / " +
                                factors[1].toFixed( 6 ) + " / " +
                                factors[2].toFixed( 6 ) );

      if ( config.useCache )
         try { File.writeTextFile( cachePath, JSON.stringify( factors ) ); }
         catch ( e ) { Util.warn( "cache", "could not cache white balance: " + e ); }

      return factors;
   }
   finally
   {
      for ( var c = temps.length - 1; c >= 0; --c )
      {
         try { reg.forget( temps[c] ); } catch ( e ) {}
         try { if ( !temps[c].isNull ) temps[c].forceClose(); } catch ( e ) {}
      }
   }
};

Pipeline.run = function( config )
{
   var reg = new Util.Registry;

   /*
    * Snapshot of everything already open. The Registry only tracks windows
    * Loom creates itself; processes create their own (StarAlignment's
    * registered outputs, ChannelCombination's result, any process that
    * spawns a working image). Anything that appears during the run and is
    * not a deliberate result gets closed by sweepNewWindows below, so no
    * working window is ever left behind.
    */
   /*
    * Close the PREVIOUS run's outputs first. They are marked with a FITS
    * keyword, so this only ever touches windows Loom itself produced --
    * never the user's own views. Without it, each run's results survive as
    * "pre-existing" into the next and pile up (RGB, RGB_1, RGB_2 ...).
    */
   var stale = ImageWindow.windows, staleClosed = [];
   for ( var si = 0; si < stale.length; ++si )
      if ( Steps.isLoomOutput( stale[si] ) )
      {
         var sid = stale[si].mainView.id;
         try { stale[si].forceClose(); staleClosed.push( sid ); }
         catch ( e ) { Util.warn( "cleanup", "could not close " + sid + ": " + e ); }
      }
   if ( staleClosed.length )
      Util.log( "cleanup", "closed " + staleClosed.length +
                           " output(s) from a previous run: " + staleClosed.join( ", " ) );

   var preexisting = {};
   var pre = ImageWindow.windows;
   for ( var pi = 0; pi < pre.length; ++pi )
      preexisting[pre[pi].mainView.id] = true;
   var results = {};

   try
   {
      // load -- a slot may name an open view or a file on disk. A view
      // always wins over a file path for the same channel; the user's own
      // view is never modified in place, only a duplicate is worked on.
      var chans = {};
      for ( var i = 0; i < Util.CHANNELS.length; ++i )
      {
         var key = Util.CHANNELS[i];
         var w, sourceKey;
         if ( config.views && config.views[key] )
         {
            var srcWin = ImageWindow.windowById( config.views[key] );
            if ( srcWin == null || srcWin.isNull )
               throw new Error( "View no longer open for " + key + ": " + config.views[key] );
            sourceKey = Cache.fingerprintView( srcWin.mainView );
            w = new ImageWindow( srcWin.mainView.image.width,
                                 srcWin.mainView.image.height,
                                 srcWin.mainView.image.numberOfChannels,
                                 srcWin.mainView.image.bitsPerSample,
                                 srcWin.mainView.image.isReal,
                                 srcWin.mainView.image.isColor,
                                 Util.freeWindowId( key + "_work" ) );
            w.mainView.beginProcess( UndoFlag_NoSwapFile );
            w.mainView.image.assign( srcWin.mainView.image );
            w.mainView.endProcess();
            w.keywords = srcWin.keywords;

            // The astrometric solution lives in XISF properties, not FITS
            // keywords, so assigning pixels and keywords does NOT carry it
            // over. Without this the duplicate looks unsolved and the
            // pipeline re-solves an already-solved master.
            try
            {
               if ( srcWin.hasAstrometricSolution )
               {
                  w.copyAstrometricSolution( srcWin );
                  Util.log( "load", key + " - astrometric solution copied" );
               }
            }
            catch ( e )
            {
               Util.warn( "load", key + " - could not copy astrometric solution: " + e );
            }
            Util.log( "load", key + " <- view " + config.views[key] + " (duplicated)" );
         }
         else if ( config.paths[key] )
         {
            /*
             * NOT opened here. The key chain needs a stat and two header
             * keywords, and nothing downstream needs the pixels unless a
             * stage actually runs -- see Pipeline.ensureLoaded. Opening
             * every master up front read ~8 GB on a seven-channel set
             * before the first cache lookup.
             */
            var path = config.paths[key];
            sourceKey = Cache.fingerprintFile( path );
            if ( sourceKey == null )
               throw new Error( "Could not read " + key + ": " + path );
            var info = Pipeline.readImageInfo( path );
            chans[key] = { key: key, path: path, window: null, view: null,
                           filter: Util.keywordValue( info.keywords, "FILTER" ),
                           instrume: Util.keywordValue( info.keywords, "INSTRUME" ),
                           sourceKey: sourceKey, currentKey: sourceKey,
                           load: function()
                           {
                              if ( this.window != null )
                                 return this.window;
                              var ws = ImageWindow.open( this.path );
                              if ( ws.length == 0 )
                                 throw new Error( "Could not open " + this.key +
                                                  ": " + this.path );
                              var win = ws[0];
                              win.mainView.id = Util.freeWindowId( this.key + "_work" );
                              // registered the moment it exists: an unregistered
                              // window is one reg.closeAll() cannot clean up, and
                              // this file has produced that bug twice
                              reg.add( win );
                              this.window = win;
                              this.view = win.mainView;
                              Util.log( "load", this.key + " <- " + this.path );
                              return win;
                           } };
            continue;
         }
         else
            continue;
         reg.add( w );
         chans[key] = { key: key, window: w, view: w.mainView,
                        filter: Util.keywordValue( w.keywords, "FILTER" ),
                        instrume: Util.keywordValue( w.keywords, "INSTRUME" ),
                        // sourceKey identifies the untouched input; currentKey
                        // is the running cache key, advanced as stages run.
                        // Narrowband channels never run solve/spfc/mgc/graxpert,
                        // so their currentKey stays the source fingerprint until
                        // register chains from it directly.
                        sourceKey: sourceKey, currentKey: sourceKey };
      }

      // solve (skipped if already solved) + correct, broadband only, on
      // native uninterpolated pixels. Absent channels are skipped; a
      // narrowband-only run corrects L alone.
      for ( var b = 0; b < Util.BROADBAND.length; ++b )
      {
         var bk = Util.BROADBAND[b];
         if ( !chans[bk] ) continue;
         var bc = chans[bk];

         var chosenFilter = config.filters ? config.filters[bk] : null;
         var qe = Steps.deviceCurveForImage( bc.instrume );
         var mgcCfg = Steps.configuredMGC( config.marsPath );
         var marsFiles = ( mgcCfg && mgcCfg.marsDatabaseFiles ) ?
                         mgcCfg.marsDatabaseFiles.slice().sort() : [];

         var bStages = {
            solve: {},
            // channel key + chosen filter + resolved QE curve name: anything
            // that changes what SPFC actually calibrates against.
            spfc: { channel: bk, filter: chosenFilter, qe: qe ? qe.name : null },
            // the MARS database file list, not just "MGC ran" -- a different
            // database gives a different correction from the same input.
            mgc: { marsFiles: marsFiles },
            // enabled flag AND smoothing: toggling GraXpert off must not
            // silently reuse a result computed with it on, and vice versa.
            graxpert: { enabled: !!config.useGraXpert, smoothing: config.smoothing },
            // Sharpening runs per channel on native, uninterpolated pixels
            // -- before registration deliberately, since resampling spreads
            // whatever aberration is already there. The tool is part of the
            // key: the same level means different pixels under BXT vs SyQon.
            /*
             * Aberration correction STAYS per channel, before registration:
             * it fixes star shape on native, uninterpolated pixels, and
             * registration's resampling would otherwise spread whatever
             * aberration is present.
             *
             * Star reduction and detail sharpening have MOVED to the
             * finished, calibrated composite, where a linked stretch keeps
             * the colour correction intact -- see Steps.correctComposite.
             */
            aberration:    { tool: config.sharpenTool || "none",
                             photometry: "linearfit-v1" }
         };
         var bChain = Pipeline.buildStageKeys( bc.sourceKey, bStages );
         bc.currentKey = bChain.length ? bChain[bChain.length - 1].key : bc.sourceKey;

         // The last stage before any correction -- where the clean reference
         // branch starts from. Captured here because the chain is the only
         // place these keys exist.
         for ( var gk = 0; gk < bChain.length; ++gk )
            if ( bChain[gk].stage == "graxpert" )
               bc.cleanKey = bChain[gk].key;

         ( function( channel )
         {
            var runners = {
               solve: function( c ) { Steps.solve( c.view ); },
               spfc: function( c )
               {
                  Steps.spfc( c.view, c.filter, channel, c.instrume,
                              config.filters ? config.filters[channel] : null );
               },
               mgc: function( c ) { Steps.mgc( c.view, config.marsPath ); },
               graxpert: function( c )
               {
                  if ( config.useGraXpert )
                     Steps.graxpert( c.view, config.smoothing );
               },
               aberration: function( c )
               {
                  // Always runs when a tool is chosen -- the safe operation,
                  // and the one that actually fixes star shape. Per channel,
                  // unlinked, on native pixels.
                  if ( config.sharpenTool && config.sharpenTool != "none" )
                     Steps.aberration( c.view, config.sharpenTool, false, channel );
               }
            };
            Pipeline.processChain( chans[channel], bChain, config, reg, runners );
         } )( bk );

         Pipeline.checkAbort( "corrected " + bk );
      }

      // register everything to L; L is the reference and is never resampled
      var refView = chans.L.view;
      /*
       * The reference is identified by L's own CACHE KEY, not by
       * fingerprinting its window.
       *
       * Cache.fingerprintView hashes view.id, and L's working copy is
       * named differently on every run (L_work, L_graxpert_1, ...), so a
       * fingerprint never matched across runs and register missed every
       * time even when the pixels were identical.
       *
       * L's chained key already identifies exactly which data, corrected
       * exactly how, is being registered against -- and it is stable
       * across runs by construction. Registering to a different L, or to
       * the same L corrected differently, changes that key and correctly
       * invalidates every dependent register entry.
       */
      var refFingerprint = chans.L.currentKey;
      for ( var c = 0; c < Util.CHANNELS.length; ++c )
      {
         var ck = Util.CHANNELS[c];
         if ( ck == "L" || !chans[ck] ) continue;
         /*
          * Swap the channel over to its registered window; the original
          * working copy is finished with. Without this the pipeline would
          * keep combining unaligned channels.
          */
         var rChain = Pipeline.buildStageKeys( chans[ck].currentKey,
                                               { register: { ref: refFingerprint } } );
         ( function( channel )
         {
            var runners = {
               register: function( cc )
               {
                  var registered = Steps.register( cc.view, refView );
                  reg.add( registered );
                  var oldWin = cc.window;
                  cc.window = registered;
                  cc.view = registered.mainView;
                  reg.forget( oldWin );
                  try { oldWin.forceClose(); }
                  catch ( e ) { Util.warn( "register", "could not close " + channel +
                                          " working copy: " + e ); }
               }
            };
            Pipeline.processChain( chans[channel], rChain, config, reg, runners );
         } )( ck );
         /*
          * Advance the channel's running key past registration.
          *
          * Without this, currentKey still named the channel as it was
          * BEFORE being registered, and everything built from it -- the RGB
          * composite, and now the palettes -- keyed on pre-registration
          * pixels. Registration depends on the reference (L), so a changed
          * L re-registered R/G/B while leaving the composite's key
          * identical: a stale composite served for a different reference.
          * Wrong pixels, silently, which is the one thing a cache must
          * never do.
          */
         chans[ck].currentKey = rChain.length ? rChain[rChain.length-1].key
                                              : chans[ck].currentKey;
         Pipeline.checkAbort( "registered " + ck );
      }

      /*
       * Crop every channel to the area all of them actually cover.
       * Registration leaves black fill where a channel has no data; without
       * this the outputs have dead edges and, worse, differ in size.
       */
      var rects = [], rectKeys = [];
      for ( var vr = 0; vr < Util.CHANNELS.length; ++vr )
      {
         var vk = Util.CHANNELS[vr];
         if ( !chans[vk] ) continue;
         rects.push( Steps.validRect( chans[vk].view, 4 ) );
         rectKeys.push( vk );
      }
      var common = Util.intersectRects( rects );
      if ( common == null )
         throw new Error( "The channels have no overlapping imaged area; " +
                          "they cannot be combined." );
      /*
       * Guard against a detection failure silently destroying the frame.
       * Registration overlap between channels of the same target is
       * normally >90%; anything under half the reference area means the
       * edge scan misread something, not that the data is that small.
       */
      var refImg = chans.L.view.image;
      var refArea = refImg.width * refImg.height;
      var cropArea = ( common.x1 - common.x0 ) * ( common.y1 - common.y0 );
      var pct = 100 * cropArea / refArea;
      Util.log( "crop", "common area " + (common.x1-common.x0) + " x " +
                        (common.y1-common.y0) + " at (" + common.x0 + "," + common.y0 +
                        ") - " + pct.toFixed( 1 ) + "% of the reference frame" );
      if ( pct < 50 )
         throw new Error( "The common imaged area is only " + pct.toFixed( 1 ) +
                          "% of the reference frame. That is almost certainly a " +
                          "misdetected edge rather than real data; refusing to " +
                          "crop away most of the image." );
      for ( var cr = 0; cr < rectKeys.length; ++cr )
         Steps.cropTo( chans[rectKeys[cr]].view, common );
      Pipeline.checkAbort();

      /*
       * Halo reduction: match every channel's PSF to the widest.
       *
       * Runs after registration and cropping, where all channels sit on
       * L's grid and their sigmas are directly comparable in pixels.
       * Destructive by nature -- the sharpest channel is blurred down --
       * which is acceptable in LRGB because L carries the detail.
       */
      if ( config.reduceHalos )
      {
         /*
          * L is measured but NEVER convolved. Matching is destructive: it
          * blurs the sharper channels down to the widest one. That cost is
          * only acceptable because L carries the detail and the colour
          * channels only carry colour -- blurring L would throw away the
          * very thing the trade was made to protect. On this data L is
          * 3.18" against G's 4.72", so an unguarded loop degrades it badly.
          */
         var measured = [], widest = 0;
         for ( var hi = 0; hi < Util.CHANNELS.length; ++hi )
         {
            var hk = Util.CHANNELS[hi];
            if ( !chans[hk] || hk == "L" ) continue;
            var psf = Steps.measurePSF( chans[hk].view );
            if ( psf == null )
            {
               Util.warn( "halos", hk + ": no PSF fit, leaving it untouched" );
               continue;
            }
            measured.push( { key: hk, sigma: psf.sigma, n: psf.n } );
            if ( psf.sigma > widest ) widest = psf.sigma;
            Util.log( "halos", hk + " sigma=" + psf.sigma.toFixed( 3 ) +
                               " (FWHM " + Util.sigmaToFWHM( psf.sigma ).toFixed( 2 ) +
                               " px, " + psf.n + " stars)" );
         }

         if ( measured.length < 2 )
            Util.log( "halos", "skipped: fewer than two channels could be measured" );
         else
         {
            Util.log( "halos", "matching to the widest, sigma=" + widest.toFixed( 3 ) );
            for ( var hj = 0; hj < measured.length; ++hj )
            {
               var m = measured[hj];
               var add = Util.sigmaToReach( widest, m.sigma );
               if ( add <= 0 )
               {
                  Util.log( "halos", m.key + ": already the widest, unchanged" );
                  continue;
               }
               Util.log( "halos", m.key + ": convolving by sigma=" + add.toFixed( 3 ) );
               Steps.convolveBy( chans[m.key].view, add );
            }
         }
         Pipeline.checkAbort();
      }

      // linear fit the narrowband set to its lowest-median member
      var medians = {};
      var nb = [ "H", "S", "O" ];
      for ( var n = 0; n < nb.length; ++n )
         if ( chans[nb[n]] )
            medians[nb[n]] = Steps.medianOfCentre( chans[nb[n]].view, 0.6 );

      var refKey = Util.minMedianKey( medians );
      var wantPalettes = ( config.palettes || [] ).length > 0;
      if ( refKey !== null && Object.keys( medians ).length > 1 )
      {
         if ( wantPalettes )
         {
            /*
             * Offset only. LinearFit assumes two images are the same signal
             * at different scales -- true across frames of one filter, false
             * across Ha, SII and OIII, which are different lines with
             * different morphology. Measured on real data it scaled H down
             * ~8x (H max 0.105 against S's 0.800), leaving Ha with about 23
             * levels of 16-bit range across the nebula. That is what
             * posterised NarrowbandNormalization and turned the palette
             * green. SPCC in narrowband mode does the calibration properly,
             * on the composite, further down.
             */
            var views = [];
            for ( var mo = 0; mo < nb.length; ++mo )
               if ( chans[nb[mo]] ) views.push( chans[nb[mo]].view );
            Util.log( "background", "narrowband sky floors matched to " + refKey +
                                    " by offset; line ratios preserved for SPCC" );
            Steps.matchBackgroundOffset( views, chans[refKey].view );
         }
         else
         {
            Util.log( "linearfit", "reference is " + refKey );
            for ( var m = 0; m < nb.length; ++m )
               if ( chans[nb[m]] && nb[m] != refKey )
                  Steps.linearFit( chans[nb[m]].view, chans[refKey].view );
         }
      }
      else
         Util.log( "linearfit", "skipped: fewer than two narrowband channels" );

      // The composite has no camera of its own, so its QE curve follows L.
      var lumInstrume = chans.L ? chans.L.instrume : null;

      // combine, then solve and calibrate the combined image.
      // Skipped entirely when the RGB group is absent (narrowband-only run) --
      // a narrowband-only selection produces no RGB.
      /*
       * A trustworthy white balance has to come from channels the aberration
       * correction has not touched -- see Steps.applyWhiteBalance. Only
       * needed when a correction actually ran.
       */
      /*
       * Still required: aberration correction runs per channel, before the
       * combine, so SPCC would otherwise measure corrected stellar
       * photometry and return a wrong white balance (measured: red halved).
       * Star reduction and detail moved to the composite, but aberration
       * did not, so this stays.
       */
      var cleanFactors = null;
      if ( chans.R && chans.G && chans.B &&
           Steps.aberrationWillRun( config.sharpenTool ) )
      {
         try
         {
            cleanFactors = Pipeline.measureCleanWhiteBalance(
               chans, config, reg, common, lumInstrume, refView, refFingerprint );
         }
         catch ( e )
         {
            Util.warn( "whitebalance",
               "could not build the uncorrected reference (" + e + "). " +
               "Falling back to SPCC on the corrected composite, which is " +
               "known to produce a colour cast on aberration-corrected data." );
            cleanFactors = null;
         }
      }

      var rgbWin = null, rgbId = null, rgbStars = null, rgbLinear = null;
      if ( chans.R && chans.G && chans.B )
      {
         rgbId = Util.freeWindowId( "RGB" );

         /*
          * The composite's chain starts from the three contributing
          * channels' keys plus the crop rectangle -- that is exactly what
          * determines its pixels. Its stages (combine, solve, SPFC, SPCC)
          * then chain from there, so re-running with identical inputs
          * skips ~46s of catalog work rather than repeating it.
          */
         /*
          * `halos` MUST be part of this key. PSF matching runs in memory
          * after the register stage -- it is not a cached stage of its own,
          * so without it here the composite chain hashes identically whether
          * matching ran or not, and enabling "Reduce halos" would serve the
          * unmatched composite straight from cache and look like a no-op.
          */
         var rgbSource = Cache.hash( "rgb|" + chans.R.currentKey + "|" +
                                     chans.G.currentKey + "|" + chans.B.currentKey +
                                     "|crop:" + common.x0 + "," + common.y0 + "," +
                                     common.x1 + "," + common.y1 +
                                     "|halos:" + ( config.reduceHalos ? "1" : "0" ) );

         var rgbParams = {
            combine:  {},
            solveRGB: {},
            // the filters and QE curve drive both calibrations, exactly as
            // for the per-channel SPFC
            spfcRGB:  { filters: config.filters || {}, instrume: lumInstrume },
            // The white balance source is part of the key: factors measured
            // on uncorrected channels give different pixels from SPCC run
            // directly on the corrected composite.
            spccRGB:  { filters: config.filters || {}, instrume: lumInstrume,
                        whiteBalance: cleanFactors ? cleanFactors : "direct" }
         };
         /*
          * Only added when they will actually do something. A stage present
          * in the chain hashes even when its runner no-ops, so including
          * "sharpen with no tool" would change every downstream key for a
          * step that does nothing.
          */
         if ( Pipeline.compositeSharpenParams( config ) != null )
            rgbParams.sharpenRGB = Pipeline.compositeSharpenParams( config );
         /*
          * Extraction sits between sharpening and noise reduction: star
          * reduction still acts on a frame that has stars in it, while the
          * stars frame is spared the denoiser entirely and noise reduction
          * works only on the starless.
          */
         if ( Pipeline.starExtractionParams( config ) != null )
            rgbParams.extractRGB = Pipeline.starExtractionParams( config );
         /*
          * The stretch acts on the STARLESS plate, after extraction. The
          * stars plate was already stretched inside the extraction stage,
          * from its own clone -- see Steps.extractStars.
          */
         if ( Pipeline.stretchParams( config, true ) != null )
            rgbParams.stretchRGB = Pipeline.stretchParams( config, true );
         if ( Pipeline.compositeDenoiseParams( config ) != null )
            rgbParams.denoiseRGB = Pipeline.compositeDenoiseParams( config );
         var rgbChain = Pipeline.buildStageKeys( rgbSource, rgbParams );

         var rgbHolder = { key: "RGB", window: null, view: null };
         var rgbRunners = {
            combine: function( h )
            {
               h.window = Steps.combineRGB( chans.R.view, chans.G.view,
                                            chans.B.view, rgbId );
               h.view = h.window.mainView;
               reg.add( h.window );
            },
            solveRGB: function( h ) { Steps.solve( h.view ); },
            spfcRGB:  function( h )
            {
               Steps.spfc( h.view, null, "RGB", lumInstrume, config.filters || {} );
            },
            spccRGB:  function( h )
            {
               if ( cleanFactors != null )
                  Steps.applyWhiteBalance( h.view, cleanFactors );
               else
                  Steps.spccRGB( h.view, lumInstrume, config.filters || {} );
            },
            sharpenRGB: function( h )
            {
               Pipeline.checkAbort( "sharpening RGB" );
               try
               {
                  Steps.correctComposite( h.view, config.sharpenTool,
                                          config.starReduction, config.detailLevel, "RGB" );
               }
               catch ( e )
               {
                  Util.warn( "sharpen", "RGB could not be corrected (" + e +
                                        "); the composite is kept as it is" );
                  return Pipeline.SKIP_CACHE;
               }
            },
            extractRGB: function( h )
            {
               Pipeline.checkAbort( "extracting stars from RGB" );
               var split = Steps.extractStars( h.window, config.starTool, "RGB",
                                               !!config.stretch );
               if ( split == null )
                  return Pipeline.SKIP_CACHE;
               reg.add( split.stars );
               h.stars = split.stars;
            },
            stretchRGB: function( h )
            {
               Pipeline.checkAbort( "stretching RGB" );
               /*
                * The stretch overwrites the composite in place, so the linear
                * version has to be taken now or not at all. It is stored as
                * this stage's companion, which is what keeps it alive when the
                * stage later comes back from cache.
                */
               if ( config.keepLinear )
                  try
                  {
                     h.linear = Steps.syqonCloneWindowForProcessing(
                                   h.window, Util.freeWindowId( "RGB" + "_linear" ) );
                     try
                     {
                        if ( h.window.hasAstrometricSolution )
                           h.linear.copyAstrometricSolution( h.window );
                     }
                     catch ( eS ) {}
                     reg.add( h.linear );
                  }
                  catch ( eL )
                  {
                     Util.warn( "stretch", "could not keep the linear copy (" +
                                           eL + "); the stretch proceeds" );
                  }
               try { Steps.stretchBy( config.stretchMethod, h.view, true, "RGB starless" ); }
               catch ( e )
               {
                  Util.warn( "stretch", "RGB could not be stretched (" + e +
                                        "); the composite is kept linear" );
                  return Pipeline.SKIP_CACHE;
               }
            },
            denoiseRGB: function( h )
            {
               Pipeline.checkAbort( "denoising RGB" );
               try { Steps.denoise( h.view, config.noiseTool, config.noiseLevel, "RGB",
                                     !!config.stretch ); }
               catch ( e )
               {
                  Util.warn( "denoise", "RGB could not be denoised (" + e +
                                        "); the composite is kept as it is" );
                  return Pipeline.SKIP_CACHE;
               }
            }
         };

         Pipeline.checkAbort( "combining and calibrating RGB" );
         Pipeline.processChain( rgbHolder, rgbChain, config, reg, rgbRunners );
         rgbWin = rgbHolder.window;
         rgbStars = rgbHolder.stars || null;
         rgbLinear = rgbHolder.linear || null;

      }
      else
         Util.log( "combine", "skipped: no RGB group supplied" );

      /*
       * Narrowband palette. A second, independent composite built from the
       * narrowband channels -- it does not replace the RGB one, and either
       * can be produced without the other.
       *
       * Deliberately NOT flux- or colour-calibrated: a palette is an
       * aesthetic mapping of emission lines onto RGB, not a photometric
       * rendition, so SPFC/SPCC would be meaningless here.
       */
      var paletteWins = [];
      var wanted = config.palettes || [];
      for ( var wi = 0; wi < wanted.length; ++wi )
      {
         var pal = wanted[wi];
         var have = [];
         for ( var pn = 0; pn < Util.NARROWBAND.length; ++pn )
            if ( chans[Util.NARROWBAND[pn]] )
               have.push( Util.NARROWBAND[pn] );

         var missing = Util.paletteMissing( pal, have );
         if ( missing == null )
            throw new Error( "Unknown palette: " + pal );
         if ( missing.length > 0 )
            throw new Error( "The " + pal + " palette needs " +
                             missing.join( " and " ) + ", which " +
                             ( missing.length == 1 ? "was" : "were" ) + " not supplied." );

         var map = Util.PALETTES[pal];
         var pId = Util.freeWindowId( pal );
         Util.log( "palette", pal + ": R=" + map[0] + " G=" + map[1] + " B=" + map[2] );

         /*
          * The palette is a cached chain, exactly like the RGB composite.
          *
          * It used to be built from scratch on every run -- combine, solve,
          * SPCC, normalise, star reduction, noise reduction -- while the
          * channels feeding it came straight out of cache. On a repeat run
          * with a SyQon star reduction that is minutes of guaranteed rework
          * for a byte-identical result.
          *
          * The source key names the three contributing channels by their
          * post-registration keys plus the crop and halo state: that, and
          * nothing else, determines the palette's pixels. The palette NAME
          * is in the key too, so SHO and HOO built from the same three
          * channels cannot collide.
          */
         var palSource = Cache.hash( "palette|" + pal + "|" +
                                     chans[map[0]].currentKey + "|" +
                                     chans[map[1]].currentKey + "|" +
                                     chans[map[2]].currentKey +
                                     "|crop:" + common.x0 + "," + common.y0 + "," +
                                     common.x1 + "," + common.y1 +
                                     "|halos:" + ( config.reduceHalos ? "1" : "0" ) );

         var palParams = {
            paletteCombine: {},
            // bandwidth drives the emission-line calibration directly
            paletteSpcc: { palette: pal, bandwidth: config.narrowbandBandwidth }
         };
         /*
          * Omitted entirely when off, rather than carried with an enabled
          * flag: a stage that is present but does nothing still hashes and
          * still stores a full-size copy of its own input, which is a
          * gigabyte of cache per palette for a no-op. The following stage
          * then chains from paletteSpcc instead, which is exactly right --
          * its input really is different pixels.
          */
         if ( config.narrowbandNormalize )
            palParams.paletteNorm = { palette: pal };
         if ( Pipeline.compositeSharpenParams( config ) != null )
            palParams.paletteSharpen = Pipeline.compositeSharpenParams( config );
         if ( Pipeline.starExtractionParams( config ) != null )
            palParams.paletteExtract = Pipeline.starExtractionParams( config );
         if ( Pipeline.stretchParams( config, true ) != null )
            palParams.paletteStretch = Pipeline.stretchParams( config, true );
         if ( Pipeline.compositeDenoiseParams( config ) != null )
            palParams.paletteDenoise = Pipeline.compositeDenoiseParams( config );

         var palChain = Pipeline.buildStageKeys( palSource, palParams );
         var palHolder = { key: pal, window: null, view: null };
         var palRunners = {
            paletteCombine: function( h )
            {
               Pipeline.checkAbort( "building " + pal );
               h.window = Steps.combineRGB( chans[map[0]].view, chans[map[1]].view,
                                            chans[map[2]].view, pId );
               h.view = h.window.mainView;
               reg.add( h.window );
            },
            /*
             * Calibrate the palette photometrically. SPCC needs an
             * astrometric solution; ChannelCombination inherits one from the
             * channels, but solve if it did not come across. A calibration
             * failure must not cost the composite -- the uncalibrated
             * palette is still useful, so it is kept and simply not cached.
             */
            paletteSpcc: function( h )
            {
               try
               {
                  if ( !h.window.hasAstrometricSolution )
                  {
                     Util.log( "solve", pal + ": no inherited solution, solving" );
                     Steps.solve( h.view );
                  }
                  Steps.spccNarrowband( h.view, pal, config.narrowbandBandwidth );
               }
               catch ( e )
               {
                  Util.warn( "spcc", pal + " could not be calibrated (" + e +
                                     "); the palette is kept uncalibrated" );
                  return Pipeline.SKIP_CACHE;
               }
            },
            /*
             * Neutral normalisation, after calibration and before any
             * sharpening: it is a colour operation and belongs with the
             * colour work, while sharpening and noise reduction act on the
             * final rendition.
             */
            paletteNorm: function( h )
            {
               try { Steps.narrowbandNormalize( h.view, pal, pal ); }
               catch ( e )
               {
                  Util.warn( "nbnorm", pal + " could not be normalised (" + e +
                                       "); the palette is kept as it is" );
                  return Pipeline.SKIP_CACHE;
               }
            },
            paletteSharpen: function( h )
            {
               Pipeline.checkAbort( "sharpening " + pal );
               try
               {
                  Steps.correctComposite( h.view, config.sharpenTool,
                                          config.starReduction, config.detailLevel, pal );
               }
               catch ( e )
               {
                  Util.warn( "sharpen", pal + " could not be corrected (" + e +
                                        "); the palette is kept as it is" );
                  return Pipeline.SKIP_CACHE;
               }
            },
            paletteExtract: function( h )
            {
               Pipeline.checkAbort( "extracting stars from " + pal );
               var split = Steps.extractStars( h.window, config.starTool, pal,
                                               !!config.stretch );
               if ( split == null )
                  return Pipeline.SKIP_CACHE;
               reg.add( split.stars );
               h.stars = split.stars;
            },
            paletteStretch: function( h )
            {
               Pipeline.checkAbort( "stretching " + pal );
               /*
                * The stretch overwrites the composite in place, so the linear
                * version has to be taken now or not at all. It is stored as
                * this stage's companion, which is what keeps it alive when the
                * stage later comes back from cache.
                */
               if ( config.keepLinear )
                  try
                  {
                     h.linear = Steps.syqonCloneWindowForProcessing(
                                   h.window, Util.freeWindowId( pal + "_linear" ) );
                     try
                     {
                        if ( h.window.hasAstrometricSolution )
                           h.linear.copyAstrometricSolution( h.window );
                     }
                     catch ( eS ) {}
                     reg.add( h.linear );
                  }
                  catch ( eL )
                  {
                     Util.warn( "stretch", "could not keep the linear copy (" +
                                           eL + "); the stretch proceeds" );
                  }
               try { Steps.stretchBy( config.stretchMethod, h.view, true, pal + " starless" ); }
               catch ( e )
               {
                  Util.warn( "stretch", pal + " could not be stretched (" + e +
                                        "); the palette is kept linear" );
                  return Pipeline.SKIP_CACHE;
               }
            },
            paletteDenoise: function( h )
            {
               Pipeline.checkAbort( "denoising " + pal );
               try { Steps.denoise( h.view, config.noiseTool, config.noiseLevel, pal,
                                     !!config.stretch ); }
               catch ( e )
               {
                  Util.warn( "denoise", pal + " could not be denoised (" + e +
                                        "); the palette is kept as it is" );
                  return Pipeline.SKIP_CACHE;
               }
            }
         };

         Pipeline.processChain( palHolder, palChain, config, reg, palRunners );
         var pw = palHolder.window;

         paletteWins.push( { name: pal, window: pw,
                             stars: palHolder.stars || null,
                             linear: palHolder.linear || null } );
      }

      /*
       * L is split LAST, after every consumer of it is finished.
       *
       * It cannot be split in the per-channel chain with the other
       * broadband work, which is where this started: L is the registration
       * reference, and handing StarAlignment a starless reference makes it
       * report "0 stars found" and fail every channel. L is also the
       * reference the clean white balance is measured against. So the split
       * waits until the composites and palettes are built and nothing will
       * look at L again.
       *
       * Its key chains from L's own post-correction key plus the crop --
       * the crop is what the earlier stages' keys do not cover, and it
       * changes L's pixels.
       */
      if ( chans.L && Pipeline.starExtractionParams( config ) != null )
      {
         var lSource = Cache.hash( "lstars|" + chans.L.currentKey +
                                   "|crop:" + common.x0 + "," + common.y0 + "," +
                                   common.x1 + "," + common.y1 +
                                   "|halos:" + ( config.reduceHalos ? "1" : "0" ) );
         var lStages = { extractL: Pipeline.starExtractionParams( config ) };
         if ( Pipeline.stretchParams( config, false ) != null )
            lStages.stretchL = Pipeline.stretchParams( config, false );
         var lChain = Pipeline.buildStageKeys( lSource, lStages );
         var lRunners = {
            extractL: function( c )
            {
               Pipeline.checkAbort( "extracting stars from L" );
               var split = Steps.extractStars( c.window, config.starTool, "L",
                                               !!config.stretch );
               if ( split == null )
                  return Pipeline.SKIP_CACHE;
               reg.add( split.stars );
               c.stars = split.stars;
            },
            stretchL: function( c )
            {
               Pipeline.checkAbort( "stretching L" );
               try { Steps.stretchBy( config.stretchMethod, c.view, false, "L starless" ); }
               catch ( e )
               {
                  Util.warn( "stretch", "L could not be stretched (" + e +
                                        "); it is kept linear" );
                  return Pipeline.SKIP_CACHE;
               }
            }
         };
         Pipeline.processChain( chans.L, lChain, config, reg, lRunners );
      }

      // Results stay as open windows in the current project; Loom never
      // writes them to disk. Saving is the user's decision, made with
      // PixInsight's own Save As once they have looked at the result.
      for ( var s = 0; s < Util.CHANNELS.length; ++s )
      {
         var sk = Util.CHANNELS[s];
         if ( !chans[sk] ) continue;
         results[sk] = chans[sk].window;
      }
      if ( rgbWin !== null )
         results.RGB = rgbWin;

      // hand the keepers to the user, close the rest: L, H, S, O, and RGB
      // (when produced) survive; every other window (working copies of
      // R/G/B once combined, etc.) is closed via the Registry.
      // Only the deliberate results survive: L and any narrowband, plus
      // the RGB composite. R/G/B are intermediates consumed by the
      // combination -- keeping them (which an empty output folder used to
      // do) just litters the workspace with *_work_registered windows.
      /*
       * When a palette was built, the narrowband channels were the
       * ingredients for it and are swept with the other intermediates.
       * Without a palette they are results in their own right and survive.
       * L and RGB are unaffected either way.
       */
      var keep = ( paletteWins.length > 0 ) ? [ "L" ] : [ "L", "H", "S", "O" ];
      if ( paletteWins.length > 0 )
      {
         var dropped = [];
         for ( var nb2 = 0; nb2 < Util.NARROWBAND.length; ++nb2 )
            if ( chans[Util.NARROWBAND[nb2]] )
               dropped.push( Util.NARROWBAND[nb2] );
         if ( dropped.length )
            Util.log( "cleanup", "narrowband consumed by the palette, not kept: " +
                                 dropped.join( ", " ) );
      }
      for ( var k = 0; k < keep.length; ++k )
         if ( chans[keep[k]] )
            reg.forget( chans[keep[k]].window );

      // Swept channels must not be reported as results: their windows are
      // about to be closed, so the entry would point at nothing.
      if ( paletteWins.length > 0 )
         for ( var dr = 0; dr < Util.NARROWBAND.length; ++dr )
            delete results[ Util.NARROWBAND[dr] ];
      if ( rgbWin !== null )
         reg.forget( rgbWin );
      /*
       * Palettes are results too, and they are registered like everything
       * else -- so without this they are closed by closeAll() moments before
       * the code below tries to name them. That is why every palette run
       * ended with "its window is gone before it could be named": the
       * registry did exactly what it was told.
       */
      for ( var pf = 0; pf < paletteWins.length; ++pf )
         reg.forget( paletteWins[pf].window );
      /*
       * The stars frames are results as well, and they are registered like
       * everything else -- exactly the trap the comment above describes.
       * Missing them produced a run with every starless plate present and
       * no stars plate at all: closeAll() closed them, and the naming pass
       * below then found nothing to name.
       */
      if ( chans.L && chans.L.stars )
         reg.forget( chans.L.stars );
      if ( rgbStars != null )
         reg.forget( rgbStars );
      if ( rgbLinear != null )
         reg.forget( rgbLinear );
      for ( var ps = 0; ps < paletteWins.length; ++ps )
      {
         if ( paletteWins[ps].stars )
            reg.forget( paletteWins[ps].stars );
         if ( paletteWins[ps].linear )
            reg.forget( paletteWins[ps].linear );
      }
      reg.closeAll();

      // Anything a process created and nobody claimed goes now.
      /*
       * Give the survivors their plain channel names -- L, H, S, O, RGB --
       * falling back to L_1, RGB_1 and so on when the id is taken (the
       * user's own project routinely has views called L and RGB).
       * They arrive here called L_work / L_graxpert_1 / R_graxpert_registered
       * depending on which stages ran or came from cache, which reads as
       * debris rather than results.
       *
       * The LOOMOUT keyword, not the name, is what lets the next run
       * recognise and close these -- so plain names are safe.
       */
      var keepIds = [];
      for ( var kk = 0; kk < keep.length; ++kk )
      {
         var kkey = keep[kk];
         if ( !chans[kkey] ) continue;
         var kw = chans[kkey].window;
         /*
          * A split channel is published as two frames and the unsplit one
          * is not kept: starless and stars screen back together into it
          * exactly, so holding a third copy of the same data buys nothing.
          */
         var kSplit = ( chans[kkey].stars != null );
         var kid = kSplit ? ( kkey + "_starless" ) : kkey;
         // AFTER the detach: the earlier assignment points at a window
         // that detach has since replaced and closed.
         kw = Pipeline.publish( kw, kid, reg, keepIds, results,
                                kSplit ? ( kkey + "_starless" ) : kkey );
         if ( kw == null ) continue;
         chans[kkey].window = kw;
         chans[kkey].view = kw.mainView;
         if ( kSplit )
         {
            delete results[kkey];
            Pipeline.publish( chans[kkey].stars, kkey + "_stars", reg, keepIds,
                              results, kkey + "_stars" );
         }
      }
      var rgbStarsKept = false;
      if ( rgbWin !== null )
      {
         var rgbSplit = ( rgbStars != null );
         if ( rgbSplit )
            delete results.RGB;
         rgbWin = Pipeline.publish( rgbWin, rgbSplit ? "RGB_starless" : "RGB",
                                    reg, keepIds, results,
                                    rgbSplit ? "RGB_starless" : "RGB" );
         if ( rgbSplit )
         {
            // Broadband stars are always kept: they are the stars everything
            // else is recombined against.
            Pipeline.publish( rgbStars, "RGB_stars", reg, keepIds,
                              results, "RGB_stars" );
            rgbStarsKept = true;
         }
         if ( rgbLinear != null )
         {
            // the composite as it was before the stretch, for reference
            Pipeline.publish( rgbLinear, "RGB_linear", reg, keepIds,
                              results, "RGB_linear" );
         }
      }
      for ( var pw2 = 0; pw2 < paletteWins.length; ++pw2 )
      {
         var entry = paletteWins[pw2];

         /*
          * Naming a result must never destroy a run that has already done
          * all of its work. A palette whose window has gone is a real fault
          * worth reporting loudly, but every other output is finished and
          * correct by this point, and throwing here would discard them all
          * through the catch below.
          */
         if ( !Pipeline.windowIsUsable( entry.window ) )
         {
            Util.error( "output", entry.name + ": its window is gone before it " +
                                  "could be named; this palette is lost, the rest " +
                                  "of the run is unaffected" );
            continue;
         }

         var eSplit = ( entry.stars != null );
         entry.window = Pipeline.publish( entry.window,
                           eSplit ? ( entry.name + "_starless" ) : entry.name,
                           reg, keepIds, results,
                           eSplit ? ( entry.name + "_starless" ) : entry.name );
         if ( eSplit )
         {
            /*
             * Narrowband stars are kept only when there are no broadband
             * stars to recombine against. With an RGB composite in the run
             * its stars are the ones that get used, and a second, dimmer
             * set from the narrowband palette is just another window to
             * close by hand.
             */
            if ( rgbStarsKept )
            {
               Util.log( "output", entry.name + ": stars dropped -- RGB_stars " +
                                   "already covers the broadband stars" );
               try { entry.stars.forceClose(); } catch ( e ) {}
            }
            else
               Pipeline.publish( entry.stars, entry.name + "_stars", reg,
                                 keepIds, results, entry.name + "_stars" );
         }
         if ( entry.linear != null )
            Pipeline.publish( entry.linear, entry.name + "_linear", reg,
                              keepIds, results, entry.name + "_linear" );
      }
      /*
       * Frequency separation of the L stars plate, before the export so the
       * two layers are written like any other result.
       *
       * Only L_stars, and only on request: it is a retouching aid for the
       * one plate whose halos get worked on by hand, not a processing step.
       * A failure costs the two layers and nothing else -- the plate itself
       * is already published.
       */
      if ( config.separateLStars && results.L_stars != null &&
           Pipeline.windowIsUsable( results.L_stars ) )
      {
         try
         {
            var fs = Steps.frequencySeparate( results.L_stars, "L_stars" );
            reg.add( fs.low );
            reg.add( fs.high );
            Pipeline.publish( fs.low, "L_stars_low", reg, keepIds, results, "L_stars_low" );
            Pipeline.publish( fs.high, "L_stars_high", reg, keepIds, results, "L_stars_high" );
         }
         catch ( e )
         {
            Util.warn( "freqsep", "L_stars was not separated (" + e +
                                  "); the plate itself is unaffected" );
         }
      }

      /*
       * Tag every result before anything is written.
       *
       * Done on the WINDOWS, not at export: the windows are the primary
       * deliverable, so a profile put here reaches the workspace, anything
       * saved from it as XISF, and the exported TIFFs alike. The colour
       * composites already carry the RGB working space; the mono plates
       * carried nothing at all and opened untagged.
       */
      for ( var rk in results )
         if ( results[rk] && Pipeline.windowIsUsable( results[rk] ) )
            Steps.assignProfile( results[rk], rk );

      Pipeline.sweepNewWindows( preexisting, keepIds );

      Pipeline.exportResults( results, config );

      return results;
   }
   catch ( e )
   {
      Util.error( "pipeline", e.toString() );
      if ( !config.keepWindowsOnError )
      {
         reg.closeAll();
         // A failed run keeps nothing: every window it created is working
         // state, and leaving it behind litters the workspace.
         Pipeline.sweepNewWindows( preexisting, [] );
      }
      throw e;
   }
};

/*
 * Closes every window that appeared during the run except the ones named
 * in `keepIds`. Windows that existed beforehand are never touched.
 */
/*
 * Detaches a finished output from the cache file it was loaded from, so
 * its title reads "L_1" rather than "L_1 | bb9fbae0f6...xisf". Returns
 * the window to keep -- the original when no detach was needed.
 */
/*
 * True when a window reference can still be used -- not null, not closed.
 * A closed ImageWindow leaves a live JS object whose mainView is null, so
 * the only reliable test is to reach through and see.
 */
Pipeline.windowIsUsable = function( w )
{
   if ( w == null )
      return false;
   try
   {
      if ( w.isNull )
         return false;
      return w.mainView != null && !w.mainView.isNull;
   }
   catch ( e ) { return false; }
};

Pipeline.detachIfCached = function( window, id, reg )
{
   var path = "";
   try { path = window.filePath || ""; } catch ( e ) { path = ""; }
   if ( path.length == 0 || path.indexOf( Cache.dir() ) != 0 )
      return window;

   var clean = Steps.detachFromFile( window, id );
   reg.add( clean );
   reg.forget( window );
   try { window.forceClose(); }
   catch ( e ) { Util.warn( "output", "could not close the cached copy of " + id + ": " + e ); }
   Util.log( "output", id + ": detached from its cache file" );
   return clean;
};

Pipeline.sweepNewWindows = function( preexisting, keepIds )
{
   var keep = {};
   for ( var k = 0; k < keepIds.length; ++k )
      if ( keepIds[k] )
         keep[keepIds[k]] = true;

   var closed = [];
   var wins = ImageWindow.windows;
   for ( var i = 0; i < wins.length; ++i )
   {
      var id = wins[i].mainView.id;
      if ( preexisting[id] || keep[id] )
         continue;
      try { wins[i].forceClose(); closed.push( id ); }
      catch ( e ) { Util.warn( "cleanup", "could not close " + id + ": " + e ); }
   }
   if ( closed.length )
      Util.log( "cleanup", "closed " + closed.length + " working window(s): " +
                           closed.join( ", " ) );
   return closed.length;
};

/*
 * The Cancel window for the current run, if one is up. Set by Pipeline.run.
 */
Pipeline.cancelWindow = null;

/*
 * Stops the run if the user asked, by either route: PixInsight's own
 * Pause/Abort on the Process Console, or Loom's Cancel window.
 *
 * Pumps events first, which is what makes the Cancel button clickable at
 * all -- a running script owns the main thread, so without this the window
 * paints but never responds.
 */
Pipeline.checkAbort = function( stage )
{
   if ( Pipeline.cancelWindow != null )
   {
      try
      {
         if ( stage )
            Pipeline.cancelWindow.setStage( stage );
         CoreApplication.processEvents();
      }
      catch ( e ) {}

      if ( Pipeline.cancelWindow.cancelled )
         throw new Error( "Cancelled by user" );

      /*
       * Re-assert the console at each checkpoint. An auto-hidden dock slides
       * away whenever something else takes focus, and a run that has just
       * logged a warning is the worst moment to lose it.
       */
      try { console.show(); } catch ( e ) {}
   }

   if ( console.abortRequested )
   {
      console.abort();
      throw new Error( "Aborted by user" );
   }
};
