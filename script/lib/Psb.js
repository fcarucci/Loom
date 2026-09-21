/*
 * Psb.js -- a minimal writer for Photoshop Large Document (.psb) files.
 *
 * WHY THIS EXISTS. The plates Loom produces are meant to be assembled in
 * Photoshop, and doing that by hand every time is the tedium the rest of
 * this script exists to remove. Nothing else could write the file:
 * ImageMagick produces MULTI-PAGE TIFFs, which Photoshop opens as a single
 * page -- a Photoshop layered TIFF is a flattened image plus Adobe's layer
 * records in private tag 37724, which ImageMagick does not write. The
 * Python libraries that can write real layer structures bring a Python and
 * numpy dependency, pinned, onto every machine Loom runs on.
 *
 * So: PSB, written directly. No dependency, identical on macOS and Windows.
 *
 * WHY PSB AND NOT PSD. PSD is capped at 2 GB and 30000 px. Five uncompressed
 * 16-bit layers of an 11957x7669 frame come to roughly 3.3 GB. PSB is the
 * same format with 8-byte lengths in four places and a version word of 2.
 *
 * WHAT IT DELIBERATELY DOES NOT DO. No compression (the format allows RLE;
 * raw keeps this simple and the writing I/O-bound rather than CPU-bound),
 * no masks, no adjustment layers, no clipping. Everything here serves one
 * document shape, described by the caller.
 *
 * FORMAT REFERENCE. Adobe Photoshop File Formats Specification. The four
 * PSB-vs-PSD differences implemented below are: the version word, the
 * length of the layer-and-mask section, the length of the layer-info
 * section, and per-channel data lengths.
 */

var Psb = {};

Psb.SIGNATURE      = "8BPS";
Psb.VERSION_PSB    = 2;
Psb.COLOR_MODE_RGB = 3;
Psb.COMPRESSION_RAW = 0;

/* Section-divider types for the 'lsct' tagged block. */
Psb.DIVIDER_OTHER   = 0;
Psb.DIVIDER_OPEN    = 1;   // group header, expanded
Psb.DIVIDER_CLOSED  = 2;   // group header, collapsed
Psb.DIVIDER_BOUNDING = 3;  // the hidden marker that closes a group

/*
 * A growable byte buffer.
 *
 * Everything in a PSB is big-endian, which is NOT this machine's order, so
 * every multi-byte value goes through one of these writers rather than
 * being memcpy'd. Pixel data is the exception and is handled separately --
 * see Psb.writeChannelData -- because it is far too big to pass through
 * here a byte at a time.
 */
Psb.Buffer = function()
{
   this.bytes = [];
};

Psb.Buffer.prototype.u8 = function( v )
{
   this.bytes.push( v & 0xFF );
   return this;
};

Psb.Buffer.prototype.u16 = function( v )
{
   this.bytes.push( ( v >> 8 ) & 0xFF, v & 0xFF );
   return this;
};

Psb.Buffer.prototype.u32 = function( v )
{
   this.bytes.push( ( v >>> 24 ) & 0xFF, ( v >>> 16 ) & 0xFF,
                    ( v >>> 8 ) & 0xFF, v & 0xFF );
   return this;
};

/*
 * 64-bit, written as two 32-bit halves. JavaScript numbers hold integers
 * exactly to 2^53, far beyond any length this writes, so the high half is
 * computed by division rather than by a shift -- >>> is a 32-bit operator
 * and would silently truncate.
 */
Psb.Buffer.prototype.u64 = function( v )
{
   var hi = Math.floor( v / 4294967296 );
   var lo = v - hi * 4294967296;
   this.u32( hi );
   this.u32( lo );
   return this;
};

/* Signed 32-bit, for layer rectangles. */
Psb.Buffer.prototype.i32 = function( v )
{
   return this.u32( v < 0 ? ( v + 4294967296 ) : v );
};

Psb.Buffer.prototype.ascii = function( s )
{
   for ( var i = 0; i < s.length; ++i )
      this.bytes.push( s.charCodeAt( i ) & 0xFF );
   return this;
};

Psb.Buffer.prototype.zeros = function( n )
{
   for ( var i = 0; i < n; ++i )
      this.bytes.push( 0 );
   return this;
};

/* Pascal string, padded so the whole field is a multiple of `pad`. */
Psb.Buffer.prototype.pascal = function( s, pad )
{
   var name = String( s );
   if ( name.length > 255 )
      name = name.substring( 0, 255 );
   this.u8( name.length );
   this.ascii( name );
   var len = 1 + name.length;
   var over = len % pad;
   if ( over != 0 )
      this.zeros( pad - over );
   return this;
};

/*
 * Photoshop's own layer name, as UTF-16BE inside a 'luni' tagged block.
 * The Pascal name in the layer record is legacy and Photoshop ignores it
 * when this is present; both are written because older readers use the
 * former.
 */
