#engine v8

#feature-id    Loom Frame Selector : Batch Processing > Loom Frame Selector
#feature-info  Measures every subframe in a folder, groups them by filter, and \
               removes the ones this night's own statistics condemn.

#include <pjsr/UndoFlag.jsh>
#include <pjsr/DataType.jsh>
#include <pjsr/StdButton.jsh>
#include <pjsr/StdIcon.jsh>
#include <pjsr/TextAlign.jsh>
#include <pjsr/BrushStyle.jsh>
#include <pjsr/CryptographicHash.jsh>

/*
 * selftest.js includes this file to reach the functions below, and it has
 * already loaded the libraries. PixInsight's preprocessor does not dedupe an
 * #include, so without this guard the suite would re-execute every library,
 * resetting each namespace object after the suite had captured references to
 * it, and re-running Steps.js's `#define VERSION`.
 */
#ifndef LOOM_LIBS_INCLUDED
#include "lib/Util.js"
#include "lib/Cache.js"
#include "lib/Psb.js"
#include "lib/Steps.js"
#include "lib/Pipeline.js"
#include "lib/Frames.js"
#endif

function FrameSelector() {}

/*
 * routine 0 measures. 1 and 2 are the preview and output routines and refuse
 * with "No measurements have been made" -- verified by executing all three.
 */
FrameSelector.MEASURE_ROUTINE = 0;

/*
 * ONE place that configures the process, so the settings that produce a
 * measurement and the settings that key its cache entry cannot drift apart.
 */
FrameSelector.newMeasureProcess = function()
{
   var P = new SubframeSelector;
   P.routine = FrameSelector.MEASURE_ROUTINE;
   P.nonInteractive = true;
   P.subframeScale = 1;
   P.scaleUnit = 0;
   return P;
};

/*
 * Measure a list of frames in ONE SubframeSelector call.
 *
 * Results are matched back to inputs by the path in the measurement row,
 * never by position, exactly as WBPP's own analyzer does
 * (BPP-SubframeAnalyzer.js:520). If one frame fails to measure, positional
 * reading shifts every later row onto the wrong file -- and a perfect
 * deletion fingerprint would then verify the wrong frame's identity against
 * another frame's metrics.
 *
 * A path with no result row is simply absent from the returned map; the
 * caller turns that into an unmeasurable entry. Returns null when the
 * channel must be abandoned entirely.
 */
FrameSelector.measure = function( paths )
{
   var out = {};
   if ( paths == null || paths.length == 0 )
      return out;

   var P = FrameSelector.newMeasureProcess();
   var rows = [];
   for ( var i = 0; i < paths.length; ++i )
      rows.push( [ true, paths[i], "", "" ] );   // four values per row, checked
   P.subframes = rows;

   if ( !P.executeGlobal() )
   {
      Util.warn( "frames", "SubframeSelector failed on " + paths.length + " frame(s)" );
      return out;
   }
   if ( P.measurements == null )
      return out;

   /*
    * A result path must be one we ASKED for. Checking only for duplicates
    * lets an unexpected path through, and an empty one be silently dropped
    * -- either way the channel's statistics would be computed over a set
    * that is not the set on screen.
    */
   var asked = {};
   for ( var a = 0; a < paths.length; ++a )
      asked[paths[a]] = true;

   var disabled = null;                   // optional columns unusable this run
   for ( var r = 0; r < P.measurements.length; ++r )
   {
      var m = Frames.metricsFromRow( P.measurements[r] );
      /*
       * The table's shape is a property of the process, not of the frame,
       * so it is checked on the FIRST row only: a second check proves
       * nothing new, and a single bad row is a bad frame, which the
       * review is there to catch.
       */
      if ( r == 0 && ( disabled = FrameSelector.checkSchema( m ) ) == null )
         return null;
      var problem = FrameSelector.pathProblem( m, asked, out );
      if ( problem != null )
      {
         Util.error( "frames", problem + "; abandoning this channel" );
         return null;                    // null = the channel failed
      }
      Frames.sanitizeOptional( m, disabled );
      out[m.path] = m;
   }
   return out;
};

/*
 * Does the first row MEAN what it is being read as? Returns the optional
 * columns to blank for this run, or null when the channel must be
 * abandoned.
 *
 * Frames.COL is a set of fixed indices read off one core build. A
 * different build returning a different table would not fail here -- it
 * would hand back numbers from the wrong columns, and this tool deletes
 * files on those numbers. meaningProblems answers the question the indices
 * cannot: are these values shaped like the quantities they claim to be.
 *
 * Background and SNR are optional: a column that is not what it should be
 * blanks that figure for the whole run and says so, and never abandons the
 * channel.
 */
FrameSelector.checkSchema = function( m )
{
   var wrong = Frames.meaningProblems( m );
   if ( wrong.length > 0 )
   {
      Util.error( "frames",
         "SubframeSelector's measurements are not shaped as expected on " +
         "this PixInsight (" + Util.formatCoreVersion( {
            major: CoreApplication.versionMajor,
            minor: CoreApplication.versionMinor,
            release: CoreApplication.versionRelease } ) + "): " +
         wrong.join( "; " ) + ". Abandoning this channel rather than " +
         "deleting frames on numbers read from the wrong columns." );
      return null;
   }
   var disabled = {}, soft = Frames.optionalProblems( m );
   for ( var i = 0; i < soft.length; ++i )
   {
      disabled[soft[i]] = true;
      Util.warn( "frames", "SubframeSelector's " + soft[i] + " column is " +
                 "not usable on this PixInsight; it is left blank for this run" );
   }
   return disabled;
};

/*
 * Why a result row cannot be accepted, or null. A result path must be one
 * that was ASKED for, and only once: an unexpected path or a duplicate
 * means the channel's statistics would describe a set that is not the set
 * on screen.
 */
FrameSelector.pathProblem = function( m, asked, out )
{
   if ( m.path == null || m.path === "" || !asked[m.path] )
      return "SubframeSelector returned an unexpected path (" + m.path + ")";
   if ( out[m.path] != null )
      return "two measurements for " + m.path;
   return null;
};

/*
 * A discarded frame is marked twice over: an X beside its name, so the rows
 * to lose are findable at a glance, and the measurement that condemned it
 * in red, so the reason is visible without reading anything. A frame
 * condemned by hand has no failing measurement, and correctly gets the mark
 * with nothing coloured.
 */
FrameSelector.markRejected = function( node, row )
{
   var rejected = Frames.finalState( row.state, row.override ) == Frames.STATE.REJECTED;
   var ico = FrameSelector.markIcon( rejected );
   if ( ico != null )
      node.setIcon( 0, ico );
   if ( !rejected )
      return;
   var failing = row.failing || [];
   for ( var f = 0; f < failing.length; ++f )
   {
      var col = Frames.metricColumn( failing[f] );
      if ( col != null )
         node.setTextColor( col, FrameSelector.REJECT_COLOUR );
   }
};

/* Null the named handlers on a control that may not have been built. */
FrameSelector.detach = function( control, names )
{
   if ( control == null )
      return;
   for ( var i = 0; i < names.length; ++i )
      control[names[i]] = null;
};

/*
 * Stretch an image in a throwaway window and render it. Image.render()
 * applies no STF, so a linear frame renders black without this; the
 * stretch is built from the image's own median and MAD. The window is
 * closed in finally, success or not.
 */
FrameSelector.stretchedRender = function( img )
{
   var med = img.median(), mad = img.MAD()*1.4826;
   var shadows = Math.max( 0, med - 2.8*mad );
   var midtone = Math.mtf( 0.25, Math.max( 1e-8, med - shadows ) );
   var dup = null;
   try
   {
      dup = new ImageWindow( img.width, img.height, img.numberOfChannels,
                             img.bitsPerSample, img.isReal, img.isColor,
                             Util.freeWindowId( "fs_render" ) );
      dup.mainView.beginProcess( UndoFlag_NoSwapFile );
      dup.mainView.image.assign( img );
      dup.mainView.endProcess();
      var H = new HistogramTransformation;
      H.H = [ [ 0, 0.5, 1, 0, 1 ], [ 0, 0.5, 1, 0, 1 ], [ 0, 0.5, 1, 0, 1 ],
              [ shadows, midtone, 1, 0, 1 ], [ 0, 0.5, 1, 0, 1 ] ];
      H.executeOn( dup.mainView );
      return dup.mainView.image.render();
   }
   finally
   {
      try { if ( dup != null && !dup.isNull ) dup.forceClose(); } catch ( e ) {}
   }
};

/*
 * One frame's thumbnail: opened, shrunk IN its own throwaway window (no
 * full-size copy), stretched, rendered. Null when it cannot be read.
 * resample's single-factor form keeps the aspect by construction.
 */
FrameSelector.thumbnailOf = function( path )
{
   if ( !File.exists( path ) )
      return null;
   var ws = [];
   try
   {
      ws = ImageWindow.open( path );
      if ( ws.length == 0 )
         return null;
      var view = ws[0].mainView, img = view.image;
      var s = Math.min( FrameSelector.THUMB.W/img.width, FrameSelector.THUMB.H/img.height );
      view.beginProcess( UndoFlag_NoSwapFile );
      view.image.resample( s );
      view.endProcess();
      return FrameSelector.stretchedRender( view.image );
   }
   catch ( e )
   {
      Util.warn( "frames", "no thumbnail for " + path + ": " + e );
      return null;
   }
   finally
   {
      FrameSelector.closeAll( ws );
   }
};

/*
 * Close every window in a list, never throwing. ImageWindow.open returns
 * one window per image in the file, so closing only the first leaks the
 * rest of a multi-image file at full size.
 */
FrameSelector.closeAll = function( ws )
{
   for ( var i = 0; i < ( ws || [] ).length; ++i )
      try { if ( ws[i] != null && !ws[i].isNull ) ws[i].forceClose(); } catch ( e ) {}
};

/*
 * A digest of the WHOLE file.
 *
 * Not the header and a sample of pixels: a change outside the sampled
 * region passes a partial hash without any collision, and this is the value
 * that authorises deleting someone's data.
 */
FrameSelector.digest = function( path )
{
   try
   {
      if ( !File.exists( path ) )
         return null;
      var f = new File;
      f.openForReading( path );
      var bytes;
      /*
       * The close belongs in a finally: a read that throws used to leave the
       * handle open, and a scan digests every frame in the folder, so one
       * unreadable file per run leaks until PixInsight exits.
       */
      try { bytes = f.read( DataType.ByteArray, f.size ); }
      finally { f.close(); }
      return ( new CryptographicHash( CryptographicHash.SHA1 ) ).hash( bytes ).toHex();
   }
   catch ( e )
   {
      Util.warn( "frames", "could not digest " + path + ": " + e );
      return null;
   }
};

FrameSelector.fileIdentity = function( path )
{
   try
   {
      if ( !File.exists( path ) )
         return null;
      var fi = new FileInfo( path );
      var t = fi.lastModified;
      var d = FrameSelector.digest( path );
      if ( d == null )
         return null;
      return { digest: d, size: fi.size, mtime: t ? t.getTime() : 0 };
   }
   catch ( e ) { return null; }
};

/*
 * The header fields the comparability check needs. Calibration state is one
 * of them: raw and calibrated frames of one filter can agree on exposure,
 * binning and geometry, and an uncalibrated frame among calibrated ones
 * differs in every measured metric.
 */
FrameSelector.entryFor = function( path )
{
   var info = null;
   try { info = Pipeline.readImageInfo( path ); } catch ( e ) { info = null; }
   var kw = info ? info.keywords : null;
   function keyword( name )
   {
      return kw ? Util.keywordValue( kw, name ) : null;
   }

   /*
    * WBPP's calibrated frames carry a _c suffix and a calibration history.
    * Unknown state counts as its own value, so a mixture of known and
    * unknown is reported rather than assumed uniform.
    */
   var calibrated = /_c(_[0-9]+)?\.[a-z]+$/i.test( path ) ? "yes"
                    : ( keyword( "HISTORY" ) ? "history" : "unknown" );

   return { path: path,
            filter:    keyword( "FILTER" ),
            exposure:  keyword( "EXPTIME" ),
            binning:   keyword( "XBINNING" ),
            /*
             * Read as well as X: asymmetric binning agrees on XBINNING and
             * differs only vertically, and Frames.comparability has always
             * asked for this field. Nothing set it, so the guard it feeds
             * could never fire.
             */
            binningY:  keyword( "YBINNING" ),
            imageType: keyword( "IMAGETYP" ),
            width:  info ? info.width  : 0,
            height: info ? info.height : 0,
            calibrated: calibrated };
};

FrameSelector.cachePath = function()
{
   return Cache.dir() + "/frame-quality.json";
};

FrameSelector.table = null;

FrameSelector.loadTable = function()
{
   if ( FrameSelector.table != null )
      return FrameSelector.table;
   FrameSelector.table = {};
   try
   {
      var p = FrameSelector.cachePath();
      if ( File.exists( p ) )
         FrameSelector.table = JSON.parse( File.readTextFile( p ) ) || {};
   }
   catch ( e ) { FrameSelector.table = {}; }
   return FrameSelector.table;
};

FrameSelector.saveTable = function()
{
   try
   {
      Cache.ensureDir();
      File.writeTextFile( FrameSelector.cachePath(),
                          JSON.stringify( FrameSelector.table || {} ) );
   }
   catch ( e ) { Util.warn( "frames", "could not save measurements: " + e ); }
};

/*
 * The configuration is part of the cache key, not just the bytes.
 *
 * A measurement taken under different SubframeSelector settings is not
 * comparable with a fresh one, and WBPP folds SS.toSource() into its own
 * measurement cache for exactly this reason. Without it, changing a setting
 * silently reuses numbers taken under the old one.
 */
FrameSelector.configSignature = function()
{
   var P = FrameSelector.newMeasureProcess();
   return ( new CryptographicHash( CryptographicHash.SHA1 ) )
             .hash( ByteArray.stringToUTF8( P.toSource() ) ).toHex().substring( 0, 12 );
};

/*
 * Keyed by the DIGEST, not the path, and carrying the measurement version.
 * A cached number whose digest no longer matches the file is simply not
 * found, so the numbers behind a verdict always describe the bytes that
 * verdict will be applied to.
 */
FrameSelector.measurementKey = function( identity )
{
   return Frames.MEASURE_VERSION + "|" + FrameSelector.configSignature() +
          "|" + identity.digest;
};

FrameSelector.cachedMeasurement = function( identity )
{
   var t = FrameSelector.loadTable();
   var e = t[FrameSelector.measurementKey( identity )];
   return Frames.cacheEntryUsable( e ) ? e : null;
};

FrameSelector.storeMeasurement = function( identity, metrics )
{
   var t = FrameSelector.loadTable();
   t[FrameSelector.measurementKey( identity )] = metrics;
   FrameSelector.saveTable();
};

FrameSelector.frameFilesIn = function( folder )
{
   var paths = [], find = new FileFind;
   if ( find.begin( folder + "/*" ) )
      do
      {
         if ( find.isFile && /\.(xisf|fits?|fit)$/i.test( find.name ) )
            paths.push( folder + "/" + find.name );
      }
      while ( find.next() );
   return paths;
};

