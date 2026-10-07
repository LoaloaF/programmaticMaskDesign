"""Config -- the FINAL fabricated wafer, MEA1K_wafer4_12x_8x_8x_breakoutboards_Rev2.gds.

This config does not nest anything. The wafer already exists; the job is to record exactly
where every piece sits on it (01c) and to rebuild it, geometrically identical, from the
source designs (01d):

    export WAFERNEST_CONFIG=config_rev2
    KL=/Applications/KLayout/klayout.app/Contents/MacOS/klayout
    $KL -b -r WaferNesting/01c_extract_exact_placements.py   # wafer -> placements.json
    $KL -b -r WaferNesting/01d_rebuild_wafer.py              # placements.json -> wafer GDS

Every part of the wafer comes from one of three places:

    TOP's own shapes   the wafer template (marks + outline), some layers dropped -- 01c
                       works out which, and that the off-wafer mark cluster is gone
    the cells          one source per cell, listed in SOURCES below
    the placements     only the wafer itself knows them -- 01c reads them out exactly

To put NEW content at the SAME placements (e.g. a revised dummy), point that cell's entry
in SOURCES at the new file and run 01d alone: it warns that the source changed and builds
the wafer with it. The check against the target then reports the difference, as it should.

Pure standard library: KLayout's Python runs this.
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))     # .../InterconnectGeneration/WaferNesting
ROOT = os.path.dirname(HERE)                          # .../InterconnectGeneration
DESIGN_DIR = os.path.join(ROOT, "designs")

RUN_TAG = "wafer4_rev2"
OUT_DIR = os.path.join(HERE, "runs", RUN_TAG)
PLACEMENTS_PATH = os.path.join(OUT_DIR, "placements.json")
WAFER_OUT = os.path.join(OUT_DIR, "wafer_%s.gds" % RUN_TAG)

# The wafer to reproduce. Byte-identical to ../reference/WaferWith12_ALLNEW.gds.
TARGET_GDS = os.path.join(os.path.dirname(ROOT), "MEA1K_wafer4_12x_8x_8x_breakoutboards_Rev2.gds")

# Wafer outline + alignment marks. Shapes whose bounding-box centre lies further than
# TEMPLATE_KEEP_RADIUS_MM from the wafer centre are discarded: the template carries a third
# copy of the mark cluster near x = +96 mm, outside the 100 mm wafer.
WAFER_TEMPLATE = os.path.join(HERE, "new_wafer_actually.GDS")
TEMPLATE_KEEP_RADIUS_MM = 50.0

# =============================================================================
# LAYER MAPS -- the FABRICATED numbering
# =============================================================================
# These are the numbers on the final wafer, NOT config.py's LAYER_MAP: the wafer's device
# layers were renumbered by hand (1 -> 3, 3 -> 7) to sit on the matching alignment-mark
# layers. Derived by matching per-layer shape counts and then XOR = 0 against the wafer.
DXF_LAYER_MAP = {                 # InterconnectGeneration DXF layer (lower-cased) -> GDS
    "metal1":             (7, 0),
    "polyimide_negative": (3, 0),
    "etchingpad":         (3, 0),
    "etching":            (5, 0),
    "via":                (5, 0),
    "metal2":             (6, 0),
}                                 # dropped: polyimide (positive), 0, defpoints
DUMMY_LAYER_MAP = {               # 02_make_dummy.py GDS layer -> wafer GDS
    (1, 0): (3, 0),
    (3, 0): (7, 0),
    (5, 0): (5, 0),
    (6, 0): (6, 0),
}

# =============================================================================
# SOURCES -- one per cell on the wafer, keyed by the wafer's own cell name
# =============================================================================
# `layers` maps the source's layers onto the wafer's: DXF sources by layer NAME, GDS sources
# by (layer, datatype). A layer the map does not list is not part of the cell.
# Where the source sits relative to the cell (the connectors are shifted -90 um in y) is NOT
# configured: 01c measures it and records it in placements.json, after proving it with XOR.
SOURCES = {
    "CONN_BIG_NEW": {
        "file": os.path.join(DESIGN_DIR, "new_interconnect_with_connector_chamfered_12Block_56_15.dxf"),
        "layers": DXF_LAYER_MAP},
    "CONN_SMALL_51_35_NEW": {
        "file": os.path.join(DESIGN_DIR, "new_interconnect_with_connector_8blocks_chamfered_1layer_51_35.dxf"),
        "layers": DXF_LAYER_MAP},
    "CONN_SMALL_71_15_NEW": {
        "file": os.path.join(DESIGN_DIR, "new_interconnect_with_connector_8blocks_chamfered_1layer_71_15.dxf"),
        "layers": DXF_LAYER_MAP},
    "dummy_61_10_NEW": {
        "file": os.path.join(DESIGN_DIR, "dummy_new_interconnect_circular_dummy_61_10_padrows.gds"),
        "layers": DUMMY_LAYER_MAP},
    "dummy_35_36_NEW": {
        "file": os.path.join(DESIGN_DIR, "dummy_new_interconnect_circular_dummy_35_36_padrows.gds"),
        "layers": DUMMY_LAYER_MAP},
}

# XOR tolerance, um^2. Zero is what the sources achieve today; this only absorbs
# floating-point noise in area sums, not real geometry.
XOR_TOL_UM2 = 1e-6
