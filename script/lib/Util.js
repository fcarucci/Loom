/*
 * Util.checkCoreVersion shows its refusal in a message box, whichever
 * entry point included this file.
 */
#include <pjsr/StdButton.jsh>
#include <pjsr/StdIcon.jsh>

var Util = {};

/*
 * Loom's release number, and the single source of truth for it.
 *
 * NOT called Util.VERSION, and this is not a style choice. PJSR's
 * preprocessor is a C-style macro processor, and Steps.js must
 * `#define VERSION "6.4.2"` for PixInsight's bundled ImageSolver engine
 * to construct. Every later occurrence of the bare token VERSION is then
 * substituted, so `Util.VERSION` reached the parser as `Util."6.4.2"` --
 * a syntax error, on which PixInsight discards the entire script with no
 * message, no console output and exit status 0.
 *
 * Two or three numeric components; Update.compareVersions treats a missing
 * patch as zero, so "0.1" and "0.1.0" are the same version.
 *
 * Bumped to the NEXT version as soon as one is tagged, so the tip is
 * never mistaken for the release behind it. A build from here reports
 * the version on the way, not the one behind it, which is the honest
 * answer for a tree that is neither.
 *
 * Releasing is: set this to the version being released, commit, tag
 * `v<version>` and push the tag. CI builds the archive and publishes it
 * as a release asset -- the only way a release is ever made, so one
 * cannot ship a zip built by hand from a tree that was not the tagged
 * one. Then bump this to the next patch.
 *
 * This alone does NOT identify the running code: the updater installs
 * whatever is on the branch, and many commits share one version. What is
 * shown to the user is this plus the short commit read from .git -- see
 * Update.describeVersion.
 */
/*
 * The banner, in the slant style PixInsight's own startup uses.
 *
 * Plain: the console strips most markup and colour is available only
 * through the semantic channels (noteln green, warningln magenta), which
 * would mean a three-tone banner rather than a gradient. Not worth the
 * noise, so this stays white.
 *
 * Printed with the version line under it, which is the cheapest place to
 * see which build is running -- the title bar carries it too, but a run
 * log does not.
 */
Util.BANNER = [
   "    __                       ",
   "   / /   ____  ____  ____ ___",
   "  / /   / __ \\/ __ \\/ __ `__ \\",
   " / /___/ /_/ / /_/ / / / / / /",
   "/_____/\\____/\\____/_/ /_/ /_/ "
];

Util.LOOM_VERSION = "0.3.0";

/* ------------------------------------------------------------------ */
/* The oldest PixInsight core Loom will run on                         */
/* ------------------------------------------------------------------ */

/*
 * PixInsight 1.9.4.
 *
 * Loom was written against 1.9.5 "Lockhart" build 1702 and still prefers
 * it. What 1.9.4 costs, and why each is survivable:
 *
 *   - <pjsr/astrometry/AstrometricResiduals.js> is new in 1.9.5, and an
 *     unresolvable #include is not an error in PJSR: the core discards
 *     the WHOLE script with no message and exit status 0, so the script
 *     simply does nothing. It is therefore included conditionally, on
 *     __PI_RELEASE__, and solve verification degrades to "not verified"
 *     rather than the script vanishing.
 *   - solverCfg.recursiveSplines (ImageSolverEngine.js:129) is new in
 *     1.9.5 and silently inert on an older engine. The solve is less
 *     accurate; nothing breaks.
 *   - Astrometric solutions written by 1.9.5 are not readable by earlier
 *     versions (1.9.5 release notes). That is a property of the DATA, not
 *     of this script: masters solved under 1.9.5 and then processed under
 *     1.9.4 are the mixed setup to avoid.
 *
 * And the one that governs everything else: SubframeSelector's
 * measurement table is read BY POSITION (Frames.COL), off 1.9.5 build
 * 1702. A core returning a different table would hand back numbers from
 * the wrong columns, and the Frame Selector deletes files on those
 * numbers. That is why FrameSelector.measure now runs
 * Frames.meaningProblems on the first row of every measurement and
 * abandons the channel if the values are not shaped like the quantities
 * they claim to be -- on ANY core, including the one this was written on.
 * The floor moved down; the guard is what makes that defensible.
 *
 * Deliberately NOT named Util.MIN_VERSION or anything containing the bare
 * token VERSION: Steps.js must `#define VERSION "6.4.2"` for the bundled
 * ImageSolver engine, the preprocessor has one define table for the whole
 * unit, and every later occurrence of that token is substituted. See the
 * comment on Util.LOOM_VERSION above -- this cost hours once already.
 */
Util.MIN_CORE = { major: 1, minor: 9, release: 4 };

/*
 * "1.9.5" from { major: 1, minor: 9, release: 5 }. Missing components read
 * as zero so a core that does not expose one of them still prints and
 * compares as something rather than "undefined".
 */
Util.formatCoreVersion = function( v )
{
   function n( x ) { return ( typeof x == "number" && isFinite( x ) ) ? x : 0; }
   return n( v.major ) + "." + n( v.minor ) + "." + n( v.release );
};

/*
 * True if `found` is at least `required`, comparing (major, minor,
 * release, build) in that order -- the ordinary lexicographic rule, so
 * 1.10.0 is newer than 1.9.5 and not older, which a string or float
 * comparison of the same numbers gets wrong, and 1.10.0 build 3 is newer
 * than 1.9.5 build 1705 whatever its build number. A `required` with no
 * build asks for none (it reads as 0, which every build meets).
 *
 * Pure on purpose: it takes both versions as arguments and reads nothing
 * from CoreApplication, so the comparison can be exercised for versions
 * this machine is not running.
 */
Util.coreVersionAtLeast = function( found, required )
{
   function n( x ) { return ( typeof x == "number" && isFinite( x ) ) ? x : 0; }
   var f = [ n( found.major ), n( found.minor ), n( found.release ), n( found.build ) ];
   var r = [ n( required.major ), n( required.minor ), n( required.release ), n( required.build ) ];
   for ( var i = 0; i < 4; ++i )
   {
      if ( f[i] > r[i] ) return true;
      if ( f[i] < r[i] ) return false;
   }
   return true;   // exactly equal counts as meeting the minimum
};

