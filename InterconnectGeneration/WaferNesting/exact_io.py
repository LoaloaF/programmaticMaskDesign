"""Shared by 01c_extract_exact_placements.py and 01d_rebuild_wafer.py.

How a source design becomes the polygons of one wafer cell, and how the wafer template
becomes TOP's own shapes. Both stages go through here, so the stage that PROVES a source
reproduces a cell (01c) and the stage that BUILDS the cell from it (01d) cannot disagree
about what "the source, mapped" means.

Runs inside KLayout's Python (pya + standard library only).
"""
import hashlib
import struct

import pya


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def gds_header(path):
    """LIBNAME, user/meter units and structure order, read straight off the GDS records.

    KLayout does not expose LIBNAME after reading, and the structure order is file order,
    which is worth recording when the aim is to reproduce a file.
    """
    out = {"libname": None, "units": None, "structures": []}
    with open(path, "rb") as f:
        data = f.read()
    i = 0
    while i + 4 <= len(data):
        length, rtype, _ = struct.unpack(">HBB", data[i:i + 4])
        if length < 4:
            break
        body = data[i + 4:i + length]
        if rtype == 0x02:                                   # LIBNAME
            out["libname"] = body.rstrip(b"\0").decode("ascii", "replace")
        elif rtype == 0x03:                                 # UNITS: two 8-byte GDS reals
            out["units"] = [_gds_real(body[0:8]), _gds_real(body[8:16])]
        elif rtype == 0x06:                                 # STRNAME
            out["structures"].append(body.rstrip(b"\0").decode("ascii", "replace"))
        elif rtype == 0x04:                                 # ENDLIB
            break
        i += length
    return out


def _gds_real(b):
    """GDS2 8-byte excess-64 base-16 real -> float."""
    sign = -1.0 if b[0] & 0x80 else 1.0
    exp = (b[0] & 0x7F) - 64
    mant = int.from_bytes(b[1:8], "big") / float(1 << 56)
    return sign * mant * (16.0 ** exp)


def read_layout(path):
    """A design file as a pya.Layout. DXF is read in um with traces as filled polygons --
    the same options stage 03 has always used."""
    ly = pya.Layout()
    if path.lower().endswith(".dxf"):
        opt = pya.LoadLayoutOptions()
        opt.dxf_unit = 1.0          # DXF coordinates are micrometres
        opt.dxf_polyline_mode = 2   # traces become filled polygons
        ly.read(path, opt)
    else:
        ly.read(path)
    return ly


def _layer_key(ly, li, by_name):
    info = ly.get_info(li)
    return info.name.lower() if by_name else (info.layer, info.datatype)


def source_polygons(path, layer_map, target_dbu):
    """{(layer, datatype) on the wafer: [pya.Polygon, ...]} for one source design.

    `layer_map` keys are lower-cased layer NAMES for a DXF and (layer, datatype) tuples for
    a GDS; a source layer it does not list is left out. Hierarchy is flattened and every
    shape becomes a polygon in the TARGET's database units, in source order. Returns
    (polygons, dropped_layer_names).
    """
    ly = read_layout(path)
    tops = ly.top_cells()
    if len(tops) != 1:
        raise SystemExit("%s: expected one top cell, found %s" % (path, [c.name for c in tops]))
    top = tops[0]
    by_name = path.lower().endswith(".dxf")
    scale = pya.ICplxTrans(ly.dbu / target_dbu)
    out, dropped = {}, []
    for li in ly.layer_indexes():
        key = _layer_key(ly, li, by_name)
        if key not in layer_map:
            if not top.bbox_per_layer(li).empty():
                dropped.append(str(key))
            continue
        ld = tuple(layer_map[key])
        it = top.begin_shapes_rec(li)
        while not it.at_end():
            sh = it.shape()
            if sh.is_polygon() or sh.is_box() or sh.is_path() or sh.is_simple_polygon():
                poly = sh.polygon.transformed(it.trans())
                out.setdefault(ld, []).append(poly.transformed(scale) if ly.dbu != target_dbu else poly)
            it.next()
    return out, sorted(dropped)


