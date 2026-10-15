# Loom OSC (one-shot colour) masters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Loom (PixInsight scripts and the Siril port) takes debayered colour
masters from a one-shot colour camera, a broadband one and/or an Hα + OIII
dual-band one, splits them into the existing logical channels and runs the whole
pipeline on them; the result is ready to release as the next `v*` version.

**Architecture:** A colour master is a *physical input* in one of two new slots,
`OSC` and `OSCNB`. Everything downstream works on *logical channels*: `OSC`
supplies `R`, `G`, `B`; `OSCNB` supplies `H`, `O`. Pure decisions (filter
detection, selection rules, preflight errors, cache keys, registration plans)
live in `script/lib/Util.js` and `script/lib/Pipeline.js` and are tested under
node; pixel work (opening the master, splitting, grouped registration) lives in
`Pipeline.js` / `Steps.js` and is exercised inside PixInsight. The Siril port in
`siril/loomsiril/loom/` mirrors each function, pinned against values recorded
from the JavaScript.

**Tech Stack:** PJSR (`#engine v8`) on PixInsight 1.9.5 (and 1.9.4); node 20
for the pure-logic harness (`ci/run-tests.js` with `ci/pjsr-shim.js`); Python
3.10+ with numpy, scipy and tifffile for the Siril port; `siril-cli` 1.4.4 in the
Docker test image for Siril-marked tests.

**Spec:** `docs/superpowers/specs/2026-10-15-osc-support-design.md` (revision 5).
Read it before any task: this plan argues from it and does not repeat its reasons.

## Global Constraints

- PixInsight scripts keep running on PixInsight 1.9.4 and 1.9.5: in CI, `node ci/run-tests.js` and `LOOM_TEST_CORE_RELEASE=4 node ci/run-tests.js` both pass.
- Every new check in `script/selftest.js` sits inside an `if ( testGroup( "<group>" ) )` block; the groups used by this plan start with `osc.` and `ci/test-groups.js` stays green. Group selectors match whole namespace segments: `osc` runs every `osc.*` group, `pipeline` every `pipeline.*` group (`pipeline.*` is not a selector).
- While working, run only the groups and test files a task names; the full suites (both PixInsight core releases, the whole Siril suite, the Docker image) run in CI on the pull request.
- `ci/check-refs.js` stays green: every `NS.member` read is defined, every new file is in the include and packaging lists.
- Python floor 3.10 (no 3.11+ syntax); dependencies stay numpy, scipy, tifffile.
- Debayered three-channel masters only; one `OSC` and/or one `OSCNB` per run; a run with a colour master takes no mono master.
- `OSC` planes 0, 1, 2 are `R`, `G`, `B`. `OSCNB` plane 0 is `H`, plane 1 is `O`. Blue is never used for `O`.
- Every cache key that names a file is `Cache.hash( identity )` (hexadecimal); identity strings are never file names.
- `OSC` calibration uses the chosen sensor's R, G, B curves from `filters.xspd` and the Ideal QE curve, never a camera QE curve.
- Error texts, verbatim from the spec:
  - "colour masters cannot be combined with mono masters; choose either the colour masters or the mono masters"
  - "SHO and HSO need SII, which a colour camera cannot separate from Ha: choose HOO"
  - "this filter passes SII and Hβ; only Hα/OIII dual-band filters are supported"
  - "choose Broadband or Dual-band Hα + OIII for <file>"
- A light-pollution filter in front of an `OSC` camera never refuses a run; the run log notes that calibration does not model it.
- Fixtures are synthetic. No real camera data is committed.
- Implementation branch: `osc`, created off `origin/main`. The Frame Selector port is a separate branch that also edits `siril/README.md` and `siril/CHANGELOG.md`: keep this work's edits to those two files inside their own `OSC` section/bullets so the two branches merge without overlap.

## Review Focus

These conditions are implied by the spec and most likely to hurt a user; each
has a test in the task named.

1. A `FILTER` keyword with odd spelling or spacing for a known dual-band model (`L eXtreme`, `l-extreme`, `LeXtreme`) must still be detected as that model, not as a family alias (Task 1).
2. A folder holding both WBPP colour masters and older mono masters of the same target: both are listed and the run is blocked, never silently mixed (Task 10).
3. A colour master whose `FILTER` is empty: kind is broadband and confident, but a remembered override for one blank-filter file must not apply to another blank-filter file (Tasks 3, 11).
4. A second run after the user only changes the sensor in the dialog: the master is not reopened, but white balance and the composite SPCC are re-measured (Tasks 5, 7).
5. An `OSCNB` master that covers less of the sky than the `OSC` master (different framing): registration and the common crop still produce pixel-aligned siblings, and the clean white balance is re-registered (Tasks 8, 12).

---

## File structure

**PixInsight (`pixinsight/`)**

| File | Responsibility in this plan |
|---|---|
| `script/lib/Util.js` | OSC filter table and detection, colour master name parsing, `numberOfChannels` in header info, supplied-channel helper, selection problems (mix, palettes, channel count, kind). |
| `script/lib/Config.js` | New keys `OSC`/`OSCNB` in paths and views, `oscKinds`, `oscSensor`, `oscKindChoices`, persisted fields. |
| `script/lib/Pipeline.js` | Consumers switched to logical channels, preflight, colour group identity and keys, lazy shared loader and split, calibration response, registration plan and grouped registration, clean white balance grouping, provenance and log lines. |
| `script/lib/Steps.js` | Per-channel bandwidths in `Steps.spccNarrowband`; a plane split helper and a plane combine helper. |
| `script/lib/UI.js` | Discovery of colour candidates, masters list kind control, greyed boxes, sensor colour filter choice. |
| `script/selftest.js` | New `osc.*` test groups. |
| `README.md`, `CHANGELOG.md`, `ci/release-notes.sh`, `ci/release-notes.test.js` | OSC documentation and release notes. |
| `ci/release-prep.js`, `ci/release-prep.test.js` (new), `ci/release.sh`, `ci/run-tests.js`, `docs/RELEASING.md` | The release script also finalises the Siril changelog and version. |

**Siril port (`siril/`)**

| File | Responsibility |
|---|---|
| `loomsiril/loom/osc.py` (new) | Python port of the OSC pure functions (detection, parsing, supplied channels, problems, keys, registration plan). |
| `loomsiril/loom/channels.py` | Consumers switched to logical channels; colour candidates in `scan_masters`. |
| `loomsiril/loom/config.py` | New config keys and persistence. |
| `loomsiril/loom/preflight.py` | OSC preflight errors. |
| `loomsiril/loom/pipeline.py` | Colour group loading and split, grouped registration step. |
| `loomsiril/loom/stages_geometry.py` | Grouped registration of a colour group in Siril. |
| `loomsiril/loom/stages_colour.py`, `cache.py` | Calibration identity in `rgb_stage_params` and the clean white-balance key; per-channel narrowband bandwidths. |
| `loomsiril/loom/siril_ops.py` | OSC SPCC command builder and name checks. |
| `loomsiril/loom/dialog.py` | Kind control, greyed boxes, sensor choice. |
| `tools/oracle/loom_osc.js` (new), `tests/oracle/loom_osc.json` (new) | Recorded JavaScript values for the pure functions. |
| `tests/loom/test_osc.py` (new) and the listed test files | Unit tests; `tests/loom/integration/test_osc_siril.py` (new) for `siril`-marked runs. |
| `docs/PARITY-LOOM.md`, `README.md`, `CHANGELOG.md`, `tests/test_package.py` | Parity rows, user docs, release notes, version consistency check. |

---

## Phase A: PixInsight

All commands in Phase A run from `pixinsight/`. Run the task's groups while
working: `LOOM_TEST_ONLY=osc.detect node ci/run-tests.js` (replace the group;
several are comma-separated). Where a step says "existing groups", run those
groups by name (for example `LOOM_TEST_ONLY=util,ui,pipeline`); the full suite
runs in CI. Every "Step N: Run ... → PASS" below means the named groups print PASS.

### Task 1: Filter table and kind detection

**Files:**
- Modify: `script/lib/Util.js` (add after `Util.channelFromFilter`, currently line 670-692, and its tables `Util.FILTER_LETTERS` / `Util.FILTER_NAMES`)
- Test: `script/selftest.js` (new group `osc.detect`)

**Interfaces:**
- Produces: `Util.OSC_FILTERS` (array of `{ match, name, kind, haNm, oiiiNm }`, `match` a normalised full name); `Util.OSC_BROADBAND` (array of normalised names); `Util.OSC_FAMILIES` (array of normalised alias tokens); `Util.normaliseFilter( value ) -> string` (lowercase, `[^a-z0-9]` removed; `""` for null); `Util.oscFilterInfo( filterValue ) -> { kind: "broadband"|"dualband"|"unsupported", name, confident, haNm, oiiiNm } | null`.

- [ ] **Step 1: Write the failing tests** (append a new block in `selftest.js`, after the last `testGroup( "util" )` block):

```javascript
   } if ( testGroup( "osc.detect" ) ) {
   // Known models win over family aliases; matching ignores case and punctuation.
   var ext = Util.oscFilterInfo( "L-eXtreme" );
   check( "oscFilterInfo L-eXtreme kind", ext.kind, "dualband" );
   check( "oscFilterInfo L-eXtreme confident", ext.confident, true );
   check( "oscFilterInfo L-eXtreme passbands", [ ext.haNm, ext.oiiiNm ], [ 7, 7 ] );
   check( "oscFilterInfo spelling variants agree",
          [ "L eXtreme", "l-extreme", "LeXtreme", "L_eXtreme" ].map( function( n ) {
             return Util.oscFilterInfo( n ).name; } ),
          [ "L-eXtreme", "L-eXtreme", "L-eXtreme", "L-eXtreme" ] );
   check( "oscFilterInfo L-Ultimate passbands",
          [ Util.oscFilterInfo( "L-Ultimate" ).haNm, Util.oscFilterInfo( "L-Ultimate" ).oiiiNm ], [ 3, 3 ] );
   check( "oscFilterInfo ALP-T Ha/OIII supported",
          Util.oscFilterInfo( "ALP-T Ha/OIII" ).kind, "dualband" );
   check( "oscFilterInfo ALP-T SII/Hb unsupported",
          Util.oscFilterInfo( "ALP-T SII/Hb" ).kind, "unsupported" );
   check( "oscFilterInfo Greek spellings",
          [ Util.oscFilterInfo( "ALP-T Hα/OIII" ).kind, Util.oscFilterInfo( "ALP-T Hα/OIII" ).haNm,
            Util.oscFilterInfo( "ALP-T SII/Hβ" ).kind ],
          [ "dualband", 5, "unsupported" ] );
   // Broadband names and an absent FILTER are broadband, confidently.
   check( "oscFilterInfo UV/IR", Util.oscFilterInfo( "UV/IR Cut" ).kind, "broadband" );
   check( "oscFilterInfo L-Pro", Util.oscFilterInfo( "L-Pro" ).kind, "broadband" );
   check( "oscFilterInfo empty FILTER", [ Util.oscFilterInfo( "" ).kind, Util.oscFilterInfo( "" ).confident ],
          [ "broadband", true ] );
   check( "oscFilterInfo absent FILTER", Util.oscFilterInfo( null ).kind, "broadband" );
   // Family aliases are dual-band but not confident.
   check( "oscFilterInfo family alias", [ Util.oscFilterInfo( "Duo Narrowband" ).kind,
                                          Util.oscFilterInfo( "Duo Narrowband" ).confident ],
          [ "dualband", false ] );
   check( "oscFilterInfo quad alias", Util.oscFilterInfo( "Quad band" ).confident, false );
   check( "oscFilterInfo unknown", Util.oscFilterInfo( "Astronomik Deep-Sky" ), null );
   check( "normaliseFilter", Util.normaliseFilter( " L-eXtreme 2\" " ), "lextreme2" );
```

- [ ] **Step 2: Run to see it fail.** `LOOM_TEST_ONLY=osc.detect node ci/run-tests.js` → FAIL (`Util.oscFilterInfo` undefined; `ci/check-refs.js` also reports the undefined member).

- [ ] **Step 3: Implement.** In `Util.js`:

```javascript
Util.normaliseFilter = function( value )
{
   if ( value == null )
      return "";
   // Greek line names are spelt out before punctuation is removed, so
   // "Hα/OIII" and "Ha/OIII" (and "Hβ" and "Hb") meet in the table.
   return String( value ).toLowerCase()
      .replace( /α/g, "a" ).replace( /β/g, "b" )
      .replace( /[^a-z0-9]/g, "" );
};

/*
 * Colour-camera filters by full normalised model name. Passbands are the
 * manufacturer's Ha and OIII bandwidths in nm. An entry whose line pair is
 * not Ha/OIII is "unsupported": its H and O planes would not hold Ha and OIII.
 */
Util.OSC_FILTERS = [
   { match: "lextreme",     name: "L-eXtreme",       kind: "dualband",    haNm: 7,   oiiiNm: 7 },
   { match: "lultimate",    name: "L-Ultimate",      kind: "dualband",    haNm: 3,   oiiiNm: 3 },
   { match: "lenhance",     name: "L-eNhance",       kind: "dualband",    haNm: 10,  oiiiNm: 24 },
   { match: "lenhanced",    name: "L-eNhance",       kind: "dualband",    haNm: 10,  oiiiNm: 24 },
   { match: "alpthaoiii",   name: "ALP-T Ha/OIII",   kind: "dualband",    haNm: 5,   oiiiNm: 5 },
   { match: "alpthaoiii5nm",name: "ALP-T Ha/OIII",   kind: "dualband",    haNm: 5,   oiiiNm: 5 },
   { match: "alptsiihb",    name: "ALP-T SII/Hb",    kind: "unsupported", haNm: null, oiiiNm: null },
   { match: "alptsiihbeta", name: "ALP-T SII/Hb",    kind: "unsupported", haNm: null, oiiiNm: null },
   { match: "nbz",          name: "NBZ",             kind: "dualband",    haNm: 12,  oiiiNm: 12 }
];
Util.OSC_BROADBAND = [ "uvir", "uvircut", "ircut", "uvirblock", "lpro", "cls", "clsccd", "lrgb" ];
Util.OSC_FAMILIES  = [ "duo", "dual", "dualband", "tri", "triad", "quad", "alpt" ];

Util.oscFilterInfo = function( filterValue )
{
   var f = Util.normaliseFilter( filterValue );
   for ( var i = 0; i < Util.OSC_FILTERS.length; ++i )
      if ( Util.OSC_FILTERS[i].match == f )
      {
         var m = Util.OSC_FILTERS[i];
         return { kind: m.kind, name: m.name, confident: m.kind != "unsupported",
                  haNm: m.haNm, oiiiNm: m.oiiiNm };
      }
   if ( f.length == 0 || Util.OSC_BROADBAND.indexOf( f ) >= 0 )
      return { kind: "broadband", name: f.length == 0 ? "" : String( filterValue ).trim(),
               confident: true, haNm: null, oiiiNm: null };
   for ( var j = 0; j < Util.OSC_FAMILIES.length; ++j )
      if ( f.indexOf( Util.OSC_FAMILIES[j] ) == 0 )
         return { kind: "dualband", name: String( filterValue ).trim(), confident: false,
                  haNm: null, oiiiNm: null };
   return null;
};
```