/*
 * The refusal an entry point makes, before anything else, on a core older
 * than Util.MIN_CORE. Returns true to carry on.
 *
 * `check` is the entry point's own wording: { title, message( core ) },
 * where core is { major, minor, release, build }. Each script has to name
 * itself and say what it needs the version FOR, because on an old core
 * "the script does nothing at all" is the symptom the user actually sees
 * -- an unresolvable #include is discarded silently (see Util.MIN_CORE).
 *
 * Deliberately NOT CoreApplication.ensureMinimumVersion(). Probed on
 * 1.9.5 build 1702: it returns true when the version is met, and on
 * failure it THROWS an ordinary catchable Error reading "This script
 * requires PixInsight core version 99.0.0 or higher." -- it does not
 * abort the script, so it is safe, but the message is the core's and
 * not Loom's. Doing the comparison here also makes it testable, which a
 * core call is not.
 *
 * `core` and `out` ({ criticalln, show }) are for the tests; left out,
 * they are the running core, the console and a modal message box.
 */
Util.checkCoreVersion = function( check, core, out )
{
   core = core || { major:   CoreApplication.versionMajor,
                    minor:   CoreApplication.versionMinor,
                    release: CoreApplication.versionRelease,
                    build:   CoreApplication.versionBuild };
   if ( Util.coreVersionAtLeast( core, Util.MIN_CORE ) )
      return true;

   out = out || {
      criticalln: function( message ) { console.criticalln( message ); },
      show: function( message, title, icon, buttons )
      {
         new MessageBox( message, title, icon, buttons ).execute();
      }
   };
   var message = check.message( core );
   out.criticalln( message );
   out.show( message, check.title, StdIcon_Error, StdButton_Ok );
   return false;
};

/* ------------------------------------------------------------------ */
/* What the running core offers beyond Util.MIN_CORE                   */
/* ------------------------------------------------------------------ */

/*
 * PixInsight 1.9.5 build 1705 brought PJSR structures Loom can use for
 * speed: KDTree (a bucket k-d tree, JavaScript in the core's bootstrap,
 * global without an #include). Loom still runs on older cores, so every
 * use goes through here: the build check and a check that the structure
 * and the methods used exist, answered once. The code these replace stays
 * as the fallback, and both give the same results (PKG-8). No other file
 * names these structures (the selftest checks it).
 */
Util.PI1705_CORE = { major: 1, minor: 9, release: 5, build: 1705 };

Util.native = { forced: null, kdTree: undefined };

/* Whether the running core -- or `core`, for the tests -- is 1.9.5 build 1705 or later. */
Util.pi1705 = function( core )
{
   core = core || { major:   CoreApplication.versionMajor,
                    minor:   CoreApplication.versionMinor,
                    release: CoreApplication.versionRelease,
                    build:   CoreApplication.versionBuild };
   return Util.coreVersionAtLeast( core, Util.PI1705_CORE );
};

/* The test seam: true or false makes every check below answer that; null goes back to the running core's answer. */
Util.forceNative = function( on )
{
   Util.native.forced = ( on === true || on === false ) ? on : null;
};

/* Whether Util.kdTree can be used: a 1705 core with a KDTree that has build() and search(). */
Util.hasKDTree = function()
{
   if ( Util.native.forced !== null ) return Util.native.forced;
   if ( Util.native.kdTree === undefined )
      Util.native.kdTree = Util.pi1705() && typeof KDTree == "function" &&
                           typeof KDTree.prototype.build == "function" && typeof KDTree.prototype.search == "function";
   return Util.native.kdTree;
};

/* A KDTree of `objects` ({ point: array-like }); only when Util.hasKDTree(). */
Util.kdTree = function( objects )
{
   return new KDTree( objects );
};

/* ------------------------------------------------------------------ */
/* Which operating system this is                                      */
/* ------------------------------------------------------------------ */

/*
 * The three values Loom branches on. Anything that is not Windows and not
 * macOS is treated as "unix": Linux and FreeBSD differ from macOS only in
 * where applications live, and every shell assumption Loom makes holds on
 * both.
 */
Util.PLATFORM_WINDOWS = "windows";
Util.PLATFORM_MACOS   = "macos";
Util.PLATFORM_UNIX    = "unix";

/*
 * The platform is decided by the PREPROCESSOR, not at runtime, and that is
 * deliberate.
 *
 * `CoreApplication.platform` exists and returns "macOS" here (probed
 * 2026-09-17), but nothing documents what it returns on Windows, so a
 * string comparison against it would be a guess. `__PI_PLATFORM__` is not
 * a guess: PixInsight's own bundled scripts switch on exactly these
 * spellings -- src/scripts/ContinuumSubtraction.js tests MACOSX,
 * MSWINDOWS and LINUX, and misc/DSSImageDownloader.js uses
 * `#ifoneof __PI_PLATFORM__ MSWINDOWS MACOSX`.
 *
 * The trailing #ifndef is the safety net: if a future core ever reports a
 * fourth value, LOOM_PLATFORM_ID would otherwise be left undefined and the
 * assignment below would throw at load. "unix" is the conservative default
 * because it is what every non-Windows platform behaves like.
 *
 * The name is LOOM_PLATFORM_ID rather than anything shorter because every
 * #define here is a textual substitution applied to the whole script from
 * this point on -- see the note on Util.LOOM_VERSION above for what a
 * common bare token costs.
 */
#ifeq __PI_PLATFORM__ MACOSX
#define LOOM_PLATFORM_ID "macos"
#endif
#ifeq __PI_PLATFORM__ MSWINDOWS
#define LOOM_PLATFORM_ID "windows"
#endif
#ifoneof __PI_PLATFORM__ LINUX FREEBSD
#define LOOM_PLATFORM_ID "unix"
#endif
#ifndef LOOM_PLATFORM_ID
#define LOOM_PLATFORM_ID "unix"
#endif

Util.PLATFORM = LOOM_PLATFORM_ID;

/*
 * Every platform-dependent function in Loom takes the platform as an
 * ARGUMENT and defaults to this, so the selftest can exercise the Windows
 * branch from a Mac. Call sites that do not care pass nothing.
 */
Util.platform = function( platform )
{
   return platform || Util.PLATFORM;
};

Util.isWindows = function( platform )
{
   return Util.platform( platform ) == Util.PLATFORM_WINDOWS;
};

