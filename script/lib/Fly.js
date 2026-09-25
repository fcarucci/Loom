/*
 * Loom Fly-Through: the maths. Pure -- no PixInsight call, no file access --
 * so every rule that decides what moves where is tested under node.
 */
var Fly = {};

Fly.RAD = Math.PI/180;

Fly.vec = function( ra, dec )
{
   var a = ra*Fly.RAD, d = dec*Fly.RAD;
   return [ Math.cos( d )*Math.cos( a ), Math.cos( d )*Math.sin( a ), Math.sin( d ) ];
};

Fly.radec = function( v )
{
   var n = Math.sqrt( v[0]*v[0] + v[1]*v[1] + v[2]*v[2] );
   var ra = Math.atan2( v[1], v[0] )/Fly.RAD;
   return { ra: ( ra + 360 ) % 360, dec: Math.asin( v[2]/n )/Fly.RAD };
};

Fly.dot = function( a, b ) { return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]; };

/* Angle between two {ra,dec} directions, degrees. */
Fly.separation = function( a, b )
{
   var c = Math.max( -1, Math.min( 1, Fly.dot( Fly.vec( a.ra, a.dec ), Fly.vec( b.ra, b.dec ) ) ) );
   return Math.acos( c )/Fly.RAD;
};

/*
 * A star at distance d, direction (ra,dec), seen from a camera moved s
 * towards the target: p = d v - s u. Exact; no small-angle assumption.
 */
Fly.moved = function( ra, dec, d, target, s )
{
   var v = Fly.vec( ra, dec ), u = Fly.vec( target.ra, target.dec );
   var p = [ d*v[0] - s*u[0], d*v[1] - s*u[1], d*v[2] - s*u[2] ];
   var len = Math.sqrt( Fly.dot( p, p ) );
   var r = Fly.radec( p );
   return { ra: r.ra, dec: r.dec, front: Fly.dot( p, u ) > 0, ratio: d/len };
};

/*
 * Where a star is drawn when the backdrop zooms by K about the target (tp):
 * its parallax position c (p0 at the start), but never nearer the target
 * than the backdrop would carry it -- whatever is in front of the backdrop
 * spreads out at least as fast, or it reads as behind it.
 */
Fly.screenPosition = function( c, p0, tp, K )
{
   var r0 = Math.hypot( p0.x - tp.x, p0.y - tp.y ), r = Math.hypot( c.x - tp.x, c.y - tp.y ), want = K*r0;
   if ( !( K > 1 ) || r >= want ) return { x: c.x, y: c.y };
   if ( r == 0 ) return { x: tp.x + ( p0.x - tp.x )*K, y: tp.y + ( p0.y - tp.y )*K };
   return { x: tp.x + ( c.x - tp.x )*want/r, y: tp.y + ( c.y - tp.y )*want/r };
};

Fly.BACKDROP_MOTION_DEFAULT = 0.4;

/*
 * Star quality: how the star sprites are drawn. Highest is the full renderer
 * from the full-size (4K working) scene -- at 1080p each output pixel
 * averages 2 x 2 or 3 x 3 samples of it. High and Medium draw from the scene
 * shrunk to the video's size (Fly.qualityScale: one scene pixel per output
 * pixel) and sample a magnified glow only as densely as its
 * footprint needs (Render.glowSamples). Medium also draws the stars layer
 * at half the video's resolution (starRes) from coarse patches -- a quarter
 * of the fill -- and upsamples it onto the full-size nebula: softer stars.
 * Without a level: Highest.
 */
Fly.STAR_QUALITY = { highest: { footprint: false, outScale: 0, starRes: 1 }, high: { footprint: true, outScale: 1, starRes: 1 },
                     medium: { footprint: true, outScale: 1, starRes: 0.5 } };
Fly.starQuality = function( level )
{
   return Object.prototype.hasOwnProperty.call( Fly.STAR_QUALITY, level ) ? Fly.STAR_QUALITY[level] : Fly.STAR_QUALITY.highest;
};

/*
 * The scale a level draws its scene at, for a crop cropW scene pixels wide
 * drawn outW wide: High and Medium shrink the scene until one output pixel
 * spans outScale scene pixels (1 for both), never enlarging it; Highest keeps
 * the full-size (4K working) scene: 1.
 */
Fly.qualityScale = function( level, cropW, outW )
{
   var target = Fly.starQuality( level ).outScale;
   return target > 0 ? Math.min( 1, target*outW/cropW ) : 1;
};

/*
 * The nebula's zoom at camera travel s: `motion` (0-1) of the physically
 * correct growth D/(D - s). At its true distance the Elephant's Trunk grew
 * 1.25x over a clip, far too much to watch; the stars keep their real
 * motion, so depth still reads. A galaxy (D infinite) stays fixed.
 */
Fly.backdropZoom = function( D, s, motion )
{
   return 1 + ( Fly.backdropScale( D, s ) - 1 )*motion;
};

Fly.backdropScale = function( D, s )
{
   return ( D === Infinity ) ? 1 : D/( D - s );
};

/* Gnomonic (TAN) projection through FITS WCS keywords, 0-based pixels. */
Fly.tanProject = function( ra, dec, w )
{
   var a = ra*Fly.RAD, d = dec*Fly.RAD, a0 = w.crval1*Fly.RAD, d0 = w.crval2*Fly.RAD;
   var cosc = Math.sin( d0 )*Math.sin( d ) + Math.cos( d0 )*Math.cos( d )*Math.cos( a - a0 );
   var xi  = Math.cos( d )*Math.sin( a - a0 )/cosc/Fly.RAD;
   var eta = ( Math.cos( d0 )*Math.sin( d ) - Math.sin( d0 )*Math.cos( d )*Math.cos( a - a0 ) )/cosc/Fly.RAD;
   var c = w.cd, det = c[0]*c[3] - c[1]*c[2];
   var dx = (  c[3]*xi - c[1]*eta )/det, dy = ( -c[2]*xi + c[0]*eta )/det;
   return { x: w.crpix1 - 1 + dx, y: w.crpix2 - 1 + dy };
};

/*
 * Where a sprite pixel drawn rOut from the star's centre comes from, grown
 * by g: inside the core (rc) nowhere else -- a star is a point, and a core
 * magnified whole looked like a soft blob -- beyond it the light is
 * stretched outward, so the glow and spikes grow as the star nears.
 */
Fly.radialSource = function( rOut, rc, g, seen )
{
   // a partly hidden star's glow and spikes are shorter by the share of it that is seen (Fly.discVisible)
   return rOut <= rc ? rOut : rc + ( rOut - rc )/( g*( seen != null ? Math.max( 0.05, seen ) : 1 ) );
};

/* The share of a disc (centre u, v, radius r) inside a W x H frame, from a 24 x 24 grid over it. */
Fly.discVisible = function( u, v, r, W, H )
{
   if ( u - r >= 0 && v - r >= 0 && u + r <= W && v + r <= H ) return 1;
   var n = 24, inDisc = 0, inFrame = 0;
   for ( var j = 0; j < n; ++j )
      for ( var i = 0; i < n; ++i )
      {
         var x = ( ( i + 0.5 )/n*2 - 1 )*r, y = ( ( j + 0.5 )/n*2 - 1 )*r;
         if ( x*x + y*y > r*r ) continue;
         ++inDisc;
         if ( u + x >= 0 && u + x < W && v + y >= 0 && v + y < H ) ++inFrame;
      }
   return inDisc ? inFrame/inDisc : 0;
};

/* The share of a disc of radius r1 covered by a disc of radius r2 whose centre is d away (the lens area). */
Fly.coverFraction = function( r1, r2, d )
{
   if ( d >= r1 + r2 ) return 0;
   if ( d + r1 <= r2 ) return 1;
   if ( d + r2 <= r1 ) return ( r2*r2 )/( r1*r1 );
   var a1 = r1*r1*Math.acos( ( d*d + r1*r1 - r2*r2 )/( 2*d*r1 ) ), a2 = r2*r2*Math.acos( ( d*d + r2*r2 - r1*r1 )/( 2*d*r2 ) );
   var k = 0.5*Math.sqrt( ( -d + r1 + r2 )*( d + r1 - r2 )*( d - r1 + r2 )*( d + r1 + r2 ) );
   return ( a1 + a2 - k )/( Math.PI*r1*r1 );
};

Fly.HDR_MAG_RANGE = 5;         // magnitudes from the brightest star to one that gets no headroom (100x in flux)
Fly.HEADROOM_RADIUS = 6;       // px (scene): the area a faint star's gain covers; the brightest cover 4x
Fly.HEADROOM_SIGMA = 0.5;      // a star's bump: its sigma, of that area's radius
Fly.HDR_REFERENCE_WHITE = 203; // nits: SDR white in an HDR video (ITU-R BT.2408)

/*
 * The factor a star's light is multiplied by, r from its centre, to reach
 * into the HDR headroom: `gain` at the centre, falling smoothly (a Gaussian
 * of `sigma`) to 1. Shaped by the distance, not by the pixel's value: a
 * core the photograph clipped flat would otherwise be lifted whole -- a
 * flat disc with a hard edge where the value crossed a knee.
 */
Fly.headroomBump = function( r, sigma, gain )
{
   return 1 + ( gain - 1 )*Math.exp( -r*r/( 2*sigma*sigma ) );
};

/* A star's HDR peak from its magnitude G: `peak` for the brightest (gBright), 1 by HDR_MAG_RANGE fainter, linear in magnitude (log in flux). */
Fly.magnitudeGain = function( G, gBright, peak )
{
   var w = Math.max( 0, Math.min( 1, ( gBright + Fly.HDR_MAG_RANGE - G )/Fly.HDR_MAG_RANGE ) );
   return 1 + ( peak - 1 )*w;
};

Fly.SPIKE_LENGTH_MAX = 4;   // the longest a star's spikes are drawn, as a multiple of their measured length

/*
 * How long a star's spikes are drawn, as a multiple of their measured
 * length. A spike falling as 1/r^2, L times longer, shows what an
 * L^2-times brighter one shows: so the square root of the brightening --
 * the light ratio when brightening -- at most SPIKE_LENGTH_MAX, and never
 * less than the glow's growth g. Their width does not change.
 */
Fly.spikeLength = function( ratio, brightening, g )
{
   return Math.max( g, Math.min( Fly.SPIKE_LENGTH_MAX, brightening ? ratio : 1 ) );
};

Fly.SHUTTER = 0.5;   // of a frame's interval: a 180-degree shutter

/* Steps a star is drawn at along the path it covers while the shutter is open (px). */
Fly.shutterSteps = function( pathPx )
{
   return Math.max( 1, Math.min( 12, Math.ceil( pathPx/0.75 ) ) );
};

Fly.TWINKLE_DEFAULT = 0.03;   // a star's brightness wobble, as a fraction

/* A repeatable random number in [0, 1) for star `seed`, draw k. */
Fly.seeded = function( seed, k )
{
   var x = Math.sin( seed*12.9898 + k*78.233 )*43758.5453;
   return x - Math.floor( x );
};

/*
 * A star's twinkle at `seconds` into the clip: 1 + amount x a sum of three
 * slow waves (0.3-1.5 Hz) of its own, minus their value at the start, so
 * the first frame is untouched. Not physical -- space has no air to make
 * stars twinkle -- a touch of life. Channel c shifts the fastest wave's
 * phase, so the colours shimmer a little apart.
 */
Fly.twinkle = function( seed, seconds, amount, c )
{
   if ( !( amount > 0 ) ) return 1;
   var sum = 0, sum0 = 0;
   for ( var k = 0; k < 3; ++k )
   {
      var f = 0.3 + 1.2*Fly.seeded( seed, 3*k ), ph = 2*Math.PI*Fly.seeded( seed, 3*k + 1 ) + ( k == 2 ? 0.6*( c || 0 ) : 0 );
      var a = ( k == 0 ) ? 0.5 : 0.25;
      sum += a*Math.sin( 2*Math.PI*f*seconds + ph );
      sum0 += a*Math.sin( ph );
   }
   return 1 + amount*( sum - sum0 );
};

Fly.GROWTH_DEFAULT = 0.15;

Fly.smoothstep = function( e0, e1, x )
{
   var t = Math.max( 0, Math.min( 1, ( x - e0 )/( e1 - e0 ) ) );
   return t*t*( 3 - 2*t );
};

/* Sprite size factor for a star whose light ratio is `ratio` = d/|p|. */
Fly.growth = function( ratio, growth ) { return 1 + growth*( ratio - 1 ); };

/*
 * Multiplier on a sprite's pixels AFTER it is resampled by g: the sprite's
 * total light then scales as ratio^2 (inverse square) -- or stays the same
 * with brightening off -- whatever the growth.
 */
Fly.pixelScale = function( ratio, growth, brightening )
{
   var g = Fly.growth( ratio, growth );
   return ( brightening ? ratio*ratio : 1 )/( g*g );
};

Fly.MODEL_BLEND = [ 2, 4 ];   // light ratios over which a star turns from its photograph to its model

Fly.CORE_MODEL_BLEND = [ 1, 1.5 ];   // ...and its core sooner: a saturated core is often a flat square in the photograph

