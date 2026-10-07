"""Nest as many devices as possible onto the wafer.

Reads the silhouettes 01_extract_footprints.py produced and writes poses.json for
03_export_wafer.py. The piece count is the OBJECTIVE, not an input: the config's counts
are read only as a RATIO, and this fills the wafer and reports how many fit.

The tiling principle
--------------------
Each design is a "sword": a 3.01 mm handle (~13 mm long), a 2.06 mm shank, a 1.72 mm
neck, then a taper to a point. Rotating a copy 180 deg puts a handle against a tip, so a
flipped stack packs at 2.66 mm pitch instead of 3.46 mm -- measured, not assumed, by
`report_interlock()`, which prints it at the top of every run. That 23 % is the whole
reason this is worth doing, and if a redesign changes the silhouette the number moves
with it.

Rather than hard-code that lattice, the packer DISCOVERS it: pieces are dropped one at a
time onto an exact skyline, and a flipped piece simply falls lower into its neighbour's
taper, so the interlocked pattern emerges on its own. Unlike a rigid lattice it also
follows the wafer's curvature and mixes kinds of different lengths freely -- worth more
than it sounds, since a flat-row lattice at the same pitch computes out ~5 pieces worse.

The grain direction is a per-trial choice, not a per-pass one. A cross-grain pass that
filled the leftover strip between rows was tried and removed: measured over 24 calls it
seated exactly zero pieces, because the skyline's staircase never leaves a pocket wide
enough for a whole 37 mm shank. Multi-start over phi is what covers the two grains.

On top sits the search: randomised multi-start over grain, fill order and mix policy,
plus ruin-and-recreate (tear a tower out of the best layout, re-grow it differently, keep
it if it is no worse).

Run:
    export WAFERNEST_CONFIG=config_64ch_4shank
    python3 WaferNesting/nest_tiler.py --time 900

Writes poses.json + nest_report.json and refreshes nest_preview.png every time the record
improves, so the PNG can be watched live. Nothing is written that has not passed a full
re-check against the true polygons: zero overlap, inside the usable radius, off the marks.
"""
import argparse, importlib, json, math, os, random, sys, time
import numpy as np
from shapely.geometry import Polygon, Point, box
from shapely.affinity import rotate as shp_rotate, translate as shp_translate
from shapely.ops import unary_union
from shapely.strtree import STRtree
from shapely import wkt as shp_wkt
from shapely.prepared import prep as shp_prep
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MplPoly

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
C = importlib.import_module(os.environ.get("WAFERNEST_CONFIG", "config_64ch_4shank"))

# Grid step for the skyline and for the piece profiles. Everything the packer PROPOSES is
# quantised to this; everything it ACCEPTS is re-checked against the true polygons, so
# this trades speed for tightness only, never for correctness.
DX = 0.025
# Profile quantisation: bottoms round down, tops round up, so the run-length encoding
# below is conservative. Also what keeps the rect count near ~120 instead of ~1600.
QZ = 0.01
# Two pieces count as overlapping only above this area. With GAP = 0 the packer deliberately
# drives pieces into contact, and exact boundary contact is legal -- see README.md, "Things that will bite you" #7.
OVERLAP_TOL = 1e-6


# --- wafer geometry -----------------------------------------------------------
R0, FLAT0 = C.WAFER_R, C.WAFER_FLAT_Y
GAP = 0.0      # mm between devices. Zero by default: they may touch. --gap overrides.

def _regions(edge_excl):
    """Usable disc for a given edge exclusion, plus the epsilon-grown copy the accept
    check uses. Rebuildable so --edge can change the exclusion at runtime."""
    r = R0 - edge_excl
    f = FLAT0 + edge_excl
    u = Point(0, 0).buffer(r, resolution=256).intersection(box(-r - 1, f, r + 1, r + 1))
    return r, f, u, u.buffer(1e-7)


def set_edge(edge_excl):
    g = globals()
    g["EDGE_EXCL"], (g["R"], g["FLAT"], g["USABLE"], g["USABLE_EPS"]) = \
        edge_excl, _regions(edge_excl)


