# ASIAIR Import for the Loom Frame Selector

Status: draft, revision 2 (after adversarial review). Not approved.

## Goal

Let the Frame Selector ingest directly from a ZWO ASIAIR connected over
USB-C: find the device, list its targets, break each target into nights,
and after the usual frame review copy the approved lights -- plus that
night's flats -- into a chosen destination.

The order matters. Frames are reviewed and rejected BEFORE anything is
written. Nothing from the card is copied and then deleted.

## What the card looks like

```
<ASIAIR root>
|-- Autorun
|   |-- Light/<Target>/<light frames>
|   `-- Flat/<flat frames>
`-- Plan            (same structure)
```

Targets are directory names under `Light/`. `Flat/` has no target and no
date subdirectory: it is one undivided folder.

### Filenames are parsed by token, never by position

The published grammar is wrong for real hardware. A frame from this
user's rig looks like:

```
Light_IC 1396A_180.0s_Bin1_2600MM_H_gain100_20260807-215716_180deg_-7.0C_0001.fit
```

against a published grammar of

```
{TYPE}_[{TARGET}_]{EXPOSURE}_Bin{N}_{FILTER}_gain{G}_{DATE}-{TIME}_{TEMP}C_{SEQ}.fit
```

Three deviations: the target contains a SPACE, there is a camera token
(`2600MM`) the grammar does not mention, and there is a rotation token
(`180deg`) it does not mention either. A positional parser would read
`2600MM` as the filter on every frame. Firmware versions and rig setups
differ, so the parser anchors on tokens that identify themselves -- and it
anchors FROM THE RIGHT, because the tail of the name is rigid while the
target is free text. Scanning left to right misparses a target that
happens to contain a token-shaped fragment: `Light_M42_30s_180.0s_Bin1_...`
has a target of `M42_30s` and two exposure-shaped tokens.

Parsed in this order:

| Step | Field    | Rule                                                      |
|------|----------|-----------------------------------------------------------|
| 1    | stamp    | the unique token matching `\d{8}-\d{6}`. TWO such tokens, or none, makes the name unparseable -- it is rejected, not guessed. |
| 2    | gain     | `gain\d+`, the token immediately before the stamp          |
| 3    | filter   | the token immediately before the gain token                |
| 4    | bin      | `Bin\d+`, the LAST such token before the filter            |
| 5    | camera   | any tokens between the bin token and the filter            |
| 6    | exposure | `\d+(\.\d+)?(s\|ms)`, the LAST such token before the bin  |
| 7    | target   | everything between the type and that exposure; may contain spaces and underscores |
| 8    | type     | the leading token                                          |
| 9    | rotation | `\d+deg` after the stamp, optional                         |
| 10   | temp     | `-?\d+(\.\d+)?C` after the stamp                          |
| 11   | sequence | the trailing all-digit token                               |

Taking the LAST exposure token before the bin reads `M42_30s` as part of
the target rather than as the exposure. Taking the filter as the token
before `gain` reads both `Bin1_S_gain360` and `Bin1_2600MM_H_gain100`
correctly without knowing the camera list.

A filter whose own name contains an underscore (`Ha_3nm`) still misparses
to `3nm`. This does not affect correctness, because the FITS header is
authoritative for filter identity everywhere it matters -- see below --
and the filename filter is only ever used to narrow candidates.

Extension matching is case-insensitive (`.fit`, `.FIT`, `.fits`). A file
whose name yields no timestamp and no exposure is not an ASIAIR frame:
it is skipped, counted, and the count is reported in the dialog. A file
that parses partially is reported by name, never silently dropped.

## Detection

By shape, never by volume name. The mounted volume is variously `BOOT`,
`EMMC Images`, `SD Images` or `USB Images` depending on model, firmware
and recording target.

A volume is an ASIAIR if it contains `Autorun/Light` or `Plan/Light`.
Loom enumerates entries of `/Volumes` only, one level deep, and tests each
candidate with two `File.directoryExists` calls. It does not walk, stat or
open anything else, so a stalled network share costs two calls rather than
a traversal. Traversal of a chosen card is depth-limited to the known
layout (`{Plan,Autorun}/{Light/<target>,Flat}`), never recursive, so
symlink cycles are unreachable. `.` and `..` are excluded, as PJSR's
FileFind yields them.

macOS only. That is the platform Loom runs on here, and the `/Volumes`
assumption is explicit rather than incidental.

## Sessions, nights and flats

