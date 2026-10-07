"""Etching: the device-release mask, and the things it must get right.

The device is not deposited, it is FREED from a full sheet of polyimide, and this is the
only layer the fab patterns for that (the positive Polyimide layer is dropped on export).
So three properties matter and none is visible in a render: that every feature at the tip is
actually surrounded by etched negative rather than left joined to the sheet, that the
insertion loop's hole is in here too rather than on a layer of its own, and that the solder
pads are opened by it as well.

The SITE openings are not its business. They are a separate, shallower etch through the top
polyimide only, and they have their own mask, `pad_etching` -- the test below holds the two
apart, because a site circle left in the release mask would be cut to full depth.
"""
import numpy as np
import pytest
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union

from electrode_bundle import shapes as sh
from electrode_bundle.bundle import build_bundle
from electrode_bundle.config import LOOP_DATUM_VAR, BundleConfig
from electrode_bundle.side_by_side import loop_datum

CFG = BundleConfig(num_channels=16, delta_y=200.0, delta_y_overrides=None)


def _layer(result, name):
    return unary_union([Polygon([(p[0], p[1]) for p in e.get_points()]).buffer(0)
                        for e in result.msp.query(f'*[layer=="{name}"]')])


@pytest.fixture(scope="module")
def built():
    return build_bundle(CFG)


def test_the_tip_is_fully_released_by_the_etch_mask(built):
    """Every bit of polyimide below the shallowest tip is ringed by negative.

    This is the check the silhouette alone does not give you. The mask's tip is drawn as a
    clean spear, and the hook's claps reach ~15 um outside it; before the moat was unioned
    back in, those clap tips stayed welded to the unetched sheet -- which renders as a
    perfectly good picture and tears the hook apart on release.
    """
    poly, neg = _layer(built, "Polyimide"), _layer(built, "Etching")
    y_tip = min(built.electrode_locs[:, 1]) - CFG.polyimide_tip_len
    zone = box(-1e6, -1e6, 1e6, y_tip)
    tip = poly.intersection(zone)
    assert not tip.is_empty, "the tip zone should hold the hook and the deepest fibers"
    #a thin collar just outside the tip polyimide: all of it has to be etched away
    collar = tip.buffer(2.0).difference(tip).intersection(zone)
    assert collar.difference(neg).area < 1e-6, \
        f"{collar.difference(neg).area:.1f} um^2 of the tip is still joined to the sheet"


def test_the_mask_clears_the_tip_by_the_configured_margin(built):
    """Released is the floor; the margin is what the mask is actually drawn to."""
    poly, neg = _layer(built, "Polyimide"), _layer(built, "Etching")
    y_tip = min(built.electrode_locs[:, 1]) - CFG.polyimide_tip_len
    zone = box(-1e6, -1e6, 1e6, y_tip)
    tip = poly.intersection(zone)
    want = tip.buffer(CFG.polyimide_release_margin).difference(tip).intersection(zone)
    assert want.difference(neg).area < 1e-6


def test_the_insertion_loop_is_a_hole_in_this_mask_not_a_layer_of_its_own(built):
    """One etch step, one layer: the loop hole has no layer of its own, it is a ring in
    `Etching` like every other opening."""
    neg = _layer(built, "Etching")
    assert neg.contains(Polygon(
        [(built.loop_xy[0] + 2 * np.cos(a), built.loop_xy[1] + 2 * np.sin(a))
         for a in np.linspace(0, 2 * np.pi, 24, endpoint=False)]))


def test_the_loop_datum_survives_into_the_saved_dxf(built, tmp_path):
    """side_by_side lines designs up from this, and cannot read it off a layer any more."""
    path = tmp_path / "bundle.dxf"
    built.doc.saveas(path)
    import ezdxf
    assert loop_datum(ezdxf.readfile(path)) == pytest.approx(built.loop_xy[1], abs=1e-6)
    assert LOOP_DATUM_VAR                      # the name is shared, not spelled twice


def test_the_design_id_is_real_geometry_in_the_gold_layer():
    """A DXF TEXT entity would vanish on the GDS export -- the marking must be outlines."""
    plain = build_bundle(CFG)
    marked = build_bundle(BundleConfig(**{**CFG.__dict__, "label": "C1,IONP01"}))
    assert not list(marked.msp.query("TEXT")) and not list(marked.msp.query("MTEXT"))
    added = (len(list(marked.msp.query('*[layer=="Metal"]')))
             - len(list(plain.msp.query('*[layer=="Metal"]'))))
    assert added > 0, "the label added no geometry to the gold layer"
    #stamped twice: once beside the reference's bottom contact band, once above the pads
    assert added % 2 == 0


