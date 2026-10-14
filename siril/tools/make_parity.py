"""Build docs/PARITY.md from the per-task rows in docs/parity/*.md plus the not-ported list.

Usage (from the repo root): python tools/make_parity.py
It also reports any JS name of tests/acceptance/js_inventory.txt that PARITY.md does not mention
(the acceptance test tests/acceptance/test_pr8_parity.py checks the same). Re-run it after editing
docs/parity/*.md; docs/PARITY.md is generated, do not edit it by hand.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ORDER = ["e1a", "e1b", "e2a", "e2b", "e2c", "e3a", "e3b", "e3c", "e3d", "e4", "e5", "e6", "e7", "e8", "e9a", "e9b",
         "r1-a", "r1-b", "r1-c", "r1-d", "r1-e", "r2-0", "r2-a", "r2-b", "r2-c", "r2-d",
         "r3-a", "r3-b", "r3-c"]
TITLES = {
    "e1a": "geometry.py, parallax.py", "e1b": "presets.py, encode.py", "e2a": "colour.py",
    "e2b": "gaia.py, wcs.py, io_fits.py, io_tiff.py", "e2c": "row order (FITS ingest) and Gaia fetch fixes",
    "e3a": "detect.py, assign.py", "e3b": "starmodel.py", "e3c": "starfit.py", "e3d": "starlayer.py",
    "e4": "resample.py, bloom.py, scene.py", "e5": "sprites.py, frame.py", "e6": "pipeline.py, cli.py",
    "e7": "siril_bridge.py, Loom/loom_fly_through.py", "e8": "Docker, CI", "e9a": "HDR frame oracle and frame tests",
    "e9b": "review clean-ups (materialise_cd public, stub docstrings, Docker script)",
    "r1-a": "refactor loop 1: jsmath", "r1-b": "refactor loop 1: orchestration", "r1-c": "refactor loop 1: render path",
    "r1-d": "refactor loop 1: star code", "r1-e": "refactor loop 1: IO and Siril edge",
    "r2-0": "loop 2: noisy mono detector fix (INV-2)", "r2-a": "loop 2: Siril layer and encode",
    "r2-b": "loop 2: orchestration, scene inputs, Gaia query", "r2-c": "loop 2: render path and resampling",
    "r2-d": "loop 2: star code interfaces",
    "r3-a": "loop 3: orchestration, package, Gaia, encode", "r3-b": "loop 3: Siril dialog, colour, frame, resample",
    "r3-c": "loop 3: star code privates",
}

HEAD = """# Parity: Loom Fly-Through (JavaScript) -> loomsiril (Python)

