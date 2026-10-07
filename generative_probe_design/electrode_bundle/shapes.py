"""Domain shapes for the electrode bundle, built on the primitives in ``geometry.py``.

These functions know about the *device* -- polyimide pads and traces, the metal
electrode pad+wire, the fanout body, the bond-pad columns and the channel->pad routing.
Some use shapely for boolean unions / morphological rounding. The insertion HOOK is not
here: it is a named spec in the ``hooks/`` package, which owns its outline and its etch
hole together. Moved verbatim from hook_bundle_generator.py, except:

  - ``assign_channels_to_pads`` is newly extracted from ``build_pad_routes`` so the
    channel->pad ordering has a single home. ``build_pad_routes`` now calls it, and
    so does ``mapping.py`` -- the flex mapping no longer *reproduces* the ordering by
    hand (it used to, in build_electrode_flex_mapping.derive_channel_to_pad).
"""
import numpy as np

from .geometry import (_arc, create_polygon_circle, s_transition,
                       stroke_centerline_to_polygon)


def _pad_fillet(pad_r, wire_hw, R):
    """Where a straight trace meets a ROUND pad through a tangent fillet of radius R.

    The fillet circle sits outside the material, touching the trace's straight edge and
    touching the pad circle from outside; that single condition fixes it completely:

        centre at (wire_hw + R, +h) from the pad centre, with
        h = sqrt((pad_r + R)^2 - (wire_hw + R)^2)

    Returns (h, alpha): `h` is how far above the pad centre the fillet meets the trace edge,
    `alpha` the angle (from +x, i.e. from the pad's widest point) at which it meets the pad.
    Tangent at both ends, so the outline has no crease anywhere -- the point of going round.
    """
    if wire_hw >= pad_r:
        raise ValueError(f"trace half-width {wire_hw} must be < pad radius {pad_r} "
                         "for the pad to swell out of the trace at all")
    h = np.sqrt((pad_r + R) ** 2 - (wire_hw + R) ** 2)
    return h, np.arctan2(h, wire_hw + R)


#Arc sampling. These arcs ARE the electrode's shape now, not just corner rounding, so they
#are sampled finer than geometry._arc's default: at PAD_ARC_N the pad circle is flat to
#~0.003 um, well under any feature the mask can hold.
PAD_ARC_N = 32
FILLET_ARC_N = 16
TIP_ARC_N = 25          # ODD, so the sweep's midpoint -- the very tip -- is a vertex


def _append_arc(x, y, seg):
    """Append an arc, dropping a leading point that repeats the current last one.

    The lens outline is a chain of TANGENT arcs, so each one starts exactly where the last
    ended. Left in, those duplicates are zero-length segments: harmless in the DXF, but they
    make the outline's turn-by-turn direction undefined right at the tangency -- exactly
    where anything checking for creases wants to look.
    """
    sx, sy = seg
    if x and abs(x[-1] - sx[0]) < 1e-12 and abs(y[-1] - sy[0]) < 1e-12:
        sx, sy = sx[1:], sy[1:]
    x.extend(sx); y.extend(sy)


def create_circle_outline(cx, cy, radius, resolution=64):
    """A circle as (x, y) arrays, matching the (x, y) convention of the outlines here."""
    pts = create_polygon_circle(cx, cy, radius, resolution)
    return pts[:-1, 0], pts[:-1, 1]


def _tangent_point(px, py, cx, cy, r, side):
    """Where a line from (px, py) touches the circle (cx, cy, r); `side` = +1 right, -1 left.

    The tip's straight edge has to leave the trace and land on the round cap without a
    corner, which is exactly a tangent line from the trace edge to the cap circle. Both
    tangents are computed and picked between by x, rather than by a rotation sign that
    silently flips with the geometry.
    """
    ux, uy = px - cx, py - cy
    d = np.hypot(ux, uy)
    if d <= r:
        raise ValueError("the tip cap must be smaller than the taper it ends -- "
                         f"cap radius {r} reaches the taper's start at distance {d}")
    beta = np.arccos(r / d)
    both = []
    for b in (beta, -beta):
        cos_b, sin_b = np.cos(b), np.sin(b)
        both.append((cx + r * (ux * cos_b - uy * sin_b) / d,
                     cy + r * (ux * sin_b + uy * cos_b) / d))
    return max(both, key=lambda t: t[0]) if side > 0 else min(both, key=lambda t: t[0])


