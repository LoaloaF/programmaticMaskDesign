"""EVERY interconnect design and EVERY wafer revision, in one file.

    DESIGNS     the five designs on the MEA1K interconnect wafer: which config_<base>.py each one
                starts from, which stage scripts build it, its file names, and the few settings in
                which it differs from its base config
    REVISIONS   per WAFER (maskgen/core/wafers.py: wafer4, wafer6, ...), its revisions. Each names its
                PARENT and lists only what it CHANGES; a revision is its parent's settings plus its own
                (wafer4 Rev4 = wafer4 Rev3 + the Rev4 list). A new wafer starts at Rev1 and names the
                revision of another wafer it starts from (wafer6 Rev1 = wafer4 Rev4 + its own list).

How a (design, revision) is resolved -- resolve.py, in this order, every step overriding the one before:

    1. config_<base>.py               the base config, imported fresh (its derived values -- PITCH,
                                      LAND_PAD_R, FAN2_G ... -- are computed HERE, at import, and
                                      are NOT recomputed after the overrides below: that is what the
                                      fabricated designs were built with)
    2. DESIGNS[d].set                 the design's own differences from its base
    3. REVISIONS root -> rev: .set    every revision in the chain, oldest first (across wafers)
    4.                    .per_design[d]
    5.                    .dummy_set  (dummy designs only)
    6. the newest via_edge_gap        moves every via so its EDGE is that far from the pad edge
    7. REDERIVE (a setting)           if set, the derived values of step 1 are recomputed from the
                                      resolved knobs (opt-in: the fabricated designs keep theirs)
    8. stage 02 only: band_ref_rev    builds around that revision's band (BAND_REF_DXF)
                                      ROUTE_SCRIPT (a setting) runs another stage-02 script than the
                                      design's (wafer6's 12-block: 02_route_wafer6.py)

    python3 -m maskgen show --wafer wafer4 --rev 4 --design 12block_56_15   every resolved setting + its source

Dict-valued settings (GEN_CONFIG, GEN_ROUTING, GEN_POLYIMIDE, DUMMY) are updated key by key; any other
setting is replaced.
"""
from collections import namedtuple

import lib                                   # noqa: F401  -- puts the repo root (maskgen) on sys.path
from maskgen.core import layers

# =============================================================================
# DESIGNS
# =============================================================================
# key -> base config, stage scripts, own settings. The key is the DEVICE's name: its folder
# (<root>/<wafer>/Rev<N>/devices/<key>/), its files (<key>.dxf|.gds, <key>_padgrid.dxf) and its wafer cell.
# Named <type>_<etch opening>_<gap between openings> (um): 12block_56_15 = 58 um metal pads, 56 um openings.
Design = namedtuple("Design", "key base gen route set note")

DESIGNS = {
    "12block_56_15": Design(
        "12block_56_15", "12block", "01_generate.py", "02_route.py",
        {}, "768 wires, two metal layers, pad 58 um (56 + 15 um gap)"),
    "8block_51_35": Design(
        "8block_51_35", "8block", "01_generate.py", "02_route.py",
        {}, "512 wires, one metal layer, pad 53 um"),
    "8block_71_15": Design(
        "8block_71_15", "8block", "01_generate.py", "02_route.py",
        {"GEN_CONFIG": {"pad_side": 73.0, "pad_side_extra": 71.0, "gap": 13.0},
         "GEN_ROUTING": {"via_offset_y": 33.75},
         "N_RIGHT_BULBS": 1},
        "8-block with 73 um pads, 13 um gap; one bulb on the right neck wall"),
    "8x64_56_15": Design(
        "8x64_56_15", "12block", "01_generate.py", "02_route.py",
        {}, "the 12-block's chip side and cable (768 wires) on Alexei's 8x64 board (512 pads); wafer6 only"),
    "dummy_61_10": Design(
        "dummy_61_10", "dummy", "01_generate.py", "02_make_dummy.py",
        {}, "12-block pad grid, wire band shorted by a cap (etch/release test), GDS"),
    "dummy_35_36": Design(
        "dummy_35_36", "dummy", "01_generate.py", "02_make_dummy.py",
        {"GEN_CONFIG": {"pad_side": 37.0, "pad_side_extra": 35.0, "gap": 34.0},
         "GEN_ROUTING": {"via_offset_y": 15.75}},
        "dummy with 37 um pads, 34 um gap"),
}


