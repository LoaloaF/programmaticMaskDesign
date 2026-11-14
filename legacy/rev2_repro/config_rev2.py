"""Config -- the FINAL fabricated wafer, MEA1K_wafer4_12x_8x_8x_breakoutboards_Rev2.gds.

This config does not nest anything. The wafer already exists; the job is to record exactly
where every piece sits on it (01c) and to rebuild it, geometrically identical, from the
source designs (01d):

    export WAFERNEST_CONFIG=config_rev2
    KL=/Applications/KLayout/klayout.app/Contents/MacOS/klayout
    $KL -b -r legacy/rev2_repro/01c_extract_exact_placements.py   # wafer -> placements.json
    $KL -b -r legacy/rev2_repro/01d_rebuild_wafer.py              # placements.json -> wafer GDS

LEGACY (frozen at 667ba2d, refactor Phase 1): this folder is the Rev2 reproduction, copied out of
mea1k_interconnects/wafer_nesting/ verbatim except for the PATHS block below (README.md here).

Every part of the wafer comes from one of three places:

    TOP's own shapes   the wafer template (marks + outline), some layers dropped -- 01c
                       works out which, and that the off-wafer mark cluster is gone
    the cells          one source per cell, listed in SOURCES below
    the placements     only the wafer itself knows them -- 01c reads them out exactly

To put NEW content at the SAME placements, write a revision config that reuses this one's
placements.json -- config_rev3.py is the example (HANDOVER.md §5.3) -- rather than editing
SOURCES here: this config stays the proof that the Rev2 wafer is reproduced.

Pure standard library: KLayout's Python runs this.
"""
import os

# --- PATHS: the only edit against the original (mea1k_interconnects/wafer_nesting/config_rev2.py
# at 667ba2d). The inputs are still read where they are committed; the outputs go to the maskgen
# output root, resolved HERE without importing maskgen, so the refactor can never change this folder.
HERE = os.path.dirname(os.path.abspath(__file__))     # .../legacy/rev2_repro
REPO = os.path.dirname(os.path.dirname(HERE))
ROOT = os.path.join(REPO, "mea1k_interconnects")   # the committed Rev2 designs + wafer template
DESIGN_DIR = os.path.join(ROOT, "rev2_reference")     # was designs/ (renamed 2026-11)


def _out_root():
    """$MASKGEN_OUT, else the path in <repo>/.maskgen_out -- the same rule as maskgen/core/paths.py."""
    root = os.environ.get("MASKGEN_OUT")
    if not root and os.path.exists(os.path.join(REPO, ".maskgen_out")):
        root = open(os.path.join(REPO, ".maskgen_out")).read().strip()
    if not root:
        raise SystemExit("set MASKGEN_OUT (or write the path into %s/.maskgen_out)" % REPO)
    return os.path.abspath(os.path.expanduser(root))


RUN_TAG = "wafer4_rev2"
OUT_DIR = os.path.join(_out_root(), "wafer", RUN_TAG)
PLACEMENTS_PATH = os.path.join(OUT_DIR, "placements.json")
WAFER_OUT = os.path.join(OUT_DIR, "wafer_%s.gds" % RUN_TAG)

# The wafer to reproduce (the fabricated Rev2 wafer, committed at the repo root).
TARGET_GDS = os.path.join(os.path.dirname(ROOT), "MEA1K_wafer4_12x_8x_8x_breakoutboards_Rev2.gds")

# Wafer outline + alignment marks. Shapes whose bounding-box centre lies further than
# TEMPLATE_KEEP_RADIUS_MM from the wafer centre are discarded: the template carries a third
# copy of the mark cluster near x = +96 mm, outside the 100 mm wafer.
WAFER_TEMPLATE = os.path.join(REPO, "maskgen", "wafer", "data", "new_wafer_actually.GDS")   # the one shared copy
TEMPLATE_KEEP_RADIUS_MM = 50.0

# =============================================================================
# LAYER MAPS -- the FABRICATED numbering
# =============================================================================
# These are the numbers on the final wafer, NOT config.py's LAYER_MAP: the wafer's device
# layers were renumbered by hand (1 -> 3, 3 -> 7) to sit on the matching alignment-mark
# layers. Derived by matching per-layer shape counts and then XOR = 0 against the wafer.
DXF_LAYER_MAP = {                 # mea1k_interconnects DXF layer (lower-cased) -> GDS
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
