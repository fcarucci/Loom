# LHSORGBPrep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A PixInsight PJSR script that selects L/H/S/O/R/G/B master frames from a dialog, prepares each according to its type, registers all channels to L, and produces a calibrated combined RGB image when the RGB group is supplied.

**Architecture:** A Feature Script with a selection dialog, an ordered pipeline over an array of `Channel` objects, and one thin wrapper per PixInsight process. Pure logic lives in `Util.js` and is asserted by a self-test that runs headlessly; process invocation is isolated in `Steps.js` so an unverified parameter has exactly one place to be fixed.

**Tech Stack:** PJSR (PixInsight JavaScript Runtime) on PixInsight 1.9.4, V8 engine.

**Spec:** `docs/superpowers/specs/2026-09-11-lhso-rgb-prep-design.md`

## Global Constraints

- **PixInsight 1.9.4.** Every `.js` file that is launched directly MUST begin with `#engine v8` as its first line. Without it PixInsight tries the removed SpiderMonkey engine and fails.
- **Run tests over IPC, not by relaunching.** Start ONE long-lived instance per session and send each script to it with `-x=`. A cold `-r=` boot costs ~45 s; an `-x=` IPC dispatch costs ~1 s. Verified on 1.9.4.

  Start the instance once (idempotent — safe to run before every test):

  ```bash
  pgrep -x PixInsight >/dev/null || {
    nohup /Applications/PixInsight/PixInsight.app/Contents/MacOS/PixInsight \
      --automation-mode -n >/dev/null 2>&1 &
    sleep 45
  }
  ```

  `--automation-mode` disables splash, update checks and GUI messages, and does
  not persist preferences on exit — so a test session cannot alter the user's
  PixInsight configuration.

  Do NOT use `--startup-script`; it requires a Pleiades code signature and fails with "Required code signature not found".
- **Do not kill the instance between tasks.** Leave it running for the whole session. Killing it re-incurs the 45 s boot on the next test.
- **The instance can wedge.** Observed on 1.9.4: a long-lived instance silently stops servicing IPC while still alive and burning no CPU. When wedged it ignores `--terminate=<slot>` too, because that is also an IPC command — so recovery needs `pkill`. If a test produces no result file within the poll window, do NOT conclude the test failed; assume a wedge, recover, and re-run:

  ```bash
  # if the result file never appeared, the instance is wedged, not the test
  pkill -x PixInsight; sleep 3
  nohup /Applications/PixInsight/PixInsight.app/Contents/MacOS/PixInsight \
    --automation-mode -n >/dev/null 2>&1 &
  sleep 45
  # then re-run the -x= dispatch and poll again
  ```

  `PixInsight -e` lists live instances and their slots, and is the quickest way to confirm one exists at all.
- **Executable path:** `/Applications/PixInsight/PixInsight.app/Contents/MacOS/PixInsight`
- **Launching PixInsight from a shell requires bypassing the Claude Code sandbox** (it fails with a neon/Qt error otherwise).
- **Sensor QE curve is exactly** `Sony IMX411/455/461/533/571`. PixInsight ships no "ASI2600MM" entry.
- **Module map** (a process is not always in the module its name suggests):
  | Process | Module |
  |---|---|
  | SpectrophotometricFluxCalibration | `ImageCalibration-pxm` |
  | SpectrophotometricColorCalibration | `ColorCalibration-pxm` |
  | MultiscaleGradientCorrection | `MultiscaleProcessing-pxm` |
  | GraXpert | `GraXpert-pxm` |
  | StarAlignment | `ImageRegistration-pxm` |
  | ChannelCombination | `ColorSpaces-pxm` |
- **Parameter names** for SPFC, SPCC, MGC and GraXpert are listed in Appendix A of the spec. They are verified. Their **types and enum values are NOT verified** — confirm each against a real process instance before marking its wrapper done.
- **Never modify input files.** All work happens on duplicated windows.
- **Each channel slot accepts EITHER an open view OR a file path.** The user's masters live as views inside `.pxiproject` bundles, not as loose files, so a file-only dialog would force an export before every run. When a view is given, the pipeline DUPLICATES it and works on the copy — the user's original view is never modified. A view selection takes precedence over a file path in the same slot.
- **Skip the solve when an astrometric solution already exists.** The user's masters are already plate-solved by WBPP. `Steps.solve` must check for an existing solution and skip, logging that it did. Re-solving is wasted minutes per channel and risks replacing a good solution with a worse one.
- **Required channels:** `L` only (the registration reference). `R`/`G`/`B` are all-or-nothing; a partial RGB set is an error. `H`/`S`/`O` are any subset, and a missing `S` is normal (HOO). At least one group must be present.
- **GraXpert is optional**, controlled by a dialog checkbox, default on. When off it is neither invoked nor required to be installed.
- **Scratch files go to `/tmp/agent-scratch`,** never bare `/tmp`.

---

## File Structure

| File | Responsibility |
|---|---|
| `LHSORGBPrep.js` | Entry point. `#feature-id`, includes, Parameters/Settings wiring, top-level error handler. |
| `lib/Util.js` | Pure logic: `uniqueWindowId`, min-median selection, central rect, FITS keyword parsing, output filenames. Plus logger and window registry. |
| `lib/Steps.js` | One thin wrapper per PixInsight process. The only file that names process parameters. |
| `lib/Pipeline.js` | Preflight validation and ordered stage execution over `Channel[]`. |
| `lib/UI.js` | The selection dialog. |
| `selftest.js` | Headless assertions over `Util.js`. Writes a machine-readable result file. |

`Util.js` is deliberately the largest testable surface. `Steps.js` holds every unverified parameter name so that fixing one is a single-file change.

---

### Task 1: Self-test harness

Nothing else can be test-driven until there is a way to run an assertion headlessly and read the result from a shell. This task builds that, and proves it by asserting a function that does not exist yet.

**Files:**
- Create: `selftest.js`
- Create: `lib/Util.js`

**Interfaces:**
- Consumes: nothing
- Produces: `Util.uniqueWindowId(base, exists)` — `(String, Function(String)->Boolean) -> String`. The `exists` parameter is dependency injection so the function is testable without real windows.

- [ ] **Step 1: Write the failing test**

Create `selftest.js`:

```javascript
#engine v8

#feature-id LHSORGBPrepSelfTest : Scripts > LHSORGBPrepSelfTest

#include "lib/Util.js"

#define RESULT_FILE "/tmp/agent-scratch/lhso-selftest.txt"

var TESTS_RUN = 0;
var FAILURES = [];

function check( name, actual, expected )
{
   TESTS_RUN++;
   var a = JSON.stringify( actual );
   var e = JSON.stringify( expected );
   if ( a != e )
      FAILURES.push( name + ": expected " + e + ", got " + a );
}

function runTests()
{
   // uniqueWindowId: no clash returns the bare base
   check( "uniqueWindowId no clash",
          Util.uniqueWindowId( "RGB", function( id ) { return false; } ),
          "RGB" );

   // uniqueWindowId: base taken returns RGB_1
   check( "uniqueWindowId one clash",
          Util.uniqueWindowId( "RGB", function( id ) { return id == "RGB"; } ),
          "RGB_1" );

   // uniqueWindowId: base and RGB_1 taken returns RGB_2
   check( "uniqueWindowId two clashes",
          Util.uniqueWindowId( "RGB", function( id ) {
             return id == "RGB" || id == "RGB_1";
          } ),
          "RGB_2" );
}

function main()
{
   try { runTests(); }
   catch ( e ) { FAILURES.push( "EXCEPTION: " + e.toString() ); }

   var summary = ( FAILURES.length == 0 ? "PASS" : "FAIL" ) +
                 " " + TESTS_RUN + " run, " + FAILURES.length + " failed\\n" +
                 FAILURES.join( "\\n" ) + "\\n";

   console.writeln( summary );

   if ( !File.directoryExists( "/tmp/agent-scratch" ) )
      File.createDirectory( "/tmp/agent-scratch", true );
   var f = new File;
   f.createForWriting( RESULT_FILE );
   f.outText( summary );
   f.close();
}

main();
```

