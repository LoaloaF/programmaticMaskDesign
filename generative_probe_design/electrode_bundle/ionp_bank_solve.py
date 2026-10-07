r"""Re-derive the frozen band table in `ionp_patterns.py`. Prints Python, writes nothing.

    python3 -m electrode_bundle.ionp_bank_solve      # paste the output into ionp_patterns.py

Run this when the geometry moves materially -- electrode pitch, keep-out, well spacing,
`id_top_margin`, or a design joining or leaving `DESIGNS`. The bank is worst-cased over the
designs in `DESIGNS`, so adding a shorter probe than U1.6 invalidates it.

WHAT IT SOLVES
==============
Four numbers, in this order:

  1. band HEIGHTS, per position: the smallest normalized height holding `TARGET` wells,
     worst case over every design. Wells sit only ABOVE their own contact, so low down the
     array most columns have not started yet and the same well count needs far more height;
     that is why the tip anchor comes out ~3x a data slot;
  2. the GRID: tip anchor at 0, then data slots packed upward at `GAP_FACTOR` x
     `min_strip_distance` measured on the LONGEST design -- the finest pitch anything can
     use -- until the top anchor's room runs out;
  3. which subsets of those slots each design can legally light. A short array needs a wider
     normalized gap, so it can only light slots far apart;
  4. four ids per design, all distinct, pairwise Hamming distance >= 2 within a design, by
     backtracking with the most constrained design first.

`TARGET` (800) and `GAP_FACTOR` (1.15) came from a scan over both: they maximise the
worst-case margin over all 16 (design, id) pairs, which lands at +9.2% on stripe spacing and
+14.1% on well count. At `TARGET` = 900 there is no feasible assignment at all -- the bank is
near the edge, and U1.6 is what puts it there.

NOT BIT-REPRODUCIBLE. The heights come from a search on the built geometry, the assignment
from a backtracking order. Re-running after an unrelated change can shift a band in the 4th
decimal or swap which id lands on which design. A diff against the committed table is not a
failure -- only `python3 -m electrode_bundle.ionp_patterns` showing a design actually
failing is. Treat the committed table as the source of truth and this as the way to rebuild
it deliberately.
"""
import itertools

import numpy as np

from .batch import config_for
from .bundle import build_bundle
from .config import IonpConfig
from .design_sets import DESIGNS
from .ionp import effective_y_top, well_locs_for_pattern

TARGET = 800         # wells a band is SIZED for; the rule is min_n_wells (700)
GAP_FACTOR = 1.15    # x min_strip_distance, for the grid pitch
TOP_END = 0.998      # the top anchor's upper edge; 1.0 has no wells above it
N_IDS = 4            # barcode variants per design
MIN_DIST = 2         # stripes by which two ids of the same design must differ
ROUND = 4


class _Design:
    """One built geometry, reduced to the only thing the solve needs: where wells CAN go."""

    def __init__(self, spec, cfg: IonpConfig):
        self.name = spec.name
        el = np.asarray(build_bundle(config_for(spec)).electrode_locs, dtype=float)
        self.y_range = effective_y_top(el, cfg) - cfg.y_bottom
        #one band covering everything, so this is every well the design could ever carry
        _, ys = well_locs_for_pattern(el, np.array([[0.0, 1.0]]), cfg)
        self.well_y = np.sort(ys)
        self.gap = cfg.min_strip_distance / self.y_range

    def h_from(self, s):
        """normalized height a band STARTING at normalized `s` needs to hold TARGET wells"""
        j = np.searchsorted(self.well_y, s * self.y_range, "left") + TARGET - 1
        if j >= len(self.well_y):
            return float("inf")
        return (self.well_y[j] - s * self.y_range) / self.y_range

    def h_to(self, b):
        """...and a band ENDING at normalized `b`"""
        i = np.searchsorted(self.well_y, b * self.y_range, "right") - TARGET
        if i < 0:
            return float("inf")
        return (b * self.y_range - self.well_y[i]) / self.y_range


def _ceil(x):
    """round UP, always. Rounding a height DOWN can drop a whole 64-well step."""
    return float(np.ceil(x * 10**ROUND) / 10**ROUND)


