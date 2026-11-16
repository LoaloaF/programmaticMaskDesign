"""Stage 02 for wafer6's 12-block: the NEW routing into the connector. Everything on Metal1, no vias.

    python3 02_route_wafer6.py --design 12block --in X_padgrid.dxf --out X.dxf   (build_designs.py runs it)

The old router (02_route.py) stays as it is for wafer4; this one reuses only its Phase 1 (band -> fan)
and its polyimide machinery (neck, bulbs, Polyimide_Negative band), and replaces the rest. Geometry is
fixed in this file on purpose -- the numbers below ARE the design.

The board is Alexei's 12x64 (assets/board_12x64_2026-06-26/, Gerber outline + Excellon drills). It is
the ground truth: the connector pads sit on its pad drills, the polyimide outline IS its outline (plus
the neck and the neck -> board fillets), and its 4 corner mounting holes get a 0.8 mm keep-out.

Lane pitch: WIDE (catalog wafer6 FAN_TARGET_PITCH, 6.5 um = 2 um wire + 4.5 um gap) wherever there is
room; TIGHT (4.5 um, the 2.5 um gap floor) only where it is tight: the outer bundles' run along the top
edge (between the mounting hole and block 1) and the start of the centre bundle (between the columns).

The 768 band wires, left to right, in 64-wire groups g0..g11:

    g0-g1    outer-left: off the neck to the left, along the top edge under the mounting hole, down
             the side wall (clear of the wall notches, no jog), into blocks 1 (g1) and 3 (g0) of the
             left column (counted from the top), from the wall side
    g2-g5    centre-left: straight down the middle, peeling LEFT into blocks 2 (g2), 4, 5, 6 (g5)
    g6-g9    centre-right: the mirror -- blocks 6 (g6), 5, 4, 2 (g9) of the right column
    g10-g11  outer-right: the mirror -- blocks 1 (g10) and 3 (g11) of the right column

Each 64 enters its block as two 32s, one per wide lane: rows 1-2 and rows 3-4 (2-3 is too narrow).
THIS ITERATION: the 32s run 500 um into their lanes and stop; the pads are drawn, not connected yet.
"""
import importlib.util
import math
import os
import re

import numpy as np
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import nearest_points, polygonize, unary_union
from shapely.prepared import prep
from shapely.strtree import STRtree

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("route_base", os.path.join(HERE, "02_route.py"))
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)          # parses this process's --design/--in/--out (see 02_route._cli)

from lib.board import smootherstep  # noqa: E402
from lib.constants import CIRCLE_RES, CONN_PAD_LAYER, WIRE_LAYER  # noqa: E402
from lib.connector import _load_extended_sig  # noqa: E402
from lib.geometry import (create_polygon_circle, ensure_layer, filleted_bulb,  # noqa: E402
                          stroke_centerline_to_polygon)

DSG = base.DSG

# ======================================================================================
# The design (um unless noted)
# ======================================================================================
# Our frame: the left connector column stays exactly where wafer4 Rev4 has it. Board mm -> our um:
#   x = (bx - BOARD_FROM_CSV_MM[0] + CONNECTOR_DX_MM) * 1000, likewise y.
# (The connector CSV + BOARD_FROM_CSV_MM puts all 384 left pads on Alexei's drills exactly; his right
# column sits 15 um further right than the CSV's -- his is used.)
CONNECTOR_DX_MM = -6.653499999999999
CONNECTOR_DY_MM = -29.77399969578025
BOARD_DIR = os.path.join(HERE, "assets", "board_12x64_2026-06-26")
BOARD_FROM_CSV_MM = (0.35, 8.082)

