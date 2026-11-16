"""Stage 02 for wafer6's 12-block: the NEW routing into the connector. Everything on Metal1, no vias.

    python3 02_route_wafer6.py --design 12block --in X_padgrid.dxf --out X.dxf   (build_designs.py runs it)

The old router (02_route.py) stays as it is for wafer4; this one reuses only its Phase 1 (band -> fan)
and its polyimide machinery (neck, bulbs, Polyimide_Negative band), and replaces the rest. Geometry is
fixed in this file on purpose -- the numbers below ARE the design.

The board is Alexei's 12x64 (assets/board_12x64_2026-06-26/, Gerber outline + Excellon drills). It is
the ground truth: the connector pads sit on its pad drills, the polyimide outline IS its outline (plus
the neck and its 2 mm neck -> board fillets), and each of its 4 corner mounting holes is cut through the
polyimide as a 0.8 mm hole (Polyimide_Negative). His outline and drills are also drawn on two HELPER_
layers (on no mask) for reference.

Lane pitch: WIDE (catalog wafer6 FAN_TARGET_PITCH, 6.5 um = 2 um wire + 4.5 um gap) wherever there is
room. Only the centre bundle narrows: to TIGHT (4.5 um, the 2.5 um gap floor) between the columns' first
blocks, MID (5.5 um, 3.5 um gap) once block 2 has peeled off, LATE (7.5 um, 5.5 um gap) once blocks
3 and 4 have.

The 768 band wires, left to right, in 64-wire groups g0..g11; connectors numbered 1-6 down the left
column and 7-12 up the right one. Connector k takes group g(k-1):

    g0       outer-left: off the neck to the left (WIDE), along the top edge just above block 1 (well
             clear of the mounting hole), down the side wall (well clear of the notches, no jog), into
             connector 1 (left block 1) from the wall
    g1-g5    centre-left: fan in to TIGHT, straight down the middle, peeling LEFT into connectors 2-6
    g6-g10   centre-right: the mirror -- connectors 7-11 (right blocks 6..2)
    g11      outer-right: the mirror -- connector 12 (right block 1) from the wall

Each 64 enters its block as two 32s, one per wide lane: rows 1-2 and rows 3-4 (2-3 is too narrow).
In a lane, the top 16 wires go to the pad row above it and the bottom 16 to the row below: the outermost
wire takes the pad nearest the lane entry, the next wire the next pad, and so on. Each turns 90 deg, widens
to PAD_TW and joins its pad with a teardrop.
"""
import importlib.util
import math
import os
import re

import ezdxf
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
from lib.teardrop import teardrop_ring, teardrop_tip_dist  # noqa: E402

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
TIGHT = 4.5               # centre lane pitch between the first blocks (2.5 um gap)
MID = 5.5                 # centre lane pitch once block 2 has peeled off (3.5 um gap)
LATE = 7.5                # centre lane pitch once blocks 3 and 4 have peeled off (5.5 um gap)
CENTRE_PAD_GAP = 150.0    # centre bundle edge -> the columns' pads, at least
PI_CLEAR = 150.0          # wire edge -> polyimide edge
NOTCH_CLEAR = 450.0       # wall-run wire edge -> the wall notches' tips (the wall run never jogs for them;
                          # ~675 um from the wall itself)
HOLE_D = 800.0            # cut through the polyimide at each corner mounting hole (the drill is 0.5 mm)
TOP_RUN_PAD_GAP = 100.0   # top-run wire edge -> block 1's pad edge
FAN_IN_PAD_GAP = 100.0    # the centre fan-in ends this far above block 1's pad edge
L_FAN_IN = 3000.0         # centre fan-in WIDE -> TIGHT
R_OFF_NECK = 1000.0       # outer bundle, neck -> top edge: radius of its innermost wire
R_TO_WALL = 300.0         # outer bundle, top edge -> side wall: radius of its innermost wire
L_WIDEN = 1500.0          # a fan-out to a wider pitch (shorter where the next block comes sooner)
R_INTO_LANE = 150.0       # a 32's turn into its lane: radius of its first-turning wire
R_TO_PAD = 30.0           # in the lane, each wire turns 90 deg up/down to its pad with this radius ...
PAD_TW = 15.0             # ... widens from WIRE_W to this ...
TEAR_FILLET = 100.0       # ... and joins its pad with a teardrop (fillet radius; tip 175 um from the centre)
PEEL_GAP = 20.0           # below a peeled group's lowest wire, before the rest change pitch
FAN_RES = 96              # samples per eased fan
SEG, SAGITTA = 10.0, 0.001  # arc sampling: max segment (um), max chord error (um)
FILLET_R = 2000.0         # neck -> board fillet (the old router's 3 mm, a little smaller)

