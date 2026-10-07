"""Geometry helpers shared by the two stage-01 generators (01_generate_*.py).

Not the same as lib/geometry.py: stroke_centerline_to_polygon here is the generators' own
version, and these need no design config.
"""
import numpy as np


def create_rectangle(x0,y0,w,l):
    x = [x0, x0+w, x0+w, x0]
    y = [y0, y0, y0+l, y0+l]

    return (x,y)


def convert_rectangle_to_polyline(rectangle):
    """
    Turn a (x, y) rectangle from create_rectangle into a closed Nx2 point array
    ready for msp.add_lwpolyline(..., close=True). Ported from
    Electrodes_Baran/hook_bundle_generator.py.
    """
    points = np.asarray(tuple(zip(rectangle[0], rectangle[1])))
    first_point = np.expand_dims(points[0], 0)
    points = np.concatenate((points, first_point))
    return points


def point_reflect(points, px, py):
    """
    180-degree rotation (point reflection) of an Nx2 point array about (px, py):
    (x, y) -> (2*px - x, 2*py - y). Used to emit a flipped mating copy of the
    whole design so its routing fans DOWNWARD and interlocks from below.
    """
    pts = np.asarray(points, dtype=float)
    out = np.empty_like(pts)
    out[:, 0] = 2 * px - pts[:, 0]
    out[:, 1] = 2 * py - pts[:, 1]
    return out


def bulb_profile(cx, seam, r, n_arc):
    """
    Ordered edge points for a BULB-ONLY (no neck) mating feature: a lower SEMICIRCLE of radius r
    centred ON the seam line at x=cx, dipping DOWN (-y), traversed LEFT->RIGHT so it splices straight
    into a left->right edge: (cx - r, seam) -> through (cx, seam - r) -> (cx + r, seam). Because there
    is no neck the widest point (the diameter, 2*r) sits AT the seam -> the feature has NO UNDERCUT, so
    the two pieces mate by straight IN-PLANE insertion (slid together while flat). On a piece whose
    material sits ABOVE the seam this reads as a MALE half-disc; grown by the puzzle gap and/or flipped
    in y it is the FEMALE socket. Units: um.
    """
    thetas = np.linspace(np.pi, 2 * np.pi, n_arc + 1)   # pi -> 2pi: left of seam, through the bottom, to right
    return [(cx + r * np.cos(t), seam + r * np.sin(t)) for t in thetas]


def circle_polyline(cx, cy, r, n):
    """
    Closed Nx2 point array approximating a circle of radius r centred at (cx, cy), sampled with n
    segments. Returned in the same closed form as convert_rectangle_to_polyline so a circular via
    flows through the identical add_lwpolyline / point_reflect pipeline as the rectangular one (a
    point-reflected circle is still a circle). Units: um.
    """
    thetas = np.linspace(0.0, 2 * np.pi, n + 1)         # first point repeated at the end -> closed
    return np.array([(cx + r * np.cos(t), cy + r * np.sin(t)) for t in thetas])


def stroke_centerline_to_polygon(centerline, widths):
    """
    Turn a centerline (list of (x, y)) with a per-vertex full trace width into a
    CLOSED polygon outline (list of (x, y)) using miter joins. Ported from
    Electrodes_Baran/hook_bundle_generator.py so each wire is emitted as a filled
    polygon of the given width (with a clean miter at the 45-degree bend). Returns None for
    degenerate input.
    """
    pts = np.asarray(centerline, dtype=float)
    w = np.asarray(widths, dtype=float)
    # Drop consecutive duplicate vertices (a zero-length diagonal collapses cleanly).
    keep = [0]
    for i in range(1, len(pts)):
        if not np.allclose(pts[i], pts[keep[-1]]):
            keep.append(i)
    pts, w = pts[keep], w[keep]
    n = len(pts)
    if n < 2:
        return None
    seg = pts[1:] - pts[:-1]
    d = seg / np.hypot(seg[:, 0], seg[:, 1])[:, None]     # unit segment directions
    seg_n = np.stack([-d[:, 1], d[:, 0]], axis=1)         # left normals per segment
    left = np.empty((n, 2))
    right = np.empty((n, 2))
    for i in range(n):
        h = w[i] / 2.0
        if i == 0:
            off = seg_n[0] * h
        elif i == n - 1:
            off = seg_n[-1] * h
        else:
            m = seg_n[i - 1] + seg_n[i]                   # miter (bisector) direction
            ml = np.hypot(m[0], m[1])
            if ml < 1e-9:                                  # ~180 deg reversal: fall back
                off = seg_n[i] * h
            else:
                m = m / ml
                cos = max(float(np.dot(m, seg_n[i])), 0.25)   # clamp sharp miters
                off = m * (h / cos)
        left[i] = pts[i] + off
        right[i] = pts[i] - off
    ring = np.vstack([left, right[::-1]])                 # forward left, backward right
    return [(float(x), float(y)) for x, y in ring]
