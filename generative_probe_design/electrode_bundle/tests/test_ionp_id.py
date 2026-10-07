import numpy as np
import pytest

from electrode_bundle.config import IonpConfig, BundleConfig, variant_tag
from electrode_bundle.bundle import build_bundle
from electrode_bundle.design_sets import DESIGNS
from electrode_bundle.batch import config_for
from electrode_bundle.ionp import (
    DesignRuleError, check_design_rules, drop_wells_near_contacts,
    calc_ionp_well_locs, effective_y_top, resolve_pattern, well_locs_for_pattern,
)
from electrode_bundle.ionp_patterns import (
    DESIGN_IDS, IONP_PATTERNS, PatternError, SLOT_BANDS, TIP_ANCHOR_BAND,
    TOP_ANCHOR_BAND, ids_for_design, min_viable_y_range_um, pattern_bands,
    validate_pattern,
)


@pytest.fixture(scope="module")
def bundle():
    return build_bundle(BundleConfig())


#Four geometries carry sixteen barcode variants, so building per test case would build the
#same four probes four times each. The build is pure, so cache it.
_EL_CACHE = {}


def _electrode_locs(spec):
    if spec.name not in _EL_CACHE:
        _EL_CACHE[spec.name] = build_bundle(config_for(spec)).electrode_locs
    return _EL_CACHE[spec.name]


def _band_counts(el_locs, cfg, pattern):
    """Real well count inside each band of `pattern`, after the contact keep-out."""
    y_top, yb = effective_y_top(el_locs, cfg), cfg.y_bottom
    yr = y_top - yb
    xs, ys = calc_ionp_well_locs(el_locs, pattern, y_top, yb,
                                 cfg.ionp_well_distance, cfg.ionp_firstwell_distance)
    xs, ys = drop_wells_near_contacts(xs, ys, el_locs, cfg.keepout_radius)
    return [int(np.sum((ys >= s * yr + yb) & (ys <= e * yr + yb))) for s, e in pattern]


# --- pattern structure (frozen bank, no geometry) ---

def test_every_design_ships_four_ids_two_stripes_apart():
    # the headline guarantee, and the one the short designs only just meet: the four ids a
    # design carries differ from each other in >= 2 stripes, so one misread stripe cannot
    # turn one of its ids into another. It is only 2 (not the old Hamming 3) because
    # U1.6 holds four stripes in total -- see the ionp_patterns docstring.
    for name, ids in DESIGN_IDS.items():
        assert len(ids) == 4, name
        lit = [frozenset(map(tuple, pattern_bands(i)[1:-1])) for i in ids]
        for a in range(4):
            for b in range(a + 1, 4):
                d = len(lit[a] ^ lit[b])
                assert d >= 2, f"{name}: ids {ids[a]} and {ids[b]} differ in {d} stripes"


def test_every_id_belongs_to_exactly_one_design():
    owned = [i for ids in DESIGN_IDS.values() for i in ids]
    assert sorted(owned) == sorted(IONP_PATTERNS), "DESIGN_IDS and the bank disagree"
    assert len(owned) == len(set(owned)), "an id is claimed by two designs"


def test_ids_for_design_raises_on_an_unknown_name():
    with pytest.raises(PatternError):
        ids_for_design("no-such-design")


def test_data_slots_come_from_the_shared_grid():
    # a pattern is a SUBSET of SLOT_BANDS; anything else is a typo in the table
    for i in IONP_PATTERNS:
        for band in pattern_bands(i)[1:-1]:
            assert tuple(band) in SLOT_BANDS, f"id {i}: {tuple(band)} is not a grid slot"


def test_both_anchors_present_in_all_16():
    for i in range(16):
        p = pattern_bands(i)
        assert tuple(p[0]) == TIP_ANCHOR_BAND
        assert tuple(p[-1]) == TOP_ANCHOR_BAND


def test_tip_anchor_is_the_fattest_stripe():
    # it is the orientation cue: an MRI reader finds which end is the tip by it
    for i in range(16):
        heights = [e - s for s, e in pattern_bands(i)]
        assert heights[0] == max(heights) and heights.count(max(heights)) == 1


def test_patterns_sorted_ascending_by_start():
    for i in IONP_PATTERNS:
        assert np.all(np.diff(pattern_bands(i)[:, 0]) > 0)


def test_all_16_patterns_distinct():
    assert len({tuple(map(tuple, pattern_bands(i))) for i in range(16)}) == 16


def test_no_pattern_packs_tighter_than_the_grid():
    # the grid's own pitch is the tightest anything may be; a pattern that skips slots has
    # LARGER gaps, never smaller. Whether a given gap is wide enough in MICRONS depends on
    # the design -- that is `check_design_rules`, below.
    grid_gap = min(SLOT_BANDS[i + 1][0] - SLOT_BANDS[i][1]
                   for i in range(len(SLOT_BANDS) - 1))
    for i in IONP_PATTERNS:
        b = pattern_bands(i)
        if len(b) > 1:
            assert (b[1:, 0] - b[:-1, 1]).min() >= grid_gap - 1e-9