Psb.Buffer.prototype.unicodeName = function( s )
{
   var name = String( s );
   this.u32( name.length );
   for ( var i = 0; i < name.length; ++i )
      this.u16( name.charCodeAt( i ) );
   // the block's own length must be a multiple of 4
   var len = 4 + name.length * 2;
   var over = len % 4;
   if ( over != 0 )
      this.zeros( 4 - over );
   return this;
};

Psb.Buffer.prototype.length = function() { return this.bytes.length; };

Psb.Buffer.prototype.toByteArray = function()
{
   return new ByteArray( new Uint8Array( this.bytes ) );
};

/*
 * One tagged block: '8BIM' + key + length + data. Lengths are 4 bytes for
 * the keys used here ('lsct' and 'luni' are not among the handful that take
 * 8-byte lengths in PSB).
 */
Psb.taggedBlock = function( key, payloadBuffer )
{
   var b = new Psb.Buffer;
   b.ascii( "8BIM" ).ascii( key );
   var data = payloadBuffer.bytes;
   b.u32( data.length );
   for ( var i = 0; i < data.length; ++i )
      b.bytes.push( data[i] );
   // blocks are padded to even lengths
   if ( data.length % 2 != 0 )
      b.zeros( 1 );
   return b;
};

/*
 * A Curves adjustment layer's 'curv' payload, with IDENTITY curves.
 *
 * The layer changes nothing on its own; it exists so the curve for a given
 * channel is already there to be dragged. `channelIds` are Photoshop's
 * curve channels EXACTLY as written: 0 = composite (RGB), 1 = red,
 * 2 = green, 3 = blue.
 *
 * The composite is NOT added for you, and adding it was a real bug. A
 * file with both the composite and the target channel present was opened
 * in Photoshop with a visibly bent curve in each variant: the bend landed
 * on the WHITE composite line and moved all three channels. With the
 * composite absent, the same bend showed as the RED overlay line and
 * moved red alone, which is the intent. Only ask for channel 0 when the
 * curve really is meant to act on all three.
 *
 * Photoshop opens the panel's dropdown on RGB regardless; that is
 * application state, not something the file carries. A channel curve
 * still draws as its own coloured line over the composite view.
 *
 * The layout is not in any public spec. It was taken from psd-tools'
 * reader/writer, which parses real Photoshop files, and checked by
 * generating the same structure there and comparing bytes:
 *
 *    B  is_map = 0            (point curves, not a 256-entry map)
 *    H  version = 1
 *    I  count_map             bitmap: bit 0 composite, 1 R, 2 G, 3 B
 *    per curve: H point count, then point count x (H output, H input)
 *    'Crv ' H version = 4, I item count
 *    per item: H channel id, H point count, then the points again
 *    padded to a multiple of four
 *
 * Version 1 carries the channels as a BITMAP and repeats the curves in the
 * 'Crv ' section with explicit ids; both have to agree.
 */
Psb.curvesPayload = function( channelIds )
{
   var ids = [];
   for ( var i = 0; i < channelIds.length; ++i )
      if ( ids.indexOf( channelIds[i] ) < 0 )
         ids.push( channelIds[i] );

   var bitmap = 0;
   for ( var j = 0; j < ids.length; ++j )
      bitmap |= ( 1 << ids[j] );

   var b = new Psb.Buffer;
   b.u8( 0 );                             // is_map: point curves
   b.u16( 1 );                            // version
   b.u32( bitmap );

   function identity( buf )
   {
      buf.u16( 2 );                       // two points
      buf.u16( 0 ).u16( 0 );              // (output, input) = black
      buf.u16( 255 ).u16( 255 );          // ...and white
   }

   for ( var k = 0; k < ids.length; ++k )
      identity( b );

   b.ascii( "Crv " );
   b.u16( 4 );                            // extra marker version
   b.u32( ids.length );
   for ( var m = 0; m < ids.length; ++m )
   {
      b.u16( ids[m] );
      identity( b );
   }

   while ( b.length() % 4 != 0 )
      b.zeros( 1 );
   return b;
};

/*
 * A Hue/Saturation adjustment layer's 'hue2' payload, with everything at
 * zero -- it changes nothing until it is touched.
 *
 * Same provenance as the curves block: psd-tools' reader/writer, checked
 * by generating the structure there and comparing bytes.
 *
 *    H  version = 2
 *    B  COLORIZE flag, then one padding byte
 *    3h colorization  (hue, saturation, lightness)
 *    3h master        (hue, saturation, lightness)
 *    6 x [ 4h range, 3h settings ]      reds .. magentas
 *    padded to a multiple of four
 *
 * The six ranges are Photoshop's own defaults; they are the band edges of
 * the colour ranges in the dropdown, not adjustments, and Photoshop shows
 * the wrong bands if they are left at zero.
 */
