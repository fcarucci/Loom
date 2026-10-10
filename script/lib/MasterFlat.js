/*
 * Master flats for the ASIAIR import.
 *
 * With a darks folder set and "Combine flats to masters" ticked, the
 * night's flats are not kept: each filter's flats are calibrated with a
 * matching dark, integrated, and stored as one masterFlat_<FILTER>.xisf.
 *
 * Two layers, kept apart on purpose:
 *
 *   pure     Dark classification and matching, subgrouping, file names,
 *            rejection choice, the plan, the master's verification and the
 *            "may this file be deleted" rule. Nothing here touches PixInsight,
 *            so the node suite covers every line.
 *   engine   MasterFlat.engine: ImageCalibration and ImageIntegration through
 *            PJSR, headers, files. Only PixInsight runs it.
 *
 * MasterFlat.execute drives an engine through a plan and owns every
 * decision about data: a filter's raw flats are deleted only after its master
 * is written AND verified, and kept (and reported) when anything goes wrong.
 * It takes the engine as an argument so the node suite can run that logic
 * with a fake. Nothing here ever writes to, or deletes from, the card.
 *
 * Needs Util and Cache loaded first.
 */

var MasterFlat = {};

MasterFlat.SETTINGS_DARKS = "Loom/importDarksFolder";
MasterFlat.SETTINGS_ONLY  = "Loom/importMasterFlatsOnly";

/* Frames of one kind needed before they are worth integrating. */
MasterFlat.MIN_FLATS = 2;
MasterFlat.MIN_DARKS = 2;

/* Exposure: 1% of the longer one, never less than a millisecond. */
MasterFlat.EXPOSURE_REL = 0.01;
MasterFlat.EXPOSURE_ABS = 0.001;
/* Sensor temperature, degrees C. Both must state it: a missing one is not a match. */
MasterFlat.TEMP_TOLERANCE = 3;
/* Gain, compared only when both state it. */
MasterFlat.GAIN_TOLERANCE = 0.5;

/* ------------------------------------------------------------------------
 * Settings
 * ---------------------------------------------------------------------- */

/* PixInsight's Settings, looked up on each call. */
MasterFlat.settingsStore = function()
{
   return { read:  function( key, type ) { return Settings.read( key, type ); },
            write: function( key, type, value ) { Settings.write( key, type, value ); } };
};

/* `store` is { read( key, type ), write( key, type, value ) }, as Config.pixinsightStore().settings. */
MasterFlat.loadSettings = function( store )
{
   var folder = "", only = false;
   try
   {
      var f = store.read( MasterFlat.SETTINGS_DARKS, DataType_String );
      if ( f != null && String( f ).length > 0 )
         folder = String( f );
      only = store.read( MasterFlat.SETTINGS_ONLY, DataType_Boolean ) === true;
   }
   catch ( e ) {}
   return { darksFolder: folder, onlyMasterFlats: only };
};

MasterFlat.saveSettings = function( store, darksFolder, onlyMasterFlats )
{
   try
   {
      store.write( MasterFlat.SETTINGS_DARKS, DataType_String, darksFolder || "" );
      store.write( MasterFlat.SETTINGS_ONLY, DataType_Boolean, !!onlyMasterFlats );
   }
   catch ( e ) {}
};

/*
 * Master-flats mode: the checkbox, and somewhere to get darks from: a darks
 * folder, or darks found on the card (`cardDarksFound`, see cardDarksUi).
 * The checkbox means nothing without either, so a stale ticked box with the
 * folder cleared is NOT the mode.
 */
MasterFlat.active = function( darksFolder, onlyMasterFlats, cardDarksFound )
{
   return !!onlyMasterFlats &&
          ( !!cardDarksFound || ( darksFolder != null && String( darksFolder ).length > 0 ) );
};

/* ------------------------------------------------------------------------
 * Describing frames
 * ---------------------------------------------------------------------- */

MasterFlat.number = function( text )
{
   if ( text == null )
      return null;
   var n = parseFloat( String( text ).replace( /[^0-9eE+.\-]/g, "" ) );
   return isFinite( n ) ? n : null;
};

/*
 * A frame as the plan needs it, from a path and a keyword( name ) reader
 * (Util.readHeader( path ).keyword). Every field is null when the header
 * does not state it.
 */
MasterFlat.describe = function( path, keyword )
{
   return {
      path:     path,
      name:     String( path ).split( "/" ).pop(),
      imagetyp: keyword( "IMAGETYP" ),
      filter:   keyword( "FILTER" ),
      exposure: MasterFlat.number( keyword( "EXPTIME" ) != null ? keyword( "EXPTIME" ) : keyword( "EXPOSURE" ) ),
      binning:  MasterFlat.number( keyword( "XBINNING" ) ),
      gain:     MasterFlat.number( keyword( "GAIN" ) ),
      temp:     MasterFlat.number( keyword( "CCD-TEMP" ) != null ? keyword( "CCD-TEMP" ) : keyword( "SET-TEMP" ) )
   };
};

/*
 * Is a file in the darks folder a dark, and a master or a raw one?
 *
 * IMAGETYP decides when present, as Util.isMasterLight does for lights;
 * the name is the fallback ("masterDark...", "Dark_..."). Flats, lights and
 * bias in the folder are not darks. Returns "master", "raw" or null.
 */