/*
 * One entry per file, each carrying the fingerprint taken BEFORE anything is
 * measured. A file that cannot be fingerprinted is left out of the cohort
 * rather than carried with an unknown identity: there would be nothing to
 * compare against afterwards, so nothing could authorise deleting it.
 */
/*
 * Reading the cohort is the slow, silent part: every frame is digested
 * whole, so a folder of 34 subframes reads several gigabytes before
 * SubframeSelector has even started. `progress` is called once per file
 * BEFORE that file is read -- so the label names what is being read now,
 * and a path that turns out to be unreadable still advances the count.
 * Returning false from it abandons the scan.
 */
FrameSelector.cohortFrom = function( paths, progress )
{
   var entries = [], before = {}, cancelled = false;
   for ( var i = 0; i < paths.length; ++i )
   {
      if ( progress != null &&
           progress( i + 1, paths.length, File.extractName( paths[i] ) ) === false )
      {
         cancelled = true;
         break;
      }
      var id = FrameSelector.fileIdentity( paths[i] );
      if ( id == null )
         continue;
      before[paths[i]] = id;
      var e = FrameSelector.entryFor( paths[i] );
      e.identity = id;
      entries.push( e );
   }
   return { entries: entries, before: before, cancelled: cancelled };
};

/* Moved to Frames, with background and SNR; kept as a name here. */
FrameSelector.storedMetrics = function( m ) { return Frames.storedMetrics( m ); };

/*
 * Fingerprint, measure, fingerprint again.
 *
 * A frame whose identity differs across the measurement is UNSTABLE:
 * something rewrote it while it was being read, so its numbers describe
 * bytes that are no longer there. Without this, measuring A, having it
 * replaced by B, and fingerprinting B would produce a manifest that
 * authorises deleting B on A's numbers. An unstable frame is dropped from
 * the cohort entirely rather than shown with numbers nobody can act on.
 *
 * Returns null when the group must be abandoned, which discards the cached
 * numbers too -- a channel measured over a set that is not the set on screen
 * has no statistics worth showing.
 */
FrameSelector.measureGroup = function( group, before )
{
   var need = [], metrics = {};
   for ( var j = 0; j < group.length; ++j )
   {
      var cached = FrameSelector.cachedMeasurement( group[j].identity );
      if ( cached != null )
         metrics[group[j].path] = cached;
      else
         need.push( group[j].path );
   }
   if ( need.length == 0 )
      return { metrics: metrics, unstable: [] };

   var measured = FrameSelector.measure( need );
   if ( measured == null )
      return null;

   var unstable = [];
   for ( var k = 0; k < need.length; ++k )
   {
      var path = need[k];
      var now = FrameSelector.fileIdentity( path );
      if ( now == null || now.digest != before[path].digest )
      {
         unstable.push( path );
         continue;                        // the measured bytes are gone
      }
      if ( measured[path] != null )
      {
         var stored = FrameSelector.storedMetrics( measured[path] );
         FrameSelector.storeMeasurement( before[path], stored );
         metrics[path] = stored;
      }
   }
   return { metrics: metrics, unstable: unstable };
};

FrameSelector.scan = function( folder, progress )
{
   var cohort = FrameSelector.cohortFrom( FrameSelector.frameFilesIn( folder ),
                                          progress ? progress.reading : null );
   if ( cohort.cancelled )
      return { channels: {}, unstable: [], cancelled: true };

   var groups = Frames.groupByFilter( cohort.entries );
   var channels = {}, unstable = [];

   var keys = Object.keys( groups );
   for ( var g = 0; g < keys.length; ++g )
   {
      if ( progress != null && progress.measuring != null &&
           progress.measuring( g + 1, keys.length, keys[g] ) === false )
         return { channels: channels, unstable: unstable, cancelled: true };
      var group = groups[keys[g]];
      var measured = FrameSelector.measureGroup( group, cohort.before );
      if ( measured == null )             // the channel was abandoned
      {
         channels[keys[g]] = { entries: group, metrics: {},
                               problems: [ "measurement failed" ] };
         continue;
      }
      unstable = unstable.concat( measured.unstable );
      channels[keys[g]] = { entries: group, metrics: measured.metrics,
                            problems: Frames.comparability( group ).problems };
   }
   return { channels: channels, unstable: unstable, cancelled: false };
};

/*
 * The audit log does NOT live where Cache.clear can remove it -- that
 * deletes every top-level file in the cache directory -- and not under the
 * system temporary directory, which is not durable.
 */
FrameSelector.logDir = function()
{
   return File.homeDirectory + "/PixInsight/Loom-frame-selector";
};

FrameSelector.writeManifestLog = function( manifest, path )
{
   try
   {
      var dir = File.extractDirectory( path );
      if ( !File.directoryExists( dir ) )
         File.createDirectory( dir, true );
      var lines = [ "# Loom Frame Selector",
                    "# built " + ( new Date( manifest.created ) ).toISOString(),
                    "# " + manifest.entries.length + " frame(s) condemned" ];
      for ( var i = 0; i < manifest.entries.length; ++i )
      {
         var e = manifest.entries[i];
         /*
          * The override is recorded beside the automatic verdict. If a
          * rescued frame turns out to have been the bad one, the record has
          * to say who chose it.
          */
         lines.push( e.path + "\t" + e.digest + "\t" +
                     ( e.override ? e.override : e.autoVerdict ) + "\t" +
                     e.reasons.join( "; " ) );
      }
      File.writeTextFile( path, lines.join( "\n" ) + "\n" );
      return true;
   }
   catch ( e )
   {
      Util.error( "frames", "could not write the deletion log: " + e );
      return false;
   }
};

/*
 * Append one entry's fate to the journal and flush it. Returns false if the
 * record could not be written, which stops the run.
 */
FrameSelector.appendOutcome = function( logPath, manifest, path )
{
   try
   {
      var e = null;
      for ( var i = 0; i < manifest.entries.length; ++i )
         if ( manifest.entries[i].path == path )
            { e = manifest.entries[i]; break; }
      if ( e == null )
         return false;
      var f = new File;
      f.openForReadWrite( logPath );
      f.seekEnd();
      f.outTextLn( e.outcome + "\t" + e.path + "\t" + e.digest + "\t" + e.detail );
      f.flush();
      f.close();
      return true;
   }
   catch ( ex )
   {
      Util.error( "frames", "could not append to the journal: " + ex );
      return false;
   }
};

/*
 * Execute a manifest. Never recomputes a verdict; never touches a file whose
 * identity has changed.
 *
 * The log is written BEFORE any unlink and appended as each file goes, so
 * the record says what actually died rather than only what was intended. If
 * the log cannot be written, nothing is deleted: an unrecorded deletion is
 * worse than a deferred one.
 */
FrameSelector.execute = function( manifest )
{
   var result = { deleted: 0, skipped: 0, failed: 0, stopped: false,
                  logPath: null };
   var pending = Frames.manifestPending( manifest );
   if ( pending.length == 0 )
      return result;

   var logPath = FrameSelector.logDir() + "/deleted-" +
                 ( new Date() ).toISOString().replace( /[:.]/g, "-" ) + ".log";
   if ( !FrameSelector.writeManifestLog( manifest, logPath ) )
   {
      Util.error( "frames", "nothing deleted: the log could not be written" );
      return result;
   }
   result.logPath = logPath;

   for ( var i = 0; i < pending.length; ++i )
   {
      var e = pending[i];
      /*
       * Recomputed immediately before the unlink. This does NOT close the
       * window -- nothing in PJSR locks a file, so a replacement in that
       * instant is undetectable -- it narrows it to microseconds. The log is
       * what survives if something slips through.
       */
      var now = FrameSelector.fileIdentity( e.path );
      if ( !Frames.identityMatches( e, now ) )
      {
         Frames.recordOutcome( manifest, e.path, "skipped",
                               "changed on disk since it was measured" );
         Util.warn( "frames", "skipped " + e.path + ": it changed since measurement" );
         ++result.skipped;
         if ( !FrameSelector.appendOutcome( logPath, manifest, e.path ) )
            { result.stopped = true; return result; }
         continue;
      }
      try
      {
         File.remove( e.path );
         Frames.recordOutcome( manifest, e.path, "deleted", "" );
         ++result.deleted;
      }
      catch ( ex )
      {
         Frames.recordOutcome( manifest, e.path, "failed", String( ex ) );
         ++result.failed;
      }
      /*
       * Journalled one entry at a time, before moving on. Writing every
       * outcome at the END loses the whole record if the run is interrupted
       * mid-way -- which is precisely when the record is needed. If the
       * journal cannot be appended, the run STOPS: continuing would delete
       * files nothing is recording.
       */
      if ( !FrameSelector.appendOutcome( logPath, manifest, e.path ) )
      {
         Util.error( "frames", "stopping: the journal could not be appended" );
         result.stopped = true;
         return result;
      }
   }

   Util.log( "frames", result.deleted + " deleted, " + result.skipped +
                       " skipped, " + result.failed + " failed; log " + logPath );
   return result;
};

/*
 * A 1:1 pannable preview.
 *
 * Image.render() does NOT apply a screen stretch -- its own documentation
 * excludes it -- so an STF on the view renders nothing different. The
 * duplicate's PIXELS are stretched with a HistogramTransformation built from
 * the frame's own median and MAD, and THAT is what is rendered.
 *
 * Rendered once per selected frame rather than once per paint, so panning is
 * a blit. The prototype measured 341 ms from open to a drawn bitmap on a
 * 26 MP sub and 0 ms to pan and repaint, against a 1.5 s gate, so a
 * full-resolution render is affordable and the downsampled fallback is not
 * needed.
 *
 * Exactly one frame's window and bitmap are held: a 26 MP ARGB bitmap is
 * about 104 MB, and browsing a folder must not accumulate them.
 */
/*
 * How close two of the plot's numbers may come before one is dropped.
 * A line of text is about this tall, so anything closer overlaps.
 */
FrameSelector.LABEL_GAP = 13;

/*
 * Pixels one scrolling unit moves the preview: an arrow button on a
 * scroll bar, or a press of Left/Right/Up/Down. The default of 1 made
 * either of those look broken.
 */
FrameSelector.SCROLL_LINE = 48;

/*
 * The preview's tags: red for what Run leaves out, amber for an anomaly,
 * each in its own box as SubframeStudio draws them.
 */
FrameSelector.TAG_COLOURS = { reject: { fill: 0xffb02222, ink: 0xffffffff },
                              flag:   { fill: 0xffc08a1e, ink: 0xff1a1a1a } };
FrameSelector.TAG = { W: 150, H: 26, GAP: 8, MARGIN: 10 };

/* A criterion box while it shows the automatic limit rather than a typed one. */
FrameSelector.AUTO_STYLE = "QLineEdit { color: #8a8a8a; font-style: italic; }";

/* A filmstrip tile's picture, and how many thumbnails are kept. */
FrameSelector.THUMB = { W: 120, H: 80 };
FrameSelector.THUMB_CAP = 600;

/* Radius of the ring round the selected frame: clear of a 4px marker. */
FrameSelector.PICK_RADIUS = 6;

/*
 * A 1:1 pannable preview.
 *
 * A ScrollBox, so the frame can be moved with bars as well as by dragging.
 *
 * A two-finger swipe does NOT move it, and cannot be made to. PixInsight
 * delivers a swipe as a wheel event carrying one delta and no orientation
 * -- MouseWheel( ..., int32 delta, ... ) -- so a sideways swipe arrives
 * as delta = 0, measured over 1535 events. Enumerating Dialog, Control,
 * ScrollBox and its viewport on the running core, with the
 * ImageWindow/TouchEvents preference ON, finds no touch, gesture, pan or
 * swipe handler on any of them: the core consumes the gesture for image
 * windows and never forwards it to a script.
 *
 * Rendered once per selected frame rather than once per paint, so panning
 * is a blit. The prototype measured 341 ms from open to a drawn bitmap on
 * a 26 MP sub and 0 ms to pan and repaint, against a 1.5 s gate.
 *
 * Exactly one frame's window and bitmap are held: a 26 MP ARGB bitmap is
 * about 104 MB, and browsing a folder must not accumulate them.
 */
