# ASIAIR Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the Loom Frame Selector ingest a night of lights directly from a USB-C connected ZWO ASIAIR, review them, and copy the approved frames plus that night's flats into a chosen destination.

**Architecture:** A pure module (`lib/AsiairNames.js`) holds every rule that can be subtly wrong -- filename parsing, timestamp keys, session/night clustering, flat batching and matching -- as functions over plain arrays, covered by node tests in CI. A thin I/O module (`lib/Asiair.js`) does `/Volumes` detection and the depth-limited tree walk. `lib/NightDialog.js` is the picker. `FrameSelector.js` gains an import mode.

**Tech Stack:** PixInsight PJSR (JavaScript, V8 engine), node 20 for the CI suite via `ci/pjsr-shim.js`.

**Spec:** `docs/superpowers/specs/2026-09-20-asiair-import-design.md`

## Global Constraints

- `#engine v8` MUST remain line 1 of any dispatched script. Under legacy SpiderMonkey `class` is a reserved identifier and the script dies with a parse error.
- Every PJSR preprocessor macro needs its header included IN THE FILE THAT USES IT. `TextAlign_Right` requires `<pjsr/TextAlign.jsh>`, `BrushStyle_Empty` requires `<pjsr/BrushStyle.jsh>`. The suite includes headers itself and can mask a missing include.
- **No JS exception may escape a Qt event handler.** It unwinds through a destructor into `std::terminate` and kills PixInsight. Every handler body is wrapped in try/catch.
- Never launch a second PixInsight instance, never quit or kill the running one.
- No hardcoded personal paths in shipped code. Fixture paths in `script/selftest.js` are the sole exception.
- Scratch files go in `/tmp/agent-scratch`, never `$TMPDIR`.
- Tests: `node ci/run-tests.js`. Assertions use `check( name, actual, expected )`, which compares with `JSON.stringify`. PixInsight-only blocks are wrapped in `if ( IN_PIXINSIGHT ) ( function() { ... } )();`.
- Any new file under `script/lib/` MUST be added to the `LIBS` array in `ci/run-tests.js` or it is silently untested.
- `GAP_HOURS` default is 4, configurable.
- The ASIAIR card is READ-ONLY. No task in this plan may write, move, rename or delete anything on it.
- Squash before fast-forward. One commit per unit of work on main.

## File Structure

| File | Responsibility |
|------|----------------|
| `script/lib/AsiairNames.js` (new) | Pure: filename parsing, timestamp keys, sessions, nights, flat batching, flat matching. No I/O, no UI. |
| `script/lib/Asiair.js` (new) | I/O: `/Volumes` enumeration, shape detection, depth-limited tree walk. |
| `script/lib/NightDialog.js` (new) | The target/night picker dialog. |
| `script/FrameSelector.js` (modify) | `scanPaths` entry point; import mode; Light/Flat output split. |
| `script/selftest.js` (modify) | Tests. |
| `ci/run-tests.js` (modify) | Register the two new pure-ish libs in `LIBS`. |
| `README.md` (modify) | Document the feature. |

`AsiairNames.js` and `Asiair.js` are separate on purpose: the first loads and runs under the node shim, the second calls `FileFind` and `File.directoryExists` and belongs to the PixInsight-only set. Merging them would put the clustering rules out of CI's reach, which is the whole reason for the split.

---

### Task 1: Filename parsing

**Files:**
- Create: `script/lib/AsiairNames.js`
- Modify: `ci/run-tests.js` (add `"lib/AsiairNames.js"` to `LIBS`)
- Test: `script/selftest.js`

**Interfaces:**
- Produces: `AsiairNames.parseName( filename ) -> null | { type, target, exposure, bin, camera, filter, gain, stamp, rotation, temp, sequence }`. `stamp` is the raw `"YYYYMMDD-HHMMSS"` string. `camera` and `rotation` are `null` when absent. Returns `null` for a name that is not an ASIAIR frame.

- [ ] **Step 1: Write the failing tests**

Add to `runTests()` in `script/selftest.js`:

```js
   /* ---- ASIAIR filename parsing ---------------------------------------- */

   /*
    * The published grammar is wrong for real hardware. A frame off this
    * rig carries a SPACE in the target, a camera token and a rotation
    * token that no forum post mentions. Parsing left to right reads the
    * camera as the filter on every single frame, so the parser anchors on
    * the timestamp -- the one token that cannot be confused -- and works
    * outward from it.
    */
   check( "parseName real rig frame",
          AsiairNames.parseName(
             "Light_IC 1396A_180.0s_Bin1_2600MM_H_gain100_20260807-215716_180deg_-7.0C_0001.fit" ),
          { type: "Light", target: "IC 1396A", exposure: "180.0s", bin: "Bin1",
            camera: "2600MM", filter: "H", gain: "gain100",
            stamp: "20260807-215716", rotation: "180deg", temp: "-7.0C",
            sequence: "0001" } );

   // The published grammar, with no camera and no rotation token
   check( "parseName published grammar",
          AsiairNames.parseName(
             "Light_M42_10.0s_Bin1_S_gain360_20240320-203324_-10.0C_0001.fit" ),
          { type: "Light", target: "M42", exposure: "10.0s", bin: "Bin1",
            camera: null, filter: "S", gain: "gain360",
            stamp: "20240320-203324", rotation: null, temp: "-10.0C",
            sequence: "0001" } );

   // A flat has no target at all
   check( "parseName flat without target",
          AsiairNames.parseName(
             "Flat_1.0ms_Bin1_S_gain100_20240320-233122_-10.5C_0001.fit" ).type,
          "Flat" );
   check( "parseName flat target empty",
          AsiairNames.parseName(
             "Flat_1.0ms_Bin1_S_gain100_20240320-233122_-10.5C_0001.fit" ).target,
          "" );

   /*
    * A target may itself contain an exposure-shaped fragment. Taking the
    * LAST exposure token before the bin is what keeps "M42_30s" in the
    * target instead of stealing "30s" as the exposure.
    */
   check( "parseName target containing an exposure-shaped token",
          AsiairNames.parseName(
             "Light_M42_30s_180.0s_Bin1_2600MM_H_gain100_20260920-220000_180deg_-7.0C_0001.fit" ).target,
          "M42_30s" );
   check( "and the exposure is the real one",
          AsiairNames.parseName(
             "Light_M42_30s_180.0s_Bin1_2600MM_H_gain100_20260920-220000_180deg_-7.0C_0001.fit" ).exposure,
          "180.0s" );

   // Extensions are matched case-insensitively
   check( "parseName accepts .FIT",
          AsiairNames.parseName(
             "Light_M42_10.0s_Bin1_S_gain360_20240320-203324_-10.0C_0001.FIT" ) != null,
          true );
   check( "parseName accepts .fits",
          AsiairNames.parseName(
             "Light_M42_10.0s_Bin1_S_gain360_20240320-203324_-10.0C_0001.fits" ) != null,
          true );

   // Not an ASIAIR frame: no timestamp
   check( "parseName rejects a foreign name",
          AsiairNames.parseName( "masterDark_BIN-1_6248x4176.xisf" ), null );

   /*
    * Two timestamps is ambiguous, and guessing which one is the capture
    * time would assign the frame to the wrong night. Rejected outright.
    */
   check( "parseName rejects two timestamps",
          AsiairNames.parseName(
             "Light_20240101-010101_10.0s_Bin1_S_gain360_20240320-203324_-10.0C_0001.fit" ),
          null );
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `node ci/run-tests.js 2>&1 | tail -20`
Expected: FAIL, with `AsiairNames is not defined`.

- [ ] **Step 3: Write the implementation**

Create `script/lib/AsiairNames.js`:

```js
/*
 * Parsing ASIAIR frame names.
 *
 * The grammar published on forums and in third-party parsers is
 *
 *    {TYPE}_[{TARGET}_]{EXP}_Bin{N}_{FILTER}_gain{G}_{STAMP}_{TEMP}C_{SEQ}
 *
 * and it is WRONG for real hardware. An actual frame off a 2600MM reads
 *
 *    Light_IC 1396A_180.0s_Bin1_2600MM_H_gain100_20260807-215716_180deg_-7.0C_0001.fit
 *
 * -- a space in the target, a camera token and a rotation token, none of
 * which the grammar has. A positional parser reads "2600MM" as the filter
 * on every frame and nothing ever tells you.
 *
 * So: no positional parsing. Anchor on the timestamp, which is the one
 * token that cannot be mistaken for anything else, and work OUTWARD. The
 * tail of the name is rigid; the target is free text and must be whatever
 * is left over, not something the parser goes looking for.
 */

