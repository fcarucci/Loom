"""Loom's input rules: which file is which channel, selection checks, master ranking, palettes and camera mapping.

Ported from Loom's JavaScript (read-only reference, tag v0.4.1): script/lib/Util.js and script/lib/Pipeline.js, cited
as file:line where a rule is ported. Each ``snake_case`` function here is the port of the JavaScript function of the
same name; the values are pinned by tests/oracle/loom_channels.json.

Functions take plain data and never open a file, with two exceptions: ``read_header`` (one header read) and
``scan_masters`` (a folder listing plus one header read per candidate). Both take their disk access as injectable
callables, so the rules are testable without masters on disk.
"""

from __future__ import annotations

import math
import os
import re
from typing import Any, Callable, Mapping, Optional, Sequence

from loomsiril import io_fits
from loomsiril.jsmath import js_string, parse_float
from loomsiril.loom import xisf

# ---------------------------------------------------------------------------
# Channels (Util.js:436-449)
# ---------------------------------------------------------------------------

CHANNELS: list[str] = ["L", "R", "G", "B", "H", "S", "O"]
BROADBAND: list[str] = ["L", "R", "G", "B"]
RGB_GROUP: list[str] = ["R", "G", "B"]
NARROWBAND: list[str] = ["H", "S", "O"]


def is_broadband(key: str) -> bool:
    return key in BROADBAND


# ---------------------------------------------------------------------------
# Header keywords (Util.js:447-460)
# ---------------------------------------------------------------------------


def keyword_value(keywords: Sequence[Mapping[str, Any]], name: str) -> Optional[str]:
    """The bare value of the first keyword called ``name``, or None when absent or blank.

    FITS string values arrive single-quoted and blank-padded; the quotes and padding go.
    """
    for kw in keywords:
        if kw["name"] == name:
            v = str(kw["value"]).strip()
            if len(v) >= 2 and v[0] == "'" and v[-1] == "'":
                v = v[1:-1]
            v = v.strip()
            return v if len(v) > 0 else None
    return None


# ---------------------------------------------------------------------------
# Selection checks (Util.js:603-669)
# ---------------------------------------------------------------------------


def _has_path(p: Any) -> bool:
    return isinstance(p, str) and len(p) > 0


def channel_supplied(paths: Mapping[str, Any], views: Mapping[str, Any], k: str) -> bool:
    """A channel is supplied by a file path or an open view (Util.js:613-617)."""
    if _has_path(paths.get(k)):
        return True
    v = views.get(k)
    return v is not None and len(js_string(v)) > 0


def channel_group_problems(paths: Mapping[str, Any], views: Mapping[str, Any]) -> list[str]:
    """The RGB group is all or nothing; any narrowband subset is fine; there must be something (Util.js:623-643).

    Logical channels count: a colour master supplies R, G, B (OSC) or H, O (OSCNB).
    """
    from loomsiril.loom import osc

    problems: list[str] = []
    supplied = osc.supplied_channels({"paths": paths, "views": views})
    rgb_present = [c for c in RGB_GROUP if c in supplied]
    rgb_missing = [c for c in RGB_GROUP if c not in supplied]
    if rgb_present and rgb_missing:
        problems.append("Incomplete RGB set: missing " + ", ".join(rgb_missing) +
                        ". Supply all three or none.")
    nb_count = sum(1 for k in NARROWBAND if k in supplied)
    if not rgb_present and nb_count == 0:
        problems.append("Nothing to do: supply R, G and B, or at least one of H, S, O")
    return problems


def _path_key(p: str) -> str:
    """The identity of a path for the duplicate check.

    Paths compare exactly as configured, as Loom does: never case-folded or resolved. A Windows-style path (a
    backslash, or a drive letter) only has its separators unified to "/".
    """
    if "\\" in p or re.match(r"^[A-Za-z]:", p):
        return p.replace("\\", "/")
    return p


def duplicate_file_problems(paths: Mapping[str, Any]) -> list[str]:
    """The same file named for two channels or colour slots, reported once per repeat (Util.js:646-661).

    Paths are compared through ``_path_key``, so C:\\a\\x.xisf and C:/a/x.xisf name the same file. The message
    prints the path as the second channel gave it, as Loom does.
    """
    from loomsiril.loom import osc

    problems: list[str] = []
    seen: dict[str, str] = {}
    for k in CHANNELS + osc.COLOUR_SLOTS:
        p = paths.get(k)
        if not _has_path(p):
            continue
        key = _path_key(p)
        if key in seen:
            problems.append("Same file selected for " + seen[key] + " and " + k + ": " + p)
        else:
            seen[key] = k
    return problems


def validate_selection(paths: Mapping[str, Any], views: Optional[Mapping[str, Any]] = None) -> list[str]:
    """Every structural problem with a channel selection; an empty list means the selection is valid (Util.js:603-608)."""
    v: Mapping[str, Any] = views or {}
    return channel_group_problems(paths, v) + duplicate_file_problems(paths)


