/*
 * lib/Cache.js
 *
 * Stage-level result cache. A full Loom run is slow -- plate solving a
 * 12006x7834 frame, MGC, GraXpert and StarAlignment at 2x all take
 * minutes -- and during iteration the same early stages are recomputed
 * repeatedly while debugging a later one.
 *
 * Keys CHAIN: each stage's key is derived from the previous stage's key
 * plus the stage name and its parameters. So changing anything early
 * (a different filter curve, a new GraXpert smoothing) invalidates every
 * stage after it automatically, with no dependency bookkeeping.
 *
 * A stale hit that silently reuses a wrong correction is far worse than
 * no cache, so the key includes every parameter that changes the result,
 * plus a format version that can be bumped to invalidate everything.
 *
 * Hashing follows PixInsight's own pattern in WBPP's BPP-ExecutionCache.js:
 * SHA1 over a UTF-8 config string.
 */

#include <pjsr/CryptographicHash.jsh>

var Cache = {};

/* Bump to invalidate every cached entry after a behaviour change. */
Cache.FORMAT_VERSION = "1";

/*
 * Where cached stage results live. Empty means the system temp dir, which
 * works on both macOS and Windows; a run can point this somewhere else --
 * a fast scratch volume, or a disk with room for the tens of gigabytes a
 * few full runs produce.
 *
 * Switching folders does NOT move anything: the old folder keeps its
 * entries and the new one starts cold. Cache.clear() likewise only ever
 * touches the folder currently selected.
 */
Cache.overrideDir = "";

Cache.setDir = function( path )
{
   Cache.overrideDir = ( path == null ) ? "" : String( path ).trim();
};

/*
 * Is the folder the user CHOSE actually there?
 *
 * Only a chosen folder can be missing in a way worth acting on. An empty
 * setting means the system temp directory, which always exists and is
 * created on demand if it does not.
 *
 * This matters because Util.ensureDirectory creates intermediate directories.
 * With the cache on an external volume -- under /Volumes -- an
 * unmounted drive would otherwise have Loom CREATE that path on the boot
 * disk and quietly fill it with the tens of gigabytes a few runs produce,
 * in a folder the user would never think to look in.
 */
Cache.selectedDirMissing = function()
{
   return Cache.overrideDir.length > 0 &&
          !File.directoryExists( Cache.overrideDir );
};

/*
 * Turns the cache off when its folder is missing, and says so once.
 *
 * Called at startup AND at the top of a run: the dialog lets the box be
 * ticked again, and the drive can be unmounted between opening the dialog
 * and pressing Run. Returns true when it disabled something, so the
 * caller can tell "already off" from "just turned off".
 */
Cache.disableIfDirMissing = function( config )
{
   if ( !config || !config.useCache || !Cache.selectedDirMissing() )
      return false;
   config.useCache = false;
   Util.warn( "cache", "the cache folder is not there (" + Cache.overrideDir +
                       "); running without the cache. If that is an external " +
                       "drive, mount it and start Loom again." );
   return true;
};

Cache.dir = function()
{
   return ( Cache.overrideDir.length > 0 ) ? Cache.overrideDir
                                           : File.systemTempDirectory + "/Loom-cache";
};

/*
 * Run logs live in a SUBDIRECTORY of the cache, not beside the entries.
 *
 * Cache.clear and Cache.totalBytes both walk every file in the cache
 * folder and both skip directories, so a subfolder keeps logs out of the
 * size the dialog reports and out of what "Clear cache" deletes. Clearing
 * the cache is about reclaiming gigabytes; taking the record of what went
 * wrong with it would be a poor trade.
 */
Cache.logDir = function()
{
   return Cache.dir() + "/logs";
};

Cache.ensureLogDir = function()
{
   var d = Cache.logDir();
   Util.ensureDirectory( d );
   return d;
};

Cache.hash = function( str )
{
   var h = new CryptographicHash( CryptographicHash.SHA1 );
   return h.hash( ByteArray.stringToUTF8( String( str ) ) ).toHex();
};

/*
 * Stable string for a parameter object: keys sorted, so two equivalent
 * objects always hash the same regardless of insertion order.
 */
