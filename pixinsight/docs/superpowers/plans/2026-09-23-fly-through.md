# Loom Fly-Through Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A third Loom script that turns a finished astrophoto into a push-in video: the photo's own stars move at their real Gaia distances, the nebula grows behind them (a galaxy stays fixed), rendered to 16-bit TIFF frames and MP4 with presets and a draft player.

**Architecture:** Pure maths in `script/lib/Fly.js` (node-tested); PixInsight-side catalogue, projection and star extraction in `script/lib/Sky.js`; compositing, output, ffmpeg and the draft player in `script/lib/Render.js`; the dialog in `script/FlyThrough.js`. Star removal and the stars split reuse `Steps.removeStars` and `Steps.deriveStarsByUnscreen`.

**Tech Stack:** PixInsight PJSR (`#engine v8`), node harness `ci/run-tests.js`, in-PixInsight suite `script/selftest.js` dispatched with `PixInsight -x=1:<path>`.

**Spec:** `docs/superpowers/specs/2026-09-23-fly-through-design.md`

## Global Constraints

- JavaScript (PJSR) only; `#engine v8` is line 1 of every entry point.
- No personal paths in shipped code; tests depend on nothing but PixInsight and files checked into the repository.
- Frames are **16-bit TIFF** (PixInsight's PNG writer cannot store 16 bits: `FileFormat("PNG").canStore16Bit` is false, measured 2026-09-23). The spec's "PNG" is superseded by this.
- New libraries are registered in all three places: `ci/run-tests.js` LIBS, each entry point's `#ifndef LOOM_LIBS_INCLUDED` block, and `script/selftest.js` includes.
- Run node tests with `node ci/run-tests.js` and `LOOM_TEST_CORE_RELEASE=4 node ci/run-tests.js` (needs `/tmp/agent-scratch` writable); run the PixInsight suite only when PixInsight is idle, never concurrently with node (they share the result file).
- **macOS and Windows.** Build paths with "/" (PJSR accepts it on Windows; there is no separator variable). Any function whose behaviour depends on the platform takes `platform` (and `home`, `pathVar`) as arguments, defaulting to `Util.PLATFORM` / `File.homeDirectory`, so node and the Mac suite test the Windows branch. Start external programs with an argument array (no shell). Read their output via `onStandardOutputDataAvailable` / `onStandardErrorDataAvailable` while pumping `CoreApplication.processEvents()` — never `waitForFinished()` then `stdout`.
- Never assign `onViewportScrolled`; release handlers and timers in `finally`; loaders check visibility.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Review Focus

1. An image whose target is not in NGC/IC (a Sharpless-only nebula) → dialog asks for name, type and distance; nothing crashes (Task 8 test "no target in the field").
2. A TIFF with no header at all → solve path asks for centre and scale; never reads a missing keyword as 0 (Task 10 test "no keywords").
3. A field where Gaia returns zero sources (tiny field / wrong database) → stops with the configure message, not an empty video (Task 10 test "empty query").
4. Travel typed larger than D → clamped to 0.9 D and the dialog says so (Task 7 test "travel cap").
5. Windows user with ffmpeg installed by winget or on PATH as `C:\Tools\ffmpeg\bin` → found without Browse (Task 12 tests "winget link first", "PATH on Windows").
6. Cancel pressed mid-final-render → current frame finishes, already-written frames stay, no MP4 attempted (Task 13 test "cancel").

---

### Task 1: Scaffolding and registration

**Files:**
- Create: `script/lib/Fly.js`, `script/lib/Sky.js`, `script/lib/Render.js`, `script/FlyThrough.js`
- Modify: `ci/run-tests.js` (LIBS, entry-point parse list), `script/selftest.js` (includes), `ci/repository.sh:69`, `.github/workflows/ci.yml:114-115`

**Interfaces:**
- Produces: globals `Fly`, `Sky`, `Render` (empty namespaces); entry point `FlyThrough.js` with `#feature-id Loom Fly-Through : Batch Processing > Loom Fly-Through`, `main()` guarded by `#ifndef LOOM_FLY_UNDER_TEST`.

- [ ] **Step 1: Failing test** — in `selftest.js` near the other load checks:

```js
   check( "Fly loads", typeof Fly, "object" );
   check( "Sky loads", typeof Sky, "object" );
   check( "Render loads", typeof Render, "object" );
```

- [ ] **Step 2: Run** `node ci/run-tests.js` → ABORTED, `Fly is not defined`.

- [ ] **Step 3: Implement**

`script/lib/Fly.js`:
```js
/*
 * Loom Fly-Through: the maths. Pure -- no PixInsight call, no file access --
 * so every rule that decides what moves where is tested under node.
 */
var Fly = {};
```
`script/lib/Sky.js`: `var Sky = {};` with a header comment "PixInsight side: catalogues, projection, star extraction." `script/lib/Render.js`: `var Render = {};` with "Frames, output, encoding, the draft player."

`script/FlyThrough.js`:
```js
#engine v8

#feature-id    Loom Fly-Through : Batch Processing > Loom Fly-Through
#feature-info  Turns a finished astrophoto into a push-in video: the photo's \
               own stars move at their real Gaia distances.

#include <pjsr/UndoFlag.jsh>
#include <pjsr/DataType.jsh>
#include <pjsr/StdButton.jsh>
#include <pjsr/StdIcon.jsh>
#include <pjsr/TextAlign.jsh>
#include <pjsr/Sizer.jsh>
#include <pjsr/StarDetector.jsh>

#ifndef LOOM_LIBS_INCLUDED
#include "lib/Util.js"
#include "lib/Cache.js"
#include "lib/Psb.js"
#include "lib/Steps.js"
#include "lib/Pipeline.js"
#include "lib/Frames.js"
#include "lib/Fly.js"
#include "lib/Sky.js"
#include "lib/Render.js"
#endif

function main()
{
   console.show();
   ( new MessageBox( "Loom Fly-Through is not finished yet.", "Loom Fly-Through",
                     StdIcon_Information, StdButton_Ok ) ).execute();
}

#ifndef LOOM_FLY_UNDER_TEST
main();
#endif
```
Check before relying on it: `<pjsr/Sizer.jsh>` makes a file fail to parse under v8 (known Loom gotcha) — if the parse check in Step 4 fails, remove that include; Sizer classes are built in.

`ci/run-tests.js`: add `"lib/Fly.js", "lib/Sky.js", "lib/Render.js"` to LIBS after `"lib/Frames.js"`; add `"FlyThrough.js"` to the entry-point parse list.
`script/selftest.js`: add `#include "lib/Fly.js"`, `"lib/Sky.js"`, `"lib/Render.js"` after `lib/Frames.js`; after the FrameSelector include add `#define LOOM_FLY_UNDER_TEST 1` and `#include "FlyThrough.js"`.
`ci/repository.sh:69`: `cp script/Loom.js script/FrameSelector.js script/FlyThrough.js "$stage/src/scripts/Loom/"`.
`ci.yml`: after the Loom.js layout check add the same `test -f .../FlyThrough.js` line.

- [ ] **Step 4: Run** both node builds → PASS; parse check lists FlyThrough.js.
- [ ] **Step 5: Commit** — `git add -A script ci .github && git commit -m "Fly-Through: scaffolding and registration"`

---

### Task 2: Exact geometry

**Files:** Modify `script/lib/Fly.js`; test `script/selftest.js`.

**Interfaces:**
- Produces:
  - `Fly.vec( raDeg, decDeg ) -> [x,y,z]` unit vector.
  - `Fly.radec( v ) -> { ra, dec }` degrees, ra in [0,360).
  - `Fly.moved( ra, dec, d, target, s ) -> { ra, dec, front: Boolean, ratio: Number }` — position after the camera moves s pc towards `target` ({ra,dec}); ratio = d/|p|.
  - `Fly.backdropScale( D, s ) -> Number` (1 when D is Infinity).
  - `Fly.tanProject( ra, dec, wcs ) -> { x, y }` with `wcs = { crval1, crval2, crpix1, crpix2, cd: [cd11, cd12, cd21, cd22] }` (FITS 1-based CRPIX; returns 0-based pixel coordinates).

- [ ] **Step 1: Failing tests**

```js
   /* ---- Fly: exact geometry ------------------------------------------- */
   ( function()
   {
      function near( a, b, eps ) { return Math.abs( a - b ) <= ( eps || 1e-9 ); }
      var r = Fly.radec( Fly.vec( 324.745, 57.514 ) );
      check( "vec/radec round trip", near( r.ra, 324.745, 1e-9 ) && near( r.dec, 57.514, 1e-9 ), true );
      var T = { ra: 324.745, dec: 57.514 };
      var on = Fly.moved( 324.745, 57.514, 500, T, 100 );
      check( "on axis: direction unchanged", near( on.ra, 324.745, 1e-9 ) && near( on.dec, 57.514, 1e-9 ), true );
      check( "on axis: ratio is d/(d-s)", near( on.ratio, 500/400, 1e-12 ), true );
      // off axis: a star 1 degree away at 100 pc, camera moves 50 pc
      var off = Fly.moved( 324.745, 58.514, 100, T, 50 );
      var th = Math.PI/180, px = 100*Math.sin( th ), pz = 100*Math.cos( th ) - 50;
      check( "off axis: exact angle from the target",
             near( Fly.separation( off, T ), Math.atan2( px, pz )*180/Math.PI, 1e-9 ), true );
      check( "off axis: exact ratio", near( off.ratio, 100/Math.sqrt( px*px + pz*pz ), 1e-12 ), true );
      check( "a passed star is behind the camera", Fly.moved( 324.745, 57.6, 40, T, 60 ).front, false );
      check( "backdrop K", Fly.backdropScale( 900, 180 ), 900/720 );
      check( "galaxy backdrop fixed", Fly.backdropScale( Infinity, 180 ), 1 );
      var w = { crval1: 324.745, crval2: 57.514, crpix1: 200.5, crpix2: 150.5, cd: [ -0.0005, 0, 0, 0.0005 ] };
      var c = Fly.tanProject( 324.745, 57.514, w );
      check( "TAN: reference point lands on CRPIX (0-based)", near( c.x, 199.5, 1e-9 ) && near( c.y, 149.5, 1e-9 ), true );
      var e = Fly.tanProject( 324.745, 57.514 + 0.05, w );
      check( "TAN: north is +y for a positive CD2_2", e.y > c.y, true );
   } )();
```

- [ ] **Step 2: Run** node → ABORTED `Fly.vec is not a function`.

- [ ] **Step 3: Implement** (append to `Fly.js`):

```js
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
```

- [ ] **Step 4: Run** both builds → PASS. Teeth: replace `ratio: d/len` with `ratio: d/(d - s)`; "off axis: exact ratio" must fail; restore.
- [ ] **Step 5: Commit** — `git commit -am "Fly: exact camera geometry and TAN projection"`

---

### Task 3: Appearance — brightness, growth, opacity

**Files:** Modify `script/lib/Fly.js`; test `script/selftest.js`.

**Interfaces:**
- Produces: `Fly.GROWTH_DEFAULT = 0.15`; `Fly.growth( ratio, growth ) -> g`; `Fly.pixelScale( ratio, growth, brightening ) -> Number` (multiplier on sprite pixels after resampling by g; total light ∝ ratio² when brightening); `Fly.opacity( ratio, front ) -> [0,1]`.

- [ ] **Step 1: Failing tests**

```js
   ( function()
   {
      function near( a, b ) { return Math.abs( a - b ) < 1e-12; }
      check( "no motion, no growth", Fly.growth( 1, 0.15 ), 1 );
      check( "growth rule", near( Fly.growth( 3, 0.15 ), 1.3 ), true );
      // total light = pixelScale * g^2 * (original total) must equal ratio^2
      var g = Fly.growth( 2.5, 0.2 );
      check( "integrated flux follows inverse square whatever the growth",
             near( Fly.pixelScale( 2.5, 0.2, true )*g*g, 2.5*2.5 ), true );
      check( "brightening off keeps total light", near( Fly.pixelScale( 2.5, 0.2, false )*g*g, 1 ), true );
      check( "opacity 1 far away", Fly.opacity( 2, true ), 1 );
      check( "opacity 0 before the singularity", Fly.opacity( 25, true ), 0 );
      var prev = 1, mono = true;
      for ( var r = 1; r <= 25; r += 0.25 ) { var o = Fly.opacity( r, true ); if ( o > prev + 1e-12 ) mono = false; prev = o; }
      check( "opacity never rises as a star nears", mono, true );
      check( "never drawn behind the camera", Fly.opacity( 1.5, false ), 0 );
   } )();
```

- [ ] **Step 2: Run** → ABORTED `Fly.growth is not a function`.

- [ ] **Step 3: Implement**

```js
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

/* Stars fade out as they near the camera, and are never drawn once passed. */
Fly.opacity = function( ratio, front )
{
   if ( !front )
      return 0;
   return 1 - Fly.smoothstep( 8, 20, ratio );
};
```

- [ ] **Step 4: Run** → PASS. Teeth: drop `/(g*g)`; "integrated flux…" fails; restore.
- [ ] **Step 5: Commit** — `git commit -am "Fly: inverse-square flux, growth, opacity"`

---

### Task 4: Usable parallax

**Files:** Modify `script/lib/Fly.js`; test `script/selftest.js`.

**Interfaces:**
- Produces: `Fly.PARALLAX_ZERO_POINT = 0.017` (mas, added to the catalogue parallax); `Fly.parallaxSigma( G ) -> mas`; `Fly.usableParallax( source ) -> Number|null` (corrected parallax when ≥ 5σ and > 0, else null). `source` = `{ ra, dec, plx, pmra, pmdec, G, BP, RP }`.

- [ ] **Step 1: Failing tests**

```js
   ( function()
   {
      function near( a, b ) { return Math.abs( a - b ) < 1e-9; }
      check( "sigma at bright end", near( Fly.parallaxSigma( 12 ), 0.02 ), true );
      check( "sigma at G 17", near( Fly.parallaxSigma( 17 ), 0.07 ), true );
      check( "sigma interpolates", Fly.parallaxSigma( 16.5 ) > 0.02 && Fly.parallaxSigma( 16.5 ) < 0.07, true );
      check( "sigma clamps faint", near( Fly.parallaxSigma( 19 ), 0.1 ), true );
      check( "zero point added", near( Fly.usableParallax( { plx: 1.0, G: 12 } ), 1.017 ), true );
      check( "below 5 sigma is not usable", Fly.usableParallax( { plx: 0.30, G: 17 } ), null );
      check( "negative is not usable", Fly.usableParallax( { plx: -0.5, G: 10 } ), null );
      check( "missing is not usable", Fly.usableParallax( { plx: null, G: 10 } ), null );
   } )();
```

- [ ] **Step 2: Run** → ABORTED.

- [ ] **Step 3: Implement**

```js
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
```

- [ ] **Step 4: Run** → PASS. Teeth: change `5*` to `1*`; "below 5 sigma" fails; restore.
- [ ] **Step 5: Commit** — `git commit -am "Fly: usable parallax from DR3's uncertainty by magnitude"`

---

### Task 5: Cluster distance

**Files:** Modify `script/lib/Fly.js`; test `script/selftest.js`.

**Interfaces:**
- Consumes: `Fly.usableParallax`, `Fly.parallaxSigma`, `Fly.separation`.
- Produces: `Fly.findCluster( sources, target, radiusDeg ) -> { distance, members, lo, hi } | null`. `sources` as in Task 4; `target` {ra,dec}.

- [ ] **Step 1: Failing tests** (constructed data with a seeded generator):

```js
   ( function()
   {
      function rng( seed ) { var a = seed >>> 0; return function() { a = ( a*1664525 + 1013904223 ) >>> 0; return a/4294967296; }; }
      function gauss( r ) { return Math.sqrt( -2*Math.log( Math.max( 1e-12, r() ) ) )*Math.cos( 2*Math.PI*r() ); }
      var r = rng( 7 ), T = { ra: 100, dec: 20 }, src = [];
      for ( var i = 0; i < 1500; ++i )                   // field: broad pm, parallax 0.2..3
      {
         src.push( { ra: 100 + ( r() - 0.5 )*6, dec: 20 + ( r() - 0.5 )*6, plx: 0.2 + 2.8*r(),
                     pmra: 6*gauss( r ), pmdec: 6*gauss( r ), G: 11 + 4*r() } );
      }
      for ( var k = 0; k < 80; ++k )                     // cluster: 1.1 mas, tight pm, inside 0.8 deg
         src.push( { ra: 100 + ( r() - 0.5 )*1.2, dec: 20 + ( r() - 0.5 )*1.2, plx: 1.1 + 0.03*gauss( r ),
                     pmra: -2.4 + 0.2*gauss( r ), pmdec: -4.6 + 0.2*gauss( r ), G: 11 + 4*r() } );
      var c = Fly.findCluster( src, T, 1.0 );
      check( "a constructed cluster is found", c != null, true );
      check( "at its distance", c != null && Math.abs( c.distance - 1000/1.117 ) < 40, true );
      check( "with its members", c != null && c.members >= 60, true );
      var none = Fly.findCluster( src.slice( 0, 1500 ), T, 1.0 );
      check( "a uniform field has no cluster", none, null );
   } )();
```

- [ ] **Step 2: Run** → ABORTED.

- [ ] **Step 3: Implement**

```js
Fly.CLUSTER_MIN_MEMBERS = 30;
Fly.CLUSTER_MIN_CONTRAST = 3;

Fly.mad = function( v )
{
   var s = v.slice().sort( function( a, b ) { return a - b; } ), m = s[s.length >> 1];
   var d = v.map( function( x ) { return Math.abs( x - m ); } ).sort( function( a, b ) { return a - b; } );
   return 1.4826*d[d.length >> 1] || 1;
};

/*
 * The ionising cluster, by shared motion and distance. Inside = within the
 * target's radius, field = annulus 1.5-3 radii. Coordinates are pm scaled
 * by the field's MAD and parallax scaled by its own uncertainty. The inside
 * star whose neighbourhood (radius 1 in scaled units) is most over-dense
 * against the field seeds the clump.
 */
Fly.findCluster = function( sources, target, radiusDeg )
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
   if ( inside.length < Fly.CLUSTER_MIN_MEMBERS || field.length < Fly.CLUSTER_MIN_MEMBERS )
      return null;
   var sa = Fly.mad( field.map( function( x ) { return x.a; } ) );
   var sd = Fly.mad( field.map( function( x ) { return x.d; } ) );
   function near( c, x ) { var da = ( x.a - c.a )/sa, dd = ( x.d - c.d )/sd, dp = ( x.p - c.p )/Math.max( c.sp, x.sp );
                           return da*da + dd*dd + dp*dp <= 1; }
   var scale = inside.length/field.length, best = null;
   inside.forEach( function( c )
   {
      var nIn = inside.filter( function( x ) { return near( c, x ); } ).length;
      var nField = field.filter( function( x ) { return near( c, x ); } ).length*scale;
      var contrast = nIn/Math.max( 1, nField );
      if ( best == null || contrast > best.contrast ) best = { seed: c, contrast: contrast };
   } );
   var members = inside.filter( function( x ) { return near( best.seed, x ); } )
                       .map( function( x ) { return x.p; } ).sort( function( a, b ) { return a - b; } );
   if ( members.length < Fly.CLUSTER_MIN_MEMBERS || best.contrast < Fly.CLUSTER_MIN_CONTRAST )
      return null;
   var q = function( f ) { return members[Math.min( members.length - 1, Math.floor( f*members.length ) )]; };
   return { distance: 1000/q( 0.5 ), members: members.length, lo: 1000/q( 0.75 ), hi: 1000/q( 0.25 ) };
};
```

- [ ] **Step 4: Run** → PASS. Teeth: set `CLUSTER_MIN_CONTRAST = 0`; "uniform field has no cluster" fails; restore.
- [ ] **Step 5: Commit** — `git commit -am "Fly: nebula distance from its cluster (motion + parallax over-density)"`

---

### Task 6: Recorded IC 1396 fixture and regression

**Files:** Create `ci/fixtures/ic1396-gaia-g16.tsv`; test `script/selftest.js`.

**Interfaces:**
- Consumes: `Fly.findCluster`. `LOOM_DIR` (selftest.js:52) locates the repository.
- Produces: the checked-in fixture; `Fly.parseSources( text ) -> [source]` (tab-separated header `ra dec plx pmra pmdec G BP RP`).

- [ ] **Step 1: Record the fixture once** (maintainer machine; the suite never repeats this). From the probe output already in scratch, keep the needed columns:

```bash
mkdir -p ci/fixtures && cp /tmp/agent-scratch/gaia-field.tsv ci/fixtures/ic1396-gaia-g16.tsv
head -2 ci/fixtures/ic1396-gaia-g16.tsv; wc -l ci/fixtures/ic1396-gaia-g16.tsv   # header + 8092 rows
```
Add a `ci/fixtures/README.md`: "ic1396-gaia-g16.tsv: Gaia DR3 sources within 1.4 deg of IC 1396 (324.745, +57.514), G < 16, recorded 2026-09-23 with the Gaia process; used by the cluster regression test."

- [ ] **Step 2: Failing test**

```js
   ( function()
   {
      var path = LOOM_DIR + "/../ci/fixtures/ic1396-gaia-g16.tsv";
      check( "the IC 1396 fixture is in the repository", File.exists( path ), true );
      var src = Fly.parseSources( File.readTextFile( path ) );
      check( "fixture parses", src.length > 8000, true );
      var c = Fly.findCluster( src, { ra: 324.745, dec: 57.514 }, 85/60 );   // NGC/IC diameter 170'
      check( "IC 1396's cluster gives 870-1000 pc: " + ( c && c.distance ),
             c != null && c.distance >= 870 && c.distance <= 1000, true );
   } )();
```

- [ ] **Step 3: Run** → ABORTED `Fly.parseSources is not a function`.

- [ ] **Step 4: Implement**

```js
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
```

- [ ] **Step 5: Run** → PASS. If the distance lands outside 870–1000, do not loosen the range: the fixture's cluster radius 85' exceeds most images; the regression is about the finder, keep the published window. Diagnose the finder (print seed and contrast) and fix it, re-running Task 5's tests.
- [ ] **Step 6: Commit** — `git add ci/fixtures script && git commit -m "Fly: recorded IC 1396 Gaia fixture; cluster regression"`

---

### Task 7: Camera path, pacing, presets, draft budget

**Files:** Modify `script/lib/Fly.js`; test `script/selftest.js`.

**Interfaces:**
- Produces:
  - `Fly.defaultTravel( type, D, medianStarDistance ) -> pc` (nebula 0.2 D, galaxy 200).
  - `Fly.clampTravel( travel, type, D ) -> { travel, clamped: Boolean }` (nebula ≤ 0.9 D).
  - `Fly.ease( t, mode )` mode "smoothstep" | "linear".
  - `Fly.timeAt( frame, frames, pingPong ) -> t ∈ [0,1]`.
  - `Fly.frameCount( duration, fps, pingPong ) -> Number`.
  - `Fly.draftPlan( duration, fps, longSide, aspect ) -> { width, height, fps, frames, bytes }` (≤ 400 MB).
  - `Fly.PRESETS = { social_vertical: [1080,1920], social_square: [1080,1080], youtube_4k: [3840,2160], youtube_1080: [1920,1080], exhibition: [3840,2160] }`.
  - `Fly.presetCrop( imgW, imgH, tx, ty, outW, outH ) -> { x, y, w, h }` (largest rectangle of the output aspect that fits, centred on the target, shifted inward).

- [ ] **Step 1: Failing tests**

```js
   ( function()
   {
      check( "nebula travel default", Fly.defaultTravel( "nebula", 900 ), 180 );
      check( "galaxy travel default", Fly.defaultTravel( "galaxy", Infinity ), 200 );
      check( "travel cap", Fly.clampTravel( 950, "nebula", 900 ), { travel: 810, clamped: true } );
      check( "galaxy never capped", Fly.clampTravel( 950, "galaxy", Infinity ), { travel: 950, clamped: false } );
      check( "smoothstep ends at rest",
             Math.abs( Fly.ease( 0.001, "smoothstep" ) - Fly.ease( 0, "smoothstep" ) ) < 1e-5, true );
      check( "frames", Fly.frameCount( 10, 30, false ), 300 );
      check( "ping-pong doubles", Fly.frameCount( 10, 30, true ), 600 );
      check( "ping-pong turns at the middle", Fly.timeAt( 300, 600, true ), 1 );
      check( "ping-pong returns", Fly.timeAt( 599, 600, true ) < 0.01, true );
      // velocity continuity at the turns: smoothstep has zero slope at 0 and 1
      var v = function( t ) { return ( Fly.ease( t + 1e-4, "smoothstep" ) - Fly.ease( t, "smoothstep" ) )/1e-4; };
      check( "no velocity jump at the far turn", Math.abs( v( 1 - 1e-4 ) ) < 1e-3, true );
      var dp = Fly.draftPlan( 120, 60, 480, 16/9 );
      check( "draft stays under 400 MB for any duration", dp.bytes <= 400e6, true );
      check( "and keeps the clip length", Math.abs( dp.frames/dp.fps - 120 ) < 1, true );
      var c = Fly.presetCrop( 6000, 4000, 5900, 2000, 1080, 1920 );
      check( "vertical crop keeps the aspect", Math.abs( c.w/c.h - 1080/1920 ) < 1e-3, true );
      check( "and stays inside the image", c.x >= 0 && c.x + c.w <= 6000 && c.y >= 0 && c.y + c.h <= 4000, true );
   } )();
```

- [ ] **Step 2: Run** → ABORTED.

- [ ] **Step 3: Implement**

```js
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

Fly.draftPlan = function( duration, fps, longSide, aspect )
{
   var w = ( aspect >= 1 ) ? longSide : Math.round( longSide*aspect );
   var h = ( aspect >= 1 ) ? Math.round( longSide/aspect ) : longSide;
   var per = w*h*4, want = Math.min( fps, 30 ), frames = Math.round( duration*want );
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
```

- [ ] **Step 4: Run** → PASS. Teeth: remove the `frames*per >` branch; the 400 MB test fails; restore.
- [ ] **Step 5: Commit** — `git commit -am "Fly: camera path, ping-pong, presets, draft budget"`

---

### Task 8: NGC/IC target

**Files:** Modify `script/lib/Fly.js`; test `script/selftest.js`.

**Interfaces:**
- Produces: `Fly.parseNgcIc( text ) -> [{ id, ra, dec, diameter (arcmin), name, pgc }]`; `Fly.pickTarget( entries, centre {ra,dec}, fieldRadiusDeg ) -> { best, runnerUp } ` (entries null when none in field); `Fly.targetType( entry ) -> "galaxy" | "nebula"`.

- [ ] **Step 1: Failing tests**

```js
   ( function()
   {
      var csv = "id,alpha,delta,magnitude,diameter,axisRatio,posAngle,Common name,PGC,PGC2,Messier\n" +
                "IC1396,324.745000,57.514000,3.50,170.00,,,,,,\n" +
                "NGC7129,325.775,66.113,11.5,7.0,,,,,,\n" +
                "IC5146,328.36,47.27,7.2,12,,,Cocoon,,,\n" +
                "NGC224,10.6847,41.269,3.4,190,,,Andromeda,PGC2557,,M31\n";
      var e = Fly.parseNgcIc( csv );
      check( "ngc/ic parses", e.length, 4 );
      var p = Fly.pickTarget( e, { ra: 324.7, dec: 57.5 }, 1.5 );
      check( "the big nearby nebula wins", p.best.id, "IC1396" );
      check( "a nebula", Fly.targetType( p.best ), "nebula" );
      check( "a PGC number makes a galaxy", Fly.targetType( e[3] ), "galaxy" );
      var none = Fly.pickTarget( e, { ra: 200, dec: -30 }, 1 );
      check( "no target in the field", none.best, null );
   } )();
```

- [ ] **Step 2: Run** → ABORTED.

- [ ] **Step 3: Implement**

```js
Fly.parseNgcIc = function( text )
{
   var lines = String( text ).split( /\r?\n/ ), out = [];
   for ( var i = 1; i < lines.length; ++i )
   {
      var f = lines[i].split( "," );
      if ( f.length < 9 || !f[0] ) continue;
      out.push( { id: f[0], ra: parseFloat( f[1] ), dec: parseFloat( f[2] ),
                  diameter: parseFloat( f[4] ) || 0, name: f[7] || "", pgc: f[8] || "" } );
   }
   return out;
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
```

- [ ] **Step 4: Run** → PASS. Teeth: drop the separation division; "the big nearby nebula wins" still passes? If it does, add an entry `IC9999` 200' across 1.4° away and assert IC1396 still wins; confirm that test fails without the separation term.
- [ ] **Step 5: Commit** — `git commit -am "Fly: NGC/IC target and type"`

---

### Task 9: `Steps.removeStars` linear flag

**Files:** Modify `script/lib/Steps.js:3107-3122`; test `script/selftest.js`.

**Interfaces:**
- Produces: `Steps.removeStars( window, tool, label, linear )`; `linear` omitted → true (existing Loom calls unchanged); only StarNet2 reads it.

- [ ] **Step 1: Failing test** (source-level, as Loom's other process-parameter guards do):

```js
   check( "removeStars passes linear through to StarNet2 only",
          /P\.linear = \( linear !== false \);/.test( Steps.removeStars.toString() ), true );
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**: change the signature to `Steps.removeStars = function( window, tool, label, linear )` and replace `P.linear = true;` with

```js
      // Loom's composites are linear; Fly-Through's input is stretched.
      // Omitted means linear, so every existing call is unchanged.
      P.linear = ( linear !== false );
```
- [ ] **Step 4: Run** both builds → PASS; then the PixInsight suite (Loom's own StarNet2 path must still pass).
- [ ] **Step 5: Commit** — `git commit -am "Steps.removeStars: optional linear flag for StarNet2"`

---

### Task 10: Sky — catalogue, projection, star layers, sprites

**Files:** Modify `script/lib/Sky.js`; test `script/selftest.js` (IN_PIXINSIGHT blocks; `synthFrame` already exists in selftest).

**Interfaces:**
- Consumes: `Fly.parseNgcIc`, `Fly.tanProject`, `Fly.usableParallax`, `Steps.removeStars`, `Steps.deriveStarsByUnscreen`, `Steps.availableStarTools`, `StarDetector`.
- Produces:
  - `Sky.querySources( centre, radiusDeg ) -> [source]` — replaceable; throws `Error("configure a Gaia DR3 database in Process > Gaia")` on failure or empty result.
  - `Sky.readNgcIc() -> [entry] | null` from `CoreApplication.srcDirPath + "/scripts/AdP/NGC-IC.csv"`.
  - `Sky.projector( window ) -> function( ra, dec ) -> {x,y}` (celestialToImage when `window.hasAstrometricSolution`, else TAN keywords via `Fly.tanProject`; null when neither).
  - `Sky.field( window, project ) -> { centre, radiusDeg }`.
  - `Sky.splitStars( window, tool ) -> { starless: ImageWindow, stars: ImageWindow }`.
  - `Sky.sprites( starsImage, placed ) -> { sprites: [{ source, rect, pixels:Float32Array[], centre{x,y} }], residualMask: Uint8Array }`.

- [ ] **Step 1: Failing PixInsight tests** (add a TAN-keyword writer next to `synthFrame` in selftest):

```js
function withTanKeywords( window, ra, dec, scaleDeg )
{
   var w = window.mainView.image.width, h = window.mainView.image.height;
   window.keywords = window.keywords.concat( [
      new FITSKeyword( "CTYPE1", "'RA---TAN'", "" ), new FITSKeyword( "CTYPE2", "'DEC--TAN'", "" ),
      new FITSKeyword( "CRVAL1", String( ra ), "" ), new FITSKeyword( "CRVAL2", String( dec ), "" ),
      new FITSKeyword( "CRPIX1", String( w/2 + 0.5 ), "" ), new FITSKeyword( "CRPIX2", String( h/2 + 0.5 ), "" ),
      new FITSKeyword( "CD1_1", String( -scaleDeg ), "" ), new FITSKeyword( "CD1_2", "0", "" ),
      new FITSKeyword( "CD2_1", "0", "" ), new FITSKeyword( "CD2_2", String( scaleDeg ), "" ) ] );
}
```

```js
   if ( IN_PIXINSIGHT ) ( function()
   {
      check( "NGC/IC is found in the core install", ( Sky.readNgcIc() || [] ).length > 9000, true );
      var path = synthFrame( synthDir( "fly-sky" ) + "/field.xisf", { fwhm: 3, background: 0.05, noise: 0.002, seed: 9 } );
      var w = ImageWindow.open( path )[0];
      try
      {
         withTanKeywords( w, 324.745, 57.514, 0.0005 );
         var proj = Sky.projector( w );
         var c = proj( 324.745, 57.514 );
         check( "projector: keywords give the reference pixel", Math.abs( c.x - 399.5 ) < 1e-6 && Math.abs( c.y - 299.5 ) < 1e-6, true );
         var f = Sky.field( w, proj );
         check( "field radius from the corners", f.radiusDeg > 0.2 && f.radiusDeg < 0.3, true );
         var none = ImageWindow.open( path )[0];
         check( "no keywords, no solution: no projector", Sky.projector( none ), null );
         none.forceClose();
         var saved = Sky.querySources;
         Sky.querySources = function() { return []; };
         var threw = false;
         try { Sky.requireSources( { ra: 0, dec: 0 }, 1 ); } catch ( e ) { threw = /Gaia/.test( String( e ) ); }
         Sky.querySources = saved;
         check( "an empty query stops with the configure message", threw, true );
      }
      finally { w.forceClose(); }
   } )();
```
(`Sky.requireSources` wraps `querySources` and throws on an empty result; include it in the implementation.)

Sprites test on a generated field: take `synthFrame` stars, build `placed` = detected star positions (the generator's star list is seeded; call `new StarDetector().stars( image )` on the synthetic image and use the first 20 as placed with fake sources), then check: sprite rects contain their centres; sprite pixel masks and residual mask are disjoint and cover every pixel (`count(sprite) + count(residual) == w*h`); a detection with `nmax > 1` is not a sprite.

- [ ] **Step 2: Run the PixInsight suite** → FAIL (`Sky.readNgcIc is not a function`).

- [ ] **Step 3: Implement** (key parts, complete):

```js
Sky.NGC_IC_RELATIVE = "/scripts/AdP/NGC-IC.csv";

Sky.readNgcIc = function()
{
   var p = CoreApplication.srcDirPath + Sky.NGC_IC_RELATIVE;
   try { return File.exists( p ) ? Fly.parseNgcIc( File.readTextFile( p ) ) : null; }
   catch ( e ) { return null; }
};

/* The configured Gaia database, as PixInsight's own scripts use it. */
Sky.querySources = function( centre, radiusDeg )
{
   var G = new Gaia;
   G.command = "search";
   G.centerRA = centre.ra; G.centerDec = centre.dec; G.radius = radiusDeg;
   G.magnitudeHigh = 17.6; G.generateTextOutput = false; G.verbosity = 0;
   if ( !G.executeGlobal() )
      return [];
   return G.sources.map( function( s )
   {
      return { ra: s[0], dec: s[1], plx: s[2], pmra: s[3], pmdec: s[4], G: s[5], BP: s[6], RP: s[7] };
   } );
};

Sky.requireSources = function( centre, radiusDeg )
{
   var s = Sky.querySources( centre, radiusDeg );
   if ( !s || s.length == 0 )
      throw new Error( "No Gaia stars for this field: configure a Gaia DR3 database in Process > Gaia" );
   return s;
};

Sky.keywordNumber = function( window, name )
{
   var k = window.keywords.filter( function( x ) { return x.name == name; } )[0];
   return k ? parseFloat( String( k.value ).replace( /'/g, "" ) ) : null;
};

Sky.projector = function( window )
{
   if ( window.hasAstrometricSolution )
      return function( ra, dec ) { var p = window.celestialToImage( ra, dec ); return { x: p.x, y: p.y }; };
   var n = function( k ) { return Sky.keywordNumber( window, k ); };
   var wcs = { crval1: n( "CRVAL1" ), crval2: n( "CRVAL2" ), crpix1: n( "CRPIX1" ), crpix2: n( "CRPIX2" ),
               cd: [ n( "CD1_1" ), n( "CD1_2" ), n( "CD2_1" ), n( "CD2_2" ) ] };
   if ( [ wcs.crval1, wcs.crval2, wcs.crpix1, wcs.crpix2 ].concat( wcs.cd ).some( function( v ) { return v == null || !isFinite( v ); } ) )
      return null;
   return function( ra, dec ) { return Fly.tanProject( ra, dec, wcs ); };
};
```

`Sky.field( window, project )`: centre = the image centre's celestial position — with a solution, `window.imageToCelestial( w/2, h/2 )`; with keywords, CRVAL (the keyword writer centres CRPIX); radius = max separation from the centre to the four corners, corners via `imageToCelestial` when solved, else via the inverse of the keyword CD matrix (compute pixel offsets × CD in degrees, which is exact enough for the radius).

`Sky.splitStars( window, tool )`:
```js
Sky.splitStars = function( window, tool )
{
   var img = window.mainView.image;
   var starless = new ImageWindow( img.width, img.height, img.numberOfChannels, 32, true,
                                   img.isColor, Util.freeWindowId( "fly_starless" ) );
   starless.mainView.beginProcess( UndoFlag_NoSwapFile );
   starless.mainView.image.assign( img );
   starless.mainView.endProcess();
   Steps.removeStars( starless, tool, "fly-through", false /*stretched*/ );
   var stars = new ImageWindow( img.width, img.height, img.numberOfChannels, 32, true,
                                img.isColor, Util.freeWindowId( "fly_stars" ) );
   stars.mainView.beginProcess( UndoFlag_NoSwapFile );
   stars.mainView.image.assign( img );
   stars.mainView.endProcess();
   Steps.deriveStarsByUnscreen( stars, starless );   // stars := unscreen(image, starless), in place
   return { starless: starless, stars: stars };
};
```
Check `Steps.deriveStarsByUnscreen`'s contract before relying on "in place": it runs PixelMath on `originalWindow.mainView` and returns it — so passing a copy (`stars`) as the original is what makes it in place.

`Sky.sprites( starsImage, placed, fwhm )`: run `new StarDetector().stars( starsImage )`; for each placed source (projected {x,y}), the detection within 1.5 px; skip if `nmax > 1` or its grown rect (rect expanded by `fwhm`) intersects another detection's rect; sprite pixels = the grown rect's pixels, other detections inside masked out (left to the residual); `residualMask[i] = 1` for every pixel not in a sprite. Return sprites with `pixels` per channel (`getSamples` into Float32Array per channel, rect-bounded) and the mask.

- [ ] **Step 4: Run** node both builds and the PixInsight suite → PASS.
- [ ] **Step 5: Commit** — `git commit -am "Sky: Gaia and NGC/IC at run time, projection, star split, sprites"`

---

### Task 11: Render — compositing, frame 0, TIFF, benchmark

**Files:** Modify `script/lib/Render.js`; test `script/selftest.js`.

**Interfaces:**
- Consumes: Tasks 2–3, 7, 10.
- Produces:
  - `Render.scene( { starless, stars, sprites, residualMask, project, target, D, placed } )` → scene object.
  - `Render.frame( scene, t, opts, outW, outH, crop ) -> Image` (32-bit float RGB) where `opts = { travel, easing, growth, brightening }`.
  - `Render.writeTiff( image, path )` 16-bit TIFF.
  - `Render.benchmark( scene, outW, outH, n ) -> ms per frame`.

Frame algorithm (in the unscreened star layer):
1. s = travel × ease(t). K = Fly.backdropScale(D, s).
2. Backdrop: starless and residual (stars × residualMask) each resampled by K about the target pixel into the crop (bicubic; `Image.resample` on a copy then crop around the scaled target).
3. For each sprite: `m = Fly.moved(src.ra, src.dec, d, target, s)`; α = `Fly.opacity(m.ratio, m.front)`; if α = 0 skip; position = `project(m.ra, m.dec)` mapped into the crop; g = `Fly.growth(m.ratio, growth)`; pixels resampled by g × K-independent (sprites are not scaled by K), multiplied by `Fly.pixelScale(m.ratio, growth, brightening) × α`, added into the star-layer accumulator at the position.
4. Output = screen(backdropStarless, accumulator + residualScaled) = 1 − (1 − S)(1 − Tsum).

- [ ] **Step 1: Failing PixInsight tests** (generated field with TAN keywords from Task 10; `placed` from injected sources at the detected star positions with distances 100–2000 pc):

```js
   // frame 0 is the input
   var f0 = Render.frame( scene, 0, opts, W, H, { x: 0, y: 0, w: W, h: H } );
   check( "frame 0 equals the input within 1 in 16 bits", maxAbsDiff( f0, input ) <= 1/65535, true );
   // a sprite lands at the exact projection
   var fT = Render.frame( scene, 1, opts, W, H, { x: 0, y: 0, w: W, h: H } );
   var m = Fly.moved( s0.ra, s0.dec, s0.d, target, opts.travel ), p = project( m.ra, m.dec );
   check( "a sprite moves to its exact projection", peakNear( fT, p, 1.5 ), true );
   // galaxy mode: backdrop fixed
   var gscene = Render.scene( Object.assign( {}, sceneArgs, { D: Infinity } ) );
   check( "galaxy backdrop does not move", backdropDiff( Render.frame( gscene, 1, opts, W, H, full ), input ) < 1/1000, true );
   // a passed star is gone
   check( "a star the camera passes is never drawn", starGone( scene, nearStar, opts ), true );
   // TIFF is 16-bit on read-back
   Render.writeTiff( f0, dir + "/f0.tif" );
   check( "frames are 16-bit TIFF", ImageWindow.open( dir + "/f0.tif" )[0].mainView.image.bitsPerSample, 16 );
```
Define the helpers `maxAbsDiff`, `peakNear`, `backdropDiff`, `starGone` in the same block (sample comparisons via `getSamples`).

- [ ] **Step 2: Run** the PixInsight suite → FAIL.
- [ ] **Step 3: Implement** `Render.scene`, `Render.frame` per the algorithm, and:

```js
Render.writeTiff = function( image, path )
{
   var w = new ImageWindow( image.width, image.height, image.numberOfChannels, 16, false,
                            image.isColor, Util.freeWindowId( "fly_frame" ) );
   try
   {
      w.mainView.beginProcess( UndoFlag_NoSwapFile );
      w.mainView.image.assign( image );
      w.mainView.endProcess();
      w.saveAs( path, false, false, false, false );
   }
   finally { w.forceClose(); }
};
```
- [ ] **Step 4: Benchmark** — in the suite, a generated 8 MP backdrop (`synthFrame` 3464×2309) with 2,000 injected sprites, `Render.benchmark(scene, 3840, 2160, 5)`; record the ms/frame in the check's name (it always passes if the frames render) and in `docs/verified-parameters.md` under "Fly-Through render time".
- [ ] **Step 5: Run** → PASS. Commit — `git commit -am "Render: compositing, exact frame 0, 16-bit TIFF, benchmark"`

---

### Task 12: ffmpeg — discovery, formats, encoding

**Files:** Modify `script/lib/Fly.js`, `script/lib/Render.js`; test `script/selftest.js`.

**Interfaces:**
- Produces:
  - `Fly.VIDEO_FORMATS` — ordered list of `{ id, label, ext, encoder }`: `h264` "MP4 · H.264" mp4 libx264; `hevc` "MP4 · H.265/HEVC" mp4 libx265; `prores` "MOV · ProRes 422 HQ" mov prores_ks; `vp9` "WebM · VP9" webm libvpx-vp9.
  - `Fly.availableFormats( encodersText ) -> [format]` — those whose encoder name appears in `ffmpeg -encoders` output.
  - `Fly.defaultFormat( formats, width ) -> id` — hevc for width > 1920 when available, else h264, else the first.
  - `Fly.ffmpegArgs( framesDir, fps, outBase, formatId, quality ) -> [String]` — quality "high" | "standard"; output `outBase + "." + ext`.
  - `Fly.ffmpegCandidates( platform, home, pathVar, saved ) -> [String]` — ordered, de-duplicated absolute paths to try; `platform` is `"windows"`, `"macos"` or `"unix"`.
  - `Fly.ffmpegInstallHint( platform ) -> String`.
  - `Fly.commandLine( program, args, platform ) -> String` — the command shown when not encoding; double-quotes any argument with a space or shell metacharacter (cmd.exe form on Windows, POSIX single quotes elsewhere).
  - `Render.runProcess( program, args, deadlineMs, isCancelled ) -> { exitCode, output }` — output is stdout + stderr, read through the data callbacks while pumping events; `exitCode` -1 on deadline or cancel (process terminated).
  - `Render.findFfmpeg() -> path|null`; `Render.ffmpegEncoders( path ) -> String`; `Render.encode( path, args, isCancelled ) -> Boolean`.

- [ ] **Step 1: Failing node tests**

```js
   ( function()
   {
      var enc = " V....D libx264  H.264\n V....D libx265  H.265\n V....D prores_ks ProRes\n";
      var av = Fly.availableFormats( enc );
      check( "only what this ffmpeg can encode", av.map( function( f ) { return f.id; } ), [ "h264", "hevc", "prores" ] );
      check( "4K defaults to HEVC", Fly.defaultFormat( av, 3840 ), "hevc" );
      check( "1080p defaults to H.264", Fly.defaultFormat( av, 1920 ), "h264" );
      check( "no HEVC: 4K falls back to H.264",
             Fly.defaultFormat( Fly.availableFormats( " libx264 " ), 3840 ), "h264" );
      var a = Fly.ffmpegArgs( "/f", 25, "/o/youtube", "h264", "high" );
      check( "frames pattern and rate", a.slice( 0, 5 ), [ "-y", "-framerate", "25", "-i", "/f/frame_%05d.tif" ] );
      check( "H.264 high is CRF 18", a.join( " " ).indexOf( "-c:v libx264 -crf 18" ) >= 0, true );
      check( "output gets the format's extension", a[a.length - 1], "/o/youtube.mp4" );
      check( "HEVC is tagged for Apple players",
             Fly.ffmpegArgs( "/f", 30, "/o/x", "hevc", "standard" ).join( " " ).indexOf( "-tag:v hvc1" ) >= 0, true );
      var pr = Fly.ffmpegArgs( "/f", 30, "/o/x", "prores", "high" ).join( " " );
      check( "ProRes 422 HQ, 10-bit 4:2:2, .mov", /prores_ks -profile:v 3 .*yuv422p10le.* \/o\/x\.mov$/.test( pr ), true );
      check( "VP9 standard CRF", Fly.ffmpegArgs( "/f", 30, "/o/x", "vp9", "standard" ).join( " " ).indexOf( "-crf 32 -b:v 0" ) >= 0, true );
   } )();

   ( function()
   {
      var mac = Fly.ffmpegCandidates( "macos", "/Users/u", "/usr/bin:/opt/x/bin:", "" );
      check( "macOS order: Homebrew, /usr/local, /usr/bin, then PATH (deduplicated, empty entries dropped)", mac,
             [ "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg", "/opt/x/bin/ffmpeg" ] );
      check( "a saved path is tried first", Fly.ffmpegCandidates( "macos", "/Users/u", "", "/s/ffmpeg" )[0], "/s/ffmpeg" );

      var win = Fly.ffmpegCandidates( "windows", "C:/Users/u",
                   "C:\\Windows\\system32;\"C:\\Tools\\ffmpeg\\bin\";;C:\\Tools\\ffmpeg\\bin\\", "" );
      check( "winget link first", win[0], "C:/Users/u/AppData/Local/Microsoft/WinGet/Links/ffmpeg.exe" );
      check( "Windows fixed locations, in order", win.slice( 1, 5 ),
             [ "C:/ProgramData/chocolatey/bin/ffmpeg.exe", "C:/Users/u/scoop/shims/ffmpeg.exe",
               "C:/ffmpeg/bin/ffmpeg.exe", "C:/Program Files/ffmpeg/bin/ffmpeg.exe" ] );
      check( "PATH on Windows: split on ';', quotes and trailing slash stripped, '/' separators, .exe, no duplicates",
             win.slice( 5 ), [ "C:/Windows/system32/ffmpeg.exe", "C:/Tools/ffmpeg/bin/ffmpeg.exe" ] );
      check( "Windows without a home skips the per-user locations",
             Fly.ffmpegCandidates( "windows", "", "", "" )[0], "C:/ProgramData/chocolatey/bin/ffmpeg.exe" );

      check( "install hint, Windows", Fly.ffmpegInstallHint( "windows" ).indexOf( "winget install Gyan.FFmpeg" ) >= 0, true );
      check( "install hint, macOS", Fly.ffmpegInstallHint( "macos" ).indexOf( "brew install ffmpeg" ) >= 0, true );

      check( "shown command, Windows: double quotes around spaces",
             Fly.commandLine( "C:/Program Files/ffmpeg/bin/ffmpeg.exe", [ "-i", "D:/My Frames/frame_%05d.tif" ], "windows" ),
             "\"C:/Program Files/ffmpeg/bin/ffmpeg.exe\" -i \"D:/My Frames/frame_%05d.tif\"" );
      check( "shown command, macOS: single quotes, bare when safe",
             Fly.commandLine( "/opt/homebrew/bin/ffmpeg", [ "-y", "/Volumes/My Disk/x.mp4" ], "macos" ),
             "/opt/homebrew/bin/ffmpeg -y '/Volumes/My Disk/x.mp4'" );
   } )();
```

- [ ] **Step 2: Run** → ABORTED.

- [ ] **Step 3: Implement**

```js
Fly.VIDEO_FORMATS = [
   { id: "h264",   label: "MP4 \u00b7 H.264",          ext: "mp4",  encoder: "libx264" },
   { id: "hevc",   label: "MP4 \u00b7 H.265/HEVC",     ext: "mp4",  encoder: "libx265" },
   { id: "prores", label: "MOV \u00b7 ProRes 422 HQ",  ext: "mov",  encoder: "prores_ks" },
   { id: "vp9",    label: "WebM \u00b7 VP9",           ext: "webm", encoder: "libvpx-vp9" } ];

Fly.availableFormats = function( encodersText )
{
   return Fly.VIDEO_FORMATS.filter( function( f )
      { return new RegExp( "\\b" + f.encoder.replace( /-/g, "\\-" ) + "\\b" ).test( encodersText ); } );
};

Fly.defaultFormat = function( formats, width )
{
   var ids = formats.map( function( f ) { return f.id; } );
   if ( width > 1920 && ids.indexOf( "hevc" ) >= 0 ) return "hevc";
   if ( ids.indexOf( "h264" ) >= 0 ) return "h264";
   return ids.length ? ids[0] : null;
};

Fly.ffmpegArgs = function( framesDir, fps, outBase, formatId, quality )
{
   var hi = ( quality == "high" ), f = Fly.VIDEO_FORMATS.filter( function( x ) { return x.id == formatId; } )[0];
   var codec = {
      h264:   [ "-c:v", "libx264", "-crf", hi ? "18" : "23", "-pix_fmt", "yuv420p" ],
      hevc:   [ "-c:v", "libx265", "-crf", hi ? "20" : "26", "-tag:v", "hvc1", "-pix_fmt", "yuv420p" ],
      prores: [ "-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le" ],
      vp9:    [ "-c:v", "libvpx-vp9", "-crf", hi ? "24" : "32", "-b:v", "0", "-pix_fmt", "yuv420p" ] }[formatId];
   return [ "-y", "-framerate", String( fps ), "-i", framesDir + "/frame_%05d.tif" ]
      .concat( codec )
      .concat( [ "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
                 outBase + "." + f.ext ] );
};
```

```js
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
```

`Render.runProcess` follows Loom's `Update` io `execute`: `new ExternalProcess`, append `String( P.stdout )` / `String( P.stderr )` in `onStandardOutputDataAvailable` / `onStandardErrorDataAvailable`, `P.start( program, args )`, loop `while ( P.isStarting || P.isRunning ) CoreApplication.processEvents();`, terminating on deadline or `isCancelled()`; null the handlers in `finally`. Reading both pipes as they fill is what keeps a long encode from stalling on a full stderr pipe (ffmpeg writes its progress there).

`Render.findFfmpeg( platform )`: `saved` = `Settings.read( "Loom/ffmpegPath", DataType_String )` (try/catch, "" when unset); `pathVar` = `System.getEnvironmentVariable( "PATH" )` (try/catch, "" on failure); candidates = `Fly.ffmpegCandidates( Util.platform( platform ), File.homeDirectory, pathVar, saved )`; the first that `File.exists` and whose `Render.runProcess( path, [ "-version" ], 10000 )` exits 0 with output starting `ffmpeg version` is written back with `Settings.write( "Loom/ffmpegPath", DataType_String, path )` and returned. `Render.ffmpegEncoders( path )` = `runProcess( path, [ "-hide_banner", "-encoders" ], 10000 ).output`. `Render.encode( path, args, isCancelled )` = `runProcess` with no deadline; true when `exitCode == 0` and the output file (last argument) exists.

- [ ] **Step 4: Run** → PASS. Teeth: drop the filter in `availableFormats`; "only what this ffmpeg can encode" fails; restore. PixInsight test asserts only the contract: `Render.findFfmpeg()` is null or a path whose `-version` exits 0; never that ffmpeg exists.
- [ ] **Step 5: Commit** — `git commit -am "Fly-Through: ffmpeg discovery, formats it can encode, encode arguments"`

---

### Task 12b: Colour management (every output)

**Files:** `script/lib/Fly.js` (pure), `script/lib/Sky.js`, `script/lib/Render.js`; tests in `script/selftest.js`.

**Interfaces:**
- `Fly.parseIccColour( byteAt, length ) -> { matrix: [9] (RGB->XYZ D50, from rXYZ/gXYZ/bXYZ), trc: [ curve x3 ] } | null` — reads `XYZ ` tags and `curv` (identity, gamma u8Fixed8, or table) / `para` (types 0-4) tone curves; null for LUT-only profiles.
- `Fly.trcDecode( curve, v ) -> linear`; `Fly.sourceToTarget( matrix, target ) -> [9]` (Bradford D50->D65, then XYZ->Rec.709 or BT.2020 linear).
- `Fly.SRGB_COLOUR` — the sRGB profile's matrix/curves, the default for untagged or LUT profiles.
- `Fly.outputTransform( colour, mode, opts ) -> { lut: Float32Array(65536) per channel decode, M: [9], encode( rgbLinear ) }` built once per render; `mode` "sdr" | "pq" | "hlg".
- `Sky.iccBytes( window ) -> ByteArray | null` — from `window.filePath` via FileFormatInstance when the file exists; else a temporary save of the view (as `Steps.iccProfileBytes` does), removed after.
- `Render.frame` gains `opts.output` (the transform): after the screen composite, every pixel goes decode -> matrix -> encode. With no `output` it behaves as now (the suite's exactness tests stay valid).

**Tests (write first):**
- node: sRGB decode at 0.5 = 0.214041 (IEC 61966-2-1); a `para` type-3 curve equals the formula at 5 points; ProPhoto -> Rec.709 of (1,1,1) is (1,1,1) (white preserved, Bradford); a saturated ProPhoto green maps outside [0,1] in Rec.709 before clipping (proves the matrix is applied); `parseIccColour` on a constructed v2 profile with rXYZ/gXYZ/bXYZ + gamma curv returns them; SDR output of an sRGB-tagged frame is the frame (identity within 1/65535).
- PixInsight: a ROMM-tagged generated image's frame 0 in SDR differs from its raw pixels in the saturated channel and equals the Fly conversion; an untagged image is treated as sRGB and logged.

### Task 12c: HDR (HLG and PQ)

**Files:** `script/lib/Fly.js`, `script/lib/Render.js`, `script/FlyThrough.js`; tests in `script/selftest.js`.

**Interfaces:**
- `Fly.pqEncode( nits ) -> [0,1]` (ST 2084); `Fly.hlgEncode( sceneLinear ) -> [0,1]` (BT.2100); `Fly.hlgFromDisplay( rgbDisplayRel ) -> rgbScene` (inverse OOTF, gamma 1.2, BT.2020 luminance weights).
- `Fly.SDR_WHITE_NITS = 203`; `Fly.rolloff( L, P ) -> L' ` (identity to 1, then 1 + (P-1)(1 - exp(-(L-1)/(P-1)))).
- `Fly.HDR_DEFAULT_TRANSFER = { social_vertical: "hlg", social_square: "hlg", youtube_4k: "hlg", youtube_1080: "hlg", exhibition: "pq" }`; `Fly.HDR_PEAK_DEFAULT = 1000`.
- `Render.frame` in HDR: base = decoded composite (linear, BT.2020, SDR white = 1) + excess (the unscreened star sum above 1, decoded as linear light above white), rolled off to P = peak/203, then PQ( L*203 nits ) or HLG( hlgFromDisplay( L*203/1000 ) ).
- `Fly.ffmpegArgs( ..., hdr )` with `hdr = { transfer: "pq"|"hlg", maxCll, maxFall }`: `-pix_fmt yuv420p10le` (ProRes yuv422p10le), filter `scale=out_color_matrix=bt2020:out_range=tv,setparams=color_primaries=bt2020:color_trc=smpte2084|arib-std-b67:colorspace=bt2020nc:range=tv`; HEVC `-x265-params hdr10=1:repeat-headers=1:master-display=G(8500,39850)B(6550,2300)R(35400,14600)WP(15635,16450)L(<peak*10000>,1):max-cll=<maxCll>,<maxFall>` for PQ; VP9 `-profile:v 2`. `Fly.availableFormats( enc, hdr )` drops H.264 when HDR.
- `FlyThrough.renderFinal` measures MaxCLL/MaxFALL while rendering PQ frames (brightest pixel, brightest frame-average, in nits) and passes them to the encode.

**Tests (write first):**
- node: `pqEncode(203)` = 0.5807 +- 1e-4; `pqEncode(10000)` = 1; HLG signal of SDR white = 0.75 +- 1e-3; `hlgEncode(1/12)` = 0.5; rolloff: continuous and slope 1 at L=1, monotone, < P for L up to 1e6; ffmpeg args contain the BT.2020 tags per transfer, 10-bit pix fmt, HDR10 x265 params only for PQ, `-profile:v 2` for VP9; H.264 absent from HDR formats.
- PixInsight: HDR frame 0 of a generated scene: every pixel equals the transform of the image (no highlight at t=0); a close star at t=1 exceeds SDR white's signal (PQ > 0.5807) and stays at or under the peak's.
- PixInsight with ffmpeg present (skipped otherwise): an HLG HEVC and a PQ HEVC clip encode; ffprobe: `yuv420p10le`, `bt2020nc,smpte2084,bt2020` / `arib-std-b67`; the PQ file carries Mastering display metadata and Content light level side data (`-show_frames -read_intervals %+#1` side_data_list).

### Task 13: Dialog, draft player, final render, cancel

**Files:** Modify `script/FlyThrough.js`, `script/lib/Render.js`; test `script/selftest.js`.

**Interfaces:**
- Consumes: everything above.
- Produces: `FlyThrough.Dialog` (constructed with a state object, testable like FrameSelector.Dialog); `Render.Player` (Frame subclass + single-shot `Timer`, `setFrames([Bitmap], fps)`, `dueFrame(nowMs) -> index`, `release()`); `FlyThrough.renderFinal( scene, presets, opts, dir, progress ) -> { written, cancelled }`.

Dialog sections, in order (spec §3): input (file or active view; FITS/XISF/TIFF via `OpenFileDialog` filters), solve hints (centre RA/Dec or name, focal length, pixel size; shown only when `Sky.projector` is null), found (target, type switch, distance with source text, placed/blended/backdrop counts, runner-up), star tool dropdown (`Steps.availableStarTools()`), motion (travel pc, easing, growth, brightening), output (duration, fps, preset checkboxes, folder), dynamic range (SDR / HDR; with HDR: peak nits, and per-preset HLG/PQ from `Fly.HDR_DEFAULT_TRANSFER`; note that the draft is SDR), video (the ffmpeg path found, with Browse; **Create video** checkbox, checked when ffmpeg is found and disabled with `Fly.ffmpegInstallHint( Util.PLATFORM )` when not; Browse filter `ffmpeg.exe` on Windows, `ffmpeg` elsewhere; format combo filled from `Fly.availableFormats( Render.ffmpegEncoders( path ) )`, preselected by `Fly.defaultFormat`; quality High/Standard), buttons (Draft, Render, Close).

- [ ] **Step 1: Failing tests**

```js
   // player: the frame shown is the one due by wall clock; late ticks drop frames
   var p = new Render.Player( dlg );
   p.setFrames( [ b0, b1, b2, b3 ], 10 );          // 10 fps
   p.startedAt = 1000;
   check( "due frame by wall clock", p.dueFrame( 1000 + 250 ), 2 );
   check( "late ticks drop, not slow", p.dueFrame( 1000 + 390 ), 3 );
   // cancel mid-render keeps written frames, writes no MP4
   var res = FlyThrough.renderFinal( scene, [ "youtube_1080" ], opts, dir, { cancelAfter: 3 } );
   check( "cancel keeps the frames written", res.written, 3 );
   check( "and makes no video", File.exists( dir + "/youtube_1080.mp4" ), false );
   // five build/play/close cycles, then PixInsight still accepts scripts (checked from the shell)
```

- [ ] **Step 2: Run** the PixInsight suite → FAIL.
- [ ] **Step 3: Implement** the player (single-shot Timer re-armed per frame; `dueFrame = floor((now - startedAt)/1000 × fps) mod n`; stops when not visible; `release()` stops the timer and nulls handlers), the dialog, `renderFinal` (frame loop over presets, `Render.writeTiff` to `<dir>/<preset>/frame_%05d.tif`, `processEvents` between frames, honours cancel, then, when Create video is checked and not cancelled, `Render.encode( path, Fly.ffmpegArgs( dir + "/" + preset, fps, dir + "/" + preset, format, quality ) )` per preset; otherwise shows `Fly.commandLine( path || "ffmpeg", args, Util.PLATFORM )`).
- [ ] **Step 4: Run** node both builds, the PixInsight suite, then from the shell: PixInsight process alive and a trivial dispatched script runs.
- [ ] **Step 5: Commit** — `git commit -am "Fly-Through: dialog, draft player, final render"`

---

### Task 14: README, hand verification, release notes

**Files:** Modify `README.md`, `docs/verified-parameters.md`.

- [ ] **Step 1:** README: a "Fly-Through" section after "Frame Selector": what it does, inputs, the distance sources (Gaia parallax; the nebula from its cluster; galaxies fixed), the presets, ffmpeg (`brew install ffmpeg` on macOS, `winget install Gyan.FFmpeg` on Windows; found automatically, Browse to override), draft vs final, known limits (spec §6).
- [ ] **Step 2: Hand verification on real data** (not part of the suite): run Fly-Through on a solved IC 1396 image; record in `verified-parameters.md` the live Gaia query's row format, the cluster distance found, placed/blended/backdrop counts, and the benchmark ms/frame. When a Windows PixInsight is available, one run there (ffmpeg found, frames written, video encoded) is recorded too; until then the README says Windows is covered by tests, not yet run by hand.
- [ ] **Step 3:** Complexity analysis on the new files; refactor any function over cognitive 15.
- [ ] **Step 4:** Full runs: node both builds, PixInsight suite, liveness check.
- [ ] **Step 5: Commit** — `git commit -am "Fly-Through: README and verified parameters"`
