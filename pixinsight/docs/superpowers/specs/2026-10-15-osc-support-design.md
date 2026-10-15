# Loom: one-shot colour (OSC) masters — design

**Date:** 2026-10-15
**Status:** revision 2, after adversarial review round 1

## What this is for

Loom is built around seven mono channels (`L R G B H S O`, `Util.CHANNELS`).
Someone shooting with a one-shot colour camera has no mono masters: WBPP gives
them one debayered RGB master per filter, typically a broadband night (UV/IR or
light-pollution filter) and a dual-band night (L-eXtreme, L-Enhance, ALP-T and
the like) of the same target. Today Loom cannot take either.

This design lets Loom take those masters and run its whole pipeline on them:
the broadband master becomes the RGB composite, the dual-band master supplies
Ha and OIII for the narrowband palettes.

## Decisions

- **Debayered masters only.** Loom receives what WBPP writes by default: a
  three-channel RGB master. Undebayered CFA masters are out of scope.
- **Split at input.** A colour master is split into mono channels when it is
  loaded; from there on the existing per-channel pipeline runs. No second,
  colour-native pipeline.
- **One broadband and/or one dual-band colour master per run.** Several masters
  of one kind are integrated before Loom, in WBPP.
- **Quad-band is dual-band.** On a colour sensor Ha and SII both land in the red
  pixels and cannot be separated, so a quad-band filter is extracted exactly as a
  dual-band one, and its `H` carries SII as well (see Mixed passbands).
- **PixInsight first.** The Siril port follows once the PixInsight version is
  merged, as for the rest of Loom.

## Vocabulary

- **Physical inputs** are what the user selected: the seven mono slots and two
  colour slots, `OSC` (broadband colour) and `OSCNB` (dual-band colour), each a
  file path or an open view.
- **Logical channels** are what the pipeline processes: the seven channel keys.
  `OSC` supplies `R`, `G`, `B`; `OSCNB` supplies `H`, `O`.
- **A colour group** is the set of logical channels derived from one colour
  input. Its members are *siblings*.

## Inputs and detection

**A colour master is a three-channel image.** `Util.headerInfo` gains
`numberOfChannels` (from the file's image description, `d[0].numberOfChannels`,
still without reading pixels); a file or view with three channels is a colour
candidate and is never mapped to a mono slot. A file with any other channel
count than 1 or 3 is refused with its path and count.

**Kind detection.** A new `Util.oscFilterInfo( filterValue )` normalises like
`Util.channelFromFilter` (lowercase, punctuation and spacing removed) and returns
`{ kind, name, haNm, oiiiNm }` or null:

| kind | filters (matched by token) | passbands |
|---|---|---|
| `broadband` | uvir, ircut, lpro, cls, and an absent or empty `FILTER` | — |
| `dualband` | lextreme, lultimate, lenhance, alpt, nbz, duo, dual, tri, triad, quad | per filter, from a table in `Util` (e.g. L-eXtreme 7 nm / 7 nm; L-Ultimate 3 nm / 3 nm; L-Enhance 10 nm / 24 nm); unknown entries use the run's narrowband bandwidth |

Unknown names return null.

**Override for every colour master.** The masters list shows every colour master
with a kind control (Broadband / Dual-band), preset to the detected kind or
empty when detection returned null; a run with an empty kind is blocked with
"choose Broadband or Dual-band for <file>". The user's choice always wins over
detection. It is remembered keyed by the normalised filter name when the master
has one, and by the master's path when it has none, so one choice for an
unlabelled file never applies to another unlabelled file. A "detected" link
beside the control clears the remembered choice for that key.

**Discovery in a folder (`UI` scan).**

- `Util.parseMasterName` keeps its mono behaviour. A new
  `Util.parseColourMasterName( name )` reads the filter token after `FILTER-` up
  to the next underscore, hyphens included (`FILTER-L-eXtreme_color` gives
  `L-eXtreme`), and marks a name containing `_color` or `_RGB` as a colour
  candidate.
- Colour candidates are collected independently of the mono winners: every
  named colour candidate is confirmed against its header (three channels,
  `IMAGETYP` a master light), and unnamed files are header-read when either mono
  slot or colour slot has no named winner. Header reads never decode pixels.
- A name that says colour and a header that says one channel (or the reverse)
  is reported as a correction, as names and headers disagreeing are today; the
  header wins.
