"""Stage 02 for wafer6's 8x64 device: the 12-block interconnect (768 wires, its band, fan and neck as on
wafer6's 12block_56_15) routed onto Alexei's 8x64 board. Everything on Metal1, no vias.

    python3 02_route_wafer6_8x.py --design 12block --in X_padgrid.dxf --out X.dxf   (build_designs.py runs it)

It reuses 02_route_wafer6.py (the 12x64 router: Phase 1, paths, Alexei's board reader, the polyimide
patches) and replaces only the routing below the fan. Geometry is fixed in this file on purpose.

The board is Alexei's 8x64 (assets/board_8x64_2026-06-26/): 2 columns x 4 connectors, the same pad
pattern as the 12x64, 11.9 mm tall, rounded (0.8 mm) corners only on the top edge -- the cable side.
It is placed so its top connector sits exactly where the 12x64's block 1 is (board mm -> 12x64 board mm:
x - 0.02, y + 7.5), so the cable down to the board is the 12x device's.

    outer 96 per side    off the neck at WIDE, narrowed to TIGHT (2.5 um gap) just before the turn, along
                         the top edge 50 um above connector 1's pads, down the side wall, into connector 1
    centre 288 per side  fan in to CENTRE (3.0 um gap), straight down the middle; connectors 2, 3, 4 peel
                         off; after connector 2 the rest widen to LATE (3.5 um gap), centred

96 wires per connector, 64 pads: each enters as 2 x 48, one 48 per wide lane (rows 1-2, rows 3-4). In a
lane, the 16 wires nearest the outer row (row 1 / row 4) go to it, one per pad; the other 32 go to the two
centre rows ALTERNATELY, nearest pad first -- the far row's wires pass between the near row's pads (they
stay WIRE_W until PASS_CLEAR past them). So rows 1 and 4 get one wire per pad and the 32 pads of rows 2
and 3 two each, one from either lane, 180 deg apart. Every wire: 90 deg turn, widened to PAD_TW, teardrop.
768 wires on 512 pads.

The top run does not fit under Alexei's top edge (his top pad row is 0.475 mm below it): the polyimide's
top edge is raised just enough (PI_CLEAR above the run), rounded corners kept.
"""
import importlib.util
import math
import os

import numpy as np
from shapely.affinity import translate
from shapely.geometry import LineString, Polygon, box
from shapely.ops import nearest_points, polygonize, unary_union
from shapely.prepared import prep
from shapely.strtree import STRtree

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("route_wafer6", os.path.join(HERE, "02_route_wafer6.py"))
w6 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(w6)            # loads 02_route.py as route_base (parses --design/--in/--out)
base = w6.base

from lib.constants import CIRCLE_RES, CONN_PAD_LAYER, WIRE_LAYER  # noqa: E402
from lib.geometry import create_polygon_circle, ensure_layer, stroke_centerline_to_polygon  # noqa: E402
from lib.teardrop import teardrop_ring, teardrop_tip_dist  # noqa: E402

DSG = base.DSG
arc, ease = w6.arc, w6.ease

# ======================================================================================
# The design (um)
# ======================================================================================
BOARD_DIR = os.path.join(HERE, "assets", "board_8x64_2026-06-26")
TO_12X_MM = (-0.02, 7.5)  # 8x64 board mm -> 12x64 board mm (top connector on the 12x64's block 1)

WIRE_W = w6.WIRE_W        # 2 um, the whole way
TIGHT = w6.TIGHT          # 4.5 um: the top run (2.5 um gap)
CENTRE = 5.0              # centre lane pitch between the columns (3.0 um gap): 144 um to each column's
                          # pads (the cable's centre is 1.5 um left of the board's)
CENTRE_PAD_GAP = 140.0    # centre bundle edge -> the columns' pads, at least (12x: 150)
PI_CLEAR = w6.PI_CLEAR    # 150: wire edge -> polyimide edge
N_OUT = 96                # wall bundle per side
WALL_IN = 675.0           # side wall -> the wall run's outermost wire centre (as on the 12x device)
TOP_RUN_PAD_GAP = 50.0    # top-run wire edge -> connector 1's pad edge (12x: 100)
FAN_IN_PAD_GAP = w6.FAN_IN_PAD_GAP    # 100: the centre fan-in ends this far above connector 1's pad edge
L_FAN_IN = w6.L_FAN_IN    # 3000: centre fan-in WIDE -> CENTRE
L_NARROW = 1500.0         # outer 96: WIDE -> TIGHT, ending where they turn off the neck
R_OFF_NECK = w6.R_OFF_NECK  # 1000: outer bundle, neck -> top edge: radius of its outermost wire
R_TO_WALL = 250.0         # outer bundle, top edge -> side wall: radius of its innermost wire (12x: 300; the
                          # top run 50 um closer to the pads leaves this much room above the upper lane's turn)
