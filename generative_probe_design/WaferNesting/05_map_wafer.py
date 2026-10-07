"""Stage 05 -- the wafer map: which device is where, and which barcode it carries.

    python3 WaferNesting/05_map_wafer.py

Reads `placement.json`, which STAGE 03 WROTE. That matters: the variant -> pose deal is a
seeded shuffle, and recomputing it here would be a second implementation of it, free to
disagree with the GDS that actually shipped. This stage reads the record instead, so the
map cannot describe a wafer other than the one on disk. Run 03 first.

Shapes come from the per-design footprints (stage 01), centred, so drawing a piece is just
rotate-by-pose then translate -- the same transform 03 applies to the real geometry, minus
the un-centring it has to undo because the DXF is not centred and the footprint is.

Two panels: the wafer, and a legend keyed to it. Every device is numbered, and the numbers
are the `i` field of placement.json, so a device picked off the map can be looked up in the
JSON and vice versa.
"""
import json, os, sys, math
import importlib

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MplPolygon, Circle
from matplotlib.lines import Line2D

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
C = importlib.import_module(os.environ.get("WAFERNEST_CONFIG", "config_64ch_4shank"))

if not os.path.exists(C.PLACEMENT_PATH):
    raise SystemExit("missing %s -- run 03_export_wafer.py first (it writes the "
                     "variant->pose record this map describes)" % C.PLACEMENT_PATH)

placement = json.load(open(C.PLACEMENT_PATH))
KINDS = C.kinds()

# One hue per GEOMETRY, four shades of it for that geometry's four barcodes. Reading the
# map at a glance is a question about geometry ("where are the 8 mm ones?"); reading it
# closely is a question about barcode, and that is what the shade and the label answer.
HUES = {"U1.6": (0.16, 0.50, 0.72),    # blue
        "U2.5": (0.85, 0.37, 0.01),    # orange
        "U4":   (0.47, 0.16, 0.51),    # purple
        "U8":   (0.11, 0.55, 0.24)}    # green
FALLBACK = [(0.80, 0.20, 0.30), (0.20, 0.45, 0.75), (0.35, 0.60, 0.20), (0.60, 0.40, 0.15)]


def shade(kind, idx, n):
    """Variant `idx` of `n` in this kind's hue: dark for the first, pale for the last."""
    base = np.array(HUES.get(kind, FALLBACK[KINDS.index(kind) % len(FALLBACK)]))
    t = 0.0 if n <= 1 else 0.55 * idx / (n - 1)      # never fully washed out
    return tuple(base + (1.0 - base) * t)


variants = {k: [d["key"] for d in C.designs_of(k)] for k in KINDS}
COLOR = {key: shade(k, i, len(v)) for k, v in variants.items() for i, key in enumerate(v)}

# --- geometry ----------------------------------------------------------------
FOOT = {key: np.asarray(json.load(open(C.footprint_path(key))), dtype=float)
        for key in COLOR}


def handle_y(poly):
    """y (in the design's own frame) of the middle of its WIDE end.

    Labels go here, not at the piece's centre. These are ~3 mm across and ~40 mm long, and
    the centre lands in the narrow shank where a flipped neighbour's handle is interlocked
    right against it -- the most crowded part of the picture. The handle is the widest part
    and, because the nest alternates 0/180, the handles of two neighbours point in opposite
    directions, so anchoring there spreads the labels out instead of stacking them.
    """
    from shapely.geometry import Polygon as _P, LineString as _L
    g = _P(poly)
    lo, hi = poly[:, 1].min(), poly[:, 1].max()
    ys = np.linspace(lo, hi, 120)[1:-1]
    w = np.array([g.intersection(_L([(-20, y), (20, y)])).length for y in ys])
    band = ys[w >= 0.92 * w.max()]
    return float(np.median(band))


HANDLE = {key: handle_y(p) for key, p in FOOT.items()}


def placed(key, angle_deg):
    """The design's centred footprint, rotated to its pose. Translation is added later."""
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)
    p = FOOT[key]
    return np.column_stack((p[:, 0] * ca - p[:, 1] * sa,
                            p[:, 0] * sa + p[:, 1] * ca))


fig = plt.figure(figsize=(19.5, 14.0))
gs = fig.add_gridspec(1, 2, width_ratios=[1.0, 0.34], wspace=0.02)
ax = fig.add_subplot(gs[0, 0])
ax.set_aspect("equal")
ax.axis("off")

# --- the wafer ---------------------------------------------------------------
R, FLAT = C.WAFER_R, C.WAFER_FLAT_Y
th = np.linspace(0, 2 * np.pi, 721)
wx, wy = R * np.cos(th), R * np.sin(th)
keep = wy >= FLAT
ax.add_patch(MplPolygon(np.column_stack((wx[keep], wy[keep])), closed=True,
                        facecolor="#f7f7f5", edgecolor="#333333", lw=1.6, zorder=0))
ru = R - C.EDGE_EXCL
ax.add_patch(Circle((0, 0), ru, facecolor="none", edgecolor="#999999",
                    lw=0.9, ls=(0, (6, 4)), zorder=1))

