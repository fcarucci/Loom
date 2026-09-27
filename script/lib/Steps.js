/*
 * lib/Steps.js
 *
 * One thin wrapper per PixInsight process used by the Loom
 * pipeline. Every parameter assignment in this file is copied verbatim
 * from docs/verified-parameters.md ("Settings the pipeline must make"),
 * which is the binding authority for process parameters in this project.
 * Do not add or change a parameter here without updating that document
 * first.
 */

// ImageSolver is a PJSR script, not a process -- "new ImageSolver" at
// global scope throws ReferenceError until the engine script has been
// #include-d. Per verified-parameters.md's ImageSolver recipe, these
// top-level includes make the ImageSolver engine class (and everything
// it depends on) available; Steps.solve() then just constructs and
// drives it like the recipe shows.
// ImageSolverEngine.js uses SOLVER_SETTINGS_MODULE, which is #defined in
// ImageSolver.js (the front-end script we deliberately do not include).
// Define it here so the engine constructs.
#define SOLVER_SETTINGS_MODULE "ImageSolver"
#define VERSION "6.4.2"
#define TITLE   "ImageSolver"
#define SETTINGS_MODULE "ImageSolver"

#include <pjsr/UndoFlag.jsh>
#include <pjsr/astrometry/AstrometricMetadata.js>
#include <pjsr/astrometry/AstronomicalCatalogs.js>
/*
 * The measurement half of astrometric solution analysis, new in 1.9.5:
 * star detection, PSF fitting, Gaia retrieval, matching, and the
 * deviations between measured centroids and catalog positions. Used by
 * Steps.verifySolution below.
 *
 * This is the base class of the AstrometricSolutionVerifier script's
 * engine, and Loom uses the base class rather than that engine on
 * purpose -- see the comment on Steps.verifySolution. It lives under
 * include/, not src/scripts/, so it needs no path gymnastics, it carries
 * its own #ifndef guard, and it depends only on AstrometricMetadata.js
 * and AstronomicalCatalogs.js, both already included immediately above.
 */
/*
 * AstrometricResiduals is NEW IN 1.9.5 and Loom now runs on 1.9.4.
 *
 * Included conditionally, because an unresolvable #include is not an
 * error in PJSR: the core discards the WHOLE script, with no message, no
 * console output and exit status 0. On 1.9.4 an unconditional include
 * here would not degrade solve verification -- it would make Loom do
 * nothing at all, undiagnosably.
 *
 * __PI_RELEASE__ is the preprocessor's own symbol, so the decision is
 * taken before the file is parsed, which is the only point at which it
 * CAN be taken. LOOM_HAVE_RESIDUALS then guards the one function that
 * needs the class.
 */
#ifoneof __PI_RELEASE__ 5 6 7 8 9
#define LOOM_HAVE_RESIDUALS 1
#include <pjsr/astrometry/AstrometricResiduals.js>
#endif
#include <pjsr/astrometry/SearchCoordinatesDialog.js>
#include <pjsr/astrometry/CatalogDownloaderDialog.js>
#include <pjsr/astrometry/ProjectionConfigurationDialog.js>
#include <pjsr/astrometry/UtilityControls.js>
#include <pjsr/astrometry/VizierMirrorDialog.js>
#include <pjsr/controls/DateTimeEditor.js>
#include <pjsr/controls/GeodeticCoordinatesEditor.js>
/*
 * The engine include, and the only one that cannot be written as an
 * ordinary <pjsr/...> path: ImageSolverEngine.js ships under src/scripts,
 * not under include.
 *
 * It used to be the absolute path /Applications/PixInsight/src/scripts/...
 * which is macOS-only, and #include is resolved at PARSE time so it cannot
 * be given a runtime value like CoreApplication.srcDirPath.
 *
 * Angle brackets resolve relative to the core's include directory
 * (<base>/include), so "../src/scripts/..." walks back to <base>/src and
 * lands on the engine whatever <base> is and whatever platform this is.
 * Verified on macOS 2026-09-17: the include resolves and ImageSolver is a
 * function afterwards.
 *
 * If this ever fails to resolve, the symptom is NOT an error. PixInsight
 * discards a script with an unresolvable include silently -- no message,
 * no console output, exit status 0. A Loom that "does nothing at all"
 * starts here.
 */
#include <../src/scripts/ImageSolver/ImageSolverEngine.js>

var Steps = {};

/*
 * ASI2600MM (Sony IMX571) quantum-efficiency curve, confirmed present in
 * <PixInsight>/library/filters.xspd as the channel="Q" entry
 * named "Sony IMX411/455/461/533/571". deviceQECurve and
 * deviceQECurveName are a matched pair (verified-parameters.md, Finding 4)
 * -- both must be set together on SPFC and SPCC, never the name alone.
 */

/*
 * Where the core is installed, asked of the core rather than assumed.
 *
 * CoreApplication.baseDirPath is /Applications/PixInsight on this Mac
 * and C:/Program Files/PixInsight on a default Windows install -- and,
 * more to the point, whatever the user actually chose in either case.
 * PJSR reports Windows paths with forward slashes, so nothing below needs
 * a separator.
 */
Steps.PI_BASE_DIR = CoreApplication.baseDirPath;
Steps.PI_SRC_SCRIPTS_DIR = CoreApplication.srcDirPath + "/scripts";

// Path to the PixInsight spectrum database that holds both device QE
// curves and filter transmission curves, per verified-parameters.md
// ("Filter-name-to-curve lookup").
Steps.FILTERS_XSPD_PATH = Steps.PI_BASE_DIR + "/library/filters.xspd";

/*
 * True if a process of this name is installed. Checked during preflight
 * so a missing module names itself rather than surfacing as an
 * undefined-symbol error mid-run. Global process constructors (e.g.
 * StarAlignment, GraXpert) are bound as bare global identifiers in PJSR,
 * so an eval-based lookup wrapped in try/catch is the reliable way to
 * test for their existence without throwing a ReferenceError up the
 * stack -- an unrecognized name (or a script-only symbol like
 * ImageSolver that is not registered as a process) correctly returns
 * false rather than throwing.
 */
Steps.moduleAvailable = function( name )
{
   try { return typeof eval( name ) == "function"; }
   catch ( e ) { return false; }
};

/*
 * Median of the centred sub-rectangle. Registration leaves per-channel
 * borders of zeros; measuring the centre keeps them out of the
 * comparison that picks the LinearFit reference.
 */
/*
 * The rectangle of actually-imaged pixels in a registered view.
 * StarAlignment fills the area outside the transformed frame with zeros,
 * so the valid area is found by walking in from each edge until a row or
 * column contains real signal. Uses one maximum() per strip -- O(w+h)
 * statistics calls rather than O(w*h) pixel reads.
 *
 * `margin` trims a few extra pixels to drop the interpolation fringe that
 * can leave small non-zero values just inside the true boundary.
 */
Steps.validRect = function( view, margin )
{
   var img = view.image;
   var W = img.width, H = img.height;
   var saved = img.selectedRect;
   var m = ( margin == null ) ? 4 : margin;

   /*
    * MEDIAN, not maximum. Registration fill is uniformly zero across a
    * whole strip, so its median is 0; a real row has noise and a positive
    * median even where the sky is dark. Using maximum() meant a single hot
    * pixel or cosmic ray in the fill stopped the scan and left a black
    * band in the result.
    *
    * The limitation this cannot solve: a row of REAL data that is exactly
    * zero across more than half its width is indistinguishable from fill
    * and will be trimmed. Background extraction can clip values to zero,
    * so that is a genuine (if unlikely) possibility at a frame edge.
    */
   function rowHasSignal( y )
   {
      img.selectedRect = new Rect( 0, y, W, y+1 );
      return img.median() > 0;
   }
   function colHasSignal( x )
   {
      img.selectedRect = new Rect( x, 0, x+1, H );
      return img.median() > 0;
   }

   var y0 = 0;      while ( y0 < H-1 && !rowHasSignal( y0 ) ) ++y0;
   var y1 = H-1;    while ( y1 > y0  && !rowHasSignal( y1 ) ) --y1;
   var x0 = 0;      while ( x0 < W-1 && !colHasSignal( x0 ) ) ++x0;
   var x1 = W-1;    while ( x1 > x0  && !colHasSignal( x1 ) ) --x1;

   img.selectedRect = saved;

   return { x0: Math.min( x0 + m, W ), y0: Math.min( y0 + m, H ),
            x1: Math.max( x1 + 1 - m, 0 ), y1: Math.max( y1 + 1 - m, 0 ) };
};

/*
 * Crops a view in place to `rect` ({x0,y0,x1,y1}) using the Crop PROCESS,
 * not Image.cropTo.
 *
 * Image.cropTo moves pixels without touching the astrometric solution, so
 * the WCS afterwards describes the uncropped frame -- wrong, and silently
 * so. It also makes the solution uncopyable to another window
 * ("AstrometricMetadata::Write(): Incompatible image dimensions"). The
 * Crop process updates the solution along with the geometry.
 *
 * Margins are negative to trim: left/top are how much to remove from each
 * edge, right/bottom likewise.
 */
Steps.cropTo = function( view, rect )
{
   var img = view.image;
   var P = new Crop;
   P.mode = Crop.AbsolutePixels;
   P.leftMargin   = -rect.x0;
   P.topMargin    = -rect.y0;
   P.rightMargin  = -( img.width  - rect.x1 );
   P.bottomMargin = -( img.height - rect.y1 );
   P.noGUIMessages = true;
   if ( !P.executeOn( view ) )
      throw new Error( "Crop failed on " + view.id );
};

/*
 * Marks a window as a Loom output, so a plate can be identified as one
 * later -- in a saved file, or by eye in the FITS header.
 *
 * It is NOT a licence to close it. An earlier version used this marker to
 * sweep away previous runs' results at startup, which closed plates
 * someone had deliberately kept. Loom closes what the current run created
 * and nothing else.
 */
Steps.LOOM_OUTPUT_KEYWORD = "LOOMOUT";

Steps.markAsOutput = function( window, label )
{
   try
   {
      var kws = window.keywords;
      kws.push( new FITSKeyword( Steps.LOOM_OUTPUT_KEYWORD, "'" + label + "'",
                                 "Loom pipeline output" ) );
      window.keywords = kws;
   }
   catch ( e ) { Util.warn( "output", "could not mark " + label + ": " + e ); }
};

Steps.medianOfCentre = function( view, fraction )
{
   var img = view.image;
   var r = Util.centralRect( img.width, img.height, fraction );
   var saved = img.selectedRect;
   img.selectedRect = new Rect( r.x0, r.y0, r.x1, r.y1 );
   var m = img.median();
   img.selectedRect = saved;
   return m;
};

/*
 * Looks up a filter transmission curve in filters.xspd by name, for the
 * SPFC gray/red/green/blue *FilterTrCurve fields. Per
 * verified-parameters.md, filters.xspd holds both device QE curves
 * (channel="Q") and filter transmission curves (channel="R"/"G"/"B"/
 * "L"/"PAN"/etc.) as sibling <Filter name="..." channel="..."
 * data="..."> elements, one element per line.
 *
 * Matching: exact (case-insensitive) name match first. If that fails,
 * a one-directional case-insensitive substring match -- the FITS
 * FILTER keyword text must appear inside a library filter name (e.g.
 * "Ha" inside "Astrodon Ha 3nm") -- and ONLY when the FILTER keyword is
 * at least 3 characters long. Bare single-letter LHSO channel labels
 * like "L", "R", "G", "B" are exactly the FITS FILTER values this
 * pipeline's own data plausibly contains, and matching a 1-2 character
 * target against library names bidirectionally (or even
 * unidirectionally without a length floor) would return an arbitrary
 * alphabetically-first library entry instead of the correct curve or
 * no match -- a silently wrong result that looks plausible. Below the
 * length floor, the lookup falls through to the documented no-match
 * path.
 *
 * Returns { data: <transmission curve CSV string>, name: <matched
 * library filter name> } on a match, or null if no match exists. A
 * null return means the caller must fall back to
 * wavelength/bandwidth-only fields and leave the *FilterTrCurve field
 * empty -- see verified-parameters.md's filter-lookup section, which
 * flags this fallback as unverified against a live SPFC run and asks
 * callers to be explicit about which path was taken (see
 * Steps.spfc's logging).
 */
/*
 * Looks up a device QE curve by name in filters.xspd (channel="Q").
 * Returns { name, data } or null. Reading the library at run time means
 * the curve is never a stale copy pasted into this file.
 */
/*
 * Every filter in filters.xspd for a given channel ("L", "R", "G", "B"),
 * as { name, data }. Used to populate the dialog's filter selectors: a
 * FITS FILTER value of "L" names the channel, not the physical filter,
 * so the actual filter can only come from the user.
 */
/*
 * Reads a saved SpectrophotometricFluxCalibration process icon, if the
 * user has one on the workspace, and returns the filters and QE curve it
 * is configured with. This is how Loom picks up the settings a user has
 * already dialled in -- a freshly constructed process carries factory
 * defaults (an EMPTY grayFilterTrCurve), not their configuration, so
 * there is no other way to inherit it.
 *
 * Returns null when no icon exists; callers then fall back to generic
 * choices and let the user pick in the dialog.
 */
Steps.configuredSPFC = function()
{
   try
   {
      var icons = ProcessInstance.iconsByProcessId( "SpectrophotometricFluxCalibration" );
      if ( !icons || icons.length == 0 )
         return null;
      var P = ProcessInstance.fromIcon( icons[0] );
      if ( P == null )
         return null;
      return {
         iconName: icons[0],
         grayFilterName:  P.grayFilterName,
         redFilterName:   P.redFilterName,
         greenFilterName: P.greenFilterName,
         blueFilterName:  P.blueFilterName,
         deviceQECurveName: P.deviceQECurveName,
         deviceQECurve:     P.deviceQECurve
      };
   }
   catch ( e )
   {
      return null;
   }
};

/*
 * Reads a saved MultiscaleGradientCorrection process icon for its MARS
 * database selection. A freshly constructed MGC has none -- hence "No
 * MARS database files have been selected" -- and a script cannot read
 * which databases the user has registered, so an icon is the only way to
 * inherit their choice without hardcoding paths.
 */
/*
 * Where PixInsight persists the MARS databases the user configured in the
 * MultiscaleGradientCorrection interface.
 *
 * This is NOT reachable through the Settings API: Settings.read and
 * Settings.readGlobal are namespaced to the calling script and return
 * lastReadOK = false for module keys (verified 2026-09-14). A fresh
 * MultiscaleGradientCorrection instance likewise carries
 * marsDatabaseFiles = [] -- the configuration lives in the core settings
 * file, not in the process defaults.
 *
 * The file is XML, and the value is element TEXT rather than a v= attribute:
 *
 *   <v k="MARSDatabaseFilePath000" t="s">/path/to/MARS-DR2-1.0.3-s08.xmars</v>
 *
 * Generic location, no user-specific path baked in.
 *
 * Asked of the core rather than spelled out: CoreApplication.configDirPath
 * is exactly this directory -- "~/Library/PixInsight" on macOS, the
 * per-user configuration folder on Windows -- so no platform branch is
 * needed and a relocated configuration is still found. Probed on macOS
 * 2026-09-17: it returns the same path the previous literal built.
 */
Steps.CORE_SETTINGS_DIR = CoreApplication.configDirPath;

Steps.marsDatabasesFromCoreSettings = function()
{
   var paths = [];
   try
   {
      var files = Steps.coreSettingsFiles( Steps.CORE_SETTINGS_DIR );
      for ( var i = 0; i < files.length; ++i )
      {
         var text = "";
         try { text = File.readTextFile( files[i] ); } catch ( e ) { continue; }
         Steps.addExistingMarsDatabases( paths, Steps.marsPathsInSettings( text ) );
      }
   }
   catch ( e )
   {
      Util.warn( "mgc", "could not read PixInsight core settings: " + e );
   }
   return paths;
};

// Every core-*-pxi.settings file in `dir`: one per PixInsight instance slot.
Steps.coreSettingsFiles = function( dir )
{
   var files = [];
   var ff = new FileFind;
   if ( !ff.begin( dir + "/core-*-pxi.settings" ) )
      return files;
   do
   {
      if ( !ff.isDirectory && ff.name != "." && ff.name != ".." )
         files.push( dir + "/" + ff.name );
   }
   while ( ff.next() );
   return files;
};

// The MARS database paths a settings file lists, trimmed, blanks dropped.
Steps.marsPathsInSettings = function( text )
{
   var found = [];
   var re = /<v\s+k="MARSDatabaseFilePath\d+"[^>]*>([^<]+)<\/v>/g, m;
   while ( ( m = re.exec( text ) ) != null )
   {
      var p = m[1].trim();
      if ( p.length > 0 )
         found.push( p );
   }
   return found;
};

/*
 * Appends each of `candidates` to `paths` once, warning about -- and
 * skipping -- any that the settings list but the disk does not have.
 */
Steps.addExistingMarsDatabases = function( paths, candidates )
{
   for ( var i = 0; i < candidates.length; ++i )
   {
      var p = candidates[i];
      if ( !File.exists( p ) )
      {
         Util.warn( "mgc", "MARS database listed in PixInsight settings " +
                           "does not exist: " + p );
         continue;
      }
      if ( paths.indexOf( p ) < 0 )
         paths.push( p );
   }
};

/*
 * The MARS databases to use, from the user's own PixInsight configuration.
 *
 * A saved process icon wins when present -- it is an explicit, current
 * statement of intent. Otherwise fall back to what the MGC interface has
 * persisted, so a working MARS setup is used without requiring the user to
 * park an icon on the workspace.
 */
/*
 * Every *.xmars file directly inside `dir`, sorted for a stable cache key.
 * Returns [] when the directory is absent or holds none, which the caller
 * treats as "this override gave nothing" rather than an error.
 */
Steps.marsDatabasesInDirectory = function( dir )
{
   var out = [];
   try
   {
      if ( !dir || String( dir ).length == 0 || !File.directoryExists( dir ) )
         return out;
      var find = new FileFind;
      if ( find.begin( dir + "/*.xmars" ) )
         do
         {
            if ( !find.isDirectory )
               out.push( dir + "/" + find.name );
         }
         while ( find.next() );
   }
   catch ( e ) {}
   out.sort();
   return out;
};

/*
 * `marsDir`, when given, is the user's explicit choice from the dialog and
 * outranks everything else -- a path the user typed is a statement of intent
 * at least as strong as a process icon, and unlike PixInsight's own settings
 * it cannot be silently changed by another tool.
 *
 * It is not a REQUIREMENT, though: an override that yields no .xmars files
 * falls through to the automatic routes below rather than failing, so a typo
 * or a detached volume degrades to the previous behaviour instead of
 * breaking a run that would otherwise work.
 */