WIRE_W = 2.0              # every wire, the whole way
TIGHT = 4.5               # lane pitch where it is tight (2.5 um gap)
PI_CLEAR = 150.0          # wire edge -> polyimide edge
NOTCH_CLEAR = 150.0       # wall-run wire edge -> the wall notches (the wall run never jogs for them)
HOLE_KEEPOUT_D = 800.0    # keep-out circle round each corner mounting hole (the drill is 0.5 mm)
HOLE_CLEAR = 100.0        # wire edge -> that keep-out
PAD_CLEAR_MIN = 100.0     # top-run wire edge -> block 1's pad edge, at least
L_FAN_IN = 3000.0         # neck fan-in WIDE -> TIGHT, ending just above the outer bundles' turn
R_OFF_NECK = 1000.0       # outer bundle, neck -> top edge: radius of its innermost wire
R_TO_WALL = 300.0         # outer bundle, top edge -> side wall: radius of its innermost wire
L_WIDEN = 1500.0          # a fan-out TIGHT -> WIDE once there is room
R_INTO_LANE = 150.0       # a 32's turn into its lane: radius of its first-turning wire
LANE_STUB = 500.0         # the 32s end this far past the block's outermost pad edge (not connected yet)
PEEL_GAP = 20.0           # below a peeled group's lowest wire, before the rest change pitch
FAN_RES = 96              # samples per eased fan
SEG, SAGITTA = 10.0, 0.001  # arc sampling: max segment (um), max chord error (um)
WALL_BLOCKS = (0, 2)      # blocks (0 = top) fed from the wall; the rest from the centre
CENTRE_BLOCKS = (1, 3, 4, 5)

HELPER_OUTLINE = "HELPER_board_outline"
HELPER_DRILL = "HELPER_board_drill"
HELPER_KEEPOUT = "HELPER_mount_keepout"


# ======================================================================================
# Paths: a wire is a list of points, built from straight runs, eased fans and circular arcs
# ======================================================================================
def arc(cx, cy, r, a0, a1):
    """Points on the circle (cx, cy, r) from angle a0 to a1 (radians, either direction), a0 excluded."""
    step = min(SEG / r, 2.0 * math.acos(1.0 - SAGITTA / r))
    n = max(4, int(math.ceil(abs(a1 - a0) / step)))
    return [(cx + r * math.cos(a), cy + r * math.sin(a)) for a in np.linspace(a0, a1, n + 1)[1:]]


def ease(x0, x1, y0, length):
    """A vertical wire moving from x0 to x1 over `length` downwards from y0 (smootherstep: vertical at
    both ends). (x0, y0) excluded."""
    return [(x0 + (x1 - x0) * smootherstep(k / FAN_RES), y0 - length * k / FAN_RES)
            for k in range(1, FAN_RES + 1)]


def mirror(pts, xm):
    return [(2.0 * xm - x, y) for x, y in pts]


# ======================================================================================
# Alexei's board
# ======================================================================================
def read_board():
    """(outline segments, drills) in board mm: segments = point lists; drills = (x, y, diameter)."""
    segs, x, y, mode = [], 0.0, 0.0, 1
    for ln in open(os.path.join(BOARD_DIR, "12x64.GM3")):      # Gerber, FSLAX44Y44, mm
        for g in re.findall(r"G0([123])", ln):
            mode = int(g)
        m = re.match(r"^(?:X(-?\d+))?(?:Y(-?\d+))?(?:I(-?\d+))?(?:J(-?\d+))?D0([12])\*", ln.strip())
        if not m:
            continue
        nx = int(m.group(1)) / 1e4 if m.group(1) else x
        ny = int(m.group(2)) / 1e4 if m.group(2) else y
        if m.group(5) == "1":
            if mode == 1:
                segs.append([(x, y), (nx, ny)])
            else:
                cx, cy = x + int(m.group(3) or 0) / 1e4, y + int(m.group(4) or 0) / 1e4
                a0, a1 = math.atan2(y - cy, x - cx), math.atan2(ny - cy, nx - cx)
                if mode == 3 and a1 <= a0:
                    a1 += 2 * math.pi
                if mode == 2 and a1 >= a0:
                    a1 -= 2 * math.pi
                r = math.hypot(x - cx, y - cy)
                mid = [(cx + r * math.cos(a), cy + r * math.sin(a)) for a in np.linspace(a0, a1, 97)[1:-1]]
                segs.append([(x, y)] + mid + [(nx, ny)])
        x, y = nx, ny
    sizes, tool, drills, hx, hy = {}, None, [], 0.0, 0.0
    for ln in open(os.path.join(BOARD_DIR, "12x64.TXT")):      # Excellon, METRIC, 4:4 leading zeros
        ln = ln.strip()
        m = re.match(r"^T(\d+)F00S00C([\d.]+)$", ln)
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
            drills.append((hx, hy, sizes[tool]))
    return segs, np.array(drills)


