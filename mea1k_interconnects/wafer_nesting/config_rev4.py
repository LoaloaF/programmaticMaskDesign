"""Config -- the Rev4 wafer: the Rev2 wafer with the Rev4 pad stack, at the SAME placements.

Rev4 = Rev3 (PI-etch circles on layer 3, Metal3 on layer 8) plus, on every design:
  * every etch circle 2.5 um (edge) from the pad border, the corner via inset the same way
  * a round Metal1 landing pad + teardrop on every routing via        -> wafer layer 7
  * 12-block and dummies: the top pad of each column gets a 3.5 um via (5.5 um Metal1 pad) and
    the other pads' vias move 0.5 um right, to hold the 2.5 um Metal1 wire gap  -> layers 5, 7
  * every Metal3 square is pulled 2 um in from each side of Metal2's pad (build_designs metal3_inset)
  * the pad-perimeter stabilization vias leave the PI outline: they get their own mask, wafer layer 1
    (etched first), aligned in the template's free mark slot; the PI outline stays on layer 3 (etched
    after) -- named layers in the .oas / .lyp, see LAYER_NAMES
Layers 1, 3, 5, 7 and 8 therefore change against Rev2; Metal2 (layer 6) must not.

The vias moving 2.25 um inward in the pad moves the lane bundle, and with it the band stage 02 reads.
Stage 02 therefore builds the fan, the layer-split vias, Metal2, the connector pads and the polyimide
neck around Rev3's band (lib.constants BAND_REF_DXF, set by build_designs) and only jogs each wire's
real band end onto it (BAND_JOG_LEN, right below the band). So below the jog the connector side is
Rev3's / Rev2's, point for point: layers 3 and 5 change only inside the Metal2 pads, Metal2 not at
all; Metal1 (7) changes in the pads, along the lanes and in the jog.

The dummies (only) also get a small polyimide handle -- a 300 um semicircular tab with blended roots on
the top edge (config_dummy handle_*), the dummies' counterpart of the connector designs' neck-wall
bulbs. It changes only the dummies' etch ring (layer 3), inside the existing dummy exemption below.

Every piece keeps its Rev2 position, so this builds from Rev2's placements -- nothing is nested.
Those placements were measured off the fabricated Rev2 wafer ONCE (legacy/rev2_repro, 01c) and are
committed as data/wafer4_rev2_placements.json; this config needs nothing from legacy/.

    cd mea1k_interconnects
    python3 build_designs.py --rev 4                    # -> <out root>/interconnect/rev4/
    KL=/Applications/KLayout/klayout.app/Contents/MacOS/klayout
    $KL -b -r wafer_nesting/01d_rebuild_wafer.py         # (config_rev4 is 01d's default)
                                                        # -> <out root>/wafer/wafer4_rev4/

Pure standard library: KLayout's Python runs this.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))     # .../mea1k_interconnects/wafer_nesting
ROOT = os.path.dirname(HERE)                          # .../mea1k_interconnects
sys.path.insert(0, os.path.dirname(ROOT))             # the repo root: the maskgen package
from maskgen.core import paths as _paths              # noqa: E402

REV4_DIR = _paths.interconnect_dir(4, create=False)   # build_designs.py --rev 4

RUN_TAG = "wafer4_rev4"
OUT_DIR = _paths.wafer_dir(RUN_TAG, create=False)
WAFER_OUT = os.path.join(OUT_DIR, "wafer_%s.gds" % RUN_TAG)

# Rev2's placements, measured off the fabricated wafer by legacy/rev2_repro/01c and committed. "<ROOT>"
# in its paths stands for the repo root (01d substitutes it). It also names the wafer 01d checks
# against: the fabricated Rev2 wafer.
PLACEMENTS_PATH = os.path.join(HERE, "data", "wafer4_rev2_placements.json")
WAFER_TEMPLATE = os.path.join(HERE, "new_wafer_actually.GDS")
TEMPLATE_KEEP_RADIUS_MM = 50.0

METAL3 = (8, 0)

# The FABRICATED layer numbers (Rev2's maps, see legacy/rev2_repro/config_rev2.py) plus Metal3.
# The etch circles need no entry: they are on Polyimide_Negative (DXF) / layer 1 (dummy GDS),
# which map to 3 -- except the stabilization vias, split off below.
DXF_LAYER_MAP = {                 # mea1k_interconnects DXF layer (lower-cased) -> GDS
    "metal1":             (7, 0),
    "polyimide_negative": (3, 0),
    "etchingpad":         (3, 0),
    "etching":            (5, 0),
    "via":                (5, 0),
    "metal2":             (6, 0),
    "metal3":             METAL3,
}                                 # dropped: polyimide (positive), 0, defpoints
DUMMY_LAYER_MAP = {               # 02_make_dummy.py GDS layer -> wafer GDS
    (1, 0): (3, 0),
    (3, 0): (7, 0),
    (5, 0): (5, 0),
    (6, 0): (6, 0),
    (8, 0): METAL3,
}

# Rev4 splits the pad-perimeter STABILIZATION VIAS off the PI outline: they are etched first, in their
# own mask (wafer layer 1), then the PI outline / connector-pad openings follow (layer 3). In the DXFs
# the circles sit on their own layer PI_Stab_Via; in the dummy GDS (02_make_dummy.py) on GDS layer 2.
STAB_VIA = (1, 0)
DXF_LAYER_MAP["pi_stab_via"] = STAB_VIA
DUMMY_LAYER_MAP[(2, 0)] = STAB_VIA

# TOP: Rev2's template layers (from placements.json) plus these, wafer layer -> template layer.
# Template layer 8 holds the small alignment mark in the third slot of the mark row (between
# Etching's and PI's inverted marks) and the coarse marks every layer has; Rev2 dropped it.
# Layer 1/0 is the template's one free mark slot (|x| = 43.3 mm, second from the outside): the stabilization-
# via mask is aligned there. Its slot ships the POSITIVE vernier, but this is a dark-field etch mask like
# 3/0 and 5/0, so MARK_INVERT_LAYERS swaps in the inverted vernier (exact_io.invert_marks, the electrode
# wafer's procedure; the transform is self-tested on 3/0 whose inverted mark the template already has).
EXTRA_TOP_LAYERS = {"8/0": list(METAL3), "1/0": list(STAB_VIA)}
MARK_INVERT_LAYERS = [STAB_VIA]
MARK_STENCIL = (5, 0)            # an inverted vernier
MARK_COMPANION = (7, 0)          # Metal1: the datum the copy is registered on
MARK_SELFTEST_LAYER = (3, 0)     # already inverted in the template -- the transform is proven on it

# Layer names, written into wafer_<tag>.oas (OASIS LAYERNAME) and wafer_<tag>.lyp (GDS2 has no such record).
LAYER_NAMES = {
    1: "PI_Stab_Via_Etch",       # stabilization vias around every pad -- etched FIRST
    3: "PI_Outline_Etch",        # PI outline, connector-pad openings, dummy ring/handle -- etched after
    5: "Etching_Via",            # routing vias + Metal2 release etching
    6: "Metal2",
    7: "Metal1",
    8: "Metal3",
    10: "WaferOutline",
}

# Look in KLayout (the .lyp written next to the GDS / OAS -- OASIS itself stores no colours).
#   metals  -> reds / orange / purple     etch masks -> blues / greens
#   small features (vias) solid; big areas (pads, PI outline) hatched so what lies beneath shows through.
# LAYER_ORDER is the layer panel top -> bottom. KLayout draws the entries further DOWN the list in front, so
# Metal1 (first) is the back of the stack: solid with a heavier frame so it stands out, and everything
# else -- hatched pads, vias, PI -- is drawn over it instead of being hidden by the wires.
LAYER_ORDER = [7, 10, 6, 8, 3, 5, 1]
LAYER_STYLE = {
    1:  dict(color="#00c060", pattern="I0",  width=1),   # PI_Stab_Via_Etch  -- 5 um circles: solid green
    5:  dict(color="#1060e0", pattern="I0",  width=1),   # Etching_Via       -- vias: solid blue
    3:  dict(color="#00b8c8", pattern="I5",  width=1),   # PI_Outline_Etch   -- rings / openings: teal hatch
    8:  dict(color="#8020d0", pattern="I9",  width=2),   # Metal3            -- purple hatch, heavier frame
    6:  dict(color="#ff9020", pattern="I5",  width=1),   # Metal2            -- orange hatch
    10: dict(color="#808080", pattern="I1",  width=2),   # WaferOutline      -- grey, hollow
    7:  dict(color="#e01818", pattern="I0",  width=2),   # Metal1            -- red, solid, heavy frame
}

_REV4 = {
    "CONN_BIG_NEW": "new_interconnect_with_connector_chamfered_12Block_56_15_rev4.dxf",
    "CONN_SMALL_51_35_NEW": "new_interconnect_with_connector_8blocks_chamfered_1layer_51_35_rev4.dxf",
    "CONN_SMALL_71_15_NEW": "new_interconnect_with_connector_8blocks_chamfered_1layer_71_15_rev4.dxf",
    "dummy_61_10_NEW": "dummy_new_interconnect_circular_dummy_61_10_padrows_rev4.gds",
    "dummy_35_36_NEW": "dummy_new_interconnect_circular_dummy_35_36_padrows_rev4.gds",
}
SOURCES = {cell: {"file": os.path.join(REV4_DIR, f),
                  "layers": DUMMY_LAYER_MAP if f.endswith(".gds") else DXF_LAYER_MAP}
           for cell, f in _REV4.items()}

# What 01d may find changed against Rev2, and where each change must lie (per device cell).
EXPECTED_CHANGED_LAYERS = [STAB_VIA, (3, 0), (5, 0), (7, 0), METAL3]
CHANGES_WITHIN = {STAB_VIA: (6, 0), (3, 0): (6, 0), (5, 0): (6, 0), METAL3: (6, 0)}   # inside the Metal2 pads; Metal1 is free

# The dummies build their own PI ring and etch circles around their Metal1 bars (02_make_dummy.py),
# and the bars have to follow the wires, which moved 2.0-2.25 um with the vias: on the dummies layer 3
# therefore also changes outside the pads (2.25 um wide strips), by design.
CHANGES_WITHIN_SKIP = {(3, 0): ["dummy_35_36_NEW", "dummy_61_10_NEW"]}

XOR_TOL_UM2 = 1e-6               # um^2: floating-point noise in area sums only

# legacy/rev2_repro/01e_overlay_wafers.py: Rev2 on datatype 0, Rev4 on 1, their XOR on 2, cells suffixed by tag.
OVERLAY_TAGS = ("REV2", "REV4")
