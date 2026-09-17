# Loom

## Introduction

Loom does the boring part.

Between a stack of integrated masters and the point where you start making
pictures there is a long stretch of mechanical work: solving, flux
calibration, gradient removal, registration, cropping, combination, colour
calibration. None of it is a matter of taste, all of it has to be right, and
doing it by hand is an hour of clicking that produces the same answer every
time. Loom runs that stretch and hands back open windows.

**It is for people who already know PixInsight.** Loom is not a wizard and
does not explain what SPFC is — it assumes you would be doing all of this by
hand and would rather not. What it deliberately does not do is give you a
hundred knobs: the options are few, each one is a decision only you can make,
and everything else is derived. Fewer settings, a workflow that runs
start-to-finish unattended, and the hour you would have spent on mechanical
preparation left over for the part that is actually interesting.

**It makes no artistic choices.** Every parameter it chooses is either
measured from your data or fixed by a published convention, and the places
where a human judgement would normally enter are either computed or left to
you afterwards in Photoshop. Where it cannot decide something on evidence, it
asks, or it does nothing.

**It preserves signal.** Nothing is clipped that is not a single-pixel defect.
Calibration is photometric where photometry applies and deliberately absent
where it does not. The stretch uses the image's own black point and sends its
own sky to a fixed level, so no range is spent on emptiness.

**It supports the XT and SyQon tools** — BlurXTerminator, StarXTerminator,
NoiseXTerminator, SyQon Parallax, SyQon Prism, SyQon Starless — and offers
only the ones your installation can actually run.

**It hands off to Photoshop.** Results can be written as 16-bit TIFFs and as a
single layered PSB with the plates already stacked, blended and clipped the way
they are meant to be used — palette at the bottom, stars screened on top,
adjustment layers in place. Everything is tagged ProPhoto RGB so nothing shifts
colour on the way across.

**It is opinionated.** The steps and their order are not configurable, because
the order is the part that is easy to get wrong and expensive to get wrong:
aberration correction belongs on native pixels before registration; colour
calibration belongs before sharpening; noise reduction belongs after. You
choose which tools run and how hard, not when.

Results stay as open windows. Loom never writes to disk — saving is your
decision, made once you have looked at the result.

## Installation

**Get the code.** Clone the repository:

```
git clone https://git.local.carucci.studio/francesco/Loom.git ~/PixInsight/scripts/Loom
```

or from the GitHub mirror:

```
git clone git@github.com:fcarucci/Loom.git ~/PixInsight/scripts/Loom
```

`~/PixInsight/scripts/Loom` is the conventional location, but any location
works — nothing in the code depends on the path.

**Register it with PixInsight.** **Script → Feature Scripts → Add**, point it
at the **Loom folder** — not the `script` subfolder — then **Done**. The scan
descends into subfolders, which is how PixInsight's own bundled scripts are
registered two levels below `src/scripts`, so the top-level folder is enough to
find `script/Loom.js`. It then appears under **Script → Batch Processing →
Loom**.

**Moving or renaming the folder breaks the registration.** PixInsight registers
feature scripts by absolute file path, so after a move you have to add it again
from its new location.

**Removing a stale entry.** The Feature Scripts dialog has no Remove button —
its buttons are Add, Regenerate, Enable All, Disable All, Done, Cancel. The
checkbox is the removal mechanism, as the dialog itself says: "Enabled scripts
will be featured on the Script menu; disabled ones will be removed." Untick the
stale entry and click **Done**. **Regenerate** rescans the registered folders
and rebuilds the list, discarding entries whose file no longer exists. A stale
entry comes up already unticked, because PixInsight disables scripts whose file
it cannot find.

**Optional tools are detected, never required.** Loom offers only what the
installation can actually run.

| kind | tools | how they are found |
|---|---|---|
| **Modules** | BlurXTerminator, StarXTerminator, NoiseXTerminator, GraXpert, StarNet2 | by name |
| **External binaries** | SyQon Parallax (`parallax_cli`), SyQon Prism (`prism_cli`), SyQon Starless (`SyQonStarless`) | a path remembered in Loom's settings, else the config file SyQon's own scripts write, else a scan of `/Applications` and `~/Applications` two levels deep |

Whatever the search finds is remembered, so it normally runs once. The
remembered path is what makes this reliable: SyQon's config files live in the
system temp directory, which the OS is free to purge — and did, which is what
made those tools silently disappear from the dropdowns until Loom kept its own
record.

**It keeps itself current.** With **Update Loom automatically** ticked, each
launch spawns a background `git` update and opens the dialog immediately —
nothing is waited on. PJSR resolves `#include` when the script is parsed, so an
update could never apply to the run that fetched it; the new version is used the
*next* time you start Loom.

