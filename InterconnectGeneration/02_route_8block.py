"""Route the 8-block interconnect into the 8-block Molex 227044 connector, on ONE metal layer.

All 512 traces stay on Metal1 from the interconnect band to the connector pads: no vias,
no landing pads. Compare 02_route_12block.py, which lifts every second wire onto Metal2.

PHASE 1 (band -> hand-off row), in um:
  1. open the stage-01 DXF and ROTATE it 180 deg so its wire band faces DOWN.
  2. take each Metal1 wire's lowest vertex as its band endpoint. The 16 pair-short ties
     that stop short of the band are not exits and are dropped (extract_band_endpoints).
  3. NO fan-out: every wire keeps its native band x (~7 um pitch) and drops straight down
     (NECK_LEN + the Phase-1 run + VIA_ROW_GAP, ~16 mm) to the hand-off row.

PHASE 2 (hand-off row -> connector), in mm:
  4. a short neck in which each trace narrows from the band width (IC_TW, 3.5 um) to
     CONNECTOR_TW (2 um), then ONE eased fan-in from the native x straight to the connector
     pitch -- CONNECTOR_PITCH = 2 um trace + 3 um gap. All lanes share one layer, so that
     3 um IS the same-layer gap the fan must hold (HANDOVER §6: it holds it with no margin).
  5. staged spine/corridor routing into the 2 x 4 connector blocks, one wire per CKT,
     45 deg angled entry on the flanked rows, teardrop pads (HANDOVER §5).

PHASE 3: the polyimide substrate -- the stage-01 jigsaw top, the neck, Simon's board.

Writes one DXF. Phase 1 works in um (the interconnect's units); Phase 2 in mm (the connector
CSV's units), converted to um on write. KLayout reads 1 unit = 1 um.
"""

import numpy as np
import ezdxf
from shapely.ops import unary_union
from shapely.geometry import Polygon

from lib import active


def _cli():
    import argparse
    p = argparse.ArgumentParser(description="Route an interconnect DXF into the Molex connector.")
    p.add_argument("--in", dest="in_dxf", default=None,
                   help="interconnect DXF from stage 01; bare name -> designs/ "
                        "(default: config_8block.DEFAULT_IN)")
    p.add_argument("--out", dest="out_dxf", default=None,
                   help="routed output DXF; bare name -> designs/ "
                        "(default: config_8block.DEFAULT_OUT)")
    return p.parse_args() if __name__ == "__main__" else p.parse_args([])


# Select the design BEFORE importing anything else from lib/ -- see lib/active.py.
_ARGS = _cli()
active.select("8block", _ARGS.in_dxf, _ARGS.out_dxf)
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
from lib.board import build_downward_polyimide, edge_bulbs, load_board_corner_features


# ======================================================================================
# What is specific to the 8-block -- everything else is shared, in lib/
# ======================================================================================
#   * ONE metal layer: no fan-out and no via split in Phase 1 -- every wire keeps its native
#     band x and drops straight down (build_fan_lanes, build_transition), all on Metal1.
#   * The band carries folded-in pair-short ties that stop short of it; they are not exits
#     (extract_band_endpoints, band_tol).
#   * In-block routing: one wire per CKT (route_blue_terminal*, route_green_single*).
#   * The spine starts from each wire's native band x (add_spine*, build_columns_staged).
#   * Traces narrow from the band width to CONNECTOR_TW in the neck, above the fan-in
#     (emit_connector_routes).
#   * The polyimide neck needs no flare (emit_polyimide).
# Knobs: config_8block.py.


