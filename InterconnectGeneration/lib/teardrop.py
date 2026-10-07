"""Teardrop pads and the angled entry on the flanked rows (HANDOVER §5).

Row/column bookkeeping, the tear outline itself, the pad-approach widening, the angled
re-shaping of the flanked rows' final approach, and the clearance clamp that shrinks
each tear until it fits.
"""
import math
import numpy as np
from shapely.strtree import STRtree
from shapely.geometry import Polygon, Point, LineString

from lib import active

active.require()
from lib.active import *  # noqa: F401,F403  -- this design's knobs (see lib/active.py)

from lib.geometry import _arc_pts, chamfer_polyline, create_polygon_circle, to_seglist
from lib.checks import _seg_int


def teardrop_tip_dist(r, fillet_r, width):
    """Distance from the pad centre out to the tear's TIP -- where the fillet arcs land on the
    trace edges. Not a free parameter: it falls out of the tangency conditions (see
    teardrop_ring). None when the radii admit no tear at all: no fillet, a trace as wide as
    the pad, or a tip that would sit inside the pad circle."""
    if fillet_r is None:
        return None
    r, f = float(r), float(fillet_r)
    hw = max(float(width), 0.0) / 2.0
    if f <= 0.0 or hw >= r:
        return None
    d2 = (r + f) ** 2 - (hw + f) ** 2
    if d2 <= 0.0:
        return None
    L = math.sqrt(d2)
    return L if L > r else None


def teardrop_fillet_for_tip(r, width, tip_dist):
    """Inverse of teardrop_tip_dist: the fillet radius that puts the tip `tip_dist` from the
    pad centre. Used to cap a tear at the straight run actually available to it."""
    r, hw = float(r), max(float(width), 0.0) / 2.0
    if hw >= r or not np.isfinite(tip_dist):
        return float("inf")
    return (float(tip_dist) ** 2 - r * r + hw * hw) / (2.0 * (r - hw))


def teardrop_ring(px, py, r, ux, uy, fillet_r, width, res=48):
    """Tangent-arc teardrop: the pad circle plus two CONCAVE fillet arcs of radius `fillet_r`,
    each tangent externally to the pad circle and tangent to one edge of the `width`-wide trace
    arriving along (ux, uy). Same construction as filleted_bulb, but aimed along an arbitrary
    direction and blended into two trace edges rather than one straight wall.

    This is the profile the mating electrode bundle uses (electrode_bundle_U4C08.dxf), and it
    is fixed once the three radii are: a fillet centre must sit r + fillet_r from the pad
    centre (external tangency) and fillet_r off the trace edge line, which puts it
    (width/2 + fillet_r) sideways and teardrop_tip_dist() along the approach. The pad arc then
    runs the LONG way between the two tangent points, i.e. around the back, away from the tip.

    Returns a closed ring in the caller's units, or the plain circle when no tear fits."""
    circle = create_polygon_circle(px, py, r, res)
    n = float(np.hypot(ux, uy))
    L = teardrop_tip_dist(r, fillet_r, width)
    if L is None or n == 0.0:
        return circle
    ux, uy = ux / n, uy / n
    nx, ny = -uy, ux                                      # left normal of the approach
    f = float(fillet_r)
    hw = max(float(width), 0.0) / 2.0
    k = r / (r + f)                                       # circle tangent-point interpolation
    bx, by = px + ux * L, py + uy * L                     # the tip, on the pad's axis
    ang = lambda p, o: math.atan2(p[1] - o[1], p[0] - o[0])

    def flank(s):
        """(fillet centre, pad tangent point, trace-edge tangent point) on side s = +1 / -1."""
        O = (bx + nx * s * (hw + f), by + ny * s * (hw + f))
        return (O,
                (px + (O[0] - px) * k, py + (O[1] - py) * k),
                (bx + nx * s * hw, by + ny * s * hw))

    Ol, Cl, Wl = flank(+1)
    Or_, Cr, Wr = flank(-1)
    pts = np.vstack([
        _arc_pts(Ol[0], Ol[1], f, ang(Wl, Ol), ang(Cl, Ol), res, chord_r=r),        # edge -> pad
        _arc_pts(px, py, r, ang(Cl, (px, py)), ang(Cr, (px, py)), res,
                 longway=True)[1:],                                                  # around the back
        _arc_pts(Or_[0], Or_[1], f, ang(Cr, Or_), ang(Wr, Or_), res, chord_r=r)[1:],  # pad -> edge
    ])
    return np.vstack((pts, pts[0]))                       # closed, tip edge implied


