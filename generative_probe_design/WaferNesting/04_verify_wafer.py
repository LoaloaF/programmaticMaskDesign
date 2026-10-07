"""Stage 04 -- independent verification of the exported wafer.

Re-reads the wafer GDS and re-derives what it SHOULD contain straight from the source
DXFs, rather than trusting anything stage 03 reported. Checks:

  1. instance counts   -- the right number of each design is present;
  2. per-layer shape counts -- for every fab layer, the wafer holds at least
     count x (shapes in that source design). This is the check that catches a layer
     silently deleted by a gap in LAYER_MAP, which is the pipeline's most likely
     and least visible failure;
  3. placement radius  -- no placed geometry reaches past R_MAX_MM.

Check 3 uses the CONVEX HULL of each cell's real shapes, never a bounding box. These
pieces are bottle-shaped: on the 2026-07 designs a bbox reads 52.4 mm, and the
axis-aligned bbox of a rotated piece 59.6 mm, where the geometry actually reaches
46.993 mm. A bbox test here fails designs that are perfectly fine.

NOT checked here: inter-piece clearance. That is enforced in stage 02 against the
footprints, and nothing downstream re-tests the true shapes against each other -- which
is why an under-covering footprint in stage 01 is a real risk, not a cosmetic one.

RUN WITH KLAYOUT:
    /Applications/klayout.app/Contents/MacOS/klayout -b -r WaferNesting/04_verify_wafer.py

Override the target with CHECK_GDS=<path> to verify some other wafer file.
"""
import pya, os, sys, json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import importlib
C = importlib.import_module(os.environ.get("WAFERNEST_CONFIG", "config_64ch_4shank"))

GDS = os.environ.get("CHECK_GDS", C.WAFER_OUT)
if not os.path.isabs(GDS):
    GDS = os.path.join(C.HERE, GDS)
fails = []


def count_by_ld(layout, cell):
    """{(layer, datatype): shape count} over the cell and everything beneath it."""
    out = {}
    for li in layout.layer_indexes():
        info = layout.get_info(li)
        ld = (info.layer, info.datatype)
        n = 0
        it = cell.begin_shapes_rec(li)
        while not it.at_end():
            n += 1
            it.next()
        if n:
            out[ld] = out.get(ld, 0) + n
    return out


def source_counts(dxf):
    """Per mapped (layer, datatype) shape counts for one source DXF, as stage 03 maps it."""
    ly = pya.Layout()
    opt = pya.LoadLayoutOptions(); opt.dxf_unit = 1.0; opt.dxf_polyline_mode = 2
    ly.read(dxf, opt)
    for li in list(ly.layer_indexes()):
        nm = ly.get_info(li).name.lower()
        if nm in C.LAYER_MAP:
            ly.set_info(li, pya.LayerInfo(*C.LAYER_MAP[nm]))
        else:
            ly.clear_layer(li)
    return count_by_ld(ly, ly.top_cell())


def convex_hull(pts):
    """Monotone chain. Max radius over a point set == max radius over its hull."""
    pts = sorted(set(pts))
    if len(pts) < 3:
        return pts
    def half(seq):
        out = []
        for p in seq:
            while len(out) >= 2:
                (x1, y1), (x2, y2) = out[-2], out[-1]
                if (x2 - x1) * (p[1] - y1) - (y2 - y1) * (p[0] - x1) > 0:
                    break
                out.pop()
            out.append(p)
        return out[:-1]
    return half(pts) + half(reversed(pts))


def cell_hull(layout, cell):
    """Convex hull of every real shape in the cell, in cell coordinates (dbu)."""
    pts = []
    for li in layout.layer_indexes():
        it = cell.begin_shapes_rec(li)
        while not it.at_end():
            s, t = it.shape(), it.trans()
            p = None
            if s.is_polygon():
                p = s.polygon
            elif s.is_box():
                p = pya.Polygon(s.box)
            elif s.is_path():
                p = s.path.polygon()
            if p is not None:
                for v in p.transformed(t).each_point_hull():
                    pts.append((v.x, v.y))
            it.next()
    return convex_hull(pts)


if not os.path.exists(GDS):
    print("FAIL: %s does not exist" % GDS)
    sys.exit(1)
print("verifying %s\n" % GDS)

wafer = pya.Layout(); wafer.read(GDS); wtop = wafer.top_cell()
dbu = wafer.dbu   # um per dbu

EXPECT = {"CONN_%s" % d["key"].upper(): (C.design_dxf(d), d["count"])
          for d in C.DESIGNS}
TOTAL = sum(d["count"] for d in C.DESIGNS)

# --- 1. instance counts -------------------------------------------------------
seen, insts = {}, []
for inst in wtop.each_inst():
    nm = wafer.cell(inst.cell_index).name
    seen[nm] = seen.get(nm, 0) + 1
    insts.append((nm, inst))
