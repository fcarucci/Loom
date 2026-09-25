/*
 * Loom Fly-Through: frames, output, encoding, the draft player.
 */
var Render = {};

/* ---------------------------------------------------------------------------
 * Resampling. The backdrop zoom is a uniform scale K about the target, so
 * every output column maps to one source x and every row to one source y:
 * the weights are computed once per axis and the 2-D sample is separable.
 * ------------------------------------------------------------------------ */

/* Catmull-Rom weights for fraction f: interpolating, so exact at whole pixels. */
Render.cubicWeights = function( f )
{
   var f2 = f*f, f3 = f2*f;
   return [ 0.5*( -f3 + 2*f2 - f ), 0.5*( 3*f3 - 5*f2 + 2 ), 0.5*( -3*f3 + 4*f2 + f ), 0.5*( f3 - f2 ) ];
};

/*
 * For each of `outN` output pixels along one axis: the source position
 * b = tp + (q - tp)/K, where q = cropStart + (u + 0.5)*cropLen/outN - 0.5 is
 * the camera pixel the output pixel covers, and the source taps and weights
 * that sample it. Output pixel u uses taps start[u] .. start[u+1]-1 of
 * idx/w (clamped to the image).
 *
 * When one output pixel spans more than one source pixel (m = step/K > 1,
 * which is every real preset: a 26 MP image is ~3x for 1080p), the kernel
 * is a normalised tent as wide as the step. A fixed 4-tap kernel there
 * point-samples: as the camera moves the phase slides, and a star's light
 * swung between 77% and 100% (measured), which reads as twinkling. At 1:1
 * and when zooming in, the kernel is Catmull-Rom (or bilinear for drafts),
 * exact at whole pixels, so frame 0 stays the image.
 */
Render.axisWeights = function( outN, cropStart, cropLen, tp, K, srcN, kernel )
{
   var step = cropLen/outN, m = step/K, start = new Int32Array( outN + 1 ), idx = [], w = [];
   for ( var u = 0; u < outN; ++u )
   {
      start[u] = idx.length;
      var q = cropStart + ( u + 0.5 )*step - 0.5, b = tp + ( q - tp )/K;
      if ( Math.abs( b - Math.round( b ) ) < 1e-9 )
         b = Math.round( b );                       // whole pixels stay whole: frame 0 is exact
      var taps = ( m > 1.0001 ) ? Render.tentTaps( b, m ) : Render.kernelTaps( b, kernel );
      for ( var k = 0; k < taps.i.length; ++k )
      {
         idx.push( Math.max( 0, Math.min( srcN - 1, taps.i[k] ) ) );
         w.push( taps.w[k] );
      }
   }
   start[outN] = idx.length;
   return { start: start, idx: Int32Array.from( idx ), w: Float32Array.from( w ), outN: outN };
};

/* Catmull-Rom (or bilinear) taps around b: interpolating, exact at whole pixels. */
Render.kernelTaps = function( b, kernel )
{
   var i0 = Math.floor( b ), f = b - i0;
   var ws = ( kernel == "bilinear" ) ? [ 0, 1 - f, f, 0 ] : Render.cubicWeights( f );
   return { i: [ i0 - 1, i0, i0 + 1, i0 + 2 ], w: ws };
};

/* A normalised tent of half-width m centred on b: an area filter for minification. */
Render.tentTaps = function( b, m )
{
   var i = [], w = [], sum = 0;
   for ( var x = Math.ceil( b - m ); x <= Math.floor( b + m ); ++x )
   {
      var t = 1 - Math.abs( x - b )/m;
      if ( t <= 0 ) continue;
      i.push( x ); w.push( t ); sum += t;
   }
   for ( var k = 0; k < w.length; ++k ) w[k] /= sum;
   return { i: i, w: w };
};

/*
 * Separable resample of a w x h channel through per-axis weights, in two
 * passes: rows first (only the source rows some output row needs), then
 * columns.
 */
Render.resample = function( buf, w, h, ax, ay )
{
   var outW = ax.outN, outH = ay.outN, out = new Float32Array( outW*outH );
   var rmin = Infinity, rmax = -1, k, u, v;
   for ( k = 0; k < ay.idx.length; ++k ) { rmin = Math.min( rmin, ay.idx[k] ); rmax = Math.max( rmax, ay.idx[k] ); }
   if ( rmax < 0 ) return out;
   var rows = rmax - rmin + 1, tmp = new Float32Array( rows*outW );
   for ( var r = 0; r < rows; ++r )
   {
      var row = ( rmin + r )*w, o = r*outW;
      for ( u = 0; u < outW; ++u )
      {
         var s = 0;
         for ( k = ax.start[u]; k < ax.start[u + 1]; ++k ) s += ax.w[k]*buf[row + ax.idx[k]];
         tmp[o + u] = s;
      }
   }
   for ( v = 0; v < outH; ++v )
      for ( k = ay.start[v]; k < ay.start[v + 1]; ++k )
      {
         var wy = ay.w[k];
         if ( wy == 0 ) continue;
         var t = ( ay.idx[k] - rmin )*outW, ov = v*outW;
         for ( u = 0; u < outW; ++u ) out[ov + u] += wy*tmp[t + u];
      }
   return out;
};

/* Bilinear sample of a rw x rh patch, zero outside. */
Render.patchSample = function( patch, rw, rh, x, y )
{
   var x0 = Math.floor( x ), y0 = Math.floor( y ), fx = x - x0, fy = y - y0, s = 0;
   for ( var j = 0; j < 2; ++j )
      for ( var i = 0; i < 2; ++i )
      {
         var xx = x0 + i, yy = y0 + j;
         if ( xx < 0 || yy < 0 || xx >= rw || yy >= rh ) continue;
         s += ( i ? fx : 1 - fx )*( j ? fy : 1 - fy )*patch[yy*rw + xx];
      }
   return s;
};

/*
 * The mip level to draw from when a sprite pixel (grown by g) is 1/fx of an
 * output pixel: one halving short of a level pixel filling the output
 * pixel, so it is still averaged from 2-3 samples a side (the coarsest
 * level put a sharp core on a single sample: 27% off at the peak).
 */
Render.mipLevel = function( fx, g )
{
   var r = fx/g;
   return r >= 4 ? Math.floor( Math.log( r )/Math.LN2 ) - 1 : 0;
};

/* A patch halved L times (2x2 averages, zero beyond the edge), cached on the patch. */
Render.mipOf = function( patch, rw, rh, L )
{
   var cache = patch._mips || ( patch._mips = [ { d: patch, w: rw, h: rh } ] );
   for ( var l = cache.length; l <= L; ++l ) cache.push( Render.halve( cache[l - 1] ) );
   return cache[L];
};

/* A mip level halved: each pixel the mean of a 2 x 2 block, zero beyond the edge. */
Render.halve = function( p )
{
   var w = Math.ceil( p.w/2 ), h = Math.ceil( p.h/2 ), d = new Float32Array( w*h );
   var at = function( x, y ) { return ( x < p.w && y < p.h ) ? p.d[y*p.w + x] : 0; };
   for ( var y = 0; y < h; ++y )
      for ( var x = 0; x < w; ++x )
         d[y*w + x] = ( at( 2*x, 2*y ) + at( 2*x + 1, 2*y ) + at( 2*x, 2*y + 1 ) + at( 2*x + 1, 2*y + 1 ) )/4;
   return { d: d, w: w, h: h };
};

/*
 * Adds one sprite channel into `acc` (outW x outH), centred at (cx, cy)
 * and grown by g. `cam` maps output to camera pixels: q = cam.x + (u +
 * 0.5)*cam.fx - 0.5. With a core radius rc, the core is drawn at its own
 * size, times k at its centre easing to kOuter at rc, and only the light
 * beyond it is stretched outward (Fly.radialSource), times kOuter; without
 * one, the whole sprite is magnified by g, times k.
 */
Render.drawSprite = function( acc, outW, outH, patch, sp, cx, cy, g, k, cam, kOuter, rc, how )
{
   Render.drawSprites( [ acc ], outW, outH, [ patch ], sp, cx, cy, g, [ k ], cam, [ kOuter ], rc, how );
};

/*
 * Render.drawSprite for several channels at once -- accs, patches, ks and
 * kOuters one per channel, the same sprite geometry -- so where each
 * sample lands is worked out once, not once per channel.
 */
