"""Golden-reference harness for the maskgen refactor (plan Phase 0).

Every refactor step must leave the generated geometry IDENTICAL. This script makes that a
one-command check:

    export MASKGEN_OUT=/path/outside/the/repo
    python3 tests/golden/golden.py capture 667ba2d   # once: build the reference from a commit
    python3 tests/golden/golden.py check             # after a change: rebuild + compare (~4 min)
    python3 tests/golden/golden.py check --only all  # + the frozen legacy Rev2 reproduction
    python3 tests/golden/golden.py check --only interconnect   # just what a change touches
                                     # parts: interconnect (designs, configs, Rev4 wafer),
                                     #        electrode (probes + their wafer), legacy (Rev2 repro)
    python3 tests/golden/golden.py compare DIR       # compare an already-built candidate folder

`capture` exports the commit with `git archive` into a scratch tree, `check` copies the
current working tree (tracked + untracked, never ignored outputs). Both then run the SAME
build commands (produce() below) in that tree, so nothing is ever written into the repo:

    interconnect  build_designs.py --wafer wafer4 --rev 2/3/4   -> interconnect/rev<N>/ (flattened, old names)
    wafer         maskgen/wafer/rebuild.py config_rev4 (committed Rev2 placements) -> wafer/wafer4_rev4/
    legacy        legacy/rev2_repro 01c (placements == frozen) + 01d config_rev2
                  (must end GEOMETRY IDENTICAL)       -> wafer/wafer4_rev2/
    electrode     electrode_bundle.batch --no-render   -> electrode/64Ch_4Shankdesigns/shanks/
    wafer         maskgen/wafer/export.py config_64ch_4shank (frozen poses) -> wafer/e64ch_4shank/
    config        the namespace every interconnect stage sees -> config/<rev>_<design>_<stage>.json

Each output is FINGERPRINTED (level A): DXF per layer as a sorted multiset of canonical
entities, GDS/OAS per cell+layer as sorted polygons and references, JSON canonicalised,
other text by content. The manifest (hashes only) is written to
tests/golden/manifest_<sha>.json; the files themselves stay in $MASKGEN_OUT/_golden/<sha>/.
A fingerprint mismatch on DXF/GDS falls back to a KLayout per-layer XOR (level B) against
the golden file: XOR = 0 everywhere still passes (e.g. only the entity order changed).
Logs are scanned (level C): crossings/pad_hits != 0 or a FAIL line fails.

WHEN THE BUILD COMMANDS CHANGE (later phases move to the maskgen CLI), update produce() so it
writes the same files to the same relative paths; the manifest keys are those paths.
The build commands write into <out>/_work/ (their $MASKGEN_OUT); produce() copies from there.
produce() always matches the CURRENT layout: to capture a commit from before refactor Phase 1
(e.g. 667ba2d) use the golden.py of that time, `git show d188a16:tests/golden/golden.py`.
"""
import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
FROZEN = os.path.join(HERE, "frozen")
KL = os.environ.get("KLAYOUT", "/Applications/KLayout/klayout.app/Contents/MacOS/klayout")
PY = sys.executable
ROUND = 9                                    # decimals kept in fingerprints (um)
GOLDEN_DIR = "_golden"                       # <root>/_golden/: <sha>/ (the reference), check/, tree_*/

# The reference (667ba2d) predates device folders and device names: its artefacts are FLAT, under the
# file names of that time. produce() builds with today's commands and copies every file to that name.
_OLD_NAMES = {   # device -> (stage-01 stem, final file) at 667ba2d; the revision suffix is appended
    "12block_56_15": ("new_interconnect_circular_12Block_56_15",
                      "new_interconnect_with_connector_chamfered_12Block_56_15.dxf"),
    "8block_51_35": ("new_interconnect_circular_8block_51_35",
                     "new_interconnect_with_connector_8blocks_chamfered_1layer_51_35.dxf"),
    "8block_71_15": ("new_interconnect_circular_8block_71_15",
                     "new_interconnect_with_connector_8blocks_chamfered_1layer_71_15.dxf"),
    "dummy_61_10": ("new_interconnect_circular_dummy_61_10", "dummy_new_interconnect_circular_dummy_61_10_padrows.gds"),
    "dummy_35_36": ("new_interconnect_circular_dummy_35_36", "dummy_new_interconnect_circular_dummy_35_36_padrows.gds"),
}
_OLD_SUFFIX = {2: "", 3: "_rev3", 4: "_rev4"}
_OLD_KEY = {"12block_56_15": "12block"}      # the config snapshots' file names