# ---------------------------------------------------------------------------
# Filter -> channel (Util.js:670-712)
# ---------------------------------------------------------------------------

FILTER_LETTERS: dict[str, str] = {"l": "L", "r": "R", "g": "G", "b": "B", "h": "H", "s": "S", "o": "O"}

# Tried in order: narrowband before broadband, because "sii" and "oiii" would be misread by a naive "i" test.
FILTER_NAMES: list[dict[str, Any]] = [
    {"token": "halpha", "exact": "ha", "channel": "H"},
    {"token": "sii", "exact": "s2", "channel": "S"},
    {"token": "oiii", "exact": "o3", "channel": "O"},
    {"token": "lum", "exact": None, "channel": "L"},
    {"token": "red", "exact": None, "channel": "R"},
    {"token": "green", "exact": None, "channel": "G"},
    {"token": "blue", "exact": None, "channel": "B"},
]

_NON_ALNUM = re.compile(r"[^a-z0-9]")


def channel_from_filter(filter_value: Any) -> Optional[str]:
    """The channel a FILTER keyword value names, or None. Case and punctuation are ignored (Util.js:670-691)."""
    if filter_value is None:
        return None
    f = _NON_ALNUM.sub("", js_string(filter_value).lower())
    if not f:
        return None
    if f in FILTER_LETTERS:
        return FILTER_LETTERS[f]
    for rule in FILTER_NAMES:
        if rule["token"] in f or f == rule["exact"]:
            return rule["channel"]
    return None


# ---------------------------------------------------------------------------
# The saved selection list (Util.js:713-748)
# ---------------------------------------------------------------------------


def serialize_entries(entries: Sequence[Mapping[str, str]]) -> str:
    """One ``source<TAB>ref`` record per line."""
    return "\n".join(e["source"] + "\t" + e["ref"] for e in entries)


def deserialize_entries(text: Optional[str], warn: Optional[Callable[[str], None]] = None) -> list[dict[str, str]]:
    """The file entries of a saved list.

    Only ``file`` records are restored. A ``view`` record refers to an open PixInsight view that does not exist in a
    Siril session, so it is dropped and reported through ``warn`` when given. Blank, tab-less and empty-reference
    lines are skipped silently.
    """
    result: list[dict[str, str]] = []
    if not text:
        return result
    for line in str(text).split("\n"):
        if line == "":
            continue
        tab = line.find("\t")
        if tab < 0:
            continue
        source = line[:tab]
        ref = line[tab + 1:]
        if not ref:
            continue
        if source == "file":
            result.append({"source": "file", "ref": ref})
        elif source == "view" and warn is not None:
            warn("saved selection: view entry " + repr(ref) + " dropped (only file entries are restored)")
    return result


# ---------------------------------------------------------------------------
# Drizzle factor (Util.js:749-837)
# ---------------------------------------------------------------------------

NATIVE_PIXEL_SIZE: float = 3.76  # um, the ASI2600MM's native pitch


def _js_float(x: Any) -> float:
    """``parseFloat`` of a value: a number passes through, text is read by its leading number, anything else is NaN."""
    if x is None or isinstance(x, bool):
        return math.nan
    if isinstance(x, (int, float)):
        return float(x)
    return parse_float(str(x))


def factor_label(factor: float) -> str:
    """"Nx" for a clean integer factor of at least 2 (within 0.05), else ""."""
    f = float(factor)
    if not math.isfinite(f):
        return ""
    rounded = math.floor(f + 0.5)  # Math.round
    if rounded < 2 or abs(f - rounded) > 0.05:
        return ""
    return str(rounded) + "x"


def drizzle_label(xpixsz: Any) -> str:
    """The XPIXSZ rule alone: the drizzle factor the recorded pixel size implies (Util.js:761-766)."""
    v = _js_float(xpixsz)
    if not math.isfinite(v) or v <= 0:
        return ""
    return factor_label(NATIVE_PIXEL_SIZE / v)


def unescape_history(history: Any) -> str:
    """A ProcessingHistory as plain XML text: it may be stored escaped (Util.js:769-772)."""
    s = "" if history is None else str(history)
    return s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", "\"").replace("&amp;", "&")


_DRIZZLE_INSTANCE = re.compile(r'<instance\s+class="DrizzleIntegration"[\s\S]*?(?=<instance\s|\Z)')
_SCALE_PARAMETER = re.compile(r'<parameter\s+id="scale"\s+value="([^"]*)"')
_IMAGE_INTEGRATION_INSTANCE = re.compile(r'<instance\s+class="ImageIntegration"')


