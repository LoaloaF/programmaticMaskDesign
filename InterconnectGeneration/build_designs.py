"""Build every design on an interconnect wafer, for one wafer revision.

    python3 build_designs.py --rev 3                      # all five -> designs/rev3/
    python3 build_designs.py --rev 3 --only 8block_71_15  # one of them
    python3 build_designs.py --rev 2 --out-dir /tmp/rev2  # Rev2 settings, e.g. as a regression

A design is a config plus a few overrides (VARIANTS -- the HANDOVER §4.4 table as data), and a
revision is a few more (REVISIONS). Nothing is edited on disk: each stage runs in its own
process, imports its config, applies the overrides in memory and runs the stage script, so
the configs keep their defaults and one design never leaks into the next.

Stages per design: stage 01 (pad grid -> DXF), then stage 02 (route into the connector ->
DXF, or cap the dummy -> GDS). Logs go to <out-dir>/logs/. The run FAILS (exit 1) if a stage
exits non-zero or a router reports crossings, pad hits or a FAIL line -- checks the routers
themselves only print.
"""
import argparse
import os
import re
import runpy
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))

# name -> config module, stage scripts, file stems (stage-01 output, final output), overrides.
_8B_71_15 = {"GEN_CONFIG": {"pad_side": 73.0, "pad_side_extra": 71.0, "gap": 13.0},
             "GEN_ROUTING": {"via_offset_y": 33.75}, "N_RIGHT_BULBS": 1}
_DUMMY_35_36 = {"GEN_CONFIG": {"pad_side": 37.0, "pad_side_extra": 35.0, "gap": 34.0},
                "GEN_ROUTING": {"via_offset_y": 15.75}}
VARIANTS = {
    "12block": dict(config="12block", gen="01_generate_12block.py", route="02_route_12block.py",
                    mid="new_interconnect_circular_12Block_56_15",
                    final="new_interconnect_with_connector_chamfered_12Block_56_15.dxf", set={}),
    "8block_51_35": dict(config="8block", gen="01_generate_8block.py", route="02_route_8block.py",
                         mid="new_interconnect_circular_8block_51_35",
                         final="new_interconnect_with_connector_8blocks_chamfered_1layer_51_35.dxf",
                         set={}),
    "8block_71_15": dict(config="8block", gen="01_generate_8block.py", route="02_route_8block.py",
                         mid="new_interconnect_circular_8block_71_15",
                         final="new_interconnect_with_connector_8blocks_chamfered_1layer_71_15.dxf",
                         set=_8B_71_15),
    "dummy_61_10": dict(config="dummy", gen="01_generate_12block.py", route="02_make_dummy.py",
                        mid="new_interconnect_circular_dummy_61_10",
                        final="dummy_new_interconnect_circular_dummy_61_10_padrows.gds", set={}),
    "dummy_35_36": dict(config="dummy", gen="01_generate_12block.py", route="02_make_dummy.py",
                        mid="new_interconnect_circular_dummy_35_36",
                        final="dummy_new_interconnect_circular_dummy_35_36_padrows.gds",
                        set=_DUMMY_35_36),
}

# revision -> pad-stack overrides (applied to every design), output folder, file-name suffix.
REVISIONS = {
    2: dict(set={"GEN_CONFIG": {"pad_etch": "square", "metal3": False}},
            out_dir=os.path.join(HERE, "designs"), suffix=""),
    3: dict(set={"GEN_CONFIG": {"pad_etch": "vias", "metal3": True}},
            out_dir=os.path.join(HERE, "designs", "rev3"), suffix="_rev3"),
}


def _with_suffix(fname, suffix):
    stem, ext = os.path.splitext(fname)
    return stem + suffix + ext


def paths(name, rev, out_dir):
    """(stage-01 DXF, final file) for design `name` at revision `rev`."""
    v, r = VARIANTS[name], REVISIONS[rev]
    return (os.path.join(out_dir, v["mid"] + r["suffix"] + ".dxf"),
            os.path.join(out_dir, _with_suffix(v["final"], r["suffix"])))


def _apply(mod, overrides):
    for key, val in overrides.items():
        if isinstance(val, dict):
            getattr(mod, key).update(val)
        else:
            setattr(mod, key, val)


def _run_stage(name, rev, stage, mid, final):
    """In a fresh process: import the config, apply the overrides, run one stage script."""
    import importlib
    os.chdir(HERE)
    sys.path.insert(0, HERE)
    v = VARIANTS[name]
    cfg = importlib.import_module("config_" + v["config"])
    _apply(cfg, v["set"])
    _apply(cfg, REVISIONS[rev]["set"])
    script = v[stage]
    argv = ["--config", v["config"]] if script == "01_generate_12block.py" else []
    argv += ["--out", mid] if stage == "gen" else ["--in", mid, "--out", final]
    sys.argv = [script] + argv
    runpy.run_path(script, run_name="__main__")


def _check_log(text):
    """Problems a stage printed without failing on them."""
    probs = []
    for m in re.finditer(r"crossings=(\d+), pad_hits=(\d+)", text):
        if m.group(1) != "0" or m.group(2) != "0":
            probs.append(m.group(0))
    probs += [ln.strip() for ln in text.splitlines() if re.search(r"\bFAIL\b", ln)]
    return probs


def build(name, rev, out_dir):
    mid, final = paths(name, rev, out_dir)
    logs = os.path.join(out_dir, "logs")
    os.makedirs(logs, exist_ok=True)
    for stage in ("gen", "route"):
        log = os.path.join(logs, "%s_%s.log" % (name, stage))
        cmd = [sys.executable, os.path.abspath(__file__), "--_stage", name, str(rev), stage, mid, final]
        p = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True)
        open(log, "w").write(p.stdout + p.stderr)
        if p.returncode != 0:
            return name, ["%s exited %d -- see %s" % (stage, p.returncode, log)]
        probs = _check_log(p.stdout)
        if probs:
            return name, ["%s: %s -- see %s" % (stage, pr, log) for pr in probs]
    return name, []


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--_stage":
        name, rev, stage, mid, final = sys.argv[2:7]
        _run_stage(name, int(rev), stage, mid, final)
        return
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rev", type=int, required=True, choices=sorted(REVISIONS))
    ap.add_argument("--only", nargs="+", choices=sorted(VARIANTS), default=sorted(VARIANTS))
    ap.add_argument("--out-dir", default=None,
                    help="default: designs/ for Rev2, designs/rev3/ for Rev3")
    a = ap.parse_args()
    out_dir = os.path.abspath(a.out_dir or REVISIONS[a.rev]["out_dir"])
    os.makedirs(out_dir, exist_ok=True)
    print("Rev%d -> %s: %s" % (a.rev, out_dir, ", ".join(a.only)))
    with ThreadPoolExecutor(max_workers=len(a.only)) as ex:
        results = list(ex.map(lambda n: build(n, a.rev, out_dir), a.only))
    bad = 0
    for name, probs in results:
        print("  %-14s %s  %s" % (name, "FAIL" if probs else "ok", os.path.basename(paths(name, a.rev, out_dir)[1])))
        for pr in probs:
            print("      " + pr)
        bad += bool(probs)
    if bad:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
