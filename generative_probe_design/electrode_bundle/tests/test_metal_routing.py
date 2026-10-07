"""Every channel actually reaches its solder pad in the gold layer.

This exists because the routing was once dropped out of `build_bundle` by an edit that
meant to touch only the block next to it, and nothing failed: the lengths still solved, the
mapping JSON was still right (it comes from `assign_channels_to_pads`, not from the drawn
polygons), and the tip renders looked perfect. A DXF with 64 electrodes, 66 pads and no
wires between them passed the whole suite.

So this asserts on the DRAWN metal, not on the routing plan: geometry that exists, connects
the two ends, and does so for every channel.
"""
import numpy as np
import pytest
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from electrode_bundle.bundle import build_bundle
from electrode_bundle.config import BundleConfig

CFG = BundleConfig(num_channels=16, delta_y=200.0, delta_y_overrides=None)


@pytest.fixture(scope="module")
def built():
    return build_bundle(CFG)


@pytest.fixture(scope="module")
def gold(built):
    """The gold layer as ONE geometry -- pads, electrode pads, routes and markings fused."""
    return unary_union([Polygon([(p[0], p[1]) for p in e.get_points()]).buffer(0)
                        for e in built.msp.query('*[layer=="Metal"]')])


def test_every_channel_is_wired_from_its_electrode_to_its_solder_pad(built, gold):
    """One connected piece of gold per channel, from the contact up into the right pad.

    The strongest statement available without a netlist: take the union of everything on the
    gold layer, and check the contact and its assigned pad land in the SAME connected piece.
    """
    pieces = list(gold.geoms) if gold.geom_type == "MultiPolygon" else [gold]
    columns = {"left": built.pads_left, "right": built.pads_right}
    for ch in range(built.num_channels):
        column, row = built.chan_pad[ch]
        pad = Point(*columns[column][row])
        contact = Point(*built.electrode_locs[ch])
        touching = [i for i, g in enumerate(pieces) if g.intersects(contact)]
        assert touching, f"channel {ch} has no gold on its contact at all"
        assert pieces[touching[0]].intersects(pad), \
            f"channel {ch}: its gold does not reach its {column} pad {row} -- the wire, the " \
            "fanout route, or the join to the pad is missing"


def test_the_gold_layer_holds_a_route_per_channel_on_top_of_the_pads(built):
    """A count, so a wholesale deletion shows up as a number rather than a subtle gap."""
    n = built.num_channels
    pads = len(built.pads_left) + len(built.pads_right)
    drawn = len(list(built.msp.query('*[layer=="Metal"]')))
    #pads + one pad-and-wire per channel + one route per channel; markings add more
    assert drawn >= pads + 2 * n, \
        f"only {drawn} gold polygons for {n} channels and {pads} pads -- something that " \
        "should be drawn is not"


def test_the_routes_span_the_gap_between_the_shoulder_and_the_pads(built, gold):
    """There is gold at every height between the fibers and the connector.

    A missing fanout leaves a clean empty band there, which is exactly what it looked like.
    """
    shoulder = max(y for _, y in built.top_points)
    first_pad = min(y for _, y in built.pads_left + built.pads_right)
    assert first_pad > shoulder
    for frac in (0.1, 0.3, 0.5, 0.7, 0.9):
        y = shoulder + frac * (first_pad - shoulder)
        band = gold.intersection(Polygon([(-2000, y - 1), (2000, y - 1),
                                          (2000, y + 1), (-2000, y + 1)]))
        assert not band.is_empty, f"no gold at all at y = {y:.0f}, {frac:.0%} up the ribbon"


def test_electrode_locs_sit_on_their_own_gold_pad(built, gold):
    """The exposed opening must land on metal, or the site is an opening onto nothing."""
    for ch, (x, y) in enumerate(built.electrode_locs):
        assert gold.contains(Point(x, y).buffer(CFG.l_contact / 2 - 1e-6)), \
            f"channel {ch}: the {CFG.l_contact} um opening is not fully inside its gold pad"


def test_the_gold_pad_is_wider_than_the_opening_and_its_coating():
    """Gold > coating > opening: the coating has to land on metal, not straddle its edge."""
    assert CFG.l_contact < CFG.l_pedot < CFG.l
    assert (CFG.l - CFG.l_pedot) / 2 == pytest.approx(1.0)     # 17 um pad, 15 um coating
    assert (CFG.l_pedot - CFG.l_contact) / 2 == pytest.approx(0.75)  # 13.5 um opening


# --- the solder-pad teardrops ------------------------------------------------------------

