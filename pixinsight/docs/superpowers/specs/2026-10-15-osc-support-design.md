# Loom: one-shot colour (OSC) masters — design

**Date:** 2026-10-15
**Status:** revision 5: revision 4 after three adversarial review rounds, with the owner decisions recorded (colour runs take no mono masters; a light-pollution filter runs normally)

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
- **Colour runs are colour only.** A run that uses a colour master takes no mono
  master (L, R, G, B, H, S or O). Mixing colour and mono rigs is out of scope.
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

**Kind detection.** A colour master's kind says what its planes contain. Only
two kinds are supported: `broadband`, and `dualband`, which means specifically a
filter passing Hα (and, for quad-band, SII) in red and OIII (with Hβ) in green and
blue. A new `Util.oscFilterInfo( filterValue )` normalises like
`Util.channelFromFilter` (lowercase, punctuation and spacing removed) and returns
`{ kind, name, confident, haNm, oiiiNm }` or null. It looks names up in this
order:

1. **Known models**, matched on the full normalised name: for example L-eXtreme,
   L-Ultimate, L-Enhance, ALP-T Hα/OIII variants, NBZ. Each entry states its line
   pair and passbands (L-eXtreme 7 nm / 7 nm; L-Ultimate 3 nm / 3 nm; and so on).
   An entry whose line pair is not Hα/OIII (for example the ALP-T SII/Hβ variant)
   has `kind: "unsupported"`, and a run with it is refused with "this filter
   passes SII and Hβ; only Hα/OIII dual-band filters are supported".
2. **Broadband names**: uvir, ircut, lpro, cls, and an absent or empty `FILTER`
   give `broadband` with `confident: true`.
3. **Family aliases** (duo, dual, tri, triad, quad, alpt, and similar) give
   `dualband` with `confident: false`: the family name alone does not establish
   the line pair.

Anything else returns null.

**Override for every colour master.** The masters list shows every colour master
with a kind control: **Broadband** or **Dual-band Hα + OIII**. It is preset to
the detected kind when detection is confident, and left empty when detection is
not confident or returned null; a run with an empty kind is blocked with
"choose Broadband or Dual-band Hα + OIII for <file>". A filter whose model is
known to be unsupported cannot be overridden. The user's choice always wins over
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

**Mixed selections are a preflight error** (the run does not start). A
configuration with a colour input (`OSC` or `OSCNB`) and any mono input is
refused, naming the inputs:
"colour masters cannot be combined with mono masters; choose either the colour
masters or the mono masters". In folder discovery, when both colour and mono
winners are found, the masters list shows both and the run stays blocked until
the user clears one side.

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
- `O` is plane 1 (green) alone. Green and blue see different mixtures of OIII and
  Hβ through the sensor's Bayer filters, so the two planes are not proportional
  to each other in general, and no fixed or fitted combination is valid for every
  scene. Green carries two of a Bayer cell's four photosites and, on the usual
  sensors, a strong response at 500.7 nm. Blue is not used. Combining green and
  blue is left for a later design with a physically justified model.
- Hβ in green is carried into `O`. No crosstalk subtraction.

**Validation in two steps.**

- *Preflight* reads headers only: three channels of one size for a colour input.
- *First load* checks the pixels: a plane whose maximum equals its minimum
  (constant, including all zero) is refused, naming the plane. The result and
  the plane statistics (minimum, maximum, median per plane) are written to a
  sidecar in the cache directory, `<Cache.hash( group identity + "|validate" )>.osc.json`,
  so a run that hits the cache neither reopens the master nor loses the logged
  values: they are read from that file and logged again.

**Loading stays lazy.** `Pipeline.loadChannels` creates each colour group as
lazy channels that share one loader: the master is opened at most once per
run, the first time a sibling needs pixels, and not at all when every stage it
feeds is a cache hit. The loader owns the master's window and releases it once
every sibling has its working copy.

**Split planes are cached.** When the master is opened, each sibling's plane is
stored in the cache under its sibling source key (`Cache.store`), before any stage
runs on it. A sibling's `load()` first tries `Cache.load` of that key and opens
the master only on a miss. This covers consumers that need a sibling's pixels
without any cached stage of their own: a reference that needs no registration,
cropping, and an `OSCNB` channel with no narrowband stages. The rule for the
number of master opens is therefore dependency-based: the master is opened only
when some sibling's split plane is not in the cache.