function AsiairNames() {}

AsiairNames.STAMP    = /^\d{8}-\d{6}$/;
AsiairNames.EXPOSURE = /^\d+(\.\d+)?(s|ms)$/;
AsiairNames.BIN      = /^Bin\d+$/;
AsiairNames.GAIN     = /^gain\d+$/;
AsiairNames.ROTATION = /^\d+deg$/;
AsiairNames.TEMP     = /^-?\d+(\.\d+)?C$/;
AsiairNames.SEQUENCE = /^\d+$/;
AsiairNames.EXTENSION = /\.(fit|fits)$/i;

AsiairNames.parseName = function( filename )
{
   if ( !AsiairNames.EXTENSION.test( filename ) )
      return null;

   var stem = filename.replace( AsiairNames.EXTENSION, "" );
   var t = stem.split( "_" );

   // 1. The stamp. Exactly one, or the name is not ours to interpret.
   var at = -1;
   for ( var i = 0; i < t.length; ++i )
      if ( AsiairNames.STAMP.test( t[i] ) )
      {
         if ( at >= 0 )
            return null;               // ambiguous, do not guess
         at = i;
      }
   if ( at < 1 )
      return null;

   // 2-3. gain sits immediately before the stamp, filter before gain.
   var gain = t[at-1];
   if ( !AsiairNames.GAIN.test( gain ) || at < 2 )
      return null;
   var filter = t[at-2];

   // 4. bin: the last one before the filter.
   var binAt = -1;
   for ( var b = at-3; b >= 0; --b )
      if ( AsiairNames.BIN.test( t[b] ) ) { binAt = b; break; }
   if ( binAt < 0 )
      return null;

   // 5. camera: whatever sits between the bin and the filter.
   var camera = ( binAt+1 <= at-3 )
              ? t.slice( binAt+1, at-2 ).join( "_" ) : null;

   // 6. exposure: the LAST exposure-shaped token before the bin. Taking
   //    the first would steal "30s" out of a target called "M42_30s".
   var expAt = -1;
   for ( var e = binAt-1; e >= 1; --e )
      if ( AsiairNames.EXPOSURE.test( t[e] ) ) { expAt = e; break; }
   if ( expAt < 0 )
      return null;

   // 7-8. Everything between the type and the exposure is the target. It
   //      may hold spaces and underscores; it is never searched for.
   var target = t.slice( 1, expAt ).join( "_" );

   // 9-11. The tail.
   var tail = t.slice( at+1 );
   var rotation = null, temp = null, sequence = null;
   for ( var k = 0; k < tail.length; ++k )
   {
      if ( rotation == null && AsiairNames.ROTATION.test( tail[k] ) )
         rotation = tail[k];
      else if ( AsiairNames.TEMP.test( tail[k] ) )
         temp = tail[k];
      else if ( AsiairNames.SEQUENCE.test( tail[k] ) )
         sequence = tail[k];
   }

   return { type: t[0], target: target, exposure: t[expAt], bin: t[binAt],
            camera: camera, filter: filter, gain: gain, stamp: t[at],
            rotation: rotation, temp: temp, sequence: sequence };
};
```

Add `"lib/AsiairNames.js"` to the `LIBS` array in `ci/run-tests.js`, before `"lib/Frames.js"`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `node ci/run-tests.js 2>&1 | tail -20`
Expected: `PASS`, with the run count up by 10.

- [ ] **Step 5: Commit**

```bash
git add script/lib/AsiairNames.js script/selftest.js ci/run-tests.js
git commit -m "Parse ASIAIR frame names by anchor rather than by position"
```

---

### Task 2: Timestamp keys

**Files:**
- Modify: `script/lib/AsiairNames.js`
- Test: `script/selftest.js`

**Interfaces:**
- Consumes: `parseName().stamp` from Task 1.
- Produces: `AsiairNames.stampKey( stamp ) -> Number | null` -- minutes since an arbitrary fixed origin, computed with `Date.UTC` on the wall-clock components. `null` for an unparseable or impossible stamp.

- [ ] **Step 1: Write the failing tests**

```js
   /* ---- ASIAIR timestamps ---------------------------------------------- */

   /*
    * The stamp is a wall-clock label in an unstated zone. It is turned
    * into a key with UTC arithmetic, NEVER by building a local Date: a
    * local Date makes clustering depend on the importing computer's
    * timezone, and across a DST boundary it mis-measures the gap. 00:30
    * to 05:00 on a US spring-forward date is 4.5 hours by the clock and
    * 3.5 elapsed -- enough to move a session boundary at a 4h threshold.
    */
   check( "stampKey difference is one hour",
          AsiairNames.stampKey( "20260807-220000" ) -
          AsiairNames.stampKey( "20260807-210000" ),
          60 );

   check( "stampKey crosses midnight",
          AsiairNames.stampKey( "20260808-003000" ) -
          AsiairNames.stampKey( "20260807-233000" ),
          60 );

   // The DST case: wall-clock arithmetic, so 4.5 hours stays 4.5 hours
   check( "stampKey ignores DST",
          AsiairNames.stampKey( "20260308-050000" ) -
          AsiairNames.stampKey( "20260308-003000" ),
          270 );

   check( "stampKey rejects rubbish", AsiairNames.stampKey( "nonsense" ), null );
   check( "stampKey rejects month 13", AsiairNames.stampKey( "20261301-000000" ), null );
   check( "stampKey rejects day 32", AsiairNames.stampKey( "20260132-000000" ), null );
   check( "stampKey rejects hour 25", AsiairNames.stampKey( "20260101-250000" ), null );
```

- [ ] **Step 2: Run to verify they fail**

Run: `node ci/run-tests.js 2>&1 | tail -20`
Expected: FAIL, `AsiairNames.stampKey is not a function`.

- [ ] **Step 3: Implement**

Append to `script/lib/AsiairNames.js`:

```js
/*
 * A comparison key in minutes, from the wall-clock components via
 * Date.UTC. Deliberately not `new Date( "..." )`: that resolves against
 * the importing machine's zone, so the same card would cluster
 * differently on a laptop in another timezone, and a DST transition would
 * move a session boundary. UTC arithmetic on the printed components is
 * monotone and has no discontinuities.
 */