def pad_rows_by_index(sig, block_gap=0.9):
    """Map each pad index -> its physical row within its connector block, 1..4 TOP-DOWN.

    Derived from the pad y values rather than hardcoded, so it survives a connector change:
    unique rows are split into blocks wherever the vertical gap exceeds `block_gap` (the
    real spacings are 0.74 / 0.37 / 0.74 inside a block and 1.15 between blocks)."""
    sig = np.asarray(sig)
    uy = np.sort(np.unique(np.round(sig[:, 1], 4)))[::-1]      # descending = top first
    blocks, cur = [], [uy[0]]
    for prev, y in zip(uy[:-1], uy[1:]):
        if (prev - y) <= block_gap:
            cur.append(y)
        else:
            blocks.append(cur)
            cur = [y]
    blocks.append(cur)
    row_of_y = {}
    for blk in blocks:
        for k, y in enumerate(blk):
            row_of_y[round(float(y), 4)] = k + 1
    return {i: row_of_y.get(round(float(y), 4), 0) for i, (_x, y) in enumerate(sig)}


def pad_column_split(sig):
    """The x separating the two connector columns: the middle of the widest gap in pad x
    (3.375 mm here, against a 0.175 mm step in pad x), so it survives a different block layout."""
    xs = np.sort(np.unique(np.round(np.asarray(sig)[:, 0], 4)))
    if len(xs) < 2:
        return float(xs[0]) if len(xs) else 0.0
    k = int(np.argmax(np.diff(xs)))
    return float(0.5 * (xs[k] + xs[k + 1]))


def pad_cols_by_index(sig):
    """Map each pad index -> "L" or "R"."""
    sig = np.asarray(sig)
    split = pad_column_split(sig)
    return {i: ("L" if x < split else "R") for i, (x, _y) in enumerate(sig)}


def angled_side_for(col):
    """Which way the diagonal leans in this column (see ANGLED_ENTRY_SIDE / ..._RIGHT)."""
    return ANGLED_ENTRY_SIDE if col == "L" else ANGLED_ENTRY_SIDE_RIGHT


def angled_rows_for(col):
    """The flanked rows in this column (see ANGLED_ENTRY_ROWS / ..._RIGHT)."""
    return ANGLED_ENTRY_ROWS if col == "L" else ANGLED_ENTRY_ROWS_RIGHT


def teardrop_tip_width(row, col):
    """Trace width the tear's flanks are built tangent to. That is the WIDENED approach width
    when one is set for this kind of row, not the bare connector width: widen_pad_approach has
    already ramped the trace up by the time it reaches the pad, and a tear tangent to the
    narrower edges would meet the trace at a kink instead of flush."""
    tw = pad_approach_for(row, col)[0]
    return float(tw) if tw is not None else float(CONNECTOR_TW)


def teardrop_fillet_for(row, col):
    """Nominal fillet radius for a pad, by whether its row is angled or vertical IN ITS
    COLUMN. Mirrors teardrop_tip_width, so the two always agree on what counts as angled."""
    return (TEARDROP_FILLET_R_ANGLED if row in angled_rows_for(col)
            else TEARDROP_FILLET_R_VERTICAL)