# ----------------------------------------------------------------------------- paths
def out_root(arg=None):
    root = os.path.abspath(arg or os.environ.get("MASKGEN_OUT") or "")
    if not (arg or os.environ.get("MASKGEN_OUT")):
        raise SystemExit("set MASKGEN_OUT (or --out) to a folder OUTSIDE the repo")
    if os.path.commonpath([root, REPO]) == REPO:
        raise SystemExit("output root %s is inside the repo %s -- refusing" % (root, REPO))
    return root


def run(cmd, cwd, log, env=None):
    os.makedirs(os.path.dirname(log), exist_ok=True)
    t = time.time()
    p = subprocess.run(cmd, cwd=cwd, env=dict(os.environ, **(env or {})),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    open(log, "w").write(p.stdout)
    print("  %-60s %4.0fs  exit %d" % (" ".join(os.path.basename(c) for c in cmd[:4])[:60],
                                         time.time() - t, p.returncode), flush=True)
    if p.returncode != 0:
        raise SystemExit("command failed (see %s): %s" % (log, " ".join(cmd)))
    return p.stdout


# ----------------------------------------------------------------------------- build
SNAPSHOT = r'''
import json, os, re, runpy, sys
sys.path.insert(0, os.getcwd())
import catalog as c
import resolve as r
name, rev, stage, out, root, wafer = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6]

def clean(mod):
    return {k: v for k, v in vars(mod).items()
            if not k.startswith("__") and not callable(v) and type(v).__name__ != "module"}

def dump(script, run_name=None):
    cfg = sys.modules["config_" + c.DESIGNS[name].base]
    snap = {"script": script, "argv": sys.argv[1:], "config": clean(cfg)}
    if "lib.constants" in sys.modules:
        snap["lib.constants"] = clean(sys.modules["lib.constants"])
    s = json.dumps(snap, sort_keys=True, indent=1, default=repr)
    s = re.sub(r" at 0x[0-9a-f]+", "", s.replace(root, "<ROOT>"))
    open(out, "w").write(s)

r.runpy.run_path = dump
band = "<band_ref>" if (stage == "route" and r.needs_band_ref(name, wafer, rev)) else None
r.run_stage(name, wafer, rev, stage, "<mid>", "<final>", band)
'''


def _work(out):
    """The output root the build commands see ($MASKGEN_OUT): a scratch folder inside `out` that the
    fingerprint skips (it starts with "_"). The outputs are then copied to fixed relative paths."""
    w = os.path.join(out, "_work")
    os.makedirs(w, exist_ok=True)
    return {"MASKGEN_OUT": w}


def _copy_wafer_files(src, stem, dst, tag, also=()):
    """<src>/<stem>.gds/.oas/.lyp -> <dst>/wafer_<tag>.* (the manifest's names, from before wafers had names)."""
    os.makedirs(dst, exist_ok=True)
    for f in os.listdir(src):
        base, ext = os.path.splitext(f)
        if base == stem and ext in (".gds", ".oas", ".lyp"):
            shutil.copy2(os.path.join(src, f), os.path.join(dst, "wafer_%s%s" % (tag, ext)))
        elif f in also:
            shutil.copy2(os.path.join(src, f), dst)


def _wafer_paths(work, key, rev):
    """(devices/ folder, layout folder, file stem) of a wafer revision under the work root."""
    sys.path.insert(0, REPO)
    from maskgen.core import wafers
    d = os.path.join(work, wafers.get(key)["name"], "Rev%d" % rev)
    return os.path.join(d, "devices"), d, wafers.stem(key, rev)


# The three independent chains. Each produces the artefacts under the listed manifest prefixes;
# `check --only` runs a subset and compares only its prefixes.
PARTS = {
    "interconnect": ("interconnect/", "config/", "wafer/wafer4_rev4/"),
    "electrode": ("electrode/", "wafer/e64ch_4shank/"),
    "legacy": ("wafer/wafer4_rev2/",),
}
JOBS = max(2, (os.cpu_count() or 4))


def _snapshot(ic, snap, name, rev, stage, dst, tree, env):
    subprocess.run([PY, snap, name, str(rev), stage, dst, tree, "wafer4"], cwd=ic, check=True,
                   stdout=subprocess.DEVNULL, env=dict(os.environ, **env))


def _flatten_interconnect(devices, dst, rev):
    """devices/<device>/{<device>_padgrid.dxf, <device>.dxf|.gds, ref_rev3/...} -> dst/ under the 667ba2d names."""
    os.makedirs(dst, exist_ok=True)
    sfx = _OLD_SUFFIX[rev]
    for dev in sorted(os.listdir(devices)):
        ddir = os.path.join(devices, dev)
        if not os.path.isdir(ddir):
            continue
        mid, final = _OLD_NAMES[dev]
        stem, ext = final.rsplit(".", 1)
        shutil.copy2(os.path.join(ddir, dev + "_padgrid.dxf"), os.path.join(dst, mid + sfx + ".dxf"))
        shutil.copy2(os.path.join(ddir, "%s.%s" % (dev, ext)), os.path.join(dst, "%s%s.%s" % (stem, sfx, ext)))
        for ref in sorted(f for f in os.listdir(ddir) if f.startswith("ref_rev")):
            rn = int(ref[len("ref_rev"):])
            os.makedirs(os.path.join(dst, ref), exist_ok=True)
            shutil.copy2(os.path.join(ddir, ref, dev + "_padgrid.dxf"),
                         os.path.join(dst, ref, mid + _OLD_SUFFIX[rn] + ".dxf"))


def produce_interconnect(tree, out, self_check=False):
    ic = os.path.join(tree, "mea1k_interconnects")
    env = _work(out)
    built = os.path.join(out, "_devices")                   # skipped by the fingerprint ("_")
    with cf.ThreadPoolExecutor(3) as ex:                    # the three revisions are independent
        list(ex.map(lambda rev: run(
            [PY, "build_designs.py", "--wafer", "wafer4", "--rev", str(rev),
             "--out-dir", os.path.join(built, "rev%d" % rev)],
            ic, os.path.join(out, "logs", "build_rev%d.log" % rev), env=env), (2, 3, 4)))
    for rev in (2, 3, 4):
        _flatten_interconnect(os.path.join(built, "rev%d" % rev), os.path.join(out, "interconnect", "rev%d" % rev), rev)
    # config snapshots: what every stage sees after the catalog's overrides
    snap = os.path.join(out, "_snapshot.py")
    open(snap, "w").write(SNAPSHOT)
    lists = json.loads(subprocess.run(
        [PY, "-c", "import json, catalog; print(json.dumps([sorted(catalog.REVISIONS['wafer4']), catalog.devices('wafer4', 4)]))"],
        cwd=ic, capture_output=True, text=True, check=True).stdout)
    os.makedirs(os.path.join(out, "config"), exist_ok=True)
    with cf.ThreadPoolExecutor(JOBS) as ex:
        list(ex.map(lambda a: _snapshot(ic, snap, *a, tree, env),
                    [(name, rev, stage, os.path.join(out, "config", "rev%d_%s_%s.json"
                                                     % (rev, _OLD_KEY.get(name, name), stage)))
                     for rev in lists[0] for name in lists[1] for stage in ("gen", "route")]))
    os.remove(snap)
    work = env["MASKGEN_OUT"]
    # Rev4 wafer: rebuild.py (default config_rev4) reads wafer4 Rev4's devices/ = the build above. Its own check
    # against the fabricated Rev2 wafer is skipped unless --self-checks: the golden wafer passed it, and
    # the fingerprint comparison proves this one is the same wafer.
    devices, layout, stem = _wafer_paths(work, "wafer4", 4)
    os.makedirs(layout, exist_ok=True)
    os.symlink(os.path.join(built, "rev4"), devices)
    log = run([KL, "-b", "-r", os.path.join(tree, "maskgen", "wafer", "rebuild.py")], ic,
              os.path.join(out, "logs", "01d_wafer4_rev4.log"), env=dict(env, WAFER_CHECK="1" if self_check else "0"))
    if self_check and not re.search(r"IDENTICAL to .* except the expected", log):
        raise SystemExit("01d config_rev4 did not pass its own check")
    _copy_wafer_files(layout, stem, os.path.join(out, "wafer", "wafer4_rev4"), "wafer4_rev4")


def produce_legacy(tree, out, self_check=False):
    """legacy/rev2_repro: measure the Rev2 placements off the fabricated wafer again, rebuild it."""
    work = _work(out)["MASKGEN_OUT"]
    leg = os.path.join(tree, "legacy", "rev2_repro")
    env2 = {"MASKGEN_OUT": work, "WAFERNEST_CONFIG": "config_rev2"}
    run([KL, "-b", "-r", os.path.join(leg, "01c_extract_exact_placements.py")], tree,
        os.path.join(out, "logs", "01c_wafer4_rev2.log"), env=env2)
    pl = os.path.join(work, "wafer", "wafer4_rev2", "placements.json")
    got = json.loads(open(pl).read().replace(tree, "<ROOT>"))
    want = json.load(open(os.path.join(FROZEN, "interconnect_wafer4_rev2", "placements.json")))
    if got != want:
        raise SystemExit("legacy 01c: placements.json differs from the frozen copy")
    log = run([KL, "-b", "-r", os.path.join(leg, "01d_rebuild_wafer.py")], tree,
              os.path.join(out, "logs", "01d_wafer4_rev2.log"), env=env2)
    if "GEOMETRY IDENTICAL" not in log:
        raise SystemExit("legacy 01d config_rev2 did not end GEOMETRY IDENTICAL")
    _copy_wafer_files(os.path.join(work, "wafer", "wafer4_rev2"), "wafer_wafer4_rev2",     # legacy: own layout
                      os.path.join(out, "wafer", "wafer4_rev2"), "wafer4_rev2")


def produce_electrode(tree, out, self_check=False):
    gpd = os.path.join(tree, "ufte_probes")
    env = _work(out)
    work = env["MASKGEN_OUT"]
    run([PY, "-m", "electrode_bundle.batch", "--no-render", "--jobs", str(min(8, JOBS))], gpd,
        os.path.join(out, "logs", "electrode_batch.log"), env=env)
    devices, wdir, stem = _wafer_paths(work, "wafer5", 1)
    shanks = os.path.join(out, "electrode", "64Ch_4Shankdesigns", "shanks")    # the 667ba2d keys: flat
    os.makedirs(shanks, exist_ok=True)
    for d, _, fs in os.walk(devices):
        for f in fs:
            if not (f.endswith(".png") or f in (".DS_Store", "manifest.json", "device.json")):
                shutil.copy2(os.path.join(d, f), os.path.join(shanks, f))
    nest = os.path.join(wdir, "nest")
    os.makedirs(nest, exist_ok=True)
    fz = os.path.join(FROZEN, "electrode_64ch_4shank_wafer")
    for f in os.listdir(fz):
        if f != "placement.json":                         # an OUTPUT of 03; it must make it again
            shutil.copy2(os.path.join(fz, f), nest)
    run([KL, "-b", "-r", os.path.join(tree, "maskgen", "wafer", "export.py")], gpd,
        os.path.join(out, "logs", "electrode_03_export.log"),
        env=dict(env, WAFERNEST_CONFIG="config_64ch_4shank"))
    _copy_wafer_files(wdir, stem, os.path.join(out, "wafer", "e64ch_4shank"), "64Ch_4Shankdesigns",
                      also=("placement.json",))


PRODUCERS = {"interconnect": produce_interconnect, "electrode": produce_electrode, "legacy": produce_legacy}


def produce(tree, out, parts=tuple(PARTS), self_check=False):
    """Run the builds of `parts` in `tree`, concurrently, collect the outputs under `out`."""
    os.makedirs(out, exist_ok=True)
    with cf.ThreadPoolExecutor(len(parts)) as ex:
        jobs = [ex.submit(PRODUCERS[p], tree, out, self_check) for p in parts]
        for j in jobs:
            j.result()


# ----------------------------------------------------------------------------- fingerprints
def _norm(x):
    if isinstance(x, float):
        v = round(x, ROUND)
        return 0.0 if v == 0 else v
    if isinstance(x, (int, str, bool)) or x is None:
        return x
    if isinstance(x, dict):
        return {str(k): _norm(v) for k, v in x.items()}
    if hasattr(x, "__iter__"):
        return [_norm(v) for v in x]
    return str(x)


def _sha(items):
    h = hashlib.sha256()
    for s in items:
        h.update(s.encode())
        h.update(b"\n")
    return h.hexdigest()[:16]


def _dxf_entity(e):
    attrs = {k: v for k, v in e.dxfattribs().items() if k not in ("handle", "owner", "layer")}
    t = e.dxftype()
    geo = None
    if t == "LWPOLYLINE":
        geo = [list(e.get_points("xyseb")), e.closed]
    elif t == "POLYLINE":
        geo = [[(v.dxf.location, v.dxf.get("bulge", 0)) for v in e.vertices], e.is_closed]
    elif t == "HATCH":
        geo = []
        for p in e.paths:
            if hasattr(p, "vertices"):
                geo.append(["P", p.is_closed, list(p.vertices)])
            else:
                geo.append(["E", [[type(ed).__name__,
                                   {k: getattr(ed, k) for k in dir(ed) if not k.startswith("_")
                                    and not callable(getattr(ed, k))}] for ed in p.edges]])
    elif t == "SPLINE":
        geo = [list(e.control_points), list(e.fit_points), list(e.knots), list(e.weights)]
    elif t == "MTEXT":
        geo = e.text
    return json.dumps([t, _norm(attrs), _norm(geo)], sort_keys=True)


def fp_dxf(path):
    import ezdxf
    doc = ezdxf.readfile(path)
    per = {}
    for e in doc.modelspace():
        per.setdefault(e.dxf.layer, []).append(_dxf_entity(e))
    for blk in doc.blocks:
        if blk.name.startswith("*"):
            continue
        for e in blk:
            per.setdefault("BLOCK:%s:%s" % (blk.name, e.dxf.layer), []).append(_dxf_entity(e))
    out = {k: {"n": len(v), "sha": _sha(sorted(v))} for k, v in per.items()}
    custom = sorted(doc.header.custom_vars)
    if custom:
        out["@custom_vars"] = {"n": len(custom), "sha": _sha([json.dumps(_norm(c)) for c in custom])}
    return {"kind": "dxf", "layers": out}


def fp_gds(path):
    import gdstk
    lib = gdstk.read_oas(path) if path.endswith(".oas") else gdstk.read_gds(path)
    out = {}
    def expand(objs):
        """OASIS stores repeated shapes as one shape + a repetition: expand to explicit copies."""
        out = []
        for o in objs:
            if o.repetition.size > 1:
                c = o.copy()
                out += [c] + c.apply_repetition()
            else:
                out.append(o)
        return out

    for cell in lib.cells:
        per = {}
        for p in expand(cell.polygons):
            per.setdefault("%s/%d/%d" % (cell.name, p.layer, p.datatype), []).append(
                json.dumps(_norm(p.points.tolist())))
        for p in cell.paths:
            for q in p.to_polygons():
                per.setdefault("%s/%d/%d" % (cell.name, q.layer, q.datatype), []).append(
                    json.dumps(_norm(q.points.tolist())))
        for lb in cell.labels:
            per.setdefault("%s/label/%d/%d" % (cell.name, lb.layer, lb.texttype), []).append(
                json.dumps(_norm([lb.text, lb.origin])))
        refs = [json.dumps(_norm([r.cell.name if hasattr(r.cell, "name") else str(r.cell),
                                  r.origin, r.rotation, r.magnification, r.x_reflection,
                                  ])) for r in expand(cell.references)]
        if refs:
            per["%s/@refs" % cell.name] = refs
        for k, v in per.items():
            out[k] = {"n": len(v), "sha": _sha(sorted(v))}
    return {"kind": "gds", "layers": out}


def fp_json(path, roots, fix=None):
    """`roots` = {token: path}: machine/run-specific prefixes replaced before hashing. `fix(data)`, if
    given, rewrites the parsed JSON first (see stage_scripts_now)."""
    s = open(path).read()
    for tok, r in sorted(roots.items(), key=lambda kv: -len(kv[1])):
        s = s.replace(r, tok)
    data = json.loads(s)
    if fix:
        data = fix(data)
    data = _norm(data)
    return {"kind": "json", "sha": _sha([json.dumps(data, sort_keys=True)])}


# Refactor Phase 8 deleted the per-design entry points; the catalog now runs the merged stage scripts
# with the design as an argument. A config snapshot records the script it ran and its argv, so a
# snapshot taken before that (the 667ba2d reference) names the old entry point. This rewrites such a
# snapshot into the call that does the same thing today; the config namespace itself is untouched and
# must still match exactly. The reference is NOT re-captured.
_OLD_STAGE_SCRIPTS = {                       # old entry point -> (script now, argument it implied)
    "01_generate_12block.py": ("01_generate.py", ["--config", "12block"]),
    "01_generate_8block.py": ("01_generate.py", ["--config", "8block"]),
    "02_route_12block.py": ("02_route.py", ["--design", "12block"]),
    "02_route_8block.py": ("02_route.py", ["--design", "8block"]),
}


def stage_scripts_now(snap):
    old = _OLD_STAGE_SCRIPTS.get(snap.get("script"))
    if old:
        snap = dict(snap, script=old[0])
        argv = list(snap.get("argv", []))
        if argv[:1] != old[1][:1]:           # 01_generate_12block.py was already called with --config
            argv = old[1] + argv
        snap["argv"] = argv
    return snap


def fp_text(path):
    return {"kind": "text", "sha": _sha([open(path, errors="replace").read()])}


def check_logs(out):
    """Level C: the problems build_designs._check_log looks for, over every log in `out`."""
    probs = []
    for dirpath, _, files in os.walk(out):
        for f in files:
            if not f.endswith(".log"):
                continue
            txt = open(os.path.join(dirpath, f), errors="replace").read()
            rel = os.path.relpath(os.path.join(dirpath, f), out)
            for m in re.finditer(r"crossings=(\d+), pad_hits=(\d+)", txt):
                if m.group(1) != "0" or m.group(2) != "0":
                    probs.append("%s: %s" % (rel, m.group(0)))
            probs += ["%s: %s" % (rel, ln.strip()) for ln in txt.splitlines() if re.search(r"\bFAIL\b", ln)]
    return probs


def fingerprint(out, roots=None):
    roots = dict(roots or {}, **{"<OUT>": out})
    arts = {}
    for dirpath, dirs, files in os.walk(out):
        dirs[:] = sorted(d for d in dirs if not d.startswith("_"))
        for f in sorted(files):
            p = os.path.join(dirpath, f)
            rel = os.path.relpath(p, out)
            ext = os.path.splitext(f)[1].lower()
            if ext == ".dxf":
                arts[rel] = fp_dxf(p)
            elif ext in (".gds", ".oas"):
                arts[rel] = fp_gds(p)
            elif ext == ".json" and not f.startswith("manifest"):    # run manifests: timestamps
                arts[rel] = fp_json(p, roots)
            elif ext in (".lyp", ".csv", ".txt"):
                arts[rel] = fp_text(p)
    return arts


def tool_versions():
    import ezdxf, gdstk, shapely, numpy
    kl = subprocess.run([KL, "-v"], capture_output=True, text=True).stdout.strip()
    return {"python": sys.version.split()[0], "ezdxf": ezdxf.__version__, "gdstk": gdstk.__version__,
            "shapely": shapely.__version__, "numpy": numpy.__version__, "klayout": kl}


# ----------------------------------------------------------------------------- compare
XOR_PYA = r'''
import json, os, pya
def load(p):
    ly = pya.Layout()
    if p.lower().endswith(".dxf"):
        o = pya.LoadLayoutOptions(); o.dxf_unit = 1.0; o.dxf_polyline_mode = 2; ly.read(p, o)
    else:
        ly.read(p)
    return ly
a, b = load(os.environ["XA"]), load(os.environ["XB"])
def regions(ly):
    out = {}
    for top in ly.top_cells():
        for li in ly.layer_indexes():
            i = ly.get_info(li)
            k = i.name or "%d/%d" % (i.layer, i.datatype)
            r = pya.Region(top.begin_shapes_rec(li))
            out[k] = out.get(k, pya.Region()) + r
    return out
ra, rb = regions(a), regions(b)
res = {}
for k in sorted(set(ra) | set(rb)):
    x = (ra.get(k, pya.Region()) ^ rb.get(k, pya.Region())).area() * a.dbu * a.dbu
    res[k] = x
open(os.environ["XOUT"], "w").write(json.dumps(res))
'''


def xor_files(a, b, scratch):
    script = os.path.join(scratch, "_xor.py")
    res = os.path.join(scratch, "_xor.json")
    open(script, "w").write(XOR_PYA)
    subprocess.run([KL, "-b", "-r", script], env=dict(os.environ, XA=a, XB=b, XOUT=res),
                   stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, check=True)
    return json.load(open(res))


def compare(gold_manifest, cand_dir, gold_dir, tree=None, parts=tuple(PARTS)):
    """Returns the list of failures (empty = identical). Only artefacts of `parts` are compared."""
    G = json.load(open(gold_manifest))
    C = fingerprint(cand_dir, roots={"<TREE>": tree} if tree else None)
    fails, viaB, renamed = [], [], []
    prefixes = tuple(p for part in parts for p in PARTS[part])
    wanted = {k: v for k, v in G["artifacts"].items() if k.startswith(prefixes)}
    for key, g in sorted(wanted.items()):
        c = C.get(key)
        if c is None:
            fails.append("MISSING  %s" % key)
            continue
        if c == g:
            continue
        if g["kind"] in ("dxf", "gds"):
            gl, cl = g["layers"], c["layers"]
            diff = sorted(k for k in set(gl) | set(cl) if gl.get(k) != cl.get(k))
            gf = os.path.join(gold_dir, key)
            if os.path.exists(gf):
                x = xor_files(gf, os.path.join(cand_dir, key), cand_dir)
                bad = {k: v for k, v in x.items() if v > 1e-6}
                if not bad:
                    viaB.append("%s (fingerprint differs on %s; XOR = 0)" % (key, ", ".join(diff[:5])))
                    continue
                fails.append("GEOMETRY %s: XOR um2 %s" % (key, json.dumps(bad)))
            else:
                fails.append("CHANGED  %s on %s (golden file missing for XOR)" % (key, ", ".join(diff[:8])))
        elif key.startswith("config/") and os.path.exists(os.path.join(gold_dir, key)):
            # the reference names the old per-design stage scripts (see stage_scripts_now)
            if fp_json(os.path.join(gold_dir, key), {}, stage_scripts_now) == \
                    fp_json(os.path.join(cand_dir, key), {"<TREE>": tree} if tree else {}, stage_scripts_now):
                renamed.append(key)
                continue
            fails.append("CHANGED  %s (json, also after mapping the old stage-script names)" % key)
        else:
            fails.append("CHANGED  %s (%s)" % (key, g["kind"]))
    extra = sorted(set(C) - set(G["artifacts"]))
    print("  compared %d artefacts (%s)" % (len(wanted), ", ".join(parts)))
    probs = check_logs(cand_dir)
    fails += ["LOG      %s" % p for p in probs if p not in G.get("log_problems", [])]
    for v in viaB:
        print("  equal by XOR: " + v)
    if renamed:
        print("  equal after mapping the old stage-script names: %d config snapshot(s)" % len(renamed))
    if extra:
        print("  note: %d new output(s) not in the golden set: %s" % (len(extra), ", ".join(extra[:6])))
    return fails


# ----------------------------------------------------------------------------- commands
def cmd_capture(sha, root):
    full = subprocess.run(["git", "rev-parse", sha], cwd=REPO, capture_output=True, text=True,
                          check=True).stdout.strip()
    short = full[:7]
    gold = os.path.join(root, GOLDEN_DIR, short)
    if os.path.exists(gold):
        raise SystemExit("%s exists -- delete it to capture again" % gold)
    tree = os.path.join(root, GOLDEN_DIR, "tree_%s" % short)
    shutil.rmtree(tree, ignore_errors=True)
    os.makedirs(tree)
    print("exporting %s -> %s" % (short, tree))
    arch = subprocess.Popen(["git", "archive", full], cwd=REPO, stdout=subprocess.PIPE)
    subprocess.run(["tar", "-x", "-C", tree], stdin=arch.stdout, check=True)
    arch.wait()
    print("building into %s" % gold)
    produce(tree, gold, tuple(PARTS), self_check=True)
    arts = fingerprint(gold, roots={"<TREE>": tree})
    man = {"sha": full, "captured": time.strftime("%Y-%m-%d %H:%M"), "tools": tool_versions(),
           "frozen": {os.path.relpath(os.path.join(d, f), FROZEN):
                      hashlib.md5(open(os.path.join(d, f), "rb").read()).hexdigest()
                      for d, _, fs in os.walk(FROZEN) for f in fs},
           "log_problems": check_logs(gold), "artifacts": arts}
    path = os.path.join(HERE, "manifest_%s.json" % short)
    json.dump(man, open(path, "w"), indent=1, sort_keys=True)
    shutil.copy2(path, os.path.join(gold, "manifest.json"))
    shutil.rmtree(tree)
    print("\n%d artifacts fingerprinted; %d log problem(s) recorded\nmanifest: %s\ngolden files: %s"
          % (len(arts), len(man["log_problems"]), path, gold))


def _latest_manifest():
    ms = sorted((os.path.getmtime(os.path.join(HERE, f)), f) for f in os.listdir(HERE)
                if f.startswith("manifest_") and f.endswith(".json"))
    if not ms:
        raise SystemExit("no manifest_<sha>.json in %s -- run capture first" % HERE)
    return os.path.join(HERE, ms[-1][1])


def _report(fails, manifest):
    if fails:
        print("\n%d DIFFERENCE(S) against %s:" % (len(fails), os.path.basename(manifest)))
        for f in fails:
            print("  " + f)
        raise SystemExit(1)
    print("\nGOLDEN CHECK PASSED against %s" % os.path.basename(manifest))


def cmd_check(root, manifest, keep, parts=tuple(PARTS), self_check=False):
    manifest = manifest or _latest_manifest()
    G = json.load(open(manifest))
    if G["tools"] != tool_versions():
        print("WARNING: tool versions differ from the capture:\n  golden %s\n  now    %s"
              % (G["tools"], tool_versions()))
    stamp = time.strftime("%Y%m%d_%H%M%S")
    tree = os.path.join(root, GOLDEN_DIR, "tree_check_%s" % stamp)
    cand = os.path.join(root, GOLDEN_DIR, "check", stamp)
    files = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=REPO,
                           capture_output=True, text=True, check=True).stdout.split("\0")
    for f in filter(None, files):
        src = os.path.join(REPO, f)
        if os.path.isfile(src):
            dst = os.path.join(tree, f)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
    print("working tree copied -> %s\nbuilding %s into %s" % (tree, ", ".join(parts), cand))
    t0 = time.time()
    produce(tree, cand, parts, self_check)
    print("  built in %.0f s" % (time.time() - t0))
    gold = os.path.join(root, GOLDEN_DIR, G["sha"][:7])
    fails = compare(manifest, cand, gold, tree, parts)
    shutil.rmtree(tree)
    if not keep and not fails:
        shutil.rmtree(cand)
    _report(fails, manifest)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", help="output root (default $MASKGEN_OUT); must be outside the repo")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture"); c.add_argument("sha")
    k = sub.add_parser("check"); k.add_argument("--manifest"); k.add_argument("--keep", action="store_true")
    k.add_argument("--only", default="interconnect,electrode",
                   help="comma list of %s, or all (default: interconnect,electrode -- legacy/ is frozen "
                        "and reads only committed files; check it with --only all or --only legacy "
                        "when those change)" % ", ".join(PARTS))
    k.add_argument("--self-checks", action="store_true",
                   help="also run 01d's own check of the Rev4 wafer against the fabricated Rev2 wafer (+4 min)")
    m = sub.add_parser("compare"); m.add_argument("dir"); m.add_argument("--manifest")
    a = ap.parse_args()
    root = out_root(a.out)
    if a.cmd == "capture":
        cmd_capture(a.sha, root)
    elif a.cmd == "check":
        parts = tuple(PARTS) if a.only == "all" else tuple(p for p in a.only.split(",") if p)
        bad = [p for p in parts if p not in PARTS]
        if bad:
            raise SystemExit("unknown part(s) %s; choose from %s" % (bad, ", ".join(PARTS)))
        cmd_check(root, a.manifest, a.keep, parts, a.self_checks)
    else:
        manifest = a.manifest or _latest_manifest()
        G = json.load(open(manifest))
        _report(compare(manifest, os.path.abspath(a.dir), os.path.join(root, GOLDEN_DIR, G["sha"][:7])),
                manifest)


if __name__ == "__main__":
    main()