AsiairNames.stampKey = function( stamp )
{
   if ( !AsiairNames.STAMP.test( String( stamp ) ) )
      return null;
   var y  = parseInt( stamp.substr( 0, 4 ), 10 );
   var mo = parseInt( stamp.substr( 4, 2 ), 10 );
   var d  = parseInt( stamp.substr( 6, 2 ), 10 );
   var h  = parseInt( stamp.substr( 9, 2 ), 10 );
   var mi = parseInt( stamp.substr( 11, 2 ), 10 );
   var s  = parseInt( stamp.substr( 13, 2 ), 10 );

   if ( mo < 1 || mo > 12 || d < 1 || d > 31 ||
        h > 23 || mi > 59 || s > 59 )
      return null;

   var ms = Date.UTC( y, mo-1, d, h, mi, s );
   // Date.UTC rolls 31 February over into March; a rolled date is not the
   // date that was printed, so it is not a date we accept.
   var back = new Date( ms );
   if ( back.getUTCMonth() != mo-1 || back.getUTCDate() != d )
      return null;

   return Math.floor( ms / 60000 );
};
```

- [ ] **Step 4: Run to verify they pass**

Run: `node ci/run-tests.js 2>&1 | tail -20`
Expected: `PASS`.

- [ ] **Step 5: Commit**

```bash
git add script/lib/AsiairNames.js script/selftest.js
git commit -m "Key ASIAIR timestamps by wall clock, not by local time"
```

---

### Task 3: Sessions from lights

**Files:**
- Modify: `script/lib/AsiairNames.js`
- Test: `script/selftest.js`

**Interfaces:**
- Consumes: `stampKey` from Task 2.
- Produces: `AsiairNames.GAP_HOURS` (Number, 4). `AsiairNames.sessions( frames, gapHours ) -> [ { first, last, frames: [] } ]` where `frames` is an array of objects each carrying at least `{ key }`, sorted, and `first`/`last` are keys. Frames with a null `key` are excluded.

- [ ] **Step 1: Write the failing tests**

```js
   /* ---- ASIAIR sessions -------------------------------------------------- */

   function lightAt( stamp, target )
   {
      return { key: AsiairNames.stampKey( stamp ), target: target || "A",
               type: "Light", stamp: stamp };
   }

   /*
    * Sessions are clustered from LIGHTS ONLY. Letting flats take part is
    * what would join two nights: a run of daytime flats at 10:00, 14:00
    * and 18:00 bridges every gap between a night ending at 06:00 and the
    * next starting at 20:00, and the two merge. Flats are assigned in
    * Task 5, after the boundaries are already fixed.
    */
   var oneNight = AsiairNames.sessions( [
      lightAt( "20260920-200000" ), lightAt( "20260920-230000" ),
      lightAt( "20260921-020000" ) ], 4 );
   check( "one unbroken session", oneNight.length, 1 );
   check( "and it holds every frame", oneNight[0].frames.length, 3 );

   var twoNights = AsiairNames.sessions( [
      lightAt( "20260920-200000" ), lightAt( "20260920-220000" ),
      lightAt( "20260921-200000" ), lightAt( "20260921-220000" ) ], 4 );
   check( "a night's gap splits the session", twoNights.length, 2 );

   // Exactly at the threshold is still one session; over it splits.
   check( "four hours exactly does not split",
          AsiairNames.sessions( [ lightAt( "20260920-200000" ),
                                  lightAt( "20260921-000000" ) ], 4 ).length, 1 );
   check( "four hours and a minute splits",
          AsiairNames.sessions( [ lightAt( "20260920-200000" ),
                                  lightAt( "20260921-000100" ) ], 4 ).length, 2 );

   // Input order must not matter
   check( "unsorted input clusters the same",
          AsiairNames.sessions( [ lightAt( "20260921-020000" ),
                                  lightAt( "20260920-200000" ),
                                  lightAt( "20260920-230000" ) ], 4 ).length, 1 );

   // Two targets in one night are ONE session
   check( "two targets share a session",
          AsiairNames.sessions( [ lightAt( "20260920-200000", "A" ),
                                  lightAt( "20260920-220000", "B" ) ], 4 ).length, 1 );

   check( "unparseable frames are dropped",
          AsiairNames.sessions( [ lightAt( "20260920-200000" ),
                                  { key: null, target: "A" } ], 4 )[0].frames.length, 1 );

   check( "no frames, no sessions", AsiairNames.sessions( [], 4 ), [] );
   check( "the default gap is four hours", AsiairNames.GAP_HOURS, 4 );
```

- [ ] **Step 2: Run to verify they fail**

Run: `node ci/run-tests.js 2>&1 | tail -20`
Expected: FAIL, `AsiairNames.sessions is not a function`.

- [ ] **Step 3: Implement**

```js
AsiairNames.GAP_HOURS = 4;

/*
 * Cluster by gap. Only LIGHTS are ever passed in -- see the note in
 * flatBatches for why including flats here silently merges nights.
 */
AsiairNames.sessions = function( frames, gapHours )
{
   var gap = ( gapHours == null ? AsiairNames.GAP_HOURS : gapHours ) * 60;

   var usable = [];
   for ( var i = 0; i < frames.length; ++i )
      if ( frames[i].key != null )
         usable.push( frames[i] );
   usable.sort( function( a, b ) { return a.key - b.key; } );

   var out = [];
   for ( var j = 0; j < usable.length; ++j )
   {
      var f = usable[j];
      var last = out.length ? out[out.length-1] : null;
      if ( last == null || f.key - last.last > gap )
         out.push( { first: f.key, last: f.key, frames: [ f ] } );
      else
      {
         last.last = f.key;
         last.frames.push( f );
      }
   }
   return out;
};
```

- [ ] **Step 4: Run to verify they pass**

Run: `node ci/run-tests.js 2>&1 | tail -20`
Expected: `PASS`.

- [ ] **Step 5: Commit**

```bash
git add script/lib/AsiairNames.js script/selftest.js
git commit -m "Cluster ASIAIR lights into observing sessions"
```

---

### Task 4: Nights within a session

**Files:**
- Modify: `script/lib/AsiairNames.js`
- Test: `script/selftest.js`

**Interfaces:**
- Consumes: `sessions()` from Task 3.
- Produces: `AsiairNames.nights( sessions ) -> [ { sessionIndex, target, first, last, count, filters: [], date } ]`, one per (session, target), ordered by `first` then `target`. `date` is `"YYYY-MM-DD"` taken from the night's first frame. `filters` is sorted and unique.

- [ ] **Step 1: Write the failing tests**

```js
   /* ---- ASIAIR nights ---------------------------------------------------- */

   function litFrame( stamp, target, filter )
   {
      return { key: AsiairNames.stampKey( stamp ), target: target,
               filter: filter, type: "Light", stamp: stamp };
   }

   var ss = AsiairNames.sessions( [
      litFrame( "20260920-200000", "IC 1396A", "H" ),
      litFrame( "20260920-210000", "IC 1396A", "O" ),
      litFrame( "20260920-220000", "M31",      "L" ),
      litFrame( "20260921-200000", "IC 1396A", "H" ) ], 4 );
   var nn = AsiairNames.nights( ss );

   // Two targets on night one, one on night two
   check( "three nights across two sessions", nn.length, 3 );
   check( "a night names its target", nn[0].target, "IC 1396A" );
   check( "a night counts its frames", nn[0].count, 2 );
   check( "a night lists its filters, sorted and unique", nn[0].filters, [ "H", "O" ] );
   check( "a night carries its date", nn[0].date, "2026-09-20" );
   check( "a night knows its session", nn[2].sessionIndex, 1 );

   /*
    * Two sessions on the SAME calendar date must stay two rows. This is
    * the behaviour gap clustering was chosen for over a noon-to-noon
    * observing date, which cannot express it.
    */
   var sameDate = AsiairNames.nights( AsiairNames.sessions( [
      litFrame( "20260920-010000", "M31", "L" ),
      litFrame( "20260920-220000", "M31", "L" ) ], 4 ) );
   check( "two sessions on one date stay separate", sameDate.length, 2 );

   check( "no sessions, no nights", AsiairNames.nights( [] ), [] );
