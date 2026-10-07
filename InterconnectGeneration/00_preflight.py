#!/usr/bin/env python3
"""Stage 00 -- check the environment and the pad parameters BEFORE running stage 02.

Stage 01 is fast (about a second). Stage 02 is not, and it fails LATE: the router counts
the wire ends in the interconnect DXF and raises only after it has already read and
parsed everything. This script answers, up front:

  * are the dependencies and the asset files actually here?
  * does the requested pad grid emit exactly as many wires as the connector has room for?
  * does a column's wire bundle still fit inside one pad pitch?
  * does the via staircase still land on the pad?

Everything is read from config_<design>.py (12block, 8block, dummy): the pad grid from its
section 1, TOTAL_WIRES from the connector section (dummy: top of the file). Nothing is generated and no stage script is run.

THE INVARIANT THAT MATTERS. Emitted wires must equal the router's TOTAL_WIRES, which is
the connector's capacity (64 x blocks) and is NOT adjustable from the pad side. The two
generators reach that number by different routes:

  8-block   n_rows*n_cols - n_shorts_left - n_shorts_right   = 12*44 - 16  = 512
  12-block  sum over columns of (n_rows - row_start(col))    = 15*53 - 27  = 768
            (row_start drops the bottom pad on columns 0..n_cols//2)

So if you change n_rows or n_cols in a config, you must rebalance the other terms or the
design will not fit the connector. That is what this script is for. It also warns when the
via is no longer in its pad's top-left corner (via_offset_y = pad_side/2 - via_offset_x),
the value that is easiest to forget when the pad size changes.

Edit the config first, then run:

    python3 00_preflight.py                        # all designs
    python3 00_preflight.py --design 8block        # one design
"""
import argparse
import importlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets")
DESIGNS = os.path.join(HERE, "designs")

DEPS = ("ezdxf", "shapely", "numpy", "gdstk", "matplotlib")   # gdstk: 02_make_dummy.py;
                                                              # matplotlib: only old_interconnect/

ASSET_FILES = ("connector_signal_pads_exact_mm.csv", "Board_8x_outline_simon.dxf",
               "Board_12x_outline_simon.dxf", "mea1k_interconnect_only.dxf")

# design key -> (generator script, router script); the numbers come from config_<key>.py
BUILDS = {
    "12block": ("01_generate_12block.py", "02_route_12block.py"),
    "8block": ("01_generate_8block.py", "02_route_8block.py"),
    "dummy": ("01_generate_12block.py --config dummy", "02_make_dummy.py"),
}


def load_config(key):
    """Import config_<key>.py -- plain data plus a few derived values, cheap to load."""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    return importlib.import_module("config_" + key)


def emitted_wires(key, cfg):
    """Wires the generator will emit for this pad grid. Mirrors the rule in each script."""
    if key == "8block":
        # row_start_A returns 0 for every column: no pad is dropped, the shorts fold pairs.
        return cfg["n_rows"] * cfg["n_cols"] - cfg["n_shorts_left"] - cfg["n_shorts_right"]
    # 12-block: columns 0..n_cols//2 drop their bottom (row 0) pad.
    half = cfg["n_cols"] // 2
    return sum(cfg["n_rows"] - (1 if c <= half else 0) for c in range(cfg["n_cols"]))


def check_env():
    print("environment")
    ok = True
    for m in DEPS:
        try:
            mod = __import__(m)
            print("    ok   %-12s %s" % (m, getattr(mod, "__version__", "")))
        except ImportError:
            print("    MISSING %s  -- pip install %s" % (m, m))
            ok = False
    for f in ASSET_FILES:
        p = os.path.join(ASSETS, f)
        if os.path.exists(p):
            print("    ok   asset  %s (%.1f MB)" % (f, os.path.getsize(p) / 1e6))
        else:
            print("    MISSING asset  assets/%s" % f)
            ok = False
    if not os.path.isdir(DESIGNS):
        print("    note designs/ will be created on first run")
    return ok


def check_design(key):
    gen_script, route_script = BUILDS[key]
    c = load_config(key)
    cfg, routing = dict(c.GEN_CONFIG), c.GEN_ROUTING
    capacity = ("%d blocks x 64" % (2 * c.N_BLOCKS_PER_COLUMN) if hasattr(c, "N_BLOCKS_PER_COLUMN")
                else "the dummy cap shorts them all")

    pad_side = cfg["pad_side"]
    pitch = pad_side + cfg["gap"]
    wire_width = routing["wire_width"]
    wire_gap = routing["wire_gap"]
    wire_pitch = wire_width + wire_gap

    print("\n%s  (%s)" % (key, gen_script))
    print("    pad grid    %d rows x %d cols, side %g um, gap %g um -> pitch %g um"
          % (cfg["n_rows"], cfg["n_cols"], pad_side, cfg["gap"], pitch))

    ok = True

    # 1. the wire-count invariant -- the hard failure in stage 02
    required = c.TOTAL_WIRES
    emitted = emitted_wires(key, cfg)
    if emitted == required:
        print("    ok   wires       %d emitted = %d required (%s)"
              % (emitted, required, capacity))
    else:
        print("    FAIL wires       %d emitted but %s needs exactly %d (%s)"
              % (emitted, route_script, required, capacity))
        print("         stage 02 will raise in extract_band_endpoints. Adjust n_rows/n_cols"
              + ("/shorts." if key == "8block" else "."))
        ok = False

    # 2. a column's bundle must fit inside one pad pitch
    max_rows = int((pitch - wire_width - wire_gap) // wire_pitch) + 1
    if cfg["n_rows"] <= max_rows:
        print("    ok   fit limit   n_rows %d <= %d (lane pitch %g um in a %g um pad pitch)"
              % (cfg["n_rows"], max_rows, wire_pitch, pitch))
    else:
        print("    FAIL fit limit   n_rows %d exceeds %d; column bundles will overlap."
              % (cfg["n_rows"], max_rows))
        print("         Raise gap or pad_side, or lower n_rows, in the config.")
        ok = False

    # 3. the via staircase must stay on the pad
    via_half = (routing["via_radius"] if routing["via_shape"] == "circle"
                else routing["via_w"] / 2.0)
    span = routing["via_offset_x"] + (cfg["n_rows"] - 1) * routing["via_pitch"] + via_half
    if span <= pad_side:
        print("    ok   via stair   reaches %.1f um <= pad_side %g um" % (span, pad_side))
    else:
        print("    FAIL via stair   reaches %.1f um > pad_side %g um; the lowest vias fall "
              "off the pad." % (span, pad_side))
        ok = False

    # 4. the via should sit in the pad's top-left corner (all fabricated designs do).
    #    A warning, not a failure: it is the value most easily forgotten when pad_side changes.
    corner = pad_side / 2.0 - routing["via_offset_x"]
    if abs(routing["via_offset_y"] - corner) < 1e-9:
        print("    ok   via corner  via_offset_y %g = pad_side/2 - via_offset_x" % corner)
    else:
        print("    WARN via corner  via_offset_y %g, but pad_side/2 - via_offset_x = %g: the via is "
              "not in the pad's top-left corner. Update GEN_ROUTING['via_offset_y'] unless intended."
              % (routing["via_offset_y"], corner))

    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--design", choices=sorted(BUILDS), help="default: check all")
    a = ap.parse_args()

    ok = check_env()
    for key in ([a.design] if a.design else sorted(BUILDS)):
        ok &= check_design(key)

    print()
    if ok:
        print("PREFLIGHT OK -- run stage 01, then stage 02.")
        return 0
    print("PREFLIGHT FAILED -- fix the items above before running stage 02.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
