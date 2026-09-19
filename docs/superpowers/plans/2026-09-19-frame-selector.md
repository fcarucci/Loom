# Loom Frame Selector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A second PixInsight script in the Loom repository that measures every
subframe in a folder with SubframeSelector, groups them by filter, decides which
are bad using per-channel robust statistics, and removes them — showing the
verdict and a 1:1 preview before anything is deleted.

**Architecture:** All decision logic is pure arithmetic in `script/lib/Frames.js`
and is tested under node with no PixInsight present. Everything that touches
PixInsight — the SubframeSelector call, the preview control, the dialog, the
file operations — lives in `script/FrameSelector.js` and is tested inside
PixInsight. The two are separated so that the part that decides what to delete
can be tested exhaustively without a workspace.

**Tech Stack:** PJSR (`#engine v8`) on PixInsight 1.9.5 Lockhart build 1702;
`SubframeSelector` for measurement; `Control` + `Bitmap` + `Image.render()` for
the preview; node 20 for the pure-logic harness (`ci/run-tests.js`).

**Spec:** `docs/superpowers/specs/2026-09-19-frame-selector-design.md`

## Global Constraints

- **PixInsight 1.9.5 or newer.** The spec's measurement indices were read off
  build 1702. Loom already refuses to run on older cores; this script does the
  same check.
- **`#engine v8`** on the first line of every script file.
- **Never `#include "relative.js"`** — a quoted relative include makes
  PixInsight discard the whole script silently. Use absolute paths in generated
  test scripts and `#include <pjsr/...>` for core headers.
- **No new library dependencies.** `Util`, `Cache` and `Steps` are reused as-is.
- **Every assertion goes in `script/selftest.js`**, using the existing
  `check( name, actual, expected )`. There is no second test file.
- **PixInsight-only assertions go inside `if ( IN_PIXINSIGHT )`** so
  `node ci/run-tests.js` stays green.
- **Tests run two ways and both must pass:**
  - `node ci/run-tests.js` — pure logic
  - dispatch `script/selftest.js` to the running instance, then read
    `/tmp/agent-scratch/lhso-selftest.txt`
- **Reuse the running PixInsight instance.** Dispatch with
  `/Applications/PixInsight/PixInsight.app/Contents/MacOS/PixInsight -x=1:<abs path>`.
  Never launch a second instance.
- **Scratch files go in `/tmp/agent-scratch`**, never in `$TMPDIR` — a script
  under the sandbox temp directory is silently not executed.
- **Comments explain why, not what**, matching the surrounding code.
- **ES6 classes for every PJSR subclass**: `class extends Dialog`, `super()`.
  The `this.__base__` pattern is rejected under V8 and Loom already uses the
  class form (`script/lib/UI.js:89`).
- **V8 enums, not the legacy underscore constants.** Verified in the running
  instance: `FocusStyle_Click`, `KeyModifier_Shift`, `Key_Left` and
  `DataType_ByteArray` are all **undefined**; use `FocusStyle.Click`,
  `KeyModifier.Shift`, `KeyCode.Left` and `DataType.ByteArray`.
- **A namespace is `var X = {};`**, matching `Util` and `Steps`.
- **PSF SNR is measurement column 28.** Not 8. See Task 3.

---

## File Structure

| File | Responsibility |
|---|---|
| `script/lib/Frames.js` (create) | Pure decision logic: metrics parsing, validity, robust statistics, gates, score, modes, presets, grouping, manifest arithmetic. No PixInsight calls except where noted in Task 4. |
| `script/FrameSelector.js` (create) | Entry point, `#feature-id`, the dialog, the preview control, the SubframeSelector driver, the file operations. |
| `script/selftest.js` (modify) | All assertions, pure ones unguarded and PixInsight ones inside `IN_PIXINSIGHT`. |
| `ci/run-tests.js` (modify, Task 2) | Load `lib/Frames.js` alongside the other libraries. |
| `docs/verified-parameters.md` (modify, Task 3) | Record the measurement column indices and how they were verified. |

`Frames.js` is one file rather than several because its pieces are a single
chain — parse, validate, summarise, gate, score — and splitting them would mean
four files that are only ever used together.

---

### Task 1: Preview control prototype

The spec names this the biggest unknown, so it is built first and thrown away if
it does not work. Nothing else depends on its code, only on its answer.

**Files:**
- Create: `/tmp/agent-scratch/preview-proto.js` (throwaway, not committed)

**Interfaces:**
- Consumes: nothing
- Produces: a measured answer to "is a 1:1 pannable preview viable", recorded in
  the plan's notes. No code that later tasks import.

- [ ] **Step 1: Write the prototype**

Create `/tmp/agent-scratch/preview-proto.js`. It opens one real sub, stretches a
duplicate's pixels, renders it once, and shows it in a pannable control:

```javascript
#engine v8
#include <pjsr/UndoFlag.jsh>

#define SUB "/Volumes/A008/Elephant Trunk/calibrated/Light_BIN-1_6248x4176_EXPOSURE-60.00s_FILTER-G_mono_DAY-11 - Pinnacles"

var LOG = "/tmp/agent-scratch/preview-proto.txt";
var lines = [];
function say( s ) { lines.push( String( s ) );
                    File.writeTextFile( LOG, lines.join( "\n" ) + "\n" ); }

/*
 * Image.render() does NOT apply a screen stretch -- its own documentation
 * excludes it -- so the duplicate's PIXELS are stretched before rendering.
 */
function stretchedBitmap( win )
{
   var img = win.mainView.image;
   var med = img.median(), mad = img.MAD()*1.4826;
   var shadows = Math.max( 0, med - 2.8*mad );
   var midtone = Math.mtf( 0.25, med - shadows );

   var dup = new ImageWindow( img.width, img.height, img.numberOfChannels,
                              img.bitsPerSample, img.isReal, img.isColor,
                              "proto_dup" );
   dup.mainView.beginProcess( UndoFlag_NoSwapFile );
   dup.mainView.image.assign( img );
   dup.mainView.endProcess();

   var H = new HistogramTransformation;
   H.H = [ [ 0, 0.5, 1, 0, 1 ], [ 0, 0.5, 1, 0, 1 ], [ 0, 0.5, 1, 0, 1 ],
           [ shadows, midtone, 1, 0, 1 ], [ 0, 0.5, 1, 0, 1 ] ];
   H.executeOn( dup.mainView );

   var t0 = Date.now();
   var bmp = dup.mainView.image.render();
   say( "render: " + ( Date.now() - t0 ) + " ms, bitmap " +
        bmp.width + "x" + bmp.height );
   dup.forceClose();
   return bmp;
}

var PreviewDialog = class extends Dialog
{
   constructor( bmp )
   {
   super();
   var self = this;
   this.bmp = bmp;
   this.ox = 0; this.oy = 0;          // top-left of the visible region
   this.dragging = false;

   this.view = new Control( this );
   this.view.setScaledMinSize( 600, 400 );
   this.view.focusStyle = FocusStyle.Click;   // arrows need focus
   this.view.onPaint = function()
   {
      var g = new Graphics( this );
      g.fillRect( 0, 0, this.width, this.height, new Brush( 0xff000000 ) );
      g.drawBitmapRect( 0, 0, self.bmp,
                        new Rect( self.ox, self.oy,
                                  Math.min( self.ox + this.width, self.bmp.width ),
                                  Math.min( self.oy + this.height, self.bmp.height ) ) );
      g.end();
   };
   this.view.onMousePress = function( x, y ) { self.dragging = true;
                                               self.lx = x; self.ly = y; };
   this.view.onMouseRelease = function() { self.dragging = false; };
   this.view.onMouseMove = function( x, y )
   {
      if ( !self.dragging ) return;
      self.pan( self.lx - x, self.ly - y );
      self.lx = x; self.ly = y;
   };
   this.view.onKeyPress = function( key, mod )
   {
      var step = ( mod & KeyModifier.Shift ) ? this.width : Math.round( this.width/4 );
      if ( key == KeyCode.Left  ) { self.pan( -step, 0 ); return true; }
      if ( key == KeyCode.Right ) { self.pan(  step, 0 ); return true; }
      if ( key == KeyCode.Up    ) { self.pan( 0, -step ); return true; }
      if ( key == KeyCode.Down  ) { self.pan( 0,  step ); return true; }
      return false;                       // MUST consume handled keys only
   };
   this.pan = function( dx, dy )
   {
      self.ox = Math.max( 0, Math.min( self.ox + dx, self.bmp.width  - self.view.width ) );
      self.oy = Math.max( 0, Math.min( self.oy + dy, self.bmp.height - self.view.height ) );
      self.view.update();
   };

   this.sizer = new VerticalSizer;
   this.sizer.add( this.view );
   this.windowTitle = "preview prototype";
   this.adjustToContents();
   }
};

try
{
   var find = new FileFind, first = null;
   if ( find.begin( SUB + "/*.xisf" ) )
      do { if ( find.isFile ) { first = SUB + "/" + find.name; break; } }
      while ( find.next() );
   if ( first == null )
      throw new Error( "no sub found" );
   say( "frame: " + first );

   var t0 = Date.now();
   var win = ImageWindow.open( first )[0];
   say( "open: " + ( Date.now() - t0 ) + " ms" );

   var bmp = stretchedBitmap( win );
   say( "total open->bitmap: " + ( Date.now() - t0 ) + " ms" );
   say( "physicalPixelRatio: " + bmp.physicalPixelRatio );
   win.forceClose();

   ( new PreviewDialog( bmp ) ).execute();
   say( "done" );
}
catch ( e ) { say( "FATAL " + e ); }
```

- [ ] **Step 2: Run it and read the timing**

```bash
rm -f /tmp/agent-scratch/preview-proto.txt
/Applications/PixInsight/PixInsight.app/Contents/MacOS/PixInsight \
  -x=1:/tmp/agent-scratch/preview-proto.js
cat /tmp/agent-scratch/preview-proto.txt
```

Expected: an open time, a render time, and a window you can drag and arrow
around. Record `total open->bitmap`.

- [ ] **Step 3: Decide, and write the answer down**

The gate is **under 1.5 s from click to a drawn preview**. If it is slower,
stop and report rather than building the dialog around it — the fallbacks are a
downsampled preview (`Image.render( -2 )`) or rendering only the visible region
via `Image.cropTo` on the duplicate.

Also confirm by eye, with a checkerboard region of the frame:
- arrow keys pan only when the preview has focus, and do not also move a
  selection elsewhere
- `physicalPixelRatio` is 2 on this Retina display, so "1:1" must divide by it

Write the measured numbers into the plan file under this task, then continue.

- [ ] **Step 4: No commit**

Nothing here is committed. The prototype is evidence, not code.

---

### Task 2: `Frames.js` skeleton and the harness wiring

**Files:**
- Create: `script/lib/Frames.js`
- Modify: `ci/run-tests.js:133` (the `LIBS` array)
- Test: `script/selftest.js`

**Interfaces:**
- Consumes: nothing
- Produces: the global `Frames` object, loadable under node and in PixInsight.

- [ ] **Step 1: Write the failing test**

Add to `runTests()` in `script/selftest.js`, immediately before the closing
`check( "the shipped model container is recognised", ... )` block:

```javascript
   /*
    * Frame Selector. The decision logic is pure so that what chooses which
    * files to delete can be tested without a workspace.
    */
   check( "Frames loads", typeof Frames, "object" );   // var Frames = {}
   check( "and declares the version its numbers came from",
          Frames.MEASURE_VERSION, "v1" );
```

- [ ] **Step 2: Run it to make sure it fails**

```bash
cd /Users/francescocarucci/PixInsight/scripts/Loom && node ci/run-tests.js
```

Expected: `FAILED TO LOAD` or `Frames loads: expected "object", got "undefined"`.

- [ ] **Step 3: Create the library**

`script/lib/Frames.js`:

```javascript
/*
 * Frame Selector: the decisions.
 *
 * Everything here is pure arithmetic over measurements, with no PixInsight
 * call and no file access, because this is the code that decides which of
 * someone's subframes get deleted. It is tested under node, exhaustively,
 * including every degenerate case the review found -- an eccentricity of
 * zero, a MAD of zero, a channel of four frames -- none of which can be
 * reproduced on demand with real data.
 *
 * The PixInsight half lives in script/FrameSelector.js.
 */

var Frames = {};

/*
 * Bumped when the meaning of a stored measurement changes. A cached number
 * from an older definition compared against a fresh one is exactly the
 * quiet wrongness this tool exists to report.
 */
Frames.MEASURE_VERSION = "v1";
```

- [ ] **Step 4: Wire it into the node harness**

In `ci/run-tests.js`, add `"lib/Frames.js"` to `LIBS`, after `"lib/Steps.js"`:

```javascript
const LIBS = [ "lib/Util.js", "lib/Cache.js", "lib/Psb.js", "lib/Steps.js",
               "lib/Frames.js",
               "lib/Pipeline.js", "lib/Update.js", "lib/UI.js" ];
```

- [ ] **Step 5: Wire it into the PixInsight harness too**

The node loader is not the only one. In `script/selftest.js`, add the include
beside the others (after `lib/Steps.js`):

```javascript
#include "lib/Frames.js"
```

Without this the suite passes under node and fails in PixInsight with
`Frames is not defined` — the two harnesses load libraries by different
mechanisms and a library must be added to both.

- [ ] **Step 6: Run both**

```bash
node ci/run-tests.js
rm -f /tmp/agent-scratch/lhso-selftest.txt
/Applications/PixInsight/PixInsight.app/Contents/MacOS/PixInsight \
  -x=1:/Users/francescocarucci/PixInsight/scripts/Loom/script/selftest.js
until [ -f /tmp/agent-scratch/lhso-selftest.txt ]; do sleep 5; done
head -4 /tmp/agent-scratch/lhso-selftest.txt
```

Expected: both PASS, two more assertions than before.

- [ ] **Step 7: Commit**

```bash
git add script/lib/Frames.js ci/run-tests.js script/selftest.js
git commit -m "Add the Frame Selector's decision library"
```

---

