/*
 * Loom's own blind plate solver: the maths. Pure -- no PixInsight call, no
 * file access -- so it runs under node. Geometric hashing of star quads
 * (astrometry.net's method): four stars give a code that shift, turn and
 * zoom leave alone, so no plate scale is needed.
 */
var Solve = {};

/* Gnomonic (TAN) projection about `centre`: degrees, xi east, eta north; null on the far half. */
Solve.toPlane = function( centre, ra, dec )
{
   var R = Fly.RAD, a0 = centre.ra*R, d0 = centre.dec*R, a = ra*R, d = dec*R;
   var cosc = Math.sin( d0 )*Math.sin( d ) + Math.cos( d0 )*Math.cos( d )*Math.cos( a - a0 );
   if ( !( cosc > 1e-6 ) ) return null;
   return [ Math.cos( d )*Math.sin( a - a0 )/cosc/R,
            ( Math.cos( d0 )*Math.sin( d ) - Math.sin( d0 )*Math.cos( d )*Math.cos( a - a0 ) )/cosc/R ];
};

Solve.fromPlane = function( centre, xi, eta )
{
   var R = Fly.RAD, x = xi*R, y = eta*R, a0 = centre.ra*R, d0 = centre.dec*R;
   var den = Math.cos( d0 ) - y*Math.sin( d0 );
   var a = a0 + Math.atan2( x, den );
   var d = Math.atan2( Math.sin( d0 ) + y*Math.cos( d0 ), Math.sqrt( x*x + den*den ) );
   return { ra: ( ( a/R ) % 360 + 360 ) % 360, dec: d/R };
};

Solve.CODE_TOL = 0.01;   // code-space match radius (ASTAP uses 0.007): ~0.5 px on a 200 px quad

/*
 * A quad's code: A and B, the farthest pair, map to (0,0) and (1,1); C and
 * D's coordinates in that frame are the code. Canonical: A/B swapped so
 * cx + dx <= 1, then C left of D -- one code per quad whatever the input
 * order. null when C or D lies outside the circle on AB (the index keeps
 * only such quads, so an image quad must obey the same rule).
 */
Solve.quadCode = function( pts )
{
   var best = -1, ia = 0, ib = 1;
   for ( var i = 0; i < 4; ++i )
      for ( var j = i + 1; j < 4; ++j )
      {
         var d2 = ( pts[i][0] - pts[j][0] )*( pts[i][0] - pts[j][0] ) + ( pts[i][1] - pts[j][1] )*( pts[i][1] - pts[j][1] );
         if ( d2 > best ) { best = d2; ia = i; ib = j; }
      }
   if ( !( best > 0 ) ) return null;
   var others = [ 0, 1, 2, 3 ].filter( function( k ) { return k != ia && k != ib; } );
   var A = pts[ia], dx = pts[ib][0] - A[0], dy = pts[ib][1] - A[1];
   function frame( P )
   {
      var vx = P[0] - A[0], vy = P[1] - A[1];
      var qr = ( vx*dx + vy*dy )/best, qi = ( vy*dx - vx*dy )/best;   // v/d
      return [ qr - qi, qr + qi ];                                     // times (1+i)
   }
   var c = frame( pts[others[0]] ), d = frame( pts[others[1]] );
   var inside = function( p ) { return ( p[0] - 0.5 )*( p[0] - 0.5 ) + ( p[1] - 0.5 )*( p[1] - 0.5 ) <= 0.5 + 1e-12; };
   if ( !inside( c ) || !inside( d ) ) return null;
   if ( c[0] + d[0] > 1 )
   {
      c = [ 1 - c[0], 1 - c[1] ]; d = [ 1 - d[0], 1 - d[1] ];
      var t = ia; ia = ib; ib = t;
   }
   if ( c[0] > d[0] ) { var u = c; c = d; d = u; others.reverse(); }
   return { code: [ c[0], c[1], d[0], d[1] ], order: [ ia, ib, others[0], others[1] ] };
};

/*
 * Least-squares similarity (turn, zoom, shift; `parity` mirrors y first)
 * from image pixels to the tangent plane. Closed form: centre both sets,
 * then a and b are two dot products.
 */
Solve.fitSimilarity = function( img, sky, parity )
{
   var n = img.length, mu = 0, mv = 0, mx = 0, my = 0, k;
   var U = img.map( function( p ) { return [ p[0], parity ? -p[1] : p[1] ]; } );
   for ( k = 0; k < n; ++k ) { mu += U[k][0]; mv += U[k][1]; mx += sky[k][0]; my += sky[k][1]; }
   mu /= n; mv /= n; mx /= n; my /= n;
   var sa = 0, sb = 0, ss = 0;
   for ( k = 0; k < n; ++k )
   {
      var u = U[k][0] - mu, v = U[k][1] - mv, x = sky[k][0] - mx, y = sky[k][1] - my;
      sa += u*x + v*y; sb += u*y - v*x; ss += u*u + v*v;
   }
   var a = sa/ss, b = sb/ss;
   var fit = { a: a, b: b, tx: mx - ( a*mu - b*mv ), ty: my - ( b*mu + a*mv ), parity: parity ? 1 : 0, scale: Math.sqrt( a*a + b*b ) };
   var e = 0;
   for ( k = 0; k < n; ++k )
   {
      var p = Solve.applySimilarity( fit, img[k][0], img[k][1] );
      e += ( p[0] - sky[k][0] )*( p[0] - sky[k][0] ) + ( p[1] - sky[k][1] )*( p[1] - sky[k][1] );
   }
   fit.rms = Math.sqrt( e/n );
   return fit;
};

Solve.applySimilarity = function( f, x, y )
{
   var v = f.parity ? -y : y;
   return [ f.a*x - f.b*v + f.tx, f.b*x + f.a*v + f.ty ];
};

Solve.invertSimilarity = function( f, xi, eta )
{
   var s2 = f.a*f.a + f.b*f.b, X = xi - f.tx, Y = eta - f.ty;
   var u = ( f.a*X + f.b*Y )/s2, v = ( f.a*Y - f.b*X )/s2;
   return [ u, f.parity ? -v : v ];
};

/*
 * Quad sizes, by the length of AB: bands a factor sqrt 2 apart from 0.3
 * degrees, so a field 0.45-25 degrees across holds quads of at least two
 * bands. Below 0.3 a whole-sky index grows past a few hundred MB.
 */
Solve.BANDS = ( function()
{
   var out = [];
   for ( var k = 0; k < 10; ++k ) out.push( { lo: 0.3*Math.pow( Math.SQRT2, k ), hi: 0.3*Math.pow( Math.SQRT2, k + 1 ) } );
   return out;
} )();
Solve.STARS_PER_CELL = 5;    // brightest stars kept per band-sized cell, so dense regions don't dominate
Solve.QUADS_PER_CELL = 2;
Solve.INDEX_G_MAX = 13;      // deep enough for 5 stars per 0.3 degree cell nearly everywhere