Render.drawSprites = function( accs, outW, outH, patches, sp, cx, cy, g, ks, cam, kOuters, rc, how )
{
   var seen = ( how && how.seen != null ) ? how.seen : 1, spike = how && how.spike;
   var radial = !spike && ( rc > 0 && ( g != 1 || seen < 1 ) ), r = sp.rect, rw = r.x1 - r.x0, rh = r.y1 - r.y0;
   // spikes stretch along their length only (how.spike = { length, angles }): shorter for what is hidden
   var stretch = spike ? spike.length*Math.max( 0.05, seen ) : 1;
   var b = Render.spriteBounds( sp, cx, cy, Math.max( g, stretch ), cam, outW, outH );
   /*
    * When a sprite pixel is smaller than an output pixel, the output pixel
    * averages n x n samples over its footprint, so a small, moving star
    * keeps its light instead of being hit or missed (measured: one point
    * sample lost 14% at 1/3 scale). n = 1 at 1:1 and when zooming in.
    */
   var gs = ( radial || spike ) ? 1 : g;      // the core, and a spike's width, are sampled at their own scale
   // shrunk far down, sample a pre-shrunk copy (a mip level), not n x n points of the full one
   var L = ( how && how.mip === false ) ? 0 : Render.mipLevel( Math.min( cam.fx, cam.fy ), gs ), f2 = 1 << L;
   var ctx = { mps: patches.map( function( p ) { return L ? Render.mipOf( p, rw, rh, L ) : { d: p, w: rw, h: rh }; } ), f2: f2, ox: sp.det.x - r.x0, oy: sp.det.y - r.y0,
               cx: cx, cy: cy, g: g, rc: rc, seen: seen, radial: radial, cam: cam,
               ks: ks.map( function( k ) { return spike ? k*seen : k; } ),
               kOs: ks.map( function( k, i ) { return spike ? k*seen : ( ( kOuters[i] != null ) ? kOuters[i] : k )*seen; } ),
               nx: Math.max( 1, Math.ceil( cam.fx/( gs*f2 ) ) ), ny: Math.max( 1, Math.ceil( cam.fy/( gs*f2 ) ) ),
               axes: spike ? spike.angles.map( function( a ) { return [ Math.cos( a ), Math.sin( a ) ]; } ) : null, stretch: stretch };
   // most of a sprite's box is dark (the glow is a disc, the spikes are lines): only the
   // output pixels whose samples can reach some channel's light are sampled (Render.reachTest)
   var darks = [], nc = patches.length, ch, all = false;
   for ( ch = 0; ch < nc; ++ch )
   {
      var t = Render.reachTest( ctx, patches[ch], rw, rh );
      if ( t === null ) { all = true; break; }
      if ( t !== true ) darks.push( t );
   }
   if ( !all && !darks.length ) return;                  // every channel dark
   var sums = new Float64Array( nc );
   for ( var v = b.v0; v <= b.v1; ++v )
   {
      var dy = cam.y + ( v + 0.5 )*cam.fy - 0.5 - cy;
      for ( var u = b.u0; u <= b.u1; ++u )
      {
         if ( !all )
         {
            var dx = cam.x + ( u + 0.5 )*cam.fx - 0.5 - cx, lit = false;
            for ( var j = 0; j < darks.length && !lit; ++j ) lit = !darks[j]( dx, dy );
            if ( !lit ) continue;
         }
         Render.spritePixels( ctx, u, v, sums );
         for ( ch = 0; ch < nc; ++ch ) if ( sums[ch] != 0 ) accs[ch][v*outW + u] += sums[ch];
      }
   }
};

/* Where a spike sample at offset (dx, dy) reads its patch: along the nearest spike axis, beyond the core, shrunk by c.stretch; across it, as it is. Null when no axis points its way. */
Render.spikeSource = function( c, dx, dy )
{
   var best = null, bestLat = Infinity;
   for ( var i = 0; i < c.axes.length; ++i )
   {
      var a = c.axes[i], along = dx*a[0] + dy*a[1], lat = -dx*a[1] + dy*a[0];
      if ( along > 0 && Math.abs( lat ) < bestLat ) { bestLat = Math.abs( lat ); best = [ a, along, lat ]; }
   }
   if ( !best ) return null;
   var ax = best[0], al = best[1] <= c.rc ? best[1] : c.rc + ( best[1] - c.rc )/c.stretch, lt = best[2];
   return { x: al*ax[0] - lt*ax[1], y: al*ax[1] + lt*ax[0] };
};

/*
 * How far a patch's light reaches from the star's centre (ox, oy), in
 * patch pixels: r, its farthest lit pixel, and with spike axes lat, the
 * farthest any lit pixel lies from the nearest spike ray. -1 when it is all
 * dark. Cached on the patch (a patch is only ever drawn for its own star).
 */
Render.patchReach = function( patch, rw, rh, ox, oy, axes )
{
   var key = axes ? "_reachSpikes" : "_reach";
   if ( patch[key] ) return patch[key];
   var r = -1, lat = -1;
   for ( var y = 0; y < rh; ++y )
      for ( var x = 0; x < rw; ++x )
      {
         if ( patch[y*rw + x] == 0 ) continue;
         var dx = x - ox, dy = y - oy;
         r = Math.max( r, Math.sqrt( dx*dx + dy*dy ) );
         if ( axes ) lat = Math.max( lat, Render.rayDistance( axes, dx, dy ) );
      }
   return ( patch[key] = { r: r, lat: lat } );
};

/* The distance from (dx, dy) to the nearest of the rays from the centre along `axes` ([cos, sin] each). */
Render.rayDistance = function( axes, dx, dy )
{
   var best = Infinity;
   for ( var i = 0; i < axes.length; ++i )
   {
      var al = dx*axes[i][0] + dy*axes[i][1];
      best = Math.min( best, al > 0 ? Math.abs( -dx*axes[i][1] + dy*axes[i][0] ) : Math.sqrt( dx*dx + dy*dy ) );
   }
   return best;
};

/*
 * For a sprite draw (Render.drawSprite's ctx): true when the patch is all
 * dark, else a test of an output pixel -- its centre's offset (dx, dy)
 * from the star, camera pixels -- that is true only when none of its
 * samples can read a lit patch pixel, or null when every pixel must be
 * sampled. Conservative by margins: a sample reads patch pixels within
 * 2 mip pixels of where it lands, and a pixel's samples lie within half
 * its diagonal (h) of its centre.
 */
Render.reachTest = function( c, patch, rw, rh )
{
   var reach = Render.patchReach( patch, rw, rh, c.ox, c.oy, c.axes );
   if ( reach.r < 0 ) return true;
   var h = 0.5*Math.sqrt( c.cam.fx*c.cam.fx + c.cam.fy*c.cam.fy ), near = 2*c.f2 + 1, R = reach.r + near;
   if ( !c.axes )
   {
      // the glow: a sample lands radialSource(ro) (or ro/g) from the centre, which grows with ro
      var s = c.radial ? Math.max( 0.05, c.seen ) : 1;
      var rMax = !c.radial ? R*c.g : ( R <= c.rc ? R : c.rc + ( R - c.rc )*c.g*s );
      return function( dx, dy ) { return Math.sqrt( dx*dx + dy*dy ) - h > rMax; };
   }
   // a spike: a sample on axis a lands at (along', lat), along' = along beyond the core / stretch;
   // across a pixel it moves by up to h times the stretch's inverse along, h across
   var slack = h*( 1 + Math.max( 1, 1/c.stretch ) ), L = reach.lat + near + slack, Rs = R + slack, ax = c.axes;
   return function( dx, dy )
   {
      for ( var i = 0; i < ax.length; ++i )
      {
         var al = dx*ax[i][0] + dy*ax[i][1];
         if ( al <= -h ) continue;                       // no sample of this pixel is read along this axis
         var lt = -dx*ax[i][1] + dy*ax[i][0], a2 = al <= c.rc ? al : c.rc + ( al - c.rc )/c.stretch;
         if ( Math.sqrt( a2*a2 + lt*lt ) <= Rs && Render.rayDistance( ax, a2*ax[i][0] - lt*ax[i][1], a2*ax[i][1] + lt*ax[i][0] ) <= L )
            return false;
      }
      return true;
   };
};

/* The output pixels a sprite (centred at cx, cy, grown by g) can touch. */
Render.spriteBounds = function( sp, cx, cy, g, cam, outW, outH )
{
   var r = sp.rect;
   function toU( q ) { return ( q - cam.x + 0.5 )/cam.fx - 0.5; }
   function toV( q ) { return ( q - cam.y + 0.5 )/cam.fy - 0.5; }
   return { u0: Math.max( 0, Math.floor( toU( cx + ( r.x0 - 1 - sp.det.x )*g ) ) ), u1: Math.min( outW - 1, Math.ceil( toU( cx + ( r.x1 - sp.det.x )*g ) ) ),
            v0: Math.max( 0, Math.floor( toV( cy + ( r.y0 - 1 - sp.det.y )*g ) ) ), v1: Math.min( outH - 1, Math.ceil( toV( cy + ( r.y1 - sp.det.y )*g ) ) ) };
};