Psb.HUE_RANGES = [ [ 315, 345,  15,  45 ],    // reds
                   [  15,  45,  75, 105 ],    // yellows
                   [  75, 105, 135, 165 ],    // greens
                   [ 135, 165, 195, 225 ],    // cyans
                   [ 195, 225, 255, 285 ],    // blues
                   [ 255, 285, 315, 345 ] ];  // magentas

Psb.hueSaturationPayload = function()
{
   var b = new Psb.Buffer;
   b.u16( 2 );                  // version
   /*
    * COLORIZE, and it must be 0.
    *
    * psd-tools calls this field `enable`, which reads like "is this
    * adjustment active" -- it is not. It is the Colorize checkbox, and
    * with it ticked and saturation at 0 the layer drains the colour out of
    * everything it touches. Set to 1 once, which is exactly what it did.
    */
   b.u8( 0 );
   b.u8( 0 );                   // padding
   b.u16( 0 ).u16( 0 ).u16( 0 );   // colorization: hue, saturation, lightness
   b.u16( 0 ).u16( 0 ).u16( 0 );   // master: the same, all neutral
   for ( var i = 0; i < Psb.HUE_RANGES.length; ++i )
   {
      var r = Psb.HUE_RANGES[i];
      for ( var j = 0; j < 4; ++j )
         b.u16( r[j] );
      b.u16( 0 ).u16( 0 ).u16( 0 );   // no shift in this band
   }
   while ( b.length() % 4 != 0 )
      b.zeros( 1 );
   return b;
};

/*
 * The tagged blocks that follow a layer record: the unicode name, the
 * section divider for a group marker, and the adjustment data for a
 * curves or hue/saturation layer.
 */
Psb.layerExtraBlocks = function( layer )
{
   var b = new Psb.Buffer;

   var luni = new Psb.Buffer;
   luni.unicodeName( layer.name );
   var lb = Psb.taggedBlock( "luni", luni );
   for ( var i = 0; i < lb.bytes.length; ++i )
      b.bytes.push( lb.bytes[i] );

   if ( layer.divider != null )
   {
      var lsct = new Psb.Buffer;
      lsct.u32( layer.divider );
      /*
       * A group header also carries its own blend mode here. Photoshop
       * reads the group's blend from THIS copy, not from the layer
       * record's -- a group set to Screen with only the record updated
       * opens as Pass Through.
       */
      if ( layer.divider == Psb.DIVIDER_OPEN || layer.divider == Psb.DIVIDER_CLOSED )
         lsct.ascii( "8BIM" ).ascii( layer.blend || "norm" );
      var db = Psb.taggedBlock( "lsct", lsct );
      for ( var j = 0; j < db.bytes.length; ++j )
         b.bytes.push( db.bytes[j] );
   }

   if ( layer.curves != null )
   {
      var cb = Psb.taggedBlock( "curv",
                   Psb.curvesPayload( layer.curves ) );
      for ( var c = 0; c < cb.bytes.length; ++c )
         b.bytes.push( cb.bytes[c] );
   }

   if ( layer.hueSaturation )
   {
      var hb = Psb.taggedBlock( "hue2", Psb.hueSaturationPayload() );
      for ( var h = 0; h < hb.bytes.length; ++h )
         b.bytes.push( hb.bytes[h] );
   }
   return b;
};

/*
 * One layer record. `channelLengths` is the byte count each channel's data
 * will occupy INCLUDING its 2-byte compression word, which the caller
 * computes once and reuses when it writes the data itself.
 */
Psb.isAdjustment = function( layer )
{
   return ( layer.curves != null ) || ( layer.hueSaturation === true );
};

/*
 * The layer whose pixels stand in for the flattened composite.
 *
 * The bottom-most visible pixel layer is written rather than a real
 * composite: compositing screen and luminosity blends here would mean a
 * second full pass over every layer to produce something Photoshop
 * discards on open.
 *
 * Group dividers and adjustment layers carry no pixels, so they are never
 * eligible. Visibility is preferred but not required -- a document whose
 * pixel layers are all hidden still needs a composite section, so the
 * first pixel layer of any visibility is taken rather than none at all.
 */
Psb.compositeBaseLayer = function( layers )
{
   var pixelLayer = function( layer )
   {
      return layer.divider == null && !Psb.isAdjustment( layer );
   };
   for ( var p = 0; p < layers.length; ++p )
      if ( pixelLayer( layers[p] ) && layers[p].visible )
         return layers[p];
   for ( var q = 0; q < layers.length; ++q )
      if ( pixelLayer( layers[q] ) )
         return layers[q];
   return null;
};