def extract_band_endpoints(msp, expected=TOTAL_WIRES, tol=0.2, band_tol=10.0):
    """After rotation the band is the MIN-y edge; take each Metal1 wire's min-y vertex. Only wires
    that actually REACH the band are exits: the interconnect's edge pair-shorts fold some bottom pads
    into a neighbour's lane as ties that stop short of the band -- those are dropped (their min-y sits
    band_tol+ above the true band line, so they never count as an exit)."""
    cand = []
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
        cand.append((ex, ymin))

    band_y = min(y for _, y in cand) if cand else 0.0
    n_ties = sum(1 for _, y in cand if y - band_y > band_tol)
    endpoints = [(ex, y) for (ex, y) in cand if y - band_y <= band_tol]   # keep only wires at the band
    band_ys = [y for _, y in endpoints]

    endpoints.sort(key=lambda p: p[0])
    if band_ys:
        print(f"band endpoints: n={len(endpoints)}, band y in "
              f"[{min(band_ys):.2f}, {max(band_ys):.2f}] um, "
              f"x in [{endpoints[0][0]:.2f}, {endpoints[-1][0]:.2f}] um"
              + (f" ({n_ties} folded-in ties dropped)" if n_ties else ""))
    if len(endpoints) != expected:
        print(f"  WARNING: expected {expected} band endpoints, got {len(endpoints)}")
    return endpoints


def build_fan_lanes(endpoints, target_pitch=FAN_TARGET_PITCH):
    """NO FAN-OUT: every wire keeps its NATIVE band x. Phase 1 is then a pure straight drop, and the
    single Phase-2 fan-in narrows each wire from its native x straight to its connector x. Returns the
    native positions as the 'lanes' (so the handoff is native) plus the bundle centre."""
    xs = np.array([p[0] for p in endpoints])
    x_center = 0.5 * (xs.min() + xs.max())
    return xs.copy(), x_center, 0.0


def build_transition(endpoints, lanes, fan_g, fan_len):
    """Straight-drop geometry. Every wire stays on Metal1 (no landing pads, no vias) and hands off
    to Phase 2 at (lane, y_via). lanes == the native band x, so the 'fan' x(s) = ex + (lane - ex) *
    g(s) is a straight vertical; fan_g / fan_len only set the length of that section."""
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
        m1_polys.append(head)                           # SINGLE LAYER: every wire stays on Metal1,
        layer_of.append(WIRE_LAYER)                     # no lift -> no landing pads, no vias

    return {
        "m1_polys": m1_polys, "m2_polys": m2_polys,
        "m1_pads": m1_pads, "m2_pads": m2_pads, "vias": vias,
        "layer_of": layer_of, "handoff": handoff, "fan_polys": fan_polys,
        "band_y": band_y, "y_via": y_via,
    }


def route_blue_terminal(chan):
    """Top pair (CKT 33-64): one wire per pad. Same pattern as route_green_single,
    entering above row 1 and dipping DOWN to each top-pair pad. IN-only (3 points)."""
    rows = sorted({round(chan[c][1], 3) for c in chan})
    if len(rows) < 2:
        return {}
    y2, y1 = rows[-2], rows[-1]                               # top pair = two HIGHEST rows
    top = sorted((c for c in chan if round(chan[c][1], 3) in (y1, y2)),
                 key=lambda c: chan[c][0])
    xlo = min(chan[c][0] for c in top)
    above1_base = y1 + 0.12                                   # lowest IN lane (above row1)
    x_in_entry = xlo - 1.2

    routes = {}
    for k, c in enumerate(top):
        px, py = chan[c]
        lane = above1_base + k * PITCH
        routes[c] = [(x_in_entry, lane), (px, lane), (px, py)]
    return routes


def route_green_single(chan):
    """Bottom pair (CKT 1-32): one wire per pad, from the row 2-3 corridor down into the pad
    centre. Returns {c: polyline} (plain CKT key). IN-only (3 points), terminates at the pad."""
    rows = sorted({round(chan[c][1], 3) for c in chan})       # the pair's 2 rows, ascending
    if len(rows) < 2:
        return {}
    y4, y3 = rows[0], rows[1]
    bottom = sorted((c for c in chan if round(chan[c][1], 3) in (y3, y4)),
                    key=lambda c: chan[c][0])
    xlo = min(chan[c][0] for c in bottom)
    corridor_base = y3 + 0.105                                 # lowest IN lane (above row3)
    x_in_entry = xlo - 1.2

    routes = {}
    for k, c in enumerate(bottom):                            # k = 0..31, CKT order
        px, py = chan[c]
        lane = corridor_base + k * PITCH                      # one lane per pad
        routes[c] = [(x_in_entry, lane), (px, lane), (px, py)]
    return routes