/* An equal-area-ish grid: rows of height `size`, each cut into cells about `size` wide. */
Solve.gridRow = function( dec, size )
{
   return Math.min( Math.ceil( 180/size ) - 1, Math.max( 0, Math.floor( ( dec + 90 )/size ) ) );
};
Solve.gridCols = function( row, size )
{
   var mid = -90 + ( row + 0.5 )*size;
   return Math.max( 1, Math.floor( 360*Math.cos( mid*Fly.RAD )/size ) );
};
Solve.cellOf = function( ra, dec, size )
{
   var row = Solve.gridRow( dec, size ), n = Solve.gridCols( row, size );
   return row*100000 + ( Math.floor( ( ( ra % 360 ) + 360 ) % 360/360*n ) % n );
};

/* Every cell within `radius` degrees of (ra, dec), over-inclusive. */
Solve.cellsNear = function( ra, dec, radius, size )
{
   var out = [], r0 = Solve.gridRow( dec - radius, size ), r1 = Solve.gridRow( dec + radius, size );
   for ( var row = r0; row <= r1; ++row )
   {
      var n = Solve.gridCols( row, size ), lo = -90 + row*size, hi = lo + size;
      var cosd = Math.cos( Math.max( Math.abs( lo ), Math.abs( hi ) )*Fly.RAD );
      var span = ( cosd <= 1e-9 ) ? 360 : ( radius + size )/cosd;
      if ( span >= 180 ) { for ( var c = 0; c < n; ++c ) out.push( row*100000 + c ); continue; }
      var c0 = Math.floor( ( ra - span )/360*n ), c1 = Math.floor( ( ra + span )/360*n );
      for ( var k = c0; k <= c1; ++k ) out.push( row*100000 + ( ( k % n ) + n ) % n );
   }
   var seen = {};
   return out.filter( function( v ) { if ( seen[v] ) return false; seen[v] = true; return true; } );
};

/*
 * Keeps the M brightest stars per cell, for several cell sizes at once, as
 * the catalogue's stars stream past (a 6 degree region to G 13 holds up
 * to ~20 000 stars; a few thousand per size are kept).
 */
Solve.Keeper = function( sizes, M )
{
   this.sizes = sizes; this.M = M;
   this.cells = sizes.map( function() { return {}; } );
};
Solve.Keeper.prototype.add = function( s )
{
   for ( var k = 0; k < this.sizes.length; ++k )
   {
      var id = Solve.cellOf( s.ra, s.dec, this.sizes[k] ), list = this.cells[k][id] || ( this.cells[k][id] = [] );
      if ( list.length == this.M && s.G >= list[this.M - 1].G ) continue;
      if ( list.some( function( o ) { return o.ra == s.ra && o.dec == s.dec; } ) ) continue;   // answers can overlap
      var i = list.length;
      while ( i > 0 && list[i - 1].G > s.G ) --i;
      list.splice( i, 0, s );
      if ( list.length > this.M ) list.pop();
   }
};
Solve.Keeper.prototype.stars = function()
{
   var seen = {}, out = [];
   this.cells.forEach( function( byCell )
   {
      for ( var id in byCell )
         byCell[id].forEach( function( s ) { var key = s.ra + "," + s.dec; if ( !seen[key] ) { seen[key] = true; out.push( s ); } } );
   } );
   return out;
};
Solve.Keeper.prototype.toArrays = function()
{
   var s = this.stars(), a = new Float32Array( 3*s.length );
   s.forEach( function( t, i ) { a[3*i] = t.ra; a[3*i + 1] = t.dec; a[3*i + 2] = t.G; } );
   return a;
};
Solve.Keeper.fromArrays = function( sizes, M, a )
{
   var k = new Solve.Keeper( sizes, M );
   for ( var i = 0; i < a.length; i += 3 ) k.add( { ra: a[i], dec: a[i + 1], G: a[i + 2] } );
   return k;
};

/* Query centres whose `radius` circles cover the sky: rows radius*sqrt2 apart. */
Solve.skyTiles = function( radius )
{
   var step = radius*Math.SQRT2, rows = Math.ceil( 180/step ), out = [];
   for ( var r = 0; r < rows; ++r )
   {
      var dec = -90 + ( r + 0.5 )*180/rows, edge = Math.max( 0, Math.abs( dec ) - 90/rows );   // the row's edge nearest the equator, where it is widest
      var n = Math.max( 1, Math.ceil( 360*Math.cos( edge*Fly.RAD )/step ) );
      for ( var c = 0; c < n; ++c ) out.push( { ra: ( c + 0.5 )*360/n, dec: dec } );
   }
   return out;
};

/* Star indices by band-sized cell. */
Solve.bandGrid = function( stars, size )
{
   var grid = {};
   stars.forEach( function( s, i ) { var c = Solve.cellOf( s.ra, s.dec, size ); ( grid[c] || ( grid[c] = [] ) ).push( i ); } );
   return grid;
};

/*
 * A band's quads: per cell, bright A first, B a band-length away, C and D
 * the two brightest stars inside the circle on AB. Codes are made on the
 * tangent plane at A, the way the image's are made on its pixels.
 */
Solve.bandQuads = function( stars, band, Q )
{
   var grid = Solve.bandGrid( stars, band.lo ), out = [], seen = {}, sorted = {};
   for ( var cell in grid )
      Solve.cellQuads( stars, band, Q, grid, cell, seen, out, sorted );
   return out;
};

/* A cell's stars, brightest first (a stable sort: ties keep the grid's order); made once per cell and band, kept in `sorted`. */
Solve.sortedCell = function( stars, grid, cell, sorted )
{
   return sorted[cell] || ( sorted[cell] = grid[cell].slice().sort( function( a, b ) { return stars[a].G - stars[b].G; } ) );
};

/*
 * One cell's quads, appended to `out`; `seen` spans the band, so a quad
 * made from a neighbour cell isn't made twice. The neighbours are read
 * brightest first through a lazy merge of their sorted cells: a cell of
 * the widest band has tens of thousands of neighbours, and sorting them
 * all took 70 ms a cell, while a quad is found among the first hundred.
 * The order is the one a stable sort of all of them would give.
 */
Solve.cellQuads = function( stars, band, Q, grid, cell, seen, out, sorted )
{
   var size = band.lo;
   sorted = sorted || {};
   var own = Solve.sortedCell( stars, grid, cell, sorted ), made = 0;
   var A0 = stars[own[0]], lists = [], at = [], near = [];
   Solve.cellsNear( A0.ra, A0.dec, band.hi + size, size ).forEach( function( c )
   {
      if ( grid[c] ) { lists.push( Solve.sortedCell( stars, grid, c, sorted ) ); at.push( 0 ); }
   } );
   /* near[j], merged on demand; -1 past the end */
   function nearAt( j )
   {
      while ( near.length <= j )
      {
         var best = -1, bestG = Infinity;
         for ( var l = 0; l < lists.length; ++l )
            if ( at[l] < lists[l].length && stars[lists[l][at[l]]].G < bestG ) { bestG = stars[lists[l][at[l]]].G; best = l; }
         if ( best < 0 ) return -1;
         near.push( lists[best][at[best]++] );
      }
      return near[j];
   }
   for ( var i = 0; i < own.length && made < Q; ++i )
      for ( var j = 0, b; made < Q && ( b = nearAt( j ) ) >= 0; ++j )
      {
         var quad = Solve.pairQuad( stars, band, own[i], b, nearAt );
         if ( !quad ) continue;
         var key = quad.ids.slice().sort().join();
         if ( seen[key] ) continue;
         seen[key] = true;
         out.push( { ids: quad.q.order.map( function( o ) { return quad.ids[o]; } ), code: quad.q.code } );
         ++made;
      }
};