Steps.configuredMGC = function( marsDir )
{
   var fromDir = Steps.marsDatabasesInDirectory( marsDir );
   if ( fromDir.length > 0 )
   {
      var chosen = [];
      for ( var d = 0; d < fromDir.length; ++d )
         chosen.push( [ true, fromDir[d] ] );
      return { iconName: null,
               marsDatabaseFiles: chosen,
               useMARSDatabase: true,
               source: "configured folder " + marsDir };
   }

   try
   {
      var icons = ProcessInstance.iconsByProcessId( "MultiscaleGradientCorrection" );
      if ( icons && icons.length > 0 )
      {
         var P = ProcessInstance.fromIcon( icons[0] );
         if ( P != null && P.marsDatabaseFiles && P.marsDatabaseFiles.length > 0 )
            return { iconName: icons[0],
                     marsDatabaseFiles: P.marsDatabaseFiles,
                     useMARSDatabase: P.useMARSDatabase,
                     source: "process icon '" + icons[0] + "'" };
      }
   }
   catch ( e ) { /* fall through to the settings file */ }

   var found = Steps.marsDatabasesFromCoreSettings();
   if ( found.length == 0 )
      return null;

   // marsDatabaseFiles is an array of [ enabled, path ] pairs.
   var list = [];
   for ( var i = 0; i < found.length; ++i )
      list.push( [ true, found[i] ] );
   return { iconName: null,
            marsDatabaseFiles: list,
            useMARSDatabase: true,
            source: "PixInsight settings" };
};

Steps.listFilterCurves = function( channel )
{
   var text = File.readTextFile( Steps.FILTERS_XSPD_PATH );
   var re = new RegExp( '<Filter\\s+name="([^"]*)"\\s+channel="' + channel +
                        '"[^>]*?data="([^"]*)"', "g" );
   var out = [], m;
   while ( (m = re.exec( text )) != null )
      out.push( { name: m[1], data: m[2] } );
   return out;
};

/* A named filter curve, any case: of `channel` ("Q" for a device's QE curve), else of any channel. */
Steps.filterCurveByName = function( name, channel )
{
   if ( name == null || name.length == 0 )
      return null;
   var curves = Steps.listFilterCurves( channel || '[^"]*' ), target = String( name ).toLowerCase();
   for ( var i = 0; i < curves.length; ++i )
      if ( curves[i].name.toLowerCase() == target )
         return curves[i];
   return null;
};

/*
 * The QE curve to use for an image, taken from its own camera. Falls back
 * to the Ideal QE curve when the camera is unknown -- a neutral response
 * is honest, whereas another sensor's curve would silently bias the
 * calibration.
 */
Steps.deviceCurveForImage = function( instrume )
{
   var name = Util.qeCurveNameForCamera( instrume );
   if ( name != null )
   {
      var c = Steps.filterCurveByName( name, "Q" );
      if ( c != null )
      {
         Util.log( "qe", "camera '" + instrume + "' -> " + c.name );
         return c;
      }
      Util.warn( "qe", "curve '" + name + "' not found in filters.xspd" );
   }
   var ideal = Steps.filterCurveByName( Util.IDEAL_QE_CURVE_NAME, "Q" );
   Util.log( "qe", "camera " + ( instrume ? "'" + instrume + "' unrecognised" : "unknown" ) +
                   " -> " + Util.IDEAL_QE_CURVE_NAME );
   return ideal;
};

Steps.lookupFilterCurve = function( filterName )
{
   if ( !filterName )
      return null;

   var text = File.readTextFile( Steps.FILTERS_XSPD_PATH );
   var lines = text.split( "\n" );
   var target = filterName.trim().toLowerCase();

   var re = /<Filter\s+name="([^"]*)"\s+channel="([^"]*)"\s+data="([^"]*)"/;

   var substringMatch = null;

   for ( var i = 0; i < lines.length; ++i )
   {
      var m = re.exec( lines[i] );
      if ( !m )
         continue;
      var name = m[1];
      var lname = name.toLowerCase();
      if ( lname == target )
      {
         Util.log( "lookupFilterCurve", "'" + filterName + "' -> exact match '" + name + "'" );
         return { data: m[3], name: name };
      }
      if ( substringMatch == null && target.length >= 3 && lname.indexOf( target ) >= 0 )
         substringMatch = { data: m[3], name: name };
   }

   if ( substringMatch != null )
      Util.log( "lookupFilterCurve", "'" + filterName + "' -> substring match '" + substringMatch.name + "'" );
   else
      Util.log( "lookupFilterCurve", "'" + filterName + "' -> no match" );

   return substringMatch;
};

/*
 * True if the window's FITS header already carries a WCS solution
 * (CTYPE1 present and non-blank). The owner's masters come out of WBPP
 * already plate-solved, so Steps.solve must recognize that and skip --
 * re-solving is wasted work at best and, since the solver can legitimately
 * fail to converge on some fields, a needless point of failure at worst.
 */
/*
 * ImageWindow exposes this directly. The previous check sniffed the
 * CTYPE1 FITS keyword, which misses solutions stored as XISF properties
 * -- exactly the case for WBPP's drizzled masters, which carry a full
 * solution but only 28 FITS keywords.
 */
Steps.hasAstrometricSolution = function( window )
{
   try { return window.hasAstrometricSolution; }
   catch ( e ) { return Util.keywordValue( window.keywords, "CTYPE1" ) !== null; }
};

/* ------------------------------------------------------------------ */
/* Verification of the astrometric solution                            */
/* ------------------------------------------------------------------ */

/*
 * Parameters of the residual measurement.
 *
 * Every value is copied verbatim from VerifierConfiguration's defaults in
 * <PixInsight>/src/scripts/AstrometricSolutionVerifier/AstrometricSolutionVerifierEngine.js
 * (lines 105-126), which are in turn the ImageSolver defaults for star
 * detection and PSF fitting. They are written out here, as a plain
 * object, rather than constructed through VerifierConfiguration, and that
 * is the point:
 *
 * VerifierConfiguration extends PersistentObject. Constructing it and
 * calling LoadSettings()/SaveSettings() reads and WRITES the user's saved
 * AstrometricSolutionVerifier configuration, so driving the script's own
 * config object would quietly replace settings the user chose in that
 * script's dialog. AstrometricResiduals asks only for these properties
 * (see its header comment), so a literal satisfies it completely and
 * touches no Settings at all.
 */
Steps.RESIDUALS_CONFIG = {
   // Star detection and PSF fitting.
   structureLayers: 5,
   minStructureSize: 0,
   hotPixelFilterRadius: 1,
   noiseReductionFilterRadius: 0,
   sensitivity: 0.5,
   peakResponse: 0.5,
   brightThreshold: 3.0,
   maxStarDistortion: 0.6,
   autoPSF: false,
   // Catalog selection. autoMagnitude searches for the limit magnitude
   // that yields about 1.5x the detected star count, so it adapts to the
   // field instead of over-fetching on a dense one.
   autoMagnitude: true,
   magnitude: 16,
   restrictToHQStars: false,
   // Matching and clipping.
   matchingTolerance: 3.0,   // px
   rejectionSigma: 5.0       // sigma clipping of deviations
};

/*
 * The two numbers the verdict is drawn against. Both are medians of the
 * deviation in PIXELS, and pixels is the scale-relative form the check
 * needs: a residual is only meaningful next to the plate scale, and
 * arcsec/px is exactly the conversion between the two.
 *
 * RESIDUALS_BAD_PX = 3.0 px is the verifier's own matchingTolerance
 * (above). It is the radius inside which a detected star is accepted as
 * the same star as a catalog entry, so it is the largest deviation the
 * measurement can even represent. A median at that level means the
 * correspondences themselves are no longer trustworthy. It is a limit of
 * the measurement, not a taste.
 *
 * RESIDUALS_WARN_PX = 0.315 px is the median deviation against Gaia DR3
 * that PixInsight's pre-1.9.5 global-surface-spline solver achieved on a
 * mosaic panel with 34,000 control points, published in the 1.9.5 release
 * announcement alongside the 0.091 px the new recursive surface splines
 * achieved on the same panel. It is therefore a bar a real solver cleared
 * on a genuinely hard field. Doing worse than the solver Loom has just
 * superseded is worth saying out loud.
 *
 * No threshold is attached to the RMS. Both published figures are
 * medians, RMS >= median by construction, and reusing a median limit on
 * an RMS would fire on solutions that are fine. The RMS is reported --
 * it is the number that shows a tail of bad corners a median hides --
 * but it is reported, not judged.
 */
Steps.RESIDUALS_WARN_PX = 0.315;
Steps.RESIDUALS_BAD_PX  = 3.0;

/*
 * The verdict, as a pure function of the measured median deviation in
 * pixels. Separated from the measurement so it can be tested for values
 * no image on this machine happens to produce.
 *
 * Returns "ok", "poor" or "bad"; "unknown" when there is no number, which
 * is what a measurement that found too few stars leaves behind.
 */
Steps.residualVerdict = function( medianPx )
{
   if ( typeof medianPx != "number" || !isFinite( medianPx ) || medianPx < 0 )
      return "unknown";
   if ( medianPx >= Steps.RESIDUALS_BAD_PX )
      return "bad";
   if ( medianPx > Steps.RESIDUALS_WARN_PX )
      return "poor";
   return "ok";
};

/*
 * EVERY solve is verified, not just the first one of a run, and the
 * reason is that the cost was measured rather than guessed.
 *
 * The worry was real: Loom solves up to seven channels plus the RGB
 * composite plus one composite per narrowband palette, and a verification
 * is a full star detection, PSF fit and Gaia search over the frame -- the
 * solver's own work, paid again. An easy assumption is that it must
 * therefore be run once, on the reference, since every channel is the
 * same field through the same optics.
 *
 * Measured instead, on the owner's own data (NGC 5907 masterLight L
 * autocrop, 5710x3182 at 0.966 arcsec/px, 1.9.5 build 1702, 2026-09-18):
 *
 *    verification          0.76 s   (230 matched stars)
 *    the solve itself      0.96 s
 *
 * Under a second. Nine of them is some seven seconds on a run that takes
 * many minutes, so the saving was never worth what it costs: every
 * channel is solved INDEPENDENTLY, so every channel's solution can be
 * independently wrong, and SPFC runs per channel against that channel's
 * own solution. Verifying only the reference would check the one thing
 * and trust the other eight.
 *
 * The cost scales with the number of matched stars, so a dense field will
 * be dearer than 0.76 s. It will not become comparable to the rest of a
 * Loom run.
 */

/*
 * Measures the astrometric solution of `window` against Gaia and returns
 * { n, medianPx, rmsPx, maxPx, medianArcsec, rmsArcsec, maxArcsec,
 *   arcsecPerPx, biasRaArcsec, biasDecArcsec, verdict }, or null if it
 * could not be measured.
 *
 * Deliberately NOT the AstrometricSolutionVerifier engine, though that is
 * the script this measurement belongs to. Three reasons, in order of
 * weight:
 *
 *   1. Its verify() is the reporting half: it prints a 98-column report
 *      and per-cell grid tables, and by default opens a false-colour
 *      deviation map and a graphs window (mapMode FalseColor, showGraphs
 *      true). Loom runs unattended across many channels; two windows per
 *      solve over the user's workspace is not acceptable, and suppressing
 *      them means setting the config fields anyway.
 *   2. Its VerifierConfiguration extends PersistentObject and so reads
 *      and writes the user's saved settings -- see Steps.RESIDUALS_CONFIG.
 *   3. It lives under src/scripts/ and would have to be reached with the
 *      same <../src/scripts/...> form as ImageSolverEngine.js, and it
 *      needs <pjsr/astrometry/AstrometricPlot.js> as well, which its own
 *      file does not include.
 *
 * The measurement itself is not duplicated: AstrometricResiduals.measure()
 * is exactly what the verifier calls, and its own header says it is shared
 * "so that every script that measures an astrometric solution measures it
 * in exactly the same way".
 */
Steps.verifySolution = function( window )
{
#ifndef LOOM_HAVE_RESIDUALS
   /*
    * 1.9.4: the verifier does not exist. Reported once, as a fact rather
    * than a failure -- the solve itself is unaffected, only the check on
    * it, and a run must not stop because it cannot measure its own work.
    */
   Util.warn( "solve", "this PixInsight has no AstrometricResiduals " +
                       "(new in 1.9.5), so the solution is not verified" );
   return null;
#endif
   var residuals = new AstrometricResiduals( Steps.RESIDUALS_CONFIG );
   var M = residuals.measure( window );   // throws when it cannot measure

   /*
    * Statistics of the retained set, i.e. after 5-sigma clipping of the
    * deviations. Statistics of ALL matches are dominated by the handful
    * of bad correspondences the clipping exists to remove.
    *
    * M.result.retained, NOT M.retained: measure() returns the retained
    * MATCHES as M.retained (a plain array) and their STATISTICS as
    * M.result.retained. Reading the array cost a run -- it has no .px --
    * and the two names are one character apart.
    */
   var S = M.result.retained;
   // metadata.resolution is in DEGREES per pixel; the verifier prints
   // 3600*resolution as arcsec/px (AstrometricSolutionVerifierEngine.js:274).
   var arcsecPerPx = 3600 * M.metadata.resolution;

   return {
      n: S.n,
      medianPx: S.px.median,
      rmsPx: S.px.rms,
      maxPx: S.px.max,
      medianArcsec: S.as.median,
      rmsArcsec: S.as.rms,
      maxArcsec: S.as.max,
      arcsecPerPx: arcsecPerPx,
      biasRaArcsec: S.bias.dra,
      biasDecArcsec: S.bias.ddec,
      verdict: Steps.residualVerdict( S.px.median )
   };
};

/*
 * Verifies and reports. Never throws: a verification that cannot run is a
 * lost diagnostic, not a reason to lose the pipeline -- the solution it
 * was going to judge is still there and still usable. Returns the
 * measurement, or null if it could not be made.
 */
Steps.verifyAndReport = function( window, label )
{
   var r;
   try
   {
      Util.reportStage( "verifying astrometric solution \u2192 " + label );
      r = Steps.verifySolution( window );
   }
   catch ( e )
   {
      Util.warn( "verify", label + ": the astrometric solution could not be " +
                           "verified (" + e + "). The solution itself is " +
                           "unchanged and the run continues." );
      return null;
   }

   /*
    * Null means the core has no verifier -- 1.9.4, where
    * AstrometricResiduals does not exist. Not a failure: the solve
    * happened and is unchanged, only the check on it is unavailable, and
    * verifySolution has already said so once.
    */
   if ( r == null )
      return null;

   var summary = label + ": " + r.n + " stars, residual RMS " +
                 r.rmsArcsec.toFixed( 3 ) + "\" (" + r.rmsPx.toFixed( 3 ) +
                 " px), median " + r.medianArcsec.toFixed( 3 ) + "\" (" +
                 r.medianPx.toFixed( 3 ) + " px), max " +
                 r.maxArcsec.toFixed( 3 ) + "\", at " +
                 r.arcsecPerPx.toFixed( 3 ) + "\"/px";

   if ( r.verdict == "bad" )
      Util.warn( "verify", summary + " -- THE SOLUTION IS WRONG. The median " +
                           "deviation has reached the " +
                           Steps.RESIDUALS_BAD_PX.toFixed( 1 ) + " px matching " +
                           "tolerance, so detected stars are no longer being " +
                           "paired with the right catalog stars. SPFC and SPCC " +
                           "will calibrate flux and colour against the wrong " +
                           "sky positions." );
   else if ( r.verdict == "poor" )
      Util.warn( "verify", summary + " -- POOR. The median deviation is worse " +
                           "than the " + Steps.RESIDUALS_WARN_PX.toFixed( 3 ) +
                           " px PixInsight's previous solver reached on a hard " +
                           "mosaic panel, so check the field edges before " +
                           "trusting SPFC and SPCC." );
   else if ( r.verdict == "unknown" )
      Util.warn( "verify", summary + " -- no usable deviation statistic; " +
                           "reported without a verdict." );
   else
      Util.log( "verify", summary );

   return r;
};

Steps.solve = function( view )
{
   Util.reportStage( "plate solving \u2192 " + view.id );
   var window = view.window;
   if ( Steps.hasAstrometricSolution( window ) )
   {
      Util.log( "solve", view.id + " already has an astrometric solution -- skipping" );
      // Verified anyway, and this is the important case rather than the
      // exception: the owner's masters come out of WBPP already solved, so
      // the solution SPFC and SPCC actually depend on is usually one Loom
      // never computed and has no other way to judge.
      Steps.verifyAndReport( window, view.id );
      return;
   }

   Util.log( "solve", view.id );
   var engine = new ImageSolver;
   engine.initialize( window, false /*prioritizeSettings*/ );

   /*
    * No catalog override. engine.initialize() calls
    * solverCfg.LoadSettings(), which restores the user's own ImageSolver
    * configuration -- the same one their manual solves use, including
    * whichever local database they have registered. Forcing catalogMode
    * or catalog here discards that and was what made the solver reach for
    * an online catalog when a perfectly good local one was configured.
    */

   /*
    * Recursive surface splines, new in 1.9.5 (ImageSolverEngine.js:129,
    * read at :565 and :939), and the one setting Loom does override.
    *
    * It models distortion as a partition of unity of local surface
    * splines over a quadtree on top of the projective model, using every
    * matched star after robust outlier rejection, instead of fitting one
    * global function to a capped set of control points. On the mosaic
    * panel with 34,000 control points published in the 1.9.5
    * announcement, the median deviation against Gaia DR3 went from 0.315
    * px to 0.091 px -- roughly one arcsecond to a third of one -- and the
    * solve was faster, not slower.
    *
    * That matters here specifically because unmodelled distortion is
    * worst at the field edges, and it is edge stars whose wrong positions
    * feed SPFC and SPCC the wrong catalog flux.
    *
    * The core default is false, so this has to be set explicitly, and it
    * has to be set HERE -- after initialize(), because initialize() calls
    * solverCfg.LoadSettings() and would otherwise overwrite it with
    * whatever the user last saved from the ImageSolver dialog. This is
    * the one deliberate exception to the "do not override the user's
    * solver configuration" rule above; it is safe because the engine
    * never calls SaveSettings (verified: the string does not appear in
    * ImageSolverEngine.js), so the override lives and dies with this
    * ImageSolver instance and the user's saved configuration is untouched.
    */
   /*
    * Silently inert before 1.9.5: assigning a property a process does not
    * have is not an error in PJSR, so on 1.9.4 this line reads as "the
    * setting had no effect" rather than announcing itself. The solve is
    * less accurate there; nothing breaks.
    */
   engine.solverCfg.recursiveSplines = true;

   engine.solveImage( window );   // throws on failure

   Steps.verifyAndReport( window, view.id );

   /*
    * Deliberately NOT engine.SaveImage( window ).
    *
    * SolveImage already writes the solution into the target window --
    * metadata.SaveKeywords() and SaveProperties(), ImageSolver.js:3760 --
    * so nothing more is needed to have a solved image in memory.
    *
    * SaveImage only writes a FILE, and it builds the path from
    * window.filePath (ImageSolver.js:3840). A window Loom created in memory
    * has no path, so it produced "/_ast.xisf" and left the result bound to
    * that junk filename, which is what PixInsight then showed in the title
    * bar. ImageSolver itself only calls it when solving files from disk.
    *
    * (The old call was also spelled `saveImage`; the method is `SaveImage`,
    * so it would have thrown had it ever been reached on an unsolved image.)
    */
};