print("instances by cell: %s" % seen)
for nm, (_dxf, n) in EXPECT.items():
    if seen.get(nm, 0) != n:
        fails.append("expected %d instances of %s, found %d" % (n, nm, seen.get(nm, 0)))
total = sum(seen.get(nm, 0) for nm in EXPECT)
if total != TOTAL:
    fails.append("expected %d connector instances in total, found %d" % (TOTAL, total))

# --- 2. per-layer shape counts ------------------------------------------------
print()
expected_ld = {}
for nm, (dxf, n) in EXPECT.items():
    for ld, c in source_counts(dxf).items():
        expected_ld[ld] = expected_ld.get(ld, 0) + n * c
got_ld = count_by_ld(wafer, wtop)
for ld in sorted(set(expected_ld) | set(got_ld)):
    exp, got = expected_ld.get(ld, 0), got_ld.get(ld, 0)
    # the wafer template contributes its own outline/mark shapes, so got may exceed exp
    print("  layer %2d/%d: connectors expect %8d, wafer has %8d   %s"
          % (ld[0], ld[1], exp, got, "OK" if got >= exp else "MISSING"))
    if got < exp:
        fails.append("layer %d/%d: %d shapes expected, wafer has only %d" % (ld[0], ld[1], exp, got))
if not expected_ld:
    fails.append("no source shapes mapped at all -- LAYER_MAP matches none of the DXF layers")

# --- 3. placement radius ------------------------------------------------------
print()
hulls = {}
for nm in EXPECT:
    idx = wafer.cell_by_name(nm)
    if idx < 0:
        continue
    hulls[nm] = cell_hull(wafer, wafer.cell(idx))
    print("  hull of %-20s: %d points" % (nm, len(hulls[nm])))

worst, worst_nm = 0.0, None
for nm, inst in insts:
    if nm not in hulls:
        continue
    tr = inst.cplx_trans
    for x, y in hulls[nm]:
        p = tr.trans(pya.Point(x, y))
        r = ((p.x * dbu / 1000.0) ** 2 + (p.y * dbu / 1000.0) ** 2) ** 0.5
        if r > worst:
            worst, worst_nm = r, nm
print("\nmax placed-geometry radius = %.3f mm (limit %.1f mm, worst piece %s)"
      % (worst, C.R_MAX_MM, worst_nm))
if worst > C.R_MAX_MM:
    fails.append("placed geometry reaches r=%.3f mm on %s, past the %.1f mm limit"
                 % (worst, worst_nm, C.R_MAX_MM))

# --- 3b. mask polarity: every dark-field mask carries the INVERTED vernier ------
# Metal is the first mask and the only bright-field one; every other mask is dark field,
# where a positive vernier prints as slits in chrome and blinds the aligner to the metal
# scale underneath. Stage 03 replaces those with the inverted stencil. Checked here on the
# finished GDS, by AREA rather than by shape count, so it stays true whatever the mask
# shop's tooling does to the polygon count.
print()
_stencil_area = None
_band_y = int(C.MARK_BAND_Y_UM / dbu)
_wlo, _whi = C.MARK_VERNIER_W_UM

def _vernier_areas(lay, dt):
    """(x_mm, area_um2) of every vernier-sized mark cluster on a layer of the wafer."""
    li = wafer.find_layer(pya.LayerInfo(lay, dt))
    if li is None:
        return []
    band = pya.Region(pya.Box(int(-C.TEMPLATE_KEEP_RADIUS_MM * 1e6 / (dbu * 1000)), -_band_y,
                              int(C.TEMPLATE_KEEP_RADIUS_MM * 1e6 / (dbu * 1000)), _band_y))
    reg = (pya.Region(wtop.shapes(li)) & band).merged()
    grouped = reg.dup(); grouped.size(int(40.0 / dbu)); grouped.merge()
    out = []
    for c in grouped.each():
        bb = c.bbox()
        if _wlo < bb.width() * dbu < _whi:
            out.append((bb.center().x * dbu / 1000.0,
                        (reg & pya.Region(bb)).area() * dbu * dbu))
    return sorted(out)

#PER BAND, not wafer-wide: the template's two mark clusters are not mirror images of one
#another, and their inverted verniers differ by ~830 um^2. Each band is therefore checked
#against the reference mark in its OWN band, which is what stage 03 copied from.
def _band_of(x_mm):
    return "left" if x_mm < 0 else "right"

_ref = {}
for x, a in _vernier_areas(*C.MARK_SELFTEST_LAYER):   # the slot that was inverted all along
    _ref[_band_of(x)] = a
