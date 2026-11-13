"""Golden-reference harness for the maskgen refactor (plan Phase 0).

Every refactor step must leave the generated geometry IDENTICAL. This script makes that a
one-command check:

    export MASKGEN_OUT=/path/outside/the/repo
    python3 tests/golden/golden.py capture 667ba2d   # once: build the reference from a commit
    python3 tests/golden/golden.py check             # after a change: rebuild + compare
    python3 tests/golden/golden.py compare DIR       # compare an already-built candidate folder

`capture` exports the commit with `git archive` into a scratch tree, `check` copies the
current working tree (tracked + untracked, never ignored outputs). Both then run the SAME
build commands (produce() below) in that tree, so nothing is ever written into the repo:

    interconnect  build_designs.py --rev 2/3/4         -> interconnect/rev<N>/
    wafer         01d config_rev4 (committed Rev2 placements) -> wafer/wafer4_rev4/
    legacy        legacy/rev2_repro 01c (placements == frozen) + 01d config_rev2
                  (must end GEOMETRY IDENTICAL)       -> wafer/wafer4_rev2/
    electrode     electrode_bundle.batch --no-render   -> electrode/64Ch_4Shankdesigns/shanks/
    wafer         electrode 03_export_wafer (frozen poses) -> wafer/e64ch_4shank/
    config        the namespace every interconnect stage sees -> config/<rev>_<design>_<stage>.json

Each output is FINGERPRINTED (level A): DXF per layer as a sorted multiset of canonical
entities, GDS/OAS per cell+layer as sorted polygons and references, JSON canonicalised,
other text by content. The manifest (hashes only) is written to
tests/golden/manifest_<sha>.json; the files themselves stay in $MASKGEN_OUT/golden/<sha>/.
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
name, rev, stage, out, root = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5]

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
band = "<band_ref>" if (stage == "route" and r.needs_band_ref(name, rev)) else None
r.run_stage(name, rev, stage, "<mid>", "<final>", band)
'''


def _work(out):
    """The output root the build commands see ($MASKGEN_OUT): a scratch folder inside `out` that the
    fingerprint skips (it starts with "_"). The outputs are then copied to fixed relative paths."""
    w = os.path.join(out, "_work")
    os.makedirs(w, exist_ok=True)
    return {"MASKGEN_OUT": w}


def _copy_wafer_files(src, dst, also=()):
    os.makedirs(dst, exist_ok=True)
    for f in os.listdir(src):
        if f.startswith("wafer_") and f.endswith((".gds", ".oas", ".lyp")) or f in also:
            shutil.copy2(os.path.join(src, f), dst)


def produce_interconnect(tree, out):
    ic = os.path.join(tree, "mea1k_interconnects")
    env = _work(out)
    for rev in (2, 3, 4):
        run([PY, "build_designs.py", "--rev", str(rev), "--out-dir",
             os.path.join(out, "interconnect", "rev%d" % rev)],
            ic, os.path.join(out, "logs", "build_rev%d.log" % rev), env=env)
    # config snapshots: what every stage sees after build_designs' overrides
    snap = os.path.join(out, "_snapshot.py")
    open(snap, "w").write(SNAPSHOT)
    lists = json.loads(subprocess.run(
        [PY, "-c", "import json, catalog; print(json.dumps([sorted(catalog.REVISIONS), sorted(catalog.DESIGNS)]))"],
        cwd=ic, capture_output=True, text=True, check=True).stdout)
    for rev in lists[0]:
        for name in lists[1]:
            for stage in ("gen", "route"):
                dst = os.path.join(out, "config", "rev%d_%s_%s.json" % (rev, name, stage))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                subprocess.run([PY, snap, name, str(rev), stage, dst, tree], cwd=ic, check=True,
                               stdout=subprocess.DEVNULL, env=dict(os.environ, **env))
    os.remove(snap)
    work = env["MASKGEN_OUT"]
    # Rev4 wafer: 01d (default config_rev4) reads <root>/interconnect/rev4 = the build above
    os.makedirs(os.path.join(work, "interconnect"), exist_ok=True)
    os.symlink(os.path.join(out, "interconnect", "rev4"), os.path.join(work, "interconnect", "rev4"))
    log = run([KL, "-b", "-r", "wafer_nesting/01d_rebuild_wafer.py"], ic,
              os.path.join(out, "logs", "01d_wafer4_rev4.log"), env=env)
    if not re.search(r"IDENTICAL to .* except the expected", log):
        raise SystemExit("01d config_rev4 did not pass its own check")
    _copy_wafer_files(os.path.join(work, "wafer", "wafer4_rev4"), os.path.join(out, "wafer", "wafer4_rev4"))
    # legacy: measure the Rev2 placements off the fabricated wafer again, rebuild it
    leg = os.path.join(tree, "legacy", "rev2_repro")
    env2 = dict(env, WAFERNEST_CONFIG="config_rev2")
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
    _copy_wafer_files(os.path.join(work, "wafer", "wafer4_rev2"), os.path.join(out, "wafer", "wafer4_rev2"))


def produce_electrode(tree, out):
    gpd = os.path.join(tree, "ufte_probes")
    env = _work(out)
    work = env["MASKGEN_OUT"]
    run([PY, "-m", "electrode_bundle.batch", "--no-render"], gpd,
        os.path.join(out, "logs", "electrode_batch.log"), env=env)
    shanks = os.path.join(work, "electrode", "64Ch_4Shankdesigns", "shanks")
    shutil.copytree(shanks, os.path.join(out, "electrode", "64Ch_4Shankdesigns", "shanks"),
                    ignore=shutil.ignore_patterns("*.png", ".DS_Store", "manifest.json"))
    wdir = os.path.join(work, "wafer", "64Ch_4Shankdesigns")
    os.makedirs(wdir, exist_ok=True)
    fz = os.path.join(FROZEN, "electrode_64ch_4shank_wafer")
    for f in os.listdir(fz):
        if f != "placement.json":                         # an OUTPUT of 03; it must make it again
            shutil.copy2(os.path.join(fz, f), wdir)
    run([KL, "-b", "-r", "wafer_nesting/03_export_wafer.py"], gpd,
        os.path.join(out, "logs", "electrode_03_export.log"),
        env=dict(env, WAFERNEST_CONFIG="config_64ch_4shank"))
    _copy_wafer_files(wdir, os.path.join(out, "wafer", "e64ch_4shank"), also=("placement.json",))


def produce(tree, out):
    """Run every build in `tree`, collect the outputs under `out`."""
    os.makedirs(out, exist_ok=True)
    with cf.ThreadPoolExecutor(2) as ex:
        jobs = [ex.submit(produce_interconnect, tree, out), ex.submit(produce_electrode, tree, out)]
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


def fp_json(path, roots):
    """`roots` = {token: path}: machine/run-specific prefixes replaced before hashing."""
    s = open(path).read()
    for tok, r in sorted(roots.items(), key=lambda kv: -len(kv[1])):
        s = s.replace(r, tok)
    data = _norm(json.loads(s))
    return {"kind": "json", "sha": _sha([json.dumps(data, sort_keys=True)])}


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


def compare(gold_manifest, cand_dir, gold_dir, tree=None):
    """Returns the list of failures (empty = identical)."""
    G = json.load(open(gold_manifest))
    C = fingerprint(cand_dir, roots={"<TREE>": tree} if tree else None)
    fails, viaB = [], []
    for key, g in sorted(G["artifacts"].items()):
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
        else:
            fails.append("CHANGED  %s (%s)" % (key, g["kind"]))
    extra = sorted(set(C) - set(G["artifacts"]))
    probs = check_logs(cand_dir)
    fails += ["LOG      %s" % p for p in probs if p not in G.get("log_problems", [])]
    for v in viaB:
        print("  equal by XOR: " + v)
    if extra:
        print("  note: %d new output(s) not in the golden set: %s" % (len(extra), ", ".join(extra[:6])))
    return fails


# ----------------------------------------------------------------------------- commands
def cmd_capture(sha, root):
    full = subprocess.run(["git", "rev-parse", sha], cwd=REPO, capture_output=True, text=True,
                          check=True).stdout.strip()
    short = full[:7]
    gold = os.path.join(root, "golden", short)
    if os.path.exists(gold):
        raise SystemExit("%s exists -- delete it to capture again" % gold)
    tree = os.path.join(root, "_tree_%s" % short)
    shutil.rmtree(tree, ignore_errors=True)
    os.makedirs(tree)
    print("exporting %s -> %s" % (short, tree))
    arch = subprocess.Popen(["git", "archive", full], cwd=REPO, stdout=subprocess.PIPE)
    subprocess.run(["tar", "-x", "-C", tree], stdin=arch.stdout, check=True)
    arch.wait()
    print("building into %s" % gold)
    produce(tree, gold)
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


def cmd_check(root, manifest, keep):
    manifest = manifest or _latest_manifest()
    G = json.load(open(manifest))
    if G["tools"] != tool_versions():
        print("WARNING: tool versions differ from the capture:\n  golden %s\n  now    %s"
              % (G["tools"], tool_versions()))
    stamp = time.strftime("%Y%m%d_%H%M%S")
    tree = os.path.join(root, "_tree_check_%s" % stamp)
    cand = os.path.join(root, "check", stamp)
    files = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=REPO,
                           capture_output=True, text=True, check=True).stdout.split("\0")
    for f in filter(None, files):
        src = os.path.join(REPO, f)
        if os.path.isfile(src):
            dst = os.path.join(tree, f)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
    print("working tree copied -> %s\nbuilding into %s" % (tree, cand))
    produce(tree, cand)
    gold = os.path.join(root, "golden", G["sha"][:7])
    fails = compare(manifest, cand, gold, tree)
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
    m = sub.add_parser("compare"); m.add_argument("dir"); m.add_argument("--manifest")
    a = ap.parse_args()
    root = out_root(a.out)
    if a.cmd == "capture":
        cmd_capture(a.sha, root)
    elif a.cmd == "check":
        cmd_check(root, a.manifest, a.keep)
    else:
        manifest = a.manifest or _latest_manifest()
        G = json.load(open(manifest))
        _report(compare(manifest, os.path.abspath(a.dir), os.path.join(root, "golden", G["sha"][:7])),
                manifest)


if __name__ == "__main__":
    main()