/*
 * SPFC on a single mono channel. filterName is the FITS FILTER keyword
 * value for this frame. channelKey is the LHSO channel this view was
 * selected under (one of "L","R","G","B","H","S","O"), or null/"RGB"
 * for the combined-composite call site.
 *
 * The project owner has confirmed directly: SPFC is not applied to
 * narrowband channels in this pipeline. narrowbandMode is therefore
 * always false -- it is never inferred from filter name text, because
 * a scientific parameter must never be guessed from a string when the
 * caller already holds the fact. channelKey makes that fact
 * enforceable: passing H/S/O throws immediately rather than silently
 * running SPFC on a narrowband frame.
 */
Steps.spfc = function( view, filterName, channelKey, instrume, chosenFilter )
{
   Util.reportStage( "flux calibration (SPFC) \u2192 " + ( channelKey || view.id ) );
   Util.log( "spfc", view.id + " filter=" + filterName + " channel=" + channelKey );

   if ( channelKey != null && channelKey != "RGB" && !Util.isBroadband( channelKey ) )
      throw new Error( "SPFC is not applied to narrowband channels; got channelKey=" + channelKey );

   /*
    * A FILTER keyword of "L"/"R"/"G"/"B" names the channel, not the
    * physical filter, so it usually has no match in filters.xspd. When
    * that happens we must NOT write an empty transmission curve: SPFC's
    * own configuration already holds the real filter (e.g. "Astronomik
    * UV-IR Block L-2"), and overwriting it with nothing calibrates flux
    * against no filter response at all -- wrong, and silently so.
    */
   // The user's chosen filter wins; the FITS FILTER value is only a
   // fallback for the rare case where it really does name a filter.
   var curve = Steps.filterCurveByName( chosenFilter );
   if ( curve == null )
      curve = Steps.lookupFilterCurve( filterName );

   var P = new SpectrophotometricFluxCalibration;
   // Only what this pipeline actually needs is set. catalogId and
   // autoLimitMagnitude are left alone: they already match the factory
   // defaults, and overriding settings the user has configured is how the
   // plate solver ended up downloading.
   P.narrowbandMode = false; // this pipeline never runs SPFC on narrowband channels
   // No graph/report windows: this runs unattended over several channels,
   // and each one would open a window over the user's workspace.
   P.generateGraphs = false;
   P.generateStarMaps = false;
   P.generateTextFiles = false;
   var qe = Steps.deviceCurveForImage( instrume );
   if ( qe != null )
   {
      P.deviceQECurveName = qe.name;
      P.deviceQECurve = qe.data;
   }
   if ( channelKey == "RGB" )
   {
      /*
       * The composite is a three-channel image: SPFC needs the red, green
       * and blue transmission curves, not the gray one. chosenFilter is a
       * { R, G, B } map of filter names here.
       */
      var rgbNames = chosenFilter || {};
      var trio = [ [ "R", "red" ], [ "G", "green" ], [ "B", "blue" ] ];
      for ( var t = 0; t < trio.length; ++t )
      {
         var ck = trio[t][0], prefix = trio[t][1];
         var c = Steps.filterCurveByName( rgbNames[ck] );
         if ( c == null )
            throw new Error( "No filter curve for the " + ck + " channel. SPFC " +
                             "cannot calibrate the RGB composite without red, " +
                             "green and blue curves: choose filters in Loom's " +
                             "dialog." );
         P[prefix + "FilterName"] = c.name;
         P[prefix + "FilterTrCurve"] = c.data;
         Util.log( "spfc", prefix + " filter -> " + c.name );
      }
   }
   else if ( curve != null )
   {
      P.grayFilterName = curve.name;
      P.grayFilterTrCurve = curve.data;
      Util.log( "spfc", "filter '" + filterName + "' -> " + curve.name );
   }
   else
      throw new Error( "No filter curve for channel " + channelKey +
                       ". SPFC cannot calibrate flux without one: choose a " +
                       "filter for this channel in Loom's dialog." );
   if ( !P.executeOn( view ) )
      throw new Error( "SPFC failed on " + view.id );
};

Steps.spccRGB = function( view, instrume, filters )
{
   Util.reportStage( "colour calibration (SPCC) \u2192 " + view.id );
   Util.log( "spcc", view.id );
   var P = new SpectrophotometricColorCalibration;
   P.applyCalibration = true; // REQUIRED -- without this SPCC only analyzes, does not correct
   P.narrowbandMode = false;  // runs on the broadband-calibrated RGB composite
   P.neutralizeBackground = true;

   // SPCC works on the three-channel composite and needs red/green/blue
   // transmission curves, exactly as SPFC does for the RGB target.
   var names = filters || {};
   var trio = [ [ "R", "red" ], [ "G", "green" ], [ "B", "blue" ] ];
   for ( var t = 0; t < trio.length; ++t )
   {
      var c = Steps.filterCurveByName( names[trio[t][0]] );
      if ( c == null )
         throw new Error( "No filter curve for the " + trio[t][0] + " channel. " +
                          "SPCC cannot colour-calibrate without red, green and " +
                          "blue curves: choose filters in Loom's dialog." );
      P[trio[t][1] + "FilterName"] = c.name;
      P[trio[t][1] + "FilterTrCurve"] = c.data;
   }

   P.generateGraphs = false;
   P.generateStarMaps = false;
   P.generateTextFiles = false;
   var qe2 = Steps.deviceCurveForImage( instrume );
   if ( qe2 != null )
   {
      P.deviceQECurveName = qe2.name;
      P.deviceQECurve = qe2.data;
   }
   if ( !P.executeOn( view ) )
      throw new Error( "SPCC failed on " + view.id );
};

/*
 * Whether a tool is chosen: not empty and not "none". A chosen sharpening
 * tool always runs the aberration correction and takes star reduction and
 * detail levels; one rule for the per-channel runner, the clean SPCC
 * reference, the composite stage and the dialog.
 */
Steps.toolChosen = function( tool )
{
   return !!tool && tool != "none";
};

/* SPCC records the gains it applied; this is the only way to read them. */
Steps.SPCC_WB_PROPERTY = "PCL:SPCC:WhiteBalanceFactors";

Steps.readWhiteBalanceFactors = function( view )
{
   var raw;
   try { raw = view.propertyValue( Steps.SPCC_WB_PROPERTY ); }
   catch ( e ) { return null; }
   if ( raw == null )
      return null;

   // The property comes back as a vector-like; normalise to three numbers.
   var f = [];
   for ( var i = 0; i < 3; ++i )
   {
      var v = parseFloat( raw[i] );
      if ( !isFinite( v ) || v <= 0 )
         return null;
      f.push( v );
   }
   return f;
};

/*
 * Applies a previously measured SPCC white balance to a composite, in place
 * of running SPCC on it.
 *
 * WHY THIS EXISTS -- measured, not assumed:
 *
 * A per-channel aberration correction changes each channel's stellar profile
 * by a DIFFERENT amount (measured on real masters: R tightened 9.7%, G
 * 34.6%), and it manufactures saturated cores where there were none (0 px
 * >= 0.999 before, 6718/9540/7626 after in R/G/B). SPCC derives its white
 * balance from exactly that stellar photometry, so on corrected channels it
 * returns nonsense -- here [0.478863, 1, 0.985317], i.e. red halved, which
 * turned the whole composite green. Raising SPCC's own saturation rejection
 * only shifts it (red 0.479 -> 0.520 -> 0.575 at thresholds 0.75/0.50/0.25);
 * the corruption reaches unsaturated stars too, so no threshold fixes it.
 *
 * The factors are therefore measured on a reference composite built from the
 * SAME channels BEFORE any correction, and applied here.
 *
 * Two parts, matching what SPCC itself does:
 *   1. the per-channel gains, and
 *   2. background neutralisation -- without it the gains alone would leave
 *      the backgrounds unequal (red at half level) and simply move the cast
 *      into the background instead of the stars.
 */
Steps.applyWhiteBalance = function( view, factors )
{
   if ( factors == null || factors.length != 3 )
      throw new Error( "applyWhiteBalance: need three factors for " + view.id );

   Util.log( "whitebalance", view.id + ": gains R/G/B = " +
                             factors[0].toFixed( 6 ) + " / " +
                             factors[1].toFixed( 6 ) + " / " +
                             factors[2].toFixed( 6 ) );

   var pm = new PixelMath;
   pm.useSingleExpression = false;
   pm.expression  = "$T*" + format( "%.10f", factors[0] );
   pm.expression1 = "$T*" + format( "%.10f", factors[1] );
   pm.expression2 = "$T*" + format( "%.10f", factors[2] );
   pm.createNewImage = false;
   pm.rescale = false;
   pm.truncate = false;
   pm.use64BitWorkingImage = true;
   if ( !pm.executeOn( view ) )
      throw new Error( "applyWhiteBalance: gain stage failed on " + view.id );

   // Background neutralisation: bring all three channel backgrounds to a
   // common level, which is what SPCC's neutralizeBackground achieves. The
   // median is the background here -- MGC and GraXpert have already flattened
   // it, so a single offset per channel is the whole correction.
   var img = view.image;
   var rect = new Rect( 0, 0, img.width, img.height );
   var med = [];
   for ( var c = 0; c < 3; ++c )
      med.push( img.median( rect, c, c ) );
   var target = ( med[0] + med[1] + med[2] ) / 3;

   Util.log( "whitebalance", view.id + ": backgrounds " +
                             med[0].toExponential( 4 ) + " / " +
                             med[1].toExponential( 4 ) + " / " +
                             med[2].toExponential( 4 ) +
                             " -> " + target.toExponential( 4 ) );

   var pm2 = new PixelMath;
   pm2.useSingleExpression = false;
   pm2.expression  = "$T+" + format( "%.12f", target - med[0] );
   pm2.expression1 = "$T+" + format( "%.12f", target - med[1] );
   pm2.expression2 = "$T+" + format( "%.12f", target - med[2] );
   pm2.createNewImage = false;
   pm2.rescale = false;
   pm2.truncate = false;
   pm2.use64BitWorkingImage = true;
   if ( !pm2.executeOn( view ) )
      throw new Error( "applyWhiteBalance: background stage failed on " + view.id );
};

/*
 * MGC gradient correction. Runs with useMARSDatabase = true; if no MARS
 * database is registered, MGC itself raises the error -- the process that
 * owns the setting reports it, rather than a preflight guess. Formerly took
 * MARS database files located and validated by Pipeline's preflight --
 * this wrapper does not go looking for them itself and does not fall
 * back to useMARSDatabase = false if the list is empty, because that
 * would silently produce a different, non-MARS-referenced correction
 * while looking like it succeeded. An empty/missing list is a caller
 * bug (preflight should have failed loudly first) and throws here too.
 */
Steps.mgc = function( view, marsDir )
{
   Util.reportStage( "gradient correction (MGC) \u2192 " + view.id );
   Util.log( "mgc", view.id );


   var cfg = Steps.configuredMGC( marsDir );
   if ( cfg == null || !cfg.marsDatabaseFiles || cfg.marsDatabaseFiles.length == 0 )
      throw new Error(
         "MGC has no MARS database selected. Loom uses the MARS folder set " +
         "in its own dialog when one is given, then looks for a saved " +
         "MultiscaleGradientCorrection process icon, then for the databases " +
         "configured in the MGC interface and persisted in PixInsight's own " +
         "settings (" + Steps.CORE_SETTINGS_DIR + "). None yielded a " +
         "database that exists on disk. Set the MARS database folder in the " +
         "Loom dialog, or -- if MARS was configured very recently and the " +
         "setting is not on disk yet -- drag an MGC process icon to the " +
         "workspace, which works immediately." );

   var P = new MultiscaleGradientCorrection;
   P.useMARSDatabase = true;
   P.marsDatabaseFiles = cfg.marsDatabaseFiles;
   Util.log( "mgc", "MARS databases from icon '" + cfg.iconName + "' (" +
                    cfg.marsDatabaseFiles.length + " entr" +
                    ( cfg.marsDatabaseFiles.length == 1 ? "y" : "ies" ) + ")" );
   P.grayMARSFilter = "L";
   P.redMARSFilter = "R";
   P.greenMARSFilter = "G";
   P.blueMARSFilter = "B";
   P.referenceImageId = ""; // empty: use the target view itself
   P.enforceFieldLimits = true;
   if ( !P.executeOn( view ) )
      throw new Error( "MGC failed on " + view.id );
};

Steps.graxpert = function( view, smoothing )
{
   Util.reportStage( "GraXpert background extraction \u2192 " + view.id );
   Util.log( "graxpert", view.id + " smoothing=" + smoothing );
   var P = new GraXpert;
   P.backgroundExtraction = true;  // boolean -- this is what turns extraction ON
   P.correction = "Subtraction";   // string enum: "Subtraction" or "Division" -- NOT what enables extraction
   P.smoothing = smoothing;
   P.createBackground = false;
   P.replaceImage = true;          // REQUIRED -- corrects the working window in place
   P.denoising = false;
   P.deconvolution = false;
   P.disableGPU = false;
   P.showLogs = false;
   if ( !P.executeOn( view ) )
      throw new Error( "GraXpert failed on " + view.id );
};

/*
 * Registers `view` to `referenceView` and RETURNS the registered window.
 *
 * StarAlignment.executeGlobal() does not modify the target: it creates a
 * NEW window (conventionally <id>_registered). Ignoring the return value
 * silently leaves the caller holding the unregistered original -- which
 * is how an RGB composite ended up built from unaligned channels, showing
 * as colour fringing on every star. The new window is located by diffing
 * the open windows, which does not depend on the naming convention.
 */
Steps.register = function( view, referenceView )
{
   Util.reportStage( "registration \u2192 " + view.id );
   Util.log( "register", view.id + " -> " + referenceView.id );

   var before = {};
   var pre = ImageWindow.windows;
   for ( var i = 0; i < pre.length; ++i )
      before[pre[i].mainView.id] = true;

   var P = new StarAlignment;
   P.referenceImage = referenceView.id;
   P.referenceIsFile = false;
   P.targets = [ [ true, false, view.id ] ];
   P.mode = StarAlignment.RegisterMatch;
   /*
    * Full registered frames, explicitly. With any intersection mode
    * StarAlignment trims each target to its own overlap with the
    * reference, so channels come back at different sizes and origins --
    * which defeats cropping them all to one common rectangle later.
    */
   P.intersection = StarAlignment.NoIntersection;
   P.writeKeywords = true;
   P.generateDrizzleData = false;
   if ( !P.executeGlobal() )
      throw new Error( "StarAlignment failed on " + view.id );

   var created = null;
   var post = ImageWindow.windows;
   for ( var j = 0; j < post.length; ++j )
      if ( !before[post[j].mainView.id] )
      {
         created = post[j];
         break;
      }

   if ( created == null )
      throw new Error( "StarAlignment produced no registered window for " + view.id );

   Util.log( "register", view.id + " -> " + created.mainView.id + " (" +
                         created.mainView.image.width + " x " +
                         created.mainView.image.height + ")" );
   return created;
};

Steps.linearFit = function( view, referenceView )
{
   Util.log( "linearfit", view.id + " -> " + referenceView.id );
   var P = new LinearFit;
   P.referenceViewId = referenceView.id;
   P.rejectLow = 0.000000;
   P.rejectHigh = 0.920000;
   if ( !P.executeOn( view ) )
      throw new Error( "LinearFit failed on " + view.id );
};

/*
 * UNVERIFIED PATH -- read before touching.
 *
 * docs/verified-parameters.md specifies executeGlobal() for
 * ChannelCombination: it builds a brand-new image window rather than
 * acting "on" an existing view, so executeOn() is the wrong call (the
 * previous version of this wrapper pre-created a window and used
 * executeOn(), which does not match the documented recipe). This
 * implementation follows executeGlobal() as documented, then identifies
 * the window it created by diffing the set of open window ids before and
 * after the call, and renames it to the requested id.
 *
 * This has deliberately NOT been exercised against a live PixInsight
 * instance: ChannelCombination.executeGlobal() over IPC has reliably
 * wedged the PixInsight automation instance in prior attempts at this
 * task, and is why earlier attempts stalled. Do not probe it either --
 * confirm this path in the GUI on the first real run: that
 * executeGlobal() creates exactly one new RGB window, that the
 * before/after diff below reliably identifies it (rather than, say, some
 * other window opened concurrently), and that its pixel content is the
 * expected R/G/B combination, before trusting this in production.
 */
/*
 * SPCC in narrowband mode, for a palette composite.
 *
 * Broadband SPCC works from filter transmission curves; narrowband mode
 * instead takes each channel's emission-line wavelength and the filter's
 * bandwidth, and derives physically meaningful relative line strengths from
 * the catalogue. That is what makes a palette photometrically defensible
 * rather than a taste judgement.
 *
 * WHY THIS MATTERS HERE -- measured 2026-09-14: LinearFit between narrowband
 * channels is invalid. It assumes two images are the same signal at
 * different scales, which is true of frames through one filter and false of
 * Ha vs SII vs OIII, which are different lines with different morphology.
 * Applied to real data it scaled H down ~8x (H max 0.105 against S's 0.800),
 * leaving Ha with 23 levels of 16-bit range across the nebula -- which is
 * why NarrowbandNormalization posterised, and why the palette went green.
 */