if len(_ref) != 2:
    fails.append("expected an inverted vernier on %d/%d at BOTH ends, found %d"
                 % (C.MARK_SELFTEST_LAYER[0], C.MARK_SELFTEST_LAYER[1], len(_ref)))
if _ref:
    print("  inverted reference (%d/%d): %s"
          % (C.MARK_SELFTEST_LAYER[0], C.MARK_SELFTEST_LAYER[1],
             ", ".join("%s band %.0f um^2" % (b, a) for b, a in sorted(_ref.items()))))
    for lay, dt in [C.MARK_SELFTEST_LAYER] + list(C.MARK_INVERT_LAYERS):
        got = _vernier_areas(lay, dt)
        if len(got) != len(_ref):
            fails.append("layer %d/%d carries %d alignment vernier(s), expected %d"
                         % (lay, dt, len(got), len(_ref)))
        bad = [(x, a) for x, a in got if abs(a - _ref.get(_band_of(x), -1)) > 1.0]
        print("  layer %2d/%d: %d vernier(s) at x = %s mm   %s"
              % (lay, dt, len(got), ", ".join("%+.3f" % x for x, _ in got),
                 "OK (inverted)" if not bad and got else "NOT INVERTED"))
        for x, a in bad:
            fails.append("layer %d/%d: the vernier at x=%+.3f mm covers %.0f um^2, not the "
                         "%.0f um^2 of the inverted form in that band -- it would print as "
                         "slits in chrome and hide the metal scale under it"
                         % (lay, dt, x, a, _ref.get(_band_of(x), float("nan"))))
    _metal = _vernier_areas(*C.MARK_COMPANION)
    print("  layer %2d/%d: %d metal companion(s), %.0f um^2 each -- bright field, left as drawn"
          % (C.MARK_COMPANION[0], C.MARK_COMPANION[1], len(_metal),
             max(a for _, a in _metal) if _metal else 0.0))
    for x, a in _metal:
        if abs(a - _ref.get(_band_of(x), -1)) < 1.0:
            fails.append("the %d/%d metal vernier at x=%+.3f mm has the INVERTED area -- "
                         "metal is the bright-field mask and must keep the positive form"
                         % (C.MARK_COMPANION[0], C.MARK_COMPANION[1], x))

# --- 4. the map's record agrees with the GDS ----------------------------------
# `placement.json` is what 05_map_wafer.py draws from, and a map that disagrees with the
# mask is worse than no map -- it is a document that says a device is something it is not.
# Both come out of stage 03, so they agree unless one was edited or the GDS was rebuilt
# without the other; this check is what makes that detectable rather than silent.
print()
if not os.path.exists(C.PLACEMENT_PATH):
    print("no placement.json -- skipping the map cross-check (re-run 03 to write it)")
else:
    import math
    place = json.load(open(C.PLACEMENT_PATH))

    def rows_from_gds():
        for inst in wtop.each_inst():
            t = inst.cplx_trans
            yield (wafer.cell(inst.cell_index).name, round(t.angle) % 360,
                   round(t.disp.x * dbu / 1000.0, 3), round(t.disp.y * dbu / 1000.0, 3))

    def rows_from_map():
        for q in place:
            off = json.load(open(C.offset_path(q["key"])))
            a = math.radians(q["angle_deg"])
            ca, sa = math.cos(a), math.sin(a)
            cx, cy = off["cx"], off["cy"]
            #  t_place * t_rot * t_center, i.e. the rotated un-centring plus the pose
            yield ("CONN_%s" % q["key"].upper(), q["angle_deg"] % 360,
                   round(q["x_mm"] + (-cx * ca + cy * sa), 3),
                   round(q["y_mm"] + (-cx * sa - cy * ca), 3))

    g, m = sorted(rows_from_gds()), sorted(rows_from_map())
    print("map cross-check: %d instances in the GDS, %d rows in placement.json"
          % (len(g), len(m)))
    if g != m:
        #A multiset difference, not a positional one: the two lists are sorted, so a single
        #edit can shift everything after it and a zip-and-count would report some arbitrary
        #smaller number. This counts rows that genuinely have no partner.
        from collections import Counter
        cg, cm = Counter(g), Counter(m)
        n = sum(((cg - cm) + (cm - cg)).values())
        for row in sorted((cm - cg))[:3]:
            print("  in placement.json but not in the GDS: %s" % (row,))
        for row in sorted((cg - cm))[:3]:
            print("  in the GDS but not in placement.json: %s" % (row,))
        fails.append("placement.json disagrees with the GDS on %d placement row(s) -- the "
                     "wafer map would mislabel devices; re-run 03 then 05" % n)
    else:
        print("  every device's cell, angle and position match to 1 um")

# --- verdict ------------------------------------------------------------------
if fails:
    for f in fails:
        print("FAIL: %s" % f)
    sys.exit(1)
print("ALL CHECKS PASSED")