/*
 * One output pixel of a sprite, in every channel (into sums): the mean of
 * nx x ny samples over it, each read where Fly.radialSource puts it (the
 * core at its own size, the glow stretched) and weighted from the core's
 * gain k to the glow's kO -- or, for a spike, along its axis
 * (Render.spikeSource).
 */
Render.spritePixels = function( c, u, v, sums )
{
   var cam = c.cam, nc = c.mps.length, ch, mp;
   for ( ch = 0; ch < nc; ++ch ) sums[ch] = 0;
   for ( var sy = 0; sy < c.ny; ++sy )
   {
      var dy = cam.y + ( v + ( sy + 0.5 )/c.ny )*cam.fy - 0.5 - c.cy;
      for ( var sx = 0; sx < c.nx; ++sx )
      {
         var dx = cam.x + ( u + ( sx + 0.5 )/c.nx )*cam.fx - 0.5 - c.cx, f = 1/c.g, sm = -1;
         if ( c.axes )
         {
            var at = Render.spikeSource( c, dx, dy );
            if ( !at ) continue;
            for ( ch = 0; ch < nc; ++ch )
            {
               mp = c.mps[ch];
               sums[ch] += c.ks[ch]*Render.patchSample( mp.d, mp.w, mp.h, ( c.ox + at.x + 0.5 )/c.f2 - 0.5, ( c.oy + at.y + 0.5 )/c.f2 - 0.5 );
            }
            continue;
         }
         if ( c.radial )
         {
            var ro = Math.sqrt( dx*dx + dy*dy );
            f = ro > 0 ? Fly.radialSource( ro, c.rc, c.g, c.seen )/ro : 1;
            sm = Fly.smoothstep( 0, c.rc, ro );   // core to glow smoothly: no edge at rc
         }
         var px = ( c.ox + dx*f + 0.5 )/c.f2 - 0.5, py = ( c.oy + dy*f + 0.5 )/c.f2 - 0.5;
         for ( ch = 0; ch < nc; ++ch )
         {
            mp = c.mps[ch];
            var w = sm < 0 ? c.ks[ch] : c.ks[ch] + ( c.kOs[ch] - c.ks[ch] )*sm;
            sums[ch] += w*Render.patchSample( mp.d, mp.w, mp.h, px, py );
         }
      }
   }
   for ( ch = 0; ch < nc; ++ch ) sums[ch] /= c.nx*c.ny;
};

/* ---------------------------------------------------------------------------
 * Scene and frame.
 * ------------------------------------------------------------------------ */

Render.channels = function( image, mask )
{
   var n = image.width*image.height, out = [];
   for ( var c = 0; c < image.numberOfChannels; ++c )
   {
      var b = new Float32Array( n );
      image.getSamples( b, new Rect( 0, 0, image.width, image.height ), c );
      if ( mask )
         for ( var i = 0; i < n; ++i ) if ( !mask[i] ) b[i] = 0;
      out.push( b );
   }
   return out;
};

/*
 * Everything a frame needs, read out of PixInsight once: the starless S
 * and the residual R (stars layer outside every sprite) per channel, the
 * sprites, and each sprite's catalogue position at frame 0.
 */
Render.scene = function( a )
{
   var tp = a.project( a.target.ra, a.target.dec );
   return { w: a.starless.width, h: a.starless.height, nc: a.starless.numberOfChannels,
            S: Render.channels( a.starless ), R: a.residual || Render.channels( a.stars, a.residualMask ),
            sprites: a.sprites.map( function( s ) { return { s: s, p0: a.project( s.source.ra, s.source.dec ) }; } ),
            project: a.project, target: a.target, tp: tp, D: a.D, catalogue: a.catalogue || null };
};

/*
 * A logo for a W x H frame at `place` (Fly.logoRect): its colour
 * premultiplied by its alpha (opaque when it has none) times `opacity`, area-averaged to
 * its size -- a mono logo is grey in every channel. Null when off.
 */
Render.logoLayer = function( img, W, H, place, channels, opacity )
{
   var op = ( opacity != null ) ? Math.max( 0, Math.min( 1, opacity ) ) : 1;
   var nominal = img.numberOfNominalChannels, r = Fly.logoRect( W, H, img.width, img.height, place );
   if ( !r ) return null;
   var x = Math.round( r.x ), y = Math.round( r.y ), n = img.width*img.height, rect = new Rect( 0, 0, img.width, img.height );
   var alpha = new Float32Array( n );
   if ( img.numberOfChannels > nominal ) img.getSamples( alpha, rect, nominal ); else alpha.fill( 1 );
   for ( var q = 0; q < n; ++q ) alpha[q] *= op;          // the logo's opacity scales its own transparency
   var p = [];
   for ( var c = 0; c < ( channels || 3 ); ++c )
   {
      var b = new Float32Array( n );
      img.getSamples( b, rect, Math.min( c, nominal - 1 ) );
      for ( var i = 0; i < n; ++i ) b[i] *= alpha[i];
      p.push( Render.fitPlane( b, img.width, img.height, r.w, r.h ) );
   }
   return { x: x, y: y, w: r.w, h: r.h, p: p, a: Render.fitPlane( alpha, img.width, img.height, r.w, r.h ) };
};

/* A plane resized to w x h: each output pixel the area average of what it covers (bilinear when enlarging). */
Render.fitPlane = function( src, sw, sh, w, h )
{
   var out = new Float32Array( w*h ), sx = sw/w, sy = sh/h;
   for ( var v = 0; v < h; ++v )
      for ( var u = 0; u < w; ++u )
      {
         out[v*w + u] = ( sx <= 1 && sy <= 1 ) ? Render.patchSample( src, sw, sh, ( u + 0.5 )*sx - 0.5, ( v + 0.5 )*sy - 0.5 )
                                               : Render.areaMean( src, sw, sh, u*sx, v*sy, sx, sy );
      }
   return out;
};

/* The mean of a plane over the rectangle x0..x0 + sx, y0..y0 + sy, each pixel weighted by how much of it is covered. */
Render.areaMean = function( src, sw, sh, x0, y0, sx, sy )
{
   var x1 = x0 + sx, y1 = y0 + sy, sum = 0, area = 0;
   for ( var yy = Math.floor( y0 ); yy < Math.min( sh, Math.ceil( y1 ) ); ++yy )
   {
      var fy = Math.min( y1, yy + 1 ) - Math.max( y0, yy );
      for ( var xx = Math.floor( x0 ); xx < Math.min( sw, Math.ceil( x1 ) ); ++xx )
      {
         var f = fy*( Math.min( x1, xx + 1 ) - Math.max( x0, xx ) );
         sum += f*src[yy*sw + xx]; area += f;
      }
   }
   return area > 0 ? sum/area : 0;
};

/* The logo over a frame's composite (premultiplied: logo + frame x (1 - alpha)), faded by `fade`; HDR star light under it is covered too. */
Render.addLogo = function( L, out, excess, outW, fade )
{
   var f = ( fade != null ) ? fade : 1;
   if ( !( f > 0 ) ) return;
   for ( var v = 0; v < L.h; ++v )
      for ( var u = 0; u < L.w; ++u )
      {
         var j = v*L.w + u, a = f*L.a[j], i = ( L.y + v )*outW + L.x + u;
         if ( a <= 0 ) continue;
         for ( var c = 0; c < out.length; ++c )
         {
            out[c][i] = f*L.p[Math.min( c, L.p.length - 1 )][j] + out[c][i]*( 1 - a );
            if ( excess[c] ) excess[c][i] *= 1 - a;
         }
      }
};

/* A plane blurred in place: three box passes a side, ~Gaussian of the given box radius; edges hold their value. */
Render.boxBlur = function( a, w, h, r )
{
   if ( !( r >= 1 ) ) return a;
   // written out rather than through per-pixel get/set closures: the same sums, several times faster
   var n = Math.max( w, h ), tmp = new Float32Array( n ), line = new Float32Array( n ), span = 2*r + 1;
   function pass( len, base, step )
   {
      var i, k, s = 0, last = len - 1;
      for ( i = 0; i < len; ++i ) line[i] = a[base + i*step];
      for ( k = -r; k <= r; ++k ) s += line[k < 0 ? 0 : ( k > last ? last : k )];
      for ( i = 0; i < len; ++i )
      {
         tmp[i] = s/span;
         var hi = i + r + 1, lo = i - r;
         s += line[hi > last ? last : hi] - line[lo < 0 ? 0 : lo];
      }
      for ( i = 0; i < len; ++i ) a[base + i*step] = tmp[i];
   }
   for ( var rep = 0; rep < 3; ++rep )
   {
      for ( var y = 0; y < h; ++y ) pass( w, y*w, 1 );
      for ( var x = 0; x < w; ++x ) pass( h, x, w );
   }
   return a;
};

