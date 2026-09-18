/*
 * Enough of PJSR for Loom's pure logic to run under node.
 *
 * THE RULE FOR THIS FILE: a shim may be absent, but it may never LIE. If
 * the real PJSR object behaves differently, the shim must not paper over
 * it -- a fake that quietly answers differently turns CI green while the
 * script is broken, which is worse than having no CI at all. Update.io
 * once read `standardOutput`, a property ExternalProcess does not have; a
 * generous shim would have "passed" that forever.
 *
 * So anything whose real behaviour cannot be reproduced honestly throws
 * NotShimmed, and the test that needs it is marked as PixInsight-only
 * rather than being given a convincing-looking fake.
 */

const crypto = require( "crypto" );
const fs = require( "fs" );
const os = require( "os" );
const path = require( "path" );

function notShimmed( what )
{
   throw new Error( "NotShimmed: " + what + " has no honest node equivalent; " +
                    "this test belongs in the PixInsight-only set" );
}

/* ---- File ------------------------------------------------------------ */

/*
 * File is BOTH a namespace of static helpers and a constructor -- the
 * suite writes its result with `new File` + createForWriting/outText.
 */
global.File = function () {
   this._path = null; this._buf = "";
   this.createForWriting = p => { this._path = p; this._buf = ""; };
   this.outText = t => { this._buf += t; };
   this.write = t => { this._buf += t; };
   this.close = () => { if ( this._path ) fs.writeFileSync( this._path, this._buf ); };
};

Object.assign( global.File, {
   exists:            p => { try { return fs.statSync( p ).isFile(); } catch ( e ) { return false; } },
   directoryExists:   p => { try { return fs.statSync( p ).isDirectory(); } catch ( e ) { return false; } },
   readTextFile:      p => fs.readFileSync( p, "utf8" ),
   writeTextFile:     ( p, t ) => fs.writeFileSync( p, t ),
   remove:            p => fs.unlinkSync( p ),
   move:              ( a, b ) => fs.renameSync( a, b ),
   createDirectory:   ( p ) => fs.mkdirSync( p, { recursive: true } ),
   extractDirectory:  p => path.dirname( p ),
   extractName:       p => path.basename( p, path.extname( p ) ),
   extractExtension:  p => path.extname( p ),
   systemTempDirectory: os.tmpdir(),
   homeDirectory:     os.homedir()
} );

/* ---- Hashing --------------------------------------------------------- */

global.CryptographicHash = function ( algorithm ) { this.algorithm = algorithm; };
global.CryptographicHash.SHA1 = "sha1";
global.CryptographicHash.prototype.hash = function ( data )
{
   const h = crypto.createHash( "sha1" ).update( Buffer.from( data ) ).digest( "hex" );
   return { toHex: () => h };
};

global.ByteArray = {
   stringToUTF8: s => Buffer.from( String( s ), "utf8" )
};

/* ---- Console --------------------------------------------------------- */

const quiet = () => {};
global.console_pjsr = {
   writeln: quiet, noteln: quiet, warningln: quiet, criticalln: quiet,
   show: quiet, hide: quiet, flush: quiet,
   beginLog: quiet, endLog: quiet, abortEnabled: false, abortRequested: false
};

/* ---- format ---------------------------------------------------------- */

/*
 * PJSR's printf. Loom uses exactly one family of specifier -- %.Nf, to
 * write process parameters at full precision -- so that is what this
 * implements, and anything else throws rather than guessing. A shim that
 * silently mis-formats a number would corrupt a cache key without ever
 * failing a test.
 */
global.format = function ( spec )
{
   const args = Array.prototype.slice.call( arguments, 1 );
   let i = 0;
   return String( spec ).replace( /%([-+ 0-9.]*)([a-zA-Z%])/g, ( all, flags, kind ) =>
   {
      if ( kind === "%" )
         return "%";
      const v = args[i++];
      switch ( kind )
      {
      case "f":
      {
         const m = /\.(\d+)/.exec( flags );
         return Number( v ).toFixed( m ? Number( m[1] ) : 6 );
      }
      case "d": return String( Math.round( Number( v ) ) );
      case "s": return String( v );
      default:  return notShimmed( "format specifier %" + kind );
      }
   } );
};

