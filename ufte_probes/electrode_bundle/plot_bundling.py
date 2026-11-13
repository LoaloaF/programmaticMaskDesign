"""Draw what bundling does to a design's contact positions.

    python3 -m electrode_bundle.plot_bundling            # every design in DESIGNS
    python3 -m electrode_bundle.plot_bundling C1 H1      # name substrings
    python3 -m electrode_bundle.plot_bundling --theta 35 # try a different gather angle

Three panels per design: the flat wafer as drawn, the same contacts after the bundle is
gathered, and the per-channel shift that took them there. The point of the picture is that
the shift is NOT uniform -- it is ~zero on the centre fiber and largest on the outermost,
so the array does not just translate, it stretches (see bundling.py).

Writes `<design>_bundling.png` into the output folder.
"""
import argparse

import numpy as np

from .batch import config_for
from .bundle import build_bundle
from .bundling import TwoArcFold, bundled_positions
from .config import ensure_out_dir
from .design_sets import DESIGNS
from .main import _ensure_usable_font


def plot_design(spec, fold, out_dir):
    import matplotlib
    matplotlib.use("Agg")
    _ensure_usable_font()
    import matplotlib.pyplot as plt

    result = build_bundle(config_for(spec))
    x, y = result.electrode_locs[:, 0], result.electrode_locs[:, 1]
    ml, dv = bundled_positions(x, y, fold)
    offset = dv - y
    ch = np.arange(len(x))

    fig, axes = plt.subplots(1, 3, figsize=(13, 7),
                             gridspec_kw={"width_ratios": [1.5, 1, 1.2]})
    colors = plt.cm.viridis(ch / max(ch.max(), 1))

    #--- flat wafer -------------------------------------------------------------------
    ax = axes[0]
    for xi, yi, c in zip(x, y, colors):
        ax.plot([xi, xi], [yi, y.max() + 800], color=c, lw=0.5, alpha=0.35)  # the fiber
    ax.scatter(x, y, c=colors, s=14, zorder=3)
    ax.set_title(f"flat wafer (as drawn)\nspan {y.max() - y.min():.0f} um")
    ax.set_xlabel("x (um)"); ax.set_ylabel("y (um)")

    #--- bundled ----------------------------------------------------------------------
    ax = axes[1]
    ax.scatter(x, y, facecolors="none", edgecolors="0.75", s=14, label="flat", zorder=2)
    for xi, yi, mi, di in zip(x, y, ml, dv):
        ax.plot([xi, mi], [yi, di], color="0.85", lw=0.4, zorder=1)   # where each one went
    ax.scatter(ml, dv, c=colors, s=14, label="bundled", zorder=3)
    ax.set_title(f"after bundling\nspan {dv.max() - dv.min():.0f} um "
                 f"({dv.max() - dv.min() - (y.max() - y.min()):+.0f})")
    ax.set_xlabel("lateral (um)")
    ax.legend(fontsize=7, loc="lower right")

    #--- the shift --------------------------------------------------------------------
    ax = axes[2]
    ax.plot(ch, offset, ".-", ms=4, lw=0.8)
    ax.set_title("shift toward the shoulder\n(quadratic in lateral offset)")
    ax.set_xlabel("channel index"); ax.set_ylabel("shift (um)")
    ax.grid(alpha=0.3)

    for ax in axes[:2]:
        ax.set_ylim(min(y.min(), dv.min()) - 400, max(y.max(), dv.max()) + 900)

    fig.suptitle(f"{spec.name}  ({spec.doc_id})   {type(fold).__name__}"
                 + (f"  theta_max={fold.max_theta_deg:g} deg, bundle_ratio={fold.bundle_ratio:g}"
                    if isinstance(fold, TwoArcFold) else ""))
    fig.tight_layout()
    out = f"{out_dir}/{spec.name}_bundling.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)

    print(f"  {spec.name:11s} span {y.max() - y.min():7.1f} -> {dv.max() - dv.min():7.1f} um"
          f"   max shift {offset.max():6.1f} um   -> {out}")


def main(argv=None):
    p = argparse.ArgumentParser(description="Plot flat vs bundled contact positions")
    p.add_argument("names", nargs="*", help="name substrings; default all of DESIGNS")
    p.add_argument("--theta", type=float, default=TwoArcFold.max_theta_deg,
                   help="max_theta_deg of the outermost fiber (dominates the result)")
    p.add_argument("--bundle-ratio", type=float, default=TwoArcFold.bundle_ratio)
    args = p.parse_args(argv)

    specs = [s for s in DESIGNS if not args.names
             or any(n in s.name for n in args.names)]
    if not specs:
        raise SystemExit("no design matches; have: " + ", ".join(s.name for s in DESIGNS))

    fold = TwoArcFold(max_theta_deg=args.theta, bundle_ratio=args.bundle_ratio)
    out_dir = ensure_out_dir()
    for spec in specs:
        plot_design(spec, fold, out_dir)


if __name__ == "__main__":
    main()