Cache.paramsString = function( params )
{
   if ( params == null )
      return "";
   var keys = Object.keys( params ).sort();
   var parts = [];
   for ( var i = 0; i < keys.length; ++i )
   {
      var v = params[keys[i]];
      if ( v != null && typeof v == "object" )
         v = JSON.stringify( v );
      parts.push( keys[i] + "=" + String( v ) );
   }
   return parts.join( ";" );
};

/*
 * The key for a stage: previous key + stage name + parameters. Passing a
 * null previous key starts a chain from a source fingerprint.
 */
Cache.chainKey = function( previousKey, stage, params )
{
   return Cache.hash( Cache.FORMAT_VERSION + "|" +
                      String( previousKey || "" ) + "|" +
                      String( stage ) + "|" +
                      Cache.paramsString( params ) );
};

/* Identity of a source file: path, size and modification time. */
Cache.fingerprintFile = function( path )
{
   var info = new FileInfo( path );
   if ( !info.exists )
      return null;
   return Cache.hash( "file|" + path + "|" + info.size + "|" +
                      info.lastModified.toISOString() );
};

/*
 * Identity of a source view. A view id alone is not enough -- ids are
 * reused across sessions and projects -- so this mixes in geometry and
 * two cheap robust statistics, which differ for any genuinely different
 * image without reading every pixel.
 */
Cache.fingerprintView = function( view )
{
   /*
    * Deliberately NOT including view.id: ids are incidental and change
    * between runs (freeWindowId suffixes, names of windows loaded from
    * cache), which would make every key unstable. Identity comes from the
    * pixels: geometry plus two robust statistics.
    */
   var img = view.image;
   return Cache.hash( "view|" + img.width + "x" + img.height +
                      "|" + img.numberOfChannels +
                      "|" + img.median().toExponential( 12 ) +
                      "|" + img.MAD().toExponential( 12 ) );
};

Cache.pathFor = function( key )
{
   return Cache.dir() + "/" + key + ".xisf";
};

Cache.metaPathFor = function( key )
{
   return Cache.dir() + "/" + key + ".json";
};

/*
 * A stage normally produces one image, and its key names that image. Star
 * extraction produces TWO from a single run of an expensive neural tool --
 * the starless frame and the stars frame -- and they are inseparable: you
 * cannot recompute one without recomputing the other.
 *
 * So the stage's own key stores the starless (the chain continues on it,
 * which is what the following stages consume) and the stars image rides
 * alongside under the same key. Making stars a stage of its own would key
 * correctly but run the tool a second time to recover an image the first
 * run already had in hand.
 */
/* "<40-hex-key>.stars.xisf" -- a companion, not an entry of its own. */
Cache.isCompanionFileName = function( name )
{
   return /^[0-9a-f]{40}\.[A-Za-z0-9_]+\.xisf$/.test( String( name ) );
};

Cache.companionPathFor = function( key, name )
{
   return Cache.dir() + "/" + key + "." + name + ".xisf";
};

Cache.storeCompanion = function( key, name, window )
{
   Util.ensureDirectory( Cache.dir() );
   var path = Cache.companionPathFor( key, name );
   window.saveAs( path, false/*queryOptions*/, false/*allowMessages*/,
                  false/*strict*/, false/*noWarnings*/ );

   /*
    * Verified on the same rule as the entry it belongs to. A companion that
    * will not load is how a run ends with every starless plate present and
    * no stars plate at all.
    */
   var bad = Cache.verifyStoredFile( path, window );
   if ( bad != null )
   {
      Cache.discardUnreadable( path, key + "." + name, bad );
      return null;
   }
   return path;
};

/*
 * Path of an entry's companion, or null when absent -- the same file-exists
 * question Cache.lookup asks about the entry itself, asked about its other
 * half. Answering it without opening anything is what lets a caller decide
 * an entry is half-written before it starts work.
 */
Cache.lookupCompanion = function( key, name )
{
   var p = Cache.companionPathFor( key, name );
   return File.exists( p ) ? p : null;
};

/*
 * Returns null when the companion is absent. A hit on the stage key with a
 * missing companion is a half-written entry -- the caller treats it as a
 * miss rather than silently continuing with no stars image.
 */