**Identity and storage keys.** Identity strings are never used as file names.
Every key that names something in the cache is `Cache.hash( identity )`, so it
keeps the cache's hexadecimal-key convention and is a valid file name on every
platform.

- The group identity: `Cache.fingerprintFile( path )` (or the view fingerprint)
  plus `|osc:<kind>`.
- Each sibling's source key: `Cache.hash( group identity + recipe )`, the recipe
  being `|plane:0`, `|plane:1`, `|plane:2` for broadband, and `|plane:0` for `H`
  and `|plane:1` for `O`.
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
- SPFC and SPCC of the RGB composite (`spfcRGB`, `spccRGB`):
  `Pipeline.compositeInstrument` returns null for an `OSC` composite, which
  `Steps.deviceCurveForImage` already turns into the Ideal QE curve;
- the clean white-balance measurement (below).

**No camera inheritance.** `Pipeline.inheritInstrument` (which lends a camera name
to a mono master whose header lacks `INSTRUME`) does nothing in a colour run:
colour-group siblings neither give nor take a camera, since they use Ideal QE.

**A filter in front of the camera.** PixInsight cannot multiply a second curve
into the sensor curves, so a broadband filter in front (UV/IR, L-Pro, CLS) is not
part of the calibration. UV/IR cut filters pass the band the sensor curves cover,
so this is close. For a light-pollution filter (L-Pro, CLS and similar) the run
goes ahead normally; the run log notes that colour calibration does not model
that filter. Such runs are never refused.

**Missing curves.** A sensor name that is not in `filters.xspd` is a preflight
problem naming the missing curve, as a missing mono filter is today.

## Registration

**Reference policy.** A colour group supplies the reference deterministically:
`OSC`'s `G` when `OSC` is used, else `OSCNB`'s `H`. `Pipeline.registrationQuality`
is not consulted for colour groups (it measures by file path, which siblings
share).

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
  `Cache.chainKey( Cache.hash( sibling keys in plane order ), "registerGroup",
  { ref: <reference key>, planes: <mapping> } )`, stored as the registered
  three-channel image. Each registered sibling's key is
  `Cache.hash( group registration key + "|plane:<n>" )`. A change to any sibling's input changes the group key and
  re-registers the whole group (one transform drives all of them). A cache that
  holds the group image but not a sibling's split re-splits from it; a cache that
  lacks the group image re-registers.
- Halo match and crop then work as today on the registered logical channels.

**Clean white balance.** `Pipeline.measureCleanWhiteBalance` registers the
pre-correction R, G and B with `registerCleanChannel`, one at a time. For `OSC`
it uses the same grouped rule: identity when the reference is the `OSC` group's
`G`, else one grouped registration of the three clean planes, cached under
`cleanRegisterGroup` with the same key scheme.

The reference view is already cropped to the common area when the clean branch
runs (it is cropped in place), so clean registrations land in the cropped frame.
Their keys therefore depend on the crop as well as on the reference: the
`cleanRegisterGroup` parameters are `{ ref: <reference key>, crop:
Pipeline.cropTag( common ), planes: <mapping> }`. With the identity case, the
clean planes are cropped with the same `common` rectangle before measuring.

`Pipeline.cleanWhiteBalanceKey` adds three things to what it hashes: the
reference key, the sensor curve names and the QE curve name. Replacing the
reference (a replaced `OSC` master, say) with the crop unchanged, or changing the sensor,
therefore measures again. The reference key is added for mono runs too, so their
cached factors are measured once more after the upgrade.

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
- Palette availability follows logical channels: `OSCNB` gives HOO. SHO and HSO
  are never built from colour masters. Asking for SHO or HSO in a run with a
  colour master is a preflight error (the run does not start): "SHO and HSO need
  SII, which a colour camera cannot separate from Ha: choose HOO".
- Outputs carry their provenance: every exported channel and palette layer from a
  colour group gets the `LOOMSRC` keyword, and the run log names each palette's
  sources ("H from OSCNB L-eXtreme (Ha, plus SII for quad-band)").

## Pipeline: what does not change

Stages on native pixels (gradient removal, MGC, aberration correction), star
extraction, denoise, stretch and export run on derived channels as on mono
ones. Derived channels keep the names `R`, `G`, `B`, `Ha`, `OIII`, so exports,
PSB layers and the cache layout are unchanged. The run log adds one line per
colour master: kind, filter, how it was split, and the plane statistics.

## Dialog