def route_block(chan):
    """Per-connector block: every CKT carries ONE wire. CKT 1-32 on the bottom
    pair (route_green_single), CKT 33-64 on the top pair (route_blue_terminal). Works
    for both columns regardless of which physical row pair holds CKT 1-32."""
    chan_bottom = {c: chan[c] for c in chan if c <= 32}
    chan_top    = {c: chan[c] for c in chan if c >= 33}
    routes = route_green_single(chan_bottom)
    routes.update(route_blue_terminal(chan_top))
    return routes


def route_blue_terminal_right(chan):
    """Right column single (CKT 33-64 = BOTTOM pair after rotation), corridor just below row 4.

    RIGHT_DIRECT_ROWS34 picks the lane ORDER, which is what decides where the corridor can be fed
    from (add_spine_right builds the matching long-haul):
      True  -- the LEFTMOST pad (the one NEAREST the central bundle) gets the HIGHEST lane. Fed
               from the LEFT: running right, a wire turning up early sits ABOVE the wires still
               travelling past it, so its riser never cuts their lanes.
      False -- the RIGHTMOST pad gets the highest lane. Fed from the RIGHT (the detour out to
               xu and back), where the nesting runs the other way.
    Only poly[0][1] (the lane), poly[1][0] (pad x) and poly[2][1] (pad y) are read downstream, so
    the x_in_entry sentinel never reaches the output either way."""
    rows = sorted({round(chan[c][1], 3) for c in chan})
    if len(rows) < 2:
        return {}
    y4, y3 = rows[0], rows[1]                                  # y4 = bottom row
    bot = sorted((c for c in chan if round(chan[c][1], 3) in (y3, y4)),
                 key=lambda c: chan[c][0])
    n = len(bot)
    xhi = max(chan[c][0] for c in bot)
    # With RIGHT_DIRECT_ROWS34 this corridor IS the top of the under-block ladder (the around
    # group's lanes continue down from it), so it takes the ladder's clearance -- that is what puts
    # the right column's stack in exactly the same y band as the left's. Fed from the right edge
    # instead, it is a standalone corridor and keeps the tighter corridor clearance.
    below4_top = y4 - (UNDER_BLOCK_CLEAR_MM if RIGHT_DIRECT_ROWS34 else CORRIDOR_CLEAR_MM)
    x_in_entry = xhi + 1.2

    routes = {}
    for k, c in enumerate(bot):
        px, py = chan[c]
        lane = below4_top - (k if RIGHT_DIRECT_ROWS34 else n - 1 - k) * PITCH
        routes[c] = [(x_in_entry, lane), (px, lane), (px, py)]
    return routes


def route_green_single_right(chan):
    """Right-column top pair (CKT 1-32): one wire per pad. Enters the row 2-3
    corridor from the RIGHT, runs LEFT, then UP into the pad, one lane per pad. Returns {c: polyline}."""
    rows = sorted({round(chan[c][1], 3) for c in chan})
    if len(rows) < 2:
        return {}
    y2, y1 = rows[-2], rows[-1]                               # y1 = topmost row
    top = sorted((c for c in chan if round(chan[c][1], 3) in (y1, y2)),
                 key=lambda c: chan[c][0])
    n = len(top)
    xhi = max(chan[c][0] for c in top)
    corridor_top = y2 - 0.105                                   # highest IN lane (just below row 2)
    x_in_entry = xhi + 1.2                                     # outside-RIGHT of column

    routes = {}
    for k, c in enumerate(top):                               # k=0 leftmost, k=n-1 rightmost
        px, py = chan[c]
        lane = corridor_top - (n - 1 - k) * PITCH             # rightmost gets highest lane
        routes[c] = [(x_in_entry, lane), (px, lane), (px, py)]
    return routes