Psb.layerRecord = function( layer, width, height, channelLengths )
{
   var b = new Psb.Buffer;

   /*
    * Group markers are zero-area. Photoshop writes them with an empty
    * rectangle and a single, empty channel; giving them the document's
    * bounds makes Photoshop treat them as real pixel layers.
    */
   // adjustment layers hold no pixels of their own, exactly like the
   // markers: empty rectangle, empty channels
   var isMarker = ( layer.divider != null ) || Psb.isAdjustment( layer );
   if ( isMarker )
      b.i32( 0 ).i32( 0 ).i32( 0 ).i32( 0 );
   else
      b.i32( 0 ).i32( 0 ).i32( height ).i32( width );

   var ids = layer.channelIds;
   b.u16( ids.length );
   for ( var c = 0; c < ids.length; ++c )
   {
      b.u16( ids[c] < 0 ? ( ids[c] + 65536 ) : ids[c] );
      b.u64( channelLengths[c] );          // PSB: 8 bytes, PSD would be 4
   }

   b.ascii( "8BIM" );
   b.ascii( layer.blend || "norm" );
   b.u8( layer.opacity == null ? 255 : layer.opacity );
   /*
    * Clipping: 0 is a base layer, 1 clips to the layer BELOW. A clipped
    * adjustment affects only that one layer instead of everything under it
    * in the group -- which for a saturation layer over the star plate is
    * the difference between colouring the stars and colouring the stack.
    */
   b.u8( layer.clipping ? 1 : 0 );
   /*
    * Flags, bit 1 = "transparency protected", bit 2 = HIDDEN. Photoshop
    * writes bit 4 (0x08) too, meaning "bit 5 has useful information", plus
    * bit 5 (0x10) "pixel data irrelevant to appearance" on group markers.
    */
   var flags = 0x08;
   if ( !layer.visible )
      flags |= 0x02;
   if ( layer.divider != null )
      flags |= 0x10;
   b.u8( flags );
   b.u8( 0 );                               // filler

   var extra = new Psb.Buffer;
   extra.u32( 0 );                          // layer mask data: none
   extra.u32( 0 );                          // blending ranges: none
   extra.pascal( layer.name, 4 );
   var blocks = Psb.layerExtraBlocks( layer );
   for ( var k = 0; k < blocks.bytes.length; ++k )
      extra.bytes.push( blocks.bytes[k] );

   b.u32( extra.length() );
   for ( var m = 0; m < extra.bytes.length; ++m )
      b.bytes.push( extra.bytes[m] );

   return b;
};

/*
 * Flattens the caller's nested description into the order Photoshop stores
 * layers in: BOTTOM FIRST, and each group expressed as three parts --
 *
 *    bounding divider (hidden, closes the group)
 *    ... the group's own layers, bottom first ...
 *    group header (carries the name and blend mode)
 *
 * which is the reverse of how anyone reading the file would describe it,
 * and the single most common way a hand-written PSD comes out inside out.
 *
 * Input: an array of entries, bottom first, each either
 *    { name, window, visible, blend }                     -- a pixel layer
 *    { name, group: [ ...entries, bottom first... ], blend, visible, open }
 */
/*
 * ORDER, ESTABLISHED THE HARD WAY.
 *
 * Callers describe a document BOTTOM FIRST -- "HSO at the bottom, stars on
 * top" -- and that is exactly the order records are written in. The last
 * record written is the TOP layer in Photoshop's panel.
 *
 * A reversal was added here once, on the strength of psd-tools' composite()
 * of a two-layer probe, which rendered the first-written layer as though it
 * were on top. Photoshop then opened the result upside down at every level.
 * psd-tools lists layers bottom-first and its composite of a file with no
 * transparency channels is not a reliable guide to stacking; PHOTOSHOP IS
 * THE AUTHORITY, and it was checked against a real 3.3 GB export.
 *
 * So: no reversal. Do not add one without opening the file in Photoshop.
 */

/* A group's opening divider: bottom-first, so this is written FIRST. */
Psb.groupOpenRecord = function()
{
   return { name: "</Layer group>", divider: Psb.DIVIDER_BOUNDING,
            visible: true, blend: "norm", opacity: 255,
            channelIds: [ 0, 1, 2, -1 ], window: null };
};

/* A group's closing divider, which is the one carrying its name. */
Psb.groupCloseRecord = function( e )
{
   return { name: e.name,
            divider: ( e.open === false ) ? Psb.DIVIDER_CLOSED : Psb.DIVIDER_OPEN,
            visible: ( e.visible !== false ),
            blend: e.blend || "pass",
            opacity: ( e.opacity == null ) ? 255 : e.opacity,
            channelIds: [ 0, 1, 2, -1 ], window: null };
};

/*
 * An ordinary layer. Note the channel ids differ from a divider's: a
 * pixel layer leads with the alpha slot.
 */
Psb.layerRecordFor = function( e )
{
   return { name: e.name, divider: null,
            visible: ( e.visible !== false ),
            blend: e.blend || "norm",
            opacity: ( e.opacity == null ) ? 255 : e.opacity,
            channelIds: [ -1, 0, 1, 2 ],
            curves: ( e.curves != null ) ? e.curves : null,
            hueSaturation: ( e.hueSaturation === true ),
            clipping: ( e.clipping === true ),
            mask: ( e.mask === true ),
            window: e.window };
};

