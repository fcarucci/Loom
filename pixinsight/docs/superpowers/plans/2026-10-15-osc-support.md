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

- PixInsight scripts keep running on PixInsight 1.9.4 and 1.9.5: `node ci/run-tests.js` and `LOOM_TEST_CORE_RELEASE=4 node ci/run-tests.js` both pass.
- Every new check in `script/selftest.js` sits inside an `if ( testGroup( "<group>" ) )` block; the groups used by this plan start with `osc.` and `ci/test-groups.js` stays green.
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
| `tools/oracle/osc.js` (new), `tests/oracle/osc.json` (new) | Recorded JavaScript values for the pure functions. |
| `tests/loom/test_osc.py` (new) and the listed test files | Unit tests; `tests/loom/integration/test_osc_siril.py` (new) for `siril`-marked runs. |
| `docs/PARITY-LOOM.md`, `README.md`, `CHANGELOG.md`, `loomsiril/__init__.py` | Parity rows, user docs, release notes, version. |

---

## Phase A: PixInsight

All commands in Phase A run from `pixinsight/`. Run one group while working:
`LOOM_TEST_ONLY=osc.detect node ci/run-tests.js` (replace the group). Before a
task's commit, also run the full suite once: `node ci/run-tests.js | tail -1`
must print PASS.

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
   return String( value ).toLowerCase().replace( /[^a-z0-9]/g, "" );
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
- [ ] **Step 5: Full suite and commit.** `node ci/run-tests.js | tail -1` → PASS. Commit `script/lib/Util.js script/selftest.js`: "OSC: colour-camera filter table and kind detection".

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

- [ ] **Step 4: Run** the group → PASS; full suite → PASS.
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
- [ ] **Step 4: Run** the group → PASS; full suite → PASS (existing `util` and `ui` groups must stay green: `paths` gained two keys).
- [ ] **Step 5: Commit**: "OSC: colour slots in the configuration, logical channels, kind overrides".

### Task 4: Preflight and the logical-channel consumers

**Files:**
- Modify: `script/lib/Pipeline.js` `Pipeline.preflightChannels` (39), `Pipeline.preflight` (58), `Pipeline.narrowbandSupplied` (95), `Pipeline.wantedPalettes` (110), `Pipeline.reportSkippedPalettes` (127), `Pipeline.marsProblems` (150), `Pipeline.preflightView` (167), `Pipeline.preflightPath` (181)
- Test: `script/selftest.js` (new group `osc.preflight`)

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: `Pipeline.oscPaletteProblem( config ) -> string | null`; `Pipeline.oscKindProblems( config, infoOf ) -> [ string ]` where `infoOf( slot ) -> { filter, path, numberOfChannels, width, height }` (injectable for tests; production reads `Util.readImageInfo` or the view); `Pipeline.preflight( config )` fills `config.oscKinds` and returns problems including the four verbatim errors.

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
   check( "oscPaletteProblem SHO with colour",
          Pipeline.oscPaletteProblem( cfg( { OSCNB: "/m/nb" }, [ "SHO", "HOO" ] ) ),
          "SHO and HSO need SII, which a colour camera cannot separate from Ha: choose HOO" );
   check( "oscPaletteProblem HOO only", Pipeline.oscPaletteProblem( cfg( { OSCNB: "/m/nb" }, [ "HOO" ] ) ), null );
   check( "oscPaletteProblem mono run unaffected",
          Pipeline.oscPaletteProblem( cfg( { H: "/m/h", S: "/m/s", O: "/m/o" }, [ "SHO" ] ) ), null );
   check( "narrowbandSupplied OSCNB", Pipeline.narrowbandSupplied( cfg( { OSCNB: "/m/nb" } ) ), true );
   check( "wantedPalettes OSCNB gives HOO",
          Pipeline.wantedPalettes( cfg( { OSCNB: "/m/nb" }, [ "HOO" ] ) ), [ "HOO" ] );
   function infoOf( table ) { return function( slot ) { return table[slot]; }; }
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
  - `Pipeline.oscKindProblems`: for each supplied colour slot, read `infoOf( slot )`; one channel → the mono-file error above; any other count than 3 → `Util.channelCountProblem`; else `Util.effectiveOscKind( slot, filter, path, config.oscKindChoices )`; `unsupported` → verbatim error; `source == "none"` → "choose Broadband or Dual-band Hα + OIII for <path>"; kind not matching the slot (`OSC` must be broadband, `OSCNB` dual-band) → the mismatch error; otherwise set `config.oscKinds[slot]`. When both slots are used, they must have the same width and height: "<pathA> and <pathB> differ in size (WxH vs WxH)".
  - `Pipeline.preflight` adds, in this order: `Util.colourMixProblem`, `Pipeline.oscPaletteProblem`, `Pipeline.oscKindProblems( config, Pipeline.oscInfoOf )` where `Pipeline.oscInfoOf( config )( slot )` reads `Util.readImageInfo( config.paths[slot] )` or the view's image, and the sensor check from Task 7 (leave a call to `Pipeline.oscSensorProblems( config )` returning `[]` for now; Task 7 fills it).
  - `Pipeline.preflightChannels`, `preflightPath` and `preflightView` iterate `Util.CHANNELS.concat( Util.COLOUR_SLOTS )`; for colour slots they skip the mono-only keyword checks that Task 7 replaces.