Three findings from review collapse into one fix. Anchoring the flat
window on a single target's cluster breaks whenever a night holds more
than one target: target A running 20:00-22:00 and target B running to
05:00 share dawn flats at 06:00, but A's window closed at 02:00 and its
flats were reported missing. The same anchoring let two clusters both
claim one flat batch, and could bisect a flat batch at the window edge.

So clustering happens ONCE, over the LIGHTS of every target together --
and deliberately NOT over the flats. Including flats in the clustering
lets a run of daytime flats chain two nights into one: lights ending at
06:00, flats at 10:00, 14:00 and 18:00, lights again at 20:00, and every
gap is under the threshold, so two nights merge and pool their flats.

1. **Session.** Take every LIGHT on the card -- all targets, from both
   `Plan` and `Autorun` -- sort by timestamp, and start a new session
   wherever the gap exceeds GAP_HOURS (default 4, configurable). Flats
   take no part in this and therefore cannot bridge a gap.
2. **Night.** Within a session, a night is the frames of one target. It is
   labelled with its own first-frame date, span, count and filters. Two
   sessions on the same calendar date give two rows, as intended.
3. **Flat batches.** Flats are clustered among THEMSELVES by the same gap
   rule into batches. Assignment is then per BATCH, not per flat: a batch
   goes whole to the session nearest its midpoint. Assigning each flat
   individually would bisect a batch that straddles the midpoint between
   two sessions -- with sessions ending 06:00 and starting 20:00, a batch
   running 12:59 to 13:01 would be torn in half. Dusk batches fall to the
   session about to begin, dawn batches to the session just ended, and a
   daytime batch between two nights goes whole to the nearer one.

That fixes all three: dawn flats belong to the session, so every target in
it gets them; assignment is per flat to exactly one session, so a batch is
never bisected and never double-claimed; and because flats do not
participate in clustering, no amount of daytime flat-taking can join two
nights.

Nearest-session assignment is a heuristic and is presented as one. It
cannot know that the rig was rebuilt during the day. The dialog therefore
shows each matched flat set with its date and time and lets it be
deselected, so a set Loom guessed wrong can be dropped before Run.

### Timestamps

The filename stamp is a wall-clock label from the device, in an unstated
zone. It is converted to a comparison key with `Date.UTC` arithmetic on
the parsed components -- never by constructing a local `Date` and never by
string comparison. Local-time arithmetic would make clustering depend on
the importing computer's timezone and would mis-measure gaps across a DST
transition: 00:30 to 05:00 on a US spring-forward date is 4.5 hours by
the clock and 3.5 elapsed, which would split or merge a session depending
on the machine doing the import. UTC arithmetic on wall-clock components
is monotone and DST-free.

A stamp that does not parse, or that yields an impossible date, excludes
the frame from clustering; it is listed as unparseable rather than being
assigned to an arbitrary night.

A session has no maximum duration. Gap chaining is the definition.

### Flat compatibility

A flat in the night's assigned batches is a candidate for a light filter
when FILTER and Bin agree, both read from headers. Where the filenames carry the camera and rotation tokens,
those must agree too: the real filenames do carry them, and a rotation
change between lights and flats invalidates the flat. Where a token is
absent from both names it is not compared; where it is present in one and
absent in the other the pair is shown as a weak match rather than being
silently accepted or silently dropped.

This is as far as filenames can establish compatibility, and the design
says so rather than implying calibration correctness. The dialog lists the
matched flats per filter with their count, time and matched tokens, so a
wrong set is visible before Run. A light filter with no candidate is shown
as MISSING, not hidden.

## Loading a night for review

`FrameSelector.scan( folder )` enumerates one folder and cannot express a
night: a night is a filtered list of paths that may span `Plan` and
`Autorun`. So scanning gains an explicit-path entry point, `scanPaths( paths )`, and
the existing `scan( folder )` becomes a caller of it that enumerates the
folder first.

`scanPaths` carries the WHOLE of the existing contract, not just the
frames: the `reading` and `measuring` progress callbacks, event pumping,
the Cancel check, and the cancelled result shape. "Thin wrapper" must not
become "drops the progress bar and ignores Cancel" -- that would regress
ordinary folder scanning, which is the feature's most-used path.

The FITS header is authoritative for filter identity EVERYWHERE, for
lights and flats alike -- review grouping, flat matching and the export
manifest. Filenames are used only to narrow candidates cheaply.

Splitting the two would contradict itself: a light named `_H_` whose
header says `OIII` would be reviewed as OIII and paired with H flats.