def to_um(bx, by):
    return ((bx - BOARD_FROM_CSV_MM[0] + CONNECTOR_DX_MM) * 1000.0,
            (by - BOARD_FROM_CSV_MM[1] + CONNECTOR_DY_MM) * 1000.0)


def board_geometry():
    """Everything taken from the board, in our um: outline polygon + its segments, connector pads (768,
    on his pad drills), all drills, mount holes, the left wall notch tip x."""
    segs_mm, drills_mm = read_board()
    segs = [[to_um(*p) for p in s] for s in segs_mm]
    # the Gerber's segment ends miss each other by up to 0.2 um: snap them onto one another (1 um)
    ends = []
    for s in segs:
        for k in (0, -1):
            hit = next((e for e in ends if math.hypot(e[0] - s[k][0], e[1] - s[k][1]) < 1.0), None)
            if hit is None:
                ends.append(s[k])
            else:
                s[k] = hit
    outline = max(polygonize(unary_union([LineString(s) for s in segs])), key=lambda g: g.area)
    drills = np.array([to_um(x, y) + (d * 1000.0,) for x, y, d in drills_mm])
    csv = _load_extended_sig(DSG) + np.array([CONNECTOR_DX_MM, CONNECTOR_DY_MM])
    csv_um = csv * 1000.0
    pads = []
    for x, y in csv_um:
        d = np.hypot(drills[:, 0] - x, drills[:, 1] - y)
        j = int(np.argmin(d))
        if d[j] > 20.0:
            raise RuntimeError("connector pad at (%.0f, %.0f) has no board drill within 20 um" % (x, y))
        pads.append(drills[j, :2])
    pads = np.array(pads)
    shift = np.hypot(*(pads - csv_um).T)
    mounts = drills[drills[:, 2] > 400.0]
    x0, y0, x1, y1 = outline.bounds
    v = np.array(outline.exterior.coords)
    mid = (v[:, 1] > y0 + 3000.0) & (v[:, 1] < y1 - 3000.0)
    notch_l = v[mid & (v[:, 0] < x0 + 1500.0)][:, 0].max()
    return dict(outline=outline, segs=segs, drills=drills, pads=pads, mounts=mounts, notch_l=notch_l,
                pad_shift=(shift[csv_um[:, 0] < csv_um[:, 0].mean()].max(),
                           shift[csv_um[:, 0] >= csv_um[:, 0].mean()].max()))


def blocks_of(pads, left):
    """The 6 blocks of one connector column, top to bottom: dict(xmin, xmax, rows = 4 row ys top to
    bottom). A block's rows come as 1 | 2 3 | 4: the wide lanes (740 um) are between rows 1-2 and 3-4."""
    xm = 0.5 * (pads[:, 0].min() + pads[:, 0].max())
    col = pads[pads[:, 0] < xm] if left else pads[pads[:, 0] >= xm]
    ys = np.unique(np.round(col[:, 1], 3))[::-1]
    if len(ys) != 24:
        raise RuntimeError("expected 24 pad rows per column, got %d" % len(ys))
    return [dict(xmin=col[:, 0].min(), xmax=col[:, 0].max(), rows=ys[4 * b: 4 * b + 4]) for b in range(6)]


def lanes_of(rows):
    """The two wide lanes of a block, upper first."""
    return 0.5 * (rows[0] + rows[1]), 0.5 * (rows[2] + rows[3])


