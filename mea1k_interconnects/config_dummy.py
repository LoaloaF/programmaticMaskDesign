"""Dummy device: the 12-block pad grid with larger pads, its 768 wire ends shorted by a cap.

A test structure for the flex etch / release process. Built in two steps:
    01_generate.py --config dummy   pad grid + per-column routing -> DXF
    02_make_dummy.py                        caps the wire band, maps layers -> GDS
Units: um. Defaults = the 61_10 variant on the fabricated wafer. The 35_36 variant has smaller
pads at the same pitch (pad grid + via_offset_y) -- see HANDOVER §4.4.
"""

DEFAULT_IN = "new_interconnect_circular_dummy_61_10.dxf"           # stage-01 output, cap input
DEFAULT_OUT = "dummy_new_interconnect_circular_dummy_61_10_padrows.gds"

TOTAL_WIRES = 768             # wires the pad grid must emit (same rule as the 12-block)


# =============================================================================
# 1. Pad grid -- stage 01 (01_generate.py --config dummy)
# =============================================================================
# Same generator as the 12-block; differs in pad size, gap, via position and the jigsaw.

GEN_CONFIG = {
    "pad_side": 63.0,        # metal pad side, Metal2 (um) -- the BIGGER square; pitch = pad_side + gap
    "gap":      8.0,         # edge-to-edge gap between the metal pads (um)
    "n_rows":   15,          # pads per column
    "n_cols":   53,          # columns
    "origin_x": 0.0,         # bottom-left pad corner (um)
    "origin_y": 0.0,
    "pad_side_extra": 61.0,  # etch opening side, EtchingPad (um) -- the SMALLER square, centred on the
                             # metal pad; rim = (pad_side - pad_side_extra)/2 per side. "square" only
    "pad_etch": "vias",      # final PI etch: "square" = one pad_side_extra square on EtchingPad (Rev2);
                             # "vias" = via-sized circles around the pad perimeter on Polyimide_Negative
                             # (Rev3), spacing from etch_via_spacing in lib/constants.py
    "metal3":   True,        # also draw every pad square on Metal3: Metal2's pads, no wires (Rev3)
    "emit_b":   False,       # also draw part B (A rotated 180 deg)
}

GEN_ROUTING = {
    "wire_width":   2.0,     # Metal1 trace width (um)
    "wire_gap":     2.5,     # edge-to-edge gap between lanes (um)
    "via_left_jog": 6.0,     # lane sits this far right of its via (um)
    "min_wire_via_margin": 2.5,  # gap the vertical keeps from its via before the 45 deg jog (um)
    "via_pitch":    0,       # horizontal staircase step between vias (um)
    "via_radius":   2.5,     # via radius (um)
    "via_offset_x": 2.75,    # top via centre x from the pad's left edge (um)
    "via_offset_y": 28.75,   # via centre above the pad centre (um)
    "top_margin":   100.0,   # wire start above the highest pad centre (um)
}

GEN_POLYIMIDE = {
    "margin":      100.0,    # outline margin above the geometry (top edge only) (um)
    "seam_margin": 20.0,     # outline extent past the seam-row pads (um)
    "step_margin": 20.0,     # gap from the seam-row corner pad to the step wall (um)
    "left_margin": 1005.0,   # extra room left of the pads for the (larger) jigsaw socket, mirrored right (um)
    "bulb_r":      280.0,    # jigsaw bulb radius (um)
    "knob_gap":    160.0,    # leftmost pads to the socket's right edge (um)
    "puzzle_gap":  5.0,      # knob <-> socket clearance (um)
    "band_width":  200.0,    # Polyimide_Negative ring width (um)
    "center_gap":  400.0,    # min gap between A's and B's rings, at the knob tip (um)
}


# =============================================================================
# 2. Cap -- stage 02 (02_make_dummy.py)
# =============================================================================
# Every band wire is extended up by band_overlap into N Metal1 bars, which are bridged into
# one continuous bar (all 768 wires shorted, as on the wafer). Above each bar sits a circle on
# Metal1 + etch, connected to it. A polyimide ring on the etch layer frames the device; its
# bottom edge follows the pad rows of the stage-01 outline (jigsaw bulbs dropped).