/*
 * PJSR's File API speaks forward slashes on every platform, including
 * Windows: File.homeDirectory and CoreApplication.baseDirPath come back
 * as C:/Users/... and C:/Program Files/PixInsight, and File.exists
 * accepts that form. So paths are BUILT with "/" throughout Loom and no
 * separator variable exists (there is no File.separator; probed).
 *
 * The one place the distinction survives is a path handed to a native
 * program -- see Update.js, where the Windows helper is PowerShell
 * precisely because cmd.exe reads a leading "/" as a switch.
 */

/*
 * Returns `base` if free, otherwise the first free `base_<n>` starting at n=1.
 * `exists` is a predicate taking an identifier and returning true if taken.
 */
Util.uniqueWindowId = function( base, exists )
{
   if ( !exists( base ) )
      return base;
   for ( var n = 1; ; ++n )
   {
      var candidate = base + "_" + n;
      if ( !exists( candidate ) )
         return candidate;
   }
};

Util.windowIdExists = function( id )
{
   return !ImageWindow.windowById( id ).isNull;
};

/*
 * A function reading byte i of a PJSR ByteArray (or a plain array).
 * PixInsight 1.9.5 deprecates ByteArray.at() and prints a console warning
 * for it, so bytes are read by subscript wherever that works; at() is
 * kept for a core where it doesn't.
 */
Util.byteReader = function( b )
{
   if ( b.length == 0 || typeof b[0] == "number" ) return function( i ) { return b[i]; };
   return function( i ) { return b.at( i ); };
};

Util.freeWindowId = function( base )
{
   return Util.uniqueWindowId( base, Util.windowIdExists );
};

/*
 * Tracks every window the script creates so a failure can close all of
 * them without guessing. Windows handed to the user are forgotten first.
 */
Util.Registry = function()
{
   this.windows = [];

   this.add = function( w )
   {
      this.windows.push( w );
      return w;
   };

   this.forget = function( w )
   {
      for ( var i = 0; i < this.windows.length; ++i )
         if ( this.windows[i] === w )
         {
            this.windows.splice( i, 1 );
            return;
         }
   };

   this.closeAll = function()
   {
      for ( var i = 0; i < this.windows.length; ++i )
         try { this.windows[i].forceClose(); }
         catch ( e ) { console.warningln( "Registry: could not close window " + this.windows[i].id + ": " + e ); }
      this.windows = [];
   };
};

/*
 * The centred sub-rectangle covering `fraction` of each dimension.
 * Used to measure medians away from the zero borders that registration
 * leaves, which differ per channel and would otherwise bias comparison.
 */
Util.centralRect = function( width, height, fraction )
{
   var mx = Math.floor( width * (1 - fraction) / 2 );
   var my = Math.floor( height * (1 - fraction) / 2 );
   return { x0: mx, y0: my, x1: width - mx, y1: height - my };
};

/*
 * Key of the lowest value, or null if there are none.
 */
Util.minMedianKey = function( medians )
{
   var keys = Object.keys( medians );
   if ( keys.length == 0 )
      return null;
   var best = keys[0];
   for ( var i = 1; i < keys.length; ++i )
      if ( medians[keys[i]] < medians[best] )
         best = keys[i];
   return best;
};

Util.CHANNELS   = [ "L", "R", "G", "B", "H", "S", "O" ];
Util.BROADBAND  = [ "L", "R", "G", "B" ];
Util.RGB_GROUP  = [ "R", "G", "B" ];
Util.NARROWBAND = [ "H", "S", "O" ];
Util.isBroadband = function( key )
{
   return Util.BROADBAND.indexOf( key ) >= 0;
};


/*
 * FITS string values arrive single-quoted and blank-padded to a fixed
 * width. Returns the bare value, or null when absent or blank.
 */
Util.keywordValue = function( keywords, name )
{
   for ( var i = 0; i < keywords.length; ++i )
      if ( keywords[i].name == name )
      {
         var v = keywords[i].value.trim();
         if ( v.length >= 2 && v.charAt( 0 ) == "'" && v.charAt( v.length-1 ) == "'" )
            v = v.substring( 1, v.length-1 );
         v = v.trim();
         return v.length > 0 ? v : null;
      }
   return null;
};

/*
 * Whether the user has asked to stop. Replaced by Loom.js when a Cancel
 * window is up; a no-op otherwise, so library code can call it freely.
 *
 * Lives here rather than on Pipeline so that Steps -- which runs the long
 * external processes and is where cancellation actually has to bite -- does
 * not have to reach across to Pipeline for it.
 */
Util.cancelRequested = function() { return false; };

/*
 * Fine-grained progress from an external CLI. Replaced by Loom.js so the
 * Cancel window can show a percentage; a no-op otherwise. Separate from
 * the 25% log milestones: these tools emit a line per tile, hundreds per
 * frame, which is far too much for a log but exactly right for a window
 * that repaints in place.
 */
Util.reportProgress = function( percent, text ) {};

/*
 * The current operation, for the Cancel window's one line of text.
 *
 * Separate from reportProgress because most of a run cannot produce a
 * percentage at all: BlurXTerminator, StarXTerminator, NoiseXTerminator
 * and StarNet2 are in-process PixInsight modules, so their "Processing:
 * 78%" goes to the Process Console and no script can read it. Only the
 * SyQon CLIs, run through ExternalProcess, have stdout to parse.
 *
 * Without this the window said "starting..." for the whole of a channel's
 * chain -- solve, SPFC, MGC, GraXpert, aberration, minutes each -- because
 * the only other updater is Pipeline.checkAbort, which fires once per
 * channel AFTER all of it.
 */
Util.reportStage = function( text ) {};

Util.log = function( stage, message )
{
   console.writeln( "<end><cbr>[" + stage + "] " + message );
};

/*
 * One line per expensive operation, in a fixed shape so a run's log can be
 * read at a glance and grepped:
 *
 *   [run] aberration      SyQon Parallax                  -> L
 *   [run] star reduction  SyQon Parallax (low)            -> RGB
 *   [run] noise reduction SyQon Prism (medium, mtf 0.50)  -> HSO
 *
 * `target` is the CHANNEL or COMPOSITE name -- L, R, G, B, H, S, O, RGB,
 * HSO -- never the working view id, which is called things like
 * L_graxpert_1 and tells the reader nothing.
 */
/*
 * The formatted line, separated from the printing so it can be tested.
 *
 * console.noteln cannot be reassigned in PJSR, so a test that tries to
 * capture the printed output captures nothing and asserts on undefined --
 * which is exactly how the first attempt at testing this failed.
 *
 * Pad-to-width, never pad-to-nothing: a name already at the column width
 * would otherwise get no separator at all and run straight into the next
 * field -- "star extractionSyQon Starless". Both "star extraction" and
 * "noise reduction" are exactly 15 characters, so the separating space is
 * added first and the padding fills from there.
 */