### Task 3: Measurement columns, verified by meaning

**Files:**
- Modify: `script/lib/Frames.js`
- Modify: `script/selftest.js`
- Modify: `docs/verified-parameters.md`

**Interfaces:**
- Consumes: `Frames` from Task 2
- Produces:
  - `Frames.COL = { path: 3, fwhm: 5, eccentricity: 6, noise: 12, stars: 14, psfSNR: 28 }`
  - `Frames.metricsFromRow( row )` → `{ path, fwhm, eccentricity, noise, stars, psfSNR }`
  - `Frames.PLAUSIBLE` → per-metric `{ lo, hi }` ranges used by the meaning test

- [ ] **Step 1: Write the failing tests**

```javascript
   /*
    * The measurement row is positional. An earlier draft of the spec had PSF
    * SNR on column 8, which reads 0 on every frame -- the dominant term of
    * the score would have been a constant zero and nothing would have looked
    * wrong. WBPP's own analyzer names these indices and carries the comment
    * that they must track the process implementation
    * (BPP-SubframeAnalyzer.js:481).
    */
   check( "PSF SNR is column 28, not 8", Frames.COL.psfSNR, 28 );
   check( "FWHM is column 5", Frames.COL.fwhm, 5 );
   check( "eccentricity is column 6", Frames.COL.eccentricity, 6 );
   check( "noise is column 12", Frames.COL.noise, 12 );
   check( "stars is column 14", Frames.COL.stars, 14 );
   check( "the path is column 3", Frames.COL.path, 3 );

   ( function()
   {
      var row = [];
      for ( var i = 0; i < 31; ++i ) row.push( 0 );
      row[3] = "/m/sub.xisf"; row[5] = 6.82; row[6] = 0.395;
      row[12] = 9.6e-6; row[14] = 10029; row[28] = 25502;
      var m = Frames.metricsFromRow( row );
      check( "a row becomes named metrics", m.fwhm, 6.82 );
      check( "including the path it belongs to", m.path, "/m/sub.xisf" );
      check( "and PSF SNR from 28", m.psfSNR, 25502 );
   } )();

   /*
    * Pinning the constants does not detect a reorder -- the assertions above
    * pass unchanged after the process shuffles its table. The ranges below
    * are what makes a shuffle fail: they do not overlap, so a value that
    * moves lands outside the range for its slot. Used by the PixInsight
    * fixture test.
    */
   /*
    * Every pair must be disjoint, not just one pair: the check exists to
    * fail when two columns are swapped, and it can only do that if no
    * metric's range contains another's.
    */
   check( "no two metric ranges overlap",
          ( function()
            {
               var names = Frames.METRIC_RANGE_ORDER;
               for ( var i = 0; i + 1 < names.length; ++i )
               {
                  var a = Frames.PLAUSIBLE[names[i]], b = Frames.PLAUSIBLE[names[i+1]];
                  if ( !( a.hi < b.lo ) )
                     return names[i] + " overlaps " + names[i+1];
               }
               return "disjoint";
            } )(), "disjoint" );
```

- [ ] **Step 2: Run to verify it fails**

```bash
node ci/run-tests.js
```

Expected: FAIL, `Frames.COL` undefined.

- [ ] **Step 3: Implement**

Append to `script/lib/Frames.js`:

```javascript
/*
 * SubframeSelector's measurement row, by position.
 *
 * Read from WBPP's own analyzer, which carries the comment "fixed indexes
 * that need to be aligned with the process implementation"
 * (BPP-SubframeAnalyzer.js:481), and cross-checked against a live
 * measurement on 1.9.5 build 1702.
 *
 * PSF SNR is 28. An earlier draft said 8; column 8 reads 0 on every frame
 * measured here, so the score's dominant term would have been a constant
 * zero -- a failure with no symptom.
 */
Frames.COL = { path: 3, fwhm: 5, eccentricity: 6, noise: 12, stars: 14,
               psfSNR: 28 };

Frames.metricsFromRow = function( row )
{
   return { path:         row[Frames.COL.path],
            fwhm:         row[Frames.COL.fwhm],
            eccentricity: row[Frames.COL.eccentricity],
            noise:        row[Frames.COL.noise],
            stars:        row[Frames.COL.stars],
            psfSNR:       row[Frames.COL.psfSNR] };
};

/*
 * Ranges a metric can occupy on a real subframe, used to check that a
 * column still MEANS what it is read as. Deliberately non-overlapping
 * between metrics, so a reordered table puts a value outside its range.
 */
/*
 * These must not overlap, or the check they exist for cannot fail. An
 * earlier draft had psfSNR spanning 1..1e9, which CONTAINS the whole star
 * range -- swapping stars 10029 with PSF SNR 25502 passed both tests and
 * proved nothing. The bounds below come from measurements of real
 * subframes on this rig and are deliberately tight.
 */
Frames.PLAUSIBLE = {
   fwhm:         { lo: 0.5,   hi: 30 },        // pixels
   eccentricity: { lo: 0.01,  hi: 0.95 },      // a fraction, never 0 or 1
   stars:        { lo: 200,   hi: 200000 },    // counts
   psfSNR:       { lo: 200000, hi: 1e9 }       // far above any star count
};

/* Ascending and disjoint, so the overlap check can walk them in order. */
Frames.METRIC_RANGE_ORDER = [ "eccentricity", "fwhm", "stars", "psfSNR" ];

Frames.metricInRange = function( name, value )
{
   var r = Frames.PLAUSIBLE[name];
   return ( r != null ) && isFinite( value ) && value >= r.lo && value <= r.hi;
};
```

- [ ] **Step 4: Run to verify it passes**

```bash
node ci/run-tests.js
```

Expected: PASS.

- [ ] **Step 5: Record the verification**

Append to `docs/verified-parameters.md`:

```markdown
## SubframeSelector measurement columns (1.9.5 build 1702)

`routine = 0` measures; 1 and 2 are the preview and output routines and refuse
with "No measurements have been made". The `subframes` table takes four values
per row: enabled, path, local normalization data, drizzle data.

| index | figure |
|---|---|
| 3 | file path |
| 5 | FWHM |
| 6 | eccentricity |
| 7 | PSF signal weight |
| 9 | SNR estimate |
| 12 | noise |
| 14 | stars |
| 28 | PSF SNR |

Source: WBPP's `BPP-SubframeAnalyzer.js:481`, which carries the comment "fixed
indexes that need to be aligned with the process implementation", cross-checked
against a live measurement. **PSF SNR is 28, not 8** — column 8 reads 0 on every
frame measured here, so reading it as PSF SNR produces a constant with no
symptom.
```

- [ ] **Step 6: Commit**

```bash
git add script/lib/Frames.js script/selftest.js docs/verified-parameters.md
git commit -m "Name the measurement columns, and record where they came from"
```

---

### Task 4: Validity and the unmeasurable state

**Files:**
- Modify: `script/lib/Frames.js`
- Modify: `script/selftest.js`

**Interfaces:**
- Consumes: `Frames.metricsFromRow`
- Produces:
  - `Frames.METRICS` → `[ "psfSNR", "fwhm", "eccentricity", "stars" ]`
  - `Frames.metricValid( value )` → Boolean
  - `Frames.frameValid( metrics )` → Boolean
  - `Frames.STATE = { APPROVED: "approved", REJECTED: "rejected", UNMEASURABLE: "unmeasurable" }`

- [ ] **Step 1: Write the failing tests**

```javascript
   /*
    * NaN escapes every comparison, so an invalid measurement would pass every
    * rejection gate and be silently kept. It becomes its own state instead.
    */
   check( "a finite positive number is valid", Frames.metricValid( 6.8 ), true );
   check( "zero is not -- it divides", Frames.metricValid( 0 ), false );
   check( "nor is a negative", Frames.metricValid( -1 ), false );
   check( "nor NaN", Frames.metricValid( NaN ), false );
   check( "nor infinity", Frames.metricValid( Infinity ), false );
   check( "nor a missing value", Frames.metricValid( undefined ), false );

   ( function()
   {
      var good = { psfSNR: 25502, fwhm: 6.82, eccentricity: 0.395, stars: 10029 };
      check( "a frame with four valid metrics is measurable",
             Frames.frameValid( good ), true );
      var zeroEcc = { psfSNR: 25502, fwhm: 6.82, eccentricity: 0, stars: 10029 };
      check( "an eccentricity of zero makes it unmeasurable",
             Frames.frameValid( zeroEcc ), false );
      var noStars = { psfSNR: 25502, fwhm: 6.82, eccentricity: 0.4, stars: 0 };
      check( "so does a star count of zero",
             Frames.frameValid( noStars ), false );
   } )();

   check( "the four metrics are named once, in scoring order",
          Frames.METRICS.join( "," ), "psfSNR,fwhm,eccentricity,stars" );
```

- [ ] **Step 2: Run to verify it fails**

```bash
node ci/run-tests.js
```

Expected: FAIL, `Frames.metricValid is not a function`.

- [ ] **Step 3: Implement**

```javascript
/*
 * The four figures the selector judges on, in the order the score weights
 * them. Named once so a metric cannot be added to the score and forgotten
 * in the gates.
 */
Frames.METRICS = [ "psfSNR", "fwhm", "eccentricity", "stars" ];

Frames.STATE = { APPROVED: "approved",
                 REJECTED: "rejected",
                 UNMEASURABLE: "unmeasurable" };

/*
 * Valid means finite AND positive, not merely present.
 *
 * Zero is excluded because every one of these appears in a denominator: an
 * eccentricity of 0 makes the score infinite, and a star count of 0 makes
 * the median ratio infinite. A zero weight does not rescue it either --
 * JavaScript evaluates 0 * Infinity as NaN.
 */
Frames.metricValid = function( value )
{
   return typeof value == "number" && isFinite( value ) && value > 0;
};

/*
 * A frame is measurable only if EVERY metric is. A frame missing one is not
 * scored and not auto-rejected: comparisons against NaN are all false, so an
 * invalid frame would otherwise sail through every gate and be kept.
 */
Frames.frameValid = function( metrics )
{
   if ( metrics == null )
      return false;
   for ( var i = 0; i < Frames.METRICS.length; ++i )
      if ( !Frames.metricValid( metrics[Frames.METRICS[i]] ) )
         return false;
   return true;
};
```

- [ ] **Step 4: Run to verify it passes**

```bash
node ci/run-tests.js
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add script/lib/Frames.js script/selftest.js
git commit -m "Make an unmeasurable frame a state rather than a silent keep"
```

---

### Task 5: Robust statistics and the gates

**Files:**
- Modify: `script/lib/Frames.js`
- Modify: `script/selftest.js`

**Interfaces:**
- Consumes: `Frames.METRICS`, `Frames.frameValid`
- Produces:
  - `Frames.MIN_FRAMES = 10`
  - `Frames.SIGMA_FLOOR_FRACTION = 0.01`
  - `Frames.median( values )` → Number
  - `Frames.sigma( values )` → normalised MAD
  - `Frames.WORSE_WHEN = { psfSNR: "lower", fwhm: "higher", eccentricity: "higher", stars: "lower" }`
  - `Frames.gate( values, metric, k )` → `{ active: Boolean, limit: Number|null, median, sigma, reason: String|null }`
  - `Frames.relativeGates( frames, k )` → `{ <metric>: gate }`

- [ ] **Step 1: Write the failing tests**

```javascript
   check( "the median of an odd count is the middle value",
          Frames.median( [ 3, 1, 2 ] ), 2 );
   check( "and of an even count, the mean of the middle two",
          Frames.median( [ 1, 2, 3, 4 ] ), 2.5 );
   /*
    * Normalised: sigma = 1.4826 * MAD, which is what makes k a number of
    * standard deviations rather than an arbitrary width.
    */
   check( "sigma is the NORMALISED MAD",
          Math.round( Frames.sigma( [ 1, 2, 3, 4, 5 ] )*10000 ), 14826 );

   ( function()
   {
      var fwhm = [ 4.0, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 5.3, 10 ];
      var g = Frames.gate( fwhm, "fwhm", 2.5 );
      check( "a gate on a spread sample is active", g.active, true );
      check( "and rejects above the limit for a higher-is-worse metric",
             g.limit > 4.7 && g.limit < 10, true );
   } )();

   /*
    * With no spread the MAD is zero and every preset rejects the one frame
    * that differs in the sixth decimal. The gate switches off instead.
    */
   ( function()
   {
      var g = Frames.gate( [ 4, 4, 4, 4, 4.000001 ], "fwhm", 2.5 );
      check( "a gate with no usable spread is disabled", g.active, false );
      check( "and says why", g.reason.indexOf( "spread" ) >= 0, true );
   } )();

   /*
    * A lower bound at or below zero cannot reject anything, so it is
    * reported inactive rather than silently passing everything.
    */
   ( function()
   {
      var g = Frames.gate( [ 10, 1000, 2000, 3000 ], "psfSNR", 3.0 );
      check( "a non-positive lower bound is inactive", g.active, false );
   } )();

   check( "ten valid frames is the minimum for a relative clip",
          Frames.MIN_FRAMES, 10 );
   check( "below it there are no gates at all",
          Object.keys( Frames.relativeGates(
             [ { psfSNR: 1, fwhm: 1, eccentricity: 0.5, stars: 200 } ], 2.5 ) ).length, 0 );
```

- [ ] **Step 2: Run to verify it fails**

```bash
node ci/run-tests.js
```

Expected: FAIL, `Frames.median is not a function`.

- [ ] **Step 3: Implement**

