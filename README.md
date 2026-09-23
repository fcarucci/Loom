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

There are two ways to install Loom: from its PixInsight update repository,
which is the easiest and keeps it up to date, or from the git repository.
Use one or the other, not both — two copies register the scripts twice.

### From the PixInsight update repository (recommended)

1. **Resources → Updates → Manage Repositories.**
2. Click **Add**, enter the repository address, and confirm:

   ```
   https://fcarucci.github.io/Loom/
   ```

   The address must end with the `/`.
3. **Resources → Updates → Check for Updates.** Loom is listed among the
   available packages; make sure it is selected and click **Apply**.
4. The repository is not yet signed, so PixInsight asks you to confirm the
   download from an unsigned source. Confirm it.
5. **Restart PixInsight** when asked — updates are installed while it
   restarts.

Both scripts then appear under **Script → Batch Processing**: **Loom** and
**Loom Frame Selector**. If they do not, run **Script → Feature Scripts →
Regenerate**, then **Done**.

**Updating** is the same **Check for Updates**: a new Loom version is offered
like any other update.

Loom needs PixInsight 1.9.4 or newer; the repository offers it only to those
versions.

### From the git repository

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
| **GraXpert** | background extraction. Broadband by default; H, S and O too when asked — off by default, because narrowband data usually has little gradient and faint emission can be taken for background | on/off, smoothing, also on H/S/O |
| **Aberration** | star-shape correction, before registration so resampling cannot spread it | None, BlurXTerminator, SyQon Parallax |

**Every solve is verified**, not just the first of a run: each channel is solved
independently and SPFC calibrates each against its own solution, so each one is
worth checking.

The thresholds are on the median deviation in pixels, which is already the
scale-relative form. At or above **3.0 px** the solution is wrong — that is the
verifier's own matching tolerance, the largest deviation it can even represent.
Above **0.315 px** it is poor: the median PixInsight published against Gaia DR3
on a 34,000-control-point mosaic panel. Nothing is judged on the RMS, because
both published figures are medians and RMS ≥ median by construction; the RMS is
reported only.

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

The finished plates are **left open and cascaded**. Each is fitted to its
window, then offset a title bar from the last, with the cascade centred as a
block so the deck sits in the middle of the workspace. They are raised in a
fixed order, so a given plate is always at the same depth in the pile and the
palette ends on top.

## Frame Selector

A second script in this repository — **Batch Processing > Loom Frame
Selector** — and not part of a Loom run. It measures every subframe in a
folder, groups them by filter, works out where the line falls for that
channel on that night, and removes the frames below it.

It exists because of a concrete failure: a re-stacked G master came back 8%
softer, 31% more elongated, 18% noisier and with 31% fewer stars than the one
it replaced, and nothing in the pipeline objected. Bad subs are cheaper to
catch before they are integrated than after.

**It does not measure anything itself.** SubframeSelector does, because those
are the numbers you already see in WBPP. A frame selector whose FWHM disagrees
with the subframe table is one nobody can act on.

Grouping is by the raw `FILTER` keyword. A channel whose frames differ in
exposure, binning, geometry or calibration state is reported as not comparable
— a shorter exposure legitimately loses on SNR, and
clipping it against the rest means less than it appears to. It is a warning,
not a veto: the figures are still shown and Run still acts on them, because a
tool that measures frames and then refuses to act on its own measurements is an
obstacle rather than a safeguard. Frames with no readable filter are shown but
never auto-rejected.

### The presets

Three, because `k` — the width of the robust gate, in normalised MADs — is the
one number that decides how much is dropped. **Each channel has its own**: a
night's L and its Ha are not the same population, and one preset over both
either spares the ragged channel or cuts into the clean one.

| preset | `k` | asymptotic | at 20 frames | at 10 frames |
|---|---|---|---|---|
| Lenient | 3.0 | ~0.5% | ~2.8% | ~6% |
| Balanced | 2.5 | ~2.5% | ~6.1% | ~10% |
| Strict | 2.0 | ~9% | ~13.1% | ~17% |

Those are expectations, not promised yields: the median and MAD are estimated
from the same small sample being clipped. The dialog shows the actual count,
which is the only number that is true.

