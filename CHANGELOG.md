# Changelog

What changed in each Loom release. Each release's notes on GitHub are its
section of this file.

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
