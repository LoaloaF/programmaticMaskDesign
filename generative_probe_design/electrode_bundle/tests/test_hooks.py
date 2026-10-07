"""The insertion hooks (hooks/) and the loop datum they define.

The hook carries the probe's ONE etch hole, and every design's `fiber_length` and
`loop_offset` are solved against it (lengths.py). So these tests guard the two things a new
or edited hook can silently break: the `drop` contract the solver depends on, and the
polyimide wall left around the hole.
"""
import numpy as np
import pytest

from electrode_bundle.bundle import build_bundle
from electrode_bundle.config import BundleConfig
from electrode_bundle.geometry import create_ellipse_polygon
from electrode_bundle.hooks import HOOKS, etch_wall, get_hook
from electrode_bundle.lengths import loop_y
from electrode_bundle.hooks.teardrop_clap import TeardropClapHook

STEM_HW = 10.0        # the wide channel's polyimide half-width, which the hook continues


def _ring(hook, drop=0.0, scale=1.0):
    x, y = hook.polygon(0.0, 0.0, stem_hw=STEM_HW, scale=scale, drop=drop)
    e = hook.etch(0.0, 0.0, stem_hw=STEM_HW, scale=scale, drop=drop)
    return (x, y), create_ellipse_polygon(*e)


def _y_of_etch(hook, drop):
    return hook.etch(0.0, 0.0, stem_hw=STEM_HW, scale=1.0, drop=drop)[1]


def _clap_tip_ys(hook, side=1, scale=1.0):
    """y of each clap tip on ONE side, shallowest first -- read off the built outline.

    Only the caps reach out this far, so their points fall into one tight y-cluster per
    clap. The threshold is keyed to the CLAPS' OWN REACH rather than to the bulb: the arm
    roots are filleted into the bulb (`clap_fillet_r`), and that fillet material does reach
    a little past the bulb's half-width, which a bulb-keyed threshold counts as a fourth
    clap.
    """
    (x, y), _ = _ring(hook, scale=scale)
    out = sorted((yy, side * xx) for xx, yy in zip(x, y)
                 if side * xx > (hook.clap_tip_x - hook.clap_tip_d) * scale)
    groups = [[out[0]]]
    for a, b in zip(out[:-1], out[1:]):
        if b[0] - a[0] > 2.0:
            groups.append([])
        groups[-1].append(b)
    #the cluster's OUTERMOST vertex, not its mean: the mean drifts with how many arm
    #vertices happen to fall inside the cluster, which the boolean union does not fix
    return [max(g, key=lambda p: p[1])[0] for g in reversed(groups)]


@pytest.mark.parametrize("name", sorted(HOOKS))
def test_etch_hole_moves_down_by_exactly_the_drop(name):
    """solve_lengths lands on `loop_offset` in one step ONLY because this slope is 1."""
    hook = get_hook(name)
    y0 = _y_of_etch(hook, 0.0)
    for drop in (25.0, 137.5, 400.0, -5.0):
        assert _y_of_etch(hook, drop) == pytest.approx(y0 - drop, abs=1e-9)


@pytest.mark.parametrize("name", sorted(HOOKS))
def test_the_head_keeps_its_shape_as_it_drops(name):
    """`drop` may only lengthen the stem: the head below it must be a rigid translation."""
    hook = get_hook(name)
    (x0, y0), _ = _ring(hook, drop=0.0)
    (x1, y1), _ = _ring(hook, drop=60.0)
    #the head is everything below the stem; compare the two lowest 40 um of outline
    head0 = np.sort(np.array([p for p in zip(x0, y0) if p[1] < y0.min() + 40], dtype=float), axis=0)
    head1 = np.sort(np.array([p for p in zip(x1, y1) if p[1] < y1.min() + 40], dtype=float), axis=0)
    assert head0.shape == head1.shape
    assert head1[:, 0] == pytest.approx(head0[:, 0])
    assert head1[:, 1] == pytest.approx(head0[:, 1] - 60.0)


@pytest.mark.parametrize("name", sorted(HOOKS))
def test_the_etch_hole_leaves_enough_polyimide(name):
    """Both hooks must clear the shipped margin -- that is what build_bundle asserts."""
    hook = get_hook(name)
    poly, etch = _ring(hook)
    assert etch_wall(poly, etch) >= BundleConfig().hook_etch_margin


def test_the_teardrop_wall_is_more_even_than_the_barbs_it_replaced():
    """The point of the redesign: an ellipse in a bulb, not a circle in an arrowhead."""
    legacy = etch_wall(*_ring(get_hook("legacy_barb")))
    teardrop = etch_wall(*_ring(get_hook("teardrop_clap")))
    assert teardrop > legacy


def test_the_build_reports_the_loop_the_hook_spec_puts_there():
    """`BundleResult.loop_xy` is the datum every design's length is solved against.

    It comes straight from the hook spec now. It used to be measured back off a layer that
    held the loop hole alone, but the loop is etched in the same step that frees the device,
    so the hole lives in `Etching` with the rest of the mask (see test_etch_mask).
    """
    cfg = BundleConfig()
    result = build_bundle(cfg)
    assert result.loop_xy is not None
    assert loop_y(result) == pytest.approx(result.loop_xy[1])


def test_build_bundle_rejects_a_hook_whose_hole_is_too_close_to_the_edge():
    cfg = BundleConfig(hook="legacy_barb", hook_etch_margin=25.0)
    with pytest.raises(AssertionError, match="polyimide around the etch hole"):
        build_bundle(cfg)