A checkout with local changes is never touched, a diverged branch is refused
rather than merged, and a failed update is reported in the Process Console at
the following launch. The version and commit are in the dialog's title bar —
`Loom 0.1 (a4c1f2e)` — because many commits share one version number.

**Verify the install.** Open **Script → Batch Processing → Loom**, add masters,
and tick **Validate only (check everything, run nothing)**. It runs every
preflight check — files and views present, required FITS keywords, installed
processes, the MARS database — and executes nothing. That is the intended first
run on a new setup; see **Requirements** for what those checks expect to find.

`script/selftest.js` is not part of installation and does not need registering.
It writes its results to a file and is run from the command line, not the menu.

## How to use

**Add masters**, by any of three routes:

| | |
|---|---|
| **Add Files...** | pick masters directly |
| **Scan Masters Folder...** | newest master per filter, preferring drizzled and autocropped variants |
| **Add Open Views** | every open view carrying a `FILTER` keyword |

The channel comes from the file's `FILTER` keyword. A view selection beats a
file path for the same channel.

**Name the project** in the box at the top. It is filled in from the folder your
masters came from and follows the file list until you type something of your
own; it names the exported PSB.

**Set the options** (all described below), then **Run**. A Cancel window stays
up for the duration and stops at the next checkpoint; the Process Console
stays open throughout, one green line per operation.

**On a new dataset, tick "Validate only" first.** It runs every preflight
check — files and views present, required keywords, installed processes, the
MARS database — and executes nothing.

**Caching.** Every stage is cached, keyed on its inputs and parameters, so a
repeat run does no pixel work. Change one setting and only the affected stage
and those after it recompute. **Ignore cache for this run** forces a recompute
without discarding anything; **Clear cache** discards it.

Set **Cache folder** to keep it somewhere with room — a fast external volume,
say — rather than the system temp directory, which the OS is free to purge. A
fully cached run reads no masters at all: the sources are opened lazily and
skipped entirely when every stage that needs them is already cached.

**Run logs.** Every run writes the whole Process Console to
`<cache>/logs/loom-run-<timestamp>.log`, including the runs that fail — which
are the ones anyone wants to read. They live in a subfolder, so **Clear cache**
does not delete them.

**Saved instances.** Drag the script's instance icon to the workspace to reuse
a configuration. Only file-path selections round-trip; a view id from a
previous session has no guaranteed meaning later.

## Process

### Per channel, on native uninterpolated pixels

| step | what | options |
|---|---|---|
| **Solve** | plate solution, skipped if one is already present | — |
| **SPFC** | spectrophotometric flux calibration. Broadband only | filter curve per L/R/G/B; camera read from `INSTRUME` |
| **MGC** | MultiscaleGradientCorrection against the MARS reference. Broadband only | MARS folder, asked for only if PixInsight does not already know one |
| **GraXpert** | background extraction. Broadband only — never narrowband | on/off, smoothing |
| **Aberration** | star-shape correction, before registration so resampling cannot spread it | None, BlurXTerminator, SyQon Parallax |

### Across channels

| step | what | options |
|---|---|---|
| **Register** | everything to L; L is the reference and is never resampled | — |
| **Crop** | to the area every channel actually covers | — |
| **Halo match** | matches each channel's PSF to the widest, L excluded | on/off |
| **White balance reference** | measured on channels the aberration correction never touched, because corrected photometry gives a wrong balance | automatic |

### RGB composite

| step | what | options |
|---|---|---|
| **Combine** | R, G, B | — |
| **Solve, SPFC, SPCC** | calibration of the composite | filter curves |
| **Sharpen** | star reduction and detail, on the finished composite with colour linked | star reduction None/Low/Medium/High, detail None/Low/Medium/High |
| **Extract stars** | splits into starless and stars | None, StarNet2, StarXTerminator, SyQon Starless |
| **Stretch** | see below | on/off |
| **Denoise** | last, after the stretch | None, NoiseXTerminator, SyQon Prism; strength Low/Medium/High |

### Narrowband palette

Built independently of RGB; either can be produced without the other.

| step | what | options |
|---|---|---|
| **Combine** | channels mapped to R, G, B by palette | SHO, HOO, HSO |
| **SPCC narrowband** | emission-line calibration by wavelength and bandwidth | bandwidth in nm |
| **Normalise** | a neutral NarrowbandNormalization pass | on/off |
| **Sharpen, extract, stretch, denoise** | as for RGB | as above |

No SPFC and no broadband SPCC: a palette is an aesthetic mapping of emission
lines onto RGB, not a photometric rendition.

### Stretch

Off by default, and there are two methods.

**Histogram (deterministic MTF)** is Loom's own. Each plate gets one
`HistogramTransformation` computed from that plate alone — black point at its
darkest level that is not a single-pixel defect, midtone placing its sky median
at a fixed target. Nothing to set, and the same input always gives the same
output.

