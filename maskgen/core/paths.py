"""Where generated files go: ONE output root, outside the repo, with a fixed layout under it.

The root is, in this order:
    1. an explicit argument (`--out` on the command line),
    2. the environment variable MASKGEN_OUT,
    3. the file `.maskgen_out` in the repo root (one line: the path; git-ignored, so set once per
       checkout -- this is what makes KLayout started from the Dock find it too),
otherwise an error that says how to set it. A root inside the repo is refused: generated files never
land in the code tree again.

Layout under the root (`python3 -m maskgen where` prints it):

    <root>/interconnect/rev<N>/          build_designs.py --rev N   (DXF/GDS, logs/, ref_rev3/)
    <root>/interconnect/adhoc/           a stage script run by hand with a bare output filename
    <root>/electrode/<set>/shanks/       electrode_bundle.batch     (DXF/JSON/PNG of one design set)
    <root>/wafer/<run tag>/              any wafer build            (GDS/OAS/LYP, poses, previews)
    <root>/review/<name>/                comparison sheets
    <root>/index.jsonl                   one line per run (what, when, where, git sha)

Every run folder also gets a manifest.json (maskgen.core.manifest).

Pure standard library and Python-3.8 safe: KLayout's own Python imports this.
"""
import os

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENV = "MASKGEN_OUT"
LOCAL_FILE = os.path.join(REPO, ".maskgen_out")


class OutputRootError(SystemExit):
    pass


def _inside(path, parent):
    path, parent = os.path.realpath(path), os.path.realpath(parent)
    return os.path.commonpath([path, parent]) == parent


def out_root(arg=None, create=True):
    """The output root (absolute). Raises OutputRootError if unset or inside the repo."""
    src, root = None, None
    if arg:
        src, root = "--out", arg
    elif os.environ.get(ENV):
        src, root = "$" + ENV, os.environ[ENV]
    elif os.path.exists(LOCAL_FILE):
        with open(LOCAL_FILE) as f:
            root = f.read().strip()
        src = LOCAL_FILE
    if not root:
        raise OutputRootError(
            "No output root set. Generated files go OUTSIDE the repo; tell maskgen where, either\n"
            "    export %s=/path/to/maskgen_out          (per shell)\n"
            "or  echo /path/to/maskgen_out > %s     (once per checkout; also seen by KLayout's GUI)"
            % (ENV, LOCAL_FILE))
    root = os.path.abspath(os.path.expanduser(root))
    if _inside(root, REPO):
        raise OutputRootError("output root %s (from %s) is inside the repo %s -- refusing"
                              % (root, src, REPO))
    if create:
        os.makedirs(root, exist_ok=True)
    return root


def _sub(*parts, **kw):
    p = os.path.join(out_root(kw.get("root")), *parts)
    if kw.get("create", True):
        os.makedirs(p, exist_ok=True)
    return p


def interconnect_dir(rev, root=None, create=True):
    """<root>/interconnect/rev<N>/ -- `rev` an int, or a name such as "adhoc"."""
    return _sub("interconnect", "rev%d" % rev if isinstance(rev, int) else str(rev),
                root=root, create=create)


def electrode_dir(design_set, root=None, create=True):
    """<root>/electrode/<set>/shanks/"""
    return _sub("electrode", design_set, "shanks", root=root, create=create)


def wafer_dir(run_tag, root=None, create=True):
    """<root>/wafer/<run tag>/"""
    return _sub("wafer", run_tag, root=root, create=create)


def review_dir(name, root=None, create=True):
    """<root>/review/<name>/"""
    return _sub("review", name, root=root, create=create)


def index_path(root=None):
    return os.path.join(out_root(root), "index.jsonl")
