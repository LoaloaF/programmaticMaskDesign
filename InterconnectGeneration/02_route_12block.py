"""Route the 12-block interconnect into the 12-block Molex 227044 connector, on two metal layers.

The stage-01 part (01_generate_12block.py -> new_interconnect_circular_12Block_56_15.dxf)
delivers all 768 wires on Metal1 at ~4.5 um pitch. To get two-layer routing room, every
second wire is lifted onto Metal2 through a via (layer Via) right after the interconnect, so
each layer carries 384 wires at ~2x pitch into the connector. Compare 02_route_8block.py,
which stays on one layer.

PHASE 1 (band -> via row), in um:
  1. open the stage-01 DXF and ROTATE it 180 deg so its wire band faces DOWN.
  2. take each Metal1 wire's lowest vertex as its band endpoint (768).
  3. FAN the bundle out from its native ~4.5 um pitch to FAN_TARGET_PITCH (7 um).
  4. VIA TRANSITION: even wires stay on Metal1; odd wires get a Metal1 6 um landing pad ->
     5 um via (layer Via) -> Metal2 6 um landing pad and continue on Metal2.

PHASE 2 (via row -> connector), in mm:
  5. a neck, then ONE eased fan-in to the connector pitch; lanes alternate Metal1/Metal2,
     so the fan holds the SAME-layer gap. Traces widen to CONNECTOR_TW below the fan.
  6. staged spine/corridor routing into the 2 x 6 connector blocks (8 from the CSV, 4
     synthesised), 45 deg angled entry on the flanked rows, teardrop pads (HANDOVER §5).

PHASE 3: the polyimide substrate -- the stage-01 jigsaw top, a neck that flares over the
fan, Simon's board.

Writes one DXF. Phase 1 works in um (the interconnect's units); Phase 2 in mm (the connector
CSV's units), converted to um on write. KLayout reads 1 unit = 1 um.
"""

import math
import numpy as np
import ezdxf
from shapely.ops import unary_union
from shapely.geometry import Polygon, LineString

from lib import active


def _cli():
    import argparse
    p = argparse.ArgumentParser(description="Route an interconnect DXF into the Molex connector.")
    p.add_argument("--in", dest="in_dxf", default=None,
                   help="interconnect DXF from stage 01; bare name -> designs/ "
                        "(default: config_12block.DEFAULT_IN)")
    p.add_argument("--out", dest="out_dxf", default=None,
                   help="routed output DXF; bare name -> designs/ "
                        "(default: config_12block.DEFAULT_OUT)")
    return p.parse_args() if __name__ == "__main__" else p.parse_args([])


# Select the design BEFORE importing anything else from lib/ -- see lib/active.py.
_ARGS = _cli()
active.select("12block", _ARGS.in_dxf, _ARGS.out_dxf)
from lib.active import *  # noqa: E402,F401,F403  -- this design's knobs
from lib.fanmath import band_runs, eased_fan_profile, min_adjacent_fan_gap
from lib.geometry import (create_polygon_circle, ensure_layer, entity_xy_points,
    stroke_centerline_to_polygon, to_seglist)
from lib.teardrop import (angled_entry_len, angled_pad_entry, clamp_teardrop_fillets,
    pad_approach_for, pad_cols_by_index, pad_rows_by_index, repair_angled_crossings,
    teardrop_len_for, teardrop_ring, teardrop_tip_width, widen_pad_approach)
from lib.phase1 import check_transition, emit_transition, load_and_rotate_interconnect
from lib.connector import (_load_extended_sig, conn_rank_mixed, conn_rank_right_direct,
    connect_handoff_to_routes, load_connector, translate_chan, translated_signal_pads)
from lib.checks import _check_fast
from lib.board import (SMOOTHERSTEP_PEAK_SLOPE, build_downward_polyimide, edge_bulbs,
    load_board_corner_features, smootherstep)


# ======================================================================================
# What is specific to the 12-block -- everything else is shared, in lib/
# ======================================================================================
#   * TWO metal layers: Phase 1 fans the band out to FAN_TARGET_PITCH and lifts every odd
#     wire onto Metal2 through a via on layer Via (build_fan_lanes, build_transition).
#   * The connector has 12 blocks but the CSV holds 8 real ones; the other 4 are synthesised
#     (N_NEW_BLOCKS_PER_COLUMN, see lib/connector.py).
#   * In-block routing: route_single_left/right (+ rows34_keys for the direct rows 3&4).
#   * The spine starts from the fanned via row (add_spine*, build_columns_staged).
#   * Connector traces alternate Metal1/Metal2 and widen BELOW the fan
#     (taper_widths_along_path, emit_connector_routes).
#   * The polyimide neck flares out over the fan (fanout_*, emit_polyimide).
# Knobs: config_12block.py.


def extract_band_endpoints(msp, expected=TOTAL_WIRES, tol=0.2):
    """After rotation the band is the MIN-y edge; take each Metal1 wire's min-y vertex."""
    endpoints, band_ys = [], []
    for e in msp:
        if e.dxftype() not in ("LWPOLYLINE", "POLYLINE"):
            continue
        if e.dxf.layer != WIRE_LAYER:
            continue
        pts = entity_xy_points(e)
        if len(pts) < 2:
            continue
        ymin = min(p[1] for p in pts)
        xs_at = [x for x, y in pts if abs(y - ymin) <= tol]
        ex = float(np.mean(xs_at)) if xs_at else float(np.mean([p[0] for p in pts]))
        endpoints.append((ex, ymin)); band_ys.append(ymin)

    endpoints.sort(key=lambda p: p[0])
    if band_ys:
        print(f"band endpoints: n={len(endpoints)}, band y in "
              f"[{min(band_ys):.2f}, {max(band_ys):.2f}] um, "
              f"x in [{endpoints[0][0]:.2f}, {endpoints[-1][0]:.2f}] um")
    if len(endpoints) != expected:
        print(f"  WARNING: expected {expected} band endpoints, got {len(endpoints)}")
    return endpoints


def build_fan_lanes(endpoints, target_pitch=FAN_TARGET_PITCH):
    n = len(endpoints)
    xs = np.array([p[0] for p in endpoints])
    x_center = 0.5 * (xs.min() + xs.max())
    mid = (n - 1) / 2.0
    lanes = np.array([x_center + (i - mid) * target_pitch for i in range(n)])
    max_shift = float(np.max(np.abs(lanes - xs)))
    return lanes, x_center, max_shift


