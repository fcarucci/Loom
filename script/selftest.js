#engine v8

#feature-id LoomSelfTest : Scripts > LoomSelfTest

#include <pjsr/DataType.jsh>
#include <pjsr/FrameStyle.jsh>
#include <pjsr/StdButton.jsh>
#include <pjsr/StdCursor.jsh>
#include <pjsr/StdIcon.jsh>
#include <pjsr/TextAlign.jsh>
#include <pjsr/ColorSpace.jsh>
#include <pjsr/SampleType.jsh>
#include <pjsr/UndoFlag.jsh>

#include "lib/Util.js"
#include "lib/Cache.js"
#include "lib/Psb.js"
#include "lib/Steps.js"
#include "lib/Frames.js"
#include "lib/Fly.js"
#include "lib/Sky.js"
#include "lib/Render.js"
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

/*
 * FrameSelector.js is the entry point for the second script, so it ends by
 * calling main(). Two defines keep including it here from doing anything
 * except defining functions: one suppresses that call, the other tells it
 * the libraries above are already loaded, since the preprocessor does not
 * dedupe an #include and re-running them would reset every namespace.
 *
 * It is included for the same reason UI.js is -- the assertions below call
 * into it, and a suite that cannot construct the thing it tests reports
 * hundreds of passes while the script will not load at all.
 */
#define LOOM_LIBS_INCLUDED 1
#define LOOM_FRAME_SELECTOR_UNDER_TEST 1
#include "FrameSelector.js"
#define LOOM_FLY_UNDER_TEST 1
#include "FlyThrough.js"

#define RESULT_FILE "/tmp/agent-scratch/lhso-selftest.txt"

/*
 * This script's own directory, so the source-level checks below do not
 * carry a hardcoded personal path.
 */
var LOOM_DIR = File.extractDirectory( #__FILE__ );

/*
 * Some assertions cannot run outside PixInsight: they ask whether a
 * process module is installed, whether the ImageSolver engine an
 * #include pulled in is in scope, or they open windows and construct
 * dialogs. ci/run-tests.js runs everything else under node, on every
 * push, and sets LOOM_NODE_HARNESS to say so.
 *
 * These are SKIPPED there, never faked. A dialog built against fake
 * widgets proves only that the fakes agree with each other, and two real
 * dialog breakages reached the user precisely because nothing built them
 * for real.
 */
var IN_PIXINSIGHT = ( typeof LOOM_NODE_HARNESS == "undefined" );

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

/*
 * Where the suite has got to, on disk, so a run that stops -- a modal box,
 * a loop that never ends -- says which check it stopped after rather than
 * leaving an idle PixInsight and no result.
 */
var PROGRESS_FILE = "/tmp/agent-scratch/lhso-selftest-progress.txt";

function check( name, actual, expected )
{
   if ( IN_PIXINSIGHT )
      try { File.writeTextFile( PROGRESS_FILE, TESTS_RUN + " " + name ); } catch ( e ) {}
   TESTS_RUN++;
   var a = JSON.stringify( actual );
   var e = JSON.stringify( expected );
   if ( a != e )
      FAILURES.push( name + ": expected " + e + ", got " + a );
}

/*
 * A 20-frame channel with real spread: FWHM gates at about 5.8, so the two
 * wide frames (6.5, 7.0) are rejected by the balanced preset, and nothing
 * else is. Background and SNR ride along for the flag and column tests.
 */
function fsFixtureChannel()
{
   var fw = [ 3.0, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9,
              4.0, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 6.5, 7.0 ];
   var entries = [], metrics = {};
   for ( var i = 0; i < fw.length; ++i )
   {
      var p = "/fx/f" + i + ".xisf";
      entries.push( { path: p, identity: { digest: "d" + i, size: 1, mtime: 1 } } );
      metrics[p] = { fwhm: fw[i], eccentricity: 0.40 + 0.01*( i % 10 ),
                     psfSNR: 10 + 0.37*i, stars: 8000 + 37*i, noise: 0.001,
                     background: 0.02 + 0.0005*( i % 7 ), snrWeight: 20 + i };
   }
   return Frames.newChannel( "O", entries, metrics, [] );
}

/*
 * A synthetic star field, so the suite depends on nothing but PixInsight:
 * flat background, Gaussian noise and Gaussian stars from a seeded
 * generator, with the header keywords the Frame Selector groups and orders
 * by. FWHM and background are known by construction.
 */
function synthRandom( seed )
{
   var a = seed >>> 0;
   return function()
   {
      a = ( a + 0x6D2B79F5 ) >>> 0;
      var t = Math.imul( a ^ ( a >>> 15 ), 1 | a );
      t = ( t + Math.imul( t ^ ( t >>> 7 ), 61 | t ) ) ^ t;
      return ( ( t ^ ( t >>> 14 ) ) >>> 0 )/4294967296;
   };
}

function synthFrame( path, o )
{
   var w = o.width || 800, h = o.height || 600, sigma = o.fwhm/2.3548;
   var rnd = synthRandom( o.seed || 1 ), starRnd = synthRandom( 12345 );   // same sky every frame
   var buf = new Float32Array( w*h );
   for ( var i = 0; i < buf.length; i += 2 )
   {
      var u = Math.max( 1e-12, rnd() ), v = rnd(), r = Math.sqrt( -2*Math.log( u ) );
      buf[i] = o.background + o.noise*r*Math.cos( 2*Math.PI*v );
      if ( i + 1 < buf.length ) buf[i + 1] = o.background + o.noise*r*Math.sin( 2*Math.PI*v );
   }
   var n = ( o.stars != null ) ? o.stars : 400, reach = Math.ceil( 4*sigma );   // stars: 0 means none
   /*
    * o.moffat = beta: Moffat stars with real stars' wide wings, same FWHM,
    * reaching out to o.reach px (default 12 FWHM), so halos are in the test.
    */
   var beta = o.moffat || 0, alpha = beta ? o.fwhm/( 2*Math.sqrt( Math.pow( 2, 1/beta ) - 1 ) ) : 0;
   if ( beta ) reach = Math.ceil( o.reach || 12*o.fwhm );
   for ( var s = 0; s < n; ++s )
   {
      var cx = 10 + starRnd()*( w - 20 ) + ( o.dx || 0 ),
          cy = 10 + starRnd()*( h - 20 ) + ( o.dy || 0 );
      var peak = ( o.flux || 1 )*( 0.03 + 0.5*Math.pow( starRnd(), 3 ) )*( 2.0/o.fwhm )*( 2.0/o.fwhm );
      for ( var y = Math.max( 0, Math.floor( cy - reach ) ); y <= Math.min( h - 1, Math.ceil( cy + reach ) ); ++y )
         for ( var x = Math.max( 0, Math.floor( cx - reach ) ); x <= Math.min( w - 1, Math.ceil( cx + reach ) ); ++x )
         {
            var dx = x - cx, dy = y - cy;
            var r2 = dx*dx + dy*dy;
            buf[y*w + x] += peak*( beta ? Math.pow( 1 + r2/( alpha*alpha ), -beta ) : Math.exp( -r2/( 2*sigma*sigma ) ) );
         }
   }
   for ( var k = 0; k < buf.length; ++k )
      buf[k] = Math.min( 1, Math.max( 0, buf[k] ) );
   var win = new ImageWindow( w, h, 1, 32, true, false, Util.freeWindowId( "fs_synth" ) );
   try
   {
      win.mainView.beginProcess( UndoFlag_NoSwapFile );
      win.mainView.image.setSamples( buf );
      win.mainView.endProcess();
      win.keywords = [ new FITSKeyword( "FILTER", "'" + ( o.filter || "S" ) + "'", "" ),
                       new FITSKeyword( "EXPTIME", "60", "" ),
                       new FITSKeyword( "IMAGETYP", "'Light Frame'", "" ),
                       new FITSKeyword( "DATE-OBS", "'" + ( o.date || "2026-01-01T00:00:00" ) + "'", "" ) ];
      win.saveAs( path, false, false, false, false );
   }
   finally { win.forceClose(); }
   return path;
}

/* TAN WCS keywords centred on the image, so a synthetic frame has sky coordinates. */
function withTanKeywords( window, ra, dec, scaleDeg )
{
   var w = window.mainView.image.width, h = window.mainView.image.height;
   window.keywords = window.keywords.concat( [
      new FITSKeyword( "CTYPE1", "'RA---TAN'", "" ), new FITSKeyword( "CTYPE2", "'DEC--TAN'", "" ),
      new FITSKeyword( "CRVAL1", String( ra ), "" ), new FITSKeyword( "CRVAL2", String( dec ), "" ),
      new FITSKeyword( "CRPIX1", String( w/2 + 0.5 ), "" ), new FITSKeyword( "CRPIX2", String( h/2 + 0.5 ), "" ),
      new FITSKeyword( "CD1_1", String( -scaleDeg ), "" ), new FITSKeyword( "CD1_2", "0", "" ),
      new FITSKeyword( "CD2_1", "0", "" ), new FITSKeyword( "CD2_2", String( scaleDeg ), "" ) ] );
}

/*
 * A generated Fly-Through scene: a star field with TAN keywords at IC 1396,
 * its starless twin (same noise, no stars), the stars layer by unscreen,
 * and sprites for the brightest detections at 200-2000 pc. The caller
 * closes `windows`.
 */
function flyTestScene( dir, flux )
{
   var T0 = { ra: 324.745, dec: 57.514 };
   var o = { fwhm: 3, background: 0.05, noise: 0.002, seed: 5, flux: flux || 1 };
   var Iwin = ImageWindow.open( synthFrame( dir + "/image.xisf", Object.assign( { stars: 150 }, o ) ) )[0];
   var Swin = ImageWindow.open( synthFrame( dir + "/starless.xisf", Object.assign( { stars: 0 }, o ) ) )[0];
   withTanKeywords( Iwin, T0.ra, T0.dec, 0.0005 );
   var Twin = Sky.copyWindow( Iwin, "fly_T" );
   Steps.deriveStarsByUnscreen( Twin, Swin );
   var I = Iwin.mainView.image, W = I.width, H = I.height, proj = Sky.projector( Iwin ), wcs = Sky.keywordWcs( Iwin );
   var sources = Sky.detections( Twin.mainView.image ).slice( 0, 30 ).map( function( d, i )
   {
      var c = Fly.tanUnproject( d.x, d.y, wcs );
      return { ra: c.ra, dec: c.dec, plx: 1000/( 200 + 60*i ) - Fly.PARALLAX_ZERO_POINT, pmra: 0, pmdec: 0, G: 12 + i*0.01 };
   } );
   var placed = Fly.placeStars( sources, proj, W, H );
   var sp = Sky.sprites( Twin.mainView.image, placed, 3, placed );
   var scene = Render.scene( { starless: Swin.mainView.image, stars: Twin.mainView.image, sprites: sp.sprites,
                               residual: sp.residual, project: proj, target: T0, D: 900 } );
   return { scene: scene, sources: sources, image: Iwin, windows: [ Iwin, Swin, Twin ], W: W, H: H, target: T0 };
}

/* The suite's own scratch folder for generated frames. Emptied on entry. */
function synthDir( name )
{
   var dir = "/tmp/agent-scratch/" + name;
   if ( File.directoryExists( dir ) )
      FrameSelector.emptyDirectory( dir );
   else
      File.createDirectory( dir, true );
   return dir;
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

   /*
    * L is no longer required. It is the registration reference when it is
    * there; without it Loom registers to the best of the channels it has
    * (Pipeline.registrationReference), so RGB-only and narrowband-only
    * sets are real work, not a validation error.
    */
   check( "validate RGB without L",
          Util.validateSelection( { R: "/a/R.xisf", G: "/a/G.xisf",
                                    B: "/a/B.xisf" } ),
          [] );
   check( "validate narrowband without L",
          Util.validateSelection( { H: "/a/H.xisf", S: "/a/S.xisf",
                                    O: "/a/O.xisf" } ),
          [] );
   check( "validate a single narrowband channel without L",
          Util.validateSelection( { H: "/a/H.xisf" } ),
          [] );

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
   if ( IN_PIXINSIGHT )
   {
      check( "module check finds StarAlignment",
             Steps.moduleAvailable( "StarAlignment" ), true );
      check( "module check finds SPFC",
             Steps.moduleAvailable( "SpectrophotometricFluxCalibration" ), true );
      check( "module check finds MGC",
             Steps.moduleAvailable( "MultiscaleGradientCorrection" ), true );
      check( "module check finds GraXpert",
             Steps.moduleAvailable( "GraXpert" ), true );
   }
   check( "module check rejects nonsense",
          Steps.moduleAvailable( "NotARealProcess" ), false );

   /*
    * lookupFilterCurve reads the real filters.xspd out of the PixInsight
    * installation, so it can only run where there is one. It passed under
    * node on the author's Mac and failed on a CI runner, which is the
    * whole argument for running the suite somewhere PixInsight is absent.
    */
   if ( IN_PIXINSIGHT )
   {
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
   }

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
   // views count the same as files for the no-L case too
   check( "validate RGB views without L",
          Util.validateSelection( {}, { R: "R1", G: "G1", B: "B1" } ),
          [] );

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

   /*
    * Not every machine HAS ROMM RGB. It is a macOS system profile: on a
    * Windows PixInsight, AssignICCProfile could not find it, nor Generic
    * Gray, and every plate went out untagged. So the installed profiles
    * are read, and the closest standard working space is chosen instead --
    * never a monitor's calibration profile, and never a linear one.
    */
   ( function()
   {
      function be32( n ) { return [ ( n >>> 24 ) & 255, ( n >>> 16 ) & 255, ( n >>> 8 ) & 255, n & 255 ]; }
      function ascii( t ) { return t.split( "" ).map( function( c ) { return c.charCodeAt( 0 ); } ); }
      /* A minimal ICC file: header, one 'desc' tag, v2 'desc' or v4 'mluc' body. */
      function fakeIcc( cls, space, desc, v4 )
      {
         var body;
         if ( v4 )
         {
            var u = [];
            desc.split( "" ).forEach( function( c ) { u.push( 0, c.charCodeAt( 0 ) ); } );
            body = ascii( "mluc" ).concat( [ 0, 0, 0, 0 ], be32( 1 ), be32( 12 ),
                                           ascii( "enUS" ), be32( u.length ), be32( 28 ), u );
         }
         else
            body = ascii( "desc" ).concat( [ 0, 0, 0, 0 ], be32( desc.length + 1 ), ascii( desc ), [ 0 ] );
         var head = [];
         for ( var i = 0; i < 128; ++i ) head.push( 0 );
         ascii( cls ).forEach( function( b, i ) { head[12 + i] = b; } );
         ascii( space ).forEach( function( b, i ) { head[16 + i] = b; } );
         return head.concat( be32( 1 ), ascii( "desc" ), be32( 144 ), be32( body.length ), body );
      }
      function parse( bytes ) { return Steps.parseIccProfile( function( i ) { return bytes[i]; }, bytes.length ); }

      check( "an ICC v2 description is read",
             parse( fakeIcc( "mntr", "RGB ", "Adobe RGB (1998)", false ) ),
             { deviceClass: "mntr", colorSpace: "RGB", description: "Adobe RGB (1998)" } );
      check( "an ICC v4 (mluc) description is read",
             parse( fakeIcc( "mntr", "GRAY", "Gray Gamma 2.2", true ) ).description, "Gray Gamma 2.2" );
      check( "a file too short to be a profile is not one", parse( [ 1, 2, 3 ] ), null );

      function P( d, space, cls ) { return { deviceClass: cls || "mntr", colorSpace: space || "RGB", description: d }; }
      var windowsLike = [ P( "sRGB IEC61966-2.1" ), P( "Adobe RGB (1998)" ), P( "Dell U2720Q calibrated" ),
                          P( "ACES CG Linear (Academy Color Encoding System AP1)" ), P( "Gray Gamma 2.2", "GRAY" ),
                          P( "Rec. ITU-R BT.2020-1" ), P( "RSWOP", "CMYK", "prtr" ),
                          P( "Rec. 2020 Linear" ) ];
      var plan = Steps.profilePlan( windowsLike );
      check( "without ROMM, the widest standard space wins: Rec. 2020 over Adobe RGB and sRGB",
             plan.rgb, [ Steps.PROFILE_RGB, "Rec. ITU-R BT.2020-1", "Adobe RGB (1998)", "sRGB IEC61966-2.1" ] );
      check( "a calibration profile and a linear space are never candidates",
             plan.rgb.filter( function( d ) { return /Dell|Linear/.test( d ); } ), [] );
      check( "gray follows the RGB space's gamma (2.2 here)", plan.gray, [ "Gray Gamma 2.2", Steps.PROFILE_GRAY ] );

      var macLike = [ P( "ROMM RGB: ISO 22028-2:2013" ), P( "Display P3" ), P( "Generic Gray Profile", "GRAY" ),
                      P( "Generic Gray Gamma 2.2 Profile", "GRAY" ), P( "sRGB IEC61966-2.1" ) ];
      var mp = Steps.profilePlan( macLike );
      check( "with ROMM installed, nothing changes", mp.rgb[0], Steps.PROFILE_RGB );
      check( "and gray stays at ProPhoto's gamma 1.8 first",
             mp.gray, [ Steps.PROFILE_GRAY, "Generic Gray Gamma 2.2 Profile" ] );
      check( "sRGB is always the last resort, even when not enumerated",
             Steps.profilePlan( [] ).rgb, [ Steps.PROFILE_RGB, "sRGB IEC61966-2.1" ] );
      check( "no gray profile anywhere: only the preferred name is tried",
             Steps.profilePlan( [] ).gray, [ Steps.PROFILE_GRAY ] );

      check( "Windows profiles live under the system root",
             Steps.iccProfileDirectories( Util.PLATFORM_WINDOWS, "C:/Users/x", "D:\\WINNT" )[0],
             "D:/WINNT/System32/spool/drivers/color" );
      check( "macOS reads the three ColorSync folders",
             Steps.iccProfileDirectories( Util.PLATFORM_MACOS, "/Users/x", "" ).slice( 0, 3 ),
             [ "/System/Library/ColorSync/Profiles", "/Library/ColorSync/Profiles",
               "/Users/x/Library/ColorSync/Profiles" ] );
   } )();

   if ( IN_PIXINSIGHT )
   {
      var installed = Steps.installedIccProfiles();
      check( "this Mac's installed profiles are enumerated",
             installed.filter( function( p ) { return p.description == Steps.PROFILE_RGB; } ).length, 1 );
      var w1 = new ImageWindow( 8, 8, 3, 16, false, true, Util.freeWindowId( "icc_plan_probe" ) );
      try
      {
         Steps.assignProfile( w1, "probe", { rgb: [ "No Such Profile", "Adobe RGB (1998)" ], gray: [] } );
         check( "a missing profile falls through to the next candidate",
                Steps.lastAssignedProfile, "Adobe RGB (1998)" );
         var kept = Steps.rgbProfileInUse;
         var psb = Steps.psbProfileBytes();
         Steps.rgbProfileInUse = kept;
         check( "the PSB carries the profile the plates were given, not ProPhoto",
                psb.length, Steps.iccProfileBytes( "Adobe RGB (1998)" ).length );
      }
      finally { w1.forceClose(); Steps.rgbProfileInUse = null; }
   }

   // The PSB carries its profile in image resource 1039, which the writer
   // builds by hand: nothing embeds it for us there.
   /*
    * The project name is derived from where the masters live, because PJSR
    * exposes nothing at all about the open PixInsight project. Container
    * folders are skipped so the answer names the target, not the layout.
    */
   check( "the target folder becomes the project name",
          Pipeline.projectNameFromPath(
             "/Volumes/Data/Crescent Nebula/master/masterLight_FILTER-H.xisf" ),
          "Crescent Nebula" );
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
    * ChannelCombination warns "Inconsistent ... (FILTER keyword) value(s) -
    * metadata not generated" whenever R, G and B disagree -- which FILTER
    * always does. The disagreeing keywords are held back for the combine
    * and restored after, so the warnings go and nothing is lost: the
    * composite never received that metadata anyway (probed 2026-09-23).
    */
   ( function()
   {
      function K( name, value ) { return { name: name, value: value }; }
      var r = [ K( "FILTER", "'R'" ), K( "EGAIN", "1.0" ), K( "TELESCOP", "'RC8'" ), K( "EXPTIME", "300" ) ];
      var g = [ K( "FILTER", "'G'" ), K( "EGAIN", "1.1" ), K( "TELESCOP", "'RC8'" ), K( "EXPTIME", "300" ) ];
      var b = [ K( "FILTER", "'B'" ), K( "EGAIN", "1.0" ), K( "TELESCOP", "'RC8'" ) ];
      check( "keywords whose values differ between the channels are found",
             Steps.inconsistentKeywords( [ r, g, b ] ), [ "EGAIN", "EXPTIME", "FILTER" ] );
      check( "keywords every channel agrees on are left alone",
             Steps.inconsistentKeywords( [ r, r, r ] ), [] );
   } )();

   if ( IN_PIXINSIGHT ) ( function()
   {
      function mk( id, filter )
      {
         var w = new ImageWindow( 16, 16, 1, 32, true, false, Util.freeWindowId( id ) );
         w.mainView.beginProcess( UndoFlag_NoSwapFile ); w.mainView.image.fill( 0.1 ); w.mainView.endProcess();
         w.keywords = [ new FITSKeyword( "FILTER", "'" + filter + "'", "" ), new FITSKeyword( "TELESCOP", "'RC8'", "" ) ];
         return w;
      }
      var r = mk( "cc_R", "R" ), g = mk( "cc_G", "G" ), b = mk( "cc_B", "B" ), rgb = null;
      try
      {
         console.beginLog();
         try { rgb = Steps.combineRGB( r.mainView, g.mainView, b.mainView, Util.freeWindowId( "cc_RGB" ) ); }
         finally { var text = String( console.endLog() ); }
         check( "combining R, G and B raises no metadata warnings", ( text.match( /Inconsistent/g ) || [] ).length, 0 );
         check( "and each channel keeps its own keywords",
                r.keywords.map( function( k ) { return k.name + "=" + k.value; } ), [ "FILTER='R'", "TELESCOP='RC8'" ] );
      }
      finally
      {
         [ r, g, b ].forEach( function( w ) { w.forceClose(); } );
         if ( rgb ) rgb.forceClose();
      }
   } )();

   /*
    * A result's final name. Star extraction already calls its window
    * "L_stars", so asking for a FREE "L_stars" found it taken -- by the
    * window being renamed -- and every stars plate came out "L_stars_1"
    * (seen on a Windows run, 2026-09-23). A window keeps a name it holds.
    */
   ( function()
   {
      var taken = { "L_stars": true, "RGB": true };
      var exists = function( id ) { return taken[id] === true; };
      check( "a window already holding its final name keeps it",
             Pipeline.publishId( "L_stars", "L_stars", exists ), "L_stars" );
      check( "another window holding the name still gets a suffix",
             Pipeline.publishId( "RGB_work", "RGB", exists ), "RGB_1" );
      check( "a free name is used as is",
             Pipeline.publishId( "x", "RGB_starless", exists ), "RGB_starless" );
   } )();

   if ( IN_PIXINSIGHT ) ( function()
   {
      var reg = new Util.Registry(), keep = [];
      var w = new ImageWindow( 8, 8, 1, 32, true, false, Util.freeWindowId( "pubtest_stars" ) );
      var want = w.mainView.id;
      try
      {
         var out = Pipeline.publish( w, want, reg, keep, null, null );
         check( "publishing a window under the name it holds does not add _1", out.mainView.id, want );
      }
      finally { w.forceClose(); }

      // the cached path: a new window is built while the cached one holds the name
      var c = new ImageWindow( 8, 8, 1, 32, true, false, Util.freeWindowId( "pubtest_cached" ) );
      var name = c.mainView.id, clean = Steps.detachFromFile( c, name );
      try
      {
         c.forceClose();
         clean.mainView.id = name;
         check( "a detached copy takes the name once the cached one is closed", clean.mainView.id, name );
      }
      finally { clean.forceClose(); }
   } )();

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
   function kindOf( r ) { return ( r == null ) ? null : r.kind; }
   check( "autoUpdate off prepares nothing",
          kindOf( Update.prepareHelper( { autoUpdate: false },
             fakeIo( [], [ "/x/Loom/.git" ], GOOD_GIT ) ) ), null );
   check( "a checkout with a working git prepares the git updater",
          kindOf( Update.prepareHelper( { autoUpdate: true },
             fakeIo( [ "/opt/homebrew/bin/git" ], [ "/x/Loom/.git" ], GOOD_GIT ) ) ),
          "git" );
   /*
    * A checkout whose git is unusable prepares NOTHING. It must not fall
    * through to the zip path: that swaps a directory into place, which
    * over a working tree leaves a repository permanently dirty and
    * refused by every later --ff-only.
    */
   check( "a checkout with no usable git prepares nothing at all",
          kindOf( Update.prepareHelper( { autoUpdate: true },
             fakeIo( [], [ "/x/Loom/.git" ], GOOD_GIT ) ) ), null );
   check( "an unknown directory prepares nothing",
          kindOf( Update.prepareHelper( { autoUpdate: true },
             fakeIo( [], [], GOOD_GIT ) ) ), null );

   /*
    * The check BLOCKS and reports now. Reporting at the next launch was
    * the first design and it was no use: it cannot answer "is there a new
    * version?", which is the only question being asked.
    */
   function checkIo( record )
   {
      var io = fakeIo( [ "/opt/homebrew/bin/git" ], [ "/x/Loom/.git" ], GOOD_GIT );
      io.ran = [];
      io.execute = function( program, args, deadline )
      {
         if ( args && args.length && String( args[0] ).indexOf( "--version" ) >= 0 )
            return GOOD_GIT;
         io.ran.push( program + " deadline=" + deadline );
         return { exitCode: 0, output: "" };
      };
      io.fileExists = function( p )
      {
         if ( p.indexOf( Update.OUTCOME_FILE ) >= 0 )
            return record != null;
         return [ "/opt/homebrew/bin/git", "/x/Loom/.git" ].indexOf( p ) >= 0;
      };
      io.readText = function() { return record; };
      return io;
   }
   var okIo = checkIo( Update.formatOutcome( { status: "updated", exitCode: 0,
                          from: "aaaaaaa", to: "bbbbbbb", when: "-", message: "" } ) );
   var res = Update.checkNow( { autoUpdate: true }, okIo );
   check( "the check runs the helper itself rather than detaching it",
          okIo.ran.length, 1 );
   check( "...under a deadline, so an unreachable server is a pause not a hang",
          okIo.ran[0].indexOf( "deadline=" + Update.CHECK_DEADLINE_MS ) > 0, true );
   check( "...and returns the outcome to the caller", res.status, "updated" );
   check( "...naming the commit to restart on", res.to, "bbbbbbb" );
   /*
    * A check that leaves no record must not read as success: the caller
    * would restart Loom for nothing.
    */
   check( "a check that leaves no record returns nothing",
          Update.checkNow( { autoUpdate: true }, checkIo( null ) ), null );

   /*
    * The relaunch hands the script back to THIS PixInsight instance, so
    * the updated #includes are parsed afresh.
    */
   var relaunchIo = fakeIo( [], [] );
   var savedFile = Update.SCRIPT_FILE;
   Update.SCRIPT_FILE = "/x/Loom/script/Loom.js";
   check( "a relaunch is dispatched to this instance",
          Update.relaunch( relaunchIo ) && relaunchIo.spawned.length == 1, true );
   check( "...with the -x form that re-runs a script in a live instance",
          relaunchIo.spawned[0].indexOf( "-x=" ) >= 0 &&
          relaunchIo.spawned[0].indexOf( "/x/Loom/script/Loom.js" ) >= 0, true );
   Update.SCRIPT_FILE = "";
   check( "with no script path there is nothing to relaunch",
          Update.relaunch( fakeIo( [], [] ) ), false );
   Update.SCRIPT_FILE = savedFile;

   /*
    * Each guard in the generated script earned its place by being wrong in
    * an earlier draft. These assertions exist so a later edit cannot
    * quietly drop one.
    */
   var gs = Update.gitScript( { git: "/opt/homebrew/bin/git", dir: "/x/Loom",
                                stateDir: "/cache/update" } );
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
   /*
    * Found by running the helper against a fixture repository: the dirty
    * check used to capture stderr along with stdout, so ANY git failure --
    * a missing binary, an unreadable repository -- came back as "local
    * changes present". The user was told they had uncommitted work they
    * did not have, and the real error was never reported at all.
    */
   /*
    * Also found against the fixture: a refused fast-forward prints a
    * paragraph of hints, and those newlines went into the record verbatim,
    * so a "one line per outcome" file stopped being one line per outcome.
    */
   check( "the shell folds git's multi-line messages into one line",
          gs.indexOf( "tr '\\n\\r'" ) >= 0, true );
   /*
    * And a third, same fixture: `[ $? -ne 0 ]` overwrites $? with the
    * test's own status, so every failure was recorded as exit code 0. The
    * status has to be captured before anything else runs.
    */
   check( "no failure path reports the status of the test instead of git's",
          gs.indexOf( "report failed $? " ) < 0, true );
   /*
    * State belongs to the cache the user chose, not to a dotfile in their
    * home directory -- scattering state is how a tool becomes something
    * you cannot fully uninstall. It sits in a SUBDIRECTORY because
    * Cache.clear deletes loose files and skips directories.
    */
   check( "updater state lives under the cache folder",
          Update.stateDir().indexOf( Cache.dir() ) == 0, true );
   check( "...in a subdirectory, so Clear cache cannot eat it",
          Update.stateDir() != Cache.dir(), true );
   check( "...and not in the home directory",
          Update.stateDir().indexOf( "/.loom" ) < 0, true );
   check( "the dirty check keeps stderr out of its output",
          gs.indexOf( "--untracked-files=all 2>&1" ) < 0, true );
   check( "...and reports a failed status check as a failure",
          gs.indexOf( "report failed $RC" ) >= 0, true );
   check( "the PowerShell branch does the same",
          Update.zipScript != null &&
          Update.gitScript( { git: "git.exe", dir: "C:/L", stateDir: "C:/S",
                              platform: "Windows" } )
            .indexOf( "--untracked-files=all 2>&1" ) < 0, true );
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

   var zs = Update.zipScript( { dir: "/x/Loom", stateDir: "/cache/update",
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
   check( "the bundled scripts are where the core says they are",
          Steps.PI_SRC_SCRIPTS_DIR, CoreApplication.srcDirPath + "/scripts" );
   check( "the core settings directory is the core's own",
          Steps.CORE_SETTINGS_DIR, CoreApplication.configDirPath );
   /*
    * The paths above are derived from the core's own properties and can
    * be checked anywhere. Whether the FILES are there describes the
    * installation, so it is asked only where there is one.
    */
   if ( IN_PIXINSIGHT )
   {
      check( "the spectrum database is really there",
             File.exists( Steps.FILTERS_XSPD_PATH ), true );
      check( "the ImageSolver engine is really there",
             File.exists( Steps.IMAGE_SOLVER_ENGINE_PATH ), true );
      check( "the core settings directory is really there",
             File.directoryExists( Steps.CORE_SETTINGS_DIR ), true );
   }
   /*
    * The include that cannot take a runtime path. It is written
    * <../src/scripts/...>, relative to the core's include directory, so it
    * resolves wherever PixInsight is installed and on whatever platform.
    * If it ever fails to resolve, PixInsight discards this whole script
    * silently -- so reaching this line at all is half the assertion.
    */
   if ( IN_PIXINSIGHT )
      check( "the ImageSolver engine class is in scope",
             typeof ImageSolver, "function" );

   /*
    * No source file may carry an absolute path into the PixInsight
    * installation again. The check is on the sources because a literal
    * that is only reached on a Windows machine cannot be caught any other
    * way from here.
    */
   var LIB_FILES = [ "Util.js", "Cache.js", "Psb.js", "Steps.js",
                     "Pipeline.js", "Update.js", "UI.js",
                     "Fly.js", "Sky.js", "Render.js" ];
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
                                dir: "C:/Users/x/Loom", stateDir: "C:/cache/update",
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
                                      stateDir: "C:/cache/O'Brien/update",
                                      platform: Util.PLATFORM_WINDOWS } );
   check( "a quote in a real path is escaped in the generated script",
          psQuoted.indexOf( "'C:/Users/O''Brien/Loom'" ) >= 0, true );

   var psZip = Update.zipScript( { dir: "C:/Users/x/Loom", stateDir: "C:/cache/update",
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
   var winCmd = Update.prepareHelper( { autoUpdate: true }, winIo );
   check( "a Windows checkout prepares the git updater",
          winCmd == null ? null : winCmd.kind, "git" );
   check( "...as a PowerShell process",
          winCmd.program.indexOf( "powershell.exe" ) == 0, true );
   check( "...running a .ps1",
          winCmd.args.join( " " ).indexOf( "update-run.ps1" ) >= 0, true );
   check( "...and nothing anywhere runs /bin/sh",
          ( winCmd.program + " " + winCmd.args.join( " " ) ).indexOf( "/bin/sh" ) < 0,
          true );
   var psWritten = winIo.written[ Update.stateDir() + "/update-run.ps1" ];
   check( "the helper written is the PowerShell one, not the shell one",
          psWritten != null && psWritten.indexOf( "$ErrorActionPreference" ) >= 0 &&
             psWritten.indexOf( "#!/bin/sh" ) < 0, true );
   /* Restore what the macOS updater tests set, so order cannot matter. */
   Update.SCRIPT_DIR = "/x/Loom";

   /*
    * Reopening a plate in the middle of the workspace.
    *
    * There is no grid, and there cannot be one: ImageWindow exposes
    * iconize, deiconize and iconic and NOTHING that positions an icon, so
    * where the icons land is the core's business. What a script can set
    * is window.position, which is the RESTORE position -- so that is set
    * to the centre, and a plate reopened from its icon appears in the
    * middle rather than wherever it was last parked.
    *
    * `max` is the largest position a window can hold and still be fully
    * visible, so half of it is the centre, and the workspace size never
    * has to be known. It is NOT the screen: availableScreenRect reads
    * 0,33 -> 1728,1117 while the workspace measures 1502x1018 from 0,0,
    * and centring against the screen put plates at y = -1227.
    */
   check( "half the maximum position is the centre",
          Pipeline.centredPosition( { x: 874, y: 589 } ).x, 437 );
   check( "and in y",
          Pipeline.centredPosition( { x: 874, y: 589 } ).y, 295 );
   /*
    * A window larger than the workspace reports a NEGATIVE maximum. Half
    * of that is further off-screen still, where the title bar cannot be
    * grabbed, so it clamps to the origin instead.
    */
   check( "a window too big for the workspace stays reachable",
          Pipeline.centredPosition( { x: -300, y: -120 } ).x, 0 );
   check( "in both axes",
          Pipeline.centredPosition( { x: -300, y: -120 } ).y, 0 );
   check( "a window that exactly fills the workspace sits at the origin",
          Pipeline.centredPosition( { x: 0, y: 0 } ).x, 0 );

   /*
    * Staggered, not stacked. Plates all at the exact centre sit on top of
    * one another, so every title but the last is hidden and unclickable.
    */
   var MAX = { x: 874, y: 589 };
   check( "a single plate is simply centred",
          Pipeline.staggeredPosition( MAX, 0, 1 ).x, 437 );
   check( "two plates do not share a position",
          Pipeline.staggeredPosition( MAX, 0, 2 ).x !=
          Pipeline.staggeredPosition( MAX, 1, 2 ).x, true );
   check( "consecutive plates are one step apart",
          Pipeline.staggeredPosition( MAX, 1, 3 ).x -
          Pipeline.staggeredPosition( MAX, 0, 3 ).x, Pipeline.CASCADE_STEP );
   check( "and step down as well as across",
          Pipeline.staggeredPosition( MAX, 1, 3 ).y -
          Pipeline.staggeredPosition( MAX, 0, 3 ).y, Pipeline.CASCADE_STEP );
   /*
    * The DECK is centred, not its first plate: an odd count puts the
    * middle plate dead centre, and the pile stays in the middle of the
    * workspace instead of starting there and running off the bottom
    * right.
    */
   check( "the middle plate of an odd deck is the centred one",
          Pipeline.staggeredPosition( MAX, 1, 3 ).x, 437 );
   check( "and the deck straddles the centre evenly",
          Pipeline.staggeredPosition( MAX, 0, 3 ).x + 
          Pipeline.staggeredPosition( MAX, 2, 3 ).x, 874 );
   /*
    * A deck longer than the workspace would otherwise walk its last
    * plates off the edge, where they cannot be reached at all.
    */
   check( "no plate is placed outside the workspace",
          ( function()
            {
               for ( var i = 0; i < 40; ++i )
               {
                  var p = Pipeline.staggeredPosition( MAX, i, 40 );
                  if ( p.x < 0 || p.y < 0 || p.x > MAX.x || p.y > MAX.y )
                     return "escaped at " + i;
               }
               return "all reachable";
            } )(), "all reachable" );

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
    * A cache folder that is not there disables the cache rather than
    * being created.
    *
    * Cache.ensureDir creates intermediate directories, so with the cache
    * on an external volume an unmounted drive would have Loom build a
    * decoy cache on the boot disk, fill it with the tens of gigabytes a
    * few runs produce, and then ignore the real one when the drive came
    * back.
    */
   var savedOverride = Cache.overrideDir;
   try
   {
      Cache.setDir( "/nonexistent/volume/Loom-cache" );
      check( "a chosen folder that is absent is reported missing",
             Cache.selectedDirMissing(), true );
      var cfg = { useCache: true };
      check( "and the cache is turned off", Cache.disableIfDirMissing( cfg ), true );
      check( "...with useCache actually false", cfg.useCache, false );
      /*
       * Once off, it must not keep announcing itself on every re-check.
       */
      check( "a second call has nothing left to disable",
             Cache.disableIfDirMissing( cfg ), false );

      Cache.setDir( "" );
      check( "the default temp folder is never treated as missing",
             Cache.selectedDirMissing(), false );
      var dflt = { useCache: true };
      check( "...so the cache stays on",
             Cache.disableIfDirMissing( dflt ) == false && dflt.useCache == true, true );

      Cache.setDir( LOOM_DIR );      // a folder that certainly exists
      check( "an existing chosen folder is not missing",
             Cache.selectedDirMissing(), false );
      /*
       * A run with the cache already off must not be "re-disabled": the
       * caller uses the return value to decide whether to say anything.
       */
      check( "a cache already off reports no change",
             Cache.disableIfDirMissing( { useCache: false } ), false );
   }
   finally { Cache.setDir( savedOverride ); }

   /*
    * The installation root is NOT the folder the script sits in.
    *
    * Loom.js lives in <root>/script and .git is at <root>/.git, so handing
    * the script's own directory to installKind reported "unknown": the
    * updater silently did nothing and the title bar showed no commit.
    * Caught by rolling the checkout back a commit and watching nothing
    * happen, which is exactly the failure a silent updater hides.
    */
   var savedScriptDir = Update.SCRIPT_DIR;
   try
   {
      Update.SCRIPT_DIR = "/x/Loom/script";
      check( "the root is found one level up from the script folder",
             Update.installDir( fakeIo( [], [ "/x/Loom/.git" ] ) ), "/x/Loom" );
      check( "a worktree root, where .git is a file, is found too",
             Update.installDir( fakeIo( [ "/x/Loom/.git" ], [] ) ), "/x/Loom" );
      check( "a release install is found by its marker",
             Update.installDir( fakeIo( [ "/x/Loom/RELEASE" ], [] ) ), "/x/Loom" );
      /*
       * Nothing recognised: report the parent, which is the installation
       * root by layout and the directory a zip install would replace --
       * never the script folder itself.
       */
      check( "with nothing to recognise it still reports the root",
             Update.installDir( fakeIo( [], [] ) ), "/x/Loom" );
      /*
       * A checkout several levels up is still found, so the script folder
       * can be nested without the updater going quiet.
       */
      Update.SCRIPT_DIR = "/x/Loom/a/b/script";
      check( "a root further up is still found",
             Update.installDir( fakeIo( [], [ "/x/Loom/.git" ] ) ), "/x/Loom" );
      /*
       * ...but not without limit: walking to / would let Loom decide that
       * some unrelated repository above it was the thing to update.
       */
      Update.SCRIPT_DIR = "/a/b/c/d/e/f/script";
      check( "the walk upwards is bounded",
             Update.installDir( fakeIo( [], [ "/a/.git" ] ) ), "/a/b/c/d/e/f" );
   }
   finally { Update.SCRIPT_DIR = savedScriptDir; }

   /*
    * The startup banner. Plain rather than coloured: the console honours
    * only its semantic channels, so colour would mean a three-tone banner
    * rather than the gradient the core prints for itself.
    */
   check( "the banner has five lines", Util.BANNER.length, 5 );
   check( "no line would wrap an 80-column console",
          Util.BANNER.filter( function( l ) { return l.length > 80; } ).length, 0 );
   check( "the banner carries no markup the console would strip",
          Util.BANNER.join( "" ).indexOf( "<" ) < 0, true );
   /*
    * Drawn by hand, so the letters can drift. This pins the shape: the
    * L's upright, and the closing /_/ of the m.
    */
   check( "it still reads as Loom",
          Util.BANNER[4].indexOf( "/_____/" ) == 0 &&
          Util.BANNER[4].indexOf( "/_/ /_/" ) > 0, true );

   /*
    * WHERE a denoiser runs is the tool's property.
    *
    * NXT and MLDenoise want linear data -- RC Astro asks for NXT after
    * colour calibration and deconvolution but before the stretch, and
    * MLDenoise's authors ask for "color calibrated linear" outright. Prism
    * is the opposite: Steps.prismMtfTarget only means anything on stretched
    * data. So the dropdown chooses a tool and Loom chooses the slot.
    */
   check( "NoiseXTerminator is a linear-stage tool",
          Steps.denoiseIsLinear( Steps.NR_TOOL_NXT ), true );
   check( "so is MLDenoise",
          Steps.denoiseIsLinear( Steps.NR_TOOL_MLDENOISE ), true );
   check( "Prism is not -- it runs after the stretch",
          Steps.denoiseIsLinear( Steps.NR_TOOL_PRISM ), false );
   check( "and nothing is linear by accident",
          Steps.denoiseIsLinear( "none" ), false );

   /*
    * The model test, not the extension test. library/ holds
    * BlurXTerminator and StarXTerminator models beside any denoise one,
    * and MLDenoise given the wrong network is a confident wrong answer,
    * where no model at all is an honest refusal.
    */
   /*
    * One session comes off one camera, so a master whose header lost
    * INSTRUME -- WBPP's autocrop rewrites it away, leaving WBPPCROP as its
    * mark -- takes the camera its siblings name. This is not cosmetic:
    * Steps.deviceCurveForImage falls back to the ideal QE curve for an
    * unnamed camera, so that channel would be calibrated against a
    * different device response from the rest while the run looked clean.
    */
   check( "one named camera answers for the session",
          Util.commonInstrument( [ null, "ZWO ASI2600MM Air", "" ] ),
          "ZWO ASI2600MM Air" );
   check( "agreement is still one answer",
          Util.commonInstrument( [ "ASI2600MM", "ASI2600MM" ] ), "ASI2600MM" );
   /*
    * Disagreement must NOT be resolved by picking one. If two masters
    * really name different cameras the premise has failed, and a
    * confident wrong curve is worse than an admitted unknown.
    */
   check( "two different cameras answer nothing",
          Util.commonInstrument( [ "ASI2600MM", "ASI1600MM" ] ), null );
   check( "no camera at all answers nothing",
          Util.commonInstrument( [ null, "", null ] ), null );
   check( "and an empty list answers nothing",
          Util.commonInstrument( [] ), null );

   check( "the camera line names the resolved QE curve",
          UI.cameraSummary( [ "ZWO ASI2600MM Air" ], "Sony IMX411/455/461/533/571" )
             .indexOf( "Sony IMX411" ) > 0, true );
   check( "a session with no camera says the ideal curve will be used",
          UI.cameraSummary( [ null, null ], null ).indexOf( "ideal QE curve" ) > 0, true );
   check( "disagreeing cameras are reported, not averaged",
          UI.cameraSummary( [ "A", "B" ], null ).indexOf( "more than one" ) > 0, true );
   check( "an empty list says nothing at all",
          UI.cameraSummary( [], null ), "" );

   /*
    * Creation time, shown because a folder of restacks differs only by a
    * "(3)" in the name.
    */
   check( "a creation time is date and time, fixed width",
          Util.formatFileTime( new Date( 2026, 8, 14, 13, 2, 4 ).getTime() ),
          "2026-09-14 13:02" );
   check( "single digits are padded",
          Util.formatFileTime( new Date( 2026, 0, 5, 9, 7, 0 ).getTime() ),
          "2026-01-05 09:07" );
   /*
    * A dropped view has no file. Empty says so; 1970 would not.
    */
   check( "no time shows as nothing, not as the epoch",
          Util.formatFileTime( 0 ), "" );
   check( "and neither does a missing one",
          Util.formatFileTime( null ), "" );

   /*
    * Master quality, compared WITHIN a channel, on FWHM.
    *
    * Not across channels, and the measurement is why: on this rig G is
    * always the softest filter, so a cross-channel threshold that catches
    * a bad G fires on a good one too. Against the stack it displaced
    * there is no such confound.
    *
    * FWHM only. An SNR comparison was built and abandoned: every form of
    * it needs the two stacks on a common flux scale, and two stacks of
    * one channel do not have one -- between two S masters the stars moved
    * 30% and the sky 50%. A sky-based definition reported -57% for a
    * stack that had improved, because a better night has a DARKER sky.
    * FWHM assumes nothing about scale.
    */
   var SHARP = { fwhm: 8.06, eccentricity: 0.48, noise: 1.50e-5, stars: 15164 };
   var SOFT  = { fwhm: 8.93, eccentricity: 0.53, noise: 1.65e-5, stars: 13304 };

   check( "a softer stack reports a larger FWHM",
          Math.round( Util.qualityDelta( SOFT, SHARP ).fwhm ), 11 );
   check( "and the comparison reverses cleanly",
          Util.qualityDelta( SHARP, SOFT ).fwhm < 0, true );
   check( "eccentricity is carried alongside it",
          Math.round( Util.qualityDelta( SOFT, SHARP ).eccentricity ), 10 );
   check( "so is the noise",
          Math.round( Util.qualityDelta( SOFT, SHARP ).noise ), 10 );
   /*
    * The senses differ, which is the reason these are four columns and
    * not one score: fewer stars is a LOSS, so its delta is negative where
    * the other three are positive for the same worse stack.
    */
   check( "and the star count, which runs the other way",
          Math.round( Util.qualityDelta( SOFT, SHARP ).stars ), -12 );
   check( "nothing to compare against is not a delta of zero",
          Util.qualityDelta( SOFT, null ), null );
   check( "an unmeasurable stack does not produce a percentage",
          Util.qualityDelta( { fwhm: 0 }, SHARP ).fwhm, null );

   /*
    * Smaller is better, so the sharpest alternative is the one to beat.
    */
   check( "the sharpest alternative is the one compared against",
          Util.bestAlternative( [ { fwhm: 9.5 }, { fwhm: 8.06 }, { fwhm: 12 } ] ).fwhm, 8.06 );
   check( "unmeasurable alternatives are ignored",
          Util.bestAlternative( [ { fwhm: 0 }, null, { fwhm: 8.9 } ] ).fwhm, 8.9 );
   check( "and no alternatives at all answers nothing",
          Util.bestAlternative( [] ), null );

   /*
    * The sign IS the message, so it is always shown; below 1% nothing is,
    * because that is the measurement moving rather than the data.
    */
   check( "a regression carries its sign", Util.formatDelta( 12.4 ), "+12%" );
   check( "so does an improvement", Util.formatDelta( -13.46 ), "-13%" );
   check( "and a difference too small to mean anything is blank",
          Util.formatDelta( 0.4 ), "" );
   check( "as is no measurement at all", Util.formatDelta( null ), "" );

   /*
    * A scan of seven masters holds the dialog for two minutes, so the
    * progress line has to say both what is being measured and how much of
    * the wait is left. The count is what a spinner cannot give.
    */
   check( "a scan says what it is measuring and how far along it is",
          Util.scanProgressMessage( "Measuring masters", "G", 3, 7 ),
          "Measuring masters: G (3 of 7)" );
   check( "the second measurement of a master says which one it is",
          Util.scanProgressMessage( "Measuring masters", "G vs the previous stack", 3, 7 ),
          "Measuring masters: G vs the previous stack (3 of 7)" );
   check( "reading headers is a different action, same shape",
          Util.scanProgressMessage( "Reading masters", "M31_G.xisf", 1, 4 ),
          "Reading masters: M31_G.xisf (1 of 4)" );
   /*
    * Before the first master is reached there is nothing to count, and
    * "(0 of 7)" reads as a stall rather than as a start.
    */
   check( "with nothing counted yet only the action is shown",
          Util.scanProgressMessage( "Measuring masters", null, null, null ),
          "Measuring masters" );
   check( "an unknown channel does not leave a dangling colon",
          Util.scanProgressMessage( "Measuring masters", "  ", 2, 5 ),
          "Measuring masters (2 of 5)" );
   check( "and a count with no total is no count",
          Util.scanProgressMessage( "Measuring masters", "G", 2, 0 ),
          "Measuring masters: G" );
   /*
    * The count is clamped because the alternative -- "8 of 7" -- reads as
    * a bug at exactly the moment the user is watching the line.
    */
   check( "the count never overruns its total",
          Util.scanProgressMessage( "Measuring masters", "G", 9, 7 ),
          "Measuring masters: G (7 of 7)" );

   /*
    * Alternatives are same channel AND same variant class: a drizzled
    * autocrop master is not comparable with a plain one, and ranking has
    * already decided which class is in play.
    */
   ( function()
   {
      var pick = { channel: "G", path: "/m/G_new.xisf", drizzle: "2x", autocrop: true, mtime: 300 };
      var pool = [ pick,
                   { channel: "G", path: "/m/G_old.xisf",   drizzle: "2x", autocrop: true,  mtime: 200 },
                   { channel: "G", path: "/m/G_older.xisf", drizzle: "2x", autocrop: true,  mtime: 100 },
                   { channel: "G", path: "/m/G_plain.xisf", drizzle: "",   autocrop: false, mtime: 250 },
                   { channel: "R", path: "/m/R_new.xisf",   drizzle: "2x", autocrop: true,  mtime: 299 } ];
      var alt = Util.sameChannelAlternatives( pool, pick );
      check( "another channel is not an alternative",
             alt.filter( function( a ) { return a.channel != "G"; } ).length, 0 );
      check( "neither is the pick itself",
             alt.filter( function( a ) { return a.path == pick.path; } ).length, 0 );
      check( "nor a different variant class",
             alt.filter( function( a ) { return a.path == "/m/G_plain.xisf"; } ).length, 0 );
      check( "the stack it displaced comes first",
             alt[0].path, "/m/G_old.xisf" );
      check( "and the limit bounds how many measurements get made",
             Util.sameChannelAlternatives( pool, pick, 1 ).length, 1 );
   } )();

   /*
    * The SubframeSelector column indices, read off a live measurement
    * rather than assumed. If a future version reorders them, this fails
    * here rather than reporting a noise figure as an FWHM.
    */
   check( "FWHM is column 5", Steps.SFS_FWHM, 5 );
   check( "eccentricity is column 6", Steps.SFS_ECCENTRICITY, 6 );
   check( "and routine 0 is the one that measures", Steps.SFS_MEASURE, 0 );

   /*
    * Run is disabled when there is nothing to run. A view that has since
    * been closed is listed so its absence is visible, but it is not
    * something to process -- a list of only those is empty in the only
    * sense that matters.
    */
   check( "an empty list has nothing to run",
          Util.runnableEntryCount( [] ), 0 );
   check( "a missing list has nothing to run",
          Util.runnableEntryCount( null ), 0 );
   check( "two usable masters count",
          Util.runnableEntryCount( [ { channel: "H" }, { channel: "O" } ] ), 2 );
   check( "a closed view does not count",
          Util.runnableEntryCount( [ { channel: "H", unavailable: true } ] ), 0 );
   check( "and is not counted among usable ones",
          Util.runnableEntryCount( [ { channel: "H" },
                                     { channel: "O", unavailable: true } ] ), 1 );

   /*
    * Loom closes what THIS run created and nothing else.
    *
    * An earlier version also swept away anything carrying the LOOMOUT
    * keyword at startup, to stop results accumulating as RGB, RGB_1,
    * RGB_2. That closed a plate from a previous run that had been set
    * aside on purpose -- and a window someone kept is not Loom's to
    * remove, whoever made it.
    */
   ( function()
   {
      var pre = { "HSO": true, "MasterLight_H": true };
      var keep = { "RGB_starless": true };
      check( "a window that existed before the run is never closed",
             Pipeline.mayCloseWindow( "HSO", pre, keep ), false );
      check( "including one an earlier Loom run produced",
             Pipeline.mayCloseWindow( "MasterLight_H", pre, keep ), false );
      check( "this run's results are kept",
             Pipeline.mayCloseWindow( "RGB_starless", pre, keep ), false );
      check( "but this run's working windows are closed",
             Pipeline.mayCloseWindow( "G_work_MGC_gradient_model", pre, keep ), true );
      check( "and a nameless window is left alone",
             Pipeline.mayCloseWindow( null, pre, keep ), false );
   } )();

   /*
    * Frame Selector. The decision logic is pure so that what chooses which
    * files to delete can be tested without a workspace.
    */
   check( "Frames loads", typeof Frames, "object" );   // var Frames = {}
   check( "and declares the version its numbers came from",
          Frames.MEASURE_VERSION, "v3" );      // v3: altitude and star flux

   /*
    * The measurement row is positional. An earlier draft of the spec had PSF
    * SNR on column 8, which reads 0 on every frame -- the dominant term of
    * the score would have been a constant zero and nothing would have looked
    * wrong. WBPP's own analyzer names these indices and carries the comment
    * that they must track the process implementation
    * (BPP-SubframeAnalyzer.js:481).
    */
   check( "PSF SNR is column 28, not 8", Frames.COL.psfSNR, 28 );
   check( "FWHM is column 5", Frames.COL.fwhm, 5 );
   check( "eccentricity is column 6", Frames.COL.eccentricity, 6 );
   check( "noise is column 12", Frames.COL.noise, 12 );
   check( "stars is column 14", Frames.COL.stars, 14 );
   check( "the path is column 3", Frames.COL.path, 3 );

   ( function()
   {
      var row = [];
      for ( var i = 0; i < 31; ++i ) row.push( 0 );
      row[3] = "/m/sub.xisf"; row[5] = 6.82; row[6] = 0.395;
      row[12] = 9.6e-6; row[14] = 10029; row[28] = 25502;
      var m = Frames.metricsFromRow( row );
      check( "a row becomes named metrics", m.fwhm, 6.82 );
      check( "including the path it belongs to", m.path, "/m/sub.xisf" );
      check( "and PSF SNR from 28", m.psfSNR, 25502 );
   } )();

   /*
    * Pinning the constants does not detect a reorder -- the assertions above
    * pass unchanged after the process shuffles its table.
    *
    * The spec's answer was four disjoint ranges. Measured on 1.9.5 build
    * 1702 that is unachievable: a single sub reads PSF SNR 8.8 against 7507
    * stars, a drizzled master reads 35527 against 40551, and no pair of
    * ranges separates those while containing both. The values below are the
    * measurements, and they are asserted so the claim is checkable rather
    * than a comment.
    */
   ( function()
   {
      var sub    = { path: "/m/s.xisf", eccentricity: 0.5472782731795826,
                     fwhm: 3.5177160432121677, psfSNR: 8.815031176766649,
                     stars: 7507 };
      var master = { path: "/m/m.xisf", eccentricity: 0.5108710707667952,
                     fwhm: 6.563287389889611, psfSNR: 35526.66266439094,
                     stars: 40551 };

      check( "on a single sub PSF SNR is far BELOW the star count, not above",
             sub.psfSNR < sub.stars, true );
      check( "so no range separates psfSNR from stars across both frames",
             ( master.psfSNR > sub.stars ), true );

      /*
       * What does discriminate: a star count is a whole number and a PSF SNR
       * is not. That is exactly the pair the spec most needs a swap caught
       * on, and it holds on both frames.
       */
      check( "a real sub passes the meaning check",
             Frames.meaningProblems( sub ).length, 0 );
      check( "and so does a master", Frames.meaningProblems( master ).length, 0 );

      var swapped = { path: "/m/s.xisf", eccentricity: sub.eccentricity,
                      fwhm: sub.fwhm, psfSNR: sub.stars, stars: sub.psfSNR };
      check( "swapping stars and PSF SNR is caught",
             Frames.meaningProblems( swapped ).length > 0, true );

      var eccSwap = { path: "/m/s.xisf", eccentricity: sub.fwhm,
                      fwhm: sub.eccentricity, psfSNR: sub.psfSNR,
                      stars: sub.stars };
      check( "and so is swapping eccentricity with FWHM",
             Frames.meaningProblems( eccSwap ).length > 0, true );

      check( "a numeric path is caught too",
             Frames.meaningProblems( { path: 5, eccentricity: 0.5, fwhm: 4,
                                       psfSNR: 8.8, stars: 100 } ).length > 0, true );

      var bad = [];
      for ( var i = 0; i < Frames.METRIC_RANGE_ORDER.length; ++i )
      {
         var n = Frames.METRIC_RANGE_ORDER[i];
         if ( !Frames.metricInRange( n, sub[n] ) || !Frames.metricInRange( n, master[n] ) )
            bad.push( n );
      }
      check( "every range contains both real measurements", bad.join( "," ), "" );
   } )();

   /*
    * NaN escapes every comparison, so an invalid measurement would pass every
    * rejection gate and be silently kept. It becomes its own state instead.
    */
   check( "a finite positive number is valid", Frames.metricValid( 6.8 ), true );
   check( "zero is not -- it divides", Frames.metricValid( 0 ), false );
   check( "nor is a negative", Frames.metricValid( -1 ), false );
   check( "nor NaN", Frames.metricValid( NaN ), false );
   check( "nor infinity", Frames.metricValid( Infinity ), false );
   check( "nor a missing value", Frames.metricValid( undefined ), false );

   ( function()
   {
      var good = { psfSNR: 25502, fwhm: 6.82, eccentricity: 0.395, stars: 10029 };
      check( "a frame with four valid metrics is measurable",
             Frames.frameValid( good ), true );
      var zeroEcc = { psfSNR: 25502, fwhm: 6.82, eccentricity: 0, stars: 10029 };
      check( "an eccentricity of zero makes it unmeasurable",
             Frames.frameValid( zeroEcc ), false );
      var noStars = { psfSNR: 25502, fwhm: 6.82, eccentricity: 0.4, stars: 0 };
      check( "so does a star count of zero",
             Frames.frameValid( noStars ), false );
   } )();

   check( "the four metrics are named once, in scoring order",
          Frames.METRICS.join( "," ), "psfSNR,fwhm,eccentricity,stars" );

   check( "the median of an odd count is the middle value",
          Frames.median( [ 3, 1, 2 ] ), 2 );
   check( "and of an even count, the mean of the middle two",
          Frames.median( [ 1, 2, 3, 4 ] ), 2.5 );
   /*
    * Normalised: sigma = 1.4826 * MAD, which is what makes k a number of
    * standard deviations rather than an arbitrary width.
    */
   check( "sigma is the NORMALISED MAD",
          Math.round( Frames.sigma( [ 1, 2, 3, 4, 5 ] )*10000 ), 14826 );

   ( function()
   {
      var fwhm = [ 4.0, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 5.3, 10 ];
      var g = Frames.gate( fwhm, "fwhm", 2.5 );
      check( "a gate on a spread sample is active", g.active, true );
      check( "and rejects above the limit for a higher-is-worse metric",
             g.limit > 4.7 && g.limit < 10, true );
   } )();

   /*
    * With no spread the MAD is zero and every preset rejects the one frame
    * that differs in the sixth decimal. The gate switches off instead.
    */
   ( function()
   {
      var g = Frames.gate( [ 4, 4, 4, 4, 4.000001 ], "fwhm", 2.5 );
      check( "a gate with no usable spread is disabled", g.active, false );
      check( "and says why", g.reason.indexOf( "spread" ) >= 0, true );
   } )();

   /*
    * A lower bound at or below zero cannot reject anything, so it is
    * reported inactive rather than silently passing everything.
    */
   ( function()
   {
      var g = Frames.gate( [ 10, 1000, 2000, 3000 ], "psfSNR", 3.0 );
      check( "a non-positive lower bound is inactive", g.active, false );
   } )();

   check( "ten valid frames is the minimum for a relative clip",
          Frames.MIN_FRAMES, 10 );
   check( "below it there are no gates at all",
          Object.keys( Frames.relativeGates(
             [ { psfSNR: 1, fwhm: 1, eccentricity: 0.5, stars: 200 } ], 2.5 ) ).length, 0 );

   ( function()
   {
      var meds = { psfSNR: 1000, fwhm: 5, eccentricity: 0.4, stars: 10000 };
      var typical = { psfSNR: 1000, fwhm: 5, eccentricity: 0.4, stars: 10000 };
      check( "a frame at the median scores 1",
             Math.round( Frames.score( typical, meds,
                         Frames.DEFAULT_WEIGHTS )*1000 )/1000, 1 );

      var sharper = { psfSNR: 1000, fwhm: 2.5, eccentricity: 0.4, stars: 10000 };
      check( "a sharper frame scores higher",
             Frames.score( sharper, meds, Frames.DEFAULT_WEIGHTS ) > 1, true );

      /*
       * Dividing by the median aligns typical levels but not dispersion: a
       * near-zero eccentricity would contribute a term of 40 and swamp a
       * nominal weight of 0.1. Terms are clamped.
       */
      var silly = { psfSNR: 1000, fwhm: 5, eccentricity: 0.001, stars: 10000 };
      check( "a near-zero eccentricity cannot dominate",
             Frames.score( silly, meds, Frames.DEFAULT_WEIGHTS ) <
             Frames.score( typical, meds, Frames.DEFAULT_WEIGHTS ) + 0.4, true );

      var broken = { psfSNR: 1000, fwhm: 5, eccentricity: 0, stars: 10000 };
      check( "an unmeasurable frame has no score, not a NaN",
             Frames.score( broken, meds, Frames.DEFAULT_WEIGHTS ), null );
   } )();

   check( "the weights favour SNR", Frames.DEFAULT_WEIGHTS.psfSNR, 0.5 );
   check( "and sum to one",
          Frames.DEFAULT_WEIGHTS.psfSNR + Frames.DEFAULT_WEIGHTS.fwhm +
          Frames.DEFAULT_WEIGHTS.eccentricity + Frames.DEFAULT_WEIGHTS.stars, 1 );

   check( "the opening preset is Balanced", Frames.DEFAULT_PRESET, "balanced" );
   check( "Lenient is the widest gate", Frames.PRESETS.lenient, 3.0 );
   check( "Strict the narrowest", Frames.PRESETS.strict, 2.0 );

   /*
    * A preset sets k for every channel whose k has not been edited by hand.
    * An edited value stands, and editing a WEIGHT does not pin k.
    */
   ( function()
   {
      var s = Frames.defaultSettings();
      s.k = 2.7; s.kEdited = true;
      check( "an edited k survives a preset change",
             Frames.applyPreset( s, "strict" ).k, 2.7 );

      var w = Frames.defaultSettings();
      w.weights.fwhm = 0.5;
      check( "but an edited weight does not pin k",
             Frames.applyPreset( w, "strict" ).k, 2.0 );

      /*
       * A preset returns a SETTINGS OBJECT, and the caller replaces the
       * channel's settings with it. A shallow copy leaves weights and limits
       * shared with the object it came from, so editing a weight afterwards
       * reaches back into whatever else still holds the original -- the
       * quiet kind of wrongness that changes a score with nothing in the
       * dialog to show for it.
       */
      var original = Frames.defaultSettings();
      original.limits.fwhm = { hi: 6 };
      var preset = Frames.applyPreset( original, "strict" );
      preset.weights.fwhm = 0.9;
      preset.limits.fwhm.hi = 99;
      check( "a preset does not share its weights with the settings it copied",
             original.weights.fwhm, Frames.DEFAULT_WEIGHTS.fwhm );
      check( "nor its limits", original.limits.fwhm.hi, 6 );
   } )();

   ( function()
   {
      var gates = Frames.relativeGates( [
         { psfSNR: 1000, fwhm: 5.0, eccentricity: 0.40, stars: 10000 },
         { psfSNR: 1010, fwhm: 5.1, eccentricity: 0.41, stars: 10100 },
         { psfSNR: 1020, fwhm: 5.2, eccentricity: 0.42, stars: 10200 },
         { psfSNR: 1030, fwhm: 5.3, eccentricity: 0.40, stars: 10300 },
         { psfSNR: 1040, fwhm: 5.4, eccentricity: 0.41, stars: 10400 },
         { psfSNR: 1050, fwhm: 5.5, eccentricity: 0.42, stars: 10500 },
         { psfSNR: 1060, fwhm: 5.6, eccentricity: 0.40, stars: 10600 },
         { psfSNR: 1070, fwhm: 5.7, eccentricity: 0.41, stars: 10700 },
         { psfSNR: 1080, fwhm: 5.8, eccentricity: 0.42, stars: 10800 },
         { psfSNR: 1090, fwhm: 5.9, eccentricity: 0.40, stars: 10900 } ], 2.5 );

      var soft = { psfSNR: 1000, fwhm: 20, eccentricity: 0.4, stars: 10000 };
      var fine = { psfSNR: 1050, fwhm: 5.5, eccentricity: 0.41, stars: 10500 };

      var rel = Frames.defaultSettings();
      check( "Relative rejects a frame outside the gate",
             Frames.verdict( soft, gates, rel ).state, Frames.STATE.REJECTED );
      check( "and names the metric that failed",
             Frames.verdict( soft, gates, rel ).reasons[0].indexOf( "FWHM" ) >= 0, true );
      check( "a typical frame is approved",
             Frames.verdict( fine, gates, rel ).state, Frames.STATE.APPROVED );

      /*
       * A limit typed for a metric REPLACES its relative gate: hard limits
       * cannot rescue a frame the gate rejects, so the two are never
       * combined on one metric. Typing a limit is how a good night is kept.
       */
      var typed = Frames.defaultSettings();
      typed.limits.fwhm = { hi: 25 };
      check( "a typed limit replaces the relative gate",
             Frames.verdict( soft, gates, typed ).state, Frames.STATE.APPROVED );
      check( "and rejects on its own ceiling",
             Frames.verdict( { psfSNR: 1000, fwhm: 30, eccentricity: 0.4, stars: 10000 },
                              gates, typed ).state, Frames.STATE.REJECTED );
      check( "the other metrics keep their relative gates",
             Frames.verdict( { psfSNR: 1000, fwhm: 5.5, eccentricity: 0.9, stars: 10000 },
                              gates, typed ).state, Frames.STATE.REJECTED );

      /*
       * The minimum-count rule disables RELATIVE gates only. A thin channel
       * rejects nothing automatically -- but a limit typed by hand still
       * applies, because it does not depend on the channel's statistics.
       */
      var thin = Frames.relativeGates( [ { psfSNR: 1, fwhm: 1, eccentricity: 0.5, stars: 200 } ], 2.5 );
      check( "a thin channel rejects nothing automatically",
             Frames.verdict( { psfSNR: 1000, fwhm: 30, eccentricity: 0.4, stars: 10000 },
                              thin, Frames.defaultSettings() ).state, Frames.STATE.APPROVED );
      var thinTyped = Frames.defaultSettings();
      thinTyped.limits.fwhm = { hi: 2 };
      check( "but a typed limit still applies there",
             Frames.verdict( { psfSNR: 1000, fwhm: 30, eccentricity: 0.4, stars: 10000 },
                              thin, thinTyped ).state, Frames.STATE.REJECTED );

      check( "an unmeasurable frame is neither approved nor rejected",
             Frames.verdict( { psfSNR: 1000, fwhm: 5, eccentricity: 0, stars: 10000 },
                              gates, rel ).state, Frames.STATE.UNMEASURABLE );
   } )();

   ( function()
   {
      var e = [
         { path: "/m/h1.xisf", filter: "H", exposure: 180, binning: 1,
           width: 6248, height: 4176, calibrated: true },
         { path: "/m/h2.xisf", filter: "H", exposure: 180, binning: 1,
           width: 6248, height: 4176, calibrated: true },
         { path: "/m/o1.xisf", filter: "O", exposure: 180, binning: 1,
           width: 6248, height: 4176, calibrated: true },
         { path: "/m/x1.xisf", filter: "", exposure: 60, binning: 1,
           width: 6248, height: 4176, calibrated: true } ];
      var g = Frames.groupByFilter( e );
      check( "frames group by their filter", g.H.length, 2 );
      check( "each filter is its own group", g.O.length, 1 );
      /*
       * The RAW filter string, not Util.channelFromFilter, which maps to
       * Loom's seven canonical channels and returns null for anything else --
       * it would merge distinct filters and drop unfamiliar ones.
       */
      check( "an unreadable filter forms its own group",
             g[Frames.NO_FILTER].length, 1 );
      /*
       * And that group is never clipped. Without a filter there is no
       * evidence the frames belong together, so "this night's worst" is
       * meaningless over them -- they may be condemned by hand, never
       * automatically.
       */
      check( "a group with no filter is never auto-rejected",
             Frames.autoRejectAllowed( Frames.NO_FILTER ), false );
      check( "a real filter is", Frames.autoRejectAllowed( "H" ), true );

      check( "a uniform group is comparable",
             Frames.comparability( g.H ).uniform, true );

      var mixed = g.H.concat( [ { path: "/m/h3.xisf", filter: "H", exposure: 60,
                                  binning: 1, width: 6248, height: 4176,
                                  calibrated: true } ] );
      check( "mixed exposures are not", Frames.comparability( mixed ).uniform, false );
      check( "and the problem is named",
             Frames.comparability( mixed ).problems[0].indexOf( "exposure" ) >= 0, true );

      /*
       * Raw and calibrated frames of one filter can agree on exposure,
       * binning and geometry, so calibration state is part of the check.
       */
      var mixedCal = g.H.concat( [ { path: "/m/h4.xisf", filter: "H", exposure: 180,
                                     binning: 1, width: 6248, height: 4176,
                                     calibrated: false } ] );
      check( "mixed calibration state is not comparable either",
             Frames.comparability( mixedCal ).uniform, false );

      /*
       * Asymmetric binning: 1x1 against 1x2 agrees on XBINNING and differs
       * only vertically. Pixel FWHM is not comparable across it, and the
       * guard used to read a field nothing ever set, so it always passed.
       */
      var mixedBin = g.H.concat( [ { path: "/m/h5.xisf", filter: "H", exposure: 180,
                                     binning: 1, binningY: 2, width: 6248,
                                     height: 4176, calibrated: true } ] );
      check( "asymmetric binning is not comparable",
             Frames.comparability( mixedBin ).uniform, false );
      check( "and the problem names binning",
             Frames.comparability( mixedBin ).problems.join( " " )
                   .indexOf( "binning" ) >= 0, true );

      /*
       * A filter named like an Object.prototype member. Absurd as a filter,
       * fatal as a bare-object map key: out["constructor"] is inherited and
       * not null, so the group array was never created and push() was called
       * on a function.
       */
      var proto = Frames.groupByFilter( [
         { path: "/m/p1.xisf", filter: "constructor", exposure: 180, binning: 1,
           width: 6248, height: 4176, calibrated: true },
         { path: "/m/p2.xisf", filter: "toString", exposure: 180, binning: 1,
           width: 6248, height: 4176, calibrated: true } ] );
      check( "a filter named after a prototype member still groups",
             proto["constructor"].length, 1 );
      check( "and so does another", proto["toString"].length, 1 );

      /*
       * The same trap inside comparability's own distinct() counter: an
       * inherited key reads as already-seen, so the value is never counted
       * and a genuine mixture reports as uniform.
       */
      var protoType = [
         { path: "/m/t1.xisf", filter: "H", exposure: 180, binning: 1,
           imageType: "toString", width: 6248, height: 4176, calibrated: true },
         { path: "/m/t2.xisf", filter: "H", exposure: 180, binning: 1,
           imageType: "LIGHT", width: 6248, height: 4176, calibrated: true } ];
      check( "a prototype-named image type is still counted",
             Frames.comparability( protoType ).uniform, false );
   } )();

   /*
    * An override is explicit and outranks the formula: somebody looked at the
    * frame at 1:1 and the formula did not.
    */
   check( "a rescued frame is kept even though the formula rejected it",
          Frames.finalState( Frames.STATE.REJECTED, Frames.OVERRIDE.RESCUED ),
          Frames.STATE.APPROVED );
   check( "a condemned frame is dropped even though the formula kept it",
          Frames.finalState( Frames.STATE.APPROVED, Frames.OVERRIDE.CONDEMNED ),
          Frames.STATE.REJECTED );
   check( "no override leaves the verdict alone",
          Frames.finalState( Frames.STATE.REJECTED, null ),
          Frames.STATE.REJECTED );
   /*
    * An unmeasurable frame can be condemned by hand but cannot be "approved":
    * there is no measurement to approve.
    */
   check( "an unmeasurable frame can be condemned",
          Frames.finalState( Frames.STATE.UNMEASURABLE, Frames.OVERRIDE.CONDEMNED ),
          Frames.STATE.REJECTED );
   check( "but rescuing one leaves it unmeasurable",
          Frames.finalState( Frames.STATE.UNMEASURABLE, Frames.OVERRIDE.RESCUED ),
          Frames.STATE.UNMEASURABLE );

   /* ---- leftOut: what Run actually leaves out, per mode ---------------- */
   ( function()
   {
      function r( state, override ) { return { state: state, override: override || null }; }
      var A = Frames.STATE.APPROVED, R = Frames.STATE.REJECTED, U = Frames.STATE.UNMEASURABLE;
      var C = Frames.OVERRIDE.CONDEMNED, S = Frames.OVERRIDE.RESCUED;
      // culling in place: only an enabled channel's rejected frames go
      check( "leftOut cull rejected",          Frames.leftOut( r( R ), true,  false ), true );
      check( "leftOut cull approved",          Frames.leftOut( r( A ), true,  false ), false );
      check( "leftOut cull disabled channel",  Frames.leftOut( r( R ), false, false ), false );
      check( "leftOut cull unmeasurable kept", Frames.leftOut( r( U ), true,  false ), false );
      check( "leftOut cull condemned unmeas.", Frames.leftOut( r( U, C ), true, false ), true );
      check( "leftOut cull rescued kept",      Frames.leftOut( r( R, S ), true, false ), false );
      // copying out: a disabled channel is not copied at all
      check( "leftOut copy rejected",          Frames.leftOut( r( R ), true,  true ), true );
      check( "leftOut copy approved",          Frames.leftOut( r( A ), true,  true ), false );
      check( "leftOut copy disabled channel",  Frames.leftOut( r( A ), false, true ), true );
      check( "leftOut copy condemned unmeas.", Frames.leftOut( r( U, C ), true, true ), true );
      check( "leftOut copy unmeasurable kept", Frames.leftOut( r( U ), true,  true ), false );

      var rows = [ r( A ), r( R ), r( U ), r( U, C ), r( R, S ) ];
      check( "keepCount cull", Frames.keepCount( rows, true, false ), { keep: 3, total: 5 } );
      check( "keepCount cull disabled", Frames.keepCount( rows, false, false ), { keep: 5, total: 5 } );
      check( "keepCount copy disabled", Frames.keepCount( rows, false, true ), { keep: 0, total: 5 } );

      /*
       * Cross-checked against what Run does: buildManifest (culling) lists
       * exactly the rows leftOut names, and the filter approvedPaths
       * applies (copying) keeps exactly the others.
       */
      var paths = rows.map( function( x, i ) { x.path = "/p" + i; return x; } );
      var manifest = Frames.buildManifest( paths ).entries.map( function( e ) { return e.path; } );
      var cull = paths.filter( function( x ) { return Frames.leftOut( x, true, false ); } )
                      .map( function( x ) { return x.path; } );
      check( "leftOut cull equals the manifest", cull, manifest );
      var copied = paths.filter( function( x )
         { return Frames.finalState( x.state, x.override ) != Frames.STATE.REJECTED; } )
         .map( function( x ) { return x.path; } );
      var notLeft = paths.filter( function( x ) { return !Frames.leftOut( x, true, true ); } )
                         .map( function( x ) { return x.path; } );
      check( "leftOut copy equals the copy filter", notLeft, copied );
   } )();

   /* ---- criteria: operators, parsing, per-metric limits ---------------- */
   ( function()
   {
      for ( var i = 0; i < Frames.METRICS.length; ++i )
      {
         var m = Frames.METRICS[i];
         check( "OPERATOR agrees with WORSE_WHEN for " + m, Frames.OPERATOR[m],
                Frames.WORSE_WHEN[m] == "higher" ? "<=" : ">=" );
      }
      check( "CRITERIA_ORDER is every scoring metric once",
             Frames.CRITERIA_ORDER.slice().sort(), Frames.METRICS.slice().sort() );

      check( "parseLimit blank",         Frames.parseLimit( "  " ), null );
      check( "parseLimit number",        Frames.parseLimit( " 4.76 " ), 4.76 );
      check( "parseLimit comma",         Frames.parseLimit( "4,5" ) === undefined, true );
      check( "parseLimit words",         Frames.parseLimit( "abc" ) === undefined, true );
      check( "parseLimit negative",      Frames.parseLimit( "-1" ) === undefined, true );
      check( "parseLimit zero",          Frames.parseLimit( "0" ) === undefined, true );
      check( "parseLimit trailing junk", Frames.parseLimit( "4.7x" ) === undefined, true );

      check( "formatLimit null",  Frames.formatLimit( "fwhm", null ), "" );
      check( "formatLimit fwhm",  Frames.formatLimit( "fwhm", 4.7612 ), "4.76" );
      check( "formatLimit stars", Frames.formatLimit( "stars", 2345.6 ), "2346" );

      var s = Frames.defaultSettings();
      check( "defaults have no typed limits", s.limits, {} );
      var s2 = Frames.withLimit( s, "fwhm", 5 );
      check( "withLimit writes the worse side (hi)", s2.limits.fwhm, { hi: 5 } );
      check( "withLimit does not touch the original", s.limits.fwhm === undefined, true );
      check( "withLimit writes lo for stars", Frames.withLimit( s2, "stars", 900 ).limits.stars, { lo: 900 } );
      check( "null clears back to automatic",
             Frames.withLimit( s2, "fwhm", null ).limits.fwhm === undefined, true );
      var legacy = Frames.copyOf( s ); legacy.limits = { fwhm: { lo: 4, hi: 9 } };
      check( "writing a limit replaces the whole object",
             Frames.withLimit( legacy, "fwhm", 6 ).limits.fwhm, { hi: 6 } );

      // an unticked criterion rejects nothing and does not narrow the band
      var lim = { fwhm: { hi: 4 } };
      var metrics = { fwhm: 5, eccentricity: 0.5, stars: 1000, psfSNR: 10 };
      check( "a ticked typed limit rejects",
             Frames.absoluteFailures( metrics, lim, { fwhm: true } ).length, 1 );
      check( "an unticked typed limit rejects nothing",
             Frames.absoluteFailures( metrics, lim, { fwhm: false } ).length, 0 );
      check( "omitted gating means all on",
             Frames.absoluteFailures( metrics, lim ).length, 1 );
      var off = Frames.copyOf( s ); off.limits = lim;
      off.gating = { fwhm: false, eccentricity: true, stars: true, psfSNR: false };
      check( "an unticked typed limit does not narrow the band",
             Frames.acceptedBand( "fwhm", {}, off ), { lo: null, hi: null } );
      check( "verdict honours the checkbox",
             Frames.verdict( metrics, {}, off ).state, Frames.STATE.APPROVED );
      off.gating = { fwhm: true, eccentricity: true, stars: true, psfSNR: false };
      check( "and rejects when it is ticked",
             Frames.verdict( metrics, {}, off ).state, Frames.STATE.REJECTED );

      // what each box shows
      var gates = { fwhm: { active: true, limit: 4.2 }, stars: { active: false, limit: null } };
      var auto = Frames.copyOf( s ); auto.limits = {};
      check( "an untouched box shows the gate, greyed",
             Frames.displayLimit( "fwhm", gates, auto ), { value: 4.2, auto: true } );
      check( "with no usable gate it is blank",
             Frames.displayLimit( "stars", gates, auto ), { value: null, auto: true } );
      check( "a typed box shows its own number",
             Frames.displayLimit( "fwhm", gates, Frames.withLimit( auto, "fwhm", 9 ) ),
             { value: 9, auto: false } );

      // autoGates: only a metric with a typed limit loses its gate
      var ag = Frames.autoGates( { fwhm: { active: true, limit: 4 },
                                   stars: { active: true, limit: 7000 } }, { fwhm: { hi: 9 } } );
      check( "a typed metric's gate is set aside", ag.fwhm.active, false );
      check( "the others keep theirs", ag.stars.active, true );
   } )();

   /* ---- recompute: per-metric limits on a real-shaped channel ---------- */
   ( function()
   {
      var ch = Frames.recompute( fsFixtureChannel() );
      var autoStates = ch.rows.map( function( r ) { return r.state; } );
      check( "the fixture has genuine rejects",
             autoStates.filter( function( s ) { return s == Frames.STATE.REJECTED; } ).length, 2 );
      check( "the FWHM gate is active", ch.gates.fwhm.active, true );

      // typing the gate's own value changes nothing (both comparisons strict)
      ch.settings = Frames.withLimit( ch.settings, "fwhm", ch.gates.fwhm.limit );
      Frames.recompute( ch );
      check( "typing the automatic value changes no verdict",
             ch.rows.map( function( r ) { return r.state; } ), autoStates );

      // a typed limit decides, and survives a k change
      ch.settings = Frames.withLimit( ch.settings, "fwhm", 6.6 );
      Frames.recompute( ch );
      check( "a typed limit decides: 6.5 now kept", ch.rows[18].state, Frames.STATE.APPROVED );
      check( "and 7.0 still goes", ch.rows[19].state, Frames.STATE.REJECTED );
      ch.settings.k = 1.0; ch.settings.kEdited = true;
      Frames.recompute( ch );
      check( "a typed limit survives a k change", Frames.limitValue( ch.settings, "fwhm" ), 6.6 );
      check( "and still decides FWHM after it", ch.rows[18].state, Frames.STATE.APPROVED );

      // clearing it returns FWHM to the relative gate
      ch.settings = Frames.withLimit( ch.settings, "fwhm", null );
      ch.settings.k = 2.5;
      Frames.recompute( ch );
      check( "clearing returns to automatic",
             ch.rows.map( function( r ) { return r.state; } ), autoStates );

      var other = Frames.recompute( fsFixtureChannel() );
      check( "another channel's settings are untouched", other.settings.limits, {} );

      var empty = Frames.newChannel( "X", [ { path: "/x", identity: null } ], {}, [] );
      check( "an unmeasured channel recomputes", Frames.recompute( empty ).rows[0].state,
             Frames.STATE.UNMEASURABLE );
   } )();

   /* ---- background and SNR: optional, never fatal ----------------------- */
   ( function()
   {
      check( "SNR estimate is column 9", Frames.COL.snr, 9 );
      check( "median is column 10", Frames.COL.median, 10 );

      var row = []; for ( var c = 0; c < 30; ++c ) row.push( 0 );
      row[3] = "/a.xisf"; row[5] = 3.5; row[6] = 0.5; row[12] = 0.001;
      row[14] = 7507; row[28] = 8.815; row[9] = NaN; row[10] = NaN;
      var m = Frames.metricsFromRow( row );
      check( "meaningProblems ignores columns 9 and 10", Frames.meaningProblems( m ), [] );
      check( "optionalProblems names both bad fields",
             Frames.optionalProblems( m ), [ "background", "snrWeight" ] );

      row[9] = 42.5; row[10] = 0.021;
      m = Frames.metricsFromRow( row );
      check( "metricsFromRow reads background", m.background, 0.021 );
      check( "metricsFromRow reads SNR", m.snrWeight, 42.5 );
      check( "altitude is column 20, star flux 21", [ Frames.COL.altitude, Frames.COL.psfFlux ], [ 20, 21 ] );
      row[20] = 58.3; row[21] = 647.3;
      m = Frames.metricsFromRow( row );
      check( "metricsFromRow reads altitude and flux", [ m.altitude, m.psfFlux ], [ 58.3, 647.3 ] );
      check( "a good row has no optional problems", Frames.optionalProblems( m ), [] );

      // schema scope: a disabled field is null whatever it holds
      check( "sanitize nulls a disabled field",
             Frames.sanitizeOptional( { background: 0.02, snrWeight: 3, altitude: 50, psfFlux: 600 },
                                      { background: true } ),
             { background: null, snrWeight: 3, altitude: 50, psfFlux: 600 } );
      // value scope: one bad reading nulls only itself
      check( "sanitize nulls only a bad value",
             Frames.sanitizeOptional( { background: NaN, snrWeight: 3, altitude: 50, psfFlux: 600 }, {} ),
             { background: null, snrWeight: 3, altitude: 50, psfFlux: 600 } );
      check( "sanitize nulls a negative value",
             Frames.sanitizeOptional( { background: -1, snrWeight: 3 }, {} ).background, null );

      var stored = Frames.storedMetrics( { path: "/a", fwhm: 1, eccentricity: 0.5,
                                           noise: 0.1, stars: 10, psfSNR: 2.5 } );
      check( "storedMetrics has no path", stored.path === undefined, true );
      check( "storedMetrics carries missing optionals as null",
             [ stored.background, stored.snrWeight ], [ null, null ] );

      check( "a v1-shaped entry is not usable",
             Frames.cacheEntryUsable( { fwhm: 1, eccentricity: 0.5, noise: 0.1,
                                        stars: 10, psfSNR: 2.5 } ), false );
      check( "a v2 entry with null optionals is usable", Frames.cacheEntryUsable( stored ), true );
      check( "null is not usable", Frames.cacheEntryUsable( null ), false );
   } )();

   /* ---- SNR column: shown, never judged -------------------------------- */
   ( function()
   {
      check( "display order puts SNR after PSF SNR", Frames.DISPLAY_METRICS,
             [ "psfSNR", "snrWeight", "fwhm", "eccentricity", "stars" ] );
      check( "SNR is not a scoring metric", Frames.METRICS.indexOf( "snrWeight" ), -1 );
      check( "SNR column sits right after PSF SNR",
             Frames.metricColumn( "snrWeight" ), Frames.metricColumn( "psfSNR" ) + 1 );
      check( "SNR heading", Frames.FRAME_COLUMNS[Frames.metricColumn( "snrWeight" )], "SNR" );
      check( "score is the last column", Frames.SCORE_COLUMN, Frames.FRAME_COLUMNS.length - 1 );
      check( "null shows a dash", Frames.displayValue( "snrWeight", null ), "-" );
      check( "stars shows whole", Frames.displayValue( "stars", 8000 ), "8000" );

      var ch = Frames.recompute( fsFixtureChannel() );
      var scoreBefore = ch.rows[3].score, stateBefore = ch.rows[3].state;
      ch.rows[3].metrics.snrWeight = 0.0001;              // terrible SNR
      Frames.recompute( ch );
      check( "a terrible SNR is still approved", ch.rows[3].state, stateBefore );
      check( "and its score is unchanged", ch.rows[3].score, scoreBefore );
      check( "SNR has no band",
             Frames.acceptedBand( "snrWeight", ch.gates, ch.settings ), { lo: null, hi: null } );
   } )();

   /* ---- anomaly flags: advisory, per metric, never a verdict ------------ */
   ( function()
   {
      function M( o ) { var b = { fwhm: 4, eccentricity: 0.45, stars: 8000, psfFlux: 600,
                                  background: 0.02, psfSNR: 10, altitude: 70 };
                        for ( var k in o ) b[k] = o[k]; return b; }
      function spread( n, f ) { var a = []; for ( var i = 0; i < n; ++i ) a.push( f( i ) ); return a; }
      function none( list ) { return list.every( function( f ) { return f.length == 0; } ); }
      function minutes( n ) { return spread( n, function( i ) { return 1790000000000 + i*60000; } ); }
      // A steady night: FWHM within 4%, eccentricity with real spread, flat background.
      var base = spread( 12, function( i ) { return M( { fwhm: 3.9 + 0.01*i,
         eccentricity: 0.40 + 0.01*i, stars: 7800 + 40*i, background: 0.020 + 0.00002*i } ); } );

      // blur, and what explains it
      var wide = base.concat( [ M( { fwhm: 9 } ) ] );
      check( "FOCUS: blur not explained by altitude, no time order",
             Frames.anomalyFlags( wide, true )[12], [ "focus" ] );
      check( "and nothing on its neighbours", none( Frames.anomalyFlags( wide, true ).slice( 0, 12 ) ), true );
      check( "ALTITUDE: blur the airmass explains",
             Frames.anomalyFlags( base.concat( [ M( { fwhm: 5.6, altitude: 30 } ) ] ), true )[12],
             [ "altitude" ] );
      check( "not ALTITUDE when the correction still leaves it wide",
             Frames.anomalyFlags( base.concat( [ M( { fwhm: 7.5, altitude: 30 } ) ] ), true )[12],
             [ "focus" ] );
      var t13 = minutes( 13 );
      check( "SEEING: one frame blurred, its neighbours in time sharp",
             Frames.anomalyFlags( base.concat( [ M( { fwhm: 9 } ) ] ), true, t13 )[12], [ "seeing" ] );
      var run = base.slice( 0, 10 ).concat( [ M( { fwhm: 9 } ), M( { fwhm: 9 } ), M( { fwhm: 3.95 } ) ] );
      check( "FOCUS: blur that persists across consecutive frames",
             Frames.anomalyFlags( run, true, t13 ).slice( 10, 12 ), [ [ "focus" ], [ "focus" ] ] );
      check( "no altitude reading means no altitude correction",
             Frames.anomalyFlags( base.concat( [ M( { fwhm: 5.6, altitude: 0 } ) ] ), true )[12],
             [ "focus" ] );
      check( "a spread under 20% is not blur",
             Frames.anomalyFlags( base.concat( [ M( { fwhm: 4.5 } ) ] ), true )[12], [] );

      check( "TRACKING fires on an eccentricity spike",
             Frames.anomalyFlags( base.concat( [ M( { eccentricity: 0.9 } ) ] ), true )[12], [ "tracking" ] );

      // cloud: dimming or a brighter sky, never just fewer stars
      check( "CLOUD: star flux down by more than a quarter",
             Frames.anomalyFlags( base.concat( [ M( { psfFlux: 400 } ) ] ), true )[12], [ "cloud" ] );
      check( "extinction at low altitude is not cloud",
             Frames.anomalyFlags( base.concat( [ M( { psfFlux: 510, altitude: 30, fwhm: 3.9 } ) ] ), true )[12],
             [] );
      check( "CLOUD: background up",
             Frames.anomalyFlags( base.concat( [ M( { background: 0.2 } ) ] ), true )[12], [ "cloud" ] );
      check( "fewer stars alone names nothing",
             Frames.anomalyFlags( base.concat( [ M( { stars: 6000 } ) ] ), true )[12], [] );
      var steady = spread( 12, function() { return M( { background: 0.00774 } ); } );
      check( "a one-step background difference is not cloud",
             Frames.anomalyFlags( steady.concat( [ M( { background: 0.00774 + 1/65535 } ) ] ), true )[12], [] );
      check( "4% above the median background is not cloud",
             Frames.anomalyFlags( steady.concat( [ M( { background: 0.00774*1.04 } ) ] ), true )[12], [] );
      check( "6% above it is",
             Frames.anomalyFlags( steady.concat( [ M( { background: 0.00774*1.06 } ) ] ), true )[12], [ "cloud" ] );
      check( "no flux reading falls back to background alone",
             Frames.anomalyFlags( base.map( function( m ) { var c = M( m ); c.psfFlux = null; return c; } )
                                  .concat( [ M( { psfFlux: null, background: 0.2 } ) ] ), true )[12], [ "cloud" ] );

      check( "DROPPED on almost no stars",
             Frames.anomalyFlags( base.concat( [ M( { stars: 40 } ) ] ), true )[12], [ "dropped" ] );
      var flat = [ M( { stars: 1000 } ), M( { stars: 1000 } ), M( { stars: 1000 } ),
                   M( { stars: 1000 } ), M( { stars: 0 } ) ];
      check( "zero stars is DROPPED (a real count)", Frames.anomalyFlags( flat, true )[4], [ "dropped" ] );
      check( "four frames flag nothing", none( Frames.anomalyFlags( flat.slice( 1 ), true ) ), true );
      check( "flags keep their fixed order",
             Frames.anomalyFlags( base.concat( [ M( { fwhm: 9, eccentricity: 0.9, background: 0.2 } ) ] ), true )[12],
             [ "focus", "tracking", "cloud" ] );
      check( "an unmeasured row is skipped, not thrown on",
             Frames.anomalyFlags( base.concat( [ null ] ), true )[12], [] );
      check( "not comparable flags nothing", Frames.anomalyFlags( wide, false )[12], [] );
      check( "an all-unmeasured channel flags nothing",
             none( Frames.anomalyFlags( [ null, null, null, null, null, null ], true ) ), true );

      check( "summary counts frames, lists kinds",
             Frames.flagSummary( [ [ "focus", "cloud" ], [], [ "cloud" ], [ "altitude" ], [ "seeing" ] ] ),
             "4 flagged: 2 cloud, 1 focus, 1 seeing, 1 altitude" );
      check( "empty summary", Frames.flagSummary( [ [], [] ] ), "" );

      var R = { state: Frames.STATE.REJECTED, override: null };
      var A = { state: Frames.STATE.APPROVED, override: null };
      check( "tags: rejected first, then each flag separately",
             Frames.frameTags( R, [ "altitude", "cloud" ], true, false ),
             [ { text: "REJECTED", kind: "reject" }, { text: "ALTITUDE", kind: "flag" },
               { text: "CLOUD", kind: "flag" } ] );
      check( "tags: channel off while copying",
             Frames.frameTags( A, [], false, true ), [ { text: "CHANNEL OFF", kind: "reject" } ] );
      check( "every flag has a tag and a badge",
             Frames.FLAG_ORDER.every( function( k ) { return !!Frames.FLAG_TAG[k] && !!Frames.FLAG_BADGE[k]; } ),
             true );
      check( "row tooltip: verdict, reasons, override",
             Frames.rowTooltip( { path: "/a.xisf", state: Frames.STATE.REJECTED,
                                  override: Frames.OVERRIDE.CONDEMNED,
                                  reasons: [ "FWHM 6 above the limit of 5" ] }, [] ),
             "/a.xisf\nrejected (condemned): FWHM 6 above the limit of 5" );
      check( "row tooltip: what it looks like",
             Frames.rowTooltip( { path: "/b.xisf", state: Frames.STATE.APPROVED,
                                  override: null, reasons: [] }, [ "cloud", "focus" ] ),
             "/b.xisf\napproved\nlooks like: cloud, focus" );

      var ch = fsFixtureChannel();
      check( "a channel carries one flag list per row", ch.flags.length, ch.rows.length );
      var mixed = Frames.newChannel( "O", ch.entries, ch.metrics, [ "exposure differs" ] );
      check( "a non-comparable channel is not flagged", none( mixed.flags ), true );
      var noFilter = Frames.newChannel( Frames.NO_FILTER, ch.entries,
                                        { "/fx/f0.xisf": M( { fwhm: 99 } ) }, [] );
      check( "the no-FILTER group is not flagged", none( noFilter.flags ), true );
      var timed = ch.entries.map( function( e, i ) { var c = Frames.copyOf( e ); c.time = "2026-09-18T0" + ( i % 10 ) + ":00:00"; return c; } );
      check( "entry times reach the channel's rows",
             Frames.newChannel( "O", timed, ch.metrics, [] ).rows[3].time, "2026-09-18T03:00:00" );
      check( "an observation time parses as UTC", Frames.obsTime( "'2026-09-18T03:29:11.730898'" ),
             Date.UTC( 2026, 8, 18, 3, 29, 11, 730 ) );
      check( "no time is null", Frames.obsTime( null ), null );
   } )();

   /* ---- emptying the output folder: what may never be emptied ---------- */
   ( function()
   {
      var src = [ "/data/night/O/a.xisf", "/data/night/S/b.xisf" ];
      check( "an unrelated folder may be emptied",
             Frames.emptyRefusal( src, "/data/out", "/Users/me" ), null );
      check( "a sibling with a shared prefix is not a parent",
             Frames.emptyRefusal( src, "/data/night/O2", "/Users/me" ), null );
      check( "not a source folder",
             Frames.emptyRefusal( src, "/data/night/O", "/Users/me" ) != null, true );
      check( "not a folder that CONTAINS a source folder",
             Frames.emptyRefusal( src, "/data/night", "/Users/me" ) != null, true );
      check( "however far up",
             Frames.emptyRefusal( src, "/data", "/Users/me" ) != null, true );
      check( "a trailing slash changes nothing",
             Frames.emptyRefusal( src, "/data/night/", "/Users/me" ) != null, true );
      check( "never the filesystem root", Frames.emptyRefusal( [], "/", "/Users/me" ) != null, true );
      check( "never the home folder",
             Frames.emptyRefusal( [], "/Users/me/", "/Users/me" ) != null, true );
      check( "never without a folder", Frames.emptyRefusal( src, null, "/Users/me" ) != null, true );
   } )();

   /* ---- filmstrip ordering --------------------------------------------- */
   ( function()
   {
      check( "strip centres the selection",  Frames.stripFirst( 74, 40, 11 ), 35 );
      check( "strip clamps at the start",    Frames.stripFirst( 74, 2, 11 ), 0 );
      check( "strip clamps at the end",      Frames.stripFirst( 74, 73, 11 ), 63 );
      check( "strip wider than the channel", Frames.stripFirst( 5, 3, 11 ), 0 );
      /*
       * A selection already on screen does not move the strip: clicking a
       * tile must leave the clicked frame under the mouse. Only a selection
       * off screen (from the table or the plot) brings it into view.
       */
      check( "a visible selection keeps the strip still", Frames.stripFirst( 74, 20, 11, 11 ), 11 );
      check( "at either edge of the view too",
             [ Frames.stripFirst( 74, 11, 11, 11 ), Frames.stripFirst( 74, 21, 11, 11 ) ], [ 11, 11 ] );
      check( "a selection off to the right is centred", Frames.stripFirst( 74, 40, 11, 11 ), 35 );
      check( "a selection off to the left is centred", Frames.stripFirst( 74, 3, 11, 11 ), 0 );
      check( "a view past the end is pulled back", Frames.stripFirst( 20, 15, 11, 15 ), 9 );
      // which frame a click lands on: none in the empty space after the tiles
      check( "a click on a tile picks it",      Frames.tileAt( 200, 128, 8, 5, 20 ), 6 );
      check( "left of the strip picks nothing", Frames.tileAt( -3, 128, 8, 0, 20 ), -1 );
      check( "past the last drawn tile nothing", Frames.tileAt( 1100, 128, 8, 0, 20 ), -1 );
      check( "past the channel's end nothing",   Frames.tileAt( 300, 128, 8, 18, 20 ), -1 );

      var o = Frames.thumbnailOrder( 20, 7, 2, 11 );
      check( "every index exactly once", o.slice().sort( function( a, b ) { return a - b; } ),
             [ 0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19 ] );
      check( "the selection first", o[0], 7 );
      check( "the visible range before anything else",
             o.slice( 0, 11 ).every( function( i ) { return i >= 2 && i < 13; } ), true );
      check( "then outward from the selection", o.slice( 11, 13 ), [ 1, 13 ] );
      check( "selection at the end", Frames.thumbnailOrder( 3, 2, 0, 11 ), [ 2, 1, 0 ] );
      check( "empty channel", Frames.thumbnailOrder( 0, -1, 0, 11 ), [] );
   } )();

   ( function()
   {
      var rows = [
         { state: Frames.STATE.APPROVED,     override: null },
         { state: Frames.STATE.APPROVED,     override: null },
         { state: Frames.STATE.REJECTED,     override: Frames.OVERRIDE.RESCUED },
         { state: Frames.STATE.REJECTED,     override: null },
         { state: Frames.STATE.APPROVED,     override: Frames.OVERRIDE.CONDEMNED },
         { state: Frames.STATE.UNMEASURABLE, override: null } ];
      var c = Frames.counts( rows );
      check( "kept counts the rescued frame", c.kept, 3 );
      check( "rejected counts the condemned one", c.rejected, 2 );
      check( "the unmeasurable frame is neither", c.unmeasurable, 1 );
      /*
       * Hand edits are counted separately so a summary never hides one.
       */
      check( "rescues are reported", c.rescued, 1 );
      check( "and condemnations", c.condemned, 1 );
      check( "the summary names both",
             Frames.summaryLine( "H", c ),
             "H: 3 kept of 6 (+1 rescued, -1 condemned, 1 unmeasurable)" );
   } )();

   ( function()
   {
      var rows = [
         { path: "/m/a.xisf", state: Frames.STATE.REJECTED, reasons: [ "FWHM" ],
           digest: "aaa", size: 100, mtime: 5 },
         { path: "/m/b.xisf", state: Frames.STATE.APPROVED, reasons: [],
           digest: "bbb", size: 100, mtime: 5 },
         { path: "/m/c.xisf", state: Frames.STATE.UNMEASURABLE, reasons: [],
           digest: "ccc", size: 100, mtime: 5 } ];
      var man = Frames.buildManifest( rows );
      /*
       * Only rejected frames are actionable. An unmeasurable frame is never
       * deleted automatically -- there is no measurement behind the verdict.
       */
      check( "only rejected frames enter the manifest", man.entries.length, 1 );
      check( "and it is the rejected one", man.entries[0].path, "/m/a.xisf" );
      check( "carrying the digest taken at measurement",
             man.entries[0].digest, "aaa" );

      /*
       * The lifecycle, as a state machine rather than a paragraph. The review
       * is editable only in REVIEW: otherwise a knob change could alter the
       * decisions of a manifest that is already deleting files.
       */
      check( "the review starts editable", Frames.canEdit( Frames.PHASE.REVIEW ), true );
      check( "and is locked while executing",
             Frames.canEdit( Frames.PHASE.EXECUTING ), false );
      check( "a stopped run is still locked",
             Frames.canEdit( Frames.PHASE.STOPPED ), false );
      check( "committing enters execution",
             Frames.nextPhase( Frames.PHASE.REVIEW, "commit" ), Frames.PHASE.EXECUTING );
      check( "a stopped run may be resumed",
             Frames.nextPhase( Frames.PHASE.STOPPED, "resume" ), Frames.PHASE.EXECUTING );
      check( "or abandoned, which is the only way back to editing",
             Frames.nextPhase( Frames.PHASE.STOPPED, "abandon" ), Frames.PHASE.REVIEW );
      /*
       * There is no edit transition out of EXECUTING, and no second commit:
       * pressing Apply twice must not run two manifests over one cohort.
       */
      check( "a second commit while executing is refused",
             Frames.nextPhase( Frames.PHASE.EXECUTING, "commit" ), null );

      check( "everything is pending before execution",
             Frames.manifestPending( man ).length, 1 );
      Frames.recordOutcome( man, "/m/a.xisf", "deleted", "" );
      check( "a recorded outcome is no longer pending",
             Frames.manifestPending( man ).length, 0 );
      /*
       * Resuming skips what is done. Recomputing instead would be iterative
       * clipping: remove the worst frame and the survivors' MAD tightens, so
       * the next pass takes the next-worst -- a frame nobody condemned.
       */
      Frames.recordOutcome( man, "/m/a.xisf", "deleted", "" );
      check( "recording twice does not duplicate the entry", man.entries.length, 1 );
   } )();

   ( function()
   {
      var e = { path: "/m/a.xisf", digest: "aaa", size: 100, mtime: 5 };
      check( "identity matches when the digest does",
             Frames.identityMatches( e, { digest: "aaa", size: 100, mtime: 5 } ), true );
      /*
       * Path, size and mtime are all preservable by a replacement. The
       * digest is what authorises a deletion.
       */
      check( "and fails when only the digest changed",
             Frames.identityMatches( e, { digest: "zzz", size: 100, mtime: 5 } ), false );
      check( "a missing current file never matches",
             Frames.identityMatches( e, null ), false );
   } )();

   ( function()
   {
      var m = Frames.outputName( "/src/Light_0001_c.xisf" );
      check( "an output keeps its name", m, "Light_0001_c.xisf" );

      /*
       * Two sources that would produce one output name must abort the
       * channel. Writing both would leave one output and, if originals were
       * ever deleted, would destroy the frame that lost the race.
       */
      var r = Frames.outputMapping(
         [ "/a/Light_0001_c.xisf", "/b/Light_0001_c.xisf" ], "/dest", ".xisf" );
      check( "a name collision is detected", r.collisions.length, 1 );

      var ok = Frames.outputMapping(
         [ "/a/one.xisf", "/a/two.xisf" ], "/dest", ".xisf" );
      check( "distinct names map cleanly", ok.collisions.length, 0 );
      check( "and land in the destination",
             ok.mapping["/a/one.xisf"], "/dest/one.xisf" );

      /*
       * Writing into the source directory is refused: it makes "the output"
       * and "the original" the same file.
       */
      check( "the destination may not be the source",
             Frames.outputMapping( [ "/a/one.xisf" ], "/a", ".xisf" ).aliased, true );
      /*
       * The output EXTENSION is part of the mapping, because converting on
       * output creates collisions the source names do not show: a.fit and
       * a.xisf both become a.xisf.
       */
      check( "a conversion collision is detected",
             Frames.outputMapping( [ "/a/one.fit", "/a/one.xisf" ],
                                   "/dest", ".xisf" ).collisions.length, 1 );
   } )();

   /*
    * A cache entry is recorded only once it has been proved readable.
    *
    * saveAs says nothing when a write goes wrong, so the sidecar used to be
    * written regardless and the broken entry was found by the NEXT run --
    * which could only respond by silently redoing the work. These drive
    * Cache.store through a stubbed verifier, because the real one opens an
    * image and there is no workspace here.
    */
   ( function()
   {
      var realVerify = Cache.verifyStoredFile;
      var realDir = Cache.overrideDir;
      var dir = "/tmp/agent-scratch/loom-cache-verify";
      try
      {
         if ( !File.directoryExists( dir ) )
            File.createDirectory( dir );
         Cache.setDir( dir );

         var key = "0123456789abcdef0123456789abcdef01234567";
         var written = [];
         var win = { saveAs: function( path )
                     {
                        written.push( path );
                        File.writeTextFile( path, "pretend image" );
                     } };

         // A write that reads back: the entry and its sidecar are recorded.
         Cache.verifyStoredFile = function() { return null; };
         var good = Cache.store( key, win, { stage: "test" } );
         check( "a verified entry is recorded", good, Cache.pathFor( key ) );
         check( "and its sidecar is written",
                File.exists( Cache.metaPathFor( key ) ), true );

         // A write that does not read back: nothing is left behind.
         File.remove( Cache.metaPathFor( key ) );
         Cache.verifyStoredFile = function() { return "it contains no readable image"; };
         var bad = Cache.store( key, win, { stage: "test" } );
         check( "an entry that will not read back is not recorded", bad, null );
         check( "the unreadable file is removed",
                File.exists( Cache.pathFor( key ) ), false );
         /*
          * The sidecar is what makes a broken entry look valid, so its
          * absence is the property that actually matters here.
          */
         check( "and no sidecar is left claiming it is good",
                File.exists( Cache.metaPathFor( key ) ), false );

         // The same rule for a companion.
         var cbad = Cache.storeCompanion( key, "stars", win );
         check( "an unreadable companion is not recorded", cbad, null );
         check( "and its file is removed too",
                File.exists( Cache.companionPathFor( key, "stars" ) ), false );

         Cache.verifyStoredFile = function() { return null; };
         var cgood = Cache.storeCompanion( key, "stars", win );
         check( "a verified companion is recorded", cgood,
                Cache.companionPathFor( key, "stars" ) );
         try { File.remove( Cache.companionPathFor( key, "stars" ) ); } catch ( e ) {}
         try { File.remove( Cache.pathFor( key ) ); } catch ( e ) {}
         try { File.remove( Cache.metaPathFor( key ) ); } catch ( e ) {}
      }
      finally
      {
         Cache.verifyStoredFile = realVerify;
         Cache.setDir( realDir );
      }
   } )();

   check( "the shipped model container is recognised",
          Steps.isMLDenoiseModelName( "MLDenoise_v41.xmlm" ), true );
   check( "case does not matter",
          Steps.isMLDenoiseModelName( "DeNoise_v2.XMLM" ), true );
   check( "BlurXTerminator's model is not a denoise model",
          Steps.isMLDenoiseModelName( "BlurXTerminator.4.xmlm" ), false );
   check( "and a bare .onnx is not the container MLDenoise wants",
          Steps.isMLDenoiseModelName( "denoise.onnx" ), false );
   check( "nor is a denoise-named file that is not a model at all",
          Steps.isMLDenoiseModelName( "denoise-notes.txt" ), false );
   check( "the install's library is searched first",
          Steps.mlDenoiseModelDirs()[0], Steps.PI_BASE_DIR + "/library" );

   /*
    * Exactly ONE denoise stage is ever in a chain. Two would denoise twice;
    * none would silently drop the step the user asked for.
    */
   function denoiseSlots( tool )
   {
      var cfg = { noiseTool: tool, noiseLevel: "medium", stretch: true };
      return ( Pipeline.linearDenoiseParams( cfg ) != null ? "linear" : "" ) +
             ( Pipeline.stretchedDenoiseParams( cfg ) != null ? "stretched" : "" );
   }
   check( "NXT occupies the linear slot only",
          denoiseSlots( Steps.NR_TOOL_NXT ), "linear" );
   check( "MLDenoise occupies the linear slot only",
          denoiseSlots( Steps.NR_TOOL_MLDENOISE ), "linear" );
   check( "Prism occupies the post-stretch slot only",
          denoiseSlots( Steps.NR_TOOL_PRISM ), "stretched" );
   check( "with no tool, neither slot is filled",
          denoiseSlots( "none" ), "" );

   /*
    * The order within the chain is the whole point: the linear denoise must
    * sit AFTER extraction -- so the stars plate is never denoised -- and
    * BEFORE the stretch.
    */
   function rgbStageOrder( tool )
   {
      var params = {
         combine: {}, spccRGB: {},
         sharpenRGB: { tool: "BlurXTerminator", stars: "low" },
         extractRGB: { tool: "StarXTerminator" }
      };
      var cfg = { noiseTool: tool, noiseLevel: "medium", stretch: true };
      if ( Pipeline.linearDenoiseParams( cfg ) != null )
         params.denoiseLinearRGB = Pipeline.linearDenoiseParams( cfg );
      params.stretchRGB = { target: 0.25, linked: true };
      if ( Pipeline.stretchedDenoiseParams( cfg ) != null )
         params.denoiseRGB = Pipeline.stretchedDenoiseParams( cfg );
      var chain = Pipeline.buildStageKeys( "src", params );
      var names = [];
      for ( var i = 0; i < chain.length; ++i )
         names.push( chain[i].stage );
      return names.join( "," );
   }
   check( "NXT lands between extraction and the stretch",
          rgbStageOrder( Steps.NR_TOOL_NXT ),
          "combine,spccRGB,sharpenRGB,extractRGB,denoiseLinearRGB,stretchRGB" );
   check( "MLDenoise lands in the same place",
          rgbStageOrder( Steps.NR_TOOL_MLDENOISE ),
          "combine,spccRGB,sharpenRGB,extractRGB,denoiseLinearRGB,stretchRGB" );
   check( "Prism stays after the stretch",
          rgbStageOrder( Steps.NR_TOOL_PRISM ),
          "combine,spccRGB,sharpenRGB,extractRGB,stretchRGB,denoiseRGB" );

   /*
    * Medium is the tool's own default for every tool, so "Medium" means
    * "what its author considered normal" rather than a number invented in
    * this script.
    */
   check( "MLDenoise Medium is the module's own default amount",
          Steps.NOISE_LEVELS.mldenoise.medium, 0.90 );
   check( "...with Low and High bracketing it",
          Steps.NOISE_LEVELS.mldenoise.low < Steps.NOISE_LEVELS.mldenoise.medium &&
          Steps.NOISE_LEVELS.mldenoise.high > Steps.NOISE_LEVELS.mldenoise.medium,
          true );
   /*
    * The header comment above NOISE_LEVELS said for a long time that "High"
    * sat at each tool's own default while the table and the rest of the same
    * comment said Medium did. Medium is correct. The prose is asserted
    * because a comment that contradicts the code it introduces is how the
    * wrong level gets chosen by the next reader.
    */
   var noiseSrc = File.readTextFile( LOOM_DIR + "/lib/Steps.js" );
   check( "no comment claims High is the tools' own default",
          noiseSrc.indexOf( "\"High\" sits at each tool's own default" ), -1 );
   check( "the ladder's header says Medium does",
          noiseSrc.indexOf( "\"Medium\" sits at each tool's own default" ) >= 0,
          true );

   /*
    * The README's Process table is user-facing documentation of WHERE the
    * denoise runs, and that moved: it is no longer unconditionally after the
    * stretch. Documentation drift is invisible until a user follows it, so
    * the claim is asserted rather than trusted.
    */
   var readmeSrc = File.readTextFile( LOOM_DIR + "/../README.md" );
   check( "the README no longer calls denoise last, after the stretch",
          readmeSrc.indexOf( "last, after the stretch" ), -1 );
   check( "the README offers MLDenoise alongside the other two",
          readmeSrc.indexOf( "MLDenoise" ) >= 0, true );

   /*
    * Moving a stage changes its cache key, so old entries cannot be served
    * as if they described the new placement.
    */
   check( "the linear and post-stretch stages are different cache stages",
          Pipeline.buildStageKeys( "s",
             { denoiseLinearRGB: { tool: "x", level: "medium" } } )[0].key !=
          Pipeline.buildStageKeys( "s",
             { denoiseRGB: { tool: "x", level: "medium" } } )[0].key, true );

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
    * The byte swap. This is the loop the threads run, and the only part of
    * the writer that touches pixels, so what it produces is what Photoshop
    * reads. PSB is big-endian and this machine is not.
    */
   ( function()
   {
      var src = new Uint16Array( [ 0x0102, 0xFFEE, 0x0000, 0x8001 ] );
      var dst = new Uint8Array( 8 );
      Psb.swapRange( src, dst, 0, 4 );
      check( "the swap writes samples big-endian",
             Array.prototype.join.call( dst, "," ), "1,2,255,238,0,0,128,1" );

      /*
       * Every thread owns a half-open slice of the chunk and must write
       * that slice and nothing else -- there is no synchronization, and a
       * thread that ran past its end would corrupt its neighbour's bytes.
       */
      var partial = new Uint8Array( 8 );
      Psb.swapRange( src, partial, 1, 3 );
      check( "the swap touches only its own range",
             Array.prototype.join.call( partial, "," ), "0,0,255,238,0,0,0,0" );

      var empty = new Uint8Array( 4 );
      Psb.swapRange( src, empty, 2, 2 );
      check( "an empty range writes nothing",
             Array.prototype.join.call( empty, "," ), "0,0,0,0" );
   } )();

   /*
    * The thread body is generated source, not a function this file can call
    * directly, and a thread that fails does so far away from here. It can
    * still be exercised without any thread at all: build it with a stand-in
    * Thread object, run it over ordinary buffers, and require that it
    * produces exactly what the serial loop produces. That is the assertion
    * that keeps the two paths from drifting apart -- they share one loop,
    * and this proves the shared loop survives being pasted into a thread.
    */
   ( function()
   {
      var src = new Uint16Array( [ 0x0102, 0xFFEE, 0x1234, 0x00FF, 0xABCD ] );
      var expected = new Uint8Array( 10 );
      Psb.swapRange( src, expected, 0, 5 );

      // the parameter shadows the global, so no core object is touched
      var make = new Function( "Thread", "return " + Psb.swapThreadSource() + ";" );
      var body = make( { sharedBuffer: function( x ) { return x; } } );

      var dst = new Uint8Array( 10 );
      var swapped = body( { src: src.buffer, dst: dst.buffer, begin: 0, end: 5 } );

      check( "the thread body swaps exactly as the serial loop does",
             Array.prototype.join.call( dst, "," ),
             Array.prototype.join.call( expected, "," ) );
      check( "the thread body reports the samples it did", swapped, 5 );

      var partial = new Uint8Array( 10 );
      body( { src: src.buffer, dst: partial.buffer, begin: 2, end: 4 } );
      var partialExpected = new Uint8Array( 10 );
      Psb.swapRange( src, partialExpected, 2, 4 );
      check( "the thread body stays inside the slice it was given",
             Array.prototype.join.call( partial, "," ),
             Array.prototype.join.call( partialExpected, "," ) );
   } )();

   /*
    * The fallback. Threads arrived in PixInsight 1.9.5; on anything older,
    * and in this harness, there is no Thread object at all, and the writer
    * has to stay serial rather than fail. Nothing about the file changes.
    */
   check( "the parallel swap reports a definite answer",
          typeof Psb.canSwapInParallel(), "boolean" );
   if ( !IN_PIXINSIGHT )
      check( "without a Thread object the swap stays serial",
             Psb.canSwapInParallel(), false );
   check( "a threaded swap is only claimed where Thread exists",
          Psb.canSwapInParallel() && typeof Thread == "undefined", false );

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
            "sharpenRGB", "extractRGB", "denoiseLinearRGB",
            "stretchRGB", "denoiseRGB",
            "paletteCombine", "paletteSpcc", "paletteNorm",
            "paletteSharpen", "paletteExtract",
            "paletteDenoiseLinear", "paletteStretch",
            "paletteDenoise",
            "extractL", "denoiseLinearL", "stretchL", "denoiseL" ] );

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
   if ( IN_PIXINSIGHT ) ( function()
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

   /* ---- the Frame Selector measures, and the columns still mean --------- */

   /*
    * Measure a frame and check each column still MEANS what it is read as.
    * Pinning the constants cannot do this -- Frames.COL.psfSNR === 28
    * passes unchanged after the process reorders its table -- and neither
    * can disjoint ranges, which real data rules out (see Frames.PLAUSIBLE).
    * The shape checks in Frames.meaningProblems are what carry it.
    *
    * The frame is GENERATED here, a synthetic star field, so the suite
    * depends on nothing but PixInsight itself.
    */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var fixture = synthFrame( synthDir( "fs-measure" ) + "/sub.xisf",
                                { fwhm: 3.5, background: 0.02, noise: 0.002, seed: 3 } );
      check( "the measurement fixture was generated", File.exists( fixture ), true );
      if ( !File.exists( fixture ) )
         return;

      var measured = FrameSelector.measure( [ fixture ] );
      check( "the channel was not abandoned", measured != null, true );
      if ( measured == null )
         return;
      var m = measured[fixture];
      check( "a frame measures", m != null, true );
      if ( m == null )
         return;

      /*
       * Matched back by PATH, never by position: the returned key is the
       * path that was asked for.
       */
      check( "the result is keyed by the path asked for", m.path, fixture );

      check( "every column still means what it is read as",
             Frames.meaningProblems( m ).join( "; " ), "" );
      check( "FWHM is in a plausible range for an FWHM",
             Frames.metricInRange( "fwhm", m.fwhm ), true );
      check( "eccentricity is a fraction",
             Frames.metricInRange( "eccentricity", m.eccentricity ), true );
      /*
       * Column 8 reads 0 on every frame measured here, which is why PSF SNR
       * is read from 28. If 28 ever went to zero the score's dominant term
       * would be a constant with no symptom, so it is asserted directly.
       */
      check( "PSF SNR is not the zero that column 8 returns",
             m.psfSNR > 0, true );
      check( "and the star count is a positive whole number",
             m.stars > 0 && Math.floor( m.stars ) === m.stars, true );

      /*
       * And prove the check can fail: put the star count where PSF SNR is
       * read from and the meaning check must reject it. Without this the
       * assertions above could pass on tests too loose to discriminate.
       */
      var swapped = { path: m.path, fwhm: m.fwhm, eccentricity: m.eccentricity,
                      psfSNR: m.stars, stars: m.psfSNR };
      check( "a star count in the PSF SNR slot is rejected",
             Frames.meaningProblems( swapped ).length > 0, true );
   } )();

   /*
    * Background and SNR estimate, confirmed on a generated frame whose
    * background is KNOWN (0.02) -- and against a second independent
    * figure, the frame's own median computed by PixInsight. SNR estimate
    * has no independent PJSR figure, so it gets shape checks and negative
    * controls. Honest limit: the optional-column handling inside measure()
    * cannot be made to fail on demand -- SubframeSelector cannot be told to
    * return a bad column -- so it is covered by the node tests of the
    * helpers plus this live run, not by a negative live test.
    */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var fixture = synthFrame( synthDir( "fs-columns" ) + "/sub.xisf",
                                { fwhm: 3.5, background: 0.02, noise: 0.002, seed: 5 } );
      var measured = FrameSelector.measure( [ fixture ] );
      check( "the generated frame measured", measured != null && measured[fixture] != null, true );
      if ( measured == null || measured[fixture] == null )
         return;
      var m = measured[fixture];

      var ws = ImageWindow.open( fixture );
      var own = ws[0].mainView.image.median();
      FrameSelector.closeAll( ws );
      check( "background is a usable number", Frames.optionalOk( m.background ), true );
      check( "background is the one the frame was made with (within 2%): " + m.background,
             Math.abs( m.background - 0.02 ) <= 0.02*0.02, true );
      check( "and matches the image's own median (within 2%): " + m.background + " vs " + own,
             Math.abs( m.background - own ) <= 0.02*Math.abs( own ), true );
      check( "noise (column 12) is not the median",
             Math.abs( m.noise - own ) > 0.02*Math.abs( own ), true );
      check( "SNR estimate is a positive number", m.snrWeight > 0, true );
      check( "SNR estimate is not PSF SNR", m.snrWeight != m.psfSNR, true );
   } )();

   /*
    * convertInPlace, characterised on generated frames: it deletes
    * originals, so what it keeps and what it removes is pinned. Frames are
    * written as FITS into scratch. Two frames that would convert to the
    * same name must be refused with NOTHING removed.
    */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var dir = synthDir( "fs-convert" );
      [ "a.fits", "b.fit", "b.fits" ].forEach( function( name, i )
      {
         synthFrame( dir + "/" + name, { fwhm: 3.5, background: 0.02, noise: 0.002, seed: 20 + i } );
      } );
      check( "the conversion fixtures were generated",
             File.exists( dir + "/a.fits" ) && File.exists( dir + "/b.fit" ) &&
             File.exists( dir + "/b.fits" ), true );

      var clash = FrameSelector.convertInPlace( [ dir + "/b.fit", dir + "/b.fits" ] );
      check( "a name collision is refused", clash.refused != null, true );
      check( "and removes nothing",
             File.exists( dir + "/b.fit" ) && File.exists( dir + "/b.fits" ), true );
      check( "and writes nothing", File.exists( dir + "/b.xisf" ), false );

      /*
       * Converting really converts: the XISF is written beside it and only
       * then is the FITS removed. This failed until 2026-09-23 -- the
       * output routine was set to 2, which is not SubframeSelector's
       * OutputSubframes (1), so nothing was written and nothing converted.
       */
      var one = FrameSelector.convertInPlace( [ dir + "/a.fits" ] );
      check( "a FITS frame converts",
             { converted: one.converted, failed: one.failed, refused: one.refused },
             { converted: 1, failed: 0, refused: null } );
      check( "the XISF is written", File.exists( dir + "/a.xisf" ), true );
      check( "and the FITS original removed", File.exists( dir + "/a.fits" ), false );
      check( "no postfixed copy is left behind", File.exists( dir + "/a_a.xisf" ), false );

      /*
       * Copying out: the approved frames land in the destination under the
       * names outputMapping promises -- no "_a" postfix -- and the sources
       * are untouched.
       */
      var outDir = dir + "-out";
      if ( File.directoryExists( outDir ) )
         FrameSelector.frameFilesIn( outDir ).forEach( function( f ) { File.remove( f ); } );
      var ex = FrameSelector.exportApproved( [ dir + "/b.fits" ], outDir );
      check( "copying out writes the frame",
             { written: ex.written, failed: ex.failed, refused: ex.refused },
             { written: 1, failed: 0, refused: null } );
      check( "under the promised name", File.exists( outDir + "/b.xisf" ), true );
      check( "and leaves the source alone", File.exists( dir + "/b.fits" ), true );
      /*
       * Copying out EMPTIES the output folder first -- everything, hidden
       * files and subfolders too -- and never follows a symbolic link out
       * of it. Filled with a stale frame, a hidden file, a nested folder and
       * a link to a folder outside; afterwards it holds exactly this run's
       * frame, and what the link pointed at is untouched.
       */
      var outside = dir + "-outside";
      if ( !File.directoryExists( outside ) )
         File.createDirectory( outside, true );
      File.writeTextFile( outside + "/sentinel.txt", "must survive" );
      File.writeTextFile( outDir + "/stale.xisf", "old run" );
      File.writeTextFile( outDir + "/.hidden", "hidden" );
      if ( !File.directoryExists( outDir + "/nested/deeper" ) )
         File.createDirectory( outDir + "/nested/deeper", true );
      File.writeTextFile( outDir + "/nested/deeper/x.txt", "x" );
      var ln = new ExternalProcess;
      ln.start( "/bin/ln", [ "-sfn", outside, outDir + "/link-out" ] );
      ln.waitForFinished();
      var again = FrameSelector.exportApproved( [ dir + "/b.fits" ], outDir );
      check( "copying out again writes the frame",
             { written: again.written, failed: again.failed, refused: again.refused },
             { written: 1, failed: 0, refused: null } );
      var left = [], lf = new FileFind;
      if ( lf.begin( outDir + "/*" ) )
         do { if ( lf.name != "." && lf.name != ".." ) left.push( lf.name ); } while ( lf.next() );
      check( "and the folder holds exactly this run's frame", left.sort(), [ "b.xisf" ] );
      check( "nothing behind the link was touched", File.exists( outside + "/sentinel.txt" ), true );

      /*
       * A folder that CONTAINS the frames being copied is never emptied:
       * refused before anything is removed.
       */
      var parent = dir + "-parent", inner = parent + "/src";
      if ( !File.directoryExists( inner ) )
         File.createDirectory( inner, true );
      File.writeTextFile( parent + "/keep.txt", "keep" );
      if ( !File.exists( inner + "/c.fits" ) )
         File.copyFile( inner + "/c.fits", dir + "/b.fits" );
      var bad = FrameSelector.exportApproved( [ inner + "/c.fits" ], parent );
      check( "a folder containing the sources is refused", bad.refused != null, true );
      check( "and nothing in it is removed",
             File.exists( parent + "/keep.txt" ) && File.exists( inner + "/c.fits" ), true );
      check( "an XISF is left alone",
             FrameSelector.convertInPlace( [ dir + "/c.xisf" ] ).alreadyXisf, 1 );
   } )();

   /*
    * The filmstrip on 20 GENERATED frames of one filter, a minute apart:
    * FWHM spread from 3.0 so the relative gate is active, and two wide ones
    * it rejects. Timer ticks are observed, every thumbnail arrives, the
    * crosses are exactly the frames Run leaves out, a click selects, a
    * stale result is discarded. Then five show/close cycles closing
    * MID-LOAD through the window path alone.
    */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var dst = synthDir( "fs-filmstrip" );
      for ( var i = 0; i < 20; ++i )
         synthFrame( dst + "/frame_" + ( i < 10 ? "0" : "" ) + i + ".xisf",
                     { fwhm: ( i == 18 ) ? 6.0 : ( i == 19 ) ? 7.0 : 3.0 + 0.05*i,
                       background: 0.02, noise: 0.002, seed: 100 + i,
                       date: "2026-01-01T02:" + ( i < 10 ? "0" : "" ) + i + ":00" } );
      check( "the filmstrip fixture was generated (20 frames)",
             FrameSelector.frameFilesIn( dst ).length, 20 );

      function waitFor( pred, seconds )
      {
         var until = Date.now() + seconds*1000;
         while ( !pred() && Date.now() < until )
            CoreApplication.processEvents();
         return pred();
      }
      function loaded( d ) { return d.filmstrip.thumbs.filter( function( b ) { return b != null; } ).length; }

      var state = FrameSelector.buildState( dst, null );
      var ch = state.channels[state.order[0]];
      check( "the fixture is one channel of 20", state.order.length == 1 && ch.rows.length == 20, true );

      // nothing loads before the dialog is shown
      var early = new FrameSelector.Dialog( FrameSelector.buildState( dst, null ) );
      check( "the loader is not running before show", early.loaderTimer.isRunning, false );
      early.release(); early.cancel();

      // real gates: the FWHM box shows the automatic limit, greyed
      var dlg = new FrameSelector.Dialog( state );
      dlg.show();
      check( "the FWHM gate on real frames is active", ch.gates.fwhm.active, true );
      check( "the FWHM box shows the automatic limit", dlg.critEdits.fwhm.text,
             Frames.formatLimit( "fwhm", ch.gates.fwhm.limit ) );
      check( "greyed, because it is automatic", dlg.critEdits.fwhm.styleSheet,
             FrameSelector.AUTO_STYLE );

      /*
       * Make the crossed set non-empty BY CONSTRUCTION: condemn the two
       * sharpest frames (which no FWHM threshold rejects) and set the
       * threshold to the median, which rejects every frame above it.
       */
      var byFwhm = ch.rows.map( function( r, k ) { return k; } )
                     .filter( function( k ) { return ch.rows[k].metrics; } )
                     .sort( function( a, b ) { return ch.rows[a].metrics.fwhm - ch.rows[b].metrics.fwhm; } );
      ch.rows[byFwhm[0]].override = Frames.OVERRIDE.CONDEMNED;
      ch.rows[byFwhm[1]].override = Frames.OVERRIDE.CONDEMNED;
      var medFwhm = Frames.median( byFwhm.map( function( k ) { return ch.rows[k].metrics.fwhm; } ) );
      dlg.channel().settings = Frames.withLimit( dlg.channel().settings, "fwhm", medFwhm );
      dlg.refresh();
      var expected = ch.rows.map( function( r ) { return Frames.leftOut( r, true, false ); } );
      check( "at least three frames are crossed", expected.filter( Boolean ).length >= 3, true );
      check( "crossed tiles are exactly the frames Run leaves out", dlg.filmstrip.crossed, expected );
      check( "the counter agrees", dlg.keepLabel.text,
             Frames.keepCount( ch.rows, true, false ).keep + " / 20 keep" );

      check( "every thumbnail arrives", waitFor( function() { return loaded( dlg ) == 20; }, 180 ), true );
      check( "the loader ticked for them", dlg.ticksObserved >= 19, true );
      check( "the dialog is still open for the click checks", dlg.released, false );
      if ( dlg.released )
         return;
      dlg.filmstrip.onPick( 12 );
      check( "clicking a tile selects its row", dlg.selectedRowIndex(), 12 );
      /*
       * Through the MOUSE path, not onPick: page forward, press on a tile
       * by position. The frame clicked is selected and is still the one
       * under the mouse afterwards -- re-centring on every click once slid
       * a different frame under it.
       */
      var fs = dlg.filmstrip;
      fs.page( 1 );
      var firstBefore = fs.first, k = Math.min( 2, fs.visibleCount() - 1 );
      var px = k*fs.tileW() + 4 + Math.round( FrameSelector.THUMB.W/2 );
      fs.onMousePress( px, 40, 1, 1, 0 );
      check( "a click selects the tile under the mouse", dlg.selectedRowIndex(), firstBefore + k );
      check( "and the strip does not move", fs.first, firstBefore );
      check( "so the clicked frame is still under the mouse", fs.indexAt( px ), firstBefore + k );
      // a table click goes through the same path: strip and tags follow
      dlg.frameTree.currentNode = dlg.frameTree.child( 5 );
      dlg.frameTree.child( 5 ).selected = true;
      dlg.frameTree.onNodeSelectionUpdated();
      check( "a table click moves the strip selection", dlg.filmstrip.selected, 5 );
      check( "and the preview's tags", JSON.stringify( dlg.preview.tags ),
             JSON.stringify( dlg.currentTags() ) );

      /*
       * A result for a stale generation is discarded: queue one item, move
       * the generation on, run the stale item by hand.
       */
      var sd = new FrameSelector.Dialog( FrameSelector.buildState( dst, null ) );
      sd.show();
      sd.loaderTimer.stop();                // only the items queued by hand run
      var rows = sd.channel().rows;
      var stalePath = rows[15].path, freshPath = rows[16].path;
      delete sd.thumbs[stalePath]; delete sd.thumbs[freshPath];
      var staleItem = { path: stalePath, key: sd.channel().key, generation: sd.loadGeneration };
      sd.rebuildQueue();
      sd.loaderTimer.stop();
      sd.loadQueue = [ staleItem ];
      sd.loaderTick();
      sd.loaderTimer.stop();
      check( "a stale thumbnail is discarded", sd.thumbs[stalePath] == null, true );
      // and the control: a CURRENT item is stored, so the check above can fail
      sd.loadQueue = [ { path: freshPath, key: sd.channel().key, generation: sd.loadGeneration } ];
      sd.loaderTick();
      sd.loaderTimer.stop();
      check( "a current thumbnail is stored", sd.thumbs[freshPath] != null, true );
      sd.release(); sd.cancel();
      dlg.release(); dlg.cancel();

      /*
       * Close mid-load, five times, through the WINDOW path only: cancel(),
       * no explicit release(). cancel() on a dialog opened with show() does
       * not fire onClose, so it is the loader's own visibility check that
       * must stop the timer -- within a tick or two.
       */
      var midLoad = 0, reached = 0;
      for ( var c = 0; c < 5; ++c )
      {
         var d2 = new FrameSelector.Dialog( FrameSelector.buildState( dst, null ) );
         d2.show();
         if ( waitFor( function() { return loaded( d2 ) >= 3; }, 60 ) )
            ++reached;
         if ( d2.loadQueue.length > 0 )
            ++midLoad;
         d2.cancel();
         check( "closing the window stops loading (" + c + ")",
                waitFor( function() { return !d2.loaderTimer.isRunning; }, 5 ), true );
         var ticks = d2.ticksObserved;
         waitFor( function() { return false; }, 0.5 );
         check( "and nothing more is read (" + c + ")", d2.ticksObserved, ticks );
         d2.release();                       // as the entry point's finally does
      }
      check( "every cycle loaded at least three thumbnails", reached, 5 );
      check( "every cycle closed with loading still pending", midLoad, 5 );
   } )();

   /* ---- digests, which are what authorise a deletion -------------------- */

   if ( IN_PIXINSIGHT ) ( function()
   {
      var tmp = "/tmp/agent-scratch/digest-test.txt";
      File.writeTextFile( tmp, "one" );
      var a = FrameSelector.digest( tmp );
      check( "a digest is produced", typeof a, "string" );
      check( "the same bytes give the same digest",
             FrameSelector.digest( tmp ), a );
      File.writeTextFile( tmp, "two" );
      /*
       * Same path, same length. Only the CONTENT changed -- which is
       * exactly the replacement a path/size/mtime check cannot see.
       */
      check( "different bytes of the same length give a different digest",
             FrameSelector.digest( tmp ) != a, true );
      File.remove( tmp );
      check( "a missing file has no digest",
             FrameSelector.digest( "/tmp/agent-scratch/not-there.xisf" ), null );
   } )();

   if ( IN_PIXINSIGHT ) ( function()
   {
      var dir = "/tmp/agent-scratch/fs-scan-test";
      if ( !File.directoryExists( dir ) )
         File.createDirectory( dir, true );
      var p = dir + "/unstable.txt";
      File.writeTextFile( p, "first" );
      var before = FrameSelector.fileIdentity( p );
      File.writeTextFile( p, "secnd" );        // same length, new content
      var after = FrameSelector.fileIdentity( p );
      /*
       * The identity check the scan relies on must notice a replacement
       * that preserves the length -- which is the only kind that matters,
       * because a size change would be caught anyway.
       */
      check( "a same-length replacement changes identity",
             before.digest != after.digest, true );
      check( "and the size it preserved is still reported as equal",
             before.size, after.size );
      File.remove( p );

      /*
       * The scan must produce a cohort from a real folder. An empty one is
       * not an error: it has no channels and nothing unstable.
       */
      var empty = FrameSelector.scan( dir );
      check( "an empty folder scans to no channels",
             Object.keys( empty.channels ).length, 0 );
      check( "and nothing unstable", empty.unstable.length, 0 );
   } )();

   /*
    * The delete path, against COPIES in scratch and never against real
    * subframes. This is the only irreversible operation in the tool.
    */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var dir = "/tmp/agent-scratch/fs-delete-test";
      if ( !File.directoryExists( dir ) )
         File.createDirectory( dir, true );
      var keep = dir + "/keep.txt", drop = dir + "/drop.txt",
          swapped = dir + "/swapped.txt";
      File.writeTextFile( keep, "keep" );
      File.writeTextFile( drop, "drop" );
      File.writeTextFile( swapped, "before" );

      var man = Frames.buildManifest( [
         { path: drop, state: Frames.STATE.REJECTED, reasons: [ "test" ],
           digest: FrameSelector.digest( drop ),
           size: ( new FileInfo( drop ) ).size, mtime: 0 },
         { path: swapped, state: Frames.STATE.REJECTED, reasons: [ "test" ],
           digest: FrameSelector.digest( swapped ),
           size: ( new FileInfo( swapped ) ).size, mtime: 0 } ] );

      /*
       * Replace one file after the manifest was built, with content of the
       * same length. Path, size and mtime all still match; only the digest
       * does not. It must survive.
       */
      File.writeTextFile( swapped, "after!" );

      var result = FrameSelector.execute( man );
      check( "the condemned file is gone", File.exists( drop ), false );
      check( "the replaced file is NOT deleted", File.exists( swapped ), true );
      check( "and is reported as skipped", result.skipped, 1 );
      check( "the untouched file is untouched", File.exists( keep ), true );
      check( "the run was not stopped by the journal", result.stopped, false );

      /*
       * The record must exist, and must have been written before the unlink
       * rather than after it.
       */
      check( "an audit log was written", result.logPath != null, true );
      if ( result.logPath != null )
      {
         check( "and it is on disk", File.exists( result.logPath ), true );
         var text = File.readTextFile( result.logPath );
         check( "naming the deleted frame", text.indexOf( drop ) >= 0, true );
         check( "and recording the skip", text.indexOf( "skipped" ) >= 0, true );
      }

      /*
       * Resuming must not act twice, and must not recompute anything.
       */
      var again = FrameSelector.execute( man );
      check( "a second execute does nothing new", again.deleted, 0 );
      check( "and the replaced file still survives it",
             File.exists( swapped ), true );

      File.remove( keep ); File.remove( swapped );
   } )();

   /*
    * The preview control, built against the real widget classes. The
    * prototype in Task 1 measured 341 ms from open to a drawn bitmap on a
    * 26 MP sub, well inside the 1.5 s gate, so the dialog is built around
    * a full-frame render rather than a downsampled one.
    */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var ok = true, err = "";
      try
      {
         var dlg = new Dialog;
         var pv = new FrameSelector.PreviewControl( dlg );
         /*
          * Arrow keys must not reach the frame table underneath, so the
          * control takes focus on click and consumes the keys it handles.
          */
         ok = ok && ( pv.focusStyle == FocusStyle.Click );
         ok = ok && ( typeof pv.load == "function" );
         ok = ok && ( typeof pv.dispose == "function" );
         /* Panning with nothing loaded must not throw. */
         pv.pan( 100, 100 );
         pv.dispose();
      }
      catch ( e ) { ok = false; err = String( e ); }
      check( "the preview control builds and pans" + ( err ? ": " + err : "" ),
             ok, true );

      /*
       * "1:1" means one image pixel per PHYSICAL display pixel. The
       * prototype measured physicalPixelRatio as 1 on this display, not the
       * 2 the plan assumed, so the control must read the ratio rather than
       * divide by a constant.
       */
      check( "a bitmap reports a physical pixel ratio",
             ( new Bitmap( 4, 4 ) ).physicalPixelRatio > 0, true );
   } )();

   /*
    * The review dialog, built against the real widget classes. Two dialog
    * breakages reached the user as constructor errors that no pure-function
    * test could see, which is why this is here rather than assumed.
    */
   /*
    * Every entry point includes the headers for the macros it uses.
    *
    * This suite cannot catch a missing header by running the code: it
    * includes FrameSelector.js AFTER its own headers, so a macro this file
    * imports is defined for everything below it. That is exactly how
    * TextAlign_Right reached a release -- 939 assertions passed while the
    * real script died on its first line of layout, because only the suite
    * had included TextAlign.jsh.
    *
    * So the check is made against the SOURCE instead: for each entry
    * point, follow its library includes, collect the macro families used,
    * and require the matching header somewhere in that set. Constants of
    * the form Name_Member are preprocessor macros from pjsr/Name.jsh --
    * unlike the dot forms, which the engine defines itself.
    */
   ( function()
   {
      var ENTRY_POINTS = [ "Loom.js", "FrameSelector.js", "FlyThrough.js" ];
      /*
       * Families this engine provides as objects rather than macros are
       * excluded: KeyCode and DataType have dot forms, and a name like
       * Math_PI or a plain identifier with an underscore is not a macro.
       * The list is deliberately of families that HAVE a pjsr header.
       */
      var FAMILIES = [ "TextAlign", "StdCursor", "FrameStyle", "StdButton",
                       "BrushStyle",
                       "StdIcon", "UndoFlag", "DataType", "CryptographicHash",
                       "Sizer", "FocusStyle", "ImageOp", "ColorSpace",
                       "SampleType", "MaskMode", "NumericControl" ];

      function sourceOf( rel )
      {
         try { return File.readTextFile( LOOM_DIR + "/" + rel ); }
         catch ( e ) { return ""; }
      }

      /* An entry point plus everything it pulls in from lib/. */
      function wholeSource( entry )
      {
         var text = sourceOf( entry );
         var libs = text.match( /#include\s+"lib\/[A-Za-z0-9_]+\.js"/g ) || [];
         for ( var i = 0; i < libs.length; ++i )
         {
            var m = libs[i].match( /lib\/[A-Za-z0-9_]+\.js/ );
            if ( m )
               text += "\n" + sourceOf( m[0] );
         }
         return text;
      }

      for ( var e = 0; e < ENTRY_POINTS.length; ++e )
      {
         var entry = ENTRY_POINTS[e];
         var text = wholeSource( entry );
         check( entry + " was read", text.length > 0, true );

         var missing = [];
         for ( var f = 0; f < FAMILIES.length; ++f )
         {
            var fam = FAMILIES[f];
            /*
             * The macro form only. `TextAlign.Right` and the string
             * "TextAlign" in a comment are not uses of the macro, and a
             * word boundary keeps MaskMode_ from matching XMaskMode_.
             */
            var used = ( new RegExp( "\\b" + fam + "_[A-Za-z0-9_]+" ) ).test( text );
            if ( !used )
               continue;
            var has = ( new RegExp( "#include\\s+<pjsr/" + fam + "\\.jsh>" ) ).test( text );
            if ( !has )
               missing.push( fam );
         }
         check( entry + " includes a header for every macro family it uses",
                missing.join( ", " ), "" );
      }

      /*
       * And the check itself has to be able to fail, or it is decoration:
       * a family that is used with no header must be reported.
       */
      var pretend = 'this.x = TextAlign_Right;\n#include <pjsr/StdIcon.jsh>\n';
      check( "a macro with no header is detected",
             ( /\bTextAlign_[A-Za-z0-9_]+/ ).test( pretend ) &&
             !( /#include\s+<pjsr\/TextAlign\.jsh>/ ).test( pretend ), true );
   } )();

   /*
    * The frame column shows what differs between frames, not what they
    * share. Whole names are far wider than the column, so the table
    * elided them to "Light_...a.xisf" on every row -- identical, and
    * useless for telling one frame from another.
    */
   ( function()
   {
      var base = "/f/Light_IC 1396A_180.0s_Bin1_2600MM_S_gain100_20260918-";
      var got = Frames.shortNames( [ base + "040548_180deg_-7.0C_0030_a.xisf",
                                     base + "040931_180deg_-7.0C_0031_a.xisf",
                                     base + "041314_180deg_-7.0C_0032_a.xisf" ] );
      check( "the shared head is dropped", got[0], "040548_180deg_-7.0C_0030_a.xisf" );
      check( "every row keeps its own tail", got[2],
             "041314_180deg_-7.0C_0032_a.xisf" );
      check( "and the rows differ from one another", got[0] != got[1], true );

      /*
       * The cut lands on a separator, so a name never starts mid-token.
       * With 0009 and 0010 the raw common prefix ends "_00", and cutting
       * there would show "09" and "10" -- true, and unreadable.
       */
      var seq = Frames.shortNames( [ "/f/Light_H_0009_c.xisf",
                                     "/f/Light_H_0010_c.xisf" ] );
      check( "a name starts at a whole token", seq[0], "0009_c.xisf" );
      check( "and so does its neighbour", seq[1], "0010_c.xisf" );

      // One frame has nothing to compare against.
      check( "a single frame keeps its name",
             Frames.shortNames( [ "/f/Light_H_0009_c.xisf" ] )[0],
             "Light_H_0009_c.xisf" );
      check( "an empty channel is empty", Frames.shortNames( [] ).length, 0 );

      /*
       * Names with no shared head are already distinct; shortening them
       * would only remove information.
       */
      var un = Frames.shortNames( [ "/f/alpha.xisf", "/f/beta.xisf" ] );
      check( "unrelated names are left whole", un[0], "alpha.xisf" );

      /*
       * A name that IS the shared prefix must not become an empty cell.
       */
      var same = Frames.shortNames( [ "/f/Light_H_0009.xisf",
                                      "/f/Light_H_0009.xisf.bak" ] );
      check( "no row is left with an empty name",
             same[0].length > 0 && same[1].length > 0, true );
   } )();

   /*
    * A frame's name is shortened from the FRONT.
    *
    * Subframe names share a long prefix -- target, exposure, binning,
    * camera -- and differ only in the timestamp and sequence number at the
    * end. Eliding the tail, or the middle, drops exactly the part that
    * says which frame this is.
    */
   ( function()
   {
      var name = "Light_IC 1396A_180.0s_Bin1_2600MM_S_gain100_" +
                 "20260918-040548_180deg_-7.0C_0030_a";
      var short_ = Util.elideHead( name, 40 );
      check( "the name is cut to the limit", short_.length, 40 );
      check( "the sequence number survives",
             short_.indexOf( "_0030_a" ) >= 0, true );
      check( "and the cut is marked", short_.substring( 0, 3 ), "..." );
      check( "a name that fits is left alone",
             Util.elideHead( "short.xisf", 40 ), "short.xisf" );
      check( "a name exactly at the limit is left alone",
             Util.elideHead( "abcd", 4 ), "abcd" );
      check( "an absent name does not throw", Util.elideHead( null, 20 ), "" );
      check( "a nonsense limit leaves the name whole",
             Util.elideHead( name, 0 ), name );
   } )();

   /*
    * Converting in place is work only where the format differs. A folder
    * already in XISF is a no-op, and saying so costs nothing -- running
    * SubframeSelector over it to find out costs a pass over every file.
    */
   ( function()
   {
      var mixed = [ "/d/a.fit", "/d/b.xisf", "/d/c.fits", "/d/e.XISF" ];
      var todo = Frames.needingXisf( mixed );
      check( "only the non-XISF frames need converting", todo.length, 2 );
      check( "the FITS ones are chosen", todo.join( "," ), "/d/a.fit,/d/c.fits" );
      /*
       * Case does not decide a format. An .XISF frame is already XISF, and
       * "converting" it would rewrite a file for nothing.
       */
      check( "an upper-case extension counts as XISF",
             Frames.needingXisf( [ "/d/e.XISF" ] ).length, 0 );
      check( "a folder already in XISF needs nothing",
             Frames.needingXisf( [ "/d/b.xisf" ] ).length, 0 );
      check( "and no frames at all need nothing",
             Frames.needingXisf( [] ).length, 0 );

      /*
       * Grouped by folder, because each conversion writes into the folder
       * its own sources came from.
       */
      var g = Frames.byDirectory( [ "/d/one/a.fit", "/d/two/b.fit",
                                    "/d/one/c.fit" ] );
      check( "frames are grouped by folder", g.order.length, 2 );
      check( "the first folder keeps both of its frames",
             g.dirs["/d/one"].length, 2 );
      check( "and the folders are in the order first seen",
             g.order.join( "," ), "/d/one,/d/two" );
      /*
       * A folder named after a prototype member must not collide with one:
       * the same bare-object trap as groupByFilter.
       */
      var proto = Frames.byDirectory( [ "/constructor/a.fit" ] );
      check( "a folder named like a prototype member still groups",
             proto.dirs["/constructor"].length, 1 );

      /*
       * Two frames converting to one name would destroy each other's
       * output, so the pair is refused rather than half done.
       */
      var clash = Frames.outputMapping( [ "/d/a.fit", "/d/a.fits" ], "/d",
                                        Frames.XISF );
      check( "a conversion collision is seen before anything is written",
             clash.collisions.length, 1 );
   } )();

   /*
    * Choosing the folder the frames are already in is not a mistake -- it
    * is how you ask for them to be culled where they sit -- but it means
    * the opposite of copying, and must be recognised BEFORE Apply so the
    * dialog can say which of the two it will do.
    */
   ( function()
   {
      var paths = [ "/data/night/a_c.xisf", "/data/night/b_c.xisf" ];
      check( "the input folder is recognised as a source",
             Frames.destinationIsSource( paths, "/data/night" ), true );
      check( "another folder is not",
             Frames.destinationIsSource( paths, "/data/culled" ), false );
      /*
       * A folder BELOW the source is a different folder: files written
       * there cannot overwrite their own originals.
       */
      check( "a subfolder of the source is not the source",
             Frames.destinationIsSource( paths, "/data/night/kept" ), false );
      check( "no destination is not a source",
             Frames.destinationIsSource( paths, null ), false );
      check( "and neither is one with no frames to compare",
             Frames.destinationIsSource( [], "/data/night" ), false );

      /*
       * The same rule outputMapping uses, so the sentence the dialog shows
       * and the refusal the export would raise cannot disagree.
       */
      var map = Frames.outputMapping( paths, "/data/night", ".xisf" );
      check( "outputMapping agrees it is aliased", map.aliased, true );
      check( "and disagrees for a different folder",
             Frames.outputMapping( paths, "/data/culled", ".xisf" ).aliased, false );
   } )();

   /*
    * A click on the plot picks a frame. The inverse of how the points are
    * placed, so the column clicked is the frame meant; only x is
    * considered, or a point near the top of the plot would be harder to
    * hit than one in the middle.
    */
   ( function()
   {
      var L = 52, pw = 400, n = 5;   // points at 52, 152, 252, 352, 452
      check( "the first point is picked at the left edge",
             Frames.pointAt( L, L, pw, n ), 0 );
      check( "the last at the right edge", Frames.pointAt( L + pw, L, pw, n ), 4 );
      check( "and the middle in between",
             Frames.pointAt( L + pw/2, L, pw, n ), 2 );
      check( "a click nearer one point than the next takes that one",
             Frames.pointAt( L + 160, L, pw, n ), 2 );
      check( "and just short of halfway takes the earlier one",
             Frames.pointAt( L + 140, L, pw, n ), 1 );

      check( "a click left of the plot picks nothing",
             Frames.pointAt( L - 40, L, pw, n ), -1 );
      check( "and one past the right picks nothing",
             Frames.pointAt( L + pw + 40, L, pw, n ), -1 );

      /*
       * A lone frame occupies the whole width, so anywhere inside picks
       * it -- the general formula would divide by zero.
       */
      check( "a single frame is picked anywhere",
             Frames.pointAt( L + 200, L, pw, 1 ), 0 );
      check( "an empty channel picks nothing",
             Frames.pointAt( L + 200, L, pw, 0 ), -1 );
      check( "and a plot with no width picks nothing",
             Frames.pointAt( L, L, 0, 5 ), -1 );
   } )();

   /*
    * Two of the plot's numbers must never land on each other.
    *
    * The axis is labelled at both extremes and the band at its limits, so
    * a limit close to an extreme drew "0.768" and "0.729" in the same few
    * pixels. Order is priority: a threshold outranks the extreme it sits
    * near, because the extreme is only where the data stops while the
    * threshold is the decision being made.
    */
   ( function()
   {
      var gap = 13;
      var kept = Frames.spacedLabels(
         [ { y: 120, text: "0.768", edge: true },   // the band limit
           { y: 10,  text: "0.900" },               // top of the axis
           { y: 126, text: "0.729" } ], gap );      // bottom, too close
      check( "the colliding label is dropped", kept.length, 2 );
      check( "and the threshold is the one kept", kept[0].text, "0.768" );
      check( "the far label survives", kept[1].text, "0.900" );

      // Nothing overlapping: everything is drawn.
      check( "well separated labels are all kept",
             Frames.spacedLabels( [ { y: 10 }, { y: 60 }, { y: 120 } ], gap ).length, 3 );

      // Exactly the gap apart is far enough; one pixel less is not.
      check( "a label exactly a gap away is kept",
             Frames.spacedLabels( [ { y: 10 }, { y: 10 + gap } ], gap ).length, 2 );
      check( "and one pixel closer is not",
             Frames.spacedLabels( [ { y: 10 }, { y: 9 + gap } ], gap ).length, 1 );

      check( "nothing to place is not an error",
             Frames.spacedLabels( [], gap ).length, 0 );
   } )();

   /*
    * PSF SNR ranks frames but does not delete them.
    *
    * Integration weights each frame by its signal -- WBPP's default is PSF
    * Signal Weight -- so a faint frame already contributes in proportion
    * to what it is worth, and the stack's SNR goes as the root of the sum
    * of squared frame SNRs. Every frame with signal raises that, so
    * deleting a faint one costs signal for nothing. FWHM, eccentricity and
    * star count are gated because no weighting repairs them.
    */
   ( function()
   {
      check( "PSF SNR does not reject by default",
             Frames.DEFAULT_GATING.psfSNR, false );
      check( "FWHM does", Frames.DEFAULT_GATING.fwhm, true );
      check( "eccentricity does", Frames.DEFAULT_GATING.eccentricity, true );
      check( "star count does", Frames.DEFAULT_GATING.stars, true );
      check( "but PSF SNR still carries the most ranking weight",
             Frames.DEFAULT_WEIGHTS.psfSNR > Frames.DEFAULT_WEIGHTS.fwhm, true );

      /*
       * An ungated metric produces an INACTIVE gate rather than being
       * skipped somewhere downstream, so everything that reads a gate --
       * the verdict, the reasons and the plot's band -- follows from one
       * decision.
       */
      var frames = [];
      for ( var i = 0; i < 12; ++i )
         frames.push( { psfSNR: 13 + (i%3)*0.2, fwhm: 3.8 + (i%4)*0.05,
                        eccentricity: 0.6 + (i%3)*0.01, stars: 8900 + i*10 } );
      frames.push( { psfSNR: 2.0, fwhm: 3.8, eccentricity: 0.6, stars: 8900 } );

      var gates = Frames.relativeGates( frames, 2.5, Frames.DEFAULT_GATING );
      check( "the ungated metric has no active gate", gates.psfSNR.active, false );
      check( "and says why", gates.psfSNR.reason, "not used as a gate" );
      check( "a gated metric still has one", gates.fwhm.active, true );

      var settings = Frames.defaultSettings();
      var v = Frames.verdict( frames[frames.length-1], gates, settings );
      check( "a frame that is only faint is kept", v.state, Frames.STATE.APPROVED );
      check( "and nothing is marked against it", v.failing.length, 0 );

      // The plot draws no band for a metric that cannot reject.
      var band = Frames.acceptedBand( "psfSNR", gates, settings );
      check( "an ungated metric has no band", band.lo + "/" + band.hi, "null/null" );

      // Turned on, it gates like any other.
      var on = Frames.relativeGates( frames, 2.5,
                 { psfSNR: true, fwhm: true, eccentricity: true, stars: true } );
      check( "switching it on gates on it", on.psfSNR.active, true );
      check( "and the faint frame is then rejected",
             Frames.verdict( frames[frames.length-1], on, settings ).state,
             Frames.STATE.REJECTED );
   } )();

   /*
    * A preset belongs to a channel, not to the folder. A night's L and its
    * Ha are different populations: one preset over both either spares the
    * ragged channel or cuts into the clean one.
    */
   ( function()
   {
      var a = Frames.defaultSettings(), b = Frames.defaultSettings();
      check( "a channel starts on the default preset", a.preset,
             Frames.DEFAULT_PRESET );

      var strict = Frames.applyPreset( a, "strict" );
      check( "a preset is recorded on the channel", strict.preset, "strict" );
      check( "and sets its k", strict.k, Frames.PRESETS.strict );
      check( "the other channel is untouched", b.preset, Frames.DEFAULT_PRESET );
      check( "and keeps its own k", b.k, Frames.PRESETS[Frames.DEFAULT_PRESET] );

      /*
       * Settings are copied, never shared. A preset that handed back the
       * same objects gave every channel one set of knobs; that has
       * happened here before.
       */
      strict.gating.fwhm = false;
      check( "gating is not shared with the settings it came from",
             a.gating.fwhm, true );

      // An edited k stands against a preset.
      var pinned = Frames.defaultSettings();
      pinned.kEdited = true;
      pinned.k = 1.75;
      var after = Frames.applyPreset( pinned, "lenient" );
      check( "a k set by hand survives a preset", after.k, 1.75 );
      check( "though the preset is still recorded", after.preset, "lenient" );
   } )();

   /*
    * The plot's band is the range that keeps a frame. A relative gate cuts
    * from one side only -- there is no such thing as too FEW stars and too
    * many at once -- so the other end is unbounded and must stay null
    * rather than becoming a number the plot would draw a line at.
    */
   ( function()
   {
      var gates = { fwhm:   { active: true, median: 3.83, limit: 4.01 },
                    stars:  { active: true, median: 8900, limit: 7000 },
                    psfSNR: { active: false, median: 13.4, limit: 12.0 } };

      var rel = { limits: {} };
      var f = Frames.acceptedBand( "fwhm", gates, rel );
      check( "FWHM is capped above", f.hi, 4.01 );
      check( "and open below", f.lo, null );

      var st = Frames.acceptedBand( "stars", gates, rel );
      check( "stars are floored below", st.lo, 7000 );
      check( "and open above", st.hi, null );

      check( "an inactive gate bounds nothing",
             Frames.acceptedBand( "psfSNR", gates, rel ).hi, null );

      var a = Frames.acceptedBand( "fwhm", gates, { limits: { fwhm: { lo: 2, hi: 5 } } } );
      check( "a typed limit replaces the gate's band", a.lo + "," + a.hi, "2,5" );
      check( "other metrics keep the gate's band",
             Frames.acceptedBand( "stars", gates, { limits: { fwhm: { hi: 5 } } } ).lo, 7000 );

      /*
       * Vertical extent. The band's edge is included even when no frame
       * comes near it: a gate nothing approaches is worth seeing as that.
       */
      var b = Frames.plotBounds( [ 3.8, 3.9, 4.06 ], { lo: null, hi: 4.01 } );
      check( "the bounds contain the data", b.lo < 3.8 && b.hi > 4.06, true );
      var far = Frames.plotBounds( [ 3.8, 3.9 ], { lo: null, hi: 9 } );
      check( "and contain a distant band edge", far.hi >= 9, true );
      check( "an open end adds nothing",
             Frames.plotBounds( [ 1, 2 ], { lo: null, hi: null } ).hi > 2, true );

      // A channel whose frames all measure the same still needs a height.
      var flat = Frames.plotBounds( [ 4, 4, 4 ], null );
      check( "a flat channel still has an extent", flat.hi > flat.lo, true );
      check( "no data at all is still a drawable range",
             Frames.plotBounds( [], null ).hi, 1 );
      check( "and so is a channel of nulls",
             Frames.plotBounds( [ null, null ], null ).hi, 1 );
   } )();

   /*
    * A verdict says WHICH measurement condemned the frame, not only why in
    * words. The review colours that column red, and picking the column by
    * reading the sentence back would tie the display to the wording.
    */
   ( function()
   {
      var gates = {
         psfSNR:       { active: true, median: 13.4, limit: 12.0 },
         fwhm:         { active: true, median: 3.83, limit: 4.01 },
         eccentricity: { active: true, median: 0.60, limit: 0.75 },
         stars:        { active: true, median: 8900, limit: 7000 } };
      var settings = { limits: {} };

      // The frame from the screenshot: only FWHM is over its limit.
      var wide = Frames.verdict( { psfSNR: 13.36, fwhm: 4.06,
                                   eccentricity: 0.590, stars: 8206 },
                                 gates, settings );
      check( "a frame over the FWHM limit is rejected", wide.state,
             Frames.STATE.REJECTED );
      check( "and names FWHM as the cause", wide.failing.join( "," ), "fwhm" );

      var good = Frames.verdict( { psfSNR: 13.64, fwhm: 3.80,
                                   eccentricity: 0.593, stars: 9034 },
                                 gates, settings );
      check( "an approved frame has nothing to mark", good.failing.length, 0 );

      // Two bad metrics, both named, in metric order.
      var bad = Frames.verdict( { psfSNR: 10.0, fwhm: 4.50,
                                  eccentricity: 0.60, stars: 8900 },
                                gates, settings );
      check( "every failing measurement is named", bad.failing.join( "," ),
             "psfSNR,fwhm" );

      /*
       * A metric is named once however many of its bounds it fails. It
       * is one column, so it must be coloured once.
       */
      var dup = Frames.verdict( { psfSNR: 13.4, fwhm: 4.50,
                                  eccentricity: 0.60, stars: 8900 },
                                gates, { limits: { fwhm: { hi: 4.2 } } } );
      check( "a typed limit replaces the gate: one reason", dup.reasons.length, 1 );
      check( "and names the metric once", dup.failing.join( "," ), "fwhm" );

      /*
       * The column map is what turns those names into cells. Wrong indices
       * would colour the wrong measurement, which is worse than none.
       */
      check( "PSF SNR is column 1", Frames.metricColumn( "psfSNR" ), 1 );
      check( "SNR is column 2", Frames.metricColumn( "snrWeight" ), 2 );
      check( "FWHM is column 3", Frames.metricColumn( "fwhm" ), 3 );
      check( "eccentricity is column 4", Frames.metricColumn( "eccentricity" ), 4 );
      check( "stars is column 5", Frames.metricColumn( "stars" ), 5 );
      check( "a metric with no column says so",
             Frames.metricColumn( "noise" ), null );
      /*
       * Headings and columns come from one list, so a metric added to
       * DISPLAY_METRICS cannot land in the table without a heading or push
       * the score column out from under the code that writes it.
       */
      check( "the headings cover every shown metric plus name and score",
             Frames.FRAME_COLUMNS.length, Frames.DISPLAY_METRICS.length + 2 );
      check( "FWHM's heading sits in FWHM's column",
             Frames.FRAME_COLUMNS[Frames.metricColumn( "fwhm" )], "FWHM" );
      check( "the score is the last column",
             Frames.SCORE_COLUMN, Frames.FRAME_COLUMNS.length - 1 );
      /*
       * There is no verdict column: the cross and the reddened measurement
       * say it, and the wording is the row's tooltip.
       */
      check( "no verdict column is claimed",
             typeof Frames.VERDICT_COLUMN, "undefined" );
   } )();

   /*
    * Reading a folder reports progress per file.
    *
    * The scan is silent for minutes on a real folder -- every frame is
    * digested whole before any measuring starts -- so the count has to
    * advance per file, and it has to advance BEFORE the file is read: a
    * label naming the file already read is a label naming the wrong one.
    * These paths do not exist, which is the point: an unreadable file must
    * still advance the count rather than stalling it.
    *
    * PixInsight only: FrameSelector.js is an entry point, not a library,
    * so the node harness never loads it.
    */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var seen = [];
      var paths = [ "/nope/a_c.xisf", "/nope/b_c.xisf", "/nope/c_c.xisf" ];
      var cohort = FrameSelector.cohortFrom( paths, function( done, total, name )
      {
         seen.push( done + "/" + total + " " + name );
         return true;
      } );
      check( "every file is reported, readable or not", seen.length, 3 );
      check( "the count starts at one, not zero", seen[0], "1/3 a_c" );
      check( "and ends at the total", seen[2], "3/3 c_c" );
      check( "a scan that is not stopped is not cancelled", cohort.cancelled, false );

      /*
       * Cancel stops the read where it is. Without this the button would
       * be decoration and the remaining gigabytes would still be read.
       */
      var count = 0;
      var stopped = FrameSelector.cohortFrom( paths, function()
      {
         ++count;
         return false;
      } );
      check( "returning false stops the read", count, 1 );
      check( "and the cohort says it was cancelled", stopped.cancelled, true );

      // No callback at all is the headless case, and must still work.
      var plain = FrameSelector.cohortFrom( paths );
      check( "a scan with no progress callback still runs", plain.cancelled, false );

      /*
       * A cancelled read must not be presented as an empty folder: the
       * caller opens a "no readable frames" box on an empty result, and
       * saying that about a scan the user stopped is a lie.
       */
      check( "the message names the phase and the count",
             Util.scanProgressMessage( "Reading", "a_c", 2, 7 ),
             "Reading: a_c (2 of 7)" );
   } )();

   if ( IN_PIXINSIGHT ) ( function()
   {
      /*
       * Space toggles an override, so the constant has to exist. Probed
       * rather than assumed: this engine has undefined constants where the
       * documentation implies otherwise.
       */
      check( "KeyCode.Space is defined", typeof KeyCode.Space, "number" );

      var ok = true, err = "";
      try
      {
         var state = { folder: "/tmp/agent-scratch", channels: {}, order: [],
                       preset: Frames.DEFAULT_PRESET, locked: false };
         var dlg = new FrameSelector.Dialog( state );
         /*
          * The review is editable until an execution is committed, and
          * locked while one is running -- otherwise editing a knob mid-run
          * would alter a manifest that is already deleting files.
          */
         ok = ok && ( typeof dlg.refresh == "function" );
         ok = ok && ( typeof dlg.commit == "function" );
         dlg.cancel();

         /*
          * The scan window is built before the review dialog exists, so a
          * constructor error in it breaks the tool before anything is on
          * screen -- the exact failure this block was added for.
          */
         var sw = new FrameSelector.ScanWindow;
         ok = ok && ( typeof sw.report == "function" );
         var cb = sw.callbacks();
         ok = ok && ( typeof cb.reading == "function" );
         ok = ok && ( typeof cb.measuring == "function" );
         // Reporting drives the label and repaints the bar; it must not throw.
         ok = ok && ( cb.reading( 1, 4, "frame_c" ) === true );
         ok = ok && ( cb.measuring( 1, 2, "H" ) === true );
         sw.cancelled = true;
         ok = ok && ( cb.reading( 2, 4, "frame_c" ) === false );

         /*
          * The plot is drawn, so a painting error is a constructor-class
          * failure: it reaches the user as a blank dialog, not an exception.
          */
         /*
          * Copying the approved frames out was implemented and reachable
          * from nothing, leaving deleting in place as the only action the
          * dialog offered -- the one that cannot be undone.
          */
         ok = ok && ( typeof dlg.chooseDestination == "function" );
         ok = ok && ( typeof dlg.commitCopy == "function" );
         ok = ok && ( typeof dlg.approvedPaths == "function" );
         ok = ok && ( typeof FrameSelector.convertInPlace == "function" );
         // Nothing to convert must not run a measurement pass.
         var none = FrameSelector.convertInPlace( [] );
         ok = ok && ( none.converted == 0 && none.refused == null );
         /*
          * Pointing the destination somewhere makes it a copy; there is no
          * mode to set. Whether a destination IS the source folder is
          * decided by Frames.destinationIsSource against the frames' own
          * paths, and is tested there -- this dialog has no frames, so
          * there is nothing here for that test to compare against.
          */
         dlg.state.destination = "/tmp/agent-scratch/elsewhere";
         ok = ok && ( dlg.copyingOut() === true );
         dlg.state.destination = null;
         ok = ok && ( dlg.copyingOut() === false );

         /*
          * A dialog with FRAMES IN IT, refreshed and clicked.
          *
          * The empty dialog above proves the constructor runs and nothing
          * else. Two exceptions reached the user through code it never
          * touches -- a stale variable in refresh's summary, and an
          * assignment to TreeBox.selectedNodes, which is read-only. Both
          * threw from inside a Qt event handler, and an exception crossing
          * back into Qt terminates PixInsight rather than being caught.
          * Closing the review killed the application.
          */
         var pEntries = [], pMetrics = {};
         for ( var pf = 0; pf < 6; ++pf )
         {
            var pp = "/nowhere/frame_" + pf + "_c.xisf";
            pEntries.push( { path: pp, identity: { digest: "d" + pf, size: 1, mtime: 1 } } );
            pMetrics[pp] = { psfSNR: 13 + (pf%3), fwhm: 3.8 + (pf%5)*0.3,
                             eccentricity: 0.6, stars: 8900 - pf*40 };
         }
         var pState = FrameSelector.emptyState( "/nowhere" );
         pState.channels.H = FrameSelector.recompute(
            FrameSelector.newChannel( "H", pEntries, pMetrics,
                                      [ "calibration state unknown for every frame" ] ) );
         pState.order.push( "H" );

         var full = new FrameSelector.Dialog( pState );
         full.refresh();                       // the summary, the label, the plot
         /*
          * The criteria panel: every box editable; untouched boxes are
          * automatic and greyed; a typed number is that metric's limit and
          * clearing it goes back to automatic. The counter says what Run
          * keeps. Six frames is below MIN_FRAMES, so there are no gates --
          * the 20-frame check with real gates is in the filmstrip block.
          */
         var chH = pState.channels.H;
         var crit = [];
         crit.push( full.criteriaGroup.title == "Approval criteria" );
         crit.push( full.critEdits.fwhm.readOnly === false );
         crit.push( full.critEdits.fwhm.styleSheet == FrameSelector.AUTO_STYLE );
         crit.push( full.keepLabel.text ==
                    Frames.keepCount( chH.rows, true, false ).keep + " / 6 keep" );
         // a clean field survives focus loss and Run: no silent rounding
         full.critEdits.fwhm.onEditCompleted();
         full.commitPendingEdits();
         crit.push( Frames.limitValue( chH.settings, "fwhm" ) === null );
         // an invalid entry reverts
         full.critEdits.fwhm.text = "abc";
         full.critEdits.fwhm.modified = true;
         full.critEdits.fwhm.onEditCompleted();
         crit.push( Frames.limitValue( chH.settings, "fwhm" ) === null );
         crit.push( full.critEdits.fwhm.text == "" );
         // two dirty fields both survive a Run-time commit, and turn plain
         full.critEdits.fwhm.text = "6.6";   full.critEdits.fwhm.modified = true;
         full.critEdits.stars.text = "9000"; full.critEdits.stars.modified = true;
         full.commitPendingEdits();
         crit.push( Frames.limitValue( chH.settings, "fwhm" ) === 6.6 );
         crit.push( Frames.limitValue( chH.settings, "stars" ) === 9000 );
         crit.push( full.critEdits.fwhm.styleSheet == "" );
         // clearing a box goes back to automatic
         full.critEdits.fwhm.text = ""; full.critEdits.fwhm.modified = true;
         full.critEdits.stars.text = ""; full.critEdits.stars.modified = true;
         full.commitPendingEdits();
         crit.push( Frames.limitValue( chH.settings, "fwhm" ) === null );
         crit.push( full.critEdits.fwhm.styleSheet == FrameSelector.AUTO_STYLE );
         // the tile number follows its own chooser
         var snrAt = Frames.DISPLAY_METRICS.indexOf( "snrWeight" );
         full.stripMetric.currentItem = snrAt;
         full.stripMetric.onItemSelected( snrAt );
         crit.push( full.filmstrip.metric == "snrWeight" );
         check( "the criteria panel behaves", crit, crit.map( function() { return true; } ) );

         /*
          * Separate tags, top-right, in viewport coordinates, in every
          * mode -- and actually PAINTED: rendered into a bitmap the size of
          * the viewport and read back at each tag's fill.
          */
         full.preview.setTags( [ { text: "REJECTED", kind: "reject" },
                                 { text: "CLOUD", kind: "flag" },
                                 { text: "FOCUS", kind: "flag" } ] );
         var tr = full.preview.tagRects(), tg = [];
         tg.push( tr.length == 3 );
         tg.push( tr[0].y1 <= tr[1].y0 && tr[1].y1 <= tr[2].y0 );     // stacked, apart
         tg.push( full.preview.tagRects( 600 )[0].x1 == 590 );        // right margin
         full.preview.setFit( false );
         tg.push( full.preview.tagRects()[0].x1 == tr[0].x1 );        // not moved by 1:1
         // The dialog is never shown here, so its viewport has no real
         // width; paint into a bitmap of a known one.
         var tb = new Bitmap( 600, 400 );
         tb.fill( 0xff000000 );
         var gfx = new Graphics( tb );
         try { full.preview.paintTags( gfx, 600 ); } finally { gfx.end(); }
         tr = full.preview.tagRects( 600 );
         // just inside the left edge: the fill, clear of the centred text
         function fillAt( r ) { return tb.pixel( r.x0 + 4, Math.round( ( r.y0 + r.y1 )/2 ) ); }
         tg.push( fillAt( tr[0] ) == FrameSelector.TAG_COLOURS.reject.fill );
         tg.push( fillAt( tr[1] ) == FrameSelector.TAG_COLOURS.flag.fill );
         tg.push( fillAt( tr[2] ) == FrameSelector.TAG_COLOURS.flag.fill );
         check( "the preview's tags are separate, placed and painted", tg,
                tg.map( function() { return true; } ) );
         full.preview.setTags( [] );

         /*
          * What the plot DRAWS, not only that it runs: the fixture channel's
          * FWHM painted into a bitmap, and colours read at known places --
          * the band's fill, paper outside it, the rejected frame's cross,
          * the selected frame's ring. Antialiased, so compared within a
          * tolerance. Geometry as Plot.paintOn lays it out.
          */
         var pch = Frames.recompute( fsFixtureChannel() );
         var pband = Frames.acceptedBand( "fwhm", pch.gates, pch.settings );
         // A plot of its own: the dialog's is checked by what follows.
         var tplot = new FrameSelector.Plot( full );
         tplot.setSeries( pch.rows, "fwhm", pband );
         tplot.selected = 5;
         var PW = 600, PH = 200, pbmp = new Bitmap( PW, PH );
         pbmp.fill( 0xff000000 );
         var pg = new Graphics( pbmp );
         try { tplot.paintOn( pg, PW, PH ); } finally { pg.end(); }
         tplot.release();
         var L = 52, T = 10, pw = PW - 52 - 8, ph = PH - 10 - 18;
         var vals = pch.rows.map( function( r ) { return r.metrics.fwhm; } );
         var pb = Frames.plotBounds( vals, pband ), pspan = pb.hi - pb.lo;
         function yOf( v ) { return Math.round( T + ph - ( ( v - pb.lo )/pspan )*ph ); }
         function xOf( i ) { return Math.round( L + ( i/( vals.length - 1 ) )*pw ); }
         function dist( c, want )
         {
            var d = 0;
            for ( var sh = 0; sh < 24; sh += 8 )
               d = Math.max( d, Math.abs( ( ( c >>> sh ) & 255 ) - ( ( want >>> sh ) & 255 ) ) );
            return d;
         }
         function near( c, want ) { return dist( c, want ) <= 70; }
         /*
          * Band grey and paper white are only 25 apart, inside any useful
          * tolerance, so those two are told apart by which is CLOSER.
          */
         function closer( c, a, b ) { return dist( c, a ) < dist( c, b ); }
         // Antialiased strokes land within a pixel of where they are aimed.
         function nearAround( x, y, want )
         {
            for ( var dy = -1; dy <= 1; ++dy )
               for ( var dx = -1; dx <= 1; ++dx )
                  if ( near( pbmp.pixel( x + dx, y + dy ), want ) )
                     return true;
            return false;
         }
         var PC = FrameSelector.PLOT_COLOURS, qx = Math.round( L + 0.25*pw ), pl = [];
         pl.push( closer( pbmp.pixel( qx, yOf( 5.5 ) ), PC.BAND, PC.PAPER ) );  // inside the band
         pl.push( closer( pbmp.pixel( qx, yOf( 6.9 ) ), PC.PAPER, PC.BAND ) );  // above it
         pl.push( nearAround( xOf( 19 ), yOf( 7.0 ), PC.REJECT ) );   // 7.0 is rejected
         pl.push( nearAround( xOf( 5 ), yOf( vals[5] ) + FrameSelector.PICK_RADIUS,
                              PC.PICKED ) );                         // the ring's bottom
         pl.push( !nearAround( xOf( 2 ), yOf( vals[2] ) + FrameSelector.PICK_RADIUS,
                               PC.PICKED ) );                        // and no ring elsewhere
         check( "the plot draws its band, paper, cross and ring", pl,
                pl.map( function() { return true; } ) );
         ok = ok && ( full.frameTree.numberOfChildren == 6 );
         /*
          * The review opens on a frame, not on an empty pane -- so the
          * constructor itself drives selectRow, the preview and the ring.
          */
         ok = ok && ( full.plot.selected == 0 );
         full.selectRow( 0 );                  // the table, the preview, the ring
         full.plot.onPick( 2 );                // as a click on the plot arrives
         /*
          * And with the destination pointing at the frames' own folder,
          * which is the branch that names a count and was where the stale
          * variable lived.
          */
         pState.destination = "/nowhere";
         full.refresh();
         full.release();
         full.cancel();

         /*
          * The preview is a ScrollBox, and the wheel is handled here
          * because a trackpad's pixel deltas are far below the notch the
          * box expects. The event carries one delta and no orientation, so
          * a sideways swipe arrives with nothing to act on.
          */
         var pv = new FrameSelector.PreviewControl( dlg );
         ok = ok && ( typeof pv.viewport == "object" );
         ok = ok && ( typeof pv.maxHorizontalScrollPosition == "number" );
         // No wheel handler, by decision: it can only ever do one axis.
         ok = ok && ( pv.viewport.onMouseWheel == null );
         pv.setFit( true );
         ok = ok && ( pv.maxHorizontalScrollPosition == 0 );

         var plot = new FrameSelector.Plot( dlg );
         plot.setSeries( [ { metrics: { psfSNR: 13, fwhm: 3.8, eccentricity: 0.6,
                                   stars: 9000 },
                        state: Frames.STATE.APPROVED, override: null },
                      { metrics: { psfSNR: 12, fwhm: 4.4, eccentricity: 0.7,
                                   stars: 8000 },
                        state: Frames.STATE.REJECTED, override: null } ],
                    "fwhm", { lo: null, hi: 4.01 } );
         ok = ok && ( typeof plot.paint == "function" );
         // Painting with nothing to show must not throw either.
         plot.setSeries( [], "fwhm", { lo: null, hi: null } );
         ok = ok && ( typeof FrameSelector.rejectIcon == "function" );
         /*
          * show() must remain Control's. Shadowing it with a data-setting
          * method broke the whole dialog, which is the kind of failure a
          * plot control has no business causing.
          */
         ok = ok && ( plot.setSeries !== plot.show );
         /*
          * Picking a point selects a row, and a ring follows the table's
          * selection -- both directions of the same link.
          */
         ok = ok && ( typeof dlg.selectRow == "function" );
         ok = ok && ( typeof dlg.plot.onPick == "function" );
         plot.setSelected( 1 );
         ok = ok && ( plot.selected == 1 );
      }
      catch ( e ) { ok = false; err = String( e ); }
      check( "the frame selector dialog builds" + ( err ? ": " + err : "" ),
             ok, true );

      /*
       * A dialog with real channels is the case that actually exercises the
       * tree population and the verdict recompute; an empty one builds even
       * when every one of those paths is broken.
       */
      var ok2 = true, err2 = "";
      try
      {
         var st = FrameSelector.emptyState( "/tmp/agent-scratch" );
         st.channels.H = FrameSelector.newChannel( "H",
            [ { path: "/m/h1.xisf", filter: "H", exposure: 180, binning: 1,
                width: 10, height: 10, calibrated: "yes",
                identity: { digest: "d1", size: 1, mtime: 0 } },
              { path: "/m/h2.xisf", filter: "H", exposure: 180, binning: 1,
                width: 10, height: 10, calibrated: "yes",
                identity: { digest: "d2", size: 1, mtime: 0 } } ],
            { "/m/h1.xisf": { psfSNR: 1000, fwhm: 5, eccentricity: 0.4, stars: 10000 },
              "/m/h2.xisf": { psfSNR: 1010, fwhm: 5.1, eccentricity: 0.41, stars: 10100 } },
            [] );
         st.order = [ "H" ];
         var d2 = new FrameSelector.Dialog( st );
         d2.refresh();
         check( "a channel below the minimum rejects nothing",
                Frames.counts( st.channels.H.rows ).rejected, 0 );
         /*
          * An override outranks the formula even here, where the formula
          * rejected nothing at all.
          */
         st.channels.H.rows[0].override = Frames.OVERRIDE.CONDEMNED;
         check( "a condemned frame counts as rejected",
                Frames.counts( st.channels.H.rows ).rejected, 1 );
         check( "and the manifest carries exactly it",
                Frames.buildManifest( st.channels.H.rows ).entries.length, 1 );
         /*
          * What Apply would act on, asked for without putting the modal
          * confirmation on screen. "Act on this channel" is only meaningful
          * if unchecking it removes the channel's rows from the set that
          * reaches buildManifest at all, so that is what is asserted.
          */
         check( "the committable set is the enabled channel's rows",
                d2.committableRows().rows.length, 2 );
         st.channels.H.settings.enabled = false;
         check( "and a disabled channel contributes nothing",
                d2.committableRows().rows.length, 0 );
         st.channels.H.settings.enabled = true;
         d2.cancel();
      }
      catch ( e ) { ok2 = false; err2 = String( e ); }
      check( "a populated dialog builds and refreshes" + ( err2 ? ": " + err2 : "" ),
             ok2, true );
   } )();

   /* ---- the dialogs must actually construct ---------------------------- */

   /*
    * PixInsight only, and deliberately so. The whole value of these two
    * assertions is that the dialogs are built against the REAL widget
    * classes -- both breakages they exist to catch were constructor
    * errors that no amount of pure-function testing could see.
    */
   if ( IN_PIXINSIGHT ) ( function()
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

      /*
       * Run must be dead while a scan measures, and alive again afterwards
       * even if the scan threw -- a dialog left disabled can only be
       * escaped by cancelling out of it.
       */
      var busyOK = true, busyErr = "";
      var runDuring = null, runAfter = null, statusDuring = "";
      try
      {
         var s = new UI.SelectDialog( cfg() );
         try
         {
            s.setBusy( Util.scanProgressMessage( "Measuring masters", "G", 1, 3 ) );
            runDuring = s.runButton.enabled;
            statusDuring = s.status.text;
            throw new Error( "scan failed" );
         }
         catch ( eScan ) {}
         finally { s.setBusy( null ); }
         runAfter = s.runButton.enabled;
         busyOK = ( s.addMastersButton.enabled === true &&
                    s.clearButton.enabled === true );
         /*
          * With something in the list, clearing the busy state must bring
          * Run back -- the two rules have to compose, not cancel.
          */
         s.entries = [ { source: "file", ref: "/m/H.xisf", channel: "H" } ];
         s.updateRunEnabled();
         runAfterWithEntries = s.runButton.enabled;
      }
      catch ( e3 ) { busyOK = false; busyErr = String( e3 ); }
      check( "the busy state sets and clears without throwing" +
             ( busyErr ? ": " + busyErr : "" ), busyOK, true );
      check( "Run is dead while masters are being measured", runDuring, false );
      /*
    * NOT alive again by itself: the list is empty here, and Run is dead
    * whenever there is nothing to run. A scan that throws leaves the
    * dialog usable -- the add buttons come back -- but Run follows the
    * list, not the busy flag.
    */
   check( "Run stays dead after a failed scan that added nothing",
          runAfter, false );
   check( "and comes back once there is something to run",
          runAfterWithEntries, true );
      check( "and the progress line is on screen meanwhile",
             statusDuring.indexOf( "1 of 3" ) >= 0, true );

      var cwOK = true, cwErr = "";
      try
      {
         var cw = new UI.CancelWindow;
         cw.setStage( "test" );
         cw.setProgress( 50, "test" );
         cwOK = ( cw.cancelled === false );
         /*
          * The Cancel button must NOT be the dialog's default. It is the
          * only button here, and Qt promotes a lone button to default --
          * which makes Return, or Space while it has focus, throw away a
          * run. That happened to a real fifteen-minute run.
          */
         cwOK = cwOK && ( cw.cancelButton.defaultButton === false );
         cw.cancel();
      }
      catch ( e2 ) { cwOK = false; cwErr = String( e2 ); }
      check( "the Cancel window constructs, drives, and is not keyboard-default" +
             ( cwErr ? ": " + cwErr : "" ), cwOK, true );
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

   // ---- minimum PixInsight core version -----------------------------------

   var MIN = Util.MIN_CORE;

   // The version Loom actually requires, spelled out so a careless edit of
   // Util.MIN_CORE has to be deliberate.
   check( "required core is 1.9.4", Util.formatCoreVersion( MIN ), "1.9.4" );

   check( "exact minimum passes",
          Util.coreVersionAtLeast( { major: 1, minor: 9, release: 4 }, MIN ), true );
   check( "one release older fails",
          Util.coreVersionAtLeast( { major: 1, minor: 9, release: 3 }, MIN ), false );
   /*
    * The core Loom was written against still passes, obviously -- but it
    * is checked explicitly, because lowering the floor is the kind of
    * change that can accidentally invert a comparison.
    */
   check( "the core it was written against still passes",
          Util.coreVersionAtLeast( { major: 1, minor: 9, release: 5 }, MIN ), true );
   check( "one release newer passes",
          Util.coreVersionAtLeast( { major: 1, minor: 9, release: 6 }, MIN ), true );
   check( "older minor fails",
          Util.coreVersionAtLeast( { major: 1, minor: 8, release: 9 }, MIN ), false );
   check( "newer major passes regardless of the rest",
          Util.coreVersionAtLeast( { major: 2, minor: 0, release: 0 }, MIN ), true );
   check( "older major fails regardless of the rest",
          Util.coreVersionAtLeast( { major: 0, minor: 99, release: 99 }, MIN ), false );

   // The case a numeric or string comparison of "1.9.5" gets wrong: 1.10
   // comes AFTER 1.9, it is not 1.1.
   check( "1.10.0 is newer than 1.9.5",
          Util.coreVersionAtLeast( { major: 1, minor: 10, release: 0 }, MIN ), true );
   check( "1.9.10 is newer than 1.9.5",
          Util.coreVersionAtLeast( { major: 1, minor: 9, release: 10 }, MIN ), true );

   // A core that does not expose one of the components must not be read as
   // NaN and quietly pass.
   check( "missing release reads as zero",
          Util.coreVersionAtLeast( { major: 1, minor: 9 }, MIN ), false );
   check( "missing release reads as zero on a newer minor",
          Util.coreVersionAtLeast( { major: 1, minor: 10 }, MIN ), true );
   check( "all components missing fails",
          Util.coreVersionAtLeast( {}, MIN ), false );
   check( "formatCoreVersion fills missing components",
          Util.formatCoreVersion( { major: 1 } ), "1.0.0" );

   // ---- astrometric residual verdict --------------------------------------

   // Both thresholds, spelled out: 3.0 px is the verifier's matching
   // tolerance, 0.315 px is the published pre-1.9.5 solver median.
   check( "bad threshold is the matching tolerance", Steps.RESIDUALS_BAD_PX, 3.0 );
   check( "warn threshold is the published median", Steps.RESIDUALS_WARN_PX, 0.315 );

   // 0.091 px: the 1.9.5 recursive-surface-spline result on the published
   // mosaic panel. It must read as good, or the threshold is wrong.
   check( "published recursive-spline result is ok",
          Steps.residualVerdict( 0.091 ), "ok" );
   // 0.315 px: the published pre-1.9.5 result on the same panel, exactly at
   // the bar. At the bar is not over it.
   check( "published old-solver result is not yet poor",
          Steps.residualVerdict( 0.315 ), "ok" );
   check( "just past the bar is poor",
          Steps.residualVerdict( 0.316 ), "poor" );
   check( "well past the bar is poor",
          Steps.residualVerdict( 1.5 ), "poor" );
   check( "at the matching tolerance is bad",
          Steps.residualVerdict( 3.0 ), "bad" );
   check( "beyond the matching tolerance is bad",
          Steps.residualVerdict( 12 ), "bad" );
   check( "a perfect solution is ok", Steps.residualVerdict( 0 ), "ok" );

   // No number is not a good number.
   check( "NaN has no verdict", Steps.residualVerdict( NaN ), "unknown" );
   check( "undefined has no verdict", Steps.residualVerdict( undefined ), "unknown" );
   check( "null has no verdict", Steps.residualVerdict( null ), "unknown" );
   check( "a string has no verdict", Steps.residualVerdict( "0.1" ), "unknown" );
   check( "a negative deviation has no verdict",
          Steps.residualVerdict( -1 ), "unknown" );

   // Measured on the owner's own data, NGC 5907 masterLight L autocrop,
   // 5710x3182 at 0.966 arcsec/px, 1.9.5 build 1702, 2026-09-18. Both
   // the solution WBPP shipped and a fresh recursive-spline solve must
   // read as good, or the bar is in the wrong place for this rig.
   check( "the WBPP solution of a real master is ok",
          Steps.residualVerdict( 0.0190 ), "ok" );
   check( "a recursive-spline solve of the same master is ok",
          Steps.residualVerdict( 0.0157 ), "ok" );

   /* ---- GraXpert on the narrowband channels, opt-in ------------------------ */

   /*
    * Off by default, and OFF MUST CHANGE NOTHING: a narrowband channel's
    * cache key is its source fingerprint until registration, and adding
    * this option must not invalidate a single cached H/S/O result for
    * anyone who never turns it on. That invariant is the real contract;
    * the rest is plumbing.
    */
   ( function()
   {
      var src = "nb-source-fingerprint";
      var refKey = "L-reference";

      function registerKey( config )
      {
         var stages = Pipeline.narrowbandStages( config );
         var start = src;
         if ( stages != null )
         {
            var pre = Pipeline.buildStageKeys( src, stages );
            start = pre[pre.length-1].key;
         }
         var reg = Pipeline.buildStageKeys( start, { register: { ref: refKey } } );
         return reg[reg.length-1].key;
      }

      var today = Pipeline.buildStageKeys( src, { register: { ref: refKey } } );
      var todayKey = today[today.length-1].key;

      check( "narrowband GraXpert is off by default",
             Pipeline.narrowbandStages( {} ), null );
      check( "off leaves the narrowband register key exactly as it was",
             registerKey( { useGraXpert: true, smoothing: 0.5 } ), todayKey );

      /*
       * Nested under GraXpert: "also on H, S, O" extends the GraXpert
       * option rather than standing alone, so it does nothing while
       * GraXpert itself is off.
       */
      check( "it does nothing while GraXpert itself is off",
             Pipeline.narrowbandStages( { useGraXpert: false,
                                          graxpertNarrowband: true,
                                          smoothing: 0.5 } ), null );

      var on = { useGraXpert: true, graxpertNarrowband: true, smoothing: 0.5 };
      check( "on, it adds a graxpert stage",
             Object.keys( Pipeline.narrowbandStages( on ) ), [ "graxpert" ] );
      check( "and registration then chains from the corrected result",
             registerKey( on ) != todayKey, true );
      check( "and the smoothing is part of the key",
             registerKey( on ) !=
             registerKey( { useGraXpert: true, graxpertNarrowband: true,
                            smoothing: 0.8 } ), true );
   } )();

   /* ---- double click zooms to WHAT WAS CLICKED ---------------------------- */

   /*
    * This was lost once, reverted along with an unrelated change, and the
    * user had to report it. The geometry is the part worth pinning: the
    * fitted frame is drawn scaled by min(vw/w, vh/h) and CENTRED, so it
    * sits inside a letterbox band -- measured at 23 px on a 3:2 frame in
    * this pane. Mapping a click without subtracting that band puts the
    * zoom 23 px off, which looks like the feature half-working.
    */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var ok = true, err = "";
      try
      {
         var p = new FrameSelector.PreviewControl( null );

         // A bitmap of known size, so the arithmetic is checkable without
         // depending on any particular frame being present.
         p.bmp = new Bitmap( 6248, 4176 );
         p.setFit( true );

         var vw = p.viewport.width, vh = p.viewport.height;
         var s = Math.min( vw/6248, vh/4176 );
         var fx = ( vw - 6248*s )/2, fy = ( vh - 4176*s )/2;

         // the middle of the DRAWN image is the middle of the bitmap
         var mid = p.fittedPixelAt( fx + 6248*s/2, fy + 4176*s/2 );
         ok = ok && ( Math.abs( mid.x - 3124 ) < 1 ) && ( Math.abs( mid.y - 2088 ) < 1 );

         // its top-left corner is pixel 0,0 -- this is the one the
         // letterbox band breaks if it is not subtracted
         var tl = p.fittedPixelAt( fx, fy );
         ok = ok && ( Math.abs( tl.x ) < 1 ) && ( Math.abs( tl.y ) < 1 );

         // and the gesture itself: zoom in on a point, land on it
         var want = p.fittedPixelAt( fx + 6248*s*0.25, fy + 4176*s*0.25 );
         p.setFit( false );
         p.centreOn( want );
         var got = { x: p.horizontalScrollPosition + vw/2,
                     y: p.verticalScrollPosition + vh/2 };
         ok = ok && ( Math.abs( got.x - want.x ) <= 1 )
                 && ( Math.abs( got.y - want.y ) <= 1 );

         // centreOn must do NOTHING while fitted: there is nowhere to scroll
         p.setFit( true );
         p.horizontalScrollPosition = 0;
         p.centreOn( { x: 5000, y: 3000 } );
         ok = ok && ( p.horizontalScrollPosition == 0 );

         try { p.release(); } catch ( e2 ) {}
      }
      catch ( e ) { ok = false; err = String( e ); }
      check( "double click zooms to the clicked point" + ( err ? ": " + err : "" ),
             ok, true );
   } )();

   /* ---- the 1.9.4 build must actually differ ------------------------------ */

   /*
    * Loom compiles differently on 1.9.4, where AstrometricResiduals does
    * not exist. Asserting that both builds merely "pass" proves nothing --
    * a conditional that never fires passes too. This checks the BEHAVIOUR
    * each build is supposed to have.
    *
    * Run the suite with LOOM_TEST_CORE_RELEASE=4 to exercise the other
    * side; CI runs both.
    */
   ( function()
   {
      var haveVerifier = true;
#ifndef LOOM_HAVE_RESIDUALS
      haveVerifier = false;
#endif
      check( "the build knows whether it has a verifier",
             typeof haveVerifier, "boolean" );

      if ( haveVerifier )
      {
         /*
          * 1.9.5+: the class must really be there -- but only PixInsight
          * can answer that. The node harness strips <pjsr/...> includes,
          * so under node it is absent whichever way the build compiled,
          * and asserting it there would fail for a reason that has
          * nothing to do with the conditional under test.
          */
         if ( IN_PIXINSIGHT )
            check( "AstrometricResiduals is available to this build",
                   typeof AstrometricResiduals != "undefined", true );
      }
      else
      {
         /*
          * 1.9.4: verifySolution must REFUSE rather than reach for a class
          * that does not exist. A window is never touched, so null is safe
          * to ask for.
          */
         check( "without the verifier, verifySolution returns null",
                Steps.verifySolution( null ), null );
         check( "and verifyAndReport passes that through, not a crash",
                Steps.verifyAndReport( null, "x" ), null );
      }
   } )();

   // ---- verification never costs the run ----------------------------------

   // A window with no solution at all: measure() throws, the warning is
   // logged, and null -- not an exception -- reaches the caller. Steps.solve
   // must not lose a good solve because the diagnostic failed.
   check( "a failed verification returns null",
          Steps.verifyAndReport( { isNull: true, mainView: { id: "x" } }, "x" ),
          null );
   check( "a failed verification is repeatable, not latched",
          Steps.verifyAndReport( { isNull: true, mainView: { id: "y" } }, "y" ),
          null );

   // ---- residual measurement configuration --------------------------------

   // The thresholds are read against this value; if the tolerance is ever
   // changed the bad threshold has to move with it.
   check( "matching tolerance matches the bad threshold",
          Steps.RESIDUALS_CONFIG.matchingTolerance, Steps.RESIDUALS_BAD_PX );
   // AstrometricResiduals' header lists exactly these as required.
   var needed = [ "structureLayers", "minStructureSize", "hotPixelFilterRadius",
                  "noiseReductionFilterRadius", "sensitivity", "peakResponse",
                  "brightThreshold", "maxStarDistortion", "autoPSF",
                  "autoMagnitude", "magnitude", "restrictToHQStars",
                  "matchingTolerance", "rejectionSigma" ];
   var missing = [];
   for ( var ri = 0; ri < needed.length; ++ri )
      if ( Steps.RESIDUALS_CONFIG[needed[ri]] === undefined )
         missing.push( needed[ri] );
   check( "every parameter AstrometricResiduals requires is present", missing, [] );

   // ---- registration reference --------------------------------------------

   /*
    * Which channel everything is registered to. L when there is one --
    * that must never change -- and otherwise the channel whose stars pin
    * the transform down best: FWHM / sqrt(star count), lowest wins. The
    * figures are the SubframeSelector ones Loom already measures per
    * master (Steps.measureMasterFWHM), so the fixtures are shaped alike.
    */
   ( function()
   {
      var rr = Pipeline.registrationReference;
      var sharpRich = { fwhm: 2.8, stars: 3000 };

      check( "L is the reference whenever it is present",
             rr( [ "L", "R", "G", "B" ],
                 { L: { fwhm: 6.0, stars: 50 }, G: sharpRich } ).key, "L" );
      check( "L wins with no measurements at all",
             rr( [ "H", "O", "L" ], {} ).key, "L" );

      // A typical RGB set: G is the sharpest and richest
      check( "RGB without L registers to the best-measured channel",
             rr( [ "R", "G", "B" ],
                 { R: { fwhm: 3.4, stars: 2400 },
                   G: { fwhm: 3.0, stars: 2600 },
                   B: { fwhm: 3.9, stars: 1900 } } ).key, "G" );

      // Sharpness alone does not win: a starved OIII registers nothing well
      check( "a sharp but star-starved channel loses to a deep one",
             rr( [ "H", "S", "O" ],
                 { H: { fwhm: 3.2, stars: 3000 },
                   S: { fwhm: 3.4, stars: 900 },
                   O: { fwhm: 2.9, stars: 150 } } ).key, "H" );

      // ...and neither does star count alone: a soft channel places every
      // centroid less precisely
      check( "a soft channel loses despite a few more stars",
             rr( [ "R", "G", "B" ],
                 { R: { fwhm: 6.0, stars: 2000 },
                   G: { fwhm: 3.0, stars: 1800 },
                   B: { fwhm: 5.5, stars: 1900 } } ).key, "G" );

      // Nothing measured: the fixed order, broadband before narrowband
      check( "unmeasured RGB falls back to G",
             rr( [ "R", "G", "B" ], {} ).key, "G" );
      check( "unmeasured RGB plus Ha still falls back to G",
             rr( [ "R", "G", "B", "H" ], null ).key, "G" );
      check( "unmeasured narrowband falls back to H",
             rr( [ "O", "S", "H" ], {} ).key, "H" );
      check( "unmeasured SII + OIII falls back to S",
             rr( [ "O", "S" ], {} ).key, "S" );
      check( "a lone channel is its own reference",
             rr( [ "O" ], {} ).key, "O" );

      // A measured channel beats one that could not be measured
      check( "measured channels rank ahead of unmeasured ones",
             rr( [ "R", "G", "B" ],
                 { R: { fwhm: 4.0, stars: 1000 }, G: null,
                   B: { fwhm: 3.5, stars: 1200 } } ).key, "B" );

      // Garbage is not a measurement
      check( "zero, negative and non-numeric measurements are ignored",
             rr( [ "R", "G", "B" ],
                 { R: { fwhm: 0, stars: 5000 },
                   G: { fwhm: NaN, stars: 5000 },
                   B: { fwhm: 3.0, stars: 0 } } ).key, "G" );
      check( "a failed measurement does not beat a real one",
             rr( [ "H", "O" ],
                 { H: { fwhm: -1, stars: 9000 },
                   O: { fwhm: 3.0, stars: 200 } } ).key, "O" );

      // Ties go to the fixed order, never to object or argument order
      check( "an exact tie goes to the fixed order (G over R)",
             rr( [ "R", "G" ], { R: sharpRich, G: sharpRich } ).key, "G" );
      check( "an exact tie goes to the fixed order (H over S)",
             rr( [ "S", "H" ], { S: sharpRich, H: sharpRich } ).key, "H" );
      check( "the argument order does not change the answer",
             rr( [ "B", "G", "R" ],
                 { R: { fwhm: 3.4, stars: 2400 },
                   G: { fwhm: 3.0, stars: 2600 },
                   B: { fwhm: 3.9, stars: 1900 } } ).key,
             rr( [ "R", "G", "B" ],
                 { B: { fwhm: 3.9, stars: 1900 },
                   R: { fwhm: 3.4, stars: 2400 },
                   G: { fwhm: 3.0, stars: 2600 } } ).key );

      check( "no channels, no reference", rr( [], {} ).key, null );

      // The reason is what the log says; it has to name the evidence
      var why = rr( [ "H", "O" ], { H: { fwhm: 3.2, stars: 3000 },
                                    O: { fwhm: 2.9, stars: 150 } } ).reason;
      check( "the reason quotes the winner's FWHM and star count",
             why.indexOf( "3.20" ) >= 0 && why.indexOf( "3000" ) >= 0, true );
      check( "the fallback says it is a fallback",
             rr( [ "R", "G", "B" ], {} ).reason.indexOf( "no usable" ) >= 0, true );
   } )();

   /*
    * The wiring: every place that assumed L now follows the chosen key.
    * StarAlignment and SubframeSelector are stubbed, so this runs under
    * node; the real processes are exercised by the PixInsight block below.
    */
   ( function()
   {
      var realRegister = Steps.register, realMeasure = Steps.measureMasterFWHM;
      var realValidRect = Steps.validRect, realCropTo = Steps.cropTo;
      var calls = [], measured = [];
      function fakeWin( id, w, h )
      {
         var win = { id: id, closed: false,
                     forceClose: function() { this.closed = true; } };
         win.mainView = { id: id, image: { width: w || 100, height: h || 100 } };
         return win;
      }
      function chan( key, path, loaded )
      {
         var c = { key: key, path: path, window: null, view: null,
                   sourceKey: "src-" + key, currentKey: "cur-" + key };
         if ( loaded )
         {
            c.window = fakeWin( key + "_work" );
            c.view = c.window.mainView;
         }
         else
            c.load = function()
            {
               this.window = fakeWin( this.key + "_work" );
               this.view = this.window.mainView;
               return this.window;
            };
         return c;
      }
      var quality = { "/m/R.xisf": { fwhm: 3.4, stars: 2400 },
                      "/m/G.xisf": { fwhm: 3.0, stars: 2600 },
                      "/m/B.xisf": { fwhm: 3.9, stars: 1900 },
                      "/m/H.xisf": { fwhm: 3.2, stars: 3000 },
                      "/m/O.xisf": { fwhm: 2.9, stars: 150 },
                      "/m/L.xisf": { fwhm: 9.0, stars: 10 } };
      try
      {
         Steps.register = function( view, refView )
         {
            calls.push( view.id + ">" + refView.id );
            return fakeWin( view.id + "_registered" );
         };
         Steps.measureMasterFWHM = function( path )
         {
            measured.push( path );
            return quality[path] || null;
         };
         var cfg = { useCache: false };

         // RGB without L: G is the reference, R and B register to it
         var chans = { R: chan( "R", "/m/R.xisf", true ),
                       G: chan( "G", "/m/G.xisf", true ),
                       B: chan( "B", "/m/B.xisf", true ) };
         var gKey = chans.G.currentKey, rKey = chans.R.currentKey;
         var ref = Pipeline.registerToReference( chans, cfg, new Util.Registry );
         check( "without L the reference is the chosen channel", ref.refKey, "G" );
         check( "everything else registers to it, and it is not registered itself",
                calls, [ "R_work>G_work", "B_work>G_work" ] );
         check( "the reference view is the chosen channel's",
                ref.refView === chans.G.view, true );
         check( "the cache fingerprint is the chosen channel's key",
                ref.refFingerprint, gKey );
         check( "the reference keeps its own running key", chans.G.currentKey, gKey );
         check( "a registered channel's key chains from the chosen reference",
                chans.R.currentKey,
                Cache.chainKey( rKey, "register", { ref: gKey } ) );

         // L present: L is the reference, keys exactly as before, and
         // nothing is measured -- the L path costs nothing new
         calls = []; measured = [];
         var lchans = { L: chan( "L", "/m/L.xisf", true ),
                        R: chan( "R", "/m/R.xisf", true ),
                        G: chan( "G", "/m/G.xisf", true ),
                        B: chan( "B", "/m/B.xisf", true ) };
         var lKey = lchans.L.currentKey, lrKey = lchans.R.currentKey;
         var lref = Pipeline.registerToReference( lchans, cfg, new Util.Registry );
         check( "with L present the reference is still L", lref.refKey, "L" );
         check( "with L present every other channel registers to L",
                calls, [ "R_work>L_work", "G_work>L_work", "B_work>L_work" ] );
         check( "with L present the register key is unchanged",
                lchans.R.currentKey,
                Cache.chainKey( lrKey, "register", { ref: lKey } ) );
         check( "with L present no master is measured", measured, [] );

         // Narrowband only, reference not yet loaded: it must be loaded
         // before StarAlignment is pointed at it
         calls = [];
         var nchans = { H: chan( "H", "/m/H.xisf", false ),
                        O: chan( "O", "/m/O.xisf", true ) };
         var nref = Pipeline.registerToReference( nchans, cfg, new Util.Registry );
         check( "narrowband only: Ha is the reference", nref.refKey, "H" );
         check( "an unloaded reference is loaded before registering to it",
                calls, [ "O_work>H_work" ] );

         /*
          * The crop's sanity floor is a percentage of the REFERENCE frame.
          * A 600x600 common area of a 1000x1000 G is 36%, and the error
          * must say so -- reading chans.L here would throw a TypeError.
          */
         Steps.validRect = function( view, margin )
         {
            return { x0: 0, y0: 0, x1: 600, y1: 600 };
         };
         Steps.cropTo = function( view, rect ) {};
         var cchans = { R: chan( "R", "/m/R.xisf", true ),
                        G: chan( "G", "/m/G.xisf", true ),
                        B: chan( "B", "/m/B.xisf", true ) };
         cchans.G.view.image = { width: 1000, height: 1000 };
         var cropError = "";
         try { Pipeline.cropToCommonArea( cchans, "G" ); }
         catch ( e ) { cropError = String( e ); }
         check( "the crop floor is measured against the chosen reference",
                cropError.indexOf( "only 36.0%" ) >= 0, true );
         Steps.validRect = function( view, margin )
         {
            return { x0: 0, y0: 0, x1: 900, y1: 1000 };
         };
         var common = Pipeline.cropToCommonArea( cchans, "G" );
         check( "and without L the crop goes ahead on the chosen reference",
                common.x1, 900 );

         // The composite's camera follows L, and the reference without it
         check( "the composite camera is L's when L is present",
                Pipeline.compositeInstrument(
                   { L: { instrume: "ZWO ASI6200MM" }, G: { instrume: "other" } }, "L" ),
                "ZWO ASI6200MM" );
         check( "and the reference channel's without L",
                Pipeline.compositeInstrument(
                   { R: { instrume: "r-cam" }, G: { instrume: "ZWO ASI2600MM" } }, "G" ),
                "ZWO ASI2600MM" );
         check( "and nothing when there is no camera to name",
                Pipeline.compositeInstrument( {}, null ), null );
      }
      finally
      {
         Steps.register = realRegister;
         Steps.measureMasterFWHM = realMeasure;
         Steps.validRect = realValidRect;
         Steps.cropTo = realCropTo;
      }
   } )();

   /*
    * The same choice on real measurements and a real StarAlignment, from
    * GENERATED masters: a sharp G and a softer, shallower R and B.
    * The quality table and cache are pointed at the suite's own scratch
    * folder so nothing of the user's is read or written.
    *
    * All three share ONE star population, as three filters on one field
    * do, and differ in width and depth -- R and B are fainter, so fewer
    * of the same stars are detected. The first version gave R and B 120
    * stars against G's 500, and StarAlignment refused R outright: the
    * 120 were a sparse subset of G's field. Measured directly, with no
    * Loom code between the files and the process, it fails whenever one
    * frame sees only ~a fifth of the other's stars -- at equal FWHM too,
    * and with the roles reversed -- and succeeds on matched populations,
    * on an identity transform and on an offset one. So that was the
    * fixture, not the wiring. R and B are also shifted by a few pixels,
    * so the registration is a real transform, not an identity.
    */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var dir = synthDir( "reg-reference" );
      var savedCacheDir = Cache.overrideDir, savedTable = Steps.qualityTable;
      var reg = new Util.Registry;
      try
      {
         Cache.setDir( dir + "/cache" );
         Steps.qualityTable = null;
         var paths = {
            R: synthFrame( dir + "/R.xisf", { fwhm: 5.0, stars: 500, flux: 0.6,
                                               dx: 3.3, dy: -2.1, background: 0.02,
                                               noise: 0.002, seed: 31, filter: "R" } ),
            G: synthFrame( dir + "/G.xisf", { fwhm: 2.6, stars: 500,
                                               background: 0.02,
                                               noise: 0.002, seed: 32, filter: "G" } ),
            B: synthFrame( dir + "/B.xisf", { fwhm: 5.5, stars: 500, flux: 0.6,
                                               dx: -1.7, dy: 2.4, background: 0.02,
                                               noise: 0.002, seed: 33, filter: "B" } )
         };
         var q = {};
         for ( var k in paths )
            q[k] = Steps.measureMasterFWHM( paths[k] );
         check( "the generated masters all measure",
                q.R != null && q.G != null && q.B != null, true );
         check( "the sharp, star-rich generated master is chosen",
                Pipeline.registrationReference( [ "R", "G", "B" ], q ).key, "G" );

         var chans = Pipeline.loadChannels( { paths: paths, views: {} }, reg );
         var ref = Pipeline.registerToReference( chans, { useCache: false }, reg );
         check( "a real no-L run registers to G", ref.refKey, "G" );
         var gw = chans.G.view.image.width, gh = chans.G.view.image.height;
         check( "every channel lands on the reference grid",
                chans.R.view.image.width == gw && chans.R.view.image.height == gh &&
                chans.B.view.image.width == gw && chans.B.view.image.height == gh, true );
         var common = Pipeline.cropToCommonArea( chans, ref.refKey );
         check( "and the common-area crop runs without L", common != null, true );
      }
      finally
      {
         reg.closeAll();
         Steps.qualityTable = savedTable;
         Cache.setDir( savedCacheDir );
      }
   } )();

   runFlyTests();
}

/*
 * Loom Fly-Through. Fly.js is pure and runs under node; Sky and Render
 * need PixInsight and sit behind IN_PIXINSIGHT.
 */
/*
 * The Fly-Through tests run with none of the user's saved Fly-Through
 * settings, and every one is put back after, whatever happens: tests that
 * read the user's own saved opacity, duration or focal length broke, and
 * one wrote 400 mm over the user's rig.
 */
function runFlyTests()
{
   var keys = typeof FlyThrough == "undefined" ? [] : [ FlyThrough.OPTIONS_SETTING, FlyThrough.OBJECTS_SETTING, FlyThrough.LOGO_SETTING,
              FlyThrough.LOGO_PLACE_SETTING, FlyThrough.FOCAL_SETTING, FlyThrough.PIXEL_SETTING, FlyThrough.TOOL_SETTING ];
   var saved = keys.map( function( k ) { return Settings.read( k, DataType_String ); } );
   keys.forEach( function( k ) { Settings.remove( k ); } );
   try { runFlyTestsClean(); }
   finally { keys.forEach( function( k, i ) { if ( saved[i] != null ) Settings.write( k, DataType_String, saved[i] ); else Settings.remove( k ); } ); }
}

function runFlyTestsClean()
{
   check( "Fly loads", typeof Fly, "object" );
   check( "Sky loads", typeof Sky, "object" );
   check( "Render loads", typeof Render, "object" );

   /* ---- Fly: exact geometry ------------------------------------------- */
   ( function()
   {
      function near( a, b, eps ) { return Math.abs( a - b ) <= ( eps || 1e-9 ); }
      var r = Fly.radec( Fly.vec( 324.745, 57.514 ) );
      check( "vec/radec round trip", near( r.ra, 324.745, 1e-9 ) && near( r.dec, 57.514, 1e-9 ), true );
      var T = { ra: 324.745, dec: 57.514 };
      var on = Fly.moved( 324.745, 57.514, 500, T, 100 );
      check( "on axis: direction unchanged", near( on.ra, 324.745, 1e-9 ) && near( on.dec, 57.514, 1e-9 ), true );
      check( "on axis: ratio is d/(d-s)", near( on.ratio, 500/400, 1e-12 ), true );
      // off axis: a star 1 degree away at 100 pc, camera moves 50 pc
      var off = Fly.moved( 324.745, 58.514, 100, T, 50 );
      var th = Math.PI/180, px = 100*Math.sin( th ), pz = 100*Math.cos( th ) - 50;
      check( "off axis: exact angle from the target",
             near( Fly.separation( off, T ), Math.atan2( px, pz )*180/Math.PI, 1e-9 ), true );
      check( "off axis: exact ratio", near( off.ratio, 100/Math.sqrt( px*px + pz*pz ), 1e-12 ), true );
      check( "a passed star is behind the camera", Fly.moved( 324.745, 57.6, 40, T, 60 ).front, false );
      check( "backdrop K", Fly.backdropScale( 900, 180 ), 900/720 );
      check( "galaxy backdrop fixed", Fly.backdropScale( Infinity, 180 ), 1 );
      var w = { crval1: 324.745, crval2: 57.514, crpix1: 200.5, crpix2: 150.5, cd: [ -0.0005, 0, 0, 0.0005 ] };
      var c = Fly.tanProject( 324.745, 57.514, w );
      check( "TAN: reference point lands on CRPIX (0-based)", near( c.x, 199.5, 1e-9 ) && near( c.y, 149.5, 1e-9 ), true );
      var e = Fly.tanProject( 324.745, 57.514 + 0.05, w );
      check( "TAN: north is +y for a positive CD2_2", e.y > c.y, true );
   } )();

   ( function()
   {
      function near( a, b ) { return Math.abs( a - b ) < 1e-12; }
      check( "no motion, no growth", Fly.growth( 1, 0.15 ), 1 );
      check( "growth rule", near( Fly.growth( 3, 0.15 ), 1.3 ), true );
      // total light = pixelScale * g^2 * (original total) must equal ratio^2
      var g = Fly.growth( 2.5, 0.2 );
      check( "integrated flux follows inverse square whatever the growth",
             near( Fly.pixelScale( 2.5, 0.2, true )*g*g, 2.5*2.5 ), true );
      check( "brightening off keeps total light", near( Fly.pixelScale( 2.5, 0.2, false )*g*g, 1 ), true );
      check( "opacity 1 far away", Fly.opacity( 2, true ), 1 );
      check( "opacity 0 before the singularity", Fly.opacity( 25, true ), 0 );
      var prev = 1, mono = true;
      for ( var r = 1; r <= 25; r += 0.25 ) { var o = Fly.opacity( r, true ); if ( o > prev + 1e-12 ) mono = false; prev = o; }
      check( "opacity never rises as a star nears", mono, true );
      check( "never drawn behind the camera", Fly.opacity( 1.5, false ), 0 );
   } )();

   ( function()
   {
      function near( a, b ) { return Math.abs( a - b ) < 1e-9; }
      check( "sigma at bright end", near( Fly.parallaxSigma( 12 ), 0.02 ), true );
      check( "sigma at G 17", near( Fly.parallaxSigma( 17 ), 0.07 ), true );
      check( "sigma interpolates", Fly.parallaxSigma( 16.5 ) > 0.02 && Fly.parallaxSigma( 16.5 ) < 0.07, true );
      check( "sigma clamps faint", near( Fly.parallaxSigma( 19 ), 0.1 ), true );
      check( "zero point added", near( Fly.usableParallax( { plx: 1.0, G: 12 } ), 1.017 ), true );
      check( "below 5 sigma is not usable", Fly.usableParallax( { plx: 0.30, G: 17 } ), null );
      check( "negative is not usable", Fly.usableParallax( { plx: -0.5, G: 10 } ), null );
      check( "missing is not usable", Fly.usableParallax( { plx: null, G: 10 } ), null );
   } )();

   ( function()
   {
      function rng( seed ) { var a = seed >>> 0; return function() { a = ( a*1664525 + 1013904223 ) >>> 0; return a/4294967296; }; }
      function gauss( r ) { return Math.sqrt( -2*Math.log( Math.max( 1e-12, r() ) ) )*Math.cos( 2*Math.PI*r() ); }
      var r = rng( 7 ), T = { ra: 100, dec: 20 }, src = [];
      for ( var i = 0; i < 1500; ++i )                   // field: broad pm, parallax 0.2..3
      {
         src.push( { ra: 100 + ( r() - 0.5 )*6, dec: 20 + ( r() - 0.5 )*6, plx: 0.2 + 2.8*r(),
                     pmra: 6*gauss( r ), pmdec: 6*gauss( r ), G: 11 + 4*r() } );
      }
      for ( var k = 0; k < 80; ++k )                     // cluster: 1.1 mas, tight pm, inside 0.8 deg
         src.push( { ra: 100 + ( r() - 0.5 )*1.2, dec: 20 + ( r() - 0.5 )*1.2, plx: 1.1 + 0.03*gauss( r ),
                     pmra: -2.4 + 0.2*gauss( r ), pmdec: -4.6 + 0.2*gauss( r ), G: 11 + 4*r() } );
      var c = Fly.findCluster( src, T, 1.0 );
      check( "a constructed cluster is found", c != null, true );
      check( "at its distance", c != null && Math.abs( c.distance - 1000/1.117 ) < 40, true );
      check( "with its members", c != null && c.members >= 60, true );
      var none = Fly.findCluster( src.slice( 0, 1500 ), T, 1.0 );
      check( "a uniform field has no cluster", none, null );
      check( "a small target keeps its own radius", Fly.clusterRadius( 0.2 ), 0.2 );
      check( "a big nebula's inside radius is capped", Fly.clusterRadius( 85/60 ), Fly.CLUSTER_MAX_RADIUS );
   } )();

   ( function()
   {
      var path = LOOM_DIR + "/../ci/fixtures/ic1396-gaia-g16.tsv";
      check( "the IC 1396 fixture is in the repository", File.exists( path ), true );
      var src = Fly.parseSources( File.readTextFile( path ) );
      check( "fixture parses", src.length > 8000, true );
      var c = Fly.findCluster( src, { ra: 324.745, dec: 57.514 }, Fly.clusterRadius( 85/60 ) );   // NGC/IC diameter 170', capped
      check( "IC 1396's cluster gives 870-1000 pc: " + ( c && c.distance ),
             c != null && c.distance >= 870 && c.distance <= 1000, true );
   } )();

   ( function()
   {
      check( "nebula travel default", Fly.defaultTravel( "nebula", 900 ), 180 );
      check( "galaxy travel default", Fly.defaultTravel( "galaxy", Infinity ), 200 );
      check( "travel cap", Fly.clampTravel( 950, "nebula", 900 ), { travel: 810, clamped: true } );
      check( "galaxy never capped", Fly.clampTravel( 950, "galaxy", Infinity ), { travel: 950, clamped: false } );
      check( "smoothstep ends at rest",
             Math.abs( Fly.ease( 0.001, "smoothstep" ) - Fly.ease( 0, "smoothstep" ) ) < 1e-5, true );
      check( "frames", Fly.frameCount( 10, 30, false ), 300 );
      check( "ping-pong doubles", Fly.frameCount( 10, 30, true ), 600 );
      check( "ping-pong turns at the middle", Fly.timeAt( 300, 600, true ), 1 );
      check( "ping-pong returns", Fly.timeAt( 599, 600, true ) < 0.01, true );
      // velocity continuity at the turns: smoothstep has zero slope at 0 and 1
      var v = function( t ) { return ( Fly.ease( t + 1e-4, "smoothstep" ) - Fly.ease( t, "smoothstep" ) )/1e-4; };
      check( "no velocity jump at the far turn", Math.abs( v( 1 - 1e-4 ) ) < 1e-3, true );
      var dp = Fly.draftPlan( 120, 60, 480, 16/9 );
      check( "draft stays under 400 MB for any duration", dp.bytes <= 400e6, true );
      check( "and keeps the clip length", Math.abs( dp.frames/dp.fps - 120 ) < 1, true );
      var c = Fly.presetCrop( 6000, 4000, 5900, 2000, 1080, 1920 );
      check( "vertical crop keeps the aspect", Math.abs( c.w/c.h - 1080/1920 ) < 1e-3, true );
      check( "and stays inside the image", c.x >= 0 && c.x + c.w <= 6000 && c.y >= 0 && c.y + c.h <= 4000, true );
   } )();

   ( function()
   {
      var csv = "id,alpha,delta,magnitude,diameter,axisRatio,posAngle,Common name,PGC,PGC2,Messier\n" +
                "IC1396,324.745000,57.514000,3.50,170.00,,,,,,\n" +
                "NGC7129,325.775,66.113,11.5,7.0,,,,,,\n" +
                "IC5146,328.36,47.27,7.2,12,,,Cocoon,,,\n" +
                "NGC224,10.6847,41.269,3.4,190,,,Andromeda,PGC2557,,M31\n";
      var e = Fly.parseNgcIc( csv );
      check( "ngc/ic parses", e.length, 4 );
      var p = Fly.pickTarget( e, { ra: 324.7, dec: 57.5 }, 1.5 );
      check( "the big nearby nebula wins", p.best.id, "IC1396" );
      check( "a nebula", Fly.targetType( p.best ), "nebula" );
      check( "a PGC number makes a galaxy", Fly.targetType( e[3] ), "galaxy" );
      var none = Fly.pickTarget( e, { ra: 200, dec: -30 }, 1 );
      check( "no target in the field", none.best, null );
      // a bigger object near the field's edge must not beat a big one at the centre
      var edge = Fly.parseNgcIc( csv + "IC9999,324.745,58.914,5,200,,,,,,\n" );
      check( "distance from the centre counts, not size alone",
             Fly.pickTarget( edge, { ra: 324.745, dec: 57.514 }, 1.5 ).best.id, "IC1396" );
   } )();

   /*
    * Fly-Through removes stars from a STRETCHED image; Loom's composites
    * are linear. Only StarNet2 has the switch, and omitting the argument
    * must keep Loom's own calls exactly as they were.
    */
   check( "removeStars passes linear through to StarNet2 only",
          /P\.linear = \( linear !== false \);/.test( Steps.removeStars.toString() ), true );

   /* ---- Fly: inverse projection, placed stars, sprite ownership -------- */
   ( function()
   {
      var w = { crval1: 324.745, crval2: 57.514, crpix1: 200.5, crpix2: 150.5, cd: [ -0.0005, 0.0001, 0.0001, 0.0005 ] };
      var back = Fly.tanUnproject( 37.25, 250.75, w ), there = Fly.tanProject( back.ra, back.dec, w );
      check( "TAN inverse round-trips a pixel", Math.abs( there.x - 37.25 ) < 1e-6 && Math.abs( there.y - 250.75 ) < 1e-6, true );

      var srcs = [ { ra: 1, dec: 1, plx: 2.0, G: 12 }, { ra: 2, dec: 2, plx: 0.01, G: 17 }, { ra: 3, dec: 3, plx: 1.0, G: 12 } ];
      var at = { 1: { x: 10, y: 10 }, 2: { x: 20, y: 20 }, 3: { x: -5, y: 10 } };
      var placed = Fly.placeStars( srcs, function( ra ) { return at[ra]; }, 100, 100 );
      check( "placed: usable parallax and inside the image only", placed.length, 1 );
      check( "placed: distance in parsecs from the corrected parallax", Math.abs( placed[0].d - 1000/2.017 ) < 1e-9, true );

      function det( x, y, r, nmax ) { return { x: x, y: y, nmax: nmax || 1, rect: { x0: x - r, y0: y - r, x1: x + r + 1, y1: y + r + 1 } }; }
      var dets = [ det( 20, 20, 2 ), det( 50, 20, 2, 2 ), det( 80, 20, 2 ), det( 86, 20, 1 ), det( 20, 60, 2 ), det( 30, 60, 2 ) ];
      var P = function( x, y ) { return { source: { x: x }, d: 100, x: x, y: y }; };
      var a = Fly.assignSprites( dets, [ P( 20.5, 20.4 ), P( 50, 20 ), P( 80, 20 ), P( 60, 80 ), P( 20, 60 ), P( 30, 60 ) ], 100, 100, 3 );
      check( "an isolated match becomes a sprite", a.sprites.some( function( s ) { return s.det === dets[0]; } ), true );
      check( "a detection with more than one maximum is a blend",
             a.sprites.some( function( s ) { return s.det === dets[1]; } ), false );
      check( "a footprint that reaches another detection is a blend",
             a.sprites.some( function( s ) { return s.det === dets[2]; } ), false );
      check( "counts: placed, blended, unmatched", [ a.sprites.length, a.blended, a.unmatched ], [ 3, 2, 1 ] );
      var s0 = a.sprites.filter( function( s ) { return s.det === dets[0]; } )[0];
      check( "the footprint is the detection grown by the FWHM", s0.rect, { x0: 15, y0: 15, x1: 26, y1: 26 } );
      var left = a.sprites.map( function( s ) { return s.det; } ).indexOf( dets[4] );
      check( "where footprints overlap, one sprite keeps the pixel", a.owner[60*100 + 25] >= 0, true );
      check( "the two neighbours at y=60 both became sprites despite touching footprints",
             a.sprites.filter( function( s ) { return s.det.y == 60; } ).length, 2 );
      check( "the one detection two sources point at goes to the nearer",
             Fly.assignSprites( [ det( 20, 20, 2 ) ], [ P( 21, 20 ), P( 20.2, 20 ) ], 100, 100, 3 ).sprites[0].placed.x, 20.2 );
   } )();

   /* ---- Sky: catalogues, projection, star layers, sprites (PixInsight) - */
   if ( IN_PIXINSIGHT ) ( function()
   {
      check( "NGC/IC is found in the core install", ( Sky.readNgcIc() || [] ).length > 9000, true );
      var path = synthFrame( synthDir( "fly-sky" ) + "/field.xisf", { fwhm: 3, background: 0.05, noise: 0.002, seed: 9 } );
      var w = ImageWindow.open( path )[0];
      try
      {
         withTanKeywords( w, 324.745, 57.514, 0.0005 );
         var proj = Sky.projector( w );
         var c = proj( 324.745, 57.514 );
         check( "projector: keywords give the reference pixel", Math.abs( c.x - 399.5 ) < 1e-6 && Math.abs( c.y - 299.5 ) < 1e-6, true );
         var f = Sky.field( w );
         check( "field radius reaches the corners", f.radiusDeg > 0.2 && f.radiusDeg < 0.3, true );
         check( "field centre is the image centre", Fly.separation( f.centre, { ra: 324.745, dec: 57.514 } ) < 0.001, true );
         var none = ImageWindow.open( path )[0];
         check( "no keywords, no solution: no projector", Sky.projector( none ), null );
         none.forceClose();

         var saved = Sky.querySources, threw = false;
         Sky.querySources = function() { return []; };
         try { Sky.requireSources( { ra: 0, dec: 0 }, 1 ); } catch ( e ) { threw = /Gaia/.test( String( e ) ); }
         Sky.querySources = saved;
         check( "an empty query stops with the configure message", threw, true );

         // sprites: the first 20 detections, as if Gaia had placed a star on each
         var img = w.mainView.image, W = img.width, H = img.height;
         var dets = Sky.detections( img );
         check( "the synthetic field has stars to detect", dets.length > 100, true );
         var placed = dets.slice( 0, 20 ).map( function( d ) { return { source: { ra: 0, dec: 0 }, d: 500, x: d.x + 0.3, y: d.y }; } );
         var sp = Sky.sprites( img, placed, 3 );
         check( "most injected stars become sprites", sp.sprites.length + sp.blended, 20 );
         check( "every sprite's footprint contains its star",
                sp.sprites.every( function( s ) { return s.centre.x >= s.rect.x0 && s.centre.x < s.rect.x1 &&
                                                         s.centre.y >= s.rect.y0 && s.centre.y < s.rect.y1; } ), true );
         // pixels are SPLIT between a sprite (its light) and the residual (the rest):
         // together they must be the stars layer exactly, or frame 0 is not the image
         var total = new Float32Array( W*H ), orig = new Float32Array( W*H ), worst = 0;
         total.set( sp.residual[0] );
         img.getSamples( orig );
         sp.sprites.forEach( function( s )
         {
            var rw = s.rect.x1 - s.rect.x0;
            for ( var y = s.rect.y0; y < s.rect.y1; ++y ) for ( var x = s.rect.x0; x < s.rect.x1; ++x )
               total[y*W + x] += s.pixels[0][( y - s.rect.y0 )*rw + x - s.rect.x0];
         } );
         for ( var i = 0; i < total.length; ++i ) worst = Math.max( worst, Math.abs( total[i] - orig[i] ) );
         check( "sprites plus residual are the stars layer exactly (worst " + worst.toExponential( 1 ) + ")", worst < 1e-6, true );
      }
      finally { w.forceClose(); }
   } )();

   /*
    * One star removal on the image; the stars layer by unscreen. Screening
    * them back must give the image, or frame 0 cannot be exact. Runs with
    * whichever tool this installation has; skipped (not faked) without one.
    */
   if ( IN_PIXINSIGHT && Steps.availableStarTools().length > 0 ) ( function()
   {
      var path = synthFrame( synthDir( "fly-split" ) + "/field.xisf", { fwhm: 3, background: 0.05, noise: 0.002, seed: 4 } );
      var w = ImageWindow.open( path )[0], parts = null;
      try
      {
         parts = Sky.splitStars( w, Steps.availableStarTools()[0] );
         var n = w.mainView.image.width*w.mainView.image.height;
         var I = new Float32Array( n ), S = new Float32Array( n ), T = new Float32Array( n );
         w.mainView.image.getSamples( I ); parts.starless.mainView.image.getSamples( S ); parts.stars.mainView.image.getSamples( T );
         var worst = 0;
         for ( var i = 0; i < n; ++i ) worst = Math.max( worst, Math.abs( 1 - ( 1 - S[i] )*( 1 - T[i] ) - I[i] ) );
         check( "screen( starless, stars ) gives the image back (worst " + worst.toExponential( 2 ) + ")", worst <= 1/65535, true );
      }
      finally
      {
         w.forceClose();
         if ( parts ) { parts.starless.forceClose(); parts.stars.forceClose(); }
      }
   } )();

   /*
    * The StarDetector in scope in PixInsight 1.9 returns position, flux and
    * area only -- no rect, no count of maxima (probed 2026-09-23) -- and it
    * merges a pair 3 px apart into one detection. So the footprint comes
    * from the area, and a merged double is caught from the catalogue: a
    * second Gaia source of comparable brightness inside the core.
    */
   ( function()
   {
      check( "footprint from the detected area: a square of half-width sqrt(area/pi)",
             Fly.detectionRect( 10.4, 20.6, 28.3 ), { x0: 7, y0: 17, x1: 14, y1: 24 } );
      check( "a tiny detection still gets a pixel either side",
             Fly.detectionRect( 5.5, 5.5, 0 ), { x0: 4, y0: 4, x1: 7, y1: 7 } );
      function det( x, y ) { return { x: x, y: y, nmax: 0, rect: Fly.detectionRect( x, y, 12 ) }; }
      /*
       * Of a pair, only the FAINTER star is a blend: the brighter one moves
       * and carries the other. The G 5.6 star of a real field had a Gaia
       * companion buried in its glow, and as an equal pair neither moved.
       */
      var src = { G: 10 }, twin = { G: 9.8 }, faint = { G: 14 };
      var P = { source: src, d: 100, x: 20, y: 20 };
      var twinNear = [ { source: src, x: 20, y: 20 }, { source: twin, x: 21.5, y: 20 } ];
      check( "a brighter catalogue star in the core makes this one the blend",
             Fly.assignSprites( [ det( 20, 20 ) ], [ P ], 100, 100, 3, twinNear ).blended, 1 );
      var faintNear = [ { source: src, x: 20, y: 20 }, { source: faint, x: 21.5, y: 20 } ];
      check( "a much fainter one does not",
             Fly.assignSprites( [ det( 20, 20 ) ], [ P ], 100, 100, 3, faintNear ).sprites.length, 1 );
      check( "a catalogue star outside the core does not",
             Fly.assignSprites( [ det( 20, 20 ) ], [ P ], 100, 100, 3,
                                [ { source: twin, x: 30, y: 20 } ] ).sprites.length, 1 );
   } )();

   /* ---- Render: resampling and sprite drawing (pure) ------------------- */
   ( function()
   {
      function near( a, b, e ) { return Math.abs( a - b ) <= ( e || 1e-9 ); }
      [ "bilinear", "bicubic" ].forEach( function( kernel )
      {
         var ax = Render.axisWeights( 6, 0, 6, 2.3, 1, 6, kernel ), ok = true;
         for ( var u = 0; u < 6; ++u )
            for ( var j = ax.start[u]; j < ax.start[u + 1]; ++j )
            {
               var wgt = ax.w[j], idx = ax.idx[j];
               if ( !( near( wgt, idx == u ? 1 : 0, 1e-12 ) || wgt == 0 ) ) ok = false;
            }
         check( kernel + ": no zoom and the full crop is the identity", ok, true );

         var w = 20, h = 3, ramp = new Float32Array( w*h );
         for ( var y = 0; y < h; ++y ) for ( var x = 0; x < w; ++x ) ramp[y*w + x] = x/100;
         var K = 2, tp = 9.5;
         var out = Render.resample( ramp, w, h, Render.axisWeights( w, 0, w, tp, K, w, kernel ),
                                    Render.axisWeights( h, 0, h, 1, K, h, kernel ) );
         check( kernel + ": a zoom of 2 about the target maps u to tp + (u - tp)/2",
                near( out[1*w + 4], ( tp + ( 4 - tp )/2 )/100, 1e-6 ) && near( out[1*w + 15], ( tp + ( 15 - tp )/2 )/100, 1e-6 ), true );
      } );
      var edge = Render.axisWeights( 5, 0, 5, 0, 1.5, 5, "bicubic" ), inside = true;
      for ( var i = 0; i < edge.idx.length; ++i ) if ( edge.idx[i] < 0 || edge.idx[i] > 4 ) inside = false;
      check( "sample indices stay inside the image", inside, true );

      // a 3x3 sprite at (5,5) in a 12x12 frame, identity camera
      var patch = new Float32Array( [ 0, 0.1, 0, 0.1, 0.5, 0.1, 0, 0.1, 0 ] );
      var sp = { rect: { x0: 4, y0: 4, x1: 7, y1: 7 }, det: { x: 5, y: 5 } };
      var acc = new Float32Array( 144 ), cam = { x: 0, y: 0, fx: 1, fy: 1 };
      Render.drawSprite( acc, 12, 12, patch, sp, 5, 5, 1, 1, cam );
      check( "unmoved, unscaled: the sprite lands on its own pixels", [ acc[5*12 + 5], acc[4*12 + 5], acc[5*12 + 6] ], [ patch[4], patch[1], patch[5] ] );
      acc.fill( 0 );
      Render.drawSprite( acc, 12, 12, patch, sp, 7, 5, 1, 2, cam );
      check( "moved by the projection's shift, scaled by the pixel factor", near( acc[5*12 + 7], 1.0, 1e-6 ), true );
      acc.fill( 0 );
      Render.drawSprite( acc, 12, 12, patch, sp, 5, 5, 2, 1, cam );
      var sum = 0; for ( i = 0; i < acc.length; ++i ) sum += acc[i];
      check( "grown by g: its drawn light scales by g squared", near( sum, 0.9*4, 0.05 ), true );
   } )();

   /* ---- Render: compositing on a generated field (PixInsight) ---------- */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var dir = synthDir( "fly-render" ), T0 = { ra: 324.745, dec: 57.514 };
      var o = { fwhm: 3, background: 0.05, noise: 0.002, seed: 5, stars: 150 };
      var Iwin = ImageWindow.open( synthFrame( dir + "/image.xisf", o ) )[0];
      var Swin = ImageWindow.open( synthFrame( dir + "/starless.xisf", { fwhm: 3, background: 0.05, noise: 0.002, seed: 5, stars: 0 } ) )[0];
      var Twin = null;
      function channel( img ) { var b = new Float32Array( img.width*img.height ); img.getSamples( b ); return b; }
      function maxAbsDiff( a, b ) { var m = 0; for ( var i = 0; i < a.length; ++i ) m = Math.max( m, Math.abs( a[i] - b[i] ) ); return m; }
      try
      {
         withTanKeywords( Iwin, T0.ra, T0.dec, 0.0005 );
         Twin = Sky.copyWindow( Iwin, "fly_T" );
         Steps.deriveStarsByUnscreen( Twin, Swin );
         var I = Iwin.mainView.image, W = I.width, H = I.height, full = { x: 0, y: 0, w: W, h: H };
         var proj = Sky.projector( Iwin ), wcs = Sky.keywordWcs( Iwin );
         var dets = Sky.detections( Twin.mainView.image ).slice( 0, 30 );
         var sources = dets.map( function( d, i )
         {
            var c = Fly.tanUnproject( d.x, d.y, wcs );
            return { ra: c.ra, dec: c.dec, plx: 1000/( 200 + 60*i ) - Fly.PARALLAX_ZERO_POINT, pmra: 0, pmdec: 0, G: 12 + i*0.01 };
         } );
         var placed = Fly.placeStars( sources, proj, W, H );
         var sp = Sky.sprites( Twin.mainView.image, placed, 3, placed );
         check( "the render scene has sprites", sp.sprites.length >= 20, true );
         var args = { starless: Swin.mainView.image, stars: Twin.mainView.image, sprites: sp.sprites,
                      residual: sp.residual, project: proj, target: T0, D: 900 };
         var scene = Render.scene( args );
         var opts = { travel: 150, easing: "linear", growth: 0.15, brightening: true };

         var f0 = Render.frame( scene, 0, opts, W, H, full );
         var d0 = maxAbsDiff( channel( f0 ), channel( I ) );
         check( "frame 0 equals the input within 1 in 16 bits (worst " + d0.toExponential( 2 ) + ")", d0 <= 1/65535, true );

         // the nearest star whose moved position stays well inside the frame
         var near = null, m, ex, ey;
         sp.sprites.slice().sort( function( a, b ) { return a.d - b.d; } ).some( function( c )
         {
            var mm = Fly.moved( c.source.ra, c.source.dec, c.d, T0, opts.travel );
            var p1 = proj( mm.ra, mm.dec ), p0 = proj( c.source.ra, c.source.dec );
            var x = c.det.x + p1.x - p0.x, y = c.det.y + p1.y - p0.y;
            if ( x < 10 || y < 10 || x > W - 10 || y > H - 10 || Math.hypot( x - c.det.x, y - c.det.y ) < 3 )
               return false;
            near = c; m = mm; ex = x; ey = y;
            return true;
         } );
         check( "a nearby star moves visibly and stays in frame", near != null, true );
         var fT = channel( Render.frame( scene, 1, opts, W, H, full ) ), best = -1, bx = 0, by = 0;
         for ( var y = Math.round( ey ) - 5; y <= Math.round( ey ) + 5; ++y )
            for ( var x = Math.round( ex ) - 5; x <= Math.round( ex ) + 5; ++x )
               if ( x >= 0 && y >= 0 && x < W && y < H && fT[y*W + x] > best ) { best = fT[y*W + x]; bx = x; by = y; }
         check( "the nearest star moves to its exact projection",
                Math.abs( bx - ex ) <= 1.5 && Math.abs( by - ey ) <= 1.5, true );

         var ones = new Uint8Array( W*H ); ones.fill( 1 );
         var gscene = Render.scene( { starless: args.starless, stars: args.stars, sprites: [], residualMask: ones,
                                      project: proj, target: T0, D: Infinity } );
         var dg = maxAbsDiff( channel( Render.frame( gscene, 1, opts, W, H, full ) ), channel( I ) );
         check( "a galaxy's backdrop does not move (worst " + dg.toExponential( 2 ) + ")", dg <= 1/65535, true );

         var past = { travel: near.d + 50, easing: "linear", growth: 0.15, brightening: true };
         var without = Render.scene( { starless: args.starless, stars: args.stars,
                                       sprites: sp.sprites.filter( function( s ) { return s !== near; } ),
                                       residual: sp.residual, project: proj, target: T0, D: 900 } );
         check( "a star the camera has passed is never drawn",
                maxAbsDiff( channel( Render.frame( scene, 1, past, W, H, full ) ),
                            channel( Render.frame( without, 1, past, W, H, full ) ) ), 0 );

         Render.writeTiff( f0, dir + "/f0.tif" );
         var back = ImageWindow.open( dir + "/f0.tif" )[0];
         check( "frames are 16-bit TIFF", back.mainView.image.bitsPerSample, 16 );
         check( "and read back as written", maxAbsDiff( channel( back.mainView.image ), channel( f0 ) ) <= 1/65535, true );
         back.forceClose();
      }
      finally
      {
         Iwin.forceClose(); Swin.forceClose();
         if ( Twin ) Twin.forceClose();
      }
   } )();

   /*
    * What a moving star leaves behind. The sprite is the star's footprint in
    * the stars layer; anything of the star outside it stays in the residual
    * and zooms with the backdrop -- a ghost halo where the star used to be.
    * Measured on Moffat stars (beta 2.5: real stars' wide wings).
    */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var dir = synthDir( "fly-halo" ), T0 = { ra: 324.745, dec: 57.514 };
      var base = { fwhm: 3, background: 0.05, noise: 0.002, seed: 6, moffat: 2.5, flux: 3 };
      var Iwin = ImageWindow.open( synthFrame( dir + "/image.xisf", Object.assign( { stars: 15 }, base ) ) )[0];
      var Swin = ImageWindow.open( synthFrame( dir + "/starless.xisf", Object.assign( { stars: 0 }, base ) ) )[0];
      var Twin = null;
      try
      {
         withTanKeywords( Iwin, T0.ra, T0.dec, 0.0005 );
         Twin = Sky.copyWindow( Iwin, "fly_halo_T" );
         Steps.deriveStarsByUnscreen( Twin, Swin );
         var T = Twin.mainView.image, W = T.width, H = T.height, wcs = Sky.keywordWcs( Iwin ), proj = Sky.projector( Iwin );
         var tb = new Float32Array( W*H ); T.getSamples( tb );
         var dets = Sky.detections( T ).slice( 0, 15 );
         var placed = Fly.placeStars( dets.map( function( d ) { var c = Fly.tanUnproject( d.x, d.y, wcs );
                         return { ra: c.ra, dec: c.dec, plx: 3, G: 10 }; } ), proj, W, H );
         var sp = Sky.sprites( T, placed, 3, placed );
         /*
          * What a moving star leaves behind, two ways. VISIBLE: inside its old
          * footprint the residual must be flat -- nothing more than 0.2% of the
          * star's peak above the level it stood on (0.05% of full scale is far
          * below one 8-bit step). INTEGRATED: the light left within 36 px,
          * which since footprints stand on their background includes that
          * flat level over the disc (a few % of a synthetic star's light),
          * must stay under 10%, so a real leak still fails.
          */
         var worst = 0, bump = 0;
         sp.sprites.forEach( function( s )
         {
            var inside = 0, left = 0, R = 36, peak = tb[Math.round( s.det.y )*W + Math.round( s.det.x )], b = s.det.halo.pedestal;
            for ( var y = Math.max( 0, Math.floor( s.det.y - R ) ); y <= Math.min( H - 1, s.det.y + R ); ++y )
               for ( var x = Math.max( 0, Math.floor( s.det.x - R ) ); x <= Math.min( W - 1, s.det.x + R ); ++x )
               {
                  var d = Math.hypot( x - s.det.x, y - s.det.y );
                  if ( d > R ) continue;
                  if ( x >= s.rect.x0 && x < s.rect.x1 && y >= s.rect.y0 && y < s.rect.y1 )
                     inside += s.pixels[0][( y - s.rect.y0 )*( s.rect.x1 - s.rect.x0 ) + x - s.rect.x0];
                  if ( dets.some( function( e ) { return e !== s.det && Math.hypot( x - e.x, y - e.y ) < 2*s.det.halo.outer; } ) )
                     continue;                        // near another star: its light, not this one's
                  left += sp.residual[0][y*W + x];
                  if ( d < s.det.halo.radius ) bump = Math.max( bump, ( sp.residual[0][y*W + x] - b )/peak );
               }
            worst = Math.max( worst, left/( inside + left ) );
         } );
         check( "nothing visible is left where a star was (worst " + ( 100*bump ).toFixed( 3 ) + "% of its peak)", bump <= 0.002, true );
         check( "and at most 10% of its light, the flat level it stood on included (worst " +
                ( 100*worst ).toFixed( 1 ) + "% over " + sp.sprites.length + " sprites)", worst <= 0.10, true );
      }
      finally { Iwin.forceClose(); Swin.forceClose(); if ( Twin ) Twin.forceClose(); }
   } )();

   /*
    * Halo-sized footprints. The radius grows ring by ring until the ring's
    * mean falls below max( noise, 0.2% of the peak ); fainter stars inside
    * a bright star's halo are left in the backdrop rather than making it a
    * blend; brighter stars own contested pixels.
    */
   ( function()
   {
      var prof = [ 1, 0.5, 0.2, 0.05, 0.01, 0.003, 0.0015, 0.001, 0.0004 ];
      check( "halo radius: first ring under 0.05% of the peak", Fly.haloRadius( prof, 1, 0, 2 ), 8 );
      check( "halo radius: or under the noise, whichever is higher", Fly.haloRadius( prof, 1, 0.02, 2 ), 4 );
      check( "halo radius: never below the core", Fly.haloRadius( prof, 1, 0.5, 3 ), 3 );
      check( "halo radius: capped at the profile's length", Fly.haloRadius( [ 1, 1, 1 ], 1, 0, 1 ), 3 );

      function det( x, y, flux ) { return { x: x, y: y, nmax: 0, flux: flux, rect: Fly.detectionRect( x, y, 5 ) }; }
      var bright = det( 30, 30, 10 ), faint = det( 36, 30, 1 ), twin = det( 60, 30, 10 ), twin2 = det( 66, 30, 6 );
      var dets = [ bright, faint, twin, twin2 ];
      var P = function( x, y ) { return { source: {}, d: 100, x: x, y: y }; };
      var halo = function() { return 10; };
      var a = Fly.assignSprites( dets, [ P( 30, 30 ), P( 60, 30 ) ], 100, 100, 3, [], halo );
      check( "a faint star inside a bright star's halo does not make it a blend",
             a.sprites.some( function( s ) { return s.det === bright; } ), true );
      /*
       * A faint star inside a bright star's reach moves WITH it. Leaving
       * its core behind as a hole tore the bright star apart once it was
       * magnified near the camera (seen on a real render).
       */
      check( "a faint star inside a bright star's reach moves with it",
             a.owner[30*100 + 36], a.sprites.map( function( s ) { return s.det; } ).indexOf( bright ) );
      check( "the bright star's halo around it moves with the bright star",
             a.owner[30*100 + 39], a.sprites.map( function( s ) { return s.det; } ).indexOf( bright ) );
      /*
       * A real image showed blends outnumbering placed stars (940 vs 766):
       * a bright star's halo reaches its neighbours, and treating any
       * comparable star inside the halo as a blend kept the brightest
       * stars still. A blend is a comparable star near the CORE (within
       * one FWHM of it); in the halo, a neighbour just stays behind.
       */
      check( "a comparable neighbour in the halo, away from the core, does not make a blend",
             a.sprites.some( function( s ) { return s.det === twin; } ), true );
      check( "and that neighbour's pixels go with the star whose reach they are in",
             a.owner[30*100 + 66], a.sprites.map( function( s ) { return s.det; } ).indexOf( twin ) );
      var close = Fly.assignSprites( [ det( 30, 30, 10 ), det( 33, 30, 12 ) ], [ P( 30, 30 ) ], 100, 100, 3, [], halo );
      check( "a brighter star within a FWHM of the core makes this one a blend", close.blended, 1 );
      var lead = Fly.assignSprites( [ det( 30, 30, 10 ), det( 33, 30, 8 ) ], [ P( 30, 30 ) ], 100, 100, 3, [], halo );
      check( "a fainter one does not: the brighter star of a pair moves", lead.sprites.length, 1 );
      var bigPair = [ { x: 60, y: 60, nmax: 0, flux: 168, rect: Fly.detectionRect( 60, 60, 1400 ) },
                      { x: 72, y: 65, nmax: 0, flux: 161, rect: Fly.detectionRect( 72, 65, 1400 ) } ];
      var gA = { G: 5.6 }, gB = { G: 8.0 };        // the same source objects, as in a real run
      var bp = Fly.assignSprites( bigPair, [ { source: gA, d: 700, x: 60, y: 60 }, { source: gB, d: 900, x: 72, y: 65 } ],
                                  140, 140, 3, [ { source: gA, x: 60, y: 60 }, { source: gB, x: 72, y: 65 } ],
                                  function( d ) { var r = d === bigPair[0] ? 40 : 21; d.halo = { radius: r, halo: r, pedestal: 0, feather: 4, outer: r + 4 }; return r + 4; } );
      check( "a saturated star with a Gaia companion in its glow moves (the companion goes with it)",
             bp.sprites.length == 1 && bp.sprites[0].det === bigPair[0], true );
      check( "the footprint is the halo radius around the centre",
             a.sprites[0].rect, { x0: 20, y0: 20, x1: 41, y1: 41 } );
   } )();

   /*
    * Render time: an 8 MP backdrop with ~2000 sprites, 4K output, RGB (the
    * mono generated channel used three times). Always passes when frames
    * render; the number goes to fly-benchmark.txt and verified-parameters.md.
    */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var dir = synthDir( "fly-bench" ), T0 = { ra: 324.745, dec: 57.514 };
      var base = { width: 3464, height: 2309, fwhm: 3, background: 0.05, noise: 0.002, seed: 8 };
      var Iwin = ImageWindow.open( synthFrame( dir + "/image.xisf", Object.assign( { stars: 3000 }, base ) ) )[0];
      var Swin = ImageWindow.open( synthFrame( dir + "/starless.xisf", Object.assign( { stars: 0 }, base ) ) )[0];
      var Twin = null;
      try
      {
         withTanKeywords( Iwin, T0.ra, T0.dec, 0.0005 );
         Twin = Sky.copyWindow( Iwin, "fly_bench_T" );
         Steps.deriveStarsByUnscreen( Twin, Swin );
         var T = Twin.mainView.image, W = T.width, H = T.height, wcs = Sky.keywordWcs( Iwin ), proj = Sky.projector( Iwin );
         var t0 = Date.now();
         var dets = Sky.detections( T ).slice( 0, 2000 );
         var placed = Fly.placeStars( dets.map( function( d, i ) { var c = Fly.tanUnproject( d.x, d.y, wcs );
                         return { ra: c.ra, dec: c.dec, plx: 1000/( 150 + i ), G: 12 }; } ), proj, W, H );
         var sp = Sky.sprites( T, placed, 3, placed );
         var tSprites = Date.now() - t0;
         var sc = Render.scene( { starless: Swin.mainView.image, stars: T, sprites: sp.sprites, residual: sp.residual,
                                  project: proj, target: T0, D: 900 } );
         sc.S = [ sc.S[0], sc.S[0], sc.S[0] ]; sc.R = [ sc.R[0], sc.R[0], sc.R[0] ]; sc.nc = 3;
         var tp = sc.tp, crop = Fly.presetCrop( W, H, tp.x, tp.y, 3840, 2160 );
         var ms = Render.benchmark( sc, 3840, 2160, crop, { travel: 180, easing: "smoothstep", growth: 0.15, brightening: true,
                                    output: Fly.outputTransform( Fly.SRGB_COLOUR, "sdr" ) }, 3 );
         var bopts = function( mode ) { return { travel: 180, easing: "smoothstep", growth: 0.15, brightening: true,
                                                 output: Fly.outputTransform( Fly.SRGB_COLOUR, mode, { peak: 1000 } ) }; };
         var msHlg = Render.benchmark( sc, 3840, 2160, crop, bopts( "hlg" ), 2 ), msPq = Render.benchmark( sc, 3840, 2160, crop, bopts( "pq" ), 2 );
         var line = "Fly-Through render: " + Math.round( ms ) + " ms/frame (SDR), " + Math.round( msHlg ) + " (HDR HLG), " +
                    Math.round( msPq ) + " (HDR PQ) at 3840x2160 RGB with colour conversion, " + sp.sprites.length +
                    " sprites on an 8 MP backdrop; sprite extraction " + tSprites + " ms";
         try { File.writeTextFile( "/tmp/agent-scratch/fly-benchmark.txt", line + "\n" ); } catch ( e ) {}
         check( line, ms > 0, true );
      }
      finally { Iwin.forceClose(); Swin.forceClose(); if ( Twin ) Twin.forceClose(); }
   } )();

   ( function()
   {
      var enc = " V....D libx264  H.264\n V....D libx265  H.265\n V....D prores_ks ProRes\n";
      var av = Fly.availableFormats( enc );
      check( "only what this ffmpeg can encode", av.map( function( f ) { return f.id; } ), [ "h264", "hevc", "prores" ] );
      check( "4K defaults to HEVC", Fly.defaultFormat( av, 3840 ), "hevc" );
      check( "1080p defaults to H.264", Fly.defaultFormat( av, 1920 ), "h264" );
      check( "no HEVC: 4K falls back to H.264",
             Fly.defaultFormat( Fly.availableFormats( " libx264 " ), 3840 ), "h264" );
      var a = Fly.ffmpegArgs( "/f", 25, "/o/youtube", "h264", "high" );
      check( "frames pattern and rate", a.slice( 0, 5 ), [ "-y", "-framerate", "25", "-i", "/f/frame_%05d.tif" ] );
      check( "H.264 high is CRF 18", a.join( " " ).indexOf( "-c:v libx264 -crf 18" ) >= 0, true );
      check( "output gets the format's extension", a[a.length - 1], "/o/youtube.mp4" );
      check( "HEVC is tagged for Apple players",
             Fly.ffmpegArgs( "/f", 30, "/o/x", "hevc", "standard" ).join( " " ).indexOf( "-tag:v hvc1" ) >= 0, true );
      var pr = Fly.ffmpegArgs( "/f", 30, "/o/x", "prores", "high" ).join( " " );
      check( "ProRes 422 HQ, 10-bit 4:2:2, .mov", /prores_ks -profile:v 3 .*yuv422p10le.* \/o\/x\.mov$/.test( pr ), true );
      check( "VP9 standard CRF", Fly.ffmpegArgs( "/f", 30, "/o/x", "vp9", "standard" ).join( " " ).indexOf( "-crf 32 -b:v 0" ) >= 0, true );
      /*
       * ffmpeg 9 takes colour tags from the frames, not only from the output
       * flags (probed: flags alone gave primaries "unknown"), and converts
       * RGB to YUV with BT.601 unless told. So the frames are converted with
       * the BT.709 matrix and tagged BT.709 in the filter chain, every format.
       */
      [ "h264", "hevc", "prores", "vp9" ].forEach( function( id )
      {
         var args = Fly.ffmpegArgs( "/f", 30, "/o/x", id, "high" ), vf = args.indexOf( "-vf" );
         check( id + ": frames converted and tagged as Rec.709", vf > 0 && args[vf + 1] ==
                "scale=out_color_matrix=bt709:out_range=tv,setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv", true );
      } );
   } )();

   ( function()
   {
      var mac = Fly.ffmpegCandidates( "macos", "/Users/u", "/usr/bin:/opt/x/bin:", "" );
      check( "macOS order: Homebrew, /usr/local, /usr/bin, then PATH (deduplicated, empty entries dropped)", mac,
             [ "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg", "/opt/x/bin/ffmpeg" ] );
      check( "a saved path is tried first", Fly.ffmpegCandidates( "macos", "/Users/u", "", "/s/ffmpeg" )[0], "/s/ffmpeg" );

      var win = Fly.ffmpegCandidates( "windows", "C:/Users/u",
                   "C:\\Windows\\system32;\"C:\\Tools\\ffmpeg\\bin\";;C:\\Tools\\ffmpeg\\bin\\", "" );
      check( "winget link first", win[0], "C:/Users/u/AppData/Local/Microsoft/WinGet/Links/ffmpeg.exe" );
      check( "Windows fixed locations, in order", win.slice( 1, 5 ),
             [ "C:/ProgramData/chocolatey/bin/ffmpeg.exe", "C:/Users/u/scoop/shims/ffmpeg.exe",
               "C:/ffmpeg/bin/ffmpeg.exe", "C:/Program Files/ffmpeg/bin/ffmpeg.exe" ] );
      check( "PATH on Windows: split on ';', quotes and trailing slash stripped, '/' separators, .exe, no duplicates",
             win.slice( 5 ), [ "C:/Windows/system32/ffmpeg.exe", "C:/Tools/ffmpeg/bin/ffmpeg.exe" ] );
      check( "Windows without a home skips the per-user locations",
             Fly.ffmpegCandidates( "windows", "", "", "" )[0], "C:/ProgramData/chocolatey/bin/ffmpeg.exe" );

      check( "install hint, Windows", Fly.ffmpegInstallHint( "windows" ).indexOf( "winget install Gyan.FFmpeg" ) >= 0, true );
      check( "install hint, macOS", Fly.ffmpegInstallHint( "macos" ).indexOf( "brew install ffmpeg" ) >= 0, true );

      check( "shown command, Windows: double quotes around spaces",
             Fly.commandLine( "C:/Program Files/ffmpeg/bin/ffmpeg.exe", [ "-i", "D:/My Frames/frame_%05d.tif" ], "windows" ),
             "\"C:/Program Files/ffmpeg/bin/ffmpeg.exe\" -i \"D:/My Frames/frame_%05d.tif\"" );
      check( "shown command, macOS: single quotes, bare when safe",
             Fly.commandLine( "/opt/homebrew/bin/ffmpeg", [ "-y", "/Volumes/My Disk/x.mp4" ], "macos" ),
             "/opt/homebrew/bin/ffmpeg -y '/Volumes/My Disk/x.mp4'" );
   } )();

   /*
    * The discovered ffmpeg: only the contract, so the suite passes whether
    * or not this machine has one installed.
    */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var ff = Render.findFfmpeg();
      check( "ffmpeg is either not found or answers -version",
             ff == null || /^ffmpeg version/.test( Render.runProcess( ff, [ "-version" ], 10000 ).output ), true );
      if ( ff != null )
         check( "and its encoder list names at least one encoder", /encoders/i.test( Render.ffmpegEncoders( ff ) ), true );
   } )();

   /* ---- Fly-Through: draft timing, final render, cancel ---------------- */
   check( "due frame by wall clock", Render.dueFrame( 1000, 1250, 10, 4 ), 2 );
   check( "late ticks drop frames, not slow the clip", Render.dueFrame( 1000, 1390, 10, 4 ), 3 );
   check( "and it loops", Render.dueFrame( 1000, 1450, 10, 4 ), 0 );
   check( "no frames, nothing due", Render.dueFrame( 1000, 1450, 10, 0 ), 0 );
   check( "a preset key resolves to its size and loop mode",
          Fly.presetSpec( "exhibition" ), { id: "exhibition", preset: "exhibition", w: 3840, h: 2160, pingPong: true, crossfade: false } );
   check( "social vertical is not a loop",
          Fly.presetSpec( "social_vertical" ), { id: "social_vertical", preset: "social_vertical", w: 1080, h: 1920, pingPong: false, crossfade: false } );
   check( "only our own frame files are cleared from a preset folder",
          [ "frame_00000.tif", "frame_12345.tif", "notes.txt", "frame_1.tif", "frame_00001.tif.bak" ]
             .filter( Fly.isFrameFile ), [ "frame_00000.tif", "frame_12345.tif" ] );

   if ( IN_PIXINSIGHT ) ( function()
   {
      var dir = synthDir( "fly-final" ), fx = flyTestScene( dir );
      try
      {
         var small = { id: "small", w: 160, h: 90, pingPong: false };
         var opts = { travel: 150, easing: "smoothstep", growth: 0.15, brightening: true, duration: 0.5, fps: 10, video: false };
         if ( !File.directoryExists( dir + "/small" ) ) File.createDirectory( dir + "/small", true );
         File.writeTextFile( dir + "/small/frame_00099.tif", "stale" );
         var res = FlyThrough.renderFinal( fx.scene, [ small ], opts, dir, {} );
         check( "every frame of the clip is written", res.written, 5 );
         check( "frame files are numbered from zero", File.exists( dir + "/small/frame_00004.tif" ), true );
         check( "a stale frame from an earlier render is gone", File.exists( dir + "/small/frame_00099.tif" ), false );
         check( "without video, the exact ffmpeg command is given",
                res.commands.length == 1 && res.commands[0].indexOf( dir + "/small/frame_%05d.tif" ) >= 0, true );

         var cut = FlyThrough.renderFinal( fx.scene, [ "youtube_1080" ], { travel: 150, easing: "linear", growth: 0.15,
                      brightening: true, duration: 1, fps: 10, video: true, ffmpeg: "/nonexistent/ffmpeg", format: "h264",
                      quality: "high" }, dir, { cancelAfter: 3 } );
         check( "cancel keeps the frames written", [ cut.written, cut.cancelled ], [ 3, true ] );
         check( "and makes no video", File.exists( dir + "/youtube_1080.mp4" ), false );
      }
      finally { fx.windows.forEach( function( w ) { w.forceClose(); } ); }
   } )();

   /*
    * The whole chain on a generated image: identify (target, distance) and
    * build (split, sprites, scene), with Gaia replaced by the detected
    * stars. Skipped without a star tool; never faked.
    */
   if ( IN_PIXINSIGHT && Steps.availableStarTools().length > 0 ) ( function()
   {
      var dir = synthDir( "fly-chain" ), fx = flyTestScene( dir ), saved = Sky.querySources, built = null;
      try
      {
         Sky.querySources = function() { return fx.sources; };
         var id = FlyThrough.identify( fx.image, {} );
         check( "identify: the field's NGC/IC target is IC 1396", id.target && id.target.id, "IC1396" );
         check( "identify: a nebula with no cluster in the sources needs a typed distance", [ id.type, id.D ], [ "nebula", null ] );
         var typed = FlyThrough.identify( fx.image, { distance: 900 } );
         check( "identify: a typed distance is used", [ typed.D, typed.distanceSource ], [ 900, "typed" ] );
         var gal = FlyThrough.identify( fx.image, { type: "galaxy" } );
         check( "identify: a galaxy is at infinity", gal.D, Infinity );
         built = FlyThrough.build( fx.image, typed, { tool: Steps.availableStarTools()[0] } );
         check( "build: stars are placed as sprites", built.counts.placed > 10, true );
         var f0 = Render.frame( built.scene, 0, { travel: 100, easing: "linear", growth: 0.15, brightening: true },
                                fx.W, fx.H, { x: 0, y: 0, w: fx.W, h: fx.H } );
         var a = new Float32Array( fx.W*fx.H ), b = new Float32Array( fx.W*fx.H ), worst = 0;
         f0.getSamples( a ); fx.image.mainView.image.getSamples( b );
         for ( var i = 0; i < a.length; ++i ) worst = Math.max( worst, Math.abs( a[i] - b[i] ) );
         check( "build: frame 0 of the built scene is the image (worst " + worst.toExponential( 2 ) + ")", worst <= 1/65535, true );
      }
      finally
      {
         Sky.querySources = saved;
         fx.windows.forEach( function( w ) { w.forceClose(); } );
         if ( built ) built.windows.forEach( function( w ) { w.forceClose(); } );
      }
   } )();

   /*
    * End to end, when this machine has an ffmpeg: a short clip rendered and
    * encoded in every format that ffmpeg offers. Skipped, not faked, when
    * there is none.
    */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var ff = Render.findFfmpeg();
      if ( ff == null )
         return;
      var dir = synthDir( "fly-video" ), fx = flyTestScene( dir );
      try
      {
         var formats = Fly.availableFormats( Render.ffmpegEncoders( ff ) );
         formats.forEach( function( f )
         {
            var opts = { travel: 150, easing: "smoothstep", growth: 0.15, brightening: true, duration: 0.5, fps: 10,
                         video: true, ffmpeg: ff, format: f.id, quality: "standard" };
            var res = FlyThrough.renderFinal( fx.scene, [ { id: "clip_" + f.id, w: 320, h: 180, pingPong: false } ], opts, dir, {} );
            check( "a " + f.label + " video is made" + ( res.failed.length ? ": " + String( res.failed[0].output ).slice( -300 ) : "" ),
                   res.videos.length == 1 && File.exists( dir + "/clip_" + f.id + "." + f.ext ), true );
            var probe = File.extractDirectory( ff ) + "/ffprobe" + ( Util.isWindows() ? ".exe" : "" );
            if ( File.exists( probe ) )
               check( "the " + f.label + " video is tagged Rec.709",
                      String( Render.runProcess( probe, [ "-v", "error", "-show_entries",
                         "stream=color_space,color_transfer,color_primaries", "-of", "csv=p=0",
                         dir + "/clip_" + f.id + "." + f.ext ], 10000 ).output ).trim(), "bt709,bt709,bt709" );
         } );
      }
      finally { fx.windows.forEach( function( w ) { w.forceClose(); } ); }
   } )();

   /* ---- Fly: colour management (pure) ---------------------------------- */
   ( function()
   {
      function near( a, b, e ) { return Math.abs( a - b ) <= e; }
      function near3( a, b, e ) { return near( a[0], b[0], e ) && near( a[1], b[1], e ) && near( a[2], b[2], e ); }
      var PROPHOTO = [ 0.7976749, 0.1351917, 0.0313534, 0.2880402, 0.7118741, 0.0000857, 0, 0, 0.8252100 ];
      var srgbCurve = Fly.SRGB_COLOUR.trc[0];
      check( "sRGB decode at 0.5 is 0.214041 (IEC 61966-2-1)", near( Fly.trcDecode( srgbCurve, 0.5 ), 0.214041, 1e-6 ), true );
      var p3 = { type: "para", fn: 3, p: [ 2.4, 1/1.055, 0.055/1.055, 1/12.92, 0.04045 ] }, ok = true;
      [ 0, 0.02, 0.04045, 0.3, 1 ].forEach( function( x )
      {
         var want = x >= 0.04045 ? Math.pow( ( x + 0.055 )/1.055, 2.4 ) : x/12.92;
         if ( !near( Fly.trcDecode( p3, x ), want, 1e-9 ) ) ok = false;
      } );
      check( "a parametric type-3 curve follows its formula", ok, true );
      check( "a gamma curve", near( Fly.trcDecode( { type: "gamma", g: 1.8 }, 0.5 ), Math.pow( 0.5, 1.8 ), 1e-12 ), true );
      check( "a table curve interpolates", near( Fly.trcDecode( { type: "table", t: [ 0, 0.25, 1 ] }, 0.75 ), 0.625, 1e-12 ), true );

      var M709 = Fly.sourceToTarget( PROPHOTO, "rec709" ), M2020 = Fly.sourceToTarget( PROPHOTO, "rec2020" );
      check( "ProPhoto white stays white in Rec.709 (Bradford D50 to D65)", near3( Fly.mul3( M709, [ 1, 1, 1 ] ), [ 1, 1, 1 ], 2e-3 ), true );
      check( "and in BT.2020", near3( Fly.mul3( M2020, [ 1, 1, 1 ] ), [ 1, 1, 1 ], 2e-3 ), true );
      check( "ProPhoto's green lies outside Rec.709: the matrix is really applied", Fly.mul3( M709, [ 0, 1, 0 ] )[0] < 0, true );
      check( "sRGB's own primaries map to Rec.709 unchanged",
             near3( Fly.mul3( Fly.sourceToTarget( Fly.SRGB_COLOUR.matrix, "rec709" ), [ 1, 0, 0 ] ), [ 1, 0, 0 ], 2e-3 ), true );

      // a constructed v2 profile: rXYZ/gXYZ/bXYZ and a gamma-1.8 curv on all three
      function be32( n ) { return [ ( n >>> 24 ) & 255, ( n >>> 16 ) & 255, ( n >>> 8 ) & 255, n & 255 ]; }
      function ascii( t ) { return t.split( "" ).map( function( c ) { return c.charCodeAt( 0 ); } ); }
      function s15( v ) { return be32( Math.round( v*65536 ) >>> 0 ); }
      function xyz( a, b, c ) { return ascii( "XYZ " ).concat( [ 0, 0, 0, 0 ], s15( a ), s15( b ), s15( c ) ); }
      var curv = ascii( "curv" ).concat( [ 0, 0, 0, 0 ], be32( 1 ), [ 1, 204, 0, 0 ] );   // 1.8 = 0x01CC (u8Fixed8), padded
      var tags = [ [ "rXYZ", xyz( 0.7976749, 0.2880402, 0 ) ], [ "gXYZ", xyz( 0.1351917, 0.7118741, 0 ) ],
                   [ "bXYZ", xyz( 0.0313534, 0.0000857, 0.82521 ) ], [ "rTRC", curv ], [ "gTRC", curv ], [ "bTRC", curv ] ];
      var head = []; for ( var i = 0; i < 128; ++i ) head.push( 0 );
      ascii( "mntrRGB " ).forEach( function( b, i ) { head[12 + i] = b; } );
      var table = be32( tags.length ), body = [], off = 128 + 4 + 12*tags.length;
      tags.forEach( function( t ) { table = table.concat( ascii( t[0] ), be32( off + body.length ), be32( t[1].length ) ); body = body.concat( t[1] ); } );
      var bytes = head.concat( table, body );
      var col = Fly.parseIccColour( function( i ) { return bytes[i]; }, bytes.length );
      check( "a matrix/curve profile is read: its matrix", col != null && near3( [ col.matrix[0], col.matrix[3], col.matrix[6] ], [ 0.7976749, 0.2880402, 0 ], 1e-4 ), true );
      check( "and its curves", col != null && near( Fly.trcDecode( col.trc[1], 0.5 ), Math.pow( 0.5, 1.8 ), 1e-3 ), true );
      check( "a profile without the matrix tags is not a matrix profile",
             Fly.parseIccColour( function( i ) { return head.concat( be32( 0 ) )[i]; }, 132 ), null );

      var t = Fly.outputTransform( Fly.SRGB_COLOUR, "sdr" ), worst = 0;
      for ( var v = 0; v <= 1; v += 1/257 )
      {
         var o = t.pixel( v, v*0.5, 1 - v );
         worst = Math.max( worst, Math.abs( o[0] - v ), Math.abs( o[1] - v*0.5 ), Math.abs( o[2] - ( 1 - v ) ) );
      }
      check( "SDR output of an sRGB image is the image (worst " + worst.toExponential( 1 ) + ")", worst <= 1/65535, true );
      var pp = Fly.outputTransform( { matrix: PROPHOTO, trc: [ { type: "gamma", g: 1.8 }, { type: "gamma", g: 1.8 }, { type: "gamma", g: 1.8 } ] }, "sdr" );
      check( "SDR output of ProPhoto white is white", near3( pp.pixel( 1, 1, 1 ), [ 1, 1, 1 ], 2e-3 ), true );
      check( "a saturated ProPhoto green is clipped into Rec.709, not wrapped", pp.pixel( 0, 1, 0 ).every( function( c ) { return c >= 0 && c <= 1; } ), true );
   } )();

   /* ---- Colour management in PixInsight -------------------------------- */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var dir = synthDir( "fly-colour" );
      function rgbWindow( id )
      {
         var w = new ImageWindow( 8, 8, 3, 32, true, true, Util.freeWindowId( id ) );
         w.mainView.beginProcess( UndoFlag_NoSwapFile ); w.mainView.image.fill( 0.5 ); w.mainView.endProcess();
         return w;
      }
      var plain = rgbWindow( "col_plain" );
      try
      {
         var c0 = Sky.colourOf( plain );
         // PixInsight shows an untagged image in its DEFAULT profile (a user
         // setting: ProPhoto on the maintainer's machine) and embeds that one
         // on save, so that is the profile its colours are read in
         var c0b = Sky.colourOf( plain );
         check( "an untagged image is read in PixInsight's default profile",
                c0.colour.matrix != null && JSON.stringify( c0.colour.matrix ) == JSON.stringify( c0b.colour.matrix ), true );
      }
      finally { plain.forceClose(); }

      // ROMM RGB is a macOS system profile: this part runs where it is installed
      var romm = rgbWindow( "col_romm" ), P = new AssignICCProfile;
      P.mode = 0; P.targetProfile = "ROMM RGB: ISO 22028-2:2013";
      var tagged = P.executeOn( romm.mainView );
      try
      {
         if ( tagged )
         {
            romm.saveAs( dir + "/romm.xisf", false, false, false, false );
            var c1 = Sky.colourOf( romm );
            check( "a ROMM-tagged image is read as ProPhoto primaries",
                   c1.colour.matrix != null && Math.abs( c1.colour.matrix[0] - 0.7977 ) < 2e-3, true );
            var o = Fly.outputTransform( c1.colour, "sdr" ).pixel( 0.2, 0.8, 0.2 );
            check( "and its greens are converted, not passed through", Math.abs( o[1] - 0.8 ) > 0.01 || Math.abs( o[0] - 0.2 ) > 0.01, true );
         }
      }
      finally { romm.forceClose(); }

      // frame 0 with the output transform: an sRGB (untagged) scene is unchanged
      var fx = flyTestScene( synthDir( "fly-colour-scene" ) );
      try
      {
         var W = fx.W, H = fx.H;
         var f0 = Render.frame( fx.scene, 0, { travel: 100, easing: "linear", growth: 0.15, brightening: true,
                                               output: Fly.outputTransform( Fly.SRGB_COLOUR, "sdr" ) }, W, H, { x: 0, y: 0, w: W, h: H } );
         var a = new Float32Array( W*H ), b = new Float32Array( W*H ), worst = 0;
         f0.getSamples( a ); fx.image.mainView.image.getSamples( b );
         for ( var i = 0; i < a.length; ++i ) worst = Math.max( worst, Math.abs( a[i] - b[i] ) );
         check( "frame 0 through the sRGB output transform is the image (worst " + worst.toExponential( 2 ) + ")", worst <= 1/65535, true );
         var g18 = { gray: true, matrix: null, trc: [ 0, 1, 2 ].map( function() { return { type: "gamma", g: 1.8 }; } ) };
         var fg = Render.frame( fx.scene, 0, { travel: 100, easing: "linear", growth: 0.15, brightening: true,
                                               output: Fly.outputTransform( g18, "sdr" ) }, W, H, { x: 0, y: 0, w: W, h: H } );
         var c = new Float32Array( W*H ), worstG = 0;
         fg.getSamples( c );
         for ( i = 0; i < c.length; i += 97 ) worstG = Math.max( worstG, Math.abs( c[i] - Fly.srgbEncode( Math.pow( b[i], 1.8 ) ) ) );
         check( "every frame goes through the output transform (gamma 1.8 source)", worstG < 1e-5, true );
      }
      finally { fx.windows.forEach( function( w ) { w.forceClose(); } ); }
   } )();

   /* ---- Fly: HDR (HLG and PQ) ------------------------------------------ */
   ( function()
   {
      function near( a, b, e ) { return Math.abs( a - b ) <= e; }
      check( "PQ of the 203-nit reference white is 0.5807", near( Fly.pqEncode( 203 ), 0.5807, 1e-4 ), true );
      check( "PQ of 10000 nits is 1", near( Fly.pqEncode( 10000 ), 1, 1e-9 ), true );
      check( "HLG of 1/12 is 0.5", near( Fly.hlgEncode( 1/12 ), 0.5, 1e-7 ), true );
      var sw = Fly.hlgFromDisplay( [ 0.203, 0.203, 0.203 ] );
      check( "HLG puts SDR white at 0.75 (BT.2408)", near( Fly.hlgEncode( sw[1] ), 0.75, 1e-3 ), true );

      var P = 1000/203, mono = true, prev = -1;
      check( "rolloff: identity up to white", Fly.rolloff( 0.6, P ), 0.6 );
      check( "rolloff: slope 1 at white (smooth)", near( ( Fly.rolloff( 1 + 1e-6, P ) - 1 )/1e-6, 1, 1e-4 ), true );
      for ( var L = 0; L < 1e6; L = L*1.5 + 0.01 ) { var y = Fly.rolloff( L, P ); if ( y < prev || y > P ) mono = false; prev = y; }
      check( "rolloff: monotone and never above the peak", mono, true );

      var pq = Fly.outputTransform( Fly.SRGB_COLOUR, "pq", { peak: 1000 } );
      var w = pq.pixelHdr( 1, 1, 1, 0, 0, 0 );
      check( "HDR (PQ): the image's white lands at 203 nits", near( w[0], 0.5807, 1e-3 ) && near( w[2], 0.5807, 1e-3 ), true );
      var hot = pq.pixelHdr( 1, 1, 1, 50, 50, 50 );
      check( "HDR (PQ): star light above white rises above it, up to the peak",
             hot[1] > 0.6 && hot[1] <= Fly.pqEncode( 1000 ) + 1e-9, true );
      var three = pq.pixelHdr( 1, 1, 1, 2, 2, 2 )[1];      // 3 x white = 609 nits before rolloff, ~521 after
      check( "HDR (PQ): a moderate highlight is compressed smoothly, not clipped",
             three > Fly.pqEncode( 500 ) && three < Fly.pqEncode( 540 ), true );
      var hlg = Fly.outputTransform( Fly.SRGB_COLOUR, "hlg", {} ).pixelHdr( 1, 1, 1, 0, 0, 0 );
      check( "HDR (HLG): the image's white lands at 0.75", near( hlg[1], 0.75, 2e-3 ), true );
      var hueKept = pq.pixelHdr( 1, 0.5, 0.25, 20, 10, 5 );
      check( "HDR: a coloured highlight keeps its hue order while rolled off", hueKept[0] > hueKept[1] && hueKept[1] > hueKept[2], true );
      var stats = Fly.outputTransform( Fly.SRGB_COLOUR, "pq", { peak: 1000 } ), R = new Float32Array( [ 1, 0 ] ), G = new Float32Array( [ 1, 0 ] ), B = new Float32Array( [ 1, 0 ] );
      stats.apply( R, G, B, 2, new Float32Array( 2 ), new Float32Array( 2 ), new Float32Array( 2 ) );
      check( "PQ frames are measured for HDR10 metadata: MaxCLL and MaxFALL in nits",
             near( stats.stats.maxCll, 203, 0.5 ) && near( stats.stats.maxFall, 101.5, 0.5 ), true );

      var enc = " V....D libx264 \n V....D libx265 \n V....D prores_ks \n V....D libvpx-vp9 \n";
      check( "HDR offers no H.264", Fly.availableFormats( enc, true ).map( function( f ) { return f.id; } ), [ "hevc", "prores", "vp9" ] );
      check( "HDR defaults to HEVC even at 1080p", Fly.defaultFormat( Fly.availableFormats( enc, true ), 1920 ), "hevc" );
      var hdrPq = { transfer: "pq", peak: 1000, maxCll: 850, maxFall: 120 }, hdrHlg = { transfer: "hlg" };
      var aPq = Fly.ffmpegArgs( "/f", 30, "/o/x", "hevc", "high", hdrPq ).join( " " ), aHlg = Fly.ffmpegArgs( "/f", 30, "/o/x", "hevc", "high", hdrHlg ).join( " " );
      check( "PQ: BT.2020 matrix and tags, 10-bit", /scale=out_color_matrix=bt2020:out_range=tv,setparams=color_primaries=bt2020:color_trc=smpte2084:colorspace=bt2020nc:range=tv/.test( aPq ) && /-pix_fmt yuv420p10le/.test( aPq ), true );
      check( "PQ: HDR10 mastering display and content light level",
             aPq.indexOf( "hdr10=1" ) >= 0 && aPq.indexOf( "master-display=G(8500,39850)B(6550,2300)R(35400,14600)WP(15635,16450)L(10000000,1)" ) >= 0 &&
             aPq.indexOf( "max-cll=850,120" ) >= 0, true );
      check( "HLG: arib-std-b67 and no HDR10 metadata", /color_trc=arib-std-b67/.test( aHlg ) && aHlg.indexOf( "master-display" ) < 0, true );
      check( "VP9 HDR is profile 2, 10-bit", /-profile:v 2 .*yuv420p10le/.test( Fly.ffmpegArgs( "/f", 30, "/o/x", "vp9", "high", hdrHlg ).join( " " ) ), true );
      check( "ProRes HDR stays 4:2:2 10-bit with the BT.2020 tags",
             /color_trc=smpte2084.*yuv422p10le/.test( Fly.ffmpegArgs( "/f", 30, "/o/x", "prores", "high", hdrPq ).join( " " ) ), true );
      check( "SDR arguments are unchanged by the HDR work", Fly.ffmpegArgs( "/f", 30, "/o/x", "h264", "high" ).join( " " ).indexOf( "bt709" ) > 0, true );
      check( "exhibition defaults to PQ, social and YouTube to HLG",
             [ Fly.HDR_DEFAULT_TRANSFER.exhibition, Fly.HDR_DEFAULT_TRANSFER.social_vertical, Fly.HDR_DEFAULT_TRANSFER.youtube_4k ], [ "pq", "hlg", "hlg" ] );
   } )();

   /* ---- HDR rendering and encoding (PixInsight) ----------------------- */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var dir = synthDir( "fly-hdr" ), fx = flyTestScene( dir, 4 );   // bright stars: some approach past white
      try
      {
         var W = fx.W, H = fx.H, full = { x: 0, y: 0, w: W, h: H };
         var t = Fly.outputTransform( Fly.SRGB_COLOUR, "pq", { peak: 1000 } );
         var f0 = Render.frame( fx.scene, 0, { travel: 150, easing: "linear", growth: 0.15, brightening: true, output: t }, W, H, full );
         var a = new Float32Array( W*H ), b = new Float32Array( W*H ), worst = 0;
         f0.getSamples( a ); fx.image.mainView.image.getSamples( b );
         for ( var i = 0; i < a.length; i += 13 ) worst = Math.max( worst, Math.abs( a[i] - t.pixel( b[i], b[i], b[i] )[1] ) );
         check( "HDR frame 0 is the image, mapped into PQ (worst " + worst.toExponential( 2 ) + ")", worst < 1e-5, true );
         var t1 = Fly.outputTransform( Fly.SRGB_COLOUR, "pq", { peak: 1000 } );
         var f1 = Render.frame( fx.scene, 1, { travel: 150, easing: "linear", growth: 0.15, brightening: true, output: t1 }, W, H, full );
         var c = new Float32Array( W*H ), top = 0;
         f1.getSamples( c );
         for ( i = 0; i < c.length; ++i ) top = Math.max( top, c[i] );
         check( "an approaching star rises above SDR white (" + top.toFixed( 3 ) + " > 0.5807) and stays under the peak",
                top > Fly.pqEncode( 203 ) + 0.02 && top <= Fly.pqEncode( 1000 ) + 1e-6, true );
         check( "and the frame's light is measured for HDR10 (MaxCLL " + Math.round( t1.stats.maxCll ) + " nits)",
                t1.stats.maxCll > 203 && t1.stats.maxCll <= 1000.5, true );

         var ff = Render.findFfmpeg();
         if ( ff == null ) return;
         var probe = File.extractDirectory( ff ) + "/ffprobe" + ( Util.isWindows() ? ".exe" : "" );
         [ [ "hlg", "arib-std-b67" ], [ "pq", "smpte2084" ] ].forEach( function( tr )
         {
            var id = "hdr_" + tr[0], opts = { travel: 150, easing: "smoothstep", growth: 0.15, brightening: true, duration: 0.5, fps: 10,
                         video: true, ffmpeg: ff, format: "hevc", quality: "standard", dynamic: "hdr", peak: 1000,
                         hdrTransfer: {} };
            opts.hdrTransfer[id] = tr[0];
            var res = FlyThrough.renderFinal( fx.scene, [ { id: id, w: 320, h: 180, pingPong: false } ], opts, dir, {} );
            check( "an HDR " + tr[0].toUpperCase() + " HEVC clip is made" + ( res.failed.length ? ": " + String( res.failed[0].output ).slice( -300 ) : "" ),
                   res.videos.length, 1 );
            if ( !File.exists( probe ) || res.videos.length != 1 ) return;
            var info = String( Render.runProcess( probe, [ "-v", "error", "-show_entries", "stream=pix_fmt,color_space,color_transfer,color_primaries",
                                                         "-of", "csv=p=0", res.videos[0] ], 10000 ).output ).trim();
            check( "it is 10-bit BT.2020 " + tr[1] + " (" + info + ")", info, "yuv420p10le,bt2020nc," + tr[1] + ",bt2020" );
            if ( tr[0] == "pq" )
            {
               var side = String( Render.runProcess( probe, [ "-v", "error", "-select_streams", "v:0", "-read_intervals", "%+#1",
                                                             "-show_frames", "-show_entries", "frame=side_data_list", res.videos[0] ], 10000 ).output );
               check( "the PQ clip carries HDR10 mastering display and content light level metadata",
                      /Mastering display metadata/.test( side ) && /Content light level metadata/.test( side ), true );
            }
         } );
      }
      finally { fx.windows.forEach( function( w ) { w.forceClose(); } ); }
   } )();

   /* ---- Draft order, player, dialog ------------------------------------ */
   check( "a clip plays its frames in order", [ 0, 1, 2, 3, 4 ].map( function( i ) { return Fly.sequenceFrame( i, 4, false ); } ), [ 0, 1, 2, 3, 0 ] );
   check( "a ping-pong draft plays the forward frames there and back",
          [ 0, 1, 2, 3, 4, 5, 6 ].map( function( i ) { return Fly.sequenceFrame( i, 4, true ); } ), [ 0, 1, 2, 3, 2, 1, 0 ] );
   check( "and loops without repeating the ends", Fly.sequenceLength( 4, true ), 6 );

   if ( IN_PIXINSIGHT ) ( function()
   {
      var host = new Dialog, p = null;
      try
      {
         p = new FlyThrough.Player( host );
         var bm = [ 0, 1, 2, 3 ].map( function() { var b = new Bitmap( 16, 9 ); b.fill( 0xff000000 ); return b; } );
         p.setFrames( bm, 10, false );
         p.startedAt = 1000;
         check( "the player shows the frame due by wall clock", p.frameAt( 1250 ), 2 );
         check( "late ticks drop frames rather than slow the clip", p.frameAt( 1390 ), 3 );
         p.setFrames( bm, 10, true );
         check( "a ping-pong draft comes back", p.frameAt( 1000 + 450 ), 2 );
         p.release();
         check( "a released player keeps no timer handler", p.timer == null || p.timer.onTimeout == null, true );
      }
      finally { if ( p ) p.release(); }

      var dir = synthDir( "fly-dialog" ), fx = flyTestScene( dir );
      try
      {
         var draft = FlyThrough.renderDraft( fx.scene, { travel: 150, easing: "smoothstep", growth: 0.15, brightening: true,
                                                         duration: 2, fps: 30 }, Fly.presetSpec( "exhibition" ), {} );
         check( "the draft renders only the forward half of a ping-pong (" + draft.bitmaps.length + " frames)",
                draft.bitmaps.length, Fly.draftPlan( 2, 30, Fly.DRAFT_LONG, 16/9 ).frames );
         check( "at the draft size on the long side", draft.bitmaps[0].width, Fly.DRAFT_LONG );

         for ( var cycle = 0; cycle < 5; ++cycle )
         {
            var dlg = new FlyThrough.Dialog( fx.image );
            try
            {
               var o = dlg.options();
               check( "dialog " + cycle + ": sensible defaults",
                      [ o.dynamic, o.fps, o.duration, o.presets.length > 0 ], [ "sdr", 30, 20, true ] );
               dlg.dynamicCombo.currentItem = 1;           // HDR
               dlg.dynamicCombo.onItemSelected( 1 );
               check( "dialog " + cycle + ": HDR offers no H.264", dlg.formatIds.indexOf( "h264" ), -1 );
            }
            finally { dlg.release(); }
         }
      }
      finally { fx.windows.forEach( function( w ) { w.forceClose(); } ); }
   } )();

   /*
    * Downscaled output must not alias. Every real preset shrinks the image
    * (a 26 MP frame is ~3.25x for 1080p) and the camera moves, so the
    * sampling phase slides every frame; point sampling would make stars
    * twinkle. A star's light must hold steady whatever the phase.
    */
   ( function()
   {
      var w = 300, h = 60, src = new Float32Array( w*h ), sig = 1.1;
      for ( var y = 0; y < h; ++y ) for ( var x = 0; x < w; ++x )
         src[y*w + x] = Math.exp( -( ( x - 150.3 )*( x - 150.3 ) + ( y - 30.2 )*( y - 30.2 ) )/( 2*sig*sig ) );
      var total = 0; for ( var i = 0; i < src.length; ++i ) total += src[i];
      [ "bilinear", "bicubic" ].forEach( function( kernel )
      {
         var lo = Infinity, hi = 0;
         for ( var ph = 0; ph < 1; ph += 0.125 )
         {
            var out = Render.resample( src, w, h, Render.axisWeights( 100, ph, 300, 150, 1, w, kernel ),
                                       Render.axisWeights( 20, ph, 60, 30, 1, h, kernel ) );
            var sum = 0; for ( var j = 0; j < out.length; ++j ) sum += out[j];
            lo = Math.min( lo, sum*9 ); hi = Math.max( hi, sum*9 );
         }
         check( kernel + ": a star downscaled 3x keeps its light at every phase (" + ( 100*lo/total ).toFixed( 1 ) + "-" +
                ( 100*hi/total ).toFixed( 1 ) + "%)", lo > 0.97*total && hi < 1.03*total, true );
      } );
      var ident = Render.axisWeights( 6, 0, 6, 2.3, 1, 6, "bicubic" );
      check( "at 1:1 the weights are still the identity", Array.prototype.slice.call( Render.resample(
             new Float32Array( [ 1, 2, 3, 4, 5, 6 ] ), 6, 1, ident, Render.axisWeights( 1, 0, 1, 0, 1, 1, "bicubic" ) ) ), [ 1, 2, 3, 4, 5, 6 ] );

      // a sprite drawn 3x smaller than its own pixels keeps its light too
      var pw = 15, patch = new Float32Array( pw*pw ), ptot = 0;
      for ( y = 0; y < pw; ++y ) for ( x = 0; x < pw; ++x ) { patch[y*pw + x] = Math.exp( -( ( x - 7 )*( x - 7 ) + ( y - 7 )*( y - 7 ) )/2.4 ); ptot += patch[y*pw + x]; }
      var sp = { rect: { x0: 43, y0: 43, x1: 58, y1: 58 }, det: { x: 50, y: 50 } }, slo = Infinity, shi = 0;
      for ( ph = 0; ph < 1; ph += 0.125 )
      {
         var acc = new Float32Array( 40*40 );
         Render.drawSprite( acc, 40, 40, patch, sp, 50 + ph, 50 + ph/2, 1, 1, { x: 0, y: 0, fx: 3, fy: 3 } );
         var s = 0; for ( i = 0; i < acc.length; ++i ) s += acc[i];
         slo = Math.min( slo, s*9 ); shi = Math.max( shi, s*9 );
      }
      check( "a sprite drawn at 1/3 scale keeps its light at every phase (" + ( 100*slo/ptot ).toFixed( 1 ) + "-" +
             ( 100*shi/ptot ).toFixed( 1 ) + "%)", slo > 0.95*ptot && shi < 1.05*ptot, true );
   } )();

   /*
    * pjsr/StarDetector.jsh must not be included by any entry point. It
    * replaces PixInsight's native StarDetector with the script one, and
    * ImageSolver's PSF.fitStars then rejects the stars ("StarData object
    * reference expected"): every unsolved image failed to solve (found on a
    * real image, 2026-09-23). The native detector is always there.
    */
   check( "no entry point includes pjsr/StarDetector.jsh",
          [ "Loom.js", "FrameSelector.js", "FlyThrough.js" ].filter( function( f )
             { return /#include\s+<pjsr\/StarDetector\.jsh>/.test( File.readTextFile( LOOM_DIR + "/" + f ) ); } ), [] );

   check( "RA in hours-minutes-seconds", Math.abs( Fly.parseAngle( "21 36 42", true ) - 324.175 ) < 1e-9, true );
   check( "RA with colons", Math.abs( Fly.parseAngle( "21:36:42", true ) - 324.175 ) < 1e-9, true );
   check( "Dec in degrees-minutes-seconds, signed", Math.abs( Fly.parseAngle( "-57 30 00", false ) + 57.5 ) < 1e-9, true );
   check( "a plain number is degrees", Fly.parseAngle( "324.18", true ), 324.18 );
   check( "garbage is not an angle", Fly.parseAngle( "north", false ), null );

   /*
    * The dialog's state flow: what is on screen is what renders. Edits made
    * after Analyse -- distance, type, travel -- must reach the scene, and a
    * nebula with no cluster must not render a static video.
    */
   if ( IN_PIXINSIGHT && Steps.availableStarTools().length > 0 ) ( function()
   {
      var dir = synthDir( "fly-dialog-flow" ), fx = flyTestScene( dir ), saved = Sky.querySources, dlg = null;
      try
      {
         Sky.querySources = function() { return fx.sources; };
         dlg = new FlyThrough.Dialog( fx.image );
         check( "the dialog has its title", dlg.windowTitle, "Loom Fly-Through" );
         check( "an untouched type lets NGC/IC decide (a PGC number makes a galaxy)", dlg.choices().type, undefined );
         dlg.analyse();
         check( "no cluster: travel is not guessed before the distance is known", dlg.travelEdit.text, "" );
         dlg.distanceEdit.text = "700"; dlg.distanceEdit.onEditCompleted();
         var o = dlg.prepare();
         check( "a typed distance reaches the render", dlg.built.scene.D, 700 );
         check( "and travel follows it", o.travel, 140 );
         dlg.distanceEdit.text = "1400"; dlg.distanceEdit.onEditCompleted();
         o = dlg.prepare();
         check( "editing it after a build updates the scene and the travel", [ dlg.built.scene.D, o.travel ], [ 1400, 280 ] );
         dlg.travelEdit.text = "5000"; dlg.travelEdit.onEditCompleted();
         o = dlg.prepare();
         check( "travel beyond the distance is capped, and the dialog says so", [ o.travel, /capped/.test( dlg.travelNote.text ) ], [ 1260, true ] );
         dlg.typeCombo.currentItem = 1; dlg.typeCombo.onItemSelected( 1 );
         o = dlg.prepare();
         check( "switching to galaxy makes the backdrop fixed", dlg.built.scene.D === Infinity, true );
         check( "the star-split windows are closed once the scene has its data", dlg.built.windows.length, 0 );
      }
      finally
      {
         Sky.querySources = saved;
         if ( dlg ) dlg.release();
         fx.windows.forEach( function( w ) { w.forceClose(); } );
      }
   } )();

   /*
    * Where the camera aims. Heading for a catalogued centre that lies
    * outside the image (IC 1396's, in a frame of the Elephant's Trunk) put
    * the vanishing point off-frame and the whole view streamed sideways
    * (seen on a real image). The target is aimed at only when it is inside
    * the image; otherwise the image centre is, and the dialog says so.
    */
   check( "a target inside the image is aimed at", Fly.insideImage( { x: 400, y: 300 }, 800, 600, 0.05 ), true );
   check( "one beyond the frame is not", Fly.insideImage( { x: 400, y: -50 }, 800, 600, 0.05 ), false );
   check( "nor one in the outer 5% margin", Fly.insideImage( { x: 790, y: 300 }, 800, 600, 0.05 ), false );

   if ( IN_PIXINSIGHT ) ( function()
   {
      var saved = Sky.querySources, w = null;
      try
      {
         Sky.querySources = function() { return [ { ra: 324.7, dec: 57.3, plx: 1, pmra: 0, pmdec: 0, G: 12 } ]; };
         w = ImageWindow.open( synthFrame( synthDir( "fly-aim" ) + "/off.xisf", { fwhm: 3, background: 0.05, noise: 0.002, seed: 3, stars: 50 } ) )[0];
         withTanKeywords( w, 324.745, 57.514 - 0.2, 0.0005 );     // IC 1396's centre 0.2 deg north: in the field circle, off the frame
         var id = FlyThrough.identify( w, { distance: 900 } );
         check( "the target is still IC 1396", id.target && id.target.id, "IC1396" );
         check( "the camera aims at the image centre", Fly.separation( id.aim, id.field.centre ) < 1e-6, true );
      }
      finally { Sky.querySources = saved; if ( w ) w.forceClose(); }
   } )();

   /*
    * Frames carry the profile of what they hold. Saving through a window
    * embedded PixInsight's default profile (ProPhoto on the maintainer's
    * machine) into every frame, SDR and HDR alike (read back from a real
    * render): an editor honouring it showed them wrong. SDR frames now carry
    * sRGB; HDR frames none -- no ICC profile describes PQ or HLG.
    */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var dir = synthDir( "fly-frame-icc" ), img = new Image( 16, 9, 3, ColorSpace_RGB, 32, SampleType_Real );
      img.fill( 0.5 );
      function iccOf( path )
      {
         var f = new FileFormatInstance( new FileFormat( ".tif", true, false ) );
         f.open( path, "verbosity 0" );
         try { var b = f.iccProfile; return ( b && b.length ) ? b : null; } finally { f.close(); }
      }
      try
      {
         Render.writeTiff( img, dir + "/sdr.tif", Render.srgbIcc() );
         var b = iccOf( dir + "/sdr.tif" ), c = b && Fly.parseIccColour( function( i ) { return b.at( i ); }, b.length );
         check( "an SDR frame carries sRGB", c != null && Math.abs( c.matrix[0] - Fly.SRGB_COLOUR.matrix[0] ) < 2e-3, true );
         Render.writeTiff( img, dir + "/hdr.tif", null );
         check( "an HDR frame carries no profile", iccOf( dir + "/hdr.tif" ), null );
         var w = ImageWindow.open( dir + "/hdr.tif" )[0];
         check( "and frames stay 16-bit", w.mainView.image.bitsPerSample, 16 );
         w.forceClose();
         // a window per frame pulled PixInsight to the front on every frame, so the user could not work
         // elsewhere during a render: frames are written straight from the image
         check( "writing a frame opens no window", /ImageWindow/.test( Render.writeTiff.toString() ), false );
      }
      finally { img.free(); }
   } )();

   /* ---- Progress ------------------------------------------------------- */
   check( "progress text: stage, count, percent, time left",
          Fly.progressText( "Rendering YouTube 1920×1080", 150, 600, 90000 ),
          "Rendering YouTube 1920×1080 — 150 of 600 (25%) — about 5 min left" );
   check( "under a minute left", /less than a minute left/.test( Fly.progressText( "x", 590, 600, 60000 ) ), true );
   check( "a stage with no count shows elapsed time", Fly.progressText( "Removing stars (StarXTerminator)", 0, 0, 75000 ),
          "Removing stars (StarXTerminator) — 1 min 15 s" );
   check( "the fraction for the bar", [ Fly.progressFraction( 150, 600 ), Fly.progressFraction( 0, 0 ) ], [ 0.25, null ] );
   check( "ffmpeg's progress: the last frame count it printed",
          Fly.ffmpegFrameProgress( "frame=   12 fps=3.1 q=28.0 size=  256kB\rframe=   37 fps=3.2 q=28.0" ), 37 );
   check( "no frame count yet", Fly.ffmpegFrameProgress( "Input #0, image2" ), null );

   /* ---- The progress bar ------------------------------------------------ */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var host = new Dialog, bar = new FlyThrough.ProgressBar( host ), bmp = new Bitmap( 200, 20 );
      try
      {
         bar.set( 0.5, "half" );
         var g = new Graphics( bmp );
         try { bar.paintOn( g, 200, 20 ); } finally { g.end(); }
         check( "the bar fills to its fraction", [ bmp.pixel( 50, 2 ) == FlyThrough.ProgressBar.FILL, bmp.pixel( 150, 2 ) == FlyThrough.ProgressBar.TRACK ], [ true, true ] );
         check( "and keeps its text", bar.text, "half" );
         bar.set( null, "waiting for the star tool" );
         check( "no fraction: a stage with no count", bar.fraction, null );
      }
      finally { bar.release(); }

      // renderFinal reports every frame, and the encode as ffmpeg counts frames
      var fx = flyTestScene( synthDir( "fly-progress" ) ), frames = [], encodes = [];
      try
      {
         var ff = Render.findFfmpeg();
         FlyThrough.renderFinal( fx.scene, [ { id: "prog", w: 160, h: 90, pingPong: false } ],
            { travel: 150, easing: "linear", growth: 0.15, brightening: true, duration: 0.5, fps: 10, video: ff != null, ffmpeg: ff, format: "h264" },
            synthDir( "fly-progress-out" ), { onFrame: function( k, n ) { frames.push( k + "/" + n ); },
                                              onEncode: function( k, n ) { encodes.push( k + "/" + n ); } } );
         check( "every frame is reported", frames, [ "1/5", "2/5", "3/5", "4/5", "5/5" ] );
         if ( ff != null )
            check( "the encode reports ffmpeg's frame count (" + encodes.slice( -1 ) + ")", encodes.length > 0 && encodes[encodes.length - 1] == "5/5", true );
      }
      finally { fx.windows.forEach( function( w ) { w.forceClose(); } ); }
   } )();

   /*
    * The working size. Every frame was resampled from the full image, so a
    * 92 MP input cost 1.7-3.1 s per frame. The image is resampled once, up
    * front, so that the largest 16:9 frame that fits it is 3840x2160: 1:1
    * for the 4K presets, a reduction for everything smaller. Never enlarged.
    */
   check( "a 3:2 image: its 16:9 band becomes 3840 wide", Math.abs( Fly.workingScale( 6000, 4000 ) - 0.64 ) < 1e-12, true );
   check( "a wide image: its height becomes 2160", Math.abs( Fly.workingScale( 12000, 4000 ) - 0.54 ) < 1e-12, true );
   check( "a small image is never enlarged", Fly.workingScale( 3000, 2000 ), 1 );

   /* ---- The working copy ---------------------------------------------- */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var w = ImageWindow.open( synthFrame( synthDir( "fly-working" ) + "/big.xisf",
                  { width: 5120, height: 2880, fwhm: 3, background: 0.05, noise: 0.002, seed: 2, stars: 300 } ) )[0], work = null;
      try
      {
         withTanKeywords( w, 324.745, 57.514, 0.0005 );
         var before = Sky.projector( w )( 324.9, 57.6 );
         work = Sky.workingCopy( w );
         check( "a 5120x2880 image works at 3840x2160", [ work.window.mainView.image.width, work.window.mainView.image.height, work.scale ], [ 3840, 2160, 0.75 ] );
         check( "the original is untouched", w.mainView.image.width, 5120 );
         var after = Sky.projector( work.window )( 324.9, 57.6 );
         check( "sky positions follow the resample",
                Math.abs( after.x - ( before.x + 0.5 )*0.75 + 0.5 ) < 0.01 && Math.abs( after.y - ( before.y + 0.5 )*0.75 + 0.5 ) < 0.01, true );
         var small = ImageWindow.open( synthFrame( synthDir( "fly-working-small" ) + "/s.xisf", { fwhm: 3, background: 0.05, noise: 0.002, seed: 2 } ) )[0];
         var ws = Sky.workingCopy( small );
         check( "a small image is not resampled", [ ws.scale, ws.window.mainView.image.width ], [ 1, 800 ] );
         ws.window.forceClose(); small.forceClose();
      }
      finally { if ( work ) work.window.forceClose(); w.forceClose(); }
   } )();

   /*
    * Matching bright stars. On a real image none of the 40 brightest moved
    * that had its Gaia star 2-4 px from the detected centre: a saturated
    * core's centroid is that uncertain, and a fixed 1.5 px radius missed
    * them. The radius grows with the star's core, and when several Gaia
    * stars are in reach the brightest is the one that made the big star.
    */
   ( function()
   {
      check( "a small star keeps the 1.5 px radius", Fly.matchRadius( { rect: Fly.detectionRect( 10, 10, 3 ) } ), 1.5 );
      var big = { x: 50, y: 50, nmax: 0, flux: 120, rect: Fly.detectionRect( 50, 50, 220 ) };
      check( "a saturated star's radius follows its core (" + Fly.matchRadius( big ).toFixed( 1 ) + " px)", Fly.matchRadius( big ) >= 3.5, true );
      var P = function( x, y, G ) { return { source: { G: G }, d: 300, x: x, y: y }; };
      var a = Fly.assignSprites( [ big ], [ P( 51.2, 53.1, 16.5 ), P( 52.5, 51.5, 11.2 ) ], 100, 100, 3, [] );
      check( "a big star 3 px from its Gaia position is matched, to the brightest candidate",
             a.sprites.length == 1 && a.sprites[0].placed.source.G, 11.2 );
   } )();

   if ( IN_PIXINSIGHT ) ( function()
   {
      var N = 240, c = 120, win = new ImageWindow( N, N, 1, 32, true, false, Util.freeWindowId( "fly_spike" ) );
      try
      {
         var buf = new Float32Array( N*N );
         for ( var y = 0; y < N; ++y ) for ( var x = 0; x < N; ++x )
         {
            var r2 = ( x - c )*( x - c ) + ( y - c )*( y - c ), v = 0.02 + 0.9*Math.pow( 1 + r2/6, -2.5 );
            if ( ( Math.abs( x - c ) < 1 || Math.abs( y - c ) < 1 ) && r2 < 60*60 ) v += 0.08*( 1 - Math.sqrt( r2 )/60 ) + 0.02;
            var nx = x - ( c + 30 ), ny = y - ( c + 30 );          // a patch of nebula between the spikes
            v += 0.03*Math.exp( -( nx*nx + ny*ny )/50 );
            buf[y*N + x] = Math.min( 1, v );
         }
         win.mainView.beginProcess( UndoFlag_NoSwapFile ); win.mainView.image.setSamples( buf ); win.mainView.endProcess();
         var sp = Sky.sprites( win.mainView.image, [ { source: { G: 9, ra: 0, dec: 0 }, d: 100, x: c, y: c } ], 3, [] );
         check( "the bright star with spikes is a sprite", sp.sprites.length, 1 );
         var s = sp.sprites[0], R = sp.residual[0];
         check( "its footprint takes the spikes (radius " + s.radius + ")", s.radius >= 55, true );
         var tip = R[c*N + c + 50], field = R[20*N + 20];
         check( "a spike tip is not left behind (residual " + tip.toFixed( 4 ) + ")", Math.abs( tip - 0.02 ) < 0.004, true );
         var under = R[( c + 10 )*N + c + 10];
         check( "where the star was, its background remains -- no dark hole (" + under.toFixed( 4 ) + ")", Math.abs( under - 0.02 ) < 0.002, true );
         check( "and outside its footprint nothing changes (" + field.toFixed( 4 ) + ")", Math.abs( field - 0.02 ) < 0.002, true );
         var neb = ( c + 30 )*N + c + 30;
         check( "nebula between the spikes stays where it is (" + R[neb].toFixed( 4 ) + " of " + buf[neb].toFixed( 4 ) + ")",
                Math.abs( R[neb] - buf[neb] ) < 0.001, true );
         var sum = 0, worst = 0, rw = s.rect.x1 - s.rect.x0;
         for ( y = s.rect.y0; y < s.rect.y1; ++y ) for ( x = s.rect.x0; x < s.rect.x1; ++x )
            worst = Math.max( worst, Math.abs( s.pixels[0][( y - s.rect.y0 )*rw + x - s.rect.x0] + R[y*N + x] - buf[y*N + x] ) );
         check( "sprite plus residual is the stars layer exactly (worst " + worst.toExponential( 1 ) + ")", worst < 1e-6, true );
      }
      finally { win.forceClose(); }
   } )();

   /*
    * A vertical image is never cut to a horizontal band: for a landscape
    * preset it is turned 90 degrees, so its long side runs along the frame's
    * (and the reverse for a vertical preset on a landscape image).
    */
   check( "portrait image, landscape preset: rotate", Fly.needsRotation( 3000, 5000, 3840, 2160 ), true );
   check( "portrait image, vertical preset: as it is", Fly.needsRotation( 3000, 5000, 1080, 1920 ), false );
   check( "landscape image, vertical preset: rotate", Fly.needsRotation( 5000, 3000, 1080, 1920 ), true );
   check( "a square preset never rotates", Fly.needsRotation( 3000, 5000, 1080, 1080 ), false );
   check( "a portrait image's long side feeds 3840", Math.abs( Fly.workingScale( 7669, 11957 ) - 3840/11957 ) < 1e-12, true );
   ( function()
   {
      // a 3x2 scene: rotating 90 degrees moves pixel (x, y) to (h - 1 - y, x)
      var sc = { w: 3, h: 2, nc: 1, S: [ new Float32Array( [ 1, 2, 3, 4, 5, 6 ] ) ], R: [ new Float32Array( [ 10, 20, 30, 40, 50, 60 ] ) ],
                 tp: { x: 2, y: 1 }, target: { ra: 1, dec: 1 }, D: 900,
                 project: function( ra, dec ) { return { x: ra, y: dec }; },
                 sprites: [ { s: { det: { x: 2, y: 0 }, rect: { x0: 1, y0: 0, x1: 3, y1: 1 }, pixels: [ new Float32Array( [ 7, 8 ] ) ],
                                   source: { ra: 2, dec: 0 }, d: 100 }, p0: { x: 2, y: 0 } } ] };
      var r = Render.rotateScene( sc );
      check( "rotated size", [ r.w, r.h ], [ 2, 3 ] );
      check( "rotated backdrop", Array.from( r.S[0] ), [ 4, 1, 5, 2, 6, 3 ] );
      check( "rotated residual", Array.from( r.R[0] ), [ 40, 10, 50, 20, 60, 30 ] );
      check( "rotated aim point", r.tp, { x: 0, y: 2 } );
      check( "rotated projection", r.project( 2, 0 ), { x: 1, y: 2 } );
      var s = r.sprites[0].s;
      check( "a rotated sprite: centre, footprint and pixels", [ s.det.x, s.det.y, s.rect, Array.from( s.pixels[0] ) ],
             [ 1, 2, { x0: 1, y0: 1, x1: 2, y1: 3 }, [ 7, 8 ] ] );
      var back = Render.rotateScene( Render.rotateScene( Render.rotateScene( r ) ) );
      check( "four quarter turns are the identity", [ Array.from( back.S[0] ), back.sprites[0].s.det ], [ [ 1, 2, 3, 4, 5, 6 ], { x: 2, y: 0 } ] );
   } )();

   if ( IN_PIXINSIGHT ) ( function()
   {
      var dir = synthDir( "fly-rotate" ), fx = flyTestScene( dir );
      try
      {
         var res = FlyThrough.renderFinal( fx.scene, [ { id: "tall", w: 600, h: 800, pingPong: false } ],
            { travel: 150, easing: "linear", growth: 0.15, brightening: true, duration: 0.1, fps: 10, video: false,
              output: Fly.outputTransform( Fly.SRGB_COLOUR, "sdr" ) }, dir, {} );
         var f = ImageWindow.open( dir + "/tall/frame_00000.tif" )[0], got = new Float32Array( 600*800 ), src = new Float32Array( 800*600 );
         f.mainView.image.getSamples( got ); fx.image.mainView.image.getSamples( src );
         var worst = 0;
         for ( var y = 0; y < 600; ++y ) for ( var x = 0; x < 800; ++x )
            worst = Math.max( worst, Math.abs( got[x*600 + ( 599 - y )] - src[y*800 + x] ) );
         check( "a landscape image in a portrait preset is turned, whole, not cut (worst " + worst.toExponential( 1 ) + ")", worst <= 1/65535 + 1e-6, true );
         f.forceClose();
      }
      finally { fx.windows.forEach( function( w ) { w.forceClose(); } ); }
   } )();

   /*
    * One star, one sprite. A bright star's diffraction spikes cross faint
    * stars that Gaia knows; each became a sprite of its own, took a piece of
    * the spike and flew off at its own distance -- the bright star tore
    * apart as it neared the camera (seen frame by frame on a real render).
    * A star whose centre lies within a brighter moving star's reach is not
    * a sprite; it moves with the bright one.
    */
   ( function()
   {
      function det( x, y, flux, area ) { return { x: x, y: y, nmax: 0, flux: flux, rect: Fly.detectionRect( x, y, area ) }; }
      var bright = det( 50, 50, 120, 150 ), onSpike = det( 64, 50, 2, 5 ), apart = det( 90, 90, 3, 5 );
      var P = function( x, y, G, d ) { return { source: { G: G }, d: d, x: x, y: y }; };
      var halo = function( d ) { var r = d === bright ? 20 : 3; d.halo = { radius: r, pedestal: 0, feather: 2, outer: r + 2 }; return r + 2; };
      var a = Fly.assignSprites( [ bright, onSpike, apart ], [ P( 50, 50, 9, 400 ), P( 64, 50, 15, 150 ), P( 90, 90, 14, 700 ) ], 120, 120, 3, [], halo );
      var ids = a.sprites.map( function( s ) { return s.det; } );
      check( "the star on the bright star's spike is not a sprite of its own", ids.indexOf( onSpike ), -1 );
      check( "the bright star and a star well away both are", [ ids.indexOf( bright ) >= 0, ids.indexOf( apart ) >= 0 ], [ true, true ] );
      check( "the spike star's pixels move with the bright star", a.owner[50*120 + 64], ids.indexOf( bright ) );
      check( "and it is counted", a.absorbed, 1 );
   } )();

   /*
    * Absorption follows the star's shape: its halo, and its spikes -- not a
    * circle as long as its longest spike, which would carry off every faint
    * star within 100 px of a bright one.
    */
   ( function()
   {
      function det( x, y, flux ) { return { x: x, y: y, nmax: 0, flux: flux, rect: Fly.detectionRect( x, y, 5 ) }; }
      var b = det( 100, 100, 120 );
      b.halo = { radius: 80, halo: 15, pedestal: 0, feather: 12, outer: 92, spikes: [ { angle: 0, reach: 80 } ] };
      var onSpike = det( 150, 100, 2 ), offSpike = det( 100, 150, 2 );
      check( "a star on a spike is within the bright star's reach", Fly.withinReach( b, onSpike ), true );
      check( "a star as far out but off the spikes is not", Fly.withinReach( b, offSpike ), false );
      check( "a star inside the halo is", Fly.withinReach( b, det( 110, 105, 2 ) ), true );
   } )();

   /*
    * The camera flies into the middle of the picture -- the photographer's
    * composition -- whatever the catalogue's centre for the target (IC 1396's
    * is inside the Elephant's Trunk frame but far off its middle, and the
    * flight veered left). The target still sets the distance.
    */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var saved = Sky.querySources, w = null;
      try
      {
         Sky.querySources = function() { return [ { ra: 324.7, dec: 57.5, plx: 1, pmra: 0, pmdec: 0, G: 12 } ]; };
         w = ImageWindow.open( synthFrame( synthDir( "fly-aim-centre" ) + "/f.xisf", { fwhm: 3, background: 0.05, noise: 0.002, seed: 3, stars: 50 } ) )[0];
         withTanKeywords( w, 324.745 + 0.1, 57.514, 0.0005 );     // IC 1396's centre inside the frame, off its middle
         var id = FlyThrough.identify( w, { distance: 900 } );
         check( "a target inside the frame, off its middle: the camera still aims at the middle",
                [ id.target && id.target.id, Fly.separation( id.aim, id.field.centre ) < 1e-6 ], [ "IC1396", true ] );
      }
      finally { Sky.querySources = saved; if ( w ) w.forceClose(); }
   } )();

   /*
    * How much the nebula zooms: a share of the physically correct zoom
    * D/(D - s). At its true distance the Elephant's Trunk grew 1.25x over the
    * clip, which was far too much to watch; the stars keep their real motion.
    */
   check( "nebula motion 100% is the physical zoom", Math.abs( Fly.backdropZoom( 900, 180, 1 ) - 900/720 ) < 1e-12, true );
   check( "40% is 40% of the growth", Math.abs( Fly.backdropZoom( 900, 180, 0.4 ) - ( 1 + 0.4*( 900/720 - 1 ) ) ) < 1e-12, true );
   check( "0% holds the nebula still", Fly.backdropZoom( 900, 180, 0 ), 1 );
   check( "a galaxy is still fixed", Fly.backdropZoom( Infinity, 180, 1 ), 1 );
   check( "the default is 40%", Fly.BACKDROP_MOTION_DEFAULT, 0.4 );

   if ( IN_PIXINSIGHT ) ( function()
   {
      var fx = flyTestScene( synthDir( "fly-nebula-setting" ) ), dlg = null;
      try
      {
         dlg = new FlyThrough.Dialog( fx.image );
         check( "the dialog offers the nebula motion, at 40%", [ dlg.nebulaSpin.value, dlg.options().backdropMotion ], [ 40, 0.4 ] );
      }
      finally { if ( dlg ) dlg.release(); fx.windows.forEach( function( w ) { w.forceClose(); } ); }
   } )();

   /*
    * The brightest star of a real field (G 5.6) was not detected at all --
    * saturated and huge, the detector rejects it -- so it stayed still,
    * while a faint Gaia star in its glow became a sprite and carried a
    * chunk of it away. Bright Gaia stars with no detection get one, and a
    * faint star in any brighter star's reach, moving or not, is no sprite.
    */
   ( function()
   {
      function det( x, y, flux, area ) { return { x: x, y: y, nmax: 0, flux: flux, rect: Fly.detectionRect( x, y, area || 5 ) }; }
      var dets = [ det( 20, 20, 5 ) ];
      var nb = [ { source: { G: 5.6 }, x: 60.4, y: 40.2 }, { source: { G: 14 }, x: 90, y: 90 }, { source: { G: 8 }, x: 20.5, y: 20.3 } ];
      var added = Fly.addMissingBright( dets, nb, function( x, y ) { return { flux: 900, area: 400 }; } );
      check( "a bright Gaia star with no detection gets one, at its Gaia position",
             added.length == 2 && added[1].x == 60.4 && added[1].y == 40.2 && added[1].flux == 900, true );
      check( "a faint one does not, nor one already detected", added.length, 2 );

      var still = det( 50, 50, 300, 300 ), faint = det( 70, 50, 3 );     // in the glow, clear of the core: not a blend
      var halo = function( d ) { var r = d === still ? 25 : 3; d.halo = { radius: r, halo: r, pedestal: 0, feather: 2, outer: r + 2 }; return r + 2; };
      var P = function( x, y ) { return { source: { G: 15 }, d: 200, x: x, y: y }; };
      var a = Fly.assignSprites( [ still, faint ], [ P( 70, 50 ) ], 120, 120, 3, [], halo );   // the bright one is not placed
      check( "a faint star in a still bright star's glow is not a sprite", a.sprites.length, 0 );
      check( "and the bright star's light stays whole", a.owner[50*120 + 70], -1 );
   } )();

   /*
    * What a star leaves behind must look like the sky around it. Taking
    * max(0, stars - background) removed only the upward noise and left a
    * darker disc, and faint stars inside a bright star's glow left with it,
    * leaving an emptier one (both seen on two different real images). The
    * sprite carries a lightly smoothed profile, so the noise stays; faint
    * stars in the glow stay too, the glow under them filled from its profile.
    */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var N = 400, c = 200, rnd = synthRandom( 17 ), win = new ImageWindow( N, N, 1, 32, true, false, Util.freeWindowId( "fly_leave" ) );
      try
      {
         var buf = new Float32Array( N*N ), clean = new Float32Array( N*N );
         for ( var y = 0; y < N; ++y ) for ( var x = 0; x < N; ++x )
         {
            var r2 = ( x - c )*( x - c ) + ( y - c )*( y - c ), f2 = ( x - c - 15 )*( x - c - 15 ) + ( y - c )*( y - c );
            var u = Math.max( 1e-12, rnd() ), v = rnd(), n = 0.004*Math.sqrt( -2*Math.log( u ) )*Math.cos( 2*Math.PI*v );
            clean[y*N + x] = 0.02 + 0.9*Math.pow( 1 + r2/36, -1.5 ) + 0.1*Math.exp( -f2/2 );    // a bright star's wide glow
            buf[y*N + x] = Math.max( 0, clean[y*N + x] + n );
         }
         win.mainView.beginProcess( UndoFlag_NoSwapFile ); win.mainView.image.setSamples( buf ); win.mainView.endProcess();
         var sp = Sky.sprites( win.mainView.image, [ { source: { G: 9, ra: 0, dec: 0 }, d: 100, x: c, y: c } ], 3, [] );
         var s = sp.sprites[0], R = sp.residual[0], inside = 0, outside = 0, ni = 0, no = 0;
         for ( y = 0; y < N; ++y ) for ( x = 0; x < N; ++x )
         {
            var d = Math.hypot( x - c, y - c ), fd = Math.hypot( x - c - 15, y - c );
            if ( fd < 4 ) continue;
            if ( d > 6 && d < s.det.halo.halo ) { inside += R[y*N + x]; ++ni; }
            else if ( d > s.radius + 4 && d < s.radius + 30 ) { outside += R[y*N + x]; ++no; }
         }
         var bias = inside/ni - outside/no;
         check( "where the star was, the sky is as bright as around it (difference " + ( bias/0.004 ).toFixed( 2 ) + " sigma)", Math.abs( bias ) < 0.25*0.004, true );
         var fi = c*N + c + 15;
         check( "a faint star in the glow stays where it is (" + R[fi].toFixed( 3 ) + " of " + buf[fi].toFixed( 3 ) + ")", R[fi] - 0.02 > 0.04, true );
         var rw = s.rect.x1 - s.rect.x0, at = function( x, y ) { return s.pixels[0][( y - s.rect.y0 )*rw + x - s.rect.x0]; };
         check( "and the moving glow has no hole under it (" + at( c + 15, c ).toFixed( 4 ) + " vs " + at( c, c - 15 ).toFixed( 4 ) + " at the same radius)",
                Math.abs( at( c + 15, c ) - at( c, c - 15 ) ) < 0.01, true );
      }
      finally { win.forceClose(); }
   } )();

   /* An image with an alpha channel (an RGBA TIFF export) works as RGB: alpha is not a colour. */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var w = new ImageWindow( 32, 16, 4, 32, true, true, Util.freeWindowId( "fly_rgba" ) ), work = null;
      try
      {
         w.mainView.beginProcess( UndoFlag_NoSwapFile ); w.mainView.image.fill( 0.3 ); w.mainView.endProcess();
         work = Sky.workingCopy( w );
         check( "an RGBA image's working copy is RGB", work.window.mainView.image.numberOfChannels, 3 );
      }
      finally { if ( work ) work.window.forceClose(); w.forceClose(); }
   } )();

   /*
    * A linear (unstretched) master renders as a black video: its sky sits
    * near 0.001-0.01, where a finished image's is ~0.05-0.25 (a real master
    * rendered black). Such an image is stretched in the working copy, with
    * Loom's own stretch, and the dialog says so.
    */
   check( "a sky at 0.004 is linear", Fly.looksLinear( 0.004 ), true );
   check( "a sky at 0.08 is a finished image", Fly.looksLinear( 0.08 ), false );
   if ( IN_PIXINSIGHT ) ( function()
   {
      var lin = ImageWindow.open( synthFrame( synthDir( "fly-linear" ) + "/lin.xisf", { fwhm: 3, background: 0.002, noise: 0.0003, seed: 4, stars: 80, flux: 0.2 } ) )[0];
      var fin = ImageWindow.open( synthFrame( synthDir( "fly-linear" ) + "/fin.xisf", { fwhm: 3, background: 0.08, noise: 0.004, seed: 4, stars: 80 } ) )[0], a = null, b = null;
      try
      {
         a = Sky.workingCopy( lin ); b = Sky.workingCopy( fin );
         var sa = Sky.stretchIfLinear( a.window ), sb = Sky.stretchIfLinear( b.window );
         check( "a linear image is stretched (sky " + a.window.mainView.image.median().toFixed( 3 ) + ")", [ sa, a.window.mainView.image.median() > 0.1 ], [ true, true ] );
         check( "a finished image is left as it is", [ sb, Math.abs( b.window.mainView.image.median() - fin.mainView.image.median() ) < 1e-6 ], [ false, true ] );
      }
      finally { [ a && a.window, b && b.window, lin, fin ].forEach( function( w ) { if ( w ) w.forceClose(); } ); }
   } )();

   /* ---- Deblending: splitting light between stars by model ------------- */
   ( function()
   {
      function near( a, b, e ) { return Math.abs( a - b ) <= e; }
      // an angular profile (1 degree bins) with spikes at 45 and 135 degrees (both ways) and noise
      var rnd = synthRandom( 3 ), prof = [];
      for ( var a = 0; a < 360; ++a )
      {
         var v = 0.002*( rnd() - 0.5 );
         [ 45, 135, 225, 315 ].forEach( function( s ) { var d = Math.min( Math.abs( a - s ), 360 - Math.abs( a - s ) ); v += 0.05*Math.exp( -d*d/2 ); } );
         prof.push( v );
      }
      var ang = Fly.spikeAngles( prof, 0.001 );
      check( "spike directions are found where the profile peaks", ang, [ 45, 135, 225, 315 ] );
      var flat = prof.map( function() { return 0.002*( rnd() - 0.5 ); } );
      check( "an image without spikes has none", Fly.spikeAngles( flat, 0.001 ), [] );
      // a neighbouring star on one side is not a spike: spikes are lines through the star
      var oneSided = flat.map( function( v, a ) { var d = Math.min( Math.abs( a - 10 ), 360 - Math.abs( a - 10 ) ); return v + 0.05*Math.exp( -d*d/2 ); } );
      check( "a peak with no opposite is a neighbour, not a spike", Fly.spikeAngles( oneSided, 0.001 ), [] );

      // the split: data shared by model where stars are bright; the model itself where they fade into the noise
      var bright = Fly.deblend( 0.6, 0.8, 1.0, 0.004 );         // Mi, Msum, L (light), sigma
      check( "where stars are bright, a star takes its model's share of the data", near( bright, 0.6/0.8*1.0, 1e-9 ), true );
      var faint = Fly.deblend( 0.001, 0.001, 0.0045, 0.004 );
      check( "where a star has faded into the noise, it takes only its model (the noise stays)", near( faint, 0.001, 1e-9 ), true );
      var shares = Fly.deblend( 0.3, 0.5, 0.7, 0.004 ) + Fly.deblend( 0.2, 0.5, 0.7, 0.004 );
      check( "the shares of the stars at a pixel add up to its light", near( shares, 0.7, 1e-9 ), true );
      check( "no model, no light", Fly.deblend( 0, 0, 0.5, 0.004 ), 0 );
      // light far beyond what the models there predict is something no model knows (a brighter
      // star's spike past its reach): a faint star took all of it and flew off with a bar of spike
      check( "a star does not take light far beyond its model", Fly.deblend( 0.01, 0.01, 1.0, 0.004 ) <= 0.01*Fly.DEBLEND_CAP + 1e-12, true );
   } )();

   /*
    * A star's profile stands on its LOCAL sky. Measured above a global sky,
    * a small local offset in the stars layer kept faint stars' profiles
    * from ever fading, and models grew to the cap (a real image: median
    * reach 126 px). The profile ends where it stops falling, and that level
    * is its sky.
    */
   ( function()
   {
      var raw = [];
      for ( var r = 0; r < 150; ++r ) raw.push( 0.003 + 0.2*Math.exp( -r*r/8 ) );     // a faint star on a sky 0.003 above the global one
      var p = Fly.localProfile( raw, 4, 0.0004 );
      check( "the profile ends where it stops falling (" + p.rmax + ")", p.rmax < 15, true );
      check( "and stands on that local sky", Math.abs( p.sky - 0.003 ) < 0.0005, true );
      check( "so the model falls to zero at its edge", p.prof[p.rmax - 1] < 0.001, true );
   } )();

   /*
    * Is it a star? A star is a local peak of the image. A second detection
    * on a bright star's core, or a bead on its spike, has brighter pixels
    * toward that star; given a model of its own it never moved and kept part
    * of the moving star's light (a ghost core and spike, seen on a real image).
    */
   ( function()
   {
      var g = function( x, y, cx, cy, a, s ) { return a*Math.exp( -( ( x - cx )*( x - cx ) + ( y - cy )*( y - cy ) )/( 2*s*s ) ); };
      var halo = function( x, y ) { return g( x, y, 50, 50, 1, 6 ); };
      check( "a star's centre is a peak", Fly.localPeak( halo, 50, 50, 4 ), true );
      check( "a point on its slope is not", Fly.localPeak( halo, 58, 50, 4 ), false );
      var withFaint = function( x, y ) { return halo( x, y ) + g( x, y, 72, 50, 0.05, 1.5 ); };
      check( "a faint star on the halo's edge is", Fly.localPeak( withFaint, 72, 50, 4 ), true );
      var spike = function( x, y ) { return halo( x, y ) + ( x > 50 && Math.abs( y - 50 ) < 1 ? 0.3*Math.exp( -( x - 50 )/30 ) : 0 ); };
      check( "a bead on a spike is not", Fly.localPeak( spike, 70, 50, 4 ), false );
      // a real star on a steep glow: two FWHM toward the glow is brighter than its peak, one is not
      var steep = function( x, y ) { var r2 = ( x - 50 )*( x - 50 ) + ( y - 50 )*( y - 50 ); return 0.9*Math.pow( 1 + r2/36, -1.5 ) + g( x, y, 65, 50, 0.1, 1 ); };
      var onGlow = { x: 65, y: 50, flux: 0.6, rect: Fly.detectionRect( 65, 50, Math.PI*4 ) };
      check( "a real star on a steep glow is not a fragment", Fly.isFragment( onGlow, [ { x: 50, y: 50, flux: 50, rect: Fly.detectionRect( 50, 50, Math.PI*36 ) }, onGlow ], steep, 3 ), false );
      // a saturated core: flat at 1 out to 8 px, so every pixel there ties
      var sat = function( x, y ) { return Math.min( 1, 3*halo( x, y ) ); };
      var big = { x: 50, y: 50, flux: 100, rect: Fly.detectionRect( 50, 50, Math.PI*64 ) };
      var frag = { x: 55, y: 50, flux: 20, rect: Fly.detectionRect( 55, 50, Math.PI*9 ) };
      var real = { x: 72, y: 50, flux: 0.5, rect: Fly.detectionRect( 72, 50, Math.PI*4 ) };
      check( "a detection inside a brighter star's flat core is a fragment", Fly.isFragment( frag, [ big, frag ], sat, 3 ), true );
      check( "the bright star itself is not", Fly.isFragment( big, [ big, frag ], sat, 3 ), false );
      check( "a real faint neighbour is not", Fly.isFragment( real, [ big, real ], withFaint, 3 ), false );
      var onSlope = { x: 58, y: 50, flux: 5, rect: Fly.detectionRect( 58, 50, Math.PI*4 ) };
      check( "a detection on the slope is a fragment", Fly.isFragment( onSlope, [ big, onSlope ], halo, 3 ), true );
   } )();

   /*
    * An unresolved close pair: the detector sees one star between the two,
    * and the second pass finds each one's peak in its core. Left as static
    * sources they stayed behind as two dots where the star was (seen on a
    * real image); inside a moving star's core they are part of that star.
    */
   ( function()
   {
      var model = { x: 100, y: 100, core: 6 }, sprites = [ { det: { model: model } } ];
      var inCore = { x: 104.5, y: 100 }, outside = { x: 112, y: 100 };
      var stay = Fly.attachToCores( sprites, [ inCore, outside ] );
      check( "a missed source in a moving star's core goes with it", model.parts && model.parts[0] === inCore, true );
      check( "one outside it stays", stay.length == 1 && stay[0] === outside, true );
   } )();

   /*
    * Spike directions to a fraction of a degree. Read in whole degrees over
    * a short band, a direction was up to half a degree off; followed 100 px
    * out, the model lay beside the real spike and a thin line of it stayed
    * behind (seen on a real image). Spikes here fall between whole degrees.
    * A spike wider than the model's assumed width left its flanks behind the
    * same way.
    */
   if ( IN_PIXINSIGHT ) [ [ 33.5, 0.7, "a spike between whole degrees" ], [ 30, 1.6, "a spike wider than the model's default" ] ].forEach( function( t )
   {
      var N = 320, c = 160, ang = t[0]*Math.PI/180, width = t[1], ca = Math.cos( ang ), sa = Math.sin( ang ), rnd = synthRandom( 31 );
      var win = new ImageWindow( N, N, 1, 32, true, false, Util.freeWindowId( "fly_angle" ) );
      try
      {
         var buf = new Float32Array( N*N );
         for ( var y = 0; y < N; ++y ) for ( var x = 0; x < N; ++x )
         {
            var dx = x - c, dy = y - c, r2 = dx*dx + dy*dy, v = 0.02 + 0.9*Math.pow( 1 + r2/6, -2.5 );
            [ [ ca, sa ], [ -sa, ca ] ].forEach( function( u )
            {
               var along = Math.abs( dx*u[0] + dy*u[1] ), lat = -dx*u[1] + dy*u[0];
               if ( along > 6 && along < 140 ) v += 0.1*( 1 - along/140 )*Math.exp( -lat*lat/( 2*width*width ) );
            } );
            var p = Math.max( 1e-12, rnd() ), q = rnd();
            buf[y*N + x] = Math.min( 1, Math.max( 0, v + 0.002*Math.sqrt( -2*Math.log( p ) )*Math.cos( 2*Math.PI*q ) ) );
         }
         win.mainView.beginProcess( UndoFlag_NoSwapFile ); win.mainView.image.setSamples( buf ); win.mainView.endProcess();
         var sp = Sky.sprites( win.mainView.image, [ { source: { G: 9, ra: 0, dec: 0 }, d: 100, x: c, y: c } ], 3, [] );
         var R = sp.residual[0], worst = 0;
         for ( var r = 60; r < 120; ++r )
            for ( var l = -3; l <= 3; ++l )
            {
               var X = Math.round( c + r*ca - l*sa ), Y = Math.round( c + r*sa + l*ca );
               worst = Math.max( worst, R[Y*N + X] - 0.02 );
            }
         check( t[2] + " is not left behind far out (" + ( worst/0.002 ).toFixed( 1 ) + " sigma)", worst < 4*0.002, true );
      }
      finally { win.forceClose(); }
   } );

   /* A spike's width from its lateral profile (samples every `step` px, centred), above its own baseline. */
   ( function()
   {
      [ 0.7, 1.6, 2.5 ].forEach( function( sig )
      {
         var p = [];
         for ( var l = -8; l <= 8 + 1e-9; l += 0.5 ) p.push( 0.3 + Math.exp( -l*l/( 2*sig*sig ) ) );
         var got = Fly.lateralSigma( p, 0.5 );
         check( "a spike " + sig + " px wide measures " + got.toFixed( 2 ), Math.abs( got - sig ) < 0.12*sig + 0.05, true );
      } );
      var flat = [];
      for ( var l = -8; l <= 8 + 1e-9; l += 0.5 ) flat.push( 0.3 );
      check( "no spike gives the default", Fly.lateralSigma( flat, 0.5 ), null );
   } )();

   /*
    * A star on a brighter star's spike does not own a spike along it: the
    * light there is the brighter star's. Measured as its own, that stretch
    * of spike moved with the fainter star and tore off the bright one (seen
    * in a real video).
    */
   ( function()
   {
      var model = function( x, y, spikes, rmax ) { return { x: x, y: y, rmax: rmax, spikeSigma: 1.4, support: 0, spikes: spikes.map( function( a ) { return { angle: a[0], start: 5, reach: a[1] }; } ) }; };
      var bright = { flux: 100, model: model( 0, 0, [ [ 0, 120 ], [ Math.PI/2, 120 ] ], 40 ) };
      var onSpike = { flux: 2, model: model( 50, 0.8, [ [ 0, 60 ], [ Math.PI, 60 ], [ Math.PI/2, 12 ] ], 8 ) };
      var offSpike = { flux: 2, model: model( 50, 12, [ [ 0, 30 ] ], 8 ) };
      Fly.dropBorrowedSpikes( [ onSpike, bright, offSpike ] );
      check( "a star on a brighter spike loses its spikes along it", onSpike.model.spikes.map( function( s ) { return s.angle; } ).join(), String( Math.PI/2 ) );
      check( "and its support shrinks to what is left (" + onSpike.model.support + ")", onSpike.model.support, 13 );
      check( "the bright star keeps its own", bright.model.spikes.length, 2 );
      check( "a star off the spike keeps its", offSpike.model.spikes.length, 1 );
   } )();

   /*
    * A bright star's spikes run further than its halo: capped at the halo's
    * 150 px, the rest was nobody's, and faint stars on it took it with them.
    */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var N = 640, c = 100, rnd = synthRandom( 41 ), win = new ImageWindow( N, 200, 1, 32, true, false, Util.freeWindowId( "fly_long" ) );
      try
      {
         var buf = new Float32Array( N*200 );
         for ( var y = 0; y < 200; ++y ) for ( var x = 0; x < N; ++x )
         {
            var dx = x - c, dy = y - c, r2 = dx*dx + dy*dy, v = 0.02 + 0.9*Math.pow( 1 + r2/6, -2.5 );
            if ( Math.abs( dx ) > 6 ) v += 0.12*Math.max( 0, 1 - Math.abs( dx )/400 )*Math.exp( -dy*dy/( 2*1.2*1.2 ) );
            if ( Math.abs( dy ) > 6 ) v += 0.12*Math.max( 0, 1 - Math.abs( dy )/400 )*Math.exp( -dx*dx/( 2*1.2*1.2 ) );
            var p = Math.max( 1e-12, rnd() ), q = rnd();
            buf[y*N + x] = Math.min( 1, Math.max( 0, v + 0.002*Math.sqrt( -2*Math.log( p ) )*Math.cos( 2*Math.PI*q ) ) );
         }
         win.mainView.beginProcess( UndoFlag_NoSwapFile ); win.mainView.image.setSamples( buf ); win.mainView.endProcess();
         var sp = Sky.sprites( win.mainView.image, [ { source: { G: 9, ra: 0, dec: 0 }, d: 100, x: c, y: c } ], 3, [] );
         check( "the long-spiked star is a sprite", sp.sprites.length, 1 );
         if ( !sp.sprites.length ) return;
         var m = sp.sprites[0].det.model, reach = m.spikes.reduce( function( r, s ) { return Math.max( r, s.reach ); }, 0 );
         check( "a long spike is followed past the halo's reach (" + reach + " of 400)", reach >= 300, true );
      }
      finally { win.forceClose(); }
   } )();

   /*
    * Spikes are measured brightest star first, each fainter star's on the
    * image with the brighter ones' spikes taken out. A faint moving star
    * beside a bright star's spike measured that spike as its own, and flew
    * off with a bar of it (seen in a real video).
    */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var N = 240, c = 100, fx = 150, fy = 105, rnd = synthRandom( 43 ), win = new ImageWindow( N, N, 1, 32, true, false, Util.freeWindowId( "fly_borrow" ) );
      try
      {
         var buf = new Float32Array( N*N );
         for ( var y = 0; y < N; ++y ) for ( var x = 0; x < N; ++x )
         {
            var r2 = ( x - c )*( x - c ) + ( y - c )*( y - c ), v = 0.02 + 0.9*Math.pow( 1 + r2/6, -2.5 );
            if ( r2 > 36 && r2 < 110*110 ) v += 0.1*( 1 - Math.sqrt( r2 )/110 )*( Math.exp( -( x - c )*( x - c )/4.5 ) + Math.exp( -( y - c )*( y - c )/4.5 ) );
            var f2 = ( x - fx )*( x - fx ) + ( y - fy )*( y - fy );
            v += 0.25*Math.pow( 1 + f2/3, -2 );
            var p = Math.max( 1e-12, rnd() ), q = rnd();
            buf[y*N + x] = Math.min( 1, Math.max( 0, v + 0.002*Math.sqrt( -2*Math.log( p ) )*Math.cos( 2*Math.PI*q ) ) );
         }
         win.mainView.beginProcess( UndoFlag_NoSwapFile ); win.mainView.image.setSamples( buf ); win.mainView.endProcess();
         // the detections are given: StarDetector's own filters are not what is tested here
         var detect = Sky.detections, sp;
         Sky.detections = function() { return [ { index: 0, x: c, y: c, flux: 50, nmax: 0, rect: Fly.detectionRect( c, c, Math.PI*25 ) },
                                                { index: 1, x: fx, y: fy, flux: 1, nmax: 0, rect: Fly.detectionRect( fx, fy, Math.PI*4 ) } ]; };
         try { sp = Sky.sprites( win.mainView.image, [ { source: { G: 9, ra: 0, dec: 0 }, d: 100, x: c, y: c }, { source: { G: 14, ra: 0, dec: 0 }, d: 50, x: fx, y: fy } ], 3, [] ); }
         finally { Sky.detections = detect; }
         var faint = sp.sprites.filter( function( s ) { return Math.abs( s.det.x - fx ) < 2; } )[0];
         check( "the faint star beside the spike is a sprite (" + JSON.stringify( { n: sp.sprites.length, blended: sp.blended, unmatched: sp.unmatched, halo: sp.sprites[0] && sp.sprites[0].det.halo } ) + ")", !!faint, true );
         if ( !faint ) return;
         var along = faint.det.model.spikes.filter( function( s ) { var d = Math.abs( s.angle ) % Math.PI; return Math.min( d, Math.PI - d ) < 0.05; } );
         check( "it has no spike along the bright star's (" + along.map( function( s ) { return s.reach; } ).join() + ")", along.length, 0 );
      }
      finally { win.forceClose(); }
   } )();

   /*
    * Spikes are part of the PSF: every star's fall off the same way, scaled
    * by the star. Each star's scale comes from its inner spike (a median, so
    * a neighbour there does not count); the rest follows the image's
    * falloff. Measured independently out to where it faded, a moderate
    * star's spike ran as far as the brightest star's -- borrowing other
    * stars' spikes and halos -- and flew off with them (seen in a real video).
    */
   ( function()
   {
      var rnd = synthRandom( 9 ), gauss = function() { var u = Math.max( 1e-12, rnd() ), v = rnd(); return Math.sqrt( -2*Math.log( u ) )*Math.cos( 2*Math.PI*v ); };
      var T = [], sigma = 0.001;
      for ( var r = 0; r < 400; ++r ) T.push( r < 5 ? 1 : 25/( r*r ) );          // the image's falloff (1 at r = 5)
      var e = [], start = 10;
      for ( var k = 0; k < 300; ++k )
      {
         var r = start + k;
         e.push( 0.2*T[r] + ( k >= 80 && k < 150 ? 0.05 : 0 ) + 1.22*sigma*gauss() );   // a borrowed bar from 80 to 150
      }
      var f = Fly.fitSpike( e, T, start, sigma );
      check( "a spike's scale comes from its inner part (" + f.scale.toFixed( 3 ) + " of 0.2)", Math.abs( f.scale - 0.2 ) < 0.02, true );
      // 0.2*25/r^2 stays above the averaged threshold (3*1.22*sigma/sqrt(17)) out to r ~ 75, k ~ 65
      check( "borrowed light further out does not lengthen it (" + f.reach + ")", f.reach >= 58 && f.reach <= 70, true );
      check( "its amplitude follows the falloff", Math.abs( f.amp[20] - 0.2*T[30] ) < 0.02*T[30] + 1e-4, true );
      var none = [];
      for ( k = 0; k < 300; ++k ) none.push( 1.22*sigma*gauss() );
      check( "noise is no spike", Fly.fitSpike( none, T, start, sigma ).reach, -1 );
   } )();

   /*
    * The dialog opens without an image and the image is chosen in it -- a
    * list of the open images, and Open... for a file -- rather than a file
    * picker first. Choosing another image starts over.
    */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var dir = synthDir( "fly-choose" ), fx = flyTestScene( dir ), other = null, dlg = null;
      try
      {
         dlg = new FlyThrough.Dialog( null );
         check( "the dialog opens with no image", dlg.imageWindow == null, true );
         check( "with nothing to render yet", [ dlg.draftButton.enabled, dlg.renderButton.enabled ], [ false, false ] );
         check( "and no Analyse button: choosing an image analyses it", dlg.analyseButton === undefined, true );
         check( "it has a list of the open images and an Open button", !!dlg.imageList && !!dlg.openButton, true );
         dlg.setImage( fx.image );
         check( "choosing an image enables it", [ dlg.draftButton.enabled, dlg.renderButton.enabled ], [ true, true ] );
         dlg.orientationCombo.currentItem = 1;
         dlg.orientationCombo.onItemSelected( 1 );
         check( "Vertical reaches the render options and the preset labels", [ dlg.options().orientation, dlg.presetChecks.youtube_1080.text.indexOf( "1080×1920" ) >= 0 ], [ "vertical", true ] );
         dlg.orientationCombo.currentItem = 0;
         dlg.orientationCombo.onItemSelected( 0 );
         check( "and names it", dlg.imageLabel.text.indexOf( fx.image.mainView.id ) >= 0, true );
         // changing the orientation re-drafts, so the preview shows the new frame
         var redrafted = 0, realDraft = dlg.draft;
         dlg.draft = function() { ++redrafted; };
         dlg.hasDraft = true;
         dlg.orientationCombo.currentItem = 1; dlg.orientationCombo.onItemSelected( 1 );
         check( "changing the orientation re-drafts the preview", redrafted, 1 );
         dlg.orientationCombo.currentItem = 0; dlg.orientationCombo.onItemSelected( 0 );
         dlg.draft = realDraft; dlg.hasDraft = false;
         // the star tool is remembered
         var savedTool = Settings.read( FlyThrough.TOOL_SETTING, DataType_String );
         try
         {
            if ( dlg.tools.length > 1 )
            {
               dlg.toolCombo.currentItem = 1; dlg.toolCombo.onItemSelected( 1 );
               var again = new FlyThrough.Dialog( null );
               try { check( "the star tool is remembered", again.toolCombo.currentItem, 1 ); }
               finally { again.release(); }
            }
            else check( "the star tool setting is written", ( dlg.toolCombo.onItemSelected( 0 ), Settings.read( FlyThrough.TOOL_SETTING, DataType_String ) ), dlg.tools[0] );
         }
         finally { if ( savedTool != null ) Settings.write( FlyThrough.TOOL_SETTING, DataType_String, savedTool ); else Settings.remove( FlyThrough.TOOL_SETTING ); }
         var still = dlg.player.frames[0];
         check( "and shows it in the preview until there is a draft", dlg.player.frames.length == 1 && still.width <= FlyThrough.STILL_LONG && still.width >= fx.image.mainView.image.width/Math.ceil( fx.image.mainView.image.width/FlyThrough.STILL_LONG ) - 1, true );
         dlg.id = { stale: true };
         other = new ImageWindow( 64, 48, 3, 32, true, true, Util.freeWindowId( "fly_other" ) );
         dlg.setImage( other );
         check( "choosing another image starts over", [ dlg.imageWindow === other, dlg.id, dlg.work ], [ true, null, null ] );
         check( "and asks for its solve hints (it has no solution)", dlg.needsHints, true );
      }
      finally
      {
         if ( dlg ) dlg.release();
         if ( other ) other.forceClose();
         fx.windows.forEach( function( w ) { w.forceClose(); } );
      }
      var src = File.readTextFile( LOOM_DIR + "/FlyThrough.js" );
      var main = src.substring( src.indexOf( "function main()" ) );
      check( "main opens the dialog, not a file picker", main.indexOf( "OpenFileDialog" ), -1 );
   } )();

   /*
    * Choosing an image gets it ready by itself: analysed (solved, target,
    * distance) and its stars extracted, so the next step is Draft or
    * Render. A nebula whose distance is not known stops after the analysis
    * and asks for it.
    */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var dir = synthDir( "fly-auto" ), fx = flyTestScene( dir ), dlg = null;
      try
      {
         dlg = new FlyThrough.Dialog( null );
         dlg.tools = dlg.tools.length ? dlg.tools : [ "StarNet2" ];
         var calls = [];
         dlg.analyse = function() { calls.push( "analyse" ); this.id = {}; this.typeCombo.currentItem = 1; };   // a galaxy
         dlg.prepare = function() { calls.push( "prepare" ); return {}; };
         dlg.makeDraft = function() { calls.push( "draft" ); };
         dlg.chooseImage( fx.image );
         check( "choosing an image schedules getting it ready", dlg.autoPending, true );
         check( "and outputs next to the image by default", File.fullPath( dlg.folderEdit.text ), File.fullPath( File.extractDrive( fx.image.filePath ) + File.extractDirectory( fx.image.filePath ) ) );
         var note = dlg.autoPrepare();
         check( "it analyses, extracts the stars, then plays a draft", calls, [ "analyse", "prepare", "draft" ] );
         check( "and says it is ready (" + note + ")", /ready/i.test( note ), true );
         calls = [];
         dlg.draft = function() { calls.push( "draft on play" ); };
         dlg.playButton.onClick();
         check( "Play with no draft yet makes one", calls, [ "draft on play" ] );
         calls = [];
         calls = [];
         dlg.analyse = function() { calls.push( "analyse" ); this.id = {}; this.typeCombo.currentItem = 0; this.distanceEdit.text = ""; };
         note = dlg.autoPrepare();
         check( "a nebula without a distance stops after the analysis", calls, [ "analyse" ] );
         check( "and asks for it (" + note + ")", /distance/i.test( note ), true );
         // an unsolved image with nothing to solve from waits for its hints, without an error
         var bare = new ImageWindow( 64, 48, 3, 32, true, true, Util.freeWindowId( "fly_bare" ) );
         var savedFocal = Settings.read( FlyThrough.FOCAL_SETTING, DataType_String ), savedPixel = Settings.read( FlyThrough.PIXEL_SETTING, DataType_String );
         try
         {
            calls = [];
            dlg.chooseImage( bare );
            dlg.raEdit.text = dlg.decEdit.text = dlg.objectEdit.text = "";
            note = dlg.autoPrepare();
            check( "an image with nothing to solve from is not analysed yet", calls, [] );
            check( "the status asks for the hints (" + note + ")", /object/i.test( note ) && /focal/i.test( note ), true );
            dlg.autoPending = false;
            dlg.objectEdit.text = "Elephant Trunk"; dlg.lookUpObject();
            check( "the Object box finds a name and fills the centre (" + dlg.objectMatch.text + ")", [ /IC1396A/.test( dlg.objectMatch.text ), dlg.raEdit.text, dlg.decEdit.text ], [ true, "324.0000", "57.5000" ] );
            dlg.focalEdit.text = "400"; dlg.pixelEdit.text = "3.76";
            dlg.hintsEdited();
            check( "complete hints start it by themselves", dlg.autoPending, true );
            dlg.chooseImage( bare );
            check( "the focal length and pixel size are remembered", [ dlg.focalEdit.text, dlg.pixelEdit.text ], [ "400", "3.76" ] );
         }
         finally
         {
            bare.forceClose();
            // the user's own remembered rig is theirs: put it back
            if ( savedFocal != null ) Settings.write( FlyThrough.FOCAL_SETTING, DataType_String, savedFocal ); else Settings.remove( FlyThrough.FOCAL_SETTING );
            if ( savedPixel != null ) Settings.write( FlyThrough.PIXEL_SETTING, DataType_String, savedPixel ); else Settings.remove( FlyThrough.PIXEL_SETTING );
         }
      }
      finally
      {
         if ( dlg ) dlg.release();
         fx.windows.forEach( function( w ) { w.forceClose(); } );
      }
   } )();

   /*
    * The Object box finds what a person types: catalogue ids however they
    * are spaced, Messier numbers, common names -- the catalogue's own and a
    * table of popular ones it lacks -- with typos forgiven.
    */
   ( function()
   {
      var csv = "id,alpha,delta,magnitude,diameter,axisRatio,posAngle,Common name,PGC,PGC2,Messier\n" +
                "NGC7000,314.695833,44.330000,,120.00,,,North America Nebula,,,\n" +
                "IC5070,312.750000,44.366667,,80.00,,,Pelican Nebula,,,\n" +
                "IC1396,324.745000,57.514000,3.50,170.00,,,,,,\n" +
                "NGC5907,228.973696,56.328850,11.40,11.22,7.96,156,,PGC54470,,\n" +
                "NGC224,10.684708,41.268750,4.36,177.83,3.09,35,Andromeda Galaxy,PGC2557,,M31\n";
      var entries = Fly.parseNgcIc( csv );
      var top = function( q ) { var r = Fly.findObject( q, entries ); return r.length ? r[0].id : null; };
      check( "an id however it is spaced", [ top( "NGC 5907" ), top( "ngc5907" ), top( " ic 1396 " ) ], [ "NGC5907", "NGC5907", "IC1396" ] );
      check( "a Messier number", [ top( "M31" ), top( "m 31" ), top( "Messier 31" ) ], [ "NGC224", "NGC224", "NGC224" ] );
      check( "the catalogue's common names", [ top( "North America" ), top( "pelican nebula" ), top( "andromeda" ) ], [ "NGC7000", "IC5070", "NGC224" ] );
      check( "popular names it lacks", [ top( "Elephant Trunk" ), top( "elephant's trunk nebula" ), top( "Splinter galaxy" ) ], [ "IC1396A", "IC1396A", "NGC5907" ] );
      check( "with typos forgiven", [ top( "elefant trunk" ), top( "north amerika" ), top( "andromida" ) ], [ "IC1396A", "NGC7000", "NGC224" ] );
      var trunk = Fly.findObject( "Elephant Trunk", entries )[0];
      check( "a named part of a larger object has its own position", Fly.separation( trunk, { ra: 324.0, dec: 57.5 } ) < 0.2, true );
      check( "and says what it is", /elephant/i.test( trunk.name ), true );
      check( "nonsense finds nothing", Fly.findObject( "qwertyuiop", entries ).length, 0 );
   } )();

   /* Vertical turns the widescreen presets portrait (their folders apart); social and square stay as they are. */
   ( function()
   {
      var v = Fly.presetSpec( "youtube_1080", "vertical" ), h = Fly.presetSpec( "youtube_1080", "horizontal" );
      check( "vertical 1080p is 1080x1920", [ v.w, v.h ], [ 1080, 1920 ] );
      check( "in a folder of its own", v.id != h.id && /vertical/.test( v.id ), true );
      check( "horizontal is the default", [ Fly.presetSpec( "youtube_4k" ).w, Fly.presetSpec( "youtube_4k" ).h, Fly.presetSpec( "youtube_4k" ).id ], [ 3840, 2160, "youtube_4k" ] );
      check( "the exhibition loop turns too, and still loops", [ Fly.presetSpec( "exhibition", "vertical" ).w, Fly.presetSpec( "exhibition", "vertical" ).pingPong ], [ 2160, true ] );
      check( "social presets keep their shape", [ Fly.presetSpec( "social_vertical", "horizontal" ).w, Fly.presetSpec( "social_square", "vertical" ).w ], [ 1080, 1080 ] );
   } )();

   /*
    * A crossfade loop: the clip plays forward, and over its first F frames
    * fades from the end of a flight F frames longer into its start, so the
    * last frame runs on into the first -- no reversal, no jump.
    */
   ( function()
   {
      var n = 100, F = 20, L = n + F, T = function( j ) { return j/( L - 1 ); };
      var near = function( a, b, tol ) { return Math.abs( a - b ) <= tol; };
      var f0 = Fly.loopFrame( 0, n, F ), fl = Fly.loopFrame( n - 1, n, F ), f10 = Fly.loopFrame( 10, n, F ), fF = Fly.loopFrame( F, n, F );
      check( "the first frame is the flight's next step after the last", [ f0.alpha, near( f0.b, T( n ), 1e-12 ), near( fl.a, T( n - 1 ), 1e-12 ), fl.alpha ], [ 0, true, true, 1 ] );
      check( "halfway through the fade it is half and half", [ f10.alpha, near( f10.a, T( 10 ), 1e-12 ), near( f10.b, T( 10 + n ), 1e-12 ) ], [ 0.5, true, true ] );
      check( "after the fade it is the flight alone", [ fF.alpha, fF.b ], [ 1, null ] );
      check( "the fade is 2 s, or a quarter of a short clip", [ Fly.crossfadeFrames( 20, 30 ), Fly.crossfadeFrames( 4, 30 ) ], [ 60, 30 ] );
      var cf = Fly.presetSpec( "exhibition", "horizontal", "crossfade" ), pp = Fly.presetSpec( "exhibition" );
      check( "the exhibition loop can crossfade instead of going back and forth", [ cf.crossfade, cf.pingPong, pp.pingPong, !!pp.crossfade ], [ true, false, true, false ] );
      check( "a crossfade loop is as long as the clip", Fly.frameCount( 20, 30, cf.pingPong ), 600 );
      check( "the flight time between frames", [ Fly.frameStep( 11, 0, false ), Fly.frameStep( 10, 0, true ), Fly.frameStep( 100, 21, false ) ], [ 0.1, 0.2, 1/120 ] );
      check( "a preset keeps its own name for its settings whatever its folder", Fly.presetSpec( "exhibition", "vertical" ).preset, "exhibition" );
   } )();

   /* A crossfade-loop frame is the two frames mixed (Render.blend); outside the fade it is the flight's own frame. */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var near = function( p, q, tol ) { return Math.abs( p - q ) <= tol; };
      var a = new Image( 4, 2, 3 ), b = new Image( 4, 2, 3 );
      try
      {
         a.fill( 0.8 ); b.fill( 0.2 );
         Render.blend( a, b, 0.25 );
         check( "a blend mixes alpha of the first with the rest of the second", near( a.sample( 1, 1, 2 ), 0.25*0.8 + 0.75*0.2, 1e-6 ), true );
      }
      finally { a.free(); b.free(); }
      var dir = synthDir( "fly-xfade" ), fx = flyTestScene( dir );
      try
      {
         var spec = { id: "x", preset: "x", w: 160, h: 90, pingPong: false, crossfade: true }, o = { travel: 150, easing: "linear", growth: 0.15, brightening: true, duration: 1, fps: 10,
                    output: Fly.outputTransform( Fly.SRGB_COLOUR, "sdr" ) };
         var ps = Render.sceneFor( fx.scene, spec.w, spec.h ), crop = Fly.presetCrop( ps.w, ps.h, ps.tp.x, ps.tp.y, spec.w, spec.h );
         var n = 10, F = 3, first = FlyThrough.loopImage( ps, 0, n, F, o, spec.w, spec.h, crop ), end = Render.frame( ps, Fly.loopFrame( 0, n, F ).b, o, spec.w, spec.h, crop );
         var late = FlyThrough.loopImage( ps, 5, n, F, o, spec.w, spec.h, crop ), own = Render.frame( ps, Fly.loopFrame( 5, n, F ).a, o, spec.w, spec.h, crop );
         var diff = function( p, q ) { var m = 0; for ( var y = 0; y < p.height; y += 7 ) for ( var x = 0; x < p.width; x += 7 ) m = Math.max( m, Math.abs( p.sample( x, y, 0 ) - q.sample( x, y, 0 ) ) ); return m; };
         check( "the loop's first frame is the flight past its end", diff( first, end ) < 1e-6, true );
         check( "after the fade a frame is the flight's own", diff( late, own ) < 1e-6, true );
         [ first, end, late, own ].forEach( function( i ) { i.free(); } );
      }
      finally { fx.windows.forEach( function( w ) { w.forceClose(); } ); }
   } )();

   /*
    * Stars up close. A sprite magnified whole looked like a soft blob: a
    * star is a point, and what grows as it nears is its glow and spikes.
    * The core stays at its size (brighter); only light beyond it is
    * stretched outward (Fly.radialSource). Motion blur spreads a star over
    * the path it covers while the shutter is open (Fly.shutterSteps).
    * Twinkle -- not physical, space has no air -- is a slow per-star wobble
    * that is exactly 1 at the first frame (Fly.twinkle).
    */
   ( function()
   {
      check( "inside the core a sprite is not magnified", Fly.radialSource( 3, 5, 2 ), 3 );
      check( "beyond it, light is stretched outward", Fly.radialSource( 25, 5, 2 ), 15 );
      check( "at growth 1 nothing moves", Fly.radialSource( 25, 5, 1 ), 25 );
      check( "a still star is drawn once", Fly.shutterSteps( 0.2 ), 1 );
      check( "a fast one along its path, a step per ~0.75 px", Fly.shutterSteps( 6 ), 8 );
      check( "at most 12 steps", Fly.shutterSteps( 100 ), 12 );
      var w = [], seed = 17;
      for ( var k = 0; k <= 300; ++k ) w.push( Fly.twinkle( seed, k/30, 0.03, 0 ) );
      check( "twinkle is exactly 1 at the first frame", w[0], 1 );
      var lo = Math.min.apply( null, w ), hi = Math.max.apply( null, w );
      check( "and wobbles by a few percent (" + lo.toFixed( 3 ) + ".." + hi.toFixed( 3 ) + ")", lo > 0.9 && hi < 1.1 && hi - lo > 0.02, true );
      var jump = 0;
      for ( k = 1; k < w.length; ++k ) jump = Math.max( jump, Math.abs( w[k] - w[k - 1] ) );
      check( "slowly: never more than 1% between frames at 30 fps (" + jump.toFixed( 4 ) + ")", jump < 0.01, true );
      check( "each star its own", Fly.twinkle( 1, 2.5, 0.03, 0 ) != Fly.twinkle( 2, 2.5, 0.03, 0 ), true );
      check( "the colours shimmer a little apart", Fly.twinkle( 1, 2.5, 0.03, 0 ) != Fly.twinkle( 1, 2.5, 0.03, 2 ), true );
      check( "and 0 is physical", Fly.twinkle( 1, 2.5, 0, 0 ), 1 );
   } )();

   /* Drawing a near star: the core keeps its size, light beyond it is stretched outward. */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var R = 20, rw = 2*R + 1, patch = new Float32Array( rw*rw );
      for ( var y = 0; y < rw; ++y ) for ( var x = 0; x < rw; ++x )
      {
         var r2 = ( x - R )*( x - R ) + ( y - R )*( y - R );
         patch[y*rw + x] = Math.exp( -r2/( 2*1.5*1.5 ) ) + 0.05*Math.exp( -Math.sqrt( r2 )/6 );    // a core and a glow
      }
      var sp = { rect: { x0: 0, y0: 0, x1: rw, y1: rw }, det: { x: R, y: R } }, W = 161, cam = { x: 0, y: 0, fx: 1, fy: 1 };
      var drawn = function( g ) { var acc = new Float32Array( W*W ); Render.drawSprite( acc, W, W, patch, sp, 80, 80, g, 1, cam, 1, 4 ); return acc; };
      var one = drawn( 1 ), three = drawn( 3 );
      var halfWidth = function( acc ) { var peak = acc[80*W + 80], x = 80; while ( acc[80*W + x] > peak/2 ) ++x; return x - 80; };
      check( "grown 3x, the core keeps its width (" + halfWidth( one ) + " vs " + halfWidth( three ) + ")", halfWidth( three ), halfWidth( one ) );
      var src = Render.patchSample( patch, rw, rw, R + Fly.radialSource( 16, 4, 3 ), R );
      check( "and light 16 px out comes from the stretched radius", Math.abs( three[80*W + 96] - src ) < 1e-4, true );
   } )();

   /* Motion blur spreads a fast star along its path and keeps its light; frame 0 stays the image. */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var dir = synthDir( "fly-blur" ), fx = flyTestScene( dir );
      try
      {
         var o = { travel: 400, easing: "linear", growth: 0.15, brightening: false, duration: 2, fps: 10, frameDt: 1/19 };
         var W = 320, H = 180, crop = Fly.presetCrop( fx.scene.w, fx.scene.h, fx.scene.tp.x, fx.scene.tp.y, W, H );
         var still = Render.frame( fx.scene, 0.8, Object.assign( {}, o, { motionBlur: false } ), W, H, crop );
         var blur = Render.frame( fx.scene, 0.8, Object.assign( {}, o, { motionBlur: true } ), W, H, crop );
         var sum = function( img ) { var t = 0; for ( var y = 0; y < H; ++y ) for ( var x = 0; x < W; ++x ) t += img.sample( x, y, 0 ); return t; };
         var diff = 0;
         for ( var y = 0; y < H; y += 3 ) for ( var x = 0; x < W; x += 3 ) diff = Math.max( diff, Math.abs( still.sample( x, y, 0 ) - blur.sample( x, y, 0 ) ) );
         check( "motion blur changes a fast frame", diff > 1e-3, true );
         check( "and keeps its light (" + ( sum( blur )/sum( still ) ).toFixed( 3 ) + ")", Math.abs( sum( blur )/sum( still ) - 1 ) < 0.02, true );
         still.free(); blur.free();
         var f0 = Render.frame( fx.scene, 0, Object.assign( {}, o, { motionBlur: true, twinkle: 0.05, bloom: 1 } ), W, H, crop );
         var p0 = Render.frame( fx.scene, 0, Object.assign( {}, o, { motionBlur: false, twinkle: 0 } ), W, H, crop );
         var d0 = 0;
         for ( y = 0; y < H; y += 2 ) for ( x = 0; x < W; x += 2 ) d0 = Math.max( d0, Math.abs( f0.sample( x, y, 0 ) - p0.sample( x, y, 0 ) ) );
         check( "with blur, twinkle and bloom, frame 0 is untouched (" + d0.toExponential( 1 ) + ")", d0 < 1e-6, true );
         f0.free(); p0.free();
      }
      finally { fx.windows.forEach( function( w ) { w.forceClose(); } ); }
   } )();

   /*
    * Nothing in front of the backdrop spreads out slower than it. A distant
    * star barely moves by parallax; with the backdrop zooming by K under it,
    * it lagged, as if it were behind the galaxy (seen in a real video). Its
    * spread from the target is at least K, and its size too.
    */
   ( function()
   {
      var tp = { x: 100, y: 100 };
      var far = Fly.screenPosition( { x: 150, y: 100 }, { x: 150, y: 100 }, tp, 1.2 );
      check( "a star with no parallax spreads with the backdrop", [ far.x, far.y ], [ 160, 100 ] );
      var near = Fly.screenPosition( { x: 200, y: 100 }, { x: 150, y: 100 }, tp, 1.2 );
      check( "a near star that spreads faster keeps its own motion", [ near.x, near.y ], [ 200, 100 ] );
      var still = Fly.screenPosition( { x: 150, y: 100 }, { x: 150, y: 100 }, tp, 1 );
      check( "with a still backdrop nothing changes", [ still.x, still.y ], [ 150, 100 ] );
      var centre = Fly.screenPosition( { x: 100, y: 100 }, { x: 100, y: 100 }, tp, 1.5 );
      check( "the target itself stays put", [ centre.x, centre.y ], [ 100, 100 ] );
   } )();

   /* The draft is small and quick: a quarter of the video's frame rate, Fly.DRAFT_LONG px on its long side. */
   ( function()
   {
      var p = Fly.draftPlan( 20, 30, Fly.DRAFT_LONG, 16/9 );
      check( "a 20 s, 30 fps draft is 150 frames at 7.5 fps", [ p.frames, p.fps ], [ 150, 7.5 ] );
      check( "a 60 fps video drafts at 15", Fly.draftPlan( 10, 60, Fly.DRAFT_LONG, 16/9 ).fps, 15 );
      check( "at 480 px on its long side", [ Fly.DRAFT_LONG, p.width ], [ 480, 480 ] );
   } )();

   /*
    * Shrunk far down (a draft is ~1/8 of the working image), a sprite was
    * averaged from 8x8 samples per output pixel, and a draft frame took
    * seconds of sprite drawing (measured 17 s at the real size). It is drawn
    * from a pre-shrunk copy (a mip level) instead, a sample or two a pixel,
    * with the same light and look.
    */
   ( function()
   {
      var R = 60, rw = 2*R + 1, patch = new Float32Array( rw*rw );
      for ( var y = 0; y < rw; ++y ) for ( var x = 0; x < rw; ++x )
      {
         var r2 = ( x - R )*( x - R ) + ( y - R )*( y - R );
         patch[y*rw + x] = Math.exp( -r2/( 2*3*3 ) ) + 0.05*Math.exp( -Math.sqrt( r2 )/15 );
      }
      var sp = { rect: { x0: 0, y0: 0, x1: rw, y1: rw }, det: { x: R, y: R } }, W = 64, cam = { x: 0, y: 0, fx: 8, fy: 8 };
      var draw = function( mip ) { var acc = new Float32Array( W*W ); Render.drawSprite( acc, W, W, patch, sp, 250, 250, 1.2, 1, cam, 0.8, 8, { mip: mip } ); return acc; };
      var a = draw( false ), b = draw( true ), sa = 0, sb = 0, worst = 0, peak = 0;
      for ( var i = 0; i < a.length; ++i ) { sa += a[i]; sb += b[i]; worst = Math.max( worst, Math.abs( a[i] - b[i] ) ); peak = Math.max( peak, a[i] ); }
      check( "a mip-drawn sprite keeps its light (" + ( sb/sa ).toFixed( 3 ) + ")", Math.abs( sb/sa - 1 ) < 0.03, true );
      // a little softer at the sharpest pixel (19% measured): only shrinks of 4x and more (drafts) use mips
      check( "and its look (worst " + ( worst/peak ).toFixed( 3 ) + " of the peak)", worst/peak < 0.22, true );
      check( "mip levels stop one halving short of an output pixel", [ Render.mipLevel( 8, 1 ), Render.mipLevel( 3, 1 ), Render.mipLevel( 8, 2 ) ], [ 2, 0, 1 ] );
   } )();

   /*
    * A logo, placed and spaced by itself for any frame: its long side a
    * fifth of the frame's short side, a 4% title-safe margin, in one of
    * seven places.
    */
   ( function()
   {
      var r = Fly.logoRect( 1920, 1080, 400, 200, "bottom-right" );
      check( "sized to a fifth of the short side", [ r.w, r.h ], [ 216, 108 ] );
      check( "in the bottom right, a 4% margin in", [ r.x, r.y ], [ 1920 - 43 - 216, 1080 - 43 - 108 ] );
      var c = Fly.logoRect( 1920, 1080, 400, 200, "center" );
      check( "centred", [ c.x, c.y ], [ ( 1920 - 216 )/2, ( 1080 - 108 )/2 ] );
      var tm = Fly.logoRect( 1080, 1920, 100, 300, "top-mid" );
      check( "a tall logo in a vertical frame, top middle", [ tm.h, tm.w, tm.x, tm.y ], [ 216, 72, ( 1080 - 72 )/2, 43 ] );
      check( "the seven places", Fly.LOGO_PLACES.length, 7 );
      check( "off is nowhere", Fly.logoRect( 1920, 1080, 400, 200, "off" ), null );
   } )();

   /*
    * Music under the video: looped if shorter, cut to the video, faded in
    * and out (2 s, a quarter of a short clip) unless that is turned off.
    */
   ( function()
   {
      var a = Fly.ffmpegArgs( "/f", 30, "/out/v", "h264", "high", null, { path: "/m/song.mp3", fade: true, duration: 20 } ), s = a.join( " " );
      check( "the music is a second input, looped", /-stream_loop -1 -i \/m\/song\.mp3/.test( s ), true );
      check( "video from the frames, audio from the music", /-map 0:v -map 1:a/.test( s ), true );
      check( "faded in and out", s.indexOf( "afade=t=in:st=0:d=2,afade=t=out:st=18:d=2" ) >= 0, true );
      check( "AAC in an MP4, cut to the video", /-c:a aac/.test( s ) && /-shortest/.test( s ), true );
      check( "the output is still last", a[a.length - 1], "/out/v.mp4" );
      var w = Fly.ffmpegArgs( "/f", 30, "/out/v", "vp9", "high", null, { path: "/m/song.mp3", fade: false, duration: 4 } ).join( " " );
      check( "Opus in a WebM, no fade when it is off", /-c:a libopus/.test( w ) && w.indexOf( "afade" ) < 0, true );
      check( "a short clip fades over a quarter of it", Fly.audioFade( 4 ), 1 );
      check( "no music, no audio", Fly.ffmpegArgs( "/f", 30, "/out/v", "h264", "high", null ).join( " " ).indexOf( "-map" ), -1 );
   } )();

   /* The logo is composited into every frame, its transparency kept, and nowhere else. */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var logo = new Image( 40, 20, 4, ColorSpace_RGB, 32, SampleType_Real );     // RGB + alpha
      var dir = synthDir( "fly-logo" ), fx = flyTestScene( dir );
      try
      {
         logo.fill( 1 );
         for ( var y = 0; y < 20; ++y ) for ( var x = 0; x < 5; ++x ) logo.setSample( 0, x, y, 3 );   // a transparent left edge
         var W = 320, H = 180, layer = Render.logoLayer( logo, W, H, "bottom-right" ), r = Fly.logoRect( W, H, 40, 20, "bottom-right" );
         check( "the layer sits where Fly.logoRect puts it", [ layer.x, layer.y, layer.w, layer.h ], [ r.x, r.y, r.w, r.h ] );
         var crop = Fly.presetCrop( fx.scene.w, fx.scene.h, fx.scene.tp.x, fx.scene.tp.y, W, H ), o = { travel: 150, easing: "smoothstep", growth: 0.15, brightening: true, duration: 2 };
         var plain = Render.frame( fx.scene, 0.5, o, W, H, crop ), marked = Render.frame( fx.scene, 0.5, Object.assign( {}, o, { logo: layer } ), W, H, crop );
         var cx = Math.round( r.x + 0.75*r.w ), cy = Math.round( r.y + r.h/2 ), ex = Math.round( r.x + 1 );
         check( "where the logo is opaque it shows", marked.sample( cx, cy, 0 ) > 0.99, true );
         check( "where it is transparent the frame does", Math.abs( marked.sample( ex, cy, 0 ) - plain.sample( ex, cy, 0 ) ) < 1e-6, true );
         check( "and outside it nothing changes", Math.abs( marked.sample( 10, 10, 0 ) - plain.sample( 10, 10, 0 ) ) < 1e-9, true );
         plain.free(); marked.free();
      }
      finally { logo.free(); fx.windows.forEach( function( w ) { w.forceClose(); } ); }
   } )();

   /* A render with a logo draws it into its frames; with music, the ffmpeg command carries it, faded. */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var dir = synthDir( "fly-brand" ), fx = flyTestScene( dir ), logo = new Image( 30, 30, 3, ColorSpace_RGB, 32, SampleType_Real );
      try
      {
         logo.fill( 1 );
         var spec = { id: "brand", w: 160, h: 90, pingPong: false };
         var opts = { travel: 150, easing: "smoothstep", growth: 0.15, brightening: true, duration: 0.5, fps: 10, video: false,
                      logoImage: logo, logoPlace: "top-left", music: { path: "/music/song.m4a", fade: true } };
         var res = FlyThrough.renderFinal( fx.scene, [ spec ], opts, dir, {} );
         check( "the music is in the ffmpeg command, faded", res.commands[0].indexOf( "/music/song.m4a" ) >= 0 && res.commands[0].indexOf( "afade" ) >= 0, true );
         var w = ImageWindow.open( dir + "/brand/frame_00002.tif" )[0], r = Fly.logoRect( 160, 90, 30, 30, "top-left" );
         check( "and the logo is in the frames", w.mainView.image.sample( Math.round( r.x + r.w/2 ), Math.round( r.y + r.h/2 ), 0 ) > 0.98, true );
         w.forceClose();
      }
      finally { logo.free(); fx.windows.forEach( function( w ) { w.forceClose(); } ); }
      var savedPlace = Settings.read( FlyThrough.LOGO_PLACE_SETTING, DataType_String ), savedOptions = Settings.read( FlyThrough.OPTIONS_SETTING, DataType_String );
      Settings.remove( FlyThrough.LOGO_PLACE_SETTING ); Settings.remove( FlyThrough.OPTIONS_SETTING );   // not the user's own choices
      var dlg = new FlyThrough.Dialog( null );
      try
      {
         var o = dlg.options();
         check( "by default: no logo, music fades", [ o.logoPlace, o.music.fade ], [ "off", true ] );
         dlg.logoPlaceCombo.currentItem = Fly.LOGO_PLACES.indexOf( "bottom-right" ) + 1;
         check( "a logo place can be chosen", dlg.options().logoPlace, "bottom-right" );
      }
      finally
      {
         dlg.release();
         [ [ FlyThrough.LOGO_PLACE_SETTING, savedPlace ], [ FlyThrough.OPTIONS_SETTING, savedOptions ] ].forEach( function( s ) { if ( s[1] != null ) Settings.write( s[0], DataType_String, s[1] ); else Settings.remove( s[0] ); } );
      }
   } )();

   /*
    * Spikes come in opposite pairs of equal strength (the support vanes). Fitted
    * one by one, a spike whose inner stretch was masked (a neighbour beside
    * it, a dark lane) dropped out and the star showed three (seen in a real
    * video). A pair takes the stronger side's fit for both.
    */
   ( function()
   {
      var rnd = synthRandom( 13 ), gauss = function() { var u = Math.max( 1e-12, rnd() ), v = rnd(); return Math.sqrt( -2*Math.log( u ) )*Math.cos( 2*Math.PI*v ); };
      var T = [], sigma = 0.001, start = 10, good = [], masked = [], none = [], none2 = [];
      for ( var r = 0; r < 400; ++r ) T.push( r < 5 ? 1 : 25/( r*r ) );
      for ( var k = 0; k < Fly.SPIKE_FIT; ++k )
      {
         good.push( 0.2*T[start + k] + 1.22*sigma*gauss() );
         masked.push( -0.01 + 1.22*sigma*gauss() );            // its surroundings read brighter than the spike
         none.push( 1.22*sigma*gauss() ); none2.push( 1.22*sigma*gauss() );
      }
      var pair = Fly.fitSpikePair( good, masked, T, start, sigma ), alone = Fly.fitSpike( good, T, start, sigma );
      check( "a pair takes the stronger side's fit", Math.abs( pair.scale - alone.scale ) < 1e-12 && pair.reach == alone.reach, true );
      check( "noise on both sides is still no spike", Fly.fitSpikePair( none, none2, T, start, sigma ).reach, -1 );
   } )();

   /*
    * Bloom: a near star's light past white used to clip to a flat, square
    * core. That excess now spreads into a tight glow (a round core) and a
    * wide faint one, whitening as a sensor does. Only light past white
    * blooms, so the first frame -- the image, nothing past white -- is
    * untouched.
    */
   ( function()
   {
      var w = 61, h = 61, one = new Float32Array( w*h );
      for ( var i = 0; i < one.length; ++i ) one[i] = 7;
      var b = Render.boxBlur( one.slice(), w, h, 3 );
      check( "a blur keeps a flat field flat", Math.abs( b[30*w + 30] - 7 ) < 1e-4 && Math.abs( b[0] - 7 ) < 1e-4, true );
      var T = [ new Float32Array( w*h ), new Float32Array( w*h ), new Float32Array( w*h ) ];
      for ( var c = 0; c < 3; ++c ) { T[c][30*w + 30] = c == 0 ? 3 : 1.5; T[c][10*w + 10] = 0.9; }     // an overexposed reddish core; a bright one within white
      var before = T.map( function( a ) { return a.slice(); } );
      Render.bloom( T, w, h, { amount: 1, seconds: 2 } );
      check( "light past white spreads around the core", T[0][30*w + 33] > before[0][30*w + 33] + 0.01, true );
      check( "and whitens it: the weak channels gain too", T[2][30*w + 32] > 0.01, true );
      check( "a star within white does not bloom", Math.abs( T[0][10*w + 12] - before[0][10*w + 12] ) < 1e-9, true );
      var still = [ new Float32Array( w*h ) ];
      still[0][30*w + 30] = 0.95;
      var s0 = still[0].slice();
      Render.bloom( still, w, h, { amount: 1, seconds: 0 } );
      check( "nothing past white, nothing changes", Math.abs( still[0][30*w + 31] - s0[30*w + 31] ) < 1e-12, true );
      var off = before.map( function( a ) { return a.slice(); } );
      Render.bloom( off, w, h, { amount: 0, seconds: 2 } );
      check( "and 0 turns it off", off[0][30*w + 33], before[0][30*w + 33] );
      check( "the wide glow breathes a little over time", Render.bloomSigmas( 1080, 0 )[1] != Render.bloomSigmas( 1080, 1.3 )[1], true );
   } )();

   /*
    * Past white a core whitens (its channels drawn toward its brightest,
    * as a saturating sensor does), and very bright stars carry a very wide,
    * faint glare.
    */
   ( function()
   {
      var w = 201, h = 201, T = [ new Float32Array( w*h ), new Float32Array( w*h ), new Float32Array( w*h ) ];
      T[0][100*w + 100] = 40; T[1][100*w + 100] = 12; T[2][100*w + 100] = 6;    // a very bright reddish core
      Render.bloom( T, w, h, { amount: 1, seconds: 2 } );
      check( "an overexposed core whitens toward its brightest channel", T[2][100*w + 100] > 20, true );
      check( "and a very bright star carries a wide glare", T[1][100*w + 120] > 1e-4, true );
      var sg = Render.bloomSigmas( 1000, 0 );
      check( "the blur radius gives the glow's width (three boxes: sigma^2 = r(r + 1))", Render.boxRadius( 10 ), 10 );
   } )();

   /* Close stars pass sooner: fully there up to 6x nearer, gone by 14x, so no star grows past what it can bear. */
   check( "close stars fade from 6x nearer to 14x", [ Fly.opacity( 6, true ), Fly.opacity( 14, true ), Fly.opacity( 10, true ) > 0 && Fly.opacity( 10, true ) < 1 ], [ 1, 0, true ] );

   /* Up close a star is drawn from its model -- smooth, no noise or processing marks -- cross-faded from its photograph. */
   check( "photo far away, model up close", [ Fly.modelWeight( 1 ), Fly.modelWeight( 2 ), Fly.modelWeight( 4 ), Fly.modelWeight( 3 ) > 0 && Fly.modelWeight( 3 ) < 1 ], [ 0, 0, 1, true ] );

   if ( IN_PIXINSIGHT ) ( function()
   {
      var N = 200, c = 100, rnd = synthRandom( 29 ), win = new ImageWindow( N, N, 1, 32, true, false, Util.freeWindowId( "fly_model" ) );
      try
      {
         var buf = new Float32Array( N*N );
         for ( var y = 0; y < N; ++y ) for ( var x = 0; x < N; ++x )
         {
            var r2 = ( x - c )*( x - c ) + ( y - c )*( y - c ), u = Math.max( 1e-12, rnd() ), v = rnd();
            buf[y*N + x] = Math.max( 0, 0.02 + 0.9*Math.pow( 1 + r2/6, -2.5 ) + 0.004*Math.sqrt( -2*Math.log( u ) )*Math.cos( 2*Math.PI*v ) );
         }
         win.mainView.beginProcess( UndoFlag_NoSwapFile ); win.mainView.image.setSamples( buf ); win.mainView.endProcess();
         var sp = Sky.sprites( win.mainView.image, [ { source: { G: 9, ra: 0, dec: 0 }, d: 100, x: c, y: c } ], 3, [] ).sprites[0];
         check( "a sprite carries its model, the same size", !!sp.modelCore && sp.modelCore[0].length == sp.pixels[0].length && sp.modelSpikes[0].length == sp.pixels[0].length, true );
         var rw = sp.rect.x1 - sp.rect.x0, at = function( a, dx, dy ) { return a[( c + dy - sp.rect.y0 )*rw + c + dx - sp.rect.x0]; };
         check( "its model matches the star's core (" + at( sp.modelCore[0], 0, 0 ).toFixed( 3 ) + " vs " + at( sp.pixels[0], 0, 0 ).toFixed( 3 ) + ")",
                Math.abs( at( sp.modelCore[0], 0, 0 ) - at( sp.pixels[0], 0, 0 ) ) < 0.15, true );
         var d = 0, m = 0;
         for ( var k = 2; k < 7; ++k ) { d += Math.abs( at( sp.pixels[0], k, 0 ) - at( sp.pixels[0], k + 1, 0 ) ); m += Math.abs( at( sp.modelCore[0], k, 0 ) - at( sp.modelCore[0], k + 1, 0 ) ); }
         check( "and is smoother than the photograph in the wings (" + m.toFixed( 4 ) + " vs " + d.toFixed( 4 ) + ")", m < d, true );
      }
      finally { win.forceClose(); }
   } )();

   /*
    * The logo shows in the preview as soon as it is chosen -- on the still
    * of the image, before any draft -- with its transparency kept. Choosing
    * a file with the place still Off puts it bottom right.
    */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var dir = synthDir( "fly-logo-preview" ), fx = flyTestScene( dir ), dlg = null, path = dir + "/logo.tif";
      var savedLogo = Settings.read( FlyThrough.LOGO_SETTING, DataType_String ), savedPlace = Settings.read( FlyThrough.LOGO_PLACE_SETTING, DataType_String );
      var savedOptions = Settings.read( FlyThrough.OPTIONS_SETTING, DataType_String );
      Settings.remove( FlyThrough.OPTIONS_SETTING );          // the user's saved opacity is not this test's
      var logo = new Image( 40, 20, 4, ColorSpace_RGB, 32, SampleType_Real );         // white, its left half transparent
      try
      {
         logo.fill( 1 );
         for ( var y = 0; y < 20; ++y ) for ( var x = 0; x < 20; ++x ) logo.setSample( 0, x, y, 3 );
         var f = new FileFormatInstance( new FileFormat( ".tif", false, true ) );
         f.create( path, "" ); f.writeImage( logo ); f.close();
         dlg = new FlyThrough.Dialog( null );
         dlg.setImage( fx.image );
         var plain = dlg.player.frames[0], bw = plain.width, bh = plain.height;
         dlg.logoPlaceCombo.currentItem = 0;
         dlg.useLogo( path );
         check( "a logo chosen with the place Off goes bottom right", dlg.options().logoPlace, "bottom-right" );
         var shown = dlg.player.frames[0], r = Fly.logoRect( bw, bh, 40, 20, "bottom-right" );
         var px = function( b, x, y ) { return b.pixel( Math.round( x ), Math.round( y ) ) & 0xffffff; };
         check( "the preview shows it at once, opaque part white", px( shown, r.x + 0.75*r.w, r.y + r.h/2 ), 0xffffff );
         check( "and its transparent part shows the image", px( shown, r.x + 0.25*r.w, r.y + r.h/2 ), px( plain, r.x + 0.25*r.w, r.y + r.h/2 ) );
      }
      finally
      {
         if ( dlg ) dlg.release();
         logo.free();
         fx.windows.forEach( function( w ) { w.forceClose(); } );
         [ [ FlyThrough.LOGO_SETTING, savedLogo ], [ FlyThrough.LOGO_PLACE_SETTING, savedPlace ], [ FlyThrough.OPTIONS_SETTING, savedOptions ] ].forEach( function( s ) { if ( s[1] != null ) Settings.write( s[0], DataType_String, s[1] ); else Settings.remove( s[0] ); } );
      }
   } )();

   /*
    * The dialog's options come back next time; the object typed for an
    * image file is remembered for that file, so its solve starts by itself;
    * the logo has an opacity. The user's own saved choices are put back.
    */
   if ( IN_PIXINSIGHT ) ( function()
   {
      // every setting this test can write is the user's own: all of them are put back (the focal length once was not,
      // and the user's solve then ran at 400 mm)
      var keys = [ FlyThrough.OPTIONS_SETTING, FlyThrough.OBJECTS_SETTING, FlyThrough.LOGO_SETTING, FlyThrough.LOGO_PLACE_SETTING,
                   FlyThrough.FOCAL_SETTING, FlyThrough.PIXEL_SETTING ];
      var saved = keys.map( function( k ) { return Settings.read( k, DataType_String ); } );
      var dir = synthDir( "fly-persist" ), bare = null, a = null, b = null;
      try
      {
         keys.forEach( function( k ) { Settings.remove( k ); } );
         a = new FlyThrough.Dialog( null );
         a.durationSpin.value = 37; a.orientationCombo.currentItem = 1; a.loopCombo.currentItem = 1; a.twinkleSpin.value = 7;
         a.bloomSpin.value = 150; a.blurCheck.checked = false; a.presetChecks.social_square.checked = true; a.logoOpacity.value = 60;
         a.musicEdit.text = "/music/x.mp3"; a.fadeCheck.checked = false;
         a.saveOptions(); a.release(); a = null;
         b = new FlyThrough.Dialog( null );
         check( "the options come back next time",
                [ b.durationSpin.value, b.orientationCombo.currentItem, b.loopCombo.currentItem, b.twinkleSpin.value, b.bloomSpin.value, b.blurCheck.checked,
                  b.presetChecks.social_square.checked, b.logoOpacity.value, b.musicEdit.text, b.fadeCheck.checked ],
                [ 37, 1, 1, 7, 150, false, true, 60, "/music/x.mp3", false ] );
         check( "and the opacity reaches the render", b.options().logoOpacity, 0.6 );
         // the object typed for a file is remembered for it
         var path = synthFrame( dir + "/unsolved.xisf", { stars: 20, fwhm: 3, background: 0.05, noise: 0.002, seed: 3 } );
         bare = ImageWindow.open( path )[0];
         b.setImage( bare );
         b.objectEdit.text = "North America"; b.lookUpObject(); b.focalEdit.text = "400"; b.pixelEdit.text = "3.76";
         b.autoPending = false; b.hintsEdited(); b.release(); b = null;
         a = new FlyThrough.Dialog( null );
         a.setImage( bare );
         check( "the object typed for a file comes back with it", [ a.objectEdit.text, a.raEdit.text != "" ], [ "North America", true ] );
         check( "with its focal length and pixel size", [ a.focalEdit.text, a.pixelEdit.text ], [ "400", "3.76" ] );
         // an image never saved to a file is remembered by its name
         a.release(); a = null;
         var unsaved = new ImageWindow( 64, 48, 3, 32, true, true, Util.freeWindowId( "fly_unsaved" ) );
         try
         {
            b = new FlyThrough.Dialog( null );
            b.setImage( unsaved );
            b.objectEdit.text = "Pelican"; b.lookUpObject(); b.focalEdit.text = "805"; b.pixelEdit.text = "4.1";
            b.autoPending = false; b.hintsEdited(); b.release(); b = null;
            Settings.write( FlyThrough.FOCAL_SETTING, DataType_String, "123" );          // another rig used since
            a = new FlyThrough.Dialog( null );
            a.setImage( unsaved );
            check( "an unsaved image's hints come back by its name", [ a.objectEdit.text, a.focalEdit.text, a.pixelEdit.text ], [ "Pelican", "805", "4.1" ] );
         }
         finally { unsaved.forceClose(); }
      }
      finally
      {
         [ a, b ].forEach( function( d ) { if ( d ) d.release(); } );
         if ( bare ) bare.forceClose();
         keys.forEach( function( k, i ) { if ( saved[i] != null ) Settings.write( k, DataType_String, saved[i] ); else Settings.remove( k ); } );
      }
   } )();

   /* The logo's opacity scales its own transparency. */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var logo = new Image( 10, 10, 3, ColorSpace_RGB, 32, SampleType_Real );
      try
      {
         logo.fill( 1 );
         var L = Render.logoLayer( logo, 100, 100, "center", 3, 0.5 );
         check( "a half-opaque logo is half there", [ L.a[5*L.w + 5], L.p[0][5*L.w + 5] ], [ 0.5, 0.5 ] );
      }
      finally { logo.free(); }
   } )();

   /*
    * A star partly hidden -- going out of the frame, or behind a nearer
    * star -- casts spikes and glow in proportion to what is still seen of
    * its core: shorter and fainter, gone with it.
    */
   ( function()
   {
      var near = function( a, b ) { return Math.abs( a - b ) < 0.02; };
      check( "a core wholly in the frame is wholly seen", Fly.discVisible( 50, 50, 5, 100, 100 ), 1 );
      check( "a core on the frame's edge is half seen", near( Fly.discVisible( 0, 50, 5, 100, 100 ), 0.5 ), true );
      check( "on a corner, a quarter", near( Fly.discVisible( 100, 100, 5, 100, 100 ), 0.25 ), true );
      check( "out of the frame, none", Fly.discVisible( -10, 50, 5, 100, 100 ), 0 );
      check( "a core clear of a nearer star is not covered", Fly.coverFraction( 5, 5, 11 ), 0 );
      check( "one inside a bigger nearer star is wholly covered", Fly.coverFraction( 3, 10, 4 ), 1 );
      check( "two equal cores half apart overlap by the lens area", near( Fly.coverFraction( 5, 5, 5 ), 0.391 ), true );
      check( "spikes shorten with what is seen", [ Fly.radialSource( 25, 5, 2, 1 ), Fly.radialSource( 25, 5, 2, 0.5 ) ], [ 15, 25 ] );
   } )();

   /* Drawing a half-hidden star: beyond its core the light comes from further in, at half strength. */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var R = 20, rw = 2*R + 1, patch = new Float32Array( rw*rw );
      for ( var y = 0; y < rw; ++y ) for ( var x = 0; x < rw; ++x )
      {
         var r2 = ( x - R )*( x - R ) + ( y - R )*( y - R );
         patch[y*rw + x] = Math.exp( -r2/( 2*1.5*1.5 ) ) + 0.05*Math.exp( -Math.sqrt( r2 )/6 );
      }
      var sp = { rect: { x0: 0, y0: 0, x1: rw, y1: rw }, det: { x: R, y: R } }, W = 161, cam = { x: 0, y: 0, fx: 1, fy: 1 }, acc = new Float32Array( W*W );
      Render.drawSprite( acc, W, W, patch, sp, 80, 80, 3, 1, cam, 1, 4, { seen: 0.5 } );
      var want = 0.5*Render.patchSample( patch, rw, rw, R + Fly.radialSource( 16, 4, 3, 0.5 ), R );
      check( "a half-seen star's glow is shorter and half as bright", Math.abs( acc[80*W + 96] - want ) < 1e-4, true );
      var core = new Float32Array( W*W );
      Render.drawSprite( core, W, W, patch, sp, 80, 80, 3, 1, cam, 1, 4 );
      check( "its core is as it was", Math.abs( acc[80*W + 80] - core[80*W + 80] ) < 1e-6, true );
   } )();

   /*
    * A saturated star is often a flat square in the photograph (clipped,
    * then processed). Boosted as it nears, that square became what one saw
    * (a real frame, 1.8x nearer). The core turns to the star's round model
    * sooner than the rest (Fly.coreModelWeight), so it comes out round.
    */
   check( "the core turns to the model sooner than the rest", [ Fly.coreModelWeight( 1 ), Fly.coreModelWeight( 2 ), Fly.modelWeight( 2 ) ], [ 0, 1, 0 ] );
   if ( IN_PIXINSIGHT ) ( function()
   {
      var R = 30, rw = 2*R + 1, photo = new Float32Array( rw*rw ), core = new Float32Array( rw*rw ), spikes = new Float32Array( rw*rw );
      for ( var y = 0; y < rw; ++y ) for ( var x = 0; x < rw; ++x )
      {
         var dx = x - R, dy = y - R;
         photo[y*rw + x] = ( Math.abs( dx ) <= 5 && Math.abs( dy ) <= 5 ) ? 0.95 : 0.95*Math.exp( -( Math.max( Math.abs( dx ), Math.abs( dy ) ) - 5 )/2 );   // a square plateau
         core[y*rw + x] = 0.95*Math.min( 1, Math.exp( -( Math.hypot( dx, dy ) - 5 )/2 ) );                                                      // its round model
      }
      var sp = { rect: { x0: 0, y0: 0, x1: rw, y1: rw }, det: { x: R, y: R }, pixels: [ photo ], modelCore: [ core ], modelSpikes: [ spikes ] };
      var W = 121, acc = new Float32Array( W*W ), cam = { x: 0, y: 0, fx: 1, fy: 1 };
      var q = { e: {}, j: 0, sp: sp, m: { ratio: 2 }, g: 1.15, kOuter: 3, kCore: 4, rc: 8, cx: 60, cy: 60, seen: 1 };
      Render.drawPlaced( null, [ acc ], q, 0, { motionBlur: false }, W, W, cam );
      var reach = function( ux, uy ) { var k = 0; while ( acc[Math.round( 60 + k*uy )*W + Math.round( 60 + k*ux )] > 1 ) ++k; return k; };
      var diag = reach( Math.SQRT1_2, Math.SQRT1_2 ), side = reach( 1, 0 );
      check( "a square photographed core comes out round (" + diag + " diagonally, " + side + " across)", Math.abs( diag - side ) <= 1, true );
   } )();

   /* Star saturation: the stars' colour, more or less, their brightness kept; 100% changes nothing. */
   ( function()
   {
      var T = function() { return [ new Float32Array( [ 0.6 ] ), new Float32Array( [ 0.3 ] ), new Float32Array( [ 0.3 ] ) ]; };
      var a = T(); Render.saturateStars( a, 1, 1 );
      check( "100% changes nothing", [ a[0][0], a[1][0], a[2][0] ], [ 0.6000000238418579, 0.30000001192092896, 0.30000001192092896 ] );
      var b = T(); Render.saturateStars( b, 1, 2 );
      check( "200% doubles the colour, brightness kept", [ Math.abs( b[0][0] - 0.8 ) < 1e-6, Math.abs( b[1][0] - 0.2 ) < 1e-6, Math.abs( ( b[0][0] + b[1][0] + b[2][0] )/3 - 0.4 ) < 1e-6 ], [ true, true, true ] );
      var c = T(); Render.saturateStars( c, 1, 0 );
      check( "0% is grey", Math.abs( c[0][0] - c[2][0] ) < 1e-6, true );
   } )();
   if ( IN_PIXINSIGHT ) ( function()
   {
      var saved = Settings.read( FlyThrough.OPTIONS_SETTING, DataType_String ), d = null;
      try
      {
         Settings.remove( FlyThrough.OPTIONS_SETTING );
         d = new FlyThrough.Dialog( null );
         check( "star saturation defaults to 100%", d.options().starSaturation, 1 );
         d.saturationSlider.value = 140;
         check( "and reaches the render", d.options().starSaturation, 1.4 );
      }
      finally { if ( d ) d.release(); if ( saved != null ) Settings.write( FlyThrough.OPTIONS_SETTING, DataType_String, saved ); else Settings.remove( FlyThrough.OPTIONS_SETTING ); }
   } )();

   /* The logo can fade in after N seconds (over one second); 0 shows it from the start. */
   check( "no delay: there from the start", [ Fly.logoFade( 0, 0 ), Fly.logoFade( 5, 0 ) ], [ 1, 1 ] );
   check( "a delay of 3 s: hidden until 3 s, in by 4 s", [ Fly.logoFade( 2.9, 3 ), Fly.logoFade( 4, 3 ), Fly.logoFade( 3.5, 3 ) > 0 && Fly.logoFade( 3.5, 3 ) < 1 ], [ 0, 1, true ] );
   if ( IN_PIXINSIGHT ) ( function()
   {
      var saved = Settings.read( FlyThrough.OPTIONS_SETTING, DataType_String ), d = null;
      try
      {
         Settings.remove( FlyThrough.OPTIONS_SETTING );
         d = new FlyThrough.Dialog( null );
         check( "the logo delay defaults to 0", d.options().logoDelay, 0 );
         d.logoDelaySpin.value = 4;
         check( "and reaches the render", d.options().logoDelay, 4 );
      }
      finally { if ( d ) d.release(); if ( saved != null ) Settings.write( FlyThrough.OPTIONS_SETTING, DataType_String, saved ); else Settings.remove( FlyThrough.OPTIONS_SETTING ); }
   } )();

   /* The information panel: how many stars were detected, how many move, how many stay in the background. */
   check( "the star counts, as the panel shows them", Fly.describeStars( { detected: 5231, placed: 1288, backdrop: 313, blended: 20 } ),
          "Stars: 5231 detected · 1288 moving · 313 in the background · 20 blended (stay still)" );
   check( "no blends, no mention", Fly.describeStars( { detected: 900, placed: 168, backdrop: 73, blended: 0 } ), "Stars: 900 detected · 168 moving · 73 in the background" );

   /*
    * A solve is only believed at a scale the rig can give: its own, or half
    * or a third of it (drizzled). A wider solve of the same image once put
    * the catalogue on the wrong stars (1288 moving stars fell to 189).
    */
   check( "the rig's scale, drizzled 2x or 3x, is plausible", [ Fly.plausibleScale( 2.5, 2.506 ), Fly.plausibleScale( 1.253, 2.506 ), Fly.plausibleScale( 0.84, 2.506 ) ], [ true, true, true ] );
   check( "a scale the rig cannot give is not", [ Fly.plausibleScale( 1.65, 2.506 ), Fly.plausibleScale( 3.4, 2.506 ) ], [ false, false ] );

   /*
    * The cheap core blend (the glow once, a core-sized correction, the
    * model's glow only up close, the spikes on their own) must draw exactly
    * what its layers drawn one by one draw; a real frame showed a
    * shield-shaped core where it did not.
    */
   ( function()
   {
      var R = 30, rw = 2*R + 1, n = rw*rw, photo = new Float32Array( n ), core = new Float32Array( n ), spikes = new Float32Array( n );
      for ( var y = 0; y < rw; ++y ) for ( var x = 0; x < rw; ++x )
      {
         var dx = x - R + 0.3, dy = y - R - 0.2, i = y*rw + x;
         photo[i] = ( Math.abs( dx ) <= 5 && Math.abs( dy ) <= 5 ) ? 0.95 : 0.95*Math.exp( -( Math.max( Math.abs( dx ), Math.abs( dy ) ) - 5 )/3 );
         core[i] = 0.95*Math.min( 1, Math.exp( -( Math.hypot( dx, dy ) - 5 )/3 ) );
         spikes[i] = ( Math.abs( dx - dy ) < 1 && Math.hypot( dx, dy ) > 6 ) ? 0.1 : 0;
      }
      var det = { x: R - 0.3, y: R + 0.2 }, rect = { x0: 0, y0: 0, x1: rw, y1: rw }, rc = 8, W = 121, cam = { x: 0, y: 0, fx: 1.28, fy: 1.28 };
      [ 1.2, 1.8, 3 ].forEach( function( ratio )
      {
         var angles = [ Math.PI/4, 5*Math.PI/4 ];
         var sp = { rect: rect, det: det, pixels: [ photo ], modelCore: [ core ], modelSpikes: [ spikes ], spikeAngles: angles };
         var q = { e: {}, j: 0, sp: sp, m: { ratio: ratio }, g: 1.3, kOuter: 2, kCore: 3.4, rc: rc, cx: 70, cy: 70, seen: 0.8, alpha: 0.9, spikeLength: 2.5 };
         var cheap = new Float32Array( W*W );
         Render.drawPlaced( null, [ cheap ], q, 0, { motionBlur: false }, W, W, cam );
         // the five layers, explicitly
         var wc = Fly.coreModelWeight( ratio ), wo = Fly.modelWeight( ratio ), ref = new Float32Array( W*W );
         var share = function( x, y ) { return 1 - Fly.smoothstep( rc, rc + 3, Math.hypot( x - det.x, y - det.y ) ); };
         var pc = new Float32Array( n ), po = new Float32Array( n ), mc = new Float32Array( n ), mo = new Float32Array( n );
         // the glow is the photograph minus the spikes; the spikes are drawn on their own, along their axes
         for ( y = 0; y < rw; ++y ) for ( x = 0; x < rw; ++x ) { var k = share( x, y ), j = y*rw + x, gl = photo[j] - spikes[j]; pc[j] = k*gl; po[j] = ( 1 - k )*gl; mc[j] = k*core[j]; mo[j] = ( 1 - k )*core[j]; }
         [ [ pc, 1 - wc, q.kCore ], [ mc, wc, q.kCore ], [ po, 1 - wo, q.kCore ], [ mo, wo, q.kCore ] ].forEach( function( l )
         {
            if ( l[1] != 0 ) Render.drawSprite( ref, W, W, l[0], { rect: rect, det: det }, 70, 70, q.g, l[2]*l[1], cam, q.kOuter*l[1], rc, { seen: q.seen } );
         } );
         Render.drawSprite( ref, W, W, spikes, { rect: rect, det: det }, 70, 70, q.g, q.alpha, cam, q.alpha, rc, { seen: q.seen, spike: { length: q.spikeLength, angles: angles } } );
         var worst = 0;
         for ( var i2 = 0; i2 < W*W; ++i2 ) worst = Math.max( worst, Math.abs( cheap[i2] - ref[i2] ) );
         check( "at " + ratio + "x nearer the cheap blend draws the five layers (worst " + worst.toExponential( 1 ) + ")", worst < 1e-4, true );
      } );
   } )();

   /*
    * Turning a scene (a vertical image into a horizontal frame) turns every
    * patch a sprite carries. The model patches were left as they were, so a
    * close star's round core sat rotated against its photograph -- a
    * shield-shaped core (seen in a real render); cached core parts from
    * before the turn must not come along either.
    */
   ( function()
   {
      var rw = 5, rh = 3, a = new Float32Array( rw*rh );
      for ( var i = 0; i < a.length; ++i ) a[i] = i + 1;
      var sp = { rect: { x0: 10, y0: 20, x1: 15, y1: 23 }, det: { x: 12, y: 21 }, source: {}, pixels: [ a ], modelCore: [ a.slice() ], modelSpikes: [ a.slice() ], _split: [ { stale: true } ] };
      var sc = { w: 100, h: 60, nc: 1, S: [ new Float32Array( 6000 ) ], R: [ new Float32Array( 6000 ) ], sprites: [ { s: sp, p0: { x: 12, y: 21 } } ],
                 project: function() { return null; }, tp: { x: 50, y: 30 } };
      var t = Render.rotateScene( sc ).sprites[0].s;
      check( "the model patches turn with the photograph", [ Array.from( t.modelCore[0] ).join(), Array.from( t.modelSpikes[0] ).join() ], [ Array.from( t.pixels[0] ).join(), Array.from( t.pixels[0] ).join() ] );
      check( "and cached core parts from before the turn do not come along", t._split, undefined );
   } )();

   /*
    * Frames are kept when only the encode changes: a render's frames carry
    * the signature of what made them, and the video format, quality, music
    * and ffmpeg are not part of it.
    */
   ( function()
   {
      var o = { travel: 184, easing: "smoothstep", growth: 0.15, bloom: 1, twinkle: 0.03, duration: 20, fps: 30, format: "h264", quality: "high",
                music: { path: "/m/a.mp3", fade: true }, video: true, ffmpeg: "/usr/bin/ffmpeg", dir: "/out", presets: [ "youtube_1080" ] };
      var spec = { id: "youtube_1080", w: 1920, h: 1080, pingPong: false }, key = "image.tif|StarXTerminator|922";
      var base = Fly.frameSignature( o, spec, key );
      var same = function( change ) { return Fly.frameSignature( Object.assign( {}, o, change ), spec, key ) == base; };
      check( "a new format, quality or music keeps the frames", [ same( { format: "hevc" } ), same( { quality: "standard" } ), same( { music: { path: "/m/b.mp3", fade: false } } ), same( { video: false } ) ], [ true, true, true, true ] );
      check( "a new flight, look or length does not", [ same( { travel: 200 } ), same( { bloom: 1.5 } ), same( { duration: 10 } ), same( { fps: 24 } ) ], [ false, false, false, false ] );
      check( "nor does another image or star tool", Fly.frameSignature( o, spec, "other.tif|StarNet2|922" ) == base, false );
      check( "nor another frame size", Fly.frameSignature( o, { id: "youtube_4k", w: 3840, h: 2160, pingPong: false }, key ) == base, false );
   } )();

   /* Rendering again with only the video format changed reuses the frames; changing the flight renders them again. */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var dir = synthDir( "fly-reuse" ), fx = flyTestScene( dir );
      try
      {
         var spec = { id: "reuse", w: 160, h: 90, pingPong: false };
         var o = { travel: 150, easing: "smoothstep", growth: 0.15, brightening: true, duration: 0.5, fps: 10, video: false, format: "h264", sceneKey: "test|tool|900" };
         var first = FlyThrough.renderFinal( fx.scene, [ spec ], o, dir, {} );
         var t0 = ( new FileInfo( dir + "/reuse/frame_00002.tif" ) ).lastModified.getTime();
         var again = FlyThrough.renderFinal( fx.scene, [ spec ], Object.assign( {}, o, { format: "hevc", quality: "standard" } ), dir, {} );
         check( "a new format reuses the frames", [ first.written, again.written, again.reused ], [ 5, 0, 5 ] );
         check( "untouched", ( new FileInfo( dir + "/reuse/frame_00002.tif" ) ).lastModified.getTime(), t0 );
         check( "and still gives the ffmpeg command", again.commands.length, 1 );
         var changed = FlyThrough.renderFinal( fx.scene, [ spec ], Object.assign( {}, o, { growth: 0.3 } ), dir, {} );
         check( "a new flight renders them again", [ changed.written, changed.reused ], [ 5, 0 ] );
         File.writeTextFile( dir + "/reuse/frames.json", "damaged" );
         check( "and a damaged record means rendering again", FlyThrough.renderFinal( fx.scene, [ spec ], Object.assign( {}, o, { growth: 0.3 } ), dir, {} ).written, 5 );
      }
      finally { fx.windows.forEach( function( w ) { w.forceClose(); } ); }
   } )();

   /* A failed solve says what was tried -- focal length, pixel sizes, centre -- and the solver's reason, so a wrong value shows. */
   ( function()
   {
      var m = Fly.solveFailure( { ra: 324, dec: 57.5, focal: 400, pixel: 3.76 }, [ "Initial field alignment failed." ] );
      check( "it names the focal length, pixel sizes and centre", [ /400 mm/.test( m ), /3\.76/.test( m ), /1\.88/.test( m ), /1\.25/.test( m ), /324\.0000/.test( m ) ], [ true, true, true, true, true ] );
      check( "and the solver's reason", /Initial field alignment failed/.test( m ), true );
   } )();

   /*
    * A looping video's music loops too: no fade in or out, the music's end
    * crossfaded into its beginning, so the loop point is seamless.
    */
   ( function()
   {
      var s = Fly.ffmpegArgs( "/f", 30, "/out/v", "hevc", "high", null, { path: "/m/song.mp3", fade: true, duration: 20, loop: true } ).join( " " );
      check( "a loop's music is not faded in or out", /afade=t=in:st=0:d=2,afade=t=out/.test( s ), false );
      check( "its first 2 s mix in the music's next 2 s, fading out", s.indexOf( "atrim=20:22" ) >= 0 && /amix=inputs=2:duration=first/.test( s ), true );
      check( "and the head fades in under it", /atrim=0:20[^;]*afade=t=in:st=0:d=2/.test( s ), true );
      check( "it is the mixed audio that is encoded", /-map \[aout\]/.test( s ), true );
      var plain = Fly.ffmpegArgs( "/f", 30, "/out/v", "hevc", "high", null, { path: "/m/song.mp3", fade: true, duration: 20 } ).join( " " );
      check( "a video that does not loop keeps its fades", /afade=t=in:st=0:d=2,afade=t=out:st=18:d=2/.test( plain ), true );
   } )();

   /* All three scripts live in one Loom folder in PixInsight's Script menu. */
   if ( IN_PIXINSIGHT ) ( function()
   {
      [ "Loom.js", "FrameSelector.js", "FlyThrough.js" ].forEach( function( f )
      {
         var m = /#feature-id\s+[^:\n]+:\s*([^\n]+)/.exec( File.readTextFile( LOOM_DIR + "/" + f ) );
         check( f + " is in the Loom folder (" + ( m && m[1].trim() ) + ")", !!m && /^Loom > /.test( m[1].trim() ), true );
      } );
   } )();

   /* The object is read from the image's file name when it can be: the words, in runs of one to three, through the Object box's search. */
   ( function()
   {
      var csv = "id,alpha,delta,magnitude,diameter,axisRatio,posAngle,Common name,PGC,PGC2,Messier\n" +
                "IC1396,324.745000,57.514000,3.50,170.00,,,,,,\n" +
                "NGC5907,228.973696,56.328850,11.40,11.22,7.96,156,,PGC54470,,\n" +
                "NGC7000,314.695833,44.330000,,120.00,,,North America Nebula,,,\n" +
                "NGC224,10.684708,41.268750,4.36,177.83,3.09,35,Andromeda Galaxy,PGC2557,,M31\n";
      var e = Fly.parseNgcIc( csv ), from = function( n ) { var r = Fly.objectFromFileName( n, e ); return r ? r.id : null; };
      check( "a name in the file name", [ from( "/x/Elephant_Trunk v4_fullres.tif" ), from( "North-America_HOO_final.xisf" ) ], [ "IC1396A", "NGC7000" ] );
      check( "a catalogue id in it", [ from( "NGC 5907 master Day 5 copy.tif" ), from( "ic1396_SHO.fits" ), from( "M31_drizzle2x.xisf" ) ], [ "NGC5907", "IC1396", "NGC224" ] );
      check( "nothing when nothing matches", [ from( "Image13093" ), from( "masterLight_BIN-2_4144x2822_EXPOSURE-180.00s_FILTER-L_mono.xisf" ) ], [ null, null ] );
   } )();

   /* An unsolved image whose file name names its object has the Object box and centre filled from it. */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var dir = synthDir( "fly-filename" ), w = null, d = null;
      try
      {
         w = ImageWindow.open( synthFrame( dir + "/North_America_test.xisf", { stars: 20, fwhm: 3, background: 0.05, noise: 0.002, seed: 4 } ) )[0];
         d = new FlyThrough.Dialog( null );
         d.setImage( w );
         check( "the object is read from the file name (" + d.objectEdit.text + ")", [ /North America/i.test( d.objectEdit.text ), d.raEdit.text != "", /NGC7000/.test( d.objectMatch.text ) ], [ true, true, true ] );
         // the next image names nothing: its hints start empty, not with the last image's, and it is not analysed
         var bare = new ImageWindow( 64, 48, 3, 32, true, true, Util.freeWindowId( "fly_nameless" ) );
         try
         {
            var analysed = false;
            d.analyse = function() { analysed = true; };
            d.setImage( bare );
            check( "a new image naming nothing starts with empty hints", [ d.objectEdit.text, d.raEdit.text, d.decEdit.text, d.objectMatch.text ], [ "", "", "", "" ] );
            d.autoPrepare();
            check( "and is not analysed", analysed, false );
         }
         finally { bare.forceClose(); }
      }
      finally { if ( d ) d.release(); if ( w ) w.forceClose(); }
   } )();

   /*
    * The loop is every preset's choice, not only the Exhibition's: a
    * YouTube short set to crossfade was rendered as a plain clip, and its
    * music faded out (a real render). Exhibition always loops.
    */
   ( function()
   {
      var yt = Fly.presetSpec( "youtube_1080", "vertical", "crossfade" ), pp = Fly.presetSpec( "social_square", "horizontal", "pingpong" );
      check( "a YouTube preset set to crossfade loops by crossfading", [ yt.crossfade, yt.pingPong ], [ true, false ] );
      check( "a social one set to back and forth loops back and forth", [ pp.pingPong, pp.crossfade ], [ true, false ] );
      var none = Fly.presetSpec( "youtube_1080", "horizontal", "none" ), ex = Fly.presetSpec( "exhibition", "horizontal", "none" );
      check( "no loop, no loop", [ none.pingPong, none.crossfade ], [ false, false ] );
      check( "but the Exhibition always loops", ex.pingPong, true );
   } )();

   /* A social preset set to crossfade renders a loop, and its ffmpeg command loops the music instead of fading it out. */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var dir = synthDir( "fly-loopmusic" ), fx = flyTestScene( dir );
      try
      {
         var o = { travel: 150, easing: "smoothstep", growth: 0.15, brightening: true, duration: 0.4, fps: 10, video: false, loop: "crossfade",
                   music: { path: "/music/song.m4a", fade: true } };
         var res = FlyThrough.renderFinal( fx.scene, [ "social_square" ], o, dir, {} ), cmd = res.commands[0] || "";
         check( "the music loops with the video (crossfaded, not faded out)", [ /amix=inputs=2/.test( cmd ), /afade=t=in:st=0:d=[0-9.]+,afade=t=out/.test( cmd ) ], [ true, false ] );
      }
      finally { fx.windows.forEach( function( w ) { w.forceClose(); } ); }
   } )();

   /*
    * The per-image cache. An image is known by a fingerprint of its pixels
    * (so an unsaved one is too) and the Loom version; a scene is packed into
    * plain data plus float arrays and back, exactly; only the most recently
    * used images are kept.
    */
   ( function()
   {
      var key = function( mtime ) { return Fly.imageCacheKey( "/x/Elephant.tif", 1234, mtime, 6000, 4000, 3, "0.2.0" ); };
      check( "the same file, size and time give the same key", key( 99 ), key( 99 ) );
      check( "a file saved again does not", key( 99 ) != key( 100 ), true );
      check( "an unsaved image is known by its view", Fly.imageCacheKey( null, 0, 0, 100, 80, 3, "0.2.0", "Image13093" ) != Fly.imageCacheKey( null, 0, 0, 100, 80, 3, "0.2.0", "Image13094" ), true );
      check( "another Loom version starts afresh", key( 99 ) != Fly.imageCacheKey( "/x/Elephant.tif", 1234, 99, 6000, 4000, 3, "0.2.1" ), true );
      check( "a key is a folder-safe name", /^[0-9a-f]{8}$/.test( key( 99 ) ), true );

      var f = function( n, k ) { var a = new Float32Array( n ); for ( var i = 0; i < n; ++i ) a[i] = k + i/7; return a; };
      var sprite = { source: { ra: 1, dec: 2, G: 9 }, d: 300, placed: { x: 5, y: 6, d: 300 }, rect: { x0: 0, y0: 0, x1: 3, y1: 2 },
                     det: { x: 1.5, y: 1, flux: 9, rect: { x0: 0, y0: 0, x1: 3, y1: 2 }, model: { prof: [ 1, 0.5 ], spikes: [], col: [ 1, 0.9, 0.8 ], core: 4 } },
                     radius: 3, centre: { x: 1.5, y: 1 }, mask: new Uint8Array( [ 1, 1, 0, 1, 1, 1 ] ),
                     pixels: [ f( 6, 1 ), f( 6, 2 ) ], modelCore: [ f( 6, 3 ), f( 6, 4 ) ], modelSpikes: [ f( 6, 5 ), f( 6, 6 ) ] };
      var scene = { w: 3, h: 2, nc: 2, S: [ f( 6, 7 ), f( 6, 8 ) ], R: [ f( 6, 9 ), f( 6, 10 ) ], sprites: [ { s: sprite, p0: { x: 5, y: 6 } } ],
                    target: { ra: 1, dec: 2 }, tp: { x: 1, y: 1 }, D: 900, project: function() { return null; } };
      var packed = Render.packScene( scene ), proj = function() { return "p"; };
      var back = Render.unpackScene( JSON.parse( JSON.stringify( packed.meta ) ), packed.arrays, proj );
      var same = function( a, b ) { return Array.from( a ).join() == Array.from( b ).join(); };
      check( "a scene packs to plain data and float arrays", packed.arrays.every( function( a ) { return a instanceof Float32Array; } ), true );
      check( "and unpacks to the same pixels", [ same( back.S[1], scene.S[1] ), same( back.R[0], scene.R[0] ), same( back.sprites[0].s.pixels[1], sprite.pixels[1] ),
             same( back.sprites[0].s.modelCore[0], sprite.modelCore[0] ), same( back.sprites[0].s.modelSpikes[1], sprite.modelSpikes[1] ), same( back.sprites[0].s.mask, sprite.mask ) ],
             [ true, true, true, true, true, true ] );
      check( "the same stars and geometry", [ back.sprites[0].s.det.model.col.join(), back.sprites[0].s.source.G, back.sprites[0].p0.y, back.w, back.D, back.tp.x ], [ "1,0.9,0.8", 9, 6, 3, 900, 1 ] );
      check( "and the projection it is given", back.project(), "p" );
      check( "the mask comes back as bytes", back.sprites[0].s.mask instanceof Uint8Array, true );

      check( "only the most recently used images are kept",
             Fly.cacheToPrune( [ { name: "a", used: 5 }, { name: "b", used: 9 }, { name: "c", used: 1 }, { name: "d", used: 7 } ], 3 ), [ "c" ] );
   } )();

   /*
    * The per-image cache, on disk (the suite's own scratch folder, never
    * the real one): float arrays round trip; a solved working copy comes
    * back solved; a star scene comes back rendering the same frame, per
    * star tool; a draft comes back; old images are pruned.
    */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var dir = synthDir( "fly-cache" ), fx = flyTestScene( dir ), root = dir + "/cache";
      try
      {
         var a = new Float32Array( 300000 ), b = new Float32Array( 7 );
         for ( var i = 0; i < a.length; ++i ) a[i] = Math.sin( i );
         b.set( [ 1, 2, 3, 4, 5, 6, 7 ] );
         Sky.writeArrays( root + "/t.bin", [ a, b ] );
         var r = Sky.readArrays( root + "/t.bin", [ a.length, b.length ] );
         check( "float arrays round trip", [ r[0][123456] == a[123456], r[1][6] ], [ true, 7 ] );

         var img = fx.image, cdir = FlyThrough.cacheDir( root, img );
         FlyThrough.saveWork( cdir, { window: img, scale: 0.5 } );
         var w = FlyThrough.loadWork( cdir );
         check( "a solved working copy comes back solved, at its scale", [ !!w && Sky.projector( w.window ) != null, w && w.scale ], [ true, 0.5 ] );
         var p1 = Sky.projector( img )( 324.7, 57.5 ), p2 = w && Sky.projector( w.window )( 324.7, 57.5 );
         check( "with the same solution", !!p2 && Math.abs( p1.x - p2.x ) < 1e-3 && Math.abs( p1.y - p2.y ) < 1e-3, true );

         var built = { scene: fx.scene, counts: { detected: 10, placed: 3, blended: 0, backdrop: 2 }, colourNote: "n", stretched: false, colour: Fly.SRGB_COLOUR };
         FlyThrough.saveBuilt( cdir, "StarXTerminator", built );
         var back = FlyThrough.loadBuilt( cdir, "StarXTerminator", w.window, img );
         check( "a star scene comes back for its star tool", !!back && back.counts.placed, 3 );
         check( "but not for another", FlyThrough.loadBuilt( cdir, "StarNet2", w.window, img ), null );
         var o = { travel: 150, easing: "smoothstep", growth: 0.15, brightening: true, duration: 2 }, W = 160, H = 90;
         var crop = Fly.presetCrop( fx.scene.w, fx.scene.h, fx.scene.tp.x, fx.scene.tp.y, W, H );
         var f1 = Render.frame( fx.scene, 0.6, o, W, H, crop ), f2 = Render.frame( back.scene, 0.6, o, W, H, crop ), worst = 0;
         for ( var y = 0; y < H; y += 3 ) for ( var x = 0; x < W; x += 3 ) worst = Math.max( worst, Math.abs( f1.sample( x, y, 0 ) - f2.sample( x, y, 0 ) ) );
         check( "and renders the same frame (worst " + worst.toExponential( 1 ) + ")", worst < 1e-6, true );
         f1.free(); f2.free();

         var bmps = [ new Bitmap( 32, 18 ), new Bitmap( 32, 18 ) ];
         bmps[0].fill( 0xff112233 ); bmps[1].fill( 0xff445566 );
         FlyThrough.saveDraft( cdir, "sig-1", { bitmaps: bmps, fps: 7.5, pingPong: false } );
         var d = FlyThrough.loadDraft( cdir, "sig-1" );
         check( "a draft comes back", [ !!d && d.bitmaps.length, d && d.fps, d && ( d.bitmaps[1].pixel( 3, 3 ) & 0xffffff ) ], [ 2, 7.5, 0x445566 ] );
         check( "only for its own options", FlyThrough.loadDraft( cdir, "sig-2" ), null );
         if ( w ) w.window.forceClose();

         [ "k1", "k2", "k3", "k4" ].forEach( function( k, i ) { File.createDirectory( root + "/" + k, true ); FlyThrough.touch( root + "/" + k, 1000 + i ); } );
         FlyThrough.touch( cdir, 5000 );
         FlyThrough.prune( root, 3 );
         check( "only the three most recently used images are kept", [ File.directoryExists( cdir ), File.directoryExists( root + "/k4" ), File.directoryExists( root + "/k3" ), File.directoryExists( root + "/k1" ) ], [ true, true, true, false ] );
      }
      finally { fx.windows.forEach( function( w ) { w.forceClose(); } ); }
   } )();

   /*
    * Choosing an image again takes its solved working copy, its stars and
    * its draft from the cache: no resample, no star removal or deblending,
    * no draft render.
    */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var dir = synthDir( "fly-cache-dialog" ), fx = flyTestScene( dir ), counts = { copy: 0, build: 0, draft: 0 };
      var real = { root: FlyThrough.cacheRoot, copy: Sky.workingCopy, identify: FlyThrough.identify, build: FlyThrough.build, draft: FlyThrough.renderDraft };
      FlyThrough.cacheRoot = function() { return dir + "/cache"; };
      Sky.workingCopy = function( w ) { ++counts.copy; var c = Sky.copyWindow( w, "fly_work" ); c.keywords = w.keywords; return { window: c, scale: 1 }; };
      FlyThrough.identify = function( w ) { return { proj: Sky.projector( w ), field: Sky.field( w ), pick: { best: null, runnerUp: null }, target: null, type: "nebula",
                                                     D: 900, distanceSource: "typed", cluster: null, sources: fx.sources }; };
      FlyThrough.build = function() { ++counts.build; return { scene: fx.scene, windows: [], colour: Fly.SRGB_COLOUR, colourNote: "", stretched: false,
                                                               counts: { detected: 9, placed: 3, blended: 0, backdrop: 1 } }; };
      FlyThrough.renderDraft = function() { ++counts.draft; var b = new Bitmap( 16, 9 ); b.fill( 0xff102030 ); return { bitmaps: [ b ], fps: 7.5, pingPong: false }; };
      var run = function()
      {
         var d = new FlyThrough.Dialog( null );
         try
         {
            d.tools = [ "StarXTerminator" ];
            d.setImage( fx.image );
            d.distanceEdit.text = "900";
            d.analyse();
            var o = d.prepare();
            d.makeDraft( o, {} );
            return d.player.frames.length;
         }
         finally { d.release(); }
      };
      try
      {
         run();
         check( "the first time, everything is made", [ counts.copy, counts.build, counts.draft ], [ 1, 1, 1 ] );
         var frames = run();
         check( "the second time, it all comes from the cache", [ counts.copy, counts.build, counts.draft, frames ], [ 1, 1, 1, 1 ] );
      }
      finally
      {
         FlyThrough.cacheRoot = real.root; Sky.workingCopy = real.copy; FlyThrough.identify = real.identify; FlyThrough.build = real.build; FlyThrough.renderDraft = real.draft;
         fx.windows.forEach( function( w ) { w.forceClose(); } );
      }
   } )();

   /*
    * Spikes grow as a brighter star's do: longer, never wider. A spike
    * falling as 1/r^2 shows, L times longer, what an L^2-times brighter one
    * shows -- so its length goes with the square root of the brightening
    * (the light ratio when brightening), at most SPIKE_LENGTH_MAX, never less
    * than the glow's growth. They are drawn on their own from the model, the
    * photograph minus them carrying the glow; at the start the two add up to
    * the photograph.
    */
   ( function()
   {
      check( "a spike's length: 1 at the start, the light ratio when brightening, capped, never below the glow's growth",
             [ Fly.spikeLength( 1, true, 1 ), Fly.spikeLength( 3, true, 1.3 ), Fly.spikeLength( 9, true, 2.2 ), Fly.spikeLength( 3, false, 1.3 ) ],
             [ 1, 3, Fly.SPIKE_LENGTH_MAX, 1.3 ] );
      // a horizontal spike, sigma 1 px across, fading along: drawn 2x longer it is no wider
      var R = 40, rw = 2*R + 1, patch = new Float32Array( rw*rw );
      for ( var y = 0; y < rw; ++y ) for ( var x = 0; x < rw; ++x )
      {
         var al = x - R, lat = y - R;
         patch[y*rw + x] = ( al > 4 ) ? Math.exp( -lat*lat/2 )*Math.exp( -( al - 4 )/8 ) : 0;
      }
      var sp = { rect: { x0: 0, y0: 0, x1: rw, y1: rw }, det: { x: R, y: R } }, W = 201, cam = { x: 0, y: 0, fx: 1, fy: 1 };
      var draw = function( L ) { var acc = new Float32Array( W*W ); Render.drawSprite( acc, W, W, patch, sp, 100, 100, 1, 1, cam, 1, 4, { spike: { length: L, angles: [ 0 ] } } ); return acc; };
      var one = draw( 1 ), two = draw( 2 );
      var width = function( acc, x ) { var p = acc[100*W + x], n = 0; for ( var yy = 90; yy <= 110; ++yy ) if ( acc[yy*W + x] > p/2 ) ++n; return n; };
      var reach = function( acc ) { var x = 105; while ( acc[100*W + x] > 0.05 ) ++x; return x - 100; };
      check( "drawn twice as long, a spike is no wider (" + width( one, 110 ) + " vs " + width( two, 116 ) + " px)", width( two, 116 ), width( one, 110 ) );
      check( "and reaches about twice as far (" + reach( one ) + " -> " + reach( two ) + ")", Math.abs( ( reach( two ) - 4 ) - 2*( reach( one ) - 4 ) ) <= 2, true );
      check( "at length 1 it is drawn as it was", Math.abs( one[100*W + 115] - patch[R*rw + R + 15] ) < 1e-6, true );
   } )();

   /*
    * A nebula with no ionising cluster -- a reflection nebula like the Iris
    * (NGC 7023) -- takes the distance of the bright star that lights it: the
    * brightest star with a reliable parallax within the nebula (Gaia).
    */
   ( function()
   {
      var target = { ra: 315.4, dec: 68.16, diameter: 18 };
      var star = function( dra, ddec, G, plx ) { return { ra: target.ra + dra, dec: target.dec + ddec, G: G, plx: plx }; };
      var sources = [ star( 0.01, 0.005, 7.3, 2.8 ), star( 1.5, 0.2, 5.0, 9 ), star( -0.02, 0.01, 8.0, 0.01 ), star( 0.03, 0, 12.5, 1.1 ) ];
      var d = Fly.illuminatorDistance( sources, target );
      check( "the brightest star with a reliable parallax inside the nebula (" + ( d && Math.round( d.distance ) ) + " pc)",
             !!d && Math.abs( d.distance - 1000/( 2.8 + Fly.PARALLAX_ZERO_POINT ) ) < 0.5 && d.G == 7.3, true );
      check( "no star bright enough to light it, no distance", Fly.illuminatorDistance( [ star( 0.01, 0, 12.5, 1.1 ), star( 0.02, 0, 11, 2 ) ], target ), null );
   } )();

   /*
    * Stars into the HDR headroom: in an HDR video the stars layer above a
    * knee is expanded smoothly past SDR white, so the brightest cores reach
    * the chosen peak, each star's hue kept; the nebula is untouched.
    */
   ( function()
   {
      var peak = 4;
      check( "below the knee, unchanged", Fly.starHeadroom( 0.5, peak ), 1 );
      check( "white reaches the peak", Math.abs( Fly.starHeadroom( 1, peak )*1 - peak ) < 1e-9, true );
      var prev = 0, mono = true;
      for ( var v = 0; v <= 1.0001; v += 0.01 ) { var o = v*Fly.starHeadroom( v, peak ); if ( o < prev - 1e-12 ) mono = false; prev = o; }
      check( "and it never turns back", mono, true );
      var T = [ new Float32Array( [ 0.95, 0.3 ] ), new Float32Array( [ 0.76, 0.2 ] ), new Float32Array( [ 0.57, 0.1 ] ) ];
      Render.starsToHeadroom( T, 2, peak );
      check( "a bright star goes past white, keeping its hue", [ T[0][0] > 1, Math.abs( T[1][0]/T[0][0] - 0.8 ) < 1e-6, Math.abs( T[2][0]/T[0][0] - 0.6 ) < 1e-6 ], [ true, true, true ] );
      check( "a faint one is left as it was", [ T[0][1], T[1][1], T[2][1] ].map( function( x ) { return +x.toFixed( 6 ); } ), [ 0.3, 0.2, 0.1 ] );
      // each star's peak from its magnitude: the brightest reaches the chosen peak, 5 magnitudes fainter none
      check( "by magnitude: the brightest star gets the peak, 2.5 mag fainter half the way, 5 fainter none",
             [ Fly.magnitudeGain( 5, 5, 4 ), Fly.magnitudeGain( 7.5, 5, 4 ), Fly.magnitudeGain( 10, 5, 4 ), Fly.magnitudeGain( 12, 5, 4 ) ], [ 4, 2.5, 1, 1 ] );
      var M = [ new Float32Array( [ 0.95, 0.95 ] ) ];
      Render.starsToHeadroom( M, 2, new Float32Array( [ 4, 1 ] ) );
      check( "a per-pixel peak: only where a bright star is", [ M[0][0] > 1, Math.abs( M[0][1] - 0.95 ) < 1e-6 ], [ true, true ] );
   } )();

   /* In an HDR frame, "Stars into HDR headroom" lifts bright stars past SDR white; the backdrop is untouched. */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var dir = synthDir( "fly-headroom" ), fx = flyTestScene( dir, 12 );   // stars bright enough to reach the knee
      try
      {
         var W = 240, H = 135, crop = Fly.presetCrop( fx.scene.w, fx.scene.h, fx.scene.tp.x, fx.scene.tp.y, W, H );
         var base = { travel: 150, easing: "smoothstep", growth: 0.15, brightening: true, duration: 2, peak: 1000 };
         var frame = function( on ) { return Render.frame( fx.scene, 0.5, Object.assign( {}, base, { starHdr: on, output: Fly.outputTransform( Fly.SRGB_COLOUR, "pq" ) } ), W, H, crop ); };
         var off = frame( false ), on = frame( true ), maxOff = 0, maxOn = 0, sky = 0;
         for ( var y = 0; y < H; ++y ) for ( var x = 0; x < W; ++x ) { maxOff = Math.max( maxOff, off.sample( x, y, 0 ) ); maxOn = Math.max( maxOn, on.sample( x, y, 0 ) ); }
         for ( y = 0; y < H; y += 4 ) for ( x = 0; x < W; x += 4 ) if ( off.sample( x, y, 0 ) < 0.3 ) sky = Math.max( sky, Math.abs( on.sample( x, y, 0 ) - off.sample( x, y, 0 ) ) );
         check( "bright stars reach higher in the HDR signal (" + maxOff.toFixed( 3 ) + " -> " + maxOn.toFixed( 3 ) + ")", maxOn > maxOff + 0.02, true );
         check( "the dark sky is untouched (" + sky.toExponential( 1 ) + ")", sky < 1e-6, true );
         off.free(); on.free();
      }
      finally { fx.windows.forEach( function( w ) { w.forceClose(); } ); }
   } )();

   /* A bright catalogued star that stays put -- the Iris's HD 200775 -- reaches into the HDR headroom too, where the backdrop carries it. */
   ( function()
   {
      var sc = { tp: { x: 100, y: 100 }, sprites: [], catalogue: [ { x: 50, y: 40, G: 7.3, ra: 315.40, dec: 68.16 }, { x: 150, y: 150, G: 11, ra: 315.5, dec: 68.2 } ] };
      var cam = { x: 0, y: 0, fx: 1, fy: 1 }, map = Render.headroomMap( sc, [], { peak: 1000, K: 1.2 }, 200, 200, cam );
      var at = function( x, y ) { return map[y*200 + x]; }, peak = 1000/Fly.HDR_REFERENCE_WHITE;
      check( "the brightest catalogued star gets the peak where the backdrop carries it", Math.abs( at( 40, 28 ) - peak ) < 1e-6, true );
      check( "a star 3.7 mag fainter gets part of it", at( 160, 160 ) > 1 && at( 160, 160 ) < peak, true );
      check( "and empty sky none", at( 190, 10 ), 1 );
   } )();

   /* fly-tests-end */
}

function main()
{
   var aborted = false;
   silenceLogging();
   try { runTests(); }
   catch ( e )
   {
      aborted = true;
      FAILURES.push( "EXCEPTION: " + e.toString() +
                     ( e.stack ? "\n" + e.stack.split( "\n" ).slice( 0, 4 ).join( "\n" ) : "" ) );
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
