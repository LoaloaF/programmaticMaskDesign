"""Stage 02 for wafer6's 12-block: the NEW routing into the connector. Everything on Metal1, no vias.

    python3 02_route_wafer6.py --design 12block --in X_padgrid.dxf --out X.dxf   (build_designs.py runs it)

The old router (02_route.py) stays as it is for wafer4; this one reuses only its Phase 1 (band -> fan)
and its polyimide, and replaces the rest. Geometry is fixed in this file on purpose -- the numbers
below ARE the design, not knobs. The one setting to iterate on is the lane pitch, catalog.py wafer6
FAN_TARGET_PITCH (= 2 um wire + the gap between wires; 2.5 um gap is the floor).

The 768 band wires, left to right, are four quarters of 192:

    outer-left    turns LEFT off the neck (big radius), runs along the raised top edge of the
                  board, turns DOWN along the side wall, and peels 64 wires into blocks 1, 3, 5 of
                  the left column (counted from the top) from the wall side
    centre-left   runs straight down the middle; peels 64 wires LEFT into blocks 2, 4, 6
    centre-right  the mirror of centre-left (right column blocks 2, 4, 6)
    outer-right   the mirror of outer-left (right column blocks 1, 3, 5)

So, numbering the connectors 1-6 down the left column and 7-12 up the right one, the side a block is
fed from alternates L, R, L, R ... all the way round, 6 -> 7 included.

THIS ITERATION: every 64-wire group bends in towards its block and STOPS short of the pads; the pads
are drawn (fixed positions, no wires to them yet). The polyimide grows upward over the board's two
top shoulders to carry the outer quarters.
"""
import importlib.util
import math
import os
import sys

import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.prepared import prep
from shapely.strtree import STRtree

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("route_base", os.path.join(HERE, "02_route.py"))
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)          # parses this process's --design/--in/--out (see 02_route._cli)

from lib.board import board_outline_dims, board_placement, load_board_corner_features  # noqa: E402
from lib.constants import CIRCLE_RES, CONN_PAD_LAYER, WIRE_LAYER  # noqa: E402
from lib.connector import _load_extended_sig, translated_signal_pads  # noqa: E402
from lib.geometry import create_polygon_circle, ensure_layer, stroke_centerline_to_polygon  # noqa: E402

DSG = base.DSG

# ======================================================================================
# The design (um unless noted)
# ======================================================================================
# Connector pads: EXACTLY where wafer4 Rev4 / wafer6 so far put them (02_route.py: centred on the
# bundle, top row CONN_TOP_BELOW_VIA_MM below the old via row). Fixed -- never derived again.
CONNECTOR_DX_MM = -6.653499999999999
CONNECTOR_DY_MM = -29.77399969578025

WIRE_W = 2.0              # every wire, the whole way (Phase 1's width)
PI_CLEAR = 150.0          # wire edge -> polyimide edge (as the neck: POLY_FAN_MARGIN_UM)
R_OFF_NECK = 1000.0       # outer quarter, neck -> top edge: radius of its INNERMOST wire
R_TO_WALL = 300.0         # outer quarter, top edge -> side wall: radius of its innermost wire
TOP_RUN_ABOVE_PADS = 150.0  # lowest top-edge wire edge above the top pad row's pad edge
R_NOTCH_JOG = 300.0       # the S-jog that takes the wall bundle in past a mounting-hole notch
NOTCH_CLEAR = PI_CLEAR    # wall wire edge -> notch
R_INTO_LANE = 150.0       # a 32-wire group's turn into its lane: radius of its first-turning wire
LANE_STUB = 500.0         # the group then runs straight into its lane, ending this far past the block's
                          # outermost pad edge (not connected yet)
PEEL_GAP = 20.0           # below a peeled group's lowest wire, before the rest jog
SHOULDER_R = 800.0        # outer top corner of the raised shoulder (as the board's corners)
SEG = 10.0                # arc sampling: max segment length (um) ...
SAGITTA = 0.005           # ... and max chord error (um)

# Helper layers (NOT on any mask -- the wafer export drops them): Alexei's 12x64 board (2026-06-26),
# assets/board_12x64_2026-06-26/ = the Gerber outline (.GM3) and Excellon drill file (.TXT) from
# mea1k_connector/BoardsWithConnectors2026-06-26_Alexei/12x64/12x64_grb.zip. Board mm = connector-CSV mm
# + BOARD_FROM_CSV_MM: that maps all 384 left-column pads onto their drills exactly (the right column's
# drills sit 15 um further right -- printed by the check below).
BOARD_DIR = os.path.join(HERE, "assets", "board_12x64_2026-06-26")
BOARD_FROM_CSV_MM = (0.35, 8.082)
HELPER_OUTLINE = "HELPER_board_outline"
HELPER_DRILL = "HELPER_board_drill"


