"""12-block design: 768 wires, two metal layers (odd wires lifted to Metal2 through vias).

Every design knob for this part, in physical order from the chip side to the connector.
Units: stage 01 and Phase 1 in um, Phase 2 (connector side) in mm. Layer names and sampling
resolutions are in lib/constants.py; the routing code is in lib/ and 02_route_12block.py.
"""

DEFAULT_IN = "new_interconnect_circular_12Block_56_15.dxf"     # router input (stage-01 output)
DEFAULT_OUT = "new_interconnect_with_connector_chamfered_12Block_56_15.dxf"
BOARD_OUTLINE = "Board_12x_outline_simon.dxf"                   # in assets/


# =============================================================================
# 1. Pad grid -- stage 01 (01_generate_12block.py)
# =============================================================================

GEN_CONFIG = {
    "pad_side": 58.0,        # metal pad side, Metal2 (um) -- the BIGGER square; pitch = pad_side + gap
    "gap":      13.0,        # edge-to-edge gap between the metal pads (um)
    "n_rows":   15,          # pads per column -- at the fit limit, one more needs a larger pitch
    "n_cols":   53,          # columns. Rows/cols set the wire count, which must equal TOTAL_WIRES
    "origin_x": 0.0,         # bottom-left pad corner (um)
    "origin_y": 0.0,
    "pad_side_extra": 56.0,  # etch opening side, EtchingPad (um) -- the SMALLER square, centred on the
                             # metal pad; rim = (pad_side - pad_side_extra)/2 per side. "square" only
    "pad_etch": "vias",      # final PI etch: "square" = one pad_side_extra square on EtchingPad (Rev2);
                             # "vias" = via-sized circles around the pad perimeter on Polyimide_Negative
                             # (Rev3), spacing from etch_via_spacing in lib/constants.py
    "metal3":   True,        # also draw every pad square on Metal3: Metal2's pads, no wires (Rev3)
    "emit_b":   False,       # also draw part B (A rotated 180 deg); A is identical either way
}

# Per-column Metal1 routing: each pad's wire runs down its own vertical lane and jogs 45 deg
# down-left into a via left of the lane. A column's lanes stay inside its pad pitch, so the
# pattern tiles to any number of columns.
GEN_ROUTING = {
    "wire_width":   2.0,     # Metal1 trace width (um)
    "wire_gap":     2.5,     # edge-to-edge gap between lanes (um)
    "via_left_jog": 6.0,     # lane sits this far right of its via (um)
    "min_wire_via_margin": 2.5,  # gap the vertical keeps from its via before the 45 deg jog (um)
    "via_pitch":    0,       # horizontal staircase step between vias (um); keep <= lane pitch
    "via_radius":   2.5,     # via radius (um)
    "via_offset_x": 2.75,    # top via centre x from the pad's left edge (um)
    "via_offset_y": 26.25,   # via centre above the pad centre (um); pad_side/2 - via_offset_x
                             # puts it in the top-left corner
    "top_margin":   100.0,   # wire start above the highest pad centre (um)
}

# Polyimide outline. Parts A and B meet along a stepped seam and mate by in-plane insertion
# through a bulb-only jigsaw (male half-disc on one, matching female socket on the other).
# Polyimide_Negative is a ring offset outward from each outline.
GEN_POLYIMIDE = {
    "margin":      100.0,    # outline margin above the geometry (top edge only) (um)
    "seam_margin": 20.0,     # outline extent past the seam-row pads (um); None -> gap/2
    "step_margin": 20.0,     # gap from the seam-row corner pad to the step wall (um); None -> gap/2
    "left_margin": 305.0,    # extra room left of the pads for the jigsaw socket, mirrored right (um)
    "bulb_r":      100.0,    # jigsaw bulb radius (um)
    "knob_gap":    50.0,     # leftmost pads to the socket's right edge (um)
    "puzzle_gap":  5.0,      # knob <-> socket clearance (um)
    "band_width":  200.0,    # Polyimide_Negative ring width (um)
    "center_gap":  400.0,    # min gap between A's and B's rings, at the knob tip (um)
}


# =============================================================================
# 2. Connector (Molex 227044)
# =============================================================================

TOTAL_WIRES = 768             # 2 columns x 6 blocks x 64. Must equal the wires stage 01 emits
N_BLOCKS_PER_COLUMN = 6
N_NEW_BLOCKS_PER_COLUMN = 2   # the CSV holds 4 real blocks per column; 2 more are synthesised below