FrameSelector.PreviewControl = class extends ScrollBox
{
   constructor( parent )
   {
      super( parent );
      var self = this;
      this.bmp = null;
      this.tags = [];
      this.fit = false;
      this.dragging = false;
      this.lx = 0;
      this.ly = 0;

      this.tracking = true;
      this.setScaledMinSize( 420, 360 );

      this.viewport.focusStyle = FocusStyle.Click;   // arrows need focus
      this.viewport.toolTip =
         "<p><b>Double click</b> to switch between the whole frame and 1:1.</p>" +
         "<p>At 1:1: <b>drag</b> the image, use the <b>scroll bars</b>, or the " +
         "<b>arrow keys</b> after clicking it. A two-finger swipe does " +
         "nothing here -- PixInsight does not pass the gesture to a script.</p>";

      /*
       * NO wheel handler is installed here, by decision.
       *
       * It was measured working for vertical swipes -- a trackpad reports
       * pixels in `delta`, and this scrolled by them directly -- but it can
       * never do sideways: the API passes a single delta with no
       * orientation, so a horizontal swipe arrives as delta = 0 with
       * nothing in it to act on.
       *
       * Panning is the scroll bars, a drag, or the arrow keys, all of
       * which work the same in both directions.
       */
      this.viewport.onPaint = function() { self.paintViewport(); };
      this.viewport.onResize = function() { self.layOutScroll(); };

      /*
       * Repaint whenever Qt moves the scroll position.
       *
       * The viewport is painted by hand, offset by the scroll position, so
       * a scroll that does not repaint shows the same pixels and looks
       * exactly like a gesture that did nothing. Qt scrolls the box, but
       * only this puts the result on screen.
       */
      this.onHorizontalScrollPosUpdated = function() { self.viewport.update(); };
      this.onVerticalScrollPosUpdated = function() { self.viewport.update(); };

      /*
       * onViewportScrolled is NOT assigned here, and must not be.
       *
       * Assigning it terminates PixInsight when the control is destroyed:
       * ~QWidget -> deleteChildren -> std::terminate, from a deferred
       * delete processed after the script's JS context has gone. It is
       * that property specifically -- the two scroll-position handlers
       * above are safe, a handler on the viewport is safe, onShow on a
       * ScrollBox is safe, and a plain Control with a handler is safe.
       * Each of those was built alone in a dispatched script and each
       * survived; this one killed the application every time.
       *
       * Nothing is lost by its absence. Panning moves the scroll
       * positions, which fires the two handlers above, so the repaint
       * still happens. This was the long-standing crash that took
       * PixInsight down whenever the self-test ran.
       */

      // Established once at construction, as MaskMerge's does.
      this.layOutScroll();

      this.viewport.onMousePress = function( x, y )
      { self.dragging = true; self.lx = x; self.ly = y; };

      this.viewport.onMouseRelease = function() { self.dragging = false; };

      this.viewport.onMouseMove = function( x, y )
      {
         if ( !self.dragging )
            return;
         self.pan( self.lx - x, self.ly - y );
         self.lx = x; self.ly = y;
      };

      /*
       * Two ways of looking at a frame -- is the field right, and are the
       * stars right -- and this switches between them without a button
       * taking up room beside the image.
       */
      this.viewport.onMouseDoubleClick = function( x, y )
      {
         /*
          * Zooming IN goes to what was clicked.
          *
          * The target is computed BEFORE the toggle, while the view is
          * still fitted -- that is the only moment the click can be
          * mapped, because fittedPixelAt describes the fitted layout.
          * Getting this backwards centres on wherever the scroll position
          * happened to be left, which looks like the feature doing
          * nothing.
          */
         var target = self.fit ? self.fittedPixelAt( x, y ) : null;

         self.setFit( !self.fit );
         if ( !self.fit )
            self.centreOn( target );

         self.dragging = false;     // the double click delivered a press first
         return true;
      };

      /*
       * Returning true CONSUMES the key. Without that the frame table also
       * acts on the same arrow press and the selection moves underneath
       * the preview.
       */
      this.viewport.onKeyPress = function( key, modifiers )
      {
         var step = ( modifiers & KeyModifier.Shift ) ? self.viewport.width
                                                      : Math.round( self.viewport.width/4 );
         if ( key == KeyCode.Left  ) { self.pan( -step, 0 ); return true; }
         if ( key == KeyCode.Right ) { self.pan(  step, 0 ); return true; }
         if ( key == KeyCode.Up    ) { self.pan( 0, -step ); return true; }
         if ( key == KeyCode.Down  ) { self.pan( 0,  step ); return true; }
         return false;
      };
   }

   setFit( on )
   {
      this.fit = !!on;
      this.layOutScroll();
   }

   /*
    * Put an image pixel under the middle of the viewport.
    *
    * Clamped to the scroll range rather than refused: a point near an edge
    * cannot be centred, and showing it as close to the middle as the frame
    * allows is what zooming to a corner should do.
    */
   centreOn( px )
   {
      if ( px == null || this.bmp == null || this.fit )
         return;

      var h = Math.round( px.x - this.viewport.width/2 );
      var v = Math.round( px.y - this.viewport.height/2 );

      this.horizontalScrollPosition =
         Math.max( 0, Math.min( h, Math.max( 0, this.bmp.width  - this.viewport.width ) ) );
      this.verticalScrollPosition =
         Math.max( 0, Math.min( v, Math.max( 0, this.bmp.height - this.viewport.height ) ) );
      this.viewport.update();
   }

   /*
    * The image pixel under a point in the viewport, while FITTED.
    *
    * Must match how paintViewport actually draws the fitted frame:
    * scaled by min(vw/w, vh/h) and CENTRED, which leaves a letterbox band.
    * Ignoring those bands puts the zoom in the wrong place by half the
    * band -- and the band is tall here, because a 3:2 frame in a nearly
    * square pane leaves a lot of it.
    */
   fittedPixelAt( x, y )
   {
      if ( this.bmp == null )
         return null;

      var vw = this.viewport.width, vh = this.viewport.height;
      var s = Math.min( vw/this.bmp.width, vh/this.bmp.height );
      if ( !( s > 0 ) )
         return null;

      var fx = ( vw - this.bmp.width*s )/2;
      var fy = ( vh - this.bmp.height*s )/2;
      return { x: ( x - fx )/s, y: ( y - fy )/s };
   }

   /*
    * The scrollable extent is the bitmap beyond the viewport. Setting it to
    * zero when the whole frame is shown is what stops a fitted image from
    * scrolling around inside its own window.
    */
   layOutScroll()
   {
      /*
       * setHorizontalScrollRange, not an assignment to
       * maxHorizontalScrollPosition.
       *
       * Assigning the maximum left the box with nothing to scroll -- no
       * gesture did anything at all. The range setters are what
       * PixInsight's own scrolling views use (MaskMerge's initScrollBars
       * is the same three lines), and they are what establishes the range
       * the scroll bars and the wheel work against.
       */
      if ( this.bmp == null || this.fit )
      {
         this.setHorizontalScrollRange( 0, 0 );
         this.setVerticalScrollRange( 0, 0 );
      }
      else
      {
         this.setHorizontalScrollRange( 0,
            Math.max( 0, this.bmp.width  - this.viewport.width ) );
         this.setVerticalScrollRange( 0,
            Math.max( 0, this.bmp.height - this.viewport.height ) );
      }
      /*
       * The scrolling STEP, which is what was actually wrong.
       *
       * These default to lineWidth/lineHeight = 1 and pageWidth/pageHeight
       * = 10. A wheel notch scrolls three lines, so on a 4150-pixel range
       * the default moved the image three pixels: scrolling worked the
       * whole time and was indistinguishable from nothing happening.
       * Measured off a loaded preview, not guessed.
       *
       * On a ScrollBox lineWidth is the horizontal scrolling unit -- an
       * arrow button, or Left/Right -- and not Frame's border width, which
       * it shadows. The wheel multiplies the same unit.
       *
       * A page is what the viewport shows, which is what paging means
       * everywhere else.
       */
      this.lineWidth = FrameSelector.SCROLL_LINE;
      this.lineHeight = FrameSelector.SCROLL_LINE;
      this.pageWidth = Math.max( 1, this.viewport.width );
      this.pageHeight = Math.max( 1, this.viewport.height );

      /*
       * The bars are shown explicitly, matching the range that exists,
       * rather than left to automatic mode.
       *
       * Three reasons, and the last is the one that matters. They say a
       * frame is bigger than its window, which nothing else on screen
       * does. They can be dragged, which is a way round the wheel
       * whatever it turns out to deliver. And a scroll area with no live
       * scroll bar has nothing for a wheel event to act on -- so if the
       * gesture is arriving and doing nothing, this is what it was
       * missing.
       */
      try
      {
         this.showScrollBars( this.maxHorizontalScrollPosition > 0,
                              this.maxVerticalScrollPosition > 0 );
      }
      catch ( e ) { /* a bar that will not show must not stop the preview */ }

      this.viewport.update();
   }

   /* Drag and the arrow keys move the same scroll positions the wheel does. */
   pan( dx, dy )
   {
      if ( this.bmp == null || this.fit )
         return;
      this.horizontalScrollPosition =
         Math.max( 0, Math.min( this.horizontalScrollPosition + dx,
                                this.maxHorizontalScrollPosition ) );
      this.verticalScrollPosition =
         Math.max( 0, Math.min( this.verticalScrollPosition + dy,
                                this.maxVerticalScrollPosition ) );
      this.viewport.update();
   }

   paintViewport()
   {
      var g = null;
      try
      {
         g = new Graphics( this.viewport );
         var vw = this.viewport.width, vh = this.viewport.height;
         g.fillRect( 0, 0, vw, vh, new Brush( 0xff101010 ) );
         if ( this.bmp != null && this.fit )
         {
            /*
             * Centred, not pinned to the corner. A frame is 3:2 and the
             * pane is nearly square, so fitting leaves a band of unused
             * height -- all of it below the image, which reads as a
             * picture that failed to load rather than one that fits.
             */
            var s = Math.min( vw/this.bmp.width, vh/this.bmp.height );
            var fw = Math.round( this.bmp.width*s );
            var fh = Math.round( this.bmp.height*s );
            var fx = Math.round( ( vw - fw )/2 );
            var fy = Math.round( ( vh - fh )/2 );
            g.drawScaledBitmap( new Rect( fx, fy, fx + fw, fy + fh ), this.bmp );
         }
         else if ( this.bmp != null )
         {
            /*
             * Offset by the scroll position, the way PixInsight's own
             * ImageView does it, so what Qt scrolled is what gets drawn.
             *
             * On an axis with nothing to scroll the image is narrower than
             * the pane, so it is centred on that axis instead -- the same
             * rule ImageView applies.
             */
            var ox = ( this.maxHorizontalScrollPosition > 0 )
                     ? -this.horizontalScrollPosition
                     : Math.round( ( vw - this.bmp.width )/2 );
            var oy = ( this.maxVerticalScrollPosition > 0 )
                     ? -this.verticalScrollPosition
                     : Math.round( ( vh - this.bmp.height )/2 );
            g.translateTransformation( ox, oy );
            g.drawBitmap( 0, 0, this.bmp );
            g.resetTransformation();
         }
         /*
          * The tags, in VIEWPORT coordinates, on every path -- no frame,
          * fitted, or 1:1 and panned -- so they stay in the corner.
          */
         this.paintTags( g );
      }
      catch ( e ) { /* a preview that cannot paint must not stop the review */ }
      finally { if ( g != null ) try { g.end(); } catch ( e2 ) {} }
   }

   dispose()
   {
      this.bmp = null;                     // the only reference; let it go
   }

   /* What the frame is: REJECTED and each anomaly, one tag apiece. */
   setTags( tags )
   {
      this.tags = tags || [];
      this.viewport.update();
   }

   /*
    * Where each tag goes: top-right, stacked, in VIEWPORT coordinates.
    * `width` defaults to the viewport's; the suite passes its own bitmap's.
    */
   tagRects( width )
   {
      var T = FrameSelector.TAG, out = [];
      var vw = ( width == null ) ? this.viewport.width : width;
      for ( var i = 0; i < this.tags.length; ++i )
      {
         var y = T.MARGIN + i*( T.H + T.GAP );
         out.push( new Rect( vw - T.MARGIN - T.W, y, vw - T.MARGIN, y + T.H ) );
      }
      return out;
   }

   paintTags( g, width )
   {
      var r = this.tagRects( width );
      for ( var i = 0; i < this.tags.length; ++i )
      {
         var c = FrameSelector.TAG_COLOURS[this.tags[i].kind] || FrameSelector.TAG_COLOURS.flag;
         g.fillRect( r[i], new Brush( c.fill ) );
         g.pen = new Pen( c.ink );
         g.drawTextRect( r[i], this.tags[i].text, TextAlign_Center );
      }
   }

   /* The loaded frame, scaled to fit a filmstrip tile, aspect kept. */
   thumbnail()
   {
      if ( this.bmp == null )
         return null;
      var s = Math.min( FrameSelector.THUMB.W/this.bmp.width,
                        FrameSelector.THUMB.H/this.bmp.height );
      return this.bmp.scaled( s );
   }

   /*
    * Detach everything before the widget tree is destroyed.
    *
    * These handlers are JS closures held by C++ controls. Qt destroys the
    * viewport as a child of this box, and a handler reached during that
    * teardown runs against a half-destroyed object -- which throws, in a
    * destructor, which terminates the process. Closing the review took
    * PixInsight with it.
    *
    * The bitmap goes too: a 26 MP frame is about 104 MB, and it has no
    * business outliving the dialog that was showing it.
    */
   release()
   {
      try
      {
         this.bmp = null;
         this.tags = [];
         this.onHorizontalScrollPosUpdated = null;
         this.onVerticalScrollPosUpdated = null;
         this.onViewportScrolled = null;
         var v = this.viewport;
         if ( v != null )
         {
            v.onPaint = null;
            v.onResize = null;
            v.onMousePress = null;
            v.onMouseMove = null;
            v.onMouseRelease = null;
            v.onMouseDoubleClick = null;
            v.onKeyPress = null;
         }
      }
      catch ( e ) { /* releasing must never be the thing that fails */ }
   }

   load( path )
   {
      var self = this;
      self.dispose();
      /*
       * The scroll position is DELIBERATELY not reset.
       *
       * Comparing frames means looking at the same stars in each, and
       * returning to the corner on every selection made that impossible.
       * The frames of a channel are registered to each other and identical
       * in size, so the same offset shows the same part of the sky.
       * layOutScroll clamps it in case this frame is smaller.
       */
      /*
       * Checked before opening, because ImageWindow.open raises a MODAL
       * error box for a file that is not there -- one the user has to
       * dismiss, per frame. A frame can vanish between the scan and the
       * review, and that is a preview which does not appear, not a dialog
       * demanding attention.
       */
      if ( !File.exists( path ) )
      {
         Util.warn( "frames", "no preview, the file is gone: " + path );
         return false;
      }

      var win = null, ws = [];
      try
      {
         ws = ImageWindow.open( path );
         if ( ws.length == 0 )
            return false;
         win = ws[0];
         /*
          * Stretched before rendering: Image.render() does NOT apply an
          * STF, so a linear sub renders as a black rectangle. The helper
          * closes its own working window whatever happens.
          */
         self.bmp = FrameSelector.stretchedRender( win.mainView.image );
         self.layOutScroll();
         return true;
      }
      catch ( e )
      {
         Util.warn( "frames", "could not preview " + path + ": " + e );
         return false;
      }
      finally
      {
         // EVERY window the open produced, not only the first.
         FrameSelector.closeAll( ws );
      }
   }
};

/* ------------------------------------------------------------------------
 * The review state: cohort, review and the phase that separates them.
 * ---------------------------------------------------------------------- */

FrameSelector.emptyState = function( folder )
{
   return { folder: folder, channels: {}, order: [],
            /*
             * The folder that was scanned, which means "cull these where
             * they are" -- the same thing the tool did before it could
             * write anywhere else. Pointing it somewhere else is what
             * turns it into a copy; there is no separate mode to choose.
             */
            destination: folder,
            preset: Frames.DEFAULT_PRESET,
            phase: Frames.PHASE.REVIEW, locked: false,
            manifest: null, unstable: [] };
};

/* Moved to Frames so the node suite can drive them; kept as names here. */
FrameSelector.newChannel = function( key, entries, metrics, problems )
{
   return Frames.newChannel( key, entries, metrics, problems );
};
FrameSelector.recompute = function( ch ) { return Frames.recompute( ch ); };

FrameSelector.buildState = function( folder, progress )
{
   var state = FrameSelector.emptyState( folder );
   var scan = FrameSelector.scan( folder, progress );
   state.cancelled = !!scan.cancelled;
   state.unstable = scan.unstable;
   var keys = Object.keys( scan.channels );
   keys.sort();
   for ( var i = 0; i < keys.length; ++i )
   {
      var c = scan.channels[keys[i]];
      state.channels[keys[i]] = FrameSelector.recompute(
         FrameSelector.newChannel( keys[i], c.entries, c.metrics, c.problems ) );
      state.order.push( keys[i] );
   }
   return state;
};

/* ------------------------------------------------------------------------
 * The review dialog.
 * ---------------------------------------------------------------------- */