# ======================================================================================
# Path helpers: a wire is a list of points, built from straight runs and circular arcs
# ======================================================================================
def arc(cx, cy, r, a0, a1):
    """Points on the circle (cx, cy, r) from angle a0 to a1 (radians, either direction), a0 excluded."""
    # chord error <= SAGITTA, and segments no longer than SEG: neighbouring arcs then hold their gap
    step = min(SEG / r, 2.0 * math.acos(1.0 - SAGITTA / r))
    n = max(4, int(math.ceil(abs(a1 - a0) / step)))
    return [(cx + r * math.cos(a), cy + r * math.sin(a)) for a in np.linspace(a0, a1, n + 1)[1:]]


def s_jog(x, y0, d_out, r1, r2, shift):
    """A vertical wire at x, heading down, shifted `shift` to the RIGHT by two opposite arcs from y0.
    d_out = how far this wire sits LEFT of the bundle's reference (innermost) wire; r1/r2 are the
    reference wire's radii, so every wire of the bundle stays parallel (radii r1 + d, r2 - d)."""
    th = math.acos(1.0 - shift / (r1 + r2))
    ra, rb = r1 + d_out, r2 - d_out
    pts = arc(x + ra, y0, ra, math.pi, math.pi + th)
    px, py = pts[-1]
    c2x, c2y = px - rb * math.cos(th), py - rb * math.sin(th)
    pts += arc(c2x, c2y, rb, th, 0.0)
    return pts, pts[-1][1]


def mirror(pts, xm):
    return [(2.0 * xm - x, y) for x, y in pts]


# ======================================================================================
def blocks_of(sig_um, left):
    """The 6 blocks of one connector column, top to bottom: (y_centre, pad x min, pad x max, top row y,
    the 4 row ys top to bottom). A block's rows come as 1 | 2 3 | 4: the two wide lanes (740 um) are
    between rows 1-2 and 3-4; 2-3 are only 370 um apart and stay free."""
    xm = 0.5 * (sig_um[:, 0].min() + sig_um[:, 0].max())
    col = sig_um[sig_um[:, 0] < xm] if left else sig_um[sig_um[:, 0] >= xm]
    ys = np.unique(np.round(col[:, 1], 3))[::-1]
    if len(ys) != 24:
        raise RuntimeError("expected 24 pad rows per column, got %d" % len(ys))
    out = []
    for b in range(6):
        rows = ys[4 * b: 4 * b + 4]
        out.append((0.5 * (rows[0] + rows[-1]), col[:, 0].min(), col[:, 0].max(), rows[0], rows))
    return out


