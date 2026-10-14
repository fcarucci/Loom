"""ICC profiles: parsing, the installed-profile scan, the profile plan and the PSB profile.

Ported from Loom's profile handling: the constants and preferences (lib/StepsIcc.js:34-77),
parseIccProfile and iccDescription with the v2 ``desc`` and v4 ``mluc`` tags (:78-135),
iccProfileDirectories and the 2 MB file cap (:136-157), installedIccProfiles,
readIccProfileFile, profilesMatching and uniqueList (:158-224), profilePlan (:225-247),
and PROFILE_RGB_FILES, psbProfileBytes and isProPhotoFamily (:323-373).

Siril's changes: nothing here touches PixInsight or Siril. Every function that reads the
disk takes the reading as an argument (``list_dir``, ``read_bytes``, ``exists``), so the
folders and the file contents are injected. Assignment is made by Siril's ``icc_assign``
with a file path, or with the built-in ``sRGB`` profile when no file qualifies
(``assignment_for``).
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable, Mapping, NamedTuple, Optional, Sequence

PLATFORM_WINDOWS = "windows"
PLATFORM_MACOS = "macos"
PLATFORM_UNIX = "unix"  # any other platform, as Loom's Util.platform treats it

# Profile names as Loom uses them (lib/StepsIcc.js:35-40).
PROFILE_RGB = "ROMM RGB: ISO 22028-2:2013"  # ProPhoto
PROFILE_GRAY = "Generic Gray Profile"  # gamma 1.8
PROFILE_SRGB = "sRGB IEC61966-2.1"
BUILTIN_SRGB = "sRGB"  # Siril's built-in name for the sRGB profile

# Widest gamut first; each entry is (pattern, gamma of the space) (lib/StepsIcc.js:52-60).
RGB_PROFILE_PREFERENCE: list[tuple[re.Pattern[str], float]] = [
    (re.compile(r"^ROMM RGB", re.I), 1.8),
    (re.compile(r"ProPhoto", re.I), 1.8),
    (re.compile(r"BT\.?\s?2020|Rec\.?\s?2020", re.I), 2.2),
    (re.compile(r"Wide\s?Gamut RGB", re.I), 2.2),
    (re.compile(r"^Adobe RGB \(1998\)|Compatible with Adobe RGB", re.I), 2.2),
    (re.compile(r"^Display P3|DCI.{0,3}P3", re.I), 2.2),
    (re.compile(r"^sRGB", re.I), 2.2),
]

GRAY_PROFILE_PREFERENCE: dict[str, list[re.Pattern[str]]] = {
    "1.8": [re.compile(r"^Generic Gray Profile$", re.I), re.compile(r"Gray Gamma 1\.8", re.I)],
    "2.2": [re.compile(r"Gray Gamma 2\.2", re.I), re.compile(r"^sGray", re.I)],
}

# Profile classes that describe a colour space an image can be tagged with (lib/StepsIcc.js:62).
ICC_USABLE_CLASSES = ("mntr", "spac")

# Standard spaces are a few KB; printer LUTs are not candidates (lib/StepsIcc.js:156).
ICC_MAX_FILE_BYTES = 2 * 1024 * 1024

# Profile files to prefer for the PSB, in order (lib/StepsIcc.js:323-330).
PROFILE_RGB_FILES: tuple[str, ...] = (
    # macOS, installed by any Adobe application
    "/Library/Application Support/Adobe/Color/Profiles/Recommended/ProPhoto.icm",
    "/Library/Application Support/Adobe/Color/Profiles/ProPhoto.icm",
    # Windows
    "C:/Program Files/Common Files/Adobe/Color/Profiles/Recommended/ProPhoto.icm",
    "C:/Windows/System32/spool/drivers/color/ProPhoto.icm",
)

_PROPHOTO_FAMILY = re.compile(r"^ROMM RGB|ProPhoto", re.I)
_LINEAR = re.compile(r"linear", re.I)
_PROFILE_EXT = re.compile(r"\.(icc|icm)$", re.I)

ReadBytes = Callable[[str], Optional[bytes]]
ListDir = Callable[[str], Iterable[str]]


# --- byte access: out-of-range reads give 0, as Loom's byteAt does through the bitwise operators ---


def _byte(data: bytes, i: int) -> int:
    return data[i] if 0 <= i < len(data) else 0


def _u32(data: bytes, o: int) -> int:
    return (_byte(data, o) << 24) + (_byte(data, o + 1) << 16) + (_byte(data, o + 2) << 8) + _byte(data, o + 3)


def _str4(data: bytes, o: int) -> str:
    return "".join(chr(_byte(data, o + k)) for k in range(4))


# --- parsing ---


def _description_at(data: bytes, off: int) -> Optional[str]:
    """The text of the tag at ``off`` (``desc`` or ``mluc``), or None for any other type."""
    n = len(data)
    kind = _str4(data, off)
    if kind == "desc":
        count = _u32(data, off + 8)
        out: list[str] = []
        i = 0
        while i < count and off + 12 + i < n:
            c = _byte(data, off + 12 + i)
            if c == 0:
                break
            out.append(chr(c))
            i += 1
        return "".join(out)
    if kind != "mluc":
        return None
    records = _u32(data, off + 8)
    size = _u32(data, off + 12)
    base = off + 16
    # Only record positions inside the data can hold "en" (past the end every byte reads as zero).
    # With a zero record size all records share one position, so one check is enough.
    if n <= base:
        checks = 0
    elif size == 0:
        checks = min(records, 1)
    else:
        checks = min(records, (n - base + size - 1) // size)
    pick = base
    for r in range(checks):
        at = base + r * size
        if _byte(data, at) == 0x65 and _byte(data, at + 1) == 0x6E:  # "en"
            pick = at
            break
    length = _u32(data, pick + 4)
    start = off + _u32(data, pick + 8)
    units = bytearray()
    i = 0
    while i + 1 < length and start + i + 1 < n:  # an odd trailing byte is not a code unit
        units.append(_byte(data, start + i))
        units.append(_byte(data, start + i + 1))
        i += 2
    # Lone surrogates become U+FFFD so the text is always UTF-8 encodable.
    return bytes(units).decode("utf-16-be", errors="replace")


def icc_description(data: bytes) -> Optional[str]:
    """The English description from the profile's ``desc`` tag (v2 or v4), or None.

    None when the bytes are too short for a header and tag count, when there is no
    ``desc`` tag, or when its type is neither ``desc`` nor ``mluc``.
    """
    n = len(data)
    if n < 132:
        return None
    tags = _u32(data, 128)
    for t in range(tags):
        if 144 + 12 * t > n:
            break
        at = 132 + 12 * t
        if _str4(data, at) != "desc":
            continue
        return _description_at(data, _u32(data, at + 4))
    return None


def parse_icc_profile(data: bytes) -> Optional[dict[str, str]]:
    """The device class, colour space and English description of a profile, or None.

    Does not reject by class: the class is checked where profiles are matched (``profiles_matching``).
    """
    if data is None or len(data) < 132:
        return None
    description = icc_description(data)
    if description is None:
        return None
    return {
        "deviceClass": _str4(data, 12),
        "colorSpace": _str4(data, 16).strip(),
        "description": description,
    }


# --- folders ---


_WINDOWS_TOKENS = frozenset({"windows", "win32", "cygwin"})
_MACOS_TOKENS = frozenset({"macos", "darwin"})


def _env_get(env: Mapping[str, str], name: str) -> Optional[str]:
    """``env[name]``, with the name matched case-insensitively (Windows environment names are not case-sensitive)."""
    if name in env:
        return env[name]
    wanted = name.upper()
    for key in env:
        if str(key).upper() == wanted:
            return env[key]
    return None


def icc_profile_directories(platform: Optional[str], env: Mapping[str, str], home: Optional[str]) -> list[str]:
    """The folders PixInsight reads profiles from.

    ``platform`` takes Loom's names ("windows", "macos") and sys.platform spellings: "win32" and
    "cygwin" are Windows, "darwin" is macOS; anything else is unix. ``env`` supplies SystemRoot on
    Windows (default C:/Windows) and its name is matched case-insensitively, so ``os.environ`` itself
    can be passed. ``home`` adds the user's folders when it is non-empty. Adobe's folders are not
    listed: PixInsight does not load from them.
    """
    has_home = home is not None and len(str(home)) > 0
    plat = str(platform or "").lower()
    if plat in _WINDOWS_TOKENS:
        root = str(_env_get(env, "SystemRoot") or "C:/Windows").replace("\\", "/").rstrip("/")
        return [root + "/System32/spool/drivers/color"]
    if plat in _MACOS_TOKENS:
        mac = ["/System/Library/ColorSync/Profiles", "/Library/ColorSync/Profiles"]
        if has_home:
            mac.append(str(home) + "/Library/ColorSync/Profiles")
        return mac
    unix = ["/usr/share/color/icc", "/usr/local/share/color/icc"]
    if has_home:
        unix.extend([str(home) + "/.color/icc", str(home) + "/.local/share/icc"])
    return unix


# --- the installed profiles ---


def _read_profile_file(path: str, read_bytes: ReadBytes) -> Optional[dict[str, str]]:
    """Parse one file. ``read_bytes`` may return only the first ICC_MAX_FILE_BYTES + 1 bytes: a longer result is skipped."""
    try:
        data = read_bytes(path)
        if data is None or len(data) > ICC_MAX_FILE_BYTES:
            return None
        profile = parse_icc_profile(data)
    except Exception:
        return None
    if profile is None:
        return None
    profile["path"] = path
    return profile


def installed_icc_profiles(dirs: Sequence[str], list_dir: ListDir, read_bytes: ReadBytes) -> list[dict[str, str]]:
    """Every readable profile file in ``dirs``, in folder then listing order.

    ``list_dir(folder)`` gives the entry names (an error gives none); names starting with a dot
    are skipped, as Loom's directory listing skips them. Each result carries its ``path``.
    ``read_bytes`` must return at most ICC_MAX_FILE_BYTES + 1 bytes of a file (a longer result is
    skipped), so a large file is never read whole. The caller caches the result for the session if it wants to.
    """
    found: list[dict[str, str]] = []
    for folder in dirs:
        try:
            names = list(list_dir(folder))
        except Exception:
            names = []
        for name in names:
            if name.startswith(".") or not _PROFILE_EXT.search(name):
                continue
            profile = _read_profile_file(folder + "/" + name, read_bytes)
            if profile is not None:
                found.append(profile)
    return found


def profiles_matching(
    profiles: Sequence[Mapping[str, str]], space: str, patterns: Iterable[re.Pattern[str]]
) -> list[str]:
    """Descriptions of ``profiles`` in colour space ``space`` matching ``patterns``, in pattern order, without repeats.

    Only usable classes qualify, and descriptions containing "linear" are rejected.
    """
    out: list[str] = []
    profiles = list(profiles)
    patterns = list(patterns)
    for rx in patterns:
        for p in profiles:
            desc = p["description"]
            if (
                p["colorSpace"] == space
                and p["deviceClass"] in ICC_USABLE_CLASSES
                and not _LINEAR.search(desc)
                and rx.search(desc)
                and desc not in out
            ):
                out.append(desc)
    return out


def _unique(items: Iterable[str]) -> list[str]:
    seen: list[str] = []
    for x in items:
        if x not in seen:
            seen.append(x)
    return seen


def _paths_by_space(profiles: Sequence[Mapping[str, str]]) -> dict[str, dict[str, str]]:
    """The first file path for each usable (space, description), so a name in the plan can be assigned from its file."""
    paths: dict[str, dict[str, str]] = {"RGB": {}, "GRAY": {}}
    for p in profiles:
        space = p.get("colorSpace")
        path = p.get("path")
        if space not in paths or not path:
            continue
        if p["deviceClass"] not in ICC_USABLE_CLASSES or _LINEAR.search(p["description"]):
            continue
        paths[space].setdefault(p["description"], path)
    return paths


def profile_plan(installed: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    """The names to try, best first, for colour (``rgb``) and mono (``gray``) plates, and the file ``paths`` they map to.

    Mirrors lib/StepsIcc.js profilePlan. With nothing installed the preferred names are listed
    (Loom tries them blind); here they carry no path, so assignment falls back to sRGB.
    ``paths`` maps "RGB" and "GRAY" to {description: file path}.
    """
    profiles = list(installed)
    rgb = profiles_matching(profiles, "RGB", [pattern for pattern, _gamma in RGB_PROFILE_PREFERENCE])
    gamma = 1.8
    if rgb:
        for pattern, g in RGB_PROFILE_PREFERENCE:
            if pattern.search(rgb[0]):
                gamma = g
                break
    order = ["1.8", "2.2"] if gamma == 1.8 else ["2.2", "1.8"]
    gray_patterns = GRAY_PROFILE_PREFERENCE[order[0]] + GRAY_PROFILE_PREFERENCE[order[1]]
    gray = profiles_matching(profiles, "GRAY", gray_patterns)
    blind = len(profiles) == 0

    def has(name: str) -> bool:
        return blind or any(p["description"] == name for p in profiles)

    preferred_gray = [PROFILE_GRAY] if has(PROFILE_GRAY) else []
    gray_list = preferred_gray + gray if gamma == 1.8 else gray + preferred_gray
    rgb_list = ([PROFILE_RGB] if has(PROFILE_RGB) else []) + rgb + [PROFILE_SRGB]
    return {
        "rgb": _unique(rgb_list),
        "gray": _unique(gray_list),
        "paths": _paths_by_space(profiles),
    }


class Assignment(NamedTuple):
    """The assignment for one plate.

    target: what to pass to icc_assign: a file path, or BUILTIN_SRGB ("sRGB") when no file qualifies.
    name: the profile's description. Loom keeps it as rgbProfileInUse, so for a colour plate pass it
        as rgb_profile_in_use to psb_profile_bytes. For the built-in fallback it is PROFILE_SRGB, the
        name psb_profile_bytes embeds.
    skipped: the names tried before the chosen one (Loom's "not installed; closest available"). For
        the fallback, every name that was tried.
    warning: text for the fallback, None otherwise.
    """

    target: str
    name: str
    skipped: tuple[str, ...]
    warning: Optional[str]


def assignment_for(plate_is_colour: bool, plan: Mapping[str, Any]) -> Assignment:
    """The assignment for a plate. Colour plates walk ``plan["rgb"]`` and mono plates ``plan["gray"]``;
    the first name with an installed file wins. When none has a file the built-in sRGB is used, for
    mono plates too: on a mono image Siril assigns its Gray profile with the sRGB curve.
    """
    space = "RGB" if plate_is_colour else "GRAY"
    names = list(plan["rgb"] if plate_is_colour else plan["gray"])
    paths = plan.get("paths", {}).get(space, {})
    skipped: list[str] = []
    for name in names:
        path = paths.get(name)
        if path:
            return Assignment(path, name, tuple(skipped), None)
        skipped.append(name)
    kind = "colour" if plate_is_colour else "mono"
    listed = ", ".join(names) if names else "none"
    warning = f"no installed {kind} profile qualifies (tried {listed}); the built-in sRGB profile is used"
    return Assignment(BUILTIN_SRGB, PROFILE_SRGB, tuple(names), warning)


def assignment_log(label: str, assignment: Assignment) -> str:
    """Loom's assignment log line, e.g. "label -> name (X not installed; closest available)"."""
    line = f"{label} -> {assignment.name}"
    if assignment.skipped:
        line += f" ({', '.join(assignment.skipped)} not installed; closest available)"
    return line


def is_prophoto_family(name: Optional[str]) -> bool:
    """True for ROMM RGB and ProPhoto, which are one colour space under two names."""
    if name is None:
        return False
    return _PROPHOTO_FAMILY.search(str(name)) is not None


def psb_profile_bytes(
    rgb_profile_in_use: Optional[str],
    profile_bytes_named: Callable[[str], Optional[bytes]],
    read_bytes: ReadBytes,
    exists: Callable[[str], bool],
) -> tuple[Optional[bytes], Optional[str]]:
    """The profile bytes to embed in the PSB, and a warning when Adobe's ProPhoto file is missing.

    The profile in use wins when it is not ProPhoto-family (the plates carry it, so the PSB must
    too). Otherwise Adobe's ProPhoto file is read from PROFILE_RGB_FILES. Failing that,
    ``profile_bytes_named(PROFILE_RGB)`` supplies the same colour space under its ROMM name.
    """
    if rgb_profile_in_use and not is_prophoto_family(rgb_profile_in_use):
        return profile_bytes_named(rgb_profile_in_use), None
    for path in PROFILE_RGB_FILES:
        try:
            if not exists(path):
                continue
            data = read_bytes(path)
        except Exception:
            continue
        if data:
            return data, None
    warning = (
        "Adobe's ProPhoto profile was not found; the PSB will carry " + PROFILE_RGB + ", which is the same "
        "colour space under a different name; Photoshop will report a profile mismatch on open"
    )
    return profile_bytes_named(PROFILE_RGB), warning
