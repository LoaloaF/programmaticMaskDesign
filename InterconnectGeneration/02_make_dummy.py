"""Stage 02 for the dummy device: cap the interconnect's wire band and write a GDS.

Reads the stage-01 DXF (01_generate_12block.py --config dummy), maps its layers to GDS,
extends every Metal1 band wire up into Metal1 bars (bridged into one continuous bar, so all
wires are shorted), adds a Metal1 + etch circle above each bar and a polyimide ring on the etch
layer. Settings: config_dummy.py, section 2.

    python3 02_make_dummy.py [--in DXF] [--out GDS]
"""
import argparse

import ezdxf
import gdstk

import config_dummy as _cfg
from lib.active import in_designs

CONFIG = _cfg.DUMMY
LAYER_MAP = _cfg.DUMMY_LAYER_MAP
RECT_METAL_LAYER = _cfg.DUMMY_METAL_LAYER   # bars, circles, extensions (Metal1)
RECT_ETCH_LAYER = _cfg.DUMMY_ETCH_LAYER     # circle etch windows + polyimide ring
CELL_NAME, UNIT, PRECISION = "TOP", 1e-6, 1e-9

# The band carries the wires we short into the rectangle; drives band detection.
BAND_LAYER = "Metal1"

# The generator's device outline. Not in LAYER_MAP (so it never reaches the GDS as-is);
# read only to recover the stepped bottom edge -- see extract_seam_profile().
POLYIMIDE_LAYER = "Polyimide"



# ======================================================================================
# Helpers
# ======================================================================================
def _lwpolyline_points(entity):
    """Return the (x, y) vertices of an LWPOLYLINE."""
    return [(p[0], p[1]) for p in entity.get_points()]


def load_interconnect_polygons(doc):
    """Convert every mapped LWPOLYLINE in the DXF into a gdstk.Polygon on its GDS layer.

    Returns (polygons, band_polylines) where band_polylines is the list of vertex
    lists for the BAND_LAYER wires (used to locate the attachment band).
    """
    msp = doc.modelspace()
    polygons = []
    band_polylines = []

    for e in msp:
        if e.dxftype() != "LWPOLYLINE":
            continue
        layer_name = e.dxf.layer
        if layer_name not in LAYER_MAP:
            continue
        pts = _lwpolyline_points(e)
        if len(pts) < 3:
            continue
        gds_layer, gds_dtype = LAYER_MAP[layer_name]
        polygons.append(gdstk.Polygon(pts, layer=gds_layer, datatype=gds_dtype))
        if layer_name == BAND_LAYER:
            band_polylines.append(pts)

    return polygons, band_polylines


def extract_seam_profile(doc, tol=1e-6, min_run=100.0):
    """Read the stepped bottom edge off the source DXF's Polyimide outline.

    The generator draws that outline as one closed LWPOLYLINE whose bottom edge follows the
    pad rows: a column that DROPS its row-0 pad lets the boundary sit one pitch higher, so
    the edge is two long horizontal runs joined by a vertical step wall. The jigsaw bulbs
    are sampled arcs and carry no long horizontal run, so they fall out here for free.

    Returns (y_left, y_right, x_step), with x_step None for a flat bottom, or None when the
    DXF has no usable outline (the caller then falls back to a plain bbox bottom).
    """
    ents = [e for e in doc.modelspace()
            if e.dxftype() == "LWPOLYLINE" and e.dxf.layer == POLYIMIDE_LAYER]
    if not ents:
        return None
    pts = [(p[0], p[1]) for p in max(ents, key=lambda e: len(e.get_points())).get_points()]
    ys = [y for (_, y) in pts]
    y_mid = 0.5 * (min(ys) + max(ys))

    runs = []                                 # (length, y, x_lo, x_hi) per bottom-half plateau
    for i, (x0, y0) in enumerate(pts):
        x1, y1 = pts[(i + 1) % len(pts)]
        if abs(y1 - y0) <= tol and abs(x1 - x0) >= min_run and y0 < y_mid:
            runs.append((abs(x1 - x0), y0, min(x0, x1), max(x0, x1)))
    if not runs:
        return None

    runs.sort(reverse=True)
    if len(runs) == 1:                        # no dropped-row transition -> flat bottom
        return (runs[0][1], runs[0][1], None)

    left, right = sorted(runs[:2], key=lambda r: r[2])   # the two plateaus, in x order
    if abs(left[1] - right[1]) <= tol:        # same height -> one edge split by a bulb
        return (left[1], left[1], None)
    return (left[1], right[1], 0.5 * (left[3] + right[2]))