- [ ] **Step 4: Run** the group → PASS; full suite → PASS.
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
  - `Pipeline.oscGroupRegisterKey( stage, siblingKeysInPlaneOrder, params ) -> hex` = `Cache.chainKey( Cache.hash( siblingKeys.join( "|" ) ), stage, params )`; `stage` is `"registerGroup"` or `"cleanRegisterGroup"`.
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
   var c1 = Pipeline.oscGroupRegisterKey( "cleanRegisterGroup", [ k0, k1, "k2" ],
              { ref: "refA", crop: Pipeline.cropTag( { x0: 0, y0: 0, x1: 90, y1: 70 } ), planes: "R,G,B" } );
   var c2 = Pipeline.oscGroupRegisterKey( "cleanRegisterGroup", [ k0, k1, "k2" ],
              { ref: "refA", crop: Pipeline.cropTag( { x0: 0, y0: 0, x1: 80, y1: 70 } ), planes: "R,G,B" } );
   check( "clean group key changes with the crop", c1 != c2, true );
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
- Produces: lazy channel objects for `R`,`G`,`B` (from `OSC`) and `H`,`O` (from `OSCNB`) with the same shape `Pipeline.lazyFileChannel` returns today (`key, path, window, view, filter, instrume, sourceKey, currentKey, load()`), plus `group: "OSC"|"OSCNB"`, `plane: n`; `Pipeline.oscGroupLoader( slot, source, kind, reg, io ) -> { open(), planeWindow( plane ), release(), opens }` where `io` holds injectable `openMaster`, `cacheLoad`, `cacheStore`, `writeSidecar`, `readSidecar` functions (production passes the real ones); `Pipeline.oscPlaneProblem( stats, plane, path ) -> string | null`.

- [ ] **Step 1: Failing tests** (node, with fake io; no pixels):

