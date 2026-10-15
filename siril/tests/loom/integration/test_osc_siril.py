"""A colour group registered as one image inside a real siril-cli (marker ``siril``).

A synthetic ``OSC`` master (R, G, B planes of one sky) and an ``OSCNB`` master (H, O planes of the same sky shifted by
(3, 2) px) go through the run's own registration (``_Run.register`` on real plates): the OSC group is the reference
(its G) and the dual-band group is registered to it as one three-plane image with one transform. The registered H
and O plates sit on G's stars within 0.2 px and on each other within 0.05 px, and carry their master-derived FILTER
and LOOMSRC. A second run on the same cache folder loads the cached plates and sends Siril nothing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pytest

from loomsiril.io_fits import read_fits, write_fits

from .. import synth
from ..siril_run import run_pyscript
from .test_osc_spcc_siril import _database_folder

pytestmark = pytest.mark.siril

W, H = 480, 384
SHIFT = (3.0, 2.0)

SCRIPT = r'''
import json, os
import sirilpy as s
from loomsiril.loom import cache, config as loom_config, pipeline
from loomsiril.loom.runctx import RunContext
from loomsiril.loom.siril_ops import SirilCommandOps

siril = s.SirilInterface(); siril.connect()
ops = SirilCommandOps(siril, log=siril.log)
ROOT = "ROOT_DIR"
CACHE = os.path.join(ROOT, "cache")
os.makedirs(CACHE, exist_ok=True)
sent = []
real_send = ops._send
ops._send = lambda text, *a, **k: (sent.append(text), real_send(text, *a, **k))[1]
ops.set_fits_compression(False)


def one_run(tag):
    work = os.path.join(ROOT, "work_" + tag)
    os.makedirs(work, exist_ok=True)
    cfg = loom_config.defaults()
    cfg.update({"paths": dict(cfg["paths"], OSC="OSC_PATH", OSCNB="NB_PATH"),
                "oscKinds": {"OSC": "broadband", "OSCNB": "dualband"}, "useCache": True, "cacheDir": CACHE,
                "outputDir": os.path.join(ROOT, "out"), "gradientTool": "none"})
    lines = []
    ctx = RunContext(ops=ops, work_dir=work, log=lines.append, warn=lines.append)
    state = pipeline._Run(ctx, cfg, cache.CacheStore(CACHE), CACHE, {})
    state.load_channels()
    before = len(sent)
    ref = state.register()
    commands = [t.split(" ")[0] for t in sent[before:]]
    plates = {key: state.plate_of(key) for key in state.chans}
    leftovers = sorted(os.path.relpath(os.path.join(d, f), work) + ("@" if os.path.islink(os.path.join(d, f)) else "")
                       for n in os.listdir(work) if n.startswith("register-")
                       for d, _dirs, files in os.walk(os.path.join(work, n)) for f in files + _dirs)
    return {"ref": ref, "plates": plates, "commands": commands, "keys": {k: c.current_key for k, c in state.chans.items()},
            "leftovers": leftovers}


# one printed line per field: Siril cuts a long log line
for tag in ("COLD", "WARM"):
    for field, value in one_run(tag.lower()).items():
        print("RESULT_" + tag + "_" + field + "=" + json.dumps(value), flush=True)
'''


def _centroid(img: np.ndarray, x: float, y: float, r: int = 5) -> tuple[float, float]:
    """Flux-weighted centroid of the background-subtracted window around (x, y)."""
    xi, yi = int(round(x)), int(round(y))
    win = img[yi - r:yi + r + 1, xi - r:xi + r + 1].astype(np.float64)
    weight = np.clip(win - np.median(win), 0.0, None)
    yy, xx = np.mgrid[yi - r:yi + r + 1, xi - r:xi + r + 1]
    total = weight.sum()
    return float((weight * xx).sum() / total), float((weight * yy).sum() / total)


def _masters(folder: Path) -> tuple[Path, Path, synth.Master]:
    """The OSC master (planes R, G, B), the OSCNB master (H, O and a spare copy of O), and the sky they share."""
    rgb = [synth.make_master(ch, w=W, h=H, seed=i + 1) for i, ch in enumerate("RGB")]
    nb = [synth.make_master(ch, w=W, h=H, seed=10 + i, shift=SHIFT) for i, ch in enumerate("HO")]
    common = {"INSTRUME": "ZWO ASI2600MC Pro"}
    osc_path, nb_path = folder / "osc_master.fits", folder / "nb_master.fits"
    write_fits(osc_path, [m.planes[0] for m in rgb], {**rgb[1].header, **common, "FILTER": "L-Pro"}, bitpix=-32)
    write_fits(nb_path, [nb[0].planes[0], nb[1].planes[0], nb[1].planes[0]],
               {**nb[0].header, **common, "FILTER": "L-eXtreme"}, bitpix=-32)
    return osc_path, nb_path, rgb[1]


def _bright(stars: np.ndarray, margin: int = 30, peak: float = 0.3) -> np.ndarray:
    keep = (stars[:, 2] > peak) & (stars[:, 0] > margin) & (stars[:, 0] < W - margin) \
        & (stars[:, 1] > margin) & (stars[:, 1] < H - margin)
    return stars[keep]


def test_a_dual_band_group_is_registered_onto_the_osc_green_as_one_image(tmp_path: Path) -> None:
    osc_path, nb_path, green = _masters(tmp_path)
    script = SCRIPT.replace("ROOT_DIR", str(tmp_path)).replace("OSC_PATH", str(osc_path)) \
        .replace("NB_PATH", str(nb_path))
    run = run_pyscript(script, tmp_path).check()
    fields = ("ref", "plates", "commands", "keys", "leftovers")
    cold = {f: run.value(f"RESULT_COLD_{f}") for f in fields}
    warm = {f: run.value(f"RESULT_WARM_{f}") for f in fields}

    assert cold["ref"] == warm["ref"] == "G"
    assert sorted(cold["plates"]) == ["B", "G", "H", "O", "R"]
    assert "register" in cold["commands"] and "rgbcomp" in cold["commands"], cold["commands"]
    assert cold["leftovers"] == [], cold["leftovers"]   # the temporary frames and their folder are gone

    g_plate = read_fits(cold["plates"]["G"])[0][0]
    stars = _bright(green.stars)
    assert len(stars) >= 5
    registered = {k: read_fits(cold["plates"][k]) for k in "HO"}
    for x, y, _ in stars:
        gx, gy = _centroid(g_plate, x, y)
        pos = {k: _centroid(registered[k][0][0], x, y) for k in "HO"}
        for k in "HO":
            assert abs(pos[k][0] - gx) < 0.2 and abs(pos[k][1] - gy) < 0.2, (k, x, y, pos[k], (gx, gy))
        assert abs(pos["H"][0] - pos["O"][0]) < 0.05 and abs(pos["H"][1] - pos["O"][1]) < 0.05, (x, y, pos)

    # the registered splits keep the master-derived provenance, not the temporary group image's
    for key, name, plane in (("H", "Ha", 0), ("O", "OIII", 1)):
        header = registered[key][1]
        assert header["FILTER"] == name and header["LOOMSRC"] == f"nb_master.fits|plane:{plane}", (key, header)

    # the warm run loads the cached splits: no Siril command, the same plates and keys
    assert warm["commands"] == [], warm["commands"]
    assert warm["plates"] == cold["plates"] and warm["keys"] == cold["keys"]
    for key in "HO":
        np.testing.assert_array_equal(read_fits(warm["plates"][key])[0][0], registered[key][0][0])


# ---- whole runs: ``pipeline.run`` on synthetic OSC and OSCNB masters, through to the exported files -------------------
#
# One Siril session runs the real orchestrator once per entry of the plan (a JSON file). Every run writes what a test
# needs (the report, the run log, the SPCC commands sent, the reads of each master, the Siril commands by name) to a JSON
# file; the tests below read those. SPCC needs the online catalogues and the database: marker ``network`` too.

NETWORK = pytest.mark.network

SONY = '"-oscsensor=Sony IMX571"'
CANON = '"-oscsensor=Canon EOS 20D"'
NO_FRONT = '"-oscfilter=No filter"'
NO_LPF = '"-osclpf=Full spectrum (no filter)"'
TRIBAND = '"-oscfilter=Antlia Triband RGB Ultra II"'
CANON_LPF = '"-osclpf=Canon EOS 350D blocking filter"'
BAADER = '"-rfilter=Baader R" "-gfilter=Baader G" "-bfilter=Baader B"'

PLAN_SCRIPT = r'''
import json, os, sys, time, traceback
import sirilpy as s
from loomsiril.loom import colour_load, config as loom_config, finishing, levels, pipeline, stages_correct
from loomsiril.loom.runctx import RunContext, StageOutput
from loomsiril.loom.siril_ops import SirilCommandOps

siril = s.SirilInterface(); siril.connect()
ROOT = "ROOT_DIR"
plan = json.load(open(os.path.join(ROOT, "plan.json")))
ops = SirilCommandOps(siril, log=siril.log)
sent = []
real_send = ops._send
ops._send = lambda text, *a, **k: (sent.append(text), real_send(text, *a, **k))[1]

real_nb = ops.spcc_narrowband
def delayed_nb(*a, **k):
    time.sleep(plan["narrowband_pause"])
    return real_nb(*a, **k)
ops.spcc_narrowband = delayed_nb

reads = []
real_read = colour_load.read_fits
colour_load.read_fits = lambda path, *a, **k: (reads.append(os.fspath(path)), real_read(path, *a, **k))[1]


def stub_sharpen_tools():
    """The RC-Astro tools are not in the image: pass-throughs stand in for the aberration and sharpen stages."""
    real_broadband, real_finishing, real_chosen = (stages_correct.broadband_runners, finishing.finishing_runners,
                                                   levels.tool_chosen)

    def broadband(ctx, channel, config):
        runners = real_broadband(ctx, channel, config)
        if "aberration" in runners:
            runners["aberration"] = lambda i, o: StageOutput(path=i)
        return runners

    def finishing_runners(ctx, label, names, config, *, noun=None):
        runners = real_finishing(ctx, label, names, config, noun=noun)
        if names.get("sharpen") in runners:
            runners[names["sharpen"]] = lambda i, o: StageOutput(path=i)
        return runners

    stages_correct.broadband_runners = broadband
    finishing.finishing_runners = finishing_runners
    levels.tool_chosen = lambda tool: tool not in (None, "none")
    return lambda: (setattr(stages_correct, "broadband_runners", real_broadband),
                    setattr(finishing, "finishing_runners", real_finishing),
                    setattr(levels, "tool_chosen", real_chosen))


def one_run(spec):
    tag = spec["tag"]
    cache_dir = os.path.join(ROOT, spec.get("cache", "cache"))
    os.makedirs(cache_dir, exist_ok=True)
    cfg = loom_config.defaults()
    cfg.update(plan["common"])
    cfg.update(spec["config"])
    cfg["paths"] = dict(loom_config.defaults()["paths"], **cfg["paths"])
    cfg.update({"useCache": True, "cacheDir": cache_dir, "outputDir": os.path.join(ROOT, "out_" + tag)})
    if spec.get("export"):
        cfg.update({"exportDir": os.path.join(ROOT, "export_" + tag), "exportPsb": True, "projectName": "Field"})
    restore = stub_sharpen_tools() if cfg.get("sharpenTool") not in (None, "none") else None
    sent_before, reads_before = len(sent), len(reads)
    result = {"tag": tag, "error": None}
    try:
        report = pipeline.run(cfg, lambda work: RunContext(ops=ops, work_dir=work, platform=sys.platform, tools={}),
                              platform="linux", env={}, home=ROOT, stamp=tag)
        result.update({"produced": list(report.produced), "outputs": dict(report.outputs),
                       "exports": list(report.exports), "warnings": list(report.warnings),
                       "log": open(report.log_path, encoding="utf-8").read().splitlines()})
    except BaseException:
        result["error"] = traceback.format_exc()
    finally:
        if restore:
            restore()
    commands = sent[sent_before:]
    result["spcc"] = [c for c in commands if c.startswith("spcc")]
    result["commands"] = [c.split(" ")[0] for c in commands]
    counted = {}
    for path in reads[reads_before:]:
        counted[os.path.basename(path)] = counted.get(os.path.basename(path), 0) + 1
    result["master_reads"] = counted
    json.dump(result, open(os.path.join(ROOT, "result_" + tag + ".json"), "w"))


for spec in plan["runs"]:
    one_run(spec)
print("PLAN_DONE", flush=True)
'''


@pytest.fixture(scope="module")
def database() -> Path:
    """The SPCC database (installed by docker/network-setup.sh when the image has none)."""
    return _database_folder()


def _mono_masters(folder: Path) -> dict[str, str]:
    """R, G, B mono masters of the sky of ``_masters``, with Baader filter names and small shifts."""
    shifts = {"R": (0.0, 0.0), "G": (-2.6, 3.4), "B": (3.1, -2.2)}
    out: dict[str, str] = {}
    for i, ch in enumerate("RGB"):
        master = synth.make_master(ch, w=W, h=H, seed=20 + i, shift=shifts[ch], filter_name=f"Baader {ch}",
                                   instrume="ZWO ASI2600MM Pro")
        out[ch] = str(synth.write_master(folder / f"mono_{ch}.fits", master))
    return out


def _run_plan(tmp_path: Path, common: dict, runs: list[dict], narrowband_pause: int = 0) -> dict[str, dict]:
    """Run the plan in one siril-cli session; return each run's record by tag (a run's error fails the test).

    ``narrowband_pause`` is a wait in seconds before every narrowband SPCC call. The image has no local Gaia catalogue,
    so Siril 1.4.4 asks its online service for the stars of every SPCC call; a second request that follows a first one
    at once fails with an SSL connect error and ends siril-cli (measured: the call after a broadband SPCC dies, the
    same call two minutes later does not).
    """
    plan = {"common": common, "runs": runs, "narrowband_pause": narrowband_pause}
    (tmp_path / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    script = PLAN_SCRIPT.replace("ROOT_DIR", str(tmp_path))
    run = run_pyscript(script, tmp_path, timeout=1800)
    if run.returncode != 0 or "Traceback (most recent call last)" in run.stdout + run.stderr:
        raise AssertionError(f"siril-cli run failed (exit {run.returncode}):\n{run.tail(400)}")
    assert run.lines("PLAN_DONE"), run.tail(60)
    records = {r["tag"]: json.loads((tmp_path / f"result_{r['tag']}.json").read_text(encoding="utf-8")) for r in runs}
    for tag, rec in records.items():
        assert rec["error"] is None, f"run {tag} failed:\n{rec['error']}"
    return records


def _osc_common(osc_path: Path, nb_path: Path, **extra: object) -> dict:
    common: dict = {"paths": {"OSC": str(osc_path), "OSCNB": str(nb_path)},
                    "oscKinds": {"OSC": "broadband", "OSCNB": "dualband"},
                    "oscSensor": {"sensor": "Sony IMX571", "frontFilter": "", "lpf": ""},
                    "gradientTool": "none", "stretch": False}
    common.update(extra)
    return common


def _log_lines(record: dict) -> list[str]:
    return [re.sub(r"^\[\w+\] ", "", line) for line in record["log"]]


def _stages_run(record: dict) -> list[tuple[str, str]]:
    """(label, stage) of every stage the run executed, in order, from the run log's ``[stage]`` lines."""
    found = [re.match(r"\[stage\] (\S+) (\S+) ran in", line) for line in _log_lines(record)]
    return [(m.group(1), m.group(2)) for m in found if m]


