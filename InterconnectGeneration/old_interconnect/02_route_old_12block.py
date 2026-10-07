"""12-block variant of route_append_to_interconnect_single.py.

Design change vs. the 8-block version:
  - Every pad on every connector gets exactly ONE wire (no doubling).
  - 4 new connector blocks are added below the existing 8 (2 below each column),
    keeping the same inter-block spacing as the existing column.
  - The wires that used to be the "second copy" on a doubled pad now land on a
    pad of one of the 4 new bottom blocks.

Totals:
  - 12 blocks total: 6 left + 6 right
  - 64 wires per block (single-wire everywhere)
  - 768 wires total (still matches the 768 interconnect endpoints)

The 4 new blocks are synthesised in memory from block 3 of each column shifted
down by the inter-block stride; the source CSV is not modified.

All internal route coordinates are in mm.
DXF coordinates are in um because KLayout reads 1 DXF unit as 1 um.
"""

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import ezdxf


# ======================================================================================
# Constants
# ======================================================================================

# Routing geometry (mm).
PADR = 0.1       # pad radius

# Interconnect side (bond band) — FIXED; the connector neck must mate these 768 endpoints.
IC_TW    = 0.002      # interconnect lead width
IC_PITCH = 0.0025     # interconnect endpoint pitch

# Connector side — ADJUSTABLE knob. CONNECTOR_TW is the connector trace width; the pitch
# tracks the width so the 0.5 µm edge-to-edge gap is preserved. Default 2 µm == the
# interconnect, so the whole routing is geometrically identical to before until the knob
# is turned up; width never drops below IC_TW (the fab minimum).
CONNECTOR_TW    = 0.0035                 # connector trace width (>= IC_TW)
GAP             = 0.0005                 # edge-to-edge spacing, constant
CONNECTOR_PITCH = CONNECTOR_TW + GAP     # connector lane pitch tracks the width

# The connector-side packing pitch drives all connector geometry (corridors, spine body,
# fan). Aliased to PITCH so the existing per-lane offset code stays unchanged.
PITCH = CONNECTOR_PITCH
TW = IC_TW        # alias (interconnect width; used by the pad-collision check)

# Transition from the bond band down to the connector body, in three stages (mm). The pitch
# fan and the width-widen are DECOUPLED so the edge-to-edge gap is never pinched: widening on
# a diagonal would shrink the perpendicular spacing below the trace width (outer lanes would
# overlap). So: (1) NECK — vertical at IC pitch/width; (2) FAN — diagonal, opens IC_PITCH ->
# CONNECTOR_PITCH at CONSTANT IC_TW width; (3) WIDEN — vertical at full CONNECTOR_PITCH, ramps
# IC_TW -> CONNECTOR_TW (gap = CONNECTOR_PITCH - width, always >= GAP). A longer FAN makes the
# fan slant gentler, keeping the gap in the fan closer to GAP (it can't quite reach GAP at the
# fan's entry corner because the locked 2.5 µm IC pitch leaves no slack for any slant).
NECK_LEN_MM   = 1.0   # vertical IC-width neck below the bond endpoints
FAN_LEN_MM    = 2.5   # diagonal pitch fan-out, constant IC_TW width
WIDEN_LEN_MM  = 0.5   # vertical width ramp at full CONNECTOR_PITCH

# Block topology.
N_BLOCKS_PER_COLUMN = 6                 # 4 existing + 2 new bottom blocks per column
N_NEW_BLOCKS_PER_COLUMN = 2
N_WIRES_PER_BLOCK = 64                  # single-wire-everywhere: one wire per pad
WIRES_PER_COLUMN = N_BLOCKS_PER_COLUMN * N_WIRES_PER_BLOCK   # 384
TOTAL_WIRES = 2 * WIRES_PER_COLUMN                            # 768

import os as _os
_HERE = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))   # the folder above old_interconnect/
_ASSETS = _os.path.join(_HERE, "assets")
_DESIGNS = _os.path.join(_HERE, "designs")
_os.makedirs(_DESIGNS, exist_ok=True)

# Interconnect source DXF and placement.
INTERCONNECT_DXF = _os.path.join(_ASSETS, "mea1k_interconnect_only.dxf")
CONNECTOR_CSV = _os.path.join(_ASSETS, "connector_signal_pads_exact_mm.csv")
INTERCONNECT_LAYERS_EMIT = ("Metal", "Metal2", "L1D0_etching1", "L3D0_etching2")
INTERCONNECT_SHIFT_UP_MM = 3.0

# Output paths and board-length target.
DEBUG_DXF = _os.path.join(_DESIGNS, "mea1k_interconnect_with_connector_12blocks_polygons_debug.dxf")
FINAL_DXF = _os.path.join(_DESIGNS, "mea1k_interconnect_with_connector_12blocks_polygons_final.dxf")
DEBUG_PNG = _os.path.join(_DESIGNS, "mea1k_interconnect_with_connector_12blocks_polygons_debug.png")

TARGET_TOTAL_LENGTH_MM = 38.0

# Polyimide outline (single unified outline; replaces the original interconnect polyimide).
# All distances in mm. See plan: top stays narrow next to the interconnect, widens to
# bottom_w_mm starting widening_offset_mm above the connector, ends bottom_margin_mm
# below the centroid of the bottom-most 64-pin block. Filleted S-curve shoulders.
POLYIMIDE = {
    "center_x_mm":        None,    # None = auto-derive from translated pad x centroid
    "top_y_mm":           15.75,   # top edge y (default matches existing interconnect top)
    "top_w_mm":           4.56,     # width at the interconnect end
    "bottom_w_mm":        17.8,    # width at the connector (= 178 mm @10:1 from 8x64_2026-05-31.pdf)
    "widening_offset_mm": 6,     # the "4–5 mm before the connector" knob
    "fillet_r_mm":        3.0,     # shoulder fillet radius (need 2r <= bottom_w - top_w)
    "bottom_margin_mm":   1.5,     # distance below bottom-block centroid
    "arc_segments":       16,      # vertices per 90° fillet arc
}

# Board outline (Simon's file). The board section of the polyimide matches this rounded
# rectangle, and the 4 corner cut-circles carve concave arc notches into the side walls
# (they straddle the edges) — i.e. they remove polyimide, not add ears. The board is
# positioned by board_placement(). All distances in mm.
BOARD_OUTLINE_DXF = _os.path.join(_ASSETS, "Board_12x_outline_simon.dxf")
BOARD_CORNER_R = 0.8       # bottom-corner fillet radius (from Simon's corner splines)
BOARD_LEFT_MARGIN_MM = 4.5   # left side of polyimide to the reference-pad right edge
BOARD_BOTTOM_GAP_MM  = 1.5   # lowest-block centroid above the polyimide bottom edge


# ======================================================================================
# Signal-pad extension: synthesise the 4 new bottom blocks
# ======================================================================================

_SIG_CACHE = None