# =============================================================================
# REVISIONS
# =============================================================================
# {wafer: {N: Rev}}. A revision of a wafer changes only some masks; a NEW wafer (new devices, new
# placement) starts again at Rev1. The wafers themselves -- names, layouts -- are maskgen/core/wafers.py.
#   parent        the revision this one starts from: N (same wafer), ("wafer4", 4) (another wafer), or
#                 None (the base configs)
#   layerset      the wafer layers (maskgen/core/layers.py LAYERSETS)
#   set           applied to every design
#   per_design    {design: settings}, applied after `set`
#   dummy_set     applied to the dummy designs only
#   via_edge_gap  um from the pad edge to every via's EDGE (None: vias where the config puts them)
#   band_ref_rev  stage 02 routes around THAT revision's wire band -- N or (wafer, N); its stage-01 DXF is
#                 regenerated into devices/<device>/ref_rev<N>/ (ref_<wafer>_rev<N>/ for another wafer's) -- so the
#                 connector side does not move. NOT inherited.
#   status, changes   for people: what this revision is, and what it changes, one line each
Rev = namedtuple("Rev", "n parent layerset set per_design dummy_set via_edge_gap band_ref_rev "
                        "status changes")

# wafer6 Rev1's 12-block settings (12block_56_15); the 8x64 device shares its chip side and cable
W6_12BLOCK = {"GEN_ROUTING": {
    "m1_margin": 2.0, "via_k0_where": "needed", "via_k0_h": 5.0, "top_lane_uniform": True},
    # the shifted lanes reach up to 5.25 um from Rev3's band; stage 02's sanity limit on that
    # step (02_route.py, default 5 um) -- the step itself is a 300 um S-curve, checked for gaps
    "BAND_REF_MAX_DX": 6.0,
    # Phase-1 fan: lanes 6.5 um apart (was 7.0), band -> fan end 3.0 mm (NECK_LEN 420 + fan)
    "FAN_TARGET_PITCH": 6.5, "FAN_LEN": 2580.0,
    # stage 02 is the new router, 02_route_wafer6.py: its geometry is fixed in that file;
    # FAN_TARGET_PITCH is its WIDE lane pitch
    "ROUTE_SCRIPT": "02_route_wafer6.py",
    # the neck's polyimide widening follows the fan: it ends at the fan bottom (automatic)
    # and now also starts 467 um lower (Rev4 -400 um), so the ramp keeps its length
    "POLY_FAN_TAPER_START_Y_UM": -400 - (2580.0 - 2112.914695780087),
    # FAN_TARGET_PITCH changed: recompute the values derived from it (resolve.py)
    "REDERIVE": True}
# 8x64_56_15: wafer4 Rev4's 12-block via settings (it has no wafer4 history of its own) + W6_12BLOCK, and its own
# stage 02 (02_route_wafer6_8x.py: Alexei's 8x64 board)
W6_8X64 = dict(W6_12BLOCK,
               GEN_ROUTING=dict({"via_k0_radius": 1.75, "via_k0_dx": -0.25, "via_rest_dx": 0.5},
                                **W6_12BLOCK["GEN_ROUTING"]),
               ROUTE_SCRIPT="02_route_wafer6_8x.py")
