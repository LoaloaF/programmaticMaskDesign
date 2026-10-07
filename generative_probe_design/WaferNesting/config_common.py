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


def derive(mod):
    """Attach OUT_DIR, the output paths and the DESIGNS helpers to a config module.

    A config may set OUT_DIR itself (the design-set layout does: <TARGET_SET>/wafer/, so
    the wafer lands beside the shanks it was nested from). Anything that does not falls
    back to runs/<RUN_TAG>/ inside this folder.
    """
    if not hasattr(mod, "OUT_DIR"):
        mod.OUT_DIR = os.path.join(mod.HERE, "runs", mod.RUN_TAG)
    mod.POSES_PATH   = os.path.join(mod.OUT_DIR, "poses.json")
    mod.PREVIEW_PATH = os.path.join(mod.OUT_DIR, "nest_preview.png")
    mod.OVERLAY_PATH = os.path.join(mod.OUT_DIR, "footprints_overlay.png")
    mod.WAFER_OUT    = os.path.join(mod.OUT_DIR, "wafer_%s.gds" % mod.RUN_TAG)
    #Which design variant stage 03 put at which pose. Written by 03 rather than recomputed
    #by anything that needs it: the variant->pose deal is a seeded shuffle, and a second
    #implementation of it is a second chance to disagree with the GDS that was shipped.
    mod.PLACEMENT_PATH = os.path.join(mod.OUT_DIR, "placement.json")
    mod.MAP_PATH       = os.path.join(mod.OUT_DIR, "wafer_map_%s.png" % mod.RUN_TAG)
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
        return os.path.join(mod.OUT_DIR, "footprint_%s.json" % key)

    def offset_path(key):
        return os.path.join(mod.OUT_DIR, "footprint_%s_offset.json" % key)

    def kind_footprint_path(kind):
        """The polygon stage 02 nests a slot of this kind against."""
        ds = designs_of(kind)
        if len(ds) == 1:
            return footprint_path(ds[0]["key"])
        return os.path.join(mod.OUT_DIR, "envelope_%s.json" % kind)

    for fn in (kinds, designs_of, count_of, design_dxf,
               footprint_path, offset_path, kind_footprint_path):
        setattr(mod, fn.__name__, fn)
    return mod