def detect_band(band_polylines):
    """Locate the wire band: the y of the tips (max y) and the x-extent of the wires."""
    if not band_polylines:
        raise RuntimeError(f"no '{BAND_LAYER}' wires found; cannot locate the band")
    band_y = max(y for poly in band_polylines for (_, y) in poly)
    x_lo = min(x for poly in band_polylines for (x, _) in poly)
    x_hi = max(x for poly in band_polylines for (x, _) in poly)
    return band_y, x_lo, x_hi


def build_band_extensions(band_polylines, band_y):
    """Extend each Metal1 band wire UP by band_overlap um so it reaches the Metal1 bars, which
    sit pad_rect_y above the band; the bars are never moved down over the pads. Extensions land
    on the wire's GDS layer (Metal1 -> 3).
    """
    overlap = CONFIG["band_overlap"]
    gds_layer, gds_dtype = LAYER_MAP[BAND_LAYER]
    exts = []
    for pts in band_polylines:
        wy = max(y for (_, y) in pts)                      # this wire's tip row
        top_xs = [x for (x, y) in pts if wy - y <= 1e-6]   # vertices on that tip edge
        if len(top_xs) < 2:
            continue                                       # tapered/degenerate tip -> already abuts
        xl, xr = min(top_xs), max(top_xs)
        exts.append(gdstk.rectangle((xl, wy), (xr, wy + overlap),
                                    layer=gds_layer, datatype=gds_dtype))
    return exts


def wire_tips(band_polylines):
    """Per-wire tip geometry at the band edge: list of (x_center, x_left, x_right),
    sorted left to right."""
    tips = []
    for pts in band_polylines:
        wy = max(y for (_, y) in pts)
        txs = [x for (x, y) in pts if wy - y <= 1e-6]
        if len(txs) >= 2:
            xl, xr = min(txs), max(txs)
        else:
            xs = [x for (x, _) in pts]
            xl = xr = sum(xs) / len(xs)
        tips.append((0.5 * (xl + xr), xl, xr))
    tips.sort()
    return tips