Below **10 valid measurements** in a channel the robust clip does not run at
all. A median exists at three frames; a dispersion worth deleting files over
does not.

### What may reject a frame

Four measurements are taken and all four are scored, but only three of them
may *delete* anything. Scoring ranks; gating rejects; they are not the same
question.

| measurement | ranks | rejects by default |
|---|---|---|
| PSF SNR | yes, most heavily | **no** |
| FWHM | yes | yes |
| eccentricity | yes | yes |
| stars | yes | yes |

**PSF SNR is off because integration already handles it.** ImageIntegration
weights each frame by its signal — WBPP's default is PSF Signal Weight — so the
stack's SNR goes as the root of the sum of the frames' squared SNRs. Every
frame carrying signal raises that sum, so deleting a faint one throws away
signal that was already being discounted in proportion to its worth. On a
measured 126-frame channel, dropping the two faintest cost about 0.4% SNR and
bought nothing.

**FWHM and eccentricity are on because no weighting repairs them.** The stacked
PSF is a weighted blend of the frames' own, and weight follows signal rather
than sharpness — so a soft frame with good SNR earns a *high* weight and blurs
the result. That is the case rejection exists for. Star count is on as the
evidence of transparency that FWHM does not carry: cloud removes stars without
widening the ones left behind, and brings gradients that do not average away.

PSF SNR remains available per channel, because a frame far below the rest
usually means something went wrong rather than that the night was dim.

### Approval criteria

The criteria sit in one panel, laid out the way SubframeStudio does it: the
preset, `k` and the channel switch on the first row, and one criterion per
metric on the second — `FWHM <=`, `ecc <=`, `stars >=`, `PSF SNR >=` — each a
checkbox and a box holding its limit. The right-hand end says how many of the
channel's frames Run keeps.

**Every box is editable, and each metric is decided on its own.** An untouched
box is *automatic*: it shows, greyed and in italics, the limit `k` times this
night's spread gives, and that relative cut is what applies. Type a number and
it becomes that metric's limit instead — the relative cut is set aside for
that metric only, and the others carry on automatically. Clear the box to go
back to automatic. A typed limit stays put when the preset or `k` changes.

The two cannot be combined on one metric, deliberately. A clip on a channel's
own median and MAD drops roughly the same *fraction* however good the night
was — right for "drop this night's worst", wrong for "drop frames that are bad
in absolute terms" — and a frame the relative cut rejects cannot be rescued by
also passing a ceiling. Typing a limit on every metric is how an entire good
night is kept.

Unticking a criterion switches it off entirely, typed limit or not. A typed
limit applies only once confirmed (Return, or moving to another box); Run
confirms a box still being typed in, and never turns a rounded display back
into a stored number.

The keep count means what Run does: when culling in place, the frames left
where they are; when the output is another folder, the frames copied there —
and a channel switched off is then not copied at all.

### Reading the review

Reading a folder digests every frame whole before any measuring starts, so it
is the slow part — a window reports the phase, the count and the file, and can
be stopped. Nothing has been written at that point; a scan only measures.

The frame column shows what differs between frames rather than what they share.
Subframe names differ only in a timestamp and a sequence number, so the common
prefix is dropped; the whole path is on the row's tooltip.

A frame that will be deleted carries a red cross beside its name, and the
measurement that condemned it is shown in red — the verdict names the metrics
it turned on, so the column marked is the one that decided. Every row carries a
mark, an empty one where the frame is kept, so the names keep a shared left
edge.

Under the table, the selected channel's measurements are plotted in frame
order, with the range that keeps a frame drawn as a band behind them; the combo
chooses which measurement. A metric that is not gated has no band, because
nothing it does can reject. The plot and the table are two views of one
selection: the selected frame is ringed, and clicking a point selects its row.

The table also shows SubframeSelector's **SNR** estimate, beside PSF SNR, and
the plot can show it. It is for reading only: it is not a criterion, not in the
score, and cannot reject anything.

The preview is 1:1. **Double click** switches between the whole frame and 1:1;
**drag** the image, use the **scroll bars**, or the arrow keys after clicking
it — Shift for a page at a time.