def create_polyimide_outline(xc, cy, pad_r, wire_hw, wire_top_y, bot_y, R, tip_r=None):
    """
    The full polyimide outline of one channel as a single closed polygon: the upper trace, a
    smooth LENS-shaped swelling around the round electrode site, and whatever the channel
    ends in below it.

    The swelling is the pad circle (radius pad_r) blended into the trace by tangent fillets
    of radius R (`_pad_fillet`), so the trace bulges out and back with no corner and no flat
    -- the smooth lens of `new_circular_electrode.png`, rather than the rounded square this
    used to draw. R is a free knob: it does not change how WIDE the swelling is (that is
    pad_r), only how LONG and how gentle the flare into it is.

    Below the swelling, `tip_r` picks between the two endings:

        tip_r > 0   a TIP: straight edges running from where the swelling closes all the
                    way down to a round cap of radius tip_r centred on the axis at bot_y.
                    Tangent to the cap, so the taper ends in the "pointy, with a slight
                    rounding" of the drawing rather than a needle or a blunt stub.
        tip_r None  a FLAT bottom at bot_y. This is the hook's channel: its polyimide does
                    not end here at all, it carries on into the hook, which is unioned onto
                    this outline afterwards.

    The shape is symmetric about the channel centre xc, so the +/-1 side only translates xc.

    Args:
        xc: channel centre x (microns)
        cy: pad centre y (microns)
        pad_r: pad radius -- the widest half-width of the swelling
        wire_hw: trace half-width above the pad (and below it, where the taper starts)
        wire_top_y: y of the flat top edge of the upper trace
        bot_y: y the channel ends at -- the tip point, or the hook's attach point
        R: fillet radius blending trace into pad (any R > 0; bigger = gentler)
        tip_r: radius of the rounding at the tip, or None for a flat bottom

    Returns:
        Tuple of (x, y) coordinate arrays tracing one closed polygon (clockwise).
    """
    h, alpha = _pad_fillet(pad_r, wire_hw, R)
    x, y = [], []
    N, F = PAD_ARC_N, FILLET_ARC_N

    def add(seg):
        _append_arc(x, y, seg)

    # Clockwise from the top-left of the upper trace, down the left side.
    x.append(xc - wire_hw); y.append(wire_top_y)
    add(_arc(xc - wire_hw - R, cy + h, R, 0.0, -alpha, F))           # left fillet into the pad
    add(_arc(xc, cy, pad_r, np.pi - alpha, np.pi + alpha, N))        # round the pad, left side
    add(_arc(xc - wire_hw - R, cy - h, R, alpha, 0.0, F))            # left fillet out of the pad

    if tip_r:
        #Taper from where the swelling closes down onto the round cap, tangent at the cap.
        cap_y = bot_y + tip_r
        tl = _tangent_point(xc - wire_hw, cy - h, xc, cap_y, tip_r, -1)
        tr = _tangent_point(xc + wire_hw, cy - h, xc, cap_y, tip_r, +1)
        #The two straight taper edges are drawn for free: the arc starts at tl and ends at
        #tr, so the gaps either side of it close onto the swelling's tangencies. Sweep the
        #LONG way (increasing angle), which is the way round that passes the very tip.
        a0 = np.arctan2(tl[1] - cap_y, tl[0] - xc)
        a1 = np.arctan2(tr[1] - cap_y, tr[0] - xc)
        add(_arc(xc, cap_y, tip_r, a0, a0 + (a1 - a0) % (2 * np.pi), TIP_ARC_N))
    else:
        x.append(xc - wire_hw); y.append(bot_y)                      # flat bottom, left
        x.append(xc + wire_hw); y.append(bot_y)                      # flat bottom, right

    add(_arc(xc + wire_hw + R, cy - h, R, np.pi, np.pi - alpha, F))  # right fillet into the pad
    add(_arc(xc, cy, pad_r, -alpha, alpha, N))                       # round the pad, right side
    add(_arc(xc + wire_hw + R, cy + h, R, np.pi + alpha, np.pi, F))  # right fillet out of the pad
    x.append(xc + wire_hw); y.append(wire_top_y)                     # upper trace top-right

    return np.array(x), np.array(y)


def create_inline_pad_outline(xc, cy, pad_r, wire_hw, R):
    """One contact sitting PART WAY ALONG a trace, rather than terminating it.

    Every recording site ends its fiber, so it is blended into the trace from above only
    and whatever is below is a tip or a hook. A REFERENCE contact has trace on both sides,
    so it needs the same tangent fillet flare mirrored underneath it -- the chamfer on the
    other side. The result is a symmetric lens: trace, flare out, circle, flare back in,
    trace.

    The polygon runs from `cy - h` to `cy + h`, where h is the fillet tangency height, and
    its two ends are exactly `wire_hw` half-width -- so it unions seamlessly onto the
    straight trace it interrupts, with no seam and no sliver. That is how it is used: the
    reference's trace is drawn as one long rectangle and these are unioned onto it.

    Same geometry as `create_polyimide_outline` with no tip and zero straight trace above
    the pad, which is exactly what an inline pad is; this wrapper exists to compute h and
    to say so in its name.
    """
    h, _ = _pad_fillet(pad_r, wire_hw, R)
    return create_polyimide_outline(xc, cy, pad_r, wire_hw,
                                    wire_top_y=cy + h, bot_y=cy - h, R=R, tip_r=None)


def inline_pad_reach(pad_r, wire_hw, R):
    """How far an inline pad's geometry reaches beyond its own centre, along the trace."""
    h, _ = _pad_fillet(pad_r, wire_hw, R)
    return h


def create_metal_pad_wire_outline(xc, cy, pad_r, wire_hw, wire_top_y, R):
    """
    One closed metal polygon per channel: the ROUND electrode pad (radius pad_r, centred at
    (xc, cy)) with the narrow wire (half-width wire_hw) rising from it up to wire_top_y,
    blended in by tangent fillets of radius R the same way `create_polyimide_outline` does.
    Nothing hangs below the pad, so the whole lower half is plain circle.

    Symmetric about xc, so left-side channels (xc < 0) work unchanged.
    Returns (x, y) tracing one closed polygon.
    """
    h, alpha = _pad_fillet(pad_r, wire_hw, R)
    x, y = [], []

    def add(seg):
        _append_arc(x, y, seg)

    # Clockwise from the wire top-left, all the way round the pad, back up the wire.
    x.append(xc - wire_hw); y.append(wire_top_y)
    add(_arc(xc - wire_hw - R, cy + h, R, 0.0, -alpha, FILLET_ARC_N))    # left fillet in
    #one arc all the way round: it starts and ends at the two fillet tangencies, so the
    #sample count is scaled by how much of the circle is actually pad
    add(_arc(xc, cy, pad_r, np.pi - alpha, 2 * np.pi + alpha,
             int(PAD_ARC_N * (np.pi + 2 * alpha) / np.pi)))
    add(_arc(xc + wire_hw + R, cy + h, R, np.pi + alpha, np.pi, FILLET_ARC_N))  # right fillet out
    x.append(xc + wire_hw); y.append(wire_top_y)
    return np.array(x), np.array(y)


def create_metal_pad_teardrop(cx, cy, pad_r, wire_hw, R, tail_dir, tail_len=None):
    """A round SOLDER pad flared into the trace that feeds it -- the pad's teardrop.

    Same construction as the electrode's `create_metal_pad_wire_outline`: the pad circle
    (radius pad_r at (cx, cy)) blended into a straight trace of half-width wire_hw by two
    tangent fillets of radius R (`_pad_fillet`), so the trace widens into the pad with no
    corner. The difference is aim: a solder pad is approached on the DIAGONAL, so the shape
    is built pointing up and then rotated onto `tail_dir` -- the unit vector from the pad
    centre back along its incoming trace.

    Why a teardrop here at all: the route ends AT the pad centre, so without it the trace
    meets the circle in two sharp notches. Those notches are where the etchant undercuts
    and where the solder fillet cracks, and they are the first thing to fail when the
    connector is pulled. R is a free knob -- it sets how LONG the flare is, not how wide.

    `tail_len` is how far the straight stub runs from the pad centre; it defaults to just
    past where the fillets land, and must reach at least that far. The stub is the same
    width as the trace it lies on, so the two coincide and the union is seamless.

    Returns (x, y) tracing one closed polygon.
    """
    h, _ = _pad_fillet(pad_r, wire_hw, R)
    if tail_len is None:
        tail_len = h + 1.0
    assert tail_len >= h, (
        f"a teardrop tail of {tail_len:.1f} um stops short of its own fillets, which land "
        f"{h:.1f} um from the pad centre -- raise tail_len or lower the fillet radius R")
    x, y = create_metal_pad_wire_outline(0.0, 0.0, pad_r, wire_hw, tail_len, R)
    #built pointing +y, so rotate by whatever turns +y onto tail_dir
    ux, uy = np.asarray(tail_dir, dtype=float) / np.hypot(*tail_dir)
    return cx + x * uy + y * ux, cy - x * ux + y * uy