Steps.spccNarrowband = function( view, palette, bandwidthNm )
{
   Util.reportStage( "narrowband SPCC \u2192 " + ( palette || view.id ) );
   var wl = Util.paletteWavelengths( palette );
   if ( wl == null )
      throw new Error( "No emission-line mapping for palette " + palette );

   var bw = parseFloat( bandwidthNm );
   if ( !isFinite( bw ) || bw <= 0 )
      bw = 3.0;   // SPCC's own default

   Util.log( "spcc", palette + " narrowband: R=" + wl[0].channel + " " + wl[0].nm +
                     "nm, G=" + wl[1].channel + " " + wl[1].nm +
                     "nm, B=" + wl[2].channel + " " + wl[2].nm +
                     "nm, bandwidth " + bw + "nm" );

   var P = new SpectrophotometricColorCalibration;
   P.applyCalibration = true;
   P.narrowbandMode = true;
   P.neutralizeBackground = true;

   P.redFilterWavelength   = wl[0].nm;   P.redFilterBandwidth   = bw;
   P.greenFilterWavelength = wl[1].nm;   P.greenFilterBandwidth = bw;
   P.blueFilterWavelength  = wl[2].nm;   P.blueFilterBandwidth  = bw;

   P.generateGraphs = false;
   P.generateStarMaps = false;
   P.generateTextFiles = false;

   if ( !P.executeOn( view ) )
      throw new Error( "SPCC (narrowband) failed on " + view.id );

   var f = Steps.readWhiteBalanceFactors( view );
   if ( f != null )
      Util.log( "spcc", palette + " factors: " + f[0].toFixed( 6 ) + " / " +
                        f[1].toFixed( 6 ) + " / " + f[2].toFixed( 6 ) );
};

/*
 * Aligns channel BACKGROUNDS without touching their scale.
 *
 * This is what linear-fitting narrowband channels was meant to achieve and
 * does not: an offset brings the sky floors together while leaving the
 * relative line strengths -- the actual astrophysics -- intact.
 */
Steps.matchBackgroundOffset = function( views, refView )
{
   var target = Steps.medianOfCentre( refView, 0.6 );
   for ( var i = 0; i < views.length; ++i )
   {
      if ( views[i].id == refView.id )
         continue;
      var here = Steps.medianOfCentre( views[i], 0.6 );
      var delta = target - here;
      Util.log( "background", views[i].id + ": offset " + delta.toExponential( 4 ) +
                              " to match " + refView.id );
      var pm = new PixelMath;
      pm.useSingleExpression = true;
      pm.expression = "$T+" + format( "%.12f", delta );
      pm.createNewImage = false;
      pm.rescale = false;
      pm.truncate = false;
      pm.use64BitWorkingImage = true;
      if ( !pm.executeOn( views[i] ) )
         throw new Error( "background offset failed on " + views[i].id );
   }
};

/* ---------------------------------------------------------------------------
 * Noise reduction, applied to finished composites only.
 *
 * Runs LAST, after SPCC, and never on the per-channel masters. That
 * placement is deliberate: SyQon Prism denoises in a temporarily STRETCHED
 * domain exactly as Parallax does (useMTF true, mtfTarget 0.15,
 * createPIStretchedTempWindow / reversePIStretchOnWindow in SyQon_Prism.js),
 * and a stretched-domain edit reversed through a convex inverse does not
 * preserve linear flux. Yesterday that is precisely what corrupted the
 * stellar photometry SPCC reads and turned an RGB green. Denoising after
 * calibration is safe because nothing downstream measures stars.
 * ------------------------------------------------------------------------- */

Steps.NR_TOOL_NXT   = "NoiseXTerminator";
Steps.NR_TOOL_PRISM = "SyQon Prism";
Steps.NR_TOOL_MLDENOISE = "MLDenoise";
// SyQon Studio's Prism (StepsSyqon.js). The _OLD name is Essential's from before
// Prism 2.0 was offered beside it; Steps.migrateConfig maps it.
Steps.NR_TOOL_STUDIO     = "SyQon Studio Prism Essential";
Steps.NR_TOOL_STUDIO2    = "SyQon Studio Prism 2.0";
Steps.NR_TOOL_STUDIO_OLD = "SyQon Studio Prism";

/*
 * WHERE a denoiser runs is the tool's property, not the user's choice.
 *
 * NoiseXTerminator and MLDenoise want LINEAR data. RC Astro's guidance for
 * NXT is to apply it after colour calibration and after deconvolution but
 * before the stretch, so the noise is reduced before the stretch amplifies
 * it; NXT tolerates stretched data because it internally stretches and
 * reverses, but tolerating is not the recommendation. MLDenoise's authors
 * say the same more firmly: "color calibrated linear deep sky images".
 *
 * SyQon Prism is the opposite: it is a post-stretch tool, which is why
 * Steps.prismMtfTarget exists at all -- that target is only meaningful on
 * data that has been stretched.
 *
 * SyQon Studio's Prism is a linear tool again: Studio publishes Prism
 * Essential's input contract as "Linear RGB or mono", so it runs in the
 * linear slot beside NXT, not where standalone Prism ran.
 *
 * Prism 2.0 is the one tool that runs in BOTH places, because its models
 * have different contracts (syqon.eu/develop): Advanced is "Linear RGB or
 * mono" like Essential, so it runs in the linear slot, while Ultra and Max
 * take "Linear or non-linear" and run after the stretch, on the noise the
 * stretch has made visible. Its linear pass is why this answers true for
 * it; Steps.NOISE_LEVELS.studio2 says which level runs what, and
 * Pipeline.linearDenoiseParams / stretchedDenoiseParams put each pass in
 * its slot.
 *
 * So the dropdown offers a tool and Loom puts it where it belongs. Order is
 * the part that is easy to get wrong, and it is not configurable here.
 */
Steps.denoiseIsLinear = function( tool )
{
   var t = Steps.noiseTool( tool );
   return t != null && t.linear;
};

Steps.prismConfigPath = function()
{
   return File.systemTempDirectory + "/SyQonPrismCLI/syqon_prism_config.csv";
};

/* Prism stores its CLI path in a one-line CSV, same idea as Parallax. */
Steps.prismExecutable = function()
{
   return Steps.findExecutable( "prism_cli", Steps.prismConfigPath() );
};

/*
 * MLDenoise ships WITHOUT a model, and a fresh instance starts with an
 * empty modelPath. Executing it then fails outright:
 *
 *    No model path specified. Please select a neural network model file.
 *
 * -- observed on 1.9.5 build 1702, which is why "the module is
 * registered" is not the same question as "the tool can run". The model
 * has to be found here; trusting the process default gets a run most of
 * the way through and then throws.
 *
 * Searched in the install's library/ (where PixInsight keeps the other
 * downloaded models) and in the user's own PixInsight folder, which is
 * where a model dropped in by hand tends to land.
 */
Steps.mlDenoiseModelDirs = function()
{
   return [ Steps.PI_BASE_DIR + "/library",
            File.homeDirectory + "/PixInsight/library",
            File.homeDirectory + "/PixInsight/models" ];
};

/*
 * A model is a .xmlm container, NOT a bare .onnx. PixInsight's Machine
 * Learning Model Format wraps several networks and their weights in one
 * file -- MLDenoise_v41.xmlm carries mono.onnx, rgb.onnx and a 595 MB
 * weights.bin, and declares <Process>MLDenoise</Process> in its header.
 *
 * The name test matters because library/ also holds BlurXTerminator and
 * StarXTerminator models; handing MLDenoise one of those would be a
 * confident wrong answer rather than a missing one.
 */
Steps.isMLDenoiseModelName = function( name )
{
   return /\.xmlm$/i.test( name ) && /denoise/i.test( name );
};

Steps.mlDenoiseModelPath = function()
{
   var dirs = Steps.mlDenoiseModelDirs();
   for ( var d = 0; d < dirs.length; ++d )
   {
      var entries = Util.directoryEntries( dirs[d] );
      for ( var i = 0; i < entries.length; ++i )
         if ( Steps.isMLDenoiseModelName( entries[i] ) )
            return dirs[d] + "/" + entries[i];
   }
   return null;
};

/* Which noise reduction tools this installation can actually run. */
Steps.availableNoiseTools = function()
{
   var found = {};
   /*
    * Both halves are required. The module without a model is a tool that
    * offers itself in the dialog and then fails mid-run, which is the one
    * outcome worth spending a directory scan to avoid.
    */
   try
   {
      found.mldenoise = Steps.moduleAvailable( "MLDenoise" ) &&
                        Steps.mlDenoiseModelPath() != null;
   }
   catch ( eM ) {}
   try { found.nxt = Steps.moduleAvailable( "NoiseXTerminator" ); }
   catch ( e ) {}
   try
   {
      found.prism = File.exists( Steps.PI_SRC_SCRIPTS_DIR + "/SyQon_Prism.js" ) &&
                    Steps.prismExecutable() != null;
   }
   catch ( eP ) {}
   found.studio = Steps.studioAvailable();
   found.prism2Unavailable = Steps.studioPrism2Unavailable();
   return Steps.noiseToolsFrom( found );
};

/*
 * The dropdown's entries from what was found, in a fixed order. Pure, so
 * the selftest can drive it. SyQon Studio's Prism is offered BESIDE
 * standalone Prism, not in its place: they are different models, and
 * Prism 2.0 is paid, so a user with Studio may still want the Prism they
 * own.
 *
 * Studio's Prism is ONE entry: Prism 2.0, or Essential in its place once
 * a preflight has found 2.0 refused by the account (found.prism2Unavailable,
 * Steps.studioPrism2Unavailable). The maintainer's rule: nobody wants the
 * lesser model offered beside the better one.
 */
Steps.noiseToolsFrom = function( found )
{
   var out = [];
   if ( found.mldenoise ) out.push( Steps.NR_TOOL_MLDENOISE );
   if ( found.nxt )       out.push( Steps.NR_TOOL_NXT );
   if ( found.prism )     out.push( Steps.NR_TOOL_PRISM );
   if ( found.studio )
      out.push( found.prism2Unavailable ? Steps.NR_TOOL_STUDIO : Steps.NR_TOOL_STUDIO2 );
   return out;
};

/*
 * Semantic levels, as elsewhere in Loom: a number like 0.72 is not something
 * a person -- or a language model -- can state as intent.
 *
 * NXT defaults are denoise 0.90 / detail 0.15; Prism's is strength 0.85.
 * "Medium" sits at each tool's own default, with Low backing off and High
 * pushing past it, so Medium means "what the tool author considered normal"
 * rather than a number invented here.
 */
Steps.NOISE_LEVELS = {
   // MEDIUM IS EACH TOOL'S OWN DEFAULT, read from the tool, not chosen here:
   // NoiseXTerminator denoise 0.90 / detail 0.15, SyQon Prism strength 0.85.
   // Low backs off from it, High pushes past it. "Medium" therefore means
   // "what the tool's author considered normal", which is what you get by
   // running the tool by hand and touching nothing.
   nxt:   { low:    { denoise: 0.70, detail: 0.25 },
            medium: { denoise: 0.90, detail: 0.15 },   // NXT default
            high:   { denoise: 0.95, detail: 0.10 } },
   prism: { low: 0.50, medium: 0.85, high: 0.95 },     // 0.85 = Prism default
   /*
    * MLDenoise's own default is amount 0.90, so that is Medium, exactly as
    * for the other two.
    *
    * Low and High are bracket values rather than measured optima, and the
    * measurement is the reason. On a 1600x1600 crop of a 180 s Ha master
    * (background sigma 2.368e-5), the noise MLDenoise removes is exactly
    * proportional to `amount`:
    *
    *    amount   0.30   0.50   0.60   0.75   0.90   1.00
    *    kept     .922   .881   .864   .844   .830   .824
    *    removed  3.57   5.95   7.14   8.92  10.7   11.9   (x1e-6)
    *
    * -- removed/amount is 1.19e-5 at every step. `amount` is a linear
    * blend between the original and the fully denoised result, not a
    * strength knob with a knee, so there is no measured optimum to find;
    * there is only how much of the denoised image you want. Low at 0.60
    * keeps 86% of the noise, High at 1.00 keeps 82%.
    *
    * The mask stays OFF, also measured. It does what it claims -- it holds
    * the denoiser off bright structure -- but on LINEAR data, which is the
    * only place Loom runs MLDenoise, there is almost nothing bright for it
    * to hold off: across five tiles spanning the frame's brightness range
    * it changed removal by -0.0%, -0.1%, -0.1%, -0.7% and -2.6%, darkest
    * to brightest. A 2.6% effect on the brightest tile is not worth
    * departing from the tool's own default for.
    */
   mldenoise: { low: 0.60, medium: 0.90, high: 1.00 }, // 0.90 = MLDenoise default
   /*
    * SyQon Studio's Prism takes `application`, which SyQon_Studio.js
    * describes as "blend the inferred result with the original input" and
    * defaults to 1.00. Same rule: Medium is that default.
    *
    * High cannot push past it. A blend of the denoised image with the
    * original tops out at all-denoised, so High is 1.00 as well -- the
    * honest ceiling rather than an invented number above it. Low is the
    * 0.60 MLDenoise uses, because `application` is the same kind of knob
    * as MLDenoise's `amount`: a linear blend, measured there to remove
    * noise exactly in proportion.
    */
   studio: { low: 0.60, medium: 1.00, high: 1.00 },    // 1.00 = Studio default
   /*
    * SyQon Studio's Prism 2.0, the paid Deep Prism models, run as a
    * TWO-PASS denoiser. The ladder is the maintainer's (2026-09-26, which
    * replaced one model per level), every model at Studio's own 1.00 blend:
    *
    *    Low     Advanced on the linear plate
    *    Medium  Advanced on the linear plate, then Ultra after the stretch
    *    High    Advanced on the linear plate, then Max after the stretch
    *
    * The split follows SyQon's published input contract (syqon.eu/develop):
    * Essential and Advanced take "Linear RGB or mono" only, Ultra and Max
    * "Linear or non-linear". Advanced therefore runs where NXT does, on
    * linear data, where it removes noise without taking the faint signal
    * the stretch is about to lift; Ultra or Max then clean what the stretch
    * has made visible. Advanced is the same pass at every level, so its
    * result is cached once and a change of level re-runs only the second
    * pass (Pipeline.linearDenoiseParams keys each slot on its own model).
    *
    * Measured with syqon-cli on a synthetic LINEAR frame (384x384 RGB, sky
    * sigma 1.73e-3), sky noise kept after one model alone, against
    * Essential:
    *
    *    Essential 1.00  0.77      Ultra 1.00  0.20
    *    Advanced 1.00   0.39      Max 1.00    0.04
    *
    * Max leaves 4% of the synthetic noise when run on linear data, which on
    * real data is where faint signal starts to go with it -- the reason
    * the strong models now run after the stretch, on top of Advanced,
    * instead of in its place.
    *
    * Each pass is { model, application }; a level without a pass after the
    * stretch has `stretched: null`. Every model here is a Prism 2.0 model
    * (Steps.studioIsPrism2), and preflight checks all three on every
    * Prism 2.0 run, whatever the level (Steps.studioModelsFor), so the
    * entry is offered only to an account licensed for all of them.
    */
   studio2: { low:    { linear:    { model: "prism-advanced", application: 1.00 },
                        stretched: null },
              medium: { linear:    { model: "prism-advanced", application: 1.00 },
                        stretched: { model: "prism-ultra",    application: 1.00 } },
              high:   { linear:    { model: "prism-advanced", application: 1.00 },
                        stretched: { model: "prism-max",      application: 1.00 } } }
};

/*
 * `pass` matters only to a tool that runs in both slots (Prism 2.0):
 * "linear" or "stretched" runs that slot's pass alone; "all", or no pass,
 * runs the whole level on what `alreadyStretched` says the data is. Other
 * tools run in one slot and ignore it.
 */
Steps.denoise = function( view, tool, level, label, alreadyStretched, pass )
{
   if ( !Steps.toolChosen( tool ) || !level || level == "none" )
      return;

   Util.operation( "noise reduction", tool, level, label || view.id );

   var t = Steps.noiseTool( tool );
   if ( t == null )
      throw new Error( "Unknown noise reduction tool: " + tool );
   t.run( view, level, alreadyStretched, pass );
};

// A tool's setting for `level`, refusing a level the tool has no entry for.
Steps.noiseLevelSetting = function( levels, level )
{
   var setting = levels[level];
   if ( setting == null )
      throw new Error( "Unknown noise reduction level: " + level );
   return setting;
};

Steps.denoiseNXT = function( view, level )
{
   var lv = Steps.noiseLevelSetting( Steps.NOISE_LEVELS.nxt, level );
   Util.log( "denoise", view.id + ": NoiseXTerminator " + level +
                        " (denoise " + lv.denoise + ", detail " + lv.detail + ")" );
   var P = new NoiseXTerminator;
   P.denoise = lv.denoise;
   P.denoise_color = lv.denoise;
   P.detail = lv.detail;
   if ( !P.executeOn( view ) )
      throw new Error( "NoiseXTerminator failed on " + view.id );
};

Steps.denoiseMLDenoise = function( view, level )
{
   var amount = Steps.noiseLevelSetting( Steps.NOISE_LEVELS.mldenoise, level );
   Util.log( "denoise", view.id + ": MLDenoise " + level +
                        " (amount " + amount + ")" );
   var model = Steps.mlDenoiseModelPath();
   if ( model == null )
      throw new Error( "MLDenoise has no model: put an ONNX denoise " +
                       "model in " + Steps.mlDenoiseModelDirs()[0] );
   var M = new MLDenoise;
   M.amount = amount;
   M.modelPath = model;
   /*
    * Left at its default: the mask (parameters `mask`, `maskClipLow`,
    * `maskBackground`, `maskSmoothness`) is off until it is measured.
    * NOT `linearMask` -- no such parameter exists, and PJSR accepts an
    * assignment to a name a process does not have without complaining,
    * so setting it would read as "the mask changes nothing".
    */
   if ( !M.executeOn( view ) )
      throw new Error( "MLDenoise failed on " + view.id );
};

Steps.denoisePrism = function( view, level, alreadyStretched )
{
   var strength = Steps.noiseLevelSetting( Steps.NOISE_LEVELS.prism, level );
   Steps.prismExecuteStage( view, strength, alreadyStretched );
};

Steps.denoiseStudio = function( view, level, alreadyStretched )
{
   var application = Steps.noiseLevelSetting( Steps.NOISE_LEVELS.studio, level );
   Util.log( "denoise", view.id + ": SyQon Studio Prism " + level +
                        " (application " + application.toFixed( 2 ) + ")" );
   /*
    * The domain is declared, not auto-detected: the pipeline puts this
    * tool in the linear slot, and the temporary file carries no metadata
    * for the CLI to read. An already-stretched call is declared as such,
    * and Prism Essential then refuses it (exit 2) rather than guessing.
    */
   Steps.studioRun( view, "noise reduction",
                    { model: Steps.STUDIO_MODEL_DENOISE,
                      domain: alreadyStretched ? "nonlinear" : "linear",
                      application: application } );
};