def _loomsrc(path: str) -> object:
    return read_fits(path)[1].get("LOOMSRC")


@NETWORK
def test_a_whole_osc_and_oscnb_run_exports_files_with_master_derived_provenance(database: Path, tmp_path: Path) -> None:
    """``pipeline.run`` on an ``OSC`` and an ``OSCNB`` master, stretched and exported: the RGB composite and HOO exist,
    their FITS results carry the masters' LOOMSRC and the TIFF and PSB exports carry none; a second run with no
    palette keeps the H and O channels, whose FITS results carry their plane's LOOMSRC; the run log names where the
    HOO channels come from; a warm run reads no master and runs no stage."""
    osc_path, nb_path, _green = _masters(tmp_path)
    common = _osc_common(osc_path, nb_path, stretch=True)
    runs = [{"tag": "cold", "config": {"palettes": ["HOO"]}, "export": True},
            {"tag": "warm", "config": {"palettes": ["HOO"]}, "export": True},
            {"tag": "channels", "config": {"palettes": []}, "export": True}]
    records = _run_plan(tmp_path, common, runs, narrowband_pause=120)

    cold = records["cold"]
    assert sorted(cold["produced"]) == ["HOO", "RGB"]
    assert cold["master_reads"] == {"osc_master.fits": 1, "nb_master.fits": 1}, cold["master_reads"]
    rgb_planes, rgb_header = read_fits(cold["outputs"]["RGB"])
    hoo_planes, hoo_header = read_fits(cold["outputs"]["HOO"])
    assert len(rgb_planes) == len(hoo_planes) == 3
    assert rgb_header["LOOMSRC"] == "OSC osc_master.fits planes 0,1,2"
    assert hoo_header["LOOMSRC"] == "OSCNB nb_master.fits plane 0; OSCNB nb_master.fits plane 1"
    assert rgb_header["LOOMOUT"] == "RGB" and hoo_header["LOOMOUT"] == "HOO"

    # the exports hold no FITS keywords: no LOOMSRC in them; the run log's palette-sources line is the record
    export = tmp_path / "export_cold"
    assert sorted(p.name for p in export.iterdir()) == ["Field.psb", "HOO.tif", "RGB.tif"], sorted(export.iterdir())
    for name in ("HOO.tif", "RGB.tif", "Field.psb"):
        assert b"LOOMSRC" not in (export / name).read_bytes(), name
    log = _log_lines(cold)
    assert any(line.startswith("HOO: H from OSCNB L-eXtreme") and "O from OSCNB L-eXtreme" in line for line in log), log
    assert any(line.startswith("HOO: approximation:") for line in log), log

    # the warm run: no master read, no stage run, no Siril command that changes pixels, the same products
    warm = records["warm"]
    assert warm["master_reads"] == {}, warm["master_reads"]
    assert _stages_run(warm) == [], _stages_run(warm)
    assert warm["spcc"] == [] and not {"register", "rgbcomp", "subsky"} & set(warm["commands"]), warm["commands"]
    assert sorted(warm["produced"]) == ["HOO", "RGB"]
    assert _loomsrc(warm["outputs"]["RGB"]) == rgb_header["LOOMSRC"]

    # no palette: the broadband composite and the two narrowband channels, each with its master's LOOMSRC
    chans = records["channels"]
    assert sorted(chans["produced"]) == ["H", "O", "RGB"]
    assert chans["master_reads"] == {}, chans["master_reads"]
    assert _loomsrc(chans["outputs"]["RGB"]) == "OSC osc_master.fits planes 0,1,2"
    assert _loomsrc(chans["outputs"]["H"]) == "nb_master.fits|plane:0"
    assert _loomsrc(chans["outputs"]["O"]) == "nb_master.fits|plane:1"
    assert b"LOOMSRC" not in (tmp_path / "export_channels" / "H.tif").read_bytes()