Util.operationLine = function( kind, tool, detail, target )
{
   var k = String( kind ) + " ";
   while ( k.length < 16 ) k += " ";
   var t = String( tool || "" ) + ( detail ? " (" + detail + ")" : "" ) + " ";
   while ( t.length < 33 ) t += " ";
   return "[run] " + k + t + "-> " + String( target || "?" );
};

Util.operation = function( kind, tool, detail, target )
{
   /*
    * console.noteln, because it renders GREEN.
    *
    * Established by probing the console, not assumed: PixInsight strips
    * <span style="color:..."> and <font color="..."> outright -- all such
    * variants came out white -- and honours only <b>. Colour is available
    * solely through the semantic channels, of which noteln is the green one
    * (warningln is magenta, writeln plain).
    */
   console.noteln( "<end><cbr>" + Util.operationLine( kind, tool, detail, target ) );
   /*
    * Called here, immediately BEFORE the expensive call this announces:
    * this is the last moment the main thread is free, and a module that
    * runs in-process will hold it until it finishes.
    */
   Util.reportStage( String( kind ) +
                     ( tool ? ( ": " + tool ) : "" ) +
                     ( detail ? ( " (" + detail + ")" ) : "" ) +
                     " → " + String( target || "?" ) );
};

Util.warn = function( stage, message )
{
   console.warningln( "<end><cbr>[" + stage + "] " + message );
};

Util.error = function( stage, message )
{
   console.criticalln( "<end><cbr>[" + stage + "] " + message );
};

/*
 * Structural problems with a channel selection, independent of the
 * filesystem. Returns a list of human-readable problems; empty is valid.
 * No channel is required on its own: without L Loom registers to the best
 * channel it has (Pipeline.registrationReference), so RGB-only and
 * narrowband-only sets are valid; there must be something to combine.
 */
Util.validateSelection = function( paths, views )
{
   views = views || {};
   return Util.channelGroupProblems( paths, views )
      .concat( Util.duplicateFileProblems( paths ) );
};

/*
 * A channel counts as supplied if it has EITHER a file path or an open
 * view. Checking only paths silently ignores every view the user added.
 */
Util.channelSupplied = function( paths, views, k )
{
   return ( paths[k] != undefined && paths[k].length > 0 )
       || ( views[k] != undefined && String( views[k] ).length > 0 );
};

/*
 * The RGB group is all-or-nothing: ChannelCombination needs all three.
 * Any subset of narrowband is fine, including a single channel -- but
 * there has to be one or the other.
 */
Util.channelGroupProblems = function( paths, views )
{
   var problems = [];
   var rgbPresent = [], rgbMissing = [];
   for ( var r = 0; r < Util.RGB_GROUP.length; ++r )
      ( Util.channelSupplied( paths, views, Util.RGB_GROUP[r] ) ? rgbPresent : rgbMissing )
         .push( Util.RGB_GROUP[r] );

   if ( rgbPresent.length > 0 && rgbMissing.length > 0 )
      problems.push( "Incomplete RGB set: missing " + rgbMissing.join( ", " ) +
                     ". Supply all three or none." );

   var nbCount = Util.NARROWBAND.filter( function( k )
                    { return Util.channelSupplied( paths, views, k ); } ).length;
   if ( rgbPresent.length == 0 && nbCount == 0 )
      problems.push( "Nothing to do: supply R, G and B, or at least one of H, S, O" );
   return problems;
};

// The same file named for two channels, reported once per repeat.
Util.duplicateFileProblems = function( paths )
{
   var problems = [];
   var seen = {};
   for ( var j = 0; j < Util.CHANNELS.length; ++j )
   {
      var k = Util.CHANNELS[j];
      var p = paths[k];
      if ( !p || p.length == 0 )
         continue;
      if ( seen[p] !== undefined )
         problems.push( "Same file selected for " + seen[p] + " and " + k + ": " + p );
      else
         seen[p] = k;
   }
   return problems;
};

/*
 * Maps a FITS FILTER keyword value to one of the seven channel keys, or
 * null when it cannot be recognised. The owner's masters carry bare
 * single letters ("L", "H", "O", ...), but filter wheels are commonly
 * configured with longer names, so the usual spellings are accepted too.
 * Matching is case-insensitive and ignores punctuation and spacing.
 */
Util.channelFromFilter = function( filterValue )
{
   if ( filterValue == null )
      return null;

   var f = String( filterValue ).toLowerCase().replace( /[^a-z0-9]/g, "" );
   if ( f.length == 0 )
      return null;

   // Exact single-letter channel names first: this is what WBPP writes.
   if ( Util.FILTER_LETTERS.hasOwnProperty( f ) )
      return Util.FILTER_LETTERS[f];

   for ( var i = 0; i < Util.FILTER_NAMES.length; ++i )
   {
      var rule = Util.FILTER_NAMES[i];
      if ( f.indexOf( rule.token ) >= 0 || f == rule.exact )
         return rule.channel;
   }
   return null;
};

Util.FILTER_LETTERS = { l: "L", r: "R", g: "G", b: "B", h: "H", s: "S", o: "O" };

/*
 * Common long forms, tried in order: a name containing `token`, or equal
 * to `exact`. Narrowband is checked BEFORE broadband, because "halpha"
 * contains no broadband token but "sii"/"oiii" would be mis-read by a
 * naive substring test against "i".
 */
Util.FILTER_NAMES = [ { token: "halpha", exact: "ha", channel: "H" },
                      { token: "sii",    exact: "s2", channel: "S" },
                      { token: "oiii",   exact: "o3", channel: "O" },
                      { token: "lum",    exact: null, channel: "L" },
                      { token: "red",    exact: null, channel: "R" },
                      { token: "green",  exact: null, channel: "G" },
                      { token: "blue",   exact: null, channel: "B" } ];

/*
 * The selection list is remembered between runs. Entries serialise as
 * one record per line, "source<TAB>ref" -- tab and newline are the two
 * characters least likely to appear in a file path or a view id.
 */
Util.serializeEntries = function( entries )
{
   var out = [];
   for ( var i = 0; i < entries.length; ++i )
      out.push( entries[i].source + "\t" + entries[i].ref );
   return out.join( "\n" );
};