W6_CHANGES = [
    "one via rule: every Metal1 landing pad 2.0 um from the Metal2 pad's left and top edges, "
    "every via edge 3.0 um (Rev4: 0.5-2.0 um in x, 1.5 um in y, by via kind) (m1_margin)",
    "top-row vias: the full 5 um via on a 7 um Metal1 pad wherever it fits -- that column's lanes "
    "move 1.5 um right to pass it; the small via only in the columns that carry 15 lanes (the "
    "left half of the finished device), whose lanes cannot move (via_k0_where)",
    "the small top vias become vertical capsules 3.5 x 5 um on a 5.5 x 7 um Metal1 pad (via_k0_h)",
    "the top pad's lane sits one 4.5 um lane pitch from the next lane, like every lane, and jogs "
    "45 deg into its via (top_lane_uniform)",
    "Phase-1 fan: lanes 6.5 um apart after the fan (Rev4: 7.0), 2 um wire + 4.5 um gap -- the bundle and "
    "the polyimide neck ~384 um narrower -- and the fan is 3.0 mm long (band -> full width; Rev4 "
    "2.53 mm); the neck's widening moves down with it by 467 um (FAN_TARGET_PITCH, FAN_LEN, "
    "POLY_FAN_TAPER_START_Y_UM)",
    "new stage 02 (02_route_wafer6.py): Metal1 only, no via row. Alexei's 12x64 board is the ground "
    "truth: connector pads on his drills (right column +15 um vs the CSV), polyimide = his outline + "
    "the neck with 2 mm neck fillets (Rev4: 3 mm), a 0.8 mm hole at each corner mounting hole "
    "(ROUTE_SCRIPT)",
    "connector k takes band group k-1 (64 wires; 1-6 down the left column, 7-12 up the right): "
    "1 and 12 from the side walls -- off the neck, along the top edge just above block 1, down the "
    "wall clear of the notches; 2-11 down the centre, peeling off block by block",
    "lane gaps: 4.5 um in the neck and the wall bundles; the centre fans in to 2.5 um between the "
    "first blocks, then 3.5 um after block 2 and 5.5 um after blocks 3-4",
    "into a block: two 32s, one per wide lane (rows 1-2, 3-4); top 16 to the row above, bottom 16 "
    "to the row below, outermost wire to the nearest pad; 90 deg turn, 15 um approach, teardrop "
    "with a 100 um fillet",
] + [
    "8x64_56_15 (new device): the 12-block's chip side, band, fan and neck as above on Alexei's 8x64 board "
    "(2 x 4 connectors), its top connector where the 12x64's block 1 is (ROUTE_SCRIPT 02_route_wafer6_8x.py)",
    "8x64: 96 wires per connector. Connector 1 / 8 from the wall: off the neck, along the top edge at 4.5 um "
    "pitch 50 um above the pads, down the wall; connectors 2-4 / 5-7 down the centre at 5.0 um pitch, 5.5 um "
    "after connector 2 / 7 has peeled off",
    "8x64: into a connector as 2 x 48, one per wide lane; 16 to the outer row, 32 alternately to the two "
    "centre rows (the far row's wires between the near row's pads) -- the 32 centre-row pads get two wires "
    "each, one from either lane, 180 deg apart; 15 um approach, 100 um teardrop fillet",
    "8x64: polyimide = his outline with the top edge raised 259 um for the top run, 2 mm neck fillets",
]

