import numpy as np
import ezdxf
from shapely.geometry import Polygon, MultiPolygon
from shapely.affinity import affine_transform

from lib.generate_helpers import (create_rectangle, convert_rectangle_to_polyline, point_reflect,
                                  bulb_profile, circle_polyline, stroke_centerline_to_polygon)
from lib.active import in_designs as _in_designs      # bare filename -> designs/

# Every knob lives in config_<name>.py (section 1) -- edit it there. Two configs use this
# generator: 12block (the connector design) and dummy (the dummy device's pad grid).
import argparse as _argparse, importlib as _importlib
CONFIGS = ("12block", "dummy")
_pre = _argparse.ArgumentParser(add_help=False)
_pre.add_argument("--config", choices=CONFIGS, default="12block")
CONFIG_NAME = _pre.parse_known_args()[0].config
_cfg = _importlib.import_module("config_" + CONFIG_NAME)
CONFIG = _cfg.GEN_CONFIG          # the pad grid
ROUTING = _cfg.GEN_ROUTING        # per-column Metal1 routing + vias
POLYIMIDE = _cfg.GEN_POLYIMIDE    # substrate outline + negative band

DEFAULT_OUT = _cfg.DEFAULT_IN     # what stage 02 reads by default

# ======================================================================================
# Command line: which config, and where to write
# ======================================================================================
# Pad size, gap and the via position depend on each other (via_offset_y = pad_side/2 -
# via_offset_x keeps the via in the pad's top-left corner), so edit them together in the config.
#
# NOTE the wire-count invariant: sum over columns of (n_rows - row_start(col))
# must equal the router's TOTAL_WIRES (768). Run 00_preflight.py before stage 02.

def _apply_cli(config, argv=None):
    """Parse --config / --out and set the output path in `config` (mutates in place)."""
    import argparse
    p = argparse.ArgumentParser(
        description="Generate the 12-block / dummy interconnect DXF (pad grid + per-column routing). "
                    "Pad sizes are set in config_<name>.py.")
    p.add_argument("--config", choices=CONFIGS, default="12block",
                   help="which config_<name>.py to build (default: %(default)s)")
    p.add_argument("--out", default=DEFAULT_OUT,
                   help="output DXF; bare name -> designs/ (default: %(default)s)")
    a = p.parse_args(argv)
    config["out_path"] = _in_designs(a.out)
    print("pad grid: %d x %d, pad_side=%g um, gap=%g um, extra=%g um -> %s"
          % (config["n_rows"], config["n_cols"], config["pad_side"], config["gap"],
             config["pad_side_extra"], config["out_path"]))
    return a