# --- the bank rejects illegal patterns ---

def test_unknown_pattern_id_raises():
    with pytest.raises(PatternError):
        pattern_bands(99)


def test_bands_above_one_rejected():
    # used to be silently trimmed at build time, so the report described geometry that was
    # not what got fabricated
    with pytest.raises(PatternError):
        validate_pattern([[0.0, 0.1], [0.95, 1.05]])


def test_overlapping_bands_rejected():
    with pytest.raises(PatternError):
        validate_pattern([[0.0, 0.5], [0.4, 0.6]])


def test_inverted_band_rejected():
    with pytest.raises(PatternError):
        validate_pattern([[0.5, 0.2]])


def test_resolve_pattern_prefers_explicit_over_bank():
    custom = np.array([[0.1, 0.2], [0.9, 0.95]])
    assert np.array_equal(resolve_pattern(IonpConfig(pattern_i=3, pattern=custom)), custom)
    assert np.array_equal(resolve_pattern(IonpConfig(pattern_i=3)), pattern_bands(3))


# --- the MRI guarantee: every stripe of every ID clears the 700-well floor ---

@pytest.mark.parametrize("spec", DESIGNS,
                         ids=[variant_tag(s.name, s.ionp_pattern_id) for s in DESIGNS])
def test_every_design_fits_the_id_it_ships(spec):
    # the fabrication guarantee. NOT "every pattern fits every design" any more -- the four
    # designs span 4.4x in array length, so an id is promised only on the design it was
    # solved for (and on longer ones). If a future design is too short for its ids, or the
    # geometry moves under the bank, this is where it says so.
    el = _electrode_locs(spec)
    cfg = IonpConfig()
    pat = pattern_bands(spec.ionp_pattern_id)
    _, ys = well_locs_for_pattern(el, pat, cfg)
    check_design_rules(pat, effective_y_top(el, cfg), cfg.y_bottom, ys, cfg, verbose=False)


@pytest.mark.parametrize("spec", DESIGNS,
                         ids=[variant_tag(s.name, s.ionp_pattern_id) for s in DESIGNS])
def test_well_counts_clear_the_floor_with_margin(spec):
    # wells are quantized in steps of ~64 (one 5um step across all 64 columns), so a stripe
    # sitting a handful of wells above 700 is one rounding away from failing. The bank is
    # sized for 800 wells for exactly this reason.
    el = _electrode_locs(spec)
    cfg = IonpConfig()
    worst = min(_band_counts(el, cfg, pattern_bands(spec.ionp_pattern_id)))
    assert worst >= cfg.min_n_wells + 64, f"only {worst - cfg.min_n_wells} wells of margin"


def test_shortest_design_is_the_one_that_binds():
    # U1.6 is at capacity: four stripes is all its array holds, so its ids light exactly
    # one data slot each. If that stops being true the bank was re-solved against something
    # else, and the margins in its docstring no longer describe what ships.
    for i in DESIGN_IDS["U1.6"]:
        assert len(pattern_bands(i)) == 3, f"id {i} is no longer tip + one slot + top"


def test_check_design_rules_raises_on_a_too_short_array(bundle):
    # a shrunk array scales every band and gap down with it
    el = bundle.electrode_locs.copy()
    el[:, 1] *= 0.25
    cfg = IonpConfig()
    pat = pattern_bands(15)
    _, ys = well_locs_for_pattern(el, pat, cfg)
    with pytest.raises(DesignRuleError):
        check_design_rules(pat, effective_y_top(el, cfg), cfg.y_bottom, ys, cfg, verbose=False)


def test_check_design_rules_strict_false_reports_instead(bundle):
    el = bundle.electrode_locs.copy()
    el[:, 1] *= 0.25
    cfg = IonpConfig()
    pat = pattern_bands(15)
    _, ys = well_locs_for_pattern(el, pat, cfg)
    report = check_design_rules(pat, effective_y_top(el, cfg), cfg.y_bottom, ys, cfg,
                                strict=False, verbose=False)
    assert report["ok"] is False and report["problems"]


@pytest.mark.parametrize("spec", DESIGNS,
                         ids=[variant_tag(s.name, s.ionp_pattern_id) for s in DESIGNS])
def test_min_viable_y_range_is_below_the_design_that_ships_it(spec):
    cfg = IonpConfig()
    need = min_viable_y_range_um(spec.ionp_pattern_id, cfg.min_strip_distance)
    el = _electrode_locs(spec)
    assert effective_y_top(el, cfg) - cfg.y_bottom > need


# --- config ---