def test_the_design_id_keeps_off_the_hook_and_the_solder_pads():
    plain = build_bundle(CFG)
    marked = build_bundle(BundleConfig(**{**CFG.__dict__, "label": "C1,IONP01"}))
    poly = _layer(marked, "Polyimide")
    metal_entities = list(marked.msp.query('*[layer=="Metal"]'))
    n_plain = len(list(plain.msp.query('*[layer=="Metal"]')))
    #the label is stamped LAST, after every other Metal entity, so the entities added since
    #the unlabelled build are exactly the two stamps' rings, in the order they were written
    label_entities = metal_entities[n_plain:]
    label = [Polygon([(p[0], p[1]) for p in e.get_points()]).buffer(0) for e in label_entities]
    assert label, "the label added no geometry to compare"

    #the pad stamp is upright in the clear band ABOVE the last pad row: clear of every pad,
    #and inside the polyimide block it is written on
    pads = [Polygon([(cx + CFG.pad_diam / 2 * np.cos(a), cy + CFG.pad_diam / 2 * np.sin(a))
                     for a in np.linspace(0, 2 * np.pi, 32, endpoint=False)])
            for cx, cy in marked.pads_left + marked.pads_right]
    top_pad = max(cy for _, cy in marked.pads_left + marked.pads_right)
    stamp = [g for g in label if g.bounds[1] > top_pad + CFG.pad_diam / 2]
    assert stamp, "no marking landed above the solder-pad field"
    for g in stamp:
        assert not any(g.intersects(p) for p in pads)
        assert abs(g.bounds[0]) <= CFG.polyimide_width / 2, "the marking runs off the polyimide"
        assert abs(g.bounds[2]) <= CFG.polyimide_width / 2, "the marking runs off the polyimide"
    #and it reads the RIGHT WAY UP: wider than it is tall
    width = max(g.bounds[2] for g in stamp) - min(g.bounds[0] for g in stamp)
    height = max(g.bounds[3] for g in stamp) - min(g.bounds[1] for g in stamp)
    assert width > height, "the pad marking should be horizontal"

    #the other stamp sits beside the reference's bottom contact band, in the open wedge the
    #recording fibers leave clear as they fan out from the hook -- clear of every pad, and
    #clear of the polyimide (it is etched carrier out there, not the finished probe)
    ref_stamp = [g for g in label if g not in stamp]
    assert ref_stamp, "no marking landed beside the reference's bottom contact band"
    for g in ref_stamp:
        assert not any(g.intersects(p) for p in pads)
        assert not g.intersects(poly), "the ref-band marking overlaps the polyimide"


# --- the electrode openings and their coating ------------------------------------------

def test_every_site_opening_is_cut_by_pad_etching_and_not_by_the_release_mask(built):
    """The sites get their own mask: a shallower etch, down onto the gold, not through.

    Both halves are asserted. A site missing from `pad_etching` is left buried; a site still
    in `Etching` is cut by the release etch instead, at full depth -- which is the bug this
    split exists to fix, and the one a renderer cannot show, since both masks draw the same
    circle in the same place.

    `Electrodes` and `Ref_Electrodes` carry the circles as well, but they are build-only and
    dropped on save -- IONP and the mapping read contact centres back off them.
    """
    openings = _layer(built, "pad_etching")
    release = _layer(built, "Etching")
    sites = [("channel", xy) for xy in built.electrode_locs] + \
            [("reference contact", xy) for xy in built.ref_locs]
    assert len(sites) == built.num_channels + len(built.ref_locs)
    for what, (x, y) in sites:
        #a hair inside the 14 um circle: the locs are ring means, off-centre by r/n
        probe = Point(x, y).buffer(CFG.l_contact / 2 - 0.5)
        assert openings.contains(probe), \
            f"{what} at ({x:.1f}, {y:.1f}) is not opened by pad_etching"
        assert not release.intersects(probe), \
            f"{what} at ({x:.1f}, {y:.1f}) is still cut by the release mask"


def test_the_solder_pads_are_opened_by_the_release_mask(built):
    """The connector end is the exception: the whole stack over a solder pad comes away."""
    release = _layer(built, "Etching")
    opening_r = CFG.pad_diam / 2 - CFG.pad_opening_inset
    for cx, cy in built.pads_left + built.pads_right:
        assert release.contains(Point(cx, cy).buffer(opening_r - 0.5)), \
            f"the solder pad at ({cx:.0f}, {cy:.0f}) is not opened"


