"use strict";
/*
 * Golden cases for loomsiril/asiair_names.py, second half: pixinsight/script/lib/AsiairNames.js lines 333-703 (flat
 * batches, batch assignment, flat matching, path containment, the import root, the summary phrases, the master-flats
 * check, the failed-copy skip and the copy manifest).
 *
 * Each case records the arguments and what the JS returned, so the Python suite replays the same calls and compares.
 * Frame records are built with the library's own stampKey. Paths that the master-flats check tests for existence are
 * relative to the siril/ folder, the working directory the suite and this recorder both run from; they are files and
 * folders that are committed there.
 *
 * Undefined does not survive JSON; outputs that are undefined are written as "__undefined__", and object keys whose
 * value is undefined are dropped, as JSON.stringify drops them. Arguments use null for "not given".
 */
module.exports = function( js )
{
   const A = js.AsiairNames;
   const cases = {};

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

   /* A pure call: the result is what the function returns. */
   function pure( key, name, args )
   {
      const out = A[name].apply( A, structuredClone( args ) );
      cases[key] = { fn: name, args: norm( args ), out: norm( out ) };
   }

   /* A frame record as the importer builds it: the stamp, its comparison key and any other fields. */
   function frame( stamp, filter, extra )
   {
      return Object.assign( { stamp: stamp, filter: filter, key: A.stampKey( stamp ) }, extra || {} );
   }

   /* A flat as the importer builds it for matchFlats: path, filter, binning, camera and rotation. */
   function flat( path, filter, binning, camera, rotation, gain )
   {
      return { path: path, filter: filter, binning: binning, camera: camera, rotation: rotation, gain: gain };
   }

   /* A light filter want, as matchFlats receives it. */
   function want( filter, binning, camera, rotation )
   {
      return { filter: filter, binning: binning, camera: camera, rotation: rotation };
   }

   /* ---- flatBatches: flats clustered among themselves ----------------------------------------------------- */
   pure( "flatBatches/empty", "flatBatches", [ [], null ] );
   pure( "flatBatches/one_flat", "flatBatches", [ [ frame( "20260807-100000", "H" ) ], null ] );
   pure( "flatBatches/three_daytime_flats_one_batch", "flatBatches", [ [
      frame( "20260807-100000", "H" ), frame( "20260807-140000", "H" ), frame( "20260807-180000", "H" ) ], null ] );
   pure( "flatBatches/gap_exactly_four_hours_one_batch", "flatBatches", [ [
      frame( "20260807-100000", "H" ), frame( "20260807-140000", "H" ) ], null ] );
   pure( "flatBatches/gap_four_hours_plus_one_second_splits", "flatBatches", [ [
      frame( "20260807-100000", "H" ), frame( "20260807-140001", "H" ) ], null ] );
   pure( "flatBatches/night_flats_do_not_bridge_through_lights", "flatBatches", [ [
      frame( "20260807-060000", "H" ), frame( "20260807-100000", "H" ), frame( "20260807-150000", "H" ),
      frame( "20260807-200000", "H" ), frame( "20260808-010000", "H" ) ], null ] );
   pure( "flatBatches/two_nights_two_batches", "flatBatches", [ [
      frame( "20260807-180000", "H" ), frame( "20260807-180500", "L" ), frame( "20260808-180000", "H" ) ], null ] );
   pure( "flatBatches/custom_gap_two_hours_splits", "flatBatches", [ [
      frame( "20260807-100000", "H" ), frame( "20260807-123000", "H" ) ], 2 ] );
   pure( "flatBatches/custom_gap_two_hours_exact_joins", "flatBatches", [ [
      frame( "20260807-100000", "H" ), frame( "20260807-120000", "H" ) ], 2 ] );
   pure( "flatBatches/gap_zero_equal_times_join", "flatBatches", [ [
      frame( "20260807-100000", "H" ), frame( "20260807-100000", "L" ), frame( "20260807-100001", "H" ) ], 0 ] );
   pure( "flatBatches/null_key_excluded", "flatBatches", [ [
      frame( "20260807-100000", "H" ), frame( "20260230-100000", "H" ), frame( "20260807-110000", "H" ) ], null ] );
   pure( "flatBatches/unsorted_input_sorted", "flatBatches", [ [
      frame( "20260807-180000", "H" ), frame( "20260807-100000", "H" ), frame( "20260807-140000", "H" ) ], null ] );
   pure( "flatBatches/midpoint_floor_half_second", "flatBatches", [ [
      frame( "20260807-100000", "H" ), frame( "20260807-100001", "H" ) ], null ] );
   pure( "flatBatches/midpoint_odd_sum_floors", "flatBatches", [ [
      frame( "20260807-100000", "H" ), frame( "20260807-100003", "H" ), frame( "20260807-100004", "H" ) ], null ] );
   pure( "flatBatches/first_flat_fields_kept", "flatBatches", [ [
      frame( "20260807-100000", "OIII", { binToken: "Bin1", camera: "2600MM" } ) ], null ] );

   /* ---- assignBatches: a batch goes WHOLE to the nearest session ------------------------------------------ */
   const night1 = A.sessions( [ frame( "20260807-200000", "H" ), frame( "20260807-220000", "H" ) ], null );
   const night2 = A.sessions( [ frame( "20260808-200000", "H" ), frame( "20260808-230000", "H" ) ], null );
   const dayFlats = ( stamps ) => A.flatBatches( stamps.map( s => frame( s, "H" ) ), null );
   pure( "assignBatches/no_sessions_none", "assignBatches", [ dayFlats( [ "20260807-100000" ] ), [] ] );
   pure( "assignBatches/batch_inside_session", "assignBatches", [ dayFlats( [ "20260807-210000" ] ), night1 ] );
   pure( "assignBatches/batch_before_all_sessions", "assignBatches", [ dayFlats( [ "20260807-100000" ] ), night1 ] );
   pure( "assignBatches/batch_after_all_sessions", "assignBatches", [ dayFlats( [ "20260809-100000" ] ), night2 ] );
   pure( "assignBatches/batch_nearer_later_session", "assignBatches", [ dayFlats( [ "20260808-100000" ] ), night1.concat( night2 ) ] );
   pure( "assignBatches/batch_nearer_earlier_session", "assignBatches", [ dayFlats( [ "20260808-150000" ] ), night1.concat( night2 ) ] );
   pure( "assignBatches/batch_across_midpoint_goes_whole", "assignBatches", [ A.flatBatches( [
      frame( "20260807-215000", "H" ), frame( "20260808-040000", "H" ), frame( "20260808-050000", "H" ) ], 4 ), night1.concat( night2 ) ] );
   pure( "assignBatches/batch_equidistant_goes_earlier", "assignBatches", [ [ { first: 100, last: 100, mid: 100 } ],
      [ { first: 0, last: 0 }, { first: 200, last: 200 } ] ] );
   pure( "assignBatches/two_batches_two_sessions", "assignBatches", [ A.flatBatches( [
      frame( "20260807-100000", "H" ), frame( "20260808-100000", "H" ) ], null ), night1.concat( night2 ) ] );
   pure( "assignBatches/far_batch_still_nearest_not_none", "assignBatches", [ [ { first: 9e9, last: 9e9, mid: 9e9 } ],
      [ { first: 0, last: 10, frames: [] }, { first: 100, last: 200, frames: [] } ] ] );

   /* ---- attributeFit: same, soft (one side stated) or clash -------------------------------------------------- */
   pure( "attributeFit/both_null_same", "attributeFit", [ null, null ] );
   pure( "attributeFit/both_equal_same", "attributeFit", [ "2600MM", "2600MM" ] );
   pure( "attributeFit/both_different_clash", "attributeFit", [ "2600MM", "294MM" ] );
   pure( "attributeFit/one_side_stated_soft", "attributeFit", [ "2600MM", null ] );
   pure( "attributeFit/other_side_stated_soft", "attributeFit", [ null, "45" ] );
   pure( "attributeFit/empty_string_is_stated", "attributeFit", [ "", null ] );
   pure( "attributeFit/zero_is_stated", "attributeFit", [ 0, null ] );
   pure( "attributeFit/numeric_string_equals_number", "attributeFit", [ "1", 1 ] );

   /* ---- mustMatch: stated on both sides, and equal ------------------------------------------------------------ */
   pure( "mustMatch/equal", "mustMatch", [ "H", "H" ] );
   pure( "mustMatch/different", "mustMatch", [ "H", "L" ] );
   pure( "mustMatch/one_missing", "mustMatch", [ "H", null ] );
   pure( "mustMatch/both_missing", "mustMatch", [ null, null ] );
   pure( "mustMatch/numeric_string_equal", "mustMatch", [ 1, "1" ] );
   pure( "mustMatch/case_sensitive", "mustMatch", [ "H", "h" ] );

   /* ---- flatSuits: filter and binning must match; camera and rotation where both state them -------------------- */
   const W = want( "H", "Bin1", "2600MM", "45deg" );
   pure( "flatSuits/exact", "flatSuits", [ W, flat( "/f/a.fit", "H", "Bin1", "2600MM", "45deg", "gain100" ) ] );
   pure( "flatSuits/gain_not_compared", "flatSuits", [ W, flat( "/f/a.fit", "H", "Bin1", "2600MM", "45deg", "gain0" ) ] );
   pure( "flatSuits/camera_stated_one_side_weak", "flatSuits", [ W, flat( "/f/a.fit", "H", "Bin1", null, "45deg" ) ] );
   pure( "flatSuits/rotation_stated_one_side_weak", "flatSuits", [ W, flat( "/f/a.fit", "H", "Bin1", "2600MM", null ) ] );
   pure( "flatSuits/camera_clash_refused", "flatSuits", [ W, flat( "/f/a.fit", "H", "Bin1", "294MM", "45deg" ) ] );
   pure( "flatSuits/rotation_clash_refused", "flatSuits", [ W, flat( "/f/a.fit", "H", "Bin1", "2600MM", "90deg" ) ] );
   pure( "flatSuits/binning_mismatch_refused", "flatSuits", [ W, flat( "/f/a.fit", "H", "Bin2", "2600MM", "45deg" ) ] );
   pure( "flatSuits/binning_absent_refused", "flatSuits", [ W, flat( "/f/a.fit", "H", null, "2600MM", "45deg" ) ] );
   pure( "flatSuits/filter_mismatch_refused", "flatSuits", [ W, flat( "/f/a.fit", "L", "Bin1", "2600MM", "45deg" ) ] );
   pure( "flatSuits/filter_absent_refused", "flatSuits", [ W, flat( "/f/a.fit", null, "Bin1", "2600MM", "45deg" ) ] );
   pure( "flatSuits/nothing_stated_both_sides_exact", "flatSuits", [ want( "H", "Bin1", null, null ),
      flat( "/f/a.fit", "H", "Bin1", null, null ) ] );
   pure( "flatSuits/binning_numeric_string_exact", "flatSuits", [ want( "H", 1, null, null ),
      flat( "/f/a.fit", "H", "1", null, null ) ] );
   pure( "flatSuits/camera_and_rotation_weak_both_soft", "flatSuits", [ want( "H", "Bin1", null, null ),
      flat( "/f/a.fit", "H", "Bin1", "2600MM", "45deg" ) ] );

   pure( "flatSuits/filter_and_binning_both_absent_refused", "flatSuits", [ want( null, null, null, null ),
      flat( "/f/a.fit", null, null, null, null ) ] );

   /* ---- matchFlats: per light filter, the hits and the strength ----------------------------------------------- */
   const flats = [
      flat( "/f/H1.fit", "H", "Bin1", "2600MM", "45deg" ),
      flat( "/f/H2.fit", "H", "Bin1", null, "45deg" ),
      flat( "/f/O1.fit", "OIII", "Bin1", "2600MM", "45deg" ),
      flat( "/f/O2.fit", "OIII", "Bin2", "2600MM", "45deg" ),
      flat( "/f/S1.fit", "SII", "Bin1", "2600MM", "45deg" ) ];
   pure( "matchFlats/exact_and_weak_mixed_is_weak", "matchFlats", [ [ want( "H", "Bin1", "2600MM", "45deg" ) ], flats ] );
   pure( "matchFlats/exact_only", "matchFlats", [ [ want( "OIII", "Bin1", "2600MM", "45deg" ) ], flats ] );
   pure( "matchFlats/missing_for_filter_without_flats", "matchFlats", [ [ want( "L", "Bin1", "2600MM", "45deg" ) ], flats ] );
   pure( "matchFlats/binning_mismatch_missing", "matchFlats", [ [ want( "OIII", "Bin3", "2600MM", "45deg" ) ], flats ] );
   pure( "matchFlats/each_light_filter_in_order", "matchFlats", [ [
      want( "SII", "Bin1", null, null ), want( "H", "Bin1", "2600MM", "45deg" ), want( "L", "Bin1", null, null ) ], flats ] );
   pure( "matchFlats/no_flats_at_all", "matchFlats", [ [ want( "H", "Bin1", null, null ) ], [] ] );

   /* ---- isInside: containment on path components ---------------------------------------------------------- */
   pure( "isInside/child", "isInside", [ "/Volumes/ASIAIR/Light/x.fit", "/Volumes/ASIAIR" ] );
   pure( "isInside/sibling_sharing_prefix", "isInside", [ "/Volumes/ASIAIR-backup/x", "/Volumes/ASIAIR" ] );
   pure( "isInside/sibling_numeric_suffix", "isInside", [ "/Volumes/ASIAIR2", "/Volumes/ASIAIR" ] );
   pure( "isInside/equal", "isInside", [ "/Volumes/ASIAIR", "/Volumes/ASIAIR" ] );
   pure( "isInside/trailing_slash_on_path", "isInside", [ "/Volumes/ASIAIR/", "/Volumes/ASIAIR" ] );
   pure( "isInside/trailing_slash_on_root", "isInside", [ "/Volumes/ASIAIR", "/Volumes/ASIAIR/" ] );
   pure( "isInside/many_trailing_slashes_both", "isInside", [ "/Volumes/ASIAIR//", "/Volumes/ASIAIR//" ] );
   pure( "isInside/parent_is_not_inside", "isInside", [ "/Volumes", "/Volumes/ASIAIR" ] );
   pure( "isInside/case_sensitive", "isInside", [ "/Volumes/asiair/x", "/Volumes/ASIAIR" ] );
   pure( "isInside/root_slash_contains_absolute", "isInside", [ "/a/b", "/" ] );
   pure( "isInside/empty_root_contains_all", "isInside", [ "relative/dir", "" ] );
   pure( "isInside/empty_path_not_inside_absolute", "isInside", [ "", "/a" ] );
   pure( "isInside/deeper_same_name", "isInside", [ "/Volumes/ASIAIR/Light", "/Volumes/ASIAIR/Light" ] );
   pure( "isInside/windows_drive_child", "isInside", [ "C:/x/y", "C:/x" ] );
   pure( "isInside/windows_drive_sibling", "isInside", [ "C:/xy/y", "C:/x" ] );
   pure( "isInside/no_normalisation_of_dot_dot", "isInside", [ "/Volumes/ASIAIR/a/../b", "/Volumes/ASIAIR" ] );

   /* ---- importRoot: a chosen Light or Flat folder means its parent ----------------------------------------- */
   pure( "importRoot/light_folder", "importRoot", [ "/Volumes/ASIAIR/Light" ] );
   pure( "importRoot/flat_folder", "importRoot", [ "/Volumes/ASIAIR/Flat" ] );
   pure( "importRoot/light_lowercase", "importRoot", [ "/Volumes/ASIAIR/light" ] );
   pure( "importRoot/flat_uppercase", "importRoot", [ "/Volumes/ASIAIR/FLAT" ] );
   pure( "importRoot/light_trailing_slash", "importRoot", [ "/Volumes/ASIAIR/Light/" ] );
   pure( "importRoot/light_many_trailing_slashes", "importRoot", [ "/Volumes/ASIAIR/Light//" ] );
   pure( "importRoot/plural_is_someones_folder", "importRoot", [ "/Volumes/ASIAIR/Lights" ] );
   pure( "importRoot/plural_flats_is_someones_folder", "importRoot", [ "/Volumes/ASIAIR/Flats" ] );
   pure( "importRoot/prefix_is_not_a_match", "importRoot", [ "/Volumes/ASIAIR/Lightroom" ] );
   pure( "importRoot/plain_folder", "importRoot", [ "/Volumes/ASIAIR" ] );
   pure( "importRoot/light_inside_light_takes_last", "importRoot", [ "/Volumes/ASIAIR/Light/Light" ] );
   pure( "importRoot/root_level_light_has_no_parent", "importRoot", [ "/Light" ] );
   pure( "importRoot/bare_name_no_parent", "importRoot", [ "Light" ] );
   pure( "importRoot/slash_alone", "importRoot", [ "/" ] );
   pure( "importRoot/empty", "importRoot", [ "" ] );
   pure( "importRoot/double_leading_slash_parent_is_slash", "importRoot", [ "//Light" ] );
   pure( "importRoot/folder_with_spaces", "importRoot", [ "/Volumes/My Disk/Light" ] );
   pure( "importRoot/trailing_space_is_not_light", "importRoot", [ "/Volumes/ASIAIR/Light " ] );

   /* ---- exposureList: the exposures as the summary words them ---------------------------------------------- */
   pure( "exposureList/empty", "exposureList", [ [] ] );
   pure( "exposureList/one", "exposureList", [ [ 15 ] ] );
   pure( "exposureList/two", "exposureList", [ [ 15, 30 ] ] );
   pure( "exposureList/three_with_fraction", "exposureList", [ [ 1.5, 15, 30 ] ] );
   pure( "exposureList/three_whole", "exposureList", [ [ 15, 30, 45 ] ] );
   pure( "exposureList/rounds_to_millisecond_half_up", "exposureList", [ [ 0.0005 ] ] );
   pure( "exposureList/rounds_down_below_half", "exposureList", [ [ 0.00025 ] ] );
   pure( "exposureList/rounds_two_millis_up", "exposureList", [ [ 0.0015 ] ] );
   pure( "exposureList/negative_tie_toward_plus_infinity", "exposureList", [ [ -2.5 ] ] );
   pure( "exposureList/float_sum_is_clean", "exposureList", [ [ 0.1 + 0.2 ] ] );
   pure( "exposureList/large_whole", "exposureList", [ [ 1e7 ] ] );
   pure( "exposureList/nan", "exposureList", [ [ NaN ] ] );

   /* ---- darkSource: the darks on the card or the darks folder --------------------------------------------- */
   pure( "darkSource/card_two_exposures", "darkSource", [ { card: true, count: 3, exposures: [ 15, 30 ] } ] );
   pure( "darkSource/card_no_exposure_stated", "darkSource", [ { card: true, count: 0, exposures: [] } ] );
   pure( "darkSource/card_single", "darkSource", [ { card: true, count: 1, exposures: [ 300 ] } ] );
   pure( "darkSource/folder", "darkSource", [ { card: false, darksFolder: "/Volumes/Darks" } ] );
   pure( "darkSource/card_absent_means_folder", "darkSource", [ { card: null, darksFolder: "/Volumes/Darks/x" } ] );

   /* ---- masterFlatsProblem: checked before anything is written (paths are relative to siril/) ----------- */
   pure( "masterFlatsProblem/off_nothing_to_check", "masterFlatsProblem", [ null, false, null ] );
   pure( "masterFlatsProblem/off_even_with_missing_folder", "masterFlatsProblem", [ "tools/oracle/absent", false, null ] );
   pure( "masterFlatsProblem/on_no_folder_given", "masterFlatsProblem", [ null, true, null ] );
   pure( "masterFlatsProblem/on_empty_folder_string", "masterFlatsProblem", [ "", true, null ] );
   pure( "masterFlatsProblem/folder_exists", "masterFlatsProblem", [ "tools/oracle", true, null ] );
   pure( "masterFlatsProblem/folder_missing", "masterFlatsProblem", [ "tools/oracle/absent", true, null ] );
   pure( "masterFlatsProblem/folder_is_a_file", "masterFlatsProblem", [ "tools/record_oracle.js", true, null ] );
   pure( "masterFlatsProblem/card_darks_present", "masterFlatsProblem", [ "tools/oracle/absent", true, [ "tools/record_oracle.js" ] ] );
   pure( "masterFlatsProblem/card_dark_missing", "masterFlatsProblem", [ null, true, [ "tools/record_oracle.js", "tools/oracle/absent.fits" ] ] );
   pure( "masterFlatsProblem/card_no_darks_listed", "masterFlatsProblem", [ null, true, [] ] );
   pure( "masterFlatsProblem/card_dark_is_a_folder", "masterFlatsProblem", [ null, true, [ "tools/oracle" ] ] );

   /* ---- importSummary: the two folders frames land in, with counts and the parent note --------------------- */
   const man2 = { lights: [ { src: "/s/L1.fit", dst: "/v/Light/L1.xisf" }, { src: "/s/L2.fit", dst: "/v/Light/L2.xisf" } ],
                  flats: [ { src: "/f/F1.fit", dst: "/v/Flat/F1.xisf", filter: "H" } ] };
   const man0 = { lights: [], flats: [] };
   pure( "importSummary/plain_folder_no_masters", "importSummary", [ man2, "/Volumes/ASIAIR", null ] );
   pure( "importSummary/light_folder_chosen", "importSummary", [ man2, "/Volumes/ASIAIR/Light", null ] );
   pure( "importSummary/flat_folder_chosen_with_card_darks", "importSummary", [ man2, "/Volumes/ASIAIR/Flat/",
      { card: true, count: 2, exposures: [ 15, 30 ] } ] );
   pure( "importSummary/light_folder_with_darks_folder", "importSummary", [ man2, "/Volumes/ASIAIR/Light",
      { card: false, darksFolder: "/Volumes/Darks" } ] );
   pure( "importSummary/card_darks_no_exposure_stated", "importSummary", [ man2, "/Volumes/ASIAIR",
      { card: true, count: 0, exposures: [] } ] );
   pure( "importSummary/nothing_to_import", "importSummary", [ man0, "/Volumes/ASIAIR", null ] );

   /* ---- skipFiltersWithFailedCopies: a master needs ALL of a filter's flats ----------------------------------- */
   const mflats = [ { src: "/a/F1.fit", filter: "H" }, { src: "/a/F2.fit", filter: "H" }, { src: "/a/O1.fit", filter: "OIII" } ];
   const jobs3 = () => [ { filter: "H" }, { filter: "OIII" }, { filter: "L" } ];
   pure( "skipFilters/no_failures", "skipFiltersWithFailedCopies", [ mflats, [], jobs3() ] );
   pure( "skipFilters/one_h_failed", "skipFiltersWithFailedCopies", [ mflats, [ "/a/F1.fit" ], jobs3() ] );
   pure( "skipFilters/two_h_failed_counts_two", "skipFiltersWithFailedCopies", [ mflats, [ "/a/F1.fit", "/a/F2.fit" ], jobs3() ] );
   pure( "skipFilters/unrelated_source_failed", "skipFiltersWithFailedCopies", [ mflats, [ "/x/none.fit" ], jobs3() ] );
   pure( "skipFilters/earlier_skip_kept", "skipFiltersWithFailedCopies", [ mflats, [ "/a/F1.fit" ],
      [ { filter: "H", skip: "earlier reason" }, { filter: "OIII" } ] ] );
   pure( "skipFilters/flat_without_filter_skips_every_job", "skipFiltersWithFailedCopies", [
      [ { src: "/a/U.fit", filter: null } ], [ "/a/U.fit" ], jobs3() ] );
   pure( "skipFilters/unfiltered_failure_plus_h_failure", "skipFiltersWithFailedCopies", [
      [ { src: "/a/U.fit", filter: null }, { src: "/a/F1.fit", filter: "H" } ], [ "/a/U.fit", "/a/F1.fit" ], jobs3() ] );
   pure( "skipFilters/job_with_null_filter_skipped_by_unfiltered", "skipFiltersWithFailedCopies", [
      [ { src: "/a/U.fit", filter: null } ], [ "/a/U.fit" ], [ { filter: null } ] ] );
   pure( "skipFilters/job_without_failures_untouched", "skipFiltersWithFailedCopies", [
      mflats, [ "/a/O1.fit" ], [ { filter: "H" } ] ] );

   /* ---- manifest: the copy schedule (the recorder's output is the JavaScript's .xisf names) ---------------- */
   const lightsA = [ { path: "/s/Light_H_1.fit", filter: "H" }, { path: "/s/Light_H_2.fit", filter: "H" },
                     { path: "/s/Light_O_1.FITS", filter: "OIII" } ];
   const matchesA = [
      { filter: "H", flats: [ { path: "/f/Flat_H_1.fit" }, { path: "/f/Flat_H_2.fit" } ], strength: "exact" },
      { filter: "OIII", flats: [ { path: "/f/Flat_O_1.fit" } ], strength: "weak" },
      { filter: "SII", flats: [ { path: "/f/Flat_S_1.fit" } ], strength: "exact" } ];
   pure( "manifest/basic", "manifest", [ lightsA, matchesA, "/Volumes/ASIAIR/Out" ] );
   pure( "manifest/filter_without_lights_dropped", "manifest", [ [ { path: "/s/Light_H_1.fit", filter: "H" } ], matchesA, "/v" ] );
   pure( "manifest/rejected_filter_drops_its_flats", "manifest", [ [ { path: "/s/Light_H_1.fit", filter: "H" } ], matchesA, "/v" ] );
   pure( "manifest/no_approved_lights", "manifest", [ [], matchesA, "/v" ] );
   pure( "manifest/no_lights_no_flats", "manifest", [ [], [], "/v" ] );
   pure( "manifest/clash_different_sources_same_name", "manifest", [ [ { path: "/a/Light_1.fit", filter: "H" },
      { path: "/b/Light_1.fit", filter: "H" } ], [], "/v" ] );
   pure( "manifest/clash_plan_and_autorun", "manifest", [ [ { path: "/Plan/Light_1.fit", filter: "H" },
      { path: "/Autorun/Light_1.fit", filter: "H" }, { path: "/Plan/Light_2.fit", filter: "H" } ], matchesA, "/v" ] );
   pure( "manifest/clash_case_insensitive", "manifest", [ [ { path: "/a/x/Frame.fit", filter: "H" },
      { path: "/b/x/frame.fit", filter: "H" } ], [], "/v" ] );
   pure( "manifest/clash_between_two_flats", "manifest", [ [ { path: "/s/L.fit", filter: "H" } ], [
      { filter: "H", flats: [ { path: "/f1/Flat.fit" }, { path: "/f2/Flat.fit" } ] } ], "/v" ] );
   pure( "manifest/same_source_twice_written_once", "manifest", [ [ { path: "/s/L.fit", filter: "H" },
      { path: "/s/L.fit", filter: "H" } ], [], "/v" ] );
   pure( "manifest/shared_flat_under_two_filters_once", "manifest", [
      [ { path: "/s/L_H.fit", filter: "H" }, { path: "/s/L_O.fit", filter: "OIII" } ],
      [ { filter: "H", flats: [ { path: "/f/Shared.fit" } ] }, { filter: "OIII", flats: [ { path: "/f/Shared.fit" } ] } ], "/v" ] );
   pure( "manifest/null_filter_survives_as_null_key", "manifest", [ [ { path: "/s/L.fit", filter: null } ],
      [ { filter: null, flats: [ { path: "/f/N.fit" } ] } ], "/v" ] );
   pure( "manifest/uppercase_extension_replaced", "manifest", [ [ { path: "/s/Light.FITS", filter: "H" } ], [], "/v" ] );
   pure( "manifest/inner_dot_kept", "manifest", [ [ { path: "/s/Light.v2.fit", filter: "H" } ], [], "/v" ] );
   pure( "manifest/no_extension_name_unchanged", "manifest", [ [ { path: "/s/Light_noext", filter: "H" } ], [], "/v" ] );
   pure( "manifest/destination_trailing_slash", "manifest", [ [ { path: "/s/Light_1.fit", filter: "H" } ], [], "/Volumes/A/" ] );
   pure( "manifest/order_preserved", "manifest", [ [ { path: "/s/Z.fit", filter: "H" }, { path: "/s/A.fit", filter: "H" } ],
      [ { filter: "H", flats: [ { path: "/f/M.fit" }, { path: "/f/B.fit" } ] } ], "/v" ] );

   return cases;
};
