"""What the Fly-Through dialog enables, and why Render is not possible yet: pure rules, no tkinter and no file access.

The dialog builds a :class:`FlyDialogModel` from its widgets on every change and applies :meth:`enabled`, :meth:`reasons`
and the notes to the widgets (one ``_sync``), so the rules are tested without Tk. File-system facts (does the file
exist, is the folder writable) are not looked up here: the dialog's worker thread computes them
(loomsiril.fly_checks) and passes the results in ``checked``; a path with no result yet is "being checked".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from loomsiril import fly_options
from loomsiril import presets as preset_mod

__all__ = ["GROUPS", "CHECKING", "FINDING_DISTANCE", "FlyDialogModel", "IMAGE_HINT", "NONE_FOUND", "OFFLINE_NOTE", "distance_hint",
           "distance_line", "found_note", "name_sources", "outside_note", "window_title",
           "ORIENTATIONS", "natural_orientation", "orientation_id", "orientation_label", "preset_label"]

#: Every greying group the dialog knows (``FlyDialogModel.enabled`` answers each; a new control's group is added here, in
#: ``enabled`` and in ``disabled_reason``).
GROUPS: tuple[str, ...] = ("extract_radio", "extract", "files", "loop", "video", "music", "encode", "logo", "logo_look",
                          "gaia_folder")
#: The prefix of the greying group of a Stars-and-motion or HDR control: ``opt:<saved key>`` (loomsiril.fly_options).
OPTION_GROUP = "opt:"

IMAGE_HINT = "empty: the image open in Siril"
NONE_FOUND = "No star-extraction tool found: StarNet2, SyQon Studio or StarXTerminator"
CHECKING = "Checking the files..."
OFFLINE_NOTE = "Name lookup needs the network"
FINDING_DISTANCE = "Finding the distance\u2026"


#: The Orientation combo's choices, ``(engine value, label)``: the engine (FlyJob.orientation) says landscape or portrait
#: where the JS dialog says Horizontal or Vertical.
ORIENTATIONS: list[tuple[str, str]] = [("landscape", "Horizontal"), ("portrait", "Vertical")]

_PRESET_NAMES = {"social_vertical": "Social", "social_square": "Social", "youtube_4k": "YouTube",
                 "youtube_1080": "YouTube", "exhibition": "Exhibition"}


def natural_orientation(grid: "Sequence[int] | None") -> str:
    """The orientation an image has: ``portrait`` when it is taller than wide, else ``landscape`` (also a square one,
    and an unknown grid)."""
    if grid is None or len(grid) < 2:
        return "landscape"
    return "portrait" if grid[1] > grid[0] else "landscape"


def orientation_label(orientation_id: str) -> str:
    """The combo's text for an engine value; an unknown value is Horizontal."""
    return dict(ORIENTATIONS).get(orientation_id, ORIENTATIONS[0][1])


def orientation_id(label: str) -> str:
    """The engine value for the combo's text; an unknown text is landscape."""
    return {text: oid for oid, text in ORIENTATIONS}.get(label, ORIENTATIONS[0][0])


def preset_label(pid: str, orientation: str = "landscape") -> str:
    """The output-size checkbox text of preset ``pid``: its name and the frame it renders at in ``orientation``
    (loomsiril.presets.preset_spec; it spells portrait \"vertical\"), e.g. "YouTube 1080\u00d71920" for Vertical; the Exhibition says "(loop)"."""
    spec = preset_mod.preset_spec(pid, "vertical" if orientation == "portrait" else "horizontal", "none")
    text = f"{_PRESET_NAMES[pid]} {spec['w']}\u00d7{spec['h']}"
    return text + " (loop)" if pid == "exhibition" else text


def distance_line(result: object) -> str:
    """The grey line under the Distance box for a :class:`loomsiril.fly_distance.DistanceResult` (None: still looking).

    "Distance: ~1,950 pc (cluster of 37 stars, Gaia)", "Distance: 2000 pc (typed)", "Distance: galaxy (fixed
    backdrop)" or "No distance found: <reason>. Type one (pc)."; a note (Gaia cache empty, used online) follows."""
    if result is None:
        return FINDING_DISTANCE
    d = getattr(result, "distance", None)
    if d is None:
        return f"No distance found: {result.reason}. Type one (pc)."
    if d == float("inf"):
        text = f"Distance: {result.source}"
    elif result.code == "typed":
        text = f"Distance: {_plain(d)} pc (typed)"
    else:
        text = f"Distance: ~{d:,.0f} pc ({result.source})"
    return f"{text}. {result.note}." if result.note else text