def build_transition(endpoints, lanes, fan_g, fan_len):
    """Fan + via-transition geometry. Every wire hands off to Phase 2 at (lane, y_via)
    on its assigned layer (even -> Metal1 pass-through, odd -> Metal2 after the via). The fan is the
    EASED curve x(s) = ex + (lane - ex) * g(s): vertical at the band entry (tight end), curving out
    to its lane by y_fan. fan_g are the res+1 shift fractions from eased_fan_profile()."""
    band_y = min(p[1] for p in endpoints)
    y_neck = band_y - NECK_LEN
    y_fan = y_neck - fan_len
    y_via = y_fan - VIA_ROW_GAP
    res = len(fan_g) - 1

    m1_polys, m2_polys = [], []
    m1_pads, m2_pads, vias = [], [], []
    layer_of, handoff = [], []
    fan_polys = []

    for i, ((ex, _ey), lane) in enumerate(zip(endpoints, lanes)):
        fan_pts = [(ex + (lane - ex) * g, y_neck - (k / res) * fan_len)
                   for k, g in enumerate(fan_g)]      # top (ex, y_neck) -> bottom (lane, y_fan)
        head = [(ex, band_y)] + fan_pts + [(lane, y_via)]
        fan_polys.append(fan_pts)
        handoff.append((float(lane), float(y_via)))
        if i % 2 == 0:
            m1_polys.append(head)                       # even: straight through on Metal1
            layer_of.append(WIRE_LAYER)
        else:
            m1_polys.append(head)                       # odd: Metal1 up to the pad
            m1_pads.append((lane, y_via)); vias.append((lane, y_via)); m2_pads.append((lane, y_via))
            layer_of.append(LIFT_LAYER)                 # continues on Metal2 (Phase 2)

    return {
        "m1_polys": m1_polys, "m2_polys": m2_polys,
        "m1_pads": m1_pads, "m2_pads": m2_pads, "vias": vias,
        "layer_of": layer_of, "handoff": handoff, "fan_polys": fan_polys,
        "band_y": band_y, "y_neck": y_neck, "y_fan": y_fan, "y_via": y_via,
    }


def route_single_left(chan):
    rows = sorted({round(chan[c][1], 3) for c in chan})
    if len(rows) < 4:
        return {}
    y4, y3, y2, y1 = rows[0], rows[1], rows[2], rows[3]
    xlo = min(p[0] for p in chan.values())
    x_in_entry = xlo - 0.2
    above1_base = y1 + 0.12
    corridor_base = y3 + 0.12
    routes = {}
    top = sorted((c for c in chan if round(chan[c][1], 3) in (y1, y2)), key=lambda c: chan[c][0])
    for k, c in enumerate(top):
        px, py = chan[c]; lane = above1_base + k * PITCH
        routes[c] = [(x_in_entry, lane), (px, lane), (px, py)]
    bottom = sorted((c for c in chan if round(chan[c][1], 3) in (y3, y4)), key=lambda c: chan[c][0])
    for k, c in enumerate(bottom):
        px, py = chan[c]; lane = corridor_base + k * PITCH
        routes[c] = [(x_in_entry, lane), (px, lane), (px, py)]
    return routes


def rows34_keys(chan):
    """CKTs on a block's two LOWEST pad rows (physical rows 3&4). On the right column these are the
    ones RIGHT_DIRECT_ROWS34 feeds straight from the under-block ladder."""
    rows = sorted({round(chan[c][1], 3) for c in chan})
    if len(rows) < 4:
        return set()
    y4, y3 = rows[0], rows[1]
    return {c for c in chan if round(chan[c][1], 3) in (y3, y4)}


def route_single_right(chan):
    rows = sorted({round(chan[c][1], 3) for c in chan})
    if len(rows) < 4:
        return {}
    y4, y3, y2, y1 = rows[0], rows[1], rows[2], rows[3]
    xlow = min(p[0] for p in chan.values())
    x_in_entry = xlow - 0.2
    corridor_top = y2 - 0.13
    # With RIGHT_DIRECT_ROWS34 the rows-3&4 corridor IS the top of the under-block ladder (the rows
    # 1&2 lanes continue down from it), so it takes the ladder's clearance -- that is what puts the
    # right column's stack in exactly the same y band as the left's. Fed from the right edge
    # instead, it is a standalone corridor and keeps the tighter corridor clearance.
    below4_top = y4 - (UNDER_BLOCK_CLEAR_MM if RIGHT_DIRECT_ROWS34 else CORRIDOR_CLEAR_MM)
    routes = {}
    top = sorted((c for c in chan if round(chan[c][1], 3) in (y1, y2)), key=lambda c: chan[c][0])
    n_top = len(top)
    for k, c in enumerate(top):
        px, py = chan[c]; lane = corridor_top - (n_top - 1 - k) * PITCH
        routes[c] = [(x_in_entry, lane), (px, lane), (px, py)]
    bottom = sorted(rows34_keys(chan), key=lambda c: chan[c][0])
    n_bot = len(bottom)
    for k, c in enumerate(bottom):
        # RIGHT_DIRECT_ROWS34 -> the LEFTMOST pad (nearest the central bundle) gets the HIGHEST lane:
        # running right, a wire turning up early sits ABOVE the wires still travelling past it, so
        # its riser never cuts their lanes. Fed from the right edge, the nesting runs the other way.
        px, py = chan[c]
        lane = below4_top - (k if RIGHT_DIRECT_ROWS34 else n_bot - 1 - k) * PITCH
        routes[c] = [(x_in_entry, lane), (px, lane), (px, py)]
    return routes


def add_spine(routes, spine_x, band_y, y_below_base, x_up_base, rank, x_center,
              rank_offset=0, global_base=0):
    """Left-column long-haul: NECK (vertical @ BAND_PITCH) -> FAN (BAND_PITCH ->
    CONNECTOR_PITCH, constant width) -> WIDEN -> body (down spine, below block, up left,
    into corridor). Fan symmetric about the global bundle center x_center."""
    neck_bottom_y = band_y - NECK_LEN_MM
    fan_bottom_y = neck_bottom_y - FAN_LEN_MM
    widen_bottom_y = fan_bottom_y - WIDEN_LEN_MM
    mid = (TOTAL_WIRES - 1) / 2.0
    out = {}
    for c, poly in routes.items():
        lane = poly[0][1]; px = poly[1][0]; py = poly[2][1]; tail = poly[3:]
        k = rank[c]; gidx = k + rank_offset
        xs_top = spine_x + gidx * BAND_PITCH_MM
        xs_body = x_center + (global_base + gidx - mid) * CONNECTOR_PITCH
        yb = y_below_base - k * PITCH
        xu = x_up_base - k * PITCH
        fan_pts = [(xs_body + (xs_top - xs_body) * FAN2_G[j],
                    fan_bottom_y + (j / FAN_RES) * FAN_LEN_MM)
                   for j in range(FAN_RES, -1, -1)]     # eased fan: neck top -> connector bottom
        out[c] = fan_pts + [(xs_body, widen_bottom_y), (xs_body, yb), (xu, yb), (xu, lane),
                            (px, lane), (px, py)] + tail
    return out