def teardrop_len_for(row, col, r=None, fillet_r=None):
    """Tip distance from the pad centre for a pad, by whether its row is angled or vertical IN
    ITS COLUMN. Derived from the fillet radius rather than set directly, so it also tracks the
    row's approach width. None when no tear fits (see teardrop_tip_dist)."""
    return teardrop_tip_dist(PADR if r is None else r,
                             teardrop_fillet_for(row, col) if fillet_r is None else fillet_r,
                             teardrop_tip_width(row, col))


def teardrop_kind_rows(col):
    """One representative (angled row, vertical row) number in this column, for reporting."""
    a = angled_rows_for(col)
    return a[0], next(r for r in (1, 2, 3, 4) if r not in a)


def _min_foreign_pad_dist(a, b, P, own):
    """Closest distance from segment a-b to any pad centre except `own`. Same point-to-segment
    distance _check_fast uses for pad_hits."""
    ax, ay = a
    vx, vy = b[0] - ax, b[1] - ay
    L2 = vx * vx + vy * vy
    if L2 <= 0.0:
        d = np.hypot(P[:, 0] - ax, P[:, 1] - ay)
    else:
        t = np.clip(((P[:, 0] - ax) * vx + (P[:, 1] - ay) * vy) / L2, 0.0, 1.0)
        d = np.hypot(P[:, 0] - (ax + t * vx), P[:, 1] - (ay + t * vy))
    if own is not None:
        d[own] = np.inf
    return float(d.min())


def pad_approach_for(row, col):
    """(tip width, ramp length, hold fraction) for a pad, by whether its row is angled or vertical in its
    column. Mirrors teardrop_len_for, so the two always agree on what counts as angled."""
    if row in angled_rows_for(col):
        return PAD_APPROACH_TW_ANGLED, PAD_APPROACH_LEN_ANGLED, PAD_APPROACH_HOLD_ANGLED
    return (PAD_APPROACH_TW_VERTICAL, PAD_APPROACH_LEN_VERTICAL, PAD_APPROACH_HOLD_VERTICAL)


def widen_pad_approach(centerline, widths, tip_w, ramp_len, hold_frac=0.0):
    """Widen the trace to `tip_w` over the last `ramp_len` before the pad.

    hold_frac is the share of that length, measured FROM THE PAD, that is already at full
    width: 0.0 ramps linearly the whole way (so full width is reached only at the pad
    itself), 0.6 reaches full width 60% of the way out and holds it in from there. Use it
    when the tear should be fed by a stout wire rather than by a wire still tapering.

    Works on distance travelled from the pad, not on y, so a vertical and a tilted approach
    behave the same. Vertices are inserted at the knee and at the ramp start when those fall
    mid-segment, otherwise a sparse tail would smear the profile across a whole segment."""
    if tip_w is None or ramp_len <= 0 or len(centerline) < 2:
        return centerline, widths
    pts = [tuple(map(float, p)) for p in centerline]
    ws = [float(w) for w in widths]
    hold = ramp_len * max(0.0, min(1.0, float(hold_frac)))

    def wid(d, base):
        if d <= hold:
            return max(base, tip_w)
        if d >= ramp_len:
            return base
        f = (d - hold) / (ramp_len - hold)
        return max(base, tip_w + (base - tip_w) * f)

    out_pts, out_ws, acc = [pts[-1]], [wid(0.0, ws[-1])], 0.0
    for i in range(len(pts) - 1, 0, -1):
        a, b = pts[i - 1], pts[i]
        seg = float(np.hypot(a[0] - b[0], a[1] - b[1]))
        if seg <= 0.0:
            continue
        base = ws[i - 1]
        for cut in sorted({hold, ramp_len}):          # knee first, then the ramp start
            if acc < cut < acc + seg:
                f = (cut - acc) / seg
                out_pts.append((b[0] + (a[0] - b[0]) * f, b[1] + (a[1] - b[1]) * f))
                out_ws.append(wid(cut, base))
        acc += seg
        out_pts.append(a)
        out_ws.append(wid(acc, base))
    out_pts.reverse()
    out_ws.reverse()
    return out_pts, out_ws