FrameSelector.Dialog = class extends Dialog
{
   constructor( state )
   {
   super();
   /*
    * Everything after super() is guarded: a half-built dialog must not keep
    * a timer, a handler or a bitmap. super() stays outside -- `this` does
    * not exist before it.
    */
   try
   {
      var self = this;
      /*
       * Fill in anything the caller left out. The dialog is handed a state by
       * buildState in normal use, but it is also constructed from a literal in
       * the suite and will be by anyone driving it from a console, and a
       * missing field must not surface as "cannot read length of undefined"
       * from inside the first refresh.
       */
      if ( state.unstable == null ) state.unstable = [];
      if ( state.phase == null )    state.phase = Frames.PHASE.REVIEW;
      if ( state.channels == null ) state.channels = {};
      if ( state.order == null )    state.order = [];
      this.state = state;
      this.current = state.order.length ? state.order[0] : null;

      this.windowTitle = "Loom Frame Selector";


      this.buildChannelTree();
      this.buildFrameTable();
      this.buildPreviewPane();
      this.buildKnobPanel();
      this.buildPlotPane();
      this.buildFilmstrip();
      this.buildActionRow();
      this.wireBehaviour();
      this.layOut();
   }
   catch ( e )
   {
      try { this.release(); } catch ( e2 ) {}
      throw e;
   }
   }

   /* The channel list: one row per filter, kept and total. */
   buildChannelTree()
   {
      var self = this;
      this.channelTree = new TreeBox( this );
      this.channelTree.alternateRowColor = true;
      this.channelTree.headerVisible = true;
      this.channelTree.numberOfColumns = 1;
      this.channelTree.setHeaderText( 0, "Channel" );
      this.channelTree.setScaledMinWidth( 200 );
      this.channelTree.onNodeSelectionUpdated = function()
      {
         var n = self.channelTree.selectedNodes;
         if ( n.length )
         {
            self.current = n[0].channelKey;
            self.fillFrames();
            self.fillPlot();
            self.syncKnobs();
            self.syncStrip();
            self.rebuildQueue();
         }
      };
   }

   /* The frame table for the selected channel. */
   /*
    * The measurement plot, with the metric it shows chosen by a combo --
    * four metrics is too many for a button that cycles, and the combo says
    * which one is on screen without being read twice.
    */
   buildPlotPane()
   {
      var self = this;

      this.plotCombo = new ComboBox( this );
      for ( var m = 0; m < Frames.DISPLAY_METRICS.length; ++m )
         this.plotCombo.addItem( Frames.METRIC_LABEL[Frames.DISPLAY_METRICS[m]] );
      this.plotCombo.currentItem = 0;
      this.plotCombo.toolTip = "<p>Which measurement the plot below shows.</p>";
      this.plotCombo.onItemSelected = function() { self.fillPlot(); };

      this.plotLabel = new Label( this );
      this.plotLabel.text = "Plot:";
      this.plotLabel.textAlignment = TextAlign_Right | TextAlign_VertCenter;

      this.plotBandLabel = new Label( this );
      this.plotBandLabel.useRichText = true;
      this.plotBandLabel.text = "";

      var head = new HorizontalSizer;
      head.spacing = 6;
      head.add( this.plotLabel );
      head.add( this.plotCombo );
      head.addSpacing( 8 );
      head.add( this.plotBandLabel );
      head.addStretch();

      this.plot = new FrameSelector.Plot( this );
      this.plot.onPick = function( index ) { self.selectRow( index ); };

      this.plotPane = new VerticalSizer;
      this.plotPane.spacing = 4;
      this.plotPane.add( head );
      this.plotPane.add( this.plot );
   }

   /*
    * Move the table's selection to a row, as though it had been clicked.
    *
    * Assigning currentNode does not fire onNodeSelectionUpdated, so the
    * preview and the plot's ring are updated here rather than left to a
    * handler that will not run.
    */
   /* Which row the table has selected, or -1. */
   selectedRowIndex()
   {
      if ( this.frameTree == null )
         return -1;
      var n = this.frameTree.selectedNodes;
      return ( n.length && n[0].rowIndex != null ) ? n[0].rowIndex : -1;
   }

   /*
    * `reload` is false when only the table was rebuilt and the same frame
    * is still selected -- re-reading a 26 MP frame to show what is already
    * on screen would make every knob cost a disk read.
    */
   selectRow( index, reload )
   {
      var node = this.rowNode( index );
      if ( node == null )
         return;
      /*
       * currentNode and node.selected, NOT an assignment to selectedNodes:
       * that property is read-only, and assigning it threw from inside the
       * plot's click handler -- an exception crossing back into Qt, which
       * takes the process with it.
       */
      this.frameTree.currentNode = node;
      node.selected = true;
      if ( reload !== false && node.rowRef )
         this.loadPreview( node.rowRef.path );
      this.plot.setSelected( index );
      this.preview.setTags( this.currentTags() );
      this.syncStrip();
      if ( reload !== false )
         this.rebuildQueue();
   }

   /* The table's node for a row, or null when there is no such row. */
   rowNode( index )
   {
      if ( this.frameTree == null || index < 0 || index >= this.frameTree.numberOfChildren )
         return null;
      return this.frameTree.child( index );
   }

   /* Load a frame into the preview; its thumbnail comes free from the render. */
   loadPreview( path )
   {
      if ( this.preview.load( path ) && this.thumbs != null && this.thumbs[path] == null )
         this.storeThumb( path, this.preview.thumbnail() );
   }

   /* The selected frame's tags: what Run does with it, then each anomaly. */
   currentTags()
   {
      var ch = this.channel(), i = this.selectedRowIndex();
      if ( ch == null || i < 0 || i >= ch.rows.length )
         return [];
      return Frames.frameTags( ch.rows[i], ch.flags ? ch.flags[i] : [],
                               ch.settings.enabled, this.copyingOut() );
   }

   /*
    * Pick the folder the approved frames are written to. A destination
    * inside the source folder is refused later by exportApproved, which
    * checks it against every source directory rather than trusting this.
    */
   chooseDestination()
   {
      if ( !this.editable() )
         return;
      var gd = new GetDirectoryDialog;
      gd.caption = "Folder for the approved frames";
      if ( gd.execute() )
         this.state.destination = gd.directoryPath;
   }

   /* The metric the combo currently names. */
   plotMetric()
   {
      var i = this.plotCombo ? this.plotCombo.currentItem : 0;
      return Frames.DISPLAY_METRICS[( i >= 0 && i < Frames.DISPLAY_METRICS.length ) ? i : 0];
   }

   fillPlot()
   {
      if ( this.plot == null )
         return;
      var ch = this.channel();
      var metric = this.plotMetric();
      if ( ch == null )
      {
         this.plot.setSeries( [], metric, null );
         this.plotBandLabel.text = "";
         return;
      }
      var band = Frames.acceptedBand( metric, ch.gates, ch.settings );
      this.plot.setSeries( ch.rows, metric, band );
      /*
       * Re-applied after a rebuild: refresh() repopulates the table, and
       * the ring has to follow the row that is still selected.
       */
      var sel = this.frameTree ? this.frameTree.selectedNodes : [];
      this.plot.setSelected( ( sel.length && sel[0].rowIndex != null )
                             ? sel[0].rowIndex : -1 );
      /*
       * The band in words as well as grey. An open end reads as "no limit"
       * rather than as a number, which is what an inactive gate means.
       */
      this.plotBandLabel.text =
         "<i>keeps " +
         ( band.lo == null ? "anything" : "&ge; " + Frames.round( band.lo ) ) +
         ( band.hi == null ? "" : ( band.lo == null ? "&le; " + Frames.round( band.hi )
                                                    : " and &le; " + Frames.round( band.hi ) ) ) +
         "</i>";
   }

   buildFrameTable()
   {
      var self = this;
      this.frameTree = new TreeBox( this );
      this.frameTree.alternateRowColor = true;
      var heads = Frames.FRAME_COLUMNS;
      this.frameTree.numberOfColumns = heads.length;
      for ( var h = 0; h < heads.length; ++h )
         this.frameTree.setHeaderText( h, heads[h] );
      this.frameTree.setScaledMinWidth( 560 );
      this.frameTree.onNodeSelectionUpdated = function()
      {
         /*
          * Through selectRow, the one path: the preview, the plot's ring,
          * the tags and the strip all follow. Assigning currentNode there
          * does not re-fire this handler.
          */
         var n = self.frameTree.selectedNodes;
         if ( n.length && n[0].rowRef )
            self.selectRow( n[0].rowIndex );
         else
            self.plot.setSelected( -1 );
      };
      /*
       * Space toggles the override on the selected row. Returning true
       * CONSUMES it, so the tree does not also treat it as an activation.
       */
      this.frameTree.onKeyPress = function( key, modifiers )
      {
         if ( key == KeyCode.Space ) { self.toggleOverride(); return true; }
         return false;
      };
   }

   /* The 1:1 preview and the override button under it. */
   buildPreviewPane()
   {
      var self = this;
      this.preview = new FrameSelector.PreviewControl( this );

      this.overrideButton = new PushButton( this );
      this.overrideButton.text = "Keep / drop this one";
      this.overrideButton.onClick = function() { self.toggleOverride(); };

      this.clearOverridesButton = new PushButton( this );
      this.clearOverridesButton.text = "Clear overrides";
      this.clearOverridesButton.onClick = function()
      {
         var ch = self.channel();
         if ( ch == null || !self.editable() )
            return;
         for ( var i = 0; i < ch.rows.length; ++i )
            ch.rows[i].override = null;
         self.refresh();
      };
   }

   /*
    * The Approval criteria panel: mode, preset, k and the per-channel
    * enable on the first row, one criterion per metric on the second --
    * checkbox, operator and the limit itself, which is the number that
    * decides. Everything is parented to the group box it sits in.
    */
   buildKnobPanel()
   {
      var self = this;
      this.criteriaGroup = new GroupBox( this );
      this.criteriaGroup.title = "Approval criteria";
      var box = this.criteriaGroup;

      this.presetCombo = new ComboBox( box );
      var presetNames = [ "lenient", "balanced", "strict" ];
      for ( var p = 0; p < presetNames.length; ++p )
         this.presetCombo.addItem( presetNames[p] );
      this.presetCombo.currentItem = presetNames.indexOf( Frames.DEFAULT_PRESET );
      this.presetCombo.toolTip =
         "<p>How hard this channel is cut. Each channel has its own: a " +
         "night's L and its Ha are not the same population.</p>";
      this.presetCombo.onItemSelected = function( i )
      {
         if ( !self.editable() )
            return;
         /*
          * THIS channel only. It used to set every channel at once, so
          * tightening a ragged Ha also cut into a clean L.
          *
          * A preset sets k unless k has been edited by hand; an edited
          * value stands, and editing a WEIGHT does not pin k.
          */
         var ch = self.channel();
         if ( ch == null )
            return;
         ch.settings = Frames.applyPreset( ch.settings, presetNames[i] );
         self.refresh();
      };

      this.kEdit = new NumericEdit( box );
      this.kEdit.label.text = "k";
      this.kEdit.setRange( 0.5, 10 );
      this.kEdit.setPrecision( 2 );
      this.kEdit.setValue( Frames.PRESETS[this.state.preset] || Frames.PRESETS[Frames.DEFAULT_PRESET] );
      this.kEdit.onValueUpdated = function( v )
      {
         var ch = self.channel();
         if ( ch == null || !self.editable() )
            return;
         ch.settings.k = v;
         ch.settings.kEdited = true;        // an edited k survives a preset change
         self.refresh();
      };

      this.enabledCheck = new CheckBox( box );
      this.enabledCheck.text = "Act on this channel";
      this.enabledCheck.checked = true;
      this.enabledCheck.onCheck = function( checked )
      {
         var ch = self.channel();
         if ( ch == null || !self.editable() )
            return;
         /*
          * Disabling suppresses ALL action on the channel, condemnations
          * included, and re-enabling restores them unchanged -- so the
          * overrides are left in place rather than cleared.
          */
         ch.settings.enabled = checked;
         self.refresh();
      };

      this.keepLabel = new Label( box );
      this.keepLabel.textAlignment = TextAlign_Right | TextAlign_VertCenter;
      this.keepLabel.toolTip =
         "<p>Frames of this channel that Run keeps: left in place when " +
         "culling, copied when the output is another folder.</p>";

      /*
       * One criterion per scoring metric. The checkbox says whether the
       * metric may reject at all -- separate from the weights: a metric can
       * rank frames without being allowed to delete one, and PSF SNR is
       * exactly that case, since integration already weights by signal.
       */
      this.critChecks = {}; this.critEdits = {};
      for ( var ci = 0; ci < Frames.CRITERIA_ORDER.length; ++ci )
         ( function( metric )
         {
            var cb = new CheckBox( box );
            cb.text = Frames.METRIC_HEADING[metric] + "  " + Frames.OPERATOR[metric];
            cb.toolTip = ( metric == "psfSNR" )
               ? ( "<p>Off by default. Integration weights each frame by its " +
                   "signal, so a faint frame already counts for less -- " +
                   "deleting it throws away signal that was being discounted " +
                   "correctly. Turn it on to catch a frame far below the rest, " +
                   "which usually means cloud rather than a dim night.</p>" )
               : ( "<p>Reject frames on " + Frames.METRIC_LABEL[metric] +
                   ". No weighting repairs this one, so a bad frame degrades " +
                   "the stack however little it is weighted.</p>" );
            cb.onCheck = function( checked )
            {
               var ch = self.channel();
               if ( ch == null || !self.editable() )
                  return;
               ch.settings.gating[metric] = checked;
               self.refresh();
            };
            var ed = new Edit( box );
            ed.setScaledFixedWidth( 70 );
            ed.toolTip = "<p>Grey: automatic -- the limit k times this night's " +
                         "spread gives. Type a number to set this metric's limit " +
                         "yourself; clear the box to go back to automatic.</p>";
            ed.onEditCompleted = function() { self.commitEdit( metric ); };
            self.critChecks[metric] = cb;
            self.critEdits[metric] = ed;
         } )( Frames.CRITERIA_ORDER[ci] );

      this.problemsLabel = new Label( this );
      this.problemsLabel.wordWrapping = true;
      this.problemsLabel.useRichText = false;

      this.summaryLabel = new Label( this );
      this.summaryLabel.wordWrapping = true;
   }

   /*
    * Apply one field to the settings, ONLY if the user typed in it, and
    * WITHOUT refreshing. Edit.modified is set by typing and reset whenever
    * the program assigns text, so focus loss or Run never turn a rounded
    * display back into the stored limit. Returns whether it was dirty.
    */
   applyEdit( metric )
   {
      var ch = this.channel(), ed = this.critEdits ? this.critEdits[metric] : null;
      if ( ch == null || ed == null || !ed.modified || !this.editable() )
         return false;
      // A number sets this metric's limit; a blank box goes back to automatic.
      var v = Frames.parseLimit( ed.text );
      if ( v !== undefined )
         ch.settings = Frames.withLimit( ch.settings, metric, v );
      return true;             // an invalid entry is redrawn from settings
   }

   commitEdit( metric )
   {
      if ( this.applyEdit( metric ) )
         this.refresh();
   }

   /*
    * Every dirty field FIRST, one refresh after. Refreshing between them
    * rewrites every field's text, which resets `modified` and would drop
    * every edit after the first.
    */
   commitPendingEdits()
   {
      var any = false;
      for ( var i = 0; i < Frames.CRITERIA_ORDER.length; ++i )
         any = this.applyEdit( Frames.CRITERIA_ORDER[i] ) || any;
      if ( any )
         this.refresh();
   }

   /*
    * The filmstrip and the loader that fills it: one frame per tick of a
    * single-shot Timer, re-armed after each, so ticks never overlap and
    * events get through between frames.
    */
   buildFilmstrip()
   {
      var self = this;
      this.thumbs = {};                  // path -> { bmp, used }
      this.thumbFailed = {};             // path -> true, never evicted
      this.thumbUse = 0;
      this.loadQueue = []; this.loadGeneration = 0; this.ticksObserved = 0;
      this.released = false;
      this.shown = false;
      this.filmstrip = new FrameSelector.Filmstrip( this );
      this.filmstrip.onPick = function( i ) { self.selectRow( i ); };
      /*
       * Which measurement is printed on the tiles. Its own chooser, beside
       * the strip, so the plot can show one thing and the tiles another.
       */
      this.stripMetric = new ComboBox( this );
      for ( var sm = 0; sm < Frames.DISPLAY_METRICS.length; ++sm )
         this.stripMetric.addItem( Frames.METRIC_HEADING[Frames.DISPLAY_METRICS[sm]] );
      this.stripMetric.currentItem = Frames.DISPLAY_METRICS.indexOf( "fwhm" );
      this.stripMetric.toolTip = "<p>The measurement printed on each frame below.</p>";
      this.stripMetric.onItemSelected = function( i )
      {
         self.filmstrip.setMetric( Frames.DISPLAY_METRICS[i] );
      };
      this.stripPrev = new ToolButton( this );
      this.stripPrev.text = "\u2039";
      this.stripPrev.toolTip = "Previous page of frames";
      this.stripNext = new ToolButton( this );
      this.stripNext.text = "\u203a";
      this.stripNext.toolTip = "Next page of frames";
      this.stripPrev.onClick = function() { self.filmstrip.page( -1 ); self.rebuildQueue(); };
      this.stripNext.onClick = function() { self.filmstrip.page( 1 ); self.rebuildQueue(); };
      /*
       * NOT started here: the constructor may still throw after this, and
       * a running timer on a dialog nobody holds is what must not exist.
       * onShow arms it, once the entry point owns the object.
       */
      this.loaderTimer = new Timer;
      this.loaderTimer.singleShot = true;
      this.loaderTimer.interval = 0.05;     // seconds
      this.loaderTimer.onTimeout = function() { self.loaderTick(); };
      this.onShow = function() { self.shown = true; self.armLoader(); };
   }

   /*
    * Never before onShow: the constructor's own selectRow(0) queues work,
    * and the timer must not run on an object the constructor might still
    * abandon by throwing.
    */
   armLoader()
   {
      if ( !this.mayLoad() || this.loadQueue.length == 0 || this.loaderTimer.isRunning )
         return;
      this.loaderTimer.start();
   }

   /*
    * Loading happens only while the dialog is on screen. A dialog closed
    * without onClose firing -- cancel() on one opened with show() does
    * not fire it -- stops at its next tick instead of reading on behind a
    * window nobody can see. No hide handler is installed for this: a
    * handler reached during teardown is what took PixInsight down in
    * v0.1.4, so the check is made here, by the loader itself.
    */
   mayLoad()
   {
      return !this.released && this.shown && this.visible && this.loaderTimer != null;
   }

   /* Rebuilt on selection, page and channel change; stale results are dropped. */
   rebuildQueue()
   {
      if ( this.filmstrip == null || this.released )
         return;
      var ch = this.channel();
      ++this.loadGeneration;
      this.loadQueue = [];
      if ( ch == null )
         return;
      var order = Frames.thumbnailOrder( ch.rows.length, this.selectedRowIndex(),
                                         this.filmstrip.first, this.filmstrip.visibleCount() );
      for ( var i = 0; i < order.length; ++i )
      {
         var p = ch.rows[order[i]].path;
         if ( this.thumbs[p] == null && !this.thumbFailed[p] )
            this.loadQueue.push( { path: p, key: ch.key, generation: this.loadGeneration } );
      }
      this.pushThumbs();
      this.armLoader();
   }

   loaderTick()
   {
      if ( !this.mayLoad() )
         return;                     // not re-armed: loading stops here
      try
      {
         ++this.ticksObserved;
         var item = this.loadQueue.shift();
         if ( item == null )
            return;
         var bmp = FrameSelector.thumbnailOf( item.path );
         // The dialog may have been released, or moved on, while reading.
         if ( this.released || item.generation != this.loadGeneration )
            return;
         this.storeThumb( item.path, bmp );
         this.pushThumbs();
      }
      catch ( e )
      {
         Util.warn( "frames", "thumbnail loading stopped: " + e );
         this.loadQueue = [];
      }
      finally
      {
         this.armLoader();           // re-arms only while mayLoad()
      }
   }

   /*
    * LRU over bitmaps, capped at THUMB_CAP (about 24 MB). `used` is
    * refreshed whenever a thumbnail is SHOWN (pushThumbs), not only when
    * stored, so what is on screen is never the oldest. Failures are kept
    * apart and never evicted: a frame that could not be read is not
    * retried in this dialog.
    */
   storeThumb( path, bmp )
   {
      if ( bmp == null )
      {
         this.thumbFailed[path] = true;
         return;
      }
      this.thumbs[path] = { bmp: bmp, used: ++this.thumbUse };
      var keys = Object.keys( this.thumbs );
      if ( keys.length <= FrameSelector.THUMB_CAP )
         return;
      var self = this;
      keys.sort( function( a, b ) { return self.thumbs[a].used - self.thumbs[b].used; } );
      for ( var i = 0; i < keys.length - FrameSelector.THUMB_CAP; ++i )
         delete this.thumbs[keys[i]];
   }

   /* Hand the strip what exists for the current channel, touching it. */
   pushThumbs()
   {
      var ch = this.channel();
      if ( ch == null || this.filmstrip == null || this.released )
         return;
      var arr = [], failed = [];
      for ( var i = 0; i < ch.rows.length; ++i )
      {
         var p = ch.rows[i].path, t = this.thumbs[p];
         if ( t != null )
            t.used = ++this.thumbUse;
         arr.push( t ? t.bmp : null );
         failed.push( !!this.thumbFailed[p] );
      }
      this.filmstrip.thumbs = arr;
      this.filmstrip.failed = failed;
      this.filmstrip.update();
   }

   /* Rows, crosses, badges and selection into the strip. */
   syncStrip()
   {
      var ch = this.channel();
      if ( ch == null || this.filmstrip == null || this.released )
         return;
      var copying = this.copyingOut();
      var crossed = ch.rows.map( function( r )
         { return Frames.leftOut( r, ch.settings.enabled, copying ); } );
      this.filmstrip.setRows( ch.rows, ch.flags, crossed, this.selectedRowIndex() );
      this.pushThumbs();
   }

   /* Apply and Close. */
   buildActionRow()
   {
      var self = this;

      /*
       * Where the frames go. There is no mode to pick: the destination
       * starts as the folder they came from, which means culling them
       * where they are, and pointing it elsewhere makes it a copy that
       * leaves every original alone. One control, and a sentence saying
       * which of the two it currently means.
       */
      this.destLabelPrefix = new Label( this );
      this.destLabelPrefix.text = "output:";

      this.destButton = new PushButton( this );
      this.destButton.text = "Folder...";
      this.destButton.toolTip =
         "<p>Leave it on the folder the frames came from to cull them where " +
         "they are. Point it anywhere else and the kept frames are copied " +
         "there instead, and every original is left alone.</p>";
      this.destButton.onClick = function() { self.chooseDestination(); self.refresh(); };

      this.destLabel = new Label( this );
      this.destLabel.useRichText = true;
      this.destLabel.text = "";

      this.applyButton = new PushButton( this );
      /*
       * "Run", not a name for whichever action is armed. The label beside
       * the folder says what will happen; a button that renames itself
       * moves that sentence somewhere it is easy to miss.
       */
      this.applyButton.text = "Run";
      this.applyButton.onClick = function() { self.commit(); };

      this.closeButton = new PushButton( this );
      this.closeButton.text = "Close";
      /*
       * Released on the way out, NOT by overriding cancel(). Dialog.cancel
       * is a native method and super.cancel() threw from it, which broke
       * the dialog outright -- caught only because the suite builds one and
       * closes it.
       */
      this.closeButton.onClick = function() { self.release(); self.cancel(); };
      // The window's own close box does not go through the Close button.
      this.onClose = function() { self.release(); };
   }

   /* What every control does, and how the table is refreshed. */
   wireBehaviour()
   {
      var self = this;







      /*
       * Recompute every verdict FROM THE COHORT. Never from survivors: that
       * would tighten the MAD after each deletion and condemn a frame nobody
       * looked at.
       */


      /*
       * Build the manifest and run it. The manifest is a snapshot: once this
       * returns the review is locked, so no knob change can alter what is
       * already deleting files, and pressing Apply twice cannot run two
       * manifests over one cohort.
       */
   }

   editable()
   {
      var self = this;
            return Frames.canEdit( self.state.phase );
   }

   channel()
   {
      var self = this;
            return ( self.current != null ) ? self.state.channels[self.current] : null;
   }

   toggleOverride()
   {
      var self = this;
            var ch = self.channel();
            if ( ch == null || !self.editable() )
               return;
            var n = self.frameTree.selectedNodes;
            if ( !n.length || !n[0].rowRef )
               return;
            var row = n[0].rowRef;
            /*
             * An unmeasurable frame may be condemned by hand but never approved:
             * there is no measurement to approve. finalState enforces that; here
             * the toggle simply offers the two states that mean anything.
             */
            if ( row.override != null )
               row.override = null;
            else
               row.override = ( row.state == Frames.STATE.REJECTED )
                              ? Frames.OVERRIDE.RESCUED : Frames.OVERRIDE.CONDEMNED;
            self.refresh();
   }

   fillChannels()
   {
      var self = this;
            self.channelTree.clear();
            for ( var i = 0; i < self.state.order.length; ++i )
            {
               var key = self.state.order[i], ch = self.state.channels[key];
               var node = new TreeBoxNode( self.channelTree );
               node.channelKey = key;
               var c = Frames.counts( ch.rows );
               node.setText( 0, Frames.summaryLine( key, c ) +
                                ( ch.settings.enabled ? "" : "  [off]" ) );
               if ( key == self.current )
                  node.selected = true;
            }
   }

   fillFrames()
   {
      var self = this;
            self.frameTree.clear();
            var ch = self.channel();
            if ( ch == null )
               return;
            /*
             * Names are shortened against the whole channel, not one at a
             * time: what can be dropped is what every frame here shares.
             */
            var paths = [];
            for ( var pn = 0; pn < ch.rows.length; ++pn )
               paths.push( ch.rows[pn].path );
            var shortNames = Frames.shortNames( paths );

            for ( var i = 0; i < ch.rows.length; ++i )
               self.fillRow( new TreeBoxNode( self.frameTree ), ch.rows[i], i,
                             shortNames[i], ch.flags ? ch.flags[i] : [] );
   }

   /* One frame's row: name, measurements, score, verdict, marks. */
   fillRow( node, row, index, shortName, flags )
   {
      node.rowRef = row;
      node.rowIndex = index;      // which point on the plot this row is
      node.setText( 0, shortName );
      if ( row.metrics != null )
         for ( var mc = 0; mc < Frames.DISPLAY_METRICS.length; ++mc )
         {
            var mn = Frames.DISPLAY_METRICS[mc];
            node.setText( Frames.metricColumn( mn ), Frames.displayValue( mn, row.metrics[mn] ) );
         }
      node.setText( Frames.SCORE_COLUMN, ( row.score == null ) ? "-" : Frames.round( row.score ) );
      /*
       * The verdict in words goes on the row rather than into a column of
       * its own -- alongside the name, so hovering a frame answers both
       * "which file" and "why".
       */
      node.setToolTip( 0, Frames.rowTooltip( row, flags ) );
      FrameSelector.markRejected( node, row );
   }

   /*
    * The current channel's flags, for the summary. A channel that is not
    * comparable says why it has none rather than looking clean.
    */
   flagLine()
   {
      var ch = this.channel();
      if ( ch == null )
         return "";
      if ( ch.problems.length || !Frames.autoRejectAllowed( ch.key ) )
         return "  |  not flagged: frames are not comparable";
      var fs = Frames.flagSummary( ch.flags || [] );
      return fs ? "  |  " + fs : "";
   }

   /*
    * The criteria row for one channel. Assigning an Edit's text resets its
    * `modified` flag, so a redraw never leaves a field looking typed-in.
    */
   syncCriteria( ch )
   {
      for ( var i = 0; i < Frames.CRITERIA_ORDER.length; ++i )
      {
         var m = Frames.CRITERIA_ORDER[i], on = !!ch.settings.gating[m];
         this.critChecks[m].checked = on;
         var ed = this.critEdits[m], shown = Frames.displayLimit( m, ch.gates, ch.settings );
         ed.text = Frames.formatLimit( m, shown.value );
         // Grey and italic while automatic; plain once a number is typed.
         ed.styleSheet = shown.auto ? FrameSelector.AUTO_STYLE : "";
         ed.enabled = on;
      }
      var kc = Frames.keepCount( ch.rows, ch.settings.enabled, this.copyingOut() );
      this.keepLabel.text = kc.keep + " / " + kc.total + " keep";
   }

   syncKnobs()
   {
      var self = this;
            var ch = self.channel();
            if ( ch == null )
            {
               self.problemsLabel.text = "";
               return;
            }
            self.kEdit.setValue( ch.settings.k );
            self.enabledCheck.checked = ch.settings.enabled;
            var names = [ "lenient", "balanced", "strict" ];
            var pi = names.indexOf( ch.settings.preset );
            if ( pi >= 0 )
               self.presetCombo.currentItem = pi;
            self.syncCriteria( ch );
            /*
             * A mixed channel says what is mixed. "Not comparable" without the
             * reason leaves someone to guess whether to trust it.
             */
            self.problemsLabel.text = ch.problems.length
               ? ( "Not comparable: " + ch.problems.join( ", " ) +
                   ". The figures for this channel compare frames that are not "
                   + "alike; look before running." )
               : ( ch.settings.kEdited ? "k has been set by hand for this channel; a "
                                       + "preset will not change it." : "" );
   }

         /*
          * Which rows a Run acts on, and the per-channel tally the
          * confirmation shows. A channel switched off contributes nothing;
          * buildManifest then decides row by row within what is left.
          *
          * A channel whose frames are not comparable is NOT withheld. The
          * mixture is reported, loudly, and the decision is the user's --
          * a tool that measures frames and then refuses to act on its own
          * measurements is only an obstacle.
          *
          * Separated from commit so that what the confirmation is counting can be
          * asked for without putting a modal box on screen.
          */
   committableRows()
   {
      var self = this;
            var rows = [], perChannel = [];
            for ( var i = 0; i < self.state.order.length; ++i )
            {
               var key = self.state.order[i], ch = self.state.channels[key];
               if ( !ch.settings.enabled )
                  continue;                    // a channel switched off is untouched
               var c = Frames.counts( ch.rows );
               if ( c.rejected )
                  perChannel.push( key + ": " + c.rejected );
               for ( var r = 0; r < ch.rows.length; ++r )
                  rows.push( ch.rows[r] );
            }
            return { rows: rows, perChannel: perChannel };
   }


   /* Recompute every verdict from the cohort and redraw. */
   refresh()
   {
      var t = this.recomputeAll();
      /*
       * fillFrames rebuilds every node, which drops the selection -- so
       * changing a knob used to blank the preview and the ring and leave
       * the table looking at nothing.
       */
      var keep = this.selectedRowIndex();
      this.fillChannels();
      this.fillFrames();
      if ( keep >= 0 )
         this.selectRow( keep, false/*reload*/ );
      this.fillPlot();
      this.syncKnobs();
      this.summaryLabel.text = this.summaryText( t );
      /*
       * Criterion and override changes do not reload the bitmap, so the
       * tags are repainted here rather than on the next load.
       */
      this.preview.setTags( this.currentTags() );
      // Knobs change crosses, not which frames need reading.
      this.syncStrip();
      this.syncActions( t );
   }

   /* Every channel's verdicts from its cohort, and the totals Run acts on. */
   recomputeAll()
   {
      var t = { total: 0, condemned: 0, mixed: [] };
      for ( var i = 0; i < this.state.order.length; ++i )
      {
         var key = this.state.order[i], ch = this.state.channels[key];
         FrameSelector.recompute( ch );
         var c = Frames.counts( ch.rows );
         t.total += c.total;
         if ( !ch.settings.enabled )
            continue;
         t.condemned += c.rejected;
         // Reported, not withheld.
         if ( ch.problems.length && c.rejected )
            t.mixed.push( key );
      }
      t.approved = t.total - t.condemned;
      return t;
   }

   summaryText( t )
   {
      var unstable = this.state.unstable.length;
      return t.total + " frame(s), " + t.condemned + " to remove" +
         ( unstable ? "; " + unstable + " changed while being measured and were excluded" : "" ) +
         ( t.mixed.length ? "; " + t.mixed.join( ", " ) +
                            " not comparable -- check before running" : "" ) +
         ( this.copyingOut() ? "; " + t.approved + " approved to copy" : "" ) +
         this.flagLine();
   }

   /*
    * Run is enabled when there is something to do, and nothing else. A
    * channel that is not comparable is reported and left to the user; it
    * does not disable the button.
    */
   syncActions( t )
   {
      var copying = this.copyingOut();
      this.applyButton.enabled = this.editable() &&
         ( copying ? ( t.approved > 0 && this.state.destination != null )
                   : t.condemned > 0 );
      this.destButton.enabled = this.editable();
      this.destLabel.text = this.destinationText( t );
   }

   /*
    * Say which of the two things Run will do. The destructive one must
    * never be reached by a label that implies the other.
    */
   destinationText( t )
   {
      if ( this.state.destination == null )
         return "<i>no folder chosen</i>";
      var where = "<i>" + Util.elideHead( this.state.destination, 40 ) + "</i>";
      if ( !this.destinationIsSource() )
         return where + " &mdash; the " + t.approved + " kept frame(s) are copied " +
                "there; nothing is deleted.";
      var toConvert = Frames.needingXisf( this.approvedPaths() ).length;
      return where + " &mdash; <b>the folder the frames are in.</b> Nothing is " +
             "copied; the " + t.condemned + " rejected frame(s) are " +
             "<b>deleted</b> where they are" +
             ( toConvert ? ( ", and " + toConvert +
                             " kept frame(s) converted to XISF in place" ) : "" ) + ".";
   }

   /* Snapshot the review into a manifest and run it. */
   /* Every frame in the review, whatever its verdict. */
   allPaths()
   {
      var out = [];
      for ( var i = 0; i < this.state.order.length; ++i )
      {
         var ch = this.state.channels[this.state.order[i]];
         for ( var r = 0; r < ch.rows.length; ++r )
            out.push( ch.rows[r].path );
      }
      return out;
   }

   /*
    * The chosen folder is where the frames already are, so there is
    * nothing to copy: the request is to cull them where they sit.
    */
   destinationIsSource()
   {
      return Frames.destinationIsSource( this.allPaths(), this.state.destination );
   }

   /*
    * Let go of every control's handlers before the dialog is destroyed.
    * Safe to call twice: closing by the button and by the window's close
    * box both arrive here.
    */
   release()
   {
      /*
       * Idempotent, and the loader goes FIRST: a tick that ran after this
       * would read into, and paint, a dialog being torn down.
       */
      if ( this.released )
         return;
      this.released = true;
      try { this.stopLoader(); } catch ( e ) {}
      try { this.detachHandlers(); } catch ( e2 ) {}
   }

   /* The thumbnail loader: timer stopped, work dropped, strip let go. */
   stopLoader()
   {
      if ( this.loaderTimer != null )
         this.loaderTimer.stop();
      FrameSelector.detach( this.loaderTimer, [ "onTimeout" ] );
      this.loadQueue = [];
      this.onShow = null;
      if ( this.filmstrip != null )
         this.filmstrip.release();
      FrameSelector.detach( this.stripMetric, [ "onItemSelected" ] );
      FrameSelector.detach( this.stripPrev, [ "onClick" ] );
      FrameSelector.detach( this.stripNext, [ "onClick" ] );
      this.thumbs = {};
      this.thumbFailed = {};
   }

   /* Every other control's handlers, and the preview's and plot's own. */
   detachHandlers()
   {
      if ( this.preview != null ) this.preview.release();
      if ( this.plot != null ) this.plot.release();
      FrameSelector.detach( this.frameTree, [ "onNodeSelectionUpdated" ] );
      FrameSelector.detach( this.channelTree, [ "onNodeSelectionUpdated" ] );
      for ( var m in ( this.critEdits || {} ) )
         FrameSelector.detach( this.critEdits[m], [ "onEditCompleted" ] );
      for ( var c in ( this.critChecks || {} ) )
         FrameSelector.detach( this.critChecks[c], [ "onCheck" ] );
      FrameSelector.detach( this.presetCombo, [ "onItemSelected" ] );
      FrameSelector.detach( this.kEdit, [ "onValueUpdated" ] );
      FrameSelector.detach( this.enabledCheck, [ "onCheck" ] );
   }

   /*
    * True when Run writes copies instead of deleting originals.
    *
    * Choosing the input folder deliberately falls back to the deleting
    * path rather than being refused: copying a file onto itself is not
    * what was meant by it.
    */
   copyingOut()
   {
      return this.state.destination != null && !this.destinationIsSource();
   }

   /*
    * Write the approved frames to the chosen folder. Nothing is deleted,
    * so this asks once and reports rather than demanding the confirmation
    * the destructive path needs.
    */
   /* The frames that survive, across every channel being acted on. */
   approvedPaths()
   {
      var approved = [];
      for ( var i = 0; i < this.state.order.length; ++i )
      {
         var ch = this.state.channels[this.state.order[i]];
         if ( !ch.settings.enabled )
            continue;
         for ( var r = 0; r < ch.rows.length; ++r )
         {
            var row = ch.rows[r];
            if ( Frames.finalState( row.state, row.override ) != Frames.STATE.REJECTED )
               approved.push( row.path );
         }
      }
      return approved;
   }

   /*
    * What a finished run converted, said plainly. Nothing to convert is
    * not worth a message of its own -- the deletion already reported.
    */
   reportOutcome( result )
   {
      var c = result ? result.converted : null;
      if ( c == null || ( c.converted == 0 && c.failed == 0 && c.refused == null ) )
         return;
      var message = ( c.refused != null )
         ? ( "Nothing was converted: " + c.refused )
         : ( c.converted + " frame(s) converted to XISF in place" +
             ( c.alreadyXisf ? ( "; " + c.alreadyXisf + " already were" ) : "" ) +
             ( c.failed ? ( "\n\n" + c.failed + " could not be converted." ) : "" ) );
      ( new MessageBox( message, "Loom Frame Selector", StdIcon_Information,
                        StdButton_Ok ) ).execute();
   }

   commitCopy()
   {
      var approved = this.approvedPaths();
      if ( approved.length == 0 )
         return null;

      var result = FrameSelector.exportApproved( approved, this.state.destination );
      var message = ( result.refused != null )
         ? ( "Nothing was written: " + result.refused )
         : ( result.written + " frame(s) written to\n" + this.state.destination +
             ( result.failed ? ( "\n\n" + result.failed + " could not be written." )
                             : "" ) );
      ( new MessageBox( message, "Loom Frame Selector", StdIcon_Information,
                        StdButton_Ok ) ).execute();
      return result;
   }

   commit()
   {
      var self = this;
            if ( !self.editable() )
               return null;
            // A limit typed but not yet confirmed is part of what Run acts on.
            self.commitPendingEdits();
            if ( self.copyingOut() )
               return self.commitCopy();
            var committable = self.committableRows();
            var manifest = Frames.buildManifest( committable.rows );
            if ( manifest.entries.length == 0 )
               return null;

            var mb = new MessageBox(
               "Delete " + manifest.entries.length + " frame(s)?\n\n" +
               committable.perChannel.join( "\n" ) + "\n\nThis cannot be undone.",
               "Loom Frame Selector", StdIcon_Warning,
               StdButton_Yes, StdButton_No );
            if ( mb.execute() != StdButton_Yes )
               return null;

            self.state.phase = Frames.nextPhase( self.state.phase, "commit" );
            self.state.locked = true;
            self.state.manifest = manifest;
            var result = FrameSelector.execute( manifest );

            /*
             * Asked for the input folder as the destination: the rejected
             * frames have just gone, and what remains is converted where it
             * sits. A folder already in XISF is a no-op, which is the
             * common case and costs nothing to say.
             *
             * After the deletion, deliberately: converting a frame that is
             * about to be removed is work thrown away.
             */
            if ( self.destinationIsSource() )
               result.converted = FrameSelector.convertInPlace( self.approvedPaths() );

            self.state.phase = Frames.nextPhase( self.state.phase,
                                                 result.stopped ? "stop" : "finish" );
            self.refresh();
            self.reportOutcome( result );
            return result;
   }


   /* Sizers only: what goes where. */
   /* The Approval criteria group's two rows. */
   layOutCriteria()
   {
      var box = this.criteriaGroup;
      var presetLabel = new Label( box );
      presetLabel.text = "Preset";
      presetLabel.textAlignment = TextAlign_Right | TextAlign_VertCenter;

      var row1 = new HorizontalSizer;
      row1.spacing = 6;
      row1.add( presetLabel );
      row1.add( this.presetCombo );
      row1.add( this.kEdit );
      row1.addSpacing( 12 );
      row1.add( this.enabledCheck );
      row1.addStretch();
      row1.add( this.keepLabel );

      var row2 = new HorizontalSizer;
      row2.spacing = 6;
      for ( var i = 0; i < Frames.CRITERIA_ORDER.length; ++i )
      {
         var m = Frames.CRITERIA_ORDER[i];
         row2.add( this.critChecks[m] );
         row2.add( this.critEdits[m] );
         row2.addSpacing( 16 );
      }
      row2.addStretch();

      box.sizer = new VerticalSizer;
      box.sizer.margin = 6;
      box.sizer.spacing = 6;
      box.sizer.add( row1 );
      box.sizer.add( row2 );
   }

   layOut()
   {
      var self = this;
      this.layOutCriteria();

      var previewSide = new VerticalSizer;
      previewSide.spacing = 6;
      previewSide.add( this.preview );
      previewSide.add( this.overrideButton );
      previewSide.add( this.clearOverridesButton );

      var middle = new HorizontalSizer;
      middle.spacing = 6;
      middle.add( this.channelTree );
      middle.add( this.frameTree, 100 );
      middle.add( previewSide );

      var dest = new HorizontalSizer;
      dest.spacing = 6;
      dest.add( this.destLabelPrefix );
      dest.add( this.destButton );
      dest.add( this.destLabel );
      dest.addStretch();

      var buttons = new HorizontalSizer;
      buttons.spacing = 6;
      buttons.addStretch();
      buttons.add( this.applyButton );
      buttons.add( this.closeButton );

      this.sizer = new VerticalSizer;
      this.sizer.margin = 8;
      this.sizer.spacing = 6;
      this.sizer.add( middle, 100 );
      this.sizer.add( this.plotPane );
      var strip = new HorizontalSizer;
      strip.spacing = 4;
      strip.add( this.stripMetric );
      strip.add( this.stripPrev );
      strip.add( this.filmstrip, 100 );
      strip.add( this.stripNext );
      this.sizer.add( strip );
      this.sizer.add( this.criteriaGroup );
      this.sizer.add( this.problemsLabel );
      this.sizer.add( this.summaryLabel );
      this.sizer.add( dest );
      this.sizer.add( buttons );

      this.refresh();
      this.adjustToContents();

      /*
       * Open on the first frame rather than on an empty pane.
       *
       * The preview is the reason to look at a frame at all, and it used
       * to stay blank until a row was clicked -- so the review opened
       * showing a table, a plot and a hole. The first row is as good a
       * starting point as any and costs one image read.
       */
      this.selectRow( 0 );
   }
};


