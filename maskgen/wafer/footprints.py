"""Silhouette extraction from a design DXF.

Lifted unchanged from pack_new_extract_footprints_chamfered.py. The pipeline uses every
layer -- see README.md, "Things that will bite you" #4.

TWO ASSUMPTIONS THAT FAIL SILENTLY, worth checking on any new design:

  1. DXF coordinates are in MICROMETRES (UM_TO_MM below). A connector reads as
     ~50000 x 15000 in the file, not 50 x 15. Do not trust $INSUNITS in the header,
     it is frequently nominal and wrong -- print the extent instead.
  2. Only LWPOLYLINE and HATCH entities are read. CIRCLE, ARC, SPLINE and block
     references (INSERT) are invisible here and must be exploded/flattened first.

Neither raises; both produce a plausible-looking wrong footprint. Always compare the
printed area and bbox against what you expect the design to be.
"""
import os
import ezdxf
from ezdxf import path as ezpath
from shapely.geometry import Polygon
from shapely.ops import unary_union
from shapely.affinity import translate

UM_TO_MM = 0.001


def read_polys_mm(dxf_path, layers=None):
    """Every LWPOLYLINE/HATCH in modelspace as shapely polygons in mm.

    layers: None for all layers, or a set of lower-cased layer names to keep.
    """
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    polys = []
    for e in msp:
        if layers is not None and e.dxf.layer.lower() not in layers:
            continue
        t = e.dxftype()
        if t == "LWPOLYLINE":
            pts = [(x * UM_TO_MM, y * UM_TO_MM) for x, y in e.get_points("xy")]
            if len(pts) >= 3:
                polys.append(Polygon(pts).buffer(0))
        elif t == "HATCH":
            for p in ezpath.from_hatch(e):
                pts = [(v.x * UM_TO_MM, v.y * UM_TO_MM) for v in p.flattening(0.05)]
                if len(pts) >= 3:
                    polys.append(Polygon(pts).buffer(0))
    return polys


def fill(g):
    """Drop interior holes: keep only each polygon's outer ring."""
    if g.geom_type == "Polygon":
        return Polygon(g.exterior)
    return unary_union([Polygon(x.exterior) for x in g.geoms])


def extract(dxf_path, layers=None, close_gap=0.08, simplify=0.03, verbose=True):
    """Outer silhouette of a design, centred on its own centroid.

    Returns (polygon_centred_on_origin, cx, cy). The caller must keep cx/cy: stage 03
    uses them to undo the centring so a piece lands exactly where it was nested.

    Note the two lossy steps -- both are deliberate but can bite:
      * buffer(+d).buffer(-d) bridges splits up to ~2*close_gap between adjacent
        shapes, which is how a ring of separate polylines becomes one closed body;
      * if the result is still a MultiPolygon only the LARGEST part is kept, so a
        design with genuinely disconnected pieces loses all but the biggest.
    """
    polys = read_polys_mm(dxf_path, layers)
    if not polys:
        raise ValueError(
            "no LWPOLYLINE/HATCH geometry found in %s%s -- wrong layer filter, or the "
            "geometry is in entity types this reader ignores"
            % (dxf_path, "" if layers is None else " on layers %s" % sorted(layers)))
    u = unary_union([p for p in polys if p.is_valid and not p.is_empty])
    u = u.buffer(close_gap).buffer(-close_gap)
    f = fill(u)
    if f.geom_type == "MultiPolygon":
        parts = sorted(f.geoms, key=lambda g: g.area, reverse=True)
        if verbose:
            print("    WARNING: silhouette is %d disjoint parts (%s mm2); keeping "
                  "the largest only" % (len(parts), ", ".join("%.2f" % p.area for p in parts[:5])))
        f = parts[0]
    f = f.simplify(simplify)
    cx, cy = f.centroid.x, f.centroid.y
    return translate(f, -cx, -cy), cx, cy


def pi_outline_mm(path, dxf_layer="polyimide", gds_layer=(1, 0)):
    """A design's POLYIMIDE OUTLINE (the flex itself, without the cutout band around it), in mm.

    DXF: the filled `dxf_layer` geometry. GDS (a capped dummy, 02_make_dummy.py): it has no
    outline layer -- its outline is the hole of the cutout ring on `gds_layer`.
    """
    if path.lower().endswith(".gds"):
        import gdstk
        cell = gdstk.read_gds(path).top_level()[0]
        polys = [Polygon(p.points * UM_TO_MM).buffer(0)
                 for p in cell.get_polygons(layer=gds_layer[0], datatype=gds_layer[1])]
        holes = [Polygon(i) for p in polys for i in getattr(p, "interiors", [])]
        if not holes:
            raise ValueError("%s: no closed cutout ring on %d/%d -- no outline to nest on" % ((path,) + tuple(gds_layer)))
        return max(holes, key=lambda h: h.area)
    polys = read_polys_mm(path, {dxf_layer})
    if not polys:
        raise ValueError("%s: nothing on layer %r -- no outline to nest on" % (path, dxf_layer))
    f = fill(unary_union(polys))
    if f.geom_type == "MultiPolygon":
        f = max(f.geoms, key=lambda g: g.area)
    return f


def cutout_mm(path, dxf_layer="polyimide_negative", gds_layer=(1, 0)):
    """Everything on a design's cutout (PI etch) layer, unioned, in mm: the band around the outline."""
    if path.lower().endswith(".gds"):
        import gdstk
        cell = gdstk.read_gds(path).top_level()[0]
        polys = [Polygon(p.points * UM_TO_MM).buffer(0)
                 for p in cell.get_polygons(layer=gds_layer[0], datatype=gds_layer[1])]
    else:
        polys = read_polys_mm(path, {dxf_layer})
    return unary_union([p for p in polys if p.is_valid and not p.is_empty])


def extract_outline(path, band_mm, simplify=0.005):
    """Footprint = polyimide outline grown by HALF the cutout band (mitred), centred on its own centroid.

    Two footprints that do not overlap keep the outlines ~band_mm apart, so a piece's cutout band may
    overlap its neighbour's band but not its neighbour's polyimide. Only ~: a mitred band reaches
    farther at a corner than band_mm, so the nester also tests the true shapes (silhouette of one vs
    outline of the other) -- hence the two extra returns.

    Returns (footprint, cx, cy, outline, silhouette), all centred by (cx, cy); silhouette = the outline
    and its cutout band, outer boundary only.
    """
    outline = pi_outline_mm(path)
    sil = fill(unary_union([outline, cutout_mm(path)]))
    if sil.geom_type == "MultiPolygon":
        sil = max(sil.geoms, key=lambda g: g.area)
    # simplify may cut a corner inward by up to `simplify`: grow by it first, so the footprint still covers
    f = outline.buffer(0.5 * band_mm + simplify, join_style=2).simplify(simplify)
    cx, cy = f.centroid.x, f.centroid.y
    return (translate(f, -cx, -cy), cx, cy, translate(outline, -cx, -cy), translate(sil, -cx, -cy))
