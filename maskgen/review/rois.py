"""Named features (ROIs) per family. Each one finds its feature in a column's GEOMETRY.

An ROI's `find(col)` returns the feature's box (x0, y0, x1, y1) in um, or None if the column has no such
feature. The sheet then draws every column of the row through a window of the SAME size, centred on that
column's box. The window size is the largest box in the row, plus `margin` on each side, and at least
`size`. So a row is at one scale, and a feature that grew between revisions shows as grown.

Fab layers by number: they are the same in every interconnect LayerSet (Metal1 7, Metal2 6, PI etch 3)
and in the probe set (Etching 6, Metal 7, pad_etching 3, PEDOT 8, IONP 1).

The interconnect rules are the ones of the former mea1k_interconnects/review/compare_rev3_rev4.py:
- The pads are the Metal2 squares of the most common size.
- The column is the middle column of pads.
- "top" is the pad row furthest from the connector.
"""
from collections import Counter, namedtuple

import numpy as np

from maskgen.core import layers as L

ROI = namedtuple("ROI", "name family find size margin cell metric note")
#   size     minimum window (w, h) um
#   margin   um added around the feature box on each side
#   cell     the larger side of a cell, inches (PNG; the PDF draws larger)
#   metric   measure the smallest gap between separate metal shapes in the window


# ============================================================================= interconnect
M1, M2, PI = 7, 6, 3


