# Loom: one-shot colour (OSC) masters — design

**Date:** 2026-10-15
**Status:** approved in discussion; awaiting review of this written form

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
  loaded; from there on the existing per-channel pipeline runs unchanged. No
  second, colour-native pipeline.
- **One broadband and/or one dual-band colour master per run.** Several masters
  of one kind are integrated before Loom, in WBPP.
- **Quad-band is dual-band.** On a colour sensor Ha and SII both land in the red
  pixels and cannot be separated, so a quad-band filter is extracted exactly as a
  dual-band one. SHO and HSO need a separate mono SII master.
- **PixInsight first.** The Siril port follows once the PixInsight version is
  merged, as for the rest of Loom.

## Inputs and detection

**A colour master is a three-channel image** (`isColor`). It is never mapped to
one of the seven channels directly.

**Its kind comes from `FILTER`,** through a new `Util.oscKindFromFilter`, with
the same normalisation as `Util.channelFromFilter` (lowercase, punctuation and
spacing removed). It returns:

| kind | filters (examples, matched by token) |
|---|---|
| `broadband` | no `FILTER`, UV/IR, IR-cut, L-Pro, CLS and similar |
| `dualband` | L-eXtreme, L-Ultimate, L-Enhance, ALP-T, NBZ, names containing duo, dual, tri, triad, quad |
| `null` | anything else |

**WBPP names.** `Util.parseMasterName` already reads `FILTER-xxx`; WBPP writes
`_color` (or `_RGB`) where a mono master has `_mono`, which marks a colour
candidate before any header is read. The header stays authoritative.

**Two new slots** sit beside the seven channels: `OSC` (broadband colour) and
`OSCNB` (dual-band colour). `Util.selectMasters` picks the best of each by the
same rank as today (drizzle, autocrop, newest).

**Conflicts are preflight problems,** each with a message naming both inputs:

- `OSC` excludes mono R, G and B (both would feed the composite).
- `OSCNB` excludes mono H and O (both would feed the palettes).
- Mono L and mono S are allowed beside either.

**An unknown filter** appears in the masters list as "colour master, filter
`<name>`" with a Broadband / Dual-band choice. The choice is remembered per
filter name, so it is asked once.

**Nothing to do** (`Util.validateSelection`) now also accepts `OSC` or `OSCNB`
alone.

## Splitting

**Broadband (`OSC`):** the three planes become channels `R`, `G`, `B`.

**Dual-band (`OSCNB`):**

- `H` is the red plane.
- `O` is the green and blue planes combined with inverse-variance weights,
  `w = 1/σ²` per plane, σ from the MRS noise estimate Loom already uses, so the
  noisier plane counts less. Hβ, which falls in blue, is carried into `O`; this
  is normal for OSC dual-band data and is documented in the README.
- No crosstalk subtraction (Ha leaking into green, OIII into red) in this
  version.

**Loading stays lazy.** `Pipeline.loadChannels` creates the derived channels as
lazy channels sharing one loader for their master: the file is opened once, the
first time any of its channels needs pixels, and not at all on a full cache hit.

**Identity.** Each derived channel's source key is the master's file fingerprint
(`Cache.fingerprintFile`) plus a recipe tag (`osc:R`, `osc:G`, `osc:B`,
`oscnb:H`, `oscnb:O:ivw`), so re-runs are cached as with mono masters and a
recipe change invalidates only the channels it affects. Each derived working
image carries `FILTER` set to its channel and a `LOOMSRC` keyword naming the
source master and recipe.

**Refused in preflight:** a "colour" master that is not exactly three channels of
one size, and a plane that is entirely zero (a broken debayer), naming the plane.

## Pipeline changes

Stages on native pixels (gradient removal, deconvolution, aberration
correction), star extraction, denoise, stretch and export run on derived
channels exactly as on mono ones. Five places change.

1. **Siblings share one registration.** Channels split from one master already
   share a pixel grid; registering them separately would resample each slightly
   differently and leave colour fringes.
   - If the registration reference comes from a colour master, its siblings get
     the identity.
   - Any other colour master is aligned once, on its best-star channel (G for
     broadband, H for dual-band), and that single transform is applied to all its
     channels. In PixInsight the derived channels are recombined into a temporary
     RGB image, aligned with one StarAlignment, and split again.
   - Halo match and crop then work as today.
2. **Registration reference.** `Pipeline.registrationReference` keeps its rules;
   derived channels compete like mono ones (`OSC` alone gives the derived G a
   chance, for example). Mono L, when present, still wins.
3. **SPCC for broadband OSC** uses the camera's Bayer RGB response, not three
   separate filters. While `OSC` is used, the SPCC section shows a "Sensor colour
   filters" choice from SPCC's OSC curve list in place of the R, G and B filter
   boxes; any filter in front of the camera is combined with those curves, as
   SPCC does. The clean white-balance measurement (`Pipeline.measureCleanWhiteBalance`,
   on channels the aberration correction never touched) is unchanged.
4. **Palettes.** `OSCNB` supplies H and O, so HOO runs. SHO and HSO need S and
   run only with a mono S master; otherwise `Pipeline.reportSkippedPalettes`
   reports the missing S. Narrowband normalisation and "Narrowband with GraXpert"
   apply to the derived H and O as usual.
5. **Outputs and logs.** Derived channels keep the names `R`, `G`, `B`, `Ha`,
   `OIII`, so exports, PSB layers and the cache layout are unchanged. The run log
   adds one line per colour master: kind, filter, the split, and the OIII weights.

## Dialog

- The masters list shows colour masters with their kind: "OSC broadband" or
  "OSC dual-band (L-eXtreme)", and the Broadband / Dual-band choice for an
  unknown filter.
- The channel boxes a colour master replaces (R, G, B or H, O) are greyed, with
  the reason.
- The SPCC section switches to "Sensor colour filters" while `OSC` is used.
- Every new option is remembered between runs, like the existing ones.

## Siril port

Once the PixInsight version is merged, the same functions are ported to Python
in `siril/loomsiril/loom/` (`osc_kind_from_filter`, the split, the OIII weights,
sibling registration) with a parity-table row each. Siril's SPCC already takes
OSC sensor curves (`spcc -oscsensor=`), so the "Sensor colour filters" choice maps
onto it directly. Sibling registration is one registration of the three-channel
image.

## Testing

- **Unit (PixInsight, via the repo's PJSR test shim):** the filter table
  (known, unknown, punctuation), WBPP name parsing for `_color`, slot selection
  and conflicts, validation, the inverse-variance weights, and the recipe cache
  keys.
- **End to end (PixInsight):** synthetic colour masters, one broadband and one
  dual-band with a known shift between them: siblings pixel-aligned after
  registration, HOO produced, SHO reported as skipped, a second run fully cached.
- **Siril:** the same logic pinned against values recorded from the JavaScript,
  plus `siril`-marked tests in the Docker image for three-channel registration and
  SPCC with an OSC sensor.

## Out of scope

- Undebayered CFA masters, and extraction from the Bayer pattern.
- Crosstalk subtraction between Bayer channels.
- More than one colour master of a kind in one run.
- A synthetic luminance from RGB.
- OSC-specific changes to the Frame Selector or the ASIAIR import (both already
  group colour subs by `FILTER`).