/*
 * Render.boxBlur's three passes as one kernel -- a box of 2r + 1 convolved
 * with itself three times -- run by PixInsight's own separable convolution
 * (C++, every core): the same result away from the frame's edges, several
 * times faster. Near an edge PixInsight treats the outside its own way,
 * where boxBlur repeated the edge pixel on each pass. Falls back to
 * boxBlur outside PixInsight (the Node suite).
 */
Render.blur3 = function( a, w, h, r )
{
   if ( !( r >= 1 ) ) return a;
   if ( typeof Image == "undefined" || typeof Vector == "undefined" ) return Render.boxBlur( a, w, h, r );
   var img = new Image( w, h, 1, ColorSpace_Gray, 32, SampleType_Real ), rect = new Rect( 0, 0, w, h );
   try
   {
      img.setSamples( a, rect, 0 );
      Render.blurImage( img, r );
      img.getSamples( a, rect, 0 );
   }
   finally { img.free(); }
   return a;
};

/* Render.blur3 on a PixInsight image, in place; returns it. */
Render.blurImage = function( img, r )
{
   if ( !( r >= 1 ) ) return img;
   var k = Render.box3Kernel( r );
   img.convolveSeparable( k, k );
   return img;
};

/* A box of 2r + 1 convolved with itself three times, as a Vector; cached by r. */
Render.box3Kernel = function( r )
{
   var cache = Render.box3Cache || ( Render.box3Cache = {} );
   if ( cache[r] ) return cache[r];
   var box = [], c = [ 1 ], i, j, k;
   for ( k = 0; k < 2*r + 1; ++k ) box.push( 1/( 2*r + 1 ) );
   for ( var pass = 0; pass < 3; ++pass )
   {
      var o = []; for ( k = 0; k < c.length + box.length - 1; ++k ) o.push( 0 );
      for ( i = 0; i < c.length; ++i ) for ( j = 0; j < box.length; ++j ) o[i + j] += c[i]*box[j];
      c = o;
   }
   var v = new Vector( c.length );
   for ( k = 0; k < c.length; ++k ) v.at( k, c[k] );
   return ( cache[r] = v );
};

Render.BLOOM_TIGHT = 0.005;    // the round-core glow's sigma, of the frame's short side (0.0035 left a hard edge)
Render.BLOOM_WIDE = 0.02;      // the wide glow's
Render.BLOOM_GLARE = 0.06;     // the very wide, faint glare's
Render.BLOOM_GAINS = [ 0.6, 0.25, 0.35, 0.1 ];   // tight, wide, the whitening share, glare

/* The stars layer's colour scaled by `sat` about each pixel's mean (its brightness kept); 1 changes nothing. */
Render.saturateStars = function( T, n, sat )
{
   if ( sat == 1 || T.length < 3 ) return;
   for ( var i = 0; i < n; ++i )
   {
      var m = ( T[0][i] + T[1][i] + T[2][i] )/3;
      for ( var c = 0; c < 3; ++c ) T[c][i] = m + ( T[c][i] - m )*sat;
   }
};

/*
 * The stars layer into the HDR headroom: every channel multiplied by the
 * factor map (Render.headroomMap: a smooth bump on each bright star, 1
 * elsewhere), so each star's hue is kept; `factor` may be one number. In
 * PixInsight's own image operations (w, the frame's width, lets them split
 * the work by rows); Render.starsToHeadroomJs outside PixInsight.
 */
Render.starsToHeadroom = function( T, n, factor, w )
{
   if ( typeof Image == "undefined" || typeof ImageOp_Mul == "undefined" ) return Render.starsToHeadroomJs( T, n, factor );
   var cw = ( w > 0 && n % w == 0 ) ? w : n, rect = new Rect( 0, 0, cw, n/cw ), made = [], c;
   function image() { var i = new Image( cw, n/cw, 1, ColorSpace_Gray, 32, SampleType_Real ); made.push( i ); return i; }
   try
   {
      var f = null;
      if ( typeof factor != "number" ) { f = image(); f.setSamples( factor, rect, 0 ); }
      for ( c = 0; c < T.length; ++c )
      {
         var L = image();
         L.setSamples( T[c], rect, 0 );
         L.apply( f || factor, ImageOp_Mul );
         L.getSamples( T[c], rect, 0 );
      }
   }
   finally { made.forEach( function( i ) { i.free(); } ); }
};

/* Render.starsToHeadroom as a per-pixel loop: the reference, and outside PixInsight. */
Render.starsToHeadroomJs = function( T, n, factor )
{
   var map = ( typeof factor == "number" ) ? null : factor;
   for ( var i = 0; i < n; ++i )
   {
      var f = map ? map[i] : factor;
      if ( f == 1 ) continue;
      for ( var c = 0; c < T.length; ++c ) T[c][i] *= f;
   }
};

/* The box radius whose three passes blur like a Gaussian of sigma s (sigma^2 = r(r + 1)). */
Render.boxRadius = function( s )
{
   return Math.max( 1, Math.round( Math.sqrt( s*s + 0.25 ) - 0.5 ) );
};

/* The two glows' sigmas (px) for a frame whose short side is `short`, `seconds` in: the wide one breathes by 5%. */
Render.bloomSigmas = function( short, seconds )
{
   var breathe = 1 + 0.05*Math.sin( 2*Math.PI*0.3*( seconds || 0 ) );
   return [ Render.BLOOM_TIGHT*short, Render.BLOOM_WIDE*short*breathe, Render.BLOOM_GLARE*short*breathe ];
};

/* Past white a core whitens: its channels are drawn toward its brightest, as a sensor saturates (from 1.5x, so the rim keeps its colour). */
Render.whitenPastWhite = function( T, n )
{
   for ( var i = 0; i < n; ++i )
   {
      var top = 0, c;
      for ( c = 0; c < T.length; ++c ) top = Math.max( top, T[c][i] );
      if ( top <= 1 ) continue;
      var f = 0.8*Fly.smoothstep( 1.5, 4, top );
      for ( c = 0; c < T.length; ++c ) T[c][i] += ( top - T[c][i] )*f;
   }
};

/* Each channel's light past white (T - 1 where T > 1), or null when there is none. */
Render.pastWhite = function( T, n )
{
   var any = false, E = T.map( function( t )
   {
      var e = new Float32Array( n );
      for ( var k = 0; k < n; ++k ) if ( t[k] > 1 ) { e[k] = t[k] - 1; any = true; }
      return e;
   } );
   return any ? E : null;
};

/*
 * Bloom over the stars layer T (per channel, w x h): light past white (T >
 * 1) whitens (its channels drawn toward its brightest) and spreads into a
 * tight glow that rounds a clipped core, a wide faint one and a very wide
 * glare, a share of it white in every channel, as a sensor saturates.
 * Nothing past white -- the image itself -- blooms nothing. opts = {
 * amount (0 = off), seconds }. In PixInsight's own image operations (C++,
 * every core); Render.bloomJs, the per-pixel version, outside PixInsight.
 */
Render.bloom = function( T, w, h, opts )
{
   var amount = opts.amount != null ? opts.amount : 1;
   if ( !( amount > 0 ) ) return;
   if ( typeof Image == "undefined" || typeof ImageOp_Max == "undefined" ) return Render.bloomJs( T, w, h, opts );
   var rect = new Rect( 0, 0, w, h ), made = [], nc = T.length, c;
   function image( from ) { var i = from ? new Image( from ) : new Image( w, h, 1, ColorSpace_Gray, 32, SampleType_Real ); made.push( i ); return i; }
   try
   {
      var L = T.map( function( t ) { var i = image(); i.setSamples( t, rect, 0 ); return i; } );
      var top = image( L[0] );
      for ( c = 1; c < nc; ++c ) top.apply( L[c], ImageOp_Max );
      if ( !( top.maximum() > 1 ) ) return;                  // nothing past white: the image itself
      if ( nc > 1 )
      {
         // past white a core whitens (Render.whitenPastWhite): T += (top - T) x 0.8 smoothstep(1.5, 4, top)
         var t = image( top ); t.apply( 1.5, ImageOp_Sub ); t.apply( 2.5, ImageOp_Div ); t.truncate( 0, 1 );
         var f = image( t ); f.apply( t, ImageOp_Mul );
         var u = image( t ); u.apply( -2, ImageOp_Mul ); u.apply( 3, ImageOp_Add );
         f.apply( u, ImageOp_Mul ); f.apply( 0.8, ImageOp_Mul );
         for ( c = 0; c < nc; ++c ) { var d = image( top ); d.apply( L[c], ImageOp_Sub ); d.apply( f, ImageOp_Mul ); L[c].apply( d, ImageOp_Add ); }
      }
      // each channel's light past white, and its mean
      var E = L.map( function( l ) { var e = image( l ); e.apply( 1, ImageOp_Sub ); e.truncate( 0, Render.EXCESS_MAX ); return e; } );
      var white = image( E[0] );
      for ( c = 1; c < nc; ++c ) white.apply( E[c], ImageOp_Add );
      white.apply( nc, ImageOp_Div );
      var sg = Render.bloomSigmas( Math.min( w, h ), opts.seconds ), G = Render.BLOOM_GAINS;
      var base = Render.blurImage( image( white ), Render.boxRadius( sg[0] ) );
      base.apply( G[2], ImageOp_Mul );
      var glare = Render.blurImage( white, Render.boxRadius( sg[2] ) );
      glare.apply( G[3], ImageOp_Mul );
      base.apply( glare, ImageOp_Add );
      for ( c = 0; c < nc; ++c )
      {
         var glow = Render.blurImage( image( E[c] ), Render.boxRadius( sg[0] ) );
         glow.apply( G[0], ImageOp_Mul );
         var wide = Render.blurImage( E[c], Render.boxRadius( sg[1] ) );
         wide.apply( G[1], ImageOp_Mul );
         glow.apply( wide, ImageOp_Add );
         glow.apply( base, ImageOp_Add );
         glow.apply( amount, ImageOp_Mul );
         L[c].apply( glow, ImageOp_Add );
         L[c].getSamples( T[c], rect, 0 );
      }
   }
   finally { made.forEach( function( i ) { i.free(); } ); }
};