```

- [ ] **Step 2: Run to verify they fail**

Run: `node ci/run-tests.js 2>&1 | tail -20`
Expected: FAIL, `AsiairNames.nights is not a function`.

- [ ] **Step 3: Implement**

```js
/*
 * A night is one target inside one session. The session supplies the
 * boundaries; the target supplies the split. Labelled with the date of
 * its own first frame, which for a session running past midnight is the
 * evening date -- the one the observer would call it.
 */
AsiairNames.nights = function( sessions )
{
   var out = [];
   for ( var s = 0; s < sessions.length; ++s )
   {
      var byTarget = Object.create( null );
      var order = [];
      var fr = sessions[s].frames;
      for ( var i = 0; i < fr.length; ++i )
      {
         var t = fr[i].target;
         if ( !( t in byTarget ) ) { byTarget[t] = []; order.push( t ); }
         byTarget[t].push( fr[i] );
      }
      for ( var k = 0; k < order.length; ++k )
      {
         var g = byTarget[order[k]];
         var filters = [], seen = Object.create( null );
         for ( var j = 0; j < g.length; ++j )
            if ( g[j].filter && !( g[j].filter in seen ) )
            {
               seen[g[j].filter] = true;
               filters.push( g[j].filter );
            }
         filters.sort();
         out.push( { sessionIndex: s, target: order[k],
                     first: g[0].key, last: g[g.length-1].key,
                     count: g.length, filters: filters,
                     date: AsiairNames.dateOf( g[0].stamp ) } );
      }
   }
   out.sort( function( a, b ) {
      return a.first - b.first || ( a.target < b.target ? -1 : a.target > b.target ? 1 : 0 );
   } );
   return out;
};

AsiairNames.dateOf = function( stamp )
{
   return stamp.substr( 0, 4 ) + "-" + stamp.substr( 4, 2 ) + "-" + stamp.substr( 6, 2 );
};
```

- [ ] **Step 4: Run to verify they pass**

Run: `node ci/run-tests.js 2>&1 | tail -20`
Expected: `PASS`.

- [ ] **Step 5: Commit**

```bash
git add script/lib/AsiairNames.js script/selftest.js
git commit -m "Split each ASIAIR session into a night per target"
```

---

### Task 5: Flat batches and their assignment

**Files:**
- Modify: `script/lib/AsiairNames.js`
- Test: `script/selftest.js`

**Interfaces:**
- Consumes: `sessions()` from Task 3.
- Produces: `AsiairNames.flatBatches( flats, gapHours ) -> [ { first, last, mid, frames: [] } ]` and `AsiairNames.assignBatches( batches, sessions ) -> [ sessionIndex ]`, one entry per batch, `-1` when there are no sessions.

- [ ] **Step 1: Write the failing tests**

```js
   /* ---- ASIAIR flat batches --------------------------------------------- */

   function flatAt( stamp, filter )
   {
      return { key: AsiairNames.stampKey( stamp ), filter: filter,
               type: "Flat", stamp: stamp };
   }

   var batches = AsiairNames.flatBatches( [
      flatAt( "20260921-060000", "H" ), flatAt( "20260921-060200", "H" ),
      flatAt( "20260922-060000", "H" ) ], 4 );
   check( "flats cluster into batches", batches.length, 2 );
   check( "a batch keeps its frames", batches[0].frames.length, 2 );

   /*
    * Assignment is per BATCH, never per flat. A batch straddling the
    * midpoint between two sessions would otherwise be torn in half: with
    * sessions ending 06:00 and starting 20:00, the midpoint is 13:00, and
    * a batch running 12:59 to 13:01 would send one flat each way.
    */
   var sess = AsiairNames.sessions( [
      lightAt( "20260920-200000" ), lightAt( "20260921-060000" ),
      lightAt( "20260921-200000" ), lightAt( "20260922-020000" ) ], 4 );
   check( "two sessions for the straddle case", sess.length, 2 );

   var straddle = AsiairNames.flatBatches( [
      flatAt( "20260921-125900", "H" ), flatAt( "20260921-130100", "H" ) ], 4 );
   check( "the straddling flats are one batch", straddle.length, 1 );
   check( "and go whole to one session",
          AsiairNames.assignBatches( straddle, sess ).length, 1 );

   // Dawn flats go to the session that just ended
   check( "dawn flats attach to the session just ended",
          AsiairNames.assignBatches(
             AsiairNames.flatBatches( [ flatAt( "20260921-063000", "H" ) ], 4 ),
             sess )[0],
          0 );

   // Dusk flats go to the session about to begin
   check( "dusk flats attach to the session about to begin",
          AsiairNames.assignBatches(
             AsiairNames.flatBatches( [ flatAt( "20260921-193000", "H" ) ], 4 ),
             sess )[0],
          1 );

   check( "no sessions means no owner",
          AsiairNames.assignBatches(
             AsiairNames.flatBatches( [ flatAt( "20260921-063000", "H" ) ], 4 ), [] )[0],
          -1 );
```

- [ ] **Step 2: Run to verify they fail**

Run: `node ci/run-tests.js 2>&1 | tail -20`
Expected: FAIL, `AsiairNames.flatBatches is not a function`.

- [ ] **Step 3: Implement**

```js
/*
 * Flats are clustered among THEMSELVES, and only after the session
 * boundaries are already fixed. Clustering them together with the lights
 * is what merges two nights: a run of daytime flats bridges the gap
 * between a night ending at 06:00 and the next beginning at 20:00, and
 * neither night can then be told apart from the other.
 */
AsiairNames.flatBatches = function( flats, gapHours )
{
   var raw = AsiairNames.sessions( flats, gapHours );
   for ( var i = 0; i < raw.length; ++i )
      raw[i].mid = Math.floor( ( raw[i].first + raw[i].last ) / 2 );
   return raw;
};

/*
 * A batch goes WHOLE to the session nearest its midpoint. Assigning each
 * flat on its own distance would bisect a batch lying across the midpoint
 * between two sessions, which is the one thing a batch must never do:
 * half a flat set calibrates nothing.
 */