HELPER_OUTLINE = "HELPER_board_outline"
HELPER_DRILL = "HELPER_board_drill"


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
    notch_r = v[mid & (v[:, 0] > x1 - 1500.0)][:, 0].min()
    return dict(outline=outline, segs=segs, drills=drills, pads=pads, mounts=mounts, notch_l=notch_l,
                notch_r=notch_r,
                pad_shift=(shift[csv_um[:, 0] < csv_um[:, 0].mean()].max(),
                           shift[csv_um[:, 0] >= csv_um[:, 0].mean()].max()))


def blocks_of(col):
    """The 6 blocks of one connector column (its 384 pads), top to bottom: dict(xmin, xmax, rows = 4 row
    ys top to bottom, pads). A block's rows come as 1 | 2 3 | 4: the wide lanes (740 um) are between rows
    1-2 and 3-4."""
    ys = np.unique(np.round(col[:, 1], 3))[::-1]
    if len(ys) != 24:
        raise RuntimeError("expected 24 pad rows per column, got %d" % len(ys))
    return [dict(xmin=col[:, 0].min(), xmax=col[:, 0].max(), rows=ys[4 * b: 4 * b + 4], pads=col)
            for b in range(6)]


def lanes_of(rows):
    """The two wide lanes of a block, upper first."""
    return 0.5 * (rows[0] + rows[1]), 0.5 * (rows[2] + rows[3])


