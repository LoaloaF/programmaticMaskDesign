"""Small geometry and DXF helpers shared by every stage of the router.
"""
import math
import numpy as np
from shapely.geometry import Polygon

from lib import active

active.require()
from lib.active import *  # noqa: F401,F403  -- this design's knobs (see lib/active.py)


def entity_xy_points(e):
    if e.dxftype() == "LWPOLYLINE":
        return [(float(p[0]), float(p[1])) for p in e.get_points()]
    if e.dxftype() == "POLYLINE":
        return [(float(v.dxf.location.x), float(v.dxf.location.y)) for v in e.vertices]
    return []


def create_polygon_circle(center_x, center_y, radius, resolution=16):
    resolution = max(3, resolution)
    angles = np.linspace(0, 2 * np.pi, resolution, endpoint=False)
    x = center_x + radius * np.cos(angles)
    y = center_y + radius * np.sin(angles)
    points = np.column_stack((x, y))
    return np.vstack((points, points[0]))


def _arc_pts(ox, oy, rad, a0, a1, resolution, longway=False, chord_r=None):
    """Points along the arc from angle a0 to a1 about (ox, oy) -- the SHORTER one, or the other
    way round with `longway`. n scales with the sweep so arc density matches a full circle of
    `resolution` segments. Pass `chord_r` to size the steps off a DIFFERENT radius instead, so
    that arcs of unequal radius in one outline come out at the same chord length (the teardrop's
    fillets are wider than its pad, and would otherwise be sampled coarser)."""
    d = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi     # shortest signed sweep in (-pi, pi]
    if longway:
        d -= math.copysign(2 * math.pi, d)
    scale = 1.0 if not chord_r else rad / float(chord_r)
    # At least 3 points, i.e. 2 segments: a tightly shrunk teardrop flank spans only a few
    # degrees at this density, and a flank rendered as one straight chord is not an arc.
    n = max(3, int(round(resolution * scale * abs(d) / (2 * math.pi))))
    a = np.linspace(a0, a0 + d, n)
    return np.column_stack((ox + rad * np.cos(a), oy + rad * np.sin(a)))


def filleted_bulb(cx, cy, r, fillet_r, side="left", resolution=16):
    """One protruding extraction-tab bulb whose two roots blend into the vertical neck wall
    (x = cx) via a concave fillet of radius `fillet_r`, instead of the plain circle's sharp
    90 deg root. side='left' protrudes -x, 'right' protrudes +x. fillet_r <= 0 falls back to the
    plain circle. Returns a shapely Polygon (um); its inner (wall) edge is absorbed on union
    with the neck, like a plain circle's inner half."""
    f = float(fillet_r)
    if f <= 0.0:
        return Polygon(create_polygon_circle(cx, cy, r, resolution))
    s = -1.0 if side == "left" else 1.0                  # protrusion direction in x
    dy = math.sqrt(r * r + 2.0 * r * f)                  # wall-tangent / fillet-center y offset (> r)
    k = r / (r + f)                                       # circle tangent-point interpolation
    # fillet centers (externally tangent to the bulb circle, tangent to the wall) and tangents
    Ot, Ob = (cx + s * f, cy + dy), (cx + s * f, cy - dy)
    Wt, Wb = (cx, cy + dy), (cx, cy - dy)                 # wall tangent points
    Ct = (cx + s * f * k, cy + dy * k)                    # circle tangent point, top
    Cb = (cx + s * f * k, cy - dy * k)                    # circle tangent point, bottom
    ang = lambda p, o: math.atan2(p[1] - o[1], p[0] - o[0])
    pts = np.vstack([
        _arc_pts(Ot[0], Ot[1], f, ang(Wt, Ot), ang(Ct, Ot), resolution),   # top fillet: wall -> circle
        _arc_pts(cx, cy, r, ang(Ct, (cx, cy)), ang(Cb, (cx, cy)), resolution),  # main bulb arc (outer)
        _arc_pts(Ob[0], Ob[1], f, ang(Cb, Ob), ang(Wb, Ob), resolution),   # bottom fillet: circle -> wall
    ])
    return Polygon(pts)