def extend_sig_with_new_blocks(sig, n_new_per_col=N_NEW_BLOCKS_PER_COLUMN):
    """Append synthetic Molex 227044 pads for N new bottom blocks per column.

    Block 3 of each existing column (the bottommost existing block) is used as the
    geometric template. New blocks are placed below it at the same inter-block
    stride that already exists between consecutive blocks of the column.
    """
    sig = np.asarray(sig, dtype=float)
    xmid = (sig[:, 0].min() + sig[:, 0].max()) / 2

    new_rows = []
    for side_mask in (sig[:, 0] < xmid, sig[:, 0] >= xmid):
        col_sig = sig[side_mask]
        ys = np.sort(np.unique(np.round(col_sig[:, 1], 2)))

        # Existing layout: 4 blocks × 4 rows = 16 unique y rows per column.
        # ys[0..3] = bottommost block (block 3); ys[12..15] = topmost block (block 0).
        if len(ys) < 8:
            raise RuntimeError(
                f"need at least 2 blocks (8 rows) per column to compute stride; got {len(ys)}"
            )

        inter_block_stride = float(ys[4] - ys[0])      # 4 rows apart = one block down

        # Pads of block 3 for this column = pads at the 4 lowest row y-values.
        block3_y_band = (ys[0] - 0.01, ys[3] + 0.01)
        block3_pads = col_sig[
            (col_sig[:, 1] >= block3_y_band[0]) & (col_sig[:, 1] <= block3_y_band[1])
        ]

        if len(block3_pads) != 64:
            raise RuntimeError(
                f"expected 64 pads in block 3 template, got {len(block3_pads)}"
            )

        for new_block_idx in range(n_new_per_col):
            dy = (new_block_idx + 1) * inter_block_stride       # shift down
            shifted = block3_pads.copy()
            shifted[:, 1] -= dy
            new_rows.append(shifted)

    extended = np.vstack([sig] + new_rows)
    return extended


def _load_extended_sig():
    """Load + extend the signal pads once, cache the result."""
    global _SIG_CACHE
    if _SIG_CACHE is None:
        sig = np.loadtxt(CONNECTOR_CSV, delimiter=",", skiprows=1)
        _SIG_CACHE = extend_sig_with_new_blocks(sig)
    return _SIG_CACHE


# ======================================================================================
# Connector pad map (generalised to N_BLOCKS_PER_COLUMN)
# ======================================================================================

def load_connector(side="left", block=0):
    """Return {CKT: (x,y)} for one connector. block ∈ [0, N_BLOCKS_PER_COLUMN-1]."""
    sig = _load_extended_sig()
    ys = np.sort(np.unique(np.round(sig[:, 1], 2)))
    xmid = (sig[:, 0].min() + sig[:, 0].max()) / 2

    n = len(ys)                                          # 4 * N_BLOCKS_PER_COLUMN
    start = n - 4 * (block + 1)
    end = n - 4 * block
    rows_top_to_bottom = ys[start:end][::-1]

    grid = []
    for y in rows_top_to_bottom:
        r = sig[np.abs(sig[:, 1] - y) < 0.05]
        r = r[r[:, 0] < xmid] if side == "left" else r[r[:, 0] >= xmid]
        grid.append([tuple(p) for p in r[np.argsort(r[:, 0])]])

    if side == "left":
        ckt = lambda r, j: [34 + 2 * j, 33 + 2 * j, 2 + 2 * j, 1 + 2 * j][r]
    else:
        # Supervisor's 180-deg rotation for right column: CKT 1 at top-right.
        ckt = lambda r, j: [1 + 2 * (15 - j), 2 + 2 * (15 - j), 33 + 2 * (15 - j), 34 + 2 * (15 - j)][r]

    return {ckt(r, j): grid[r][j] for r in range(4) for j in range(16)}


# ======================================================================================
# Geometry primitives: segment-segment + segment-pad intersection
# ======================================================================================

def _seg_int(a, b, c, d):
    def cc(p, q, r): return (r[1]-p[1])*(q[0]-p[0]) - (q[1]-p[1])*(r[0]-p[0])
    return (cc(c, d, a) > 0) != (cc(c, d, b) > 0) and (cc(a, b, c) > 0) != (cc(a, b, d) > 0)


def _seg_pad(a, b, pad):
    (x1, y1), (x2, y2) = a, b
    cx, cy = pad
    dx, dy = x2 - x1, y2 - y1
    L2 = dx*dx + dy*dy
    t = 0 if L2 == 0 else max(0, min(1, ((cx-x1)*dx + (cy-y1)*dy) / L2))
    qx, qy = x1 + t*dx, y1 + t*dy
    return (qx-cx)**2 + (qy-cy)**2 < (PADR + max(IC_TW, CONNECTOR_TW)/2)**2


def _check_fast(seglist, pads):
    """Crossing + pad-collision check with a bbox sweep."""
    items = sorted((min(a[0], b[0]), max(a[0], b[0]), min(a[1], b[1]), max(a[1], b[1]), i, a, b)
                   for i, a, b in seglist)
    xings = 0
    for k in range(len(items)):
        x0i, x1i, y0i, y1i, idi, a, b = items[k]
        for m in range(k + 1, len(items)):
            it = items[m]
            if it[0] > x1i:
                break
            if idi == it[4] or y0i > it[3] or it[2] > y1i:
                continue
            c, d = it[5], it[6]
            if a in (c, d) or b in (c, d):
                continue
            if _seg_int(a, b, c, d):
                xings += 1
    hits = 0
    own = 2.5e-5
    for x0, x1, y0, y1, i, a, b in items:
        for pad in pads:
            if not (x0 - PADR <= pad[0] <= x1 + PADR and y0 - PADR <= pad[1] <= y1 + PADR):
                continue
            if (a[0]-pad[0])**2 + (a[1]-pad[1])**2 < own or (b[0]-pad[0])**2 + (b[1]-pad[1])**2 < own:
                continue
            if _seg_pad(a, b, pad):
                hits += 1
    return xings, hits


# ======================================================================================
# Single-wire-everywhere routing
# ======================================================================================

def route_single_left(chan):
    """Route one left-column connector: all 64 pads, one wire each, entering from the left.

    Top pair (rows y1, y2):     32 lanes in the ABOVE-row-1 corridor.
    Bottom pair (rows y3, y4):  32 lanes in the row-2-row-3 corridor.
    """
    rows = sorted({round(chan[c][1], 3) for c in chan})
    if len(rows) < 4:
        return {}

    y4, y3, y2, y1 = rows[0], rows[1], rows[2], rows[3]

    xlo = min(p[0] for p in chan.values())
    x_in_entry = xlo - 0.2

    above1_base = y1 + 0.12
    corridor_base = y3 + 0.12

    routes = {}

    top = sorted(
        (c for c in chan if round(chan[c][1], 3) in (y1, y2)),
        key=lambda c: chan[c][0],
    )
    for k, c in enumerate(top):
        px, py = chan[c]
        lane = above1_base + k * PITCH
        routes[c] = [(x_in_entry, lane), (px, lane), (px, py)]

    bottom = sorted(
        (c for c in chan if round(chan[c][1], 3) in (y3, y4)),
        key=lambda c: chan[c][0],
    )
    for k, c in enumerate(bottom):
        px, py = chan[c]
        lane = corridor_base + k * PITCH
        routes[c] = [(x_in_entry, lane), (px, lane), (px, py)]

    return routes


