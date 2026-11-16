"""What a review column shows: one design's polygons per FAB layer, plus the LayerSet that names and colours them.

    interconnect(design, rev)   the FINAL file of a mea1k_interconnects design (routed DXF / dummy GDS)
    probe(name)                 a ufte_probes electrode bundle DXF (a geometry "U4" or a variant "U4C08")
    wafer(tag)                  a wafer GDS (maskgen/wafer/specs.py), or a reference wafer (REFERENCE_WAFERS)

Polygons are lists of rings, all in um: [outer, hole, hole, ...]. Most have a single ring. A DXF
HATCH becomes one polygon with holes; LWPOLYLINEs and GDS polygons have no holes. Layers are keyed
by fab layer number (the LayerSet's dxf_map / dummy_map). A layer the LayerSet does not map is not on
any mask, so it is dropped; `Column.dropped` counts it. Frames:

  interconnect DXF   point-reflected (x, y) -> (-x, -y). Stage 02 turned the stage-01 pad grid by
                     180 deg; this turns it back, so the pads lie below the wire band and the via of a
                     pad is top-left. The dummy GDS is already in that frame.
  probe              as drawn: shank tip at the bottom, solder pads at the top.
  wafer              wafer coordinates. Only the TOP cell's own polygons are read (the template:
                     outline + alignment marks), not the placed devices.

A missing input raises Missing; its .build says which command makes it.
"""
import os
import sys

import numpy as np

from maskgen.core import layers as L
from maskgen.core import paths

PROBE_SET = "64Ch_4Shankdesigns"
REFERENCE_WAFERS = {        # tag -> (file in the repo, LayerSet key)
    "rev2_fabricated": ("mea1k_interconnects/rev2_reference/MEA1K_wafer4_12x_8x_8x_breakoutboards_Rev2.gds", "interconnect_rev2"),
}


class Missing(Exception):
    def __init__(self, path, build):
        Exception.__init__(self, "%s does not exist -- build it with: %s" % (path, " ".join(build[1])))
        self.path, self.build = path, build      # build = (cwd, argv)


class Column(object):
    """One column of a sheet. `group` ties revisions of one design together (--xor compares inside it)."""

    def __init__(self, label, family, layers, layerset, files, group=None, kind=None, dropped=None):
        self.label, self.family, self.layerset, self.files = label, family, layerset, list(files)
        self.layers = {k: v for k, v in layers.items() if v}
        self.group, self.kind, self.dropped = group or label, kind, dropped or {}
        self.cache = {}                          # rois.py keeps per-column measurements here
        self._bb = {k: np.array([[r[0][:, 0].min(), r[0][:, 1].min(), r[0][:, 0].max(), r[0][:, 1].max()]
                                 for r in v]) for k, v in self.layers.items()}

    def rings(self, gds):
        """Every polygon on fab layer `gds`, as its ring list."""
        return self.layers.get(gds, [])

    def outer(self, gds):
        """The outer ring of every polygon on fab layer `gds`."""
        return [r[0] for r in self.layers.get(gds, [])]

    def bbox(self, gds):
        """(n, 4) array of x0 y0 x1 y1 per polygon on `gds`."""
        return self._bb.get(gds, np.zeros((0, 4)))

    def in_window(self, gds, win):
        """The polygons on `gds` whose bbox meets win = (x0, y0, x1, y1)."""
        bb = self._bb.get(gds)
        if bb is None or not len(bb):
            return []
        hit = (bb[:, 2] >= win[0]) & (bb[:, 0] <= win[2]) & (bb[:, 3] >= win[1]) & (bb[:, 1] <= win[3])
        polys = self.layers[gds]
        return [polys[i] for i in np.nonzero(hit)[0]]

    def fab_layers(self):
        """The layers present, back -> front (LayerSet order, then anything else by number)."""
        rank = {g: i for i, g in enumerate(self.layerset.order)}
        return sorted(self.layers, key=lambda g: (rank.get(g, len(rank)), g))


# ----------------------------------------------------------------------------- readers
def _ring_area(r):
    x, y = r[:, 0], r[:, 1]
    return 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _inside(inner, outer):
    """Is ring `inner` inside ring `outer`? (Its first vertex, even-odd test.)"""
    px, py = inner[0]
    x, y = outer[:, 0], outer[:, 1]
    x2, y2 = np.roll(x, -1), np.roll(y, -1)
    cross = (y > py) != (y2 > py)
    with np.errstate(divide="ignore", invalid="ignore"):
        xi = x + (py - y) * (x2 - x) / (y2 - y)
    return bool(np.count_nonzero(cross & (px < xi)) % 2)


def _hatch_polys(h):
    """A HATCH's boundary paths as polygons with holes (the largest ring first, rings inside it are holes)."""
    rings = []
    for p in h.paths:
        if hasattr(p, "vertices"):                         # polyline boundary path
            v = np.array([(q[0], q[1]) for q in p.vertices], float)
        else:                                              # edge path: flatten
            from ezdxf.path import from_hatch_edge_path
            v = np.array([(q.x, q.y) for q in from_hatch_edge_path(p).flattening(0.05)], float)
        if len(v) >= 3:
            rings.append(v)
    rings.sort(key=_ring_area, reverse=True)
    out = []
    for r in rings:
        for poly in out:
            if _inside(r, poly[0]):
                poly.append(r)
                break
        else:
            out.append([r])
    return out