def build_polyimide_fanout_body(bot_hw, fan_hw, top_hw,
                                y_bottom, y_fan_top, y_curve, y_top, curve_h, corner_R=0.0):
    """
    Closed polyimide body outline (x, y arrays), symmetric about x=0, built bottom->top:
      a flat BOTTOM edge (half-width bot_hw) hugging the wire bundle -> a diagonal FAN-OUT
      to (fan_hw, y_fan_top) -> a vertical STEM at fan_hw -> an S-TRANSITION `curve_h` tall
      out to the wide BLOCK (half-width top_hw), landing at y_curve -> straight up to y_top
      over the pad columns. Only the right-hand profile is built; it is mirrored to the left
      so the whole body is one closed, symmetric ring (top and bottom edges close implicitly).

    The shank -> block transition is an S of two tangent arcs, vertical at both ends, NOT a
    fillet-ledge-fillet step. The step version left a horizontal shelf wherever the two
    fillets did not meet (475 um of offset against 2 x 150 um of fillet = a 175 um shelf),
    and the shelf is where a flex device tears. Taller `curve_h` = gentler S; see
    `geometry.s_transition`.

    Args:
        bot_hw: bottom half-width (hugs the wire bundle + margin)
        fan_hw: stem half-width the fan-out opens to
        top_hw: wide block half-width over the pad columns
        y_bottom: y of the flat bottom edge (= wire_top)
        y_fan_top: y where the diagonal fan-out finishes (stem begins)
        y_curve: y where the transition LANDS on the block, i.e. where the block's parallel
            sides begin -- the drawing's "solder pad area start", which every solder-pad
            dimension is measured from
        y_top: y of the top edge over the pads
        curve_h: vertical run of the S-transition, which starts that far BELOW y_curve
        corner_R: if > 0, round the two outer bottom corners (where the flat bottom meets the
            fan-out diagonal) with a convex fillet of this radius. The trace<->body junction
            rounding downstream only rounds concave corners, so these convex corners are
            filleted here instead.

    The alignment tabs on the block edge are NOT drawn here: they differ left from right,
    and this builds one profile and mirrors it. `pad_block_tab` makes them instead, and they
    are unioned in with the rest of the polyimide.
    """
    if fan_hw < bot_hw:
        print(f"WARNING: POLYIMIDE_FAN_HW ({fan_hw}) < bottom half-width ({bot_hw:.1f}); "
              "the polyimide fans IN rather than out.")
    if curve_h <= 0:
        raise ValueError(f"the shank -> block transition needs a positive height, got {curve_h}")
    if not (y_bottom < y_fan_top <= y_curve - curve_h and y_curve < y_top):
        raise ValueError(
            f"the {curve_h} um shank -> block transition does not fit in the stem: it runs "
            f"from y={y_curve - curve_h} down-to-up onto the block at y={y_curve}, but the "
            f"fan-out only finishes at y={y_fan_top}. Shorten it, or give the design more "
            "ribbon.")

    rx, ry = [], []
    def add(seg): rx.extend(seg[0]); ry.extend(seg[1])
    if corner_R > 0:
        #Convex fillet of the outer bottom corner (bot_hw, y_bottom) where the horizontal
        #bottom meets the diagonal fan-out. Tangent length t = corner_R / tan(theta/2), with
        #theta the interior angle between the two edges (bottom -> corner, corner -> stem top).
        e_in = np.array([bot_hw - 0.0, 0.0])                     # direction into the corner (bottom, +x)
        e_out = np.array([fan_hw - bot_hw, y_fan_top - y_bottom])# direction out (up the diagonal)
        e_in = e_in / np.hypot(*e_in); e_out = e_out / np.hypot(*e_out)
        theta = np.arccos(np.clip(np.dot(-e_in, e_out), -1.0, 1.0))  # interior angle at the corner
        t = corner_R / np.tan(theta / 2.0)
        p_bot = np.array([bot_hw - t, y_bottom])                 # tangent point on the flat bottom
        p_dia = np.array([bot_hw, y_bottom]) + e_out * t         # tangent point on the diagonal
        cen = p_bot + np.array([0.0, corner_R])                  # arc centre (corner_R above the bottom)
        a0 = np.arctan2(p_bot[1] - cen[1], p_bot[0] - cen[0])
        a1 = np.arctan2(p_dia[1] - cen[1], p_dia[0] - cen[0])
        rx.append(p_bot[0]); ry.append(p_bot[1])                 # bottom (start of the fillet)
        add(_arc(cen[0], cen[1], corner_R, a0, a1))              # convex outer-bottom fillet
        rx.append(fan_hw); ry.append(y_fan_top)                  # end of diagonal fan-out
    else:
        rx.append(bot_hw); ry.append(y_bottom)                   # bottom (hugs bundle + margin)
        rx.append(fan_hw); ry.append(y_fan_top)                  # end of diagonal fan-out
    rx.append(fan_hw); ry.append(y_curve - curve_h)               # up the stem to the S
    add(s_transition(fan_hw, y_curve - curve_h, top_hw, y_curve, n=48))   # stem -> block
    rx.append(top_hw); ry.append(y_top)                           # up the block to the top edge
    x = rx + [-v for v in rx[::-1]]                               # mirror to the left, top->bottom
    y = ry + ry[::-1]
    return np.array(x), np.array(y)


