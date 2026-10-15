"""The Frame Selector's window helpers (loomsiril.frames_ui): the text and the geometry of the progress window, the
row marks and the constants, checked against values worked out from the JavaScript formulas.

The pure tests need no display. The window tests create widgets under a hidden root and skip when tkinter is not
importable or there is no display.
"""

from __future__ import annotations

import pytest

from loomsiril import frames_ui as fu
from loomsiril.frames_files import measuring_lines


# ---- pure: the constants and the title

def test_the_constants_are_the_javascript_values() -> None:
    assert fu.LABEL_GAP == 13
    assert fu.SCROLL_LINE == 48
    assert fu.TAG_COLOURS == {"reject": {"fill": 0xffb02222, "ink": 0xffffffff},
                              "flag": {"fill": 0xffc08a1e, "ink": 0xff1a1a1a}}
    assert fu.TAG == {"W": 150, "H": 26, "GAP": 8, "MARGIN": 10}
    assert fu.THUMB == {"W": 120, "H": 80}
    assert fu.THUMB_CAP == 600
    assert fu.PICK_RADIUS == 6
    assert fu.REJECT_COLOUR == 0xffcc2222
    assert fu.MARK_SIZE == 12
    assert fu.SCAN_NAME_CHARS == 58
    assert fu.PULSE_PERIOD_MS == 1600
    assert fu.PLOT_COLOURS["REJECT"] == fu.REJECT_COLOUR
    assert fu.PLOT_COLOURS["PICKED"] == 0xffe07000
    assert fu.AUTO_STYLE == "QLineEdit { color: #8a8a8a; font-style: italic; }"


def test_tk_colour_drops_the_alpha_byte() -> None:
    assert fu.tk_colour(0xffcc2222) == "#cc2222"
    assert fu.tk_colour(0xff202020) == "#202020"
    assert fu.tk_colour(0xffffffff) == "#ffffff"


def test_the_window_title_is_the_javascript_title() -> None:
    # FrameSelector.title(suffix): "Loom Frame Selector <version> (<sha>) - <suffix>", the sha and suffix optional.
    assert fu.frame_selector_title("scanning", version="0.5.1", sha="abc1234") == \
        "Loom Frame Selector 0.5.1 (abc1234) - scanning"
    assert fu.frame_selector_title("scanning", version="0.5.1") == "Loom Frame Selector 0.5.1 - scanning"
    assert fu.frame_selector_title(version="0.5.1") == "Loom Frame Selector 0.5.1"
    assert fu.frame_selector_title(version="0.5.1", sha="") == "Loom Frame Selector 0.5.1"


# ---- pure: the reject mark's pixels

def test_the_reject_mask_is_a_square_cross() -> None:
    assert len(fu.REJECT_MASK) == fu.MARK_SIZE
    assert all(len(row) == fu.MARK_SIZE for row in fu.REJECT_MASK)
    # A cross is the same flipped left-right and top-bottom, and its centre is inked.
    assert all(row == row[::-1] for row in fu.REJECT_MASK)
    assert fu.REJECT_MASK == fu.REJECT_MASK[::-1]
    assert fu.REJECT_MASK[5][5] == "X" and fu.REJECT_MASK[6][6] == "X"
    # Its arms stay inside the pad of 3 pixels the JS leaves round the pen (FrameSelector.js 4240-4246).
    assert all(ch == "." for row in fu.REJECT_MASK[:3] + fu.REJECT_MASK[9:] for ch in row)
    assert all(row[:3] == "..." and row[9:] == "..." for row in fu.REJECT_MASK[3:9])


# ---- pure: elideHead (lib/Util.js 963-980)

@pytest.mark.parametrize(("text", "limit", "expected"), [
    ("abcdefghij", 7, "...ghij"),            # 10 > 7: keep the last 4, mark the cut
    ("abcdefghij", 10, "abcdefghij"),        # fits exactly
    ("short", 10, "short"),
    ("abcdef", 3, "abcdef"),                 # a limit below 4 leaves the text whole
    ("abcdef", None, "abcdef"),              # Number(null) is 0
    ("abcdef", "nope", "abcdef"),            # NaN
    ("abcdefghij", 7.5, "...fghij"),         # substring() truncates the fractional start 5.5 to 5
    (None, 10, ""),                          # no text is the empty string
    (12345, 4, "...5"),                      # String(12345) is "12345"
])
def test_elide_head_matches_the_javascript(text, limit, expected) -> None:
    assert fu.elide_head(text, limit) == expected