PADR = 0.105                  # connector pad radius (mm); also the routing collision radius
CONN_PAD_R = 0.095            # pad circle on Polyimide_Negative (mm); None -> PADR
CONNECTOR_TW = 0.0035         # connector trace width (mm)
GAP = 0.0005                  # lane gap (mm). Lanes alternate Metal1/Metal2, so the same-layer
                              # gap is 2*(CONNECTOR_TW + GAP) - CONNECTOR_TW = 4.5 um


# =============================================================================
# 3. Band -> via row (Phase 1, um)
# =============================================================================

WIRE_W = 2.0                  # trace width at the band (matches the stage-01 leads)
LAND_PAD_DIA = 6.0            # via landing pad, on Metal1 and Metal2
VIA_DIA = 5.0                 # lift via (layer Via), concentric in the landing pad
FAN_TARGET_PITCH = 7.0        # lane pitch after the fan-out (native ~4.5 um); room for the pads
NECK_LEN = 420.0              # straight run below the band before the fan
FAN_TARGET_GAP = None         # min gap held across the fan; None -> the native gap
FAN_TILT_CAP_DEG = 45.0       # max trace tilt in the fan
FAN_LEN_SLACK = 1.10          # fan length x this, so the sampled polyline (not just the ideal
                              # curve) holds the gap
FAN_LEN = None                # fixed fan length override; None -> shortest that holds the gap
VIA_ROW_GAP = 14600.0         # fan bottom -> via row


# =============================================================================
# 4. Via row -> connector (Phase 2, mm)
# =============================================================================

NECK_LEN_MM = 1.0             # straight run below the via row before the fan-in
FAN2_TARGET_GAP_MM = 0.0025   # min same-layer gap held across the fan-in (2.5 um)
FAN2_TILT_CAP_DEG = 50.0      # max trace tilt in the fan-in; above Phase 1's to keep it short
FAN_LEN_MM = None             # fixed fan-in length override; None -> shortest that holds the gap
WIDEN_LEN_MM = 0.5            # width ramp from the band width to CONNECTOR_TW, below the fan-in

CONN_TOP_BELOW_VIA_MM = 2.299085  # connector top below the via row. Smaller than neck + fan + widen
                                  # (~2.58 mm), so the spine has no straight run left; it still routes.
UNDER_BLOCK_CLEAR_MM = 0.2    # nearest pad row -> top lane of the under-block ladder (both columns)
RIGHT_DIRECT_ROWS34 = True    # right column rows 3&4 drop straight into their corridor (their
                              # corridor sits on the under-block ladder) instead of detouring
                              # round the right edge
CORRIDOR_CLEAR_MM = 0.15      # pad row -> that corridor when RIGHT_DIRECT_ROWS34 is off

CHAMFER_MAX_MM = 0.030        # 45 deg bevel on every 90 deg corner: max set-back per leg
CHAMFER_FRAC = 0.4            # ... and at most this share of the shorter leg (<= 0.5, no self-cross)


# =============================================================================
# 5. Teardrop pads + angled entry
# =============================================================================
# Pad = circle + two concave fillet arcs tangent to the pad and to the trace edges, as on the
# mating electrode bundle (U4C08). Tip distance is derived, not set:
#     L = sqrt((PADR + F)^2 - (w/2 + F)^2),  F = fillet radius, w = PAD_APPROACH_TW_*

TEARDROP = True                     # False -> plain circular pads
TEARDROP_FILLET_R_VERTICAL = 0.275  # mm (U4C08 value) -> tip 246 um
TEARDROP_FILLET_R_ANGLED = 0.10     # mm -> tip 170 um. Smaller because the angled entry drifts
                                    # 135 -> 120 deg across a row, and 0.275 flares far past a 30 um wire
TEARDROP_CLEAR = 0.02               # mm, gap a tear keeps from foreign metal; tears shrink to fit
TEARDROP_CLEAR_FLOOR_FROM_PAD = True  # pads already closer than TEARDROP_CLEAR are held to their
                                      # existing gap instead of losing the tear (L row 2, R row 3)
TEARDROP_PAD_LAYER = False          # also shape the Polyimide_Negative opening

PAD_APPROACH_TW_ANGLED = 0.030      # mm, trace width at the pad -- also the tear's width
PAD_APPROACH_TW_VERTICAL = 0.03
PAD_APPROACH_LEN_ANGLED = None      # ramp length; None -> the whole last straight run.
PAD_APPROACH_LEN_VERTICAL = None    # Not 0: that skips the widening but keeps the wide tear
PAD_APPROACH_HOLD_ANGLED = 0.5      # share of the ramp already at full width (raised per pad
PAD_APPROACH_HOLD_VERTICAL = 0.8    # so the whole tear sits on full-width trace)

