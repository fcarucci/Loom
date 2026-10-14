/*
 * Steps, continued: colour profiles. Which ICC profile each plate is
 * tagged with, the installed profiles that decides it from, the session
 * state of what was assigned or found missing, and the raw profile bytes
 * the PSB and the fly-through embed.
 *
 * Not a namespace of its own: it adds to `Steps`, so it is included after
 * Steps.js and StepsSyqon.js. The section text is the one Steps.js
 * carried, unchanged.
 */

/* ---------------------------------------------------------------------------
 * Colour profiles.
 *
 * Loom's colour composites come out of PixInsight already tagged with the
 * RGB working space -- ProPhoto here -- but the MONO plates carried no
 * profile at all, so L_starless and the L star plates opened untagged and
 * Photoshop guessed at them. A guessed gamma against ProPhoto-tagged
 * composites is a tone mismatch the moment they are combined.
 *
 * Grayscale cannot carry an RGB profile, so the mono plates get the
 * grayscale profile with ProPhoto's gamma (1.8) instead: same tone
 * response, correctly typed.
 *
 * MEASURED, because the API is not what it looks like: AssignICCProfile's
 * `mode` must be 0 to use `targetProfile`. Its DEFAULT is 1, which assigns
 * the application's default profile -- sRGB -- while still returning true.
 * The name must be the profile's full description; "ROMM RGB" and
 * "ProPhoto RGB" both silently fall back to sRGB. Verified 2026-09-17 by
 * writing all nine mode/name combinations and reading back what was
 * embedded.
 * ------------------------------------------------------------------------ */

Steps.ASSIGN_NEW_PROFILE = 0;
Steps.PROFILE_RGB  = "ROMM RGB: ISO 22028-2:2013";   // ProPhoto
Steps.PROFILE_GRAY = "Generic Gray Profile";         // gamma 1.8

/*
 * Not every installation HAS those two. ROMM RGB and Generic Gray are
 * macOS system profiles: a Windows PixInsight reported "Couldn't find the
 * 'ROMM RGB: ISO 22028-2:2013' profile" and every plate went out untagged.
 *
 * So the profiles actually installed are read -- PJSR has no ICCProfile
 * object to ask (probed 2026-09-23), so the ICC files themselves are
 * parsed -- and the closest standard working space is used instead,
 * widest gamut first. Only these named standards qualify: a folder of
 * profiles also holds the user's monitor calibrations, which are RGB
 * display profiles too and must never tag an image. Linear variants are
 * excluded because the plates are stretched.
 *
 * AssignICCProfile with an unknown name returns false without throwing
 * (probed), so the candidates can simply be tried in order.
 */
Steps.PROFILE_SRGB = "sRGB IEC61966-2.1";

Steps.RGB_PROFILE_PREFERENCE = [
   { pattern: /^ROMM RGB/i,                                  gamma: 1.8 },
   { pattern: /ProPhoto/i,                                   gamma: 1.8 },
   { pattern: /BT\.?\s?2020|Rec\.?\s?2020/i,                 gamma: 2.2 },
   { pattern: /Wide\s?Gamut RGB/i,                           gamma: 2.2 },
   { pattern: /^Adobe RGB \(1998\)|Compatible with Adobe RGB/i, gamma: 2.2 },
   { pattern: /^Display P3|DCI.{0,3}P3/i,                    gamma: 2.2 },
   { pattern: /^sRGB/i,                                      gamma: 2.2 } ];

Steps.GRAY_PROFILE_PREFERENCE = {
   "1.8": [ /^Generic Gray Profile$/i, /Gray Gamma 1\.8/i ],
   "2.2": [ /Gray Gamma 2\.2/i, /^sGray/i ] };

/* Profile classes that describe a colour space an image can be tagged with. */
Steps.ICC_USABLE_CLASSES = [ "mntr", "spac" ];

/*
 * The class, colour space and English description of an ICC profile, or
 * null when the bytes are not one. `byteAt( i )` returns byte i, so the
 * same code reads a PJSR ByteArray and a plain array in the node suite.
 * Handles the v2 'desc' tag type and the v4 'mluc' one.
 */
