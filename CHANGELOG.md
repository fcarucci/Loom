# Changelog

What changed in each Loom release. Each release's notes on GitHub are its
section of this file.

## [Unreleased]

## [0.3.3] — 2026-10-01

### Fixes

- **A run without narrowband no longer fails on a saved palette.** The palette ticks are hidden when no narrowband is supplied, so an HSO saved from an earlier run couldn't be unticked and stopped the run at the palette stage. A palette whose channels aren't all supplied is now skipped with a note, and the rest of the run goes on.
- **Cancel stops the run.** It used to be treated as a failed step (Parallax, stretch, denoise, calibration, normalisation) and the run carried on.
- **Drizzled masters are recognised** from the integration metadata PixInsight wrote, not the pixel-size keyword a plate solve rewrites, so every drizzled channel reads "2x". Each Source cell shows its full file name as a tooltip.
- **Layered PSB for a run without narrowband:** the RGB group is visible; it was always hidden, so the PSB opened black.
- **Solution check:** with only Gaia DR3/SP set up, PixInsight's check failed with "wrong database configuration" for every channel. Loom now skips it with one plain note.
- **Frame Selector:** cancelling a scan keeps the fingerprints it had already taken.

### New

- **Warning when the cache drive is nearly full:** with **Use cache** on, the cache line turns red when the drive has under 20 GB free, and a run starts with one warning. A full drive used to cut cache writes short.
- **SyQon Studio Parallax profile:** Classic (default) or Aesthetics, for Studio Parallax as the sharpening tool. BlurXTerminator has no profile.
- **Layered PSB:** three neutral adjustment layers for the nebula only, above the DSO groups and below the stars: **Background Curve**, **Faint Nebulosity Curve** and **Color Vibrance**.
- **Prism 2.0's two strengths are named Ultra and Max**, after the models they run. Saved settings and caches are unaffected.

## [0.3.2] — 2026-09-28

### Frame Selector

- **Much faster:** "Reading frames" on 126 subs went from ~97 s to 4 s, and re-opening a night takes under a second. Existing cached measurements stay valid.
- **Previews are cached**, so clicking a frame you've seen shows it instantly.
- The metric drop-downs no longer close by themselves.
- Master and calibration frames are skipped; "Light" and "Light Frame" count as the same type.
- ASIAIR import: Cancel works, a night can't be imported twice, and missing flats are shown per filter.
- No more "msleep() is deprecated" warning.

### Loom

- **Prism 2.0** runs after the stretch only (Medium = Ultra, High = Max). Low and the linear Advanced pass are off until SyQon fixes the tile seams they left.
- **Stretch** no longer clips faint pixels to black, and unlinked colour stretches use each channel's own settings.
- Progress bar when scanning masters.
- A broadband run without a MARS database is refused before it starts.

### Everywhere

- All scripts are in one **Loom** folder under Script.
- Hidden "._" files on exFAT drives and ASIAIR cards are ignored.
- Release installs update through PixInsight's update repository; only a git checkout updates itself.

## [0.3.1] — 2026-09-26

- **Updates:** Loom's updater now installs a release only when it is newer than the version you have. Before, it installed the latest published release whenever its version merely differed from yours, so a copy newer than the latest release, the same version written differently (0.3 and 0.3.0), or a release tag that is not a version number would have been installed over it, updating you backwards.

## [0.3.0] — 2026-09-26

### Loom Fly-Through

**Blind solving.** An image with no astrometric solution, no object name, no RA/Dec and no plate scale is now plate-solved by Loom's own solver, and PixInsight's ImageSolver confirms the result. A match ImageSolver can't confirm, or confirms somewhere else, is never used.
- The solver's star index is built while it solves, from Gaia: the local database set up in Process → Gaia, else Gaia DR3 online. It searches the sky region by region, starting where your earlier solves were, then popular named objects, Messier objects, the rest of NGC/IC, and finally the whole sky.
- Each region is read from the catalogue in one query: a 2° cone around a remembered solve or a catalogued object (objects within 1° of each other share one), a 6° cone in the whole-sky pass. Its stars are cached in `PixInsight/Loom/solver` in your home folder, so a region is never queried twice and later solves read the catalogue less and less. Nothing is downloaded ahead of time, and nothing is built in the background.
- Online, a Gaia answer may take up to two minutes to start (VizieR is slow in dense fields such as the Magellanic Clouds), and a transfer that stops for 30 seconds is abandoned. A region that gets no answer is asked again after a short pause, then skipped (and asked again next time); the solve stops only when the catalogue has not answered for five minutes.
- The progress bar says which region is being searched, and Cancel stops the solve.
- An image solved blind with an empty Object box gets the name of the target found in its field. A name you typed is never replaced.
- Offline, with no local Gaia and nothing cached, the solve stops at once and says what to install.

### Loom Frame Selector

