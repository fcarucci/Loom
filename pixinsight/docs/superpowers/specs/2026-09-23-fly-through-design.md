# Loom Fly-Through — design

Status: draft, revised after codex review round 1 and a focused round 2, 2026-09-23

## Intent

A third Loom script, **Script → Batch Processing → Loom Fly-Through**, that
turns a finished, stretched astrophoto into a short video of the camera
pushing in towards the target: the image's own stars drift outward at the
rates their **real distances** dictate, brightening as they approach, while
the nebula swells gently behind them — or, for a galaxy, stays fixed, as it
physically would.

Audiences: social media (short, vertical or square), YouTube (landscape, up
to 4K), an exhibition screen (continuous loop). One render serves all three
through presets.

Success: frame 0 is the photograph; the motion is physically grounded
(depth from Gaia parallaxes, exact camera geometry, inverse-square
brightening) and reads as a smooth push-in; drafts preview within minutes;
final render time is measured by a benchmark before it is promised.

Out of scope: flying past or through the target; any drawn or invented
star (every moving star is a patch of the photo); depth for stars without
a usable parallax, or blended with a neighbour (they stay in the
backdrop); audio; non-JavaScript renderers.

Constraints carried from Loom: JavaScript (PJSR) only; `#engine v8` on line
1; no personal paths in shipped code; tests depend on nothing but PixInsight
and files checked into the repository. **Runs on macOS and Windows** (and
Linux wherever Loom's own tools do): paths are built with "/" as everywhere
in Loom (PJSR accepts that form on Windows), every platform-dependent
function takes the platform as an argument so the Mac suite exercises the
Windows branch, and external programs are started with an argument list,
never through a shell.

## Findings that shaped it (probe on IC 1396, 2026-09-23)

- The Gaia process (`command = "search"`) returns per source: RA, Dec,
  **parallax**, pmRA, pmDec, G, BP, RP, flags, spectrum. No per-source
  parallax error. The DR3 spectrophotometric XPSD files reach G ≈ 17.6.
- PixInsight ships `src/scripts/AdP/NGC-IC.csv` (id, RA, Dec, magnitude,
  diameter, axis ratio, PA, common name, PGC, …). Galaxies carry a PGC
  number. No shipped catalogue holds distances.
- Reddening cannot give the distance from a G < 16 sample (distant stars in
  it are intrinsically red giants; a fake colour step at ~2000 pc).
- The ionising cluster does: stars sharing Trumpler 37's proper motion gave
  140 members, median 960 pc against the published ~925 pc. A coarse grid
  did not find it automatically (it locked onto background at 0.4 mas).
- For a push-in, a 10% error in the backdrop distance is hard to see.

## 1. Pipeline

```
image (solved) ─┬─> star removal ─> starless S; stars layer T = unscreen(image, S)
                ├─> WCS: field ─┬─> NGC/IC: target, type
                │               └─> Gaia (configured database) ─> sources
                └─> placed stars: sources with usable parallax, matched to T
```

- **Star layer.** Star removal runs once on the image, giving S; T comes
  from `Steps.deriveStarsByUnscreen( image, S )` on that single input, so
  screen(S, T) is the image. (Loom's existing `extractStars` uses two
  removals on differently stretched inputs and does not reconstruct the
  original; Fly-Through does not use it.)
- **Placed stars.** Gaia sources with a usable parallax (§2.5), projected to
  pixels through the solution, and matched to a star that PixInsight's own
  **StarDetector** (`pjsr/StarDetector.jsh`, as ImageSolver uses) finds in T,
  within 1.5 px. Each gets a **sprite**: the patch of T covered by that
  detection's footprint (its detected bounding rectangle, grown by one
  FWHM), with other detections inside it masked out. A star whose footprint
  overlaps another detection's is a *blend* and is not placed. Sprite and
  residual are **disjoint pixel masks** over T: every pixel of T belongs to
  exactly one sprite or to the residual R.
- **Compositing.** Each frame sums, in the unscreened star layer, R scaled
  with the backdrop plus every moved sprite, then screens that sum onto the
  scaled S once. At frame 0 the sum is exactly T, so the frame is exactly
  the image.
- **Target** from NGC/IC (§1.1). **Type**: galaxy when the entry has a PGC
  number, nebula otherwise; the dialog lets the user switch it.
- **Distance** D: infinite for a galaxy. For a nebula, from its cluster
  (§2.6) when one is found; otherwise typed, and rendering waits for it.

### 1.1 Catalogues at run time