def drizzle_scale_from_history(history: Any) -> Optional[float]:
    """The scale of the LAST DrizzleIntegration instance: None when there is none, NaN when it has no readable scale.

    Only that instance's own text is searched, so another process's "scale" is not read (Util.js:779-788).
    """
    h = unescape_history(history)
    last: Optional[str] = None
    for m in _DRIZZLE_INSTANCE.finditer(h):
        last = m.group(0)
    if last is None:
        return None
    sm = _SCALE_PARAMETER.search(last)
    return parse_float(sm.group(1)) if sm else math.nan


def integration_drizzle_label(history: Any, signature: Any, xpixsz: Any) -> str:
    """The drizzle label of a master from its integration metadata (Util.js:800-817).

    1. a DrizzleIntegration scale: that factor (a clean integer of at least 2, else "")
    2. a DrizzleIntegration signature without a scale: the XPIXSZ factor, else "drizzle"
    3. an ImageIntegration signature or history: not drizzled ("")
    4. no metadata: the XPIXSZ rule
    """
    scale = drizzle_scale_from_history(history)
    if scale is not None and math.isfinite(scale):
        return factor_label(scale)
    sig = "" if signature is None else str(signature)
    if scale is not None or re.match(r"process=DrizzleIntegration", sig):
        return drizzle_label(xpixsz) or "drizzle"
    if re.match(r"process=ImageIntegration", sig) or _IMAGE_INTEGRATION_INSTANCE.search(unescape_history(history)):
        return ""
    return drizzle_label(xpixsz)


def master_drizzle_label(info: Optional[Mapping[str, Any]]) -> str:
    """The drizzle label of a header read as ``read_header`` returns it (Util.js:819-825)."""
    if info is None:
        return ""
    return integration_drizzle_label(info.get("history"), info.get("signature"),
                                     keyword_value(info.get("keywords") or [], "XPIXSZ"))


# ---------------------------------------------------------------------------
# Camera -> QE curve (Util.js:838-890, 1401-1438)
# ---------------------------------------------------------------------------

IDEAL_QE_CURVE_NAME = "Ideal QE curve"

# Loom's table: normalised INSTRUME text (lowercase, alphanumeric) that contains a token -> PixInsight curve name.
CAMERA_QE_CURVES: list[tuple[tuple[str, ...], str]] = [
    (("asi2600", "asi6200", "asi533", "asi094", "qhy268", "qhy600",
      "imx571", "imx455", "imx461", "imx533", "imx411"), "Sony IMX411/455/461/533/571"),
    (("asi1600", "mn34230"), "Panasonic MN34230 (ASI1600MM)"),
    (("asi178", "imx178"), "Sony IMX178"),
    (("asi183", "imx183"), "Sony IMX183"),
    (("asi294", "imx492"), "Sony IMX492"),
    (("asi585", "imx585"), "Sony IMX585"),
    (("kaf16200",), "KAF-16200"),
    (("kaf16803",), "KAF-16803"),
    (("kaf8300",), "KAF-8300"),
]


def _normalised(text: Any) -> str:
    return _NON_ALNUM.sub("", js_string(text).lower())


def qe_curve_name_for_camera(instrume: Any) -> Optional[str]:
    """Loom's PixInsight QE curve name for a camera's INSTRUME, or None when the sensor is not in the table."""
    if instrume is None:
        return None
    n = _normalised(instrume)
    if not n:
        return None
    for tokens, curve in CAMERA_QE_CURVES:
        for tok in tokens:
            if tok in n:
                return curve
    return None


# The same matching, mapped to the sensor names of Siril 1.4.4's SPCC database. Patterns are tried in Loom's order
# against the normalised INSTRUME text; a camera that matches none is "Generic mono sensor".
GENERIC_SENSOR = "Generic mono sensor"

SPCC_SENSOR_TABLE: dict[str, str] = {
    r"asi2600|asi6200|asi533|asi094|qhy268|qhy600|imx571|imx455|imx461|imx533|imx411": "Sony IMX411/455/461/533/571",
    r"asi1600|mn34230": "Panasonic MN34230ALJ",
    r"asi178|imx178": "Sony IMX178",
    r"asi183|imx183": "Sony IMX183",
    r"asi294|imx492": "Sony IMX492/294",
    r"asi585|imx585": "Sony IMX585",
    r"kaf16200": "KAF16200",
    r"kaf16803": "KAF16803",
    r"kaf8300": "KAF8300",
}


def spcc_sensor_for(instrume: Any) -> str:
    """The Siril SPCC sensor name for a camera's INSTRUME value; "Generic mono sensor" when unknown."""
    if instrume is None:
        return GENERIC_SENSOR
    n = _normalised(instrume)
    if not n:
        return GENERIC_SENSOR
    for pattern, sensor in SPCC_SENSOR_TABLE.items():
        if re.search(pattern, n):
            return sensor
    return GENERIC_SENSOR