Create `lib/Util.js` as an empty namespace so the include resolves:

```javascript
var Util = {};
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
mkdir -p /tmp/agent-scratch && rm -f /tmp/agent-scratch/lhso-selftest.txt
/Applications/PixInsight/PixInsight.app/Contents/MacOS/PixInsight \
  -x=$HOME/PixInsight/scripts/LHSORGBPrep/selftest.js
```

This needs `dangerouslyDisableSandbox: true`. The `-x=` command returns immediately — it only queues the script with the running instance — so always poll for the result file rather than trusting the command's exit:

```bash
for i in $(seq 1 60); do [ -f /tmp/agent-scratch/lhso-selftest.txt ] && break; sleep 2; done
cat /tmp/agent-scratch/lhso-selftest.txt
```

Expected: `FAIL 0 run, 1 failed` with `EXCEPTION: TypeError: Util.uniqueWindowId is not a function`.

The count is zero, not three: `Util.uniqueWindowId(...)` is evaluated as an *argument* to `check()`, so it throws before `check()` runs and before `TESTS_RUN` increments. A red phase reporting `0 run` is correct here and is not a harness bug.

- [ ] **Step 3: Write the minimal implementation**

Replace `lib/Util.js`:

```javascript
var Util = {};

/*
 * Returns `base` if free, otherwise the first free `base_<n>` starting at n=1.
 * `exists` is a predicate taking an identifier and returning true if taken.
 */
Util.uniqueWindowId = function( base, exists )
{
   if ( !exists( base ) )
      return base;
   for ( var n = 1; ; ++n )
   {
      var candidate = base + "_" + n;
      if ( !exists( candidate ) )
         return candidate;
   }
};
```

- [ ] **Step 4: Run the test to verify it passes**

Same commands as Step 2. Expected: `PASS 3 run, 0 failed`.

- [ ] **Step 5: Commit**

```bash
git add selftest.js lib/Util.js
git commit -m "test: add headless self-test harness and uniqueWindowId"
```

---

### Task 2: Window identifier binding and the window registry

`uniqueWindowId` takes an injected predicate; this task binds it to real PixInsight windows and adds the registry that makes cleanup bookkeeping rather than guesswork.

**Files:**
- Modify: `lib/Util.js`
- Modify: `selftest.js`

**Interfaces:**
- Consumes: `Util.uniqueWindowId(base, exists)`
- Produces:
  - `Util.windowIdExists(id)` — `String -> Boolean`
  - `Util.freeWindowId(base)` — `String -> String`, `uniqueWindowId` bound to real windows
  - `Util.Registry()` — constructor; methods `add(window)`, `forget(window)`, `closeAll()`

- [ ] **Step 1: Write the failing test**

Add to `runTests()` in `selftest.js`:

```javascript
   // Registry tracks and forgets windows without needing real ones
   var reg = new Util.Registry;
   var fakeA = { id: "a", closed: false, forceClose: function() { this.closed = true; } };
   var fakeB = { id: "b", closed: false, forceClose: function() { this.closed = true; } };
   reg.add( fakeA );
   reg.add( fakeB );
   reg.forget( fakeB );
   reg.closeAll();
   check( "registry closes tracked window", fakeA.closed, true );
   check( "registry skips forgotten window", fakeB.closed, false );
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
rm -f /tmp/agent-scratch/lhso-selftest.txt
/Applications/PixInsight/PixInsight.app/Contents/MacOS/PixInsight \
  -x=$HOME/PixInsight/scripts/LHSORGBPrep/selftest.js
for i in $(seq 1 60); do [ -f /tmp/agent-scratch/lhso-selftest.txt ] && break; sleep 2; done
cat /tmp/agent-scratch/lhso-selftest.txt
```

Expected: FAIL with `EXCEPTION: TypeError: Util.Registry is not a constructor`.

- [ ] **Step 3: Write the minimal implementation**

Append to `lib/Util.js`:

```javascript
Util.windowIdExists = function( id )
{
   return !ImageWindow.windowById( id ).isNull;
};

Util.freeWindowId = function( base )
{
   return Util.uniqueWindowId( base, Util.windowIdExists );
};

/*
 * Tracks every window the script creates so a failure can close all of
 * them without guessing. Windows handed to the user are forgotten first.
 */
Util.Registry = function()
{
   this.windows = [];

   this.add = function( w )
   {
      this.windows.push( w );
      return w;
   };

   this.forget = function( w )
   {
      for ( var i = 0; i < this.windows.length; ++i )
         if ( this.windows[i] === w )
         {
            this.windows.splice( i, 1 );
            return;
         }
   };

   this.closeAll = function()
   {
      for ( var i = 0; i < this.windows.length; ++i )
         try { this.windows[i].forceClose(); }
         catch ( e ) { /* already closed */ }
      this.windows = [];
   };
};
```

- [ ] **Step 4: Run the test to verify it passes**

Same commands as Step 2. Expected: `PASS 5 run, 0 failed`.

- [ ] **Step 5: Commit**

```bash
git add lib/Util.js selftest.js
git commit -m "feat: bind window ids to real windows and add window registry"
```

---

### Task 3: LinearFit reference selection

The narrowband reference is the channel with the lowest median, measured over the central 60% of the frame because registration leaves per-channel borders of zeros that would bias a whole-frame median.

**Files:**
- Modify: `lib/Util.js`
- Modify: `selftest.js`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `Util.centralRect(width, height, fraction)` — `(Number, Number, Number) -> {x0,y0,x1,y1}`
  - `Util.minMedianKey(medians)` — `Object<String,Number> -> String|null`. Returns the key with the lowest value, or `null` for an empty object. Ties resolve to the first key in `Object.keys` order.

- [ ] **Step 1: Write the failing test**

Add to `runTests()`:

```javascript
   // centralRect: 60% of a 1000x500 frame, centred
   check( "centralRect 60pct",
          Util.centralRect( 1000, 500, 0.6 ),
          { x0: 200, y0: 100, x1: 800, y1: 400 } );

   // centralRect: odd dimensions floor consistently and stay in bounds
   var r = Util.centralRect( 101, 101, 0.6 );
   check( "centralRect odd in bounds", r.x0 >= 0 && r.x1 <= 101, true );
   check( "centralRect odd non-empty", r.x1 > r.x0, true );

   // minMedianKey picks the lowest
   check( "minMedianKey picks lowest",
          Util.minMedianKey( { H: 0.0042, S: 0.0011, O: 0.0033 } ),
          "S" );

   // minMedianKey with a single channel returns it
   check( "minMedianKey single",
          Util.minMedianKey( { H: 0.5 } ),
          "H" );

   // minMedianKey with nothing returns null
   check( "minMedianKey empty", Util.minMedianKey( {} ), null );
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
rm -f /tmp/agent-scratch/lhso-selftest.txt
/Applications/PixInsight/PixInsight.app/Contents/MacOS/PixInsight \
  -x=$HOME/PixInsight/scripts/LHSORGBPrep/selftest.js
for i in $(seq 1 60); do [ -f /tmp/agent-scratch/lhso-selftest.txt ] && break; sleep 2; done
cat /tmp/agent-scratch/lhso-selftest.txt
```

Expected: FAIL with `EXCEPTION: TypeError: Util.centralRect is not a function`.

- [ ] **Step 3: Write the minimal implementation**

Append to `lib/Util.js`:

```javascript
/*
 * The centred sub-rectangle covering `fraction` of each dimension.
 * Used to measure medians away from the zero borders that registration
 * leaves, which differ per channel and would otherwise bias comparison.
 */
Util.centralRect = function( width, height, fraction )
{
   var mx = Math.floor( width * (1 - fraction) / 2 );
   var my = Math.floor( height * (1 - fraction) / 2 );
   return { x0: mx, y0: my, x1: width - mx, y1: height - my };
};

/*
 * Key of the lowest value, or null if there are none.
 */
Util.minMedianKey = function( medians )
{
   var keys = Object.keys( medians );
   if ( keys.length == 0 )
      return null;
   var best = keys[0];
   for ( var i = 1; i < keys.length; ++i )
      if ( medians[keys[i]] < medians[best] )
         best = keys[i];
   return best;
};
```

- [ ] **Step 4: Run the test to verify it passes**

Same commands as Step 2. Expected: `PASS 11 run, 0 failed`.

- [ ] **Step 5: Commit**

```bash
git add lib/Util.js selftest.js
git commit -m "feat: add central rect and min-median reference selection"
```

---

### Task 4: Channel model, FITS metadata, and output filenames

**Files:**
- Modify: `lib/Util.js`
- Modify: `selftest.js`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `Util.CHANNELS` — `["L","R","G","B","H","S","O"]`
  - `Util.BROADBAND` — `["L","R","G","B"]`
  - `Util.isBroadband(key)` — `String -> Boolean`
  - `Util.outputPath(dir, key, ext)` — `(String, String, String) -> String`
  - `Util.RGB_GROUP` — `["R","G","B"]`; `Util.NARROWBAND` — `["H","S","O"]`; `Util.REQUIRED` — `["L"]`
  - `Util.keywordValue(keywords, name)` — `(Array<{name,value}>, String) -> String|null`. Strips FITS quoting and surrounding whitespace; returns `null` when absent or empty.

- [ ] **Step 1: Write the failing test**

Add to `runTests()`:

```javascript
   check( "isBroadband L", Util.isBroadband( "L" ), true );
   check( "isBroadband H", Util.isBroadband( "H" ), false );

   check( "outputPath joins",
          Util.outputPath( "/out", "R", "xisf" ),
          "/out/R_processed.xisf" );

   check( "outputPath strips trailing slash",
          Util.outputPath( "/out/", "RGB", "fit" ),
          "/out/RGB_processed.fit" );

   // FITS values arrive quoted and padded
   var kw = [ { name: "FILTER", value: "'Ha      '" },
              { name: "TELESCOP", value: " 'FSQ-106' " },
              { name: "EMPTY", value: "'   '" } ];
   check( "keywordValue unquotes", Util.keywordValue( kw, "FILTER" ), "Ha" );
   check( "keywordValue trims outside quotes", Util.keywordValue( kw, "TELESCOP" ), "FSQ-106" );
   check( "keywordValue blank is null", Util.keywordValue( kw, "EMPTY" ), null );
   check( "keywordValue missing is null", Util.keywordValue( kw, "NOPE" ), null );
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
rm -f /tmp/agent-scratch/lhso-selftest.txt
/Applications/PixInsight/PixInsight.app/Contents/MacOS/PixInsight \
  -x=$HOME/PixInsight/scripts/LHSORGBPrep/selftest.js
for i in $(seq 1 60); do [ -f /tmp/agent-scratch/lhso-selftest.txt ] && break; sleep 2; done
cat /tmp/agent-scratch/lhso-selftest.txt
```

Expected: FAIL with `EXCEPTION: TypeError: Util.isBroadband is not a function`.

- [ ] **Step 3: Write the minimal implementation**

Append to `lib/Util.js`:

```javascript
Util.CHANNELS   = [ "L", "R", "G", "B", "H", "S", "O" ];
Util.BROADBAND  = [ "L", "R", "G", "B" ];
Util.RGB_GROUP  = [ "R", "G", "B" ];
Util.NARROWBAND = [ "H", "S", "O" ];
Util.REQUIRED   = [ "L" ];

Util.isBroadband = function( key )
{
   return Util.BROADBAND.indexOf( key ) >= 0;
};

Util.outputPath = function( dir, key, ext )
{
   var d = dir.replace( /\/+$/, "" );
   return d + "/" + key + "_processed." + ext;
};

/*
 * FITS string values arrive single-quoted and blank-padded to a fixed
 * width. Returns the bare value, or null when absent or blank.
 */
Util.keywordValue = function( keywords, name )
{
   for ( var i = 0; i < keywords.length; ++i )
      if ( keywords[i].name == name )
      {
         var v = keywords[i].value.trim();
         if ( v.length >= 2 && v.charAt( 0 ) == "'" && v.charAt( v.length-1 ) == "'" )
            v = v.substring( 1, v.length-1 );
         v = v.trim();
         return v.length > 0 ? v : null;
      }
   return null;
};
```

- [ ] **Step 4: Run the test to verify it passes**

Same commands as Step 2. Expected: `PASS 19 run, 0 failed`.

- [ ] **Step 5: Commit**

```bash
git add lib/Util.js selftest.js
git commit -m "feat: add channel model, FITS keyword parsing, output paths"
```

---

### Task 5: Logger and preflight validation rules

Preflight is a wall: nothing opens or processes until every check passes, so a missing keyword costs a dialog box rather than a half-finished run. The *rules* are pure and testable here; binding them to the filesystem happens in Task 7.

**Files:**
- Modify: `lib/Util.js`
- Modify: `selftest.js`

**Interfaces:**
- Consumes: `Util.CHANNELS`, `Util.REQUIRED`, `Util.isBroadband`
- Produces:
  - `Util.log(stage, message)`, `Util.warn(stage, message)`, `Util.error(stage, message)`
  - `Util.validateSelection(paths)` — `Object<String,String> -> Array<String>` of human-readable problems; empty array means valid. Enforces: `L` required; RGB all-or-nothing; any narrowband subset; at least one group present; no duplicate files.


- [ ] **Step 1: Write the failing test**

Add to `runTests()`:

```javascript
   // L plus a complete RGB set is valid
   check( "validate RGB only",
          Util.validateSelection( { L: "/a/L.xisf", R: "/a/R.xisf",
                                    G: "/a/G.xisf", B: "/a/B.xisf" } ),
          [] );

   // L plus narrowband, no RGB at all, is valid
   check( "validate narrowband only",
          Util.validateSelection( { L: "/a/L.xisf", H: "/a/H.xisf",
                                    O: "/a/O.xisf" } ),
          [] );

   // A missing S is normal HOO work, not an error
   check( "validate missing S is fine",
          Util.validateSelection( { L: "/a/L.xisf", H: "/a/H.xisf" } ),
          [] );

   // Both groups together is valid
   check( "validate both groups",
          Util.validateSelection( { L: "/a/L.xisf", R: "/a/R.xisf",
                                    G: "/a/G.xisf", B: "/a/B.xisf",
                                    H: "/a/H.xisf" } ),
          [] );

   // A partial RGB set cannot be combined
   check( "validate partial RGB",
          Util.validateSelection( { L: "/a/L.xisf", R: "/a/R.xisf",
                                    G: "/a/G.xisf" } ),
          [ "Incomplete RGB set: missing B. Supply all three or none." ] );

   // L is the registration reference and is always required
   check( "validate missing L",
          Util.validateSelection( { R: "/a/R.xisf", G: "/a/G.xisf",
                                    B: "/a/B.xisf" } ),
          [ "Missing required channel: L" ] );

   // L on its own has nothing to do
   check( "validate L alone",
          Util.validateSelection( { L: "/a/L.xisf" } ),
          [ "Nothing to do: supply R, G and B, or at least one of H, S, O" ] );

   // The same file used twice is a mistake worth catching early
   check( "validate duplicate path",
          Util.validateSelection( { L: "/a/L.xisf", R: "/a/X.xisf",
                                    G: "/a/X.xisf", B: "/a/B.xisf" } ),
          [ "Same file selected for R and G: /a/X.xisf" ] );
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
rm -f /tmp/agent-scratch/lhso-selftest.txt
/Applications/PixInsight/PixInsight.app/Contents/MacOS/PixInsight \
  -x=$HOME/PixInsight/scripts/LHSORGBPrep/selftest.js
for i in $(seq 1 60); do [ -f /tmp/agent-scratch/lhso-selftest.txt ] && break; sleep 2; done
cat /tmp/agent-scratch/lhso-selftest.txt
```