MasterFlat.darkKind = function( name, imagetyp )
{
   var n = String( name || "" ), t = String( imagetyp || "" ).replace( /'/g, "" ).trim();
   if ( t.length > 0 )
   {
      if ( /flat|light|bias|offset|zero/i.test( t ) || !/dark/i.test( t ) )
         return null;
      return ( /master/i.test( t ) || /^master/i.test( n ) ) ? "master" : "raw";
   }
   if ( /flat|light|bias|offset/i.test( n ) || !/dark/i.test( n ) )
      return null;
   return /^master/i.test( n ) ? "master" : "raw";
};

MasterFlat.IMAGE_EXTENSION = /\.(xisf|fits?|fts)$/i;

/* Darks out of a list of described frames: each gains `kind`. Others are dropped. */
MasterFlat.darksOf = function( frames )
{
   var out = [];
   for ( var i = 0; i < frames.length; ++i )
   {
      var kind = MasterFlat.darkKind( frames[i].name, frames[i].imagetyp );
      if ( kind != null )
      {
         var d = {};
         for ( var k in frames[i] ) d[k] = frames[i][k];
         d.kind = kind;
         out.push( d );
      }
   }
   return out;
};

/* ------------------------------------------------------------------------
 * Darks on the ASIAIR card
 * ---------------------------------------------------------------------- */

/*
 * The darks of a card, described, from Asiair.scanCard's `darks` frames.
 *
 * Headers come from `readHeader( path )` (FrameSelector.quietHeader): never
 * an ImageWindow.open, and a file that has gone (a card pulled out) reads
 * as no header and is left out -- so a vanished card yields no darks, and a
 * plan made from none keeps every raw flat. Each dark gains size and
 * modified (when readable) and source "card".
 */
MasterFlat.cardDarks = function( frames, readHeader )
{
   var described = [];
   for ( var i = 0; i < frames.length; ++i )
   {
      var h = readHeader( frames[i].path );
      if ( h == null || h.info == null )
         continue;
      var d = MasterFlat.describe( frames[i].path, h.keyword );
      // size and modified only key the cache; engine.masterDark reads them again and refuses a dark that has gone.
      try
      {
         var fi = new FileInfo( frames[i].path );
         d.size = fi.size;
         d.modified = fi.lastModified.toISOString();
      }
      catch ( e ) { d.size = null; d.modified = null; }
      d.source = "card";
      described.push( d );
   }
   return MasterFlat.darksOf( described );
};

/*
 * Do these darks suit these flats? `flats` and `darks` are described frames.
 * found is true when at least one flat has a dark under the usual rules
 * (chooseDark: exposure, both temperatures, binning, gain, two raw darks at
 * least). `used` are the darks that suit some flat, `exposures` theirs in
 * seconds, ascending.
 */
MasterFlat.cardDarkMatch = function( flats, darks )
{
   var used = Object.create( null ), usedList = [];
   var found = false;
   flats.forEach( function( flat )
   {
      var pick = MasterFlat.chooseDark( flat, darks );
      if ( pick == null )
         return;
      found = true;
      var paths = pick.kind == "master" ? [ pick.path ] : pick.paths;
      paths.forEach( function( p ) { used[p] = true; } );
   } );
   var exposures = [];
   darks.forEach( function( d )
   {
      if ( !( d.path in used ) )
         return;
      usedList.push( d );
      if ( d.exposure != null && exposures.indexOf( d.exposure ) < 0 )
         exposures.push( d.exposure );
   } );
   exposures.sort( function( a, b ) { return a - b; } );
   return { found: found, used: usedList, exposures: exposures };
};

/*
 * The card's darks against the night's flats, in one go: { darks, match }
 * with `darks` as cardDarks and `match` as cardDarkMatch. `darkFrames` are
 * scanCard's darks, `flatFrames` the night's candidate flats (anything with
 * a path); headers through `readHeader`, quietly.
 */
MasterFlat.readCardDarks = function( darkFrames, flatFrames, readHeader )
{
   var flats = [];
   flatFrames.forEach( function( f )
   {
      var h = readHeader( f.path );
      if ( h != null && h.info != null )
         flats.push( MasterFlat.describe( f.path, h.keyword ) );
   } );
   var darks = MasterFlat.cardDarks( darkFrames, readHeader );
   return { darks: darks, match: MasterFlat.cardDarkMatch( flats, darks ) };
};

/*
 * Where the flat-darks will come from, as AsiairNames.darkSource takes it:
 * the card when its darks were found (`match` from cardDarkMatch), else the
 * saved folder.
 */
MasterFlat.darkSourceOf = function( match, darksFolder )
{
   return match != null && match.found
      ? { card: true, count: match.used.length, exposures: match.exposures }
      : { darksFolder: darksFolder };
};

/*
 * What the import dialog shows of the darks controls, as plain data (no
 * widgets): `found` is cardDarkMatch's. With card darks the Folder... / Clear
 * button and label are hidden and the option needs no folder; without, the
 * folder is needed, exactly as before. The saved folder is not an input to
 * any change: it is only read here.
 */
MasterFlat.cardDarksUi = function( found, darksFolder, onlyMasterFlats, editable )
{
   var folder = darksFolder != null && String( darksFolder ).length > 0;
   var available = !!found || folder;
   return { found: !!found,
            darksVisible: !found,
            folderEnabled: !!editable,
            clearEnabled: !!editable && folder,
            optionAvailable: !!editable && available,
            optionChecked: !!onlyMasterFlats && available };
};

/* ------------------------------------------------------------------------
 * Matching
 * ---------------------------------------------------------------------- */

MasterFlat.sameExposure = function( a, b )
{
   if ( a == null || b == null )
      return false;
   return Math.abs( a - b ) <= Math.max( MasterFlat.EXPOSURE_ABS, MasterFlat.EXPOSURE_REL * Math.max( a, b ) );
};

/* Equal, or not both stated -- a field one side does not state cannot contradict. */
MasterFlat.compatible = function( a, b, tolerance )
{
   return a == null || b == null || Math.abs( a - b ) <= tolerance;
};

/*
 * Can this dark be this flat's flat-dark? The exposure must be stated on
 * both and agree, and so must the temperature (a missing one is no match);
 * binning and gain must agree where both state them.
 */
MasterFlat.darkSuits = function( flat, dark )
{
   return MasterFlat.sameExposure( flat.exposure, dark.exposure ) &&
          MasterFlat.compatible( flat.binning, dark.binning, 0 ) &&
          MasterFlat.compatible( flat.gain, dark.gain, MasterFlat.GAIN_TOLERANCE ) &&
          flat.temp != null && dark.temp != null &&
          Math.abs( flat.temp - dark.temp ) <= MasterFlat.TEMP_TOLERANCE;
};

function masterFlatClosest( x, y )
{
   return ( x == null || y == null ) ? 0 : Math.abs( x - y );
}

/*
 * The dark for a flat, or null.
 *
 * A master dark wins over raw darks, then the closest exposure, then the
 * closest temperature. Raw darks are grouped by what makes them one master
 * (exposure, binning, gain) and the best group
 * is chosen the same way, the larger one winning a tie.
 * Returns { kind: "master", path } or { kind: "raw", paths, key, exposure,
 * binning, gain, temp }.
 */
MasterFlat.chooseDark = function( flat, darks )
{
   var masters = [], groups = Object.create( null ), order = [];
   for ( var i = 0; i < darks.length; ++i )
   {
      var d = darks[i];
      if ( !MasterFlat.darkSuits( flat, d ) )
         continue;
      if ( d.kind == "master" )
         masters.push( d );
      else
      {
         var key = MasterFlat.rawGroupKey( d );
         if ( !( key in groups ) )
         {
            groups[key] = { kind: "raw", key: key, paths: [], exposure: d.exposure,
                            binning: d.binning, gain: d.gain, temp: null, temps: [] };
            order.push( key );
         }
         groups[key].paths.push( d.path );
         groups[key].temps.push( d.temp );
      }
   }
   for ( var gk in groups )
      groups[gk].temp = groups[gk].temps.reduce( function( a, t ) { return a + t; }, 0 ) / groups[gk].temps.length;

   function rank( x, y )
   {
      return masterFlatClosest( flat.exposure, x.exposure ) - masterFlatClosest( flat.exposure, y.exposure ) ||
             masterFlatClosest( flat.temp, x.temp ) - masterFlatClosest( flat.temp, y.temp );
   }

   if ( masters.length > 0 )
   {
      masters.sort( function( x, y ) { return rank( x, y ) || ( x.path < y.path ? -1 : x.path > y.path ? 1 : 0 ); } );
      return { kind: "master", path: masters[0].path };
   }

   var raws = order.map( function( k ) { return groups[k]; } )
                   .filter( function( g ) { return g.paths.length >= MasterFlat.MIN_DARKS; } );
   if ( raws.length == 0 )
      return null;
   raws.sort( function( x, y ) { return rank( x, y ) || ( y.paths.length - x.paths.length ) ||
                                         ( x.key < y.key ? -1 : x.key > y.key ? 1 : 0 ); } );
   raws[0].paths.sort();
   return raws[0];
};

/*
 * What makes raw darks one master: exposure (ms), binning, gain. Not the
 * temperature: a regulated sensor drifts a couple of degrees through a
 * session, and chooseDark has already kept only the darks within tolerance
 * of the flat.
 */
MasterFlat.rawGroupKey = function( d )
{
   function f( v, scale ) { return v == null ? "-" : String( Math.round( v * scale ) ); }
   return "e" + f( d.exposure, 1000 ) + "b" + f( d.binning, 1 ) + "g" + f( d.gain, 1 );
};

/* ------------------------------------------------------------------------
 * Names, rejection
 * ---------------------------------------------------------------------- */

MasterFlat.safeName = function( text )
{
   var s = String( text == null ? "" : text ).replace( /[^A-Za-z0-9._-]+/g, "_" ).replace( /^[_.]+|[_.]+$/g, "" );
   return s.length > 0 ? s : "unknown";
};

MasterFlat.fileName = function( filter, binning )
{
   return "masterFlat_" + MasterFlat.safeName( filter ) +
          ( binning != null ? "_bin" + binning : "" ) + ".xisf";
};

/*
 * The rejection suited to a stack of this many flats. Flats are shot at
 * the same signal, so what is rejected is dust motes' neighbours -- stars,
 * satellites, hot pixels -- not a spread of values.
 *   8 or more   Winsorized sigma clipping
 *   3 to 7      percentile clipping (too few frames for a sigma)
 *   2           none
 */
MasterFlat.rejectionFor = function( count )
{
   if ( count >= 8 ) return { method: "winsorized", sigmaLow: 4.0, sigmaHigh: 3.0 };
   if ( count >= 3 ) return { method: "percentile", pcLow: 0.2, pcHigh: 0.1 };
   return { method: "none" };
};

/* The integration of a master dark: plain average, rejection by count. */
MasterFlat.darkIntegrationSpec = function( count )
{
   return { combination: "average", normalization: "none", rejection: MasterFlat.rejectionFor( count ) };
};

/* The integration of calibrated flats: average, multiplicative normalisation. */
MasterFlat.flatIntegrationSpec = function( count )
{
   return { combination: "average", normalization: "multiplicative",
            rejectionNormalization: "equalizeFluxes", rejection: MasterFlat.rejectionFor( count ) };
};

/* Where a raw-dark master is cached: a subfolder, so Cache.clear (files only) leaves it. */
MasterFlat.darkCachePath = function( group, sources, cacheDir )
{
   var parts = [ "masterDark", group.key ];
   for ( var i = 0; i < sources.length; ++i )
      parts.push( sources[i].path + "|" + sources[i].size + "|" + sources[i].modified );
   return cacheDir + "/master-darks/masterDark_" + group.key + "_" + Cache.hash( parts.join( "\n" ) ).substring( 0, 16 ) + ".xisf";
};

/* ------------------------------------------------------------------------
 * The plan
 * ---------------------------------------------------------------------- */

/*
 * One job per filter and binning of the night.
 *
 * `flats` are described frames ({ path, filter, binning, exposure, gain,
 * temp, raw }) where `path` is the XISF copy in <dest>/Flat and `raw` is
 * that same path (kept for the report). A job's flats are split by exposure
 * (and gain): each part is calibrated with its own dark, then all are
 * integrated together.
 *
 * A job is ready only if EVERY part has a dark; otherwise it is skipped
 * with a reason and its raw flats stay. `darks` come from darksOf; `darksWhere`
 * ("on the ASIAIR card") says where they were looked for in that reason.
 */
MasterFlat.buildPlan = function( flats, darks, destFlatDir, darksWhere )
{
   var byJob = Object.create( null ), order = [], seen = Object.create( null );
   for ( var i = 0; i < flats.length; ++i )
   {
      var f = flats[i];
      // The same copy listed twice is one flat (and is deleted once).
      if ( f.path in seen )
         continue;
      seen[f.path] = true;
      var key = String( f.filter == null ? "" : f.filter ) + "\u0001" + ( f.binning == null ? "" : f.binning );
      if ( !( key in byJob ) )
      {
         byJob[key] = { filter: f.filter, binning: f.binning, flats: [] };
         order.push( key );
      }
      byJob[key].flats.push( f );
   }

   var jobs = order.map( function( k ) { return byJob[k]; } );
   jobs.sort( function( a, b )
   {
      var x = String( a.filter ).toLowerCase(), y = String( b.filter ).toLowerCase();
      return x < y ? -1 : x > y ? 1 : ( ( a.binning || 0 ) - ( b.binning || 0 ) );
   } );

   // A filter shot at two binnings needs the binning in the name.
   var perFilter = Object.create( null );
   jobs.forEach( function( j ) { var k = String( j.filter ).toLowerCase(); perFilter[k] = ( perFilter[k] || 0 ) + 1; } );
   var names = Object.create( null );
   jobs.forEach( function( j )
   {
      var base = MasterFlat.fileName( j.filter, perFilter[String( j.filter ).toLowerCase()] > 1 ? j.binning : null );
      var n = base, c = 1;
      while ( n.toLowerCase() in names )
         n = base.replace( /\.xisf$/, "" ) + "_" + ( ++c ) + ".xisf";
      names[n.toLowerCase()] = true;
      j.name = n;
      j.master = destFlatDir + "/" + n;

      j.parts = [];
      j.flats.sort( function( a, b ) { return a.path < b.path ? -1 : a.path > b.path ? 1 : 0; } );
      j.flats.forEach( function( f )
      {
         for ( var p = 0; p < j.parts.length; ++p )
            if ( MasterFlat.sameExposure( j.parts[p].exposure, f.exposure ) &&
                 MasterFlat.compatible( j.parts[p].gain, f.gain, 0 ) &&
                 ( j.parts[p].exposure != null || f.exposure == null ) )
            {
               j.parts[p].flats.push( f );
               return;
            }
         j.parts.push( { exposure: f.exposure, gain: f.gain, flats: [ f ], dark: null } );
      } );

      j.skip = null;
      if ( j.flats.length < MasterFlat.MIN_FLATS )
         j.skip = "only " + j.flats.length + " flat(s); at least " + MasterFlat.MIN_FLATS + " are needed";
      for ( var p = 0; p < j.parts.length && j.skip == null; ++p )
      {
         var part = j.parts[p];
         part.dark = MasterFlat.chooseDark( part.flats[0], darks );
         if ( part.dark == null )
            j.skip = "no matching dark for " + ( part.exposure == null ? "flats with no exposure time"
                                                                       : "the " + part.exposure + " s flats" ) +
                     MasterFlat.darkShortfall( part.flats[0], darks, darksWhere );
      }
   } );
   return jobs;
};

/*
 * Why no dark suited: " (no darks found in the folder)" or what the folder
 * does hold, so a wrong folder, a missing temperature or another exposure
 * shows at a glance. Appended to the skip reason.
 */
MasterFlat.darkShortfall = function( flat, darks, where )
{
   if ( darks.length == 0 )
      return " (no darks found " + ( where || "in the darks folder or below it" ) + ")";
   var seen = Object.create( null ), list = [];
   darks.forEach( function( d )
   {
      var e = d.exposure == null ? "no exposure" : String( Math.round( d.exposure * 1000 ) / 1000 ) + " s";
      if ( !( e in seen ) ) { seen[e] = true; list.push( e ); }
   } );
   var temp = flat.temp == null ? "the flats state no temperature" : "flats at " + flat.temp.toFixed( 1 ) + " C";
   return " (" + temp + "; darks found: " + list.slice( 0, 8 ).join( ", " ) + ( list.length > 8 ? ", ..." : "" ) + ")";
};

/* ------------------------------------------------------------------------
 * Verification and deletion rules
 * ---------------------------------------------------------------------- */

/*
 * What is wrong with a written master, or null. `got` and `want` are
 * { width, height, keyword( name ), mean } -- the master, and one flat it
 * came from. Geometry must match; FILTER must be the job's; IMAGETYP must
 * say flat; and the image must be real: a finite, positive mean (an empty
 * or NaN integration is not a master).
 */
MasterFlat.verifyMaster = function( got, want, filter )
{
   if ( got == null || got.width == null )
      return "the master could not be read";
   if ( got.width != want.width || got.height != want.height )
      return "geometry changed";
   var f = got.keyword( "FILTER" );
   if ( filter != null && f != filter )
      return "FILTER is " + ( f == null ? "missing" : f ) + ", expected " + filter;
   var t = got.keyword( "IMAGETYP" );
   if ( t == null || !/flat/i.test( t ) )
      return "IMAGETYP is " + ( t == null ? "missing" : t ) + ", expected a master flat";
   if ( typeof got.mean != "number" || !isFinite( got.mean ) || got.mean <= 0 )
      return "the master is empty or not finite";
   return null;
};

/* A path with repeated and trailing slashes folded away. */
MasterFlat.cleanPath = function( path )
{
   var p = String( path ).replace( /\/{2,}/g, "/" );
   return p.length > 1 ? p.replace( /\/+$/, "" ) : p;
};

/* Check if x is inside root directory (case-insensitive). */
function masterFlatPathInside( x, root )
{
   x = x.toLowerCase(); root = root.toLowerCase();
   return x == root || root == "/" || x.substring( 0, root.length + 1 ) == root + "/";
}

/* Validate path structure: inside dir, no parent refs, valid basename. */
function masterFlatValidatePathStructure( p, dir )
{
   if ( /(^|\/)\.\.(\/|$)/.test( p ) )
      return null;
   if ( p.substring( 0, dir.length + 1 ) != dir + "/" || p.indexOf( "/", dir.length + 1 ) >= 0 )
      return null;
   var base = p.substring( dir.length + 1 );
   if ( base.length == 0 || /^masterFlat_/i.test( base ) )
      return null;
   return base;
}

/* Check if path is excluded by master or card boundaries. */
function masterFlatIsExcludedPath( p, masterPath, card )
{
   if ( masterPath != null && p.toLowerCase() == MasterFlat.cleanPath( masterPath ).toLowerCase() )
      return true;
   if ( card != null && masterFlatPathInside( p, card ) )
      return true;
   return false;
}

/*
 * Find an available master file name when collisions exist. Updates
 * master, name if necessary, and returns a note if renamed.
 */
function masterFlatFindAvailableName( job, engine )
{
   var master = job.master, name = job.name, dir = master.substring( 0, master.lastIndexOf( "/" ) );
   for ( var n = 2; engine.exists( master ); ++n )
   {
      name = job.name.replace( /\.xisf$/, "" ) + "_" + n + ".xisf";
      master = dir + "/" + name;
   }
   var note = master != job.master ? job.name + " already exists and is kept; the new master is " + name : null;
   return { master: master, name: name, note: note };
}

/*
 * Delete raw flats after master verification. Returns count removed.
 * Logs undeletable flats to notes.
 */
function masterFlatDeleteRawFlats( job, opts, engine, master, notes )
{
   var removed = 0;
   job.flats.forEach( function( f )
   {
      if ( !MasterFlat.mayDelete( f.path, opts.destFlatDir, master, opts.cardRoot, engine.resolve ) )
      {
         notes.push( "not deleted (not a plain file in the destination Flat folder): " + f.path );
         return;
      }
      var gone = false;
      try { gone = engine.remove( f.path ); } catch ( e2 ) {}
      if ( gone || !engine.exists( f.path ) ) ++removed;
      else notes.push( "could not delete " + f.path );
   } );
   return removed;
}

/*
 * Check if a job should be skipped before processing. Returns a reason
 * if it should be skipped (stopped, has skip, or no resolve), else null.
 */
function masterFlatCheckJobSkip( job, stopped, engine )
{
   if ( stopped() )
      return "cancelled";
   if ( job.skip != null )
      return job.skip;
   if ( typeof engine.resolve != "function" )
      return "the engine cannot resolve paths, so nothing is deleted";
   return null;
}

/*
 * Process parts: get dark paths, calibrate flats. Returns { calibrated, failure }
 * where failure is null on success, or an error reason string.
 */
function masterFlatProcessParts( job, engine, tmpDir, stopped )
{
   var calibrated = [], failure = null;
   for ( var p = 0; p < job.parts.length && failure == null; ++p )
   {
      if ( stopped() ) { failure = "cancelled"; break; }
      var part = job.parts[p], darkPath;
      if ( part.dark.kind == "master" )
         darkPath = part.dark.path;
      else
      {
         var md = engine.masterDark( part.dark );
         if ( !md.ok ) { failure = "master dark failed: " + md.reason; break; }
         darkPath = md.path;
      }
      var cal = engine.calibrate( part.flats.map( function( f ) { return f.path; } ), darkPath, tmpDir );
      if ( !cal.ok ) { failure = "calibration failed: " + cal.reason; break; }
      calibrated = calibrated.concat( cal.paths );
   }
   return { calibrated: calibrated, failure: failure };
}

/*
 * Verify and publish master: integrate calibrated flats, verify result,
 * and move into place. Returns failure reason or null on success.
 */
function masterFlatVerifyAndPublish( job, engine, partialPath, masterPath, calibrated, stopped )
{
   var failure = null;
   var res = engine.integrate( calibrated, MasterFlat.flatIntegrationSpec( calibrated.length ), partialPath, job );
   if ( !res.ok ) return "integration failed: " + res.reason;

   if ( stopped() ) return "cancelled";

   var bad = engine.verify( partialPath, job );
   if ( bad != null ) return "master failed verification: " + bad;

   if ( stopped() ) return "cancelled";

   var published = false;
   try { published = engine.publish( partialPath, masterPath ) === true; } catch ( e5 ) {}
   if ( !published ) return "could not move the master into place";

   return null;
}

/*
 * Record job outcome: success (made) or failure (kept).
 */
function masterFlatRecordJobResult( job, out, opts, engine, master, failed, reason )
{
   if ( failed )
   {
      out.kept.push( { filter: job.filter, binning: job.binning, reason: reason, flats: job.flats.length } );
      return;
   }
   var removed = masterFlatDeleteRawFlats( job, opts, engine, master, out.notes );
   var name = master.substring( master.lastIndexOf( "/" ) + 1 );
   out.made.push( { name: name, filter: job.filter, binning: job.binning, flats: job.flats.length, removed: removed } );
}

/*
 * Execute a single job's processing: handle try/catch/finally for temp
 * directory and partial file cleanup.  Modifies out in place.
 */
function masterFlatExecuteOneJob( job, engine, opts, out, stopped )
{
   var nameInfo = masterFlatFindAvailableName( job, engine );
   var master = nameInfo.master, name = nameInfo.name;
   if ( nameInfo.note != null ) out.notes.push( nameInfo.note );
   var dir = master.substring( 0, master.lastIndexOf( "/" ) );
   var partial = dir + "/.partial_" + name;

   var tmp = null;
   try
   {
      tmp = engine.tempDir( job );
      var result = masterFlatProcessJobParts( job, engine, { path: partial, master: master, tmp: tmp }, stopped );
      masterFlatRecordJobResult( job, out, opts, engine, master, result.failure != null, result.failure );
   }
   catch ( e )
   {
      if ( Util.isCancel( e ) )
         out.cancelled = true;
      else
         e = "error: " + ( e && e.message !== undefined ? e.message : e );
      masterFlatRecordJobResult( job, out, opts, engine, master, true, e === true ? "cancelled" : e );
   }
   finally
   {
      try { if ( partial != master && engine.exists( partial ) ) engine.remove( partial ); } catch ( e6 ) {}
      if ( tmp != null ) try { engine.removeDir( tmp ); } catch ( e4 ) {}
   }
}

/*
 * Process all jobs in sequence. For each: check skip, find names, process,
 * and record results. Modifies out in place.
 */
function masterFlatProcessAllJobs( jobs, engine, opts, out, stopped )
{
   for ( var j = 0; j < jobs.length; ++j )
   {
      var job = jobs[j];
      if ( opts.onProgress ) opts.onProgress( j, jobs.length,
         job.filter + ( job.binning != null ? " (bin " + job.binning + ")" : "" ) );

      var skipReason = masterFlatCheckJobSkip( job, stopped, engine );
      if ( skipReason != null )
      {
         out.kept.push( { filter: job.filter, binning: job.binning, reason: skipReason, flats: job.flats.length } );
         continue;
      }

      masterFlatExecuteOneJob( job, engine, opts, out, stopped );
   }
}

/*
 * Process job parts: calibrate, integrate, verify, publish. Called within
 * a try-catch that manages tmp. Returns failure reason or null on success.
 * Modifies calibrated array in place; sets partial on success.
 */
function masterFlatProcessJobParts( job, engine, partial, stopped )
{
   var parts = masterFlatProcessParts( job, engine, partial.tmp, stopped );
   var calibrated = parts.calibrated, failure = parts.failure;

   if ( failure == null && stopped() ) failure = "cancelled";
   if ( failure == null && calibrated.length != job.flats.length )
      failure = "calibration produced " + calibrated.length + " of " + job.flats.length + " flat(s)";
   if ( failure == null )
      failure = masterFlatVerifyAndPublish( job, engine, partial.path, partial.master, calibrated, stopped );
   return { failure: failure, calibrated: calibrated };
}

/* Verify resolved paths match expected structure and are not inside card. */
function masterFlatVerifyResolvedPaths( p, dir, base, card, resolve )
{
   var real = null, realDir = null, realCard = null;
   try
   {
      real = MasterFlat.cleanPath( resolve( p ) );
      realDir = MasterFlat.cleanPath( resolve( dir ) );
      if ( card != null ) realCard = MasterFlat.cleanPath( resolve( card ) );
   }
   catch ( e ) { return false; }
   if ( real == null || realDir == null || real != realDir + "/" + base )
      return false;
   if ( realCard != null && ( masterFlatPathInside( real, realCard ) || masterFlatPathInside( realDir, realCard ) ) )
      return false;
   return true;
}

/*
 * May this path be deleted as a raw flat? Only a file directly inside the
 * destination's Flat folder, never a master (the job's, or any masterFlat_*
 * -- a master from an earlier import survives), never anything inside the
 * card. Refusals are case-insensitive (macOS volumes are), so the answer
 * is the cautious one on either kind of disk.
 *
 * `resolve( path )`, when given, is the real path with symlinks followed
 * (File.fullPath). Then the file must itself resolve to its own name inside
 * the resolved Flat folder -- a symlinked flat, or a Flat folder that is
 * itself a link onto the card, is refused, never followed.
 */
MasterFlat.mayDelete = function( path, destFlatDir, masterPath, cardRoot, resolve )
{
   if ( path == null || destFlatDir == null )
      return false;
   var p = MasterFlat.cleanPath( path ), dir = MasterFlat.cleanPath( destFlatDir );
   var card = ( cardRoot != null && String( cardRoot ).length > 0 ) ? MasterFlat.cleanPath( cardRoot ) : null;

   var base = masterFlatValidatePathStructure( p, dir );
   if ( base == null )
      return false;
   if ( masterFlatIsExcludedPath( p, masterPath, card ) )
      return false;

   if ( typeof resolve == "function" && !masterFlatVerifyResolvedPaths( p, dir, base, card, resolve ) )
      return false;
   return true;
};

/* ------------------------------------------------------------------------
 * Running a plan
 * ---------------------------------------------------------------------- */

/*
 * Run a plan through `engine`, filter by filter.
 *
 * engine:
 *   masterDark( group )                      -> { ok, path, reason }  (cached)
 *   calibrate( paths, darkPath, outDir )     -> { ok, paths, reason }
 *   integrate( paths, spec, partialPath, job ) -> { ok, reason }  (writes partialPath)
 *   verify( partialPath, job )               -> null or a problem
 *   publish( partialPath, finalPath )        -> bool  (rename; false, and no
 *                                               overwrite, if finalPath exists)
 *   resolve( path )                          -> real path, symlinks followed (REQUIRED:
 *                                               without it every filter is kept)
 *   removePartials( dir )                    -> optional; removes stale .partial_masterFlat_*.xisf
 *   remove( path ) -> bool,  removeDir( dir ) -> bool,  exists( path ) -> bool
 *   tempDir( job ) -> a fresh folder for the job's calibrated flats
 *
 * opts: { destFlatDir, cardRoot, shouldStop(), onProgress( done, total, text ) }
 *
 * Returns { made: [ { name, filter, flats, removed } ], kept: [ { filter,
 * reason, flats } ], cancelled, notes: [] }. A filter is in `made` only
 * after its master verified; its raw flats are then removed and any that
 * could not be are named in `notes`. Anything else is in `kept`, raw flats
 * untouched.
 */
MasterFlat.execute = function( jobs, engine, opts )
{
   opts = opts || {};
   var out = { made: [], kept: [], cancelled: false, notes: [] };

   function stopped()
   {
      if ( opts.shouldStop && opts.shouldStop() )
         out.cancelled = true;
      return out.cancelled;
   }

   // A temporary left by a run that died: ours only (".partial_masterFlat_*.xisf"), only directly in the Flat folder.
   if ( typeof engine.removePartials == "function" && opts.destFlatDir != null )
      try { engine.removePartials( opts.destFlatDir ); } catch ( e7 ) {}

   masterFlatProcessAllJobs( jobs, engine, opts, out, stopped );

   if ( opts.onProgress ) opts.onProgress( jobs.length, jobs.length, "" );
   return out;
};

/* What the import says about the master flats, for the closing message. */
MasterFlat.report = function( result )
{
   var lines = [];
   result.made.forEach( function( m )
   {
      lines.push( "Master flat " + m.name + " from " + m.flats + " flat(s); " + m.removed + " raw flat(s) deleted." );
   } );
   result.kept.forEach( function( k )
   {
      lines.push( "Raw flats kept for " + k.filter + ( k.binning != null ? " (bin " + k.binning + ")" : "" ) +
                  " (" + k.flats + "): " + k.reason + "." );
   } );
   result.notes.forEach( function( n ) { lines.push( n ); } );
   if ( result.cancelled )
      lines.push( "Cancelled; the remaining filters keep their raw flats." );
   return lines.join( "\n" );
};

/* ------------------------------------------------------------------------
 * The import log
 * ---------------------------------------------------------------------- */

function masterFlatNum( v, digits ) { return v == null ? "-" : String( Math.round( v * digits ) / digits ); }

/* One line describing a frame: what the matching looked at. */
MasterFlat.frameLine = function( f )
{
   return f.name + "  filter=" + ( f.filter == null ? "-" : String( f.filter ).replace( /^'|'$/g, "" ).trim() ) +
          " exposure=" + masterFlatNum( f.exposure, 1000 ) + " bin=" + masterFlatNum( f.binning, 1 ) +
          " gain=" + masterFlatNum( f.gain, 10 ) + " temp=" + masterFlatNum( f.temp, 10 ) +
          ( f.kind != null ? " kind=" + f.kind : "" );
};

/* The darks that were found, one line per exposure/binning/gain, then the plan, one line per part. */
MasterFlat.darkSummaryLines = function( darks )
{
   var groups = Object.create( null ), order = [];
   darks.forEach( function( d )
   {
      var k = d.kind + " exposure=" + masterFlatNum( d.exposure, 1000 ) + " bin=" + masterFlatNum( d.binning, 1 ) +
              " gain=" + masterFlatNum( d.gain, 10 );
      if ( !( k in groups ) ) { groups[k] = { n: 0, lo: null, hi: null }; order.push( k ); }
      var g = groups[k];
      ++g.n;
      if ( d.temp != null )
      {
         g.lo = g.lo == null ? d.temp : Math.min( g.lo, d.temp );
         g.hi = g.hi == null ? d.temp : Math.max( g.hi, d.temp );
      }
   } );
   return order.map( function( k )
   {
      var g = groups[k];
      return "  " + g.n + " x " + k + " temp=" + ( g.lo == null ? "-" : masterFlatNum( g.lo, 10 ) + ".." + masterFlatNum( g.hi, 10 ) );
   } );
};

MasterFlat.planLines = function( jobs )
{
   var out = [];
   jobs.forEach( function( j )
   {
      var head = "  " + j.filter + ( j.binning != null ? " bin " + j.binning : "" ) + ": " + j.flats.length + " flat(s)";
      if ( j.skip )
         { out.push( head + " -> SKIPPED, " + j.skip ); return; }
      out.push( head );
      j.parts.forEach( function( p )
      {
         var d = p.dark;
         out.push( "    " + p.flats.length + " flat(s) of " + masterFlatNum( p.exposure, 1000 ) + " s -> " +
                   ( d == null ? "no dark"
                     : d.kind == "master" ? "master dark " + d.path
                     : "integrate " + d.paths.length + " raw dark(s), exposure " + masterFlatNum( d.exposure, 1000 ) +
                       " s, mean temp " + masterFlatNum( d.temp, 10 ) ) );
      } );
   } );
   return out;
};

/* ------------------------------------------------------------------------
 * The PixInsight engine
 * ---------------------------------------------------------------------- */

/*
 * Frames in a folder and its subfolders (to MasterFlat.SCAN_DEPTH levels),
 * described. Only files with "dark" in the name are read, so a deep archive
 * of lights costs nothing; symbolic links to folders are not followed. A
 * header that cannot be read without opening the image is skipped (never an
 * ImageWindow.open: a missing or unreadable file raises a modal box).
 */
MasterFlat.SCAN_DEPTH = 4;
MasterFlat.scanFolder = function( dir, readHeader, depth )
{
   var out = [];
   if ( dir == null || dir.length == 0 || !File.directoryExists( dir ) )
      return out;
   var base = dir.replace( /\/+$/, "" );
   var entries = Util.findEntries( base + "/*" );
   for ( var i = 0; i < entries.length; ++i )
   {
      var e = entries[i], path = base + "/" + e.name;
      if ( e.isDirectory )
      {
         if ( !e.isSymbolicLink && ( depth || 0 ) < MasterFlat.SCAN_DEPTH )
            out = out.concat( MasterFlat.scanFolder( path, readHeader, ( depth || 0 ) + 1 ) );
         continue;
      }
      if ( !MasterFlat.IMAGE_EXTENSION.test( e.name ) || !/dark/i.test( e.name ) )
         continue;
      var h = readHeader( path );
      if ( h == null || h.info == null )
         continue;
      var d = MasterFlat.describe( path, h.keyword );
      d.size = e.size;
      d.modified = ( new FileInfo( path ) ).lastModified.toISOString();
      out.push( d );
   }
   return out;
};

/*
 * `readHeader( path )` -> { info, keyword } that never raises a modal box
 * (FrameSelector.quietHeader); `shouldStop()` is polled between steps.
 */
MasterFlat.engine = function( readHeader, cacheDir )
{
   function open( path )
   {
      var ws = ImageWindow.open( path );
      return ws.length > 0 ? ws[0] : null;
   }
   function ids()
   {
      var s = {};
      ImageWindow.windows.forEach( function( w ) { s[w.mainView.id] = true; } );
      return s;
   }
   function closeNew( before )
   {
      ImageWindow.windows.forEach( function( w )
      {
         if ( !( w.mainView.id in before ) )
            try { w.forceClose(); } catch ( e ) {}
      } );
   }
   function integration( paths, spec, outPath, header )
   {
      var before = ids();
      try
      {
         var P = new ImageIntegration;
         P.images = paths.map( function( p ) { return [ true, p, "", "" ]; } );
         P.inputHints = ""; P.weightMode = ImageIntegration.DontCare;
         P.combination = ImageIntegration.Average;
         P.normalization = spec.normalization == "multiplicative"
            ? ImageIntegration.Multiplicative : ImageIntegration.NoNormalization;
         P.rejectionNormalization = spec.rejectionNormalization == "equalizeFluxes"
            ? ImageIntegration.EqualizeFluxes : ImageIntegration.Scale;
         var r = spec.rejection;
         P.rejection = r.method == "winsorized" ? ImageIntegration.WinsorizedSigmaClip
                     : r.method == "percentile" ? ImageIntegration.PercentileClip
                     : ImageIntegration.NoRejection;
         if ( r.method == "winsorized" ) { P.sigmaLow = r.sigmaLow; P.sigmaHigh = r.sigmaHigh; }
         if ( r.method == "percentile" ) { P.pcClipLow = r.pcLow; P.pcClipHigh = r.pcHigh; }
         P.generateRejectionMaps = false;
         P.generateIntegratedImage = true;
         P.generateDrizzleData = false;
         P.evaluateSNR = false;
         P.noGUIMessages = true;
         P.useCache = false;
         P.closePreviousImages = false;
         if ( !P.executeGlobal() )
            return { ok: false, reason: "ImageIntegration did not run" };

         var win = null;
         ImageWindow.windows.forEach( function( w )
         {
            if ( !( w.mainView.id in before ) && win == null && !/^rejection|^slope/i.test( w.mainView.id ) )
               win = w;
         } );
         if ( win == null )
            return { ok: false, reason: "no integrated image" };

         if ( header != null )
         {
            var kept = win.keywords.filter( function( k )
               { return [ "FILTER", "IMAGETYP", "XBINNING", "YBINNING", "EXPTIME", "GAIN", "CCD-TEMP", "DATE-OBS" ].indexOf( k.name ) < 0; } );
            var add = [];
            Object.keys( header ).forEach( function( n )
               { if ( header[n] != null ) add.push( new FITSKeyword( n, String( header[n] ), "" ) ); } );
            win.keywords = kept.concat( add );
         }
         Util.ensureDirectory( outPath.substring( 0, outPath.lastIndexOf( "/" ) ) );
         // outPath is always a temporary (a ".partial_" name or a cache file), never a user's master.
         if ( File.exists( outPath ) )
            File.remove( outPath );
         if ( !win.saveAs( outPath, false, false, false, false ) )
            return { ok: false, reason: "could not write " + outPath };
         return { ok: true, reason: "" };
      }
      finally { closeNew( before ); }
   }
   function sizeOf( path )
   {
      var h = readHeader( path );
      return h && h.info ? h : null;
   }
   /* The mean of the image in a file, read without opening a window; null when it cannot be. */
   function meanOf( path )
   {
      var inst = null;
      try
      {
         inst = new FileFormatInstance( new FileFormat( File.extractExtension( path ), true, false ) );
         var d = inst.open( path, "verbosity 0" );
         if ( d == null || d.length == 0 )
            return null;
         var img = new Image;
         if ( !inst.readImage( img ) )
            return null;
         var m = img.mean();
         img.free();
         return m;
      }
      catch ( e ) { return null; }
      finally { try { if ( inst != null ) inst.close(); } catch ( e1 ) {} }
   }
   function move( from, to )
   {
      if ( File.exists( to ) )
         return false;
      try { File.move( from, to ); } catch ( e ) { return false; }
      return File.exists( to ) && !File.exists( from );
   }

   return {
      exists: function( p ) { return File.exists( p ); },
      remove: function( p ) { try { File.remove( p ); } catch ( e ) {} return !File.exists( p ); },
      removeDir: function( d )
      {
         try
         {
            Util.findEntries( d + "/*", true ).forEach( function( e )
               { if ( !e.isDirectory ) try { File.remove( d + "/" + e.name ); } catch ( e1 ) {} } );
            File.removeDirectory( d );
         }
         catch ( e2 ) {}
         return !File.directoryExists( d );
      },
      tempDir: function( job )
      {
         var d = cacheDir + "/master-flat-work/" + MasterFlat.safeName( job.name.replace( /\.xisf$/, "" ) );
         if ( File.directoryExists( d ) ) this.removeDir( d );
         Util.ensureDirectory( d );
         return d;
      },
      calibrate: function( paths, darkPath, outDir )
      {
         var before = ids();
         try
         {
            var P = new ImageCalibration;
            P.targetFrames = paths.map( function( p ) { return [ true, p ]; } );
            P.masterBiasEnabled = false;
            P.masterDarkEnabled = true;
            P.masterDarkPath = darkPath;
            P.masterFlatEnabled = false;
            P.calibrateBias = false;
            P.calibrateDark = false;       // the flat-dark is dark signal as it is
            P.calibrateFlat = false;
            P.optimizeDarks = false;
            P.evaluateNoise = false;
            P.outputDirectory = outDir;
            P.outputExtension = ".xisf";
            P.outputPrefix = "";
            P.outputPostfix = "_c";
            P.outputSampleFormat = ImageCalibration.f32;
            P.overwriteExistingFiles = true;
            P.onError = ImageCalibration.Abort;
            P.noGUIMessages = true;
            if ( !P.executeGlobal() )
               return { ok: false, reason: "ImageCalibration did not run" };
            var made = [];
            for ( var i = 0; i < paths.length; ++i )
            {
               var base = paths[i].split( "/" ).pop().replace( /\.[^.]*$/, "" );
               var out = outDir + "/" + base + "_c.xisf";
               if ( File.exists( out ) ) made.push( out );
            }
            if ( made.length != paths.length )
               return { ok: false, reason: made.length + " of " + paths.length + " flats calibrated" };
            return { ok: true, paths: made, reason: "" };
         }
         finally { closeNew( before ); }
      },
      masterDark: function( group )
      {
         // A dark that has gone (a card pulled out mid-run) fails the filter, which keeps its raw flats.
         for ( var g = 0; g < group.paths.length; ++g )
            if ( !File.exists( group.paths[g] ) )
               return { ok: false, reason: "a dark is no longer there: " + group.paths[g] };
         var sources = group.paths.map( function( p )
            { return { path: p, size: ( new FileInfo( p ) ).size, modified: ( new FileInfo( p ) ).lastModified.toISOString() }; } );
         var path = MasterFlat.darkCachePath( group, sources, cacheDir );
         if ( File.exists( path ) && sizeOf( path ) != null )
            return { ok: true, path: path, reason: "cached" };
         // Built under a temporary name and moved into the cache only when sound,
         // so a cache hit is always a complete master dark.
         var partial = path.substring( 0, path.lastIndexOf( "/" ) ) + "/.partial_" + path.substring( path.lastIndexOf( "/" ) + 1 );
         var made = integration( group.paths, MasterFlat.darkIntegrationSpec( group.paths.length ), partial,
                                 { IMAGETYP: "'Master Dark'" } );
         if ( !made.ok )
         {
            try { File.remove( partial ); } catch ( e0 ) {}
            return made;
         }
         var first = sizeOf( group.paths[0] ), got = sizeOf( partial ), mean = meanOf( partial );
         if ( got == null || first == null || got.info.width != first.info.width || got.info.height != first.info.height ||
              mean == null || !isFinite( mean ) )
         {
            try { File.remove( partial ); } catch ( e ) {}
            return { ok: false, reason: "the master dark does not match its darks" };
         }
         if ( File.exists( path ) )
            try { File.remove( path ); } catch ( e2 ) {}     // a cache file that failed its read above
         if ( !move( partial, path ) )
         {
            try { File.remove( partial ); } catch ( e3 ) {}
            return { ok: false, reason: "could not store the master dark in the cache" };
         }
         return { ok: true, path: path, reason: "" };
      },
      integrate: function( paths, spec, outPath, job )
      {
         var f = job.filter, f0 = job.flats[0];
         // EXPTIME only when every flat shared one (else it would state a lie).
         var header = { FILTER: f == null ? null : "'" + f + "'", IMAGETYP: "'Master Flat'",
                        XBINNING: job.binning, YBINNING: job.binning,
                        EXPTIME: job.parts.length == 1 ? f0.exposure : null,
                        GAIN: f0.gain, "CCD-TEMP": f0.temp };
         return integration( paths, spec, outPath, header );
      },
      /*
       * The real path. File.fullPath follows a symlinked folder but NOT a
       * symlinked file, so a link is detected from its directory entry and
       * reported as itself-with-a-marker, which never equals the expected
       * "<real folder>/<name>" -- mayDelete then refuses it.
       */
      removePartials: function( dir )
      {
         Util.findEntries( dir + "/*", true ).forEach( function( e )
         {
            if ( e.isFile && !e.isSymbolicLink && /^\.partial_masterFlat_.*\.xisf$/.test( e.name ) )
               try { File.remove( dir + "/" + e.name ); } catch ( e1 ) {}
         } );
      },
      resolve: function( p )
      {
         if ( File.directoryExists( p ) )
            return File.fullPath( p );
         var cut = p.lastIndexOf( "/" ), dir = p.substring( 0, cut ), name = p.substring( cut + 1 );
         var hit = Util.findEntries( dir + "/*", true ).filter( function( e ) { return e.name == name; } )[0];
         if ( hit != null && hit.isSymbolicLink )
            return "symlink:" + p;
         return File.fullPath( dir ) + "/" + name;
      },
      publish: function( partial, finalPath ) { return move( partial, finalPath ); },
      verify: function( path, job )
      {
         var got = readHeader( path ), src = readHeader( job.flats[0].path );
         if ( got == null || got.info == null || src == null || src.info == null )
            return "the master could not be read";
         return MasterFlat.verifyMaster( { width: got.info.width, height: got.info.height, keyword: got.keyword,
                                           mean: meanOf( path ) },
                                         { width: src.info.width, height: src.info.height },
                                         job.filter );
      }
   };
};