def test_elide_head_counts_utf16_units() -> None:
    # "a" + one astral character (two UTF-16 units) + "bcdefg" is 9 units in JS; a limit of 6 keeps the last 3.
    assert fu.elide_head("a\U0001F600bcdefg", 6) == "...efg"
    assert fu.elide_head("a\U0001F600bcdefg", 9) == "a\U0001F600bcdefg"


def test_elide_head_keeps_the_identifying_tail_of_a_subframe_name() -> None:
    name = "/data/target/exposure_600s_binning2/Light_LRGB_300s_2026-10-01_23-15-07_0042.fit"
    shown = fu.elide_head(name, fu.SCAN_NAME_CHARS)
    assert len(shown) == fu.SCAN_NAME_CHARS
    assert shown.startswith("...") and shown.endswith("23-15-07_0042.fit")


# ---- pure: pulseBlock (lib/Util.js 982-998)

def test_pulse_block_moves_across_and_back_once_per_period() -> None:
    # Width 460: the block is a quarter (115) and travels 345 pixels.
    assert fu.pulse_block(0, 460) == {"x": 0, "width": 115}
    assert fu.pulse_block(400, 460) == {"x": 173, "width": 115}     # half way there: 172.5 rounds up
    assert fu.pulse_block(800, 460) == {"x": 345, "width": 115}     # the far end
    assert fu.pulse_block(1200, 460) == {"x": 173, "width": 115}    # half way back
    assert fu.pulse_block(1600, 460) == {"x": 0, "width": 115}      # one period: back at the start
    assert fu.pulse_block(1700, 460) == {"x": 43, "width": 115}     # 100 ms into the period: 0.125 * 345 = 43.125


def test_pulse_block_width_is_at_least_eight_pixels() -> None:
    assert fu.pulse_block(800, 20) == {"x": 12, "width": 8}         # round(20/4) = 5, raised to 8; travel 12
    assert fu.pulse_block(800, 10) == {"x": 2, "width": 8}          # travel 10 - 8 = 2


def test_pulse_block_before_the_start_or_with_no_time_is_at_the_start() -> None:
    assert fu.pulse_block(-5, 460) == {"x": 0, "width": 115}
    assert fu.pulse_block(float("nan"), 460) == {"x": 0, "width": 115}


# ---- Tk: a hidden root, skipped without a display

@pytest.fixture
def tk_root():
    try:
        import tkinter
    except (ImportError, OSError) as exc:
        pytest.skip(f"tkinter cannot be imported here: {exc}")
    try:
        root = tkinter.Tk()
    except tkinter.TclError:
        pytest.skip("no display: tkinter.Tk() does not work here")
    root.withdraw()
    yield root
    try:
        root.destroy()
    except tkinter.TclError:
        pass


def _gone(w: fu.ScanWindow) -> bool:
    """True once the window's Tk widget is destroyed (Tk answers 0 for a window that no longer exists)."""
    import tkinter

    try:
        return not w.top.winfo_exists()
    except tkinter.TclError:
        return True


def test_report_puts_the_count_and_the_elided_name_on_the_two_lines(tk_root) -> None:
    w = fu.ScanWindow(tk_root)
    try:
        name = "/frames/Light_" + "x" * 80 + "_0007.fit"
        assert w.report("Reading frames", 3, 10, name) is True
        assert w.title_label.cget("text") == "Reading frames (3 of 10)"
        assert w.detail_label.cget("text") == fu.elide_head(name, fu.SCAN_NAME_CHARS)
        assert w.fraction == pytest.approx(0.3)
        assert w.indeterminate is False
    finally:
        w.release()


def test_report_with_no_known_total_shows_the_count_alone(tk_root) -> None:
    clock = [1000.0]
    w = fu.ScanWindow(tk_root, clock=lambda: clock[0])
    try:
        w.report("Reading frames", 4, 0, "frame.fit")
        assert w.title_label.cget("text") == "Reading frames (4)"
        assert w.indeterminate is True
        clock[0] = 1800.0
        w.report("Reading frames", 5, 0, "frame.fit")
        assert w.pulse_start == 1000.0
        assert w.pulse_elapsed == pytest.approx(800.0)
    finally:
        w.release()