Util.deserializeEntries = function( text )
{
   var result = [];
   if ( !text || text.length == 0 )
      return result;
   var lines = String( text ).split( "\n" );
   for ( var i = 0; i < lines.length; ++i )
   {
      if ( lines[i].length == 0 )
         continue;
      var tab = lines[i].indexOf( "\t" );
      if ( tab < 0 )
         continue;
      var source = lines[i].substring( 0, tab );
      var ref = lines[i].substring( tab + 1 );
      if ( ( source == "view" || source == "file" ) && ref.length > 0 )
         result.push( { source: source, ref: ref } );
   }
   return result;
};

/*
 * Drizzle factor from the XPIXSZ FITS keyword. Drizzling divides the
 * recorded pixel size: the ASI2600MM's native 3.76 um becomes 1.88 at 2x
 * and 0.94 at 4x. This is read from metadata rather than parsed out of a
 * filename, so a renamed master still reports correctly.
 * Returns "" when the value is native or unreadable.
 */
Util.NATIVE_PIXEL_SIZE = 3.76;

Util.drizzleLabel = function( xpixsz, nativePixelSize )
{
   var native = nativePixelSize || Util.NATIVE_PIXEL_SIZE;
   var v = parseFloat( xpixsz );
   if ( !isFinite( v ) || v <= 0 )
      return "";
   var factor = native / v;
   var rounded = Math.round( factor );
   // Only report clean integer factors; anything else is not a drizzle.
   if ( rounded < 2 || Math.abs( factor - rounded ) > 0.05 )
      return "";
   return rounded + "x";
};

/*
 * Maps a camera's FITS INSTRUME value to the name of a QE curve in
 * PixInsight's filters.xspd. Only sensors whose mapping is certain are
 * listed; anything unrecognised returns null so the caller falls back to
 * the Ideal QE curve rather than calibrating against the wrong sensor.
 *
 * Matching is on normalised text (lowercase, alphanumeric only), so
 * "ZWO ASI2600MM Air", "ASI2600MM Pro" and "ASI 2600MM" all resolve.
 */
Util.IDEAL_QE_CURVE_NAME = "Ideal QE curve";

Util.CAMERA_QE_CURVES = [
   // IMX571 family: ASI2600, ASI6200 (455), ASI094 (461), ASI533, QHY268
   { match: [ "asi2600", "asi6200", "asi533", "asi094", "qhy268", "qhy600",
              "imx571", "imx455", "imx461", "imx533", "imx411" ],
     curve: "Sony IMX411/455/461/533/571" },
   { match: [ "asi1600", "mn34230" ], curve: "Panasonic MN34230 (ASI1600MM)" },
   { match: [ "asi178", "imx178" ],   curve: "Sony IMX178" },
   { match: [ "asi183", "imx183" ],   curve: "Sony IMX183" },
   { match: [ "asi294", "imx492" ],   curve: "Sony IMX492" },
   { match: [ "asi585", "imx585" ],   curve: "Sony IMX585" },
   { match: [ "kaf16200" ],           curve: "KAF-16200" },
   { match: [ "kaf16803" ],           curve: "KAF-16803" },
   { match: [ "kaf8300" ],            curve: "KAF-8300" }
];

/*
 * "2026-09-14 13:02" -- date and time, no seconds and no timezone. These
 * are stamps to compare against each other ("is this the master I stacked
 * this afternoon?"), not timestamps to compute with, and a narrow column
 * that always has the same width reads faster than a locale string.
 *
 * Returns "" for a missing or unusable time rather than a fake one: a
 * dropped view has no file, and "" says so where 1970 would not.
 */
/*
 * Master quality, compared WITHIN a channel.
 *
 * Deliberately not across channels: on this rig G is always the softest
 * filter -- 9.85 px against R's 8.06 on a stack judged perfectly good --
 * so any cross-channel threshold that catches a bad G also fires on a
 * good one. Comparing a stack against the OTHER stacks of its own channel
 * has no such confound and answers the question that actually matters:
 * is the one being used better or worse than the one it displaced.
 *
 * The four figures SubframeSelector reports, in the units its own
 * subframe table shows: FWHM, eccentricity, noise and star count.
 *
 * The SENSE differs between them, which is why nothing here tries to
 * reduce them to one score: smaller is better for FWHM, eccentricity and
 * noise, but LARGER is better for the star count. A negative delta is an
 * improvement in the first three and a regression in the last.
 */
/*
 * The stacks of the same channel that were NOT chosen -- newest first.
 *
 * Same rank as well as same channel: a drizzled autocrop master is not
 * comparable with a plain one, and ranking already decided which class is
 * in play. `limit` bounds the cost, because each one costs a
 * SubframeSelector measurement; one is enough to answer "is this better
 * than what I had".
 */
Util.sameChannelAlternatives = function( candidates, pick, limit )
{
   if ( pick == null )
      return [];
   var out = [];
   for ( var i = 0; i < candidates.length; ++i )
   {
      var c = candidates[i];
      if ( !c || c.channel != pick.channel || c.path == pick.path )
         continue;
      if ( Util.masterVariantRank( c.drizzle, c.autocrop ) !=
           Util.masterVariantRank( pick.drizzle, pick.autocrop ) )
         continue;
      out.push( c );
   }
   out.sort( function( a, b ) { return ( b.mtime || 0 ) - ( a.mtime || 0 ); } );
   return ( limit == null ) ? out : out.slice( 0, limit );
};

Util.qualityDelta = function( chosen, other )
{
   if ( chosen == null || other == null )
      return null;
   function pct( a, b ) { return ( a > 0 && b > 0 ) ? ( 100*( a - b )/b ) : null; }
   return { fwhm:         pct( chosen.fwhm, other.fwhm ),
            eccentricity: pct( chosen.eccentricity, other.eccentricity ),
            noise:        pct( chosen.noise, other.noise ),
            stars:        pct( chosen.stars, other.stars ) };
};

/*
 * The sharpest alternative, so the comparison is against the best thing
 * available for that channel rather than an arbitrary one. SMALLER IS
 * BETTER for FWHM, which is the opposite of the SNR this replaced -- a
 * comparison left pointing the wrong way would quietly flatter every new
 * stack.
 */
