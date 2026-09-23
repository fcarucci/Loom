# Approval criteria, filmstrip, anomaly flags, SNR — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework the Frame Selector review into SubframeStudio's shape: an Approval criteria panel with visible/editable limits, a thumbnail filmstrip with red crosses, separate advisory anomaly tags, and an SNR column.

**Architecture:** Every decision is a pure function in `script/lib/Frames.js`, tested under node; `script/FrameSelector.js` only draws what those return. The channel recompute moves into `Frames` so the prefill ordering is node-testable. Thumbnails load one per single-shot `Timer` tick, owned and torn down by the dialog.

**Tech Stack:** PixInsight PJSR (`#engine v8`), node harness `ci/run-tests.js`, in-PixInsight suite `script/selftest.js` dispatched with `PixInsight -x=1:<path>`.

**Spec:** `docs/superpowers/specs/2026-09-22-criteria-filmstrip-flags-design.md` (read it; this plan argues from it).

## Global Constraints

- Branch `feature/criteria-filmstrip-flags`; squash before fast-forwarding to main.
- JS only, no C++. `#engine v8` stays line 1 of every entry point.
- No hardcoded personal paths in shipped code (tests may use `/tmp/agent-scratch` and `/Volumes/<drive>`).
- Scratch files in `/tmp/agent-scratch`, never `$TMPDIR`.
- Never assign `onViewportScrolled` on any control.
- Never launch a second PixInsight; dispatch into the running one (`/Applications/PixInsight/PixInsight.app/Contents/MacOS/PixInsight -x=1:<script>`), start it only if closed.
- The originals in a local folder of subframes are never modified; tests copy them.
- Flags are always on: no switch, preference or checkbox for any detector.
- A flag never changes a verdict or the manifest.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Every node test is proven to have teeth: run it once with the guarded line removed and see it fail.

## Review Focus

1. A user types `4,5` or `abc` or `-1` into a threshold → nothing changes, field reverts (Task 2 `parseLimit` tests, Task 7 in-PixInsight check).
2. A user switches channel while thumbnails load → no thumbnail from the old channel is stored against the new channel's tiles (Task 9 generation test).
3. A channel with all frames unmeasurable (no measurements at all) → no crash in flags, counter shows `N / N keep`, strip draws grey tiles (Task 4 and Task 6 tests).
4. The output folder is changed from source to elsewhere while reviewing → crosses and counter switch meaning immediately (Task 1 test + Task 7 refresh on destination change).
5. PixInsight closes the dialog through its window close box while the loader runs → timer stopped, process survives (Task 10 teardown test).

---

### Task 1: One "left out" predicate and the keep count

**Files:**
- Modify: `script/lib/Frames.js` (after `Frames.finalState`, ~line 853)
- Test: `script/selftest.js` (new block after the existing `finalState` tests; find with `grep -n "finalState" script/selftest.js`)

**Interfaces:**
- Produces: `Frames.leftOut( row, channelEnabled, copying ) -> Boolean`; `Frames.keepCount( rows, channelEnabled, copying ) -> { keep: Number, total: Number }`.

- [ ] **Step 1: Write the failing tests**

```js
   /* ---- leftOut: what Run actually leaves out, per mode ---------------- */
   ( function()
   {
      function r( state, override ) { return { state: state, override: override || null }; }
      var A = Frames.STATE.APPROVED, R = Frames.STATE.REJECTED, U = Frames.STATE.UNMEASURABLE;
      var C = Frames.OVERRIDE.CONDEMNED, S = Frames.OVERRIDE.RESCUED;
      // culling in place: only an enabled channel's rejected frames go
      check( "leftOut cull rejected",          Frames.leftOut( r( R ), true,  false ), true );
      check( "leftOut cull approved",          Frames.leftOut( r( A ), true,  false ), false );
      check( "leftOut cull disabled channel",  Frames.leftOut( r( R ), false, false ), false );
      check( "leftOut cull unmeasurable kept", Frames.leftOut( r( U ), true,  false ), false );
      check( "leftOut cull condemned unmeas.", Frames.leftOut( r( U, C ), true, false ), true );
      check( "leftOut cull rescued kept",      Frames.leftOut( r( R, S ), true, false ), false );
      // copying out: a disabled channel is not copied at all
      check( "leftOut copy rejected",          Frames.leftOut( r( R ), true,  true ), true );
      check( "leftOut copy approved",          Frames.leftOut( r( A ), true,  true ), false );
      check( "leftOut copy disabled channel",  Frames.leftOut( r( A ), false, true ), true );
      check( "leftOut copy condemned unmeas.", Frames.leftOut( r( U, C ), true, true ), true );
      check( "leftOut copy unmeasurable kept", Frames.leftOut( r( U ), true,  true ), false );

      var rows = [ r( A ), r( R ), r( U ), r( U, C ), r( R, S ) ];
      check( "keepCount cull", Frames.keepCount( rows, true, false ), { keep: 3, total: 5 } );
      check( "keepCount cull disabled", Frames.keepCount( rows, false, false ), { keep: 5, total: 5 } );
      check( "keepCount copy disabled", Frames.keepCount( rows, false, true ), { keep: 0, total: 5 } );

      /*
       * Cross-check against what Run does: buildManifest (culling) must
       * list exactly the rows leftOut names, and the copy filter
       * approvedPaths applies must keep exactly the others.
       */
      var paths = rows.map( function( x, i ) { x.path = "/p" + i; return x; } );
      var manifest = Frames.buildManifest( paths ).entries.map( function( e ) { return e.path; } );
      var cull = paths.filter( function( x ) { return Frames.leftOut( x, true, false ); } )
                      .map( function( x ) { return x.path; } );
      check( "leftOut cull equals the manifest", cull, manifest );
      var copied = paths.filter( function( x )
         { return Frames.finalState( x.state, x.override ) != Frames.STATE.REJECTED; } )
         .map( function( x ) { return x.path; } );
      var notLeft = paths.filter( function( x ) { return !Frames.leftOut( x, true, true ); } )
                         .map( function( x ) { return x.path; } );
      check( "leftOut copy equals the copy filter", notLeft, copied );
   } )();
```

- [ ] **Step 2: Run to see it fail**

Run: `cd ~/PixInsight/scripts/Loom && node ci/run-tests.js`
Expected: FAIL / ABORTED with `Frames.leftOut is not a function`.

- [ ] **Step 3: Implement**

In `Frames.js`, after `Frames.finalState`:

```js
/*
 * Whether Run leaves this frame out of its result -- the one rule behind
 * the filmstrip's cross, the preview's red tag and the keep counter.
 *
 * Mirrors the two things Run does. Culling in place deletes an enabled
 * channel's rejected frames (committableRows + buildManifest). Copying out
 * copies only an enabled channel's frames that are not rejected
 * (approvedPaths + commitCopy), so a switched-off channel is left out
 * entirely. finalState already applies overrides: a condemned frame is
 * left out even when it could not be measured.
 */
Frames.leftOut = function( row, channelEnabled, copying )
{
   var rejected = Frames.finalState( row.state, row.override ) == Frames.STATE.REJECTED;
   return copying ? ( !channelEnabled || rejected ) : ( channelEnabled && rejected );
};

Frames.keepCount = function( rows, channelEnabled, copying )
{
   var keep = 0;
   for ( var i = 0; i < rows.length; ++i )
      if ( !Frames.leftOut( rows[i], channelEnabled, copying ) )
         ++keep;
   return { keep: keep, total: rows.length };
};
```

- [ ] **Step 4: Run to see it pass**

Run: `node ci/run-tests.js && LOOM_TEST_CORE_RELEASE=4 node ci/run-tests.js`
Expected: both PASS. Then delete `!channelEnabled ||` temporarily, re-run, see "leftOut copy disabled channel" fail, restore.

- [ ] **Step 5: Commit**

```bash
git add script/lib/Frames.js script/selftest.js
git commit -m "Frames.leftOut: one rule for what Run leaves out, per mode"
```

---

### Task 2: Criteria semantics — operators, parsing, gating-aware limits, prefill

**Files:**
- Modify: `script/lib/Frames.js` (`defaultSettings` ~385, `applyPreset` ~434, `acceptedBand` ~483, `absoluteFailures` ~653, `absoluteReasons` ~673, `verdict` ~688; new helpers after `verdict`)
- Test: `script/selftest.js`

**Interfaces:**
- Consumes: `Frames.WORSE_WHEN`, `Frames.MODE`, `Frames.copyOf`.
- Produces:
  - `Frames.OPERATOR` `{ fwhm: "<=", eccentricity: "<=", stars: ">=", psfSNR: ">=" }`
  - `Frames.LIMIT_DECIMALS` `{ fwhm: 2, eccentricity: 3, stars: 0, psfSNR: 2 }`
  - `Frames.CRITERIA_ORDER = [ "fwhm", "eccentricity", "stars", "psfSNR" ]`
  - `Frames.parseLimit( text ) -> Number | null | undefined`
  - `Frames.formatLimit( metric, value ) -> String` ("" for null)
  - `Frames.limitValue( settings, metric ) -> Number | null`
  - `Frames.withLimit( settings, metric, value, byHand ) -> settings` (copy)
  - `Frames.displayLimit( metric, gates, settings ) -> Number | null`
  - `Frames.relativeHint( metric, gates, settings ) -> Number | null`
  - `Frames.prefillLimits( settings, gates, metrics ) -> settings` (copy)
  - `Frames.absoluteFailures( metrics, limits, gating )` (gating optional)
  - settings gain `limitsEdited: {}` and `pendingPrefill: null`

- [ ] **Step 1: Write the failing tests**

```js
   /* ---- criteria: operators, parsing, limits ---------------------------- */
   ( function()
   {
      for ( var i = 0; i < Frames.METRICS.length; ++i )
      {
         var m = Frames.METRICS[i];
         check( "OPERATOR agrees with WORSE_WHEN for " + m, Frames.OPERATOR[m],
                Frames.WORSE_WHEN[m] == "higher" ? "<=" : ">=" );
      }
      check( "CRITERIA_ORDER is every scoring metric once",
             Frames.CRITERIA_ORDER.slice().sort(), Frames.METRICS.slice().sort() );

      check( "parseLimit blank",        Frames.parseLimit( "  " ), null );
      check( "parseLimit number",       Frames.parseLimit( " 4.76 " ), 4.76 );
      check( "parseLimit comma",        Frames.parseLimit( "4,5" ) === undefined, true );
      check( "parseLimit words",        Frames.parseLimit( "abc" ) === undefined, true );
      check( "parseLimit negative",     Frames.parseLimit( "-1" ) === undefined, true );
      check( "parseLimit zero",         Frames.parseLimit( "0" ) === undefined, true );
      check( "parseLimit trailing junk", Frames.parseLimit( "4.7x" ) === undefined, true );

      check( "formatLimit null",  Frames.formatLimit( "fwhm", null ), "" );
      check( "formatLimit fwhm",  Frames.formatLimit( "fwhm", 4.7612 ), "4.76" );
      check( "formatLimit stars", Frames.formatLimit( "stars", 2345.6 ), "2346" );

      var s = Frames.defaultSettings();
      check( "defaults have no limits edited", s.limitsEdited, {} );
      var s2 = Frames.withLimit( s, "fwhm", 5, true );
      check( "withLimit writes the worse side (hi)", s2.limits.fwhm, { hi: 5 } );
      check( "withLimit marks a hand edit", s2.limitsEdited.fwhm, true );
      check( "withLimit does not touch the original", s.limits.fwhm === undefined, true );
      var s3 = Frames.withLimit( s2, "stars", 900, false );
      check( "withLimit writes lo for stars", s3.limits.stars, { lo: 900 } );
      check( "a program write is not a hand edit", !!s3.limitsEdited.stars, false );
      var s4 = Frames.withLimit( Frames.withLimit( s, "fwhm", 5, false ), "fwhm", null, true );
      check( "blank deletes the entry", s4.limits.fwhm === undefined, true );
      check( "clearing by hand is an edit", s4.limitsEdited.fwhm, true );
      // the whole object is replaced: no opposite bound survives
      var legacy = Frames.copyOf( s ); legacy.limits = { fwhm: { lo: 4, hi: 9 } };
      check( "writing a limit replaces the whole object",
             Frames.withLimit( legacy, "fwhm", 6, true ).limits.fwhm, { hi: 6 } );

      // an unchecked criterion rejects nothing and does not narrow the band
      var lim = { fwhm: { hi: 4 } };
      var metrics = { fwhm: 5, eccentricity: 0.5, stars: 1000, psfSNR: 10 };
      check( "a checked threshold rejects",
             Frames.absoluteFailures( metrics, lim, { fwhm: true } ).length, 1 );
      check( "an unchecked threshold rejects nothing",
             Frames.absoluteFailures( metrics, lim, { fwhm: false } ).length, 0 );
      check( "omitted gating means all on",
             Frames.absoluteFailures( metrics, lim ).length, 1 );
      var abs = Frames.copyOf( s ); abs.mode = Frames.MODE.ABSOLUTE; abs.limits = lim;
      abs.gating = { fwhm: false, eccentricity: true, stars: true, psfSNR: false };
      check( "an unchecked threshold does not narrow the band",
             Frames.acceptedBand( "fwhm", {}, abs ), { lo: null, hi: null } );
      check( "verdict honours the checkbox in Thresholds",
             Frames.verdict( metrics, {}, abs ).state, Frames.STATE.APPROVED );
      abs.gating.fwhm = true;
      check( "and rejects when it is checked",
             Frames.verdict( metrics, {}, abs ).state, Frames.STATE.REJECTED );

      // displayLimit / relativeHint per mode
      var gates = { fwhm: { active: true, limit: 4.2 }, stars: { active: false, limit: null } };
      var rel = Frames.copyOf( s ); rel.mode = Frames.MODE.RELATIVE; rel.limits = { fwhm: { hi: 9 } };
      check( "Relative shows the gate",         Frames.displayLimit( "fwhm", gates, rel ), 4.2 );
      check( "Relative inactive gate is blank", Frames.displayLimit( "stars", gates, rel ), null );
      check( "Relative has no hint",            Frames.relativeHint( "fwhm", gates, rel ), null );
      var th = Frames.copyOf( rel ); th.mode = Frames.MODE.ABSOLUTE;
      check( "Thresholds shows the stored limit", Frames.displayLimit( "fwhm", gates, th ), 9 );
      check( "Thresholds has no hint",            Frames.relativeHint( "fwhm", gates, th ), null );
      var both = Frames.copyOf( rel ); both.mode = Frames.MODE.BOTH;
      check( "Both shows the stored limit", Frames.displayLimit( "fwhm", gates, both ), 9 );
      check( "Both hints the gate",         Frames.relativeHint( "fwhm", gates, both ), 4.2 );

      // prefill
      var pf = Frames.prefillLimits( rel, gates, [ "fwhm", "stars" ] );
      check( "prefill copies the exact gate", pf.limits.fwhm, { hi: 4.2 } );
      check( "prefill of an inactive gate is blank", pf.limits.stars === undefined, true );
      check( "prefill is not a hand edit", !!pf.limitsEdited.fwhm, false );
      var edited = Frames.withLimit( rel, "fwhm", 7, true );
      check( "prefill never replaces a hand edit",
             Frames.prefillLimits( edited, gates, [ "fwhm" ] ).limits.fwhm, { hi: 7 } );
      check( "prefill touches only the listed metrics",
             Frames.prefillLimits( rel, gates, [ "stars" ] ).limits.fwhm, { hi: 9 } );
      var cleared = Frames.withLimit( rel, "fwhm", null, true );
      check( "a field cleared by hand is not refilled",
             Frames.prefillLimits( cleared, gates, [ "fwhm" ] ).limits.fwhm === undefined, true );

      // settings copies do not share the new maps
      var a = Frames.defaultSettings(), b = Frames.applyPreset( a, "strict" );
      b.limitsEdited.fwhm = true;
      check( "applyPreset copies limitsEdited", !!a.limitsEdited.fwhm, false );
   } )();
```