def pad_block_tab(edge_x, top_y, length, resolution=64):
    """One alignment tab on the solder-pad block's edge, as a closed (x, y) outline.

    The tab is a half-disc standing proud of an otherwise straight edge: `length` along the
    edge, half that deep. It is drawn as a FULL circle centred ON the edge -- the half inside
    the block vanishes when this is unioned with the body, and starting from a circle leaves
    no sliver where the two outlines meet.

    The drawing aligns both tabs by their upper END, not by their centres, and they are
    different lengths, so `top_y` is where the arc starts and it runs DOWNWARD from there.
    """
    if length <= 0:
        raise ValueError(f"an alignment tab needs a positive length, got {length}")
    r = length / 2.0
    return create_circle_outline(edge_x, top_y - r, r, resolution)


def merge_polyimide_with_fillets(body_xy, part_xys, band_ylo, band_yhi, R):
    """
    Union the fanout body with every per-channel polyimide part (traces + hooks) into ONE
    polygon, then ROUND the trace<->body junction corners with radius R. The rounding is a
    morphological closing (dilate by R, erode by R) applied ONLY inside the horizontal band
    [band_ylo, band_yhi] where the junctions live, so everything outside the band (pads,
    tips, hook barbs) is preserved exactly.

    Because closing narrows every gap by 2R before restoring it, R must be < half the
    inter-trace gap or neighbouring traces fuse; that also caps the notch-ceiling fillet
    radius at R.

    Inputs:
        body_xy: (x_array, y_array) of the fanout body outline.
        part_xys: list of (x_array, y_array), one per polyimide trace/hook.
        band_ylo, band_yhi: y-range of the junction band to round within.
        R: fillet radius.

    Returns:
        list of exterior rings [[(x, y), ...], ...] (usually one).
    """
    from shapely.geometry import Polygon, box
    from shapely.ops import unary_union
    polys = [Polygon(list(zip(body_xy[0], body_xy[1]))).buffer(0)]
    for px, py in part_xys:
        polys.append(Polygon(list(zip(px, py))).buffer(0))
    merged = unary_union(polys)
    xmin, _, xmax, _ = merged.bounds
    band = box(xmin - 1.0, band_ylo, xmax + 1.0, band_yhi)
    closed = merged.intersection(band).buffer(R, join_style=1).buffer(-R, join_style=1)
    result = unary_union([merged.difference(band), closed.intersection(band)])
    geoms = list(result.geoms) if result.geom_type == "MultiPolygon" else [result]
    return [list(g.exterior.coords) for g in geoms]


def polygon_to_seamed_ring(g):
    """
    Flatten a shapely Polygon-with-holes into ONE closed ring (list of (x, y)) so a single
    LWPOLYLINE renders as a filled region with EMPTY holes: each hole is tied to the outer
    boundary by a zero-width "keyhole" seam (traversed there-and-back), leaving the hole a
    true void even in viewers that fill each closed polyline independently. Holes are spliced
    at their nearest vertex to the current ring, keeping the seam short.
    """
    ring = list(g.exterior.coords)[:-1]                       # drop the closing duplicate
    for interior in g.interiors:
        inn = list(interior.coords)[:-1]
        best = None                                           # nearest (ring vertex, hole vertex)
        for a, (ex, ey) in enumerate(ring):
            for b, (ix, iy) in enumerate(inn):
                dd = (ex - ix) ** 2 + (ey - iy) ** 2
                if best is None or dd < best[0]:
                    best = (dd, a, b)
        _, a, b = best
        inn_seq = inn[b:] + inn[:b] + [inn[b]]                # traverse the hole, start & end at b
        ring = ring[:a + 1] + inn_seq + ring[a:]              # splice in with the there-and-back seam
    ring.append(ring[0])
    return ring


def text_outlines(text, origin, height, rotation_deg=0.0, align="left", valign="bottom",
                  mirror=False, flatness=0.02, line_gap=0.25):
    """Text as real closed OUTLINES, ready to be written to a mask layer.

    A DXF TEXT entity is an annotation, not a feature: it would render in a viewer and then
    vanish on the GDS export, so a design ID stamped that way never reaches the wafer. This
    converts the string to glyph outlines instead, so the marking is patterned in whatever
    layer it is written to like any other geometry.

    Counters (the holes in O, P, 0, 4) come back as separate rings, and filling them would
    turn the label into a row of blobs. They are resolved by EVEN-ODD: symmetric-differencing
    the rings leaves a ring inside a ring as a hole, exactly as the font intends. Each result
    is then flattened to one closed ring by `polygon_to_seamed_ring`, so a glyph with a
    counter is still a single polyline.

    `text` may contain "\\n": each line is laid out independently and stacked, first line on
    top, all lines sharing one bounding box, alignment, rotation and mirror.

    Placement is by BOUNDING BOX, not by the font's baseline: `origin` is where the label's
    lower-left corner lands by default (or another corner/edge -- see `align`/`valign`).
    Glyphs overshoot their nominal size in both directions -- accents above, commas below --
    so a label placed by its baseline into a gap that just fits will quietly overhang it.

    `mirror` flips the label left-right so it reads correctly when the finished part is
    viewed from the opposite face it was drawn for -- e.g. a back-face design meant to be
    read from the front. It is applied AFTER rotation, not to the glyphs' own unrotated
    frame: mirroring is a reflection of the label as actually placed, so doing it before
    rotation would give the wrong result for anything rotated off 0 deg.

    Args:
        text: the string, "\\n"-separated for multiple lines
        origin: (x, y) the label's bounding box is placed at, AFTER rotation and mirroring
        height: font size in drawing units
        rotation_deg: turn the label; 90 makes it read upward, which costs width instead of
            length -- the point, on a part that is stacked across x
        align: "left" puts the box's x-min at origin.x, "right" its x-max, "center" its
            x-midpoint (of the whole block's bounding box, not per-line)
        valign: "bottom" puts the box's y-min at origin.y (growing `height` pushes the top
            up), "top" puts the box's y-max there instead (growing `height` pushes the
            bottom down) -- pick whichever edge must stay fixed while the label is resized
        mirror: flip the label left-right (see above)
        flatness: curve flattening tolerance, as a fraction of `height`
        line_gap: extra space between stacked lines, as a fraction of `height`

    Returns:
        list of closed rings [[(x, y), ...], ...], one per glyph (or per merged group).
    """
    from functools import reduce
    from ezdxf.addons import text2path
    from ezdxf.fonts.fonts import FontFace
    from shapely.geometry import Polygon

    line_step = height * (1.0 + line_gap)
    polys = []
    for li, line in enumerate(str(text).split("\n")):
        paths = text2path.make_paths_from_str(line, FontFace(family="DejaVu Sans"),
                                              size=float(height))
        y_shift = -li * line_step                    # first line on top, rest drop down
        for path in paths:
            pts = [(v.x, v.y + y_shift) for v in path.flattening(distance=flatness * height)]
            if len(pts) >= 3:
                polys.append(Polygon(pts).buffer(0))
    if not polys:
        return []
    filled = reduce(lambda a, b: a.symmetric_difference(b), polys)

    th = np.radians(rotation_deg)
    cos_t, sin_t = np.cos(th), np.sin(th)
    rings = []
    for g in (filled.geoms if filled.geom_type == "MultiPolygon" else [filled]):
        if not g.is_empty:
            rings.append([(x * cos_t - y * sin_t, x * sin_t + y * cos_t)
                          for x, y in polygon_to_seamed_ring(g)])
    if not rings:
        return []
    if mirror:
        rings = [[(-x, y) for x, y in r] for r in rings]
    pts = [p for r in rings for p in r]
    xs = [p[0] for p in pts]
    if align == "right":
        x_ref = max(xs)
    elif align == "center":
        x_ref = (min(xs) + max(xs)) / 2.0
    else:
        x_ref = min(xs)
    ys = [p[1] for p in pts]
    y_ref = max(ys) if valign == "top" else min(ys)
    dx, dy = origin[0] - x_ref, origin[1] - y_ref
    return [[(x + dx, y + dy) for x, y in r] for r in rings]