/*
 * The quad of stars a and b, a pair in the band, with the first two stars
 * of nearAt( k ) inside their circle: { ids, q: its Solve.quadCode }, or
 * null when there is none.
 */
Solve.pairQuad = function( stars, band, a, b, nearAt )
{
   if ( a == b ) return null;
   var dAB = Fly.separation( stars[a], stars[b] );
   if ( dAB < band.lo || dAB >= band.hi ) return null;
   var pA = Solve.toPlane( stars[a], stars[a].ra, stars[a].dec ), pB = Solve.toPlane( stars[a], stars[b].ra, stars[b].dec );
   var mid = [ ( pA[0] + pB[0] )/2, ( pA[1] + pB[1] )/2 ], r2 = ( ( pB[0] - pA[0] )*( pB[0] - pA[0] ) + ( pB[1] - pA[1] )*( pB[1] - pA[1] ) )/4;
   var inside = [];
   for ( var k = 0, c; inside.length < 2 && ( c = nearAt( k ) ) >= 0; ++k )
   {
      if ( c == a || c == b ) continue;
      var p = Solve.toPlane( stars[a], stars[c].ra, stars[c].dec );
      if ( p && ( p[0] - mid[0] )*( p[0] - mid[0] ) + ( p[1] - mid[1] )*( p[1] - mid[1] ) < r2 ) inside.push( { i: c, p: p } );
   }
   if ( inside.length < 2 ) return null;
   var q = Solve.quadCode( [ pA, pB, inside[0].p, inside[1].p ] );
   return q ? { ids: [ a, b, inside[0].i, inside[1].i ], q: q } : null;
};

Solve.HASH_BIN = 0.01;        // bin width; lookups search the 3^4 bins around a code (tol <= bin)
Solve.HASH_BINS = 160;        // bins per axis over the code range [-0.25, 1.35)

Solve.hashKey = function( c0, c1, c2, c3 )
{
   var B = Solve.HASH_BINS, w = Solve.HASH_BIN;
   function bin( v ) { return Math.min( B - 1, Math.max( 0, Math.floor( ( v + 0.25 )/w ) ) ); }
   return ( ( bin( c0 )*B + bin( c1 ) )*B + bin( c2 ) )*B + bin( c3 );
};

/* Codes sorted by bin key: key*2^22 + index sorts natively as one Float64Array (up to 4M quads). */
Solve.buildHash = function( codes )
{
   var n = codes.length/4, packed = new Float64Array( n ), P = 4194304;
   if ( n >= P ) throw new Error( "Solver index too large for its hash (" + n + " quads)" );
   for ( var i = 0; i < n; ++i ) packed[i] = Solve.hashKey( codes[4*i], codes[4*i + 1], codes[4*i + 2], codes[4*i + 3] )*P + i;
   packed.sort();
   var keys = new Float64Array( n ), order = new Uint32Array( n );
   for ( i = 0; i < n; ++i ) { keys[i] = Math.floor( packed[i]/P ); order[i] = packed[i] - keys[i]*P; }
   return { keys: keys, order: order };
};

Solve.lowerBound = function( a, v )
{
   var lo = 0, hi = a.length;
   while ( lo < hi ) { var m = ( lo + hi ) >> 1; if ( a[m] < v ) lo = m + 1; else hi = m; }
   return lo;
};

/* The quads whose codes lie within tol of code: from a k-d tree on a 1705 core (Solve.lookupTree), else from the hash. */
Solve.lookup = function( hash, codes, code, tol )
{
   var tree = Util.hasKDTree() && Solve.treeKeysExact( codes.length/4 );
   return ( tree ? Solve.lookupTree : Solve.lookupHash )( hash, codes, code, tol );
};

/* The hash's answer: the 3^4 bins around code, each read in quad order, bins in key order (tol <= bin width, so none is missed). */
Solve.lookupHash = function( hash, codes, code, tol )
{
   var B = Solve.HASH_BINS, w = Solve.HASH_BIN, out = [];
   var base = code.map( function( v ) { return Math.floor( ( v + 0.25 )/w ); } );
   for ( var d0 = -1; d0 <= 1; ++d0 ) for ( var d1 = -1; d1 <= 1; ++d1 ) for ( var d2 = -1; d2 <= 1; ++d2 ) for ( var d3 = -1; d3 <= 1; ++d3 )
   {
      var b = [ base[0] + d0, base[1] + d1, base[2] + d2, base[3] + d3 ];
      if ( b.some( function( v ) { return v < 0 || v >= B; } ) ) continue;
      Solve.lookupBin( hash, codes, code, tol, ( ( b[0]*B + b[1] )*B + b[2] )*B + b[3], out );
   }
   return out;
};

/* The quads in one hash bin (key) within tol of code, appended to out. */
Solve.lookupBin = function( hash, codes, code, tol, key, out )
{
   for ( var i = Solve.lowerBound( hash.keys, key ); i < hash.keys.length && hash.keys[i] == key; ++i )
   {
      var q = hash.order[i], e = 0;
      for ( var j = 0; j < 4; ++j ) e += ( codes[4*q + j] - code[j] )*( codes[4*q + j] - code[j] );
      if ( e <= tol*tol ) out.push( q );
   }
};

/*
 * Solve.lookup's answer from a k-d tree of the codes (PixInsight 1.9.5
 * build 1705 and later, Util.hasKDTree), made on the first lookup and kept
 * on the hash: about 7 times faster a lookup, tree included. The tree's
 * box of half-side tol (widened a hair against rounding) holds every quad
 * within tol; each is kept on the hash's own tests -- within tol, and in
 * one of the 3^4 bins around the code -- and the kept ones are put in the
 * hash's order, by bin key and then quad number, so the answer is the same
 * one, element for element and in order.
 */
Solve.lookupTree = function( hash, codes, code, tol )
{
   if ( !hash.tree || hash.treeCodes !== codes )
   {
      var objs = new Array( codes.length/4 );
      for ( var i = 0; i < objs.length; ++i ) objs[i] = { point: Array.prototype.slice.call( codes, 4*i, 4*i + 4 ) };
      hash.tree = Util.kdTree( objs ); hash.treeCodes = codes;
   }
   var w = Solve.HASH_BIN, P = 4194304, found = hash.tree.search( code, tol*( 1 + 1e-9 ) ), keyed = [];
   var base = [ Math.floor( ( code[0] + 0.25 )/w ), Math.floor( ( code[1] + 0.25 )/w ), Math.floor( ( code[2] + 0.25 )/w ), Math.floor( ( code[3] + 0.25 )/w ) ];
   for ( var k = 0; k < found.length; ++k )
   {
      var q = found[k], e = 0;
      for ( var j = 0; j < 4; ++j ) e += ( codes[4*q + j] - code[j] )*( codes[4*q + j] - code[j] );
      if ( !( e <= tol*tol ) ) continue;
      var key = Solve.hashKey( codes[4*q], codes[4*q + 1], codes[4*q + 2], codes[4*q + 3] );
      if ( Solve.keyNear( key, base ) ) keyed.push( key*P + q );
   }
   keyed.sort( function( a, b ) { return a - b; } );
   return keyed.map( function( v ) { return v % P; } );
};

