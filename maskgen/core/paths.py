"""Where generated files go: ONE output root, outside the repo, with a fixed layout under it.

The root is, in this order:
    1. an explicit argument (`--out` on the command line),
    2. the environment variable MASKGEN_OUT,
    3. the file `.maskgen_out` in the repo root (one line: the path; git-ignored, so set once per
       checkout -- this is what makes KLayout started from the Dock find it too),
otherwise an error that says how to set it. A root inside the repo is refused: generated files never
land in the code tree again.

Layout under the root (`python3 -m maskgen where` prints it). One folder per WAFER, one per revision
(maskgen/core/wafers.py names them), and in it one folder per DEVICE -- a single piece that the wafer
layout then places, many times:

    <root>/<wafer name>/Rev<N>/
        devices/<device>/                ONE device: <device>.dxf|.gds (what goes on the wafer), its
                                         intermediates, device.json (what it is, how it was made), logs/
        devices/                         + the set-wide files (side-by-side drawings, manifest.json)
        <wafer name>[_Rev<N>].gds/.oas/.lyp, placement.json, <...>_map.png    the wafer layout
        nest/                            footprints, poses, nest preview (nested wafers)
        review/<name>/                   review sheets of this wafer revision
    <root>/electrode/<set>/              an electrode design set that belongs to no wafer
    <root>/adhoc/                        a stage script run by hand with a bare output filename
    <root>/_golden/                      the regression harness (tests/golden/golden.py): its reference
                                         build and its check rebuilds -- not for reading
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


def wafer_rev_dir(wafer, rev, root=None, create=True):
    """<root>/<wafer name>/Rev<N>/ -- the wafer layout of `wafer` (a maskgen/core/wafers.py key) at `rev`."""
    from maskgen.core import wafers
    wafers.rev(wafer, rev)
    return _sub(wafers.get(wafer)["name"], "Rev%d" % rev, root=root, create=create)


def devices_dir(wafer, rev, root=None, create=True):
    """<root>/<wafer name>/Rev<N>/devices/ -- every device of that wafer revision, one folder each."""
    from maskgen.core import wafers
    wafers.rev(wafer, rev)
    return _sub(wafers.get(wafer)["name"], "Rev%d" % rev, "devices", root=root, create=create)


def device_dir(wafer, rev, device, root=None, create=True):
    """<root>/<wafer name>/Rev<N>/devices/<device>/ -- one device."""
    return _sub(os.path.relpath(devices_dir(wafer, rev, root, False), out_root(root)), device,
                root=root, create=create)


def electrode_dir(design_set, root=None, create=True):
    """The folder of an electrode design set: its wafer revision's devices/ (wafers.py design_set), else
    <root>/electrode/<set>/. Each geometry is a device folder below it."""
    from maskgen.core import wafers
    w = wafers.of_design_set(design_set)
    if w:
        return devices_dir(w[0], w[1], root, create)
    return _sub("electrode", design_set, root=root, create=create)


def adhoc_dir(root=None, create=True):
    """<root>/adhoc/ -- a stage script run by hand with a bare output filename."""
    return _sub("adhoc", root=root, create=create)


def review_dir(name, wafer, rev, root=None, create=True):
    """<root>/<wafer name>/Rev<N>/review/<name>/ -- a sheet belongs to the wafer revision it reviews."""
    from maskgen.core import wafers
    wafers.rev(wafer, rev)
    return _sub(wafers.get(wafer)["name"], "Rev%d" % rev, "review", name, root=root, create=create)


def index_path(root=None):
    return os.path.join(out_root(root), "index.jsonl")
