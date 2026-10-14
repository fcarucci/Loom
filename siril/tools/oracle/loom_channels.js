/*
 * Parity recorder for loomsiril/loom/channels.py and xisf.py: channel and
 * filter rules, selection checks, the saved-list text format, drizzle and
 * ProcessingHistory helpers, camera -> QE curve names, palettes, master
 * ranking, project names and the palette-skipping reports.
 *
 * Each case is { fn, args, result }: the Python port is called as
 * fn(*args) and must return result. Inputs are cloned before a call that
 * mutates them, so the stored args are the inputs Loom saw.
 */
"use strict";

module.exports = function( js )
{
   const { Util, Pipeline } = js;
   const cases = {};
   const add = ( name, fn, args, result ) => { cases[name] = { fn: fn, args: args, result: result }; };
   const clone = v => JSON.parse( JSON.stringify( v ) );

   // Collects the message of every Util.log / Util.warn call made by fn().
   function capture( key, fn )
   {
      const saved = Util[key];
      const got = [];
      Util[key] = function( stage, message ) { got.push( message ); };
      try { fn(); }
      finally { Util[key] = saved; }
      return got;
   }

   // ---- constants ----------------------------------------------------------
   add( "constant/CHANNELS", "CHANNELS", [], Util.CHANNELS );
   add( "constant/BROADBAND", "BROADBAND", [], Util.BROADBAND );
   add( "constant/RGB_GROUP", "RGB_GROUP", [], Util.RGB_GROUP );
   add( "constant/NARROWBAND", "NARROWBAND", [], Util.NARROWBAND );
   add( "constant/NARROWBAND_NM", "NARROWBAND_NM", [], Util.NARROWBAND_NM );
   add( "constant/PALETTES", "PALETTES", [], Util.PALETTES );
   add( "constant/FILTER_LETTERS", "FILTER_LETTERS", [], Util.FILTER_LETTERS );
   add( "constant/FILTER_NAMES", "FILTER_NAMES", [], Util.FILTER_NAMES );
   add( "constant/NATIVE_PIXEL_SIZE", "NATIVE_PIXEL_SIZE", [], Util.NATIVE_PIXEL_SIZE );
   add( "constant/GENERIC_FOLDERS", "GENERIC_FOLDERS", [], Pipeline.GENERIC_FOLDERS );
   for ( const k of Util.CHANNELS.concat( [ "X", "" ] ) )
      add( "is_broadband/" + k, "is_broadband", [ k ], Util.isBroadband( k ) );

   // ---- keyword values -----------------------------------------------------
   const kw = ( n, v ) => ( { name: n, value: v } );
   const keywordCases = [
      [ [ kw( "FILTER", "'Ha      '" ) ], "FILTER" ],
      [ [ kw( "FILTER", "'L'" ) ], "FILTER" ],
      [ [ kw( "FILTER", "   'OIII'   " ) ], "FILTER" ],
      [ [ kw( "FILTER", "''" ) ], "FILTER" ],
      [ [ kw( "FILTER", "'      '" ) ], "FILTER" ],
      [ [ kw( "XPIXSZ", "3.76" ) ], "XPIXSZ" ],
      [ [ kw( "XPIXSZ", "'3.76'" ) ], "XPIXSZ" ],
      [ [ kw( "FILTER", "'R'" ), kw( "FILTER", "'G'" ) ], "FILTER" ],
      [ [ kw( "INSTRUME", "'ZWO ASI2600MM'" ) ], "FILTER" ],
      [ [], "FILTER" ]
   ];
   keywordCases.forEach( ( c, i ) =>
      add( "keyword_value/" + i, "keyword_value", [ c[0], c[1] ], Util.keywordValue( c[0], c[1] ) ) );

   // ---- filter -> channel --------------------------------------------------
   const FILTERS = [ "L", "l", "L ", "  l  ", "R", "G", "B", "H", "S", "O", "h", "o",
                     "Ha", "ha", "HA", "Ha ", "H-alpha", "H alpha", "Halpha", "H_alpha", "HAlpha", "Hα",
                     "S2", "SII", "S-II", "sii", "SII 3nm", "OIII", "O3", "O-III", "oiii", "OIII 3nm",
                     "Lum", "Luminance", "lum.", "Lum (Clear)", "Ha-Lum",
                     "Red", "RED", "red_filter", "Green", "G-Green", "Blue", "BLUE 1",
                     "L-Pro", "Dark", "Clear", "xyz", "Ha_6nm", "has2",
                     "Hydrogen Alpha", "Sulfur II", "Oxygen III", "", "   ", null ];
   FILTERS.forEach( ( f, i ) => add( "channel_from_filter/" + i, "channel_from_filter", [ f ],
                                     Util.channelFromFilter( f ) ) );

   // ---- selection checks ---------------------------------------------------
   const sel = [
      [ "nothing", {}, {} ],
      [ "nothing_views_empty", {}, { R: "", H: "" } ],
      [ "rgb_missing_b", { R: "/d/r.xisf", G: "/d/g.xisf" }, null ],
      [ "rgb_only_r", { R: "/d/r.xisf" }, null ],
      [ "rgb_full", { R: "/d/r.xisf", G: "/d/g.xisf", B: "/d/b.xisf" }, null ],
      [ "lrgb", { L: "/d/l.xisf", R: "/d/r.xisf", G: "/d/g.xisf", B: "/d/b.xisf" }, null ],
      [ "l_only", { L: "/d/l.xisf" }, null ],
      [ "nb_only_h", { H: "/d/h.xisf" }, null ],
      [ "nb_only_hso", { H: "/d/h.xisf", S: "/d/s.xisf", O: "/d/o.xisf" }, null ],
      [ "l_and_nb", { L: "/d/l.xisf", O: "/d/o.xisf" }, null ],
      [ "duplicate_rg", { R: "/d/x.xisf", G: "/d/x.xisf", B: "/d/b.xisf" }, null ],
      [ "duplicate_hso", { H: "/d/n.xisf", S: "/d/n.xisf", O: "/d/n.xisf" }, null ],
      [ "duplicate_and_missing", { R: "/x", G: "/x" }, null ],
      [ "empty_path_is_missing", { R: "", G: "/d/g.xisf", B: "/d/b.xisf" }, null ],
      [ "view_supplies_b", { R: "/d/r.xisf", G: "/d/g.xisf" }, { B: "view B" } ],
      [ "empty_view_is_missing", { R: "/d/r.xisf", G: "/d/g.xisf" }, { B: "" } ],
      [ "view_and_path_partial_rgb", { R: "/d/r.xisf" }, { G: "view G" } ],
      [ "nb_view_only", {}, { H: "HaView" } ],
      [ "view_null_is_missing", { H: "/d/h.xisf" }, { S: null } ],
      [ "views_undefined_nothing", {}, undefined ],
      [ "dup_windows_backslash_only", { H: "D:\\m\\n.xisf", S: "D:\\m\\n.xisf", O: "D:/m/other.xisf" }, null ]
   ];
   sel.forEach( ( c, i ) => {
      const paths = clone( c[1] ), views = c[2] === undefined ? undefined : clone( c[2] );
      const args = views === undefined ? [ paths ] : [ paths, views === null ? null : views ];
      add( "validate_selection/" + c[0], "validate_selection", args,
           Util.validateSelection( clone( c[1] ), c[2] === undefined ? undefined : clone( c[2] ) ) );
   } );
   add( "validate_selection/no_views_argument", "validate_selection", [ { R: "/d/r.xisf" } ],
        Util.validateSelection( { R: "/d/r.xisf" } ) );
   add( "channel_supplied/view_only", "channel_supplied", [ {}, { B: "view" }, "B" ],
        Util.channelSupplied( {}, { B: "view" }, "B" ) );
   add( "channel_supplied/none", "channel_supplied", [ {}, {}, "R" ], Util.channelSupplied( {}, {}, "R" ) );
   add( "channel_supplied/path", "channel_supplied", [ { R: "/r" }, {}, "R" ], Util.channelSupplied( { R: "/r" }, {}, "R" ) );

   // ---- saved selection list (file lines only are ported; view lines are dropped) ----
   const entriesA = [ { source: "file", ref: "/data/M42/master/masterLight_FILTER-L.xisf" },
                      { source: "file", ref: "/data/with space/x y.xisf" } ];
   const entriesB = [ { source: "file", ref: "/d/a.xisf" }, { source: "view", ref: "HaView" } ];
   add( "serialize_entries/files", "serialize_entries", [ clone( entriesA ) ], Util.serializeEntries( entriesA ) );
   add( "serialize_entries/mixed", "serialize_entries", [ clone( entriesB ) ], Util.serializeEntries( entriesB ) );
   add( "serialize_entries/empty", "serialize_entries", [ [] ], Util.serializeEntries( [] ) );
   add( "serialize_entries/single_file", "serialize_entries", [ [ { source: "file", ref: "/a.xisf" } ] ],
        Util.serializeEntries( [ { source: "file", ref: "/a.xisf" } ] ) );
   const texts = [ "", null, "file\t/a/b.xisf", "file\t/a/b.xisf\nfile\t/c d/e.xisf\n",
                   "file\t/a/b.xisf\nview\tHaView", "bogus\tx\nfile\t/z.xisf", "file\t",
                   "noTab", "file\t/a\tb", "\n\nfile\t/x.xisf\n", "file\t/a.xisf\r\n",
                   "view\tOnly", "file\t/a.xisf\nfile\t/a.xisf" ];
   texts.forEach( ( t, i ) => add( "deserialize_entries/" + i, "deserialize_entries", [ t ],
                                   Util.deserializeEntries( t ) ) );
   add( "deserialize_entries/round_trip_files", "deserialize_entries",
        [ Util.serializeEntries( entriesA ) ], Util.deserializeEntries( Util.serializeEntries( entriesA ) ) );

   // ---- drizzle helpers ----------------------------------------------------
   const factors = [ 1, 1.5, 2, 2.04, 2.06, 2.5, 3, 3.96, 4, 4.02, 0, -2, 1.9999, 2.0001, 10, 0.94, NaN, Infinity ];
   factors.forEach( ( f, i ) => add( "factor_label/" + i, "factor_label", [ f ], Util.factorLabel( f ) ) );
   const pix = [ "3.76", 3.76, "1.88", 1.88, "1.2533", 1.2533333333333333, "0.94", "2.5", "7.52", "5.64",
                 "", null, "abc", "0", "-1", "3.76 um", "NaN", "Infinity", "1.35" ];
   pix.forEach( ( p, i ) => add( "drizzle_label/" + i, "drizzle_label", [ p ], Util.drizzleLabel( p ) ) );
   const hist = [ "&lt;a&gt;", "&amp;lt;", "a &quot;b&quot;", null, "plain", "x&amp;y", "" ];
   hist.forEach( ( h, i ) => add( "unescape_history/" + i, "unescape_history", [ h ], Util.unescapeHistory( h ) ) );

   const DI = "<instance class=\"DrizzleIntegration\" id=\"x\">";
   const histories = [
      null,
      "",
      "<instance class=\"ImageIntegration\"><parameter id=\"scale\" value=\"2\"/></instance>",
      DI + "<parameter id=\"scale\" value=\"2\"/></instance>",
      "&lt;instance class=&quot;DrizzleIntegration&quot;&gt;&lt;parameter id=&quot;scale&quot; value=&quot;3&quot;/&gt;&lt;/instance&gt;",
      DI + "<parameter id=\"scale\" value=\"2\"/></instance><instance class=\"DrizzleIntegration\"><parameter id=\"scale\" value=\"4\"/></instance>",
      DI + "<parameter id=\"other\" value=\"9\"/></instance>",
      DI + "<parameter id=\"scale\" value=\"abc\"/></instance>",
      DI + "<parameter id=\"scale\" value=\"1.5\"/></instance>",
      "<instance class=\"ImageIntegration\"><parameter id=\"scale\" value=\"2\"/></instance>" + DI + "</instance>",
      DI + "<parameter id=\"scale\" value=\"2.5\"/></instance><instance class=\"ImageIntegration\"></instance>"
   ];
   histories.forEach( ( h, i ) => add( "drizzle_scale_from_history/" + i, "drizzle_scale_from_history", [ h ],
                                       Util.drizzleScaleFromHistory( h ) ) );

   const integ = [
      [ "scale2", histories[3], null, null ],
      [ "scale2_escaped", histories[4], "process=ImageIntegration", "1.88" ],
      [ "scale_not_clean", histories[8], null, "1.88" ],
      [ "scale_unreadable_sig_drizzle", histories[7], "process=DrizzleIntegration", "1.88" ],
      [ "sig_drizzle_no_xpixsz", null, "process=DrizzleIntegration v1", null ],
      [ "sig_drizzle_native_xpixsz", null, "process=DrizzleIntegration", "3.76" ],
      [ "sig_image_integration", null, "process=ImageIntegration", "1.88" ],
      [ "history_image_integration", "<instance class=\"ImageIntegration\"></instance>", null, "1.88" ],
      [ "no_metadata_xpixsz", null, null, "1.88" ],
      [ "no_metadata_no_xpixsz", null, null, null ],
      [ "no_metadata_native", null, "", "3.76" ],
      [ "scale_beats_image_sig", histories[3], "process=ImageIntegration", "3.76" ],
      [ "scale_2_5_with_xpixsz", histories[10], null, "1.88" ],
      [ "scale_nan_and_no_sig", histories[9], null, "1.88" ]
   ];
   integ.forEach( c => add( "integration_drizzle_label/" + c[0], "integration_drizzle_label",
                            [ c[1], c[2], c[3] ], Util.integrationDrizzleLabel( c[1], c[2], c[3] ) ) );

   const infos = [
      null,
      { keywords: [ kw( "XPIXSZ", "1.88" ) ] },
      { keywords: [ kw( "XPIXSZ", "'1.35'" ) ] },
      { keywords: [ kw( "XPIXSZ", "3.76" ) ], signature: "process=ImageIntegration" },
      { keywords: [], history: histories[3] },
      { keywords: [ kw( "XPIXSZ", "3.76" ) ], history: histories[3], signature: null },
      { keywords: [ kw( "FILTER", "'Ha'" ) ] }
   ];
   infos.forEach( ( inf, i ) => add( "master_drizzle_label/" + i, "master_drizzle_label", [ clone( inf ) ],
                                     Util.masterDrizzleLabel( inf ) ) );

   // ---- camera -> QE curve (Loom's PixInsight curve names) ----------------
   const cams = [ "ZWO ASI2600MM Pro", "ZWO ASI2600MM Air", "ASI 2600MM", "ASI2600MC", "ASI6200MM",
                  "ASI533MM Pro", "ASI094MM", "ASI1600MM", "ASI1600MM-Cool", "ASI183MM Pro", "ASI178MMC",
                  "ASI294MM Pro", "ASI585MC", "ASI585MM", "QHY268M", "QHY268C", "QHY600M", "IMX571",
                  "KAF-16803", "KAF-8300", "KAF-16200", "KAF-3200", "ICX694", "ASI071MC Pro", "Canon EOS R",
                  "", null, "Unknown Cam" ];
   cams.forEach( ( c, i ) => add( "qe_curve_name_for_camera/" + i, "qe_curve_name_for_camera", [ c ],
                                  Util.qeCurveNameForCamera( c ) ) );

   const common = [
      [ "same", [ "A", "A" ] ],
      [ "differ", [ "A", "B" ] ],
      [ "blanks_skipped", [ null, "ZWO ASI2600MM", "", "ZWO ASI2600MM" ] ],
      [ "one_blank", [ null, "" ] ],
      [ "empty", [] ],
      [ "one_value", [ "ZWO ASI183MM" ] ],
      [ "conflict_after_blank", [ "", "QHY268M", "ZWO ASI2600MM" ] ]
   ];
   common.forEach( c => add( "common_instrument/" + c[0], "common_instrument", [ c[1] ], Util.commonInstrument( c[1] ) ) );

   // ---- palettes -----------------------------------------------------------
   add( "palette_names", "palette_names", [], Util.paletteNames() );
   const subsets = [];
   for ( let m = 0; m < 8; ++m )
      subsets.push( [ "H", "S", "O" ].filter( ( _, b ) => ( m >> b ) & 1 ) );
   subsets.push( [ "L", "R" ], [ "H", "S", "O", "L" ] );
   Util.paletteNames().concat( [ "XYZ" ] ).forEach( pal => {
      add( "palette_wavelengths/" + pal, "palette_wavelengths", [ pal ], Util.paletteWavelengths( pal ) );
      add( "palette_channels/" + pal, "palette_channels", [ pal ], Util.paletteChannels( pal ) );
      subsets.forEach( s => add( "palette_missing/" + pal + "/" + s.join( "" ), "palette_missing",
                                 [ pal, s.slice() ], Util.paletteMissing( pal, s.slice() ) ) );
   } );

   // ---- master names and ranking -------------------------------------------
   const names = [
      "masterLight_BIN-1_6248x4176_EXPOSURE-60.00s_FILTER-L_mono_drizzle_2x_(1)_autocrop.xisf",
      "masterLight_BIN-1_6248x4176_EXPOSURE-180.00s_FILTER-O_mono_fastIntegration.xisf",
      "masterLight_BIN-1_6248x4176_EXPOSURE-60.00s_FILTER-B_mono_(2).xisf",
      "masterLight_BIN-1_6248x4176_EXPOSURE-300.00s_FILTER-Ha_mono_drizzle_3x.fits",
      "masterLight_FILTER-SII_drizzle_2x_AUTOCROP.xisf",
      "masterDark_BIN-1_EXPOSURE-60.00s.xisf",
      "masterFlat_FILTER-Ha_mono.xisf",
      "masterBias.xisf",
      "lightsOnly.xisf",
      "master_Ha.fits",
      "FILTER-Ha.xisf",
      "masterLight_FILTER-Lum_mono.xisf",
      "masterLight_FILTER-Red_mono_drizzle_2x.xisf",
      "masterLight_FILTER-Green.xisf",
      "masterLight_FILTER-Blue_drizzle_10x.xisf",
      "masterLight_FILTER-OIII_drizzle_1x.xisf",
      "masterLight_FILTER-XYZ.xisf",
      "",
      null,
      "autocrop_masterLight_FILTER-H.xisf",
      "masterLight_FILTER-S_(1)_AutoCrop.xisf"
   ];
   names.forEach( ( n, i ) => {
      add( "is_autocrop_name/" + i, "is_autocrop_name", [ n ], Util.isAutocropName( n ) );
      add( "parse_master_name/" + i, "parse_master_name", [ n ], Util.parseMasterName( n ) );
   } );
   const imagetyps = [ [ "masterLight_x.xisf", null ], [ "masterDark.xisf", null ], [ "x.xisf", "Master Light Frame" ],
                       [ "masterLight_x.xisf", "Master Dark Frame" ], [ "masterDark.xisf", "light" ],
                       [ "masterLight.xisf", "" ], [ "Light.xisf", null ], [ "masterFlat.xisf", "Flat" ],
                       [ "masterLight.xisf", "LIGHT" ] ];
   imagetyps.forEach( ( c, i ) => add( "is_master_light/" + i, "is_master_light", [ c[0], c[1] ],
                                       Util.isMasterLight( c[0], c[1] ) ) );
   [ [ "2x", true ], [ "2x", false ], [ "", true ], [ "", false ], [ null, true ], [ null, false ],
     [ "1x", false ], [ "10x", true ] ].forEach( ( c, i ) => add( "master_variant_rank/" + i, "master_variant_rank",
                                                                  [ c[0], c[1] ], Util.masterVariantRank( c[0], c[1] ) ) );

   const cand = ( ch, d, a, m, p ) => ( { channel: ch, drizzle: d, autocrop: a, mtime: m, path: p } );
   const selections = [
      [ "variant_beats_recency", () => [ cand( "L", "", false, 100, "plainNew" ), cand( "L", "2x", true, 50, "dAcOld" ),
                                          cand( "L", "2x", false, 200, "dNew" ) ] ],
      [ "recency_within_variant", () => [ cand( "H", "2x", false, 10, "older" ), cand( "H", "2x", false, 20, "newer" ) ] ],
      [ "tie_keeps_first", () => [ cand( "O", "", false, 5, "first" ), cand( "O", "", false, 5, "second" ) ] ],
      [ "autocrop_beats_plain", () => [ cand( "S", "", false, 999, "plain" ), cand( "S", "", true, 1, "auto" ) ] ],
      [ "drizzle_beats_autocrop", () => [ cand( "S", "", true, 999, "auto" ), cand( "S", "3x", false, 1, "drz" ) ] ],
      [ "channelless_and_null_ignored", () => [ null, { path: "x", mtime: 9 }, cand( "R", "", false, 1, "r1" ) ] ],
      [ "three_channels", () => [ cand( "R", "2x", false, 1, "r" ), cand( "G", "", false, 2, "g" ),
                                   cand( "B", "2x", true, 3, "b" ), cand( "B", "2x", true, 2, "bOld" ) ] ],
      [ "missing_mtime_is_zero", () => [ { channel: "L", drizzle: "", autocrop: false, path: "noTime" },
                                         { channel: "L", drizzle: "", autocrop: false, mtime: 1, path: "withTime" } ] ],
      [ "empty", () => [] ],
      [ "null_drizzle_plain", () => [ { channel: "G", drizzle: null, autocrop: null, mtime: 3, path: "a" } ] ]
   ];
   selections.forEach( ( s, i ) => {
      const input = s[1]();
      const args = [ clone( input ) ];
      const result = Util.selectMasters( input );
      add( "select_masters/" + s[0], "select_masters", args, result );
   } );

   // ---- project names ------------------------------------------------------
   // PJSR's File API speaks forward slashes on every platform: a backslash is read as "/",
   // then the directory is everything before the last "/".
   const savedFile = globalThis.File;
   globalThis.File = { extractDirectory: p => { const q = p.replace( /\\/g, "/" ); const i = q.lastIndexOf( "/" );
                                                 return i < 0 ? "" : q.substring( 0, i ); } };
   try
   {
      const paths = [
         "/data/M42/master/masterLight_FILTER-Ha.xisf",
         "/data/NGC7000/integrations/masterLight.xisf",
         "/data/NGC7000/Master/x.xisf",
         "/data/Target/DRIZZLE/AUTOCROP/x.xisf",
         "/x.xisf",
         "C:/Users/fc/Astro/IC1396/master/masterLight.xisf",
         "//server/share/M42/master/masterLight.xisf",
         "D:/Astro/Targets/Rosette/Lights/masters/x.xisf",
         "/a/b/c/d/e/f/g/tmp/x.xisf",
         "/data/Crab Nebula/master/masterLight.xisf",
         "",
         null,
         "/data/Orion/cache/Calibrated/x.xisf",
         "/data/Veil/master\\x.xisf",
         "C:\\Users\\fc\\Astro\\Targets\\Cygnus\\master\\masterLight.xisf",
         "C:\\Astro/M31\\masters/x.fits",
         "\\\\server\\share\\M42\\masters\\x.fits"
      ];
      paths.forEach( ( p, i ) => add( "project_name_from_path/" + i, "project_name_from_path", [ p ],
                                      Pipeline.projectNameFromPath( p ) ) );
      const configs = [
         { paths: { R: "/d/M42/master/r.xisf" } },
         { paths: { H: "/a/B/x.xisf", L: "/c/D/y.xisf" } },
         { paths: {} },
         { paths: { L: "/gen/master/l.xisf", R: "/t/Veil/r.xisf" } },
         { paths: { L: "", G: "/t/Rosette/g.xisf" } },
         { paths: { L: "/x.xisf" } },
         {},
         null
      ];
      configs.forEach( ( c, i ) => add( "project_name_for/" + i, "project_name_for", [ clone( c ) ],
                                        Pipeline.projectNameFor( c ) ) );
   }
   finally { globalThis.File = savedFile; }

   [ { projectName: " M42 " }, { projectName: "" }, { projectName: "   " }, { projectName: "IC 1396" }, {}, null ]
      .forEach( ( c, i ) => add( "psb_base_name/" + i, "psb_base_name", [ clone( c ) ], Pipeline.psbBaseName( c ) ) );

   // ---- palette configuration ---------------------------------------------
   const palConfigs = [
      { paths: { H: "/h.xisf" }, palettes: [ "SHO" ] },
      { paths: { H: "/h.xisf", S: "/s.xisf", O: "/o.xisf" }, palettes: [ "SHO", "HOO", "HSO" ] },
      { paths: { H: "/h.xisf", O: "/o.xisf" }, palettes: [ "SHO", "HOO" ] },
      { paths: { R: "/r.xisf", G: "/g.xisf", B: "/b.xisf" }, palettes: [ "SHO" ] },
      { paths: { H: "/h.xisf" }, views: { S: "view S", O: "" }, palettes: [ "SHO", "XYZ" ] },
      { paths: { H: "/h.xisf", S: "/s.xisf" }, palettes: [ "HSO" ] },
      { paths: { O: "/o.xisf" }, palettes: [] },
      { paths: { H: "/h.xisf", S: "/s.xisf", O: "/o.xisf" }, palettes: [ "HOO", "SHO" ] },
      { palettes: [ "SHO" ] },
      { paths: {}, views: { H: "view H", S: "view S", O: "view O" }, palettes: [ "HSO", "SHO" ] },
      { paths: { H: "/h.xisf", S: "/s.xisf" }, views: { O: "view O" }, palettes: [ "SHO", "HOO", "HSO" ] },
      { paths: { L: "/l.xisf", R: "/r.xisf" }, palettes: [ "SHO", "HSO", "bogus" ] }
   ];
   palConfigs.forEach( ( c, i ) => {
      add( "narrowband_supplied/" + i, "narrowband_supplied", [ clone( c ) ], Pipeline.narrowbandSupplied( clone( c ) ) );
      add( "wanted_palettes/" + i, "wanted_palettes", [ clone( c ) ], Pipeline.wantedPalettes( clone( c ) ) );
      const warnings = capture( "warn", () => Pipeline.reportSkippedPalettes( clone( c ) ) );
      add( "report_skipped_palettes/" + i, "report_skipped_palettes", [ clone( c ) ], warnings );
   } );

   // ---- instrument inheritance ---------------------------------------------
   const inh = [
      { L: { key: "L", instrume: "ZWO ASI2600MM" }, R: { key: "R", instrume: null }, G: { key: "G", instrume: "" } },
      { L: { key: "L", instrume: "ZWO ASI2600MM" }, R: { key: "R", instrume: "QHY268M" } },
      { L: { key: "L", instrume: null }, R: { key: "R", instrume: null } },
      { L: { key: "L", instrume: "ZWO ASI183MM" }, H: { key: "H", instrume: undefined } },
      { L: { key: "L", instrume: "ZWO ASI294MM" } }
   ];
   inh.forEach( ( c, i ) => {
      const chans = clone( c );
      const before = clone( c );
      const logs = capture( "log", () => Pipeline.inheritInstrument( chans ) );
      add( "inherit_instrument/" + i, "inherit_instrument", [ before ], { logs: logs, chans: clone( chans ) } );
   } );

   return cases;
};