/* ---- Application ----------------------------------------------------- */

/*
 * The install paths deliberately point at a directory that does not
 * exist, EVEN ON A MACHINE WITH PIXINSIGHT INSTALLED.
 *
 * Otherwise a run here is not a rehearsal of a run on a CI machine: tests
 * that read filters.xspd out of the real installation passed locally and
 * failed on the runner, which is a slow and confusing way to discover
 * that the local run was never representative.
 */
const NO_INSTALL = "/nonexistent/PixInsight";

global.CoreApplication = {
   instance: 1,
   filePath: NO_INSTALL + "/PixInsight",
   baseDirPath: NO_INSTALL,
   srcDirPath: NO_INSTALL + "/src",
   libDirPath: NO_INSTALL + "/lib",
   configDirPath: NO_INSTALL + "/etc",
   platform: "macos",
   processEvents: quiet
};

global.ElapsedTime = function () { this.text = "0.00 s"; this.reset = quiet; };

/*
 * FileFind, which CAN be reproduced honestly: it enumerates a directory.
 * PJSR yields "." and ".." like the C API it wraps, and Loom's callers
 * filter them out -- so the shim yields them too. Hiding them would make
 * a test pass here that fails in PixInsight.
 */
global.FileFind = function ()
{
   let entries = [], i = -1, dir = "";
   this.begin = function ( pattern )
   {
      dir = path.dirname( pattern );
      try { entries = [ ".", ".." ].concat( fs.readdirSync( dir ) ); }
      catch ( e ) { entries = []; }
      i = -1;
      return this.next();
   };
   this.next = function ()
   {
      if ( ++i >= entries.length )
         return false;
      const name = entries[i];
      this.name = name;
      let st = null;
      try { st = fs.statSync( path.join( dir, name ) ); } catch ( e ) {}
      this.isDirectory = ( name === "." || name === ".." ) ? true
                       : ( st ? st.isDirectory() : false );
      this.isFile = st ? st.isFile() : false;
      this.size = st ? st.size : 0;
      return true;
   };
};

/* ---- Deliberately dishonest to fake: these throw -------------------- */

global.ExternalProcess = function () { notShimmed( "ExternalProcess" ); };
global.ExternalProcess.startDetached = () => notShimmed( "ExternalProcess.startDetached" );
global.ImageWindow = function () { notShimmed( "ImageWindow" ); };
/*
 * Honest, not a convenience: in a headless run no image windows exist, so
 * "nothing by that id" is the truth rather than a fake. PJSR signals it
 * with a null window rather than null itself.
 */
global.ImageWindow.windowById = () => ( { isNull: true } );
global.ImageWindow.windows = [];
global.ImageWindow.openWindows = [];
global.Settings = { read: () => null, write: quiet };
global.Parameters = { has: () => false, get: () => null, set: quiet, clear: quiet };

/*
 * The UI classes are NOT shimmed. Constructing a dialog against fakes
 * would prove only that the fakes are consistent with themselves, and two
 * real dialog breakages have reached the user precisely because nothing
 * constructed them for real. Those tests stay in PixInsight.
 */
for ( const name of [ "Dialog", "Control", "Sizer", "HorizontalSizer",
                      "VerticalSizer", "Label", "Edit", "PushButton",
                      "CheckBox", "ComboBox", "TreeBox", "NumericControl",
                      "SpinBox", "Frame", "GroupBox", "ToolButton",
                      "MessageBox", "Timer", "Bitmap", "Point", "Rect" ] )
   global[name] = function () { notShimmed( name ); };

global.TextAlign_Right = 2; global.TextAlign_VertCenter = 128;
global.TextAlign_Left = 1; global.TextAlign_Center = 4;
global.FrameStyle_Sunken = 1; global.FrameStyle_Box = 2;
global.StdButton_Ok = 1; global.StdButton_Cancel = 2; global.StdButton_Yes = 3;
global.StdButton_No = 4; global.StdIcon_Error = 1; global.StdIcon_Warning = 2;
global.StdIcon_Information = 3; global.StdIcon_Question = 4;
global.DataType_Boolean = 1; global.DataType_String = 2; global.DataType_Double = 3;
global.UndoFlag_NoSwapFile = 1;

module.exports = { notShimmed };