Expected: FAIL with `EXCEPTION: TypeError: Util.validateSelection is not a function`.

- [ ] **Step 3: Write the minimal implementation**

Append to `lib/Util.js`:

```javascript
Util.log = function( stage, message )
{
   console.writeln( "<end><cbr>[" + stage + "] " + message );
};

Util.warn = function( stage, message )
{
   console.warningln( "<end><cbr>[" + stage + "] " + message );
};

Util.error = function( stage, message )
{
   console.criticalln( "<end><cbr>[" + stage + "] " + message );
};

/*
 * Structural problems with a channel selection, independent of the
 * filesystem. Returns a list of human-readable problems; empty is valid.
 */
Util.validateSelection = function( paths )
{
   var problems = [];

   function have( k ) { return paths[k] != undefined && paths[k].length > 0; }

   for ( var i = 0; i < Util.REQUIRED.length; ++i )
      if ( !have( Util.REQUIRED[i] ) )
         problems.push( "Missing required channel: " + Util.REQUIRED[i] );

   // The RGB group is all-or-nothing: ChannelCombination needs all three.
   var rgbPresent = [], rgbMissing = [];
   for ( var r = 0; r < Util.RGB_GROUP.length; ++r )
      ( have( Util.RGB_GROUP[r] ) ? rgbPresent : rgbMissing ).push( Util.RGB_GROUP[r] );

   if ( rgbPresent.length > 0 && rgbMissing.length > 0 )
      problems.push( "Incomplete RGB set: missing " + rgbMissing.join( ", " ) +
                     ". Supply all three or none." );

   // Any subset of narrowband is fine, including a single channel.
   var nbCount = 0;
   for ( var n = 0; n < Util.NARROWBAND.length; ++n )
      if ( have( Util.NARROWBAND[n] ) )
         nbCount++;

   if ( rgbPresent.length == 0 && nbCount == 0 )
      problems.push( "Nothing to do: supply R, G and B, or at least one of H, S, O" );

   var seen = {};
   for ( var j = 0; j < Util.CHANNELS.length; ++j )
   {
      var k = Util.CHANNELS[j];
      var p = paths[k];
      if ( !p || p.length == 0 )
         continue;
      if ( seen[p] !== undefined )
         problems.push( "Same file selected for " + seen[p] + " and " + k + ": " + p );
      else
         seen[p] = k;
   }

   return problems;
};
```

- [ ] **Step 4: Run the test to verify it passes**

Same commands as Step 2. Expected: `PASS 27 run, 0 failed`.

- [ ] **Step 5: Commit**

```bash
git add lib/Util.js selftest.js
git commit -m "feat: add logging and selection validation rules"
```

---

### Task 6: Process parameter verification

**This task writes no pipeline code.** It resolves the plan's single largest risk: parameter names were recovered from module binaries, but their **types and enum values were not**. Guessing here produces a script that runs happily and silently does the wrong correction — the worst failure mode available.

**This is fully automated — do NOT ask a human to use the GUI.** Every PixInsight process object implements `toSource()`, which emits exactly what the GUI's *Edit Instance Source Code* produces, including each parameter's type and default value. Verified working over IPC on 1.9.4.

**Files:**
- Create: `docs/verified-parameters.md`

**Interfaces:**
- Consumes: Appendix A of the spec
- Produces: confirmed types and default values for every parameter used in Task 7

- [ ] **Step 1: Write the dump script**

Create the probe in `/tmp/agent-scratch/` — NOT in the repo, it must not appear in the diff:

```javascript
#engine v8
var out = [];
function say( s ){ out.push( String( s ) ); }

function dump( name )
{
   say( "===== " + name + " =====" );
   try {
      var P = eval( "new " + name );
      try {
         say( P.toSource() );
      } catch ( e1 ) {
         say( "-- toSource() unavailable: " + e1 );
      }
      var consts = [], proto = eval( name + ".prototype" );
      for ( var k in proto )
         if ( typeof proto[k] == "number" )
            consts.push( k + "=" + proto[k] );
      if ( consts.length )
         say( "-- prototype constants: " + consts.join( ", " ) );
   } catch ( e ) {
      say( "EXCEPTION for " + name + ": " + e );
   }
   say( "" );
}

[ "SpectrophotometricFluxCalibration",
  "SpectrophotometricColorCalibration",
  "MultiscaleGradientCorrection",
  "GraXpert",
  "StarAlignment",
  "LinearFit",
  "ChannelCombination",
  "ImageSolver" ].forEach( dump );

var f = new File;
f.createForWriting( "/tmp/agent-scratch/params-dump.txt" );
f.outText( out.join( "\n" ) + "\n" );
f.close();
```

- [ ] **Step 2: Run it over IPC and read the dump**

```bash
rm -f /tmp/agent-scratch/params-dump.txt
/Applications/PixInsight/PixInsight.app/Contents/MacOS/PixInsight \
  -x=/tmp/agent-scratch/probe-params.js
for i in $(seq 1 30); do [ -f /tmp/agent-scratch/params-dump.txt ] && break; sleep 1; done
cat /tmp/agent-scratch/params-dump.txt
```

If no file appears, the instance is wedged — recover per the Global Constraints and re-run.

`ImageSolver` is a script, not a process, so it is expected to throw. Record what it reports; Task 7 needs to know how to invoke it.

- [ ] **Step 3: Record the results**

Create `docs/verified-parameters.md` with the verbatim `toSource()` output for each process, under one heading per process.

Then add a **Findings** section calling out, explicitly, every place the real parameters differ from the spec's Appendix A or from the plan's assumptions. Known already from a partial dump — confirm and extend:

- GraXpert Background Extraction is enabled by `backgroundExtraction = true` (a **boolean**). `correction` is a **string** (`"Subtraction"`), NOT an enum constant selecting the mode. The plan's Task 7 skeleton comment saying `correction` selects Background Extraction is WRONG.
- GraXpert also exposes `appPath`, `denoising` and `deconvolution` booleans absent from Appendix A.

- [ ] **Step 4: State the settings the pipeline needs**

For each of SPFC, SPCC, MGC and GraXpert, write the exact assignments Task 7 must make, with real values — e.g. the device QE curve string `Sony IMX411/455/461/533/571`, the boolean that enables background extraction, and whether MGC's MARS database is actually available. Task 7 codes from this section, so leave nothing as prose where a value belongs.

- [ ] **Step 5: Commit**

```bash
git add docs/verified-parameters.md
git commit -m "docs: record verified process parameters via toSource()"
```

### Task 7: Process wrappers

One thin wrapper per process, each taking explicit arguments and returning the resulting view or window. This is the only file naming process parameters, so a wrong name is a one-file fix.

**Files:**
- Create: `lib/Steps.js`
- Modify: `selftest.js`

**Interfaces:**
- Consumes: `Util.log`, `Util.freeWindowId`, `docs/verified-parameters.md`
- Produces:
  - `Steps.solve(view)` — runs ImageSolver; throws on failure
  - `Steps.spfc(view, filterName)` — `(View, String) -> void`
  - `Steps.spccRGB(view)` — `View -> void`
  - `Steps.mgc(view)` — `View -> void`
  - `Steps.graxpert(view, smoothing)` — `(View, Number) -> void`; called only when enabled
  - `Steps.register(view, referenceView)` — `(View, View) -> ImageWindow` (the registered result)
  - `Steps.linearFit(view, referenceView)` — `(View, View) -> void`
  - `Steps.combineRGB(rView, gView, bView, id)` — `(View, View, View, String) -> ImageWindow`
  - `Steps.medianOfCentre(view, fraction)` — `(View, Number) -> Number`
  - `Steps.moduleAvailable(name)` — `String -> Boolean`

