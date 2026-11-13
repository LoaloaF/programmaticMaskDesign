r"""The insertion hooks: named shapes for the anchor at the very tip of the probe.

The hook continues the WIDE channel's polyimide trace past the deepest electrode and ends
in a head carrying ONE elliptical ETCH HOLE -- the insertion loop the surgeon threads. That
hole is the probe's depth datum: `lengths.loop_y()` reads it back out of the DXF and
`lengths.solve_lengths()` tunes `hook_drop` until every design's `loop_offset` is exactly on
spec. So a hook owns BOTH its outline and its etch hole; they must not drift apart.

Each hook is a NAMED SPEC in `HOOKS`, picked by `BundleConfig.hook`:

    legacy_barb    the original 38-point coordinate template (2 barbs). Archived as-is --
                   see hooks/legacy_barb.py for why it was replaced.
    teardrop_clap  today's hook: a bulbous teardrop with 6 rounded claps. Parametric.

A spec is any object with these two methods (`stem_hw` is the half-width of the polyimide
trace the hook continues, so a parametric hook can match it seamlessly):

    polygon(cx, y_top, stem_hw, scale, drop) -> (x_array, y_array)   closed outline
    etch(cx, y_top, stem_hw, scale, drop)    -> (ecx, ecy, rx, ry)   the etch ellipse

THE `drop` CONTRACT -- load-bearing, do not break it
    `drop` lowers the head by that many microns and splices a straight stem of half-width
    `stem_hw` back up to `y_top`, so the hook reaches deeper without changing shape. The
    etch hole must therefore move DOWN BY EXACTLY `drop`: `solve_lengths` measures one build
    and adds the residual, which lands on target only because `loop_offset` is linear in
    `hook_drop` with SLOPE 1. A hook whose etch moves by anything other than `drop` (or a
    head whose shape changes with it) makes that solve fail its assert.

    `drop` may be slightly NEGATIVE. The solver reaches a `loop_offset` below the hook's own
    floor (see `floor_loop_offset` below) by pulling the head UP into the trace, which is
    harmless -- the hook and the trace are unioned into one polyimide body -- as long as the
    stem is the same width as the trace.

THE ETCH WALL
    The etch hole is a hole in a thin polyimide film: too close to the outline and the head
    tears off during insertion. `etch_wall()` measures the narrowest polyimide left around
    the hole and `build_bundle` asserts it against `BundleConfig.hook_etch_margin`, so a hook
    whose hole is "partly etched away" cannot ship silently.
"""
import numpy as np

from .legacy_barb import LEGACY_BARB
from .teardrop_clap import TEARDROP_CLAP

HOOKS = {
    "legacy_barb": LEGACY_BARB,
    "teardrop_clap": TEARDROP_CLAP,
}


def get_hook(name):
    """The hook spec registered under `name` (BundleConfig.hook)."""
    try:
        return HOOKS[name]
    except KeyError:
        raise ValueError(f"unknown hook {name!r}; have: {', '.join(sorted(HOOKS))}") from None


def etch_wall(hook_xy, etch_ring) -> float:
    """Narrowest polyimide between the etch hole and the hook outline, in um.

    NEGATIVE if the hole pokes out of the hook (the hole would eat the head's edge), so a
    single `>= margin` check covers both "outside" and "too close".
    """
    from shapely.geometry import Point, Polygon

    hook = Polygon(zip(hook_xy[0], hook_xy[1])).buffer(0)
    pts = [Point(p) for p in np.asarray(etch_ring)[:, :2]]
    wall = min(hook.exterior.distance(p) for p in pts)
    return wall if all(hook.contains(p) for p in pts) else -wall


def floor_loop_offset(hook, stem_hw=10.0, scale=1.0) -> float:
    """How far the etch hole sits below the attach point at `drop` = 0, in um.

    The hook's contribution to the smallest reachable `loop_offset`: a design asking for
    less than this plus the trace run above the hook drives `hook_drop` negative.
    """
    _, ecy, _, _ = hook.etch(0.0, 0.0, stem_hw=stem_hw, scale=scale, drop=0.0)
    return -ecy