def add_spine_right(routes, spine_x, band_y, y_below_base, x_up_base, rank, x_center,
                    rank_offset=0, global_base=0, direct_keys=()):
    """Right-column long-haul. Wires in direct_keys (rows 3&4 under RIGHT_DIRECT_ROWS34) drop
    STRAIGHT into their corridor lane, run right and turn up into the pad -- their corridor is the
    top of the under-block ladder, so the trip out to x_up_base and back is pure detour. Every other
    wire keeps the around-the-right-edge route (down to yb, right to xu, up, back left), which rows
    1&2 genuinely need: their corridor is buried in the mid-gap between pad rows."""
    neck_bottom_y = band_y - NECK_LEN_MM
    fan_bottom_y = neck_bottom_y - FAN_LEN_MM
    widen_bottom_y = fan_bottom_y - WIDEN_LEN_MM
    mid = (TOTAL_WIRES - 1) / 2.0
    out = {}
    for c, poly in routes.items():
        lane = poly[0][1]; px = poly[1][0]; py = poly[2][1]; tail = poly[3:]
        k = rank[c]; gidx = k + rank_offset
        xs_top = spine_x + gidx * BAND_PITCH_MM
        xs_body = x_center + (global_base + gidx - mid) * CONNECTOR_PITCH
        fan_pts = [(xs_body + (xs_top - xs_body) * FAN2_G[j],
                    fan_bottom_y + (j / FAN_RES) * FAN_LEN_MM)
                   for j in range(FAN_RES, -1, -1)]     # eased fan: neck top -> connector bottom
        if c in direct_keys:
            out[c] = fan_pts + [(xs_body, widen_bottom_y), (xs_body, lane),
                                (px, lane), (px, py)] + tail
        else:
            yb = y_below_base + k * PITCH
            xu = x_up_base - k * PITCH
            out[c] = fan_pts + [(xs_body, widen_bottom_y), (xs_body, yb), (xu, yb), (xu, lane),
                                (px, lane), (px, py)] + tail
    return out


def build_columns_staged(spine_x_left, y_top_left, spine_x_right, y_top_right,
                         connector_dx=0.0, connector_dy=0.0):
    spine_routes_all = {}
    ranks_all = {}
    all_pads = []
    x_center = spine_x_left + (TOTAL_WIRES - 1) / 2.0 * BAND_PITCH_MM

    for b in range(N_BLOCKS_PER_COLUMN):
        chan = translate_chan(load_connector("left", b), connector_dx, connector_dy)
        all_pads += list(chan.values())
        routes_local = route_single_left(chan)
        rank = conn_rank_mixed(routes_local)
        for k in routes_local:
            ranks_all[("left", b, k)] = ("left", b, rank[k])
        ymin = min(p[1] for p in chan.values()); xmin = min(p[0] for p in chan.values())
        # y_below_base is the TOPMOST lane here (add_spine steps DOWN: yb = base - k*PITCH).
        routes_spine = add_spine(routes_local, spine_x_left, y_top_left,
                                 ymin - UNDER_BLOCK_CLEAR_MM,
                                 xmin - 0.5, rank, x_center,
                                 rank_offset=b * N_WIRES_PER_BLOCK, global_base=0)
        for k, poly in routes_spine.items():
            spine_routes_all[("left", b, k)] = poly

    for b in range(N_BLOCKS_PER_COLUMN):
        chan = translate_chan(load_connector("right", N_BLOCKS_PER_COLUMN - 1 - b),
                              connector_dx, connector_dy)
        all_pads += list(chan.values())
        routes_local = route_single_right(chan)
        direct_keys = rows34_keys(chan) & set(routes_local) if RIGHT_DIRECT_ROWS34 else set()
        rank = (conn_rank_right_direct(routes_local, direct_keys) if direct_keys
                else conn_rank_mixed(routes_local, descending=True))
        for k in routes_local:
            ranks_all[("right", b, k)] = ("right", b, rank[k])
        ymin = min(p[1] for p in chan.values()); xmax = max(p[0] for p in chan.values())
        # Under-block lanes: ONE contiguous ladder at PITCH, in the same y band as the left column's.
        # The direct group (rows 3&4) takes the top half -- its own corridor, at
        # y4-UNDER_BLOCK_CLEAR_MM -- and the around group's yb lanes continue straight on down from
        # there, so every adjacent lane is exactly PITCH apart (no wider seam between the groups) and
        # the band is sized to the wires that actually occupy it. Anchoring to the corridor's own
        # lowest lane leaves the clearance in route_single_right as the single source of truth.
        # Without RIGHT_DIRECT_ROWS34 every wire needs a yb lane -> 64 lanes.
        n_around = len(routes_local) - len(direct_keys)
        if direct_keys:
            y_below_base = min(routes_local[c][0][1] for c in direct_keys) - n_around * PITCH
        else:
            y_below_base = ymin - 0.28 - 64 * PITCH
        routes_spine = add_spine_right(routes_local, spine_x_right, y_top_right,
                                       y_below_base, xmax + 0.5, rank, x_center,
                                       rank_offset=b * N_WIRES_PER_BLOCK,
                                       global_base=WIRES_PER_COLUMN,
                                       direct_keys=direct_keys)
        for k, poly in routes_spine.items():
            spine_routes_all[("right", b, k)] = poly

    return spine_routes_all, ranks_all, all_pads