/* Whether Solve.lookupTree's packed sort key (bin key*2^22 + quad number) is exact for n quads: fewer than 2^22, and every key below 2^53. */
Solve.treeKeysExact = function( n )
{
   return n < 4194304 && Math.pow( Solve.HASH_BINS, 4 )*4194304 <= 9007199254740992;
};

/* Whether each of the bins of hash key `key` is within one of `base`'s: the bins Solve.lookup reads. */
Solve.keyNear = function( key, base )
{
   for ( var j = 3; j >= 0; --j )
   {
      var b = key % Solve.HASH_BINS;
      if ( b < base[j] - 1 || b > base[j] + 1 ) return false;
      key = ( key - b )/Solve.HASH_BINS;
   }
   return true;
};

Solve.INDEX_VERSION = 1;     // bump when the index's content or format changes: a new one is built
Solve.GRID_DEG = 1;          // star lookup cells for verification

/*
 * Builds an index a slice at a time: step( maxCells ) makes the quads of
 * up to maxCells more cells and returns done(). A band's star grid is made
 * when the band starts.
 */
Solve.GRID_CHUNK = 100000;   // stars put in a band's grid per step

Solve.IndexBuilder = function( stars, bands, Q )
{
   this.stars = stars; this.bands = bands; this.Q = Q;
   this.ids = []; this.codes = []; this.band = [];
   this.k = 0; this.grid = null; this.cells = null; this.cursor = 0; this.seen = null; this.sorted = null;
   this.finished = null;
};
Solve.IndexBuilder.prototype.step = function( maxCells )
{
   var left = maxCells;
   while ( !this.done() && left > 0 )
   {
      if ( !this.cells ) { this.gridStep(); return false; }
      var out = [], end = Math.min( this.cells.length, this.cursor + left );
      for ( ; this.cursor < end; ++this.cursor, --left )
         Solve.cellQuads( this.stars, this.bands[this.k], this.Q, this.grid, this.cells[this.cursor], this.seen, out, this.sorted );
      for ( var i = 0; i < out.length; ++i )
      {
         var q = out[i];
         this.ids.push( q.ids[0], q.ids[1], q.ids[2], q.ids[3] );
         this.codes.push( q.code[0], q.code[1], q.code[2], q.code[3] );
         this.band.push( this.k );
      }
      if ( this.cursor >= this.cells.length ) { ++this.k; this.grid = this.cells = this.seen = this.sorted = null; }
   }
   return this.done();
};
/* GRID_CHUNK more stars into the band's star grid (2.3M stars took 0.5 s in one go); when all are in, the band's cells. */
Solve.IndexBuilder.prototype.gridStep = function()
{
   var size = this.bands[this.k].lo, stars = this.stars;
   if ( !this.grid ) { this.grid = {}; this.next = 0; }
   for ( var end0 = Math.min( stars.length, this.next + Solve.GRID_CHUNK ); this.next < end0; ++this.next )
   {
      var c = Solve.cellOf( stars[this.next].ra, stars[this.next].dec, size );
      ( this.grid[c] || ( this.grid[c] = [] ) ).push( this.next );
   }
   if ( this.next < stars.length ) return;
   this.cells = Object.keys( this.grid );   // the same order as bandQuads' for-in: integer keys, ascending
   this.cursor = 0; this.seen = {}; this.sorted = {};
};
Solve.IndexBuilder.prototype.done = function()
{
   return this.k >= this.bands.length;
};
/* 0..1: whole bands done plus the current band's share of its cells. */
Solve.IndexBuilder.prototype.fraction = function()
{
   if ( this.done() ) return 1;
   var part = this.cells && this.cells.length ? this.cursor/this.cells.length : 0;
   return ( this.k + part )/this.bands.length;
};
/* The finished index (hash and star grid made here, once); null until done(). */
Solve.IndexBuilder.prototype.index = function()
{
   if ( !this.done() ) return null;
   if ( !this.finished )
   {
      var stars = this.stars, s = new Float32Array( 3*stars.length );
      stars.forEach( function( t, i ) { s[3*i] = t.ra; s[3*i + 1] = t.dec; s[3*i + 2] = t.G; } );
      this.finished = Solve.finishIndex( { version: Solve.INDEX_VERSION, bands: this.bands, stars: s,
                                           quads: new Float32Array( this.ids ), codes: new Float32Array( this.codes ), band: new Float32Array( this.band ) } );
      this.ids = this.codes = this.band = null;
   }
   return this.finished;
};

Solve.makeIndex = function( stars, bands, Q )
{
   var b = new Solve.IndexBuilder( stars, bands, Q );
   while ( !b.step( 1e9 ) ) {}
   return b.index();
};

/* The parts not stored: the code hash and the star grid. */
Solve.finishIndex = function( index )
{
   index.hash = Solve.buildHash( index.codes );
   index.grid = {};
   for ( var i = 0; i < index.stars.length/3; ++i )
   {
      var c = Solve.cellOf( index.stars[3*i], index.stars[3*i + 1], Solve.GRID_DEG );
      ( index.grid[c] || ( index.grid[c] = [] ) ).push( i );
   }
   return index;
};

Solve.indexArrays = function( index )
{
   var arrays = [ index.stars, index.quads, index.codes, index.band ];
   return { header: { version: index.version, bands: index.bands, lengths: arrays.map( function( a ) { return a.length; } ) }, arrays: arrays };
};

Solve.indexFromArrays = function( header, arrays )
{
   return Solve.finishIndex( { version: header.version, bands: header.bands,
                               stars: arrays[0], quads: arrays[1], codes: arrays[2], band: arrays[3] } );
};

Solve.starsNear = function( index, centre, radius )
{
   var out = [];
   Solve.cellsNear( centre.ra, centre.dec, radius, Solve.GRID_DEG ).forEach( function( c )
   {
      ( index.grid[c] || [] ).forEach( function( i )
      {
         if ( Fly.separation( centre, { ra: index.stars[3*i], dec: index.stars[3*i + 1] } ) <= radius ) out.push( i );
      } );
   } );
   return out;
};

Solve.IMAGE_QUAD_STARS = 40;    // brightest detections quads are made from
Solve.IMAGE_QUAD_INSIDE = 4;    // of the brightest inside a pair's circle, each two make a quad with it
Solve.VERIFY_STARS = 300;       // brightest detections a hypothesis is checked against
Solve.MIN_MATCHES = 8;
Solve.MAX_LOG10_CHANCE = -10;   // accept when the matches happening by chance is below 1e-10
Solve.MAX_VERIFY = 3000;
Solve.MIN_SPREAD = 4;           // of the 3x3 image cells holding matches
Solve.FIELD_MIN = 0.3;          // degrees, the image's long side
Solve.FIELD_MAX = 25;

