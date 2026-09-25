# Faster Star Sprites Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A **Star quality** setting (Highest / High / Medium) that trades star-drawing time for quality. Highest is today's renderer, unchanged. High and Medium cut the star-sprite share of a frame (≈1.1 s of ≈1.45 s for a 1080×1920 HDR frame of the Iris) with a small, measured, quality-gated cost.

**Architecture:**
- **Highest:** today's renderer, pixel for pixel. At 1080p the stars are computed at the 4K working resolution (Loom's working copy is sized so its largest 16:9 frame is 3840×2160) and downsampled to 1080: each output pixel averages 3×3 samples of the star.
- **High:** footprint-matched sampling. Inside a sprite's magnified glow, an output pixel takes only as many samples as its footprint needs.
- **Medium:** High, plus a static far field (stars that never separate from the zooming backdrop are put back into it), plus the dimmest share of the remaining moving stars put back into the backdrop too.
- **Stamp reuse** between frames is measured, not built.

**Tech Stack:**
- PixInsight JavaScript Runtime (PJSR, V8).
- Loom's `script/lib/Fly.js` (pure), `script/lib/Render.js` and `script/FlyThrough.js`.
- The self-test `script/selftest.js` and the Node harness `ci/run-tests.js`.

**Spec:** No separate spec file. The design was agreed in conversation on 2026-09-24 and is recorded in **Decisions** below. An external review (Codex, 2026-09-24) found eight problems in the first draft; every one is addressed here and listed under **Review changes**.

## Decisions (agreed with the maintainer)

- **Highest = none of this plan's optimizations.** It is the renderer at `ae1a387`. Its reach skipping, channels drawn together and native bloom are already in, pixel-identical or edge-only.
- **High** = footprint-matched sampling (no pre-shrunk sprites: a pre-shrunk sharp core once lost 27% of its peak, see the comment above `Render.mipLevel`).
- **Medium** = High + static far field + the dimmest `Fly.DIM_SHARE` (proposed 25%) of the stars still moving, by flux, put back into the backdrop. A star put back is never removed: its light stays in the backdrop, so frame 0 keeps it. It zooms with the nebula instead of flying.
- Far stars in Medium lose their twinkle. That is wanted (a static far field).
- **Quality over speed:** a task that fails its quality gate is not shipped.
- **No parallel PixInsight instances.** No new C++; PixInsight's own image operations are allowed.
- The setting is named **Star quality**, because "Quality" is already the video encoder's (`opts.quality`, in `Fly.ENCODE_ONLY`). It changes frames, so it stays out of `Fly.ENCODE_ONLY`.
- **Defaults:** the dialog defaults to **High**. Code that renders without `opts.starQuality` (tests, old callers) gets **Highest**, so nothing existing changes silently.

## Global Constraints

- JavaScript only (PJSR). No new native code.
- The committed test suite never reads the maintainer's images and never touches the network (`runFlyTests` stubs `Sky.fetchText`).
- Tests go in `script/selftest.js`, inside `runFlyTestsClean()`, before `/* fly-tests-end */`. Tests that need PixInsight objects are wrapped in `if ( IN_PIXINSIGHT ) ( function() { … } )();`. Checks use `check( label, actual, expected )`.
- Test commands:
  - Node: `node ci/run-tests.js` and `LOOM_TEST_CORE_RELEASE=4 node ci/run-tests.js` (both must print `PASS`).
  - PixInsight: `/tmp/agent-scratch/pi-suite.sh` (dispatches `script/selftest.js` to the persistent second PixInsight, slot 2, and prints `PASS n run, 0 failed`). Never run it at the same time as a Node run: they share a result file.
- One commit per task on `main`, message ending with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Do not push.
- Real-image checks are ad hoc, never in the suite. They are `/tmp/agent-scratch` scripts on the Iris (`~/Downloads/NGC 7023 - Iris Nebula_fullres.tif`, its cached scene), dispatched to slot 2.

## Review changes (Codex findings → what changed)

1. **Restoration test compared against the wrong thing.** The deblend leaves light in the residual that no sprite holds. → The test compares the settled residual with *the residual plus the settled sprites' patches*.
2. **The still rule missed occlusion, the model blend, spikes and motion-blur streaks.** → The rule now also requires, at every sampled time:
   - `seen == 1` (computed by the real `Render.seenShares`, with every placed star);
   - the photo-to-model weights `wo == 0` and `wc ≤ Fly.STILL_CORE_MODEL`;
   - spike length within `Fly.STILL_GROWTH·K`;
   - a shutter streak within `Fly.STILL_PX`.