```javascript
/*
 * Below this many VALID measurements the relative clip does not run. A
 * median exists at three frames; a dispersion worth deleting files over does
 * not. At ten frames the simulated loss is already about double its
 * asymptotic value, which is the honest reason this is not lower.
 */
Frames.MIN_FRAMES = 10;

/*
 * A spread below this fraction of the median is not a spread, it is the
 * measurement's own repeatability. Clipping on it rejects a perfect night:
 * FWHM [4, 4, 4, 4, 4.000001] has a MAD of zero and every k rejects the
 * last frame.
 */
Frames.SIGMA_FLOOR_FRACTION = 0.01;

/* Which direction is bad, per metric. */
Frames.WORSE_WHEN = { psfSNR: "lower", fwhm: "higher",
                      eccentricity: "higher", stars: "lower" };

Frames.median = function( values )
{
   if ( values == null || values.length == 0 )
      return NaN;
   var v = values.slice().sort( function( a, b ) { return a - b; } );
   var n = v.length, h = n >> 1;
   return ( n % 2 ) ? v[h] : ( v[h-1] + v[h] )/2;
};

/*
 * MAD, normalised to a Gaussian sigma. MAD rather than the standard
 * deviation because a handful of disasters widens a deviation-based gate and
 * defeats the thing meant to catch them.
 */
Frames.sigma = function( values )
{
   var m = Frames.median( values );
   if ( !isFinite( m ) )
      return NaN;
   var d = [];
   for ( var i = 0; i < values.length; ++i )
      d.push( Math.abs( values[i] - m ) );
   return 1.4826 * Frames.median( d );
};

/*
 * One metric's rejection bound. Returns the bound AND whether it is usable,
 * because a gate that cannot reject must say so rather than quietly passing
 * everything.
 */
Frames.gate = function( values, metric, k )
{
   var med = Frames.median( values ), sd = Frames.sigma( values );
   var g = { active: false, limit: null, median: med, sigma: sd, reason: null };
   if ( !isFinite( med ) || !isFinite( sd ) )
      { g.reason = "no usable measurements"; return g; }
   if ( sd <= 0 || sd < Frames.SIGMA_FLOOR_FRACTION*Math.abs( med ) )
      { g.reason = "no usable spread"; return g; }

   g.limit = ( Frames.WORSE_WHEN[metric] == "higher" ) ? med + k*sd : med - k*sd;
   if ( Frames.WORSE_WHEN[metric] == "lower" && g.limit <= 0 )
      { g.reason = "lower bound is not positive"; g.limit = null; return g; }
   g.active = true;
   return g;
};

/*
 * Every gate for a channel, or none at all below the minimum count. The
 * caller cannot accidentally apply a gate built from four frames.
 */
Frames.relativeGates = function( frames, k )
{
   var valid = [];
   for ( var i = 0; i < frames.length; ++i )
      if ( Frames.frameValid( frames[i] ) )
         valid.push( frames[i] );
   if ( valid.length < Frames.MIN_FRAMES )
      return {};

   var gates = {};
   for ( var m = 0; m < Frames.METRICS.length; ++m )
   {
      var name = Frames.METRICS[m], values = [];
      for ( var j = 0; j < valid.length; ++j )
         values.push( valid[j][name] );
      gates[name] = Frames.gate( values, name, k );
   }
   return gates;
};
```

- [ ] **Step 4: Run to verify it passes**

```bash
node ci/run-tests.js
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add script/lib/Frames.js script/selftest.js
git commit -m "Add the robust gates, and make an unusable gate say so"
```

---

### Task 6: The score

**Files:**
- Modify: `script/lib/Frames.js`
- Modify: `script/selftest.js`

**Interfaces:**
- Consumes: `Frames.METRICS`, `Frames.frameValid`, `Frames.median`
- Produces:
  - `Frames.DEFAULT_WEIGHTS = { psfSNR: 0.5, fwhm: 0.3, eccentricity: 0.1, stars: 0.1 }`
  - `Frames.CLAMP = { lo: 0.25, hi: 4 }`
  - `Frames.medians( frames )` → `{ <metric>: Number }`
  - `Frames.score( metrics, medians, weights )` → Number, or `null` when unmeasurable

- [ ] **Step 1: Write the failing tests**

```javascript
   ( function()
   {
      var meds = { psfSNR: 1000, fwhm: 5, eccentricity: 0.4, stars: 10000 };
      var typical = { psfSNR: 1000, fwhm: 5, eccentricity: 0.4, stars: 10000 };
      check( "a frame at the median scores 1",
             Math.round( Frames.score( typical, meds,
                         Frames.DEFAULT_WEIGHTS )*1000 )/1000, 1 );

      var sharper = { psfSNR: 1000, fwhm: 2.5, eccentricity: 0.4, stars: 10000 };
      check( "a sharper frame scores higher",
             Frames.score( sharper, meds, Frames.DEFAULT_WEIGHTS ) > 1, true );

      /*
       * Dividing by the median aligns typical levels but not dispersion: a
       * near-zero eccentricity would contribute a term of 40 and swamp a
       * nominal weight of 0.1. Terms are clamped.
       */
      var silly = { psfSNR: 1000, fwhm: 5, eccentricity: 0.001, stars: 10000 };
      check( "a near-zero eccentricity cannot dominate",
             Frames.score( silly, meds, Frames.DEFAULT_WEIGHTS ) <
             Frames.score( typical, meds, Frames.DEFAULT_WEIGHTS ) + 0.4, true );

      var broken = { psfSNR: 1000, fwhm: 5, eccentricity: 0, stars: 10000 };
      check( "an unmeasurable frame has no score, not a NaN",
             Frames.score( broken, meds, Frames.DEFAULT_WEIGHTS ), null );
   } )();

   check( "the weights favour SNR", Frames.DEFAULT_WEIGHTS.psfSNR, 0.5 );
   check( "and sum to one",
          Frames.DEFAULT_WEIGHTS.psfSNR + Frames.DEFAULT_WEIGHTS.fwhm +
          Frames.DEFAULT_WEIGHTS.eccentricity + Frames.DEFAULT_WEIGHTS.stars, 1 );
```

- [ ] **Step 2: Run to verify it fails**

```bash
node ci/run-tests.js
```

Expected: FAIL, `Frames.score is not a function`.

- [ ] **Step 3: Implement**

```javascript
/*
 * SNR dominant, which is the conventional weighting. The weights are a
 * ranking preference and not an allocation of "quality" between independent
 * things: SNR, star count and FWHM all move together through detection and
 * fitting.
 */
Frames.DEFAULT_WEIGHTS = { psfSNR: 0.5, fwhm: 0.3, eccentricity: 0.1,
                           stars: 0.1 };

/*
 * Two stops either side of typical. Beyond that the frame's ranking is not
 * in question, and without a clamp a near-zero eccentricity contributes 40
 * against its nominal weight of 0.1.
 */
Frames.CLAMP = { lo: 0.25, hi: 4 };

Frames.medians = function( frames )
{
   var out = {};
   for ( var m = 0; m < Frames.METRICS.length; ++m )
   {
      var name = Frames.METRICS[m], values = [];
      for ( var i = 0; i < frames.length; ++i )
         if ( Frames.frameValid( frames[i] ) )
            values.push( frames[i][name] );
      out[name] = Frames.median( values );
   }
   return out;
};

/*
 * Higher is better in every term: FWHM and eccentricity are inverted, so a
 * sharper or rounder frame raises the score.
 *
 * Returns null rather than a number for an unmeasurable frame. A score of 0
 * would sort it below everything and invite someone to delete it.
 */
Frames.score = function( metrics, medians, weights )
{
   if ( !Frames.frameValid( metrics ) )
      return null;
   var w = weights || Frames.DEFAULT_WEIGHTS, total = 0;
   for ( var m = 0; m < Frames.METRICS.length; ++m )
   {
      var name = Frames.METRICS[m], med = medians[name];
      if ( !Frames.metricValid( med ) )
         return null;
      var ratio = ( Frames.WORSE_WHEN[name] == "higher" )
                  ? med/metrics[name] : metrics[name]/med;
      ratio = Math.max( Frames.CLAMP.lo, Math.min( Frames.CLAMP.hi, ratio ) );
      total += ( w[name] || 0 ) * ratio;
   }
   return total;
};
```

- [ ] **Step 4: Run to verify it passes**

```bash
node ci/run-tests.js
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add script/lib/Frames.js script/selftest.js
git commit -m "Score frames for ranking, clamped so one term cannot dominate"
```

---

### Task 7: Modes, presets and the verdict

**Files:**
- Modify: `script/lib/Frames.js`
- Modify: `script/selftest.js`

**Interfaces:**
- Consumes: everything from Tasks 4–6
- Produces:
  - `Frames.PRESETS = { lenient: 3.0, balanced: 2.5, strict: 2.0 }`
  - `Frames.DEFAULT_PRESET = "balanced"`
  - `Frames.MODE = { RELATIVE: "relative", ABSOLUTE: "absolute", BOTH: "both" }`
  - `Frames.defaultSettings()` → `{ mode, k, weights, limits, enabled, kEdited }`
  - `Frames.applyPreset( settings, preset )` → new settings
  - `Frames.verdict( metrics, gates, settings )` → `{ state, reasons: [String] }`

- [ ] **Step 1: Write the failing tests**

```javascript
   check( "the opening preset is Balanced", Frames.DEFAULT_PRESET, "balanced" );
   check( "Lenient is the widest gate", Frames.PRESETS.lenient, 3.0 );
   check( "Strict the narrowest", Frames.PRESETS.strict, 2.0 );

   /*
    * A preset sets k for every channel whose k has not been edited by hand.
    * An edited value stands, and editing a WEIGHT does not pin k.
    */
   ( function()
   {
      var s = Frames.defaultSettings();
      s.k = 2.7; s.kEdited = true;
      check( "an edited k survives a preset change",
             Frames.applyPreset( s, "strict" ).k, 2.7 );

      var w = Frames.defaultSettings();
      w.weights.fwhm = 0.5;
      check( "but an edited weight does not pin k",
             Frames.applyPreset( w, "strict" ).k, 2.0 );
   } )();

   ( function()
   {
      var gates = Frames.relativeGates( [
         { psfSNR: 1000, fwhm: 5.0, eccentricity: 0.40, stars: 10000 },
         { psfSNR: 1010, fwhm: 5.1, eccentricity: 0.41, stars: 10100 },
         { psfSNR: 1020, fwhm: 5.2, eccentricity: 0.42, stars: 10200 },
         { psfSNR: 1030, fwhm: 5.3, eccentricity: 0.40, stars: 10300 },
         { psfSNR: 1040, fwhm: 5.4, eccentricity: 0.41, stars: 10400 },
         { psfSNR: 1050, fwhm: 5.5, eccentricity: 0.42, stars: 10500 },
         { psfSNR: 1060, fwhm: 5.6, eccentricity: 0.40, stars: 10600 },
         { psfSNR: 1070, fwhm: 5.7, eccentricity: 0.41, stars: 10700 },
         { psfSNR: 1080, fwhm: 5.8, eccentricity: 0.42, stars: 10800 },
         { psfSNR: 1090, fwhm: 5.9, eccentricity: 0.40, stars: 10900 } ], 2.5 );

      var soft = { psfSNR: 1000, fwhm: 20, eccentricity: 0.4, stars: 10000 };
      var fine = { psfSNR: 1050, fwhm: 5.5, eccentricity: 0.41, stars: 10500 };

      var rel = Frames.defaultSettings();
      check( "Relative rejects a frame outside the gate",
             Frames.verdict( soft, gates, rel ).state, Frames.STATE.REJECTED );
      check( "and names the metric that failed",
             Frames.verdict( soft, gates, rel ).reasons[0].indexOf( "FWHM" ) >= 0, true );
      check( "a typical frame is approved",
             Frames.verdict( fine, gates, rel ).state, Frames.STATE.APPROVED );

      /*
       * Hard limits cannot rescue a frame the relative gate rejects, so
       * Absolute switches the relative gate OFF. It is the only mode that
       * can keep an entire good night.
       */
      var abs = Frames.defaultSettings();
      abs.mode = Frames.MODE.ABSOLUTE;
      abs.limits.fwhm = { hi: 25 };
      check( "Absolute ignores the relative gate",
             Frames.verdict( soft, gates, abs ).state, Frames.STATE.APPROVED );
      check( "and rejects on its own ceiling",
             Frames.verdict( { psfSNR: 1000, fwhm: 30, eccentricity: 0.4, stars: 10000 },
                              gates, abs ).state, Frames.STATE.REJECTED );
      check( "Absolute with no limits keeps everything",
             Frames.verdict( soft, gates,
                ( function(){ var a = Frames.defaultSettings();
                              a.mode = Frames.MODE.ABSOLUTE; return a; } )()
             ).state, Frames.STATE.APPROVED );

      var both = Frames.defaultSettings();
      both.mode = Frames.MODE.BOTH;
      both.limits.fwhm = { hi: 25 };
      check( "Both rejects on either condition",
             Frames.verdict( soft, gates, both ).state, Frames.STATE.REJECTED );

      /*
       * The minimum-count rule disables RELATIVE gates only. In Relative mode
       * a thin channel therefore rejects nothing; it does not fall through to
       * limits that mode ignores by definition.
       */
      var thin = Frames.relativeGates( [ { psfSNR: 1, fwhm: 1, eccentricity: 0.5, stars: 200 } ], 2.5 );
      var relThin = Frames.defaultSettings();
      relThin.limits.fwhm = { hi: 2 };
      check( "a thin channel in Relative rejects nothing",
             Frames.verdict( { psfSNR: 1000, fwhm: 30, eccentricity: 0.4, stars: 10000 },
                              thin, relThin ).state, Frames.STATE.APPROVED );

      check( "an unmeasurable frame is neither approved nor rejected",
             Frames.verdict( { psfSNR: 1000, fwhm: 5, eccentricity: 0, stars: 10000 },
                              gates, rel ).state, Frames.STATE.UNMEASURABLE );
   } )();
```

- [ ] **Step 2: Run to verify it fails**

```bash
node ci/run-tests.js
```

Expected: FAIL, `Frames.PRESETS is not defined`.

- [ ] **Step 3: Implement**