**MultiscaleAdaptiveStretch** hands the plate to the process of that name, with
target background 0.15, aggressiveness 0.70, dynamic range compression 0.40 and
contrast recovery on at full intensity. Scale separation is left at the
process's own default. A saved MultiscaleAdaptiveStretch process icon, if you
have made one, is used instead — so the way to change these numbers is to make
an icon, not to edit the script.

Starless plates are stretched after extraction. The stars plate is stretched
*before* it, at a different target chosen to maximise the separation between
faint stars and sky. The two therefore carry independent transforms so each
looks right alone — and will **not** screen back together into the original.

See `docs/superpowers/specs/2026-09-15-stretch-design.md` for the derivation
and for the alternatives that measurement rejected.

### Export

Optional. Set a folder in **Export 16-bit TIFFs to:** and every result is
written there as a 16-bit TIFF named after its window — `RGB_starless.tif`,
`RGB_stars.tif`, and so on. Leave it empty and nothing is written.

The plates stay 32-bit float in the workspace; the conversion happens on a
throwaway copy, so exporting never degrades what is on screen.

Export requires the stretch. A linear plate keeps all its signal in the bottom
fraction of a percent of the range, and quantising that to 16 bits posterises
it — so Loom writes nothing and says why, rather than producing a file that
looks fine in a listing and is ruined on open.

#### Colour profiles

Every exported file carries a profile, and so does every plate left in the
workspace: **ROMM RGB** — colorimetrically ProPhoto RGB — for colour, and
Generic Gray for mono. The gamut is wide enough to hold saturated emission-line
colour that sRGB clips outright.

The PSB embeds Adobe's own `ProPhoto.icm` bytes where they are installed,
because Photoshop matches its working space by profile *name*: ROMM RGB and
ProPhoto RGB are the same space, and Photoshop will still offer to convert
between them.

#### Frequency separation

**Frequency-separate the L stars plate** splits it into `L_stars_low` and
`L_stars_high` using a Gaussian sized from the plate's own measured star width.
This is exactly Photoshop's Apply Image method — the high layer is
`(original − low) / 2 + 0.5`, recombining through Linear Light — so star cores
and their halos can be retouched separately.

#### The layered PSB

**Also write one layered `<project>.psb`** assembles everything into a single
Photoshop Large Document, bottom to top:

| layer | |
|---|---|
| **HSO** group | the palette starless plate, with **Ha**, **SII** and **OIII** Curves layers above it, each already set to the channel that line was mapped to |
| **RGB** group | the broadband starless plate, hidden — switch it on when you want it |
| **Stars** group, *Screen* | RGB stars, and the L stars plate in *Luminosity*; with frequency separation on, that becomes an **L Stars** group holding the low layer and the high layer in *Soft Light* |
| **Stars Curve**, **Stars Saturation** | clipped to the stars, so they work on the stars as they come out of the Screen blend and leave everything beneath alone |

PSB rather than PSD because uncompressed 16-bit layers of a modern sensor's
frame run to about 3 GB and PSD stops at 2. Expect the write to take a minute.

The Curves layers open on RGB in Photoshop's panel whatever the file says —
that is Photoshop's own state, not something a file can set — so each layer's
name carries its channel: `Ha[R]`, `SII[G]`, `OIII[B]`, and `OIII[G,B]` for HOO
where one line feeds two channels.

### Outputs

With star extraction on: `L_starless` + `L_stars`, `RGB_starless` +
`RGB_stars`, and `<palette>_starless`. Narrowband stars are kept only when the
run produced no RGB composite, since the broadband stars are the ones you
would recombine against. The unsplit plates are not kept — starless and stars
reconstruct them.

With it off: `L`, `RGB`, `<palette>`, and the narrowband channels themselves
when no palette was built.

## Requirements

PixInsight 1.9.4 or later. Developed and run on macOS. It is written to run on
Windows as well, but it has never been run on one — treat that as untested
rather than as supported.

The camera is read from the `INSTRUME` keyword, not assumed.
`Util.qeCurveNameForCamera` maps it to one of PixInsight's QE curves — the
IMX571 family (ASI2600/6200/533/094, QHY268/600), IMX178, IMX183, IMX492,
IMX585, MN34230 and the KAF sensors. An unrecognised camera falls back to the
Ideal QE curve rather than calibrating against the wrong sensor.

MGC needs at least one MARS database (`*.xmars`). No path is assumed: Loom
uses the folder set in its own dialog, else a saved
`MultiscaleGradientCorrection` process icon, else what the MGC interface has
persisted. The dialog asks only when the last two come up empty. Preflight
fails loudly if no route yields a database on disk.

Optional tools are detected, and only what is installed is offered.