def build_dummy_targets(band_y, x_lo, x_hi, band_polylines):
    """Build the dummy targets: N Metal1 bars, each covering exactly 1/N of the wires,
    then N circles above them drawn on BOTH Metal1 and the etch layer (GDS 1).

    Bars are sized to their wire GROUP (not the geometric x-span), so every wire lands
    under a bar and the inter-bar gaps fall between wire groups. The gap is set by
    pad_gap but clamped at each boundary so it never uncovers a wire centre.
    """
    metal_l, metal_d = RECT_METAL_LAYER
    etch_l, etch_d = RECT_ETCH_LAYER

    n = CONFIG["n_pads"]
    gap = CONFIG["pad_gap"]
    rh = CONFIG["pad_rect_height"]
    ry = band_y + CONFIG["pad_rect_y"]

    tips = wire_tips(band_polylines)
    m = len(tips)
    bounds = [round(k * m / n) for k in range(n + 1)]  # split indices -> ~equal groups

    # Each bar spans its group's full tip extent (covers all its wires).
    bars = []
    for i in range(n):
        a, b = bounds[i], bounds[i + 1]
        grp = tips[a:b]
        bars.append([min(t[1] for t in grp), max(t[2] for t in grp)])  # [left, right]

    # Open a gap at each internal boundary, clamped so both boundary wires stay FULLY
    # covered: the limit is the facing EDGES of those wires (not their centres, which
    # would cut each bar back by half a trace width), so the widest possible gap is the
    # bare metal spacing between the two groups.
    for i in range(1, n):
        a = bounds[i]
        xL, xR = tips[a - 1][2], tips[a][1]          # facing edges of the two boundary wires
        mid = 0.5 * (xL + xR)
        half = max(0.0, min(0.5 * gap, 0.5 * (xR - xL)))
        bars[i - 1][1] = min(bars[i - 1][1], mid - half)
        bars[i][0] = max(bars[i][0], mid + half)

    polys = []
    for left, right in bars:
        polys.append(gdstk.rectangle((left, ry - 0.5 * rh), (right, ry + 0.5 * rh),
                                     layer=metal_l, datatype=metal_d))
    if CONFIG["bridge_bars"]:
        # Fill each gap between neighbouring bars, so the bars form one continuous bar and all
        # wires are shorted together (as fabricated).
        for (_, r0), (l1, _) in zip(bars, bars[1:]):
            if l1 > r0:
                polys.append(gdstk.rectangle((r0, ry - 0.5 * rh), (l1, ry + 0.5 * rh),
                                             layer=metal_l, datatype=metal_d))

    # N circles above, centred on each bar, on Metal1 AND the etch layer.
    cr_m = CONFIG["circle_radius_metal"]
    cr_e = CONFIG["circle_radius_etching"]
    cy = band_y + CONFIG["circle_y"]
    centers = [0.5 * (left + right) for left, right in bars]
    for cx in centers:
        # tolerance keeps each circle a single smooth polygon (avoids GDS vertex-limit split)
        polys.append(gdstk.ellipse((cx, cy), cr_m, tolerance=1.0, layer=metal_l, datatype=metal_d))
        polys.append(gdstk.ellipse((cx, cy), cr_e, tolerance=1.0, layer=etch_l, datatype=etch_d))

    # Connect each circle down to its bar. Two modes:
    #   circle_trace_width = a number -> ONE wide Metal1 trace per circle (bar -> circle),
    #                                    self-redundant, no single-point-of-failure.
    #   circle_trace_width = None     -> the full fan: every wire under the circle extends up.
    ov = CONFIG["circle_wire_overlap"]
    tw = CONFIG["circle_trace_width"]
    bar_top = ry + 0.5 * rh
    for cx in centers:
        if tw is not None:
            # single wide trace from the bar up into the circle
            y_top = cy - cr_m + ov                            # enters the circle by `ov`
            polys.append(gdstk.rectangle((cx - 0.5 * tw, ry), (cx + 0.5 * tw, y_top),
                                         layer=metal_l, datatype=metal_d))
        else:
            for xc, xl, xr in tips:
                dx = xc - cx
                if abs(dx) >= cr_m:
                    continue                                # not under this circle
                y_enter = cy - (cr_m * cr_m - dx * dx) ** 0.5   # where a vertical wire meets the circle
                y_top = min(cy, y_enter + ov)               # poke a little way in
                polys.append(gdstk.rectangle((xl, band_y), (xr, y_top),
                                             layer=metal_l, datatype=metal_d))

    bbox = (min(x_lo, min(centers) - cr_m), ry - 0.5 * rh,
            max(x_hi, max(centers) + cr_m), cy + cr_m)
    return polys, bbox


def build_polyimide_outline(dev_bbox, seam=None):
    """Build the polyimide outline as a hollow band ring on the etch layer (GDS 1).

    Left/right/top are the device bounding box grown by `polyimide_margin`. The BOTTOM
    follows the pad rows when `seam` is given (from extract_seam_profile): two heights
    joined by a vertical step, exactly as the generator's own outline does -- minus the
    jigsaw bulbs. Without a seam it is a plain flat rectangle.

    The profile is then stroked OUTWARD into a `polyimide_width`-wide frame, so the outline
    is the polyimide edge and the band is the etch region around it -- the same sense as
    Polyimide_Negative in the generator (buffer(bw).difference(outline)). Stroking inward
    would put the band on top of the pads, since `polyimide_margin` (the clearance to the
    geometry) is not larger than the band width.

    Returns a list of gdstk.Polygons on the etch layer.
    """
    dx_lo, dy_lo, dx_hi, dy_hi = dev_bbox
    m = CONFIG["polyimide_margin"]
    w = CONFIG["polyimide_width"]

    ox_lo, ox_hi, oy_hi = dx_lo - m, dx_hi + m, dy_hi + m

    if seam is None:
        pts = [(ox_lo, dy_lo - m), (ox_hi, dy_lo - m), (ox_hi, oy_hi), (ox_lo, oy_hi)]
    else:
        y_left, y_right, x_step = seam
        if x_step is None:
            pts = [(ox_lo, y_left), (ox_hi, y_left), (ox_hi, oy_hi), (ox_lo, oy_hi)]
        else:
            pts = [(ox_lo, y_left), (x_step, y_left), (x_step, y_right),
                   (ox_hi, y_right), (ox_hi, oy_hi), (ox_lo, oy_hi)]

    # Offset the whole profile rather than growing a rectangle, so the step survives.
    etch_l, etch_d = RECT_ETCH_LAYER
    outer = gdstk.Polygon(pts)
    grown = gdstk.offset(outer, w, join="miter", use_union=True)
    ring = gdstk.boolean(grown, outer, "not", layer=etch_l, datatype=etch_d)
    return ring