Steps.denoiseStudio2 = function( view, level, alreadyStretched, pass )
{
   var step = Steps.noiseLevelSetting( Steps.NOISE_LEVELS.studio2, level );
   var name = view.id + ": SyQon Studio Prism 2.0 " + level;
   function run( p, domain )
   {
      Util.log( "denoise", name + " (" + Steps.studioModelLabel( p.model ) + ", " + domain +
                           ", application " + p.application.toFixed( 2 ) + ")" );
      Steps.studioRun( view, "noise reduction",
                       { model: p.model, domain: domain, application: p.application } );
   }

   // Advanced: linear input only, by SyQon's contract
   if ( pass != "stretched" )
   {
      if ( !alreadyStretched )
         run( step.linear, "linear" );
      else
         Util.warn( "denoise", name + ": " + Steps.studioModelLabel( step.linear.model ) +
                    " takes linear data only and this image is already stretched, so it " +
                    "is skipped" + ( step.stretched
                    ? "; only " + Steps.studioModelLabel( step.stretched.model ) + " runs"
                    : "; this level has no pass after the stretch, so nothing runs" ) );
   }

   // Ultra or Max: after the stretch, on the stretched plate
   if ( pass != "linear" && step.stretched )
   {
      if ( alreadyStretched )
         run( step.stretched, "nonlinear" );
      else
         Util.log( "denoise", name + ": the post-stretch pass (" +
                   Steps.studioModelLabel( step.stretched.model ) +
                   ") is skipped because there is no stretch" );
   }
};

/*
 * One entry per noise reduction tool: whether it runs on LINEAR data
 * (Steps.denoiseIsLinear says why), its runner, and the ladder whose value
 * its cache key carries (Steps.noiseAmountFor), mapped by `amount` where
 * the key wants something other than the level's own value.
 */
Steps.NOISE_TOOLS = {};
Steps.NOISE_TOOLS[Steps.NR_TOOL_NXT] = { linear: true, run: Steps.denoiseNXT,
   levels: Steps.NOISE_LEVELS.nxt,
   // an array, not the object: paramsString serialises it verbatim, and
   // the order here is fixed by this line rather than by key insertion
   amount: function( lv ) { return [ lv.denoise, lv.detail ]; } };
Steps.NOISE_TOOLS[Steps.NR_TOOL_MLDENOISE] = { linear: true, run: Steps.denoiseMLDenoise,
   levels: Steps.NOISE_LEVELS.mldenoise };
Steps.NOISE_TOOLS[Steps.NR_TOOL_PRISM] = { linear: false, run: Steps.denoisePrism,
   levels: Steps.NOISE_LEVELS.prism };
Steps.NOISE_TOOLS[Steps.NR_TOOL_STUDIO] = { linear: true, run: Steps.denoiseStudio,
   levels: Steps.NOISE_LEVELS.studio };
Steps.NOISE_TOOLS[Steps.NR_TOOL_STUDIO2] = { linear: true, run: Steps.denoiseStudio2,
   levels: Steps.NOISE_LEVELS.studio2,
   /*
    * Per pass, the model as well as the blend: every pass runs at 1.00,
    * so the model is what tells them apart. Built here so the key's
    * field order is fixed. Without a pass, both of the level's passes.
    */
   amount: function( m, pass )
   {
      function one( p ) { return ( p == null ) ? null : { model: p.model, application: p.application }; }
      return ( pass == null ) ? { linear: one( m.linear ), stretched: one( m.stretched ) } : one( m[pass] );
   } };

// A tool's entry, or null for one Loom does not know: own properties only, so "toString" is unknown.
Steps.noiseTool = function( tool )
{
   return Object.prototype.hasOwnProperty.call( Steps.NOISE_TOOLS, tool ) ? Steps.NOISE_TOOLS[tool] : null;
};

/*
 * NarrowbandNormalization on a palette composite, NEUTRAL.
 *
 * The module carries two kinds of parameter and this uses only the first:
 *
 *   structural -- which palette the channels represent, so it knows what it
 *                 is looking at. Set from Loom's own palette.
 *   aesthetic  -- o3Boost, s2Boost, scnr, shadowpoint, highlightReduction,
 *                 brightness. ALL pinned to unity/zero here.
 *
 * So it performs the normalisation and nothing else: no line boosting, no
 * green suppression, no stretching, no brightness change. Those are
 * decisions for the user at the eyepiece, not for a batch pipeline, and
 * baking them in would make the output impossible to reason about.
 *
 * haBlend stays at the module's own 0.600 default: it is part of how the
 * palette is rendered rather than an adjustment knob, and there is no
 * documented neutral value for it. Parameters verified against a live
 * instance 2026-09-14.
 */
Steps.NBN_PALETTES = { HOO: 0, SHO: 1, HSO: 2, HOS: 3 };

Steps.narrowbandNormalize = function( view, palette, label )
{
   var p = Steps.NBN_PALETTES[palette];
   if ( p == null )
   {
      Util.warn( "nbnorm", "no NarrowbandNormalization palette for " + palette +
                           "; skipping" );
      return false;
   }

   Util.operation( "nb normalization", "NarrowbandNormalization", palette,
                   label || view.id );

   var P = new NarrowbandNormalization;
   P.palette = p;
   P.lightness = NarrowbandNormalization.Lightness_Off;
   P.blendMode = NarrowbandNormalization.Blend_Mode1;

   // neutral: every aesthetic adjustment at identity
   P.o3Boost = 1.000;
   P.s2Boost = 1.000;
   P.scnr = 0.000;
   P.shadowpoint = 1.000;
   P.highlightReduction = 1.000;
   P.brightness = 1.000;

   if ( !P.executeOn( view ) )
      throw new Error( "NarrowbandNormalization failed on " + view.id );
   return true;
};

/*
 * Names of the keywords the channels disagree on (a different value, or
 * present in some and not others), sorted. `lists` holds each channel's
 * keywords, as {name, value} records.
 */
Steps.inconsistentKeywords = function( lists )
{
   var values = {};
   lists.forEach( function( list, i )
   {
      list.forEach( function( k )
      {
         var name = String( k.name ).trim();
         ( values[name] = values[name] || [] )[i] = String( k.value ).trim();
      } );
   } );
   return Object.keys( values ).filter( function( name )
   {
      var v = values[name];
      for ( var i = 0; i < lists.length; ++i )
         if ( v[i] === undefined || v[i] !== v[0] )
            return true;
      return false;
   } ).sort();
};

/*
 * ChannelCombination warns "Inconsistent Instrument:Filter:Name (FILTER
 * keyword) value(s) - metadata not generated" for every keyword the
 * channels disagree on -- FILTER always does. Holding those keywords back
 * for the call silences it and loses nothing: the composite never receives
 * that metadata either way (probed 2026-09-23). They are restored after,
 * whatever happens.
 */
Steps.withoutInconsistentKeywords = function( views, fn )
{
   var windows = views.map( function( v ) { return v.window; } );
   var saved = windows.map( function( w ) { return w.keywords; } );
   var drop = Steps.inconsistentKeywords( saved );
   try
   {
      if ( drop.length > 0 )
         windows.forEach( function( w, i )
         {
            w.keywords = saved[i].filter( function( k ) { return drop.indexOf( String( k.name ).trim() ) < 0; } );
         } );
      return fn();
   }
   finally
   {
      windows.forEach( function( w, i ) { try { w.keywords = saved[i]; } catch ( e ) {} } );
   }
};

// The ids of every open window, as a set.
Steps.openWindowIds = function()
{
   var ids = {};
   var all = ImageWindow.windows;
   for ( var i = 0; i < all.length; ++i )
      ids[ all[i].mainView.id ] = true;
   return ids;
};

// Every open window whose id is not in `before`, a set from openWindowIds.
Steps.windowsOpenedSince = function( before )
{
   var all = ImageWindow.windows;
   var fresh = [];
   for ( var j = 0; j < all.length; ++j )
      if ( !before[ all[j].mainView.id ] )
         fresh.push( all[j] );
   return fresh;
};

/*
 * The first three-channel window of `windows`, or null; any further one
 * is warned about, since only one was expected while building `id`.
 */
Steps.firstColourWindow = function( windows, id )
{
   var w = null;
   for ( var k = 0; k < windows.length; ++k )
   {
      if ( !Steps.isColourWindow( windows[k] ) )
         continue;
      if ( w != null )
         Util.warn( "combine", "more than one new colour window appeared while " +
                               "building " + id + "; using " + w.mainView.id );
      else
         w = windows[k];
   }
   return w;
};

// A window whose image can be read and has three channels.
Steps.isColourWindow = function( window )
{
   var img = null;
   try { img = window.mainView.image; } catch ( e ) { img = null; }
   return img != null && img.numberOfChannels == 3;
};

// " (new windows: a, b)", or that there were none, for an error message.
Steps.newWindowsNote = function( windows )
{
   var names = [];
   for ( var n = 0; n < windows.length; ++n )
      try { names.push( windows[n].mainView.id ); } catch ( e ) {}
   return names.length ? " (new windows: " + names.join( ", " ) + ")"
                       : " (no new windows at all)";
};

Steps.combineRGB = function( rView, gView, bView, id )
{
   Util.reportStage( "channel combination \u2192 " + id );
   Util.log( "combine", "-> " + id );

   var before = Steps.openWindowIds();

   var P = new ChannelCombination;
   P.colorSpace = ChannelCombination.RGB;
   P.channels = [ [ true, rView.id ],
                  [ true, gView.id ],
                  [ true, bView.id ] ];
   P.inheritAstrometricSolution = true;
   Steps.withoutInconsistentKeywords( [ rView, gView, bView ], function() { return P.executeGlobal(); } );

   /*
    * Identify the window ChannelCombination created by diffing the open set.
    *
    * Taking the FIRST new window is not safe: any other window that appears
    * during the call -- a transient, a model, anything -- would be picked
    * instead, and the real composite would be left unowned while a window
    * that something else later closes is returned. That produces a null
    * mainView much later, at rename time, long after the cause.
    *
    * So: collect EVERY new window, and require a three-channel colour image,
    * which is the only thing ChannelCombination produces here.
    */
   var fresh = Steps.windowsOpenedSince( before );
   var w = Steps.firstColourWindow( fresh, id );
   if ( w == null )
      throw new Error( "ChannelCombination.executeGlobal() produced no colour window for " +
                       id + Steps.newWindowsNote( fresh ) );

   w.mainView.id = Util.freeWindowId( id );
   return w;
};

/* ---------------------------------------------------------------------------
 * Sharpening: aberration correction, star reduction, detail.
 *
 * Three SEPARATE operations with different risk profiles. Aberration
 * correction always runs and is the safe one: it fixes star shape without
 * inventing detail. Star reduction is aesthetic. Sharpening is the risky one.
 *
 * BXT parameter names are snake_case and were read from a live
 * toSource() dump on 2026-09-12 -- NOT from the module's binary strings,
 * which carry the C++ member names (correctOnly, sharpenStars) and do not
 * match the PJSR properties.
 * ------------------------------------------------------------------------ */

Steps.SHARPEN_TOOL_BXT = "BlurXTerminator";
Steps.SHARPEN_TOOL_SYQON = "SyQon Parallax";

/* Which sharpening tools are usable right now. */
Steps.availableSharpenTools = function()
{
   return Steps.sharpenToolsFrom( {
      bxt:      Steps.moduleAvailable( "BlurXTerminator" ),
      parallax: File.exists( Steps.PI_SRC_SCRIPTS_DIR + "/SyQon_Parallax.js" ) &&
                Steps.syqonExecutable() != null,
      studio:   Steps.studioAvailable() } );
};

/*
 * Pure, like Steps.noiseToolsFrom. Studio Parallax sits beside standalone
 * Parallax. Studio's correction alone is not an entry: with Studio found
 * it replaces BlurXTerminator's correct-only pass by itself -- see
 * Steps.aberrationCorrector.
 */
Steps.sharpenToolsFrom = function( found )
{
   var tools = [];
   if ( found.bxt )      tools.push( Steps.SHARPEN_TOOL_BXT );
   if ( found.parallax ) tools.push( Steps.SHARPEN_TOOL_SYQON );
   if ( found.studio )   tools.push( Steps.SHARPEN_TOOL_STUDIO );
   return tools;
};

/*
 * Level maps. Kept in one place so they can be reviewed and tuned without
 * hunting through the call sites. Phase 1 is deterministic: these fixed
 * values ARE the policy. Phase 2 replaces this table with an agent.
 */
Steps.SHARPEN_LEVELS = {
   // Same rule: MEDIUM is the tool's own default, read from the tool.
   // BlurXTerminator sharpen_stars 0.5 and sharpen_nonstellar 0.5;
   // SyQon Parallax starReduction 3 (of 1..6) and sharpen 0.8.
   bxt: {
      /*
       * HIGH stars is 0.70, not 0.75, because BlurXTerminator REFUSES
       * anything above 0.70 on sharpen_stars -- "Valid range is from 0 to
       * 0.7". The ladder had 0.75, so selecting high star reduction threw,
       * the throw was caught as a tolerated sharpening failure, and the
       * plate came out untouched. A whole run on 2026-09-17 reduced no
       * stars at all and said so only in a warning.
       *
       * sharpen_nonstellar has no such limit -- it takes up to 1.00, probed
       * against the process itself -- so detail high stays at 0.75 and the
       * two ladders are deliberately not symmetric.
       */
      stars:  { none: 0.00, low: 0.25, medium: 0.50, high: 0.70 },  // 0.50 default
      detail: { none: 0.00, low: 0.25, medium: 0.50, high: 0.75 }   // 0.50 default
   },
   syqon: {
      // starReduction is 0 = off, 1..6 discrete levels; sharpen is a 0..1 alpha
      stars:  { none: 0,   low: 2,   medium: 3,   high: 5 },        // 3 default
      detail: { none: 0.0, low: 0.4, medium: 0.8, high: 1.0 }       // 0.8 default
   },
   /*
    * SyQon Studio's Parallax: reduction level 0-10 with default 5, deblur
    * strength 0-1 with default 0.5 (SyQon_Studio.js P.pxReductionLevel,
    * P.pxDeblurStrength). Medium is each default. Low and High are
    * BlurXTerminator's ratios to its own 0.50 default -- half, and 1.4x for
    * stars (BXT's 0.70 ceiling) or 1.5x for detail -- carried onto Studio's
    * scales, so a level means the same step away from the author's normal
    * whichever tool is chosen. The reduction level is an integer; 2.5
    * rounds to 3.
    */
   studio: {
      stars:  { none: 0,   low: 3,    medium: 5,    high: 7 },      // 5 default
      detail: { none: 0.0, low: 0.25, medium: 0.50, high: 0.75 }    // 0.5 default
   }
};

/*
 * What a level LABEL actually resolves to for a given tool.
 *
 * These exist for the cache key, and the distinction matters. A key built
 * from the label alone stays byte-identical when a ladder is remapped --
 * "medium" is still "medium" whether it means 0.8 or 0.4 -- so every
 * cached result would keep passing as current while the pixels it claims
 * to represent had changed. Keying on the number that actually reaches the
 * tool makes a remap invalidate exactly the stages it alters, and nothing
 * else.
 *
 * Both return null for an unknown tool or level rather than throwing: they
 * feed a key, and a stage that cannot resolve its own amount is one the
 * caller has already decided not to run.
 */
Steps.sharpenAmountFor = function( tool, kind, level )
{
   if ( level == null || level == "none" )
      return null;
   var ladder = null;
   if ( tool == Steps.SHARPEN_TOOL_BXT )
      ladder = Steps.SHARPEN_LEVELS.bxt;
   else if ( tool == Steps.SHARPEN_TOOL_SYQON )
      ladder = Steps.SHARPEN_LEVELS.syqon;
   else if ( tool == Steps.SHARPEN_TOOL_STUDIO )
      ladder = Steps.SHARPEN_LEVELS.studio;
   if ( ladder == null || ladder[kind] == null )
      return null;
   var v = ladder[kind][level];
   return ( v == null ) ? null : v;
};

Steps.noiseAmountFor = function( tool, level, pass )
{
   var t = Steps.noiseTool( tool );
   var v = ( t == null || !t.levels || level == null || level == "none" ) ? null : t.levels[level];
   if ( v == null )
      return null;
   return t.amount ? t.amount( v, pass ) : v;
};

/*
 * Aberration correction. Always runs when a tool is selected. correct_only
 * fixes star shape and optical aberration WITHOUT adding detail, so it is
 * the operation with no aesthetic downside.
 */
/*
 * The tool that really runs the aberration pass for a chosen tool.
 *
 * With BlurXTerminator chosen and SyQon Studio found, Studio Parallax's
 * correction runs in place of BXT's correct_only -- automatically, the
 * maintainer's rule: nothing to choose in the dialog. BXT still does star
 * reduction and detail on the composite. Without Studio, BXT as before;
 * every other tool corrects with itself (standalone Parallax keeps its
 * own correction, Studio Parallax already uses Studio's).
 */
Steps.aberrationCorrector = function( tool, studioFound )
{
   return ( tool == Steps.SHARPEN_TOOL_BXT && studioFound ) ? Steps.SHARPEN_TOOL_STUDIO : tool;
};

/* Says which tool corrected, for the operation log line. */
Steps.aberrationLogText = function( tool, studioFound )
{
   var corrector = Steps.aberrationCorrector( tool, studioFound );
   return ( corrector == tool ) ? tool
          : corrector + " (correction only, in place of " + tool + ")";
};

Steps.aberration = function( view, tool, linked, label )
{
   var studioFound = ( tool == Steps.SHARPEN_TOOL_BXT ) && Steps.studioAvailable();
   Util.operation( "aberration", Steps.aberrationLogText( tool, studioFound ), null,
                   label || view.id );
   var corrector = Steps.aberrationCorrector( tool, studioFound );
   if ( corrector == Steps.SHARPEN_TOOL_BXT )
   {
      var P = new BlurXTerminator;
      P.correct_only = true;
      P.sharpen_stars = 0.00;
      P.sharpen_nonstellar = 0.00;
      P.auto_nonstellar_psf = true;
      if ( !P.executeOn( view ) )
         throw new Error( "BlurXTerminator (aberration) failed on " + view.id );
      return;
   }
   if ( corrector == Steps.SHARPEN_TOOL_SYQON )
   {
      Steps.syqonExecuteStage( view, "aberration correction",
         { correctAberration: true, starReduction: 0, sharpen: 0.0 }, linked );
      return;
   }
   if ( corrector == Steps.SHARPEN_TOOL_STUDIO )
   {
      /*
       * Studio's "stellar correction / defect repair" stage alone -- for
       * Studio Parallax, and in BXT's place when Studio is found. Linear,
       * declared: aberration runs on the calibrated per-channel masters,
       * before registration. `linked` is Loom's temporary-stretch choice
       * and Studio has no temporary stretch, so it does not apply.
       */
      Steps.studioRun( view, "aberration correction",
                       { model: Steps.STUDIO_MODEL_PARALLAX, domain: "linear",
                         parallax: { correction: true } } );
      return;
   }
   throw new Error( "Aberration correction is not implemented for " + tool );
};