EDGE_EXCL = C.EDGE_EXCL
R, FLAT, USABLE, USABLE_EPS = _regions(EDGE_EXCL)
_raw_marks = shp_wkt.loads(open(C.MARKS_WKT).read())
_mark_parts = list(_raw_marks.geoms) if _raw_marks.geom_type == "MultiPolygon" else [_raw_marks]
# Buffer the 974 parts INDIVIDUALLY and union the results. Calling .buffer() on the
# MultiPolygon as a whole takes over two minutes here; this is 0.13 s for a geometry of
# identical area.
MARKS = unary_union([g.buffer(C.MARK_BUF, quad_segs=6) for g in _mark_parts]).simplify(0.01)
# The accept check runs once per candidate placement, so it must not touch
# USABLE.difference(MARKS): that is one polygon with ~970 holes and buffering or
# differencing it costs more than the whole drop test. Kept as two cheap tests instead --
# containment in a simple ring, plus a prepared point-set query against the marks.
MARKS_PREP = shp_prep(MARKS)

KINDS = list(C.kinds())
FP = {}
for _k in KINDS:
    _p = C.kind_footprint_path(_k)
    if not os.path.exists(_p):
        raise SystemExit("missing %s -- run 01_extract_footprints.py first" % _p)
    FP[_k] = Polygon(json.load(open(_p)))

# Desired mix. SHANK_RATIO is the authority when the config states one, because
# SHANK_COUNTS gets overwritten with each run's result -- pasting 22:15:14:9 back in would
# make the NEXT run target 36.7/25/23.3/15 instead of the intended 37.5/25/25/12.5, and the
# mix would drift a little further every time. Configs without SHANK_RATIO fall back to
# the counts.
_mix = getattr(C, "SHANK_RATIO", None) or {k: C.count_of(k) for k in KINDS}
_missing = [k for k in KINDS if k not in _mix]
if _missing:
    raise SystemExit("SHANK_RATIO in %s.py is missing %s" % (C.__name__, ", ".join(_missing)))
_rsum = float(sum(_mix[k] for k in KINDS))
RATIO = {k: _mix[k] / _rsum for k in KINDS}


# --- profiles -----------------------------------------------------------------
def vline_extent(poly, xs):
    """Lower and upper y of `poly` on each vertical line x = xs[i].

    Analytic edge walk rather than 3760 shapely intersections per (kind, rotation):
    the footprints are simple rings of ~30 vertices, so this is two orders of magnitude
    faster and exact at the sample points.
    """
    c = np.asarray(poly.exterior.coords)
    ax, ay, bx, by = c[:-1, 0], c[:-1, 1], c[1:, 0], c[1:, 1]
    lo = np.full(xs.size, np.inf)
    hi = np.full(xs.size, -np.inf)
    for i in range(ax.size):
        x1, y1, x2, y2 = ax[i], ay[i], bx[i], by[i]
        if x1 == x2:
            continue                       # vertical edge: its endpoints are vertices,
        m = (xs > min(x1, x2)) & (xs < max(x1, x2))   # folded in by profile() below
        if not m.any():
            continue
        yy = y1 + (xs[m] - x1) * (y2 - y1) / (x2 - x1)
        lo[m] = np.minimum(lo[m], yy)
        hi[m] = np.maximum(hi[m], yy)
    return lo, hi


def rle(vals, support):
    """[(t0, t1, value)] runs of equal `value` over the supported bins."""
    out = []
    t = 0
    n = vals.size
    while t < n:
        if not support[t]:
            t += 1
            continue
        v = vals[t]
        t1 = t + 1
        while t1 < n and support[t1] and vals[t1] == v:
            t1 += 1
        out.append((t, t1, float(v)))
        t = t1
    return out