def _plain(d: float) -> str:
    return f"{d:.0f}" if d == int(d) else f"{d:g}"


def distance_hint(result: object) -> str:
    """The Distance box's placeholder: the value auto will use ("~1,950 pc (auto)"), else just "auto"."""
    d = getattr(result, "distance", None)
    if result is None or d is None or d == float("inf") or getattr(result, "code", "") == "typed":
        return "auto"
    return f"~{d:,.0f} pc (auto)"


def name_sources(image_path: str, header_object: str, open_path: str) -> list[tuple[str, str]]:
    """What the name lookup asks about, in order, as ``(kind, text)`` (see skyname.find_named): the Image file's path,
    the open image's OBJECT keyword, the open image's own file path. Empty ones and repeats are left out."""
    out: list[tuple[str, str]] = []
    image, obj, opened = (image_path or "").strip(), (header_object or "").strip().strip("'").strip(), (open_path or "").strip()
    if image:
        out.append(("file", image))
    if obj:
        out.append(("header", obj))
    if opened and opened != image:
        out.append(("open", opened))
    return out


def found_note(hit: Mapping[str, object], how: str) -> str:
    """The grey line under Target after a name was found."""
    return f"Found: {hit['id']} (from {how} via CDS Sesame)"


def outside_note(object_id: str, separation_deg: float, radius_deg: float) -> str:
    """Why a name that Sesame knows is not taken as the target: it lies outside the solved field."""
    where = "" if separation_deg == float("inf") else f", {separation_deg:.1f} deg from the image centre"
    return (f"{object_id} is not in this image{where} (the field reaches {radius_deg:.1f} deg): "
            "not used as the target")


def window_title(image_file: str) -> str:
    """The dialog's title: the image's file name after the product name (Siril has no Image<number> views; the file
    name is the image's title)."""
    name = (image_file or "").replace("\\", "/").rsplit("/", 1)[-1]
    return f"Loom Fly-Through - {name}" if name else "Loom Fly-Through"


