/*
 * Recorder for the ICC library functions of the Loom main set (lib/StepsIcc.js):
 * profile parsing (v2 `desc` and v4 `mluc` tags), the profile plan, the profile
 * folders per platform, and the installed-profile scan over a fake file tree.
 * Every profile here is built from synthetic bytes by the helpers below and
 * recorded as hex, so the Python replay needs no node and no real profile.
 */
"use strict";

module.exports = function( js )
{
   const { Util, Steps } = js;

   const hex = a => a.map( b => ( "0" + ( b & 255 ).toString( 16 ) ).slice( -2 ) ).join( "" );
   const ascii = s => Array.from( s, c => c.charCodeAt( 0 ) );
   const u32 = n => [ ( n >>> 24 ) & 255, ( n >>> 16 ) & 255, ( n >>> 8 ) & 255, n & 255 ];
   const utf16 = s =>
   {
      const out = [];
      for ( let i = 0; i < s.length; ++i ) out.push( s.charCodeAt( i ) >> 8, s.charCodeAt( i ) & 255 );
      return out;
   };

   // A v2 'desc' tag holding the given raw bytes (the count includes the terminator).
   const descRaw = bytes => ascii( "desc" ).concat( [ 0, 0, 0, 0 ], u32( bytes.length ), bytes );
   const desc = text => descRaw( ascii( text ).concat( [ 0 ] ) );

   // A v4 'mluc' tag: records of [ lang, text ]; string data follows the record directory.
   function mluc( records )
   {
      let off = 16 + 12 * records.length;
      const dir = [], data = [];
      records.forEach( r =>
      {
         const s = utf16( r.text );
         dir.push( ...ascii( r.lang ), 0, 0, ...u32( s.length ), ...u32( off ) );
         data.push( ...s );
         off += s.length;
      } );
      return ascii( "mluc" ).concat( [ 0, 0, 0, 0 ], u32( records.length ), u32( 12 ), dir, data );
   }

   // A profile: 128-byte header (size, version, class at 12, colour space at 16), tag count, tag table, tag data.
   function profile( o )
   {
      const tableLen = 132 + 12 * o.tags.length;
      let off = tableLen;
      const table = [], body = [];
      o.tags.forEach( t =>
      {
         table.push( ...ascii( t.sig ), ...u32( off ), ...u32( t.data.length ) );
         body.push( ...t.data );
         off += t.data.length;
      } );
      const header = new Array( 128 ).fill( 0 );
      header.splice( 0, 4, ...u32( tableLen + body.length ) );
      header.splice( 8, 4, o.version || 2, 0, 0, 0 );
      header.splice( 12, 4, ...ascii( ( o.cls || "mntr" ).padEnd( 4, " " ) ) );
      header.splice( 16, 4, ...ascii( ( o.space || "RGB" ).padEnd( 4, " " ) ) );
      header.splice( 20, 4, ...ascii( "Lab " ) );
      return header.concat( u32( o.tags.length ), table, body );
   }

   const parse = bytes => Steps.parseIccProfile( i => bytes[i], bytes.length );
   const out = {};

   // parse / description cases
   const v2 = profile( { tags: [ { sig: "desc", data: desc( "sRGB IEC61966-2.1" ) } ] } );
   out.parse_v2_srgb = { hex: hex( v2 ), out: parse( v2 ) };

   const scnr = profile( { cls: "scnr", tags: [ { sig: "desc", data: desc( "Wide Gamut RGB" ) } ] } );
   out.parse_wrong_class_kept_by_parse = { hex: hex( scnr ), out: parse( scnr ) };

   const v4en = profile( { version: 4, tags: [ { sig: "desc", data: mluc( [ { lang: "de", text: "Deutsch" }, { lang: "en", text: "Display P3" } ] ) } ] } );
   out.parse_v4_mluc_picks_en = { hex: hex( v4en ), out: parse( v4en ) };

   const v4first = profile( { version: 4, tags: [ { sig: "desc", data: mluc( [ { lang: "fr", text: "Premier" }, { lang: "de", text: "Zweiter" } ] ) } ] } );
   out.parse_v4_mluc_no_en_takes_first = { hex: hex( v4first ), out: parse( v4first ) };

   const v4uni = profile( { version: 4, tags: [ { sig: "desc", data: mluc( [ { lang: "en", text: "Adobe RGB (1998) \u00e9" } ] ) } ] } );
   out.parse_v4_mluc_non_ascii = { hex: hex( v4uni ), out: parse( v4uni ) };

   const nul = profile( { tags: [ { sig: "desc", data: descRaw( ascii( "sRGB" ).concat( [ 0 ], ascii( "junk" ), [ 0 ] ) ) } ] } );
   out.parse_v2_stops_at_nul = { hex: hex( nul ), out: parse( nul ) };

   out.parse_too_short_131 = { hex: hex( v2.slice( 0, 131 ) ), out: parse( v2.slice( 0, 131 ) ) };
   const zeros132 = new Array( 132 ).fill( 0 );
   out.parse_no_tags_132 = { hex: hex( zeros132 ), out: parse( zeros132 ) };

   const cprt = profile( { tags: [ { sig: "cprt", data: desc( "Copyright" ) } ] } );
   out.parse_no_desc_tag = { hex: hex( cprt ), out: parse( cprt ) };

   const badType = profile( { tags: [ { sig: "desc", data: ascii( "text" ).concat( [ 0, 0, 0, 0 ], [ 1, 2, 3, 4 ] ) } ] } );
   out.parse_unknown_desc_type = { hex: hex( badType ), out: parse( badType ) };

   const cut = v2.slice( 0, v2.length - 3 );
   out.parse_truncated_desc = { hex: hex( cut ), out: parse( cut ) };

   // profile plan cases: lists of installed profiles
   const pf = ( description, deviceClass, colorSpace ) => ( { description, deviceClass, colorSpace } );
   const ROMM = "ROMM RGB: ISO 22028-2:2013";
   const plans = {
      plan_none_installed: [],
      plan_macos_standard: [ pf( ROMM, "mntr", "RGB" ), pf( "Generic Gray Profile", "mntr", "GRAY" ),
                             pf( "sRGB IEC61966-2.1", "mntr", "RGB" ), pf( "Display P3", "mntr", "RGB" ),
                             pf( "Adobe RGB (1998)", "mntr", "RGB" ), pf( "Dell U2415", "mntr", "RGB" ),
                             pf( "Gray Gamma 2.2", "mntr", "GRAY" ) ],
      plan_windows_prophoto: [ pf( "ProPhoto RGB", "mntr", "RGB" ), pf( "sRGB IEC61966-2.1", "mntr", "RGB" ),
                               pf( "Gray Gamma 1.8", "mntr", "GRAY" ) ],
      plan_rec2020_sets_gamma22: [ pf( "Rec. 2020 RGB", "mntr", "RGB" ), pf( "sRGB IEC61966-2.1", "mntr", "RGB" ),
                                   pf( "Gray Gamma 1.8", "mntr", "GRAY" ), pf( "sGray", "spac", "GRAY" ) ],
      plan_linear_and_wrong_class: [ pf( "ProPhoto RGB Linear", "mntr", "RGB" ), pf( "Wide Gamut RGB", "scnr", "RGB" ),
                                     pf( "sGray", "scnr", "GRAY" ), pf( "Generic Gray Profile", "mntr", "GRAY" ) ],
      plan_gray_only: [ pf( "Gray Gamma 2.2", "mntr", "GRAY" ), pf( "sGray", "spac", "GRAY" ) ],
      plan_romm_prophoto_duplicate: [ pf( ROMM, "mntr", "RGB" ), pf( "ProPhoto RGB", "mntr", "RGB" ),
                                      pf( ROMM, "mntr", "RGB" ), pf( "Generic Gray Profile", "mntr", "GRAY" ) ],
      plan_only_monitor_profiles: [ pf( "Dell U2415", "mntr", "RGB" ), pf( "Dell U2415 gray", "mntr", "GRAY" ) ]
   };
   Object.keys( plans ).forEach( name =>
   {
      const plan = Steps.profilePlan( plans[name] );
      out[name] = { installed: plans[name], rgb: plan.rgb, gray: plan.gray };
   } );

   // profile folders per platform
   const dirCases = {
      dirs_windows_default: [ "windows", "", "" ],
      dirs_windows_backslash_root: [ "windows", "C:\\Users\\x", "D:\\WINNT\\" ],
      dirs_macos_with_home: [ "macos", "/Users/a", "" ],
      dirs_macos_no_home: [ "macos", null, "" ],
      dirs_unix_with_home: [ "unix", "/opt/tester", "" ],
      dirs_unix_no_home: [ "unix", "", "" ]
   };
   Object.keys( dirCases ).forEach( name =>
   {
      const [ platform, home, sysRoot ] = dirCases[name];
      out[name] = { platform, home, systemRoot: sysRoot, out: Steps.iccProfileDirectories( platform, home, sysRoot ) };
   } );

   // installed-profile scan over a fake tree (fake the globals the scan reads, then restore them)
   function scan( platform, home, sysRoot, tree )
   {
      const saved = { plat: Util.PLATFORM, entries: Util.directoryEntries, File: global.File,
                      System: global.System, FileInfo: global.FileInfo, cache: Steps.installedIccCache };
      Util.PLATFORM = platform;
      Util.directoryEntries = root => ( tree.dirs[root] || [] ).filter( n => n.charAt( 0 ) != "." );
      global.File = { homeDirectory: home, readFile: p => tree.files[p] };
      global.System = { getEnvironmentVariable: n => ( n == "SystemRoot" ? sysRoot : undefined ) };
      global.FileInfo = function( p ) { this.size = ( tree.sizes && tree.sizes[p] != null ) ? tree.sizes[p] : tree.files[p].length; };
      Steps.installedIccCache = undefined;
      try { return Steps.installedIccProfiles().map( p => ( { deviceClass: p.deviceClass, colorSpace: p.colorSpace, description: p.description } ) ); }
      finally
      {
         Util.PLATFORM = saved.plat;
         Util.directoryEntries = saved.entries;
         global.File = saved.File;
         global.System = saved.System;
         global.FileInfo = saved.FileInfo;
         Steps.installedIccCache = saved.cache;
      }
   }

   const recordTree = ( platform, home, sysRoot, tree ) =>
   {
      const dirs = Steps.iccProfileDirectories( platform, home, sysRoot );
      const files = {};
      Object.keys( tree.files ).forEach( p => files[p] = hex( tree.files[p] ) );
      return { platform, home, systemRoot: sysRoot, dirs, dirEntries: tree.dirs, files,
               sizes: tree.sizes || {}, out: scan( platform, home, sysRoot, tree ) };
   };

   const monoProfile = ( desc_, cls, space ) => profile( { cls, space, tags: [ { sig: "desc", data: desc( desc_ ) } ] } );
   const macTree = { dirs: {}, files: {}, sizes: {} };
   macTree.dirs[ "/System/Library/ColorSync/Profiles" ] = [ "Generic Gray.icc", "readme.txt", ".DS_Store", "ROMM.icm" ];
   macTree.files[ "/System/Library/ColorSync/Profiles/Generic Gray.icc" ] = monoProfile( "Generic Gray Profile", "mntr", "GRAY" );
   macTree.files[ "/System/Library/ColorSync/Profiles/readme.txt" ] = ascii( "not a profile at all, no tags" );
   macTree.files[ "/System/Library/ColorSync/Profiles/ROMM.icm" ] = profile( { version: 4, tags: [ { sig: "desc", data: mluc( [ { lang: "en", text: ROMM } ] ) } ] } );
   macTree.dirs[ "/Library/ColorSync/Profiles" ] = [ "sRGB.icc", "huge.icc", "Broken.icc", "Wide.icc" ];
   macTree.files[ "/Library/ColorSync/Profiles/sRGB.icc" ] = monoProfile( "sRGB IEC61966-2.1", "mntr", "RGB" );
   macTree.files[ "/Library/ColorSync/Profiles/huge.icc" ] = monoProfile( "Display P3", "mntr", "RGB" );
   macTree.sizes[ "/Library/ColorSync/Profiles/huge.icc" ] = 2 * 1024 * 1024 + 1;
   macTree.files[ "/Library/ColorSync/Profiles/Broken.icc" ] = [ 1, 2, 3, 4, 5 ];
   macTree.files[ "/Library/ColorSync/Profiles/Wide.icc" ] = profile( { cls: "scnr", tags: [ { sig: "desc", data: desc( "Wide Gamut RGB" ) } ] } );
   macTree.dirs[ "/Users/tester/Library/ColorSync/Profiles" ] = [ "Monitor.icc", "Display P3.icc", "Adobe.icc" ];
   macTree.files[ "/Users/tester/Library/ColorSync/Profiles/Monitor.icc" ] = monoProfile( "Dell U2415", "mntr", "RGB" );
   macTree.files[ "/Users/tester/Library/ColorSync/Profiles/Display P3.icc" ] = monoProfile( "Display P3", "mntr", "RGB" );
   macTree.files[ "/Users/tester/Library/ColorSync/Profiles/Adobe.icc" ] = profile( { version: 4, tags: [ { sig: "desc", data: mluc( [ { lang: "en", text: "Adobe RGB (1998)" } ] ) } ] } );
   out.installed_macos = recordTree( "macos", "/Users/tester", "", macTree );

   const winTree = { dirs: {}, files: {}, sizes: {} };
   const winDir = "D:/WINNT/System32/spool/drivers/color";
   winTree.dirs[ winDir ] = [ "ProPhoto.icm", "sRGB Color Space Profile.icm", "Linear.icc" ];
   winTree.files[ winDir + "/ProPhoto.icm" ] = monoProfile( "ProPhoto RGB", "mntr", "RGB" );
   winTree.files[ winDir + "/sRGB Color Space Profile.icm" ] = monoProfile( "sRGB IEC61966-2.1", "mntr", "RGB" );
   winTree.files[ winDir + "/Linear.icc" ] = monoProfile( "ProPhoto RGB Linear", "mntr", "RGB" );
   out.installed_windows = recordTree( "windows", "", "D:\\WINNT", winTree );

   const unixTree = { dirs: {}, files: {}, sizes: {} };
   unixTree.dirs[ "/opt/tester/.color/icc" ] = [ "Gray Gamma 2.2.icc", "sGray.icc" ];
   unixTree.files[ "/opt/tester/.color/icc/Gray Gamma 2.2.icc" ] = monoProfile( "Gray Gamma 2.2", "mntr", "GRAY" );
   unixTree.files[ "/opt/tester/.color/icc/sGray.icc" ] = monoProfile( "sGray", "spac", "GRAY" );
   out.installed_unix = recordTree( "unix", "/opt/tester", "", unixTree );

   return out;
};