R_INTO_LANE = w6.R_INTO_LANE  # 150: a 48's turn into its lane: radius of its first-turning wire
LATE = w6.MID             # 5.5: centre lane pitch once connector 2 has peeled off (3.5 um gap)
L_WIDEN = w6.L_WIDEN      # 1500: that widening (shorter if connector 3 comes sooner)
PEEL_GAP = w6.PEEL_GAP    # 20: below a peeled group's lowest wire, before the rest widen
R_TO_PAD = w6.R_TO_PAD    # 30: in the lane, each wire turns 90 deg towards its pad with this radius ...
PAD_TW = w6.PAD_TW        # ... widens to 15 um ...
TEAR_FILLET = w6.TEAR_FILLET  # ... and joins its pad with a teardrop (100 um fillet, tip 175 um from the centre)
PASS_CLEAR = 20.0         # a wire to the far centre row stays WIRE_W until this far past the adjacent row's pads

HELPER_OUTLINE, HELPER_DRILL = w6.HELPER_OUTLINE, w6.HELPER_DRILL


# ======================================================================================
# Alexei's 8x64 board
# ======================================================================================
def to_um(bx, by):
    return w6.to_um(bx + TO_12X_MM[0], by + TO_12X_MM[1])


def board_geometry():
    """Alexei's outline (polygon + segments), drills, the 512 connector pads (drills in the 32-pad rows)
    and his left / right wall x, in our um."""
    segs_mm, drills_mm = w6.read_board(BOARD_DIR, "8x64")
    segs = [[to_um(*p) for p in s] for s in segs_mm]
    ends = []                                   # snap segment ends onto one another (1 um)
    for s in segs:
        for k in (0, -1):
            hit = next((e for e in ends if math.hypot(e[0] - s[k][0], e[1] - s[k][1]) < 1.0), None)
            if hit is None:
                ends.append(s[k])
            else:
                s[k] = hit
    outline = max(polygonize(unary_union([LineString(s) for s in segs])), key=lambda g: g.area)
    drills = np.array([to_um(x, y) + (d * 1000.0,) for x, y, d in drills_mm])
    ys = np.round(drills[:, 1], 1)
    rows = [y for y in np.unique(ys) if (ys == y).sum() == 32]
    pads = drills[np.isin(ys, rows)][:, :2]
    if len(pads) != 512 or len(rows) != 16:
        raise RuntimeError("8x64 board: %d pads in %d rows (expected 512 in 16)" % (len(pads), len(rows)))
    return dict(outline=outline, segs=segs, drills=drills, pads=pads)


def raised_outline(outline, ext):
    """Alexei's outline with its top edge `ext` higher (walls extended straight up, top corners kept)."""
    x0, _y0, x1, top = outline.bounds
    cut = top - 1500.0                          # below the 0.8 mm corners, far above the wall notches
    head = outline.intersection(box(x0 - 1, cut, x1 + 1, top + 1))
    out = unary_union([outline, box(x0, cut, x1, cut + ext + 1.0), translate(head, 0.0, ext)])
    if out.geom_type != "Polygon":
        raise RuntimeError("raised outline: %s" % out.geom_type)
    return Polygon(out.exterior)


def blocks_of(col):
    """The 4 connectors of one column, top to bottom: rows (4 ys, top first), xmin, xmax."""
    ys = np.unique(np.round(col[:, 1], 3))[::-1]
    return [dict(xmin=col[:, 0].min(), xmax=col[:, 0].max(), rows=ys[4 * b: 4 * b + 4]) for b in range(4)]


