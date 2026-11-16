"""Geometry helpers shared by the stage-01 generator (01_generate.py).

Not the same as lib/geometry.py: stroke_centerline_to_polygon here is the generators' own
version, and these need no design config.
"""
import math

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


def capsule_polyline(cx, cy, w, h, n):
    """Closed Nx2 point array of a VERTICAL stadium (capsule) w wide and h tall, centred at (cx, cy):
    two half circles of radius w/2 joined by straight sides (h <= w gives a plain circle). Units: um."""
    r, d = w / 2.0, max(h - w, 0.0) / 2.0
    half = max(n // 2, 2)
    top = [(cx + r * np.cos(t), cy + d + r * np.sin(t)) for t in np.linspace(0.0, np.pi, half + 1)]
    bot = [(cx + r * np.cos(t), cy - d + r * np.sin(t)) for t in np.linspace(np.pi, 2 * np.pi, half + 1)]
    pts = top + bot
    return np.array(pts + [pts[0]])


def etch_via_layout(side, inset, r, gap, via_rel, spacing_range, extra_vias=()):
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

    extra_vias: more (x, y, radius) circles, relative to the same corner, that every etch via must also
    keep `gap` from -- e.g. the two end circles of a capsule-shaped top via (wafer6). None by default.

    Returns (centres, info) with info = dict(a, k, step, per_side, n).
    """
    pitch = 2.0 * r + gap                      # centre distance that leaves `gap` between edges
    run = side - 2.0 * inset                   # corner point to corner point
    a = pitch / np.sqrt(2.0)                   # the two neighbours of one corner
    # The routing via sits near one corner point C. A neighbour at C + t*u (u along a side, into
    # the pad) keeps `pitch` from it for t >= the larger root of |C - V + t*u| = pitch.
    corners = [(inset, inset), (side - inset, inset), (side - inset, side - inset), (inset, side - inset)]
    for vx, vy, need in [(via_rel[0], via_rel[1], pitch)] + [(x, y, r + vr + gap) for x, y, vr in extra_vias]:
        cx, cy = min(corners, key=lambda c: np.hypot(c[0] - vx, c[1] - vy))
        w = np.array([cx - vx, cy - vy])
        for u in (np.array([1.0 if cx == inset else -1.0, 0.0]), np.array([0.0, 1.0 if cy == inset else -1.0])):
            b = float(w @ u)
            disc = b * b - float(w @ w) + need * need
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


# ---- Metal1 landing pad + teardrop at the routing via (Rev4) ---------------------------------
# A standalone copy of the tangent-fillet teardrop in lib/teardrop.py (teardrop_ring): the stage-01
# generators cannot import lib/ (it needs a selected router config), and the construction is pure
# geometry. Both give the same outline -- checked numerically when this was written.

def _arc(ox, oy, rad, a0, a1, res, longway=False, chord_r=None):
    d = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi          # shortest signed sweep
    if longway:
        d -= math.copysign(2 * math.pi, d)
    scale = 1.0 if not chord_r else rad / float(chord_r)
    n = max(3, int(round(res * scale * abs(d) / (2 * math.pi))))
    a = np.linspace(a0, a0 + d, n)
    return np.column_stack((ox + rad * np.cos(a), oy + rad * np.sin(a)))


def tear_tip_dist(r, fillet_r, width):
    """Pad centre -> tear tip (where the fillets meet the trace edges); None if no tear fits."""
    hw = max(float(width), 0.0) / 2.0
    if fillet_r is None or fillet_r <= 0.0 or hw >= r:
        return None
    d2 = (r + fillet_r) ** 2 - (hw + fillet_r) ** 2
    if d2 <= 0.0:
        return None
    L = math.sqrt(d2)
    return L if L > r else None


def tear_fillet_for_tip(r, width, tip_dist):
    """Inverse of tear_tip_dist: the fillet radius that puts the tip `tip_dist` from the centre."""
    hw = max(float(width), 0.0) / 2.0
    return (tip_dist ** 2 - r * r + hw * hw) / (2.0 * (r - hw))


def tear_ring(px, py, r, ux, uy, fillet_r, width, res=48):
    """Pad circle (px, py, r) plus two concave fillet arcs of radius `fillet_r`, each tangent to the
    circle and to one edge of the `width`-wide trace leaving along (ux, uy). Closed ring, tip edge
    implied (the trace covers it); the plain circle when no tear fits."""
    n = float(np.hypot(ux, uy))
    L = tear_tip_dist(r, fillet_r, width)
    if L is None or n == 0.0:
        t = np.linspace(0.0, 2 * np.pi, res, endpoint=False)
        pts = np.column_stack((px + r * np.cos(t), py + r * np.sin(t)))
        return np.vstack((pts, pts[0]))
    ux, uy = ux / n, uy / n
    nx, ny = -uy, ux
    f, hw = float(fillet_r), width / 2.0
    k = r / (r + f)
    bx, by = px + ux * L, py + uy * L
    ang = lambda p, o: math.atan2(p[1] - o[1], p[0] - o[0])

    def flank(s):
        O = (bx + nx * s * (hw + f), by + ny * s * (hw + f))
        return (O, (px + (O[0] - px) * k, py + (O[1] - py) * k), (bx + nx * s * hw, by + ny * s * hw))

    Ol, Cl, Wl = flank(+1)
    Or_, Cr, Wr = flank(-1)
    pts = np.vstack([
        _arc(Ol[0], Ol[1], f, ang(Wl, Ol), ang(Cl, Ol), res, chord_r=r),
        _arc(px, py, r, ang(Cl, (px, py)), ang(Cr, (px, py)), res, longway=True)[1:],
        _arc(Or_[0], Or_[1], f, ang(Cr, Or_), ang(Wr, Or_), res, chord_r=r)[1:],
    ])
    return np.vstack((pts, pts[0]))


def wire_with_end_pad(ring, centerline, wire_width, pad_r, fillet_r, res=48, tip_margin=1.0):
    """Merge a landing pad of radius `pad_r` and a teardrop into a wire polygon `ring`, at the
    wire's LAST centerline point (where the routing via is). One polygon comes back, so a wire stays
    one Metal1 entity (stage 02 finds each wire's band end from its own polygon).

    The tear leaves along the last segment, so its tip is capped to `tip_margin` short of that
    segment's start -- a longer tear would run past the 45-degree bend. Returns (points, kind) with
    kind "tear" or "circle" (the cap left no room for a tear)."""
    from shapely.geometry import Polygon
    c = np.asarray(centerline, dtype=float)
    px, py = c[-1]
    keep = [0]
    for i in range(1, len(c)):                    # same duplicate-point drop as the stroker
        if not np.allclose(c[i], c[keep[-1]]):
            keep.append(i)
    c = c[keep]
    seg = c[-2] - c[-1]
    run = float(np.hypot(*seg))
    ux, uy = seg / run
    f = fillet_r
    L_max = run - tip_margin
    if f is not None and L_max > pad_r:
        f = min(f, tear_fillet_for_tip(pad_r, wire_width, L_max))
    kind = "tear" if tear_tip_dist(pad_r, f, wire_width) is not None else "circle"
    tear = tear_ring(px, py, pad_r, ux, uy, f, wire_width, res)
    merged = Polygon(ring).buffer(0).union(Polygon(tear).buffer(0))
    if merged.geom_type != "Polygon" or merged.interiors:
        raise ValueError("wire + end pad did not merge into one simple polygon")
    return np.asarray(merged.exterior.coords, dtype=float), kind