def route_block_right(chan):
    """Right-column block: every CKT carries ONE wire. CKT 1-32 on the top pair
    (route_green_single_right), CKT 33-64 on the bottom pair (route_blue_terminal_right)."""
    chan_top    = {c: chan[c] for c in chan if c <= 32}      # CKT 1-32 sit on TOP pair (rotated)
    chan_bottom = {c: chan[c] for c in chan if c >= 33}      # CKT 33-64 sit on BOTTOM pair
    routes = route_green_single_right(chan_top)
    routes.update(route_blue_terminal_right(chan_bottom))
    return routes


def add_spine(routes, handoff_x, band_y, y_below_base, x_up_base, rank, x_center,
              rank_offset=0, global_base=0):
    """Left-column long-haul: NECK (vertical @ the wire's NATIVE x) -> FAN-IN (native x ->
    CONNECTOR_PITCH) -> body (down spine, below block, up left, into corridor). The fan-in maps each
    wire straight from its native band x (handoff_x) to its connector-pitch slot -- no fan-out."""
    neck_bottom_y = band_y - NECK_LEN_MM
    fan_bottom_y = neck_bottom_y - FAN_LEN_MM
    widen_bottom_y = fan_bottom_y - WIDEN_LEN_MM
    mid = (TOTAL_WIRES - 1) / 2.0
    out = {}
    for c, poly in routes.items():
        lane = poly[0][1]; px = poly[1][0]; py = poly[2][1]; tail = poly[3:]
        k = rank[c]; gidx = k + rank_offset
        xs_top = handoff_x[global_base + gidx]                 # this wire's NATIVE band x (straight drop)
        xs_body = x_center + (global_base + gidx - mid) * CONNECTOR_PITCH
        yb = y_below_base - k * PITCH
        xu = x_up_base - k * PITCH
        fan_pts = [(xs_body + (xs_top - xs_body) * FAN2_G[j],
                    fan_bottom_y + (j / FAN_RES) * FAN_LEN_MM)
                   for j in range(FAN_RES, -1, -1)]     # eased fan: neck top -> connector bottom
        out[c] = fan_pts + [(xs_body, widen_bottom_y), (xs_body, yb), (xu, yb), (xu, lane),
                            (px, lane), (px, py)] + tail
    return out


def add_spine_right(routes, handoff_x, band_y, y_below_base, x_up_base, rank, x_center,
                    rank_offset=0, global_base=0, direct_keys=()):
    """Right-column long-haul. Wires in direct_keys (rows 3&4 under RIGHT_DIRECT_ROWS34) drop
    STRAIGHT into their corridor lane, run right and turn up into the pad -- their corridor sits
    just above the under-block band, so the trip out to x_up_base and back is pure detour. Every
    other wire keeps the around-the-right-edge route (down to yb, right to xu, up, back left),
    which rows 1&2 genuinely need: their corridor is buried in the mid-gap between pad rows."""
    neck_bottom_y = band_y - NECK_LEN_MM
    fan_bottom_y = neck_bottom_y - FAN_LEN_MM
    widen_bottom_y = fan_bottom_y - WIDEN_LEN_MM
    mid = (TOTAL_WIRES - 1) / 2.0
    out = {}
    for c, poly in routes.items():
        lane = poly[0][1]; px = poly[1][0]; py = poly[2][1]; tail = poly[3:]
        k = rank[c]; gidx = k + rank_offset
        xs_top = handoff_x[global_base + gidx]                 # this wire's NATIVE band x (straight drop)
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