def test_display_with_a_fraction_leaves_the_pulse_off(tk_root) -> None:
    w = fu.ScanWindow(tk_root)
    try:
        w.display("Measuring Ha", "", 0.25)
        assert w.indeterminate is False and w.fraction == 0.25
        w.announce("starting...")
        assert w.title_label.cget("text") == "starting..." and w.fraction == 0
    finally:
        w.release()


def test_the_callbacks_report_the_measuring_lines(tk_root) -> None:
    w = fu.ScanWindow(tk_root)
    try:
        cb = w.callbacks()
        overall = {"done": 3, "total": 12, "channel": 1, "channels": 2}
        assert cb.measuring(2, 5, "Ha", overall) is True
        lines = measuring_lines("Ha", 2, 5, overall)
        assert w.title_label.cget("text") == lines["title"]
        assert w.detail_label.cget("text") == lines["detail"]
        assert w.fraction == pytest.approx(3 / 12)
        assert cb.reading(1, 4, "a.fit") is True
        assert w.title_label.cget("text") == "Reading frames (1 of 4)"
    finally:
        w.release()


def test_cancel_sets_cancelled_and_the_callbacks_return_false(tk_root) -> None:
    w = fu.ScanWindow(tk_root)
    try:
        w.cancel_button.invoke()
        assert w.cancelled is True
        assert w.title_label.cget("text") == "stopping..."
        assert str(w.cancel_button.cget("state")) == "disabled"
        cb = w.callbacks()
        assert cb.reading(1, 4, "a.fit") is False
        assert cb.measuring(1, 4, "Ha", None) is False
    finally:
        w.release()


def test_release_destroys_the_window(tk_root) -> None:
    w = fu.ScanWindow(tk_root)
    w.release()
    assert _gone(w)


def test_with_progress_returns_the_result_and_releases_the_window(tk_root) -> None:
    seen: list = []

    def work(w):
        seen.append(w)
        assert w.window_title == "Loom Frame Selector 0.5.1 - Deleting"
        return 42

    result = fu.with_progress(tk_root, "Loom Frame Selector 0.5.1 - Deleting", work)
    assert result == 42
    assert _gone(seen[0])


def test_with_progress_releases_the_window_when_the_work_raises(tk_root) -> None:
    seen: list = []

    def work(w):
        seen.append(w)
        raise RuntimeError("the step failed")

    with pytest.raises(RuntimeError, match="the step failed"):
        fu.with_progress(tk_root, None, work)
    assert _gone(seen[0])


def test_with_progress_offers_cancel_only_when_cancellable(tk_root) -> None:
    states: list = []

    def work(w):
        states.append(str(w.cancel_button.cget("state")))

    fu.with_progress(tk_root, None, work)
    fu.with_progress(tk_root, None, work, cancellable=True)
    assert states == ["disabled", "normal"]


def test_with_progress_runs_before_before_showing(tk_root) -> None:
    order: list = []

    def before(w):
        order.append(("before", w.top.winfo_ismapped()))

    def work(w):
        order.append(("work",))

    fu.with_progress(tk_root, None, work, before=before)
    assert order[0] == ("before", 0) and order[1] == ("work",)


def test_the_icons_are_mark_sized_images(tk_root) -> None:
    reject = fu.reject_icon(tk_root)
    blank = fu.blank_icon(tk_root)
    assert (reject.width(), reject.height()) == (fu.MARK_SIZE, fu.MARK_SIZE)
    assert (blank.width(), blank.height()) == (fu.MARK_SIZE, fu.MARK_SIZE)


def test_the_reject_icon_is_red_where_the_cross_is_and_clear_elsewhere(tk_root) -> None:
    reject = fu.reject_icon(tk_root)
    assert tuple(reject.get(4, 4)) == (0xCC, 0x22, 0x22)
    assert reject.transparency_get(0, 0) is True
    blank = fu.blank_icon(tk_root)
    assert all(blank.transparency_get(x, y) for x in range(fu.MARK_SIZE) for y in range(fu.MARK_SIZE))


def test_mark_icon_picks_the_cross_or_the_spacer_and_reuses_it(tk_root) -> None:
    cache: dict = {}
    marked = fu.mark_icon(tk_root, True, cache)
    assert marked is fu.mark_icon(tk_root, True, cache)
    assert fu.mark_icon(tk_root, False, cache) is fu.blank_icon(tk_root, cache)
    assert fu.mark_icon(tk_root, True, cache) is not fu.mark_icon(tk_root, False, cache)
