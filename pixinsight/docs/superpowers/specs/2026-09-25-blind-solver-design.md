# Loom's own blind plate solver — design

**Status:** draft for the maintainer's review (2026-09-25).

## Goal

Loom Fly-Through can plate-solve an image with **no object name, no RA/Dec and no plate scale**. The solver is Loom's own code: JavaScript only, with no external program and no external solving service. The only data it needs is a star catalogue:
- **the local Gaia database** set up in Process → Gaia (DR3/SP, then DR3);
- **else the online Gaia DR3 catalogue at VizieR**, which Loom already uses for lookups.

The blind solve finds an approximate centre and scale. PixInsight's own ImageSolver then makes the final, precise solution Loom already trusts (`Sky.solveWithHints`).

## Where it sits: the order Loom tries

1. The image already has an astrometric solution: use it (as today).
2. **Centre keywords in the file:** FITS `RA`/`DEC`, `OBJCTRA`/`OBJCTDEC`, `CRVAL1/2`; XISF `Observation:Center:RA/Dec`. These go to ImageSolver as hints. *(The hints researcher's findings will settle the exact order.)*
3. An object name, typed, from the file name, or from the folder name (e.g. `IC 1396A/`), gives RA/Dec hints (as today, plus folder names).
4. **Blind solve** (this design), when all of the above give nothing, or when ImageSolver fails with the hints.

## How it works

The method is geometric hashing of star quads, as astrometry.net does. It has two parts: an index built once, and a match per image.

### Quads and their codes
- **A quad** is 4 stars. The two farthest apart, A and B, define a frame: A at (0,0), B at (1,1). The other two, C and D, are expressed in that frame.
- **Its code** is (xC, yC, xD, yD). It doesn't change under shift, rotation or scale, so it needs no plate scale.
- **Canonical form:** a fixed ordering makes the code unique (swapping A/B, ordering C/D, xC ≤ xD), and C and D must lie inside the circle whose diameter is AB.
- **Diameter:** the length of AB. It's in pixels for image quads, in degrees for sky quads.

### The index (built once, then cached)
- **Scale bands:** quad diameters from about 0.1° to 10°, in bands of a factor of about 1.4 (as astrometry.net's index series). A field of any size between about 0.2° and 20° then has quads in at least two bands.
- **Stars per band:**
  - the sky is divided into cells about one band-diameter across;
  - each cell keeps its brightest *M* catalogue stars (*M* ≈ 8–12), so bright, dense regions don't dominate;
  - quads are formed from nearby stars whose AB diameter falls in the band.
- **Lookup:** the codes go into a 4-D grid hash, or a sorted list with an n-d range search, so a code finds its near neighbours quickly.
- **Stored per quad:** the code, the diameter and the 4 star ids. Star positions (RA/Dec) and magnitudes are kept once.
- **Catalogue depth:** it follows the band. Small bands need fainter stars, down to about G 13; large bands are satisfied by G < 9.
- **Built from:** the local Gaia database, in large sky tiles through the Gaia process, else VizieR online in tiles.
- **Cached** in a persistent Loom data folder, not the per-image temp cache. It's keyed by a solver-index version and the catalogue it came from, so later solves never rebuild it.
- **Size target:** tens of MB on disk. The progress bar shows the build ("Building the star index (once)…").

### Matching an image
1. **Stars:** Loom's star detector on the working copy gives stars with positions and fluxes. The brightest *N* (about 40–60) are used.
2. **Image quads:** from the brightest stars, taken bright first and among near neighbours, with their codes and pixel diameters.
3. **Lookup:** each image code finds index quads within a code tolerance, and each hit proposes a match.
4. **Hypothesis:** a match's 4 star pairs give a similarity transform from image pixels to the tangent plane. That's a centre, a scale in arcsec/px, a rotation and a parity (mirror images are allowed).
5. **Verify:** project the catalogue stars around the proposed centre (from the index's stars, or a local or online query) and count how many image stars they match within a tolerance. The hypothesis is accepted when the odds of that many matches happening by chance are overwhelming (a log-odds threshold, as astrometry.net uses). Otherwise it tries the next hypothesis.
6. **Order:** bright quads first, and hypotheses whose scale several hits agree on first, so typical fields solve after few verifications.
7. **Hand-off:** the accepted centre and scale become hints for `Sky.solveWithHints`. The focal length and pixel size are derived from the scale; any consistent pair works. ImageSolver produces the final solution.

### Performance targets (to be measured and confirmed)
- **Index build:** minutes, once, with progress.
- **Blind solve of a typical Loom image:** under about 30 s after the index exists (1–2° field, thousands of stars).
- **Responsiveness:** the dialog stays usable during both, as the analysis is today, and Cancel works between steps.

## Code layout
- `script/lib/Solve.js` (new, pure where possible):
  - quad codes and canonical form;
  - scale bands;
  - the index structure and search;
  - similarity-transform fitting;
  - verification odds.
- `script/lib/Sky.js`:
  - building the index from the catalogue (local Gaia, else VizieR);
  - its disk cache;
  - star lists from the working copy;
  - `Sky.solveBlind( window, progress )`, which returns hints.
- `script/FlyThrough.js`: the order Loom tries (above), the progress text, and what the dialog says when blind solving fails.

## Testing
- **Pure tests in the Node suite:**
  - codes are unchanged under shift, rotation, scale and mirroring;
  - the canonical form is unique;
  - band assignment;
  - index search;
  - a transform fitted from 4 point pairs;
  - the verification odds.
- **Synthetic end-to-end, in PixInsight and in the committed suite:**
  - a star field made by projecting a small synthetic catalogue at a known centre, scale and rotation;
  - the blind solve must recover them within tolerance;
  - the catalogue is stubbed, so there's no network and none of the user's files.
- **Ad-hoc real checks, never committed:** the user's Iris, Elephant's Trunk and NGC 5907, with the object name removed. Recovered centre versus the known solution, and time.
- **Failure modes:** a blank frame, too few stars, and a field outside the catalogue. Each fails quickly, with a clear message.

## Out of scope for the first version
- Solving from pixels alone without a star detector.
- Distortion fitting. ImageSolver does that from the hints.
- Fields under about 0.2° or over about 20°.

## Open points for the maintainer
- **Where the index lives on disk.** Proposed: a Loom folder next to PixInsight's settings, persistent and per user. It's rebuilt only when its version changes.
- **Whether to precompute the index in CI and ship it,** instead of building it on first use. That's rejected for now: you chose the local catalogue, with online as the fallback.