Cache.loadCompanion = function( key, name, newId )
{
   var path = Cache.companionPathFor( key, name );
   return File.exists( path ) ? Cache.openAs( path, newId ) : null;
};

/* The image at `path` as a window with id `newId` (when given); null when it does not open. */
Cache.openAs = function( path, newId )
{
   var ws = ImageWindow.open( path );
   if ( ws.length == 0 )
      return null;
   var w = ws[0];
   if ( newId )
      w.mainView.id = newId;
   return w;
};

/* Path of a cached result, or null when absent. */
Cache.lookup = function( key )
{
   var p = Cache.pathFor( key );
   return File.exists( p ) ? p : null;
};

/*
 * Human-readable byte count. Pure, so it is unit tested.
 */
Cache.formatBytes = function( bytes )
{
   if ( bytes == null || bytes < 0 )
      return "0 B";
   var units = [ "B", "KB", "MB", "GB", "TB" ];
   var v = bytes, u = 0;
   while ( v >= 1024 && u < units.length-1 ) { v /= 1024; ++u; }
   return ( u == 0 ? v.toFixed( 0 ) : v.toFixed( 1 ) ) + " " + units[u];
};

/* Total bytes currently held in the cache directory. */
Cache.totalBytes = function()
{
   if ( !File.directoryExists( Cache.dir() ) )
      return 0;
   var total = 0;
   Util.findEntries( Cache.dir() + "/*", true ).forEach( function( e ) { if ( !e.isDirectory ) total += e.size; } );
   return total;
};

/* Deletes every cached file. Returns the number of bytes freed. */
Cache.clear = function()
{
   var freed = Cache.totalBytes();
   if ( !File.directoryExists( Cache.dir() ) )
      return 0;
   Util.findEntries( Cache.dir() + "/*", true ).forEach( function( e )
   {
      if ( !e.isDirectory )
         try { File.remove( Cache.dir() + "/" + e.name ); }
         catch ( x ) { /* leave it; the count below is best effort */ }
   } );
   return freed;
};

/*
 * Writes a view's window to the cache as XISF. XISF is used because it
 * preserves the astrometric solution and image properties -- FITS would
 * silently drop them, and the pipeline depends on the solution surviving.
 */
/*
 * Prove a stored entry reads back before it is recorded as one.
 *
 * saveAs reports nothing useful when a write goes wrong: Cache.store used
 * to call it and then write the sidecar unconditionally, so a bad write
 * became an entry that looked completely valid. The cost landed on the NEXT
 * run, which found the key, failed to open the file, and silently rebuilt
 * whatever the entry was meant to save -- observed as a finished run whose
 * successor redid the entire HSO palette and the RGB tail.
 *
 * The check is a real ImageWindow.open, the same call Cache.load makes,
 * because anything weaker is a guess about what the reader will accept. A
 * size test cannot be used: XISF may be written compressed, so a valid file
 * is legitimately smaller than its pixel data and the guard would reject
 * every entry and disable the cache entirely.
 *
 * It costs one extra read of what was just written, and only on a MISS --
 * the path that already paid for the processing.
 *
 * Returns null when the entry is good, or a reason when it is not.
 */
Cache.verifyStoredFile = function( path, window )
{
   if ( !File.exists( path ) )
      return "it was not written at all";
   var back = null;
   try
   {
      var ws = ImageWindow.open( path );
      if ( ws == null || ws.length == 0 )
         return "it contains no readable image";
      back = ws[0];
      var a = back.mainView.image, b = window.mainView.image;
      if ( a.width != b.width || a.height != b.height ||
           a.numberOfChannels != b.numberOfChannels )
         return "it reads back as " + a.width + "x" + a.height + "x" +
                a.numberOfChannels + ", not the " + b.width + "x" + b.height +
                "x" + b.numberOfChannels + " that was saved";
      return null;
   }
   catch ( e )
   {
      return String( e );
   }
   finally
   {
      if ( back != null )
         try { back.forceClose(); } catch ( e ) {}
   }
};