def common_instrument(values: Sequence[Any]) -> Optional[str]:
    """The camera every non-blank value names, or None when they disagree or none is given (Util.js:1412-1427)."""
    seen: Any = None
    for v in values:
        if v is None or v == "":
            continue
        if seen is None:
            seen = v
        elif seen != v:
            return None
    return seen


# ---------------------------------------------------------------------------
# Narrowband palettes (Util.js:1461-1557)
# ---------------------------------------------------------------------------

PALETTES: dict[str, list[str]] = {
    "SHO": ["S", "H", "O"],
    "HOO": ["H", "O", "O"],
    "HSO": ["H", "S", "O"],
}

NARROWBAND_NM: dict[str, float] = {"H": 656.28, "S": 671.60, "O": 500.70}


def palette_names() -> list[str]:
    return list(PALETTES.keys())


def palette_wavelengths(palette: str) -> Optional[list[dict[str, Any]]]:
    """The (channel, nm) pairs a palette presents to SPCC, in R, G, B order; None for an unknown palette."""
    mapping = PALETTES.get(palette)
    if mapping is None:
        return None
    out: list[dict[str, Any]] = []
    for ch in mapping:
        nm = NARROWBAND_NM.get(ch)
        if nm is None:
            return None
        out.append({"channel": ch, "nm": nm})
    return out


def palette_channels(palette: str) -> Optional[list[str]]:
    """The distinct channels a palette needs, in first-use order; None for an unknown palette."""
    mapping = PALETTES.get(palette)
    if mapping is None:
        return None
    out: list[str] = []
    for ch in mapping:
        if ch not in out:
            out.append(ch)
    return out


def palette_missing(palette: str, available: Sequence[str]) -> Optional[list[str]]:
    """The channels a palette needs that ``available`` lacks; [] means it can be built; None for an unknown palette."""
    need = palette_channels(palette)
    if need is None:
        return None
    return [ch for ch in need if ch not in available]


# ---------------------------------------------------------------------------
# Master selection (Util.js:1558-1645)
# ---------------------------------------------------------------------------


def is_autocrop_name(name: Any) -> bool:
    """Autocrop is not recorded in metadata, so the file name is the only source."""
    return re.search("autocrop", "" if name is None else str(name), re.IGNORECASE) is not None


def is_master_light(name: Any, imagetyp: Any) -> bool:
    """IMAGETYP decides when present; the name prefix is the fallback for files that lack it."""
    if imagetyp is not None and str(imagetyp) != "":
        return re.search("light", str(imagetyp), re.IGNORECASE) is not None
    return re.match("masterLight", "" if name is None else str(name), re.IGNORECASE) is not None


def master_variant_rank(drizzle_label_value: Any, autocrop: Any) -> int:
    """3 drizzled and autocropped, 2 drizzled, 1 autocropped, 0 plain."""
    d = drizzle_label_value is not None and str(drizzle_label_value) != ""
    a = bool(autocrop)
    if d and a:
        return 3
    if d:
        return 2
    if a:
        return 1
    return 0


_FILTER_TOKEN = re.compile(r"FILTER-([A-Za-z][A-Za-z0-9]*)", re.IGNORECASE)
_DRIZZLE_TOKEN = re.compile(r"drizzle_([0-9]+)x", re.IGNORECASE)


def parse_master_name(name: Any) -> Optional[dict[str, Any]]:
    """What a master's FILE NAME claims: filter, channel, drizzle and autocrop. A pre-filter only.

    None when the name carries no FILTER token. The FILTER keyword in the header stays authoritative (Util.js:1610-1622).
    """
    n = "" if name is None else str(name)
    m = _FILTER_TOKEN.search(n)
    if m is None:
        return None
    dm = _DRIZZLE_TOKEN.search(n)
    return {
        "filter": m.group(1),
        "channel": channel_from_filter(m.group(1)),
        "drizzle": (dm.group(1) + "x") if dm else "",
        "autocrop": is_autocrop_name(n),
    }


def select_masters(candidates: Sequence[Optional[Mapping[str, Any]]]) -> dict[str, dict[str, Any]]:
    """The best master per channel: highest variant rank, then the most recent ``mtime`` (Util.js:1625-1643).

    Each candidate is a mapping with ``channel``, ``drizzle``, ``autocrop`` and ``mtime`` (plus any other fields).
    Returns the winning candidate per channel as a copy carrying its ``rank``; entries without a channel are ignored.
    """
    best: dict[str, dict[str, Any]] = {}
    for c in candidates:
        if not c or not c.get("channel"):
            continue
        rank = master_variant_rank(c.get("drizzle"), c.get("autocrop"))
        cur = best.get(c["channel"])
        if (cur is None or rank > cur["rank"] or
                (rank == cur["rank"] and (c.get("mtime") or 0) > (cur.get("mtime") or 0))):
            winner = dict(c)
            winner["rank"] = rank
            best[c["channel"]] = winner
    return best