/* Star reduction. Aesthetic; skipped entirely at level "none". */
Steps.starReduction = function( view, tool, level, linked, label )
{
   if ( level == null || level == "none" )
      return;
   Util.operation( "star reduction", tool, level, label || view.id );
   var run = Steps.sharpenRunner( tool, Steps.STAR_REDUCTION_RUNNERS );
   if ( run == null )
      throw new Error( "Star reduction is not implemented for " + tool );
   run( view, level, linked );
};

/*
 * The runner a sharpening tool uses from `runners` -- one of the two tables
 * below, keyed by tool name when first asked, because the Studio tool is
 * defined further down this file -- or null for a tool with none.
 */
Steps.sharpenRunner = function( tool, runners )
{
   var byTool = {};
   byTool[Steps.SHARPEN_TOOL_BXT]    = runners.bxt;
   byTool[Steps.SHARPEN_TOOL_SYQON]  = runners.syqon;
   byTool[Steps.SHARPEN_TOOL_STUDIO] = runners.studio;
   return byTool.hasOwnProperty( tool ) ? byTool[tool] : null;
};

// A tool's setting for `level`, refusing a level the tool has no entry for.
Steps.sharpenLevelSetting = function( levels, level, what )
{
   var setting = levels[level];
   if ( setting == null )
      throw new Error( "Unknown " + what + " level: " + level );
   return setting;
};

Steps.STAR_REDUCTION_RUNNERS = {
   bxt: function( view, level )
   {
      var amount = Steps.sharpenLevelSetting( Steps.SHARPEN_LEVELS.bxt.stars, level, "star reduction" );
      Util.log( "starreduction", view.id + " (BXT sharpen_stars=" + amount + ")" );
      var P = new BlurXTerminator;
      P.correct_only = false;
      P.sharpen_stars = amount;
      P.sharpen_nonstellar = 0.00;   // separate operation; do not sharpen detail here
      P.auto_nonstellar_psf = true;
      if ( !P.executeOn( view ) )
         throw new Error( "BlurXTerminator (star reduction) failed on " + view.id );
   },
   syqon: function( view, level, linked )
   {
      var syqonAmount = Steps.sharpenLevelSetting( Steps.SHARPEN_LEVELS.syqon.stars, level, "star reduction" );
      if ( syqonAmount <= 0 )
         return;
      Steps.syqonExecuteStage( view, "star reduction",
         { correctAberration: false, starReduction: syqonAmount, sharpen: 0.0 }, linked );
   },
   studio: function( view, level )
   {
      var studioLevel = Steps.sharpenLevelSetting( Steps.SHARPEN_LEVELS.studio.stars, level, "star reduction" );
      if ( studioLevel <= 0 )
         return;
      // the finished composite is still linear here: extraction and the
      // stretch come after (Pipeline.STAGE_ORDER)
      Steps.studioRun( view, "star reduction",
                       { model: Steps.STUDIO_MODEL_PARALLAX, domain: "linear",
                         parallax: { reduction: studioLevel } } );
   }
};

/* Detail sharpening. The risky one; skipped entirely at level "none". */
Steps.sharpenDetail = function( view, tool, level, linked, label )
{
   if ( level == null || level == "none" )
      return;
   Util.operation( "detail sharpening", tool, level, label || view.id );
   var run = Steps.sharpenRunner( tool, Steps.DETAIL_RUNNERS );
   if ( run == null )
      throw new Error( "Detail sharpening is not implemented for " + tool );
   run( view, level, linked );
};

Steps.DETAIL_RUNNERS = {
   bxt: function( view, level )
   {
      var amount = Steps.sharpenLevelSetting( Steps.SHARPEN_LEVELS.bxt.detail, level, "detail" );
      Util.log( "sharpen", view.id + " (BXT sharpen_nonstellar=" + amount + ")" );
      var P = new BlurXTerminator;
      P.correct_only = false;
      P.sharpen_stars = 0.00;        // separate operation; do not touch stars here
      P.sharpen_nonstellar = amount;
      P.auto_nonstellar_psf = true;
      if ( !P.executeOn( view ) )
         throw new Error( "BlurXTerminator (detail) failed on " + view.id );
   },
   syqon: function( view, level, linked )
   {
      var syqonDetail = Steps.sharpenLevelSetting( Steps.SHARPEN_LEVELS.syqon.detail, level, "detail" );
      if ( syqonDetail <= 0.0 )
         return;
      Steps.syqonExecuteStage( view, "detail sharpening",
         { correctAberration: false, starReduction: 0, sharpen: syqonDetail }, linked );
   },
   studio: function( view, level )
   {
      var studioDeblur = Steps.sharpenLevelSetting( Steps.SHARPEN_LEVELS.studio.detail, level, "detail" );
      if ( studioDeblur <= 0.0 )
         return;
      // Studio calls its deconvolution "deblur"
      Steps.studioRun( view, "detail sharpening",
                       { model: Steps.STUDIO_MODEL_PARALLAX, domain: "linear",
                         parallax: { deblur: studioDeblur } } );
   }
};

/*
 * Star reduction and detail sharpening on a FINISHED, CALIBRATED composite.
 *
 * NOT aberration: that stays per channel, before registration, because it
 * fixes star shape on native uninterpolated pixels and resampling would
 * spread whatever aberration is present. These two are aesthetic and have
 * no such requirement, so they belong here.
 *
 * `linked = true` is the point of running them here. On a colour image the
 * temporary stretch these tools require must use ONE blackpoint and ONE
 * midtone across all three channels; an unlinked stretch applies a different
 * curve per channel and destroys the white balance SPCC just established.
 *
 * Returns true if anything actually ran, so callers can log honestly.
 */
/*
 * Star reduction and detail in ONE BlurXTerminator run.
 *
 * sharpen_stars and sharpen_nonstellar are independent parameters of the
 * same process, so two executions were never required -- and they cost
 * more than time. With correct_only false BXT corrects the PSF as well as
 * sharpening, so running it twice deconvolved the composite twice and
 * corrected the same stars twice, on top of the per-channel correction
 * done before registration.
 *
 * One pass, ~27 s saved per composite, and no deconvolution of a
 * deconvolution.
 *
 * It falls back to two passes when the two settings are not both BXT,
 * which is the only reason they were ever separate calls: the dialog lets
 * star reduction and detail choose different tools.
 */
Steps.correctComposite = function( view, tool, starLevel, detailLevel, label )
{
   if ( !Steps.toolChosen( tool ) )
      return false;
   var wantStars  = ( starLevel  && starLevel  != "none" );
   var wantDetail = ( detailLevel && detailLevel != "none" );
   if ( !wantStars && !wantDetail )
      return false;

   if ( tool == Steps.SHARPEN_TOOL_BXT && wantStars && wantDetail )
   {
      Steps.bxtStarsAndDetail( view, starLevel, detailLevel, label || view.id );
      return true;
   }

   Steps.starReduction( view, tool, starLevel, true, label || view.id );
   Steps.sharpenDetail( view, tool, detailLevel, true, label || view.id );
   return true;
};

/*
 * The merged run. Both levels resolve through the same tables the separate
 * calls use, so the values cannot drift apart from them.
 */
Steps.bxtStarsAndDetail = function( view, starLevel, detailLevel, label )
{
   var stars  = Steps.SHARPEN_LEVELS.bxt.stars[starLevel];
   var detail = Steps.SHARPEN_LEVELS.bxt.detail[detailLevel];
   if ( stars == null )
      throw new Error( "Unknown star reduction level: " + starLevel );
   if ( detail == null )
      throw new Error( "Unknown detail level: " + detailLevel );

   Util.operation( "sharpening", Steps.SHARPEN_TOOL_BXT,
                   starLevel + "/" + detailLevel, label );
   Util.log( "sharpen", view.id + " (BXT sharpen_stars=" + stars +
                        ", sharpen_nonstellar=" + detail + ")" );

   var P = new BlurXTerminator;
   P.correct_only = false;
   P.sharpen_stars = stars;
   P.sharpen_nonstellar = detail;
   P.auto_nonstellar_psf = true;
   if ( !P.executeOn( view ) )
      throw new Error( "BlurXTerminator failed on " + view.id );
};

/* ---------------------------------------------------------------------------
 * Export.
 *
 * Writes a result as a 16-bit TIFF. The plates live in the workspace as
 * 32-bit float and STAY that way: the conversion happens on a throwaway
 * clone, so exporting never degrades what is on screen.
 *
 * 16-bit is the right depth here only because export happens AFTER the
 * stretch -- a linear 32-bit plate quantised to 16 bits would posterise,
 * a stretched one does not. Loom refuses to export linear data for that
 * reason rather than producing a quietly ruined file.
 *
 * ImageWindow.setSampleFormat( 16, false ) is PixInsight's own idiom for this
 * (FFTRegistration.js:750), not an invented call.
 */
Steps.exportTiff16 = function( window, dir, name )
{
   var path = dir + "/" + Steps.syqonSanitizeFileName( name ) + ".tif";
   /*
    * Overwrite, always. saveAs is asked not to verify, but PixInsight can
    * still be configured to guard overwrites, and a run that silently kept a
    * stale TIFF from an earlier attempt is worse than one that fails loudly.
    * Removing the file first makes the outcome independent of that setting.
    */
   try { if ( File.exists( path ) ) File.remove( path ); }
   catch ( e0 )
   {
      throw new Error( "cannot overwrite " + path + ": " + e0 );
   }
   var clone = Steps.syqonCloneWindowForProcessing(
                  window, Util.freeWindowId( Steps.syqonSanitizeFileName( name ) + "_export" ) );
   try
   {
      clone.setSampleFormat( 16, false );
      clone.saveAs( path, false/*queryOptions*/, false/*allowMessages*/,
                    false/*strict*/, false/*verifyOverwrite*/ );
   }
   finally
   {
      try { clone.forceClose(); } catch ( e ) {}
   }
   return path;
};

/* ---------------------------------------------------------------------------
 * Deterministic stretch.
 *
 * See docs/superpowers/specs/2026-09-15-stretch-design.md. One
 * HistogramTransformation per plate, two numbers, no iteration and nothing
 * for the user to set.
 *
 * BLACK POINT. The level where the light curve begins -- the image's own
 * minimum. Nothing is clipped and no range is wasted, by construction.
 *
 * This was ORIGINALLY a noise model: c0 = median - z(N)*MADN with
 * z(N) the normal quantile at 1-1/N, the level below which fewer than one pixel
 * is expected if the sky is Gaussian. Verified against the L master on
 * 2026-09-15 and the assumption is false: the darkest pixel sits only 3.93
 * MADN below the median on the full frame (3.24 on a centre crop), where the
 * Gaussian model demanded 5.60. A stacked, drizzled master has a much shorter
 * lower tail than a normal distribution. The consequence was not clipping but
 * waste -- c0 fell below every real pixel, the darkest mapped to 0.0902, and
 * ~10% of the output range held nothing at all.
 *
 * The empirical minimum needs no distributional assumption and cannot be
 * wrong about its own data. Its one exposure is a single cold pixel or a
 * residual zero border dragging c0 down; Loom crops to the common imaged area
 * before this runs, which removes the border case.
 *
 * MIDTONE. The sky median is sent to 0.25, PixInsight's own autostretch
 * target.
 *
 * The source method instead puts the midtone at the light curve's upper edge.
 * That edge was measured and it is not a well-defined quantity: sweeping the
 * vertical-zoom threshold the method depends on, the upper edge of the L
 * master drifted monotonically 30x across five decades of zoom and never
 * converged, so "zoom until it stops moving" does not terminate. The sky
 * median is robust and defined on every frame. The two agree in practice --
 * at the zoom range the source states, the sky lands at 0.27/0.23/0.18,
 * bracketing 0.25.
 */
Steps.STRETCH_SKY_TARGET = 0.25;

/*
 * The stars master is stretched to a DIFFERENT target, and the value is
 * derived rather than chosen.
 *
 * The stars plate is unscreen( stretched master, its starless ), so the sky is
 * subtracted out of it either way and the sky's own level is irrelevant to how
 * the plate looks. What decides whether a faint star survives is the SLOPE of
 * the transform at the sky level -- how hard it separates a just-above-sky
 * star from the sky itself.
 *
 * For a fixed input level, dMTF/dx at that level is maximised over m exactly
 * when the level maps to 0.5 (argmax verified numerically for x0 from 1e-6 to
 * 0.3). So 0.5 is the maximum-separation point for the faint stars, not a
 * second taste constant.
 *
 * At 0.25 the first real run produced a sparse plate: a handful of bright
 * stars on black, with the faint population lost.
 */
Steps.STRETCH_STARS_TARGET = 0.5;

/*
 * The two numbers for one plate, from statistics the caller has measured.
 * Pure, so the whole rule is unit-tested without a running PixInsight.
 *
 * `median` and `madn` are in the image's own [0,1] scale. Returns
 * { c0, m, skyOut } with skyOut the level the sky actually lands on --
 * 0.25 by construction, returned so callers can assert rather than trust.
 */
Steps.stretchParametersFrom = function( median, minimum, target )
{
   var t = ( target == null ) ? Steps.STRETCH_SKY_TARGET : target;
   var c0 = Math.max( 0, minimum );
   /*
    * A flat frame -- every pixel at the same level -- puts c0 on the median
    * and normalises it to zero, which no midtone can lift. Nothing to
    * stretch, so say so rather than return an infinity.
    */
   var x = ( median - c0 ) / ( 1 - c0 );
   if ( !( x > 0 ) )
      throw new Error( "This image has no signal above its darkest pixel; " +
                       "there is nothing to stretch." );
   var m = Steps.mtfMidtoneFor( x, t );
   return { c0: c0, m: m, skyOut: Steps.mtfApply( m, x ) };
};

/*
 * Measures a view and returns its stretch parameters.
 *
 * `linked` forces ONE black point and ONE midtone across all channels: RGB is
 * photometrically calibrated by SPCC and a palette has been through
 * NarrowbandNormalization, so an independent per-channel stretch would undo
 * both. The black point takes the MINIMUM across channels, so the shared
 * value cannot clip whichever channel sits lowest.
 */
/*
 * The darkest level that is not an isolated defect.
 *
 * The plain minimum is the right black point in principle -- it clips nothing
 * and wastes nothing -- but it is a single-pixel statistic, so one cold pixel
 * or a dead column drags it below all real data and hands back exactly the
 * wasted range that the Gaussian rule was replaced for.
 *
 * So the minimum is taken over a 3x3 MEDIAN-FILTERED copy. A lone bad pixel
 * cannot survive a 3x3 median, so it is rejected by construction, while real
 * structure -- which by definition covers more than one pixel -- is not. This
 * introduces no threshold to tune: radius 1 is the smallest neighbourhood
 * that exists, not a chosen value, and it is the standard meaning of
 * "isolated single pixel".
 *
 * Returns a per-channel array; callers take the minimum across channels for a
 * linked stretch so the shared black point cannot clip any channel.
 *
 * MorphologicalTransformation.Median is 4, read from the module rather than
 * assumed. structureSize defaults to 3 and the default structure is already a
 * full 3x3 of ones, so neither is set here.
 */
Steps.robustMinimumPerChannel = function( window )
{
   var probe = Steps.syqonCloneWindowForProcessing(
                  window, Util.freeWindowId(
                     Steps.syqonSanitizeFileName( window.mainView.id ) + "_minprobe" ) );
   try
   {
      var P = new MorphologicalTransformation;
      P.operator = MorphologicalTransformation.Median;
      if ( !P.executeOn( probe.mainView ) )
         throw new Error( "median filter failed" );

      var img = probe.mainView.image;
      var rect = new Rect( 0, 0, img.width, img.height );
      var mins = [];
      for ( var c = 0; c < img.numberOfChannels; ++c )
         mins.push( img.minimum( rect, c, c ) );
      return mins;
   }
   catch ( e )
   {
      /*
       * Falling back to the raw minimum keeps the run alive; it is only less
       * robust, never wrong, and the warning says which one was used.
       */
      Util.warn( "stretch", window.mainView.id + ": could not compute a " +
                            "defect-robust minimum (" + e + "); using the raw " +
                            "minimum, which one cold pixel can drag down" );
      var raw = window.mainView.image;
      var rr = new Rect( 0, 0, raw.width, raw.height );
      var out = [];
      for ( var c2 = 0; c2 < raw.numberOfChannels; ++c2 )
         out.push( raw.minimum( rr, c2, c2 ) );
      return out;
   }
   finally
   {
      try { probe.forceClose(); } catch ( e2 ) {}
   }
};

Steps.stretchParametersForView = function( view, linked, target )
{
   var img = view.image;
   var window = view.isMainView ? view.window : view.mainView.window;
   var mins = Steps.robustMinimumPerChannel( window );

   if ( !linked || img.numberOfChannels < 3 )
      return Steps.stretchParametersFrom( img.median(), mins[0], target );

   /*
    * One black point and one midtone for all three channels. The black point
    * is the darkest level in ANY channel, so the shared value cannot clip
    * whichever channel sits lowest; the midtone follows the channel mean, so
    * no channel is privileged.
    */
   var rect = new Rect( 0, 0, img.width, img.height );
   var meds = [];
   for ( var c = 0; c < 3; ++c )
      meds.push( img.median( rect, c, c ) );
   var c0 = Math.max( 0, Math.min( mins[0], Math.min( mins[1], mins[2] ) ) );
   var medMean = ( meds[0] + meds[1] + meds[2] ) / 3;
   var x = ( medMean - c0 ) / ( 1 - c0 );
   if ( !( x > 0 ) )
      throw new Error( "This image has no signal above its darkest pixel; " +
                       "there is nothing to stretch." );
   var m = Steps.mtfMidtoneFor( x, ( target == null ) ? Steps.STRETCH_SKY_TARGET
                                                       : target );
   return { c0: c0, m: m, skyOut: Steps.mtfApply( m, x ) };
};

/*
 * Applies the transform. HistogramTransformation's H matrix is five rows of
 * [ shadows, midtones, highlights, lowRange, highRange ]; rows 0-2 are R, G
 * and B and row 3 is the combined RGB/K channel. A linked stretch writes row
 * 3 only, which is what makes it one transform for all three channels.
 */
Steps.applyStretch = function( view, params, linked, label )
{
   var P = new HistogramTransformation;
   var id = [ 0, 0.5, 1, 0, 1 ];
   var row = [ params.c0, params.m, 1, 0, 1 ];
   /*
    * Row 3 is the combined RGB/K channel, and a GRAYSCALE image is driven by
    * that row -- not by row 0. Writing rows 0-2 for a mono plate leaves row 3
    * at identity and the transform silently does nothing: observed on L,
    * which came back unstretched while the linked RGB and palette plates
    * were correct.
    */
   var combined = linked || view.image.numberOfChannels < 3;
   P.H = combined ? [ id, id, id, row, id ]
                  : [ row, row, row, id, id ];
   if ( !P.executeOn( view ) )
      throw new Error( "Stretch failed on " + ( label || view.id ) );
};

