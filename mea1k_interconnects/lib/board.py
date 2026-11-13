"""Phase 3: the polyimide substrate -- Simon's board outline and its placement,
the neck down to it (optionally flaring out, 12-block), the wall notches and the
extraction-tab bulbs.
"""
import math
import numpy as np
import ezdxf

from lib import active

active.require()
from lib.active import *  # noqa: F401,F403  -- this design's knobs (see lib/active.py)

from lib.geometry import filleted_bulb


def _bottom_block_centroid_y_mm(sig):
    """Mean y of all pads in the bottom-most row band (the last 4 rows = one block)."""
    sig = np.asarray(sig)
    ys = np.sort(np.unique(np.round(sig[:, 1], 2)))
    band_lo, band_hi = ys[0] - 0.01, ys[3] + 0.01
    bottom_block = sig[(sig[:, 1] >= band_lo) & (sig[:, 1] <= band_hi)]
    return float(bottom_block[:, 1].mean())


def board_outline_dims(path=BOARD_OUTLINE_DXF):
    """(width_mm, height_mm) of Simon's board outline bbox (lines + spline ctrl points)."""
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    bx, by = [], []
    for e in msp:
        if e.dxftype() == "LINE":
            bx += [e.dxf.start.x, e.dxf.end.x]; by += [e.dxf.start.y, e.dxf.end.y]
        elif e.dxftype() == "SPLINE":
            for p in e.control_points:
                bx.append(p[0]); by.append(p[1])
    if not bx:
        raise RuntimeError(f"no outline boundary (lines/splines) in {path}")
    return max(bx) - min(bx), max(by) - min(by)


def board_placement(sig, path=BOARD_OUTLINE_DXF):
    """Board centre y (mm), from the bottom-gap constraint. The board's x is not set here: the
    callers centre it on the connector bundle."""
    sig = np.asarray(sig)
    _bw, bh = board_outline_dims(path)
    bot_y = _bottom_block_centroid_y_mm(sig) - BOARD_BOTTOM_GAP_MM
    board_cy = bot_y + bh / 2.0
    return board_cy


def load_board_corner_features(sig, path=BOARD_OUTLINE_DXF):
    """Return (corners, r_outer, r_inner) — the 4 corner circle centers (mm), translated
    with the board. r_outer reaches the edge (wall notch); r_inner is the mounting hole."""
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    circles = [(float(e.dxf.center.x), float(e.dxf.center.y), float(e.dxf.radius))
               for e in msp if e.dxftype() == "CIRCLE"]
    radii = sorted({round(r, 3) for _x, _y, r in circles})
    if len(radii) < 2:
        raise RuntimeError(f"expected ear + hole radii in {path}, got {radii}")
    r_inner, r_outer = radii[0], radii[-1]
    bx, by = [], []
    for e in msp:
        if e.dxftype() == "LINE":
            bx += [e.dxf.start.x, e.dxf.end.x]; by += [e.dxf.start.y, e.dxf.end.y]
        elif e.dxftype() == "SPLINE":
            for p in e.control_points:
                bx.append(p[0]); by.append(p[1])
    simon_cx = 0.5 * (min(bx) + max(bx)); simon_cy = 0.5 * (min(by) + max(by))
    # X: track the CONNECTOR/bundle center (matches the board walls in build_downward_polyimide).
    # Y: keep board_placement's bottom-gap placement.
    conn_cx = 0.5 * (np.asarray(sig)[:, 0].min() + np.asarray(sig)[:, 0].max())
    board_cy = board_placement(sig, path)
    tx, ty = conn_cx - simon_cx, board_cy - simon_cy
    corners = [(x + tx, y + ty) for x, y, r in circles if abs(r - r_outer) < 1e-3]
    return corners, r_outer, r_inner


def _wall_notch_arc(x_wall, ncx, ncy, Rc, n=24):
    """Concave arc (mm) a cut-circle (ncx,ncy,Rc) carves into a vertical wall at x=x_wall.
    Ordered so the right wall reads top->bottom and the left wall bottom->top."""
    d = x_wall - ncx
    s = Rc * Rc - d * d
    if s <= 1e-12:
        return []
    alpha = np.arccos(min(1.0, abs(d) / Rc))
    extreme = 0.0 if d > 0 else np.pi
    th = np.linspace(extreme - alpha, extreme + alpha, n)
    return [(ncx + Rc * np.cos(t), ncy + Rc * np.sin(t)) for t in th]


def smootherstep(s):
    """Perlin's C2 ease: 0 at s=0, 1 at s=1, with zero FIRST and SECOND derivative at both ends.

    Used for the polyimide neck taper, NOT for wire fans: eased_fan_profile()'s cosh is vertical
    at its TIGHT end only, so it would smooth the top of the taper and leave a kink where the
    taper rejoins the straight neck below. Peak slope is 15/8 = 1.875x the average, which is what
    sets the peak wall tilt."""
    s = min(1.0, max(0.0, s))
    return s * s * s * (s * (6.0 * s - 15.0) + 10.0)