def route_single_right(chan):
    """Route one right-column connector: all 64 pads, one wire each, entering from the right.

    Top pair (rows y1, y2):     32 lanes in the row-2-row-3 corridor (just below y2).
    Bottom pair (rows y3, y4):  32 lanes BELOW row 4.
    """
    rows = sorted({round(chan[c][1], 3) for c in chan})
    if len(rows) < 4:
        return {}

    y4, y3, y2, y1 = rows[0], rows[1], rows[2], rows[3]

    xlow = min(p[0] for p in chan.values())
    x_in_entry = xlow - 0.2

    corridor_top = y2 - 0.13
    below4_top = y4 - 0.15

    routes = {}

    top = sorted(
        (c for c in chan if round(chan[c][1], 3) in (y1, y2)),
        key=lambda c: chan[c][0],
    )
    n_top = len(top)
    for k, c in enumerate(top):
        px, py = chan[c]
        lane = corridor_top - (n_top - 1 - k) * PITCH
        routes[c] = [(x_in_entry, lane), (px, lane), (px, py)]

    bottom = sorted(
        (c for c in chan if round(chan[c][1], 3) in (y3, y4)),
        key=lambda c: chan[c][0],
    )
    n_bot = len(bottom)
    for k, c in enumerate(bottom):
        px, py = chan[c]
        lane = below4_top - (n_bot - 1 - k) * PITCH
        routes[c] = [(x_in_entry, lane), (px, lane), (px, py)]

    return routes


def conn_rank_mixed(routes, descending=False):
    """Assign local rank 0..(N_WIRES_PER_BLOCK-1) by route lane y-position."""
    lanes = {k: poly[0][1] for k, poly in routes.items()}
    order = sorted(lanes, key=lambda k: lanes[k], reverse=descending)
    return {k: i for i, k in enumerate(order)}


# ======================================================================================
# Spine wrap routing (left + right column variants)
# ======================================================================================

def add_spine(routes, spine_x, band_y, y_below_base, x_up_base, rank, x_center,
              rank_offset=0, global_base=0):
    """Prepend the IN long-haul to each left-column trace.

    From the bond band the trace runs: NECK (vertical at IC_PITCH/IC_TW, mates the endpoint)
    → FAN (diagonal, opens IC_PITCH → CONNECTOR_PITCH at constant IC_TW width) → WIDEN
    (vertical at xs_body, the width ramps to CONNECTOR_TW here) → body: down the spine → below
    the block → up the left → into the corridor. With rank_offset = b * N_WIRES_PER_BLOCK,
    this connector's 64 lanes occupy a unique slice of the shared spine.

    The fan is symmetric about the GLOBAL bundle center x_center over all TOTAL_WIRES lanes
    (global index = global_base + gidx), so every adjacent pair — including the seam pair
    shared with the right column — opens from IC_PITCH up to CONNECTOR_PITCH. Keeping the
    width fixed through the diagonal fan and only widening on the vertical WIDEN segment is
    what keeps the edge-to-edge gap from being pinched by the fan slant.
    """
    neck_bottom_y = band_y - NECK_LEN_MM
    fan_bottom_y = neck_bottom_y - FAN_LEN_MM
    widen_bottom_y = fan_bottom_y - WIDEN_LEN_MM
    mid = (TOTAL_WIRES - 1) / 2.0
    out = {}
    for c, poly in routes.items():
        lane = poly[0][1]; px = poly[1][0]; py = poly[2][1]
        tail = poly[3:]
        k = rank[c]
        gidx = k + rank_offset
        xs_top = spine_x + gidx * IC_PITCH                                  # mates endpoint
        xs_body = x_center + (global_base + gidx - mid) * CONNECTOR_PITCH    # global fan
        yb = y_below_base - k * PITCH
        xu = x_up_base - k * PITCH
        out[c] = [(xs_top, neck_bottom_y), (xs_body, fan_bottom_y),
                  (xs_body, widen_bottom_y), (xs_body, yb), (xu, yb), (xu, lane),
                  (px, lane), (px, py)] + tail
    return out

def add_spine_right(routes, spine_x, band_y, y_below_base, x_up_base, rank, x_center,
                    rank_offset=0, global_base=0):
    """Prepend the IN long-haul to each right-column trace.

    Same NECK → FAN → WIDEN → body as add_spine, and the same GLOBAL-center fan (global index
    = global_base + gidx, with global_base = WIRES_PER_COLUMN for the right column), so the
    two columns spread symmetrically about x_center and the seam pair opens to
    CONNECTOR_PITCH instead of overlapping. Each trace: down the central spine → below the
    block → up the right → into the corridor.
    """
    neck_bottom_y = band_y - NECK_LEN_MM
    fan_bottom_y = neck_bottom_y - FAN_LEN_MM
    widen_bottom_y = fan_bottom_y - WIDEN_LEN_MM
    mid = (TOTAL_WIRES - 1) / 2.0
    out = {}
    for c, poly in routes.items():
        lane = poly[0][1]; px = poly[1][0]; py = poly[2][1]
        tail = poly[3:]
        k = rank[c]
        gidx = k + rank_offset
        xs_top = spine_x + gidx * IC_PITCH                                  # mates endpoint
        xs_body = x_center + (global_base + gidx - mid) * CONNECTOR_PITCH    # global fan
        yb = y_below_base + k * PITCH
        xu = x_up_base - k * PITCH
        out[c] = [(xs_top, neck_bottom_y), (xs_body, fan_bottom_y),
                  (xs_body, widen_bottom_y), (xs_body, yb), (xu, yb), (xu, lane),
                  (px, lane), (px, py)] + tail
    return out

# def add_spine_right(routes, spine_x, y_top, y_below_base, x_up_base, rank, rank_offset=0):
#     """Right-column fanout: drop from spine to a unique fan lane → slide outward →
#     drop into the local route lane → enter the connector from the right.
#     """
#     out = {}
#     right_total = WIRES_PER_COLUMN

#     first_key = min(rank, key=lambda kk: rank[kk])
#     x_entry_base = routes[first_key][0][0]

#     for c, poly in routes.items():
#         x_entry, lane = poly[0]
#         tail = poly[1:]

#         k = rank[c]
#         global_rank = k + rank_offset

#         xs = spine_x + global_rank * PITCH
#         y_fan = y_top - (right_total - 1 - global_rank) * PITCH
#         y_join = lane
#         x_out = x_entry_base + global_rank * PITCH

#         out[c] = [
#             (xs, y_fan),
#             (x_out, y_fan),
#             (x_out, y_join),
#             (x_entry, y_join),
#             (x_entry, lane),
#         ] + tail

#     return out


# ======================================================================================
# Interconnect DXF parsing
# ======================================================================================

def entity_xy_points(e):
    """Return [(x, y), ...] for LWPOLYLINE/POLYLINE entities."""
    if e.dxftype() == "LWPOLYLINE":
        return [(float(p[0]), float(p[1])) for p in e.get_points()]
    if e.dxftype() == "POLYLINE":
        return [
            (float(v.dxf.location.x), float(v.dxf.location.y))
            for v in e.vertices
        ]
    return []


def interconnect_bbox_mm(path=INTERCONNECT_DXF):
    """Return (xmin, xmax, ymin, ymax) of emitted interconnect layers in millimetres."""
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()

    xs = []
    ys = []
    for e in msp:
        if e.dxftype() not in ("LWPOLYLINE", "POLYLINE"):
            continue
        if e.dxf.layer not in INTERCONNECT_LAYERS_EMIT:
            continue
        pts = entity_xy_points(e)
        if not pts:
            continue
        xs.extend([p[0] for p in pts])
        ys.extend([p[1] for p in pts])

    if not xs or not ys:
        raise RuntimeError(f"No interconnect geometry found in emitted layers of {path}")

    return (
        min(xs) / 1000.0,
        max(xs) / 1000.0,
        min(ys) / 1000.0,
        max(ys) / 1000.0,
    )


