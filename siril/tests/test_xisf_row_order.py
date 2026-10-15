"""Which way round siril-cli's ``load_image_from_file(path, with_pixels=True)`` returns the rows of an XISF file.

Result (siril-cli 1.4.4, Docker image; see the test ``test_siril_returns_xisf_rows_in_order``): XISF rows are
RESULT_PLACEHOLDER.

The FITS rows were already shown to be bottom-up (evidence/master-flat-probe.md, P3). This file settles the XISF case
on a minimal file written here, without a library: a 64 x 48 UInt16 mono image whose top 8 rows (file rows 0..7) hold
60000, whose other rows hold 1000, and whose single pixel at x=3, y=40 (file row 40) holds 65000. The XISF row order
(top-down) is the one the file stores; loomsiril/frames_copy.py and loomsiril/frames_render.py turn Siril's rows
top-down themselves, so this decides whether they must flip XISF data.

No JS function is ported here; the file is a probe. Pixels and header are read back with
loomsiril.loom.xisf.read_xisf_header in a plain test, and Siril is run through tests/loom/siril_run.run_pyscript
in the one test marked siril (skipped where siril-cli is absent).
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

from loomsiril.loom.xisf import read_xisf_header

from loom.siril_run import run_pyscript

WIDTH = 64
HEIGHT = 48
DATA_OFFSET = 4096
BRIGHT_X = 3
BRIGHT_Y = 40
TOP_ROWS = 8
TOP_VALUE = 60000
REST_VALUE = 1000
BRIGHT_VALUE = 65000


def _samples() -> np.ndarray:
    """The image as stored in the file: row 0 is the top row."""
    img = np.full((HEIGHT, WIDTH), REST_VALUE, dtype="<u2")
    img[:TOP_ROWS, :] = TOP_VALUE
    img[BRIGHT_Y, BRIGHT_X] = BRIGHT_VALUE
    return img


def write_xisf(path: Path) -> Path:
    """A minimal monolithic XISF 1.0 file: signature, header length, 4 reserved bytes, XML header, then the pixels."""
    xml_head = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<xisf version="1.0" xmlns="http://www.pixinsight.com/xisf">'
        f'<Image geometry="{WIDTH}:{HEIGHT}:1" sampleFormat="UInt16" colorSpace="Gray" '
        f'location="attachment:{DATA_OFFSET}:{WIDTH * HEIGHT * 2}">'
        '<FITSKeyword name="FILTER" value="\'L\'" comment=""/>'
        "</Image></xisf>"
    ).encode("utf-8")
    header_room = DATA_OFFSET - 16
    assert len(xml_head) <= header_room
    xml = xml_head + b" " * (header_room - len(xml_head))
    blob = b"XISF0100" + struct.pack("<I", len(xml)) + b"\x00" * 4 + xml
    blob += b"\x00" * (DATA_OFFSET - len(blob))
    blob += _samples().tobytes()
    path.write_bytes(blob)
    return path


def test_written_xisf_header_reads_geometry_and_filter(tmp_path: Path) -> None:
    head = read_xisf_header(write_xisf(tmp_path / "probe.xisf"))
    assert head.geometry == (WIDTH, HEIGHT, 1)
    assert head.sample_format == "UInt16"
    assert ("FILTER", "L", "") in head.keywords


def test_written_xisf_pixels_start_at_the_declared_offset(tmp_path: Path) -> None:
    path = write_xisf(tmp_path / "probe.xisf")
    raw = path.read_bytes()
    assert raw[:8] == b"XISF0100"
    assert struct.unpack("<I", raw[8:12])[0] == DATA_OFFSET - 16
    assert raw[12:16] == b"\x00" * 4
    data = np.frombuffer(raw[DATA_OFFSET:DATA_OFFSET + WIDTH * HEIGHT * 2], dtype="<u2").reshape(HEIGHT, WIDTH)
    assert int(data[0, 0]) == TOP_VALUE and int(data[TOP_ROWS, 0]) == REST_VALUE
    assert int(data[BRIGHT_Y, BRIGHT_X]) == BRIGHT_VALUE


_KEYS = [
    "full.shape", "full.dtype", "full.mean_rows_0_7", "full.mean_rows_40_47", "full.max_at_xy", "full.max_value",
    "preview.shape", "preview.dtype", "preview.mean_rows_0_7", "preview.mean_rows_40_47", "preview.max_at_xy",
    "preview.max_value",
]
EXPECTED_SIRIL_FACTS: dict = {}  # filled from the Docker run; placeholder for the first run

# The Siril side: prints what it sees, one key=<json> line per fact; the test reads them back with run.value().
_SIRIL_SCRIPT = '''
import json
import sys

import numpy as np
import sirilpy as s

path = sys.argv[1]
siril = s.SirilInterface()
siril.connect()


def report(prefix, loaded):
    data = np.asarray(loaded.data)
    print(prefix + ".shape=" + json.dumps(list(data.shape)))
    print(prefix + ".dtype=" + json.dumps(str(data.dtype)))
    print(prefix + ".mean_rows_0_7=" + json.dumps(float(data[0:8].mean())))
    print(prefix + ".mean_rows_40_47=" + json.dumps(float(data[40:48].mean())))
    row, col = np.unravel_index(int(np.argmax(data)), data.shape)
    print(prefix + ".max_at_xy=" + json.dumps([int(col), int(row)]))
    print(prefix + ".max_value=" + json.dumps(float(data.max())))


report("full", siril.load_image_from_file(path, with_pixels=True))
report("preview", siril.load_image_from_file(path, with_pixels=True, preview=True))
'''


@pytest.mark.siril
@pytest.mark.siril
def test_siril_returns_xisf_rows_in_order(tmp_path: Path) -> None:
    path = write_xisf(tmp_path / "probe.xisf")
    run = run_pyscript(_SIRIL_SCRIPT, tmp_path, args=[str(path)]).check()
    got = {key: run.value(key) for key in _KEYS}
    assert got == EXPECTED_SIRIL_FACTS