```javascript
   } if ( testGroup( "osc.load" ) ) {
   check( "oscPlaneProblem constant plane",
          Pipeline.oscPlaneProblem( { min: 0, max: 0, median: 0 }, 2, "/m/a.xisf" ),
          "/m/a.xisf: plane 2 is constant (all values 0); the master looks broken" );
   check( "oscPlaneProblem normal plane",
          Pipeline.oscPlaneProblem( { min: 0.01, max: 0.9, median: 0.1 }, 0, "/m/a.xisf" ), null );
   function fakeIo( cached )
   {
      var io = { opens: 0, stored: [], sidecar: null };
      io.openMaster = function() { io.opens++; return { id: "master", planes: 3 }; };
      io.cacheLoad = function( key ) { return cached[key] ? { id: "cached_" + key } : null; };
      io.cacheStore = function( key ) { io.stored.push( key ); cached[key] = true; };
      io.planeWindow = function( master, plane ) { return { id: "plane" + plane }; };
      io.planeStats = function() { return { min: 0.01, max: 0.9, median: 0.1 }; };
      io.writeSidecar = function( name, rec ) { io.sidecar = rec; };
      io.readSidecar = function() { return io.sidecar; };
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
   check( "warm run logs the cached plane statistics", loader2.validation().planes.length, 3 );
   delete cache[ Pipeline.oscSiblingKey( Pipeline.oscGroupIdentity( "fp", "broadband" ), 1 ) ];
   var io3 = fakeIo( cache );
   Pipeline.oscGroupLoader( "OSC", { path: "/m/a.xisf", fingerprint: "fp" }, "broadband", null, io3 ).planeWindow( 1 );
   check( "a missing split plane reopens the master once", io3.opens, 1 );
   // OSCNB: H is plane 0, O is plane 1, blue never requested.
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
- [ ] **Step 4: Run** the group → PASS; full suite → PASS.
- [ ] **Step 5: In PixInsight** (hand check, logged in the PR description): the `osc.load` checks also pass inside PixInsight (`Loom selftest` script), and `Steps.extractPlane` on a synthetic three-channel image returns the right plane (add an `IN_PIXINSIGHT`-guarded check that builds a 3×2 three-channel image with distinct plane values).
- [ ] **Step 6: Commit**: "OSC: one shared loader per colour master, cached split planes, plane validation".

### Task 7: Calibration response

**Files:**
- Modify: `script/lib/Pipeline.js` `Pipeline.broadbandStages` (1836), `Pipeline.broadbandRunners` (1879), `Pipeline.compositeInstrument` (2122), `Pipeline.inheritInstrument` (1780), `Pipeline.buildPalette` (1453); add `Pipeline.oscSensorProblems`, `Pipeline.oscSpfcParams`
- Modify: `script/lib/Steps.js` `Steps.spccNarrowband` (1461)
- Test: `script/selftest.js` (new group `osc.calibration`)

**Interfaces:**
- Consumes: `config.oscSensor`, `config.oscKinds`, `Util.oscFilterInfo`.
- Produces: `Pipeline.oscSpfcParams( channel, config ) -> { channel, filter: <sensor curve>, qe: "Ideal QE curve" }`; `Pipeline.oscSensorProblems( config, curveExists ) -> [ string ]` (`curveExists( name, channel )` injectable; production `Steps.filterCurveByName`); `Steps.spccNarrowband( view, palette, bandwidthNm )` where `bandwidthNm` is a number (all channels) or `{ R, G, B }`; `Pipeline.oscNarrowbandBandwidths( config ) -> { R, G, B }` for HOO from `OSCNB`.

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
  - `Pipeline.inheritInstrument`: return unchanged when any channel has a `group`.
  - `Pipeline.oscSensorProblems` runs in `Pipeline.preflight` (replace the Task 4 stub).
  - `Steps.spccNarrowband`: accept `{ R, G, B }`; set `redFilterBandwidth` etc. per channel; log the three bandwidths and, when called for an `OSCNB` palette, "approximation: H and O from a colour master are not single lines".
  - `Pipeline.buildPalette`: for a palette built from `OSCNB` channels pass `Pipeline.oscNarrowbandBandwidths( config, info )`.
  - When `OSC` is used with a light-pollution filter (`Util.oscFilterInfo( filter ).name` normalised in `["lpro", "cls", "clsccd"]`), log once: "colour calibration does not model the <name> filter in front of the camera".
- [ ] **Step 4: Run** the group, `steps.*` groups and the full suite → PASS.
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

- [ ] **Step 2: Run** `LOOM_TEST_ONLY=osc.register node ci/run-tests.js` → FAIL.
- [ ] **Step 3: Implement.**
  - `Pipeline.oscRegistrationPlan` as specified (spec "Registration").
  - `Pipeline.registrationReference( present, quality, groups )`: when `groups` has any member, return `{ key: plan.ref, reason: "colour group reference" }` without consulting `quality`; mono behaviour unchanged when `groups` is absent.
  - `Pipeline.registerToReference`: build `groups` from the loaded channels' `group` fields. Identity members keep their views. For each grouped entry: key = `Pipeline.oscGroupRegisterKey( "registerGroup", member current keys in plane order, { ref: refKey, planes: planes.join( "," ) } )`. On a cache hit of every `Pipeline.oscRegisteredSiblingKey( key, i )` load those; else if the group image `key` is cached, split it (`Steps.extractPlane`) and store the splits; else `Steps.combinePlanes`, `Steps.register( combined, refView )` once, store the group image under `key`, split into the members, store each split, release the temporary windows from the run's registry. Members' `currentKey` become their registered split keys.
  - `Steps.combinePlanes( views, newId )`: a three-channel float window whose channel i is `views[i]`'s image (same size required, else throw).
- [ ] **Step 4: Run** group and full suite → PASS.
- [ ] **Step 5: In PixInsight** (hand check, logged): an `IN_PIXINSIGHT`-guarded check registers a synthetic `OSCNB` pair shifted by (3, 2) px against a synthetic `OSC` `G` and asserts the registered `H` and `O` are pixel-aligned with each other (star centroids within 0.05 px) and with `G` (within 0.2 px).
- [ ] **Step 6: Commit**: "OSC: siblings share one registration transform".

### Task 9: Clean white balance for a colour group

**Files:**
- Modify: `script/lib/Pipeline.js` `Pipeline.measureCleanWhiteBalance` (1207), `Pipeline.registerCleanChannel` (1155), `Pipeline.cleanWhiteBalance` (2390)
- Test: `script/selftest.js` (group `osc.register`, extended)

**Interfaces:**
- Consumes: Tasks 5 and 8.
- Produces: `Pipeline.cleanRegistrationPlan( refKey, groups ) -> "identity" | "grouped" | "mono"`.

- [ ] **Step 1: Failing tests** (append to `osc.register`):

```javascript
   check( "clean plan: OSC is the reference group",
          Pipeline.cleanRegistrationPlan( "G", { OSC: [ "R", "G", "B" ] } ), "identity" );
   check( "clean plan: OSC is not the reference group",
          Pipeline.cleanRegistrationPlan( "H", { OSC: [ "R", "G", "B" ], OSCNB: [ "H", "O" ] } ), "grouped" );
   check( "clean plan: mono run", Pipeline.cleanRegistrationPlan( "L", {} ), "mono" );
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** In `measureCleanWhiteBalance`: `"mono"` keeps today's per-channel `registerCleanChannel`; `"identity"` crops the three clean planes with `common` (the same crop call `Pipeline.cropToCommonArea` uses) and measures; `"grouped"` combines the clean R, G, B into one image, registers once against the cropped reference, caches under `Pipeline.oscGroupRegisterKey( "cleanRegisterGroup", cleanKeys, { ref: refKey, crop: Pipeline.cropTag( common ), planes: "R,G,B" } )` and splits. Pass the reference key to `Pipeline.cleanWhiteBalanceKey` (Task 5).
- [ ] **Step 4: Run** group, `pipeline.*` groups and full suite → PASS.
- [ ] **Step 5: In PixInsight** (hand check, logged): the Task 8 synthetic pair, white-balance branch: clean planes pixel-aligned; a smaller `OSCNB` (cropped by 20 px) re-registers the clean group (log shows a MISS for `cleanRegisterGroup`); replacing the `OSC` file with a byte-different copy re-measures the white balance.
- [ ] **Step 6: Commit**: "OSC: clean white balance registers a colour group once".