def _pads(col):
    """(pad side, x of the middle pad column, pad y's of that column, bottom to top) -- cached."""
    if "pads" not in col.cache:
        sq = [q for q in col.outer(M2)
              if abs(np.ptp(q[:, 0]) - np.ptp(q[:, 1])) < 1e-6 and np.ptp(q[:, 0]) > 10]
        if not sq:
            col.cache["pads"] = None
            return None
        side = Counter(round(float(np.ptp(q[:, 0])), 3) for q in sq).most_common(1)[0][0]
        pads = np.array([q.min(0) for q in sq if abs(np.ptp(q[:, 0]) - side) < 1e-6])
        xs = np.unique(pads[:, 0].round(3))
        x0 = xs[len(xs) // 2]
        ys = sorted(pads[np.abs(pads[:, 0] - x0) < 1e-3][:, 1])
        col.cache["pads"] = (side, x0, ys)
    return col.cache["pads"]


def _pad(pick):
    def find(col):
        p = _pads(col)
        if not p:
            return None
        side, x0, ys = p
        y = pick(ys)
        return (x0, y, x0 + side, y + side)
    return find


def _via_corner(col):
    """The top-left corner of the column's top pad: routing via + Metal1 landing pad + teardrop."""
    p = _pads(col)
    if not p:
        return None
    side, x0, ys = p
    return (x0, ys[-1] + side - 24.0, x0 + 24.0, ys[-1] + side)


def _band_above(col):
    """The 360 um of wire band leaving the column's top pad (Rev4: where the wires step onto Rev3's path)."""
    p = _pads(col)
    if not p or col.kind != "routed":
        return None
    side, x0, ys = p
    top = ys[-1] + side
    return (x0 - 22.0, top - 6.0, x0 + 78.0, top + 354.0)


def _openings(col):
    """Connector-pad openings: the PI-etch shapes of the most common size larger than 100 um."""
    if "openings" not in col.cache:
        bb = col.bbox(PI)
        w, h = bb[:, 2] - bb[:, 0], bb[:, 3] - bb[:, 1]
        big = (w > 100) & (h > 100) & (w < 400) & (h < 400)
        if col.kind != "routed" or not big.any():
            col.cache["openings"] = None
        else:
            size = Counter(zip(w[big].round(1), h[big].round(1))).most_common(1)[0][0]
            keep = big & (w.round(1) == size[0]) & (h.round(1) == size[1])
            col.cache["openings"] = bb[keep]
    return col.cache["openings"]


def _connector(outermost):
    def find(col):
        o = _openings(col)
        if o is None:
            return None
        c = (o[:, :2] + o[:, 2:]) / 2
        d = np.hypot(*(c - c.mean(0)).T)
        return tuple(o[int(np.argmax(d) if outermost else np.argmin(d))])
    return find


def _dummy_band_end(col):
    p = _pads(col)
    if not p or col.kind != "dummy":
        return None
    side, x0, ys = p
    m1 = col.outer(M1)
    yb = max(q[:, 1].max() for q in m1 if np.ptp(q[:, 1]) > 500 and q[:, 1].max() < 2000)
    return (x0 - 22.0, yb - 100.0, x0 + 78.0, yb + 260.0)


def _dummy_bars(col):
    if col.kind != "dummy":
        return None
    m1 = col.outer(M1)
    bars = [q for q in m1 if q[:, 1].min() > 2000 and np.ptp(q[:, 1]) < 40 and np.ptp(q[:, 0]) > 500]
    if not bars:
        return None
    col.cache["bar_y"] = bar_y = float(np.mean([q[:, 1].mean() for q in bars]))
    bx = sorted(q[:, 0].max() for q in bars)[len(bars) // 2]       # a gap between two bars, mid-device
    return (bx - 50.0, bar_y - 50.0, bx + 50.0, bar_y + 50.0)


def _dummy_circle(col):
    p = _pads(col)
    if not p or _dummy_bars(col) is None:
        return None
    x0, bar_y = p[1], col.cache["bar_y"]
    cm = [q for q in col.outer(M1) if np.ptp(q[:, 0]) > 700 and q[:, 1].min() > bar_y]
    if not cm:
        return None
    cc = min(cm, key=lambda q: abs((q[:, 0].min() + q[:, 0].max()) / 2 - x0))
    cx, cy = (cc[:, 0].min() + cc[:, 0].max()) / 2, cc[:, 1].min()
    return (cx - 50.0, cy - 30.0, cx + 50.0, cy + 70.0)


INTERCONNECT = [
    ROI("top pad", "interconnect", _pad(lambda ys: ys[-1]), (0, 0), 12, 2.4, True,
        "top pad of the middle column (furthest from the connector): the lanes of every row below pass it"),
    ROI("2nd pad", "interconnect", _pad(lambda ys: ys[-2]), (0, 0), 12, 2.4, True,
        "second pad from the top, same column"),
    ROI("middle pad", "interconnect", _pad(lambda ys: ys[len(ys) // 2]), (0, 0), 12, 2.4, True,
        "a pad from the middle row"),
    ROI("bottom pad", "interconnect", _pad(lambda ys: ys[0]), (0, 0), 12, 2.4, True,
        "bottom pad of the column (nearest the connector)"),
    ROI("top-pad via", "interconnect", _via_corner, (0, 0), 3, 2.4, True,
        "top-left corner of the top pad: routing via, Metal1 landing pad and teardrop"),
    ROI("band above column", "interconnect", _band_above, (100, 360), 0, 3.6, True,
        "the wire band leaving the column's top pad (Rev4: the wires step onto Rev3's path here)"),
    ROI("connector pad", "interconnect", _connector(False), (0, 0), 60, 2.4, True,
        "the connector-pad opening nearest the middle of the connector, with its pad and entry"),
    ROI("outer connector pad", "interconnect", _connector(True), (0, 0), 60, 2.4, True,
        "the connector-pad opening furthest from the middle of the connector"),
    ROI("dummy band end", "interconnect", _dummy_band_end, (100, 360), 0, 3.6, True,
        "dummies: where the wire band ends and the 1.2 mm extensions start"),
    ROI("dummy bar ends", "interconnect", _dummy_bars, (100, 100), 0, 2.4, True,
        "dummies: a gap between two Metal1 bars (the extensions end here)"),
    ROI("dummy circle", "interconnect", _dummy_circle, (100, 100), 0, 2.4, False,
        "dummies: bottom of a Metal1 circle with its PI-etch circle and ring"),
]


# ============================================================================= probe
P_ETCH, P_METAL, P_SITE, P_IONP = 6, 7, 3, 1


def _outline(col):
    """The device outline: the largest shape on the etch layer."""
    bb = col.bbox(P_ETCH)
    if not len(bb):
        return None
    return col.outer(P_ETCH)[int(np.argmax((bb[:, 2] - bb[:, 0]) * (bb[:, 3] - bb[:, 1])))]


def _tip(col):
    o = _outline(col)
    if o is None:
        return None
    x, y = o[int(np.argmin(o[:, 1]))]
    return (x - 110.0, y - 20.0, x + 110.0, y + 200.0)


def _first_sites(col):
    bb = col.bbox(P_SITE)
    if not len(bb):
        return None
    low = bb[np.argsort(bb[:, 1])[:5]]
    return (low[:, 0].min(), low[:, 1].min(), low[:, 2].max(), low[:, 3].max())


def _ionp_start(col):
    bb = col.bbox(P_IONP)
    if not len(bb):
        return None
    i = int(np.lexsort((bb[:, 0], bb[:, 1].round(1)))[0])        # lowest well, then leftmost
    x, y = bb[i, 0], bb[i, 1]
    return (x - 10.0, y - 10.0, x + 110.0, y + 110.0)


def _ionp_band(col):
    bb = col.bbox(P_IONP)
    if not len(bb):
        return None
    return (bb[:, 0].min(), bb[:, 1].min(), bb[:, 2].max(), bb[:, 3].max())


def _solder_block(col):
    o = _outline(col)
    if o is None:
        return None
    ytop = o[:, 1].max()
    bb = col.bbox(P_METAL)
    top = bb[bb[:, 1] > ytop - 3200.0]
    if not len(top):
        return None
    return (top[:, 0].min(), top[:, 1].min(), top[:, 2].max(), top[:, 3].max())


def _overview(col):
    o = _outline(col)
    return None if o is None else (o[:, 0].min(), o[:, 1].min(), o[:, 0].max(), o[:, 1].max())


PROBE = [
    ROI("tip", "probe", _tip, (0, 0), 0, 2.6, False, "the shank tip and the loop around it"),
    ROI("first sites", "probe", _first_sites, (0, 0), 15, 2.6, False,
        "the five recording sites nearest the tip: Metal pad, pad_etching opening, PEDOT/SIROF"),
    ROI("IONP wells", "probe", _ionp_start, (0, 0), 0, 2.6, False, "the first IONP wells (the barcode's start)"),
    ROI("IONP band", "probe", _ionp_band, (0, 0), 50, 3.6, False, "every IONP well of the device (the barcode)"),
    ROI("solder pads + ID", "probe", _solder_block, (0, 0), 60, 3.6, False,
        "the solder-pad block and the ID label"),
    ROI("whole device", "probe", _overview, (0, 0), 100, 6.0, False, "the whole device"),
]


# ============================================================================= wafer
def _mark(x_mm):
    def find(col):
        bb = col.bbox(L.TEMPLATE_MARK_COMPANION)
        if not len(bb):
            return None
        c = (bb[:, :2] + bb[:, 2:]) / 2
        near = bb[(np.abs(c[:, 0] - x_mm * 1000.0) < 300.0) & (np.abs(c[:, 1]) < 1000.0)]
        if not len(near):
            return None
        return (near[:, 0].min(), near[:, 1].min(), near[:, 2].max(), near[:, 3].max())
    return find


WAFER = [ROI("mark %+.1f mm (slot of %d/0)" % (sgn * x, lay), "wafer", _mark(sgn * x), (0, 0), 40, 2.6, False,
             "alignment-mark slot at x = %+.1f mm; the template ships layer %d's vernier here (%s), the "
             "%d/0 companion beside it" % (sgn * x, lay, L.TEMPLATE_MARK_POLARITY[lay], L.TEMPLATE_MARK_COMPANION))
         for sgn in (-1, 1) for lay, x in sorted(L.TEMPLATE_MARK_SLOTS.items(), key=lambda kv: kv[1])]

FAMILIES = {"interconnect": INTERCONNECT, "probe": PROBE, "wafer": WAFER}


def select(family, names=None):
    """The ROIs of a family, optionally only those whose name contains one of `names` (case-insensitive)."""
    rois = FAMILIES[family]
    if not names:
        return rois
    out = [r for r in rois if any(n.lower() in r.name.lower() for n in names)]
    if not out:
        raise SystemExit("no %s feature matches %s; known: %s" % (family, names, ", ".join(r.name for r in rois)))
    return out