```javascript
/*
 * k, the one number that decides how much is dropped. The names say which
 * end of the range they sit at; "Strict" and "Stricter" read as the same
 * thing and leave the loose end unnamed.
 */
Frames.PRESETS = { lenient: 3.0, balanced: 2.5, strict: 2.0 };
Frames.DEFAULT_PRESET = "balanced";

/*
 * A clip on median and MAD is scale-invariant, so Relative drops roughly the
 * same FRACTION however good the night was. That is right for "drop this
 * night's worst" and wrong for "drop frames that are bad in absolute terms",
 * and no amount of extra criteria reconciles them -- a frame the relative
 * gate rejects cannot be rescued by also passing a ceiling. Hence modes.
 */
Frames.MODE = { RELATIVE: "relative", ABSOLUTE: "absolute", BOTH: "both" };

Frames.defaultSettings = function()
{
   return { mode: Frames.MODE.RELATIVE,
            k: Frames.PRESETS[Frames.DEFAULT_PRESET],
            kEdited: false,
            weights: { psfSNR: Frames.DEFAULT_WEIGHTS.psfSNR,
                       fwhm: Frames.DEFAULT_WEIGHTS.fwhm,
                       eccentricity: Frames.DEFAULT_WEIGHTS.eccentricity,
                       stars: Frames.DEFAULT_WEIGHTS.stars },
            limits: {},              // metric -> { lo?, hi? }
            enabled: true };
};

/*
 * A preset sets k for every channel that has not had k edited by hand.
 * Editing a weight does not pin k -- only editing k does.
 */
Frames.applyPreset = function( settings, preset )
{
   var out = {}, keys = Object.keys( settings );
   for ( var i = 0; i < keys.length; ++i )
      out[keys[i]] = settings[keys[i]];
   if ( !settings.kEdited && Frames.PRESETS[preset] != null )
      out.k = Frames.PRESETS[preset];
   return out;
};

Frames.METRIC_LABEL = { psfSNR: "PSF SNR", fwhm: "FWHM",
                        eccentricity: "eccentricity", stars: "stars" };

/*
 * The verdict, with the rejection predicate stated per mode so nothing is
 * left to infer:
 *
 *   Relative  rejected iff an ACTIVE relative gate fails; hard limits are
 *             ignored entirely, even if values remain from another mode
 *   Absolute  rejected iff a CONFIGURED hard limit fails
 *   Both      rejected iff either fails
 */
Frames.verdict = function( metrics, gates, settings )
{
   if ( !Frames.frameValid( metrics ) )
      return { state: Frames.STATE.UNMEASURABLE,
               reasons: [ "a metric is missing or not positive" ] };

   var reasons = [];
   var useRelative = ( settings.mode != Frames.MODE.ABSOLUTE );
   var useAbsolute = ( settings.mode != Frames.MODE.RELATIVE );

   if ( useRelative )
      for ( var m = 0; m < Frames.METRICS.length; ++m )
      {
         var name = Frames.METRICS[m], g = gates[name];
         if ( g == null || !g.active )
            continue;
         var v = metrics[name];
         var bad = ( Frames.WORSE_WHEN[name] == "higher" ) ? v > g.limit : v < g.limit;
         if ( bad )
            reasons.push( Frames.METRIC_LABEL[name] + " " + Frames.round( v ) +
                          ", median " + Frames.round( g.median ) +
                          ", limit " + Frames.round( g.limit ) );
      }

   if ( useAbsolute )
      for ( var a = 0; a < Frames.METRICS.length; ++a )
      {
         var an = Frames.METRICS[a], lim = settings.limits[an];
         if ( lim == null )
            continue;
         if ( lim.hi != null && metrics[an] > lim.hi )
            reasons.push( Frames.METRIC_LABEL[an] + " " + Frames.round( metrics[an] ) +
                          " above the limit of " + Frames.round( lim.hi ) );
         if ( lim.lo != null && metrics[an] < lim.lo )
            reasons.push( Frames.METRIC_LABEL[an] + " " + Frames.round( metrics[an] ) +
                          " below the limit of " + Frames.round( lim.lo ) );
      }

   return { state: reasons.length ? Frames.STATE.REJECTED : Frames.STATE.APPROVED,
            reasons: reasons };
};

/* Three significant figures, so a reason reads as a sentence. */
Frames.round = function( v )
{
   if ( !isFinite( v ) )
      return String( v );
   var a = Math.abs( v );
   if ( a >= 100 ) return String( Math.round( v ) );
   if ( a >= 1 )   return v.toFixed( 2 );
   return v.toPrecision( 3 );
};
```

- [ ] **Step 4: Run to verify it passes**

```bash
node ci/run-tests.js
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add script/lib/Frames.js script/selftest.js
git commit -m "Decide a verdict, with the predicate stated per mode"
```

---

### Task 8: Grouping and comparability

**Files:**
- Modify: `script/lib/Frames.js`
- Modify: `script/selftest.js`

**Interfaces:**
- Consumes: nothing from earlier tasks
- Produces:
  - `Frames.groupByFilter( entries )` → `{ <filter>: [entry] }`, `entry` being
    `{ path, filter, exposure, binning, width, height, calibrated }`
  - `Frames.comparability( group )` → `{ uniform: Boolean, problems: [String] }`

- [ ] **Step 1: Write the failing tests**

```javascript
   ( function()
   {
      var e = [
         { path: "/m/h1.xisf", filter: "H", exposure: 180, binning: 1,
           width: 6248, height: 4176, calibrated: true },
         { path: "/m/h2.xisf", filter: "H", exposure: 180, binning: 1,
           width: 6248, height: 4176, calibrated: true },
         { path: "/m/o1.xisf", filter: "O", exposure: 180, binning: 1,
           width: 6248, height: 4176, calibrated: true },
         { path: "/m/x1.xisf", filter: "", exposure: 60, binning: 1,
           width: 6248, height: 4176, calibrated: true } ];
      var g = Frames.groupByFilter( e );
      check( "frames group by their filter", g.H.length, 2 );
      check( "each filter is its own group", g.O.length, 1 );
      /*
       * The RAW filter string, not Util.channelFromFilter, which maps to
       * Loom's seven canonical channels and returns null for anything else --
       * it would merge distinct filters and drop unfamiliar ones.
       */
      check( "an unreadable filter forms its own group",
             g[Frames.NO_FILTER].length, 1 );
      /*
       * And that group is never clipped. Without a filter there is no
       * evidence the frames belong together, so "this night's worst" is
       * meaningless over them -- they may be condemned by hand, never
       * automatically.
       */
      check( "a group with no filter is never auto-rejected",
             Frames.autoRejectAllowed( Frames.NO_FILTER ), false );
      check( "a real filter is", Frames.autoRejectAllowed( "H" ), true );

      check( "a uniform group is comparable",
             Frames.comparability( g.H ).uniform, true );

      var mixed = g.H.concat( [ { path: "/m/h3.xisf", filter: "H", exposure: 60,
                                  binning: 1, width: 6248, height: 4176,
                                  calibrated: true } ] );
      check( "mixed exposures are not", Frames.comparability( mixed ).uniform, false );
      check( "and the problem is named",
             Frames.comparability( mixed ).problems[0].indexOf( "exposure" ) >= 0, true );

      /*
       * Raw and calibrated frames of one filter can agree on exposure,
       * binning and geometry, so calibration state is part of the check.
       */
      var mixedCal = g.H.concat( [ { path: "/m/h4.xisf", filter: "H", exposure: 180,
                                     binning: 1, width: 6248, height: 4176,
                                     calibrated: false } ] );
      check( "mixed calibration state is not comparable either",
             Frames.comparability( mixedCal ).uniform, false );
   } )();
```

- [ ] **Step 2: Run to verify it fails**

```bash
node ci/run-tests.js
```

Expected: FAIL, `Frames.groupByFilter is not a function`.

- [ ] **Step 3: Implement**

```javascript
Frames.NO_FILTER = "(no filter)";

/*
 * Grouping is by the RAW FILTER string.
 *
 * Not Util.channelFromFilter: that maps to Loom's seven canonical channels
 * and returns null for anything else, so it would merge two distinct
 * filters into one group and silently drop unfamiliar ones. Here a group is
 * a filter, whatever it is called.
 *
 * Splitting further -- by exposure, by night -- is a judgement about
 * someone's data. This does not do it; it reports instead.
 */
Frames.groupByFilter = function( entries )
{
   var out = {};
   for ( var i = 0; i < entries.length; ++i )
   {
      var f = entries[i].filter;
      var key = ( f == null || String( f ).trim() == "" ) ? Frames.NO_FILTER
                                                          : String( f ).trim();
      if ( out[key] == null )
         out[key] = [];
      out[key].push( entries[i] );
   }
   return out;
};

/*
 * A group of one filter is not automatically comparable. A shorter exposure
 * legitimately loses on SNR and star count; a different binning makes pixel
 * FWHM incomparable; an uncalibrated frame among calibrated ones differs in
 * every metric. Any of those makes the channel's statistics meaningless, so
 * they are reported and Apply is blocked for that channel.
 */
/*
 * Frames with no readable FILTER are grouped so they can be SEEN, never so
 * they can be clipped: there is no evidence they belong together, so the
 * channel statistics that justify a deletion do not apply to them.
 */
Frames.autoRejectAllowed = function( filterKey )
{
   return filterKey != Frames.NO_FILTER;
};

Frames.comparability = function( group )
{
   var problems = [];
   function distinct( field )
   {
      var seen = {}, n = 0;
      for ( var i = 0; i < group.length; ++i )
      {
         var v = String( group[i][field] );
         if ( !seen[v] ) { seen[v] = true; ++n; }
      }
      return n;
   }
   if ( group == null || group.length == 0 )
      return { uniform: true, problems: [] };

   if ( distinct( "exposure" ) > 1 )
      problems.push( "mixed exposure times" );
   if ( distinct( "binning" ) > 1 || distinct( "binningY" ) > 1 )
      problems.push( "mixed binning" );
   if ( distinct( "imageType" ) > 1 )
      problems.push( "mixed image types" );
   if ( distinct( "width" ) > 1 || distinct( "height" ) > 1 )
      problems.push( "mixed image geometry" );
   if ( distinct( "calibrated" ) > 1 )
      problems.push( "mixed calibration state" );
   /*
    * An ENTIRELY unknown calibration state is not uniformity, it is an
    * absence of evidence. A group of raw and calibrated frames that all
    * report "unknown" would otherwise pass the guard and be clipped.
    */
   if ( group[0].calibrated == "unknown" && distinct( "calibrated" ) == 1 )
      problems.push( "calibration state unknown for every frame" );

   return { uniform: problems.length == 0, problems: problems };
};
```

- [ ] **Step 4: Run to verify it passes**

```bash
node ci/run-tests.js
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add script/lib/Frames.js script/selftest.js
git commit -m "Group by filter, and notice when a group is not comparable"
```

---

### Task 9: Manual overrides

The spec gives overrides their own rules — they outrank the formula, they are
counted separately, they survive a knob change — and every one of those is
arithmetic. None of it should wait for the dialog.

**Files:**
- Modify: `script/lib/Frames.js`
- Modify: `script/selftest.js`

**Interfaces:**
- Consumes: `Frames.STATE`, `Frames.verdict`
- Produces:
  - `Frames.OVERRIDE = { RESCUED: "rescued", CONDEMNED: "condemned" }`
  - `Frames.finalState( verdictState, override )` → a `Frames.STATE`
  - `Frames.counts( rows )` → `{ total, kept, rejected, unmeasurable, rescued, condemned }`
  - `Frames.summaryLine( channel, counts )` → String

- [ ] **Step 1: Write the failing tests**

```javascript
   /*
    * An override is explicit and outranks the formula: somebody looked at the
    * frame at 1:1 and the formula did not.
    */
   check( "a rescued frame is kept even though the formula rejected it",
          Frames.finalState( Frames.STATE.REJECTED, Frames.OVERRIDE.RESCUED ),
          Frames.STATE.APPROVED );
   check( "a condemned frame is dropped even though the formula kept it",
          Frames.finalState( Frames.STATE.APPROVED, Frames.OVERRIDE.CONDEMNED ),
          Frames.STATE.REJECTED );
   check( "no override leaves the verdict alone",
          Frames.finalState( Frames.STATE.REJECTED, null ),
          Frames.STATE.REJECTED );
   /*
    * An unmeasurable frame can be condemned by hand but cannot be "approved":
    * there is no measurement to approve.
    */
   check( "an unmeasurable frame can be condemned",
          Frames.finalState( Frames.STATE.UNMEASURABLE, Frames.OVERRIDE.CONDEMNED ),
          Frames.STATE.REJECTED );
   check( "but rescuing one leaves it unmeasurable",
          Frames.finalState( Frames.STATE.UNMEASURABLE, Frames.OVERRIDE.RESCUED ),
          Frames.STATE.UNMEASURABLE );

   ( function()
   {
      var rows = [
         { state: Frames.STATE.APPROVED,     override: null },
         { state: Frames.STATE.APPROVED,     override: null },
         { state: Frames.STATE.REJECTED,     override: Frames.OVERRIDE.RESCUED },
         { state: Frames.STATE.REJECTED,     override: null },
         { state: Frames.STATE.APPROVED,     override: Frames.OVERRIDE.CONDEMNED },
         { state: Frames.STATE.UNMEASURABLE, override: null } ];
      var c = Frames.counts( rows );
      check( "kept counts the rescued frame", c.kept, 3 );
      check( "rejected counts the condemned one", c.rejected, 2 );
      check( "the unmeasurable frame is neither", c.unmeasurable, 1 );
      /*
       * Hand edits are counted separately so a summary never hides one.
       */
      check( "rescues are reported", c.rescued, 1 );
      check( "and condemnations", c.condemned, 1 );
      check( "the summary names both",
             Frames.summaryLine( "H", c ),
             "H: 3 kept of 6 (+1 rescued, -1 condemned, 1 unmeasurable)" );
   } )();
```

- [ ] **Step 2: Run to verify it fails**

```bash
node ci/run-tests.js
```

Expected: FAIL, `Frames.finalState is not a function`.

- [ ] **Step 3: Implement**