/*
 * Measure, apply, log. Returns the parameters used.
 */
Steps.stretch = function( view, linked, label, target )
{
   var name = label || view.id;
   var p = Steps.stretchParametersForView( view, linked, target );
   Util.operation( "stretch", linked ? "linked" : "unlinked",
                   "sky -> " + p.skyOut.toFixed( 3 ), name );
   Util.log( "stretch", name + ": c0 " + p.c0.toExponential( 4 ) +
                        ", midtone " + p.m.toExponential( 4 ) );
   Steps.applyStretch( view, p, linked, name );
   return p;
};

/* ---------------------------------------------------------------------------
 * MultiscaleAdaptiveStretch -- the second stretch method.
 *
 * Unlike Loom's own MTF stretch, which is a single midtone transfer derived
 * from the image's own statistics, MAS stretches adaptively and puts back
 * large-scale contrast afterwards. It is offered as an alternative rather
 * than a replacement: the MTF stretch is deterministic and reproducible
 * from three measured numbers, which is what the stretch design asked for.
 * ------------------------------------------------------------------------ */

Steps.STRETCH_METHOD_MTF = "mtf";
Steps.STRETCH_METHOD_MAS = "MultiscaleAdaptiveStretch";

/*
 * The values Francesco specified, 2026-09-16.
 *
 * scaleSeparation is DELIBERATELY ABSENT. It is a pcl::MASScaleSeparation
 * enum, not the pixel count the dialog displays -- the combo label "1024"
 * is computed from the enum index at runtime, so writing 1024 here would
 * set a different scale, silently. Loom therefore leaves it at whatever
 * the process itself defaults to, unless a saved process icon supplies a
 * value, and logs the effective number either way.
 */
Steps.MAS_PARAMETERS = {
   targetBackground:          0.15,
   aggressiveness:            0.70,
   dynamicRangeCompression:   0.40,
   contrastRecovery:          true,
   contrastRecoveryIntensity: 1.00,
   previewLargeScale:         false,
   backgroundROIEnabled:      false,
   saturationEnabled:         false
};

/*
 * A saved MultiscaleAdaptiveStretch process icon, if the user made one.
 *
 * Same contract as configuredSPFC/configuredMGC: a setting the user has
 * already expressed in PixInsight's own UI beats one guessed here. It is
 * also the only way to pin scaleSeparation exactly, since its enum values
 * are not discoverable from a script.
 */
Steps.configuredMAS = function()
{
   try
   {
      var icons = ProcessInstance.iconsByProcessId( "MultiscaleAdaptiveStretch" );
      if ( !icons || icons.length == 0 )
         return null;
      var P = ProcessInstance.fromIcon( icons[0] );
      return ( P == null ) ? null : { iconName: icons[0], instance: P };
   }
   catch ( e ) { return null; }
};

Steps.multiscaleStretch = function( view, label )
{
   var name = label || view.id;
   var icon = Steps.configuredMAS();
   var P;
   if ( icon != null )
   {
      // the user's own settings, verbatim -- including scaleSeparation
      P = icon.instance;
      Util.log( "stretch", name + ": MultiscaleAdaptiveStretch from process icon '" +
                           icon.iconName + "'" );
   }
   else
   {
      P = new MultiscaleAdaptiveStretch;
      for ( var k in Steps.MAS_PARAMETERS )
         P[k] = Steps.MAS_PARAMETERS[k];
   }

   Util.operation( "stretch", "MultiscaleAdaptiveStretch",
                   "background -> " + Number( P.targetBackground ).toFixed( 3 ), name );
   /*
    * Logged, not assumed: scaleSeparation is the one parameter Loom does
    * not set, so the run has to say what it actually used.
    */
   Util.log( "stretch", name + ": target " + P.targetBackground +
                        ", aggressiveness " + P.aggressiveness +
                        ", DRC " + P.dynamicRangeCompression +
                        ", contrast recovery " + ( P.contrastRecovery ? "on" : "off" ) +
                        " (scale separation " + P.scaleSeparation +
                        ", intensity " + P.contrastRecoveryIntensity + ")" +
                        ", saturation " + ( P.saturationEnabled ? "on" : "off" ) );

   if ( !P.executeOn( view ) )
      throw new Error( "MultiscaleAdaptiveStretch failed on " + name );
};

/* Whichever stretch the run asked for. MAS carries its own target background. */
Steps.stretchBy = function( method, view, linked, label )
{
   if ( method == Steps.STRETCH_METHOD_MAS )
      return Steps.multiscaleStretch( view, label );
   return Steps.stretch( view, linked, label );
};

/* ---------------------------------------------------------------------------
 * Star extraction.
 *
 * Three tools, one contract: the target window is left holding the STARLESS
 * image and a second window carrying the stars is returned alongside.
 *
 * WHY THE STARS IMAGE IS DERIVED RATHER THAN TAKEN FROM THE TOOL. Each tool
 * offers its own star output and they are not the same thing: StarNet2's
 * `mask` is a star MASK, StarXTerminator's `stars` is a star IMAGE, and the
 * SyQon Starless CLI emits no star output at all -- SyQon_Starless.js builds
 * one itself in createStarsOnlyImage() (~line 300) from the original and the
 * starless. Taking each tool's own output would make "stars" mean something
 * different depending on a dropdown.
 *
 * So all three are run for their starless result only, and the stars are
 * derived the one way, by UNSCREEN:
 *
 *    stars = (original - starless) / (1 - starless)
 *
 * which is the exact inverse of the screen blend that recombines them,
 * original = 1 - (1-starless)(1-stars). Screen is how stars superimpose on
 * nebulosity, so this splits the image into two frames that put it back
 * together exactly -- which is also why Loom does not keep the unsplit
 * composite. Division by (1-starless) is guarded: where starless has
 * reached 1 the original is saturated, there is no headroom left, and the
 * stars frame is given 0 rather than an infinity.
 */
Steps.STAR_TOOL_STARNET = "StarNet2";
Steps.STAR_TOOL_SXT     = "StarXTerminator";
Steps.STAR_TOOL_SYQON   = "SyQon Starless";

/* Which star extraction tools this installation can actually run. */
Steps.availableStarTools = function()
{
   var found = {};
   try { found.starnet = Steps.moduleAvailable( "StarNet2" ); }
   catch ( e ) {}
   try { found.sxt = Steps.moduleAvailable( "StarXTerminator" ); }
   catch ( e2 ) {}
   try { found.starless = Steps.starlessExecutable() != null && Steps.starlessModelPath() != null; }
   catch ( e3 ) {}
   found.studio = Steps.studioAvailable();
   return Steps.starToolsFrom( found );
};

/* Pure, like Steps.noiseToolsFrom: Studio's Axiom beside SyQon Starless. */
Steps.starToolsFrom = function( found )
{
   var out = [];
   if ( found.starnet )  out.push( Steps.STAR_TOOL_STARNET );
   if ( found.sxt )      out.push( Steps.STAR_TOOL_SXT );
   if ( found.starless ) out.push( Steps.STAR_TOOL_SYQON );
   if ( found.studio )   out.push( Steps.STAR_TOOL_STUDIO );
   return out;
};

/*
 * stars = (original - starless) / (1 - starless), the inverse of the screen
 * blend. `original` is consumed: it is the window that held the pre-
 * extraction image and becomes the stars frame in place.
 */
Steps.deriveStarsByUnscreen = function( originalWindow, starlessWindow )
{
   var sl = starlessWindow.mainView.id;
   var pm = new PixelMath;
   pm.expression           = "iif( " + sl + " < 1, max( 0, ($T - " + sl +
                             ") / (1 - " + sl + ") ), 0 )";
   pm.useSingleExpression  = true;
   pm.generateOutput       = true;
   pm.rescale              = false;
   pm.truncate             = true;
   pm.truncateLower        = 0;
   pm.truncateUpper        = 1;
   pm.createNewImage       = false;
   pm.showNewImage         = false;
   pm.newImageColorSpace   = PixelMath.SameAsTarget;
   pm.newImageSampleFormat = PixelMath.SameAsTarget;
   if ( !pm.executeOn( originalWindow.mainView ) )
      throw new Error( "Could not derive the stars image for " +
                       originalWindow.mainView.id );
   return originalWindow;
};

/*
 * Runs the chosen tool for its starless result, in place on `window`.
 * Returns nothing; the caller derives the stars frame.
 */
Steps.removeStars = function( window, tool, label, linear )
{
   var view = window.mainView;

   if ( tool == Steps.STAR_TOOL_STARNET )
   {
      var P = new StarNet2;
      // Loom's composites are LINEAR at this point -- nothing has been
      // stretched -- and StarNet2 has to be told so or it mangles them.
      // Fly-Through's input is stretched and passes false; omitted means
      // linear, so every existing call is unchanged.
      P.linear = ( linear !== false );
      // no star mask: the stars frame is derived by unscreen instead
      P.mask = false;
      if ( !P.executeOn( view ) )
         throw new Error( "StarNet2 failed on " + ( label || view.id ) );
      return;
   }

   if ( tool == Steps.STAR_TOOL_SXT )
   {
      var X = new StarXTerminator;
      X.stars = false;
      X.unscreen = false;
      if ( !X.executeOn( view ) )
         throw new Error( "StarXTerminator failed on " + ( label || view.id ) );
      return;
   }

   if ( tool == Steps.STAR_TOOL_SYQON )
   {
      Steps.syqonStarlessRun( window, label );
      return;
   }

   if ( tool == Steps.STAR_TOOL_STUDIO )
   {
      /*
       * Axiom takes either domain, but must be TOLD which: linear input
       * gets Axiom's own stretch, stretched input none, and applying the
       * stretch to data already stretched is exactly the double stretch
       * the Prism notes above measured. Same convention as StarNet2:
       * omitted means linear, Fly-Through passes false.
       */
      Steps.studioRun( view, "star extraction",
                       { model: Steps.STUDIO_MODEL_STARLESS,
                         domain: ( linear !== false ) ? "linear" : "nonlinear" } );
      return;
   }

   throw new Error( "Unknown star extraction tool: " + tool );
};

/*
 * Splits `window` into starless (in place) and stars (a new window named
 * <label>_stars). Returns { starless, stars }.
 */
Steps.extractStars = function( window, tool, label, stretchStars )
{
   if ( !Steps.toolChosen( tool ) )
      return null;

   var name = label || window.mainView.id;
   var linked = window.mainView.image.numberOfChannels >= 3;
   Util.operation( "star extraction", tool, null, name );

   /*
    * TWO passes, on two different inputs. This is the source workflow's own
    * order and it is why extraction costs two runs of the tool per plate.
    *
    *   stars    <- stretch a clone of the master, THEN remove stars from it;
    *               the difference is the stars, already non-linear.
    *   starless <- remove stars from the LINEAR master; it is stretched
    *               afterwards by its own pipeline stage.
    *
    * The two plates therefore carry independent transforms, which is what
    * "each must look right on its own" requires -- and why screening them
    * back together will not reconstruct the original.
    */
   var stars = Steps.extractStarsPlate( window, tool, name, linked, stretchStars );

   // second pass: the starless, from the untouched linear master
   Steps.removeStars( window, tool, name + " starless" );

   return { starless: window, stars: stars };
};

/*
 * The first pass: the stars plate, from a clone of `window` that is
 * stretched (when asked) and then made starless. Both clones are closed
 * if the pass fails; the stars plate is the caller's once it returns.
 */
Steps.extractStarsPlate = function( window, tool, name, linked, stretchStars )
{
   var stretched = Steps.syqonCloneWindowForProcessing(
                      window, Util.freeWindowId(
                         Steps.syqonSanitizeFileName( name ) + "_starsrc" ) );
   var stars = null;
   try
   {
      Steps.copySolutionQuietly( stretched, window );

      /*
       * Gated on the stretch option, or the run would be inconsistent with
       * itself: starless plates left linear while the stars plate came back
       * non-linear. With the stretch off everything stays linear, and the
       * stars are simply the unscreen difference on linear data.
       */
      if ( stretchStars )
         Steps.stretch( stretched.mainView, linked, name + " stars",
                        Steps.STRETCH_STARS_TARGET );

      // the pre-removal copy is what the stars are differenced against
      stars = Steps.syqonCloneWindowForProcessing(
                 stretched, Util.freeWindowId(
                    Steps.syqonSanitizeFileName( name ) + "_stars" ) );
      Steps.copySolutionQuietly( stars, stretched );

      /*
       * Studio is told this copy's real domain. The other tools keep the
       * call they have always had -- StarNet2 is given `linear` as well,
       * and changing what it is told here would change every cached stars
       * plate it has made without changing its key.
       */
      Steps.removeStars( stretched, tool, name + " stars",
                         ( tool == Steps.STAR_TOOL_STUDIO && stretchStars ) ? false : undefined );
      Steps.deriveStarsByUnscreen( stars, stretched );

   }
   catch ( e3 )
   {
      try { if ( stars ) stars.forceClose(); } catch ( e4 ) {}
      try { stretched.forceClose(); } catch ( e5 ) {}
      throw e3;
   }
   try { stretched.forceClose(); } catch ( e6 ) {}
   return stars;
};

// `to` takes `from`'s plate solution, if it has one; a failure is not fatal.
Steps.copySolutionQuietly = function( to, from )
{
   try
   {
      if ( from.hasAstrometricSolution )
         to.copyAstrometricSolution( from );
   }
   catch ( e ) {}
};

/* ---------------------------------------------------------------------------
 * Halo reduction by PSF matching.
 *
 * Colour halos on stars come from the channels having different star sizes:
 * on the reference data G measured 4.72" against R's 2.95", so a green star
 * is genuinely half again as large as the red one beneath it and spills past
 * the edge of the combined star. Matching the widths removes the halo at its
 * source.
 *
 * This is DESTRUCTIVE: every channel is blurred up to the widest one. That is
 * acceptable in an LRGB workflow, where RGB carries colour and the untouched L
 * carries detail; it would not be for an RGB-only image.
 * ------------------------------------------------------------------------ */

/* ---------------------------------------------------------------------------
 * Master quality, so a fresh stack that is worse than the one it displaced
 * says so before nine minutes of processing rather than afterwards.
 *
 * Loom always uses the newest variant of a channel -- that is what a
 * re-stack is for -- but on 2026-09-18 the newest G was 23% noisier and
 * softer than the G it replaced, and the only symptom was green stars in
 * the finished plate.
 * ------------------------------------------------------------------------- */

/*
 * Identity of a FILE, not of a path: a re-stack writes the same name with
 * a new size and time, and must be measured again rather than answered
 * from the cache.
 */
Steps.MASTER_QUALITY_VERSION = "v3";

Steps.masterQualityKey = function( path )
{
   try
   {
      var fi = new FileInfo( path );
      var t = fi.lastModified;
      /*
       * The version prefix is not decoration. What this measures has
       * changed twice, and a cached number from an older definition
       * compared against a fresh one is exactly the quiet wrongness the
       * column exists to report.
       */
      return Steps.MASTER_QUALITY_VERSION + "|" + path + "|" + fi.size +
             "|" + ( t ? t.getTime() : 0 );
   }
   catch ( e ) { return Steps.MASTER_QUALITY_VERSION + "|" + path + "|0|0"; }
};

Steps.masterQualityCachePath = function()
{
   return Cache.dir() + "/master-quality.json";
};

/*
 * Read once per script run and held in memory: the dialog asks about
 * every master in a folder, and re-reading the file for each would be
 * the slow part of a cheap operation.
 */
Steps.qualityTable = null;

Steps.loadQualityTable = function()
{
   if ( Steps.qualityTable != null )
      return Steps.qualityTable;
   Steps.qualityTable = {};
   try
   {
      var p = Steps.masterQualityCachePath();
      if ( File.exists( p ) )
         Steps.qualityTable = JSON.parse( File.readTextFile( p ) ) || {};
   }
   catch ( e ) { Steps.qualityTable = {}; }
   return Steps.qualityTable;
};

Steps.saveQualityTable = function()
{
   try
   {
      Cache.ensureDir();
      File.writeTextFile( Steps.masterQualityCachePath(),
                          JSON.stringify( Steps.qualityTable || {} ) );
   }
   catch ( e )
   {
      // A cache that cannot be written costs a re-measurement, nothing more.
      Util.warn( "quality", "could not save the measurements: " + e );
   }
};

/*
 * Global FWHM of a master, measured by SubframeSelector.
 *
 * SubframeSelector rather than a hand-rolled fit, because it is the tool
 * whose numbers the owner already knows from WBPP: an FWHM that does not
 * agree with the one in the subframe table is a number nobody can act on.
 * It also measures the whole frame, which a central crop cannot -- two
 * masters whose autocrop trimmed them differently do not share a centre,
 * and star fields are not uniform.
 *
 * FWHM and eccentricity only. An SNR comparison between two stacks was
 * tried at length and abandoned: every form of it needs the two frames on
 * a common flux scale, and two stacks of one channel do not have one --
 * between two S masters the stars moved 30% and the sky 50%, so no single
 * factor describes the pair, and sky-, star- and noise-based definitions
 * each got some channels right and some wrong. FWHM needs no such
 * assumption. It is seeing and optics, in pixels, directly comparable
 * between any two stacks of the same rig.
 *
 * The measurement columns are positional; these indices were read off a
 * live run rather than assumed.
 */
Steps.SFS_FWHM = 5;
Steps.SFS_ECCENTRICITY = 6;
Steps.SFS_NOISE = 12;
Steps.SFS_STARS = 14;

/* SubframeSelector.routine: 0 measures. Verified by execution -- 1 and 2
   are the preview and output routines and refuse with "No measurements
   have been made". */
Steps.SFS_MEASURE = 0;

Steps.measureMasterFWHM = function( path )
{
   var table = Steps.loadQualityTable();
   var key = Steps.masterQualityKey( path );
   if ( table[key] != null )
      return table[key];

   try
   {
      var P = new SubframeSelector;
      /*
       * Four values per row, and the count is checked by the process:
       * enabled, path, local normalization data, drizzle data.
       */
      P.subframes = [ [ true, path, "", "" ] ];
      P.routine = Steps.SFS_MEASURE;
      P.nonInteractive = true;
      P.subframeScale = 1;          // pixels, so the figure is scale-free
      P.scaleUnit = 0;
      if ( !P.executeGlobal() )
         return null;
      if ( P.measurements == null || P.measurements.length == 0 )
         return null;

      var m = P.measurements[0];
      var q = { fwhm: m[Steps.SFS_FWHM],
                eccentricity: m[Steps.SFS_ECCENTRICITY],
                noise: m[Steps.SFS_NOISE],
                stars: m[Steps.SFS_STARS] };
      if ( !( q.fwhm > 0 ) )
         return null;
      table[key] = q;
      Steps.saveQualityTable();
      return q;
   }
   catch ( e )
   {
      Util.warn( "quality", "could not measure " + path + ": " + e );
      return null;
   }
};