/* Render.bloom as per-pixel loops: the reference, and outside PixInsight. */
Render.bloomJs = function( T, w, h, opts )
{
   var amount = opts.amount != null ? opts.amount : 1;
   if ( !( amount > 0 ) ) return;
   var n = w*h, c, i;
   if ( T.length > 1 ) Render.whitenPastWhite( T, n );
   var E = Render.pastWhite( T, n );
   if ( !E ) return;
   var sg = Render.bloomSigmas( Math.min( w, h ), opts.seconds ), G = Render.BLOOM_GAINS, white = new Float32Array( n );
   for ( c = 0; c < E.length; ++c ) for ( i = 0; i < n; ++i ) white[i] += E[c][i]/E.length;
   var tightW = Render.boxBlur( white.slice(), w, h, Render.boxRadius( sg[0] ) ), glare = Render.boxBlur( white, w, h, Render.boxRadius( sg[2] ) );
   for ( c = 0; c < T.length; ++c )
   {
      var tight = Render.boxBlur( E[c].slice(), w, h, Render.boxRadius( sg[0] ) ), wide = Render.boxBlur( E[c], w, h, Render.boxRadius( sg[1] ) );
      for ( i = 0; i < n; ++i ) T[c][i] += amount*( G[0]*tight[i] + G[1]*wide[i] + G[2]*tightW[i] + G[3]*glare[i] );
   }
};

/*
 * A scene as plain data plus Float32Arrays, for the per-image cache: every
 * typed array (starless, residual, each sprite's pixels, model and mask)
 * goes into `arrays` and is replaced in `meta` by its index; the
 * projection is left out (it is rebuilt from the solved working copy).
 */
Render.packScene = function( sc )
{
   var arrays = [];
   var put = function( a ) { arrays.push( a instanceof Float32Array ? a : Float32Array.from( a ) ); return arrays.length - 1; };
   var putAll = function( list ) { return list ? list.map( put ) : null; };
   var sprites = sc.sprites.map( function( e )
   {
      var s = e.s, meta = {};
      Object.keys( s ).forEach( function( k ) { if ( [ "pixels", "modelCore", "modelSpikes", "mask", "_split", "_glow" ].indexOf( k ) < 0 ) meta[k] = s[k]; } );
      return { s: meta, p0: e.p0, pixels: putAll( s.pixels ), modelCore: putAll( s.modelCore ), modelSpikes: putAll( s.modelSpikes ), mask: s.mask ? put( s.mask ) : null };
   } );
   return { meta: { w: sc.w, h: sc.h, nc: sc.nc, target: sc.target, tp: sc.tp, D: sc.D, S: putAll( sc.S ), R: putAll( sc.R ), sprites: sprites,
                    catalogue: sc.catalogue || null }, arrays: arrays };
};

/* A packed scene (Render.packScene) back, with its projection. */
Render.unpackScene = function( meta, arrays, project )
{
   var get = function( i ) { return arrays[i]; }, getAll = function( list ) { return list ? list.map( get ) : undefined; };
   var sprites = meta.sprites.map( function( e )
   {
      var s = Object.assign( {}, e.s, { pixels: getAll( e.pixels ) } );
      if ( e.modelCore ) s.modelCore = getAll( e.modelCore );
      if ( e.modelSpikes ) s.modelSpikes = getAll( e.modelSpikes );
      if ( e.mask != null ) s.mask = Uint8Array.from( arrays[e.mask] );
      return { s: s, p0: e.p0 };
   } );
   return { w: meta.w, h: meta.h, nc: meta.nc, target: meta.target, tp: meta.tp, D: meta.D, S: getAll( meta.S ), R: getAll( meta.R ),
            sprites: sprites, project: project, catalogue: meta.catalogue || null };
};

/* a = alpha*a + (1 - alpha)*b, per channel (same size); a crossfade loop's frames. */
Render.blend = function( a, b, alpha )
{
   var n = a.width*a.height, pa = new Float32Array( n ), pb = new Float32Array( n );
   for ( var c = 0; c < a.numberOfChannels; ++c )
   {
      a.getSamples( pa, new Rect( 0, 0, a.width, a.height ), c );
      b.getSamples( pb, new Rect( 0, 0, b.width, b.height ), c );
      for ( var i = 0; i < n; ++i ) pa[i] = alpha*pa[i] + ( 1 - alpha )*pb[i];
      a.setSamples( pa, new Rect( 0, 0, a.width, a.height ), c );
   }
   return a;
};

/*
 * Frame at time t in [0,1], outW x outH, of the camera crop `crop`
 * ({x, y, w, h} in image pixels). opts = { travel, easing, growth,
 * brightening, kernel }. Sums the residual (scaled with the backdrop) and
 * every moved sprite in the unscreened star layer, then screens the sum
 * onto the scaled starless image once.
 */
Render.frame = function( sc, t, opts, outW, outH, crop )
{
   var s = opts.travel*Fly.ease( t, opts.easing );
   var K = Fly.backdropZoom( sc.D, s, opts.backdropMotion != null ? opts.backdropMotion : Fly.BACKDROP_MOTION_DEFAULT );
   var kernel = opts.kernel || "bicubic";
   var ax = Render.axisWeights( outW, crop.x, crop.w, sc.tp.x, K, sc.w, kernel );
   var ay = Render.axisWeights( outH, crop.y, crop.h, sc.tp.y, K, sc.h, kernel );
   var cam = { x: crop.x, y: crop.y, fx: crop.w/outW, fy: crop.h/outH };
   var S = [], T = [], c;
   for ( c = 0; c < sc.nc; ++c )
   {
      S.push( Render.resample( sc.S[c], sc.w, sc.h, ax, ay ) );
      T.push( Render.resample( sc.R[c], sc.w, sc.h, ax, ay ) );
   }
   var placed = Render.addSprites( sc, T, s, Object.assign( {}, opts, { t: t, K: K } ), outW, outH, cam );
   Render.saturateStars( T, outW*outH, opts.starSaturation != null ? opts.starSaturation : 1 );
   // light past white blooms (none in the image itself, so frame 0 is untouched)
   Render.bloom( T, outW, outH, { amount: opts.bloom != null ? opts.bloom : 0, seconds: t*( opts.duration || 0 ) } );
   // in HDR, bright stars reach into the headroom by their magnitude; the backdrop keeps its tone
   var hdrOut = opts.output && ( opts.output.mode == "pq" || opts.output.mode == "hlg" );
   // the map with the frame's backdrop zoom, so a catalogue star's bump follows the star the backdrop carries
   if ( hdrOut && opts.starHdr ) Render.starsToHeadroom( T, outW*outH, Render.headroomMap( sc, placed, Object.assign( {}, opts, { K: K } ), outW, outH, cam ), outW );
   var img = new Image( outW, outH, sc.nc, sc.nc >= 3 ? ColorSpace_RGB : ColorSpace_Gray, 32, SampleType_Real );
   var n = outW*outH, out = [], excess = [];
   var hdr = opts.output && ( opts.output.mode == "pq" || opts.output.mode == "hlg" );
   for ( c = 0; c < sc.nc; ++c )
   {
      var r = Render.composite( S[c], T[c], n, hdr, outW );
      out.push( r.out );
      excess.push( r.excess );
   }
   if ( opts.logo ) Render.addLogo( opts.logo, out, excess, outW, Fly.logoFade( t*( opts.duration || 0 ), opts.logoDelay || 0 ) );
   // the composite is in the image's own encoding; the output transform
   // converts it to the video's colour space (Fly.outputTransform)
   if ( opts.output )
   {
      if ( sc.nc >= 3 ) opts.output.apply( out[0], out[1], out[2], n, excess[0], excess[1], excess[2] );
      else opts.output.applyGray( out[0], n, excess[0] );
   }
   for ( c = 0; c < sc.nc; ++c )
      img.setSamples( out[c], new Rect( 0, 0, outW, outH ), c );
   return img;
};

