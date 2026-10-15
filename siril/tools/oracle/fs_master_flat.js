"use strict";
/*
 * Golden cases for loomsiril/master_flat.py: the decision part of pixinsight/script/lib/MasterFlat.js, lines 1-447
 * (constants, settings, describing frames, darks by IMAGETYP and by name, darks on the card, matching, names,
 * rejection, integration specs, the dark cache path). The plan, the execution and the engine (449 on) are not here.
 *
 * Fakes are built here from each case's table, and the table is recorded in the case so the Python suite rebuilds
 * the same fakes:
 *   store     { values, throwRead, throwWrite }  the settings store; every read and write it receives is logged
 *   headers   { path: { info, keywords } }        readHeader( path ): null when the path is not in the table
 *   keywords  { NAME: value }                     keyword( name ): null when the name is absent
 * The DataType arguments are logged by name. Undefined does not survive JSON: an undefined output is written as
 * "__undefined__" and an object key whose value is undefined is dropped; a null is a JS null.
 */
module.exports = function( js )
{
   const M = js.MasterFlat;
   const cases = {};
   const has = ( o, k ) => Object.prototype.hasOwnProperty.call( o, k );

   function norm( v )
   {
      if ( v === undefined ) return "__undefined__";
      if ( Array.isArray( v ) ) return v.map( norm );
      if ( v && typeof v === "object" )
      {
         const o = {};
         for ( const k of Object.keys( v ) ) if ( v[k] !== undefined ) o[k] = norm( v[k] );
         return o;
      }
      return v;
   }

   /* A constant of MasterFlat, by name. */
   function value( name )
   {
      cases["const/" + name] = { value: name, out: norm( M[name] ) };
   }

   /* A pure call: the result is what the function returns. `extra` adds fields the replay needs (ext). */
   function pure( key, name, args, extra )
   {
      const out = M[name].apply( M, structuredClone( args ) );
      cases[key] = Object.assign( { fn: name, args: norm( args ), out: norm( out ) }, extra || {} );
   }

   /* describe( path, keyword ): the keyword reader is built from the table. */
   function describeCase( key, path, kw )
   {
      const out = M.describe( path, keywordOf( kw ) );
      cases[key] = { fn: "describe", args: [ path ], keywords: kw, out: norm( out ) };
   }

   function keywordOf( kw )
   {
      return function( name ) { return has( kw, name ) ? kw[name] : null; };
   }

   function readerOf( headers )
   {
      return function( p )
      {
         if ( !has( headers, p ) ) return null;
         const e = headers[p];
         return { info: e.info ? {} : null, keyword: keywordOf( e.keywords ) };
      };
   }

   /* A call whose last argument is readHeader( path ), built from the `headers` table. */
   function withHeaders( key, name, args, headers )
   {
      const out = M[name].apply( M, structuredClone( args ).concat( [ readerOf( headers ) ] ) );
      cases[key] = { fn: name, args: norm( args ), headers: headers, out: norm( out ) };
   }

   function typeName( t )
   {
      return t === DataType_String ? "DataType_String" : t === DataType_Boolean ? "DataType_Boolean" : String( t );
   }

   function storeOf( spec, calls )
   {
      const data = Object.assign( {}, spec.values || {} );
      const rd = spec.throwRead || [], wr = spec.throwWrite || [];
      return {
         read( key, type )
         {
            calls.push( [ "read", key, typeName( type ) ] );
            if ( rd.includes( key ) ) throw new Error( "store read refused" );
            return has( data, key ) ? data[key] : null;
         },
         write( key, type, v )
         {
            calls.push( [ "write", key, typeName( type ), v ] );
            if ( wr.includes( key ) ) throw new Error( "store write refused" );
            data[key] = v;
         },
         dump() { return data; }
      };
   }

   /* loadSettings( store, ... ) and saveSettings( store, ... ): the result, the store afterwards and the calls. */
   function settingsCase( key, name, spec, rest )
   {
      const calls = [];
      const store = storeOf( spec, calls );
      const result = M[name].apply( M, [ store ].concat( structuredClone( rest || [] ) ) );
      cases[key] = { fn: name, store: spec, args: norm( rest || [] ),
                     out: norm( { result: result === undefined ? null : result, values: store.dump(), calls: calls } ) };
   }

   /* Described frames, as the plan and the card code build them. */
   function frameOf( path, over )
   {
      return Object.assign( { path: path, name: String( path ).split( "/" ).pop(), imagetyp: null, filter: null,
                              exposure: null, binning: null, gain: null, temp: null }, over );
   }
   function flatOf( over )
   {
      return frameOf( "/flats/F.xisf", Object.assign( { imagetyp: "Flat", filter: "Ha", exposure: 2, binning: 1,
                                                        gain: 120, temp: -10 }, over ) );
   }
   function darkOf( path, kind, over )
   {
      return Object.assign( frameOf( path, { imagetyp: kind == "master" ? "Master Dark" : "Dark", exposure: 2,
                                             binning: 1, gain: 120, temp: -10 } ), { kind: kind }, over || {} );
   }

   /* ---- constants ------------------------------------------------------------------------------- */
   [ "SETTINGS_DARKS", "SETTINGS_ONLY", "MIN_FLATS", "MIN_DARKS", "EXPOSURE_REL", "EXPOSURE_ABS",
     "TEMP_TOLERANCE", "GAIN_TOLERANCE" ].forEach( value );

   /* ---- settings: loadSettings ------------------------------------------------------------------ */
   const DARKS = M.SETTINGS_DARKS, ONLY = M.SETTINGS_ONLY;
   settingsCase( "load/empty_store", "loadSettings", {}, [] );
   settingsCase( "load/folder_and_only", "loadSettings", { values: { [DARKS]: "/darks/night", [ONLY]: true } }, [] );
   settingsCase( "load/folder_empty_string", "loadSettings", { values: { [DARKS]: "" } }, [] );
   settingsCase( "load/folder_whitespace", "loadSettings", { values: { [DARKS]: "   " } }, [] );
   settingsCase( "load/folder_zero", "loadSettings", { values: { [DARKS]: 0 } }, [] );
   settingsCase( "load/folder_false", "loadSettings", { values: { [DARKS]: false } }, [] );
   settingsCase( "load/folder_null", "loadSettings", { values: { [DARKS]: null } }, [] );
   settingsCase( "load/folder_number", "loadSettings", { values: { [DARKS]: 12 } }, [] );
   settingsCase( "load/folder_float", "loadSettings", { values: { [DARKS]: 1.5 } }, [] );
   settingsCase( "load/only_string_true", "loadSettings", { values: { [ONLY]: "true" } }, [] );
   settingsCase( "load/only_number_one", "loadSettings", { values: { [ONLY]: 1 } }, [] );
   settingsCase( "load/only_null", "loadSettings", { values: { [ONLY]: null } }, [] );
   settingsCase( "load/only_without_folder", "loadSettings", { values: { [ONLY]: true } }, [] );
   settingsCase( "load/read_refused_folder", "loadSettings", { values: { [ONLY]: true }, throwRead: [ DARKS ] }, [] );
   settingsCase( "load/read_refused_only", "loadSettings", { values: { [DARKS]: "/d", [ONLY]: true }, throwRead: [ ONLY ] }, [] );
   settingsCase( "load/extra_keys_ignored", "loadSettings", { values: { [DARKS]: "/d", [ONLY]: true, "Loom/other": "x" } }, [] );

   /* ---- settings: saveSettings ------------------------------------------------------------------ */
   settingsCase( "save/basic", "saveSettings", {}, [ "/darks", true ] );
   settingsCase( "save/overwrites", "saveSettings", { values: { [DARKS]: "/old", [ONLY]: true } }, [ "/new", false ] );
   settingsCase( "save/null_folder", "saveSettings", {}, [ null, false ] );
   settingsCase( "save/zero_folder_truthy_only", "saveSettings", {}, [ 0, "yes" ] );
   settingsCase( "save/number_folder_kept_raw", "saveSettings", {}, [ 5, 0 ] );
   settingsCase( "save/only_zero", "saveSettings", {}, [ "/d", 0 ] );
   settingsCase( "save/whitespace_folder_kept", "saveSettings", {}, [ "   ", true ] );
   settingsCase( "save/no_arguments", "saveSettings", {}, [] );
   settingsCase( "save/write_refused_folder", "saveSettings", { throwWrite: [ DARKS ] }, [ "/d", true ] );
   settingsCase( "save/write_refused_only", "saveSettings", { throwWrite: [ ONLY ] }, [ "/d", true ] );

   /* ---- active ---------------------------------------------------------------------------------- */
   pure( "active/folder_and_box", "active", [ "/d", true, false ] );
   pure( "active/cards_and_box", "active", [ null, true, true ] );
   pure( "active/stale_box_no_folder", "active", [ null, true, false ] );
   pure( "active/box_off", "active", [ "/d", false, true ] );
   pure( "active/empty_folder_no_cards", "active", [ "", true, false ] );
   pure( "active/empty_folder_cards", "active", [ "", true, true ] );
   pure( "active/string_truthy_box", "active", [ "/d", "yes", false ] );
   pure( "active/zero_box", "active", [ "/d", 0, true ] );
   pure( "active/empty_string_box", "active", [ "/d", "", true ] );
   pure( "active/number_folder", "active", [ 7, true, false ] );
   pure( "active/two_arguments", "active", [ "/d", true ] );

   /* ---- number ---------------------------------------------------------------------------------- */
   [ [ "plain", "12.5" ], [ "celsius", "-10.0 C" ], [ "seconds", "1.5e-3s" ], [ "no_digits", "abc" ],
     [ "null", null ], [ "prefix_e", "1e" ], [ "plus", "+5" ], [ "two_points", "1.2.3" ], [ "overflow", "1e400" ],
     [ "number_input", 120 ], [ "empty", "" ], [ "spaced_units", "  30.0 sec  " ], [ "bare_e", "e5" ],
     [ "bare_minus", "-" ], [ "double_minus", "--3" ], [ "trailing_minus", "5-" ], [ "upper_exponent", "1E+2" ],
     [ "zero", "0.0" ] ].forEach( ( [ n, t ] ) => pure( "number/" + n, "number", [ t ] ) );

   /* ---- describe -------------------------------------------------------------------------------- */
   describeCase( "describe/full", "/subs/Flat/F_01.xisf",
                 { IMAGETYP: "Flat Frame", FILTER: "Ha", EXPTIME: "2.0", XBINNING: "1", GAIN: "120", "CCD-TEMP": "-10.0" } );
   describeCase( "describe/sparse", "/x/y.fits", { IMAGETYP: "Flat" } );
   describeCase( "describe/no_keywords", "plain.xisf", {} );
   describeCase( "describe/exposure_fallback", "/d/a.xisf", { EXPOSURE: "30" } );
   describeCase( "describe/exptime_wins", "/d/a.xisf", { EXPTIME: "5", EXPOSURE: "30" } );
   describeCase( "describe/exptime_null_falls_back", "/d/a.xisf", { EXPTIME: null, EXPOSURE: "30" } );
   describeCase( "describe/temp_set_fallback", "/d/a.xisf", { "SET-TEMP": "-15" } );
   describeCase( "describe/temp_ccd_null_falls_back", "/d/a.xisf", { "CCD-TEMP": null, "SET-TEMP": "-5" } );
   describeCase( "describe/temp_ccd_wins", "/d/a.xisf", { "CCD-TEMP": "-9.5", "SET-TEMP": "-5" } );
   describeCase( "describe/unparseable_values", "/d/a.xisf", { EXPTIME: "n/a", XBINNING: "", GAIN: "x", "CCD-TEMP": "cold" } );
   describeCase( "describe/quoted_imagetyp_kept", "/d/a.xisf", { IMAGETYP: "'Dark Frame'", FILTER: "'Ha'" } );
   describeCase( "describe/root_path", "/", {} );

   /* ---- darkKind ------------------------------------------------------------------------------- */
   [ [ "imagetyp_raw", "x.xisf", "Dark Frame" ],
     [ "imagetyp_master", "masterDark_x.xisf", "Master Dark" ],
     [ "imagetyp_quoted", "d.xisf", "'Dark'" ],
     [ "flat_wins_over_dark_name", "Dark_flat.xisf", "Flat Frame" ],
     [ "light_with_dark_name", "dark_light.xisf", "Light" ],
     [ "bias_dark", "d.xisf", "Bias dark" ],
     [ "master_only_in_name", "masterdark.xisf", "Dark" ],
     [ "master_word_mid_name", "dark_master.xisf", "Dark" ],
     [ "offset_dark", "d.xisf", "Offset dark" ],
     [ "name_fallback_raw", "Dark_120s.xisf", null ],
     [ "name_fallback_master", "masterDark_120.xisf", null ],
     [ "name_fallback_flat", "FlatDark.xisf", "" ],
     [ "name_fallback_no_dark", "L_001.xisf", null ],
     [ "name_fallback_master_word_mid", "dark_master.xisf", null ],
     [ "blank_imagetyp_falls_to_name", "Dark_1.xisf", "   " ],
     [ "blank_imagetyp_master_name", "masterdark.xisf", "   " ],
     [ "null_name_dark_imagetyp", null, "Dark" ],
     [ "dark_flat_imagetyp", "d.xisf", "DARK FLAT" ],
     [ "zero_dark", "d.xisf", "Zero Dark" ],
     [ "light_frame_dark_name", "Dark.xisf", "Light Frame" ],
     [ "upper_master_dark", "x.xisf", "MASTER DARK" ],
     [ "lower_dark", "d.xisf", "dark" ],
     [ "flat_imagetyp_dark_name", "dark.xisf", "Flat" ],
     [ "name_light_word_no_imagetyp", "Light_dark_x.xisf", "" ],
     [ "name_fallback_zero_is_not_excluded", "Zero_dark.xisf", null ] ]
     .forEach( ( [ n, name, imagetyp ] ) => pure( "darkKind/" + n, "darkKind", [ name, imagetyp ] ) );

   /* ---- darksOf --------------------------------------------------------------------------------- */
   pure( "darksOf/mixed", "darksOf", [ [
      frameOf( "/c/masterDark_120.xisf", { imagetyp: "Master Dark", exposure: 120 } ),
      frameOf( "/c/Dark_120.xisf", { imagetyp: "Dark Frame", exposure: 120 } ),
      frameOf( "/c/Flat_1.xisf", { imagetyp: "Flat Frame", exposure: 1 } ),
      frameOf( "/c/L.xisf", { imagetyp: "Light" } ) ] ] );
   pure( "darksOf/empty", "darksOf", [ [] ] );
   pure( "darksOf/none_dark", "darksOf", [ [ frameOf( "/c/Flat.xisf", { imagetyp: "Flat" } ) ] ] );
   pure( "darksOf/name_only", "darksOf", [ [ frameOf( "/c/Dark_300.xisf", {} ) ] ] );

   /* ---- cardDarks ------------------------------------------------------------------------------- */
   const CARD_DARKS = [ { path: "/card/Dark/dark_one.fit" }, { path: "/card/Dark/dark_two.fit" }, { path: "/card/Dark/gone.fit" },
                        { path: "/card/Dark/M.fit" }, { path: "/card/Dark/unreadable.fit" } ];
   const CARD_HEADERS = {
      "/card/Dark/dark_one.fit": { info: true, keywords: { IMAGETYP: "Dark Frame", EXPTIME: "120", XBINNING: "1", GAIN: "120", "CCD-TEMP": "-10" } },
      "/card/Dark/dark_two.fit": { info: true, keywords: { IMAGETYP: "Dark", EXPTIME: "120", XBINNING: "1", GAIN: "120", "CCD-TEMP": "-10.5" } },
      "/card/Dark/M.fit": { info: true, keywords: { IMAGETYP: "Master Dark", EXPTIME: "120", XBINNING: "1", GAIN: "120", "CCD-TEMP": "-10" } },
      "/card/Dark/unreadable.fit": { info: false, keywords: { IMAGETYP: "Dark Frame", EXPTIME: "120" } } };
   withHeaders( "cardDarks/readable_missing_unreadable", "cardDarks", [ CARD_DARKS ], CARD_HEADERS );
   withHeaders( "cardDarks/light_and_flat_dropped", "cardDarks",
                [ [ { path: "/card/Light/L1.fit" }, { path: "/card/Flat/F1.fit" } ] ],
                { "/card/Light/L1.fit": { info: true, keywords: { IMAGETYP: "Light", EXPTIME: "60" } },
                  "/card/Flat/F1.fit": { info: true, keywords: { IMAGETYP: "Flat", EXPTIME: "1" } } } );
   withHeaders( "cardDarks/empty", "cardDarks", [ [] ], {} );
   withHeaders( "cardDarks/name_fallback_no_imagetyp", "cardDarks",
                [ [ { path: "/card/masterDark_x.fit" }, { path: "/card/Dark_300.fit" } ] ],
                { "/card/masterDark_x.fit": { info: true, keywords: { EXPTIME: "300" } },
                  "/card/Dark_300.fit": { info: true, keywords: { EXPTIME: "300" } } } );

   /* ---- card darks against the flats ------------------------------------------------------------ */
   const FLAT_HEADERS = {
      "/card/F1.fit": { info: true, keywords: { IMAGETYP: "Flat", FILTER: "Ha", EXPTIME: "2", XBINNING: "1", GAIN: "120", "CCD-TEMP": "-10" } },
      "/card/F2.fit": { info: true, keywords: { IMAGETYP: "Flat", FILTER: "Ha", EXPTIME: "2", XBINNING: "1", GAIN: "120", "CCD-TEMP": "-9.5" } } };
   const FLAT_FRAMES = [ { path: "/card/F1.fit" }, { path: "/card/F2.fit" }, { path: "/card/F_gone.fit" } ];
   withHeaders( "readCardDarks/matched", "readCardDarks", [ CARD_DARKS, FLAT_FRAMES ],
                Object.assign( {}, CARD_HEADERS, FLAT_HEADERS ) );
   withHeaders( "readCardDarks/temperature_far", "readCardDarks", [ CARD_DARKS, FLAT_FRAMES ],
                Object.assign( {}, CARD_HEADERS, FLAT_HEADERS,
                               { "/card/F1.fit": { info: true, keywords: { IMAGETYP: "Flat", EXPTIME: "2", XBINNING: "1", GAIN: "120", "CCD-TEMP": "+20" } } } ) );
   withHeaders( "readCardDarks/no_darks", "readCardDarks", [ [], FLAT_FRAMES ], FLAT_HEADERS );

   pure( "cardDarkMatch/master_matched", "cardDarkMatch",
         [ [ flatOf( { path: "/f/1.xisf" } ) ], [ darkOf( "/d/m.xisf", "master", { temp: -10.5 } ) ] ] );
   pure( "cardDarkMatch/raw_pair_matched", "cardDarkMatch",
         [ [ flatOf( {} ) ], [ darkOf( "/d/r1.xisf", "raw" ), darkOf( "/d/r2.xisf", "raw", { temp: -9.5 } ) ] ] );
   pure( "cardDarkMatch/single_raw_not_enough", "cardDarkMatch",
         [ [ flatOf( {} ) ], [ darkOf( "/d/r1.xisf", "raw" ) ] ] );
   pure( "cardDarkMatch/no_flats", "cardDarkMatch", [ [], [ darkOf( "/d/m.xisf", "master" ) ] ] );
   pure( "cardDarkMatch/unused_dark_left_out", "cardDarkMatch",
         [ [ flatOf( {} ) ], [ darkOf( "/d/m.xisf", "master" ), darkOf( "/d/far.xisf", "master", { exposure: 60 } ) ] ] );
   pure( "cardDarkMatch/exposures_ascending", "cardDarkMatch",
         [ [ flatOf( { exposure: 2 } ), flatOf( { path: "/f/b.xisf", exposure: 30 } ) ],
           [ darkOf( "/d/a.xisf", "master", { exposure: 30 } ), darkOf( "/d/b.xisf", "master", { exposure: 2 } ) ] ] );

   pure( "darkSourceOf/card_found", "darkSourceOf",
         [ { found: true, used: [ { path: "/d/a" }, { path: "/d/b" } ], exposures: [ 2, 30 ] }, "/darks" ] );
   pure( "darkSourceOf/not_found_folder", "darkSourceOf", [ { found: false, used: [], exposures: [] }, "/darks" ] );
   pure( "darkSourceOf/no_match_no_folder", "darkSourceOf", [ null, null ] );
   pure( "darkSourceOf/no_match_folder", "darkSourceOf", [ null, "/d" ] );

   /* ---- cardDarksUi ----------------------------------------------------------------------------- */
   pure( "ui/found_folder_editable", "cardDarksUi", [ true, "/d", true, true ] );
   pure( "ui/not_found_no_folder", "cardDarksUi", [ false, null, true, true ] );
   pure( "ui/not_found_folder_checked", "cardDarksUi", [ false, "/d", true, true ] );
   pure( "ui/not_editable", "cardDarksUi", [ true, "/d", true, false ] );
   pure( "ui/found_box_off", "cardDarksUi", [ true, "/d", false, true ] );
   pure( "ui/no_folder_clear_disabled", "cardDarksUi", [ false, "", true, true ] );
   pure( "ui/found_no_folder_clear_disabled", "cardDarksUi", [ true, null, true, true ] );

   /* ---- sameExposure ---------------------------------------------------------------------------- */
   pure( "same/one_percent_inside", "sameExposure", [ 10, 10.1 ] );
   pure( "same/one_percent_just_beyond", "sameExposure", [ 10, 10.11 ] );
   pure( "same/one_percent_reversed", "sameExposure", [ 10.1, 10 ] );
   pure( "same/long_one_percent_inside", "sameExposure", [ 300, 303 ] );
   pure( "same/long_one_percent_beyond", "sameExposure", [ 300, 304 ] );
   pure( "same/short_absolute_inside", "sameExposure", [ 0.0005, 0.0012 ] );
   pure( "same/short_absolute_beyond", "sameExposure", [ 0.0005, 0.0020 ] );
   pure( "same/equal", "sameExposure", [ 2, 2 ] );
   pure( "same/null_first", "sameExposure", [ null, 2 ] );
   pure( "same/null_second", "sameExposure", [ 2, null ] );
   pure( "same/both_null", "sameExposure", [ null, null ] );

   /* ---- compatible ------------------------------------------------------------------------------ */
   pure( "compatible/null_first", "compatible", [ null, 5, 0 ] );
   pure( "compatible/null_second", "compatible", [ 5, null, 0 ] );
   pure( "compatible/both_null", "compatible", [ null, null, 0 ] );
   pure( "compatible/within", "compatible", [ 3, 3.4, 0.5 ] );
   pure( "compatible/at_tolerance", "compatible", [ 120, 120.5, 0.5 ] );
   pure( "compatible/beyond", "compatible", [ 3, 3.6, 0.5 ] );
   pure( "compatible/zero_tolerance_equal", "compatible", [ 1, 1, 0 ] );
   pure( "compatible/zero_tolerance_differs", "compatible", [ 1, 1.0000001, 0 ] );

   /* ---- darkSuits ------------------------------------------------------------------------------- */
   pure( "suits/temp_at_three_degrees", "darkSuits", [ flatOf( { temp: -10 } ), darkOf( "/d/a", "raw", { temp: -13 } ) ] );
   pure( "suits/temp_just_beyond", "darkSuits", [ flatOf( { temp: -10 } ), darkOf( "/d/a", "raw", { temp: -13.5 } ) ] );
   pure( "suits/temp_flat_missing", "darkSuits", [ flatOf( { temp: null } ), darkOf( "/d/a", "raw" ) ] );
   pure( "suits/temp_dark_missing", "darkSuits", [ flatOf( {} ), darkOf( "/d/a", "raw", { temp: null } ) ] );
   pure( "suits/binning_differs", "darkSuits", [ flatOf( { binning: 1 } ), darkOf( "/d/a", "raw", { binning: 2 } ) ] );
   pure( "suits/binning_one_missing", "darkSuits", [ flatOf( { binning: null } ), darkOf( "/d/a", "raw", { binning: 2 } ) ] );
   pure( "suits/gain_at_half", "darkSuits", [ flatOf( { gain: 120 } ), darkOf( "/d/a", "raw", { gain: 120.5 } ) ] );
   pure( "suits/gain_beyond_half", "darkSuits", [ flatOf( { gain: 120 } ), darkOf( "/d/a", "raw", { gain: 120.6 } ) ] );
   pure( "suits/gain_missing", "darkSuits", [ flatOf( { gain: null } ), darkOf( "/d/a", "raw", { gain: 300 } ) ] );
   pure( "suits/exposure_differs", "darkSuits", [ flatOf( { exposure: 2 } ), darkOf( "/d/a", "raw", { exposure: 2.1 } ) ] );
   pure( "suits/all_agree", "darkSuits", [ flatOf( {} ), darkOf( "/d/a", "raw" ) ] );
   pure( "suits/binning_and_gain_unstated", "darkSuits",
         [ flatOf( { binning: null, gain: null } ), darkOf( "/d/a", "master", { binning: null, gain: null } ) ] );

   /* ---- chooseDark ------------------------------------------------------------------------------ */
   pure( "choose/master_over_raw", "chooseDark", [ flatOf( {} ),
         [ darkOf( "/d/m.xisf", "master" ), darkOf( "/d/r1.xisf", "raw" ), darkOf( "/d/r2.xisf", "raw" ) ] ] );
   pure( "choose/raw_pair", "chooseDark", [ flatOf( {} ), [ darkOf( "/d/r2.xisf", "raw" ), darkOf( "/d/r1.xisf", "raw" ) ] ] );
   pure( "choose/raw_single_none", "chooseDark", [ flatOf( {} ), [ darkOf( "/d/r1.xisf", "raw" ) ] ] );
   pure( "choose/raw_groups_closest_exposure", "chooseDark", [ flatOf( { exposure: 300 } ), [
         darkOf( "/d/a1.xisf", "raw", { exposure: 300 } ), darkOf( "/d/a2.xisf", "raw", { exposure: 300 } ),
         darkOf( "/d/b1.xisf", "raw", { exposure: 303 } ), darkOf( "/d/b2.xisf", "raw", { exposure: 303 } ) ] ] );
   pure( "choose/raw_tie_larger_group_wins", "chooseDark", [ flatOf( { binning: null } ), [
         darkOf( "/d/x1.xisf", "raw", { binning: 1 } ), darkOf( "/d/x2.xisf", "raw", { binning: 1 } ),
         darkOf( "/d/y1.xisf", "raw", { binning: 2 } ), darkOf( "/d/y2.xisf", "raw", { binning: 2 } ),
         darkOf( "/d/y3.xisf", "raw", { binning: 2 } ) ] ] );
   pure( "choose/raw_tie_smaller_key", "chooseDark", [ flatOf( { binning: null } ), [
         darkOf( "/d/y1.xisf", "raw", { binning: 2 } ), darkOf( "/d/y2.xisf", "raw", { binning: 2 } ),
         darkOf( "/d/x1.xisf", "raw", { binning: 1 } ), darkOf( "/d/x2.xisf", "raw", { binning: 1 } ) ] ] );
   pure( "choose/raw_group_temp_average", "chooseDark", [ flatOf( { temp: -11 } ), [
         darkOf( "/d/r1.xisf", "raw", { temp: -10 } ), darkOf( "/d/r2.xisf", "raw", { temp: -12 } ) ] ] );
   pure( "choose/master_closest_exposure", "chooseDark", [ flatOf( { exposure: 300 } ), [
         darkOf( "/d/m303.xisf", "master", { exposure: 303 } ), darkOf( "/d/m300.xisf", "master", { exposure: 300 } ) ] ] );
   pure( "choose/master_tie_by_path", "chooseDark", [ flatOf( {} ), [
         darkOf( "/d/zz.xisf", "master" ), darkOf( "/d/aa.xisf", "master" ) ] ] );
   pure( "choose/master_closest_temperature", "chooseDark", [ flatOf( { temp: -10 } ), [
         darkOf( "/d/t125.xisf", "master", { temp: -12.5 } ), darkOf( "/d/t10.xisf", "master", { temp: -10 } ) ] ] );
   pure( "choose/none_suited", "chooseDark", [ flatOf( {} ), [ darkOf( "/d/a.xisf", "raw", { exposure: 60 } ),
                                                                darkOf( "/d/b.xisf", "raw", { exposure: 60 } ) ] ] );
   pure( "choose/no_darks", "chooseDark", [ flatOf( {} ), [] ] );
   pure( "choose/unsuited_master_raw_used", "chooseDark", [ flatOf( {} ), [
         darkOf( "/d/m.xisf", "master", { exposure: 60 } ), darkOf( "/d/r1.xisf", "raw" ), darkOf( "/d/r2.xisf", "raw" ) ] ] );
   pure( "choose/flat_temp_missing", "chooseDark", [ flatOf( { temp: null } ), [ darkOf( "/d/m.xisf", "master" ) ] ] );
   pure( "choose/raw_null_binning_key", "chooseDark", [ flatOf( { binning: null } ), [
         darkOf( "/d/n1.xisf", "raw", { binning: null } ), darkOf( "/d/n2.xisf", "raw", { binning: null } ) ] ] );
   pure( "choose/raw_gain_split_tie", "chooseDark", [ flatOf( { gain: null } ), [
         darkOf( "/d/g121a.xisf", "raw", { gain: 121 } ), darkOf( "/d/g121b.xisf", "raw", { gain: 121 } ),
         darkOf( "/d/g120a.xisf", "raw", { gain: 120 } ), darkOf( "/d/g120b.xisf", "raw", { gain: 120 } ) ] ] );
   pure( "choose/raw_paths_sorted", "chooseDark", [ flatOf( {} ), [
         darkOf( "/d/z.xisf", "raw" ), darkOf( "/d/a.xisf", "raw" ), darkOf( "/d/m.xisf", "raw" ) ] ] );
   pure( "choose/raw_fractional_exposure_key", "chooseDark", [ flatOf( { exposure: 0.0025 } ), [
         darkOf( "/d/f1.xisf", "raw", { exposure: 0.0025 } ), darkOf( "/d/f2.xisf", "raw", { exposure: 0.0025 } ) ] ] );

   /* ---- rawGroupKey ----------------------------------------------------------------------------- */
   pure( "key/full", "rawGroupKey", [ { exposure: 300, binning: 2, gain: 120 } ] );
   pure( "key/nulls", "rawGroupKey", [ { exposure: null, binning: null, gain: null } ] );
   pure( "key/fractional_exposure_rounds_half_up", "rawGroupKey", [ { exposure: 0.0025, binning: 1, gain: 120 } ] );
   pure( "key/gain_half_rounds_up", "rawGroupKey", [ { exposure: 2, binning: 1, gain: 120.5 } ] );
   pure( "key/gain_negative_half", "rawGroupKey", [ { exposure: 2, binning: 1, gain: -2.5 } ] );
   pure( "key/temperature_not_in_key", "rawGroupKey", [ { exposure: 2, binning: 1, gain: 120, temp: -10 } ] );
   pure( "key/tiny_exposure", "rawGroupKey", [ { exposure: 0.0001, binning: 1, gain: 120 } ] );
   pure( "key/binning_half_rounds_up", "rawGroupKey", [ { exposure: 2, binning: 1.5, gain: 120 } ] );

   /* ---- safeName -------------------------------------------------------------------------------- */
   [ [ "plain", "Ha" ], [ "symbols", "Ha+OIII" ], [ "spaces_edges", "  S II  " ], [ "leading_dots", "..._L." ],
     [ "all_bad", "%%%" ], [ "null", null ], [ "empty", "" ], [ "non_ascii", "Hα" ], [ "dots_only", "..." ],
     [ "number", 5 ], [ "hyphen_underscore", "A-b_c" ], [ "surrogate_pair", "🌙x" ],
     [ "path_separator", "rgb/Red" ], [ "boolean", false ] ]
     .forEach( ( [ n, t ] ) => pure( "safeName/" + n, "safeName", [ t ] ) );

   /* ---- fileName (the JS writes .xisf; the Python port takes ext) ------------------------------- */
   [ [ "plain", "Ha", null ], [ "binning_two", "Ha", 2 ], [ "odd_filter_binning_one", "S II", 1 ],
     [ "null_filter", null, null ], [ "binning_zero", "L", 0 ], [ "fractional_binning", "L", 1.5 ] ]
     .forEach( ( [ n, f, b ] ) => pure( "fileName/" + n, "fileName", [ f, b ], { ext: ".xisf" } ) );

   /* ---- rejectionFor and the integration specs -------------------------------------------------- */
   [ 0, 1, 2, 3, 7, 8, 30 ].forEach( n => pure( "rejectionFor/" + n, "rejectionFor", [ n ] ) );
   pure( "darkIntegrationSpec/two", "darkIntegrationSpec", [ 2 ] );
   pure( "darkIntegrationSpec/eight", "darkIntegrationSpec", [ 8 ] );
   pure( "flatIntegrationSpec/one", "flatIntegrationSpec", [ 1 ] );
   pure( "flatIntegrationSpec/three", "flatIntegrationSpec", [ 3 ] );
   pure( "flatIntegrationSpec/ten", "flatIntegrationSpec", [ 10 ] );

   /* ---- darkCachePath (the JS writes .xisf) ----------------------------------------------------- */
   const SRC_A = { path: "/card/Dark/dark_one.fit", size: 4096, modified: "2026-09-18T20:00:00.000Z" };
   const SRC_B = { path: "/card/Dark/dark_two.fit", size: 4100, modified: "2026-09-18T20:02:00.000Z" };
   pure( "cache/one_source", "darkCachePath", [ { key: "e2000b1g120" }, [ SRC_A ], "/work/cache" ], { ext: ".xisf" } );
   pure( "cache/two_sources", "darkCachePath", [ { key: "e2000b1g120" }, [ SRC_A, SRC_B ], "/work/cache" ], { ext: ".xisf" } );
   pure( "cache/null_size_and_modified", "darkCachePath",
         [ { key: "e2000b1g120" }, [ { path: "/card/x.fit", size: null, modified: null } ], "/work/cache" ], { ext: ".xisf" } );
   pure( "cache/no_sources", "darkCachePath", [ { key: "e-b-g-" }, [], "/work/cache" ], { ext: ".xisf" } );
   pure( "cache/non_ascii_path", "darkCachePath",
         [ { key: "e2000b1g120" }, [ { path: "/card/Dünkel.fit", size: 1, modified: "x" } ], "/work/cache" ], { ext: ".xisf" } );
   pure( "cache/trailing_slash_dir", "darkCachePath", [ { key: "e2000b1g120" }, [ SRC_A ], "/work/cache/" ], { ext: ".xisf" } );
   pure( "cache/zero_size", "darkCachePath",
         [ { key: "e2000b1g120" }, [ { path: "/card/z.fit", size: 0, modified: "t" } ], "/work/cache" ], { ext: ".xisf" } );
   pure( "cache/large_size", "darkCachePath",
         [ { key: "e2000b1g120" }, [ { path: "/card/big.fit", size: 1.5e21, modified: "t" } ], "/work/cache" ], { ext: ".xisf" } );

   return cases;
};