AsiairNames.assignBatches = function( batches, sessions )
{
   var out = [];
   for ( var b = 0; b < batches.length; ++b )
   {
      var best = -1, bestDist = Infinity;
      for ( var s = 0; s < sessions.length; ++s )
      {
         var d = ( batches[b].mid < sessions[s].first )
               ? sessions[s].first - batches[b].mid
               : ( batches[b].mid > sessions[s].last
                 ? batches[b].mid - sessions[s].last : 0 );
         if ( d < bestDist ) { bestDist = d; best = s; }
      }
      out.push( best );
   }
   return out;
};
```

- [ ] **Step 4: Run to verify they pass**

Run: `node ci/run-tests.js 2>&1 | tail -20`
Expected: `PASS`.

- [ ] **Step 5: Commit**

```bash
git add script/lib/AsiairNames.js script/selftest.js
git commit -m "Assign whole flat batches to their nearest ASIAIR session"
```

---

### Task 6: Matching flats to a night's filters

**Files:**
- Modify: `script/lib/AsiairNames.js`
- Test: `script/selftest.js`

**Interfaces:**
- Consumes: nothing from earlier tasks at call time -- this is a pure match over already-read header records.
- Produces: `AsiairNames.matchFlats( lightFilters, flatRecords ) -> [ { filter, flats: [], strength } ]`, one entry per entry in `lightFilters`, in that order. `strength` is `"exact"`, `"weak"` or `"missing"`. A record is `{ path, filter, bin, camera, rotation }`, all read from FITS headers.

- [ ] **Step 1: Write the failing tests**

```js
   /* ---- ASIAIR flat matching -------------------------------------------- */

   /*
    * Matching is on HEADER values, for lights and flats alike. The
    * filename filter is never used to select or reject a flat, not even
    * to narrow the candidates: narrowing by a value the header can
    * contradict drops flats that actually match, and no later header
    * check can get them back.
    */
   var cand = [
      { path: "/f/h1.fit", filter: "H", bin: "Bin1", camera: "2600MM", rotation: "180deg" },
      { path: "/f/h2.fit", filter: "H", bin: "Bin1", camera: "2600MM", rotation: "180deg" },
      { path: "/f/o1.fit", filter: "O", bin: "Bin1", camera: "2600MM", rotation: "180deg" },
      { path: "/f/rot.fit", filter: "S", bin: "Bin1", camera: "2600MM", rotation: "090deg" }
   ];
   var want = [
      { filter: "H", bin: "Bin1", camera: "2600MM", rotation: "180deg" },
      { filter: "O", bin: "Bin1", camera: "2600MM", rotation: "180deg" },
      { filter: "S", bin: "Bin1", camera: "2600MM", rotation: "180deg" },
      { filter: "L", bin: "Bin1", camera: "2600MM", rotation: "180deg" }
   ];
   var m = AsiairNames.matchFlats( want, cand );

   check( "one result per light filter", m.length, 4 );
   check( "H matches both H flats", m[0].flats.length, 2 );
   check( "H is an exact match", m[0].strength, "exact" );

   /*
    * A rotation change between lights and flats invalidates the flat --
    * the dust is in a different place. The real filenames carry the
    * rotation, so this is checkable rather than hoped for.
    */
   check( "a rotated flat does not match", m[2].flats.length, 0 );
   check( "and is reported missing", m[2].strength, "missing" );

   check( "a filter with no flats at all is missing", m[3].strength, "missing" );
   check( "and carries no flats", m[3].flats.length, 0 );

   /*
    * A token absent from BOTH sides is not compared. Present on one side
    * only is a weak match: shown, and left for the observer to drop,
    * rather than silently accepted or silently discarded.
    */
   var weak = AsiairNames.matchFlats(
      [ { filter: "H", bin: "Bin1", camera: "2600MM", rotation: "180deg" } ],
      [ { path: "/f/x.fit", filter: "H", bin: "Bin1", camera: null, rotation: null } ] );
   check( "a half-specified flat is a weak match", weak[0].strength, "weak" );
   check( "but it is still offered", weak[0].flats.length, 1 );

   var bare = AsiairNames.matchFlats(
      [ { filter: "H", bin: "Bin1", camera: null, rotation: null } ],
      [ { path: "/f/x.fit", filter: "H", bin: "Bin1", camera: null, rotation: null } ] );
   check( "absent on both sides is not compared", bare[0].strength, "exact" );

   check( "a mismatched bin never matches",
          AsiairNames.matchFlats(
             [ { filter: "H", bin: "Bin2", camera: null, rotation: null } ],
             [ { path: "/f/x.fit", filter: "H", bin: "Bin1", camera: null, rotation: null } ]
          )[0].strength, "missing" );
```

- [ ] **Step 2: Run to verify they fail**

Run: `node ci/run-tests.js 2>&1 | tail -20`
Expected: FAIL, `AsiairNames.matchFlats is not a function`.

- [ ] **Step 3: Implement**

```js
/*
 * Filter and bin must agree. Camera and rotation must agree WHERE BOTH
 * sides state them -- a rotation change puts the dust somewhere else, and
 * the real filenames carry the angle, so this is worth checking. Stated
 * on one side only is "weak": offered, flagged, and droppable, because
 * refusing it would lose good flats and accepting it silently would hide
 * a real mismatch.
 *
 * Gain is deliberately not compared. A flat is a ratio, and WBPP groups
 * flats by gain itself.
 */
AsiairNames.matchFlats = function( lightFilters, flatRecords )
{
   var out = [];
   for ( var i = 0; i < lightFilters.length; ++i )
   {
      var want = lightFilters[i];
      var hits = [], weak = false;

      for ( var j = 0; j < flatRecords.length; ++j )
      {
         var f = flatRecords[j];
         if ( f.filter != want.filter || f.bin != want.bin )
            continue;

         var soft = false, hard = false;
         var pairs = [ [ want.camera, f.camera ], [ want.rotation, f.rotation ] ];
         for ( var p = 0; p < pairs.length; ++p )
         {
            var a = pairs[p][0], b = pairs[p][1];
            if ( a != null && b != null ) { if ( a != b ) hard = true; }
            else if ( a != null || b != null ) soft = true;
         }
         if ( hard )
            continue;
         if ( soft )
            weak = true;
         hits.push( f );
      }

      out.push( { filter: want.filter, flats: hits,
                  strength: hits.length == 0 ? "missing" : ( weak ? "weak" : "exact" ) } );
   }
   return out;
};
```

- [ ] **Step 4: Run to verify they pass**

Run: `node ci/run-tests.js 2>&1 | tail -20`
Expected: `PASS`.

- [ ] **Step 5: Commit**

```bash
git add script/lib/AsiairNames.js script/selftest.js
git commit -m "Match ASIAIR flats on header filter, bin, camera and rotation"
```

---

### Task 7: Card detection

**Files:**
- Create: `script/lib/Asiair.js`
- Modify: `ci/run-tests.js` (add `"lib/Asiair.js"` to `LIBS`)
- Test: `script/selftest.js`

**Interfaces:**
- Produces: `Asiair.MOUNTS` (`"/Volumes"`), `Asiair.looksLikeCard( root ) -> Boolean`, `Asiair.detect() -> [ rootPath ]`.

`looksLikeCard` is the whole decision and is testable with a real directory; `detect` enumerates `/Volumes` and is PixInsight-only.

- [ ] **Step 1: Write the failing tests**

```js
   /* ---- ASIAIR detection ------------------------------------------------- */

   /*
    * By SHAPE, never by volume name. The mounted volume is variously
    * BOOT, "EMMC Images", "SD Images" or "USB Images" depending on model,
    * firmware and which storage was recording. That list would rot; the
    * Autorun/Plan layout will not.
    */
   ( function()
   {
      var root = "/tmp/agent-scratch/asiair-detect";
      File.createDirectory( root + "/Plan/Light", true );
      check( "a Plan/Light tree is a card", Asiair.looksLikeCard( root ), true );

      var other = "/tmp/agent-scratch/asiair-detect-auto";
      File.createDirectory( other + "/Autorun/Light", true );
      check( "an Autorun/Light tree is a card", Asiair.looksLikeCard( other ), true );

      var no = "/tmp/agent-scratch/asiair-detect-not";
      File.createDirectory( no + "/Pictures", true );
      check( "an ordinary folder is not", Asiair.looksLikeCard( no ), false );
      check( "and neither is one that is not there",
             Asiair.looksLikeCard( "/tmp/agent-scratch/no-such-thing" ), false );
   } )();
```

- [ ] **Step 2: Run to verify they fail**

Run: `node ci/run-tests.js 2>&1 | tail -20`
Expected: FAIL, `Asiair is not defined`.

- [ ] **Step 3: Implement**

Create `script/lib/Asiair.js`:

```js
/*
 * Finding and reading an ASIAIR card.
 *
 * This file does I/O and therefore does NOT run under the node shim's
 * honest-or-absent rule for everything it touches; the rules that can be
 * subtly wrong live next door in AsiairNames.js, where CI can reach them.
 * Keep it that way: anything here that starts making a DECISION belongs
 * over there.
 */