SMOOTHERSTEP_PEAK_SLOPE = 15.0 / 8.0   # max g'(s), at s = 0.5; sets the taper's steepest tilt


def _taper_wall_pts(x_top, x_bot, y_top, y_bot, res=None):
    """res+1 points down ONE tapering neck wall, (x_top, y_top) -> (x_bot, y_bot).

    y is linear in s, x is eased by smootherstep, so the wall is tangent-VERTICAL at both ends:
    it leaves the graft-width wall above and rejoins the straight neck below with no slope break
    and no curvature step. g(0) = 0 makes the first sample the top vertex itself, so a straight
    collar above the taper start joins the curve tangentially with no extra vertex."""
    n = max(2, int(POLY_FAN_TAPER_RES if res is None else res))
    return [(x_top + (x_bot - x_top) * smootherstep(k / n),
             y_top + (y_bot - y_top) * (k / n)) for k in range(n + 1)]


def build_downward_polyimide(sig, center_x_mm, top_w_mm, top_y_mm,
                             neck_l_mm=None, neck_r_mm=None,
                             flare_l_mm=None, flare_r_mm=None, flare_y_mm=None,
                             flare_y0_mm=None):
    """Neck (top_w wide, at center_x, from top_y) flaring onto Simon's board rectangle at
    the connector, with the 4 corner cut-circles carved into the side walls. Returns the
    closed outline as a list of (x_um, y_um).

    neck_l_mm/neck_r_mm override the neck's left/right wall x (default: symmetric about
    center_x). Pass the jigsaw's actual bottom-edge corners so the neck grafts flush onto
    the jigsaw instead of jogging by (jigsaw_center - center_x); the board stays centered
    on center_x and the flare fillets absorb the small neck<->board offset.

    flare_l_mm/flare_r_mm/flare_y_mm add the FANOUT TAPER: instead of running straight from
    (neck_l, top_y) / (neck_r, top_y) all the way down, each wall eases out (smootherstep, tangent-vertical at both ends) to
    flare_l/flare_r by y = flare_y (put that at the Phase-1 fan bottom, so the substrate
    widens exactly where the bundle does) and continues vertically from there. All three
    must be given together; None -> a constant-width neck (the 8-block).

    flare_y0_mm is where that ramp STARTS (default: the neck top). Below the neck top it
    leaves a straight collar at graft width first; above it the neck is extended upward to
    meet it, which only makes sense because that stretch is inside the jigsaw and the two are
    unioned -- the caller is responsible for keeping it clear of the bond pads."""
    sig = np.asarray(sig)
    bw, bh = board_outline_dims()
    board_cy = board_placement(sig)

    cx = center_x_mm
    top_y = top_y_mm
    top_w = top_w_mm
    r = POLYIMIDE["fillet_r_mm"]
    rb = BOARD_CORNER_R
    n_arc = POLYIMIDE["arc_segments"]

    half_t = top_w / 2.0
    half_b = bw / 2.0
    neck_l = neck_l_mm if neck_l_mm is not None else cx - half_t
    neck_r = neck_r_mm if neck_r_mm is not None else cx + half_t
    board_top_y = board_cy + bh / 2.0
    bot_y = board_cy - bh / 2.0 - BOARD_BOTTOM_EXTEND_MM   # extend the bottom edge down only

    # Below the fanout taper the walls sit at flare_l/flare_r; without a taper they stay at the
    # graft corners. wall_l/wall_r are what the board flare fillets and the bulbs hang off.
    has_flare = None not in (flare_l_mm, flare_r_mm, flare_y_mm)
    wall_l = flare_l_mm if has_flare else neck_l
    wall_r = flare_r_mm if has_flare else neck_r

    # Where the ramp starts, and therefore how high the neck polygon has to reach.
    flare_y0 = flare_y0_mm if (has_flare and flare_y0_mm is not None) else top_y
    neck_top = max(top_y, flare_y0) if has_flare else top_y

    neck_half_max = max(neck_r - cx, cx - neck_l, wall_r - cx, cx - wall_l)
    if neck_half_max + r >= half_b - rb:
        raise ValueError(f"neck flare (neck_half_max+r={neck_half_max + r:.3f}) overruns the top "
                         f"edge (half_b-rb={half_b - rb:.3f}); reduce fillet_r or top_w "
                         f"(or POLY_FAN_MARGIN_UM, if the neck flares)")
    if board_top_y + r >= neck_top:
        raise ValueError(f"neck too short for the flare fillet (board_top_y={board_top_y:.3f}, "
                         f"neck_top={neck_top}, fillet_r={r})")
    if has_flare:
        if not (board_top_y + r < flare_y_mm < flare_y0):
            raise ValueError(f"fanout taper end y={flare_y_mm:.3f} must lie between the board "
                             f"flare fillet ({board_top_y + r:.3f}) and the taper start "
                             f"({flare_y0:.3f})")
        if wall_r < neck_r or wall_l > neck_l:
            raise ValueError(f"fanout taper must widen the neck, not narrow it: "
                             f"neck x[{neck_l:.3f},{neck_r:.3f}] -> x[{wall_l:.3f},{wall_r:.3f}]")

    def arc(center, t0, t1, n, rad):
        cxc, cyc = center
        ts = np.linspace(t0, t1, n + 1)[1:]
        return [(cxc + rad * np.cos(t), cyc + rad * np.sin(t)) for t in ts]

    cut_centers, cut_r, _r_inner = load_board_corner_features(sig)
    right_cuts = sorted((c for c in cut_centers if c[0] > cx), key=lambda c: -c[1])
    left_cuts = sorted((c for c in cut_centers if c[0] < cx), key=lambda c: c[1])

    PI = np.pi
    pts = []
    pts.append((neck_l, neck_top))
    pts.append((neck_r, neck_top))
    if has_flare:
        if flare_y0 < neck_top:
            pts.append((neck_r, flare_y0))        # straight collar before the ease starts
        # right wall eases out across the fan (tangent-vertical at both ends)
        pts += _taper_wall_pts(neck_r, wall_r, flare_y0, flare_y_mm)[1:]
    pts.append((wall_r, board_top_y + r))
    pts += arc((wall_r + r, board_top_y + r), PI, 1.5 * PI, n_arc, r)
    pts.append((cx + half_b - rb, board_top_y))
    pts += arc((cx + half_b - rb, board_top_y - rb), 0.5 * PI, 0.0, n_arc, rb)
    for ncx, ncy in right_cuts:
        pts += _wall_notch_arc(cx + half_b, ncx, ncy, cut_r)
    pts.append((cx + half_b, bot_y + rb))
    pts += arc((cx + half_b - rb, bot_y + rb), 0.0, -0.5 * PI, n_arc, rb)
    pts.append((cx - half_b + rb, bot_y))
    pts += arc((cx - half_b + rb, bot_y + rb), -0.5 * PI, -PI, n_arc, rb)
    for ncx, ncy in left_cuts:
        pts += _wall_notch_arc(cx - half_b, ncx, ncy, cut_r)
    pts.append((cx - half_b, board_top_y - rb))
    pts += arc((cx - half_b + rb, board_top_y - rb), PI, 0.5 * PI, n_arc, rb)
    pts.append((wall_l - r, board_top_y))
    pts += arc((wall_l - r, board_top_y + r), 1.5 * PI, 2.0 * PI, n_arc, r)
    if has_flare:
        # Left wall, walked UPWARD: the same ease reversed, back in to the graft corner. Its last
        # point IS (neck_l, flare_y0) -- the collar vertex when there is a collar, and the ring's
        # own start point when there is not, so drop it in that case rather than repeat it.
        up = _taper_wall_pts(neck_l, wall_l, flare_y0, flare_y_mm)[::-1]
        pts += up if flare_y0 < neck_top else up[:-1]

    UM = 1000.0
    return [(x * UM, y * UM) for x, y in pts]