class Profile:
    """A piece at one rotation, reduced to what the skyline needs.

    bot/top are per-bin lower/upper edges on the DX grid, made conservative (bottom
    rounded down, top rounded up) and run-length encoded so a drop test costs ~120
    vectorised range queries instead of 1600.
    """

    def __init__(self, kind, rot):
        self.kind, self.rot = kind, rot
        self.poly = shp_rotate(FP[kind], rot, origin=(0, 0))
        b = self.poly.bounds
        self.origin = (b[0], b[1])                      # bin 0 starts at bbox minx
        self.T = int(math.ceil((b[2] - b[0]) / DX))
        self.len = b[2] - b[0]
        xe = b[0] + np.arange(self.T + 1) * DX
        eps = min(DX * 1e-3, (b[2] - b[0]) * 1e-9)
        lo, hi = vline_extent(self.poly, np.clip(xe, b[0] + eps, b[2] - eps))
        bot = np.minimum(lo[:-1], lo[1:])
        top = np.maximum(hi[:-1], hi[1:])
        # A vertex can dip below / rise above both sampled bin edges inside its own bin.
        # Folding every vertex into the bin it lands in (and its neighbour, for vertices
        # sitting on a bin boundary) makes bot/top exact for a piecewise-linear ring.
        for vx, vy in np.asarray(self.poly.exterior.coords):
            t = int((vx - b[0]) / DX)
            for tt in (t - 1, t, t + 1):
                if 0 <= tt < self.T:
                    bot[tt] = min(bot[tt], vy)
                    top[tt] = max(top[tt], vy)
        self.support = np.isfinite(bot) & np.isfinite(top)
        bot = np.where(self.support, np.floor(bot / QZ) * QZ, np.inf)
        top = np.where(self.support, np.ceil(top / QZ) * QZ, -np.inf)
        self.bot_rects = rle(bot, self.support)
        self.top_rects = rle(top, self.support)
        self.rel_bot = bot                              # for the exact placement offset


PROFILES = {}


def profile(kind, rot):
    key = (kind, rot)
    if key not in PROFILES:
        PROFILES[key] = Profile(kind, rot)
    return PROFILES[key]


# --- range-query tables -------------------------------------------------------
def sparse_max(a):
    tbl, k = [a], 1
    while (1 << k) <= a.size:
        prev, L = tbl[-1], 1 << (k - 1)
        tbl.append(np.maximum(prev[: prev.size - L], prev[L:]))
        k += 1
    return tbl


def sparse_min(a):
    tbl, k = [a], 1
    while (1 << k) <= a.size:
        prev, L = tbl[-1], 1 << (k - 1)
        tbl.append(np.minimum(prev[: prev.size - L], prev[L:]))
        k += 1
    return tbl


def rq(tbl, starts, length, fmax):
    k = max(int(length).bit_length() - 1, 0)
    L = 1 << k
    t = tbl[k]
    a, b = t[starts], t[starts + length - L]
    return np.maximum(a, b) if fmax else np.minimum(a, b)


# --- the packer ---------------------------------------------------------------
class Frame:
    """One grain direction: the usable region rotated so that pieces lie along +x and
    the packer drops them along +y."""

    def __init__(self, phi, region):
        self.phi = phi
        reg = shp_rotate(region, -phi, origin=(0, 0))
        self.region = reg
        b = reg.bounds
        self.x0 = b[0]
        self.M = int(math.ceil((b[2] - b[0]) / DX)) + 1
        xs = self.x0 + (np.arange(self.M) + 0.5) * DX
        # Per-column floor and roof. For a region that is y-convex at each x (a wafer, a
        # leftover strip) this is exact; where it is not, the exact accept check below is
        # what actually guarantees correctness.
        floor = np.full(self.M, np.inf)
        roof = np.full(self.M, -np.inf)
        for g in (reg.geoms if reg.geom_type == "MultiPolygon" else [reg]):
            lo, hi = vline_extent(g, xs)
            floor = np.minimum(floor, lo)
            roof = np.maximum(roof, hi)
        self.floor, self.roof = floor, roof
        self.roof_tbl = sparse_min(roof)


def to_world(phi, xf, yf):
    a = math.radians(phi)
    return xf * math.cos(a) - yf * math.sin(a), xf * math.sin(a) + yf * math.cos(a)


def place_poly(kind, theta, x, y):
    return shp_translate(shp_rotate(FP[kind], theta, origin=(0, 0)), x, y)


