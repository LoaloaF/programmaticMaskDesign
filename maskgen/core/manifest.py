"""manifest.json per run folder + one line per run in <root>/index.jsonl.

    from maskgen.core import manifest
    manifest.write(folder, kind="interconnect", name="rev4", spec={...},
                   inputs=[paths...], outputs=[paths...])

A manifest answers "what made this folder": the git commit (and whether the tree had local changes),
the command line, the settings that matter (`spec`, with a hash so two runs can be compared at a
glance), every input and output with its md5, and the tool versions. The index lets
`python3 -m maskgen where` list the runs without walking the tree.

Never fails a build: anything it cannot determine is recorded as null.
Pure standard library and Python-3.8 safe: KLayout's own Python imports this.
"""
import hashlib
import json
import os
import subprocess
import sys
import time

from maskgen.core import paths


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_state(repo=paths.REPO):
    def git(*a):
        try:
            return subprocess.run(("git",) + a, cwd=repo, capture_output=True, text=True,
                                  timeout=20).stdout.strip()
        except Exception:
            return None
    sha = git("rev-parse", "HEAD") or None
    if sha is None:                     # not a git checkout (e.g. the golden harness's copied tree)
        return {"sha": None, "dirty": None}
    dirty = git("status", "--porcelain", "--untracked-files=no")
    return {"sha": sha, "dirty": bool(dirty) if dirty is not None else None}


def tool_versions():
    out = {"python": sys.version.split()[0]}
    for m in ("ezdxf", "gdstk", "shapely", "numpy"):
        mod = sys.modules.get(m)
        if mod is not None:
            out[m] = getattr(mod, "__version__", None)
    pya = sys.modules.get("pya") or sys.modules.get("klayout.db")
    if pya is not None:
        try:
            out["klayout"] = pya.Application.instance().version()
        except Exception:
            out["klayout"] = getattr(pya, "__version__", "pya")
    return out


def _files(items, folder):
    out = []
    for p in items or ():
        p = os.path.abspath(p)
        rec = {"path": os.path.relpath(p, folder) if paths._inside(p, folder) else p}
        if os.path.isfile(p):
            rec["md5"] = md5(p)
            rec["bytes"] = os.path.getsize(p)
        else:
            rec["missing"] = True
        out.append(rec)
    return out


def spec_hash(spec):
    return hashlib.sha256(json.dumps(spec, sort_keys=True, default=repr).encode()).hexdigest()[:16]


def write(folder, kind, name, spec=None, inputs=(), outputs=(), extra=None, root=None):
    """Write <folder>/manifest.json and append a line to <root>/index.jsonl. Returns the manifest."""
    folder = os.path.abspath(folder)
    spec = spec or {}
    man = {
        "kind": kind, "name": name, "folder": folder,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "git": git_state(), "argv": [sys.argv[0]] + sys.argv[1:],
        "spec": spec, "spec_hash": spec_hash(spec),
        "inputs": _files(inputs, folder), "outputs": _files(outputs, folder),
        "tools": tool_versions(),
    }
    if extra:
        man.update(extra)
    with open(os.path.join(folder, "manifest.json"), "w") as f:
        json.dump(man, f, indent=1, sort_keys=True, default=repr)
    line = {k: man[k] for k in ("time", "kind", "name", "folder", "spec_hash")}
    line["git"] = (man["git"]["sha"] or "?")[:7] + ("+dirty" if man["git"]["dirty"] else "")
    line["n_outputs"] = len(man["outputs"])
    try:
        with open(paths.index_path(root), "a") as f:
            f.write(json.dumps(line, sort_keys=True) + "\n")
    except SystemExit:
        pass                   # folder given explicitly but no root configured: manifest only
    return man


def report(man):
    """The lines every command prints at the end: where its outputs are."""
    print("\noutputs (%s %s) -> %s" % (man["kind"], man["name"], man["folder"]))
    for o in man["outputs"]:
        print("    %s%s" % (o["path"], "   (MISSING)" if o.get("missing") else ""))
    print("    manifest.json")
