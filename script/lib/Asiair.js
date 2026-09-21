/*
 * Finding and reading an ASIAIR card.
 *
 * This file does I/O. The rules that can be subtly wrong -- parsing,
 * clustering, matching -- live next door in AsiairNames.js, where the
 * node suite can reach them. Keep it that way: anything here that starts
 * making a DECISION belongs over there, because nothing in this file is
 * covered by CI.
 */

function Asiair() {}

Asiair.MOUNTS  = "/Volumes";
Asiair.SOURCES = [ "Plan", "Autorun" ];

/*
 * By shape, never by volume name.
 *
 * Over USB-C the volume has been reported as BOOT; over the network the
 * same content is shared as "EMMC Images", and as "SD Images" or "USB
 * Images" when recording to removable storage. Which one you get depends
 * on model, firmware and where the frames were being written. A name list
 * would rot; the Autorun/Plan layout will not.
 *
 * Two directory tests, no walking. A stalled network mount costs two
 * calls rather than a traversal -- PJSR offers no way to preempt a hung
 * filesystem call, so the only defence available is to make very few.
 */
Asiair.looksLikeCard = function( root )
{
   try
   {
      return File.directoryExists( root + "/Plan/Light" ) ||
             File.directoryExists( root + "/Autorun/Light" );
   }
   catch ( e ) { return false; }
};

/*
 * Enumerates /Volumes one level deep. macOS only -- stated rather than
 * assumed, since the mount point is the whole assumption.
 *
 * Cancellable between volumes, not inside one: a single hung stat cannot
 * be interrupted from PJSR, and pretending otherwise would be a comment
 * that lies.
 */
Asiair.detect = function( shouldStop )
{
   var found = [];
   var find = new FileFind;
   if ( !find.begin( Asiair.MOUNTS + "/*" ) )
      return found;
   do
   {
      if ( shouldStop && shouldStop() )
         break;
      CoreApplication.processEvents();
      if ( !find.isDirectory || find.name == "." || find.name == ".." )
         continue;
      var root = Asiair.MOUNTS + "/" + find.name;
      if ( Asiair.looksLikeCard( root ) )
         found.push( root );
   }
   while ( find.next() );
   return found;
};

/*
 * Names of the entries in a directory, one level, no dot entries.
 *
 * FileFind yields "." and ".." like the C API it wraps. Collecting the
 * names first means the callers below are plain loops over an array
 * rather than do/while machinery wrapped around their own logic.
 */
Asiair.entriesIn = function( dir, wantDirectories )
{
   var out = [];
   var find = new FileFind;
   if ( !find.begin( dir + "/*" ) )
      return out;
   do
   {
      if ( find.name == "." || find.name == ".." )
         continue;
      if ( !!find.isDirectory == !!wantDirectories )
         out.push( find.name );
   }
   while ( find.next() );
   return out;
};

/*
 * Turn one filename into a frame, or say why not.
 *
 * A name that PARSES but carries an impossible date yields a null key,
 * and clustering drops null keys in silence -- so it is rejected here,
 * where the filename is still in hand to name in the report.
 */
Asiair.frameFrom = function( dir, name, target, source )
{
   var f = AsiairNames.parseName( name );
   if ( f == null )
      return null;
   var key = AsiairNames.stampKey( f.stamp );
   if ( key == null )
      return null;

   f.path = dir + "/" + name;
   f.source = source;
   f.key = key;
   if ( target != null )
      f.target = target;      // the folder, not the name: firmware omits it
   return f;
};

/*
 * Walk a card.
 *
 * Depth-limited to the layout that actually exists --
 * {Plan,Autorun}/Light/<target> and {Plan,Autorun}/Flat -- and never
 * recursive, so a symlink cycle on the card is unreachable rather than
 * merely unlikely.
 */
Asiair.scanCard = function( root, onProgress, shouldStop )
{
   var out = { lights: [], flats: [], unparseable: [],
               cancelled: false, removed: false };

   if ( !Asiair.looksLikeCard( root ) )
   {
      out.removed = true;
      return out;
   }

   /*
    * Yield to the UI and re-check the card. Returns false when the walk
    * must stop -- either the user cancelled or the card went away
    * mid-walk, which must read as removed rather than as a short but
    * successful scan.
    */
   function alive()
   {
      if ( shouldStop && shouldStop() ) { out.cancelled = true; return false; }
      CoreApplication.processEvents();
      if ( !Asiair.looksLikeCard( root ) ) { out.removed = true; return false; }
      return true;
   }

   function take( dir, target, source, into )
   {
      if ( !alive() )
         return false;

      var names = Asiair.entriesIn( dir, false );
      for ( var i = 0; i < names.length; ++i )
      {
         var f = Asiair.frameFrom( dir, names[i], target, source );
         if ( f == null )
            out.unparseable.push( dir + "/" + names[i] );
         else
            into.push( f );

         if ( onProgress )
            onProgress( out.lights.length + out.flats.length );
         if ( !alive() )
            return false;
      }
      return true;
   }

   for ( var s = 0; s < Asiair.SOURCES.length; ++s )
   {
      var src = Asiair.SOURCES[s];
      var lightDir = root + "/" + src + "/Light";

      var targets = File.directoryExists( lightDir )
                  ? Asiair.entriesIn( lightDir, true ) : [];
      for ( var t = 0; t < targets.length; ++t )
         if ( !take( lightDir + "/" + targets[t], targets[t], src, out.lights ) )
            return out;

      var flatDir = root + "/" + src + "/Flat";
      if ( File.directoryExists( flatDir ) )
         if ( !take( flatDir, null, src, out.flats ) )
            return out;
   }

   /*
    * Defence in depth: a card removed after the last directory was walked
    * would otherwise read as a clean scan. Not covered by a test --
    * isolating it needs a card with no target folders, which gives no
    * callback from which to remove it.
    */
   if ( !Asiair.looksLikeCard( root ) )
      out.removed = true;

   return out;
};

/*
 * A card frame described the way the flat matcher needs it.
 *
 * Each field from the best source available, which is NOT the same source
 * for all four:
 *
 *   filter, binning  header. Authoritative, and what WBPP will read.
 *   camera           header INSTRUME, falling back to the filename token.
 *   rotation         FILENAME ONLY. There is no standard FITS keyword for
 *                    a rotator angle, and it is not established that the
 *                    ASIAIR writes one. Comparing filenames is sound here
 *                    precisely because both sides come off the same card
 *                    with the same naming: it is name against name, not
 *                    name against header.
 *
 * Headers are read directly rather than through FrameSelector.entryFor,
 * so this library does not depend on the script that uses it.
 *
 * Without this adapter, entryFor-shaped records reach matchFlats with no
 * binning, no camera and no rotation at all -- and a comparison between
 * two absent fields reads as agreement, so every mismatched flat comes
 * back an exact match.
 */
Asiair.describe = function( frame )
{
   var info = null;
   try { info = Pipeline.readImageInfo( frame.path ); } catch ( e ) { info = null; }
   var kw = info ? info.keywords : null;

   function keyword( name )
   {
      return kw ? Util.keywordValue( kw, name ) : null;
   }

   return { path:     frame.path,
            filter:   keyword( "FILTER" ),
            binning:  keyword( "XBINNING" ),
            camera:   keyword( "INSTRUME" ) || frame.camera,
            rotation: frame.rotation };
};
