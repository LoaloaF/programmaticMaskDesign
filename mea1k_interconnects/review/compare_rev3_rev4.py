"""Rev3 (dashed grey) vs Rev4 (solid) for every design, ALL panels at the same physical scale.

    python3 mea1k_interconnects/review/compare_rev3_rev4.py [design ...]

Reads <out root>/interconnect/rev3/ and rev4/ (build_designs.py --rev 3 / --rev 4), writes
<out root>/review/rev3_vs_rev4/cmp_<design>.png.

Panels (each 100 x 100 um unless noted, anchored on the top-left corner of one pad, where the routing
via sits): top row / 2nd row / a middle row / bottom row of one column, then the Metal1 band jog
(100 x 360 um, the 300 um where the wires step onto Rev3's path) and, for the dummies, the bars/circles
above the band. Everything is read from the FINAL files (routed DXF / dummy GDS), rotated back into the
stage-01 frame (pads with the via top-left).
"""
import sys, numpy as np, ezdxf, gdstk
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Polygon as MP
from shapely.geometry import Polygon, box
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from maskgen.core import paths  # noqa: E402
D = os.path.dirname(paths.interconnect_dir(4)) + "/"      # <out root>/interconnect/
OUT = paths.review_dir("rev3_vs_rev4") + "/"
K = 0.075                      # inches per um, the SAME for every panel of every design
DPI = 100
DES = {  # key: (title, stage01 stem, final file stem or gds, pad side, kind)
    "12block":      ("12-block 56_15", "new_interconnect_circular_12Block_56_15", "new_interconnect_with_connector_chamfered_12Block_56_15", 58, "dxf"),
    "8block_51_35": ("8-block 51_35", "new_interconnect_circular_8block_51_35", "new_interconnect_with_connector_8blocks_chamfered_1layer_51_35", 53, "dxf"),
    "8block_71_15": ("8-block 71_15", "new_interconnect_circular_8block_71_15", "new_interconnect_with_connector_8blocks_chamfered_1layer_71_15", 73, "dxf"),
    "dummy_61_10":  ("dummy 61_10", "new_interconnect_circular_dummy_61_10", "dummy_new_interconnect_circular_dummy_61_10_padrows", 63, "gds"),
    "dummy_35_36":  ("dummy 35_36", "new_interconnect_circular_dummy_35_36", "dummy_new_interconnect_circular_dummy_35_36_padrows", 37, "gds"),
}

def rev_path(rev, stem, ext):
    return D + ("rev3/%s_rev3" if rev == 3 else "rev4/%s_rev4") % stem + "." + ext

def centre(path):
    xs, ys = [], []
    for e in ezdxf.readfile(path).modelspace().query("LWPOLYLINE"):
        for p in e.get_points(): xs.append(p[0]); ys.append(p[1])
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2

def load_dxf(path, c):
    L = {}
    for e in ezdxf.readfile(path).modelspace().query("LWPOLYLINE"):
        P = np.array([(p[0], p[1]) for p in e.get_points()])
        P = np.c_[2 * c[0] - P[:, 0], 2 * c[1] - P[:, 1]]          # back into the stage-01 frame
        L.setdefault({"Via": "Etching"}.get(e.dxf.layer, e.dxf.layer), []).append(P)
    return L

def load_gds(path):
    names = {(3, 0): "Metal1", (5, 0): "Etching", (6, 0): "Metal2", (8, 0): "Metal3", (1, 0): "Polyimide_Negative"}
    L = {}
    top = gdstk.read_gds(path).top_level()[0]
    for p in top.polygons:
        k = names.get((p.layer, p.datatype))
        if k: L.setdefault(k, []).append(np.array(p.points))
    return L

def inwin(q, w): return q[:, 0].max() >= w[0] and q[:, 0].min() <= w[1] and q[:, 1].max() >= w[2] and q[:, 1].min() <= w[3]
def circ(q): return (q.min(0) + q.max(0)) / 2, (np.ptp(q[:, 0]) + np.ptp(q[:, 1])) / 4