def build_pad_columns(row_pitch, first_y, pitch, n_per_column):
    """
    Centres of the two pad columns ("rows"), each a vertical stack of n_per_column pads
    at `pitch` spacing starting at `first_y`. Returns (left, right) lists of (x, y),
    each sorted by ascending y. Index 0 (lowest) is the REF/GND pad; indices 1.. connect.
    """
    col_x = row_pitch / 2.0
    left = [(-col_x, first_y + i * pitch) for i in range(n_per_column)]
    right = [(col_x, first_y + i * pitch) for i in range(n_per_column)]
    return left, right


#WHERE THE REFERENCE LANDS ON THE CONNECTOR. Not computed -- the flex PCB is fixed
#hardware, and row 0 of each column is its REF/GND pad (flex numbers 65 left, 66 right).
#The reference takes the RIGHT one; row 0 of the left column stays unconnected, the only
#spare pad on the device. Row 0 is the pad NEAREST the bundle, i.e. the far end of the
#column from where the innermost channel lands, which is why reaching it needs the
#wrap-around in build_pad_routes rather than an ordinary diagonal.
#
#WHICH SIDE is the ONE thing to change to move it: everything downstream reads the side
#off this constant. `_build_ref_wrap_route` signs its corridor, its exit diagonal and the
#teardrop's tail off the pad's own x, and `mapping` reports the connected REF/GND pad
#number from here, so neither has a left in it to miss.
REF_PAD = ('right', 0)


def assign_channels_to_pads(top_points):
    """
    channel index -> (column, pad_row) reproducing the crossing-free routing order.

    Channels are sorted by x; the lowest-x half serves the LEFT pad column, the
    highest-x half the RIGHT. Within the left column the outer lane (lowest x) peels to
    the LOWEST connected pad; within the right column the outer lane (highest x) peels to
    the lowest pad (hence the right half is reversed). pad_row is 1..N-1 (row 1 = lowest
    connected pad); row 0 of each column is the REF/GND pad and no recording channel is
    ever assigned one -- the right row 0 belongs to the reference fiber (REF_PAD) and the
    left row 0 is the device's one spare pad.

    This is the single source of truth for the ordering: build_pad_routes() consumes it to
    place each channel's diagonal into the right pad, and mapping.build_mapping() consumes
    it to attach flex-pad numbers. (It used to be duplicated in
    build_electrode_flex_mapping.derive_channel_to_pad.)
    """
    n = len(top_points)
    #RECORDING channels only, and there must be an even number of them: the split below is
    #n // 2, so an odd count silently overfills one column and asks for a pad row that does
    #not exist. The reference is NOT passed in here -- it is a fiber, not a channel, and its
    #pad is fixed at REF_PAD.
    assert n % 2 == 0, \
        f"assign_channels_to_pads got {n} channels; it splits them evenly between the two " \
        f"pad columns, so the count must be even (the reference fiber does not belong in " \
        f"this list -- its pad is REF_PAD)"
    order = sorted(range(n), key=lambda i: top_points[i][0])   # channels by x ascending
    half = n // 2
    left_ch, right_ch = order[:half], order[half:]
    chan_pad = {}
    for k, ci in enumerate(left_ch):                # left column: outer lane -> lowest pad
        chan_pad[ci] = ('left', k + 1)
    for k, ci in enumerate(right_ch[::-1]):         # right column: reversed, outer lane -> lowest pad
        chan_pad[ci] = ('right', k + 1)
    return chan_pad