Psb.flatten = function( entries )
{
   var out = [];
   for ( var i = 0; i < entries.length; ++i )
   {
      var e = entries[i];
      if ( e.group == null )
      {
         out.push( Psb.layerRecordFor( e ) );
         continue;
      }

      out.push( Psb.groupOpenRecord() );
      var inner = Psb.flatten( e.group );
      for ( var j = 0; j < inner.length; ++j )
         out.push( inner[j] );
      out.push( Psb.groupCloseRecord( e ) );
   }
   return out;
};

/*
 * Writes one channel's samples, big-endian, to an open file.
 *
 * This is the only place in the writer that touches bulk pixel data, and
 * the only per-sample loop in it. Image.pixelData hands over the channel as
 * an ArrayBuffer in one call -- no per-pixel reads -- and the loop below
 * does nothing but swap byte order, because PSB is big-endian and this
 * machine is not.
 *
 * Written in chunks so peak memory stays bounded: a full 11957x7669
 * channel is 183 MB, and holding a swapped copy of every channel at once
 * would be pointless when the file is written sequentially anyway.
 *
 * Being the only per-sample loop, it is also the only part of the writer
 * worth threading, and since PixInsight 1.9.5 it is threaded -- see
 * Psb.canSwapInParallel below for how that is decided and Psb.swapRange for
 * the loop itself. The chunks, and the order in which they are written, are
 * exactly the same either way: PSB is a byte-exact format and the layer
 * data section has to be assembled in the order the layer records declared.
 */
Psb.CHUNK_SAMPLES = 4 * 1024 * 1024;   // 8 MB per chunk at 16 bits

/*
 * The swap, over one half-open range of samples of one chunk.
 *
 * Kept as a function of its own because it is the body of a thread as well
 * as the serial loop, and a thread body is compiled from its source in a
 * runtime where nothing of this script exists: it may use its argument and
 * nothing else. That is why the ranges and the buffers are passed in rather
 * than read from Psb.
 */
Psb.swapRange = function( src, dst, begin, end )
{
   for ( var k = begin; k < end; ++k )
   {
      var v = src[k];
      dst[k*2]     = ( v >> 8 ) & 0xFF;
      dst[k*2 + 1] = v & 0xFF;
   }
};

/*
 * The same loop, as the body of a thread.
 *
 * The source of Psb.swapRange is pasted into the thread's own script rather
 * than the loop being written out a second time. A thread body is compiled
 * in a runtime where this script does not exist, so it cannot call
 * Psb.swapRange -- but it can carry its text, which is what the PJSR
 * Parallel library calls a preamble. Two copies of a byte-order loop, one
 * serial and one threaded, is exactly the arrangement where somebody fixes
 * one of them; there is only one here, and the serial path and the threads
 * run the same characters.
 *
 * The body reattaches the two shared buffers -- the only bulk data that
 * reaches a thread without being copied -- and swaps its own slice of the
 * chunk in place.
 */
Psb.swapThreadSource = function()
{
   return "(function( d )\n"
        + "{\n"
        + "   var swapRange = " + Psb.swapRange.toString() + ";\n"
        + "   swapRange( new Uint16Array( Thread.sharedBuffer( d.src ) ),\n"
        + "              new Uint8Array( Thread.sharedBuffer( d.dst ) ),\n"
        + "              d.begin, d.end );\n"
        + "   return d.end - d.begin;\n"
        + "});\n";
};

/*
 * How many threads the swap should use.
 *
 * Measured on a 16-processor machine, one 6000x6000 channel, chunked as
 * below: 2 threads 1.30x, 4 threads 1.67x, 6 and 8 threads 1.88x, 12
 * threads 1.30x, and 16 threads 0.11x -- nine times SLOWER than serial,
 * because leaving no processor for the thread that is handing out the work
 * turns the whole group into a scheduling problem. The curve is flat from
 * six threads on and falls off a cliff past twelve, so this caps well short
 * of the processor count rather than trying to find the exact peak.
 *
 * The ceiling is low because a byte swap does almost no arithmetic per
 * sample it moves; the PJSR Thread documentation measures the same ceiling,
 * three to four times, for any loop that is pure data movement.
 */
Psb.PARALLEL_MAX_THREADS = 8;

/*
 * Below this many samples the swap is a few milliseconds and starting
 * threads cannot pay for itself.
 */
Psb.PARALLEL_MIN_SAMPLES = 1024 * 1024;