Util.bestAlternative = function( others )
{
   var best = null;
   for ( var i = 0; i < others.length; ++i )
   {
      var o = others[i];
      if ( o == null || !( o.fwhm > 0 ) )
         continue;
      if ( best == null || o.fwhm < best.fwhm )
         best = o;
   }
   return best;
};

/*
 * "+5%" / "-13%" / "" -- a sign is always shown, because the sign IS the
 * message, and nothing is shown below 1% where the difference is noise in
 * the measurement rather than in the data.
 */
Util.formatDelta = function( pct )
{
   if ( pct == null || !isFinite( pct ) )
      return "";
   if ( Math.abs( pct ) < 1 )
      return "";
   return ( pct > 0 ? "+" : "" ) + pct.toFixed( 0 ) + "%";
};

/*
 * "Measuring masters: G (3 of 7)".
 *
 * A count, not a spinner. Each master costs a SubframeSelector execution
 * of around sixteen seconds, so a folder of seven holds the dialog for two
 * minutes; the only thing worth saying during that is how much of the wait
 * is left. The count is per master rather than per folder, because a
 * per-folder message never changes and so answers nothing.
 */
/*
 * How many entries could actually be processed.
 *
 * A view that has since been closed is listed so its absence is visible,
 * but it is not something to run on -- and a list made only of those is a
 * list with nothing in it, however many rows it shows.
 */
Util.runnableEntryCount = function( entries )
{
   if ( entries == null )
      return 0;
   var n = 0;
   for ( var i = 0; i < entries.length; ++i )
      if ( entries[i] != null && !entries[i].unavailable )
         ++n;
   return n;
};

/*
 * Shorten a name to `max` characters by dropping the FRONT, not the middle
 * and not the end.
 *
 * Subframe names are a long shared prefix -- target, exposure, binning,
 * camera -- followed by the only part that differs: the timestamp and the
 * sequence number. Eliding the middle or the tail throws that away and
 * leaves a column of identical-looking rows; eliding the head keeps
 * exactly the part that identifies the frame.
 */
Util.elideHead = function( text, max )
{
   var t = ( text == null ) ? "" : String( text );
   var n = Number( max );
   if ( !isFinite( n ) || n < 4 || t.length <= n )
      return t;
   return "..." + t.substring( t.length - ( n - 3 ) );
};

/*
 * The moving block of a progress bar whose total is not known yet (a card
 * being read). An empty bar there looked like nothing happening; a block
 * going back and forth says the work is alive. Placed by the time since
 * the step began, not by the number of reports, so it moves at the same
 * speed however fast or slow the files come -- and it moves only when a
 * report comes in, so a step that really has stopped shows as stopped.
 */
Util.PULSE_PERIOD_MS = 1600;

Util.pulseBlock = function( elapsedMs, width )
{
   var w = Math.max( 8, Math.round( width/4 ) ), travel = Math.max( 0, width - w );
   var t = Number( elapsedMs );
   if ( !isFinite( t ) || t < 0 )
      return { x: 0, width: w };
   var phase = ( t % Util.PULSE_PERIOD_MS )/Util.PULSE_PERIOD_MS;
   var along = ( phase < 0.5 ) ? 2*phase : 2 - 2*phase;
   return { x: Math.round( along*travel ), width: w };
};

Util.scanProgressMessage = function( action, label, done, total )
{
   var text = String( action );
   var name = ( label == null ) ? "" : String( label ).trim();
   if ( name )
      text += ": " + name;
   var n = Number( done ), t = Number( total );
   if ( isFinite( n ) && isFinite( t ) && n > 0 && t > 0 )
      text += " (" + Math.min( n, t ) + " of " + t + ")";
   return text;
};

Util.formatFileTime = function( ms )
{
   if ( ms == null || !isFinite( ms ) || ms <= 0 )
      return "";
   var d = new Date( ms );
   function p2( n ) { return ( n < 10 ? "0" : "" ) + n; }
   return d.getFullYear() + "-" + p2( d.getMonth()+1 ) + "-" + p2( d.getDate() ) +
          " " + p2( d.getHours() ) + ":" + p2( d.getMinutes() );
};

/*
 * The file's creation time in milliseconds, or 0.
 *
 * FileFind exposes `created` and FileInfo `timeCreated`; both are real
 * Dates on macOS. Kept in one place because the fallback matters: a file
 * on a filesystem without a birth time reports its modification time
 * instead, which is still the more useful of the two answers here.
 */
Util.fileCreatedMs = function( path, io )
{
   var F = io || ( typeof FileInfo != "undefined" ? FileInfo : null );
   if ( F == null )
      return 0;
   try
   {
      var fi = new F( path );
      var t = fi.timeCreated || fi.lastModified;
      return t ? t.getTime() : 0;
   }
   catch ( e ) { return 0; }
};

/*
 * The immediate children of `root`, with "." and ".." dropped. One level
 * only -- the application scan in Steps is deliberately not recursive, and
 * this is where that stops.
 *
 * A root that cannot be enumerated yields the empty list rather than an
 * error: the scan walks a list of places applications MIGHT live, and one
 * of them being absent is the normal case, not a fault.
 */
Util.directoryEntries = function( root )
{
   var entries = [];
   try
   {
      var find = new FileFind;
      if ( find.begin( root + "/*" ) )
         do
         {
            if ( find.name != "." && find.name != ".." )
               entries.push( find.name );
         }
         while ( find.next() );
   }
   catch ( e )
   {
      return [];
   }
   return entries;
};

/* Keywords plus geometry from a single open, so the dialog can show size. */
/*
 * Reads keywords and geometry WITHOUT decoding pixel data.
 *
 * The obvious implementation -- ImageWindow.open() then read .keywords --
 * loads and decompresses the whole image to extract a few header fields.
 * For a master folder that is ruinous: ~51 masterLight files at 300-850 MB
 * each is tens of gigabytes read off an external drive to learn FILTER,
 * XPIXSZ and the dimensions.
 *
 * FileFormatInstance.open() returns the ImageDescription array from the
 * header alone; pixels are only read by the separate readImage() call,
 * which is never made here. Keywords come from the instance directly.
 *
 * Falls back to the old whole-image path if the format cannot be
 * instantiated, so an exotic format still works, just slowly.
 */
/*
 * Within one run the same file is asked about more than once -- preflight
 * checks FILTER, the load loop wants FILTER and INSTRUME, the dialog lists
 * geometry -- and re-reading a header off an external drive for each is
 * waste. Keyed on identity, not just path, so a file replaced under the
 * same name is not served from a stale entry.
 */