**Import from an ASIAIR.** With an ASIAIR mounted (its card or its storage over USB), the Frame Selector finds it as it starts, reads it, and shows every target on it with its last three nights: frames, filters and flats. Pick a night, review it as usual, and choose a folder. The approved lights and that night's flats are copied into its `Light` and `Flat` folders as XISF, and the confirmation names both. Choosing a folder that is itself named `Light` or `Flat` uses its parent. Nothing on the ASIAIR is changed.

**It says what it is doing.** A window opens at once while it looks for an ASIAIR, volume by volume. Reading the card shows the files found so far, and measuring goes frame by frame in small batches, with a bar that moves across the whole scan.

**Fixes.** The review window's title-bar close button now closes it.

### Loom

**SyQon Studio.** When SyQon Studio is installed (its `syqon-cli` is found in the app, through `SYQON_CLI_PATH`, or where SyQon's own script remembers it), its models are offered beside the standalone SyQon tools, which stay in the dropdowns when they are installed:
- **Noise reduction:** SyQon Studio Prism 2.0. It takes linear data, so it runs where NoiseXTerminator does: on the linear starless plate, before the stretch.
  - Prism 2.0 is Studio's paid Deep Prism. Low is Advanced, Medium is Ultra, High is Max, each at Studio's full blend. On a synthetic linear frame they keep 39%, 20% and 4% of the sky noise; Essential at full strength keeps 77%.
  - SyQon Studio Prism Essential, the included model, is not offered beside it. If the check before a run finds Prism 2.0 not available to your account, the error says so and Loom offers Essential in its place from then on, until a later check finds 2.0 available again. A saved choice of either opens as the one offered.
  - Prism Essential's strength is Studio's blend: Low 0.60, Medium 1.00 (Studio's default). High is also 1.00, because a blend cannot go past fully denoised.
- **Sharpening:** SyQon Studio Parallax, classic family, for aberration correction, star reduction (level 3/5/7 of 10) and detail (deblur strength 0.25/0.50/0.75), all on linear data. With SyQon Studio installed, the aberration pass uses Studio Parallax's correction automatically: with BlurXTerminator chosen, Studio corrects each channel before registration and BlurXTerminator does star reduction and detail on the composite. The log says which tool corrected. Without Studio, BlurXTerminator corrects as before, and its cached channels still hit; with Studio, channels cached with BlurXTerminator's correction are corrected again, once, by Studio.
- **Star extraction:** SyQon Studio Axiom, in Loom and in Loom Fly-Through. Loom tells it whether the image is linear or already stretched.
- Studio runs without Loom's temporary stretch: it takes linear data directly, so linear flux is never pushed through a curve and back.
- **Your SyQon account is checked before the run.** Each Studio model a run will use is tried once on a tiny image, about 13 s each (65 s for all five), once per PixInsight session. A model your account cannot run stops the run before it starts and says which one ("Prism Deep Max (prism-max) is not available to your SyQon account…"). A run that fails later still names the model.
- **The Keychain prompt.** SyQon Studio keeps its sign-in in the macOS Keychain and may ask for your Mac password to reach it. Before the first Studio run Loom says so: enter the password and choose Always Allow, or it asks again every run. A run that shows nothing for 10 s says "Waiting for SyQon Studio's sign-in (check for a Keychain prompt)".

**Gradient removal is a dropdown.** Multi Gradient only, GraXpert or SyQon Studio Deep Gradient, in place of the GraXpert checkbox. MultiscaleGradientCorrection always runs; GraXpert or Deep Gradient adds a second pass after it. Deep Gradient runs on the same linear channels as GraXpert and takes no settings; the smoothing slider is GraXpert's alone. "Also remove gradients from H, S and O" applies to whichever tool is chosen. Saved settings and process icons from before carry over: GraXpert on becomes GraXpert, off becomes Multi Gradient only, and cached GraXpert results are still used.

**Fixes.**
- Where Loom's preferred colour profiles (ROMM RGB, Generic Gray) are missing, as on Windows, the fallback printed "Couldn't find the 'ProPhoto RGB' profile" and similar errors for every plate. It offered profiles from Adobe's folders, which PixInsight doesn't load. Now only profiles PixInsight can find are tried, and a name that fails is not tried again.
- On newer PixInsight versions, the console warning "ByteArray.at() is deprecated" no longer appears.

## [0.2.1] — 2026-09-25

### Loom Fly-Through

**Star quality.** A new setting in the Output section chooses how the stars are drawn:
- **Highest:** the renderer as before, every star drawn from the full-size (4K) working image.
- **High** (the default): stars drawn from the image scaled to the video's size, glows sampled only as finely as they need. It looks the same as Highest and renders about 30% faster.
- **Medium:** stars drawn at half the video's resolution and scaled up onto the full-size nebula. Stars are softer; renders are about three times faster than Highest.

**Faster rendering at every level.**
- Star sprites skip the pixels that could only read darkness. The frames are unchanged, and about 20% faster.
- A crossfade loop renders its still pre-roll frame once instead of once per frame.

**Rendering.**
- While a video renders, the preview shows the frame just finished. HDR frames are labelled "HDR Preview", because their PQ/HLG signal looks flat on an SDR screen.
- A crossfade loop's dissolve and its music's loop blend now take 1 s (they took 2 s).
- A looping video's music blends at the end, like the video's dissolve, instead of at the beginning: the video's music starts 1 s into the song, and the song's first second fades in over the last.
- A video is named after its object, preset and colour encoding, for example `NGC7023_Iris_Nebula_youtube_1080_vertical_HDR-PQ.mp4`.
- **Clear rendered frames…** in the Video section deletes the frames kept for reuse and resume, so the next render draws every frame afresh. It is greyed out when there is nothing to clear, says how many it would delete and asks first, and only touches Loom's own frame files: the videos and anything else stay.

**The dialog while an image is analysed.**
- The dialog stays usable. Draft, Play, the star counts and the image choice are greyed out; every other option can be changed, and the changes are kept.
- Changing the star tool during star removal starts it again with the new tool once the running one finishes. The plate solve isn't repeated, and the first tool's stars stay cached.
- The progress bar says what is running in plain words, and shows a step's time only once it has run a second (no more "— 0 s").

**Fixes.**
- Switching Orientation back while a draft was running was ignored: the vertical draft stayed on screen until the dialog was reopened.
- A vertical preset rendered with the distance it was first turned with, not the one typed afterwards.
- On Windows, the title-bar close button did nothing. The dialog's options are now saved however it is closed.
- A console line "ICC profile embedded" was printed for every rendered frame.
- Cancel, closing the dialog, or changing the star tool during star removal no longer pops up PixInsight's "Do you want to abort the current process?".
- In HDR, the headroom boost of stars carried by the zooming backdrop stayed where the stars were at the first frame.

A new Loom version starts its per-image cache afresh, so each image is plate-solved once more after updating.

## [0.2.0] — 2026-09-24

### Loom Fly-Through (new)

A new script, **Script → Loom → Loom Fly-Through**, turns a finished astrophoto into a video flying into it:
- The photo's own stars move at their real Gaia distances, and the nebula zooms behind them.
- The image is plate-solved, the object identified, and its distance found from its ionising cluster or the star that lights it.
- The stars are separated with the installed star tool (StarXTerminator, StarNet2 or SyQon Starless) and redrawn as sprites that grow, brighten, blur with motion, twinkle and bloom as they approach.
- Presets for YouTube, social media and exhibition, horizontal or vertical, SDR or HDR (PQ/HDR10 or HLG), with a peak in nits. Bright stars can reach into the HDR headroom by their magnitude.
- Loops: back and forth, or a crossfade. Optional logo, and music that fades or loops with the video.
- A draft plays in the dialog. Final frames are 16-bit TIFFs encoded with ffmpeg (H.264, H.265/HEVC, ProRes or VP9).
- A per-image cache (the solve, the stars per tool, the drafts), and frames kept when only the encode changes. A stopped render resumes where it left off.
- Gaia is read from the database set up in Process → Gaia (DR3/SP, then DR3), or from Gaia DR3 online when none answers.
- Renders about 2.5× faster than at first: bloom, the composite and the HDR headroom run in PixInsight's own image operations.

### Loom Frame Selector (new)

A script that measures every subframe in a folder, groups them by filter, and removes the ones the night's own statistics condemn. It comes with three presets, an approval-criteria panel, a filmstrip, anomaly tags, SNR, and blur causes told apart.

### Loom

- All scripts sit in one folder: **Script → Loom** (Loom, Frame Selector, Loom Fly-Through).
- Loom installs and updates from its own PixInsight update repository.
- The masters folder is scanned first, with progress while masters are measured.
- Registration uses the best channel when there is no L.
- L is denoised at its own strength.
- BlurXTerminator runs once, with both sharpening amounts.
- GraXpert is offered on the narrowband channels, off by default.
- Every plate is left open and cascaded, and only the windows a run created are closed.
- When ROMM RGB or Generic Gray are missing, colour profiles fall back to the closest installed working space.
- Stars plates keep their names (no more `L_stars_1`).
- Channel combination no longer warns "Inconsistent ... metadata not generated".
- A ScrollBox event could terminate PixInsight.

## [0.1.0] — 2026-09-19

The first release of **Loom**, a PixInsight script that runs the mechanical stretch from integrated masters to plates ready for creative work:
- It does the solving, flux calibration, gradient removal, registration, cropping, combination and colour calibration, start to finish and unattended.
- It makes no artistic choices: every parameter is measured from the data or fixed by a published convention.
- It supports the XT and SyQon tools (BlurXTerminator, StarXTerminator, NoiseXTerminator, SyQon Parallax, Prism and Starless) and offers only those installed.
- It hands off to Photoshop: 16-bit TIFFs and one layered PSB with the plates stacked, blended and clipped, all tagged ProPhoto RGB.
- Results stay as open windows; Loom never writes to disk unless asked.
- PixInsight 1.9.5 or later. Every astrometric solution is verified, with recursive splines.
- It checks for updates and ships as an installable zip built by CI.