def template_polygons(path, keep_radius_mm, target_dbu):
    """{(layer, datatype): [pya.Polygon, ...]} of the wafer template, shapes whose bbox centre
    lies beyond `keep_radius_mm` discarded (the off-wafer copy of the mark cluster)."""
    ly = read_layout(path)
    top = ly.top_cells()[0]
    r_max = keep_radius_mm * 1000.0 / ly.dbu        # mm -> template dbu
    scale = pya.ICplxTrans(ly.dbu / target_dbu)
    out = {}
    for li in ly.layer_indexes():
        info = ly.get_info(li)
        it = top.begin_shapes_rec(li)
        while not it.at_end():
            poly = it.shape().polygon.transformed(it.trans())
            c = poly.bbox().center()
            if (c.x ** 2 + c.y ** 2) ** 0.5 <= r_max:
                out.setdefault((info.layer, info.datatype), []).append(
                    poly.transformed(scale) if ly.dbu != target_dbu else poly)
            it.next()
    return out


def invert_marks(tmpl, layers, stencil, companion, selftest, keep_radius_mm, dbu,
                 vernier_w_um=(550.0, 700.0), band_y_um=1000.0):
    """Give each of `layers` the INVERTED vernier in its alignment-mark slot (in `tmpl`, in place).

    The template ships each slot's vernier either POSITIVE (129 separate bars: 6/0, 8/0, 1/0) or
    INVERTED (one polygon with the bars as holes: 3/0, 5/0). A dark-field mask -- every etch mask --
    needs the inverted form: on that plate the positive form is 129 slits in chrome and hides the
    metal scale underneath. The inverted form is copied from the `stencil` layer's slot, registered on
    the `companion` layer (Metal1: an identical cluster sits at the centre of every slot and is the
    feature already on the wafer when the later mask is aligned), one band (left / right of the wafer
    centre) at a time. The transform is proven first on `selftest`, a layer whose inverted mark the
    template already carries: rebuilding it from the stencil must reproduce it exactly.
    (Same procedure as generative_probe_design/WaferNesting/03_export_wafer.py.)
    """
    um = lambda v: int(round(v / dbu))

    def verniers(ld, xlo, xhi):
        reg = region(tmpl.get(tuple(ld), [])) & pya.Region(
            pya.Box(um(xlo), -um(band_y_um), um(xhi), um(band_y_um)))
        reg.merge()
        grouped = reg.dup()
        grouped.size(um(40.0))
        grouped.merge()
        out = [(c.bbox(), reg & pya.Region(c.bbox())) for c in grouped.each()
               if vernier_w_um[0] < c.bbox().width() * dbu < vernier_w_um[1]]
        return sorted(out, key=lambda t: t[0].center().x)

    def nearest(comps, bbox):
        return min(comps, key=lambda t: abs(t[0].center().x - bbox.center().x))[0]

    r = keep_radius_mm * 1000.0
    for xlo, xhi in ((-r, 0.0), (0.0, r)):
        comps = verniers(companion, xlo, xhi)
        stencils = verniers(stencil, xlo, xhi)
        if len(stencils) != 1 or not comps:
            raise SystemExit("band [%.1f, %.1f] mm: found %d stencil marks on %s and %d companions on %s"
                             % (xlo / 1000, xhi / 1000, len(stencils), stencil, len(comps), companion))
        sbb, sten = stencils[0]
        scomp = nearest(comps, sbb)

        def stencil_on(ld):
            found = verniers(ld, xlo, xhi)
            if len(found) != 1:
                raise SystemExit("expected one vernier on %s in band [%.1f, %.1f] mm, found %d"
                                 % (ld, xlo / 1000, xhi / 1000, len(found)))
            bbox, present = found[0]
            comp = nearest(comps, bbox)
            made = sten.dup()
            made.transform(pya.Trans(comp.center().x - scomp.center().x, comp.center().y - scomp.center().y))
            return bbox, present, made

        _, real, rebuilt = stencil_on(selftest)
        if not (rebuilt ^ real).is_empty():
            raise SystemExit("mark self-test FAILED on %s in band [%.1f, %.1f] mm: rebuilding it from %s "
                             "differs by %.4f um2 -- the companion registration is wrong"
                             % (selftest, xlo / 1000, xhi / 1000, stencil, (rebuilt ^ real).area() * dbu * dbu))
        print("  marks [%+.1f, %+.1f] mm: self-test OK (%d/%d rebuilt from %d/%d exactly)"
              % (xlo / 1000, xhi / 1000, selftest[0], selftest[1], stencil[0], stencil[1]))
        for ld in layers:
            bbox, old, new = stencil_on(ld)
            rest = region(tmpl[tuple(ld)]) - pya.Region(bbox)
            tmpl[tuple(ld)] = [p for p in (rest + new).each()]
            print("    %d/%d: positive vernier (%d polys) -> inverted (%d polys) at x = %+.4f mm"
                  % (ld[0], ld[1], old.count(), new.count(), new.bbox().center().x * dbu / 1000))