def test_unknown_hook_name_is_refused_by_name():
    with pytest.raises(ValueError, match="unknown hook"):
        get_hook("no_such_hook")


# --- the teardrop's own parameters -------------------------------------------------------

def test_six_claps_three_a_side():
    hook = get_hook("teardrop_clap")
    assert hook.n_claps_per_side == 3
    left, right = _clap_tip_ys(hook, side=-1), _clap_tip_ys(hook, side=1)
    assert len(left) == len(right) == 3
    assert left == pytest.approx(right)            # the head is symmetric about the trace


def test_the_claps_are_five_microns_wide():
    """The user's one requested change: 2.1257413 um in the drawing -> 5 um."""
    assert get_hook("teardrop_clap").clap_width == 5.0


def test_widening_the_claps_spreads_them_instead_of_closing_the_slots_up():
    """Arms step by width+gap, so the etch slot between them survives the widening.

    Measured off the built outline, not off the fields: the pitch has to come out of the
    geometry or this only tests that the dataclass stores what it was given.
    """
    #A 2.13 um arm under a 7 um cap leaves only 3.56 um beside each cap, so the narrow
    #variant cannot carry the shipped 2 um root fillet (`_min_clap_gap` refuses it). The
    #fillet is irrelevant here either way: it rounds inner corners, and the tips these
    #pitches are read off are convex.
    narrow = TeardropClapHook(clap_width=2.1257413, clap_fillet_r=1.5)  # the drawing's callout
    wide = TeardropClapHook(clap_width=5.0)             # what the user asked for
    pitch_n = -np.mean(np.diff(_clap_tip_ys(narrow)))
    pitch_w = -np.mean(np.diff(_clap_tip_ys(wide)))
    grew = (wide.clap_width - narrow.clap_width) / np.cos(np.radians(wide.clap_angle_deg))
    assert pitch_w - pitch_n == pytest.approx(grew, abs=1e-6)


def test_the_stem_matches_whatever_trace_it_continues():
    """A parametric hook meets the trace seamlessly at any trace width."""
    hook = get_hook("teardrop_clap")
    for stem_hw in (8.0, 10.0, 12.0):
        x, y = hook.polygon(0.0, 0.0, stem_hw=stem_hw, scale=1.0, drop=50.0)
        top = [xx for xx, yy in zip(x, y) if yy > y.max() - 1e-9]
        assert max(top) == pytest.approx(stem_hw)
        assert min(top) == pytest.approx(-stem_hw)


def test_scale_scales_the_whole_hook_including_its_hole():
    hook = get_hook("teardrop_clap")
    (x1, y1), e1 = _ring(hook, scale=1.0)
    (x2, y2), e2 = _ring(hook, scale=2.0)
    assert y2.min() == pytest.approx(2 * y1.min())
    assert hook.etch(0.0, 0.0, stem_hw=STEM_HW, scale=2.0)[2] == pytest.approx(
        2 * hook.etch(0.0, 0.0, stem_hw=STEM_HW, scale=1.0)[2])


@pytest.mark.parametrize("hook_drop", [-97.5, 0.0, 152.5])
def test_the_reference_keeps_its_gold_and_its_pads_out_of_the_loop(hook_drop):
    """Nothing the reference draws may land in the etched hole -- at ANY hook_drop.

    `hook_drop` goes NEGATIVE whenever a design asks for a `loop_offset` shorter than the
    fiber's own overhang below the deepest site (`polyimide_pad_r + 250` = 262.5 um; U2.5
    asks for 250). The head is then pulled UP and the hole ends up ABOVE where the fiber
    ends, which is the case the -97.5 leg covers and the case that used to fail: the
    reference's trace was clamped to stop no higher than the fiber end, so it ran straight
    across the hole, and the bottom band -- which fills every pitch down to the end of the
    gold -- put its lowest contacts inside the loop.

    Checked on the DRAWN geometry rather than on `ref_metal_bottom`, because it is the
    contacts landing in the hole that ruins the part, and they are placed by a separate
    calculation from the trace's.
    """
    from shapely.geometry import Point, Polygon as _P
    from electrode_bundle.config import BundleConfig

    cfg = BundleConfig(num_channels=16, delta_y=200.0, delta_y_overrides=None,
                       hook_drop=hook_drop)
    built = build_bundle(cfg)
    hole = _P(create_ellipse_polygon(
        *get_hook(cfg.hook).etch(0.0, built.electrode_locs[:, 1].min() - cfg.polyimide_pad_r
                                 - 250, stem_hw=cfg.wide_wire_hw, scale=cfg.hook_scale,
                                 drop=hook_drop))[:-1])

    for x, y in built.ref_locs:
        assert not Point(x, y).buffer(cfg.l / 2).intersects(hole), (
            f"a reference contact at y={y:.1f} lands in the loop hole "
            f"(centre y={loop_y(built):.1f}) at hook_drop={hook_drop}")
    # and the trace that feeds them
    for lay in ("Metal", "PEDOT_SIROF"):
        for e in built.msp.query(f'*[layer=="{lay}"]'):
            pts = [(p[0], p[1]) for p in e.get_points()]
            if len(pts) >= 3:
                assert not _P(pts).buffer(0).intersects(hole.buffer(-1e-6)), \
                    f"{lay} geometry crosses the loop hole at hook_drop={hook_drop}"
