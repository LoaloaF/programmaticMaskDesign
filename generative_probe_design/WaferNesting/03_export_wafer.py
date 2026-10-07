"""Stage 03 -- poses + design DXFs  ->  wafer GDS.

Nothing is "extracted" here. The footprints from stage 01 were only a collision proxy;
this stage goes back to the ORIGINAL DXFs, remaps their layers for fab, and places the
complete geometry at the poses stage 02 found.

Placement for each piece is   t_place * t_rot * t_center   , i.e. undo the centring
that stage 01 applied (using that design's own offset JSON), rotate to the pose angle,
then translate onto the wafer. Using an offset from a DIFFERENT footprint run than the
poses came from will shift pieces off their nested slots -- config.RUN_TAG keeps a run's
footprints, offsets and poses together in one folder precisely to prevent that.

Designs sharing a kind are dealt out across that kind's poses in a SEEDED SHUFFLE, so the
four magnetic-ID variants are scattered over the wafer instead of correlating with the
order the nester happened to seat them. Counts stay exactly as the config asks.

The template is not just a backdrop. Every target layer in LAYER_MAP is one the template
already carries alignment marks on, so a device layer and its mark ship as a single mask.
This stage also strips the template's third, out-of-wafer copy of the mark cluster and the
mark layers no mask uses.

RUN WITH KLAYOUT:
    /Applications/klayout.app/Contents/MacOS/klayout -b -r WaferNesting/03_export_wafer.py

WARNING: any DXF layer missing from config.LAYER_MAP is silently DELETED. This script
prints every layer it maps and every layer it drops -- read that output.
"""
import pya, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import importlib
C = importlib.import_module(os.environ.get("WAFERNEST_CONFIG", "config_64ch_4shank"))

if not os.path.exists(C.POSES_PATH):
    raise SystemExit("missing %s -- run nest_tiler.py first" % C.POSES_PATH)
if os.path.exists(C.WAFER_OUT):
    raise SystemExit("refusing to overwrite %s -- bump RUN_TAG in config.py or delete it"
                     % C.WAFER_OUT)

poses = json.load(open(C.POSES_PATH))
expected = sum(C.count_of(k) for k in C.kinds())
assert len(poses) == expected, "expected %d poses, got %d" % (expected, len(poses))

# --- assign a concrete design to each pose ------------------------------------
# poses carry a kind; within a kind, cycle through that kind's designs so each is
# spread across the wafer. Counts are asserted afterwards.
import random
pool = {k: [] for k in C.kinds()}
for d in C.DESIGNS:
    pool[d["kind"]].extend([d["key"]] * d["count"])
# Seeded so a re-run reproduces the same wafer, shuffled so a variant is not correlated
# with where the nester happened to seat it.
rng = random.Random(getattr(C, "VARIANT_SEED", 12345))
for k in pool:
    rng.shuffle(pool[k])
cursor = {k: 0 for k in C.kinds()}
assign = []
for kind, *_ in poses:
    assign.append(pool[kind][cursor[kind]])
    cursor[kind] += 1
for d in C.DESIGNS:
    got = assign.count(d["key"])
    assert got == d["count"], "expected %d x %s, got %d" % (d["count"], d["key"], got)

wafer = pya.Layout(); wafer.read(C.WAFER_TEMPLATE); wtop = wafer.top_cell()
print("wafer template: %s" % os.path.basename(C.WAFER_TEMPLATE))

# --- clean the template -------------------------------------------------------
# 1. the stray third mark cluster, centred near x = +96 mm, is outside the wafer entirely.
keep = pya.Box(int(-C.TEMPLATE_KEEP_RADIUS_MM * 1e6), int(-C.TEMPLATE_KEEP_RADIUS_MM * 1e6),
               int(C.TEMPLATE_KEEP_RADIUS_MM * 1e6), int(C.TEMPLATE_KEEP_RADIUS_MM * 1e6))
for li in wafer.layer_indexes():
    before = wtop.shapes(li).size()
    reg = (pya.Region(wtop.shapes(li)) & pya.Region(keep))
    wtop.shapes(li).clear()
    wtop.shapes(li).insert(reg)
    if before != wtop.shapes(li).size():
        print("  template %s: dropped %d shape(s) outside the wafer"
              % (wafer.get_info(li), before - wtop.shapes(li).size()))