def interconnect_placement_for_sig(sig):
    """Return (dx_mm, dy_mm) used to place the interconnect in the output DXF."""
    board_xc = (sig[:, 0].min() + sig[:, 0].max()) / 2
    ixmin, ixmax, _iymin, _iymax = interconnect_bbox_mm(INTERCONNECT_DXF)
    interconnect_xc = 0.5 * (ixmin + ixmax)

    dx_mm = board_xc - interconnect_xc
    dy_mm = INTERCONNECT_SHIFT_UP_MM

    return dx_mm, dy_mm


def extract_interconnect_endpoints(
    path=INTERCONNECT_DXF,
    expected=TOTAL_WIRES,
    y_bottom_cluster_max=10500,
    snap=0.25,
):
    """Extract exactly `expected` lower endpoints from the real interconnect."""
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()

    candidates = []

    for e in msp:
        if e.dxftype() not in ("LWPOLYLINE", "POLYLINE"):
            continue
        layer = e.dxf.layer
        if layer not in ("Metal", "Metal2"):
            continue
        pts = entity_xy_points(e)
        if len(pts) < 2:
            continue

        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        xmin = min(xs); xmax = max(xs)
        ymin = min(ys); ymax = max(ys)

        if ymin > y_bottom_cluster_max:
            continue

        x_at_ymin = [x for x, y in pts if abs(y - ymin) <= 0.1]
        bottom_x = float(np.mean(x_at_ymin)) if x_at_ymin else float((xmin + xmax) / 2)

        candidates.append({
            "layer": layer,
            "bottom_x": bottom_x,
            "bottom_y": ymin,
            "xmin": xmin, "xmax": xmax,
            "ymin": ymin, "ymax": ymax,
            "width": xmax - xmin, "height": ymax - ymin,
        })

    endpoint_groups = {}
    for r in candidates:
        key = (round(r["bottom_x"] / snap), round(r["bottom_y"] / snap))
        endpoint_groups.setdefault(key, []).append(r)

    deduped = []
    for key in sorted(endpoint_groups, key=lambda k: (k[0], k[1])):
        group = endpoint_groups[key]
        chosen = sorted(group, key=lambda r: (r["bottom_y"], -r["height"], r["layer"]))[0]
        deduped.append(chosen)

    selected = sorted(deduped, key=lambda r: (r["bottom_y"], r["bottom_x"]))[:expected]
    selected = sorted(selected, key=lambda r: (r["bottom_x"], r["bottom_y"]))

    print(
        f"interconnect endpoint extraction: raw_bottom_cluster={len(candidates)}, "
        f"deduped_physical={len(deduped)}, selected={len(selected)}, expected={expected}"
    )

    if selected:
        print(
            f"selected endpoint y range: "
            f"[{min(r['bottom_y'] for r in selected):.1f}, "
            f"{max(r['bottom_y'] for r in selected):.1f}] um"
        )
        print(
            f"selected endpoint x range: "
            f"[{min(r['bottom_x'] for r in selected):.1f}, "
            f"{max(r['bottom_x'] for r in selected):.1f}] um"
        )

    if len(selected) != expected:
        xs = sorted(r["bottom_x"] for r in deduped)
        gaps = [(xs[i + 1] - xs[i], xs[i], xs[i + 1]) for i in range(len(xs) - 1)]
        largest_gaps = sorted(gaps, reverse=True)[:10]
        print("largest bottom-cluster x gaps:")
        for gap, x0, x1 in largest_gaps:
            print(f"  gap={gap:.3f} um between x={x0:.3f} and x={x1:.3f}")

        if len(selected) == expected - 1 and largest_gaps:
            gap, x0, x1 = largest_gaps[0]
            expected_pitch_um = IC_PITCH * 1000.0
            xs_selected = sorted(r["bottom_x"] for r in selected)
            median_y = float(np.median([r["bottom_y"] for r in selected]))
            normal_pitch_tol = 0.20

            if 1.5 * expected_pitch_um <= gap <= 2.5 * expected_pitch_um:
                missing_x = 0.5 * (x0 + x1)
                reason = f"interior largest gap={gap:.3f} um"
            elif abs(gap - expected_pitch_um) <= normal_pitch_tol:
                missing_x = xs_selected[-1] + expected_pitch_um
                reason = f"right-edge completion; all gaps ~= {expected_pitch_um:.3f} um"
            else:
                raise RuntimeError(
                    f"Expected {expected} endpoints, got {len(selected)}. "
                    f"Largest gap={gap:.3f} um doesn't match a fixable pattern."
                )

            synthetic = {
                "layer": "synthetic",
                "bottom_x": missing_x, "bottom_y": median_y,
                "xmin": missing_x, "xmax": missing_x,
                "ymin": median_y, "ymax": median_y,
                "width": 0.0, "height": 0.0,
            }
            selected.append(synthetic)
            selected = sorted(selected, key=lambda r: (r["bottom_x"], r["bottom_y"]))
            print(
                f"synthesized missing endpoint at x={missing_x:.3f} um, "
                f"y={median_y:.3f} um ({reason})"
            )

        if len(selected) != expected:
            raise RuntimeError(
                f"Expected {expected} endpoints, got {len(selected)}."
            )

    return selected


# ======================================================================================
# Interconnect <-> route joining
# ======================================================================================

def ordered_routes_by_spine(routes_all, ranks_all):
    """Sort routes deterministically by (side, block, local_rank)."""
    return sorted(
        routes_all.items(),
        key=lambda item: (
            0 if ranks_all[item[0]][0] == "left" else 1,
            ranks_all[item[0]][1],
            ranks_all[item[0]][2],
        ),
    )


def connect_interconnect_to_routes(routes_all, ranks_all, interconnect_rows):
    """Prepend each route with the matching interconnect bottom endpoint."""
    ordered_routes = ordered_routes_by_spine(routes_all, ranks_all)

    if len(ordered_routes) != len(interconnect_rows):
        raise RuntimeError(
            f"routes={len(ordered_routes)}, interconnect endpoints={len(interconnect_rows)}"
        )

    connected = {}
    max_dx = 0.0
    max_dy = 0.0

    for (gkey, poly), src in zip(ordered_routes, interconnect_rows):
        sx = src["bottom_x"] / 1000.0
        sy = src["bottom_y"] / 1000.0
        rx, ry = poly[0]

        dx = abs(sx - rx); dy = abs(sy - ry)
        max_dx = max(max_dx, dx); max_dy = max(max_dy, dy)

        if dx > 1e-6:
            raise RuntimeError(
                f"endpoint x={sx:.6f} mm not aligned to route start x={rx:.6f} mm for {gkey}"
            )

        connected[gkey] = [(sx, sy)] + poly

    print(
        f"direct interconnect connection alignment: "
        f"max_dx={max_dx * 1000:.4f} um, max_dy={max_dy * 1000:.4f} um"
    )

    return connected


# ======================================================================================
# Connector translation helpers
# ======================================================================================