def angled_entry_len():
    """Diagonal displacement, mm. `None` auto-derives the value that puts the trace's
    straight run all the way into the tear's tip: the run must reach the tear's tip distance
    plus whatever the corner bevel sets back, and a 45 deg leg of displacement d is d*sqrt(2).
    Both columns give the same tip distance (same PADR, same PAD_APPROACH_TW_ANGLED), so the
    left column stands in for both."""
    if ANGLED_ENTRY_LEN is not None:
        return float(ANGLED_ENTRY_LEN)
    tip = teardrop_len_for(angled_rows_for("L")[0], "L")
    return ((PADR if tip is None else tip) + CHAMFER_MAX_MM) / np.sqrt(2.0)


def angled_pad_entry(routes, sig, rows=None, d=None):
    """Enter the flanked pad rows on a 45 deg diagonal instead of perpendicular.

    Rewrites ONLY the final approach: the feed lane and its y stay where they are, and the
    vertical drop is replaced by ONE straight segment from (px +- d, lane_y) into the pad.
    d = lane height (exactly 45 deg) is tried first, then angled_entry_len() if that is
    smaller, then steeper tilts down to 0.2 * d_req.

    The displacement is decided PER ROUTE, not globally: a candidate is kept only if its new
    tail clears every foreign pad (PADR + ANGLED_ENTRY_MARGIN) and every foreign trace
    (ANGLED_ENTRY_TRACE_GAP). A route that fits no candidate is left perpendicular.

    Runs BEFORE chamfer_polyline, so the new corners get the usual bevel, and before
    _check_fast, so anything this clamp misses still shows up as crossings / pad_hits."""
    d_req = angled_entry_len() if d is None else float(d)
    if not ANGLED_ENTRY or d_req <= 0:
        return routes, {}, {}
    sig_arr = np.asarray(sig)
    P = sig_arr[:, :2]
    row_of = pad_rows_by_index(sig_arr)
    col_of = pad_cols_by_index(sig_arr)
    need = PADR + ANGLED_ENTRY_MARGIN
    floor = 0.2 * d_req                       # below this the diagonal is not worth having

    # index of every route's full geometry, so a candidate can be tested against the traces it
    # would sweep across.
    tr_geom, tr_key = [], []
    for k2, p2 in routes.items():
        q2 = [tuple(map(float, z)) for z in p2]
        # Index the FULL route. The non-angled rows of each column are never re-shaped, so their
        # vertical drops stay exactly where they are and a sweeping diagonal can cross them. For
        # re-shaped routes this is merely conservative: their tail only moves toward its lean.
        if len(q2) >= 2:
            tr_geom.append(LineString(q2))
            tr_key.append(k2)
    tr_tree = STRtree(tr_geom)

    out, stats, done, originals = {}, {}, [], {}
    for key, poly in routes.items():
        pts = [tuple(map(float, p)) for p in poly]
        if len(pts) < 2:
            out[key] = poly
            continue
        px, py = pts[-1]
        qx, qy = pts[-2]
        j = int(np.argmin((P[:, 0] - px) ** 2 + (P[:, 1] - py) ** 2))
        row = row_of.get(j, 0)
        col = col_of.get(j, "L")
        rows_here = angled_rows_for(col) if rows is None else rows
        # NOTE: do NOT require the lane to be at least d_req away. With the single-segment
        # tail, d is simply clamped to the lane height, giving a 45 deg run -- so a close
        # lane is routable, just at a shallower displacement. Requiring d_req would skip the
        # first pad of each block row (its lane is closer than d_req), leaving it perpendicular.
        if row not in rows_here or abs(qx - px) > 1e-9 or abs(qy - py) <= 1e-9:
            out[key] = poly
            continue

        side = angled_side_for(col)
        s = 1.0 if side == "right" else -1.0    # "auto" would tie here, so treat it as left
        ydir = 1.0 if qy > py else -1.0

        # The tail is ALWAYS one straight segment from the lane into the pad -- never a vertical
        # drop plus a turn, which is what produced the crooked stub-and-facet entry. d = h makes
        # that segment exactly 45 deg; a smaller d simply TILTS IT STEEPER. Either way the
        # entrance is clean, and the teardrop rotates to match for free, because the tear points
        # back up its own trace.
        h = abs(qy - py)
        cands = [h]                                          # exactly 45 deg off the lane
        if d_req < h:
            cands.append(d_req)                              # the target displacement
        d_shrink = min(cands)
        for _ in range(8):                                   # tilt steeper until it fits
            d_shrink *= 0.7
            if d_shrink < floor:
                break
            cands.append(d_shrink)

        d_use, ok = None, False
        for cand in cands:
            tail = [pts[-3]] if len(pts) >= 3 else []
            tail += [(px + s * cand, qy), (px, py)]
            clear = min(_min_foreign_pad_dist(a, b, P, j)
                        for a, b in zip(tail[:-1], tail[1:]))
            if clear <= need:
                continue
            probe = LineString(tail).buffer(ANGLED_ENTRY_TRACE_GAP, resolution=4)
            if any(tr_geom[int(i)].intersects(probe)
                   for i in tr_tree.query(probe) if tr_key[int(i)] != key):
                continue                                     # would cross/crowd another trace
            # ...and against the tails already re-shaped this pass, which the static index
            # cannot know about: two reshaped tails can sweep into each other.
            pb = probe.bounds
            if any(not (db[2] < pb[0] or db[0] > pb[2] or db[3] < pb[1] or db[1] > pb[3])
                   and dg.intersects(probe) for dg, db in done):
                continue
            d_use, ok = cand, True
            break
        if not ok:
            out[key] = poly                    # leave this one perpendicular
            stats.setdefault((col, row), []).append((0.0, 90.0))
            continue
        newtail = [(px + s * d_use, qy), (px, py)]
        originals[key] = poly                       # so a crossing can be repaired later
        out[key] = pts[:-2] + newtail
        g = LineString(([pts[-3]] if len(pts) >= 3 else []) + newtail) \
            .buffer(ANGLED_ENTRY_TRACE_GAP, resolution=4)
        done.append((g, g.bounds))
        stats.setdefault((col, row), []).append((d_use, float(np.degrees(np.arctan2(h, d_use)))))
    return out, stats, originals


