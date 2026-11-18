"""Config -- wafer6 Rev1: 8x64 + 12-block devices at 2:1, dummy pairs as fill (2/3 shorted, 1/3 not), nested
(python3 -m maskgen wafer wafer6)."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.dirname(ROOT)); sys.path.insert(0, ROOT)
from maskgen.core import paths as _paths, layers as _layers
from maskgen import wafer as _wafer
import catalog as _catalog

WAFER, REV = "wafer6", 1
TARGET_SET = "MEA1K_wafer6_12x_8x_breakoutboards"      # title of the wafer map
DESIGN_DIR = _paths.devices_dir(WAFER, REV, create=False)
WAFER_TEMPLATE, MARKS_WKT = _wafer.TEMPLATE_GDS, _wafer.MARKS_WKT
TEMPLATE_KEEP_RADIUS_MM = 50.0

# the mix the nester aims for (proportions only): 2 x 8x64 per 12-block; then dummy pairs fill what is left
SHANK_RATIO  = {"8x64_56_15": 2, "12block_56_15": 1}
FILL_KINDS   = ["dummy_2x768_56_15"]
SHANK_COUNTS = {"8x64_56_15": 8, "12block_56_15": 4, "dummy_2x768_56_15": 18}   # the nester's result (2026-11-16)
# nest on the polyimide outline + half the cutout band: neighbours' cutout bands may overlap, a band never
# reaches a neighbour's polyimide (maskgen/wafer/footprints.py extract_outline; checked exactly in the nester)
PI_BAND_UM = 200.0
# variants sharing a kind's slots (after nesting): about 1/3 of the dummy pairs non-shorted (no bottom handle, its
# outline inside the shorted pair's, so it takes the same slots). Unlisted kinds: one design, key = kind.
VARIANTS = {"dummy_2x768_56_15": {"dummy_2x768_56_15": 12, "dummy_2x768_56_15_open": 6}}
DESIGNS = [{"key": v, "kind": k, "count": m, "dxf": "%s/%s" % (v, _catalog.file_names(v)[1])}
           for k, n in SHANK_COUNTS.items() for v, m in VARIANTS.get(k, {k: n}).items()]
assert all(sum(VARIANTS[k].values()) == SHANK_COUNTS[k] for k in VARIANTS)
# a kind's variants are placed on the FIRST variant's centre (extract_footprints.py, PI_BAND_UM): they share its
# frame, so a smaller outline sits exactly inside the slot nested for the first
VARIANTS_SHARE_CENTRE = True

CLOSE_GAP, SIMPLIFY, ENVELOPE_PAD, ENVELOPE_SIMPLIFY = 0.08, 0.03, 0.01, 0.01
WAFER_R, WAFER_FLAT_Y, EDGE_EXCL, GAP, MARK_BUF = 50.0, -47.285, 3.0, 0.05, 0.7

LAYERSET = _layers.LAYERSETS[_catalog.get(WAFER, REV).layerset]
LAYER_MAP, LAYER_NAMES = LAYERSET.dxf_map, LAYERSET.names
LYP_FROM_LAYERSET = True                # .lyp styled as wafer4 Rev4's (export.py); without it a generic palette
DUMMY_LAYER_MAP = LAYERSET.dummy_map    # the dummy's GDS layers (02_make_dummy.py) -> wafer
MARK_LAYERS_UNUSED = []                 # as on wafer4 Rev4: all five mark slots are used
MARK_STENCIL, MARK_COMPANION = (5, 0), (7, 0)
MARK_INVERT_LAYERS, MARK_SELFTEST_LAYER = [(1, 0)], (3, 0)
MARK_VERNIER_W_UM, MARK_BAND_Y_UM = (550.0, 700.0), 1000.0

from maskgen.wafer import config_common
config_common.derive(sys.modules[__name__])