def draw(ax, L, w, old):
    gk = dict(ls=(0, (4, 3)), lw=1.0)
    for q in L.get("Metal2", []):
        if inwin(q, w): ax.add_patch(MP(q, closed=True, fc="none", ec="#aaa" if old else "#b8a050", lw=1.0, ls=(0, (2, 3)) if old else "-", zorder=1))
    for q in L.get("Metal1", []):
        if inwin(q, w):
            ax.add_patch(MP(q, closed=True, fc="#0000000c", ec="#444", zorder=2, **gk) if old else
                         MP(q, closed=True, fc="#4a86c8", ec="#1d4f8a", alpha=.5, lw=.8, zorder=3))
    for q in L.get("Polyimide_Negative", []):
        if not inwin(q, w): continue
        if np.ptp(q[:, 0]) < 8 and len(q) > 8:
            c, r = circ(q)
            ax.add_patch(Circle(c, r, fc="none", ec="#555", **gk) if old else Circle(c, r, fc="#d94a4a", ec="k", lw=.5, alpha=.75, zorder=4))
        elif np.ptp(q[:, 0]) >= 8:
            ax.add_patch(MP(q, closed=True, fc="none", ec="#555" if old else "#c03030", lw=1.0 if old else 1.2, ls=gk["ls"] if old else "-", zorder=4))
    for q in L.get("Etching", []):
        if inwin(q, w):
            c, r = circ(q)
            ax.add_patch(Circle(c, r, fc="none", ec="#333", **gk) if old else Circle(c, r, fc="#2a7f3f", ec="k", lw=.6, alpha=.85, zorder=5))
    ax.set_xlim(w[0], w[1]); ax.set_ylim(w[2], w[3]); ax.set_aspect("equal")
    ax.set_xticks(np.arange(np.ceil(w[0] / 10) * 10, w[1], 10), minor=False)
    ax.set_yticks(np.arange(np.ceil(w[2] / 10) * 10, w[3], 10), minor=False)
    ax.grid(alpha=.18); ax.tick_params(labelsize=7)

def min_gap(L, w):
    polys = [Polygon(q).buffer(0) for q in L.get("Metal1", []) if inwin(q, w)]
    win = box(*[w[0], w[2], w[1], w[3]])
    polys = [p.intersection(win) for p in polys]; polys = [p for p in polys if not p.is_empty and p.area > 1]
    from shapely.ops import unary_union
    u = unary_union(polys); polys = list(u.geoms) if hasattr(u, "geoms") else [u]   # touching pieces of one wire -> one
    g = float("inf")
    for i, a in enumerate(polys):
        for b in polys[i + 1:]:
            if a.bounds[0] > b.bounds[2] + 10 or b.bounds[0] > a.bounds[2] + 10 or a.bounds[1] > b.bounds[3] + 10 or b.bounds[1] > a.bounds[3] + 10: continue
            g = min(g, a.distance(b))
    return g

def panel(fig, left, bottom, wum, hum):
    return fig.add_axes([left / (fig.get_figwidth()), bottom / fig.get_figheight(), wum * K / fig.get_figwidth(), hum * K / fig.get_figheight()])

