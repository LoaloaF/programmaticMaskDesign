"""Stage 01c -- an EXISTING wafer GDS  ->  placements.json (exact).

The nesting path (01 -> 02 -> 03) makes a new wafer. This one goes the other way: it takes
a wafer that already exists -- a fabricated one -- and records everything needed to build it
again from its sources, so 01d_rebuild_wafer.py can reproduce it geometrically identically.

It does not trust anything. For every part of the wafer it PROVES where the part comes from,
and refuses to write placements.json if any part cannot be traced:

  1. every CELL is reproduced by its entry in config.SOURCES -- the source, layer-mapped,
     shifted by an offset measured here, must XOR to zero against the cell on every layer;
  2. TOP's OWN shapes (marks, outline) are reproduced by the wafer template -- each wafer
     layer must XOR to zero against one template layer, with the off-wafer mark cluster
     discarded. Which template layers survive, and onto which wafer layers, is recorded;
  3. every PLACEMENT is read off the wafer exactly: integer-dbu displacement, the angle as
     stored, mirror and magnification. Nothing is rounded.

Identical placements (same cell, same transform) are legal in GDS and are kept, since the
goal is to reproduce the wafer, but they are reported -- one is almost certainly a mistake.

RUN WITH KLAYOUT:
    export WAFERNEST_CONFIG=config_rev2
    /Applications/KLayout/klayout.app/Contents/MacOS/klayout -b -r WaferNesting/01c_extract_exact_placements.py
"""
import importlib, json, os, sys

import pya

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
C = importlib.import_module(os.environ.get("WAFERNEST_CONFIG", "config_rev2"))
import exact_io as X

problems, warnings = [], []

W = pya.Layout()
W.read(C.TARGET_GDS)
dbu = W.dbu
tops = W.top_cells()
if len(tops) != 1:
    raise SystemExit("expected one top cell in %s, found %s" % (C.TARGET_GDS, [c.name for c in tops]))
wtop = tops[0]
hdr = X.gds_header(C.TARGET_GDS)

print("target   : %s" % C.TARGET_GDS)
print("           md5 %s, dbu %g um, libname %r, top %r" % (X.md5(C.TARGET_GDS), dbu, hdr["libname"], wtop.name))
print("structures in file order: %s" % ", ".join(hdr["structures"]))

# --- 1. cells ---------------------------------------------------------------------
cells = []
print("\ncells:")
for name in hdr["structures"]:
    if name == wtop.name:
        continue
    cell = W.cell(name)
    if cell.child_instances():
        problems.append("%s: has child instances -- only flat device cells are supported" % name)
    src = C.SOURCES.get(name)
    if src is None:
        problems.append("%s: no entry in SOURCES -- add one in %s" % (name, C.__name__))
        continue
    if not os.path.exists(src["file"]):
        problems.append("%s: source missing: %s" % (name, src["file"]))
        continue
    polys, dropped = X.source_polygons(src["file"], src["layers"], dbu)

    # offset: line up the bounding boxes, then prove it with XOR on every layer
    sbox = pya.Box()
    for ps in polys.values():
        for p in ps:
            sbox += p.bbox()
    cbox = cell.bbox()
    dx, dy = cbox.left - sbox.left, cbox.bottom - sbox.bottom
    moved = {ld: [p.moved(dx, dy) for p in ps] for ld, ps in polys.items()}

    layers = sorted(set(moved) | set(X.cell_layers(W, cell)))
    per_layer, ok = {}, True
    for ld in layers:
        mine = X.cell_layer_polys(W, cell, ld)
        theirs = moved.get(ld, [])
        x = X.xor_um2(mine, theirs, dbu)
        per_layer["%d/%d" % ld] = {"wafer_polys": len(mine), "source_polys": len(theirs),
                                   "xor_um2": round(x, 6)}
        if x > C.XOR_TOL_UM2:
            ok = False
    status = "OK" if ok else "MISMATCH"
    print("  %-22s <- %s" % (name, os.path.basename(src["file"])))
    print("      offset (%+.3f, %+.3f) um   %s" % (dx * dbu, dy * dbu, status))
    for k, v in per_layer.items():
        print("      %-5s wafer %6d  source %6d  XOR %.6f um2" % (k, v["wafer_polys"], v["source_polys"], v["xor_um2"]))
    if dropped:
        print("      source layers not mapped (not part of the cell): %s" % ", ".join(dropped))
    if not ok:
        problems.append("%s: source does not reproduce the cell (XOR above). Wrong source "
                        "file, wrong layer map, or the design changed since fabrication" % name)
    cells.append({"name": name, "source": src["file"], "source_md5": X.md5(src["file"]),
                  "layers": {str(k): list(v) for k, v in src["layers"].items()},
                  "offset_dbu": [int(dx), int(dy)], "check": per_layer})