/*
 * Measuring first is not optional: routines 1 and 2 refuse with "No
 * measurements have been made". False means that measurement pass failed,
 * so nothing was written.
 */
FrameSelector.runOutputRoutine = function( sources, destination, overwrite )
{
   var P = FrameSelector.newMeasureProcess();
   var rows = [];
   for ( var t = 0; t < sources.length; ++t )
      rows.push( [ true, sources[t], "", "" ] );
   P.subframes = rows;
   if ( !P.executeGlobal() )
      return false;

   /*
    * By NAME. The literal 2 was here, taken to be "output"; it is not --
    * SubframeSelector.OutputSubframes is 1 -- and routine 2 returns true
    * having written nothing, so no frame was ever converted or copied.
    */
   P.routine = SubframeSelector.OutputSubframes;
   P.outputDirectory = destination;
   P.overwriteExistingFiles = !!overwrite;
   /*
    * No prefix and no postfix, always: both callers check for the output
    * under the name Frames.outputMapping gives it, which is the source's
    * own name with the new extension. The process's default postfix is
    * "_a", which wrote a_a.xisf where a.xisf was looked for.
    */
   P.outputPrefix = "";
   P.outputPostfix = "";
   return P.executeGlobal();
};

/*
 * Convert frames to XISF where they already are, and remove what they
 * were converted from.
 *
 * Only for a destination that IS the source folder, where copying makes
 * no sense. A folder already in XISF is untouched and reports as such --
 * the conversion is the work, not the walk over the list.
 *
 * The original is deleted only once its replacement has been confirmed on
 * disk, so a failed write costs nothing.
 */
