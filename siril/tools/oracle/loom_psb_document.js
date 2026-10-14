/*
 * Recorder for the layered PSB document builder: Steps.buildPsbDocument and the
 * helpers it calls (lib/Steps.js:3726-3977). Pixels are not involved, so each
 * result window is a plain object { id } and the trees record the window id.
 *
 * Keys of the form "set|arg" name a result set and one extra argument. The
 * result sets themselves are recorded under "result_sets" so the Python side
 * feeds exactly the same inputs.
 */
"use strict";

module.exports = function( js )
{
   const { Util, Steps } = js;

   const sets = {
      rgb_only: [ "RGB" ],
      rgb_with_stars: [ "RGB", "RGB_stars" ],
      rgb_starless_with_stars_and_l: [ "RGB_starless", "RGB_stars", "L_stars" ],
      rgb_and_sho: [ "RGB", "RGB_starless", "SHO_starless" ],
      sho_hoo_hso_no_rgb: [ "SHO_starless", "HOO_starless", "HSO_starless" ],
      palette_without_stars: [ "HSO_starless" ],
      palette_with_stars: [ "HSO_starless", "RGB_stars", "L_stars" ],
      palette_plain_plate: [ "HOO" ],
      l_stars_plain: [ "RGB_starless", "L_stars" ],
      l_stars_separated: [ "RGB_starless", "L_stars_low", "L_stars_high" ],
      l_stars_separated_and_plain: [ "RGB_starless", "L_stars", "L_stars_low", "L_stars_high" ],
      l_stars_low_only: [ "RGB_starless", "L_stars_low" ],
      nothing_but_l: [ "L_stars" ],
      nothing: []
   };

   // A results map from a list of result names; each window is a plain fake.
   const res = names => {
      const r = {};
      for ( const n of names )
         r[n] = { id: n };
      return r;
   };
   const setOf = k => res( sets[k] );

   // Normalised node: every field the writer reads, with defaults made explicit.
   const norm = node => ( {
      name: node.name,
      blend: ( node.blend == null ) ? null : node.blend,
      visible: node.visible !== false,
      clipping: node.clipping === true,
      curves: ( node.curves == null ) ? null : node.curves,
      hue_saturation: node.hueSaturation === true,
      vibrance: node.vibrance === true,
      window: ( node.window == null ) ? null : node.window.id,
      group: ( node.group == null ) ? null : node.group.map( norm )
   } );
   const normOrNull = n => ( n == null ) ? null : norm( n );

   const out = { result_sets: sets, documents: {}, palette_names: {}, palette_name: {}, star_layers: {},
                 l_stars_layer: {}, palette_group: {}, broadband_group: {}, curve_layer_name: {},
                 assert_plates_share_size: {}, palette_channels: {} };

   for ( const k of Object.keys( sets ) )
   {
      out.documents[k] = Steps.buildPsbDocument( setOf( k ) ).map( norm );
      out.palette_names[k] = Steps.psbPaletteNames( setOf( k ) );
      out.palette_name[k] = Steps.psbPaletteName( setOf( k ) );
      out.star_layers[k] = Steps.psbStarLayers( setOf( k ) ).map( norm );
      out.l_stars_layer[k] = normOrNull( Steps.psbLStarsLayer( setOf( k ) ) );
   }

   out.dso_adjustments = Steps.psbDsoAdjustments().map( norm );

   for ( const [ k, pal ] of [ [ "sho_hoo_hso_no_rgb", "SHO" ], [ "sho_hoo_hso_no_rgb", "HOO" ],
                               [ "sho_hoo_hso_no_rgb", "HSO" ], [ "sho_hoo_hso_no_rgb", "" ],
                               [ "palette_without_stars", "" ], [ "palette_plain_plate", "" ],
                               [ "palette_plain_plate", "HOO" ], [ "nothing", "" ], [ "nothing", "SHO" ] ] )
      out.palette_group[k + "|" + pal] = normOrNull( Steps.psbPaletteGroup( setOf( k ), pal || undefined ) );

   for ( const k of [ "rgb_and_sho", "rgb_only", "sho_hoo_hso_no_rgb", "nothing" ] )
      for ( const shown of [ true, false ] )
         out.broadband_group[k + "|" + shown] = normOrNull( Steps.psbBroadbandGroup( setOf( k ), shown ) );

   for ( const pk of [ "SHO", "HOO", "HSO" ] )
      for ( const key of [ "H", "S", "O" ] )
         out.palette_channels[pk + "|" + key] = Steps.paletteChannelsOf( Util.PALETTES[pk], key );

   for ( const [ label, ids ] of [ [ "Ha", [ 1 ] ], [ "OIII", [ 2, 3 ] ], [ "Stars", [ 0 ] ], [ "SII", [ 2 ] ],
                                   [ "OIII", [ 1, 2, 3 ] ] ] )
      out.curve_layer_name[label + "|" + ids.join( "," )] = Steps.curveLayerName( label, ids );

   // Plates-share-size: clones are objects with mainView.image { width, height }.
   const clone = ( w, h ) => ( { mainView: { image: { width: w, height: h } } } );
   const sizeCase = ( key, sizes, w, h ) => {
      const clones = sizes.map( s => clone( s[0], s[1] ) );
      let message = null;
      try
      {
         Steps.assertPlatesShareSize( clones, w, h );
      }
      catch ( e )
      {
         message = String( e.message );
      }
      out.assert_plates_share_size[key] = { sizes, width: w, height: h, message };
   };
   sizeCase( "same", [ [ 7, 5 ], [ 7, 5 ], [ 7, 5 ] ], 7, 5 );
   sizeCase( "second_differs", [ [ 7, 5 ], [ 7, 5 ], [ 6, 5 ] ], 7, 5 );
   sizeCase( "first_differs_height", [ [ 7, 5 ], [ 7, 4 ] ], 7, 5 );
   sizeCase( "single", [ [ 3, 3 ] ], 7, 5 );

   return out;
};