function Asiair() {}

Asiair.MOUNTS = "/Volumes";

/*
 * Two directory tests, no walking. A stalled network mount costs two
 * calls rather than a traversal -- PJSR gives no way to preempt a hung
 * filesystem call, so the only defence is to make very few of them.
 */
Asiair.looksLikeCard = function( root )
{
   try
   {
      return File.directoryExists( root + "/Plan/Light" ) ||
             File.directoryExists( root + "/Autorun/Light" );
   }
   catch ( e ) { return false; }
};

/*
 * Enumerates /Volumes one level deep. macOS only, stated rather than
 * assumed. Cancellable between volumes, not inside one.
 */
Asiair.detect = function( shouldStop )
{
   var found = [];
   var find = new FileFind;
   if ( !find.begin( Asiair.MOUNTS + "/*" ) )
      return found;
   do
   {
      if ( shouldStop && shouldStop() )
         break;
      if ( !find.isDirectory || find.name == "." || find.name == ".." )
         continue;
      var root = Asiair.MOUNTS + "/" + find.name;
      if ( Asiair.looksLikeCard( root ) )
         found.push( root );
   }
   while ( find.next() );
   return found;
};
```

Add `"lib/Asiair.js"` to `LIBS` in `ci/run-tests.js`, after `"lib/AsiairNames.js"`.

- [ ] **Step 4: Run to verify they pass**

Run: `node ci/run-tests.js 2>&1 | tail -20`
Expected: `PASS`.

- [ ] **Step 5: Commit**

```bash
git add script/lib/Asiair.js script/selftest.js ci/run-tests.js
git commit -m "Detect an ASIAIR card by its layout, not by its volume name"
```

---

### Task 8: The tree walk

**Files:**
- Modify: `script/lib/Asiair.js`
- Test: `script/selftest.js`

**Interfaces:**
- Consumes: `AsiairNames.parseName`, `AsiairNames.stampKey`, `Asiair.looksLikeCard`.
- Produces: `Asiair.scanCard( root, onProgress, shouldStop ) -> { lights: [], flats: [], unparseable: [], cancelled: Boolean, removed: Boolean }`. Each entry is `parseName()`'s result plus `{ path, key, source }`, where `source` is `"Plan"` or `"Autorun"`.

- [ ] **Step 1: Write the failing tests**

```js
   /* ---- ASIAIR card scan ------------------------------------------------- */

   ( function()
   {
      var root = "/tmp/agent-scratch/asiair-card";
      File.createDirectory( root + "/Plan/Light/IC 1396A", true );
      File.createDirectory( root + "/Autorun/Flat", true );
      File.writeTextFile( root + "/Plan/Light/IC 1396A/" +
         "Light_IC 1396A_180.0s_Bin1_2600MM_H_gain100_20260807-215716_180deg_-7.0C_0001.fit", "x" );
      File.writeTextFile( root + "/Autorun/Flat/" +
         "Flat_1.0ms_Bin1_2600MM_H_gain100_20260808-061500_180deg_-7.0C_0001.fit", "x" );
      File.writeTextFile( root + "/Plan/Light/IC 1396A/notes.txt", "x" );

      var r = Asiair.scanCard( root );
      check( "the walk finds the light", r.lights.length, 1 );
      check( "and the flat", r.flats.length, 1 );
      check( "and records where the light came from", r.lights[0].source, "Plan" );
      check( "and where the flat came from", r.flats[0].source, "Autorun" );
      check( "and the target from the folder, not the name",
             r.lights[0].target, "IC 1396A" );
      check( "and a sort key", r.lights[0].key != null, true );

      /*
       * A file that is not an ASIAIR frame is COUNTED, not silently
       * dropped. A scan that quietly ignores things is how a missing
       * night gets blamed on the card.
       */
      check( "a foreign file is reported", r.unparseable.length, 1 );

      check( "a card that is not there reads as removed",
             Asiair.scanCard( "/tmp/agent-scratch/no-card" ).removed, true );
   } )();
```

- [ ] **Step 2: Run to verify they fail**

Run: `node ci/run-tests.js 2>&1 | tail -20`
Expected: FAIL, `Asiair.scanCard is not a function`.

- [ ] **Step 3: Implement**

Append to `script/lib/Asiair.js`:

```js
Asiair.SOURCES = [ "Plan", "Autorun" ];

/*
 * Depth-limited to the layout that exists: {Plan,Autorun}/Light/<target>
 * and {Plan,Autorun}/Flat. Never recursive, so a symlink cycle on the
 * card is unreachable rather than merely unlikely.
 *
 * The TARGET comes from the directory name, never from the filename. Some
 * firmware omits it from the name entirely, and the folder is the thing
 * the ASIAIR itself organises by.
 */
Asiair.scanCard = function( root, onProgress, shouldStop )
{
   var out = { lights: [], flats: [], unparseable: [],
               cancelled: false, removed: false };

   if ( !Asiair.looksLikeCard( root ) )
   {
      out.removed = true;
      return out;
   }

   function take( dir, target, source, into )
   {
      var find = new FileFind;
      if ( !find.begin( dir + "/*" ) )
         return true;
      do
      {
         if ( shouldStop && shouldStop() ) { out.cancelled = true; return false; }
         if ( find.isDirectory || find.name == "." || find.name == ".." )
            continue;
         var path = dir + "/" + find.name;
         var f = AsiairNames.parseName( find.name );
         if ( f == null ) { out.unparseable.push( path ); continue; }
         f.path = path;
         f.source = source;
         f.key = AsiairNames.stampKey( f.stamp );
         if ( target != null )
            f.target = target;
         into.push( f );
         if ( onProgress )
            onProgress( out.lights.length + out.flats.length );
      }
      while ( find.next() );
      return true;
   }

   for ( var s = 0; s < Asiair.SOURCES.length; ++s )
   {
      var src = Asiair.SOURCES[s];
      var lightDir = root + "/" + src + "/Light";

      if ( File.directoryExists( lightDir ) )
      {
         var targets = new FileFind;
         if ( targets.begin( lightDir + "/*" ) )
            do
            {
               if ( !targets.isDirectory || targets.name == "." || targets.name == ".." )
                  continue;
               if ( !take( lightDir + "/" + targets.name, targets.name, src, out.lights ) )
                  return out;
            }
            while ( targets.next() );
      }

      var flatDir = root + "/" + src + "/Flat";
      if ( File.directoryExists( flatDir ) )
         if ( !take( flatDir, null, src, out.flats ) )
            return out;
   }
   return out;
};
```

- [ ] **Step 4: Run to verify they pass**

Run: `node ci/run-tests.js 2>&1 | tail -20`
Expected: `PASS`.

- [ ] **Step 5: Commit**

```bash
git add script/lib/Asiair.js script/selftest.js
git commit -m "Walk an ASIAIR card without recursing"
```

---

### Task 9: `scanPaths` -- an explicit-path entry point

**Files:**
- Modify: `script/FrameSelector.js` (around the existing `scan` at line ~390)
- Test: `script/selftest.js`

**Interfaces:**
- Produces: `FrameSelector.prototype.scanPaths( paths, ... )` with the same contract `scan( folder )` has today. `scan( folder )` becomes a caller of it.

This task changes NO behaviour. It is a refactor whose only job is to make a night expressible, and its test is a regression guard.

- [ ] **Step 1: Read the existing contract**

Run: `sed -n '380,470p' script/FrameSelector.js`

Note every element of it: the `reading` and `measuring` progress callbacks, the `CoreApplication.processEvents` pumping, the Cancel check, and the shape of the cancelled result. All of them must survive. "Thin wrapper" must NOT become "drops the progress bar and ignores Cancel" -- ordinary folder scanning is this feature's most-used path and a silent regression there is worse than the new feature is good.

- [ ] **Step 2: Write the failing test**

```js
   /* ---- scanPaths keeps the whole of scan's contract ---------------------- */

   /*
    * A night is a filtered list of paths that may span Plan and Autorun,
    * so scan( folder ) cannot express it. Extracting scanPaths is the
    * smallest change that can -- and the risk is not the new path, it is
    * silently dropping progress or Cancel from the old one.
    */
   check( "scanPaths exists", typeof FrameSelector.prototype.scanPaths, "function" );
   check( "scan still exists", typeof FrameSelector.prototype.scan, "function" );