class Placement:
    """A seated piece: the pose 03_export_wafer.py needs, plus its world polygon."""
    __slots__ = ("kind", "theta", "x", "y", "geom")

    def __init__(self, kind, theta, x, y, geom):
        self.kind, self.theta, self.x, self.y, self.geom = kind, theta, x, y, geom

    def pose(self):
        return [self.kind, self.theta % 360, self.x, self.y]

    def reach(self):
        """How far the piece's outermost point sits from the wafer centre, in mm."""
        return max(math.hypot(px, py) for px, py in self.geom.exterior.coords)


def raise_sky(sky, frame, geom):
    """Fold one placed polygon into the skyline."""
    gf = shp_rotate(geom, -frame.phi, origin=(0, 0))
    gb = gf.bounds
    j0 = max(int((gb[0] - frame.x0) / DX) - 1, 0)
    j1 = min(int((gb[2] - frame.x0) / DX) + 2, frame.M)
    if j1 <= j0:
        return
    xs = frame.x0 + (np.arange(j0, j1) + 0.5) * DX
    _, hi = vline_extent(gf, xs)
    seg = sky[j0:j1]
    sky[j0:j1] = np.where(np.isfinite(hi), np.maximum(seg, hi + GAP), seg)


def build_sky(frame, placed):
    """Wafer floor raised by everything already seated. Pieces from an earlier pass are
    simply ground this pass has to stand on."""
    sky = frame.floor.copy()
    for pl in placed:
        raise_sky(sky, frame, pl.geom)
    return np.where(np.isfinite(frame.floor), sky, np.inf)


def spread(cand, topmax, pick, min_idx_sep, k=24):
    """`pick` plus the next-best candidates, forced to be far apart in x.

    Why spread and not simply the next 24 by height: the near-best positions are all
    within a bin or two of each other, so when the best one is refused -- almost always
    because it lands on an alignment mark -- its neighbours are refused for exactly the
    same reason. At phi = 270 that dead-ends the whole pass: every low candidate crosses
    the mark band at the wafer's left edge, and one-at-a-time blacklisting burned the
    entire reject budget without seating a single piece. Spread alternatives escape the
    blockage in one evaluation instead of hundreds.
    """
    out = [int(pick)]
    for c in cand[np.argsort(topmax[cand])]:
        c = int(c)
        if len(out) >= k:
            break
        if all(abs(c - o) >= min_idx_sep for o in out):
            out.append(c)
    return out