def route_left(lanes, y_start, sig_um, board_wall_x, notch_tip_x, pitch):
    """{band index: path from (lane, y_start) down} for the LEFT half (indices 0..383)."""
    padr = DSG.PADR * 1000.0
    blocks = blocks_of(sig_um, left=True)
    paths = {}

    # ---------------- outer-left quarter: i = 0..191 (i = 191 innermost, next to the blocks)
    n = 192
    top_pad_edge = blocks[0][3] + padr
    y_low = top_pad_edge + TOP_RUN_ABOVE_PADS + WIRE_W / 2.0          # i = 191 on the top run
    yt = y_low + R_OFF_NECK + (n - 1) * pitch                          # where every wire leaves the neck
    c1x = lanes[0] - R_OFF_NECK
    x_out = board_wall_x + PI_CLEAR + WIRE_W / 2.0                    # i = 0 on the wall run
    c2x = x_out + R_TO_WALL + (n - 1) * pitch
    c2y = y_low - R_TO_WALL
    wall_x = {}
    for i in range(n):
        r1 = lanes[i] - c1x
        y_run = yt - r1
        r2 = y_run - c2y
        p = [(lanes[i], y_start), (lanes[i], yt)]
        p += arc(c1x, yt, r1, 0.0, -math.pi / 2)                     # down -> left
        p += [(c2x, y_run)]
        p += arc(c2x, c2y, r2, math.pi / 2, math.pi)                  # left -> down
        paths[i] = p
        wall_x[i] = c2x - r2
    geo = dict(yt=yt, c1x=c1x, top_y=yt - R_OFF_NECK, x_out=x_out)

    # Peel into blocks 1, 3, 5 (index 0, 2, 4): the innermost 64 first, as two 32s -- the inner 32
    # into the upper lane (rows 1-2), the outer 32 into the lower lane (rows 3-4).
    remaining = list(range(n))
    for b in (0, 2, 4):
        grp = remaining[-64:]
        remaining = remaining[:-64]
        _yc, pxmin, _pxmax, _ytop, rows = blocks[b]
        x_end = pxmin - padr + LANE_STUB
        y_lowest = None
        for sub, y_lane in ((grp[32:], 0.5 * (rows[0] + rows[1])), (grp[:32], 0.5 * (rows[2] + rows[3]))):
            c3x = wall_x[sub[-1]] + R_INTO_LANE
            r_lo = c3x - wall_x[sub[0]]
            ys = y_lane + 0.5 * (R_INTO_LANE + r_lo)                  # the 32 are centred on the lane
            if ys > paths[sub[0]][-1][1]:
                raise RuntimeError("block %d: no room to turn into the lane at y=%.0f" % (b + 1, y_lane))
            for i in sub:
                r3 = c3x - wall_x[i]
                paths[i] += [(wall_x[i], ys)] + arc(c3x, ys, r3, math.pi, 1.5 * math.pi)   # down -> right
                paths[i] += [(x_end, ys - r3)]
            y_lowest = ys - r_lo
        if not remaining:
            break
        # The rest jog in past the mounting-hole notch below this block, if they would hit it.
        y0 = y_lowest - WIRE_W / 2.0 - PEEL_GAP
        need = notch_tip_x + NOTCH_CLEAR + WIRE_W / 2.0 - wall_x[remaining[0]]
        if b == 0 and need > 0:
            ref = remaining[-1]
            d_max = wall_x[ref] - wall_x[remaining[0]]
            for i in remaining:
                d = wall_x[ref] - wall_x[i]
                pts, _ = s_jog(wall_x[i], y0, d, R_NOTCH_JOG, R_NOTCH_JOG + d_max, need)
                paths[i] += [(wall_x[i], y0)] + pts
                wall_x[i] = pts[-1][0]
            geo["jog"] = (y0, need)

    # ---------------- centre-left quarter: i = 192..383 straight down; peel LEFT into blocks 2, 4, 6,
    # each as two 32s: the left 32 into the upper lane, the next 32 into the lower one
    rest = list(range(192, 384))
    for b in (1, 3, 5):
        grp, rest = rest[:64], rest[64:]
        _yc, _pxmin, pxmax, _ytop, rows = blocks[b]
        x_end = pxmax + padr - LANE_STUB
        for sub, y_lane in ((grp[:32], 0.5 * (rows[0] + rows[1])), (grp[32:], 0.5 * (rows[2] + rows[3]))):
            c4x = lanes[sub[0]] - R_INTO_LANE
            r_hi = lanes[sub[-1]] - c4x
            ys = y_lane + 0.5 * (R_INTO_LANE + r_hi)
            for i in sub:
                r = lanes[i] - c4x
                paths[i] = ([(lanes[i], y_start), (lanes[i], ys)] + arc(c4x, ys, r, 0.0, -math.pi / 2)  # down -> left
                            + [(x_end, ys - r)])
    return paths, geo