def build_columns_staged(handoff_x, x_center, band_y_mm,
                         connector_dx=0.0, connector_dy=0.0):
    """handoff_x: native band x (mm) per GLOBAL gidx (left 0..WIRES_PER_COLUMN-1, then right).
    x_center: bundle centre (mm) the connector-pitch slots fan into."""
    spine_routes_all = {}
    ranks_all = {}
    all_pads = []

    for b in range(N_BLOCKS_PER_COLUMN):
        chan = translate_chan(load_connector("left", b), connector_dx, connector_dy)
        all_pads += list(chan.values())
        routes_local = route_block(chan)
        rank = conn_rank_mixed(routes_local)
        for k in routes_local:
            ranks_all[("left", b, k)] = ("left", b, rank[k])
        ymin = min(p[1] for p in chan.values()); xmin = min(p[0] for p in chan.values())
        # y_below_base is the TOPMOST lane here (add_spine steps DOWN: yb = base - k*PITCH).
        routes_spine = add_spine(routes_local, handoff_x, band_y_mm,
                                 ymin - UNDER_BLOCK_CLEAR_MM,
                                 xmin - 0.5, rank, x_center,
                                 rank_offset=b * N_WIRES_PER_BLOCK, global_base=0)
        for k, poly in routes_spine.items():
            spine_routes_all[("left", b, k)] = poly

    for b in range(N_BLOCKS_PER_COLUMN):
        chan = translate_chan(load_connector("right", N_BLOCKS_PER_COLUMN - 1 - b),
                              connector_dx, connector_dy)
        all_pads += list(chan.values())
        routes_local = route_block_right(chan)
        # CKT 33-64 sit on the right column's physical rows 3&4, whose corridor is just below the
        # block -- reachable without the detour out to xmax+0.5. See RIGHT_DIRECT_ROWS34.
        direct_keys = {c for c in routes_local if c >= 33} if RIGHT_DIRECT_ROWS34 else set()
        rank = (conn_rank_right_direct(routes_local, direct_keys) if direct_keys
                else conn_rank_mixed(routes_local, descending=True))
        for k in routes_local:
            ranks_all[("right", b, k)] = ("right", b, rank[k])
        ymin = min(p[1] for p in chan.values()); xmax = max(p[0] for p in chan.values())
        # Under-block lanes: ONE contiguous ladder at PITCH, like the left column's. The direct
        # group (rows 3&4) takes the top half -- its own corridor, at y4-UNDER_BLOCK_CLEAR_MM --
        # and the around group's yb lanes continue straight on down from there, so every adjacent lane in the
        # stack is exactly PITCH apart (no wider seam between the two groups) and the band is sized
        # to the wires that actually occupy it. Anchoring to the corridor's own lowest lane leaves
        # the y4-UNDER_BLOCK_CLEAR_MM clearance in route_blue_terminal_right as the single source of
        # truth. Without RIGHT_DIRECT_ROWS34 every wire needs a yb lane -> N_WIRES_PER_BLOCK lanes.
        n_around = len(routes_local) - len(direct_keys)
        if direct_keys:
            y_below_base = min(routes_local[c][0][1] for c in direct_keys) - n_around * PITCH
        else:
            y_below_base = ymin - 0.28 - N_WIRES_PER_BLOCK * PITCH
        routes_spine = add_spine_right(routes_local, handoff_x, band_y_mm,
                                       y_below_base, xmax + 0.5,
                                       rank, x_center,
                                       rank_offset=b * N_WIRES_PER_BLOCK,
                                       global_base=WIRES_PER_COLUMN,
                                       direct_keys=direct_keys)
        for k, poly in routes_spine.items():
            spine_routes_all[("right", b, k)] = poly

    return spine_routes_all, ranks_all, all_pads