/* How much of a star's core is drawn from its round model: none at the start, all by 1.5x nearer. */
Fly.coreModelWeight = function( ratio )
{
   return Fly.smoothstep( Fly.CORE_MODEL_BLEND[0], Fly.CORE_MODEL_BLEND[1], ratio );
};

/* How much of a star is drawn from its model at light ratio `ratio`: none far away (frame 0 is the photograph), all up close. */
Fly.modelWeight = function( ratio )
{
   return Fly.smoothstep( Fly.MODEL_BLEND[0], Fly.MODEL_BLEND[1], ratio );
};

Fly.FADE_NEAR = [ 6, 14 ];   // light ratios over which a close star fades out (was 8-20: close stars grew past what a sprite bears)

/* Stars fade out as they near the camera, and are never drawn once passed. */
Fly.opacity = function( ratio, front )
{
   if ( !front )
      return 0;
   return 1 - Fly.smoothstep( Fly.FADE_NEAR[0], Fly.FADE_NEAR[1], ratio );
};

/*
 * Gaia DR3's catalogue rows here carry no per-source parallax error, so
 * quality comes from DR3's published median uncertainty by magnitude
 * (Lindegren et al. 2021): a population model, not a per-star error.
 */
Fly.PARALLAX_ZERO_POINT = 0.017;                   // mas, DR3 global offset
Fly.SIGMA_TABLE = [ [ 15, 0.02 ], [ 17, 0.07 ], [ 17.6, 0.10 ] ];

Fly.parallaxSigma = function( G )
{
   var t = Fly.SIGMA_TABLE;
   if ( !( G > t[0][0] ) )
      return t[0][1];
   for ( var i = 1; i < t.length; ++i )
      if ( G <= t[i][0] )
         return t[i-1][1] + ( G - t[i-1][0] )*( t[i][1] - t[i-1][1] )/( t[i][0] - t[i-1][0] );
   return t[t.length - 1][1];
};

Fly.usableParallax = function( s )
{
   if ( typeof s.plx != "number" || !isFinite( s.plx ) )
      return null;
   var p = s.plx + Fly.PARALLAX_ZERO_POINT;
   return ( p > 0 && p >= 5*Fly.parallaxSigma( s.G ) ) ? p : null;
};

Fly.ILLUMINATOR_MAX_G = 10;      // a star must be at least this bright to light a nebula
Fly.ILLUMINATOR_MIN_RADIUS = 0.1; // degrees: the smallest area searched about the target

/*
 * The distance of the star that lights a nebula with no ionising cluster
 * (a reflection nebula such as NGC 7023): the brightest star within the
 * nebula (its catalogued radius, at least ILLUMINATOR_MIN_RADIUS) with a
 * reliable parallax and bright enough (G < ILLUMINATOR_MAX_G). Returns
 * { distance (pc), G, source } or null.
 */
Fly.illuminatorRadius = function( target )
{
   return Math.max( Fly.ILLUMINATOR_MIN_RADIUS, ( target.diameter > 0 ? target.diameter/120 : 0 ) );
};

Fly.illuminatorDistance = function( sources, target )
{
   var r = Fly.illuminatorRadius( target ), best = null;
   ( sources || [] ).forEach( function( s )
   {
      if ( !( s.G < Fly.ILLUMINATOR_MAX_G ) || Fly.separation( s, target ) > r ) return;
      var p = Fly.usableParallax( s );
      if ( p != null && ( !best || s.G < best.G ) ) best = { distance: 1000/p, G: s.G, source: s };
   } );
   return best;
};

Fly.CLUSTER_MIN_MEMBERS = 30;
Fly.CLUSTER_MIN_CONTRAST = 3;
Fly.CLUSTER_MIN_SIGNIFICANCE = 5;   // excess over the field, in Poisson sigmas
Fly.CLUSTER_MAX_RADIUS = 0.45;      // degrees; the annulus reaches 3x this
Fly.CLUSTER_PM_SCALE = 0.3;         // kernel in proper motion, x the field's MAD
Fly.CLUSTER_PLX_SCALE = 3;          // kernel in parallax, x sigma(G)
Fly.CLUSTER_GROW = 1.5;             // membership kernel, x the seed kernel

Fly.median = function( v )
{
   var s = v.slice().sort( function( a, b ) { return a - b; } );
   return s[s.length >> 1];
};

Fly.mad = function( v )
{
   var m = Fly.median( v );
   return 1.4826*Fly.median( v.map( function( x ) { return Math.abs( x - m ); } ) ) || 1;
};

/*
 * The inside radius the finder uses for a target of catalogued radius
 * `targetRadiusDeg`. A big nebula's cluster sits near its centre, and the
 * comparison annulus reaches three times this -- capped, it stays within a
 * query of a degree or two instead of the whole of IC 1396's 4 degrees.
 */
Fly.clusterRadius = function( targetRadiusDeg )
{
   return Math.min( targetRadiusDeg, Fly.CLUSTER_MAX_RADIUS );
};

/* Stars within radius ("inside") and in the 1.5-3 radii annulus ("field"). */
Fly.clusterSamples = function( sources, target, radiusDeg )
{
   var inside = [], field = [];
   sources.forEach( function( s )
   {
      var p = Fly.usableParallax( s );
      if ( p == null || !( s.G < 16 ) || !isFinite( s.pmra ) || !isFinite( s.pmdec ) )
         return;
      var sep = Fly.separation( s, target ), rec = { p: p, a: s.pmra, d: s.pmdec, sp: Fly.parallaxSigma( s.G ) };
      if ( sep <= radiusDeg ) inside.push( rec );
      else if ( sep >= 1.5*radiusDeg && sep <= 3*radiusDeg ) field.push( rec );
   } );
   return { inside: inside, field: field };
};

/*
 * The ionising cluster, by shared motion and distance.
 *
 * Each inside star is scored by how far the inside stars near it (in pm
 * and parallax) exceed what the field predicts, in Poisson sigmas -- an
 * excess, not a ratio, because a ratio lets a chance clump of five stars
 * over an empty field outrank a real cluster of fifty. The best seed's
 * centre is then refined to the median of its neighbours, and members are
 * taken with a wider kernel: a real cluster's parallaxes spread by its
 * depth as well as by Gaia's error.
 *
 * Validated on IC 1396: 922 pc (Trumpler 37; published ~925 pc).
 */
Fly.findCluster = function( sources, target, radiusDeg )
{
   var S = Fly.clusterSamples( sources, target, radiusDeg ), inside = S.inside, field = S.field;
   if ( inside.length < Fly.CLUSTER_MIN_MEMBERS || field.length < Fly.CLUSTER_MIN_MEMBERS )
      return null;
   var sa = Fly.mad( field.map( function( x ) { return x.a; } ) )*Fly.CLUSTER_PM_SCALE;
   var sd = Fly.mad( field.map( function( x ) { return x.d; } ) )*Fly.CLUSTER_PM_SCALE;
   var scale = inside.length/field.length;
   function near( c, k )
   {
      return function( x )
      {
         var da = ( x.a - c.a )/sa, dd = ( x.d - c.d )/sd,
             dp = ( x.p - c.p )/( Fly.CLUSTER_PLX_SCALE*Math.max( c.sp, x.sp ) );
         return da*da + dd*dd + dp*dp <= k*k;
      };
   }
   function counts( c, k ) { return { n: inside.filter( near( c, k ) ).length, f: field.filter( near( c, k ) ).length*scale }; }

   var best = null;
   inside.forEach( function( c )
   {
      var n = counts( c, 1 ), sig = ( n.n - n.f )/Math.sqrt( n.f + 1 );
      if ( best == null || sig > best.sig ) best = { centre: c, sig: sig };
   } );
   var centre = Fly.refineCluster( inside, best.centre, near );
   var members = inside.filter( near( centre, Fly.CLUSTER_GROW ) );
   var n = counts( centre, Fly.CLUSTER_GROW );
   if ( members.length < Fly.CLUSTER_MIN_MEMBERS || n.n/Math.max( 1, n.f ) < Fly.CLUSTER_MIN_CONTRAST ||
        best.sig < Fly.CLUSTER_MIN_SIGNIFICANCE )
      return null;
   var ps = members.map( function( x ) { return x.p; } ).sort( function( a, b ) { return a - b; } );
   var q = function( f ) { return ps[Math.min( ps.length - 1, Math.floor( f*ps.length ) )]; };
   return { distance: 1000/q( 0.5 ), members: ps.length, lo: 1000/q( 0.75 ), hi: 1000/q( 0.25 ) };
};

/* Moves the seed to the median of its members, a few times. */
Fly.refineCluster = function( inside, seed, near )
{
   var c = seed;
   for ( var it = 0; it < 5; ++it )
   {
      var m = inside.filter( near( c, Fly.CLUSTER_GROW ) );
      if ( m.length == 0 )
         break;
      c = { a: Fly.median( m.map( function( x ) { return x.a; } ) ),
            d: Fly.median( m.map( function( x ) { return x.d; } ) ),
            p: Fly.median( m.map( function( x ) { return x.p; } ) ), sp: seed.sp };
   }
   return c;
};

Fly.parseSources = function( text )
{
   var lines = String( text ).split( /\r?\n/ ), head = lines[0].split( "\t" ), out = [];
   for ( var i = 1; i < lines.length; ++i )
   {
      if ( !lines[i] ) continue;
      var f = lines[i].split( "\t" ), s = {};
      for ( var j = 0; j < head.length; ++j ) s[head[j]] = parseFloat( f[j] );
      out.push( s );
   }
   return out;
};

Fly.defaultTravel = function( type, D ) { return ( type == "galaxy" ) ? 200 : 0.2*D; };

Fly.clampTravel = function( travel, type, D )
{
   if ( type == "galaxy" || !( travel > 0.9*D ) )
      return { travel: travel, clamped: false };
   return { travel: 0.9*D, clamped: true };
};

Fly.ease = function( t, mode ) { return ( mode == "linear" ) ? t : Fly.smoothstep( 0, 1, t ); };

Fly.frameCount = function( duration, fps, pingPong )
{
   return Math.round( duration*fps )*( pingPong ? 2 : 1 );
};

Fly.timeAt = function( frame, frames, pingPong )
{
   if ( !pingPong )
      return frames <= 1 ? 0 : frame/( frames - 1 );
   var half = frames/2;
   return ( frame <= half ) ? frame/half : ( frames - frame )/half;
};

Fly.DRAFT_BYTES = 400e6;
Fly.DRAFT_LONG = 480;   // px: the draft's long side

Fly.draftPlan = function( duration, fps, longSide, aspect )
{
   var w = ( aspect >= 1 ) ? longSide : Math.round( longSide*aspect );
   var h = ( aspect >= 1 ) ? Math.round( longSide/aspect ) : longSide;
   var per = w*h*4, want = fps/4, frames = Math.round( duration*want );   // a quarter of the frames: a draft is for judging, and fast
   if ( frames*per > Fly.DRAFT_BYTES )
   {
      frames = Math.floor( Fly.DRAFT_BYTES/per );
      want = frames/duration;
   }
   return { width: w, height: h, fps: want, frames: frames, bytes: frames*per };
};

Fly.PRESETS = { social_vertical: [ 1080, 1920 ], social_square: [ 1080, 1080 ],
                youtube_4k: [ 3840, 2160 ], youtube_1080: [ 1920, 1080 ], exhibition: [ 3840, 2160 ] };

Fly.presetCrop = function( imgW, imgH, tx, ty, outW, outH )
{
   var aspect = outW/outH, w = imgW, h = Math.round( imgW/aspect );
   if ( h > imgH ) { h = imgH; w = Math.round( imgH*aspect ); }
   var x = Math.round( tx - w/2 ), y = Math.round( ty - h/2 );
   x = Math.max( 0, Math.min( imgW - w, x ) );
   y = Math.max( 0, Math.min( imgH - h, y ) );
   return { x: x, y: y, w: w, h: h };
};

Fly.parseNgcIc = function( text )
{
   var lines = String( text ).split( /\r?\n/ ), out = [];
   for ( var i = 1; i < lines.length; ++i )
   {
      var f = lines[i].split( "," );
      if ( f.length < 9 || !f[0] ) continue;
      out.push( { id: f[0], ra: parseFloat( f[1] ), dec: parseFloat( f[2] ),
                  diameter: parseFloat( f[4] ) || 0, name: f[7] || "", pgc: f[8] || "", messier: ( f[10] || "" ).trim() } );
   }
   return out;
};

/*
 * Popular names the NGC/IC catalogue lacks. Most point at a catalogue id,
 * so the position is the catalogue's; a named part of a larger object
 * (the Elephant's Trunk in IC 1396) carries its own position (J2000, deg).
 */