/*
 * Discard an entry that will not read back, loudly. Leaving it in place is
 * the one outcome that must not happen: it is indistinguishable from a good
 * entry until the next run tries to use it.
 */
Cache.discardUnreadable = function( path, key, reason )
{
   Util.error( "cache", String( key ).substring( 0, 12 ) +
      " was written but will not read back (" + reason + "); discarding it " +
      "rather than leaving a broken entry for the next run" );
   try { File.remove( path ); }
   catch ( e ) { Util.warn( "cache", "could not remove " + path + ": " + e ); }
};

Cache.store = function( key, window, meta )
{
   Util.ensureDirectory( Cache.dir() );
   var path = Cache.pathFor( key );
   window.saveAs( path, false/*queryOptions*/, false/*allowMessages*/,
                  false/*strict*/, false/*noWarnings*/ );

   var bad = Cache.verifyStoredFile( path, window );
   if ( bad != null )
   {
      Cache.discardUnreadable( path, key, bad );
      return null;
   }

   try
   {
      var f = new File;
      f.createForWriting( Cache.metaPathFor( key ) );
      f.outText( JSON.stringify( meta || {}, null, 2 ) + "\n" );
      f.close();
   }
   catch ( e ) { /* the sidecar is for humans; its absence is not fatal */ }
   return path;
};

/*
 * Opens a cached result as a new window with the given id. Returns null
 * when there is no entry for the key.
 */
Cache.load = function( key, newId )
{
   var path = Cache.lookup( key );
   return ( path == null ) ? null : Cache.openAs( path, newId );
};

/* Number of cached results (xisf entries, ignoring json sidecars). */
Cache.entryCount = function()
{
   if ( !File.directoryExists( Cache.dir() ) )
      return 0;
   /*
    * Companions (<key>.stars.xisf) belong to an entry, they are not
    * entries: counting them would report twice as many cached results
    * as there are stages.
    */
   return Util.findEntries( Cache.dir() + "/*.xisf" ).filter( function( e )
   {
      return !e.isDirectory && !Cache.isCompanionFileName( e.name );
   } ).length;
};

/* ------------------------------------------------------------------------
 * Frame previews: the Frame Selector's stretched renders, kept on disk.
 *
 * Clicking a frame used to read the whole sub and run a
 * HistogramTransformation every time, and every thumbnail was read and
 * stretched again on every launch. Each render is kept here as an 8-bit
 * image, keyed by the frame's path, size and modification time, what it is
 * (a full preview or a thumbnail) and every setting that changes the
 * render, so a frame replaced under the same name, or a changed stretch,
 * is a different key rather than a stale hit.
 *
 * Its own folder in the system temp directory, NOT Cache.dir(): these are
 * small, disposable and per machine, and must not count against, or be
 * placed on, the drive a user chose for stage results. Capped by size,
 * least recently used first; Loom's "Clear cache" empties it too.
 * ---------------------------------------------------------------------- */

Cache.PREVIEW_VERSION = "1";

/* About forty full-size 26 MP previews (25 MB each at 8 bits), and every thumbnail. */
Cache.PREVIEW_CAP_BYTES = 1024*1024*1024;

/* Empty means the default; the suite points it at its own scratch folder. */
Cache.previewDirOverride = "";

Cache.previewDir = function()
{
   return ( Cache.previewDirOverride.length > 0 )
      ? Cache.previewDirOverride : File.systemTempDirectory + "/Loom-previews";
};

Cache.previewPath = function( key )
{
   return Cache.previewDir() + "/" + key + ".xisf";
};

Cache.previewIndexPath = function()
{
   return Cache.previewDir() + "/index.json";
};

/* Pure: the key for one render of one version of one file. */
Cache.previewKeyOf = function( path, size, modified, kind, params )
{
   return Cache.hash( "preview|" + Cache.PREVIEW_VERSION + "|" + kind + "|" +
                      path + "|" + size + "|" + modified + "|" +
                      Cache.paramsString( params ) );
};

/* The key for `path` as it is on disk now, or null when it is not there. */
Cache.previewKey = function( path, kind, params )
{
   var info = new FileInfo( path );
   if ( !info.exists )
      return null;
   return Cache.previewKeyOf( path, info.size, info.lastModified.toISOString(),
                              kind, params );
};

