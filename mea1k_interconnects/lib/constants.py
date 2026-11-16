"""Internal constants -- the same for every design, and not design decisions.

Layer names, sampling resolutions and numerical tolerances. Changing a layer name here renames
that layer in every output. Everything that shapes the physical design lives in
config_<design>.py instead.

The *_FIXED dicts hold the stage-01 generator entries of the same kind. Each config merges them
into its GEN_CONFIG / GEN_ROUTING / GEN_POLYIMIDE (its "Derived" section), so the generators see one dict.
"""

# ---- Layers (the interconnect's own 2-metal + via stack) --------------------------
# The names themselves are defined ONCE, in maskgen/core/layers.py (IC), next to the wafer layer each
# one ends up on; these are the roles the routers use them in.
from maskgen.core import layers as _layers      # (a module: invisible to the config snapshots)

WIRE_LAYER = _layers.IC.METAL1          # even wires stay here; wire ends arrive here
LIFT_LAYER = _layers.IC.METAL2          # odd wires lifted here (12-block only)
VIA_LAYER = _layers.IC.VIA              # via cuts
CONN_PAD_LAYER = _layers.IC.POLYIMIDE_NEG   # marker layer for the connector contact pads
POLYIMIDE_LAYER = _layers.IC.POLYIMIDE
POLYIMIDE_NEG_LAYER = _layers.IC.POLYIMIDE_NEG

N_WIRES_PER_BLOCK = 64      # Molex 227044: 64 signal contacts per connector block

# ---- Sampling and numerics --------------------------------------------------------
CIRCLE_RES = 48             # vertices per landing-pad / via circle
TEARDROP_RES = 64           # segments per full turn on the tear's arcs (fillet arcs use the same chord)
TEARDROP_CLAMP_ITERS = 12   # bisection steps when shrinking a tear to fit (~0.03 um resolution)
FAN_RES = 48                # sample points per wire along each eased fan
FAN_CHECK_TOL_UM = 0.02     # tolerance of the fan min-gap check (a min-length fan sits exactly on its target)
POLY_FAN_TAPER_RES = 48     # vertices per tapering polyimide neck wall (12-block flare)

# ---- Stage-01 generator entries that are not design knobs ----------------------------
GEN_CONFIG_FIXED = {
    "layer": _layers.IC.METAL2,                    # pad squares
    "pad_layers_extra": [_layers.IC.ETCHING_PAD],  # pad_etch="square": the PI etch square is drawn here
    "layer_top": _layers.IC.METAL3,                # pad squares again, when metal3 is on (Rev3)
    "out_path": None,                     # set from --out by the generator's CLI
    # Defaults for the pad-stack knobs = Rev2; every config sets its own (see config_<design>.py)
    "pad_etch": "square",
    "metal3": False,
    # Metal3 squares are pulled in this far from each side of Metal2's pad (um). Rev4: 2.
    "metal3_inset": 0.0,
    # DXF layer for the "vias" etch circles (the pad-perimeter stabilization vias). None = on
    # Polyimide_Negative with the PI outline (Rev3); Rev4 gives them their own layer = their own mask.
    "etch_via_layer": None,
    # "vias" etch: the spacing range the vias between two corner neighbours are counted from (um).
    # One range for all designs, so pads of different sizes get similar spacing. 21, not 20: the
    # 63 um dummy pad (61_10) gets 20.81 with one via between its neighbours, 13.87 with two.
    "etch_via_spacing": (15.0, 21.0),
    # "vias" etch: min edge-to-edge gap between the two neighbours of a corner, and between them
    # and the routing via (um). Sets how far from the corner point the neighbours sit.
    "etch_via_corner_gap": 3.0,
    # "vias" etch: how much further in from the pad edge the etch vias sit than the routing via
    # (via_offset_x = 2.75 -> centres 3.75 um in, 1.25 um metal between circle and pad edge).
    "etch_via_inset_extra": 1.0,
}
GEN_ROUTING_FIXED = {
    "via_shape": "circle",   # "circle" (via_radius) or "rect" (via_w x via_h)
    "via_arc": 48,           # segments per circular via
    "via_w": 5.5,            # rect vias only (um)
    "via_h": 43.0,           # rect vias only (um)
    "layer_wire": _layers.IC.METAL1,
    "layer_via": _layers.IC.ETCHING,
    # Rev4: each Metal1 wire ends in a round landing pad centred on its routing via, blended into the
    # wire by a tangent-fillet teardrop. Off = Rev2/Rev3 (the wire stops at the via centre).
    "m1_pad": False,
    "m1_pad_enc": 1.0,        # pad radius = via_radius + this (um): 2.5 + 1 -> 7 um diameter
    # Rev4, 12-block generator only (01_generate.py --config 12block; the 8-block ignores them). The top pad of a
    # column sits between two lanes only 12.5 um apart (centres), too tight for a full via + 1 um
    # Metal1 margin: its via gets radius via_k0_radius, moved via_k0_dx in x. The lower pads' vias move
    # via_rest_dx in x, clear of the previous column's last lane. None / 0 = unchanged.
    "via_k0_radius": None,
    "via_k0_dx": 0.0,
    "via_rest_dx": 0.0,
    # wafer6: two more top-via knobs, read with .get() so they are absent from every older revision:
    #   via_k0_where  "all" (Rev4: every column's top via is the small one) | "needed": the full via at
    #                 m1_margin wherever its column's lanes can shift right to pass it (01_generate.py);
    #                 the small via elsewhere (12-block: the 15-lane columns)
    #   via_k0_h      total height (um) of the small via: a vertical capsule via_k0_radius*2 wide; its
    #                 Metal1 pad keeps m1_pad_enc all round. None = a circle (Rev4)
    #   m1_margin     um from every Metal1 landing pad to the Metal2 pad's left/top edge (sets via_offset_y,
    #                 via_rest_dx, via_k0_dx); needed by via_k0_where="needed". None = Rev4's numbers
    #   top_lane_uniform  the top pad's lane one wire pitch left of the next lane, jogging into its via
    #                 (True), instead of straight down onto it (Rev4)
    "m1_tear_fillet_r": 14.0, # teardrop fillet radius (um); larger = longer, gentler blend. Capped
                              # per wire so the tip stays on the last straight run
}
GEN_POLYIMIDE_FIXED = {
    "layer": _layers.IC.POLYIMIDE,
    "color": 7,
    "neg_layer": _layers.IC.POLYIMIDE_NEG,
    "neg_color": 5,
    "n_arc": 48,             # segments per jigsaw bulb
    # Rev4: the outline is sized to the wire bbox, whose far lane moved 2.25 um outward with the vias.
    # Shrinking the right extent by this (B's mirrored left extent follows) keeps the outline where
    # Rev3 had it. 0 = unchanged.
    "trim_xmax": 0.0,
}

# Stage 02, Rev4+: keep the connector side where an earlier revision had it. A revision that moves the
# vias (and with them the lanes) moves the stage-01 band; stage 02 builds the fan, the layer-split
# vias, Metal2, the connector pads and the polyimide neck around that band, so they would all move
# relative to the pad grid. BAND_REF_DXF = the stage-01 DXF of the revision whose band to keep
# (set by build_designs). Stage 02 then builds EVERYTHING from the reference band and only joins
# each wire's real band end to its reference position with a short smootherstep jog of BAND_JOG_LEN um
# right below the band -- from there on the Metal1 path is the reference revision's, point for point.
BAND_REF_DXF = None
BAND_JOG_LEN = 300.0