Check the UV/IR entries against the test names: `"UV/IR Cut"` normalises to `uvircut`, `"L-Pro"` to `lpro`. Before writing the table, look up each model's published Ha and OIII bandwidth and correct the numbers above if the manufacturer states otherwise; keep the test values in step with the table.

- [ ] **Step 4: Run the group.** `LOOM_TEST_ONLY=osc.detect node ci/run-tests.js` → PASS.
- [ ] **Step 5: Existing groups and commit.** `LOOM_TEST_ONLY=util node ci/run-tests.js` → PASS. Commit `script/lib/Util.js script/selftest.js`: "OSC: colour-camera filter table and kind detection".

### Task 2: Channel count in header info and colour master names

**Files:**
- Modify: `script/lib/Util.js` `Util.headerInfo` (line 1284-1301), `Util.readImageInfo` (line 1347), add `Util.parseColourMasterName` after `Util.parseMasterName` (line 1610)
- Test: `script/selftest.js` (new group `osc.header`)

**Interfaces:**
- Consumes: `Util.oscFilterInfo` (Task 1).
- Produces: `Util.headerInfo( F, f, d )` result gains `numberOfChannels` (integer; 1 when the description does not say); `Util.parseColourMasterName( name ) -> { filter, colour: true|false, drizzle, autocrop } | null`; `Util.channelCountProblem( path, n ) -> string | null`.

- [ ] **Step 1: Failing tests.**

```javascript
   } if ( testGroup( "osc.header" ) ) {
   // headerInfo takes an already-read description; fake one with three channels.
   var fakeD = [ { width: 40, height: 30, numberOfChannels: 3 } ];
   var hi = Util.headerInfo( null, null, fakeD );
   check( "headerInfo numberOfChannels", hi.numberOfChannels, 3 );
   check( "headerInfo defaults to one channel",
          Util.headerInfo( null, null, [ { width: 4, height: 3 } ] ).numberOfChannels, 1 );
   check( "channelCountProblem 2 channels",
          Util.channelCountProblem( "/m/a.xisf", 2 ),
          "/m/a.xisf has 2 channels; Loom takes mono (1) or colour (3) masters" );
   check( "channelCountProblem 3 channels", Util.channelCountProblem( "/m/a.xisf", 3 ), null );
   var cn = Util.parseColourMasterName(
      "masterLight_BIN-1_6248x4176_EXPOSURE-300.00s_FILTER-L-eXtreme_color_drizzle_2x_autocrop.xisf" );
   check( "parseColourMasterName hyphenated filter", cn.filter, "L-eXtreme" );
   check( "parseColourMasterName colour flag", cn.colour, true );
   check( "parseColourMasterName drizzle", cn.drizzle, "2x" );
   check( "parseColourMasterName autocrop", cn.autocrop, true );
   check( "parseColourMasterName _RGB",
          Util.parseColourMasterName( "masterLight_FILTER-UVIR_RGB.xisf" ).colour, true );
   check( "parseColourMasterName mono name",
          Util.parseColourMasterName( "masterLight_FILTER-Ha_mono.xisf" ).colour, false );
   check( "parseColourMasterName no filter token",
          Util.parseColourMasterName( "masterLight_color.xisf" ).filter, "" );
   // parseMasterName keeps its mono behaviour.
   check( "parseMasterName unchanged",
          Util.parseMasterName( "masterLight_BIN-1_FILTER-L_mono.xisf" ).channel, "L" );
```

`Util.headerInfo( F, f, d )` today reads geometry from `d`; read its body before changing it and keep its other fields. If its signature or the shape of `d` differs from the fake above, adjust the fake to match the real shape (the check names stay).

- [ ] **Step 2: Run** `LOOM_TEST_ONLY=osc.header node ci/run-tests.js` → FAIL.
- [ ] **Step 3: Implement.** In `Util.headerInfo` add
`numberOfChannels: ( d && d[0] && d[0].numberOfChannels > 0 ) ? d[0].numberOfChannels : 1`
to the returned object, and pass it through in `Util.readImageInfo`. Add:

```javascript
Util.channelCountProblem = function( path, n )
{
   if ( n == 1 || n == 3 )
      return null;
   return path + " has " + n + " channels; Loom takes mono (1) or colour (3) masters";
};

/*
 * A WBPP colour master name: FILTER-<token> runs to the next underscore,
 * hyphens included, and _color / _RGB marks the colour case. The header
 * stays authoritative; this only orders the work.
 */
Util.parseColourMasterName = function( name )
{
   var n = String( name || "" );
   var m = /FILTER-([^_.]*)/i.exec( n );
   var dm = /drizzle_(\d+)x/i.exec( n );
   return {
      filter: m ? m[1] : "",
      colour: /_(color|colour|RGB)(_|\.|$)/i.test( n ),
      drizzle: dm ? ( dm[1] + "x" ) : "",
      autocrop: Util.isAutocropName( n )
   };
};
```

- [ ] **Step 4: Run** the group and `util,imageinfo` → PASS.
- [ ] **Step 5: Commit**: "OSC: channel count in header info, colour master names".

### Task 3: Config keys and logical channels

**Files:**
- Modify: `script/lib/Config.js` (`Config.FIELDS` line 68, `Config.defaults` line 126, `Config.load` line 274, `Config.save` line 346)
- Modify: `script/lib/Util.js` (`Util.validateSelection` line 603, `Util.duplicateFileProblems` line 645; add helpers)
- Test: `script/selftest.js` (new group `osc.config`)

**Interfaces:**
- Produces:
  - `Util.COLOUR_SLOTS = [ "OSC", "OSCNB" ]`; `Util.COLOUR_MEMBERS = { OSC: [ "R", "G", "B" ], OSCNB: [ "H", "O" ] }`.
  - `Util.colourSupplied( config ) -> [ "OSC" | "OSCNB" ]` (slots with a path or view).
  - `Util.monoSupplied( config ) -> [ channel keys ]`.
  - `Util.suppliedChannels( config ) -> [ logical keys ]` in `Util.CHANNELS` order, without duplicates.
  - `Util.colourMixProblem( config ) -> string | null`.
  - `Util.oscKindKey( filterValue, path ) -> "filter:<normalised>" | "path:<path>"`.
  - `Util.effectiveOscKind( slot, filterValue, path, choices ) -> { kind, name, confident, haNm, oiiiNm, source: "choice"|"detected"|"none" }`.
  - Config fields: `config.paths.OSC`, `config.paths.OSCNB`, `config.views.OSC`, `config.views.OSCNB`, `config.oscKinds` (`{ OSC: kind, OSCNB: kind }`, filled by preflight), `config.oscSensor` (`{ R, G, B }` curve names), `config.oscKindChoices` (`{ <key>: "broadband"|"dualband" }`, persisted).

- [ ] **Step 1: Failing tests.**

```javascript
   } if ( testGroup( "osc.config" ) ) {
   function oscConfig( paths, views )
   {
      var c = Config.defaults();
      for ( var k in paths ) c.paths[k] = paths[k];
      for ( var v in ( views || {} ) ) c.views[v] = views[v];
      return c;
   }
   check( "suppliedChannels OSC", Util.suppliedChannels( oscConfig( { OSC: "/m/rgb.xisf" } ) ),
          [ "R", "G", "B" ] );
   check( "suppliedChannels OSCNB", Util.suppliedChannels( oscConfig( { OSCNB: "/m/nb.xisf" } ) ),
          [ "H", "O" ] );
   check( "suppliedChannels both", Util.suppliedChannels( oscConfig( { OSC: "/m/a", OSCNB: "/m/b" } ) ),
          [ "R", "G", "B", "H", "O" ] );
   check( "suppliedChannels mono unchanged", Util.suppliedChannels( oscConfig( { L: "/m/l", H: "/m/h" } ) ),
          [ "L", "H" ] );
   check( "suppliedChannels colour view",
          Util.suppliedChannels( oscConfig( {}, { OSC: "rgb_view" } ) ), [ "R", "G", "B" ] );
   check( "colourMixProblem mixed",
          Util.colourMixProblem( oscConfig( { OSC: "/m/a", L: "/m/l" } ) ),
          "colour masters cannot be combined with mono masters; choose either the colour masters or the mono masters" );
   check( "colourMixProblem colour only", Util.colourMixProblem( oscConfig( { OSC: "/m/a", OSCNB: "/m/b" } ) ), null );
   check( "validateSelection OSCNB alone has something to do",
          Util.validateSelection( { OSCNB: "/m/b" }, {} ).length, 0 );
   check( "duplicateFileProblems colour vs mono",
          Util.duplicateFileProblems( { OSC: "/m/a.xisf", R: "/m/a.xisf" } ).length, 1 );
   // Overrides are keyed by filter name, or by path when the filter is blank.
   check( "oscKindKey filter", Util.oscKindKey( "L-eXtreme", "/m/a" ), "filter:lextreme" );
   check( "oscKindKey blank uses path", Util.oscKindKey( "", "/m/a" ), "path:/m/a" );
   var choices = { "path:/m/a": "dualband" };
   check( "effectiveOscKind choice beats detection",
          Util.effectiveOscKind( "OSC", "", "/m/a", choices ).kind, "dualband" );
   check( "effectiveOscKind other blank file not affected",
          Util.effectiveOscKind( "OSC", "", "/m/b", choices ).source, "detected" );
   check( "effectiveOscKind unsupported cannot be overridden",
          Util.effectiveOscKind( "OSCNB", "ALP-T SII/Hb", "/m/c", { "filter:alptsiihb": "dualband" } ).kind,
          "unsupported" );
   check( "effectiveOscKind unknown name has no kind",
          Util.effectiveOscKind( "OSCNB", "Astronomik X", "/m/d", {} ).source, "none" );
   // Persistence: oscSensor and oscKindChoices survive save/load.
   var store = {}, mem = { read: function( k ) { return store[k]; }, write: function( k, v ) { store[k] = v; } };
   var c1 = Config.defaults();
   c1.oscSensor = { R: "Sony Color Sensor R", G: "Sony Color Sensor G", B: "Sony Color Sensor B" };
   c1.oscKindChoices = { "filter:duonarrowband": "dualband" };
   Config.save( c1, mem );
   var c2 = Config.load( mem );
   check( "Config persists oscSensor", c2.oscSensor, c1.oscSensor );
   check( "Config persists oscKindChoices", c2.oscKindChoices, c1.oscKindChoices );
```

The `mem` store must match the store interface `Config.save` / `Config.load` use (read `Config.pixinsightStore`, line 33, and an existing persistence test in the `util` or `ui` groups); adapt the two lambdas to that interface.

- [ ] **Step 2: Run** `LOOM_TEST_ONLY=osc.config node ci/run-tests.js` → FAIL.
- [ ] **Step 3: Implement** the helpers listed under Interfaces. `Util.validateSelection( paths, views )` builds a config-like object `{ paths: paths, views: views }` and uses `Util.suppliedChannels` for the "nothing to do" decision (R, G and B all present, or any of H, S, O). `Util.duplicateFileProblems` iterates `Util.CHANNELS.concat( Util.COLOUR_SLOTS )`. `Util.effectiveOscKind`:
  1. `info = Util.oscFilterInfo( filterValue )`; if `info && info.kind == "unsupported"` return it with `source: "detected"`.
  2. `choice = choices[ Util.oscKindKey( filterValue, path ) ]`; if set, return `{ kind: choice, name: info ? info.name : String( filterValue || "" ), confident: true, haNm: info ? info.haNm : null, oiiiNm: info ? info.oiiiNm : null, source: "choice" }`.
  3. if `info && info.confident` return it with `source: "detected"`.
  4. return `{ kind: null, name: ..., confident: false, haNm: null, oiiiNm: null, source: "none" }`.
  In `Config.js`, add the three fields to `Config.FIELDS` (JSON-serialised like the existing object fields; read how another object-valued field is stored) and their defaults (`oscSensor: { R: "", G: "", B: "" }`, `oscKindChoices: {}`, `oscKinds: {}`), and make `paths` / `views` defaults carry `OSC: ""` and `OSCNB: ""`.
- [ ] **Step 4: Run** the group and `util,ui,pipeline` → PASS (`paths` gained two keys).
- [ ] **Step 5: Commit**: "OSC: colour slots in the configuration, logical channels, kind overrides".

### Task 4: Preflight and the logical-channel consumers