# ======================================================================================
# One half, drawn as the LEFT half; the right half is routed the same way against its own pads, mirrored
# into this frame, and mirrored back (so it lands on Alexei's right column, which is not an exact mirror)
# ======================================================================================
def route_side(wide, xm, y_start, side, wide_pitch):
    """For band indices 0..383 of this half: paths {i: centreline from (wide lane, y_start) to the end of
    its turn towards its pad}, tails {i: (centreline, widths) from there into the pad centre}, tears
    [(px, py, uy)], and numbers for the log. `side`: pads (this column's 384, in the left frame), mount
    (the top mounting hole), notch (the wall notch tip x)."""
    padr = DSG.PADR * 1000.0
    blocks = blocks_of(side["pads"])
    tight = {i: xm + (i - 383.5) * TIGHT for i in range(384)}
    paths, cur, geo, tails, tears = {}, {}, {}, {}, []
    tip = teardrop_tip_dist(padr, TEAR_FILLET, PAD_TW)

    def to_pads(wires, rows2, s):
        """wires [(i, y)] of one lane, travelling in x direction s (+1 right, -1 left); rows2 = (row above,
        row below). Top 16 -> the row above, bottom 16 -> the row below, outermost first, nearest pad first."""
        wires = sorted(wires, key=lambda w: -w[1])
        for half, row_y, v in ((wires[:16], rows2[0], +1), (wires[16:][::-1], rows2[1], -1)):
            row = side["pads"][np.abs(side["pads"][:, 1] - row_y) < 1.0]
            row = row[np.argsort(s * row[:, 0])]                    # nearest the entry first
            if len(row) != len(half):
                raise RuntimeError("lane at y=%.0f: %d wires for %d pads" % (row_y, len(half), len(row)))
            for (i, y), (px, py) in zip(half, row):
                cx, cy = px - s * R_TO_PAD, y + v * R_TO_PAD
                paths[i] += [(cx, y)] + arc(cx, cy, R_TO_PAD, -v * math.pi / 2, 0.0 if s > 0 else -v * math.pi)
                tails[i] = ([(px, cy), (px, py - v * tip), (px, py)], [WIRE_W, PAD_TW, PAD_TW])
                tears.append((px, py, -v))

    # ---- outer-left, i = 0..63 (63 innermost), WIDE: off the neck, along the top just above block 1,
    # down the wall (clear of the notches) and straight into block 1
    n_out = 64
    pad_top = blocks[0]["rows"][0] + padr
    y_low_run = pad_top + TOP_RUN_PAD_GAP + WIRE_W / 2.0
    y_top_run = y_low_run + (n_out - 1) * wide_pitch
    hole = side["mount"]
    hole_gap = hole[1] - HOLE_D / 2.0 - y_top_run - WIRE_W / 2.0
    yt = y_top_run + R_OFF_NECK                                     # the outer wires turn from here
    c1x = wide[0] - R_OFF_NECK
    x_out = side["notch"] + NOTCH_CLEAR + WIRE_W / 2.0              # i = 0 on the wall: clear of the notches
    c2x = x_out + R_TO_WALL + (n_out - 1) * wide_pitch
    c2y = y_low_run - R_TO_WALL
    for i in range(n_out):
        r1 = wide[i] - c1x
        y_run = yt - r1
        r2 = y_run - c2y
        paths[i] = ([(wide[i], y_start), (wide[i], yt)] + arc(c1x, yt, r1, 0.0, -math.pi / 2)
                    + [(c2x, y_run)] + arc(c2x, c2y, r2, math.pi / 2, math.pi))
        cur[i] = c2x - r2

    # ---- centre-left, i = 64..383: fan in to TIGHT below the outer turn, ending above block 1
    y_fi1 = pad_top + FAN_IN_PAD_GAP
    y_fi0 = y_fi1 + L_FAN_IN
    for i in range(n_out, 384):
        paths[i] = [(wide[i], y_start), (wide[i], y_fi0)] + ease(wide[i], tight[i], y_fi0, L_FAN_IN)
        cur[i] = tight[i]
    geo.update(y_top_run=y_top_run, y_low_run=y_low_run, hole_gap=hole_gap, yt=yt, x_out=x_out,
               notch_l=side["notch"], y_fi0=y_fi0, y_fi1=y_fi1)

    def into_lanes_from_wall(grp, blk):
        """grp (64, outer..inner) turns RIGHT into blk: the inner 32 into the upper lane, the outer 32
        into the lower one. Returns the lowest wire y."""
        low = None
        for sub, y_lane, rows2 in zip((grp[32:], grp[:32]), lanes_of(blk["rows"]),
                                      (blk["rows"][:2], blk["rows"][2:])):
            cx = cur[sub[-1]] + R_INTO_LANE
            r_lo = cx - cur[sub[0]]
            ys = y_lane + 0.5 * (R_INTO_LANE + r_lo)                # the 32 centred on the lane
            if ys > paths[sub[0]][-1][1]:
                raise RuntimeError("no room to turn into the lane at y=%.0f" % y_lane)
            for i in sub:
                r = cx - cur[i]
                paths[i] += [(cur[i], ys)] + arc(cx, ys, r, math.pi, 1.5 * math.pi)
            to_pads([(i, ys - (cx - cur[i])) for i in sub], rows2, +1)
            low = ys - r_lo
        return low

    def into_lanes_from_centre(grp, blk):
        """grp (64, left..right) turns LEFT into blk: the left 32 into the upper lane, the right 32 into
        the lower one. Returns the lowest wire y."""
        low = None
        for sub, y_lane, rows2 in zip((grp[:32], grp[32:]), lanes_of(blk["rows"]),
                                      (blk["rows"][:2], blk["rows"][2:])):
            cx = cur[sub[0]] - R_INTO_LANE
            r_hi = cur[sub[-1]] - cx
            ys = y_lane + 0.5 * (R_INTO_LANE + r_hi)
            if ys > paths[sub[0]][-1][1]:
                raise RuntimeError("no room to turn into the lane at y=%.0f" % y_lane)
            for i in sub:
                r = cur[i] - cx
                paths[i] += [(cur[i], ys)] + arc(cx, ys, r, 0.0, -math.pi / 2)
            to_pads([(i, ys - (cur[i] - cx)) for i in sub], rows2, -1)
            low = ys - r_hi
        return low

    def widen(idx, target, y0, next_blk, pitch):
        # as long as there is room above the next block's first turn (its upper 32 at this pitch)
        y_turn = lanes_of(next_blk["rows"])[0] + 0.5 * (2 * R_INTO_LANE + 31 * pitch)
        length = min(L_WIDEN, y0 - y_turn - 50.0)
        for i in idx:
            paths[i] += [(cur[i], y0)] + ease(cur[i], target[i], y0, length)
            cur[i] = target[i]
        return length

    into_lanes_from_wall(list(range(n_out)), blocks[0])

    # centre, always centred: block 2 at TIGHT; MID for blocks 3, 4; LATE for blocks 5, 6
    edge_l = blocks[0]["xmax"] + padr                               # left column's pad edge
    room = (2.0 * xm - edge_l) - edge_l - 2.0 * CENTRE_PAD_GAP - WIRE_W   # (right edge: the mirror)
    rest = list(range(n_out, 384))
    stages, pitch_now = [], TIGHT
    for kind in (None, "mid", "late", "late"):
        if kind is not None:
            pitch = MID if kind == "mid" else LATE
            if (2 * len(rest) - 1) * pitch > room:
                raise RuntimeError("%d centre wires at %.1f um do not fit between the columns" % (2 * len(rest), pitch))
            if pitch > pitch_now + 1e-9:
                nxt = blocks[6 - len(rest) // 64]
                length = widen(rest, {i: xm + (i - 383.5) * pitch for i in rest},
                               low - WIRE_W / 2.0 - PEEL_GAP, nxt, pitch)
                stages.append((6 - len(rest) // 64 + 1, pitch, length))
                pitch_now = pitch
        for _ in range(2 if kind == "mid" else 1):
            low = into_lanes_from_centre(rest[:64], blocks[6 - len(rest) // 64])
            rest = rest[64:]
    geo["stages"] = stages
    return paths, tails, tears, geo


# ======================================================================================
# Polyimide: Alexei's outline + the neck + the neck -> board fillets
# ======================================================================================
def polyimide_patches(bd):
    """Swap the old router's board (Simon's outline, placed from the CSV) for Alexei's: returns the
    replacements for base.build_downward_polyimide, base.edge_bulbs and base.load_board_corner_features."""
    orig_build = base.build_downward_polyimide
    top = bd["outline"].bounds[3]
    fr = FILLET_R

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

    pads = bd["pads"]
    is_l = pads[:, 0] < xm
    top_mounts = bd["mounts"][bd["mounts"][:, 1] > pads[:, 1].max()]
    left_side = dict(pads=pads[is_l], mount=top_mounts[top_mounts[:, 0] < xm][0], notch=bd["notch_l"])
    m_r = top_mounts[top_mounts[:, 0] > xm][0]
    right_side = dict(pads=np.column_stack([2 * xm - pads[~is_l][:, 0], pads[~is_l][:, 1]]),
                      mount=(2 * xm - m_r[0], m_r[1]), notch=2 * xm - bd["notch_r"])
    paths, tails, geo = {}, {}, None
    tears = []
    for side, flip in ((left_side, False), (right_side, True)):
        p_, t_, tr_, g_ = route_side(lanes, xm, info["y_fan"], side, wide_pitch)
        geo = geo or g_
        for i in p_:
            j = 767 - i if flip else i
            paths[j] = mirror(p_[i], xm) if flip else p_[i]
            tails[j] = (mirror(t_[i][0], xm) if flip else t_[i][0], t_[i][1])
        tears += [((2 * xm - px) if flip else px, py, uy) for px, py, uy in tr_]
    if len(tears) != 768:
        raise RuntimeError("%d of 768 pads connected" % len(tears))
    print("lane pitch: WIDE %.1f um (gap %.1f), TIGHT %.1f um (gap %.1f)"
          % (wide_pitch, wide_pitch - WIRE_W, TIGHT, TIGHT - WIRE_W))
    print("outer bundles (64 each, WIDE): turn off the neck at y=%.0f, top run y=%.0f..%.0f (%.0f um above "
          "block 1's pads, %.0f um below the mounting-hole cut), wall run from x=%.0f (notch tip %.0f)"
          % (geo["yt"], geo["y_top_run"], geo["y_low_run"], TOP_RUN_PAD_GAP, geo["hole_gap"], geo["x_out"],
             geo["notch_l"]))
    print("centre bundles (320 each): fan in to TIGHT (%.1f um) over y %.0f -> %.0f for block 2; then "
          % (TIGHT, geo["y_fi0"], geo["y_fi1"])
          + ", ".join("%.2f um from block %d (widened over %.0f um)" % (p, b, L) for b, p, L in geo["stages"]))
    print("connector k <- band group g(k-1): 1-6 down the left column, 7-12 up the right; 1 and 12 from "
          "the wall, the rest from the centre")

    # ---- emit
    ensure_layer(doc, WIRE_LAYER, 4)
    ensure_layer(doc, CONN_PAD_LAYER, 2)
    lines = []
    own = []
    for i in range(768):
        poly = list(info["m1_polys"][i]) + paths[i][1:]
        tail, widths = tails[i]
        lines.append(LineString(poly + tail[1:]))
        own.append(tail[-1])
        msp.add_lwpolyline(stroke_centerline_to_polygon(poly, [WIRE_W] * len(poly)), close=True,
                           dxfattribs={"layer": WIRE_LAYER})
        msp.add_lwpolyline(stroke_centerline_to_polygon(tail, widths), close=True,
                           dxfattribs={"layer": WIRE_LAYER})
    padr = DSG.PADR * 1000.0
    tear_polys = []
    for px, py, uy in tears:
        ring = teardrop_ring(px, py, padr, 0.0, uy, TEAR_FILLET, PAD_TW, res=64)
        msp.add_lwpolyline([tuple(q) for q in ring], close=True, dxfattribs={"layer": WIRE_LAYER})
        tear_polys.append((Polygon(ring), (px, py)))
    print("connector pads: all %d joined -- 90 deg turn (r %.0f um), widened to %.0f um, teardrop (fillet %.0f um, "
          "tip %.0f um from the centre)" % (len(tears), R_TO_PAD, PAD_TW, TEAR_FILLET,
                                            teardrop_tip_dist(padr, TEAR_FILLET, PAD_TW)))
    for x, y in bd["pads"]:
        msp.add_lwpolyline(create_polygon_circle(x, y, (base.CONN_PAD_R or DSG.PADR) * 1000.0, CIRCLE_RES),
                           close=True, dxfattribs={"layer": CONN_PAD_LAYER})
    for lay, col in ((HELPER_OUTLINE, 3), (HELPER_DRILL, 1)):
        ensure_layer(doc, lay, col)
    for s_ in bd["segs"]:
        msp.add_lwpolyline(s_, dxfattribs={"layer": HELPER_OUTLINE})
    for x, y, d in bd["drills"]:
        msp.add_lwpolyline(create_polygon_circle(x, y, d / 2.0, CIRCLE_RES), close=True,
                           dxfattribs={"layer": HELPER_DRILL})
    print("helper layers (not on any mask): %s, %s" % (HELPER_OUTLINE, HELPER_DRILL))

    # ---- polyimide
    base.build_downward_polyimide, base.edge_bulbs, base.load_board_corner_features = polyimide_patches(bd)
    print("\n=== polyimide: Alexei's outline + neck + neck -> board fillets (r %.0f um) ==="
          % FILLET_R)
    merged, _holes = base.emit_polyimide(doc, msp, bd["pads"] / 1000.0, info, ic_pads_bbox=ic_pads_bbox)
    if merged.geom_type != "Polygon":
        raise RuntimeError("polyimide came out as %d pieces" % len(merged.geoms))
    # the mounting holes: cut through the polyimide (an island in the Polyimide hatch + the disk on
    # Polyimide_Negative, which is what etches it)
    pi_hatch = max(msp.query('HATCH[layer=="%s"]' % base.POLYIMIDE_LAYER), key=lambda h: len(h.paths))
    cuts = []
    for x, y, _d in bd["mounts"]:
        ring = create_polygon_circle(x, y, HOLE_D / 2.0, CIRCLE_RES)
        pi_hatch.paths.add_polyline_path(ring, is_closed=True, flags=ezdxf.const.BOUNDARY_PATH_DEFAULT)
        msp.add_lwpolyline(ring, close=True, dxfattribs={"layer": base.POLYIMIDE_NEG_LAYER})
        cuts.append(Polygon(ring))
    merged = merged.difference(unary_union(cuts))
    print("mounting holes: %d x %.1f mm cut through the polyimide" % (len(cuts), HOLE_D / 1000.0))

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
    ttree = STRtree([t for t, _c in tear_polys])
    pad_min = 1e9
    for i, ln in enumerate(lines):
        for j in ttree.query(ln, predicate="dwithin", distance=400.0):
            if math.hypot(tear_polys[j][1][0] - own[i][0], tear_polys[j][1][1] - own[i][1]) > 1.0:
                pad_min = min(pad_min, ln.distance(tear_polys[j][0]) - WIRE_W / 2.0)
    pi_min = min(ln.distance(merged.boundary) for ln in lines) - WIRE_W / 2.0
    pm = prep(merged.buffer(1e-6))
    outside = sum(1 for ln in lines if not pm.contains(ln))
    print("\nchecks: min wire gap %.3f um (wires %s at %.0f, %.0f), floor 2.5: %s"
          % (worst[0], worst[1], at.x, at.y, "OK" if worst[0] >= 2.5 - 5e-3 else "FAIL"))
    print("        min wire -> another wire's pad/teardrop %.1f um; -> polyimide edge (incl. the holes) %.1f um "
          "(target %.0f); "
          "wires off the polyimide: %d" % (pad_min, pi_min, PI_CLEAR, outside))

    doc.saveas(base.FINAL_DXF)
    print("wrote %s" % base.FINAL_DXF)


if __name__ == "__main__":
    main()