- Each colour slot keeps the best candidate by `Util.masterVariantRank`
  (drizzle, autocrop, then newest). Candidates whose kind is undetected still
  take part, and land in the list with an empty kind control.

**Open views.** A three-channel open view can be chosen for `OSC` or `OSCNB`,
with its kind from its `FILTER` keyword and the same override.

**Conflicts are preflight problems,** each naming both inputs:

- `OSC` excludes mono R, G and B;
- `OSCNB` excludes mono H and O;
- mono L and mono S are allowed beside either.

## Logical channels everywhere

Today several consumers inspect `config.paths` / `config.views` or dialog entry
channels directly. They switch to one helper, `Util.suppliedChannels( config )`,
which returns the logical channel keys a configuration supplies (mono slots
plus the members of each colour group). Every one of these uses it:

- `Util.validateSelection` ("nothing to do" accepts `OSC` or `OSCNB` alone);
- `Pipeline.narrowbandSupplied`, `Pipeline.wantedPalettes`,
  `Pipeline.reportSkippedPalettes`;
- `Pipeline.marsProblems` (derived R, G, B run MGC like mono ones);
- the dialog's palette and bandwidth visibility (`updatePaletteVisibility`), from
  the logical channels of the current entries;
- `Util.duplicateFileProblems`, which also checks a colour file against mono
  slots.

Physical inputs stay in `config.paths` / `config.views` under the new keys `OSC`
and `OSCNB`, plus `config.oscKinds` (the effective kind of each) and
`config.oscSensor` (below).

## Splitting

**Broadband (`OSC`):** planes 0, 1, 2 become `R`, `G`, `B`.

**Dual-band (`OSCNB`):**

- `H` is plane 0.
- `O` combines planes 1 and 2. Plane 2 is first brought to plane 1's scale with
  PixInsight's LinearFit (reference: plane 1; default rejection limits), so a
  difference in throughput or scaling between the two is removed. Then
  `O = ( 2 * G + B' ) / 3`. The 2:1 weights follow the sensor's photosites: a
  Bayer cell has two green and one blue, and the debayered planes do not carry
  independent noise, so a per-plane noise estimate is not used for weighting.
  This is a fixed, documented heuristic, not an optimal estimator.
- If LinearFit fails or returns a non-finite or non-positive slope, `O` is plane
  1 alone, with a warning that names the master.
- Hβ, which falls in blue, is carried into `O`. No crosstalk subtraction.

**Validation in two steps.**

- *Preflight* reads headers only: three channels of one size for a colour input.
- *First load* checks the pixels: a plane whose maximum equals its minimum
  (constant, including all zero) is refused, naming the plane. The result is
  written to the cache beside the derived channels (`<group key>.osc.json`: the
  validation result, the LinearFit slope and offset, and the plane statistics
  used), so a run that hits the cache neither reopens the master nor loses the
  logged values: they are read from that file and logged again.

**Loading stays lazy.** `Pipeline.loadChannels` creates each colour group as
lazy channels that share one loader: the master is opened at most once per
run, the first time a sibling needs pixels, and not at all when every stage it
feeds is a cache hit. The loader owns the master's window and releases it once
every sibling has its working copy.

**Identity.**

- The group's source key: `Cache.fingerprintFile( path )` (or the view
  fingerprint) plus `|osc:<kind>`.
- Each sibling's source key: the group key plus its recipe: `|plane:0`,
  `|plane:1`, `|plane:2` for broadband; `|plane:0` for `H`, and `|o:2g+lf(b)/3`
  for `O`.
- Each derived working image carries `FILTER` set to its channel and a
  `LOOMSRC` keyword: source file name and recipe.

## Calibration response

The camera's Bayer filters are the colour filters. PixInsight's filter database
(`filters.xspd`, the list SPCC shows) holds per-camera colour-sensor curves (for
example the `Sony Color Sensor` R, G and B curves), and these already include the
sensor's response. So for every target that comes from `OSC`:

- the filters are the three curves of the chosen sensor, `config.oscSensor` =
  `{ R, G, B }` names, picked in the dialog from the R/G/B colour-sensor curves in
  `filters.xspd` (the "Sensor colour filters" choice);
- the QE curve is the Ideal QE curve, never the camera's: the sensor curves carry
  the response already, and multiplying a camera QE in would count it twice.