/*
 * Whether threads can be used at all, decided once and remembered.
 *
 * Nothing here is assumed from the core version. Thread, SharedArrayBuffer
 * and Thread.shareBuffer arrived in PixInsight 1.9.5, and on anything older
 * they are simply not there; the check is for the objects themselves. This
 * deliberately does NOT #include <pjsr/utility/Parallel.js>, which would be
 * the tidier API: an #include that cannot be resolved makes PixInsight
 * discard the whole script silently, so depending on a file that only 1.9.5
 * ships would turn a missing feature into an unrunnable Loom. Thread is a
 * runtime global and can be tested for.
 *
 * The test is a real swap on a real worker rather than a feature test: it
 * proves that a thread starts, that the shared buffer reaches it, and that
 * what it wrote is visible here. If any of that fails, for any reason, the
 * writer stays serial and still produces the same file.
 */
Psb.parallelReady = null;      // null until tested

Psb.canSwapInParallel = function()
{
   if ( Psb.parallelReady !== null )
      return Psb.parallelReady;

   Psb.parallelReady = false;
   try
   {
      if ( typeof Thread == "undefined" || typeof SharedArrayBuffer == "undefined" )
         return false;
      if ( typeof Thread.shareBuffer != "function" || typeof Thread.sharedBuffer != "function" )
         return false;
      if ( !( Thread.numberOfProcessors > 1 ) )
         return false;

      var srcSab = new SharedArrayBuffer( 4 );     // two samples
      var dstSab = new SharedArrayBuffer( 4 );
      var sdesc = Thread.shareBuffer( srcSab );
      var ddesc = Thread.shareBuffer( dstSab );
      try
      {
         var sv = new Uint16Array( srcSab );
         sv[0] = 0x0102; sv[1] = 0xFFEE;
         var t = new Thread( Psb.swapThreadSource(),
                             { src: sdesc, dst: ddesc, begin: 0, end: 2 },
                             { pooled: true } );
         t.start();
         t.wait();
         if ( t.error.length > 0 )
            return false;
         var dv = new Uint8Array( dstSab );
         Psb.parallelReady = ( dv[0] == 0x01 && dv[1] == 0x02
                            && dv[2] == 0xFF && dv[3] == 0xEE );
      }
      finally
      {
         Thread.releaseBuffer( sdesc );
         Thread.releaseBuffer( ddesc );
      }
   }
   catch ( e )
   {
      Psb.parallelReady = false;
   }
   return Psb.parallelReady;
};

/*
 * The parallel swap. Same chunks, same order, same bytes.
 *
 * The two buffers are allocated once per channel and reused by every chunk,
 * and they are SharedArrayBuffers because that is the only bulk data that
 * reaches a thread without being copied -- everything else crosses by
 * structured clone, which would copy the chunk into every thread and back
 * out again and cost more than the swap it is trying to speed up.
 *
 * The threads are pooled. A full frame is some forty channels of twenty-two
 * chunks, so this starts many hundreds of short-lived groups, and a fresh
 * thread runtime costs milliseconds against a warm worker's microseconds.
 * Only the swap body runs on those workers and it touches no global
 * scope, so nothing is left behind on a worker for whatever runs next.
 *
 * Each thread owns a disjoint slice of the chunk, so no synchronization is
 * needed; the group is joined before the chunk is written, which is what
 * keeps the file order identical to the serial path.
 */
/*
 * Divide `n` samples across `threads`, started and ready to be joined.
 *
 * The remainder is spread one sample at a time rather than dumped on the
 * last thread, so no worker gets a chunk the others have to wait for.
 */
Psb.startSwapThreads = function( body, sdesc, ddesc, n, threads )
{
   var group = [];
   var base = Math.floor( n/threads );
   var rem = n - base*threads;
   var begin = 0;

   for ( var t = 0; t < threads; ++t )
   {
      var count = base + ( ( t < rem ) ? 1 : 0 );
      if ( count > 0 )
         group.push( new Thread( body,
                                 { src: sdesc, dst: ddesc,
                                   begin: begin, end: begin + count },
                                 { pooled: true } ) );
      begin += count;
   }

   for ( var a = 0; a < group.length; ++a )
      group[a].start();
   return group;
};

/*
 * Join EVERY thread, then raise the first error if there was one.
 *
 * Never raise before joining: a script must not be left with threads
 * still running.
 */
Psb.joinSwapThreads = function( group )
{
   var failure = "";
   for ( var i = 0; i < group.length; ++i )
   {
      group[i].wait();
      if ( group[i].error.length > 0 && failure.length == 0 )
         failure = group[i].error;
   }
   if ( failure.length > 0 )
      throw new Error( "Psb: parallel byte swap failed: " + failure );
};