# ======================================================================================
# The left half; the right half is its mirror
# ======================================================================================
def route_left(wide, xm, y_start, bd, wide_pitch):
    """{band index: path from (wide lane, y_start) down} for indices 0..383, plus numbers for the log."""
    padr = DSG.PADR * 1000.0
    blocks = blocks_of(bd["pads"], left=True)
    tight = {i: xm + (i - 383.5) * TIGHT for i in range(384)}
    paths, cur, geo = {}, {}, {}

    # ---- the top run sits between the mounting-hole keep-out and block 1's top pads
    hole = bd["mounts"][(bd["mounts"][:, 0] < xm) & (bd["mounts"][:, 1] > bd["pads"][:, 1].max())][0]
    y_top_run = hole[1] - HOLE_KEEPOUT_D / 2.0 - HOLE_CLEAR - WIRE_W / 2.0
    n_out = 128
    y_low_run = y_top_run - (n_out - 1) * TIGHT
    pad_gap = y_low_run - WIRE_W / 2.0 - (blocks[0]["rows"][0] + padr)
    if pad_gap < PAD_CLEAR_MIN:
        raise RuntimeError("top run: only %.1f um to block 1's pads" % pad_gap)
    yt = y_top_run + R_OFF_NECK                                     # the outer wires turn from here
    y_fi0 = yt + L_FAN_IN                                           # neck fan-in: y_fi0 -> yt
    geo.update(y_top_run=y_top_run, pad_gap=pad_gap, yt=yt, y_fi0=y_fi0)

    for i in range(384):                                            # neck, then fan-in to TIGHT
        paths[i] = [(wide[i], y_start), (wide[i], y_fi0)] + ease(wide[i], tight[i], y_fi0, L_FAN_IN)
        cur[i] = tight[i]

    # ---- outer-left, i = 0..127 (127 innermost): off the neck, along the top, down the wall
    c1x = tight[0] - R_OFF_NECK
    x_out = bd["notch_l"] + NOTCH_CLEAR + WIRE_W / 2.0              # i = 0 on the wall: clear of the notches
    c2x = x_out + R_TO_WALL + (n_out - 1) * TIGHT
    c2y = y_low_run - R_TO_WALL
    for i in range(n_out):
        r1 = tight[i] - c1x
        y_run = yt - r1
        r2 = y_run - c2y
        paths[i] += arc(c1x, yt, r1, 0.0, -math.pi / 2) + [(c2x, y_run)] + arc(c2x, c2y, r2, math.pi / 2, math.pi)
        cur[i] = c2x - r2
    geo.update(x_out=x_out, notch_l=bd["notch_l"])

    def into_lanes_from_wall(grp, blk):
        """grp (64, outer..inner) turns RIGHT into blk: the inner 32 into the upper lane, the outer 32
        into the lower one. Returns the lowest wire y."""
        x_end = blk["xmin"] - padr + LANE_STUB
        low = None
        for sub, y_lane in zip((grp[32:], grp[:32]), lanes_of(blk["rows"])):
            cx = cur[sub[-1]] + R_INTO_LANE
            r_lo = cx - cur[sub[0]]
            ys = y_lane + 0.5 * (R_INTO_LANE + r_lo)                # the 32 centred on the lane
            if ys > paths[sub[0]][-1][1]:
                raise RuntimeError("no room to turn into the lane at y=%.0f" % y_lane)
            for i in sub:
                r = cx - cur[i]
                paths[i] += [(cur[i], ys)] + arc(cx, ys, r, math.pi, 1.5 * math.pi) + [(x_end, ys - r)]
            low = ys - r_lo
        return low

    def into_lanes_from_centre(grp, blk):
        """grp (64, left..right) turns LEFT into blk: the left 32 into the upper lane, the right 32 into
        the lower one. Returns the lowest wire y."""
        x_end = blk["xmax"] + padr - LANE_STUB
        low = None
        for sub, y_lane in zip((grp[:32], grp[32:]), lanes_of(blk["rows"])):
            cx = cur[sub[0]] - R_INTO_LANE
            r_hi = cur[sub[-1]] - cx
            ys = y_lane + 0.5 * (R_INTO_LANE + r_hi)
            if ys > paths[sub[0]][-1][1]:
                raise RuntimeError("no room to turn into the lane at y=%.0f" % y_lane)
            for i in sub:
                r = cur[i] - cx
                paths[i] += [(cur[i], ys)] + arc(cx, ys, r, 0.0, -math.pi / 2) + [(x_end, ys - r)]
            low = ys - r_hi
        return low

    def widen(idx, target, y0):
        for i in idx:
            paths[i] += [(cur[i], y0)] + ease(cur[i], target[i], y0, L_WIDEN)
            cur[i] = target[i]

    # wall side: block 1 takes the inner 64; the other 64 widen to WIDE (the outer wire stays put) and
    # go on to block 3
    low = into_lanes_from_wall(list(range(64, 128)), blocks[WALL_BLOCKS[0]])
    rest = list(range(64))
    widen(rest, {i: x_out + i * wide_pitch for i in rest}, low - WIRE_W / 2.0 - PEEL_GAP)
    into_lanes_from_wall(rest, blocks[WALL_BLOCKS[1]])

    # ---- centre-left, i = 128..383: the first block at TIGHT, then WIDE (symmetric about the centre)
    grp = list(range(128, 192))
    low = into_lanes_from_centre(grp, blocks[CENTRE_BLOCKS[0]])
    rest = list(range(192, 384))
    widen(rest, {i: xm + (i - 383.5) * wide_pitch for i in rest}, low - WIRE_W / 2.0 - PEEL_GAP)
    for k, b in enumerate(CENTRE_BLOCKS[1:]):
        into_lanes_from_centre(rest[64 * k: 64 * k + 64], blocks[b])
    return paths, geo