/*
 * One channel's screen composite, 1 - (1 - S)(1 - T), and for HDR the star
 * light above 1 that SDR would clip -- in PixInsight's own image operations
 * (clamp, screen, subtract; C++, every core, which it splits by rows: the
 * channel is held w wide, or as one row when w is not given). Render.compositeJs
 * outside PixInsight (the Node suite).
 */
Render.composite = function( Sc, Tc, n, hdr, w )
{
   if ( typeof Image == "undefined" || typeof ImageOp_Screen == "undefined" ) return Render.compositeJs( Sc, Tc, n, hdr );
   var cw = ( w > 0 && n % w == 0 ) ? w : n, ch = n/cw;
   var rect = new Rect( 0, 0, cw, ch ), a = new Image( cw, ch, 1, ColorSpace_Gray, 32, SampleType_Real ), b = new Image( cw, ch, 1, ColorSpace_Gray, 32, SampleType_Real );
   var o = new Float32Array( n ), X = null;
   try
   {
      a.setSamples( Sc, rect, 0 );
      b.setSamples( Tc, rect, 0 );
      if ( hdr )
      {
         // the light past white: T - 1 where T > 1
         var x = new Image( b );
         try { x.apply( 1, ImageOp_Sub ); x.truncate( 0, Render.EXCESS_MAX ); X = new Float32Array( n ); x.getSamples( X, rect, 0 ); }
         finally { x.free(); }
      }
      a.truncate( 0, 1 );
      b.truncate( 0, 1 );
      a.apply( b, ImageOp_Screen );
      a.getSamples( o, rect, 0 );
   }
   finally { a.free(); b.free(); }
   return { out: o, excess: X };
};

Render.EXCESS_MAX = 1e30;   // no upper clamp on the light past white

/* Render.composite as a per-pixel loop: the reference, and outside PixInsight. */
Render.compositeJs = function( Sc, Tc, n, hdr )
{
   var o = new Float32Array( n ), X = hdr ? new Float32Array( n ) : null;
   for ( var i = 0; i < n; ++i )
   {
      var a = Math.min( 1, Math.max( 0, Sc[i] ) ), b = Math.min( 1, Math.max( 0, Tc[i] ) );
      o[i] = 1 - ( 1 - a )*( 1 - b );
      if ( X && Tc[i] > 1 ) X[i] = Tc[i] - 1;
   }
   return { out: o, excess: X };
};

/*
 * Every sprite in front of the camera, at its exact moved position: the
 * detected centre plus the shift of its projection, so frame 0 draws each
 * sprite on its own pixels whatever the catalogue-vs-centroid offset.
 */
Render.addSprites = function( sc, T, s, opts, outW, outH, cam )
{
   var placed = [];
   sc.sprites.forEach( function( e, j ) { var q = Render.placeSprite( sc, e, j, s, opts ); if ( q ) placed.push( q ); } );
   // a star partly out of the frame or behind a nearer one casts glow and spikes by what is seen of it;
   // eased in over the clip's first tenth, so frame 0 stays the image
   Render.seenShares( placed, outW, outH, cam, Fly.smoothstep( 0, 0.1, opts.t || 0 ) );
   placed.forEach( function( q ) { Render.drawPlaced( sc, T, q, s, opts, outW, outH, cam ); } );
   return placed;
};

/*
 * Each output pixel's HDR peak (Fly.magnitudeGain): every catalogued star
 * in the frame -- moving where it is drawn, still where the backdrop
 * carries it -- paints its magnitude's gain around it; elsewhere 1. The
 * peak is the chosen one over SDR white (HDR_REFERENCE_WHITE).
 */
Render.headroomMap = function( sc, placed, opts, outW, outH, cam )
{
   var stars = sc.catalogue || sc.sprites.map( function( e ) { return { x: e.p0 ? e.p0.x : 0, y: e.p0 ? e.p0.y : 0, G: e.s.source && e.s.source.G }; } );
   if ( sc.gBright == null ) sc.gBright = stars.reduce( function( m, s ) { return ( s.G < m ) ? s.G : m; }, Infinity );
   var map = new Float32Array( outW*outH ).fill( 1 ), peak = ( opts.peak || Fly.HDR_PEAK_DEFAULT )/Fly.HDR_REFERENCE_WHITE, K = opts.K || 1;
   // a star's bump (Fly.headroomBump): sigma HEADROOM_SIGMA of its reach r, painted out to 3 sigma, the larger where two meet
   function paint( x, y, r, gain )
   {
      var u = ( x - cam.x + 0.5 )/cam.fx - 0.5, v = ( y - cam.y + 0.5 )/cam.fy - 0.5, sigma = Math.max( 0.5, Fly.HEADROOM_SIGMA*r/cam.fx ), ro = 3*sigma;
      for ( var yy = Math.max( 0, Math.floor( v - ro ) ); yy <= Math.min( outH - 1, Math.ceil( v + ro ) ); ++yy )
         for ( var xx = Math.max( 0, Math.floor( u - ro ) ); xx <= Math.min( outW - 1, Math.ceil( u + ro ) ); ++xx )
         {
            var f = Fly.headroomBump( Math.sqrt( ( xx - u )*( xx - u ) + ( yy - v )*( yy - v ) ), sigma, gain ), i = yy*outW + xx;
            if ( f > map[i] ) map[i] = f;
         }
   }
   // a star's reach grows with its brightness; only pixels above the knee are changed, so it can be generous
   var reach = function( gain ) { return Fly.HEADROOM_RADIUS*( 1 + 3*( gain - 1 )/Math.max( 1e-6, peak - 1 ) ); };
   var moving = {};
   placed.forEach( function( q )
   {
      var s = q.sp.source || {}, gain = isFinite( s.G ) ? Fly.magnitudeGain( s.G, sc.gBright, peak ) : 1;
      moving[Render.starKey( s )] = true;
      if ( gain > 1 ) paint( q.cx, q.cy, Math.max( reach( gain ), q.rc*Math.max( 1, q.g ) + 2 ), gain );
   } );
   // every other catalogued star, where the zooming backdrop carries it
   stars.forEach( function( st )
   {
      if ( !isFinite( st.G ) || moving[Render.starKey( st )] ) return;
      var gain = Fly.magnitudeGain( st.G, sc.gBright, peak );
      if ( gain > 1 ) paint( sc.tp.x + ( st.x - sc.tp.x )*K, sc.tp.y + ( st.y - sc.tp.y )*K, reach( gain )*K, gain );
   } );
   return map;
};

/* A catalogue star's identity across the scene's copies (its position on the sky). */
Render.starKey = function( s ) { return ( s.ra != null ) ? s.ra.toFixed( 6 ) + "," + s.dec.toFixed( 6 ) : ""; };

/*
 * Where and how a sprite is drawn this frame, or null when it is not: its
 * centre (never spreading slower than the backdrop, Fly.screenPosition),
 * growth, core and glow gains, and fade.
 */
Render.placeSprite = function( sc, e, j, s, opts )
{
   var sp = e.s, m = Fly.moved( sp.source.ra, sp.source.dec, sp.d, sc.target, s );
   var alpha = Fly.opacity( m.ratio, m.front );
   if ( alpha <= 0 || e.p0 == null )
      return null;
   var p = sc.project( m.ra, m.dec );
   if ( p == null )
      return null;
   // the glow grows and spreads its light (Fly.pixelScale); the core keeps the light a
   // magnified core had, at its own size -- sharper and brighter -- and fades into the glow
   var K = opts.K || 1, g = Math.max( K, Fly.growth( m.ratio, opts.growth ) );
   var kOuter = ( opts.brightening ? m.ratio*m.ratio : 1 )/( g*g )*alpha;
   var at = Fly.screenPosition( { x: sp.det.x + p.x - e.p0.x, y: sp.det.y + p.y - e.p0.y }, sp.det, sc.tp, K );
   return { e: e, j: j, sp: sp, m: m, g: g, kOuter: kOuter, kCore: kOuter*g*g, rc: Render.coreRadius( sp ), cx: at.x, cy: at.y, seen: 1,
            alpha: alpha, spikeLength: Fly.spikeLength( m.ratio, opts.brightening, g ) };
};

