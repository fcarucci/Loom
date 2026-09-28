# Changelog

What changed in each Loom release. Each release's notes on GitHub are its
section of this file.

## [Unreleased]

- **Frame Selector:** the metric drop-downs (the one beside the filmstrip and **Plot:**) no longer close by themselves while the thumbnails are loading, so the metric can be changed. Each thumbnail used to be read by opening the frame in PixInsight's workspace, stretching it with HistogramTransformation and closing it again, every ~0.5 s for every frame of the channel; that closed an open drop-down. Frames are now read and stretched without a window or a process, and nothing is written to the Process Console while the review loads thumbnails or shows a frame.
- **Frame Selector:** clicking a frame you have already looked at shows it straight away: previews and filmstrip thumbnails are now kept in a **Loom-previews** folder in the system temporary folder, so a frame is read and stretched once, not on every click or every launch. An entry is used only while the frame's path, size and modification date are unchanged. The folder is kept under 1 GB, dropping the least recently viewed frames first, and Loom's **Clear cache** empties it.
- **Folders on non-Mac disks:** Loom no longer tries to open macOS's hidden "._" companion files (and other hidden files) as images when scanning a masters or frames folder on an exFAT disk such as an external drive or an ASIAIR card. Emptying or removing a folder still removes them.
- **Script menu:** Loom, Loom Fly-Through and Frame Selector now all appear in one **Loom** folder under Script. Fly-Through and Frame Selector had each shown up as a separate category of its own.
- **Noise reduction, SyQon Studio Prism 2.0:** runs after the stretch only: Ultra at Medium, Max at High. Its Advanced pass on the linear plate is off, and with it Low: Advanced left faint tile seams that the stretch turned into a flat band with a hard edge, for example a green-teal band along two edges of a narrowband palette. It stays off until SyQon fixes it. With the stretch off, Prism 2.0 offers Medium only, Ultra on the linear image. A saved Low loads as Medium, and so does High with the stretch off. The check before a run tries only the models your strengths will use.
- **Stretch:** Loom's own stretch (the one used when MultiscaleAdaptiveStretch is not chosen, and for every stars plate) no longer turns faint real pixels black. Its black point is now kept below all but one pixel in ten thousand of each channel; before, on a plate with a long dark noise tail, up to 1% of the pixels went to 0. A colour plate stretched unlinked now gets each channel's own black point and midtone; before, all three used the first channel's, which cut off a darker channel. Cached results of this stretch are re-run once.
- **Noise reduction, SyQon Studio Prism 2.0:** the console now names each pass, for example `(medium, Advanced, before stretch)` and `(medium, Ultra, after stretch)`, and the noise reduction tooltips say where each tool runs, what each strength runs for Prism 2.0, and that High is very slow (Max takes about 40 times as long as Ultra). The tool's tooltip used to say Studio's Prism runs where NoiseXTerminator does, which is true of Essential only.
- **Scan Masters Folder and Add Files:** a progress bar now shows how far through the masters the scan is, with the master being measured marked by a moving block, and each master appears in the table as soon as it is measured instead of all of them at the end. The bar is hidden when nothing is being scanned. The block pauses while PixInsight measures a master, because PixInsight does not let the dialog repaint during that step, and moves on as soon as the step ends.
- **Frame Selector, import from an ASIAIR:** Cancel now stops an import and a card read. Before, Cancel said it was stopping and the import went on. After an import or a copy the review is locked, so the same night can't be imported twice by pressing Run again.
- **Updates:** a release install no longer updates itself; PixInsight's update repository (Resources → Updates) keeps it current. A git checkout still updates itself at startup, and only a checkout shows **Update Loom automatically**.
- **Frame Selector, import from an ASIAIR:** the chosen night's description now lists its flats filter by filter, and a filter with no flats is shown in red, so a night missing some flats is noticed before it is imported. Before, it gave only the total number of flats in the session.
- **Noise reduction:** MLDenoise's cached results now record the strength they were made at, as every other denoiser's do, so a future change to what Low, Medium or High means re-runs them instead of reusing the old result. Existing MLDenoise noise reduction results in the cache are re-run once.
- **MARS check before a run:** a run with any broadband channel and no MARS database is now refused before it starts, with the other pre-run problems. Before, it stopped at gradient correction, after the first channel had been plate-solved and flux-calibrated.
- **Noise reduction:** SyQon Studio Prism 2.0 now denoises in two passes (its Advanced pass is now off again; see the first entry). Advanced runs on the linear plate, before the stretch, where it keeps faint detail; after the stretch, Ultra (Medium) or Max (High) removes the noise that is left. Low is Advanced alone. Every pass runs at Studio's full blend. This follows SyQon's published input contract: Advanced takes linear data only, Ultra and Max take linear or stretched data.
  - Medium is Advanced then Ultra, and High is Advanced then Max. This replaces the one-model-per-level ladder, including the Medium/High swap made earlier in this release cycle.
  - Advanced is the same at every level, so its result is cached once. Changing between Medium and High re-runs only the pass after the stretch.
  - With the stretch off, only Advanced runs, and the log says the second pass was skipped.
  - The check before a run tries all three models, Advanced, Ultra and Max, whatever the level, so Prism 2.0 is offered only when your account can run all of them. If it can't, Loom offers Prism Essential in Prism 2.0's place, as before.

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
