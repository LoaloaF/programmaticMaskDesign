#!/usr/bin/env python3
"""Stage 03 -- print what a generated DXF actually contains, before you open it.

A layer-by-layer census: entity counts, vertex counts, entity types and the bounding box.
Useful for knowing what you are looking at, and for spotting a layer that came out empty
or a wire count that is not what you asked for.

It also flags one thing you CANNOT see in a viewer: `WaferNesting` stage 01 reads only
LWPOLYLINE and HATCH entities. Geometry emitted as any other type looks perfectly normal
on screen but is invisible to the nester's footprint, which then comes out smaller than
the real part -- the dangerous direction (WaferNesting HANDOVER.md 3.2).

    python3 03_inspect.py                       # everything in designs/
    python3 03_inspect.py designs/foo.dxf       # one file
"""
import argparse
import glob
import os
import sys

import ezdxf

HERE = os.path.dirname(os.path.abspath(__file__))
DESIGNS = os.path.join(HERE, "designs")

# What WaferNesting stage 01 can read.
NESTABLE = {"LWPOLYLINE", "HATCH"}


def _vertices(e):
    t = e.dxftype()
    if t == "LWPOLYLINE":
        return len(e)
    if t == "POLYLINE":
        return len(e.vertices)
    if t == "HATCH":
        return sum(len(getattr(p, "vertices", ()) or ()) for p in e.paths)
    return 0


def _points(e):
    t = e.dxftype()
    if t == "LWPOLYLINE":
        return [(p[0], p[1]) for p in e.get_points()]
    if t == "POLYLINE":
        return [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
    if t == "HATCH":
        return [(v[0], v[1]) for p in e.paths for v in (getattr(p, "vertices", ()) or ())]
    return []


def inspect(path):
    msp = ezdxf.readfile(path).modelspace()

    layers = {}
    xs, ys = [], []
    for e in msp:
        d = layers.setdefault(e.dxf.layer, {"n": 0, "v": 0, "types": {}})
        d["n"] += 1
        d["v"] += _vertices(e)
        d["types"][e.dxftype()] = d["types"].get(e.dxftype(), 0) + 1
        for x, y in _points(e):
            xs.append(x)
            ys.append(y)

    print("%s   (%.1f MB)" % (os.path.basename(path), os.path.getsize(path) / 1e6))
    for lay in sorted(layers):
        d = layers[lay]
        types = ", ".join("%s" % t if n == d["n"] else "%d %s" % (n, t)
                          for t, n in sorted(d["types"].items()))
        print("    %-22s %6d %-8s %8d vertices   %s"
              % (lay, d["n"], "entity" if d["n"] == 1 else "entities", d["v"], types))

    if xs:
        print("    %-22s %.3f x %.3f mm      bbox [%.1f %.1f %.1f %.1f] um"
              % ("bbox", (max(xs) - min(xs)) / 1000.0, (max(ys) - min(ys)) / 1000.0,
                 min(xs), min(ys), max(xs), max(ys)))
    print("    %-22s %6d %-8s %8d vertices"
          % ("TOTAL", sum(d["n"] for d in layers.values()), "entities",
             sum(d["v"] for d in layers.values())))

    bad = {}
    for d in layers.values():
        for t, n in d["types"].items():
            if t not in NESTABLE:
                bad[t] = bad.get(t, 0) + n
    if bad:
        print("    NOTE: %s -- invisible to WaferNesting stage 01, which reads only"
              % ", ".join("%d x %s" % (n, t) for t, n in sorted(bad.items())))
        print("          LWPOLYLINE and HATCH. Its footprint would under-cover this part.")
    print()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("files", nargs="*", help="DXFs to inspect (default: all of designs/)")
    a = ap.parse_args()

    files = a.files or sorted(glob.glob(os.path.join(DESIGNS, "*.dxf")))
    if not files:
        raise SystemExit("nothing to inspect -- run stage 01/02 first")
    for p in files:
        inspect(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