- **NGC/IC**: read from `<PixInsight>/src/scripts/AdP/NGC-IC.csv`, the core
  install located through the core's own source directory. Missing or
  unreadable file: no identification; the user types a name, type and
  distance. Target choice: among entries whose centre falls in the field,
  the highest score = diameter / (1 + separation from the field centre /
  field radius); ties to the larger. The dialog shows the runner-up and
  lets the user pick it.
- **Gaia**: the Gaia process with **no** `databaseFilePaths` set, so it uses
  the database the user configured for SPFC/SPCC, as PixInsight's own
  NBExtractPI does. A failed execution or an empty result over a field with
  stars stops with "configure a Gaia DR3 database in Process → Gaia".

### 1.2 Files

- `script/FlyThrough.js` — entry point and dialog.
- `script/lib/Fly.js` — pure maths, node-tested: camera path, exact
  projection, brightness/size/opacity, colour, cluster finding, parallax
  quality, presets and framing, ping-pong timing.
- `script/lib/Sky.js` — Gaia query (one replaceable function), NGC/IC,
  WCS projection, sprite extraction and matching.
- `script/lib/Render.js` — frame compositing, 16-bit TIFF writing, ffmpeg,
  draft player.

Reuse, each with a stated adaptation:

- **Solving**: Steps.solve initialises ImageSolver from the image and saved
  settings. Fly-Through adds a path that sets the solver's metadata
  (centre RA/Dec, focal length, pixel size) from the dialog before solving,
  for images with no solution (typically TIFF).
- **Star removal**: the same tools Loom already supports and finds the same
  way — StarXTerminator, StarNet2 (modules, by name) and SyQon Starless
  (external binary, via `Steps.findExecutable` and its config file) —
  through `Steps.removeStars`. It gains an optional `linear` argument used
  only by StarNet2 (the one tool with a linear switch); omitted, it stays
  true, so every existing Loom call is unchanged. Fly-Through passes false.
  StarXTerminator and SyQon Starless are called exactly as Loom calls them.
  The stars layer is Loom's unscreen split, reused as is. The dialog offers
  whichever tools are installed, like Loom's own dropdown.
- **ffmpeg**: auto-discovered from a candidate list per platform, each
  checked by running `ffmpeg -version`; the found path is remembered, and a
  Browse button overrides it.
  - macOS / Linux: the saved path, `/opt/homebrew/bin`, `/usr/local/bin`,
    `/usr/bin`, then every PATH entry (split on `:`).
  - Windows: the saved path, winget's `%LOCALAPPDATA%\Microsoft\WinGet\Links`,
    Chocolatey's `C:\ProgramData\chocolatey\bin`, Scoop's `~\scoop\shims`,
    `C:\ffmpeg\bin` and `C:\Program Files\ffmpeg\bin`, then every PATH
    entry (split on `;`, quotes stripped, backslashes turned to `/`); the
    binary is `ffmpeg.exe`.
  Output of ffmpeg is read through ExternalProcess's data callbacks while
  pumping events (Loom's pattern; blocking reads lose it, and an unread
  stderr pipe can stall a long encode). Its encoders are read once (`ffmpeg -hide_banner
  -encoders`) so only formats it can produce are offered.

## 2. Motion and rendering

### 2.1 Camera

The camera starts at Earth looking at the target direction **u** and moves
a distance s(t) along **u**. Travel is set in parsecs: default 20% of D for
a nebula, 200 pc for a galaxy (shown and editable in both cases), so the
same number means the same physical motion for any image. For a nebula,
travel is capped at 0.9 D.

s(t) = s_end × smoothstep(t) (or linear), t ∈ [0, 1].

### 2.2 Exact projection

For a star at distance d in unit direction **v**, its position relative to
the moved camera is **p** = d·**v** − s·**u**. Its new direction is
**p**/|**p**|, projected onto the image through the frame-0 tangent-plane
geometry: the gnomonic projection about the image's reference point, then
the image's own pixel mapping (the WCS). This is exact for any field size
and off-axis position; no small-angle assumption.

The backdrop is a plane at distance D facing the camera. Moving the camera
along **u** scales that plane's gnomonic image uniformly about the target
by K = D/(D − s); K = 1 for a galaxy. Exact for a TAN projection; SIP
distortion is ignored for the backdrop (stated).

A star is drawn only while it is in front of the camera (**p**·**u** > 0);
once the camera has passed it, it is gone for the rest of the clip.