/*
 * Median PSF sigma of a view, via StarDetector for positions and DynamicPSF
 * for the fits. Measured on a central region: representative, and far cheaper
 * than fitting every star in a 12006x7834 frame.
 */
Steps.measurePSF = function( view )
{
   Util.reportStage( "measuring PSF \u2192 " + view.id );
   var img = view.image;
   var S = Math.min( 1200, Math.min( img.width, img.height ) );
   var rect = new Rect( (img.width-S) >> 1, (img.height-S) >> 1,
                        ((img.width-S) >> 1) + S, ((img.height-S) >> 1) + S );

   var D = new StarDetector;
   D.structureLayers = 5;
   D.sensitivity = 0.2;
   D.hotPixelFilterRadius = 1;
   var saved = img.selectedRect;
   img.selectedRect = rect;
   var det = D.stars( img );
   img.selectedRect = saved;
   if ( det.length == 0 )
      return null;

   var stars = [];
   for ( var i = 0; i < det.length && i < 400; ++i )
   {
      var x = det[i].pos.x + rect.x0, y = det[i].pos.y + rect.y0, r = 8;
      stars.push( [ 0, 0, DynamicPSF.Star_DetectedOk, x-r, y-r, x+r, y+r, x, y ] );
   }

   var P = new DynamicPSF;
   P.views = [[ view.id ]];
   P.stars = stars;
   P.astrometry = false;
   P.autoAperture = true;
   P.searchRadius = 8;
   P.circularPSF = false;
   P.autoPSF = false;
   P.gaussianPSF = true;
   P.moffatPSF = P.moffat10PSF = P.moffat8PSF = P.moffat6PSF =
      P.moffat4PSF = P.moffat25PSF = P.moffat15PSF = P.lorentzianPSF = false;
   P.variableShapePSF = false;
   if ( !P.executeGlobal() )
      return null;

   var sx = [], sy = [];
   for ( var j = 0; j < P.psf.length; ++j )
   {
      var p = P.psf[j];
      if ( p[3] == DynamicPSF.PSF_FittedOk ) { sx.push( p[8] ); sy.push( p[9] ); }
   }
   if ( sx.length == 0 )
      return null;
   function med( a ) { a.sort( function( u, v ) { return u-v; } ); return a[a.length >> 1]; }
   var mx = med( sx ), my = med( sy );
   return { sx: mx, sy: my, sigma: (mx + my)/2, n: sx.length };
};

/* Widens a view's PSF by `sigma` pixels with a circular parametric Gaussian. */
/* ---------------------------------------------------------------------------
 * Frequency separation, sized from the stars themselves.
 *
 * Splits a plate into a blurred LOW layer and a HIGH layer holding what the
 * blur removed:
 *
 *    low  = gaussian( original, sigma )
 *    high = (original - low)/2 + 0.5
 *
 * This is EXACTLY Photoshop's Apply Image recipe, in both of its forms:
 *
 *    8-bit  : Apply Image, Subtract, Scale 2, Offset 128
 *             -> (target - source)/2 + 128/255
 *    16-bit : Apply Image, Add, Scale 2, Invert
 *             -> (target + (1 - source))/2  ==  (target - source)/2 + 0.5
 *
 * The 0.5 pedestal is there because `high` is a signed difference and no
 * integer file format holds negatives. The DIVISION BY TWO is there because
 * the recombination is LINEAR LIGHT, which is
 *
 *    result = base + 2*blend - 1
 *
 * so that low + 2*((orig-low)/2 + 0.5) - 1 == orig exactly. Without the
 * halving the same blend yields 2*orig - low, which is not the image: the
 * detail is doubled and the low layer subtracted. Halving also costs
 * nothing in headroom -- it is what lets a full-range difference survive
 * [0,1] instead of clipping beyond +/-0.5.
 *
 * The blur radius is not a number anyone has to pick: it comes from the
 * plate's own measured PSF, so it follows the seeing of the night rather
 * than a habit carried over from another image.
 * ------------------------------------------------------------------------ */

// sigma is a half-width; the visible star is about twice it, and that is
// the scale the low layer should absorb.
Steps.FS_SIGMA_FACTOR = 2.0;
Steps.FS_PEDESTAL     = 0.5;
// Photoshop's Apply Image "Scale", and the reason Linear Light inverts the
// split exactly. Not adjustable: it is fixed by the blend mode's algebra.
Steps.FS_SCALE        = 2.0;

Steps.frequencySeparate = function( window, label )
{
   var name = label || window.mainView.id;
   var psf = Steps.measurePSF( window.mainView );
   if ( psf == null || !( psf.sigma > 0 ) )
      throw new Error( "frequency separation: could not measure the stars on " +
                       name + ", so there is no radius to separate at" );

   var sigma = psf.sigma * Steps.FS_SIGMA_FACTOR;
   Util.operation( "freq. separation", "Gaussian",
                   "sigma " + sigma.toFixed( 2 ) + " px", name );
   Util.log( "freqsep", name + ": measured PSF sigma " + psf.sigma.toFixed( 3 ) +
                        " px (FWHM " + Util.sigmaToFWHM( psf.sigma ).toFixed( 2 ) +
                        " px, " + psf.n + " stars) -> blur sigma " +
                        sigma.toFixed( 3 ) + " px" );

   var low = Steps.syqonCloneWindowForProcessing(
                window, Util.freeWindowId( Steps.syqonSanitizeFileName( name ) + "_low" ) );
   var high = null;
   try
   {
      try { if ( window.hasAstrometricSolution ) low.copyAstrometricSolution( window ); }
      catch ( e ) {}
      Steps.convolveBy( low.mainView, sigma );

      high = Steps.syqonCloneWindowForProcessing(
                window, Util.freeWindowId( Steps.syqonSanitizeFileName( name ) + "_high" ) );
      try { if ( window.hasAstrometricSolution ) high.copyAstrometricSolution( window ); }
      catch ( e2 ) {}

      var pm = new PixelMath;
      pm.useSingleExpression = true;
      pm.expression = "($T - " + low.mainView.id + ")/" +
                      format( "%.8f", Steps.FS_SCALE ) + " + " +
                      format( "%.8f", Steps.FS_PEDESTAL );
      pm.createNewImage = false;
      pm.rescale        = false;
      // clipped deliberately: a 16-bit TIFF cannot store what falls outside
      // [0,1] anyway, and silently rescaling would break the recombination
      pm.truncate       = true;
      pm.truncateLower  = 0;
      pm.truncateUpper  = 1;
      pm.use64BitWorkingImage = true;
      if ( !pm.executeOn( high.mainView ) )
         throw new Error( "frequency separation: could not build the high layer for " + name );
   }
   catch ( e3 )
   {
      try { low.forceClose(); } catch ( e4 ) {}
      try { if ( high ) high.forceClose(); } catch ( e5 ) {}
      throw e3;
   }
   return { low: low, high: high, sigma: sigma, psfSigma: psf.sigma, stars: psf.n };
};

/*
 * A curves layer's name, carrying the channel it acts on: "Ha[R]",
 * "OIII[B]", and "OIII[G,B]" where one line feeds two channels.
 *
 * The channel is in the NAME because the panel cannot show it: Photoshop
 * opens the dropdown on RGB whatever the file says, so a stack of curves
 * layers is otherwise three identical-looking entries.
 */
Steps.CURVE_CHANNEL_LETTERS = [ "RGB", "R", "G", "B" ];

Steps.curveLayerName = function( label, channelIds )
{
   var letters = [];
   for ( var i = 0; i < channelIds.length; ++i )
      letters.push( Steps.CURVE_CHANNEL_LETTERS[channelIds[i]] );
   return label + "[" + letters.join( "," ) + "]";
};

/*
 * The layered PSB: Loom's plates as one Photoshop document.
 *
 * Bottom to top, which is the order Psb.write expects:
 *
 *    HSO           group   -> HSO_starless
 *    RGB           group   -> RGB_starless          (layer hidden)
 *    Stars         group, SCREEN
 *                          -> RGB_stars
 *                          -> L stars, LUMINOSITY
 *
 * where the last is either a single L_stars layer, or -- when the L stars
 * plate was frequency-separated -- a group carrying the luminosity blend
 * over its two halves:
 *
 *                          -> L Stars group, LUMINOSITY
 *                               -> L_stars_low
 *                               -> L_stars_high, LINEAR LIGHT
 *
 * L sits ABOVE RGB_stars because luminosity blending applies the top
 * layer's luminance to what is beneath it; below, it would do nothing.
 * Linear Light on the high half is what makes the separation reversible --
 * see Steps.frequencySeparate.
 *
 * Every plate that is missing is simply left out, so a run without a
 * palette or without star extraction still produces a sensible document.
 */
Steps.buildPsbDocument = function( results )
{
   var doc = [];
   var palette = Steps.psbPaletteGroup( results );
   if ( palette != null )
      doc.push( palette );
   var broadband = Steps.psbBroadbandGroup( results );
   if ( broadband != null )
      doc.push( broadband );
   var stars = Steps.psbStarLayers( results );
   if ( stars.length > 0 )
   {
      doc.push( { name: "Stars", blend: Psb.BLEND_SCREEN, group: stars } );
      /*
       * At the very top, OUTSIDE the group: it works on the stars as they
       * come out of the screen blend, which is not the same as curving
       * any one plate inside it.
       */
      doc.push( { name: "Stars Curve", curves: [ 0 ],   // 0 = composite RGB
                  // clipped to the Stars group directly below, so it curves
                  // the stars as they come out of the screen blend and
                  // leaves the palette and broadband beneath them alone
                  clipping: true } );
   }
   return doc;
};

/*
 * The first palette with a plate in `results`, or null. Util.PALETTES'
 * own order decides when there is more than one.
 */
Steps.psbPaletteName = function( results )
{
   for ( var pk in Util.PALETTES )
      if ( results[pk + "_starless"] != null || results[pk] != null )
         return pk;
   return null;
};

/*
 * The 1-based channels (1 = red, 2 = green, 3 = blue) a palette puts an
 * emission line in: more than one under HOO, where OIII fills two.
 */
Steps.paletteChannelsOf = function( map, key )
{
   var chans = [];
   for ( var mi = 0; mi < map.length; ++mi )
      if ( map[mi] == key )
         chans.push( mi + 1 );
   return chans;
};

/*
 * The palette group: the plate, then one Curves layer per emission line,
 * each already pointed at the channel that line occupies.
 *
 * Which channel that is depends on the palette -- Ha is red in HSO and
 * HOO but GREEN in SHO -- so the mapping is read from Util.PALETTES
 * rather than assumed. Under HOO, OIII fills both green and blue and
 * gets a curve covering the pair.
 *
 * Bottom to top: the plate, SII, OIII, Ha. Null without a palette plate.
 */
Steps.psbPaletteGroup = function( results )
{
   var palName = Steps.psbPaletteName( results );
   if ( palName == null )
      return null;

   var plateName = ( results[palName + "_starless"] != null ) ? ( palName + "_starless" )
                                                               : palName;
   var layers = [ { name: plateName, window: results[plateName] } ];
   var map = Util.PALETTES[palName] || [];
   /*
    * BOTTOM to top, so the panel reads Ha, SII, OIII downwards -- the
    * order of the palette's own name, which is how the lines are talked
    * about. The array is reversed from what the panel shows.
    */
   var lines = [ { key: "O", label: "OIII" },
                 { key: "S", label: "SII" },
                 { key: "H", label: "Ha" } ];
   for ( var li = 0; li < lines.length; ++li )
   {
      var chans = Steps.paletteChannelsOf( map, lines[li].key );
      if ( chans.length > 0 )
         layers.push( { name: Steps.curveLayerName( lines[li].label, chans ),
                        curves: chans } );
   }
   return { name: palName, group: layers };
};

/*
 * Both the group and the layer are off: the palette carries the colour,
 * and the broadband starless is here to be switched on when wanted.
 * Null without a broadband plate.
 */
Steps.psbBroadbandGroup = function( results )
{
   var plateName = ( results.RGB_starless != null ) ? "RGB_starless"
                 : ( results.RGB != null )          ? "RGB"
                 :                                     null;
   if ( plateName == null )
      return null;
   return { name: "RGB", visible: false, group: [
               { name: plateName, window: results[plateName], visible: false } ] };
};

/*
 * The layers of the Stars group, bottom to top; empty when there is no
 * RGB star plate and no L star plate.
 */
Steps.psbStarLayers = function( results )
{
   var stars = [];
   if ( results.RGB_stars != null )
   {
      stars.push( { name: "RGB_stars", window: results.RGB_stars } );
      /*
       * Saturation for the stars, sitting directly over RGB_stars and under
       * the L layers so it colours the stars without touching the luminance
       * laid over them. Neutral until it is touched.
       */
      stars.push( { name: "Stars Saturation", hueSaturation: true,
                    // clipped to RGB_stars directly below: it colours the
                    // star plate alone, not everything under it
                    clipping: true } );
   }
   var lStars = Steps.psbLStarsLayer( results );
   if ( lStars != null )
      stars.push( lStars );
   return stars;
};

/*
 * The separated L stars are NOT set up to recombine. Linear Light over
 * the low layer would return the original exactly; this instead hides
 * the low layer and lays the high frequencies over the stars beneath in
 * Soft Light, which is a creative use of the split rather than a
 * reversible one. Francesco's setup, 2026-09-17.
 */
Steps.psbLStarsLayer = function( results )
{
   if ( results.L_stars_low != null && results.L_stars_high != null )
      return { name: "L Stars", blend: Psb.BLEND_PASS_THROUGH, group: [
                  { name: "L_stars_low", window: results.L_stars_low,
                    visible: false },
                  { name: "L_stars_high", window: results.L_stars_high,
                    blend: Psb.BLEND_SOFT_LIGHT } ] };
   if ( results.L_stars != null )
      return { name: "L_stars", window: results.L_stars,
               blend: Psb.BLEND_LUMINOSITY };
   return null;
};

/*
 * Writes the document, converting each plate to 16 bits first.
 *
 * The conversions are clones, so the workspace keeps its 32-bit floats;
 * they are closed in the finally, including on the error paths, or a
 * failed export would leave a dozen full-size windows behind.
 */
Steps.exportPsb = function( results, dir, name )
{
   var doc = Steps.buildPsbDocument( results );
   if ( doc.length == 0 )
      throw new Error( "there is nothing to put in a PSB" );

   var path = dir + "/" + Steps.syqonSanitizeFileName( name ) + ".psb";
   try { if ( File.exists( path ) ) File.remove( path ); }
   catch ( e0 ) { throw new Error( "cannot overwrite " + path + ": " + e0 ); }

   var clones = [];
   function to16( entry )
   {
      if ( entry.group != null )
      {
         for ( var i = 0; i < entry.group.length; ++i )
            to16( entry.group[i] );
         return;
      }
      // adjustment layers carry no pixels, so there is nothing to convert
      if ( entry.window == null )
         return;
      var c = Steps.syqonCloneWindowForProcessing(
                 entry.window, Util.freeWindowId(
                    Steps.syqonSanitizeFileName( entry.name ) + "_psb" ) );
      c.setSampleFormat( 16, false );
      clones.push( c );
      entry.window = c;
   }

   var width = 0, height = 0;
   try
   {
      for ( var d = 0; d < doc.length; ++d )
         to16( doc[d] );
      // geometry comes from the first real plate; Psb.write requires that
      // every layer match it
      var first = clones[0].mainView.image;
      width = first.width; height = first.height;
      for ( var k = 1; k < clones.length; ++k )
      {
         var im = clones[k].mainView.image;
         if ( im.width != width || im.height != height )
            throw new Error( "plates differ in size (" + im.width + "x" + im.height +
                             " against " + width + "x" + height +
                             "); they cannot share one document" );
      }
      /*
       * The document is RGB, so it carries the RGB profile regardless of
       * how many mono plates are in it: inside a PSB every layer lives in
       * the document's space.
       */
      var icc = null;
      try { icc = Steps.psbProfileBytes(); }
      catch ( eI )
      {
         Util.warn( "icc", "the PSB will be untagged (" + eI + ")" );
      }
      Util.operation( "export", "PSB",
                      doc.length + " groups, " + clones.length + " layers", name );
      Psb.write( path, doc, width, height, icc );
   }
   finally
   {
      for ( var f = 0; f < clones.length; ++f )
         try { clones[f].forceClose(); } catch ( e ) {}
   }
   Util.log( "export", "wrote " + path + " (" + width + "x" + height + ", " +
                       clones.length + " layers)" );
   return path;
};

Steps.convolveBy = function( view, sigma )
{
   Util.reportStage( "matching PSF \u2192 " + view.id );
   if ( !( sigma > 0 ) )
      return;
   var P = new Convolution;
   P.mode = Convolution.Parametric;
   P.sigma = sigma;
   P.shape = 2.00;          // 2 = Gaussian
   P.aspectRatio = 1.00;    // circular: matches width, not elongation
   P.rotationAngle = 0.00;
   if ( !P.executeOn( view ) )
      throw new Error( "Convolution failed on " + view.id );
};

/*
 * Returns a copy of `window` in a brand-new window with NO file
 * association, named `id`.
 *
 * A result loaded from the stage cache keeps the cache file as its path,
 * so PixInsight titles it "L_1 | bb9fbae0f6...xisf". PJSR has no setter
 * for a window's file path (the API exposes GetImageWindowFilePath only),
 * so the only way to drop the association is to build a fresh window.
 *
 * Pixels, FITS keywords and the astrometric solution are all carried
 * across -- the solution explicitly, because assigning the image and
 * keywords does NOT bring it.
 */
Steps.detachFromFile = function( window, id )
{
   var src = window.mainView.image;
   var w = new ImageWindow( src.width, src.height, src.numberOfChannels,
                            src.bitsPerSample, src.isReal, src.isColor, id );
   w.mainView.beginProcess( UndoFlag_NoSwapFile );
   w.mainView.image.assign( src );
   w.mainView.endProcess();
   w.keywords = window.keywords;
   try
   {
      if ( window.hasAstrometricSolution )
         w.copyAstrometricSolution( window );
   }
   catch ( e )
   {
      Util.warn( "output", "could not carry the astrometric solution to " + id + ": " + e );
   }

   /*
    * A window built with `new ImageWindow` starts HIDDEN -- unlike one from
    * ImageWindow.open(), which is shown. Without this the detached results
    * exist and are named correctly but never appear on screen.
    */
   w.show();
   return w;
};