def translate_chan(chan, dx=0.0, dy=0.0):
    if abs(dx) < 1e-15 and abs(dy) < 1e-15:
        return chan
    return {c: (x + dx, y + dy) for c, (x, y) in chan.items()}


def translated_signal_pads(sig_original, dx=0.0, dy=0.0):
    sig = sig_original.copy()
    sig[:, 0] += dx
    sig[:, 1] += dy
    return sig


def min_route_y(routes_all):
    return min(y for poly in routes_all.values() for _x, y in poly)


# ======================================================================================
# Routing builder
# ======================================================================================

def build_columns_staged(
    spine_x_left,
    y_top_left,
    spine_x_right,
    y_top_right,
    connector_dx=0.0,
    connector_dy=0.0,
):
    """Build all 12 connector blocks after translating the connector array."""
    local_routes_all = {}
    spine_routes_all = {}
    ranks_all = {}
    all_pads = []

    # Center of the full 768-lane bundle (the bond band is contiguous at IC_PITCH starting
    # at spine_x_left). Both columns fan symmetrically about this so the seam opens up too.
    x_center = spine_x_left + (TOTAL_WIRES - 1) / 2.0 * IC_PITCH

    # Left column: N_BLOCKS_PER_COLUMN blocks.
    for b in range(N_BLOCKS_PER_COLUMN):
        chan = load_connector("left", b)
        chan = translate_chan(chan, connector_dx, connector_dy)
        all_pads += list(chan.values())

        routes_local = route_single_left(chan)
        rank = conn_rank_mixed(routes_local)

        for k, poly in routes_local.items():
            gkey = ("left", b, k)
            local_routes_all[gkey] = poly
            ranks_all[gkey] = ("left", b, rank[k])

        ymin = min(p[1] for p in chan.values())
        xmin = min(p[0] for p in chan.values())

        routes_spine = add_spine(
            routes_local,
            spine_x_left,
            y_top_left,
            ymin - 0.2,
            xmin - 0.5,
            rank,
            x_center,
            rank_offset=b * N_WIRES_PER_BLOCK,
            global_base=0,
        )

        for k, poly in routes_spine.items():
            gkey = ("left", b, k)
            spine_routes_all[gkey] = poly

    # Right column: N_BLOCKS_PER_COLUMN blocks.
    for b in range(N_BLOCKS_PER_COLUMN):
        chan = load_connector("right", N_BLOCKS_PER_COLUMN - 1 - b)
        chan = translate_chan(chan, connector_dx, connector_dy)
        all_pads += list(chan.values())

        routes_local = route_single_right(chan)
        rank = conn_rank_mixed(routes_local, descending=True)

        for k, poly in routes_local.items():
            gkey = ("right", b, k)
            local_routes_all[gkey] = poly
            ranks_all[gkey] = ("right", b, rank[k])

        ymin = min(p[1] for p in chan.values())
        xmax = max(p[0] for p in chan.values())
        x_up_base = xmax + 0.5

        routes_spine = add_spine_right(
            routes_local,
            spine_x_right,
            y_top_right,
            ymin - 0.28 - 64 * PITCH,
            x_up_base,
            rank,
            x_center,
            rank_offset=b * N_WIRES_PER_BLOCK,
            global_base=WIRES_PER_COLUMN,
        )

        for k, poly in routes_spine.items():
            gkey = ("right", b, k)
            spine_routes_all[gkey] = poly

    return {
        "local": (local_routes_all, ranks_all, all_pads),
        "spine": (spine_routes_all, ranks_all, all_pads),
    }


# ======================================================================================
# Output helpers
# ======================================================================================

def to_seglist(routes_all):
    out = []
    for i, (_, poly) in enumerate(routes_all.items()):
        for a, b in zip(poly, poly[1:]):
            out.append((i, a, b))
    return out


def write_png(routes_all, sig, xings, hits, out_path):
    """Visual check PNG. All wires are single-color since there is no doubling."""
    fig, ax = plt.subplots(figsize=(14, 16))

    for x, y in sig:
        ax.add_patch(
            plt.Circle(
                (x, y), PADR,
                color="0.92", ec="0.6", lw=0.25, zorder=3,
            )
        )

    for _gkey, poly in routes_all.items():
        ax.plot(*zip(*poly), color="#3366cc", lw=0.2, zorder=2)

    ok = xings == 0 and hits == 0

    ax.set_aspect("equal")
    ax.set_xlabel("x (mm)")
    ax.set_ylabel("y (mm)")
    ax.set_title(
        f"12-block connector columns ({len(routes_all)} traces)\n"
        f"crossings={xings}, pad_hits={hits} " + ("OK" if ok else "FAIL"),
        color=("#1f8a1f" if ok else "#b00"),
        weight="bold",
    )

    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def ensure_layer(doc, layer_name, color):
    if layer_name not in doc.layers:
        doc.layers.new(layer_name, dxfattribs={"color": color})


def _bottom_block_centroid_y_mm(sig):
    """Mean y of all pads in the bottom-most row band (the last 4 rows = one block)."""
    sig = np.asarray(sig)
    ys = np.sort(np.unique(np.round(sig[:, 1], 2)))
    band_lo, band_hi = ys[0] - 0.01, ys[3] + 0.01
    bottom_block = sig[(sig[:, 1] >= band_lo) & (sig[:, 1] <= band_hi)]
    return float(bottom_block[:, 1].mean())


def board_outline_dims(path=BOARD_OUTLINE_DXF):
    """Return (width_mm, height_mm) of Simon's board outline bbox (lines + spline ctrl)."""
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    bx, by = [], []
    for e in msp:
        if e.dxftype() == "LINE":
            bx += [e.dxf.start.x, e.dxf.end.x]
            by += [e.dxf.start.y, e.dxf.end.y]
        elif e.dxftype() == "SPLINE":
            for p in e.control_points:
                bx.append(p[0]); by.append(p[1])
    if not bx:
        raise RuntimeError(f"no outline boundary (lines/splines) in {path}")
    return max(bx) - min(bx), max(by) - min(by)


def board_placement(sig, path=BOARD_OUTLINE_DXF):
    """Board center (cx, board_cy) in mm from the two placement constraints.

    Single source of truth for both the polyimide outline and the ear/hole annuli:
      - left side of the board is BOARD_LEFT_MARGIN_MM left of the reference pad's
        right edge. The reference pad is the 8th pad (by x) of the 3rd row from the
        top of a left-side connector block (selected explicitly so it is robust to
        the quad-row x-stagger; all left blocks share these x-columns).
      - lowest-block centroid is BOARD_BOTTOM_GAP_MM above the board bottom edge.
    """
    sig = np.asarray(sig)
    _bw, bh = board_outline_dims(path)

    xmid = (sig[:, 0].min() + sig[:, 0].max()) / 2
    ys = np.sort(np.unique(np.round(sig[:, 1], 2)))
    third_row_y = ys[-4:][::-1][2]                        # 3rd row from top of top block
    row = sig[(np.abs(sig[:, 1] - third_row_y) < 0.05) & (sig[:, 0] < xmid)]
    ref_x = row[np.argsort(row[:, 0])][7, 0]              # 8th pad by x

    left_wall_x = (ref_x + PADR) - BOARD_LEFT_MARGIN_MM
    cx = left_wall_x + _bw / 2.0
    bot_y = _bottom_block_centroid_y_mm(sig) - BOARD_BOTTOM_GAP_MM
    board_cy = bot_y + bh / 2.0
    return cx, board_cy


