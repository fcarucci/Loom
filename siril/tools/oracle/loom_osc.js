/*
 * Parity recorder for loomsiril/loom/osc.py: colour-camera (OSC) filter detection, colour-master slots,
 * selection rules, the remembered-kind key, and the colour group's cache keys.
 *
 * Each case is { fn, args, result }: the Python port is called as fn(*args) and must return result. Every call
 * is made on a JSON copy of the stored args, so the stored args are exactly what the Python port receives (an
 * absent value is null). The functions that fill a config or a candidate record what they left behind: see the
 * notes at oscKindProblems and selectColourMasters.
 *
 * One section per JavaScript function, so a later section can be added without touching the others.
 */
"use strict";

module.exports = function( js )
{
   const { Util, Pipeline } = js;
   const cases = {};
   const clone = v => JSON.parse( JSON.stringify( v === undefined ? null : v ) );
   // call( args ) runs on a fresh copy of the args; the stored args are another copy.
   function rec( name, fn, args, call )
   {
      const stored = clone( args );
      cases[name] = { fn: fn, args: stored, result: call( clone( stored ) ) };
   }
   const constant = ( fn, value ) => { cases["constant/" + fn] = { fn: fn, args: [], result: clone( value ) }; };

   // ---- constants -----------------------------------------------------------
   constant( "OSC_FILTERS", Util.OSC_FILTERS );
   constant( "OSC_BROADBAND", Util.OSC_BROADBAND );
   constant( "OSC_FAMILIES", Util.OSC_FAMILIES );
   constant( "COLOUR_SLOTS", Util.COLOUR_SLOTS );
   constant( "COLOUR_MEMBERS", Util.COLOUR_MEMBERS );
   constant( "OSC_RECIPES", Pipeline.OSC_RECIPES );

   // ---- filter names --------------------------------------------------------
   const modelNames = [ "L-eXtreme", "L-Ultimate", "L-eNhance", "NBZ", "NBZ-II", "ALP-T Hα/OIII", "ALP-T SII/Hβ" ];
   const spellings = [
      // five spellings each of two models: brand, size, case, spacing, Greek letters
      "L-eXtreme", "Optolong L-eXtreme 2\"", "l extreme 36mm", "  L_EXTREME  ", "Antlia L.eXtreme 1.25in",
      "ALP-T Hα/OIII", "Antlia ALP-T Ha/OIII 5nm", "alpt  ha oiii", "IDAS ALP-T HΑ/OIII 50mm", "ALP-T Hα/OIII 2''",
      // ten mixed-case names
      "uvir", "UVIR", "Uv-Ir Cut", "L-PRO", "lPro", "Cls", "CLS CCD", "lrgb", "Duo-Band", "TRIAD",
      "Quad Band", "alpt", "Optolong", "idas", "antlia  ", "Astronomik Deep-Sky", "xyzzy",
      "L-eXtreme  2\" ", "Optolong_L-Ultimate", "idas-L-eNhance 1.25\"", "Optolong\tL-eXtreme", "Optolong L-eXtreme 2 in",
      "ALP-T SII/Hb 5nm", "ALPT SIIHb", "ALP-T SII/Hbeta", "ALP-T SII/Hβ 2\"", "NBZex", "NBZ-II 36mm",
      "Hα", "HΒ", "ΑΒ", "optolong l-extreme", "OPTOLONG L-EXTREME 2\"", "", "   ", "Ha 7nm", "Baader Moon & Skyglow",
      "L-eXtreme 36 mm", "L-eXtreme 2.5\"", "L-eXtreme 1.25 IN", "Optolong 2\"", "L-eXtreme\u00a0",
      "İdas L-eXtreme", "ıdas L-eXtreme", "optolong\u3000L-eXtreme", "optolong L-eXtreme 2\u2003\"",
      "Astronomik L-eXtreme 2\"", "ZWO UV/IR Cut", "Baader UVIR", "SVBONY L-Pro 36mm", "STC Duo-Band", "Radian_L-eNhance",
      "astronomik", "zwo", "Baader\tCLS", "stc-Astro Duo", "Radian L-Ultimate 1.25\"", "ZWO L-eXtreme",
      "uv/ir", "IR-Cut", "ircut", "uvirblock", "UV/IR Block", "L eNhanced", "ALP-T Ha/OIII 5 nm"
   ].concat( modelNames, Util.OSC_FILTERS.map( f => f.name ), Util.OSC_FILTERS.map( f => f.match ),
             Util.OSC_BROADBAND, Util.OSC_FAMILIES );
   const filterValues = spellings.concat( [ null, 5, 1.25, true ] );
   // Distinct values only, in a stable order.
   const uniq = a => a.filter( ( v, i ) => a.findIndex( w => Object.is( w, v ) ) === i );
   const values = uniq( filterValues );

   values.forEach( ( v, i ) =>
   {
      rec( "normalise_filter/" + i, "normalise_filter", [ v ], a => Util.normaliseFilter( a[0] ) );
      rec( "osc_model_name/" + i, "osc_model_name", [ v ], a => Util.oscModelName( a[0] ) );
      rec( "osc_filter_info/" + i, "osc_filter_info", [ v ], a => Util.oscFilterInfo( a[0] ) );
   } );

   // ---- the kind key ----------------------------------------------------------
   const fingerprints = [
      "fp-abc", "/data/masters/M 31 night 2/masterLight.xisf", "C:\\Astro\\M31\\light.xisf", "view:M31_RGB",
      "/Users/émile/Astro/Ørsted ∆ Nebula/colour master.xisf", "", "α/β/γ", "  spaced  ", "a|b|osc:c",
      "/Volumes/Data/ÅÄÖ/\u00e9\u0301.xisf"
   ];
   const kindFilters = [ "L-eXtreme", "Optolong L-eXtreme 2\"", "l extreme 36mm", "ALP-T Hα/OIII", "", null,
                         "Optolong", "Duo", "UVIR" ];
   fingerprints.forEach( ( p, i ) =>
   {
      kindFilters.forEach( ( f, j ) =>
         rec( "osc_kind_key/" + i + "/" + j, "osc_kind_key", [ f, p ], a => Util.oscKindKey( a[0], a[1] ) ) );
      rec( "osc_kind_key/path-only/" + i, "osc_kind_key", [ null, p ], a => Util.oscKindKey( a[0], a[1] ) );
   } );
   rec( "osc_kind_key/null-path", "osc_kind_key", [ "", null ], a => Util.oscKindKey( a[0], a[1] ) );
   rec( "osc_kind_key/case-a", "osc_kind_key", [ "", "/Masters/A.xisf" ], a => Util.oscKindKey( a[0], a[1] ) );
   rec( "osc_kind_key/case-b", "osc_kind_key", [ "", "/masters/a.xisf" ], a => Util.oscKindKey( a[0], a[1] ) );

   // ---- the effective kind ----------------------------------------------------
   const choiceSets = [
      null, {}, { "filter:lextreme": "broadband" }, { "filter:lextreme": "dualband" },
      { "filter:duo": "dualband", "filter:uvir": "dualband", "path:/m/a.xisf": "broadband" },
      { "filter:alptsiihb": "broadband" }, { "filter:optolong": "dualband" }, { "path:": "broadband" },
      { "filter:lextreme": "other" }, { "filter:lextreme": null }, { "path:/m/a.xisf": "dualband", "path:null": "broadband" },
      { "filter:xyzzy": "broadband" }, { "filter:alpthaoiii": "broadband" }
   ];
   const effFilters = [ "L-eXtreme", "Optolong L-eXtreme 2\"", "L-Ultimate", "ALP-T Hα/OIII", "ALP-T SII/Hb", "Duo",
                        "UVIR", "", null, "Optolong", "xyzzy", "Astronomik Deep-Sky" ];
   let n = 0;
   choiceSets.forEach( c => effFilters.forEach( f =>
      [ "OSC", "OSCNB", null ].forEach( slot =>
         rec( "effective_osc_kind/" + ( n++ ), "effective_osc_kind", [ slot, f, "/m/a.xisf", c ],
              a => Util.effectiveOscKind( a[0], a[1], a[2], a[3] ) ) ) ) );
   rec( "effective_osc_kind/blank-path", "effective_osc_kind", [ "OSC", "", null, { "path:null": "dualband" } ],
        a => Util.effectiveOscKind( a[0], a[1], a[2], a[3] ) );
   rec( "effective_osc_kind/view-id", "effective_osc_kind", [ "OSC", "", "M31_RGB", { "path:M31_RGB": "dualband" } ],
        a => Util.effectiveOscKind( a[0], a[1], a[2], a[3] ) );

   // ---- channel counts --------------------------------------------------------
   const counts = [ 0, 1, 2, 3, 4, 5, 7, -1, 2.5, 1e21, null, "3", "1", "2", true, false, "" ];
   counts.forEach( ( c, i ) =>
      [ "/m/a.xisf", "", null, "C:\\a b\\ü.xisf" ].forEach( ( p, j ) =>
         rec( "channel_count_problem/" + i + "/" + j, "channel_count_problem", [ p, c ],
              a => Util.channelCountProblem( a[0], a[1] ) ) ) );

   // ---- colour master names ---------------------------------------------------
   const masterNames = [
      "masterLight_BIN-1_6248x4176_EXPOSURE-300.00s_FILTER-L-eXtreme_color.xisf",
      "masterLight_BIN-1_6248x4176_EXPOSURE-300.00s_FILTER-NoFilter_RGB_drizzle_2x_autocrop.xisf",
      "masterLight_FILTER-ALP-T_Ha-OIII_colour_drizzle_4x.xisf",
      "masterLight_FILTER-L_mono.xisf", "masterLight_FILTER-Ha_mono_autocrop.xisf", "master_color.xisf",
      "masterLight_FILTER-.xisf", "masterLight_FILTER-X.rgb", "FILTER-UVIR_RGB", "FILTER-UVIR_rgb_", "x_Color.fits",
      "x_colour", "x_COLOURS.fits", "x_RGBA.fits", "x_RGB.fits", "x_RGB", "/Users/me/Astro/FILTER-DUO_color.xisf",
      "Drizzle_2x_color", "drizzle_12x_AUTOCROP_color_(1).xisf", "FILTER-a.b_color", "filter-lowercase_color",
      "", null, "plain.xisf", "FILTER-Ünï_color.xisf", "_color", "a_color_b", "a_color.b", "a_RGB\n", "FILTER-x\ny_color"
   ];
   masterNames.forEach( ( nme, i ) =>
      rec( "parse_colour_master_name/" + i, "parse_colour_master_name", [ nme ], a => Util.parseColourMasterName( a[0] ) ) );

   // ---- supplied inputs -------------------------------------------------------
   const cfgs = [
      null, {}, { paths: {} }, { views: {} }, { paths: null, views: null }, { paths: {}, views: {} },
      { paths: { R: "/m/r.xisf" } }, { paths: { R: "/m/r.xisf", G: "/m/g.xisf", B: "/m/b.xisf" } },
      { paths: { OSC: "/m/rgb.xisf" } }, { paths: { OSCNB: "/m/nb.xisf" } },
      { paths: { OSC: "/m/rgb.xisf", OSCNB: "/m/nb.xisf" } },
      { paths: { OSC: "" }, views: { OSCNB: "NB_View" } },
      { paths: { OSC: "" }, views: { OSC: "" } },
      { paths: { OSC: "/m/rgb.xisf", H: "/m/h.xisf" } },
      { paths: { OSC: "/m/rgb.xisf", R: "/m/r.xisf" }, views: {} },
      { paths: { OSC: "C:\\Astro\\M 31\\rgb.xisf" }, views: { L: "L_View", H: "Hview" } },
      { paths: { OSCNB: "/m/nb.xisf", L: "/m/l.xisf", H: "/m/h.xisf" } },
      { paths: { OSCNB: "/m/nb.xisf", O: "/m/o.xisf", S: "/m/s.xisf" } },
      { paths: { OSC: "/m/rgb.xisf" }, views: { R: "R_View", G: "G_View" } },
      { paths: {}, views: { OSC: "OSC_View", B: "B_View" } },
      { views: { OSCNB: "NB_View", H: "H_View", O: "O_View" } },
      { paths: { OSC: "/m/rgb.xisf" }, views: { OSC: "OSC_View" } },
      { paths: { H: "/m/h.xisf", S: "/m/s.xisf", O: "/m/o.xisf", L: "/m/l.xisf" } },
      { paths: { R: "" }, views: { R: "" } },
      { paths: { OSC: 5 }, views: {} }, { paths: {}, views: { OSC: 7 } }, { paths: { OSC: false }, views: { OSC: true } },
      { paths: { OSC: "/a/b\\c/d e.xisf", L: "x" }, views: {} }, { paths: { L: "line1\nline2/z.xisf", OSC: "o" } }
   ];
   cfgs.forEach( ( c, i ) =>
   {
      rec( "colour_supplied/" + i, "colour_supplied", [ c ], a => Util.colourSupplied( a[0] ) );
      rec( "mono_supplied/" + i, "mono_supplied", [ c ], a => Util.monoSupplied( a[0] ) );
      rec( "supplied_channels/" + i, "supplied_channels", [ c ], a => Util.suppliedChannels( a[0] ) );
      rec( "colour_mix_problem/" + i, "colour_mix_problem", [ c ], a => Util.colourMixProblem( a[0] ) );
   } );
   [ [ {}, {}, "R" ], [ { R: "/a" }, {}, "R" ], [ { R: "" }, {}, "R" ], [ {}, { R: "v" }, "R" ], [ {}, { R: "" }, "R" ],
     [ { OSC: "/a" }, { OSC: "v" }, "OSC" ], [ { OSC: null }, { OSC: null }, "OSC" ], [ { R: 0 }, { R: 0 }, "R" ],
     [ { R: [ "x" ] }, {}, "R" ], [ {}, { R: false }, "R" ] ].forEach( ( t, i ) =>
      rec( "channel_supplied/" + i, "channel_supplied", t, a => Util.channelSupplied( a[0], a[1], a[2] ) ) );

   // ---- where a masters-list entry goes -----------------------------------------
   const entryFilters = [ "L-eXtreme", "UVIR", "", null, "Duo", "ALP-T SII/Hb", "Optolong L-eXtreme 2\"", "Ha", "L", "xyzzy",
                          "halpha", "Red", "OIII", "Astronomik Deep-Sky" ];
   const entryCounts = [ 1, 3, 2, 0, null, 4, "3", "1" ];
   const entryChoices = [ null, {}, { "filter:lextreme": "broadband" }, { "filter:duo": "dualband", "filter:uvir": "dualband" },
                          { "path:/m/a.xisf": "dualband", "filter:": "broadband" }, { "path:/m/a.xisf": "broadband" } ];
   n = 0;
   entryFilters.forEach( f => entryCounts.forEach( c => entryChoices.forEach( ch =>
      rec( "entry_channel/" + ( n++ ), "entry_channel", [ f, c, "/m/a.xisf", ch ],
           a => Util.entryChannel( a[0], a[1], a[2], a[3] ) ) ) ) );

   // ---- the best colour master per slot ------------------------------------------
   const cand = ( filter, channels, drizzle, autocrop, mtime, path ) =>
      ( { path: path, filter: filter, numberOfChannels: channels, drizzle: drizzle, autocrop: autocrop, mtime: mtime } );
   const candidateSets = [
      [],
      [ null ],
      [ cand( "UVIR", 3, "", false, 5, "/m/a.xisf" ) ],
      [ cand( "UVIR", 3, "", false, 5, "/m/a.xisf" ), cand( "", 3, "2x", false, 1, "/m/b.xisf" ) ],
      [ cand( "UVIR", 3, "", false, 5, "/m/a.xisf" ), cand( "UVIR", 3, "", false, 9, "/m/b.xisf" ) ],
      [ cand( "UVIR", 3, "", true, 5, "/m/a.xisf" ), cand( "UVIR", 3, "2x", false, 1, "/m/b.xisf" ),
        cand( "UVIR", 3, "2x", true, 0, "/m/c.xisf" ) ],
      [ cand( "L-eXtreme", 3, "", false, 5, "/m/a.xisf" ), cand( "L-Ultimate", 3, "", false, 5, "/m/b.xisf" ) ],
      [ cand( "Duo", 3, "", false, 5, "/m/a.xisf" ), cand( "L-eXtreme", 3, "", false, 5, "/m/b.xisf" ),
        cand( "ALP-T SII/Hb", 3, "", false, 5, "/m/c.xisf" ) ],
      [ cand( "L", 1, "", false, 5, "/m/a.xisf" ), cand( "UVIR", 3, "", false, 5, "/m/b.xisf" ) ],
      [ cand( "UVIR", null, "", false, null, "/m/a.xisf" ), cand( "UVIR", 3, null, null, 0, "/m/b.xisf" ) ],
      [ cand( "UVIR", 2, "", false, 5, "/m/a.xisf" ), cand( "UVIR", 4, "", false, 5, "/m/b.xisf" ) ],
      [ cand( "", 3, "", false, 7, "view1" ), cand( "", 3, "", false, 8, "view2" ) ],
      [ cand( "Optolong L-eXtreme 2\"", 3, "4x", true, 3, "/m/é.xisf" ), cand( "l extreme", 3, "4x", true, 3, "/m/f.xisf" ) ],
      [ cand( "UVIR", 3, "", false, 5, "/m/a.xisf" ), null, cand( "L-eXtreme", 3, "2x", true, 2, "/m/b.xisf" ), null ],
      [ cand( "xyzzy", 3, "", false, 1, "/m/a.xisf" ), cand( "UVIR", 3, "", false, 1, "/m/b.xisf" ) ]
   ];
   const pickChoices = [ null, { "filter:duo": "dualband", "filter:xyzzy": "broadband" }, { "filter:lextreme": "broadband" } ];
   // The result holds the two picks and the unresolved list; the picks carry the rank written onto them.
   candidateSets.forEach( ( cs, i ) => pickChoices.forEach( ( ch, j ) =>
      rec( "select_colour_masters/" + i + "/" + j, "select_colour_masters", [ cs, ch ],
           a => Util.selectColourMasters( a[0], a[1] ) ) ) );

   // ---- a name and a header that disagree -----------------------------------------
   [ null, {}, { path: "/m/a.xisf", colour: true, numberOfChannels: 1 }, { path: "/m/a.xisf", colour: false, numberOfChannels: 3 },
     { path: "/m/a.xisf", colour: true, numberOfChannels: 3 }, { path: "/m/a.xisf", colour: false, numberOfChannels: 1 },
     { path: "/m/a.xisf", colour: true, numberOfChannels: 2 }, { path: "/m/a.xisf", numberOfChannels: 3 },
     { path: "/m/a.xisf", colour: true }, { path: "C:\\x y\\é.xisf", colour: false, numberOfChannels: 3 },
     { colour: true, numberOfChannels: 1 }, { path: "v", colour: true, numberOfChannels: "1" },
     { path: "v", colour: 0, numberOfChannels: 3 } ].forEach( ( c, i ) =>
      rec( "colour_candidate_correction/" + i, "colour_candidate_correction", [ c ],
           a => Util.colourCandidateCorrection( a[0] ) ) );

   // ---- the palette check -----------------------------------------------------------
   const paletteLists = [ undefined, [], [ "HOO" ], [ "SHO" ], [ "HSO" ], [ "HOO", "SHO" ], [ "HOO", "HSO", "SHO" ],
                          [ "SHO", "HSO" ], [ "OHS" ], [ "HOO", "HOS" ], null ];
   const paletteConfigs = [ null, {}, { paths: {} }, { paths: { R: "/m/r", G: "/m/g", B: "/m/b" } },
                            { paths: { OSC: "/m/rgb.xisf" } }, { paths: { OSCNB: "/m/nb.xisf" } },
                            { views: { OSC: "OSC_View" } }, { paths: { OSC: "" }, views: {} }, { paths: { H: "/m/h" } } ];
   n = 0;
   paletteConfigs.forEach( c => paletteLists.forEach( p =>
   {
      const cfg = c === null ? ( p === undefined ? null : { palettes: p } ) : Object.assign( {}, c, p === undefined ? {} : { palettes: p } );
      rec( "osc_palette_problem/" + ( n++ ), "osc_palette_problem", [ cfg ], a => Pipeline.oscPaletteProblem( a[0] ) );
   } ) );

   // ---- the channel-count and kind checks (infoOf is a table, slot -> info) -----------
   const info = ( filter, channels, path ) => ( { path: path, filter: filter, numberOfChannels: channels } );
   const tables = [
      {}, { R: info( "", 1, "/m/r.xisf" ) }, { R: info( "", 3, "/m/r.xisf" ) }, { R: info( "", 2, "/m/r.xisf" ) },
      { R: { path: "/m/r.xisf" } }, { R: info( "", null, "" ) }, { R: info( "", 1, "" ) }, { R: info( "", "1", "/m/r.xisf" ) },
      { R: info( "", 1, "/m/r.xisf" ), G: info( "", 3, "/m/g.xisf" ), B: info( "", 4, "/m/b.xisf" ) },
      { OSC: info( "UVIR", 3, "/m/rgb.xisf" ) }, { OSC: info( "", 3, "/m/rgb.xisf" ) }, { OSC: info( "L-eXtreme", 3, "/m/rgb.xisf" ) },
      { OSC: info( "Duo", 3, "/m/rgb.xisf" ) }, { OSC: info( "ALP-T SII/Hb", 3, "/m/rgb.xisf" ) },
      { OSC: info( "xyzzy", 3, "/m/rgb.xisf" ) }, { OSC: info( "UVIR", 1, "/m/rgb.xisf" ) }, { OSC: info( "UVIR", 2, "/m/rgb.xisf" ) },
      { OSC: info( "UVIR", null, "/m/rgb.xisf" ) }, { OSC: info( "UVIR", 3, "" ) }, { OSC: info( "", 3, "" ) },
      { OSCNB: info( "L-eXtreme", 3, "/m/nb.xisf" ) }, { OSCNB: info( "Optolong L-eXtreme 2\"", 3, "/m/nb.xisf" ) },
      { OSCNB: info( "UVIR", 3, "/m/nb.xisf" ) }, { OSCNB: info( "Duo", 3, "/m/nb.xisf" ) }, { OSCNB: info( "", 3, "/m/nb.xisf" ) },
      { OSCNB: info( "ALP-T SII/Hβ", 3, "/m/nb.xisf" ) }, { OSCNB: info( "xyzzy", 3, "/m/nb.xisf" ) },
      { OSC: info( "UVIR", 3, "/m/rgb.xisf" ), OSCNB: info( "L-Ultimate", 3, "/m/nb.xisf" ) },
      { OSC: info( "L-eXtreme", 3, "/m/rgb.xisf" ), OSCNB: info( "UVIR", 3, "/m/nb.xisf" ) },
      { OSC: info( "", 3, "/m/é ü.xisf" ), OSCNB: info( "NBZ-II", 3, "C:\\n b\\x.xisf" ) },
      { L: info( "L", 1, "/m/l.xisf" ), H: info( "Ha", 3, "/m/h.xisf" ), O: info( "OIII", 0, "/m/o.xisf" ) }
   ];
   const checkConfigs = [
      { paths: { R: "/m/r.xisf" } }, { paths: { R: "/m/r.xisf", G: "/m/g.xisf", B: "/m/b.xisf" } },
      { paths: { OSC: "/m/rgb.xisf" } }, { paths: { OSCNB: "/m/nb.xisf" } },
      { paths: { OSC: "/m/rgb.xisf", OSCNB: "/m/nb.xisf" }, oscKindChoices: { "filter:duo": "dualband", "filter:uvir": "broadband" } },
      { paths: { OSC: "" }, views: { OSC: "RGB_View" }, oscKindChoices: { "path:RGB_View": "broadband", "path:": "dualband" } },
      { paths: { OSCNB: "" }, views: { OSCNB: "NB_View" }, oscKindChoices: { "path:NB_View": "dualband" } },
      { paths: { OSC: "/m/rgb.xisf" }, oscKindChoices: { "filter:lextreme": "dualband", "filter:xyzzy": "broadband" }, oscKinds: { OSCNB: "dualband" } },
      { paths: { OSCNB: "/m/nb.xisf" }, oscKindChoices: { "filter:xyzzy": "dualband" } },
      { paths: { OSCNB: "/m/nb.xisf" }, oscKindChoices: { "filter:duo": "broadband", "filter:uvir": "dualband" } },
      { paths: { R: "" }, views: { R: "R_View" } }, { paths: { L: "/m/l.xisf", H: "/m/h.xisf", O: "/m/o.xisf" } },
      { paths: { OSC: "/m/é ü.xisf", OSCNB: "C:\\n b\\x.xisf" }, oscKinds: { OSC: "broadband" } },
      { paths: { OSC: "/m/rgb.xisf" }, oscKinds: null }
   ];
   n = 0;
   checkConfigs.forEach( c => tables.forEach( t =>
   {
      rec( "input_channel_problems/" + n, "input_channel_problems", [ c, t ],
           a => Pipeline.inputChannelProblems( a[0], s => a[1][s] ) );
      // oscKindProblems fills config.oscKinds: the result is the problems and the kinds left in the config.
      rec( "osc_kind_problems/" + n, "osc_kind_problems", [ c, t ], a =>
      {
         const problems = Pipeline.oscKindProblems( a[0], s => a[1][s] );
         return { problems: problems, oscKinds: a[0].oscKinds === undefined ? null : a[0].oscKinds };
      } );
      ++n;
   } ) );

   // ---- the keys of a colour group -------------------------------------------------
   n = 0;
   fingerprints.forEach( fp => [ "broadband", "dualband", "", null ].forEach( kind =>
   {
      rec( "osc_group_identity/" + n, "osc_group_identity", [ fp, kind ], a => Pipeline.oscGroupIdentity( a[0], a[1] ) );
      const gid = Pipeline.oscGroupIdentity( fp, kind );
      rec( "osc_validate_name/" + n, "osc_validate_name", [ gid ], a => Pipeline.oscValidateName( a[0] ) );
      [ 0, 1, 2 ].forEach( plane =>
         rec( "osc_sibling_key/" + n + "/" + plane, "osc_sibling_key", [ gid, plane ],
              a => Pipeline.oscSiblingKey( a[0], a[1] ) ) );
      ++n;
   } ) );
   const sib = ( fp, k, p ) => Pipeline.oscSiblingKey( Pipeline.oscGroupIdentity( fp, k ), p );
   const triples = [ [ sib( "fp-abc", "broadband", 0 ), sib( "fp-abc", "broadband", 1 ), sib( "fp-abc", "broadband", 2 ) ],
                     [ sib( "/m/é.xisf", "dualband", 0 ), sib( "/m/é.xisf", "dualband", 1 ) ], [ "k0", "k1", "k2" ], [ "k0" ], [],
                     [ "a", null, "c" ] ];
   const groupParams = [ { ref: "refA", planes: "R,G,B" }, { ref: "refB", planes: "R,G,B" }, { planes: "H,O", ref: "é/ü α" },
                         {}, null, { n: 3, f: 0.5, ok: true, nested: { b: 1, a: [ 1, "x" ] } } ];
   n = 0;
   triples.forEach( t => [ "registerGroup", "register" ].forEach( st => groupParams.forEach( p =>
   {
      rec( "osc_group_register_key/" + n, "osc_group_register_key", [ st, t, p ],
           a => Pipeline.oscGroupRegisterKey( a[0], a[1], a[2] ) );
      const gk = Pipeline.oscGroupRegisterKey( st, t, p );
      [ 0, 1, 2 ].forEach( plane =>
         rec( "osc_registered_sibling_key/" + n + "/" + plane, "osc_registered_sibling_key", [ gk, plane ],
              a => Pipeline.oscRegisteredSiblingKey( a[0], a[1] ) ) );
      ++n;
   } ) ) );

   // ---- a plane's statistics ---------------------------------------------------------
   const statsCases = [ { min: 0, max: 0, median: 0 }, { min: 0, max: 1, median: 0.5 }, { min: 0.25, max: 0.25, median: 0.25 },
                        { min: 0.1, max: 0.1, median: 0.1 }, { min: 1e-7, max: 1e-7, median: 1e-7 },
                        { min: 1e21, max: 1e21, median: 1e21 }, { min: -3, max: -3, median: -3 }, { min: 0, max: 65535, median: 120 },
                        { min: 0.000123, max: 0.000123, median: 0.000123 }, { min: null, max: null, median: null },
                        { min: 1, max: 2, median: null } ];
   n = 0;
   statsCases.forEach( s => [ 0, 1, 2 ].forEach( plane => [ "/m/a.xisf", "C:\\x y\\é.xisf", "view" ].forEach( p =>
      rec( "osc_plane_problem/" + ( n++ ), "osc_plane_problem", [ s, plane, p ], a => Pipeline.oscPlaneProblem( a[0], a[1], a[2] ) ) ) ) );
   [ [], [ { min: 0, max: 1, median: 0.5 } ], statsCases.slice( 0, 3 ), statsCases.slice( 3, 6 ), statsCases.slice( 7, 10 ),
     [ { min: 0.1, max: 0.9, median: 0.5, mean: 7 }, { min: 0, max: 0, median: 0 }, { min: 1, max: 1, median: 1 } ] ].forEach( ( l, i ) =>
      rec( "osc_validation_record/" + i, "osc_validation_record", [ l ], a => Pipeline.oscValidationRecord( a[0] ) ) );

   // ---- narrowband SPCC bandwidths of a dual-band colour master --------------------------
   // Every config names its fallback bandwidth: an absent one would leave a key out of the JSON.
   const nbInfos = [ null, {}, { filter: null }, { filter: "" }, { filter: "L-eXtreme" }, { filter: "Optolong L-Ultimate 2\"" },
                     { filter: "L-eNhance" }, { filter: "NBZ-II" }, { filter: "NBZ" }, { filter: "ALP-T Ha/OIII" },
                     { filter: "ALP-T SII/Hb" }, { filter: "Duo" }, { filter: "UVIR" }, { filter: "xyzzy" },
                     { filter: "ZWO L-eXtreme 36mm" }, { filter: "Astronomik L-Ultimate" }, { filter: "Baader Duo" } ];
   n = 0;
   [ 3, 6, 0, 12.5, null, 1e-9 ].forEach( bw => nbInfos.forEach( i =>
      rec( "osc_narrowband_bandwidths/" + ( n++ ), "osc_narrowband_bandwidths", [ { narrowbandBandwidth: bw }, i ],
           a => Pipeline.oscNarrowbandBandwidths( a[0], a[1] ) ) ) );

   // ---- how a colour run's channels are registered -------------------------------------------
   const thrown = f => { try { return f(); } catch ( e ) { return { throws: e.message }; } };
   const groupSets = [ null, {}, { OSC: [ "R", "G", "B" ] }, { OSCNB: [ "H", "O" ] },
                       { OSC: [ "R", "G", "B" ], OSCNB: [ "H", "O" ] }, { OSCNB: [ "H", "O" ], OSC: [ "R", "G", "B" ] },
                       { OSC: [ "R", "G" ] }, { OSC: [ "R", "B" ] }, { OSC: [ "G" ] }, { OSC: [] }, { OSCNB: [] },
                       { OSC: null }, { OSC: [ "B", "R", "G" ] }, { OSC: [ "R", "G", "B" ], OSCNB: [ "O" ] },
                       { OSC: [ "R", "G", "B" ], OTHER: [ "L" ] }, { OTHER: [ "L" ] }, { OSC: [ "R", "G", "B", "L" ] } ];
   const presents = [ [], [ "R", "G", "B" ], [ "H", "O" ], [ "R", "G", "B", "H", "O" ], [ "L", "R", "G", "B" ],
                      [ "L", "H", "O" ], [ "R", "G" ], [ "R", "B" ], [ "G" ], [ "H" ], [ "O" ], [ "B", "G", "R" ],
                      [ "L", "R", "G", "B", "H", "O", "S" ], [ "R", "R", "G", "B" ], [ "L" ], [ "S", "H", "O" ],
                      [ "r", "g", "b" ] ];
   n = 0;
   groupSets.forEach( g => presents.forEach( p =>
      rec( "osc_registration_plan/" + ( n++ ), "osc_registration_plan", [ p, g ], a => Pipeline.oscRegistrationPlan( a[0], a[1] ) ) ) );
   rec( "osc_registration_plan/no-groups-arg", "osc_registration_plan", [ [ "L", "H" ], null ],
        a => Pipeline.oscRegistrationPlan( a[0], a[1] ) );

   const refKeys = [ "G", "R", "B", "H", "O", "L", "S", "", null, "g", "OSC" ];
   n = 0;
   groupSets.forEach( g => refKeys.forEach( r =>
      rec( "clean_registration_plan/" + ( n++ ), "clean_registration_plan", [ r, g ],
           a => thrown( () => Pipeline.cleanRegistrationPlan( a[0], a[1] ) ) ) ) );
   rec( "clean_registration_plan/no-groups-no-ref", "clean_registration_plan", [ null, null ],
        a => thrown( () => Pipeline.cleanRegistrationPlan( a[0], a[1] ) ) );

   // ---- what a grouped registration does given the cache --------------------------------------
   const policies = [ { useCache: true, ignoreCache: false }, { useCache: true, ignoreCache: true },
                      { useCache: false, ignoreCache: false }, { useCache: false, ignoreCache: true }, {},
                      { useCache: true }, { ignoreCache: false }, { useCache: 1, ignoreCache: 0 },
                      { useCache: "yes", ignoreCache: "" }, { useCache: true, ignoreCache: null }, { useCache: null, ignoreCache: false } ];
   const splitSets = [ [], [ true ], [ false ], [ true, true ], [ true, false ], [ false, true ], [ false, false ],
                       [ true, true, true ], [ true, true, false ], [ true, false, true ], [ false, true, true ], [ false, false, false ],
                       [ 1, "x", {} ], [ 1, 0 ], [ null, true ], [ "", true ], [ true, "0" ] ];
   n = 0;
   [ true, false ].forEach( gc => policies.forEach( pol => splitSets.forEach( sp =>
      rec( "osc_group_cache_action/" + ( n++ ), "osc_group_cache_action", [ gc, sp, pol ],
           a => Pipeline.oscGroupCacheAction( a[0], a[1], a[2] ) ) ) ) );

   // ---- the colour groups of a run ---------------------------------------------------------------
   const ch = ( group, plane ) => ( { group: group, plane: plane } );
   const chanSets = [
      {}, { L: ch( null, null ) }, { R: ch( "OSC", 0 ), G: ch( "OSC", 1 ), B: ch( "OSC", 2 ) },
      { H: ch( "OSCNB", 0 ), O: ch( "OSCNB", 1 ) },
      { R: ch( "OSC", 0 ), G: ch( "OSC", 1 ), B: ch( "OSC", 2 ), H: ch( "OSCNB", 0 ), O: ch( "OSCNB", 1 ) },
      { B: ch( "OSC", 2 ), G: ch( "OSC", 1 ), R: ch( "OSC", 0 ) },
      { L: ch( null, null ), R: ch( "OSC", 0 ), G: ch( "OSC", 1 ), B: ch( "OSC", 2 ) },
      { L: ch( null, null ), H: ch( "OSCNB", 0 ), O: ch( "OSCNB", 1 ), S: ch( null, null ) },
      { R: ch( "OSC", 0 ), G: ch( "OSC", 1 ) }, { G: ch( "OSC", 1 ) }, { H: ch( "OSCNB", 0 ) }, { O: ch( "OSCNB", 1 ) },
      { R: ch( null, null ), G: ch( null, null ), B: ch( null, null ) },
      { R: ch( "OSCNB", 0 ), G: ch( "OSC", 1 ), B: ch( "OSC", 2 ) },
      { R: ch( "OSC", 0 ), G: null, B: ch( "OSC", 2 ) },
      { R: { plane: 0 }, G: ch( "OSC", 1 ) },
      { R: ch( "osc", 0 ), G: ch( "Osc", 1 ) },
      { H: ch( "OSC", 0 ), O: ch( "OSC", 1 ) },
      { L: ch( "OSC", 0 ), S: ch( "OSCNB", 0 ) },
      { R: ch( "OSC", 0 ), G: ch( "OSC", 1 ), B: ch( "OSC", 2 ), H: ch( "OSC", 0 ), O: ch( "OSCNB", 1 ), L: ch( null, null ) },
      { R: { group: "OSC" }, G: { group: "OSC" }, B: { group: "OSC" } }
   ];
   chanSets.forEach( ( c, i ) =>
      rec( "osc_channel_groups/" + i, "osc_channel_groups", [ c ], a => Pipeline.oscChannelGroups( a[0] ) ) );

   // ---- the colour camera's sensor checks (parity only) ----------------------------------------
   // curveExists is a table here: channel -> the curve names that exist for it.
   const sensorTables = [ { R: [], G: [], B: [] },
                          { R: [ "Sony Color Sensor R" ], G: [ "Sony Color Sensor G" ], B: [ "Sony Color Sensor B" ] },
                          { R: [ "Sony Color Sensor R" ], G: [], B: [ "Sony Color Sensor B" ] },
                          { R: [ "Sony Color Sensor R", "Other R" ], G: [ "Other G" ], B: [ "Sony Color Sensor B", "Other B" ] },
                          { R: [ "é R" ], G: [ "é G" ], B: [ "é B" ] }, {}, null ];
   const oscSensors = [ { R: "Sony Color Sensor R", G: "Sony Color Sensor G", B: "Sony Color Sensor B" },
                        { R: "Other R", G: "Other G", B: "Other B" },
                        { R: "Sony Color Sensor R", G: "Other G", B: "Sony Color Sensor B" },
                        { R: "Nope R", G: "Nope G", B: "Nope B" }, { R: "é R", G: "é G", B: "é B" },
                        { R: "Sony Color Sensor R", G: "Sony Color Sensor G" }, { R: "Sony Color Sensor R", G: "", B: "Sony Color Sensor B" },
                        { R: "", G: "", B: "" }, {}, null, { G: "Sony Color Sensor G" }, { R: "Sony Color Sensor R", G: "Sony Color Sensor G", B: null } ];
   const sensorRuns = [ { paths: { OSC: "/m/rgb.xisf" } }, { views: { OSC: "RGB_View" } }, { paths: { OSCNB: "/m/nb.xisf" } },
                        { paths: { R: "/m/r", G: "/m/g", B: "/m/b" } }, { paths: { OSC: "/m/rgb.xisf", OSCNB: "/m/nb.xisf" } },
                        { paths: { OSC: "" } }, {} ];
   n = 0;
   sensorRuns.forEach( run => oscSensors.forEach( s => sensorTables.forEach( t =>
   {
      const cfg = Object.assign( {}, run, { oscSensor: s } );
      rec( "osc_sensor_problems/" + ( n++ ), "osc_sensor_problems", [ cfg, t ], a =>
      {
         const table = a[1] || {};
         return Pipeline.oscSensorProblems( a[0], ( name, channel ) => !!( table[channel] && table[channel].indexOf( name ) >= 0 ) );
      } );
   } ) ) );
   sensorRuns.forEach( ( run, i ) =>
      rec( "osc_sensor_problems/no-sensor/" + i, "osc_sensor_problems", [ run, sensorTables[1] ], a =>
         Pipeline.oscSensorProblems( a[0], ( name, channel ) => !!( a[1][channel] && a[1][channel].indexOf( name ) >= 0 ) ) ) );

   // ---- which dialog controls a colour master changes --------------------------------------------
   const controlConfigs = [ {}, null, { paths: {} }, { paths: { OSC: "/m/rgb.xisf" } }, { paths: { OSCNB: "/m/nb.xisf" } },
                            { paths: { OSC: "/m/rgb.xisf", OSCNB: "/m/nb.xisf" } }, { views: { OSC: "RGB_View" } },
                            { views: { OSCNB: "NB_View" } }, { paths: { R: "/m/r", G: "/m/g", B: "/m/b" } },
                            { paths: { OSC: "" } }, { paths: { OSC: "", OSCNB: "/m/nb.xisf" } },
                            { paths: { OSC: "/m/rgb.xisf", L: "/m/l.xisf" } }, { views: { OSC: "" }, paths: { OSC: "/m/x" } },
                            { paths: { H: "/m/h.xisf", O: "/m/o.xisf", S: "/m/s.xisf" } }, { views: {}, paths: {} } ];
   controlConfigs.forEach( ( c, i ) =>
      rec( "osc_control_state/" + i, "osc_control_state", [ c ], a => Util.oscControlState( a[0] ) ) );

   // ---- the white-balance key (recorded only: replayed with the white-balance port) -----
   const chansOk = { R: { cleanKey: "cR" }, G: { cleanKey: "cG" }, B: { cleanKey: "cB" } };
   const common = { x0: 1, y0: 2, x1: 300, y1: 200 };
   const sensors = [ { R: "Sony Color Sensor R", G: "Sony Color Sensor G", B: "Sony Color Sensor B" },
                     { R: "Sony Color Sensor R", G: "Other G", B: "Sony Color Sensor B" }, {}, null ];
   const wbConfigs = [
      { paths: { R: "/m/r", G: "/m/g", B: "/m/b" }, filters: { R: "Astronomik R" } },
      { paths: { R: "/m/r", G: "/m/g", B: "/m/b" } },
      { paths: { R: "/m/r", G: "/m/g", B: "/m/b" }, filters: { R: "Optolong R", G: "Optolong G", B: "Optolong B", L: "Ünï" } }
   ];
   sensors.forEach( s => wbConfigs.push( { paths: { OSC: "/m/rgb.xisf" }, oscSensor: s } ) );
   wbConfigs.push( { paths: { OSC: "/m/rgb.xisf" }, oscSensor: sensors[0], filters: { R: "X" } } );
   wbConfigs.push( { paths: { OSC: "/m/rgb.xisf" } } );
   n = 0;
   wbConfigs.forEach( cfg => [ "refA", "refB", null, "" ].forEach( ref => [ "CAM", null, "ASI 2600" ].forEach( lum =>
      [ common, { x0: 0, y0: 0, x1: 10.5, y1: 20 } ].forEach( cm =>
         rec( "clean_white_balance_key/" + ( n++ ), "clean_white_balance_key", [ chansOk, cfg, cm, lum, ref ],
              a => Pipeline.cleanWhiteBalanceKey( a[0], a[1], a[2], a[3], a[4] ) ) ) ) ) );
   rec( "clean_white_balance_key/missing-key", "clean_white_balance_key",
        [ { R: { cleanKey: "cR" }, G: { cleanKey: "" }, B: { cleanKey: "cB" } }, wbConfigs[0], common, "CAM", "refA" ], a =>
   {
      try { return Pipeline.cleanWhiteBalanceKey( a[0], a[1], a[2], a[3], a[4] ); }
      catch ( e ) { return { throws: e.message }; }
   } );

   // ---- the LOOMSRC texts of the exports: the colour master's file and planes -----------------------
   constant( "OSC_LINE_NOTES", Pipeline.OSC_LINE_NOTES );

   // The palette line of the run log, and what the palette sources of an export are built from.
   const srcPaletteConfigs = [ { paths: { OSCNB: "/m/nb.xisf" } }, { paths: { OSC: "/m/rgb.xisf" } },
                            { paths: { OSC: "/m/rgb.xisf", OSCNB: "/m/nb.xisf" } }, { views: { OSCNB: "NB_View" } },
                            { paths: { H: "/m/h.xisf", O: "/m/o.xisf" } }, { paths: { OSCNB: "" } }, {}, null,
                            { paths: { OSCNB: "/m/nb.xisf" }, palettes: [ "HOO" ] } ];
   const srcPaletteInfos = [ { OSCNB: { name: "L-eXtreme" } }, { OSCNB: { name: "ALP-T Ha/OIII" } }, { OSCNB: {} },
                          { OSCNB: { name: "" } }, { OSCNB: { name: 0 } }, { OSCNB: null }, {}, null,
                          { OSCNB: { name: "Optolong L-eXtreme 2\"" } } ];
   const srcPalettes = [ "HOO", "SHO", "HSO", "", null, "XYZ", "hoo", undefined ];
   n = 0;
   srcPaletteConfigs.forEach( c => srcPaletteInfos.forEach( i => srcPalettes.forEach( p =>
      rec( "osc_palette_sources/" + ( n++ ), "osc_palette_sources", [ c, i, p ],
           a => Pipeline.oscPaletteSources( a[0], a[1], a[2] ) ) ) ) );

   // One channel record: its colour master's file and plane, or null for a mono or unnamed record.
   const srcRecords = [
      null, {}, { group: null, path: "/m/x.xisf", plane: 0 }, { group: "", path: "/m/x.xisf", plane: 0 },
      { group: "OSC", path: "/m/rgb.xisf", plane: 0 }, { group: "OSC", path: "/m/rgb.xisf", plane: 2 },
      { group: "OSCNB", path: "C:\\masters\\nb.xisf", plane: 1 }, { group: "OSC", path: "rgb.xisf", plane: 1 },
      { group: "OSC", path: "/m/dir/", plane: 1 }, { group: "OSC", path: "", plane: 0 },
      { group: "OSC", path: null, plane: 0 }, { group: "OSC", path: "/m/\u00dc/\u03a8 rgb.xisf", plane: 1 },
      { group: "OSC", path: "/m/rgb.xisf" }, { group: "OSC", path: "/m/rgb.xisf", plane: null },
      { group: "OSC", path: "/m/rgb.xisf", plane: 1.5 }, { group: 5, path: "/m/rgb.xisf", plane: 0 },
      { group: "OSC", path: "a\\b/c.xisf", plane: 0 }, { group: "OSC", path: "/m/rgb.xisf", plane: true },
      { group: "OSC", path: 12, plane: 0 }, { group: "OSC", path: "/m/rgb.xisf", plane: "2" }
   ];
   srcRecords.forEach( ( r, i ) =>
      rec( "osc_channel_source/" + i, "osc_channel_source", [ r ], a => Pipeline.oscChannelSource( a[0] ) ) );

   // The channel records of a run: a composite or palette names the masters its channels come from.
   const srcChans = {
      R: { group: "OSC", path: "/m/rgb.xisf", plane: 0 }, G: { group: "OSC", path: "/m/rgb.xisf", plane: 1 },
      B: { group: "OSC", path: "/m/rgb.xisf", plane: 2 }, H: { group: "OSCNB", path: "/m/nb.xisf", plane: 0 },
      O: { group: "OSCNB", path: "/m/nb.xisf", plane: 1 }, L: { group: null, path: "/m/l.xisf", plane: null },
      S: { group: "OSCNB", path: "/n/other.xisf", plane: 0 }, X: { group: "OSC", path: "C:\\m\\rgb.xisf", plane: 0 },
      Q: { group: "OSC", path: "/m/rgb.xisf", plane: null }, D: { group: "OSC", path: "/m/rgb.xisf", plane: 0 },
      T: { group: "OSC", path: "/m/rgb.xisf", plane: "0" }, W: { group: "OSC", path: "/m/w.xisf", plane: 0 }
   };
   const srcKeySets = [ [ "R", "G", "B" ], [ "H", "O", "O" ], [ "S", "H", "O" ], [ "H", "S", "O" ], [ "L" ], [],
                        [ "L", "R" ], [ "R", "L", "G" ], [ "Z" ], [ "R", "R", "G" ], [ "H", "H" ], [ "R", "H", "O", "G" ],
                        [ "Q" ], [ "R", "D", "G" ], [ "X", "R" ], [ "S", "O" ], [ "R", "G", "B", "H", "O" ], [ "R", "Q" ],
                        [ "Q", "Q" ], [ "R", "T" ], [ "R", "W", "G" ], [ "H", "O", "S", "R" ] ];
   const srcPerPlane = [ false, true, null, 0, "yes" ];
   n = 0;
   srcKeySets.forEach( k => srcPerPlane.forEach( pp =>
      rec( "osc_source_text/" + ( n++ ), "osc_source_text", [ srcChans, k, pp ],
           a => Pipeline.oscSourceText( a[0], a[1], a[2] ) ) ) );
   rec( "osc_source_text/no-channels", "osc_source_text", [ {}, [ "R", "G", "B" ], false ],
        a => Pipeline.oscSourceText( a[0], a[1], a[2] ) );

   return cases;
};
