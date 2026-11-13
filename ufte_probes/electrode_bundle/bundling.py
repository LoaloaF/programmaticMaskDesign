r"""Flat-wafer electrode position -> where it ends up once the bundle is formed.

The probe is fabricated FLAT: n_channels polyimide fibers side by side, fiber i carrying
its contact at wafer (x_i, y_i), each running up to a common shoulder. It is then gathered
into a bundle. A fiber is INEXTENSIBLE, so length it spends travelling laterally in to the
bundle axis is length it no longer has to reach downward -- its contact ends up nearer the
shoulder than the flat drawing says.

SIGN. In this package y increases TOWARD THE SHOULDER (ch0 is the deepest contact, at the
smallest y). "Cannot reach as far down" therefore means the contact moves to a LARGER y, so
the offset is ADDED. Getting this backwards is easy and silent.

WHICH WAY THE ARRAY DISTORTS. Channel index grows outward in x AND upward in y at the same
time (see design_sets.py), so the shallowest contacts are also the outermost ones -- they
pay the largest offset and move furthest. The array therefore STRETCHES toward the shallow
end; it does not compress, and the pitch is no longer uniform even if the flat drawing's is.

    flat wafer (top view)          bundled (side view)
      |  |  |  |  |                      ||
      |  |  |  |  |                      ||   outer fibers turn in hardest,
       \ |  |  | /                       ||   so they lose the most reach
        \|  |  |/                        ||
    ch4 ch2 ch0 ch1 ch3                  ||   -> shallow end stretches
    |x| large ... small

MODEL. `TwoArcFold` follows electrode2geometry (Peter Gombkoto, ETH Neurotechnology; MIT,
Copyright (c) 2026 Peter Gombkoto -- https://github.com/Neurotechnology-at-ETH-Zurich/
electrode2geometry). Each fiber runs straight, fillets through an arc turning by theta
toward the axis, runs straight, fillets back, and continues -- with total arc length held
equal to the flat length. The closed form below was derived from that algorithm and checked
against its own numerical routine to ~1e-12 um.

    theta_i = radians(max_theta_deg) * |x_i| / max|x|      outermost fiber bends most
    l_mid_i = (1 - bundle_ratio) * |x_i| / sin(theta_i)    lateral run it must make
    t_sum   = (r1 + r2) * tan(theta_i / 2)                 fillet tangent lengths

    offset_i = (l_mid_i - t_sum) * (1 - cos theta_i) + (r1 + r2) * (theta_i - sin theta_i)

The first term is the Pythagorean loss of the straight run, the second the extra loss from
rounding the two corners. Small-angle, offset ~ x^2 * theta_max / (2 * max|x|): QUADRATIC in
lateral offset, zero at the centre fiber.

THE USEFUL PROPERTY: `offset` depends only on x, never on y. So the flat->bundled map is a
fixed per-channel shift, and inverting it is one pass with no solve -- see `precompensate`,
which answers "what do I draw flat so the BUNDLED contacts land where I want?".

CALIBRATION WARNING. `max_theta_deg` dominates (~60 um per 10 deg at this geometry) and is
not derivable from the drawing -- it is a property of how the bundle is actually made. The
defaults here are the reference GUI's, NOT a measurement of your process. Pin it against a
real bundled device before trusting absolute numbers; the author is at ETH.

UNITS: microns and degrees.
"""
from dataclasses import dataclass
from math import radians
from typing import Protocol, Sequence, Tuple

import numpy as np


class Fold(Protocol):
    """A bundling model: how far bundling moves a contact, given its lateral offset."""

    def offsets(self, x_um: np.ndarray) -> np.ndarray:
        """Per-fiber shift toward the shoulder (um, >= 0), from signed lateral offset."""

    def lateral(self, x_um: np.ndarray) -> np.ndarray:
        """Where each fiber's lateral offset ends up after gathering (um)."""


@dataclass(frozen=True)
class NoFold:
    """Bundling changes nothing. The default, so no correction is ever invented silently."""

    def offsets(self, x_um) -> np.ndarray:
        return np.zeros_like(np.asarray(x_um, dtype=float))

    def lateral(self, x_um) -> np.ndarray:
        return np.asarray(x_um, dtype=float)


@dataclass(frozen=True)
class TwoArcFold:
    """Arc-length-conserving two-arc gather (electrode2geometry; see module docstring).

    Defaults are that tool's Python-GUI defaults, not a measurement -- see the calibration
    warning above.
    """
    max_theta_deg: float = 50.0
    bundle_ratio: float = 0.07      # final |x| as a fraction of the flat |x|
    bend_r1_um: float = 400.0
    bend_r2_um: float = 400.0

    def offsets(self, x_um) -> np.ndarray:
        x = np.asarray(x_um, dtype=float)
        x_max = np.abs(x).max()
        out = np.zeros_like(x)
        if x_max <= 0:
            return out

        #the centre fiber does not turn at all, and theta -> 0 makes l_mid singular
        moving = np.abs(x) > 1e-12
        theta = radians(self.max_theta_deg) * np.abs(x[moving]) / x_max
        l_mid = (1.0 - self.bundle_ratio) * np.abs(x[moving]) / np.sin(theta)
        r_sum = self.bend_r1_um + self.bend_r2_um
        t_sum = r_sum * np.tan(theta / 2.0)

        if np.any(t_sum >= l_mid):
            worst = float(np.abs(x[moving])[np.argmax(t_sum - l_mid)])
            raise ValueError(
                f"the two fillets ({r_sum} um of radius) do not fit the lateral run at "
                f"|x| = {worst:.1f} um -- reduce bend_r1_um/bend_r2_um or max_theta_deg")

        out[moving] = ((l_mid - t_sum) * (1.0 - np.cos(theta))
                       + r_sum * (theta - np.sin(theta)))
        return out

    def lateral(self, x_um) -> np.ndarray:
        return self.bundle_ratio * np.asarray(x_um, dtype=float)


def bundled_positions(x_um: Sequence[float], y_um: Sequence[float],
                      fold: Fold) -> Tuple[np.ndarray, np.ndarray]:
    """Flat (x, y) -> bundled (lateral, y), in the same y datum as the input."""
    x = np.asarray(x_um, dtype=float)
    y = np.asarray(y_um, dtype=float)
    return fold.lateral(x), y + fold.offsets(x)


def precompensate(x_um: Sequence[float], y_target_um: Sequence[float],
                  fold: Fold) -> np.ndarray:
    """The flat y to DRAW so the contacts land on `y_target_um` once bundled.

    Exact and non-iterative because the offset depends only on x: draw each contact as far
    below its target as bundling will later lift it.
    """
    x = np.asarray(x_um, dtype=float)
    return np.asarray(y_target_um, dtype=float) - fold.offsets(x)
