"""The electrode site: round contact, round metal pad, smooth polyimide swelling.

The site used to be a rounded SQUARE at all three layers. These tests pin the two things
the switch to circles had to preserve -- the contact CENTRES, which IONP keepouts and the
measured site span are derived from, and a tangent (crease-free) join into the trace -- and
the two things it had to change: the shapes themselves.
"""
import numpy as np
import pytest

from electrode_bundle.bundle import build_bundle
from electrode_bundle.config import BundleConfig
from electrode_bundle.lengths import measure
from electrode_bundle.shapes import (_pad_fillet, create_metal_pad_wire_outline,
                                     create_polyimide_outline)

CFG = BundleConfig()


def _layer_rings(result, layer):
    return [np.array([(p[0], p[1]) for p in e.get_points()])
            for e in result.msp.query(f'*[layer=="{layer}"]')]


def test_the_contact_is_a_circle_of_the_configured_diameter():
    result = build_bundle(CFG)
    for ring in _layer_rings(result, "Electrodes"):
        centre = ring[:-1].mean(axis=0)             # the ring's own centre, not electrode_locs
        r = np.hypot(ring[:, 0] - centre[0], ring[:, 1] - centre[1])
        assert r == pytest.approx(CFG.l_contact / 2, abs=1e-9)


def test_electrode_locs_still_matches_what_reading_the_dxf_back_gives():
    """electrode_locs is the mean of the ring AS WRITTEN -- closing duplicate included.

    Not the geometric centre: the duplicate first vertex pulls it off by radius/n_points.
    That bias is deliberate and load-bearing -- `ionp.electrode_locs_from_dxf` re-derives
    the same number the same way, so the in-memory and round-tripped pipelines agree on
    where a contact is. Changing the pad's shape must not break that agreement.
    """
    from electrode_bundle.dxf_io import extract_electrode_centroids_from_msp

    result = build_bundle(CFG)
    round_tripped = extract_electrode_centroids_from_msp(result.msp, "Electrodes")
    assert round_tripped == pytest.approx(result.electrode_locs, abs=1e-9)


def test_the_site_span_did_not_move_when_the_pads_went_round():
    """Round or square, the span is centre-to-centre -- it must not depend on pad shape."""
    span, _, _ = measure(build_bundle(BundleConfig(delta_y=90.0, delta_y_overrides=None)))
    assert span == pytest.approx(63 * 90.0)


def test_the_metal_pad_is_a_circle_with_a_wire_on_top():
    x, y = create_metal_pad_wire_outline(0.0, 0.0, pad_r=CFG.l / 2, wire_hw=1.0,
                                         wire_top_y=500.0, R=4.0)
    #everything at or below the pad centre is pad, and every one of those is on the circle
    below = np.array([(a, b) for a, b in zip(x, y) if b <= 0.0])
    assert len(below) > 8
    assert np.hypot(below[:, 0], below[:, 1]) == pytest.approx(CFG.l / 2, abs=1e-9)
    assert y.max() == pytest.approx(500.0)


def test_the_polyimide_swelling_is_a_lens_around_the_pad_circle():
    pad_r, wire_hw, R = CFG.polyimide_pad_r, 5.0, CFG.polyimide_lens_radius
    x, y = create_polyimide_outline(0.0, 0.0, pad_r, wire_hw, wire_top_y=500.0,
                                    bot_y=-100.0, R=R, tip_r=1.0)
    #the widest part is the pad circle itself, and it IS a circle
    on_pad = np.array([(a, b) for a, b in zip(x, y) if abs(a) > wire_hw + 1e-9
                       and np.hypot(a, b) < pad_r + 1e-9])
    assert np.hypot(on_pad[:, 0], on_pad[:, 1]) == pytest.approx(pad_r, abs=1e-9)
    assert np.abs(x).max() == pytest.approx(pad_r, abs=0.01)
    #and only at the pad: the swelling has closed back onto the trace by the tangent point
    h, _ = _pad_fillet(pad_r, wire_hw, R)
    assert np.abs(np.array([a for a, b in zip(x, y) if b > h + 1e-9])).max() \
        == pytest.approx(wire_hw)