@NETWORK
def test_an_oscnb_only_run_builds_hoo_with_no_sensor_and_sends_no_spcc_names(database: Path, tmp_path: Path) -> None:
    """A dual-band master alone, no sensor chosen: HOO is built and its narrowband SPCC (the first of its Siril session)
    carries only the wavelengths and bandwidths."""
    _osc_path, nb_path, _green = _masters(tmp_path)
    common = {"paths": {"OSCNB": str(nb_path)}, "oscKinds": {"OSCNB": "dualband"}, "palettes": ["HOO"],
              "gradientTool": "none", "stretch": False}
    record = _run_plan(tmp_path, common, [{"tag": "nb", "config": {}}])["nb"]
    assert record["produced"] == ["HOO"]
    assert _loomsrc(record["outputs"]["HOO"]) == "OSCNB nb_master.fits plane 0; OSCNB nb_master.fits plane 1"
    assert len(record["spcc"]) == 1, record["spcc"]
    command = record["spcc"][0]
    assert command.startswith("spcc -narrowband -rwl=656.28 -rbw="), command
    assert not re.search(r"sensor|filter|lpf", command), command   # no names of any kind
    assert record["master_reads"] == {"nb_master.fits": 1}


def _osc_trio(sensor: str, front: str, lpf: str) -> str:
    return f"spcc {sensor} {front} {lpf}"