def build_pad_routes(top_points, pads_left, pads_right,
                     neck_tw, top_tw, neck_len, fan_len,
                     bundle_pitch, approach_deg,
                     ref_top_point=None, ref_tw=None, pad_r=0.0,
                     poly_hw=None, poly_top_y=None,
                     #THE REFERENCE ROUTE'S SHAPE IS TUNED IN config.py, not here.
                     #`bundle.py` always passes all four of these from the matching
                     #BundleConfig.ref_wrap_* fields, so editing the values below changes
                     #nothing about a real build -- they exist only for calling this
                     #function on its own. Change BundleConfig.
                     wrap_clearance=100.0, wrap_exit_run=875.0,
                     wrap_corner_chamfer=380.0, wrap_pad_chamfer=150.0):
    """
    Route every electrode wire up into its bond pad, keeping the traces as ONE compact
    bundle in the MIDDLE and fanning OUT to the side pad columns only near the top. Each
    channel = one closed polygon: a vertical NECK above the wire top, a short FAN that
    gathers the wire into a centred bundle lane, a vertical RISER straight up the middle,
    and a straight DIAGONAL that peels out into the pad. The diagonal meets the riser at
    `approach_deg` (interior bend angle), i.e. it climbs `approach_deg - 90` degrees above
    horizontal (140 deg -> a 50 deg climb). Width tapers neck_tw -> top_tw.

    Crossing-free by construction: channels are sorted by x into one centred bundle (so the
    middle is filled, no gap); the lowest 32 lanes serve the left column, the highest 32 the
    right (see assign_channels_to_pads). Within a side the OUTER lane (nearest its column)
    peels to the LOWEST pad and the inner lane to the highest, and every diagonal shares the
    same angle, so the parallel diagonals and the vertical risers never cross.

    Inputs:
        top_points (list[(x_src, y_top)]): one wire top per channel.
        pads_left, pads_right (list[(x, y)]): pad centres per column, ascending y, index 0
            = REF/GND (skipped); indices 1.. are the connected pads.
        neck_tw, top_tw, neck_len, fan_len: gather-fan geometry (micron).
        bundle_pitch (float): lane pitch of the centred bundle.
        approach_deg (float): interior bend angle of the diagonal into the pad.

    THE REFERENCE takes a lane like everything else, and it is given one HERE rather than
    squeezed in afterwards. Lanes are `(pos - mid) * bundle_pitch` over however many fibers
    there are: with 64 recording channels alone that puts lanes at +/-12.5, +/-37.5 ... and
    leaves NO lane on x = 0, only a half-pitch slot straddling it. Counting the reference in
    makes it 65 lanes at (pos - 32) * pitch, so every lane keeps the full pitch and the
    reference sits exactly on x = 0. It costs 25 um of bundle width (+/-800 rather than
    +/-787.5) against a polyimide fan half-width of 950.

    The reference cannot take an ordinary diagonal. Row 0 is the pad NEAREST the bundle, and
    the innermost lane reaching the lowest pad is precisely the crossing case this ordering
    exists to avoid -- it would cut every riser between them, and there is no second metal
    layer. So it goes OVER THE TOP instead: straight up the centre lane past the whole pad
    field, out sideways above it, down the empty corridor OUTSIDE the pad column, and into
    row 0 from the side. It crosses nothing, and two facts make that true:

      - x = 0 is the one lane no diagonal ever crosses. Left-column diagonals run from a
        lane at x <= -pitch out to x = -pad_row_pitch/2; right-column ones mirror that. The
        centre lane is the only one with empty space above it all the way up.
      - beyond the pad centres there is nothing. Every channel's route TERMINATES at its pad
        centre, so no metal reaches further out than pad_r past it, and the gap from there
        to the polyimide edge is free.

    Returns:
        (polys, ref_ring, pad_tails): dict[int, list[(x, y)]] per-channel closed polygon
        ring (None entries skipped), the reference's ring (None when `ref_top_point` is
        None), and dict[(column, pad_row)] -> the unit vector pointing from that pad's
        centre back along the trace that arrives there. The caller draws the pads
        themselves and needs the approach direction to aim each teardrop
        (`create_metal_pad_teardrop`); a pad no route reaches -- the spare -- is simply
        absent from the dict.
    """
    n = len(top_points)
    lanes = [(top_points[i][0], i) for i in range(n)]
    if ref_top_point is not None:
        lanes.append((ref_top_point[0], 'ref'))
    order = [k for _, k in sorted(lanes, key=lambda e: e[0])]  # fibers by x ascending
    mid = (len(order) - 1) / 2.0
    lane_x = {k: (pos - mid) * bundle_pitch for pos, k in enumerate(order)}  # centred bundle
    slope = np.tan(np.radians(approach_deg - 90.0))            # diagonal climb above horizontal
    chan_pad = assign_channels_to_pads(top_points)             # shared ordering (single source of truth)
    pad_cols = {'left': pads_left, 'right': pads_right}

    polys = {}
    pad_tails = {}
    max_bend_y = -np.inf
    for ci in range(n):
        col, pad_row = chan_pad[ci]
        x_src, y_top = top_points[ci]
        x_lane = lane_x[ci]
        pad_x, y_pad = pad_cols[col][pad_row]                  # index 0 = REF/GND; pad_row 1.. connect
        y_neck = y_top + neck_len
        y_fan = y_neck + fan_len
        y_bend = y_pad - abs(pad_x - x_lane) * slope           # peel-off height of the diagonal
        max_bend_y = max(max_bend_y, y_bend)
        centerline = [
            (x_src, y_top - 1.0),  # overlap the vertical wire to avoid a seam at the join
            (x_src, y_neck),   # top of neck
            (x_lane, y_fan),   # gathered into the centred bundle lane
            (x_lane, y_bend),  # straight up the middle to the peel-off height
            (pad_x, y_pad),    # diagonal out into the pad centre
        ]
        widths = [neck_tw, neck_tw, top_tw, top_tw, top_tw]
        polys[ci] = stroke_centerline_to_polygon(centerline, widths)
        #the diagonal's own direction, read off the last leg: this is where the pad's
        #teardrop has to point
        _t = np.array([x_lane - pad_x, y_bend - y_pad], dtype=float)
        pad_tails[(col, pad_row)] = tuple(_t / np.hypot(*_t))

    ref_ring = None
    if ref_top_point is not None:
        #the reference comes in from the SIDE, down the corridor outside its column, so its
        #teardrop lies along x -- pointing outward, away from the bundle
        pad_tails[REF_PAD] = (-1.0, 0.0) if pad_cols[REF_PAD[0]][REF_PAD[1]][0] < 0 else (1.0, 0.0)
        ref_ring = _build_ref_wrap_route(
            ref_top_point, lane_x['ref'], pad_cols[REF_PAD[0]], REF_PAD[1],
            ref_tw=ref_tw if ref_tw is not None else neck_tw, top_tw=top_tw,
            neck_len=neck_len, fan_len=fan_len, pad_r=pad_r,
            poly_hw=poly_hw, poly_top_y=poly_top_y, wrap_clearance=wrap_clearance,
            max_bend_y=max_bend_y, slope=slope, exit_run=wrap_exit_run,
            corner_chamfer=wrap_corner_chamfer, pad_chamfer=wrap_pad_chamfer)
    return polys, ref_ring, pad_tails


def _point_segment_distance(p, a, b):
    """Shortest distance from point `p` to the segment `a`-`b`. Plain arithmetic rather
    than shapely: this runs inside the geometry build, which has no shapely import."""
    p, a, b = np.asarray(p, float), np.asarray(a, float), np.asarray(b, float)
    ab = b - a
    denom = float(ab @ ab)
    t = 0.0 if denom < 1e-12 else float(np.clip(((p - a) @ ab) / denom, 0.0, 1.0))
    return float(np.hypot(*(p - (a + t * ab))))


