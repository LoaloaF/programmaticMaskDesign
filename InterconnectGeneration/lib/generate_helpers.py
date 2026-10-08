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


def etch_via_layout(side, inset, r, gap, via_rel, spacing_range):
    """
    Rev3 PI-etch via centres for one square pad, relative to its lower-left corner. Units: um.

    No via sits in a corner. Each corner instead gets two NEIGHBOURS, one on each adjacent side,
    `a` along the side from the corner point; then each side gets k evenly spaced vias between
    its two corner neighbours. All centres sit `inset` in from the pad edge, so the corner points
    are (inset, inset) etc. and the four sides are identical.

    a is the smallest offset that keeps `gap` (edge to edge) between the two neighbours of a
    corner and between every neighbour and the routing via at `via_rel`; it is the same at all
    four corners. k is the count whose spacing (side run between the neighbours) / (k + 1) lies
    in spacing_range = (lo, hi) and is nearest its middle; k = 0 is allowed.

    Returns (centres, info) with info = dict(a, k, step, per_side, n).
    """
    pitch = 2.0 * r + gap                      # centre distance that leaves `gap` between edges
    run = side - 2.0 * inset                   # corner point to corner point
    a = pitch / np.sqrt(2.0)                   # the two neighbours of one corner
    # The routing via sits near one corner point C. A neighbour at C + t*u (u along a side, into
    # the pad) keeps `pitch` from it for t >= the larger root of |C - V + t*u| = pitch.
    vx, vy = via_rel
    corners = [(inset, inset), (side - inset, inset), (side - inset, side - inset), (inset, side - inset)]
    cx, cy = min(corners, key=lambda c: np.hypot(c[0] - vx, c[1] - vy))
    w = np.array([cx - vx, cy - vy])
    for u in (np.array([1.0 if cx == inset else -1.0, 0.0]), np.array([0.0, 1.0 if cy == inset else -1.0])):
        b = float(w @ u)
        disc = b * b - float(w @ w) + pitch * pitch
        if disc > 0:
            a = max(a, -b + np.sqrt(disc))
    a = np.ceil(a * 1000.0) / 1000.0           # up to the 1 nm grid
    inner = run - 2.0 * a                      # between the two corner neighbours of a side
    if inner < pitch:
        raise ValueError(f"a {side} um pad has no room for corner neighbours {a:.3f} um from each "
                         f"corner with a {gap} um gap; reduce etch_via_corner_gap")
    lo, hi = spacing_range
    fits = [(abs(inner / (k + 1) - 0.5 * (lo + hi)), k) for k in range(0, int(inner // lo) + 2)
            if lo <= inner / (k + 1) <= hi]
    if not fits:
        raise ValueError(f"no via count puts the spacing between the corner neighbours of a {side} um "
                         f"pad ({inner:.3f} um) inside {spacing_range} um")
    k = min(fits)[1]
    step = inner / (k + 1)
    ts = [a + j * step for j in range(k + 2)]  # along a side, from one corner point
    lo_, hi_ = inset, side - inset
    centres = ([(lo_ + t, lo_) for t in ts] + [(lo_ + t, hi_) for t in ts] +       # bottom, top
               [(lo_, lo_ + t) for t in ts] + [(hi_, lo_ + t) for t in ts])        # left, right
    return centres, dict(a=a, k=k, step=step, per_side=k + 2, n=len(centres))


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