FrameSelector.convertInPlace = function( paths )
{
   var result = { converted: 0, failed: 0, alreadyXisf: 0,
                  refused: null, outcomes: {} };
   if ( paths == null || paths.length == 0 )
      return result;

   var todo = Frames.needingXisf( paths );
   result.alreadyXisf = paths.length - todo.length;
   if ( todo.length == 0 )
      return result;                       // nothing to do, and that is fine

   var grouped = Frames.byDirectory( todo );
   for ( var g = 0; g < grouped.order.length; ++g )
   {
      var dir = grouped.order[g];
      if ( !FrameSelector.convertGroup( grouped.dirs[dir], dir, result ) )
         return result;                    // refused: nothing further is attempted
   }
   return result;
};

/*
 * Convert one folder's frames. Returns false when the conversion is refused
 * -- result.refused says why -- and nothing more should be attempted.
 */
FrameSelector.convertGroup = function( group, dir, result )
{
   /*
    * a.fit and a.fits in one folder both become a.xisf. Converting either
    * would destroy the other's output, so neither is attempted.
    */
   var map = Frames.outputMapping( group, dir, Frames.XISF );
   if ( map.collisions.length > 0 )
   {
      result.refused = "two or more frames would convert to the same name: " +
                       map.collisions.join( ", " );
      Util.error( "frames", result.refused );
      return false;
   }
   try
   {
      if ( !FrameSelector.runOutputRoutine( group, dir, true/*overwrite*/ ) )
      {
         result.refused = "SubframeSelector's measurement or output pass failed";
         return false;
      }
   }
   catch ( e )
   {
      result.refused = String( e );
      Util.error( "frames", "conversion failed: " + e );
      return false;
   }
   for ( var i = 0; i < group.length; ++i )
      FrameSelector.settleConverted( group[i], map.mapping[group[i]], result );
   return true;
};