def _build_ref_wrap_route(ref_top_point, x_lane, pad_col, pad_row,
                          ref_tw, top_tw, neck_len, fan_len, pad_r,
                          poly_hw, poly_top_y, wrap_clearance, max_bend_y, slope,
                          exit_run, corner_chamfer, pad_chamfer):
    """The reference's over-the-top route into the REF/GND pad. See build_pad_routes.

    Everything it has to clear is asserted rather than assumed: the corridor it descends
    has to sit outside every pad in the column and inside the polyimide edge, and the
    sideways run above the pad field has to sit above the topmost pad and below the top of
    the block. `wrap_clearance` is the margin demanded at each of those, edge to edge.
    """
    x_src, y_top = ref_top_point
    pad_x, y_pad = pad_col[pad_row]
    y_top_pad = max(y for _, y in pad_col)
    hw = top_tw / 2.0

    #Out past the pads, in from the polyimide edge. Placed midway between the two so the
    #corridor is as forgiving as it can be rather than hugging either wall.
    x_out = abs(pad_x) + pad_r + wrap_clearance + hw
    x_in = poly_hw - wrap_clearance - hw
    assert x_out <= x_in, (
        f"no corridor for the reference route outside the {abs(pad_x):.0f} um pad column: "
        f"it needs to clear the pads at x={x_out:.1f} but the polyimide edge only allows "
        f"x={x_in:.1f} (polyimide half-width {poly_hw:.0f}, clearance {wrap_clearance:.0f})")
    x_corr = -0.5 * (x_out + x_in) if pad_x < 0 else 0.5 * (x_out + x_in)

    #THE SHAPE OF THE TOP. Structurally this can NOT be one diagonal all the way from the
    #centre lane out to the corridor, however much that would mimic a channel's riser +
    #diagonal: the topmost pad is in the way, and a full-span run passes straight THROUGH
    #it. So the top is necessarily an exit diagonal, then a turn down into the corridor,
    #with whatever they leave over staying horizontal -- see BundleConfig.ref_wrap_* for
    #the arithmetic that shares the span between the three.
    #
    #The EXIT diagonal climbs at `approach_deg`, the same angle every channel's peel-off
    #uses, so it runs PARALLEL to every left-column diagonal beside it -- which is exactly
    #why those never cross each other. The corner below it stays at 45 deg: it is bounded by
    #the topmost pad, not by anything to do with matching the fanout.
    span = abs(x_corr - x_lane)
    side = -1.0 if pad_x < 0 else 1.0
    run = min(exit_run, span - corner_chamfer)
    assert run > 0, (
        f"ref_wrap_exit_run {exit_run:.0f} and ref_wrap_corner_chamfer "
        f"{corner_chamfer:.0f} do not both fit in the {span:.0f} um between the centre "
        f"lane and the corridor")
    rise = run * slope
    x_peak = x_lane + side * run
    x_turn = x_corr - side * corner_chamfer

    def _top(y_wrap):
        """The top of the route, as (centerline, widths), for a given crossing height."""
        return ([(x_lane, y_wrap - rise),        # up the middle, past the whole pad field
                 (x_peak, y_wrap),               # out on the channels' own diagonal
                 (x_turn, y_wrap),               # whatever horizontal is left over
                 (x_corr, y_wrap - corner_chamfer)],   # 45 deg down, clear of the top pad
                [top_tw] * 4)

    #How high the drawn metal sits above its own centerline, MEASURED rather than derived.
    #It is not simply hw: where two diagonals meet, the mitre at that joint projects
    #hw/sin(half the turn) above the centerline -- 1.41*hw for two 45 deg legs, more as the
    #angles differ. Stroking the top once at y=0 gives the real number for whatever angles
    #and whatever bar length the config asks for, so the clearance below is honest.
    probe = stroke_centerline_to_polygon(*_top(0.0))
    overshoot = max(y for _, y in probe)

    #The crossing goes as high as the block allows, which puts it ABOVE the design-ID
    #marking rather than level with it -- the marking sits in this same band.
    y_wrap = poly_top_y - wrap_clearance - overshoot
    #The CROSSING itself has to sit above the top pad. Only the crossing: the corner below
    #it descends past that pad on its way down, 275 um to the side of it, and how close it
    #really gets is the per-pad check below, not this one.
    assert y_wrap >= y_top_pad + pad_r + wrap_clearance + hw, (
        f"no headroom for the reference route above the pad field: the top pad edge is at "
        f"y={y_top_pad + pad_r:.1f} and the polyimide ends at y={poly_top_y:.1f}, which "
        f"leaves less than the {wrap_clearance:.0f} um clearance it needs at both")
    #EVERY PAD IN THE COLUMN, against every segment of the top. The exit diagonal is NOT
    #automatically clear of them: it is free only while it stays inside the pad column's x
    #range, and a long enough run reaches over the topmost pad and closes on it from above.
    #At approach_deg the margin is gone by exit_run ~= 960. Checked here, on the drawn
    #extent, so an over-long run fails the build with the number it needs.
    top_pts = _top(y_wrap)[0]
    need = pad_r + wrap_clearance + hw
    for px, py in pad_col:
        for a, b in zip(top_pts, top_pts[1:]):
            got = _point_segment_distance((px, py), a, b)
            assert got >= need, (
                f"the reference's crossing passes {got - pad_r - hw:.1f} um from the pad at "
                f"({px:.0f}, {py:.0f}), inside the {wrap_clearance:.0f} um it must keep. "
                f"Shorten ref_wrap_exit_run (it is {exit_run:.0f}) or "
                f"ref_wrap_corner_chamfer (it is {corner_chamfer:.0f})")

    assert y_wrap - rise > max_bend_y, (
        f"the reference's exit diagonal would start at y={y_wrap - rise:.1f}, at or below "
        f"the highest channel peel-off (y={max_bend_y:.1f}), and cut through the fanout -- "
        f"shorten ref_wrap_exit_run (it is {exit_run:.0f})")

    centerline = ([(x_src, y_top - 1.0),   # overlap the fiber's trace, no seam at the join
                   (x_src, y_top + neck_len),                    # top of neck
                   (x_lane, y_top + neck_len + fan_len)]         # into the centre lane
                  + _top(y_wrap)[0]
                  + [(x_corr, y_pad),      # down the empty corridor OUTSIDE the pad column
                     (pad_x, y_pad)])      # in to the pad, from the side
    widths = [ref_tw, ref_tw] + [top_tw] * 7

    #Only the turn IN TO THE PAD is left to the chamfer helper -- it is the one corner that
    #genuinely wants clamping, since its approach run is just 280 um. Lengths are positional
    #over the interior vertices; everything above is already a bend, not a square corner.
    lengths = [0.0] * 6 + [pad_chamfer]

    centerline, widths = chamfer_corners(centerline, widths, lengths)
    return stroke_centerline_to_polygon(centerline, widths)