# 2. MASK POLARITY: give every dark-field mask the INVERTED vernier.
#    Metal is the first mask and the only bright-field one -- it is untouched, and it is
#    what defines the marks on the wafer. Every later mask is dark field, where the drawn
#    shapes are the openings, so a vernier drawn as 129 separate bars comes out as 129
#    slits in chrome and hides the metal scale underneath it. The inverted form -- one
#    polygon with those bars as holes -- comes out as a clear window with the scale in
#    chrome, and the metal mark below stays visible. See config.MARK_* for the full story.
DBU = wafer.dbu                                     # um per database unit

def _verniers(lay, dt, xlo_um, xhi_um):
    """Every vernier-sized cluster of a layer inside one mark band, as (bbox, Region)."""
    li = wafer.find_layer(lay, dt)
    if li is None:
        return []
    band = pya.Region(pya.Box(int(xlo_um / DBU), int(-C.MARK_BAND_Y_UM / DBU),
                              int(xhi_um / DBU), int(C.MARK_BAND_Y_UM / DBU)))
    reg = (pya.Region(wtop.shapes(li)) & band).merged()
    grouped = reg.dup(); grouped.size(int(40.0 / DBU)); grouped.merge()
    wlo, whi = C.MARK_VERNIER_W_UM
    out = [(c.bbox(), reg & pya.Region(c.bbox()))
           for c in grouped.each() if wlo < c.bbox().width() * DBU < whi]
    return sorted(out, key=lambda t: t[0].center().x)

def _nearest(companions, bbox):
    """The metal companion this mark belongs to -- slots are 700 um apart, so nearest wins."""
    return min(companions, key=lambda t: abs(t[0].center().x - bbox.center().x))[0]

#The two bands, left and right of the wafer centre. They are handled SEPARATELY: a band's
#stencil serves that band's slots, so whatever the two ends do differently (they are not
#mirror images of each other, and the mark sits a fraction off its companion by a different
#amount on each side) never enters the transform.
_bands = [(-C.TEMPLATE_KEEP_RADIUS_MM * 1000.0, 0.0), (0.0, C.TEMPLATE_KEEP_RADIUS_MM * 1000.0)]
for _xlo, _xhi in _bands:
    _comps = _verniers(C.MARK_COMPANION[0], C.MARK_COMPANION[1], _xlo, _xhi)
    _stencils = _verniers(C.MARK_STENCIL[0], C.MARK_STENCIL[1], _xlo, _xhi)
    assert len(_stencils) == 1 and _comps, (
        "band [%.1f, %.1f] mm: found %d stencil marks on %d/%d and %d companions on %d/%d"
        % (_xlo / 1000, _xhi / 1000, len(_stencils), C.MARK_STENCIL[0], C.MARK_STENCIL[1],
           len(_comps), C.MARK_COMPANION[0], C.MARK_COMPANION[1]))
    _sbb, _sten = _stencils[0]
    _scomp = _nearest(_comps, _sbb)

    def _stencil_on(lay, dt):
        """The stencil translated onto `lay`'s slot, companion to companion.

        Returns (slot bbox, the mark that is there now, the inverted mark for it). Nothing
        is written -- the caller decides, which is what lets the self-test below compare
        against a slot it must not touch.
        """
        found = _verniers(lay, dt, _xlo, _xhi)
        assert len(found) == 1, ("expected exactly one vernier on %d/%d in band "
                                 "[%.1f, %.1f] mm, found %d"
                                 % (lay, dt, _xlo / 1000, _xhi / 1000, len(found)))
        bbox, present = found[0]
        comp = _nearest(_comps, bbox)
        made = _sten.dup()
        made.transform(pya.Trans(comp.center().x - _scomp.center().x,
                                 comp.center().y - _scomp.center().y))
        return bbox, present, made

    #PROVE THE TRANSFORM BEFORE USING IT. 3/0 is a slot whose inverted mark the template
    #already carries, so rebuilding it from the stencil must reproduce it exactly. If this
    #ever fails, every copied mark below is in the wrong place and the wafer is scrap.
    _tbb, _real, _rebuilt = _stencil_on(*C.MARK_SELFTEST_LAYER)
    _xor = _rebuilt ^ _real
    assert _xor.is_empty(), (
        "mark self-test FAILED in band [%.1f, %.1f] mm: rebuilding %d/%d's inverted vernier "
        "from the %d/%d stencil differs from the template's own by %.4f um^2 -- the "
        "companion registration is wrong, do NOT ship this wafer"
        % (_xlo / 1000, _xhi / 1000, C.MARK_SELFTEST_LAYER[0], C.MARK_SELFTEST_LAYER[1],
           C.MARK_STENCIL[0], C.MARK_STENCIL[1], _xor.area() * DBU * DBU))
    print("  marks [%+.1f, %+.1f] mm: self-test OK (%d/%d rebuilt from %d/%d exactly)"
          % (_xlo / 1000, _xhi / 1000, C.MARK_SELFTEST_LAYER[0], C.MARK_SELFTEST_LAYER[1],
             C.MARK_STENCIL[0], C.MARK_STENCIL[1]))

    for _lay, _dt in C.MARK_INVERT_LAYERS:
        _bbox, _old, _new = _stencil_on(_lay, _dt)
        _li = wafer.find_layer(_lay, _dt)
        _rest = pya.Region(wtop.shapes(_li)) - pya.Region(_bbox)    # everything but that mark
        wtop.shapes(_li).clear()
        wtop.shapes(_li).insert(_rest + _new)
        print("    %d/%d: positive vernier (%d polys, %.0f um^2) -> inverted "
              "(%d polys, %.0f um^2) at x = %+.4f mm"
              % (_lay, _dt, _old.size(), _old.area() * DBU * DBU, _new.size(),
                 _new.area() * DBU * DBU, _new.bbox().center().x * DBU / 1000))

