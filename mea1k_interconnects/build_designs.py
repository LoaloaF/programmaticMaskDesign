"""Build every design on an interconnect wafer, for one revision of that wafer.

    python3 build_designs.py --wafer wafer4 --rev 4                     # all five -> <root>/<wafer4 name>/Rev4/devices/
    python3 build_designs.py --wafer wafer4 --rev 4 --only 8block_71_15 # one of them
    python3 build_designs.py --wafer wafer6 --rev 1 --only 12block_56_15 # the next wafer
    python3 build_designs.py --wafer wafer4 --rev 2 --out-dir /tmp/rev2 # any folder outside the repo
    python3 build_designs.py --wafer wafer6 --rev 1 --stage01           # pad grids only, ~5 s -> .../Rev1/draft/

A wafer (wafer4, wafer6, ...) is maskgen/core/wafers.py; its revisions are catalog.REVISIONS[wafer].
The output root is --out, else $MASKGEN_OUT, else the path in <repo>/.maskgen_out (maskgen/core/
paths.py); `python3 -m maskgen where wafer6 1` prints the folder and what the last run wrote.
The committed Rev2 reference DXFs in rev2_reference/ are INPUTS (the wafer rebuild reads them) -- nothing
writes there any more.

WHAT gets built is catalog.py: the designs (a base config plus a few overrides) and the revisions
(each its parent plus a list of changes). HOW a (design, revision) becomes settings is resolve.py;
`python3 -m maskgen show --wafer wafer4 --rev 4 --design 12block_56_15` prints the result, setting by setting.
This file only drives the builds. Nothing is edited on disk: each stage runs in its own process, imports
its config, applies the overrides in memory and runs the stage script, so the configs keep their defaults
and one design never leaks into the next.

Every design is a DEVICE with its own folder, devices/<key>/:
    <key>_padgrid.dxf      stage 01: the pad grid and the Metal1 lanes
    <key>.dxf | <key>.gds  stage 02: routed into the connector (or the capped dummy) -- what the wafer places
    device.json            what it is and how it was made: wafer, revision chain, every setting a design or
                           revision changed and who set it, the pad grid, wire count, files
    logs/gen.log, route.log, and ref_rev<N>/ (the band reference stage 02 routes around)
A manifest.json for the whole run (git commit, settings, md5 of every input and output) goes to devices/. The run FAILS (exit 1) if a stage
exits non-zero or a router reports crossings, pad hits or a FAIL line -- checks the routers
themselves only print.
"""
import argparse
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))             # the repo root: the maskgen package
from maskgen.core import manifest, paths as out_paths  # noqa: E402

import catalog            # noqa: E402  -- DESIGNS, REVISIONS
import resolve            # noqa: E402  -- how a (design, revision) becomes settings


def paths(name, dev_dir):
    """(stage-01 pad-grid DXF, final file) of device `name` in its folder `dev_dir`."""
    return tuple(os.path.join(dev_dir, f) for f in catalog.file_names(name))


def _check_log(text):
    """Problems a stage printed without failing on them."""
    probs = []
    for m in re.finditer(r"crossings=(\d+), pad_hits=(\d+)", text):
        if m.group(1) != "0" or m.group(2) != "0":
            probs.append(m.group(0))
    probs += [ln.strip() for ln in text.splitlines() if re.search(r"\bFAIL\b", ln)]
    return probs


def build(name, wafer, rev, dev_dir, stages=("gen", "route")):
    """Build device `name` at (wafer, rev) into its own folder `dev_dir`: the stages, logs/, device.json."""
    mid, final = paths(name, dev_dir)
    logs = os.path.join(dev_dir, "logs")
    os.makedirs(logs, exist_ok=True)
    ref = None
    ref_wr = resolve.needs_band_ref(name, wafer, rev)
    if ref_wr and "route" in stages:
        # the earlier revision's stage-01 DXF, regenerated here so the reference always matches the code
        rw, rn = ref_wr
        ref_dir = os.path.join(dev_dir, "ref_rev%d" % rn if rw == wafer else "ref_%s_rev%d" % (rw, rn))
        ref = paths(name, ref_dir)[0]
        n, probs = build(name, rw, rn, ref_dir, stages=("gen",))
        if probs:
            return name, ["band reference (%s) failed: %s" % (catalog.label(rw, rn), p_) for p_ in probs]
    for stage in stages:
        log = os.path.join(logs, "%s.log" % stage)
        cmd = [sys.executable, os.path.abspath(__file__), "--_stage", name, wafer, str(rev), stage, mid, final]
        if ref and stage == "route":
            cmd.append(ref)
        p = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True)
        open(log, "w").write(p.stdout + p.stderr)
        if p.returncode != 0:
            return name, ["%s exited %d -- see %s" % (stage, p.returncode, log)]
        probs = _check_log(p.stdout)
        if probs:
            return name, ["%s: %s -- see %s" % (stage, pr, log) for pr in probs]
    p = subprocess.run([sys.executable, os.path.abspath(__file__), "--_summary", name, wafer, str(rev),
                        os.path.join(dev_dir, "device.json")], cwd=HERE, capture_output=True, text=True)
    if p.returncode != 0:
        return name, ["device.json: %s" % (p.stderr.strip().splitlines() or ["?"])[-1]]
    return name, []


