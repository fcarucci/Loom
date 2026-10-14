#engine v8

/*
 * PixInsight only: MasterFlat.engine against synthetic 16x16 flats and darks.
 *
 * The node suite runs MasterFlat.execute with a fake engine; this runs the
 * real one -- ImageCalibration, ImageIntegration, the headers, the rename --
 * in a temporary tree. No dialogs; every file is made here and the tree is
 * removed on success. Run it in the SECOND PixInsight (never the user's main
 * one), holding /tmp/agent-scratch/pi-slot2.lock:
 *
 *    rm -f /tmp/agent-scratch/pi-masterflat-result.txt
 *    /tmp/agent-scratch/pi2.sh <repo>/ci/pi-masterflat.js
 *    (wait for) cat /tmp/agent-scratch/pi-masterflat-result.txt
 *
 * The result goes to its own file, which other runs in the slot do not touch.
 *
 * Scenarios (A: a whole night; B: the same, with the integration made to fail):
 *   L   4 flats + a "_c" name + a symlink to a file on the "card"; a master dark in the folder
 *   R   4 flats; only raw darks -> integrated, cached; a master from an earlier import exists
 *   G   3 flats at an exposure no dark has -> kept
 *   B   3 flats at bin 2, only bin 1 darks of that exposure -> kept
 */

#include <pjsr/DataType.jsh>
#include <pjsr/FrameStyle.jsh>
#include <pjsr/StdButton.jsh>
#include <pjsr/ImageOp.jsh>
#include <pjsr/StdCursor.jsh>
#include <pjsr/StdIcon.jsh>
#include <pjsr/TextAlign.jsh>
#include <pjsr/ColorSpace.jsh>
#include <pjsr/SampleType.jsh>
#include <pjsr/UndoFlag.jsh>

#include "../script/lib/Util.js"
#include "../script/lib/Hasher.js"
#include "../script/lib/Cache.js"
#include "../script/lib/MasterFlat.js"

