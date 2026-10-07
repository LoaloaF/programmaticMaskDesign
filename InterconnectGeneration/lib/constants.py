"""Internal constants -- the same for every design, and not design decisions.

Layer names, sampling resolutions and numerical tolerances. Changing a layer name here renames
that layer in every output. Everything that shapes the physical design lives in
config_<design>.py instead.

The *_FIXED dicts hold the stage-01 generator entries of the same kind. Each config merges them
into its GEN_CONFIG / GEN_ROUTING / GEN_POLYIMIDE (its "Derived" section), so the generators see one dict.
"""

# ---- Layers (the interconnect's own 2-metal + via stack) --------------------------
WIRE_LAYER = "Metal1"       # even wires stay here; wire ends arrive here
LIFT_LAYER = "Metal2"       # odd wires lifted here (12-block only)
VIA_LAYER = "Via"           # via cuts
CONN_PAD_LAYER = "Polyimide_Negative"   # marker layer for the connector contact pads
POLYIMIDE_LAYER = "Polyimide"
POLYIMIDE_NEG_LAYER = "Polyimide_Negative"

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
    "layer": "Metal2",                    # pad squares
    "pad_layers_extra": ["EtchingPad"],   # pad_etch="square": the PI etch square is drawn here
    "layer_top": "Metal3",                # pad squares again, when metal3 is on (Rev3)
    "out_path": None,                     # set from --out by the generator's CLI
    # Defaults for the pad-stack knobs = Rev2; every config sets its own (see config_<design>.py)
    "pad_etch": "square",
    "metal3": False,
    # "vias" etch: the spacing range every design's per-side count is chosen from (um). One range
    # for all designs, so pads of different sizes get similar spacing.
    "etch_via_spacing": (15.0, 20.0),
}
GEN_ROUTING_FIXED = {
    "via_shape": "circle",   # "circle" (via_radius) or "rect" (via_w x via_h)
    "via_arc": 48,           # segments per circular via
    "via_w": 5.5,            # rect vias only (um)
    "via_h": 43.0,           # rect vias only (um)
    "layer_wire": "Metal1",
    "layer_via": "Etching",
}
GEN_POLYIMIDE_FIXED = {
    "layer": "Polyimide",
    "color": 7,
    "neg_layer": "Polyimide_Negative",
    "neg_color": 5,
    "n_arc": 48,             # segments per jigsaw bulb
}