> **`docs/verified-parameters.md` is the BINDING AUTHORITY for every process assignment in this task.** It records real values dumped from a live PixInsight via `toSource()`. Its "Settings the pipeline must make" section gives the exact assignments to write. Copy them from that file. Do not write any parameter from memory, from the spec's Appendix A, or from the skeleton below.
>
> The skeleton below shows STRUCTURE ONLY — function shapes, error handling, logging. Several of its parameter comments were written before Task 6 and are now known to be WRONG. Where the skeleton and `verified-parameters.md` disagree, the document wins, every time.
>
> Specifically, these plan assumptions were disproved by Task 6 and must NOT be carried into the code:
>
> | Plan said | Reality |
> |---|---|
> | `correction` selects Background Extraction mode | `backgroundExtraction = true` (boolean) enables it; `correction` is a STRING selecting the method ("Subtraction"/"Division") |
> | Setting `deviceQECurveName` selects the sensor | `deviceQECurve` and `deviceQECurveName` are a MATCHED PAIR. Setting only the name silently leaves stale curve data and calibrates against the wrong sensor response. Copy the full 3242-character curve literal from the document. |
> | `new ImageSolver` | ImageSolver is a SCRIPT, not a process — `new ImageSolver` throws `ReferenceError`. Use the include-and-engine recipe in the document. |
> | SPCC just needs the curve | SPCC also needs `applyCalibration = true`, or it reports without modifying the image. |
> | GraXpert `replaceImage` optional | `replaceImage = true` is REQUIRED — the pipeline corrects the working window in place, then registers and saves that same window. |
> | Appendix A lists the parameters | Appendix A is NON-EXHAUSTIVE for every process, and several names in it do not exist on the live instance. |
>
> **MGC and the MARS database.** The MARS reference database is NOT installed on this machine. Implement MGC properly per the document — do NOT skip it, and do NOT silently degrade it. Task 8's preflight is responsible for detecting the missing database and failing loudly. A silently skipped gradient correction would leave uncorrected gradients in data the user believes was corrected.
>
> **Filter lookup.** SPFC needs each channel's filter transmission curve, resolved at runtime from the image's FITS `FILTER` keyword against `filters.xspd`. The document flags this as a Task 7 prerequisite and describes the file's structure and the no-match fallback. Implement it as a helper in `Steps.js`.

- [ ] **Step 1: Write the failing test**

Add to `runTests()` in `selftest.js` (add `#include "lib/Steps.js"` at the top of the file):

```javascript
   // Availability checks use real process constructors
   check( "module check finds StarAlignment",
          Steps.moduleAvailable( "StarAlignment" ), true );
   check( "module check finds SPFC",
          Steps.moduleAvailable( "SpectrophotometricFluxCalibration" ), true );
   check( "module check finds MGC",
          Steps.moduleAvailable( "MultiscaleGradientCorrection" ), true );
   check( "module check finds GraXpert",
          Steps.moduleAvailable( "GraXpert" ), true );
   check( "module check rejects nonsense",
          Steps.moduleAvailable( "NotARealProcess" ), false );
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
rm -f /tmp/agent-scratch/lhso-selftest.txt
/Applications/PixInsight/PixInsight.app/Contents/MacOS/PixInsight \
  -x=$HOME/PixInsight/scripts/LHSORGBPrep/selftest.js
for i in $(seq 1 60); do [ -f /tmp/agent-scratch/lhso-selftest.txt ] && break; sleep 2; done
cat /tmp/agent-scratch/lhso-selftest.txt
```

Expected: FAIL — `lib/Steps.js` does not exist, so the include fails.

- [ ] **Step 3: Write the implementation**

Create `lib/Steps.js`:

```javascript
var Steps = {};

Steps.DEVICE_QE_CURVE = "Sony IMX411/455/461/533/571";

/*
 * True if a process of this name is installed. Checked during preflight
 * so a missing module names itself rather than surfacing as an
 * undefined-symbol error mid-run.
 */
Steps.moduleAvailable = function( name )
{
   try { return typeof( this.global[name] ) == "function"; }
   catch ( e ) { /* fall through */ }
   try { return eval( "typeof " + name ) == "function"; }
   catch ( e2 ) { return false; }
};

/*
 * Median of the centred sub-rectangle. Registration leaves per-channel
 * borders of zeros; measuring the centre keeps them out of the
 * comparison that picks the LinearFit reference.
 */
Steps.medianOfCentre = function( view, fraction )
{
   var img = view.image;
   var r = Util.centralRect( img.width, img.height, fraction );
   var saved = img.selectedRect;
   img.selectedRect = new Rect( r.x0, r.y0, r.x1, r.y1 );
   var m = img.median();
   img.selectedRect = saved;
   return m;
};

Steps.solve = function( view )
{
   Util.log( "solve", view.id );
   // ImageSolver ships as a script, not a process. Fill in the
   // invocation recorded in docs/verified-parameters.md.
   // It must throw if no astrometric solution results, because
   // SPFC, SPCC and MGC all depend on one.
};

Steps.spfc = function( view, filterName )
{
   Util.log( "spfc", view.id + " filter=" + filterName );
   var P = new SpectrophotometricFluxCalibration;
   P.deviceQECurveName = Steps.DEVICE_QE_CURVE;
   // Set grayFilterName for a mono target; the red/green/blue triplet
   // for an RGB target. Remaining values from verified-parameters.md.
   if ( !P.executeOn( view ) )
      throw new Error( "SPFC failed on " + view.id );
};

Steps.spccRGB = function( view )
{
   Util.log( "spcc", view.id );
   var P = new SpectrophotometricColorCalibration;
   P.deviceQECurveName = Steps.DEVICE_QE_CURVE;
   if ( !P.executeOn( view ) )
      throw new Error( "SPCC failed on " + view.id );
};

Steps.mgc = function( view )
{
   Util.log( "mgc", view.id );
   var P = new MultiscaleGradientCorrection;
   P.useMARSDatabase = true;
   if ( !P.executeOn( view ) )
      throw new Error( "MGC failed on " + view.id );
};

Steps.graxpert = function( view, smoothing )
{
   Util.log( "graxpert", view.id + " smoothing=" + smoothing );
   var P = new GraXpert;
   // P.correction must select Background Extraction using the exact
   // constant recorded in docs/verified-parameters.md.
   P.smoothing = smoothing;
   P.replaceImage = true;
   if ( !P.executeOn( view ) )
      throw new Error( "GraXpert failed on " + view.id );
};

Steps.register = function( view, referenceView )
{
   Util.log( "register", view.id + " -> " + referenceView.id );
   var P = new StarAlignment;
   // Reference is a view, not a file. Exact property from
   // verified-parameters.md.
   if ( !P.executeOn( view ) )
      throw new Error( "StarAlignment failed on " + view.id );
};

Steps.linearFit = function( view, referenceView )
{
   Util.log( "linearfit", view.id + " -> " + referenceView.id );
   var P = new LinearFit;
   P.referenceViewId = referenceView.id;
   if ( !P.executeOn( view ) )
      throw new Error( "LinearFit failed on " + view.id );
};

Steps.combineRGB = function( rView, gView, bView, id )
{
   Util.log( "combine", "-> " + id );
   var w = new ImageWindow( rView.image.width, rView.image.height,
                            3, 32, true, true, id );
   var P = new ChannelCombination;
   P.colorSpace = ChannelCombination.prototype.RGB;
   P.channels = [ [ true, rView.id ],
                  [ true, gView.id ],
                  [ true, bView.id ] ];
   P.executeOn( w.mainView );
   return w;
};
```

- [ ] **Step 4: Run the test to verify it passes**

Same commands as Step 2. Expected: `PASS 32 run, 0 failed`.

