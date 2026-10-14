"""Output names: the layout order, unique ids and file names, and which results are kept.

Ported from Loom lib/Pipeline.js:2890-2908 (OUTPUT_ORDER, orderedOutputKeys),
lib/Util.js:342-353 (uniqueWindowId), lib/Pipeline.js:592-594 (publishId),
lib/Pipeline.js:204-208 (psbBaseName), lib/StepsSyqon.js:250-252 (the export file-name
sanitiser), and the keep rules of lib/Pipeline.js:2452-2760 (publishOutputs and its
helpers, read for which results survive a run). Pure: no window, no Siril call.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

# lib/Pipeline.js:2890-2895: known outputs keep this order in the layout.
OUTPUT_ORDER: tuple[str, ...] = (
    "L", "L_starless", "L_stars", "L_stars_low", "L_stars_high",
    "RGB", "RGB_starless", "RGB_stars",
    "HSO", "HSO_starless", "HSO_stars",
    "SHO", "SHO_starless", "SHO_stars",
    "HOO", "HOO_starless", "HOO_stars",
)

_OUTPUT_INDEX: dict[str, int] = {key: index for index, key in enumerate(OUTPUT_ORDER)}

# Palettes in the order main_result prefers them (lib/Pipeline.js palette wanted order).
PALETTES: tuple[str, ...] = ("SHO", "HOO", "HSO")
NARROWBAND: tuple[str, ...] = ("H", "S", "O")

_SAFE_CHAR = re.compile(r"[A-Za-z0-9_\-]")


def ordered_output_keys(keys: Sequence[str]) -> list[str]:
    """Known outputs in OUTPUT_ORDER first, then the others sorted; the input is not changed."""
    known = [key for key in keys if key in _OUTPUT_INDEX]
    rest = [key for key in keys if key not in _OUTPUT_INDEX]
    known.sort(key=_OUTPUT_INDEX.__getitem__)
    rest.sort()
    return known + rest


def unique_name(base: str, exists: Callable[[str], bool]) -> str:
    """``base`` when free, otherwise the first free ``base_<n>`` with n starting at 1."""
    if not exists(base):
        return base
    n = 1
    while exists(f"{base}_{n}"):
        n += 1
    return f"{base}_{n}"


def unique_file_name(folder: str | os.PathLike[str], name: str, ext: str = ".fits") -> str:
    """Full path ``<folder>/<name><ext>``, numbered like unique_name so an existing file is never overwritten."""
    folder_text = os.fspath(folder)

    def taken(stem: str) -> bool:
        return os.path.lexists(os.path.join(folder_text, stem + ext))

    return os.path.join(folder_text, unique_name(name, taken) + ext)


def publish_id(current: str, wanted: str, exists: Callable[[str], bool]) -> str:
    """``wanted`` when the window already has that id, otherwise a unique id based on it."""
    return wanted if current == wanted else unique_name(wanted, exists)


def psb_base_name(config: Mapping[str, Any] | None) -> str:
    """The project name trimmed, or "Loom" when it is empty or missing."""
    raw = config.get("projectName") if config else None
    name = str(raw).strip() if raw else ""
    return name if name else "Loom"


def export_file_name(name: str) -> str:
    """The name with every character outside A-Za-z0-9_- replaced by "_".

    A character outside the Basic Multilingual Plane becomes two underscores, as the
    JavaScript original replaces each UTF-16 code unit.
    """
    out: list[str] = []
    for char in str(name):
        if _SAFE_CHAR.match(char):
            out.append(char)
        else:
            out.append("__" if ord(char) > 0xFFFF else "_")
    return "".join(out)


def _has(available: Mapping[str, bool], name: str) -> bool:
    return bool(available.get(name))


def kept_results(available: Mapping[str, bool], config: Mapping[str, Any] | None) -> list[str]:
    """The result names a run keeps, given which results it produced and the options.

    ``available`` maps each produced result to True: L, L_stars, RGB, RGB_stars,
    RGB_linear, H, S, O, the palettes SHO/HOO/HSO with their _stars and _linear
    companions, and L_stars_low / L_stars_high. The rules (lib/Pipeline.js:2452-2760):

    * L, kept always. A split L is kept as L_starless and L_stars, else as L.
    * H, S, O, kept only when no palette was built; each split like L.
    * R, G and B are never kept.
    * RGB, split into RGB_starless and RGB_stars when it has stars.
    * A palette: its _starless (when split, else the plain name). Its stars are kept only
      when no broadband stars were kept, since RGB_stars already covers them.
    * RGB_linear (only with RGB) and palette _linear, only with keepLinear.
    * L_stars_low and L_stars_high, only with separateLStars.
    """
    cfg = config or {}

    def plate(key: str) -> list[str]:
        if not _has(available, key):
            return []
        if _has(available, key + "_stars"):
            return [key + "_starless", key + "_stars"]
        return [key]

    palettes = [name for name in PALETTES if _has(available, name)]
    rgb_stars_kept = _has(available, "RGB") and _has(available, "RGB_stars")

    kept: list[str] = plate("L")
    if not palettes:
        for key in NARROWBAND:
            kept += plate(key)

    if _has(available, "RGB"):
        if rgb_stars_kept:
            kept += ["RGB_starless", "RGB_stars"]
        else:
            kept.append("RGB")

    for palette in palettes:
        if _has(available, palette + "_stars"):
            kept.append(palette + "_starless")
            if not rgb_stars_kept:
                kept.append(palette + "_stars")
        else:
            kept.append(palette)

    if cfg.get("keepLinear"):
        # publishRGB returns before its linear copy when there is no RGB window (lib/Pipeline.js:2614-2640)
        if _has(available, "RGB") and _has(available, "RGB_linear"):
            kept.append("RGB_linear")
        for palette in palettes:
            if _has(available, palette + "_linear"):
                kept.append(palette + "_linear")

    if cfg.get("separateLStars"):
        for low_high in ("L_stars_low", "L_stars_high"):
            if _has(available, low_high):
                kept.append(low_high)

    return ordered_output_keys(kept)


def main_result(names: Iterable[str]) -> str | None:
    """The result to show first: RGB_starless or RGB, then the first palette (its _starless, else plain),
    then L_starless, L, H, S, O. None when none of them is among ``names``."""
    present = set(names)
    candidates: list[str] = ["RGB_starless", "RGB"]
    for palette in PALETTES:
        if palette + "_starless" in present or palette in present:
            candidates.append(palette + "_starless" if palette + "_starless" in present else palette)
            break
    candidates += ["L_starless", "L", "H", "S", "O"]
    for candidate in candidates:
        if candidate in present:
            return candidate
    return None