Stars behind the nebula (d > D) are drawn too, added by screen blending:
emission nebulae are translucent, and any dimming by dust is already in the
photo's pixels. They simply move less than the backdrop.

### 2.3 Appearance

- **Brightness**: the star's total light scales as (d / |**p**|)², the
  inverse-square law, applied to the sprite's **integrated** flux — its
  pixels are scaled by (d/|**p**|)² / g², where g is the growth factor
  below, so growing a star does not add light. Applied in the unscreened
  star layer; the screen composite then keeps the result below white.
- **Size**: the sprite is resampled by g = 1 + growth × (d/|**p**| − 1),
  growth 0 … 0.3 (default 0.15).
- A "brightening" switch turns the flux scaling off (pure parallax).

### 2.4 Opacity (stars nearing the camera)

Opacity α = 1 − smoothstep of d/|**p**| from 8 to 20. It is continuous,
reaches 0 before the singularity at |**p**| → 0, and stars with
|**p**| < 0.02 d are never drawn. No star passes through the viewer.

### 2.5 Usable parallax

Rows carry no parallax error, so quality comes from Gaia DR3's published
median parallax uncertainty as a function of G (Lindegren et al. 2021):
about 0.02 mas at G ≤ 15, 0.07 at G = 17, 0.1 at G = 17.6, interpolated.
Parallaxes are corrected by the DR3 global zero-point (+0.017 mas). A star
is placed when ϖ ≥ 5 σ(G) and ϖ > 0. Rows flagged as duplicated or with
poor astrometry (from the flags field) are excluded. This is a population
model, not a per-star error; the dialog says so.

### 2.6 Nebula distance from its cluster

- Sample: a Gaia query of its own, centred on the target (not limited to the
  image), G < 16. "Inside" = within r = min(catalogued radius, 0.45°);
  "field" = the annulus 1.5–3 r. The cap keeps the annulus inside a query of
  ~1.35°; a big nebula's cluster sits near its centre.
- Kernel: pmRA and pmDec in units of 0.3 × the field's MAD, parallax in
  units of 3σ(G).
- Seed: the inside star whose kernel neighbourhood has the largest excess
  over the field (count scaled by the ratio of star counts), in Poisson
  sigmas — an excess, not a ratio, so a chance clump of five stars over an
  empty field cannot outrank a real cluster.
- Members: the seed's centre is moved to the median of its neighbours
  (five passes); members are inside stars within 1.5 kernels of it, since
  a real cluster's parallaxes spread by its depth as well as Gaia's error.
- Accept when members ≥ 30, members / field-predicted ≥ 3 and seed excess
  ≥ 5σ; D = 1000 / median member parallax, reported with member count and
  interquartile distance range. Otherwise "no cluster found".
- Measured (2026-09-23): the ratio-seeded, 1σ-window design found nothing on
  IC 1396; this one gives 922 pc, 48 members, 908–941 pc (Trumpler 37,
  published ~925 pc).
- The estimate is always shown and editable; nothing renders from it
  unseen.
- Regression: a recorded IC 1396 source list (§5) must return 870–1000 pc.

### 2.7 Pacing and the exhibition loop

Presets other than exhibition play t from 0 to 1. The exhibition loop is
**ping-pong**: 0 → 1 then 1 → 0 along the same path. With smoothstep the
camera is at rest at both turning points, so position and velocity are
continuous everywhere; the loop point is invisible. Its length is twice the
chosen duration.

### 2.8 Colour and dynamic range

**Colour management (every output).** The input's pixels are in whatever
space its profile says -- Loom's own plates are ProPhoto (ROMM RGB). Split,
sprites and compositing stay in the image's own encoding (so frame 0 is
exact); each finished frame is then converted in Fly-Through's own code:
decode with the source profile's tone curves to linear light, convert
primaries (source matrix -> XYZ D50 -> Bradford -> D65 -> Rec.709 for SDR or
BT.2020 for HDR), then encode (sRGB curve for SDR; PQ or HLG for HDR). The
curves and matrix are read from the embedded ICC profile itself: every
standard working space (ROMM/ProPhoto, sRGB, Adobe RGB, Display P3, BT.2020)
is a matrix/tone-curve profile, so nothing depends on which profiles the
machine has installed (Windows has few). An image with no profile of its
own is read in PixInsight's default profile -- the one PixInsight displays
it in, and embeds when saving it; a profile that cannot be read, or a
LUT-based one, falls back to sRGB; the dialog says which. Per-frame cost is kept down with 16-bit lookup tables for the curves.

