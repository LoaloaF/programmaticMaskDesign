"""Stage 01e -- the old and the new wafer in ONE GDS, for checking a revision by eye.

Reads the wafer 01d checked against (placements.json's target, e.g. Rev2) and the wafer 01d
built (config.WAFER_OUT, e.g. Rev3), and writes them on top of each other:

    <layer>/0   the old wafer        (cells renamed *_<old tag>, under WAFER_<old tag>)
    <layer>/1   the new wafer        (cells renamed *_<new tag>, under WAFER_<new tag>)
    <layer>/2   old XOR new, flattened -- empty on every layer the revision did not touch

The tags are config.OVERLAY_TAGS (config_rev3: REV2, REV3; default OLD, NEW).

Both wafers are placed untransformed under one top cell, OVERLAY, so marks and devices that
line up in the files line up here. A .lyp next to the GDS names every layer (load it with
File > Load Layer Properties, or `klayout <gds> -l <lyp>`).

RUN WITH KLAYOUT, after 01d:
    export WAFERNEST_CONFIG=config_rev3
    /Applications/KLayout/klayout.app/Contents/MacOS/klayout -b -r WaferNesting/01e_overlay_wafers.py
"""
import importlib, json, os, sys

import pya

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
C = importlib.import_module(os.environ.get("WAFERNEST_CONFIG", "config_rev3"))

P = json.load(open(C.PLACEMENTS_PATH))
OLD, NEW = P["target"]["path"], C.WAFER_OUT
TOP = P["target"]["top"]
OUT = os.path.join(C.OUT_DIR, "overlay_%s.gds" % C.RUN_TAG)
LYP = os.path.splitext(OUT)[0] + ".lyp"
OLD_TAG, NEW_TAG = getattr(C, "OVERLAY_TAGS", ("OLD", "NEW"))

# Fabricated meaning of each wafer layer (config_rev2 / config_rev3 layer maps).
LAYER_NAMES = getattr(C, "LAYER_NAMES", {
    3: "PI etch (Polyimide_Negative + EtchingPad)", 5: "Etching + Via", 6: "Metal2",
    7: "Metal1", 8: "Metal3", 10: "wafer outline"})
COLORS = {0: "#3060ff", 1: "#ff3030", 2: "#ffd000"}

O = pya.Layout()
for path in (OLD, NEW):
    if not os.path.exists(path):
        raise SystemExit("missing %s -- run 01c (old) / 01d (new) first" % path)


def add(path, suffix, datatype):
    """Copy wafer `path` into O under WAFER<suffix>, every layer moved to `datatype`."""
    src = pya.Layout()
    src.read(path)
    if O.cells() == 0:
        O.dbu = src.dbu
    elif O.dbu != src.dbu:
        raise SystemExit("the two wafers have different dbu (%g vs %g)" % (O.dbu, src.dbu))
    for li in src.layer_indexes():
        info = src.get_info(li)
        src.set_info(li, pya.LayerInfo(info.layer, datatype))
    for cell in src.each_cell():
        cell.name = cell.name + suffix
    holder = O.create_cell("WAFER" + suffix)
    holder.copy_tree(src.cell(TOP + suffix))
    return holder


old = add(OLD, "_" + OLD_TAG, 0)
new = add(NEW, "_" + NEW_TAG, 1)
top = O.create_cell("OVERLAY")
for c in (old, new):
    top.insert(pya.CellInstArray(c.cell_index(), pya.Trans()))

layers = sorted({O.get_info(li).layer for li in O.layer_indexes()})
print("XOR (datatype 2), um2:")
for ln in layers:
    regions = []
    for cell, dt in ((old, 0), (new, 1)):
        li = O.find_layer(ln, dt)
        regions.append(pya.Region(cell.begin_shapes_rec(li)) if li is not None else pya.Region())
    x = regions[0] ^ regions[1]
    if not x.is_empty():
        top.shapes(O.layer(pya.LayerInfo(ln, 2))).insert(x)
    print("  %2d  %-45s %14.3f" % (ln, LAYER_NAMES.get(ln, "?"), x.area() * O.dbu * O.dbu))

O.write(OUT)

entries = []
for ln in layers:
    for dt, what in ((0, OLD_TAG), (1, NEW_TAG), (2, "XOR")):
        if O.find_layer(ln, dt) is None:
            continue
        entries.append(
            "  <properties>\n"
            "   <frame-color>%s</frame-color>\n   <fill-color>%s</fill-color>\n"
            "   <dither-pattern>I%d</dither-pattern>\n   <visible>%s</visible>\n"
            "   <name>%d/%d  %s  [%s]</name>\n   <source>%d/%d@1</source>\n"
            "  </properties>" % (COLORS[dt], COLORS[dt], 5 + dt, "true",
                                 ln, dt, LAYER_NAMES.get(ln, "?"), what, ln, dt))
open(LYP, "w").write('<?xml version="1.0" encoding="utf-8"?>\n<layer-properties>\n%s\n</layer-properties>\n'
                     % "\n".join(entries))
print("\nwrote %s\n      %s\nopen: klayout %s -l %s" % (OUT, LYP, OUT, LYP))
