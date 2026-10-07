import numpy as np
import ezdxf
from shapely.geometry import Polygon, MultiPolygon
from shapely.affinity import affine_transform

from lib.generate_helpers import (create_rectangle, convert_rectangle_to_polyline, point_reflect,
                                  bulb_profile, circle_polyline, stroke_centerline_to_polygon,
                                  perimeter_via_centres, vias_per_side_for)
from lib.active import in_designs as _in_designs      # bare filename -> designs/

# Every knob for this design lives in config_8block.py (section 1) -- edit it there.
import config_8block as _cfg
CONFIG = _cfg.GEN_CONFIG          # the pad grid
ROUTING = _cfg.GEN_ROUTING        # per-column Metal1 routing + vias
POLYIMIDE = _cfg.GEN_POLYIMIDE    # substrate outline + negative band

DEFAULT_OUT = _cfg.DEFAULT_IN     # what stage 02 reads by default

# ======================================================================================
# Command line: where to write
# ======================================================================================
# Pad size, gap and the via position depend on each other (via_offset_y = pad_side/2 -
# via_offset_x keeps the via in the pad's top-left corner), so edit them together in
# config_8block.py -- see HANDOVER §4.4 for the 71_15 variant.
#
# NOTE the wire-count invariant: n_rows*n_cols - n_shorts_left - n_shorts_right
# must equal the router's TOTAL_WIRES (512). Run 00_preflight.py before stage 02.