Psb.writeChannelDataParallel = function( file, src, sampleCount, threads )
{
   var chunk = Math.min( Psb.CHUNK_SAMPLES, sampleCount );
   var srcSab = new SharedArrayBuffer( chunk * 2 );
   var dstSab = new SharedArrayBuffer( chunk * 2 );
   var sdesc = Thread.shareBuffer( srcSab );
   var ddesc = Thread.shareBuffer( dstSab );
   var body = Psb.swapThreadSource();
   try
   {
      var sv = new Uint16Array( srcSab );
      var dv = new Uint8Array( dstSab );
      var i = 0;
      while ( i < sampleCount )
      {
         var n = Math.min( chunk, sampleCount - i );
         sv.set( src.subarray( i, i + n ) );

         Psb.joinSwapThreads(
            Psb.startSwapThreads( body, sdesc, ddesc, n, threads ) );

         /*
          * The last chunk of a channel is usually short. It is copied out
          * to a buffer of exactly the right length rather than handed over
          * as a subarray of the shared one: ByteArray is a core object and
          * how it reads a view's length is not something this writer should
          * be betting the file format on. One extra copy, once per channel.
          */
         file.write( new ByteArray( ( n == chunk ) ? dv
                                                   : new Uint8Array( dv.subarray( 0, n*2 ) ) ) );
         i += n;
      }
   }
   finally
   {
      Thread.releaseBuffer( sdesc );
      Thread.releaseBuffer( ddesc );
   }
};

Psb.writeChannelData = function( file, image, channel, sampleCount )
{
   var src = new Uint16Array( image.pixelData( channel ) );

   if ( sampleCount >= Psb.PARALLEL_MIN_SAMPLES && Psb.canSwapInParallel() )
   {
      Psb.writeChannelDataParallel( file, src, sampleCount,
                                    Math.min( Thread.numberOfProcessors,
                                              Psb.PARALLEL_MAX_THREADS ) );
      return;
   }

   var i = 0;
   while ( i < sampleCount )
   {
      var n = Math.min( Psb.CHUNK_SAMPLES, sampleCount - i );
      var out = new Uint8Array( n * 2 );
      Psb.swapRange( src.subarray( i, i + n ), out, 0, n );
      file.write( new ByteArray( out ) );
      i += n;
   }
};

/* Photoshop's four-character blend keys. */
Psb.BLEND_NORMAL       = "norm";
Psb.BLEND_SCREEN       = "scrn";
Psb.BLEND_LUMINOSITY   = "lum ";   // the trailing space is part of the key
Psb.BLEND_LINEAR_LIGHT = "lLit";
Psb.BLEND_SOFT_LIGHT   = "sLit";
Psb.BLEND_PASS_THROUGH = "pass";

/*
 * Writes the whole document.
 *
 * `entries` is bottom-first and nested; see Psb.flatten. Every pixel layer
 * must carry a 16-bit window of exactly `width` x `height`.
 *
 * No transparency channel is written. A layer without one is fully opaque,
 * which every layer here is, and omitting it saves 183 MB per layer on a
 * frame this size.
 */
/*
 * One image-resource block: '8BIM' + id + name + length + data, with both
 * the name and the data padded to even lengths.
 */
Psb.RESOURCE_ICC_PROFILE = 1039;   // 0x040F

Psb.imageResource = function( id, dataBytes )
{
   var b = new Psb.Buffer;
   b.ascii( "8BIM" );
   b.u16( id );
   b.u16( 0 );                         // empty Pascal name, padded to even
   b.u32( dataBytes.length );
   for ( var i = 0; i < dataBytes.length; ++i )
      b.bytes.push( dataBytes[i] );
   if ( dataBytes.length % 2 != 0 )
      b.zeros( 1 );
   return b;
};

/*
 * How many bytes each layer's three channels occupy.
 *
 * A divider or an adjustment layer has NO pixels -- it declares a
 * zero-area channel, which is the compression word and nothing else.
 * Every layer declares three channels: R, G, B.
 */
Psb.channelLengthsFor = function( layers, width, height )
{
   var pixelBytes = 2 + width*height*2;     // compression word + raw samples
   var markerBytes = 2;                     // compression word, zero area

   var lengths = [];
   for ( var i = 0; i < layers.length; ++i )
   {
      var empty = ( layers[i].divider != null ) || Psb.isAdjustment( layers[i] );
      var per = empty ? markerBytes : pixelBytes;
      lengths.push( [ per, per, per ] );
      layers[i].channelIds = [ 0, 1, 2 ];
   }
   return lengths;
};

/* The layer records section, which must be sized before anything is written. */
Psb.layerRecordsFor = function( layers, width, height, channelLengths )
{
   var records = new Psb.Buffer;
   records.u16( layers.length );
   for ( var r = 0; r < layers.length; ++r )
   {
      var rec = Psb.layerRecord( layers[r], width, height, channelLengths[r] );
      for ( var b = 0; b < rec.bytes.length; ++b )
         records.bytes.push( rec.bytes[b] );
   }
   return records;
};

/*
 * The file header, including image resources.
 *
 * The ICC profile goes here deliberately. Without it Photoshop opens the
 * document untagged and applies whatever its policy says, which for
 * ProPhoto data is a visible shift. The rest of Loom's outputs carry
 * their profile because PixInsight embeds it on save; this file is
 * written by hand.
 */