Fly.OBJECT_ALIASES = [
   { names: [ "Elephant's Trunk Nebula" ], id: "IC1396A", ra: 324.0, dec: 57.5, diameter: 30 },
   { names: [ "Horsehead Nebula" ], id: "B33", ra: 85.246, dec: -2.458, diameter: 8 },
   { names: [ "Splinter Galaxy", "Knife Edge Galaxy" ], id: "NGC5907" },
   { names: [ "Needle Galaxy" ], id: "NGC4565" },
   { names: [ "Heart Nebula" ], id: "IC1805" },
   { names: [ "Soul Nebula" ], id: "IC1848" },
   { names: [ "Rosette Nebula" ], id: "NGC2237" },
   { names: [ "Pacman Nebula" ], id: "NGC281" },
   { names: [ "Wizard Nebula" ], id: "NGC7380" },
   { names: [ "Bubble Nebula" ], id: "NGC7635" },
   { names: [ "Crescent Nebula" ], id: "NGC6888" },
   { names: [ "Iris Nebula" ], id: "NGC7023" },
   { names: [ "Cocoon Nebula" ], id: "IC5146" },
   { names: [ "Flame Nebula" ], id: "NGC2024" },
   { names: [ "Cone Nebula", "Christmas Tree Cluster" ], id: "NGC2264" },
   { names: [ "Witch Head Nebula" ], id: "IC2118" },
   { names: [ "Jellyfish Nebula" ], id: "IC443" },
   { names: [ "Flaming Star Nebula" ], id: "IC405" },
   { names: [ "Tadpoles Nebula" ], id: "IC410" },
   { names: [ "California Nebula" ], id: "NGC1499" },
   { names: [ "Fish Head Nebula" ], id: "IC1795" },
   { names: [ "Eastern Veil Nebula" ], id: "NGC6992" },
   { names: [ "Western Veil Nebula", "Witch's Broom Nebula" ], id: "NGC6960" },
   { names: [ "Seagull Nebula" ], id: "IC2177" },
   { names: [ "Fireworks Galaxy" ], id: "NGC6946" },
   { names: [ "Cat's Eye Nebula" ], id: "NGC6543" },
   { names: [ "Helix Nebula" ], id: "NGC7293" },
   { names: [ "Silver Dollar Galaxy", "Sculptor Galaxy" ], id: "NGC253" },
   { names: [ "Double Cluster" ], id: "NGC869" } ];

Fly.OBJECT_STOPWORDS = [ "the", "nebula", "galaxy", "cluster", "of", "complex" ];
Fly.OBJECT_MIN_SCORE = 0.75;   // below this a name is not what was typed