/*
 * One frame after the output pass: the original is removed only once its
 * XISF is confirmed on disk, so a failed write costs nothing.
 */
FrameSelector.settleConverted = function( src, out, result )
{
   if ( !File.exists( out ) )
   {
      result.outcomes[src] = "not converted";
      ++result.failed;
      Util.warn( "frames", "not converted, left alone: " + src );
      return;
   }
   try
   {
      File.remove( src );
      result.outcomes[src] = "converted";
      ++result.converted;
   }
   catch ( e )
   {
      // The XISF is there; only the original is left behind.
      result.outcomes[src] = "converted, original kept";
      ++result.failed;
      Util.warn( "frames", "converted but could not remove " + src + ": " + e );
   }
};

/*
 * Write the approved frames somewhere else, using SubframeSelector's output
 * routine. The output folder is EMPTIED first -- everything in it,
 * subfolders too, without asking -- so afterwards it holds exactly this
 * run's frames. No original is ever touched, and success is confirmed per
 * file rather than assumed.
 *
 * Refused before anything is removed if two sources would produce one
 * output name, or if the folder is, or contains, a source folder (see
 * Frames.emptyRefusal).
 */
FrameSelector.exportApproved = function( approved, destination )
{
   var result = { written: 0, failed: 0, emptied: 0, refused: null, outcomes: {} };
   if ( approved == null || approved.length == 0 )
      return result;

   var map = Frames.outputMapping( approved, destination, ".xisf" );
   result.refused = FrameSelector.exportRefusal( map ) ||
                    Frames.emptyRefusal( approved, destination, File.homeDirectory );
   if ( result.refused != null )
   {
      Util.error( "frames", result.refused );
      return result;
   }

   try
   {
      if ( !File.directoryExists( destination ) )
         File.createDirectory( destination, true );
      /*
       * Emptied first, everything in it, so the folder holds exactly this
       * run's frames afterwards. If anything cannot be removed, nothing is
       * copied: a half-emptied folder mixed with new frames is the one
       * outcome worse than either.
       */
      var emptied = FrameSelector.emptyDirectory( destination );
      result.emptied = emptied.removed;
      if ( emptied.failed.length > 0 )
      {
         result.refused = "could not empty the output folder (" + emptied.failed.length +
                          " item(s), e.g. " + emptied.failed[0] + "); nothing was copied";
         Util.error( "frames", result.refused );
         return result;
      }
      if ( !FrameSelector.runOutputRoutine( approved, destination, true/*overwrite*/ ) )
      {
         result.refused = "SubframeSelector's measurement or output pass failed";
         return result;
      }
   }
   catch ( e )
   {
      result.refused = String( e );
      Util.error( "frames", "export failed: " + e );
      return result;
   }
   FrameSelector.confirmWritten( approved, map, result );
   return result;
};

/*
 * Remove everything inside a folder -- files, hidden files, subfolders --
 * leaving the folder itself. A symbolic link is removed as a link and never
 * followed: emptying the output must not reach whatever a link points at.
 * Returns what was removed and what could not be.
 */
FrameSelector.emptyDirectory = function( dir )
{
   var out = { removed: 0, failed: [] };
   var entries = [], find = new FileFind;
   if ( find.begin( dir + "/*" ) )
      do
      {
         if ( find.name != "." && find.name != ".." )
            entries.push( { path: dir + "/" + find.name,
                            folder: find.isDirectory && !find.isSymbolicLink } );
      }
      while ( find.next() );
   for ( var i = 0; i < entries.length; ++i )
      FrameSelector.removeEntry( entries[i], out );
   return out;
};

FrameSelector.removeEntry = function( entry, out )
{
   try
   {
      if ( entry.folder )
      {
         var inner = FrameSelector.emptyDirectory( entry.path );
         out.removed += inner.removed;
         out.failed = out.failed.concat( inner.failed );
         if ( inner.failed.length > 0 )
            return;                          // a folder that is not empty stays
         File.removeDirectory( entry.path );
      }
      else
         File.remove( entry.path );
      ++out.removed;
   }
   catch ( e )
   {
      out.failed.push( entry.path );
   }
};

/*
 * Why an export must not start, or null. Two sources sharing an output name
 * would overwrite each other; a destination that is a source folder would
 * write over the originals.
 */
FrameSelector.exportRefusal = function( map )
{
   if ( map.collisions.length > 0 )
      return "two or more frames would be written to the same name: " +
             map.collisions.join( ", " );
   if ( map.aliased )
      return "the destination is one of the source folders";
   return null;
};

/*
 * Confirmed per file rather than assumed from the process returning true,
 * so a retry knows which outputs this run actually produced.
 */
FrameSelector.confirmWritten = function( written, map, result )
{
   for ( var i = 0; i < written.length; ++i )
   {
      var s = written[i];
      if ( File.exists( map.mapping[s] ) )
         { result.outcomes[s] = "written"; ++result.written; }
      else
         { result.outcomes[s] = "missing"; ++result.failed; }
   }
};

/*
 * Progress while a folder is read.
 *
 * The scan runs before the review dialog exists, and it is the slow part:
 * every frame is digested whole, then each channel is measured. Without
 * this the tool sits silent for minutes on a real folder with nothing on
 * screen to say it is working, or how far along it is.
 *
 * Modeless and always on top, like the pipeline's own run window. The bar
 * is drawn rather than composed from a control because PJSR has no
 * progress bar of its own.
 */
/*
 * The reject mark, drawn rather than taken from PixInsight's resources.
 *
 * Drawing it is what makes it certain: a named resource has to exist in
 * whichever build is running, and the one this needs -- a small red X --
 * cannot be confirmed without looking at it. Built once and reused for
 * every row.
 */
FrameSelector.REJECT_COLOUR = 0xffcc2222;
FrameSelector.MARK_SIZE = 12;

FrameSelector.rejectIcon = function()
{
   if ( FrameSelector._rejectIcon != null )
      return FrameSelector._rejectIcon;
   try
   {
      var s = FrameSelector.MARK_SIZE, pad = 3;
      var b = new Bitmap( s, s );
      b.fill( 0x00000000 );                     // transparent, not white
      var g = new Graphics( b );
      try
      {
         g.antialiasing = true;
         g.pen = new Pen( FrameSelector.REJECT_COLOUR, 2 );
         g.drawLine( pad, pad, s - pad - 1, s - pad - 1 );
         g.drawLine( s - pad - 1, pad, pad, s - pad - 1 );
      }
      finally { g.end(); }
      FrameSelector._rejectIcon = b;
   }
   catch ( e )
   {
      // A mark that cannot be drawn must not cost the review its rows.
      FrameSelector._rejectIcon = null;
   }
   return FrameSelector._rejectIcon;
};

/*
 * An empty mark of the same size, for the frames that are kept.
 *
 * Every row carries one. Setting an icon only on the rejected rows
 * indents those rows alone, so the names no longer share a left edge and
 * the column reads as ragged -- the marked rows stand out by being moved
 * rather than by being marked.
 */
FrameSelector.blankIcon = function()
{
   if ( FrameSelector._blankIcon != null )
      return FrameSelector._blankIcon;
   try
   {
      var b = new Bitmap( FrameSelector.MARK_SIZE, FrameSelector.MARK_SIZE );
      b.fill( 0x00000000 );
      FrameSelector._blankIcon = b;
   }
   catch ( e ) { FrameSelector._blankIcon = null; }
   return FrameSelector._blankIcon;
};

/* The mark a row carries: the cross when it is going, a spacer when not. */
FrameSelector.markIcon = function( rejected )
{
   return rejected ? FrameSelector.rejectIcon() : FrameSelector.blankIcon();
};

/*
 * The plot's palette. On paper rather than on the dialog's own dark
 * background: a measurement plot is read, and these are the contrasts a
 * chart is normally printed with.
 */
FrameSelector.PLOT_COLOURS = {
   PAPER:  0xffffffff,
   BAND:   0xffe6e6e6,   // the range that keeps a frame
   AXIS:   0xff9a9a9a,
   EDGE:   0xff6e6e6e,   // the band's own limits, drawn and labelled
   INK:    0xff404040,   // the axis numbers
   LINE:   0xff1f6fb4,
   REJECT: 0xffcc2222,
   /* The selected frame's ring, matching the table's highlight. */
   PICKED: 0xffe07000
};

/*
 * A measurement plotted across the channel's frames, the way
 * SubframeSelector shows one: frame order along the bottom, the value up
 * the side, and the range that keeps a frame drawn as a band behind it.
 *
 * Drawn, because PJSR has no plot control. The geometry lives in Frames
 * (acceptedBand, plotBounds) so what decides the picture can be tested
 * without a window; this only paints what those return.
 */
FrameSelector.Plot = class extends Frame
{
   constructor( parent )
   {
      super( parent );
      var self = this;
      this.rows = [];
      this.metric = Frames.METRICS[0];
      this.band = { lo: null, hi: null };
      this.selected = -1;
      this.setScaledMinHeight( 150 );
      this.geom = null;
      this.onPick = null;                  // set by the dialog
      this.onMousePress = function( x )
      {
         if ( self.geom == null || self.onPick == null )
            return;
         var i = Frames.pointAt( x, self.geom.left, self.geom.width,
                                 self.rows.length );
         if ( i >= 0 )
            self.onPick( i );
      };
      this.toolTip =
         "<p>Each frame's measurement, in table order. The grey band is the " +
         "range that keeps a frame; a red cross is one that will be deleted.</p>" +
         "<p><b>Click</b> a point to select that frame in the list.</p>";
      this.onPaint = function() { self.paint(); };
   }

   /*
    * NOT called show(). Frame inherits Control.show(), which takes no
    * arguments, and overriding it made the dialog fail to build at all
    * with "Control.show(): Wrong number of arguments" -- the framework
    * calls show() itself when the pane is laid out.
    */
   setSeries( rows, metric, band )
   {
      this.rows = rows || [];
      this.metric = metric;
      this.band = band || { lo: null, hi: null };
      this.repaint();
   }

   /* Detached before teardown, for the reason PreviewControl.release states. */
   release()
   {
      try
      {
         this.rows = [];
         this.onPick = null;
         this.onPaint = null;
         this.onMousePress = null;
      }
      catch ( e ) {}
   }

   /*
    * Ring the frame the table has selected, so the row and the point are
    * the same frame without counting along the line to find it.
    */
   setSelected( index )
   {
      var i = ( index == null ) ? -1 : index;
      if ( i === this.selected )
         return;                            // no repaint for no change
      this.selected = i;
      this.repaint();
   }

   paint()
   {
      var g = null;
      try
      {
         g = new Graphics( this );
         this.paintOn( g, this.width, this.height );
      }
      catch ( e )
      {
         // A plot that cannot draw must not cost the review its table.
      }
      finally
      {
         if ( g != null ) try { g.end(); } catch ( e2 ) {}
      }
   }

   /*
    * Everything the plot draws, onto any Graphics of the given size -- the
    * control's own, or a bitmap's in the suite, which is how what it draws
    * is checked rather than only that it runs.
    */
   paintOn( g, W, H )
   {
      g.antialiasing = true;
      g.fillRect( 0, 0, W, H, new Brush( FrameSelector.PLOT_COLOURS.PAPER ) );
      var f = this.layout( W, H );
      /*
       * Kept for the hit test: a click has to map back through exactly the
       * geometry the points were drawn with, not a second copy of the
       * arithmetic that could drift from it.
       */
      this.geom = { left: f.L, width: f.pw };
      this.drawBand( g, f );
      this.drawAxisAndLabels( g, f );
      this.drawLine( g, f );
      this.drawMarkers( g, f );
      this.drawRing( g, f );
   }

   /* Where everything goes: margins, the values, and the two mappings. */
   layout( W, H )
   {
      var L = 52, R = 8, T = 10, B = 18;      // room for the value labels
      var pw = Math.max( 1, W - L - R ), ph = Math.max( 1, H - T - B );
      var values = [];
      for ( var i = 0; i < this.rows.length; ++i )
         values.push( this.rows[i].metrics ? this.rows[i].metrics[this.metric] : null );
      var bounds = Frames.plotBounds( values, this.band );
      var span = ( bounds.hi - bounds.lo ) || 1;
      return {
         L: L, T: T, pw: pw, ph: ph, values: values, bounds: bounds,
         yOf: function( v ) { return T + ph - ( ( v - bounds.lo ) / span ) * ph; },
         xOf: function( k )
         {
            return ( values.length < 2 ) ? ( L + pw/2 )
                                         : ( L + ( k / ( values.length - 1 ) ) * pw );
         },
         has: function( k ) { return values[k] != null && isFinite( values[k] ); }
      };
   }

   /*
    * The accepted band. An open end runs to the edge of the plot, which is
    * what "no limit on this side" looks like.
    */
   drawBand( g, f )
   {
      var top = ( this.band.hi != null ) ? f.yOf( this.band.hi ) : f.T;
      var bot = ( this.band.lo != null ) ? f.yOf( this.band.lo ) : f.T + f.ph;
      if ( bot > top )
         g.fillRect( f.L, Math.max( f.T, top ), f.L + f.pw, Math.min( f.T + f.ph, bot ),
                     new Brush( FrameSelector.PLOT_COLOURS.BAND ) );
   }

   /*
    * The frame, the band's edges drawn and labelled -- a band with no
    * number on it does not say what the threshold actually is -- and the
    * axis extremes. An edge is listed first, so a threshold keeps its
    * number when the axis extreme would land on top of it.
    */
   drawAxisAndLabels( g, f )
   {
      g.pen = new Pen( FrameSelector.PLOT_COLOURS.AXIS );
      g.drawRect( f.L, f.T, f.L + f.pw, f.T + f.ph );
      var wanted = [], edges = [ this.band.lo, this.band.hi ];
      for ( var e = 0; e < edges.length; ++e )
      {
         var ye = ( edges[e] != null ) ? f.yOf( edges[e] ) : null;
         if ( ye == null || ye < f.T || ye > f.T + f.ph )
            continue;
         g.pen = new Pen( FrameSelector.PLOT_COLOURS.EDGE );
         g.drawLine( f.L, ye, f.L + f.pw, ye );
         wanted.push( { y: ye + 4, text: Frames.round( edges[e] ), edge: true } );
      }
      wanted.push( { y: f.T + 8,    text: Frames.round( f.bounds.hi ) } );
      wanted.push( { y: f.T + f.ph, text: Frames.round( f.bounds.lo ) } );
      var labels = Frames.spacedLabels( wanted, FrameSelector.LABEL_GAP );
      for ( var li = 0; li < labels.length; ++li )
      {
         g.pen = new Pen( labels[li].edge ? FrameSelector.PLOT_COLOURS.EDGE
                                          : FrameSelector.PLOT_COLOURS.INK );
         g.drawText( 2, labels[li].y, labels[li].text );
      }
   }

   /*
    * The line, before the markers. A gap in the data breaks it rather than
    * being bridged: an unmeasurable frame has no value, and joining across
    * it would draw a trend through a frame that was never measured.
    */
   drawLine( g, f )
   {
      g.pen = new Pen( FrameSelector.PLOT_COLOURS.LINE, 1 );
      var prevX = null, prevY = null;
      for ( var j = 0; j < f.values.length; ++j )
      {
         if ( !f.has( j ) )
         {
            prevX = null;
            continue;
         }
         var x = f.xOf( j ), y = f.yOf( f.values[j] );
         if ( prevX != null )
            g.drawLine( prevX, prevY, x, y );
         prevX = x; prevY = y;
      }
   }

   /*
    * A point per frame; a rejected one gets the same cross as the table's,
    * for the same reason: the frames about to be deleted are the ones
    * worth finding.
    */
   drawMarkers( g, f )
   {
      for ( var k = 0; k < f.values.length; ++k )
      {
         if ( !f.has( k ) )
            continue;
         var x = f.xOf( k ), y = f.yOf( f.values[k] ), row = this.rows[k];
         if ( Frames.finalState( row.state, row.override ) == Frames.STATE.REJECTED )
         {
            g.pen = new Pen( FrameSelector.PLOT_COLOURS.REJECT, 2 );
            g.drawLine( x-4, y-4, x+4, y+4 );
            g.drawLine( x+4, y-4, x-4, y+4 );
            continue;
         }
         g.pen = new Pen( FrameSelector.PLOT_COLOURS.LINE );
         g.brush = new Brush( FrameSelector.PLOT_COLOURS.LINE );
         g.fillRect( x-2, y-2, x+2, y+2 );
      }
   }

   /*
    * The selection last, so the ring is never drawn under a marker or the
    * line. An empty brush, or the circle would fill and hide the very
    * point it is pointing at.
    */
   drawRing( g, f )
   {
      var sel = this.selected;
      if ( sel < 0 || sel >= f.values.length || !f.has( sel ) )
         return;
      g.pen = new Pen( FrameSelector.PLOT_COLOURS.PICKED, 2 );
      g.brush = new Brush( FrameSelector.PLOT_COLOURS.PICKED, BrushStyle_Empty );
      g.drawCircle( f.xOf( sel ), f.yOf( f.values[sel] ), FrameSelector.PICK_RADIUS );
   }
};