def crossing_route_keys(routes_dict):
    """Keys of routes involved in a crossing, using _check_fast's own segment rule so the
    two can never disagree."""
    keys = list(routes_dict.keys())
    items = sorted((min(a[0], b[0]), max(a[0], b[0]), min(a[1], b[1]), max(a[1], b[1]), i, a, b)
                   for i, a, b in to_seglist(routes_dict))
    bad = set()
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
                bad.add(keys[idi]); bad.add(keys[it[4]])
    return bad


def repair_angled_crossings(routes, originals, rounds=4):
    """Chamfer, look for crossings, put the offending re-shaped tails back, repeat.

    The chamfer runs AFTER the angled pass and its bevel juts out far enough to clip a
    neighbour, which no check on the un-chamfered tail can see. Rather than predict the bevel,
    emit it and repair."""
    def _cham(k, p):
        """Chamfer a route. For a re-shaped angled tail with its own ANGLED_ENTRY_CHAMFER,
        only the FINAL corner -- the lane->pad one -- uses that target; every corner upstream
        keeps CHAMFER_MAX_MM. Applying the knob to the whole route instead un-chamfers the fan
        and spine corners too, which makes them clip their neighbours (nearly every tail then
        gets reverted)."""
        if ANGLED_ENTRY_CHAMFER is None or k not in originals:
            return chamfer_polyline(p, CHAMFER_MAX_MM, CHAMFER_FRAC, keep_tail=0)
        base = chamfer_polyline(p, CHAMFER_MAX_MM, CHAMFER_FRAC, keep_tail=1)
        if len(base) < 3:
            return base
        # chamfer_polyline never moves endpoints, so the last corner can be done separately
        return list(base[:-3]) + list(chamfer_polyline(base[-3:], ANGLED_ENTRY_CHAMFER,
                                                       CHAMFER_FRAC, keep_tail=0))

    cur = dict(routes)
    reverted = 0
    for _ in range(rounds):
        ch = {k: _cham(k, p) for k, p in cur.items()}
        bad = crossing_route_keys(ch) & set(originals)
        if not bad:
            return ch, reverted
        for k in bad:
            cur[k] = originals.pop(k)
            reverted += 1
    return {k: _cham(k, p) for k, p in cur.items()}, reverted


