"""Phase 2: the Molex 227044 connector pads and the routing ranks/hand-off that feed them.
"""
import numpy as np

from lib import active

active.require()
from lib.active import *  # noqa: F401,F403  -- this design's knobs (see lib/active.py)


_SIG_CACHE = None      # connector pads, loaded once per process


def extend_sig_with_new_blocks(sig, n_new_per_col=N_NEW_BLOCKS_PER_COLUMN):
    sig = np.asarray(sig, dtype=float)
    xmid = (sig[:, 0].min() + sig[:, 0].max()) / 2
    new_rows = []
    for side_mask in (sig[:, 0] < xmid, sig[:, 0] >= xmid):
        col_sig = sig[side_mask]
        ys = np.sort(np.unique(np.round(col_sig[:, 1], 2)))
        if len(ys) < 8:
            raise RuntimeError(f"need >=2 blocks (8 rows) per column; got {len(ys)}")
        inter_block_stride = float(ys[4] - ys[0])
        block3 = col_sig[(col_sig[:, 1] >= ys[0] - 0.01) & (col_sig[:, 1] <= ys[3] + 0.01)]
        if len(block3) != 64:
            raise RuntimeError(f"expected 64 pads in block-3 template, got {len(block3)}")
        for k in range(n_new_per_col):
            shifted = block3.copy(); shifted[:, 1] -= (k + 1) * inter_block_stride
            new_rows.append(shifted)
    return np.vstack([sig] + new_rows)


def _load_extended_sig():
    """Load the real connector pads from the CSV once, cache the result. The 12-block also
    synthesises N_NEW_BLOCKS_PER_COLUMN extra blocks per column below the real ones (see
    extend_sig_with_new_blocks); the 8-block uses the CSV as is (N_NEW_BLOCKS_PER_COLUMN = 0)."""
    global _SIG_CACHE
    if _SIG_CACHE is None:
        sig = np.loadtxt(CONNECTOR_CSV, delimiter=",", skiprows=1)
        _SIG_CACHE = extend_sig_with_new_blocks(sig) if N_NEW_BLOCKS_PER_COLUMN else sig
    return _SIG_CACHE


def load_connector(side="left", block=0):
    sig = _load_extended_sig()
    ys = np.sort(np.unique(np.round(sig[:, 1], 2)))
    xmid = (sig[:, 0].min() + sig[:, 0].max()) / 2
    n = len(ys)
    rows_top_to_bottom = ys[n - 4 * (block + 1): n - 4 * block][::-1]
    grid = []
    for y in rows_top_to_bottom:
        r = sig[np.abs(sig[:, 1] - y) < 0.05]
        r = r[r[:, 0] < xmid] if side == "left" else r[r[:, 0] >= xmid]
        grid.append([tuple(p) for p in r[np.argsort(r[:, 0])]])
    if side == "left":
        ckt = lambda r, j: [34 + 2 * j, 33 + 2 * j, 2 + 2 * j, 1 + 2 * j][r]
    else:
        ckt = lambda r, j: [1 + 2 * (15 - j), 2 + 2 * (15 - j), 33 + 2 * (15 - j), 34 + 2 * (15 - j)][r]
    return {ckt(r, j): grid[r][j] for r in range(4) for j in range(16)}


def translate_chan(chan, dx=0.0, dy=0.0):
    if abs(dx) < 1e-15 and abs(dy) < 1e-15:
        return chan
    return {c: (x + dx, y + dy) for c, (x, y) in chan.items()}


def translated_signal_pads(sig_original, dx=0.0, dy=0.0):
    sig = sig_original.copy(); sig[:, 0] += dx; sig[:, 1] += dy
    return sig


def conn_rank_mixed(routes, descending=False):
    lanes = {k: poly[0][1] for k, poly in routes.items()}
    order = sorted(lanes, key=lambda k: lanes[k], reverse=descending)
    return {k: i for i, k in enumerate(order)}


def conn_rank_right_direct(routes, direct_keys):
    """Group-aware right-column rank (RIGHT_DIRECT_ROWS34). The rank sets both the under-block lane
    (yb = y_below_base + k*PITCH) and the spine slot (xs_body grows with k), so it is what keeps the
    two groups from cutting each other:

      ranks 0..31  AROUND group (rows 1&2): lane DESCENDING -- the existing nesting, where the
                   highest final lane takes the lowest under-block lane and the innermost slot.
      ranks 32..63 DIRECT group (rows 3&4): lane ASCENDING, so the HIGHEST lane lands in the
                   OUTERMOST slot. That wire peels off the descending bundle first (highest y), so
                   it must be the outermost strand or it cuts the descents of the wires going lower.

    The direct group must occupy the OUTER rank half: the around group descends THROUGH the rows-3&4
    corridor band on its way to its lower under-block lanes, so every direct horizontal run has to
    start to the right of every around descent.

    This also makes the under-block lane index == rank. On the 12-block that keeps the
    Metal1/Metal2 alternation intact: adjacent lanes stay adjacent in gidx, so they stay on
    opposite layers and the same-layer spacing is 2*PITCH."""
    lanes = {k: poly[0][1] for k, poly in routes.items()}
    around = sorted((k for k in routes if k not in direct_keys),
                    key=lambda k: lanes[k], reverse=True)
    direct = sorted(direct_keys, key=lambda k: lanes[k])
    return {k: i for i, k in enumerate(around + direct)}


def ordered_routes_by_spine(routes_all, ranks_all):
    return sorted(routes_all.items(),
                  key=lambda item: (0 if ranks_all[item[0]][0] == "left" else 1,
                                    ranks_all[item[0]][1], ranks_all[item[0]][2]))


def connect_handoff_to_routes(routes_all, ranks_all, handoff_mm):
    """Prepend each spine route with its via-row hand-off point (band order == spine order,
    both index gidx). handoff_mm is the list of (x,y) in mm, sorted left-to-right."""
    ordered = ordered_routes_by_spine(routes_all, ranks_all)
    if len(ordered) != len(handoff_mm):
        raise RuntimeError(f"routes={len(ordered)}, handoff points={len(handoff_mm)}")
    connected = {}
    max_dx = 0.0
    for (gkey, poly), (sx, sy) in zip(ordered, handoff_mm):
        rx, _ry = poly[0]
        max_dx = max(max_dx, abs(sx - rx))
        if abs(sx - rx) > 1e-6:
            raise RuntimeError(f"hand-off x={sx:.6f} not aligned to neck x={rx:.6f} for {gkey}")
        connected[gkey] = [(sx, sy)] + poly
    print(f"hand-off alignment: max_dx={max_dx * 1000:.4f} um")
    return connected