def test_the_trace_meets_the_pad_without_a_crease():
    """Tangent at both ends is the whole point of the fillet -- check the outline's slope.

    A rounded square steps from a vertical trace edge onto a horizontal pad edge; a lens
    must never turn by more than the sampling step, anywhere between the two tangencies.
    """
    pad_r, wire_hw, R = CFG.polyimide_pad_r, 5.0, CFG.polyimide_lens_radius
    x, y = create_polyimide_outline(0.0, 0.0, pad_r, wire_hw, wire_top_y=60.0,
                                    bot_y=-100.0, R=R, tip_r=1.0)
    h, _ = _pad_fillet(pad_r, wire_hw, R)
    band = np.array([(a, b) for a, b in zip(x, y) if abs(b) <= h + 1e-9 and a > 0])
    d = np.diff(band, axis=0)
    ang = np.degrees(np.arctan2(d[:, 1], d[:, 0]))
    turn = np.abs((np.diff(ang) + 180) % 360 - 180)
    assert turn.max() < 6.0             # the coarsest arc here steps by ~5 degrees


def test_a_pad_no_wider_than_its_trace_is_refused():
    with pytest.raises(ValueError, match="must be < pad radius"):
        create_polyimide_outline(0.0, 0.0, 5.0, 5.0, 60.0, -100.0, R=12.0)


# --- the tip below each site -------------------------------------------------------------

def test_the_tip_ends_a_fixed_distance_below_its_own_electrode():
    """`polyimide_tip_len` is measured from the CONTACT CENTRE, per the drawing."""
    result = build_bundle(CFG)
    poly = np.concatenate([r for r in _layer_rings(result, "Polyimide")])
    #the shallowest channel's tip is the lowest polyimide anywhere near its own x
    for ch in (5, 40, 63):
        cx, cy = result.electrode_locs[ch]
        near = poly[np.abs(poly[:, 0] - cx) < 6]
        assert near[:, 1].min() == pytest.approx(cy - CFG.polyimide_tip_len, abs=0.05)


def test_the_tip_is_a_taper_onto_a_round_cap_not_a_needle():
    pad_r, wire_hw, R, tip_r = CFG.polyimide_pad_r, 5.0, CFG.polyimide_lens_radius, 1.0
    x, y = create_polyimide_outline(0.0, 0.0, pad_r, wire_hw, wire_top_y=60.0,
                                    bot_y=-100.0, R=R, tip_r=tip_r)
    assert y.min() == pytest.approx(-100.0, abs=1e-9)      # the tip is a real vertex
    #the very end is the cap: every point below its centre is on the cap circle
    cap = np.array([(a, b) for a, b in zip(x, y) if b < -100.0 + tip_r])
    assert np.hypot(cap[:, 0], cap[:, 1] + 100.0 - tip_r) == pytest.approx(tip_r, abs=1e-9)
    #and the taper is monotonic: no waist, no flare on the way down
    lower = sorted(((b, abs(a)) for a, b in zip(x, y) if -100.0 + tip_r < b < -20.0),
                   reverse=True)
    widths = [w for _, w in lower]
    assert all(b <= a + 1e-9 for a, b in zip(widths, widths[1:])), "the taper is not monotonic"


def test_the_hook_channel_ends_flat_because_the_hook_carries_on():
    x, y = create_polyimide_outline(0.0, 0.0, CFG.polyimide_pad_r, 10.0, wire_top_y=60.0,
                                    bot_y=-250.0, R=CFG.polyimide_lens_radius, tip_r=None)
    bottom = [a for a, b in zip(x, y) if b == pytest.approx(-250.0)]
    assert sorted(bottom) == pytest.approx([-10.0, 10.0])
