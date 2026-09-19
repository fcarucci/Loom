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
calibration belongs before sharpening; noise reduction belongs wherever its
tool was designed to work. You choose which tools run and how hard, not when.

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
launch checks for a newer Loom before the dialog opens and says what it found.
If there is one it is fast-forwarded and **Loom restarts itself** — PJSR
resolves `#include` when the script is parsed, so an update cannot apply to the
run that fetched it, and the dialog opens from the updated copy instead.

The check is quick — a few hundred milliseconds against a local server — and
gives up after fifteen seconds, so an unreachable one is a pause rather than a
hang.

A checkout with local changes is never touched, a diverged branch is refused
rather than merged, and anything that goes wrong is named in the Process
Console rather than passed over in silence. What each attempt did is recorded
in `<cache>/update/update.log`, beside the run logs and safe from **Clear
cache**. The version and commit are in the dialog's title bar —
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
| **Scan Masters Folder...** | newest master per filter, preferring drizzled and autocropped variants |
| **Add Files...** | pick masters directly |

Scanning a folder is listed first because it is how a run normally starts: WBPP
writes a masters folder and Loom picks the best variant per filter out of it.
Adding files by hand is the exception.

The channel comes from the file's `FILTER` keyword. A view dropped on the list
still works and beats a file path for the same channel, but there is no longer a
button to add every open view at once — it added whatever happened to be on the
workspace, which is rarely what a run wants.

**What the list tells you.** Beside the filter, size and drizzle factor:

| column | |
|---|---|
| **Created** | when the master was stacked — a folder of restacks is otherwise distinguished only by a `(3)` in the name |
| **FWHM**, **Ecc**, **Noise**, **Stars** | measured by SubframeSelector, each with its change against the **previous integration of the same channel** |

Loom always uses the newest master — that is what a re-stack is for — but a
newer stack is not automatically a better one, and the columns say so before the
run rather than afterwards. A channel that went backwards is coloured. Bear in
mind the senses differ: smaller FWHM, eccentricity and noise are better, more
stars are better.

The comparison is **within a channel**, never across. On any given rig one
filter is simply softer than another — comparing G against R would flag a
perfectly good G every time.

Measuring costs about 16 seconds per master, so a first scan of a folder takes a
while and the dialog says how far it has got: *Measuring masters: G (3 of 7)*.
Everything that could change the list is disabled meanwhile, Run included.
Results cache by path, size and modification time, so a rescan is instant and a
re-stacked file is measured again.

**The camera is reported once**, under the list, with the QE curve it resolves
to. One session comes off one camera, so a master whose header lost `INSTRUME`
— WBPP's own autocrop rewrites it away — takes the camera its siblings name.
That matters more than it looks: an unnamed camera would otherwise be calibrated
against the ideal QE curve while its siblings used the real one.

**Name the project** in the box at the top. It is filled in from the folder your
masters came from and follows the file list until you type something of your
own; it names the exported PSB.

**Set the options** (all described below), then **Run**. Run is greyed out until
there is something to run.

A Cancel window stays up for the duration and stops at the next checkpoint. It
asks before it does, because cancelling is not undoable — the cached stages
survive, the step in flight does not — and because a modeless dialog with one
button collects the keyboard focus, so a stray Return would otherwise discard
the work in progress. The Process Console stays open throughout, one green line
per operation.

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
| **Solve** | plate solution, skipped if one is already present. Solved with **recursive surface splines** and **verified** against the catalogue | — |
| **SPFC** | spectrophotometric flux calibration. Broadband only | filter curve per L/R/G/B; camera read from `INSTRUME` |
| **MGC** | MultiscaleGradientCorrection against the MARS reference. Broadband only | MARS folder, asked for only if PixInsight does not already know one |
| **GraXpert** | background extraction. Broadband only — never narrowband | on/off, smoothing |
| **Aberration** | star-shape correction, before registration so resampling cannot spread it | None, BlurXTerminator, SyQon Parallax |

**Every solve is verified**, not just the first of a run: each channel is solved
independently and SPFC calibrates each against its own solution, so each one is
worth checking. Verification costs 762 ms against 959 ms for the solve itself,
measured — cheap enough that a once-per-run latch was not worth having.

The thresholds are on the median deviation in pixels, which is already the
scale-relative form. At or above **3.0 px** the solution is wrong — that is the
verifier's own matching tolerance, the largest deviation it can even represent.
Above **0.315 px** it is poor: the median PixInsight published against Gaia DR3
on a 34,000-control-point mosaic panel. Nothing is judged on the RMS, because
both published figures are medians and RMS ≥ median by construction; the RMS is
reported only.

Recursive splines are measured rather than quoted. On NGC 5907 masterLight_L,
5710×3182 at 0.9664″/px, against the solution WBPP shipped: median residual
0.0190 → 0.0157 px, RMS 0.0414 → 0.0338, max 0.1411 → 0.1099. That is −17%,
−18% and −22%.

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
| **Denoise** | where the tool belongs: NoiseXTerminator and MLDenoise on linear data, after star extraction and before the stretch; SyQon Prism after the stretch | None, NoiseXTerminator, MLDenoise, SyQon Prism; strength Low/Medium/High |

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

The finished plates are minimised, and each one's **restore position is set to
the centre**, staggered a title bar apart so every title stays readable and
clickable. Where the icons themselves land is the core's business: PJSR exposes
`iconize`, `deiconize` and `iconic` and nothing that positions an icon, so no
script can lay them out.

## Requirements

**PixInsight 1.9.5 or later**, checked at startup: Loom refuses to run on an
older core rather than failing later on a symbol that is not there. 1.9.5 is
required for the astrometric solution verifier and for recursive surface
splines, both of which Loom uses on every solve.

Developed and run on macOS. It is written to run on Windows as well, but it has
never been run on one — treat that as untested rather than as supported.

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

**MLDenoise needs a model, and PixInsight ships none.** The process lives in the
core `MachineLearning` module, but a fresh instance has an empty `modelPath` and
executing it then fails outright with *"No model path specified"*. Models are
distributed separately, as `.xmlm` containers, through the Databases repository
under **Resources → Updates**. Loom finds one itself — the install's `library`,
then `~/PixInsight/library`, then `~/PixInsight/models` — and offers MLDenoise
only when module *and* model are both present, so a tool that would die mid-run
is never in the dropdown.

For the record, since the parameter names are easy to get wrong: they are
`mask`, `maskClipLow`, `maskBackground` and `maskSmoothness`. There is no
`linearMask`. PJSR accepts assignment to a property a process does not have
without complaining, so measuring against that name reports "the mask changes
nothing" with complete confidence.