def taper_widths_along_path(poly, fan_bottom_y):
    """IC_TW -> CONNECTOR_TW taper measured along the PATH, not by absolute y.

    A route is IC_TW for as long as it is still in the fan (y >= fan_bottom_y), ramps to
    CONNECTOR_TW over the first WIDEN_LEN_MM of path length below the fan, and HOLDS
    CONNECTOR_TW from there to the pad. Arc length rather than y is what makes the hold
    stick: the left column's top two rows drop down the spine, run left under their block
    and then climb BACK UP into their pads, so a y-keyed ramp re-narrows them on the way
    up (measured: 32 traces running 5.7-8.5 mm at 2.0-2.7 um instead of 3.5). It also
    survives a connector array whose top row sits inside the ramp's y-band, i.e.
    CONN_TOP_BELOW_VIA_MM < NECK_LEN_MM + FAN_LEN_MM + WIDEN_LEN_MM.

    Returns (pts, widths) in mm. Vertices are inserted where the ramp starts and where it
    reaches full width, so the ramp occupies exactly WIDEN_LEN_MM of path however sparsely
    the route happens to be sampled there.
    """
    pts = [(float(x), float(y)) for (x, y) in poly]
    if not pts:
        return [], []

    def width_at(s):
        """s = path length past the fan bottom; None = not there yet."""
        if s is None:
            return IC_TW
        frac = min(s / WIDEN_LEN_MM, 1.0) if WIDEN_LEN_MM > 0 else 1.0
        return IC_TW + frac * (CONNECTOR_TW - IC_TW)

    s = None if pts[0][1] >= fan_bottom_y else 0.0
    out_pts, out_w = [pts[0]], [width_at(s)]
    for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
        seg = math.hypot(x1 - x0, y1 - y0)
        if seg <= 0.0:
            continue
        if s is None:
            if y1 >= fan_bottom_y:                  # still in the fan
                out_pts.append((x1, y1)); out_w.append(IC_TW)
                continue
            t = min(max((y0 - fan_bottom_y) / (y0 - y1), 0.0), 1.0)   # fan-bottom crossing
            x0, y0, seg = x0 + t * (x1 - x0), y0 + t * (y1 - y0), seg * (1.0 - t)
            if t > 0.0:
                out_pts.append((x0, y0)); out_w.append(IC_TW)
            s = 0.0
            if seg <= 0.0:
                continue
        if s < WIDEN_LEN_MM <= s + seg:             # ramp tops out inside this segment
            t = (WIDEN_LEN_MM - s) / seg
            out_pts.append((x0 + t * (x1 - x0), y0 + t * (y1 - y0)))
            out_w.append(CONNECTOR_TW)
        s += seg
        out_pts.append((x1, y1)); out_w.append(width_at(s))
    return out_pts, out_w


def emit_connector_routes(doc, msp, connected_routes, sig, band_y_mm):
    """Stroke each connector route to a polygon on its metal (even->Metal1, odd->Metal2)
    with the IC_TW->CONNECTOR_TW taper, and drop a pad circle on the feeding metal."""
    um = 1000.0
    ensure_layer(doc, WIRE_LAYER, 4)
    ensure_layer(doc, LIFT_LAYER, 3)
    ensure_layer(doc, CONN_PAD_LAYER, 2)

    fan_bottom_y = band_y_mm - NECK_LEN_MM - FAN_LEN_MM

    sig_arr = np.asarray(sig)
    pad_metal = {i: set() for i in range(len(sig_arr))}
    pad_feed = {}
    pad_run = {}
    route_metal = []
    hold_mm = []
    row_of = pad_rows_by_index(sig_arr)
    col_of = pad_cols_by_index(sig_arr)
    n_m1 = n_m2 = 0
    for trace_i, (_gkey, poly) in enumerate(connected_routes.items()):
        layer = WIRE_LAYER if trace_i % 2 == 0 else LIFT_LAYER
        n_m1 += trace_i % 2 == 0
        n_m2 += trace_i % 2 == 1
        px_end, py_end = poly[-1]
        d2 = (sig_arr[:, 0] - px_end) ** 2 + (sig_arr[:, 1] - py_end) ** 2
        best = int(np.argmin(d2))
        if d2[best] <= (PITCH * 0.6) ** 2:
            pad_metal[best].add(layer)
            if len(poly) >= 2 and best not in pad_feed:
                qx, qy = poly[-2]
                # The tear points back up the trace, and may not outrun the FINAL STRAIGHT
                # segment: past that the trace turns away, so a longer tip would jut into
                # empty space and the trace would meet the tear's flank instead of its tip.
                pad_feed[best] = (qx - px_end, qy - py_end)
                pad_run[best] = float(np.hypot(qx - px_end, qy - py_end))
        taper_pts, taper_w = taper_widths_along_path(poly, fan_bottom_y)
        centerline = [(x * um, y * um) for (x, y) in taper_pts]
        widths = [w * um for w in taper_w]
        if d2[best] <= (PITCH * 0.6) ** 2:              # only traces that land on a pad
            _tw, _rl, _hf = pad_approach_for(row_of.get(best, 0), col_of.get(best, "L"))
            if _rl is None and len(poly) >= 2:
                # Auto: ramp from where the CHAMFER BEGINS down to the pad. After chamfering
                # the tail reads a -> b -> pad, with a->b the bevel itself, so the ramp spans
                # the last TWO segments: the bevel plus the straight run after it. Measuring
                # only the straight run would start the ramp where the bevel ENDS and leave
                # the bevel at the un-widened width.
                _rl = float(np.hypot(poly[-1][0] - poly[-2][0], poly[-1][1] - poly[-2][1]))
                if len(poly) >= 3:
                    _rl += float(np.hypot(poly[-2][0] - poly[-3][0],
                                          poly[-2][1] - poly[-3][1]))
            # Hold full width all the way out to the TEAR'S TIP. The tear's flanks are built
            # tangent to two PARALLEL trace edges, so if the trace is still ramping where they
            # land, the tip comes out wider than the wire and the edges splay into it at an
            # angle -- the mismatch is plainly visible on the angled rows. Holding full width
            # past the tip puts the whole tear inside a constant-width wire, which is the
            # condition U4C08 has for free (its trace is a uniform 10 um through the tear).
            # Sized off the tear before clearance shrinking, so a shrunk tear is covered too.
            if TEARDROP and _rl:
                _L = teardrop_len_for(row_of.get(best, 0), col_of.get(best, "L"))
                if _L is not None:
                    _L = min(_L, pad_run.get(best, _L))
                    _hf = min(1.0, max(_hf, _L / _rl))
                    hold_mm.append(_hf * _rl)
            if _tw is not None and _rl:
                centerline, widths = widen_pad_approach(centerline, widths, _tw * um,
                                                        _rl * um, _hf)
        ring = stroke_centerline_to_polygon(centerline, widths)
        if ring:
            msp.add_lwpolyline(ring, close=True, dxfattribs={"layer": layer})
            # Keep the stroked metal (back in mm) for the teardrop clamp: it is the real
            # copper this trace lays down, so the clamp does not have to guess a width.
            route_metal.append((np.asarray(ring, dtype=float) / um,
                                best if d2[best] <= (PITCH * 0.6) ** 2 else -1))

    conn_pad_r = CONN_PAD_R if CONN_PAD_R is not None else PADR
    tear_f = (clamp_teardrop_fillets(sig_arr, pad_feed, pad_run, route_metal,
                                     TEARDROP_CLEAR, PADR, row_of, col_of)
              if TEARDROP else {})
    for i, (x, y) in enumerate(sig_arr):
        u, F = pad_feed.get(i), tear_f.get(i)
        tip_w = teardrop_tip_width(row_of.get(i, 0), col_of.get(i, "L"))
        def _ring(r):
            """Tear when this pad has a known feed direction and a cleared fillet, else circle."""
            if TEARDROP and u is not None and F:
                return teardrop_ring(x * um, y * um, r * um, u[0], u[1], F * um,
                                     tip_w * um, TEARDROP_RES)
            return create_polygon_circle(x * um, y * um, r * um, resolution=64)

        pad_ring = _ring(conn_pad_r) if TEARDROP_PAD_LAYER else \
            create_polygon_circle(x * um, y * um, conn_pad_r * um, resolution=64)
        msp.add_lwpolyline(pad_ring, close=True, dxfattribs={"layer": CONN_PAD_LAYER})
        metal_ring = _ring(PADR)
        for layer in pad_metal[i]:
            msp.add_lwpolyline(metal_ring, close=True, dxfattribs={"layer": layer})
    print(f"connector routes: Metal1(even)={n_m1}, Metal2(odd)={n_m2}; pads={len(sig_arr)}"
          + ("; pad approach widened to "
             + ", ".join(f"{nm} {tw*1000:.1f} um/" + ("auto" if rl is None else f"{rl*1000:.0f} um")
                         for nm, tw, rl in (("angled", PAD_APPROACH_TW_ANGLED, PAD_APPROACH_LEN_ANGLED),
                                            ("vertical", PAD_APPROACH_TW_VERTICAL, PAD_APPROACH_LEN_VERTICAL))
                         if tw is not None)
             + (f"; full width held out to {min(hold_mm)*1000:.0f}-{max(hold_mm)*1000:.0f} um "
                f"so the tears sit in constant-width wire" if hold_mm else "")
             if (PAD_APPROACH_TW_ANGLED or PAD_APPROACH_TW_VERTICAL) else ""))