def _apply_cli(config, argv=None):
    """Parse --out and set the output path in `config` (mutates in place)."""
    import argparse
    p = argparse.ArgumentParser(
        description="Generate the 8-block interconnect DXF (pad grid + per-column routing). "
                    "Pad sizes are set in config_8block.py.")
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
    if CONFIG["metal3"]:
        doc.layers.new(name=CONFIG["layer_top"], dxfattribs={'linetype': 'CONTINUOUS', 'color': 6})
    
    doc.layers.new(name=POLYIMIDE["layer"],
                   dxfattribs={'linetype': 'CONTINUOUS', 'color': POLYIMIDE["color"]})
    doc.layers.new(name=POLYIMIDE["neg_layer"],
                   dxfattribs={'linetype': 'CONTINUOUS', 'color': POLYIMIDE["neg_color"]})

    #Lowest row each column keeps (1 = drop the bottom pad). The 8-block drops nothing: every column
    #keeps row 0, so the full 12 x 44 grid is drawn and the polyimide seam below comes out flat. The
    #seam code derives its heights and step position from this function, so it follows any pattern.
    half_col = CONFIG["n_cols"] // 2
    def row_start_A(col):
        return 0

    #PAIR-SHORTS at the two edges, applied to CONTINUOUS (consecutive) column boundaries: each short
    #folds one column's BOTTOM pad (row 0 -> rightmost lane) straight into the NEXT column's TOP-pad
    #wire (top row -> leftmost lane, which keeps its own exit at y_top). The two lanes sit next to each
    #other across the empty inter-column gap, so the fold is a short jog with no crossings. Because the
    #boundaries are consecutive -- (0,1),(1,2),(2,3),... -- every column's bottom pad feeds the next
    #(no column skipped). n_shorts_left folds march in from col 0 (cols 0..nl); n_shorts_right from the
    #last col (cols nc-1-nr..nc-1). Lower column index first so bottom-of-left -> top-of-right holds on
    #both sides. Emitted Metal1 EXITS = n_rows*n_cols - (n_shorts_left + n_shorts_right); the folded-in
    #bottom pads become ties (extra Metal1 polylines that do NOT reach y_top, so they are not exits).
    nl, nr, nc = CONFIG["n_shorts_left"], CONFIG["n_shorts_right"], CONFIG["n_cols"]
    short_pairs = ([(i, i + 1) for i in range(nl)] +                    # continuous from the left
                   [(nc - 2 - i, nc - 1 - i) for i in range(nr)])       # continuous from the right
    if nl + nr >= nc - 1:                                               # left cols 0..nl meet right cols nc-1-nr..
        print(f"WARNING: n_shorts_left ({nl}) + n_shorts_right ({nr}) chains overlap in a {nc}-col grid "
              f"(need nl+nr < nc-1). Reduce n_shorts_left/right.")

    #ROUTING (Metal1) + VIAS (Etching), TILED PER COLUMN. Every column is routed the SAME way and
    #stays inside its own pad-pitch band, so the pattern tiles to any number of columns with
    #no inter-column crossings. Per column each wire drops down its vertical lane then jogs 45 deg
    #DOWN-LEFT into a via that sits just to the LEFT of the lane. k=0 (TOP pad) has its via on the
    #column's LEFT edge, and lanes/vias STAIRCASE right for lower pads. Since neighbouring wires
    #occupy distinct lanes, nothing crosses within a column.
    wire_width = ROUTING["wire_width"]
    wire_gap = ROUTING["wire_gap"]
    wire_pitch = wire_width + wire_gap                     # lane pitch (7 um here)
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

    #FINAL PI ETCH. "square": one pad_side_extra square per pad on EtchingPad (Rev2). "vias": via-sized
    #circles around the pad perimeter on Polyimide_Negative (Rev3) -- the same count on all four sides,
    #corners included, chosen so the spacing falls in etch_via_spacing (shared by every design, so all
    #pads get similar spacing); at the routing via's diameter and at its inset from the pad edge, so the
    #corner circles line up with the routing via. The perimeter position on the routing via (the
    #top-left corner) is left out, so that via is not etched through twice.
    if CONFIG["pad_etch"] not in ("square", "vias"):
        raise SystemExit(f"pad_etch must be 'square' or 'vias', got {CONFIG['pad_etch']!r}")
    if CONFIG["pad_etch"] == "vias":
        if ROUTING["via_shape"] != "circle":
            raise SystemExit("pad_etch='vias' copies the routing via's circle; set via_shape='circle'")
        etch_r = ROUTING["via_radius"]
        etch_inset = ROUTING["via_offset_x"]
        etch_layer = POLYIMIDE["neg_layer"]
        n_side, etch_step = vias_per_side_for(pad_side, etch_inset, CONFIG["etch_via_spacing"])
        if etch_step - 2 * etch_r <= 0:
            raise SystemExit(f"etch vias {etch_step:.2f} um apart overlap at {2 * etch_r} um diameter; "
                             f"raise etch_via_spacing")

    def build_shapes(row_start, short_pairs=()):
        """Generate one design instance's geometry as a list of (points, layer). row_start(col)
        gives the lowest row index that column keeps (1 = drop the bottom pad). Routing is derived
        per column from the actual pads, so any drop pattern routes correctly. short_pairs is a list
        of (left_col, right_col): for each, the left col's BOTTOM pad loses its own wire and is joined
        by a separate Metal1 tie into the right col's TOP-pad wire, which stays the single exit."""
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
                if CONFIG["metal3"]:
                    shapes.append((pad_pts, CONFIG["layer_top"]))     # same square, no wires
                if CONFIG["pad_etch"] == "square":
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
        n_etch_skipped = [0]

        #Resolve each pair (left_col, right_col) to pad indices: L = left col's BOTTOM pad (min y),
        #R = right col's TOP pad (max y). Only L's OWN wire is skipped below (its bottom pad is folded
        #into R's lane by a tie emitted after the loop); R keeps its normal wire, which is the single
        #exit for the merged net. merged holds the folded-in (skipped) pads.
        def _col_pad(col, want_top):
            members = [i for i in range(n_pads) if pad_info[i][2] == col]
            if not members:
                return None
            return max(members, key=lambda j: pad_info[j][1]) if want_top \
                else min(members, key=lambda j: pad_info[j][1])
        merge_LR = []
        merged = set()
        for cL, cR in short_pairs:
            L = _col_pad(cL, want_top=False)   # left col: bottom pad (folded away)
            R = _col_pad(cR, want_top=True)    # right col: top pad (keeps its exit)
            if L is None or R is None:
                continue
            merge_LR.append((L, R)); merged.add(L)

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

            #Rev3 etch vias around this pad's perimeter, minus any that would touch the routing via.
            if CONFIG["pad_etch"] == "vias":
                x0 = pad_info[i][3]; y0 = cy - pad_side / 2.0
                for ex, ey in perimeter_via_centres(x0, y0, pad_side, n_side, etch_inset):
                    if np.hypot(ex - vx, ey - vcy) < 2 * etch_r + 1e-6:
                        n_etch_skipped[0] += 1
                        continue
                    shapes.append((circle_polyline(ex, ey, etch_r, ROUTING["via_arc"]), etch_layer))

            #Metal1 wire: down the lane from the top, then a 45-deg jog DOWN-LEFT into the via centre
            #(vx, vcy). The jog length = lane_x - via_x, so the diagonal stays exactly 45 deg for any
            #via_pitch; the bend sits (lane_x - vx) above vcy.
            if i in merged:
                continue                                 # folded-in bottom pad: emitted as a tie below
            centerline = [(lane_x[i], y_top), (lane_x[i], vcy + (lane_x[i] - vx)), (vx, vcy)]
            ring = stroke_centerline_to_polygon(centerline, [wire_width] * 3)
            if ring is not None:
                shapes.append((np.asarray(ring, dtype=float), ROUTING["layer_wire"]))

        #Folded-in ties: one Metal1 polyline per short. Up the LEFT col's bottom-pad lane, then a
        #45-deg diagonal into the RIGHT col's top-pad lane, reaching it at join_y -- i.e. the left wire
        #runs into the right wire, which carries the single exit up to y_top. join_y sits above all
        #pad squares but below y_top, so the tie never reaches the band (it is NOT an exit) and crosses
        #nothing (the two lanes straddle the empty inter-column gap). The exits all end at y_top -> the
        #band stays perfectly coplanar.
        pad_top_max = max(pad_info[i][1] for i in range(n_pads)) + pad_side / 2.0
        join_y = 0.5 * (pad_top_max + y_top)
        for L, R in merge_LR:
            vcyL = pad_info[L][1] + ROUTING["via_offset_y"]
            x_end = lane_x[R] + wire_width / 7.0                      # end just past the right lane centerline (a bit longer, still ~inside the trace edge)
            dx = x_end - lane_x[L]                                    # horizontal reach -> 45-deg ramp rises the same
            centerline = [
                (via_x[L], vcyL),                                     # into left pad's via
                (lane_x[L], vcyL + (lane_x[L] - via_x[L])),          # left 45-deg via bend
                (lane_x[L], join_y - dx),                            # up the left lane (stop dx short)
                (x_end, join_y),                                      # 45-deg diagonal into the right wire
            ]
            ring = stroke_centerline_to_polygon(centerline, [wire_width] * len(centerline))
            if ring is not None:
                shapes.append((np.asarray(ring, dtype=float), ROUTING["layer_wire"]))
        if CONFIG["pad_etch"] == "vias" and n_etch_skipped[0] != n_pads:
            raise SystemExit(f"expected exactly one etch via per pad to land on the routing via, "
                             f"skipped {n_etch_skipped[0]} for {n_pads} pads -- check via_offset_x/y")
        return shapes, pad_info

    #Build both instances: A and B use the same pattern (no drops), so B is a plain 180-deg mirror
    #of A with an identical pad count.
    shapes_A, pad_info_A = build_shapes(row_start_A, short_pairs)
    shapes_B, pad_info_B = build_shapes(row_start_A, short_pairs)

    #Emit both. Design A is drawn as generated. Design B is a true 180-deg rotation (point reflection)
    #of A about the centre column x and the row-0 centre y; its routing fans DOWNWARD.
    px = CONFIG["origin_x"] + (CONFIG["n_cols"] - 1) / 2.0 * pitch + pad_side / 2.0
    py = CONFIG["origin_y"] + pad_side / 2.0

    #SEPARATE THE WHOLE DESIGNS so each polyimide outline has room for its outward band on the facing
    #(seam) edge. The bulb-only joint is itself IN-PLANE separable (no undercut), so the split is purely
    #for the bands: the pieces must part until the male bulb TIP (plus its band) clears the facing seam
    #band by center_gap. Each design shifts vertically by d (A up +d, B down -d); 2*d must therefore
    #reach knob_depth + 2*band_width + center_gap. center_gap is thus the MINIMUM band-to-band gap (at
    #the bulb tip); the straight seam ends up wider (= 2*d - 2*band_width). d shifts the outlines below by the same amount.
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
    #boundary. Each meets its pads along a FLAT seam (no column drops its row-0 pad); every A pad
    #ends up above its line, every B pad below its line. A carries a MALE knob on its far-LEFT seam
    #corner and a FEMALE socket on its far-RIGHT; B, being the exact 180-deg reflection of A, is the
    #SAME part rotated (female-left + male-right), and the two mate on BOTH corners.
    def bbox(shape_list, xform=None):
        allpts = np.vstack([(xform(np.asarray(p)) if xform else np.asarray(p, dtype=float))
                            for p, _ in shape_list])
        return allpts[:, 0].min(), allpts[:, 0].max(), allpts[:, 1].min(), allpts[:, 1].max()

    A_xmin, A_xmax, A_ymin, A_ymax = bbox(shapes_A)
    B_xmin, B_xmax, B_ymin, B_ymax = bbox(shapes_B, lambda p: point_reflect(p, px, py))

    #Seam. The code below supports a stepped seam (low + high line joined by a one-row step, for
    #designs that drop bottom pads); with no dropped pads both corners sit at the LOW line and the
    #seam is flat. A grows UP so its seam is its BOTTOM edge, sm below its row-0 pads; B grows DOWN
    #so its seam is its TOP edge, sm above its row-0 pads. The two lines coincide only at
    #sm == gap/2 (the fallback when seam_margin is None).
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
    #row_start_A. Here no column drops row 0, so both corners sit at the LOW seam.
    y_left = y_low if row_start_A(0) == 0 else y_high
    y_right = y_low if row_start_A(CONFIG["n_cols"] - 1) == 0 else y_high
    #Vertical step-wall x, placed at the ONE column where row_start_A flips (the seam is a single
    #step -> a two-segment outline; warn if the pattern has more than one transition, which this
    #outline can't represent). step_margin (stm) is the HORIZONTAL clearance from the LOW-side (row-0-
    #keeping) corner pad to the wall: if the low run is on the LEFT the wall sits stm RIGHT of col t's
    #right edge; if the low run is on the RIGHT it sits stm LEFT of col t+1's left edge. In the
    #8-block there is no transition: t falls back to half_col and the step segment has zero length.
    stm = POLYIMIDE["step_margin"] if POLYIMIDE["step_margin"] is not None else g / 2.0
    transitions = [c for c in range(CONFIG["n_cols"] - 1) if row_start_A(c) != row_start_A(c + 1)]
    if len(transitions) > 1:                                  # 0 = flat seam (no dropped pads): fine
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

    #A's boundary, left->right: male dip at the far-left corner (y_left), the step to y_right (zero
    #length here, y_left == y_right), female notch at the far-right corner, then close UP and over the
    #top. A_fill is the
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
    dropped = [c for c in range(CONFIG["n_cols"]) if row_start_A(c)]
    drop_desc = (f"drop bottom on cols {dropped[0]}..{dropped[-1]}" if dropped
                 else "no bottom pads dropped")
    if CONFIG["emit_b"]:
        print(f"A: {n_pads_A} pads ({drop_desc}); "
              f"B: {n_pads_B} pads (plain mirror, same drop pattern) -> {n_pads_tot} pads total")
    else:
        print(f"A: {n_pads_A} pads ({drop_desc}); B not emitted")
    via_desc = (f"circle r{ROUTING['via_radius']} um" if ROUTING["via_shape"] == "circle"
                else f"{ROUTING['via_w']}x{ROUTING['via_h']} um")
    n_merges = len(short_pairs)                               # folds PER design (each removes 1 exit)
    n_exits_tot = n_pads_tot - n_merges * n_designs           # folded bottom pads no longer exit
    print(f"pair-shorts (CONTINUOUS): n_shorts_left={nl}, n_shorts_right={nr} -> {n_merges} folds/design "
          f"({n_merges * n_designs} total); each folds a col's bottom pad straight into the NEXT col's "
          f"top-pad wire (one exit). left pairs={[(i,i+1) for i in range(nl)]}, "
          f"right pairs={[(nc-2-i,nc-1-i) for i in range(nr)]}")
    print(f"{CONFIG['n_rows']} rows x {CONFIG['n_cols']} col, {pad_side} um side, {CONFIG['gap']} um "
          f"gap (pitch {pitch} um); {n_pads_tot} pads -> {n_exits_tot} Metal1 EXITS "
          f"({n_pads_tot - n_exits_tot} folded in as ties, not exits) + {n_pads_tot} vias "
          f"(wire width {wire_width} um, lane pitch {wire_pitch} um, "
          f"via {via_desc} on {ROUTING['layer_via']})")
    if transitions:
        seam_desc = (f"a stepped seam DERIVED from row_start_A (seam_margin {sm:.0f} um, step_margin "
                     f"{stm:.0f} um; low seam y={y_low:.0f}, high seam y={y_high:.0f}; A left corner "
                     f"y={y_left:.0f}, right corner y={y_right:.0f}; A steps at x={x_step:.0f}, B mirror "
                     f"at x={x_step_B:.0f} -> {abs(x_step_B - x_step):.0f} um hole between them)")
    else:
        seam_desc = f"a flat seam at y={y_left:.0f} (no dropped pads; seam_margin {sm:.0f} um)"
    print(f"{n_designs} {POLYIMIDE['layer']} outline(s), each with {seam_desc}; "
          f"A y[{min(y_left, y_right):.0f},{yTopA:.0f}], x[{xL:.0f},{xR:.0f}]; B = exact 180-deg reflection of A; "
          f"bulb-only semicircles on BOTH corners (A male-left x={cx:.0f} + female-right x={cx_right:.0f}, "
          f"B mirror; r{POLYIMIDE['bulb_r']:.0f} um, {pg:.0f} um puzzle_gap, no neck -> in-plane insertable)")
    print(f"{n_designs} {POLYIMIDE['neg_layer']} BAND(s) ({POLYIMIDE['band_width']:.0f} um-wide hollow rings hugging "
          f"each polyimide outline, empty centre; both female jigsaw sockets filled solid, both male "
          f"knobs wrapped; B band = 180-deg reflection of A's); min band gap "
          f"{POLYIMIDE['center_gap']:.0f} um at the knob tip (knob depth {knob_depth:.0f} um), "
          f"straight-seam gap {straight_band_gap:.0f} um")
    if CONFIG["pad_etch"] == "vias":
        print(f"PI etch: {4 * (n_side - 1) - 1} circles per pad on {etch_layer} (r{etch_r} um, "
              f"{n_side} per side incl. corners, {etch_step:.3f} um apart -- range "
              f"{CONFIG['etch_via_spacing']} um; centres {etch_inset} um in from the pad edge; the "
              f"corner over the routing via left out)")
    else:
        print(f"PI etch: one {pad_side_extra} um square per pad on {CONFIG['pad_layers_extra']}")
    if CONFIG["metal3"]:
        print(f"{CONFIG['layer_top']}: the {pad_side} um pad squares (Metal2's pads, no wires)")
    print(f"wrote {CONFIG['out_path']}")
