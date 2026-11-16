"""Derived settings shared by the config modules.

Holds no paths of its own and looks for nothing on disk. Every input location is stated
explicitly in the config that uses it -- see the PATHS block at the top of config.py or
config_64ch_4shank.py. This module only computes the things that follow mechanically
from those settings, so each config file states only what is specific to it.

Call it at the bottom of a config:

    import sys, config_common
    config_common.derive(sys.modules[__name__])

Pure standard library: stages 03 and 04 import configs from inside KLayout's Python,
which has no shapely/numpy/matplotlib.
"""
import os
import sys


def _paths():
    """maskgen.core.paths (pure stdlib, so KLayout's Python can import it)."""
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if repo not in sys.path:
        sys.path.insert(0, repo)
    from maskgen.core import paths, wafers
    return paths, wafers


def derive(mod):
    """Attach OUT_DIR, the output paths and the DESIGNS helpers to a config module.

    A config names its wafer revision -- WAFER (a maskgen/core/wafers.py key) and REV -- and gets
        OUT_DIR    <out root>/<wafer name>/Rev<N>/ (maskgen/core/paths.py -- outside the repo)
        RUN_TAG    the wafer's file stem: <wafer name> for Rev1, <wafer name>_Rev<N> after
        WAFER_OUT  OUT_DIR/<RUN_TAG>.gds (+ .oas, .lyp next to it)
        NEST_DIR   OUT_DIR/nest/ -- the nest's working files: footprints, poses, previews
    unless it sets them itself.
    """
    paths, wafers = _paths()
    if not hasattr(mod, "RUN_TAG"):
        mod.RUN_TAG = wafers.stem(mod.WAFER, mod.REV)
    if not hasattr(mod, "OUT_DIR"):
        mod.OUT_DIR = paths.wafer_rev_dir(mod.WAFER, mod.REV, create=False)
    if not hasattr(mod, "NEST_DIR"):
        mod.NEST_DIR = os.path.join(mod.OUT_DIR, "nest")
    mod.POSES_PATH   = os.path.join(mod.NEST_DIR, "poses.json")
    mod.PREVIEW_PATH = os.path.join(mod.NEST_DIR, "nest_preview.png")
    mod.OVERLAY_PATH = os.path.join(mod.NEST_DIR, "footprints_overlay.png")
    if not hasattr(mod, "WAFER_OUT"):
        mod.WAFER_OUT = os.path.join(mod.OUT_DIR, "%s.gds" % mod.RUN_TAG)
    #Which design variant stage 03 put at which pose. Written by 03 rather than recomputed
    #by anything that needs it: the variant->pose deal is a seeded shuffle, and a second
    #implementation of it is a second chance to disagree with the GDS that was shipped.
    mod.PLACEMENT_PATH = os.path.join(mod.OUT_DIR, "placement.json")
    mod.MAP_PATH       = os.path.join(mod.OUT_DIR, "%s_map.png" % mod.RUN_TAG)
    mod.R_MAX_MM     = mod.WAFER_R - mod.EDGE_EXCL + 0.5

    def kinds():
        out = []
        for d in mod.DESIGNS:
            if d["kind"] not in out:
                out.append(d["kind"])
        return out

    def designs_of(kind):
        return [d for d in mod.DESIGNS if d["kind"] == kind]

    def count_of(kind):
        return sum(d["count"] for d in designs_of(kind))

    def design_dxf(d):
        """Absolute path to a design's DXF: absolute as given, else under DESIGN_DIR."""
        p = d["dxf"]
        return p if os.path.isabs(p) else os.path.join(mod.DESIGN_DIR, p)

    def footprint_path(key):
        return os.path.join(mod.NEST_DIR, "footprint_%s.json" % key)

    def offset_path(key):
        return os.path.join(mod.NEST_DIR, "footprint_%s_offset.json" % key)

    def kind_footprint_path(kind):
        """The polygon stage 02 nests a slot of this kind against."""
        ds = designs_of(kind)
        if len(ds) == 1:
            return footprint_path(ds[0]["key"])
        return os.path.join(mod.NEST_DIR, "envelope_%s.json" % kind)

    for fn in (kinds, designs_of, count_of, design_dxf,
               footprint_path, offset_path, kind_footprint_path):
        setattr(mod, fn.__name__, fn)
    return mod