def build_grid(designs):
    """-> tip anchor, data slots, top anchor. Heights worst-cased over every design."""
    tip = (0.0, _ceil(max(d.h_from(0.0) for d in designs)))
    top_h = _ceil(max(d.h_to(TOP_END) for d in designs))
    top = (round(TOP_END - top_h, ROUND), TOP_END)
    #the finest pitch anything tolerates is set by the LONGEST design: its normalized gap is
    #the smallest. Shorter designs then light a sparser subset of the same grid.
    pitch_gap = min(d.gap for d in designs) * GAP_FACTOR
    slots, s = [], tip[1] + pitch_gap
    while True:
        h = _ceil(max(d.h_from(s) for d in designs))
        a = round(s, ROUND)
        if not np.isfinite(h) or a + h + pitch_gap > top[0]:
            return tip, tuple(slots), top
        slots.append((a, round(a + h, ROUND)))
        s = a + h + pitch_gap


def legal_subsets(d, tip, slots, top):
    """every subset of slots whose gaps clear `min_strip_distance` on this design"""
    out = []
    for r in range(len(slots) + 1):
        for c in itertools.combinations(range(len(slots)), r):
            b = [tip] + [slots[i] for i in c] + [top]
            if all(b[i + 1][0] - b[i][1] >= d.gap for i in range(len(b) - 1)):
                out.append(c)
    return out


def assign(designs, tip, slots, top):
    """N_IDS subsets per design, all distinct, >= MIN_DIST apart within a design.

    Most constrained design first and backtracking: U1.6 has a handful of legal subsets
    and U8 has hundreds, so letting the roomy one pick first starves the tight one.
    Subsets are generated lazily -- materialising U8's C(n, 4) is millions of tuples.
    """
    pools = {d.name: legal_subsets(d, tip, slots, top) for d in designs}
    order = sorted(designs, key=lambda d: len(pools[d.name]))

    def rec(i, used, out):
        if i == len(order):
            return out
        for c in itertools.combinations([c for c in pools[order[i].name] if c not in used],
                                        N_IDS):
            if any(len(set(x) ^ set(y)) < MIN_DIST
                   for x, y in itertools.combinations(c, 2)):
                continue
            r = rec(i + 1, used | set(c), out + [(order[i].name, c)])
            if r:
                return r
        return None

    return rec(0, set(), [])


def _emit(designs, tip, slots, top, asg):
    fmt = lambda b: f"({b[0]:.4f}, {b[1]:.4f})"
    print("SLOT_BANDS: Tuple[Band, ...] = (")
    for b in slots:
        print(f"    {fmt(b)},")
    print(")")
    print(f"TIP_ANCHOR_BAND: Band = {fmt(tip)}")
    print(f"TOP_ANCHOR_BAND: Band = {fmt(top)}\n")

    by_name = {n: c for n, c in asg}
    #number ids by ARRAY LENGTH, not by DESIGNS order: ids then ascend with the
    #shortest array they fit, which is the order the fit matrix reads in.
    designs = sorted(designs, key=lambda d: d.y_range)
    ids, pid = [], 0
    print("IONP_PATTERNS: Dict[int, Pattern] = {")
    for d in designs:                       # shortest array first, so ids read in blocks
        for c in by_name[d.name]:
            bands = ", ".join(fmt(b) for b in [tip] + [slots[i] for i in c] + [top])
            print(f"{pid:6d}: ({bands}),")
            ids.append((d.name, pid))
            pid += 1
    print("}\n")
    print("DESIGN_IDS: Dict[str, Tuple[int, ...]] = {")
    for d in designs:
        mine = [p for n, p in ids if n == d.name]
        print(f'    "{d.name}": {tuple(mine)},')
    print("}")


def main():
    cfg = IonpConfig()
    #one _Design per GEOMETRY: DESIGNS lists each name once per barcode variant,
    #and the barcode is what this is solving for
    designs = [_Design(s, cfg) for s in {s.name: s for s in DESIGNS}.values()]
    tip, slots, top = build_grid(designs)
    asg = assign(designs, tip, slots, top)
    if asg is None:
        raise SystemExit(
            f"no assignment of {N_IDS} ids per design at distance {MIN_DIST}. The shortest "
            f"array cannot carry it: "
            + ", ".join(f"{d.name} {d.y_range:.0f}um "
                        f"{len(legal_subsets(d, tip, slots, top))} legal subsets"
                        for d in designs)
            + f".\nLower TARGET ({TARGET}) or GAP_FACTOR ({GAP_FACTOR}), or give the short "
              f"design more room (id_top_margin), before touching min_strip_distance.")
    _emit(designs, tip, slots, top, asg)
    print(f"\n# TARGET={TARGET} GAP_FACTOR={GAP_FACTOR}; "
          + ", ".join(f"{d.name} y_range {d.y_range:.0f}um" for d in designs))


if __name__ == "__main__":
    main()