# ======================================================================================
# One half, drawn as the LEFT half (the right half: its own pads mirrored into this frame, mirrored back)
# ======================================================================================
def route_side(wide, xm, y_start, side, wide_pitch):
    """Band indices 0..383 of this half -> paths {i: centreline from (wide lane, y_start) to the end of its
    turn towards its pad}, tails {i: (centreline, widths) into the pad centre}, tears [(px, py, uy)], geo.
    `side`: pads (this column's 256, left frame), wall (its side wall x)."""
    padr = DSG.PADR * 1000.0
    blocks = blocks_of(side["pads"])
    b1 = blocks[0]
    pad_top = b1["rows"][0] + padr
    tip = teardrop_tip_dist(padr, TEAR_FILLET, PAD_TW)
    paths, cur, geo, tails, tears = {}, {}, {}, {}, []

    def row_pads(y):
        r = side["pads"][np.abs(side["pads"][:, 1] - y) < 1.0]
        if len(r) != 16:
            raise RuntimeError("pad row y=%.0f: %d pads" % (y, len(r)))
        return r

    def to_pads(wires, blk, upper, s):
        """wires [(i, y)] of one wide lane of blk, travelling in x direction s (+1 right, -1 left).
        upper lane (rows 1-2): the top 16 -> row 1; the bottom 32 -> rows 2 AND 3 alternately (row 3's
        wires pass between row 2's pads). lower lane (rows 3-4): the mirror. Each group: the wire nearest
        its rows takes the pad nearest the entry."""
        rows = blk["rows"]
        out_y, adj_y, far_y, v_out = (rows[0], rows[1], rows[2], +1) if upper else (rows[3], rows[2], rows[1], -1)
        wires = sorted(wires, key=lambda w: -w[1] * v_out)            # nearest the outer row first
        groups = ((wires[:16], [(p, None) for p in row_pads(out_y)], v_out),
                  (wires[16:][::-1], [(p, None) for p in row_pads(adj_y)] + [(p, adj_y) for p in row_pads(far_y)],
                   -v_out))
        for half, targets, v in groups:
            targets.sort(key=lambda t: s * t[0][0])                       # nearest the entry first
            if len(targets) != len(half):
                raise RuntimeError("lane: %d wires for %d pads" % (len(half), len(targets)))
            for (i, y), ((px, py), passing) in zip(half, targets):
                cx, cy = px - s * R_TO_PAD, y + v * R_TO_PAD
                paths[i] += [(cx, y)] + arc(cx, cy, R_TO_PAD, -v * math.pi / 2, 0.0 if s > 0 else -v * math.pi)
                if passing is None:
                    tails[i] = ([(px, cy), (px, py - v * tip), (px, py)], [WIRE_W, PAD_TW, PAD_TW])
                else:                                   # thin between the adjacent row's pads, then widen
                    tails[i] = ([(px, cy), (px, passing + v * (padr + PASS_CLEAR)), (px, py - v * tip), (px, py)],
                                [WIRE_W, WIRE_W, PAD_TW, PAD_TW])
                tears.append((px, py, -v))

    def lane_turn(sub, y_lane, s):
        """sub (48, left..right) turns into the lane at y_lane towards s, centred on it."""
        first = sub[-1] if s > 0 else sub[0]
        last = sub[0] if s > 0 else sub[-1]
        cx = cur[first] + s * R_INTO_LANE
        r_max = abs(cx - cur[last])
        ys = y_lane + 0.5 * (R_INTO_LANE + r_max)
        if ys > paths[last][-1][1]:
            raise RuntimeError("no room to turn into the lane at y=%.0f" % y_lane)
        out = []
        for i in sub:
            r = abs(cx - cur[i])
            paths[i] += [(cur[i], ys)] + (arc(cx, ys, r, math.pi, 1.5 * math.pi) if s > 0
                                          else arc(cx, ys, r, 0.0, -math.pi / 2))
            out.append((i, ys - r))
        return out, ys - r_max

    def into_block(grp, blk, s):
        """grp (96, left..right) into blk's two wide lanes: from the wall (s=+1) the inner 48 -> upper lane;
        from the centre (s=-1) the left 48 -> upper lane. Returns the lowest wire y."""
        first, second = (grp[48:], grp[:48]) if s > 0 else (grp[:48], grp[48:])
        low = None
        for sub, upper in ((first, True), (second, False)):
            rows = blk["rows"]
            y_lane = 0.5 * (rows[0] + rows[1]) if upper else 0.5 * (rows[2] + rows[3])
            ws, low = lane_turn(sub, y_lane, s)
            to_pads(ws, blk, upper, s)
        return low

    # ---- outer 96 (0 = outermost): narrow to TIGHT on the innermost one, turn off the neck, top run,
    # corner, down the wall, into connector 1
    y_low_run = pad_top + TOP_RUN_PAD_GAP + WIRE_W / 2.0
    y_top_run = y_low_run + (N_OUT - 1) * TIGHT
    yt = y_top_run + R_OFF_NECK
    narrow = {i: wide[N_OUT - 1] - (N_OUT - 1 - i) * TIGHT for i in range(N_OUT)}
    if yt + L_NARROW > y_start:
        raise RuntimeError("the outer narrowing starts above the fan end (%.0f > %.0f)" % (yt + L_NARROW, y_start))
    c1x = narrow[0] - R_OFF_NECK
    x_out = side["wall"] + WALL_IN
    c2x = x_out + R_TO_WALL + (N_OUT - 1) * TIGHT
    c2y = y_low_run - R_TO_WALL
    for i in range(N_OUT):
        r1 = narrow[i] - c1x
        y_run = yt - r1
        r2 = y_run - c2y
        paths[i] = ([(wide[i], y_start), (wide[i], yt + L_NARROW)] + ease(wide[i], narrow[i], yt + L_NARROW, L_NARROW)
                    + arc(c1x, yt, r1, 0.0, -math.pi / 2) + [(c2x, y_run)] + arc(c2x, c2y, r2, math.pi / 2, math.pi))
        cur[i] = c2x - r2
    into_block(list(range(N_OUT)), b1, +1)

    # ---- centre 288: fan in to CENTRE above connector 1; connectors 2, 3, 4 peel off to the left (96
    # each); after connector 2 the rest widen to LATE, centred
    y_fi1 = pad_top + FAN_IN_PAD_GAP
    y_fi0 = y_fi1 + L_FAN_IN
    if y_fi0 > y_start:
        raise RuntimeError("the centre fan-in starts above the fan end")
    edge = b1["xmax"] + padr
    for i in range(N_OUT, 384):
        x = xm + (i - 383.5) * CENTRE
        paths[i] = [(wide[i], y_start), (wide[i], y_fi0)] + ease(wide[i], x, y_fi0, L_FAN_IN)
        cur[i] = x
    gap_l = xm + (N_OUT - 383.5) * CENTRE - WIRE_W / 2.0 - edge
    if gap_l < CENTRE_PAD_GAP - 1e-6:
        raise RuntimeError("centre bundle %.1f um from the left column's pads (< %.0f)" % (gap_l, CENTRE_PAD_GAP))
    rest = list(range(N_OUT, 384))
    widened = None
    for b in (1, 2, 3):
        if b == 2:                                  # widen the remaining 192 to LATE, centred
            nxt = blocks[b]
            y0 = low - WIRE_W / 2.0 - PEEL_GAP
            y_turn = 0.5 * (nxt["rows"][0] + nxt["rows"][1]) + 0.5 * (2 * R_INTO_LANE + 47 * LATE)
            length = min(L_WIDEN, y0 - y_turn - 50.0)
            for i in rest:
                x = xm + (i - 383.5) * LATE
                paths[i] += [(cur[i], y0)] + ease(cur[i], x, y0, length)
                cur[i] = x
            widened = length
        low = into_block(rest[:96], blocks[b], -1)
        rest = rest[96:]
    geo.update(y_top_run=y_top_run, y_low_run=y_low_run, yt=yt, x_out=x_out, y_fi0=y_fi0, y_fi1=y_fi1,
               centre_gap=gap_l, widened=widened)
    return paths, tails, tears, geo