If `moduleAvailable` returns false for a process you know is installed, the detection approach is wrong — fix it here rather than working around it in Pipeline.

- [ ] **Step 5: Commit**

```bash
git add lib/Steps.js selftest.js
git commit -m "feat: add process wrappers with module availability checks"
```

---

### Task 8: Pipeline orchestration

**Files:**
- Create: `lib/Pipeline.js`

**Interfaces:**
- Consumes: all of `Util` and `Steps`
- Produces:
  - `Pipeline.preflight(config)` — `Object -> Array<String>` of problems; empty means go
  - `Pipeline.run(config)` — `Object -> Object` mapping channel key to result window, plus `RGB`

`config` is `{ paths, outputDir, outputFormat, smoothing, validateOnly }`.

- [ ] **Step 1: Write the implementation**

Create `lib/Pipeline.js`:

```javascript
var Pipeline = {};

Pipeline.REQUIRED_PROCESSES = [
   "SpectrophotometricFluxCalibration",
   "SpectrophotometricColorCalibration",
   "MultiscaleGradientCorrection",
   "StarAlignment",
   "LinearFit",
   "ChannelCombination"
];

/*
 * A wall: nothing opens or processes until every check passes, so a
 * missing FITS keyword costs a dialog box rather than a half-finished run.
 */
Pipeline.preflight = function( config )
{
   var problems = Util.validateSelection( config.paths );

   for ( var i = 0; i < Pipeline.REQUIRED_PROCESSES.length; ++i )
   {
      var p = Pipeline.REQUIRED_PROCESSES[i];
      if ( !Steps.moduleAvailable( p ) )
         problems.push( "Process not installed: " + p );
   }

   // GraXpert is only required when it is enabled.
   if ( config.useGraXpert && !Steps.moduleAvailable( "GraXpert" ) )
      problems.push( "Process not installed: GraXpert (disable it to proceed without)" );

   for ( var j = 0; j < Util.CHANNELS.length; ++j )
   {
      var key = Util.CHANNELS[j];
      var path = config.paths[key];
      if ( !path )
         continue;
      if ( !File.exists( path ) )
      {
         problems.push( "File not found for " + key + ": " + path );
         continue;
      }
      if ( Util.isBroadband( key ) )
      {
         var kws = Pipeline.readKeywords( path );
         if ( Util.keywordValue( kws, "FILTER" ) === null )
            problems.push( "No FILTER keyword in " + key + ": " + path +
                           " (SPFC cannot proceed; the script will not guess a filter)" );
      }
   }

   if ( !File.directoryExists( config.outputDir ) )
      problems.push( "Output folder does not exist: " + config.outputDir );

   return problems;
};

Pipeline.readKeywords = function( path )
{
   var w = ImageWindow.open( path );
   if ( w.length == 0 )
      return [];
   var kws = w[0].keywords;
   w[0].forceClose();
   return kws;
};

Pipeline.run = function( config )
{
   var reg = new Util.Registry;
   var results = {};

   try
   {
      // load — a slot may name an open view or a file on disk
      var chans = {};
      for ( var i = 0; i < Util.CHANNELS.length; ++i )
      {
         var key = Util.CHANNELS[i];
         var w;
         if ( config.views && config.views[key] )
         {
            // Work on a DUPLICATE so the user's own view is never modified.
            var srcWin = ImageWindow.windowById( config.views[key] );
            if ( srcWin.isNull )
               throw new Error( "View no longer open for " + key + ": " + config.views[key] );
            w = new ImageWindow( srcWin.mainView.image.width,
                                 srcWin.mainView.image.height,
                                 srcWin.mainView.image.numberOfChannels,
                                 srcWin.mainView.image.bitsPerSample,
                                 srcWin.mainView.image.isReal,
                                 srcWin.mainView.image.isColor,
                                 Util.freeWindowId( key + "_work" ) );
            w.mainView.beginProcess( UndoFlag_NoSwapFile );
            w.mainView.image.assign( srcWin.mainView.image );
            w.mainView.endProcess();
            w.keywords = srcWin.keywords;
            Util.log( "load", key + " <- view " + config.views[key] + " (duplicated)" );
         }
         else if ( config.paths[key] )
         {
            var ws = ImageWindow.open( config.paths[key] );
            if ( ws.length == 0 )
               throw new Error( "Could not open " + key + ": " + config.paths[key] );
            w = ws[0];
         }
         else
            continue;
         w.mainView.id = Util.freeWindowId( key + "_work" );
         reg.add( w );
         chans[key] = { key: key, window: w, view: w.mainView,
                        filter: Util.keywordValue( w.keywords, "FILTER" ) };
      }

      // solve + correct, broadband only, on native uninterpolated pixels.
      // Absent channels are skipped; a narrowband-only run corrects L alone.
      for ( var b = 0; b < Util.BROADBAND.length; ++b )
      {
         var bk = Util.BROADBAND[b];
         if ( !chans[bk] ) continue;
         Steps.solve( chans[bk].view );
         Steps.spfc( chans[bk].view, chans[bk].filter );
         Steps.mgc( chans[bk].view );
         if ( config.useGraXpert )
            Steps.graxpert( chans[bk].view, config.smoothing );
         Pipeline.checkAbort();
      }

      // register everything to L; L is the reference and is never resampled
      var refView = chans.L.view;
      for ( var c = 0; c < Util.CHANNELS.length; ++c )
      {
         var ck = Util.CHANNELS[c];
         if ( ck == "L" || !chans[ck] ) continue;
         Steps.register( chans[ck].view, refView );
         Pipeline.checkAbort();
      }

      // linear fit the narrowband set to its lowest-median member
      var medians = {};
      var nb = [ "H", "S", "O" ];
      for ( var n = 0; n < nb.length; ++n )
         if ( chans[nb[n]] )
            medians[nb[n]] = Steps.medianOfCentre( chans[nb[n]].view, 0.6 );

      var refKey = Util.minMedianKey( medians );
      if ( refKey !== null && Object.keys( medians ).length > 1 )
      {
         Util.log( "linearfit", "reference is " + refKey );
         for ( var m = 0; m < nb.length; ++m )
            if ( chans[nb[m]] && nb[m] != refKey )
               Steps.linearFit( chans[nb[m]].view, chans[refKey].view );
      }
      else
         Util.log( "linearfit", "skipped: fewer than two narrowband channels" );

      // combine, then solve and calibrate the combined image.
      // Skipped entirely when the RGB group is absent (narrowband-only run).
      var rgbWin = null, rgbId = null;
      if ( chans.R && chans.G && chans.B )
      {
         rgbId = Util.freeWindowId( "RGB" );
         rgbWin = Steps.combineRGB( chans.R.view, chans.G.view, chans.B.view, rgbId );
         reg.add( rgbWin );
         Steps.solve( rgbWin.mainView );
         Steps.spfc( rgbWin.mainView, null );
         Steps.spccRGB( rgbWin.mainView );
      }
      else
         Util.log( "combine", "skipped: no RGB group supplied" );

      // save everything
      for ( var s = 0; s < Util.CHANNELS.length; ++s )
      {
         var sk = Util.CHANNELS[s];
         if ( !chans[sk] ) continue;
         chans[sk].window.saveAs(
            Util.outputPath( config.outputDir, sk, config.outputFormat ),
            false, false, false, false );
         results[sk] = chans[sk].window;
      }
      if ( rgbWin !== null )
      {
         rgbWin.saveAs( Util.outputPath( config.outputDir, rgbId, config.outputFormat ),
                        false, false, false, false );
         results.RGB = rgbWin;
      }

      // hand the keepers to the user, close the rest
      var keep = [ "L", "H", "S", "O" ];
      for ( var k = 0; k < keep.length; ++k )
         if ( chans[keep[k]] )
            reg.forget( chans[keep[k]].window );
      if ( rgbWin !== null )
         reg.forget( rgbWin );
      reg.closeAll();

      return results;
   }
   catch ( e )
   {
      Util.error( "pipeline", e.toString() );
      if ( !config.keepWindowsOnError )
         reg.closeAll();
      throw e;
   }
};

Pipeline.checkAbort = function()
{
   if ( console.abortRequested )
   {
      console.abort();
      throw new Error( "Aborted by user" );
   }
};
```