def board_helper(msp, to_um):
    """Alexei's board outline + drill holes on the HELPER_* layers; to_um maps board mm -> our um."""
    import re
    ensure_layer(msp.doc, HELPER_OUTLINE, 3)
    ensure_layer(msp.doc, HELPER_DRILL, 1)
    # Gerber outline: FSLAX44Y44, mm; G01 lines, G03 (CCW) / G02 (CW) arcs with I/J centre offsets
    x = y = 0.0
    mode = 1
    for ln in open(os.path.join(BOARD_DIR, "12x64.GM3")):
        for g in re.findall(r"G0([123])", ln):
            mode = int(g)
        m = re.match(r"^(?:X(-?\d+))?(?:Y(-?\d+))?(?:I(-?\d+))?(?:J(-?\d+))?D0([12])\*", ln.strip())
        if not m:
            continue
        nx = int(m.group(1)) / 1e4 if m.group(1) else x
        ny = int(m.group(2)) / 1e4 if m.group(2) else y
        if m.group(5) == "1":
            if mode == 1:
                pts = [(x, y), (nx, ny)]
            else:
                cx, cy = x + int(m.group(3) or 0) / 1e4, y + int(m.group(4) or 0) / 1e4
                a0, a1 = math.atan2(y - cy, x - cx), math.atan2(ny - cy, nx - cx)
                if mode == 3 and a1 <= a0:
                    a1 += 2 * math.pi
                if mode == 2 and a1 >= a0:
                    a1 -= 2 * math.pi
                r = math.hypot(x - cx, y - cy)
                pts = [(x, y)] + [(cx + r * math.cos(a), cy + r * math.sin(a))
                                  for a in np.linspace(a0, a1, 48)[1:]]
            msp.add_lwpolyline([to_um(*p) for p in pts], dxfattribs={"layer": HELPER_OUTLINE})
        x, y = nx, ny
    # Excellon drills: METRIC, FILE_FORMAT 4:4 with leading zeros -> first 4 digits are whole mm
    sizes, tool, holes = {}, None, []
    hx = hy = 0.0
    for ln in open(os.path.join(BOARD_DIR, "12x64.TXT")):
        ln = ln.strip()
        m = re.match(r"^T(\d+)C([\d.]+)$", ln.replace("F00S00", ""))
        if m:
            sizes[int(m.group(1))] = float(m.group(2))
            continue
        m = re.match(r"^T(\d+)$", ln)
        if m:
            tool = int(m.group(1))
            continue
        m = re.match(r"^(?:X(-?\d+))?(?:Y(-?\d+))?$", ln)
        if ln and ln[0] in "XY" and m:
            def mm(v):
                neg = v.startswith("-")
                v = v.lstrip("-").ljust(8, "0")
                return (-1 if neg else 1) * (int(v[:4]) + int(v[4:]) / 1e4)
            hx = mm(m.group(1)) if m.group(1) else hx
            hy = mm(m.group(2)) if m.group(2) else hy
            holes.append((hx, hy, sizes[tool] / 2.0))
    for hx, hy, r in holes:
        cx, cy = to_um(hx, hy)
        msp.add_lwpolyline(create_polygon_circle(cx, cy, r * 1000.0, CIRCLE_RES), close=True,
                           dxfattribs={"layer": HELPER_DRILL})
    return np.array([to_um(hx, hy) + (r,) for hx, hy, r in holes])


def shoulder(geo, neck_l, wall_x, y_cut):
    """The raised LEFT shoulder: neck wall -> concentric fillet round the outer quarter's first turn ->
    flat top PI_CLEAR above its top wire -> rounded corner -> side wall, down to y_cut."""
    # concentric with the outer quarter's first turn, PI_CLEAR outside its first wire; it meets the neck
    # wall tangentially when the neck hugs the bundle (flared), else a little below the turn
    rf = R_OFF_NECK - PI_CLEAR - WIRE_W / 2.0
    y_top = geo["yt"] - rf
    pts = [(geo["xm"], y_cut), (geo["xm"], geo["yt"]), (geo["c1x"] + rf, geo["yt"])]
    pts += arc(geo["c1x"], geo["yt"], rf, 0.0, -math.pi / 2)
    pts += [(wall_x + SHOULDER_R, y_top)]
    pts += arc(wall_x + SHOULDER_R, y_top - SHOULDER_R, SHOULDER_R, math.pi / 2, math.pi)
    pts += [(wall_x, y_cut)]
    return Polygon(pts), y_top