# ======================================================================================
# Polyimide: Alexei's outline + the neck + the neck -> board fillets
# ======================================================================================
def polyimide_patches(bd):
    """Swap the old router's board (Simon's outline, placed from the CSV) for Alexei's: returns the
    replacements for base.build_downward_polyimide, base.edge_bulbs and base.load_board_corner_features."""
    orig_build = base.build_downward_polyimide
    top = bd["outline"].bounds[3]
    fr = base.POLYIMIDE["fillet_r_mm"] * 1000.0

    def build(*a, **kw):
        old = Polygon(orig_build(*a, **kw))                # only its neck (+ taper) is kept
        flared = kw.get("flare_l_mm") is not None
        nl = (kw["flare_l_mm"] if flared else kw["neck_l_mm"]) * 1000.0
        nr = (kw["flare_r_mm"] if flared else kw["neck_r_mm"]) * 1000.0
        neck = old.intersection(box(nl, top - 10.0, nr, 1e6))
        fil_l = box(nl - fr, top - 1.0, nl + 1.0, top + fr).difference(Point(nl - fr, top + fr).buffer(fr, 256))
        fil_r = box(nr - 1.0, top - 1.0, nr + fr, top + fr).difference(Point(nr + fr, top + fr).buffer(fr, 256))
        out = unary_union([neck, bd["outline"], fil_l, fil_r])
        if out.geom_type != "Polygon":
            raise RuntimeError("neck + board: %s" % out.geom_type)
        return [tuple(c) for c in out.exterior.coords]

    def bulbs(dsg, sig, neck_edge_x_um, neck_top_y_um, side="left", n_bulbs=None, flare_y_um=None):
        n_bulbs = n_bulbs or dsg.N_LEFT_BULBS
        y_hi = neck_top_y_um - dsg.LEFT_BULB_MARGIN_UM - 5000.0
        if flare_y_um is not None:
            y_hi = min(y_hi, flare_y_um - dsg.LEFT_BULB_MARGIN_UM)
        y_lo = top + fr + dsg.LEFT_BULB_MARGIN_UM                  # above the neck -> board fillet
        ys = np.linspace(y_hi, y_lo, n_bulbs)
        print("%s bulbs: %d x r=%.0f um at x=%.0f, y %.0f..%.0f um"
              % (side, n_bulbs, dsg.LEFT_BULB_R_UM, neck_edge_x_um, ys[0], ys[-1]))
        return [filleted_bulb(neck_edge_x_um, float(y), dsg.LEFT_BULB_R_UM, dsg.BULB_FILLET_R_UM, side,
                              CIRCLE_RES) for y in ys]

    def no_corners(*a, **kw):                              # Simon's corner circles: not on this board
        return [], 0.0, 0.0

    return build, bulbs, no_corners