```

Plus, inside the existing `if ( IN_PIXINSIGHT )` populated-dialog block, assert that a `scanPaths` call over two explicit paths reports progress at least once and honours a `shouldStop` that returns true immediately.

- [ ] **Step 3: Run to verify it fails**

Run: `node ci/run-tests.js 2>&1 | tail -20`
Expected: FAIL on `scanPaths exists`.

- [ ] **Step 4: Implement**

Move the body of `scan` into `scanPaths( paths, ... )`, keeping every callback and the Cancel check exactly as they are. Reduce `scan( folder )` to enumerating the folder into a path array and delegating.

- [ ] **Step 5: Run the whole suite under node AND under PixInsight**

Run: `node ci/run-tests.js 2>&1 | tail -20`
Expected: `PASS`, with the run count up by 2 and NOTHING else changed.

Then dispatch `script/selftest.js` to the already-running PixInsight — do not launch a second instance — and confirm the dialog tests still pass.

- [ ] **Step 6: Commit**

```bash
git add script/FrameSelector.js script/selftest.js
git commit -m "Let the Frame Selector scan an explicit list of frames"
```

---

### Task 10: The night picker dialog

**Files:**
- Create: `script/lib/NightDialog.js`
- Test: `script/selftest.js` (PixInsight-only)

**Interfaces:**
- Consumes: `Asiair.scanCard`, `AsiairNames.sessions`, `nights`, `flatBatches`, `assignBatches`.
- Produces: `NightDialog( cardRoot )` with `execute()` and, on accept, `selectedNight` (a night record) and `selectedFlats` (an array of flat records the observer left ticked).

- [ ] **Step 1: Write the failing test**

Inside `if ( IN_PIXINSIGHT )`, build a scratch card under `/tmp/agent-scratch` with two targets and a flat batch, construct `NightDialog` against it, and assert the tree lists the expected target/night rows and that each night's flat summary reports the expected per-filter counts. Do NOT call `execute()` — a modal dialog in a test run stops the suite.

- [ ] **Step 2: Run under PixInsight to verify it fails**

Dispatch `script/selftest.js` to the running instance.
Expected: FAIL, `NightDialog is not defined`.

- [ ] **Step 3: Implement**

Create `script/lib/NightDialog.js`. Requirements:

- `#include <pjsr/Sizer.jsh>`, `<pjsr/FrameStyle.jsh>`, `<pjsr/TextAlign.jsh>`, `<pjsr/StdButton.jsh>`, `<pjsr/StdIcon.jsh>` — every macro family used, in THIS file. The suite includes headers itself and will mask a missing include.
- A `TreeBox` of targets, each with its nights as children: date, clock span, frame count, filters.
- A flats panel for the selected night: one row per light filter, showing count, time, `strength`, and a tick to drop a set.
- A filter with `strength === "missing"` is shown in red and stays visible.
- **Every event handler body wrapped in try/catch.** An exception escaping a Qt handler unwinds through a destructor into `std::terminate` and takes PixInsight with it. This has already cost this project two crashes.
- `TreeBox.selectedNodes` is READ-ONLY. Use `currentNode` and `node.selected = true`.
- Do not call `super.cancel()` on a native `Dialog` method — it throws. Release, then cancel.

- [ ] **Step 4: Run under PixInsight to verify it passes**

Expected: `PASS`.

- [ ] **Step 5: Commit**

```bash
git add script/lib/NightDialog.js script/selftest.js
git commit -m "Pick a target and a night off the card"
```

---

### Task 11: Import mode, and never writing to the card

**Files:**
- Modify: `script/FrameSelector.js`
- Test: `script/selftest.js`

**Interfaces:**
- Consumes: `NightDialog`, `scanPaths`.
- Produces: `FrameSelector.prototype.importMode` (Boolean), `FrameSelector.resolvePath( p ) -> String`, `FrameSelector.destinationIsOnCard( dest, cardRoot ) -> Boolean`.

- [ ] **Step 1: Write the failing tests**

```js
   /* ---- the card is never written to ------------------------------------- */

   /*
    * A mandatory destination does not by itself protect the card.
    * Comparing the two chosen directories as strings passes
    * /Volumes/ASIAIR/export, and passes a symlink pointing into the card.
    * Both paths are resolved first, and the check is containment.
    */
   check( "a folder inside the card is refused",
          FrameSelector.destinationIsOnCard( "/Volumes/ASIAIR/export", "/Volumes/ASIAIR" ),
          true );
   check( "the card root itself is refused",
          FrameSelector.destinationIsOnCard( "/Volumes/ASIAIR", "/Volumes/ASIAIR" ),
          true );
   check( "a folder elsewhere is allowed",
          FrameSelector.destinationIsOnCard( "/Volumes/A008/M42", "/Volumes/ASIAIR" ),
          false );
   /*
    * A prefix match on the raw strings would call this a hit. The check
    * is on path COMPONENTS, not on characters.
    */
   check( "a sibling with a shared prefix is allowed",
          FrameSelector.destinationIsOnCard( "/Volumes/ASIAIR-backup", "/Volumes/ASIAIR" ),
          false );
```

Plus a PixInsight-only assertion that a symlink at `/tmp/agent-scratch/link-to-card` pointing at a scratch card resolves and is refused.

- [ ] **Step 2: Run to verify they fail**

Run: `node ci/run-tests.js 2>&1 | tail -20`
Expected: FAIL, `FrameSelector.destinationIsOnCard is not a function`.

- [ ] **Step 3: Implement**

- `destinationIsOnCard` resolves both paths, splits on `/`, and tests component-wise containment.
- In import mode, Run stays disabled until a destination is chosen AND `destinationIsOnCard` is false, with the reason shown.
- The destructive same-folder path (`convertInPlace` and delete-in-place) is gated on `!this.importMode`. Gate on the MODE, not on a path comparison: a mode flag cannot be defeated by a symlink.

- [ ] **Step 4: Run to verify they pass**

Run: `node ci/run-tests.js 2>&1 | tail -20`
Expected: `PASS`.

- [ ] **Step 5: Commit**

```bash
git add script/FrameSelector.js script/selftest.js
git commit -m "Refuse to write anything back to the ASIAIR card"
```

---

### Task 12: Writing the Light and Flat folders

**Files:**
- Modify: `script/FrameSelector.js`
- Test: `script/selftest.js`

**Interfaces:**
- Consumes: `AsiairNames.matchFlats`, import mode from Task 11.
- Produces: `FrameSelector.importManifest( approvedLights, flatMatches ) -> { lights: [ {src,dst} ], flats: [ {src,dst} ], collisions: [] }` and `FrameSelector.convertFlats( paths, destination )`.

- [ ] **Step 1: Write the failing tests**