#: Each run changes one spectral choice of the run before it: (tag, the oscSensor choice, the SPCC command it sends).
VARIANTS = [
    ("sensor", {"sensor": "Canon EOS 20D", "frontFilter": "", "lpf": ""}, _osc_trio(CANON, NO_FRONT, NO_LPF)),
    ("front", {"sensor": "Canon EOS 20D", "frontFilter": "Antlia Triband RGB Ultra II", "lpf": ""},
     _osc_trio(CANON, TRIBAND, NO_LPF)),
    ("lpf", {"sensor": "Canon EOS 20D", "frontFilter": "Antlia Triband RGB Ultra II",
             "lpf": "Canon EOS 350D blocking filter"}, _osc_trio(CANON, TRIBAND, CANON_LPF)),
]


@NETWORK
@pytest.mark.parametrize("sharpen", [False, True], ids=["sharpening-off", "sharpening-on"])
def test_changing_each_osc_spectral_choice_reruns_colour_calibration_and_nothing_before_it(
        database: Path, tmp_path: Path, sharpen: bool) -> None:
    """Warm cache, one choice changed per run (sensor, then front filter, then low-pass filter): the colour calibration
    runs again with the full new set of names, the composite's combine and solve do not, no channel stage runs, the
    colour group is not registered again and no master is read. With sharpening on the calibration is the clean
    white-balance measurement (re-measured, never taken from the cache) and the composite applies its gains."""
    osc_path, nb_path, _green = _masters(tmp_path)
    extra = {"sharpenTool": "BlurXTerminator"} if sharpen else {}
    common = _osc_common(osc_path, nb_path, palettes=[], **extra)
    runs = [{"tag": "base", "config": {}}] + [{"tag": tag, "config": {"oscSensor": choice}}
                                              for tag, choice, _cmd in VARIANTS]
    records = _run_plan(tmp_path, common, runs)

    base = records["base"]
    assert base["spcc"] == [_osc_trio(SONY, NO_FRONT, NO_LPF)], base["spcc"]
    for tag, _choice, expected in VARIANTS:
        rec = records[tag]
        assert rec["spcc"] == [expected], (tag, rec["spcc"])   # one call, all three names
        ran = _stages_run(rec)
        assert ran == [("RGB", "spccRGB")], (tag, ran)          # nothing before it, nothing but it
        assert rec["master_reads"] == {}, (tag, rec["master_reads"])
        log = _log_lines(rec)
        assert any("registerGroup HIT" in line for line in log), (tag, log)
        assert not any("registerGroup MISS" in line for line in log), (tag, log)
        if sharpen:
            assert any(line.startswith("cache: clean white balance MISS") for line in log), (tag, log)
            assert any(line.startswith("whitebalance: gains R/G/B = ") for line in log), (tag, log)
        else:
            assert not any("clean white balance" in line for line in log), (tag, log)
            assert any(line == "colour calibration (SPCC) -> RGB" for line in log), (tag, log)


