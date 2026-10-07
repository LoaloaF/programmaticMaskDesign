r"""The bank of IONP barcode patterns, indexed by pattern id.

A pattern is a plain tuple of `(start, end)` bands in coordinates normalized along the
ELECTRODE ARRAY -- `0.0` = the tip (the deepest contact, ch0), `1.0` = `id_top_margin`
(220 um) above the shallowest contact (ch63). It is NOT normalized along the whole shank:
the barcode covers the recording sites and stops well short of the fanout.

FIXED BANDS, NOT GROWN
======================
These numbers are frozen data. They do not depend on the design being built, which is the
whole point: a pattern id means the same normalized barcode everywhere it is stamped.

The consequence is that a pattern is NOT guaranteed to fit every probe, and on today's set
most of them do not. A band's absolute height is `norm_height * y_range`, so a shorter
electrode array shrinks every stripe and every gap between stripes. Short arrays therefore
fail the design rules, and that failure is deliberate and loud -- `ionp.check_design_rules`
raises rather than quietly stamping an undersized barcode.

    y_range = id_top_margin above ch63  -  the tip           # 1.86-8.09 mm today
    a stripe holds        >= min_n_wells (700)               # the MRI floor
    stripes stay          >= min_strip_distance (300 um) apart

HOW THESE NUMBERS WERE CHOSEN
=============================
Two always-on anchors bracket 7 data slots on a SHARED grid, and a pattern is a subset of
those slots. What changed from the previous bank is that a pattern no longer has to use a
FIXED number of slots: the four shipped designs span 4.4x in array length (1858 um to 8093
um), and no single slot count serves that range.

    - the shortest design, U1.6, holds FOUR stripes and no more. At 300 um between
      stripes and 700 wells in each, greedy packing (which is optimal for stripe count) gets
      4 in 1858 um. Two are anchors, so it has room for two data slots and no more;
    - its four ids are therefore ONE-HOT -- exactly one data slot lit, out of slots 1..4.
      One-hot beats binary at identical physical cost: 4 ids one-hot differ from each other
      in 2 stripes, where 4 ids binary-coded on two slots differ in only 1. A single misread
      stripe is detected on every shipped design;
    - the grid pitch is as fine as the LONGEST design tolerates, so U8 can light adjacent
      slots that U1.6 cannot. That is what makes one grid serve all four;
    - band HEIGHTS are the worst case over the four shipped designs: each is the smallest
      height holding 800 wells on whichever design makes that slot hardest. Heights shrink
      going up the array because more contact columns are live up there -- wells sit only
      ABOVE their own contact, so near the tip most columns have not started yet, which is
      also why the tip anchor is so much fatter than any data slot;
    - 800, not the 700 floor, because WELL COUNT IS QUANTIZED. Each column steps in 5 um, so
      one step across 64 columns is ~64 wells and a band edge moving a fraction of a micron
      can take a whole step with it. A band sized to land on exactly 700 is unstable;
    - the grid gap is 1.15x `min_strip_distance` for the same reason.

The worst case over all 16 ids on their own designs is +9.2% on stripe spacing and +14.1%
on well count. That is the whole margin: this bank is close to the edge, and it is the
U1.6 design that puts it there. `ionp_bank_solve.py` re-derives the table.

THE FIT IS NOT SYMMETRIC
========================
`DESIGN_IDS` says which ids belong to which design, and `design_sets.DESIGNS` loops over it
so every shipped probe gets four barcode variants. An id is legal on its own design and on
every LONGER one, never on a shorter one -- ids 12-15 (U8) fit nothing else, ids 0-3
(U1.6) fit everything. `python3 -m electrode_bundle.ionp_patterns` prints the matrix.

Decoding does not need to be told which design it is looking at: the tip and top anchors
bracket the array, and their separation (1858 / 2739 / 4157 / 8093 um) identifies the design
on its own. The lit slot(s) between them then give the id within that design.

ADDING A PATTERN
================
Ids 0-15 are the shipped set. To add one, give it an id >= 16, a band list obeying
`validate_pattern`, and an entry in `DESIGN_IDS` for the design it is meant for. Check it
with the fit table before committing -- editing a band by hand is a fabrication-affecting
change.
"""
from typing import Dict, Tuple

import numpy as np

Band = Tuple[float, float]
Pattern = Tuple[Band, ...]