Util.imageInfoCache = {};

Util.imageInfoCacheKey = function( path )
{
   try
   {
      var fi = new FileInfo( path );
      if ( !fi.exists )
         return null;
      return path + "|" + fi.size + "|" + fi.lastModified.toISOString();
   }
   catch ( e ) { return null; }
};

/*
 * Reads geometry and keywords from `path`'s header alone, without decoding
 * a single pixel.
 *
 * Returns { info, why }: `info` is the header fields on success and null on
 * failure, and `why` then carries the diagnostic string the caller prints
 * before it falls back to a full read. Every failure point has its own
 * reason -- the reason is the whole point of this probe, because the
 * fallback is expensive enough that "it did not work" is not a useful
 * thing to read in a log.
 */
Util.tryHeaderRead = function( path )
{
   try
   {
      var ext = File.extractExtension( path );
      var F = new FileFormat( ext, true /*toRead*/, false /*toWrite*/ );
      if ( F.isNull )
         return { info: null, why: "no reader for extension '" + ext + "'" };

      var f = new FileFormatInstance( F );
      if ( f.isNull )
         return { info: null, why: "could not instantiate the " + ext + " reader" };

      var d = f.open( path, "verbosity 0" );
      if ( d == null || d.length < 1 )
      {
         try { f.close(); } catch ( e ) {}
         return { info: null, why: "the reader returned no image description" };
      }

      var info = {
         keywords: F.canStoreKeywords ? f.keywords : [],
         width: d[0].width,
         height: d[0].height
      };
      // The header is already in hand; a close that fails now costs
      // nothing and must not send the caller down the full-read path.
      try { f.close(); } catch ( e1 ) {}
      return { info: info, why: null };
   }
   catch ( e2 )
   {
      return { info: null, why: String( e2 ) };
   }
};

Util.readImageInfo = function( path )
{
   var ck = Util.imageInfoCacheKey( path );
   if ( ck != null && Util.imageInfoCache[ck] != null )
      return Util.imageInfoCache[ck];

   var probe = Util.tryHeaderRead( path );
   var info = probe.info;

   /*
    * The fallback reads and decodes the ENTIRE image to recover a few
    * header fields -- roughly a gigabyte per master here. It used to be
    * reached silently whenever the reader could not be instantiated, so a
    * run could be doing full reads on every file with nothing in the log
    * to say so. It is always announced now.
    */
   if ( info == null )
   {
      Util.warn( "read", "header-only read unavailable for " + path +
                         " (" + ( probe.why || "unknown reason" ) + ")" +
                         " -- falling back to a FULL read of the image" );
      var w = ImageWindow.open( path );
      if ( w.length == 0 )
         info = { keywords: [], width: 0, height: 0 };
      else
      {
         info = { keywords: w[0].keywords,
                  width: w[0].mainView.image.width,
                  height: w[0].mainView.image.height };
         w[0].forceClose();
      }
   }

   if ( ck != null )
      Util.imageInfoCache[ck] = info;
   return info;
};

/*
 * The camera, inferred across a set of masters when a header does not
 * carry it.
 *
 * WBPP's own autocrop step rewrites the header and drops INSTRUME (its
 * mark is the WBPPCROP keyword), so one channel of a session can arrive
 * without a camera while its siblings have one. Physically they cannot
 * differ -- the masters of a session come off one camera -- and an
 * unnamed camera is not a harmless blank: Steps.deviceCurveForImage
 * falls back to the ideal QE curve, so that channel would be calibrated
 * against a different device response from the others.
 *
 * Only a UNANIMOUS answer is returned. If two masters name different
 * cameras the premise does not hold, and guessing which one is right
 * would be worse than leaving it unknown.
 */
Util.commonInstrument = function( values )
{
   var seen = null;
   for ( var i = 0; i < values.length; ++i )
   {
      var v = values[i];
      if ( v == null || v === "" )
         continue;
      if ( seen == null )
         seen = v;
      else if ( seen != v )
         return null;
   }
   return seen;
};

Util.qeCurveNameForCamera = function( instrume )
{
   if ( instrume == null )
      return null;
   var n = String( instrume ).toLowerCase().replace( /[^a-z0-9]/g, "" );
   if ( n.length == 0 )
      return null;
   for ( var i = 0; i < Util.CAMERA_QE_CURVES.length; ++i )
   {
      var entry = Util.CAMERA_QE_CURVES[i];
      for ( var j = 0; j < entry.match.length; ++j )
         if ( n.indexOf( entry.match[j] ) >= 0 )
            return entry.curve;
   }
   return null;
};

/*
 * Intersection of rectangles given as { x0, y0, x1, y1 }. Returns null if
 * they do not overlap. Used to find the area covered by every registered
 * channel, so all outputs can be cropped to identical bounds.
 */
Util.intersectRects = function( rects )
{
   if ( !rects || rects.length == 0 )
      return null;
   var r = { x0: rects[0].x0, y0: rects[0].y0, x1: rects[0].x1, y1: rects[0].y1 };
   for ( var i = 1; i < rects.length; ++i )
   {
      r.x0 = Math.max( r.x0, rects[i].x0 );
      r.y0 = Math.max( r.y0, rects[i].y0 );
      r.x1 = Math.min( r.x1, rects[i].x1 );
      r.y1 = Math.min( r.y1, rects[i].y1 );
   }
   return ( r.x1 > r.x0 && r.y1 > r.y0 ) ? r : null;
};

/*
 * Narrowband palettes: which channel drives each of R, G and B.
 *
 *   SHO  the Hubble palette    R=S  G=H  B=O
 *   HOO  bicolour              R=H  G=O  B=O   (O used twice)
 *   HSO                        R=H  G=S  B=O
 */
Util.PALETTES = {
   SHO: [ "S", "H", "O" ],
   HOO: [ "H", "O", "O" ],
   HSO: [ "H", "S", "O" ]
};

/*
 * Emission line wavelengths in nm, for SPCC's narrowband mode.
 *
 * These are physical constants of the lines, not properties of anyone's
 * filters -- the filter contributes only the BANDWIDTH, which differs by
 * make and model and is therefore configurable.
 */
Util.NARROWBAND_NM = { H: 656.28, S: 671.60, O: 500.70 };