def clamp_teardrop_fillets(sig, pad_feed, pad_run, route_metal, clear, pad_r, row_of, col_of):
    """Per-pad fillet radius, shrunk until the metal the tear ADDS clears foreign metal.

    Judged on the incremental region (tear minus the pad disc), not the whole tear: foreign
    trace centrelines legitimately run about PADR from a pad centre (the pad_hits threshold
    is PADR + half a trace width), so the pad disc itself is already near foreign metal, and
    testing the whole tear would reject every pad.

    This is the only thing standing between a long tear and a short to a neighbour: the
    tear is emission-only, so _check_fast never sees it.

    `route_metal` is the list of (stroked ring in mm, owning pad index or -1) the caller has
    already built for emission -- the actual copper, tapers and pad-approach widening and all.
    Re-buffering the centrelines at one width instead would have to pick between understating
    the widened final stretch and overstating the hair-thin run behind it."""
    sig_arr = np.asarray(sig)
    obstacles, owner = [], []
    for i, (x, y) in enumerate(sig_arr):
        obstacles.append(Point(float(x), float(y)).buffer(pad_r, resolution=24))
        owner.append(i)
    for ring, j in route_metal:
        poly = Polygon(ring)
        if not poly.is_valid:                       # a stroked corner can fold over itself
            poly = poly.buffer(0)
        if poly.is_empty:
            continue
        obstacles.append(poly)
        owner.append(j)
    tree = STRtree(obstacles)

    fillets, full, short, skip = {}, 0, 0, 0
    crowded, tightest = 0, float("inf")
    for i, (x, y) in enumerate(sig_arr):
        u = pad_feed.get(i)
        if u is None:
            continue
        row, col = row_of.get(i, 0), col_of.get(i, "L")
        tip_w = teardrop_tip_width(row, col)
        disc = Point(float(x), float(y)).buffer(pad_r, resolution=24)
        # What the BARE pad already holds. A pad whose neighbours are closer than `clear`
        # cannot be asked for `clear` -- it never had it -- so hold it to what it has and
        # the tear still cannot worsen the gap. See TEARDROP_CLEAR_FLOOR_FROM_PAD.
        near = min((obstacles[int(k)].distance(disc) for k in tree.query(disc.buffer(clear))
                    if owner[int(k)] != i), default=float("inf"))
        want = min(clear, near) if TEARDROP_CLEAR_FLOOR_FROM_PAD else clear
        if want < clear:
            crowded += 1
            tightest = min(tightest, want)

        def fits(F):
            """Does the metal this fillet ADDS (tear minus the pad disc) hold `want` from
            every foreign pad and trace?"""
            ring = teardrop_ring(float(x), float(y), pad_r, u[0], u[1], F, tip_w, 24)
            extra = Polygon([(a, b) for a, b in ring]).difference(disc)
            if extra.is_empty:
                return True
            probe = extra.buffer(want)
            for idx in tree.query(probe):
                if owner[int(idx)] == i:            # its own pad / its own trace
                    continue
                if obstacles[int(idx)].intersects(probe):
                    return False
            return True

        # The tear may not outrun the final straight run, so cap the fillet at the radius
        # whose tip lands exactly there (teardrop_fillet_for_tip inverts the length formula).
        F_nom = float(teardrop_fillet_for(row, col))
        F_max = min(F_nom,
                    teardrop_fillet_for_tip(pad_r, tip_w, pad_run.get(i, float("inf"))))
        L_max = teardrop_tip_dist(pad_r, F_max, tip_w)
        if L_max is None:                           # no tear fits here at all
            skip += 1
            continue
        if fits(F_max):
            fillets[i] = F_max
            full += abs(F_max - F_nom) < 1e-12
            short += abs(F_max - F_nom) >= 1e-12
            continue
        # Otherwise find the biggest tear that does fit, by bisecting on the TIP DISTANCE
        # rather than decaying the fillet radius. Tip distance is what the neighbours
        # actually constrain, it moves monotonically with the fillet, and
        # teardrop_fillet_for_tip inverts back to a radius exactly -- so this converges on
        # the real limit instead of overshooting past it in a few coarse steps.
        lo, hi, best = pad_r * 1.02, L_max, None
        for _ in range(TEARDROP_CLAMP_ITERS):
            mid = 0.5 * (lo + hi)
            Fm = teardrop_fillet_for_tip(pad_r, tip_w, mid)
            if teardrop_tip_dist(pad_r, Fm, tip_w) is None:
                lo = mid                            # degenerate, nothing to test
            elif fits(Fm):
                best, lo = Fm, mid
            else:
                hi = mid
        if best is None:
            skip += 1
        else:
            fillets[i] = best
            short += 1

    def _tip_um(row, col, fillet_r=None):
        L = teardrop_len_for(row, col, pad_r, fillet_r)
        return "-" if L is None else "%.0f um" % (L * 1000.0)

    a_row, v_row = teardrop_kind_rows("L")
    print(f"teardrops: {full} at full fillet, {short} shrunk, {skip} left as plain circles "
          f"(fillet r {TEARDROP_FILLET_R_ANGLED*1000:.0f} um -> tip {_tip_um(a_row, 'L')} angled"
          f" / {TEARDROP_FILLET_R_VERTICAL*1000:.0f} um -> tip {_tip_um(v_row, 'L')} vertical,"
          f" clearance {clear*1000:.0f} um)")
    if crowded:
        print(f"    {crowded} pads already sit closer than {clear*1000:.0f} um to foreign "
              f"metal (tightest {tightest*1000:.1f} um); their tears are held to that gap "
              f"instead, so none of them closes it further")
    by_row = {}
    for i in range(len(sig_arr)):
        if i not in pad_feed:
            continue
        r = (col_of.get(i, "L"), row_of.get(i, 0))
        d = by_row.setdefault(r, [0, 0, 0])
        F = fillets.get(i)
        d[0 if (F is not None and abs(F - teardrop_fillet_for(row_of.get(i, 0),
                                                              col_of.get(i, "L"))) < 1e-12)
          else (1 if F is not None else 2)] += 1
    for r in sorted(by_row):
        f_, s_, k_ = by_row[r]
        tag = ("   <- angled" if r[1] in angled_rows_for(r[0]) else "   <- vertical")
        got = [fillets[i] for i in range(len(sig_arr))
               if fillets.get(i) is not None and (col_of.get(i, "L"), row_of.get(i, 0)) == r]
        worst = ("" if not got or min(got) >= teardrop_fillet_for(r[1], r[0])
                 else f", shrunk down to {_tip_um(r[1], r[0], min(got))}")
        print(f"    {r[0]} row {r[1]}: {f_} full, {s_} shrunk, {k_} plain "
              f"(tip {_tip_um(r[1], r[0])}{worst})" + tag)
    return fillets