### Task 10: Folder discovery of colour masters

**Files:**
- Modify: `script/lib/Util.js` (add `Util.selectColourMasters`, `Util.colourCandidateCorrection`)
- Modify: `script/lib/UI.js` `scanMasterFolder` (2007), `resolveUnnamedMasters` (2024), `confirmMasters` (2046), `adoptMasters` (2178), `reportScan` (2196)
- Test: `script/selftest.js` (new group `osc.discovery`)

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: `Util.selectColourMasters( candidates ) -> { OSC: candidate|null, OSCNB: candidate|null }` where a candidate is `{ path, filter, colour, drizzle, autocrop, mtime, numberOfChannels, imagetyp }` and its slot is decided by `Util.effectiveOscKind` (unknown kind lands in `OSC` when broadband-looking names fail, else is listed with an empty kind: return it under `unresolved: [ candidates ]`); `Util.colourCandidateCorrection( candidate ) -> string | null` ("name says colour, header says 1 channel: <file> is used as mono", and the reverse).

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
```

`Util.selectColourMasters( candidates, choices )` takes the remembered kind choices as its second argument.

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** the two Util functions (variant ranking with `Util.masterVariantRank`, then newest `mtime`, as `Util.selectMasters` does). In `UI.js`: `scanMasterFolder` collects names with `Util.parseColourMasterName( name ).colour` into `scan.colourNamed`; `resolveUnnamedMasters` header-reads unnamed files when a mono slot or a colour slot has no named winner, routing three-channel files to colour candidates; `confirmMasters` confirms colour candidates by header (`numberOfChannels == 3`, `IMAGETYP` a master light via `Util.isMasterLight` or its equivalent) and reports `Util.colourCandidateCorrection`; `adoptMasters` sets `config.paths.OSC` / `OSCNB` and keeps mono winners too; when both kinds are adopted the masters list shows both and `updateRunEnabled` (1937) keeps Run disabled with `Util.colourMixProblem`'s text.
- [ ] **Step 4: Run** group and full suite → PASS (the `ui` groups must stay green).
- [ ] **Step 5: In PixInsight** (hand check, logged): a folder with a synthetic `OSC`, a synthetic `OSCNB` and a mono L master lists all three and Run stays disabled with the mix message.
- [ ] **Step 6: Commit**: "OSC: discover colour masters in a folder".

### Task 11: Dialog controls

**Files:**
- Modify: `script/lib/UI.js` `buildMasterList` (855), `showMaster` (2166), `buildFilterRows` (982), `buildPaletteGroup` (1119), `updatePaletteVisibility` (2391), `rebuild` (2417), `updateRunEnabled` (1937); add `UI.oscControlState`
- Test: `script/selftest.js` (new group `osc.ui`)

**Interfaces:**
- Consumes: Tasks 1-4, 7, 10.
- Produces: `UI.oscControlState( config ) -> { monoEnabled, monoReason, shoHsoEnabled, shoHsoReason, sensorRowVisible, rgbFilterRowsVisible }` (pure, node-tested); the dialog applies it.

- [ ] **Step 1: Failing tests.**

```javascript
   } if ( testGroup( "osc.ui" ) ) {
   var c = Config.defaults(); c.paths.OSC = "/m/a.xisf";
   var s = UI.oscControlState( c );
   check( "mono boxes greyed with reason", [ s.monoEnabled, s.monoReason ],
          [ false, "colour and mono masters cannot be combined" ] );
   check( "SHO/HSO greyed with reason", [ s.shoHsoEnabled, s.shoHsoReason ],
          [ false, "needs SII, which a colour camera cannot separate from Ha" ] );
   check( "sensor row instead of R/G/B filter boxes", [ s.sensorRowVisible, s.rgbFilterRowsVisible ], [ true, false ] );
   var m = Config.defaults(); m.paths.L = "/m/l.xisf";
   var t = UI.oscControlState( m );
   check( "mono run unchanged", [ t.monoEnabled, t.shoHsoEnabled, t.sensorRowVisible ], [ true, true, false ] );
   var nb = Config.defaults(); nb.paths.OSCNB = "/m/n.xisf";
   check( "OSCNB alone: no sensor row", UI.oscControlState( nb ).sensorRowVisible, false );
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `UI.oscControlState` and apply it:
  - masters list rows for `OSC` / `OSCNB`: file name, detected filter, a kind `ComboBox` ("", "Broadband", "Dual-band Hα + OIII") preset from `Util.effectiveOscKind`, disabled for `unsupported` kinds, writing `config.oscKindChoices[ Util.oscKindKey( filter, path ) ]` on change; a "detected" `Label` (link style) that deletes that key and refreshes;
  - mono channel boxes disabled with the reason as tooltip while a colour master is selected;
  - SHO and HSO check boxes unticked and disabled with the reason; a remembered SHO/HSO choice is not applied to a colour run (it stays in the saved settings for mono runs);
  - SPCC section: while `OSC` is used, hide the R, G, B filter rows and show a "Sensor colour filters" row of three `ComboBox`es filled from `Steps.listFilterCurves( "R" | "G" | "B" )` (Steps.js 530), writing `config.oscSensor`;
  - every new value saved through `Config.save` like the existing fields.
- [ ] **Step 4: Run** group and full suite → PASS.
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
- [ ] **Step 5: Run** the node suite → PASS. Run `osc.e2e` in PixInsight (hand check; paste the result line in the PR description).
- [ ] **Step 6: Commit**: "OSC: provenance keywords, log lines, end-to-end checks".

---

## Phase B: Siril port

All commands in Phase B run from `siril/`. Unit tests:
`PYTHONPATH=$PWD python -m pytest -q tests/loom/<file>`. Siril-marked tests run
in the Docker image built from `docker/Dockerfile`; CI runs them in the
`docker` job of `.github/workflows/siril.yml`.

### Task 13: Record the JavaScript oracle for the OSC pure functions

**Files:**
- Create: `tools/oracle/osc.js`, `tests/oracle/osc.json`
- Modify: `tools/record_oracle.js` (add the `osc` set)

**Interfaces:**
- Produces: `tests/oracle/osc.json` with cases for `Util.normaliseFilter`, `Util.oscFilterInfo`, `Util.parseColourMasterName`, `Util.channelCountProblem`, `Util.suppliedChannels`, `Util.colourMixProblem`, `Util.oscKindKey`, `Util.effectiveOscKind`, `Pipeline.oscPaletteProblem`, `Pipeline.oscKindProblems` (with a table-driven `infoOf`), `Pipeline.oscGroupIdentity`, `Pipeline.oscSiblingKey`, `Pipeline.oscValidateName`, `Pipeline.oscGroupRegisterKey`, `Pipeline.oscRegisteredSiblingKey`, `Pipeline.cleanWhiteBalanceKey` (OSC and mono), `Pipeline.oscRegistrationPlan`, `Pipeline.cleanRegistrationPlan`, `Pipeline.oscNarrowbandBandwidths`, `Pipeline.oscPaletteSources`, `UI.oscControlState`.

- [ ] **Step 1:** Read `tools/record_oracle.js` and one existing recorder under `tools/oracle/`; copy its structure (`module.exports = function( js ) { return { "<case>": { fn, args, out } } }`, clone args before each call).
- [ ] **Step 2:** Write `tools/oracle/osc.js` with at least the inputs used in the Phase A tests plus: every entry of `Util.OSC_FILTERS`, five spellings each of two models, ten random mixed-case names, empty/absent values, all palette combinations with and without colour slots, and the key functions over ten fingerprints.
- [ ] **Step 3:** Record: `LOOM_JS_DIR=../pixinsight/script node tools/record_oracle.js tests/oracle/ osc`. Commit both files: "Siril: record the OSC oracle from the JavaScript".

### Task 14: `osc.py` pure functions

**Files:**
- Create: `loomsiril/loom/osc.py`, `tests/loom/test_osc.py`

**Interfaces:**
- Consumes: Task 13 oracle.
- Produces: `normalise_filter`, `OSC_FILTERS`, `osc_filter_info`, `parse_colour_master_name`, `channel_count_problem`, `COLOUR_SLOTS`, `COLOUR_MEMBERS`, `supplied_channels`, `colour_mix_problem`, `osc_kind_key`, `effective_osc_kind`, `osc_palette_problem`, `osc_kind_problems`, `group_identity`, `sibling_key`, `validate_name`, `group_register_key`, `registered_sibling_key`, `registration_plan`, `clean_registration_plan`, `narrowband_bandwidths`, `palette_sources`, `control_state`: one Python function per JS function, snake_case of the JS name, same arguments and return shapes (dicts for JS objects).

- [ ] **Step 1: Failing test.** `tests/loom/test_osc.py` replays every case of `tests/oracle/osc.json` (copy the replay and compare helpers of an existing oracle test such as `tests/test_frames.py`: floats to 1e-9 relative, NaN equal, the rest exact) and adds a guard that every recorded function has a Python twin.
- [ ] **Step 2: Run** `pytest -q tests/loom/test_osc.py` → FAIL (module missing).
- [ ] **Step 3: Implement** `osc.py`. Hashes use the port's existing `hash_str` / chain-key helpers in `loomsiril/loom/cache.py` (`clean_white_balance_key`, line 205, shows the pattern) so Python keys equal JavaScript keys.
- [ ] **Step 4: Run** → PASS. **Step 5: Commit**: "Siril: OSC detection, selection rules and keys ported".

### Task 15: Configuration, preflight and channel consumers

**Files:**
- Modify: `loomsiril/loom/config.py` (`defaults` line 108, `_apply_document` 275, `_document` 364), `loomsiril/loom/preflight.py` (`preflight` 156), `loomsiril/loom/channels.py` (`validate_selection` 119, `duplicate_file_problems` 99, `narrowband_supplied` 554, `wanted_palettes` 561, `report_skipped_palettes` 572, `inherit_instrument` 588, `scan_masters` 686)
- Test: `tests/loom/test_config.py`, `tests/loom/test_preflight.py`, `tests/loom/test_channels.py` (new tests in each)

**Interfaces:**
- Consumes: Task 14.
- Produces: config keys `paths["OSC"]`, `paths["OSCNB"]`, `osc_kinds`, `osc_sensor` (`{"sensor", "front_filter", "lpf"}` Siril names, see Task 17), `osc_kind_choices`; preflight errors identical to the JavaScript texts; `scan_masters` returns colour candidates and corrections.

- [ ] **Step 1: Failing tests:** the mix error, the SHO/HSO error, an unconfident kind, an unsupported filter, a mono file in a colour slot, `OSCNB` alone giving HOO, `osc_sensor` and `osc_kind_choices` round-tripping through `save`/`load`, `scan_masters` on a temporary folder with a synthetic three-channel FITS named `..._FILTER-L-eXtreme_color.fit` and a mono `..._FILTER-L_mono.fit` (both listed; selection blocked), and a name/header disagreement reported.
- [ ] **Step 2: Run** the three files → FAIL. **Step 3: Implement** by calling `osc.py`. **Step 4: Run** → PASS. **Step 5: Commit**: "Siril: OSC configuration, preflight and discovery".

### Task 16: Loading and splitting a colour master

**Files:**
- Modify: `loomsiril/loom/pipeline.py` (`_Run.load_channels`, line 190; `_Chan`, line 140)
- Test: `tests/loom/test_pipeline.py` (new tests)

**Interfaces:**
- Produces: `_Chan` gains `group` and `plane`; colour members are loaded from FITS plates written into the cache under their sibling keys; the validation sidecar (same name as the JS) holds the plane statistics; the master is read at most once per run, not at all when every sibling plate is cached.

- [ ] **Step 1: Failing tests** (synthetic three-channel FITS from `tests/loom/synth.py` written with the port's FITS writer): split planes equal the source planes (`O` equals plane 1 exactly, independent of plane 2); a constant plane is refused with the JS text; a second run reads the master zero times (count opens by monkeypatching the reader); deleting one plate reads it once; `FILTER` and `LOOMSRC` are set on each plate.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** (numpy; pixels via the port's existing FITS reader; never through the image open in Siril). **Step 4: Run** → PASS. **Step 5: Commit**: "Siril: colour masters split into cached channel plates".

### Task 17: SPCC for OSC and the calibration identity

**Files:**
- Modify: `loomsiril/loom/siril_ops.py` (`_spcc_checks` 190, `_text_spcc_broadband` 196, `_text_spcc_narrowband` 210, the `spcc_broadband` methods at 412, 618, 842); `loomsiril/loom/stages_colour.py` (`rgb_stage_params` 61, `rgb_spcc_names` 96, `_palette_spcc` 228); `loomsiril/loom/cache.py` (`clean_white_balance_key` 205)
- Test: `tests/loom/test_siril_ops.py`, `tests/loom/test_siril_ops_spcc_session.py`, `tests/loom/test_stages_colour.py`, `tests/loom/test_cache.py`

**Interfaces:**
- Produces: `_text_spcc_osc( sensor, front_filter, lpf ) -> str` emitting `spcc -oscsensor=<sensor> -oscfilter=<front> -osclpf=<lpf>` (all three always present; Siril's no-filter entry when none is chosen; read Siril 1.4 `spcc` help in the Docker image for the exact names, and record them in the module docstring); `spcc_osc(...)` methods on every ops class; a calibration identity `calibration_identity( config ) -> str` (mode plus every effective spectral argument) added to `rgb_stage_params` and to `clean_white_balance_key`; narrowband per-channel bandwidths from `osc.narrowband_bandwidths`.

- [ ] **Step 1: Failing tests:** the exact OSC command text; a mono call after an OSC call in one session sends the full mono argument set (and the reverse), extending `test_siril_ops_spcc_session.py`; `rgb_stage_params` and the clean white-balance key change when only the sensor, only the front filter or only the low-pass filter changes; narrowband SPCC gets `-rbw/-gbw/-bbw` from the filter table.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS. **Step 5: Commit**: "Siril: SPCC with an OSC sensor and the calibration identity in the keys".

### Task 18: Grouped registration in Siril

**Files:**
- Modify: `loomsiril/loom/stages_geometry.py` (`register_channels` 63, `register_stage_params` 50); `loomsiril/loom/pipeline.py` (`_Run.register` 262, `_Run.clean_white_balance` 318)
- Test: `tests/loom/test_stages_geometry.py` (stub tests) and `tests/loom/integration/test_osc_siril.py` (new, `siril`-marked)

**Interfaces:**
- Consumes: `osc.registration_plan`, `osc.group_register_key`, `osc.clean_registration_plan`.
- Produces: a registration step that, for a grouped colour group, expands the reference plate to three identical channels (`rgbcomp`), builds a two-image colour sequence (reference, group image), runs `register` with `-layer=1` for `OSC` or `-layer=0` for `OSCNB`, and splits the registered group image back into its member plates; the reference group's members are not registered.

- [ ] **Step 1: Failing stub tests:** the command sequence for an `OSCNB` group against an `OSC` reference (`rgbcomp`, sequence link, `register ... -layer=0`, split), the cache keys used, the identity case sending no registration command.
- [ ] **Step 2: Failing `siril`-marked tests** (`tests/loom/integration/test_osc_siril.py`): synthetic `OSC` and `OSCNB` masters with a (3, 2) px shift: registered `H` and `O` aligned to the reference `G` within 0.2 px and to each other within 0.05 px; the clean white-balance branch likewise.
- [ ] **Step 3: Implement.** **Step 4: Run** the stub tests locally and the `siril` tests in the Docker image → PASS. **Step 5: Commit**: "Siril: a colour group is registered as one image".

### Task 19: Siril dialog

**Files:**
- Modify: `loomsiril/loom/dialog.py`
- Test: `tests/loom/test_dialog.py` (new tests)

**Interfaces:**
- Consumes: `osc.control_state`, Task 15 config keys.
- Produces: masters list rows for `OSC` / `OSCNB` with a kind control and a "detected" reset; greyed mono boxes and SHO/HSO with reasons; a "Sensor colour filters" row (sensor, front filter, low-pass filter, filled from the names Siril's SPCC accepts) while `OSC` is used; values saved like the existing options. ttk widgets, as the existing dialog uses.

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
- Modify: `pixinsight/README.md` (new "One-shot colour (OSC) cameras" section; remove "not supported" wording), `pixinsight/CHANGELOG.md` (`## [Unreleased]`), `pixinsight/ci/release-notes.sh` (line 44) and `pixinsight/ci/release-notes.test.js` (lines 57-58), `siril/README.md` (replace the "Cameras" bullet at line 12; add an OSC section), `siril/CHANGELOG.md` (`## [Unreleased]`), `siril/loomsiril/__init__.py` (`__version__`)

- [ ] **Step 1: Failing test.** In `ci/release-notes.test.js` replace the "OSC cameras are said not to be supported" check with one asserting the notes say OSC masters are supported ("broadband and Hα + OIII dual-band colour masters") and no longer contain "not supported yet". Run `node ci/run-tests.js` → FAIL.
- [ ] **Step 2:** Update `ci/release-notes.sh` text; run → PASS.
- [ ] **Step 3: READMEs.** Both READMEs gain a section covering: what OSC masters are accepted (debayered, three channels; one broadband and/or one Hα + OIII dual-band per run), how kind is detected and overridden, that colour and mono masters cannot be combined, HOO only (SHO/HSO refused with the reason), the sensor colour filter choice, the light-pollution filter note, and quad-band carrying SII in H. Siril README edits stay inside the new section and the one "Cameras" bullet.
- [ ] **Step 4: CHANGELOGs.** One `### Added` bullet list under `## [Unreleased]` in each CHANGELOG, OSC only, in a separate list item from anything else there.
- [ ] **Step 5: Version.** The next release is a minor release: set `siril/loomsiril/__init__.py` `__version__` to the next minor version after the latest `v*` tag (`git describe --tags --abbrev=0`; 0.5.1 today, so `0.6.0`). `Util.LOOM_VERSION` is set by `ci/release.sh` at release time and is not edited here. Run `PYTHONPATH=$PWD python -m pytest -q tests/test_package.py` (version tests) → PASS.
- [ ] **Step 6:** Hygiene: `node ci/run-tests.js` and `pytest -q siril/tests/acceptance` → PASS. Commit: "OSC: documentation, release notes and version".

### Task 22: Release candidate checks

- [ ] **Step 1:** Open a pull request from `osc` to `main`. Both workflows run (`.github/workflows/ci.yml`, `siril.yml`); the pull request is ready only when every required job is green on the head commit.
- [ ] **Step 2:** Run in PixInsight (hand checks, results pasted in the pull request): the full selftest including `osc.e2e`, and one real run with a broadband and a dual-band OSC master (the maintainer's data), checking the masters list, the dialog controls and the outputs.
- [ ] **Step 3:** Run in Siril (hand check): the same masters through `Loom/loom_preprocess.py`.
- [ ] **Step 4:** Release itself (merge, `ci/release.sh <version> --push`) is the maintainer's step, per `docs/RELEASING.md`.

---

## Dependency order and parallel groups

| Wave | Tasks (parallel within a wave) | Needs |
|---|---|---|
| 1 | 1, 2 | — |
| 2 | 3 | 1, 2 |
| 3 | 4, 5 | 3 |
| 4 | 6, 7, 10 | 4, 5 (10 needs only 3) |
| 5 | 8, 11 | 6 (8), 7 and 10 (11) |
| 6 | 9, 12 | 8 (9), 6-9 (12) |
| 7 | 13 | Phase A merged on `osc` |
| 8 | 14 | 13 |
| 9 | 15, 16, 17 | 14 |
| 10 | 18, 19 | 16, 17 (18), 15 (19) |
| 11 | 20 | 18 |
| 12 | 21 | 12, 20 |
| 13 | 22 | 21 |

Tasks touching the same file in one wave (6 and 7 both edit `Pipeline.js` and `Steps.js`; 8 and 11 edit different files) are owned function by function as listed under each task's **Files**; whoever lands second rebuilds on the first.