# ---------------------------------------------------------------------------
# Project name (Pipeline.js:204-260)
# ---------------------------------------------------------------------------

GENERIC_FOLDERS: list[str] = ["master", "masters", "integration", "integrations",
                              "calibrated", "registered", "drizzle", "autocrop",
                              "light", "lights", "output", "outputs", "tiff",
                              "cache", "tmp", "temp"]


def psb_base_name(config: Optional[Mapping[str, Any]]) -> str:
    """The project name, trimmed, or "Loom" when there is none (Pipeline.js:204-208)."""
    raw = config.get("projectName") if config else None
    n = str(raw).strip() if raw else ""
    return n if len(n) > 0 else "Loom"


def extract_directory(path: str) -> str:
    """The directory part of a path, as PixInsight's File.extractDirectory gives it.

    PJSR's File API speaks forward slashes on every platform, so backslashes are read as separators: they are
    replaced by "/", and the directory is everything before the last "/" ("" when there is none).
    """
    p = path.replace("\\", "/")
    i = p.rfind("/")
    return "" if i < 0 else p[:i]


def project_name_from_path(path: Any) -> str:
    """The name of the folder a master lives in, skipping generic container folders (Pipeline.js:226-244).

    Both "/" and "\\" separate folders (see ``extract_directory``), so Windows, mixed and UNC paths resolve alike.
    """
    if not path or str(path) == "":
        return ""
    d = extract_directory(str(path))
    for part in reversed(d.split("/")):
        if part == "":
            continue
        if part.lower() in GENERIC_FOLDERS:
            continue
        return part
    return ""


def project_name_for(config: Optional[Mapping[str, Any]]) -> str:
    """The project name guessed from the first source path of the run, in channel order (Pipeline.js:247-260)."""
    if not config or not config.get("paths"):
        return ""
    paths = config["paths"]
    for k in CHANNELS:
        p = paths.get(k)
        if p and str(p) != "":
            n = project_name_from_path(p)
            if n:
                return n
    return ""


# ---------------------------------------------------------------------------
# Narrowband supply and palette reports (Pipeline.js:95-149)
# ---------------------------------------------------------------------------


def narrowband_supplied(config: Mapping[str, Any]) -> list[str]:
    """The narrowband channels the run supplies, as files, open views or the members of a colour master."""
    from loomsiril.loom import osc

    supplied = osc.supplied_channels(config)
    return [k for k in NARROWBAND if k in supplied]


def wanted_palettes(config: Mapping[str, Any]) -> list[str]:
    """The saved palettes this run can build: every channel the palette needs is supplied."""
    have = narrowband_supplied(config)
    out: list[str] = []
    for pal in config.get("palettes") or []:
        missing = palette_missing(pal, have)
        if missing is not None and len(missing) == 0:
            out.append(pal)
    return out


def report_skipped_palettes(config: Mapping[str, Any]) -> list[str]:
    """The warning messages for saved palettes this run leaves out. Nothing is reported when no narrowband is supplied."""
    have = narrowband_supplied(config)
    messages: list[str] = []
    for pal in config.get("palettes") or []:
        missing = palette_missing(pal, have)
        if missing is not None and len(missing) == 0:
            continue
        if missing is None:
            messages.append("the " + pal + " palette is skipped: it is not a palette Loom knows")
        elif len(have) > 0:
            messages.append("the " + pal + " palette is skipped: it needs " + " and ".join(missing) + ", which " +
                            ("was" if len(missing) == 1 else "were") + " not supplied")
    return messages


def inherit_instrument(chans: Mapping[str, dict[str, Any]]) -> list[str]:
    """Give each channel with no INSTRUME the camera the other channels name, when they agree (Pipeline.js:1780-1798).

    Mutates the channel records in place (``instrume`` and ``instrumeInherited``) and returns the log messages.
    A colour master's channels (records with a ``group``) neither give nor take a camera: nothing changes.
    """
    keys = list(chans.keys())
    if any(chans[k].get("group") for k in keys):
        return []
    common = common_instrument([chans[k].get("instrume") for k in keys])
    logs: list[str] = []
    if common is not None:
        for k in keys:
            cc = chans[k]
            if cc.get("instrume") is None or cc.get("instrume") == "":
                cc["instrume"] = common
                cc["instrumeInherited"] = True
                logs.append(cc["key"] + " has no INSTRUME; taking '" + common +
                            "' from the other masters of this session")
    return logs


# ---------------------------------------------------------------------------
# Header reads (the only functions here that touch the disk, apart from scan_masters)
# ---------------------------------------------------------------------------

_FITS_BLOCK = 2880
_MAX_HDUS = 64  # extensions looked at for a tile-compressed image before giving up