def test_the_coating_covers_the_opening_and_still_sits_on_gold(built):
    """PEDOT/SIROF is WIDER than the opening and NARROWER than the gold pad under it."""
    coat = [Polygon([(p[0], p[1]) for p in e.get_points()])
            for e in built.msp.query('*[layer=="PEDOT_SIROF"]')]
    assert len(coat) == built.num_channels
    gold = _layer(built, "Metal")
    for ch, (x, y) in enumerate(built.electrode_locs):
        here = [g for g in coat if g.intersects(Point(x, y))]
        assert len(here) == 1, f"channel {ch}: {len(here)} coating circles on its site"
        g = here[0]
        assert g.bounds[2] - g.bounds[0] == pytest.approx(CFG.l_pedot, abs=0.02)
        assert g.contains(Point(x, y).buffer(CFG.l_contact / 2 - 0.5)), \
            f"channel {ch}: the coating does not cover the whole opening"
        assert gold.contains(g), \
            f"channel {ch}: the coating reaches past the gold pad it should sit on"


# --- the solder-pad area ------------------------------------------------------------------

def _pad_area_start(result):
    """The drawing's datum: where the fanout stops flaring and the block's sides go parallel."""
    return result.pads_left[0][1] - CFG.polyimide_curve_offset


def test_the_solder_pad_block_is_the_drawn_width(built):
    """2.85 mm across, measured on the block itself -- not on the tabs standing out of it."""
    poly = _layer(built, "Polyimide")
    y0 = _pad_area_start(built)
    #a slice clear of both the ledge fillet below and the tabs above
    #clear of the LONGER tab, whichever side it is on -- the two lengths swap with the
    #reference column (see BundleConfig.pad_block_tab_len_*)
    _tab = max(CFG.pad_block_tab_len_left, CFG.pad_block_tab_len_right)
    band = poly.intersection(box(-9e3, y0 + 100, 9e3,
                                 y0 + CFG.pad_block_tab_top - _tab - 100))
    assert band.bounds[2] - band.bounds[0] == pytest.approx(CFG.polyimide_width, abs=1e-6)
    assert band.bounds[0] == pytest.approx(-CFG.polyimide_width / 2, abs=1e-6)


def test_a_tab_stands_out_of_each_side_hanging_from_the_drawn_height(built):
    """Two half-discs, both ENDING 3.5 mm above the pad-area start and arcing downward.

    They are different lengths -- 0.6 mm left, 0.3 mm right -- so the block is deliberately
    NOT symmetric here, which is the whole point of the feature: it keys the orientation.

    Taken as whatever polyimide lies OUTSIDE the block's own width, which is the tabs and
    nothing else, since every other part of the device is narrower than the block.
    """
    poly = _layer(built, "Polyimide")
    top = _pad_area_start(built) + CFG.pad_block_tab_top
    ylo, yhi = poly.bounds[1] - 1, poly.bounds[3] + 1
    outside = poly.difference(box(-CFG.polyimide_width / 2, ylo, CFG.polyimide_width / 2, yhi))
    tabs = sorted(getattr(outside, "geoms", [outside]), key=lambda g: g.centroid.x)
    assert len(tabs) == 2, f"expected one tab a side, found {len(tabs)}"
    for side, length, tab in zip((-1, 1),
                                 (CFG.pad_block_tab_len_left, CFG.pad_block_tab_len_right),
                                 tabs):
        xlo, tlo, xhi, thi = tab.bounds
        assert thi == pytest.approx(top, abs=1.0), "the tab does not end where it is dimensioned"
        assert thi - tlo == pytest.approx(length, abs=1.0)       # along the edge
        assert xhi - xlo == pytest.approx(length / 2, abs=1.0)   # half that, proud of it
        assert np.sign(tab.centroid.x) == side
        assert tab.area == pytest.approx(np.pi * (length / 2) ** 2 / 2, rel=0.01)  # half-disc


def test_the_two_tabs_are_different_so_the_block_keys_its_orientation(built):
    """2:1, and the BIG one on the side away from the reference.

    Which side is long is not decoration: the tabs are what says which way round a released
    probe is lying, and the thing an assembler reads off them is where the Ref pad is. So
    the ratio AND the side are both pinned, the side against `shapes.REF_PAD` rather than
    against the word "left" -- the pair has been mirrored once already, and a test naming a
    side outright would have passed on a probe keyed backwards.
    """
    poly = _layer(built, "Polyimide")
    ylo, yhi = poly.bounds[1] - 1, poly.bounds[3] + 1
    tabs = poly.difference(box(-CFG.polyimide_width / 2, ylo, CFG.polyimide_width / 2, yhi))
    left, right = sorted(tabs.geoms, key=lambda g: g.centroid.x)
    big, small = (right, left) if CFG.pad_block_tab_len_right > CFG.pad_block_tab_len_left \
        else (left, right)
    assert big.area == pytest.approx(4 * small.area, rel=0.02), \
        "the tabs are no longer 2:1, so the orientation key is weaker than it was drawn"
    ref_side = -1 if sh.REF_PAD[0] == 'left' else 1      # the pad columns straddle x = 0
    assert np.sign(big.centroid.x) == -ref_side, \
        "the big tab moved onto the reference's own side; it marks the edge AWAY from it"


