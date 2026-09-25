/*
 * Loom Fly-Through, PixInsight side: catalogues, projection, star
 * extraction.
 */
var Sky = {};

Sky.NGC_IC_RELATIVE = "/scripts/AdP/NGC-IC.csv";

/* PixInsight's own NGC/IC table, from the core install; null when unreadable. */
Sky.readNgcIc = function()
{
   var p = CoreApplication.srcDirPath + Sky.NGC_IC_RELATIVE;
   try { return File.exists( p ) ? Fly.parseNgcIc( File.readTextFile( p ) ) : null; }
   catch ( e ) { return null; }
};

/*
 * Gaia's data releases as the Gaia process numbers them (its menu order,
 * probed 2026-09-24: on a machine with only DR3/SP installed, 3 answers
 * and 1 and 2 do not). The default resolved to DR3/SP, which holds only
 * stars with published spectra -- the Iris's lighting star HD 200775 is
 * not in it -- so full DR3 is asked first, then DR3/SP, then DR2.
 */
Sky.GAIA_DR2 = 0;
Sky.GAIA_EDR3 = 1;
Sky.GAIA_DR3 = 2;
Sky.GAIA_DR3SP = 3;
Sky.GAIA_RELEASES = [ Sky.GAIA_DR3, Sky.GAIA_DR3SP, Sky.GAIA_DR2 ];

/*
 * The Gaia stars around `centre`, from the first release installed in the
 * order Sky.GAIA_RELEASES (the databases configured in Process > Gaia).
 * Replaceable, so the suite can inject a source list.
 */
Sky.querySources = function( centre, radiusDeg )
{
   for ( var k = 0; k < Sky.GAIA_RELEASES.length; ++k )
   {
      var G = new Gaia;
      G.command = "search";
      G.dataRelease = Sky.GAIA_RELEASES[k];
      G.centerRA = centre.ra;
      G.centerDec = centre.dec;
      G.radius = radiusDeg;
      G.magnitudeHigh = 17.6;
      G.generateTextOutput = false;
      G.verbosity = 0;
      if ( !G.executeGlobal() )
         continue;
      return G.sources.map( function( s )
      {
         return { ra: s[0], dec: s[1], plx: s[2], pmra: s[3], pmdec: s[4], G: s[5], BP: s[6], RP: s[7] };
      } );
   }
   return [];
};

Sky.requireSources = function( centre, radiusDeg )
{
   var s = Sky.querySources( centre, radiusDeg );
   if ( !s || s.length == 0 )
      throw new Error( "No Gaia stars for this field: configure a Gaia DR3 database in Process > Gaia" );
   return s;
};

/*
 * The cluster finder's own sample: centred on the TARGET, out to three
 * inside radii. The comparison annulus usually lies outside the image, so
 * the image's own stars cannot supply it.
 */
Sky.clusterSources = function( target, targetRadiusDeg )
{
   return Sky.requireSources( target, 3*Fly.clusterRadius( targetRadiusDeg ) );
};