def run(key):
    title, mid, fin, side, kind = DES[key]
    c = centre(rev_path(4, mid, "dxf"))
    if kind == "dxf":
        L3, L4 = load_dxf(rev_path(3, fin, "dxf"), c), load_dxf(rev_path(4, fin, "dxf"), c)
    else:
        L3, L4 = load_gds(rev_path(3, fin, "gds")), load_gds(rev_path(4, fin, "gds"))
    pads = np.array([q.min(0) for q in L4["Metal2"] if abs(np.ptp(q[:, 0]) - side) < 1e-6 and abs(np.ptp(q[:, 1]) - side) < 1e-6])
    xs = np.unique(pads[:, 0].round(3)); X0 = xs[len(xs) // 2]
    ys = sorted(pads[np.abs(pads[:, 0] - X0) < 1e-3][:, 1])
    rows = [("top row", ys[-1]), ("2nd row from top", ys[-2]), ("a middle row", ys[len(ys) // 2]), ("bottom row", ys[0])]
    ptop = max(ys) + side
    W, H = 100.0, 100.0
    panels = [(nm, (X0 - 22, X0 + W - 22, y0 + side - H + 22, y0 + side + 22)) for nm, y0 in rows]
    jog = (X0 - 22, X0 + W - 22, ptop - 6, ptop + 354)
    extra = []
    if kind == "gds":     # the dummy has no connector: its wires run on 1.2 mm to Metal1 bars, with a big circle on top
        yb = max(q[:, 1].max() for q in L4["Metal1"] if np.ptp(q[:, 1]) > 500 and q[:, 1].max() < 2000)   # band end
        jog = (X0 - 22, X0 + W - 22, yb - 100, yb + 260)
        bars = [q for q in L4["Metal1"] if q[:, 1].min() > 2000 and np.ptp(q[:, 1]) < 40 and np.ptp(q[:, 0]) > 500]
        bar_y = np.mean([q[:, 1].mean() for q in bars])
        bx = sorted(q[:, 0].max() for q in bars)[len(bars) // 2]            # a gap between two bars, mid-device
        cm = [q for q in L4["Metal1"] if np.ptp(q[:, 0]) > 700 and q[:, 1].min() > bar_y]
        cc = sorted(cm, key=lambda q: abs((q[:, 0].min() + q[:, 0].max()) / 2 - X0))[0]
        cx_, cy_ = (cc[:, 0].min() + cc[:, 0].max()) / 2, cc[:, 1].min()
        extra = [("dummy Metal1 bar ends (the wires' 1.2 mm extensions end here)", (bx - 50, bx + 50, bar_y - 50, bar_y + 50)),
                 ("dummy circle bottom (Metal1 circle, PI-etch circle + ring)", (cx_ - 50, cx_ + 50, cy_ - 30, cy_ + 70))]
    mx, my, gap = 1.0, 0.7, 0.35           # margins / gaps in inches
    wp = W * K
    ncol = 4
    fw = mx * 2 + ncol * wp + (ncol - 1) * gap
    fh = my * 2 + H * K + 0.6 + 360 * K + 0.5
    fig = plt.figure(figsize=(fw, fh), dpi=DPI)
    y_top_row = fh - my - 0.4 - H * K
    stats = []
    for i, (nm, w) in enumerate(panels):
        ax = panel(fig, mx + i * (wp + gap), y_top_row, W, H)
        draw(ax, L3, w, True); draw(ax, L4, w, False)
        g3, g4 = min_gap(L3, w), min_gap(L4, w)
        ax.set_title("%s   min Metal1 gap in view: Rev3 %s -> Rev4 %s um" % (nm, "%.2f" % g3 if g3 < 1e8 else "-", "%.2f" % g4 if g4 < 1e8 else "-"), fontsize=9)
    ax = panel(fig, mx, my, W, 360)
    draw(ax, L3, jog, True); draw(ax, L4, jog, False)
    ax.set_title(("wire band end + extensions" if kind == "gds" else "band jog: wires step onto Rev3's path") + " (min Metal1 gap Rev4 %.2f um)" % min_gap(L4, jog), fontsize=9)
    for j, (nm, w) in enumerate(extra):
        ax = panel(fig, mx + (j + 1) * (wp + gap), my + 360 * K - H * K, W, H)
        draw(ax, L3, w, True); draw(ax, L4, w, False)
        ax.set_title(nm, fontsize=8)
    # legend / numbers
    fig.text((mx + 1.15 * (wp + gap)) / fw, my + 360 * K * 0.4,
             "%s   (pad %g um, column x=%g)\n\nSOLID = Rev4\n  blue   Metal1 (wire + landing pad + teardrop)\n  green  routing via (Etching/Via)\n  red    PI-etch circle / PI ring\n  tan    Metal2 pad edge\nDASHED GREY = Rev3 (previous)\n\nALL panels: same scale, 100 um across = %.1f in\ngrid = 10 um" % (title, side, X0, W * K),
             fontsize=11, va="center")
    fig.suptitle("%s: Rev3 (dashed) vs Rev4 (solid) -- every changed region, one column, final files" % title, fontsize=14, y=0.995)
    out = OUT + "cmp_%s.png" % key
    fig.savefig(out, dpi=DPI); print("wrote", out, "%.0fx%.0f px" % (fw * DPI, fh * DPI))

if __name__ == "__main__":
    for k in (sys.argv[1:] or DES): run(k)