This applies to:

- per-channel SPFC of derived `R`, `G`, `B` (`Pipeline.broadbandStages` passes the
  sensor curve as the channel's filter and Ideal QE as its QE; both are already
  in the `spfc` stage's cache parameters, so the key changes with the sensor);
- SPFC and SPCC of the RGB composite (`spfcRGB`, `spccRGB`), whatever camera a
  mono L came from: `Pipeline.compositeInstrument` returns null for an `OSC`
  composite, which `Steps.deviceCurveForImage` already turns into the Ideal QE
  curve;
- the clean white-balance measurement (below).

A mono L keeps its own camera and filter for its own SPFC, as today.

**A filter in front of the camera.** PixInsight cannot multiply a second curve
into the sensor curves, so a broadband filter in front (UV/IR, L-Pro, CLS) is not
part of the calibration. UV/IR cut filters pass the band the sensor curves cover,
so this is close. For a light-pollution filter (L-Pro, CLS and similar) the run
goes ahead and both the log and the closing summary say colour calibration does
not model that filter. **Owner decision needed:** accept this warning-only
behaviour (the default in this revision), or refuse such runs.

**Missing curves.** A sensor name that is not in `filters.xspd` is a preflight
problem naming the missing curve, as a missing mono filter is today.

## Registration

**Reference policy.** With mono L present, L is the reference, as today. Without
it, a colour group supplies the reference deterministically: `OSC`'s `G` when
`OSC` is used, else `OSCNB`'s `H`; mono S is never the reference beside a colour
group. `Pipeline.registrationQuality` is not consulted for colour groups (it
measures by file path, which siblings share).

**Siblings share one transform.** Registering siblings separately would resample
each slightly differently and leave colour fringes. So:

- The reference's own group needs no registration: its siblings are on the
  reference grid already (identity).
- Any other group is registered as one image: its siblings (after their
  per-channel stages) are recombined into a temporary three-channel image,
  `(R, G, B)` for `OSC`, `(H, O, O)` for `OSCNB`, aligned to the reference with
  one `Steps.register` call (StarAlignment's own detection on that image), and
  split again into the registered siblings. The temporary windows are registered
  with the run's window registry and released after the split.
- **Cache.** The grouped registration's key is
  `Cache.chainKey( hash( sibling keys in plane order ), "registerGroup",
  { ref: <reference key>, planes: <mapping> } )`, stored as the registered
  three-channel image. Each registered sibling's key is that key plus
  `|plane:<n>`. A change to any sibling's input changes the group key and
  re-registers the whole group (one transform drives all of them). A cache that
  holds the group image but not a sibling's split re-splits from it; a cache that
  lacks the group image re-registers.
- Halo match and crop then work as today on the registered logical channels.

**Clean white balance.** `Pipeline.measureCleanWhiteBalance` registers the
pre-correction R, G and B with `registerCleanChannel`, one at a time. For `OSC`
it uses the same grouped rule: identity when the reference is the `OSC` group's
`G` (the crop then applies as for the corrected channels), else one grouped
registration of the three clean planes, cached under `cleanRegisterGroup` with
the same key scheme. `Pipeline.cleanWhiteBalanceKey` adds the sensor curve names
and the QE curve name to what it hashes, so changing the sensor re-measures.

## Mixed passbands

`H` and `O` from a colour master are not single lines: `O` carries Hβ beside
OIII, and a quad-band `H` carries SII beside Ha. Narrowband SPCC
(`Steps.spccNarrowband`) models each output channel as one line with one
bandwidth.

- For palettes built from `OSCNB`, narrowband SPCC keeps the line wavelengths
  (Ha 656.3 nm, OIII 500.7 nm) and takes each channel's bandwidth from the
  dual-band filter table (`haNm`, `oiiiNm`), else the run's narrowband
  bandwidth. `Steps.spccNarrowband` gains per-channel bandwidths for this; mono
  runs pass the single bandwidth to all three, as today. The result is an
  approximation and the log says so.
- Palette availability follows logical channels: `OSCNB` alone gives HOO; SHO and
  HSO need a mono S master and are otherwise reported as skipped.
- **Quad-band with mono S.** The `H` of a quad-band master contains SII, so an SHO
  or HSO palette would show SII in two channels. **Owner decision needed:** build
  those palettes with a warning (the default in this revision), or skip them.
- Outputs carry their provenance: every exported channel and palette layer from a
  colour group gets the `LOOMSRC` keyword, and the run log names each palette's
  sources ("H from OSCNB L-eXtreme (Ha, plus SII for quad-band)").

## Pipeline: what does not change

Stages on native pixels (gradient removal, MGC, aberration correction), star
extraction, denoise, stretch and export run on derived channels as on mono
ones. Derived channels keep the names `R`, `G`, `B`, `Ha`, `OIII`, so exports,
PSB layers and the cache layout are unchanged. The run log adds one line per
colour master: kind, filter, how it was split, the LinearFit values.

## Dialog

- Colour masters appear in the masters list with their kind control (above) and
  the detected filter.
- The channel boxes a colour master replaces (R, G, B or H, O) are greyed, with
  the reason.
- While `OSC` is used, the SPCC section shows "Sensor colour filters" (the R, G, B
  sensor curves) in place of the R, G and B filter boxes.
- Every new option is remembered between runs, like the existing ones.

## Siril port

Once the PixInsight version is merged, the same functions are ported to Python
in `siril/loomsiril/loom/` with a parity-table row each. Siril-specific points:

- **SPCC.** Every `spcc` call passes its full set of spectral arguments, because
  Siril keeps omitted ones from the previous call: for `OSC`,
  `-oscsensor=<name>` together with `-oscfilter=<name>` (the front filter, or
  Siril's no-filter entry) and `-osclpf=<name>` (the sensor's IR/low-pass entry,
  or none); for mono, the existing `-monosensor` and `-r/-g/-bfilter` set. The
  adapter (`siril_ops.py`) gains an OSC variant of its SPCC command builder and
  name checks.
- **Registration of a colour group.** Siril cannot mix one- and three-channel
  images in one sequence. The reference (mono or derived) is expanded to three
  identical channels with `rgbcomp`, a two-image colour sequence (reference,
  group image) is registered with `-layer=` set to the group's alignment plane
  (1 for `OSC`, 0 for `OSCNB`), and the registered group image is split back into
  its siblings. A reference group's siblings are not registered.
- **Narrowband SPCC** passes per-channel bandwidths (`-rbw`, `-gbw`, `-bbw`),
  which its command already supports.

## Testing

**Unit (PixInsight, through the repo's PJSR test shim):**

- `Util.oscFilterInfo`: known names, unknown names, punctuation, absent `FILTER`.
- `Util.parseColourMasterName`: hyphenated filters, `_color` / `_RGB`, misleading
  names corrected by the header.
- Discovery: colour candidates beside mono L and S winners; unnamed colour files;
  a name-header disagreement.
- Overrides: precedence over detection, keyed per filter name and per path for
  blank filters, reset.
- `Util.suppliedChannels` and every consumer listed above, including `OSCNB`
  alone (HOO built, SHO reported) and `OSCNB` + S.
- Conflicts and validation.
- The `O` combination: unequal plane scaling (result independent of B's scale),
  LinearFit failure fallback, constant planes refused.
- Cache keys: sibling recipes, the group registration key, the clean
  white-balance key changing with the sensor.

**End to end (PixInsight), synthetic masters with a known shift between them:**

- siblings pixel-aligned after registration, in both the corrected and the clean
  white-balance branch;
- counting opens of the master file: one on a cold run, none on a warm run, one
  when only a later stage is invalidated;
- changing one sibling's input re-registers the group; deleting one sibling's
  split re-splits without re-registering;
- `OSC` with a mono L from another camera: the composite uses the sensor curves
  and Ideal QE.

**Siril:** the logic pinned against values recorded from the JavaScript, plus
`siril`-marked tests in the Docker image: grouped registration with a mono
reference (mixed channel counts), SPCC with an OSC sensor, and SPCC called OSC
then mono and mono then OSC in one session, checking each call's arguments.

## Out of scope

- Undebayered CFA masters, and extraction from the Bayer pattern.
- Crosstalk subtraction between Bayer channels.
- More than one colour master of a kind in one run.
- A synthetic luminance from RGB.
- Modelling a light-pollution filter's curve in calibration.
- OSC-specific changes to the Frame Selector or the ASIAIR import (both already
  group colour subs by `FILTER`).
