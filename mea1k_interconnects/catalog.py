"""EVERY interconnect design and EVERY wafer revision, in one file.

    DESIGNS     the five designs on the MEA1K interconnect wafer: which config_<base>.py each one
                starts from, which stage scripts build it, its file names, and the few settings in
                which it differs from its base config
    REVISIONS   the wafer revisions. Each names its PARENT and lists only what it CHANGES; a
                revision is its parent's settings plus its own (Rev4 = Rev3 + the Rev4 list)

How a (design, revision) is resolved -- resolve.py, in this order, every step overriding the one before:

    1. config_<base>.py               the base config, imported fresh (its derived values -- PITCH,
                                      LAND_PAD_R, FAN2_G ... -- are computed HERE, at import, and
                                      are NOT recomputed after the overrides below: that is what the
                                      fabricated designs were built with)
    2. DESIGNS[d].set                 the design's own differences from its base
    3. REVISIONS root -> rev: .set    every revision in the chain, oldest first
    4.                    .per_design[d]
    5.                    .dummy_set  (dummy designs only)
    6. the newest via_edge_gap        moves every via so its EDGE is that far from the pad edge
    7. stage 02 only: band_ref_rev    builds around that revision's band (BAND_REF_DXF)

    python3 -m maskgen show --rev 4 --design 12block      every resolved setting + where it came from

Dict-valued settings (GEN_CONFIG, GEN_ROUTING, GEN_POLYIMIDE, DUMMY) are updated key by key; any other
setting is replaced.
"""
from collections import namedtuple

import lib                                   # noqa: F401  -- puts the repo root (maskgen) on sys.path
from maskgen.core import layers

# =============================================================================
# DESIGNS
# =============================================================================
# key -> base config, stage scripts, file stems, own settings.
#   mid    stage-01 output (pad grid DXF), without revision suffix or extension
#   final  stage-02 output (routed DXF / capped dummy GDS), without revision suffix
Design = namedtuple("Design", "key base gen route mid final set note")

DESIGNS = {
    "12block": Design(
        "12block", "12block", "01_generate_12block.py", "02_route_12block.py",
        "new_interconnect_circular_12Block_56_15",
        "new_interconnect_with_connector_chamfered_12Block_56_15.dxf",
        {}, "768 wires, two metal layers, pad 58 um (56 + 15 um gap)"),
    "8block_51_35": Design(
        "8block_51_35", "8block", "01_generate_8block.py", "02_route_8block.py",
        "new_interconnect_circular_8block_51_35",
        "new_interconnect_with_connector_8blocks_chamfered_1layer_51_35.dxf",
        {}, "512 wires, one metal layer, pad 53 um"),
    "8block_71_15": Design(
        "8block_71_15", "8block", "01_generate_8block.py", "02_route_8block.py",
        "new_interconnect_circular_8block_71_15",
        "new_interconnect_with_connector_8blocks_chamfered_1layer_71_15.dxf",
        {"GEN_CONFIG": {"pad_side": 73.0, "pad_side_extra": 71.0, "gap": 13.0},
         "GEN_ROUTING": {"via_offset_y": 33.75},
         "N_RIGHT_BULBS": 1},
        "8-block with 73 um pads, 13 um gap; one bulb on the right neck wall"),
    "dummy_61_10": Design(
        "dummy_61_10", "dummy", "01_generate_12block.py", "02_make_dummy.py",
        "new_interconnect_circular_dummy_61_10",
        "dummy_new_interconnect_circular_dummy_61_10_padrows.gds",
        {}, "12-block pad grid, wire band shorted by a cap (etch/release test), GDS"),
    "dummy_35_36": Design(
        "dummy_35_36", "dummy", "01_generate_12block.py", "02_make_dummy.py",
        "new_interconnect_circular_dummy_35_36",
        "dummy_new_interconnect_circular_dummy_35_36_padrows.gds",
        {"GEN_CONFIG": {"pad_side": 37.0, "pad_side_extra": 35.0, "gap": 34.0},
         "GEN_ROUTING": {"via_offset_y": 15.75}},
        "dummy with 37 um pads, 34 um gap"),
}