def test_config_min_n_wells_is_700():
    assert IonpConfig().min_n_wells == 700


def test_config_default_pattern_is_none():
    # None -> look pattern_i up in the bank; an explicit array overrides
    assert IonpConfig().pattern is None


def test_config_explicit_pattern_preserved():
    custom = np.array([[0.1, 0.2], [0.9, 1.0]])
    cfg = IonpConfig(pattern_i=3, pattern=custom)
    assert np.array_equal(cfg.pattern, custom)


def test_config_keepout_radius_default():
    assert IonpConfig().keepout_radius == 30.0


def test_config_id_top_margin_default():
    assert IonpConfig().id_top_margin == 220.0


# --- keep-out filter ---

def test_keepout_removes_wells_within_radius():
    xs = np.array([0.0, 0.0, 100.0])
    ys = np.array([10.0, 40.0, 40.0])          # first well 10um from a contact at (0,0)
    contacts = np.array([[0.0, 0.0]])
    fx, fy = drop_wells_near_contacts(xs, ys, contacts, radius=30.0)
    assert set(zip(fx.tolist(), fy.tolist())) == {(0.0, 40.0), (100.0, 40.0)}


def test_keepout_does_not_reach_neighbouring_fibers():
    # regression: keepout_radius (30) exceeds delta_x (24), so a Euclidean circle around a
    # contact also cleared its NEIGHBOURS' columns, leaving gaps in the stripes on fibers
    # that carry no contact there. The keep-out is along the fiber, not across fibers.
    contacts = np.array([[0.0, 1000.0], [24.0, 500.0]])
    xs = np.array([24.0, 24.0, 0.0, 0.0])
    ys = np.array([1000.0, 1010.0, 1010.0, 1100.0])
    fx, fy = drop_wells_near_contacts(xs, ys, contacts, radius=30.0)
    kept = set(zip(fx.tolist(), fy.tolist()))
    # the two wells on the x=24 fiber are within 30um of the x=0 contact as the crow flies,
    # but they are on the other fiber, so they stay
    assert (24.0, 1000.0) in kept and (24.0, 1010.0) in kept
    # on its own fiber the contact still clears its neighbourhood
    assert (0.0, 1010.0) not in kept
    assert (0.0, 1100.0) in kept


def test_keepout_radius_zero_is_noop():
    xs = np.array([0.0, 5.0])
    ys = np.array([0.0, 0.0])
    contacts = np.array([[0.0, 0.0]])
    fx, fy = drop_wells_near_contacts(xs, ys, contacts, radius=0.0)
    assert len(fx) == 2 and len(fy) == 2


# --- effective top (id_top_margin) ---

def test_effective_y_top_uses_highest_electrode_plus_margin():
    el_locs = np.array([[0.0, 100.0], [10.0, 1968.0], [20.0, 500.0]])  # max y = 1968
    cfg = IonpConfig(id_top_margin=220.0)
    assert effective_y_top(el_locs, cfg) == 1968.0 + 220.0


def test_effective_y_top_none_falls_back_to_y_top():
    el_locs = np.array([[0.0, 100.0], [10.0, 1968.0]])
    cfg = IonpConfig(id_top_margin=None)
    assert effective_y_top(el_locs, cfg) == cfg.y_top


# --- legend ---

def test_legend_renders_16_panels_and_writes_png(tmp_path):
    import matplotlib
    matplotlib.use("Agg")
    from electrode_bundle.id_legend import render_id_legend
    out = tmp_path / "legend.png"
    fig = render_id_legend(str(out))
    assert len(fig.axes) == len(IONP_PATTERNS)
    assert out.exists() and out.stat().st_size > 0


# --- per-ID output filenames + CLI ---

def test_config_filenames_encode_pattern_i():
    cfg = IonpConfig(pattern_i=7)
    #`C07`, the same code `variant_tag` and the stamped label use -- see config.variant_tag
    assert "C07" in cfg.dxf_file
    assert "C07" in cfg.json_file
    assert IonpConfig(pattern_i=3).dxf_file != IonpConfig(pattern_i=4).dxf_file
    assert IonpConfig(pattern_i=3).json_file != IonpConfig(pattern_i=4).json_file


def test_config_explicit_filenames_preserved():
    cfg = IonpConfig(pattern_i=9, dxf_file="/tmp/custom.dxf", json_file="/tmp/custom.json")
    assert cfg.dxf_file == "/tmp/custom.dxf"
    assert cfg.json_file == "/tmp/custom.json"


def test_cli_accepts_pattern_i():
    from electrode_bundle.main import _build_parser
    args = _build_parser().parse_args(["all", "--pattern-i", "11"])
    assert args.pattern_i == 11


def test_cli_rejects_out_of_range_pattern_i():
    from electrode_bundle.main import _build_parser
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["all", "--pattern-i", "16"])