/*
 * When each entry was last used: { key: time }. A missing or unreadable
 * index is an empty one -- the entries still work, they are just the
 * first to go when the folder is trimmed.
 */
Cache.readPreviewIndex = function()
{
   try
   {
      if ( File.exists( Cache.previewIndexPath() ) )
      {
         var idx = JSON.parse( File.readTextFile( Cache.previewIndexPath() ) );
         if ( idx != null && typeof idx == "object" )
            return idx;
      }
   }
   catch ( e ) {}
   return {};
};

/*
 * Records a use of `key`. Strictly later than every use already recorded,
 * so two uses in the same millisecond still have an order.
 */
Cache.notePreviewUse = function( key )
{
   try
   {
      Util.ensureDirectory( Cache.previewDir() );
      var idx = Cache.readPreviewIndex(), latest = 0;
      for ( var k in idx )
         latest = Math.max( latest, Number( idx[k] ) || 0 );
      idx[key] = Math.max( Date.now(), latest + 1 );
      File.writeTextFile( Cache.previewIndexPath(), JSON.stringify( idx ) );
   }
   catch ( e ) { /* the order is advisory; the entry itself is intact */ }
};

/* Every entry on disk: { key, bytes, used }. */
Cache.previewEntries = function()
{
   if ( !File.directoryExists( Cache.previewDir() ) )
      return [];
   var idx = Cache.readPreviewIndex();
   return Util.findEntries( Cache.previewDir() + "/*.xisf" ).filter( function( e )
   {
      return !e.isDirectory && /\.xisf$/.test( e.name );
   } ).map( function( e )
   {
      var key = e.name.replace( /\.xisf$/, "" );
      return { key: key, bytes: e.size, used: Number( idx[key] ) || 0 };
   } );
};

Cache.previewBytes = function()
{
   return Cache.previewEntries().reduce( function( t, e ) { return t + e.bytes; }, 0 );
};

/*
 * Pure: which keys to remove so what is left fits `cap`, least recently
 * used first. `keep` -- the entry just stored -- is never one of them.
 */
Cache.previewEvictions = function( entries, cap, keep )
{
   var total = entries.reduce( function( t, e ) { return t + e.bytes; }, 0 );
   var order = entries.filter( function( e ) { return e.key !== keep; } )
                      .sort( function( a, b ) { return a.used - b.used; } );
   var out = [];
   for ( var i = 0; i < order.length && total > cap; ++i )
   {
      out.push( order[i].key );
      total -= order[i].bytes;
   }
   return out;
};

/*
 * Removes the least recently used entries until the folder fits `cap`,
 * then rewrites the index with only the entries still on disk, so it does
 * not keep a line for every frame ever shown.
 */
Cache.trimPreviews = function( cap, keep )
{
   var gone = Cache.previewEvictions( Cache.previewEntries(), cap, keep );
   for ( var i = 0; i < gone.length; ++i )
      try { File.remove( Cache.previewPath( gone[i] ) ); } catch ( e ) {}
   Cache.prunePreviewIndex();
   return gone.length;
};

/* Drops index entries whose preview file is gone. */
Cache.prunePreviewIndex = function()
{
   try
   {
      var idx = Cache.readPreviewIndex(), kept = {}, changed = false;
      for ( var k in idx )
      {
         if ( File.exists( Cache.previewPath( k ) ) )
            kept[k] = idx[k];
         else
            changed = true;
      }
      if ( changed )
         File.writeTextFile( Cache.previewIndexPath(), JSON.stringify( kept ) );
   }
   catch ( e ) { /* the index is advisory; a stale line is only a stale line */ }
};

/* Deletes every preview and the index. Returns the bytes the previews held. */
Cache.clearPreviews = function()
{
   var freed = Cache.previewBytes();
   if ( !File.directoryExists( Cache.previewDir() ) )
      return 0;
   Util.findEntries( Cache.previewDir() + "/*", true ).forEach( function( e )
   {
      if ( !e.isDirectory )
         try { File.remove( Cache.previewDir() + "/" + e.name ); } catch ( x ) {}
   } );
   return freed;
};