```javascript
/*
 * A hand decision, which OUTRANKS the formula: somebody looked at the frame
 * and the formula did not. Changing k or a weight recomputes every verdict
 * and leaves overrides standing.
 *
 * Session only. Persisting them would put a second source of truth on disk
 * to silently contradict the formula next time.
 */
Frames.OVERRIDE = { RESCUED: "rescued", CONDEMNED: "condemned" };

Frames.finalState = function( verdictState, override )
{
   /*
    * An unmeasurable frame may be condemned by hand but never approved:
    * there is no measurement behind an approval, and "approved" would put it
    * in the kept set on the strength of nothing.
    */
   if ( verdictState == Frames.STATE.UNMEASURABLE )
      return ( override == Frames.OVERRIDE.CONDEMNED ) ? Frames.STATE.REJECTED
                                                        : Frames.STATE.UNMEASURABLE;
   if ( override == Frames.OVERRIDE.RESCUED )
      return Frames.STATE.APPROVED;
   if ( override == Frames.OVERRIDE.CONDEMNED )
      return Frames.STATE.REJECTED;
   return verdictState;
};

Frames.counts = function( rows )
{
   var c = { total: rows.length, kept: 0, rejected: 0, unmeasurable: 0,
             rescued: 0, condemned: 0 };
   for ( var i = 0; i < rows.length; ++i )
   {
      var r = rows[i];
      var final = Frames.finalState( r.state, r.override );
      if ( final == Frames.STATE.APPROVED )          ++c.kept;
      else if ( final == Frames.STATE.REJECTED )     ++c.rejected;
      else                                           ++c.unmeasurable;
      if ( r.override == Frames.OVERRIDE.RESCUED )   ++c.rescued;
      if ( r.override == Frames.OVERRIDE.CONDEMNED ) ++c.condemned;
   }
   return c;
};

/* Hand edits are named, so a summary never hides one. */
Frames.summaryLine = function( channel, c )
{
   var extra = [];
   if ( c.rescued )      extra.push( "+" + c.rescued + " rescued" );
   if ( c.condemned )    extra.push( "-" + c.condemned + " condemned" );
   if ( c.unmeasurable ) extra.push( c.unmeasurable + " unmeasurable" );
   return channel + ": " + c.kept + " kept of " + c.total +
          ( extra.length ? " (" + extra.join( ", " ) + ")" : "" );
};
```

- [ ] **Step 4: Run to verify it passes**

```bash
node ci/run-tests.js
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add script/lib/Frames.js script/selftest.js
git commit -m "Let a hand decision outrank the formula, and count it separately"
```

---

### Task 10: The manifest and its lifecycle

**Files:**
- Modify: `script/lib/Frames.js`
- Modify: `script/selftest.js`

**Interfaces:**
- Consumes: `Frames.STATE`, `Frames.finalState` (Task 9 — the manifest reads
  the FINAL state, so overrides must already exist)
- Produces:
  - `Frames.PHASE = { REVIEW: "review", EXECUTING: "executing", STOPPED: "stopped", DONE: "done" }`
  - `Frames.canEdit( phase )` → Boolean
  - `Frames.nextPhase( phase, event )` → a phase, or null when the transition
    is not allowed
  - `Frames.buildManifest( rows )` → `{ entries: [...], created: Number }`
  - `Frames.manifestPending( manifest )` → `[entry]`
  - `Frames.recordOutcome( manifest, path, outcome, detail )` → mutates in place
  - `Frames.identityMatches( entry, current )` → Boolean

- [ ] **Step 1: Write the failing tests**

```javascript
   ( function()
   {
      var rows = [
         { path: "/m/a.xisf", state: Frames.STATE.REJECTED, reasons: [ "FWHM" ],
           digest: "aaa", size: 100, mtime: 5 },
         { path: "/m/b.xisf", state: Frames.STATE.APPROVED, reasons: [],
           digest: "bbb", size: 100, mtime: 5 },
         { path: "/m/c.xisf", state: Frames.STATE.UNMEASURABLE, reasons: [],
           digest: "ccc", size: 100, mtime: 5 } ];
      var man = Frames.buildManifest( rows );
      /*
       * Only rejected frames are actionable. An unmeasurable frame is never
       * deleted automatically -- there is no measurement behind the verdict.
       */
      check( "only rejected frames enter the manifest", man.entries.length, 1 );
      check( "and it is the rejected one", man.entries[0].path, "/m/a.xisf" );
      check( "carrying the digest taken at measurement",
             man.entries[0].digest, "aaa" );

      /*
    * The lifecycle, as a state machine rather than a paragraph. The review
    * is editable only in REVIEW: otherwise a knob change could alter the
    * decisions of a manifest that is already deleting files.
    */
   check( "the review starts editable", Frames.canEdit( Frames.PHASE.REVIEW ), true );
   check( "and is locked while executing",
          Frames.canEdit( Frames.PHASE.EXECUTING ), false );
   check( "a stopped run is still locked",
          Frames.canEdit( Frames.PHASE.STOPPED ), false );
   check( "committing enters execution",
          Frames.nextPhase( Frames.PHASE.REVIEW, "commit" ), Frames.PHASE.EXECUTING );
   check( "a stopped run may be resumed",
          Frames.nextPhase( Frames.PHASE.STOPPED, "resume" ), Frames.PHASE.EXECUTING );
   check( "or abandoned, which is the only way back to editing",
          Frames.nextPhase( Frames.PHASE.STOPPED, "abandon" ), Frames.PHASE.REVIEW );
   /*
    * There is no edit transition out of EXECUTING, and no second commit:
    * pressing Apply twice must not run two manifests over one cohort.
    */
   check( "a second commit while executing is refused",
          Frames.nextPhase( Frames.PHASE.EXECUTING, "commit" ), null );

   check( "everything is pending before execution",
             Frames.manifestPending( man ).length, 1 );
      Frames.recordOutcome( man, "/m/a.xisf", "deleted", "" );
      check( "a recorded outcome is no longer pending",
             Frames.manifestPending( man ).length, 0 );
      /*
       * Resuming skips what is done. Recomputing instead would be iterative
       * clipping: remove the worst frame and the survivors' MAD tightens, so
       * the next pass takes the next-worst -- a frame nobody condemned.
       */
      Frames.recordOutcome( man, "/m/a.xisf", "deleted", "" );
      check( "recording twice does not duplicate the entry", man.entries.length, 1 );
   } )();

   ( function()
   {
      var e = { path: "/m/a.xisf", digest: "aaa", size: 100, mtime: 5 };
      check( "identity matches when the digest does",
             Frames.identityMatches( e, { digest: "aaa", size: 100, mtime: 5 } ), true );
      /*
       * Path, size and mtime are all preservable by a replacement. The
       * digest is what authorises a deletion.
       */
      check( "and fails when only the digest changed",
             Frames.identityMatches( e, { digest: "zzz", size: 100, mtime: 5 } ), false );
      check( "a missing current file never matches",
             Frames.identityMatches( e, null ), false );
   } )();
```

- [ ] **Step 2: Run to verify it fails**

```bash
node ci/run-tests.js
```

Expected: FAIL, `Frames.buildManifest is not a function`.

- [ ] **Step 3: Implement**

```javascript
/*
 * The execution manifest: a copy of the review taken when Apply is
 * confirmed, immutable thereafter.
 *
 * Three layers keep this honest. The COHORT is the frames and their
 * measurements, fixed at scan time -- channel medians always come from it,
 * never from the survivors of a partial run. The REVIEW is verdicts, knobs
 * and overrides, freely editable and worth nothing until committed. The
 * MANIFEST is what executes.
 *
 * Without that separation, retrying after a partial run recomputes the
 * statistics over the survivors, which is iterative clipping: the MAD
 * tightens with every deletion and the next pass condemns a frame nobody
 * looked at.
 */
/*
 * The execution lifecycle. Written as a table because the prose version of
 * this ("decisions are frozen") did not say what happens when someone edits
 * a knob after a partial run, and that is exactly the case that would
 * reintroduce iterative clipping.
 */
Frames.PHASE = { REVIEW: "review", EXECUTING: "executing",
                 STOPPED: "stopped", DONE: "done" };

Frames.canEdit = function( phase ) { return phase == Frames.PHASE.REVIEW; };

Frames.nextPhase = function( phase, event )
{
   var T = {};
   T[Frames.PHASE.REVIEW]    = { commit: Frames.PHASE.EXECUTING };
   T[Frames.PHASE.EXECUTING] = { stop: Frames.PHASE.STOPPED,
                                 finish: Frames.PHASE.DONE };
   T[Frames.PHASE.STOPPED]   = { resume: Frames.PHASE.EXECUTING,
                                 abandon: Frames.PHASE.REVIEW };
   T[Frames.PHASE.DONE]      = { review: Frames.PHASE.REVIEW };
   var row = T[phase] || {};
   return ( row[event] != null ) ? row[event] : null;
};

Frames.buildManifest = function( rows )
{
   var entries = [];
   for ( var i = 0; i < rows.length; ++i )
   {
      var r = rows[i];
      /*
       * The FINAL state, so a rescued frame is not deleted and a condemned
       * one is. Reading r.state here instead would silently ignore every
       * hand decision.
       */
      if ( Frames.finalState( r.state, r.override ) != Frames.STATE.REJECTED )
         continue;
      /*
       * A SNAPSHOT, deep-copied. Sharing the review's reasons array would
       * let a later edit change what the "frozen" manifest says, and the
       * audit record must survive the review being edited.
       *
       * The automatic verdict and the override are both recorded: if a
       * rescued frame later turns out to have been the bad one, the log
       * says who chose it.
       */
      entries.push( { path: r.path, channel: r.channel || "",
                      digest: r.digest, size: r.size, mtime: r.mtime,
                      autoVerdict: r.state,
                      override: r.override || null,
                      reasons: ( r.reasons || [] ).slice(),
                      outcome: null, detail: "" } );
   }
   return { entries: entries, created: Date.now() };
};

Frames.manifestPending = function( manifest )
{
   var out = [];
   for ( var i = 0; i < manifest.entries.length; ++i )
      if ( manifest.entries[i].outcome == null )
         out.push( manifest.entries[i] );
   return out;
};

Frames.recordOutcome = function( manifest, path, outcome, detail )
{
   for ( var i = 0; i < manifest.entries.length; ++i )
      if ( manifest.entries[i].path == path )
      {
         manifest.entries[i].outcome = outcome;
         manifest.entries[i].detail = detail || "";
         return;
      }
};

/*
 * Identity is the digest. Path, size and modification time are all
 * preservable by a replacement, so none of them authorises a deletion.
 *
 * This does NOT close the window between the check and the unlink -- nothing
 * in PJSR locks a file. It narrows it to microseconds; the audit log is what
 * survives if something slips through.
 */
Frames.identityMatches = function( entry, current )
{
   return current != null && entry != null &&
          current.digest === entry.digest &&
          current.size === entry.size;
};
```

- [ ] **Step 4: Run to verify it passes**

```bash
node ci/run-tests.js
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add script/lib/Frames.js script/selftest.js
git commit -m "Freeze decisions in a manifest so a retry cannot clip twice"
```

---

### Task 11: The SubframeSelector driver

**Files:**
- Create: `script/FrameSelector.js`
- Modify: `script/selftest.js`

**Interfaces:**
- Consumes: `Frames.COL`, `Frames.metricsFromRow`, `Frames.metricInRange`
- Produces:
  - `FrameSelector.measure( paths )` → `{ <path>: metrics }` with missing paths
    absent, or **`null`** when the channel must be abandoned (an unexpected or
    duplicated result path)
  - `FrameSelector.MEASURE_ROUTINE = 0`

- [ ] **Step 1: Write the failing test**

Inside the `IN_PIXINSIGHT` block in `script/selftest.js`:

```javascript
      /*
       * Measure a real frame and check each column still MEANS what it is
       * read as. Pinning the constants cannot do this -- Frames.COL.psfSNR
       * === 28 passes unchanged after the process reorders its table -- so
       * the ranges are checked instead, and they do not overlap.
       */
      /*
       * A missing fixture must FAIL, not skip. An assertion that silently
       * disappears when a path is absent is how column-meaning coverage
       * evaporates on another machine.
       */
      var fixture = Steps.firstExistingPath( [
         "/Volumes/A008/Elephant Trunk/master/" +
         "masterLight_BIN-1_6248x4176_EXPOSURE-60.00s_FILTER-R_mono_drizzle_2x_(1)_autocrop.xisf" ] );
      check( "the measurement fixture is present", fixture != null, true );
      if ( fixture != null )
      {
         var measured = FrameSelector.measure( [ fixture ] );
         var m = measured[fixture];
         check( "a frame measures", m != null, true );
         if ( m != null )
         {
            check( "FWHM is in a plausible range for an FWHM",
                   Frames.metricInRange( "fwhm", m.fwhm ), true );
            check( "the star count is in a plausible range for a star count",
                   Frames.metricInRange( "stars", m.stars ), true );
            check( "PSF SNR is not the zero column 8 returns",
                   Frames.metricInRange( "psfSNR", m.psfSNR ), true );
            check( "eccentricity is a fraction",
                   Frames.metricInRange( "eccentricity", m.eccentricity ), true );
            /*
             * And prove the check can fail: put the star count where PSF SNR
             * is read from and the range test must reject it. Without this
             * the four assertions above could all pass on ranges too loose
             * to discriminate.
             */
            check( "a star count in the PSF SNR slot is rejected",
                   Frames.metricInRange( "psfSNR", m.stars ), false );
            check( "and an FWHM in the star slot",
                   Frames.metricInRange( "stars", m.fwhm ), false );
         }
      }
```

- [ ] **Step 2: Run to verify it fails**

```bash
rm -f /tmp/agent-scratch/lhso-selftest.txt
/Applications/PixInsight/PixInsight.app/Contents/MacOS/PixInsight \
  -x=1:/Users/francescocarucci/PixInsight/scripts/Loom/script/selftest.js
until [ -f /tmp/agent-scratch/lhso-selftest.txt ]; do sleep 5; done
head -4 /tmp/agent-scratch/lhso-selftest.txt
```

Expected: FAIL, `FrameSelector is not defined`.

- [ ] **Step 3: Implement**

Create `script/FrameSelector.js`:

```javascript
#engine v8

#feature-id    Loom Frame Selector : Batch Processing > Loom Frame Selector
#feature-info  Measures every subframe in a folder, groups them by filter, and \
               removes the ones this night's own statistics condemn.

#include <pjsr/UndoFlag.jsh>
#include <pjsr/DataType.jsh>
#include <pjsr/StdButton.jsh>
#include <pjsr/StdIcon.jsh>
#include <pjsr/CryptographicHash.jsh>

#include "lib/Util.js"
#include "lib/Cache.js"
#include "lib/Psb.js"
#include "lib/Steps.js"
#include "lib/Frames.js"

function FrameSelector() {}

/*
 * routine 0 measures. 1 and 2 are the preview and output routines and refuse
 * with "No measurements have been made" -- verified by executing all three.
 */
FrameSelector.MEASURE_ROUTINE = 0;

/*
 * Measure a list of frames in ONE SubframeSelector call.
 *
 * Results are matched back to inputs by the path in the measurement row,
 * never by position, exactly as WBPP's own analyzer does
 * (BPP-SubframeAnalyzer.js:520). If one frame fails to measure, positional
 * reading shifts every later row onto the wrong file -- and a perfect
 * deletion fingerprint would then verify the wrong frame's identity against
 * another frame's metrics.
 *
 * A path with no result row is simply absent from the returned map; the
 * caller turns that into an unmeasurable entry.
 */
/*
 * ONE place that configures the process, so the settings that produce a
 * measurement and the settings that key its cache entry cannot drift apart.
 */
FrameSelector.newMeasureProcess = function()
{
   var P = new SubframeSelector;
   P.routine = FrameSelector.MEASURE_ROUTINE;
   P.nonInteractive = true;
   P.subframeScale = 1;
   P.scaleUnit = 0;
   return P;
};

FrameSelector.measure = function( paths )
{
   var out = {};
   if ( paths == null || paths.length == 0 )
      return out;

   var P = FrameSelector.newMeasureProcess();
   var rows = [];
   for ( var i = 0; i < paths.length; ++i )
      rows.push( [ true, paths[i], "", "" ] );   // four values per row, checked
   P.subframes = rows;

   if ( !P.executeGlobal() )
   {
      Util.warn( "frames", "SubframeSelector failed on " + paths.length + " frame(s)" );
      return out;
   }
   if ( P.measurements == null )
      return out;

   /*
    * A result path must be one we ASKED for. Checking only for duplicates
    * lets an unexpected path through, and an empty one be silently dropped
    * -- either way the channel's statistics would be computed over a set
    * that is not the set on screen.
    */
   var asked = {};
   for ( var a = 0; a < paths.length; ++a )
      asked[paths[a]] = true;

   for ( var r = 0; r < P.measurements.length; ++r )
   {
      var m = Frames.metricsFromRow( P.measurements[r] );
      if ( m.path == null || m.path === "" || !asked[m.path] )
      {
         Util.error( "frames", "SubframeSelector returned an unexpected path (" +
                               m.path + "); abandoning this channel" );
         return null;                    // null = the channel failed
      }
      if ( out[m.path] != null )
      {
         Util.error( "frames", "two measurements for " + m.path +
                               "; abandoning this channel" );
         return null;
      }
      out[m.path] = m;
   }
   return out;
};
```

- [ ] **Step 4: Run to verify it passes**

```bash
rm -f /tmp/agent-scratch/lhso-selftest.txt
/Applications/PixInsight/PixInsight.app/Contents/MacOS/PixInsight \
  -x=1:/Users/francescocarucci/PixInsight/scripts/Loom/script/selftest.js
until [ -f /tmp/agent-scratch/lhso-selftest.txt ]; do sleep 5; done
head -4 /tmp/agent-scratch/lhso-selftest.txt
node ci/run-tests.js
```

Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add script/FrameSelector.js script/selftest.js
git commit -m "Measure frames, matching results to inputs by path"
```

---

### Task 12: Digests, the cache, and reading headers

**Files:**
- Modify: `script/FrameSelector.js`
- Modify: `script/selftest.js`

**Interfaces:**
- Consumes: `Frames.MEASURE_VERSION`, `Cache.dir()`
- Produces:
  - `FrameSelector.digest( path )` → String, or null
  - `FrameSelector.fileIdentity( path )` → `{ digest, size, mtime }` or null
  - `FrameSelector.entryFor( path )` → `{ path, filter, exposure, binning, width, height, calibrated }`
  - `FrameSelector.cachedMeasurement( path )` / `FrameSelector.storeMeasurement( path, metrics )`

- [ ] **Step 1: Write the failing test**

Inside `IN_PIXINSIGHT`:

```javascript
      ( function()
      {
         var tmp = "/tmp/agent-scratch/digest-test.txt";
         File.writeTextFile( tmp, "one" );
         var a = FrameSelector.digest( tmp );
         check( "a digest is produced", typeof a, "string" );
         check( "the same bytes give the same digest",
                FrameSelector.digest( tmp ), a );
         File.writeTextFile( tmp, "two" );
         /*
          * Same path, same length. Only the CONTENT changed -- which is
          * exactly the replacement a path/size/mtime check cannot see.
          */
         check( "different bytes of the same length give a different digest",
                FrameSelector.digest( tmp ) != a, true );
         File.remove( tmp );
         check( "a missing file has no digest",
                FrameSelector.digest( "/tmp/agent-scratch/not-there.xisf" ), null );
      } )();
```

- [ ] **Step 2: Run to verify it fails**

```bash
rm -f /tmp/agent-scratch/lhso-selftest.txt
/Applications/PixInsight/PixInsight.app/Contents/MacOS/PixInsight \
  -x=1:/Users/francescocarucci/PixInsight/scripts/Loom/script/selftest.js
until [ -f /tmp/agent-scratch/lhso-selftest.txt ]; do sleep 5; done
head -4 /tmp/agent-scratch/lhso-selftest.txt
```

Expected: FAIL, `FrameSelector.digest is not a function`.

- [ ] **Step 3: Implement**

Append to `script/FrameSelector.js`:

```javascript
/*
 * A digest of the WHOLE file.
 *
 * Not the header and a sample of pixels: a change outside the sampled
 * region passes a partial hash without any collision, and this is the value
 * that authorises deleting someone's data.
 */
FrameSelector.digest = function( path )
{
   try
   {
      if ( !File.exists( path ) )
         return null;
      var f = new File;
      f.openForReading( path );
      var bytes = f.read( DataType.ByteArray, f.size );
      f.close();
      return ( new CryptographicHash( CryptographicHash.SHA1 ) ).hash( bytes ).toHex();
   }
   catch ( e )
   {
      Util.warn( "frames", "could not digest " + path + ": " + e );
      return null;
   }
};

FrameSelector.fileIdentity = function( path )
{
   try
   {
      if ( !File.exists( path ) )
         return null;
      var fi = new FileInfo( path );
      var t = fi.lastModified;
      var d = FrameSelector.digest( path );
      if ( d == null )
         return null;
      return { digest: d, size: fi.size, mtime: t ? t.getTime() : 0 };
   }
   catch ( e ) { return null; }
};

/*
 * The header fields the comparability check needs. Calibration state is one
 * of them: raw and calibrated frames of one filter can agree on exposure,
 * binning and geometry, and an uncalibrated frame among calibrated ones
 * differs in every measured metric.
 */
FrameSelector.entryFor = function( path )
{
   var info = null;
   try { info = Pipeline.readImageInfo( path ); } catch ( e ) { info = null; }
   var kw = info ? info.keywords : null;
   var hist = ( info && info.keywords ) ? Util.keywordValue( kw, "HISTORY" ) : null;
   return { path: path,
            filter:   kw ? Util.keywordValue( kw, "FILTER" ) : null,
            exposure: kw ? Util.keywordValue( kw, "EXPTIME" ) : null,
            binning:  kw ? Util.keywordValue( kw, "XBINNING" ) : null,
            width:    info ? info.width : 0,
            height:   info ? info.height : 0,
            /*
             * WBPP's calibrated frames carry a _c suffix and a calibration
             * history. Unknown state counts as its own value, so a mixture
             * of known and unknown is reported rather than assumed uniform.
             */
            calibrated: /_c(_[0-9]+)?\.[a-z]+$/i.test( path ) ? "yes"
                        : ( hist ? "history" : "unknown" ) };
};

FrameSelector.cachePath = function()
{
   return Cache.dir() + "/frame-quality.json";
};

FrameSelector.table = null;

FrameSelector.loadTable = function()
{
   if ( FrameSelector.table != null )
      return FrameSelector.table;
   FrameSelector.table = {};
   try
   {
      var p = FrameSelector.cachePath();
      if ( File.exists( p ) )
         FrameSelector.table = JSON.parse( File.readTextFile( p ) ) || {};
   }
   catch ( e ) { FrameSelector.table = {}; }
   return FrameSelector.table;
};

FrameSelector.saveTable = function()
{
   try
   {
      Cache.ensureDir();
      File.writeTextFile( FrameSelector.cachePath(),
                          JSON.stringify( FrameSelector.table || {} ) );
   }
   catch ( e ) { Util.warn( "frames", "could not save measurements: " + e ); }
};

/*
 * A cached measurement is keyed by the DIGEST, not the path, and carries the
 * measurement version. A cached number whose digest no longer matches the
 * file is not reused -- the numbers that produced a verdict always describe
 * the bytes that verdict will be applied to.
 */
/*
 * The configuration is part of the key, not just the bytes.
 *
 * A measurement taken under different SubframeSelector settings is not
 * comparable with a fresh one, and WBPP folds SS.toSource() into its own
 * measurement cache for exactly this reason. Without it, changing a setting
 * silently reuses numbers taken under the old one.
 */
FrameSelector.configSignature = function()
{
   var P = FrameSelector.newMeasureProcess();
   return ( new CryptographicHash( CryptographicHash.SHA1 ) )
             .hash( ByteArray.stringToUTF8( P.toSource() ) ).toHex().substring( 0, 12 );
};

FrameSelector.measurementKey = function( identity )
{
   return Frames.MEASURE_VERSION + "|" + FrameSelector.configSignature() +
          "|" + identity.digest;
};

FrameSelector.cachedMeasurement = function( identity )
{
   var t = FrameSelector.loadTable();
   return t[FrameSelector.measurementKey( identity )] || null;
};

FrameSelector.storeMeasurement = function( identity, metrics )
{
   var t = FrameSelector.loadTable();
   t[FrameSelector.measurementKey( identity )] = metrics;
   FrameSelector.saveTable();
};
```

Also add `#include "lib/Pipeline.js"` to the include block, after `lib/Steps.js`,
since `entryFor` uses `Pipeline.readImageInfo`.

- [ ] **Step 4: Run to verify it passes**

```bash
rm -f /tmp/agent-scratch/lhso-selftest.txt
/Applications/PixInsight/PixInsight.app/Contents/MacOS/PixInsight \
  -x=1:/Users/francescocarucci/PixInsight/scripts/Loom/script/selftest.js
until [ -f /tmp/agent-scratch/lhso-selftest.txt ]; do sleep 5; done
head -4 /tmp/agent-scratch/lhso-selftest.txt
node ci/run-tests.js
```

Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add script/FrameSelector.js script/selftest.js
git commit -m "Key measurements by a whole-file digest, not by path"
```

---

### Task 13: The scan, binding measurements to the bytes measured

Without this, nothing guarantees that the metrics behind a verdict describe the
file the verdict is applied to. Measure A, have it replaced by B, fingerprint B,
and the manifest would authorise deleting B on A's numbers.

**Files:**
- Modify: `script/FrameSelector.js`
- Modify: `script/selftest.js`

**Interfaces:**
- Consumes: `FrameSelector.measure`, `FrameSelector.fileIdentity`,
  `FrameSelector.cachedMeasurement`, `Frames.groupByFilter`
- Produces:
  - `FrameSelector.scan( folder )` → `{ channels: { <filter>: { entries, metrics, problems } }, unstable: [path] }`

- [ ] **Step 1: Write the failing test**

Inside `IN_PIXINSIGHT`, using copies in scratch:

```javascript
      ( function()
      {
         var dir = "/tmp/agent-scratch/fs-scan-test";
         if ( !File.directoryExists( dir ) )
            File.createDirectory( dir, true );
         var p = dir + "/unstable.txt";
         File.writeTextFile( p, "first" );
         var before = FrameSelector.fileIdentity( p );
         File.writeTextFile( p, "secnd" );        // same length, new content
         var after = FrameSelector.fileIdentity( p );
         /*
          * The identity check the scan relies on must notice a replacement
          * that preserves the length -- which is the only kind that matters,
          * because a size change would be caught anyway.
          */
         check( "a same-length replacement changes identity",
                before.digest != after.digest, true );
         File.remove( p );
      } )();
```

- [ ] **Step 2: Run to verify it fails**

```bash
rm -f /tmp/agent-scratch/lhso-selftest.txt
/Applications/PixInsight/PixInsight.app/Contents/MacOS/PixInsight \
  -x=1:/Users/francescocarucci/PixInsight/scripts/Loom/script/selftest.js
until [ -f /tmp/agent-scratch/lhso-selftest.txt ]; do sleep 5; done
head -4 /tmp/agent-scratch/lhso-selftest.txt
```

Expected: FAIL until Task 12's `fileIdentity` is present; if Task 12 is done it
passes immediately and the real work is Step 3.

- [ ] **Step 3: Implement the scan**

```javascript
/*
 * Fingerprint, measure, fingerprint again.
 *
 * A frame whose identity differs across the measurement is UNSTABLE: something
 * rewrote it while we were reading it, so its numbers describe bytes that are
 * no longer there. It is excluded from the cohort entirely rather than being
 * shown with numbers that cannot be acted on.
 */