- [ ] **Step 2: Run to see it fail**

Run: `node ci/run-tests.js`
Expected: FAIL/ABORTED, `Frames.OPERATOR` undefined.

- [ ] **Step 3: Implement**

In `Frames.defaultSettings`, add after `gating: {...},`:

```js
            /*
             * Which thresholds were typed by hand, per metric. A hand edit
             * stands; prefill only ever writes a limit nobody has touched.
             */
            limitsEdited: {},
            // Metrics to prefill on the next recompute, set by the dialog.
            pendingPrefill: null,
```

In `Frames.applyPreset`, after the `gating` copy:

```js
   if ( settings.limitsEdited != null )
      out.limitsEdited = Frames.copyOf( settings.limitsEdited );
```

Replace `Frames.absoluteFailures` with:

```js
/*
 * Rejections from a hard limit: only a CONFIGURED limit on a CHECKED
 * criterion can reject. An omitted gating map means every criterion is on,
 * which is what the metric-level tests assume.
 */
Frames.absoluteFailures = function( metrics, limits, gating )
{
   var out = [];
   for ( var a = 0; a < Frames.METRICS.length; ++a )
   {
      var an = Frames.METRICS[a], lim = limits[an];
      if ( lim == null || ( gating != null && !gating[an] ) )
         continue;
      if ( lim.hi != null && metrics[an] > lim.hi )
         out.push( { metric: an,
                     text: Frames.METRIC_LABEL[an] + " " + Frames.round( metrics[an] ) +
                           " above the limit of " + Frames.round( lim.hi ) } );
      if ( lim.lo != null && metrics[an] < lim.lo )
         out.push( { metric: an,
                     text: Frames.METRIC_LABEL[an] + " " + Frames.round( metrics[an] ) +
                           " below the limit of " + Frames.round( lim.lo ) } );
   }
   return out;
};

Frames.absoluteReasons = function( metrics, limits, gating )
{
   return Frames.absoluteFailures( metrics, limits, gating ).map(
      function( f ) { return f.text; } );
};
```

In `Frames.verdict`, change the absolute line to:

```js
      failures = failures.concat( Frames.absoluteFailures( metrics, settings.limits,
                                                           settings.gating ) );
```

In `Frames.acceptedBand`, change the absolute block's first lines to:

```js
   if ( settings.mode != Frames.MODE.RELATIVE &&
        !( settings.gating != null && settings.gating[metric] === false ) )
   {
      var lim = settings.limits ? settings.limits[metric] : null;
```

Add after `Frames.verdict`:

```js
/* ---- the criteria panel's arithmetic -------------------------------- */

Frames.OPERATOR = { fwhm: "<=", eccentricity: "<=", stars: ">=", psfSNR: ">=" };
Frames.LIMIT_DECIMALS = { fwhm: 2, eccentricity: 3, stars: 0, psfSNR: 2 };
/* Left to right in the panel, as SubframeStudio lays them out. */
Frames.CRITERIA_ORDER = [ "fwhm", "eccentricity", "stars", "psfSNR" ];

/*
 * A typed threshold: a positive finite number, null for blank (no
 * threshold), undefined for anything else -- which the dialog answers by
 * putting the stored value back. A comma is not accepted as a decimal
 * point: guessing wrong would move a limit by a factor of a thousand.
 */
Frames.parseLimit = function( text )
{
   var t = String( text == null ? "" : text ).trim();
   if ( t === "" )
      return null;
   if ( !/^[0-9]*\.?[0-9]+(e[+-]?[0-9]+)?$/i.test( t ) )
      return undefined;
   var v = parseFloat( t );
   return ( isFinite( v ) && v > 0 ) ? v : undefined;
};

Frames.formatLimit = function( metric, value )
{
   if ( value == null || !isFinite( value ) )
      return "";
   var d = Frames.LIMIT_DECIMALS[metric];
   return value.toFixed( d == null ? 2 : d );
};

/* The stored worse-side bound, or null. */
Frames.limitValue = function( settings, metric )
{
   var lim = settings.limits ? settings.limits[metric] : null;
   if ( lim == null )
      return null;
   var v = ( Frames.WORSE_WHEN[metric] == "higher" ) ? lim.hi : lim.lo;
   return ( v == null ) ? null : v;
};

/*
 * Settings with one threshold written. The whole entry is replaced, so no
 * bound on the other side can survive; null deletes it. `byHand` marks the
 * edit so prefill never overwrites it. Returns a copy.
 */
Frames.withLimit = function( settings, metric, value, byHand )
{
   var out = Frames.copyOf( settings );
   out.limits = Frames.copyOf( settings.limits );
   out.limitsEdited = Frames.copyOf( settings.limitsEdited );
   if ( value == null )
      delete out.limits[metric];
   else
      out.limits[metric] = ( Frames.WORSE_WHEN[metric] == "higher" ) ? { hi: value }
                                                                    : { lo: value };
   if ( byHand )
      out.limitsEdited[metric] = true;
   return out;
};

/* What the field shows: the gate in Relative, the stored limit otherwise. */
Frames.displayLimit = function( metric, gates, settings )
{
   if ( settings.mode == Frames.MODE.RELATIVE )
   {
      var g = gates ? gates[metric] : null;
      return ( g != null && g.active ) ? g.limit : null;
   }
   return Frames.limitValue( settings, metric );
};

/* The grey figure beside the field: the relative limit, in Both only. */
Frames.relativeHint = function( metric, gates, settings )
{
   if ( settings.mode != Frames.MODE.BOTH )
      return null;
   var g = gates ? gates[metric] : null;
   return ( g != null && g.active ) ? g.limit : null;
};

/*
 * Fill the listed metrics' thresholds from the relative gates, exactly.
 * A hand-edited threshold is never touched; an inactive gate clears the
 * entry. Returns a copy.
 */
Frames.prefillLimits = function( settings, gates, metrics )
{
   var out = settings;
   for ( var i = 0; i < metrics.length; ++i )
   {
      var m = metrics[i];
      if ( settings.limitsEdited && settings.limitsEdited[m] )
         continue;
      var g = gates ? gates[m] : null;
      out = Frames.withLimit( out, m, ( g != null && g.active ) ? g.limit : null, false );
   }
   return ( out === settings ) ? Frames.copyOf( settings ) : out;
};
```

- [ ] **Step 4: Run to see it pass; prove teeth**

Run: `node ci/run-tests.js && LOOM_TEST_CORE_RELEASE=4 node ci/run-tests.js`
Expected: both PASS. Remove `|| ( gating != null && !gating[an] )`, re-run, see "an unchecked threshold rejects nothing" fail; restore. Remove the `limitsEdited` guard in `prefillLimits`, see "prefill never replaces a hand edit" fail; restore.

- [ ] **Step 5: Commit**

```bash
git add script/lib/Frames.js script/selftest.js
git commit -m "Frames: criteria arithmetic -- operators, parsing, gating-aware thresholds, prefill"
```

---

### Task 3: Move channel construction and recompute into Frames; prefill between gates and verdicts

**Files:**
- Modify: `script/lib/Frames.js` (new `Frames.newChannel`, `Frames.recompute` at the end of the file)
- Modify: `script/FrameSelector.js:1095-1175` (`FrameSelector.newChannel`, `FrameSelector.recompute` become one-line delegations)
- Test: `script/selftest.js`

**Interfaces:**
- Consumes: Task 2 `prefillLimits`, `settings.pendingPrefill`.
- Produces: `Frames.newChannel( key, entries, metrics, problems ) -> channel`; `Frames.recompute( ch ) -> ch` (sets `ch.gates`, row verdicts, consumes `ch.settings.pendingPrefill`); `Frames.testChannel()` is NOT added -- the fixture lives in selftest as `fsFixtureChannel()`.

- [ ] **Step 1: Write the failing tests**

Add near the top of `runTests()` helpers (before first use), as a plain function in selftest.js at file scope, below `check`:

```js
/*
 * A 20-frame channel with real spread: FWHM gates at about 5.8, so the two
 * wide frames (6.5, 7.0) are rejected by the balanced preset, and nothing
 * else is. Background and SNR ride along for the flag and column tests.
 */
function fsFixtureChannel()
{
   var fw = [ 3.0, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9,
              4.0, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 6.5, 7.0 ];
   var entries = [], metrics = {};
   for ( var i = 0; i < fw.length; ++i )
   {
      var p = "/fx/f" + i + ".xisf";
      entries.push( { path: p, identity: { digest: "d" + i, size: 1, mtime: 1 } } );
      metrics[p] = { fwhm: fw[i], eccentricity: 0.40 + 0.01*( i % 10 ),
                     psfSNR: 10 + 0.37*i, stars: 8000 + 37*i, noise: 0.001,
                     background: 0.02 + 0.0005*( i % 7 ), snrWeight: 20 + i };
   }
   return Frames.newChannel( "O", entries, metrics, [] );
}
```

Tests:

```js
   /* ---- recompute: gates, then prefill, then verdicts ------------------- */
   ( function()
   {
      var ch = Frames.recompute( fsFixtureChannel() );
      var relStates = ch.rows.map( function( r ) { return r.state; } );
      var rejected = relStates.filter( function( s ) { return s == Frames.STATE.REJECTED; } ).length;
      check( "the fixture has genuine rejects", rejected, 2 );
      check( "the FWHM gate is active", ch.gates.fwhm.active, true );

      // Relative -> Thresholds with prefill: every verdict unchanged
      ch.settings.mode = Frames.MODE.ABSOLUTE;
      ch.settings.pendingPrefill = Frames.CRITERIA_ORDER.filter(
         function( m ) { return ch.settings.gating[m]; } );
      Frames.recompute( ch );
      check( "prefill is consumed", ch.settings.pendingPrefill, null );
      check( "switching to Thresholds changes no verdict",
             ch.rows.map( function( r ) { return r.state; } ), relStates );
      check( "the threshold is the exact gate",
             Frames.limitValue( ch.settings, "fwhm" ), ch.gates.fwhm.limit );

      // a frame exactly at the limit passes both tests
      var at = { fwhm: ch.gates.fwhm.limit, eccentricity: 0.45, stars: 8300, psfSNR: 12 };
      check( "at the limit passes the relative test",
             Frames.relativeFailures( at, ch.gates ).length, 0 );
      check( "at the limit passes the threshold",
             Frames.absoluteFailures( at, ch.settings.limits, ch.settings.gating ).length, 0 );

      // a hand edit survives a k change and a round trip
      ch.settings = Frames.withLimit( ch.settings, "fwhm", 6.6, true );
      Frames.recompute( ch );
      check( "a hand edit decides: 6.5 now kept",
             ch.rows[18].state, Frames.STATE.APPROVED );
      ch.settings.mode = Frames.MODE.RELATIVE; ch.settings.k = 3.0; ch.settings.kEdited = true;
      Frames.recompute( ch );
      ch.settings.mode = Frames.MODE.ABSOLUTE;
      ch.settings.pendingPrefill = [ "fwhm", "eccentricity", "stars" ];
      Frames.recompute( ch );
      check( "the hand edit survives a k change and a round trip",
             Frames.limitValue( ch.settings, "fwhm" ), 6.6 );
      check( "an untouched threshold follows the new k on re-entry",
             Frames.limitValue( ch.settings, "eccentricity" ), ch.gates.eccentricity.limit );

      // rechecking one criterion touches only that one
      var before = Frames.limitValue( ch.settings, "eccentricity" );
      ch.settings.k = 2.0;
      ch.settings.gating.stars = false; Frames.recompute( ch );
      ch.settings.gating.stars = true; ch.settings.pendingPrefill = [ "stars" ];
      Frames.recompute( ch );
      check( "rechecking Stars leaves Ecc alone",
             Frames.limitValue( ch.settings, "eccentricity" ), before );
      check( "and prefilled Stars from the current gate",
             Frames.limitValue( ch.settings, "stars" ), ch.gates.stars.limit );

      // channels are independent
      var other = Frames.recompute( fsFixtureChannel() );
      check( "another channel's settings are untouched", other.settings.limits, {} );

      // a channel with nothing measured does not throw
      var empty = Frames.newChannel( "X", [ { path: "/x", identity: null } ], {}, [] );
      check( "an unmeasured channel recomputes", Frames.recompute( empty ).rows[0].state,
             Frames.STATE.UNMEASURABLE );
   } )();
```