@NETWORK
@pytest.mark.parametrize("order", ["osc-then-mono", "mono-then-osc"])
def test_spcc_osc_then_mono_and_mono_then_osc_in_one_session_each_with_its_full_set(database: Path, tmp_path: Path,
                                                                                     order: str) -> None:
    """An OSC run and a mono run, one after the other in one Siril session: each run's SPCC sends the complete set of
    its own mode, whichever came first."""
    osc_path, nb_path, _green = _masters(tmp_path)
    mono = _mono_masters(tmp_path)
    osc_run = {"tag": "osc", "cache": "cache_osc", "config": {
        "paths": {"OSC": str(osc_path)}, "oscKinds": {"OSC": "broadband"},
        "oscSensor": {"sensor": "Sony IMX571", "frontFilter": "", "lpf": ""}}}
    mono_run = {"tag": "mono", "cache": "cache_mono", "config": {
        "paths": dict(mono), "filters": {"R": "Baader R", "G": "Baader G", "B": "Baader B"}}}
    runs = [osc_run, mono_run] if order == "osc-then-mono" else [mono_run, osc_run]
    records = _run_plan(tmp_path, {"gradientTool": "none", "stretch": False, "palettes": []}, runs)

    assert records["osc"]["spcc"] == [_osc_trio(SONY, NO_FRONT, NO_LPF)], records["osc"]["spcc"]
    assert len(records["mono"]["spcc"]) == 1, records["mono"]["spcc"]
    mono_spcc = records["mono"]["spcc"][0]
    assert re.fullmatch(r'spcc "-monosensor=[^"]+" ' + re.escape(BAADER), mono_spcc), mono_spcc
    for rec in records.values():
        assert rec["produced"] == ["RGB"], rec["produced"]