def fanout_neck_walls(info, jig_left_x_um, jig_right_x_um):
    """(flare_l_um, flare_r_um, flare_y_um) -- the neck walls BELOW the fanout taper, and the y
    the taper ends at.

    The Phase-1 fan spreads the bundle from the band width out to TOTAL_WIRES * FAN_TARGET_PITCH,
    which is WIDER than the jigsaw's bottom edge; a neck pinned to that edge therefore leaves the
    outer lanes off the substrate. Each wall is pushed out to POLY_FAN_MARGIN_UM clear of the
    outermost wire EDGE at the hand-off row -- per side, since the bundle centre and the jigsaw
    centre differ by a few um -- and never pulled in, so this is a no-op when the bundle fits."""
    lanes = [x for x, _y in info["handoff"]]
    half = max(WIRE_W / 2.0, LAND_PAD_R)   # outer lane is a bare wire; take the max so a changed
                                           # pitch that puts a landing pad outermost still clears
    bundle_l, bundle_r = min(lanes) - half, max(lanes) + half
    flare_l = min(jig_left_x_um, bundle_l - POLY_FAN_MARGIN_UM)
    flare_r = max(jig_right_x_um, bundle_r + POLY_FAN_MARGIN_UM)
    return flare_l, flare_r, float(info["y_fan"])


def fanout_taper_start_y(info, top_y_um, flare_y_um, ic_pads_bbox=None):
    """Resolve POLY_FAN_TAPER_START_Y_UM to an absolute y (um), with the pad ceiling enforced.

    None -> the neck top (the graft seam), i.e. the taper spans the whole neck top.
    The ceiling is the LOWEST MEA1K bond pad edge (ic_pads_bbox y0), falling back to the band y
    when the pad squares could not be isolated: the widening may reach up alongside the jigsaw,
    but never into the pad field."""
    ceiling = ic_pads_bbox[1] if ic_pads_bbox else float(info["band_y"])
    if POLY_FAN_TAPER_START_Y_UM is None:
        return top_y_um, ceiling
    y0 = float(POLY_FAN_TAPER_START_Y_UM)
    if y0 >= ceiling:
        raise ValueError(
            f"POLY_FAN_TAPER_START_Y_UM={y0:.1f} um is not below the pads: the lowest bond-pad "
            f"edge is at y={ceiling:.1f} um. Lower it (the taper ends at y={flare_y_um:.1f} um, "
            f"so anything in ({flare_y_um:.1f}, {ceiling:.1f}) is legal).")
    if y0 <= flare_y_um:
        raise ValueError(
            f"POLY_FAN_TAPER_START_Y_UM={y0:.1f} um is at or below the taper END "
            f"(y={flare_y_um:.1f} um, the Phase-1 fan bottom); the ramp would be inverted. "
            f"Use a value in ({flare_y_um:.1f}, {ceiling:.1f}).")
    return y0, ceiling


def fanout_taper_clearance(info, jig_l, jig_r, flare_l, flare_r, y0, y1):
    """Min gap (um) from the outermost Phase-1 fan wire EDGE to the neck wall, over the taper.

    The wall is graft width above y0, a linear ramp to flare_l/flare_r at y1, then constant. A
    taper that starts too late is the one way POLY_FAN_TAPER_START_Y_UM can go wrong: the eased
    fan is nearly vertical at the top, so a late start is usually still fine -- but "usually"
    is not a guarantee, so measure it instead of assuming. Negative -> wire off the substrate."""
    def wall(y, jig, flare):
        if y >= y0:
            return jig
        if y <= y1:
            return flare
        return jig + smootherstep((y0 - y) / (y0 - y1)) * (flare - jig)

    half = WIRE_W / 2.0
    gmin = float("inf")
    for x, y in info["fan_polys"][0]:            # leftmost wire: edge must stay RIGHT of the wall
        gmin = min(gmin, (x - half) - wall(y, jig_l, flare_l))
    for x, y in info["fan_polys"][-1]:           # rightmost wire: edge must stay LEFT of the wall
        gmin = min(gmin, wall(y, jig_r, flare_r) - (x + half))
    return gmin