So the filename filter is NEVER used to select or reject a flat, not even
to narrow the candidate list -- narrowing by a value the header may
contradict would drop flats that actually match, and no later header check
could get them back. The candidate set for a night is every flat in the
batches assigned to its session, with no filename filtering at all. Their
headers are then read -- a few dozen files, not a card -- and filter, bin,
camera and rotation are matched on header values. Filenames contribute
only the timestamp used for batching, which no header disagreement can
make wrong.

## Writing

In ASIAIR mode a destination is mandatory and Run stays disabled until one
is chosen. There is no same-folder case.

**The card is never written to.** This is enforced, not assumed:

- The destructive same-folder path (`convertInPlace`, delete-in-place) is
  unreachable in import mode. It is gated on the mode, not on a path
  comparison.
- The destination is rejected if its resolved path lies within the
  resolved path of the detected card root. String comparison of the two
  chosen directories is not enough: `/Volumes/ASIAIR/export` and a symlink
  pointing into the card both pass a naive test.
- Output paths are resolved before writing, so a pre-existing `Light` or
  `Flat` symlink in the destination cannot redirect a write onto the card.

Layout:

```
<dest>/Light/   approved lights, all filters
<dest>/Flat/    matched flats
```

**Approved** means: a frame not rejected by gating and not rejected by
hand, in a filter that has at least one such frame. Flats are copied for
exactly those filters -- not for filters whose lights were all rejected,
and not for filters that were never reviewed. Lights and flats are frozen
into one manifest at Run, so the flat set cannot drift from the light set
while the copy proceeds.

Two kinds of collision, resolved differently:

- **Two sources, one output name** -- `Plan` and `Autorun` both holding
  `Light_M42_..._0001.fit`. Overwrite cannot resolve this: whichever is
  written second wins and a frame is silently lost. This is REFUSED
  outright, regardless of the overwrite setting, and the colliding pairs
  are named.
- **An output name already on disk** from a previous import. Reported
  before any write, and written only if overwrite is chosen.

The output postfix is set to the empty string, not left at
SubframeSelector's `_a` default, and the same postfix is used to build the
expected-output mapping. Those two must agree or the accounting of what
was written is wrong.

Both are converted to XISF. Flats do NOT go through
`FrameSelector.runOutputRoutine`: that runs SubframeSelector routine 1
first ("measuring first is not optional"), which means star detection, and
a flat has no stars. Flats convert by open-and-save, with no measurement.

### Verification

Geometry alone does not establish a faithful import: a wrong frame or lost
metadata keeps the same dimensions. Each written file is reopened and
checked for matching geometry AND for the survival of the keywords WBPP
needs -- `FILTER`, `EXPTIME`, `GAIN`, `DATE-OBS`, and the CFA keywords
where present.

A file that fails verification is DELETED before the failure is reported.
Leaving it would block the retry, because the existing output path refuses
to write over an existing file unless overwrite is set. The card copy is
of course untouched.

## Structure

- `script/lib/AsiairNames.js` -- pure, no I/O: filename parsing, timestamp
  keys, session and night clustering, flat matching. Every rule that could
  be subtly wrong lives here, takes plain arrays and returns plain
  objects, and is covered by node tests in CI.
- `script/lib/Asiair.js` -- the I/O layer: `/Volumes` enumeration, shape
  detection, the depth-limited tree walk. PixInsight-only tests. It pumps
  events during the walk so the UI stays responsive, and it reports a card
  that disappears mid-walk as a removal rather than throwing.
- `script/lib/NightDialog.js` -- the target/night picker.
- `script/FrameSelector.js` -- import mode: forces a destination, and
  routes Run through the Light/Flat split.

The dialog, the conversion and the verification cannot be tested under the
node shim -- it refuses `ImageWindow` and the UI classes by design, and
rightly so. Those get PixInsight tests. The claim is only that the
clustering and matching rules are CI-covered, which is where the subtle
errors live.

## Accepted limits

Stated so they are decisions rather than oversights:

- **Verification cannot detect altered pixels.** Converting to XISF
  re-encodes, so the copy cannot be hashed against the card. Geometry plus
  header survival is the strongest check available under that choice.
- **A stalled mount can block detection.** The probe is two
  `File.directoryExists` calls per volume, which is as small as it gets,
  but PJSR offers no way to preempt a hung filesystem call. Detection
  pumps events and is cancellable between volumes, not inside one.
- **Filenames cannot establish calibration correctness.** Matching filter,
  bin, camera and rotation is the limit of what the names support.

## Explicitly out of scope

- Darks and bias frames.
- Writing to, reorganising or deleting anything on the card.
- Establishing calibration correctness beyond what filenames support.
- Platforms other than macOS.
