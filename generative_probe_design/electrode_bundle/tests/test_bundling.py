"""Flat-wafer -> bundled contact positions (bundling.py)."""
import numpy as np
import pytest

from electrode_bundle.batch import config_for
from electrode_bundle.bundle import build_bundle
from electrode_bundle.bundling import (NoFold, TwoArcFold, bundled_positions, precompensate)
from electrode_bundle.design_sets import DESIGNS


def _c1_flat():
    r = build_bundle(config_for(DESIGNS[0]))
    return r.electrode_locs[:, 0], r.electrode_locs[:, 1]


def test_nofold_changes_nothing():
    x, y = _c1_flat()
    ml, dv = bundled_positions(x, y, NoFold())
    assert np.allclose(ml, x) and np.allclose(dv, y)


def test_bundling_moves_contacts_toward_the_shoulder():
    """Sign guard. y increases toward the shoulder here, and an inextensible fiber that
    spends length going sideways cannot reach as far DOWN -- so every contact must move to
    a LARGER y. Subtracting instead would silently deepen the whole array."""
    x, y = _c1_flat()
    _, dv = bundled_positions(x, y, TwoArcFold())
    assert np.all(dv >= y - 1e-9)
    assert dv.max() > y.max()          # the shallowest really does move


def test_centre_fiber_pays_almost_nothing_and_outermost_pays_most():
    x, _ = _c1_flat()
    off = TwoArcFold().offsets(x)
    assert off[np.argmin(np.abs(x))] < 1.0        # centre fiber barely turns
    assert off[np.argmax(np.abs(x))] == off.max()  # outermost turns hardest
    #monotone in |x|, which is what makes the distortion a smooth stretch
    order = np.argsort(np.abs(x))
    assert np.all(np.diff(off[order]) >= -1e-9)


def test_offset_grows_faster_than_linearly():
    """Quadratic, not linear: doubling the lateral offset costs well more than double."""
    fold = TwoArcFold()
    x = np.array([0.0, 200.0, 400.0, 800.0])
    off = fold.offsets(x)
    assert off[3] > 3.0 * off[1]       # 4x the offset costs >3x, not 2x


def test_the_array_stretches_toward_the_shallow_end():
    """The structural claim: index grows outward in x AND upward in y, so the shallowest
    contacts pay most and the span gets LONGER, not shorter."""
    x, y = _c1_flat()
    _, dv = bundled_positions(x, y, TwoArcFold())
    assert dv.max() - dv.min() > y.max() - y.min()


def test_offset_is_independent_of_y():
    """What makes precompensation a single pass rather than a solve."""
    x, y = _c1_flat()
    fold = TwoArcFold()
    assert np.allclose(fold.offsets(x), fold.offsets(x))
    shifted = fold.offsets(x)          # same x, y irrelevant by construction
    _, dv_a = bundled_positions(x, y, fold)
    _, dv_b = bundled_positions(x, y + 1000.0, fold)
    assert np.allclose(dv_b - dv_a, 1000.0)
    assert np.allclose(shifted, dv_a - y)


def test_precompensate_lands_on_the_target():
    x, y_target = _c1_flat()
    fold = TwoArcFold()
    drawn = precompensate(x, y_target, fold)
    _, landed = bundled_positions(x, drawn, fold)
    assert np.allclose(landed, y_target, atol=1e-9)
    #and it really is drawn deeper than the target, by exactly what bundling will lift
    assert np.all(drawn <= y_target + 1e-9)


def test_fillets_that_cannot_fit_raise():
    with pytest.raises(ValueError, match="fillets"):
        TwoArcFold(bend_r1_um=50_000, bend_r2_um=50_000).offsets(np.array([0.0, 100.0]))


def test_record_carries_both_wafer_and_bundled_positions():
    """The JSON's whole job: where a channel is on the wafer AND after folding."""
    from electrode_bundle.probe_json import build_record
    from electrode_bundle.design_sets import BUNDLE_FOLD

    spec = DESIGNS[0]
    cfg = config_for(spec)
    result = build_bundle(cfg)
    rec = build_record(spec, cfg, result, BUNDLE_FOLD)

    assert len(rec["channels"]) == rec["n_channels"] == 64
    ch = rec["channels"]
    #both positions present and, for a real fold, actually different
    assert all({"wafer", "bundled"} <= set(c) for c in ch)
    assert ch[63]["bundled"]["y"] != ch[63]["wafer"]["y"]
    #the centre fiber barely moves, the outermost moves most
    assert abs(ch[0]["bundled"]["y"] - ch[0]["wafer"]["y"]) < 1.0
    assert ch[63]["bundled"]["y"] > ch[63]["wafer"]["y"]
    #spans disagree, which is the point of recording both
    assert rec["lengths"]["site_span_bundled"] > rec["lengths"]["site_span_wafer"]