def read_dxf(path, layerset, reflect=False):
    """{fab layer: [rings, ...]} from a DXF (LWPOLYLINE + HATCH), and {dropped DXF layer: count}."""
    import ezdxf
    dmap = layerset.dxf_map
    out, dropped = {}, {}
    s = -1.0 if reflect else 1.0
    for e in ezdxf.readfile(path).modelspace():
        t = e.dxftype()
        if t not in ("LWPOLYLINE", "HATCH"):
            continue
        g = dmap.get(e.dxf.layer.lower())
        if g is None:
            dropped[e.dxf.layer] = dropped.get(e.dxf.layer, 0) + 1
            continue
        if t == "LWPOLYLINE":
            v = np.array([(p[0], p[1]) for p in e.get_points()], float)
            polys = [[v]] if len(v) >= 2 else []
        else:
            polys = _hatch_polys(e)
        out.setdefault(g[0], []).extend([[r * s for r in p] for p in polys])
    return out, dropped


def read_gds(path, layer_map=None, top_only=False):
    """{fab layer: [rings, ...]} from a GDS/OAS. `layer_map` (layer, datatype) -> (fab, 0); None keeps
    the layer numbers. Flattened unless `top_only`."""
    import gdstk
    lib = gdstk.read_oas(path) if path.lower().endswith(".oas") else gdstk.read_gds(path)
    top = lib.top_level()[0]
    polys = top.polygons if top_only else top.get_polygons()
    out, dropped = {}, {}
    for p in polys:
        k = (p.layer, p.datatype)
        g = layer_map.get(k) if layer_map is not None else k
        if g is None:
            dropped["%d/%d" % k] = dropped.get("%d/%d" % k, 0) + 1
            continue
        out.setdefault(g[0], []).append([np.array(p.points, float)])
    return out, dropped


# ----------------------------------------------------------------------------- columns
def _ic_catalog():
    ic = os.path.join(paths.REPO, "mea1k_interconnects")
    if ic not in sys.path:
        sys.path.insert(0, ic)
    import catalog
    return catalog


def interconnect_designs():
    return list(_ic_catalog().DESIGNS)


def interconnect(design, rev, root=None):
    cat = _ic_catalog()
    if design not in cat.DESIGNS:
        raise SystemExit("unknown design %r; known: %s" % (design, ", ".join(cat.DESIGNS)))
    if rev not in cat.REVISIONS:
        raise SystemExit("unknown revision %r; known: %s" % (rev, ", ".join(map(str, cat.REVISIONS))))
    ls = L.LAYERSETS[cat.REVISIONS[rev].layerset]
    path = os.path.join(paths.interconnect_dir(rev, root, create=False), cat.file_names(design, rev)[1])
    if not os.path.exists(path):
        raise Missing(path, (os.path.join(paths.REPO, "mea1k_interconnects"),
                             [sys.executable, "build_designs.py", "--rev", str(rev)]))
    if path.lower().endswith(".gds"):
        layers, dropped = read_gds(path, ls.dummy_map)
        kind = "dummy"
    else:
        layers, dropped = read_dxf(path, ls, reflect=True)
        kind = "routed"
    return Column("%s Rev%d" % (design, rev), "interconnect", layers, ls, [path], group=design, kind=kind,
                  dropped=dropped)


def probe_designs(design_set=PROBE_SET, root=None):
    """The geometries (U1.6, ...) and barcoded variants (U1.6C00, ...) present in the output root."""
    d = paths.electrode_dir(design_set, root, create=False)
    names = sorted(f[len("electrode_bundle_"):-4] for f in (os.listdir(d) if os.path.isdir(d) else ())
                   if f.startswith("electrode_bundle_") and f.endswith(".dxf"))
    return names


def probe(name, design_set=PROBE_SET, root=None):
    path = os.path.join(paths.electrode_dir(design_set, root, create=False), "electrode_bundle_%s.dxf" % name)
    if not os.path.exists(path):
        raise Missing(path, (os.path.join(paths.REPO, "ufte_probes"),
                             [sys.executable, "-m", "electrode_bundle.batch", "--no-render"]))
    layers, dropped = read_dxf(path, L.PROBE_64CH)
    return Column(name, "probe", layers, L.PROBE_64CH, [path], group=name, kind="probe", dropped=dropped)


def wafer(tag, root=None):
    from maskgen.wafer import specs
    if tag in REFERENCE_WAFERS:
        f, ls = REFERENCE_WAFERS[tag]
        path, ls = os.path.join(paths.REPO, f), L.LAYERSETS[ls]
    elif tag in specs.WAFERS:
        path = os.path.join(paths.wafer_dir(tag, root, create=False), "wafer_%s.gds" % tag)
        ls = L.LAYERSETS[specs.WAFERS[tag]["layerset"]]
        if not os.path.exists(path):
            raise Missing(path, (paths.REPO, [sys.executable, "-m", "maskgen", "wafer", tag]))
    else:
        raise SystemExit("unknown wafer %r; known: %s" % (tag, ", ".join(list(specs.WAFERS) + list(REFERENCE_WAFERS))))
    layers, dropped = read_gds(path, top_only=True)
    return Column(tag, "wafer", layers, ls, [path], group=tag, kind="wafer", dropped=dropped)