```js
   /* ---- the import manifest ---------------------------------------------- */

   /*
    * Flats follow the LIGHTS THAT SURVIVED. A filter whose lights were
    * all rejected gets no flats: copying calibration frames for data that
    * is not there is just clutter in the destination.
    */
   var man = FrameSelector.importManifest(
      [ { path: "/c/Plan/Light/M42/Light_M42_10.0s_Bin1_S_gain360_20240320-203324_-10.0C_0001.fit",
          filter: "S" } ],
      [ { filter: "S", flats: [ { path: "/c/Autorun/Flat/Flat_1.0ms_Bin1_S_gain100_20240320-233122_-10.5C_0001.fit" } ] },
        { filter: "L", flats: [ { path: "/c/Autorun/Flat/Flat_1.0ms_Bin1_L_gain100_20240320-233500_-10.5C_0001.fit" } ] } ],
      "/dest" );

   check( "the approved light is written", man.lights.length, 1 );
   check( "into a Light folder",
          man.lights[0].dst, "/dest/Light/Light_M42_10.0s_Bin1_S_gain360_20240320-203324_-10.0C_0001.xisf" );
   check( "its flats come along", man.flats.length, 1 );
   check( "into a Flat folder",
          man.flats[0].dst, "/dest/Flat/Flat_1.0ms_Bin1_S_gain100_20240320-233122_-10.5C_0001.xisf" );

   /*
    * Two sources landing on one output name cannot be resolved by
    * overwriting: whichever is written second wins and a frame is lost
    * without a word. Refused outright, whatever the overwrite setting.
    */
   var clash = FrameSelector.importManifest(
      [ { path: "/c/Plan/Light/M42/Light_M42_10.0s_Bin1_S_gain360_20240320-203324_-10.0C_0001.fit", filter: "S" },
        { path: "/c/Autorun/Light/M42/Light_M42_10.0s_Bin1_S_gain360_20240320-203324_-10.0C_0001.fit", filter: "S" } ],
      [], "/dest" );
   check( "a source collision is caught", clash.collisions.length, 1 );
   check( "and nothing is scheduled", clash.lights.length, 0 );
```

- [ ] **Step 2: Run to verify they fail**

Run: `node ci/run-tests.js 2>&1 | tail -20`
Expected: FAIL, `FrameSelector.importManifest is not a function`.

- [ ] **Step 3: Implement**

- `importManifest` builds both lists, rewrites the extension to `.xisf`, and detects two sources mapping to one destination. Any collision empties the schedule.
- The manifest is built ONCE at Run and both lists are frozen together, so the flat set cannot drift from the light set mid-copy.
- `convertFlats` opens and saves. It MUST NOT call `FrameSelector.runOutputRoutine`: that runs SubframeSelector routine 1 first — the comment in the source says "Measuring first is not optional" — which means star detection, and a flat has no stars to detect.
- For lights, pass an explicit empty output postfix. SubframeSelector's default is `_a`, and the expected-output mapping must be built with the same postfix or the accounting of what was written is wrong.

- [ ] **Step 4: Run to verify they pass**

Run: `node ci/run-tests.js 2>&1 | tail -20`
Expected: `PASS`.

- [ ] **Step 5: Commit**

```bash
git add script/FrameSelector.js script/selftest.js
git commit -m "Write approved lights and their flats into the destination"
```

---

### Task 13: Verifying what was written

**Files:**
- Modify: `script/FrameSelector.js`
- Test: `script/selftest.js` (PixInsight-only — this opens real images)

**Interfaces:**
- Consumes: the manifest from Task 12.
- Produces: `FrameSelector.verifyImported( src, dst ) -> { ok, reason }`.

- [ ] **Step 1: Write the failing test**

Inside `if ( IN_PIXINSIGHT )`, write a real frame out as XISF, verify it passes, then write a file of the right geometry with `FILTER` stripped and assert it fails with a reason naming `FILTER`.

- [ ] **Step 2: Run under PixInsight to verify it fails**

Expected: FAIL, `FrameSelector.verifyImported is not a function`.

- [ ] **Step 3: Implement**

- Reopen the written file. Compare geometry as `Cache.verifyStoredFile` does, AND check that `FILTER`, `EXPTIME`, `GAIN`, `DATE-OBS` survived, plus the CFA keywords where the source had them.
- **A file that fails verification is DELETED before the failure is reported.** Leaving it permanently blocks the retry: the existing output path has `if ( !overwrite && File.exists( ... ) ) blocked.push( src )`, so the half-written file would refuse its own replacement forever.
- `ImageWindow.open` raises a MODAL error box for a missing file. Check `File.exists` first — an unattended test run that stops on a modal box looks like a hang.
- Record in a comment that this cannot detect altered pixels: converting to XISF re-encodes, so the copy cannot be hashed against the card. That is an accepted limit of the XISF choice, not an oversight.

- [ ] **Step 4: Run under PixInsight to verify it passes**

Expected: `PASS`.

- [ ] **Step 5: Commit**

```bash
git add script/FrameSelector.js script/selftest.js
git commit -m "Verify each imported frame, and clear it away when it fails"
```

---

### Task 14: Documentation and release

**Files:**
- Modify: `README.md`, `script/lib/Util.js`

- [ ] **Step 1: Write the README section**

Under the existing Frame Selector section, add "Importing from an ASIAIR". Cover: plugging in over USB-C; detection by layout rather than volume name; the target/night list and what a night means; that flats are matched on header filter, bin, camera and rotation, and that a rotation change between lights and flats is reported rather than accepted; that a destination is mandatory and the card is never written to; the `Light`/`Flat` layout; and the three accepted limits from the spec, stated plainly.

- [ ] **Step 2: Bump the version**

`Util.LOOM_VERSION` is already `"0.1.4"` and `v0.1.3` is the released tag. Set it to `"0.2.0"` — this is a feature, not a fix.

- [ ] **Step 3: Run the whole suite both ways**

Run: `node ci/run-tests.js 2>&1 | tail -20`
Expected: `PASS`, zero failures.

Then dispatch `script/selftest.js` to the running PixInsight and confirm the same.

**Read the output.** Do not pipe it through `grep -E "^PASS|^FAIL"` inside an `&&` chain: that pattern matches FAIL too, the chain continues, and this project has already committed twice with failing tests that way.

- [ ] **Step 4: Squash and commit**

```bash
git add README.md script/lib/Util.js
git commit -m "Document importing from an ASIAIR"
```

- [ ] **Step 5: Release**

Squash the branch to one commit before fast-forwarding to main, then tag:

```bash
git tag v0.2.0 && git push origin main --tags && git push github main --tags
```

CI builds and publishes the zip from the tag. That is the only way a release is ever built.

---

## Self-Review

**Spec coverage.** Filename parsing T1; timestamps T2; sessions T3; nights T4; flat batching and assignment T5; flat matching T6; detection T7; the tree walk T8; `scanPaths` T9; the dialog T10; mandatory destination and card protection T11; the Light/Flat split, collisions and flats-without-measurement T12; verification and cleanup T13; docs T14. The three accepted limits are recorded in code comments in T13 and in the README in T14.

**Placeholders.** None. Every code step carries the code. T10 and T13 describe tests rather than quoting them, because both need a live `ImageWindow` and a real frame, and inventing that fixture text here would be a guess at what the machine has — the requirements those tests must meet are listed instead.

**Type consistency.** `parseName` returns the same eleven fields everywhere. `stampKey` returns minutes, and `sessions`/`flatBatches`/`assignBatches` all compare in minutes. `matchFlats` takes header records `{ path, filter, bin, camera, rotation }` and T12's manifest consumes `{ filter, flats: [ { path } ] }`, which is what it returns. `scanCard` adds `{ path, key, source }` to a parsed frame, and T3's `sessions` needs only `key`, which it has.
