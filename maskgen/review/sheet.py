"""Draw a review sheet: rows = features (rois.py), columns = designs / revisions (sources.py).

    sheet.build(columns, rois, folder, title, xor=False) -> [written files]

Writes sheet.png (every row), sheet.pdf (one page per row, larger) and index.md (rows, columns,
sources, per-cell measurements) into `folder`.
- Every cell of a row is a window of the same size in um, centred on that column's feature: one scale
  per row. A scale bar sits bottom-left in every cell; the grid is in window-local um.
- Colours and the back-to-front order are the column's LayerSet (maskgen/core/layers.py). Shapes are
  translucent, so overlaps show.
- --xor: every column after the first of its group (the revisions of one design) or, with no group,
  after the first column, gets a black outline where its geometry differs from that reference.
  The difference is per fab layer, in window-local coordinates, i.e. relative to the feature. The
  index lists its area per layer.
"""
import math
import os
import time

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                     # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402
from matplotlib.patches import Patch, PathPatch, Rectangle  # noqa: E402
from matplotlib.path import Path                    # noqa: E402

GREY = "#808080"
ALPHA = 0.42
METAL = 7                       # the first metal in every LayerSet: the gap metric is measured on it


# ----------------------------------------------------------------------------- geometry helpers
def _path(rings, dx=0.0, dy=0.0):
    """A matplotlib Path for a polygon with holes (outer counter-clockwise, holes clockwise)."""
    verts, codes = [], []
    for i, r in enumerate(rings):
        r = r - (dx, dy)
        x, y = r[:, 0], r[:, 1]
        ccw = (np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) > 0
        if ccw != (i == 0):
            r = r[::-1]
        verts += list(r) + [r[0]]
        codes += [Path.MOVETO] + [Path.LINETO] * (len(r) - 1) + [Path.CLOSEPOLY]
    return Path(verts, codes)


def _shapely(rings_list, win, dx, dy):
    """The union of polygons clipped to `win`, shifted by (-dx, -dy)."""
    from shapely.geometry import Polygon, box
    from shapely.ops import unary_union
    from shapely import affinity
    b = box(*win)
    parts = []
    for rings in rings_list:
        if len(rings[0]) < 3:
            continue
        g = Polygon(rings[0], [r for r in rings[1:] if len(r) >= 3]).buffer(0).intersection(b)
        if not g.is_empty:
            parts.append(g)
    u = unary_union(parts) if parts else Polygon()
    return affinity.translate(u, -dx, -dy)


def min_gap(col, win):
    """Smallest distance between two separate metal shapes inside the window. Pieces of one wire that
    touch, or butt end to end with a float-noise gap (< 1 nm, e.g. stage 01's wire meeting stage 02's band),
    count as one shape. inf if fewer than two."""
    from shapely.ops import unary_union
    u = _shapely(col.in_window(METAL, win), win, 0, 0)
    u = unary_union([p.buffer(5e-4) for p in (getattr(u, "geoms", None) or [u]) if not p.is_empty])
    polys = [p for p in (getattr(u, "geoms", None) or [u]) if not p.is_empty and p.area > 1]
    g = float("inf")
    for i, a in enumerate(polys):
        for b in polys[i + 1:]:
            if (a.bounds[0] > b.bounds[2] + 10 or b.bounds[0] > a.bounds[2] + 10
                    or a.bounds[1] > b.bounds[3] + 10 or b.bounds[1] > a.bounds[3] + 10):
                continue
            g = min(g, a.distance(b) + 1e-3)               # undo the 2 x 0.5 nm grow
    return g


def xor(col, win, ref, rwin):
    """{fab layer: (area um2, shapely geometry in col's frame)} where col and ref differ, both relative to
    their own window."""
    out = {}
    cx, cy = (win[0] + win[2]) / 2, (win[1] + win[3]) / 2
    rx, ry = (rwin[0] + rwin[2]) / 2, (rwin[1] + rwin[3]) / 2
    from shapely import affinity
    for g in sorted(set(col.layers) | set(ref.layers)):
        a = _shapely(col.in_window(g, win), win, cx, cy)
        b = _shapely(ref.in_window(g, rwin), rwin, rx, ry)
        d = a.symmetric_difference(b)
        if d.area > 1e-4:
            out[g] = (d.area, affinity.translate(d, cx, cy))
    return out


def _nice(x):
    """1, 2, 5 x 10^n at or below x."""
    e = 10 ** math.floor(math.log10(x))
    return max(m * e for m in (1, 2, 5) if m * e <= x)