def _pad_nets(cfg, R):
    """Per-channel gold at the CONNECTOR end -- route fused with its own pad teardrop.

    Built straight from `shapes`, not read back off the DXF, so it can be evaluated at an R
    the config does not carry: the point of the test below is what WOULD happen at a bigger
    radius, which a built document cannot answer.
    """
    from shapely.ops import unary_union

    from electrode_bundle import shapes as sh
    from electrode_bundle.config import REF_SITES

    built = build_bundle(cfg)
    pad_r, hw = cfg.pad_diam / 2, cfg.fan_top_tw / 2
    routes, ref, tails = sh.build_pad_routes(
        built.top_points, built.pads_left, built.pads_right,
        neck_tw=cfg.fan_neck_tw, top_tw=cfg.fan_top_tw, neck_len=cfg.fan_neck_len,
        fan_len=cfg.fan_fan_len, bundle_pitch=cfg.bundle_pitch,
        approach_deg=cfg.pad_approach_deg, ref_top_point=(0.0, built.wire_top),
        ref_tw=REF_SITES.wire_width, pad_r=pad_r, poly_hw=cfg.polyimide_width / 2,
        poly_top_y=built.pads_left[-1][1] + 500, wrap_clearance=cfg.ref_wrap_clearance,
        wrap_exit_run=cfg.ref_wrap_exit_run, wrap_corner_chamfer=cfg.ref_wrap_corner_chamfer,
        wrap_pad_chamfer=cfg.ref_wrap_pad_chamfer)
    pads = {'left': built.pads_left, 'right': built.pads_right}
    nets = {}
    for ci, ring in routes.items():
        col, pr = built.chan_pad[ci]
        cx, cy = pads[col][pr]
        td = sh.create_metal_pad_teardrop(cx, cy, pad_r, hw, R, tails[(col, pr)])
        nets[ci] = unary_union([Polygon(ring).buffer(0), Polygon(zip(*td)).buffer(0)])
    cx, cy = pads[sh.REF_PAD[0]][sh.REF_PAD[1]]
    rtd = sh.create_metal_pad_teardrop(cx, cy, pad_r, hw, R, tails[sh.REF_PAD])
    nets['ref'] = unary_union([Polygon(ref).buffer(0), Polygon(zip(*rtd)).buffer(0)])
    return nets


def _min_gap(nets):
    keys = sorted(nets, key=str)
    return min(nets[a].distance(nets[b]) for i, a in enumerate(keys) for b in keys[i + 1:])


def test_pad_teardrops_keep_the_metal_spacing():
    """The teardrops must not be the thing that sets the minimum gap in the gold.

    `bundle_pitch` - `fan_top_tw` = 15 um is the tightest the metal gets anywhere, held by
    the parallel lanes of the bundle itself. The pad flare is free as long as it stays
    behind that: it is a cosmetic-and-stress feature, and it should not be what a fab quotes
    the design's minimum spacing from.

    It is NOT free by much, which is why this is asserted rather than assumed. The two
    lowest connected pads in a column are fed by adjacent lanes running parallel one
    `bundle_pitch` apart, so the flare closes on its neighbour directly.
    """
    cfg = BundleConfig()
    want = cfg.bundle_pitch - cfg.fan_top_tw
    got = _min_gap(_pad_nets(cfg, cfg.pad_teardrop_fillet_radius))
    #The measured floor is ~1.8 nm under the nominal 15, and that is the bundle's own
    #polygon faceting, NOT the flare: it reads identically at every radius from 150 to 295,
    #the teardrop included or not. So the tolerance is faceting-sized (10 nm, four orders
    #under any feature a mask holds) rather than approx-exact, and anything the flare
    #actually costs -- 0.6 um at R=300, 12.5 at R=350 -- is far outside it.
    assert got >= want - 0.01, (
        f"the teardrops at R={cfg.pad_teardrop_fillet_radius} close the gold to {got:.3f} "
        f"um, inside the {want:.0f} um the bundle lanes hold; lower "
        f"pad_teardrop_fillet_radius")


def test_a_teardrop_big_enough_to_short_the_pads_is_visible_to_this_test():
    """The guard above has teeth: at R = 400 adjacent channels actually MERGE.

    Without this, a passing spacing test proves nothing -- it would pass just as happily on
    a build where the teardrops were too small to reach anything. 400 is only 1.5x the
    shipped radius, so the headroom being asserted is real and narrow.
    """
    cfg = BundleConfig()
    nets = _pad_nets(cfg, 400.0)
    shorted = [(a, b) for i, a in enumerate(sorted(nets, key=str))
               for b in sorted(nets, key=str)[i + 1:]
               if nets[a].intersection(nets[b]).area > 1e-9]
    assert shorted, "R=400 no longer shorts any pads -- re-derive the ceiling in config.py"