# alignment marks, so the map shows the keep-outs the nest had to respect
if os.path.exists(C.MARKS_WKT):
    try:
        from shapely import wkt as _wkt
        m = _wkt.loads(open(C.MARKS_WKT).read())
        for g in (m.geoms if m.geom_type == "MultiPolygon" else [m]):
            ax.add_patch(MplPolygon(np.asarray(g.exterior.coords), closed=True,
                                    facecolor="#b0b0b0", edgecolor="none", zorder=2))
    except ImportError:
        pass      # shapely is optional here; the marks are decoration on this figure

# --- the devices -------------------------------------------------------------
for p in placement:
    key, ang, x, y = p["key"], p["angle_deg"], p["x_mm"], p["y_mm"]
    poly = placed(key, ang) + (x, y)
    ax.add_patch(MplPolygon(poly, closed=True, facecolor=COLOR[key],
                            edgecolor="#1a1a1a", lw=0.45, zorder=3))

    # The label runs ALONG the device: ~3 mm across is no room for the code, ~40 mm along
    # is plenty. The design's long axis is its Y, and text at rotation 0 runs along X, so
    # the text angle is the pose angle PLUS 90. Folded into [-90, 90) so it never prints
    # upside down -- the piece is not folded, only the lettering on it.
    a = math.radians(ang)
    hx = -HANDLE[key] * math.sin(a) + x
    hy = HANDLE[key] * math.cos(a) + y
    ta = (ang + 90) % 360
    ta = ta - 180 if 90 < ta <= 270 else ta
    ax.text(hx, hy, "%d  %s" % (p["i"], key), rotation=ta, rotation_mode="anchor",
            ha="center", va="center", fontsize=6.4, color="#111111", zorder=4,
            fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.13", fc="white", ec="none", alpha=0.70))

ax.set_xlim(-R - 2, R + 2)
ax.set_ylim(FLAT - 2, R + 2)

counts = {}
for p in placement:
    counts[p["key"]] = counts.get(p["key"], 0) + 1
ax.set_title("%s  --  %d devices on a %g mm wafer\n"
             "numbers are the `i` field of placement.json; "
             "dashed circle is the %g mm edge exclusion"
             % (C.TARGET_SET, len(placement), 2 * R, C.EDGE_EXCL),
             fontsize=13, pad=14)

# --- the legend panel --------------------------------------------------------
lax = fig.add_subplot(gs[0, 1])
lax.axis("off")
lax.set_xlim(0, 1)
lax.set_ylim(0, 1)

# Laid out by WEIGHT, not by row count: a variant row is two lines (swatch + the device
# numbers under it) and a heading is one, so a flat `1/len(rows)` step runs the last kind
# off the bottom of the axes, where it is silently clipped away.
rows = [("title", "WHAT IS WHERE", 0, 2.2)]
for k in KINDS:
    rows.append(("head", k, sum(counts.get(v, 0) for v in variants[k]), 1.7))
    for v in variants[k]:
        rows.append(("item", v, counts.get(v, 0), 1.85))
    rows.append(("gap", "", 0, 0.5))
rows.append(("note", "", 0, 4.4))

dy = 0.99 / sum(w for *_, w in rows)
y = 0.995
for kind, label, n, w in rows:
    if kind == "title":
        lax.text(0.0, y, label, fontsize=11.5, fontweight="bold", va="top")
    elif kind == "head":
        lax.text(0.0, y - dy * 0.55, "%s   (%d devices)" % (label, n),
                 fontsize=10, fontweight="bold", va="center")
    elif kind == "item":
        cy = y - dy * 0.5
        lax.add_patch(plt.Rectangle((0.045, cy - dy * 0.34), 0.06, dy * 0.68,
                                    facecolor=COLOR[label], edgecolor="#1a1a1a", lw=0.6))
        lax.text(0.125, cy, "%-9s x%d" % (label, n), fontsize=9, va="center",
                 family="monospace")
        idx = sorted(p["i"] for p in placement if p["key"] == label)
        lax.text(0.125, cy - dy * 0.72, "#" + " ".join("%d" % i for i in idx),
                 fontsize=7, va="center", color="#555555", family="monospace")
    elif kind == "note":
        lax.text(0.0, y - dy * 0.4,
                 "Shade within a colour = barcode variant;\n"
                 "the darkest is the first of that kind's four.\n\n"
                 "Grey blobs are the template's alignment marks.\n"
                 "Nothing may be placed on them.\n\n"
                 "Device numbers match placement.json, written\n"
                 "by 03_export_wafer.py from the same deal that\n"
                 "built the GDS.",
                 fontsize=8, va="top", color="#333333")
    y -= dy * w

fig.savefig(C.MAP_PATH, dpi=200, bbox_inches="tight", facecolor="white")
print("wrote %s" % C.MAP_PATH)
print("  %d devices, %d variants" % (len(placement), len(counts)))
for k in KINDS:
    print("  %-6s %s" % (k, "  ".join("%s x%d" % (v, counts.get(v, 0))
                                      for v in variants[k])))