REVISIONS = {
    "wafer4": {
        2: Rev(2, None, "interconnect_rev2",
               set={"GEN_CONFIG": {"pad_etch": "square", "metal3": False}},
               per_design={}, dummy_set={}, via_edge_gap=None, band_ref_rev=None,
               status="FABRICATED (MEA1K_wafer4_12x_8x_8x_breakoutboards_Rev2.gds)",
               changes=["the PI etch is one square per pad (EtchingPad), no Metal3"]),

        3: Rev(3, 2, "interconnect_rev3",
               set={"GEN_CONFIG": {"pad_etch": "vias", "metal3": True}},
               per_design={}, dummy_set={}, via_edge_gap=None, band_ref_rev=None,
               status="not fabricated",
               changes=["the PI etch becomes 5 um etch vias around the pad perimeter (none in the corners)",
                        "Metal3: the pad squares again, on a new layer (wafer 8)"]),

        4: Rev(4, 3, "interconnect_rev4",
               set={"GEN_CONFIG": {"etch_via_spacing": (9.5, 21.0),
                                   "etch_via_layer": layers.IC.PI_STAB_VIA,
                                   "metal3_inset": 2.0},
                    "GEN_ROUTING": {"m1_pad": True},
                    "GEN_POLYIMIDE": {"trim_xmax": 2.25}},
               per_design={d: {"GEN_ROUTING": {"via_k0_radius": 1.75, "via_k0_dx": -0.25, "via_rest_dx": 0.5}}
                           for d in ("12block_56_15", "dummy_61_10", "dummy_35_36")},
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
    },

    "wafer6": {
        1: Rev(1, ("wafer4", 4), "interconnect_rev4",
               set={},
               per_design={"12block_56_15": W6_12BLOCK, "8x64_56_15": W6_8X64},
               dummy_set={}, via_edge_gap=None, band_ref_rev=("wafer4", 3),
               status="in design",
               changes=W6_CHANGES),
    },
}


# A revision always sits under its wafer: REVISIONS = {"wafer6": {1: Rev(...)}}.
for _w, _revs in REVISIONS.items():
    if not isinstance(_w, str) or not isinstance(_revs, dict):
        raise SystemExit("catalog.REVISIONS: %r is not a wafer -- each revision goes inside its wafer's dict, "
                         "e.g. \"wafer6\": {1: Rev(...)}" % (_w,))


def _ref(wafer, x):
    """A parent / band_ref_rev entry as (wafer, n): an int means the same wafer."""
    return None if x is None else tuple(x) if isinstance(x, (tuple, list)) else (wafer, x)


def get(wafer, rev):
    """The Rev of `wafer` at `rev`, or a SystemExit naming what exists."""
    if wafer not in REVISIONS:
        raise SystemExit("no interconnect revisions for wafer %r; known: %s" % (wafer, ", ".join(REVISIONS)))
    if rev not in REVISIONS[wafer]:
        raise SystemExit("%s has no Rev%s; it has: %s"
                         % (wafer, rev, ", ".join("Rev%d" % n for n in sorted(REVISIONS[wafer]))))
    return REVISIONS[wafer][rev]


def label(wafer, rev):
    return "%s Rev%d" % (wafer, rev)


def chain(wafer, rev):
    """[(wafer, Rev), ...] that (wafer, rev) is built from, oldest first, ending with itself. A chain may
    cross wafers: wafer6 Rev1 starts from wafer4 Rev4."""
    out, cur = [], (wafer, rev)
    while cur is not None:
        r = get(*cur)
        out.insert(0, (cur[0], r))
        cur = _ref(cur[0], r.parent)
    return out


def band_ref(wafer, rev):
    """(wafer, n) whose stage-01 band stage 02 routes around, or None."""
    return _ref(wafer, get(wafer, rev).band_ref_rev)


def devices(wafer, rev):
    """The designs on (wafer, rev): maskgen/core/wafers.py `devices`, else every design in DESIGNS."""
    from maskgen.core import wafers
    get(wafer, rev)
    ds = wafers.rev(wafer, rev).get("devices")
    return list(ds) if ds else list(DESIGNS)


def file_names(design):
    """(stage-01 pad-grid DXF, final file) of `design`, inside its device folder: <key>_padgrid.dxf and
    <key>.dxf (routed) or <key>.gds (capped dummy)."""
    d = DESIGNS[design]
    return d.key + "_padgrid.dxf", d.key + (".gds" if d.route == "02_make_dummy.py" else ".dxf")