def _wall_notch_arc(x_wall, ncx, ncy, Rc, n=24):
    """Points (mm) of the concave arc that the cut-circle (ncx, ncy, Rc) carves into a
    vertical board wall at x=x_wall. Ordered so the right wall (cut center to the right)
    reads top→bottom and the left wall (center to the left) reads bottom→top, matching
    the outline traversal. Returns [] if the circle does not reach the wall."""
    d = x_wall - ncx
    s = Rc * Rc - d * d
    if s <= 1e-12:
        return []
    alpha = np.arccos(min(1.0, abs(d) / Rc))
    extreme = 0.0 if d > 0 else np.pi          # board-side extreme direction from center
    th = np.linspace(extreme - alpha, extreme + alpha, n)
    return [(ncx + Rc * np.cos(t), ncy + Rc * np.sin(t)) for t in th]


def build_polyimide_outline(sig, params=POLYIMIDE):
    """Build the polyimide outline (closed polyline, in µm).

    A narrow interconnect neck on top flares onto the top edge of the board; the board
    itself is Simon's full rounded rectangle (rounded top AND bottom corners), centered
    on the connector pad-array bbox. Profile, top to bottom, centered on cx:
      - top_w-wide neck at top_y, straight down
      - fillet_r flare from the neck wall onto the board top edge
      - flat top edge out to the rounded top corner (BOARD_CORNER_R)
      - right wall down, rounded bottom corner, bottom edge, then mirror back up.
    The 4 corner cut-circles (Simon's outline) carve concave arc notches into the left
    and right walls (they straddle the edges, ~0.25 mm deep), so they remove polyimide
    rather than adding ears.
    Board width/height come from Simon's outline (board_outline_dims); the neck width
    (top_w), neck top (top_y) and flare radius (fillet_r) come from POLYIMIDE.
    Returns a list of (x_um, y_um); the caller closes it via close=True.
    """
    sig = np.asarray(sig)

    bw, bh = board_outline_dims()
    board_cx, board_cy = board_placement(sig)

    cx = params["center_x_mm"] if params["center_x_mm"] is not None else board_cx
    top_y = params["top_y_mm"]
    top_w = params["top_w_mm"]
    r     = params["fillet_r_mm"]
    rb    = BOARD_CORNER_R
    n_arc = params["arc_segments"]

    half_t = top_w / 2.0
    half_b = bw / 2.0
    board_top_y = board_cy + bh / 2.0
    bot_y       = board_cy - bh / 2.0

    if half_t + r >= half_b - rb:
        raise ValueError(
            f"neck flare (half_t+fillet_r={half_t + r:.3f}) overruns the top edge "
            f"(half_b-corner_r={half_b - rb:.3f}); reduce fillet_r or top_w"
        )
    if board_top_y + r >= top_y:
        raise ValueError(
            f"neck too short for the flare fillet (board_top_y={board_top_y:.3f}, "
            f"top_y={top_y}, fillet_r={r})"
        )

    def arc(center, t0, t1, n, rad):
        """Arc points from t0 to t1 (exclusive of t0, inclusive of t1)."""
        cxc, cyc = center
        ts = np.linspace(t0, t1, n + 1)[1:]
        return [(cxc + rad * np.cos(t), cyc + rad * np.sin(t)) for t in ts]

    # Corner cut-circles to carve into the side walls (right wall top→bottom, left
    # wall bottom→top), from Simon's outline placed with the board.
    cut_centers, cut_r, _r_inner = load_board_corner_features(sig)
    right_cuts = sorted((c for c in cut_centers if c[0] > cx), key=lambda c: -c[1])
    left_cuts = sorted((c for c in cut_centers if c[0] < cx), key=lambda c: c[1])

    PI = np.pi
    pts = []

    # Neck top edge (left → right).
    pts.append((cx - half_t, top_y))
    pts.append((cx + half_t, top_y))

    # Right neck wall down to the flare fillet, then flare onto the board top edge.
    pts.append((cx + half_t, board_top_y + r))
    pts += arc((cx + half_t + r, board_top_y + r), PI, 1.5 * PI, n_arc, r)

    # Board top edge to the top-right rounded corner, then down the right wall.
    pts.append((cx + half_b - rb, board_top_y))
    pts += arc((cx + half_b - rb, board_top_y - rb), 0.5 * PI, 0.0, n_arc, rb)

    # Right wall (top→bottom) with cut-circle notches → bottom-right corner →
    # bottom edge → bottom-left corner.
    for ncx, ncy in right_cuts:
        pts += _wall_notch_arc(cx + half_b, ncx, ncy, cut_r)
    pts.append((cx + half_b, bot_y + rb))
    pts += arc((cx + half_b - rb, bot_y + rb), 0.0, -0.5 * PI, n_arc, rb)
    pts.append((cx - half_b + rb, bot_y))
    pts += arc((cx - half_b + rb, bot_y + rb), -0.5 * PI, -PI, n_arc, rb)

    # Left wall (bottom→top) with cut-circle notches → top-left corner → top edge.
    for ncx, ncy in left_cuts:
        pts += _wall_notch_arc(cx - half_b, ncx, ncy, cut_r)
    pts.append((cx - half_b, board_top_y - rb))
    pts += arc((cx - half_b + rb, board_top_y - rb), PI, 0.5 * PI, n_arc, rb)

    # Left flare fillet from the board top edge back up to the neck wall.
    pts.append((cx - half_t - r, board_top_y))
    pts += arc((cx - half_t - r, board_top_y + r), 1.5 * PI, 2.0 * PI, n_arc, r)

    # Left neck wall up: implied by close=True back to pts[0] (vertical, same x).

    UM = 1000.0
    return [(x * UM, y * UM) for x, y in pts]


def load_board_corner_features(sig, path=BOARD_OUTLINE_DXF):
    """Read Simon's board outline and return the 4 corner cut-circles, placed with the board.

    Returns (corners, r_outer, r_inner) where corners is a list of (cx, cy) in mm.
    Each corner has a concentric pair r_outer (~1.225) / r_inner (~0.6); only r_outer
    reaches the board edge, so it is the circle that carves the concave arc notch.
    The whole board is translated so its outline bbox center sits on the board center
    from board_placement(), so the cut arcs track the repositioned polyimide outline.
    """
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()

    circles = [
        (float(e.dxf.center.x), float(e.dxf.center.y), float(e.dxf.radius))
        for e in msp if e.dxftype() == "CIRCLE"
    ]
    radii = sorted({round(r, 3) for _x, _y, r in circles})
    if len(radii) < 2:
        raise RuntimeError(f"expected ear + hole radii in {path}, got radii={radii}")
    r_inner, r_outer = radii[0], radii[-1]

    # Outline bbox from the boundary entities (lines + spline control points).
    bx, by = [], []
    for e in msp:
        if e.dxftype() == "LINE":
            bx += [e.dxf.start.x, e.dxf.end.x]
            by += [e.dxf.start.y, e.dxf.end.y]
        elif e.dxftype() == "SPLINE":
            for p in e.control_points:
                bx.append(p[0]); by.append(p[1])
    simon_cx = 0.5 * (min(bx) + max(bx))
    simon_cy = 0.5 * (min(by) + max(by))

    board_cx, board_cy = board_placement(sig, path)
    tx, ty = board_cx - simon_cx, board_cy - simon_cy

    # The ear (outer) circle centers define the corners; holes are concentric.
    corners = [
        (x + tx, y + ty) for x, y, r in circles if abs(r - r_outer) < 1e-3
    ]
    return corners, r_outer, r_inner