def skyline_fill(frame, quota, placed, tree_state, rng, stride, tiebreak, counts,
                 slack=0.0):
    """Drop pieces onto `frame` until nothing else fits. Mutates `placed`.

    `quota` maps kind -> how many more of that kind the mix still wants; kinds are tried
    in deficit order so the 6:4:4:2 ratio is held without a density penalty (all four
    kinds are within 8 % of the same footprint area, so which one lands where costs
    almost nothing).
    """
    sky = build_sky(frame, placed)

    sky_tbl = sparse_max(sky)
    n_before = len(placed)
    # Positions the exact check refused. Blacklisting just that (kind, rotation, x) is
    # enough to stop the loop re-proposing it; the earlier version raised the skyline to
    # +inf across the whole piece span instead, which threw away good wafer.
    dead = {}
    rejects = 0
    reject_cap = 40
    spread_idx = max(int(2.0 / DX / stride), 1)     # alternatives >= 2 mm apart

    while True:
        # Deficit against the TARGET RATIO, not against the raw quota left. Sorting by
        # quota alone just places the most-wanted kind first until its counter falls to
        # the next one's, which produced runs of 20 U4 and zero U8.
        n_next = sum(counts.values()) + 1
        order = sorted(KINDS,
                       key=lambda k: (-(RATIO[k] * n_next - counts[k]), rng.random()))
        per_kind = {}
        for kind in order:
            if quota[kind] <= 0:
                continue
            for rot in (90, 270):
                pr = profile(kind, rot)
                if pr.T + 1 >= frame.M:
                    continue
                j0s = np.arange(0, frame.M - pr.T, stride)
                yoff = np.full(j0s.size, -np.inf)
                for t0, t1, bv in pr.bot_rects:
                    yoff = np.maximum(yoff, rq(sky_tbl, j0s + t0, t1 - t0, True) - bv)
                yoff += GAP
                ok = np.isfinite(yoff)
                for t0, t1, tv in pr.top_rects:
                    if not ok.any():
                        break
                    ok &= (yoff + tv) <= rq(frame.roof_tbl, j0s + t0, t1 - t0, False)
                d = dead.get((kind, rot))
                if d is not None:
                    ok &= ~d
                if not ok.any():
                    continue
                # Lowest resulting skyline wins; that is what leaves the most room above.
                topmax = yoff + max(tv for _, _, tv in pr.top_rects)
                cand = np.where(ok)[0]
                score = topmax[cand]
                lo = score.min()
                near = cand[score <= lo + 1e-9]
                if tiebreak == "right":
                    pick = near[-1]
                elif tiebreak == "random":
                    pick = near[rng.randrange(near.size)]
                else:
                    pick = near[0]
                cur = per_kind.get(kind)
                if cur is None or topmax[pick] < cur[0] - 1e-9:
                    per_kind[kind] = (topmax[pick], kind, rot, int(j0s[pick]),
                                      float(yoff[pick]), int(pick), j0s.size,
                                      spread(cand, topmax, pick, spread_idx), j0s, yoff,
                                      topmax)
            if kind in per_kind and slack <= 0.0:
                break           # strict: first kind in deficit order that fits, wins
        if not per_kind:
            break
        # With slack > 0 a kind that seats only slightly higher but is further behind the
        # target mix can win. slack = 0 reduces to plain deficit order.
        lo = min(v[0] for v in per_kind.values())
        best = max((v for v in per_kind.values() if v[0] <= lo + slack),
                   key=lambda v: RATIO[v[1]] * n_next - counts[v[1]])

        _, kind, rot, j0, yoff, j0_index, j0s_last, alts, j0s_all, yoff_all, _tm = best
        pr = profile(kind, rot)
        theta = (rot + frame.phi) % 360

        # The skyline only PROPOSES. Accept only what the true polygons allow. A refusal
        # means an overhang the per-column floor could not see, or an alignment mark, so
        # nudge up a little and then move on to a well-separated alternative.
        accepted = None
        tried = []
        for a in alts:
            tried.append(a)
            xf = frame.x0 + int(j0s_all[a]) * DX - pr.origin[0]
            for bump in range(0, 8):
                # yoff IS the y translation: bot[] carries absolute y in the rotated
                # piece's own frame, so max(sky - bot) is already the shift. (x differs --
                # bin 0 is anchored at the bbox left edge, hence the origin[0] term.)
                yf = float(yoff_all[a]) + bump * 0.05
                wx, wy = to_world(frame.phi, xf, yf)
                g = place_poly(kind, theta, wx, wy)
                if not g.within(USABLE_EPS) or MARKS_PREP.intersects(g):
                    continue
                hit = False
                for idx in tree_state["tree"].query(g):
                    o = tree_state["geoms"][idx]
                    if g.intersection(o).area > OVERLAP_TOL:
                        hit = True
                        break
                    if GAP > 0 and g.distance(o) < GAP - 1e-9:
                        hit = True
                        break
                if not hit:
                    accepted = (g, wx, wy)
                    break
            if accepted is not None:
                break
        if accepted is None:
            d = dead.setdefault((kind, rot), np.zeros(j0s_last, bool))
            d[tried] = True
            rejects += 1
            if rejects > reject_cap:
                break
            continue

        g, wx, wy = accepted

        placed.append(Placement(kind, theta, wx, wy, g))
        tree_state["geoms"].append(g)
        tree_state["tree"] = STRtree(tree_state["geoms"])
        quota[kind] -= 1
        counts[kind] += 1

        raise_sky(sky, frame, g)
        sky_tbl = sparse_max(sky)

    return len(placed) - n_before


def refill(placed, quota, counts, tree_state, rng, params):
    """Top up a layout in place, along this trial's grain."""
    skyline_fill(Frame(params["phi"], USABLE), quota, placed, tree_state, rng,
                 params["stride"], params["tiebreak"], counts, params["slack"])