Solve.brightest = function( dets, n )
{
   return dets.slice().sort( function( a, b ) { return b.flux - a.flux; } ).slice( 0, n );
};

/*
 * The image's quads, both parities: every pair of the brightest, with each
 * two of the IMAGE_QUAD_INSIDE brightest inside its circle. The index took
 * the two brightest catalogue stars inside; in a real image a nebula knot,
 * or a star the catalogue ranks fainter, can be brighter than them (the
 * Iris solved from 1 true index hit with the two brightest only, from 4 with
 * the four brightest).
 */
Solve.imageQuads = function( dets, N )
{
   var s = Solve.brightest( dets, N ), out = [];
   for ( var i = 0; i < s.length; ++i )
      for ( var j = i + 1; j < s.length; ++j )
      {
         var mx = ( s[i].x + s[j].x )/2, my = ( s[i].y + s[j].y )/2;
         var r2 = ( ( s[i].x - s[j].x )*( s[i].x - s[j].x ) + ( s[i].y - s[j].y )*( s[i].y - s[j].y ) )/4, inside = Solve.insideCircle( s, i, j, mx, my, r2 );
         for ( var c = 0; c < inside.length; ++c )
            for ( var e = c + 1; e < inside.length; ++e )
               Solve.pushImageQuad( out, [ s[i], s[j], s[inside[c]], s[inside[e]] ], 2*Math.sqrt( r2 ) );
      }
   return out;
};

/* The first IMAGE_QUAD_INSIDE stars of s, other than i and j, inside the circle at (mx, my) of squared radius r2: their indices. */
Solve.insideCircle = function( s, i, j, mx, my, r2 )
{
   var inside = [];
   for ( var k = 0; k < s.length && inside.length < Solve.IMAGE_QUAD_INSIDE; ++k )
      if ( k != i && k != j && ( s[k].x - mx )*( s[k].x - mx ) + ( s[k].y - my )*( s[k].y - my ) < r2 ) inside.push( k );
   return inside;
};

Solve.pushImageQuad = function( out, pts, diameter )
{
   [ 0, 1 ].forEach( function( parity )
   {
      var q = Solve.quadCode( pts.map( function( p ) { return [ p.x, parity ? -p.y : p.y ]; } ) );
      if ( q ) out.push( { pts: q.order.map( function( o ) { return [ pts[o].x, pts[o].y ]; } ), code: q.code, parity: parity, diameter: diameter } );
   } );
};

/*
 * The image's bright, saturated stars, from its intensity (`buf`, W x H,
 * 0..1). StarDetector drops them in a stretched, finished image -- flat
 * tops, diffraction spikes, halos -- and they are the very stars the index
 * is made of: on the Iris it missed 22 of the 45 Gaia stars to G 13 in the
 * field, and those it kept ranked no better than fainter ones. Here a star
 * is a compact blob above a high threshold (the BRIGHT_QUANTILE of the
 * pixels); a blob that isn't compact (a star in bright nebula, two stars
 * touching) is split at a higher threshold, halfway to its peak. The centre
 * is the top of the core, which a nebula around it doesn't pull. Returns
 * [{ x, y, n }], biggest core first: the core's size ranks a saturated star.
 */
Solve.BRIGHT_QUANTILE = 0.998;
Solve.BRIGHT_MIN = 0.3;          // threshold limits: never into the sky of a dark image,
Solve.BRIGHT_MAX = 0.9;          // and always below a saturated core
Solve.BRIGHT_FILL = 0.4;         // of the blob's bounding square: a star is round, a spike cross still fills 0.4-0.8
Solve.BRIGHT_SIZE = 100;         // px, the widest star core (a 4K working copy)
/* The bright stars' threshold: the BRIGHT_QUANTILE of the n pixels of buf, within BRIGHT_MIN..BRIGHT_MAX. */
Solve.brightThreshold = function( buf, n )
{
   var hist = new Float64Array( 1024 ), i;
   for ( i = 0; i < n; ++i ) ++hist[Math.max( 0, Math.min( 1023, Math.floor( buf[i]*1024 ) ) )];
   var above = n*( 1 - Solve.BRIGHT_QUANTILE ), T = 1;
   for ( i = 1023; i >= 0 && above > 0; --i ) { above -= hist[i]; T = i/1024; }
   return Math.max( Solve.BRIGHT_MIN, Math.min( Solve.BRIGHT_MAX, T ) );
};

/*
 * The 4-connected pixels marked `cur` around `start` in mark (W x H), each
 * re-marked `done`: { pixels, peak: the brightest in buf, size: the side of
 * the bounding square }.
 */
Solve.brightBlob = function( buf, W, H, mark, cur, done, start )
{
   var n = W*H, comp = [], st = [ start ], x0 = W, x1 = 0, y0 = H, y1 = 0, peak = 0;
   function visit( k ) { if ( mark[k] == cur ) { mark[k] = done; st.push( k ); } }
   mark[start] = done;
   while ( st.length )
   {
      var j = st.pop(), x = j % W, y = ( j - x )/W;
      comp.push( j );
      x0 = Math.min( x0, x ); x1 = Math.max( x1, x ); y0 = Math.min( y0, y ); y1 = Math.max( y1, y );
      peak = Math.max( peak, buf[j] );
      if ( x > 0 ) visit( j - 1 );
      if ( x < W - 1 ) visit( j + 1 );
      if ( j >= W ) visit( j - W );
      if ( j + W < n ) visit( j + W );
   }
   return { pixels: comp, peak: peak, size: Math.max( x1 - x0 + 1, y1 - y0 + 1 ) };
};

/* A star's { x, y, n }: its pixels (comp) weighted by how far they are above tc, the top of its core; a flat top at exactly the peak, its middle. */
Solve.brightCentre = function( buf, W, comp, tc, peak )
{
   var sx = 0, sy = 0, sw = 0;
   comp.forEach( function( k ) { var v = buf[k] - tc; if ( v > 0 ) { var kx = k % W; sx += kx*v; sy += ( k - kx )/W*v; sw += v; } } );
   if ( sw > 0 ) return { x: sx/sw, y: sy/sw, n: comp.length };
   comp.forEach( function( k ) { if ( buf[k] >= peak ) { var kx = k % W; sx += kx; sy += ( k - kx )/W; sw += 1; } } );
   return { x: sx/sw, y: sy/sw, n: comp.length };
};

/* A blob found above threshold w.t: a compact one is a star (into out), another is split at a higher threshold (into work). */
Solve.brightTake = function( buf, W, blob, w, out, work )
{
   var comp = blob.pixels, peak = blob.peak, d = blob.size;
   if ( comp.length < 4 ) return;   // a hot pixel or two
   if ( comp.length/( d*d ) >= Solve.BRIGHT_FILL && d <= Solve.BRIGHT_SIZE )
      out.push( Solve.brightCentre( buf, W, comp, ( w.t + peak )/2, peak ) );
   else if ( w.depth < 6 && peak - w.t > 0.01 )
      work.push( { pixels: comp, t: w.t + ( peak - w.t )/2, depth: w.depth + 1 } );
};