/* What is seen of each placed star's core: inside the frame, not behind a nearer star; `ease` 0 leaves all seen. */
Render.seenShares = function( placed, outW, outH, cam, ease )
{
   if ( !( ease > 0 ) ) return;
   placed.forEach( function( q )
   {
      var u = ( q.cx - cam.x + 0.5 )/cam.fx - 0.5, v = ( q.cy - cam.y + 0.5 )/cam.fy - 0.5, r = q.rc/cam.fx;
      q.u = u; q.v = v; q.r = r;
      q.frameSeen = Fly.discVisible( u, v, r, outW, outH );
   } );
   placed.forEach( function( q )
   {
      var cover = 0;
      placed.forEach( function( o )
      {
         if ( o === q || !( o.m.ratio > q.m.ratio ) ) return;
         var d = Math.hypot( o.u - q.u, o.v - q.v );
         if ( d < o.r + q.r ) cover = Math.max( cover, Fly.coverFraction( q.r, o.r, d ) );
      } );
      q.seen = 1 - ease*( 1 - q.frameSeen*( 1 - cover ) );
   } );
};

/*
 * A sprite's photograph and round model cut at its core (rc, a soft 3 px
 * edge), cached on the sprite: the core parts (in the core's own box,
 * coreSprite) and the model beyond the core. The core and the rest turn to
 * the model at different distances.
 */
Render.splitPatches = function( sp, c, rc, photo )
{
   var cache = sp._split || ( sp._split = [] );
   if ( cache[c] ) return cache[c];
   var r = sp.rect, rw = r.x1 - r.x0, rh = r.y1 - r.y0, P = photo || sp.pixels[c], M = sp.modelCore[c];
   // the core parts are cut to the core's own box (rc + 3 px), so drawing them costs little
   var R = Math.ceil( rc ) + 4, cr = { x0: Math.max( r.x0, Math.floor( sp.det.x ) - R ), y0: Math.max( r.y0, Math.floor( sp.det.y ) - R ),
                                          x1: Math.min( r.x1, Math.floor( sp.det.x ) + R + 1 ), y1: Math.min( r.y1, Math.floor( sp.det.y ) + R + 1 ) };
   var cw = cr.x1 - cr.x0, ch = cr.y1 - cr.y0;
   var out = { photoCore: new Float32Array( cw*ch ), modelCore: new Float32Array( cw*ch ), modelOuter: new Float32Array( rw*rh ),
               coreSprite: { rect: cr, det: sp.det } };
   var coreShare = function( x, y ) { return 1 - Fly.smoothstep( rc, rc + 3, Math.hypot( x - sp.det.x, y - sp.det.y ) ); };
   for ( var y = 0; y < rh; ++y )
      for ( var x = 0; x < rw; ++x )
         out.modelOuter[y*rw + x] = ( 1 - coreShare( r.x0 + x, r.y0 + y ) )*M[y*rw + x];
   for ( y = cr.y0; y < cr.y1; ++y )
      for ( x = cr.x0; x < cr.x1; ++x )
      {
         var k = coreShare( x, y ), i = ( y - r.y0 )*rw + x - r.x0, o = ( y - cr.y0 )*cw + x - cr.x0;
         out.photoCore[o] = k*P[i]; out.modelCore[o] = k*M[i];
      }
   return ( cache[c] = out );
};

/* A sprite's photograph minus its spikes (its glow), cached on the sprite. */
Render.glowPatch = function( sp, c )
{
   var cache = sp._glow || ( sp._glow = [] );
   if ( !cache[c] )
   {
      var P = sp.pixels[c], S = sp.modelSpikes[c], g = new Float32Array( P.length );
      for ( var i = 0; i < P.length; ++i ) g[i] = P[i] - S[i];
      cache[c] = g;
   }
   return cache[c];
};

/* One placed star, along its shutter path, in each channel: its photograph turning to its model up close, twinkling. */
Render.drawPlaced = function( sc, T, q, s, opts, outW, outH, cam )
{
   var sp = q.sp, path = Render.shutterPath( sc, sp, q.e, s, opts, cam, q.cx, q.cy ), seconds = ( opts.t || 0 )*( opts.duration || 0 );
   // up close the star turns from its photograph (noise, processing marks) to its smooth model
   // -- its core sooner (Fly.coreModelWeight): a saturated core is often a flat square in the photograph,
   // and boosted the square is what one sees; its spikes take only the glow's boost
   var wo = sp.modelCore ? Fly.modelWeight( q.m.ratio ) : 0, wc = sp.modelCore ? Fly.coreModelWeight( q.m.ratio ) : 0;
   // the same sum, cheaply: the photograph once at full size; a core-sized correction swapping its
   // core for the model's; the model's glow and spikes at full size only for stars really near
   // its spikes are drawn on their own, from the model, growing along their length only (Fly.spikeLength);
   // the photograph minus them is the glow -- at the start the two add up to the photograph.
   // All channels are drawn together (Render.drawSprites); each has its own twinkle.
   var nc = T.length, tw = [], glow = [], spikes = [], parts = [], c;
   for ( c = 0; c < nc; ++c )
   {
      var cc = Math.min( c, sp.pixels.length - 1 );
      tw.push( Fly.twinkle( q.j + 1, seconds, opts.twinkle, c ) );
      spikes.push( sp.spikeAngles && sp.modelSpikes ? sp.modelSpikes[cc] : null );
      glow.push( spikes[c] ? Render.glowPatch( sp, cc ) : sp.pixels[cc] );
      parts.push( ( wc > 0 ) ? Render.splitPatches( sp, cc, q.rc, glow[c] ) : null );
   }
   var pick = function( key ) { return parts.map( function( p ) { return p[key]; } ); };
   var draws = [ [ glow, sp, 1 - wo ] ];
   if ( parts[0] ) draws.push( [ pick( "photoCore" ), parts[0].coreSprite, wo - wc ], [ pick( "modelCore" ), parts[0].coreSprite, wc ] );
   if ( parts[0] && wo > 0 ) draws.push( [ pick( "modelOuter" ), sp, wo ] );
   draws.forEach( function( pw )
   {
      if ( pw[2] == 0 ) return;
      var s1 = tw.map( function( t ) { return t*pw[2]/path.length; } );
      var ks = s1.map( function( x ) { return q.kCore*x; } ), kOs = s1.map( function( x ) { return q.kOuter*x; } );
      path.forEach( function( p ) { Render.drawSprites( T, outW, outH, pw[0], pw[1], p.x, p.y, q.g, ks, cam, kOs, q.rc, { seen: q.seen } ); } );
   } );
   if ( spikes[0] )
   {
      var sk = tw.map( function( t ) { return q.alpha*t/path.length; } ), how = { seen: q.seen, spike: { length: q.spikeLength, angles: sp.spikeAngles } };
      path.forEach( function( p ) { Render.drawSprites( T, outW, outH, spikes, sp, p.x, p.y, q.g, sk, cam, sk, q.rc, how ); } );
   }
};

/* A sprite's core radius: its model's (core + one FWHM), else half its detection box. */
Render.coreRadius = function( sp )
{
   if ( sp.det.model && sp.det.model.core > 0 ) return sp.det.model.core;
   var r = sp.det.rect;
   return r ? Math.max( 1, ( r.x1 - r.x0 )/2 ) : 0;
};

/*
 * Where a star is drawn while the shutter is open (opts.motionBlur): its
 * centres from half a shutter before to half after, a step per ~0.75
 * output px (Fly.shutterSteps). The first frame is drawn still, so it
 * stays the image.
 */
Render.shutterPath = function( sc, sp, e, s, opts, cam, cx, cy )
{
   if ( !opts.motionBlur || !( opts.frameDt > 0 ) || !( opts.t > 0 ) ) return [ { x: cx, y: cy } ];
   var h = 0.5*Fly.SHUTTER*Math.min( opts.frameDt, opts.t ), ends = [ -h, h ].map( function( d )
   {
      var tt = Math.max( 0, Math.min( 1, opts.t + d ) ), m = Fly.moved( sp.source.ra, sp.source.dec, sp.d, sc.target, opts.travel*Fly.ease( tt, opts.easing ) );
      var p = m.front ? sc.project( m.ra, m.dec ) : null;
      return p ? Fly.screenPosition( { x: sp.det.x + p.x - e.p0.x, y: sp.det.y + p.y - e.p0.y }, sp.det, sc.tp, opts.K || 1 ) : { x: cx, y: cy };
   } );
   var len = Math.hypot( ( ends[1].x - ends[0].x )/cam.fx, ( ends[1].y - ends[0].y )/cam.fy ), n = Fly.shutterSteps( len ), out = [];
   for ( var k = 0; k < n; ++k )
   {
      var f = n > 1 ? k/( n - 1 ) : 0.5;
      out.push( { x: ends[0].x + f*( ends[1].x - ends[0].x ), y: ends[0].y + f*( ends[1].y - ends[0].y ) } );
   }
   return out;
};