# --- 2. TOP's own shapes against the template ---------------------------------------
print("\nTOP's own shapes vs template %s (keep radius %.1f mm):"
      % (os.path.basename(C.WAFER_TEMPLATE), C.TEMPLATE_KEEP_RADIUS_MM))
tmpl = X.template_polygons(C.WAFER_TEMPLATE, C.TEMPLATE_KEEP_RADIUS_MM, dbu)
top_layers = {}
for ld in X.cell_layers(W, wtop):
    mine = X.cell_layer_polys(W, wtop, ld)
    # same layer number first, then any other template layer
    candidates = [ld] + [t for t in sorted(tmpl) if t != ld]
    found = None
    for t in candidates:
        if t in tmpl and X.xor_um2(mine, tmpl[t], dbu) <= C.XOR_TOL_UM2:
            found = t
            break
    if found is None:
        problems.append("TOP %d/%d: no template layer reproduces it" % ld)
        print("  %d/%d  %6d polys  NOT REPRODUCIBLE from the template" % (ld + (len(mine),)))
        continue
    top_layers["%d/%d" % ld] = list(found)
    print("  %d/%d  %6d polys  <- template %d/%d (%d polys)  XOR 0"
          % (ld + (len(mine),) + found + (len(tmpl[found]),)))
used = {tuple(v) for v in top_layers.values()}
dropped_t = sorted(t for t in tmpl if t not in used)
print("  template layers NOT on the wafer: %s" % ", ".join("%d/%d" % t for t in dropped_t))

# --- 3. placements ------------------------------------------------------------------
instances, seen = [], {}
for inst in wtop.each_inst():
    if inst.is_regular_array():
        problems.append("array instance of %s -- not supported" % W.cell(inst.cell_index).name)
        continue
    rec = {"cell": W.cell(inst.cell_index).name}
    rec.update(X.trans_record(inst.cplx_trans))
    key = json.dumps(rec, sort_keys=True)
    if key in seen:
        warnings.append("duplicate placement: %s at (%.3f, %.3f) mm, %.1f deg -- identical to "
                        "placement #%d. Kept, because the wafer has it."
                        % (rec["cell"], rec["x"] * dbu / 1000, rec["y"] * dbu / 1000,
                           rec["angle"], seen[key]))
    else:
        seen[key] = len(instances)
    rec["i"] = len(instances)
    instances.append(rec)

print("\nplacements: %d" % len(instances))
for name in [c["name"] for c in cells]:
    print("  %-22s x %d" % (name, sum(1 for r in instances if r["cell"] == name)))

for w in warnings:
    print("\nWARNING: " + w)

if problems:
    print("\nNOT WRITTEN -- %d problem(s):" % len(problems))
    for p in problems:
        print("  - " + p)
    raise SystemExit(1)

os.makedirs(C.OUT_DIR, exist_ok=True)
out = {
    "target": {"path": C.TARGET_GDS, "md5": X.md5(C.TARGET_GDS), "dbu_um": dbu,
               "libname": hdr["libname"], "units": hdr["units"], "top": wtop.name,
               "structure_order": hdr["structures"]},
    "template": {"path": C.WAFER_TEMPLATE, "md5": X.md5(C.WAFER_TEMPLATE),
                 "keep_radius_mm": C.TEMPLATE_KEEP_RADIUS_MM,
                 "top_layers": top_layers, "dropped": ["%d/%d" % t for t in dropped_t]},
    "cells": cells,
    "instances": instances,
    "warnings": warnings,
}
with open(C.PLACEMENTS_PATH, "w") as f:
    json.dump(out, f, indent=1)
print("\nwrote %s" % C.PLACEMENTS_PATH)
print("every part of the wafer traced to a source -- now: 01d_rebuild_wafer.py")