# ======================================================================================
def main():
    print("=== wafer6 8x64 router: Phase 1 (band -> fan), Metal1 only ===")
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
    base.R = dict(base.R, two_layer=False)
    base.VIA_ROW_GAP = 0.0
    info = base.build_transition(endpoints, lanes, fan_g, fan_len, actual=actual)
    print("fan: native pitch %.2f -> %.2f um (WIDE), len %.0f um" % (native, wide_pitch, fan_len))
    _ok, msgs = base.check_transition(DSG, info, lanes, wide_pitch, gap)
    for m in msgs:
        if "via" not in m:
            print("  " + m)

    bd = board_geometry()
    pads = bd["pads"]
    x0, _y0, x1, top = bd["outline"].bounds
    print("board: Alexei 8x64, outline %s um; %d drills, %d connector pads (on his pad drills)"
          % ([round(v) for v in bd["outline"].bounds], len(bd["drills"]), len(pads)))

    is_l = pads[:, 0] < xm
    left_side = dict(pads=pads[is_l], wall=x0)
    right_side = dict(pads=np.column_stack([2 * xm - pads[~is_l][:, 0], pads[~is_l][:, 1]]), wall=2 * xm - x1)
    paths, tails, tears, geo = {}, {}, [], None
    for side, flip in ((left_side, False), (right_side, True)):
        p_, t_, tr_, g_ = route_side(lanes, xm, info["y_fan"], side, wide_pitch)
        geo = geo or g_
        if flip:
            geo["centre_gap_r"] = g_["centre_gap"]
        for i in p_:
            j = 767 - i if flip else i
            paths[j] = w6.mirror(p_[i], xm) if flip else p_[i]
            tails[j] = (w6.mirror(t_[i][0], xm) if flip else t_[i][0], t_[i][1])
        tears += [((2 * xm - px) if flip else px, py, uy) for px, py, uy in tr_]
    if len(tears) != 768:
        raise RuntimeError("%d of 768 wires reach a pad" % len(tears))
    n_two = len({(round(px), round(py)) for px, py, _ in tears})
    if n_two != 512:
        raise RuntimeError("%d of 512 pads joined" % n_two)

    ext = geo["y_top_run"] + WIRE_W / 2.0 + PI_CLEAR - top
    print("outer bundles (96 each): WIDE %.1f -> TIGHT %.1f um over %.0f um, turn off the neck at y=%.0f, top "
          "run y=%.0f..%.0f (%.0f um above connector 1's pads), wall run from x=%.0f (%.0f um in from the wall), "
          "into connector 1 as 2 x 48"
          % (wide_pitch, TIGHT, L_NARROW, geo["yt"], geo["y_top_run"], geo["y_low_run"], TOP_RUN_PAD_GAP,
             geo["x_out"], WALL_IN))
    print("centre bundle (576): fan in to %.1f um over y %.0f -> %.0f (%.1f / %.1f um to the left / right "
          "column's pads); connectors 2-4 peel off as 2 x 48; after connector 2 the rest widen to %.1f um "
          "(over %.0f um)" % (CENTRE, geo["y_fi0"], geo["y_fi1"], geo["centre_gap"], geo["centre_gap_r"],
                              LATE, geo["widened"]))
    print("pads: each lane's 16 nearest the outer row -> the outer row; its other 32 -> the two centre rows "
          "alternately (the far row's wires pass between the near row's pads): rows 1 and 4 one wire per pad, "
          "rows 2 and 3 two (one from each lane, 180 deg apart); 768 wires on 512 pads")
    print("polyimide top edge raised %.0f um above Alexei's (the top run + %.0f um)" % (ext, PI_CLEAR))
    bd["outline"] = raised_outline(bd["outline"], ext)

    # ---- emit
    ensure_layer(doc, WIRE_LAYER, 4)
    ensure_layer(doc, CONN_PAD_LAYER, 2)
    lines, own = [], []
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
    for x, y in pads:
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
    base.build_downward_polyimide, base.edge_bulbs, base.load_board_corner_features = w6.polyimide_patches(bd)
    print("\n=== polyimide: Alexei's outline (top raised) + neck + neck -> board fillets (r %.0f um) ==="
          % w6.FILLET_R)
    merged, _holes = base.emit_polyimide(doc, msp, pads / 1000.0, info, ic_pads_bbox=ic_pads_bbox)
    if merged.geom_type != "Polygon":
        raise RuntimeError("polyimide came out as %d pieces" % len(merged.geoms))

    # ---- checks (centrelines; edge gap = distance - WIRE_W)
    tree = STRtree(lines)
    worst = (1e9, None)
    for i, ln in enumerate(lines):
        for j in tree.query(ln, predicate="dwithin", distance=WIRE_W + gap + 5.0):
            if j > i and math.hypot(own[i][0] - own[j][0], own[i][1] - own[j][1]) > 1.0:   # not one pad's pair
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
    print("        min wire -> another pad / its teardrop %.1f um; -> polyimide edge %.1f um (target %.0f); wires off the polyimide: %d"
          % (pad_min, pi_min, PI_CLEAR, outside))

    doc.saveas(base.FINAL_DXF)
    print("wrote %s" % base.FINAL_DXF)


if __name__ == "__main__":
    main()