/* A frame as a 16-bit TIFF. */
/*
 * A frame as a 16-bit TIFF carrying exactly the ICC profile given (sRGB
 * bytes for SDR, null for HDR -- no ICC profile describes PQ or HLG).
 * Written through FileFormatInstance, from a 16-bit copy of the image and
 * no window, because a window's saveAs embeds
 * PixInsight's default profile (ProPhoto on the maintainer's machine), and
 * frames holding sRGB or HLG data were tagged ProPhoto (read back from a
 * real render, 2026-09-23).
 */
Render.writeTiff = function( image, path, icc )
{
   // no window: a window per frame brought PixInsight to the front on every frame of a render
   var out = new Image( image.width, image.height, image.numberOfChannels,
                        image.numberOfChannels >= 3 ? ColorSpace_RGB : ColorSpace_Gray, 16, SampleType_Integer );
   try
   {
      out.apply( image );
      if ( File.exists( path ) )
         File.remove( path );
      var f = new FileFormatInstance( new FileFormat( ".tif", false, true ) );
      // quiet: the TIFF module otherwise logs "ICC profile embedded: ..." for every frame (the file is the same)
      if ( !f.create( path, "verbosity 0" ) )
         throw new Error( "Could not create " + path );
      try
      {
         if ( icc ) f.iccProfile = icc;
         if ( !f.writeImage( out ) )
            throw new Error( "Could not write " + path );
      }
      finally { f.close(); }
   }
   finally { out.free(); }
};

/* The sRGB profile's bytes, for SDR frames; read once. */
Render.srgbIcc = function()
{
   if ( Render.srgbIccCache === undefined )
      try { Render.srgbIccCache = Steps.iccProfileBytes( Fly.SRGB_PROFILE_NAME ); }
      catch ( e ) { Render.srgbIccCache = null; Util.warn( "fly", "no sRGB profile to embed in SDR frames: " + e ); }
   return Render.srgbIccCache;
};

/* Milliseconds per frame over `n` frames spread along the path. */
Render.benchmark = function( sc, outW, outH, crop, opts, n )
{
   var t0 = Date.now();
   for ( var i = 0; i < n; ++i )
      Render.frame( sc, n > 1 ? i/( n - 1 ) : 0, opts, outW, outH, crop ).free();
   return ( Date.now() - t0 )/n;
};

/* ---------------------------------------------------------------------------
 * ffmpeg.
 * ------------------------------------------------------------------------ */

Render.FFMPEG_SETTING = "Loom/ffmpegPath";

/*
 * Runs `program` with an argument list (no shell, so "%05d" survives on
 * Windows) and returns { exitCode, output } with stdout and stderr. Both
 * pipes are read as they fill, pumping events -- as Loom's other external
 * processes are -- because ffmpeg writes its progress to stderr and a pipe
 * nobody reads stalls a long encode. exitCode is -1 on deadline or cancel.
 */
Render.runProcess = function( program, args, deadlineMs, isCancelled, onOutput )
{
   var P = new ExternalProcess, out = "";
   function take( chunk ) { out += chunk; if ( onOutput ) onOutput( chunk ); }
   P.onStandardOutputDataAvailable = function() { take( String( P.stdout ) ); };
   P.onStandardErrorDataAvailable = function() { take( String( P.stderr ) ); };
   try
   {
      P.start( program, args );
      var until = deadlineMs ? Date.now() + deadlineMs : Infinity;
      while ( P.isStarting || P.isRunning )
      {
         CoreApplication.processEvents();
         if ( Date.now() > until || ( isCancelled && isCancelled() ) )
         {
            try { P.terminate(); } catch ( e ) {}
            return { exitCode: -1, output: out };
         }
      }
      return { exitCode: P.exitCode, output: out };
   }
   catch ( e )
   {
      return { exitCode: -1, output: out + String( e ) };
   }
   finally
   {
      P.onStandardOutputDataAvailable = null;
      P.onStandardErrorDataAvailable = null;
   }
};

Render.isWorkingFfmpeg = function( path )
{
   try { if ( !File.exists( path ) ) return false; } catch ( e ) { return false; }
   var r = Render.runProcess( path, [ "-version" ], 10000 );
   return r.exitCode == 0 && /^ffmpeg version/.test( String( r.output ).trim() );
};

/*
 * The saved path, then the platform's usual places, then PATH; the first
 * that answers -version is remembered. `platform` is for the suite.
 */
Render.findFfmpeg = function( platform )
{
   var saved = "", pathVar = "", home = "";
   try { saved = String( Settings.read( Render.FFMPEG_SETTING, DataType_String ) || "" ); } catch ( e ) {}
   try { pathVar = String( System.getEnvironmentVariable( "PATH" ) || "" ); } catch ( e ) {}
   try { home = File.homeDirectory; } catch ( e ) {}
   var candidates = Fly.ffmpegCandidates( Util.platform( platform ), home, pathVar, saved );
   for ( var i = 0; i < candidates.length; ++i )
      if ( Render.isWorkingFfmpeg( candidates[i] ) )
      {
         try { Settings.write( Render.FFMPEG_SETTING, DataType_String, candidates[i] ); } catch ( e ) {}
         return candidates[i];
      }
   return null;
};

Render.ffmpegEncoders = function( path )
{
   return Render.runProcess( path, [ "-hide_banner", "-encoders" ], 10000 ).output;
};

/* Encodes; true when ffmpeg succeeded and the video (the last argument) exists. */
Render.encode = function( path, args, isCancelled, onOutput )
{
   var r = Render.runProcess( path, args, 0, isCancelled, onOutput );
   Render.lastEncodeOutput = r.output;
   try { return r.exitCode == 0 && File.exists( args[args.length - 1] ); }
   catch ( e ) { return false; }
};

/*
 * The frame due at wall-clock `now` for a clip of n frames at fps that
 * started at `startedAt` (ms). Late ticks skip ahead instead of slowing
 * the clip; it loops.
 */
Render.dueFrame = function( startedAt, now, fps, n )
{
   if ( !( n > 0 ) )
      return 0;
   return Math.floor( ( now - startedAt )/1000*fps ) % n;
};

/*
 * The scene turned 90 degrees clockwise: pixel (x, y) goes to (h - 1 - y, x).
 * Backdrop, residual, every sprite and the projection turn together, so
 * frame 0 of the turned scene is exactly the turned image.
 */
Render.rotateScene = function( sc )
{
   var w = sc.w, h = sc.h;
   function turn( buf, bw, bh )
   {
      var out = new Float32Array( bw*bh );
      for ( var y = 0; y < bh; ++y )
         for ( var x = 0; x < bw; ++x )
            out[x*bh + ( bh - 1 - y )] = buf[y*bw + x];
      return out;
   }
   function pt( p ) { return p == null ? null : { x: h - 1 - p.y, y: p.x }; }
   var sprites = sc.sprites.map( function( e )
   {
      var s = e.s, r = s.rect, rw = r.x1 - r.x0, rh = r.y1 - r.y0;
      var turned = Object.assign( {}, s, {
         det: Object.assign( {}, s.det, pt( s.det ) ),
         rect: { x0: h - r.y1, y0: r.x0, x1: h - r.y0, y1: r.x1 },
         pixels: s.pixels.map( function( b ) { return turn( b, rw, rh ); } ) } );
      // every patch it carries turns with it (a model left as it was sat rotated against the photograph)
      [ "modelCore", "modelSpikes" ].forEach( function( k ) { if ( s[k] ) turned[k] = s[k].map( function( b ) { return turn( b, rw, rh ); } ); } );
      delete turned._split;                     // core parts cut before the turn are not this sprite's
      delete turned._glow;
      if ( s.spikeAngles ) turned.spikeAngles = s.spikeAngles.map( function( a ) { return a + Math.PI/2; } );   // (x, y) -> (h - 1 - y, x) turns directions by 90 degrees
      if ( s.mask ) turned.mask = Uint8Array.from( turn( Float32Array.from( s.mask ), rw, rh ) );
      return { s: turned, p0: pt( e.p0 ) };
   } );
   var project = sc.project;
   return Object.assign( {}, sc, {
      w: h, h: w,
      S: sc.S.map( function( b ) { return turn( b, w, h ); } ),
      R: sc.R.map( function( b ) { return turn( b, w, h ); } ),
      sprites: sprites, tp: pt( sc.tp ),
      catalogue: sc.catalogue ? sc.catalogue.map( function( c ) { return Object.assign( {}, c, pt( c ) ); } ) : null,
      project: function( ra, dec ) { return pt( project( ra, dec ) ); } } );
};

/* The scene in the orientation a preset needs, turning it once and keeping it. */
Render.sceneFor = function( sc, outW, outH )
{
   if ( !Fly.needsRotation( sc.w, sc.h, outW, outH ) )
      return sc;
   if ( !sc.turned ) sc.turned = Render.rotateScene( sc );
   sc.turned.D = sc.D;              // the dialog sets the distance on the scene: the turned copy follows it
   return sc.turned;
};