def stroke_centerline_to_polygon(centerline, widths):
    """Turn a centerline (list of (x, y)) with a per-vertex full trace width into a
    CLOSED polygon outline (list of (x, y)), using miter joins.

    Used so connector routes are emitted as polygons (mask requirement) instead of
    width-bearing open polylines (paths). Returns None for degenerate input.
    """
    pts = np.asarray(centerline, dtype=float)
    w = np.asarray(widths, dtype=float)
    # Drop consecutive duplicate vertices (zero-length segments break the normals).
    keep = [0]
    for i in range(1, len(pts)):
        if not np.allclose(pts[i], pts[keep[-1]]):
            keep.append(i)
    pts, w = pts[keep], w[keep]
    n = len(pts)
    if n < 2:
        return None
    seg = pts[1:] - pts[:-1]
    d = seg / np.hypot(seg[:, 0], seg[:, 1])[:, None]    # unit segment directions
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
            if ml < 1e-9:                                  # ~180° reversal: fall back
                off = seg_n[i] * h
            else:
                m = m / ml
                cos = max(float(np.dot(m, seg_n[i])), 0.25)   # clamp sharp miters
                off = m * (h / cos)
        left[i] = pts[i] + off
        right[i] = pts[i] - off
    ring = np.vstack([left, right[::-1]])                  # forward left, backward right
    return [(float(x), float(y)) for x, y in ring]

def create_polygon_circle(
    center_x: float, 
    center_y: float, 
    radius: float, 
    resolution: int = 16
) -> np.ndarray:
    """Generate points for a circular polyline with adjustable resolution.

    Args:
        center_x: X coordinate of circle center
        center_y: Y coordinate of circle center
        radius: Distance from center to edge
        resolution: Number of vertices (min 3)

    Returns:
        Array of shape (resolution+1, 2) with x,y coordinates
    """
    resolution = max(3, resolution)
    angles = np.linspace(0, 2 * np.pi, resolution, endpoint=False)
    
    x = center_x + radius * np.cos(angles)
    y = center_y + radius * np.sin(angles)
    
    points = np.column_stack((x, y))
    return np.vstack((points, points[0]))


def write_into_existing_interconnect_dxf(routes_all, sig, out_path, use_connector_layers=True):
    """Append generated connector routes to the original interconnect DXF.

    Keeps all original MEA1K geometry untouched. Each pad gets:
      - one circle on the "pads" marker layer,
      - one circle on whichever metal layer (Metal / Metal2) carries its incoming trace.
    """
    um = 1000.0

    doc = ezdxf.readfile(INTERCONNECT_DXF)
    doc.dxfversion = "AC1015"

    msp = doc.modelspace()

    # Drop the original interconnect polyimide; we draw a new unified outline below.
    removed_polyimide = 0
    for ent in list(msp.query('*[layer=="Polyimide"]')):
        msp.delete_entity(ent)
        removed_polyimide += 1

    ensure_layer(doc, "connector_Metal", 3)
    ensure_layer(doc, "connector_Metal2", 5)
    ensure_layer(doc, "pads", 2)
    ensure_layer(doc, "Metal", 3)
    ensure_layer(doc, "Metal2", 5)
    ensure_layer(doc, "L3D0_etching2", 7)
    sig_array = np.asarray(sig)
    pad_to_metal_layers = {i: set() for i in range(len(sig_array))}

    # Per-vertex trace width as a function of y. Width stays IC_TW through the neck AND the
    # diagonal fan (so the fan can't pinch the gap), then ramps IC_TW -> CONNECTOR_TW only in
    # the vertical WIDEN zone (between fan_bottom_y and widen_bottom_y, where pitch is already
    # full CONNECTOR_PITCH), then CONNECTOR_TW for the body. Must match add_spine's zones.
    # band_y = the bond-band y (top of every connected route, all endpoints share it).
    band_y = max(poly[0][1] for poly in routes_all.values())
    fan_bottom_y = band_y - NECK_LEN_MM - FAN_LEN_MM
    widen_bottom_y = fan_bottom_y - WIDEN_LEN_MM

    def trace_width(y):
        if y >= fan_bottom_y:
            return IC_TW
        if y >= widen_bottom_y:
            frac = (fan_bottom_y - y) / WIDEN_LEN_MM
            return IC_TW + frac * (CONNECTOR_TW - IC_TW)
        return CONNECTOR_TW

    metal_count = 0
    metal2_count = 0

    for trace_i, (gkey, poly) in enumerate(routes_all.items()):
        if trace_i % 2 == 0:
            route_layer = "connector_Metal" if use_connector_layers else "Metal"
            metal_count += 1
        else:
            route_layer = "connector_Metal2" if use_connector_layers else "Metal2"
            metal2_count += 1

        # Record which metal layer enters this trace's pad.
        px_end, py_end = poly[-1]
        dists_sq = (sig_array[:, 0] - px_end) ** 2 + (sig_array[:, 1] - py_end) ** 2
        best = int(np.argmin(dists_sq))
        if dists_sq[best] <= (PITCH * 0.6) ** 2:
            pad_to_metal_layers[best].add(route_layer)

        # Emit the trace as a CLOSED polygon outline (mask requirement) rather than a
        # width-bearing open polyline. The variable width (2 µm neck -> connector width)
        # is baked into the per-vertex offset, so the taper is preserved.
        centerline_um = [(x * um, y * um) for (x, y) in poly]
        widths_um = [trace_width(y) * um for (_x, y) in poly]
        ring = stroke_centerline_to_polygon(centerline_um, widths_um)
        if ring is None:
            continue
        msp.add_lwpolyline(
            ring,
            format="xy",
            close=True,
            dxfattribs={"layer": route_layer},
        )

    # Pad circles: marker + per-metal layer matching incoming trace.
    metal_name = "connector_Metal" if use_connector_layers else "Metal"
    metal2_name = "connector_Metal2" if use_connector_layers else "Metal2"
    pad_metal_circles = 0
    pad_metal2_circles = 0

    for i, (x, y) in enumerate(sig_array):
        # msp.add_circle(
        #     (x * um, y * um),
        #     PADR * um,
        #     dxfattribs={"layer": "L3D0_etching2"},
        # )
        circle = create_polygon_circle(x*um, y*um, PADR * um, resolution=64)
        msp.add_lwpolyline(circle, close=True, dxfattribs={'layer': "L3D0_etching2"})
        for metal_layer in pad_to_metal_layers[i]:
            # msp.add_circle(
            #     (x * um, y * um),
            #     PADR * um,
            #     dxfattribs={"layer": metal_layer},
            # )
            msp.add_lwpolyline(circle, close=True, dxfattribs={'layer': metal_layer})

            if metal_layer == metal_name:
                pad_metal_circles += 1
            elif metal_layer == metal2_name:
                pad_metal2_circles += 1

    # New unified polyimide region (replaces the original). Emitted as a SOLID HATCH so
    # KLayout reads it as a filled region. The boundary already has the 4 corner
    # cut-circle arcs carved into the side walls (built in build_polyimide_outline), so
    # the polyimide is removed there rather than added as ears.
    ensure_layer(doc, "Polyimide", 7)
    polyimide_pts = build_polyimide_outline(sig)
    board_hatch = msp.add_hatch(dxfattribs={"layer": "L3D0_etching2"})
    board_hatch.paths.add_polyline_path(polyimide_pts, is_closed=True)
    print(
        f"polyimide: removed {removed_polyimide} original entity(ies), "
        f"added solid region with {len(polyimide_pts)}-vertex boundary "
        f"(4 corner cut-arcs carved into the side walls)"
    )

    doc.saveas(out_path)

    if use_connector_layers:
        print(
            f"appended connector routes on connector_Metal={metal_count}, "
            f"connector_Metal2={metal2_count}"
        )
    else:
        print(
            f"appended connector routes directly on Metal={metal_count}, "
            f"Metal2={metal2_count}"
        )

    print(
        f"pad circles: pads={len(sig_array)}, "
        f"{metal_name}={pad_metal_circles}, {metal2_name}={pad_metal2_circles}; "
        f"wrote {out_path}"
    )