/*
 * The (wavelength, channel) triple a palette presents to SPCC, in R,G,B
 * order. HOO maps O to both green and blue, which is legitimate: the same
 * line feeds two channels.
 */
Util.paletteWavelengths = function( palette )
{
   var map = Util.PALETTES[palette];
   if ( !map )
      return null;
   var out = [];
   for ( var i = 0; i < 3; ++i )
   {
      var nm = Util.NARROWBAND_NM[ map[i] ];
      if ( nm == null )
         return null;
      out.push( { channel: map[i], nm: nm } );
   }
   return out;
};

Util.paletteNames = function()
{
   return Object.keys( Util.PALETTES );
};

/* The distinct channels a palette needs, in no particular order. */
Util.paletteChannels = function( palette )
{
   var map = Util.PALETTES[palette];
   if ( !map )
      return null;
   var seen = {}, out = [];
   for ( var i = 0; i < map.length; ++i )
      if ( !seen[map[i]] ) { seen[map[i]] = true; out.push( map[i] ); }
   return out;
};

/*
 * Which required channels are missing for a palette, given what was
 * supplied. Empty array means it can be built.
 */
Util.paletteMissing = function( palette, available )
{
   var need = Util.paletteChannels( palette );
   if ( need == null )
      return null;
   var missing = [];
   for ( var i = 0; i < need.length; ++i )
      if ( available.indexOf( need[i] ) < 0 )
         missing.push( need[i] );
   return missing;
};

/*
 * Extra Gaussian sigma needed to widen a PSF from `current` to `target`.
 * Gaussians add in quadrature, so sigma_add = sqrt(target^2 - current^2).
 * Returns 0 when the channel is already at or wider than the target --
 * never a NaN from a negative root.
 */
/* ---------------------------------------------------------------------------
 * Master selection: picking one master per filter out of a folder.
 *
 * A WBPP master folder accumulates many masters per filter -- successive
 * runs write _(1), _(2) generations, and each integration may exist as
 * plain, drizzled, autocropped, or both -- one real target folder held
 * 51 masterLight files for 7 filters.
 *
 * Ranking, in order:
 *   1. variant     drizzled AND autocropped beats drizzled beats autocropped
 *                  beats plain
 *   2. most recent within that variant
 *
 * WHY VARIANT FIRST, when "latest" was stated first: if recency were the
 * primary key the variant rules could only ever break an exact timestamp
 * tie, which essentially never happens -- they would be dead rules. Variant
 * first is the only reading in which all three rules do work. Recency then
 * resolves the _(1)/_(2) generations within a variant, which is exactly what
 * it is needed for.
 * ------------------------------------------------------------------------- */

/* Autocrop is not recorded in metadata, so the filename is the only source. */
Util.isAutocropName = function( name )
{
   return /autocrop/i.test( String( name || "" ) );
};

/*
 * Calibration masters are not lights. IMAGETYP is authoritative when
 * present; the name prefix is the fallback for files that lack it.
 */
Util.isMasterLight = function( name, imagetyp )
{
   if ( imagetyp != null && String( imagetyp ).length > 0 )
      return /light/i.test( String( imagetyp ) );
   return /^masterLight/i.test( String( name || "" ) );
};

/* 3 = drizzled + autocropped, 2 = drizzled, 1 = autocropped, 0 = plain. */
Util.masterVariantRank = function( drizzleLabel, autocrop )
{
   var d = ( drizzleLabel != null && String( drizzleLabel ).length > 0 ) ? 1 : 0;
   var a = autocrop ? 1 : 0;
   if ( d && a ) return 3;
   if ( d ) return 2;
   if ( a ) return 1;
   return 0;
};

/*
 * Picks the best master per channel.
 *
 * `candidates` is an array of { channel, drizzle, autocrop, mtime, ... };
 * entries with no channel are ignored. Returns an object keyed by channel,
 * each value the winning candidate with `rank` attached.
 */
/*
 * Parses what a WBPP master's FILENAME claims about it.
 *
 * This is a PRE-FILTER ONLY. A master folder holds dozens of files and
 * opening each one to read a header -- even a header-only read -- is wasted
 * work when the name already says which filter and variant it is. Rank on
 * names, then confirm only the winners against their FILTER keyword, which
 * stays authoritative: if the header disagrees with the name, the header
 * wins and the name was just a hint that got us to the right file quickly.
 *
 * Returns null when the name carries no filter token, which means the file
 * has to be opened to know anything about it.
 *
 * Real examples this must handle:
 *   masterLight_BIN-1_6248x4176_EXPOSURE-60.00s_FILTER-L_mono_drizzle_2x_(1)_autocrop.xisf
 *   masterLight_BIN-1_6248x4176_EXPOSURE-180.00s_FILTER-O_mono_fastIntegration.xisf
 *   masterLight_BIN-1_6248x4176_EXPOSURE-60.00s_FILTER-B_mono_(2).xisf
 */
Util.parseMasterName = function( name )
{
   var n = String( name || "" );
   var m = /FILTER-([A-Za-z][A-Za-z0-9]*)/i.exec( n );
   if ( m == null )
      return null;
   var dm = /drizzle_(\d+)x/i.exec( n );
   return {
      filter: m[1],
      channel: Util.channelFromFilter( m[1] ),
      drizzle: dm ? ( dm[1] + "x" ) : "",
      autocrop: Util.isAutocropName( n )
   };
};

Util.selectMasters = function( candidates )
{
   var best = {};
   for ( var i = 0; i < candidates.length; ++i )
   {
      var c = candidates[i];
      if ( !c || !c.channel ) continue;

      var rank = Util.masterVariantRank( c.drizzle, c.autocrop );
      var cur = best[c.channel];
      if ( cur == null ||
           rank > cur.rank ||
           ( rank == cur.rank && ( c.mtime || 0 ) > ( cur.mtime || 0 ) ) )
      {
         c.rank = rank;
         best[c.channel] = c;
      }
   }
   return best;
};

Util.sigmaToReach = function( target, current )
{
   var t = parseFloat( target ), c = parseFloat( current );
   if ( !isFinite( t ) || !isFinite( c ) || t <= c )
      return 0;
   return Math.sqrt( t*t - c*c );
};

/* FWHM of a Gaussian from its sigma, and back. */
Util.sigmaToFWHM = function( sigma ) { return sigma * 2.3548200450309493; };
Util.fwhmToSigma = function( fwhm )  { return fwhm / 2.3548200450309493; };
