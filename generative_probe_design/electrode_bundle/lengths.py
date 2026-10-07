r"""The doc's three length numbers <-> BundleConfig knobs.

A design doc row like `64-lin-B-5590-10600 (H2)` states, per the design's author:

    5590   DV span from the bottom to the top electrode site   -> delta_y / delta_y_overrides
    10600  total bundle length, LOOP to SHOULDER               -> bottom_elec      (here)
    345    "distance from loop to first pad", loop to the
           DEEPEST ELECTRODE pad (not the solder pad)          -> hook_drop        (here)

plus a fourth length the doc does not carry, SHOULDER to the first solder pad, which is
`BundleConfig.ribbon_length`.

    loop ---- bundle_length ----> shoulder ---- ribbon_length ----> first solder pad
       \__ loop_to_first_site __/^ deepest site

Why solve numerically instead of inverting a formula. Both targets ARE closed forms today

    loop->shoulder      = bottom_elec + delta_y + hook_drop + 338.75
    loop->first site    = hook_drop + 347.5

but those constants fold in the hook's own etch-hole offset (hooks/, and `hook_scale`)
and `l/2`, so they silently go stale the moment the hook or any of those change. Both
relations are exactly LINEAR WITH SLOPE 1 in their knob, so measuring one build and adding
the residual lands on the target exactly, whatever those constants happen to be -- and
`solve_lengths` asserts that it did.
"""
from dataclasses import replace
from typing import Optional, Tuple

from ezdxf import bbox

from .bundle import build_bundle
from .config import BundleConfig

TOL_UM = 0.05


def loop_y(result) -> float:
    """y of the insertion loop's centre = the hook's etch hole, the probe's depth datum.

    Taken from `BundleResult.loop_xy`, which build_bundle sets from the hook spec. It used to
    be read back off a layer holding the loop hole alone, but that hole is etched in the SAME
    step that frees the device, so it now lives in `Etching` with the rest of the mask and
    nothing holds it by itself. Reading it from the result is also simply better:
    the datum every design's length is solved against no longer depends on a layer query
    picking the right ring.
    """
    if result.loop_xy is None:
        raise ValueError("this build has no insertion loop (hook_on_wide_channel is off), "
                         "so there is no datum to measure lengths from")
    return float(result.loop_xy[1])


def measure(result) -> Tuple[float, float, float]:
    """(site span, fiber length, loop offset) of a built bundle, in um."""
    ys = result.electrode_locs[:, 1]
    ly = loop_y(result)
    return float(ys.max() - ys.min()), float(result.wire_top - ly), float(ys.min() - ly)


def measure_overall(result, cfg: BundleConfig) -> float:
    """loop -> first solder pad, i.e. the whole device (fiber_length + ribbon_length)."""
    return float(result.wire_top + cfg.ribbon_length - loop_y(result))


def solve_lengths(cfg: BundleConfig, fiber_length: Optional[float] = None,
                  loop_offset: Optional[float] = None) -> BundleConfig:
    """A copy of `cfg` whose bottom_elec/hook_drop hit the requested lengths exactly.

    Either target may be None, meaning "leave that knob alone". `loop_offset` is solved
    first because moving the loop also moves the fiber measurement that follows it.
    """
    if loop_offset is not None:
        _, _, got = measure(build_bundle(cfg))
        cfg = replace(cfg, hook_drop=cfg.hook_drop + (loop_offset - got))

    if fiber_length is not None:
        _, got, _ = measure(build_bundle(cfg))
        cfg = replace(cfg, bottom_elec=cfg.bottom_elec + (fiber_length - got))

    _, got_fiber, got_loop = measure(build_bundle(cfg))
    if fiber_length is not None:
        assert abs(got_fiber - fiber_length) < TOL_UM, \
            f"fiber length solve missed: wanted {fiber_length}, got {got_fiber}"
    if loop_offset is not None:
        assert abs(got_loop - loop_offset) < TOL_UM, \
            f"loop offset solve missed: wanted {loop_offset}, got {got_loop} " \
            f"(a loop offset below the hook's own floor -- see hooks.floor_loop_offset -- " \
            f"is reached by pulling the head up into the trace, so this should not happen)"
    return cfg