def ruin(placed, rng, phi):
    """Delete part of the layout so the refill can rebuild it differently.

    The cut is made in FRAME coordinates and always removes material that is open from
    above. That is not a stylistic choice: `build_sky` takes the upper envelope, so a
    hole with pieces still sitting over it is invisible to the packer and can never be
    refilled. Carving a tower down from the skyline is the only ruin a skyline packer can
    actually act on -- a random disk in the middle of the wafer just loses pieces.

    Two shapes: a full-width cut (everything above y), and atower (everything above y
    within an x window), which is the one that lets the fill re-grow a few columns while
    leaving the rest of the wafer alone.
    """
    if len(placed) < 8:
        return placed

    def fb(pl):
        return shp_rotate(pl.geom, -phi, origin=(0, 0)).bounds

    bl = [(pl, fb(pl)) for pl in placed]
    tops = sorted(b[3] for _, b in bl)
    cut = tops[int(len(tops) * rng.uniform(0.25, 0.85))]
    if rng.random() < 0.45:
        keep = [pl for pl, b in bl if b[3] <= cut]
    else:
        xs = [b[0] for _, b in bl]
        w = rng.uniform(18.0, 60.0)
        xa = rng.uniform(min(xs) - 5, max(xs) + 5)
        xb = xa + w
        keep = [pl for pl, b in bl
                if b[3] <= cut or b[2] <= xa or b[0] >= xb]
    # Never tear out everything; that is just a restart with extra steps.
    return keep if len(keep) >= 4 else placed


def state_of(placed, budget):
    counts = {k: 0 for k in KINDS}
    for pl in placed:
        counts[pl.kind] += 1
    quota = {k: max(int(round(RATIO[k] * budget)) - counts[k], 0) for k in KINDS}
    tree_state = {"geoms": [pl.geom for pl in placed], "tree": STRtree([pl.geom for pl in placed])}
    return counts, quota, tree_state


def improve(placed, params, rng, rounds):
    """Ruin-and-recreate around `placed`. Returns the best layout seen."""
    best = list(placed)
    for _ in range(rounds):
        trial = ruin(list(best), rng, params["phi"])
        if len(trial) == len(best):
            continue
        budget = max(int(len(best) * 1.6), 120)
        counts, quota, tree_state = state_of(trial, budget)
        # The recreate MUST be randomised. With the trial's own fixed tiebreak it rebuilds
        # the identical tower it just tore down and the loop cannot climb at all.
        rp = dict(params)
        rp["tiebreak"] = rng.choice(["left", "right", "random", "random"])
        rp["slack"] = rng.choice([0.4, 0.4, 1.2, 0.0])
        refill(trial, quota, counts, tree_state, rng, rp)
        if len(trial) > len(best):
            best = trial
        elif len(trial) == len(best) and rng.random() < 0.5:
            best = trial          # sideways move: same count, different shape to ruin next
    return best


def pack(params):
    rng = random.Random(params["seed"])
    quota = dict(params["quota"])
    placed = []
    counts = {k: 0 for k in KINDS}
    tree_state = {"geoms": [], "tree": STRtree([])}
    skyline_fill(Frame(params["phi"], USABLE), quota, placed, tree_state, rng,
                 params["stride"], params["tiebreak"], counts, params["slack"])
    if params["rr_rounds"]:
        placed = improve(placed, params, rng, params["rr_rounds"])

    return placed


# --- verification -------------------------------------------------------------
def verify(placed):
    """Hard check on the real polygons. Nothing is ever reported without passing this."""
    if not placed:
        return False, "empty", {}
    geoms = [pl.geom for pl in placed]
    tree = STRtree(geoms)
    worst_ov = 0.0
    for i, g in enumerate(geoms):
        for j in tree.query(g):
            if j <= i:
                continue
            worst_ov = max(worst_ov, g.intersection(geoms[j]).area)
    if worst_ov > OVERLAP_TOL:
        return False, "overlap %.6f mm2" % worst_ov, {}
    oob = max(g.difference(USABLE).area for g in geoms)
    if oob > 1e-7:
        return False, "out of bounds %.6f mm2" % oob, {}
    onmark = max(g.intersection(MARKS).area if g.intersects(MARKS) else 0.0 for g in geoms)
    if onmark > 1e-7:
        return False, "on alignment marks %.6f mm2" % onmark, {}
    edge = Point(0, 0).buffer(R0, 512).intersection(box(-R0 - 1, FLAT0, R0 + 1, R0 + 1)).boundary
    m = {
        "overlap": worst_ov,
        "min_edge": min(g.distance(edge) for g in geoms),
        "max_reach": max(pl.reach() for pl in placed),
        "min_mark": min(g.distance(MARKS) for g in geoms),
        "density": sum(g.area for g in geoms) / USABLE.area,
        "min_sep": min((geoms[i].distance(geoms[j])
                        for i in range(len(geoms))
                        for j in range(i + 1, len(geoms))), default=float("nan")),
    }
    return True, "ok", m