3. **HDR headroom** treated settled stars as catalogue stars, with a different radius. → `Render.headroomMap` paints settled stars with the sprite rule at their backdrop position, so frame 0 is identical in HDR too. It is tested in PQ with star HDR on.
4. **Draft vs render.** The decision is made for the *render's* size, crop and times, and the draft reuses it. This is conservative for the draft: a star within 0.25 px at the render's size is within less at the draft's smaller size, and the render's times include the draft's.
5. **Crossfade times were missing.** → The decision samples the exact times the frames use (`FlyThrough.flightTimes`: `Fly.loopFrame`'s `a` and `b` for crossfade, `Fly.timeAt` otherwise). The cache key includes the loop mode, the frame step and everything that changes a streak.
6. **The adaptive-sampling bound** was argued, not proved. → Pixels that straddle the core edge keep the full count, and the gate measures the worst per-pixel error per channel, not only total light.
7. **Tests that proved less than their labels.** Replaced by:
   - Highest pixel-identical to the full-count path;
   - per-pixel, per-channel error bounds;
   - occlusion through the real `seenShares` on a scene.
8. **The default quietly changed existing callers.** → Code without `opts.starQuality` gets Highest. No existing test is relabelled.

## Review Focus

1. **Vertical presets** (the scene is turned by `Render.rotateScene`): settled stars are added back at their turned rects. Pinned by Task 2's vertical frame-0 test.
2. **Changing the flight or the level after a draft:** a cached settled scene must not be reused. Pinned by Task 2's cache test.
3. **Crossfade loops:** the decision must sample both halves of each blended frame. Pinned by Task 2's `flightTimes` test.
4. **HDR with star headroom on:** frame 0 identical in PQ at 1:1. Pinned by Task 2's HDR test. A scaled-down frame 0 differs slightly for settled stars (area filter vs sprite box); measured in Task 2's gate, not assumed.
5. **A near star passing in front of a far one:** the far one must stay a sprite. Pinned by Task 2's occlusion test.

---

## File Structure

- `script/lib/Fly.js`:
  - `Fly.STAR_QUALITY`, `Fly.starQuality( level )`;
  - still-rule constants (`Fly.STILL_PX`, `Fly.STILL_GROWTH`, `Fly.STILL_BRIGHT`, `Fly.STILL_CORE_MODEL`, `Fly.DIM_SHARE`);
  - `Fly.describeStars( c, settled )`.
- `script/lib/Render.js`:
  - `Render.glowSamples`, and changes to `drawSprites`, `spritePixels` and `drawPlaced` (Task 1);
  - `Render.stillStars`, `Render.dimmestMovers`, `Render.settleStill` and `Render.forFlight`, plus `Render.headroomMap` painting settled stars (Task 2).
- `script/FlyThrough.js`:
  - `FlyThrough.STAR_QUALITIES` and the dialog combo (Task 0);
  - `FlyThrough.flightTimes` and `FlyThrough.flightScene`, used by `presetJob`, `renderDraft` and the dialog's `makeDraft` (Task 2).
- `script/selftest.js`: the tests.

---

### Task 0: The Star quality setting

**Files:** `script/lib/Fly.js` (after `Fly.BACKDROP_MOTION_DEFAULT`, ~line 58), `script/FlyThrough.js` (next to `FlyThrough.LOOPS`; dialog `buildMotion` "Star colour:" row ~line 1136; `options()` ~1379; `optionControls()` ~1608), `script/selftest.js`.

**Interfaces — Produces:**
- `Fly.starQuality( level ) -> { footprint: boolean, settle: boolean }`: `"highest"`, `"high"` or `"medium"`. Missing or unknown gives Highest.
- `opts.starQuality`: the level string, read by Tasks 1 and 2.

- [ ] **Step 1: Write the failing tests**

```js
   /* Star quality: Highest is the renderer as it was; High samples glows by their footprint; Medium also puts still and dim stars into the backdrop. */
   check( "the three levels", [ Fly.starQuality( "highest" ), Fly.starQuality( "high" ), Fly.starQuality( "medium" ) ],
          [ { footprint: false, settle: false }, { footprint: true, settle: false }, { footprint: true, settle: true } ] );
   check( "without a level (old callers, the tests) it is Highest", [ Fly.starQuality(), Fly.starQuality( "ultra" ) ], [ Fly.starQuality( "highest" ), Fly.starQuality( "highest" ) ] );
   ( function()
   {
      var spec = { id: "youtube_1080", w: 1920, h: 1080, pingPong: false }, o = { travel: 184, duration: 20, fps: 30, starQuality: "highest" };
      check( "the level changes the frames, not only the encode", Fly.frameSignature( o, spec, "k" ) != Fly.frameSignature( Object.assign( {}, o, { starQuality: "medium" } ), spec, "k" ), true );
   } )();
```

In the existing dialog-flow test (search `selftest.js` for `synthDir( "fly-dialog-flow" )`), after the dialog is created, add:

```js
         check( "Star quality: three levels, High by default, in the options", [ dlg.starQualityCombo.numberOfItems, dlg.options().starQuality ], [ 3, "high" ] );
```

- [ ] **Step 2: Run to see them fail.** Node: `TypeError: Fly.starQuality is not a function`.

- [ ] **Step 3: Implement**

`script/lib/Fly.js`:

```js
/*
 * Star quality: how the star sprites are drawn. Highest is the full renderer --
 * at 1080p the stars are computed at the 4K working resolution and each output
 * pixel averages 3 x 3 samples of them. High samples a magnified glow only
 * as densely as its footprint needs (Render.glowSamples). Medium also puts the
 * stars that never leave the backdrop, and the dimmest of the moving ones,
 * back into the backdrop (Render.forFlight). Without a level: Highest.
 */
Fly.STAR_QUALITY = { highest: { footprint: false, settle: false }, high: { footprint: true, settle: false }, medium: { footprint: true, settle: true } };
Fly.starQuality = function( level )
{
   return Fly.STAR_QUALITY[level] || Fly.STAR_QUALITY.highest;
};
```

`script/FlyThrough.js`, next to `FlyThrough.LOOPS`:

```js
FlyThrough.STAR_QUALITIES = [ "highest", "high", "medium" ];   // the Star quality combo's items, in order
FlyThrough.STAR_QUALITY_DEFAULT = "high";                 // the dialog's default (code without a level gets Highest)
```

In the dialog's `buildMotion`, before `this.motion = this.group( "Motion", [` (add `var self = this;` at the top of `buildMotion` if it is not there):

```js
      this.starQualityCombo = new ComboBox( this );
      [ "Highest", "High", "Medium" ].forEach( function( t ) { self.starQualityCombo.addItem( t ); } );
      this.starQualityCombo.currentItem = FlyThrough.STAR_QUALITIES.indexOf( FlyThrough.STAR_QUALITY_DEFAULT );
      this.starQualityCombo.toolTip = "<p><b>Highest</b>: every star drawn in full (for 1080p, computed at 4K and downsampled). " +
                                      "<b>High</b>: a near star's glow sampled only as finely as it needs; faster, the same look. " +
                                      "<b>Medium</b>: also leaves the far stars, and the dimmest moving ones, in the backdrop (still, no twinkle); fastest.</p>";
      this.starQualityCombo.onItemSelected = function() { self.redraft(); };
```

Change the "Star colour:" row to:

```js
         this.row( [ this.label( "Star colour:" ), this.saturationSlider, this.saturationLabel, this.label( "Star quality:" ), this.starQualityCombo, "stretch" ] ) ] );
```

In `options()`, add `starQuality: FlyThrough.STAR_QUALITIES[this.starQualityCombo.currentItem],` next to `starHdr`. In `optionControls()`, add `[ "starQuality", this.starQualityCombo, "currentItem" ]`.

- [ ] **Step 4: Run all three suites.** Expected: `PASS`.
- [ ] **Step 5: Commit**: `Fly-Through: a Star quality setting (Highest, High, Medium)`.

---

### Task 1: High — glow samples matched to the footprint

**Files:** `script/lib/Render.js` (`Render.drawSprites`, `Render.spritePixels`, `Render.drawPlaced`; new `Render.glowSamples`), `script/selftest.js`.

**Interfaces:**
- Consumes: `Render.drawSprites( accs, outW, outH, patches, sp, cx, cy, g, ks, cam, kOuters, rc, how )` and `Render.spritePixels( c, u, v, sums )` as at `ae1a387`; `Fly.starQuality`.
- Produces:
  - `Render.glowSamples( c, dx, dy ) -> integer in [1, c.nx]`;
  - `Render.spritePixels( c, u, v, sums, n )`, where `n` is the samples a side and `0` or missing means the full count;
  - `how.footprint === true` turns the thinning on. Without it every pixel gets the full count, so Highest is unchanged.

**Why the bound holds.** Beyond the core, the sample for output offset `ro` lands at `Fly.radialSource( ro )`. There the gain is constant (the `k → kO` blend runs only inside `rc`). The map stretches by `1/(g·seen)` radially and by `radialSource( ro )/ro ≥ 1/(g·seen)` tangentially. So the pixel's footprint in the sprite is at most `cam.f·radialSource( ro )/ro` sprite pixels a side, taken at the pixel's nearest point to the star. A pixel whose nearest point is inside `rc` (it straddles the core edge or is in the core) keeps the full count.

- [ ] **Step 1: Write the failing tests**

```js
   /*
    * High: a magnified glow sampled only as densely as its footprint needs.
    * Against the full count (Highest): per pixel and per channel within 1% of the
    * star's peak, the same light, as steady while it drifts; Highest never thins.
    */
   ( function()
   {
      var R = 20, n = 2*R + 1, cols = [ 1, 0.7, 0.45 ], patches = cols.map( function( k ) { var p = new Float32Array( n*n ); for ( var y = 0; y < n; ++y ) for ( var x = 0; x < n; ++x ) p[y*n + x] = k*( Math.exp( -( ( x - R )*( x - R ) + ( y - R )*( y - R ) )/( 2*1.6*1.6 ) ) + 0.03*Math.exp( -Math.hypot( x - R, y - R )/6 ) ); return p; } );
      var sp = { rect: { x0: 0, y0: 0, x1: n, y1: n }, det: { x: R, y: R } }, W = 120, cam = { x: 0, y: 0, fx: 2.4, fy: 2.4 };
      check( "a magnified glow takes fewer samples", Render.glowSamples( { cam: cam, g: 3.5, seen: 1, rc: 3, nx: 3, f2: 1, radial: true }, 30, 0 ) < 3, true );
      check( "a pixel at the core's edge takes them all", Render.glowSamples( { cam: cam, g: 3.5, seen: 1, rc: 3, nx: 3, f2: 1, radial: true }, 4, 0 ), 3 );
      function draw( g, cx, footprint, seen )
      {
         var accs = cols.map( function() { return new Float32Array( W*W ); } );
         Render.drawSprites( accs, W, W, patches, sp, cx, cx, g, [ 1, 1, 1 ], cam, [ 1/( g*g ), 1/( g*g ), 1/( g*g ) ], 3, { footprint: footprint, seen: seen } );
         return accs;
      }
      function compare( a, b )
      {
         var worst = 0, peak = 0, sa = 0, sb = 0;
         for ( var c = 0; c < a.length; ++c ) for ( var i = 0; i < a[c].length; ++i ) { worst = Math.max( worst, Math.abs( a[c][i] - b[c][i] ) ); peak = Math.max( peak, b[c][i] ); sa += a[c][i]; sb += b[c][i]; }
         return { local: worst/peak, light: sa/sb };
      }
      [ 1.3, 2, 3.5 ].forEach( function( g )
      {
         var d = compare( draw( g, 140, true ), draw( g, 140, false ) );
         check( "g " + g + ": every pixel within 1% of the peak (" + ( 100*d.local ).toFixed( 2 ) + "%), the same light (" + d.light.toFixed( 4 ) + ")",
                [ d.local < 0.01, Math.abs( d.light - 1 ) < 0.005 ], [ true, true ] );
         var spread = function( fp ) { var v = []; for ( var k = 0; k < 24; ++k ) { var a = draw( g, 140 + k*0.1, fp ), s = 0; a.forEach( function( ch ) { for ( var i = 0; i < ch.length; ++i ) s += ch[i]; } ); v.push( s ); } return Math.max.apply( null, v )/Math.min.apply( null, v ) - 1; };
         var sa = spread( true ), sb = spread( false );
         check( "g " + g + ": as steady while it drifts (" + ( 100*sa ).toFixed( 2 ) + "% vs " + ( 100*sb ).toFixed( 2 ) + "%)", sa <= sb + 0.002, true );
      } );
      var h = compare( draw( 2, 140, true, 0.4 ), draw( 2, 140, false, 0.4 ) );
      check( "a partly hidden star: every pixel within 1% (" + ( 100*h.local ).toFixed( 2 ) + "%)", h.local < 0.01, true );
   } )();

   /* Highest draws exactly what the full count draws; High draws within the gate on a whole scene, occlusion included. */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var dir = synthDir( "fly-quality" ), fx = flyTestScene( dir ), real = Render.glowSamples, calls = 0;
      try
      {
         var sc = fx.scene, W = 160, H = 90, crop = Fly.presetCrop( sc.w, sc.h, sc.tp.x, sc.tp.y, W, H );
         var o = { travel: 600, easing: "smoothstep", growth: 0.15, brightening: true, output: Fly.outputTransform( null, "sdr" ) };
         var pix = function( img ) { var p = Render.channels( img ); img.free(); return p; };
         Render.glowSamples = function() { ++calls; return real.apply( this, arguments ); };
         var high = pix( Render.frame( sc, 0.9, Object.assign( {}, o, { starQuality: "highest" } ), W, H, crop ) );
         check( "Highest never thins the samples", calls, 0 );
         var med = pix( Render.frame( sc, 0.9, Object.assign( {}, o, { starQuality: "high" } ), W, H, crop ) );
         check( "High does", calls > 0, true );
         Render.glowSamples = function( c ) { return c.nx; };
         var full = pix( Render.frame( sc, 0.9, Object.assign( {}, o, { starQuality: "high" } ), W, H, crop ) );
         var same = true, worst = 0, top = 0;
         for ( var c = 0; c < high.length; ++c ) for ( var i = 0; i < high[c].length; ++i ) { if ( high[c][i] !== full[c][i] ) same = false; worst = Math.max( worst, Math.abs( med[c][i] - high[c][i] ) ); top = Math.max( top, high[c][i] ); }
         check( "Highest is pixel for pixel the full count", same, true );
         check( "High within 1% of the brightest pixel everywhere (" + ( 100*worst/top ).toFixed( 2 ) + "%)", worst < 0.01*top, true );
      }
      finally { Render.glowSamples = real; fx.windows.forEach( function( w ) { w.forceClose(); } ); }
   } )();
```

(`travel: 600` with the fixture's stars at 200–1940 pc brings near stars up close, growing and occluding one another.)

- [ ] **Step 2: Run to see them fail.** Node: `TypeError: Render.glowSamples is not a function`.

- [ ] **Step 3: Implement** in `script/lib/Render.js`:

After `Render.spritePixels`:

```js
/*
 * Samples a side for the glow pixel centred (dx, dy) from the star, in
 * High and Medium (Fly.starQuality): its footprint in the sprite, cam.f x
 * radialSource(ro)/ro sprite pixels at its nearest point to the star (the
 * wider of the radial and tangential stretch beyond the core), rounded up,
 * at most the full count c.nx. A pixel reaching into the core, where the
 * gain blends from k to kO, keeps the full count.
 */
Render.glowSamples = function( c, dx, dy )
{
   if ( !c.radial ) return c.nx;
   var h = 0.5*Math.sqrt( c.cam.fx*c.cam.fx + c.cam.fy*c.cam.fy ), ro = Math.sqrt( dx*dx + dy*dy ) - h;
   if ( ro <= c.rc ) return c.nx;
   var span = Math.max( c.cam.fx, c.cam.fy )*Fly.radialSource( ro, c.rc, c.g, c.seen )/ro/c.f2;
   return Math.max( 1, Math.min( c.nx, Math.ceil( span - 1e-9 ) ) );
};
```

`Render.spritePixels( c, u, v, sums )` becomes `( c, u, v, sums, n )`. Its first line becomes `var cam = c.cam, nc = c.mps.length, ch, mp, nx = n || c.nx, ny = n || c.ny;`. Every `c.nx`/`c.ny` in its body becomes `nx`/`ny`: the two loop bounds, `( sx + 0.5 )/nx`, `( sy + 0.5 )/ny`, and the final `/= nx*ny`. With `n` missing the arithmetic is the old code's, operation for operation.

In `Render.drawSprites`'s pixel loop, replace `Render.spritePixels( ctx, u, v, sums );` with:

```js
         var n = ( how && how.footprint ) ? Render.glowSamples( ctx, cam.x + ( u + 0.5 )*cam.fx - 0.5 - cx, dy ) : 0;
         Render.spritePixels( ctx, u, v, sums, n );
```

(`nx == ny` for every preset: crops keep the aspect ratio, so `cam.fx == cam.fy`.)

In `Render.drawPlaced`, the glow draws' `how` (`{ seen: q.seen }`) becomes `{ seen: q.seen, footprint: Fly.starQuality( opts.starQuality ).footprint }`. Spike draws are left as they are: a spike's width is not magnified.

- [ ] **Step 4: Run all three suites.** Expected: `PASS`, and no existing test changes (they render without a level, which is Highest).

- [ ] **Step 5: Quality gate on the real Iris (ad hoc)**

Adapt `/tmp/agent-scratch/cmp.js` to render the 1080 vertical HDR frames at t = 0.3, 0.6 and 0.95 at Highest and at High. Report:
- per frame: time; the largest per-channel difference relative to the frame's brightest star pixel; the pixels over one 16-bit step.
- steadiness: for the three nearest stars at t = 0.95, the total light over 20 consecutive frames at both levels, as max/min − 1.
- crops: 100%-scale PNG crops of those stars side by side. Look at them.

Gate: per-pixel ≤ 1% of peak, light within 0.5%, steadiness no worse than Highest's + 0.2%, crops indistinguishable. If any of these fails, revert the task.

- [ ] **Step 6: Commit**: `Fly-Through: High star quality samples a glow as densely as its footprint needs`.

---

### Task 2: Medium — static far field and the dimmest moving stars into the backdrop

**Files:** `script/lib/Fly.js` (constants; `Fly.describeStars` ~line 1044), `script/lib/Render.js` (new functions after `Render.sceneFor`; `Render.headroomMap`), `script/FlyThrough.js` (new functions before `presetJob`; `presetJob` ~118; `renderDraft` ~395; dialog `makeDraft` ~1741), `script/selftest.js`.

**Interfaces — Produces:**
- `FlyThrough.flightTimes( opts, spec ) -> { times: number[], frameDt: number }`.
- `Render.stillStars( sc, opts, outW, outH, crop, times ) -> boolean[]`.
- `Render.dimmestMovers( sc, still, share ) -> boolean[]`: still, plus the dimmest `share` of the rest.
- `Render.settleStill( sc, settle ) -> scene`: `R` copied, with the settled light added; `sprites` without the settled ones; `settledStars` (their entries); `settled` (their count).
- `Render.forFlight( sc, opts, outW, outH, crop, flight ) -> scene`, cached on `sc._flight`.
- `FlyThrough.flightScene( scene, opts, spec ) -> scene`.
- `Fly.describeStars( counts, settled ) -> string`.

- [ ] **Step 1: Write the failing tests**

```js
   check( "the star counts move settled stars to the background",
          Fly.describeStars( { detected: 100, placed: 40, backdrop: 60, blended: 0 }, 25 ),
          "Stars: 100 detected · 15 moving · 85 in the background" );
   ( function()
   {
      var o = { duration: 1, fps: 10 };
      check( "a flight's times are its frames' times", FlyThrough.flightTimes( o, { pingPong: false } ).times.length, 10 );
      var cf = FlyThrough.flightTimes( o, { pingPong: false, crossfade: true } ), F = Fly.crossfadeFrames( 1, 10 ), lf = Fly.loopFrame( 0, 10, F );
      check( "a crossfade's include both halves of each blended frame", [ cf.times.indexOf( lf.a ) >= 0, cf.times.indexOf( lf.b ) >= 0 ], [ true, true ] );
   } )();

   /*
    * Medium: a star that never separates from the zooming backdrop -- never
    * strays 0.25 px, grows, brightens, turns to its model, streaks or hides
    * behind a nearer star -- is put back into the backdrop, and so is the
    * dimmest share of the rest. Frame 0 is unchanged, in SDR and in HDR.
    */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var dir = synthDir( "fly-still" ), fx = flyTestScene( dir );
      try
      {
         var sc = fx.scene, W = 160, H = 90, crop = Fly.presetCrop( sc.w, sc.h, sc.tp.x, sc.tp.y, W, H ), hspec = { id: "still", w: W, h: H, pingPong: false };
         var flight = { travel: 150, easing: "smoothstep", growth: 0.15, brightening: true, duration: 1, fps: 10, motionBlur: true, starQuality: "medium" };
         var times = FlyThrough.flightTimes( flight, hspec );
         var fo = Object.assign( {}, flight, { frameDt: times.frameDt } );
         check( "Highest and High draw every star", [ "highest", "high" ].map( function( l ) { return FlyThrough.flightScene( sc, Object.assign( {}, flight, { starQuality: l } ), hspec ) === sc; } ), [ true, true ] );
         var none = Render.stillStars( sc, Object.assign( {}, fo, { travel: 1e-6 } ), W, H, crop, times.times );
         check( "a flight that barely moves leaves every star still", none.every( function( v ) { return v; } ), true );
         var still = Render.stillStars( sc, fo, W, H, crop, times.times );
         check( "a real flight keeps the near stars as sprites (" + still.filter( Boolean ).length + " of " + still.length + " still)", still.some( function( v ) { return !v; } ), true );

         // a near star in front of a far one: the far one stays a sprite (Render.seenShares)
         var saw = false, real = Render.seenShares;
         Render.seenShares = function( placed ) { real.apply( this, arguments ); placed.forEach( function( q ) { if ( q.j === 0 ) q.seen = 0.5; } ); saw = true; };
         try { check( "a star hidden behind another is not settled", [ Render.stillStars( sc, Object.assign( {}, fo, { travel: 1e-6 } ), W, H, crop, times.times )[0], saw ], [ false, true ] ); }
         finally { Render.seenShares = real; }

         // the dimmest share of the moving stars goes too
         var low = Render.dimmestMovers( sc, still, 0.25 ), movers = still.filter( function( v ) { return !v; } ).length;
         check( "the dimmest quarter of the moving stars is settled", low.filter( Boolean ).length - still.filter( Boolean ).length, Math.floor( 0.25*movers ) );
         var flux = function( j ) { return sc.sprites[j].s.det.flux; }, keptMin = Infinity, droppedMax = -Infinity;
         low.forEach( function( v, j ) { if ( still[j] ) return; if ( v ) droppedMax = Math.max( droppedMax, flux( j ) ); else keptMin = Math.min( keptMin, flux( j ) ); } );
         check( "and it is the dimmest", droppedMax <= keptMin, true );

         // settling adds back exactly what the deblend took out
         var settled = Render.settleStill( sc, low ), worst = 0;
         var expect = sc.R.map( function( a ) { return a.slice(); } );
         sc.sprites.forEach( function( e, j ) { if ( !low[j] ) return; var r = e.s.rect, rw = r.x1 - r.x0; for ( var c = 0; c < expect.length; ++c ) for ( var y = r.y0; y < r.y1; ++y ) for ( var x = r.x0; x < r.x1; ++x ) expect[c][y*sc.w + x] += e.s.pixels[Math.min( c, e.s.pixels.length - 1 )][( y - r.y0 )*rw + x - r.x0]; } );
         for ( var c = 0; c < expect.length; ++c ) for ( var i = 0; i < expect[c].length; ++i ) worst = Math.max( worst, Math.abs( settled.R[c][i] - expect[c][i] ) );
         check( "the backdrop gets back the settled stars' own light", [ worst, settled.sprites.length + settled.settled, sc.R[0] !== settled.R[0] ], [ 0, sc.sprites.length, true ] );

         // frame 0 is the same image at 1:1 (the fixture is 800 x 600: an 800 x 450 frame, a 450 x 800 turned one),
         // SDR and HDR with star headroom. Scaled down it is not exact -- see the gate in Step 5.
         var W1 = 800, H1 = 450, crop1 = Fly.presetCrop( sc.w, sc.h, sc.tp.x, sc.tp.y, W1, H1 ), settled1 = FlyThrough.flightScene( sc, flight, { id: "one", w: W1, h: H1, pingPong: false } );
         var same = function( a, b ) { var d = 0, pa = Render.channels( a ), pb = Render.channels( b ); for ( var c = 0; c < pa.length; ++c ) for ( var i = 0; i < pa[c].length; ++i ) d = Math.max( d, Math.abs( pa[c][i] - pb[c][i] ) ); a.free(); b.free(); return d; };
         [ Fly.outputTransform( null, "sdr" ), Fly.outputTransform( null, "pq", { peak: 1000 } ) ].forEach( function( out )
         {
            var o = Object.assign( {}, flight, { output: out, starHdr: true, peak: 1000 } );
            check( out.mode + ": frame 0 unchanged by settling", same( Render.frame( sc, 0, o, W1, H1, crop1 ), Render.frame( settled1, 0, o, W1, H1, crop1 ) ) < 1/65535, true );
         } );
         var vspec = { id: "vert", w: 450, h: 800, pingPong: false }, vt = Render.sceneFor( sc, vspec.w, vspec.h ), vs = FlyThrough.flightScene( sc, flight, vspec );
         var vcrop = Fly.presetCrop( vt.w, vt.h, vt.tp.x, vt.tp.y, vspec.w, vspec.h ), vo = Object.assign( {}, flight, { output: Fly.outputTransform( null, "sdr" ) } );
         check( "vertical: frame 0 unchanged", same( Render.frame( vt, 0, vo, vspec.w, vspec.h, vcrop ), Render.frame( vs, 0, vo, vspec.w, vspec.h, vcrop ) ) < 1/65535, true );

         // the decision follows the flight: the same one reuses it, another decides again
         var a1 = FlyThrough.flightScene( sc, flight, hspec ), a2 = FlyThrough.flightScene( sc, flight, hspec );
         var b1 = FlyThrough.flightScene( sc, Object.assign( {}, flight, { travel: 400 } ), hspec );
         var c1 = FlyThrough.flightScene( sc, flight, Object.assign( {}, hspec, { crossfade: true } ) );
         check( "cached for the same flight; decided again for a longer one or a crossfade", [ a1 === a2, b1 !== a1, c1 !== a1 ], [ true, true, true ] );
         var D = sc.D; sc.D = Infinity;
         try { check( "a galaxy (no backdrop zoom) decides without failing", Render.stillStars( sc, fo, W, H, crop, times.times ).length, sc.sprites.length ); }
         finally { sc.D = D; }
      }
      finally { fx.windows.forEach( function( w ) { w.forceClose(); } ); }
   } )();
```

- [ ] **Step 2: Run to see them fail.** Node: the counts check fails (the count is ignored today); `FlyThrough.flightTimes is not a function`. PixInsight: `Render.stillStars is not a function`.

- [ ] **Step 3: Implement**

`script/lib/Fly.js`, after the Star quality block:

```js
/*
 * Medium star quality's static far field: a star is put back into the backdrop
 * when at every time of the flight it is within STILL_PX output pixels of
 * where the zooming backdrop carries it, drawn no bigger than STILL_GROWTH
 * times the backdrop's zoom (spikes too), no brighter than STILL_BRIGHT,
 * fully seen, its streak within STILL_PX, and drawn from its photograph (its
 * core's model share at most STILL_CORE_MODEL). Then DIM_SHARE of the
 * stars still moving, the dimmest, go into the backdrop too. (Iris,
 * 2026-09-24: 496 of 718 sprites stayed within 0.25 px, 54% of the drawing.)
 */
Fly.STILL_PX = 0.25;
Fly.STILL_GROWTH = 1.01;
Fly.STILL_BRIGHT = 1.02;
Fly.STILL_CORE_MODEL = 0.01;
Fly.DIM_SHARE = 0.25;
```

Replace `Fly.describeStars`:

```js
/* The star counts line; `settled` of the placed stars are in the backdrop (Medium star quality), so counted there. */
Fly.describeStars = function( c, settled )
{
   var k = settled || 0;
   return "Stars: " + c.detected + " detected · " + ( c.placed - k ) + " moving · " + ( c.backdrop + k ) + " in the background" +
          ( c.blended > 0 ? " · " + c.blended + " blended (stay still)" : "" );
};
```

`script/lib/Render.js`, after `Render.sceneFor`:

```js
/*
 * Which sprites stay still (Fly.STILL_*) at every time in `times`, drawn
 * at outW x outH from `crop` with opts (which carries frameDt, for the
 * streak). Each is placed as Render.addSprites places it -- seenShares over
 * all the stars placed at that time -- and compared with where the zooming
 * backdrop carries its pixel, tp + (det - tp) K.
 */
Render.stillStars = function( sc, opts, outW, outH, crop, times )
{
   var fx = crop.w/outW, fy = crop.h/outH, cam = { x: crop.x, y: crop.y, fx: fx, fy: fy }, still = sc.sprites.map( function() { return true; } );
   var motion = opts.backdropMotion != null ? opts.backdropMotion : Fly.BACKDROP_MOTION_DEFAULT;
   times.forEach( function( t )
   {
      var s = opts.travel*Fly.ease( t, opts.easing ), K = Fly.backdropZoom( sc.D, s, motion ), o = Object.assign( {}, opts, { t: t, K: K } );
      var placed = [];
      sc.sprites.forEach( function( e, j ) { var q = Render.placeSprite( sc, e, j, s, o ); if ( q ) placed.push( q ); else still[j] = false; } );
      Render.seenShares( placed, outW, outH, cam, Fly.smoothstep( 0, 0.1, t ) );
      placed.forEach( function( q )
      {
         var j = q.j;
         if ( !still[j] ) return;
         var e = q.e, bx = sc.tp.x + ( e.s.det.x - sc.tp.x )*K, by = sc.tp.y + ( e.s.det.y - sc.tp.y )*K;
         var path = Render.shutterPath( sc, q.sp, e, s, o, cam, q.cx, q.cy ), p0 = path[0], p1 = path[path.length - 1];
         var bright = opts.brightening ? q.m.ratio*q.m.ratio : 1;
         if ( q.alpha < 1 || q.seen < 1 ||
              Math.hypot( ( q.cx - bx )/fx, ( q.cy - by )/fy ) > Fly.STILL_PX ||
              Math.hypot( ( p1.x - p0.x )/fx, ( p1.y - p0.y )/fy ) > Fly.STILL_PX ||
              q.g > Fly.STILL_GROWTH*K || q.spikeLength > Fly.STILL_GROWTH*K || bright > Fly.STILL_BRIGHT ||
              Fly.modelWeight( q.m.ratio ) > 0 || Fly.coreModelWeight( q.m.ratio ) > Fly.STILL_CORE_MODEL )
            still[j] = false;
      } );
   } );
   return still;
};

/* `still`, plus the dimmest `share` of the sprites not still (by detected flux): what Medium puts into the backdrop. */
Render.dimmestMovers = function( sc, still, share )
{
   var movers = [];
   still.forEach( function( v, j ) { if ( !v ) movers.push( j ); } );
   movers.sort( function( a, b ) { return sc.sprites[a].s.det.flux - sc.sprites[b].s.det.flux; } );
   var out = still.slice();
   for ( var k = 0; k < Math.floor( share*movers.length ); ++k ) out[movers[k]] = true;
   return out;
};

/*
 * A scene with the sprites marked in `settle` put back into the backdrop's
 * stars layer -- each one's deblended light added exactly where
 * Sky.deblendSprite took it from -- and dropped from the sprites. The scene
 * given is not changed. settledStars keeps them for the HDR headroom map.
 */
Render.settleStill = function( sc, settle )
{
   var R = sc.R.map( function( a ) { return a.slice(); } ), movers = [], gone = [];
   sc.sprites.forEach( function( e, j )
   {
      if ( !settle[j] ) { movers.push( e ); return; }
      gone.push( e );
      var s = e.s, r = s.rect, rw = r.x1 - r.x0;
      for ( var c = 0; c < R.length; ++c )
      {
         var P = s.pixels[Math.min( c, s.pixels.length - 1 )], Rc = R[c];
         for ( var y = r.y0; y < r.y1; ++y )
            for ( var x = r.x0; x < r.x1; ++x )
               Rc[y*sc.w + x] += P[( y - r.y0 )*rw + x - r.x0];
      }
   } );
   return Object.assign( {}, sc, { R: R, sprites: movers, settledStars: gone, settled: gone.length, turned: undefined, _flight: undefined } );
};

/* Render.settleStill for a flight ({ times, frameDt }), kept on the scene for the same flight (one at a time: each holds a copy of the stars layer). */
Render.forFlight = function( sc, opts, outW, outH, crop, flight )
{
   var key = JSON.stringify( [ opts.travel, opts.easing, opts.growth, !!opts.brightening, opts.backdropMotion, !!opts.motionBlur, sc.D,
                               outW, outH, crop.x, crop.y, crop.w, crop.h, flight.frameDt, flight.times, Fly.DIM_SHARE ] );
   if ( sc._flight && sc._flight.key == key ) return sc._flight.scene;
   sc._flight = null;                                   // the old copy goes before the new one is made
   var o = Object.assign( {}, opts, { frameDt: flight.frameDt } );
   var settle = Render.dimmestMovers( sc, Render.stillStars( sc, o, outW, outH, crop, flight.times ), Fly.DIM_SHARE );
   sc._flight = { key: key, scene: Render.settleStill( sc, settle ) };
   return sc._flight.scene;
};
```

In `Render.headroomMap`, after the `placed.forEach( … )` block and before `// every other catalogued star`, paint the settled stars as the sprite rule would have painted them, at the backdrop's position:

```js
   // stars Medium put back into the backdrop: the sprite rule, where the backdrop carries them (at frame 0 exactly where the sprite was)
   ( sc.settledStars || [] ).forEach( function( e )
   {
      var s = e.s.source || {}, gain = isFinite( s.G ) ? Fly.magnitudeGain( s.G, sc.gBright, peak ) : 1;
      moving[Render.starKey( s )] = true;
      if ( gain > 1 ) paint( sc.tp.x + ( e.s.det.x - sc.tp.x )*K, sc.tp.y + ( e.s.det.y - sc.tp.y )*K,
                             Math.max( reach( gain ), Render.coreRadius( e.s )*Math.max( 1, K ) + 2 ), gain );
   } );
```

(At frame 0, `K = 1` and a still sprite's `q.g = 1` and `q.cx = det.x`, so this paints exactly what the `placed` block painted for it before.)

`script/FlyThrough.js`, before `FlyThrough.presetJob`:

```js
/* The times a preset's frames are drawn at -- both halves of a crossfade's blended frames -- and the step between frames (the shutter's). */
FlyThrough.flightTimes = function( opts, spec )
{
   var n = Fly.frameCount( opts.duration, opts.fps, spec.pingPong ), F = spec.crossfade ? Fly.crossfadeFrames( opts.duration, opts.fps ) : 0, times = [];
   for ( var i = 0; i < n; ++i )
   {
      if ( !spec.crossfade ) { times.push( Fly.timeAt( i, n, spec.pingPong ) ); continue; }
      var f = Fly.loopFrame( i, n, F );
      times.push( f.a );
      if ( f.b != null ) times.push( f.b );
   }
   return { times: times, frameDt: Fly.frameStep( n, F, spec.pingPong ) };
};

/*
 * The scene a preset's frames are drawn from: turned for it (Render.sceneFor)
 * and, at Medium star quality, with its still and dimmest stars in the backdrop
 * (Render.forFlight) -- decided at the render's size, crop and times. A draft
 * uses the same decision: a star within STILL_PX at the render's size is
 * within less at the draft's, and the render's times include the draft's.
 */
FlyThrough.flightScene = function( scene, opts, spec )
{
   var ps = Render.sceneFor( scene, spec.w, spec.h );
   if ( !Fly.starQuality( opts.starQuality ).settle ) return ps;
   var crop = Fly.presetCrop( ps.w, ps.h, ps.tp.x, ps.tp.y, spec.w, spec.h );
   return Render.forFlight( ps, opts, spec.w, spec.h, crop, FlyThrough.flightTimes( opts, spec ) );
};
```

In `FlyThrough.presetJob`, replace `ps = Render.sceneFor( scene, p.w, p.h )` with `ps = FlyThrough.flightScene( scene, opts, p )`.
In `FlyThrough.renderDraft`, replace `scene = Render.sceneFor( scene, spec.w, spec.h );` with `scene = FlyThrough.flightScene( scene, opts, spec );`.
In the dialog's `makeDraft`, after `this.player.setFrames( … );`:

```js
      // at Medium star quality the flight decides which stars stay in the backdrop: the counts say so
      try { this.starsLabel.text = Fly.describeStars( this.built.counts, FlyThrough.flightScene( this.built.scene, o, spec ).settled || 0 ); }
      catch ( e ) { console.warningln( "Loom Fly-Through: star counts: " + e ); }
```

- [ ] **Step 4: Run all three suites.** Expected: `PASS`.

- [ ] **Step 5: Quality gate on the real Iris (ad hoc)**

Render the 1080 vertical HDR frames at t = 0, 0.3, 0.6 and 0.95 at High and at Medium. Report:
- how many stars settled, split into still and dim;
- frame 0: any pixel over one 16-bit step (expected: none);
- later frames: time, the largest difference and where, as 100%-scale PNG crops. Look at them.

Expected differences:
- far stars without twinkle;
- growth ≤1%, brightening ≤2%;
- the dim quarter zooming with the nebula instead of flying;
- **a slight softening of settled stars when the frame is smaller than the working image** (1080 from 4K). A sprite averages a 3×3 box over each output pixel; the backdrop's area filter is a tent of half-width `step/K` (`Render.tentTaps`), which is wider. Report the peak change of the ten brightest settled stars at frame 0. Judge the crops at 100%: if the softening is visible, stop and ask the maintainer.

Anything else (a star jumping, a halo left behind, a colour shift) stops the task.

- [ ] **Step 6: Commit**: `Fly-Through: Medium star quality -- a static far field, and the dimmest moving stars in the backdrop`.

---

### Task 3: Measure whether reusing a star's drawing is worth building

**Files:** none in the repository. Ad hoc: `/tmp/agent-scratch/probe-reuse.js`.

- [ ] **Step 1: Measure.** Over 60 consecutive frames of the Iris 1080 vertical flight at High and at Medium, record for each sprite drawn:
  - frame to frame, the relative change of `q.g`, `q.seen`, `Fly.modelWeight`, `Fly.coreModelWeight` and `q.spikeLength`;
  - its share of the sprite drawing time (wrap `Render.drawPlaced`).

  A drawing is "reusable" when all of those change by less than 0.2% (twinkle only rescales, so it does not count).
- [ ] **Step 2: Report** the share of sprite time on reusable drawings, and stop. Building the reuse needs its own plan and the maintainer's go-ahead: sliding a stamp by a fraction of a pixel softens a star and must pass Task 1's steadiness gate.

---

### Task 4: Finish

- [ ] Render the Iris 1080 vertical HDR video in full at each level from slot 2. Time each, write frame sheets (`/tmp/agent-scratch/review.py`) and inspect every sheet.
- [ ] Re-enable App Nap: `defaults delete com.pixinsight.PixInsight NSAppSleepDisabled`.
- [ ] Report the per-level timings and every quality-gate number. Do not push.
