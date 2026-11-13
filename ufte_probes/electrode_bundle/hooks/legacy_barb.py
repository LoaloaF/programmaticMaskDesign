r"""The ORIGINAL hook, archived verbatim: a barbed arrowhead from a fixed point template.

Kept so old devices can be regenerated bit-identically and so the two shapes can be
compared side by side; `teardrop_clap` is what new designs use. Select it with
``BundleConfig(hook="legacy_barb")``.

WHY IT WAS REPLACED. The head is an arrowhead with two barbs swept up and outward, and the
V-notch at the root of each barb is the closest the outline ever comes to the round etch
hole -- 9.07 um of polyimide there against ~20 um everywhere else. That pinch is the
"the hook is partly etched away" the redesign set out to fix, and it is not tunable: the
outline is a hardcoded 38-point list with no parameter to open the notch up, and the hole
is a circle in a head that is not round. `teardrop_clap` puts an ellipse in a bulb instead,
where the wall is even the whole way round.

The numbers below are archived AS MEASURED, including the etch offsets -- fixing them here
would defeat the point of keeping the old shape reproducible.
"""
from dataclasses import dataclass, field

import numpy as np

from ..geometry import polygon_centroid

#Hook template taken from the reference file (micron). The ~20um-wide stem is at the TOP
#(max y); the anchor/barb body hangs below it. `polygon()` normalises this so the stem
#centre is at x=0 and the stem top at y=0, then places it at a given attach point with the
#body hanging toward -y (i.e. below the electrode trace it continues).
HOOK_TEMPLATE = np.array([
    (58.33000, 14.75800), (53.50000, 15.71800), (48.95000, 17.60300), (41.34100, 21.47300),
    (34.38000, 27.20600), (30.53000, 32.77500), (27.41800, 39.49100), (18.00000, 87.07200),
    (33.29200, 53.51700), (33.29200, 64.76100), (33.40500, 67.69200), (33.92200, 69.37300),
    (35.56000, 73.20900), (37.45700, 75.19100), (42.75800, 76.44100), (45.55900, 77.90600),
    (47.80000, 80.36300), (49.48100, 83.55200), (51.79200, 90.70700), (51.79200, 131.31300),
    (71.79200, 131.31300), (71.79200, 90.70700), (73.10300, 83.55200), (74.78400, 80.36300),
    (77.02500, 77.90600), (85.12700, 75.19100), (86.97200, 71.31200), (88.17900, 67.69200),
    (88.29200, 64.76100), (88.29200, 53.61200), (103.58400, 87.07200), (94.16600, 39.49100),
    (91.05400, 32.77500), (87.20400, 27.20600), (80.24300, 21.47300), (73.23900, 18.00800),
    (68.08400, 15.71800), (63.25400, 14.75800),
])
_STEM_CX = 0.5 * (51.79200 + 71.79200)   # 61.792 -> stem centre, aligns with the trace
_STEM_TOP = 131.31300                    # max y -> attach point
_STEM_HW = 0.5 * (71.79200 - 51.79200)   # 10 -> the template's own stem half-width


@dataclass(frozen=True)
class LegacyBarbHook:
    """The archived barbed hook. See the module docstring before changing any number."""
    template: np.ndarray = field(default_factory=lambda: HOOK_TEMPLATE)
    etch_r: float = 17.5      # the hole is a CIRCLE; ry == rx
    etch_dx: float = 0.0      # offset from the barb body's area centroid
    etch_dy: float = -10.0

    def polygon(self, cx, y_top, stem_hw=None, scale=1.0, drop=0.0):
        """Closed (x, y) outline, stem centred at (cx, y_top), body hanging toward -y.

        `scale` scales the whole shape about the stem top. `stem_hw` is IGNORED -- this
        hook's stem width is baked into the template at 10 um half-width, which is exactly
        the wide channel's polyimide half-width, so it already matches the trace.
        """
        t = np.asarray(self.template, dtype=float).copy()
        t[:, 0] = (t[:, 0] - _STEM_CX) * scale + cx
        t[:, 1] = (t[:, 1] - _STEM_TOP) * scale + (y_top - drop)
        x, y = list(t[:, 0]), list(t[:, 1])
        if drop > 0:
            # Splice a vertical riser up to y_top between the two stem-top corners (19/20).
            x = x[:20] + [x[19], x[20]] + x[20:]
            y = y[:20] + [y_top,  y_top]  + y[20:]
        return np.asarray(x), np.asarray(y)

    def etch(self, cx, y_top, stem_hw=None, scale=1.0, drop=0.0):
        """(ecx, ecy, rx, ry) of the etch hole.

        Centred on the BARB BODY (measured at drop=0) and then moved down by `drop`, so the
        thin drop-stem riser cannot skew the area centroid upward as `hook_drop` grows --
        and so the hole tracks `drop` with slope exactly 1, which `solve_lengths` needs.
        """
        bx, by = self.polygon(cx, y_top, stem_hw=stem_hw, scale=scale, drop=0.0)
        ecx, ecy = polygon_centroid(bx, by)
        r = self.etch_r * scale
        return (ecx + self.etch_dx * scale, ecy + self.etch_dy * scale - drop, r, r)


LEGACY_BARB = LegacyBarbHook()