Source: Loom v0.4.1 (https://github.com/fcarucci/Loom), `script/FlyThrough.js`, `script/lib/Fly.js`,
`script/lib/Render.js`, `script/lib/Sky.js`, read-only. Oracle: Loom's `script/selftest.js` groups and the golden
numbers recorded by `tools/record_oracle.js` (one recorder per module in `tools/oracle/`) into `tests/oracle/*.json`;
the Python tests replay them at the JS tolerances.

Status: **P** ported (same maths, tests pin the JS numbers) - **V** ported vectorised (numpy) - **R** replaced by a
Python equivalent (documented) - **D** not ported (reason given; summary in "Not ported, and why") - **S** Siril layer.

Conventions that differ from the JS on purpose (engineering decisions, each tested):
- Images are numpy arrays shaped (h, w), one per channel; the JS flat index `y*w + x` is `[y, x]`.
- Row order is TOP-DOWN everywhere inside loomsiril, as in the JS. FITS files and Siril's pixel data are bottom-up:
  they are flipped once on the way in, and their WCS converted with `wcs.flip_rows` (CRPIX2 -> h + 1 - CRPIX2,
  CD1_2 and CD2_2 negated). PSF stars from Siril are pixel-edge coordinates from the top (x - 0.5, y - 0.5).
- Records the JS passes as plain objects are dataclasses in `loomsiril/types.py`.
- The star detector (`detect.detect_stars`, Python-only: it replaces PixInsight's StarDetector) floors its noise at one
  16-bit step, 1/65535, so a noiseless (synthetic or heavily denoised) stars layer does not turn every tail pixel into a
  detection. Caveat: a stars layer whose noise is below that floor loses stars fainter than about 7.6e-5 above the sky;
  real 16-bit frames are not affected. Details in the E3A section ("Noise floor").
- On a noisy stars layer that is mostly exact zeros (a one-channel layer after star removal clips the negative noise),
  the detector takes its noise from the upper half of the distribution (loop 2, INV-2 fix). Known limits: in a crowded
  noisy one-channel field that estimate stays biased high by star light (about 1.3x sigma with 150 bright stars), which
  costs faint-star recall; and a three-channel frame with an exactly-zero border of about 25% of the width or more also
  takes this path and loses some 5-sigma stars (6 sigma and above unaffected). Details in the E3A section.
- JavaScript number semantics Python lacks (NaN-propagating Math.max/min, IEEE division, `||` defaults, Number#toString,
  JSON.stringify) live in one module, `loomsiril/jsmath.py` (phase R loop 1).

## Shared primitives (core.py, types.py; task E0)

| JS | Python | Status | Test |
|---|---|---|---|
| Fly.RAD, smoothstep, median, mad | loomsiril.core.RAD, smoothstep, median, mad | P | tests/test_core.py |
| Fly.vec, radec, dot, separation, tanProject, tanUnproject | loomsiril.core.vec, radec, dot, separation, tan_project, tan_unproject | P | tests/test_core.py |
| Fly.grownRect, detectionRect, squareRect | loomsiril.core.grown_rect, detection_rect, square_rect | P | tests/test_core.py |
| Math.round (JS halves) | loomsiril.core.js_round | P | tests/test_core.py |
| Steps.deriveStarsByUnscreen (PixelMath) | loomsiril.core.unscreen (and core.screen) | R | tests/test_core.py |
| (plain JS records: placed, detection, halo, model, sprite, scene) | loomsiril.types.* | R | tests/test_types.py |
"""

NOT_PORTED = """
## Not ported, and why

1. **PixInsight-only accelerations** (`Render.bloom`/`composite`/`blend`/`starsToHeadroom`/`blurImage`/`box3Kernel`/
   `bloomWhiten` ImageOp paths): numpy is the vectorised engine; the JS reference versions (`bloomJs`,
   `compositeJs`, `starsToHeadroomJs`, the blend loop) are what is ported. The exact dark-pixel skips for a scalar
   interpreter (`anyLit`, `reachTests`, `reachTest`, `darkReach`, `darkMaps`, `darkMap`, `darkRow`, `nearLight`,
   `patchReach`, `rayDistance`, `SKIP_DARK`) only skip zero contributions; a numpy bounds test does the same job.
   (`footprintPays` IS ported: it changes the result, see the E5 rows.)
2. **PJSR user interface**: `FlyThrough.Dialog`, `Player`, `renderDraft`, `draftOptions`, `measureSpeed`,
   `estimateMs`, `PREVIEW_EVERY`, `Render.dueFrame`, `paintLayer`, `still`, `STILL_LONG`, `describeTarget`,
   `blindObject`, `describeDistance`: the Siril script has its own small tkinter dialog
   (`Loom/loom_fly_through.py`).
3. **NGC/IC object lookup** (`Fly.parseNgcIc`, `OBJECT_ALIASES`, `OBJECT_STOPWORDS`, `OBJECT_MIN_SCORE`,
   `objectWords`, `levenshtein`, `nameScore`, `messierHits`, `nameHits`, `aliasHits`, `findObject`, `pickTarget`,
   `targetType`, `objectFromFileName`, `objectFromPath`, `FILE_NAME_SCORE`, `Sky.readNgcIc`, `NGC_IC_RELATIVE`,
   `NGC_IC_CANDIDATES`, `installDir`, `ngcIcMissingWarned`, `FlyThrough.identify`, `headerObject`, `findDistance`,
   `stageOf`): needs PixInsight's NGC-IC catalogue file. v1 takes `--target ra,dec` / `--target-pixel` (or the header
   centre) with `--type` and `--distance` or the Gaia cluster/illuminator finder.
4. **Logo overlay** (`Fly.LOGO_PLACES`, `LOGO_SIZE`, `LOGO_MARGIN`, `logoFade`, `logoRect`, `Render.logoLayer`,
   `addLogo`, `FlyThrough.readLogo`, `LOGO_SETTING`, `LOGO_PLACE_SETTING`): cosmetic, needs PNG-with-alpha reading.
5. **Image cache and settings** (`Fly.CACHE_FORMAT`, `hashKey`, `imageCacheKey`, `cacheToPrune`,
   `Render.packScene`, `unpackScene`, `Sky.writeArrays`, `readArrays`, `FlyThrough.CACHE_KEEP`, `cacheRoot`,
   `cacheDir`, `saveWork`, `loadWork`, `starsName`, `saveBuilt`, `loadBuilt`, `saveDraft`, `loadDraft`, `touch`,
   `prune`, `removeTree`, `OPTIONS_SETTING`, `OBJECTS_SETTING`, `TOOL_SETTING`, `FOCAL_SETTING`, `PIXEL_SETTING`,
   `Render.FFMPEG_SETTING`, `cancel`, `isCancel`): PixInsight-session conveniences. The frames.json signature and
   resume ARE ported; the Siril script keeps its last options in one JSON file.
6. **Plate solving** (`Sky.solverIndexDir` ... `solveBlind`, `catalogueTile`, `regionDir`, `regionCatalogue`,
   `readCachedRegion`, `cacheRegion`, `historyPath`, `readHistory`, `remember`, `blindCandidates`, `solveOnce`,
   `solveAtPixel`, `solveWithHints`, `solutionHints`, `clearSolution`, `writeTanKeywords`, `WCS_KEYWORD`,
   `BLIND_RETRY_PAUSE`, `BLIND_OUTAGE`, `NO_CATALOGUE`, `forgetRegions`, `_regions`, `now`, `pause`, `cancelled`,
   `FlyThrough.solveUnsolved`, `solveByHints`, `Fly.solveFailure`, `SCALE_TOLERANCE`, `plausibleScale`): Siril
   plate-solves; the CLI takes a solved FITS header or a JSON solution.
7. **Linear-image stretch** (`Sky.stretchIfLinear`): `presets.looks_linear` is ported; the CLI warns, and in Siril
   the user stretches first. Porting Loom's stretch is a separate module.
8. **Gaia through PixInsight's Gaia process** (`Sky.querySources`, `gaiaRelease`, `GAIA_DR2`, `GAIA_EDR3`,
   `GAIA_DR3`, `GAIA_DR3SP`, `GAIA_NAMES`, `GAIA_ORDER`, `GAIA_MAG_LIMIT`, `resetGaia`, `requireSources`,
   `GAIA_ONLINE_TIMEOUT`): no equivalent in Siril's Python API; Gaia DR3 comes from VizieR online (cached) or a TSV.
