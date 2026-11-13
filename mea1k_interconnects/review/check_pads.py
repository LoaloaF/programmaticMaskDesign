"""Zoom on one pad per design from the emitted stage-01 DXFs; Rev4 (solid) over Rev3 (dashed grey).

    python3 mea1k_interconnects/review/check_pads.py [out.png]

Reads the stage-01 DXFs in <out root>/interconnect/rev3/ and rev4/ (a full build or --stage01, whose
draft/ is used if present); default output <out root>/review/pads_rev3_vs_rev4/pad_check_rev4.png.
"""
import sys, numpy as np, ezdxf
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Rectangle
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from maskgen.core import paths  # noqa: E402
D = os.path.dirname(paths.interconnect_dir(4)) + "/"      # <out root>/interconnect/
R4 = "rev4/draft/" if os.path.isdir(D + "rev4/draft") else "rev4/"
OUT_PNG = sys.argv[1] if len(sys.argv) > 1 else paths.review_dir("pads_rev3_vs_rev4") + "/pad_check_rev4.png"
DES = [("12-block 56_15  (pad 58)", "new_interconnect_circular_12Block_56_15", 58),
       ("8-block 51_35  (pad 53)", "new_interconnect_circular_8block_51_35", 53),
       ("8-block 71_15  (pad 73)", "new_interconnect_circular_8block_71_15", 73),
       ("dummy 61_10  (pad 63)", "new_interconnect_circular_dummy_61_10", 63),
       ("dummy 35_36  (pad 37)", "new_interconnect_circular_dummy_35_36", 37)]

def load(path):
    out = {}
    for e in ezdxf.readfile(path).modelspace().query("LWPOLYLINE"):
        out.setdefault(e.dxf.layer, []).append(np.array([(p[0], p[1]) for p in e.get_points()]))
    return out

def pad_report(L, side, top=False):
    pads = [p for p in L["Metal2"] if abs(np.ptp(p[:, 0]) - side) < 1e-6 and abs(np.ptp(p[:, 1]) - side) < 1e-6]
    c = np.array([p.mean(0) for p in pads]); mid = np.median(c, 0)
    if top:    # top-most pad of a column near the middle: the lanes of the rows below pass right of it
        col = [i for i in range(len(pads)) if abs(c[i][0] - mid[0]) < 36]
        pad = pads[max(col, key=lambda i: c[i][1])]
    else:
        pad = pads[int(np.argmin(np.hypot(*(c - mid).T)))]
    x0, y0 = pad.min(0)
    inside = lambda q: x0 - 1 <= q[:, 0].min() and q[:, 0].max() <= x0 + side + 1 and y0 - 1 <= q[:, 1].min() and q[:, 1].max() <= y0 + side + 1
    circ = lambda q: ((q.min(0) + q.max(0)) / 2 - [x0, y0], (np.ptp(q[:, 0]) + np.ptp(q[:, 1])) / 4)
    via = [circ(q) for q in L.get("Etching", []) if inside(q)]
    etch = [circ(q) for q in L.get("Polyimide_Negative", []) if len(q) > 10 and np.ptp(q[:, 0]) < 6 and inside(q)]
    from shapely.geometry import Polygon, box
    win = box(x0 - 8, y0 - 8, x0 + side + 8, y0 + side + 8)
    wire = []
    for q in L["Metal1"]:
        g = Polygon(q).buffer(0).intersection(win)
        for gg in ([g] if g.geom_type == "Polygon" else list(getattr(g, "geoms", []))):
            if not gg.is_empty and gg.geom_type == "Polygon": wire.append(np.array(gg.exterior.coords) - [x0, y0])
    return via, etch, wire

def metrics(side, via, etch):
    allc = [c for c, r in via + etch]; r = (via + etch)[0][1]
    edge = [min(x, y, side - x, side - y) - r for x, y in allc]   # via EDGE to pad border
    d = [np.hypot(*(np.array(a) - b)) - 2 * r for i, a in enumerate(allc) for b in allc[:i]]
    return min(edge), max(edge), min(d), len(etch)

fig, axs = plt.subplots(2, 3, figsize=(21, 14)); axs = axs.ravel()
print("%-26s %5s %9s %9s %12s" % ("design", "etch", "edge min", "edge max", "min gap(um)"))
for ax, (title, stem, side) in zip(axs, DES):
    v4, e4, w4 = pad_report(load(D + R4 + stem + "_rev4.dxf"), side)
    v3, e3, _ = pad_report(load(D + "rev3/" + stem + "_rev3.dxf"), side)
    mn, mx, gap, n = metrics(side, v4, e4)
    corner = v4[0][0]
    if title.startswith('12-block'): ZOOM = pad_report(load(D + R4 + stem + '_rev4.dxf'), side, top=True) + (side,)
    print("%-26s %5d %9.3f %9.3f %12.3f" % (title, n, mn, mx, gap))
    ax.add_patch(Rectangle((0, 0), side, side, fc="#f1e7b8", ec="k", lw=1.2))
    for w in w4: ax.fill(w[:, 0], w[:, 1], fc="#9bb8d9", ec="none", alpha=.8)
    for (c, r) in e3 + v3: ax.add_patch(Circle(c, r, fc="none", ec="grey", ls="--", lw=.8))
    for (c, r) in e4: ax.add_patch(Circle(c, r, fc="#d94a4a", ec="k", lw=.5, alpha=.85))
    for (c, r) in v4: ax.add_patch(Circle(c, r, fc="#2a7f3f", ec="k", lw=.5, alpha=.9))
    ax.set_xlim(-6, side + 6); ax.set_ylim(-6, side + 6); ax.set_aspect("equal"); ax.grid(alpha=.2)
    ax.set_title("%s\nRev4: %d etch circles (+ routing via); etch circle edge to border %.2f um, min gap %.2f um" % (title, n, mn, gap), fontsize=10)
v4, e4, w4, side = ZOOM[0], ZOOM[1], ZOOM[2], ZOOM[3]
ax = axs[5]
for w in w4: ax.fill(w[:, 0], w[:, 1], fc="#9bb8d9", ec="#2b4c7e", lw=.6, alpha=.9)
ax.add_patch(Rectangle((0, 0), side, side, fc="none", ec="k", lw=1.2))
for (c, r) in e4: ax.add_patch(Circle(c, r, fc="#d94a4a", ec="k", lw=.5, alpha=.6))
for (c, r) in v4: ax.add_patch(Circle(c, r, fc="#2a7f3f", ec="k", lw=.6, alpha=.8))
ax.set_xlim(-3, 24); ax.set_ylim(side - 24, side + 3); ax.set_aspect("equal"); ax.grid(alpha=.2)
ax.set_title("12-block, TOP pad of a column, zoom: 3.5 um via (green) on a 5.5 um Metal1 pad + teardrop (blue)\n"
             "centred between its two neighbour lanes: 2.5 um to each", fontsize=10)
fig.text(0.01, 0.005, "yellow = Metal2/3 pad   green = routing via (Etching, layer 5)   red = PI-etch circle (Polyimide_Negative, layer 3)   "
         "blue = Metal1 wire + pad   dashed grey = Rev3 circles.   Rev4 12-block/dummies: top pad of a column 3.5 um via on 5.5 um Metal1 pad; other pads 5 um via on 7 um pad, via 0.5 um right (edge 3.0 um from border).", fontsize=10)
plt.tight_layout(rect=(0, 0.02, 1, 1)); plt.savefig(OUT_PNG, dpi=80); print("wrote", OUT_PNG)
