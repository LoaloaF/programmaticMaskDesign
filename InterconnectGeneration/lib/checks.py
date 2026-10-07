"""Centreline-only routing checks: crossings and pad hits.

NOTE these see centrelines only -- no edge-to-edge copper spacing (HANDOVER §6).
"""

from lib import active

active.require()
from lib.active import *  # noqa: F401,F403  -- this design's knobs (see lib/active.py)


def _seg_int(a, b, c, d):
    def cc(p, q, r): return (r[1]-p[1])*(q[0]-p[0]) - (q[1]-p[1])*(r[0]-p[0])
    return (cc(c, d, a) > 0) != (cc(c, d, b) > 0) and (cc(a, b, c) > 0) != (cc(a, b, d) > 0)


def _seg_pad(a, b, pad):
    (x1, y1), (x2, y2) = a, b
    cx, cy = pad
    dx, dy = x2 - x1, y2 - y1
    L2 = dx * dx + dy * dy
    t = 0 if L2 == 0 else max(0, min(1, ((cx - x1) * dx + (cy - y1) * dy) / L2))
    qx, qy = x1 + t * dx, y1 + t * dy
    return (qx - cx) ** 2 + (qy - cy) ** 2 < (PADR + max(IC_TW, CONNECTOR_TW) / 2) ** 2


def _check_fast(seglist, pads, own_pad=None):
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
        opad = own_pad[i] if own_pad is not None else None   # trace i's own destination pad
        for pad in pads:
            if opad is not None and abs(pad[0]-opad[0]) < 1e-9 and abs(pad[1]-opad[1]) < 1e-9:
                continue                                     # a trace may touch its own pad
            if not (x0 - PADR <= pad[0] <= x1 + PADR and y0 - PADR <= pad[1] <= y1 + PADR):
                continue
            if (a[0]-pad[0])**2 + (a[1]-pad[1])**2 < own or (b[0]-pad[0])**2 + (b[1]-pad[1])**2 < own:
                continue
            if _seg_pad(a, b, pad):
                hits += 1
    return xings, hits