FrameSelector.scan = function( folder )
{
   var paths = [], find = new FileFind;
   if ( find.begin( folder + "/*" ) )
      do
      {
         if ( find.isFile && /\.(xisf|fits?|fit)$/i.test( find.name ) )
            paths.push( folder + "/" + find.name );
      }
      while ( find.next() );

   var before = {}, entries = [];
   for ( var i = 0; i < paths.length; ++i )
   {
      var id = FrameSelector.fileIdentity( paths[i] );
      if ( id == null )
         continue;
      before[paths[i]] = id;
      var e = FrameSelector.entryFor( paths[i] );
      e.identity = id;
      entries.push( e );
   }

   var groups = Frames.groupByFilter( entries );
   var channels = {}, unstable = [];

   var keys = Object.keys( groups );
   for ( var g = 0; g < keys.length; ++g )
   {
      var group = groups[keys[g]], need = [], metrics = {};
      for ( var j = 0; j < group.length; ++j )
      {
         var cached = FrameSelector.cachedMeasurement( group[j].identity );
         if ( cached != null )
            metrics[group[j].path] = cached;
         else
            need.push( group[j].path );
      }
      if ( need.length > 0 )
      {
         var measured = FrameSelector.measure( need );
         if ( measured == null )          // the channel was abandoned
         {
            channels[keys[g]] = { entries: group, metrics: {},
                                  problems: [ "measurement failed" ] };
            continue;
         }
         for ( var k = 0; k < need.length; ++k )
         {
            var path = need[k];
            var now = FrameSelector.fileIdentity( path );
            if ( now == null || now.digest != before[path].digest )
            {
               unstable.push( path );
               continue;                  // measured bytes are gone
            }
            if ( measured[path] != null )
            {
               /* stored without the path: identical bytes elsewhere must not
                  come back naming the first file */
               var m = measured[path];
               var stored = { fwhm: m.fwhm, eccentricity: m.eccentricity,
                              noise: m.noise, stars: m.stars, psfSNR: m.psfSNR };
               FrameSelector.storeMeasurement( before[path], stored );
               metrics[path] = stored;
            }
         }
      }
      var cmp = Frames.comparability( group );
      channels[keys[g]] = { entries: group, metrics: metrics,
                            problems: cmp.problems };
   }
   return { channels: channels, unstable: unstable };
};
```

- [ ] **Step 4: Run both suites**

```bash
node ci/run-tests.js
rm -f /tmp/agent-scratch/lhso-selftest.txt
/Applications/PixInsight/PixInsight.app/Contents/MacOS/PixInsight \
  -x=1:/Users/francescocarucci/PixInsight/scripts/Loom/script/selftest.js
until [ -f /tmp/agent-scratch/lhso-selftest.txt ]; do sleep 5; done
head -4 /tmp/agent-scratch/lhso-selftest.txt
```

Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add script/FrameSelector.js script/selftest.js
git commit -m "Scan by fingerprinting around the measurement"
```

---

### Task 14: Deleting, with an audit log that precedes the unlink

**Files:**
- Modify: `script/FrameSelector.js`
- Modify: `script/selftest.js`

**Interfaces:**
- Consumes: `Frames.manifestPending`, `Frames.identityMatches`, `Frames.recordOutcome`, `FrameSelector.fileIdentity`
- Produces:
  - `FrameSelector.logDir()` → String
  - `FrameSelector.writeManifestLog( manifest, path )` → Boolean
  - `FrameSelector.execute( manifest )` → `{ deleted, skipped, failed }`

- [ ] **Step 1: Write the failing test**

Inside `IN_PIXINSIGHT`. It operates on **copies in scratch**, never on real subs:

```javascript
      ( function()
      {
         var dir = "/tmp/agent-scratch/fs-delete-test";
         if ( !File.directoryExists( dir ) )
            File.createDirectory( dir, true );
         var keep = dir + "/keep.txt", drop = dir + "/drop.txt",
             swapped = dir + "/swapped.txt";
         File.writeTextFile( keep, "keep" );
         File.writeTextFile( drop, "drop" );
         File.writeTextFile( swapped, "before" );

         var man = Frames.buildManifest( [
            { path: drop, state: Frames.STATE.REJECTED, reasons: [ "test" ],
              digest: FrameSelector.digest( drop ),
              size: ( new FileInfo( drop ) ).size, mtime: 0 },
            { path: swapped, state: Frames.STATE.REJECTED, reasons: [ "test" ],
              digest: FrameSelector.digest( swapped ),
              size: ( new FileInfo( swapped ) ).size, mtime: 0 } ] );

         /*
          * Replace one file after the manifest was built, with content of the
          * same length. Path, size and mtime all still match; only the
          * digest does not. It must survive.
          */
         File.writeTextFile( swapped, "after!" );

         var result = FrameSelector.execute( man );
         check( "the condemned file is gone", File.exists( drop ), false );
         check( "the replaced file is NOT deleted", File.exists( swapped ), true );
         check( "and is reported as skipped", result.skipped, 1 );
         check( "the untouched file is untouched", File.exists( keep ), true );

         /*
          * Resuming must not act twice, and must not recompute anything.
          */
         var again = FrameSelector.execute( man );
         check( "a second execute does nothing new", again.deleted, 0 );

         File.remove( keep ); File.remove( swapped );
      } )();
```

- [ ] **Step 2: Run to verify it fails**

```bash
rm -f /tmp/agent-scratch/lhso-selftest.txt
/Applications/PixInsight/PixInsight.app/Contents/MacOS/PixInsight \
  -x=1:/Users/francescocarucci/PixInsight/scripts/Loom/script/selftest.js
until [ -f /tmp/agent-scratch/lhso-selftest.txt ]; do sleep 5; done
head -4 /tmp/agent-scratch/lhso-selftest.txt
```

Expected: FAIL, `FrameSelector.execute is not a function`.

- [ ] **Step 3: Implement**

```javascript
/*
 * The audit log does NOT live where Cache.clear can remove it -- that
 * deletes every top-level file in the cache directory -- and not under the
 * system temporary directory, which is not durable.
 */
FrameSelector.logDir = function()
{
   return File.homeDirectory + "/PixInsight/Loom-frame-selector";
};

FrameSelector.writeManifestLog = function( manifest, path )
{
   try
   {
      var dir = File.extractDirectory( path );
      if ( !File.directoryExists( dir ) )
         File.createDirectory( dir, true );
      var lines = [ "# Loom Frame Selector",
                    "# built " + ( new Date( manifest.created ) ).toISOString(),
                    "# " + manifest.entries.length + " frame(s) condemned" ];
      for ( var i = 0; i < manifest.entries.length; ++i )
      {
         var e = manifest.entries[i];
         lines.push( e.path + "\t" + e.digest + "\t" + e.reasons.join( "; " ) );
      }
      File.writeTextFile( path, lines.join( "\n" ) + "\n" );
      return true;
   }
   catch ( e )
   {
      Util.error( "frames", "could not write the deletion log: " + e );
      return false;
   }
};

/*
 * Execute a manifest. Never recomputes a verdict; never touches a file whose
 * identity has changed.
 *
 * The log is written BEFORE any unlink and appended as each file goes, so
 * the record says what actually died rather than only what was intended. If
 * the log cannot be written, nothing is deleted: an unrecorded deletion is
 * worse than a deferred one.
 */
/*
 * Append one entry's fate to the journal and flush it. Returns false if the
 * record could not be written, which stops the run.
 */
FrameSelector.appendOutcome = function( logPath, manifest, path )
{
   try
   {
      var e = null;
      for ( var i = 0; i < manifest.entries.length; ++i )
         if ( manifest.entries[i].path == path )
            { e = manifest.entries[i]; break; }
      if ( e == null )
         return false;
      var f = new File;
      f.openForReadWrite( logPath );
      f.seekEnd();
      f.outTextLn( e.outcome + "\t" + e.path + "\t" + e.digest + "\t" + e.detail );
      f.flush();
      f.close();
      return true;
   }
   catch ( ex )
   {
      Util.error( "frames", "could not append to the journal: " + ex );
      return false;
   }
};

FrameSelector.execute = function( manifest )
{
   var result = { deleted: 0, skipped: 0, failed: 0, stopped: false };
   var pending = Frames.manifestPending( manifest );
   if ( pending.length == 0 )
      return result;

   var logPath = FrameSelector.logDir() + "/deleted-" +
                 ( new Date() ).toISOString().replace( /[:.]/g, "-" ) + ".log";
   if ( !FrameSelector.writeManifestLog( manifest, logPath ) )
   {
      Util.error( "frames", "nothing deleted: the log could not be written" );
      return result;
   }

   for ( var i = 0; i < pending.length; ++i )
   {
      var e = pending[i];
      var now = FrameSelector.fileIdentity( e.path );
      if ( !Frames.identityMatches( e, now ) )
      {
         Frames.recordOutcome( manifest, e.path, "skipped",
                               "changed on disk since it was measured" );
         Util.warn( "frames", "skipped " + e.path + ": it changed since measurement" );
         ++result.skipped;
         if ( !FrameSelector.appendOutcome( logPath, manifest, e.path ) )
            { result.stopped = true; return result; }
         continue;
      }
      /*
       * Journalled one entry at a time, before moving on. Writing every
       * outcome at the END loses the whole record if the run is interrupted
       * mid-way -- which is precisely when the record is needed. If the
       * journal cannot be appended, the run STOPS: continuing would delete
       * files nothing is recording.
       */
      try
      {
         File.remove( e.path );
         Frames.recordOutcome( manifest, e.path, "deleted", "" );
         ++result.deleted;
      }
      catch ( ex )
      {
         Frames.recordOutcome( manifest, e.path, "failed", String( ex ) );
         ++result.failed;
      }
      if ( !FrameSelector.appendOutcome( logPath, manifest, e.path ) )
      {
         Util.error( "frames", "stopping: the journal could not be appended" );
         result.stopped = true;
         return result;
      }
   }

   Util.log( "frames", result.deleted + " deleted, " + result.skipped +
                       " skipped, " + result.failed + " failed; log " + logPath );
   return result;
};
```

- [ ] **Step 4: Run to verify it passes**

```bash
rm -f /tmp/agent-scratch/lhso-selftest.txt
/Applications/PixInsight/PixInsight.app/Contents/MacOS/PixInsight \
  -x=1:/Users/francescocarucci/PixInsight/scripts/Loom/script/selftest.js
until [ -f /tmp/agent-scratch/lhso-selftest.txt ]; do sleep 5; done
head -4 /tmp/agent-scratch/lhso-selftest.txt
node ci/run-tests.js
```

Expected: both PASS. Confirm the log exists:

```bash
ls ~/PixInsight/Loom-frame-selector/
```

- [ ] **Step 5: Commit**

```bash
git add script/FrameSelector.js script/selftest.js
git commit -m "Delete only what the digest still matches, and log it first"
```

---

### Task 15: The preview control

**Files:**
- Modify: `script/FrameSelector.js`
- Modify: `script/selftest.js`

**Interfaces:**
- Consumes: the prototype's measured answer from Task 1
- Produces:
  - `FrameSelector.PreviewControl( parent )` — a `Control` with
    `load( path )`, `pan( dx, dy )`, `setFit( Boolean )`, `dispose()`

- [ ] **Step 1: Write the failing test**

Inside `IN_PIXINSIGHT`:

```javascript
      ( function()
      {
         var ok = true, err = "";
         try
         {
            var dlg = new Dialog;
            var pv = new FrameSelector.PreviewControl( dlg );
            /*
             * Arrow keys must not reach the frame table underneath, so the
             * control takes focus on click and consumes the keys it handles.
             */
            ok = ok && ( pv.focusStyle == FocusStyle.Click );
            ok = ok && ( typeof pv.load == "function" );
            ok = ok && ( typeof pv.dispose == "function" );
            /* Panning with nothing loaded must not throw. */
            pv.pan( 100, 100 );
            pv.dispose();
         }
         catch ( e ) { ok = false; err = String( e ); }
         check( "the preview control builds and pans" + ( err ? ": " + err : "" ),
                ok, true );
      } )();
```

- [ ] **Step 2: Run to verify it fails**

```bash
rm -f /tmp/agent-scratch/lhso-selftest.txt
/Applications/PixInsight/PixInsight.app/Contents/MacOS/PixInsight \
  -x=1:/Users/francescocarucci/PixInsight/scripts/Loom/script/selftest.js
until [ -f /tmp/agent-scratch/lhso-selftest.txt ]; do sleep 5; done
head -4 /tmp/agent-scratch/lhso-selftest.txt
```

Expected: FAIL, `FrameSelector.PreviewControl is not a constructor`.

- [ ] **Step 3: Implement**

Use the prototype from Task 1, with the lifecycle the spec requires: exactly one
frame's window and bitmap held at a time.

