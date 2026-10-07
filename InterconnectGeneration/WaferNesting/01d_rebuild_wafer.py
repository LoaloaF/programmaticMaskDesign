"""Stage 01d -- placements.json + sources + template  ->  wafer GDS, then check it.

Builds the wafer 01c_extract_exact_placements.py recorded, from the source designs: each
cell is its source, layer-mapped and shifted by the offset 01c measured; TOP's own shapes
are the template layers 01c matched; every placement is the exact transform 01c read off.

Then it checks the result against the target wafer -- not against what this script meant
to do -- and ends with GEOMETRY IDENTICAL or a list of what differs:

  1. the same cells, and per cell, per layer: XOR = 0 and the same polygon count;
  2. TOP's own shapes, per layer: XOR = 0;
  3. the same placements -- cell, angle, mirror, magnification, integer-dbu displacement;
  4. the whole wafer flattened, per layer: XOR = 0.

Byte identity is reported but not required. GDS files carry creation timestamps and the
writer's own record order, so two files with identical geometry rarely match byte for byte.

SWAPPING CONTENT. To put new content at the recorded placements (a revised dummy, say),
point that cell's entry in config.SOURCES at the new file and run this stage alone. It warns
that the source no longer matches what 01c recorded and builds with the new file; check 1
then reports the difference, which is the point.

RUN WITH KLAYOUT:
    export WAFERNEST_CONFIG=config_rev2
    /Applications/KLayout/klayout.app/Contents/MacOS/klayout -b -r WaferNesting/01d_rebuild_wafer.py

Overwrites its own output (runs/<RUN_TAG>/wafer_<RUN_TAG>.gds): it is fully determined by
placements.json and the sources, so there is nothing in it to lose.
"""
import importlib, json, os, sys

import pya

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
C = importlib.import_module(os.environ.get("WAFERNEST_CONFIG", "config_rev2"))
import exact_io as X

if not os.path.exists(C.PLACEMENTS_PATH):
    raise SystemExit("missing %s -- run 01c_extract_exact_placements.py first" % C.PLACEMENTS_PATH)
P = json.load(open(C.PLACEMENTS_PATH))
dbu = P["target"]["dbu_um"]
TOP = P["target"]["top"]

# --- build ------------------------------------------------------------------------
L = pya.Layout()
L.dbu = dbu
layer_index = {}


def li(ld):
    ld = tuple(ld)
    if ld not in layer_index:
        layer_index[ld] = L.layer(pya.LayerInfo(*ld))
    return layer_index[ld]


cells, changed = {}, []
print("cells:")
for rec in P["cells"]:
    name = rec["name"]
    src = C.SOURCES[name]["file"] if name in C.SOURCES else rec["source"]
    layers = C.SOURCES[name]["layers"] if name in C.SOURCES else rec["layers"]
    note = ""
    if os.path.abspath(src) != os.path.abspath(rec["source"]):
        note = "  (source SWAPPED, 01c recorded %s)" % os.path.basename(rec["source"])
        changed.append(name)
    elif X.md5(src) != rec["source_md5"]:
        note = "  (source CHANGED since 01c -- md5 differs)"
        changed.append(name)
    polys, _ = X.source_polygons(src, layers, dbu)
    dx, dy = rec["offset_dbu"]
    cell = L.create_cell(name)
    for ld, ps in polys.items():
        shapes = cell.shapes(li(ld))
        for p in ps:
            shapes.insert(p.moved(dx, dy))
    cells[name] = cell
    print("  %-22s <- %s  offset (%+.3f, %+.3f) um%s"
          % (name, os.path.basename(src), dx * dbu, dy * dbu, note))

top = L.create_cell(TOP)
tmpl = X.template_polygons(P["template"]["path"], P["template"]["keep_radius_mm"], dbu)
for wl, tl in P["template"]["top_layers"].items():
    ld = tuple(int(v) for v in wl.split("/"))
    shapes = top.shapes(li(ld))
    for p in tmpl[tuple(tl)]:
        shapes.insert(p)
for r in P["instances"]:
    top.insert(pya.CellInstArray(cells[r["cell"]].cell_index(), X.trans_from_record(r)))
print("TOP: template layers %s; %d placements" % (", ".join(sorted(P["template"]["top_layers"])),
                                                  len(P["instances"])))

os.makedirs(C.OUT_DIR, exist_ok=True)
opt = pya.SaveLayoutOptions()
opt.format = "GDS2"
if P["target"].get("libname"):
    opt.gds2_libname = P["target"]["libname"]
L.write(C.WAFER_OUT, opt)
print("\nwrote %s" % C.WAFER_OUT)