**Dynamic range: SDR (default) or HDR**, for the whole render.

- SDR: as §2.3; star light above white clips. Rec.709 tags.
- HDR: the same composite, in linear light. The image's own light maps SDR
  white to the reference 203 nits (ITU-R BT.2408), so frame 0 is the image
  at its normal brightness. Star light that would clip in SDR -- the part
  of the unscreened star sum above 1 -- is kept as highlight above white,
  rolled off smoothly towards the peak, so an approaching star glows
  brighter than the nebula instead of flattening into a white disc: in
  units of SDR white, L = linear( composite ) + excess, then above 1,
  y = 1 + (P - 1)(1 - exp( -(L - 1)/(P - 1) )) with P = peak / 203 (smooth
  at white, monotone, never above the peak). The **peak** (default 1000
  nits, range 400-4000) applies to PQ; HLG is relative to a 1000-nit
  reference display (system gamma 1.2, the BT.2100 inverse OOTF), where
  SDR white lands at signal 0.75 as BT.2408 specifies. The 16-bit TIFF
  frames hold the final HDR signal (the curves make 16 bits ample).
- Transfer per preset, each switchable: **HLG** (ARIB STD-B67) for Social
  and YouTube -- it degrades gracefully on SDR screens -- and **PQ**
  (SMPTE ST 2084, HDR10) for Exhibition, for a display you control.
- HDR formats: HEVC Main 10 (default), VP9 profile 2, ProRes 422 HQ; all
  10-bit, tagged bt2020nc / bt2020 / smpte2084 or arib-std-b67. PQ carries
  HDR10 mastering metadata (BT.2020 primaries, D65, 0.0001-peak nits) and
  MaxCLL/MaxFALL measured from the rendered frames. H.264 is SDR only.
- The draft preview is SDR (PixInsight cannot display HDR); the dialog says
  so. The HDR look is checked in the video.

## 3. Dialog, presets, preview

**Input**: a file (FITS, XISF or TIFF) or the active view.

- Solved (astrometric solution present): used as is.
- Not solved: solved from a rough centre (typed, or from an object name via
  NGC/IC) and pixel scale (focal length, pixel size; prefilled from the
  header when present). The solution is shown before anything renders.

**Found**: target, type (switchable), distance with its source ("960 pc,
cluster of 140 stars, 900–1040 pc" or a required field), placed / blended
/ backdrop star counts, the runner-up target.

**Motion**: travel (pc), easing (smoothstep / linear), growth, brightening.

**Output**:
- Duration (s), fps (24, 25, 30, 60).
- Presets, any combination: Social 1080×1920 and 1080×1080; YouTube
  3840×2160 or 1920×1080; Exhibition loop 3840×2160, ping-pong.
- Each preset takes the largest rectangle of its aspect ratio that fits the
  image, centred on the target and shifted inward if needed; it is
  resampled to the preset's size.
- Output folder: 16-bit RGB TIFF frames per preset (`frame_00000.tif` …) —
  PixInsight's PNG writer cannot store 16 bits (`canStore16Bit` false,
  measured) — a test reads one back and checks its sample depth.
- **Dynamic range**: SDR or HDR (§2.8). With HDR: peak nits, and each
  preset's transfer (HLG / PQ) with the §2.8 defaults. The format list
  shows only the formats valid for the choice.
- **Create video** (checked by default when ffmpeg is found; disabled with
  "install ffmpeg, e.g. `brew install ffmpeg`" on macOS or
  `winget install Gyan.FFmpeg` on Windows, when not). Format, from those
  the discovered ffmpeg can encode:
  - MP4 · H.264 (libx264) — plays everywhere; default for 1080p/social
  - MP4 · H.265/HEVC (libx265, tagged hvc1) — smaller 4K; default for 4K
  - MOV · ProRes 422 HQ (prores_ks) — for editing
  - WebM · VP9 (libvpx-vp9) — for web pages
  Quality: High / Standard (CRF 18/23 for H.264, 20/26 for HEVC, 24/32 for
  VP9; ProRes is fixed-quality). yuv420p (ProRes: yuv422p10le), Rec.709
  tags. One video per preset, named `<preset>.<ext>`. Without ffmpeg the
  frames are still written and the exact command is shown (quoted for
  the platform's usual shell: cmd.exe on Windows).

**Preview (draft)**:
- Rendered at 480 px on the long side. Frames kept = min(duration × fps,
  400 MB / bytes per frame); the draft's frame rate is lowered by whatever
  factor that needs, so memory stays under 400 MB for any duration (a 20 s
  clip keeps 30 fps at ~300 MB).
- Played in a window with a single-shot Timer re-armed each frame; the
  frame shown is the one due by wall-clock time, so late ticks drop frames
  instead of slowing the clip. Play/pause, scrubber, loop toggle.
- Cancel during draft or final finishes the current frame.
- Teardown as in the Frame Selector: release in finally, the player
  checks visibility each tick, no teardown-time handlers.

**Performance**: the plan's first rendering task is a benchmark (4K frame
from an 8 MP backdrop with 2,000 sprites) that sets the per-frame time; the
dialog then shows the estimated render time for the chosen presets before
starting. No render time is promised before it is measured.