def _summary(name, wafer, rev, path):
    """device.json: what the device is and how it was made (resolve.summary) + its files' sizes."""
    import json
    d = resolve.summary(name, wafer, rev)
    d["stages_run"] = sorted(f[:-4] for f in os.listdir(os.path.join(os.path.dirname(path), "logs")))
    d["file_sizes_bytes"] = {f: os.path.getsize(os.path.join(os.path.dirname(path), f))
                             for f in d["files"].values() if os.path.exists(os.path.join(os.path.dirname(path), f))}
    with open(path, "w") as f:
        json.dump(d, f, indent=1, default=repr)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--_stage":
        name, wafer, rev, stage, mid, final = sys.argv[2:8]
        resolve.run_stage(name, wafer, int(rev), stage, mid, final, sys.argv[8] if len(sys.argv) > 8 else None)
        return
    if len(sys.argv) > 1 and sys.argv[1] == "--_summary":
        _summary(sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5])
        return
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--wafer", required=True, choices=sorted(catalog.REVISIONS),
                    help="the wafer (maskgen/core/wafers.py)")
    ap.add_argument("--rev", type=int, required=True, help="its revision (catalog.REVISIONS[wafer])")
    ap.add_argument("--only", nargs="+", choices=sorted(catalog.DESIGNS), default=sorted(catalog.DESIGNS))
    ap.add_argument("--out", default=None,
                    help="output root (default: $MASKGEN_OUT, then <repo>/.maskgen_out)")
    ap.add_argument("--out-dir", default=None,
                    help="exact folder for the device folders instead (default: <out root>/<wafer name>/"
                         "Rev<N>/devices/, or .../Rev<N>/draft/ with --stage01)")
    ap.add_argument("--stage01", action="store_true",
                    help="stage 01 only (the pad grids), e.g. to review a pad change quickly")
    a = ap.parse_args()
    catalog.get(a.wafer, a.rev)                       # a clear error for a revision that does not exist
    stages = ("gen",) if a.stage01 else ("gen", "route")
    if a.out_dir:
        out_dir = os.path.abspath(a.out_dir)
        if out_paths._inside(out_dir, out_paths.REPO):
            raise SystemExit("--out-dir %s is inside the repo -- generated files go outside it" % out_dir)
    else:
        out_dir = out_paths.devices_dir(a.wafer, a.rev, a.out)
        if a.stage01:
            out_dir = os.path.join(os.path.dirname(out_dir), "draft")
    os.makedirs(out_dir, exist_ok=True)
    print("%s -> %s: %s" % (catalog.label(a.wafer, a.rev), out_dir, ", ".join(a.only)))
    with ThreadPoolExecutor(max_workers=len(a.only)) as ex:
        results = list(ex.map(lambda n: build(n, a.wafer, a.rev, os.path.join(out_dir, n), stages), a.only))
    bad = 0
    for name, probs in results:
        print("  %-14s %s  %s" % (name, "FAIL" if probs else "ok",
                                  os.path.relpath(paths(name, os.path.join(out_dir, name))[0 if a.stage01 else 1],
                                                  out_dir)))
        for pr in probs:
            print("      " + pr)
        bad += bool(probs)
    _write_manifest(a, out_dir, stages, results)
    if bad:
        raise SystemExit(1)


def _write_manifest(a, out_dir, stages, results):
    outs = []
    for name in a.only:
        mid, final = paths(name, os.path.join(out_dir, name))
        outs += ([mid] if a.stage01 else [mid, final]) + [os.path.join(out_dir, name, "device.json")]
    cfgs = sorted({"config_%s.py" % catalog.DESIGNS[n].base for n in a.only})
    ins = [os.path.join(HERE, f) for f in cfgs + ["config_common.py", "catalog.py", os.path.join("lib", "constants.py")]]
    spec = {"wafer": a.wafer, "rev": a.rev, "stages": list(stages),
            "designs": {n: catalog.DESIGNS[n]._asdict() for n in a.only},
            "revisions": [dict(r._asdict(), wafer=w) for w, r in catalog.chain(a.wafer, a.rev)]}
    m = manifest.write(out_dir, "designs", catalog.label(a.wafer, a.rev), spec=spec, inputs=ins, outputs=outs,
                       extra={"failed": [n for n, pr in results if pr]}, root=a.out)
    manifest.report(m)


if __name__ == "__main__":
    main()