# 3. mark layers no mask uses -- AFTER the stencil above has been copied off 5/0
for lay, dt in C.MARK_LAYERS_UNUSED:
    li = wafer.find_layer(lay, dt)
    if li is not None:
        print("  template %d/%d: dropping %d unused mark shape(s)" % (lay, dt, wtop.shapes(li).size()))
        wafer.delete_layer(li)

cells = {}
for d in C.DESIGNS:
    dxf = C.design_dxf(d)
    conn = pya.Layout()
    opt = pya.LoadLayoutOptions()
    opt.dxf_unit = 1.0          # DXF coordinates are micrometres
    opt.dxf_polyline_mode = 2   # traces become filled polygons
    conn.read(dxf, opt)
    mapped, dropped = [], []
    for li in list(conn.layer_indexes()):
        nm = conn.get_info(li).name.lower()
        if nm in C.LAYER_MAP:
            conn.set_info(li, pya.LayerInfo(*C.LAYER_MAP[nm]))
            mapped.append("%s->%d/%d" % (nm, C.LAYER_MAP[nm][0], C.LAYER_MAP[nm][1]))
        else:
            conn.clear_layer(li); dropped.append(nm)
    cc = wafer.create_cell("CONN_%s" % d["key"].upper())
    cc.copy_tree(conn.top_cell())
    off = json.load(open(C.offset_path(d["key"])))
    cells[d["key"]] = (cc, int(round(off["cx"] * 1e6)), int(round(off["cy"] * 1e6)))
    print("\n%s  <- %s" % (cc.name, d["dxf"]))
    print("   mapped : %s" % ", ".join(mapped))
    print("   DROPPED: %s" % (", ".join(dropped) if dropped else "(none)"))
    if dropped:
        print("   ^^ confirm every dropped layer is one you meant to drop")

for (kind, theta, xmm, ymm), key in zip(poses, assign):
    cc, cx_dbu, cy_dbu = cells[key]
    t_center = pya.ICplxTrans(1.0, 0.0,          False, -cx_dbu, -cy_dbu)
    t_rot    = pya.ICplxTrans(1.0, float(theta), False, 0, 0)
    t_place  = pya.ICplxTrans(1.0, 0.0,          False, int(round(xmm * 1e6)), int(round(ymm * 1e6)))
    wtop.insert(pya.CellInstArray(cc.cell_index(), t_place * t_rot * t_center))

# --- name the layers from their DXF names ------------------------------------
# The names are set in memory here; how far each output format can carry them differs, and
# the format is the limit, not KLayout:
#
#   .oas   OASIS has a LAYERNAME record. The names survive, and a fab or a colleague who
#          opens it sees `Metal`, not `7/0`. Written alongside the GDS for that reason.
#   .gds   GDS2 has no such record -- there is no SaveLayoutOptions switch for it, and one
#          written with names and read back comes out unnamed (verified). The names go in a
#          KLayout layer-properties sidecar with the same stem instead, which KLayout offers
#          to load when the GDS is opened.
#
# The in-memory set_info below is what the print-out, the .lyp and stage 04 all read.
for lay, name in getattr(C, "LAYER_NAMES", {}).items():
    li = wafer.find_layer(lay, 0)
    if li is not None:
        wafer.set_info(li, pya.LayerInfo(lay, 0, name))

