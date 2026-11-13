"""Eased (curved) fan geometry + its verification. Pure math: no config, so the config files can use it too.
"""
import math


def eased_fan_profile(pitch_tight, wide_pitch, D, target_gap, wire_w,
                      tilt_cap_deg, res, length_slack=1.0):
    """Curved ('eased') fan profile via uniform scaling of the bundle about its centre.

    Returns (g_list, L): res+1 normalised shift fractions g_k in [0, 1] (0 at the TIGHT end, 1 at the
    WIDE end), sampled at equal vertical steps s_k = k*L/res, plus the fan length L. A wire whose x
    runs from tight_x to wide_x is drawn as  x(s_k) = tight_x + (wide_x - tight_x) * g_k .

    The curve is the minimum-length profile that keeps the OUTERMOST wire's perpendicular gap
        pitch(s)*cos(theta(s)) - wire_w >= target_gap ,
    with pitch(s) = pitch_tight*f(s) and theta(s) = atan(D*f'(s)), f running 1 -> wide/tight. The
    optimal f is a cosh (vertical at the tight end, so any tilt only appears once the pitch has
    opened). The local tilt is capped at tilt_cap_deg for manufacturability (a straight run past the
    cap only lengthens the fan, preserving the guarantee) and the whole thing is stretched by
    length_slack for discretisation margin. Units are the caller's (um or mm) -- just keep
    pitch_*/D/wire_w/target_gap consistent. D is the outermost wire's offset from centre at the
    tight end (same units)."""
    P_req = target_gap + wire_w
    f_max = wide_pitch / pitch_tight
    r = min(P_req / pitch_tight, 1.0 - 1e-9)           # r < 1 required; target==floor -> r -> 1
    C = math.acosh(1.0 / r)                             # phase so that f(0) = 1
    tcap = math.radians(tilt_cap_deg)
    f_c = r / math.cos(tcap)                            # f where the local tilt reaches the cap
    if f_c >= f_max:                                    # cap never reached -> pure cosh
        s_c = None
        L_min = r * D * (math.acosh(f_max / r) - C)
    else:
        s_c = r * D * (math.acosh(f_c / r) - C)         # end of the cosh phase
        L_min = s_c + D * (f_max - f_c) / math.tan(tcap)

    def f_of_s(s):
        if s_c is None or s <= s_c:
            return r * math.cosh(s / (r * D) + C)
        return f_c + (s - s_c) * math.tan(tcap) / D

    L = L_min * length_slack
    g_list = []
    for k in range(res + 1):
        f = f_of_s(min((k * L / res) / length_slack, L_min))
        g_list.append((f - 1.0) / (f_max - 1.0))
    g_list[0] = 0.0
    g_list[-1] = 1.0
    return g_list, L


def _pt_seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 == 0.0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _polyline_dist(A, B):
    d = float("inf")
    for (px, py) in A:
        for (ax, ay), (bx, by) in zip(B, B[1:]):
            d = min(d, _pt_seg_dist(px, py, ax, ay, bx, by))
    for (px, py) in B:
        for (ax, ay), (bx, by) in zip(A, A[1:]):
            d = min(d, _pt_seg_dist(px, py, ax, ay, bx, by))
    return d


def band_runs(poly, y_lo, y_hi, eps=1e-9):
    """The parts of `poly` that lie inside the horizontal band, as CONTIGUOUS runs.

    Filtering a polyline's points by y and keeping the survivors in one list is wrong: where
    the route leaves the band and comes back, the two sides end up adjacent in the filtered
    list and zipping it invents a straight segment between them that the route never had; the
    gap check would then measure a neighbouring wire's real vertex against that phantom chord.
    Splitting at every exit keeps only real segments."""
    runs, cur = [], []
    for (x, y) in poly:
        if y_lo - eps <= y <= y_hi + eps:
            cur.append((x, y))
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    return [r for r in runs if len(r) >= 2]


def min_adjacent_fan_gap(fan_polys, width, layers=None, owners=None):
    """Minimum edge-to-edge gap between SAME-LAYER neighbouring fan centrelines (subtract `width`).
    fan_polys: one centreline point-list per wire. layers: per-wire layer key; when given, only wires
    on the same layer are treated as neighbours (cross-layer overlap is legal on different metals).
    owners: per-entry wire id, for when one wire contributes several band_runs -- two runs of the
    SAME wire are not neighbours and are never compared against each other."""
    if layers is None:
        groups = {None: list(range(len(fan_polys)))}
    else:
        groups = {}
        for i, lay in enumerate(layers):
            groups.setdefault(lay, []).append(i)
    gmin = float("inf")
    for idxs in groups.values():
        idxs = sorted(idxs, key=lambda i: fan_polys[i][0][0])
        for a, b in zip(idxs, idxs[1:]):
            if owners is not None and owners[a] == owners[b]:
                continue
            gmin = min(gmin, _polyline_dist(fan_polys[a], fan_polys[b]) - width)
    return gmin