# --- check against the target -----------------------------------------------------
target = P["target"]["path"]
if not os.path.exists(target):
    print("target %s not found -- built, but NOT checked" % target)
    raise SystemExit(0)

T = pya.Layout(); T.read(target)
B = pya.Layout(); B.read(C.WAFER_OUT)      # re-read what was WRITTEN, not the in-memory copy
fails = []
print("\nchecking against %s" % os.path.basename(target))

# 1. cells
tnames = {c.name for c in T.each_cell()}
bnames = {c.name for c in B.each_cell()}
if tnames != bnames:
    fails.append("cell names differ: only in target %s, only in rebuild %s"
                 % (sorted(tnames - bnames), sorted(bnames - tnames)))
for name in sorted(tnames & bnames):
    tc, bc = T.cell(name), B.cell(name)
    for ld in sorted(set(X.cell_layers(T, tc)) | set(X.cell_layers(B, bc))):
        a, b = X.cell_layer_polys(T, tc, ld), X.cell_layer_polys(B, bc, ld)
        x = X.xor_um2(a, b, dbu)
        flag = "OK" if x <= C.XOR_TOL_UM2 and len(a) == len(b) else "DIFF"
        if flag != "OK":
            fails.append("%s %d/%d: XOR %.6f um2, polys %d vs %d" % ((name,) + ld + (x, len(a), len(b))))
    print("  1. cell %-22s %s" % (name, "OK" if not any(f.startswith(name + " ") for f in fails) else "DIFF"))

# 3. placements (2 is covered by check 1 on the TOP cell's own shapes)
#
# Cell, mirror, magnification and the integer-dbu displacement must match EXACTLY. The angle
# gets ANGLE_TOL_DEG: KLayout holds a rotation as its sine and cosine, not as the GDS ANGLE
# double, and recomputes the angle on write -- which can move it by one unit in the last
# place (276.751004645 -> 276.7510046450001 on this wafer). That is ~1e-13 um at the wafer
# rim, a million times below the 1 nm grid; check 4 is the proof that it moves nothing.
ANGLE_TOL_DEG = 1e-9


def placements(ly):
    out = []
    for inst in ly.cell(TOP).each_inst():
        r = {"cell": ly.cell(inst.cell_index).name}
        r.update(X.trans_record(inst.cplx_trans))
        out.append(r)
    return sorted(out, key=lambda r: (r["cell"], r["x"], r["y"], r["mirror"], r["mag"], r["angle"]))


pt, pb = placements(T), placements(B)
worst = 0.0
bad = len(pt) != len(pb)
for a, b in zip(pt, pb):
    exact = all(a[k] == b[k] for k in ("cell", "x", "y", "mirror", "mag"))
    d = abs((a["angle"] - b["angle"] + 180.0) % 360.0 - 180.0)
    worst = max(worst, d)
    if not exact or d > ANGLE_TOL_DEG:
        bad = True
if bad:
    fails.append("placements differ (counts %d vs %d, or a cell/position/mirror/mag "
                 "mismatch, or an angle off by more than %g deg)" % (len(pt), len(pb), ANGLE_TOL_DEG))
print("  3. placements: %d target, %d rebuild, positions exact, worst angle deviation %.1e deg  %s"
      % (len(pt), len(pb), worst, "DIFF" if bad else "OK"))

# 4. the whole wafer, flattened
lds = sorted({(i.layer, i.datatype) for ly in (T, B) for i in (ly.get_info(k) for k in ly.layer_indexes())})
for ld in lds:
    rt = pya.Region(); rb = pya.Region()
    k = T.find_layer(*ld)
    if k is not None:
        rt = pya.Region(T.cell(TOP).begin_shapes_rec(k))
    k = B.find_layer(*ld)
    if k is not None:
        rb = pya.Region(B.cell(TOP).begin_shapes_rec(k))
    x = (rt ^ rb).area() * dbu * dbu
    print("  4. flattened %2d/%d  XOR %.6f um2  %s" % (ld + (x, "OK" if x <= C.XOR_TOL_UM2 else "DIFF")))
    if x > C.XOR_TOL_UM2:
        fails.append("flattened %d/%d: XOR %.6f um2" % (ld + (x,)))

same_bytes = X.md5(target) == X.md5(C.WAFER_OUT)
print("\nbyte-identical to the target: %s" % ("yes" if same_bytes else
      "no (expected: timestamps and writer record order differ; geometry is what is checked)"))
if changed:
    print("sources changed since 01c: %s -- differences in those cells are expected" % ", ".join(changed))
if fails:
    print("\n%d DIFFERENCE(S):" % len(fails))
    for f in fails:
        print("  - " + f)
    raise SystemExit(1)
print("GEOMETRY IDENTICAL")
