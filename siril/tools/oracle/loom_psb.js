/*
 * Recorder for the Photoshop Large Document writer (lib/Psb.js:32-50 constants,
 * :51-165 buffer, :166-212 tagged blocks, :213-338 adjustment payloads,
 * :339-503 layer records and flattening, :847-938 blend keys, resources and the
 * writer itself). Feeds tests/oracle/loom_psb.json.
 *
 * Byte strings are recorded as lowercase hex. The whole-document case replaces
 * the File and ByteArray globals with in-memory fakes for the duration of one
 * Psb.write call and restores them in finally; nothing reaches the disk.
 */
"use strict";

const crypto = require( "crypto" );

module.exports = function( js )
{
   const { Psb } = js;

   const out = {};

   const hex = b => Array.from( b.bytes || b, v => ( v & 0xFF ).toString( 16 ).padStart( 2, "0" ) ).join( "" );
   const bufOf = fn => { const b = new Psb.Buffer; fn( b ); return b; };

   /* Deterministic 16-bit planes: window( seed, n ) gives n channels of 7 x 5. */
   const W = 7, H = 5;
   const sample = ( seed, ch, y, x ) => ( seed * 7919 + ch * 1237 + y * 131 + x * 17 ) % 65536;
   const window_ = ( seed, channels ) => ( {
      mainView: { image: {
         numberOfChannels: channels,
         pixelData: ch => {
            const a = new Uint16Array( W * H );
            for ( let y = 0; y < H; ++y )
               for ( let x = 0; x < W; ++x )
                  a[y * W + x] = sample( seed, ch, y, x );
            return a.buffer;
         }
      } }
   } );

   const icc = Array.from( { length: 37 }, ( _, i ) => ( i * 7 + 3 ) & 0xFF );

   /* ---- tagged blocks ------------------------------------------------ */
   const luniName = bufOf( b => b.unicodeName( "Étoiles" ) );
   out.tagged_block_luni = hex( Psb.taggedBlock( "luni", luniName ) );
   out.tagged_block_lsct = hex( Psb.taggedBlock( "lsct", bufOf( b => b.u32( 3 ).ascii( "8BIM" ).ascii( "scrn" ) ) ) );
   out.tagged_block_curv = hex( Psb.taggedBlock( "curv", Psb.curvesPayload( [ 1 ] ) ) );
   out.tagged_block_hue2 = hex( Psb.taggedBlock( "hue2", Psb.hueSaturationPayload() ) );
   out.tagged_block_vibA = hex( Psb.taggedBlock( "vibA", Psb.vibrancePayload() ) );
   out.tagged_block_odd = hex( Psb.taggedBlock( "lsct", bufOf( b => b.u8( 7 ).u16( 0x0102 ) ) ) );
   out.unicode_name_astral = hex( bufOf( b => b.unicodeName( "M42 ° \u{1F600}" ) ) );

   /* ---- image resources and the file header ------------------------- */
   out.image_resource_1039 = hex( Psb.imageResource( 1039, icc ) );
   out.image_resource_odd = hex( Psb.imageResource( 1039, [ 1, 2, 3 ] ) );
   out.file_header_7x5_icc = hex( Psb.fileHeader( 7, 5, icc ) );
   out.file_header_7x5_none = hex( Psb.fileHeader( 7, 5, null ) );

   /* ---- adjustment payloads ----------------------------------------- */
   out.curves_payload_1 = hex( Psb.curvesPayload( [ 1 ] ) );
   out.curves_payload_23 = hex( Psb.curvesPayload( [ 2, 3 ] ) );
   out.curves_payload_0 = hex( Psb.curvesPayload( [ 0 ] ) );
   out.curves_payload_0123 = hex( Psb.curvesPayload( [ 0, 1, 2, 3 ] ) );
   out.hue_saturation_payload = hex( Psb.hueSaturationPayload() );
   out.vibrance_payload = hex( Psb.vibrancePayload() );

   /* ---- a nested tree, flattened, and its layer records ------------- */
   const tree = () => [
      { name: "Base", window: window_( 1, 3 ) },
      { name: "Hidden base", visible: false, window: window_( 2, 1 ) },
      { name: "Étoiles", blend: "scrn", open: false, opacity: 128,
        group: [ { name: "Curve", curves: [ 1 ] },
                 { name: "HS", hueSaturation: true, clipping: true, visible: false },
                 { name: "Vib", vibrance: true },
                 { name: "M42 ° \u{1F600}", window: window_( 3, 3 ) } ] },
      { name: "", blend: "lum ", window: window_( 4, 3 ) }
   ];

   const norm = L => ( {
      name: L.name,
      divider: L.divider == null ? null : L.divider,
      visible: L.visible === true,
      blend: L.blend,
      opacity: L.opacity,
      channel_ids: L.channelIds,
      curves: L.curves == null ? null : L.curves,
      hue_saturation: L.hueSaturation === true,
      vibrance: L.vibrance === true,
      clipping: L.clipping === true
   } );

   const flat = Psb.flatten( tree() );
   out.flatten_nested = flat.map( norm );
   out.composite_base_name = Psb.compositeBaseLayer( Psb.flatten( tree() ) ).name;
   out.composite_base_none = Psb.compositeBaseLayer( Psb.flatten( [ { name: "Curve", curves: [ 0 ] } ] ) );

   const flat2 = Psb.flatten( tree() );
   const lengths = Psb.declareChannels( flat2, W * H );
   out.declare_channels_lengths = lengths;
   out.layer_records_nested = hex( Psb.layerRecords( flat2, W, H, lengths ) );
   const label = [ "pixel", "hidden_pixel", "bounding_inner", "curves", "hue", "vibrance", "pixel_astral", "group_header", "pixel_lum" ];
   // flattened order: Base, Hidden base, [bounding, Curve, HS, Vib, M42, header], lum pixel
   const idx = [ 0, 1, 2, 3, 4, 5, 6, 7, 8 ];
   idx.forEach( ( i, k ) => { out[ "layer_record_" + label[k] ] = hex( Psb.layerRecord( flat2[i], W, H, lengths[i] ) ); } );

   /* ---- the whole document, written to memory ----------------------- */
   const savedFile = global.File;
   const savedByteArray = global.ByteArray;
   const chunks = [];
   try
   {
      global.ByteArray = function( u8 ) { this.u8 = u8 instanceof Uint8Array ? u8 : Uint8Array.from( u8 ); };
      global.File = function()
      {
         this.createForWriting = p => { this.path = p; };
         this.write = ba => { chunks.push( Buffer.from( ba.u8.buffer, ba.u8.byteOffset, ba.u8.byteLength ) ); };
         this.close = () => {};
      };
      Psb.write( "/unused/golden_7x5.psb", tree(), W, H, icc );
   }
   finally
   {
      global.File = savedFile;
      global.ByteArray = savedByteArray;
   }
   const whole = Buffer.concat( chunks );
   out.whole_7x5_length = whole.length;
   out.whole_7x5_sha256 = crypto.createHash( "sha256" ).update( whole ).digest( "hex" );
   out.whole_7x5_hex = whole.toString( "hex" );

   return out;
};
