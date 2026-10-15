"use strict";
/*
 * Golden cases for loomsiril/master_flat.py: the plan and the report of pixinsight/script/lib/MasterFlat.js, lines
 * 449-1082 that are not the engine: buildPlan and darkShortfall (462-550), the import-log lines (masterFlatNum 982,
 * frameLine 985-991, darkSummaryLines 994-1015, planLines 1017-1037) and the folder scan (SCAN_DEPTH 1050,
 * masterFlatScanFolderEntry 1052-1070, scanFolder 1072-1082).
 *
 * Fakes, recorded in each case so the Python suite rebuilds them:
 *   headers   { path: { info, keywords } }   readHeader( path ): null when the path is not in the table
 *   keywords  { NAME: value }                keyword( name ): null when the name is absent
 *   tree      { dir: [ entry ] }             the folder listing; a directory key is a folder that exists.
 *             An entry is { name, isDirectory, isSymbolicLink, size, modified } (modified: ISO time of a file).
 *             Util.findEntries( dir + "/*" ) returns the entries of that dir as the real one does (name, isDirectory,
 *             isFile, isSymbolicLink, size), in name order (code units): the port lists the names sorted too.
 *             File.directoryExists and `new FileInfo( path ).lastModified` are faked from the same tree.
 * Util.findEntries, File.directoryExists and FileInfo are replaced only while a scan case runs, then restored.
 * Undefined does not survive JSON: an undefined output is written as "__undefined__" and an object key whose value
 * is undefined is dropped; a null is a JS null.
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

   /* A pure call: the result is what the function returns. `extra` adds fields the replay needs (ext). */
   function pure( key, name, args, extra )
   {
      const out = M[name].apply( M, structuredClone( args ) );
      cases[key] = Object.assign( { fn: name, args: norm( args ), out: norm( out ) }, extra || {} );
   }

   /* A call of a plain function of the lib (not on MasterFlat): masterFlatNum is a global of the sloppy lib file. */
   function global_( key, name, args )
   {
      const out = globalThis[name].apply( null, structuredClone( args ) );
      cases[key] = { fn: name, args: norm( args ), out: norm( out ) };
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

   /* Described frames, as the plan builds them (the same shape as fs_master_flat.js). */
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
   function flatAt( path, over ) { return flatOf( Object.assign( { path: path }, over ) ); }

   const ATCARD = "at the ASIAIR card";
   const DEST = "/work/Flat";
   const plan = ( key, flats, darks, where, extra ) =>
   {
      const args = [ flats, darks, DEST ];
      if ( where !== undefined ) args.push( where );
      pure( key, "buildPlan", args, Object.assign( { ext: ".xisf" }, extra || {} ) );
   };

   /* ---- buildPlan: one job per filter and binning ----------------------------------------------- */
   plan( "plan/one_filter_one_exposure",
         [ flatAt( "/f/a.xisf" ), flatAt( "/f/b.xisf" ), flatAt( "/f/c.xisf" ) ],
         [ darkOf( "/d/m.xisf", "master" ) ] );
   plan( "plan/two_exposures_two_darks",
         [ flatAt( "/f/a.xisf", { exposure: 2 } ), flatAt( "/f/b.xisf", { exposure: 2 } ),
           flatAt( "/f/c.xisf", { exposure: 30 } ), flatAt( "/f/d.xisf", { exposure: 30 } ) ],
         [ darkOf( "/d/r2a.xisf", "raw", { exposure: 2 } ), darkOf( "/d/r2b.xisf", "raw", { exposure: 2 } ),
           darkOf( "/d/r30a.xisf", "raw", { exposure: 30 } ), darkOf( "/d/r30b.xisf", "raw", { exposure: 30 } ) ] );
   plan( "plan/parts_listed_in_reverse_path_order",
         [ flatAt( "/f/z.xisf", { exposure: 30 } ), flatAt( "/f/y.xisf", { exposure: 2 } ),
           flatAt( "/f/x.xisf", { exposure: 30 } ), flatAt( "/f/w.xisf", { exposure: 2 } ) ],
         [ darkOf( "/d/a.xisf", "master", { exposure: 2 } ), darkOf( "/d/b.xisf", "master", { exposure: 30 } ) ] );
   plan( "plan/part_without_dark_skips_job",
         [ flatAt( "/f/a.xisf", { exposure: 2 } ), flatAt( "/f/b.xisf", { exposure: 2 } ),
           flatAt( "/f/c.xisf", { exposure: 30 } ), flatAt( "/f/d.xisf", { exposure: 30 } ) ],
         [ darkOf( "/d/r2a.xisf", "raw", { exposure: 2 } ), darkOf( "/d/r2b.xisf", "raw", { exposure: 2 } ) ] );
   plan( "plan/no_darks_with_where",
         [ flatAt( "/f/a.xisf" ), flatAt( "/f/b.xisf" ) ], [], ATCARD );
   plan( "plan/no_darks_where_omitted",
         [ flatAt( "/f/a.xisf" ), flatAt( "/f/b.xisf" ) ], [] );
   plan( "plan/no_darks_where_null",
         [ flatAt( "/f/a.xisf" ), flatAt( "/f/b.xisf" ) ], [], null );
   plan( "plan/no_darks_where_empty",
         [ flatAt( "/f/a.xisf" ), flatAt( "/f/b.xisf" ) ], [], "" );
   plan( "plan/flats_without_temperature",
         [ flatAt( "/f/a.xisf", { temp: null } ), flatAt( "/f/b.xisf", { temp: null } ) ],
         [ darkOf( "/d/r1.xisf", "raw" ), darkOf( "/d/r2.xisf", "raw" ) ], ATCARD );
   plan( "plan/darks_without_exposure",
         [ flatAt( "/f/a.xisf" ), flatAt( "/f/b.xisf" ) ],
         [ darkOf( "/d/n1.xisf", "raw", { exposure: null } ), darkOf( "/d/n2.xisf", "raw", { exposure: null } ) ], ATCARD );
   plan( "plan/many_dark_exposures_truncated",
         [ flatAt( "/f/a.xisf", { exposure: 300 } ), flatAt( "/f/b.xisf", { exposure: 300 } ) ],
         [ 1, 2, 3, 4, 5, 6, 7, 8, 9, 10 ].map( ( s ) => darkOf( "/d/e" + s + ".xisf", "raw", { exposure: s } ) ), ATCARD );
   plan( "plan/flats_without_exposure",
         [ flatAt( "/f/a.xisf", { exposure: null } ), flatAt( "/f/b.xisf", { exposure: null } ) ],
         [ darkOf( "/d/r1.xisf", "raw" ), darkOf( "/d/r2.xisf", "raw" ) ], ATCARD );
   plan( "plan/one_flat_only",
         [ flatAt( "/f/a.xisf" ) ], [ darkOf( "/d/m.xisf", "master" ) ] );
   plan( "plan/no_flats", [], [ darkOf( "/d/m.xisf", "master" ) ] );
   plan( "plan/two_binnings_of_one_filter",
         [ flatAt( "/f/a.xisf", { binning: 1 } ), flatAt( "/f/b.xisf", { binning: 1 } ),
           flatAt( "/f/c.xisf", { binning: 2 } ), flatAt( "/f/d.xisf", { binning: 2 } ) ],
         [ darkOf( "/d/m1.xisf", "master", { binning: 1 } ), darkOf( "/d/m2.xisf", "master", { binning: 2 } ) ] );
   plan( "plan/binning_mismatch_no_dark",
         [ flatAt( "/f/a.xisf", { binning: 1 } ), flatAt( "/f/b.xisf", { binning: 1 } ) ],
         [ darkOf( "/d/m2.xisf", "master", { binning: 2 } ) ], ATCARD );
   plan( "plan/two_filters_no_binning_suffix",
         [ flatAt( "/f/a.xisf", { filter: "OIII" } ), flatAt( "/f/b.xisf", { filter: "OIII" } ),
           flatAt( "/f/c.xisf", { filter: "Ha" } ), flatAt( "/f/d.xisf", { filter: "Ha" } ) ],
         [ darkOf( "/d/m.xisf", "master" ) ] );
   plan( "plan/case_clashing_filters_with_binning",
         [ flatAt( "/f/a.xisf", { filter: "Ha" } ), flatAt( "/f/b.xisf", { filter: "Ha" } ),
           flatAt( "/f/c.xisf", { filter: "HA" } ), flatAt( "/f/d.xisf", { filter: "HA" } ) ],
         [ darkOf( "/d/m.xisf", "master" ) ] );
   plan( "plan/case_clashing_filters_without_binning",
         [ flatAt( "/f/a.xisf", { filter: "Ha", binning: null } ), flatAt( "/f/b.xisf", { filter: "Ha", binning: null } ),
           flatAt( "/f/c.xisf", { filter: "HA", binning: null } ), flatAt( "/f/d.xisf", { filter: "HA", binning: null } ) ],
         [ darkOf( "/d/m.xisf", "master", { binning: null } ) ] );
   plan( "plan/safe_name_clash",
         [ flatAt( "/f/a.xisf", { filter: "S II" } ), flatAt( "/f/b.xisf", { filter: "S II" } ),
           flatAt( "/f/c.xisf", { filter: "S_II" } ), flatAt( "/f/d.xisf", { filter: "S_II" } ) ],
         [ darkOf( "/d/m.xisf", "master" ) ] );
   plan( "plan/duplicate_flat_path_counted_once",
         [ flatAt( "/f/a.xisf" ), flatAt( "/f/a.xisf" ), flatAt( "/f/b.xisf" ) ],
         [ darkOf( "/d/m.xisf", "master" ) ] );
   plan( "plan/gain_split_into_two_parts",
         [ flatAt( "/f/a.xisf", { gain: 120 } ), flatAt( "/f/b.xisf", { gain: 120 } ), flatAt( "/f/c.xisf", { gain: 300 } ) ],
         [ darkOf( "/d/m120.xisf", "master", { gain: 120 } ), darkOf( "/d/m300.xisf", "master", { gain: 300 } ) ] );
   plan( "plan/gain_split_one_part_missing_dark",
         [ flatAt( "/f/a.xisf", { gain: 120 } ), flatAt( "/f/b.xisf", { gain: 120 } ), flatAt( "/f/c.xisf", { gain: 300 } ) ],
         [ darkOf( "/d/m120.xisf", "master", { gain: 120 } ) ], ATCARD );
   plan( "plan/gain_null_joins_a_part",
         [ flatAt( "/f/a.xisf", { gain: null } ), flatAt( "/f/b.xisf", { gain: 120 } ) ],
         [ darkOf( "/d/m120.xisf", "master", { gain: 120 } ) ] );
   plan( "plan/exposure_within_one_percent_joins",
         [ flatAt( "/f/a.xisf", { exposure: 2 } ), flatAt( "/f/b.xisf", { exposure: 2.01 } ) ],
         [ darkOf( "/d/m.xisf", "master", { exposure: 2 } ) ] );
   plan( "plan/null_exposure_then_exposure",
         [ flatAt( "/f/a.xisf", { exposure: null } ), flatAt( "/f/b.xisf", { exposure: 2 } ), flatAt( "/f/c.xisf", { exposure: 2 } ) ],
         [ darkOf( "/d/r1.xisf", "raw" ), darkOf( "/d/r2.xisf", "raw" ) ], ATCARD );
   plan( "plan/job_order_by_filter_name",
         [ flatAt( "/f/a.xisf", { filter: "Sii" } ), flatAt( "/f/b.xisf", { filter: "Sii" } ),
           flatAt( "/f/c.xisf", { filter: "ha" } ), flatAt( "/f/d.xisf", { filter: "ha" } ),
           flatAt( "/f/e.xisf", { filter: "Oiii" } ), flatAt( "/f/f.xisf", { filter: "Oiii" } ) ],
         [] );
   plan( "plan/master_dark_wins_over_raw",
         [ flatAt( "/f/a.xisf" ), flatAt( "/f/b.xisf" ) ],
         [ darkOf( "/d/r1.xisf", "raw" ), darkOf( "/d/r2.xisf", "raw" ), darkOf( "/d/m.xisf", "master" ) ] );
   plan( "plan/null_filter_named_unknown",
         [ flatAt( "/f/a.xisf", { filter: null } ), flatAt( "/f/b.xisf", { filter: null } ) ],
         [ darkOf( "/d/m.xisf", "master" ) ] );
   plan( "plan/dark_with_no_matching_flat_temp_reports_folder",
         [ flatAt( "/f/a.xisf", { temp: 5 } ), flatAt( "/f/b.xisf", { temp: 5 } ) ],
         [ darkOf( "/d/r1.xisf", "raw" ), darkOf( "/d/r2.xisf", "raw" ) ], ATCARD );
   plan( "plan/raw_group_with_larger_count_wins",
         [ flatAt( "/f/a.xisf" ), flatAt( "/f/b.xisf" ) ],
         [ darkOf( "/d/g1a.xisf", "raw" ), darkOf( "/d/g1b.xisf", "raw" ),
           darkOf( "/d/g2a.xisf", "raw", { gain: 240 } ), darkOf( "/d/g2b.xisf", "raw", { gain: 240 } ),
           darkOf( "/d/g2c.xisf", "raw", { gain: 240 } ) ] );

   /* ---- darkShortfall --------------------------------------------------------------------------- */
   pure( "shortfall/no_darks_default", "darkShortfall", [ flatOf( {} ), [], null ] );
   pure( "shortfall/no_darks_where", "darkShortfall", [ flatOf( {} ), [], ATCARD ] );
   pure( "shortfall/no_darks_empty_where", "darkShortfall", [ flatOf( {} ), [], "" ] );
   pure( "shortfall/darks_listed_once_each", "darkShortfall", [ flatOf( { temp: -10 } ),
         [ darkOf( "/d/a", "raw", { exposure: 2 } ), darkOf( "/d/b", "raw", { exposure: 30 } ),
           darkOf( "/d/c", "raw", { exposure: 2 } ) ] ] );
   pure( "shortfall/flat_without_temperature", "darkShortfall", [ flatOf( { temp: null } ), [ darkOf( "/d/a", "raw", { exposure: 2 } ) ] ] );
   pure( "shortfall/temperature_rounds_half_up", "darkShortfall", [ flatOf( { temp: -10.25 } ), [ darkOf( "/d/a", "raw" ) ] ] );
   pure( "shortfall/exposure_rounds_to_millisecond", "darkShortfall", [ flatOf( {} ), [ darkOf( "/d/a", "raw", { exposure: 1.23456 } ) ] ] );
   pure( "shortfall/dark_without_exposure", "darkShortfall", [ flatOf( {} ), [ darkOf( "/d/a", "raw", { exposure: null } ) ] ] );
   pure( "shortfall/eight_exposures_no_ellipsis", "darkShortfall", [ flatOf( {} ),
         [ 1, 2, 3, 4, 5, 6, 7, 8 ].map( ( s ) => darkOf( "/d/e" + s, "raw", { exposure: s } ) ) ] );
   pure( "shortfall/nine_exposures_ellipsis", "darkShortfall", [ flatOf( {} ),
         [ 1, 2, 3, 4, 5, 6, 7, 8, 9 ].map( ( s ) => darkOf( "/d/e" + s, "raw", { exposure: s } ) ) ] );

   /* ---- masterFlatNum (a global of the sloppy lib file, not on MasterFlat) ---------------------- */
   global_( "num/null", "masterFlatNum", [ null, 1000 ] );
   global_( "num/zero", "masterFlatNum", [ 0, 1000 ] );
   global_( "num/negative_zero_prints_zero", "masterFlatNum", [ -0.0001, 1000 ] );
   global_( "num/exposure_milliseconds", "masterFlatNum", [ 1.23456, 1000 ] );
   global_( "num/half_rounds_up", "masterFlatNum", [ -10.25, 10 ] );
   global_( "num/integer_digits", "masterFlatNum", [ 120, 1 ] );
   global_( "num/large", "masterFlatNum", [ 1e21, 1 ] );

   /* ---- frameLine ------------------------------------------------------------------------------- */
   pure( "line/frame_full", "frameLine", [ { name: "F_01.xisf", filter: "'Ha'", exposure: 2, binning: 1,
                                             gain: 120, temp: -10, kind: "raw" } ] );
   pure( "line/frame_nulls", "frameLine", [ { name: "F.xisf", filter: null, exposure: null, binning: null,
                                              gain: null, temp: null } ] );
   pure( "line/frame_rounding", "frameLine", [ { name: "F.xisf", filter: "Ha", exposure: 0.0015, binning: 2,
                                                 gain: 120.04, temp: -10.25 } ] );
   pure( "line/filter_with_spaces_keeps_quotes", "frameLine", [ { name: "F.xisf", filter: "  'OIII'  ", exposure: 1,
                                                                   binning: 1, gain: 1, temp: 1 } ] );
   pure( "line/filter_two_quotes_empty", "frameLine", [ { name: "F.xisf", filter: "''", exposure: 1,
                                                          binning: 1, gain: 1, temp: 1 } ] );
   pure( "line/filter_only_leading_quote", "frameLine", [ { name: "F.xisf", filter: "'Ha", exposure: 1,
                                                             binning: 1, gain: 1, temp: 1 } ] );
   pure( "line/kind_present", "frameLine", [ { name: "D.xisf", filter: "Ha", exposure: 120, binning: 1,
                                               gain: 120, temp: -10, kind: "master" } ] );

   /* ---- darkSummaryLines ------------------------------------------------------------------------ */
   pure( "summary/groups_by_kind_exposure_binning_gain", "darkSummaryLines", [ [
      darkOf( "/d/a", "raw", { exposure: 120, temp: -10 } ), darkOf( "/d/b", "raw", { exposure: 120, temp: -9.5 } ),
      darkOf( "/d/c", "master", { exposure: 120, temp: -10 } ), darkOf( "/d/d", "raw", { exposure: 300, temp: null } ) ] ] );
   pure( "summary/empty", "darkSummaryLines", [ [] ] );
   pure( "summary/temperature_range", "darkSummaryLines", [ [
      darkOf( "/d/a", "raw", { temp: -12.5 } ), darkOf( "/d/b", "raw", { temp: -9.25 } ), darkOf( "/d/c", "raw", { temp: -10 } ) ] ] );

   /* ---- planLines (over the plans built above) -------------------------------------------------- */
   const twoExposures = M.buildPlan( [ flatAt( "/f/a.xisf", { exposure: 2 } ), flatAt( "/f/b.xisf", { exposure: 2 } ),
                                       flatAt( "/f/c.xisf", { exposure: 30 } ), flatAt( "/f/d.xisf", { exposure: 30 } ) ],
                                     [ darkOf( "/d/r2a.xisf", "raw", { exposure: 2 } ), darkOf( "/d/r2b.xisf", "raw", { exposure: 2 } ),
                                       darkOf( "/d/m30.xisf", "master", { exposure: 30 } ) ], DEST, ATCARD );
   pure( "lines/master_and_raw_parts", "planLines", [ twoExposures ] );
   const skipped = M.buildPlan( [ flatAt( "/f/a.xisf", { binning: 2 } ), flatAt( "/f/b.xisf", { binning: 2 } ) ], [], DEST, ATCARD );
   pure( "lines/skipped_job_with_binning", "planLines", [ skipped ] );
   const tooFew = M.buildPlan( [ flatAt( "/f/a.xisf", { filter: null, binning: null } ) ], [], DEST );
   pure( "lines/job_with_null_filter_one_flat", "planLines", [ tooFew ] );
   pure( "lines/no_jobs", "planLines", [ [] ] );

   /* ---- scanFolder: folder listing and header reader, through the fake tree --------------------- */
   const SOURCE_ROOT_MODIFIED = "2026-09-18T20:00:00.000Z";
   const F = ( name, size, extra ) => Object.assign( { name: name, isDirectory: false, isSymbolicLink: false, size: size,
                                                       modified: SOURCE_ROOT_MODIFIED }, extra || {} );
   const D = ( name, extra ) => Object.assign( { name: name, isDirectory: true, isSymbolicLink: false, size: 0 }, extra || {} );
   const DARK_HEADER = { info: true, keywords: { IMAGETYP: "Dark Frame", EXPTIME: "120", XBINNING: "1", GAIN: "120", "CCD-TEMP": "-10" } };
   const FLAT_HEADER = { info: true, keywords: { IMAGETYP: "Flat", FILTER: "Ha", EXPTIME: "2", XBINNING: "1", GAIN: "120", "CCD-TEMP": "-10" } };
   const UNREADABLE = { info: false, keywords: { IMAGETYP: "Dark Frame", EXPTIME: "120" } };

   /* Runs scanFolder( dir, readHeader [, depth] ) over the tree, the fakes in place only for the call. */
   function scanCase( key, dir, tree, headers, depth )
   {
      const savedFind = js.Util.findEntries, savedExists = File.directoryExists, savedInfo = globalThis.FileInfo;
      const dirs = new Set( Object.keys( tree ).map( d => d.replace( /\/+$/, "" ) ) );
      const modified = {};
      for ( const d of Object.keys( tree ) )
         for ( const e of tree[d] )
            if ( !e.isDirectory ) modified[d.replace( /\/+$/, "" ) + "/" + e.name] = e.modified;
      try
      {
         js.Util.findEntries = function( pattern )
         {
            const d = pattern.replace( /\/\*$/, "" );
            const listed = ( tree[d] || [] ).slice().sort( ( a, b ) => a.name < b.name ? -1 : a.name > b.name ? 1 : 0 );
            return listed.map( e => ( { name: e.name, isDirectory: e.isDirectory, isFile: !e.isDirectory,
                                        isSymbolicLink: e.isSymbolicLink, size: e.size } ) );
         };
         File.directoryExists = function( p ) { return dirs.has( String( p ).replace( /\/+$/, "" ) ); };
         globalThis.FileInfo = function( p ) { this.lastModified = new Date( modified[p] ); };
         const args = [ dir, readerOf( headers ) ];
         if ( depth !== undefined ) args.push( depth );
         const out = M.scanFolder.apply( M, args );
         cases[key] = { fn: "scanFolder", tree: tree, dir: dir, headers: headers,
                        depth: depth === undefined ? null : depth, out: norm( out ) };
      }
      finally
      {
         js.Util.findEntries = savedFind;
         File.directoryExists = savedExists;
         globalThis.FileInfo = savedInfo;
      }
   }

   const ROOT = "/fake/Darks";
   scanCase( "scan/basic", ROOT,
      { [ROOT]: [ F( "Flat_dark.xisf", 300 ), F( "Light_1.xisf", 500 ), F( "dark_a.xisf", 4096 ), F( "dark_b.fit", 2048 ),
                  F( "dark_c.xisf", 1000 ), F( "dark_missing.fit", 1000 ), F( "dark_notes.txt", 100 ), F( "dark_d.fits", 900 ) ] },
      { [ROOT + "/Flat_dark.xisf"]: FLAT_HEADER, [ROOT + "/dark_a.xisf"]: DARK_HEADER, [ROOT + "/dark_b.fit"]: DARK_HEADER,
        [ROOT + "/dark_c.xisf"]: UNREADABLE, [ROOT + "/dark_notes.txt"]: DARK_HEADER, [ROOT + "/dark_d.fits"]: DARK_HEADER } );
   scanCase( "scan/name_case_and_master_names", ROOT,
      { [ROOT]: [ F( "DARK_UP.XISF", 10 ), F( "masterdark.fts", 10 ), F( "MasterDark_120.Fits", 10 ) ] },
      { [ROOT + "/DARK_UP.XISF"]: DARK_HEADER, [ROOT + "/masterdark.fts"]: DARK_HEADER, [ROOT + "/MasterDark_120.Fits"]: DARK_HEADER } );
   scanCase( "scan/image_extensions_only", ROOT,
      { [ROOT]: [ F( "dark.fz", 10 ), F( "dark.xisf.txt", 10 ), F( "dark.fts", 10 ), F( "dark.fit.bak", 10 ) ] },
      { [ROOT + "/dark.fz"]: DARK_HEADER, [ROOT + "/dark.xisf.txt"]: DARK_HEADER, [ROOT + "/dark.fts"]: DARK_HEADER,
        [ROOT + "/dark.fit.bak"]: DARK_HEADER } );
   scanCase( "scan/directory_named_dark_is_recursed_not_listed", ROOT,
      { [ROOT]: [ D( "dark_sub" ), F( "dark_top.xisf", 10 ) ], [ROOT + "/dark_sub"]: [ F( "dark_in_sub.xisf", 10 ) ] },
      { [ROOT + "/dark_top.xisf"]: DARK_HEADER, [ROOT + "/dark_sub/dark_in_sub.xisf"]: DARK_HEADER } );
   scanCase( "scan/symbolic_link_directory_not_followed", ROOT,
      { [ROOT]: [ D( "linked", { isSymbolicLink: true } ), F( "dark_top.xisf", 10 ) ], [ROOT + "/linked"]: [ F( "dark_linked.xisf", 10 ) ] },
      { [ROOT + "/dark_top.xisf"]: DARK_HEADER, [ROOT + "/linked/dark_linked.xisf"]: DARK_HEADER } );
   /* Depth: the root is depth 0; a folder is entered while its depth is below SCAN_DEPTH (4). */
   scanCase( "scan/depth_limit_default", ROOT,
      { [ROOT]: [ D( "L1" ), F( "dark_R.xisf", 10 ) ], [ROOT + "/L1"]: [ D( "L2" ), F( "dark_L1.xisf", 10 ) ],
        [ROOT + "/L1/L2"]: [ D( "L3" ), F( "dark_L2.xisf", 10 ) ], [ROOT + "/L1/L2/L3"]: [ D( "L4" ), F( "dark_L3.xisf", 10 ) ],
        [ROOT + "/L1/L2/L3/L4"]: [ D( "L5" ), F( "dark_L4.xisf", 10 ) ], [ROOT + "/L1/L2/L3/L4/L5"]: [ F( "dark_L5.xisf", 10 ) ] },
      Object.fromEntries( [ "dark_R", "L1/dark_L1", "L1/L2/dark_L2", "L1/L2/L3/dark_L3", "L1/L2/L3/L4/dark_L4",
                            "L1/L2/L3/L4/L5/dark_L5" ].map( p => [ ROOT + "/" + p + ".xisf", DARK_HEADER ] ) ) );
   scanCase( "scan/depth_given_four_lists_root_only", ROOT,
      { [ROOT]: [ D( "sub" ), F( "dark_top.xisf", 10 ) ], [ROOT + "/sub"]: [ F( "dark_sub.xisf", 10 ) ] },
      { [ROOT + "/dark_top.xisf"]: DARK_HEADER, [ROOT + "/sub/dark_sub.xisf"]: DARK_HEADER }, 4 );
   scanCase( "scan/depth_given_three_enters_one_more", ROOT,
      { [ROOT]: [ D( "sub" ) ], [ROOT + "/sub"]: [ D( "deeper" ) ], [ROOT + "/sub/deeper"]: [ F( "dark_deeper.xisf", 10 ) ] },
      { [ROOT + "/sub/deeper/dark_deeper.xisf"]: DARK_HEADER }, 3 );
   scanCase( "scan/trailing_slash_stripped", ROOT + "/",
      { [ROOT]: [ F( "dark_a.xisf", 10 ) ] }, { [ROOT + "/dark_a.xisf"]: DARK_HEADER } );
   scanCase( "scan/many_trailing_slashes_stripped", ROOT + "//",
      { [ROOT]: [ F( "dark_a.xisf", 10 ) ] }, { [ROOT + "/dark_a.xisf"]: DARK_HEADER } );
   scanCase( "scan/missing_folder", "/fake/Missing", {}, {} );
   scanCase( "scan/empty_string_folder", "", { [ROOT]: [ F( "dark_a.xisf", 10 ) ] }, { [ROOT + "/dark_a.xisf"]: DARK_HEADER } );
   scanCase( "scan/null_folder", null, { [ROOT]: [ F( "dark_a.xisf", 10 ) ] }, { [ROOT + "/dark_a.xisf"]: DARK_HEADER } );
   scanCase( "scan/empty_folder", ROOT, { [ROOT]: [] }, {} );
   scanCase( "scan/only_unreadable_and_unlisted_headers", ROOT,
      { [ROOT]: [ F( "dark_a.xisf", 10 ), F( "dark_b.xisf", 10 ) ] },
      { [ROOT + "/dark_a.xisf"]: UNREADABLE } );
   scanCase( "scan/listing_order_kept", ROOT,
      { [ROOT]: [ F( "Dark_b.xisf", 10 ), F( "dark_a.xisf", 10 ), F( "dark_c.xisf", 10 ) ] },
      { [ROOT + "/Dark_b.xisf"]: DARK_HEADER, [ROOT + "/dark_a.xisf"]: DARK_HEADER, [ROOT + "/dark_c.xisf"]: DARK_HEADER } );

   return cases;
};