A two-finger swipe does not move the preview, and cannot be made to.
PixInsight delivers a swipe to a script as a wheel event carrying a single
delta with no orientation, so a sideways swipe arrives with nothing in it;
and no touch, gesture or pan handler exists on any scriptable control, with
`ImageWindow/TouchEvents` enabled or not. The gesture is consumed by the core
for image windows and never reaches a script. Changing frame keeps the view where it was — the
frames of a channel are registered to each other, so the same offset shows the
same stars, which is the only way to compare them.

### The filmstrip

Under the plot, the channel's frames are laid out as thumbnails, the selected
one outlined in yellow with its neighbours either side; the arrows page
through. A **red cross** marks every frame Run leaves out — the same rule as
the table's mark and the keep count. Clicking
a thumbnail selects that frame everywhere. The number under each thumbnail is
FWHM to begin with; the chooser at the left of the strip switches it to any
other measurement, independently of the plot. Clicking a thumbnail that is on
screen leaves the strip where it is; choosing a frame elsewhere — the table,
the plot — brings it into view.

Thumbnails are read one frame per step while you work, the ones on screen
first. Each takes about a third of a second, and the dialog pauses for that
long while it does; a grey tile is one not read yet, and `!` is one that
could not be read. The crosses and letters are drawn straight away: a verdict
never waits for a picture.

### Anomaly tags

Frames that look wrong compared with the rest of their channel are tagged, the
way SubframeStudio tags them. The tags are **advisory and always on**: they
never reject a frame, never change a verdict, and never reach the deletion
log. They name symptoms, not causes.

| tag | letter | fires when |
|---|---|---|
| FOCUS | F | FWHM more than 3σ above the channel's median |
| TRACKING | T | eccentricity more than 3σ above the median |
| CLOUD | C | background more than 5% above the channel's median, or stars more than 3σ below it |
| DROPPED | D | fewer than 10% of the channel's median star count |

σ is 1.4826 × MAD, as for the gates. A metric raises nothing with fewer than
5 usable values in the channel, and the 3σ rules raise nothing when the spread
is under 1% of the median — measurement noise, not a spread. Background is
judged as a percentage instead: SubframeSelector's background moves by about
one 16-bit step between frames on a steady night, far too little to have a
spread, while a real cloud lifts it by tens of percent. A dropped frame
is not also called cloud for the same missing stars. A channel whose frames are
not comparable, or that has no readable filter, is not tagged at all: comparing
the background of unlike frames means nothing.

Background is SubframeSelector's median column, checked against PixInsight's
own median of the frame. If it cannot be read, CLOUD falls back to the star
count alone.

Each tag is its own box, top right of the preview, under a red **REJECTED**
when Run leaves the frame out (**CHANNEL OFF** when that is only because the
channel is switched off while copying). On the filmstrip they are single
letters along the top of the thumbnail. The summary line counts the tagged
frames by kind.

### Deleting

The default action deletes in place, and that is the only irreversible thing
the tool does.

Nothing is deleted until the table has been shown, a confirmation names the
exact count and the per-channel breakdown, and the manifest has been written to
a durable log under `~/PixInsight/Loom-frame-selector/` — **before** any file is
removed, with each outcome appended as it happens. If the log cannot be
written, nothing is deleted.

Identity is a digest of the whole file, taken when the frame was measured and
rechecked immediately before the unlink. Path, size and modification time are
not identity; a replacement preserves all three. A frame that changed, vanished
or became unreadable is skipped and reported rather than deleted.

Writing the approved frames **to another folder** is available instead: point
the output at it and Run copies them there as XISF, leaving every original
alone. **The output folder is emptied first** — everything in it, hidden files
and subfolders included, without asking — so afterwards it holds exactly this
run's frames. A symbolic link inside it is removed as a link; what it points at
is never touched. If anything in the folder cannot be removed, nothing is
copied. Run refuses outright to empty a folder that is, or contains, the folder
of any frame being copied, and never empties the filesystem root or your home
folder. Deleting the originals after a copy is deliberately *not* offered:
check the results, then delete the source folder yourself.

A verdict can be overridden by hand — space on the row, or the button under the
preview. An override wins over the formula, survives a change of `k`, is counted
separately so a summary never hides it, and is recorded in the deletion log.
Overrides last for the session only.

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