#The 7 data slots on the shared grid. A pattern lights a subset; never moved.
SLOT_BANDS: Tuple[Band, ...] = (
    (0.2864, 0.3776),
    (0.4202, 0.4872),
    (0.5298, 0.5843),
    (0.6269, 0.6737),
    (0.7163, 0.7577),
    (0.8003, 0.8375),
    (0.8801, 0.9142),
)
#Always present, in every pattern. They bracket the array, so their separation tells the
#reader which design this is; the tip anchor is also the fattest stripe = orientation cue.
TIP_ANCHOR_BAND: Band = (0.0000, 0.2438)
TOP_ANCHOR_BAND: Band = (0.9643, 0.9980)

IONP_PATTERNS: Dict[int, Pattern] = {
     0: ((0.0000, 0.2438), (0.4202, 0.4872), (0.9103, 0.9980)),
     1: ((0.0000, 0.2438), (0.5298, 0.5843), (0.9643, 0.9980)),
     2: ((0.0000, 0.2438), (0.6269, 0.6737), (0.9203, 0.9980)),
     3: ((0.0000, 0.2438), (0.7463, 0.7877), (0.9643, 0.9980)),
     4: ((0.0000, 0.2438), (0.8003, 0.8375), (0.9543, 0.9980)),
     5: ((0.0000, 0.2438), (0.4202, 0.4872),   (0.9543, 0.9980)),
     6: ((0.0000, 0.2438), (0.5298-.02, 0.5843-.02), (0.7163-.02, 0.7577-.02), (0.9643, 0.9980)),
     7: ((0.0000, 0.2438), (0.4202, 0.4872), (0.6269, 0.6737), (0.8003, 0.8375),
         (0.9643, 0.9980)),
     8: ((0.0000, 0.2438), (0.4298, 0.4633), (0.9543, 0.9980)),
     9: ((0.0000, 0.2438), (0.4202, 0.4872), (0.6269, 0.6737), (0.9643, 0.9980)),
    10: ((0.0000, 0.2438), (0.4202-.07, 0.4872-.07), (0.8003, 0.8375), (0.9643, 0.9980)),
    11: ((0.0000, 0.2438), (0.5298, 0.5843), (0.8003+.03, 0.8375+.05), (0.9643, 0.9980)),
    12: ((0.0000, 0.2438), (0.2864, 0.3776),   (0.6864, 0.7276), (0.9643, 0.9980)),
    13: ((0.0000, 0.2438),   (0.4864, 0.516), (0.8801, 0.9142), (0.9643, 0.9980)),
    14: ((0.0000, 0.2438), (0.4202-.05, 0.4872-.05), (0.5298-.05, 0.5843-.05), (0.9643, 0.9980)),
    15: ((0.0000, 0.2438), (0.5298+.05, 0.5843+.05), (0.6269+.05, 0.6737+.05), (0.9643, 0.9980)),
}

#Which ids belong to which design, by DesignSpec.name. Four per design, so every probe
#ships in four distinguishable barcode variants; `design_sets.DESIGNS` expands this.
#An id is listed against the SHORTEST design it fits -- that is the design it was solved
#for, and the one whose limits it is sized against.
DESIGN_IDS: Dict[str, Tuple[int, ...]] = {
    "U1.6": (0, 1, 2, 3),
    "U2.5": (4, 5, 6, 7),
    "U4":   (8, 9, 10, 11),
    "U8":   (12, 13, 14, 15),
}


class PatternError(ValueError):
    """A pattern id that is not in the bank, or a band list that is not a legal pattern."""


def validate_pattern(pattern, what: str = "pattern") -> np.ndarray:
    """Check a band list is a legal pattern and return it as an Nx2 float array.

    Legal means: at least one band, every band inside [0, 1] with start < end, and bands
    strictly ascending and non-overlapping. Bands outside [0, 1] used to be silently
    trimmed at build time, which made `check_design_rules` and the info JSON describe
    geometry that was not what got fabricated -- so they are rejected here instead.
    """
    arr = np.asarray(pattern, dtype=float)
    if arr.ndim != 2 or arr.shape[0] < 1 or arr.shape[1] != 2:
        raise PatternError(f"{what}: expected an Nx2 array of (start, end) bands, got {arr.shape}")
    if not np.all(arr[:, 0] < arr[:, 1]):
        raise PatternError(f"{what}: every band needs start < end, got {arr.tolist()}")
    if arr.min() < 0.0 or arr.max() > 1.0:
        raise PatternError(
            f"{what}: bands must lie inside the normalized array range [0, 1]; got "
            f"[{arr.min():.4f}, {arr.max():.4f}]. 1.0 is `id_top_margin` above the "
            f"shallowest contact -- there are no wells above it to fill a band with.")
    if arr.shape[0] > 1 and not np.all(arr[1:, 0] > arr[:-1, 1]):
        raise PatternError(f"{what}: bands must be sorted and non-overlapping, got {arr.tolist()}")
    return arr