# --- preview ------------------------------------------------------------------
PALETTE = {"U4": "magenta", "U1.6": "cyan", "U2.5": "indianred", "U8": "darkkhaki"}


def write_preview(placed, path, subtitle):
    fig, ax = plt.subplots(figsize=(9, 9))
    th = np.linspace(0, 2 * np.pi, 512)
    ax.plot(R0 * np.cos(th), R0 * np.sin(th), "k-", lw=1.2, label="wafer r=%.0f" % R0)
    ax.plot(R * np.cos(th), R * np.sin(th), "r--", lw=0.7,
            label="usable r=%.1f (excl %.1f)" % (R, EDGE_EXCL))
    ax.axhline(FLAT0, color="gray", lw=1)
    for geom in (MARKS.geoms if MARKS.geom_type == "MultiPolygon" else [MARKS]):
        ax.add_patch(MplPoly(list(geom.exterior.coords), closed=True,
                             fc="orange", ec="darkorange", lw=0.4, alpha=0.7))
    cyc = ["magenta", "cyan", "indianred", "darkkhaki", "seagreen", "steelblue"]
    col = {k: PALETTE.get(k, cyc[i % len(cyc)]) for i, k in enumerate(KINDS)}
    for pl in placed:
        ax.add_patch(MplPoly(list(pl.geom.exterior.coords), closed=True,
                             fc=col[pl.kind], ec="k", lw=0.3, alpha=0.85))
    ax.set_aspect("equal")
    ax.set_xlim(-52, 52)
    ax.set_ylim(-52, 52)
    ax.set_title(subtitle, fontsize=9)
    handles = [plt.Rectangle((0, 0), 1, 1, fc=col[k], ec="k", lw=0.3) for k in KINDS]
    labels = ["%s x%d" % (k, sum(1 for pl in placed if pl.kind == k)) for k in KINDS]
    ax.legend(handles + ax.get_legend_handles_labels()[0],
              labels + ax.get_legend_handles_labels()[1], loc="upper right", fontsize=7)
    plt.tight_layout()
    plt.savefig(path, dpi=110)
    plt.close(fig)


# --- interlock report ---------------------------------------------------------
def report_interlock():
    """Print the measured flip-180 pitch gain -- the number the whole approach rests on."""
    for k in KINDS:
        pr = profile(k, 90)
        n = pr.T
        bot = np.where(pr.support, pr.rel_bot, np.inf)
        top = np.array([np.nan] * n)
        b = pr.poly.bounds
        xs = b[0] + (np.arange(n) + 0.5) * DX
        lo, hi = vline_extent(pr.poly, np.clip(xs, b[0] + 1e-9, b[2] - 1e-9))
        naive = np.nanmax(hi) - np.nanmin(lo)
        flip_bot = -hi[::-1]
        best = min((float(np.nanmax(hi - np.roll(flip_bot, s))), s * DX) for s in range(n))
        print("  %-5s len %.2f mm   naive pitch %.3f -> flipped %.3f mm  (%.0f%% tighter)"
              % (k, pr.len, naive, best[0], 100 * (1 - best[0] / naive)))