/*
 * The channel's frames as a strip of thumbnails, SubframeStudio's
 * filmstrip. Painted by hand like the Plot, with no scroll handlers of any
 * kind. Tiles are drawn from whatever thumbnails have arrived -- a grey box
 * until then, with the cross and badges already on it: the verdict never
 * waits for a picture.
 */
FrameSelector.Filmstrip = class extends Frame
{
   constructor( parent )
   {
      super( parent );
      var self = this;
      this.rows = []; this.flags = []; this.crossed = [];
      this.thumbs = []; this.failed = [];
      this.selected = -1; this.first = 0;
      this.metric = "fwhm";                // the number printed on each tile
      this.onPick = null;                  // set by the dialog
      this.setScaledMinHeight( FrameSelector.THUMB.H + 12 );
      this.toolTip = "<p>Click a frame to review it. A red cross marks a " +
                     "frame Run leaves out; the letters are advisory: " +
                     "C cloud, F focus, T tracking, D dropped.</p>";
      this.onPaint = function() { self.paint(); };
      this.onMousePress = function( x, y )
      {
         var i = self.indexAt( x );
         if ( i >= 0 && self.onPick != null )
            self.onPick( i );
      };
   }

   tileW() { return FrameSelector.THUMB.W + 8; }
   visibleCount() { return Math.max( 1, Math.floor( this.width/this.tileW() ) ); }

   setMetric( metric )
   {
      this.metric = metric;
      this.update();
   }

   setRows( rows, flags, crossed, selected )
   {
      this.rows = rows || []; this.flags = flags || []; this.crossed = crossed || [];
      this.selected = selected;
      // Kept still when the selection is already on screen: see stripFirst.
      this.first = Frames.stripFirst( this.rows.length, selected, this.visibleCount(),
                                      this.first );
      this.update();
   }

   page( dir )
   {
      var v = this.visibleCount();
      this.first = Math.max( 0, Math.min( this.first + dir*v,
                                          Math.max( 0, this.rows.length - v ) ) );
      this.update();
   }

   indexAt( x )
   {
      return Frames.tileAt( x, this.tileW(), this.visibleCount(), this.first, this.rows.length );
   }

   paintTile( g, i, x, y )
   {
      var T = FrameSelector.THUMB;
      var r = new Rect( x, y, x + T.W, y + T.H );
      var bmp = this.thumbs[i];
      if ( bmp != null )
         g.drawBitmap( x + Math.round( ( T.W - bmp.width )/2 ),
                       y + Math.round( ( T.H - bmp.height )/2 ), bmp );
      else
      {
         g.fillRect( r, new Brush( 0xff3a3a3a ) );
         if ( this.failed[i] )             // could not be read: say so
         {
            g.pen = new Pen( 0xffe0e0e0 );
            g.drawTextRect( r, "!", TextAlign_Center );
         }
      }
      if ( this.crossed[i] )
      {
         g.pen = new Pen( FrameSelector.REJECT_COLOUR, 2 );
         g.drawLine( r.x0, r.y0, r.x1, r.y1 );
         g.drawLine( r.x1, r.y0, r.x0, r.y1 );
      }
      var fl = this.flags[i] || [];
      for ( var b = 0; b < fl.length; ++b )
      {
         var br = new Rect( x + 3 + b*18, y + 3, x + 19 + b*18, y + 19 );
         g.fillRect( br, new Brush( FrameSelector.TAG_COLOURS.flag.fill ) );
         g.pen = new Pen( FrameSelector.TAG_COLOURS.flag.ink );
         g.drawTextRect( br, Frames.FLAG_BADGE[fl[b]], TextAlign_Center );
      }
      var m = this.rows[i].metrics;
      g.pen = new Pen( 0xffe8e8e8 );
      g.drawTextRect( new Rect( x, y + T.H - 16, x + T.W, y + T.H ),
                      m ? Frames.displayValue( this.metric, m[this.metric] ) : "-",
                      TextAlign_Center );
      if ( i == this.selected )
      {
         g.pen = new Pen( 0xffe0b020, 3 );
         g.brush = new Brush( 0xff000000, BrushStyle_Empty );
         g.drawRect( r );
      }
   }

   paint()
   {
      var g = null;
      try
      {
         g = new Graphics( this );
         g.fillRect( 0, 0, this.width, this.height, new Brush( 0xff1a1a1a ) );
         var last = Math.min( this.rows.length, this.first + this.visibleCount() );
         for ( var i = this.first; i < last; ++i )
            this.paintTile( g, i, ( i - this.first )*this.tileW() + 4, 6 );
      }
      catch ( e ) { /* a strip that cannot paint must not stop the review */ }
      finally { if ( g != null ) try { g.end(); } catch ( e2 ) {} }
   }

   /* Detached before teardown, for the reason PreviewControl.release states. */
   release()
   {
      try
      {
         this.onPaint = null;
         this.onMousePress = null;
         this.onPick = null;
         this.thumbs = []; this.rows = [];
      }
      catch ( e ) {}
   }
};

/*
 * How much of a frame's name the scan window can show. Measured against
 * its 460px line at the default UI font rather than guessed generously:
 * too many characters is a clipped line, which is the bug this replaced.
 */
FrameSelector.SCAN_NAME_CHARS = 58;

FrameSelector.ScanWindow = class extends Dialog
{
   constructor()
   {
      super();
      var self = this;
      this.cancelled = false;
      this.fraction = 0;

      this.windowTitle = "Loom Frame Selector - scanning";

      this.stageLabel = new Label( this );
      this.stageLabel.useRichText = true;
      this.stageLabel.text = "starting...";
      /*
       * Two lines, always, and never wrapped.
       *
       * The window is fixed-size, so whatever does not fit is clipped. A
       * subframe name runs to about eighty characters, which wrapped onto a
       * third line and took the count with it -- the one part of the line
       * that actually has to be readable. The count now has a line of its
       * own and the name is elided to fit on the other.
       */
      this.stageLabel.wordWrapping = false;
      this.stageLabel.setScaledMinWidth( 460 );
      this.stageLabel.setScaledMinHeight( 36 );

      this.bar = new Frame( this );
      this.bar.setScaledFixedHeight( 16 );
      this.bar.setScaledMinWidth( 460 );
      this.bar.onPaint = function()
      {
         try
         {
            var g = new Graphics( this );
            try
            {
               var w = this.width, h = this.height;
               g.fillRect( 0, 0, w, h, new Brush( 0xff202020 ) );
               var filled = Math.round( w * Math.max( 0, Math.min( 1, self.fraction ) ) );
               if ( filled > 0 )
                  g.fillRect( 0, 0, filled, h, new Brush( 0xff3c8cd8 ) );
               g.pen = new Pen( 0xff606060 );
               g.drawRect( 0, 0, w - 1, h - 1 );
            }
            finally { g.end(); }
         }
         catch ( e ) { /* a bar that cannot paint must not stop the scan */ }
      };

      this.cancelButton = new PushButton( this );
      this.cancelButton.text = "Cancel";
      this.cancelButton.defaultButton = false;
      this.cancelButton.toolTip =
         "<p>Stop reading this folder. Nothing has been changed on disk: " +
         "the scan only measures.</p>";
      this.cancelButton.onClick = function()
      {
         /*
          * No confirmation, unlike the pipeline's run window. A scan has
          * written nothing and thrown nothing away, so there is no cost to
          * weigh -- asking twice would just be in the way.
          */
         self.cancelled = true;
         self.stageLabel.text = "stopping...";
         self.cancelButton.enabled = false;
      };

      var row = new HorizontalSizer;
      row.addStretch();
      row.add( this.cancelButton );

      this.sizer = new VerticalSizer;
      this.sizer.margin = 8;
      this.sizer.spacing = 6;
      this.sizer.add( this.stageLabel );
      this.sizer.add( this.bar );
      this.sizer.addSpacing( 4 );
      this.sizer.add( row );

      this.adjustToContents();
      this.setFixedSize();
   }

   /*
    * Returns false once Cancel has been pressed, which is what the scan
    * reads to know it should stop.
    */
   report( phase, done, total, label )
   {
      try
      {
         this.fraction = ( total > 0 ) ? ( done / total ) : 0;
         /*
          * Count first and on its own line: it is what the window is for.
          * The name goes second, where clipping costs least, elided from
          * the front so the timestamp and sequence number survive.
          */
         this.stageLabel.text =
            "<b>" + phase + " (" + done + " of " + total + ")</b><br>" +
            Util.elideHead( label, FrameSelector.SCAN_NAME_CHARS );
         this.bar.repaint();
         CoreApplication.processEvents();
      }
      catch ( e ) {}
      return !this.cancelled;
   }

   /* Detached before teardown, for the reason PreviewControl.release states. */
   release()
   {
      try
      {
         this.bar.onPaint = null;
         this.cancelButton.onClick = null;
      }
      catch ( e ) {}
   }

   /* The pair of callbacks FrameSelector.scan expects. */
   callbacks()
   {
      var self = this;
      return {
         reading: function( done, total, name )
         {
            return self.report( "Reading", done, total, name );
         },
         measuring: function( done, total, filter )
         {
            return self.report( "Measuring", done, total, filter );
         }
      };
   }
};

FrameSelector.main = function()
{
   var gd = new GetDirectoryDialog;
   gd.caption = "Select a folder of subframes";
   if ( !gd.execute() )
      return;

   /*
    * directoryPath, not directory: the older name is deprecated in 1.9.5
    * and warns on every run. Loom requires 1.9.5, so the new name is
    * always there.
    */

   var progress = new FrameSelector.ScanWindow;
   var state;
   try
   {
      progress.show();
      CoreApplication.processEvents();
      state = FrameSelector.buildState( gd.directoryPath, progress.callbacks() );
   }
   finally
   {
      // Always, and before anything modal: a progress window left on top
      // of a message box is one the user cannot get past.
      try { progress.hide(); } catch ( e ) {}
      /*
       * And its handlers, rather than waiting for the collector to destroy
       * a window that still has JS attached to it.
       */
      try { progress.release(); } catch ( e ) {}
   }

   if ( state.cancelled )
      return;

   if ( state.order.length == 0 )
   {
      ( new MessageBox( "No readable frames in that folder.",
                        "Loom Frame Selector", StdIcon_Information,
                        StdButton_Ok ) ).execute();
      return;
   }
   /*
    * Released on every way out, including an exception during the review:
    * the loader's timer must never outlive the dialog.
    */
   var dialog = new FrameSelector.Dialog( state );
   try { dialog.execute(); }
   finally { dialog.release(); }
};

/*
 * The measurement column indices were read on 1.9.5. An older core is not
 * merely untested here -- it returns a different table, so every metric
 * would be read from the wrong place and the tool would delete files on
 * numbers that mean something else.
 */
function checkCoreVersion()
{
   var core = { major:   CoreApplication.versionMajor,
                minor:   CoreApplication.versionMinor,
                release: CoreApplication.versionRelease };
   if ( Util.coreVersionAtLeast( core, Util.MIN_CORE ) )
      return true;

   var message =
      "The Loom Frame Selector needs PixInsight " +
      Util.formatCoreVersion( Util.MIN_CORE ) + " or later.\n\n" +
      "This is PixInsight " + Util.formatCoreVersion( core ) +
      " (build " + CoreApplication.versionBuild + ").\n\n" +
      "SubframeSelector's measurement columns were read off " +
      Util.formatCoreVersion( Util.MIN_CORE ) + ". An earlier version " +
      "returns a different table, so the figures this tool deletes frames " +
      "on would be read from the wrong columns. Please update PixInsight " +
      "and run it again.";

   console.criticalln( message );
   new MessageBox( message, "Loom Frame Selector: PixInsight is too old",
                   StdIcon_Error, StdButton_Ok ).execute();
   return false;
}

function main()
{
   console.show();

   if ( !checkCoreVersion() )
      return;

   FrameSelector.main();
}

/*
 * selftest.js includes this file to reach the functions above, and must not
 * open a dialog while doing it: a modal window in a dispatched test run
 * holds the script queue with nobody there to dismiss it. Loom.js can end
 * with a bare main() because nothing includes Loom.js.
 */
#ifndef LOOM_FRAME_SELECTOR_UNDER_TEST
main();
#endif
