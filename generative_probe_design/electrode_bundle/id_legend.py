"""Render the 16 IONP barcode patterns as a decode reference card.

Each panel is one ID (0..15): filled horizontal bands at their normalized shank positions
(0 = the shank tip, where the DEEPEST electrode sits; 1 = just above the SHALLOWEST electrode,
i.e. the top of the 64-electrode array, not the top of the shank). Two always-on thick anchors (the
fattest = bottom, for orientation) bracket the present data bands, which encode the ID.

The bands are frozen data (`ionp_patterns`), so this card is the decode reference for the
NORMALIZED barcode. It is not the whole decode: the panels are normalized, and each id is
only stamped on the design named in its title. In a real read the anchor separation gives
the design (1858 / 2739 / 4157 / 8093 um) and the lit slots then give the id within it --
which is why the panel titles carry the design. It renders whatever is in the bank, so a
custom pattern added at id >= 16 shows up here too.
"""
from typing import Optional

import matplotlib.pyplot as plt
import numpy as np

#Allow running this file BY PATH (`python3 electrode_bundle/id_legend.py`) as well as by module
#(`python3 -m electrode_bundle.id_legend`). A path run has no parent package, so the relative
#imports below would fail with "attempted relative import with no known parent package"; putting
#the package's parent directory on sys.path and naming the package fixes that. No effect on
#normal imports, where __package__ is already set.
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "electrode_bundle"

from .ionp_patterns import DESIGN_IDS, IONP_PATTERNS, pattern_bands


def render_id_legend(out_png: Optional[str] = None):
    ids = sorted(IONP_PATTERNS)
    home = {i: n for n, pids in DESIGN_IDS.items() for i in pids}
    fig, axes = plt.subplots(1, len(ids), figsize=(len(ids), 6), sharey=True)
    for i, ax in zip(ids, np.atleast_1d(axes)):
        for start, end in pattern_bands(i):
            ax.axhspan(start, end, color="black")
        ax.set_title(f"{i}\n{home.get(i, '?')}", fontsize=8)
        ax.set_ylim(0, 1)
        ax.set_xlim(0, 1)
        ax.set_xticks([])
    axes[0].set_ylabel("norm shank\n0 = tip (deepest electrode)  ->  1 = just above shallowest electrode")
    fig.suptitle(f"IONP barcodes: {len(ids)} patterns, 4 per design "
             f"(panel subtitle = the design that ships the id)")
    fig.tight_layout()
    if out_png:
        fig.savefig(out_png, dpi=150)
    return fig


if __name__ == "__main__":
    #Run as `python3 -m electrode_bundle.id_legend` (NOT `python3 electrode_bundle/id_legend.py`
    #-- a direct path run has no parent package, so the relative imports above fail).
    #The `legend` subcommand of main.py does the same thing.
    from .config import ensure_out_dir
    _out = f"{ensure_out_dir()}/ionp_id_legend.png"
    render_id_legend(_out)
    print(f"Saved legend -> {_out}")


def render_barcode_profile(spec, out_png: Optional[str] = None):
    """The barcode as an MRI would read it: well count per 25 um of depth, on a real build.

    The whole-probe DXF render cannot show this -- at that scale the shank fill covers the
    1.5 um wells -- so this is the picture to judge a stamped barcode by. The bands the
    pattern asked for are drawn behind the profile, so a stripe that came out thin, or a gap
    that closed up, is visible against what was intended.
    """
    import numpy as np

    from .batch import config_for
    from .bundle import build_bundle
    from .config import IonpConfig
    from .ionp import effective_y_top, well_locs_for_pattern
    from .ionp_patterns import pattern_bands

    cfg = IonpConfig(pattern_i=spec.ionp_pattern_id)
    el = build_bundle(config_for(spec)).electrode_locs
    y_top, yb = effective_y_top(el, cfg), cfg.y_bottom
    pattern = pattern_bands(spec.ionp_pattern_id)
    _, ys = well_locs_for_pattern(el, pattern, cfg)

    fig, ax = plt.subplots(figsize=(9, 4))
    for start, end in pattern:
        ax.axvspan(start * (y_top - yb) + yb, end * (y_top - yb) + yb,
                   color="0.85", zorder=0)
    bins = np.arange(yb, y_top + 25, 25)
    ax.hist(ys, bins=bins, color="black", zorder=2)
    ax.axhline(0, color="black", lw=0.8)
    ax.set_xlabel("depth along the array (um)   0 = tip, deepest contact")
    ax.set_ylabel("wells per 25 um")
    ax.set_title(f"{spec.name} pattern {spec.ionp_pattern_id}: {len(ys)} wells in "
                 f"{len(pattern)} stripes (grey = the bands the pattern asked for)")
    fig.tight_layout()
    if out_png:
        fig.savefig(out_png, dpi=150)
    return fig
