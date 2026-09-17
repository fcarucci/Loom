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
   if ( !File.directoryExists( d ) )
      File.createDirectory( d, true );
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
   Cache.ensureDir();
   var path = Cache.companionPathFor( key, name );
   window.saveAs( path, false/*queryOptions*/, false/*allowMessages*/,
                  false/*strict*/, false/*noWarnings*/ );
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
   if ( !File.exists( path ) )
      return null;
   var ws = ImageWindow.open( path );
   if ( ws.length == 0 )
      return null;
   var w = ws[0];
   if ( newId )
      w.mainView.id = newId;
   return w;
};

Cache.ensureDir = function()
{
   if ( !File.directoryExists( Cache.dir() ) )
      File.createDirectory( Cache.dir(), true );
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
   var find = new FileFind;
   if ( find.begin( Cache.dir() + "/*" ) )
      do
      {
         if ( !find.isDirectory )
            total += find.size;
      }
      while ( find.next() );
   return total;
};

/* Deletes every cached file. Returns the number of bytes freed. */
Cache.clear = function()
{
   var freed = Cache.totalBytes();
   if ( !File.directoryExists( Cache.dir() ) )
      return 0;
   var doomed = [];
   var find = new FileFind;
   if ( find.begin( Cache.dir() + "/*" ) )
      do
      {
         if ( !find.isDirectory )
            doomed.push( Cache.dir() + "/" + find.name );
      }
      while ( find.next() );
   for ( var i = 0; i < doomed.length; ++i )
      try { File.remove( doomed[i] ); }
      catch ( e ) { /* leave it; the count below is best effort */ }
   return freed;
};

/*
 * Writes a view's window to the cache as XISF. XISF is used because it
 * preserves the astrometric solution and image properties -- FITS would
 * silently drop them, and the pipeline depends on the solution surviving.
 */
Cache.store = function( key, window, meta )
{
   Cache.ensureDir();
   var path = Cache.pathFor( key );
   window.saveAs( path, false/*queryOptions*/, false/*allowMessages*/,
                  false/*strict*/, false/*noWarnings*/ );
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
   if ( path == null )
      return null;
   var ws = ImageWindow.open( path );
   if ( ws.length == 0 )
      return null;
   var w = ws[0];
   if ( newId )
      w.mainView.id = newId;
   return w;
};

/* Number of cached results (xisf entries, ignoring json sidecars). */
Cache.entryCount = function()
{
   if ( !File.directoryExists( Cache.dir() ) )
      return 0;
   var n = 0;
   var find = new FileFind;
   if ( find.begin( Cache.dir() + "/*.xisf" ) )
      do
      {
         /*
          * Companions (<key>.stars.xisf) belong to an entry, they are not
          * entries: counting them would report twice as many cached results
          * as there are stages.
          */
         if ( !find.isDirectory && !Cache.isCompanionFileName( find.name ) )
            ++n;
      }
      while ( find.next() );
   return n;
};
