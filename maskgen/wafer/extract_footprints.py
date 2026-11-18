"""Stage 01 -- design DXF  ->  footprint polygon (+ centroid offset).

For each design in config.DESIGNS, extracts the outer silhouette and writes it centred
on its own centroid, plus the offset needed to put it back. For each kind that groups
more than one design, also writes the union ENVELOPE those designs are nested against.

Nothing here touches the wafer. The footprint is only a collision proxy for stage 02;
the real geometry is placed from the original DXF in stage 03.

RUN WITH SYSTEM PYTHON (ezdxf + shapely + matplotlib):
    python3 maskgen/wafer/extract_footprints.py

CHECK BEFORE MOVING ON: the printed area and bbox must match the design you expect,
and the overlay PNG must show a single closed outline with no spurious lobes.
"""
import json, os
from shapely.geometry import Polygon
from shapely.ops import unary_union
from shapely.affinity import translate
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

import sys, importlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))   # repo root: maskgen
from maskgen.wafer.configs import load as _load_config   # noqa: E402
C = _load_config('config_64ch_4shank')
from maskgen.wafer.footprints import extract, extract_outline

# PI_BAND_UM (config): nest on the polyimide OUTLINE + half the cutout band, so neighbours' cutout bands
# may overlap (footprints.extract_outline). Without it: the whole silhouette, band included.
BAND_UM = getattr(C, "PI_BAND_UM", None)

os.makedirs(C.NEST_DIR, exist_ok=True)

print("run tag   : %s" % C.RUN_TAG)
print("out dir   : %s" % C.NEST_DIR)
print()

shapes = {}
SHARE = getattr(C, "VARIANTS_SHARE_CENTRE", False) and BAND_UM
# --keep-frames (maskgen wafer on an existing nest): a design that already has an offset keeps it -- its poses
# were nested in that frame, even if its outline has changed since (only ever shrunk, or nest again)
KEEP = "--keep-frames" in sys.argv[1:]
first_centre = {}                       # kind -> (cx, cy) of its first design (VARIANTS_SHARE_CENTRE)
for d in C.DESIGNS:
    dxf = C.design_dxf(d)
    if not os.path.exists(dxf):
        raise SystemExit("missing DXF: %s" % dxf)
    if BAND_UM:
        fp, cx, cy, outline, sil = extract_outline(dxf, BAND_UM * 1e-3)
        frame = None
        if KEEP and os.path.exists(C.offset_path(d["key"])):
            o = json.load(open(C.offset_path(d["key"])))
            frame = (o["cx"], o["cy"])
        elif SHARE and d["kind"] in first_centre:
            # every variant of a kind in its first design's frame: the same pose puts all of them in one place
            frame = first_centre[d["kind"]]
        if frame:
            fp, outline, sil = (translate(g, cx - frame[0], cy - frame[1]) for g in (fp, outline, sil))
            cx, cy = frame
        first_centre.setdefault(d["kind"], (cx, cy))
        # the true shapes, for the nester's exact test (a band may not reach a neighbour's outline)
        json.dump({"outline": list(outline.exterior.coords), "silhouette": list(sil.exterior.coords)},
                  open(C.footprint_path(d["key"]).replace(".json", "_true.json"), "w"))
    else:
        fp, cx, cy = extract(dxf, close_gap=C.CLOSE_GAP, simplify=C.SIMPLIFY)
    mnx, mny, mxx, mxy = fp.bounds
    json.dump(list(fp.exterior.coords), open(C.footprint_path(d["key"]), "w"))
    json.dump({"cx": cx, "cy": cy}, open(C.offset_path(d["key"]), "w"))
    shapes[d["key"]] = fp
    print("%-14s kind=%-6s x%d  area=%8.3f mm2  bbox=%7.3f x %7.3f mm  "
          "centroid=(%9.3f, %9.3f)  %d verts"
          % (d["key"], d["kind"], d["count"], fp.area,
             mxx - mnx, mxy - mny, cx, cy, len(fp.exterior.coords)))

# --- per-kind envelopes -------------------------------------------------------
print()
for kind in C.kinds():
    ds = C.designs_of(kind)
    if len(ds) == 1:
        print("kind %-6s: single design %s, nested against its own footprint"
              % (kind, ds[0]["key"]))
        continue
    env = unary_union([shapes[d["key"]] for d in ds])
    if not SHARE:                       # shared frame: the plain union (= the first's when it covers the rest)
        env = env.buffer(C.ENVELOPE_PAD).simplify(C.ENVELOPE_SIMPLIFY)
    if env.geom_type == "MultiPolygon":
        env = max(env.geoms, key=lambda g: g.area)
    env = Polygon(env.exterior)
    json.dump(list(env.exterior.coords), open(C.kind_footprint_path(kind), "w"))
    mnx, mny, mxx, mxy = env.bounds
    print("kind %-6s: envelope of %d designs  area=%8.3f mm2  bbox=%7.3f x %7.3f mm"
          % (kind, len(ds), env.area, mxx - mnx, mxy - mny))
    for d in ds:
        s = shapes[d["key"]]
        ok = env.covers(s)
        print("    covers %-14s %-5s  (outside envelope %.6f mm2)"
              % (d["key"], ok, s.difference(env).area))
        if not ok and s.difference(env).area > 1e-4:
            raise SystemExit("envelope does not cover %s -- pieces would collide" % d["key"])
    waste = env.area - min(shapes[d["key"]].area for d in ds)
    print("    worst-case wasted area per slot: %.3f mm2 -- if this is large the "
          "designs are too dissimilar to share a kind" % waste)

# --- overlay plot -------------------------------------------------------------
n = len(C.DESIGNS)
fig, axes = plt.subplots(1, n, figsize=(5 * n, 7), squeeze=False)
for ax, d in zip(axes[0], C.DESIGNS):
    fp = shapes[d["key"]]
    ax.plot(*fp.exterior.xy, "r-", lw=1.2, label="footprint")
    env_path = C.kind_footprint_path(d["kind"])
    if len(C.designs_of(d["kind"])) > 1 and os.path.exists(env_path):
        env = Polygon(json.load(open(env_path)))
        ax.plot(*env.exterior.xy, "b--", lw=1.0, label="kind envelope")
    ax.set_aspect("equal"); ax.grid(alpha=.3); ax.legend(fontsize=8)
    ax.set_title("%s  (%.1f mm2)" % (d["key"], fp.area), fontsize=9)
plt.tight_layout()
plt.savefig(C.OVERLAY_PATH, dpi=120)
print("\nwrote footprints + %s" % os.path.basename(C.OVERLAY_PATH))
