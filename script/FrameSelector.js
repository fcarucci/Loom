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

   for ( var r = 0; r < P.measurements.length; ++r )
   {
      var m = Frames.metricsFromRow( P.measurements[r] );
      if ( m.path == null || m.path === "" || !asked[m.path] )
      {
         Util.error( "frames", "SubframeSelector returned an unexpected path (" +
                               m.path + "); abandoning this channel" );
         return null;                    // null = the channel failed
      }
      if ( out[m.path] != null )
      {
         Util.error( "frames", "two measurements for " + m.path +
                               "; abandoning this channel" );
         return null;
      }
      out[m.path] = m;
   }
   return out;
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
   return t[FrameSelector.measurementKey( identity )] || null;
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

/*
 * Stored WITHOUT the path. The key is the digest, so identical bytes sitting
 * somewhere else must not come back naming the first file.
 */
FrameSelector.storedMetrics = function( m )
{
   return { fwhm: m.fwhm, eccentricity: m.eccentricity, noise: m.noise,
            stars: m.stars, psfSNR: m.psfSNR };
};

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

/* Radius of the ring round the selected frame: clear of a 4px marker. */
FrameSelector.PICK_RADIUS = 6;

/*
 * A 1:1 pannable preview.
 *
 * A ScrollBox, not a bare Control, and that is the whole reason it scrolls
 * properly. PixInsight's wheel event carries ONE delta and no orientation
 * -- the C++ API is MouseWheel( ..., int32 delta, ... ) -- so a control
 * handling the wheel itself can never see a sideways swipe. A ScrollBox
 * does not handle the wheel itself: Qt scrolls it, in whichever direction
 * the gesture went. So no onMouseWheel handler is installed here, on
 * purpose; installing one is what broke horizontal scrolling before.
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
      this.fit = false;
      this.dragging = false;
      this.lx = 0;
      this.ly = 0;

      this.tracking = true;
      this.setScaledMinSize( 420, 360 );

      this.viewport.focusStyle = FocusStyle.Click;   // arrows need focus
      this.viewport.toolTip =
         "<p><b>Double click</b> to switch between the whole frame and 1:1.</p>" +
         "<p>At 1:1: <b>swipe</b> in any direction, <b>drag</b>, or the " +
         "<b>arrow keys</b> after clicking the image.</p>";

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
      this.onViewportScrolled = function() { self.viewport.update(); };

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
      this.viewport.onMouseDoubleClick = function()
      {
         self.setFit( !self.fit );
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
         if ( this.bmp == null )
            return;
         if ( this.fit )
         {
            var s = Math.min( vw/this.bmp.width, vh/this.bmp.height );
            g.drawScaledBitmap(
               new Rect( 0, 0, Math.round( this.bmp.width*s ),
                               Math.round( this.bmp.height*s ) ), this.bmp );
            return;
         }
         /*
          * Offset by the scroll position, the way PixInsight's own
          * ImageView does it, so what Qt scrolled is what gets drawn.
          */
         g.translateTransformation( -this.horizontalScrollPosition,
                                    -this.verticalScrollPosition );
         g.drawBitmap( 0, 0, this.bmp );
      }
      catch ( e ) { /* a preview that cannot paint must not stop the review */ }
      finally { if ( g != null ) try { g.end(); } catch ( e2 ) {} }
   }

   dispose()
   {
      this.bmp = null;                     // the only reference; let it go
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

      var win = null;
      try
      {
         var ws = ImageWindow.open( path );
         if ( ws.length == 0 )
            return false;
         win = ws[0];
         var img = win.mainView.image;
         var med = img.median(), mad = img.MAD()*1.4826;
         var shadows = Math.max( 0, med - 2.8*mad );
         var midtone = Math.mtf( 0.25, Math.max( 1e-8, med - shadows ) );

         /*
          * Stretched before rendering: Image.render() does NOT apply an
          * STF, so a linear sub renders as a black rectangle.
          */
         var dup = new ImageWindow( img.width, img.height, img.numberOfChannels,
                                    img.bitsPerSample, img.isReal, img.isColor,
                                    Util.freeWindowId( "fs_preview" ) );
         dup.mainView.beginProcess( UndoFlag_NoSwapFile );
         dup.mainView.image.assign( img );
         dup.mainView.endProcess();

         var H = new HistogramTransformation;
         H.H = [ [ 0, 0.5, 1, 0, 1 ], [ 0, 0.5, 1, 0, 1 ], [ 0, 0.5, 1, 0, 1 ],
                 [ shadows, midtone, 1, 0, 1 ], [ 0, 0.5, 1, 0, 1 ] ];
         H.executeOn( dup.mainView );

         self.bmp = dup.mainView.image.render();
         dup.forceClose();
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
         try { if ( win != null && !win.isNull ) win.forceClose(); } catch ( e2 ) {}
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

/*
 * A channel holds its COHORT -- the entries and the measurements taken at
 * scan time -- separately from the rows, which are the review. Medians and
 * MADs are computed from the cohort every time, never from whatever
 * survived a partial run, because recomputing on survivors is iterative
 * clipping.
 */
FrameSelector.newChannel = function( key, entries, metrics, problems )
{
   var rows = [];
   for ( var i = 0; i < entries.length; ++i )
   {
      var e = entries[i];
      rows.push( { path: e.path, channel: key,
                   metrics: metrics[e.path] || null,
                   digest: e.identity ? e.identity.digest : null,
                   size: e.identity ? e.identity.size : 0,
                   mtime: e.identity ? e.identity.mtime : 0,
                   state: Frames.STATE.UNMEASURABLE, reasons: [],
                   override: null, score: null } );
   }
   return { key: key, entries: entries, metrics: metrics,
            problems: problems || [],
            settings: Frames.defaultSettings(), rows: rows };
};

/*
 * Recompute every verdict in a channel from its cohort.
 *
 * Overrides are left standing: changing k or a weight must not discard the
 * one judgement in the dialog that was made by looking at the frame.
 */
FrameSelector.recompute = function( ch )
{
   var cohort = [];
   for ( var i = 0; i < ch.rows.length; ++i )
      if ( ch.rows[i].metrics != null )
         cohort.push( ch.rows[i].metrics );

   var gates = Frames.relativeGates( cohort, ch.settings.k, ch.settings.gating );
   /*
    * Kept on the channel: the plot draws the band these define, and
    * recomputing them there would be a second place for k to be read.
    */
   ch.gates = gates;
   var meds = Frames.medians( cohort );
   /*
    * A disabled channel is untouched in every mode, and a group with no
    * readable FILTER is never auto-rejected -- there is no evidence its
    * frames belong together, so "this night's worst" means nothing over
    * them. Both still get scores, so the table is readable.
    */
   var mayReject = ch.settings.enabled && Frames.autoRejectAllowed( ch.key );

   for ( var r = 0; r < ch.rows.length; ++r )
   {
      var row = ch.rows[r];
      row.score = ( row.metrics != null )
                  ? Frames.score( row.metrics, meds, ch.settings.weights ) : null;
      if ( row.metrics == null )
      {
         row.state = Frames.STATE.UNMEASURABLE;
         row.reasons = [ "no measurement" ];
         row.failing = [];
         continue;
      }
      var v = Frames.verdict( row.metrics, gates, ch.settings );
      var suppressed = !mayReject && v.state == Frames.STATE.REJECTED;
      row.state   = suppressed ? Frames.STATE.APPROVED : v.state;
      row.reasons = suppressed ? [] : v.reasons;
      /*
       * Cleared with the reasons when a rejection is suppressed: the
       * channel is not being acted on, so there is nothing to mark.
       */
      row.failing = suppressed ? [] : v.failing;
   }
   return ch;
};

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

/*
 * The mode combo's items, in the order it shows them. A constant rather
 * than a constructor local because two methods need it -- the one that
 * fills the combo and the one that reads the selection back.
 */
FrameSelector.MODE_NAMES = [ Frames.MODE.RELATIVE, Frames.MODE.ABSOLUTE,
                             Frames.MODE.BOTH ];

FrameSelector.Dialog = class extends Dialog
{
   constructor( state )
   {
   super();
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
   this.buildActionRow();
   this.wireBehaviour();
   this.layOut();
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
      for ( var m = 0; m < Frames.METRICS.length; ++m )
         this.plotCombo.addItem( Frames.METRIC_LABEL[Frames.METRICS[m]] );
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
   selectRow( index )
   {
      if ( this.frameTree == null ||
           index < 0 || index >= this.frameTree.numberOfChildren )
         return;
      var node = this.frameTree.child( index );
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
      if ( node.rowRef )
         this.preview.load( node.rowRef.path );
      this.plot.setSelected( index );
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
      return Frames.METRICS[( i >= 0 && i < Frames.METRICS.length ) ? i : 0];
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
         var n = self.frameTree.selectedNodes;
         if ( n.length && n[0].rowRef )
         {
            self.preview.load( n[0].rowRef.path );
            self.plot.setSelected( n[0].rowIndex );
         }
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

   /* Preset, mode, k and the per-channel enable. */
   buildKnobPanel()
   {
      var self = this;
      this.presetCombo = new ComboBox( this );
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

      this.modeCombo = new ComboBox( this );
      for ( var mo = 0; mo < FrameSelector.MODE_NAMES.length; ++mo )
         this.modeCombo.addItem( FrameSelector.MODE_NAMES[mo] );
      this.modeCombo.onItemSelected = function( i )
      {
         var ch = self.channel();
         if ( ch == null || !self.editable() )
            return;
         ch.settings.mode = FrameSelector.MODE_NAMES[i];
         self.refresh();
      };

      this.kEdit = new NumericEdit( this );
      this.kEdit.label.text = "k";
      this.kEdit.setRange( 0.5, 10 );
      this.kEdit.setPrecision( 2 );
      this.kEdit.setValue( Frames.PRESETS[this.state.preset] );
      this.kEdit.onValueUpdated = function( v )
      {
         var ch = self.channel();
         if ( ch == null || !self.editable() )
            return;
         ch.settings.k = v;
         ch.settings.kEdited = true;        // an edited k survives a preset change
         self.refresh();
      };

      this.enabledCheck = new CheckBox( this );
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

      /*
       * Which metrics may reject, per channel. Separate from the weights:
       * a metric can rank frames without being allowed to delete one, and
       * PSF SNR is exactly that case -- integration already weights by
       * signal, so a faint frame is discounted rather than discarded.
       */
      this.gateChecks = {};
      for ( var gi = 0; gi < Frames.METRICS.length; ++gi )
         ( function( metric )
         {
            var cb = new CheckBox( self );
            cb.text = Frames.METRIC_HEADING[metric];
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
            self.gateChecks[metric] = cb;
         } )( Frames.METRICS[gi] );

      this.gateLabel = new Label( this );
      this.gateLabel.text = "reject on:";

      this.problemsLabel = new Label( this );
      this.problemsLabel.wordWrapping = true;
      this.problemsLabel.useRichText = false;

      this.summaryLabel = new Label( this );
      this.summaryLabel.wordWrapping = true;
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
                                ( ch.settings.enabled ? "" : "  [off]" ) +
                                ( ch.problems.length ? "  [mixed]" : "" ) );
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
            {
               var row = ch.rows[i];
               var node = new TreeBoxNode( self.frameTree );
               node.rowRef = row;
               node.rowIndex = i;      // which point on the plot this row is
               node.setText( 0, shortNames[i] );
               if ( row.metrics != null )
               {
                  for ( var mc = 0; mc < Frames.METRICS.length; ++mc )
                  {
                     var mn = Frames.METRICS[mc], mv = row.metrics[mn];
                     node.setText( Frames.metricColumn( mn ),
                        ( mn == "stars" ) ? String( mv ) : Frames.round( mv ) );
                  }
               }
               node.setText( Frames.SCORE_COLUMN,
                             ( row.score == null ) ? "-" : Frames.round( row.score ) );
               var final = Frames.finalState( row.state, row.override );
               var mark = ( row.override == Frames.OVERRIDE.RESCUED ) ? " (rescued)"
                        : ( row.override == Frames.OVERRIDE.CONDEMNED ) ? " (condemned)" : "";
               /*
                * The verdict in words goes on the row rather than into a
                * column of its own -- alongside the name, so hovering a
                * frame answers both "which file" and "why".
                */
               node.setToolTip( 0, row.path + "\n" + final + mark +
                                ( row.reasons.length ? ": " + row.reasons.join( "; " ) : "" ) );

               /*
                * A discarded frame is marked twice over: an X beside its
                * name, so the rows to lose are findable at a glance, and
                * the measurement that condemned it in red, so the reason
                * is visible without reading the verdict column. A frame
                * condemned by hand has no failing measurement, and
                * correctly gets the mark with nothing coloured.
                */
               var rejected = ( final == Frames.STATE.REJECTED );
               var ico = FrameSelector.markIcon( rejected );
               if ( ico != null )
                  node.setIcon( 0, ico );

               if ( rejected )
               {
                  var failing = row.failing || [];
                  for ( var f = 0; f < failing.length; ++f )
                  {
                     var col = Frames.metricColumn( failing[f] );
                     if ( col != null )
                        node.setTextColor( col, FrameSelector.REJECT_COLOUR );
                  }
               }
            }
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
            self.modeCombo.currentItem =
               FrameSelector.MODE_NAMES.indexOf( ch.settings.mode );
            var names = [ "lenient", "balanced", "strict" ];
            var pi = names.indexOf( ch.settings.preset );
            if ( pi >= 0 )
               self.presetCombo.currentItem = pi;
            for ( var gm = 0; gm < Frames.METRICS.length; ++gm )
            {
               var mk = Frames.METRICS[gm];
               if ( self.gateChecks[mk] != null )
                  self.gateChecks[mk].checked = !!ch.settings.gating[mk];
            }
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
      var self = this;
            var total = 0, condemned = 0, mixed = [];
            for ( var i = 0; i < self.state.order.length; ++i )
            {
               var key = self.state.order[i], ch = self.state.channels[key];
               FrameSelector.recompute( ch );
               var c = Frames.counts( ch.rows );
               total += c.total;
               if ( ch.settings.enabled )
                  condemned += c.rejected;
               // Reported, not withheld.
               if ( ch.settings.enabled && ch.problems.length && c.rejected )
                  mixed.push( key );
            }
            self.fillChannels();
            self.fillFrames();
            self.fillPlot();
            self.syncKnobs();
            self.summaryLabel.text =
               total + " frame(s), " + condemned + " to remove" +
               ( self.state.unstable.length
                 ? "; " + self.state.unstable.length +
                   " changed while being measured and were excluded" : "" ) +
               ( mixed.length ? ( "; " + mixed.join( ", " ) +
                                  " not comparable -- check before running" ) : "" ) +
               ( self.copyingOut()
                 ? ( "; " + ( total - condemned ) + " approved to copy" ) : "" );
            /*
             * Run is enabled when there is something to do, and nothing
             * else. A channel that is not comparable is reported and left
             * to the user; it does not disable the button.
             */
            var copying = self.copyingOut();
            var approved = total - condemned;
            self.applyButton.enabled = self.editable() &&
               ( copying ? ( approved > 0 && self.state.destination != null )
                         : condemned > 0 );

            self.destButton.enabled = self.editable();
            /*
             * Say which of the two things Apply will do. The destructive
             * one must never be reached by a label that implies the other.
             */
            if ( self.destinationIsSource() )
            {
               var toConvert = Frames.needingXisf( self.approvedPaths() ).length;
               self.destLabel.text =
                  "<i>" + Util.elideHead( self.state.destination, 40 ) + "</i> " +
                  "&mdash; <b>the folder the frames are in.</b> Nothing is " +
                  "copied; the " + condemned + " rejected frame(s) are " +
                  "<b>deleted</b> where they are" +
                  ( toConvert ? ( ", and " + toConvert +
                                  " kept frame(s) converted to XISF in place" ) : "" ) +
                  ".";
            }
            else if ( self.state.destination == null )
               self.destLabel.text = "<i>no folder chosen</i>";
            else
               self.destLabel.text =
                  "<i>" + Util.elideHead( self.state.destination, 40 ) +
                  "</i> &mdash; the " + approved + " kept frame(s) are copied " +
                  "there; nothing is deleted.";
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
      try
      {
         if ( this.preview != null ) this.preview.release();
         if ( this.plot != null ) this.plot.release();
         if ( this.frameTree != null ) this.frameTree.onNodeSelectionUpdated = null;
         if ( this.channelTree != null ) this.channelTree.onNodeSelectionUpdated = null;
      }
      catch ( e ) {}
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

      var result = FrameSelector.exportApproved( approved, this.state.destination,
                                                 false/*overwrite*/ );
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
   layOut()
   {
      var self = this;
      var knobs = new HorizontalSizer;
      knobs.spacing = 6;
      var presetLabel = new Label( this );
      presetLabel.text = "preset:";
      knobs.add( presetLabel );
      knobs.add( this.presetCombo );
      knobs.add( this.modeCombo );
      knobs.add( this.kEdit );
      knobs.add( this.enabledCheck );
      knobs.addSpacing( 10 );
      knobs.add( this.gateLabel );
      for ( var gk = 0; gk < Frames.METRICS.length; ++gk )
         knobs.add( this.gateChecks[Frames.METRICS[gk]] );
      knobs.addStretch();

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
      this.sizer.add( knobs );
      this.sizer.add( this.problemsLabel );
      this.sizer.add( this.summaryLabel );
      this.sizer.add( dest );
      this.sizer.add( buttons );

      this.refresh();
      this.adjustToContents();
   }
};


/*
 * With overwriting off, a destination that already exists is a FAILURE for
 * that entry rather than a success -- otherwise a stale file from an earlier
 * run counts as this run's work.
 */
FrameSelector.exportSplit = function( approved, map, overwrite )
{
   var toWrite = [], blocked = [];
   for ( var i = 0; i < approved.length; ++i )
   {
      var src = approved[i];
      if ( !overwrite && File.exists( map.mapping[src] ) )
         blocked.push( src );
      else
         toWrite.push( src );
   }
   return { toWrite: toWrite, blocked: blocked };
};

/*
 * Measuring first is not optional: routines 1 and 2 refuse with "No
 * measurements have been made". False means that measurement pass failed,
 * so nothing was written.
 */
FrameSelector.runOutputRoutine = function( sources, destination, overwrite, postfix )
{
   var P = FrameSelector.newMeasureProcess();
   var rows = [];
   for ( var t = 0; t < sources.length; ++t )
      rows.push( [ true, sources[t], "", "" ] );
   P.subframes = rows;
   if ( !P.executeGlobal() )
      return false;

   P.routine = 2;                         // output
   P.outputDirectory = destination;
   P.overwriteExistingFiles = !!overwrite;
   /*
    * The postfix is stated rather than left to the process. Its default is
    * "_a", which is right when writing beside the originals and wrong when
    * converting them: a.fit would become a_a.xisf, and the conversion
    * would rename as well as convert.
    */
   if ( postfix != null )
      P.outputPostfix = postfix;
   P.executeGlobal();
   return true;
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
      var dir = grouped.order[g], group = grouped.dirs[dir];

      /*
       * a.fit and a.fits in one folder both become a.xisf. Converting
       * either would destroy the other's output, so neither is attempted.
       */
      var map = Frames.outputMapping( group, dir, Frames.XISF );
      if ( map.collisions.length > 0 )
      {
         result.refused = "two or more frames would convert to the same name: " +
                          map.collisions.join( ", " );
         Util.error( "frames", result.refused );
         return result;
      }

      try
      {
         if ( !FrameSelector.runOutputRoutine( group, dir, true/*overwrite*/,
                                               ""/*postfix*/ ) )
         {
            result.refused = "the measurement pass failed";
            return result;
         }
      }
      catch ( e )
      {
         result.refused = String( e );
         Util.error( "frames", "conversion failed: " + e );
         return result;
      }

      for ( var i = 0; i < group.length; ++i )
      {
         var src = group[i], out = map.mapping[src];
         if ( !File.exists( out ) )
         {
            result.outcomes[src] = "not converted";
            ++result.failed;
            Util.warn( "frames", "not converted, left alone: " + src );
            continue;
         }
         try
         {
            File.remove( src );
            result.outcomes[src] = "converted";
            ++result.converted;
         }
         catch ( e2 )
         {
            // The XISF is there; only the original is left behind.
            result.outcomes[src] = "converted, original kept";
            ++result.failed;
            Util.warn( "frames", "converted but could not remove " + src + ": " + e2 );
         }
      }
   }
   return result;
};

/*
 * Write the approved frames somewhere else, using SubframeSelector's output
 * routine. No original is deleted, but success still has to be well defined
 * or "it worked" is a guess.
 *
 * The mapping is computed first and the whole channel is refused if two
 * sources would produce one output name, or if the destination is a source
 * directory.
 */
FrameSelector.exportApproved = function( approved, destination, overwrite )
{
   var result = { written: 0, failed: 0, skipped: 0, refused: null, outcomes: {} };
   if ( approved == null || approved.length == 0 )
      return result;

   var map = Frames.outputMapping( approved, destination, ".xisf" );
   if ( map.collisions.length > 0 )
   {
      result.refused = "two or more frames would be written to the same name: " +
                       map.collisions.join( ", " );
      Util.error( "frames", result.refused );
      return result;
   }
   if ( map.aliased )
   {
      result.refused = "the destination is one of the source folders";
      Util.error( "frames", result.refused );
      return result;
   }

   var split = FrameSelector.exportSplit( approved, map, overwrite );
   for ( var b = 0; b < split.blocked.length; ++b )
   {
      var blocked = split.blocked[b];
      result.outcomes[blocked] = "exists";
      ++result.failed;                    // not a success: this run did not write it
      Util.warn( "frames", "not written, already there: " + map.mapping[blocked] );
   }
   if ( split.toWrite.length == 0 )
      return result;

   try
   {
      if ( !File.directoryExists( destination ) )
         File.createDirectory( destination, true );
      if ( !FrameSelector.runOutputRoutine( split.toWrite, destination, overwrite ) )
      {
         result.refused = "the measurement pass failed";
         return result;
      }
   }
   catch ( e )
   {
      result.refused = String( e );
      Util.error( "frames", "export failed: " + e );
      return result;
   }

   /*
    * Confirmed per file rather than assumed from the process returning
    * true, so a retry knows which outputs this run actually produced.
    */
   for ( var w = 0; w < split.toWrite.length; ++w )
   {
      var s = split.toWrite[w];
      if ( File.exists( map.mapping[s] ) )
         { result.outcomes[s] = "written"; ++result.written; }
      else
         { result.outcomes[s] = "missing"; ++result.failed; }
   }
   return result;
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
         g.antialiasing = true;
         var W = this.width, H = this.height;
         g.fillRect( 0, 0, W, H, new Brush( FrameSelector.PLOT_COLOURS.PAPER ) );

         var L = 52, R = 8, T = 10, B = 18;      // room for the value labels
         var pw = Math.max( 1, W - L - R ), ph = Math.max( 1, H - T - B );
         /*
          * Kept for the hit test: a click has to map back through exactly
          * the geometry the points were drawn with, not a second copy of
          * the arithmetic that could drift from it.
          */
         this.geom = { left: L, width: pw };

         var values = [];
         for ( var i = 0; i < this.rows.length; ++i )
            values.push( this.rows[i].metrics ? this.rows[i].metrics[this.metric] : null );
         var bounds = Frames.plotBounds( values, this.band );
         var span = ( bounds.hi - bounds.lo ) || 1;

         function yOf( v ) { return T + ph - ( ( v - bounds.lo ) / span ) * ph; }
         function xOf( i )
         {
            return ( values.length < 2 ) ? ( L + pw/2 )
                                         : ( L + ( i / ( values.length - 1 ) ) * pw );
         }

         // The accepted band. An open end runs to the edge of the plot,
         // which is what "no limit on this side" looks like.
         var bTop = ( this.band.hi != null ) ? yOf( this.band.hi ) : T;
         var bBot = ( this.band.lo != null ) ? yOf( this.band.lo ) : T + ph;
         if ( bBot > bTop )
            g.fillRect( L, Math.max( T, bTop ), L + pw, Math.min( T + ph, bBot ),
                        new Brush( FrameSelector.PLOT_COLOURS.BAND ) );

         g.pen = new Pen( FrameSelector.PLOT_COLOURS.AXIS );
         g.drawRect( L, T, L + pw, T + ph );

         // The band's edges, labelled -- a band with no number on it does
         // not say what the threshold actually is.
         var edges = [ this.band.lo, this.band.hi ];
         var wanted = [];
         for ( var e = 0; e < edges.length; ++e )
            if ( edges[e] != null )
            {
               var ye = yOf( edges[e] );
               if ( ye >= T && ye <= T + ph )
               {
                  g.pen = new Pen( FrameSelector.PLOT_COLOURS.EDGE );
                  g.drawLine( L, ye, L + pw, ye );
                  // First in the list, so a threshold keeps its number
                  // when the axis extreme would land on top of it.
                  wanted.push( { y: ye + 4, text: Frames.round( edges[e] ),
                                 edge: true } );
               }
            }
         wanted.push( { y: T + 8,  text: Frames.round( bounds.hi ) } );
         wanted.push( { y: T + ph, text: Frames.round( bounds.lo ) } );

         var labels = Frames.spacedLabels( wanted, FrameSelector.LABEL_GAP );
         for ( var li = 0; li < labels.length; ++li )
         {
            g.pen = new Pen( labels[li].edge ? FrameSelector.PLOT_COLOURS.EDGE
                                             : FrameSelector.PLOT_COLOURS.INK );
            g.drawText( 2, labels[li].y, labels[li].text );
         }

         /*
          * The line first, the markers over it.
          *
          * Drawn in one pass so a gap in the data breaks the line rather
          * than being bridged: an unmeasurable frame has no value, and
          * joining across it would draw a trend through a frame that was
          * never measured.
          */
         g.pen = new Pen( FrameSelector.PLOT_COLOURS.LINE, 1 );
         var prevX = null, prevY = null;
         for ( var j = 0; j < values.length; ++j )
         {
            if ( values[j] == null || !isFinite( values[j] ) )
            {
               prevX = null;
               continue;
            }
            var jx = xOf( j ), jy = yOf( values[j] );
            if ( prevX != null )
               g.drawLine( prevX, prevY, jx, jy );
            prevX = jx; prevY = jy;
         }

         for ( var k = 0; k < values.length; ++k )
         {
            if ( values[k] == null || !isFinite( values[k] ) )
               continue;
            var x = xOf( k ), y = yOf( values[k] );
            var row = this.rows[k];
            var rejected = ( Frames.finalState( row.state, row.override ) ==
                             Frames.STATE.REJECTED );
            if ( rejected )
            {
               // The same mark as the table's, for the same reason: the
               // frames about to be deleted are the ones worth finding.
               g.pen = new Pen( FrameSelector.PLOT_COLOURS.REJECT, 2 );
               g.drawLine( x-4, y-4, x+4, y+4 );
               g.drawLine( x+4, y-4, x-4, y+4 );
            }
            else
            {
               g.pen = new Pen( FrameSelector.PLOT_COLOURS.LINE );
               g.brush = new Brush( FrameSelector.PLOT_COLOURS.LINE );
               g.fillRect( x-2, y-2, x+2, y+2 );
            }
         }

         /*
          * The selection last, so the ring is never drawn under a marker
          * or the line. An empty brush, or the circle would fill and hide
          * the very point it is pointing at.
          */
         var sel = this.selected;
         if ( sel >= 0 && sel < values.length &&
              values[sel] != null && isFinite( values[sel] ) )
         {
            g.pen = new Pen( FrameSelector.PLOT_COLOURS.PICKED, 2 );
            g.brush = new Brush( FrameSelector.PLOT_COLOURS.PICKED,
                                 BrushStyle_Empty );
            g.drawCircle( xOf( sel ), yOf( values[sel] ),
                          FrameSelector.PICK_RADIUS );
         }
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
   ( new FrameSelector.Dialog( state ) ).execute();
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