Solve.brightStars = function( buf, W, H )
{
   var n = W*H, i, T = Solve.brightThreshold( buf, n );
   var mark = new Int32Array( n ), stamp = 0, out = [], all = [];
   for ( i = 0; i < n; ++i ) if ( buf[i] >= T ) all.push( i );
   var work = [ { pixels: all, t: T, depth: 0 } ];
   while ( work.length )
   {
      var w = work.pop(), cur = ++stamp;
      w.pixels.forEach( function( j ) { if ( buf[j] >= w.t ) mark[j] = cur; } );
      var done = ++stamp;
      for ( var p = 0; p < w.pixels.length; ++p )
      {
         if ( mark[w.pixels[p]] != cur ) continue;
         Solve.brightTake( buf, W, Solve.brightBlob( buf, W, H, mark, cur, done, w.pixels[p] ), w, out, work );
      }
   }
   return out.sort( function( a, b ) { return b.n - a.n; } );
};

/*
 * The stars a blind solve works from: the bright stars above first, biggest
 * core first, then the detector's by flux, leaving out a detection on a
 * bright star. `flux` is rewritten so Solve.brightest keeps that order.
 */
Solve.blindStars = function( dets, buf, W, H )
{
   var bright = Solve.brightStars( buf, W, H ), top = 0, out = [];
   dets.forEach( function( d ) { if ( d.flux > top ) top = d.flux; } );
   top = top > 0 ? top : 1;
   var grid = {};
   bright.forEach( function( b, i )
   {
      out.push( { x: b.x, y: b.y, flux: top*( 2 + b.n ) } );
      var key = Math.floor( b.x/8 ) + "," + Math.floor( b.y/8 );
      ( grid[key] || ( grid[key] = [] ) ).push( b );
   } );
   dets.forEach( function( d )
   {
      var gx = Math.floor( d.x/8 ), gy = Math.floor( d.y/8 );
      for ( var x = gx - 1; x <= gx + 1; ++x ) for ( var y = gy - 1; y <= gy + 1; ++y )
         if ( ( grid[x + "," + y] || [] ).some( function( b ) { return Math.hypot( b.x - d.x, b.y - d.y ) < 5; } ) ) return;
      out.push( d );
   } );
   return out;
};

/* log10 of P(X >= k), X ~ Binomial(n, p): the chance k matches are luck. */
Solve.log10Chance = function( k, n, p )
{
   if ( k <= 0 ) return 0;
   if ( k > n ) return -Infinity;
   var lg = function( m ) { var s = 0; for ( var i = 2; i <= m; ++i ) s += Math.log( i ); return s; };
   var lnC = lg( n ) - lg( k ) - lg( n - k ), terms = [];
   for ( var j = k; j <= n; ++j )
   {
      terms.push( lnC + j*Math.log( p ) + ( n - j )*Math.log( 1 - p ) );
      lnC += Math.log( n - j ) - Math.log( j + 1 );
   }
   var m = Math.max.apply( null, terms ), sum = 0;
   terms.forEach( function( t ) { sum += Math.exp( t - m ); } );
   return ( m + Math.log( sum ) )/Math.LN10;
};

/*
 * How many catalogue stars the hypothesis puts on detections, and the odds
 * of that by chance. Loose radius first, refit on those pairs, then tight.
 */
Solve.verify = function( index, fit, centre, dets, W, H )
{
   var diag = Math.hypot( W, H ), bright = Solve.brightest( dets, Solve.VERIFY_STARS );
   var cellPx = 32, grid = {};
   bright.forEach( function( d, i ) { var key = Math.floor( d.x/cellPx ) + "," + Math.floor( d.y/cellPx ); ( grid[key] || ( grid[key] = [] ) ).push( i ); } );
   var near = Solve.starsNear( index, centre, 0.55*fit.scale*diag );
   function pass( f, r )
   {
      var img = [], sky = [], n = 0, used = {};
      near.forEach( function( i )
      {
         var p = Solve.toPlane( centre, index.stars[3*i], index.stars[3*i + 1] );
         if ( !p ) return;
         var xy = Solve.invertSimilarity( f, p[0], p[1] );
         if ( xy[0] < 0 || xy[1] < 0 || xy[0] >= W || xy[1] >= H ) return;
         ++n;
         var best = -1, bd = r*r, gx = Math.floor( xy[0]/cellPx ), gy = Math.floor( xy[1]/cellPx ), span = Math.ceil( r/cellPx );
         for ( var x = gx - span; x <= gx + span; ++x ) for ( var y = gy - span; y <= gy + span; ++y )
            ( grid[x + "," + y] || [] ).forEach( function( k )
            {
               var d2 = ( bright[k].x - xy[0] )*( bright[k].x - xy[0] ) + ( bright[k].y - xy[1] )*( bright[k].y - xy[1] );
               if ( d2 <= bd && !used[k] ) { bd = d2; best = k; }
            } );
         if ( best < 0 ) return;
         used[best] = true;
         img.push( [ bright[best].x, bright[best].y ] ); sky.push( p );
      } );
      return { img: img, sky: sky, n: n };
   }
   var loose = pass( fit, 0.01*diag );
   if ( loose.img.length < 4 ) return { matches: loose.img.length, of: loose.n, log10Chance: 0, fit: fit, centre: centre };
   var refit = Solve.fitSimilarity( loose.img, loose.sky, fit.parity ), r = Math.max( 3, 0.0015*diag );
   var tight = pass( refit, r );
   var p0 = Math.min( 0.5, bright.length*Math.PI*r*r/( W*H ) );
   var best = tight.img.length >= 4 ? Solve.fitSimilarity( tight.img, tight.sky, fit.parity ) : refit;
   // matches bunched in one corner (a nebula's knots, a bright star's halo) are no evidence of a field
   var cells = {};
   tight.img.forEach( function( p ) { cells[Math.min( 2, Math.floor( 3*p[0]/W ) ) + "," + Math.min( 2, Math.floor( 3*p[1]/H ) )] = true; } );
   return { matches: tight.img.length, of: tight.n, spread: Object.keys( cells ).length,
            log10Chance: Solve.log10Chance( tight.img.length, tight.n, p0 ), fit: best, centre: centre };
};

/* A hypothesis's vote: its scale (2% steps) and centre (0.05 degree cells). */
Solve.voteKey = function( fit, centre )
{
   return Math.round( Math.log( fit.scale )/0.02 ) + ":" + Solve.cellOf( centre.ra, centre.dec, 0.05 );
};

/* Which hypotheses one verification stands for: the vote, and the mirroring and turn (5 degree steps) -- the same centre turned is another field. */
Solve.triedKey = function( fit, centre )
{
   return Solve.voteKey( fit, centre ) + ":" + fit.parity + ":" + ( ( Math.round( Math.atan2( fit.b, fit.a )/Fly.RAD/5 ) % 72 + 72 ) % 72 );
};