def stroke_centerline_to_polygon(centerline, widths):
    pts = np.asarray(centerline, dtype=float)
    w = np.asarray(widths, dtype=float)
    keep = [0]
    for i in range(1, len(pts)):
        if not np.allclose(pts[i], pts[keep[-1]]):
            keep.append(i)
    pts, w = pts[keep], w[keep]
    n = len(pts)
    if n < 2:
        return None
    seg = pts[1:] - pts[:-1]
    d = seg / np.hypot(seg[:, 0], seg[:, 1])[:, None]
    seg_n = np.stack([-d[:, 1], d[:, 0]], axis=1)
    left = np.empty((n, 2))
    right = np.empty((n, 2))
    for i in range(n):
        h = w[i] / 2.0
        if i == 0:
            off = seg_n[0] * h
        elif i == n - 1:
            off = seg_n[-1] * h
        else:
            m = seg_n[i - 1] + seg_n[i]
            ml = np.hypot(m[0], m[1])
            if ml < 1e-9:
                off = seg_n[i] * h
            else:
                m = m / ml
                cos = max(float(np.dot(m, seg_n[i])), 0.25)
                off = m * (h / cos)
        left[i] = pts[i] + off
        right[i] = pts[i] - off
    ring = np.vstack([left, right[::-1]])
    return [(float(x), float(y)) for x, y in ring]


def chamfer_polyline(pts, target=CHAMFER_MAX_MM, frac=CHAMFER_FRAC, tol=1e-12, keep_tail=1):
    """Bevel every interior corner: at each vertex set back an EQUAL distance d along both
    legs and join the two set-back points with a straight diagonal. On a 90 deg corner that
    diagonal is exactly 45 deg. Endpoints are never moved. d = min(target, frac*len_in,
    frac*len_out) so it auto-shrinks in tight spots; frac<=0.5 keeps two chamfers sharing a
    segment from crossing (self-intersection-free).

    keep_tail leaves the last N interior corners square; the caller (repair_angled_crossings
    in lib/teardrop.py) bevels the final pad-approach corner separately."""
    pts = [(float(x), float(y)) for x, y in pts]
    if len(pts) < 3:
        return pts
    last_i = len(pts) - 2 - keep_tail    # last interior vertex we still chamfer
    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        if i > last_i:                   # leave the pad-approach corner(s) square
            out.append(tuple(pts[i])); continue
        p0, p1, p2 = np.array(pts[i - 1]), np.array(pts[i]), np.array(pts[i + 1])
        v_in = p1 - p0
        v_out = p2 - p1
        l_in = float(np.hypot(v_in[0], v_in[1]))
        l_out = float(np.hypot(v_out[0], v_out[1]))
        if l_in < tol or l_out < tol:
            out.append(tuple(p1)); continue
        cross = v_in[0] * v_out[1] - v_in[1] * v_out[0]
        if abs(cross) < tol * l_in * l_out:      # collinear -> no corner to cut
            out.append(tuple(p1)); continue
        d = min(target, frac * l_in, frac * l_out)
        if d <= tol:                             # target 0 -> leave the corner sharp
            out.append(tuple(p1)); continue
        a = p1 - v_in / l_in * d
        b = p1 + v_out / l_out * d
        out.append((float(a[0]), float(a[1])))
        out.append((float(b[0]), float(b[1])))
    out.append(pts[-1])
    return out


def to_seglist(routes_all):
    out = []
    for i, (_, poly) in enumerate(routes_all.items()):
        for a, b in zip(poly, poly[1:]):
            out.append((i, a, b))
    return out


def ensure_layer(doc, name, color):
    if name not in doc.layers:
        doc.layers.new(name, dxfattribs={"color": color})