- [ ] **Step 2: Verify preflight catches a bad selection**

With PixInsight open, run the script and click Run with only L and R selected.
Expected: a message box listing `Missing required channel: G` and `Missing required channel: B`, and no windows opened.

- [ ] **Step 3: Commit**

```bash
git add lib/Pipeline.js
git commit -m "feat: add pipeline orchestration with preflight wall"
```

---

### Task 9: Selection dialog

**Files:**
- Create: `lib/UI.js`

**Interfaces:**
- Consumes: `Util.CHANNELS`, `Util.isBroadband`, `Util.keywordValue`, `Pipeline.readKeywords`
- Produces: `UI.SelectDialog(config)` — a `Dialog` subclass writing selections back into `config` (`paths`, `outputDir`, `outputFormat`, `useGraXpert`, `smoothing`, `validateOnly`)

- [ ] **Step 1: Write the implementation**

Create `lib/UI.js`:

```javascript
#include <pjsr/Sizer.jsh>
#include <pjsr/FrameStyle.jsh>
#include <pjsr/StdButton.jsh>
#include <pjsr/StdIcon.jsh>
#include <pjsr/TextAlign.jsh>

var UI = {};

UI.SelectDialog = function( config )
{
   this.__base__ = Dialog;
   this.__base__();

   var self = this;
   this.config = config;
   this.pathEdits = {};
   this.metaLabels = {};

   this.windowTitle = "LHSORGBPrep";

   var rows = new VerticalSizer;
   rows.spacing = 4;

   for ( var i = 0; i < Util.CHANNELS.length; ++i )
      rows.add( this.makeRow( Util.CHANNELS[i] ) );

   // output folder
   this.outLabel = new Label( this );
   this.outLabel.text = "Output folder:";
   this.outLabel.minWidth = 90;
   this.outLabel.textAlignment = TextAlign_Right | TextAlign_VertCenter;

   this.outEdit = new Edit( this );
   this.outEdit.readOnly = true;
   this.outEdit.text = config.outputDir || "";

   this.outButton = new PushButton( this );
   this.outButton.text = "Browse";
   this.outButton.onClick = function()
   {
      var d = new GetDirectoryDialog;
      d.caption = "Output folder";
      if ( d.execute() )
      {
         self.config.outputDir = d.directory;
         self.outEdit.text = d.directory;
      }
   };

   var outRow = new HorizontalSizer;
   outRow.spacing = 4;
   outRow.add( this.outLabel );
   outRow.add( this.outEdit, 100 );
   outRow.add( this.outButton );

   // options
   this.smoothing = new NumericControl( this );
   this.smoothing.label.text = "GraXpert smoothing:";
   this.smoothing.setRange( 0, 1 );
   this.smoothing.setPrecision( 2 );
   this.smoothing.setValue( config.smoothing );
   this.smoothing.onValueUpdated = function( v ) { self.config.smoothing = v; };
   this.smoothing.enabled = config.useGraXpert;

   this.formatLabel = new Label( this );
   this.formatLabel.text = "Output format:";
   this.formatLabel.minWidth = 90;
   this.formatLabel.textAlignment = TextAlign_Right | TextAlign_VertCenter;

   this.formatCombo = new ComboBox( this );
   this.formatCombo.addItem( "XISF" );
   this.formatCombo.addItem( "FITS" );
   this.formatCombo.currentItem = ( config.outputFormat == "fit" ) ? 1 : 0;
   this.formatCombo.onItemSelected = function( i )
   {
      self.config.outputFormat = ( i == 1 ) ? "fit" : "xisf";
   };

   var formatRow = new HorizontalSizer;
   formatRow.spacing = 4;
   formatRow.add( this.formatLabel );
   formatRow.add( this.formatCombo );
   formatRow.addStretch();

   this.useGraXpert = new CheckBox( this );
   this.useGraXpert.text = "Run GraXpert background extraction";
   this.useGraXpert.checked = config.useGraXpert;
   this.useGraXpert.onCheck = function( c )
   {
      self.config.useGraXpert = c;
      self.smoothing.enabled = c;
   };

   this.validateOnly = new CheckBox( this );
   this.validateOnly.text = "Validate only (check everything, run nothing)";
   this.validateOnly.checked = config.validateOnly;
   this.validateOnly.onCheck = function( c ) { self.config.validateOnly = c; };

   this.runButton = new PushButton( this );
   this.runButton.text = "Run";
   this.runButton.onClick = function() { self.ok(); };

   this.cancelButton = new PushButton( this );
   this.cancelButton.text = "Cancel";
   this.cancelButton.onClick = function() { self.cancel(); };

   var buttons = new HorizontalSizer;
   buttons.spacing = 6;
   buttons.addStretch();
   buttons.add( this.runButton );
   buttons.add( this.cancelButton );

   this.sizer = new VerticalSizer;
   this.sizer.margin = 8;
   this.sizer.spacing = 6;
   this.sizer.add( rows );
   this.sizer.add( outRow );
   this.sizer.add( formatRow );
   this.sizer.add( this.useGraXpert );
   this.sizer.add( this.smoothing );
   this.sizer.add( this.validateOnly );
   this.sizer.add( buttons );

   this.adjustToContents();
};

// MUST precede the prototype method assignments below: assigning the
// prototype replaces the object and would discard anything set on it first.
UI.SelectDialog.prototype = new Dialog;

/*
 * One row per channel. Selecting a broadband file immediately shows its
 * FITS metadata, so a missing FILTER keyword is visible while picking
 * rather than forty minutes into a run.
 */
UI.SelectDialog.prototype.makeRow = function( key )
{
   var self = this;

   var label = new Label( this );
   label.text = key + ":";
   label.minWidth = 90;
   label.textAlignment = TextAlign_Right | TextAlign_VertCenter;

   var edit = new Edit( this );
   edit.readOnly = true;
   edit.text = this.config.paths[key] || "";
   this.pathEdits[key] = edit;

   var meta = new Label( this );
   meta.minWidth = 170;
   this.metaLabels[key] = meta;

   var browse = new PushButton( this );
   browse.text = "Browse";
   browse.onClick = function()
   {
      var d = new OpenFileDialog;
      d.caption = "Select " + key + " master";
      d.multipleSelections = false;
      if ( d.execute() )
      {
         self.config.paths[key] = d.fileName;
         edit.text = d.fileName;
         self.showMetadata( key, d.fileName );
      }
   };

   var clear = new PushButton( this );
   clear.text = "Clear";
   clear.onClick = function()
   {
      self.config.paths[key] = "";
      edit.text = "";
      meta.text = "";
   };

   var row = new HorizontalSizer;
   row.spacing = 4;
   row.add( label );
   row.add( edit, 100 );
   row.add( meta );
   row.add( browse );
   row.add( clear );
   return row;
};

UI.SelectDialog.prototype.showMetadata = function( key, path )
{
   var meta = this.metaLabels[key];
   if ( !Util.isBroadband( key ) )
   {
      meta.text = "";
      return;
   }
   var kws = Pipeline.readKeywords( path );
   var filter = Util.keywordValue( kws, "FILTER" );
   if ( filter === null )
   {
      meta.text = "NO FILTER KEYWORD";
      meta.styleSheet = "QLabel { color: #ff5555; }";
   }
   else
   {
      meta.text = filter;
      meta.styleSheet = "QLabel { color: #55bb55; }";
   }
};
```

- [ ] **Step 2: Verify the dialog renders and reads metadata**