if __name__ == "__main__":

    _apply_cli(CONFIG)

    pad_side = CONFIG["pad_side"]
    pad_side_extra = CONFIG.get("pad_side_extra") or pad_side   # EtchingPad side; None -> same as pad_side
    pitch = pad_side + CONFIG["gap"]   # pad centre-to-centre spacing

    doc = ezdxf.new('R2010')
    msp = doc.modelspace()
    doc.layers.new(name='Metal1', dxfattribs={'linetype': 'CONTINUOUS', 'color': 4})
    doc.layers.new(name='Metal2', dxfattribs={'linetype': 'CONTINUOUS', 'color': 3})
    doc.layers.new(name='EtchingPad', dxfattribs={'linetype': 'CONTINUOUS', 'color': 2})
    doc.layers.new(name='Etching', dxfattribs={'linetype': 'CONTINUOUS', 'color': 1})
    
    doc.layers.new(name=POLYIMIDE["layer"],
                   dxfattribs={'linetype': 'CONTINUOUS', 'color': POLYIMIDE["color"]})
    doc.layers.new(name=POLYIMIDE["neg_layer"],
                   dxfattribs={'linetype': 'CONTINUOUS', 'color': POLYIMIDE["neg_color"]})

    #Which columns drop their BOTTOM (row-0) pad. Design A DROPS cols 0..half_col and KEEPS
    #half_col+1..end, so the retained bottom row lives in the RIGHT (second) half. Design B is a
    #PLAIN 180-deg MIRROR of A: it uses the SAME drop pattern, so A and B have the SAME pad count.
    #When the two are put together the odd column count leaves a single empty site -- a HOLE in the
    #middle of the shared row (accepted; this is the mirror-symmetric design). The polyimide seam
    #below DERIVES its low/high halves + step column straight from this function, so it always tracks.
    half_col = CONFIG["n_cols"] // 2
    def row_start_A(col):
        return 1 if col <= half_col else 0

    #ROUTING (Metal1) + VIAS (Etching), TILED PER COLUMN. Every column is routed the SAME way and
    #stays inside its own pad-pitch band, so the pattern tiles to any number of columns with
    #no inter-column crossings. Per column each wire drops down its vertical lane then jogs 45 deg
    #DOWN-LEFT into a via that sits just to the LEFT of the lane. k=0 (TOP pad) has its via on the
    #column's LEFT edge, and lanes/vias STAIRCASE right for lower pads. Since neighbouring wires
    #occupy distinct lanes, nothing crosses within a column.
    wire_width = ROUTING["wire_width"]
    wire_gap = ROUTING["wire_gap"]
    wire_pitch = wire_width + wire_gap                     # 4.5 um lane pitch
    via_pitch = ROUTING["via_pitch"]
    jog0 = ROUTING["via_left_jog"]
    #Effective via half-width (the via's rightmost reach from its centre): the disc radius in "circle"
    #mode, else half the rectangle width. Single source of truth for every trace-vs-via / off-pad check.
    via_half_w = ROUTING["via_radius"] if ROUTING["via_shape"] == "circle" else ROUTING["via_w"] / 2.0
    #Minimum jog (lane centre - via centre) so the vertical trace stays min_wire_via_margin clear of
    #the via edge and only the diagonal enters it: half via + half wire + the wanted margin. Uses the
    #shape-aware via_half_w, so a circular via clears against its actual radius, not the rect width.
    min_jog = via_half_w + wire_width / 2.0 + ROUTING["min_wire_via_margin"]

    #FIT LIMIT: a column's (n_rows-1)*wire_pitch bundle must fit inside one pad pitch, leaving a
    #clean wire gap before the next column's leftmost lane. Warn (don't fail) if n_rows exceeds it.
    max_rows = int((pitch - wire_width - wire_gap) // wire_pitch) + 1
    if CONFIG["n_rows"] > max_rows:
        print(f"WARNING: n_rows={CONFIG['n_rows']} exceeds the per-column fit limit ({max_rows}); "
              f"columns' bundles will overlap. Reduce n_rows or shrink wire_width/wire_gap.")

    #The vias staircase right by via_pitch (its own knob). Warn if that staircase runs off the pad,
    #or if via_pitch > wire_pitch (then the per-row jog turns negative and lower vias fall to the
    #RIGHT of their trace instead of the left).
    #Rightmost via extent uses the effective half-width (via_half_w, shape-aware; defined above).
    via_span_right = ROUTING["via_offset_x"] + (CONFIG["n_rows"] - 1) * via_pitch + via_half_w
    if via_span_right > pad_side:
        print(f"WARNING: the via staircase reaches {via_span_right:.1f} um > pad_side ({pad_side}); "
              f"the lowest vias fall off the pad. Reduce via_pitch or n_rows.")
    if via_pitch > wire_pitch:
        print(f"WARNING: via_pitch ({via_pitch}) > wire_pitch ({wire_pitch}); lower vias will land "
              f"to the RIGHT of their trace. Keep via_pitch <= wire_pitch to keep the via left.")

    def build_shapes(row_start):
        """Generate one design instance's geometry as a list of (points, layer). row_start(col)
        gives the lowest row index that column keeps (1 = drop the bottom pad). Routing is derived
        per column from the actual pads, so any drop pattern routes correctly."""
        #Grid of square pads, filled column-by-column: n_rows stacked along +y, n_cols along +x.
        #Record each pad's centre, column index and left-x so the routing can drop a via + wire.
        shapes = []     # (points, layer) for every polyline in this instance
        pad_info = []   # (cx, cy, col, x0) per pad, in generation order (row 0 = bottom)
        for col in range(CONFIG["n_cols"]):
            for row in range(row_start(col), CONFIG["n_rows"]):
                x0 = CONFIG["origin_x"] + col * pitch
                y0 = CONFIG["origin_y"] + row * pitch
                cx = x0 + pad_side / 2.0; cy = y0 + pad_side / 2.0
                rect = create_rectangle(x0, y0, pad_side, pad_side)
                pad_pts = convert_rectangle_to_polyline(rect)
                shapes.append((pad_pts, CONFIG["layer"]))
                # Extra-layer (EtchingPad) pad: its OWN side length, centred on the same pad centre.
                extra_rect = create_rectangle(cx - pad_side_extra / 2.0, cy - pad_side_extra / 2.0,
                                              pad_side_extra, pad_side_extra)
                extra_pts = convert_rectangle_to_polyline(extra_rect)
                for extra in CONFIG.get("pad_layers_extra", []):
                    shapes.append((extra_pts, extra))
                pad_info.append((cx, cy, col, x0))
        n_pads = len(pad_info)

        #Per-pad via x and lane x, resolved per column. The trace LANES are the fixed part: they step
        #by wire_pitch and start via_left_jog right of the natural via staircase, so the lane bundle
        #packs tightly into the pad pitch and TILES cleanly to the next column. The VIAS then sit as
        #far LEFT as needed: at the natural staircase position (vx0 + k*via_pitch), but pulled further
        #LEFT to lane - min_jog on the upper rows so the vertical trace keeps its margin from the via
        #and only the 45-deg diagonal enters (moving the VIA left, never the lane right, so the bundle
        #width -- and the inter-column clearance -- is untouched). Vias are clamped to the pad's left
        #edge (vx0). The TOP pad (k=0) needs no jog: its via sits at the left edge and the wire runs
        #STRAIGHT DOWN into it (fully inside).
        via_x, lane_x = {}, {}
        clamped_cols = 0
        for col in range(CONFIG["n_cols"]):
            members = [i for i in range(n_pads) if pad_info[i][2] == col]
            vx0 = pad_info[members[0]][3] + ROUTING["via_offset_x"]   # this column's top-pad via x (k=0)
            prev_lane = None
            #k=0 -> top pad; iterate this column's pads by DESCENDING y.
            for k, i in enumerate(sorted(members, key=lambda j: pad_info[j][1], reverse=True)):
                nat_via = vx0 + k * via_pitch                # natural staircase position
                if k == 0:
                    lane_x[i] = nat_via                      # top pad: straight down over the via
                    via_x[i] = nat_via
                else:
                    lane_x[i] = max(nat_via + jog0, prev_lane + wire_pitch)
                    #Pull the via LEFT so the trace clears it by min_wire_via_margin; keep on the pad.
                    want = min(nat_via, lane_x[i] - min_jog)
                    via_x[i] = max(want, vx0)
                    if via_x[i] > lane_x[i] - min_jog + 1e-6:
                        clamped_cols += 1                    # couldn't reach the full margin on the pad
                prev_lane = lane_x[i]
        if clamped_cols:
            size_knob = "via_radius" if ROUTING["via_shape"] == "circle" else "via_w"
            print(f"WARNING: {clamped_cols} vias hit the pad's left edge before reaching the "
                  f"{ROUTING['min_wire_via_margin']} um trace-via margin; increase pad_side/via_left_jog "
                  f"or reduce {size_knob}/min_wire_via_margin for full clearance.")
        y_top = max(pad_info[i][1] for i in range(n_pads)) + ROUTING["top_margin"]

        for i in range(n_pads):
            cy = pad_info[i][1]
            vx = via_x[i]
            #Via centre y: pad centre shifted by via_offset_y (+ = UP). vcy sets BOTH the via geometry
            #and the wire endpoint so the trace always lands on the via for any offset (a circular via
            #with via_offset_y = pad_side/2 - via_offset_x sits in the pad's TOP-LEFT corner).
            vcy = cy + ROUTING["via_offset_y"]
            #Via (staircased, LEFT of the trace lane), on the Etching layer. Either a via_w x via_h
            #rectangle or a via_radius circle, both centred at (vx, vcy) so the wire jogs into the same
            #point; a circular via is emitted as a closed polyline so design B's 180-deg reflection and
            #the add_lwpolyline pipeline handle it identically to the rectangle.
            if ROUTING["via_shape"] == "circle":
                vpts = circle_polyline(vx, vcy, ROUTING["via_radius"], ROUTING["via_arc"])
            else:
                vrect = create_rectangle(vx - ROUTING["via_w"] / 2.0, vcy - ROUTING["via_h"] / 2.0,
                                         ROUTING["via_w"], ROUTING["via_h"])
                vpts = convert_rectangle_to_polyline(vrect)
            shapes.append((vpts, ROUTING["layer_via"]))

            #Metal1 wire: down the lane from the top, then a 45-deg jog DOWN-LEFT into the via centre
            #(vx, vcy). The jog length = lane_x - via_x, so the diagonal stays exactly 45 deg for any
            #via_pitch; the bend sits (lane_x - vx) above vcy.
            centerline = [(lane_x[i], y_top), (lane_x[i], vcy + (lane_x[i] - vx)), (vx, vcy)]
            ring = stroke_centerline_to_polygon(centerline, [wire_width] * 3)
            if ring is not None:
                shapes.append((np.asarray(ring, dtype=float), ROUTING["layer_wire"]))
        return shapes, pad_info

    #Build both instances: A and B use the SAME drop pattern, so B is a plain 180-deg mirror of A
    #with an identical pad count (a hole remains in the middle of the shared row).
    shapes_A, pad_info_A = build_shapes(row_start_A)
    shapes_B, pad_info_B = build_shapes(row_start_A)

    #Emit both. Design A is drawn as generated. Design B is a true 180-deg rotation (point reflection)
    #of A about the centre column x and the row-0 centre y; its routing fans DOWNWARD. Before the
    #+-d separation below, B's top pads would fill A's empty row-0 sites (under cols <= half_col);
    #A and B carry the SAME pad count, so a single empty site (hole) remains at the seam.
    px = CONFIG["origin_x"] + (CONFIG["n_cols"] - 1) / 2.0 * pitch + pad_side / 2.0
    py = CONFIG["origin_y"] + pad_side / 2.0

    #SEPARATE THE WHOLE DESIGNS so each polyimide outline has room for its outward band on the facing
    #(seam) edge. The bulb-only joint is itself IN-PLANE separable (no undercut), so the split is purely
    #for the bands: the pieces must part until the male bulb TIP (plus its band) clears the facing seam
    #band by center_gap. Each design shifts vertically by d (A up +d, B down -d); 2*d must therefore
    #reach knob_depth + 2*band_width + center_gap. center_gap is thus the MINIMUM band-to-band gap (at
    #the bulb tip); the straight seam ends up wider (= 2*d - 2*band_width). The row-0 pads therefore
    #no longer interlock (accepted). d shifts the outlines below by the same amount.
    knob_depth = POLYIMIDE["bulb_r"]                # seam -> male bulb tip (the semicircle protrudes bulb_r)
    d = (knob_depth + 2.0 * POLYIMIDE["band_width"] + POLYIMIDE["center_gap"]) / 2.0
    shift_A = np.array([0.0, d])
    shift_B = np.array([0.0, -d])
    for pts, layer in shapes_A:                                # design A (shifted UP by d)
        apts = np.asarray(pts, dtype=float) + shift_A
        msp.add_lwpolyline(apts, close=True, dxfattribs={'layer': layer})
    if CONFIG["emit_b"]:
        for pts, layer in shapes_B:                            # design B (180-deg flip of shapes_B, shifted DOWN by d)
            bpts = point_reflect(pts, px, py) + shift_B
            msp.add_lwpolyline(bpts, close=True, dxfattribs={'layer': layer})

    #POLYIMIDE OUTLINES (one per design) + TWO-CORNER JIGSAW JOIN. Each design gets its own closed
    #boundary. Each meets its pads along a STEPPED seam: A keeps its row-0 pads on the RIGHT half
    #(cols half_col+1..end) so its seam runs HIGH on the left and LOW on the right; B is a plain
    #180-deg mirror, so ITS low/high halves are the reflection of A's -- B's step sits one column to
    #the left (x_step_B). Every A pad ends up above its line, every B pad below its line, and the one
    #column between the two steps stays empty (the hole). A carries a MALE knob on its far-LEFT seam
    #corner and a FEMALE socket on its far-RIGHT; B, being the exact 180-deg reflection of A, is the
    #SAME part rotated (female-left + male-right), and the two mate on BOTH corners.
    def bbox(shape_list, xform=None):
        allpts = np.vstack([(xform(np.asarray(p)) if xform else np.asarray(p, dtype=float))
                            for p, _ in shape_list])
        return allpts[:, 0].min(), allpts[:, 0].max(), allpts[:, 1].min(), allpts[:, 1].max()

    A_xmin, A_xmax, A_ymin, A_ymax = bbox(shapes_A)
    B_xmin, B_xmax, B_ymin, B_ymax = bbox(shapes_B, lambda p: point_reflect(p, px, py))

    #Stepped seam. Each piece's seam is TWO horizontal lines (low + high) a `sm` clearance past that
    #piece's own seam-row pads, joined by a one-row (== pitch) step in a pad-free column gap. A grows
    #UP so its seam is its BOTTOM edge; B grows DOWN so its seam is its TOP edge. These are SEPARATE
    #lines: A's bottom sits sm BELOW A's lowest pads, B's top sits sm ABOVE B's highest pads. They
    #COINCIDE only at sm == gap/2 (the natural centred split of the one pad-free row between them) --
    #the fallback when seam_margin is None. A and B also step at DIFFERENT columns: since B is a
    #plain 180-deg mirror of A, its full/dropped boundary reflects across the pivot, landing ONE
    #column LEFT of A's. So A steps between col half_col/half_col+1, B between half_col-1/half_col;
    #the single column BETWEEN the two steps stays empty -- the HOLE in the middle of the shared row.
    g = CONFIG["gap"]
    oy = CONFIG["origin_y"]
    sm = POLYIMIDE["seam_margin"] if POLYIMIDE["seam_margin"] is not None else g / 2.0
    #Two ABSOLUTE seam heights, DERIVED from whether a column keeps or drops its row-0 pad (the
    #invariant everything hangs off): a column that KEEPS row-0 sits at the LOW seam (sm below its
    #row-0 pads); a column that DROPS row-0 sits at the HIGH seam (sm below its row-1 pads). These
    #formulas are orientation-independent -- only their ASSIGNMENT to the left/right corner flips
    #with row_start_A, so the seam auto-follows any drop pattern and can never desync from the pads.
    y_low = oy - sm                       # a column that KEEPS row-0 (row_start==0)
    y_high = oy + pad_side + g - sm       # a column that DROPS row-0 (row_start==1)
    #Corner seam heights: read the drop state of the leftmost/rightmost columns straight off
    #row_start_A. With the current pattern (drop cols 0..half_col, keep half_col+1..end) the LEFT
    #corner is HIGH and the RIGHT corner is LOW.
    y_left = y_low if row_start_A(0) == 0 else y_high
    y_right = y_low if row_start_A(CONFIG["n_cols"] - 1) == 0 else y_high
    #Vertical step-wall x, placed at the ONE column where row_start_A flips (the seam is a single
    #step -> a two-segment outline; warn if the pattern has more than one transition, which this
    #outline can't represent). step_margin (stm) is the HORIZONTAL clearance from the LOW-side (row-0-
    #keeping) corner pad to the wall: if the low run is on the LEFT the wall sits stm RIGHT of col t's
    #right edge; if the low run is on the RIGHT it sits stm LEFT of col t+1's left edge. stm==gap/2
    #centres the wall in the column gap; larger stm pushes it off its pad and narrows the middle hole.
    stm = POLYIMIDE["step_margin"] if POLYIMIDE["step_margin"] is not None else g / 2.0
    transitions = [c for c in range(CONFIG["n_cols"] - 1) if row_start_A(c) != row_start_A(c + 1)]
    if len(transitions) != 1:
        print(f"WARNING: row_start_A has {len(transitions)} seam transition(s) ({transitions}); the "
              f"stepped polyimide seam is a SINGLE step (two-segment outline) and can only represent "
              f"one. Use a single-transition drop pattern or generalize the outline builder.")
    t = transitions[0] if transitions else half_col          # last col of the left run
    if row_start_A(0) == 0:                                   # low run on the LEFT
        x_step = CONFIG["origin_x"] + t * pitch + pad_side + stm
    else:                                                     # low run on the RIGHT
        x_step = CONFIG["origin_x"] + (t + 1) * pitch - stm
    x_step_B = 2 * px - x_step                                # diagnostic mirror of A's step (B is 180-deg)
    if (x_step - px) * (x_step_B - px) >= 0:                  # A's step has reached/crossed the centre
        print(f"WARNING: step_margin ({stm}) is too large; A's step wall has reached the centre "
              f"(x_step={x_step:.0f}, centre px={px:.0f}) and the middle A<->B hole has closed. "
              f"Reduce step_margin.")

    M = POLYIMIDE["margin"]
    pg = POLYIMIDE["puzzle_gap"]
    #Knob centre sits in the LEFT margin, pad-free: the socket's right edge lands knob_gap left of
    #the leftmost pads of EITHER design, so neither piece's pads clash with the jigsaw.
    cx = min(A_xmin, B_xmin) - (POLYIMIDE["bulb_r"] + pg) - POLYIMIDE["knob_gap"]
    cx_right = 2 * px - cx                                     # right jigsaw centre = mirror of the left
    xL = min(A_xmin, B_xmin) - POLYIMIDE["left_margin"]       # far enough left to hold the socket
    xR = 2 * px - xL                                           # mirror the left extent so A and B are
                                                              # congruent and the right socket has room
    yTopA = A_ymax + M                                         # A grows up from the seam

    #A carries a MALE half-disc on its far-LEFT seam corner AND a FEMALE socket on its far-RIGHT corner,
    #so that B = 180-deg reflection of A is the SAME part rotated: B ends up with a female-left + male-
    #right, and the two mate on BOTH corners. Male/female are NOT tied to seam HEIGHT -- both are
    #downward-facing features on A's bottom edge (A material is always above its own seam), so keeping
    #male-left/female-right and just anchoring each to its corner's derived height (y_left/y_right) keeps
    #the mate valid for either orientation. Each bulb is a semicircle centred ON the seam it splices into
    #(no neck -> no undercut). The female socket is the male bulb grown OUTWARD by puzzle_gap (a uniform
    #loose fit) -- the exact complement of the mating bulb on the other piece.
    fem_r = POLYIMIDE["bulb_r"] + pg
    if POLYIMIDE["left_margin"] < 2 * fem_r + POLYIMIDE["knob_gap"]:
        print(f"WARNING: left_margin ({POLYIMIDE['left_margin']}) < 2*(bulb_r+puzzle_gap)+knob_gap "
              f"({2 * fem_r + POLYIMIDE['knob_gap']:.0f}); the bulb + socket won't fit the left margin. "
              f"Increase left_margin or reduce bulb_r/knob_gap.")
    #Male-left: a DOWN semicircle on A's LEFT-corner seam -> a half-disc protrusion (A material is above).
    male = bulb_profile(cx, y_left, POLYIMIDE["bulb_r"], POLYIMIDE["n_arc"])
    #Female-right: the SAME bulb (grown by puzzle_gap) but flipped in y about A's RIGHT-corner seam so it
    #notches UP into A's material -> a socket opening downward. Still traversed left->right in x.
    female_right = [(x, 2 * y_right - y) for (x, y) in bulb_profile(cx_right, y_right, fem_r, POLYIMIDE["n_arc"])]

    #A's stepped boundary, left->right: male dip at the far-left corner (y_left), the step to y_right,
    #female notch at the far-right corner (y_right), then close UP and over the top. The step segment
    #(x_step,y_left)->(x_step,y_right) rises or falls automatically with the orientation. A_fill is the
    #SAME outline with the female notch replaced by the straight y_right edge (male kept) -- used to fill
    #the socket solid in the band.
    A_outline = ([(xL, y_left)] + male + [(x_step, y_left), (x_step, y_right)]
                 + female_right + [(xR, y_right), (xR, yTopA), (xL, yTopA)])
    A_fill_pts = ([(xL, y_left)] + male + [(x_step, y_left), (x_step, y_right), (xR, y_right),
                                          (xR, yTopA), (xL, yTopA)])
    #Shift A up by d (as its design), then derive B as the EXACT 180-deg reflection of A -- one part,
    #rotated. Because A is already +d, point_reflect lands B at its correct -d position (no extra shift).
    A_outline = [(x, y + d) for (x, y) in A_outline]
    A_fill_pts = [(x, y + d) for (x, y) in A_fill_pts]
    B_outline = [(float(x), float(y)) for x, y in point_reflect(A_outline, px, py)]
    for outline in ((A_outline, B_outline) if CONFIG["emit_b"] else (A_outline,)):
        msp.add_lwpolyline(outline, close=True, dxfattribs={'layer': POLYIMIDE["layer"]})

    #NEGATIVE band (hollow frame, NOT a filled slab): a uniform-width RING around each polyimide outline.
    #Inner edge == the existing polyimide outline EXACTLY; outer edge == that outline offset OUTWARD by
    #band_width (buffer join_style=2 keeps corners sharp, the jigsaw arcs stay smooth). The centre (under
    #the polyimide) stays EMPTY -- it is only a band. Each piece now has BOTH a convex MALE knob (to be
    #WRAPPED by the band) and a concave FEMALE socket (to be FILLED solid). A's band is built from A_fill
    #-- A's outline with the female socket replaced by its STRAIGHT seam but the male knob KEPT -- offset
    #out, minus A's REAL polyimide. Over the socket A_fill is solid while A_poly recedes, so the cavity
    #fills solid and merges with the band (no sliver at the mouth); over the male knob A_fill keeps the
    #protrusion, so buffer - A_poly is a clean annulus wrapping it. B's band is the EXACT 180-deg
    #reflection of A's (buffer/difference commute with a rigid reflection), landing at B's -d position
    #and filling B's female-left / wrapping B's male-right. Each design is emitted as EXACTLY ONE hatch.
    bw = POLYIMIDE["band_width"]
    A_poly = Polygon(A_outline)
    B_poly = Polygon(B_outline)
    A_fill = Polygon(A_fill_pts)                              # straight right seam (socket solid), male kept
    A_neg = A_fill.buffer(bw, join_style=2, mitre_limit=5).difference(A_poly)
    B_neg = affine_transform(A_neg, [-1, 0, 0, -1, 2 * px, 2 * py])   # B band = 180-deg reflection of A's
    #Growing seam_margin past gap/2 pulls A's bottom band and B's top band toward each other along the
    #straight seam (each extra um of seam_margin closes the gap by 2 um). Warn if that straight-seam
    #band gap drops below center_gap (then the seam -- not the knob tip -- becomes the tightest point).
    straight_band_gap = 2 * d - 2 * (sm - g / 2.0) - 2 * bw
    if straight_band_gap < POLYIMIDE["center_gap"]:
        print(f"WARNING: seam_margin ({sm}) narrows the straight-seam band gap to "
              f"{straight_band_gap:.0f} um (< center_gap {POLYIMIDE['center_gap']:.0f}); the seam, not "
              f"the knob tip, is now the tightest A<->B point. Reduce seam_margin or raise center_gap.")

    def emit_band(geom, layer, color):
        """Emit a shapely (Multi)Polygon band as EXACTLY ONE solid HATCH: its exterior(s) + hole(s) as
        the hatch boundary paths, nothing else (no separate outline polylines). One entity per design."""
        geoms = geom.geoms if isinstance(geom, MultiPolygon) else [geom]
        hatch = msp.add_hatch(color=color, dxfattribs={'layer': layer})
        for g in geoms:
            if g.is_empty:
                continue
            hatch.paths.add_polyline_path([(float(x), float(y)) for x, y in g.exterior.coords],
                                          is_closed=True, flags=ezdxf.const.BOUNDARY_PATH_EXTERNAL)
            for ring in g.interiors:                          # the hole == the polyimide outline (empty)
                hatch.paths.add_polyline_path([(float(x), float(y)) for x, y in ring.coords],
                                              is_closed=True, flags=ezdxf.const.BOUNDARY_PATH_DEFAULT)

    emit_band(A_neg, POLYIMIDE["neg_layer"], POLYIMIDE["neg_color"])
    if CONFIG["emit_b"]:
        emit_band(B_neg, POLYIMIDE["neg_layer"], POLYIMIDE["neg_color"])

    doc.saveas(CONFIG["out_path"])

    n_pads_A, n_pads_B = len(pad_info_A), len(pad_info_B)
    if CONFIG["emit_b"]:
        print(f"2 designs (A + 180-deg flipped B; flip pivot x={px:.2f} y={py:.2f}) pushed "
              f"{2 * d:.0f} um APART (A +{d:.0f}, B -{d:.0f}; row-0 pad interlock now open by design)")
    else:
        print(f"1 design (A ONLY; emit_b=False -- B is the same part rotated 180 deg about "
              f"x={px:.2f} y={py:.2f}, so one copy suffices)")
    n_designs = 2 if CONFIG["emit_b"] else 1
    n_pads_tot = n_pads_A + (n_pads_B if CONFIG["emit_b"] else 0)
    if CONFIG["emit_b"]:
        print(f"A: {n_pads_A} pads (drop bottom on cols 0..{half_col}); "
              f"B: {n_pads_B} pads (plain mirror, same drop pattern) -> {n_pads_tot} pads total "
              f"(hole left in the middle of the shared row)")
    else:
        print(f"A: {n_pads_A} pads (drop bottom on cols 0..{half_col}); B not emitted")
    via_desc = (f"circle r{ROUTING['via_radius']} um" if ROUTING["via_shape"] == "circle"
                else f"{ROUTING['via_w']}x{ROUTING['via_h']} um")
    print(f"{CONFIG['n_rows']} rows x {CONFIG['n_cols']} col, {pad_side} um side, {CONFIG['gap']} um "
          f"gap (pitch {pitch} um); {n_pads_tot} Metal1 wires + {n_pads_tot} vias "
          f"(wire width {wire_width} um, lane pitch {wire_pitch} um, "
          f"via {via_desc} on {ROUTING['layer_via']})")
    print(f"{n_designs} {POLYIMIDE['layer']} outline(s), each with a stepped seam DERIVED from row_start_A "
          f"(seam_margin {sm:.0f} um, step_margin {stm:.0f} um; low seam y={y_low:.0f}, high seam y={y_high:.0f}; "
          f"A left corner y={y_left:.0f}, right corner y={y_right:.0f}; A steps at x={x_step:.0f}, "
          f"B mirror at x={x_step_B:.0f} -> {abs(x_step_B - x_step):.0f} um hole between them); "
          f"A y[{min(y_left, y_right):.0f},{yTopA:.0f}], x[{xL:.0f},{xR:.0f}]; B = exact 180-deg reflection of A; "
          f"bulb-only semicircles on BOTH corners (A male-left x={cx:.0f} + female-right x={cx_right:.0f}, "
          f"B mirror; r{POLYIMIDE['bulb_r']:.0f} um, {pg:.0f} um puzzle_gap, no neck -> in-plane insertable)")
    print(f"{n_designs} {POLYIMIDE['neg_layer']} BAND(s) ({POLYIMIDE['band_width']:.0f} um-wide hollow rings hugging "
          f"each polyimide outline, empty centre; both female jigsaw sockets filled solid, both male "
          f"knobs wrapped; B band = 180-deg reflection of A's); min band gap "
          f"{POLYIMIDE['center_gap']:.0f} um at the knob tip (knob depth {knob_depth:.0f} um), "
          f"straight-seam gap {straight_band_gap:.0f} um")
    print(f"wrote {CONFIG['out_path']}")