/*
 * The blind solve: every image quad looked up in the index (both
 * parities), each hit a hypothesis -- a similarity from pixels to the sky
 * -- checked by Solve.verify, the ones several hits agree on first.
 * Returns the accepted solutions, best first; empty when none.
 */
Solve.solve = function( index, dets, W, H, opts )
{
   opts = opts || {};
   var tick = opts.tick || function() {}, out = [];
   if ( dets.length < Solve.MIN_MATCHES ) return out;
   var hyps = [], quads = Solve.imageQuads( dets, Solve.IMAGE_QUAD_STARS );
   quads.forEach( function( iq, n )
   {
      if ( n % 50 == 0 ) tick();
      Solve.lookup( index.hash, index.codes, iq.code, Solve.CODE_TOL ).forEach( function( q )
      {
         var a = index.quads[4*q], ref = { ra: index.stars[3*a], dec: index.stars[3*a + 1] }, sky = [];
         for ( var j = 0; j < 4; ++j ) { var s = index.quads[4*q + j]; sky.push( Solve.toPlane( ref, index.stars[3*s], index.stars[3*s + 1] ) ); }
         if ( sky.some( function( p ) { return !p; } ) ) return;
         var fit = Solve.fitSimilarity( iq.pts, sky, iq.parity );
         var field = fit.scale*Math.max( W, H );
         if ( field < Solve.FIELD_MIN || field > Solve.FIELD_MAX || fit.rms > 0.02*fit.scale*iq.diameter ) return;
         var c = Solve.applySimilarity( fit, W/2, H/2 ), centre = Solve.fromPlane( ref, c[0], c[1] );
         hyps.push( { ref: ref, fit: fit, centre: centre, key: Solve.voteKey( fit, centre ), tried: Solve.triedKey( fit, centre ) } );
      } );
   } );
   var votes = {};
   hyps.forEach( function( h ) { votes[h.key] = ( votes[h.key] || 0 ) + 1; } );
   hyps.sort( function( a, b ) { return votes[b.key] - votes[a.key]; } );
   // many hypotheses are tried: the threshold tightens with the whole verification budget (Bonferroni), fixed up front
   var tried = {}, budget = Math.max( 1, Math.min( hyps.length, Solve.MAX_VERIFY ) ), limit = Solve.MAX_LOG10_CHANCE - Math.log( budget )/Math.LN10;
   for ( var i = 0; i < hyps.length && i < Solve.MAX_VERIFY && out.length < ( opts.maxResults || 3 ); ++i )
   {
      if ( i % 20 == 0 ) tick();
      var h = hyps[i];
      if ( tried[h.tried] ) continue;
      tried[h.tried] = true;
      // the fit is about the quad's reference star; re-centre it on the image centre's tangent point
      var img = [ [ 0, 0 ], [ W, 0 ], [ 0, H ], [ W, H ], [ W/2, H/2 ] ];
      var sky = img.map( function( p ) { var q = Solve.applySimilarity( h.fit, p[0], p[1] ), s = Solve.fromPlane( h.ref, q[0], q[1] ); return Solve.toPlane( h.centre, s.ra, s.dec ); } );
      var v = Solve.verify( index, Solve.fitSimilarity( img, sky, h.fit.parity ), h.centre, dets, W, H );
      if ( v.matches < Solve.MIN_MATCHES || v.spread < Solve.MIN_SPREAD || v.log10Chance > limit ) continue;
      var c = Solve.applySimilarity( v.fit, W/2, H/2 ), centre = Solve.fromPlane( h.centre, c[0], c[1] );
      var rotation = Math.atan2( v.fit.b, v.fit.a )/Fly.RAD;
      if ( out.some( function( o ) { return Solve.sameSolution( o, { ra: centre.ra, dec: centre.dec, rotation: rotation, parity: v.fit.parity } ); } ) ) continue;
      // the corners pin the turn and the mirroring, which a centre and a scale leave open (Solve.sameField)
      var corners = [ [ 0, 0 ], [ W, 0 ], [ 0, H ], [ W, H ] ].map( function( p ) { var q = Solve.applySimilarity( v.fit, p[0], p[1] ); return Solve.fromPlane( h.centre, q[0], q[1] ); } );
      out.push( { ra: centre.ra, dec: centre.dec, scale: v.fit.scale*3600, rotation: rotation,
                  parity: v.fit.parity, corners: corners, matches: v.matches, of: v.of, spread: v.spread, log10Chance: v.log10Chance } );
   }
   return out.sort( function( a, b ) { return a.log10Chance - b.log10Chance; } );
};

/*
 * Whether two blind solutions are one: the same centre (within 0.05 deg),
 * mirroring and turn (within 2 deg). The same centre turned or mirrored is
 * another solution, and is offered to ImageSolver on its own.
 */
Solve.sameSolution = function( a, b )
{
   var turn = Math.abs( ( ( a.rotation - b.rotation ) % 360 + 540 ) % 360 - 180 );
   return Fly.separation( a, b ) < 0.05 && a.parity == b.parity && turn < 2;
};

/* ImageSolver's corners must lie within this fraction of the image diagonal of the blind match's (Solve.sameField): about 3.4 degrees of turn. */
Solve.CORNER_TOL = 0.03;

/*
 * Whether two sets of image corners ({ra, dec}, in the same order) lie
 * within tolDeg of each other, one for one: a solution at the right centre
 * and scale, but turned or mirrored, fails. False when either is missing.
 */
Solve.sameField = function( a, b, tolDeg )
{
   if ( !a || !b || a.length != b.length || a.length == 0 ) return false;
   return a.every( function( c, i ) { return !!b[i] && Fly.separation( c, b[i] ) <= tolDeg; } );
};

/* A blind solution as Sky.solveWithHints hints: any focal/pixel pair with the right ratio (focal fixed at 1000 mm). */
Solve.hintsFrom = function( r )
{
   return { ra: r.ra, dec: r.dec, focal: 1000, pixel: r.scale*1000/206.265 };
};

/*
 * Solving without a whole-sky index: the index is made on the fly, a region
 * at a time, from the catalogue's stars around a candidate centre, most
 * likely first (Solve.searchOrder). A region is a cone of REGION_RADIUS;
 * candidates are REGION_STEP apart, so an image whose half-diagonal is up
 * to RADIUS - STEP lies wholly inside some region.
 */
Solve.REGION_RADIUS = 6;
Solve.REGION_STEP = 3;
/*
 * Around a catalogued target the region is a small cone: a framed object
 * sits within a degree or two of its catalogue position, and a region
 * costs one catalogue query whatever its size (~0.1-0.3 s), so the likely
 * places are tried cheaply first. Targets closer than TARGET_STEP share
 * one. Fields wider than TARGET_RADIUS are found by the sky pass.
 */
Solve.TARGET_RADIUS = 2;
Solve.TARGET_STEP = 1;
Solve.HISTORY_MAX = 200;       // earlier solves remembered
Solve.HISTORY_SAME = 0.1;      // degrees: a solve this close to an earlier one is the same field