Sky.keywordNumber = function( window, name )
{
   var k = window.keywords.filter( function( x ) { return x.name == name; } )[0];
   if ( !k )
      return null;
   var v = parseFloat( String( k.value ).replace( /'/g, "" ) );
   return isFinite( v ) ? v : null;
};

/* TAN WCS from FITS keywords, or null when any of them is missing. */
Sky.keywordWcs = function( window )
{
   var n = function( k ) { return Sky.keywordNumber( window, k ); };
   var wcs = { crval1: n( "CRVAL1" ), crval2: n( "CRVAL2" ), crpix1: n( "CRPIX1" ), crpix2: n( "CRPIX2" ),
               cd: [ n( "CD1_1" ), n( "CD1_2" ), n( "CD2_1" ), n( "CD2_2" ) ] };
   var all = [ wcs.crval1, wcs.crval2, wcs.crpix1, wcs.crpix2 ].concat( wcs.cd );
   return all.some( function( v ) { return v == null; } ) ? null : wcs;
};

/*
 * ( ra, dec ) -> { x, y } in Fly-Through's pixel convention -- pixel i's
 * centre at i, as StarDetector's centroids and the frame sampling use --
 * or null when the image has no WCS. PixInsight's celestialToImage puts
 * pixel centres at i + 0.5: on a real image every Gaia star sat +0.5 px from
 * its detected centre in x and y until this was corrected (median offset
 * 0.55, 0.47 over the 300 brightest stars).
 */
Sky.projector = function( window )
{
   if ( window.hasAstrometricSolution )
      return function( ra, dec ) { var p = window.celestialToImage( ra, dec ); return p ? { x: p.x - 0.5, y: p.y - 0.5 } : null; };
   var wcs = Sky.keywordWcs( window );
   return wcs ? function( ra, dec ) { return Fly.tanProject( ra, dec, wcs ); } : null;
};

/* ( x, y ) -> { ra, dec }, or null when the image has no WCS. */
Sky.unprojector = function( window )
{
   if ( window.hasAstrometricSolution )
      return function( x, y ) { var p = window.imageToCelestial( x + 0.5, y + 0.5 ); return p ? { ra: p.x, dec: p.y } : null; };
   var wcs = Sky.keywordWcs( window );
   return wcs ? function( x, y ) { return Fly.tanUnproject( x, y, wcs ); } : null;
};

/* The image's centre and the radius reaching its farthest corner, degrees. */
Sky.field = function( window )
{
   var un = Sky.unprojector( window ), img = window.mainView.image, w = img.width, h = img.height;
   if ( un == null )
      return null;
   var centre = un( w/2, h/2 ), r = 0;
   [ [ 0, 0 ], [ w, 0 ], [ 0, h ], [ w, h ] ].forEach( function( c )
   {
      r = Math.max( r, Fly.separation( centre, un( c[0], c[1] ) ) );
   } );
   return { centre: centre, radiusDeg: r };
};

Sky.copyWindow = function( source, id )
{
   var img = source.mainView.image;
   // colour channels only: an RGBA export's alpha is not a colour
   var nc = img.isColor ? Math.min( 3, img.numberOfChannels ) : 1;
   var w = new ImageWindow( img.width, img.height, nc, 32, true, img.isColor, Util.freeWindowId( id ) );
   w.mainView.beginProcess( UndoFlag_NoSwapFile );
   if ( nc == img.numberOfChannels )
      w.mainView.image.assign( img );
   else
   {
      var b = new Float32Array( img.width*img.height ), all = new Rect( 0, 0, img.width, img.height );
      for ( var c = 0; c < nc; ++c ) { img.getSamples( b, all, c ); w.mainView.image.setSamples( b, all, c ); }
   }
   w.mainView.endProcess();
   return w;
};

/*
 * Starless S and stars T from ONE removal, so screen( S, T ) is the image
 * and frame 0 can be exact. S is first clamped to the image: where a tool
 * brightened a pixel, unscreen would clip T at 0 and screen could no longer
 * give the original back.
 */
Sky.splitStars = function( window, tool )
{
   var starless = Sky.copyWindow( window, "fly_starless" ), stars = null;
   try
   {
      Steps.removeStars( starless, tool, "fly-through", false /*stretched*/ );
      var pm = new PixelMath;
      pm.expression = "min( $T, " + window.mainView.id + " )";
      pm.useSingleExpression = true;
      pm.createNewImage = false;
      pm.rescale = false;
      pm.truncate = true;
      if ( !pm.executeOn( starless.mainView ) )
         throw new Error( "Could not clamp the starless image" );
      stars = Sky.copyWindow( window, "fly_stars" );
      Steps.deriveStarsByUnscreen( stars, starless );   // stars := unscreen( image, starless ), in place
      return { starless: starless, stars: stars };
   }
   catch ( e )
   {
      starless.forceClose();
      if ( stars ) stars.forceClose();
      throw e;
   }
};

/*
 * StarDetector's detections as plain records: {x, y, nmax, flux, rect}.
 * PixInsight 1.9's StarDetector returns position, flux and area only
 * (probed 2026-09-23), so the core rect comes from the area and nmax is 0
 * ("not measured"); blends are judged from the catalogue instead.
 */
Sky.detections = function( image )
{
   var found = ( new StarDetector ).stars( image ) || [];
   return found.map( function( s, i )
   {
      return { index: i, x: s.pos.x, y: s.pos.y, flux: s.flux, nmax: s.nmax || 0,
               rect: Fly.detectionRect( s.pos.x, s.pos.y, s.size || 0 ) };
   } );
};

Sky.FWHM_DEFAULT = 3;   // px, when the caller has no measurement
Sky.HALO_MAX = 150;     // px, the largest halo half-width measured
Sky.SPIKE_MAX = 600;    // px, the longest spike followed (spikes run far beyond halos)

/* The stars layer's intensity (mean of channels), for measuring halos. */
Sky.intensity = function( image )
{
   var n = image.width*image.height, out = new Float32Array( n ), b = new Float32Array( n ), nc = image.numberOfChannels;
   for ( var c = 0; c < nc; ++c )
   {
      image.getSamples( b, new Rect( 0, 0, image.width, image.height ), c );
      for ( var i = 0; i < n; ++i ) out[i] += b[i]/nc;
   }
   return out;
};

/* Robust noise of a buffer: 1.4826 x MAD over a sparse sample. */
Sky.noiseSigma = function( buf )
{
   var sample = [];
   for ( var i = 0; i < buf.length; i += 7 ) sample.push( buf[i] );
   return Fly.mad( sample ) == 1 ? 0 : Fly.mad( sample );
};

/* Median of the square ring of half-width r around (cx, cy); null off the image. */
Sky.ringMedian = function( buf, w, h, cx, cy, r )
{
   var x0 = Math.round( cx ) - r, x1 = Math.round( cx ) + r, y0 = Math.round( cy ) - r, y1 = Math.round( cy ) + r, v = [];
   function at( x, y ) { if ( x >= 0 && y >= 0 && x < w && y < h ) v.push( buf[y*w + x] ); }
   for ( var x = x0; x <= x1; ++x ) { at( x, y0 ); at( x, y1 ); }
   for ( var y = y0 + 1; y < y1; ++y ) { at( x0, y ); at( x1, y ); }
   return v.length ? Fly.median( v ) : null;
};

/*
 * Median and brightest pixel of the round ring of radius r (|d - r| < 0.5)
 * around (cx, cy), skipping pixels that belong to another detection's
 * core (labels[i] >= 0 and != self). Null when the ring is off the image.
 * Each row visits only the ring's own span, so a ring costs O(r).
 */
Sky.ringStats = function( buf, w, h, cx, cy, r, labels, self )
{
   var v = [], top = -Infinity, lo = ( r - 0.5 )*( r - 0.5 ), hi = ( r + 0.5 )*( r + 0.5 );
   var X = Math.round( cx ), Y = Math.round( cy );
   function take( x, y )
   {
      if ( x < 0 || x >= w ) return;
      var i = y*w + x;
      if ( labels && labels[i] >= 0 && labels[i] != self ) return;
      v.push( buf[i] );
      if ( buf[i] > top ) top = buf[i];
   }
   for ( var dy = -r - 1; dy <= r + 1; ++dy )
   {
      var y = Y + dy;
      if ( y < 0 || y >= h ) continue;
      var outer = hi - dy*dy;
      if ( outer <= 0 ) continue;
      var a = ( lo - dy*dy > 0 ) ? Math.ceil( Math.sqrt( lo - dy*dy ) ) : 0, b = Math.ceil( Math.sqrt( outer ) ) - 1;
      for ( var dx = a; dx <= b; ++dx )
      {
         var d2 = dx*dx + dy*dy;
         if ( d2 < lo || d2 >= hi ) continue;
         take( X + dx, y );
         if ( dx != 0 ) take( X - dx, y );
      }
   }
   return v.length ? { med: Fly.median( v ), max: top } : null;
};

/* Every detection's core (grown by one FWHM), labelled with its index. */
Sky.coreLabels = function( dets, w, h, fwhm )
{
   var labels = new Int32Array( w*h );
   labels.fill( -1 );
   dets.forEach( function( d, k )
   {
      var r = Fly.grownRect( d.rect, fwhm, w, h );
      for ( var y = r.y0; y < r.y1; ++y )
         for ( var x = r.x0; x < r.x1; ++x )
            if ( labels[y*w + x] < 0 ) labels[y*w + x] = k;
   } );
   return labels;
};


/*
 * The sprites: every star's light, deblended.
 *
 * Every detection gets a model -- its own radial profile (ring median,
 * other stars excluded, above the sky), its colour, and its diffraction
 * spikes along the image's spike directions (each measured against the
 * pixels beside it, so a neighbour's glow does not count). Every pixel of
 * the stars layer is then shared among the models there (Fly.deblend), and
 * each moving star takes its share: the data where stars stand above the
 * noise, its model where they fade into it. Sprites plus residual are the
 * stars layer exactly, so frame 0 stays the image.
 */
Sky.sprites = function( starsImage, placed, fwhm, neighbours, stage )
{
   stage = stage || function() {};
   stage( "Detecting stars", 0, 0 );
   var w = starsImage.width, h = starsImage.height, nc = starsImage.numberOfChannels;
   var px = Math.max( 1, Math.round( fwhm || Sky.FWHM_DEFAULT ) );
   var I = Sky.intensity( starsImage ), T = Render.channels( starsImage );
   var dets = Fly.addMissingBright( Sky.detections( starsImage ), neighbours, function( x, y ) { return Sky.brightMeasure( I, w, h, x, y ); } );
   var valueI = Sky.reader( I, w, h );
   // a detection on a brighter star's core, slope or spike is part of it (Fly.isFragment)
   dets = dets.filter( function( d ) { return !Fly.isFragment( d, dets, valueI, px ); } );
   dets.forEach( function( d, k ) { d.index = k; } );
   var labels = Sky.coreLabels( dets, w, h, px );
   var env = { I: I, T: T, w: w, h: h, nc: nc, fwhm: px, labels: labels, noise: Sky.noiseSigma( I ),
               sky: T.map( Sky.sparseMedian ), skyI: Sky.sparseMedian( I ) };
   env.spikes = Sky.spikeDirections( env, dets );
   env.spikeSigma = Sky.spikeWidth( env, dets, env.spikes );
   env.spikeFalloff = Sky.spikeFalloff( env, dets, env.spikes );
   var modelled = 0;
   var halo = function( d )
   {
      if ( !d.model ) { d.model = Sky.starModel( env, d ); if ( ++modelled % 200 == 0 ) stage( "Modelling stars", modelled, dets.length ); }
      return d.model.support;
   };
   stage( "Modelling stars", 0, 0 );
   dets.forEach( function( d ) { halo( d ); } );
   Fly.dropBorrowedSpikes( dets );
   var a = Fly.assignSprites( dets, placed, w, h, px, neighbours, halo );
   var sums = Sky.modelSums( env, dets );
   // a second pass, as crowded-field photometry does: compact light the
   // models do not explain (a faint star the detector missed on a bright
   // glow) gets a model of its own, so the glow's star does not take it;
   // one inside a moving star's core is part of that star (Fly.attachToCores)
   stage( "Looking for stars the detector missed", 0, 0 );
   var extra = Sky.unexplainedSources( env, a.sprites, sums );
   if ( extra.length ) Sky.addToSums( env, extra, sums );
   Fly.attachToCores( a.sprites, extra );
   var residual = T.map( function( b ) { return b.slice(); } );
   var sprites = a.sprites.map( function( s, k )
   {
      if ( k % 50 == 0 ) stage( "Deblending moving stars", k, a.sprites.length );
      return Sky.deblendSprite( env, s, sums, residual );
   } );
   var residualMask = new Uint8Array( w*h );
   residualMask.fill( 1 );
   return { sprites: sprites, residual: residual, residualMask: residualMask, blended: a.blended, unmatched: a.unmatched, detected: dets.length };
};

/* A pixel reader for buf (w x h), clamped at the edges. */
Sky.reader = function( buf, w, h )
{
   return function( x, y ) { return buf[Math.max( 0, Math.min( h - 1, y ) )*w + Math.max( 0, Math.min( w - 1, x ) )]; };
};

/*
 * The stars layer's sky: the median of a sparse sample, iterated with
 * everything more than 3 sigma above it dropped, so big glows do not pull
 * it up (a plain median read a bright star's glow as sky, and the star
 * left part of its light behind).
 */
Sky.sparseMedian = function( buf )
{
   var v = [];
   for ( var i = 0; i < buf.length; i += 7 ) v.push( buf[i] );
   var m = Fly.median( v );
   for ( var it = 0; it < 5; ++it )
   {
      var sig = Fly.mad( v )/1.4826 || 0, kept = v.filter( function( x ) { return x <= m + 3*sig; } );
      if ( kept.length == v.length || kept.length < 10 ) break;
      v = kept;
      m = Fly.median( v );
   }
   return m;
};

Sky.bilinear = function( buf, w, h, x, y )
{
   var x0 = Math.floor( x ), y0 = Math.floor( y ), fx = x - x0, fy = y - y0, s = 0, wt = 0;
   for ( var j = 0; j < 2; ++j ) for ( var i = 0; i < 2; ++i )
   {
      var xx = x0 + i, yy = y0 + j;
      if ( xx < 0 || yy < 0 || xx >= w || yy >= h ) continue;
      var k = ( i ? fx : 1 - fx )*( j ? fy : 1 - fy );
      s += k*buf[yy*w + xx]; wt += k;
   }
   return wt > 0 ? s/wt : 0;
};

Sky.SPIKE_SIDE = 3;          // px either side of a spike where its surroundings are read
Sky.SPIKE_SIGMA = 1.0;       // px: a spike's half-width (Gaussian sigma) when it cannot be measured
Sky.SPIKE_STARS = 12;        // the brightest stars read for the image's spike directions
Sky.SPIKE_LEVER = 100;       // px out along a spike its direction is refined over

/* Light on the line at angle ang, distance r from (cx, cy), above the pixels beside it. */
Sky.spikeExcess = function( buf, w, h, cx, cy, ang, r, side )
{
   var c = Math.cos( ang ), s = Math.sin( ang ), x = cx + r*c, y = cy + r*s, S = side || Sky.SPIKE_SIDE;
   var on = Sky.bilinear( buf, w, h, x, y );
   var side = 0.5*( Sky.bilinear( buf, w, h, x - S*s, y + S*c ) + Sky.bilinear( buf, w, h, x + S*s, y - S*c ) );
   return on - side;
};

/*
 * The image's spike directions (radians): the brightest stars' light by
 * angle, above what lies beside it, over a band of radii outside their
 * cores (Fly.spikeAngles). None for an image without spikes.
 */
Sky.spikeDirections = function( env, dets )
{
   var top = dets.slice().sort( function( a, b ) { return b.flux - a.flux; } ).slice( 0, Sky.SPIKE_STARS );
   var prof = [];
   for ( var a = 0; a < 360; ++a ) prof.push( 0 );
   top.forEach( function( d )
   {
      var core = Math.max( d.rect.x1 - d.x, d.x - d.rect.x0 ) + env.fwhm, norm = Math.max( 1e-6, d.flux );
      for ( var a = 0; a < 360; ++a )
      {
         var ang = a*Math.PI/180, sum = 0;
         for ( var r = core + 2; r < core + 22; ++r ) sum += Sky.spikeExcess( env.I, env.w, env.h, d.x, d.y, ang, r );
         prof[a] += sum/norm;
      }
   } );
   var noise = Fly.mad( prof )/1.4826;
   // whole degrees over a short band are up to half a degree off, which
   // puts a model followed 100 px out beside its spike: each direction is
   // refined to 0.05 degrees on the same stars over a long lever arm
   var along = function( deg )
   {
      var ang = deg*Math.PI/180, sum = 0;
      top.forEach( function( d )
      {
         var core = Math.max( d.rect.x1 - d.x, d.x - d.rect.x0 ) + env.fwhm, part = 0;
         for ( var r = core + 2; r < core + Sky.SPIKE_LEVER; ++r ) part += Sky.spikeExcess( env.I, env.w, env.h, d.x, d.y, ang, r );
         sum += part/Math.max( 1e-6, d.flux );
      } );
      return sum;
   };
   return Fly.spikeAngles( prof, noise ).map( function( a )
   {
      var best = a, bestScore = -Infinity;
      for ( var t = a - 1; t <= a + 1 + 1e-9; t += 0.05 ) { var sc = along( t ); if ( sc > bestScore ) { bestScore = sc; best = t; } }
      return best*Math.PI/180;
   } );
};

/* Where a spike's surroundings are read: clear of its flanks. */
Sky.spikeSide = function( sigma )
{
   return Math.max( Sky.SPIKE_SIDE, 3*( sigma || Sky.SPIKE_SIGMA ) );
};

/*
 * The image's spike width (Gaussian sigma, px): the brightest stars' light
 * across their spikes, every half pixel, over the spikes' lever arm
 * (Fly.lateralSigma). A model narrower than the spikes left their flanks
 * behind.
 */
Sky.spikeWidth = function( env, dets, angles )
{
   if ( !angles.length ) return Sky.SPIKE_SIGMA;
   var top = dets.slice().sort( function( a, b ) { return b.flux - a.flux; } ).slice( 0, Sky.SPIKE_STARS ), p = [];
   for ( var l = -8; l <= 8 + 1e-9; l += 0.5 ) p.push( 0 );
   angles.forEach( function( ang )
   {
      var c = Math.cos( ang ), s = Math.sin( ang );
      top.forEach( function( d )
      {
         var core = Math.max( d.rect.x1 - d.x, d.x - d.rect.x0 ) + env.fwhm, norm = Math.max( 1e-6, d.flux );
         for ( var r = core + 2; r < core + Sky.SPIKE_LEVER; ++r )
            for ( var k = 0; k < p.length; ++k )
            {
               var lat = -8 + 0.5*k;
               p[k] += Sky.bilinear( env.I, env.w, env.h, d.x + r*c - lat*s, d.y + r*s + lat*c )/norm;
            }
      } );
   } );
   var sigma = Fly.lateralSigma( p, 0.5 );
   return sigma ? Math.max( 0.5, Math.min( 4, sigma ) ) : Sky.SPIKE_SIGMA;
};

/*
 * The image's spike falloff T[r], r = 0 .. SPIKE_MAX: the brightest stars'
 * spike excess per unit flux, averaged over their spikes (outside each
 * one's core), smoothed and never rising outward. Every star's spikes are
 * this, scaled (Fly.fitSpike).
 */
Sky.spikeFalloff = function( env, dets, angles )
{
   var T = [], n = [], top = dets.slice().sort( function( a, b ) { return b.flux - a.flux; } ).slice( 0, Sky.SPIKE_STARS ), side = Sky.spikeSide( env.spikeSigma );
   for ( var r = 0; r < Sky.SPIKE_MAX; ++r ) { T.push( 0 ); n.push( 0 ); }
   angles.forEach( function( ang )
   {
      top.forEach( function( d )
      {
         var core = Math.ceil( Math.max( d.rect.x1 - d.x, d.x - d.rect.x0 ) ) + env.fwhm, norm = Math.max( 1e-6, d.flux );
         for ( var r = core + 2; r < Sky.SPIKE_MAX; ++r ) { T[r] += Sky.spikeExcess( env.I, env.w, env.h, d.x, d.y, ang, r, side )/norm; ++n[r]; }
      } );
   } );
   var first = -1;
   for ( r = 0; r < T.length; ++r ) { if ( n[r] ) { T[r] /= n[r]; if ( first < 0 ) first = r; } }
   if ( first < 0 ) return T;
   var W = Fly.SPIKE_WINDOW, out = [];
   for ( r = 0; r < T.length; ++r )
   {
      if ( r < first ) { out.push( 0 ); continue; }
      var s = 0, m = 0;
      for ( var k = Math.max( first, r - W ); k <= Math.min( T.length - 1, r + W ); ++k ) { s += T[k]; ++m; }
      out.push( Math.max( 0, s/m ) );
   }
   for ( r = first + 1; r < out.length; ++r ) out[r] = Math.min( out[r], out[r - 1] );
   for ( r = 0; r < first; ++r ) out[r] = out[first];
   return out;
};

/*
 * One star's model: its radial profile (ring medians, other stars excluded,
 * never rising outward) on its local sky, out to where it stops falling
 * (Fly.localProfile), its colour, and its spikes' amplitudes along the
 * image's spike directions. d.halo is set for the reach tests of Fly.assignSprites.
 */
Sky.starModel = function( env, d )
{
   var minR = Math.ceil( Math.max( d.rect.x1 - d.x, d.x - d.rect.x0 ) ) + env.fwhm;
   var lp = Sky.starProfile( env, d, minR ), spikes = Sky.starSpikes( env, d, minR ), col = Sky.starColour( env, d, minR );
   var support = Math.max( lp.rmax, spikes.reduce( function( m, sp ) { return Math.max( m, sp.reach ); }, 0 ) ) + 1;
   d.halo = { radius: support, halo: lp.rmax, pedestal: 0, feather: 0, outer: support,
              spikes: spikes.map( function( sp ) { return { angle: sp.angle, reach: sp.reach }; } ) };
   return { x: d.x, y: d.y, core: minR, spikeSigma: env.spikeSigma, prof: Sky.smoothProfile( lp.prof, minR ), rmax: lp.rmax,
            spikes: spikes, col: col, support: support };
};

/* A star's ring medians outward, other stars excluded, until its profile stops falling (Fly.localProfile). */
Sky.starProfile = function( env, d, minR )
{
   var self = ( d.index != null ) ? d.index : -1, raw = [], lp = null;
   for ( var r = 0; r < Sky.HALO_MAX; ++r )
   {
      var st = ( r == 0 ) ? { med: Sky.bilinear( env.I, env.w, env.h, d.x, d.y ) } : Sky.ringStats( env.I, env.w, env.h, d.x, d.y, r, env.labels, self );
      raw.push( ( st ? st.med : env.skyI ) - env.skyI );
      if ( r >= minR + 2*Fly.SETTLE_WINDOW && ( lp = Fly.localProfile( raw, minR, env.noise ) ).settled ) return lp;
   }
   return Fly.localProfile( raw, minR, env.noise );
};

/* A star's spikes along the image's directions, each fitted with its opposite partner (Fly.fitSpikePair). */
Sky.starSpikes = function( env, d, minR )
{
   var excess = function( a ) { var e = []; for ( var k = 0; k < Fly.SPIKE_FIT; ++k ) e.push( Sky.spikeExcess( env.I, env.w, env.h, d.x, d.y, a, minR + k, Sky.spikeSide( env.spikeSigma ) ) ); return e; };
   var spikes = [];
   env.spikes.forEach( function( ang )
   {
      // spikes come in opposite pairs of equal strength: fit each with its partner
      var partner = env.spikes.filter( function( b ) { return Math.abs( Math.abs( b - ang ) - Math.PI ) < 2*Math.PI/180; } )[0];
      var sp = ( partner != null ) ? Fly.fitSpikePair( excess( ang ), excess( partner ), env.spikeFalloff, minR, env.noise )
                                   : Fly.fitSpike( excess( ang ), env.spikeFalloff, minR, env.noise );
      if ( sp.reach > 0 ) spikes.push( { angle: ang, start: minR, amp: sp.amp, reach: minR + sp.reach } );
   } );
   return spikes;
};

/* A star's colour: each channel's light over its core against the intensity's. */
Sky.starColour = function( env, d, minR )
{
   var col = [], sI = 0, c;
   for ( c = 0; c < env.nc; ++c ) col.push( 0 );
   for ( var y = Math.max( 0, Math.floor( d.y - minR ) ); y <= Math.min( env.h - 1, Math.ceil( d.y + minR ) ); ++y )
      for ( var x = Math.max( 0, Math.floor( d.x - minR ) ); x <= Math.min( env.w - 1, Math.ceil( d.x + minR ) ); ++x )
      {
         if ( Math.hypot( x - d.x, y - d.y ) > minR ) continue;
         var i = y*env.w + x;
         sI += env.I[i] - env.skyI;
         for ( c = 0; c < env.nc; ++c ) col[c] += env.T[c][i] - env.sky[c];
      }
   return col.map( function( v ) { return sI > 0 ? Math.max( 0, v/sI ) : 1; } );
};

/*
 * A radial profile made smooth and never rising outward. Beyond the core,
 * each ring is averaged with its neighbours first: a running minimum of
 * noisy ring medians follows the noise down, the model came out low, and
 * the star left part of its light behind (0.66 sigma, measured).
 */
Sky.smoothProfile = function( prof, from )
{
   var out = prof.slice();
   for ( var r = from; r < prof.length; ++r )
   {
      var sum = 0, n = 0;
      for ( var k = Math.max( from, r - 2 ); k <= Math.min( prof.length - 1, r + 2 ); ++k ) { sum += prof[k]; ++n; }
      out[r] = sum/n;
   }
   for ( r = 1; r < out.length; ++r ) out[r] = Math.min( out[r], out[r - 1] );
   return out;
};

/* A model's value (intensity) at pixel (x, y). */
Sky.modelAt = function( m, x, y )
{
   return Sky.radialAt( m, x, y ) + Sky.spikeAt( m, x, y );
};

/* A model's round part at pixel (x, y): its radial profile. */
Sky.radialAt = function( m, x, y )
{
   var r = Math.hypot( x - m.x, y - m.y );
   if ( !( r < m.rmax ) ) return 0;
   var r0 = Math.floor( r ), f = r - r0;
   return m.prof[r0] + f*( ( r0 + 1 < m.prof.length ? m.prof[r0 + 1] : 0 ) - m.prof[r0] );
};

/* A model's spikes at pixel (x, y). */
Sky.spikeAt = function( m, x, y )
{
   var dx = x - m.x, dy = y - m.y, v = 0, sg = m.spikeSigma || Sky.SPIKE_SIGMA;
   for ( var k = 0; k < m.spikes.length; ++k )
   {
      var sp = m.spikes[k], c = Math.cos( sp.angle ), s = Math.sin( sp.angle );
      var along = dx*c + dy*s, lat = -dx*s + dy*c;
      if ( along < sp.start || along > sp.reach || Math.abs( lat ) > 3*sg ) continue;
      var t = along - sp.start, t0 = Math.floor( t ), g = t - t0;
      var a = sp.amp[t0] + g*( ( t0 + 1 < sp.amp.length ? sp.amp[t0 + 1] : 0 ) - sp.amp[t0] );
      v += a*Math.exp( -lat*lat/( 2*sg*sg ) );
   }
   return v;
};

/* Every model added up, per channel (model x colour), over the whole image. */
Sky.modelSums = function( env, dets )
{
   var sums = [];
   for ( var c = 0; c < env.nc; ++c ) sums.push( new Float32Array( env.w*env.h ) );
   dets.forEach( function( d )
   {
      var m = d.model, R = m.support;
      for ( var y = Math.max( 0, Math.floor( m.y - R ) ); y <= Math.min( env.h - 1, Math.ceil( m.y + R ) ); ++y )
         for ( var x = Math.max( 0, Math.floor( m.x - R ) ); x <= Math.min( env.w - 1, Math.ceil( m.x + R ) ); ++x )
         {
            var v = Sky.modelAt( m, x, y );
            if ( v <= 0 ) continue;
            for ( var c = 0; c < env.nc; ++c ) sums[c][y*env.w + x] += v*m.col[c];
         }
   } );
   return sums;
};

Sky.MISSED_SIGMA = 5;       // an unexplained peak this far above the noise is a star
Sky.MISSED_SHARE = 0.5;     // ...and at least half the modelled light there (the models' own shape errors stay below)

/*
 * Compact light the models do not explain, inside the moving stars' reach:
 * local maxima of (stars layer - sky - all models), well above the noise
 * and a real share of the modelled light, that are peaks of the image too
 * (a model's shortfall along a spike is not a star). Each becomes a point model (a
 * Gaussian of the image's FWHM with that peak), which never moves.
 */
Sky.unexplainedSources = function( env, sprites, sums )
{
   var w = env.w, h = env.h, found = [], seen = {}, valueI = Sky.reader( env.I, w, h );
   function sumI( i ) { var t = 0; for ( var c = 0; c < env.nc; ++c ) t += sums[c][i]; return t/env.nc; }
   function excess( x, y ) { var i = y*w + x; return env.I[i] - env.skyI - sumI( i ); }
   sprites.forEach( function( s )
   {
      var m = s.det.model, R = m.support;
      for ( var y = Math.max( 1, Math.floor( m.y - R ) ); y <= Math.min( h - 2, Math.ceil( m.y + R ) ); ++y )
         for ( var x = Math.max( 1, Math.floor( m.x - R ) ); x <= Math.min( w - 2, Math.ceil( m.x + R ) ); ++x )
         {
            var e = excess( x, y ), i = y*w + x;
            if ( seen[i] || e < Sky.MISSED_SIGMA*env.noise || e < Sky.MISSED_SHARE*sumI( i ) ) continue;
            if ( !Sky.excessPeak( excess, x, y, e ) || !Fly.localPeak( valueI, x, y, Math.max( 2, env.fwhm ) ) ) continue;   // not a bead on a spike or slope
            seen[i] = true;
            found.push( Sky.pointModel( env, sums, x, y, e ) );
         }
   } );
   return found;
};

/* Is (x, y), excess e, the highest of its 3 x 3 neighbours in excess(x, y)? */
Sky.excessPeak = function( excess, x, y, e )
{
   for ( var j = -1; j <= 1; ++j )
      for ( var k = -1; k <= 1; ++k )
         if ( ( j || k ) && excess( x + k, y + j ) > e ) return false;
   return true;
};

/* A point source's model at (x, y) with peak e: a Gaussian of the image's FWHM, its colour what the models there leave. */
Sky.pointModel = function( env, sums, x, y, e )
{
   var i = y*env.w + x, sig = env.fwhm/2.3548, col = [], rr = Math.ceil( 3*sig ), prof = [];
   for ( var c = 0; c < env.nc; ++c ) col.push( env.I[i] - env.skyI > 0 ? Math.max( 0, ( env.T[c][i] - env.sky[c] - sums[c][i] )/e ) : 1 );
   for ( var r = 0; r <= rr; ++r ) prof.push( e*Math.exp( -r*r/( 2*sig*sig ) ) );
   return { x: x, y: y, prof: prof, rmax: rr, spikes: [], col: col, support: rr + 1 };
};

/* Adds models to the per-channel sums. */
Sky.addToSums = function( env, models, sums )
{
   Sky.modelSums( env, models.map( function( m ) { return { model: m }; } ) ).forEach( function( add, c )
   {
      for ( var i = 0; i < add.length; ++i ) if ( add[i] ) sums[c][i] += add[i];
   } );
};

/* One moving star's deblended light, taken out of `residual` (per channel). */
Sky.deblendSprite = function( env, s, sums, residual )
{
   var m = s.det.model, R = m.support, w = env.w;
   var r = { x0: Math.max( 0, Math.floor( m.x - R ) ), y0: Math.max( 0, Math.floor( m.y - R ) ),
             x1: Math.min( w, Math.ceil( m.x + R ) + 1 ), y1: Math.min( env.h, Math.ceil( m.y + R ) + 1 ) };
   var rw = r.x1 - r.x0, rh = r.y1 - r.y0, mask = new Uint8Array( rw*rh ), pixels = [], core = [], spikes = [];
   for ( var c = 0; c < env.nc; ++c ) { pixels.push( new Float32Array( rw*rh ) ); core.push( new Float32Array( rw*rh ) ); spikes.push( new Float32Array( rw*rh ) ); }
   for ( var y = r.y0; y < r.y1; ++y )
      for ( var x = r.x0; x < r.x1; ++x )
      {
         var mi = Sky.modelAt( m, x, y );
         if ( mi <= 0 ) continue;
         var i = y*w + x, o = ( y - r.y0 )*rw + x - r.x0;
         for ( c = 0; c < env.nc; ++c )
         {
            var mc = mi*m.col[c];
            for ( var k = 0; k < ( m.parts || [] ).length; ++k ) mc += Sky.modelAt( m.parts[k], x, y )*m.parts[k].col[c];
            var v = Fly.deblend( mc, sums[c][i], env.T[c][i] - env.sky[c], env.noise );
            pixels[c][o] = v;
            // the star as its model, drawn up close (Fly.modelWeight): its round part and its spikes apart,
            // so a close star's boosted core stays round
            var sk = Sky.spikeAt( m, x, y )*m.col[c];
            spikes[c][o] = sk;
            core[c][o] = mc - sk;
            residual[c][i] -= v;
         }
         mask[o] = 1;
      }
   return { source: s.placed.source, d: s.placed.d, placed: s.placed, det: s.det, rect: r, radius: R,
            centre: { x: s.det.x, y: s.det.y }, mask: mask, pixels: pixels, modelCore: core, modelSpikes: spikes,
            spikeAngles: m.spikes.map( function( k ) { return k.angle; } ) };
};

/*
 * Flux and core area of a star the detector missed, around (x, y): light
 * within 8 px, and the pixels within 30 px at half its peak or more (the
 * saturated core).
 */
Sky.brightMeasure = function( buf, w, h, x, y )
{
   var cx = Math.round( x ), cy = Math.round( y ), flux = 0, peak = 0, area = 0;
   Sky.eachInWindow( w, h, cx, cy, 30, function( i, dx, dy ) { if ( buf[i] > peak ) peak = buf[i]; if ( dx*dx + dy*dy <= 64 ) flux += buf[i]; } );
   Sky.eachInWindow( w, h, cx, cy, 30, function( i ) { if ( buf[i] >= 0.5*peak ) ++area; } );
   return { flux: flux, area: area };
};

/* fn( index, dx, dy ) for every pixel of the (2R + 1)-square window about (cx, cy) that is in the w x h image. */
Sky.eachInWindow = function( w, h, cx, cy, R, fn )
{
   for ( var yy = Math.max( 0, cy - R ); yy <= Math.min( h - 1, cy + R ); ++yy )
      for ( var xx = Math.max( 0, cx - R ); xx <= Math.min( w - 1, cx + R ); ++xx )
         fn( yy*w + xx, xx - cx, yy - cy );
};

/*
 * Solves an image with no astrometric solution (typically a TIFF) from the
 * user's hints: centre (degrees) and focal length (mm) with pixel size
 * (um). The user's own ImageSolver configuration is used otherwise, as
 * Steps.solve does; the hints only replace the metadata it lacks.
 */
Sky.solveOnce = function( window, hints )
{
   var engine = new ImageSolver;
   engine.initialize( window, false /*prioritizeSettings*/ );
   var m = engine.metadata;
   m.ra = hints.ra;
   m.dec = hints.dec;
   m.focal = hints.focal;
   m.xpixsz = hints.pixel;
   m.useFocal = true;
   m.resolution = m.ResolutionFromFocal( hints.focal );
   /*
    * Gaia's XPSD catalogue refuses to load without an observation time (it
    * applies proper motions from epoch 2016), and an exported image carries
    * none. Today is used then: a decade of proper motion is far below a
    * pixel for almost every star.
    */
   if ( !m.observationTime )
      m.observationTime = m.startTime = Math.calendarTimeToJD( ( new Date ).toISOString() );
   engine.solverCfg.recursiveSplines = true;
   engine.solveImage( window );   // throws on failure, with the solver's reason
};

/*
 * Solve from the hints, then -- when that fails -- at 1/2 and 1/3 of the
 * pixel size: a drizzled image's pixels are that much finer than the
 * camera's, and a finished image says nothing about how it was stacked (a
 * real 2x-drizzled image failed at the camera's 3.76 um and solved at
 * 1.88). Returns the pixel size that solved; throws the first error.
 */
Sky.solveWithHints = function( window, hints, stage )
{
   var reasons = [];
   for ( var k = 1; k <= 3; ++k )
   {
      try
      {
         if ( stage ) stage( "Solving at " + ( hints.pixel/k ).toFixed( 2 ) + " \u00b5m per pixel" + ( k > 1 ? " (drizzled " + k + "\u00d7?)" : "" ), 0, 0 );
         Sky.solveOnce( window, Object.assign( {}, hints, { pixel: hints.pixel/k } ) );
         // only a scale the rig can give is believed: a wider solve once put the catalogue on the wrong stars
         var solved = Sky.solvedScale( window ), expected = 206.265*hints.pixel/hints.focal;
         if ( !Fly.plausibleScale( solved, expected ) )
            throw new Error( "The solve came out at " + solved.toFixed( 2 ) + "\u2033/px, which the focal length and pixel size cannot give (" +
                             expected.toFixed( 2 ) + "\u2033/px, or half or a third of it drizzled)." );
         if ( k > 1 ) Util.log( "fly", "solved at " + ( hints.pixel/k ).toFixed( 2 ) + " um: the image is drizzled " + k + "x" );
         return hints.pixel/k;
      }
      catch ( e ) { reasons.push( String( e.message || e ) ); }
   }
   // what was tried, so a wrong focal length or pixel size shows (the solver's own message did not say)
   throw new Error( Fly.solveFailure( hints, reasons ) );
};

/*
 * Float32Arrays to one binary file and back (the lengths are kept by the
 * caller). A File object's write(typedArray) and read(ByteArray) --
 * File.writeFile with a typed array crashes PixInsight 1.9.5 (probed).
 */
Sky.writeArrays = function( path, arrays )
{
   var dir = File.extractDrive( path ) + File.extractDirectory( path );
   if ( !File.directoryExists( dir ) ) File.createDirectory( dir, true );
   var f = new File;
   f.createForWriting( path );
   try { arrays.forEach( function( a ) { if ( a.length ) f.write( a ); } ); }
   finally { f.close(); }
};

Sky.readArrays = function( path, lengths )
{
   var f = new File;
   f.openForReading( path );
   try { return lengths.map( function( n ) { return n ? f.read( DataType_ByteArray, 4*n ).toFloat32Array() : new Float32Array( 0 ); } ); }
   finally { f.close(); }
};

/* The solved image's scale (arcsec/px), across its width. */
Sky.solvedScale = function( window )
{
   var un = Sky.unprojector( window ), w = window.mainView.image.width, h = window.mainView.image.height;
   return 3600*Fly.separation( un( 0, h/2 ), un( w, h/2 ) )/w;
};

/*
 * The image's embedded ICC profile, as bytes, or null. PJSR exposes no
 * profile on an open window (probed 2026-09-23), so it is read from the
 * image's file when there is one -- a header read -- and otherwise from a
 * temporary save of the view, removed after.
 */
Sky.iccBytes = function( window )
{
   function readFrom( path )
   {
      var ext = File.extractExtension( path ).toLowerCase();
      var f = new FileFormatInstance( new FileFormat( ext, true, false ) );
      if ( !f.open( path, "verbosity 0" ) )
         return null;
      try { var b = f.iccProfile; return ( b && b.length > 0 ) ? b : null; }
      finally { f.close(); }
   }
   try
   {
      var path = window.filePath;
      if ( path && File.exists( path ) )
         return readFrom( path );
   }
   catch ( e ) {}
   var tmp = File.systemTempDirectory + "/loom-fly-icc-" + Date.now() + ".xisf";
   try
   {
      var copy = Sky.copyWindow( window, "fly_icc_probe" );
      try { copy.saveAs( tmp, false, false, false, false ); }
      finally { copy.forceClose(); }
      return readFrom( tmp );
   }
   catch ( e ) { return null; }
   finally { try { if ( File.exists( tmp ) ) File.remove( tmp ); } catch ( e2 ) {} }
};

/* { colour, note }: the image's colour description, or sRGB with the reason. */
Sky.colourOf = function( window )
{
   var b = Sky.iccBytes( window );
   if ( b == null )
      return { colour: Fly.SRGB_COLOUR, note: "no colour profile: taken as sRGB" };
   var c = Fly.parseIccColour( function( i ) { return b.at( i ); }, b.length );
   if ( c == null )
      return { colour: Fly.SRGB_COLOUR, note: "colour profile is not a matrix/curve profile: taken as sRGB" };
   return { colour: c, note: "colour converted from the image's own profile" };
};

/*
 * The image Fly-Through works on: a copy, resampled once so its largest
 * 16:9 frame is 3840x2160 (Fly.workingScale). Everything after -- solving,
 * star removal, sprites, every frame -- then costs what a 4K image costs,
 * not what the original does: a 92 MP input took 1.7-3.1 s a frame when
 * each frame was resampled from full size. Resample's Auto interpolation
 * filters when it reduces. The original is untouched. Returns
 * { window, scale }; the caller closes the window.
 */
Sky.workingCopy = function( window )
{
   var img = window.mainView.image, f = Fly.workingScale( img.width, img.height );
   var hints = window.hasAstrometricSolution ? Sky.solutionHints( window ) : null;
   var wcs = Sky.keywordWcs( window );                // Resample drops these; restored scaled
   var copy = Sky.copyWindow( window, "fly_work" );
   try
   {
      copy.keywords = window.keywords;
      if ( window.hasAstrometricSolution )
         copy.copyAstrometricSolution( window );
      if ( f < 1 )
      {
         var P = new Resample;
         P.mode = Resample.RelativeDimensions;
         P.xSize = f;
         P.ySize = f;
         P.interpolation = Resample.Auto;
         P.noGUIMessages = true;     // a solved image would otherwise stop on a modal question
         if ( !P.executeOn( copy.mainView ) )
            throw new Error( "Could not resample the image to its working size" );
         if ( wcs ) Sky.writeTanKeywords( copy, Sky.scaledWcs( wcs, f ) );
         if ( hints && !copy.hasAstrometricSolution )
            Sky.solveOnce( copy, Object.assign( hints, { pixel: hints.pixel/f } ) );
      }
      return { window: copy, scale: f };
   }
   catch ( e ) { copy.forceClose(); throw e; }
};

/*
 * A linear master renders as a black video (its sky near 0.001-0.01; a
 * finished image's is ~0.05-0.25): stretch it, with Loom's own stretch.
 * Done AFTER solving -- the solver works best on the linear data (on a
 * real master it took 19 minutes stretched, under one linear). Returns
 * whether it stretched.
 */
Sky.stretchIfLinear = function( window )
{
   if ( !Fly.looksLinear( window.mainView.image.median() ) )
      return false;
   Steps.stretch( window.mainView, true, "fly-through", Steps.STRETCH_SKY_TARGET );
   return true;
};

/* Centre and scale of a solved image, as solve hints (focal fixed at 1000 mm). */
Sky.solutionHints = function( window )
{
   var img = window.mainView.image, c = window.imageToCelestial( img.width/2, img.height/2 );
   var resDeg = window.resolutionAt( c );              // degrees per pixel
   return { ra: c.x, dec: c.y, focal: 1000, pixel: resDeg*3600*1000/206.265 };
};

/* TAN WCS after a resample by f: pixel centres map to (x + 0.5) f - 0.5. */
Sky.scaledWcs = function( wcs, f )
{
   return { crval1: wcs.crval1, crval2: wcs.crval2,
            crpix1: ( wcs.crpix1 - 0.5 )*f + 0.5, crpix2: ( wcs.crpix2 - 0.5 )*f + 0.5,
            cd: wcs.cd.map( function( v ) { return v/f; } ) };
};

/* Replaces the window's TAN keywords with `wcs`. */
Sky.writeTanKeywords = function( window, wcs )
{
   var names = [ "CTYPE1", "CTYPE2", "CRVAL1", "CRVAL2", "CRPIX1", "CRPIX2", "CD1_1", "CD1_2", "CD2_1", "CD2_2" ];
   window.keywords = window.keywords.filter( function( k ) { return names.indexOf( k.name ) < 0; } ).concat( [
      new FITSKeyword( "CTYPE1", "'RA---TAN'", "" ), new FITSKeyword( "CTYPE2", "'DEC--TAN'", "" ),
      new FITSKeyword( "CRVAL1", String( wcs.crval1 ), "" ), new FITSKeyword( "CRVAL2", String( wcs.crval2 ), "" ),
      new FITSKeyword( "CRPIX1", String( wcs.crpix1 ), "" ), new FITSKeyword( "CRPIX2", String( wcs.crpix2 ), "" ),
      new FITSKeyword( "CD1_1", String( wcs.cd[0] ), "" ), new FITSKeyword( "CD1_2", String( wcs.cd[1] ), "" ),
      new FITSKeyword( "CD2_1", String( wcs.cd[2] ), "" ), new FITSKeyword( "CD2_2", String( wcs.cd[3] ), "" ) ] );
};
