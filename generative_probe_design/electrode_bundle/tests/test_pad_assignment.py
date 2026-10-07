"""Which solder pad each channel lands on, and that the routes to them do not cross.

`assign_channels_to_pads` is the single source of truth for TWO things at once -- where
`build_pad_routes` aims each diagonal, and which flex-PCB pad number `mapping.py` hands the
channel. Nothing guarded it. That matters more than it looks: the pad column order is not a
convention anyone is free to change, it is the ONLY crossing-free ordering this topology
has (each trace is a riser plus one fixed-angle diagonal, so an inner lane reaching a lower
pad crosses every riser between them), and the flex PCB on the other end is fixed hardware.

So this pins today's assignment literally, in both forms, and then checks the geometric
property the assignment exists to provide.
"""
import pytest
from shapely.geometry import Point, Polygon

from electrode_bundle import shapes as sh
from electrode_bundle.bundle import build_bundle
from electrode_bundle.config import REF_SITES, BundleConfig, MappingConfig
from electrode_bundle.mapping import build_mapping, standalone_chan_pad

N = 64
CFG = BundleConfig()


@pytest.fixture(scope="module")
def built():
    return build_bundle(CFG)


def _todays_assignment():
    """The 64-channel assignment as it stands, written as the rule it follows.

    The REFERENCE fiber holds the centre slot, so the recording channels alternate outward
    from it: channel 0 takes slot +1 and the EVEN channels run up the +x side, the odd ones
    up -x. The lowest-x half serves the left pad column, and within a column the OUTERMOST
    lane peels to the LOWEST connected pad. So the odd channels fill the left column from
    the top down and the even ones do the same on the right.

    This flipped when the reference took the centre slot. Before that there were 64 fibers
    with channel 0 in the middle, the even channels ran up -x, and the columns were the
    other way round (ch0 -> left 32, ch1 -> right 32, ch62 -> left 1, ch63 -> right 1).
    Every channel's flex pad number changed with it -- see test_todays_flex_pad_numbers.
    """
    chan_pad = {}
    for ch in range(N):
        column = 'right' if ch % 2 == 0 else 'left'
        chan_pad[ch] = (column, 32 - (ch if ch % 2 == 0 else ch - 1) // 2)
    return chan_pad


def test_the_rule_above_really_is_todays_assignment():
    """Spot-check the derived rule against literal values, so a typo in it cannot self-confirm."""
    got = _todays_assignment()
    assert got[0] == ('right', 32)     # deepest site, innermost lane -> far end of its column
    assert got[2] == ('right', 31)
    assert got[62] == ('right', 1)     # outermost on +x -> lowest connected pad
    assert got[1] == ('left', 32)
    assert got[63] == ('left', 1)
    assert len(got) == N


def test_standalone_chan_pad_matches_it():
    assert standalone_chan_pad(N, CFG.delta_x) == _todays_assignment()


def test_the_real_build_matches_it(built):
    """The widened reference fiber shifts x positions but not their ORDER, so the
    parametric fallback and the real build must agree -- that is why `standalone_chan_pad`
    is allowed to exist at all."""
    assert built.chan_pad == _todays_assignment()


def test_every_connected_pad_is_used_exactly_once(built):
    rows = {'left': [], 'right': []}
    for column, row in built.chan_pad.values():
        rows[column].append(row)
    for column, got in rows.items():
        assert sorted(got) == list(range(1, CFG.n_pads_per_column)), \
            f"{column} column does not use rows 1..32 exactly once"


def test_todays_flex_pad_numbers(built):
    """The other half of the contract: the pad NUMBER on the fixed flex PCB."""
    summary = build_mapping(built.chan_pad, MappingConfig())
    m = summary["mapping"]
    assert m["0"] == 33      # deepest site, far end of the bottom row
    assert m["2"] == 35
    assert m["62"] == 34
    assert m["1"] == 2
    assert m["63"] == 1
    assert sorted(int(v) for v in m.values()) == list(range(1, 65))


def test_the_routes_to_those_pads_do_not_cross(built):
    """The property the ordering exists for, measured on the drawn polygons.

    Two traces that cross overlap in area; two that merely run parallel at lane pitch do
    not touch at all. Checked pairwise over all 64 rather than on centerlines, because the
    polygons are what is fabricated.
    """
    routes, ref, _tails = _routes(built)
    polys = {ci: Polygon(ring).buffer(0) for ci, ring in routes.items() if ring}
    polys['ref'] = Polygon(ref).buffer(0)
    assert len(polys) == N + 1
    keys = sorted(polys, key=str)
    overlaps = [(a, b) for i, a in enumerate(keys) for b in keys[i + 1:]
                if polys[a].intersection(polys[b]).area > 1e-9]
    assert not overlaps, f"routes overlap (i.e. cross): {overlaps[:10]}"


def _routes(built):
    return sh.build_pad_routes(
        built.top_points, built.pads_left, built.pads_right,
        neck_tw=CFG.fan_neck_tw, top_tw=CFG.fan_top_tw,
        neck_len=CFG.fan_neck_len, fan_len=CFG.fan_fan_len,
        bundle_pitch=CFG.bundle_pitch, approach_deg=CFG.pad_approach_deg,
        ref_top_point=(0.0, built.wire_top), ref_tw=REF_SITES.wire_width,
        pad_r=CFG.pad_diam / 2, poly_hw=CFG.polyimide_width / 2,
        poly_top_y=built.pads_left[-1][1] + 500,
        #from the config, exactly as build_bundle does -- NOT the defaults in shapes.py.
        #Those are a standalone-use fallback, and a test that silently used them instead
        #would be checking geometry no build ever produces.
        wrap_clearance=CFG.ref_wrap_clearance, wrap_exit_run=CFG.ref_wrap_exit_run,
        wrap_corner_chamfer=CFG.ref_wrap_corner_chamfer,
        wrap_pad_chamfer=CFG.ref_wrap_pad_chamfer,
    )


def _ref_column(built):
    """(the pad column the Ref lands on, the other one) -- read off shapes.REF_PAD.

    Which side the reference takes is a wiring decision that has moved once already, and
    these tests assert the SHAPE of the wrap, which is the same shape either way. So they
    follow REF_PAD rather than naming a side: flipping it must not need a test edit, and
    must not leave a test quietly checking the spare pad's side instead.
    """
    cols = {'left': built.pads_left, 'right': built.pads_right}
    return cols.pop(sh.REF_PAD[0]), cols.popitem()[1]


def test_the_reference_wraps_around_into_pad_row_0(built):
    """The Ref reaches row 0 of its column, and does it from OUTSIDE the pad column.

    Row 0 is the pad nearest the bundle, which no ordinary diagonal can reach without
    cutting every riser above it. The wrap is what makes it legal, so this asserts the
    shape of the wrap and not merely that the two ends are connected: the route has to get
    out past the pad centres, and it has to come back down out there.
    """
    _, ref, _tails = _routes(built)
    rp = Polygon(ref).buffer(0)
    ref_col, _ = _ref_column(built)
    pad_x, pad_y = ref_col[sh.REF_PAD[1]]
    assert rp.intersects(Point(pad_x, pad_y)), "the Ref route does not reach pad row 0"
    #it goes further out than the pad column, which is the whole point. "Out" is whichever
    #way its own column lies, so the bound to read is the one on that side.
    outer = rp.bounds[0] if pad_x < 0 else rp.bounds[2]
    assert abs(outer) > abs(pad_x) + CFG.pad_diam / 2, \
        "the Ref route never leaves the pad column, so it cannot be the wrap-around"
    #and it climbs past the TOP pad before coming back down
    assert rp.bounds[3] > ref_col[-1][1] + CFG.pad_diam / 2, \
        "the Ref route does not clear the top of the pad field"


def test_the_wrap_keeps_its_clearances(built):
    """`ref_wrap_clearance` edge to edge from every pad it is not going to, and from
    both polyimide edges it passes."""
    _, ref, _tails = _routes(built)
    rp = Polygon(ref).buffer(0)
    poly_hw = CFG.polyimide_width / 2
    ref_col, other_col = _ref_column(built)
    block_top = ref_col[-1][1] + 500
    want = CFG.ref_wrap_clearance
    assert poly_hw - max(abs(rp.bounds[0]), abs(rp.bounds[2])) >= want, \
        "the wrap runs too close to the polyimide edge"
    assert block_top - rp.bounds[3] >= want, "the wrap runs too close to the top of the block"
    #every pad it is NOT going to: all of the other column, and all of its own but row 0
    pads = [Point(x, y).buffer(CFG.pad_diam / 2)
            for x, y in other_col + [p for i, p in enumerate(ref_col) if i != sh.REF_PAD[1]]]
    worst = min(rp.distance(pd) for pd in pads)
    assert worst >= want, f"the wrap passes {worst:.1f} um from a pad it does not belong to"


def test_the_reference_lane_does_not_narrow_the_bundle_pitch(built):
    """The Ref gets a full lane, not the half-pitch slot between the two innermost ones.

    With 64 recording lanes alone there is no lane on x = 0 at all; counting the reference
    in makes it 65 at full pitch. If this ever drops to ~half `bundle_pitch`, the reference
    was squeezed between its neighbours instead of being given a lane of its own.
    """
    routes, ref, _tails = _routes(built)
    rp = Polygon(ref).buffer(0)
    nearest = min(rp.distance(Polygon(r).buffer(0)) for r in routes.values() if r)
    assert nearest == pytest.approx(CFG.bundle_pitch - CFG.fan_top_tw, abs=1e-6), \
        f"the Ref lane sits {nearest:.1f} um from its neighbours, not the full pitch"