- Colour masters appear in the masters list with their kind control (above) and
  the detected filter.
- While a colour master is selected, the mono channel boxes are greyed, with the
  reason (colour and mono masters cannot be combined).
- While a colour master is selected, the SHO and HSO palette boxes are unticked
  and greyed, with the reason ("needs SII, which a colour camera cannot separate
  from Ha"); a remembered SHO or HSO choice is not applied to a colour run.
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
  images in one sequence. The reference (a derived channel of the other group) is
  expanded to three identical channels with `rgbcomp`, a two-image colour
  sequence (reference, group image) is registered with `-layer=` set to the group's alignment plane
  (1 for `OSC`, 0 for `OSCNB`), and the registered group image is split back into
  its siblings. A reference group's siblings are not registered.
- **Calibration identity in the cache.** The Siril port has no per-channel SPFC
  stage whose key would carry the response, so the response enters the keys
  explicitly: a resolved calibration identity (the mode, OSC or mono, and every
  effective spectral argument: sensor, front filter, low-pass filter, or the mono
  sensor and the three filters) is added to `rgb_stage_params` (the composite
  SPCC stage) and to the clean white-balance key. Changing any one of them
  re-runs colour calibration, with sharpening on or off.
- **Narrowband SPCC** passes per-channel bandwidths (`-rbw`, `-gbw`, `-bbw`),
  which its command already supports.

## Testing

**Unit (PixInsight, through the repo's PJSR test shim):**

- `Util.oscFilterInfo`: known models before family aliases; the ALP-T Hα/OIII and
  SII/Hβ variants separately (supported, refused); family aliases not confident;
  unknown names; punctuation; absent `FILTER`.
- Storage keys: every generated cache basename (sibling keys, split keys, the
  validation sidecar) is hexadecimal.
- `Util.parseColourMasterName`: hyphenated filters, `_color` / `_RGB`, misleading
  names corrected by the header.
- Discovery: colour and mono winners in one folder (both listed, run blocked);
  unnamed colour files; a name-header disagreement.
- Overrides: precedence over detection, keyed per filter name and per path for
  blank filters, reset.
- `Util.suppliedChannels` and every consumer listed above, including `OSCNB`
  alone (HOO built).
- Preflight errors: a colour master with any mono master (both errors' messages
  checked, run not started); SHO or HSO requested with a colour master (error,
  run not started), including a remembered SHO choice.
- Dialog: SHO/HSO boxes and mono boxes greyed while a colour master is selected.
- Validation.
- The split: `O` equals the green plane whatever the blue plane holds (synthetic
  masters with independent Hβ and OIII structure, continuum and a background
  gradient); constant planes refused.
- Cache keys: sibling recipes, the group registration key, the clean
  registration key changing with the crop, the clean white-balance key changing
  with the reference and with the sensor.

**End to end (PixInsight), synthetic masters with a known shift between them:**

- siblings pixel-aligned after registration, in both the corrected and the clean
  white-balance branch;
- a changed overlap (a dual-band master covering less of the field) re-registers
  the clean group; a replaced `OSC` master with an unchanged crop re-measures the
  white balance;
- counting opens of the master file: one on a cold run, none on any later run
  while the split planes are cached (whatever later stage is invalidated), one
  after the split planes are removed from the cache; including `OSCNB` alone with
  narrowband gradient removal off;
- changing one sibling's input re-registers the group; deleting one sibling's
  split re-splits without re-registering;
- the composite uses the sensor curves and Ideal QE.

**Siril:** the logic pinned against values recorded from the JavaScript, plus
`siril`-marked tests in the Docker image: grouped registration against the other
group's derived reference (mixed channel counts), SPCC with an OSC sensor, warm-cache runs changing
each OSC spectral choice in turn (sensor, front filter, low-pass filter) with
sharpening on and off, and SPCC called OSC
then mono and mono then OSC in one session (separate runs), checking each call's
arguments.

## Out of scope

- Undebayered CFA masters, and extraction from the Bayer pattern.
- Crosstalk subtraction between Bayer channels.
- Combining the green and blue planes into `O`.
- Dual-band filters other than Hα/OIII (SII/Hβ and similar).
- More than one colour master of a kind in one run.
- Colour and mono masters in one run (mixed rigs).
- A synthetic luminance from RGB.
- Modelling a light-pollution filter's curve in calibration.
- OSC-specific changes to the Frame Selector or the ASIAIR import (both already
  group colour subs by `FILTER`).