# The DXFs carry empty CAD bookkeeping layers (0, defpoints). clear_layer() emptied them
# but left the layer registered, which is invisible in the GDS (empty layers are not
# written) yet would show up in the .lyp. Drop them so both agree.
for li in list(wafer.layer_indexes()):
    if pya.Region(wtop.begin_shapes_rec(li)).is_empty():
        print("  dropping empty layer %s" % wafer.get_info(li))
        wafer.delete_layer(li)

COLORS = ["#ff00ff", "#00c8ff", "#ff4040", "#c8a000", "#40ff40", "#8080ff"]
lyp = ['<?xml version="1.0" encoding="utf-8"?>', "<layer-properties>"]
for i, li in enumerate(sorted(wafer.layer_indexes(), key=lambda j: wafer.get_info(j).layer)):
    info = wafer.get_info(li)
    lyp += ["  <properties>",
            "    <frame-color>%s</frame-color>" % COLORS[i % len(COLORS)],
            "    <fill-color>%s</fill-color>" % COLORS[i % len(COLORS)],
            "    <dither-pattern>I%d</dither-pattern>" % (i % 8 + 1),
            "    <visible>true</visible>", "    <transparent>false</transparent>",
            "    <width>1</width>", "    <marked>false</marked>",
            "    <name>%s (%d/%d)</name>" % (info.name or "unnamed", info.layer, info.datatype),
            "    <source>%d/%d@1</source>" % (info.layer, info.datatype),
            "  </properties>"]
lyp.append("</layer-properties>")
lyp_path = os.path.splitext(C.WAFER_OUT)[0] + ".lyp"
open(lyp_path, "w").write("\n".join(lyp) + "\n")
print("\nwrote %s  (layer names for KLayout; GDS2 cannot store them)"
      % os.path.basename(lyp_path))

print("\nfinal layers:")
for li in sorted(wafer.layer_indexes(), key=lambda i: wafer.get_info(i).layer):
    info = wafer.get_info(li)
    reg = pya.Region(wtop.begin_shapes_rec(li)); reg.merge()
    b = reg.bbox().to_dtype(wafer.dbu)
    print("  %-5s %-13s %7d shapes  x[%8.3f,%8.3f] mm"
          % ("%d/%d" % (info.layer, info.datatype), info.name or "(unnamed)",
             reg.count(), b.left / 1000, b.right / 1000))

# GDS2 is a 1970s format with hard structural limits, and the merged Etching mask blows
# through both: it is ONE polygon of ~20k vertices per design, against a BOUNDARY limit of
# 8191 points and a record-length field that is a SIGNED 16-bit int, i.e. 32 KB. Written
# unsplit, KLayout reads its own file back with "record length larger than 0x8000 --
# interpreting as unsigned"; a stricter reader on the fab's side is entitled to reject it
# outright. Capping vertices per polygon at 4000 keeps every XY record inside 32000 bytes,
# so no record needs that reinterpretation. The split pieces abut exactly and the union is
# unchanged -- verified against the OASIS twin, which has no such limits.
opt = pya.SaveLayoutOptions()
opt.gds2_max_vertex_count = 4000
opt.gds2_multi_xy_records = False
# Which variant ended up where, for 05_map_wafer.py and for anyone reading the wafer back.
# The deal above is a seeded shuffle; writing the result means nothing downstream has to
# reproduce it and risk disagreeing with the GDS that actually shipped.
json.dump([{"i": i, "key": key, "kind": kind, "angle_deg": theta,
            "x_mm": xmm, "y_mm": ymm}
           for i, ((kind, theta, xmm, ymm), key) in enumerate(zip(poses, assign))],
          open(C.PLACEMENT_PATH, "w"), indent=1)
print("wrote %s  (variant -> pose, for the map)" % os.path.basename(C.PLACEMENT_PATH))

wafer.write(C.WAFER_OUT, opt)
print("\nwrote %s" % C.WAFER_OUT)

# The same layout in OASIS, which unlike GDS2 stores the layer names in the file itself.
oas_path = os.path.splitext(C.WAFER_OUT)[0] + ".oas"
wafer.write(oas_path)
print("wrote %s  (same layout; OASIS carries the layer names natively)"
      % os.path.basename(oas_path))
for d in C.DESIGNS:
    print("  %-14s x %d" % (d["key"], assign.count(d["key"])))
print("\nnow verify:  klayout -b -r WaferNesting/04_verify_wafer.py")