# ======================================================================================
# Main
# ======================================================================================
def main():
    p = argparse.ArgumentParser(description="Cap the dummy interconnect's wire band -> GDS.")
    p.add_argument("--in", dest="in_dxf", default=_cfg.DEFAULT_IN,
                   help="stage-01 dummy DXF; bare name -> designs/ (default: %(default)s)")
    p.add_argument("--out", dest="out_gds", default=_cfg.DEFAULT_OUT,
                   help="output GDS; bare name -> designs/ (default: %(default)s)")
    a = p.parse_args()
    in_path = in_designs(a.in_dxf)
    out_gds = in_designs(a.out_gds)

    doc = ezdxf.readfile(in_path)
    polygons, band_polylines = load_interconnect_polygons(doc)
    seam = extract_seam_profile(doc) if CONFIG["polyimide_follow_pads"] else None
    band_y, x_lo, x_hi = detect_band(band_polylines)
    extensions = build_band_extensions(band_polylines, band_y)
    targets, (tx_lo, ty_lo, tx_hi, ty_hi) = build_dummy_targets(band_y, x_lo, x_hi, band_polylines)

    outline = []
    if CONFIG["polyimide_outline"]:
        # device bounding box over all emitted geometry (interconnect + extensions + targets)
        dev = polygons + extensions + targets
        boxes = [p.bounding_box() for p in dev]
        dx_lo = min(b[0][0] for b in boxes); dy_lo = min(b[0][1] for b in boxes)
        dx_hi = max(b[1][0] for b in boxes); dy_hi = max(b[1][1] for b in boxes)
        outline = build_polyimide_outline((dx_lo, dy_lo, dx_hi, dy_hi), seam)
        # The band sits OUTSIDE the outline, so it must not touch a pad/wire/target. Checking
        # containment in the outline is not enough -- that passes even while the band eats them.
        clash = gdstk.boolean(dev, outline, "and")
        if clash:
            print(f"WARNING: polyimide band overlaps {len(clash)} device polygons "
                  f"({sum(abs(c.area()) for c in clash) / 1e6:.4f} mm2) -- "
                  f"check polyimide_margin ({CONFIG['polyimide_margin']}) / "
                  f"polyimide_width ({CONFIG['polyimide_width']})")

    lib = gdstk.Library(unit=UNIT, precision=PRECISION)
    cell = lib.new_cell(CELL_NAME)
    for p in polygons:
        cell.add(p)
    for e in extensions:
        cell.add(e)
    for t in targets:
        cell.add(t)
    for o in outline:
        cell.add(o)
    lib.write_gds(out_gds)

    n = CONFIG["n_pads"]
    print(f"interconnect wires (band '{BAND_LAYER}'): {len(band_polylines)}  extended: {len(extensions)}")
    print(f"polyimide outline ring polys (L{RECT_ETCH_LAYER[0]}): {len(outline)}")
    if seam is None:
        print("polyimide bottom: flat (no Polyimide outline in the DXF, or follow_pads off)")
    else:
        y_left, y_right, x_step = seam
        step = "flat" if x_step is None else f"step at x={x_step:.0f}"
        print(f"polyimide bottom: follows the pad rows -- y_left={y_left:.0f} "
              f"y_right={y_right:.0f}, {step} (bulbs dropped)")
    print(f"band y = {band_y:.2f}  x-extent = [{x_lo:.2f}, {x_hi:.2f}]  ({x_hi - x_lo:.1f} um)")
    print(f"targets: {n} Metal1 bars @ y={band_y + CONFIG['pad_rect_y']:.0f} + "
          f"{n} circles (Metal1+Etch) @ y={band_y + CONFIG['circle_y']:.0f}  "
          f"x [{tx_lo:.0f}, {tx_hi:.0f}]")
    print(f"wrote {out_gds}")


if __name__ == "__main__":
    main()
