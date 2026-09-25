# Loom Blind Solver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This plan is executed by a **swarm** (see "Swarm execution" at the end).

**Goal:** Loom Fly-Through plate-solves an image with no object name, no RA/Dec and no plate scale, with its own JavaScript solver, and hands the result to PixInsight's ImageSolver.

**Architecture:** A pure library `script/lib/Solve.js` does the geometry: quad codes, a sky grid, the quad index with its code hash, similarity fitting and the chance-match verification. It runs under node. `Sky.js` builds the index once from the Gaia catalogue (local, else online through the existing `Sky.querySources`), caches it on disk, runs the blind solve on the working copy's stars, and passes the found centre and scale to the existing `Sky.solveWithHints`. `FlyThrough.js` calls the blind solve when an unsolved image has no complete hints, or when the hints fail.

**Tech Stack:** PJSR (PixInsight JavaScript, ECMAScript 5 style in libraries: `var`, `function`), node for the pure suite (`node ci/run-tests.js`), the PixInsight suite (`script/selftest.js` in the slot-2 test instance).

**Spec:** `docs/superpowers/specs/2026-09-25-blind-solver-design.md`

## Global Constraints

- JavaScript only. No external program (ASTAP, astrometry.net, solve-field) and no external solving service. The only network use is the existing Gaia fallback (`Sky.querySources` → `Sky.queryOnline`, VizieR I/355/gaiadr3).
- Catalogue order: the local Gaia database set up in Process → Gaia (DR3/SP, then DR3), else Gaia DR3 online. This is exactly what `Sky.querySources` already does. Don't write a second catalogue path.
- Fully blind: nothing may read the focal length, pixel size, RA/Dec or object name inside the blind path.
- The final solution always comes from PixInsight's ImageSolver via `Sky.solveWithHints`. A blind result that ImageSolver can't confirm is never used. (A real ASTAP run produced a confident wrong solution at RA 172.1°, Dec +26.6° for the Iris, so a blind match alone can't be trusted.)
- The index is built once and cached in a persistent per-user folder, `File.homeDirectory + "/PixInsight/Loom/solver"`. It's keyed by `Solve.INDEX_VERSION` and never rebuilt otherwise.
- The committed suite never reads the user's images or the user's index, and never touches the network: `runFlyTests` already stubs `Sky.fetchText`, and solver tests inject their own catalogue and their own index folder.
- Tests run in the persistent slot-2 PixInsight (`/tmp/agent-scratch/pi-suite.sh`). Never close it between runs, and never use the user's main PixInsight.
- The dialog stays usable during the index build and the solve. Cancel works: long loops call a `tick()` that pumps events and throws a `loomCancel` error when cancelled.
- Match the surrounding code: 3-space indent, spaces inside parentheses (`f( a, b )`), `/* */` comments that say why, and `Namespace.name = function( … )` definitions.
- Never use the word "wedged" anywhere: code, comments, commits, board posts.
- **Field range for v1:** image short side ≥ 0.45° and long side ≤ 25°. The smallest quad band starts at 0.3°. *(This is narrower than the spec's 0.2°; see "Rulings" below.)*

## Rulings made while planning (reported to the maintainer)

1. **Smallest field is 0.45°, not 0.2°.** A whole-sky index for 0.1° quads needs millions of cells, which means hundreds of MB to GB of index. Starting the bands at 0.3° covers the Iris (0.61° × 0.92°) and every Loom image seen so far, at about 100 MB. *Cost if wrong:* a narrow-field image (for example a long-focal-length galaxy crop) can't be blind-solved and still needs an object name.
2. **Index size is about 100 MB, not "tens of MB".** Measured in Task 7, step 6; reported, not hidden.
3. **The file-hint step (spec order, step 2)** reads XISF `Observation:Center:RA/Dec`, then the FITS keywords (`RA`/`DEC`, `OBJCTRA`/`OBJCTDEC`, `CRVAL1`/`CRVAL2` on an RA axis), then `OBJECT`/`Observation:Object:Name` through `Fly.findObject`, then folder names up to 3 levels up. The hints researcher found that the user's finished TIFF/PSB/PNG/JPG exports carry **no** centre (only masters and lights do), so on real finished images folder names and blind solving do the work. `Fly.findObject` also learns letter-suffixed ids ("IC 1396A" → IC1396): that's the user's own ASIAIR target name, and it resolved to nothing. *Deferred:* a candidate list of past solved centres, which the blind solver makes unnecessary.

## Revision 3 (the maintainer, 2026-09-25): the index builds fully in the background

The maintainer's words: "the solver building must be fully in background". A PJSR script has one thread, and a second PixInsight was ruled out earlier for star removal. So the build runs as **timer-driven slices on the dialog's event loop**: each slice is one catalogue tile, or one batch of quad cells, and stays within about 100 ms of JavaScript. This supersedes the blocking `Sky.buildSolverIndex` call inside `Sky.solverIndex` and `Sky.solveBlind`.

- **Solve (Core, Task 5):** a resumable quad builder is added. `Solve.makeIndex` stays, as a thin loop over it, so every existing test still holds.
  ```js
  /* Builds an index a slice at a time: step( maxCells ) makes the quads of up to maxCells more cells; done() when all bands are made. */
  Solve.IndexBuilder = function( stars, bands, Q ) { … };
  Solve.IndexBuilder.prototype.step = function( maxCells ) { … return this.done(); };
  Solve.IndexBuilder.prototype.done = function() { … };
  Solve.IndexBuilder.prototype.fraction = function() { … };   // 0..1, over all bands' cells
  Solve.IndexBuilder.prototype.index = function() { … };      // the finished index (Solve.finishIndex), only when done()
  Solve.makeIndex = function( stars, bands, Q ) { var b = new Solve.IndexBuilder( stars, bands, Q ); while ( !b.step( 1e9 ) ) {} return b.index(); };
  ```
  `bandQuads` becomes per-cell: the IndexBuilder keeps each band's grid, its list of cells and a cursor. `Solve.bandQuads` stays, for its tests, as the same per-cell code run over every cell.
  - Test: `IndexBuilder in slices makes the same index as makeIndex`. Run step(7) repeatedly and compare `quads`, `codes` and `band` element by element with `makeIndex` on the same stars.
- **Sky (Task 7):** `Sky.buildSolverIndex` becomes a state machine: `Sky.IndexBuild( opts )` with:
  - `step( budgetMs ) → true when finished`;
  - `fraction()` and `text()` (for example "Star index for blind solving: reading the catalogue, 34%");
  - `checkpoint()`;
  - `error` (set when it failed; the message is as before).

  The phases are:
  1. **tiles:** one `Sky.catalogueTile` per step, checkpointing every 50 tiles and on `checkpoint()`;
  2. **quads:** `IndexBuilder.step` in batches until the budget is spent;
  3. **write:** as before, header last.

  It never calls `processEvents`: the caller's timer returns to the event loop between steps.

  `Sky.buildSolverIndex( progress, opts )` remains as a blocking wrapper, used only by tests and the ad hoc real build:
  ```js
  var b = new Sky.IndexBuild( opts );
  while ( !b.step( 200 ) )
  {
     if ( progress.isCancelled && progress.isCancelled() ) { b.checkpoint(); throw Sky.cancelled(); }
     progress.stage( b.text(), Math.round( 1000*b.fraction() ), 1000 );
  }
  if ( b.error ) throw b.error;
  return b.index;
  ```
  All of Task 7's existing tests keep passing through the wrapper. New tests:
  - `IndexBuild steps are short`: on the test patch, no `step( 50 )` takes longer than 500 ms, measured with `Date.now()`.
  - `IndexBuild: checkpoint then a new IndexBuild resumes`.
- **Sky (Task 8):** `Sky.solverIndex( progress )` no longer builds. It returns the loaded index, or `null` when none is ready. When `Sky.solveBlind` gets `null`, it throws an error with `needsIndex = true` and the message "Blind solving needs the star index, which is still being built in the background (N%). The analysis starts again by itself when it is ready." The PixInsight test for this stubs the index to `null`.
- **Dialog (Task 10):**
  - **Start:** when the Fly-Through dialog opens (in `onShow` or its constructor's end), it looks for the index with `Sky.loadSolverIndex`. The load happens in a single-shot timer after the dialog is shown, so opening stays instant. If there's no index, it starts `this.indexBuild = new Sky.IndexBuild( {} )`, driven by a `Timer` (`interval` 0.05 s, `periodic`) whose `onTimeout` calls `step( 100 )`.
  - **While the dialog is busy analysing or drafting,** the timer skips its step: the build never competes with the user's job, and never runs inside it.
  - **Status:** a small label under the hints row shows the build's `text()` and hides when the build is done.
  - **When the build finishes,** it sets `Sky._solverIndex`. If an analysis was waiting on it (an `identify` that threw `needsIndex`), it calls `scheduleAuto()`.
  - **When it fails,** the label shows the error, the timer stops, and the next dialog opening retries.
  - **Closing the dialog** (in `closing()`, where the options are already saved) calls `this.indexBuild.checkpoint()` and stops the timer.
  - **In the analysis,** a `needsIndex` error is shown as the status line, not as an error box, and the analysis is marked waiting.

  Tests (PixInsight):
  - `the dialog starts the index build when none exists`: stub `Sky.loadSolverIndex` to return `null` and `Sky.IndexBuild` to a counter; construct the dialog.
  - `a needsIndex analysis restarts when the build finishes`: stub a build that finishes on its second step, and check that `scheduleAuto` is called.
  - `closing checkpoints the build`.
- **Global constraint added:** no blocking whole-sky work ever runs on the dialog's thread inside a user action. The only blocking path is the test and ad hoc wrapper.

## Revision 1 (Codex round 1, 2026-09-25)

Codex found no bug in the core maths: its code was run from this plan in node and passes all 75 of its tests in 2 s. What it found is that real-world behaviour isn't proven. Changes:
- **Real-field gate (new Task 6b)** before any whole-sky work. A local index is built from Gaia around each of the three real images, and the blind solve must succeed on their real working copies. Constants are tuned there, from measurements. This covers findings 1, 7 and 8.
- **Build benchmark (Task 5, step 6).** A synthetic whole-sky-sized catalogue goes through Keeper → makeIndex in node, with the time and peak heap recorded, before the PJSR build. `cellsNear` dedupes with an object, not `indexOf`. (Finding 3.)
- **Catalogue failures versus empty sky (Task 7).**
  - A failed query (an answer without `origin`) is retried 3 times.
  - The build stops with the "no catalogue" message only when the first tiles all fail.
  - An online answer that hits `Fly.GAIA_ONLINE_MAX_ROWS` is split into 4 sub-tiles.

  (Finding 4.)
- **Index identity (Task 7).** The header and the checkpoint carry the full build configuration (`version`, bands, `gMax`, M, Q, tile radius, tile count) as `config`. Load and resume both reject a mismatch; `origin` is recorded. (Finding 5.)
- **Cancel (Task 8).** `Sky.solveWithHints` rethrows `loomCancel` errors instead of collecting them as a failed scale. (Finding 6.)
- **Chance and spread (Task 6).** Matches must spread over at least 4 cells of a 3×3 grid on the image. The chance threshold is corrected for the whole verification budget, fixed before the first check: accept when `log10Chance <= MAX_LOG10_CHANCE - log10(min(hypotheses, MAX_VERIFY))`. (Finding 7.)
- **Confirmation checks the position (Task 8).** After ImageSolver, the solved centre must lie within 10% of the field diagonal of the blind centre, and the solved scale within 3% of the blind scale. Otherwise that candidate is rejected. (Finding 2.)
- **PixInsight synthetic test (Task 8).** It renders with one `setSamples` buffer and asserts StarDetector finds at least 100 stars before solving. The real ImageSolver hand-off is checked ad hoc on real images (Task 8, step 6), because CI has no Gaia database. (Finding 9.)
- **Hint order (Task 9).** The header centre is read before names. A `CRVAL` without a full WCS is used only as a rough hint. (A full WCS is already an astrometric solution through `Sky.keywordWcs`.) (Finding 10.)
- **Blind results aren't remembered as focal/pixel hints (Task 10).** The solved working copy is cached by `saveWork`, which is enough. (Finding 11.)
- **Swarm.** Each agent works in its own worktree and branch. The orchestrator integrates them in dependency order, so nobody commits another agent's half-done `selftest.js` edits. (Finding 12.)

## Review Focus

1. **A drizzled image** (2× finer than the camera) solves blind: blind never uses the rig's scale, and the scale in the hints to `solveWithHints` comes from the blind fit. The expected behaviour is a solve at the drizzled scale (Iris: 0.483″/px full size). Test: Task 6's synthetic solve at two scales a factor 2 apart.
2. **A mirrored (flipped) image** solves, with the parity found. Test: Task 6's `mirrored field solves`.
3. **An image dominated by nebula knots and few real stars:** false detections must not produce a confident wrong answer. The expected behaviour is either the right solve or a clear "no match". Test: Task 6's field with 60% spurious detections.
4. **Cancelling during the index build**, then starting again, resumes from the checkpoint without starting over, and never loads a half-written index. Test: Task 7's `cancelled build resumes`.
5. **Offline, with no local Gaia**, the build stops quickly with a message that says what to install. It doesn't spin through thousands of empty tiles. Test: Task 7's `no catalogue: build fails fast with a clear message`.

---

## File Structure

| File | Responsibility |
|---|---|
| `script/lib/Solve.js` (new) | Pure solver maths: tangent plane, quad codes, sky grid, star keeper, band quads, code hash, index arrays, image quads, hypotheses, verification, `Solve.solve`. No PixInsight call, no file access. |
| `script/lib/Sky.js` | `Sky.solverIndexDir`, `Sky.buildSolverIndex`, `Sky.loadSolverIndex`, `Sky.solverIndex`, `Sky.solveBlind`, `Sky.cancelled`. |
| `script/lib/Fly.js` | `Fly.headerCentre` (FITS keywords → centre), `Fly.objectFromPath` (file name, then folder names). |
| `script/FlyThrough.js` | `#include "lib/Solve.js"`; `identify` falls back to blind; the dialog no longer blocks on missing hints; status texts. |
| `script/selftest.js` | `#include "lib/Solve.js"`; `runSolveTests()` (pure) called from `runFlyTestsClean`; PixInsight-only build/solve tests. |
| `ci/run-tests.js` | Add `"lib/Solve.js"` to `LIBS` after `lib/Fly.js`. |
| `CHANGELOG.md` | An `## [Unreleased]` entry. |

Load order everywhere: `Util, Cache, Psb, Steps, Frames, Fly, Solve, Sky, Render, …`. Solve depends only on `Fly` (it uses `Fly.RAD`).

Test commands used throughout:
- Node: `cd ~/PixInsight/scripts/Loom && node ci/run-tests.js 2>&1 | tail -5`
- Node, 1.9.4 branch: `LOOM_TEST_CORE_RELEASE=4 node ci/run-tests.js 2>&1 | tail -3`
- PixInsight (slot 2, persistent): `/tmp/agent-scratch/pi-suite.sh ~/PixInsight/scripts/Loom/script/selftest.js` (prints `PASS n run, 0 failed` or the failures). The runner holds the board's `test` claim: post `CLAIM test` before and `RELEASE test` after.

---

### Task 1: Solve.js skeleton, tangent plane and quad codes

**Files:**
- Create: `script/lib/Solve.js`
- Modify: `ci/run-tests.js` (LIBS), `script/selftest.js` (include + `runSolveTests`), `script/FlyThrough.js` (include)

**Interfaces:**
- Produces:
  - `Solve.toPlane( centre{ra,dec}, ra, dec ) → [xi, eta]` in degrees, or `null` behind the plane;
  - `Solve.fromPlane( centre, xi, eta ) → {ra, dec}`;
  - `Solve.quadCode( pts[4] of [x,y] ) → { code: [cx,cy,dx,dy], order: [iA,iB,iC,iD] } | null`. `order` indexes into `pts`; the result is `null` when C or D lies outside the circle on AB.
  - `Solve.CODE_TOL = 0.01`.

- [ ] **Step 1: Wire the empty library in and write the failing tests**

`script/lib/Solve.js`:
```js
/*
 * Loom's own blind plate solver: the maths. Pure -- no PixInsight call, no
 * file access -- so it runs under node. Geometric hashing of star quads
 * (astrometry.net's method): four stars give a code that shift, turn and
 * zoom leave alone, so no plate scale is needed.
 */
var Solve = {};
```
Add `"lib/Solve.js"` to `LIBS` in `ci/run-tests.js` right after `"lib/Fly.js"`. Add `#include "lib/Solve.js"` after `#include "lib/Fly.js"` in both `script/selftest.js` and `script/FlyThrough.js`.

In `script/selftest.js`, add after `runFlyTestsClean`:
```js
/* Loom's blind solver. Solve.js is pure and runs under node. */
function runSolveTests()
{
   check( "Solve loads", typeof Solve, "object" );
   function near( a, b, e ) { return Math.abs( a - b ) <= e; }

   /* ---- tangent plane ---------------------------------------------- */
   ( function()
   {
      var c = { ra: 315.4, dec: 68.1 };
      var p = Solve.toPlane( c, 315.4, 68.1 );
      check( "toPlane: the centre is the origin", near( p[0], 0, 1e-12 ) && near( p[1], 0, 1e-12 ), true );
      var q = Solve.toPlane( c, 316.4, 68.6 ), back = Solve.fromPlane( c, q[0], q[1] );
      check( "fromPlane undoes toPlane", near( back.ra, 316.4, 1e-9 ) && near( back.dec, 68.6, 1e-9 ), true );
      check( "toPlane: east is +xi", Solve.toPlane( c, 315.5, 68.1 )[0] > 0, true );
      check( "toPlane: north is +eta", Solve.toPlane( c, 315.4, 68.2 )[1] > 0, true );
      check( "toPlane: the far side is null", Solve.toPlane( c, 135.4, -68.1 ), null );
      var w = Solve.fromPlane( { ra: 359.9, dec: 0 }, 0.3, 0 );
      check( "fromPlane wraps RA into [0,360)", near( w.ra, 0.2, 1e-4 ), true );   // atan: 0.3 on the plane is 0.29999 degrees
   } )();

   /* ---- quad codes --------------------------------------------------- */
   ( function()
   {
      var pts = [ [ 0, 0 ], [ 10, 10 ], [ 3, 6 ], [ 7, 2 ] ];
      var q = Solve.quadCode( pts );
      check( "quadCode: A and B are the farthest pair", [ q.order[0], q.order[1] ].sort().join(), "0,1" );
      check( "quadCode: C is left of D", q.code[0] <= q.code[2], true );
      check( "quadCode: canonical (cx + dx <= 1)", q.code[0] + q.code[2] <= 1 + 1e-12, true );
      function moved( f ) { return pts.map( function( p ) { return f( p[0], p[1] ); } ); }
      function same( a, b ) { return a.code.every( function( v, i ) { return near( v, b.code[i], 1e-9 ); } ); }
      var t = 0.7, s = 3.2;
      check( "quadCode: shift, turn and zoom leave the code alone",
             same( q, Solve.quadCode( moved( function( x, y ) { return [ 5 + s*( x*Math.cos( t ) - y*Math.sin( t ) ), -8 + s*( x*Math.sin( t ) + y*Math.cos( t ) ) ]; } ) ) ), true );
      var shuffled = [ pts[2], pts[0], pts[3], pts[1] ], r = Solve.quadCode( shuffled );
      check( "quadCode: the input order does not matter", same( q, r ), true );
      check( "quadCode: order maps back to the same stars", shuffled[r.order[0]].join() + shuffled[r.order[2]].join(), pts[q.order[0]].join() + pts[q.order[2]].join() );
      var m = Solve.quadCode( moved( function( x, y ) { return [ x, -y ]; } ) );
      check( "quadCode: a mirror image has a different code", same( q, m ), false );
      check( "quadCode: a star outside the AB circle is no quad", Solve.quadCode( [ [ 0, 0 ], [ 10, 0 ], [ 5, 4.9 ], [ 9.5, 3 ] ] ), null );
   } )();
}
```
Call it from the end of `runFlyTestsClean()`: add `runSolveTests();` as its last statement.

- [ ] **Step 2: Run the node suite and see it fail**

Run: `node ci/run-tests.js 2>&1 | tail -5`
Expected: FAIL. It reports `Solve.toPlane is not a function`, or the `toPlane`/`quadCode` checks fail.

- [ ] **Step 3: Implement**

Append to `script/lib/Solve.js`:
```js
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
```

- [ ] **Step 4: Run the node suite and see it pass**

Run: `node ci/run-tests.js 2>&1 | tail -5` and `LOOM_TEST_CORE_RELEASE=4 node ci/run-tests.js 2>&1 | tail -3`
Expected: both end with `every runnable assertion passed`.

- [ ] **Step 5: Commit**

```bash
git add script/lib/Solve.js ci/run-tests.js script/selftest.js script/FlyThrough.js
git commit -m "Solve: tangent plane and quad codes"
```

---

### Task 2: Similarity fit

**Files:**
- Modify: `script/lib/Solve.js`, `script/selftest.js` (inside `runSolveTests`)

**Interfaces:**
- Consumes: nothing new.
- Produces: `Solve.fitSimilarity( img[n] of [x,y], sky[n] of [xi,eta], parity 0|1 ) → { a, b, tx, ty, parity, scale, rms }`. This is least squares for `xi = a·u − b·v + tx`, `eta = b·u + a·v + ty`, where `(u,v) = (x, parity ? −y : y)`; `scale = hypot(a,b)` in degrees per pixel, and `rms` is in degrees. `Solve.applySimilarity( fit, x, y ) → [xi, eta]`; `Solve.invertSimilarity( fit, xi, eta ) → [x, y]`.

- [ ] **Step 1: Write the failing test** (append inside `runSolveTests`)

```js
   /* ---- similarity fit ------------------------------------------------ */
   ( function()
   {
      var truth = { a: 1e-4*Math.cos( 0.4 ), b: 1e-4*Math.sin( 0.4 ), tx: 0.2, ty: -0.1 };
      [ 0, 1 ].forEach( function( parity )
      {
         var img = [ [ 10, 20 ], [ 900, 40 ], [ 400, 700 ], [ 50, 600 ], [ 700, 500 ] ];
         var sky = img.map( function( p ) { var u = p[0], v = parity ? -p[1] : p[1]; return [ truth.a*u - truth.b*v + truth.tx, truth.b*u + truth.a*v + truth.ty ]; } );
         var f = Solve.fitSimilarity( img, sky, parity );
         check( "fitSimilarity recovers the transform (parity " + parity + ")",
                near( f.a, truth.a, 1e-12 ) && near( f.b, truth.b, 1e-12 ) && near( f.tx, 0.2, 1e-9 ) && near( f.ty, -0.1, 1e-9 ), true );
         check( "fitSimilarity: exact points, no residual (parity " + parity + ")", f.rms < 1e-12, true );
         var p = Solve.applySimilarity( f, 123, 456 ), back = Solve.invertSimilarity( f, p[0], p[1] );
         check( "invertSimilarity undoes applySimilarity (parity " + parity + ")", near( back[0], 123, 1e-6 ) && near( back[1], 456, 1e-6 ), true );
      } );
      check( "fitSimilarity: scale is degrees per pixel", near( Solve.fitSimilarity( [ [ 0, 0 ], [ 100, 0 ] ], [ [ 0, 0 ], [ 0.01, 0 ] ], 0 ).scale, 1e-4, 1e-15 ), true );
   } )();
```

- [ ] **Step 2: Run and see it fail**

Run: `node ci/run-tests.js 2>&1 | tail -5`
Expected: FAIL on `Solve.fitSimilarity is not a function`.

- [ ] **Step 3: Implement**

```js
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
```

- [ ] **Step 4: Run and see it pass**

Run: `node ci/run-tests.js 2>&1 | tail -5`
Expected: `every runnable assertion passed`.

- [ ] **Step 5: Commit**

```bash
git add script/lib/Solve.js script/selftest.js
git commit -m "Solve: similarity fit"
```

---

### Task 3: Sky grid, star keeper, sky tiles and band quads

**Files:**
- Modify: `script/lib/Solve.js`, `script/selftest.js`

**Interfaces:**
- Consumes: `Solve.toPlane`, `Solve.quadCode` (Task 1).
- Produces:
  - `Solve.BANDS`: an array of `{ lo, hi }` in degrees. `lo = 0.3·√2^k` for k = 0..9, and `hi = lo·√2`.
  - `Solve.STARS_PER_CELL = 5`, `Solve.QUADS_PER_CELL = 2`, `Solve.INDEX_G_MAX = 13`, `Solve.TILE_RADIUS = 2`.
  - `Solve.cellOf( ra, dec, size ) → integer id`.
  - `Solve.cellsNear( ra, dec, radius, size ) → [ids]`.
  - `Solve.Keeper( sizes[] , M )`, an object:
    - `add( star{ra,dec,G} )`;
    - `stars() → [star]`, the union over all sizes, each star once;
    - `toArrays() → Float32Array` of [ra,dec,G]·n;
    - `Solve.Keeper.fromArrays( sizes, M, arr )`.
  - `Solve.skyTiles( radius ) → [{ra,dec}]`, covering the whole sky.
  - `Solve.bandQuads( stars[{ra,dec,G}], band, Q ) → [{ ids:[4], code:[4] }]`. The ids index into `stars`.

- [ ] **Step 1: Write the failing tests** (append inside `runSolveTests`)

```js
   /* ---- sky grid, keeper, tiles, band quads --------------------------- */
   /* A deterministic synthetic sky patch: n stars, uniform over [ra0, ra0+w] x [dec0, dec0+h], G 6..13. */
   function synthSky( seed, n, ra0, dec0, w, h )
   {
      var s = seed >>> 0, out = [];
      function rnd() { s = ( Math.imul( s, 1664525 ) + 1013904223 ) >>> 0; return s/4294967296; }
      for ( var i = 0; i < n; ++i )
         out.push( { ra: ra0 + w*rnd(), dec: dec0 + h*rnd(), G: 6 + 7*rnd() } );
      return out;
   }
   ( function()
   {
      check( "BANDS start at 0.3 degrees", near( Solve.BANDS[0].lo, 0.3, 1e-12 ), true );
      check( "BANDS: each hi is the next lo", Solve.BANDS.slice( 1 ).every( function( b, k ) { return near( b.lo, Solve.BANDS[k].hi, 1e-12 ); } ), true );
      check( "cellOf: nearby points share a cell", Solve.cellOf( 10.01, 20.01, 0.3 ) == Solve.cellOf( 10.02, 20.02, 0.3 ), true );
      check( "cellOf: far points do not", Solve.cellOf( 10, 20, 0.3 ) == Solve.cellOf( 11, 20, 0.3 ), false );
      check( "cellsNear includes the point's own cell", Solve.cellsNear( 10, 20, 0.5, 0.3 ).indexOf( Solve.cellOf( 10, 20, 0.3 ) ) >= 0, true );
      check( "cellsNear at the pole includes every column", Solve.cellsNear( 0, 89.9, 0.5, 0.3 ).indexOf( Solve.cellOf( 180, 89.95, 0.3 ) ) >= 0, true );

      var sky = synthSky( 7, 4000, 100, 30, 4, 4 );
      var keep = new Solve.Keeper( [ 0.3 ], 5 );
      sky.forEach( function( s ) { keep.add( s ); } );
      var kept = keep.stars(), perCell = {};
      kept.forEach( function( s ) { var c = Solve.cellOf( s.ra, s.dec, 0.3 ); perCell[c] = ( perCell[c] || 0 ) + 1; } );
      check( "Keeper keeps at most M per cell", Object.keys( perCell ).every( function( c ) { return perCell[c] <= 5; } ), true );
      var c0 = Solve.cellOf( kept[0].ra, kept[0].dec, 0.3 );
      var inCell = sky.filter( function( s ) { return Solve.cellOf( s.ra, s.dec, 0.3 ) == c0; } ).sort( function( a, b ) { return a.G - b.G; } ).slice( 0, 5 );
      check( "Keeper keeps the brightest", inCell.every( function( s ) { return kept.indexOf( s ) >= 0; } ), true );
      var round = Solve.Keeper.fromArrays( [ 0.3 ], 5, keep.toArrays() ).stars();
      check( "Keeper round-trips through arrays", round.length, kept.length );

      var tiles = Solve.skyTiles( 2 ), probe = synthSky( 11, 300, 0, -90, 360, 180 ), covered = true;
      probe.forEach( function( p ) { if ( !tiles.some( function( t ) { return Fly.separation( t, p ) <= 2; } ) ) covered = false; } );
      check( "skyTiles cover the sky", covered, true );
      check( "skyTiles: a few thousand at 2 degrees", tiles.length > 3000 && tiles.length < 7000, true );

      var quads = Solve.bandQuads( kept, Solve.BANDS[0], 2 );
      check( "bandQuads makes quads", quads.length > 100, true );
      check( "bandQuads: every quad's AB is in its band", quads.every( function( q )
      {
         var d = Fly.separation( kept[q.ids[0]], kept[q.ids[1]] );
         return d >= Solve.BANDS[0].lo - 1e-9 && d < Solve.BANDS[0].hi + 1e-9;
      } ), true );
      check( "bandQuads: no quad twice", quads.map( function( q ) { return q.ids.slice().sort().join(); } ).filter( function( k, i, all ) { return all.indexOf( k ) != i; } ).length, 0 );
   } )();
```

- [ ] **Step 2: Run and see it fail**

Run: `node ci/run-tests.js 2>&1 | tail -5`
Expected: FAIL on `Solve.BANDS` being undefined.

- [ ] **Step 3: Implement**

```js
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
Solve.TILE_RADIUS = 2;       // degrees, one catalogue query while building

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
 * the catalogue streams past tile by tile (the whole sky to G 13 is ~10M
 * stars; only a few hundred thousand per size are kept).
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
      if ( list.some( function( o ) { return o.ra == s.ra && o.dec == s.dec; } ) ) continue;   // tiles overlap
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

/*
 * A band's quads: per cell, bright A first, B a band-length away, C and D
 * the two brightest stars inside the circle on AB. Codes are made on the
 * tangent plane at A, the way the image's are made on its pixels.
 */
Solve.bandQuads = function( stars, band, Q )
{
   var size = band.lo, grid = {}, out = [], seen = {};
   stars.forEach( function( s, i ) { var c = Solve.cellOf( s.ra, s.dec, size ); ( grid[c] || ( grid[c] = [] ) ).push( i ); } );
   for ( var cell in grid )
   {
      var own = grid[cell].slice().sort( function( a, b ) { return stars[a].G - stars[b].G; } ), made = 0;
      var A0 = stars[own[0]], near = [];
      Solve.cellsNear( A0.ra, A0.dec, band.hi + size, size ).forEach( function( c ) { if ( grid[c] ) near = near.concat( grid[c] ); } );
      near.sort( function( a, b ) { return stars[a].G - stars[b].G; } );
      for ( var i = 0; i < own.length && made < Q; ++i )
         for ( var j = 0; j < near.length && made < Q; ++j )
         {
            var a = own[i], b = near[j];
            if ( a == b ) continue;
            var dAB = Fly.separation( stars[a], stars[b] );
            if ( dAB < band.lo || dAB >= band.hi ) continue;
            var pA = Solve.toPlane( stars[a], stars[a].ra, stars[a].dec ), pB = Solve.toPlane( stars[a], stars[b].ra, stars[b].dec );
            var mid = [ ( pA[0] + pB[0] )/2, ( pA[1] + pB[1] )/2 ], r2 = ( ( pB[0] - pA[0] )*( pB[0] - pA[0] ) + ( pB[1] - pA[1] )*( pB[1] - pA[1] ) )/4;
            var inside = [];
            for ( var k = 0; k < near.length && inside.length < 2; ++k )
            {
               var c = near[k];
               if ( c == a || c == b ) continue;
               var p = Solve.toPlane( stars[a], stars[c].ra, stars[c].dec );
               if ( p && ( p[0] - mid[0] )*( p[0] - mid[0] ) + ( p[1] - mid[1] )*( p[1] - mid[1] ) < r2 ) inside.push( { i: c, p: p } );
            }
            if ( inside.length < 2 ) continue;
            var ids = [ a, b, inside[0].i, inside[1].i ], q = Solve.quadCode( [ pA, pB, inside[0].p, inside[1].p ] );
            if ( !q ) continue;
            var key = ids.slice().sort().join();
            if ( seen[key] ) continue;
            seen[key] = true;
            out.push( { ids: q.order.map( function( o ) { return ids[o]; } ), code: q.code } );
            ++made;
         }
   }
   return out;
};
```

- [ ] **Step 4: Run and see it pass**

Run: `node ci/run-tests.js 2>&1 | tail -5`
Expected: `every runnable assertion passed`. If `skyTiles cover the sky` fails near the poles, fix `skyTiles` (the pole row must reach 90°). Don't loosen the test.

- [ ] **Step 5: Commit**

```bash
git add script/lib/Solve.js script/selftest.js
git commit -m "Solve: sky grid, star keeper, sky tiles and band quads"
```

---

### Task 4: Code hash

**Files:**
- Modify: `script/lib/Solve.js`, `script/selftest.js`

**Interfaces:**
- Consumes: `Solve.CODE_TOL`.
- Produces:
  - `Solve.buildHash( codes Float32Array(4n) ) → { keys: Float64Array(n) sorted, order: Uint32Array(n) }`.
  - `Solve.lookup( hash, codes, code[4], tol ) → [quad indices]`. Every result is within `tol` Euclidean distance in code space.

- [ ] **Step 1: Write the failing test** (append inside `runSolveTests`)

```js
   /* ---- code hash ------------------------------------------------------ */
   ( function()
   {
      var n = 20000, codes = new Float32Array( 4*n ), s = 5;
      function rnd() { s = ( Math.imul( s, 1664525 ) + 1013904223 ) >>> 0; return s/4294967296; }
      for ( var i = 0; i < 4*n; ++i ) codes[i] = -0.2 + 1.4*rnd();
      var h = Solve.buildHash( codes ), q = [ codes[400], codes[401], codes[402], codes[403] ];
      var hits = Solve.lookup( h, codes, [ q[0] + 0.004, q[1] - 0.004, q[2], q[3] ], 0.01 );
      check( "lookup finds a near code", hits.indexOf( 100 ) >= 0, true );
      var brute = [];
      for ( var k = 0; k < n; ++k )
      {
         var d = 0;
         for ( var j = 0; j < 4; ++j ) d += ( codes[4*k + j] - q[j] )*( codes[4*k + j] - q[j] );
         if ( Math.sqrt( d ) <= 0.01 ) brute.push( k );
      }
      check( "lookup matches brute force", Solve.lookup( h, codes, q, 0.01 ).sort( function( a, b ) { return a - b; } ).join(), brute.join() );
      check( "lookup: nothing far away", Solve.lookup( h, codes, [ 5, 5, 5, 5 ], 0.01 ).length, 0 );
   } )();
```

- [ ] **Step 2: Run and see it fail**

Run: `node ci/run-tests.js 2>&1 | tail -5`
Expected: FAIL on `Solve.buildHash is not a function`.

- [ ] **Step 3: Implement**

```js
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

Solve.lookup = function( hash, codes, code, tol )
{
   var B = Solve.HASH_BINS, w = Solve.HASH_BIN, out = [];
   var base = code.map( function( v ) { return Math.floor( ( v + 0.25 )/w ); } );
   for ( var d0 = -1; d0 <= 1; ++d0 ) for ( var d1 = -1; d1 <= 1; ++d1 ) for ( var d2 = -1; d2 <= 1; ++d2 ) for ( var d3 = -1; d3 <= 1; ++d3 )
   {
      var b = [ base[0] + d0, base[1] + d1, base[2] + d2, base[3] + d3 ];
      if ( b.some( function( v ) { return v < 0 || v >= B; } ) ) continue;
      var key = ( ( b[0]*B + b[1] )*B + b[2] )*B + b[3];
      for ( var i = Solve.lowerBound( hash.keys, key ); i < hash.keys.length && hash.keys[i] == key; ++i )
      {
         var q = hash.order[i], e = 0;
         for ( var j = 0; j < 4; ++j ) e += ( codes[4*q + j] - code[j] )*( codes[4*q + j] - code[j] );
         if ( e <= tol*tol ) out.push( q );
      }
   }
   return out;
};
```

- [ ] **Step 4: Run and see it pass**

Run: `node ci/run-tests.js 2>&1 | tail -5`
Expected: `every runnable assertion passed`.

- [ ] **Step 5: Commit**

```bash
git add script/lib/Solve.js script/selftest.js
git commit -m "Solve: code hash"
```

---

### Task 5: The index object and its arrays

**Files:**
- Modify: `script/lib/Solve.js`, `script/selftest.js`

**Interfaces:**
- Consumes: `Solve.Keeper`, `Solve.bandQuads`, `Solve.buildHash`, `Solve.BANDS`.
- Produces:
  - `Solve.INDEX_VERSION = 1`.
  - `Solve.makeIndex( stars[{ra,dec,G}], bands, Q ) → index`, where `index = { version, bands, stars: Float32Array(3m), quads: Float32Array(4n) star ids, band: Float32Array(n), codes: Float32Array(4n), hash, grid }`. `grid` is a star lookup on 1° cells: `{ cellId: [star index…] }`.
  - `Solve.indexArrays( index ) → { header: {version, bands, lengths:[…]}, arrays: [Float32Array…] }`.
  - `Solve.indexFromArrays( header, arrays ) → index`: the hash and grid are rebuilt.
  - `Solve.starsNear( index, centre, radius ) → [star index]`.

- [ ] **Step 1: Write the failing test** (append inside `runSolveTests`)

```js
   /* ---- index ----------------------------------------------------------- */
   ( function()
   {
      var keep = new Solve.Keeper( Solve.BANDS.slice( 0, 3 ).map( function( b ) { return b.lo; } ), 5 );
      synthSky( 21, 6000, 200, -20, 5, 5 ).forEach( function( s ) { keep.add( s ); } );
      var idx = Solve.makeIndex( keep.stars(), Solve.BANDS.slice( 0, 3 ), 2 );
      check( "makeIndex has quads in each band", [ 0, 1, 2 ].every( function( b ) { for ( var i = 0; i < idx.band.length; ++i ) if ( idx.band[i] == b ) return true; return false; } ), true );
      var io = Solve.indexArrays( idx ), back = Solve.indexFromArrays( JSON.parse( JSON.stringify( io.header ) ), io.arrays );
      check( "index round-trips: stars", back.stars.length, idx.stars.length );
      check( "index round-trips: quads", back.quads.length, idx.quads.length );
      check( "index round-trips: the hash finds quad 0", Solve.lookup( back.hash, back.codes, Array.prototype.slice.call( back.codes, 0, 4 ), 1e-6 ).indexOf( 0 ) >= 0, true );
      var c = { ra: 202.5, dec: -17.5 }, near = Solve.starsNear( idx, c, 0.5 );
      var brute = 0;
      for ( var i = 0; i < idx.stars.length; i += 3 ) if ( Fly.separation( c, { ra: idx.stars[i], dec: idx.stars[i + 1] } ) <= 0.5 ) ++brute;
      check( "starsNear matches brute force", near.length, brute );
   } )();
```

- [ ] **Step 2: Run and see it fail**

Run: `node ci/run-tests.js 2>&1 | tail -5`
Expected: FAIL on `Solve.makeIndex is not a function`.

- [ ] **Step 3: Implement**

```js
Solve.INDEX_VERSION = 1;     // bump when the index's content or format changes: a new one is built
Solve.GRID_DEG = 1;          // star lookup cells for verification

Solve.makeIndex = function( stars, bands, Q )
{
   var ids = [], codes = [], band = [];
   bands.forEach( function( b, k )
   {
      Solve.bandQuads( stars, b, Q ).forEach( function( q )
      {
         ids.push( q.ids[0], q.ids[1], q.ids[2], q.ids[3] );
         codes.push( q.code[0], q.code[1], q.code[2], q.code[3] );
         band.push( k );
      } );
   } );
   var s = new Float32Array( 3*stars.length );
   stars.forEach( function( t, i ) { s[3*i] = t.ra; s[3*i + 1] = t.dec; s[3*i + 2] = t.G; } );
   return Solve.finishIndex( { version: Solve.INDEX_VERSION, bands: bands, stars: s,
                               quads: new Float32Array( ids ), codes: new Float32Array( codes ), band: new Float32Array( band ) } );
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
```

- [ ] **Step 4: Run and see it pass**

Run: `node ci/run-tests.js 2>&1 | tail -5`
Expected: `every runnable assertion passed`.

- [ ] **Step 5: Commit**

```bash
git add script/lib/Solve.js script/selftest.js
git commit -m "Solve: index object and its arrays"
```

- [ ] **Step 6: Benchmark a whole-sky-sized build in node (ad hoc, not committed)**

Write `/tmp/agent-scratch/blind/bench.js`. It loads `script/lib/Fly.js` and `script/lib/Solve.js` the way `ci/run-tests.js` does (reuse its `load`, or `require("./ci/pjsr-shim.js")` and eval both files). It then streams a synthetic whole sky through `Solve.Keeper( Solve.BANDS.map( b => b.lo ), 5 )`:
- 10M stars;
- uniform on the sphere (dec = asin(2u−1));
- G drawn so that N(<G) grows 10^(0.4 G), capped at 13;
- a 5× denser band within ±10° of an arbitrary great circle, standing in for the galactic plane.

After that it runs `Solve.makeIndex( keep.stars(), Solve.BANDS, 2 )`, then `Solve.indexArrays`.

Record on the board and in the ledger:
- time per phase;
- `process.memoryUsage().heapUsed` peak (run with `node --max-old-space-size=8192`);
- stars kept, and quads per band;
- total array bytes.

**Acceptance:** under 15 min in node, peak heap under 3 GB, arrays under 200 MB. PJSR's V8 is slower than node, so leave margin. If the benchmark fails, fix the hot spot and re-measure. Candidates: `bandQuads`' per-cell `near.concat`/sort (precompute per-cell sorted lists once), `Fly.separation` in the inner loop (compare unit vectors), and Keeper's string keys. Don't touch the band or cell constants; those are Task 6b's to set.

---

### Task 6: Matching and verification (`Solve.solve`)

**Files:**
- Modify: `script/lib/Solve.js`, `script/selftest.js`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `Solve.imageQuads( dets[{x,y,flux}], N ) → [{ pts:[4][x,y] in canonical order, code, parity, diameter }]`: both parities.
  - `Solve.log10Chance( k, n, p ) → log10 P(X ≥ k)`, for X ~ Binomial(n, p).
  - `Solve.verify( index, fit, centre, dets, W, H ) → { matches, of, log10Chance, fit, centre }`.
  - `Solve.solve( index, dets, W, H, opts{ tick(), maxResults } ) → [result]`, best first, where `result = { ra, dec, scale (arcsec/px), rotation (deg), parity, matches, of, log10Chance }`.
  - Constants: `Solve.IMAGE_QUAD_STARS = 40`, `Solve.VERIFY_STARS = 300`, `Solve.MIN_MATCHES = 8`, `Solve.MAX_LOG10_CHANCE = -10`, `Solve.MAX_VERIFY = 3000`, `Solve.FIELD_MIN = 0.3`, `Solve.FIELD_MAX = 25`.

- [ ] **Step 1: Write the failing tests** (append inside `runSolveTests`)

```js
   /* ---- blind solve on synthetic fields --------------------------------- */
   ( function()
   {
      // one index for a 12x12 degree patch; fields are cut out of it
      var bands = Solve.BANDS.slice( 0, 6 ), keep = new Solve.Keeper( bands.map( function( b ) { return b.lo; } ), 5 );
      var sky = synthSky( 99, 60000, 60, 10, 12, 12 );
      sky.forEach( function( s ) { keep.add( s ); } );
      var idx = Solve.makeIndex( keep.stars(), bands, 2 );

      /* An image of `sky` at centre, scale (deg/px), turn (rad), parity; drop and spurious are fractions. */
      function field( centre, scale, turn, parity, drop, spurious, seed )
      {
         var W = 3840, H = 2560, s = seed >>> 0, dets = [];
         function rnd() { s = ( Math.imul( s, 1664525 ) + 1013904223 ) >>> 0; return s/4294967296; }
         var f = { a: scale*Math.cos( turn ), b: scale*Math.sin( turn ), parity: parity, tx: 0, ty: 0 };
         var c0 = Solve.applySimilarity( f, W/2, H/2 ); f.tx = -c0[0]; f.ty = -c0[1];
         sky.forEach( function( t )
         {
            var p = Solve.toPlane( centre, t.ra, t.dec );
            if ( !p ) return;
            var xy = Solve.invertSimilarity( f, p[0], p[1] );
            if ( xy[0] < 0 || xy[1] < 0 || xy[0] >= W || xy[1] >= H || rnd() < drop ) return;
            dets.push( { x: xy[0] + 0.3*( rnd() - 0.5 ), y: xy[1] + 0.3*( rnd() - 0.5 ), flux: Math.pow( 10, -0.4*t.G )*( 0.8 + 0.4*rnd() ) } );
         } );
         var real = dets.length;
         for ( var k = 0; k < spurious*real; ++k ) dets.push( { x: W*rnd(), y: H*rnd(), flux: Math.pow( 10, -0.4*( 8 + 5*rnd() ) ) } );
         return { dets: dets, W: W, H: H };
      }
      function solves( name, centre, scale, turn, parity, drop, spurious )
      {
         var f = field( centre, scale, turn, parity, drop, spurious, 3 ), t0 = Date.now();
         var r = Solve.solve( idx, f.dets, f.W, f.H, {} )[0];
         check( name + ": solved", !!r, true );
         if ( !r ) return;
         check( name + ": centre within 0.01 deg", Fly.separation( r, centre ) < 0.01, true );
         check( name + ": scale within 0.5%", Math.abs( r.scale/( scale*3600 ) - 1 ) < 0.005, true );
         check( name + ": parity", r.parity, parity );
         check( name + ": under 20 s", Date.now() - t0 < 20000, true );
      }
      solves( "1.2 deg field", { ra: 66, dec: 16 }, 1.2/3840, 0.3, 0, 0.2, 0.2 );
      solves( "same sky at half the scale (drizzled)", { ra: 66, dec: 16 }, 0.6/3840, 0.3, 0, 0.2, 0.2 );
      solves( "mirrored field solves", { ra: 65, dec: 15 }, 1.5/3840, 2.0, 1, 0.2, 0.2 );
      solves( "field with 60% spurious detections", { ra: 67, dec: 17 }, 2/3840, -1.0, 0, 0.3, 0.6 );
      solves( "4 deg field", { ra: 66, dec: 16 }, 4/3840, 1.0, 0, 0.1, 0.1 );

      var blank = Solve.solve( idx, [], 3840, 2560, {} );
      check( "no stars: no solution", blank.length, 0 );
      var few = field( { ra: 66, dec: 16 }, 1.2/3840, 0, 0, 0, 0, 1 ).dets.slice( 0, 5 );
      check( "five stars: no solution", Solve.solve( idx, few, 3840, 2560, {} ).length, 0 );
      var elsewhere = field( { ra: 66, dec: 16 }, 1.2/3840, 0, 0, 0, 0, 1 );
      elsewhere.dets.forEach( function( d ) { d.x = 3840 - d.x; d.y = ( d.y*7919 ) % 2560; } );   // no sky looks like this
      check( "scrambled field: no confident match", Solve.solve( idx, elsewhere.dets, 3840, 2560, {} ).length, 0 );
      check( "log10Chance: 10 of 10 at p 0.01 is -20", Math.abs( Solve.log10Chance( 10, 10, 0.01 ) + 20 ) < 1e-9, true );
      check( "log10Chance: 0 of n is certain", Solve.log10Chance( 0, 50, 0.1 ), 0 );
      var r12 = Solve.solve( idx, field( { ra: 66, dec: 16 }, 1.2/3840, 0.3, 0, 0.2, 0.2, 3 ).dets, 3840, 2560, {} )[0];
      check( "a solution's matches spread over the image", r12 && r12.spread >= Solve.MIN_SPREAD, true );
      var corner = field( { ra: 66, dec: 16 }, 1.2/3840, 0.3, 0, 0.2, 0, 3 );
      corner.dets = corner.dets.filter( function( d ) { return d.x < 1280 && d.y < 850; } );   // one ninth of the image
      check( "matches in one corner only: no solution", Solve.solve( idx, corner.dets, 3840, 2560, {} ).length, 0 );
      var ticks = 0;
      Solve.solve( idx, field( { ra: 66, dec: 16 }, 1.2/3840, 0.3, 0, 0.2, 0.2, 3 ).dets, 3840, 2560, { tick: function() { ++ticks; } } );
      check( "solve ticks while it works", ticks > 0, true );
   } )();
```

- [ ] **Step 2: Run and see it fail**

Run: `node ci/run-tests.js 2>&1 | tail -5`
Expected: FAIL on `Solve.solve is not a function`.

- [ ] **Step 3: Implement**

```js
Solve.IMAGE_QUAD_STARS = 40;    // brightest detections quads are made from
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

/* The image's quads, both parities: every pair of the brightest, with the two brightest inside its circle. */
Solve.imageQuads = function( dets, N )
{
   var s = Solve.brightest( dets, N ), out = [];
   for ( var i = 0; i < s.length; ++i )
      for ( var j = i + 1; j < s.length; ++j )
      {
         var mx = ( s[i].x + s[j].x )/2, my = ( s[i].y + s[j].y )/2;
         var r2 = ( ( s[i].x - s[j].x )*( s[i].x - s[j].x ) + ( s[i].y - s[j].y )*( s[i].y - s[j].y ) )/4, inside = [];
         for ( var k = 0; k < s.length && inside.length < 2; ++k )
            if ( k != i && k != j && ( s[k].x - mx )*( s[k].x - mx ) + ( s[k].y - my )*( s[k].y - my ) < r2 ) inside.push( k );
         if ( inside.length < 2 ) continue;
         var pts = [ s[i], s[j], s[inside[0]], s[inside[1]] ];
         [ 0, 1 ].forEach( function( parity )
         {
            var q = Solve.quadCode( pts.map( function( p ) { return [ p.x, parity ? -p.y : p.y ]; } ) );
            if ( q ) out.push( { pts: q.order.map( function( o ) { return [ pts[o].x, pts[o].y ]; } ), code: q.code, parity: parity, diameter: 2*Math.sqrt( r2 ) } );
         } );
      }
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
         hyps.push( { ref: ref, fit: fit, centre: centre, key: Math.round( Math.log( fit.scale )/0.02 ) + ":" + Solve.cellOf( centre.ra, centre.dec, 0.05 ) } );
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
      if ( tried[h.key] ) continue;
      tried[h.key] = true;
      // the fit is about the quad's reference star; re-centre it on the image centre's tangent point
      var img = [ [ 0, 0 ], [ W, 0 ], [ 0, H ], [ W, H ], [ W/2, H/2 ] ];
      var sky = img.map( function( p ) { var q = Solve.applySimilarity( h.fit, p[0], p[1] ), s = Solve.fromPlane( h.ref, q[0], q[1] ); return Solve.toPlane( h.centre, s.ra, s.dec ); } );
      var v = Solve.verify( index, Solve.fitSimilarity( img, sky, h.fit.parity ), h.centre, dets, W, H );
      if ( v.matches < Solve.MIN_MATCHES || v.spread < Solve.MIN_SPREAD || v.log10Chance > limit ) continue;
      var c = Solve.applySimilarity( v.fit, W/2, H/2 ), centre = Solve.fromPlane( h.centre, c[0], c[1] );
      if ( out.some( function( o ) { return Fly.separation( o, centre ) < 0.05; } ) ) continue;
      out.push( { ra: centre.ra, dec: centre.dec, scale: v.fit.scale*3600, rotation: Math.atan2( v.fit.b, v.fit.a )/Fly.RAD,
                  parity: v.fit.parity, matches: v.matches, of: v.of, spread: v.spread, log10Chance: v.log10Chance } );
   }
   return out.sort( function( a, b ) { return a.log10Chance - b.log10Chance; } );
};
```

- [ ] **Step 4: Run and see it pass**

Run: `node ci/run-tests.js 2>&1 | tail -5`
Expected: `every runnable assertion passed`. If a synthetic case fails, diagnose with superpowers:systematic-debugging. Print hypothesis counts and the best verify result for the failing case. Change the solver, not the test's tolerances. If a constant has to change, ledger the change with the measured reason.

- [ ] **Step 5: Measure node timing and commit**

Run: `node -e 'require("./ci/pjsr-shim.js")' ; time node ci/run-tests.js 2>&1 | tail -2` and record the total suite time in the commit message. The solver tests must add less than 60 s in total.

```bash
git add script/lib/Solve.js script/selftest.js
git commit -m "Solve: matching and verification (Solve.solve)"
```

---

### Task 6b: Real-field gate (ad hoc in slot 2, never committed)

**Why:** synthetic stars have a clean magnitude-to-flux relation. Real images have saturated cores, colour, blends and nebula knots. Before any whole-sky work, prove the algorithm on the three real images and tune the constants from measurements. (Codex round 1, findings 1, 7 and 8.)

**Files:** scratch only: `/tmp/agent-scratch/blind/gate.js`, dispatched to slot 2 (`/tmp/agent-scratch/pi2.sh`), with the `test` claim held.

- [ ] **Step 1: Local index per image.**
  - **Images:**
    - the Iris (RA 315.41, Dec +68.08);
    - the Elephant's Trunk (IC 1396A, about RA 324.2, Dec +57.5);
    - NGC 5907 (RA 228.97, Dec +56.33).

    Find each image's finished TIFF under the user's Astro folder, where the hints researcher found them: Downloads and `2026/…/TIFFs`. Read them only.
  - **Build:** for each one, query `Sky.querySources( centre, r, Solve.INDEX_G_MAX )` over `Solve.skyTiles( 2 )` tiles within 10° of the centre. Feed the results to a `Solve.Keeper`, then run `Solve.makeIndex`.
- [ ] **Step 2: Blind solve the real working copy.**
  - **Prepare:** open the image, make `Sky.workingCopy`, and on the copy only, delete its solution and keywords.
  - **Solve:** run `Sky.detections`, then `Solve.solve( localIndex, dets, W, H, {} )`.
  - **Record:**
    - the number of detections;
    - how many of the brightest 40 detections have a Gaia counterpart within 3 px under the known solution. For that, solve a separate copy with hints via `Sky.solveWithHints`, and project the index stars with `Sky.projector`.
    - the image quads that hit any index quad, and the hits that are true (the right sky position);
    - hypotheses, verifications, and the result versus the truth;
    - time.
- [ ] **Step 3: Tune until all three solve**, one change at a time, re-measuring each. The levers, in this order:
  1. `Solve.IMAGE_QUAD_STARS` (40 → 60 → 80);
  2. C/D choice: add the pairs (1st, 3rd) and (2nd, 3rd) brightest inside the circle, on both the image and the index side;
  3. `Solve.STARS_PER_CELL` (5 → 8);
  4. `Solve.QUADS_PER_CELL` (2 → 4);
  5. image stars ranked by `flux` versus by peak (`nmax`), since saturated stars all share a peak;
  6. `Solve.CODE_TOL` (0.01 → 0.015).

  Each constant that changes lands as a commit on Solve.js. The commit message carries the measurement that justified it, and the node suite stays green.
- [ ] **Step 4: Chance calibration.** Run each real image's detections against the *other two* images' local indexes, where there should be no match. Record the best `log10Chance` any hypothesis reaches. It must be at least 3 decades above the acceptance threshold; if it isn't, tighten `MAX_LOG10_CHANCE` or `MIN_SPREAD`.
- [ ] **Step 5: Report to the orchestrator.** Give the numbers for steps 2–4 and the final constants. **This is a gate:** Task 7's real build (its step 6) doesn't start until all three images solve here.

---

### Task 7: Building, caching and loading the index (Sky)

**Files:**
- Modify: `script/lib/Sky.js`, `script/selftest.js`

**Interfaces:**
- Consumes:
  - `Solve.Keeper`, `Solve.skyTiles`, `Solve.makeIndex`, `Solve.indexArrays`, `Solve.indexFromArrays`, `Solve.INDEX_VERSION`, `Solve.BANDS`, `Solve.INDEX_G_MAX`, `Solve.TILE_RADIUS`, `Solve.STARS_PER_CELL`, `Solve.QUADS_PER_CELL`;
  - `Sky.querySources`, `Sky.writeArrays`, `Sky.readArrays`.
- Produces:
  - `Sky.solverIndexDir() → string`. It's replaceable, so tests point it elsewhere.
  - `Sky.cancelled() → Error` with `loomCancel = true`.
  - `Sky.buildSolverIndex( progress, opts ) → index`. `progress = { stage(name, done, total), isCancelled() }`. `opts = { tiles, bands, gMax, dir }` is for tests; it defaults to the whole sky.
  - `Sky.loadSolverIndex( dir, config? ) → index | null`. `config` defaults to the whole-sky build's `Sky.solverConfig`, and a mismatch gives `null`.
  - `Sky.solverConfig( bands, gMax, tiles ) → string`. `Sky.catalogueTile( centre, radius, gMax ) → [sources] | null`. `Sky.clearSolution( window )` (Task 8).
  - `Sky.solverIndex( progress ) → index`: loaded or built, and memoised in `Sky._solverIndex`.
  - Files in `dir`:
    - `index-v<INDEX_VERSION>.json` (the header plus `origin`, `gMax` and `built`);
    - `index-v<INDEX_VERSION>.bin`;
    - while building, `build-v<INDEX_VERSION>.part.json` and `build-v<INDEX_VERSION>.part.bin`.

- [ ] **Step 1: Write the failing tests** (PixInsight only: add to `runSolveTests`, inside `if ( IN_PIXINSIGHT )`)

```js
   /* ---- building and caching the index (PixInsight: files) --------------- */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var dir = synthDir( "solver-index" ), savedQuery = Sky.querySources, savedDir = Sky.solverIndexDir;
      var patch = synthSky( 42, 30000, 60, 10, 12, 12 ), calls = 0;
      var tiles = Solve.skyTiles( 2 ).filter( function( t ) { return t.ra > 58 && t.ra < 74 && t.dec > 8 && t.dec < 24; } );
      var bands = Solve.BANDS.slice( 0, 4 );
      Sky.querySources = function( centre, radius, gMax )
      {
         ++calls;
         var s = patch.filter( function( t ) { return t.G <= gMax && Fly.separation( t, centre ) <= radius; } );
         s.origin = "test";
         return s;
      };
      Sky.solverIndexDir = function() { return dir; };
      Sky._solverIndex = null;
      try
      {
         File.findFiles && null;
         var quiet = { stage: function() {}, isCancelled: function() { return false; } };
         var idx = Sky.buildSolverIndex( quiet, { tiles: tiles, bands: bands } );
         check( "buildSolverIndex queries every tile", calls, tiles.length );
         check( "buildSolverIndex writes the index", File.exists( dir + "/index-v" + Solve.INDEX_VERSION + ".json" ), true );
         check( "buildSolverIndex leaves no part files", File.exists( dir + "/build-v" + Solve.INDEX_VERSION + ".part.json" ), false );
         var testConfig = Sky.solverConfig( bands, Solve.INDEX_G_MAX, tiles );
         var loaded = Sky.loadSolverIndex( dir, testConfig );
         check( "loadSolverIndex reads what was built", loaded && loaded.quads.length, idx.quads.length );
         check( "the index records its origin", JSON.parse( File.readTextFile( dir + "/index-v" + Solve.INDEX_VERSION + ".json" ) ).origin, "test" );
         var savedM = Solve.STARS_PER_CELL;
         Solve.STARS_PER_CELL = savedM + 1;
         check( "an index built with other settings is not loaded", Sky.loadSolverIndex( dir, Sky.solverConfig( bands, Solve.INDEX_G_MAX, tiles ) ), null );
         Solve.STARS_PER_CELL = savedM;
         check( "the whole-sky configuration does not load a test patch's index", Sky.loadSolverIndex( dir ), null );
         Solve.STARS_PER_CELL = savedM + 1;
         Solve.STARS_PER_CELL = savedM;

         // cancelled half way, then resumed: the tiles already read are not read again
         FlyThroughTestRemove( dir );
         calls = 0;
         var n = 0, stopAt = Math.floor( tiles.length/2 );
         try { Sky.buildSolverIndex( { stage: function() {}, isCancelled: function() { return ++n > stopAt; } }, { tiles: tiles, bands: bands, checkpointEvery: 5 } ); }
         catch ( e ) { check( "a cancelled build throws a cancel", !!e.loomCancel, true ); }
         check( "a cancelled build leaves no index", Sky.loadSolverIndex( dir, testConfig ), null );
         var first = calls;
         calls = 0;
         Sky.buildSolverIndex( quiet, { tiles: tiles, bands: bands, checkpointEvery: 5 } );
         check( "cancelled build resumes (reads fewer tiles the second time)", calls < tiles.length && calls + first >= tiles.length, true );

         // no catalogue anywhere: fail fast and say what to do
         FlyThroughTestRemove( dir );
         calls = 0;
         Sky.querySources = function() { ++calls; return []; };
         var msg = "";
         try { Sky.buildSolverIndex( quiet, { tiles: tiles, bands: bands } ); } catch ( e ) { msg = String( e.message ); }
         check( "no catalogue: build fails fast with a clear message", calls <= 9 && /Process > Gaia/.test( msg ) && /internet/.test( msg ), true );

         // an online answer at the row limit is split; one still at the limit at a quarter degree fails the build
         var savedMax = Fly.GAIA_ONLINE_MAX_ROWS, sizes = [];
         Fly.GAIA_ONLINE_MAX_ROWS = 10;
         Sky.querySources = function( c, r ) { sizes.push( r ); var a = []; for ( var i = 0; i < ( r >= 2 ? 10 : 3 ); ++i ) a.push( { ra: c.ra, dec: c.dec + i*1e-3, G: 10 } ); a.origin = "online"; return a; };
         check( "a tile at the row limit is read again as four", Sky.catalogueTile( { ra: 66, dec: 16 }, 2, 13 ).length, 12 );
         Sky.querySources = function( c ) { var a = []; for ( var i = 0; i < 10; ++i ) a.push( { ra: c.ra, dec: c.dec, G: 10 } ); a.origin = "online"; return a; };
         msg = "";
         try { Sky.catalogueTile( { ra: 66, dec: 16 }, 2, 13 ); } catch ( e ) { msg = String( e.message ); }
         check( "a patch at the row limit even when small fails the build", /Process > Gaia/.test( msg ), true );
         Fly.GAIA_ONLINE_MAX_ROWS = savedMax;
      }
      finally { Sky.querySources = savedQuery; Sky.solverIndexDir = savedDir; Sky._solverIndex = null; }
   } )();
```
Also add this helper next to `synthDir` in `selftest.js`:
```js
/* Empties a test folder (only ever one under synthDir). */
function FlyThroughTestRemove( dir )
{
   if ( !File.directoryExists( dir ) ) return;
   var f = new FileFind;
   if ( f.begin( dir + "/*" ) ) do { if ( !f.isDirectory ) File.remove( dir + "/" + f.name ); } while ( f.next() );
}
```
Delete the stray line `File.findFiles && null;` if you copied it: it does nothing.

- [ ] **Step 2: Run the PixInsight suite and see it fail**

Post `CLAIM test` on the board. Run: `/tmp/agent-scratch/pi-suite.sh ~/PixInsight/scripts/Loom/script/selftest.js`
Expected: failures on `Sky.buildSolverIndex is not a function`. Post `RELEASE test`.

- [ ] **Step 3: Implement** (in `script/lib/Sky.js`, after `Sky.requireSources`)

```js
/* ------------------------------------------------------------------------
 * Loom's blind solver: its whole-sky quad index, built once from Gaia
 * (Sky.querySources: the local database, else online) and kept per user.
 * ------------------------------------------------------------------------ */

Sky.solverIndexDir = function() { return File.homeDirectory + "/PixInsight/Loom/solver"; };

Sky.cancelled = function() { var e = new Error( "Cancelled." ); e.loomCancel = true; return e; };

Sky.indexName = function( dir, part ) { return dir + "/" + ( part ? "build-v" : "index-v" ) + Solve.INDEX_VERSION + ( part ? ".part" : "" ); };

/*
 * Streams the catalogue tile by tile into a Solve.Keeper (brightest stars
 * per cell), checkpointing every few tiles so a cancelled or failed build
 * resumes; then makes the quads and writes the index. Throws
 * Sky.cancelled() when cancelled, keeping the checkpoint.
 */
Sky.buildSolverIndex = function( progress, opts )
{
   opts = opts || {};
   var dir = opts.dir || Sky.solverIndexDir(), tiles = opts.tiles || Solve.skyTiles( Solve.TILE_RADIUS );
   var bands = opts.bands || Solve.BANDS, gMax = opts.gMax || Solve.INDEX_G_MAX, every = opts.checkpointEvery || 50;
   var sizes = bands.map( function( b ) { return b.lo; } ), part = Sky.indexName( dir, true );
   var config = Sky.solverConfig( bands, gMax, tiles );
   if ( !File.directoryExists( dir ) ) File.createDirectory( dir, true );
   var keep = new Solve.Keeper( sizes, Solve.STARS_PER_CELL ), next = 0, origin = null, failed = 0;
   if ( File.exists( part + ".json" ) )
   {
      var st = JSON.parse( File.readTextFile( part + ".json" ) );
      if ( st.config == config )
      {
         keep = Solve.Keeper.fromArrays( sizes, Solve.STARS_PER_CELL, Sky.readArrays( part + ".bin", [ st.length ] )[0] );
         next = st.next; origin = st.origin;
      }
   }
   function checkpoint( k )
   {
      var a = keep.toArrays();
      Sky.writeArrays( part + ".bin", [ a ] );
      File.writeTextFile( part + ".json", JSON.stringify( { config: config, next: k, origin: origin, length: a.length } ) );
   }
   for ( var k = next; k < tiles.length; ++k )
   {
      if ( progress.isCancelled && progress.isCancelled() ) { checkpoint( k ); throw Sky.cancelled(); }
      progress.stage( "Building the star index for blind solving (once)", k, tiles.length );
      var s = Sky.catalogueTile( tiles[k], Solve.TILE_RADIUS*1.05, gMax );
      if ( s == null )
      {
         // no answer at all (not an empty sky): nothing read yet means no catalogue anywhere
         if ( origin == null && ++failed >= 3 )
            throw new Error( "Blind solving needs a star catalogue: configure a Gaia DR3 database in Process > Gaia, or connect to the internet." );
         if ( origin != null ) { checkpoint( k ); throw new Error( "The star catalogue stopped answering while building the blind-solve index (tile " + ( k + 1 ) + " of " + tiles.length + "). It resumes where it stopped next time." ); }
         --k;   // retry the same tile (up to 3 failures in all before anything was read)
         continue;
      }
      if ( origin == null ) origin = s.origin;
      s.forEach( function( t ) { keep.add( { ra: t.ra, dec: t.dec, G: t.G } ); } );
      if ( ( k + 1 ) % every == 0 ) checkpoint( k + 1 );
   }
   progress.stage( "Building the star index: making quads", 0, 0 );
   var index = Solve.makeIndex( keep.stars(), bands, Solve.QUADS_PER_CELL ), io = Solve.indexArrays( index );
   var name = Sky.indexName( dir, false );
   Sky.writeArrays( name + ".bin", io.arrays );
   // the header last: an index without one is never loaded
   File.writeTextFile( name + ".json", JSON.stringify( Object.assign( io.header, { config: config, origin: origin, gMax: gMax, built: ( new Date ).toISOString() } ) ) );
   [ part + ".json", part + ".bin" ].forEach( function( f ) { try { if ( File.exists( f ) ) File.remove( f ); } catch ( e ) {} } );
   return index;
};

/* What an index is built from: a checkpoint or index made otherwise is not this one. */
Sky.solverConfig = function( bands, gMax, tiles )
{
   return JSON.stringify( { v: Solve.INDEX_VERSION, bands: bands.map( function( b ) { return +b.lo.toFixed( 6 ); } ), gMax: gMax,
                            M: Solve.STARS_PER_CELL, Q: Solve.QUADS_PER_CELL, tileRadius: Solve.TILE_RADIUS,
                            tiles: Fly.hashKey( tiles.map( function( t ) { return t.ra.toFixed( 4 ) + "," + t.dec.toFixed( 4 ); } ).join( ";" ) ) } );
};

/*
 * One tile of the catalogue, or null when nothing answered. Sky.querySources
 * marks a real answer with its origin; an answer without one is a failure
 * (offline, refused), not an empty sky. An online answer cut at the row
 * limit is read again as four smaller tiles.
 */
Sky.catalogueTile = function( centre, radius, gMax )
{
   var s = null;
   for ( var attempt = 0; attempt < 3 && !( s && s.origin ); ++attempt ) s = Sky.querySources( centre, radius, gMax );
   if ( !s || !s.origin ) return null;
   if ( s.origin == "online" && s.length >= Fly.GAIA_ONLINE_MAX_ROWS )
   {
      // still cut at a quarter degree: the answer cannot be made complete, and an incomplete index would miss stars silently
      if ( radius <= 0.25 ) throw new Error( "Gaia online returned more stars than it sends in one answer, even for a small patch at RA " + centre.ra.toFixed( 2 ) + ", Dec " + centre.dec.toFixed( 2 ) + ": configure a Gaia DR3 database in Process > Gaia." );
      var out = [], r = radius/2, d = radius/2;
      [ [ -1, -1 ], [ -1, 1 ], [ 1, -1 ], [ 1, 1 ] ].forEach( function( q )
      {
         var c = Solve.fromPlane( centre, q[0]*d, q[1]*d ), part = Sky.catalogueTile( c, r*1.5, gMax );
         if ( part == null ) out = null; else if ( out ) out = out.concat( part );
      } );
      if ( out ) out.origin = "online";
      return out;
   }
   return s;
};

/* The cached index, or null (missing, built otherwise, or unreadable). `config` defaults to the whole-sky build's. */
Sky.loadSolverIndex = function( dir, config )
{
   config = config || Sky.solverConfig( Solve.BANDS, Solve.INDEX_G_MAX, Solve.skyTiles( Solve.TILE_RADIUS ) );
   var name = Sky.indexName( dir || Sky.solverIndexDir(), false );
   try
   {
      if ( !File.exists( name + ".json" ) || !File.exists( name + ".bin" ) ) return null;
      var header = JSON.parse( File.readTextFile( name + ".json" ) );
      if ( header.version != Solve.INDEX_VERSION ) return null;
      // made with other settings (bands, depth, stars or quads per cell, tiles): not this index
      if ( header.config != config ) return null;
      return Solve.indexFromArrays( header, Sky.readArrays( name + ".bin", header.lengths ) );
   }
   catch ( e ) { Util.warn( "fly", "solver index: " + e ); return null; }
};

Sky._solverIndex = null;
Sky.solverIndex = function( progress )
{
   if ( !Sky._solverIndex )
   {
      progress.stage( "Loading the star index for blind solving", 0, 0 );
      Sky._solverIndex = Sky.loadSolverIndex( Sky.solverIndexDir() ) || Sky.buildSolverIndex( progress );
   }
   return Sky._solverIndex;
};
```
`Sky.buildSolverIndex` must also let the dialog repaint. In the tile loop, call `processEvents()` once per tile, guarded as `if ( typeof processEvents == "function" ) processEvents();`, and keep the cancel check right after it.

- [ ] **Step 4: Run the PixInsight suite and see it pass**

`CLAIM test` → `/tmp/agent-scratch/pi-suite.sh ~/PixInsight/scripts/Loom/script/selftest.js` → `RELEASE test`.
Expected: `PASS n run, 0 failed`. The node suite still passes: these tests sit behind `IN_PIXINSIGHT`.

- [ ] **Step 5: Commit**

```bash
git add script/lib/Sky.js script/selftest.js
git commit -m "Sky: build, cache and load the blind solver's index"
```

- [ ] **Step 6: Measure the real build (ad hoc, never committed)**

This step is owned by one agent only; post `CLAIM index-build` first.
- **Run:** in slot-2 PixInsight, run a scratch script under `/tmp/agent-scratch/` that includes the libs, sets `Sky.solverIndexDir` to `/tmp/agent-scratch/solver-index-real`, and calls `Sky.buildSolverIndex` with a progress object that logs every 100 tiles. It uses the local Gaia DR3/SP database.
- **Record** on the board and in the ledger:
  - tiles per minute;
  - total minutes;
  - stars kept;
  - quads per band;
  - `.bin` size in MB;
  - load time for `Sky.loadSolverIndex`.
- **Acceptance:** total build ≤ 60 min, index ≤ 200 MB, load ≤ 15 s. If one is missed, stop and post the numbers to the orchestrator. Don't change band or cell constants unilaterally.
- **Keep** this index at `/tmp/agent-scratch/solver-index-real` for Task 8's real-image checks. Don't copy it into `~/PixInsight/Loom/solver`.

---

### Task 8: `Sky.solveBlind` and the hand-off to ImageSolver

**Files:**
- Modify: `script/lib/Sky.js`, `script/selftest.js`

**Interfaces:**
- Consumes: `Sky.solverIndex`, `Solve.solve`, `Sky.detections`, `Sky.solveWithHints`.
- Produces:
  - `Solve.hintsFrom( result ) → { ra, dec, focal: 1000, pixel }`, with `pixel = scale·1000/206.265`. It goes in Solve.js, pure.
  - `Sky.solveBlind( window, progress ) → { hints, solvedPixel, result }`. It throws a plain `Error` when there's no solution, and `Sky.cancelled()` when cancelled.

- [ ] **Step 1: Write the failing tests**

Node (in `runSolveTests`):
```js
   check( "hintsFrom: focal 1000 and the pixel that gives the scale",
          ( function() { var h = Solve.hintsFrom( { ra: 1, dec: 2, scale: 0.966 } ); return h.focal == 1000 && Math.abs( 206.265*h.pixel/h.focal - 0.966 ) < 1e-12 && h.ra == 1 && h.dec == 2; } )(), true );
```
PixInsight (`if ( IN_PIXINSIGHT )` in `runSolveTests`): render a synthetic star image and solve it blind, with ImageSolver stubbed.
```js
   if ( IN_PIXINSIGHT ) ( function()
   {
      var bands = Solve.BANDS.slice( 0, 6 ), keep = new Solve.Keeper( bands.map( function( b ) { return b.lo; } ), 5 );
      var sky = synthSky( 77, 60000, 60, 10, 12, 12 );
      sky.forEach( function( s ) { keep.add( s ); } );
      var savedIndex = Sky._solverIndex, savedSolve = Sky.solveWithHints, got = null;
      Sky._solverIndex = Solve.makeIndex( keep.stars(), bands, 2 );
      var savedField = Sky.field, savedScale = Sky.solvedScale;
      Sky.solveWithHints = function( w, hints ) { got = hints; return hints.pixel; };
      // "ImageSolver" here agrees with the hints it was given
      Sky.field = function() { return { centre: { ra: got.ra, dec: got.dec }, radiusDeg: 1 }; };
      Sky.solvedScale = function() { return 206.265*got.pixel/got.focal; };
      var W = 1920, H = 1280, scale = 1.5/W, centre = { ra: 66, dec: 16 };
      var win = new ImageWindow( W, H, 1, 32, true, false, "solve_synth" );
      try
      {
         var img = win.mainView.image, f = { a: scale, b: 0, tx: 0, ty: 0, parity: 0 };
         var c0 = Solve.applySimilarity( f, W/2, H/2 ); f.tx = -c0[0]; f.ty = -c0[1];
         // one buffer, one setSamples: per-pixel setSample over thousands of stars is slow in PJSR
         var buf = new Float32Array( W*H );
         for ( var i = 0; i < buf.length; ++i ) buf[i] = 0.05;
         sky.forEach( function( t )
         {
            var p = Solve.toPlane( centre, t.ra, t.dec ); if ( !p ) return;
            var xy = Solve.invertSimilarity( f, p[0], p[1] ), amp = Math.min( 0.9, 50*Math.pow( 10, -0.4*t.G ) );
            for ( var y = Math.floor( xy[1] ) - 4; y <= Math.floor( xy[1] ) + 4; ++y )
               for ( var x = Math.floor( xy[0] ) - 4; x <= Math.floor( xy[0] ) + 4; ++x )
                  if ( x >= 0 && y >= 0 && x < W && y < H )
                     buf[y*W + x] = Math.min( 1, buf[y*W + x] + amp*Math.exp( -( ( x - xy[0] )*( x - xy[0] ) + ( y - xy[1] )*( y - xy[1] ) )/( 2*1.5*1.5 ) ) );
         } );
         win.mainView.beginProcess( UndoFlag_NoSwapFile );
         img.setSamples( buf );
         win.mainView.endProcess();
         check( "the synthetic image has stars to solve from", Sky.detections( img ).length >= 100, true );
         var quiet = { stage: function() {}, isCancelled: function() { return false; } };
         var r = Sky.solveBlind( win, quiet );
         check( "solveBlind hands ImageSolver the centre", got && Fly.separation( got, centre ) < 0.02, true );
         check( "solveBlind hands ImageSolver the scale", got && Math.abs( 206.265*got.pixel/got.focal - scale*3600 )/( scale*3600 ) < 0.01, true );
         Sky.solveWithHints = function() { throw new Error( "not confirmed" ); };
         var msg = "";
         try { Sky.solveBlind( win, quiet ); } catch ( e ) { msg = String( e.message ); }
         check( "an unconfirmed blind match is not used", /could not confirm/.test( msg ), true );
         Sky.solveWithHints = function( w, hints ) { got = hints; return hints.pixel; };
         Sky.field = function() { return { centre: { ra: got.ra + 3, dec: got.dec }, radiusDeg: 1 }; };   // "solved" elsewhere
         msg = "";
         try { Sky.solveBlind( win, quiet ); } catch ( e ) { msg = String( e.message ); }
         check( "a confirmation somewhere else is not used", /not where the blind match/.test( msg ), true );
      }
      finally { win.forceClose(); Sky._solverIndex = savedIndex; Sky.solveWithHints = savedSolve; Sky.field = savedField; Sky.solvedScale = savedScale; }
   } )();
```

- [ ] **Step 2: Run both suites and see them fail**

Run: `node ci/run-tests.js 2>&1 | tail -5`
Expected: FAIL on `Solve.hintsFrom is not a function`.

Then run the PixInsight suite, with CLAIM and RELEASE around it.
Expected: FAIL on `Sky.solveBlind is not a function`.

- [ ] **Step 3: Implement**

First, in the existing `Sky.solveWithHints`, a cancel must end the loop, not count as a failed scale. Change its catch to:
```js
      catch ( e ) { if ( e && e.loomCancel ) throw e; reasons.push( String( e.message || e ) ); }
```
Add the test `solveWithHints: a cancel is not retried at other scales`: stub `Sky.solveOnce` to throw `Sky.cancelled()` and count its calls; expect 1, and expect `loomCancel` on the error. Write the test first and watch it fail (3 calls).

Solve.js:
```js
/* A blind solution as Sky.solveWithHints hints: any focal/pixel pair with the right ratio (focal fixed at 1000 mm). */
Solve.hintsFrom = function( r )
{
   return { ra: r.ra, dec: r.dec, focal: 1000, pixel: r.scale*1000/206.265 };
};
```
Sky.js:
```js
/*
 * Solves an image with no hints at all: its stars against the whole-sky
 * index (Solve.solve), then each candidate handed to ImageSolver, which
 * alone decides -- a blind match without its confirmation is never used
 * (a blind solver once reported the Iris at RA 172, Dec +27).
 */
Sky.solveBlind = function( window, progress )
{
   var tick = function()
   {
      if ( typeof processEvents == "function" ) processEvents();
      if ( progress.isCancelled && progress.isCancelled() ) throw Sky.cancelled();
   };
   progress.stage( "Blind solving: finding the image's stars", 0, 0 );
   var img = window.mainView.image, dets = Sky.detections( img );
   if ( dets.length < Solve.MIN_MATCHES )
      throw new Error( "Blind solving found only " + dets.length + " stars in the image: too few to solve from. Fill in Object (or RA/Dec)." );
   var index = Sky.solverIndex( progress );
   tick();
   progress.stage( "Blind solving: matching " + dets.length + " stars against the sky", 0, 0 );
   var results = Solve.solve( index, dets, img.width, img.height, { tick: tick } ), reasons = [];
   if ( results.length == 0 )
      throw new Error( "Blind solving found no match in the sky for this image's stars. Fill in Object (or RA/Dec), focal length and pixel size." );
   for ( var k = 0; k < results.length; ++k )
   {
      var hints = Solve.hintsFrom( results[k] );
      Util.log( "fly", "blind: RA " + results[k].ra.toFixed( 4 ) + " Dec " + results[k].dec.toFixed( 4 ) + ", " + results[k].scale.toFixed( 3 ) +
                "″/px, " + results[k].matches + "/" + results[k].of + " stars, chance 1e" + results[k].log10Chance.toFixed( 0 ) );
      try
      {
         var solvedPixel = Sky.solveWithHints( window, hints, progress.stage );
         // ImageSolver can settle on another field at the same scale: the solution must be where the blind match said
         var got = Sky.field( window ).centre, diag = results[k].scale*Math.hypot( img.width, img.height )/3600;
         if ( Fly.separation( got, results[k] ) > 0.1*diag || Math.abs( Sky.solvedScale( window )/results[k].scale - 1 ) > 0.03 )
            throw new Error( "ImageSolver's solution (RA " + got.ra.toFixed( 3 ) + ", Dec " + got.dec.toFixed( 3 ) + ") is not where the blind match put the image" );
         return { hints: hints, solvedPixel: solvedPixel, result: results[k] };
      }
      catch ( e )
      {
         // a rejected or failed candidate must leave no solution behind: Sky.projector would believe it next time
         Sky.clearSolution( window );
         if ( e && e.loomCancel ) throw e;
         reasons.push( String( e.message || e ) );
      }
   }
   throw new Error( "Blind solving found " + results.length + " candidate position(s), but ImageSolver could not confirm any: " + reasons[0] );
};
```
The test regex `/could not confirm/` matches this message.

Add `Sky.clearSolution`, next to `Sky.writeTanKeywords`:
```js
/* Removes an astrometric solution and every WCS keyword from a window (a rejected blind candidate's). */
Sky.WCS_KEYWORD = /^(CTYPE[12]|CRVAL[12]|CRPIX[12]|CD[12]_[12]|CDELT[12]|CROTA[12]|PC[12]_[12]|PV[12]_\d+|LONPOLE|LATPOLE|EQUINOX|RADESYS|A_\w+|B_\w+|AP_\w+|BP_\w+)$/;
Sky.clearSolution = function( window )
{
   try { window.clearAstrometricSolution(); } catch ( e ) {}
   window.keywords = window.keywords.filter( function( k ) { return !Sky.WCS_KEYWORD.test( k.name.trim() ); } );
};
```
Before relying on it, probe `ImageWindow.prototype.clearAstrometricSolution` in slot 2 with a one-line script. If it doesn't exist, find the PJSR way to drop a solution (search `/Applications/PixInsight/src/scripts` and `include/pjsr`), use that, and ledger a ruling. The PixInsight test adds `a rejected candidate leaves the window unsolved`: after the "somewhere else" case, `Sky.projector( win )` is `null`. Its stubs don't write a real solution, so the test also writes TAN keywords with `Sky.writeTanKeywords` and calls `win.regenerateAstrometricSolution()` inside the stubbed `solveWithHints`, which gives the clean-up something to remove.

- [ ] **Step 4: Run both suites and see them pass**

Expected: node `every runnable assertion passed`, and PixInsight `PASS n run, 0 failed`.

- [ ] **Step 5: Commit**

```bash
git add script/lib/Solve.js script/lib/Sky.js script/selftest.js
git commit -m "Sky: blind solve with ImageSolver confirmation"
```

- [ ] **Step 6: Real-image checks (ad hoc, never committed; they need Task 7 step 6's index)**

This is done in slot 2 with `Sky.solverIndexDir` pointed at `/tmp/agent-scratch/solver-index-real`.
- **Images:**
  - the Iris (`NGC7023` TIFF under the user's Astro folder);
  - the Elephant's Trunk;
  - NGC 5907.

  For each:
  - open it;
  - make `Sky.workingCopy`;
  - **delete its astrometric solution and its RA/DEC/OBJCT keywords on the copy** (never on the original file; never save);
  - run `Sky.solveBlind`.
- **Record:** centre versus the known solution (the Iris: RA 315.41, Dec +68.08, 0.483″/px at full size), time, matches, and whether ImageSolver confirmed.
- **Acceptance:** all three solve, each under 60 s after the index is loaded. If one fails, post the numbers and the hypothesis count to the orchestrator before changing constants.

---

### Task 9: Hints from the file (keywords and folder names)

**Files:**
- Modify: `script/lib/Fly.js`, `script/FlyThrough.js` (`fillFromImage`, `objectFromName`), `script/selftest.js` (in `runFlyTestsClean`, next to the `objectFromFileName` tests)

**Interfaces:**
- Consumes: `Fly.parseAngle( text, isRA )`, `Fly.findObject`, `Fly.FILE_NAME_SCORE`.
- Produces:
  - `Fly.headerCentre( keywords[{name, value}] ) → {ra, dec} | null`. It tries `CRVAL1`/`CRVAL2` only with `CTYPE1` starting `RA`, then `RA`/`DEC` in degrees, then `OBJCTRA`/`OBJCTDEC` in sexagesimal.
  - `Fly.objectFromPath( path, entries ) → hit | null`: the file name first, then the parent folder names, innermost first, at most 3 levels up.
  - `Fly.findObject` resolves a letter-suffixed NGC/IC id ("IC 1396A", "IC1396A") to the base entry when no exact id exists.

- [ ] **Step 1: Write the failing tests** (in `runFlyTestsClean`)

```js
   ( function()
   {
      function kw( pairs ) { return pairs.map( function( p ) { return { name: p[0], value: p[1] }; } ); }
      check( "headerCentre: RA/DEC in degrees", JSON.stringify( Fly.headerCentre( kw( [ [ "RA", "315.41" ], [ "DEC", "68.08" ] ] ) ) ), JSON.stringify( { ra: 315.41, dec: 68.08 } ) );
      var o = Fly.headerCentre( kw( [ [ "OBJCTRA", "'21 01 38.0'" ], [ "OBJCTDEC", "'+68 04 48'" ] ] ) );
      check( "headerCentre: OBJCTRA/OBJCTDEC sexagesimal", o && Math.abs( o.ra - 315.408 ) < 0.01 && Math.abs( o.dec - 68.08 ) < 0.01, true );
      check( "headerCentre: CRVAL with an RA axis", JSON.stringify( Fly.headerCentre( kw( [ [ "CTYPE1", "'RA---TAN'" ], [ "CRVAL1", "10.5" ], [ "CRVAL2", "-5" ] ] ) ) ), JSON.stringify( { ra: 10.5, dec: -5 } ) );
      check( "headerCentre: CRVAL without an RA axis is ignored", Fly.headerCentre( kw( [ [ "CRVAL1", "10.5" ], [ "CRVAL2", "-5" ] ] ) ), null );
      check( "headerCentre: nothing", Fly.headerCentre( kw( [ [ "EXPTIME", "300" ] ] ) ), null );
      check( "headerCentre: out of range is ignored", Fly.headerCentre( kw( [ [ "RA", "400" ], [ "DEC", "10" ] ] ) ), null );
      var entries = [ { id: "IC1396", ra: 324.7, dec: 57.5, diameter: 170, name: "Elephant's Trunk Nebula" } ];
      var hit = Fly.objectFromPath( "/Astro/IC 1396/2026-09-01/final_stretched.tif", entries );
      check( "objectFromPath: from the folder name", hit && hit.id, "IC1396" );
      check( "objectFromPath: the file name wins", Fly.objectFromPath( "/Astro/nothing/IC1396.tif", entries ).id, "IC1396" );
      check( "objectFromPath: nothing", Fly.objectFromPath( "/Astro/2026-09-01/final.tif", entries ), null );
      check( "objectFromPath: at most 3 folders up", Fly.objectFromPath( "/IC 1396/a/b/c/final.tif", entries ), null );
      check( "findObject: IC 1396A resolves to IC1396", ( Fly.findObject( "IC 1396A", entries )[0] || {} ).id, "IC1396" );
      check( "findObject: IC1396A resolves to IC1396", ( Fly.findObject( "IC1396A", entries )[0] || {} ).id, "IC1396" );
   } )();
```

- [ ] **Step 2: Run and see it fail**

Run: `node ci/run-tests.js 2>&1 | tail -5`
Expected: FAIL on `Fly.headerCentre is not a function`.

- [ ] **Step 3: Implement** (in `Fly.js`, after `Fly.objectFromFileName`)

```js
/* The image's centre from its FITS keywords (RA/DEC degrees, OBJCTRA/OBJCTDEC sexagesimal, CRVAL on an RA axis), or null. */
Fly.headerCentre = function( keywords )
{
   var kw = {};
   ( keywords || [] ).forEach( function( k ) { kw[String( k.name ).trim().toUpperCase()] = String( k.value ).replace( /^'|'$/g, "" ).trim(); } );
   function ok( ra, dec ) { return ra != null && dec != null && isFinite( ra ) && isFinite( dec ) && ra >= 0 && ra < 360 && Math.abs( dec ) <= 90 ? { ra: ra, dec: dec } : null; }
   var r = null;
   if ( /^RA/.test( kw.CTYPE1 || "" ) ) r = ok( parseFloat( kw.CRVAL1 ), parseFloat( kw.CRVAL2 ) );
   if ( !r && kw.RA != null && kw.DEC != null ) r = ok( parseFloat( kw.RA ), parseFloat( kw.DEC ) );
   if ( !r && kw.OBJCTRA != null && kw.OBJCTDEC != null ) r = ok( Fly.parseAngle( kw.OBJCTRA, true ), Fly.parseAngle( kw.OBJCTDEC, false ) );
   return r;
};

/* An object named by the file (Fly.objectFromFileName) or, failing that, by a folder, innermost first. */
Fly.objectFromPath = function( path, entries )
{
   var parts = String( path || "" ).split( /[\/\\]/ ).filter( function( p ) { return p.length; } );
   for ( var i = parts.length - 1; i >= Math.max( 0, parts.length - 4 ); --i )
   {
      var hit = Fly.objectFromFileName( i == parts.length - 1 ? parts[i] : parts[i] + ".x", entries );
      if ( hit ) return hit;
   }
   return null;
};
```
In `Fly.findObject`, right after `if ( byId[compact] ) hit( byId[compact], 1 );`, add the suffix rule (an ASIAIR target such as "IC 1396A" names a part of a catalogued object):
```js
   var sub = /^((?:NGC|IC)\d+)[A-Z]$/.exec( compact );
   if ( !byId[compact] && sub && byId[sub[1]] ) hit( byId[sub[1]], 0.97 );
```

Check that `Fly.parseAngle( "21 01 38.0", true )` returns degrees (315.408). If it returns hours, or needs `h m s`, adapt the call in `headerCentre`, not the test.

In `FlyThrough.js`:
- in `objectFromName()`, replace `Fly.objectFromFileName(` with `Fly.objectFromPath(`, and change the note to `" (from the file or folder name)"`;
- in `fillFromImage()`, the name guess must not overwrite a header centre. Change `if ( this.needsHints && !this.objectEdit.text.trim() ) this.objectFromName();` to `if ( this.needsHints && !this.objectEdit.text.trim() && !this.raEdit.text.trim() ) this.objectFromName();`. The dialog test adds an image whose `OBJCTRA`/`OBJCTDEC` point at the Iris and whose view id names `IC1396`: RA stays at the header's 315.4. (`recallObject` stays as it is: a user's own remembered hints win over both.)
- in `fillFromImage()`, **before** the `if ( this.needsHints ) this.recallObject();` line (the header's own pointing outranks a name guessed from the file or folder; remembered hints are applied after and still win), add:
```js
      // the header's pointing, when no name gave one (ASIAIR and NINA lights and WBPP masters carry it; exports never do)
      if ( this.needsHints && !this.raEdit.text.trim() )
      {
         var hc = FlyThrough.headerCentre( this.imageWindow );
         if ( hc ) { this.raEdit.text = hc.ra.toFixed( 4 ); this.decEdit.text = hc.dec.toFixed( 4 ); this.objectMatch.text = "Centre from the image's header"; }
      }
      // and the header's object name (OBJECT, Observation:Object:Name)
      if ( this.needsHints && !this.raEdit.text.trim() )
      {
         var name = FlyThrough.headerObject( this.imageWindow );
         if ( this.ngcIc === undefined ) this.ngcIc = Sky.readNgcIc();
         var oh = name ? Fly.findObject( name, this.ngcIc || [] )[0] : null;
         if ( oh ) { this.objectEdit.text = oh.name || oh.id; this.raEdit.text = oh.ra.toFixed( 4 ); this.decEdit.text = oh.dec.toFixed( 4 ); this.objectMatch.text = oh.id + " (from the image's header)"; }
      }
```

And add, in `FlyThrough.js` near `FlyThrough.identify`, the two readers. XISF properties come first; they need PixInsight, so they're tested there:
```js
/* An open image's centre: XISF Observation:Center:RA/Dec, else its FITS keywords (Fly.headerCentre); null when neither. */
FlyThrough.headerCentre = function( window )
{
   var v = window.mainView, ra = null, dec = null;
   try { ra = v.propertyValue( "Observation:Center:RA" ); dec = v.propertyValue( "Observation:Center:Dec" ); } catch ( e ) {}
   if ( typeof ra == "number" && typeof dec == "number" && ra >= 0 && ra < 360 && Math.abs( dec ) <= 90 ) return { ra: ra, dec: dec };
   return Fly.headerCentre( window.keywords.map( function( k ) { return { name: k.name, value: k.value }; } ) );
};

/* An open image's object name: XISF Observation:Object:Name, else the OBJECT keyword; "" when neither. */
FlyThrough.headerObject = function( window )
{
   var name = "";
   try { name = window.mainView.propertyValue( "Observation:Object:Name" ) || ""; } catch ( e ) {}
   if ( !name ) window.keywords.forEach( function( k ) { if ( k.name.trim().toUpperCase() == "OBJECT" ) name = String( k.value ).replace( /^'|'$/g, "" ).trim(); } );
   return String( name );
};
```
PixInsight tests (in `runFlyTestsClean`, `if ( IN_PIXINSIGHT )`). On a fresh 16×16 `ImageWindow`:
- set `Observation:Center:RA` = 315.41 and `Observation:Center:Dec` = 68.08 with `view.setPropertyValue`, and assert `FlyThrough.headerCentre` returns them;
- on another fresh one, set `keywords = [ new FITSKeyword( "OBJECT", "'IC 1396A'", "" ) ]` and assert `FlyThrough.headerObject` returns `"IC 1396A"`.

Close both windows in a `finally`.

- [ ] **Step 4: Run node and the PixInsight suite; both pass**

Expected: node `every runnable assertion passed`, and PixInsight `PASS … 0 failed`.

- [ ] **Step 5: Commit**

```bash
git add script/lib/Fly.js script/FlyThrough.js script/selftest.js
git commit -m "Fly-Through: solve hints from header keywords and folder names"
```

---

### Task 10: The dialog and `identify` use the blind solve

**Files:**
- Modify: `script/FlyThrough.js` (`identify` ~line 313; `autoPrepare` ~1062; `hintsMissing` ~1142; `hintsEdited` ~1155; `choices` ~1733; status text ~1063), `script/selftest.js`

**Interfaces:**
- Consumes: `Sky.solveBlind( window, progress )` (Task 8).
- Produces:
  - `choices.blind = true` when an unsolved image lacks complete hints.
  - `FlyThrough.identify( window, choices, progress )`:
    - with `choices.blind`, it solves blind;
    - with `choices.hints` that fail (a non-cancel error), it solves blind next;
    - if that fails too, the error carries both reasons.
  - `id.blind` is the blind result or `null`.

- [ ] **Step 1: Write the failing tests** (PixInsight only; in `runFlyTestsClean`, next to the existing `identify` tests)

```js
   if ( IN_PIXINSIGHT ) ( function()
   {
      var savedBlind = Sky.solveBlind, savedHints = Sky.solveWithHints, savedProj = Sky.projector, blindCalls = 0, win = new ImageWindow( 64, 64, 1, 32, true, false, "blind_id" );
      try
      {
         Sky.projector = function() { return null; };                       // never solved: identify stops at the solve
         Sky.solveBlind = function() { ++blindCalls; throw new Error( "blind: no match" ); };
         var msg = "";
         try { FlyThrough.identify( win, { blind: true }, { stage: function() {}, isCancelled: function() { return false; } } ); } catch ( e ) { msg = e.message; }
         check( "identify: no hints solves blind", blindCalls, 1 );
         Sky.solveWithHints = function() { throw new Error( "hints: failed" ); };
         blindCalls = 0; msg = "";
         try { FlyThrough.identify( win, { hints: { ra: 1, dec: 2, focal: 500, pixel: 3.76 } }, { stage: function() {}, isCancelled: function() { return false; } } ); } catch ( e ) { msg = e.message; }
         check( "identify: failed hints fall back to blind", blindCalls, 1 );
         check( "identify: both reasons are reported", /hints: failed/.test( msg ) && /blind: no match/.test( msg ), true );
         Sky.solveWithHints = function() { throw FlyThrough.cancel(); };
         blindCalls = 0;
         try { FlyThrough.identify( win, { hints: { ra: 1, dec: 2, focal: 500, pixel: 3.76 } }, { stage: function() {}, isCancelled: function() { return true; } } ); } catch ( e ) {}
         check( "identify: a cancel is not followed by a blind solve", blindCalls, 0 );
         check( "identify: neither hints nor blind still asks for a solve", FlyThrough.identify( win, {}, null ).needsSolve, true );
      }
      finally { Sky.solveBlind = savedBlind; Sky.solveWithHints = savedHints; Sky.projector = savedProj; win.forceClose(); }
   } )();
```
Also add a dialog test next to the existing `hintsMissing` tests (search selftest.js for `hintsMissing`). An unsolved image with empty RA/Dec gives `dialog.choices().blind === true`, and `dialog.hintsMissing() === false`.

- [ ] **Step 2: Run the PixInsight suite and see it fail**

CLAIM, run, RELEASE.
Expected: `identify: no hints solves blind` fails (0 calls), and the dialog test fails.

- [ ] **Step 3: Implement**

`identify` replaces the unsolved branch:
```js
   var blind = null;
   if ( Sky.projector( window ) == null )
   {
      if ( !choices.hints && !choices.blind )
         return { needsSolve: true };
      var blindProgress = { stage: stage, isCancelled: function() { return !!( progress && progress.isCancelled && progress.isCancelled() ); } };
      var hintError = null;
      if ( choices.hints )
      {
         stage( "Plate-solving: finding where in the sky the image points", 0, 0 );
         try { var solvedPixel = Sky.solveWithHints( window, choices.hints, stage ); }
         catch ( e ) { if ( FlyThrough.isCancel( e ) ) throw e; hintError = e; }
      }
      if ( !choices.hints || hintError )
      {
         if ( hintError ) stage( "The hints did not solve: solving blind", 0, 0 );
         try { blind = Sky.solveBlind( window, blindProgress ); solvedPixel = blind.solvedPixel; }
         catch ( e )
         {
            if ( FlyThrough.isCancel( e ) || !hintError ) throw e;
            throw new Error( String( hintError.message || hintError ) + "\n\nBlind solving: " + String( e.message || e ) );
         }
      }
   }
```
Keep the `var id = { … }` literal as it is, and add `blind: blind ? blind.result : null` to it.

Dialog:
- `hintsMissing()` returns `false` now. An unsolved image always proceeds: complete hints go through `solveWithHints`, anything else goes blind. Keep the method, because `hintsEdited` uses it, and have it return `false` with a comment saying so. Then delete the dead branch in `autoPrepare`, the one returning "No astrometric solution and nothing in the header to solve from…".
- `choices()`: when `this.needsHints` and the hints are incomplete, set `c.blind = true` instead of throwing:
```js
      if ( this.needsHints )
      {
         var h = { ra: Fly.parseAngle( this.raEdit.text, true ), dec: Fly.parseAngle( this.decEdit.text, false ),
                   focal: this.number( this.focalEdit ), pixel: this.number( this.pixelEdit ) };
         // complete hints solve directly; anything less is solved blind (Sky.solveBlind)
         if ( h.ra != null && h.dec != null && h.focal > 0 && h.pixel > 0 ) c.hints = h; else c.blind = true;
      }
```
- `analyseImage()`: blind hints must not be divided by `this.work.scale`. The existing `if ( choices.hints ) choices.hints.pixel /= this.work.scale;` already only touches `hints`, so leave it as it is.
- Blind results are **not** written into the hint fields or remembered with `rememberObject`: the focal and pixel fields may describe another rig, and the solved working copy is already cached by `saveWork`, which is what makes the next run fast.
- `targetLabel`: `FlyThrough.describeTarget( id )` gets the suffix `" · solved blind"` when `id.blind` is set.

- [ ] **Step 4: Run node and the PixInsight suite; both pass**

Expected: node `every runnable assertion passed`, and PixInsight `PASS … 0 failed`.

- [ ] **Step 5: Commit**

```bash
git add script/FlyThrough.js script/selftest.js
git commit -m "Fly-Through: solve blind when there are no hints or they fail"
```

- [ ] **Step 6: Visual check in slot 2 (ad hoc)**

- **Setup:** open a copy of the Iris with its solution and keywords removed. Use a temporary view that's never saved, and point `Sky.solverIndexDir` at the Task 7 real index.
- **Run:** Fly-Through on it.
- **Confirm:**
  - it starts by itself;
  - the progress bar shows the blind stages;
  - Cancel stops it without the "Do you want to abort" prompt;
  - running again solves;
  - the target label says "solved blind".
- **Evidence:** screenshots under `/tmp/agent-scratch/blind-ui/`.

---

### Task 11: Changelog and final whole-branch verification

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add the entry** (above `## [0.2.1]`)

```markdown
## [Unreleased]

### Loom Fly-Through

**Blind plate solving.** An image with no astrometric solution, no object name and no RA/Dec is solved by Loom's own solver:
- Its stars are matched against a whole-sky index of four-star patterns, and PixInsight's ImageSolver confirms the answer. No focal length or pixel size is needed, and drizzled images solve at their own scale.
- The index is built once from Gaia (the database set up in Process > Gaia, else Gaia DR3 online) and kept in `PixInsight/Loom/solver` in your home folder. The first build takes a while and shows its progress, and a cancelled build resumes where it stopped.
- Fields from about 0.45° to 25° across.
- When the hints given don't solve, the blind solver is tried next.

**Solve hints from the file.** The centre is read from the image's XISF properties or FITS keywords (RA/DEC, OBJCTRA/OBJCTDEC, or a WCS), and the object from its OBJECT keyword or from a folder name up to three levels up, not only from the file name. Letter-suffixed ids such as "IC 1396A" are recognised.
```

- [ ] **Step 2: Full verification**

Run:
- `node ci/run-tests.js 2>&1 | tail -3`;
- `LOOM_TEST_CORE_RELEASE=4 node ci/run-tests.js 2>&1 | tail -3`;
- the PixInsight suite in slot 2.

Expected: all green. Paste the three result lines into the ledger.

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "Changelog: blind plate solving"
```

---

## Swarm execution

The user asked for a swarm implementation with several agents working together. The orchestrator cuts the integration branch `feature/blind-solver` from `main`. Each agent works in its own worktree and branch (see the shared rules), and agents coordinate on the board. A branch is merged into `feature/blind-solver` only after its upstream work has been merged and the verifier has posted VERIFIED for it.

- **Job:** `loom-blind-solver`. Its goal: "Tasks 1–11 done, node suites and the PixInsight suite green, the Iris, the Elephant's Trunk and NGC 5907 blind-solve and are confirmed by ImageSolver".
- **Agents:**
  - **Core (Solve.js):** Tasks 1 → 2 → 3 → 4 → 5 → 6, in order, then Task 6b (the real-field gate: it needs slot 2 and the Sky helpers that already exist, nothing new from Sky). It owns `script/lib/Solve.js` and `runSolveTests`' pure part.
  - **Sky:** Tasks 7 and 8. It starts Task 7's Sky code once Task 5 is merged (it needs `Solve.makeIndex`/`indexArrays`), and Task 8 once Task 6 is merged. It owns `Sky.js`'s solver section and Task 7 step 6 (the real index build: `CLAIM index-build`), which **waits for Task 6b to pass**.
  - **Dialog:** Task 9 at once (independent: Fly.js and FlyThrough.js hint code). Then Task 10 once Task 8 is committed. It owns `FlyThrough.js`, `Fly.js` and Task 10 step 6.
  - **Verifier:** checks every `DONE:` claim independently: re-runs the named tests and reads the diff against this plan. It's read-only.
  - **Judge:** holds the goal and records the verdict.
- **Shared rules:**
  - **Every agent works in its own git worktree**, on its own branch off `feature/blind-solver`: `blind/core`, `blind/sky` and `blind/dialog`. Only the orchestrator merges into `feature/blind-solver`, in dependency order (core, then sky, then dialog), after the verifier's VERIFIED. That way nobody commits another agent's half-done `selftest.js` edits. An agent that needs an upstream task (Sky needs Task 5 and then Task 6) asks the orchestrator to merge it, then rebases its branch on `feature/blind-solver`.
  - Interfaces are frozen as this plan's Interfaces blocks state them. A change is posted on the board `--to` the consumers before it's committed.
  - Slot 2 is also used by the SyQon Studio agent. Hold the `mkdir /tmp/agent-scratch/pi-slot2.lock` lock while running PixInsight, in addition to the board's `CLAIM test`.
  - The PixInsight suite needs `CLAIM test`/`RELEASE test` on the board, and runs in slot 2 only. Never close slot 2.
  - Task 11 is done by the orchestrator after all the others.

---

## Self-review

- **Spec coverage:**

  | Spec item | Where |
  |---|---|
  | Order Loom tries (1–4) | Existing solution first; Task 9 (header, folder); Task 10 (hints, then blind) |
  | Quads, codes and canonical form | Task 1 |
  | Bands, cells, M per cell | Task 3 |
  | Lookup | Task 4 |
  | Stored arrays | Task 5 |
  | Built from local/online Gaia | Task 7, via `Sky.querySources` |
  | Cached per user, keyed by version | Task 7 |
  | Size | Task 7 step 6 |
  | Matching steps 1–7 | Task 6 (1–6), Task 8 (7) |
  | Performance | Task 6 step 5, Task 7 step 6, Task 8 step 6 |
  | Responsiveness and cancel | `tick`/`processEvents` in Tasks 7–8; Task 10 step 6 |
  | Code layout | As the spec's |
  | Testing: pure, synthetic end-to-end, ad hoc real, failure modes | Tasks 1–6; Tasks 6 and 8; Task 8 step 6; Task 6 (blank, few, scrambled), Task 7 (no catalogue) |

- **Rulings versus the spec:**
  - the minimum field (0.45°, not 0.2°);
  - the index size (about 100 MB, measured);
  - the XISF centre properties, deferred.

  All three are listed under "Rulings" and are to be reported to the maintainer.
- **Type consistency:**
  - Index star ids are stored as `Float32` and are exact below 16.7M stars. `Solve.buildHash` guards against 4M quads.
  - `Solve.solve`'s results go to `Solve.hintsFrom`, whose output goes to `Sky.solveWithHints`: the same `{ra, dec, focal, pixel}` shape as today.
  - `progress` is always `{ stage, isCancelled }`.