Psb.headerFor = function( width, height, iccProfile, layerAndMaskLength, layerInfoLength )
{
   var h = new Psb.Buffer;
   h.ascii( Psb.SIGNATURE );
   h.u16( Psb.VERSION_PSB );
   h.zeros( 6 );
   h.u16( 3 );                            // channels in the composite
   h.u32( height );
   h.u32( width );
   h.u16( 16 );                           // bits per sample
   h.u16( Psb.COLOR_MODE_RGB );
   h.u32( 0 );                            // colour mode data: none

   if ( iccProfile != null && iccProfile.length > 0 )
   {
      var icc = [];
      for ( var i = 0; i < iccProfile.length; ++i )
         icc.push( iccProfile.at( i ) );
      var res = Psb.imageResource( Psb.RESOURCE_ICC_PROFILE, icc );
      h.u32( res.length() );
      for ( var rb = 0; rb < res.bytes.length; ++rb )
         h.bytes.push( res.bytes[rb] );
   }
   else
      h.u32( 0 );

   h.u64( layerAndMaskLength );
   h.u64( layerInfoLength );
   return h;
};

/* One layer's pixels, in the order its record declared. */
Psb.writeLayerChannels = function( file, layer, samples )
{
   if ( layer.divider != null || Psb.isAdjustment( layer ) )
   {
      // zero-area: the compression word and nothing else
      var empty = new Psb.Buffer;
      empty.u16( Psb.COMPRESSION_RAW ).u16( Psb.COMPRESSION_RAW )
           .u16( Psb.COMPRESSION_RAW );
      file.write( empty.toByteArray() );
      return;
   }

   var img = layer.window.mainView.image;
   for ( var ch = 0; ch < 3; ++ch )
   {
      var cw = new Psb.Buffer;
      cw.u16( Psb.COMPRESSION_RAW );
      file.write( cw.toByteArray() );
      // a mono plate fills all three channels from its only one
      Psb.writeChannelData( file, img, ( img.numberOfChannels > 1 ) ? ch : 0, samples );
   }
};

/*
 * The flattened composite.
 *
 * Photoshop rebuilds its view from the layers and uses this only as a
 * preview, but the section is mandatory and other readers show it. See
 * Psb.compositeBaseLayer for which layer's pixels go here.
 */
Psb.writeComposite = function( file, layers, samples )
{
   var base = Psb.compositeBaseLayer( layers );

   var ch0 = new Psb.Buffer; ch0.u16( Psb.COMPRESSION_RAW );
   file.write( ch0.toByteArray() );

   var img = base.window.mainView.image;
   for ( var c = 0; c < 3; ++c )
      Psb.writeChannelData( file, img, ( img.numberOfChannels > 1 ) ? c : 0, samples );
};

Psb.write = function( path, entries, width, height, iccProfile )
{
   // bottom-first, straight through: see the note above Psb.flatten
   var layers = Psb.flatten( entries );
   var samples = width * height;

   var channelLengths = Psb.channelLengthsFor( layers, width, height );
   var records = Psb.layerRecordsFor( layers, width, height, channelLengths );

   var channelDataBytes = 0;
   for ( var c = 0; c < layers.length; ++c )
      channelDataBytes += channelLengths[c][0] * 3;

   var layerInfoLength = records.length() + channelDataBytes;
   // PSB pads the layer info section to an even length
   var layerInfoPad = ( layerInfoLength % 2 != 0 ) ? 1 : 0;
   var layerAndMaskLength = 8 + layerInfoLength + layerInfoPad + 4; // +len +global mask

   var file = new File;
   file.createForWriting( path );
   try
   {
      file.write( Psb.headerFor( width, height, iccProfile,
                                 layerAndMaskLength,
                                 layerInfoLength + layerInfoPad ).toByteArray() );
      file.write( records.toByteArray() );

      for ( var L = 0; L < layers.length; ++L )
         Psb.writeLayerChannels( file, layers[L], samples );

      if ( layerInfoPad )
      {
         var pad = new Psb.Buffer; pad.zeros( 1 );
         file.write( pad.toByteArray() );
      }

      // global layer mask info: none
      var g = new Psb.Buffer; g.u32( 0 );
      file.write( g.toByteArray() );

      Psb.writeComposite( file, layers, samples );
   }
   finally
   {
      try { file.close(); } catch ( e ) {}
      /*
       * Give back the warm thread runtimes the swap left behind. Each is a
       * whole V8 isolate, and Loom is a long-lived script holding several
       * full-size images; there is no reason to keep eight of them alive
       * between exports for the sake of a few milliseconds at the next one.
       */
      if ( Psb.parallelReady === true && typeof Thread != "undefined"
        && typeof Thread.releasePool == "function" )
         try { Thread.releasePool(); } catch ( e2 ) {}
   }
   return path;
};
