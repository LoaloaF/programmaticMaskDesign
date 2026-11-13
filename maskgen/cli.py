"""maskgen command line.

    python3 -m maskgen where                    # the output root and its layout, last runs
    python3 -m maskgen where interconnect 4     # one folder (+ its manifest summary)
    python3 -m maskgen where electrode 64Ch_4Shankdesigns
    python3 -m maskgen where wafer wafer4_rev4
    python3 -m maskgen runs [-n 20]             # the run index, newest last
    python3 -m maskgen show                     # interconnect revisions: chain, status, changes
    python3 -m maskgen show --rev 4 --design 12block [--changed]   # every resolved setting + source

The build commands still live in their folders for now (refactor phases 2-6 move them here):
    mea1k_interconnects/build_designs.py --rev 4
    cd ufte_probes && python3 -m electrode_bundle.batch
    KL -b -r mea1k_interconnects/wafer_nesting/01d_rebuild_wafer.py      (config_rev4)
    KL -b -r ufte_probes/wafer_nesting/03_export_wafer.py ...
"""
import argparse
import json
import os

from maskgen.core import paths


def _runs(root, n=None, folder=None):
    p = os.path.join(root, "index.jsonl")
    if not os.path.exists(p):
        return []
    rows = [json.loads(l) for l in open(p) if l.strip()]
    if folder:
        rows = [r for r in rows if os.path.abspath(r["folder"]) == os.path.abspath(folder)]
    return rows[-n:] if n else rows


def _show_folder(root, folder):
    print(folder + ("" if os.path.isdir(folder) else "   (does not exist yet)"))
    man = os.path.join(folder, "manifest.json")
    if os.path.exists(man):
        m = json.load(open(man))
        print("  last run %s, git %s%s, %d outputs:"
              % (m["time"], (m["git"]["sha"] or "?")[:7], "+dirty" if m["git"]["dirty"] else "",
                 len(m["outputs"])))
        for o in m["outputs"]:
            print("    " + o["path"])


def cmd_where(a):
    root = paths.out_root(a.out, create=False)
    if not a.what:
        print("output root: %s" % root)
        for label, p in (("interconnect", os.path.join(root, "interconnect", "rev<N>")),
                         ("electrode", os.path.join(root, "electrode", "<set>", "shanks")),
                         ("wafer", os.path.join(root, "wafer", "<run tag>")),
                         ("review", os.path.join(root, "review", "<name>")),
                         ("run index", os.path.join(root, "index.jsonl"))):
            print("  %-13s %s" % (label, p))
        rows = _runs(root, 8)
        if rows:
            print("last runs:")
            for r in rows:
                print("  %s  %-12s %-20s %s" % (r["time"], r["kind"], r["name"], r["folder"]))
        return
    kind, name = a.what[0], (a.what[1] if len(a.what) > 1 else None)
    if kind == "interconnect":
        f = paths.interconnect_dir(int(name) if name and name.isdigit() else (name or 4), root, False)
    elif kind == "electrode":
        f = paths.electrode_dir(name or "64Ch_4Shankdesigns", root, False)
    elif kind == "wafer":
        if not name:
            base = os.path.join(root, "wafer")
            for d in sorted(os.listdir(base)) if os.path.isdir(base) else ():
                print(os.path.join(base, d))
            return
        f = paths.wafer_dir(name, root, False)
    elif kind == "review":
        f = paths.review_dir(name or "", root, False)
    else:
        raise SystemExit("where: interconnect [rev] | electrode [set] | wafer [tag] | review [name]")
    _show_folder(root, f)


def cmd_runs(a):
    root = paths.out_root(a.out, create=False)
    for r in _runs(root, a.n):
        print("%s  %-12s %-20s %-14s %s" % (r["time"], r["kind"], r["name"], r["git"], r["folder"]))


def cmd_show(a):
    """mea1k_interconnects/resolve.py, one process per design (a config is mutated when resolved)."""
    import subprocess
    import sys
    ic = os.path.join(paths.REPO, "mea1k_interconnects")
    designs = [a.design] if a.design or a.rev is None else None
    if designs is None:                      # --rev without --design: every design
        designs = json.loads(subprocess.run(
            [sys.executable, "-c", "import json, catalog; print(json.dumps(list(catalog.DESIGNS)))"],
            cwd=ic, capture_output=True, text=True, check=True).stdout)
    for d in designs:
        cmd = [sys.executable, "resolve.py", "--stage", a.stage]
        if a.rev is not None:
            cmd += ["--rev", str(a.rev)]
        if d:
            cmd += ["--design", d]
        if a.changed:
            cmd.append("--changed")
        subprocess.run(cmd, cwd=ic, check=True)
        if len(designs) > 1:
            print()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="maskgen", description=__doc__.split("\n")[0])
    ap.add_argument("--out", help="output root (default: $MASKGEN_OUT, then <repo>/.maskgen_out)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("where", help="where outputs are")
    w.add_argument("what", nargs="*")
    r = sub.add_parser("runs", help="the run index")
    r.add_argument("-n", type=int, default=20)
    s = sub.add_parser("show", help="interconnect revisions / one design's resolved settings")
    s.add_argument("--rev", type=int)
    s.add_argument("--design")
    s.add_argument("--stage", default="route", choices=("gen", "route"))
    s.add_argument("--changed", action="store_true", help="only settings the design / revisions change")
    a = ap.parse_args(argv)
    {"where": cmd_where, "runs": cmd_runs, "show": cmd_show}[a.cmd](a)


if __name__ == "__main__":
    main()