/*
 * Where to look, most likely first: this user's earlier solves; the
 * objects with a popular name (the catalogue's own, or Loom's
 * Fly.OBJECT_ALIASES, `aliases` when given), largest first; the Messier
 * objects by number; the rest of NGC/IC by diameter, largest first; then
 * the sky tiles of `step`, which cover the rest. Popular names come before
 * Messier: Messier's hundred regions span a quarter of the sky, and every
 * tile of it is read from the catalogue before an Iris (NGC 7023, no
 * Messier number) is reached (modelled: region 103, 1798 tiles, against 28
 * and 633 this way). A named centre within `step` of an earlier one is
 * left out: its region is searched already. The sky tiles are all kept --
 * dropping one near a named centre would leave a gap in the cover.
 * [{ ra, dec, name, sky }].
 */
Solve.searchOrder = function( history, ngc, step, aliases, targetStep, targetRadius )
{
   var out = [], grid = {}, tStep = targetStep || step, tRadius = targetRadius || Solve.REGION_RADIUS;
   function add( c, name )
   {
      if ( !isFinite( c.ra ) || !isFinite( c.dec ) ) return;
      var near = Solve.cellsNear( c.ra, c.dec, tStep, tStep );
      for ( var i = 0; i < near.length; ++i )
         if ( ( grid[near[i]] || [] ).some( function( o ) { return Fly.separation( o, c ) < tStep; } ) ) return;
      var e = { ra: c.ra, dec: c.dec, name: name, sky: false, radius: tRadius }, cell = Solve.cellOf( c.ra, c.dec, tStep );
      ( grid[cell] || ( grid[cell] = [] ) ).push( e );
      out.push( e );
   }
   function messier( e ) { return parseInt( String( e.messier || "" ).replace( /\D/g, "" ), 10 ); }
   function label( e ) { return messier( e ) > 0 ? "M" + messier( e ) : e.id; }
   function largestFirst( list ) { return list.map( function( e, i ) { return { e: e, i: i }; } ).sort( function( a, b ) { return ( b.e.diameter || 0 ) - ( a.e.diameter || 0 ) || a.i - b.i; } ).map( function( x ) { return x.e; } ); }
   ngc = ngc || [];
   var byId = {};
   ngc.forEach( function( e ) { byId[e.id] = e; } );
   ( history || [] ).forEach( function( h ) { add( h, "an earlier solve" ); } );
   var popular = ngc.filter( function( e ) { return !!e.name; } );
   ( aliases || Fly.OBJECT_ALIASES ).forEach( function( a )
   {
      var e = byId[a.id];
      if ( a.ra != null ) popular.push( { id: a.id, ra: a.ra, dec: a.dec, diameter: a.diameter || ( e && e.diameter ) || 0 } );
      else if ( e ) popular.push( e );
   } );
   largestFirst( popular ).forEach( function( e ) { add( e, label( e ) ); } );
   ngc.filter( function( e ) { return messier( e ) > 0; } )
      .map( function( e, i ) { return { e: e, i: i }; } )
      .sort( function( a, b ) { return messier( a.e ) - messier( b.e ) || a.i - b.i; } )
      .forEach( function( x ) { add( x.e, label( x.e ) ); } );
   largestFirst( ngc.filter( function( e ) { return !( messier( e ) > 0 ); } ) ).forEach( function( e ) { add( e, e.id ); } );
   Solve.skyTiles( step ).forEach( function( t )
   {
      out.push( { ra: t.ra, dec: t.dec, name: "RA " + t.ra.toFixed( 1 ) + "\u00b0, Dec " + ( t.dec >= 0 ? "+" : "" ) + t.dec.toFixed( 1 ) + "\u00b0", sky: true, radius: Solve.REGION_RADIUS } );
   } );
   return out;
};

/* The bands whose quads fit in a region of `radius`. */
Solve.regionBands = function( bands, radius )
{
   return bands.filter( function( b ) { return b.hi <= radius; } );
};


/*
 * A region's stars from its catalogue answers (Float32 [ra, dec, G] each):
 * those in the cone, the brightest per cell as the index keeps them (the
 * answers can overlap, and a cell can span two).
 */
Solve.regionStars = function( arrays, centre, radius, sizes, M )
{
   var keep = new Solve.Keeper( sizes, M );
   arrays.forEach( function( a )
   {
      for ( var i = 0; i < a.length; i += 3 )
         if ( Math.abs( a[i + 1] - centre.dec ) <= radius && Fly.separation( centre, { ra: a[i], dec: a[i + 1] } ) <= radius )
            keep.add( { ra: a[i], dec: a[i + 1], G: a[i + 2] } );
   } );
   return keep.stars();
};

/* A region's index, made in memory: the bands that fit in a region. */
Solve.regionIndex = function( stars, radius )
{
   return Solve.makeIndex( stars, Solve.regionBands( Solve.BANDS, radius || Solve.REGION_RADIUS ), Solve.QUADS_PER_CELL );
};

/* The earlier solves with `centre` first: the same field once, at most HISTORY_MAX. */
/* A region's cache name: its centre and radius (sky regions and targets are both fixed by the catalogue and the grid). */
Solve.regionKey = function( c )
{
   return Fly.hashKey( c.ra.toFixed( 4 ) + "," + c.dec.toFixed( 4 ) + "," + ( c.radius || Solve.REGION_RADIUS ) );
};

Solve.pushHistory = function( history, centre )
{
   var out = [ { ra: centre.ra, dec: centre.dec } ];
   ( history || [] ).forEach( function( h ) { if ( out.length < Solve.HISTORY_MAX && Fly.separation( h, centre ) > Solve.HISTORY_SAME ) out.push( { ra: h.ra, dec: h.dec } ); } );
   return out;
};

/*
 * The blind search: region after region (`indexFor( candidate )` gives
 * its index, or null to skip it), each solved with Solve.solve; every
 * match is offered to opts.accept( result, candidate ), which alone
 * decides (ImageSolver's confirmation). A rejected match is not offered
 * again when a later region finds it too. opts.onRegion( k, n, candidate )
 * reports progress; opts.tick is called between regions and inside the
 * solve, and may throw to cancel. { result, candidate, region } or null.
 */
Solve.searchSky = function( candidates, indexFor, dets, W, H, opts )
{
   opts = opts || {};
   var tick = opts.tick || function() {}, rejected = [];
   for ( var k = 0; k < candidates.length; ++k )
   {
      var c = candidates[k];
      if ( opts.onRegion ) opts.onRegion( k + 1, candidates.length, c );
      tick();
      var index = indexFor( c );
      if ( !index || index.quads.length == 0 ) continue;
      var results = Solve.solve( index, dets, W, H, { tick: tick } );
      for ( var i = 0; i < results.length; ++i )
      {
         var r = results[i];
         if ( rejected.some( function( o ) { return Solve.sameSolution( o, r ); } ) ) continue;
         if ( opts.accept( r, c ) ) return { result: r, candidate: c, region: k + 1 };
         rejected.push( r );
      }
   }
   return null;
};