Run the script, select a broadband master with a `FILTER` keyword.
Expected: the filter name appears in green beside the row. Select one without the keyword; expected: `NO FILTER KEYWORD` in red.

- [ ] **Step 3: Commit**

```bash
git add lib/UI.js
git commit -m "feat: add channel selection dialog with FITS metadata display"
```

---

### Task 10: Entry point, saveable instance, and install

**Files:**
- Create: `LHSORGBPrep.js`
- Create: `README.md`

**Interfaces:**
- Consumes: everything
- Produces: the installable feature script

- [ ] **Step 1: Write the implementation**

Create `LHSORGBPrep.js`:

```javascript
#engine v8

#feature-id    LHSORGBPrep : Batch Processing > LHSORGBPrep
#feature-info  Prepares L/H/S/O/R/G/B masters and combines a calibrated RGB image.

#include "lib/Util.js"
#include "lib/Steps.js"
#include "lib/Pipeline.js"
#include "lib/UI.js"

#define SETTINGS_KEY "LHSORGBPrep/"

function defaultConfig()
{
   return {
      paths: { L: "", R: "", G: "", B: "", H: "", S: "", O: "" },
      outputDir: "",
      outputFormat: "xisf",
      useGraXpert: true,
      smoothing: 0.5,
      validateOnly: false,
      keepWindowsOnError: false
   };
}

/*
 * Restores from a saved process instance when launched from one, and
 * from Settings otherwise. This is what makes the script draggable to
 * the workspace as a reusable icon.
 */
function loadConfig()
{
   var config = defaultConfig();

   for ( var i = 0; i < Util.CHANNELS.length; ++i )
   {
      var key = Util.CHANNELS[i];
      if ( Parameters.has( "path_" + key ) )
         config.paths[key] = Parameters.getString( "path_" + key );
   }
   if ( Parameters.has( "outputDir" ) )
      config.outputDir = Parameters.getString( "outputDir" );
   if ( Parameters.has( "outputFormat" ) )
      config.outputFormat = Parameters.getString( "outputFormat" );
   if ( Parameters.has( "smoothing" ) )
      config.smoothing = Parameters.getReal( "smoothing" );
   if ( Parameters.has( "useGraXpert" ) )
      config.useGraXpert = Parameters.getBoolean( "useGraXpert" );

   if ( config.outputDir.length == 0 )
   {
      var saved = Settings.read( SETTINGS_KEY + "outputDir", DataType_String );
      if ( saved != null )
         config.outputDir = saved;
   }

   return config;
}

function saveConfig( config )
{
   for ( var i = 0; i < Util.CHANNELS.length; ++i )
   {
      var key = Util.CHANNELS[i];
      Parameters.set( "path_" + key, config.paths[key] );
   }
   Parameters.set( "outputDir", config.outputDir );
   Parameters.set( "outputFormat", config.outputFormat );
   Parameters.set( "smoothing", config.smoothing );
   Parameters.set( "useGraXpert", config.useGraXpert );

   Settings.write( SETTINGS_KEY + "outputDir", DataType_String, config.outputDir );
}

function main()
{
   console.show();

   var config = loadConfig();

   if ( config.outputDir.length == 0 && config.paths.L.length > 0 )
      config.outputDir = File.extractDrive( config.paths.L ) +
                         File.extractDirectory( config.paths.L );

   var dialog = new UI.SelectDialog( config );
   if ( !dialog.execute() )
      return;

   var problems = Pipeline.preflight( config );
   if ( problems.length > 0 )
   {
      new MessageBox( problems.join( "\\n" ),
                      "LHSORGBPrep: preflight failed",
                      StdIcon_Error, StdButton_Ok ).execute();
      return;
   }

   saveConfig( config );

   if ( config.validateOnly )
   {
      Util.log( "validate", "All checks passed. Nothing executed." );
      for ( var i = 0; i < Util.CHANNELS.length; ++i )
      {
         var k = Util.CHANNELS[i];
         if ( config.paths[k] )
            Util.log( "validate", k + " <- " + config.paths[k] );
      }
      Util.log( "validate", "Output -> " + config.outputDir );
      return;
   }

   var t = new ElapsedTime;
   var results = Pipeline.run( config );
   Util.log( "done", "Finished in " + t.text +
             ( results.RGB ? ". RGB is " + results.RGB.mainView.id
                           : ". No RGB produced (narrowband-only run)." ) );
}

main();
```

Create `README.md`:

```markdown
# LHSORGBPrep

Prepares L/H/S/O/R/G/B integrated masters and combines a calibrated RGB image.

## Install

PixInsight → **Script → Feature Scripts → Add**, select this directory,
then **Done**. The script appears under **Script → Batch Processing →
LHSORGBPrep**.

## Use

Select the masters, pick an output folder, and run. Tick **Validate only**
first on a new dataset: it checks files, FITS keywords, installed
processes and the output folder, prints the plan, and executes nothing.

To reuse a configuration, run once and drag the script's instance icon to
the workspace.

## Requirements

PixInsight 1.9.4 or later. Camera assumed to be an ASI2600MM; the QE
curve used is `Sony IMX411/455/461/533/571`.
```

- [ ] **Step 2: Install and verify the script loads**

Add the directory via Script → Feature Scripts → Add. Expected: `LHSORGBPrep` appears under Batch Processing with no parse errors in the console.

- [ ] **Step 3: Verify Validate only end-to-end**

Select a full L/R/G/B set, tick **Validate only**, Run.
Expected: console lists each channel and the output folder, no windows open, no processes run.

- [ ] **Step 4: Verify the instance saves**

Run once, then drag the instance icon to the workspace. Relaunch from the icon.
Expected: the dialog reopens with the same paths and output folder.

- [ ] **Step 5: Commit**

```bash
git add LHSORGBPrep.js README.md
git commit -m "feat: add entry point, saveable instance support, and install docs"
```

---

### Task 11: Acceptance run

**Files:**
- Modify: whichever wrapper proves wrong

- [ ] **Step 1: Run Validate only against the real dataset**

Expected: all checks pass. If the MARS database is missing, MGC will be the failure — resolve by downloading it in PixInsight before continuing.

- [ ] **Step 2: Run the full pipeline**

Expected result:
- Open windows: `L_work`, `RGB` (plus `H`/`S`/`O` if supplied)
- Output folder contains one file per supplied channel plus `RGB_processed.<ext>`
- The console log shows every stage with elapsed time
- Input files are unmodified

- [ ] **Step 3: Check the enum values actually took effect**

The two parameters that can be wrong *without erroring* are GraXpert's `correction` and SPFC/SPCC's `narrowbandMode`. Confirm GraXpert actually performed background extraction by comparing a channel before and after; a no-op means the constant is wrong. Fix in `lib/Steps.js` and re-run.

- [ ] **Step 4: Verify the RGB naming rule**

With an existing window named `RGB` open, run again.
Expected: the new image is named `RGB_1`, and the existing `RGB` is untouched.

- [ ] **Step 5: Verify the optional-group rules**

Four runs, each checked against its expected behaviour:

| Selection | Expected |
|---|---|
| L + R + G + B, no narrowband | RGB produced; LinearFit logged as skipped |
| L + H + O, no RGB | No RGB window; `combine` logged as skipped; H and O linear-fitted to the lower-median of the two |
| L + H only | No RGB; LinearFit skipped (fewer than two narrowband channels); H registered and saved |
| L + R + G (no B) | Preflight refuses with `Incomplete RGB set: missing B`; nothing opened |

- [ ] **Step 6: Verify GraXpert can be disabled**

Untick **Run GraXpert background extraction** and run.
Expected: the smoothing control greys out, no `[graxpert]` lines appear in the log, and the run completes. Rename or move the GraXpert module and confirm preflight still passes with it disabled.

- [ ] **Step 7: Commit any fixes**

```bash
git add -A
git commit -m "fix: correct process parameters found during acceptance run"
```