## 4. Failures

- No star-removal tool (StarXTerminator, StarNet2 or SyQon Starless):
  refuse, naming all three.
- No Gaia database configured, or the query fails: explain where to
  configure it and stop; never invent distances.
- Solve fails: show the solver's reason; ask for a better centre or scale.
- NGC/IC unreadable or no target in the field: user types name, type,
  distance.
- Few placed stars (< 50): warn, allow.
- Nebula with no cluster found: distance required before rendering.
- ffmpeg missing: frames only, plus the command.

## 5. Testing

Node (`Fly.js`, pure):
- Exact projection against hand-computed cases on and off axis, wide
  fields, stars behind the nebula; K for D finite and infinite.
- Integrated flux follows inverse-square whatever the growth; the
  brightening switch; opacity continuous and zero before the singularity.
- Parallax quality: σ(G) interpolation, zero-point, the 5σ rule.
- Cluster finding: a constructed clump is found with the right distance; a
  uniform field finds none; thresholds.
- NGC/IC target score and tie rule on a small constructed table.
- Ping-pong: position and velocity continuous at both turns; preset crop
  rectangles for every aspect and image shape; draft memory cap.
- **Recorded fixture**: a trimmed IC 1396 Gaia source list (RA, Dec,
  parallax, pm, G, BP, RP; G < 16) checked into `ci/fixtures/` — the
  cluster finder must return 870–1000 pc from it. Recorded once from the
  live query; the suite never reads a catalogue file.

PixInsight (generated inputs only):
- A synthetic star field with an astrometric solution written as FITS WCS
  keywords; a test first proves `celestialToImage` works on it.
- An injected source list in place of the Gaia query.
- Frame 0 equals the input within 1 in 16 bits; sprites move to the exact
  projection; the backdrop scales by K; galaxy mode leaves it fixed; a
  blended star stays in the backdrop; a star the camera passes is never
  drawn again.
- TIFF frames written, named, and 16-bit on read-back.
- Colour and HDR (node): PQ and HLG curves at published points (PQ of 203
  nits = 0.5807; SDR white on HLG = 0.75); the 709-to-2020 matrix keeps
  white; the highlight rolloff is monotone and never exceeds the peak; frame
  0 in HDR maps the image's white to 203 nits; ffmpeg arguments per
  transfer. PixInsight: a ProPhoto-tagged image is converted (its pixels
  change, an sRGB one's do not); with ffmpeg present, HDR clips encode and
  ffprobe reports 10-bit, the BT.2020 tags, and HDR10 side data for PQ.
- ffmpeg candidates for both platforms (node, platform passed in): Windows
  order, `;` splitting, backslash and quote handling, `.exe`; the shown
  command's quoting per platform.
- Draft player: frames due by wall clock, drops when late, five build/
  play/close cycles, PixInsight alive and accepting scripts afterwards.
- The benchmark runs in the suite on a generated 8 MP backdrop and records
  the per-frame time.

Not coverable by the suite: the live Gaia query against a configured
database. Verified by hand on IC 1396 and recorded in
`docs/verified-parameters.md`, as the SubframeSelector columns were.

## 6. Known limits

- The suite runs on the Mac; the Windows branches are covered by tests that
  pass the platform explicitly, and by one hand run on a Windows PixInsight
  when one is available (recorded in `docs/verified-parameters.md`).

- Parallax quality uses Gaia's population uncertainty by magnitude, not
  each star's own error: some placed stars will be at the wrong depth.
- The cluster finder is validated on one recorded field (IC 1396). Its
  estimate is therefore always shown with its member count and range, and
  editable; a wrong estimate costs a visibly odd backdrop growth, not data.
- Sprite footprints in crowded fields are as good as StarDetector's
  detections; blends are left in the backdrop rather than guessed apart.