def _fits_value_text(v: Any) -> str:
    """A parsed FITS value as the quoted or bare text Loom's keyword() would see."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "T" if v else "F"
    if isinstance(v, str):
        return "'" + v + "'"
    if isinstance(v, float):
        return repr(v)
    return str(v)


def _read_hdu_header(fh: Any, path: str) -> dict[str, Any]:
    """One FITS header read block by block up to its END card, leaving ``fh`` at the start of the HDU's data."""
    data = b""
    while True:
        block = fh.read(_FITS_BLOCK)
        if len(block) < _FITS_BLOCK:
            raise ValueError(f"FITS header has no END card: {path}")
        data += block
        if any(block[c:c + 8].rstrip() == b"END" for c in range(0, _FITS_BLOCK, 80)):
            return io_fits._parse_header(data)[0]


def _int_of(header: Mapping[str, Any], key: str) -> Optional[int]:
    v = header.get(key)
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _data_size(header: Mapping[str, Any]) -> int:
    """The bytes of an HDU's data part, padding included (FITS standard 4.4.1.1 and 7.3)."""
    def non_negative(key: str, default: int) -> int:
        v = _int_of(header, key)
        return default if v is None else max(0, v)  # a corrupt negative size must never move the file position back

    naxis = min(non_negative("NAXIS", 0), 999)
    if naxis == 0:
        return 0
    count = 1
    for i in range(1, naxis + 1):
        count *= non_negative(f"NAXIS{i}", 0)
    bits = abs(_int_of(header, "BITPIX") or 8)
    size = bits // 8 * non_negative("GCOUNT", 1) * (count + non_negative("PCOUNT", 0))
    return size + (-size % _FITS_BLOCK)


def _fits_keywords(header: Mapping[str, Any]) -> list[dict[str, str]]:
    return [{"name": k, "value": _fits_value_text(v)} for k, v in header.items()]