def chamfer_corners(points, widths, length):
    """Replace each interior corner of a polyline with a straight 45 deg chamfer.

    `length` is either one value for every corner or a sequence with ONE ENTRY PER INTERIOR
    VERTEX (len(points) - 2), positional -- collinear vertices consume an entry and are
    skipped, so the indexing stays readable against the centerline it came from. A per
    corner list is what the reference route needs: its three corners have very different
    amounts of room around them.

    A corner vertex becomes two, backed off `length` along each of its legs, so the square
    turn becomes a short diagonal. `length` is clamped to HALF the shorter adjacent
    segment, per corner, so chamfering can never eat a whole segment or reorder the path --
    a corner between two short legs simply gets a small chamfer. Collinear vertices (no
    actual turn) are left alone rather than being split into two coincident points.

    Returns the new (points, widths).
    """
    if len(points) < 3:
        return points, widths
    lengths = ([float(length)] * (len(points) - 2) if np.isscalar(length)
               else [float(v) for v in length])
    assert len(lengths) == len(points) - 2, \
        f"chamfer_corners got {len(lengths)} lengths for {len(points) - 2} interior corners"
    if not any(v > 0 for v in lengths):
        return points, widths
    pts = [np.asarray(q, dtype=float) for q in points]
    out_p, out_w = [pts[0]], [widths[0]]
    for i in range(1, len(pts) - 1):
        back, fwd = pts[i - 1] - pts[i], pts[i + 1] - pts[i]
        lb, lf = np.hypot(*back), np.hypot(*fwd)
        cross = back[0] * fwd[1] - back[1] * fwd[0]
        if lb < 1e-9 or lf < 1e-9 or abs(cross) < 1e-9 * lb * lf:
            out_p.append(pts[i]); out_w.append(widths[i])      # straight through
            continue
        c = min(lengths[i - 1], lb / 2.0, lf / 2.0)
        if c <= 0:
            out_p.append(pts[i]); out_w.append(widths[i])
            continue
        out_p.append(pts[i] + back / lb * c); out_w.append(widths[i])
        out_p.append(pts[i] + fwd / lf * c); out_w.append(widths[i])
    out_p.append(pts[-1]); out_w.append(widths[-1])
    return [tuple(q) for q in out_p], out_w


def fiber_x_positions(n_channels, delta_x, wire_hw, ref_hw):
    """Signed x of every fiber on the shank: the REFERENCE plus the recording channels.

    There are `n_channels + 1` fibers. The reference takes the CENTRE slot -- it is the one
    carrying the insertion hook and the one that is widened -- and the recording channels
    fill the slots outward from it, alternating sides. With 64 recording channels that is
    slots -32..+32, which is symmetric: 32 channels each side of the reference, and the
    bundle's centre of symmetry is the reference fiber itself.

    That symmetry is the reason the reference goes in the middle rather than at an edge.
    With an even fiber count the alternating layout gives one more slot on +x than on -x, so
    `channel_x_positions` has to shift the whole bundle by ~delta_x/2 to make its outer edges
    symmetric and the centre fiber lands OFF axis (it sat at x = -12.0 with 64 fibers). An
    odd count needs no shift at all and the centre fiber sits exactly on x = 0.

    Recording channel `c` is fiber `c + 1`, so channel 0 (the deepest site) takes slot +1 and
    the even channels run up the +x side, the odd ones up -x.

    Returns:
        (chan_x, ref_x): dict {recording channel -> x}, and the reference fiber's x.
    """
    half_widths = {0: ref_hw}
    half_widths.update({c + 1: wire_hw for c in range(n_channels)})
    pos = channel_x_positions(n_channels + 1, delta_x, half_widths, wire_hw)
    return {c: pos[c + 1] for c in range(n_channels)}, pos[0]


def channel_x_positions(n, delta_x, half_widths, wire_hw):
    """
    Signed x of each channel so the edge-to-edge GAP between neighbouring polyimide traces
    stays constant (= delta_x - 2*wire_hw) even when some channels are wider than wire_hw.

    Channels are laid out on alternating sides from the centre: odd i -> +slot, even i ->
    -slot, matching the original `bx = x_counter*delta_x` interleave. Each slot is placed by
    accumulating half-widths + gap outward from the centre, so:
      - with uniform widths it reproduces the original delta_x grid exactly,
      - a wider channel keeps its gaps by pushing only the channels FURTHER from the centre
        outward; every pitch that doesn't touch a wide channel is unchanged.

    The alternating layout gives one more slot on the +side than the -side for even n (e.g.
    64 -> slots -31..+32), so the raw bundle is off-centre by ~delta_x/2. A final shift
    recentres it so its outer EDGES are symmetric about x=0 -- this keeps the bundle aligned
    with the symmetric polyimide body and the +/-PAD_ROW_PITCH/2 pad columns, giving equal
    margins on both sides. (Channel 0 therefore lands slightly off x=0 for even n.)

    half_widths: dict {channel_index: polyimide-trace half-width}.
    """
    gap = delta_x - 2 * wire_hw
    slot_of = {i: (0 if i == 0 else ((i + 1) // 2 if i % 2 else -(i // 2))) for i in range(n)}
    chan_at = {s: i for i, s in slot_of.items()}
    pos = {0: 0.0}
    s = 1
    while s in chan_at:
        pos[s] = pos[s - 1] + half_widths[chan_at[s - 1]] + gap + half_widths[chan_at[s]]
        s += 1
    s = -1
    while s in chan_at:
        pos[s] = pos[s + 1] - (half_widths[chan_at[s + 1]] + gap + half_widths[chan_at[s]])
        s -= 1
    #Recentre on the bundle's outer edges so both margins are equal (see docstring).
    smax, smin = max(pos), min(pos)
    center = 0.5 * ((pos[smax] + half_widths[chan_at[smax]]) +
                    (pos[smin] - half_widths[chan_at[smin]]))
    return {i: pos[slot_of[i]] - center for i in range(n)}