def pattern_bands(pattern_id: int) -> np.ndarray:
    """The bank's pattern for `pattern_id`, as an Nx2 array of normalized bands."""
    if pattern_id not in IONP_PATTERNS:
        raise PatternError(
            f"no IONP pattern with id {pattern_id}; the bank has "
            f"{sorted(IONP_PATTERNS)}. Add one to IONP_PATTERNS in ionp_patterns.py "
            f"(use an id >= 16 for a custom pattern).")
    return validate_pattern(IONP_PATTERNS[pattern_id], what=f"pattern {pattern_id}")


def ids_for_design(name: str) -> Tuple[int, ...]:
    """The barcode ids this design ships with. `design_sets.DESIGNS` loops over these.

    Keyed by `DesignSpec.name`, so renaming a design breaks here rather than silently
    shipping it without a barcode -- which id a probe carries is device-identification
    data, not something to default.
    """
    if name not in DESIGN_IDS:
        raise PatternError(
            f"design {name!r} has no IONP ids; DESIGN_IDS in ionp_patterns.py covers "
            f"{sorted(DESIGN_IDS)}. Pick ids that fit its electrode array (see "
            f"`python3 -m electrode_bundle.ionp_patterns`) and add an entry, or drop the "
            f"design from the expansion loop in design_sets.py.")
    return DESIGN_IDS[name]


def min_gap_normalized(pattern_id: int) -> float:
    """The tightest normalized gap between consecutive bands of this pattern."""
    b = pattern_bands(pattern_id)
    if len(b) < 2:
        return float("inf")
    return float(np.min(b[1:, 0] - b[:-1, 1]))


def min_viable_y_range_um(pattern_id: int, min_strip_distance: float) -> float:
    """Shortest electrode-array y-range (tip -> `id_top_margin` above ch63) this pattern
    fits on, from the stripe-spacing rule alone.

    Gaps scale linearly with the array, so this is exact for spacing. The well floor is a
    separate check (`ionp.check_design_rules`), which is why this is a lower bound and not
    a guarantee -- but on today's geometry spacing is what binds first.
    """
    return min_strip_distance / min_gap_normalized(pattern_id)


def _fit_report():
    """Print the pattern x design fit matrix. Fabrication sanity check."""
    from .batch import config_for
    from .bundle import build_bundle
    from .config import IonpConfig
    from .design_sets import DESIGNS
    from .ionp import check_design_rules, effective_y_top, well_locs_for_pattern

    cfg = IonpConfig()
    specs = list({s.name: s for s in DESIGNS}.values())
    els = {s.name: build_bundle(config_for(s)).electrode_locs for s in specs}

    print(f"{'design':8} {'y_range':>8}  ids it ships with")
    for s in specs:
        yr = effective_y_top(els[s.name], cfg) - cfg.y_bottom
        print(f"{s.name:8} {yr:8.0f}  {list(DESIGN_IDS.get(s.name, ()))}")

    print(f"\n{'id':>3} {'home':8} " + " ".join(f"{s.name:>10}" for s in specs)
          + f"   {'min gap':>8} {'min wells':>10}")
    home = {i: n for n, ids in DESIGN_IDS.items() for i in ids}
    for pid in sorted(IONP_PATTERNS):
        pat = pattern_bands(pid)
        cells, gaps, wells = [], [], []
        for s in specs:
            el = els[s.name]
            _, ys = well_locs_for_pattern(el, pat, cfg)
            r = check_design_rules(pat, effective_y_top(el, cfg), cfg.y_bottom, ys, cfg,
                                   strict=False, verbose=False)
            cells.append(f"{'ok' if r['ok'] else '--':>10}")
            if s.name == home.get(pid):
                gaps.append(r["min_gap_um"])
                wells.append(r["min_wells"])
        print(f"{pid:3d} {home.get(pid, '?'):8} " + " ".join(cells)
              + f"   {min(gaps):8.0f} {min(wells):10d}")
    print("\n'ok' off the home design means the id would also be legal there; it is still "
          "not stamped there -- DESIGN_IDS decides that.")


if __name__ == "__main__":
    _fit_report()