# ======================================================================================
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
    wide_pitch = base.FAN_TARGET_PITCH
    lanes, xm, _ = base.build_fan_lanes(endpoints, target_pitch=wide_pitch)
    xs = sorted(p[0] for p in endpoints)
    native = min(b - a for a, b in zip(xs, xs[1:]))
    D = max(abs(p[0] - xm) for p in endpoints)
    gap = native - WIRE_W
    fan_g, fan_len = base.eased_fan_profile(native, wide_pitch, D, gap, WIRE_W, base.FAN_TILT_CAP_DEG,
                                            base.FAN_RES, base.FAN_LEN_SLACK)
    if base.FAN_LEN is not None:
        fan_len = base.FAN_LEN
    base.R = dict(base.R, two_layer=False)          # no via row: every wire stays on Metal1
    base.VIA_ROW_GAP = 0.0                          # hand-off at the fan bottom; the routes below take over
    info = base.build_transition(endpoints, lanes, fan_g, fan_len, actual=actual)
    print("fan: native pitch %.2f -> %.2f um (WIDE), len %.0f um" % (native, wide_pitch, fan_len))
    _ok, msgs = base.check_transition(DSG, info, lanes, wide_pitch, gap)
    for m in msgs:
        if "via" not in m:
            print("  " + m)

    bd = board_geometry()
    print("board: Alexei 12x64, outline %s um; %d drills; connector pads on his drills (left column moved "
          "%.1f um, right column %.1f um vs the CSV)" % ([round(v) for v in bd["outline"].bounds],
                                                           len(bd["drills"]), *bd["pad_shift"]))

    left, geo = route_left(lanes, xm, info["y_fan"], bd, wide_pitch)
    paths = dict(left)
    for i, p in left.items():
        paths[767 - i] = mirror(p, xm)
    print("lane pitch: WIDE %.1f um (gap %.1f), TIGHT %.1f um (gap %.1f)"
          % (wide_pitch, wide_pitch - WIRE_W, TIGHT, TIGHT - WIRE_W))
    print("neck fan-in to TIGHT: y %.0f -> %.0f um; outer bundles (128 each) turn off at y=%.0f, top run "
          "y=%.0f..%.0f (under the mounting-hole keep-out, %.0f um above block 1's pads), wall run from "
          "x=%.0f (notch tip %.0f)" % (geo["y_fi0"], geo["yt"], geo["yt"], geo["y_top_run"],
                                       geo["y_top_run"] - 127 * TIGHT, geo["pad_gap"], geo["x_out"],
                                       geo["notch_l"]))
    print("blocks (top = 1): wall-fed %s, centre-fed %s, in both columns"
          % ([b + 1 for b in WALL_BLOCKS], [b + 1 for b in CENTRE_BLOCKS]))

    # ---- emit
    ensure_layer(doc, WIRE_LAYER, 4)
    ensure_layer(doc, CONN_PAD_LAYER, 2)
    lines = []
    for i in range(768):
        poly = list(info["m1_polys"][i]) + paths[i][1:]
        lines.append(LineString(poly))
        msp.add_lwpolyline(stroke_centerline_to_polygon(poly, [WIRE_W] * len(poly)), close=True,
                           dxfattribs={"layer": WIRE_LAYER})
    padr = DSG.PADR * 1000.0
    for x, y in bd["pads"]:
        msp.add_lwpolyline(create_polygon_circle(x, y, padr, CIRCLE_RES), close=True,
                           dxfattribs={"layer": WIRE_LAYER})
        msp.add_lwpolyline(create_polygon_circle(x, y, (base.CONN_PAD_R or DSG.PADR) * 1000.0, CIRCLE_RES),
                           close=True, dxfattribs={"layer": CONN_PAD_LAYER})
    for lay, col in ((HELPER_OUTLINE, 3), (HELPER_DRILL, 1), (HELPER_KEEPOUT, 6)):
        ensure_layer(doc, lay, col)
    for s in bd["segs"]:
        msp.add_lwpolyline(s, dxfattribs={"layer": HELPER_OUTLINE})
    for x, y, d in bd["drills"]:
        msp.add_lwpolyline(create_polygon_circle(x, y, d / 2.0, CIRCLE_RES), close=True,
                           dxfattribs={"layer": HELPER_DRILL})
    for x, y, _d in bd["mounts"]:
        msp.add_lwpolyline(create_polygon_circle(x, y, HOLE_KEEPOUT_D / 2.0, CIRCLE_RES), close=True,
                           dxfattribs={"layer": HELPER_KEEPOUT})
    print("helper layers (not on any mask): %s, %s, %s (%.1f mm round the %d mounting holes)"
          % (HELPER_OUTLINE, HELPER_DRILL, HELPER_KEEPOUT, HOLE_KEEPOUT_D / 1000.0, len(bd["mounts"])))

    # ---- polyimide
    base.build_downward_polyimide, base.edge_bulbs, base.load_board_corner_features = polyimide_patches(bd)
    print("\n=== polyimide: Alexei's outline + neck + neck -> board fillets (r %.0f um) ==="
          % (base.POLYIMIDE["fillet_r_mm"] * 1000.0))
    merged, _holes = base.emit_polyimide(doc, msp, bd["pads"] / 1000.0, info, ic_pads_bbox=ic_pads_bbox)
    if merged.geom_type != "Polygon":
        raise RuntimeError("polyimide came out as %d pieces" % len(merged.geoms))

    # ---- checks (centrelines; edge gap = distance - WIRE_W)
    tree = STRtree(lines)
    worst = (1e9, None)
    for i, ln in enumerate(lines):
        for j in tree.query(ln, predicate="dwithin", distance=WIRE_W + gap + 5.0):
            if j > i:
                d = ln.distance(lines[j]) - WIRE_W
                if d < worst[0]:
                    worst = (d, (i, j))
    at = nearest_points(lines[worst[1][0]], lines[worst[1][1]])[0]
    pads = [Point(x, y).buffer(padr, 64) for x, y in bd["pads"]]
    ptree = STRtree(pads)
    pad_min = min(ln.distance(pads[ptree.query_nearest(ln)[0]]) for ln in lines) - WIRE_W / 2.0
    pi_min = min(ln.distance(merged.exterior) for ln in lines) - WIRE_W / 2.0
    keep = unary_union([Point(x, y).buffer(HOLE_KEEPOUT_D / 2.0, 128) for x, y, _d in bd["mounts"]])
    keep_min = min(ln.distance(keep) for ln in lines) - WIRE_W / 2.0
    pm = prep(merged.buffer(1e-6))
    outside = sum(1 for ln in lines if not pm.contains(ln))
    print("\nchecks: min wire gap %.3f um (wires %s at %.0f, %.0f), floor 2.5: %s"
          % (worst[0], worst[1], at.x, at.y, "OK" if worst[0] >= 2.5 - 5e-3 else "FAIL"))
    print("        min wire -> connector pad edge %.1f um; -> polyimide edge %.1f um (target %.0f); "
          "-> mount keep-out %.1f um; wires off the polyimide: %d"
          % (pad_min, pi_min, PI_CLEAR, keep_min, outside))

    doc.saveas(base.FINAL_DXF)
    print("wrote %s" % base.FINAL_DXF)


if __name__ == "__main__":
    main()