# ----------------------------------------------------------------------------- layout
class Row(object):
    def __init__(self, roi, boxes):
        self.roi, self.boxes = roi, boxes                  # boxes[i]: column i's feature box or None
        hits = [b for b in boxes if b is not None]
        w = max(b[2] - b[0] for b in hits) + 2 * roi.margin
        h = max(b[3] - b[1] for b in hits) + 2 * roi.margin
        self.w, self.h = max(w, roi.size[0]), max(h, roi.size[1])
        self.wins = [None if b is None else
                     ((b[0] + b[2]) / 2 - self.w / 2, (b[1] + b[3]) / 2 - self.h / 2,
                      (b[0] + b[2]) / 2 + self.w / 2, (b[1] + b[3]) / 2 + self.h / 2) for b in boxes]
        self.gap, self.diff = [None] * len(boxes), [None] * len(boxes)

    def cell_in(self, zoom):
        k = self.roi.cell * zoom / max(self.w, self.h)       # inches per um, the row's scale
        return self.w * k, self.h * k


def locate(columns, rois):
    rows, skipped = [], []
    for roi in rois:
        boxes = []
        for c in columns:
            try:
                b = roi.find(c)
            except (ValueError, IndexError):                 # the column lacks what the rule needs
                b = None
            boxes.append(None if b is None else tuple(float(v) for v in b))
        if any(b is not None for b in boxes):
            rows.append(Row(roi, boxes))
        else:
            skipped.append(roi.name)
    return rows, skipped


def _refs(columns):
    """Column index each column is XOR-ed against (None for a reference itself)."""
    first, out = {}, []
    groups = [c.group for c in columns]
    for i, c in enumerate(columns):
        if groups.count(c.group) > 1:
            out.append(first.get(c.group))
            first.setdefault(c.group, i)
        else:
            out.append(None if i == 0 else 0)
    return out


def measure(columns, rows, do_xor):
    refs = _refs(columns)
    for row in rows:
        for i, c in enumerate(columns):
            win = row.wins[i]
            if win is None:
                continue
            if row.roi.metric:
                row.gap[i] = min_gap(c, win)
            r = refs[i]
            if do_xor and r is not None and row.wins[r] is not None:
                row.diff[i] = (r, xor(c, win, columns[r], row.wins[r]))
    return refs


def _style(col, g):
    st = col.layerset.style.get(g)
    return st["color"] if st else GREY


def draw_cell(ax, col, row, i):
    win = row.wins[i]
    ax.set_xticks([])
    ax.set_yticks([])
    if win is None:
        ax.set_facecolor("#f4f4f4")
        ax.text(0.5, 0.5, "not in this design", ha="center", va="center", fontsize=8, color="#888",
                transform=ax.transAxes)
        return
    for z, g in enumerate(col.fab_layers()):
        colr = _style(col, g)
        for rings in col.in_window(g, win):
            ax.add_patch(PathPatch(_path(rings), fc=colr, ec=colr, alpha=ALPHA, lw=0.5, zorder=z + 1))
    if row.diff[i]:
        for g, (area, geom) in row.diff[i][1].items():
            for p in getattr(geom, "geoms", None) or [geom]:
                if p.geom_type != "Polygon" or p.is_empty:
                    continue
                rings = [np.array(p.exterior.coords)] + [np.array(h.coords) for h in p.interiors]
                ax.add_patch(PathPatch(_path(rings), fc="none", ec="k", lw=0.9, ls=(0, (3, 2)), zorder=50))
    ax.set_xlim(win[0], win[2])
    ax.set_ylim(win[1], win[3])
    ax.set_aspect("equal")
    step = _nice(row.w / 6)
    for v in np.arange(step, row.w, step):
        ax.axvline(win[0] + v, color="k", alpha=0.07, lw=0.5, zorder=0)
    for v in np.arange(step, row.h, step):
        ax.axhline(win[1] + v, color="k", alpha=0.07, lw=0.5, zorder=0)
    bar = _nice(row.w / 4)
    x0, y0 = win[0] + row.w * 0.04, win[1] + row.h * 0.04
    ax.add_patch(Rectangle((x0, y0), bar, row.h * 0.012, color="k", zorder=60))
    ax.text(x0, y0 + row.h * 0.03, "%g um" % bar, fontsize=7, zorder=60)


def _cell_title(col, row, i, columns):
    t = "%s -- %s" % (col.label, row.roi.name)
    extra = []
    if row.gap[i] is not None and row.wins[i] is not None:
        extra.append("min metal gap %s" % ("%.2f um" % row.gap[i] if row.gap[i] < 1e8 else "-"))
    if row.diff[i] is not None:
        r, d = row.diff[i]
        extra.append("XOR vs %s: %s" % (columns[r].label, "%.1f um2" % sum(a for a, _ in d.values()) if d else "none"))
    return t + ("\n" + "; ".join(extra) if extra else "")


