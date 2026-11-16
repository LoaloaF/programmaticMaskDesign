"""Config -- the Rev3 wafer: the Rev2 wafer with the Rev3 pad stack, at the SAME placements.

Rev3 changes only the pads of every design (build_designs.py --rev 3):
  * the final PI etch is no longer one square per pad but via-sized circles around the pad
    perimeter, drawn on Polyimide_Negative                         -> wafer layer 3
  * a new layer Metal3 = the pad squares again, no wires            -> wafer layer 8
and TOP gains the template's layer-8 alignment marks for Metal3: the small (non-inverted)
mark in the slot Rev2 left free, plus that layer's coarse marks.

Every piece keeps its Rev2 position, so this builds from Rev2's placements.json -- nothing is
nested. 01d checks the result against the Rev2 wafer and passes only if layers 3 and 8 are the
only ones that changed, and only inside the pad metal (layer 6):

    cd mea1k_interconnects
    python3 build_designs.py --wafer wafer4 --rev 3     # <out root>/MEA1K_wafer4_12x_8x_8x_breakoutboards/Rev3/devices/
    KL=/Applications/KLayout/klayout.app/Contents/MacOS/klayout
    WAFERNEST_CONFIG=config_rev2 $KL -b -r ../legacy/rev2_repro/01c_extract_exact_placements.py
    WAFERNEST_CONFIG=config_rev3 $KL -b -r ../legacy/rev2_repro/01d_rebuild_wafer.py

Pure standard library: KLayout's Python runs this.
"""
import os

import config_rev2 as _rev2

HERE = _rev2.HERE
ROOT = _rev2.ROOT
# LEGACY (refactor Phase 1): Rev3 designs from `build_designs.py --rev 3`, now under the output root.
REV3_DIR = os.path.join(_rev2._out_root(), "MEA1K_wafer4_12x_8x_8x_breakoutboards", "Rev3", "devices")

RUN_TAG = "wafer4_rev3"
OUT_DIR = os.path.join(_rev2._out_root(), "wafer", RUN_TAG)
WAFER_OUT = os.path.join(OUT_DIR, "wafer_%s.gds" % RUN_TAG)

# Rev2's placements, measured off the fabricated wafer by 01c (run it with config_rev2 first).
# It also names the wafer 01d checks against: the Rev2 wafer.
PLACEMENTS_PATH = _rev2.PLACEMENTS_PATH
WAFER_TEMPLATE = _rev2.WAFER_TEMPLATE
TEMPLATE_KEEP_RADIUS_MM = _rev2.TEMPLATE_KEEP_RADIUS_MM

METAL3 = (8, 0)

# Rev2's maps plus Metal3. The etch circles need no entry: they are on Polyimide_Negative
# (DXF) / layer 1 (dummy GDS), which Rev2 already maps to 3.
DXF_LAYER_MAP = dict(_rev2.DXF_LAYER_MAP, metal3=METAL3)
DUMMY_LAYER_MAP = dict(_rev2.DUMMY_LAYER_MAP)
DUMMY_LAYER_MAP[(8, 0)] = METAL3

# TOP: Rev2's template layers (from placements.json) plus these, wafer layer -> template layer.
# Template layer 8 holds the small alignment mark in the third slot of the mark row (between
# Etching's and PI's inverted marks) and the coarse marks every layer has; Rev2 dropped it.
EXTRA_TOP_LAYERS = {"8/0": list(METAL3)}

_REV3 = {     # paths only (2026-11-16): one folder per device, <device>/<device>.dxf|.gds
    "CONN_BIG_NEW": "12block_56_15/12block_56_15.dxf",
    "CONN_SMALL_51_35_NEW": "8block_51_35/8block_51_35.dxf",
    "CONN_SMALL_71_15_NEW": "8block_71_15/8block_71_15.dxf",
    "dummy_61_10_NEW": "dummy_61_10/dummy_61_10.gds",
    "dummy_35_36_NEW": "dummy_35_36/dummy_35_36.gds",
}
SOURCES = {cell: {"file": os.path.join(REV3_DIR, f),
                  "layers": DUMMY_LAYER_MAP if f.endswith(".gds") else DXF_LAYER_MAP}
           for cell, f in _REV3.items()}

# What 01d may find changed against Rev2, and where each change must lie (per device cell).
EXPECTED_CHANGED_LAYERS = [(3, 0), METAL3]
CHANGES_WITHIN = {(3, 0): (6, 0), METAL3: (6, 0)}     # inside the Metal2 pads

XOR_TOL_UM2 = _rev2.XOR_TOL_UM2

# 01e_overlay_wafers.py: Rev2 on datatype 0, Rev3 on 1, their XOR on 2, cells suffixed by tag.
OVERLAY_TAGS = ("REV2", "REV3")