Steps.parseIccProfile = function( byteAt, length )
{
   function u32( o ) { return ( ( byteAt( o ) << 24 ) >>> 0 ) + ( byteAt( o + 1 ) << 16 ) + ( byteAt( o + 2 ) << 8 ) + byteAt( o + 3 ); }
   function str4( o ) { return String.fromCharCode( byteAt( o ), byteAt( o + 1 ), byteAt( o + 2 ), byteAt( o + 3 ) ); }
   if ( length < 132 )
      return null;
   var tags = u32( 128 );
   for ( var t = 0; t < tags && 144 + 12*t <= length; ++t )
   {
      var at = 132 + 12*t;
      if ( str4( at ) != "desc" )
         continue;
      var d = Steps.iccDescription( byteAt, u32( at + 4 ), length, u32, str4 );
      if ( d == null )
         return null;
      return { deviceClass: str4( 12 ), colorSpace: str4( 16 ).trim(), description: d };
   }
   return null;
};

Steps.iccDescription = function( byteAt, off, length, u32, str4 )
{
   var type = str4( off ), out = "", i;
   if ( type == "desc" )
   {
      var n = u32( off + 8 );
      for ( i = 0; i < n && off + 12 + i < length; ++i )
      {
         var c = byteAt( off + 12 + i );
         if ( c == 0 ) break;
         out += String.fromCharCode( c );
      }
      return out;
   }
   if ( type != "mluc" )
      return null;
   var records = u32( off + 8 ), size = u32( off + 12 ), pick = off + 16;
   for ( var r = 0; r < records; ++r )
      if ( String.fromCharCode( byteAt( off + 16 + r*size ), byteAt( off + 17 + r*size ) ) == "en" )
      {
         pick = off + 16 + r*size;
         break;
      }
   var len = u32( pick + 4 ), start = off + u32( pick + 8 );
   for ( i = 0; i + 1 < len && start + i + 1 < length; i += 2 )
      out += String.fromCharCode( ( byteAt( start + i ) << 8 ) + byteAt( start + i + 1 ) );
   return out;
};

/*
 * Where PixInsight finds profiles. `systemRoot` is %SystemRoot% on Windows
 * (C:\Windows unless the system was installed elsewhere). Adobe's folders
 * are NOT searched: PixInsight does not load from them, and every name it
 * cannot find prints "Couldn't find the ... profile" (a Windows user's log
 * had ProPhoto, Wide Gamut and Adobe RGB from there failing on every
 * plate). The PSB's own profile file is looked for separately
 * (Steps.PSB_PROFILE_FILES).
 */
Steps.iccProfileDirectories = function( platform, home, systemRoot )
{
   var hasHome = ( home != null && String( home ).length > 0 );
   if ( Util.isWindows( platform ) )
   {
      var root = String( systemRoot || "C:/Windows" ).replace( /\\/g, "/" ).replace( /\/+$/, "" );
      return [ root + "/System32/spool/drivers/color" ];
   }
   if ( Util.platform( platform ) == Util.PLATFORM_MACOS )
   {
      var mac = [ "/System/Library/ColorSync/Profiles", "/Library/ColorSync/Profiles" ];
      if ( hasHome ) mac.push( home + "/Library/ColorSync/Profiles" );
      return mac;
   }
   var unix = [ "/usr/share/color/icc", "/usr/local/share/color/icc" ];
   if ( hasHome ) unix.push( home + "/.color/icc", home + "/.local/share/icc" );
   return unix;
};

Steps.ICC_MAX_FILE_BYTES = 2*1024*1024;   // standard spaces are a few KB; printer LUTs are not candidates

/* Every readable profile in the platform's profile folders. Read once per session. */
Steps.installedIccProfiles = function()
{
   if ( Steps.installedIccCache )
      return Steps.installedIccCache;
   var home = "", sysRoot = "";
   try { home = File.homeDirectory; } catch ( e ) {}
   try { sysRoot = System.getEnvironmentVariable( "SystemRoot" ) || ""; } catch ( e ) {}
   var found = [];
   Steps.iccProfileDirectories( Util.PLATFORM, home, sysRoot ).forEach( function( dir )
   {
      Util.directoryEntries( dir ).forEach( function( name )
      {
         if ( !/\.(icc|icm)$/i.test( name ) )
            return;
         var p = Steps.readIccProfileFile( dir + "/" + name );
         if ( p != null )
            found.push( p );
      } );
   } );
   Steps.installedIccCache = found;
   return found;
};

Steps.readIccProfileFile = function( path )
{
   try
   {
      if ( ( new FileInfo( path ) ).size > Steps.ICC_MAX_FILE_BYTES )
         return null;
      var b = File.readFile( path );
      return Steps.parseIccProfile( Util.byteReader( b ), b.length );
   }
   catch ( e ) { return null; }
};