def main():
    print("=== wafer6 router: Phase 1 (band -> fan), Metal1 only ===")
    doc, msp, _c = base.load_and_rotate_interconnect(DSG)
    endpoints = base.extract_band_endpoints(msp)
    actual = None
    if base.BAND_REF_DXF:
        print("building around the band of %s; real band joins it by a %.0f um jog"
              % (os.path.basename(base.BAND_REF_DXF), base.BAND_JOG_LEN))
        _rd, ref_msp, _rc = base.load_and_rotate_interconnect(DSG, base.BAND_REF_DXF)
        ref = base.extract_band_endpoints(ref_msp)
        base.check_band_pairing(endpoints, ref, max_dx=getattr(base, "BAND_REF_MAX_DX", 5.0))
        actual, endpoints = endpoints, ref
    ic_pads_bbox = base.interconnect_pad_bbox(msp)
    pitch = base.FAN_TARGET_PITCH
    lanes, x_center, _ = base.build_fan_lanes(endpoints, target_pitch=pitch)
    xs = sorted(p[0] for p in endpoints)
    native = min(b - a for a, b in zip(xs, xs[1:]))
    D = max(abs(p[0] - x_center) for p in endpoints)
    gap = native - WIRE_W
    fan_g, fan_len = base.eased_fan_profile(native, pitch, D, gap, WIRE_W, base.FAN_TILT_CAP_DEG,
                                            base.FAN_RES, base.FAN_LEN_SLACK)
    if base.FAN_LEN is not None:
        fan_len = base.FAN_LEN
    base.R = dict(base.R, two_layer=False)          # no via row: every wire stays on Metal1
    base.VIA_ROW_GAP = 0.0                          # hand-off at the fan bottom; the routes below take over
    info = base.build_transition(endpoints, lanes, fan_g, fan_len, actual=actual)
    print("fan: native pitch %.2f -> lane pitch %.2f um (wire %.1f, gap %.2f um), len %.0f um"
          % (native, pitch, WIRE_W, pitch - WIRE_W, fan_len))
    ok1, msgs = base.check_transition(DSG, info, lanes, pitch, gap)
    for m in msgs:
        if "via" not in m:
            print("  " + m)

    # ---- connector pads, fixed
    sig = translated_signal_pads(_load_extended_sig(DSG), CONNECTOR_DX_MM, CONNECTOR_DY_MM)
    sig_um = sig * 1000.0
    padr = DSG.PADR * 1000.0

    # ---- board walls + notches (the polyimide the old router builds, before our shoulders)
    bw, _bh = board_outline_dims(DSG)
    conn_cx_um = 0.5 * (sig[:, 0].min() + sig[:, 0].max()) * 1000.0
    wall_l = conn_cx_um - bw * 500.0
    corners, r_outer, _ri = load_board_corner_features(DSG, sig)
    notch_l = max(cx * 1000.0 + r_outer * 1000.0 for cx, _cy in corners if cx * 1000.0 < conn_cx_um)
    xm = x_center                                    # lanes are symmetric about it: lane[767-i] = 2 xm - lane[i]

    y_start = info["y_fan"]
    left, geo = route_left(lanes, y_start, sig_um, wall_l, notch_l, pitch)
    geo["xm"] = xm
    paths = dict(left)
    for i, p in left.items():
        paths[767 - i] = mirror(p, xm)
    print("outer quarters: leave the neck at y=%.0f (inner radius %.0f um), top run up to y=%.0f, "
          "wall run from x=%.0f (left) / %.0f (right)"
          % (geo["yt"], R_OFF_NECK, geo["top_y"], geo["x_out"], 2 * xm - geo["x_out"]))
    if "jog" in geo:
        print("  past the notch below block 1: the remaining 128 wires jog %.0f um in from y=%.0f"
              % (geo["jog"][1], geo["jog"][0]))

    # ---- emit: Phase-1 head + route, one polyline per wire
    ensure_layer(doc, WIRE_LAYER, 4)
    ensure_layer(doc, CONN_PAD_LAYER, 2)
    lines = []
    for i in range(768):
        poly = list(info["m1_polys"][i]) + paths[i][1:]
        lines.append(LineString(poly))
        ring = stroke_centerline_to_polygon(poly, [WIRE_W] * len(poly))
        msp.add_lwpolyline(ring, close=True, dxfattribs={"layer": WIRE_LAYER})
    for x, y in sig_um:
        msp.add_lwpolyline(create_polygon_circle(x, y, padr, CIRCLE_RES), close=True,
                           dxfattribs={"layer": WIRE_LAYER})
        msp.add_lwpolyline(create_polygon_circle(x, y, (base.CONN_PAD_R or DSG.PADR) * 1000.0, CIRCLE_RES),
                           close=True, dxfattribs={"layer": CONN_PAD_LAYER})
    print("connector pads: %d at fixed positions (dx %.4f, dy %.4f mm), not connected yet"
          % (len(sig_um), CONNECTOR_DX_MM, CONNECTOR_DY_MM))

    def to_um(bx, by):
        return ((bx - BOARD_FROM_CSV_MM[0] + CONNECTOR_DX_MM) * 1000.0,
                (by - BOARD_FROM_CSV_MM[1] + CONNECTOR_DY_MM) * 1000.0)
    holes = board_helper(msp, to_um)
    dtree = STRtree([Point(h[0], h[1]) for h in holes])
    off = np.array([Point(x, y).distance(dtree.geometries[dtree.query_nearest(Point(x, y))[0]])
                    for x, y in sig_um])
    left_pad = sig_um[:, 0] < x_center
    print("board helper (%s, %s): %d drills (%d not at a pad), outline; pad -> its drill: left column "
          "max %.1f um, right column %.1f..%.1f um"
          % (HELPER_OUTLINE, HELPER_DRILL, len(holes), len(holes) - len(sig_um),
             off[left_pad].max(), off[~left_pad].min(), off[~left_pad].max()))

    # ---- polyimide: the old outline with both top shoulders raised over the outer quarters
    orig_build = base.build_downward_polyimide
    raised = {}

    def build_with_shoulders(*a, **kw):
        poly = Polygon(orig_build(*a, **kw))
        x0, _y0, x1, _y1 = poly.bounds
        flared = kw.get("flare_l_mm") is not None
        neck_l = (kw["flare_l_mm"] if flared else kw["neck_l_mm"]) * 1000.0
        neck_r = (kw["flare_r_mm"] if flared else kw["neck_r_mm"]) * 1000.0
        board_top = (board_placement(DSG, sig) + _bh / 2.0) * 1000.0
        y_cut = board_top - base.BOARD_CORNER_R * 1000.0 - 1.0
        sh_l, y_top = shoulder(geo, neck_l, x0, y_cut)
        sh_r_m, _ = shoulder(geo, 2 * xm - neck_r, 2 * xm - x1, y_cut)     # built on the left, mirrored
        sh_r = Polygon(mirror(list(sh_r_m.exterior.coords), xm))
        strip = Polygon([(neck_l, y_cut), (neck_r, y_cut), (neck_r, 1e6), (neck_l, 1e6)])
        below = Polygon([(x0 - 1, -1e6), (x1 + 1, -1e6), (x1 + 1, y_cut), (x0 - 1, y_cut)])
        out = poly.intersection(below.union(strip)).union(sh_l).union(sh_r)
        if out.geom_type != "Polygon":
            raise RuntimeError("raised shoulders: polyimide is %s" % out.geom_type)
        raised["top"], raised["was"] = y_top, board_top
        return [tuple(c) for c in out.exterior.coords]

    base.build_downward_polyimide = build_with_shoulders
    print("\n=== polyimide ===")
    merged, _holes = base.emit_polyimide(doc, msp, sig, info, ic_pads_bbox=ic_pads_bbox)
    if merged.geom_type != "Polygon":
        raise RuntimeError("polyimide came out as %d pieces: %s" % (len(merged.geoms),
                           [[round(v) for v in g.bounds] for g in merged.geoms]))
    print("shoulders raised: board top y=%.0f -> %.0f um (+%.0f um), outer corner r %.0f um"
          % (raised["was"], raised["top"], raised["top"] - raised["was"], SHOULDER_R))

    # ---- checks (centrelines; edge gap = distance - WIRE_W)
    tree = STRtree(lines)
    worst = (1e9, None)
    for i, ln in enumerate(lines):
        for j in tree.query(ln, predicate="dwithin", distance=WIRE_W + gap + 5.0):
            if j > i:
                d = ln.distance(lines[j]) - WIRE_W
                if d < worst[0]:
                    worst = (d, (i, j))
    pads = [Point(x, y).buffer(padr, 32) for x, y in sig_um]
    ptree = STRtree(pads)
    pad_min = min(ln.distance(pads[ptree.query_nearest(ln)[0]]) for ln in lines) - WIRE_W / 2.0
    pi_min = min(ln.distance(merged.exterior) for ln in lines) - WIRE_W / 2.0
    pm = prep(merged.buffer(1e-6))
    outside = sum(1 for ln in lines if not pm.contains(ln))
    from shapely.ops import nearest_points
    at = nearest_points(lines[worst[1][0]], lines[worst[1][1]])[0]
    print("\nchecks: min wire gap %.3f um (wires %s at %.0f, %.0f), floor 2.5: %s" % (worst[0], worst[1], at.x, at.y,
          "OK" if worst[0] >= 2.5 - 5e-3 else "FAIL"))
    print("        min wire -> connector pad edge %.1f um; min wire -> polyimide edge %.1f um "
          "(target %.0f); wires off the polyimide: %d" % (pad_min, pi_min, PI_CLEAR, outside))

    doc.saveas(base.FINAL_DXF)
    print("wrote %s" % base.FINAL_DXF)


if __name__ == "__main__":
    main()