DUMMY = {
    "band_overlap":    1200.0,  # each band wire extended up by this (um)
    "n_pads":          4,       # bars / circles, evenly spaced in x
    "pad_gap":         8.75,    # requested gap between bars (um); clamped so no wire is uncovered
    "bridge_bars":     True,    # fill the gaps between bars -> one continuous bar (wafer version)
    "pad_rect_height": 25.0,    # bar height (um)
    "pad_rect_y":      1200.0,  # bar centre above the band (um)
    "circle_radius_metal":   430.0,  # Metal1 circle radius (um)
    "circle_radius_etching": 420.0,  # etch window radius inside it (um)
    "circle_y":        2100.0,  # circle centre above the band (um)
    "circle_wire_overlap": 100.0,  # how far the connection enters the circle (um)
    "circle_trace_width": None, # one trace of this width bar -> circle; None -> every wire under it
    "polyimide_outline": True,  # polyimide ring on the etch layer
    "polyimide_width": 200.0,   # ring width (um)
    "polyimide_margin": 200.0,  # clearance to the ring, left/right/top (um)
    "polyimide_follow_pads": True,  # bottom edge follows the pad rows; False -> plain rectangle
    # Handle (Rev4): one semicircular polyimide tab on the TOP edge, to lift the flex off the wafer with
    # tweezers -- the same blended bulb the connector designs carry on their neck walls (their r = 560 um),
    # but small, because dummies sit as little as ~420 um (ring to ring) below the next device. The etch
    # ring follows it. 0 = no handle (Rev2/Rev3).
    "handle_radius":   0.0,     # tab radius = how far it sticks out of the outline's top edge (um)
    "handle_fillet":   150.0,   # concave fillet blending each root into the edge (um); 0 -> sharp
    "handle_x_frac":   0.62,    # tab centre along the top edge, 0 = left end, 1 = right end
    # Pair (wafer6): the capped device plus a copy rotated 180 deg, its pad grid continuing this one's
    # across the stepped bottom edge (the generator's part B), as ONE device: one polyimide outline
    # (a rectangle around both + both handles) and one ring. False = a single device.
    "pair_180":        False,
    "orient_chamfer":  0.0,     # pair only: cut the outline's top-left corner by this at 45 deg (um), so the
                                # pair shows which side faces up (it is otherwise mirror-symmetric); 0 = none
    "pair_gap_y":      0.0,     # the copy this much lower than the exact fit (um), so the two grids stay distinct
}

import lib                                     # puts the repo root (maskgen) on sys.path
from maskgen.core import layers as _layers

# DXF layer -> GDS (layer, datatype) written by 02_make_dummy.py. Only LWPOLYLINEs are read, so
# Polyimide_Negative contributes the Rev3 etch circles but not its band (a HATCH); Polyimide is
# dropped. The fabricated wafer renumbers these by hand (1 -> 3, 3 -> 7) -- see HANDOVER §1.1.
DUMMY_LAYER_MAP = dict(_layers.IC_DUMMY_GDS)   # maskgen/core/layers.py: EtchingPad + Polyimide_Negative
                                              # -> 1/0, PI_Stab_Via -> 2/0 (Rev4), Etching 5/0,
                                              # Metal1 3/0, Metal2 6/0, Metal3 8/0
DUMMY_METAL_LAYER = _layers.IC_DUMMY_METAL    # (3, 0): bars, circles, extensions (= Metal1)
DUMMY_ETCH_LAYER = _layers.IC_DUMMY_ETCH      # (1, 0): circle etch windows + polyimide ring


# =============================================================================
# 3. Derived -- do not edit
# =============================================================================
from lib.constants import GEN_CONFIG_FIXED, GEN_ROUTING_FIXED, GEN_POLYIMIDE_FIXED

GEN_CONFIG = {**GEN_CONFIG_FIXED, **GEN_CONFIG}
GEN_ROUTING = {**GEN_ROUTING_FIXED, **GEN_ROUTING}
GEN_POLYIMIDE = {**GEN_POLYIMIDE_FIXED, **GEN_POLYIMIDE}