- [ ] **Step 2: Run to see it fail**

Run: `node ci/run-tests.js` → ABORTED, `Frames.newChannel is not a function`.

- [ ] **Step 3: Implement**

Move the bodies of `FrameSelector.newChannel` and `FrameSelector.recompute` (FrameSelector.js:1103-1175, including their comments) to the end of `Frames.js` as `Frames.newChannel` and `Frames.recompute`, and change `Frames.recompute`'s gate block to:

```js
   var gates = Frames.relativeGates( cohort, ch.settings.k, ch.settings.gating );
   ch.gates = gates;
   /*
    * Prefill sits BETWEEN the gates and the verdicts: it reads the gates
    * for this cohort and k, and every verdict below is computed against
    * the thresholds it wrote. Row states are never left over from before.
    */
   if ( ch.settings.pendingPrefill != null )
   {
      var pending = ch.settings.pendingPrefill;
      ch.settings = Frames.prefillLimits( ch.settings, gates, pending );
      ch.settings.pendingPrefill = null;
   }
```

In `FrameSelector.js`, replace the two functions with:

```js
/* Moved to Frames so the node suite can drive them; kept as names here. */
FrameSelector.newChannel = function( key, entries, metrics, problems )
{
   return Frames.newChannel( key, entries, metrics, problems );
};
FrameSelector.recompute = function( ch ) { return Frames.recompute( ch ); };
```

- [ ] **Step 4: Run to see it pass; prove teeth**

Run both node builds → PASS. Move the prefill block AFTER the verdict loop, re-run, see "switching to Thresholds changes no verdict" still pass but set a hand edit first... instead: comment the prefill block out and see "the threshold is the exact gate" fail; restore.

- [ ] **Step 5: Commit**

```bash
git add script/lib/Frames.js script/FrameSelector.js script/selftest.js
git commit -m "Frames.recompute: gates, then prefill, then verdicts"
```

---

### Task 4: Background and SNR from SubframeSelector

**Files:**
- Modify: `script/lib/Frames.js:21-46` (`MEASURE_VERSION`, `COL`, `metricsFromRow`); new `Frames.OPTIONAL`, `optionalProblems`, `sanitizeOptional`, `storedMetrics`, `cacheEntryUsable`
- Modify: `script/FrameSelector.js:97-146` (`measure`), `:299-303` (`cachedMeasurement`), `:365-369` (`storedMetrics`)
- Modify: `docs/verified-parameters.md` (SubframeSelector table)
- Test: `script/selftest.js` (node block + extend the IN_PIXINSIGHT measurement block at ~3710)

**Interfaces:**
- Produces: `Frames.COL.snr = 9`, `Frames.COL.median = 10`; metrics carry `background` and `snrWeight` (Number or null); `Frames.OPTIONAL = [ "background", "snrWeight" ]`; `Frames.optionalProblems( m ) -> [ fieldName ]`; `Frames.sanitizeOptional( m, disabled ) -> m` (disabled: `{ field: true }`); `Frames.storedMetrics( m )`; `Frames.cacheEntryUsable( e ) -> Boolean`.

- [ ] **Step 1: Write the failing node tests**

```js
   /* ---- background and SNR: optional, never fatal ----------------------- */
   ( function()
   {
      check( "measurement version bumped", Frames.MEASURE_VERSION, "v2" );
      check( "SNR estimate is column 9", Frames.COL.snr, 9 );
      check( "median is column 10", Frames.COL.median, 10 );

      var row = []; for ( var c = 0; c < 30; ++c ) row.push( 0 );
      row[3] = "/a.xisf"; row[5] = 3.5; row[6] = 0.5; row[12] = 0.001;
      row[14] = 7507; row[28] = 8.815; row[9] = NaN; row[10] = NaN;
      var m = Frames.metricsFromRow( row );
      check( "meaningProblems ignores columns 9 and 10",
             Frames.meaningProblems( m ), [] );
      check( "optionalProblems names both bad fields",
             Frames.optionalProblems( m ), [ "background", "snrWeight" ] );

      row[9] = 42.5; row[10] = 0.021;
      m = Frames.metricsFromRow( row );
      check( "metricsFromRow reads background", m.background, 0.021 );
      check( "metricsFromRow reads SNR", m.snrWeight, 42.5 );
      check( "a good row has no optional problems", Frames.optionalProblems( m ), [] );

      // schema scope: a disabled field is null whatever it holds
      check( "sanitize nulls a disabled field",
             Frames.sanitizeOptional( { background: 0.02, snrWeight: 3 },
                                      { background: true } ),
             { background: null, snrWeight: 3 } );
      // value scope: one bad reading nulls only itself
      check( "sanitize nulls only a bad value",
             Frames.sanitizeOptional( { background: NaN, snrWeight: 3 }, {} ),
             { background: null, snrWeight: 3 } );
      check( "sanitize nulls a negative value",
             Frames.sanitizeOptional( { background: -1, snrWeight: 3 }, {} ).background, null );

      var stored = Frames.storedMetrics( { path: "/a", fwhm: 1, eccentricity: 0.5,
                                           noise: 0.1, stars: 10, psfSNR: 2.5 } );
      check( "storedMetrics has no path", stored.path === undefined, true );
      check( "storedMetrics carries missing optionals as null",
             [ stored.background, stored.snrWeight ], [ null, null ] );

      check( "a v1-shaped entry is not usable",
             Frames.cacheEntryUsable( { fwhm: 1, eccentricity: 0.5, noise: 0.1,
                                        stars: 10, psfSNR: 2.5 } ), false );
      check( "a v2 entry with null optionals is usable",
             Frames.cacheEntryUsable( stored ), true );
      check( "null is not usable", Frames.cacheEntryUsable( null ), false );
   } )();
```

- [ ] **Step 2: Run to see it fail** — `node ci/run-tests.js` → FAIL on "measurement version bumped".

Also update the existing check at selftest.js:1797 (`Frames.MEASURE_VERSION, "v1"`) to `"v2"` -- it pins the version deliberately; changing it is the point of this task.

- [ ] **Step 3: Implement in Frames.js**

```js
Frames.MEASURE_VERSION = "v2";   // v2: adds background and SNR estimate

Frames.COL = { path: 3, fwhm: 5, eccentricity: 6, snr: 9, median: 10,
               noise: 12, stars: 14, psfSNR: 28 };
```