def test_the_tabs_are_released_by_the_etch_mask(built):
    """They are polyimide like everything else -- the moat has to wrap them."""
    poly, neg = _layer(built, "Polyimide"), _layer(built, "Etching")
    top = _pad_area_start(built) + CFG.pad_block_tab_top
    for side, length in ((-1, CFG.pad_block_tab_len_left), (1, CFG.pad_block_tab_len_right)):
        edge, r = side * CFG.polyimide_width / 2, length / 2
        assert poly.contains(Point(edge + side * r / 2, top - r)), "the tab is not there"
        assert neg.contains(Point(edge + side * (r + 20), top - r)), \
            "the tab is not surrounded by etched negative"


def test_a_tab_longer_than_the_block_edge_is_refused():
    cfg = BundleConfig(num_channels=4, delta_y=200.0, delta_y_overrides=None,
                       pad_block_tab_len_right=9e4)
    with pytest.raises(AssertionError, match="does not fit on the block edge"):
        build_bundle(cfg)


def test_a_tab_with_no_length_is_refused():
    from electrode_bundle.shapes import pad_block_tab
    with pytest.raises(ValueError, match="positive length"):
        pad_block_tab(1425.0, 18000.0, 0.0)


# --- the shank -> block transition --------------------------------------------------------

def _right_profile(result, ylo, yhi):
    """The outer right-hand polyimide edge between two heights, bottom -> top."""
    ring = max((np.array([(p[0], p[1]) for p in e.get_points()])
                for e in result.msp.query('*[layer=="Polyimide"]')), key=len)
    band = ring[(ring[:, 1] >= ylo) & (ring[:, 1] <= yhi) & (ring[:, 0] > 0)]
    return band[np.argsort(band[:, 1])]


def test_the_shank_meets_the_block_without_a_shelf(built):
    """It used to be fillet-ledge-fillet, and the ledge was a literal horizontal shelf.

    475 um of offset per side against two 150 um fillets left 175 um of the profile running
    dead horizontal -- a right-angle in flex, and where a flex device tears. The S has no
    horizontal anywhere: it is vertical at both ends and only ever leans.
    """
    y1 = _pad_area_start(built)
    prof = _right_profile(built, y1 - CFG.polyimide_curve_height, y1)
    d = np.diff(prof, axis=0)
    d = d[np.hypot(d[:, 0], d[:, 1]) > 1e-9]
    off_vertical = np.abs(np.degrees(np.arctan2(d[:, 0], d[:, 1])))
    assert off_vertical.max() < 60, \
        f"the profile leans {off_vertical.max():.0f} deg off vertical somewhere -- that is a " \
        "shelf, not a transition"


def test_the_transition_is_tangent_continuous_end_to_end(built):
    """No corner anywhere on it, including where it lands on the block and leaves the stem."""
    y1 = _pad_area_start(built)
    prof = _right_profile(built, y1 - CFG.polyimide_curve_height - 20, y1 + 20)
    d = np.diff(prof, axis=0)
    d = d[np.hypot(d[:, 0], d[:, 1]) > 1e-9]
    ang = np.degrees(np.arctan2(d[:, 1], d[:, 0]))
    turn = np.abs((np.diff(ang) + 180) % 360 - 180)
    assert turn.max() < 5.0, f"a {turn.max():.1f} deg corner on the transition"


def test_the_transition_is_as_tall_as_the_ribbon_allows(built):
    """Configured height is an AIM: a short ribbon leaves less stem, and it clamps to that."""
    y1 = _pad_area_start(built)
    y_fan_top = max(y for _, y in built.top_points) + CFG.polyimide_fan_height
    want = min(CFG.polyimide_curve_height,
               y1 - y_fan_top - CFG.polyimide_curve_min_stem)
    prof = _right_profile(built, y_fan_top, y1)
    #the S is everything that is not the straight stem below it. The threshold is tight
    #because the S leaves the stem TANGENTIALLY -- its first samples are a fraction of a
    #micron out, and a loose one would clip the start of the curve and under-measure it.
    leaning = prof[np.abs(prof[:, 0] - CFG.polyimide_fan_hw) > 0.05]
    assert y1 - leaning[:, 1].min() == pytest.approx(want, abs=25)
    assert want > 0


def test_a_transition_taller_than_the_stem_is_refused():
    from electrode_bundle.shapes import build_polyimide_fanout_body
    with pytest.raises(ValueError, match="does not fit in the stem"):
        build_polyimide_fanout_body(100, 950, 1425, y_bottom=0, y_fan_top=2000,
                                    y_curve=3000, y_top=9000, curve_h=2500)