9. **PixInsight window plumbing** (`Sky.copyWindow`, `iccBytes`, `Render.channels` as an Image reader,
   `Render.writeTiff` via FileFormat, `Render.srgbIcc` via the system profile, `Render.runProcess` via
   ExternalProcess): replaced by numpy planes, tifffile and subprocess (rows marked R above).
10. **Small UI helpers**: `Fly.describeStars` (the dialog's star-count sentence; the CLI and the Siril script print
    pipeline.Result.counts instead) and `Sky.transferCancelled` (the dialog's Cancel during a Gaia download;
    gaia.fetch_text stops on its stall/total deadlines and has no cancel button to watch).
"""


def main() -> None:
    repo = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
    inv = Path(sys.argv[2]) if len(sys.argv) > 2 else repo / "tests" / "acceptance" / "js_inventory.txt"
    out = Path(sys.argv[3]) if len(sys.argv) > 3 else repo / "docs" / "PARITY.md"
    parts = [HEAD]
    for t in ORDER:
        f = repo / "docs" / "parity" / f"{t}.md"
        if not f.exists():
            continue
        body = f.read_text(encoding="utf-8").splitlines()
        if body and body[0].startswith("# "):
            body = body[1:]
        body = [("#" + ln) if ln.startswith("#") else ln for ln in body]  # demote headings one level
        parts.append(f"\n## Task {t.upper()}: {TITLES.get(t, '')}\n" + "\n".join(body).strip() + "\n")
    parts.append(NOT_PORTED)
    text = "\n".join(parts)
    out.write_text(text, encoding="utf-8")
    names = [ln.strip() for ln in inv.read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    missing = [q for q in names
               if not re.search(r"(?<![\w.])(?:\w+\.)?" + re.escape(q.split(".", 1)[1]) + r"(?!\w)", text)]
    print(f"{len(names)} names, {len(missing)} missing")
    for m in missing:
        print("  ", m)


if __name__ == "__main__":
    main()