@dataclass
class FlyDialogModel:
    """The dialog's inputs as plain values. ``tools``: "looking", "found" or "none"; ``ffmpeg``: None while it is
    being looked for, else whether it was found; ``checked``: ``(kind, path) -> "" or a message`` for the paths the
    worker has looked at (kinds image, starless, stars, dir); ``busy``: a Render or Draft click is being prepared."""

    mode: str = "files"
    tools: str = "looking"
    tool_id: str = ""
    image_path: str = ""
    starless_path: str = ""
    stars_path: str = ""
    out_dir: str = ""
    presets: Sequence[str] = field(default_factory=tuple)
    ffmpeg: "bool | None" = None
    checked: Mapping[tuple[str, str], str] = field(default_factory=dict)
    busy: bool = False
    grid_notes: Mapping[str, str] = field(default_factory=dict)  # Image file path -> the worker's grey line about its size
    open_grid: "Sequence[int] | None" = None                     # the open image's (width, height, channels)
    file_grids: Mapping[str, Sequence[int]] = field(default_factory=dict)  # Image file path -> its grid (worker)
    orientation: str = "landscape"                               # the Orientation combo's engine value
    video: bool = True                                           # the Encode video box is ticked
    music_path: str = ""                                         # the Music box ("": no music)
    controls: Mapping[str, object] = field(default_factory=dict)  # key -> raw value of each rules=True section control
    dynamic: str = "sdr"                                         # the Dynamic range combo: "sdr" or "hdr"
    target_type: str = "nebula"                                  # the Target type combo: "nebula" or "galaxy"
    gaia: str = "cache"                                          # the Gaia data combo: "cache", "online" or "folder"
    gaia_folder: str = ""                                        # the Gaia folder box

    def image_orientation(self) -> str:
        """The orientation of the image the clip is made from: the Image file's when it is in use and its header was
        read, else the open image's; landscape when neither is known."""
        grid = self.file_grids.get(self.image_path.strip()) if self.mode == "extract" else None
        return natural_orientation(grid if grid is not None else self.open_grid)

    def orientation_note(self) -> str:
        """The grey line beside the Orientation combo: that it follows the image, or what the image is when the user
        chose the other one. Empty while no image size is known."""
        grid = self.file_grids.get(self.image_path.strip()) if self.mode == "extract" else None
        if (grid if grid is not None else self.open_grid) is None:
            return ""
        natural = self.image_orientation()
        if self.orientation == natural:
            return f"follows the image: {natural}"
        return f"the image is {natural}"

    def enabled(self, name: str) -> bool:
        """Whether the widget group ``name`` is usable now. Names: ``extract_radio``, ``extract`` (tool drop-down,
        image file, its Browse), ``files`` (starless and stars boxes with their Browse), ``loop``, ``video``, ``music`` (the Music box, its Browse and the Fade box: only
        when the video is encoded, so Encode video is ticked and ffmpeg was not found missing)."""
        if name == "extract_radio":
            return self.tools == "found"
        if name == "extract":
            return self.mode == "extract" and self.tools == "found"
        if name == "files":
            return self.mode == "files"
        if name == "loop":
            # the Exhibition preset always loops (presets.preset_loops): the Loop choice only matters to the others
            return not (self.presets and all(preset_mod.preset_loops(p) for p in self.presets))
        if name == "video":
            return self.ffmpeg_ok()
        if name == "music":
            return self.ffmpeg_ok() and self.video
        if name == "encode":                  # Format and Quality: the encode's options
            return self.ffmpeg_ok() and self.video
        if name == "gaia_folder":             # the Gaia folder box and its Browse: only when Gaia data is "folder"
            return self.gaia == "folder"
        if name == "logo":                    # the Placement combo: needs a logo file
            return bool(self.logo_path())
        if name == "logo_look":               # Opacity and Fade in after: need a logo file that is placed (not Off)
            return bool(self.logo_path()) and self.logo_place() != "off"
        if name.startswith(OPTION_GROUP):     # a Stars-and-motion or HDR control: its own rule (fly_options.reason)
            return fly_options.enabled(fly_options.by_saved(name[len(OPTION_GROUP):]), self._option_context())
        raise KeyError(name)

    def _option_context(self) -> "fly_options.Context":
        return fly_options.Context(self.dynamic, tuple(self.presets), self.target_type)

    def logo_path(self) -> str:
        """The Logo box's file ("": no logo)."""
        return str(self.controls.get("logo_path") or "").strip()

    def logo_place(self) -> str:
        """The Placement combo's id: "off" or one of loomsiril.logo.LOGO_PLACES."""
        from loomsiril import logo

        return logo.place_id(str(self.controls.get("logo_place") or "off"))

    def logo_note(self) -> str:
        """The grey line under the Logo box: why the logo cannot be used (the worker's message for the file), "" when it can
        or is not in use."""
        path = self.logo_path()
        if not path:
            return "no logo: Browse... for a PNG, TIFF or FITS picture"
        if self.logo_place() == "off":
            return "placement is Off: no logo is drawn"
        return self.checked.get(("logo", path), "")

    def ffmpeg_path(self) -> str:
        """The ffmpeg path the user typed in Video details ("": the one found on this machine)."""
        return str(self.controls.get("ffmpeg_path") or "").strip()

    def ffmpeg_ok(self) -> bool:
        """Whether there is an ffmpeg to encode with: a typed path the worker found working, else the one discovered
        (still being looked for counts as there). A typed path that does not work leaves the discovered one's answer;
        Render is stopped by its message (``reasons``), never silently sent to another binary."""
        typed = self.ffmpeg_path()
        if typed and self.checked.get(("ffmpeg", typed)) == "":
            return True
        return self.ffmpeg is not False

    def disabled_reason(self, name: str) -> str:
        """Why the group ``name`` is greyed now (a short phrase for a section header), "" when it is usable. A new group
        adds its branch here and in ``enabled``."""
        if self.enabled(name):
            return ""
        if name == "extract_radio":
            return "no star-extraction tool found"
        if name == "extract":
            return "needs a star-extraction tool" if self.tools != "found" else "needs the extract mode"
        if name == "files":
            return "needs the files mode"
        if name == "loop":
            return "the Exhibition preset always loops"
        if name == "video":
            return "ffmpeg was not found"
        if name in ("music", "encode"):
            return "ffmpeg was not found" if not self.ffmpeg_ok() else "needs Encode video"
        if name == "gaia_folder":
            return "needs Gaia data set to folder"
        if name == "logo":
            return "needs a logo file"
        if name == "logo_look":
            return "needs a logo file" if not self.logo_path() else "placement is Off"
        if name.startswith(OPTION_GROUP):
            return fly_options.reason(fly_options.by_saved(name[len(OPTION_GROUP):]), self._option_context())
        raise KeyError(name)

    def image_note(self) -> str:
        """The grey line under the Image file box: the size note or the refusal the worker made for the typed path,
        else the hint (always the hint when the Image file is not in use: files mode, or no tool found)."""
        if not self.enabled("extract"):
            return IMAGE_HINT
        return self.grid_notes.get(self.image_path.strip(), "") or IMAGE_HINT

    def video_note(self) -> str:
        return "" if self.ffmpeg_ok() else "ffmpeg was not found: only the frames are written"

    def music_note(self) -> str:
        """The grey line beside the Fade box: why the Music row is greyed, or what is wrong with the music file."""
        if self.ffmpeg is False:
            return "Music needs ffmpeg: it was not found"
        if not self.video:
            return "Tick Encode video to add music"
        path = self.music_path.strip()
        return self.checked.get(("music", path), "") if path else ""

    def gaia_note(self) -> str:
        """The grey line under the Gaia folder box: what the chosen folder holds (or why it cannot be used), and Siril's own
        catalogue, which is shown but never used (it has no parallax)."""
        from loomsiril import gaia_local

        text = gaia_local.siril_note()
        if self.gaia != "folder":
            return text
        path = self.gaia_folder.strip()
        problem = self.checked.get(("gaia_dir", path), "") if path else ""
        return f"{problem or gaia_local.folder_status(path)}\n{text}"

    def loop_note(self) -> str:
        return "" if self.enabled("loop") else "The Exhibition preset always loops"

    def wanted_paths(self) -> list[tuple[str, str]]:
        """The ``(kind, path)`` pairs whose check the current mode needs (non-empty paths only)."""
        pairs = [("image", self.image_path)] if self.mode == "extract" else [
            ("starless", self.starless_path), ("stars", self.stars_path)]
        pairs.append(("dir", self.out_dir))
        if self.ffmpeg_path():                                    # a chosen ffmpeg is looked at even before Encode video is ticked
            pairs.append(("ffmpeg", self.ffmpeg_path()))
        if self.enabled("music") and self.music_path.strip():      # a music file only matters when it will be used
            pairs.append(("music", self.music_path))
        if self.gaia == "folder" and self.gaia_folder.strip():      # a chosen Gaia folder must be there
            pairs.append(("gaia_dir", self.gaia_folder))
        if self.logo_path() and self.logo_place() != "off":        # a logo file only matters when it is placed (kept when Off)
            pairs.append(("logo", self.logo_path()))
        return [(k, p.strip()) for k, p in pairs if p.strip()]

    def reasons(self) -> list[str]:
        """Why Render and Draft are not possible now (empty when they are); the first is shown next to the buttons."""
        out: list[str] = []
        if self.mode == "extract":
            if self.tools == "looking":
                out.append("Still looking for star-extraction tools...")
            elif self.tools == "none" or not self.tool_id:
                out.append(NONE_FOUND + ": choose the files instead.")
        else:
            for label, path in (("starless", self.starless_path), ("stars", self.stars_path)):
                if not path.strip():
                    out.append(f"Choose the {label} file.")
        if not self.presets:
            out.append("Choose at least one output size.")
        if not self.out_dir.strip():
            out.append("Choose an output folder.")
        if self.gaia == "folder" and not self.gaia_folder.strip():
            out.append("Choose the Gaia folder, or set Gaia data to cache or online.")
        for kind, path in self.wanted_paths():
            if kind == "ffmpeg" and not self.video:               # only the encode needs it
                continue
            result = self.checked.get((kind, path))
            if result is None:
                if CHECKING not in out:
                    out.append(CHECKING)
            elif result:
                out.append(result)
        return out

    def can_render(self) -> bool:
        return not self.busy and not self.reasons()
