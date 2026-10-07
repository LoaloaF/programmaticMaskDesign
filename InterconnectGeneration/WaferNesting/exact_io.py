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