/* Descriptions of `profiles` in colour space `space` matching `patterns`, in pattern order. */
Steps.profilesMatching = function( profiles, space, patterns )
{
   var out = [];
   patterns.forEach( function( re )
   {
      profiles.forEach( function( p )
      {
         if ( p.colorSpace == space && Steps.ICC_USABLE_CLASSES.indexOf( p.deviceClass ) >= 0 &&
              !/linear/i.test( p.description ) &&
              re.test( p.description ) && out.indexOf( p.description ) < 0 )
            out.push( p.description );
      } );
   } );
   return out;
};

Steps.uniqueList = function( list )
{
   return list.filter( function( x, i ) { return list.indexOf( x ) == i; } );
};

/*
 * The names to try, best first, for colour and for mono plates. Only
 * profiles found installed are tried -- each name PixInsight cannot find
 * prints an error in the console -- except when nothing could be read at
 * all, when the preferred names are tried blind. The preferred names lead
 * where they exist, so nothing changes there; sRGB closes the RGB list
 * because PixInsight always has it. The gray list is
 * ordered by the gamma of the RGB space this machine will actually get,
 * so mono and colour plates keep the same tone response.
 */
Steps.profilePlan = function( profiles )
{
   var rgbPatterns = Steps.RGB_PROFILE_PREFERENCE.map( function( x ) { return x.pattern; } );
   var rgb = Steps.profilesMatching( profiles, "RGB", rgbPatterns );
   var gamma = 1.8;
   if ( rgb.length > 0 )
      for ( var i = 0; i < Steps.RGB_PROFILE_PREFERENCE.length; ++i )
         if ( Steps.RGB_PROFILE_PREFERENCE[i].pattern.test( rgb[0] ) )
         {
            gamma = Steps.RGB_PROFILE_PREFERENCE[i].gamma;
            break;
         }
   var order = ( gamma == 1.8 ) ? [ "1.8", "2.2" ] : [ "2.2", "1.8" ];
   var grayPatterns = Steps.GRAY_PROFILE_PREFERENCE[order[0]].concat( Steps.GRAY_PROFILE_PREFERENCE[order[1]] );
   var gray = Steps.profilesMatching( profiles, "GRAY", grayPatterns );
   var blind = ( profiles.length == 0 );
   var has = function( name ) { return blind || profiles.some( function( p ) { return p.description == name; } ); };
   var preferredGray = has( Steps.PROFILE_GRAY ) ? [ Steps.PROFILE_GRAY ] : [];
   var grayList = ( gamma == 1.8 ) ? preferredGray.concat( gray ) : gray.concat( preferredGray );
   return { rgb: Steps.uniqueList( ( has( Steps.PROFILE_RGB ) ? [ Steps.PROFILE_RGB ] : [] ).concat( rgb, [ Steps.PROFILE_SRGB ] ) ),
            gray: Steps.uniqueList( grayList ) };
};

Steps.currentProfilePlan = function()
{
   if ( !Steps.profilePlanCache )
      Steps.profilePlanCache = Steps.profilePlan( Steps.installedIccProfiles() );
   return Steps.profilePlanCache;
};

/* The RGB profile the colour plates were given this session, or null. */
Steps.rgbProfileInUse = null;
Steps.lastAssignedProfile = null;

Steps.tryAssignProfile = function( window, name )
{
   try
   {
      var P = new AssignICCProfile;
      P.mode = Steps.ASSIGN_NEW_PROFILE;
      P.targetProfile = name;
      return P.executeOn( window.mainView ) === true;
   }
   catch ( e ) { return false; }
};

/* Names PixInsight could not find this session: each try prints an error, so one is enough. */
Steps.missingProfiles = {};

/* `plan` is for the suite; a run uses the installed profiles. */
Steps.assignProfile = function( window, label, plan )
{
   plan = plan || Steps.currentProfilePlan();
   var isColor = window.mainView.image.numberOfChannels >= 3;
   var names = ( isColor ? plan.rgb : plan.gray ).filter( function( n ) { return !Steps.missingProfiles[n]; } );
   var who = label || window.mainView.id;
   for ( var i = 0; i < names.length; ++i )
   {
      if ( !Steps.tryAssignProfile( window, names[i] ) )
      {
         Steps.missingProfiles[names[i]] = true;
         continue;
      }
      Steps.lastAssignedProfile = names[i];
      if ( isColor )
         Steps.rgbProfileInUse = names[i];
      Util.log( "icc", who + " -> " + names[i] +
                ( i > 0 ? " (" + names.slice( 0, i ).join( ", " ) + " not installed; closest available)" : "" ) );
      return true;
   }
   Util.warn( "icc", who + ": no suitable profile is installed (tried " + names.join( ", " ) +
                     "); left untagged" );
   return false;
};