# --- search loop --------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=400)
    ap.add_argument("--time", type=float, default=900.0, help="wall-clock budget, seconds")
    ap.add_argument("--stride", type=int, default=2, help="candidate x stride in DX units")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--edge", type=float, default=None,
                    help="mm of edge exclusion (default: the config's EDGE_EXCL)")
    ap.add_argument("--gap", type=float, default=None,
                    help="mm of clearance between devices (default 0 -- they may touch)")
    args = ap.parse_args()

    if args.edge is not None:
        set_edge(args.edge)
    if args.gap is not None:
        globals()["GAP"] = float(args.gap)
    os.makedirs(C.OUT_DIR, exist_ok=True)
    print("wafer: usable r=%.1f mm (edge excl %.1f), flat y=%.2f, gap=%.3f mm"
          % (R, EDGE_EXCL, FLAT0, GAP))
    print("usable area %.0f mm2;  piece areas: %s"
          % (USABLE.area, ", ".join("%s %.1f" % (k, FP[k].area) for k in KINDS)))
    print("target mix %s" % ", ".join("%s %.0f%%" % (k, 100 * RATIO[k]) for k in KINDS))
    print("flip-180 interlock (measured):")
    report_interlock()
    ceiling = int(USABLE.area / (sum(FP[k].area * RATIO[k] for k in KINDS)))
    print("area ceiling at 100%% density: %d pieces (unreachable; strips in a circle "
          "run ~55-65%%)\n" % ceiling)

    master = random.Random(args.seed)
    best_n, best = 0, None
    t_start = time.time()

    for trial in range(args.trials):
        if time.time() - t_start > args.time:
            print("time budget reached")
            break
        # Generous quota so the mix, not the quota, is what limits the fill.
        budget = max(int(best_n * 1.6), 120)
        quota = {k: max(int(round(RATIO[k] * budget)), 1) for k in KINDS}
        params = {
            "seed": master.randrange(1 << 30),
            "phi": master.choice([0, 0, 0, 180, 180, 180, 90, 270]),
            "stride": args.stride,
            "tiebreak": master.choice(["left", "right", "random"]),
            "rr_rounds": master.choice([10, 25, 50]),
            "slack": master.choice([0.4, 0.4, 1.2, 0.0]),
            "quota": quota,
        }
        t0 = time.time()
        placed = pack(params)
        n = len(placed)
        dt = time.time() - t0
        ok, why, m = verify(placed)
        flag = "" if ok else "  REJECTED: %s" % why
        print("trial %3d  phi=%3d %-6s slack=%.1f rr=%2d  n=%3d  %.1fs%s"
              % (trial, params["phi"], params["tiebreak"], params["slack"],
                 params["rr_rounds"], n, dt, flag), flush=True)
        if not ok or n <= best_n:
            continue
        best_n, best = n, (placed, params, m)
        poses = [pl.pose() for pl in placed]
        json.dump(poses, open(C.POSES_PATH, "w"))
        counts = {k: sum(1 for p in poses if p[0] == k) for k in KINDS}
        sub = ("n=%d   %s   density %.1f%%   gap %.3f mm"
               % (n, "  ".join("%s x%d" % (k, counts[k]) for k in KINDS),
                  100 * m["density"], GAP))
        json.dump({
            "n_total": n, "edge_excl_mm": EDGE_EXCL, "gap_mm": GAP,
            "wafer_r_mm": R0, "counts": counts, "density": m["density"],
            "max_reach_mm": m["max_reach"],
            "pieces": [{"i": i, "kind": pl.kind, "theta": pl.theta % 360,
                        "x": pl.x, "y": pl.y, "reach_mm": pl.reach()}
                       for i, pl in enumerate(placed)],
        }, open(os.path.join(C.OUT_DIR, "nest_report.json"), "w"), indent=1)
        write_preview(placed, C.PREVIEW_PATH, sub)
        print("  *** %s" % sub)
        print("      SHANK_COUNTS = %s   <- put this in %s so 03_export_wafer.py's "
              "count assertion matches" % (repr(counts), C.__name__ + ".py"))
        print("      min edge %.3f mm, min mark %.3f mm, min piece sep %.3f mm -> %s"
              % (m["min_edge"], m["min_mark"], m["min_sep"],
                 os.path.basename(C.PREVIEW_PATH)), flush=True)

    if best is None:
        raise SystemExit("no verified layout produced")
    print("\nBEST %d pieces -> %s + %s" % (best_n, os.path.basename(C.POSES_PATH),
                                           os.path.basename(C.PREVIEW_PATH)))


if __name__ == "__main__":
    main()