/* Lower case, apostrophes dropped, punctuation to spaces, stop words out. */
Fly.objectWords = function( s )
{
   return String( s ).toLowerCase().replace( /['\u2019]/g, "" ).replace( /[^a-z0-9]+/g, " " ).trim().split( " " )
      .filter( function( w ) { return w && Fly.OBJECT_STOPWORDS.indexOf( w ) < 0; } );
};

Fly.levenshtein = function( a, b )
{
   var prev = [], cur;
   for ( var j = 0; j <= b.length; ++j ) prev.push( j );
   for ( var i = 1; i <= a.length; ++i )
   {
      cur = [ i ];
      for ( j = 1; j <= b.length; ++j )
         cur.push( Math.min( prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + ( a[i - 1] == b[j - 1] ? 0 : 1 ) ) );
      prev = cur;
   }
   return prev[b.length];
};

/* How well the typed words match a name's words (0..1): each typed word's best match, averaged; a name word nobody typed costs a little. */
Fly.nameScore = function( typed, words )
{
   if ( !typed.length || !words.length ) return 0;
   var used = 0, sum = typed.reduce( function( acc, t )
   {
      var best = 0;
      words.forEach( function( w ) { best = Math.max( best, 1 - Fly.levenshtein( t, w )/Math.max( t.length, w.length ) ); } );
      if ( best >= 0.6 ) ++used;
      return acc + best;
   }, 0 );
   return sum/typed.length - 0.05*Math.max( 0, words.length - used );
};

/*
 * What a person typed in the Object box, best first: [{id, ra, dec, name,
 * diameter, score}]. A catalogue id however it is spaced, or a Messier
 * number, is exact; names -- the catalogue's and Fly.OBJECT_ALIASES -- are
 * matched word by word with typos forgiven. Empty when nothing is close.
 */
Fly.findObject = function( query, entries )
{
   var q = String( query || "" ).trim(), compact = q.toUpperCase().replace( /\s+/g, "" ), out = [];
   if ( !q ) return out;
   var byId = {};
   ( entries || [] ).forEach( function( e ) { byId[e.id.toUpperCase()] = e; } );
   var hit = function( e, score, name ) { out.push( { id: e.id, ra: e.ra, dec: e.dec, diameter: e.diameter || 0, name: name || e.name || "", score: score } ); };
   if ( byId[compact] ) hit( byId[compact], 1 );
   var m = /^M(?:ESSIER)?(\d+)$/.exec( compact );
   if ( m ) ( entries || [] ).forEach( function( e ) { if ( e.messier == "M" + m[1] ) hit( e, 1, e.name || "M" + m[1] ); } );
   var typed = Fly.objectWords( q );
   ( entries || [] ).forEach( function( e )
   {
      if ( !e.name ) return;
      var s = Fly.nameScore( typed, Fly.objectWords( e.name ) );
      if ( s >= Fly.OBJECT_MIN_SCORE ) hit( e, s );
   } );
   Fly.OBJECT_ALIASES.forEach( function( a )
   {
      var s = Math.max.apply( null, a.names.map( function( n ) { return Fly.nameScore( typed, Fly.objectWords( n ) ); } ) );
      if ( s < Fly.OBJECT_MIN_SCORE ) return;
      var e = ( a.ra != null ) ? a : byId[a.id];
      if ( e ) hit( { id: a.id, ra: e.ra, dec: e.dec, diameter: a.diameter || e.diameter }, s, a.names[0] );
   } );
   var seen = {};
   return out.sort( function( a, b ) { return b.score - a.score; } )
             .filter( function( r ) { if ( seen[r.id] ) return false; seen[r.id] = true; return true; } );
};

/* Highest diameter / (1 + separation / field radius); ties to the larger. */
Fly.pickTarget = function( entries, centre, fieldRadiusDeg )
{
   var scored = entries.filter( function( e ) { return Fly.separation( e, centre ) <= fieldRadiusDeg; } )
      .map( function( e ) { return { e: e, score: e.diameter/( 1 + Fly.separation( e, centre )/fieldRadiusDeg ) }; } )
      .sort( function( a, b ) { return ( b.score - a.score ) || ( b.e.diameter - a.e.diameter ); } );
   return { best: scored.length ? scored[0].e : null, runnerUp: scored.length > 1 ? scored[1].e : null };
};

Fly.targetType = function( e ) { return ( e && e.pgc ) ? "galaxy" : "nebula"; };

/* Inverse gnomonic (TAN): 0-based pixel to {ra, dec} degrees. */
Fly.tanUnproject = function( x, y, w )
{
   var c = w.cd, dx = x - ( w.crpix1 - 1 ), dy = y - ( w.crpix2 - 1 );
   var xi = ( c[0]*dx + c[1]*dy )*Fly.RAD, eta = ( c[2]*dx + c[3]*dy )*Fly.RAD;
   var a0 = w.crval1*Fly.RAD, d0 = w.crval2*Fly.RAD;
   var den = Math.cos( d0 ) - eta*Math.sin( d0 );
   var a = a0 + Math.atan2( xi, den );
   var d = Math.atan2( Math.sin( d0 ) + eta*Math.cos( d0 ), Math.sqrt( xi*xi + den*den ) );
   return { ra: ( a/Fly.RAD + 360 ) % 360, dec: d/Fly.RAD };
};

/*
 * Sources with a usable parallax that land inside a w x h image, with
 * their distance in parsecs. `project( ra, dec ) -> {x, y}` is the image's
 * projection.
 */
Fly.placeStars = function( sources, project, w, h )
{
   var out = [];
   sources.forEach( function( s )
   {
      var p = Fly.usableParallax( s );
      if ( p == null )
         return;
      var q = project( s.ra, s.dec );
      if ( q == null || !( q.x >= 0 && q.x < w && q.y >= 0 && q.y < h ) )
         return;
      out.push( { source: s, d: 1000/p, x: q.x, y: q.y } );
   } );
   return out;
};

Fly.MATCH_RADIUS = 1.5;   // px, placed star to detection
Fly.GRID = 16;            // px, spatial hash cell

Fly.rectsIntersect = function( a, b )
{
   return a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;
};

/* A spatial hash of detections by the cells their raw rects cover. */
Fly.detectionGrid = function( dets )
{
   var grid = {};
   dets.forEach( function( d, i )
   {
      for ( var gy = Math.floor( d.rect.y0/Fly.GRID ); gy <= Math.floor( ( d.rect.y1 - 1 )/Fly.GRID ); ++gy )
         for ( var gx = Math.floor( d.rect.x0/Fly.GRID ); gx <= Math.floor( ( d.rect.x1 - 1 )/Fly.GRID ); ++gx )
         {
            var k = gx + "," + gy;
            ( grid[k] = grid[k] || [] ).push( i );
         }
   } );
   return grid;
};

/* Indices of detections whose raw rect meets `r` (deduplicated). */
Fly.detectionsIn = function( grid, r )
{
   var seen = {}, out = [];
   for ( var gy = Math.floor( r.y0/Fly.GRID ); gy <= Math.floor( ( r.y1 - 1 )/Fly.GRID ); ++gy )
      for ( var gx = Math.floor( r.x0/Fly.GRID ); gx <= Math.floor( ( r.x1 - 1 )/Fly.GRID ); ++gx )
         ( grid[gx + "," + gy] || [] ).forEach( function( i ) { if ( !seen[i] ) { seen[i] = true; out.push( i ); } } );
   return out;
};

/*
 * How far a detection's centre may be from its Gaia position: 1.5 px, or
 * 0.45 of its core's half-width when that is more. A saturated core's
 * centroid is uncertain by pixels (the 40 brightest stars of a real image
 * sat 1-4 px from their Gaia stars, and none moved at 1.5 px).
 */
Fly.matchRadius = function( det )
{
   return Math.max( Fly.MATCH_RADIUS, 0.45*( det.rect.x1 - det.rect.x0 )/2 );
};

Fly.MATCH_SEARCH = 6;     // px: the largest radius any detection may have

/*
 * Every (placed, detection) pair within the detection's match radius,
 * brightest Gaia star first, then nearest: the brightest star in reach is
 * the one that made a big detection, and it claims it before a faint one.
 */
Fly.matchPairs = function( dets, placed, grid )
{
   var pairs = [], R = Fly.MATCH_SEARCH;
   placed.forEach( function( p, pi )
   {
      var box = { x0: Math.floor( p.x - R ), y0: Math.floor( p.y - R ), x1: Math.ceil( p.x + R ) + 1, y1: Math.ceil( p.y + R ) + 1 };
      Fly.detectionsIn( grid, box ).forEach( function( di )
      {
         var dx = dets[di].x - p.x, dy = dets[di].y - p.y, r2 = dx*dx + dy*dy, mr = Fly.matchRadius( dets[di] );
         if ( r2 <= mr*mr ) pairs.push( { pi: pi, di: di, r2: r2, G: ( p.source && isFinite( p.source.G ) ) ? p.source.G : 99 } );
      } );
   } );
   return pairs.sort( function( a, b ) { return ( a.G - b.G ) || ( a.r2 - b.r2 ); } );
};

/* The detection's rect grown by `grow` px, clipped to the image. */
Fly.grownRect = function( r, grow, w, h )
{
   return { x0: Math.max( 0, r.x0 - grow ), y0: Math.max( 0, r.y0 - grow ),
            x1: Math.min( w, r.x1 + grow ), y1: Math.min( h, r.y1 + grow ) };
};

/*
 * A detection's core from its area: a square of half-width sqrt(area/pi),
 * at least one pixel, x1/y1 exclusive. PixInsight 1.9's StarDetector gives
 * no rectangle of its own.
 */
Fly.detectionRect = function( x, y, area )
{
   var r = Math.max( 1, Math.round( Math.sqrt( Math.max( 0, area )/Math.PI ) ) );
   return { x0: Math.floor( x - r ), y0: Math.floor( y - r ), x1: Math.floor( x + r ) + 1, y1: Math.floor( y + r ) + 1 };
};

Fly.BLEND_COMPANION_MAG = 0.3;   // a catalogue companion this close in magnitude (or brighter) makes a blend

/*
 * A second catalogue source inside the detection's core, no more than
 * BLEND_COMPANION_MAG fainter than the placed star (or brighter): a double the
 * detector merged, of which this is not the brighter star.
 * `neighbours` are projected catalogue sources ({source, x, y}).
 */
Fly.catalogueBlend = function( det, placed, neighbours )
{
   // a catalogue neighbour makes this star the blend only if it is about as bright or brighter
   var limit = ( placed.source && isFinite( placed.source.G ) ) ? placed.source.G + Fly.BLEND_COMPANION_MAG : Infinity;
   return ( neighbours || [] ).some( function( n )
   {
      return n.source !== placed.source && n.x >= det.rect.x0 && n.x < det.rect.x1 &&
             n.y >= det.rect.y0 && n.y < det.rect.y1 && !( n.source && n.source.G > limit );
   } );
};

Fly.HALO_FRACTION = 0.0005;  // a ring below this fraction of the peak is no longer the star
Fly.BLEND_FLUX_RATIO = 0.25; // a neighbour at least this bright relative to the star is a blend

/*
 * The half-width that holds a star's halo: the first ring radius, from
 * `minR`, whose value (profile[r], the ring's median in the stars layer)
 * is at or below max( noise, HALO_FRACTION x peak ). What lies beyond is
 * under the noise or under 0.05% of the core, so nothing visible is left
 * behind when the star moves. Measured: core + 1 FWHM left up to 62% of a
 * Moffat star's light behind.
 */
Fly.haloRadius = function( profile, peak, noise, minR )
{
   var limit = Math.max( noise, Fly.HALO_FRACTION*peak );
   for ( var r = minR; r < profile.length; ++r )
      if ( profile[r] <= limit )
         return r;
   return profile.length;
};

/* A square footprint of half-width r around (x, y), clipped; x1/y1 exclusive. */
Fly.squareRect = function( x, y, r, w, h )
{
   return { x0: Math.max( 0, Math.floor( x - r ) ), y0: Math.max( 0, Math.floor( y - r ) ),
            x1: Math.min( w, Math.floor( x + r ) + 1 ), y1: Math.min( h, Math.floor( y + r ) + 1 ) };
};

/*
 * A blend: several maxima, or another detection's core inside the
 * footprint that is at least BLEND_FLUX_RATIO as bright. Fainter ones stay
 * behind in the backdrop (their cores are never owned by the sprite).
 * Without fluxes, any neighbour counts.
 */
Fly.isBlend = function( dets, di, rect, grid )
{
   if ( dets[di].nmax > 1 )
      return true;
   var f = dets[di].flux;
   return Fly.detectionsIn( grid, rect ).some( function( j )
   {
      if ( j == di || !Fly.rectsIntersect( rect, dets[j].rect ) )
         return false;
      var g = dets[j].flux;
      // only a neighbour at least as bright makes this star the blend: of a
      // pair the brighter one moves and carries the fainter (a saturated
      // star with a Gaia companion in its glow stayed still as an equal pair)
      return !( isFinite( f ) && isFinite( g ) ) || g >= f;
   } );
};

/*
 * Which placed stars become sprites, and which pixels each owns.
 *
 * `dets` are detections in the stars layer ({x, y, nmax, rect{x0,y0,x1,y1}},
 * x1/y1 exclusive); `placed` from Fly.placeStars. Each detection goes to
 * the nearest placed star within 1.5 px. The footprint is the detection's
 * rect grown by `grow` (one FWHM). Blends stay in the backdrop; the
 * optional `neighbours` (projected catalogue sources) catch doubles the
 * detector merged into one. Where two
 * footprints overlap, the pixels go to the first sprite: `owner[y*w + x]`
 * is a sprite index or -1 (the residual), so sprites and residual are
 * disjoint by construction.
 */
/*
 * owner[y*w + x]: the sprite owning each pixel, or -1 (the residual).
 * Every other detection's core is reserved for the residual; each sprite
 * then claims its own core, and footprints are filled brightest first, so
 * a bright star's halo is not cut by a fainter neighbour's.
 */
/*
 * One star, one sprite: brightest first, a candidate whose centre lies
 * within an accepted brighter star's reach is dropped -- it moves with that
 * star. A bright star's spikes cross faint Gaia stars; as sprites of their
 * own they each took a piece of the spike and flew off at their own
 * distance, tearing the bright star apart near the camera (real render).
 */
Fly.oneStarOneSprite = function( sprites )
{
   var order = sprites.map( function( s, i ) { return i; } )
                      .sort( function( a, b ) { return ( sprites[b].det.flux || 0 ) - ( sprites[a].det.flux || 0 ) || a - b; } );
   var accepted = [];
   order.forEach( function( i )
   {
      var s = sprites[i];
      if ( !accepted.some( function( a ) { return Fly.withinReach( a.det.halo ? a.det : Object.assign( {}, a.det, { rect: a.rect } ), s.det ); } ) )
         accepted.push( s );
   } );
   return sprites.filter( function( s ) { return accepted.indexOf( s ) >= 0; } );
};

Fly.spriteOwnership = function( dets, sprites, w, h, haloMode )
{
   var owner = new Int32Array( w*h ), RESERVED = -2;
   owner.fill( -1 );
   function fill( r, value, when )
   {
      for ( var y = Math.max( 0, r.y0 ); y < Math.min( h, r.y1 ); ++y )
         for ( var x = Math.max( 0, r.x0 ); x < Math.min( w, r.x1 ); ++x )
            if ( when( owner[y*w + x], x, y ) ) owner[y*w + x] = value;
   }
   var own = sprites.map( function( s ) { return s.det; } );
   // without halo footprints, other stars' cores stay behind; with them, a
   // star moves everything in its reach (a hole tore the star when magnified)
   if ( !haloMode )
      dets.forEach( function( d ) { if ( own.indexOf( d ) < 0 ) fill( d.rect, RESERVED, function() { return true; } ); } );
   sprites.forEach( function( s, k ) { fill( s.det.rect, k, function( o ) { return o != RESERVED; } ); } );
   sprites.map( function( s, k ) { return k; } )
          .sort( function( a, b ) { return ( sprites[b].det.flux || 0 ) - ( sprites[a].det.flux || 0 ) || a - b; } )
          .forEach( function( k )
          {
             var sp = sprites[k], H = sp.det.halo;
             var outer = H ? H.outer : ( sp.rect.x1 - sp.rect.x0 )/2;
             fill( sp.rect, k, haloMode ? function( o, x, y ) { return o == -1 && Math.hypot( x - sp.det.x, y - sp.det.y ) < outer; }
                                        : function( o ) { return o == -1; } );
          } );
   for ( var i = 0; i < owner.length; ++i ) if ( owner[i] == RESERVED ) owner[i] = -1;
   return owner;
};

Fly.assignSprites = function( dets, placed, w, h, grow, neighbours, haloOf )
{
   var grid = Fly.detectionGrid( dets ), usedP = {}, usedD = {}, sprites = [], blended = 0;
   Fly.matchPairs( dets, placed, grid ).forEach( function( m )
   {
      if ( usedP[m.pi] || usedD[m.di] )
         return;
      usedP[m.pi] = usedD[m.di] = true;
      var rect = haloOf ? Fly.squareRect( dets[m.di].x, dets[m.di].y, haloOf( dets[m.di] ), w, h )
                        : Fly.grownRect( dets[m.di].rect, grow, w, h );
      // blends are judged near the core (one FWHM), not across the whole halo:
      // a neighbour in the halo just stays behind (its core is reserved)
      var core = Fly.grownRect( dets[m.di].rect, grow, w, h );
      if ( Fly.isBlend( dets, m.di, core, grid ) || Fly.catalogueBlend( dets[m.di], placed[m.pi], neighbours ) )
      {
         ++blended;
         return;
      }
      sprites.push( { placed: placed[m.pi], det: dets[m.di], rect: rect } );
   } );
   var absorbed = 0;
   if ( haloOf )
   {
      var kept = Fly.oneStarOneSprite( Fly.dropInsideBrighter( sprites, dets, grid, haloOf ) );
      absorbed = sprites.length - kept.length;
      sprites = kept;
   }
   var owner = Fly.spriteOwnership( dets, sprites, w, h, !!haloOf );
   var matched = Object.keys( usedP ).length;
   return { sprites: sprites, owner: owner, blended: blended, absorbed: absorbed, unmatched: placed.length - matched };
};

Fly.VIDEO_FORMATS = [
   { id: "h264",   label: "MP4 \u00b7 H.264",          ext: "mp4",  encoder: "libx264" },
   { id: "hevc",   label: "MP4 \u00b7 H.265/HEVC",     ext: "mp4",  encoder: "libx265" },
   { id: "prores", label: "MOV \u00b7 ProRes 422 HQ",  ext: "mov",  encoder: "prores_ks" },
   { id: "vp9",    label: "WebM \u00b7 VP9",           ext: "webm", encoder: "libvpx-vp9" } ];

/* The formats this ffmpeg can encode; H.264 is SDR only. */
Fly.availableFormats = function( encodersText, hdr )
{
   return Fly.VIDEO_FORMATS.filter( function( f )
   {
      if ( hdr && f.id == "h264" ) return false;
      return new RegExp( "\\b" + f.encoder.replace( /-/g, "\\-" ) + "\\b" ).test( encodersText );
   } );
};

Fly.defaultFormat = function( formats, width )
{
   var ids = formats.map( function( f ) { return f.id; } );
   if ( width > 1920 && ids.indexOf( "hevc" ) >= 0 ) return "hevc";
   if ( ids.indexOf( "h264" ) >= 0 ) return "h264";
   return ids.length ? ids[0] : null;
};

/*
 * Tag the frames BT.709 and convert RGB to YUV with the BT.709 matrix.
 * ffmpeg 9 takes the stream's colour tags from the frames: output flags
 * alone left the primaries "unknown" (probed 2026-09-23). ffmpeg 9 then
 * converts with the tagged matrix by itself (a colour-bar round trip gives
 * 0.43/255 either way); the explicit scale makes older builds, whose
 * default is BT.601, convert the same way.
 */
Fly.REC709_FILTER = "scale=out_color_matrix=bt709:out_range=tv," +
                    "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv";

/* The BT.2020 equivalent of REC709_FILTER, for a transfer "pq" | "hlg". */
Fly.hdrFilter = function( transfer )
{
   var trc = ( transfer == "pq" ) ? "smpte2084" : "arib-std-b67";
   return "scale=out_color_matrix=bt2020:out_range=tv," +
          "setparams=color_primaries=bt2020:color_trc=" + trc + ":colorspace=bt2020nc:range=tv";
};

/* HDR10 static metadata for x265: BT.2020 primaries, D65, 0.0001 nits to the peak. */
Fly.x265Hdr10 = function( hdr )
{
   return "hdr10=1:repeat-headers=1:master-display=G(8500,39850)B(6550,2300)R(35400,14600)WP(15635,16450)L(" +
          Math.round( ( hdr.peak || Fly.HDR_PEAK_DEFAULT )*10000 ) + ",1):max-cll=" +
          Math.round( hdr.maxCll || 0 ) + "," + Math.round( hdr.maxFall || 0 );
};

Fly.codecArgs = function( formatId, hi, hdr )
{
   var ten = hdr ? "yuv420p10le" : "yuv420p";
   var hevc = [ "-c:v", "libx265", "-crf", hi ? "20" : "26", "-tag:v", "hvc1", "-pix_fmt", ten ];
   if ( hdr && hdr.transfer == "pq" )
      hevc = hevc.concat( [ "-x265-params", Fly.x265Hdr10( hdr ) ] );
   return {
      h264:   [ "-c:v", "libx264", "-crf", hi ? "18" : "23", "-pix_fmt", "yuv420p" ],
      hevc:   hevc,
      prores: [ "-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le" ],
      vp9:    [ "-c:v", "libvpx-vp9" ].concat( hdr ? [ "-profile:v", "2" ] : [] )
                 .concat( [ "-crf", hi ? "24" : "32", "-b:v", "0", "-pix_fmt", ten ] ) }[formatId];
};

/*
 * The ffmpeg command line for a folder of frames. `hdr` (optional) =
 * { transfer: "pq" | "hlg", peak, maxCll, maxFall }: 10-bit, BT.2020 tags,
 * and for PQ the HDR10 metadata; the frames already hold the HDR signal.
 */
Fly.ffmpegArgs = function( framesDir, fps, outBase, formatId, quality, hdr, audio )
{
   var f = Fly.VIDEO_FORMATS.filter( function( x ) { return x.id == formatId; } )[0];
   var tags = hdr ? [ "-colorspace", "bt2020nc", "-color_primaries", "bt2020",
                      "-color_trc", hdr.transfer == "pq" ? "smpte2084" : "arib-std-b67" ]
                  : [ "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709" ];
   var music = ( audio && audio.path ) ? Fly.audioArgs( audio, formatId ) : null;
   return [ "-y", "-framerate", String( fps ), "-i", framesDir + "/frame_%05d.tif" ]
      .concat( music ? music.input : [] )
      .concat( [ "-vf", hdr ? Fly.hdrFilter( hdr.transfer ) : Fly.REC709_FILTER ] )
      .concat( Fly.codecArgs( formatId, quality == "high", hdr ) )
      .concat( music ? music.output : [] )
      .concat( tags ).concat( [ outBase + "." + f.ext ] );
};

Fly.AUDIO_FADE = 2;        // s: the music's fade in and out, at most a quarter of the clip
Fly.AUDIO_LOOP_FADE = 1;   // s: a looping video's music crossfading end into beginning, as quick as the video's (CROSSFADE_SECONDS)

Fly.audioFade = function( duration )
{
   return Math.min( Fly.AUDIO_FADE, duration/4 );
};

/*
 * Music under the video (audio = { path, fade, duration, loop }): looped if
 * it is shorter, cut to the video (-shortest), faded in and out unless fade
 * is off -- or, for a looping video, crossfaded end into beginning
 * (Fly.loopAudio); AAC in MP4 and MOV, Opus in WebM.
 */
Fly.audioArgs = function( audio, formatId )
{
   var d = Fly.audioFade( audio.duration ), codec = formatId == "vp9" ? [ "-c:a", "libopus", "-b:a", "160k" ] : [ "-c:a", "aac", "-b:a", "192k" ];
   if ( audio.loop ) return { input: [ "-stream_loop", "-1", "-i", audio.path ], output: Fly.loopAudio( audio.duration, Math.min( Fly.AUDIO_LOOP_FADE, audio.duration/4 ) ).concat( codec ).concat( [ "-shortest" ] ) };
   var out = [ "-map", "0:v", "-map", "1:a" ];
   if ( audio.fade && d > 0 )
      out = out.concat( [ "-af", "afade=t=in:st=0:d=" + d + ",afade=t=out:st=" + ( audio.duration - d ) + ":d=" + d ] );
   out = out.concat( codec ).concat( [ "-shortest" ] );
   return { input: [ "-stream_loop", "-1", "-i", audio.path ], output: out };
};

/*
 * A looping video's music, D seconds, looping too: no fade in or out; its
 * first x seconds fade in under the music's next x seconds (D to D + x)
 * fading out, so the end runs on into the beginning.
 */
Fly.loopAudio = function( D, x )
{
   var graph = "[1:a]atrim=0:" + ( D + x ) + ",asetpts=PTS-STARTPTS,asplit=2[a][b];" +
               "[a]atrim=0:" + D + ",asetpts=PTS-STARTPTS,afade=t=in:st=0:d=" + x + "[head];" +
               "[b]atrim=" + D + ":" + ( D + x ) + ",asetpts=PTS-STARTPTS,afade=t=out:st=0:d=" + x + "[tail];" +
               "[head][tail]amix=inputs=2:duration=first:normalize=0[aout]";
   return [ "-filter_complex", graph, "-map", "0:v", "-map", "[aout]" ];
};

/* What a failed solve tried -- the focal length, the pixel sizes (full, drizzled 2x and 3x), the centre -- and the solver's reason. */
Fly.solveFailure = function( hints, reasons )
{
   var sizes = [ 1, 2, 3 ].map( function( k ) { return ( hints.pixel/k ).toFixed( 2 ) + " \u00b5m"; } ).join( ", " );
   return "The image could not be solved: tried " + hints.focal + " mm with pixels of " + sizes + " around RA " + Number( hints.ra ).toFixed( 4 ) +
          "\u00b0, Dec " + Number( hints.dec ).toFixed( 4 ) + "\u00b0. Check the focal length and pixel size" +
          ( reasons.length ? ". The solver said: " + reasons[0] : "." );
};

Fly.SCALE_TOLERANCE = 0.1;   // a solved scale this close to one the rig can give is believed

/* Is a solved scale (arcsec/px) one the rig can give -- its own (expected) or half or a third of it (drizzled)? */
Fly.plausibleScale = function( solved, expected )
{
   return [ 1, 2, 3 ].some( function( k ) { return Math.abs( solved*k/expected - 1 ) < Fly.SCALE_TOLERANCE; } );
};

/* The information panel's star counts: detected, moving, in the background, and blends (which stay still). */
Fly.describeStars = function( c )
{
   return "Stars: " + c.detected + " detected \u00b7 " + c.placed + " moving \u00b7 " + c.backdrop + " in the background" +
          ( c.blended > 0 ? " \u00b7 " + c.blended + " blended (stay still)" : "" );
};

Fly.FILE_NAME_SCORE = 0.85;   // a file name's words must match an object this well (file names carry other words)

/*
 * The object an image's file name names, or null: its words (extension,
 * folders and separators dropped), in runs of three, two and one, through
 * Fly.findObject; the best match at or above FILE_NAME_SCORE.
 */
Fly.objectFromFileName = function( path, entries )
{
   var base = String( path || "" ).replace( /^.*[\/\\]/, "" ).replace( /\.[^.]*$/, "" );
   var words = base.split( /[^A-Za-z0-9']+/ ).filter( function( w ) { return w.length; } ), best = null;
   for ( var k = Math.min( 3, words.length ); k >= 1; --k )
      for ( var i = 0; i + k <= words.length; ++i )
      {
         var hit = Fly.findObject( words.slice( i, i + k ).join( " " ), entries )[0];
         if ( hit && hit.score >= Fly.FILE_NAME_SCORE && ( !best || hit.score > best.score ) ) best = hit;
      }
   return best;
};

Fly.CACHE_FORMAT = 3;   // bump when what the cache holds changes shape

/* An 8-hex-digit FNV-1a hash of a string: a folder-safe name. */
Fly.hashKey = function( text )
{
   var h = 0x811c9dc5, s = String( text );
   for ( var i = 0; i < s.length; ++i ) { h ^= s.charCodeAt( i ); h = Math.imul( h, 0x01000193 ) >>> 0; }
   return ( "0000000" + h.toString( 16 ) ).slice( -8 );
};

/*
 * An image's cache key: its file (path, size, time) or, never saved, its
 * view's name; its size; the Loom version and the cache's format -- so a
 * file saved again, or a new Loom, starts afresh. No pixels are read.
 */
Fly.imageCacheKey = function( path, bytes, mtime, w, h, nc, version, viewId )
{
   return Fly.hashKey( [ path ? path + "|" + bytes + "|" + mtime : "view:" + viewId, w, h, nc, version, Fly.CACHE_FORMAT ].join( "|" ) );
};

/* The cached images to delete (names) so only the `keep` most recently used remain; entries are { name, used }. */
Fly.cacheToPrune = function( entries, keep )
{
   return entries.slice().sort( function( a, b ) { return b.used - a.used; } ).slice( keep ).map( function( e ) { return e.name; } );
};

Fly.LOGO_PLACES = [ "center", "top-left", "top-mid", "top-right", "bottom-left", "bottom-mid", "bottom-right" ];
Fly.LOGO_SIZE = 0.2;      // the logo's long side, of the frame's short side
Fly.LOGO_MARGIN = 0.04;   // its margin from the edges (title-safe), of the short side

/* How much of the logo shows `seconds` in, with a fade-in `delay` (s): all at 0; hidden until the delay, then in over a second. */
Fly.logoFade = function( seconds, delay )
{
   return ( delay > 0 ) ? Fly.smoothstep( delay, delay + 1, seconds ) : 1;
};

/* Where a logo (lw x lh) goes in a W x H frame at `place`; null for "off". */
Fly.logoRect = function( W, H, lw, lh, place )
{
   if ( Fly.LOGO_PLACES.indexOf( place ) < 0 ) return null;
   var sh = Math.min( W, H ), k = Math.round( Fly.LOGO_SIZE*sh )/Math.max( lw, lh ), m = Math.round( Fly.LOGO_MARGIN*sh );
   var w = Math.round( lw*k ), h = Math.round( lh*k ), p = place.split( "-" );
   if ( place == "center" ) return { x: ( W - w )/2, y: ( H - h )/2, w: w, h: h };
   return { x: p[1] == "left" ? m : p[1] == "right" ? W - m - w : ( W - w )/2,
            y: p[0] == "top" ? m : H - m - h, w: w, h: h };
};

Fly.ffmpegCandidates = function( platform, home, pathVar, saved )
{
   var win = ( platform == "windows" ), out = [], hasHome = !!( home && String( home ).length );
   function add( p ) { if ( p && out.indexOf( p ) < 0 ) out.push( p ); }
   add( saved );
   if ( win )
   {
      if ( hasHome ) add( home + "/AppData/Local/Microsoft/WinGet/Links/ffmpeg.exe" );
      add( "C:/ProgramData/chocolatey/bin/ffmpeg.exe" );
      if ( hasHome ) add( home + "/scoop/shims/ffmpeg.exe" );
      add( "C:/ffmpeg/bin/ffmpeg.exe" );
      add( "C:/Program Files/ffmpeg/bin/ffmpeg.exe" );
   }
   else
      [ "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin" ].forEach( function( d ) { add( d + "/ffmpeg" ); } );
   String( pathVar || "" ).split( win ? ";" : ":" ).forEach( function( d )
   {
      d = d.trim().replace( /^"|"$/g, "" );
      if ( win ) d = d.replace( /\\/g, "/" );
      d = d.replace( /\/+$/, "" );
      if ( d.length ) add( d + ( win ? "/ffmpeg.exe" : "/ffmpeg" ) );
   } );
   return out;
};

Fly.ffmpegInstallHint = function( platform )
{
   if ( platform == "windows" ) return "install ffmpeg, e.g. winget install Gyan.FFmpeg";
   if ( platform == "macos" )   return "install ffmpeg, e.g. brew install ffmpeg";
   return "install ffmpeg with your package manager";
};

Fly.commandLine = function( program, args, platform )
{
   var win = ( platform == "windows" );
   return [ program ].concat( args ).map( function( a )
   {
      a = String( a );
      if ( /^[A-Za-z0-9_\-.,:\/=%+]+$/.test( a ) ) return a;
      return win ? "\"" + a.replace( /"/g, "\\\"" ) + "\""
                 : "'" + a.replace( /'/g, "'\\''" ) + "'";
   } ).join( " " );
};

/* A preset key (or a {id, w, h, pingPong} spec, for the suite) as a spec. */
/*
 * A preset's frame and loop. Vertical turns the widescreen ones portrait,
 * in a folder of their own; the social presets keep their shape either
 * way. `loop` (none, pingpong, crossfade) applies to any preset.
 */
Fly.presetSpec = function( p, orientation, loop )
{
   if ( typeof p != "string" )
      return p;
   // the loop is every preset's choice (none, pingpong, crossfade); the Exhibition always loops
   var size = Fly.PRESETS[p], turn = ( orientation == "vertical" && !/^social_/.test( p ) && size[0] > size[1] );
   var loops = ( p == "exhibition" ) || ( loop == "pingpong" || loop == "crossfade" );
   return { id: turn ? p + "_vertical" : p, preset: p, w: size[turn ? 1 : 0], h: size[turn ? 0 : 1],
            pingPong: loops && loop != "crossfade", crossfade: loops && loop == "crossfade" };
};

/* The flight time between two frames (0..1): n frames, a crossfade's F more, or there and back. */
Fly.frameStep = function( n, F, pingPong )
{
   var steps = pingPong ? n/2 : n - 1;             // a crossfade's flight is its clip (Fly.loopFrame)
   return steps > 0 ? 1/steps : 0;
};

/*
 * The renderer's version, in every frame signature (Fly.frameSignature):
 * bumped whenever the same options make different frames, so frames kept
 * from an older renderer are rendered again, not reused or resumed.
 * 2: HDR headroom as a bump on each star; crossfade loops dissolve at the end.
 * 3: the headroom bumps follow the backdrop's zoom.
 * 4: a crossfade's pre-roll holds the clock (twinkle, bloom, logo) at 0.
 * 5: vertical High/Medium drafts could be drawn from the unturned shrunk scene.
 * 6: the crossfade is 1 s.
 */
Fly.RENDERER_VERSION = 6;

Fly.ENCODE_ONLY = [ "format", "quality", "music", "video", "ffmpeg", "dir", "presets", "logoImage", "output" ];   // options that change only the encode

/*
 * What a preset's frames were made from, as a string: every option but the
 * encode's (Fly.ENCODE_ONLY), keys sorted, with the frame's spec and the
 * scene's key (image, star tool, distance). Equal signatures, same frames.
 */
Fly.frameSignature = function( opts, spec, sceneKey )
{
   var keep = {};
   Object.keys( opts ).sort().forEach( function( k ) { if ( Fly.ENCODE_ONLY.indexOf( k ) < 0 ) keep[k] = opts[k]; } );
   return JSON.stringify( { opts: keep, spec: { w: spec.w, h: spec.h, pingPong: !!spec.pingPong, crossfade: !!spec.crossfade }, scene: sceneKey, renderer: Fly.RENDERER_VERSION } );
};

Fly.CROSSFADE_SECONDS = 1;   // a crossfade loop's fade, at most a quarter of the clip (was 2: slow)

Fly.crossfadeFrames = function( duration, fps )
{
   return Math.max( 1, Math.round( Math.min( Fly.CROSSFADE_SECONDS, duration/4 )*fps ) );
};

/*
 * Frame i of an n-frame crossfade loop fading over F frames. It starts
 * clean: frame i shows the flight at time a = i/(n - 1), frame 0 its start
 * (the photograph) and frame n - 1 its end. Over the last F frames it
 * dissolves -- alpha*A + (1 - alpha)*B -- into the moments just before the
 * start, b = (i - n)/(n - 1) (smoothstep holds the camera at rest there;
 * linear keeps its pace), so the last frame runs on into frame 0 at the
 * same step. (It used to fade in over the first F frames, so the video
 * opened on a blend.)
 */
Fly.loopFrame = function( i, n, F )
{
   var step = n > 1 ? 1/( n - 1 ) : 0;
   if ( i < n - F ) return { a: i*step, b: null, alpha: 1 };
   return { a: i*step, b: ( i - n )*step, alpha: ( n - i )/( F + 1 ) };
};

/* Files this script writes into a preset folder, and nothing else. */
Fly.isFrameFile = function( name )
{
   return /^frame_\d{5}\.tif$/.test( name );
};

Fly.framePath = function( folder, i )
{
   return folder + "/frame_" + ( "0000" + i ).slice( -5 ) + ".tif";
};

/* ---------------------------------------------------------------------------
 * Colour management. Every standard working space is a matrix/tone-curve
 * ICC profile, so the conversion is done here from the profile's own tags:
 * nothing depends on which profiles a machine has installed.
 * ------------------------------------------------------------------------ */

Fly.mul3 = function( M, v )
{
   return [ M[0]*v[0] + M[1]*v[1] + M[2]*v[2], M[3]*v[0] + M[4]*v[1] + M[5]*v[2], M[6]*v[0] + M[7]*v[1] + M[8]*v[2] ];
};

Fly.matMul = function( A, B )
{
   var C = [];
   for ( var r = 0; r < 3; ++r )
      for ( var c = 0; c < 3; ++c )
         C.push( A[3*r]*B[c] + A[3*r + 1]*B[3 + c] + A[3*r + 2]*B[6 + c] );
   return C;
};

/* Bradford adaptation D50 -> D65 (ICC profiles' PCS is D50; video is D65). */
Fly.BRADFORD_D50_D65 = [ 0.9555766, -0.0230393, 0.0631636, -0.0282895, 1.0099416, 0.0210077, 0.0122982, -0.0204830, 1.3299098 ];
Fly.XYZ_TO_RGB = {
   rec709:  [ 3.2404542, -1.5371385, -0.4985314, -0.9692660, 1.8760108, 0.0415560, 0.0556434, -0.2040259, 1.0572252 ],
   rec2020: [ 1.7166512, -0.3556708, -0.2533663, -0.6666844, 1.6164812, 0.0157685, 0.0176399, -0.0427706, 0.9421031 ] };

/* The sRGB profile (D50-adapted primaries, IEC 61966-2-1 curve): the default. */
Fly.SRGB_PROFILE_NAME = "sRGB IEC61966-2.1";
Fly.SRGB_CURVE = { type: "para", fn: 3, p: [ 2.4, 1/1.055, 0.055/1.055, 1/12.92, 0.04045 ] };
Fly.SRGB_COLOUR = { matrix: [ 0.4360747, 0.3850649, 0.1430804, 0.2225045, 0.7168786, 0.0606169, 0.0139322, 0.0971045, 0.7141733 ],
                    trc: [ Fly.SRGB_CURVE, Fly.SRGB_CURVE, Fly.SRGB_CURVE ], name: "sRGB" };

/* An ICC tone curve at x in [0,1]: identity, gamma, table, or parametric types 0-4. */
Fly.trcDecode = function( c, x )
{
   x = Math.max( 0, Math.min( 1, x ) );
   if ( c.type == "gamma" ) return Math.pow( x, c.g );
   if ( c.type == "table" )
   {
      var t = c.t, f = x*( t.length - 1 ), i = Math.min( t.length - 2, Math.floor( f ) );
      return t[i] + ( f - i )*( t[i + 1] - t[i] );
   }
   if ( c.type != "para" ) return x;
   var p = c.p, g = p[0], a = p[1], b = p[2], cc = p[3], d = p[4], e = p[5], ff = p[6];
   switch ( c.fn )
   {
   case 0: return Math.pow( x, g );
   case 1: return x >= -b/a ? Math.pow( a*x + b, g ) : 0;
   case 2: return x >= -b/a ? Math.pow( a*x + b, g ) + cc : cc;
   case 3: return x >= d ? Math.pow( a*x + b, g ) : cc*x;
   case 4: return x >= d ? Math.pow( a*x + b, g ) + e : cc*x + ff;
   }
   return x;
};

/* One tone curve tag at `off`, or null. */
Fly.readCurve = function( byteAt, off, u32 )
{
   function str4( o ) { return String.fromCharCode( byteAt( o ), byteAt( o + 1 ), byteAt( o + 2 ), byteAt( o + 3 ) ); }
   function u16( o ) { return ( byteAt( o ) << 8 ) + byteAt( o + 1 ); }
   function s15( o ) { var v = u32( o ); return ( v > 0x7FFFFFFF ? v - 0x100000000 : v )/65536; }
   var type = str4( off );
   if ( type == "curv" )
   {
      var n = u32( off + 8 );
      if ( n == 0 ) return { type: "identity" };
      if ( n == 1 ) return { type: "gamma", g: u16( off + 12 )/256 };
      var t = [];
      for ( var i = 0; i < n; ++i ) t.push( u16( off + 12 + 2*i )/65535 );
      return { type: "table", t: t };
   }
   if ( type == "para" )
   {
      var fn = u16( off + 8 ), count = [ 1, 3, 4, 5, 7 ][fn], p = [];
      if ( count === undefined ) return null;
      for ( var k = 0; k < count; ++k ) p.push( s15( off + 12 + 4*k ) );
      return { type: "para", fn: fn, p: p };
   }
   return null;
};

/*
 * { matrix (RGB -> XYZ D50, row-major), trc: [r, g, b] } from an ICC
 * profile's rXYZ/gXYZ/bXYZ and rTRC/gTRC/bTRC tags; a gray profile's kTRC
 * gives { gray: true, trc: [k, k, k] }. Null for LUT-only profiles.
 */
Fly.parseIccColour = function( byteAt, length )
{
   function u32( o ) { return ( ( byteAt( o ) << 24 ) >>> 0 ) + ( byteAt( o + 1 ) << 16 ) + ( byteAt( o + 2 ) << 8 ) + byteAt( o + 3 ); }
   function s15( o ) { var v = u32( o ); return ( v > 0x7FFFFFFF ? v - 0x100000000 : v )/65536; }
   if ( length < 132 ) return null;
   var tags = {}, n = u32( 128 );
   for ( var i = 0; i < n && 144 + 12*i <= length; ++i )
   {
      var at = 132 + 12*i;
      tags[String.fromCharCode( byteAt( at ), byteAt( at + 1 ), byteAt( at + 2 ), byteAt( at + 3 ) )] = u32( at + 4 );
   }
   if ( tags.kTRC !== undefined )
   {
      var k = Fly.readCurve( byteAt, tags.kTRC, u32 );
      return k ? { gray: true, trc: [ k, k, k ], matrix: null } : null;
   }
   var need = [ "rXYZ", "gXYZ", "bXYZ", "rTRC", "gTRC", "bTRC" ];
   if ( need.some( function( t ) { return tags[t] === undefined || tags[t] + 12 > length; } ) )
      return null;
   var cols = [ "rXYZ", "gXYZ", "bXYZ" ].map( function( t ) { return [ s15( tags[t] + 8 ), s15( tags[t] + 12 ), s15( tags[t] + 16 ) ]; } );
   var trc = [ "rTRC", "gTRC", "bTRC" ].map( function( t ) { return Fly.readCurve( byteAt, tags[t], u32 ); } );
   if ( trc.some( function( c ) { return c == null; } ) ) return null;
   return { matrix: [ cols[0][0], cols[1][0], cols[2][0], cols[0][1], cols[1][1], cols[2][1], cols[0][2], cols[1][2], cols[2][2] ],
            trc: trc, gray: false };
};

/* Source linear RGB -> target linear RGB ("rec709" | "rec2020"). */
Fly.sourceToTarget = function( matrix, target )
{
   return Fly.matMul( Fly.XYZ_TO_RGB[target], Fly.matMul( Fly.BRADFORD_D50_D65, matrix ) );
};

Fly.srgbEncode = function( L )
{
   L = Math.max( 0, Math.min( 1, L ) );
   return L <= 0.0031308 ? 12.92*L : 1.055*Math.pow( L, 1/2.4 ) - 0.055;
};

Fly.SDR_WHITE_NITS = 203;          // ITU-R BT.2408 reference white
Fly.HDR_PEAK_DEFAULT = 1000;
Fly.HLG_REFERENCE_PEAK = 1000;      // HLG is relative to a 1000-nit display, system gamma 1.2
Fly.HDR_DEFAULT_TRANSFER = { social_vertical: "hlg", social_square: "hlg", youtube_4k: "hlg",
                             youtube_1080: "hlg", exhibition: "pq" };

/* SMPTE ST 2084 (PQ) inverse EOTF: absolute nits -> signal. */
Fly.pqEncode = function( nits )
{
   var m1 = 2610/16384, m2 = 2523/4096*128, c1 = 3424/4096, c2 = 2413/4096*32, c3 = 2392/4096*32;
   var L = Math.pow( Math.max( 0, Math.min( 10000, nits ) )/10000, m1 );
   return Math.pow( ( c1 + c2*L )/( 1 + c3*L ), m2 );
};

/* BT.2100 HLG OETF: scene-linear [0,1] -> signal. */
Fly.hlgEncode = function( E )
{
   var a = 0.17883277, b = 1 - 4*a, c = 0.5 - a*Math.log( 4*a );
   E = Math.max( 0, Math.min( 1, E ) );
   return E <= 1/12 ? Math.sqrt( 3*E ) : a*Math.log( 12*E - b ) + c;
};

/*
 * BT.2100 inverse OOTF for HLG (gamma 1.2): display-relative RGB (1 = the
 * 1000-nit reference peak), BT.2020, -> scene-linear RGB.
 */
Fly.hlgFromDisplay = function( rgb )
{
   var Y = 0.2627*rgb[0] + 0.6780*rgb[1] + 0.0593*rgb[2];
   if ( !( Y > 0 ) ) return [ 0, 0, 0 ];
   var k = Math.pow( Y, ( 1 - 1.2 )/1.2 );
   return [ rgb[0]*k, rgb[1]*k, rgb[2]*k ];
};

/* Highlights above white roll off towards P (in units of SDR white). */
Fly.rolloff = function( L, P )
{
   if ( L <= 1 ) return L;
   return 1 + ( P - 1 )*( 1 - Math.exp( -( L - 1 )/( P - 1 ) ) );
};

Fly.DECODE_LUT = 65536;

/* A table for any [0,1] -> [0,1] curve, read with linear interpolation. */
Fly.lutOf = function( fn )
{
   var n = Fly.DECODE_LUT, lut = new Float64Array( n + 1 );
   for ( var i = 0; i <= n; ++i ) lut[i] = fn( i/n );
   return function( x )
   {
      if ( !( x > 0 ) ) return lut[0];
      if ( x >= 1 ) return lut[n];
      var f = x*n, i = Math.floor( f );
      return lut[i] + ( f - i )*( lut[i + 1] - lut[i] );
   };
};

/* A decode table for one curve, read with linear interpolation. */
Fly.decodeLut = function( curve )
{
   return Fly.lutOf( function( x ) { return Fly.trcDecode( curve, x ); } );
};

/*
 * The per-frame output transform for a source colour description and a
 * mode ("sdr" here; HDR modes are added by Task 12c). pixel( r, g, b ) ->
 * [r, g, b] for tests; apply( R, G, B, n ) converts channel arrays in place.
 */
Fly.outputTransform = function( colour, mode, opts )
{
   colour = colour || Fly.SRGB_COLOUR;
   var dec = colour.trc.map( Fly.decodeLut );
   var M = colour.gray ? null : Fly.sourceToTarget( colour.matrix, "rec709" );
   var enc = Fly.lutOf( Fly.srgbEncode );   // pow per sample cost ~550 ms per 4K frame
   function one( r, g, b, out )
   {
      var lr = dec[0]( r ), lg = dec[1]( g ), lb = dec[2]( b );
      if ( M )
      {
         var x = M[0]*lr + M[1]*lg + M[2]*lb, y = M[3]*lr + M[4]*lg + M[5]*lb, z = M[6]*lr + M[7]*lg + M[8]*lb;
         lr = x; lg = y; lb = z;
      }
      out[0] = enc( lr ); out[1] = enc( lg ); out[2] = enc( lb );
      return out;
   }
   if ( mode == "pq" || mode == "hlg" )
      return Fly.hdrTransform( colour, dec, mode, opts || {} );
   return {
      mode: mode,
      pixel: function( r, g, b ) { return one( r, g, b, [ 0, 0, 0 ] ); },
      apply: function( R, G, B, n )
      {
         var o = [ 0, 0, 0 ];
         for ( var i = 0; i < n; ++i ) { one( R[i], G[i], B[i], o ); R[i] = o[0]; G[i] = o[1]; B[i] = o[2]; }
      },
      applyGray: function( K, n ) { for ( var i = 0; i < n; ++i ) K[i] = enc( dec[0]( K[i] ) ); }
   };
};

/*
 * The HDR output transform. Linear light in SDR-white units = the decoded
 * composite plus the star light that would have clipped (x, the unscreened
 * star sum above 1), in BT.2020; the brightest channel is rolled off to the
 * peak with the others scaled alike (hue kept); then PQ in absolute nits or
 * HLG through the inverse OOTF. PQ frames update stats.maxCll/maxFall (nits)
 * for the HDR10 metadata.
 */
Fly.hdrTransform = function( colour, dec, mode, opts )
{
   var M = colour.gray ? [ 1, 0, 0, 0, 1, 0, 0, 0, 1 ] : Fly.sourceToTarget( colour.matrix, "rec2020" );
   var peak = ( mode == "pq" ) ? ( opts.peak || Fly.HDR_PEAK_DEFAULT ) : Fly.HLG_REFERENCE_PEAK;
   var P = peak/Fly.SDR_WHITE_NITS, W = Fly.SDR_WHITE_NITS;
   var pq = Fly.lutOf( function( x ) { return Fly.pqEncode( x*peak ); } );   // x = nits / peak
   var stats = { maxCll: 0, maxFall: 0 };
   function one( r, g, b, xr, xg, xb, out )
   {
      var lr = dec[0]( r ) + xr, lg = dec[1]( g ) + xg, lb = dec[2]( b ) + xb;
      var v0 = Math.max( 0, M[0]*lr + M[1]*lg + M[2]*lb ), v1 = Math.max( 0, M[3]*lr + M[4]*lg + M[5]*lb ),
          v2 = Math.max( 0, M[6]*lr + M[7]*lg + M[8]*lb ), m = Math.max( v0, v1, v2 );
      if ( m > 1 ) { var k = Fly.rolloff( m, P )/m; v0 *= k; v1 *= k; v2 *= k; m *= k; }
      if ( mode == "pq" )
      {
         out[0] = pq( v0/P ); out[1] = pq( v1/P ); out[2] = pq( v2/P );
      }
      else
      {
         var s = Fly.hlgFromDisplay( [ v0*W/peak, v1*W/peak, v2*W/peak ] );
         out[0] = Fly.hlgEncode( s[0] ); out[1] = Fly.hlgEncode( s[1] ); out[2] = Fly.hlgEncode( s[2] );
      }
      return m*W;
   }
   function frame( n, each )
   {
      var sum = 0, o = [ 0, 0, 0 ];
      for ( var i = 0; i < n; ++i )
      {
         var nits = each( i, o );
         if ( nits > stats.maxCll ) stats.maxCll = nits;
         sum += nits;
      }
      if ( n > 0 && sum/n > stats.maxFall ) stats.maxFall = sum/n;
   }
   return {
      mode: mode, stats: stats,
      pixelHdr: function( r, g, b, xr, xg, xb ) { var o = [ 0, 0, 0 ]; one( r, g, b, xr, xg, xb, o ); return o; },
      pixel: function( r, g, b ) { var o = [ 0, 0, 0 ]; one( r, g, b, 0, 0, 0, o ); return o; },
      apply: function( R, G, B, n, XR, XG, XB )
      {
         frame( n, function( i, o )
         {
            var nits = one( R[i], G[i], B[i], XR ? XR[i] : 0, XG ? XG[i] : 0, XB ? XB[i] : 0, o );
            R[i] = o[0]; G[i] = o[1]; B[i] = o[2];
            return nits;
         } );
      },
      applyGray: function( K, n, XK )
      {
         frame( n, function( i, o )
         {
            var x = XK ? XK[i] : 0, nits = one( K[i], K[i], K[i], x, x, x, o );
            K[i] = o[1];
            return nits;
         } );
      }
   };
};

/* Frames in a played sequence: n forward, or there and back (ends once). */
Fly.sequenceLength = function( n, pingPong )
{
   return ( pingPong && n > 1 ) ? 2*n - 2 : n;
};

Fly.sequenceFrame = function( i, n, pingPong )
{
   var k = i % Fly.sequenceLength( n, pingPong );
   return k < n ? k : 2*n - 2 - k;
};

/*
 * An angle typed by a person: decimal degrees ("324.18"), or sexagesimal
 * ("21 36 42", "21:36:42", "-57 30 00") -- hours for RA, degrees for Dec.
 * Null when it is not an angle.
 */
Fly.parseAngle = function( text, isRA )
{
   var t = String( text ).trim();
   if ( /^[-+]?\d+(\.\d+)?$/.test( t ) )
      return parseFloat( t );
   var m = t.match( /^([-+]?)(\d+)[\s:]+(\d+)(?:[\s:]+(\d+(?:\.\d+)?))?$/ );
   if ( !m )
      return null;
   var v = parseInt( m[2], 10 ) + parseInt( m[3], 10 )/60 + ( m[4] ? parseFloat( m[4] ) : 0 )/3600;
   return ( m[1] == "-" ? -1 : 1 )*v*( isRA ? 15 : 1 );
};

/* Is pixel p inside a w x h image, clear of an outer margin (a fraction of each side)? */
Fly.insideImage = function( p, w, h, margin )
{
   return p != null && p.x >= margin*w && p.x <= ( 1 - margin )*w && p.y >= margin*h && p.y <= ( 1 - margin )*h;
};

/* ---------------------------------------------------------------------------
 * Progress: what the dialog's bar says.
 * ------------------------------------------------------------------------ */

/* "1 min 15 s", "42 s". */
Fly.formatElapsed = function( ms )
{
   var s = Math.round( ms/1000 ), m = Math.floor( s/60 );
   return m > 0 ? m + " min " + ( s - 60*m ) + " s" : s + " s";
};

/*
 * The bar's text: a stage with a count gets the count, the percentage and
 * the time left (extrapolated from the time so far); a stage with no count
 * -- a star removal tool that reports nothing -- gets the time so far.
 * `kept` of the done ones were already there, so they do not set the pace.
 */
Fly.progressText = function( stage, done, total, elapsedMs, kept )
{
   if ( !( total > 0 ) )
      return stage + " — " + Fly.formatElapsed( elapsedMs );
   var t = stage + " — " + done + " of " + total + " (" + Math.round( 100*done/total ) + "%)";
   // `kept` of the done were there before (a resumed render): the pace is the rest's
   var fresh = done - ( kept || 0 );
   if ( fresh > 0 && done < total )
   {
      var left = elapsedMs/fresh*( total - done );
      t += " — " + ( left < 60000 ? "less than a minute left" : "about " + Math.round( left/60000 ) + " min left" );
   }
   return t;
};

Fly.progressFraction = function( done, total )
{
   return total > 0 ? Math.max( 0, Math.min( 1, done/total ) ) : null;
};

/* The last "frame= N" ffmpeg printed on stderr, or null. */
Fly.ffmpegFrameProgress = function( text )
{
   var re = /frame=\s*(\d+)/g, m, last = null;
   while ( ( m = re.exec( String( text ) ) ) != null ) last = parseInt( m[1], 10 );
   return last;
};

Fly.WORKING_WIDTH = 3840;     // the largest preset: the working image serves it at 1:1
Fly.WORKING_HEIGHT = 2160;

/*
 * The factor the input is resampled by, once, before anything else: the
 * largest 16:9 rectangle that fits the image becomes 3840x2160. Never above
 * 1 -- a smaller image is used as it is.
 */
Fly.workingScale = function( w, h )
{
   // by the long side: a vertical image is turned for landscape presets (Fly.needsRotation)
   var L = Math.max( w, h ), S = Math.min( w, h ), aspect = Fly.WORKING_WIDTH/Fly.WORKING_HEIGHT;
   var cw = ( L/S >= aspect ) ? S*aspect : L;
   return Math.min( 1, Fly.WORKING_WIDTH/cw );
};

/*
 * A vertical image is never cut to a horizontal band (nor the reverse): a
 * preset whose orientation differs from the image's renders it turned 90
 * degrees. Square presets fit either way.
 */
Fly.needsRotation = function( imgW, imgH, outW, outH )
{
   if ( outW == outH || imgW == imgH ) return false;
   return ( imgW > imgH ) != ( outW > outH );
};

/*
 * Where a star's halo ends, and the background it stands on. `med[r]` is
 * the median of the ring at radius r (other stars' cores excluded). The
 * halo continues while the median is above the floor and still falling by
 * more than 3% of itself every two rings (a power-law wing does for
 * hundreds of pixels); a background level is flat. The median, not the
 * ring's brightest pixel: in a dense field that is nearly always some
 * faint star, and footprints grew to the cap (1.8x the image in total, on
 * a real render). Spikes are found separately, by Fly.spikeReach.
 */

Fly.SPIKE_WIDTH = 3;      // px either side of a spike's ray that count as on it

/*
 * Is detection s within bright star a's reach -- inside its halo, or lying
 * along one of its spikes? A circle as long as the longest spike would
 * carry off every faint star around a bright one.
 */
Fly.withinReach = function( a, s )
{
   var d = Math.hypot( s.x - a.x, s.y - a.y );
   if ( !a.halo ) return d < ( a.rect.x1 - a.rect.x0 )/2;
   if ( d < ( a.halo.halo != null ? a.halo.halo : a.halo.radius ) ) return true;
   var th = Math.atan2( s.y - a.y, s.x - a.x );
   return ( a.halo.spikes || [] ).some( function( sp )
   {
      var dth = Math.abs( Math.atan2( Math.sin( th - sp.angle ), Math.cos( th - sp.angle ) ) );
      return d <= sp.reach + Fly.SPIKE_WIDTH && d*Math.sin( Math.min( dth, Math.PI/2 ) ) <= Fly.SPIKE_WIDTH;
   } );
};

Fly.BRIGHT_G = 9.5;        // Gaia stars this bright are always given a detection
Fly.BRIGHT_MISS_RADIUS = 6; // px: no detection this close means the detector missed it

/*
 * The detector rejects the biggest saturated stars (the G 5.6 star of a
 * real field was not detected at all), and a bright star that is not
 * detected cannot move. Every Gaia star brighter than BRIGHT_G with no
 * detection within 6 px gets one at its Gaia position; `measure( x, y )`
 * returns its { flux, area } from the image. Returns a new list.
 */
Fly.addMissingBright = function( dets, neighbours, measure )
{
   var out = dets.slice();
   ( neighbours || [] ).forEach( function( n )
   {
      if ( !( n.source && n.source.G < Fly.BRIGHT_G ) ) return;
      var seen = out.some( function( d ) { return Math.abs( d.x - n.x ) < Fly.BRIGHT_MISS_RADIUS && Math.abs( d.y - n.y ) < Fly.BRIGHT_MISS_RADIUS &&
                                                  Math.hypot( d.x - n.x, d.y - n.y ) < Fly.BRIGHT_MISS_RADIUS; } );
      if ( seen ) return;
      var m = measure( n.x, n.y );
      out.push( { index: out.length, x: n.x, y: n.y, flux: m.flux, nmax: 0, rect: Fly.detectionRect( n.x, n.y, m.area ), fromGaia: true } );
   } );
   return out;
};

Fly.STILL_REACH_SEARCH = 150;   // px around a candidate to look for brighter stars whose reach it is in

/*
 * Candidates inside the reach of any clearly brighter star -- moving or
 * not -- are not sprites: nothing may carry off part of a star that stays
 * (a faint Gaia star in an undetected bright star's glow did).
 */
Fly.dropInsideBrighter = function( sprites, dets, grid, haloOf )
{
   var R = Fly.STILL_REACH_SEARCH;
   return sprites.filter( function( s )
   {
      var box = { x0: Math.floor( s.det.x - R ), y0: Math.floor( s.det.y - R ), x1: Math.ceil( s.det.x + R ), y1: Math.ceil( s.det.y + R ) };
      return !Fly.detectionsIn( grid, box ).some( function( j )
      {
         var d = dets[j];
         if ( d === s.det || !( d.flux > 2*( s.det.flux || 0 ) ) ) return false;
         if ( !d.halo ) haloOf( d );
         return Fly.withinReach( d, s.det );
      } );
   } );
};

Fly.LINEAR_SKY = 0.02;   // a sky median below this is a linear (unstretched) image

/* Does an image whose median (its sky) is `median` look linear? */
Fly.looksLinear = function( median )
{
   return median < Fly.LINEAR_SKY;
};

/* ---------------------------------------------------------------------------
 * Deblending. A star's light is not cut out with a footprint: every pixel of
 * the stars layer is SHARED among the stars around it in proportion to what
 * each one's model predicts there (as crowded-field photometry does). No
 * edges, no wedges, and a faint star in a bright star's glow keeps its own
 * share. Footprint thresholds tuned on one image broke on the next.
 * ------------------------------------------------------------------------ */

/*
 * Diffraction-spike directions (degrees) from an angular profile: prof[a]
 * is the light at angle a (1 degree bins) above what lies beside it,
 * summed over the brightest stars. A direction is a spike where the
 * profile peaks well above its noise. An image without spikes has none.
 */
Fly.spikeAngles = function( prof, noise )
{
   var n = prof.length, top = Math.max.apply( null, prof ), out = [];
   var thr = Math.max( 6*noise, 0.1*top );
   for ( var a = 0; a < n; ++a )
   {
      var v = prof[a], peak = v > thr;
      for ( var d = -3; d <= 3 && peak; ++d )
         if ( d != 0 && prof[( a + d + n ) % n] > v ) peak = false;
      if ( peak ) out.push( a );
   }
   // spikes are lines through the star: keep a direction only with its
   // opposite (a neighbouring star on one side read as a spike and was
   // carried off with the bright star)
   return out.filter( function( a ) { return out.some( function( b ) { var d = Math.abs( ( b - a + 3*n/2 ) % n - n/2 ); return Math.abs( d - n/2 ) <= 2 || d >= n/2 - 2; } ); } );
};

/*
 * Is (x, y) a local peak of value(x, y)? The brightest pixel of its 3x3
 * (a centroid need not fall on the peak pixel) is no fainter than any pixel
 * within rad. A point on a brighter star's slope or spike has brighter
 * pixels toward that star.
 */
Fly.localPeak = function( value, x, y, rad )
{
   var X = Math.round( x ), Y = Math.round( y ), v0 = -Infinity, R = Math.ceil( rad );
   for ( var j = -1; j <= 1; ++j ) for ( var i = -1; i <= 1; ++i ) v0 = Math.max( v0, value( X + i, Y + j ) );
   for ( j = -R; j <= R; ++j )
      for ( i = -R; i <= R; ++i )
         if ( ( Math.abs( i ) > 1 || Math.abs( j ) > 1 ) && i*i + j*j <= rad*rad && value( X + i, Y + j ) > v0 ) return false;
   return true;
};

/*
 * A detection that is part of a brighter star, not a star: it is no local
 * peak (within its FWHM), or it lies in a brighter detection's core (grown
 * by a FWHM) where that one is at least as bright -- a saturated core is
 * flat, so every point of it ties. Catalogue-added bright stars are stars.
 */
Fly.isFragment = function( d, dets, value, fwhm )
{
   if ( d.fromGaia ) return false;
   if ( !Fly.localPeak( value, d.x, d.y, Math.max( 2, fwhm ) ) ) return true;
   var X = Math.round( d.x ), Y = Math.round( d.y ), v0 = -Infinity;
   for ( var j = -1; j <= 1; ++j ) for ( var i = -1; i <= 1; ++i ) v0 = Math.max( v0, value( X + i, Y + j ) );
   return dets.some( function( b )
   {
      if ( b === d || !( b.flux > d.flux ) ) return false;
      var core = 0.5*( b.rect.x1 - b.rect.x0 ) + fwhm;
      return Math.hypot( b.x - d.x, b.y - d.y ) <= core && value( Math.round( b.x ), Math.round( b.y ) ) >= v0;
   } );
};

/*
 * Missed sources (the second pass) inside a moving star's core -- an
 * unresolved companion, or a pair the detector saw as one -- are part of
 * that star: each joins its model's parts. Returns the ones that stay.
 */
Fly.attachToCores = function( sprites, extras )
{
   return extras.filter( function( e )
   {
      var owner = null;
      sprites.forEach( function( s )
      {
         var m = s.det.model;
         if ( Math.hypot( e.x - m.x, e.y - m.y ) <= m.core && ( !owner || Math.hypot( e.x - m.x, e.y - m.y ) < Math.hypot( e.x - owner.x, e.y - owner.y ) ) ) owner = m;
      } );
      if ( owner ) ( owner.parts = owner.parts || [] ).push( e );
      return !owner;
   } );
};

Fly.SPIKE_WINDOW = 8;      // samples averaged either side along a spike for its detection threshold

/*
 * A spike's lateral sigma from its profile across it (p sampled every step
 * px, centred on the spike): the full width at half maximum above the
 * profile's own baseline (its outer two px on either side), over 2.3548.
 * null when there is no peak.
 */
Fly.lateralSigma = function( p, step )
{
   var n = p.length, mid = ( n - 1 )/2, edge = Math.max( 1, Math.round( 2/step ) ), base = 0;
   for ( var k = 0; k < edge; ++k ) base += p[k] + p[n - 1 - k];
   base /= 2*edge;
   var q = p.map( function( v ) { return v - base; } ), peak = Math.max.apply( null, q );
   if ( !( peak > 1e-9 ) ) return null;
   var half = function( dir )
   {
      for ( var k = Math.round( mid ); k + dir >= 0 && k + dir < n; k += dir )
         if ( q[k + dir] < peak/2 ) return ( Math.abs( k - mid ) + ( q[k] - peak/2 )/( q[k] - q[k + dir] ) )*step;
      return mid*step;
   };
   return ( half( -1 ) + half( 1 ) )/2.3548;
};

/*
 * A star lying on a brighter star's spike has no spike of its own along
 * that axis (either way): the light there is the brighter star's, and
 * owning it tore a stretch of spike off the bright star when the two moved
 * apart. Its support shrinks to what it has left. dets carry flux and
 * model ({x, y, rmax, spikeSigma, spikes: [{angle, start, reach}]}).
 */
Fly.dropBorrowedSpikes = function( dets )
{
   var parallel = function( a, b ) { var d = Math.abs( a - b ) % Math.PI; return Math.min( d, Math.PI - d ) < 2*Math.PI/180; };
   var spiky = dets.filter( function( d ) { return d.model && d.model.spikes.length; } );
   spiky.forEach( function( d )
   {
      var m = d.model, borrowed = [];
      spiky.forEach( function( b )
      {
         if ( b === d || !( b.flux > d.flux ) ) return;
         var bm = b.model, dx = m.x - bm.x, dy = m.y - bm.y, half = Math.max( 2, 3*( bm.spikeSigma || 1 ) );
         bm.spikes.forEach( function( sp )
         {
            var c = Math.cos( sp.angle ), s = Math.sin( sp.angle ), along = dx*c + dy*s;
            if ( along >= sp.start && along <= sp.reach && Math.abs( -dx*s + dy*c ) <= half ) borrowed.push( sp.angle );
         } );
      } );
      if ( !borrowed.length ) return;
      m.spikes = m.spikes.filter( function( sp ) { return !borrowed.some( function( a ) { return parallel( a, sp.angle ); } ); } );
      m.support = Math.max( m.rmax, m.spikes.reduce( function( r, sp ) { return Math.max( r, sp.reach ); }, 0 ) ) + 1;
      if ( d.halo ) { d.halo.radius = d.halo.outer = m.support; d.halo.spikes = m.spikes.map( function( sp ) { return { angle: sp.angle, reach: sp.reach }; } ); }
   } );
};

Fly.SPIKE_FIT = 20;   // inner spike samples a star's spike scale is fitted over

/*
 * One star's spike as part of the PSF. e[k] is its excess at r = start + k;
 * T[r] the image's spike falloff (per unit scale). The scale is the median
 * of e/T over the inner SPIKE_FIT samples, so light borrowed from a
 * neighbour there does not count, and nothing further out does at all; the
 * amplitude follows the falloff, and reaches as far as it stands above the
 * noise of a spike averaged over a 2*SPIKE_WINDOW + 1 sample window.
 */
Fly.fitSpike = function( e, T, start, sigma )
{
   var ratios = [];
   for ( var k = 0; k < Math.min( Fly.SPIKE_FIT, e.length ); ++k ) if ( T[start + k] > 0 ) ratios.push( e[k]/T[start + k] );
   var scale = ratios.length ? Fly.median( ratios ) : 0, n = 2*Fly.SPIKE_WINDOW + 1;
   var floor = 3*1.2247*sigma/Math.sqrt( n ), amp = [], reach = -1;
   // the fitted scale itself must stand out above its median's noise, measured from the ratios' own
   // scatter: dividing by a falloff that shrinks outward makes the outer ratios far noisier than sigma/T[start]
   var noise = ratios.length ? 1.2533*Fly.mad( ratios )/Math.sqrt( ratios.length ) : Infinity;
   if ( scale > 3*noise )
      for ( k = 0; start + k < T.length && scale*T[start + k] > floor; ++k ) { amp.push( scale*T[start + k] ); reach = k; }
   return { scale: scale, amp: amp, reach: reach };
};

/* An opposite pair of spikes: equal by nature, so both take the stronger side's fit (Fly.fitSpike). */
Fly.fitSpikePair = function( e1, e2, T, start, sigma )
{
   var a = Fly.fitSpike( e1, T, start, sigma ), b = Fly.fitSpike( e2, T, start, sigma );
   return b.scale > a.scale ? b : a;
};

Fly.SETTLE_WINDOW = 4;      // rings averaged on each side of the "has it stopped falling" test

/*
 * A star's radial profile on its local sky. raw[r] is the ring median above
 * the global sky; a local offset there (a gradient, a faint glow) never fades,
 * so a profile ending "where it reaches the noise" ran on to the cap. Instead
 * the profile ends where it stops falling: the mean of the next W rings is no
 * more than 3 sigma (of that difference) above the mean of the W after them.
 * The later window is the local sky; the profile is raw minus that, and
 * reaches zero at rmax. settled is false when raw ran out first.
 */
Fly.localProfile = function( raw, minR, noise )
{
   var W = Fly.SETTLE_WINDOW, mean = function( a, b ) { var s = 0; for ( var k = a; k < b; ++k ) s += raw[k]; return s/( b - a ); };
   for ( var r = Math.max( 1, minR ); r + 2*W <= raw.length; ++r )
   {
      var ringNoise = 1.2533*noise/Math.sqrt( 2*Math.PI*r ), limit = 3*ringNoise*Math.SQRT2/Math.sqrt( W );
      var near = mean( r, r + W ), far = mean( r + W, r + 2*W );
      if ( near - far <= limit )
      {
         var rmax = r + W, prof = [];
         for ( var k = 0; k <= rmax; ++k ) prof.push( k < rmax ? Math.max( 0, raw[k] - far ) : 0 );
         return { prof: prof, rmax: rmax, sky: far, settled: true };
      }
   }
   var sky = raw.length ? Math.min( 0, raw[raw.length - 1] ) : 0;
   return { prof: raw.map( function( v ) { return Math.max( 0, v - sky ); } ), rmax: raw.length, sky: sky, settled: false };
};

/*
 * The light a star takes at one pixel. Mi is its model there, Msum all the
 * models there, L the pixel's light (stars layer above the sky), sigma the
 * noise. Where the stars stand well above the noise, the star takes its
 * model's share of the data; where they have faded into it, only its model
 * -- so the noise stays where it is and the spot the star leaves looks like
 * the sky around it. The shares of all stars add up to the pixel's light
 * wherever the data is used -- up to twice what the models predict: light
 * far beyond that is something no model knows (a brighter star's spike past
 * its reach), and a faint star took it along as a bar.
 */
Fly.DEBLEND_CAP = 2;   // the most light the models at a pixel take, as a multiple of what they predict

Fly.deblend = function( Mi, Msum, L, sigma )
{
   if ( !( Msum > 0 ) || !( Mi > 0 ) ) return 0;
   var a = Fly.smoothstep( 1, 5, Msum/sigma );
   return a*( Mi/Msum )*Math.min( L, Fly.DEBLEND_CAP*Msum ) + ( 1 - a )*Mi;
};

/*
 * Gaia DR3 online, for when no configured catalogue answers: a cone search
 * at VizieR (CDS), which answers a field in seconds (ESA's own archive timed
 * out after 65 s on the Iris's 0.8 degree field). Positions are ICRS at
 * epoch 2016.0, as in the Gaia process's databases.
 */
Fly.GAIA_ONLINE_URL = "https://tapvizier.cds.unistra.fr/TAPVizieR/tap/sync";
Fly.GAIA_ONLINE_COLUMNS = [ "RA_ICRS", "DE_ICRS", "Plx", "pmRA", "pmDE", "Gmag", "BPmag", "RPmag" ];
Fly.GAIA_ONLINE_MAX_ROWS = 500000;

Fly.gaiaOnlineUrl = function( centre, radiusDeg, gMax )
{
   var q = "SELECT " + Fly.GAIA_ONLINE_COLUMNS.join( "," ) + " FROM \"I/355/gaiadr3\" WHERE Gmag < " + gMax +
           " AND 1=CONTAINS(POINT('ICRS',RA_ICRS,DE_ICRS),CIRCLE('ICRS'," + centre.ra + "," + centre.dec + "," + radiusDeg + "))";
   return Fly.GAIA_ONLINE_URL + "?REQUEST=doQuery&LANG=ADQL&FORMAT=csv&MAXREC=" + Fly.GAIA_ONLINE_MAX_ROWS + "&QUERY=" + encodeURIComponent( q );
};

/* The CSV answer as source records ({ ra, dec, plx, pmra, pmdec, G, BP, RP }; a blank is NaN), or null when it is not one. */
Fly.parseGaiaCsv = function( text )
{
   var lines = String( text || "" ).split( /\r?\n/ );
   if ( lines[0] != Fly.GAIA_ONLINE_COLUMNS.join( "," ) ) return null;
   var num = function( v ) { return v === "" ? NaN : Number( v ); }, out = [];
   for ( var i = 1; i < lines.length; ++i )
   {
      var f = lines[i].split( "," );
      if ( f.length != Fly.GAIA_ONLINE_COLUMNS.length ) continue;
      out.push( { ra: num( f[0] ), dec: num( f[1] ), plx: num( f[2] ), pmra: num( f[3] ), pmdec: num( f[4] ), G: num( f[5] ), BP: num( f[6] ), RP: num( f[7] ) } );
   }
   return out;
};