def edge_bulbs(sig, neck_edge_x_um, neck_top_y_um, side="left", n_bulbs=N_LEFT_BULBS,
               flare_y_um=None):
    """Convex 'bulb' extraction tabs on one straight vertical neck edge. Each bulb is centred ON
    the edge line and its roots blend into the wall via a BULB_FILLET_R_UM fillet (see
    filleted_bulb; 0 -> plain circle). Unioned with the neck it protrudes outward (left edge ->
    left, right edge -> right). Returns shapely Polygons (um).

    neck_edge_x_um is the x of the STRAIGHT part of the wall (i.e. the flared wall when the
    fanout taper is on); flare_y_um keeps the topmost bulb below the taper, so no bulb can
    straddle the sloped stretch and come out lopsided."""
    bw, bh = board_outline_dims()
    bcy = board_placement(sig)
    board_top_y_um = (bcy + bh / 2.0) * 1000.0
    neck_bottom_y = board_top_y_um + POLYIMIDE["fillet_r_mm"] * 1000.0   # flare start
    y_hi = neck_top_y_um - LEFT_BULB_MARGIN_UM - 5000.0
    if flare_y_um is not None:
        y_hi = min(y_hi, flare_y_um - LEFT_BULB_MARGIN_UM)
    y_lo = neck_bottom_y + LEFT_BULB_MARGIN_UM
    ys = np.linspace(y_hi, y_lo, n_bulbs)
    bulbs = [filleted_bulb(neck_edge_x_um, float(y), LEFT_BULB_R_UM, BULB_FILLET_R_UM, side, CIRCLE_RES)
             for y in ys]
    base_h = 2.0 * math.sqrt(LEFT_BULB_R_UM**2 + 2.0 * LEFT_BULB_R_UM * BULB_FILLET_R_UM)
    pitch = abs(ys[1] - ys[0]) if len(ys) >= 2 else 0.0   # pitch undefined for a single bulb
    print(f"{side} bulbs: {n_bulbs} x r={LEFT_BULB_R_UM:.0f} um (fillet {BULB_FILLET_R_UM:.0f} um, "
          f"base {base_h:.0f} um) at x={neck_edge_x_um:.0f}, "
          f"y {ys[0]:.0f}..{ys[-1]:.0f} um (pitch {pitch:.0f} um)")
    return bulbs