function masterFlatPiCheck()
{
   var failures = [], run = 0, notes = [];
   function check( name, ok, detail )
   {
      ++run;
      if ( !ok ) failures.push( name + ( detail !== undefined ? " [" + detail + "]" : "" ) );
      console.writeln( ( ok ? "ok   " : "FAIL ") + name );
   }

   var W = 16, H = 16;
   var root = "/tmp/agent-scratch/loom-masterflat-" + Date.now();
   var card = root + "/card", darksDir = root + "/darks", cache = root + "/cache";
   [ card, darksDir, cache ].forEach( function( d ) { Util.ensureDirectory( d ); } );

   var seed = 12345;
   function rnd() { seed = ( seed * 1103515245 + 12345 ) & 0x7fffffff; return seed / 0x7fffffff; }

   function makeFrame( path, level, keywords )
   {
      var w = new ImageWindow( W, H, 1, 32, true, false, "mfsyn" );
      try
      {
         w.mainView.beginProcess( UndoFlag_NoSwapFile );
         var img = w.mainView.image;
         for ( var y = 0; y < H; ++y )
            for ( var x = 0; x < W; ++x )
               img.setSample( Math.max( 0.0001, level + ( rnd() - 0.5 ) * 0.01 ), x, y );
         w.mainView.endProcess();
         w.keywords = Object.keys( keywords ).map( function( k )
            { return new FITSKeyword( k, keywords[k], "" ); } );
         Util.ensureDirectory( path.substring( 0, path.lastIndexOf( "/" ) ) );
         if ( !w.saveAs( path, false, false, false, false ) )
            throw new Error( "could not write " + path );
      }
      finally { w.forceClose(); }
   }
   function flatKw( filter, exp, bin, extra )
   {
      var k = { IMAGETYP: "'Flat Frame'", FILTER: "'" + filter + "'", EXPTIME: String( exp ),
                XBINNING: String( bin ), YBINNING: String( bin ), INSTRUME: "'SynCam'" };
      for ( var e in ( extra || {} ) ) k[e] = extra[e];
      return k;
   }
   function darkKw( exp, bin, type )
   {
      return { IMAGETYP: "'" + ( type || "Dark Frame" ) + "'", EXPTIME: String( exp ),
               XBINNING: String( bin ), YBINNING: String( bin ), INSTRUME: "'SynCam'" };
   }

   function sh( program, args )
   {
      var p = new ExternalProcess;
      p.start( program, args );
      p.waitForFinished();
      return p.exitCode;
   }
   function listing( dir )
   {
      return Util.findEntries( dir + "/*", true ).map( function( e ) { return e.name + ":" + e.size; } ).sort().join( "|" );
   }
   // Read without a window, and a missing file is null (ImageWindow.open raises a modal box).
   function meanOf( path )
   {
      var inst = null;
      try
      {
         if ( !File.exists( path ) )
            return null;
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
   function windowIds()
   {
      return ImageWindow.windows.map( function( w ) { return w.mainView.id; } ).sort().join( "," );
   }
   function keywordOf( path, name )
   {
      return FrameSelectorHeader( path ).keyword( name );
   }
   function FrameSelectorHeader( path )
   {
      var info = null;
      try { info = File.exists( path ) ? Util.tryHeaderRead( path ).info : null; } catch ( e ) {}
      var kw = info ? info.keywords : null;
      return { info: info, keyword: function( n ) { return kw ? Util.keywordValue( kw, n ) : null; } };
   }

   // ---- the card: a sentinel that must come through unchanged ----
   var outside = card + "/outside.xisf";
   makeFrame( outside, 0.5, flatKw( "L", 1.0, 1 ) );
   var f = new File; f.createForWriting( card + "/sentinel.txt" ); f.outText( "card data\n" ); f.close();
   var cardBefore = listing( card ), outsideMean = meanOf( outside );

   // ---- the darks folder ----
   makeFrame( darksDir + "/masterDark_1s.xisf", 0.02, darkKw( 1.0, 1, "Master Dark" ) );
   for ( var i = 0; i < 3; ++i )
   {
      makeFrame( darksDir + "/Dark_0.5s_" + i + ".xisf", 0.015, darkKw( 0.5, 1 ) );
      makeFrame( darksDir + "/Dark_0.1s_bin1_" + i + ".xisf", 0.012, darkKw( 0.1, 1 ) );
   }
   makeFrame( darksDir + "/Flat_decoy.xisf", 0.4, flatKw( "L", 1.0, 1 ) );    // not a dark

   function makeNight( dest )
   {
      var fd = dest + "/Flat";
      var made = [];
      for ( var a = 0; a < 4; ++a ) made.push( fd + "/Flat_L_" + a + ".xisf" ), makeFrame( made[made.length-1], 0.50, flatKw( "L", 1.0, 1 ) );
      made.push( fd + "/Flat_L_c_9.xisf" ); makeFrame( made[made.length-1], 0.50, flatKw( "L", 1.0, 1 ) );
      for ( var b = 0; b < 4; ++b ) made.push( fd + "/Flat_R_" + b + ".xisf" ), makeFrame( made[made.length-1], 0.40, flatKw( "R", 0.5, 1 ) );
      for ( var c = 0; c < 3; ++c ) made.push( fd + "/Flat_G_" + c + ".xisf" ), makeFrame( made[made.length-1], 0.45, flatKw( "G", 2.0, 1 ) );
      for ( var d = 0; d < 3; ++d ) made.push( fd + "/Flat_B_" + d + ".xisf" ), makeFrame( made[made.length-1], 0.45, flatKw( "B", 0.1, 2 ) );
      // a symlink to a flat that lives on the card, with a header that says L
      var link = fd + "/Flat_L_link.xisf";
      sh( "/bin/ln", [ "-s", outside, link ] );
      made.push( link );
      // a master from an earlier import
      makeFrame( fd + "/masterFlat_R.xisf", 0.77, flatKw( "R", 0.5, 1, { IMAGETYP: "'Master Flat'" } ) );
      return made;
   }
   function planFor( dest )
   {
      var fd = dest + "/Flat";
      var flats = Util.findEntries( fd + "/*" ).filter( function( e ) { return /^Flat_/.test( e.name ); } )
         .map( function( e ) { return MasterFlat.describe( fd + "/" + e.name, FrameSelectorHeader( fd + "/" + e.name ).keyword ); } );
      var darks = MasterFlat.darksOf( MasterFlat.scanFolder( darksDir, function( p ) { return FrameSelectorHeader( p ); } ) );
      return { flats: flats, darks: darks, jobs: MasterFlat.buildPlan( flats, darks, fd ), fd: fd };
   }
   function engineFor()
   {
      return MasterFlat.engine( FrameSelectorHeader, cache );
   }
   function exists( p ) { return File.exists( p ); }

   // ------------------------------------------------------------------
   // Plan sanity
   // ------------------------------------------------------------------
   var destA = root + "/destA", destB = root + "/destB";
   var nightA = makeNight( destA );
   var existingBefore = listing( destA + "/Flat" ).split( "|" ).filter( function( s ) { return /^masterFlat_R/.test( s ); } )[0];
   var existingMean = meanOf( destA + "/Flat/masterFlat_R.xisf" );
   var planA = planFor( destA );
   var byName = {};
   planA.jobs.forEach( function( j ) { byName[j.filter] = j; } );
   check( "plan: four filter jobs", planA.jobs.length == 4, planA.jobs.map( function( j ) { return j.filter; } ).join( "," ) );
   check( "plan: L has a master dark, ready", byName.L && byName.L.skip == null && byName.L.parts[0].dark.kind == "master",
          byName.L && String( byName.L.skip ) );
   check( "plan: L includes the symlinked flat (reads through it)", byName.L && byName.L.flats.length == 6, byName.L && byName.L.flats.length );
   check( "plan: R uses raw darks", byName.R && byName.R.skip == null && byName.R.parts[0].dark.kind == "raw" );
   check( "plan: G has no matching dark (kept)", byName.G && /no matching dark/.test( String( byName.G.skip ) ), byName.G && String( byName.G.skip ) );
   check( "plan: B bin 2 does not take the bin 1 darks (kept)", byName.B && /no matching dark/.test( String( byName.B.skip ) ), byName.B && String( byName.B.skip ) );

   // ------------------------------------------------------------------
   // Scenario A: the real engine, a whole night
   // ------------------------------------------------------------------
   var windowsBefore = windowIds();
   var resA = MasterFlat.execute( planA.jobs, engineFor(), { destFlatDir: planA.fd, cardRoot: card } );
   notes.push( "A report:\n" + MasterFlat.report( resA ) + "\nA Flat folder: " + listing( planA.fd ) );
   check( "A: no ImageWindow left behind", windowIds() == windowsBefore, windowIds() + " vs " + windowsBefore );
   check( "A: L and R made, G and B kept", resA.made.length == 2 && resA.kept.length == 2,
          resA.made.length + " made, " + resA.kept.length + " kept: " + MasterFlat.report( resA ) );

   check( "A: masterFlat_L.xisf exists", exists( planA.fd + "/masterFlat_L.xisf" ) );
   var mL = FrameSelectorHeader( planA.fd + "/masterFlat_L.xisf" );
   check( "A: master L header: FILTER, IMAGETYP, INSTRUME, EXPTIME, size",
          mL.keyword( "FILTER" ) == "L" && /Master Flat/i.test( mL.keyword( "IMAGETYP" ) || "" ) &&
          mL.keyword( "INSTRUME" ) == "SynCam" && Math.abs( parseFloat( mL.keyword( "EXPTIME" ) ) - 1.0 ) < 1e-6 &&
          mL.info && mL.info.width == W && mL.info.height == H,
          [ mL.keyword( "FILTER" ), mL.keyword( "IMAGETYP" ), mL.keyword( "INSTRUME" ), mL.keyword( "EXPTIME" ) ].join( ";" ) );
   var mean = meanOf( planA.fd + "/masterFlat_L.xisf" );
   // flat 0.50 minus the 0.02 dark, multiplicatively normalised: near 0.48 (average of the stack)
   check( "A: master L is calibrated (dark taken out) and finite", mean != null && isFinite( mean ) && mean > 0.3 && mean < 0.52, mean );

   check( "A: R got a NEW master beside the earlier one", exists( planA.fd + "/masterFlat_R_2.xisf" ) );
   check( "A: the earlier masterFlat_R.xisf is untouched",
          listing( planA.fd ).split( "|" ).filter( function( s ) { return /^masterFlat_R\.xisf/.test( s ); } )[0] == existingBefore &&
          meanOf( planA.fd + "/masterFlat_R.xisf" ) == existingMean );

   check( "A: raw L flats deleted (the plain ones)", [ 0, 1, 2, 3 ].every( function( i ) { return !exists( planA.fd + "/Flat_L_" + i + ".xisf" ); } ) &&
                                                      !exists( planA.fd + "/Flat_L_c_9.xisf" ) );
   check( "A: raw R flats deleted", [ 0, 1, 2, 3 ].every( function( i ) { return !exists( planA.fd + "/Flat_R_" + i + ".xisf" ); } ) );
   check( "A: G and B raw flats kept", [ 0, 1, 2 ].every( function( i ) { return exists( planA.fd + "/Flat_G_" + i + ".xisf" ) && exists( planA.fd + "/Flat_B_" + i + ".xisf" ); } ) );
   check( "A: the symlinked flat was NOT deleted and not followed", exists( planA.fd + "/Flat_L_link.xisf" ) && exists( outside ) );
   check( "A: the card is unchanged", listing( card ) == cardBefore && meanOf( outside ) == outsideMean, listing( card ) );
   check( "A: no temporary left in Flat",
          Util.findEntries( planA.fd + "/*", true ).every( function( e ) { return !/^\.partial_/.test( e.name ); } ),
          listing( planA.fd ) );
   check( "A: no intermediate left in the work folder",
          !File.directoryExists( cache + "/master-flat-work" ) ||
          Util.findEntries( cache + "/master-flat-work/*", true ).every( function( e ) { return e.isDirectory && Util.findEntries( cache + "/master-flat-work/" + e.name + "/*", true ).length == 0; } ) );

   // the raw-dark master went to the cache, whole
   var cachedDarks = Util.findEntries( cache + "/master-darks/*", true );
   check( "A: one cached master dark, no partial", cachedDarks.length == 1 && /^masterDark_/.test( cachedDarks[0].name ), cachedDarks.map( function( e ) { return e.name; } ).join( "," ) );
   var cdPath = cache + "/master-darks/" + ( cachedDarks[0] ? cachedDarks[0].name : "" );
   var cdKw = FrameSelectorHeader( cdPath );
   check( "A: the cached master dark says Master Dark (darkKind -> master)",
          MasterFlat.darkKind( "x", cdKw.keyword( "IMAGETYP" ) ) == "master", cdKw.keyword( "IMAGETYP" ) );
   var cdMod = ( new FileInfo( cdPath ) ).lastModified.toISOString();

   // ------------------------------------------------------------------
   // Scenario B: integration fails; cache hit; pre-existing master survives
   // ------------------------------------------------------------------
   var nightB = makeNight( destB );
   var planB = planFor( destB );
   var realEngine = engineFor(), integrateCalls = 0;
   var failing = {};
   for ( var k in realEngine ) failing[k] = realEngine[k];
   failing.integrate = function( paths, spec, out, job )
   {
      ++integrateCalls;
      var r = realEngine.integrate( paths, spec, out, job );   // really written ...
      return { ok: false, reason: "forced failure after writing" };   // ... then reported failed
   };
   var existingMeanB = meanOf( planB.fd + "/masterFlat_R.xisf" );
   var existingBeforeB = listing( planB.fd ).split( "|" ).filter( function( s ) { return /^masterFlat_R\.xisf/.test( s ); } )[0];
   var sizesBeforeB = listing( planB.fd );
   var resB0 = null;
   var resB = MasterFlat.execute( planB.jobs, failing, { destFlatDir: planB.fd, cardRoot: card } );
   check( "B: every ready filter was kept (failure)", resB.made.length == 0 && integrateCalls >= 2 && resB.kept.length == 4, MasterFlat.report( resB ) );
   check( "B: all raw flats and the earlier master still there, nothing new left",
          listing( planB.fd ) == sizesBeforeB, listing( planB.fd ) );
   check( "B: the earlier masterFlat_R.xisf is byte-for-byte as it was", meanOf( planB.fd + "/masterFlat_R.xisf" ) == existingMeanB && existingMeanB != null );
   check( "B: raw-dark master came from the cache (not rebuilt)",
          ( new FileInfo( cdPath ) ).lastModified.toISOString() == cdMod && Util.findEntries( cache + "/master-darks/*", true ).length == 1 );
   check( "B: the card is still unchanged", listing( card ) == cardBefore );

   // verification: a master that is not finite / empty is refused
   var emptyPath = destB + "/Flat/.partial_empty.xisf";
   makeFrame( emptyPath, 0.0001, flatKw( "L", 1.0, 1, { IMAGETYP: "'Master Flat'" } ) );
   var emptyJob = { filter: "L", flats: [ { path: destB + "/Flat/Flat_L_0.xisf" } ], binning: 1 };
   var problem = engineFor().verify( emptyPath, emptyJob );
   check( "verify: a near-empty master is accepted only if mean > 0 (0.0001 is >0)", problem == null, problem );
   var badFilter = engineFor().verify( emptyPath, { filter: "R", flats: emptyJob.flats, binning: 1 } );
   check( "verify: a master with the wrong FILTER is refused", badFilter != null && /FILTER/.test( badFilter ), badFilter );
   File.remove( emptyPath );

   // publish never overwrites
   var e2 = engineFor();
   var pA = destB + "/Flat/.partial_t.xisf", pB = destB + "/Flat/masterFlat_T.xisf";
   makeFrame( pA, 0.3, flatKw( "T", 1, 1 ) ); makeFrame( pB, 0.6, flatKw( "T", 1, 1 ) );
   var mBefore = meanOf( pB );
   check( "publish: refuses to overwrite an existing file", e2.publish( pA, pB ) === false && meanOf( pB ) == mBefore && exists( pA ) );
   File.remove( pB );
   check( "publish: moves into a free name", e2.publish( pA, pB ) === true && exists( pB ) && !exists( pA ) );

   // resolve follows links
   var linkPath = destB + "/Flat/Flat_L_link.xisf";
   check( "resolve: a symlinked file is not taken for a plain one",
          engineFor().resolve( linkPath ) != File.fullPath( destB + "/Flat" ) + "/Flat_L_link.xisf" );
   check( "resolve: a plain file resolves to its own name in the real folder",
          engineFor().resolve( destB + "/Flat/Flat_G_0.xisf" ) == File.fullPath( destB + "/Flat" ) + "/Flat_G_0.xisf" );
   check( "mayDelete refuses the symlinked flat, accepts a plain one",
          !MasterFlat.mayDelete( linkPath, destB + "/Flat", null, card, engineFor().resolve ) &&
          MasterFlat.mayDelete( destB + "/Flat/Flat_G_0.xisf", destB + "/Flat", null, card, engineFor().resolve ) );

   var summary = ( failures.length == 0 ? "PASS" : "FAIL" ) + " PI-MASTERFLAT " + run + " run, " +
                 failures.length + " failed\n" + failures.join( "\n" ) + "\n" + notes.join( "\n" ) + "\n";
   console.writeln( summary );
   var out = new File;
   out.createForWriting( "/tmp/agent-scratch/pi-masterflat-result.txt" );
   out.outText( summary );
   out.close();
   if ( failures.length == 0 )
      sh( "/bin/rm", [ "-rf", root ] );
   else
      console.writeln( "kept for inspection: " + root );
}

try { masterFlatPiCheck(); }
catch ( e )
{
   var ef = new File;
   ef.createForWriting( "/tmp/agent-scratch/pi-masterflat-result.txt" );
   ef.outText( "FAIL PI-MASTERFLAT exception: " + e + "\n" + ( e && e.stack ? e.stack : "" ) + "\n" );
   ef.close();
}