# ======================================================================================
# Main pipeline
# ======================================================================================

def main():
    print("running route_append_to_interconnect_12blocks.py")
    print(
        f"design: {N_BLOCKS_PER_COLUMN} blocks/column × {N_WIRES_PER_BLOCK} wires/block = "
        f"{TOTAL_WIRES} wires total"
    )

    sig_original = _load_extended_sig()
    print(f"extended signal-pad count: {len(sig_original)}")

    interconnect_rows = extract_interconnect_endpoints()

    if len(interconnect_rows) != TOTAL_WIRES:
        raise RuntimeError(
            f"Expected {TOTAL_WIRES} interconnect endpoints, got {len(interconnect_rows)}"
        )

    dx_mm, _dy_mm = interconnect_placement_for_sig(sig_original)
    connector_dx = -dx_mm

    _ixmin, _ixmax, _iymin, interconnect_top_y = interconnect_bbox_mm(INTERCONNECT_DXF)
    _dx_tmp, dy_mm_old = interconnect_placement_for_sig(sig_original)
    connector_dy = -dy_mm_old

    sig = translated_signal_pads(sig_original, connector_dx, connector_dy)

    print(
        f"keeping interconnect fixed; provisional connector translation "
        f"dx={connector_dx:.6f} mm, dy={connector_dy:.6f} mm"
    )

    # Left column gets the first WIRES_PER_COLUMN endpoints (sorted left-to-right).
    spine_x_left = interconnect_rows[0]["bottom_x"] / 1000.0
    spine_x_right = interconnect_rows[WIRES_PER_COLUMN]["bottom_x"] / 1000.0

    # Both columns start their neck at the bond band (all endpoints share this y). The neck
    # is measured from this fixed interconnect y, independent of the connector translation.
    band_y = interconnect_rows[0]["bottom_y"] / 1000.0
    y_top_left = band_y
    y_top_right = band_y

    print(f"spine_x_left={spine_x_left:.6f}")
    print(f"spine_x_right={spine_x_right:.6f}")
    print(f"band_y={band_y:.6f}")

    stages = build_columns_staged(
        spine_x_left, y_top_left, spine_x_right, y_top_right,
        connector_dx=connector_dx, connector_dy=connector_dy,
    )

    spine_routes, ranks_all, all_pads = stages["spine"]

    if len(spine_routes) != TOTAL_WIRES:
        raise RuntimeError(f"Expected {TOTAL_WIRES} spine routes, got {len(spine_routes)}")

    connected_routes = connect_interconnect_to_routes(
        spine_routes, ranks_all, interconnect_rows,
    )

    # Second-pass vertical placement: total length = full POLYIMIDE extent
    # (polyimide top edge → polyimide bottom edge), so the device length now
    # accounts for the polyimide tail beyond the metal traces on both ends.
    polyimide_bottom_y = _bottom_block_centroid_y_mm(sig) - POLYIMIDE["bottom_margin_mm"]
    target_bottom_y = POLYIMIDE["top_y_mm"] - TARGET_TOTAL_LENGTH_MM
    extra_dy = target_bottom_y - polyimide_bottom_y

    print(
        f"first-pass polyimide bottom y={polyimide_bottom_y:.6f} mm; "
        f"target bottom y={target_bottom_y:.6f} mm; "
        f"extra_dy={extra_dy:.6f} mm"
    )

    if abs(extra_dy) > 1e-9:
        connector_dy += extra_dy
        sig = translated_signal_pads(sig_original, connector_dx, connector_dy)
        y_top_left = band_y

        print(
            f"rebuilding with polyimide-bottom target; final connector translation "
            f"dx={connector_dx:.6f} mm, dy={connector_dy:.6f} mm"
        )

        stages = build_columns_staged(
            spine_x_left, y_top_left, spine_x_right, y_top_right,
            connector_dx=connector_dx, connector_dy=connector_dy,
        )

        spine_routes, ranks_all, all_pads = stages["spine"]

        if len(spine_routes) != TOTAL_WIRES:
            raise RuntimeError(f"Expected {TOTAL_WIRES} spine routes after rebuild")

        connected_routes = connect_interconnect_to_routes(
            spine_routes, ranks_all, interconnect_rows,
        )

    polyimide_top_y = POLYIMIDE["top_y_mm"]
    polyimide_bottom_y = _bottom_block_centroid_y_mm(sig) - POLYIMIDE["bottom_margin_mm"]
    route_bottom_y = min_route_y(connected_routes)
    print(
        f"target total length: {TARGET_TOTAL_LENGTH_MM:.3f} mm; "
        f"polyimide_top_y={polyimide_top_y:.6f} mm, "
        f"polyimide_bottom_y={polyimide_bottom_y:.6f} mm, "
        f"actual={polyimide_top_y - polyimide_bottom_y:.6f} mm "
        f"(interconnect_top_y={interconnect_top_y:.6f} mm, "
        f"route_bottom_y={route_bottom_y:.6f} mm)"
    )

    seglist = to_seglist(connected_routes)
    xings, hits = _check_fast(seglist, [tuple(p) for p in sig])

    print(
        f"12-block connected routes: routes={len(connected_routes)}, "
        f"crossings={xings}, pad_hits={hits}"
    )

    write_png(connected_routes, sig, xings, hits, DEBUG_PNG)
    print(f"wrote {DEBUG_PNG}")

    write_into_existing_interconnect_dxf(
        connected_routes, sig, DEBUG_DXF, use_connector_layers=True,
    )
    write_into_existing_interconnect_dxf(
        connected_routes, sig, FINAL_DXF, use_connector_layers=False,
    )

    print("\nOpen this first in KLayout:")
    print(f"  {DEBUG_DXF}")
    print("Expected counts:")
    print(f"  connector_Metal  = {TOTAL_WIRES // 2}")
    print(f"  connector_Metal2 = {TOTAL_WIRES // 2}")
    print(f"  pads             = {12 * N_WIRES_PER_BLOCK}")


if __name__ == "__main__":
    main()
