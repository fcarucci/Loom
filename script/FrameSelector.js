#engine v8

#feature-id    LoomFrameSelector : Loom > Frame Selector
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
 * it.
 */
#ifndef LOOM_LIBS_INCLUDED
#include "lib/Util.js"
#include "lib/Cache.js"
#include "lib/AsiairNames.js"
#include "lib/Asiair.js"
#include "lib/NightDialog.js"
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
 *
 * `schema`, when given, is shared by every batch of one channel: the
 * table's shape is checked on the channel's FIRST row only, and the
 * optional columns that check blanks stay blank for the whole channel --
 * exactly what one call over the channel would do. Checked per batch, a
 * column could be blank for some frames and not others.
 */
FrameSelector.measure = function( paths, schema )
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

   var shape = schema || {};              // disabled: optional columns unusable this run
   for ( var r = 0; r < P.measurements.length; ++r )
   {
      var m = Frames.metricsFromRow( P.measurements[r] );
      /*
       * The table's shape is a property of the process, not of the frame,
       * so it is checked on the FIRST row only: a second check proves
       * nothing new, and a single bad row is a bad frame, which the
       * review is there to catch.
       */
      if ( r == 0 && !shape.checked )
      {
         shape.checked = true;
         if ( ( shape.disabled = FrameSelector.checkSchema( m ) ) == null )
            return null;
      }
      var problem = FrameSelector.pathProblem( m, asked, out );
      if ( problem != null )
      {
         Util.error( "frames", problem + "; abandoning this channel" );
         return null;                    // null = the channel failed
      }
      Frames.sanitizeOptional( m, shape.disabled );
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
 * Frames read for display, counted: the tests hold the reading paths to
 * one read per frame shown.
 */
FrameSelector.io = { frameReads: 0 };

/*
 * A frame's first image read straight from its file, or null.
 *
 * NOT through ImageWindow.open, and nothing here runs a process: the
 * thumbnail loader calls this from a Timer while the review is open, and
 * every image window it created and closed, and every process it ran into
 * the Process Console, happened in PixInsight's workspace underneath the
 * dialog -- which closed a metric drop-down the moment it had been opened.
 * With the reader's verbosity at 0 nothing reaches the console either.
 */
FrameSelector.readFrame = function( path )
{
   var F = new FileFormat( File.extractExtension( path ), true, false );
   if ( F.isNull )
      return null;
   var f = new FileFormatInstance( F ), img = null;
   try
   {
      var d = f.open( path, "verbosity 0" );
      if ( d == null || d.length < 1 )
         return null;
      ++FrameSelector.io.frameReads;
      img = new Image;
      if ( !f.readImage( img ) )
         return null;
      var out = img;
      img = null;
      return out;
   }
   finally
   {
      if ( img != null )
         img.free();
      try { f.close(); } catch ( e ) {}
   }
};

/* The midtones transfer function, inlined: Math.mtf per sample is a native call. */
FrameSelector.mtf = function( m, x )
{
   if ( x <= 0 )
      return 0;
   if ( x >= 1 )
      return 1;
   return ( m - 1 )*x/( ( 2*m - 1 )*x - m );
};

/*
 * Stretch an image in place, as HistogramTransformation would with the
 * shadows clipped at median - 2.8 MAD and the median sent to a quarter.
 * Image.render() applies no STF, so a linear frame renders black without
 * this. Done on the samples, band by band, so no window and no process
 * is involved (see readFrame).
 */
FrameSelector.stretchInPlace = function( img )
{
   var med = img.median(), mad = img.MAD()*1.4826;
   var shadows = Math.max( 0, med - 2.8*mad );
   var midtone = Math.mtf( 0.25, Math.max( 1e-8, med - shadows ) );
   var span = Math.max( 1e-8, 1 - shadows );
   var band = Math.max( 1, Math.floor( 1048576/img.width ) );
   for ( var c = 0; c < img.numberOfChannels; ++c )
      for ( var y = 0; y < img.height; y += band )
      {
         var r = new Rect( 0, y, img.width, Math.min( img.height, y + band ) );
         var a = new Float32Array( r.width*r.height );
         img.getSamples( a, r, c );
         for ( var i = 0; i < a.length; ++i )
            a[i] = FrameSelector.mtf( midtone, ( a[i] - shadows )/span );
         img.setSamples( a, r, c );
      }
};

/*
 * A frame stretched for display, or null when it cannot be read. `fit`,
 * a { W, H } box, shrinks it into the box first (a thumbnail; resample's
 * single-factor form keeps the aspect); null keeps full size. The caller
 * frees the image.
 */
FrameSelector.stretchedImage = function( path, fit )
{
   var img = FrameSelector.readFrame( path );
   if ( img == null )
      return null;
   try
   {
      if ( fit != null )
         img.resample( Math.min( fit.W/img.width, fit.H/img.height ) );
      FrameSelector.stretchInPlace( img );
      return img;
   }
   catch ( e )
   {
      img.free();
      throw e;
   }
};

/* A frame stretched and rendered, the image freed either way; null when unreadable. */
FrameSelector.renderedFrame = function( path, fit )
{
   var img = FrameSelector.stretchedImage( path, fit );
   if ( img == null )
      return null;
   try { return img.render(); }
   finally { img.free(); }
};

/* One frame's thumbnail, or null when it cannot be read. */
FrameSelector.thumbnailOf = function( path )
{
   if ( !File.exists( path ) )
      return null;
   try
   {
      return FrameSelector.renderedFrame( path, FrameSelector.THUMB );
   }
   catch ( e )
   {
      Util.warn( "frames", "no thumbnail for " + path + ": " + e );
      return null;
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
 * A header read that can never put a box on screen.
 *
 * Util.readHeader falls back to a FULL read through ImageWindow.open when
 * the header-only reader fails, and ImageWindow.open raises a modal error
 * for a file that is missing or unreadable -- which stops a scan, and a
 * dispatched run with nobody there to dismiss it. A scan reads the header
 * only, and a file that is not there is not opened at all.
 */
FrameSelector.quietHeader = function( path )
{
   var info = null;
   try { info = File.exists( path ) ? Util.tryHeaderRead( path ).info : null; }
   catch ( e ) { info = null; }
   var kw = info ? info.keywords : null;
   return { info: info,
            keyword: function( name ) { return kw ? Util.keywordValue( kw, name ) : null; } };
};

/*
 * The header fields the comparability check needs. Calibration state is one
 * of them: raw and calibrated frames of one filter can agree on exposure,
 * binning and geometry, and an uncalibrated frame among calibrated ones
 * differs in every measured metric.
 */
FrameSelector.entryFor = function( path )
{
   var header = FrameSelector.quietHeader( path ), info = header.info, keyword = header.keyword;

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
            // When it was taken: blur that persists over consecutive frames
            // is focus, a single blurred frame among sharp ones is seeing.
            time:      keyword( "DATE-OBS" ),
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
      Util.ensureDirectory( Cache.dir() );
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
   return Util.findEntries( folder + "/*" ).filter( function( e )
   {
      return e.isFile && /\.(xisf|fits?|fit)$/i.test( e.name );
   } ).map( function( e ) { return folder + "/" + e.name; } );
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
 *
 * A master or calibration frame is left out -- and listed in `skipped` as
 * { path, kind } -- on its header alone, BEFORE it is digested: a masters
 * folder is gigabytes nobody asked to have read. See Frames.notSubframe.
 */
FrameSelector.cohortFrom = function( paths, progress )
{
   var entries = [], before = {}, skipped = [], unreadable = [], cancelled = false;
   for ( var i = 0; i < paths.length; ++i )
   {
      if ( progress != null &&
           progress( i + 1, paths.length, File.extractName( paths[i] ) ) === false )
      {
         cancelled = true;
         break;
      }
      if ( !File.exists( paths[i] ) )
      {
         unreadable.push( paths[i] );
         continue;
      }
      var e = FrameSelector.entryFor( paths[i] );
      var kind = Frames.notSubframe( File.extractName( paths[i] ), e.imageType );
      if ( kind != null )
      {
         skipped.push( { path: paths[i], kind: kind } );
         continue;
      }
      var id = FrameSelector.fileIdentity( paths[i] );
      if ( id == null )
      {
         unreadable.push( paths[i] );
         continue;
      }
      before[paths[i]] = id;
      e.identity = id;
      entries.push( e );
   }
   return { entries: entries, before: before, skipped: skipped, unreadable: unreadable,
            cancelled: cancelled };
};

/*
 * Frames per SubframeSelector call.
 *
 * Small enough that the scan window moves and Cancel is read every few
 * seconds on a real channel; large enough that the fixed cost of a call
 * stays a small part of the whole. Measured on 1.9.5 over 16 generated
 * 3000x2000 frames, warm: one call 523 ms; batches of 8, 596 ms; of 4,
 * 990 ms; of 1, 1052 ms -- roughly 35-150 ms per extra call, against
 * frames that measured at only 33 ms each. A real frame is several times
 * larger and slower, so at 8 the cost is a few percent.
 */
FrameSelector.MEASURE_BATCH = 8;

/*
 * Measure in batches of `size` through ONE schema, so the whole channel
 * is read exactly as a single call would read it. onBatch( done ) and
 * settle( batch, measured ) are optional; see Frames.measureInBatches.
 */
FrameSelector.measureBatched = function( paths, size, onBatch, settle )
{
   var schema = {};
   return Frames.measureInBatches( paths, size, function( batch )
   {
      var measured = FrameSelector.measure( batch, schema );
      if ( measured != null && settle != null )
         settle( batch, measured );
      return measured;
   }, onBatch );
};

/* A channel's frames split into those already measured and those not. */
FrameSelector.cachedSplit = function( group )
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
   return { need: need, metrics: metrics };
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
 * Done per batch, right after it is measured, and each batch's numbers are
 * cached then: a scan cancelled half way keeps the half it measured.
 *
 * Returns null when the group must be abandoned, which discards the
 * channel's numbers too -- a channel measured over a set that is not the
 * set on screen has no statistics worth showing. `cancelled` is set when
 * onBatch returned false.
 */
FrameSelector.measureGroup = function( group, before, onBatch, split )
{
   var plan = split || FrameSelector.cachedSplit( group );
   var metrics = plan.metrics, unstable = [];
   if ( plan.need.length == 0 )
      return { metrics: metrics, unstable: [], cancelled: false };

   var run = FrameSelector.measureBatched( plan.need, FrameSelector.MEASURE_BATCH, onBatch,
      function( batch, measured )
      {
         for ( var k = 0; k < batch.length; ++k )
         {
            var path = batch[k];
            var now = FrameSelector.fileIdentity( path );
            if ( now == null || now.digest != before[path].digest )
            {
               unstable.push( path );
               continue;                  // the measured bytes are gone
            }
            if ( measured[path] != null )
            {
               var stored = Frames.storedMetrics( measured[path] );
               FrameSelector.storeMeasurement( before[path], stored );
               metrics[path] = stored;
            }
         }
      } );
   if ( run.abandoned )
      return null;
   return { metrics: metrics, unstable: unstable, cancelled: run.cancelled };
};

/*
 * Scan an explicit list of frames.
 *
 * A night on an ASIAIR card is a FILTERED list of paths that may span
 * Plan and Autorun, which a single folder cannot express. This is scan()'s
 * body, with the enumeration lifted out; scan() now supplies the list.
 *
 * The whole contract is preserved: the `reading` and `measuring` progress
 * callbacks, cancellation by a callback RETURNING FALSE, and the shape of
 * a cancelled result. Ordinary folder scanning is this feature's
 * most-used path and must not regress.
 *
 * measuring( done, total, filter, overall ) is called as each channel
 * starts and after every batch: done and total count the channel's
 * frames, cached ones included; overall is { done, total, channel,
 * channels } over the frames still to be measured in the WHOLE scan, which
 * is what the bar shows. Cancel is read on every call.
 */
FrameSelector.scanPaths = function( paths, progress )
{
   var cohort = FrameSelector.cohortFrom( paths,
                                          progress ? progress.reading : null );
   if ( cohort.cancelled )
      return { channels: {}, unstable: [], skipped: cohort.skipped, cancelled: true };
   [ Frames.skippedLine( cohort.skipped ), Frames.unreadableLine( cohort.unreadable ) ]
      .forEach( function( line ) { if ( line.length > 0 ) Util.log( "frames", line ); } );

   var groups = Frames.groupByFilter( cohort.entries );
   var channels = {}, unstable = [];

   var keys = Object.keys( groups );
   var splits = [], overall = { done: 0, total: 0, channel: 0, channels: keys.length };
   for ( var i = 0; i < keys.length; ++i )
   {
      splits.push( FrameSelector.cachedSplit( groups[keys[i]] ) );
      overall.total += splits[i].need.length;
   }

   for ( var g = 0; g < keys.length; ++g )
   {
      var group = groups[keys[g]];
      var tell = FrameSelector.measuringReporter( progress, keys[g], group.length,
                                                  group.length - splits[g].need.length, overall );
      overall.channel = g + 1;
      if ( !tell( 0 ) )
         return { channels: channels, unstable: unstable, skipped: cohort.skipped, cancelled: true };
      var measured = FrameSelector.measureGroup( group, cohort.before, tell, splits[g] );
      overall.done = tell.base + splits[g].need.length;
      if ( measured == null )             // the channel was abandoned
      {
         channels[keys[g]] = { entries: group, metrics: {},
                               problems: [ "measurement failed" ] };
         continue;
      }
      unstable = unstable.concat( measured.unstable );
      /*
       * A channel stopped half way is not put in the result: its
       * statistics would describe a set that is not the channel.
       */
      if ( measured.cancelled )
         return { channels: channels, unstable: unstable, skipped: cohort.skipped, cancelled: true };
      channels[keys[g]] = { entries: group, metrics: measured.metrics,
                            problems: Frames.comparability( group ).problems };
   }
   return { channels: channels, unstable: unstable, skipped: cohort.skipped, cancelled: false };
};

/*
 * One channel's report: `measured` frames of this channel's batches done,
 * so far. Moves the whole-scan count on and answers whether to go on.
 * Created before the channel starts, so `base` is where it started.
 */
FrameSelector.measuringReporter = function( progress, filter, size, cached, overall )
{
   var tell = function( measured )
   {
      overall.done = tell.base + measured;
      if ( progress == null || progress.measuring == null )
         return true;
      return progress.measuring( cached + measured, size, filter, overall ) !== false;
   };
   tell.base = overall.done;
   return tell;
};

FrameSelector.scan = function( folder, progress )
{
   return FrameSelector.scanPaths( FrameSelector.frameFilesIn( folder ), progress );
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
      Util.ensureDirectory( File.extractDirectory( path ) );
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
 *
 * Each file is fingerprinted whole before it goes, which is slow on a big
 * folder, so onProgress( done, total, path ) is told before each one.
 */
FrameSelector.execute = function( manifest, onProgress )
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
      if ( onProgress )
         onProgress( i + 1, pending.length, e.path );
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
       * Checked before reading: ImageWindow.open raised a MODAL error
       * box for a file that is not there -- one the user has to
       * dismiss, per frame. A frame can vanish between the scan and the
       * review, and that is a preview which does not appear, not a dialog
       * demanding attention.
       */
      if ( !File.exists( path ) )
      {
         Util.warn( "frames", "no preview, the file is gone: " + path );
         return false;
      }

      try
      {
         /*
          * Stretched before rendering: Image.render() does NOT apply an
          * STF, so a linear sub renders as a black rectangle. Read and
          * stretched without a window or a process (see readFrame).
          */
         self.bmp = FrameSelector.renderedFrame( path, null );
         if ( self.bmp == null )
            return false;
         self.layOutScroll();
         return true;
      }
      catch ( e )
      {
         Util.warn( "frames", "could not preview " + path + ": " + e );
         return false;
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
            manifest: null, unstable: [],
            /*
             * Import mode, set only when the frames came off an ASIAIR
             * card. cardRoot being non-null is what makes the mode true,
             * and every guard reads it from here -- the review dialog
             * extends the native Dialog and does not inherit from
             * FrameSelector.prototype, so a flag hung there would be
             * permanently undefined and would guard nothing.
             */
            cardRoot: null, candidateFlats: [] };
};

/*
 * State from an explicit list of frames -- a night off an ASIAIR card.
 *
 * Shares everything below with buildState; only the source of the frames
 * differs. `label` is what the review calls the folder it is showing,
 * since a night has no single directory.
 */
FrameSelector.buildStateFrom = function( paths, label, progress )
{
   return FrameSelector.stateFromScan(
      FrameSelector.emptyState( label ),
      FrameSelector.scanPaths( paths, progress ) );
};

FrameSelector.stateFromScan = function( state, scan )
{
   state.cancelled = !!scan.cancelled;
   state.unstable = scan.unstable;
   state.skipped = scan.skipped || [];
   var keys = Object.keys( scan.channels );
   keys.sort();
   for ( var i = 0; i < keys.length; ++i )
   {
      var c = scan.channels[keys[i]];
      state.channels[keys[i]] = Frames.recompute(
         Frames.newChannel( keys[i], c.entries, c.metrics, c.problems ) );
      state.order.push( keys[i] );
   }
   return state;
};

FrameSelector.buildState = function( folder, progress )
{
   return FrameSelector.stateFromScan( FrameSelector.emptyState( folder ),
                                       FrameSelector.scan( folder, progress ) );
};

/*
 * Approved lights as { path, filter } -- what the manifest needs.
 *
 * approvedPaths returns bare strings and the review's rows carry the
 * channel rather than a filter field, so the two cannot be handed to
 * AsiairNames.manifest directly.
 */
FrameSelector.approvedLightRecords = function( state )
{
   var out = [];
   for ( var i = 0; i < state.order.length; ++i )
   {
      var key = state.order[i];
      var ch = state.channels[key];
      if ( !ch.settings.enabled )
         continue;
      for ( var r = 0; r < ch.rows.length; ++r )
         if ( Frames.finalState( ch.rows[r].state, ch.rows[r].override ) !=
              Frames.STATE.REJECTED )
            out.push( { path: ch.rows[r].path, filter: key } );
   }
   return out;
};

/*
 * Convert one frame by opening and saving it.
 *
 * NOT runOutputRoutine: that runs FrameSelector.MEASURE_ROUTINE first --
 * routines 1 and 2 refuse with "No measurements have been made" until it
 * has -- and measuring means star detection. A flat has no stars.
 */
FrameSelector.convertOne = function( src, dst )
{
   if ( !File.exists( src ) )
      return { ok: false, reason: "source is gone" };

   var win = null;
   try
   {
      var ws = ImageWindow.open( src );
      if ( ws.length == 0 )
         return { ok: false, reason: "could not open" };
      win = ws[0];
      if ( !win.saveAs( dst, false, false, false, false ) )
         return { ok: false, reason: "could not write" };
      return { ok: true, reason: "" };
   }
   catch ( e ) { return { ok: false, reason: String( e ) }; }
   finally { try { if ( win != null && !win.isNull ) win.forceClose(); } catch ( e2 ) {} }
};

/*
 * Keywords an imported frame must still carry. Geometry alone proves
 * nothing -- a wrong frame keeps the same dimensions.
 */
FrameSelector.REQUIRED_KEYWORDS = [ "FILTER", "EXPTIME", "DATE-OBS" ];

/*
 * Check what was written against what it came from.
 *
 * A file that fails is DELETED before the failure is reported: the export
 * path refuses to write over an existing file unless overwrite is set, so
 * leaving a bad one would block its own replacement forever.
 *
 * This cannot detect altered pixels. Converting to XISF re-encodes, so
 * the copy cannot be hashed against the card; geometry plus keyword
 * survival is the strongest check available under that choice.
 */
FrameSelector.verifyImported = function( src, dst )
{
   if ( !File.exists( dst ) )
      return { ok: false, reason: "nothing was written" };

   var problem = null;
   try
   {
      var a = Util.readImageInfo( src );
      var b = Util.readImageInfo( dst );
      if ( a.width != b.width || a.height != b.height )
         problem = "geometry changed";
      else
         for ( var i = 0; i < FrameSelector.REQUIRED_KEYWORDS.length; ++i )
         {
            var k = FrameSelector.REQUIRED_KEYWORDS[i];
            if ( Util.keywordValue( a.keywords, k ) != null &&
                 Util.keywordValue( b.keywords, k ) == null )
            {
               problem = k + " did not survive";
               break;
            }
         }
   }
   catch ( e ) { problem = String( e ); }

   if ( problem != null )
   {
      try { File.remove( dst ); } catch ( e2 ) {}
      return { ok: false, reason: problem };
   }
   return { ok: true, reason: "" };
};

/*
 * Write a whole manifest, verifying each file as it lands. onProgress
 * returning false (Cancel) stops it after the file just written.
 */
FrameSelector.writeManifest = function( manifest, onProgress )
{
   var all = manifest.lights.concat( manifest.flats );
   var written = 0, failed = [], cancelled = false;
   for ( var i = 0; i < all.length; ++i )
   {
      Util.ensureDirectory( all[i].dst.substring( 0, all[i].dst.lastIndexOf( "/" ) ) );

      var made = FrameSelector.convertOne( all[i].src, all[i].dst );
      var good = made.ok ? FrameSelector.verifyImported( all[i].src, all[i].dst ) : made;
      if ( good.ok )
         ++written;
      else
         failed.push( { src: all[i].src, reason: good.reason } );

      if ( onProgress && onProgress( i + 1, all.length ) === false )
      {
         cancelled = true;
         break;
      }
   }
   return { written: written, failed: failed, cancelled: cancelled };
};

/*
 * What a finished import says: how many were written, what failed, and
 * whether it was cancelled -- in which case the rest were never written,
 * and the card should not be wiped on the strength of it.
 */
FrameSelector.importOutcome = function( result )
{
   var head = result.cancelled
      ? "Import cancelled after " + result.written + " frame(s) were imported"
      : "Imported " + result.written + " frame(s)";
   var text = ( result.failed.length == 0 )
      ? head + "."
      : head + "; " + result.failed.length + " failed:\n\n" +
        result.failed[0].src + "\n" + result.failed[0].reason;
   if ( result.cancelled )
      text += ( result.failed.length == 0 ? " " : "\n\n" ) + "The rest were not written.";
   return { text: text,
            icon: ( result.failed.length == 0 && !result.cancelled ) ? StdIcon_Information
                                                                     : StdIcon_Warning };
};

/*
 * Is any output path on the card?
 *
 * A mandatory destination does not by itself protect it. Comparing the
 * two chosen directories as strings passes /Volumes/ASIAIR/export, and
 * passes a symlink pointing into the card -- so <dest>/Light and
 * <dest>/Flat are checked too, not just <dest>, and each is RESOLVED
 * first. Containment is compared on path components, so a sibling that
 * merely shares a prefix is not mistaken for a child.
 */
FrameSelector.outputsAreSafe = function( destination, cardRoot )
{
   if ( destination == null || cardRoot == null )
      return false;

   var root = FrameSelector.resolved( cardRoot );
   var out = [ destination, destination + "/Light", destination + "/Flat" ];
   for ( var i = 0; i < out.length; ++i )
      if ( AsiairNames.isInside( FrameSelector.resolved( out[i] ), root ) )
         return false;
   return true;
};

/*
 * A path with symlinks followed, where it exists. A path that is not
 * there yet resolves to itself -- the destination folders are created
 * later, and a name that does not exist cannot be a link onto the card.
 */
FrameSelector.resolved = function( path )
{
   try
   {
      if ( File.exists( path ) || File.directoryExists( path ) )
         return File.fullPath( path );
   }
   catch ( e ) {}
   return path;
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

   /* Which row the table has selected, or -1. */
   selectedRowIndex()
   {
      if ( this.frameTree == null )
         return -1;
      var n = this.frameTree.selectedNodes;
      return ( n.length && n[0].rowIndex != null ) ? n[0].rowIndex : -1;
   }

   /*
    * Move the table's selection to a row, as though it had been clicked.
    *
    * Assigning currentNode does not fire onNodeSelectionUpdated, so the
    * preview and the plot's ring are updated here rather than left to a
    * handler that will not run.
    *
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

   /* The frame table for the selected channel. */
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
      var presetNames = Frames.PRESET_NAMES;
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
      this.applyButton.onClick = function()
      {
         /*
          * WRAPPED. commit() resolves paths, builds a manifest, converts
          * and verifies -- every one of those can throw, and an exception
          * crossing back into Qt unwinds through a destructor into
          * std::terminate and takes the application with it.
          */
         try { self.commit(); }
         catch ( e )
         {
            try
            {
               FrameSelector.tell( "The run failed:\n\n" + e, StdIcon_Error );
            }
            catch ( e2 ) {}
         }
      };

      this.closeButton = new PushButton( this );
      this.closeButton.text = "Close";
      /*
       * Released on the way out, NOT by overriding cancel(). Dialog.cancel
       * is a native method and super.cancel() threw from it, which broke
       * the dialog outright -- caught only because the suite builds one and
       * closes it.
       */
      this.closeButton.onClick = function() { self.release(); self.cancel(); };
      /*
       * The window's own close box does not go through the Close button.
       * True, always: PJSR keeps the window open when onClose returns
       * anything else, undefined included, so the title-bar close did
       * nothing (FlyThrough's closing() had the same bug).
       */
      this.onClose = function() { self.release(); return true; };
   }

   editable()
   {
      // locked once an import or a copy has run, as a deletion locks it
      return Frames.canEdit( this.state.phase ) && !this.state.locked;
   }

   channel()
   {
      return ( this.current != null ) ? this.state.channels[this.current] : null;
   }

   toggleOverride()
   {
      var ch = this.channel();
      if ( ch == null || !this.editable() )
         return;
      var n = this.frameTree.selectedNodes;
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
      this.refresh();
   }

   fillChannels()
   {
      this.channelTree.clear();
      for ( var i = 0; i < this.state.order.length; ++i )
      {
         var key = this.state.order[i], ch = this.state.channels[key];
         var node = new TreeBoxNode( this.channelTree );
         node.channelKey = key;
         var c = Frames.counts( ch.rows );
         node.setText( 0, Frames.summaryLine( key, c ) +
                          ( ch.settings.enabled ? "" : "  [off]" ) );
         if ( key == this.current )
            node.selected = true;
      }
   }

   fillFrames()
   {
      this.frameTree.clear();
      var ch = this.channel();
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
         this.fillRow( new TreeBoxNode( this.frameTree ), ch.rows[i], i,
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
      var ch = this.channel();
      if ( ch == null )
      {
         this.problemsLabel.text = "";
         return;
      }
      this.kEdit.setValue( ch.settings.k );
      this.enabledCheck.checked = ch.settings.enabled;
      var pi = Frames.PRESET_NAMES.indexOf( ch.settings.preset );
      if ( pi >= 0 )
         this.presetCombo.currentItem = pi;
      this.syncCriteria( ch );
      /*
       * A mixed channel says what is mixed. "Not comparable" without the
       * reason leaves someone to guess whether to trust it.
       */
      this.problemsLabel.text = ch.problems.length
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
      var rows = [], perChannel = [];
      for ( var i = 0; i < this.state.order.length; ++i )
      {
         var key = this.state.order[i], ch = this.state.channels[key];
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

   /*
    * Every channel's verdicts from its cohort, and the totals Run acts on.
    * FROM THE COHORT, never from survivors: that would tighten the MAD
    * after each deletion and condemn a frame nobody looked at.
    */
   recomputeAll()
   {
      var t = { total: 0, condemned: 0, mixed: [] };
      for ( var i = 0; i < this.state.order.length; ++i )
      {
         var key = this.state.order[i], ch = this.state.channels[key];
         Frames.recompute( ch );
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
      FrameSelector.tell( message, StdIcon_Information );
   }

   /*
    * Write the approved frames to the chosen folder. Nothing is deleted,
    * so this asks once and reports rather than demanding the confirmation
    * the destructive path needs.
    */
   commitCopy()
   {
      var approved = this.approvedPaths();
      if ( approved.length == 0 )
         return null;

      var dest = this.state.destination;
      var result = FrameSelector.withProgress( "Loom Frame Selector - writing", function( w )
      {
         // one SubframeSelector call: a count it cannot give, so the block
         w.display( "Writing " + approved.length + " approved frame(s) as XISF", dest, null );
         return FrameSelector.exportApproved( approved, dest );
      } );
      // written: the review is done, as after a deletion -- Run is off
      if ( result.refused == null )
         this.state.locked = true;
      var message = ( result.refused != null )
         ? ( "Nothing was written: " + result.refused )
         : ( result.written + " frame(s) written to\n" + this.state.destination +
             ( result.failed ? ( "\n\n" + result.failed + " could not be written." )
                             : "" ) );
      FrameSelector.tell( message, StdIcon_Information );
      this.refresh();
      return result;
   }

   /* The frames came off a card, so nothing may be written back to it. */
   importing()
   {
      return this.state.cardRoot != null;
   }

   /*
    * Import: write the approved lights and this night's flats into the
    * chosen destination. Nothing on the card is touched, ever.
    */
   commitImport()
   {
      var self = this;
      var dest = self.state.destination;

      if ( dest == null || dest == self.state.folder )
      {
         FrameSelector.tell( "Choose a destination folder first.\n\n" +
                             "Frames are never written back to the card.", StdIcon_Information );
         return null;
      }
      if ( !FrameSelector.outputsAreSafe( dest, self.state.cardRoot ) )
      {
         FrameSelector.tell( "That destination is on the card.\n\n" +
                             dest + "\n\nPick somewhere else.", StdIcon_Error );
         return null;
      }

      var lights = FrameSelector.approvedLightRecords( self.state );
      if ( lights.length == 0 )
      {
         FrameSelector.tell( "Every frame is rejected; there is nothing to import.",
                             StdIcon_Information );
         return null;
      }

      var matches = FrameSelector.withProgress( "Loom Frame Selector - flats", function( w )
      {
         return self.matchedFlats( lights, function( done, total, path )
         {
            w.report( "Reading flat headers", done, total, File.extractName( path ) );
         } );
      } );
      var manifest = AsiairNames.manifest( lights, matches, AsiairNames.importRoot( dest ).root );
      if ( manifest.collisions.length > 0 )
      {
         FrameSelector.tell( "Two source frames would be written to one name, which would " +
                             "lose one of them:\n\n" + manifest.collisions[0].dst +
                             "\n\nNothing has been written.", StdIcon_Error );
         return null;
      }

      var summary = AsiairNames.importSummary( manifest, dest );
      if ( !FrameSelector.ask( summary, StdIcon_Question ) )
         return null;

      self.state.locked = true;
      var result = FrameSelector.withProgress( null, function( w )
      {
         return FrameSelector.writeManifest( manifest, function( done, total ) {
            try { return w.report( "Importing", done, total, "" ); } catch ( e ) { return true; }
         } );
      }, { cancellable: true } );

      var outcome = FrameSelector.importOutcome( result );
      FrameSelector.tell( outcome.text, outcome.icon );

      self.refresh();
      return result;
   }

   /*
    * Which candidate flats suit the surviving light filters.
    *
    * Headers are read here and not before: the filename filter is never
    * used to select or reject a flat, because narrowing by a value the
    * header can contradict drops flats that actually match and no later
    * check gets them back.
    *
    * One header per candidate flat, off the card: onFlat( done, total, path )
    * is told before each.
    */
   matchedFlats( lights, onFlat )
   {
      var wanted = [], seen = {};
      for ( var i = 0; i < lights.length; ++i )
         if ( !( lights[i].filter in seen ) )
         {
            seen[lights[i].filter] = true;
            var e = FrameSelector.entryFor( lights[i].path );
            wanted.push( { filter: e.filter, binning: e.binning,
                           camera: null, rotation: null } );
         }

      var records = [];
      var flats = this.state.candidateFlats || [];
      for ( var f = 0; f < flats.length; ++f )
      {
         if ( onFlat )
            onFlat( f + 1, flats.length, flats[f].path );
         records.push( Asiair.describe( flats[f] ) );
      }

      return AsiairNames.matchFlats( wanted, records );
   }

   /*
    * Snapshot the review into a manifest and run it. The manifest is a
    * snapshot: once this returns the review is locked, so no knob change
    * can alter what is already deleting files, and pressing Run twice
    * cannot run two manifests over one cohort.
    */
   commit()
   {
      if ( !this.editable() )
         return null;
      // A limit typed but not yet confirmed is part of what Run acts on.
      this.commitPendingEdits();
      /*
       * Import mode FIRST, and gated on the mode rather than on a
       * path comparison: the delete-in-place path below must be
       * unreachable when the source is a card, and a mode flag
       * cannot be defeated by a symlink.
       */
      if ( this.importing() )
         return this.commitImport();
      if ( this.copyingOut() )
         return this.commitCopy();
      var committable = this.committableRows();
      var manifest = Frames.buildManifest( committable.rows );
      if ( manifest.entries.length == 0 )
         return null;

      if ( !FrameSelector.ask( "Delete " + manifest.entries.length + " frame(s)?\n\n" +
                               committable.perChannel.join( "\n" ) + "\n\nThis cannot be undone.",
                               StdIcon_Warning ) )
         return null;

      this.state.phase = Frames.nextPhase( this.state.phase, "commit" );
      this.state.locked = true;
      this.state.manifest = manifest;
      var result = FrameSelector.withProgress( "Loom Frame Selector - deleting", function( w )
      {
         return FrameSelector.execute( manifest, function( done, total, path )
         {
            w.report( "Deleting rejected frames", done, total, File.extractName( path ) );
         } );
      } );

      /*
       * Asked for the input folder as the destination: the rejected
       * frames have just gone, and what remains is converted where it
       * sits. A folder already in XISF is a no-op, which is the
       * common case and costs nothing to say.
       *
       * After the deletion, deliberately: converting a frame that is
       * about to be removed is work thrown away.
       */
      if ( this.destinationIsSource() )
      {
         var keep = this.approvedPaths(), todo = Frames.needingXisf( keep ).length;
         // a folder already in XISF has nothing to convert and gets no window
         result.converted = ( todo == 0 ) ? FrameSelector.convertInPlace( keep ) :
            FrameSelector.withProgress( "Loom Frame Selector - converting", function( w )
            {
               w.display( "Converting " + todo + " frame(s) to XISF where they are",
                          "", null );
               return FrameSelector.convertInPlace( keep );
            } );
      }

      this.state.phase = Frames.nextPhase( this.state.phase,
                                           result.stopped ? "stop" : "finish" );
      this.refresh();
      this.reportOutcome( result );
      return result;
   }

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

   /* Sizers only: what goes where. */
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
      /*
       * The strip right under the frames and the preview it belongs with,
       * then the plot.
       */
      var strip = new HorizontalSizer;
      strip.spacing = 4;
      strip.add( this.stripMetric );
      strip.add( this.stripPrev );
      strip.add( this.filmstrip, 100 );
      strip.add( this.stripNext );
      this.sizer.add( middle, 100 );
      this.sizer.add( strip );
      this.sizer.add( this.plotPane );
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
      Util.ensureDirectory( destination );
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
   var entries = Util.findEntries( dir + "/*", true ).map( function( e )
   {
      return { path: dir + "/" + e.name, folder: e.isDirectory && !e.isSymbolicLink };
   } );
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
                     "F focus, S seeing, A altitude, T tracking, C cloud, " +
                     "D dropped.</p>";
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
      /*
       * A total nobody knows yet: the bar shows a moving block instead,
       * placed by the time since the first such report (Util.pulseBlock).
       */
      this.indeterminate = false;
      this.pulseStart = null;
      this.pulseElapsed = 0;

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
               if ( self.indeterminate )
               {
                  var block = Util.pulseBlock( self.pulseElapsed, w );
                  g.fillRect( block.x, 0, block.x + block.width, h, new Brush( 0xff3c8cd8 ) );
               }
               else
               {
                  var filled = Math.round( w * Math.max( 0, Math.min( 1, self.fraction ) ) );
                  if ( filled > 0 )
                     g.fillRect( 0, 0, filled, h, new Brush( 0xff3c8cd8 ) );
               }
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
      /*
       * Count first and on its own line: it is what the window is for.
       * The name goes second, where clipping costs least, elided from
       * the front so the timestamp and sequence number survive.
       *
       * An unknown total (a card being read) shows the count alone, never
       * "of 0", and a moving block rather than a bar that stays empty.
       */
      return this.display( phase + " (" + ( total > 0 ? done + " of " + total : done ) + ")",
                           Util.elideHead( label, FrameSelector.SCAN_NAME_CHARS ),
                           ( total > 0 ) ? ( done / total ) : null );
   }

   /*
    * Both lines and the bar. A fraction of null is a total nobody knows:
    * the block moves on by the time since the first such call.
    */
   display( title, detail, fraction )
   {
      try
      {
         this.indeterminate = ( fraction == null );
         if ( this.indeterminate )
         {
            var now = Date.now();
            if ( this.pulseStart == null )
               this.pulseStart = now;
            this.pulseElapsed = now - this.pulseStart;
         }
         else
            this.fraction = fraction;
         this.stageLabel.text = "<b>" + title + "</b><br>" + ( detail || "" );
         this.bar.repaint();
         CoreApplication.processEvents();
      }
      catch ( e ) {}
      return !this.cancelled;
   }

   /* A message with no count, for a step that has none yet (starting up). */
   announce( text )
   {
      this.display( text, "", 0 );
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
         /*
          * Each frame is fingerprinted whole and its header read before
          * anything is measured -- the slow first half of a scan.
          */
         reading: function( done, total, name )
         {
            return self.report( "Reading frames", done, total, name );
         },
         measuring: function( done, total, filter, overall )
         {
            var l = Frames.measuringLines( filter, done, total, overall );
            return self.display( l.title, l.detail, l.fraction );
         }
      };
   }
};

/*
 * Every message the Frame Selector shows goes through these two, under
 * its own title: tell() informs, ask() is a Yes/No question, Yes first and
 * the default. One place for the boxes, and the seam a test answers them
 * through instead of a modal window nobody is there to close.
 */
FrameSelector.tell = function( text, icon )
{
   ( new MessageBox( text, "Loom Frame Selector", icon, StdButton_Ok ) ).execute();
};

FrameSelector.ask = function( text, icon )
{
   return ( new MessageBox( text, "Loom Frame Selector", icon,
                            StdButton_Yes, StdButton_No ) ).execute() == StdButton_Yes;
};

/*
 * Run one step under its own progress window, which is always taken down
 * again, before anything modal can open behind it -- and released, rather
 * than left for the collector with JS still attached to it.
 *
 * Cancel is off unless opts.cancellable: steps that cannot be stopped half
 * way -- deleting, converting, writing -- must not offer a button that does
 * nothing. A null title keeps the window's own. opts.before( w ) runs
 * before the window is shown, so its first paint already says something.
 */
FrameSelector.withProgress = function( title, work, opts )
{
   opts = opts || {};
   var w = new FrameSelector.ScanWindow;
   try
   {
      if ( title != null )
         w.windowTitle = title;
      w.cancelButton.enabled = !!opts.cancellable;
      if ( opts.before )
         opts.before( w );
      w.show();
      CoreApplication.processEvents();
      return work( w );
   }
   finally
   {
      try { w.hide(); } catch ( e ) {}
      try { w.release(); } catch ( e ) {}
   }
};

/*
 * Offer a card, if one is plugged in.
 *
 * Returns a state to review, or null to fall through to the ordinary
 * folder chooser. Detection must never block startup: a card that is not
 * there costs two directory tests per mounted volume and nothing else.
 */
FrameSelector.offerCard = function()
{
   /*
    * The first thing on screen: looking through every mounted volume takes
    * a moment (Time Machine backups, network shares), and with nothing
    * shown the tool looked stuck while it opened.
    */
   var cards = [];
   try
   {
      cards = FrameSelector.withProgress( "Loom Frame Selector - starting up", function( looking )
      {
         return Asiair.detect( function() { return looking.cancelled; },
                               function( k, n, root ) { looking.report( "Looking for an ASIAIR", k, n, root ); } );
      }, { cancellable: true,
           before: function( w ) { w.announce( "Starting up: looking for an ASIAIR\u2026" ); } } );
   }
   catch ( e ) { cards = []; }
   if ( cards.length == 0 )
      return null;

   // no question first: a card is read straight away and every target's nights are shown (Cancel there opens the folder chooser)
   var root = cards[0];

   var scan = FrameSelector.withProgress( null, function( progress )
   {
      return Asiair.scanCard( root, function( n ) {
         try { progress.report( "Reading the ASIAIR card: files found", n, 0, root ); } catch ( e ) {}
      }, function() { return progress.cancelled; } );
   }, { cancellable: true } );

   if ( scan == null || scan.removed )
   {
      FrameSelector.tell( "The card went away while it was being read.", StdIcon_Warning );
      return null;
   }
   // a read the user stopped is no card: the folder chooser follows
   if ( scan.cancelled )
      return null;
   if ( scan.lights.length == 0 )
   {
      FrameSelector.tell( "No readable light frames on that card.", StdIcon_Information );
      return null;
   }

   var survey = NightDialog.surveyOf( scan, AsiairNames.GAP_HOURS );
   var picker = new NightDialog.Dialog( survey, root );
   var chose = false;
   try { chose = picker.execute(); }
   finally { try { picker.release(); } catch ( e ) {} }
   if ( !chose || picker.selectedNight == null )
      return null;

   var night = picker.selectedNight;
   var paths = [];
   for ( var i = 0; i < night.frames.length; ++i )
      paths.push( night.frames[i].path );

   var state = FrameSelector.withProgress( null, function( w )
   {
      return FrameSelector.buildStateFrom( paths, night.target + " " + night.date, w.callbacks() );
   }, { cancellable: true } );

   if ( state == null || state.cancelled )
      return null;

   /*
    * Import mode lives on the STATE, which the review dialog is handed.
    * FrameSelector.Dialog extends the native Dialog and does NOT inherit
    * from FrameSelector.prototype, so a flag put there would read as
    * undefined and every guard depending on it would be no guard at all.
    */
   state.cardRoot = root;
   state.candidateFlats = NightDialog.flatsForNight( survey, night );
   return state;
};

FrameSelector.main = function()
{
   var fromCard = FrameSelector.offerCard();
   if ( fromCard != null )
   {
      if ( fromCard.order.length > 0 )
         ( new FrameSelector.Dialog( fromCard ) ).execute();
      return;
   }

   var gd = new GetDirectoryDialog;
   gd.caption = "Select a folder of subframes";
   if ( !gd.execute() )
      return;

   /*
    * directoryPath, not directory: the older name is deprecated in 1.9.5
    * and warns on every run. Loom requires 1.9.5, so the new name is
    * always there.
    */
   var state = FrameSelector.withProgress( null, function( w )
   {
      return FrameSelector.buildState( gd.directoryPath, w.callbacks() );
   }, { cancellable: true } );

   if ( state.cancelled )
      return;

   if ( state.order.length == 0 )
   {
      FrameSelector.tell( Frames.noSubframesMessage( state.skipped ), StdIcon_Information );
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
 * numbers that mean something else. The refusal, checked first thing in
 * main(); see Util.checkCoreVersion.
 */
FrameSelector.CORE_CHECK = {
   title: "Loom Frame Selector: PixInsight is too old",
   message: function( core )
   {
      return "The Loom Frame Selector needs PixInsight " +
             Util.formatCoreVersion( Util.MIN_CORE ) + " or later.\n\n" +
             "This is PixInsight " + Util.formatCoreVersion( core ) +
             " (build " + core.build + ").\n\n" +
             "SubframeSelector's measurement columns were read off " +
             Util.formatCoreVersion( Util.MIN_CORE ) + ". An earlier version " +
             "returns a different table, so the figures this tool deletes frames " +
             "on would be read from the wrong columns. Please update PixInsight " +
             "and run it again.";
   }
};

function main()
{
   console.show();

   if ( !Util.checkCoreVersion( FrameSelector.CORE_CHECK ) )
      return;

   FrameSelector.main();
}

/*
 * selftest.js includes this file to reach the functions above, and must not
 * open a dialog while doing it: a modal window in a dispatched test run
 * holds the script queue with nobody there to dismiss it. Loom.js carries
 * the same guard, LOOM_UNDER_TEST.
 */
#ifndef LOOM_FRAME_SELECTOR_UNDER_TEST
main();
#endif