def test_record_states_the_model_and_that_it_is_uncalibrated():
    """Bundled numbers are only as good as max_theta_deg, so the file must say what it used."""
    from electrode_bundle.probe_json import build_record
    from electrode_bundle.design_sets import BUNDLE_FOLD

    spec = DESIGNS[0]
    cfg = config_for(spec)
    rec = build_record(spec, cfg, build_bundle(cfg), BUNDLE_FOLD)
    assert rec["bundling"]["model"] == "TwoArcFold"
    assert rec["bundling"]["params"]["max_theta_deg"] == BUNDLE_FOLD.max_theta_deg
    assert rec["bundling"]["calibrated"] is False


def test_depth_from_loop_is_measured_from_the_recorded_loop():
    from electrode_bundle.probe_json import build_record
    from electrode_bundle.design_sets import BUNDLE_FOLD

    spec = DESIGNS[0]
    cfg = config_for(spec)
    rec = build_record(spec, cfg, build_bundle(cfg), BUNDLE_FOLD)
    for c in rec["channels"]:
        #each of the three values is rounded to 3 dp independently, so the difference of
        #two of them can sit up to 1.5 ulp from the rounded difference
        assert c["bundled"]["depth_from_loop_um"] == pytest.approx(
            c["bundled"]["y"] - rec["loop_y_um"], abs=2e-3)


#--- the pipeline: sites at spec, fold cost absorbed by the staggered shoulder ----------
#The extra fiber each channel needs is added at the TOP (it is freed from the polyimide
#block higher up), so the drawn contacts must come out exactly where `sites` says.

#building a design costs ~4 bundle builds (solve_lengths iterates), and these tests are
#parametrised over every design, so cache per design rather than rebuilding 4x per assert
_BUILT = {}


def _built(spec):
    """(spec positions, drawn contact y, per-channel shoulder y, fold cost per channel)."""
    from electrode_bundle.batch import _channel_x, _target_positions
    from electrode_bundle.design_sets import BUNDLE_FOLD
    if spec.name not in _BUILT:
        cfg = config_for(spec)
        result = build_bundle(cfg)
        #the fold cost is set by the FIBER's lateral position, not the contact centroid --
        #those differ by ~0.1 um now that the contact is round
        x = _channel_x(cfg, spec.n_channels)
        tops = np.array([p[1] for p in result.top_points], dtype=float)
        _BUILT[spec.name] = (_target_positions(spec), result.electrode_locs[:, 1], tops,
                             BUNDLE_FOLD.offsets(x))
    return _BUILT[spec.name]


@pytest.mark.parametrize("spec", DESIGNS, ids=lambda s: s.name)
def test_contacts_are_drawn_exactly_where_the_spec_says(spec):
    """`sites` states drawn positions. Nothing may shift them -- the whole point of taking
    the fold cost at the shoulder is that the site array stays untouched."""
    target, drawn, _, _ = _built(spec)
    assert drawn == pytest.approx(target, abs=0.05)


@pytest.mark.parametrize("spec", DESIGNS, ids=lambda s: s.name)
def test_each_fiber_is_lengthened_by_exactly_its_fold_cost(spec):
    """The stagger IS the elongation: channel i is freed `offset(x_i)` above the lowest
    shoulder, so its free fiber is that much longer than a flat-shoulder cut would give."""
    _, drawn, tops, rise = _built(spec)
    gained = (tops - drawn) - (tops.min() - drawn)     # vs a flat cut at the lowest shoulder
    assert gained == pytest.approx(rise - rise.min(), abs=0.05)


@pytest.mark.parametrize("spec", DESIGNS, ids=lambda s: s.name)
def test_the_shoulder_is_staggered_not_flat(spec):
    """Guards the regression that started this: a flat block edge means no elongation."""
    _, _, tops, rise = _built(spec)
    assert np.ptp(tops) == pytest.approx(np.ptp(rise), abs=0.05)
    assert np.ptp(tops) > 100.0                       # ~282 um on a 64-ch probe
    #centre fiber freed lowest, outermost highest -- the curve follows the fold cost
    assert tops[np.argmin(rise)] == pytest.approx(tops.min(), abs=0.05)
    assert tops[np.argmax(rise)] == pytest.approx(tops.max(), abs=0.05)


@pytest.mark.parametrize("spec", DESIGNS, ids=lambda s: s.name)
def test_the_stagger_does_not_disturb_the_solved_lengths(spec):
    """`solve_lengths` must run against the STAGGERED build, or fiber_length drifts by the
    full stagger (~282 um) -- exactly what regressed when the two ran in the wrong order.
    fiber_length is measured loop -> block bottom, i.e. the top of the stagger."""
    from electrode_bundle.lengths import measure
    result = build_bundle(config_for(spec))
    _, fiber, loop_offset = measure(result)
    assert fiber == pytest.approx(spec.fiber_length, abs=0.05)
    assert loop_offset == pytest.approx(spec.loop_offset, abs=0.05)


def test_uniform_sites_are_uniform_as_drawn():
    spec = next(s for s in DESIGNS if hasattr(s.sites, "span"))
    _, drawn, _, _ = _built(spec)
    assert np.ptp(np.diff(drawn)) < 0.05