# =============================================================================
# REVISIONS
# =============================================================================
#   parent        the revision this one starts from (None: the base configs)
#   suffix        appended to every file name ("" for Rev2: the fabricated names)
#   layerset      the wafer layers (maskgen/core/layers.py LAYERSETS)
#   set           applied to every design
#   per_design    {design: settings}, applied after `set`
#   dummy_set     applied to the dummy designs only
#   via_edge_gap  um from the pad edge to every via's EDGE (None: vias where the config puts them)
#   band_ref_rev  stage 02 routes around THAT revision's wire band (its stage-01 DXF is regenerated
#                 into <out>/ref_rev<N>/), so the connector side does not move. NOT inherited.
#   status, changes   for people: what this revision is, and what it changes, one line each
Rev = namedtuple("Rev", "n parent suffix layerset set per_design dummy_set via_edge_gap band_ref_rev "
                        "status changes")

REVISIONS = {
    2: Rev(2, None, "", "interconnect_rev2",
           set={"GEN_CONFIG": {"pad_etch": "square", "metal3": False}},
           per_design={}, dummy_set={}, via_edge_gap=None, band_ref_rev=None,
           status="FABRICATED (MEA1K_wafer4_12x_8x_8x_breakoutboards_Rev2.gds)",
           changes=["the PI etch is one square per pad (EtchingPad), no Metal3"]),

    3: Rev(3, 2, "_rev3", "interconnect_rev3",
           set={"GEN_CONFIG": {"pad_etch": "vias", "metal3": True}},
           per_design={}, dummy_set={}, via_edge_gap=None, band_ref_rev=None,
           status="not fabricated",
           changes=["the PI etch becomes 5 um etch vias around the pad perimeter (none in the corners)",
                    "Metal3: the pad squares again, on a new layer (wafer 8)"]),

    4: Rev(4, 3, "_rev4", "interconnect_rev4",
           set={"GEN_CONFIG": {"etch_via_spacing": (9.5, 21.0),
                               "etch_via_layer": layers.IC.PI_STAB_VIA,
                               "metal3_inset": 2.0},
                "GEN_ROUTING": {"m1_pad": True},
                "GEN_POLYIMIDE": {"trim_xmax": 2.25}},
           per_design={d: {"GEN_ROUTING": {"via_k0_radius": 1.75, "via_k0_dx": -0.25, "via_rest_dx": 0.5}}
                       for d in ("12block", "dummy_61_10", "dummy_35_36")},
           dummy_set={"DUMMY": {"handle_radius": 300.0, "handle_fillet": 150.0, "handle_x_frac": 0.62}},
           via_edge_gap=2.5, band_ref_rev=3,
           status="current",
           changes=[
               "every via -- routing via and etch circles -- with its EDGE 2.5 um from the pad edge "
               "(centre 5 um in); the routing via stays in the top-left corner (via_edge_gap)",
               "etch-via spacing range (9.5, 21) um: the 5 um inset leaves less room between corner "
               "neighbours (51_35 gets 13.5 um, dummy 35_36 11 um)",
               "a round 7 um Metal1 landing pad on every routing via, blended in by a teardrop (m1_pad)",
               "12-block + dummies: the top pad of each column gets a 3.5 um via on a 5.5 um Metal1 pad, "
               "0.25 um left; the lower pads' vias 0.5 um right -- holds the 2.5 um Metal1 wire gap "
               "between the two lanes beside a column's top pad (per_design)",
               "the pad-perimeter stabilization vias move to their own DXF layer PI_Stab_Via = their own "
               "mask, wafer layer 1, etched BEFORE the PI outline (etch_via_layer)",
               "every Metal3 square 2 um in from each side of Metal2's pad (metal3_inset)",
               "the polyimide outline stays where Rev3 had it (trim_xmax 2.25 um)",
               "dummies: a 300 um polyimide handle on the top edge, 150 um fillets, at 62% (dummy_set)",
               "stage 02 routes around Rev3's band and only jogs each wire onto it, so Metal2, the vias "
               "and the connector side stay identical to Rev3 / Rev2 (band_ref_rev)",
           ]),
}


def chain(rev):
    """The revisions `rev` is built from, oldest first, ending with `rev` itself."""
    out = []
    r = REVISIONS[rev]
    while r is not None:
        out.insert(0, r)
        r = REVISIONS[r.parent] if r.parent is not None else None
    return out


def file_names(design, rev):
    """(stage-01 DXF, final file) names of `design` at revision `rev` (no folder)."""
    d, s = DESIGNS[design], REVISIONS[rev].suffix
    stem, ext = d.final.rsplit(".", 1)
    return d.mid + s + ".dxf", stem + s + "." + ext