**Files:**
- Modify: `script/lib/Pipeline.js` `Pipeline.preflightChannels` (39), `Pipeline.preflight` (58), `Pipeline.narrowbandSupplied` (95), `Pipeline.wantedPalettes` (110), `Pipeline.reportSkippedPalettes` (127), `Pipeline.marsProblems` (150), `Pipeline.preflightView` (167), `Pipeline.preflightPath` (181)
- Test: `script/selftest.js` (new group `osc.preflight`)

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: `Pipeline.oscPaletteProblem( config ) -> string | null`; `Pipeline.inputChannelProblems( config, infoOf ) -> [ string ]` (every physical input, mono and colour, file or view); `Pipeline.oscKindProblems( config, infoOf ) -> [ string ]` where `infoOf( slot ) -> { filter, path, numberOfChannels, width, height }` (injectable for tests; production is `Pipeline.oscInfoOf( config )`, a function of the slot that reads `Util.readImageInfo( config.paths[slot] )` or the open view's image); `Pipeline.preflight( config )` fills `config.oscKinds` and returns problems including the four verbatim errors. `Pipeline.narrowbandSupplied( config )` keeps its contract: an array of the supplied narrowband channel keys.

- [ ] **Step 1: Failing tests.**

```javascript
   } if ( testGroup( "osc.preflight" ) ) {
   function cfg( paths, palettes )
   {
      var c = Config.defaults();
      for ( var k in paths ) c.paths[k] = paths[k];
      if ( palettes ) c.palettes = palettes;   // use the real palette field name from Config.defaults()
      return c;
   }
   function infoOf( table ) { return function( slot ) { return table[slot]; }; }
   check( "oscPaletteProblem SHO with colour",
          Pipeline.oscPaletteProblem( cfg( { OSCNB: "/m/nb" }, [ "SHO", "HOO" ] ) ),
          "SHO and HSO need SII, which a colour camera cannot separate from Ha: choose HOO" );
   check( "oscPaletteProblem HOO only", Pipeline.oscPaletteProblem( cfg( { OSCNB: "/m/nb" }, [ "HOO" ] ) ), null );
   check( "oscPaletteProblem mono run unaffected",
          Pipeline.oscPaletteProblem( cfg( { H: "/m/h", S: "/m/s", O: "/m/o" }, [ "SHO" ] ) ), null );
   check( "narrowbandSupplied OSCNB", Pipeline.narrowbandSupplied( cfg( { OSCNB: "/m/nb" } ) ), [ "H", "O" ] );
   // Channel counts are checked for every physical input, not only colour slots.
   var mc = cfg( { R: "/m/r.xisf", H: "/m/h.xisf" } );
   check( "inputChannelProblems colour file in a mono slot",
          Pipeline.inputChannelProblems( mc, infoOf( {
             R: { filter: "R", path: "/m/r.xisf", numberOfChannels: 3, width: 9, height: 9 },
             H: { filter: "Ha", path: "/m/h.xisf", numberOfChannels: 2, width: 9, height: 9 } } ) ),
          [ "/m/r.xisf is a colour master (3 channels); a mono slot needs a one-channel master",
            "/m/h.xisf has 2 channels; Loom takes mono (1) or colour (3) masters" ] );
   // Two colour masters of different sizes are allowed (registration and the common crop handle the overlap).
   var sz = cfg( { OSC: "/m/a.xisf", OSCNB: "/m/b.xisf" } ); sz.oscKindChoices = {};
   check( "oscKindProblems different sizes allowed",
          Pipeline.oscKindProblems( sz, infoOf( {
             OSC:   { filter: "UV/IR Cut", path: "/m/a.xisf", numberOfChannels: 3, width: 100, height: 80 },
             OSCNB: { filter: "L-eXtreme", path: "/m/b.xisf", numberOfChannels: 3, width: 90, height: 70 } } ) ), [] );
   check( "wantedPalettes OSCNB gives HOO",
          Pipeline.wantedPalettes( cfg( { OSCNB: "/m/nb" }, [ "HOO" ] ) ), [ "HOO" ] );
   var c = cfg( { OSC: "/m/a.xisf", OSCNB: "/m/b.xisf" } );
   var probs = Pipeline.oscKindProblems( c, infoOf( {
      OSC:   { filter: "UV/IR Cut", path: "/m/a.xisf", numberOfChannels: 3, width: 100, height: 80 },
      OSCNB: { filter: "Duo",       path: "/m/b.xisf", numberOfChannels: 3, width: 100, height: 80 } } ) );
   check( "oscKindProblems unconfident needs a choice",
          probs, [ "choose Broadband or Dual-band Hα + OIII for /m/b.xisf" ] );
   check( "oscKindProblems fills oscKinds", c.oscKinds.OSC, "broadband" );
   var c2 = cfg( { OSCNB: "/m/c.xisf" } );
   check( "oscKindProblems unsupported filter",
          Pipeline.oscKindProblems( c2, infoOf( { OSCNB: { filter: "ALP-T SII/Hb", path: "/m/c.xisf",
                                                          numberOfChannels: 3, width: 9, height: 9 } } ) ),
          [ "this filter passes SII and Hβ; only Hα/OIII dual-band filters are supported" ] );
   var c3 = cfg( { OSC: "/m/m.xisf" } );
   check( "oscKindProblems mono file in a colour slot",
          Pipeline.oscKindProblems( c3, infoOf( { OSC: { filter: "", path: "/m/m.xisf",
                                                        numberOfChannels: 1, width: 9, height: 9 } } ) ),
          [ "/m/m.xisf is a mono master (1 channel); a colour slot needs a three-channel master" ] );
   // A broadband master used as OSCNB is a kind mismatch.
   var c4 = cfg( { OSCNB: "/m/u.xisf" } );
   check( "oscKindProblems broadband in the dual-band slot",
          Pipeline.oscKindProblems( c4, infoOf( { OSCNB: { filter: "UV/IR Cut", path: "/m/u.xisf",
                                                          numberOfChannels: 3, width: 9, height: 9 } } ) ),
          [ "/m/u.xisf is a broadband colour master; the dual-band slot needs a Dual-band Hα + OIII master" ] );
```

Find the palette selection field in `Config.defaults()` (search `HOO` in `Config.js`) and use it in `cfg`.

- [ ] **Step 2: Run** `LOOM_TEST_ONLY=osc.preflight node ci/run-tests.js` → FAIL.
- [ ] **Step 3: Implement.**
  - `Pipeline.narrowbandSupplied`, `wantedPalettes`, `reportSkippedPalettes` and `marsProblems` read `Util.suppliedChannels( config )` instead of `config.paths` / `config.views`.
  - `Pipeline.oscPaletteProblem`: colour supplied and SHO or HSO requested → the verbatim error.
  - `Pipeline.oscKindProblems`: for each supplied colour slot, read `infoOf( slot )`; one channel → the mono-file error above; any other count than 3 → `Util.channelCountProblem`; else `Util.effectiveOscKind( slot, filter, path, config.oscKindChoices )`; `unsupported` → verbatim error; `source == "none"` → "choose Broadband or Dual-band Hα + OIII for <path>"; kind not matching the slot (`OSC` must be broadband, `OSCNB` dual-band) → the mismatch error; otherwise set `config.oscKinds[slot]`. The two colour masters may differ in size: registration and the common crop handle the overlap, as for mono masters.
  - `Pipeline.inputChannelProblems`: for every physical input (each of `Util.CHANNELS` and `Util.COLOUR_SLOTS`, file or view), a mono slot holding three channels gives "<path> is a colour master (3 channels); a mono slot needs a one-channel master"; any count other than 1 or 3 gives `Util.channelCountProblem`; colour slots are left to `oscKindProblems`. Open views are read through the same `infoOf` (number of channels from `view.image.numberOfChannels`).
  - `Pipeline.preflight` adds, in this order: `Util.colourMixProblem`, `Pipeline.oscPaletteProblem`, `Pipeline.inputChannelProblems( config, Pipeline.oscInfoOf( config ) )`, `Pipeline.oscKindProblems( config, Pipeline.oscInfoOf( config ) )` where `Pipeline.oscInfoOf( config )( slot )` reads `Util.readImageInfo( config.paths[slot] )` or the view's image, and the sensor check from Task 7 (leave a call to `Pipeline.oscSensorProblems( config )` returning `[]` for now; Task 7 fills it).
  - `Pipeline.preflightChannels`, `preflightPath` and `preflightView` iterate `Util.CHANNELS.concat( Util.COLOUR_SLOTS )`; for colour slots they skip the mono-only keyword checks that Task 7 replaces.
- [ ] **Step 4: Run** the group and `pipeline` → PASS.
- [ ] **Step 5: Commit**: "OSC: preflight errors and logical channels for palettes and MGC".

### Task 5: Colour group identity and cache keys

**Files:**
- Modify: `script/lib/Pipeline.js` (add after `Pipeline.buildStageKeys`, line 764; modify `Pipeline.cleanWhiteBalanceKey`, line 1117)
- Test: `script/selftest.js` (new group `osc.keys`)

**Interfaces:**
- Consumes: `Cache.hash` (Cache.js 111), `Cache.chainKey` (141), `Pipeline.cropTag` (1103).
- Produces:
  - `Pipeline.OSC_RECIPES = { OSC: { R: 0, G: 1, B: 2 }, OSCNB: { H: 0, O: 1 } }`.
  - `Pipeline.oscGroupIdentity( fingerprint, kind ) -> string` = `fingerprint + "|osc:" + kind`.
  - `Pipeline.oscSiblingKey( groupIdentity, plane ) -> hex` = `Cache.hash( groupIdentity + "|plane:" + plane )`.
  - `Pipeline.oscValidateName( groupIdentity ) -> string` = `Cache.hash( groupIdentity + "|validate" ) + ".osc.json"`.
  - `Pipeline.oscGroupRegisterKey( stage, siblingKeysInPlaneOrder, params ) -> hex` = `Cache.chainKey( Cache.hash( siblingKeys.join( "|" ) ), stage, params )`; `stage` is `"registerGroup"` (the clean branch never registers a colour group, see Task 9).
  - `Pipeline.oscRegisteredSiblingKey( groupKey, plane ) -> hex` = `Cache.hash( groupKey + "|plane:" + plane )`.
  - `Pipeline.cleanWhiteBalanceKey( chans, config, common, lumInstrume, refKey )` gains `refKey` and hashes `refKey`, the sensor curve names (`config.oscSensor` when `OSC` is used, else the existing filters) and the QE curve name.

- [ ] **Step 1: Failing tests.**

```javascript
   } if ( testGroup( "osc.keys" ) ) {
   var gid = Pipeline.oscGroupIdentity( "fp-abc", "broadband" );
   check( "oscGroupIdentity", gid, "fp-abc|osc:broadband" );
   var hexOnly = /^[0-9a-f]+$/;
   var k0 = Pipeline.oscSiblingKey( gid, 0 ), k1 = Pipeline.oscSiblingKey( gid, 1 );
   check( "sibling keys are hexadecimal", [ hexOnly.test( k0 ), hexOnly.test( k1 ) ], [ true, true ] );
   check( "sibling keys differ by plane", k0 != k1, true );
   check( "sibling key changes with kind",
          Pipeline.oscSiblingKey( Pipeline.oscGroupIdentity( "fp-abc", "dualband" ), 0 ) != k0, true );
   var vname = Pipeline.oscValidateName( gid );
   check( "validate sidecar name", /^[0-9a-f]+\.osc\.json$/.test( vname ), true );
   var g1 = Pipeline.oscGroupRegisterKey( "registerGroup", [ k0, k1, "k2" ], { ref: "refA", planes: "R,G,B" } );
   check( "group key hexadecimal", hexOnly.test( g1 ), true );
   check( "group key changes with one sibling",
          Pipeline.oscGroupRegisterKey( "registerGroup", [ k0, "k1x", "k2" ], { ref: "refA", planes: "R,G,B" } ) != g1, true );
   check( "group key changes with the reference",
          Pipeline.oscGroupRegisterKey( "registerGroup", [ k0, k1, "k2" ], { ref: "refB", planes: "R,G,B" } ) != g1, true );
   check( "registered sibling keys hexadecimal",
          hexOnly.test( Pipeline.oscRegisteredSiblingKey( g1, 2 ) ), true );
```

Then add three checks for `Pipeline.cleanWhiteBalanceKey`: build the arguments the way the existing `cleanWhiteBalanceKey` tests do (search `cleanWhiteBalanceKey` in `selftest.js`) and assert that the key changes when only `refKey` changes, when only `config.oscSensor.G` changes (with `paths.OSC` set), and that a mono configuration's key also changes with `refKey`. Use the `common` shape `Pipeline.cropTag` expects (read line 1103).

- [ ] **Step 2: Run** `LOOM_TEST_ONLY=osc.keys node ci/run-tests.js` → FAIL.
- [ ] **Step 3: Implement** as listed under Interfaces. Update every caller of `Pipeline.cleanWhiteBalanceKey` (search the file) to pass the reference's cache key.
- [ ] **Step 4: Run** the group and `LOOM_TEST_ONLY=pipeline.keys node ci/run-tests.js` → PASS (update the existing expected values there only where the new `refKey` argument is the reason; say so in the commit message).
- [ ] **Step 5: Commit**: "OSC: colour group identity and cache keys; clean white balance keyed on the reference".

### Task 6: Shared loader, split and validation

**Files:**
- Modify: `script/lib/Pipeline.js` `Pipeline.loadChannels` (1668), `Pipeline.lazyFileChannel` (1733), `Pipeline.duplicateViewChannel` (1684), `Pipeline.ensureLoaded` (757); add `Pipeline.oscGroupLoader`, `Pipeline.oscPlaneProblem`, `Pipeline.oscValidationRecord`
- Modify: `script/lib/Steps.js` (add `Steps.extractPlane( view, plane, newId ) -> ImageWindow`, `Steps.planeStats( view, plane ) -> { min, max, median }`)
- Test: `script/selftest.js` (new group `osc.load`)

**Interfaces:**
- Consumes: Task 5 keys; `Cache.store` (433), `Cache.load` (462), `Cache.dir` (84).
- Produces: `Pipeline.oscCacheIo( config, base ) -> io` (the wrapper keeps the wrapped io as `io.base`) wraps the production io with the run's cache policy: when `config.useCache` is false (including after `Cache.disableIfDirMissing`, Cache.js 73), `cacheLoad`, `cacheStore`, `readSidecar` and `writeSidecar` do nothing (null / no write) and the loader keeps the split planes and statistics in memory for the run; when `config.ignoreCache` is true, loads return null but stores and sidecar writes happen (refreshing the cache), as the existing stages do (Pipeline.js 1007, 1064, 1073). Lazy channel objects for `R`,`G`,`B` (from `OSC`) and `H`,`O` (from `OSCNB`) with the same shape `Pipeline.lazyFileChannel` returns today (`key, path, window, view, filter, instrume, sourceKey, currentKey, load()`), plus `group: "OSC"|"OSCNB"`, `plane: n`; `Pipeline.oscGroupLoader( slot, source, kind, reg, io ) -> { open(), planeWindow( plane ), release(), opens }` where `io` holds injectable `openMaster`, `cacheLoad`, `cacheStore`, `writeSidecar`, `readSidecar` functions (production passes the real ones); `Pipeline.oscPlaneProblem( stats, plane, path ) -> string | null`.

- [ ] **Step 1: Failing tests** (node, with fake io; no pixels):

```javascript
   } if ( testGroup( "osc.load" ) ) {
   check( "oscPlaneProblem constant plane",
          Pipeline.oscPlaneProblem( { min: 0, max: 0, median: 0 }, 2, "/m/a.xisf" ),
          "/m/a.xisf: plane 2 is constant (all values 0); the master looks broken" );
   check( "oscPlaneProblem normal plane",
          Pipeline.oscPlaneProblem( { min: 0.01, max: 0.9, median: 0.1 }, 0, "/m/a.xisf" ), null );
   var sidecars = {};   // persists across loaders, like the cache directory
   function fakeIo( cached )
   {
      var io = { opens: 0, stored: [] };
      io.openMaster = function() { io.opens++; return { id: "master", planes: 3 }; };
      io.cacheLoad = function( key ) { return cached[key] ? { id: "cached_" + key } : null; };
      io.cacheStore = function( key ) { io.stored.push( key ); cached[key] = true; };
      io.planeWindow = function( master, plane ) { return { id: "plane" + plane }; };
      io.planeStats = function() { return { min: 0.01, max: 0.9, median: 0.1 }; };
      io.writeSidecar = function( name, rec ) { sidecars[name] = JSON.stringify( rec ); };
      io.readSidecar = function( name ) { return sidecars[name] ? JSON.parse( sidecars[name] ) : null; };
      io.closeMaster = function() {};
      return io;
   }
   var cache = {};
   var io = fakeIo( cache );
   var loader = Pipeline.oscGroupLoader( "OSC", { path: "/m/a.xisf", fingerprint: "fp" }, "broadband", null, io );
   loader.planeWindow( 0 ); loader.planeWindow( 1 ); loader.planeWindow( 2 );
   check( "loader opens the master once for three siblings", io.opens, 1 );
   check( "loader stores every split plane", io.stored.length, 3 );
   var io2 = fakeIo( cache );
   var loader2 = Pipeline.oscGroupLoader( "OSC", { path: "/m/a.xisf", fingerprint: "fp" }, "broadband", null, io2 );
   loader2.planeWindow( 1 );
   check( "warm run does not open the master", io2.opens, 0 );
   check( "warm run logs the cached plane statistics", loader2.validation(), loader.validation() );
   check( "validation record holds three planes", loader2.validation().planes.length, 3 );
   delete cache[ Pipeline.oscSiblingKey( Pipeline.oscGroupIdentity( "fp", "broadband" ), 1 ) ];
   var io3 = fakeIo( cache );
   Pipeline.oscGroupLoader( "OSC", { path: "/m/a.xisf", fingerprint: "fp" }, "broadband", null, io3 ).planeWindow( 1 );
   check( "a missing split plane reopens the master once", io3.opens, 1 );
   // OSCNB: H is plane 0, O is plane 1, blue never requested.
   // Cache policy: no cache means no persistent reads or writes, one open, planes kept in memory.
   var off = Config.defaults(); off.useCache = false;
   var ioOff = Pipeline.oscCacheIo( off, fakeIo( {} ) );
   var lOff = Pipeline.oscGroupLoader( "OSC", { path: "/m/z.xisf", fingerprint: "fz" }, "broadband", null, ioOff );
   lOff.planeWindow( 0 ); lOff.planeWindow( 2 );
   check( "cache off: one open, nothing stored", [ ioOff.base.opens, ioOff.base.stored.length ], [ 1, 0 ] );
   check( "cache off: statistics kept in memory", lOff.validation().planes.length, 3 );
   var ign = Config.defaults(); ign.useCache = true; ign.ignoreCache = true;
   var warmCache = {}; warmCache[ Pipeline.oscSiblingKey( Pipeline.oscGroupIdentity( "fy", "broadband" ), 0 ) ] = true;
   var ioIgn = Pipeline.oscCacheIo( ign, fakeIo( warmCache ) );
   Pipeline.oscGroupLoader( "OSC", { path: "/m/y.xisf", fingerprint: "fy" }, "broadband", null, ioIgn ).planeWindow( 0 );
   check( "ignore cache: cached plane not reused, cache refreshed", [ ioIgn.base.opens, ioIgn.base.stored.length ], [ 1, 3 ] );
   var cache4 = {}, io4 = fakeIo( cache4 );
   var nb = Pipeline.oscGroupLoader( "OSCNB", { path: "/m/n.xisf", fingerprint: "fn" }, "dualband", null, io4 );
   nb.planeWindow( 0 ); nb.planeWindow( 1 );
   check( "OSCNB stores two planes", io4.stored.length, 2 );
```

`loader.validation()` returns the record read from the sidecar (`{ planes: [ { min, max, median } x3 ] }`); the validation sidecar holds the statistics of all three planes even for `OSCNB`, because they are measured once on the opened master.

- [ ] **Step 2: Run** `LOOM_TEST_ONLY=osc.load node ci/run-tests.js` → FAIL.
- [ ] **Step 3: Implement.**
  - `Pipeline.oscGroupLoader`: on `planeWindow( plane )`, try `io.cacheLoad( siblingKey )`; on a miss open the master once (`io.openMaster`), measure `io.planeStats` for planes 0-2, refuse with `Pipeline.oscPlaneProblem` (throw `new Error( problem )`), write the sidecar `Pipeline.oscValidationRecord( stats )`, store every plane the slot uses (`OSC`: 0,1,2; `OSCNB`: 0,1) with `io.cacheStore`, then close the master. `validation()` returns the sidecar record.
  - Production `io`: `openMaster` = `ImageWindow.open( path )[0]` registered in `reg`; `planeWindow` = `Steps.extractPlane`; `planeStats` = `Steps.planeStats`; `cacheStore` / `cacheLoad` = `Cache.store` / `Cache.load`; sidecar files under `Cache.dir()` with the Task 5 name, written with `File.writeTextFile( ..., JSON.stringify( rec ) )`.
  - `Steps.extractPlane( view, plane, newId )`: a new one-channel float `ImageWindow` of the view's size whose image is `view.image` channel `plane` (use `Image.selectedChannel` and `Image.assign` the way `Pipeline.duplicateViewChannel` copies images); keywords copied, `FILTER` set to the channel letter (`R`,`G`,`B`,`Ha`,`OIII` as the exports name them), `LOOMSRC` = `<file name>|<recipe>`.
  - `Pipeline.loadChannels`: for each supplied colour slot create one loader and lazy channels for its members (`plane` from `Pipeline.OSC_RECIPES`); `sourceKey` = `currentKey` = the sibling key; `load()` calls `loader.planeWindow( plane )`. Views (`config.views.OSC`) use a loader whose `openMaster` returns the view's window without closing it.
  - The run log gets one line per colour group: "OSC <file>: broadband, filter <name>, planes R/G/B, min/max/median <values>", taken from `validation()` on cold and warm runs alike.
- [ ] **Step 4: Run** the group and `pipeline,cache` → PASS.
- [ ] **Step 5: In PixInsight** (hand check, logged in the PR description): the `osc.load` checks also pass inside PixInsight (`Loom selftest` script), and `Steps.extractPlane` on a synthetic three-channel image returns the right plane (add an `IN_PIXINSIGHT`-guarded check that builds a 3×2 three-channel image with distinct plane values).
- [ ] **Step 6: Commit**: "OSC: one shared loader per colour master, cached split planes, plane validation".

### Task 7: Calibration response

**Files:**
- Modify: `script/lib/Pipeline.js` `Pipeline.broadbandStages` (1836), `Pipeline.broadbandRunners` (1879), `Pipeline.compositeInstrument` (2122), `Pipeline.inheritInstrument` (1780), `Pipeline.buildPalette` (1453); add `Pipeline.oscSensorProblems`, `Pipeline.oscSpfcParams`
- Modify: `script/lib/Steps.js` `Steps.spccNarrowband` (1461)
- Test: `script/selftest.js` (new group `osc.calibration`)

**Interfaces:**
- Consumes: `config.oscSensor`, `config.oscKinds`, `Util.oscFilterInfo`.
- Produces: `Pipeline.effectiveFilters( config ) -> { R, G, B, ... }` (the `config.filters` object with R, G and B replaced by `config.oscSensor` when `OSC` is used; `config.filters` unchanged otherwise); `Pipeline.oscSpfcParams( channel, config ) -> { channel, filter: <sensor curve>, qe: "Ideal QE curve" }`; `Pipeline.oscSensorProblems( config, curveExists ) -> [ string ]` (`curveExists( name, channel )` injectable; production `Steps.filterCurveByName`); `Steps.spccNarrowband( view, palette, bandwidthNm )` where `bandwidthNm` is a number (all channels) or `{ R, G, B }`; `Pipeline.oscNarrowbandBandwidths( config ) -> { R, G, B }` for HOO from `OSCNB`.

- [ ] **Step 1: Failing tests.**

```javascript
   } if ( testGroup( "osc.calibration" ) ) {
   var c = Config.defaults();
   c.paths.OSC = "/m/a.xisf";
   c.oscSensor = { R: "Sony Color Sensor R", G: "Sony Color Sensor G", B: "Sony Color Sensor B" };
   check( "oscSpfcParams uses the sensor curve and Ideal QE",
          Pipeline.oscSpfcParams( "G", c ),
          { channel: "G", filter: "Sony Color Sensor G", qe: "Ideal QE curve" } );
   check( "oscSensorProblems missing curve",
          Pipeline.oscSensorProblems( c, function( name ) { return name != "Sony Color Sensor B"; } ),
          [ "sensor colour filter curve 'Sony Color Sensor B' is not in filters.xspd" ] );
   var empty = Config.defaults(); empty.paths.OSC = "/m/a.xisf";
   check( "oscSensorProblems sensor not chosen",
          Pipeline.oscSensorProblems( empty, function() { return true; } ),
          [ "choose the sensor colour filters for the colour master (SPCC section)" ] );
   // SPFC stage keys move with the sensor (they already include filter and qe).
   var a = Pipeline.buildStageKeys( "src-G", { solve: {}, spfc: Pipeline.oscSpfcParams( "G", c ), mgc: { marsFiles: [] },
                                               graxpert: { enabled: false } } );
   c.oscSensor.G = "Other Color Sensor G";
   var b = Pipeline.buildStageKeys( "src-G", { solve: {}, spfc: Pipeline.oscSpfcParams( "G", c ), mgc: { marsFiles: [] },
                                               graxpert: { enabled: false } } );
   check( "spfc key changes with the sensor", a[1].key != b[1].key, true );
   check( "compositeInstrument is null for an OSC composite",
          Pipeline.compositeInstrument( { R: { group: "OSC", instrume: "ZWO ASI2600MC" },
                                          G: { group: "OSC", instrume: "ZWO ASI2600MC" },
                                          B: { group: "OSC", instrume: "ZWO ASI2600MC" } }, "G" ), null );
   // Composite and clean-reference calibration read the effective filters, never config.filters directly.
   var ef = Config.defaults();
   ef.filters = { R: "Mono R filter", G: "Mono G filter", B: "Mono B filter" };
   check( "effectiveFilters mono run", Pipeline.effectiveFilters( ef ).G, "Mono G filter" );
   ef.paths.OSC = "/m/a.xisf";
   ef.oscSensor = { R: "Sony Color Sensor R", G: "Sony Color Sensor G", B: "Sony Color Sensor B" };
   check( "effectiveFilters OSC run uses the sensor curves",
          [ Pipeline.effectiveFilters( ef ).R, Pipeline.effectiveFilters( ef ).G, Pipeline.effectiveFilters( ef ).B ],
          [ "Sony Color Sensor R", "Sony Color Sensor G", "Sony Color Sensor B" ] );
   // The runners receive them: stub Steps.spfc / Steps.spccRGB and record their arguments.
   var seen = [], keepSpfc = Steps.spfc, keepSpcc = Steps.spccRGB;
   Steps.spfc = function( view, f, ch, instrume, filters ) { seen.push( [ "spfc", ch, instrume, filters.G ] ); };
   Steps.spccRGB = function( view, instrume, filters ) { seen.push( [ "spcc", instrume, filters.G ] ); };
   var keepWb = Steps.applyWhiteBalance;
   Steps.applyWhiteBalance = function( view, factors ) { seen.push( [ "wb", factors ] ); };
   try { Pipeline.rgbCalibrationRunners( ef, null, null ).spfcRGB( { view: {} } );
         Pipeline.rgbCalibrationRunners( ef, null, null ).spccRGB( { view: {} } );
         Pipeline.rgbCalibrationRunners( ef, null, [ 1, 2, 3 ] ).spccRGB( { view: {} } ); }
   finally { Steps.spfc = keepSpfc; Steps.spccRGB = keepSpcc; Steps.applyWhiteBalance = keepWb; }
   check( "composite runners: sensor curves, no camera; clean factors applied instead of SPCC",
          seen, [ [ "spfc", "RGB", null, "Sony Color Sensor G" ], [ "spcc", null, "Sony Color Sensor G" ],
                  [ "wb", [ 1, 2, 3 ] ] ] );
   // Mono runs keep their filters and still apply clean factors.
   var mono = Config.defaults(); mono.filters = { R: "Mono R filter", G: "Mono G filter", B: "Mono B filter" };
   seen = [];
   Steps.spfc = function( view, f, ch, instrume, filters ) { seen.push( [ "spfc", filters.G ] ); };
   Steps.spccRGB = function( view, instrume, filters ) { seen.push( [ "spcc", filters.G ] ); };
   Steps.applyWhiteBalance = function( view, factors ) { seen.push( [ "wb", factors ] ); };
   try { Pipeline.rgbCalibrationRunners( mono, "ZWO ASI2600MM", null ).spccRGB( { view: {} } );
         Pipeline.rgbCalibrationRunners( mono, "ZWO ASI2600MM", [ 4, 5, 6 ] ).spccRGB( { view: {} } ); }
   finally { Steps.spfc = keepSpfc; Steps.spccRGB = keepSpcc; Steps.applyWhiteBalance = keepWb; }
   check( "mono runners unchanged", seen, [ [ "spcc", "Mono G filter" ], [ "wb", [ 4, 5, 6 ] ] ] );
   var nb = Config.defaults(); nb.paths.OSCNB = "/m/n.xisf"; nb.oscKinds = { OSCNB: "dualband" };
   check( "narrowband bandwidths from the filter table",
          Pipeline.oscNarrowbandBandwidths( nb, { filter: "L-eXtreme" } ), { R: 7, G: 7, B: 7 } );
   check( "narrowband bandwidths fall back to the run's bandwidth",
          Pipeline.oscNarrowbandBandwidths( nb, { filter: "Duo" } ).R, Config.defaults().narrowbandBandwidth );
```

Use the real name of the narrowband bandwidth field in `Config.defaults()` (search `bandwidth` in `Config.js`). The HOO mapping is R = Ha, G = OIII, B = OIII, so `{ R: haNm, G: oiiiNm, B: oiiiNm }`.

- [ ] **Step 2: Run** `LOOM_TEST_ONLY=osc.calibration node ci/run-tests.js` → FAIL.
- [ ] **Step 3: Implement.**
  - `Pipeline.broadbandStages( bk, bc, config )`: when `bc.group == "OSC"` the `spfc` parameters are `Pipeline.oscSpfcParams( bk, config )`; `Pipeline.broadbandRunners` passes that filter name and Ideal QE to `Steps.spfc` (check how `instrume` resolves to a QE curve in `Steps.setDeviceQE`, line 1029, and pass a value that resolves to the Ideal QE curve).
  - `Pipeline.compositeInstrument`: return `null` when the reference channel's `group` is `"OSC"`.
  - `Pipeline.effectiveFilters` replaces every read of `config.filters` on the composite and the clean reference: the clean-reference `Steps.spfc` / `Steps.spccRGB` calls (Pipeline.js 1232-1233), the `spfcRGB` cache parameters (1617) and the composite runners (1641-1645). Extract only the two calibration runners of `Pipeline.buildRGB` (`spfcRGB`, `spccRGB`, lines 1639-1649) into `Pipeline.rgbCalibrationRunners( config, lumInstrume, cleanFactors ) -> { spfcRGB, spccRGB }`; `spccRGB` applies `cleanFactors` with `Steps.applyWhiteBalance` when given and never calls `Steps.spccRGB` then, as today. `Pipeline.buildRGB` keeps `combine` and `solveRGB` (they need `chans`, `rgbId`, `reg`) and merges the two extracted runners into its runner object.
  - Palette calibration keys: the `paletteSpcc` stage parameters (Pipeline.js 1496) become `{ palette: pal, bandwidth: <the { R, G, B } bandwidths actually passed> }`, so changing a filter's bandwidth source re-runs palette calibration; add a test that the key differs between `L-eXtreme` and `L-Ultimate` for HOO from `OSCNB`.
  - `Pipeline.inheritInstrument`: return unchanged when any channel has a `group`.
  - `Pipeline.oscSensorProblems` runs in `Pipeline.preflight` (replace the Task 4 stub).
  - `Steps.spccNarrowband`: accept `{ R, G, B }`; set `redFilterBandwidth` etc. per channel; log the three bandwidths and, when called for an `OSCNB` palette, "approximation: H and O from a colour master are not single lines".
  - `Pipeline.buildPalette`: for a palette built from `OSCNB` channels pass `Pipeline.oscNarrowbandBandwidths( config, info )`.
  - When `OSC` is used with a light-pollution filter (`Util.oscFilterInfo( filter ).name` normalised in `["lpro", "cls", "clsccd"]`), log once: "colour calibration does not model the <name> filter in front of the camera".
- [ ] **Step 4: Run** the group and `steps,pipeline` → PASS.
- [ ] **Step 5: Commit**: "OSC: sensor colour curves and Ideal QE for calibration; per-channel narrowband bandwidths".

### Task 8: Registration plan and grouped registration

**Files:**
- Modify: `script/lib/Pipeline.js` `Pipeline.registrationReference` (2056), `Pipeline.registerToReference` (2129); add `Pipeline.oscRegistrationPlan`
- Modify: `script/lib/Steps.js` (add `Steps.combinePlanes( views, newId ) -> ImageWindow`)
- Test: `script/selftest.js` (new group `osc.register`)

**Interfaces:**
- Consumes: Task 5 keys, Task 6 channels (`group`, `plane`).
- Produces: `Pipeline.oscRegistrationPlan( present, groups ) -> { ref: <channel key>, identity: [ keys ], groups: [ { slot, members: [ keys in plane order ], planes: [ keys to combine, 3 entries ] } ], mono: [ keys ] }` where `groups` is `{ OSC: ["R","G","B"], OSCNB: ["H","O"] }` restricted to what is present.

- [ ] **Step 1: Failing tests.**

```javascript
   } if ( testGroup( "osc.register" ) ) {
   var both = Pipeline.oscRegistrationPlan( [ "R", "G", "B", "H", "O" ],
                                            { OSC: [ "R", "G", "B" ], OSCNB: [ "H", "O" ] } );
   check( "reference is OSC G", both.ref, "G" );
   check( "reference group is identity", both.identity, [ "R", "G", "B" ] );
   check( "OSCNB registered as one image (H, O, O)",
          both.groups, [ { slot: "OSCNB", members: [ "H", "O" ], planes: [ "H", "O", "O" ] } ] );
   var nbOnly = Pipeline.oscRegistrationPlan( [ "H", "O" ], { OSCNB: [ "H", "O" ] } );
   check( "OSCNB alone: reference H", nbOnly.ref, "H" );
   check( "OSCNB alone: no grouped registration", nbOnly.groups, [] );
   check( "registrationReference with colour groups ignores quality",
          Pipeline.registrationReference( [ "R", "G", "B", "H", "O" ], { H: { fwhm: 1 }, G: { fwhm: 9 } },
                                          { OSC: [ "R", "G", "B" ], OSCNB: [ "H", "O" ] } ).key, "G" );
```

Add a node test of the cache policy with a fake cache: `Pipeline.oscGroupCacheAction( groupCached, splitsCached, policy ) -> "register" | "split" | "load"` (`policy` = `{ useCache, ignoreCache }`, taken from the config) returns `"register"` for `( false, [ true, true, true ] )`, `"split"` for `( true, [ true, false, true ] )` and `"load"` for `( true, [ true, true, true ] )`; the test passes `{ useCache: true, ignoreCache: false }` for those three; with `policy` = `{ useCache: false }` or `{ useCache: true, ignoreCache: true }` it returns `"register"` whatever is cached. Production calls, the oracle (Task 13) and the Python twin use this three-argument form. `Pipeline.registerToReference` follows it.

Reference choice happens before any quality measurement: `Pipeline.registerToReference` builds the colour groups first and calls `Pipeline.registrationQuality( chans )` only for a run without colour groups. Test with a `Pipeline.registrationQuality` replaced by a function that throws: a colour run picks `G` without calling it, a mono run still calls it.

- [ ] **Step 2: Run** `LOOM_TEST_ONLY=osc.register node ci/run-tests.js` → FAIL.
- [ ] **Step 3: Implement.**
  - `Pipeline.oscGroupCacheAction` as tested above.
  - `Pipeline.oscRegistrationPlan` as specified (spec "Registration").
  - `Pipeline.registrationReference( present, quality, groups )`: when `groups` has any member, return `{ key: plan.ref, reason: "colour group reference" }` without consulting `quality`; mono behaviour unchanged when `groups` is absent.
  - `Pipeline.registerToReference`: build `groups` from the loaded channels' `group` fields. Identity members keep their views. For each grouped entry: key = `Pipeline.oscGroupRegisterKey( "registerGroup", member current keys in plane order, { ref: refKey, planes: planes.join( "," ) } )`. All grouped-registration cache reads and writes go through the same policy as Task 6 (`config.useCache`, `config.ignoreCache`): with the cache off the group is registered and split in memory; with it ignored nothing is reused but results are stored. The group image is authoritative: if the group image `key` is not in the cache, re-register (even when some splits are cached); if it is cached, load each member from its split `Pipeline.oscRegisteredSiblingKey( key, i )` when present, else split it from the group image (`Steps.extractPlane`) and store that split. Re-registering is `Steps.combinePlanes`, `Steps.register( combined, refView )` once, store the group image under `key`, split into the members, store each split, release the temporary windows from the run's registry. Members' `currentKey` become their registered split keys.
  - `Steps.combinePlanes( views, newId )`: a three-channel float window whose channel i is `views[i]`'s image (same size required, else throw).
- [ ] **Step 4: Run** the group and `pipeline` → PASS.
- [ ] **Step 5: In PixInsight** (hand check, logged): an `IN_PIXINSIGHT`-guarded check registers a synthetic `OSCNB` pair shifted by (3, 2) px against a synthetic `OSC` `G` and asserts the registered `H` and `O` are pixel-aligned with each other (star centroids within 0.05 px) and with `G` (within 0.2 px).
- [ ] **Step 6: Commit**: "OSC: siblings share one registration transform".

### Task 9: Clean white balance for a colour group

The clean white balance measures `R`, `G` and `B`, which in a colour run come only
from `OSC`. By the spec's reference policy the reference is `OSC`'s `G` whenever
`OSC` is used, so the `OSC` group is always the reference group there and its
clean planes need no registration: the spec's "else one grouped registration"
case cannot arise. This task implements the identity case and makes the
impossible case an explicit internal error rather than untested code.

**Files:**
- Modify: `script/lib/Pipeline.js` `Pipeline.measureCleanWhiteBalance` (1207), `Pipeline.registerCleanChannel` (1155), `Pipeline.cleanWhiteBalance` (2390)
- Test: `script/selftest.js` (group `osc.register`, extended)

**Interfaces:**
- Consumes: Tasks 5 and 8.
- Produces: `Pipeline.cleanRegistrationPlan( refKey, groups ) -> "identity" | "mono"` (throws for a colour run whose reference is not in the `OSC` group).

- [ ] **Step 1: Failing tests** (append to `osc.register`):

```javascript
   check( "clean plan: OSC is the reference group",
          Pipeline.cleanRegistrationPlan( "G", { OSC: [ "R", "G", "B" ], OSCNB: [ "H", "O" ] } ), "identity" );
   check( "clean plan: mono run", Pipeline.cleanRegistrationPlan( "L", {} ), "mono" );
   var thrown = null;
   try { Pipeline.cleanRegistrationPlan( "H", { OSC: [ "R", "G", "B" ], OSCNB: [ "H", "O" ] } ); }
   catch ( e ) { thrown = e.message; }
   check( "clean plan: a colour run's reference is always OSC G",
          thrown, "internal: with OSC in the run the reference must be its G (got H)" );
   // The white-balance key follows the crop and the reference in the identity case.
   var cfgW = Config.defaults(); cfgW.paths.OSC = "/m/a.xisf";
   cfgW.oscSensor = { R: "S R", G: "S G", B: "S B" };
   function wbKey( common, refKey )
   {
      var chans = { R: { currentKey: "r1" }, G: { currentKey: "g1" }, B: { currentKey: "b1" } };
      return Pipeline.cleanWhiteBalanceKey( chans, cfgW, common, null, refKey );
   }
   var full = { x0: 0, y0: 0, x1: 100, y1: 80 }, smaller = { x0: 0, y0: 0, x1: 90, y1: 70 };
   check( "clean white balance key changes when the overlap shrinks", wbKey( full, "g1" ) != wbKey( smaller, "g1" ), true );
   check( "clean white balance key changes with a replaced OSC master (new reference key, same crop)",
          wbKey( full, "g1" ) != wbKey( full, "g2" ), true );
```

Match `chans` and `common` to the shapes `Pipeline.cleanWhiteBalanceKey` and `Pipeline.cropTag` read (Task 5 made `refKey` the last argument).

- [ ] **Step 2: Run** `LOOM_TEST_ONLY=osc.register node ci/run-tests.js` → FAIL.
- [ ] **Step 3: Implement.** In `measureCleanWhiteBalance`: `"mono"` keeps today's per-channel `registerCleanChannel`; `"identity"` crops the three clean `OSC` planes with `common` (the same crop call `Pipeline.cropToCommonArea` uses) and measures with `Pipeline.effectiveFilters` (Task 7). Pass the reference's cache key to `Pipeline.cleanWhiteBalanceKey`.
- [ ] **Step 4: Run** the group and `pipeline` → PASS.
- [ ] **Step 5: In PixInsight** (hand check, logged): the Task 8 synthetic pair, white-balance branch: no clean registration runs (log), the measured factors are those of the cropped `OSC` planes; an `OSCNB` covering 20 px less of the field shrinks the common crop and the white balance is measured again (log shows a MISS for the clean white balance); replacing the `OSC` file with a byte-different copy measures it again.
- [ ] **Step 6: Commit**: "OSC: clean white balance on the reference colour group".

### Task 10: Folder discovery of colour masters

**Files:**
- Modify: `script/lib/Util.js` (add `Util.selectColourMasters`, `Util.colourCandidateCorrection`, `Util.entryChannel`)
- Modify: `script/lib/UI.js` `UI.foldEntries` (385), `UI.fileEntry` (484), `addView` (2244), `addMastersFolder` (1966-2000), `scanMasterFolder` (2007), `resolveUnnamedMasters` (2024), `confirmMasters` (2046), `adoptMasters` (2178), `reportScan` (2196)
- Test: `script/selftest.js` (new group `osc.discovery`)

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: `Util.selectColourMasters( candidates ) -> { OSC: candidate|null, OSCNB: candidate|null }` where a candidate is `{ path, filter, colour, drizzle, autocrop, mtime, numberOfChannels, imagetyp }` and its slot is decided by `Util.effectiveOscKind` (unknown kind lands in `OSC` when broadband-looking names fail, else is listed with an empty kind: return it under `unresolved: [ candidates ]`); `Util.colourCandidateCorrection( candidate ) -> string | null` ("name says colour, header says 1 channel: <file> is used as mono", and the reverse); `Util.entryChannel( filter, numberOfChannels, path, choices ) -> { channel: <mono key> | "OSC" | "OSCNB" | null, colour: true|false, kindSource: "choice"|"detected"|"none"|null }`. Every masters-list entry (files and open views) carries `channel`, `colour` and `numberOfChannels`; `UI.foldEntries( entries, choices )` returns `{ paths, views, problems }` with `OSC` / `OSCNB` filled from colour entries.

- [ ] **Step 1: Failing tests.**

```javascript
   } if ( testGroup( "osc.discovery" ) ) {
   function cand( path, filter, extra )
   {
      var c = { path: path, filter: filter, colour: true, drizzle: "", autocrop: false, mtime: 1,
                numberOfChannels: 3, imagetyp: "Master Light" };
      for ( var k in ( extra || {} ) ) c[k] = extra[k];
      return c;
   }
   var picks = Util.selectColourMasters( [
      cand( "/m/a_uvir.xisf", "UV/IR Cut" ),
      cand( "/m/b_lext.xisf", "L-eXtreme" ),
      cand( "/m/b_lext_drizzle.xisf", "L-eXtreme", { drizzle: "2x" } ),
      cand( "/m/c_duo.xisf", "Duo" ) ], {} );
   check( "OSC pick", picks.OSC.path, "/m/a_uvir.xisf" );
   check( "OSCNB pick prefers drizzle", picks.OSCNB.path, "/m/b_lext_drizzle.xisf" );
   check( "unconfident candidate listed without a kind", picks.unresolved.map( function( c ) { return c.path; } ),
          [ "/m/c_duo.xisf" ] );
   check( "correction: colour name, mono header",
          Util.colourCandidateCorrection( cand( "/m/x_color.xisf", "UV/IR", { numberOfChannels: 1 } ) ),
          "/m/x_color.xisf: the name says colour but the image has 1 channel; it is used as a mono master" );
   check( "correction: none", Util.colourCandidateCorrection( cand( "/m/y_color.xisf", "UV/IR" ) ), null );
   // Entries route by channel count first, then by kind.
   check( "entryChannel mono", Util.entryChannel( "Ha", 1, "/m/h", {} ).channel, "H" );
   check( "entryChannel broadband colour", Util.entryChannel( "UV/IR Cut", 3, "/m/a", {} ).channel, "OSC" );
   check( "entryChannel dual-band colour", Util.entryChannel( "L-eXtreme", 3, "/m/b", {} ).channel, "OSCNB" );
   check( "entryChannel unconfident colour waits for a choice",
          [ Util.entryChannel( "Duo", 3, "/m/c", {} ).channel, Util.entryChannel( "Duo", 3, "/m/c", {} ).colour ],
          [ null, true ] );
   check( "entryChannel override moves a broadband-named master to OSCNB",
          Util.entryChannel( "UV/IR Cut", 3, "/m/a", { "filter:uvircut": "dualband" } ).channel, "OSCNB" );
   function ent( ref, filter, n, source )
   {
      var r = Util.entryChannel( filter, n, ref, {} );
      return { source: source || "file", ref: ref, label: ref, filter: filter, numberOfChannels: n,
               channel: r.channel, colour: r.colour };
   }
   var folded = UI.foldEntries( [ ent( "/m/a", "UV/IR Cut", 3 ), ent( "nbview", "L-eXtreme", 3, "view" ) ], {} );
   check( "foldEntries colour file and view", [ folded.paths.OSC, folded.views.OSCNB, folded.problems ],
          [ "/m/a", "nbview", [] ] );
   check( "foldEntries colour entry without a kind",
          UI.foldEntries( [ ent( "/m/c", "Duo", 3 ) ], {} ).problems,
          [ "choose Broadband or Dual-band Hα + OIII for /m/c" ] );
   check( "foldEntries keeps mono behaviour",
          UI.foldEntries( [ ent( "/m/h", "Ha", 1 ) ], {} ).paths.H, "/m/h" );
```

`Util.selectColourMasters( candidates, choices )` takes the remembered kind choices as its second argument.

- [ ] **Step 2: Run** → FAIL.
Folder discovery is orchestrated by `addMastersFolder` (UI.js 1966): today it picks only `scan.named` mono winners, falls back to unnamed files only when there is no mono winner at all (line 1992), and passes mono picks alone to `confirmMasters`. Its new flow, extracted into a pure `UI.planFolderPicks( scan, choices ) -> { mono: picks, colour: { OSC, OSCNB }, unresolved: [ ], readUnnamed: true|false }` so it can be tested under node:
1. mono picks from `Util.selectMasters( scan.named )`, colour picks from `Util.selectColourMasters( scan.colourNamed, choices )`;
2. `readUnnamed` is true when any mono channel slot or either colour slot has no named winner and `scan.unnamed` is not empty (the spec's missing-slot rule), not only when nothing was found;
3. `resolveUnnamedMasters` then fills only the missing slots, routing three-channel files to colour candidates;
4. `confirmMasters` receives mono picks and colour picks together; colour picks are confirmed by header (three channels, master light); unresolved colour candidates go to the masters list with an empty kind control;
5. `adoptMasters` adopts both, and a run with both kinds stays blocked by `Util.colourMixProblem`.
Add node tests for `UI.planFolderPicks`: a named mono winner plus an unnamed colour master (`readUnnamed` true, the colour master reaches the colour picks after resolution with a fake header reader); named mono and colour winners together (both returned, `readUnnamed` false when nothing is missing for the slots the folder has names for, mix problem raised on adoption).

- [ ] **Step 3: Implement** `Util.entryChannel` (three channels → `Util.effectiveOscKind`, `broadband` → `OSC`, `dualband` → `OSCNB`, `unsupported` or no kind → `null` with `colour: true`; one channel → `Util.channelFromFilter`). `UI.fileEntry` and `addView` set `numberOfChannels` (from `Util.readImageInfo` / `view.image.numberOfChannels`) and `channel`/`colour` from `Util.entryChannel( filter, n, ref, config.oscKindChoices )`. `UI.foldEntries( entries, choices )` initialises `paths`/`views` for `Util.CHANNELS.concat( Util.COLOUR_SLOTS )`, maps colour entries to `OSC`/`OSCNB` (two entries for one slot is the existing "Two images map to ..." problem), reports a colour entry with `channel === null` with the kind message (or the unsupported-filter message), and keeps today's text for unrecognised mono filters; its caller in the dialog (the Run handler near UI.js 2505) passes `this.config.oscKindChoices`. Then the two Util functions (variant ranking with `Util.masterVariantRank`, then newest `mtime`, as `Util.selectMasters` does). In `UI.js`: `scanMasterFolder` collects names with `Util.parseColourMasterName( name ).colour` into `scan.colourNamed`; `resolveUnnamedMasters` header-reads unnamed files when a mono slot or a colour slot has no named winner, routing three-channel files to colour candidates; `confirmMasters` confirms colour candidates by header (`numberOfChannels == 3`, `IMAGETYP` a master light via `Util.isMasterLight` or its equivalent) and reports `Util.colourCandidateCorrection`; `adoptMasters` sets `config.paths.OSC` / `OSCNB` and keeps mono winners too; when both kinds are adopted the masters list shows both and `updateRunEnabled` (1937) keeps Run disabled with `Util.colourMixProblem`'s text.
- [ ] **Step 4: Run** the group and `util,ui` → PASS.
- [ ] **Step 5: In PixInsight** (hand check, logged): a folder with a synthetic `OSC`, a synthetic `OSCNB` and a mono L master lists all three and Run stays disabled with the mix message.
- [ ] **Step 6: Commit**: "OSC: discover colour masters in a folder".

### Task 11: Dialog controls

**Files:**
- Modify: `script/lib/Util.js` (add `Util.oscControlState`)
- Modify: `script/lib/UI.js` `buildMasterList` (855), `showMaster` (2166), `buildFilterRows` (982), `buildPaletteGroup` (1119), `updatePaletteVisibility` (2391), `rebuild` (2417), `updateRunEnabled` (1937)
- Test: `script/selftest.js` (new group `osc.ui`)

**Interfaces:**
- Consumes: Tasks 1-4, 7, 10.
- Produces: `Util.oscControlState( config ) -> { monoEnabled, monoReason, shoHsoEnabled, shoHsoReason, sensorRowVisible, rgbFilterRowsVisible }` (pure, node-tested, and recorded for the Siril port); the dialog applies it. A kind change or a "detected" reset recomputes the entry's `channel` with `Util.entryChannel` (Task 10) and calls `rebuild()`, so a master moves between `OSC` and `OSCNB` at once; `updatePaletteVisibility` reads `Util.suppliedChannels` of the folded entries.

- [ ] **Step 1: Failing tests.**

```javascript
   } if ( testGroup( "osc.ui" ) ) {
   var c = Config.defaults(); c.paths.OSC = "/m/a.xisf";
   var s = Util.oscControlState( c );
   check( "mono boxes greyed with reason", [ s.monoEnabled, s.monoReason ],
          [ false, "colour and mono masters cannot be combined" ] );
   check( "SHO/HSO greyed with reason", [ s.shoHsoEnabled, s.shoHsoReason ],
          [ false, "needs SII, which a colour camera cannot separate from Ha" ] );
   check( "sensor row instead of R/G/B filter boxes", [ s.sensorRowVisible, s.rgbFilterRowsVisible ], [ true, false ] );
   var m = Config.defaults(); m.paths.L = "/m/l.xisf";
   var t = Util.oscControlState( m );
   check( "mono run unchanged", [ t.monoEnabled, t.shoHsoEnabled, t.sensorRowVisible ], [ true, true, false ] );
   var nb = Config.defaults(); nb.paths.OSCNB = "/m/n.xisf";
   check( "OSCNB alone: no sensor row", Util.oscControlState( nb ).sensorRowVisible, false );
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `Util.oscControlState` and apply it:
  - masters list rows for `OSC` / `OSCNB`: file name, detected filter, a kind `ComboBox` ("", "Broadband", "Dual-band Hα + OIII") preset from `Util.effectiveOscKind`, disabled for `unsupported` kinds, writing `config.oscKindChoices[ Util.oscKindKey( filter, path ) ]` on change; a "detected" `Label` (link style) that deletes that key and refreshes;
  - mono channel boxes disabled with the reason as tooltip while a colour master is selected;
  - SHO and HSO check boxes unticked and disabled with the reason; a remembered SHO/HSO choice is not applied to a colour run (it stays in the saved settings for mono runs);
  - SPCC section: while `OSC` is used, hide the R, G, B filter rows and show a "Sensor colour filters" row of three `ComboBox`es filled from `Steps.listFilterCurves( "R" | "G" | "B" )` (Steps.js 530), writing `config.oscSensor`;
  - every new value saved through `Config.save` like the existing fields.
- [ ] **Step 4: Run** the group and `util,ui` → PASS.
- [ ] **Step 5: In PixInsight** (hand check, logged with a screenshot in the PR description): the dialog with a synthetic `OSC` master shows the kind control, the sensor row, greyed mono and SHO/HSO boxes with their tooltips; values survive closing and reopening the dialog.
- [ ] **Step 6: Commit**: "OSC: dialog controls for colour masters".

### Task 12: Provenance, log lines and end-to-end checks

**Files:**
- Modify: `script/lib/Pipeline.js` `Pipeline.publishChannels` (2583), `Pipeline.buildPalette` (1453), `Pipeline.exportResults` (672), `Pipeline.run` (2762)
- Test: `script/selftest.js` (new groups `osc.log` (node) and `osc.e2e` (PixInsight only))

**Interfaces:**
- Produces: `Pipeline.oscPaletteSources( config ) -> string` (log line); `LOOMSRC` keyword on every exported channel and palette layer from a colour group.

- [ ] **Step 1: Failing node tests.**

```javascript
   } if ( testGroup( "osc.log" ) ) {
   var c = Config.defaults(); c.paths.OSCNB = "/m/n.xisf"; c.oscKinds = { OSCNB: "dualband" };
   check( "palette sources line, quad-band note",
          Pipeline.oscPaletteSources( c, { OSCNB: { name: "L-eXtreme" } } ),
          "HOO: H from OSCNB L-eXtreme (Ha, plus SII for quad-band), O from OSCNB L-eXtreme (OIII with Hβ)" );
```

- [ ] **Step 2: Run** → FAIL; **Step 3: Implement** `Pipeline.oscPaletteSources` and write `LOOMSRC` on exports (`Pipeline.exportResults`) and PSB palette layers (where `Pipeline.buildPalette` names its layers) when the source channel has a `group`.
- [ ] **Step 4: PixInsight end-to-end checks** (`osc.e2e`, guarded by `IN_PIXINSIGHT`; skipped under node). Build synthetic masters in a temporary folder under the cache directory: an `OSC` (three planes with stars, a sky gradient and continuum) and an `OSCNB` shifted by (3, 2) px with independent Hβ and OIII structure in green and blue. Count master opens by wrapping `ImageWindow.open` for the duration of the group. Checks:
  - cold run: each master opened once; siblings pixel-aligned in the corrected and the clean branch;
  - warm run with only the sensor changed: no master opened; white balance re-measured (cache MISS in the log); composite SPCC run with the sensor curves and Ideal QE (log line);
  - split planes removed from the cache: one open;
  - `OSCNB` alone with narrowband gradient removal off: one open cold, none warm;
  - `O` equals the green plane exactly, whatever the blue plane holds;
  - a constant plane is refused with the Task 6 message;
  - one sibling's input changed (re-made `OSCNB` file): the group re-registers; one sibling's registered split deleted: re-split without re-registering.
- [ ] **Step 5: Run** `LOOM_TEST_ONLY=osc,pipeline node ci/run-tests.js` → PASS. Run `osc.e2e` in PixInsight (hand check; paste the result line in the PR description).
- [ ] **Step 6: Commit**: "OSC: provenance keywords, log lines, end-to-end checks".

---

## Phase B: Siril port

All commands in Phase B run from `siril/`. Unit tests:
`PYTHONPATH=$PWD python -m pytest -q tests/loom/<file>`. Siril-marked tests run
in the Docker image built from `docker/Dockerfile`; CI runs them in the
`docker` job of `.github/workflows/siril.yml`.

### Task 13: Record the JavaScript oracle for the OSC pure functions

**Files:**
- Create: `tools/oracle/loom_osc.js`, `tests/oracle/loom_osc.json`

The recorder's name must start with `loom_`: `tools/record_oracle.js` loads the Loom libraries (Util, Hasher, Cache, Psb, Steps, Config, Pipeline) only for `loom_*` recorders (line 64-67) and exposes `Util`, `Cache`, `Config`, `Pipeline` in `js`. Every function recorded here lives in those namespaces (the dialog state is `Util.oscControlState`, not in UI.js), so `record_oracle.js` needs no change.

**Interfaces:**
- Produces: `tests/oracle/loom_osc.json` with cases for `Util.entryChannel`, `Pipeline.inputChannelProblems`, `Pipeline.effectiveFilters`, `Pipeline.oscGroupCacheAction`, `Util.selectColourMasters`, `Util.colourCandidateCorrection`, `Util.normaliseFilter`, `Util.oscFilterInfo`, `Util.parseColourMasterName`, `Util.channelCountProblem`, `Util.suppliedChannels`, `Util.colourMixProblem`, `Util.oscKindKey`, `Util.effectiveOscKind`, `Pipeline.oscPaletteProblem`, `Pipeline.oscKindProblems` (with a table-driven `infoOf`), `Pipeline.oscGroupIdentity`, `Pipeline.oscSiblingKey`, `Pipeline.oscValidateName`, `Pipeline.oscGroupRegisterKey`, `Pipeline.oscRegisteredSiblingKey`, `Pipeline.cleanWhiteBalanceKey` (OSC and mono), `Pipeline.oscRegistrationPlan`, `Pipeline.cleanRegistrationPlan`, `Pipeline.oscNarrowbandBandwidths`, `Pipeline.oscPaletteSources`, `Util.oscControlState`.

- [ ] **Step 1:** Read `tools/record_oracle.js` and one existing recorder under `tools/oracle/`; copy its structure (`module.exports = function( js ) { return { "<case>": { fn, args, out } } }`, clone args before each call).
- [ ] **Step 2:** Write `tools/oracle/loom_osc.js` with at least the inputs used in the Phase A tests plus: every entry of `Util.OSC_FILTERS`, five spellings each of two models, ten random mixed-case names, empty/absent values, all palette combinations with and without colour slots, and the key functions over ten fingerprints.
- [ ] **Step 3:** Record from `siril/`: `LOOM_JS_DIR=../pixinsight/script node tools/record_oracle.js tests/oracle/ loom_osc`; check that the JSON holds every function listed above (a missing namespace fails the recorder). Commit both files: "Siril: record the OSC oracle from the JavaScript".

### Task 14: `osc.py` pure functions

**Files:**
- Create: `loomsiril/loom/osc.py`, `tests/loom/test_osc.py`

**Interfaces:**
- Consumes: Task 13 oracle.
- Produces: `clean_white_balance_key` stays in `cache.py` (its twin, extended in Task 17); `colour_run` (true when `OSC` or `OSCNB` is supplied; Siril only, used by Task 17), `entry_channel`, `input_channel_problems`, `effective_filters`, `group_cache_action`, `select_colour_masters`, `colour_candidate_correction`, `normalise_filter`, `OSC_FILTERS`, `osc_filter_info`, `parse_colour_master_name`, `channel_count_problem`, `COLOUR_SLOTS`, `COLOUR_MEMBERS`, `supplied_channels`, `colour_mix_problem`, `osc_kind_key`, `effective_osc_kind`, `osc_palette_problem`, `osc_kind_problems`, `group_identity`, `sibling_key`, `validate_name`, `group_register_key`, `registered_sibling_key`, `registration_plan`, `clean_registration_plan`, `narrowband_bandwidths`, `palette_sources`, `control_state`: one Python function per JS function, snake_case of the JS name, same positional arguments and return shapes (dicts for JS objects). Configuration objects are passed in the JavaScript shape (`paths`, `views`, `oscKinds`, `oscSensor`, `oscKindChoices`, `filters`, `palettes`); the test converts the port's configuration to that shape with `osc.js_config( config )`, defined here, which maps the Python keys of Task 15 (`osc_kinds`, `osc_sensor`, `osc_kind_choices`) to the JavaScript names. The recorded `Pipeline.cleanWhiteBalanceKey` cases are replayed in Task 17, where the Python key gains the same inputs; Task 14 marks them deferred to Task 17 in its replay table (a test asserts the deferred list is exactly that one function). Three differences are explicit and tested: the port's `osc_sensor` is `{"sensor", "front_filter", "lpf"}` (Siril's SPCC takes one OSC sensor name), so `js_config` maps `sensor` to all three of R, G, B for the functions that only compare names, and `effective_filters` has no Siril twin (Siril has no per-channel SPFC; Task 17 builds the calibration identity instead) — its oracle cases are skipped with that reason in the replay; and the Siril clean white-balance key hashes the shared JavaScript inputs (clean keys, crop, reference key, filter or sensor names) *plus* the Siril calibration identity of Task 17, so the replay compares the shared part: `cache.clean_white_balance_key( ..., calibration=None )` must equal the JavaScript key, and a non-None `calibration` must change it.

- [ ] **Step 1: Failing test.** `tests/loom/test_osc.py` replays every case of `tests/oracle/loom_osc.json` (copy the replay and compare helpers of an existing oracle test such as `tests/test_frames.py`: floats to 1e-9 relative, NaN equal, the rest exact) and adds a guard that every recorded function has a Python twin.
- [ ] **Step 2: Run** `pytest -q tests/loom/test_osc.py` → FAIL (module missing).
- [ ] **Step 3: Implement** `osc.py`. Hashes use the port's existing `hash_str` / chain-key helpers in `loomsiril/loom/cache.py` (`clean_white_balance_key`, line 205, shows the pattern) so Python keys equal JavaScript keys.
- [ ] **Step 4: Run** → PASS. **Step 5: Commit**: "Siril: OSC detection, selection rules and keys ported".

### Task 15: Configuration, preflight and channel consumers

**Files:**
- Modify: `loomsiril/loom/config.py` (`defaults` line 108, `_apply_document` 275, `_document` 364), `loomsiril/loom/preflight.py` (`preflight` 156), `loomsiril/loom/channels.py` (`_read_fits_header` 627-648 and `read_header` 652-675 gain `channels`, from `NAXIS3` (absent or `NAXIS` < 3 gives 1) for FITS, compressed FITS included (`ZNAXIS3` in the tile-compressed extension), and from the third component of the XISF `Image` geometry attribute (`width:height:channels`); `validate_selection` 119, `duplicate_file_problems` 99, `narrowband_supplied` 554, `wanted_palettes` 561, `report_skipped_palettes` 572, `inherit_instrument` 588, `scan_masters` 686)
- Test: `tests/loom/test_config.py`, `tests/loom/test_preflight.py`, `tests/loom/test_channels.py` (new tests in each)

**Interfaces:**
- Consumes: Task 14.
- Produces: config keys `paths["OSC"]`, `paths["OSCNB"]`, `osc_kinds`, `osc_sensor` (`{"sensor", "front_filter", "lpf"}` Siril names, see Task 17), `osc_kind_choices`; preflight errors identical to the JavaScript texts; `scan_masters` returns colour candidates and corrections.

- [ ] **Step 1: Failing tests:** header-only reads give `channels` 1 and 3 for FITS, `.fz` and XISF files written in `tmp_path` (and 2 for an unsupported FITS, which preflight refuses with the JavaScript text), with pixel decoding forbidden (monkeypatch the pixel readers to raise); `channels` feeds `numberOfChannels` in the JS-shaped info `osc.input_channel_problems` and discovery receive; the mix error, the SHO/HSO error, an unconfident kind, an unsupported filter, a mono file in a colour slot, `OSCNB` alone giving HOO, `osc_sensor` and `osc_kind_choices` round-tripping through `save`/`load`, `scan_masters` on a temporary folder with a synthetic three-channel FITS named `..._FILTER-L-eXtreme_color.fit` and a mono `..._FILTER-L_mono.fit` (both listed; selection blocked), and a name/header disagreement reported.
- [ ] **Step 2: Run** the three files → FAIL. **Step 3: Implement** by calling `osc.py`. Existing assertions pin the default configuration and the option list: update `tests/loom/test_config.py` (defaults, around line 40) and `tests/acceptance/test_loom_pr3_options.py` (option list, around line 75) for the new keys, adding the new keys to the expected values rather than relaxing the checks. **Step 4: Run** those files plus the three above → PASS. **Step 5: Commit**: "Siril: OSC configuration, preflight and discovery".

### Task 16: Loading and splitting a colour master

**Files:**
- Modify: `loomsiril/loom/pipeline.py` (`_Run.load_channels`, line 190; `_Chan`, line 140; the run log lines); `loomsiril/loom/stages_colour.py` (`_combine`, line 138: `LOOMSRC` on combined products only) and `loomsiril/loom/publish.py` / `psb.py` (palette layer keywords, where the port writes layer metadata)
- Test: `tests/loom/test_pipeline.py`, `tests/loom/test_stages_colour.py` (new tests)

**Interfaces:**
- Produces: `_Chan` gains `group` and `plane`. For a colour member, `path` stays the physical master's path (for logs and provenance) and a new `source` callable returns the member's one-channel plate path from the shared group loader `ColourGroupLoader` (one per colour master per run). Both source paths go through it: `_Run.plate_of` (line 167) and `_Run._ingest_source` (line 229) call `ch.source()` for a colour member instead of ingesting `ch.path`; mono members keep today's code. Colour members are loaded from FITS plates written into the cache under their sibling keys; the validation sidecar (same name as the JS) holds the plane statistics; the master is read at most once per run, not at all when every sibling plate is cached.

Provenance survives to the products: the port's channel combination (`stages_colour._combine`, line 138, through `imageops` around line 324) drops keywords that differ between inputs, so the composite and palette products get their `LOOMSRC` set explicitly after combining (one value per product naming each source plane, e.g. `OSC <file> planes 0,1,2`), and the PSB palette layers get it like the JavaScript (Task 12). The run log carries, on cold and warm runs alike, one line per colour group with kind, filter, split and the plane statistics read from the validation sidecar, the palette-sources line and the narrowband approximation line.

- [ ] **Step 1: Failing tests** through the pipeline paths, with the recording ops: `correct_broadband` for `R` and `G`, an enabled `correct_narrowband` for `H` and the no-narrowband-stage path for `O` each receive a one-channel plate; a cold run reads (or converts) the master once in total; a warm run reads it zero times. Then the unit tests (synthetic three-channel FITS from `tests/loom/synth.py` written with the port's FITS writer): split planes equal the source planes (`O` equals plane 1 exactly, independent of plane 2); a constant plane is refused with the JS text; a second run reads the master zero times (count opens by monkeypatching the reader); deleting one plate reads it once; `FILTER` and `LOOMSRC` are set on each plate; the composite and an HOO palette product carry `LOOMSRC`; the log lines appear on a cold run and again on a warm run (statistics from the sidecar, master not read).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** An uncompressed FITS master (`.fit`, `.fits`, `.fts`) is read with the port's FITS reader. A compressed FITS master (`.fz`) or an XISF master (what WBPP writes by default) is first converted once per group with the existing ingestion route (`stages_correct.ingest_channel`, which loads the file in Siril and saves `<work>/<name>_src.fits`, line 89), under the group identity, and the converted FITS is then split with numpy; the conversion happens only when some sibling plate is missing from the cache, so a warm run does not touch Siril for it. Add `siril`-marked tests with a synthetic `.fz` colour master and a synthetic XISF colour master (write it with the port's XISF test helper if there is one, else with Siril from a FITS in the Docker image): cold run converts and splits once, warm run converts nothing, plates equal the source planes. **Step 4: Run** → PASS. **Step 5: Commit**: "Siril: colour masters split into cached channel plates".

### Task 17: SPCC for OSC and the calibration identity

**Files:**
- Modify: `loomsiril/loom/siril_ops.py` (`_spcc_checks` 190, `_text_spcc_broadband` 196, `_text_spcc_narrowband` 210, the `spcc_broadband` methods at 412, 618, 842); `loomsiril/loom/stages_colour.py` (`rgb_stage_params` 61, `palette_stage_params` 76, `rgb_spcc_names` 96, `_spcc_rgb_runner` 155-165, `_palette_spcc` 228-254, `_measure_clean` 315-333); `loomsiril/loom/cache.py` (`clean_white_balance_key` 205); `tests/loom/test_cache.py` (the pinned key around line 156)
- Test: `tests/loom/test_siril_ops.py`, `tests/loom/test_siril_ops_spcc_session.py`, `tests/loom/test_stages_colour.py`, `tests/loom/test_cache.py`

**Interfaces:**
- Produces: `_text_spcc_osc( sensor, front_filter, lpf ) -> str` emitting `spcc -oscsensor=<sensor> -oscfilter=<front> -osclpf=<lpf>` (all three always present; Siril's no-filter entry when none is chosen; read Siril 1.4 `spcc` help in the Docker image for the exact names, and record them in the module docstring); `spcc_osc(...)` methods on every ops class; a calibration identity `calibration_identity( config ) -> str` (mode plus every effective spectral argument) added to `rgb_stage_params` and to `clean_white_balance_key`; narrowband per-channel bandwidths from `osc.narrowband_bandwidths`.

**Narrowband in a colour run.** Siril's narrowband SPCC takes the sensor arguments as well as the six wavelength/bandwidth values, and keeps omitted ones from the previous call, so the narrowband builder is extended rather than replaced: `_text_spcc_narrowband( mode_args, rwl, rbw, gwl, gbw, bwl, bbw, ... )` where `mode_args` is the complete OSC set (`-oscsensor= -oscfilter= -osclpf=`) in a colour run and the complete mono set otherwise. In Siril the sensor choice is therefore needed for any colour run, `OSCNB` alone included: the Siril dialog shows the sensor row whenever a colour master is selected and preflight requires it (a documented Siril deviation; the PixInsight dialog shows it only for `OSC`, because PixInsight's narrowband SPCC uses no sensor curves). `palette_stage_params` (line 76) hashes the palette, the per-channel bandwidths actually sent and the calibration identity, so HOO is re-calibrated when any spectral choice changes.

**Clean white balance in a colour run.** `_measure_clean` (line 315) today registers the clean R, G and B against the reference (line 327). In a colour run the reference is `OSC`'s `G` (Task 18), so the clean `OSC` planes are on the reference grid already: they skip registration entirely and are cropped once with the common rectangle; mono runs keep the existing path.

Every SPCC call site dispatches on one predicate, `osc.colour_run( config )` (true when `OSC` or `OSCNB` is supplied): the composite runner (`_spcc_rgb_runner`, line 165) and the clean white balance (`_measure_clean`, line 333) call the broadband OSC method `spcc_osc( sensor, front_filter, lpf )` in a colour run (they only run when `OSC` supplies R, G and B) and the existing mono broadband method otherwise; the palettes (`_palette_spcc`, line 254, `OSCNB`-only runs included) always call the extended narrowband method `_text_spcc_narrowband( mode_args, rwl, rbw, gwl, gbw, bwl, bbw, ... )`, with the complete OSC `mode_args` in a colour run and the complete mono `mode_args` otherwise. `calibration_identity` is computed from the same effective arguments those calls send, and `clean_white_balance_key` also hashes the reference's cache key (as Task 5 does in the JavaScript), threaded from `_Run.clean_white_balance`.

- [ ] **Step 1: Failing tests:** complete command text for an HOO palette in a mono run, an `OSC` + `OSCNB` run and an `OSCNB`-only run; the HOO stage key changes with the sensor, the front filter, the low-pass filter and the bandwidth source; in a colour run `_measure_clean` sends zero registration commands and its planes equal the cropped source planes, while a mono run still registers; the exact OSC command text; each of the three call sites sends the OSC set for an `OSC` run and the mono set for a mono run (record the commands with the recording ops class); an `OSCNB`-only run sends complete narrowband arguments; a mono call after an OSC call in one session sends the full mono argument set (and the reverse), extending `test_siril_ops_spcc_session.py`; `rgb_stage_params` and the clean white-balance key change when only the sensor, only the front filter or only the low-pass filter changes; narrowband SPCC gets `-rbw/-gbw/-bbw` from the filter table.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** The clean white-balance key's pinned value in `tests/loom/test_cache.py` changes because the reference key is now hashed: re-record it from the JavaScript (`Pipeline.cleanWhiteBalanceKey`) through the Task 13 recorder rather than editing the number by hand. **Step 4: Run** → PASS. **Step 5: Commit**: "Siril: SPCC with an OSC sensor and the calibration identity in the keys".

### Task 18: Grouped registration in Siril

**Files:**
- Modify: `loomsiril/loom/siril_ops.py` (`_text_register` 230 and the three `register` methods at 439, 645, 859 gain an optional `layer: Optional[int] = None`; `None` keeps today's command text exactly; the recording/stub ops record it); `loomsiril/loom/stages_geometry.py` (`register_channels` 63, `register_stage_params` 50); `loomsiril/loom/pipeline.py` (`_Run.register` 262)
- Test: `tests/loom/test_stages_geometry.py` (stub tests) and `tests/loom/integration/test_osc_siril.py` (new, `siril`-marked)

**Interfaces:**
- Consumes: `osc.registration_plan`, `osc.group_register_key`, `osc.clean_registration_plan`.
- Produces: (the clean white balance's identity branch is Task 17's) a registration step that, for a grouped colour group, expands the reference plate to three identical channels (`rgbcomp`), builds a two-image colour sequence (reference, group image), runs `register` with `-layer=1` for `OSC` or `-layer=0` for `OSCNB`, and splits the registered group image back into its member plates; the reference group's members are not registered.

- [ ] **Step 1: Failing stub tests:** `_Run.register` decides the colour-group reference before calling `_registration_quality` (line 252-264): a colour run with a quality function that raises still registers, a mono run still measures; `_text_register( name, layer=None )` equals today's text and `layer=1` appends `-layer=1`; the command sequence for an `OSCNB` group against an `OSC` reference (`rgbcomp`, sequence link, `register ... -layer=0`, split), the cache keys used, the identity case sending no registration command.
- [ ] **Step 2: Failing `siril`-marked tests** (`tests/loom/integration/test_osc_siril.py`): synthetic `OSC` and `OSCNB` masters with a (3, 2) px shift: registered `H` and `O` aligned to the reference `G` within 0.2 px and to each other within 0.05 px; the clean white-balance branch likewise.
- [ ] **Step 3: Implement.** **Step 4: Run** the stub tests locally and the `siril` tests in the Docker image → PASS. **Step 5: Commit**: "Siril: a colour group is registered as one image".

### Task 19: Siril dialog

**Files:**
- Modify: `loomsiril/loom/dialog.py`
- Test: `tests/loom/test_dialog.py` (new tests)

**Interfaces:**
- Consumes: `osc.control_state`, Task 15 config keys. In Siril the sensor row is visible for any colour run (Task 17): the dialog uses `osc.control_state( config )` with `sensorRowVisible` overridden to true whenever a colour master is selected, and a test pins that difference.
- Produces: masters list rows for `OSC` / `OSCNB` with a kind control and a "detected" reset; greyed mono boxes and SHO/HSO with reasons; a "Sensor colour filters" row (sensor, front filter, low-pass filter, filled from the names Siril's SPCC accepts) whenever a colour master (`OSC` or `OSCNB`) is selected; values saved like the existing options. ttk widgets, as the existing dialog uses.

- [ ] **Step 1: Failing tests** in the existing dialog test style (model-level tests that run without a display; Tk tests skip without one). **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS. **Step 5: Commit**: "Siril: dialog controls for colour masters".

### Task 20: Siril end-to-end and parity

**Files:**
- Modify: `tests/loom/integration/test_osc_siril.py`; `docs/PARITY-LOOM.md`
- Test: `tests/acceptance/` hygiene tests stay green

- [ ] **Step 1:** Add `siril`-marked warm-cache runs that change each OSC spectral choice in turn (sensor, front filter, low-pass filter), with sharpening on and off, and assert colour calibration re-runs each time; SPCC OSC-then-mono and mono-then-OSC in separate runs of one session, checking each call's arguments.
- [ ] **Step 2:** Add a `docs/PARITY-LOOM.md` row for every function in `osc.py` and every modified function (JS name with file:line, Python name, test file), and a deviations entry for each Siril-specific point of the spec's "Siril port" section.
- [ ] **Step 3:** Run the parity and hygiene tests (`pytest -q tests/acceptance`) and the Docker `siril` tests → PASS. **Step 4: Commit**: "Siril: OSC end-to-end checks and parity rows".

---

## Phase C: Release readiness

### Task 21: Documentation, release notes and version

**Files:**
- Modify: `pixinsight/README.md` (new "One-shot colour (OSC) cameras" section; remove "not supported" wording), `pixinsight/CHANGELOG.md` (`## [Unreleased]`), `pixinsight/ci/release-notes.sh` (line 44) and `pixinsight/ci/release-notes.test.js` (lines 57-58), `siril/README.md` (replace the "Cameras" bullet at line 12; add an OSC section), `siril/CHANGELOG.md` (`## [Unreleased]`); release tooling: `pixinsight/ci/release-prep.js` and `pixinsight/ci/release-prep.test.js` (new), `pixinsight/ci/release.sh`, `pixinsight/ci/run-tests.js` (gate list), `pixinsight/docs/RELEASING.md`, `siril/tests/test_package.py`

- [ ] **Step 1: Failing test.** In `ci/release-notes.test.js` replace the "OSC cameras are said not to be supported" check with one asserting the notes say OSC masters are supported ("broadband and Hα + OIII dual-band colour masters") and no longer contain "not supported yet". Run `node ci/run-tests.js` → FAIL.
- [ ] **Step 2:** Update `ci/release-notes.sh` text; run → PASS.
- [ ] **Step 3: READMEs.** Both READMEs gain a section covering: what OSC masters are accepted (debayered, three channels; one broadband and/or one Hα + OIII dual-band per run), how kind is detected and overridden, that colour and mono masters cannot be combined, HOO only (SHO/HSO refused with the reason), the sensor colour filter choice, the light-pollution filter note, and quad-band carrying SII in H. Siril README edits stay inside the new section and the one "Cameras" bullet.
- [ ] **Step 4: CHANGELOGs.** Both `## [Unreleased]` sections already say OSC cameras are not supported (`pixinsight/CHANGELOG.md` line 8, `siril/CHANGELOG.md` line 11): replace those bullets with the support announcement (an `### Added` list, OSC only), so no contradictory statement ships. Then generate the release notes from the real candidate changelogs (`ci/release-notes.sh` with the next version, run against a scratch copy where `[Unreleased]` has been finalised with `ci/release-prep.js`) and check by eye and with `grep -i "not supported"` that the notes announce OSC support and nowhere deny it; paste the generated notes in the pull request.
- [ ] **Step 5: Siril in the release script.** `ci/release.sh` finalises only the PixInsight `CHANGELOG.md` and `Util.LOOM_VERSION` (release.sh 52-71, staged at line 84), so a tag would leave the Siril notes under `[Unreleased]` and its version at the previous release. Add `pixinsight/ci/release-prep.js` exporting `finalizeChangelog( text, version, date ) -> text` (the same transformation release.sh's Python does today: insert `## [<version>] — <date>` after `## [Unreleased]`, refusing an empty section) and `setPythonVersion( text, version ) -> text` (replaces `__version__ = "..."`, refusing a file without exactly one). Add `pixinsight/ci/release-prep.test.js` (cases: normal, empty section refused, missing heading refused, version line replaced, two version lines refused) and run it from `ci/run-tests.js` beside `release-notes.test.js` (line 57). Make `ci/release.sh` call `node ci/release-prep.js "$version"` right after its Python block to update `../siril/CHANGELOG.md` and `../siril/loomsiril/__init__.py`, and stage both in the release commit (line 84: `git add CHANGELOG.md script ci/signed-tree.sha256 repo ../siril/CHANGELOG.md ../siril/loomsiril/__init__.py`). Update `docs/RELEASING.md` steps 2 and 6 to say so. In `siril/tests/test_package.py` add a check that `loomsiril.__version__` equals the newest dated section of `siril/CHANGELOG.md`, which holds before and after a release. Run `node ci/run-tests.js` (the gate runs the new test first) and `PYTHONPATH=$PWD python -m pytest -q tests/test_package.py` from `siril/` → PASS. The version itself (`0.6.0`, the next minor after the latest `v*` tag) is set by the release script, not edited here.
- [ ] **Step 6:** Run `LOOM_TEST_ONLY=osc node ci/run-tests.js` and `pytest -q siril/tests/acceptance` → PASS. Commit: "OSC: documentation, release notes; the release script finalises the Siril notes and version".

### Task 22: Release candidate checks

- [ ] **Step 1:** Open a pull request from `osc` to `main`. Both workflows run (`.github/workflows/ci.yml`, `siril.yml`); the pull request is ready only when every job is green on the head commit.
- [ ] **Step 2: Siril integration evidence.** The `docker` job in `siril.yml` has `continue-on-error: true` (line 51), so a green workflow does not prove the Siril-marked OSC tests ran. Run the Docker image on the pull request's head commit with `python -m pytest -q -rs -m siril tests/loom/integration/test_osc_siril.py` and paste the output in the pull request: every OSC test passed, none skipped. The candidate is not ready without it.
- [ ] **Step 3:** Run in PixInsight (hand checks, results pasted in the pull request): the full selftest including `osc.e2e`, the hand checks of Tasks 6, 8, 9, 10 and 11, and one real run with a broadband and a dual-band OSC master (the maintainer's data), checking the masters list, the dialog controls and the outputs.
- [ ] **Step 4:** Run in Siril (hand check): the same masters through `Loom/loom_preprocess.py`.
- [ ] **Step 5:** Release is the maintainer's step, per `docs/RELEASING.md`: merge, then from `pixinsight/` run `ci/release.sh 0.6.0 --key-dir <folder> --push` (it finalises both changelogs and both versions, signs, tags `v0.6.0` and pushes).

## Dependency order and parallel groups

A task starts only when every task it consumes from is merged on `osc`.

| Wave | Tasks (parallel within a wave) | Needs |
|---|---|---|
| 1 | 1 | — |
| 2 | 2 | 1 |
| 3 | 3 | 1, 2 |
| 4 | 4, 5 | 3 |
| 5 | 6, 10 | 5 (6); 3 (10) |
| 6 | 7 | 4, 6 |
| 7 | 8, 11 | 6, 7 (8); 7, 10 (11) |
| 8 | 9 | 7, 8 |
| 9 | 12 | 6-9 |
| 10 | 13 | 1-12 (Phase A complete) |
| 11 | 14 | 13 |
| 12 | 15 | 14 |
| 13 | 16 | 15 |
| 14 | 17 | 16 |
| 15 | 18, 19 | 17 (18); 15 (19) |
| 16 | 20 | 18 |
| 17 | 21 | 12, 20 |
| 18 | 22 | 21 |

Tasks in one wave never edit the same function. Waves 5 and 7 hold tasks that touch different files (6: `Pipeline.js` loading and `Steps.js` plane helpers; 10: `Util.js` discovery and `UI.js` entry routing; 8: `Pipeline.js` registration and `Steps.combinePlanes`; 11: `Util.oscControlState` and `UI.js` dialog builders). Each task's brief carries the **Interfaces** of the tasks it consumes.