def emit_polyimide(doc, msp, sig, info, ic_pads_bbox=None):
    """Replace the jigsaw Polyimide outline with ONE solid HATCH = union(jigsaw, neck+board).
    Mounting holes (r_inner) become hatch islands. The Polyimide_Negative band is regenerated
    around the merged outline.

    `info` is the Phase-1 transition dict; it supplies the fan bottom and the hand-off lanes the
    fanout taper is sized from (see fanout_neck_walls). `ic_pads_bbox` is the MEA1K bond-pad box
    (um) that caps how far up the taper may start (see fanout_taper_start_y)."""
    ensure_layer(doc, POLYIMIDE_LAYER, 7)

    jig_ents = list(msp.query(f'LWPOLYLINE[layer=="{POLYIMIDE_LAYER}"]'))
    if not jig_ents:
        raise RuntimeError("no jigsaw Polyimide outline found to graft onto")
    jig_ent = max(jig_ents, key=lambda e: len(e.get_points()))   # the jigsaw outline
    jig_pts = [(float(p[0]), float(p[1])) for p in jig_ent.get_points()]
    jig_poly = Polygon(jig_pts)

    ys = [y for _x, y in jig_pts]
    ymin = min(ys)
    bx = [x for x, y in jig_pts if abs(y - ymin) <= 1.0]
    jig_left_x_um, jig_right_x_um = min(bx), max(bx)
    graft_w_um = jig_right_x_um - jig_left_x_um          # neck-top width = jigsaw bottom-edge width
    # The board stays centered on the CONNECTOR/bundle center (keeps it aligned to the 768 band
    # endpoints), but the NECK walls are anchored to the jigsaw's actual bottom-edge corners. The
    # jigsaw outline isn't centered on the connector, so tying the neck walls to conn_cx used to
    # jog the side edge by (jigsaw_center - conn_cx) where the neck meets the jigsaw; anchoring the
    # neck to jig_left/jig_right removes that step, and the flare absorbs the neck<->board offset.
    conn_cx_um = 0.5 * (np.asarray(sig)[:, 0].min() + np.asarray(sig)[:, 0].max()) * 1000.0
    top_y_mm = (ymin + POLYIMIDE["graft_overlap_um"]) / 1000.0

    flare_l_um, flare_r_um, flare_y_um = fanout_neck_walls(info, jig_left_x_um, jig_right_x_um)
    flared = (flare_l_um < jig_left_x_um) or (flare_r_um > jig_right_x_um)
    taper_y0_um, pad_ceiling_um = fanout_taper_start_y(info, top_y_mm * 1000.0, flare_y_um,
                                                       ic_pads_bbox)
    if flared:
        where = ("graft seam" if POLY_FAN_TAPER_START_Y_UM is None else
                 f"POLY_FAN_TAPER_START_Y_UM, {taper_y0_um - flare_y_um:.0f} um of ramp")
        print(f"neck flare: {graft_w_um:.1f} -> {flare_r_um - flare_l_um:.1f} um "
              f"(L {jig_left_x_um - flare_l_um:+.1f}, R {flare_r_um - jig_right_x_um:+.1f}) "
              f"over y [{taper_y0_um:.0f}, {flare_y_um:.0f}] um from the {where}; clears the "
              f"outer fanned wire by {POLY_FAN_MARGIN_UM:.1f} um "
              f"(pad ceiling y={pad_ceiling_um:.0f} um)")
        # Smootherstep is tangent-vertical at both ends, so the wall's steepest point is mid-span
        # at 15/8 the average slope. Reported because it is the one thing the ease trades away.
        peak = math.degrees(math.atan(SMOOTHERSTEP_PEAK_SLOPE
                                      * max(jig_left_x_um - flare_l_um, flare_r_um - jig_right_x_um)
                                      / (taper_y0_um - flare_y_um)))
        print(f"  taper: smootherstep C2 ease, {POLY_FAN_TAPER_RES} pts/wall, "
              f"{taper_y0_um - flare_y_um:.0f} um of ramp, peak wall tilt {peak:.2f} deg "
              f"(0 deg at both ends -- no corner)")
        # Starting the ramp above the neck top only works because that stretch is swallowed by
        # the jigsaw; if the jigsaw does not actually cover the raised top edge the union would
        # leave a step in the side wall, so say so rather than ship a silent notch.
        gap = fanout_taper_clearance(info, jig_left_x_um, jig_right_x_um,
                                     flare_l_um, flare_r_um, taper_y0_um, flare_y_um)
        print(f"  taper clearance to the outer fan wire: {gap:+.1f} um"
              + ("" if gap >= 0 else "  <-- WIRE OFF THE SUBSTRATE"))
        if gap < 0:
            print(f"  WARNING: the taper starts too late for the fan. Raise "
                  f"POLY_FAN_TAPER_START_Y_UM (ceiling y={pad_ceiling_um:.0f} um) or "
                  f"POLY_FAN_MARGIN_UM.")
        if taper_y0_um > top_y_mm * 1000.0:
            top_edge = LineString([(jig_left_x_um, taper_y0_um), (jig_right_x_um, taper_y0_um)])
            if not jig_poly.buffer(1e-6).contains(top_edge):
                print(f"  WARNING: the neck top raised to y={taper_y0_um:.0f} um is not fully "
                      f"inside the jigsaw; the side wall will step there. Lower "
                      f"POLY_FAN_TAPER_START_Y_UM.")
    else:
        print(f"neck flare: none needed -- the graft ({graft_w_um:.1f} um) already clears the "
              f"fanned bundle by >= {POLY_FAN_MARGIN_UM:.1f} um")

    down_pts = build_downward_polyimide(sig, conn_cx_um / 1000.0, graft_w_um / 1000.0, top_y_mm,
                                        neck_l_mm=jig_left_x_um / 1000.0,
                                        neck_r_mm=jig_right_x_um / 1000.0,
                                        flare_l_mm=flare_l_um / 1000.0 if flared else None,
                                        flare_r_mm=flare_r_um / 1000.0 if flared else None,
                                        flare_y_mm=flare_y_um / 1000.0 if flared else None,
                                        flare_y0_mm=taper_y0_um / 1000.0 if flared else None)
    down_poly = Polygon(down_pts)

    merged = unary_union([jig_poly, down_poly])

    # Extraction-tab bulbs sit on the STRAIGHT neck edges -- the flared walls when the fanout
    # taper is on, the jigsaw corners otherwise.
    neck_left_x = flare_l_um if flared else jig_left_x_um
    neck_right_x = flare_r_um if flared else jig_right_x_um
    bulb_flare_y = flare_y_um if flared else None
    merged = unary_union([merged,
                          *edge_bulbs(sig, neck_left_x, ymin, side="left", n_bulbs=N_LEFT_BULBS,
                                      flare_y_um=bulb_flare_y),
                          *edge_bulbs(sig, neck_right_x, ymin, side="right", n_bulbs=N_RIGHT_BULBS,
                                      flare_y_um=bulb_flare_y)])

    corners, _r_outer, r_inner = load_board_corner_features(sig)
    holes = []
    for (mcx, mcy) in corners:
        c = create_polygon_circle(mcx * 1000.0, mcy * 1000.0, r_inner * 1000.0, resolution=48)
        if merged.contains(Polygon(c)):
            holes.append(c)

    msp.delete_entity(jig_ent)   # now represented by the hatch boundary (Polyimide_Negative is rebuilt below)

    geoms = list(merged.geoms) if merged.geom_type == "MultiPolygon" else [merged]
    hatch = msp.add_hatch(dxfattribs={"layer": POLYIMIDE_LAYER})
    for g in geoms:
        hatch.paths.add_polyline_path([(float(x), float(y)) for x, y in g.exterior.coords],
                                      is_closed=True, flags=ezdxf.const.BOUNDARY_PATH_EXTERNAL)
        for ring in g.interiors:
            hatch.paths.add_polyline_path([(float(x), float(y)) for x, y in ring.coords],
                                          is_closed=True, flags=ezdxf.const.BOUNDARY_PATH_DEFAULT)
    for c in holes:
        hatch.paths.add_polyline_path([(float(x), float(y)) for x, y in c],
                                      is_closed=True, flags=ezdxf.const.BOUNDARY_PATH_DEFAULT)

    ext_n = len(merged.exterior.coords) if merged.geom_type == "Polygon" else -1
    print(f"polyimide: unified {merged.geom_type} (exterior verts={ext_n}), "
          f"mounting holes={len(holes)}/{len(corners)}")

    # --- Polyimide_Negative: a uniform BAND_W_UM ring hugging the OUTSIDE edge ---
    # Replace the interconnect's stale jigsaw-only band (part of which is now interior).
    ensure_layer(doc, POLYIMIDE_NEG_LAYER, 5)
    removed = 0
    for ent in list(msp.query(f'HATCH[layer=="{POLYIMIDE_NEG_LAYER}"]')):
        msp.delete_entity(ent); removed += 1
    # Fill concave mating pockets (the female jigsaw socket, narrower than 2*BAND_CLOSE_UM) with a
    # morphological close BEFORE offsetting, so the band forms a clean solid ring there instead of
    # pinching a hole -- matches the interconnect generator, which builds its band from the
    # socket-FILLED outline. The band's INNER edge is still the exact polyimide (difference(merged)).
    poly_fill = (merged.buffer(BAND_CLOSE_UM, join_style=2, mitre_limit=5)
                       .buffer(-BAND_CLOSE_UM, join_style=2, mitre_limit=5))
    band = poly_fill.buffer(BAND_W_UM, join_style=2, mitre_limit=5).difference(merged)
    band_geoms = list(band.geoms) if band.geom_type == "MultiPolygon" else [band]
    neg_hatch = msp.add_hatch(dxfattribs={"layer": POLYIMIDE_NEG_LAYER})
    n_rings = 0
    for g in band_geoms:
        if g.is_empty:
            continue
        neg_hatch.paths.add_polyline_path([(float(x), float(y)) for x, y in g.exterior.coords],
                                          is_closed=True, flags=ezdxf.const.BOUNDARY_PATH_EXTERNAL)
        for ring in g.interiors:
            neg_hatch.paths.add_polyline_path([(float(x), float(y)) for x, y in ring.coords],
                                              is_closed=True, flags=ezdxf.const.BOUNDARY_PATH_DEFAULT)
        n_rings += 1
    print(f"polyimide_negative: {BAND_W_UM:.0f} um band, replaced {removed} old, "
          f"{n_rings} ring(s), bbox x[{band.bounds[0]:.0f},{band.bounds[2]:.0f}] "
          f"y[{band.bounds[1]:.0f},{band.bounds[3]:.0f}] um")
    return merged, holes