def _compressed_image_header(fh: Any, path: str, primary: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """The header of the first tile-compressed image extension (a BINTABLE with ZIMAGE = T) after the primary HDU.

    The data of every HDU passed over is skipped by its size, never read. None when there is no such extension.
    """
    try:
        fh.seek(_data_size(primary), 1)
        for _ in range(_MAX_HDUS):
            ext = _read_hdu_header(fh, path)
            if ext.get("XTENSION") == "BINTABLE" and ext.get("ZIMAGE") is True:
                return ext
            fh.seek(_data_size(ext), 1)
    except (ValueError, OSError):  # a corrupt or truncated file: no compressed image to report
        return None
    return None


def _read_fits_header(path: str) -> dict[str, Any]:
    """The header of a FITS file, read block by block up to END. No pixels are read.

    A plain image gives the primary header. A tile-compressed one (``.fz``) has an empty primary and its image in the
    first compressed BINTABLE: that header gives width, height and channels from ZNAXIS1-3, and its keywords come
    after the primary's, so they win.
    """
    with open(path, "rb") as fh:
        header = _read_hdu_header(fh, path)
        keywords = _fits_keywords(header)
        width, height = _int_of(header, "NAXIS1"), _int_of(header, "NAXIS2")
        naxis, naxis3 = _int_of(header, "NAXIS") or 0, _int_of(header, "NAXIS3")
        channels = naxis3 if naxis >= 3 and naxis3 is not None else 1
        ext = _compressed_image_header(fh, path, header) if naxis == 0 else None
    if ext is not None:
        merged = {k["name"]: k for k in keywords}
        for k in _fits_keywords(ext):
            merged[k["name"]] = k
        keywords = list(merged.values())
        width, height = _int_of(ext, "ZNAXIS1"), _int_of(ext, "ZNAXIS2")
        znaxis, znaxis3 = _int_of(ext, "ZNAXIS") or 0, _int_of(ext, "ZNAXIS3")
        channels = znaxis3 if znaxis >= 3 and znaxis3 is not None else 1
    return {"format": "fits", "keywords": keywords, "history": None, "signature": None,
            "width": width, "height": height, "channels": channels, "notes": []}


def read_header(path: str) -> dict[str, Any]:
    """The header fields a master needs, read without decoding pixels.

    Returns ``{"format", "keywords": [{"name", "value"}], "history", "signature", "width", "height", "channels"}``.
    ``channels`` is NAXIS3 of a FITS image (1 below three axes), ZNAXIS3 of a tile-compressed one, and the third part
    of an XISF geometry. ``history`` and ``signature`` are the XISF properties ``PixInsight:ProcessingHistory`` and
    ``PCL:Signature:Integration``; FITS has neither. Raises ValueError for anything that is neither XISF nor FITS.
    """
    with open(path, "rb") as fh:
        sig = fh.read(8)
    if sig == xisf.XISF_SIGNATURE:
        hdr = xisf.read_xisf_header(path)
        geometry = hdr.geometry
        return {
            "format": "xisf",
            "keywords": [{"name": n, "value": v} for n, v, _c in hdr.keywords],
            "history": hdr.properties.get("PixInsight:ProcessingHistory"),
            "signature": hdr.properties.get("PCL:Signature:Integration"),
            "width": geometry[0] if geometry else None,
            "height": geometry[1] if geometry else None,
            "channels": geometry[2] if geometry else 1,
            "notes": list(hdr.notes),
        }
    if sig.startswith(b"SIMPLE"):
        return _read_fits_header(path)
    raise ValueError("not an XISF or FITS file: " + str(path))


def _is_master_file_name(name: str) -> bool:
    return re.search(r"\.(xisf|fits?|fit|fz)$", name, re.IGNORECASE) is not None


def _is_calibration_name(name: str) -> bool:
    return re.match(r"master(Flat|Dark|Bias)", name, re.IGNORECASE) is not None


def _channel_count(info: Mapping[str, Any]) -> int:
    """The channel count of a header read; a header that does not say is one channel."""
    n = info.get("channels")
    return 1 if n is None else n


def _colour_entry(rec: Mapping[str, Any], info: Mapping[str, Any], slot: Optional[str], filter_value: Optional[str],
                  kws: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "path": rec["path"],
        "name": rec["name"],
        "filter": filter_value,
        "instrume": keyword_value(kws, "INSTRUME"),
        "channel": slot,
        "colour": True,
        "numberOfChannels": 3,
        "width": info.get("width"),
        "height": info.get("height"),
        "drizzle": master_drizzle_label(info),
        "autocrop": rec.get("autocrop", False),
        "mtime": rec["mtime"],
    }


def scan_masters(
    folder: str,
    read_header: Optional[Callable[[str], Optional[dict[str, Any]]]] = None,
    stat: Optional[Callable[[str], float]] = None,
    list_dir: Optional[Callable[[str], Sequence[str]]] = None,
    is_dir: Optional[Callable[[str], bool]] = None,
    choices: Optional[Mapping[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Pick the best master per channel, and per colour slot, from a WBPP master folder (UI.js scanMasterFolder).

    Phase 1 ranks candidates by their file names alone; calibration masters are skipped by name. A name that carries
    a colour mark (``_color``, ``_colour``, ``_RGB``) is a colour candidate, collected apart from the mono winners;
    its header decides. Files whose names carry no filter are opened only when some mono slot or colour slot has no
    named winner, and fill only those slots. Phase 2 reads the header of each winner and of every colour candidate:
    the channel count (1 or 3) decides mono or colour, FILTER the channel or the colour slot (through ``choices``,
    the remembered kinds), and a master must be a light (IMAGETYP).

    Returns None when ``folder`` is not a directory. Otherwise a dict with ``confirmed`` (mono channel -> entry),
    ``colour`` (``{"OSC": entry or None, "OSCNB": entry or None}``), ``unresolved`` (colour entries whose kind is
    still to be chosen), ``corrected`` (name-versus-header disagreements), ``skipped`` (the count of winners that
    were not lights plus the files that could not be read), ``unreadable`` (one line with the reason for each file
    that could not be read or decoded) and ``total``. A file whose header cannot be read, or whose name cannot be
    decoded, is reported and skipped, never raised. Directories are skipped, even when their names end in .fits.
    The default ``read_header``, ``stat``, ``list_dir`` and ``is_dir`` use the disk; tests inject their own.
    """
    from loomsiril.loom import osc

    reader = read_header or _read_header_or_none
    mtime_of = stat or _mtime
    listing = list_dir or os.listdir
    directory = is_dir or os.path.isdir
    if not directory(folder):
        return None

    unreadable: list[str] = []
    scan_total = 0
    named: list[dict[str, Any]] = []
    colour_named: list[dict[str, Any]] = []
    unnamed: list[dict[str, Any]] = []
    for name in sorted(listing(folder)):
        try:
            if name.startswith(".") or not _is_master_file_name(name):
                continue
            path = os.path.join(folder, name)
            if directory(path):
                continue
            scan_total += 1
            if _is_calibration_name(name):
                continue
            rec: dict[str, Any] = {"path": path, "name": name, "mtime": mtime_of(path)}
            colour_name = osc.parse_colour_master_name(name)
            if colour_name["colour"]:
                rec.update(filter=colour_name["filter"], colour=True, drizzle=colour_name["drizzle"],
                           autocrop=colour_name["autocrop"], numberOfChannels=3)
                colour_named.append(rec)
                continue
            parsed = parse_master_name(name)
            if parsed is not None and parsed["channel"] is not None:
                rec.update(channel=parsed["channel"], drizzle=parsed["drizzle"], autocrop=parsed["autocrop"],
                           colour=False)
                named.append(rec)
            else:
                unnamed.append(rec)
        except (UnicodeError, ValueError, OSError) as exc:
            unreadable.append(repr(name) + ": name could not be read (" + str(exc) + ")")

    kinds = choices or {}

    def plan() -> tuple[dict[str, dict[str, Any]], dict[str, Any], bool]:
        mono = select_masters(named)
        cp = osc.select_colour_masters(colour_named, kinds)
        missing = any(k not in mono for k in CHANNELS) or cp["OSC"] is None or cp["OSCNB"] is None
        return mono, cp, missing and len(unnamed) > 0

    mono_picks, colour_plan, read_unnamed = plan()
    if read_unnamed:
        for rec in unnamed:
            info = reader(rec["path"])
            if info is None:
                unreadable.append(rec["path"] + ": header could not be read")
                continue
            n = _channel_count(info)
            bad = osc.channel_count_problem(rec["path"], n)
            if bad is not None:
                unreadable.append(bad)
                continue
            kws = info.get("keywords") or []
            filter_value = keyword_value(kws, "FILTER")
            rec.update(drizzle=master_drizzle_label(info), autocrop=is_autocrop_name(rec["name"]))
            if n == 3:
                slot = osc.entry_channel(filter_value, n, rec["path"], kinds)["channel"]
                if slot is not None and colour_plan[slot] is not None:
                    continue  # that slot already has a named winner
                rec.update(filter="" if filter_value is None else filter_value, colour=True, numberOfChannels=3)
                colour_named.append(rec)
            else:
                channel = channel_from_filter(filter_value)
                if channel is None or channel in mono_picks:
                    continue
                rec.update(channel=channel, colour=False, numberOfChannels=1)
                named.append(rec)
        mono_picks, colour_plan, _ = plan()

    picks: list[dict[str, Any]] = list(mono_picks.values())
    picks += [colour_plan[slot] for slot in osc.COLOUR_SLOTS if colour_plan[slot] is not None]
    picks += colour_plan["unresolved"]

    confirmed: dict[str, dict[str, Any]] = {}
    colour: dict[str, Optional[dict[str, Any]]] = {"OSC": None, "OSCNB": None}
    unresolved: list[dict[str, Any]] = []
    corrected: list[str] = []
    skipped = 0

    def better(entry: Mapping[str, Any], prev: Optional[Mapping[str, Any]]) -> bool:
        return prev is None or (master_variant_rank(entry["drizzle"], entry["autocrop"]) >
                                master_variant_rank(prev["drizzle"], prev["autocrop"]))

    for pick in picks:
        info = reader(pick["path"])
        if info is None:
            unreadable.append(pick["path"] + ": header could not be read")
            continue
        kws = info.get("keywords") or []
        if not is_master_light(pick["name"], keyword_value(kws, "IMAGETYP")):
            skipped += 1
            continue
        n = _channel_count(info)
        bad = osc.channel_count_problem(pick["path"], n)
        if bad is not None:
            unreadable.append(bad)
            continue
        filter_value = keyword_value(kws, "FILTER")
        route = osc.entry_channel(filter_value, n, pick["path"], kinds)
        fix = None
        if route["colour"] or route["channel"] is not None:
            fix = osc.colour_candidate_correction(
                {"path": pick["path"], "colour": bool(pick.get("colour")), "numberOfChannels": n})
        if fix is not None:
            corrected.append(fix)
        if route["colour"]:
            entry = _colour_entry(pick, info, route["channel"], filter_value, kws)
            if route["channel"] is None:
                unresolved.append(entry)
            elif better(entry, colour[route["channel"]]):
                colour[route["channel"]] = entry
            continue
        channel = route["channel"]
        if channel is None:
            unreadable.append(pick["path"] + ": unrecognised FILTER " +
                              ("(missing)" if filter_value is None else "'" + filter_value + "'"))
            continue
        if pick.get("channel") is not None and channel != pick["channel"] and fix is None:
            corrected.append(pick["name"] + ": name says " + pick["channel"] + ", header says " + channel)
        entry = {
            "path": pick["path"],
            "name": pick["name"],
            "filter": filter_value,
            "instrume": keyword_value(kws, "INSTRUME"),
            "channel": channel,
            "width": info.get("width"),
            "height": info.get("height"),
            "drizzle": master_drizzle_label(info),
            "autocrop": pick["autocrop"],
            "mtime": pick["mtime"],
        }
        if better(entry, confirmed.get(channel)):
            confirmed[channel] = entry
    return {"confirmed": confirmed, "colour": colour, "unresolved": unresolved, "corrected": corrected,
            "skipped": skipped + len(unreadable), "unreadable": unreadable, "total": scan_total}


def _read_header_or_none(path: str) -> Optional[dict[str, Any]]:
    try:
        return read_header(path)
    except (OSError, ValueError):
        return None


def _mtime(path: str) -> float:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0.0