# Rows that other traces pass on their way to the far rows enter on one diagonal segment, which
# moves the tear off the neighbours' path. The right blocks are rotated 180 deg, so rows mirror.
ANGLED_ENTRY = True
ANGLED_ENTRY_ROWS = (1, 3)          # left column, rows numbered top-down
ANGLED_ENTRY_ROWS_RIGHT = (2, 4)    # right column
ANGLED_ENTRY_SIDE = "left"          # lean of the diagonal: "left" | "right" | "auto"
ANGLED_ENTRY_SIDE_RIGHT = "right"   # ("auto" flips sign across a block)
ANGLED_ENTRY_LEN = None             # mm sideways; None -> just reaches the tear tip. Each route
                                    # starts at 45 deg and steepens only as needed to clear
ANGLED_ENTRY_MARGIN = 0.005         # mm beyond PADR kept from foreign pads
ANGLED_ENTRY_TRACE_GAP = 0.003      # mm kept from foreign traces (the diagonal crosses other lanes)
ANGLED_ENTRY_CHAMFER = None         # corner bevel on the diagonal only; None -> CHAMFER_MAX_MM


# =============================================================================
# 6. Polyimide + board (Simon's board outline)
# =============================================================================

BOARD_CORNER_R = 0.8          # board bottom-corner fillet (mm)
BOARD_BOTTOM_GAP_MM = 1.5     # lowest block centroid above the board bottom (moves the whole board)
BOARD_BOTTOM_EXTEND_MM = 1.0  # extends only the polyimide bottom edge downward

POLYIMIDE = {
    "fillet_r_mm":  3.0,        # neck -> board flare fillet
    "arc_segments": 16,         # vertices per 90 deg fillet arc
    "graft_overlap_um": 50.0,   # neck top reaches this far into the stage-01 jigsaw
}

# The fanned bundle (768 x 7 um) is wider than the stage-01 jigsaw edge, so the neck flares out
# over the Phase-1 fan to keep the outer wires on substrate.
POLY_FAN_MARGIN_UM = 150.0           # flared wall -> outermost wire edge (um)
POLY_FAN_TAPER_START_Y_UM = -400     # y where the flare starts (um); None -> the graft seam.
                                     # Must stay below the lowest MEA1K bond pad

BAND_W_UM = 200.0             # Polyimide_Negative ring width (um)
BAND_CLOSE_UM = 450.0         # fills concave pockets (jigsaw socket) before the ring is offset;
                              # keep below the board notch width (~2 mm)

# Extraction tabs: semicircular bulbs on the neck walls, to lift the flex off the wafer
N_LEFT_BULBS = 4
N_RIGHT_BULBS = 3
LEFT_BULB_R_UM = 560.0
LEFT_BULB_MARGIN_UM = 1000.0  # first/last bulb distance from the neck transitions
BULB_FILLET_R_UM = 300.0      # fillet blending each bulb into the wall (0 -> sharp)


# =============================================================================
# 7. Derived -- computed from the above, do not edit
# =============================================================================
import config_common
from lib.constants import FAN_RES, GEN_CONFIG_FIXED, GEN_ROUTING_FIXED, GEN_POLYIMIDE_FIXED
from lib.fanmath import eased_fan_profile

GEN_CONFIG = {**GEN_CONFIG_FIXED, **GEN_CONFIG}
GEN_ROUTING = {**GEN_ROUTING_FIXED, **GEN_ROUTING}
GEN_POLYIMIDE = {**GEN_POLYIMIDE_FIXED, **GEN_POLYIMIDE}

config_common.derive(globals())   # LAND_PAD_R, VIA_R, IC_TW, CONNECTOR_PITCH, PITCH, BAND_PITCH_MM,
                                  # WIRES_PER_COLUMN

# Phase-2 fan profile. Lanes alternate Metal1/Metal2, so the same-layer pitch is 2x the lane
# pitch; the fan carries the band width IC_TW.
_FAN2_SL_TIGHT = 2.0 * CONNECTOR_PITCH
_FAN2_SL_WIDE = 2.0 * BAND_PITCH_MM
_FAN2_D = (TOTAL_WIRES - 1) / 2.0 * CONNECTOR_PITCH
FAN2_G, _FAN2_LEN_AUTO = eased_fan_profile(_FAN2_SL_TIGHT, _FAN2_SL_WIDE, _FAN2_D,
                                           FAN2_TARGET_GAP_MM, IC_TW,
                                           FAN2_TILT_CAP_DEG, FAN_RES, FAN_LEN_SLACK)
if FAN_LEN_MM is None:
    FAN_LEN_MM = _FAN2_LEN_AUTO