def interconnect_pad_bbox(msp, pad_um=(20.0, 100.0)):
    """Combined bbox (um) of the MEA1K bond pads on the rotated interconnect. Isolates the
    ~53um pad SQUARES by size, so the thin leads / polyimide don't widen the box. Call while
    msp still holds only the interconnect (before fan/via/connector geometry is emitted).
    Returns [x0, y0, x1, y1] in um, or None if no pad-sized squares are found."""
    lo, hi = pad_um
    xs0 = ys0 = float("inf")
    xs1 = ys1 = float("-inf")
    found = False
    for e in msp.query("LWPOLYLINE"):
        p = np.array(e.get_points("xy"), dtype=float)
        if len(p) < 4:
            continue
        w, h = p[:, 0].ptp(), p[:, 1].ptp()
        if lo <= w <= hi and lo <= h <= hi:
            found = True
            xs0 = min(xs0, p[:, 0].min()); ys0 = min(ys0, p[:, 1].min())
            xs1 = max(xs1, p[:, 0].max()); ys1 = max(ys1, p[:, 1].max())
    return [xs0, ys0, xs1, ys1] if found else None


def main():
    print("=== Phase 1: rotate + fan + via transition ===")
    doc, msp, _center = load_and_rotate_interconnect()
    endpoints = extract_band_endpoints(msp)
    # Capture the MEA1K bond-pad footprint now, while msp holds ONLY the rotated interconnect.
    ic_pads_bbox = interconnect_pad_bbox(msp)
    lanes, x_center_um, max_shift = build_fan_lanes(endpoints)
    # Native (minimum adjacent) band pitch, read straight from the DXF so it tracks the interconnect:
    # this is the tightest wire spacing, and the floor the eased fan must not go below.
    xs_sorted = sorted(p[0] for p in endpoints)
    native_pitch = min(b - a for a, b in zip(xs_sorted, xs_sorted[1:]))
    D = max(abs(p[0] - x_center_um) for p in endpoints)   # outer offset from centre at the entry
    fan_target_gap = FAN_TARGET_GAP if FAN_TARGET_GAP is not None else native_pitch - WIRE_W
    fan_g, fan_len = eased_fan_profile(native_pitch, FAN_TARGET_PITCH, D, fan_target_gap, WIRE_W,
                                       FAN_TILT_CAP_DEG, FAN_RES, FAN_LEN_SLACK)
    if FAN_LEN is not None:
        fan_len = FAN_LEN                            # hard override: same curve, stretched to length
    print(f"phase-1 fan (eased): native pitch {native_pitch:.2f} um, target gap {fan_target_gap:.2f} um, "
          f"len {fan_len:.0f} um, tilt cap {FAN_TILT_CAP_DEG:.0f} deg, center x={x_center_um:.1f} um")

    info = build_transition(endpoints, lanes, fan_g, fan_len)
    n_m1 = sum(l == WIRE_LAYER for l in info["layer_of"])
    n_m2 = sum(l == LIFT_LAYER for l in info["layer_of"])
    print(f"split: Metal1(even)={n_m1}, Metal2(odd)={n_m2}; via row y={info['y_via']:.1f} um")
    ok1, msgs = check_transition(info, lanes, FAN_TARGET_PITCH, fan_target_gap)
    print("DRC (via transition):")
    for m in msgs:
        print("  " + m)
    emit_transition(msp, info)

    print("\n=== Phase 2: route the two-layer bundle into the connector ===")
    sl_floor_um = (_FAN2_SL_TIGHT - IC_TW) * 1000.0     # untilted connector same-layer floor
    print(f"phase-2 fan (eased): len {FAN_LEN_MM:.2f} mm, target same-layer gap "
          f"{FAN2_TARGET_GAP_MM*1000.0:.2f} um, tilt cap {FAN2_TILT_CAP_DEG:.0f} deg "
          f"(connector same-layer floor {sl_floor_um:.2f} um)")
    # Hand-off (via row) anchors, in mm, left-to-right (== gidx order).
    handoff_mm = [(x / 1000.0, y / 1000.0) for (x, y) in info["handoff"]]
    band_y_mm = info["y_via"] / 1000.0
    spine_x_left = handoff_mm[0][0]
    spine_x_right = handoff_mm[WIRES_PER_COLUMN][0]

    # Place the connector: centre it in x on the bundle centre, and set its top row
    # CONN_TOP_BELOW_VIA_MM below the via row so the spine runs downward without inverting.
    sig_original = _load_extended_sig()
    conn_cx = 0.5 * (sig_original[:, 0].min() + sig_original[:, 0].max())
    conn_ymax = sig_original[:, 1].max()
    connector_dx = (x_center_um / 1000.0) - conn_cx
    connector_dy = (band_y_mm - CONN_TOP_BELOW_VIA_MM) - conn_ymax
    sig = translated_signal_pads(sig_original, connector_dx, connector_dy)
    print(f"connector translate: dx={connector_dx:.4f} mm, dy={connector_dy:.4f} mm; "
          f"pads x[{sig[:,0].min():.2f},{sig[:,0].max():.2f}] y[{sig[:,1].min():.2f},{sig[:,1].max():.2f}] mm")

    spine_routes, ranks_all, _pads = build_columns_staged(
        spine_x_left, band_y_mm, spine_x_right, band_y_mm,
        connector_dx=connector_dx, connector_dy=connector_dy)
    if len(spine_routes) != TOTAL_WIRES:
        raise RuntimeError(f"expected {TOTAL_WIRES} spine routes, got {len(spine_routes)}")

    connected = connect_handoff_to_routes(spine_routes, ranks_all, handoff_mm)
    # Verify the eased Phase-2 fan holds the target SAME-LAYER gap (pre-chamfer geometry). Spine order
    # == connected.items() order == emit's trace_i parity, so layer = ti % 2 alternates M1/M2.
    neck_bottom_mm = band_y_mm - NECK_LEN_MM
    fan_bottom_mm = neck_bottom_mm - FAN_LEN_MM
    fan_polys2, fan_layers2, fan_owners2 = [], [], []
    for ti, (_gk, poly) in enumerate(connected.items()):
        for run in band_runs(poly, fan_bottom_mm, neck_bottom_mm):
            fan_polys2.append(run); fan_layers2.append(ti % 2); fan_owners2.append(ti)
    if fan_polys2:
        gmin2_um = min_adjacent_fan_gap(fan_polys2, IC_TW, layers=fan_layers2,
                                        owners=fan_owners2) * 1000.0
        tgt_um = FAN2_TARGET_GAP_MM * 1000.0
        ok2 = gmin2_um >= tgt_um - FAN_CHECK_TOL_UM
        print(f"phase-2 fan min same-layer gap = {gmin2_um:.3f} um (target {tgt_um:.2f} um): "
              f"{'OK' if ok2 else 'FAIL'}")
        if not ok2:
            print("  FAIL: raise FAN_LEN_SLACK / lower FAN2_TILT_CAP_DEG for a gentler Phase-2 fan.")
    connected, angled_stats, angled_orig = angled_pad_entry(connected, sig)
    if angled_stats:
        d_req = angled_entry_len()
        n_tot = sum(len(v) for v in angled_stats.values())
        print(f"angled pad entry: {n_tot} routes at 45 deg "
              f"(L {ANGLED_ENTRY_SIDE} / R {ANGLED_ENTRY_SIDE_RIGHT}), "
              f"target d={d_req*1000:.0f} um"
              + (" (auto: trace reaches the tear tip)" if ANGLED_ENTRY_LEN is None else ""))
        for r in sorted(angled_stats):
            ang = np.asarray([a for _v, a in angled_stats[r]])
            n45 = int((ang <= 45.5).sum())
            none_ = int(sum(1 for v, _a in angled_stats[r] if v <= 0))
            msg = (f"    {r[0]} row {r[1]}: {len(ang)} single-segment entries, "
                   f"{n45} at 45 deg, {len(ang)-n45} tilted steeper "
                   f"(median {np.median(ang):.0f} deg, max {ang.max():.0f} deg)")
            if none_:
                msg += f"; {none_} left perpendicular"
            print(msg)
    connected, n_rev = repair_angled_crossings(connected, angled_orig)
    print(f"chamfered corners: cap {CHAMFER_MAX_MM*1000:.0f} um, frac {CHAMFER_FRAC}"
          + (f"; angled tails {ANGLED_ENTRY_CHAMFER*1000:.0f} um"
             if ANGLED_ENTRY_CHAMFER is not None else "")
          + (f"; {n_rev} angled tail(s) reverted where the bevel would clip a neighbour"
             if n_rev else ""))
    # Each trace's own terminating pad (same order to_seglist enumerates connected), so the
    # pad-approach bevel isn't flagged as a hit against the pad it lands on.
    sig_arr = np.asarray(sig)
    end_pads = []
    for _gk, poly in connected.items():
        d2 = (sig_arr[:, 0] - poly[-1][0]) ** 2 + (sig_arr[:, 1] - poly[-1][1]) ** 2
        best = int(np.argmin(d2))
        end_pads.append(tuple(sig_arr[best]) if d2[best] <= (PITCH * 0.6) ** 2 else None)
    xings, hits = _check_fast(to_seglist(connected), [tuple(p) for p in sig], own_pad=end_pads)
    # NOTE what this pair does NOT cover. _check_fast works on centreline segments and pad
    # CENTRES only -- it carries no trace width at all -- so crossings/pad_hits is not an
    # edge-to-edge check of anything. In particular nothing here sees the teardrops or the
    # PAD_APPROACH_TW widening: both are emission-only, drawn later in
    # emit_connector_routes, after these checks have run. The teardrop clamp
    # (clamp_teardrop_fillets) is the only edge-to-edge test of connector-side copper (besides
    # the fan gap checks) and it guards
    # the tear alone, against the real stroked copper. Raise PAD_APPROACH_TW_* and nothing
    # checks the widened trace against its neighbours -- inspect the DXF.
    print(f"connected routes: {len(connected)}, crossings={xings}, pad_hits={hits}")

    emit_connector_routes(doc, msp, connected, sig, band_y_mm)

    print("\n=== Phase 3: unified polyimide (jigsaw top + neck/board) ===")
    merged, holes = emit_polyimide(doc, msp, sig, info, ic_pads_bbox=ic_pads_bbox)

    doc.saveas(FINAL_DXF)
    print(f"wrote {FINAL_DXF}")


if __name__ == "__main__":
    main()