def write_lyp(path, layers, names, style, order):
    """KLayout layer-properties file: `layers` = [(layer, datatype)], `names` {layer: name},
    `style` {layer: dict(color, pattern, width)}, `order` = layers top -> bottom of the panel.

    KLayout draws entries further DOWN the list in front of those above (verified by rendering), so
    the first entry in `order` is the back of the stack. Layers not in `order` go below it, in
    layer-number order (so in front)."""
    rank = {ln: i for i, ln in enumerate(order)}
    ordered = sorted(layers, key=lambda ld: (rank.get(ld[0], len(order)), ld))
    out = ['<?xml version="1.0" encoding="utf-8"?>', "<layer-properties>"]
    for ld in ordered:
        st = style.get(ld[0], dict(color="#808080", pattern="I1", width=1))
        out += ["  <properties>",
                "    <frame-color>%s</frame-color>" % st["color"],
                "    <fill-color>%s</fill-color>" % st["color"],
                "    <frame-brightness>0</frame-brightness>",
                "    <fill-brightness>0</fill-brightness>",
                "    <dither-pattern>%s</dither-pattern>" % st["pattern"],
                "    <visible>true</visible>", "    <transparent>false</transparent>",
                "    <width>%d</width>" % st["width"], "    <marked>false</marked>",
                "    <name>%s (%d/%d)</name>" % (names.get(ld[0], "unnamed"), ld[0], ld[1]),
                "    <source>%d/%d@1</source>" % ld,
                "  </properties>"]
    out.append("</layer-properties>")
    open(path, "w").write("\n".join(out) + "\n")


def region(polys):
    r = pya.Region()
    for p in polys:
        r.insert(p)
    return r


def cell_layer_polys(layout, cell, ld):
    """The cell's OWN shapes on one layer (no hierarchy below), as a list of polygons."""
    li = layout.find_layer(*ld)
    if li is None:
        return []
    return [s.polygon for s in cell.shapes(li).each() if s.polygon is not None]


def cell_layers(layout, cell):
    """(layer, datatype) pairs on which the cell has shapes of its own."""
    out = []
    for li in layout.layer_indexes():
        if not cell.shapes(li).is_empty():
            info = layout.get_info(li)
            out.append((info.layer, info.datatype))
    return sorted(out)


def xor_um2(a_polys, b_polys, dbu):
    """Area of the symmetric difference, um^2."""
    return (region(a_polys) ^ region(b_polys)).area() * dbu * dbu


def trans_record(t):
    """ICplxTrans (dbu) -> JSON-able dict. Displacement stays in integer dbu, so nothing is
    lost; the angle is the double KLayout read from the file."""
    return {"angle": t.angle, "mirror": bool(t.is_mirror()), "mag": t.mag,
            "x": int(t.disp.x), "y": int(t.disp.y)}


def trans_from_record(r):
    return pya.ICplxTrans(r["mag"], r["angle"], r["mirror"], r["x"], r["y"])