/*
 * The raw bytes of a profile, for embedding somewhere PixInsight will not
 * do it for us -- the PSB.
 *
 * There is no PJSR call that hands over a profile by name, so this takes
 * the long way round: tag a tiny throwaway image, save it, and read the
 * profile back out of the file. The image is 8x8, so the round trip costs
 * nothing measurable.
 */
/*
 * Profile FILES to prefer for the PSB, in order.
 *
 * ROMM RGB and ProPhoto RGB are the same colour space -- identical
 * primaries, D50 white point, gamma 1.8, verified by reading both ICCs --
 * but they carry different DESCRIPTIONS, and Photoshop matches its working
 * space by description. A document tagged "ROMM RGB: ISO 22028-2:2013"
 * against a "ProPhoto RGB" working space therefore opens with a profile
 * mismatch, even though nothing about the pixels differs.
 *
 * PixInsight cannot assign Adobe's profile -- it does not know the name,
 * and falls back to sRGB when given it -- but the PSB is written here, so
 * its bytes can be taken straight from the file.
 */
Steps.PROFILE_RGB_FILES = [
   // macOS, installed by any Adobe application
   "/Library/Application Support/Adobe/Color/Profiles/Recommended/ProPhoto.icm",
   "/Library/Application Support/Adobe/Color/Profiles/ProPhoto.icm",
   // Windows
   "C:/Program Files/Common Files/Adobe/Color/Profiles/Recommended/ProPhoto.icm",
   "C:/Windows/System32/spool/drivers/color/ProPhoto.icm"
];

/*
 * The profile to embed in the PSB: Adobe's ProPhoto if it is installed,
 * otherwise whatever PixInsight will give us for ROMM. The fallback is the
 * same colour space and is correct -- it just makes Photoshop ask.
 */
Steps.psbProfileBytes = function()
{
   /*
    * The PSB must carry the profile the plates carry. Where ROMM was not
    * installed and another space was used, Adobe's ProPhoto would
    * describe the same pixels differently.
    */
   if ( Steps.rgbProfileInUse != null && !Steps.isProPhotoFamily( Steps.rgbProfileInUse ) )
      return Steps.iccProfileBytes( Steps.rgbProfileInUse );
   for ( var i = 0; i < Steps.PROFILE_RGB_FILES.length; ++i )
   {
      var p = Steps.PROFILE_RGB_FILES[i];
      try
      {
         if ( !File.exists( p ) )
            continue;
         var b = File.readFile( p );
         if ( b != null && b.length > 0 )
         {
            Util.log( "icc", "PSB profile from " + p + " (" + b.length + " bytes)" );
            return b;
         }
      }
      catch ( e ) { /* try the next one */ }
   }
   Util.warn( "icc", "Adobe's ProPhoto profile was not found; the PSB will " +
                     "carry " + Steps.PROFILE_RGB + ", which is the same " +
                     "colour space under a different name -- Photoshop will " +
                     "report a profile mismatch on open" );
   return Steps.iccProfileBytes( Steps.PROFILE_RGB );
};

Steps.isProPhotoFamily = function( name )
{
   return /^ROMM RGB|ProPhoto/i.test( String( name ) );
};

Steps.iccProfileBytes = function( profileName )
{
   var tmp = File.systemTempDirectory + "/loom-icc-" +
             Steps.syqonSanitizeFileName( profileName ) + ".tif";
   var w = new ImageWindow( 8, 8, 3, 16, false, true,
                            Util.freeWindowId( "loom_icc_probe" ) );
   try
   {
      w.mainView.beginProcess( UndoFlag_NoSwapFile );
      w.mainView.image.fill( 0.5 );
      w.mainView.endProcess();

      var P = new AssignICCProfile;
      P.mode = Steps.ASSIGN_NEW_PROFILE;
      P.targetProfile = profileName;
      if ( !P.executeOn( w.mainView ) )
         throw new Error( "could not assign " + profileName );

      try { if ( File.exists( tmp ) ) File.remove( tmp ); } catch ( e0 ) {}
      w.saveAs( tmp, false, false, false, false );
   }
   finally { try { w.forceClose(); } catch ( e1 ) {} }

   var bytes = null;
   try
   {
      var F = new FileFormat( ".tif", true, false );
      var f = new FileFormatInstance( F );
      f.open( tmp, "verbosity 0" );
      bytes = f.iccProfile;
      f.close();
   }
   finally { try { if ( File.exists( tmp ) ) File.remove( tmp ); } catch ( e2 ) {} }

   if ( bytes == null || bytes.length == 0 )
      throw new Error( "no profile came back for " + profileName );
   return bytes;
};