def figure(columns, rows, title, zoom=1.0):
    gap_x, gap_y, title_h = 0.35, 0.25, 0.42
    head, foot = 1.15, 0.25
    left = max(1.9, 0.35 + 0.085 * max(len(r.roi.name) for r in rows))   # room for the row labels
    cw = max(r.cell_in(zoom)[0] for r in rows)
    colw = max(cw, 1.9)
    fw = left + len(columns) * (colw + gap_x) + 0.2
    rh = [r.cell_in(zoom)[1] + title_h + gap_y for r in rows]
    fh = head + sum(rh) + foot
    fig = plt.figure(figsize=(fw, fh))
    fig.text(0.2 / fw, 1 - 0.3 / fh, title, fontsize=14, va="top")
    # legend: every (fab layer, name, colour) shown
    seen = {}
    for c in columns:
        for g in c.fab_layers():
            seen.setdefault((g, c.layerset.names.get(g, "unnamed")), _style(c, g))
    handles = [Patch(fc=v, ec=v, alpha=ALPHA + 0.2, label="%s (%d)" % (n, g)) for (g, n), v in seen.items()]
    if any(d is not None for r in rows for d in r.diff):
        handles.append(Patch(fc="none", ec="k", ls="--", label="differs from the reference column"))
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.2 / fw, 1 - 0.62 / fh),
               ncol=min(len(handles), 8), fontsize=8, frameon=False)
    y = fh - head
    for r, h in zip(rows, rh):
        w_in, h_in = r.cell_in(zoom)
        y -= h
        fig.text(0.15 / fw, (y + gap_y + h_in / 2) / fh, "%s\n%.0f x %.0f um" % (r.roi.name, r.w, r.h),
                 fontsize=10, va="center", weight="bold")
        for i, c in enumerate(columns):
            x = left + i * (colw + gap_x) + (colw - w_in) / 2
            ax = fig.add_axes([x / fw, (y + gap_y) / fh, w_in / fw, h_in / fh])
            draw_cell(ax, c, r, i)
            ax.set_title(_cell_title(c, r, i, columns), fontsize=7.5)
    return fig


# ----------------------------------------------------------------------------- output
def _index(columns, rows, skipped, refs, title, files):
    out = ["# %s" % title, "", "Generated %s by `maskgen review`. Files: %s." % (
        time.strftime("%Y-%m-%d %H:%M"), ", ".join("`%s`" % os.path.basename(f) for f in files)), "",
        "## Columns", "", "| # | column | layer set | source |", "|---|---|---|---|"]
    for i, c in enumerate(columns):
        out.append("| %d | %s | %s | `%s` |" % (i + 1, c.label, c.layerset.key, c.files[0]))
    out += ["", "Each row is one feature, at one scale. The window is centred on each column's own copy of "
            "the feature (found in its geometry, see maskgen/review/rois.py).", ""]
    for r in rows:
        out += ["## %s (%.0f x %.0f um)" % (r.roi.name, r.w, r.h), "", r.roi.note, ""]
        heads = ["column", "feature centre (um)"]
        if r.roi.metric:
            heads.append("min metal gap (um)")
        if any(d is not None for d in r.diff):
            heads.append("XOR vs reference, um2 per fab layer")
        out += ["| " + " | ".join(heads) + " |", "|" + "---|" * len(heads)]
        for i, c in enumerate(columns):
            b = r.boxes[i]
            cells = [c.label, "-" if b is None else "%.2f, %.2f" % ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)]
            if r.roi.metric:
                cells.append("-" if r.gap[i] is None or r.gap[i] > 1e8 else "%.3f" % r.gap[i])
            if any(d is not None for d in r.diff):
                if r.diff[i] is None:
                    cells.append("(reference)" if refs[i] is None and b is not None else "-")
                else:
                    ref, d = r.diff[i]
                    cells.append("vs %s: %s" % (columns[ref].label, ", ".join(
                        "%s %.2f" % (c.layerset.names.get(g, g), a) for g, (a, _) in sorted(d.items())) or "none"))
            out.append("| " + " | ".join(cells) + " |")
        out.append("")
    if skipped:
        out += ["Not in any column (no row drawn): %s." % ", ".join(skipped), ""]
    dropped = {k: v for c in columns for k, v in c.dropped.items()}
    if dropped:
        out += ["Layers not on any mask, not drawn: %s." % ", ".join(sorted(dropped)), ""]
    return "\n".join(out) + "\n"


def build(columns, rois, folder, title, do_xor=False, dpi=110):
    rows, skipped = locate(columns, rois)
    if not rows:
        raise SystemExit("none of the features %s is in any column" % ", ".join(r.name for r in rois))
    refs = measure(columns, rows, do_xor)
    os.makedirs(folder, exist_ok=True)
    png, pdf, md = (os.path.join(folder, f) for f in ("sheet.png", "sheet.pdf", "index.md"))
    fig = figure(columns, rows, title)
    fig.savefig(png, dpi=dpi)
    plt.close(fig)
    with PdfPages(pdf) as pp:
        for r in rows:
            fig = figure(columns, [r], "%s -- %s" % (title, r.roi.name), zoom=1.8)
            pp.savefig(fig)
            plt.close(fig)
    with open(md, "w") as f:
        f.write(_index(columns, rows, skipped, refs, title, (png, pdf)))
    return [png, pdf, md], rows, skipped
