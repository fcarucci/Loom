#engine v8

#feature-id    Loom Frame Selector : Batch Processing > Loom Frame Selector
#feature-info  Measures every subframe in a folder, groups them by filter, and \
               removes the ones this night's own statistics condemn.

#include <pjsr/UndoFlag.jsh>
#include <pjsr/DataType.jsh>
#include <pjsr/StdButton.jsh>
#include <pjsr/StdIcon.jsh>
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
FrameSelector.cohortFrom = function( paths )
{
   var entries = [], before = {};
   for ( var i = 0; i < paths.length; ++i )
   {
      var id = FrameSelector.fileIdentity( paths[i] );
      if ( id == null )
         continue;
      before[paths[i]] = id;
      var e = FrameSelector.entryFor( paths[i] );
      e.identity = id;
      entries.push( e );
   }
   return { entries: entries, before: before };
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

FrameSelector.scan = function( folder )
{
   var cohort = FrameSelector.cohortFrom( FrameSelector.frameFilesIn( folder ) );
   var groups = Frames.groupByFilter( cohort.entries );
   var channels = {}, unstable = [];

   var keys = Object.keys( groups );
   for ( var g = 0; g < keys.length; ++g )
   {
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
   return { channels: channels, unstable: unstable };
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
FrameSelector.PreviewControl = class extends Control
{
   constructor( parent )
   {
   super( parent );

   var self = this;
   this.bmp = null;
   this.ox = 0;
   this.oy = 0;
   this.fit = false;
   this.dragging = false;
   this.lx = 0;
   this.ly = 0;

   this.setScaledMinSize( 420, 360 );
   this.focusStyle = FocusStyle.Click;     // arrows need focus; a click gives it






   this.setFit = function( on ) { self.fit = !!on; self.update(); };

   this.onPaint = function()
   {
      var g = new Graphics( this );
      try
      {
         g.fillRect( 0, 0, this.width, this.height, new Brush( 0xff101010 ) );
         if ( self.bmp == null )
            return;
         if ( self.fit )
         {
            var s = Math.min( this.width/self.bmp.width, this.height/self.bmp.height );
            g.drawScaledBitmap(
               new Rect( 0, 0, Math.round( self.bmp.width*s ),
                               Math.round( self.bmp.height*s ) ), self.bmp );
         }
         else
            g.drawBitmapRect( 0, 0, self.bmp,
               new Rect( self.ox, self.oy,
                         Math.min( self.ox + self.viewportWidth(),  self.bmp.width ),
                         Math.min( self.oy + self.viewportHeight(), self.bmp.height ) ) );
      }
      finally { g.end(); }
   };

   this.onMousePress = function( x, y )
   { self.dragging = true; self.lx = x; self.ly = y; };

   this.onMouseRelease = function() { self.dragging = false; };

   this.onMouseMove = function( x, y )
   {
      if ( !self.dragging )
         return;
      self.pan( self.lx - x, self.ly - y );
      self.lx = x; self.ly = y;
   };

   /*
    * Returning true CONSUMES the key. Without that the frame table below
    * also acts on the same arrow press and the selection moves underneath
    * the preview.
    */
   this.onKeyPress = function( key, modifiers )
   {
      var step = ( modifiers & KeyModifier.Shift ) ? this.width
                                                   : Math.round( this.width/4 );
      if ( key == KeyCode.Left  ) { self.pan( -step, 0 ); return true; }
      if ( key == KeyCode.Right ) { self.pan(  step, 0 ); return true; }
      if ( key == KeyCode.Up    ) { self.pan( 0, -step ); return true; }
      if ( key == KeyCode.Down  ) { self.pan( 0,  step ); return true; }
      return false;
   };

   this.onMouseWheel = function( x, y, delta )
   { self.setFit( !self.fit ); return true; };
   }

   dispose()
   {
      var self = this;
         self.bmp = null;                     // the only reference; let it go
   }

   load( path )
   {
      var self = this;
         self.dispose();
         self.ox = self.oy = 0;
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
            self.update();
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

      /*
       * The visible region is measured in BITMAP pixels, and a bitmap pixel is
       * a physical display pixel while the control's width is in logical ones.
       * The prototype measured physicalPixelRatio as 1 on this display, not the
       * 2 the plan assumed, so the ratio is read rather than hardcoded.
       */
   viewportWidth()
   {
      var self = this;
         return Math.round( self.width * ( self.bmp ? self.bmp.physicalPixelRatio : 1 ) );
   }

   viewportHeight()
   {
      var self = this;
         return Math.round( self.height * ( self.bmp ? self.bmp.physicalPixelRatio : 1 ) );
   }

   pan( dx, dy )
   {
      var self = this;
         if ( self.bmp == null )
            return;
         self.ox = Math.max( 0, Math.min( self.ox + dx,
                      Math.max( 0, self.bmp.width  - self.viewportWidth()  ) ) );
         self.oy = Math.max( 0, Math.min( self.oy + dy,
                      Math.max( 0, self.bmp.height - self.viewportHeight() ) ) );
         self.update();
   }

};

/* ------------------------------------------------------------------------
 * The review state: cohort, review and the phase that separates them.
 * ---------------------------------------------------------------------- */

FrameSelector.emptyState = function( folder )
{
   return { folder: folder, channels: {}, order: [],
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
            problems: problems || [], accepted: false,
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

   var gates = Frames.relativeGates( cohort, ch.settings.k );
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
         continue;
      }
      var v = Frames.verdict( row.metrics, gates, ch.settings );
      var suppressed = !mayReject && v.state == Frames.STATE.REJECTED;
      row.state   = suppressed ? Frames.STATE.APPROVED : v.state;
      row.reasons = suppressed ? [] : v.reasons;
   }
   return ch;
};

FrameSelector.buildState = function( folder )
{
   var state = FrameSelector.emptyState( folder );
   var scan = FrameSelector.scan( folder );
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
            self.syncKnobs();
         }
      };
   }

   /* The frame table for the selected channel. */
   buildFrameTable()
   {
      var self = this;
      this.frameTree = new TreeBox( this );
      this.frameTree.alternateRowColor = true;
      this.frameTree.numberOfColumns = 7;
      var heads = [ "Frame", "PSF SNR", "FWHM", "ecc", "stars", "score", "verdict" ];
      for ( var h = 0; h < heads.length; ++h )
         this.frameTree.setHeaderText( h, heads[h] );
      this.frameTree.setScaledMinWidth( 560 );
      this.frameTree.onNodeSelectionUpdated = function()
      {
         var n = self.frameTree.selectedNodes;
         if ( n.length && n[0].rowRef )
            self.preview.load( n[0].rowRef.path );
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
      this.presetCombo.currentItem = presetNames.indexOf( this.state.preset );
      this.presetCombo.onItemSelected = function( i )
      {
         if ( !self.editable() )
            return;
         self.state.preset = presetNames[i];
         /*
          * A preset sets k for every channel whose k has not been edited by
          * hand. An edited value stands; editing a WEIGHT does not pin k.
          */
         for ( var k = 0; k < self.state.order.length; ++k )
         {
            var c = self.state.channels[self.state.order[k]];
            c.settings = Frames.applyPreset( c.settings, presetNames[i] );
         }
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

      this.acceptMixedCheck = new CheckBox( this );
      this.acceptMixedCheck.text = "Accept a mixed channel";
      this.acceptMixedCheck.onCheck = function( checked )
      {
         var ch = self.channel();
         if ( ch == null || !self.editable() )
            return;
         ch.accepted = checked;
         self.refresh();
      };

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
      this.applyButton = new PushButton( this );
      this.applyButton.text = "Apply";
      this.applyButton.onClick = function() { self.commit(); };

      this.closeButton = new PushButton( this );
      this.closeButton.text = "Close";
      this.closeButton.onClick = function() { self.cancel(); };
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
                                ( ch.problems.length && !ch.accepted ? "  [mixed]" : "" ) );
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
            for ( var i = 0; i < ch.rows.length; ++i )
            {
               var row = ch.rows[i];
               var node = new TreeBoxNode( self.frameTree );
               node.rowRef = row;
               node.setText( 0, Frames.outputName( row.path ) );
               if ( row.metrics != null )
               {
                  node.setText( 1, Frames.round( row.metrics.psfSNR ) );
                  node.setText( 2, Frames.round( row.metrics.fwhm ) );
                  node.setText( 3, Frames.round( row.metrics.eccentricity ) );
                  node.setText( 4, String( row.metrics.stars ) );
               }
               node.setText( 5, ( row.score == null ) ? "-" : Frames.round( row.score ) );
               var final = Frames.finalState( row.state, row.override );
               var mark = ( row.override == Frames.OVERRIDE.RESCUED ) ? " (rescued)"
                        : ( row.override == Frames.OVERRIDE.CONDEMNED ) ? " (condemned)" : "";
               node.setText( 6, final + mark +
                                ( row.reasons.length ? ": " + row.reasons.join( "; " ) : "" ) );
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
            self.acceptMixedCheck.checked = ch.accepted;
            self.acceptMixedCheck.enabled = ch.problems.length > 0;
            self.modeCombo.currentItem =
               FrameSelector.MODE_NAMES.indexOf( ch.settings.mode );
            /*
             * A mixed channel says what is mixed. "Not comparable" without the
             * reason leaves someone to guess whether to trust it.
             */
            self.problemsLabel.text = ch.problems.length
               ? ( "Not comparable: " + ch.problems.join( ", " ) +
                   ". Apply is blocked for this channel until it is accepted or the "
                   + "folder is narrowed." )
               : ( ch.settings.kEdited ? "k has been set by hand for this channel; a "
                                       + "preset will not change it." : "" );
   }

         /*
          * Which rows an Apply is allowed to act on, and the per-channel tally the
          * confirmation shows. A disabled channel contributes nothing, and neither
          * does one that is not comparable until somebody has accepted it by hand;
          * buildManifest then decides row by row within what is left.
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
                  continue;                    // a disabled channel is untouched
               if ( ch.problems.length && !ch.accepted )
                  continue;
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
            var total = 0, condemned = 0, blocked = [];
            for ( var i = 0; i < self.state.order.length; ++i )
            {
               var key = self.state.order[i], ch = self.state.channels[key];
               FrameSelector.recompute( ch );
               var c = Frames.counts( ch.rows );
               total += c.total;
               if ( ch.settings.enabled )
                  condemned += c.rejected;
               if ( ch.settings.enabled && ch.problems.length && !ch.accepted && c.rejected )
                  blocked.push( key );
            }
            self.fillChannels();
            self.fillFrames();
            self.syncKnobs();
            self.blocked = blocked;
            self.summaryLabel.text =
               total + " frame(s), " + condemned + " to remove" +
               ( self.state.unstable.length
                 ? "; " + self.state.unstable.length +
                   " changed while being measured and were excluded" : "" ) +
               ( blocked.length ? "; blocked: " + blocked.join( ", " ) : "" );
            self.applyButton.enabled = self.editable() && condemned > 0 && blocked.length == 0;
   }

   /* Snapshot the review into a manifest and run it. */
   commit()
   {
      var self = this;
            if ( !self.editable() )
               return null;
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
            self.state.phase = Frames.nextPhase( self.state.phase,
                                                 result.stopped ? "stop" : "finish" );
            self.refresh();
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
      knobs.add( this.acceptMixedCheck );
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

      var buttons = new HorizontalSizer;
      buttons.spacing = 6;
      buttons.addStretch();
      buttons.add( this.applyButton );
      buttons.add( this.closeButton );

      this.sizer = new VerticalSizer;
      this.sizer.margin = 8;
      this.sizer.spacing = 6;
      this.sizer.add( middle, 100 );
      this.sizer.add( knobs );
      this.sizer.add( this.problemsLabel );
      this.sizer.add( this.summaryLabel );
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
FrameSelector.runOutputRoutine = function( sources, destination, overwrite )
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
   P.executeGlobal();
   return true;
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

FrameSelector.main = function()
{
   var gd = new GetDirectoryDialog;
   gd.caption = "Select a folder of subframes";
   if ( !gd.execute() )
      return;

   var state = FrameSelector.buildState( gd.directory );
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