(Add to the COL comment: 9 and 10 are WBPP's `iSNREstimate` and `iMedian`, BPP-SubframeAnalyzer.js:487-488, confirmed live -- see verified-parameters.md.)

```js
Frames.metricsFromRow = function( row )
{
   return { path:         row[Frames.COL.path],
            fwhm:         row[Frames.COL.fwhm],
            eccentricity: row[Frames.COL.eccentricity],
            noise:        row[Frames.COL.noise],
            stars:        row[Frames.COL.stars],
            psfSNR:       row[Frames.COL.psfSNR],
            background:   row[Frames.COL.median],
            snrWeight:    row[Frames.COL.snr] };
};

/*
 * Figures that are SHOWN (SNR) or feed an advisory flag (background) but
 * decide nothing. A problem with one of them blanks that figure; it never
 * abandons a channel the way meaningProblems does.
 */
Frames.OPTIONAL = [ "background", "snrWeight" ];

Frames.optionalOk = function( v )
{
   return typeof v == "number" && isFinite( v ) && v >= 0;
};

/* Schema check, first row only: which optional columns are not usable. */
Frames.optionalProblems = function( m )
{
   var bad = [];
   for ( var i = 0; i < Frames.OPTIONAL.length; ++i )
      if ( !Frames.optionalOk( m[Frames.OPTIONAL[i]] ) )
         bad.push( Frames.OPTIONAL[i] );
   return bad;
};

/*
 * Two scopes. `disabled` names fields the schema check failed on: null for
 * every row. Otherwise one bad reading is null for that row alone.
 */
Frames.sanitizeOptional = function( m, disabled )
{
   for ( var i = 0; i < Frames.OPTIONAL.length; ++i )
   {
      var f = Frames.OPTIONAL[i];
      if ( ( disabled && disabled[f] ) || !Frames.optionalOk( m[f] ) )
         m[f] = null;
   }
   return m;
};

/*
 * Stored WITHOUT the path. The key is the digest, so identical bytes sitting
 * somewhere else must not come back naming the first file.
 */
Frames.STORED_KEYS = [ "fwhm", "eccentricity", "noise", "stars", "psfSNR",
                       "background", "snrWeight" ];

Frames.storedMetrics = function( m )
{
   var out = {};
   for ( var i = 0; i < Frames.STORED_KEYS.length; ++i )
   {
      var k = Frames.STORED_KEYS[i];
      out[k] = ( m[k] === undefined ) ? null : m[k];
   }
   return out;
};

/*
 * A cached entry is usable when it has every key. A null optional is a
 * cached "unavailable", not a miss -- otherwise a frame whose background
 * cannot be read would be re-measured on every scan, forever.
 */
Frames.cacheEntryUsable = function( e )
{
   if ( e == null || typeof e != "object" )
      return false;
   for ( var i = 0; i < Frames.STORED_KEYS.length; ++i )
      if ( !Object.prototype.hasOwnProperty.call( e, Frames.STORED_KEYS[i] ) )
         return false;
   return true;
};
```

In `FrameSelector.js`:

```js
FrameSelector.storedMetrics = function( m ) { return Frames.storedMetrics( m ); };

FrameSelector.cachedMeasurement = function( identity )
{
   var t = FrameSelector.loadTable();
   var e = t[FrameSelector.measurementKey( identity )];
   return Frames.cacheEntryUsable( e ) ? e : null;
};
```

In `FrameSelector.measure`, declare `var disabled = {};` before the row loop, and inside `if ( r == 0 )` after the `meaningProblems` block:

```js
         /*
          * Background and SNR are optional: a column that is not what it
          * should be blanks that figure for the whole run and says so,
          * and never abandons the channel.
          */
         var soft = Frames.optionalProblems( m );
         for ( var sp = 0; sp < soft.length; ++sp )
         {
            disabled[soft[sp]] = true;
            Util.warn( "frames", "SubframeSelector's " + soft[sp] +
                       " column is not usable on this PixInsight; it is left " +
                       "blank for this run" );
         }
```

and just before `out[m.path] = m;`:

```js
      Frames.sanitizeOptional( m, disabled );
```

- [ ] **Step 4: Run node to see it pass; prove teeth** — both builds PASS; remove the `disabled[f] ||` clause, see "sanitize nulls a disabled field" fail; restore.

- [ ] **Step 5: Column confirmation on a LIGHT frame (PixInsight)**

The spec requires a subframe, not the drizzled master the existing block
uses. New IN_PIXINSIGHT block right after it:

```js
   if ( IN_PIXINSIGHT ) ( function()
   {
      var src = "<a local folder of subframes>";
      var have = File.directoryExists( src ) ? FrameSelector.frameFilesIn( src ) : [];
      check( "a light frame for column confirmation is present", have.length > 0, true );
      if ( have.length == 0 )
         return;
      have.sort();
      if ( !File.directoryExists( "/tmp/agent-scratch/fs-columns" ) )
         File.createDirectory( "/tmp/agent-scratch/fs-columns", true );
      var fixture = "/tmp/agent-scratch/fs-columns/" + File.extractNameAndExtension( have[0] );
      if ( !File.exists( fixture ) )
         File.copyFile( fixture, have[0] );         // target, source
      var measured = FrameSelector.measure( [ fixture ] );
      check( "the light frame measured", measured != null && measured[fixture] != null, true );
      if ( measured == null || measured[fixture] == null )
         return;
      var m = measured[fixture];
```

then:

```js
      /*
       * Background is confirmed against an INDEPENDENT figure: the frame's
       * own median, computed by PixInsight on the same file. Same units is
       * part of what is being established; the ratio is recorded.
       */
      var win = ImageWindow.open( fixture )[0];
      var own = win.mainView.image.median();
      win.forceClose();
      check( "background is a usable number", Frames.optionalOk( m.background ), true );
      check( "background matches the image's own median (within 2%)",
             Math.abs( m.background - own ) <= 0.02*Math.abs( own ), true );
      check( "noise (column 12) is not the median",
             Math.abs( m.noise - own ) > 0.02*Math.abs( own ), true );
      /*
       * SNR estimate has no independent PJSR figure. Shape, plus negative
       * controls: it is not PSF SNR, and not column 8's constant zero.
       */
      check( "SNR estimate is a positive number", m.snrWeight > 0, true );
      check( "SNR estimate is not PSF SNR", m.snrWeight != m.psfSNR, true );
   } )();
```

Honest limit, stated in the test's comment: the integration of
`optionalProblems` into `measure` cannot be made to fail on demand --
SubframeSelector cannot be told to return a bad column -- so it is covered
by the node tests of the helpers plus this live run, not by a negative
live test.

If "within 2%" fails because SubframeSelector reports data units (e.g. ×65535), do NOT loosen the tolerance: record the observed ratio in the failure, establish the scale in `docs/verified-parameters.md`, and compare after dividing by it. Background is used only relative to the channel, so a constant scale is harmless; say that in the doc.

- [ ] **Step 6: Run in PixInsight**

Ensure PixInsight is running (start it only if `ps -axo pid,comm | awk '$2 ~ /PixInsight$/'` is empty), mount check `/Volumes/<drive>`, then:

```bash
rm -f /tmp/agent-scratch/lhso-selftest.txt
/Applications/PixInsight/PixInsight.app/Contents/MacOS/PixInsight -x=1:"$HOME/PixInsight/scripts/Loom/script/selftest.js"
until [ -f /tmp/agent-scratch/lhso-selftest.txt ]; do sleep 5; done; head -20 /tmp/agent-scratch/lhso-selftest.txt
```

(sandbox bypass is required for the PixInsight CLI.) Expected: PASS. Add the confirmed columns to `docs/verified-parameters.md`'s SubframeSelector table: `| 9 | SNR estimate (display only) |`, `| 10 | median (background; units as measured) |`.

- [ ] **Step 7: Commit**

```bash
git add script/lib/Frames.js script/FrameSelector.js script/selftest.js docs/verified-parameters.md
git commit -m "Read background and SNR estimate; optional, never fatal; cache v2"
```

---

### Task 5: SNR column in the table and the plot

**Files:**
- Modify: `script/lib/Frames.js` (`METRIC_LABEL`, `METRIC_HEADING` ~452-456, `FRAME_COLUMNS` ~470, `metricColumn` ~610, `SCORE_COLUMN` ~617; new `DISPLAY_METRICS`, `displayValue`)
- Modify: `script/FrameSelector.js` (`buildPlotPane` ~1272, `plotMetric` ~1363, `fillFrames` value loop ~1725; `Plot.setSeries` / paint must skip null values -- check `grep -n "setSeries\|values\[" script/FrameSelector.js`)
- Test: `script/selftest.js`

**Interfaces:**
- Produces: `Frames.DISPLAY_METRICS = [ "psfSNR", "snrWeight", "fwhm", "eccentricity", "stars" ]`; `Frames.displayValue( metric, value ) -> String` ("-" for null).

- [ ] **Step 1: Failing tests**

```js
   /* ---- SNR column: shown, never judged -------------------------------- */
   ( function()
   {
      check( "display order puts SNR after PSF SNR", Frames.DISPLAY_METRICS,
             [ "psfSNR", "snrWeight", "fwhm", "eccentricity", "stars" ] );
      check( "SNR is not a scoring metric", Frames.METRICS.indexOf( "snrWeight" ), -1 );
      check( "SNR column sits right after PSF SNR",
             Frames.metricColumn( "snrWeight" ), Frames.metricColumn( "psfSNR" ) + 1 );
      check( "SNR heading", Frames.FRAME_COLUMNS[Frames.metricColumn( "snrWeight" )], "SNR" );
      check( "score is the last column", Frames.SCORE_COLUMN, Frames.FRAME_COLUMNS.length - 1 );
      check( "null shows a dash", Frames.displayValue( "snrWeight", null ), "-" );
      check( "stars shows whole", Frames.displayValue( "stars", 8000 ), "8000" );

      var ch = Frames.recompute( fsFixtureChannel() );
      var scoreBefore = ch.rows[3].score, stateBefore = ch.rows[3].state;
      ch.rows[3].metrics.snrWeight = 0.0001;              // terrible SNR
      Frames.recompute( ch );
      check( "a terrible SNR is still approved", ch.rows[3].state, stateBefore );
      check( "and its score is unchanged", ch.rows[3].score, scoreBefore );
   } )();
```

- [ ] **Step 2: Update the existing column assertions, then run** → FAIL.

The existing checks at selftest.js ~4434-4446 pin the OLD layout and must
move with it, deliberately:

```js
      check( "PSF SNR is column 1", Frames.metricColumn( "psfSNR" ), 1 );
      check( "SNR is column 2", Frames.metricColumn( "snrWeight" ), 2 );
      check( "FWHM is column 3", Frames.metricColumn( "fwhm" ), 3 );
      check( "eccentricity is column 4", Frames.metricColumn( "eccentricity" ), 4 );
      check( "stars is column 5", Frames.metricColumn( "stars" ), 5 );
      ...
      check( "the headings cover every shown metric plus name and score",
             Frames.FRAME_COLUMNS.length, Frames.DISPLAY_METRICS.length + 2 );
```

- [ ] **Step 3: Implement (Frames.js)**

```js
Frames.METRIC_LABEL = { psfSNR: "PSF SNR", fwhm: "FWHM", eccentricity: "eccentricity",
                        stars: "stars", snrWeight: "SNR estimate" };
Frames.METRIC_HEADING = { psfSNR: "PSF SNR", fwhm: "FWHM", eccentricity: "ecc",
                          stars: "stars", snrWeight: "SNR" };

/*
 * What the table and the plot show, in column order. Wider than METRICS:
 * SNR is shown and plotted but never gated, weighted or scored, so it must
 * not be in the list that drives those.
 */
Frames.DISPLAY_METRICS = [ "psfSNR", "snrWeight", "fwhm", "eccentricity", "stars" ];

Frames.FRAME_COLUMNS = [ "Frame" ]
   .concat( Frames.DISPLAY_METRICS.map( function( m ) { return Frames.METRIC_HEADING[m]; } ) )
   .concat( [ "score" ] );

Frames.metricColumn = function( metric )
{
   var i = Frames.DISPLAY_METRICS.indexOf( metric );
   return ( i < 0 ) ? null : i + 1;
};
Frames.SCORE_COLUMN = Frames.DISPLAY_METRICS.length + 1;

Frames.displayValue = function( metric, value )
{
   if ( value == null || !isFinite( value ) )
      return "-";
   return ( metric == "stars" ) ? String( Math.round( value ) ) : Frames.round( value );
};
```

(`FRAME_COLUMNS` is defined at ~470 before `metricColumn`; keep the definition order: `DISPLAY_METRICS` above `FRAME_COLUMNS`.)

`FrameSelector.js`:
- `buildPlotPane`: loop `Frames.DISPLAY_METRICS` instead of `Frames.METRICS` for the combo items.
- `plotMetric()`: `return Frames.DISPLAY_METRICS[( i >= 0 && i < Frames.DISPLAY_METRICS.length ) ? i : 0];`
- `fillFrames`: loop `Frames.DISPLAY_METRICS`, text `Frames.displayValue( mn, mv )`.
- `fillPlot`: SNR has no band -- `Frames.acceptedBand` already returns `{lo:null,hi:null}` for a metric with no gate and no limit; confirm by test below.
- `Plot`: NO change. It already keeps one slot per row (FrameSelector.js ~2540) and `plotBounds` / the drawing loop skip null and non-finite values, so a missing SNR leaves a gap at that frame's own x. Do not compress the array: the slot index is the row index that selection and verdict colours use.

Add test: `check( "SNR has no band", Frames.acceptedBand( "snrWeight", ch.gates, ch.settings ), { lo: null, hi: null } );`

- [ ] **Step 4: Run node both builds → PASS.** Teeth: temporarily add `"snrWeight"` to `METRICS`, see "SNR is not a scoring metric" fail; restore.

- [ ] **Step 5: Commit** — `git commit -am "SNR estimate as a display-only column and plot choice"`

---

### Task 6: Anomaly flags

**Files:**
- Modify: `script/lib/Frames.js` (new section at the end); `Frames.newChannel` sets `ch.flags`
- Test: `script/selftest.js`

**Interfaces:**
- Produces: `Frames.FLAG_K = 3`, `Frames.FLAG_MIN_FRAMES = 5`, `Frames.DROPPED_FRACTION = 0.1`, `Frames.FLAG_ORDER`, `Frames.FLAG_TAG`, `Frames.FLAG_BADGE`; `Frames.anomalyFlags( metricsList, comparable ) -> [ [ flagKey ] ]`; `Frames.flagSummary( flagsList ) -> String`; `Frames.frameTags( row, flags, channelEnabled, copying ) -> [ { text, kind: "reject"|"flag" } ]`; channel gets `ch.flags` (array parallel to `ch.rows`), computed in `newChannel` with `comparable = problems.length == 0 && Frames.autoRejectAllowed( key )`.

- [ ] **Step 1: Failing tests**

```js
   /* ---- anomaly flags: advisory, per metric, never a verdict ------------ */
   ( function()
   {
      function M( o ) { var b = { fwhm: 4, eccentricity: 0.45, stars: 8000,
                                  background: 0.02, psfSNR: 10 };
                        for ( var k in o ) b[k] = o[k]; return b; }
      function spread( n, f ) { var a = []; for ( var i = 0; i < n; ++i ) a.push( f( i ) ); return a; }
      // a channel with real spread in every metric
      var base = spread( 12, function( i ) { return M( { fwhm: 3.8 + 0.04*i,
         eccentricity: 0.40 + 0.01*i, stars: 7800 + 40*i, background: 0.020 + 0.0004*i } ); } );

      var focus = base.concat( [ M( { fwhm: 9 } ) ] );
      check( "FOCUS fires on a wide frame", Frames.anomalyFlags( focus, true )[12], [ "focus" ] );
      check( "and not on its neighbours",
             Frames.anomalyFlags( focus, true ).slice( 0, 12 ).every( function( f ) { return f.length == 0; } ), true );
      check( "TRACKING fires on an eccentricity spike",
             Frames.anomalyFlags( base.concat( [ M( { eccentricity: 0.9 } ) ] ), true )[12], [ "tracking" ] );
      check( "CLOUD fires on bright background",
             Frames.anomalyFlags( base.concat( [ M( { background: 0.2 } ) ] ), true )[12], [ "cloud" ] );
      check( "CLOUD fires on few stars",
             Frames.anomalyFlags( base.concat( [ M( { stars: 6000 } ) ] ), true )[12], [ "cloud" ] );
      check( "DROPPED, not CLOUD, on almost no stars",
             Frames.anomalyFlags( base.concat( [ M( { stars: 40 } ) ] ), true )[12], [ "dropped" ] );
      check( "flags keep their fixed order",
             Frames.anomalyFlags( base.concat( [ M( { fwhm: 9, eccentricity: 0.9, background: 0.2 } ) ] ), true )[12],
             [ "focus", "tracking", "cloud" ] );

      var flat = [ M( { stars: 1000 } ), M( { stars: 1000 } ), M( { stars: 1000 } ),
                   M( { stars: 1000 } ), M( { stars: 1 } ) ];
      check( "no spread still flags DROPPED",   Frames.anomalyFlags( flat, true )[4], [ "dropped" ] );
      flat[4] = M( { stars: 0 } );
      check( "zero stars is DROPPED (a real count)", Frames.anomalyFlags( flat, true )[4], [ "dropped" ] );
      check( "four frames flag nothing",
             Frames.anomalyFlags( flat.slice( 1 ), true ).every( function( f ) { return f.length == 0; } ), true );
      check( "a spread under 1% of the median flags nothing",
             Frames.anomalyFlags( spread( 8, function( i ) { return M( { fwhm: 4 + 0.001*i } ); } )
                                  .concat( [ M( { fwhm: 4.05 } ) ] ), true )[8], [] );
      var noBg = base.map( function( m ) { var c = M( m ); c.background = null; return c; } )
                     .concat( [ M( { background: null, stars: 6000 } ) ] );
      check( "null background falls back to the star test",
             Frames.anomalyFlags( noBg, true )[12], [ "cloud" ] );
      check( "an unmeasured row is skipped, not thrown on",
             Frames.anomalyFlags( base.concat( [ null ] ), true )[12], [] );
      check( "not comparable flags nothing",
             Frames.anomalyFlags( focus, false )[12], [] );

      check( "summary counts frames, lists kinds",
             Frames.flagSummary( [ [ "focus", "cloud" ], [], [ "cloud" ], [ "dropped" ] ] ),
             "3 flagged: 2 cloud, 1 focus, 1 dropped" );
      check( "empty summary", Frames.flagSummary( [ [], [] ] ), "" );

      var R = { state: Frames.STATE.REJECTED, override: null };
      var A = { state: Frames.STATE.APPROVED, override: null };
      check( "tags: rejected first, then each flag separately",
             Frames.frameTags( R, [ "cloud", "focus" ], true, false ),
             [ { text: "REJECTED", kind: "reject" }, { text: "CLOUD", kind: "flag" },
               { text: "FOCUS", kind: "flag" } ] );
      check( "tags: channel off while copying",
             Frames.frameTags( A, [], false, true ), [ { text: "CHANNEL OFF", kind: "reject" } ] );
      check( "tags: kept frame, flags only",
             Frames.frameTags( A, [ "tracking" ], true, false ),
             [ { text: "TRACKING", kind: "flag" } ] );

      var ch = fsFixtureChannel();
      check( "a channel carries one flag list per row", ch.flags.length, ch.rows.length );
      var mixed = Frames.newChannel( "O", fsFixtureChannel().entries, fsFixtureChannel().metrics,
                                     [ "exposure differs" ] );
      check( "a non-comparable channel is not flagged",
             mixed.flags.every( function( f ) { return f.length == 0; } ), true );
   } )();
```

(`fsFixtureChannel().entries/metrics` works because `newChannel` stores both on the channel.)

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement (end of Frames.js)**

```js
/* ---- anomaly flags ----------------------------------------------------
 *
 * Advisory, always on, and never a verdict: a flag does not reject, does
 * not change a state, does not reach the manifest. The tags name symptoms
 * -- "looks like" -- not diagnoses, and they are relative to the channel.
 */
Frames.FLAG_K = 3;
Frames.FLAG_MIN_FRAMES = 5;
Frames.DROPPED_FRACTION = 0.1;
Frames.FLAG_ORDER = [ "focus", "tracking", "cloud", "dropped" ];
Frames.FLAG_TAG = { focus: "FOCUS", tracking: "TRACKING", cloud: "CLOUD", dropped: "DROPPED" };
Frames.FLAG_BADGE = { focus: "F", tracking: "T", cloud: "C", dropped: "D" };
/* Summary order, as SubframeStudio words it. */
Frames.FLAG_SUMMARY_ORDER = [ "cloud", "focus", "tracking", "dropped" ];

/*
 * One metric's baseline over the channel: every row with a usable value --
 * positive, or non-negative for a star COUNT, where 0 is a real reading.
 * Null below FLAG_MIN_FRAMES values. `spread` says whether a sigma rule may
 * use it, with the same 1% floor the rejection gates use.
 */
Frames.flagBaseline = function( list, metric, allowZero )
{
   var v = [];
   for ( var i = 0; i < list.length; ++i )
   {
      var x = list[i] ? list[i][metric] : null;
      if ( typeof x == "number" && isFinite( x ) && ( allowZero ? x >= 0 : x > 0 ) )
         v.push( x );
   }
   if ( v.length < Frames.FLAG_MIN_FRAMES )
      return null;
   var med = Frames.median( v ), sd = Frames.sigma( v );
   return { median: med, sigma: sd,
            spread: sd > 0 && sd >= Frames.SIGMA_FLOOR_FRACTION*Math.abs( med ) };
};

Frames.anomalyFlags = function( list, comparable )
{
   var out = [];
   for ( var n = 0; n < list.length; ++n )
      out.push( [] );
   if ( !comparable )
      return out;

   var K = Frames.FLAG_K;
   var fw = Frames.flagBaseline( list, "fwhm", false );
   var ec = Frames.flagBaseline( list, "eccentricity", false );
   var bg = Frames.flagBaseline( list, "background", false );
   var st = Frames.flagBaseline( list, "stars", true );
   function num( v ) { return typeof v == "number" && isFinite( v ); }

   for ( var i = 0; i < list.length; ++i )
   {
      var m = list[i];
      if ( m == null )
         continue;
      var focus = fw && fw.spread && num( m.fwhm ) && m.fwhm > fw.median + K*fw.sigma;
      var tracking = ec && ec.spread && num( m.eccentricity ) &&
                     m.eccentricity > ec.median + K*ec.sigma;
      var dropped = st && num( m.stars ) && m.stars < Frames.DROPPED_FRACTION*st.median;
      var bright = bg && bg.spread && num( m.background ) &&
                   m.background > bg.median + K*bg.sigma;
      // A dropped frame is not ALSO cloud for the same low star count.
      var fewStars = !dropped && st && st.spread && num( m.stars ) &&
                     m.stars < st.median - K*st.sigma;
      var f = { focus: focus, tracking: tracking, cloud: bright || fewStars, dropped: dropped };
      for ( var o = 0; o < Frames.FLAG_ORDER.length; ++o )
         if ( f[Frames.FLAG_ORDER[o]] )
            out[i].push( Frames.FLAG_ORDER[o] );
   }
   return out;
};

/* "N flagged: a cloud, b focus", N counting frames. "" when none. */
Frames.flagSummary = function( flagsList )
{
   var frames = 0, per = {};
   for ( var i = 0; i < flagsList.length; ++i )
   {
      if ( flagsList[i].length )
         ++frames;
      for ( var j = 0; j < flagsList[i].length; ++j )
         per[flagsList[i][j]] = ( per[flagsList[i][j]] || 0 ) + 1;
   }
   if ( frames == 0 )
      return "";
   var parts = [];
   for ( var k = 0; k < Frames.FLAG_SUMMARY_ORDER.length; ++k )
   {
      var key = Frames.FLAG_SUMMARY_ORDER[k];
      if ( per[key] )
         parts.push( per[key] + " " + key );
   }
   return frames + " flagged: " + parts.join( ", " );
};

/* The preview's tags, top to bottom: the red one, then each flag alone. */
Frames.frameTags = function( row, flags, channelEnabled, copying )
{
   var tags = [];
   if ( Frames.leftOut( row, channelEnabled, copying ) )
      tags.push( { text: ( Frames.finalState( row.state, row.override ) == Frames.STATE.REJECTED )
                         ? "REJECTED" : "CHANNEL OFF", kind: "reject" } );
   for ( var i = 0; i < ( flags || [] ).length; ++i )
      tags.push( { text: Frames.FLAG_TAG[flags[i]], kind: "flag" } );
   return tags;
};
```

In `Frames.newChannel`, before `return`, build the result object into `var ch = {...}` and add:

```js
   var list = [];
   for ( var f = 0; f < rows.length; ++f )
      list.push( rows[f].metrics );
   /*
    * Computed once, from the measurements: knobs do not move a flag.
    * Frames that are not comparable are not flagged at all -- a raw
    * background compared across unlike frames means nothing.
    */
   ch.flags = Frames.anomalyFlags( list, ch.problems.length == 0 &&
                                         Frames.autoRejectAllowed( key ) );
   return ch;
```

- [ ] **Step 4: Run node both builds → PASS.** Teeth: remove `!dropped &&`, see "DROPPED, not CLOUD" fail; set `allowZero` false for stars, see "zero stars is DROPPED" fail; restore both.

- [ ] **Step 5: Commit** — `git commit -am "Anomaly flags: focus, tracking, cloud, dropped -- advisory, always on"`

---

### Task 7: Approval criteria panel

**Files:**
- Modify: `script/FrameSelector.js` — `MODE_NAMES` (~1203), `buildKnobPanel` (~1457-1571), `syncKnobs` (~1771), `refresh` (~1838), `commit` (~2025), `layOut` (~2070), `chooseDestination` caller (`destButton.onClick`)
- Test: `script/selftest.js` (IN_PIXINSIGHT block that builds `full` at ~4593)

**Interfaces:**
- Consumes: Tasks 1-3, 6.
- Produces: dialog members `criteriaGroup`, `keepLabel`, `critChecks[metric]`, `critEdits[metric]`, `critHints[metric]`; method `commitPendingEdits()`.

- [ ] **Step 1: Failing in-PixInsight test** — in the IN_PIXINSIGHT dialog test block, after `full.refresh();` add:

```js
         /*
          * The criteria panel: read-only in Relative, editable in
          * Thresholds, a hint in Both; the counter says what Run keeps.
          */
         var chH = pState.channels.H;
         ok = ok && ( full.criteriaGroup.title == "Approval criteria" );
         ok = ok && ( full.critEdits.fwhm.readOnly === true );
         ok = ok && ( full.keepLabel.text ==
                      Frames.keepCount( chH.rows, true, false ).keep + " / 6 keep" );
         full.modeCombo.currentItem = 1;                 // Thresholds
         full.modeCombo.onItemSelected( 1 );
         ok = ok && ( full.critEdits.fwhm.readOnly === false );
         ok = ok && ( full.presetCombo.enabled === false );
         // a clean field survives focus loss and Run: no silent rounding
         var storedBefore = Frames.limitValue( chH.settings, "fwhm" );
         full.critEdits.fwhm.onEditCompleted();
         full.commitPendingEdits();
         ok = ok && ( Frames.limitValue( chH.settings, "fwhm" ) === storedBefore );
         // an invalid entry reverts
         full.critEdits.fwhm.text = "abc";
         full.critEdits.fwhm.modified = true;
         full.critEdits.fwhm.onEditCompleted();
         ok = ok && ( Frames.limitValue( chH.settings, "fwhm" ) === storedBefore );
         ok = ok && ( full.critEdits.fwhm.text == Frames.formatLimit( "fwhm", storedBefore ) );
         // two dirty fields both survive a Run-time commit
         full.critEdits.fwhm.text = "6.6";   full.critEdits.fwhm.modified = true;
         full.critEdits.stars.text = "9000"; full.critEdits.stars.modified = true;
         full.commitPendingEdits();
         ok = ok && ( Frames.limitValue( chH.settings, "fwhm" ) === 6.6 );
         ok = ok && ( Frames.limitValue( chH.settings, "stars" ) === 9000 );
         // Both shows the relative limit beside the field
         full.modeCombo.currentItem = 2; full.modeCombo.onItemSelected( 2 );
         ok = ok && ( chH.gates.fwhm == null || !chH.gates.fwhm.active ||
                      full.critHints.fwhm.text.indexOf( "k:" ) >= 0 );
```

(`pState` has 6 frames, below `MIN_FRAMES`, so gates are absent and the prefill is blank; the checks above hold with a blank stored value -- `storedBefore` null, text "". The 20-frame check with real gates is in Task 10.)

- [ ] **Step 2: Run in PixInsight** (command in Task 4 Step 6) → FAIL ("dialog tests" false or `criteriaGroup` undefined).

- [ ] **Step 3: Implement**

`MODE_NAMES` stays the internal values; add labels:

```js
FrameSelector.MODE_LABELS = [ "Relative (k×MAD)", "Thresholds", "Both" ];
```

In `buildKnobPanel`: populate `modeCombo` from `MODE_LABELS`; its handler becomes:

```js
      this.modeCombo.onItemSelected = function( i )
      {
         var ch = self.channel();
         if ( ch == null || !self.editable() )
            return;
         var was = ch.settings.mode, now = FrameSelector.MODE_NAMES[i];
         ch.settings.mode = now;
         // Entering Thresholds or Both from Relative prefills every
         // checked criterion that nobody has typed into.
         if ( was == Frames.MODE.RELATIVE && now != Frames.MODE.RELATIVE )
            ch.settings.pendingPrefill = Frames.CRITERIA_ORDER.filter(
               function( m ) { return !!ch.settings.gating[m]; } );
         self.refresh();
      };
```

Delete `gateChecks`/`gateLabel` creation; add:

```js
      this.criteriaGroup = new GroupBox( this );
      this.criteriaGroup.title = "Approval criteria";

      this.keepLabel = new Label( this.criteriaGroup );
      this.keepLabel.textAlignment = TextAlign_Right | TextAlign_VertCenter;

      this.critChecks = {}; this.critEdits = {}; this.critHints = {};
      for ( var ci = 0; ci < Frames.CRITERIA_ORDER.length; ++ci )
         ( function( metric )
         {
            var cb = new CheckBox( self.criteriaGroup );
            cb.text = Frames.METRIC_HEADING[metric] + "  " + Frames.OPERATOR[metric];
            cb.toolTip = /* the two tooltip texts previously on gateChecks, verbatim */;
            cb.onCheck = function( checked )
            {
               var ch = self.channel();
               if ( ch == null || !self.editable() )
                  return;
               ch.settings.gating[metric] = checked;
               // Checking ONE criterion in Thresholds/Both prefills that one only.
               if ( checked && ch.settings.mode != Frames.MODE.RELATIVE )
                  ch.settings.pendingPrefill = [ metric ];
               self.refresh();
            };
            var ed = new Edit( self.criteriaGroup );
            ed.setScaledFixedWidth( 70 );
            ed.onEditCompleted = function() { self.commitEdit( metric ); };
            var hint = new Label( self.criteriaGroup );
            hint.useRichText = true;
            self.critChecks[metric] = cb;
            self.critEdits[metric] = ed;
            self.critHints[metric] = hint;
         } )( Frames.CRITERIA_ORDER[ci] );
```

(Copy the two tooltip strings from the deleted `gateChecks` loop -- the PSF SNR one and the generic one keyed by `Frames.METRIC_LABEL[metric]`.)

New methods on the dialog:

```js
   /*
    * Apply one field to the settings, ONLY if the user typed in it, and
    * WITHOUT refreshing. Edit.modified is set by typing and reset whenever
    * the program assigns text, so focus loss or Run never turn a rounded
    * display back into the stored limit. Returns whether it was dirty.
    */
   applyEdit( metric )
   {
      var ch = this.channel(), ed = this.critEdits[metric];
      if ( ch == null || ed == null || !ed.modified || !this.editable() )
         return false;
      var v = Frames.parseLimit( ed.text );
      if ( v !== undefined )
         ch.settings = Frames.withLimit( ch.settings, metric, v, true );
      return true;             // an invalid entry is redrawn from settings
   }

   commitEdit( metric )
   {
      if ( this.applyEdit( metric ) )
         this.refresh();
   }

   /*
    * Every dirty field FIRST, one refresh after. Refreshing between them
    * rewrites every field's text, which resets `modified` and would drop
    * every edit after the first.
    */
   commitPendingEdits()
   {
      var any = false;
      for ( var i = 0; i < Frames.CRITERIA_ORDER.length; ++i )
         any = this.applyEdit( Frames.CRITERIA_ORDER[i] ) || any;
      if ( any )
         this.refresh();
   }
```

In `syncKnobs`, replace the `gateChecks` loop with:

```js
            var relative = ch.settings.mode == Frames.MODE.RELATIVE;
            self.presetCombo.enabled = ch.settings.mode != Frames.MODE.ABSOLUTE;
            self.kEdit.enabled = ch.settings.mode != Frames.MODE.ABSOLUTE;
            self.modeCombo.currentItem = FrameSelector.MODE_NAMES.indexOf( ch.settings.mode );
            for ( var cm = 0; cm < Frames.CRITERIA_ORDER.length; ++cm )
            {
               var mk = Frames.CRITERIA_ORDER[cm];
               var on = !!ch.settings.gating[mk];
               self.critChecks[mk].checked = on;
               var ed = self.critEdits[mk];
               ed.text = Frames.formatLimit( mk, Frames.displayLimit( mk, ch.gates, ch.settings ) );
               ed.readOnly = relative;
               ed.enabled = on;
               var h = Frames.relativeHint( mk, ch.gates, ch.settings );
               self.critHints[mk].text = ( h == null ) ? ""
                  : "<span style='color:#808080'>(k: " + Frames.formatLimit( mk, h ) + ")</span>";
            }
            var kc = Frames.keepCount( ch.rows, ch.settings.enabled, self.copyingOut() );
            self.keepLabel.text = kc.keep + " / " + kc.total + " keep";
```

(`ed.text = ...` resets `ed.modified`, so a redraw never leaves a stale dirty flag.) Remove the old `self.modeCombo.currentItem = ...` line it duplicates.

In `refresh()`, the summary: append `Frames.flagSummary( ch.flags )` for the current channel when non-empty, or `"; not flagged: frames are not comparable"` when `ch.problems.length || !Frames.autoRejectAllowed( ch.key )`:

```js
            var cur = self.channel();
            if ( cur != null )
            {
               var fs = Frames.flagSummary( cur.flags || [] );
               var comparable = cur.problems.length == 0 && Frames.autoRejectAllowed( cur.key );
               self.summaryLabel.text += !comparable ? "; not flagged: frames are not comparable"
                                       : ( fs ? "  |  " + fs : "" );
            }
```

In `commit()`, first line after `if ( !self.editable() ) return null;`: `self.commitPendingEdits();`.

`destButton.onClick` already calls `self.refresh()`; `syncKnobs` reads `copyingOut()` so the counter follows.

In `layOut`, replace the `knobs` sizer with:

```js
      var row1 = new HorizontalSizer;
      row1.spacing = 6;
      var modeLabel = new Label( this.criteriaGroup ); modeLabel.text = "Mode";
      var presetLabel = new Label( this.criteriaGroup ); presetLabel.text = "Preset";
      row1.add( modeLabel ); row1.add( this.modeCombo );
      row1.addSpacing( 12 );
      row1.add( presetLabel ); row1.add( this.presetCombo ); row1.add( this.kEdit );
      row1.addSpacing( 12 );
      row1.add( this.enabledCheck );
      row1.addStretch();
      row1.add( this.keepLabel );

      var row2 = new HorizontalSizer;
      row2.spacing = 6;
      for ( var cr = 0; cr < Frames.CRITERIA_ORDER.length; ++cr )
      {
         var mk = Frames.CRITERIA_ORDER[cr];
         row2.add( this.critChecks[mk] );
         row2.add( this.critEdits[mk] );
         row2.add( this.critHints[mk] );
         row2.addSpacing( 16 );
      }
      row2.addStretch();

      this.criteriaGroup.sizer = new VerticalSizer;
      this.criteriaGroup.sizer.margin = 6;
      this.criteriaGroup.sizer.spacing = 6;
      this.criteriaGroup.sizer.add( row1 );
      this.criteriaGroup.sizer.add( row2 );
```

and in the main sizer replace `this.sizer.add( knobs );` with `this.sizer.add( this.criteriaGroup );`. The mode, preset, k and enable controls were created with `this` as parent; re-parent is not needed in PJSR sizers, but create them with `this.criteriaGroup` as parent in `buildKnobPanel` (move `buildKnobPanel` so `criteriaGroup` is created first in it).

In `release()`, null `onEditCompleted` on every `critEdits` entry and `onCheck` on every `critChecks` entry.

- [ ] **Step 4: Run node (both builds) and the PixInsight suite → PASS.**

- [ ] **Step 5: Commit** — `git commit -am "Approval criteria panel: limits visible, editable in Thresholds, hint in Both"`

---

### Task 8: Tags on the big preview

**Files:**
- Modify: `script/FrameSelector.js` — `PreviewControl` constructor (`this.tags = []`), new `setTags`, `paintViewport` (~925-963), dialog `selectRow` and `refresh`
- Test: `script/selftest.js` (IN_PIXINSIGHT dialog block)

**Interfaces:**
- Consumes: `Frames.frameTags`.
- Produces: `PreviewControl.setTags( tags )`, `PreviewControl.tagRects()` -> `[ Rect ]` in viewport coordinates (for the test), `FrameSelector.TAG_COLOURS`.

- [ ] **Step 1: Failing test** (PixInsight dialog block, after the criteria checks):

```js
         // separate tags, top-right, in viewport coordinates, in every mode
         full.preview.setTags( [ { text: "REJECTED", kind: "reject" },
                                 { text: "CLOUD", kind: "flag" },
                                 { text: "FOCUS", kind: "flag" } ] );
         var tr = full.preview.tagRects();
         ok = ok && ( tr.length == 3 );
         ok = ok && ( tr[0].y1 <= tr[1].y0 && tr[1].y1 <= tr[2].y0 );     // stacked, apart
         ok = ok && ( tr[0].x1 <= full.preview.viewport.width );
         full.preview.setFit( false );
         ok = ok && ( full.preview.tagRects()[0].x1 == tr[0].x1 );        // not moved by 1:1
         /*
          * And they are actually PAINTED: render the tags into a bitmap the
          * size of the viewport and read the colour at each tag's centre.
          */
         var tb = new Bitmap( full.preview.viewport.width, full.preview.viewport.height );
         tb.fill( 0xff000000 );
         var tg = new Graphics( tb );
         try { full.preview.paintTags( tg ); } finally { tg.end(); }
         // just inside the left edge: the fill, clear of the centred text
         function mid( r ) { return tb.pixel( r.x0 + 4, Math.round( ( r.y0 + r.y1 )/2 ) ); }
         ok = ok && ( mid( tr[0] ) == FrameSelector.TAG_COLOURS.reject.fill );
         ok = ok && ( mid( tr[1] ) == FrameSelector.TAG_COLOURS.flag.fill );
```

- [ ] **Step 2: Run in PixInsight → FAIL.**

- [ ] **Step 3: Implement**

```js
FrameSelector.TAG_COLOURS = { reject: { fill: 0xffb02222, ink: 0xffffffff },
                              flag:   { fill: 0xffc08a1e, ink: 0xff1a1a1a } };
FrameSelector.TAG = { W: 150, H: 26, GAP: 8, MARGIN: 10 };
```

`PreviewControl`:

```js
   setTags( tags )
   {
      this.tags = tags || [];
      this.viewport.update();
   }

   /* Where each tag goes: top-right, stacked, in VIEWPORT coordinates. */
   tagRects()
   {
      var T = FrameSelector.TAG, out = [], vw = this.viewport.width;
      for ( var i = 0; i < this.tags.length; ++i )
      {
         var y = T.MARGIN + i*( T.H + T.GAP );
         out.push( new Rect( vw - T.MARGIN - T.W, y, vw - T.MARGIN, y + T.H ) );
      }
      return out;
   }

   paintTags( g )
   {
      var r = this.tagRects();
      for ( var i = 0; i < this.tags.length; ++i )
      {
         var c = FrameSelector.TAG_COLOURS[this.tags[i].kind] || FrameSelector.TAG_COLOURS.flag;
         g.fillRect( r[i], new Brush( c.fill ) );
         g.pen = new Pen( c.ink );
         g.drawTextRect( r[i], this.tags[i].text, TextAlign_Center );
      }
   }
```

`paintViewport`: restructure so every path reaches the overlay -- replace the two `return;` statements with an `if/else`, and after the image is drawn:

```js
         if ( this.bmp != null )
         {
            if ( this.fit ) { /* existing fitted draw, no return */ }
            else
            {
               /* existing 1:1 offset draw */
               g.resetTransformation();
            }
         }
         this.paintTags( g );          // viewport coordinates, every path
```

Dialog: add `currentTags()`:

```js
   currentTags()
   {
      var ch = this.channel(), i = this.selectedRowIndex();
      if ( ch == null || i < 0 || i >= ch.rows.length )
         return [];
      return Frames.frameTags( ch.rows[i], ch.flags ? ch.flags[i] : [],
                               ch.settings.enabled, this.copyingOut() );
   }
```

Call `this.preview.setTags( this.currentTags() )` at the end of `refresh()` (criterion and override changes do not reload the bitmap). `selectRow` gets it in Task 9's rewrite; until then add the same line at the end of the existing `selectRow`. `PreviewControl.release()` sets `this.tags = []`.

Frame row tooltip in `fillFrames`: append `( ch.flags && ch.flags[i].length ? "\nlooks like: " + ch.flags[i].map( function( f ) { return Frames.FLAG_TAG[f].toLowerCase(); } ).join( ", " ) : "" )`.

- [ ] **Step 4: Run node + PixInsight → PASS.**

- [ ] **Step 5: Commit** — `git commit -am "Preview: REJECTED and each anomaly as its own tag, top-right"`

---

### Task 9: Filmstrip control and thumbnail loader

**Files:**
- Modify: `script/lib/Frames.js` (new `Frames.stripFirst`, `Frames.thumbnailOrder`)
- Modify: `script/FrameSelector.js` — new `FrameSelector.stretchedRender`, `FrameSelector.thumbnailOf`, `FrameSelector.Filmstrip` class (after `FrameSelector.Plot`), dialog `buildFilmstrip`, loader methods, `layOut`, `selectRow`, `refresh`, channel switch, `release`; `PreviewControl.load` uses `stretchedRender`
- Test: `script/selftest.js`

**Interfaces:**
- Produces: `Frames.stripFirst( count, selected, visible ) -> Number`; `Frames.thumbnailOrder( count, selected, first, visible ) -> [ Number ]`; `FrameSelector.stretchedRender( image ) -> Bitmap`; `FrameSelector.thumbnailOf( path ) -> Bitmap | null`; `FrameSelector.Filmstrip` with `setRows( rows, flags, crossed, selected )`, `setThumb( index, bmp )`, `visibleCount()`, `first`, `onPick`, `release()`; dialog `thumbs` (path -> `{ bmp, failed, used }`), `loaderTimer`, `loadQueue`, `loadGeneration`, `rebuildQueue()`, `loaderTick()`, `armLoader()`, `ticksObserved`.

- [ ] **Step 1: Failing node tests**

```js
   /* ---- filmstrip ordering --------------------------------------------- */
   ( function()
   {
      check( "strip centres the selection",  Frames.stripFirst( 74, 40, 11 ), 35 );
      check( "strip clamps at the start",    Frames.stripFirst( 74, 2, 11 ), 0 );
      check( "strip clamps at the end",      Frames.stripFirst( 74, 73, 11 ), 63 );
      check( "strip wider than the channel", Frames.stripFirst( 5, 3, 11 ), 0 );

      var o = Frames.thumbnailOrder( 20, 7, 2, 11 );
      check( "every index exactly once", o.slice().sort( function( a, b ) { return a - b; } ),
             [ 0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19 ] );
      check( "the selection first", o[0], 7 );
      check( "the visible range before anything else",
             o.slice( 0, 11 ).every( function( i ) { return i >= 2 && i < 13; } ), true );
      check( "then outward from the selection", o.slice( 11, 13 ), [ 1, 13 ] );
      check( "selection at the end", Frames.thumbnailOrder( 3, 2, 0, 11 ), [ 2, 1, 0 ] );
      check( "empty channel", Frames.thumbnailOrder( 0, -1, 0, 11 ), [] );
   } )();
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement pure helpers (Frames.js)**

```js
/* First tile index so the selection sits centred, clamped to the ends. */
Frames.stripFirst = function( count, selected, visible )
{
   if ( count <= visible )
      return 0;
   var s = ( selected < 0 ) ? 0 : selected;
   return Math.max( 0, Math.min( s - Math.floor( visible/2 ), count - visible ) );
};

/*
 * The order thumbnails are loaded in: the visible tiles first, nearest the
 * selection first, then the rest of the channel outward from it. Ties go
 * to the lower index. Every index appears exactly once.
 */
Frames.thumbnailOrder = function( count, selected, first, visible )
{
   var s = ( selected < 0 ) ? first : selected;
   function byDistance( a, b )
   {
      var d = Math.abs( a - s ) - Math.abs( b - s );
      return d != 0 ? d : a - b;
   }
   var inView = [], rest = [];
   for ( var i = 0; i < count; ++i )
      ( i >= first && i < first + visible ? inView : rest ).push( i );
   return inView.sort( byDistance ).concat( rest.sort( byDistance ) );
};
```

- [ ] **Step 4: Run node → PASS.**

- [ ] **Step 5: Shared stretch, thumbnail and preview cleanup (FrameSelector.js)**

```js
/*
 * Stretch an image in a throwaway window and render it. Image.render()
 * applies no STF, so a linear frame renders black without this. The
 * window is closed in finally, success or not.
 */
FrameSelector.stretchedRender = function( img )
{
   var med = img.median(), mad = img.MAD()*1.4826;
   var shadows = Math.max( 0, med - 2.8*mad );
   var midtone = Math.mtf( 0.25, Math.max( 1e-8, med - shadows ) );
   var dup = null;
   try
   {
      dup = new ImageWindow( img.width, img.height, img.numberOfChannels,
                             img.bitsPerSample, img.isReal, img.isColor,
                             Util.freeWindowId( "fs_render" ) );
      dup.mainView.beginProcess( UndoFlag_NoSwapFile );
      dup.mainView.image.assign( img );
      dup.mainView.endProcess();
      var H = new HistogramTransformation;
      H.H = [ [ 0, 0.5, 1, 0, 1 ], [ 0, 0.5, 1, 0, 1 ], [ 0, 0.5, 1, 0, 1 ],
              [ shadows, midtone, 1, 0, 1 ], [ 0, 0.5, 1, 0, 1 ] ];
      H.executeOn( dup.mainView );
      return dup.mainView.image.render();
   }
   finally
   {
      try { if ( dup != null && !dup.isNull ) dup.forceClose(); } catch ( e ) {}
   }
};

FrameSelector.THUMB = { W: 120, H: 80 };
FrameSelector.THUMB_CAP = 600;

/*
 * One frame's thumbnail: opened, shrunk IN its own throwaway window (no
 * full-size copy), stretched, rendered. Null when it cannot be read.
 */
FrameSelector.thumbnailOf = function( path )
{
   if ( !File.exists( path ) )
      return null;
   var win = null, ws = [];
   try
   {
      ws = ImageWindow.open( path );
      if ( ws.length == 0 )
         return null;
      win = ws[0];
      var img = win.mainView.image;
      var s = Math.min( FrameSelector.THUMB.W/img.width, FrameSelector.THUMB.H/img.height );
      win.mainView.beginProcess( UndoFlag_NoSwapFile );
      win.mainView.image.resample( s );
      win.mainView.endProcess();
      return FrameSelector.stretchedRender( win.mainView.image );
   }
   catch ( e )
   {
      Util.warn( "frames", "no thumbnail for " + path + ": " + e );
      return null;
   }
   finally
   {
      /*
       * EVERY window the open produced: a file can hold several images and
       * open returns one window per image.
       */
      FrameSelector.closeAll( ws );
   }
};

/* Close every window in a list, never throwing. */
FrameSelector.closeAll = function( ws )
{
   for ( var i = 0; i < ( ws || [] ).length; ++i )
      try { if ( ws[i] != null && !ws[i].isNull ) ws[i].forceClose(); } catch ( e ) {}
};
```

In `PreviewControl.load`, keep the whole `ws` array (declare `var ws = [];` beside `win`) and replace its `finally` with `FrameSelector.closeAll( ws );` -- today it closes only `ws[0]`.

In `PreviewControl.load`, replace the dup/HT/render block (lines ~1041-1062) with `self.bmp = FrameSelector.stretchedRender( img );` (the source `win` keeps its `finally` close). Add a method on `PreviewControl`: `thumbnail()` returning `this.bmp ? this.bmp.scaledTo( w, h )` -- use `Bitmap.scaled( s )` if `scaledTo` is absent; check `/Applications/PixInsight/doc/pjsr/objects/Bitmap/Bitmap.html` for the exact name (`scaled( Number sx[, Number sy] )`).

- [ ] **Step 6: Filmstrip control**

```js
/*
 * The channel's frames as a strip of thumbnails, SubframeStudio's
 * filmstrip. Painted by hand like the Plot; no scroll handlers of any
 * kind. Tiles are drawn from whatever thumbnails have arrived -- a grey
 * box until then, with the cross and badges already on it: the verdict
 * never waits for a picture.
 */
FrameSelector.Filmstrip = class extends Frame
{
   constructor( parent )
   {
      super( parent );
      var self = this;
      this.rows = []; this.flags = []; this.crossed = []; this.thumbs = [];
      this.failed = [];
      this.selected = -1; this.first = 0;
      this.onPick = null;
      this.setScaledMinHeight( FrameSelector.THUMB.H + 16 );
      this.onPaint = function() { self.paint(); };
      this.onMousePress = function( x, y )
      {
         var i = self.indexAt( x );
         if ( i >= 0 && self.onPick != null )
            self.onPick( i );
      };
   }

   tileW() { return FrameSelector.THUMB.W + 8; }
   visibleCount() { return Math.max( 1, Math.floor( this.width/this.tileW() ) ); }

   setRows( rows, flags, crossed, selected )
   {
      this.rows = rows; this.flags = flags || []; this.crossed = crossed || [];
      this.selected = selected;
      this.first = Frames.stripFirst( rows.length, selected, this.visibleCount() );
      this.update();
   }

   page( dir )
   {
      var v = this.visibleCount();
      this.first = Math.max( 0, Math.min( this.first + dir*v,
                                          Math.max( 0, this.rows.length - v ) ) );
      this.update();
   }

   indexAt( x )
   {
      var i = this.first + Math.floor( x/this.tileW() );
      return ( i >= 0 && i < this.rows.length && x >= 0 ) ? i : -1;
   }

   paint()
   {
      var g = null;
      try
      {
         g = new Graphics( this );
         g.fillRect( 0, 0, this.width, this.height, new Brush( 0xff1a1a1a ) );
         var T = FrameSelector.THUMB, tw = this.tileW();
         var last = Math.min( this.rows.length, this.first + this.visibleCount() );
         for ( var i = this.first; i < last; ++i )
         {
            var x = ( i - this.first )*tw + 4, y = 6;
            var r = new Rect( x, y, x + T.W, y + T.H );
            var bmp = this.thumbs[i];
            if ( bmp != null )
               g.drawBitmap( x + Math.round( ( T.W - bmp.width )/2 ),
                             y + Math.round( ( T.H - bmp.height )/2 ), bmp );
            else
            {
               g.fillRect( r, new Brush( 0xff3a3a3a ) );
               if ( this.failed[i] )           // could not be read: say so
               {
                  g.pen = new Pen( 0xffe0e0e0 );
                  g.drawTextRect( r, "!", TextAlign_Center );
               }
            }
            if ( this.crossed[i] )
            {
               g.pen = new Pen( FrameSelector.REJECT_COLOUR, 2 );
               g.drawLine( r.x0, r.y0, r.x1, r.y1 );
               g.drawLine( r.x1, r.y0, r.x0, r.y1 );
            }
            var fl = this.flags[i] || [];
            for ( var b = 0; b < fl.length; ++b )
            {
               var br = new Rect( x + 3 + b*18, y + 3, x + 3 + b*18 + 16, y + 19 );
               g.fillRect( br, new Brush( FrameSelector.TAG_COLOURS.flag.fill ) );
               g.pen = new Pen( FrameSelector.TAG_COLOURS.flag.ink );
               g.drawTextRect( br, Frames.FLAG_BADGE[fl[b]], TextAlign_Center );
            }
            var m = this.rows[i].metrics;
            g.pen = new Pen( 0xffe0e0e0 );
            g.drawTextRect( new Rect( x, y + T.H - 16, x + T.W, y + T.H ),
                            m ? Frames.displayValue( "fwhm", m.fwhm ) : "-", TextAlign_Center );
            if ( i == this.selected )
            {
               g.pen = new Pen( 0xffe0b020, 3 );
               g.drawRect( r );
            }
         }
      }
      catch ( e ) { /* a strip that cannot paint must not stop the review */ }
      finally { if ( g != null ) try { g.end(); } catch ( e2 ) {} }
   }

   release()
   {
      try { this.onPaint = null; this.onMousePress = null; this.onPick = null;
            this.thumbs = []; this.rows = []; } catch ( e ) {}
   }
};
```

- [ ] **Step 7: Loader in the dialog**

`buildFilmstrip()` (called from the constructor after `buildPlotPane`):

```js
   buildFilmstrip()
   {
      var self = this;
      this.thumbs = {};                  // path -> { bmp, used }
      this.thumbFailed = {};             // path -> true, never evicted
      this.thumbUse = 0;
      this.loadQueue = []; this.loadGeneration = 0; this.ticksObserved = 0;
      this.released = false;
      this.filmstrip = new FrameSelector.Filmstrip( this );
      this.filmstrip.onPick = function( i ) { self.selectRow( i ); };
      this.stripPrev = new ToolButton( this ); this.stripPrev.text = "‹";
      this.stripNext = new ToolButton( this ); this.stripNext.text = "›";
      this.stripPrev.onClick = function() { self.filmstrip.page( -1 ); self.rebuildQueue(); };
      this.stripNext.onClick = function() { self.filmstrip.page( 1 ); self.rebuildQueue(); };
      /*
       * Single shot, re-armed after each frame, so ticks never overlap.
       * NOT started here: the constructor may still throw after this, and
       * a running timer on a dialog nobody holds is what must not exist.
       * onShow arms it, once the entry point owns the object.
       */
      this.loaderTimer = new Timer;
      this.loaderTimer.singleShot = true;
      this.loaderTimer.interval = 0.05;     // seconds
      this.loaderTimer.onTimeout = function() { self.loaderTick(); };
      this.shown = false;
      this.onShow = function() { self.shown = true; self.armLoader(); };
   }

   /*
    * Never before onShow: the constructor's own selectRow(0) queues work,
    * and the timer must not run on an object the constructor might still
    * abandon by throwing.
    */
   armLoader()
   {
      if ( this.released || !this.shown || this.loadQueue.length == 0 ||
           this.loaderTimer.isRunning )
         return;
      this.loaderTimer.start();
   }

   /* Rebuilt on selection, page and channel change; stale results are dropped. */
   rebuildQueue()
   {
      var ch = this.channel();
      ++this.loadGeneration;
      this.loadQueue = [];
      if ( ch == null )
         return;
      var order = Frames.thumbnailOrder( ch.rows.length, this.selectedRowIndex(),
                                         this.filmstrip.first, this.filmstrip.visibleCount() );
      for ( var i = 0; i < order.length; ++i )
      {
         var p = ch.rows[order[i]].path;
         if ( this.thumbs[p] == null && !this.thumbFailed[p] )
            this.loadQueue.push( { path: p, key: ch.key, generation: this.loadGeneration } );
      }
      this.pushThumbs();
      this.armLoader();
   }

   loaderTick()
   {
      if ( this.released )
         return;
      try
      {
         ++this.ticksObserved;
         var item = this.loadQueue.shift();
         if ( item == null )
            return;
         var bmp = FrameSelector.thumbnailOf( item.path );
         // The dialog may have been released, or moved on, while reading.
         if ( this.released || item.generation != this.loadGeneration )
            return;
         this.storeThumb( item.path, bmp );
         this.pushThumbs();
      }
      catch ( e )
      {
         Util.warn( "frames", "thumbnail loading stopped: " + e );
         this.loadQueue = [];
      }
      finally
      {
         if ( !this.released )
            this.armLoader();
      }
   }

   /*
    * LRU over bitmaps, capped at 600 (about 24 MB). `used` is refreshed
    * whenever a thumbnail is SHOWN (pushThumbs), not only when stored, so
    * what is on screen is never the oldest. Failures are recorded apart in
    * `thumbFailed` and never evicted: a frame that could not be read is not
    * retried in this dialog.
    */
   storeThumb( path, bmp )
   {
      if ( bmp == null )
      {
         this.thumbFailed[path] = true;
         return;
      }
      this.thumbs[path] = { bmp: bmp, used: ++this.thumbUse };
      var keys = Object.keys( this.thumbs );
      if ( keys.length <= FrameSelector.THUMB_CAP )
         return;
      var self = this;
      keys.sort( function( a, b ) { return self.thumbs[a].used - self.thumbs[b].used; } );
      for ( var i = 0; i < keys.length - FrameSelector.THUMB_CAP; ++i )
         delete this.thumbs[keys[i]];
   }

   /* Hand the strip what exists for the current channel, touching it. */
   pushThumbs()
   {
      var ch = this.channel();
      if ( ch == null || this.filmstrip == null )
         return;
      var arr = [], failed = [];
      for ( var i = 0; i < ch.rows.length; ++i )
      {
         var p = ch.rows[i].path, t = this.thumbs[p];
         if ( t != null )
            t.used = ++this.thumbUse;
         arr.push( t ? t.bmp : null );
         failed.push( !!this.thumbFailed[p] );
      }
      this.filmstrip.thumbs = arr;
      this.filmstrip.failed = failed;
      this.filmstrip.update();
   }

   /* Rows, crosses, badges and selection into the strip. */
   syncStrip()
   {
      var ch = this.channel();
      if ( ch == null || this.filmstrip == null )
         return;
      var copying = this.copyingOut();
      var crossed = ch.rows.map( function( r ) { return Frames.leftOut( r, ch.settings.enabled, copying ); } );
      this.filmstrip.setRows( ch.rows, ch.flags, crossed, this.selectedRowIndex() );
      this.pushThumbs();
   }
```

Wire it:
- `selectRow( index, reload )`: replace the `preview.load` line with

```js
      if ( reload !== false && node.rowRef )
      {
         var path = node.rowRef.path;
         // The selected frame's thumbnail comes free from the full render.
         if ( this.preview.load( path ) && this.thumbs[path] == null )
            this.storeThumb( path, this.preview.thumbnail() );
      }
      this.plot.setSelected( index );
      this.preview.setTags( this.currentTags() );
      this.syncStrip();
      if ( reload !== false )
         this.rebuildQueue();
```

- The frame table's `onNodeSelectionUpdated` (FrameSelector.js ~1412) currently calls `preview.load` and `plot.setSelected` itself, which would leave the previous frame's tags and strip selection in place. Route it through `selectRow`, the single path:

```js
      this.frameTree.onNodeSelectionUpdated = function()
      {
         var n = self.frameTree.selectedNodes;
         if ( n.length && n[0].rowRef )
            self.selectRow( n[0].rowIndex );   // assigning currentNode does not re-fire this
         else
            self.plot.setSelected( -1 );
      };
```

- `PreviewControl.thumbnail()`:

```js
   /* The loaded frame, scaled to fit a filmstrip tile, aspect kept. */
   thumbnail()
   {
      if ( this.bmp == null )
         return null;
      var s = Math.min( FrameSelector.THUMB.W/this.bmp.width,
                        FrameSelector.THUMB.H/this.bmp.height );
      return this.bmp.scaled( s );
   }
```

(`Bitmap.scaled( Number s )` scales both axes by one factor; `scaledTo` forces both dimensions and would distort.)
- `refresh()`: at the end, `self.syncStrip();` (no queue rebuild: knobs do not change which frames need reading).
- channel switch (`channelTree.onNodeSelectionUpdated`): after `syncKnobs()`, `self.syncStrip(); self.rebuildQueue();`.
- `layOut`: a `HorizontalSizer` `[ stripPrev, filmstrip (stretch 100), stripNext ]` added to the main sizer between `this.plotPane` and `this.criteriaGroup`.
- `release()`, FIRST statements:

```js
      if ( this.released )
         return;
      this.released = true;
      try
      {
         if ( this.loaderTimer != null )
         {
            this.loaderTimer.stop();
            this.loaderTimer.onTimeout = null;
         }
         this.loadQueue = [];
         this.onShow = null;
         if ( this.filmstrip != null ) this.filmstrip.release();
         if ( this.stripPrev != null ) this.stripPrev.onClick = null;
         if ( this.stripNext != null ) this.stripNext.onClick = null;
         this.thumbs = {};
         this.thumbFailed = {};
      }
      catch ( e ) {}
```

(then the existing body). Because `released` now guards re-entry, existing double calls (button + onClose) stay safe.

- [ ] **Step 8: Run node → PASS; PixInsight suite → PASS (existing dialog tests exercise the constructor, selectRow and refresh with the strip present).**

- [ ] **Step 9: Commit** — `git commit -am "Filmstrip: thumbnails with crosses and flag badges, loaded one per tick"`

---

### Task 10: Constructor and entry-point teardown; live filmstrip and teardown tests

**Files:**
- Modify: `script/FrameSelector.js` — `Dialog` constructor body, `FrameSelector.main`
- Test: `script/selftest.js` (new IN_PIXINSIGHT block), a shell liveness check

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Constructor and entry point**

Wrap the constructor body after `super()`:

```js
   try
   {
      /* ...existing body from `var self = this;` through `this.layOut();`... */
   }
   catch ( e )
   {
      // A half-built dialog must not keep a timer, a handler or a bitmap.
      try { this.release(); } catch ( e2 ) {}
      throw e;
   }
```

(`super()` must stay first and outside the try: `this` does not exist before it.) `buildFilmstrip()` is already called once, from Task 9; do not add a second call.

`FrameSelector.main`, last line becomes:

```js
   var dialog = new FrameSelector.Dialog( state );
   try { dialog.execute(); }
   finally { dialog.release(); }
```

- [ ] **Step 2: Live filmstrip test (IN_PIXINSIGHT)**

```js
   /*
    * The filmstrip on REAL frames: 20 of the O set, copied so the originals
    * are never touched. Timer ticks are observed, every thumbnail arrives,
    * the crosses are exactly the frames Run leaves out, a click selects.
    * Then five build/show/close cycles closing MID-LOAD.
    */
   if ( IN_PIXINSIGHT ) ( function()
   {
      var src = "<a local folder of subframes>";
      var dst = "/tmp/agent-scratch/fs-filmstrip";
      var have = File.directoryExists( src ) ? FrameSelector.frameFilesIn( src ) : [];
      check( "the filmstrip fixture is present (20 frames)", have.length >= 20, true );
      if ( have.length < 20 )
         return;
      if ( !File.directoryExists( dst ) )
         File.createDirectory( dst, true );
      have.sort();
      var paths = [];
      for ( var i = 0; i < 20; ++i )
      {
         var to = dst + "/" + File.extractNameAndExtension( have[i] );
         if ( !File.exists( to ) )
            File.copyFile( to, have[i] );
         paths.push( to );
      }

      function waitFor( pred, seconds )
      {
         var until = Date.now() + seconds*1000;
         while ( !pred() && Date.now() < until )
            CoreApplication.processEvents();
         return pred();
      }

      var state = FrameSelector.buildState( dst, null );
      var key = state.order[0];
      var ch = state.channels[key];
      check( "the fixture is one channel of 20", ch.rows.length, 20 );

      // nothing loads before the dialog is shown
      var early = new FrameSelector.Dialog( FrameSelector.buildState( dst, null ) );
      check( "the loader is not running before show", early.loaderTimer.isRunning, false );
      early.release(); early.cancel();

      // real gates: switching to Thresholds keeps every verdict
      var rel = ch.rows.map( function( r ) { return r.state; } );
      var dlg = new FrameSelector.Dialog( state );
      dlg.show();
      dlg.modeCombo.currentItem = 1; dlg.modeCombo.onItemSelected( 1 );
      check( "Thresholds on real frames keeps every verdict",
             ch.rows.map( function( r ) { return r.state; } ), rel );
      check( "the FWHM field shows the prefilled limit",
             dlg.critEdits.fwhm.text,
             Frames.formatLimit( "fwhm", Frames.limitValue( ch.settings, "fwhm" ) ) );

      /*
       * Make the crossed set non-empty BY CONSTRUCTION: condemn the two
       * sharpest frames (which no FWHM threshold rejects) and set the
       * threshold to the median, which rejects every frame above it.
       */
      var byFwhm = ch.rows.map( function( r, i ) { return i; } )
                     .filter( function( i ) { return ch.rows[i].metrics; } )
                     .sort( function( a, b ) { return ch.rows[a].metrics.fwhm - ch.rows[b].metrics.fwhm; } );
      ch.rows[byFwhm[0]].override = Frames.OVERRIDE.CONDEMNED;
      ch.rows[byFwhm[1]].override = Frames.OVERRIDE.CONDEMNED;
      var medFwhm = Frames.median( byFwhm.map( function( i ) { return ch.rows[i].metrics.fwhm; } ) );
      ch.settings = Frames.withLimit( ch.settings, "fwhm", medFwhm, true );
      dlg.refresh();
      var expected = ch.rows.map( function( r ) { return Frames.leftOut( r, true, false ); } );
      check( "at least three frames are crossed", expected.filter( Boolean ).length >= 3, true );
      check( "crossed tiles are exactly the frames Run leaves out",
             dlg.filmstrip.crossed, expected );

      check( "every thumbnail arrives",
             waitFor( function() { return dlg.filmstrip.thumbs.filter( Boolean ).length == 20; }, 120 ),
             true );
      check( "the loader ticked for them", dlg.ticksObserved >= 19, true );
      dlg.filmstrip.onPick( 12 );
      check( "clicking a tile selects its row", dlg.selectedRowIndex(), 12 );
      // a table click goes through the same path: tags follow the frame
      dlg.frameTree.currentNode = dlg.frameTree.child( 5 );
      dlg.frameTree.child( 5 ).selected = true;
      dlg.frameTree.onNodeSelectionUpdated();
      check( "a table click moves the strip selection", dlg.filmstrip.selected, 5 );
      check( "and the preview's tags", JSON.stringify( dlg.preview.tags ),
             JSON.stringify( dlg.currentTags() ) );

      /*
       * A result for a stale generation is discarded. Queue one item,
       * rebuild the queue (the generation moves on), then run the stale
       * item by hand: nothing may be stored for it.
       */
      var staleDlg = new FrameSelector.Dialog( FrameSelector.buildState( dst, null ) );
      var stalePath = staleDlg.channel().rows[15].path;
      delete staleDlg.thumbs[stalePath];
      var staleItem = { path: stalePath, key: staleDlg.channel().key,
                        generation: staleDlg.loadGeneration };
      staleDlg.rebuildQueue();
      staleDlg.loadQueue = [ staleItem ];
      staleDlg.shown = true;
      staleDlg.loaderTick();
      check( "a stale thumbnail is discarded", staleDlg.thumbs[stalePath] == null, true );
      staleDlg.release(); staleDlg.cancel();
      dlg.release(); dlg.cancel();

      /*
       * Close mid-load, five times, through the WINDOW path only: cancel(),
       * no explicit release(), so onClose is what must stop the timer.
       */
      var midLoad = 0, reached = 0;
      for ( var c = 0; c < 5; ++c )
      {
         var d2 = new FrameSelector.Dialog( FrameSelector.buildState( dst, null ) );
         d2.show();
         if ( waitFor( function() { return d2.filmstrip.thumbs.filter( Boolean ).length >= 3; }, 30 ) )
            ++reached;
         if ( d2.loadQueue.length > 0 )
            ++midLoad;
         d2.cancel();
         CoreApplication.processEvents();
         check( "closing the window released it (" + c + ")", d2.released, true );
         check( "and stopped its timer (" + c + ")", d2.loaderTimer.isRunning, false );
      }
      check( "every cycle loaded at least three thumbnails", reached, 5 );
      check( "every cycle closed with loading still pending", midLoad, 5 );
   } )();
```

(`buildState( dst, null )` re-measures the copies once; the measurement cache makes later cycles fast. If `File.copyFile` argument order differs, check `/Applications/PixInsight/doc/pjsr/objects/File/File.html` -- PJSR is `File.copyFile( targetPath, sourcePath )`.)

- [ ] **Step 3: Run the suite in PixInsight, then the liveness check from the shell**

```bash
rm -f /tmp/agent-scratch/lhso-selftest.txt
/Applications/PixInsight/PixInsight.app/Contents/MacOS/PixInsight -x=1:"$HOME/PixInsight/scripts/Loom/script/selftest.js"
until [ -f /tmp/agent-scratch/lhso-selftest.txt ]; do sleep 5; done
head -30 /tmp/agent-scratch/lhso-selftest.txt
sleep 20      # let deferred deletes run after the script returned
ps -axo pid,comm | awk '$2 ~ /PixInsight$/'           # must still list PixInsight
cat > /tmp/agent-scratch/alive.js <<'EOF'
#engine v8
File.writeTextFile( "/tmp/agent-scratch/alive.txt", "alive " + Date.now() );
EOF
rm -f /tmp/agent-scratch/alive.txt
/Applications/PixInsight/PixInsight.app/Contents/MacOS/PixInsight -x=1:/tmp/agent-scratch/alive.js
until [ -f /tmp/agent-scratch/alive.txt ]; do sleep 2; done; cat /tmp/agent-scratch/alive.txt
```

Expected: PASS; PixInsight still listed; `alive.txt` written. Also check for a new crash report: `ls -t ~/Library/Logs/DiagnosticReports | grep -i pixinsight | head -1` has no entry newer than the run.

- [ ] **Step 4: Commit** — `git commit -am "Release on every exit; live filmstrip and mid-load teardown tests"`

---

### Task 11: README, complexity, visual check

**Files:**
- Modify: `README.md` (Frame Selector section)

- [ ] **Step 1: README** — document: the Approval criteria panel (modes, fields, prefill, hand edits stand, Both hint, keep counter meaning in cull vs copy); the filmstrip (crosses = left out by Run; badges; loading order; ~0.3 s pause per frame while loading); anomaly flags table with the exact rules, `FLAG_K = 3`, minimum 5 frames, always on, advisory only, none on non-comparable channels; SNR column is display only.

- [ ] **Step 2: Complexity** — `~/.claude/skills/complexity-analyzer/js/analyze.sh script/FrameSelector.js script/lib/Frames.js`; refactor any NEW function over 15.

- [ ] **Step 3: Visual check** — launch `FrameSelector.js` in the running PixInsight on `/tmp/agent-scratch/fs-filmstrip`; confirm with a screenshot (`screencapture -x /tmp/agent-scratch/fs.png`, then read it): group box, fields, counter, strip with crosses/badges, separate tags on the preview.

- [ ] **Step 4: Full test runs** — node both builds, PixInsight suite, liveness check.

- [ ] **Step 5: Commit** — `git commit -am "README: approval criteria, filmstrip, anomaly flags, SNR"`