def emit_connector_routes(doc, msp, connected_routes, sig, band_y_mm):
    """Stroke each connector route to a Metal1 polygon, narrowing IC_TW -> CONNECTOR_TW in the
    neck (above the fan-in), and drop a pad circle on Metal1."""
    um = 1000.0
    ensure_layer(doc, WIRE_LAYER, 4)
    ensure_layer(doc, LIFT_LAYER, 3)
    ensure_layer(doc, CONN_PAD_LAYER, 2)

    neck_bottom_y = band_y_mm - NECK_LEN_MM

    def trace_width(y):
        # Narrow from the 3.5 um band lead (IC_TW) down to the 2 um connector trace (CONNECTOR_TW) in
        # the NECK -- ABOVE the fan-in -- so the fan converges 2 um traces to CONNECTOR_PITCH and keeps
        # the full 3 um gap. (Doing it below the fan left 3.5 um traces at 5 um pitch -> only 1.5 um.)
        if y >= neck_bottom_y:
            frac = (band_y_mm - y) / NECK_LEN_MM
            return IC_TW + frac * (CONNECTOR_TW - IC_TW)
        return CONNECTOR_TW

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
        layer = WIRE_LAYER                              # SINGLE LAYER: every connector trace on Metal1
        n_m1 += 1
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
        centerline = [(x * um, y * um) for (x, y) in poly]
        widths = [trace_width(y) * um for (_x, y) in poly]
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
    print(f"connector routes: Metal1={n_m1}, Metal2={n_m2} (single layer); pads={len(sig_arr)}"
          + ("; pad approach widened to "
             + ", ".join(f"{nm} {tw*1000:.1f} um/" + ("auto" if rl is None else f"{rl*1000:.0f} um")
                         for nm, tw, rl in (("angled", PAD_APPROACH_TW_ANGLED, PAD_APPROACH_LEN_ANGLED),
                                            ("vertical", PAD_APPROACH_TW_VERTICAL, PAD_APPROACH_LEN_VERTICAL))
                         if tw is not None)
             + (f"; full width held out to {min(hold_mm)*1000:.0f}-{max(hold_mm)*1000:.0f} um "
                f"so the tears sit in constant-width wire" if hold_mm else "")
             if (PAD_APPROACH_TW_ANGLED or PAD_APPROACH_TW_VERTICAL) else ""))


def emit_polyimide(doc, msp, sig):
    """Replace the jigsaw Polyimide outline with ONE solid HATCH = union(jigsaw, neck+board).
    Mounting holes (r_inner) become hatch islands. The Polyimide_Negative band is regenerated
    around the merged outline."""
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
    # The board stays centered on the CONNECTOR/bundle center (keeps it aligned to the 512 band
    # endpoints), but the NECK walls are anchored to the jigsaw's actual bottom-edge corners. The
    # jigsaw outline isn't centered on the connector, so tying the neck walls to conn_cx used to
    # jog the side edge by (jigsaw_center - conn_cx) where the neck meets the jigsaw; anchoring the
    # neck to jig_left/jig_right removes that step, and the flare absorbs the neck<->board offset.
    conn_cx_um = 0.5 * (np.asarray(sig)[:, 0].min() + np.asarray(sig)[:, 0].max()) * 1000.0
    top_y_mm = (ymin + POLYIMIDE["graft_overlap_um"]) / 1000.0

    down_pts = build_downward_polyimide(sig, conn_cx_um / 1000.0, graft_w_um / 1000.0, top_y_mm,
                                        neck_l_mm=jig_left_x_um / 1000.0,
                                        neck_r_mm=jig_right_x_um / 1000.0)
    down_poly = Polygon(down_pts)

    merged = unary_union([jig_poly, down_poly])

    # Extraction-tab bulbs sit on the (jigsaw-anchored) neck edges.
    neck_left_x = jig_left_x_um
    neck_right_x = jig_right_x_um
    merged = unary_union([merged,
                          *edge_bulbs(sig, neck_left_x, ymin, side="left", n_bulbs=N_LEFT_BULBS),
                          *edge_bulbs(sig, neck_right_x, ymin, side="right", n_bulbs=N_RIGHT_BULBS)])

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