```javascript
/*
 * A 1:1 pannable preview.
 *
 * Image.render() does NOT apply a screen stretch -- its own documentation
 * excludes it -- so an STF on the view renders nothing different. The
 * duplicate's PIXELS are stretched with a HistogramTransformation built from
 * the frame's own median and MAD, and THAT is what is rendered.
 *
 * Rendered once per selected frame rather than once per paint, so panning is
 * a blit. Exactly one frame's window and bitmap are held: a 26 MP ARGB
 * bitmap is about 104 MB, and browsing a folder must not accumulate them.
 */
FrameSelector.PreviewControl = class extends Control
{
   constructor( parent )
   {
   super( parent );

   var self = this;
   this.bmp = null;
   this.ox = 0;
   this.oy = 0;
   this.fit = false;
   this.dragging = false;
   this.lx = 0;
   this.ly = 0;

   this.setScaledMinSize( 420, 360 );
   this.focusStyle = FocusStyle.Click;     // arrows need focus; a click gives it

   this.dispose = function()
   {
      self.bmp = null;                     // the only reference; let it go
   };

   this.load = function( path )
   {
      self.dispose();
      self.ox = self.oy = 0;
      var win = null;
      try
      {
         var ws = ImageWindow.open( path );
         if ( ws.length == 0 )
            return false;
         win = ws[0];
         var img = win.mainView.image;
         var med = img.median(), mad = img.MAD()*1.4826;
         var shadows = Math.max( 0, med - 2.8*mad );
         var midtone = Math.mtf( 0.25, Math.max( 1e-8, med - shadows ) );

         var dup = new ImageWindow( img.width, img.height, img.numberOfChannels,
                                    img.bitsPerSample, img.isReal, img.isColor,
                                    Util.freeWindowId( "fs_preview" ) );
         dup.mainView.beginProcess( UndoFlag_NoSwapFile );
         dup.mainView.image.assign( img );
         dup.mainView.endProcess();

         var H = new HistogramTransformation;
         H.H = [ [ 0, 0.5, 1, 0, 1 ], [ 0, 0.5, 1, 0, 1 ], [ 0, 0.5, 1, 0, 1 ],
                 [ shadows, midtone, 1, 0, 1 ], [ 0, 0.5, 1, 0, 1 ] ];
         H.executeOn( dup.mainView );

         self.bmp = dup.mainView.image.render();
         dup.forceClose();
         self.update();
         return true;
      }
      catch ( e )
      {
         Util.warn( "frames", "could not preview " + path + ": " + e );
         return false;
      }
      finally
      {
         try { if ( win != null && !win.isNull ) win.forceClose(); } catch ( e2 ) {}
      }
   };

   this.pan = function( dx, dy )
   {
      if ( self.bmp == null )
         return;
      self.ox = Math.max( 0, Math.min( self.ox + dx, Math.max( 0, self.bmp.width  - self.width  ) ) );
      self.oy = Math.max( 0, Math.min( self.oy + dy, Math.max( 0, self.bmp.height - self.height ) ) );
      self.update();
   };

   this.setFit = function( on ) { self.fit = !!on; self.update(); };

   this.onPaint = function()
   {
      var g = new Graphics( this );
      try
      {
         g.fillRect( 0, 0, this.width, this.height, new Brush( 0xff101010 ) );
         if ( self.bmp == null )
            return;
         if ( self.fit )
         {
            var s = Math.min( this.width/self.bmp.width, this.height/self.bmp.height );
            g.drawScaledBitmap(
               new Rect( 0, 0, Math.round( self.bmp.width*s ),
                               Math.round( self.bmp.height*s ) ), self.bmp );
         }
         else
            g.drawBitmapRect( 0, 0, self.bmp,
               new Rect( self.ox, self.oy,
                         Math.min( self.ox + this.width,  self.bmp.width ),
                         Math.min( self.oy + this.height, self.bmp.height ) ) );
      }
      finally { g.end(); }
   };

   this.onMousePress = function( x, y )
   { self.dragging = true; self.lx = x; self.ly = y; };

   this.onMouseRelease = function() { self.dragging = false; };

   this.onMouseMove = function( x, y )
   {
      if ( !self.dragging )
         return;
      self.pan( self.lx - x, self.ly - y );
      self.lx = x; self.ly = y;
   };

   /*
    * Returning true CONSUMES the key. Without that the frame table below
    * also acts on the same arrow press and the selection moves underneath
    * the preview.
    */
   this.onKeyPress = function( key, modifiers )
   {
      var step = ( modifiers & KeyModifier.Shift ) ? this.width
                                                   : Math.round( this.width/4 );
      if ( key == KeyCode.Left  ) { self.pan( -step, 0 ); return true; }
      if ( key == KeyCode.Right ) { self.pan(  step, 0 ); return true; }
      if ( key == KeyCode.Up    ) { self.pan( 0, -step ); return true; }
      if ( key == KeyCode.Down  ) { self.pan( 0,  step ); return true; }
      return false;
   };

   this.onMouseWheel = function( x, y, delta )
   { self.setFit( !self.fit ); return true; };
   }
};
```

- [ ] **Step 4: Run to verify it passes**

```bash
rm -f /tmp/agent-scratch/lhso-selftest.txt
/Applications/PixInsight/PixInsight.app/Contents/MacOS/PixInsight \
  -x=1:/Users/francescocarucci/PixInsight/scripts/Loom/script/selftest.js
until [ -f /tmp/agent-scratch/lhso-selftest.txt ]; do sleep 5; done
head -4 /tmp/agent-scratch/lhso-selftest.txt
node ci/run-tests.js
```

Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add script/FrameSelector.js script/selftest.js
git commit -m "Add the 1:1 preview, stretching pixels because render ignores an STF"
```

---

### Task 16: The dialog

**Files:**
- Modify: `script/FrameSelector.js`
- Modify: `script/selftest.js`

**Interfaces:**
- Consumes: everything above
- Produces:
  - `FrameSelector.Dialog( state )` — the review window
  - `FrameSelector.buildState( folder )` → the cohort + review
  - `FrameSelector.main()`

- [ ] **Step 1: Write the failing test**

Inside `IN_PIXINSIGHT`:

```javascript
      ( function()
      {
         var ok = true, err = "";
         try
         {
            var state = { folder: "/tmp/agent-scratch", channels: {}, order: [],
                          preset: Frames.DEFAULT_PRESET, locked: false };
            var dlg = new FrameSelector.Dialog( state );
            /*
             * The review is editable until an execution is committed, and
             * locked while one is running -- otherwise editing a knob
             * mid-run would alter a manifest that is already deleting files.
             */
            ok = ok && ( typeof dlg.refresh == "function" );
            ok = ok && ( typeof dlg.commit == "function" );
            dlg.cancel();
         }
         catch ( e ) { ok = false; err = String( e ); }
         check( "the frame selector dialog builds" + ( err ? ": " + err : "" ),
                ok, true );
      } )();
```

- [ ] **Step 2: Run to verify it fails**

```bash
rm -f /tmp/agent-scratch/lhso-selftest.txt
/Applications/PixInsight/PixInsight.app/Contents/MacOS/PixInsight \
  -x=1:/Users/francescocarucci/PixInsight/scripts/Loom/script/selftest.js
until [ -f /tmp/agent-scratch/lhso-selftest.txt ]; do sleep 5; done
head -4 /tmp/agent-scratch/lhso-selftest.txt
```

Expected: FAIL, `FrameSelector.Dialog is not a constructor`.

- [ ] **Step 3: Implement**

Build the layout from the spec: channels left, frames centre, preview right,
knobs below. Required behaviours, each of which the spec fixes:

- the channel list shows `kept/total` and the overrides count
- selecting a row calls `preview.load( path )`
- **space** on a row toggles the manual override; an override wins over the
  formula and is counted separately
- changing a knob, a mode or the preset calls `refresh()`, which recomputes
  verdicts **from the cohort**, never from survivors
- `commit()` builds the manifest, shows a confirmation naming the count per
  channel, sets `state.locked = true`, and calls `FrameSelector.execute`
- a disabled channel takes no action at all, in any mode
- a channel whose `comparability` is not uniform shows the problems and
  refuses to commit until it is accepted explicitly

Keep the dialog under ~400 lines; if it grows past that, split the knob panel
into its own constructor in the same file.

- [ ] **Step 4: Run to verify it passes**

```bash
rm -f /tmp/agent-scratch/lhso-selftest.txt
/Applications/PixInsight/PixInsight.app/Contents/MacOS/PixInsight \
  -x=1:/Users/francescocarucci/PixInsight/scripts/Loom/script/selftest.js
until [ -f /tmp/agent-scratch/lhso-selftest.txt ]; do sleep 5; done
head -4 /tmp/agent-scratch/lhso-selftest.txt
node ci/run-tests.js
```

Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add script/FrameSelector.js script/selftest.js
git commit -m "Add the review dialog, locked while an execution runs"
```

---

### Task 17: Export to another folder

**Files:**
- Modify: `script/FrameSelector.js`
- Modify: `script/selftest.js`

**Interfaces:**
- Consumes: `Frames.STATE`
- Produces:
  - `Frames.outputMapping( approved, destination, extension )` → `{ mapping, collisions, aliased }`
    — pure, so the collision rule is tested under node, where `FrameSelector`
    is never loaded
  - `FrameSelector.exportApproved( approved, destination )` → `{ written, failed, skipped }`

- [ ] **Step 1: Write the failing tests**

Pure, so outside `IN_PIXINSIGHT`:

```javascript
   ( function()
   {
      var m = Frames.outputName( "/src/Light_0001_c.xisf" );
      check( "an output keeps its name", m, "Light_0001_c.xisf" );

      /*
       * Two sources that would produce one output name must abort the
       * channel. Writing both would leave one output and, if originals were
       * ever deleted, would destroy the frame that lost the race.
       */
      var r = Frames.outputMapping(
         [ "/a/Light_0001_c.xisf", "/b/Light_0001_c.xisf" ], "/dest", ".xisf" );
      check( "a name collision is detected", r.collisions.length, 1 );

      var ok = Frames.outputMapping(
         [ "/a/one.xisf", "/a/two.xisf" ], "/dest", ".xisf" );
      check( "distinct names map cleanly", ok.collisions.length, 0 );
      check( "and land in the destination",
             ok.mapping["/a/one.xisf"], "/dest/one.xisf" );

      /*
       * Writing into the source directory is refused: it makes "the output"
       * and "the original" the same file.
       */
      check( "the destination may not be the source",
             Frames.outputMapping( [ "/a/one.xisf" ], "/a", ".xisf" ).aliased, true );
      /*
       * The output EXTENSION is part of the mapping, because converting on
       * output creates collisions the source names do not show: a.fit and
       * a.xisf both become a.xisf.
       */
      check( "a conversion collision is detected",
             Frames.outputMapping( [ "/a/one.fit", "/a/one.xisf" ],
                                   "/dest", ".xisf" ).collisions.length, 1 );
   } )();
```

- [ ] **Step 2: Run to verify it fails**

```bash
node ci/run-tests.js
```

Expected: FAIL, `Frames.outputName is not a function`.

- [ ] **Step 3: Implement**

Put `Frames.outputName` in `Frames.js` (pure) and `FrameSelector.outputMapping`
/ `exportApproved` in `FrameSelector.js`. Required behaviour:

```javascript
/* In Frames.js -- pure, so the collision rule is tested under node. */
Frames.outputName = function( path )
{
   var i = Math.max( path.lastIndexOf( "/" ), path.lastIndexOf( "\\" ) );
   return ( i < 0 ) ? path : path.substring( i + 1 );
};
```

```javascript
/*
 * The destination mapping is computed BEFORE anything is written.
 *
 * Export success has to be well defined even though no original is deleted,
 * because otherwise "it worked" is a guess. Two sources sharing an output
 * name abort the channel rather than silently overwriting one with the
 * other, and a destination inside a source directory is refused outright.
 */
FrameSelector.outputMapping = function( approved, destination )
{
   var mapping = {}, taken = {}, collisions = [], aliased = false;
   for ( var i = 0; i < approved.length; ++i )
   {
      var src = approved[i];
      if ( File.extractDirectory( src ) == destination )
         aliased = true;
      var name = Frames.outputName( src );
      if ( taken[name] )
         collisions.push( name );
      taken[name] = true;
      mapping[src] = destination + "/" + name;
   }
   return { mapping: mapping, collisions: collisions, aliased: aliased };
};
```

`exportApproved` uses SubframeSelector's output routine, refuses to run when
`collisions.length > 0` or `aliased`, treats an existing destination file as a
failure for that entry rather than a success when overwriting is off, and
returns per-file outcomes.

- [ ] **Step 4: Run to verify it passes**

```bash
node ci/run-tests.js
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add script/lib/Frames.js script/FrameSelector.js script/selftest.js
git commit -m "Map outputs before writing, and refuse a collision"
```

---

### Task 18: Version gate, registration and documentation

**Files:**
- Modify: `script/FrameSelector.js`
- Modify: `README.md`
- Modify: `ci/package.sh` (verify the new script ships)

**Interfaces:**
- Consumes: `Util.coreVersionAtLeast` (the check Loom already performs)
- Produces: `FrameSelector.main()` wired to the feature id

- [ ] **Step 1: Add the version gate and entry point**

```javascript
function main()
{
   console.show();

   /*
    * Before anything is opened: the measurement column indices were read on
    * 1.9.5, and an older core is not merely untested, it returns a different
    * table.
    */
   if ( !checkCoreVersion() )
      return;

   FrameSelector.main();
}

main();
```

Copy `checkCoreVersion` from `script/Loom.js` — it already reports the found and
required versions and names the script.

- [ ] **Step 2: Verify the packaged zip carries it**

```bash
ci/package.sh 0.0.0-test
unzip -l dist/Loom-0.0.0-test.zip | grep -E "FrameSelector|Frames.js"
rm -rf dist
```

Expected: both files listed. `ci/package.sh` copies `script/` wholesale, so this
should pass without changes — confirm rather than assume.

- [ ] **Step 3: Document it in the README**

Add a section describing what the Frame Selector does, that it deletes in place,
where the audit log goes, and that the three presets set `k`. State plainly that
Absolute mode is the only one that keeps an entire good night.

- [ ] **Step 4: Run both suites one last time**

```bash
node ci/run-tests.js
rm -f /tmp/agent-scratch/lhso-selftest.txt
/Applications/PixInsight/PixInsight.app/Contents/MacOS/PixInsight \
  -x=1:/Users/francescocarucci/PixInsight/scripts/Loom/script/selftest.js
until [ -f /tmp/agent-scratch/lhso-selftest.txt ]; do sleep 5; done
head -4 /tmp/agent-scratch/lhso-selftest.txt
```

Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add script/FrameSelector.js README.md
git commit -m "Register the Frame Selector and require 1.9.5"
```

---

## First real run

Not a task, but the acceptance test that matters. Against a **copy** of one
night's calibrated subs, never the originals:

1. Open the folder. Confirm the channels are the filters you expect and the
   counts match the file counts.
2. Confirm a channel with fewer than ten frames rejects nothing in Relative
   mode.
3. Switch presets and watch the kept counts move. Confirm Strict drops more than
   Lenient, and that the number is plausible rather than the asymptotic figure.
4. Select a rejected frame, pan it at 1:1 with both the mouse and the arrows,
   and rescue it. Confirm the count shows `(+1 rescued)`.
5. Apply. Confirm the log appears under `~/PixInsight/Loom-frame-selector/`
   **before** the files disappear, that it names every deleted frame with its
   reason, and that the outcome file lists each one.
6. Apply again. Confirm nothing further is deleted.
