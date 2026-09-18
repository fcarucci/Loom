#engine v8

#feature-id LoomSelfTest : Scripts > LoomSelfTest

#include <pjsr/DataType.jsh>
#include <pjsr/FrameStyle.jsh>
#include <pjsr/StdButton.jsh>
#include <pjsr/StdIcon.jsh>
#include <pjsr/TextAlign.jsh>

#include "lib/Util.js"
#include "lib/Cache.js"
#include "lib/Psb.js"
#include "lib/Steps.js"
#include "lib/Pipeline.js"
#include "lib/Update.js"
/*
 * UI.js is included so the dialogs can actually be CONSTRUCTED below.
 *
 * It was absent for a long time, and that absence had a cost: the suite
 * reported hundreds of passing assertions while the dialog would not open at
 * all. Two separate breakages reached the user that way -- once the Cancel
 * window, so a run had no progress display and no way to stop, and once the
 * main dialog. Both were constructor errors that no amount of pure-function
 * testing could see.
 */
#include "lib/UI.js"

#define RESULT_FILE "/tmp/agent-scratch/lhso-selftest.txt"

/*
 * This script's own directory, so the source-level checks below do not
 * carry a hardcoded personal path.
 */
var LOOM_DIR = File.extractDirectory( #__FILE__ );

var TESTS_RUN = 0;
var FAILURES = [];

/*
 * Silence the library's own logging for the duration of the tests.
 *
 * Functions like Steps.lookupFilterCurve narrate themselves, which is
 * wanted during a real run -- knowing whether a filter resolved by exact
 * or substring match matters -- and is pure noise here, where the only
 * output that means anything is the PASS/FAIL line. Restored in main() so
 * a crash still reports through the normal channels.
 */
var REAL_LOG = Util.log, REAL_WARN = Util.warn, REAL_OPERATION = Util.operation;
function silenceLogging()
{
   Util.log = function() {};
   Util.warn = function() {};
   Util.operation = function() {};
}
function restoreLogging()
{
   Util.log = REAL_LOG;
   Util.warn = REAL_WARN;
   Util.operation = REAL_OPERATION;
}

function check( name, actual, expected )
{
   TESTS_RUN++;
   var a = JSON.stringify( actual );
   var e = JSON.stringify( expected );
   if ( a != e )
      FAILURES.push( name + ": expected " + e + ", got " + a );
}

function runTests()
{
   // uniqueWindowId: no clash returns the bare base
   check( "uniqueWindowId no clash",
          Util.uniqueWindowId( "RGB", function( id ) { return false; } ),
          "RGB" );

   // uniqueWindowId: base taken returns RGB_1
   check( "uniqueWindowId one clash",
          Util.uniqueWindowId( "RGB", function( id ) { return id == "RGB"; } ),
          "RGB_1" );

   // uniqueWindowId: base and RGB_1 taken returns RGB_2
   check( "uniqueWindowId two clashes",
          Util.uniqueWindowId( "RGB", function( id ) {
             return id == "RGB" || id == "RGB_1";
          } ),
          "RGB_2" );

   // Registry tracks and forgets windows without needing real ones
   var reg = new Util.Registry;
   var fakeA = { id: "a", closed: false, forceClose: function() { this.closed = true; } };
   var fakeB = { id: "b", closed: false, forceClose: function() { this.closed = true; } };
   reg.add( fakeA );
   reg.add( fakeB );
   reg.forget( fakeB );
   reg.closeAll();
   check( "registry closes tracked window", fakeA.closed, true );
   check( "registry skips forgotten window", fakeB.closed, false );

   // centralRect: 60% of a 1000x500 frame, centred
   check( "centralRect 60pct",
          Util.centralRect( 1000, 500, 0.6 ),
          { x0: 200, y0: 100, x1: 800, y1: 400 } );

   // centralRect: odd dimensions floor consistently and stay in bounds
   var r = Util.centralRect( 101, 101, 0.6 );
   check( "centralRect odd in bounds", r.x0 >= 0 && r.x1 <= 101, true );
   check( "centralRect odd non-empty", r.x1 > r.x0, true );

   // minMedianKey picks the lowest
   check( "minMedianKey picks lowest",
          Util.minMedianKey( { H: 0.0042, S: 0.0011, O: 0.0033 } ),
          "S" );

   // minMedianKey with a single channel returns it
   check( "minMedianKey single",
          Util.minMedianKey( { H: 0.5 } ),
          "H" );

   // minMedianKey with nothing returns null
   check( "minMedianKey empty", Util.minMedianKey( {} ), null );

   check( "isBroadband L", Util.isBroadband( "L" ), true );
   check( "isBroadband H", Util.isBroadband( "H" ), false );

   // FITS values arrive quoted and padded
   var kw = [ { name: "FILTER", value: "'Ha      '" },
              { name: "TELESCOP", value: " 'FSQ-106' " },
              { name: "EMPTY", value: "'   '" } ];
   check( "keywordValue unquotes", Util.keywordValue( kw, "FILTER" ), "Ha" );
   check( "keywordValue trims outside quotes", Util.keywordValue( kw, "TELESCOP" ), "FSQ-106" );
   check( "keywordValue blank is null", Util.keywordValue( kw, "EMPTY" ), null );
   check( "keywordValue missing is null", Util.keywordValue( kw, "NOPE" ), null );

   // L plus a complete RGB set is valid
   check( "validate RGB only",
          Util.validateSelection( { L: "/a/L.xisf", R: "/a/R.xisf",
                                    G: "/a/G.xisf", B: "/a/B.xisf" } ),
          [] );

   // L plus narrowband, no RGB at all, is valid
   check( "validate narrowband only",
          Util.validateSelection( { L: "/a/L.xisf", H: "/a/H.xisf",
                                    O: "/a/O.xisf" } ),
          [] );

   // A missing S is normal HOO work, not an error
   check( "validate missing S is fine",
          Util.validateSelection( { L: "/a/L.xisf", H: "/a/H.xisf" } ),
          [] );

   // Both groups together is valid
   check( "validate both groups",
          Util.validateSelection( { L: "/a/L.xisf", R: "/a/R.xisf",
                                    G: "/a/G.xisf", B: "/a/B.xisf",
                                    H: "/a/H.xisf" } ),
          [] );

   // A partial RGB set cannot be combined
   check( "validate partial RGB",
          Util.validateSelection( { L: "/a/L.xisf", R: "/a/R.xisf",
                                    G: "/a/G.xisf" } ),
          [ "Incomplete RGB set: missing B. Supply all three or none." ] );

   // L is the registration reference and is always required
   check( "validate missing L",
          Util.validateSelection( { R: "/a/R.xisf", G: "/a/G.xisf",
                                    B: "/a/B.xisf" } ),
          [ "Missing required channel: L" ] );

   // L on its own has nothing to do
   check( "validate L alone",
          Util.validateSelection( { L: "/a/L.xisf" } ),
          [ "Nothing to do: supply R, G and B, or at least one of H, S, O" ] );

   // The same file used twice is a mistake worth catching early
   check( "validate duplicate path",
          Util.validateSelection( { L: "/a/L.xisf", R: "/a/X.xisf",
                                    G: "/a/X.xisf", B: "/a/B.xisf" } ),
          [ "Same file selected for R and G: /a/X.xisf" ] );

   // Availability checks use real process constructors
   check( "module check finds StarAlignment",
          Steps.moduleAvailable( "StarAlignment" ), true );
   check( "module check finds SPFC",
          Steps.moduleAvailable( "SpectrophotometricFluxCalibration" ), true );
   check( "module check finds MGC",
          Steps.moduleAvailable( "MultiscaleGradientCorrection" ), true );
   check( "module check finds GraXpert",
          Steps.moduleAvailable( "GraXpert" ), true );
   check( "module check rejects nonsense",
          Steps.moduleAvailable( "NotARealProcess" ), false );

   // lookupFilterCurve against the real filters.xspd library, per the
   // exact-then-length-gated-substring rule in Steps.lookupFilterCurve.
   var exact = Steps.lookupFilterCurve( "Antlia ALP-T" );
   check( "lookupFilterCurve exact match name",
          exact != null ? exact.name : null, "Antlia ALP-T" );

   var sub = Steps.lookupFilterCurve( "Skyglow" );
   check( "lookupFilterCurve substring match name",
          sub != null ? sub.name : null, "Baader Neodymiun Skyglow" );

   // Bare single-letter LHSO labels like "L" must NOT match: below the
   // 3-character floor and there is no exact "L" entry in filters.xspd.
   check( "lookupFilterCurve single-letter L is no-match",
          Steps.lookupFilterCurve( "L" ), null );

   check( "lookupFilterCurve absent name is no-match",
          Steps.lookupFilterCurve( "Definitely Not A Real Filter Name" ), null );

   // channelFromFilter: the owner's masters carry bare single letters
   check( "channelFromFilter L", Util.channelFromFilter( "L" ), "L" );
   check( "channelFromFilter H", Util.channelFromFilter( "H" ), "H" );
   check( "channelFromFilter O lower", Util.channelFromFilter( "o" ), "O" );
   // long forms
   check( "channelFromFilter Ha", Util.channelFromFilter( "Ha" ), "H" );
   check( "channelFromFilter H-alpha", Util.channelFromFilter( "H-alpha" ), "H" );
   check( "channelFromFilter SII", Util.channelFromFilter( "SII" ), "S" );
   check( "channelFromFilter OIII", Util.channelFromFilter( "OIII 3nm" ), "O" );
   check( "channelFromFilter Luminance", Util.channelFromFilter( "Luminance" ), "L" );
   check( "channelFromFilter Red", Util.channelFromFilter( "Red" ), "R" );
   // unrecognised and empty must be null, never a guess
   check( "channelFromFilter unknown", Util.channelFromFilter( "L-eXtreme" ), null );
   check( "channelFromFilter empty", Util.channelFromFilter( "" ), null );
   check( "channelFromFilter null", Util.channelFromFilter( null ), null );

   // validateSelection must accept channels supplied as VIEWS, not just files
   check( "validate views only",
          Util.validateSelection( {}, { L: "L", R: "R1", G: "G1", B: "B1" } ),
          [] );
   check( "validate mixed files and views",
          Util.validateSelection( { L: "/a/L.xisf" }, { H: "H", O: "O" } ),
          [] );
   check( "validate view L plus view narrowband",
          Util.validateSelection( {}, { L: "L", H: "H" } ),
          [] );
   check( "validate missing L when only views given",
          Util.validateSelection( {}, { R: "R1", G: "G1", B: "B1" } ),
          [ "Missing required channel: L" ] );

   // the selection list is remembered between runs
   check( "serializeEntries round trip",
          Util.deserializeEntries( Util.serializeEntries(
             [ { source: "view", ref: "L" },
               { source: "file", ref: "/a/b/master R.xisf" } ] ) ),
          [ { source: "view", ref: "L" },
            { source: "file", ref: "/a/b/master R.xisf" } ] );
   check( "serializeEntries empty", Util.serializeEntries( [] ), "" );
   check( "deserializeEntries empty", Util.deserializeEntries( "" ), [] );
   check( "deserializeEntries null", Util.deserializeEntries( null ), [] );
   check( "deserializeEntries junk ignored",
          Util.deserializeEntries( "garbage\nview\tL\nbogus\tX" ),
          [ { source: "view", ref: "L" } ] );

   // drizzle factor comes from XPIXSZ, not the filename
   check( "drizzleLabel native 3.76", Util.drizzleLabel( 3.76 ), "" );
   check( "drizzleLabel 2x", Util.drizzleLabel( 1.88 ), "2x" );
   check( "drizzleLabel 4x", Util.drizzleLabel( 0.94 ), "4x" );
   check( "drizzleLabel string value", Util.drizzleLabel( "1.88" ), "2x" );
   check( "drizzleLabel unreadable", Util.drizzleLabel( null ), "" );
   check( "drizzleLabel zero", Util.drizzleLabel( 0 ), "" );
   check( "drizzleLabel non-integer factor", Util.drizzleLabel( 2.5 ), "" );

   // the QE curve follows the camera in the image's INSTRUME keyword
   check( "qeCurve ASI2600MM Air", Util.qeCurveNameForCamera( "ZWO ASI2600MM Air" ),
          "Sony IMX411/455/461/533/571" );
   check( "qeCurve ASI2600MM Pro", Util.qeCurveNameForCamera( "ASI2600MM Pro" ),
          "Sony IMX411/455/461/533/571" );
   check( "qeCurve ASI1600", Util.qeCurveNameForCamera( "ZWO ASI1600MM-Cool" ),
          "Panasonic MN34230 (ASI1600MM)" );
   check( "qeCurve by sensor name", Util.qeCurveNameForCamera( "IMX455" ),
          "Sony IMX411/455/461/533/571" );
   check( "qeCurve unknown camera", Util.qeCurveNameForCamera( "Some Unknown Cam" ), null );
   check( "qeCurve null", Util.qeCurveNameForCamera( null ), null );
   check( "qeCurve empty", Util.qeCurveNameForCamera( "" ), null );

   // common-area crop: rectangle intersection
   check( "intersectRects identical",
          Util.intersectRects( [ {x0:0,y0:0,x1:10,y1:10}, {x0:0,y0:0,x1:10,y1:10} ] ),
          {x0:0,y0:0,x1:10,y1:10} );
   check( "intersectRects offset",
          Util.intersectRects( [ {x0:0,y0:0,x1:10,y1:10}, {x0:2,y0:3,x1:12,y1:9} ] ),
          {x0:2,y0:3,x1:10,y1:9} );
   check( "intersectRects three",
          Util.intersectRects( [ {x0:0,y0:0,x1:10,y1:10},
                                 {x0:1,y0:0,x1:10,y1:8},
                                 {x0:0,y0:2,x1:9,y1:10} ] ),
          {x0:1,y0:2,x1:9,y1:8} );
   check( "intersectRects disjoint", 
          Util.intersectRects( [ {x0:0,y0:0,x1:5,y1:5}, {x0:6,y0:6,x1:9,y1:9} ] ),
          null );
   check( "intersectRects empty", Util.intersectRects( [] ), null );

   // --- cache key derivation ---
   // stable regardless of key insertion order
   check( "paramsString sorts keys",
          Cache.paramsString( { b: 2, a: 1 } ),
          Cache.paramsString( { a: 1, b: 2 } ) );
   check( "paramsString null", Cache.paramsString( null ), "" );
   check( "paramsString nested object",
          Cache.paramsString( { x: [ 1, 2 ] } ), "x=[1,2]" );

   // the same inputs always give the same key
   check( "chainKey deterministic",
          Cache.chainKey( "abc", "mgc", { a: 1 } ),
          Cache.chainKey( "abc", "mgc", { a: 1 } ) );

   // ...and any change to source, stage or params changes it
   check( "chainKey differs on source",
          Cache.chainKey( "abc", "mgc", { a: 1 } ) ==
          Cache.chainKey( "xyz", "mgc", { a: 1 } ), false );
   check( "chainKey differs on stage",
          Cache.chainKey( "abc", "mgc", { a: 1 } ) ==
          Cache.chainKey( "abc", "spfc", { a: 1 } ), false );
   check( "chainKey differs on params",
          Cache.chainKey( "abc", "mgc", { a: 1 } ) ==
          Cache.chainKey( "abc", "mgc", { a: 2 } ), false );

   // a change early in the chain must invalidate everything after it
   var earlyA = Cache.chainKey( "src", "spfc", { filter: "Baader R" } );
   var earlyB = Cache.chainKey( "src", "spfc", { filter: "Baader G" } );
   check( "chain propagates invalidation",
          Cache.chainKey( earlyA, "mgc", {} ) ==
          Cache.chainKey( earlyB, "mgc", {} ), false );

   // hashes are hex sha1
   check( "hash is 40 hex chars", /^[0-9a-f]{40}$/.test( Cache.hash( "x" ) ), true );
   check( "hash deterministic", Cache.hash( "hello" ), Cache.hash( "hello" ) );

   // byte formatting
   check( "formatBytes bytes", Cache.formatBytes( 512 ), "512 B" );
   check( "formatBytes KB", Cache.formatBytes( 2048 ), "2.0 KB" );
   check( "formatBytes GB", Cache.formatBytes( 4 * 1024*1024*1024 ), "4.0 GB" );
   check( "formatBytes zero", Cache.formatBytes( 0 ), "0 B" );
   check( "formatBytes null", Cache.formatBytes( null ), "0 B" );

   // --- cache folder selection ---
   // Every path helper reads Cache.dir(), so overriding it has to move the
   // whole cache and not just the entries someone remembered to update.
   var savedCacheDir = Cache.overrideDir;
   try
   {
      Cache.setDir( "" );
      check( "cache dir defaults to system temp",
             Cache.dir(), File.systemTempDirectory + "/Loom-cache" );

      Cache.setDir( "/somewhere/else" );
      check( "cache dir honours the override", Cache.dir(), "/somewhere/else" );
      check( "cache entry path follows the override",
             Cache.pathFor( "abc" ), "/somewhere/else/abc.xisf" );
      check( "cache meta path follows the override",
             Cache.metaPathFor( "abc" ), "/somewhere/else/abc.json" );
      check( "cache companion path follows the override",
             Cache.companionPathFor( "abc", "stars" ),
             "/somewhere/else/abc.stars.xisf" );

      // A folder typed as spaces is not a folder -- treat it as unset
      // rather than creating a directory named " " beside the script.
      Cache.setDir( "   " );
      check( "whitespace-only cache dir counts as unset",
             Cache.dir(), File.systemTempDirectory + "/Loom-cache" );

      Cache.setDir( null );
      check( "null cache dir counts as unset",
             Cache.dir(), File.systemTempDirectory + "/Loom-cache" );

      // Surrounding whitespace is a typo, not part of the path.
      Cache.setDir( "  /trimmed  " );
      check( "cache dir is trimmed", Cache.dir(), "/trimmed" );

      /*
       * Run logs go in a subdirectory: Cache.clear and Cache.totalBytes
       * both walk every FILE in the cache folder and skip directories, so
       * this is what keeps logs out of the reported size and out of what
       * "Clear cache" deletes.
       */
      Cache.setDir( "/somewhere/else" );
      check( "the log folder is inside the cache folder",
             Cache.logDir(), "/somewhere/else/logs" );
      check( "the log folder follows the cache folder",
             Cache.logDir().indexOf( Cache.dir() ), 0 );
      check( "the log folder is a directory, not a sibling file",
             Cache.logDir() != Cache.dir(), true );
   }
   finally { Cache.setDir( savedCacheDir ); }

   /*
    * Frequency separation. The radius comes from the plate's own stars, so
    * a plate whose stars cannot be measured has no radius -- it must refuse
    * rather than fall back to some invented number.
    */
   check( "the blur is twice the measured PSF sigma", Steps.FS_SIGMA_FACTOR, 2.0 );
   check( "the high layer carries a half-scale pedestal", Steps.FS_PEDESTAL, 0.5 );
   check( "the difference is halved, as Apply Image's Scale 2 does",
          Steps.FS_SCALE, 2.0 );

   /*
    * Colour profiles. AssignICCProfile's mode MUST be 0 to use the name it
    * is given; its default of 1 assigns the application default -- sRGB --
    * and still returns true, which is how the mono plates ended up
    * untagged without anything complaining.
    */
   check( "profiles are assigned by name, not by the default mode",
          Steps.ASSIGN_NEW_PROFILE, 0 );
   check( "colour plates get ProPhoto", Steps.PROFILE_RGB,
          "ROMM RGB: ISO 22028-2:2013" );
   check( "mono plates get the gamma-1.8 grayscale profile",
          Steps.PROFILE_GRAY, "Generic Gray Profile" );
   check( "a three-channel plate takes the RGB profile",
          Steps.profileNameFor( { mainView: { image: { numberOfChannels: 3 } } } ),
          Steps.PROFILE_RGB );
   check( "a one-channel plate takes the grayscale profile",
          Steps.profileNameFor( { mainView: { image: { numberOfChannels: 1 } } } ),
          Steps.PROFILE_GRAY );

   // The PSB carries its profile in image resource 1039, which the writer
   // builds by hand: nothing embeds it for us there.
   /*
    * The project name is derived from where the masters live, because PJSR
    * exposes nothing at all about the open PixInsight project. Container
    * folders are skipped so the answer names the target, not the layout.
    */
   check( "the target folder becomes the project name",
          Pipeline.projectNameFromPath(
             "/Volumes/A008/Elephant Trunk/master/masterLight_FILTER-H.xisf" ),
          "Elephant Trunk" );
   check( "generic folders are skipped, however many",
          Pipeline.projectNameFromPath(
             "/data/NGC 7000/integration/master/autocrop/masterLight.xisf" ),
          "NGC 7000" );
   check( "a path with nothing but generic folders yields nothing",
          Pipeline.projectNameFromPath( "/master/autocrop/x.xisf" ), "" );
   check( "an empty path yields nothing",
          Pipeline.projectNameFromPath( "" ), "" );
   check( "the first channel with a path wins",
          Pipeline.projectNameFor( { paths: { L: "", R: "/x/Rosette/master/r.xisf" } } ),
          "Rosette" );
   check( "no paths at all yields nothing",
          Pipeline.projectNameFor( { paths: {} } ), "" );

   /*
    * The header-only probe. It must never throw and must never report a
    * failure without a reason: the reason is what the caller prints before
    * it falls back to decoding a whole gigabyte-sized master, and an
    * unexplained fallback is exactly the silent behaviour that warning was
    * added to end.
    */
   var noReader = Pipeline.tryHeaderRead( "/nonexistent/loom-selftest.zzzz" );
   check( "an unreadable extension yields no header info",
          noReader.info, null );
   check( "...and says why, in words",
          typeof noReader.why == "string" && noReader.why.length > 0, true );
   var noFile = Pipeline.tryHeaderRead( "/nonexistent/loom-selftest.xisf" );
   check( "a missing file does not throw out of the probe",
          noFile.info, null );
   check( "...and it too carries a reason",
          typeof noFile.why == "string" && noFile.why.length > 0, true );
   check( "an empty path is a failure like any other, not a crash",
          Pipeline.tryHeaderRead( "" ).info, null );

   /*
    * The PSB's own name. One rule, used by both the writer and the
    * checkbox that promises what will be written.
    */
   check( "the PSB takes the project name",
          Pipeline.psbBaseName( { projectName: "Rosette" } ), "Rosette" );
   check( "surrounding space is not part of the name",
          Pipeline.psbBaseName( { projectName: "  Rosette  " } ), "Rosette" );
   check( "an empty box falls back to Loom",
          Pipeline.psbBaseName( { projectName: "" } ), "Loom" );
   check( "so does no name at all",
          Pipeline.psbBaseName( {} ), "Loom" );

   /*
    * ROMM RGB and ProPhoto RGB are the same colour space under different
    * names, and Photoshop matches its working space by NAME -- so the PSB
    * prefers Adobe's profile file, which PixInsight cannot assign.
    */
   check( "Adobe's ProPhoto is looked for first",
          Steps.PROFILE_RGB_FILES[0].indexOf( "Adobe" ) > 0, true );
   check( "Windows locations are covered too",
          Steps.PROFILE_RGB_FILES.join( "|" ).indexOf( "C:/" ) > 0, true );
   check( "every candidate is an ICC file",
          Steps.PROFILE_RGB_FILES.filter( function( p )
             { return !/\.(icm|icc)$/i.test( p ); } ).length, 0 );

   /*
    * The palette curves. Which channel a line occupies depends on the
    * palette -- Ha is RED in HSO and HOO but GREEN in SHO -- so getting
    * this from Util.PALETTES rather than assuming it is the whole point.
    */
   function curvesIn( results )
   {
      var doc = Steps.buildPsbDocument( results );
      var out = {};
      for ( var i = 0; i < doc.length; ++i )
         if ( doc[i].group != null )
            for ( var j = 0; j < doc[i].group.length; ++j )
               if ( doc[i].group[j].curves != null )
               {
                  /*
                   * Keyed by the LINE, with the "[R]" the name now carries
                   * stripped off -- these tests are about which channel the
                   * curve acts on, and the name is checked separately.
                   */
                  var nm = doc[i].group[j].name.replace( /\[.*$/, "" );
                  out[nm] = doc[i].group[j].curves.join( "," );
               }
      return out;
   }
   var fake = { mainView: { image: { numberOfChannels: 3 } } };

   /*
    * Order, which the channel mapping alone does not pin down. The array
    * is bottom-first, so Ha last puts it at the TOP of the panel and the
    * layers read Ha, SII, OIII downwards.
    */
   function curveOrder( results )
   {
      var doc = Steps.buildPsbDocument( results );
      for ( var i = 0; i < doc.length; ++i )
         if ( doc[i].group != null && doc[i].name.length == 3 )
         {
            var names = [];
            for ( var j = 0; j < doc[i].group.length; ++j )
               if ( doc[i].group[j].curves != null )
                  names.push( doc[i].group[j].name );
            return names.join( "," );
         }
      return "";
   }
   check( "bottom to top the curves are OIII, SII, Ha, each naming its channel",
          curveOrder( { HSO_starless: fake } ), "OIII[B],SII[G],Ha[R]" );
   check( "SHO names the channels it actually moved them to",
          curveOrder( { SHO_starless: fake } ), "OIII[B],SII[R],Ha[G]" );
   check( "HOO names both of OIII's channels",
          curveOrder( { HOO_starless: fake } ), "OIII[G,B],Ha[R]" );
   /*
    * Compared field by field, not as whole objects: JSON.stringify is
    * key-order sensitive, so reordering the LAYERS once broke these tests
    * while the mapping they exist to check was untouched.
    */
   function mappingOf( results )
   {
      var m = curvesIn( results );
      return [ "Ha=" + ( m.Ha || "-" ),
               "SII=" + ( m.SII || "-" ),
               "OIII=" + ( m.OIII || "-" ) ].join( " " );
   }
   check( "HSO puts Ha on red, SII on green, OIII on blue",
          mappingOf( { HSO_starless: fake } ), "Ha=1 SII=2 OIII=3" );
   check( "SHO moves Ha to green and SII to red",
          mappingOf( { SHO_starless: fake } ), "Ha=2 SII=1 OIII=3" );
   check( "HOO gives OIII both green and blue, and has no SII",
          mappingOf( { HOO_starless: fake } ), "Ha=1 SII=- OIII=2,3" );

   /*
    * The 'curv' payload's own shape, against bytes generated by psd-tools
    * for the same structure: is_map 0, version 1, then the channel bitmap.
    */
   var cp = Psb.curvesPayload( [ 1 ] );
   check( "curves payload is point data, not a map", cp.bytes[0], 0 );
   check( "curves payload is version 1", cp.bytes[1]*256 + cp.bytes[2], 1 );
   /*
    * The bitmap names ONLY what was asked for. An added composite bit is
    * the bug that made every palette curve act on all three channels:
    * Photoshop put the bend on the white composite line instead of the
    * red one.
    */
   check( "the bitmap names the target channel and nothing else",
          cp.bytes[3]*16777216 + cp.bytes[4]*65536 + cp.bytes[5]*256 + cp.bytes[6],
          0x2 );
   check( "a two-channel curve sets both bits and no others",
          ( function() { var b = Psb.curvesPayload( [ 2, 3 ] );
                         return b.bytes[6]; } )(), 0xC );
   /*
    * The curve itself is IDENTITY -- two points, black to white. The layer
    * is there to be dragged, and must change nothing until it is.
    */
   check( "the curve has two points", cp.bytes[7]*256 + cp.bytes[8], 2 );
   check( "and they are the identity, black to white",
          [ cp.bytes[9], cp.bytes[10], cp.bytes[11], cp.bytes[12],
            cp.bytes[13], cp.bytes[14], cp.bytes[15], cp.bytes[16] ].join( "," ),
          "0,0,0,0,0,255,0,255" );

   /*
    * The channel is in the layer NAME because the panel will not show it:
    * Photoshop opens the dropdown on RGB whatever the file says.
    */
   check( "a curve layer names its channel",
          Steps.curveLayerName( "Ha", [ 1 ] ), "Ha[R]" );
   check( "...and the blue one too",
          Steps.curveLayerName( "OIII", [ 3 ] ), "OIII[B]" );
   check( "a line feeding two channels names both",
          Steps.curveLayerName( "OIII", [ 2, 3 ] ), "OIII[G,B]" );

   check( "asking for the composite still gets it",
          ( function() { var b = Psb.curvesPayload( [ 0 ] );
                         return b.bytes[6]; } )(), 0x1 );
   check( "the payload is padded to a multiple of four",
          cp.length() % 4, 0 );

   /*
    * Which layer stands in for the flattened composite. Psb.write itself
    * cannot be exercised here -- a malformed PSB fails in Photoshop, not
    * in PixInsight -- but the choice of base layer is pure, so it is
    * pinned directly.
    */
   function psbLayer( name, opts )
   {
      var l = { name: name, divider: null, visible: true };
      for ( var k in opts )
         l[k] = opts[k];
      return l;
   }
   function baseName( layers )
   {
      var b = Psb.compositeBaseLayer( layers );
      return ( b == null ) ? null : b.name;
   }
   check( "the bottom-most visible pixel layer is the composite",
          baseName( [ psbLayer( "bottom" ), psbLayer( "top" ) ] ), "bottom" );
   check( "group dividers are not pixels",
          baseName( [ psbLayer( "open", { divider: "open" } ),
                      psbLayer( "real" ) ] ), "real" );
   check( "neither are adjustment layers, of either kind",
          baseName( [ psbLayer( "curves", { curves: [ 0 ] } ),
                      psbLayer( "hs", { hueSaturation: true } ),
                      psbLayer( "real" ) ] ), "real" );
   check( "a hidden pixel layer loses to a visible one further up",
          baseName( [ psbLayer( "hidden", { visible: false } ),
                      psbLayer( "shown" ) ] ), "shown" );
   check( "but an all-hidden document still gets a composite",
          baseName( [ psbLayer( "hidden", { visible: false } ),
                      psbLayer( "alsoHidden", { visible: false } ) ] ), "hidden" );
   check( "a document with no pixel layer at all has no base",
          baseName( [ psbLayer( "curves", { curves: [ 0 ] } ) ] ), null );
   check( "and neither does an empty document",
          Psb.compositeBaseLayer( [] ), null );

   /*
    * The updater. Everything below runs against injected predicates and a
    * stubbed spawn: no repository, no network, no filesystem.
    */
   function fakeIo( files, dirs, gitResult )
   {
      var spawned = [];
      return {
         spawned: spawned,
         written: {},
         fileExists:      function( p ) { return files.indexOf( p ) >= 0; },
         directoryExists: function( p ) { return dirs.indexOf( p ) >= 0; },
         readText:        function( p ) { return files[p] || ""; },
         writeText:       function( p, t ) { this.written[p] = t; },
         remove:          function() {},
         rename:          function() {},
         makeDirectory:   function() {},
         platform:        function() { return Util.PLATFORM_MACOS; },
         execute:         function() { return gitResult; },
         spawnDetached:   function( prog, args ) { spawned.push( prog + " " + args.join( " " ) ); }
      };
   }
   var GOOD_GIT = { exitCode: 0, output: "git version 2.39.5 (Apple Git-154)" };

   check( "the version is a well-formed release number",
          Update.parseVersion( Util.LOOM_VERSION ) != null, true );
   check( "a missing patch counts as zero",
          Update.compareVersions( "0.1", "0.1.0" ), 0 );
   check( "a newer minor wins", Update.compareVersions( "0.2", "0.1" ), 1 );
   check( "an older major loses", Update.compareVersions( "0.9", "1.0" ), -1 );
   check( "a leading v is not part of the number",
          Update.compareVersions( "v0.2", "0.2" ), 0 );
   check( "garbage is not a version", Update.parseVersion( "main" ), null );
   /*
    * An unparseable tag must not read as an update: a release is only ever
    * taken on a POSITIVE answer, never on the absence of a negative one.
    */
   check( "an unparseable tag is never newer",
          Update.isNewerTag( "nightly", "0.1" ), false );
   check( "a newer tag is newer", Update.isNewerTag( "v0.2", "0.1" ), true );
   check( "the same version is not newer",
          Update.isNewerTag( "v0.1", "0.1" ), false );

   /*
    * /usr/bin/git EXISTS on every Mac whether or not git is installed: it
    * is a stub that opens Apple's installer dialog when run. Returning it
    * unguarded would throw a modal system installer into Loom's startup.
    */
   check( "the bare macOS stub is not a usable git",
          Update.resolveGitPath( Update.gitCandidates( Util.PLATFORM_MACOS ),
             fakeIo( [ "/usr/bin/git" ], [] ) ), null );
   check( "the stub counts once the command line tools are there",
          Update.resolveGitPath( Update.gitCandidates( Util.PLATFORM_MACOS ),
             fakeIo( [ "/usr/bin/git",
                       "/Library/Developer/CommandLineTools/usr/bin/git" ], [] ) ),
          "/usr/bin/git" );
   check( "homebrew's git needs no guard",
          Update.resolveGitPath( Update.gitCandidates( Util.PLATFORM_MACOS ),
             fakeIo( [ "/opt/homebrew/bin/git", "/usr/bin/git" ], [] ) ),
          "/opt/homebrew/bin/git" );
   check( "no git at all resolves to nothing",
          Update.resolveGitPath( Update.gitCandidates( Util.PLATFORM_MACOS ),
             fakeIo( [], [] ) ), null );
   check( "a working git is recognised", Update.isWorkingGit( GOOD_GIT ), true );
   check( "a non-zero exit is not a working git",
          Update.isWorkingGit( { exitCode: 1, output: "git version 2.39" } ), false );
   check( "empty output is not a working git",
          Update.isWorkingGit( { exitCode: 0, output: "" } ), false );

   /*
    * .git is a FILE in a linked worktree or a submodule checkout. Testing
    * only for the directory reports "not a repository" for a real
    * checkout, which would route it into the path that REPLACES the
    * directory -- exactly what the rule exists to prevent.
    */
   check( "an ordinary clone is git-managed",
          Update.isGitManaged( "/x/Loom", fakeIo( [], [ "/x/Loom/.git" ] ) ), true );
   check( "a worktree, where .git is a file, is git-managed too",
          Update.isGitManaged( "/x/Loom", fakeIo( [ "/x/Loom/.git" ], [] ) ), true );
   check( "a plain directory is not git-managed",
          Update.isGitManaged( "/x/Loom", fakeIo( [], [] ) ), false );

   check( "a checkout is a git install",
          Update.installKind( "/x/Loom", fakeIo( [], [ "/x/Loom/.git" ] ) ), "git" );
   check( "a marked directory is a release install",
          Update.installKind( "/x/Loom", fakeIo( [ "/x/Loom/RELEASE" ], [] ) ),
          "release" );
   /*
    * No .git and no marker: unknown, and left alone. A directory is only
    * ever replaced if this feature created it.
    */
   check( "an unmarked directory is unknown and untouchable",
          Update.installKind( "/x/Loom", fakeIo( [], [] ) ), "unknown" );

   Update.SCRIPT_DIR = "/x/Loom";
   check( "autoUpdate off spawns nothing",
          Update.start( { autoUpdate: false },
             fakeIo( [], [ "/x/Loom/.git" ], GOOD_GIT ) ), null );
   check( "a checkout with a working git spawns the git updater",
          Update.start( { autoUpdate: true },
             fakeIo( [ "/opt/homebrew/bin/git" ], [ "/x/Loom/.git" ], GOOD_GIT ) ),
          "git" );
   /*
    * A checkout whose git is unusable spawns NOTHING. It must not fall
    * through to the zip path: that swaps a directory into place, which
    * over a working tree leaves a repository permanently dirty and
    * refused by every later --ff-only.
    */
   check( "a checkout with no usable git spawns nothing at all",
          Update.start( { autoUpdate: true },
             fakeIo( [], [ "/x/Loom/.git" ], GOOD_GIT ) ), null );
   check( "an unknown directory spawns nothing",
          Update.start( { autoUpdate: true }, fakeIo( [], [], GOOD_GIT ) ), null );

   /*
    * Each guard in the generated script earned its place by being wrong in
    * an earlier draft. These assertions exist so a later edit cannot
    * quietly drop one.
    */
   var gs = Update.gitScript( { git: "/opt/homebrew/bin/git", dir: "/x/Loom",
                                stateDir: "/home/.loom" } );
   check( "the update is fast-forward only", gs.indexOf( "--ff-only" ) >= 0, true );
   check( "the dirty check sees untracked files too",
          gs.indexOf( "--untracked-files=all" ) >= 0, true );
   /*
    * The guard that was wrong before: `git diff --quiet` compares tracked
    * files against the index, so it misses staged changes and untracked
    * files. It must not come back.
    */
   check( "the discredited diff guard is not used",
          gs.indexOf( "diff --quiet" ) < 0, true );
   check( "an inherited autostash cannot stash the user's work",
          gs.indexOf( "merge.autoStash=false" ) >= 0, true );
   check( "a credential prompt cannot stall the background fetch",
          gs.indexOf( "GIT_TERMINAL_PROMPT=0" ) >= 0, true );
   check( "ssh runs in batch mode for the same reason",
          gs.indexOf( "BatchMode=yes" ) >= 0, true );
   check( "two launches cannot both update",
          gs.indexOf( "mkdir \"$LOCK\"" ) >= 0, true );
   check( "the upstream is resolved rather than assumed",
          gs.indexOf( "--symbolic-full-name" ) >= 0, true );
   check( "the outcome is published by rename, not written in place",
          gs.indexOf( "mv \"$TMP\" \"$OUT\"" ) >= 0, true );

   var zs = Update.zipScript( { dir: "/x/Loom", stateDir: "/home/.loom",
                                version: "0.1", owner: "o", repo: "r" } );
   check( "curl fails on an error page instead of saving it",
          zs.indexOf( "-fsSL" ) >= 0, true );
   check( "a redirect cannot downgrade the transport",
          zs.indexOf( "--proto '=https'" ) >= 0, true );
   check( "the old copy is kept until the new one is in place",
          zs.indexOf( "mv \"$DIR\" \"$DIR.old\"" ) >= 0, true );
   check( "a failed install rolls back",
          zs.indexOf( "mv \"$DIR.old\" \"$DIR\"" ) >= 0, true );
   check( "the installed tree is marked as a release install",
          zs.indexOf( Update.RELEASE_MARKER ) >= 0, true );

   /*
    * Outcome records. A shell redirect would record TEXT, not outcome:
    * `>` truncates when the process starts, so an empty file cannot tell
    * success from a refusal from a worker that was killed.
    */
   var rec = Update.formatOutcome( { status: "updated", exitCode: 0,
                                     from: "aaaaaaa", to: "bbbbbbb",
                                     when: "2026-09-17T00:00:00Z", message: "" } );
   var back = Update.parseOutcome( rec );
   check( "an outcome survives the round trip", back.status, "updated" );
   check( "...with both commits", back.from + ">" + back.to, "aaaaaaa>bbbbbbb" );
   check( "a truncated record is not mistaken for success",
          Update.parseOutcome( "updated\t0" ), null );
   check( "an unknown status is rejected",
          Update.parseOutcome( "hacked\t0\t-\t-\t-\t" ), null );
   check( "a newline in git's message cannot forge a second record",
          Update.formatOutcome( { status: "failed", exitCode: 1, from: "-", to: "-",
                                  when: "-", message: "bad\nupdated\t0" }
                              ).split( "\n" ).length, 1 );
   check( "a dirty tree reads as a refusal, not a failure",
          Update.isFailure( { status: "skipped-dirty" } ), true );
   check( "an unchanged repository is not a failure",
          Update.isFailure( { status: "unchanged" } ), false );
   check( "a failure quotes what git actually said",
          Update.outcomeMessage( { status: "failed", exitCode: 128,
                                   message: "Could not resolve host" } )
            .indexOf( "Could not resolve host" ) >= 0, true );

   /*
    * The commit is read straight out of .git, with no git binary, because
    * the version number alone does not identify the running code.
    */
   var headIo = fakeIo( [], [] );
   headIo.fileExists = function( p )
   {
      return p == "/x/Loom/.git/HEAD" || p == "/x/Loom/.git/refs/heads/main";
   };
   headIo.readText = function( p )
   {
      return ( p == "/x/Loom/.git/HEAD" ) ? "ref: refs/heads/main\n"
                                          : "a4c1f2e9876543210fedcba9876543210fedcba9\n";
   };
   check( "the short commit comes from the ref HEAD names",
          Update.headCommit( "/x/Loom", headIo ), "a4c1f2e" );
   check( "the title carries version and commit",
          Update.describeVersion( "/x/Loom", headIo ),
          "Loom " + Util.LOOM_VERSION + " (a4c1f2e)" );
   check( "with no repository it carries the version alone",
          Update.describeVersion( "/x/Loom", fakeIo( [], [] ) ),
          "Loom " + Util.LOOM_VERSION );


   /* ---------------------------------------------------------------- */
   /* Running on Windows as well as macOS                               */
   /* ---------------------------------------------------------------- */

   /*
    * These tests are the whole reason the platform is an ARGUMENT
    * everywhere rather than a read of Util.PLATFORM: the machine this
    * suite runs on is a Mac, so the Windows branch can only ever be
    * checked by injecting it. Testing the branch you happen to be
    * standing on is not testing the code.
    */
   check( "the platform resolves to one Loom knows",
          [ Util.PLATFORM_WINDOWS, Util.PLATFORM_MACOS, Util.PLATFORM_UNIX ]
             .indexOf( Util.PLATFORM ) >= 0, true );
   /*
    * The preprocessor branch actually taken. If __PI_PLATFORM__ ever stops
    * being MACOSX here, this is the test that says so rather than a
    * mysteriously macOS-shaped Windows run.
    */
   check( "this build identifies itself as macOS", Util.PLATFORM,
          Util.PLATFORM_MACOS );
   check( "an injected platform overrides it",
          Util.platform( Util.PLATFORM_WINDOWS ), Util.PLATFORM_WINDOWS );
   check( "with nothing injected the running platform is used",
          Util.platform(), Util.PLATFORM );
   check( "Windows is Windows", Util.isWindows( Util.PLATFORM_WINDOWS ), true );
   check( "macOS is not Windows", Util.isWindows( Util.PLATFORM_MACOS ), false );
   check( "linux is not Windows", Util.isWindows( Util.PLATFORM_UNIX ), false );

   /*
    * The install layout is asked of the core, not spelled out. These are
    * the four paths that used to be /Applications literals; each is
    * checked against the running installation, so a wrong property name
    * fails here rather than mid-run.
    */
   check( "the spectrum database is where the core says it is",
          Steps.FILTERS_XSPD_PATH,
          CoreApplication.baseDirPath + "/library/filters.xspd" );
   check( "...and it is really there", File.exists( Steps.FILTERS_XSPD_PATH ), true );
   check( "the bundled scripts are where the core says they are",
          Steps.PI_SRC_SCRIPTS_DIR, CoreApplication.srcDirPath + "/scripts" );
   check( "...and the ImageSolver engine is really there",
          File.exists( Steps.IMAGE_SOLVER_ENGINE_PATH ), true );
   check( "the core settings directory is the core's own",
          Steps.CORE_SETTINGS_DIR, CoreApplication.configDirPath );
   check( "...and it is really there",
          File.directoryExists( Steps.CORE_SETTINGS_DIR ), true );
   /*
    * The include that cannot take a runtime path. It is written
    * <../src/scripts/...>, relative to the core's include directory, so it
    * resolves wherever PixInsight is installed and on whatever platform.
    * If it ever fails to resolve, PixInsight discards this whole script
    * silently -- so reaching this line at all is half the assertion.
    */
   check( "the ImageSolver engine class is in scope",
          typeof ImageSolver, "function" );

   /*
    * No source file may carry an absolute path into the PixInsight
    * installation again. The check is on the sources because a literal
    * that is only reached on a Windows machine cannot be caught any other
    * way from here.
    */
   var LIB_FILES = [ "Util.js", "Cache.js", "Psb.js", "Steps.js",
                     "Pipeline.js", "Update.js", "UI.js" ];
   var hardcoded = [];
   for ( var lf = 0; lf < LIB_FILES.length; ++lf )
   {
      var src = File.readTextFile( LOOM_DIR + "/lib/" + LIB_FILES[lf] );
      // A quote immediately before the path: a string literal, not prose.
      if ( src.indexOf( "\"/Applications/PixInsight" ) >= 0 ||
           src.indexOf( "\"C:/Program Files/PixInsight" ) >= 0 ||
           src.indexOf( "\"/Library/PixInsight" ) >= 0 )
         hardcoded.push( LIB_FILES[lf] );
   }
   check( "no library hardcodes the PixInsight install path", hardcoded, [] );

   /*
    * Where applications live. The macOS answer must not have changed --
    * this is the list that finds the SyQon binaries today.
    */
   check( "macOS looks in both Applications folders",
          Steps.applicationRoots( Util.PLATFORM_MACOS, "/Users/x" ),
          [ "/Applications", "/Users/x/Applications" ] );
   check( "linux uses the same list",
          Steps.applicationRoots( Util.PLATFORM_UNIX, "/home/x" ),
          [ "/Applications", "/home/x/Applications" ] );
   check( "Windows looks in the Program Files trees and the per-user one",
          Steps.applicationRoots( Util.PLATFORM_WINDOWS, "C:/Users/x" ),
          [ "C:/Program Files", "C:/Program Files (x86)",
            "C:/Users/x/AppData/Local/Programs" ] );
   check( "an unknown home drops the per-user root rather than building \"/AppData\"",
          Steps.applicationRoots( Util.PLATFORM_WINDOWS, "" ),
          [ "C:/Program Files", "C:/Program Files (x86)" ] );
   check( "and does the same on macOS",
          Steps.applicationRoots( Util.PLATFORM_MACOS, "" ), [ "/Applications" ] );

   /*
    * One level of a directory, which is all the scan ever looks at. Run
    * against Loom's own lib/ folder: it is guaranteed to be there, and its
    * contents are already known to this suite.
    */
   var libEntries = Steps.directoryEntries( LOOM_DIR + "/lib" );
   check( "one directory level lists the library files",
          libEntries.indexOf( "Steps.js" ) >= 0 &&
          libEntries.indexOf( "Pipeline.js" ) >= 0, true );
   check( "the dot entries are not part of the listing",
          libEntries.indexOf( "." ) < 0 && libEntries.indexOf( ".." ) < 0, true );
   check( "a root that is not there enumerates as empty, not as an error",
          Steps.directoryEntries( "/nonexistent/loom-selftest-root" ), [] );

   /*
    * Picking a binary out of a candidate list. First match wins, and a
    * path that cannot be tested counts as absent rather than aborting the
    * search -- otherwise one odd entry in /Applications would hide every
    * tool installed after it.
    */
   check( "the first candidate that exists is the answer",
          Steps.firstExistingPath( [ LOOM_DIR + "/lib/nothing-here.js",
                                     LOOM_DIR + "/lib/Steps.js",
                                     LOOM_DIR + "/lib/Util.js" ] ),
          LOOM_DIR + "/lib/Steps.js" );
   check( "no candidate exists and the answer is null",
          Steps.firstExistingPath( [ "/nonexistent/a", "/nonexistent/b" ] ), null );
   check( "an empty candidate list is null too",
          Steps.firstExistingPath( [] ), null );

   /*
    * What an executable looks like under one of those roots. The .exe
    * suffix is added here rather than baked into the tool names, so
    * Steps.findExecutable( "parallax_cli" ) is one call on both platforms.
    */
   check( "macOS checks the bare binary and the app bundle",
          Steps.executableCandidates( "/Applications/X", "tool",
                                      Util.PLATFORM_MACOS ),
          [ "/Applications/X/tool", "/Applications/X/Contents/MacOS/tool" ] );
   check( "Windows checks .exe, bin\\.exe and the bare name",
          Steps.executableCandidates( "C:/Program Files/X", "tool",
                                      Util.PLATFORM_WINDOWS ),
          [ "C:/Program Files/X/tool.exe", "C:/Program Files/X/bin/tool.exe",
            "C:/Program Files/X/tool" ] );

   /*
    * The starless model is derived from wherever the binary was found. The
    * bare /Applications fallback is macOS-only: on Windows that is a path
    * on the current drive, which is worse than no candidate at all.
    */
   var MODEL_MAC = "/Applications/SyQonStarless.app/Contents/Resources/axiom3.mlmodelc";
   check( "the model is looked for beside the binary first",
          Steps.starlessModelCandidates(
             "/Applications/SyQonStarless.app/Contents/MacOS/SyQonStarless",
             Util.PLATFORM_MACOS )[0],
          MODEL_MAC );
   check( "with no binary macOS still has its last resort",
          Steps.starlessModelCandidates( null, Util.PLATFORM_MACOS ),
          [ MODEL_MAC ] );
   check( "Windows does not fall back to a macOS path",
          Steps.starlessModelCandidates( null, Util.PLATFORM_WINDOWS ), [] );
   check( "Windows still looks beside the binary",
          Steps.starlessModelCandidates( "C:/Program Files/SyQon/SyQonStarless.exe",
                                         Util.PLATFORM_WINDOWS ).length, 2 );

   /* ---------------------------------------------------------------- */
   /* The updater on Windows                                            */
   /* ---------------------------------------------------------------- */

   check( "Git for Windows is looked for where its installer puts it",
          Update.resolveGitPath(
             Update.gitCandidates( Util.PLATFORM_WINDOWS, "C:/Users/x" ),
             fakeIo( [ "C:/Program Files/Git/cmd/git.exe" ], [] ) ),
          "C:/Program Files/Git/cmd/git.exe" );
   /*
    * A machine where the user cannot elevate gets git under their own
    * profile. Missing that path means the updater silently never runs.
    */
   check( "a per-user Git for Windows install is found too",
          Update.resolveGitPath(
             Update.gitCandidates( Util.PLATFORM_WINDOWS, "C:/Users/x" ),
             fakeIo( [ "C:/Users/x/AppData/Local/Programs/Git/cmd/git.exe" ], [] ) ),
          "C:/Users/x/AppData/Local/Programs/Git/cmd/git.exe" );
   check( "no git on Windows resolves to nothing",
          Update.resolveGitPath(
             Update.gitCandidates( Util.PLATFORM_WINDOWS, "C:/Users/x" ),
             fakeIo( [], [] ) ), null );
   /*
    * The macOS stub guard is a macOS concept. It must not leak into the
    * Windows list, where /usr/bin/git means nothing.
    */
   check( "the Windows candidates carry no macOS guards",
          Update.gitCandidates( Util.PLATFORM_WINDOWS, "C:/Users/x" )
             .filter( function( c ) { return c.guards.length > 0; } ).length, 0 );

   check( "the POSIX helper is a shell script", Update.helperFileName( Util.PLATFORM_MACOS ),
          "update-run.sh" );
   /*
    * .ps1 is not decoration: powershell -File refuses any other extension.
    */
   check( "the Windows helper is a .ps1", Update.helperFileName( Util.PLATFORM_WINDOWS ),
          "update-run.ps1" );
   var macCmd = Update.helperCommand( Util.PLATFORM_MACOS, "/s/update-run.sh" );
   check( "macOS runs the helper with /bin/sh", macCmd.program, "/bin/sh" );
   check( "...and passes it the file", macCmd.args, [ "/s/update-run.sh" ] );
   var winCmd = Update.helperCommand( Util.PLATFORM_WINDOWS, "C:/s/update-run.ps1" );
   /*
    * PowerShell rather than cmd.exe because PJSR hands out forward-slash
    * paths on Windows too, and cmd.exe reads a leading "/" as a switch.
    */
   check( "Windows runs the helper with PowerShell", winCmd.program, "powershell.exe" );
   check( "the script path is the last argument",
          winCmd.args[ winCmd.args.length - 1 ], "C:/s/update-run.ps1" );
   check( "the default Restricted policy cannot block it",
          winCmd.args.indexOf( "Bypass" ) >= 0, true );
   check( "a user profile cannot change the updater's environment",
          winCmd.args.indexOf( "-NoProfile" ) >= 0, true );
   check( "nothing detached can sit waiting for input",
          winCmd.args.indexOf( "-NonInteractive" ) >= 0, true );

   /* The same guards again, this time in PowerShell. */
   var ps = Update.gitScript( { git: "C:/Program Files/Git/cmd/git.exe",
                                dir: "C:/Users/x/Loom", stateDir: "C:/Users/x/.loom",
                                platform: Util.PLATFORM_WINDOWS } );
   check( "the Windows update is fast-forward only",
          ps.indexOf( "--ff-only" ) >= 0, true );
   check( "the Windows dirty check sees untracked files too",
          ps.indexOf( "--untracked-files=all" ) >= 0, true );
   check( "the discredited diff guard is not used on Windows either",
          ps.indexOf( "diff --quiet" ) < 0, true );
   check( "an inherited autostash cannot stash the user's work on Windows",
          ps.indexOf( "merge.autoStash=false" ) >= 0, true );
   check( "a credential prompt cannot stall the Windows fetch",
          ps.indexOf( "GIT_TERMINAL_PROMPT" ) >= 0, true );
   check( "ssh runs in batch mode on Windows too",
          ps.indexOf( "BatchMode=yes" ) >= 0, true );
   check( "two Windows launches cannot both update",
          ps.indexOf( "New-Item -ItemType Directory -Path $LOCK -ErrorAction Stop" ) >= 0,
          true );
   check( "the lock is released however the Windows helper ends",
          ps.indexOf( "finally {" ) >= 0, true );
   check( "the Windows upstream is resolved rather than assumed",
          ps.indexOf( "--symbolic-full-name" ) >= 0, true );
   check( "the Windows outcome is published by rename",
          ps.indexOf( "Move-Item -LiteralPath $TMP -Destination $OUT -Force" ) >= 0,
          true );
   /*
    * Set-Content and Out-File write ANSI or UTF-16-with-BOM under
    * PowerShell 5.1, and a BOM in front of the status word makes
    * Update.parseOutcome reject the record as malformed. Everything is
    * written through [System.IO.File] instead.
    */
   check( "the Windows helper writes no BOM",
          ps.indexOf( "Set-Content" ) < 0 && ps.indexOf( "Out-File" ) < 0, true );
   check( "...by writing through System.IO.File",
          ps.indexOf( "[System.IO.File]::WriteAllText" ) >= 0, true );
   /*
    * Under $ErrorActionPreference = 'Stop', PowerShell 5.1 turns anything
    * a native program writes to stderr into a terminating error -- so an
    * ordinary git progress line would abort the update. Failure is read
    * from $LASTEXITCODE instead.
    */
   check( "native stderr cannot abort the Windows update",
          ps.indexOf( "$ErrorActionPreference = 'Continue'" ) >= 0, true );
   check( "...and git's own exit code is what decides",
          ps.indexOf( "$LASTEXITCODE" ) >= 0, true );
   check( "an unexpected throw still leaves a record",
          ps.indexOf( "catch {" ) >= 0, true );

   /*
    * Quoting. A path may contain a single quote -- "C:/Users/O'Brien" is
    * an ordinary Windows home -- and it must not be able to end the
    * literal it sits in.
    */
   check( "PowerShell quoting doubles an embedded quote",
          Update.quotePowerShell( "C:/Users/O'Brien" ), "'C:/Users/O''Brien'" );
   check( "POSIX quoting is unchanged",
          Update.quotePosix( "/Users/O'Brien" ), "'/Users/O'\\''Brien'" );
   var psQuoted = Update.gitScript( { git: "C:/Git/git.exe",
                                      dir: "C:/Users/O'Brien/Loom",
                                      stateDir: "C:/Users/O'Brien/.loom",
                                      platform: Util.PLATFORM_WINDOWS } );
   check( "a quote in a real path is escaped in the generated script",
          psQuoted.indexOf( "'C:/Users/O''Brien/Loom'" ) >= 0, true );

   var psZip = Update.zipScript( { dir: "C:/Users/x/Loom", stateDir: "C:/Users/x/.loom",
                                   version: "0.1", owner: "o", repo: "r",
                                   platform: Util.PLATFORM_WINDOWS } );
   check( "the Windows download refuses a non-https asset",
          psZip.indexOf( "StartsWith('https://')" ) >= 0, true );
   /*
    * PowerShell 5.1 inherits .NET's default protocol list, which on an
    * un-updated machine still offers TLS 1.0 -- GitHub refuses it.
    */
   check( "TLS 1.2 is forced",
          psZip.indexOf( "Tls12" ) >= 0, true );
   check( "the old copy is kept until the new one is in place on Windows",
          psZip.indexOf( "Move-Item -LiteralPath $DIR -Destination ($DIR + '.old') -Force" ) >= 0,
          true );
   check( "a failed Windows install rolls back",
          psZip.indexOf( "Move-Item -LiteralPath ($DIR + '.old') -Destination $DIR -Force" ) >= 0,
          true );
   check( "the installed tree is marked as a release install on Windows",
          psZip.indexOf( Update.RELEASE_MARKER ) >= 0, true );
   check( "the staging directory is cleaned up however it ends",
          psZip.indexOf( "Remove-Item -LiteralPath $WORK" ) >= 0, true );

   /*
    * And the whole path end to end: a Windows checkout writes a .ps1 and
    * spawns PowerShell, with no trace of /bin/sh.
    */
   function windowsIo( files, dirs, gitResult )
   {
      var io = fakeIo( files, dirs, gitResult );
      io.platform = function() { return Util.PLATFORM_WINDOWS; };
      return io;
   }
   Update.SCRIPT_DIR = "C:/Users/x/Loom";
   var winIo = windowsIo( [ "C:/Program Files/Git/cmd/git.exe" ],
                          [ "C:/Users/x/Loom/.git" ], GOOD_GIT );
   check( "a Windows checkout spawns the git updater",
          Update.start( { autoUpdate: true }, winIo ), "git" );
   check( "...as a PowerShell process",
          winIo.spawned.length == 1 &&
          winIo.spawned[0].indexOf( "powershell.exe" ) == 0, true );
   check( "...running a .ps1",
          winIo.spawned[0].indexOf( "update-run.ps1" ) >= 0, true );
   check( "...and nothing anywhere runs /bin/sh",
          winIo.spawned[0].indexOf( "/bin/sh" ) < 0, true );
   var psWritten = winIo.written[ Update.stateDir() + "/update-run.ps1" ];
   check( "the helper written is the PowerShell one, not the shell one",
          psWritten != null && psWritten.indexOf( "$ErrorActionPreference" ) >= 0 &&
             psWritten.indexOf( "#!/bin/sh" ) < 0, true );
   /* Restore what the macOS updater tests set, so order cannot matter. */
   Update.SCRIPT_DIR = "/x/Loom";

   /*
    * Minimising the run's plates into a grid in the middle.
    *
    * An icon's position IS its restore position -- verified by moving an
    * icon and deiconizing it -- so the grid decides both where the icons
    * sit and where each plate reopens. The arithmetic is pure and tested
    * here; the iconize/position calls themselves need a workspace.
    */
   var AREA = { x: 0, y: 33, width: 1728, height: 1084 };
   var ICON = { width: 160, height: 120 };

   check( "nothing to arrange produces no positions",
          Pipeline.iconGrid( 0, ICON, AREA ).length, 0 );
   check( "one plate gets one position",
          Pipeline.iconGrid( 1, ICON, AREA ).length, 1 );
   check( "nine plates get nine",
          Pipeline.iconGrid( 9, ICON, AREA ).length, 9 );
   /*
    * Kept as square as the count allows, so the block reads as a block
    * rather than a long row that runs off the side.
    */
   check( "nine plates make three columns",
          ( function()
            {
               var g = Pipeline.iconGrid( 9, ICON, AREA );
               return ( g[3].y > g[0].y && g[3].x == g[0].x ) ? "wrapped at 3" : "did not wrap at 3";
            } )(), "wrapped at 3" );
   check( "the block is centred horizontally",
          ( function()
            {
               var g = Pipeline.iconGrid( 4, ICON, AREA );   // 2 x 2
               var left = g[0].x;
               var right = g[1].x + ICON.width;
               return Math.abs( ( left - AREA.x ) -
                                ( AREA.x + AREA.width - right ) ) <= 1;
            } )(), true );
   check( "the block is centred vertically",
          ( function()
            {
               var g = Pipeline.iconGrid( 4, ICON, AREA );
               var top = g[0].y;
               var bottom = g[2].y + ICON.height;
               return Math.abs( ( top - AREA.y ) -
                                ( AREA.y + AREA.height - bottom ) ) <= 1;
            } )(), true );
   /*
    * An icon bigger than the area would otherwise be centred at a
    * NEGATIVE coordinate, off the edge of the screen where it cannot be
    * clicked.
    */
   check( "a grid too big for the area is still reachable",
          ( function()
            {
               var g = Pipeline.iconGrid( 6, { width: 900, height: 700 }, AREA );
               for ( var i = 0; i < g.length; ++i )
                  if ( g[i].x < AREA.x || g[i].y < AREA.y )
                     return "off screen";
               return "on screen";
            } )(), "on screen" );
   check( "icons do not overlap",
          ( function()
            {
               var g = Pipeline.iconGrid( 6, ICON, AREA );
               var seen = {};
               for ( var i = 0; i < g.length; ++i )
               {
                  var key = g[i].x + "," + g[i].y;
                  if ( seen[key] )
                     return "overlap at " + key;
                  seen[key] = true;
               }
               return "distinct";
            } )(), "distinct" );

   /*
    * A fixed order, so a given plate's icon is in the same place every
    * run and can be found by position rather than by reading labels.
    */
   check( "the plates are ordered L, RGB, palette",
          Pipeline.orderedOutputKeys(
             [ "HSO_starless", "RGB_stars", "L_starless", "RGB_starless" ] ).join( "," ),
          "L_starless,RGB_starless,RGB_stars,HSO_starless" );
   check( "starless comes before stars",
          Pipeline.orderedOutputKeys( [ "L_stars", "L_starless" ] ).join( "," ),
          "L_starless,L_stars" );
   /*
    * An output nobody listed must still get a slot: dropping it from the
    * layout would leave a window unminimised with no indication why.
    */
   check( "an unknown output still gets placed, after the known ones",
          Pipeline.orderedOutputKeys( [ "zzz_custom", "L" ] ).join( "," ),
          "L,zzz_custom" );
   check( "ordering never loses or invents a plate",
          Pipeline.orderedOutputKeys( [ "b", "RGB", "a" ] ).length, 3 );

   /*
    * A Rect is corners, not an origin and a size: reading y1 as a height
    * would push the grid off the bottom by the height of the menu bar.
    */
   check( "the workspace area has a positive size",
          ( function()
            {
               var a = Pipeline.workspaceArea();
               return a.width > 0 && a.height > 0;
            } )(), true );

   /*
    * Hue/Saturation, neutral. The six range quadruples are Photoshop's own
    * band edges -- not adjustments -- and the dropdown shows the wrong
    * bands if they are left at zero.
    */
   var hp = Psb.hueSaturationPayload();
   check( "hue/saturation is version 2", hp.bytes[0]*256 + hp.bytes[1], 2 );
   /*
    * The byte after the version is COLORIZE, not "enabled" -- psd-tools'
    * name for it is misleading. Ticked, with saturation at 0, the layer
    * drains the colour from everything it touches, which is what it did.
    */
   check( "colorize is OFF", hp.bytes[2], 0 );
   check( "hue, saturation and lightness are all neutral",
          [ hp.bytes[4], hp.bytes[5], hp.bytes[6], hp.bytes[7],
            hp.bytes[8], hp.bytes[9] ].join( "" ), "000000" );
   check( "there are six colour bands", Psb.HUE_RANGES.length, 6 );
   check( "each band has four edges",
          Psb.HUE_RANGES.filter( function( r ) { return r.length != 4; } ).length, 0 );
   check( "the reds band wraps past 360 as Photoshop writes it",
          Psb.HUE_RANGES[0].join( "," ), "315,345,15,45" );
   check( "the payload is padded to a multiple of four", hp.length() % 4, 0 );

   // Both adjustment kinds are pixel-less and must be recognised as such,
   // or the writer would try to convert and store a window they do not have.
   check( "a curves layer is an adjustment",
          Psb.isAdjustment( { curves: [ 1 ] } ), true );
   check( "a hue/saturation layer is an adjustment",
          Psb.isAdjustment( { hueSaturation: true } ), true );
   check( "a pixel layer is not",
          Psb.isAdjustment( { window: {} } ), false );

   /*
    * Clipping. The saturation layer must affect the star plate ALONE, not
    * everything beneath it in the Stars group, which is the difference
    * between colouring the stars and colouring the whole stack.
    */
   var starsDoc = Steps.buildPsbDocument( {
      RGB_stars: { mainView: { image: { numberOfChannels: 3 } } } } );
   var sat = null, satIndex = -1, plateIndex = -1;
   for ( var sd = 0; sd < starsDoc.length; ++sd )
      if ( starsDoc[sd].group != null && starsDoc[sd].name == "Stars" )
         for ( var sg = 0; sg < starsDoc[sd].group.length; ++sg )
         {
            var g = starsDoc[sd].group[sg];
            if ( g.name == "Stars Saturation" ) { sat = g; satIndex = sg; }
            if ( g.name == "RGB_stars" ) plateIndex = sg;
         }
   check( "the saturation layer exists", sat != null, true );
   check( "it is clipped", sat != null && sat.clipping === true, true );
   check( "it sits directly above the star plate -- what it clips TO",
          satIndex - plateIndex, 1 );

   // ...and the curve at the top clips to the Stars GROUP below it, so it
   // never reaches the palette or the broadband plate.
   var topCurve = null, curveIndex = -1, groupIndex = -1;
   for ( var td = 0; td < starsDoc.length; ++td )
   {
      if ( starsDoc[td].name == "Stars Curve" ) { topCurve = starsDoc[td]; curveIndex = td; }
      if ( starsDoc[td].name == "Stars" && starsDoc[td].group != null ) groupIndex = td;
   }
   check( "the top curve exists", topCurve != null, true );
   check( "it is clipped", topCurve != null && topCurve.clipping === true, true );
   check( "it sits directly above the Stars group -- what it clips TO",
          curveIndex - groupIndex, 1 );

   check( "the ICC image resource is 1039", Psb.RESOURCE_ICC_PROFILE, 1039 );
   var res = Psb.imageResource( Psb.RESOURCE_ICC_PROFILE, [ 1, 2, 3 ] );
   check( "an image resource starts with 8BIM",
          String.fromCharCode( res.bytes[0], res.bytes[1], res.bytes[2], res.bytes[3] ),
          "8BIM" );
   check( "it names the resource id", res.bytes[4]*256 + res.bytes[5], 1039 );
   check( "odd payloads are padded to an even length",
          res.length() % 2, 0 );

   /*
    * The property that matters, checked as arithmetic rather than trusted
    * from a comment: Photoshop's Linear Light is
    *
    *    result = base + 2*blend - 1
    *
    * and the split must be its exact inverse. An earlier version omitted
    * the /2 and would have reconstructed 2*orig - low instead -- which the
    * constants alone would never have revealed.
    */
   function fsHigh( orig, low )
   {
      return ( orig - low )/Steps.FS_SCALE + Steps.FS_PEDESTAL;
   }
   function linearLight( base, blend ) { return base + 2*blend - 1; }
   var fsCases = [ [ 0.5, 0.5 ], [ 0.9, 0.2 ], [ 0.1, 0.8 ],
                   [ 1.0, 0.0 ], [ 0.0, 1.0 ], [ 0.62, 0.37 ] ];
   var worst = 0;
   for ( var fc = 0; fc < fsCases.length; ++fc )
   {
      var o = fsCases[fc][0], lo = fsCases[fc][1];
      var err = Math.abs( linearLight( lo, fsHigh( o, lo ) ) - o );
      if ( err > worst ) worst = err;
   }
   check( "Linear Light over the low layer returns the original exactly",
          worst < 1e-12, true );
   // ...and the high layer stays inside [0,1] even for the worst difference,
   // which is what the halving buys.
   check( "a full-range difference still fits in the file",
          fsHigh( 1, 0 ) <= 1 && fsHigh( 0, 1 ) >= 0, true );
   var savedPSF = Steps.measurePSF;
   try
   {
      Steps.measurePSF = function() { return null; };
      var threw = false;
      try { Steps.frequencySeparate( { mainView: { id: "L_stars" } }, "L_stars" ); }
      catch ( e ) { threw = /could not measure/.test( String( e ) ); }
      check( "it refuses when the stars cannot be measured", threw, true );

      Steps.measurePSF = function() { return { sigma: 0, n: 0 }; };
      var threw0 = false;
      try { Steps.frequencySeparate( { mainView: { id: "L_stars" } }, "L_stars" ); }
      catch ( e2 ) { threw0 = true; }
      check( "it refuses a zero radius", threw0, true );
   }
   finally { Steps.measurePSF = savedPSF; }

   var savedCacheDir2 = Cache.overrideDir;
   try
   {
   }
   finally { Cache.setDir( savedCacheDir ); }

   // --- Pipeline.buildStageKeys: the full per-channel key chain ---
   // Pure logic -- no PixInsight objects touched -- so invalidation is
   // exercised directly here rather than trusted from Cache's own tests.

   function baseBroadbandParams()
   {
      return {
         solve: {},
         spfc: { channel: "L", filter: "Astronomik L-2",
                 qe: "Sony IMX411/455/461/533/571" },
         mgc: { marsFiles: [ "/mars/a.bin", "/mars/b.bin" ] },
         graxpert: { enabled: true, smoothing: 0.5 }
      };
   }

   var chainA = Pipeline.buildStageKeys( "src-L", baseBroadbandParams() );
   var chainB = Pipeline.buildStageKeys( "src-L", baseBroadbandParams() );
   check( "buildStageKeys covers solve/spfc/mgc/graxpert in order",
          chainA.map( function( s ) { return s.stage; } ),
          [ "solve", "spfc", "mgc", "graxpert" ] );
   check( "buildStageKeys identical params give an identical final key",
          chainA[chainA.length - 1].key, chainB[chainB.length - 1].key );

   function finalKeyWith( mutate )
   {
      var p = baseBroadbandParams();
      mutate( p );
      return Pipeline.buildStageKeys( "src-L", p )[3].key;
   }
   var baseFinal = finalKeyWith( function( p ) {} );

   // Every parameter this task calls out must move the key: SPFC's chosen
   // filter and resolved QE curve, MGC's database list, and GraXpert's
   // enabled flag and smoothing value.
   check( "buildStageKeys changes on SPFC filter",
          finalKeyWith( function( p ) { p.spfc.filter = "Astronomik L-3"; } ) != baseFinal, true );
   check( "buildStageKeys changes on SPFC QE curve",
          finalKeyWith( function( p ) { p.spfc.qe = "Ideal QE curve"; } ) != baseFinal, true );
   check( "buildStageKeys changes on MGC database list",
          finalKeyWith( function( p ) { p.mgc.marsFiles = [ "/mars/a.bin" ]; } ) != baseFinal, true );
   check( "buildStageKeys changes on GraXpert enabled flag",
          finalKeyWith( function( p ) { p.graxpert.enabled = false; } ) != baseFinal, true );
   check( "buildStageKeys changes on GraXpert smoothing",
          finalKeyWith( function( p ) { p.graxpert.smoothing = 0.9; } ) != baseFinal, true );
   // ...and an unrelated field left untouched still reproduces the same key
   check( "buildStageKeys unchanged params reproduce the same key",
          finalKeyWith( function( p ) {} ), baseFinal );

   // register chains from the PREVIOUS stage's key plus the reference
   // view's fingerprint -- a different L must invalidate it.
   var regSame = Pipeline.buildStageKeys( baseFinal, { register: { ref: "fp-L-v1" } } );
   check( "buildStageKeys register is the only stage when only register is given",
          regSame.map( function( s ) { return s.stage; } ), [ "register" ] );
   var regOtherRef = Pipeline.buildStageKeys( baseFinal, { register: { ref: "fp-L-v2" } } );
   check( "buildStageKeys register invalidates on a different reference fingerprint",
          regSame[0].key != regOtherRef[0].key, true );

   // narrowband channels (H/S/O) go straight from source to register --
   // no solve/spfc/mgc/graxpert entries at all.
   check( "buildStageKeys no applicable stages yields an empty chain",
          Pipeline.buildStageKeys( "src-H", {} ), [] );

   // an upstream change (e.g. a different SPFC filter) must still be felt
   // all the way through to the chained register key, exactly as it is
   // through the rest of the chain -- this is the "silently wrong reuse"
   // failure mode the whole cache exists to avoid.
   var upstreamDefault = finalKeyWith( function( p ) {} );
   var upstreamChanged = finalKeyWith( function( p ) { p.spfc.filter = "Different Filter"; } );
   var regFromDefault = Pipeline.buildStageKeys( upstreamDefault,
                            { register: { ref: "fp-L" } } )[0].key;
   var regFromChanged = Pipeline.buildStageKeys( upstreamChanged,
                            { register: { ref: "fp-L" } } )[0].key;
   check( "buildStageKeys upstream SPFC change propagates to register",
          regFromDefault != regFromChanged, true );

   // composite stages are part of the chain, in order, after the per-channel ones.
   // The composite TAIL -- sharpening, noise reduction, and the whole palette --
   // is cached too. It used to be excluded, which meant a repeat run redid a
   // SyQon star reduction and rebuilt every palette from scratch every time.
   check( "STAGE_ORDER includes composite stages",
          Pipeline.STAGE_ORDER,
          [ "solve", "spfc", "mgc", "graxpert",
            "aberration", "register",
            "combine", "solveRGB", "spfcRGB", "spccRGB",
            "sharpenRGB", "extractRGB", "stretchRGB", "denoiseRGB",
            "paletteCombine", "paletteSpcc", "paletteNorm",
            "paletteSharpen", "paletteExtract", "paletteStretch",
            "paletteDenoise",
            "extractL", "stretchL" ] );

   /*
    * The stretch acts on the STARLESS plate, after extraction: the stars were
    * already stretched inside the extraction stage, from their own clone, so
    * the two plates carry independent transforms.
    */
   check( "RGB is stretched after extraction, before denoising",
          Pipeline.STAGE_ORDER.indexOf( "extractRGB" ) <
          Pipeline.STAGE_ORDER.indexOf( "stretchRGB" ) &&
          Pipeline.STAGE_ORDER.indexOf( "stretchRGB" ) <
          Pipeline.STAGE_ORDER.indexOf( "denoiseRGB" ), true );
   check( "a palette is stretched after extraction, before denoising",
          Pipeline.STAGE_ORDER.indexOf( "paletteExtract" ) <
          Pipeline.STAGE_ORDER.indexOf( "paletteStretch" ) &&
          Pipeline.STAGE_ORDER.indexOf( "paletteStretch" ) <
          Pipeline.STAGE_ORDER.indexOf( "paletteDenoise" ), true );
   check( "L is stretched after it is split",
          Pipeline.STAGE_ORDER.indexOf( "extractL" ) <
          Pipeline.STAGE_ORDER.indexOf( "stretchL" ), true );

   check( "no stretch stages when the stretch is off",
          Pipeline.stretchParams( { stretch: false }, true ), null );
   check( "stretch params carry the target, linkage and linear-keeping",
          Pipeline.stretchParams( { stretch: true }, true ),
          { target: Steps.STRETCH_SKY_TARGET, linked: true, keepLinear: false } );

   /*
    * The linear composite is stored as a COMPANION of the stretch stage,
    * because the stretch overwrites its input in place -- a plain clone would
    * vanish the moment the stage came back from cache, which is how the stars
    * frames were lost once already. The companion is only created when asked
    * for, or every run would write a second full-size image nobody wants.
    */
   function stretchEntry( keep )
   {
      return Pipeline.buildStageKeys( "s", { stretchRGB:
                Pipeline.stretchParams( { stretch: true, keepLinear: keep }, true ) } )[0];
   }
   check( "keeping the linear copy marks a companion on the stretch stage",
          stretchEntry( true ).companion, "linear" );
   check( "not keeping it marks no companion",
          stretchEntry( false ).companion === undefined, true );
   check( "toggling it changes the stretch key, so a cached entry cannot be reused",
          stretchEntry( true ).key != stretchEntry( false ).key, true );
   check( "extraction stages still carry their stars companion",
          Pipeline.buildStageKeys( "s", { extractRGB: Pipeline.starExtractionParams(
                     { starTool: "StarNet2", stretch: true } ) } )[0].companion,
          "stars" );
   check( "L is not given a linear companion",
          Pipeline.STAGE_COMPANIONS.stretchL === undefined, true );
   check( "linked and unlinked stretches key differently",
          Pipeline.buildStageKeys( "s", { stretchRGB: Pipeline.stretchParams( { stretch: true }, true ) } )[0].key !=
          Pipeline.buildStageKeys( "s", { stretchRGB: Pipeline.stretchParams( { stretch: true }, false ) } )[0].key,
          true );

   /*
    * L is the registration reference. Splitting it before registration
    * hands StarAlignment a starless reference, which reports "0 stars
    * found" and fails every channel -- observed on real data before this
    * was moved. L is also the reference the clean white balance is
    * measured against, so its split comes after every consumer of it.
    */
   check( "L is split after registration, not before",
          Pipeline.STAGE_ORDER.indexOf( "extractL" ) >
          Pipeline.STAGE_ORDER.indexOf( "register" ), true );
   check( "L is split after the composites that reference it",
          Pipeline.STAGE_ORDER.indexOf( "extractL" ) >
          Pipeline.STAGE_ORDER.indexOf( "paletteDenoise" ), true );

   // A tail stage is the LAST link of its chain, so changing it must rehash
   // only itself -- this is the property that made excluding them pointless.
   var tailBase = "src-rgb";
   var tailA = Pipeline.buildStageKeys( tailBase,
                  { combine: {}, sharpenRGB: { tool: "t", stars: "high", detail: null } } );
   var tailB = Pipeline.buildStageKeys( tailBase,
                  { combine: {}, sharpenRGB: { tool: "t", stars: "low", detail: null } } );
   check( "changing star reduction leaves the combine key untouched",
          tailA[0].key, tailB[0].key );
   check( "changing star reduction does change the sharpen key",
          tailA[1].key != tailB[1].key, true );

   // A stage that would do nothing is left out of the chain entirely, so
   // turning noise reduction off must not shift any other key.
   check( "no sharpen params when no tool is chosen",
          Pipeline.compositeSharpenParams( { sharpenTool: "none",
                                             starReduction: "high" } ), null );
   check( "no sharpen params when every level is off",
          Pipeline.compositeSharpenParams( { sharpenTool: "SyQon Parallax",
                                             starReduction: "none",
                                             detailLevel: "none" } ), null );
   check( "sharpen params carry tool, levels AND the amounts they mean",
          Pipeline.compositeSharpenParams( { sharpenTool: "SyQon Parallax",
                                             starReduction: "high",
                                             detailLevel: "medium" } ),
          { tool: "SyQon Parallax", stars: "high", detail: "medium",
            starsAmount: 5, detailAmount: 0.8 } );
   check( "no denoise params when the tool is off",
          Pipeline.compositeDenoiseParams( { noiseTool: "none",
                                             noiseLevel: "medium" } ), null );
   check( "denoise params carry tool, level, stretched flag AND the amount",
          Pipeline.compositeDenoiseParams( { noiseTool: "SyQon Prism",
                                             noiseLevel: "medium" } ),
          { tool: "SyQon Prism", level: "medium", stretched: false,
            amount: 0.85 } );

   /*
    * BlurXTerminator REFUSES sharpen_stars above 0.70 -- probed against the
    * process itself, which reports "Valid range is from 0 to 0.7". The
    * ladder carried 0.75, so "high" star reduction threw every time, the
    * throw was swallowed as a tolerated sharpening failure, and the plate
    * came out with its stars untouched.
    *
    * sharpen_nonstellar has no such ceiling (it accepts 1.00), so the two
    * ladders are deliberately asymmetric and only the stars one is capped.
    */
   check( "no BXT star level exceeds what the process accepts",
          ( function()
            {
               var over = [];
               for ( var k in Steps.SHARPEN_LEVELS.bxt.stars )
                  if ( Steps.SHARPEN_LEVELS.bxt.stars[k] > 0.70 )
                     over.push( k + "=" + Steps.SHARPEN_LEVELS.bxt.stars[k] );
               return over.join( "," );
            } )(), "" );
   check( "high star reduction is the maximum the process allows",
          Steps.SHARPEN_LEVELS.bxt.stars.high, 0.70 );
   check( "detail sharpening is not capped with it",
          Steps.SHARPEN_LEVELS.bxt.detail.high, 0.75 );

   /*
    * The bug these guard against: a level LABEL is a name for a number
    * that lives in a ladder in Steps.js. Remap the ladder and "medium"
    * still reads "medium", so a key built from the label alone keeps
    * serving pixels produced by the OLD number as if they were current --
    * silently, with no way to tell from the cache that anything moved.
    *
    * Each test remaps a ladder, asserts the key moved, and puts it back.
    */
   var savedDetail = Steps.SHARPEN_LEVELS.syqon.detail.medium;
   try
   {
      var sharpCfg = { sharpenTool: "SyQon Parallax", starReduction: "none",
                       detailLevel: "medium" };
      var keyBefore = Pipeline.buildStageKeys( "s",
         { sharpenRGB: Pipeline.compositeSharpenParams( sharpCfg ) } )[0].key;
      Steps.SHARPEN_LEVELS.syqon.detail.medium = 0.4;
      var keyAfter = Pipeline.buildStageKeys( "s",
         { sharpenRGB: Pipeline.compositeSharpenParams( sharpCfg ) } )[0].key;
      check( "remapping the sharpen ladder invalidates the cached stage",
             keyBefore != keyAfter, true );
   }
   finally { Steps.SHARPEN_LEVELS.syqon.detail.medium = savedDetail; }

   var savedPrism = Steps.NOISE_LEVELS.prism.medium;
   try
   {
      var nrCfg = { noiseTool: "SyQon Prism", noiseLevel: "medium" };
      var nrBefore = Pipeline.buildStageKeys( "s",
         { denoiseRGB: Pipeline.compositeDenoiseParams( nrCfg ) } )[0].key;
      Steps.NOISE_LEVELS.prism.medium = 0.5;
      var nrAfter = Pipeline.buildStageKeys( "s",
         { denoiseRGB: Pipeline.compositeDenoiseParams( nrCfg ) } )[0].key;
      check( "remapping the noise ladder invalidates the cached stage",
             nrBefore != nrAfter, true );
   }
   finally { Steps.NOISE_LEVELS.prism.medium = savedPrism; }

   // NXT carries two numbers; both have to reach the key.
   var savedNxt = Steps.NOISE_LEVELS.nxt.medium.detail;
   try
   {
      var nxtCfg = { noiseTool: Steps.NR_TOOL_NXT, noiseLevel: "medium" };
      var nxtBefore = Pipeline.buildStageKeys( "s",
         { denoiseRGB: Pipeline.compositeDenoiseParams( nxtCfg ) } )[0].key;
      Steps.NOISE_LEVELS.nxt.medium.detail = 0.99;
      var nxtAfter = Pipeline.buildStageKeys( "s",
         { denoiseRGB: Pipeline.compositeDenoiseParams( nxtCfg ) } )[0].key;
      check( "remapping NXT's detail figure invalidates the cached stage",
             nxtBefore != nxtAfter, true );
   }
   finally { Steps.NOISE_LEVELS.nxt.medium.detail = savedNxt; }

   // Resolution itself: the label maps to what actually reaches the tool.
   check( "sharpen amount resolves through the tool's own ladder",
          Steps.sharpenAmountFor( "SyQon Parallax", "detail", "medium" ), 0.8 );
   check( "sharpen amount differs per tool for the same label",
          Steps.sharpenAmountFor( "BlurXTerminator", "detail", "medium" ), 0.50 );
   check( "sharpen amount is null at level none",
          Steps.sharpenAmountFor( "SyQon Parallax", "detail", "none" ), null );
   check( "sharpen amount is null for an unknown tool",
          Steps.sharpenAmountFor( "Nonesuch", "detail", "medium" ), null );
   check( "noise amount resolves for Prism",
          Steps.noiseAmountFor( "SyQon Prism", "low" ), 0.50 );
   check( "noise amount is null for an unknown level",
          Steps.noiseAmountFor( "SyQon Prism", "nonsense" ), null );

   /*
    * Lazy source loading. A cached run must not open the master at all:
    * the key chain is built from a stat and the file's header, and the
    * pixels are wanted only when a stage actually has to run.
    *
    * processChain cannot be exercised against real images here, so these
    * drive it with a fake channel, fake runners and a stubbed Cache, and
    * assert the DECISION -- how many times the loader was called.
    */
   var realLookup = Cache.lookup, realLoad = Cache.load, realStore = Cache.store;
   try
   {
      var fakeReg = { add: function() {}, forget: function() {} };
      var fakeChain = [ { stage: "solve", key: "k1", params: {} },
                        { stage: "spfc",  key: "k2", params: {} } ];
      function driveChain( cachedKeys )
      {
         var loads = 0, ran = [];
         Cache.lookup = function( k ) { return cachedKeys[k] ? { key: k } : null; };
         Cache.load = function( k, id )
         {
            return { mainView: { id: id }, hasAstrometricSolution: true,
                     forceClose: function() {} };
         };
         Cache.store = function() {};
         var chan = { key: "L", window: null, view: null,
                      load: function()
                      {
                         loads++;
                         this.window = { mainView: { id: "fake" },
                                         forceClose: function() {} };
                         this.view = this.window.mainView;
                         return this.window;
                      } };
         var runners = {
            solve: function( c ) { ran.push( "solve" ); },
            spfc:  function( c ) { ran.push( "spfc" ); }
         };
         Pipeline.processChain( chan, fakeChain, { useCache: true },
                                fakeReg, runners );
         return { loads: loads, ran: ran.join( "," ) };
      }

      // everything cached: the file must never be opened, nothing must run
      var allHit = driveChain( { k1: true, k2: true } );
      check( "a fully cached chain never opens the source", allHit.loads, 0 );
      check( "a fully cached chain runs no stage", allHit.ran, "" );

      // only the LAST stage cached: earlier stages are superseded, so the
      // source is still not needed
      var tailHit = driveChain( { k2: true } );
      check( "a chain cached at its last stage never opens the source",
             tailHit.loads, 0 );

      // nothing cached: the source is opened exactly once, for stage 0
      var allMiss = driveChain( {} );
      check( "an uncached chain opens the source once", allMiss.loads, 1 );
      check( "an uncached chain runs every stage", allMiss.ran, "solve,spfc" );

      // cached at the FIRST stage only: the second still has to run, but on
      // the loaded cache window -- not on the source
      var headHit = driveChain( { k1: true } );
      check( "a chain cached at its first stage does not open the source",
             headHit.loads, 0 );
      check( "a chain cached at its first stage still runs the rest",
             headHit.ran, "spfc" );
   }
   finally
   {
      Cache.lookup = realLookup; Cache.load = realLoad; Cache.store = realStore;
   }

   /*
    * Whether a cached entry is usable at all. Asked once, before anything
    * executes, so the chain's hit index is settled up front -- an
    * extraction entry missing its stars frame used to be discovered
    * halfway through the run.
    */
   var realLookup2 = Cache.lookup, realLookupComp = Cache.lookupCompanion;
   try
   {
      Cache.lookup = function( k )
      {
         return ( k == "whole" || k == "half" ) ? ( "/cache/" + k ) : null;
      };
      Cache.lookupCompanion = function( k, n )
      {
         return ( k == "whole" ) ? ( "/cache/" + k + "." + n ) : null;
      };
      check( "an ordinary entry is complete when its file is there",
             Pipeline.cacheEntryComplete( { stage: "solve", key: "whole" } ), true );
      check( "an entry with no file is not complete",
             Pipeline.cacheEntryComplete( { stage: "solve", key: "absent" } ), false );
      check( "an extraction entry with both halves is complete",
             Pipeline.cacheEntryComplete( { stage: "extractL", key: "whole",
                                            companion: "stars" } ), true );
      check( "an extraction entry missing its stars frame is half-written",
             Pipeline.cacheEntryComplete( { stage: "extractL", key: "half",
                                            companion: "stars" } ), false );
      check( "no entry at all is not complete",
             Pipeline.cacheEntryComplete( null ), false );
   }
   finally
   {
      Cache.lookup = realLookup2; Cache.lookupCompanion = realLookupComp;
   }

   /*
    * ...and what processChain then does with a half-written extraction
    * entry: recompute the stage and store it, rather than continue with no
    * stars image. Driven with the same fakes as above.
    */
   var rLookup = Cache.lookup, rLookupComp = Cache.lookupCompanion,
       rLoad = Cache.load, rLoadComp = Cache.loadCompanion,
       rStore = Cache.store, rStoreComp = Cache.storeCompanion;
   try
   {
      var extractChain = [ { stage: "extractL", key: "kx", params: {},
                             companion: "stars" } ];
      function driveExtraction( companionPresent )
      {
         var ran = [], stored = [];
         var starsWindow = { mainView: { id: "stars" }, forceClose: function() {} };
         Cache.lookup = function( k ) { return "/cache/" + k; };
         Cache.lookupCompanion = function()
         {
            return companionPresent ? "/cache/kx.stars.xisf" : null;
         };
         Cache.load = function( k, id )
         {
            return { mainView: { id: id }, hasAstrometricSolution: true,
                     forceClose: function() {} };
         };
         Cache.loadCompanion = function()
         {
            return companionPresent ? starsWindow : null;
         };
         Cache.store = function( k ) { stored.push( k ); };
         Cache.storeCompanion = function( k, n ) { stored.push( k + "." + n ); };
         var chan = { key: "L", view: null,
                      window: { mainView: { id: "src" }, forceClose: function() {} } };
         var runners = { extractL: function( c ) { ran.push( "extractL" ); } };
         Pipeline.processChain( chan, extractChain, { useCache: true },
                                { add: function() {}, forget: function() {} },
                                runners );
         return { ran: ran.join( "," ), stored: stored.join( "," ),
                  starsRecovered: chan.stars === starsWindow };
      }
      var whole = driveExtraction( true );
      check( "a complete extraction entry is reused, not run", whole.ran, "" );
      check( "and its stars frame comes back with it",
             whole.starsRecovered, true );
      var half = driveExtraction( false );
      check( "a half-written extraction entry is recomputed instead",
             half.ran, "extractL" );
      check( "and the recomputed result is stored once", half.stored, "kx" );

      /*
       * THE BUG THIS PINS. A chain of more than one stage whose LAST
       * cached entry is half-written.
       *
       * The stages before it are skipped as superseded -- correct only if
       * the later entry is actually usable. When it is not, the recompute
       * ran the stage against the UNPROCESSED SOURCE rather than against
       * the previous stage's output, and stored that wrong result in the
       * cache under the right key, where every later run would serve it.
       *
       * The fix is to choose the latest COMPLETE entry as the hit, so the
       * earlier stage is reused and the recompute chains from it.
       */
      function driveTwoStage( companionPresent )
      {
         var sawInput = [], stored = [];
         var starsWindow = { mainView: { id: "stars" }, forceClose: function() {} };
         var chain = [ { stage: "mgc", key: "k1", params: {}, companion: null },
                       { stage: "extractL", key: "k2", params: {},
                         companion: "stars" } ];
         Cache.lookup = function( k ) { return "/cache/" + k; };
         Cache.lookupCompanion = function( k )
         {
            // only the LAST stage's companion is in question
            return ( k == "k2" && !companionPresent ) ? null : "/cache/x.stars.xisf";
         };
         Cache.load = function( k, id )
         {
            return { mainView: { id: "from_" + k }, hasAstrometricSolution: true,
                     forceClose: function() {} };
         };
         Cache.loadCompanion = function( k )
         {
            return ( k == "k2" && !companionPresent ) ? null : starsWindow;
         };
         Cache.store = function( k ) { stored.push( k ); };
         Cache.storeCompanion = function( k, n ) { stored.push( k + "." + n ); };
         var chan = { key: "L", view: null,
                      window: { mainView: { id: "SOURCE" },
                                forceClose: function() {} } };
         var runners = { mgc: function( c ) { sawInput.push( "mgc:" + c.window.mainView.id ); },
                         extractL: function( c )
                         {
                            sawInput.push( "extractL:" + c.window.mainView.id );
                         } };
         Pipeline.processChain( chan, chain, { useCache: true },
                                { add: function() {}, forget: function() {} },
                                runners );
         return { saw: sawInput.join( "," ), stored: stored.join( "," ) };
      }
      var twoWhole = driveTwoStage( true );
      check( "a complete last entry means nothing runs", twoWhole.saw, "" );

      var twoHalf = driveTwoStage( false );
      check( "a half-written LAST entry recomputes only that stage",
             twoHalf.saw.indexOf( "mgc:" ) < 0, true );
      /*
       * The assertion that matters: the recomputed stage must see the
       * earlier CACHED stage's window, never the untouched source.
       */
      check( "and it runs against the previous stage's output, not the source",
             twoHalf.saw, "extractL:from_k1" );

      /*
       * The same hazard by a rarer route: the entry is complete at lookup
       * time but will not load, or loses its companion between the lookup
       * and the load. Falling back to an EARLIER entry is always safe --
       * an earlier stage's output is what the next stage expects as input
       * -- whereas recomputing from the source is the bug above.
       */
      function driveResolve( failMode )
      {
         var closed = [];
         var chain = [ { stage: "mgc", key: "k1", params: {}, companion: null },
                       { stage: "extractL", key: "k2", params: {},
                         companion: "stars" } ];
         Cache.lookup = function( k ) { return "/cache/" + k; };
         Cache.lookupCompanion = function() { return "/cache/x.stars.xisf"; };
         Cache.load = function( k, id )
         {
            if ( failMode == "load" && k == "k2" )
               return null;
            return { mainView: { id: "from_" + k }, hasAstrometricSolution: true,
                     forceClose: function() { closed.push( "from_" + k ); } };
         };
         Cache.loadCompanion = function( k )
         {
            if ( failMode == "companion" && k == "k2" )
               return null;
            return { mainView: { id: "stars" }, forceClose: function() {} };
         };
         Cache.store = function() {}; Cache.storeCompanion = function() {};
         var chan = { key: "L", view: null,
                      window: { mainView: { id: "SOURCE" },
                                forceClose: function() {} } };
         var saw = [];
         var runners = { mgc: function( c ) { saw.push( "mgc:" + c.window.mainView.id ); },
                         extractL: function( c )
                         { saw.push( "extractL:" + c.window.mainView.id ); } };
         Pipeline.processChain( chan, chain, { useCache: true },
                                { add: function() {}, forget: function() {} },
                                runners );
         return { saw: saw.join( "," ), closed: closed.join( "," ) };
      }
      var wontLoad = driveResolve( "load" );
      check( "an entry that will not load falls back to the earlier stage",
             wontLoad.saw, "extractL:from_k1" );
      var lostComp = driveResolve( "companion" );
      check( "so does one that loses its companion before loading",
             lostComp.saw, "extractL:from_k1" );
      /*
       * And the window opened for the rejected entry is closed rather
       * than left behind -- these are full-size plates.
       */
      check( "the rejected entry's window is not leaked",
             lostComp.closed, "from_k2" );
   }
   finally
   {
      Cache.lookup = rLookup; Cache.lookupCompanion = rLookupComp;
      Cache.load = rLoad; Cache.loadCompanion = rLoadComp;
      Cache.store = rStore; Cache.storeCompanion = rStoreComp;
   }

   // A channel already holding a window (a view source, or a chain whose
   // first stage creates one) has no loader and must pass through untouched.
   var noLoader = { key: "RGB", window: { mainView: { id: "x" } } };
   check( "ensureLoaded leaves an already-loaded channel alone",
          Pipeline.ensureLoaded( noLoader ).window.mainView.id, "x" );
   check( "ensureLoaded tolerates a channel with no loader at all",
          Pipeline.ensureLoaded( { key: "RGB", window: null } ).window, null );

   /*
    * Narrowband normalisation is switchable. Turning it off must drop the
    * stage from the chain entirely -- not carry it as a no-op that still
    * hashes and still stores a full-size copy of its own input -- and the
    * stage AFTER it must then chain from paletteSpcc, because its input
    * really is different pixels.
    */
   function palChainStages( normalize )
   {
      var params = {
         paletteCombine: {},
         paletteSpcc: { palette: "HSO", bandwidth: 3 }
      };
      if ( normalize )
         params.paletteNorm = { palette: "HSO" };
      params.paletteStretch = { target: 0.25, linked: true };
      var chain = Pipeline.buildStageKeys( "palsrc", params );
      var names = [];
      for ( var i = 0; i < chain.length; ++i )
         names.push( chain[i].stage );
      return { names: names.join( "," ), chain: chain };
   }
   var withNorm = palChainStages( true );
   var withoutNorm = palChainStages( false );
   check( "normalisation on puts paletteNorm in the chain",
          withNorm.names, "paletteCombine,paletteSpcc,paletteNorm,paletteStretch" );
   check( "normalisation off drops the stage entirely",
          withoutNorm.names, "paletteCombine,paletteSpcc,paletteStretch" );
   check( "the stage after it chains from a different key either way",
          withNorm.chain[withNorm.chain.length-1].key ==
          withoutNorm.chain[withoutNorm.chain.length-1].key, false );
   // ...while everything BEFORE it is untouched, so toggling the switch
   // does not re-run the combine or the calibration.
   check( "turning normalisation off does not invalidate SPCC",
          withNorm.chain[1].key, withoutNorm.chain[1].key );

   /*
    * Second stretch method. The method and the numbers it runs with both
    * have to reach the key, or a plate stretched one way would be served
    * for a run asking for the other.
    */
   check( "no stretch params when the stretch is off",
          Pipeline.stretchParams( { stretch: false,
                                    stretchMethod: Steps.STRETCH_METHOD_MAS }, true ),
          null );
   var mtfP = Pipeline.stretchParams( { stretch: true, keepLinear: false }, true );
   check( "the MTF stretch keys on its sky target, unchanged",
          mtfP, { target: Steps.STRETCH_SKY_TARGET, linked: true, keepLinear: false } );
   var masP = Pipeline.stretchParams( { stretch: true, keepLinear: false,
                                        stretchMethod: Steps.STRETCH_METHOD_MAS }, true );
   check( "the MAS stretch names its method", masP.method, Steps.STRETCH_METHOD_MAS );
   check( "the MAS stretch carries its own numbers",
          masP.mas.targetBackground, 0.15 );
   check( "switching stretch method changes the stage key",
          Pipeline.buildStageKeys( "s", { stretchRGB: mtfP } )[0].key ==
          Pipeline.buildStageKeys( "s", { stretchRGB: masP } )[0].key, false );
   // The values Francesco specified, asserted so an edit to the ladder is
   // a deliberate act rather than a silent drift.
   check( "MAS target background", Steps.MAS_PARAMETERS.targetBackground, 0.15 );
   check( "MAS aggressiveness", Steps.MAS_PARAMETERS.aggressiveness, 0.70 );
   check( "MAS dynamic range compression",
          Steps.MAS_PARAMETERS.dynamicRangeCompression, 0.40 );
   check( "MAS contrast recovery is on", Steps.MAS_PARAMETERS.contrastRecovery, true );
   check( "MAS contrast recovery intensity",
          Steps.MAS_PARAMETERS.contrastRecoveryIntensity, 1.00 );
   check( "MAS colour saturation is off", Steps.MAS_PARAMETERS.saturationEnabled, false );
   check( "MAS background ROI is off", Steps.MAS_PARAMETERS.backgroundROIEnabled, false );
   /*
    * scaleSeparation is an enum a script cannot read, so Loom must NOT
    * pretend to set it -- writing the dialog's "1024" would select a
    * different scale entirely.
    */
   check( "Loom does not set scaleSeparation",
          ( "scaleSeparation" in Steps.MAS_PARAMETERS ), false );

   /*
    * CLI progress parsing. The three SyQon binaries do not share a format,
    * and only Parallax/Prism's was handled -- so star extraction, the
    * longest stage in a run, moved no progress bar at all. Every string
    * below is real captured output, not an invention.
    */
   var pp = Steps.parseProgressLine( "[  2%] [Sharpen/classic] tile 1/64... (1/64)" );
   check( "parallax percent", pp.percent, 2 );
   check( "parallax text", pp.text, "[Sharpen/classic] tile 1/64..." );
   check( "parallax counter", [ pp.done, pp.total ], [ 1, 64 ] );
   var p0 = Steps.parseProgressLine( "[  0%] Loading image... (0/1)" );
   check( "parallax at zero", p0.percent, 0 );
   var ps = Steps.parseProgressLine( "[CLI] Progress: 32%" );
   check( "starless percent", ps.percent, 32 );
   check( "starless has no tile counter", [ ps.done, ps.total ], [ null, null ] );
   check( "starless at completion",
          Steps.parseProgressLine( "[CLI] Progress: 100%" ).percent, 100 );
   check( "a percentage with no counter still parses",
          Steps.parseProgressLine( "[ 50%] Doing something" ).percent, 50 );
   check( "ordinary output is not progress",
          Steps.parseProgressLine( "[CLI] License verified successfully." ), null );
   check( "an empty line is not progress", Steps.parseProgressLine( "" ), null );
   check( "null is not progress", Steps.parseProgressLine( null ), null );

   /*
    * Starless separates its updates with CARRIAGE RETURNS and emits one
    * newline at the very end, so a reader splitting on "\n" alone sees
    * nothing until the process exits.
    */
   var crRun = "\r[CLI] Progress: 32%\r[CLI] Progress: 64%\r[CLI] Progress: 100%\n";
   var crLines = crRun.split( /\r\n|\r|\n/ );
   var seen = [];
   for ( var cp = 0; cp < crLines.length; ++cp )
   {
      var one = Steps.parseProgressLine( crLines[cp] );
      if ( one != null ) seen.push( one.percent );
   }
   check( "CR-separated progress yields every update", seen, [ 32, 64, 100 ] );
   check( "splitting on newline alone would have found none",
          crRun.split( "\n" ).filter( function( l )
             { return Steps.parseProgressLine( l ) != null; } ).length, 0 );

   /*
    * Every operation must reach the Cancel window, not just the sixteen
    * checkAbort checkpoints. BXT/SXT/NXT/StarNet2 are in-process modules
    * whose percentages no script can read, so the operation name is the
    * only sign of life during the minutes they hold the main thread.
    */
   var savedStage = Util.reportStage;
   try
   {
      var staged = [];
      Util.reportStage = function( t ) { staged.push( t ); };
      /*
       * REAL_OPERATION, not Util.operation: silenceLogging() has replaced
       * the latter with a no-op for the duration of the suite, so calling
       * it here would assert on a stub and pass whatever the code did.
       */
      REAL_OPERATION( "aberration", "BlurXTerminator", null, "L" );
      REAL_OPERATION( "noise reduction", "SyQon Prism", "medium", "HSO" );
      check( "an operation announces itself to the window", staged.length, 2 );
      check( "the stage text names the operation, tool and target",
             staged[0], "aberration: BlurXTerminator → L" );
      check( "the level is included when there is one",
             staged[1], "noise reduction: SyQon Prism (medium) → HSO" );
   }
   finally { Util.reportStage = savedStage; }
   /*
    * Prism's pre-stretch target depends on whether its input is already
    * non-linear -- SyQon's fixed 0.15 when it is, a measured one when it is
    * not -- so the flag changes the pixels and has to be in the key.
    */
   check( "the stretched flag changes the denoise key",
          Pipeline.buildStageKeys( "s", { denoiseRGB: Pipeline.compositeDenoiseParams(
                     { noiseTool: "SyQon Prism", noiseLevel: "medium",
                       stretch: true } ) } )[0].key !=
          Pipeline.buildStageKeys( "s", { denoiseRGB: Pipeline.compositeDenoiseParams(
                     { noiseTool: "SyQon Prism", noiseLevel: "medium",
                       stretch: false } ) } )[0].key,
          true );
   check( "SyQon's published default is the one used on stretched input",
          Steps.PRISM_DEFAULT_TARGET, 0.15 );

   // Star extraction is a stage, between sharpening and noise reduction, so
   // the stars frame never sees the denoiser.
   check( "extraction sits between sharpening and noise reduction",
          Pipeline.STAGE_ORDER.indexOf( "sharpenRGB" ) <
          Pipeline.STAGE_ORDER.indexOf( "extractRGB" ) &&
          Pipeline.STAGE_ORDER.indexOf( "extractRGB" ) <
          Pipeline.STAGE_ORDER.indexOf( "denoiseRGB" ), true );
   check( "palette extraction sits between sharpening and noise reduction",
          Pipeline.STAGE_ORDER.indexOf( "paletteSharpen" ) <
          Pipeline.STAGE_ORDER.indexOf( "paletteExtract" ) &&
          Pipeline.STAGE_ORDER.indexOf( "paletteExtract" ) <
          Pipeline.STAGE_ORDER.indexOf( "paletteDenoise" ), true );

   // A name exactly at the column width must still be separated from the
   // next field: "star extraction" and "noise reduction" are both 15 chars.
   var opStar   = Util.operationLine( "star extraction", "SyQon Starless", null, "RGB" );
   var opNoise  = Util.operationLine( "noise reduction", "SyQon Prism", "medium", "RGB" );
   var opAberr  = Util.operationLine( "aberration", "SyQon Parallax", null, "R" );
   check( "a 15-character operation name is still separated from the tool",
          opStar.indexOf( "star extractionSyQon" ) < 0, true );
   check( "a 15-character operation name keeps the tool readable",
          opNoise.indexOf( "noise reductionSyQon" ) < 0, true );
   check( "a short operation name is unaffected",
          opAberr.indexOf( "aberration " ) >= 0, true );
   check( "the target column lines up across operation names",
          opStar.indexOf( "->" ), opAberr.indexOf( "->" ) );

   check( "no star params when no tool is chosen",
          Pipeline.starExtractionParams( { starTool: "none" } ), null );
   /*
    * The stars target and the colour recovery both change the pixels the
    * extraction stage produces, so both have to be in its key -- otherwise a
    * cached entry from a previous setting is served unchanged, which is
    * exactly what happened when the stars target moved and the old plates came
    * straight back out of cache.
    */
   check( "star params carry the tool",
          Pipeline.starExtractionParams( { starTool: "StarNet2" } ),
          { tool: "StarNet2", starsTarget: Steps.STRETCH_STARS_TARGET,
            stretchStars: false } );

   function exKey( cfg )
   {
      return Pipeline.buildStageKeys(
                "s", { extractRGB: Pipeline.starExtractionParams( cfg ) } )[0].key;
   }
   check( "the stars stretch itself changes the extraction key",
          exKey( { starTool: "StarNet2", stretch: true } ) !=
          exKey( { starTool: "StarNet2", stretch: false } ),
          true );

   // Extraction stages carry a companion marker so processChain stores and
   // reloads the stars frame with the stage.
   var exChain = Pipeline.buildStageKeys( "src-x",
                    { combine: {}, extractRGB: { tool: "StarNet2" } } );
   check( "extraction stage is marked as carrying a stars companion",
          exChain[1].companion, "stars" );
   check( "an ordinary stage carries no companion",
          exChain[0].companion === undefined, true );

   // Changing the star tool must not disturb anything upstream of it.
   var exOther = Pipeline.buildStageKeys( "src-x",
                    { combine: {}, extractRGB: { tool: "StarXTerminator" } } );
   check( "changing the star tool leaves the combine key untouched",
          exChain[0].key, exOther[0].key );
   check( "changing the star tool does change the extraction key",
          exChain[1].key != exOther[1].key, true );

   /*
    * Every window a run keeps must be removed from the Registry before
    * closeAll(), or it is closed moments before the naming pass. This has
    * now bitten twice: first the palettes, then every stars frame -- a run
    * produced L_starless / RGB_starless / HSO_starless and no stars plate
    * at all. The check is on the source, because the Registry interaction
    * needs a live PixInsight run to exercise.
    */
   var pipelineSrc = File.readTextFile( LOOM_DIR + "/lib/Pipeline.js" );
   var beforeClose = pipelineSrc.substring( 0, pipelineSrc.indexOf( "reg.closeAll();" ) );
   check( "L's stars frame is forgotten before closeAll",
          beforeClose.indexOf( "reg.forget( chans.L.stars )" ) >= 0, true );
   check( "the RGB stars frame is forgotten before closeAll",
          beforeClose.indexOf( "reg.forget( rgbStars )" ) >= 0, true );
   check( "palette stars frames are forgotten before closeAll",
          beforeClose.indexOf( "reg.forget( paletteWins[ps].stars )" ) >= 0, true );

   /*
    * The MARS folder is an override, not a requirement: a folder with no
    * .xmars in it must fall through to the automatic routes rather than
    * breaking a run that would otherwise work.
    */
   check( "an empty MARS folder setting finds nothing",
          Steps.marsDatabasesInDirectory( "" ).length, 0 );
   check( "a non-existent MARS folder finds nothing",
          Steps.marsDatabasesInDirectory( "/nonexistent/loom/mars" ).length, 0 );
   check( "a null MARS folder finds nothing",
          Steps.marsDatabasesInDirectory( null ).length, 0 );

   // The camera is read, never assumed.
   check( "qeCurve falls back for an unknown camera",
          Util.qeCurveNameForCamera( "Some Unknown Camera 9000" ), null );
   check( "qeCurve handles a missing INSTRUME",
          Util.qeCurveNameForCamera( null ), null );
   check( "qeCurve resolves a QHY268 as the IMX571 family",
          Util.qeCurveNameForCamera( "QHY268M" ), "Sony IMX411/455/461/533/571" );
   check( "qeCurve resolves an ASI183 independently",
          Util.qeCurveNameForCamera( "ZWO ASI183MM Pro" ), "Sony IMX183" );

   /* ---- deterministic stretch -------------------------------------- */

   // Acklam's approximation against known quantiles of the normal.
   check( "inverseNormalCDF(0.5) is 0",
          Math.abs( Steps.inverseNormalCDF( 0.5 ) ) < 1e-9, true );
   check( "inverseNormalCDF(0.975) is 1.959964",
          Math.abs( Steps.inverseNormalCDF( 0.975 ) - 1.959964 ) < 1e-5, true );
   check( "inverseNormalCDF(0.025) is -1.959964",
          Math.abs( Steps.inverseNormalCDF( 0.025 ) + 1.959964 ) < 1e-5, true );
   check( "inverseNormalCDF is antisymmetric about 0.5",
          Math.abs( Steps.inverseNormalCDF( 0.9 ) +
                    Steps.inverseNormalCDF( 0.1 ) ) < 1e-6, true );

   /*
    * z(N) is retained only as the record of a REJECTED rule. Verified against
    * the L master on 2026-09-15: the Gaussian assumption behind it is false
    * for a stacked, drizzled frame -- the darkest pixel sat 3.93 MADN below
    * the median where the model demanded 5.60, so the black point fell below
    * every real pixel and left ~10% of the output range empty.
    */
   var z92 = Steps.stretchSigmaForPixelCount( 11966*7678 );
   check( "the rejected Gaussian rule demanded more than 5 sigma",
          z92 > 5.5 && z92 < 5.7, true );
   check( "the real frame's darkest pixel was far short of that",
          3.93 < z92, true );

   // The rule in force: the black point is the image's own minimum.
   var sp = Steps.stretchParametersFrom( 1.44898040e-3, 8.43324524e-4 );
   check( "the sky lands exactly on the target",
          Math.abs( sp.skyOut - 0.25 ) < 1e-9, true );
   check( "the black point is the darkest pixel",
          sp.c0, 8.43324524e-4 );
   check( "the darkest pixel therefore maps to exactly zero",
          Steps.mtfApply( sp.m, 0 ), 0 );
   check( "no output range is wasted below the darkest pixel",
          Steps.mtfApply( sp.m, (8.43324524e-4 - sp.c0)/(1 - sp.c0) ) < 1e-12, true );

   // Same rule, a different frame: still exactly on target, different midtone.
   var sp2 = Steps.stretchParametersFrom( 1.53977694e-3, 1.13232352e-3 );
   check( "a different frame also lands exactly on the target",
          Math.abs( sp2.skyOut - 0.25 ) < 1e-9, true );
   check( "a different frame gets a different midtone", sp.m != sp2.m, true );

   // A negative minimum (possible after background subtraction) is floored.
   var sp4 = Steps.stretchParametersFrom( 1e-3, -5e-4 );
   check( "a negative minimum is floored at zero", sp4.c0, 0 );

   // An explicit target is honoured, so the stars path can differ later.
   var sp3 = Steps.stretchParametersFrom( 1.5398e-3, 8.4332e-4, 0.10 );
   check( "an explicit target is honoured",
          Math.abs( sp3.skyOut - 0.10 ) < 1e-9, true );

   // A flat frame has nothing to stretch; say so, don't return Inf.
   var threw = false;
   try { Steps.stretchParametersFrom( 0.5, 0.5 ); }
   catch ( e ) { threw = true; }
   check( "a flat frame is refused, not stretched", threw, true );

   /*
    * The stars target is DERIVED: for a fixed input level, the slope of the
    * transform at that level is maximised when the level maps to 0.5. That is
    * what decides whether a faint star survives the unscreen, so verify the
    * claim numerically rather than trusting it.
    */
   var argmaxOk = true, worstX = 0;
   var xs = [ 1e-6, 1e-5, 1e-4, 1e-3, 1e-2, 0.1, 0.3 ];
   for ( var xi = 0; xi < xs.length; ++xi )
   {
      var x0 = xs[xi], bestSlope = -1, bestOut = -1;
      for ( var ti = 1; ti < 100; ++ti )
      {
         var tt = ti/100;
         var mm = Steps.mtfMidtoneFor( x0, tt );
         var h = x0*1e-3;
         var slope = ( Steps.mtfApply( mm, x0+h ) - Steps.mtfApply( mm, x0-h ) )/( 2*h );
         if ( slope > bestSlope ) { bestSlope = slope; bestOut = tt; }
      }
      if ( Math.abs( bestOut - 0.5 ) > 0.02 ) { argmaxOk = false; worstX = x0; }
   }
   check( "slope at a level is maximised when that level maps to 0.5",
          argmaxOk, true );
   check( "the stars target is that argmax", Steps.STRETCH_STARS_TARGET, 0.5 );
   check( "the starless target is the autostretch convention",
          Steps.STRETCH_SKY_TARGET, 0.25 );
   check( "the stars target separates a faint star from sky more than the sky target",
          ( function()
            {
               var sky = 1.5e-3, star = sky + 3*1.25e-4, mn = 9.0e-4;
               function sep( t )
               {
                  var p = Steps.stretchParametersFrom( sky, mn, t );
                  var n = function( v ) { return ( v - p.c0 )/( 1 - p.c0 ); };
                  return Steps.mtfApply( p.m, n( star ) ) - Steps.mtfApply( p.m, n( sky ) );
               }
               return sep( 0.5 ) > sep( 0.25 );
            } )(), true );

   /*
    * A GRAYSCALE image is driven by HistogramTransformation's combined RGB/K
    * row, not row 0. Writing rows 0-2 for a mono plate is a silent no-op --
    * observed on L, which came back unstretched.
    */
   ( function()
   {
      var w = new ImageWindow( 64, 64, 1, 32, true, false,
                               Util.freeWindowId( "stretchprobe" ) );
      try
      {
         w.mainView.beginProcess( UndoFlag_NoSwapFile );
         for ( var y = 0; y < 64; ++y )
            for ( var x = 0; x < 64; ++x )
               w.mainView.image.setSample( 0.01 + 0.0001*x, x, y, 0 );
         w.mainView.endProcess();
         var before = w.mainView.image.median();
         Steps.applyStretch( w.mainView, { c0: 0, m: 0.05 }, false, "probe" );
         var after = w.mainView.image.median();
         check( "an unlinked stretch actually moves a mono image",
                after > before*2, true );
      }
      finally { try { w.forceClose(); } catch ( e ) {} }
   } )();

   /* ---- the dialogs must actually construct ---------------------------- */

   ( function()
   {
      function cfg()
      {
         // mirrors Loom.js defaultConfig(); the dialog is only ever handed a
         // complete config, so that is the contract being tested
         return { paths: { L:"", R:"", G:"", B:"", H:"", S:"", O:"" }, views: {},
                  savedList: "", filters: {}, useGraXpert: true, smoothing: 0.5,
                  validateOnly: false, keepWindowsOnError: false, palettes: [],
                  narrowbandBandwidth: 3.0, reduceHalos: false,
                  sharpenTool: "none", stretch: false, keepLinear: false,
                  exportDir: "", marsPath: "", starTool: "none",
                  noiseTool: "none", noiseLevel: "medium",
                  starReduction: "none", detailLevel: "none",
                  useCache: true, ignoreCache: false };
      }

      var built = true, err = "";
      var offState = null, onState = null;
      try
      {
         var a = new UI.SelectDialog( cfg() );
         offState = a.keepLinearCheck.enabled;
         var c2 = cfg(); c2.stretch = true;
         var b = new UI.SelectDialog( c2 );
         onState = b.keepLinearCheck.enabled;
      }
      catch ( e ) { built = false; err = String( e ); }
      check( "the main dialog constructs" + ( built ? "" : ": " + err ), built, true );

      /*
       * Set at construction, not only on the first toggle -- the initial state
       * was wrong for exactly that reason.
       */
      check( "stretch-dependent controls are disabled when the stretch is off",
             offState, false );
      check( "stretch-dependent controls are enabled when the stretch is on",
             onState, true );

      var cwOK = true, cwErr = "";
      try
      {
         var cw = new UI.CancelWindow;
         cw.setStage( "test" );
         cw.setProgress( 50, "test" );
         cwOK = ( cw.cancelled === false );
         cw.cancel();
      }
      catch ( e2 ) { cwOK = false; cwErr = String( e2 ); }
      check( "the Cancel window constructs and drives" + ( cwErr ? ": " + cwErr : "" ),
             cwOK, true );
   } )();

   check( "companion path is distinct from the stage path",
          Cache.companionPathFor( "abc", "stars" ) != Cache.pathFor( "abc" ), true );

   // A companion belongs to an entry and is not one: counting it would
   // report twice as many cached results as there are stages.
   var key40 = "0123456789abcdef0123456789abcdef01234567";
   check( "a stars companion is recognised as a companion",
          Cache.isCompanionFileName( key40 + ".stars.xisf" ), true );
   check( "a plain entry is not a companion",
          Cache.isCompanionFileName( key40 + ".xisf" ), false );
   check( "a json sidecar is not a companion",
          Cache.isCompanionFileName( key40 + ".json" ), false );

   // Two palettes from the same three channels must not collide.
   var palSHO = Cache.hash( "palette|SHO|kS|kH|kO|crop:0,0,10,10|halos:0" );
   var palHOO = Cache.hash( "palette|HOO|kS|kH|kO|crop:0,0,10,10|halos:0" );
   check( "palette name is part of the palette source key", palSHO != palHOO, true );

   // an RGB chain built from the composite stages keeps that order
   var rgbChain = Pipeline.buildStageKeys( "rgbsrc", {
      combine: {}, solveRGB: {}, spfcRGB: { filters: { R: "Baader R" } },
      spccRGB: { filters: { R: "Baader R" } } } );
   check( "rgb chain stages in order",
          [ rgbChain[0].stage, rgbChain[1].stage, rgbChain[2].stage, rgbChain[3].stage ],
          [ "combine", "solveRGB", "spfcRGB", "spccRGB" ] );

   // changing a contributing channel changes the whole composite chain
   var rgbA = Pipeline.buildStageKeys( "srcA", { combine: {}, solveRGB: {} } );
   var rgbB = Pipeline.buildStageKeys( "srcB", { combine: {}, solveRGB: {} } );
   check( "composite invalidates on a changed source",
          rgbA[1].key == rgbB[1].key, false );

   // and changing the filters invalidates SPCC but not the earlier combine
   var f1 = Pipeline.buildStageKeys( "s", { combine: {}, spccRGB: { filters: { R: "Baader R" } } } );
   var f2 = Pipeline.buildStageKeys( "s", { combine: {}, spccRGB: { filters: { R: "Astrodon R" } } } );
   check( "composite combine survives a filter change", f1[0].key, f2[0].key );
   check( "composite spcc invalidates on a filter change",
          f1[1].key == f2[1].key, false );

   // --- sharpening ---
   check( "sharpening stages are in STAGE_ORDER before register",
          Pipeline.STAGE_ORDER.indexOf( "aberration" ) <
          Pipeline.STAGE_ORDER.indexOf( "register" ), true );
   // Star reduction and detail are NOT cached stages: they act on the
   // finished composite, so they must not appear in the chain at all.
   check( "star reduction is not a cached stage",
          Pipeline.STAGE_ORDER.indexOf( "starreduction" ), -1 );
   check( "detail sharpening is not a cached stage",
          Pipeline.STAGE_ORDER.indexOf( "sharpen" ), -1 );
   check( "aberration is still a cached stage",
          Pipeline.STAGE_ORDER.indexOf( "aberration" ) >= 0, true );

   // level tables: none is always zero/off, and levels increase
   check( "bxt stars none is 0", Steps.SHARPEN_LEVELS.bxt.stars.none, 0.00 );
   check( "bxt stars low < high",
          Steps.SHARPEN_LEVELS.bxt.stars.low < Steps.SHARPEN_LEVELS.bxt.stars.high, true );
   check( "bxt detail none is 0", Steps.SHARPEN_LEVELS.bxt.detail.none, 0.00 );
   check( "bxt detail increases",
          Steps.SHARPEN_LEVELS.bxt.detail.low < Steps.SHARPEN_LEVELS.bxt.detail.medium &&
          Steps.SHARPEN_LEVELS.bxt.detail.medium < Steps.SHARPEN_LEVELS.bxt.detail.high, true );
   check( "syqon star levels are integers in 0..6",
          Steps.SHARPEN_LEVELS.syqon.stars.high <= 6 &&
          Steps.SHARPEN_LEVELS.syqon.stars.high == Math.round( Steps.SHARPEN_LEVELS.syqon.stars.high ), true );
   check( "syqon detail alpha stays in 0..1",
          Steps.SHARPEN_LEVELS.syqon.detail.high <= 1.0, true );

   // changing the sharpening TOOL must still invalidate aberration, which
   // remains a per-channel cached stage
   var shA = Pipeline.buildStageKeys( "src", { aberration: { tool: "BlurXTerminator" } } );
   var shC = Pipeline.buildStageKeys( "src", { aberration: { tool: "SyQon Parallax" } } );
   check( "aberration tool change invalidates", shA[0].key == shC[0].key, false );

   // a stage absent from params is skipped entirely rather than hashed empty
   var noSharp = Pipeline.buildStageKeys( "src", { aberration: { tool: "none" } } );
   check( "only the stages given are chained", noSharp.length, 1 );
   check( "and it is the aberration stage", noSharp[0].stage, "aberration" );

   // composite-level correction is a no-op unless a level asks for something
   check( "correctComposite does nothing without a tool",
          Steps.correctComposite( null, "none", "high", "high" ), false );
   check( "correctComposite does nothing at none/none",
          Steps.correctComposite( null, "SyQon Parallax", "none", "none" ), false );

   // --- SyQon CLI argument construction (Steps.syqonBuildArgs) ---
   // Each of the three stages must emit ONLY its own flag(s); the other
   // two stage flags must be absent, never present-but-zero.

   function syqonOpts( correctAberration, starReduction, sharpen )
   {
      return {
         inputFilePath:  "/tmp/agent-scratch/in.fits",
         outputFilePath: "/tmp/agent-scratch/out.fits",
         jsonInfoPath:   "/tmp/agent-scratch/out.json",
         correctAberration: correctAberration,
         starReduction:     starReduction,
         sharpen:            sharpen,
         tileSize: Steps.SYQON_TILE_SIZE,
         overlap:  Steps.SYQON_OVERLAP,
         pad:      Steps.SYQON_PAD
      };
   }

   // aberration-only call
   var abArgs = Steps.syqonBuildArgs( syqonOpts( true, 0, 0.0 ) );
   check( "syqon aberration: --i/--o present", abArgs[0] == "--i" && abArgs[2] == "--o", true );
   check( "syqon aberration: --correct-aberration present",
          abArgs.indexOf( "--correct-aberration" ) >= 0, true );
   check( "syqon aberration: --star-reduction absent",
          abArgs.indexOf( "--star-reduction" ), -1 );
   check( "syqon aberration: --sharpen absent", abArgs.indexOf( "--sharpen" ), -1 );
   check( "syqon aberration: --mode absent (mode pinned to classic)",
          abArgs.indexOf( "--mode" ), -1 );
   check( "syqon aberration: tile/overlap/pad present",
          abArgs.indexOf( "--tile" ) >= 0 && abArgs.indexOf( "--overlap" ) >= 0 &&
          abArgs.indexOf( "--pad" ) >= 0, true );
   check( "syqon aberration: tile value is SyQon default 512",
          abArgs[ abArgs.indexOf( "--tile" ) + 1 ], "512" );

   // star-reduction-only call
   var srArgs = Steps.syqonBuildArgs( syqonOpts( false, 5, 0.0 ) );
   check( "syqon star-reduction: --star-reduction present with value",
          srArgs[ srArgs.indexOf( "--star-reduction" ) + 1 ], "5" );
   check( "syqon star-reduction: --correct-aberration absent",
          srArgs.indexOf( "--correct-aberration" ), -1 );
   check( "syqon star-reduction: --sharpen absent", srArgs.indexOf( "--sharpen" ), -1 );

   // detail-sharpen-only call
   var shArgs = Steps.syqonBuildArgs( syqonOpts( false, 0, 0.9 ) );
   check( "syqon detail: --sharpen present with 2-decimal value",
          shArgs[ shArgs.indexOf( "--sharpen" ) + 1 ], "0.90" );
   check( "syqon detail: --correct-aberration absent",
          shArgs.indexOf( "--correct-aberration" ), -1 );
   check( "syqon detail: --star-reduction absent",
          shArgs.indexOf( "--star-reduction" ), -1 );

   // starReduction == 0 and sharpen == 0.0 must never emit their flags,
   // even when the caller passes them explicitly as zero
   var noneArgs = Steps.syqonBuildArgs( syqonOpts( false, 0, 0.0 ) );
   check( "syqon all-off: no stage flags at all",
          noneArgs.indexOf( "--correct-aberration" ) < 0 &&
          noneArgs.indexOf( "--star-reduction" ) < 0 &&
          noneArgs.indexOf( "--sharpen" ) < 0, true );

   // --json-info only appears when a path is supplied
   var noJsonArgs = Steps.syqonBuildArgs( { inputFilePath: "i", outputFilePath: "o",
      jsonInfoPath: "", correctAberration: true, starReduction: 0, sharpen: 0.0,
      tileSize: 512, overlap: 128, pad: 512 } );
   check( "syqon: --json-info omitted when path is empty",
          noJsonArgs.indexOf( "--json-info" ), -1 );

   // the three stage-building call sites in Steps.aberration/starReduction/
   // sharpenDetail must be mutually exclusive per call -- verified above by
   // construction, but also assert the level tables backing them once more
   // here so a future edit to SHARPEN_LEVELS.syqon is caught by this file.
   check( "syqon star level 'low' maps to 2", Steps.SHARPEN_LEVELS.syqon.stars.low, 2 );
   check( "syqon star level 'high' maps to 5", Steps.SHARPEN_LEVELS.syqon.stars.high, 5 );
   check( "syqon detail level 'medium' is Parallax's own default",
          Steps.SHARPEN_LEVELS.syqon.detail.medium, 0.8 );
   check( "syqon star level 'medium' is Parallax's own default",
          Steps.SHARPEN_LEVELS.syqon.stars.medium, 3 );
   check( "bxt 'medium' is BlurXTerminator's own default",
          Steps.SHARPEN_LEVELS.bxt.detail.medium, 0.50 );

   // ---- Prism pre-stretch target ------------------------------------------

   check( "corpus std bounds match the paper",
          [ Steps.PRISM_CORPUS_STD_MIN, Steps.PRISM_CORPUS_STD_MAX ], [ 0.0474, 0.1288 ] );
   check( "default is SyQon's own", Steps.PRISM_DEFAULT_TARGET, 0.15 );

   // MTF sanity: maps the background to the target, monotonic, fixed ends
   var x0p = 3.2561e-4;                      // measured on the real HSO composite
   var mp = Steps.mtfMidtoneFor( x0p, 0.35 );
   check( "MTF maps background to target",
          Math.abs( Steps.mtfApply( mp, x0p ) - 0.35 ) < 1e-9, true );
   check( "MTF monotonic",
          Steps.mtfApply( mp, 0.01 ) < Steps.mtfApply( mp, 0.10 ), true );
   check( "MTF fixes 1", Math.abs( Steps.mtfApply( mp, 1 ) - 1 ) < 1e-9, true );

   /*
    * The decision itself, on synthetic pixel sets so it needs no image.
    * A CONTRASTY set must keep SyQon's default; a FLAT one must be raised.
    */
   function flatSet( spread )
   {
      var px = [], n = 400;
      for ( var i = 0; i < n; ++i )
      {
         var v = x0p * ( 1 + spread*( i/(n-1) - 0.5 ) );
         px.push( [ v, v, v ] );
      }
      return px;
   }
   var contrasty = flatSet( 40.0 );      // wide spread about the background
   var veryFlat  = flatSet( 0.2 );       // almost no spread

   var sc = Steps.prismStretchStats( contrasty, x0p, 0.15 );
   var sf = Steps.prismStretchStats( veryFlat,  x0p, 0.15 );
   check( "a contrasty set has more spread than a flat one at the same target",
          sc.std > sf.std, true );
   check( "stretch stats are finite",
          isFinite( sc.std ) && isFinite( sf.std ) && sf.std >= 0, true );

   // raising the target must not reduce a flat image's contrast on the way up
   var f15 = Steps.prismStretchStats( veryFlat, x0p, 0.15 ).std;
   var f35 = Steps.prismStretchStats( veryFlat, x0p, 0.35 ).std;
   check( "raising the target lifts contrast for a flat image", f35 > f15, true );

   // ---- narrowband normalization -------------------------------------------

   // the module's palette enum, verified against a live instance
   check( "NBN palette HOO", Steps.NBN_PALETTES.HOO, 0 );
   check( "NBN palette SHO", Steps.NBN_PALETTES.SHO, 1 );
   check( "NBN palette HSO", Steps.NBN_PALETTES.HSO, 2 );

   // every palette Loom can build must map to one the module knows
   var names = Util.paletteNames();
   var mapped = true;
   for ( var pi = 0; pi < names.length; ++pi )
      if ( Steps.NBN_PALETTES[names[pi]] === undefined )
         mapped = false;
   check( "every Loom palette maps to a NBN palette", mapped, true );

   // an unknown palette is skipped, not fatal
   check( "unknown palette is skipped",
          Steps.narrowbandNormalize( null, "NOPE", "NOPE" ), false );

   // ---- noise reduction levels --------------------------------------------

   /*
    * MEDIUM is each tool's own default, read from the tool rather than chosen
    * here: NoiseXTerminator denoise 0.90 / detail 0.15, SyQon Prism 0.85.
    * Selecting Medium therefore reproduces running the tool by hand and
    * touching nothing. (This inverted an earlier policy where HIGH sat on the
    * defaults, which meant Medium ran everything below them.)
    */
   check( "nxt medium matches the tool default denoise",
          Steps.NOISE_LEVELS.nxt.medium.denoise, 0.90 );
   check( "nxt medium matches the tool default detail",
          Steps.NOISE_LEVELS.nxt.medium.detail, 0.15 );
   check( "prism medium matches the tool default strength",
          Steps.NOISE_LEVELS.prism.medium, 0.85 );
   check( "prism low is well below the default, not a softer default",
          Steps.NOISE_LEVELS.prism.low, 0.50 );
   check( "nxt levels increase with amount",
          Steps.NOISE_LEVELS.nxt.low.denoise < Steps.NOISE_LEVELS.nxt.medium.denoise &&
          Steps.NOISE_LEVELS.nxt.medium.denoise < Steps.NOISE_LEVELS.nxt.high.denoise, true );
   check( "nxt detail loosens as denoise rises",
          Steps.NOISE_LEVELS.nxt.low.detail > Steps.NOISE_LEVELS.nxt.high.detail, true );
   check( "prism levels increase with amount",
          Steps.NOISE_LEVELS.prism.low < Steps.NOISE_LEVELS.prism.medium &&
          Steps.NOISE_LEVELS.prism.medium < Steps.NOISE_LEVELS.prism.high, true );
   check( "denoise is a no-op with no tool",
          Steps.denoise( null, "none", "high" ), undefined );
   check( "denoise is a no-op with no level",
          Steps.denoise( null, Steps.NR_TOOL_NXT, "none" ), undefined );

   // ---- narrowband emission lines ----------------------------------------

   check( "Ha wavelength", Util.NARROWBAND_NM.H, 656.28 );
   check( "SII wavelength", Util.NARROWBAND_NM.S, 671.60 );
   check( "OIII wavelength", Util.NARROWBAND_NM.O, 500.70 );

   var wHSO = Util.paletteWavelengths( "HSO" );
   check( "HSO maps R,G,B to H,S,O",
          [ wHSO[0].channel, wHSO[1].channel, wHSO[2].channel ], [ "H", "S", "O" ] );
   check( "HSO wavelengths in R,G,B order",
          [ wHSO[0].nm, wHSO[1].nm, wHSO[2].nm ], [ 656.28, 671.60, 500.70 ] );

   var wSHO = Util.paletteWavelengths( "SHO" );
   check( "SHO wavelengths in R,G,B order",
          [ wSHO[0].nm, wSHO[1].nm, wSHO[2].nm ], [ 671.60, 656.28, 500.70 ] );

   // HOO feeds the same line to two channels; that is legitimate
   var wHOO = Util.paletteWavelengths( "HOO" );
   check( "HOO repeats OIII on G and B",
          [ wHOO[1].nm, wHOO[2].nm ], [ 500.70, 500.70 ] );

   check( "unknown palette has no wavelengths",
          Util.paletteWavelengths( "XYZ" ), null );

   // --- narrowband palettes ---
   check( "palette names", Util.paletteNames().sort(), [ "HOO", "HSO", "SHO" ] );
   check( "SHO maps S,H,O to R,G,B", Util.PALETTES.SHO, [ "S", "H", "O" ] );
   check( "HOO uses O twice", Util.PALETTES.HOO, [ "H", "O", "O" ] );
   check( "HOO needs only H and O", Util.paletteChannels( "HOO" ).sort(), [ "H", "O" ] );
   check( "SHO needs all three", Util.paletteChannels( "SHO" ).sort(), [ "H", "O", "S" ] );
   check( "unknown palette has no channels", Util.paletteChannels( "XYZ" ), null );
   check( "HOO buildable from H and O",
          Util.paletteMissing( "HOO", [ "H", "O" ] ), [] );
   check( "SHO reports missing S",
          Util.paletteMissing( "SHO", [ "H", "O" ] ), [ "S" ] );
   check( "HSO reports every missing channel",
          Util.paletteMissing( "HSO", [ "H" ] ).sort(), [ "O", "S" ] );

   // --- halo reduction: PSF matching maths ---
   check( "sigmaToReach quadrature",
          Math.round( Util.sigmaToReach( 5, 3 ) * 1e6 ) / 1e6, 4 );   // 3-4-5
   check( "sigmaToReach at target is 0", Util.sigmaToReach( 2.5, 2.5 ), 0 );
   check( "sigmaToReach when already wider is 0", Util.sigmaToReach( 2.0, 3.0 ), 0 );
   check( "sigmaToReach never NaN on bad input", Util.sigmaToReach( null, 3 ), 0 );
   // the real measured case: R 1.298 -> G 2.079
   check( "sigmaToReach R to G",
          Math.round( Util.sigmaToReach( 2.079, 1.298 ) * 1000 ) / 1000, 1.624 );
   check( "sigma/FWHM round trip",
          Math.round( Util.fwhmToSigma( Util.sigmaToFWHM( 1.7 ) ) * 1e9 ) / 1e9, 1.7 );

   // ---- master folder selection -------------------------------------------

   check( "autocrop detected in name", Util.isAutocropName( "m_drizzle_2x_autocrop.xisf" ), true );
   check( "autocrop absent", Util.isAutocropName( "m_drizzle_2x.xisf" ), false );
   check( "autocrop case insensitive", Util.isAutocropName( "M_AutoCrop.xisf" ), true );

   check( "masterLight by IMAGETYP", Util.isMasterLight( "anything.xisf", "Master Light" ), true );
   check( "masterFlat by IMAGETYP", Util.isMasterLight( "masterLight_x.xisf", "Master Flat" ), false );
   check( "masterLight by name when no IMAGETYP", Util.isMasterLight( "masterLight_a.xisf", null ), true );
   check( "masterFlat by name when no IMAGETYP", Util.isMasterLight( "masterFlat_a.xisf", "" ), false );

   check( "rank drizzle+autocrop", Util.masterVariantRank( "2x", true ), 3 );
   check( "rank drizzle only", Util.masterVariantRank( "2x", false ), 2 );
   check( "rank autocrop only", Util.masterVariantRank( "", true ), 1 );
   check( "rank plain", Util.masterVariantRank( "", false ), 0 );
   check( "rank plain with null drizzle", Util.masterVariantRank( null, false ), 0 );

   // variant beats recency
   var sel1 = Util.selectMasters( [
      { channel: "L", drizzle: "",   autocrop: false, mtime: 900, ref: "plain-newest" },
      { channel: "L", drizzle: "2x", autocrop: true,  mtime: 100, ref: "drizzle-autocrop-oldest" }
   ] );
   check( "variant outranks recency", sel1.L.ref, "drizzle-autocrop-oldest" );

   // recency breaks ties within a variant -- the _(1)/_(2) case
   var sel2 = Util.selectMasters( [
      { channel: "G", drizzle: "2x", autocrop: true, mtime: 100, ref: "gen0" },
      { channel: "G", drizzle: "2x", autocrop: true, mtime: 500, ref: "gen2" },
      { channel: "G", drizzle: "2x", autocrop: true, mtime: 300, ref: "gen1" }
   ] );
   check( "newest wins within a variant", sel2.G.ref, "gen2" );

   // one winner per channel, independently
   var sel3 = Util.selectMasters( [
      { channel: "R", drizzle: "",   autocrop: false, mtime: 1, ref: "r-plain" },
      { channel: "B", drizzle: "2x", autocrop: false, mtime: 1, ref: "b-drizzle" },
      { channel: "R", drizzle: "",   autocrop: true,  mtime: 1, ref: "r-autocrop" }
   ] );
   check( "per-channel winners", [ sel3.R.ref, sel3.B.ref ], [ "r-autocrop", "b-drizzle" ] );
   check( "rank attached to winner", sel3.B.rank, 2 );

   // ---- filename pre-parse (real WBPP names) ------------------------------

   var pn1 = Util.parseMasterName( "masterLight_BIN-1_6248x4176_EXPOSURE-60.00s_FILTER-L_mono_drizzle_2x_(1)_autocrop.xisf" );
   check( "parse filter L", pn1.channel, "L" );
   check( "parse drizzle 2x", pn1.drizzle, "2x" );
   check( "parse autocrop true", pn1.autocrop, true );

   var pn2 = Util.parseMasterName( "masterLight_BIN-1_6248x4176_EXPOSURE-180.00s_FILTER-O_mono_fastIntegration.xisf" );
   check( "parse filter O", pn2.channel, "O" );
   check( "parse no drizzle", pn2.drizzle, "" );
   check( "parse no autocrop", pn2.autocrop, false );

   var pn3 = Util.parseMasterName( "masterLight_BIN-1_6248x4176_EXPOSURE-60.00s_FILTER-B_mono_(2).xisf" );
   check( "parse filter B with generation", pn3.channel, "B" );

   check( "parse returns null without a FILTER token",
          Util.parseMasterName( "masterFlat_something_else.xisf" ), null );
   check( "parse null on empty", Util.parseMasterName( "" ), null );

   // the pre-parse must agree with the ranking it feeds
   check( "parsed name ranks as drizzle+autocrop",
          Util.masterVariantRank( pn1.drizzle, pn1.autocrop ), 3 );
   check( "parsed plain name ranks 0",
          Util.masterVariantRank( pn2.drizzle, pn2.autocrop ), 0 );

   // entries with no recognised channel are ignored
   var sel4 = Util.selectMasters( [
      { channel: null, drizzle: "2x", autocrop: true, mtime: 9, ref: "nofilter" },
      { channel: "O",  drizzle: "",   autocrop: false, mtime: 1, ref: "o-plain" }
   ] );
   check( "unchannelled ignored", Object.keys( sel4 ).sort(), [ "O" ] );
   check( "empty input", Object.keys( Util.selectMasters( [] ) ).length, 0 );

}

function main()
{
   var aborted = false;
   silenceLogging();
   try { runTests(); }
   catch ( e )
   {
      aborted = true;
      FAILURES.push( "EXCEPTION: " + e.toString() );
   }
   finally { restoreLogging(); }

   var status = ( FAILURES.length == 0 ? "PASS" : "FAIL" );
   if ( aborted )
      status += " ABORTED after " + TESTS_RUN + " checks";

   var summary = status +
                 " " + TESTS_RUN + " run, " + FAILURES.length + " failed\n" +
                 FAILURES.join( "\n" ) + "\n";

   console.writeln( summary );

   if ( !File.directoryExists( "/tmp/agent-scratch" ) )
      File.createDirectory( "/tmp/agent-scratch", true );
   var f = new File;
   f.createForWriting( RESULT_FILE );
   f.outText( summary );
   f.close();
}

main();