def main():
    print("=== Phase 1: rotate + straight drop to the hand-off row ===")
    doc, msp, _center = load_and_rotate_interconnect()
    endpoints = extract_band_endpoints(msp)
    lanes, x_center_um, max_shift = build_fan_lanes(endpoints)
    # Native (minimum adjacent) band pitch, read straight from the DXF so it tracks the
    # interconnect: this is the tightest wire spacing.
    xs_sorted = sorted(p[0] for p in endpoints)
    native_pitch = min(b - a for a, b in zip(xs_sorted, xs_sorted[1:]))
    # Phase 1 has no fan-out (lanes = native x); the eased profile only sets fan_len, which here is
    # a straight vertical run. D = outermost wire offset from centre at the band.
    D = max(abs(p[0] - x_center_um) for p in endpoints)
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
    print(f"layers: Metal1={n_m1}, Metal2={n_m2} (single layer); hand-off row y={info['y_via']:.1f} um")
    ok1, msgs = check_transition(info, lanes, FAN_TARGET_PITCH, fan_target_gap)
    print("DRC (Phase 1):")
    for m in msgs:
        print("  " + m)
    emit_transition(msp, info)

    print("\n=== Phase 2: route the single-layer bundle into the connector ===")
    sl_floor_um = (_FAN2_SL_TIGHT - CONNECTOR_TW) * 1000.0     # untilted connector same-layer floor
    print(f"phase-2 fan (eased): len {FAN_LEN_MM:.2f} mm, target same-layer gap "
          f"{FAN2_TARGET_GAP_MM*1000.0:.2f} um, tilt cap {FAN2_TILT_CAP_DEG:.0f} deg "
          f"(connector same-layer floor {sl_floor_um:.2f} um)")
    # Hand-off row anchors, in mm, left-to-right (== global gidx order). These are the NATIVE
    # band x's (no fan-out); the spine fans each straight from here to its connector slot.
    handoff_mm = [(x / 1000.0, y / 1000.0) for (x, y) in info["handoff"]]
    band_y_mm = info["y_via"] / 1000.0
    handoff_x = [h[0] for h in handoff_mm]

    # Place the connector: centre it in x on the bundle centre, and set its top row
    # CONN_TOP_BELOW_VIA_MM below the hand-off row so the spine runs downward without inverting.
    sig_original = _load_extended_sig()
    conn_cx = 0.5 * (sig_original[:, 0].min() + sig_original[:, 0].max())
    conn_ymax = sig_original[:, 1].max()
    connector_dx = (x_center_um / 1000.0) - conn_cx
    connector_dy = (band_y_mm - CONN_TOP_BELOW_VIA_MM) - conn_ymax
    sig = translated_signal_pads(sig_original, connector_dx, connector_dy)
    print(f"connector translate: dx={connector_dx:.4f} mm, dy={connector_dy:.4f} mm; "
          f"pads x[{sig[:,0].min():.2f},{sig[:,0].max():.2f}] y[{sig[:,1].min():.2f},{sig[:,1].max():.2f}] mm")

    spine_routes, ranks_all, _pads = build_columns_staged(
        handoff_x, x_center_um / 1000.0, band_y_mm,
        connector_dx=connector_dx, connector_dy=connector_dy)
    if len(spine_routes) != TOTAL_WIRES:
        raise RuntimeError(f"expected {TOTAL_WIRES} spine routes, got {len(spine_routes)}")

    connected = connect_handoff_to_routes(spine_routes, ranks_all, handoff_mm)
    # Verify the eased Phase-2 fan holds the target gap (pre-chamfer geometry). SINGLE LAYER: every
    # trace is on Metal1, so ALL adjacent lanes are neighbours (no cross-layer relief) -> layers=None.
    neck_bottom_mm = band_y_mm - NECK_LEN_MM
    fan_bottom_mm = neck_bottom_mm - FAN_LEN_MM
    fan_polys2, fan_owners2 = [], []
    for ti, (_gk, poly) in enumerate(connected.items()):
        for run in band_runs(poly, fan_bottom_mm, neck_bottom_mm):
            fan_polys2.append(run); fan_owners2.append(ti)
    if fan_polys2:
        gmin2_um = min_adjacent_fan_gap(fan_polys2, CONNECTOR_TW, layers=None,
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
    # the fan gap check) and it guards
    # the tear alone, against the real stroked copper. Raise PAD_APPROACH_TW_* and nothing
    # checks the widened trace against its neighbours -- inspect the DXF.
    print(f"connected routes: {len(connected)}, crossings={xings}, pad_hits={hits}")

    emit_connector_routes(doc, msp, connected, sig, band_y_mm)

    print("\n=== Phase 3: unified polyimide (jigsaw top + neck/board) ===")
    merged, holes = emit_polyimide(doc, msp, sig)

    doc.saveas(FINAL_DXF)
    print(f"wrote {FINAL_DXF}")


if __name__ == "__main__":
    main()
