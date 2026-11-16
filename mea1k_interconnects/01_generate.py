import numpy as np
import ezdxf
from shapely.geometry import Polygon, MultiPolygon
from shapely.affinity import affine_transform

from lib.generate_helpers import (capsule_polyline, create_rectangle, convert_rectangle_to_polyline, point_reflect,
                                  bulb_profile, circle_polyline, stroke_centerline_to_polygon,
                                  etch_via_layout, wire_with_end_pad)
from lib.active import in_designs as _in_designs      # bare filename -> <out root>/adhoc/

# Stage 01 for EVERY interconnect design: the pad grid + per-column Metal1 routing + vias + polyimide.
# Every knob lives in config_<name>.py (section 1) -- edit it there. Three base configs use this
# generator: 12block (the connector design), dummy (the dummy device's pad grid, a 12-block grid) and
# 8block. What differs between them in HOW the grid is built is STRATEGIES below (this file merges
# the former per-design generators; each switch selects that generator's original code).
import argparse as _argparse, importlib as _importlib
CONFIGS = ("12block", "dummy", "8block")
_pre = _argparse.ArgumentParser(add_help=False)
_pre.add_argument("--config", choices=CONFIGS, default="12block")
CONFIG_NAME = _pre.parse_known_args()[0].config
_cfg = _importlib.import_module("config_" + CONFIG_NAME)

# drop_bottom_left  12-block grid: columns 0..n_cols//2 drop their bottom (row-0) pad (stepped seam,
#                   one-site hole between A and its mirror B); False: every column keeps row 0
# pair_shorts       8-block: fold n_shorts_left / n_shorts_right bottom pads into the next column's
#                   top-pad wire (config n_shorts_left / n_shorts_right)
# k0_via            12-block lane geometry: per-pad via radius and the Rev4 top-via knobs (via_k0_radius,
#                   via_k0_dx, via_rest_dx); False: every via is via_radius, in the plain staircase
STRATEGIES = {
    "12block": dict(drop_bottom_left=True, pair_shorts=False, k0_via=True),
    "dummy":   dict(drop_bottom_left=True, pair_shorts=False, k0_via=True),
    "8block":  dict(drop_bottom_left=False, pair_shorts=True, k0_via=False),
}
S = STRATEGIES[CONFIG_NAME]
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
# NOTE the wire-count invariant: sum over columns of (n_rows - row_start(col)), minus the pair-shorts,
# must equal the router's TOTAL_WIRES (12-block 768, 8-block 512). Run 00_preflight.py before stage 02.

def _apply_cli(config, argv=None):
    """Parse --config / --out and set the output path in `config` (mutates in place)."""
    import argparse
    p = argparse.ArgumentParser(
        description="Generate an interconnect DXF -- 12-block, dummy or 8-block (pad grid + per-column routing). "
                    "Pad sizes are set in config_<name>.py.")
    p.add_argument("--config", choices=CONFIGS, default="12block",
                   help="which config_<name>.py to build (default: %(default)s)")
    p.add_argument("--out", default=DEFAULT_OUT,
                   help="output DXF; bare name -> <out root>/adhoc/ (default: %(default)s)")
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
    if CONFIG["pad_etch"] == "vias" and CONFIG["etch_via_layer"]:
        doc.layers.new(name=CONFIG["etch_via_layer"], dxfattribs={'linetype': 'CONTINUOUS', 'color': 1})

    #Which columns drop their BOTTOM (row-0) pad.
    #drop_bottom_left (12-block, dummy): Design A DROPS cols 0..half_col and KEEPS half_col+1..end, so
    #the retained bottom row lives in the RIGHT (second) half. Design B is a PLAIN 180-deg MIRROR of A:
    #it uses the SAME drop pattern, so A and B have the SAME pad count. When the two are put together
    #the odd column count leaves a single empty site -- a HOLE in the middle of the shared row
    #(accepted; this is the mirror-symmetric design).
    #Otherwise (8-block) nothing is dropped: every column keeps row 0, so the full grid is drawn and
    #the polyimide seam below comes out flat.
    #The seam DERIVES its low/high halves + step column straight from this function, so it always tracks.
    half_col = CONFIG["n_cols"] // 2
    def row_start_A(col):
        if S["drop_bottom_left"]:
            return 1 if col <= half_col else 0
        return 0

    #pair_shorts (8-block): PAIR-SHORTS at the two edges, applied to CONTINUOUS (consecutive) column
    #boundaries: each short folds one column's BOTTOM pad (row 0 -> rightmost lane) straight into the
    #NEXT column's TOP-pad wire (top row -> leftmost lane, which keeps its own exit at y_top). The two
    #lanes sit next to each other across the empty inter-column gap, so the fold is a short jog with no
    #crossings. Because the boundaries are consecutive -- (0,1),(1,2),(2,3),... -- every column's bottom
    #pad feeds the next (no column skipped). n_shorts_left folds march in from col 0 (cols 0..nl);
    #n_shorts_right from the last col (cols nc-1-nr..nc-1). Lower column index first so bottom-of-left
    #-> top-of-right holds on both sides. Emitted Metal1 EXITS = n_rows*n_cols - (n_shorts_left +
    #n_shorts_right); the folded-in bottom pads become ties (extra Metal1 polylines that do NOT reach
    #y_top, so they are not exits). Without pair_shorts there are none.
    short_pairs = ()
    if S["pair_shorts"]:
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
    wire_pitch = wire_width + wire_gap                     # lane pitch (12-block 4.5 um, 8-block 7 um)
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

    #m1_margin (wafer6): ONE rule for every routing via of a 12-block-style grid -- its Metal1 landing pad
    #sits m1_margin from the Metal2 pad's left and top edges (so the via edge sits m1_margin + m1_pad_enc
    #from them). It replaces three Rev4 numbers that each set one side of that distance: via_offset_y (from
    #via_edge_gap), via_rest_dx (lower vias) and via_k0_dx (small top via). The full top via gets the same
    #margin; via_k0_where="needed" then shifts that column's lanes right just enough to pass it.
    m1m = ROUTING.get("m1_margin")
    if m1m is not None:
        if not (S["k0_via"] and ROUTING["m1_pad"]) or ROUTING["via_pitch"]:
            raise SystemExit("m1_margin needs the 12-block lane geometry (k0_via), m1_pad and via_pitch = 0")
        _enc = ROUTING["m1_pad_enc"]
        _R_f = ROUTING["via_radius"] + _enc
        _r_s = ROUTING["via_k0_radius"] if ROUTING["via_k0_radius"] is not None else ROUTING["via_radius"]
        ROUTING["via_offset_y"] = pad_side / 2.0 - (m1m + _R_f)
        ROUTING["via_rest_dx"] = m1m + _R_f - ROUTING["via_offset_x"]
        ROUTING["via_k0_dx"] = m1m + _r_s + _enc - ROUTING["via_offset_x"]
        _h = ROUTING.get("via_k0_h")
        if _h and abs((_h / 2.0 + _enc) - _R_f) > 1e-9:
            print(f"NOTE: the small top via's pad is {_h + 2 * _enc:g} um tall, the full one {2 * _R_f:g} um: "
                  f"their top margins differ")
        print(f"m1_margin {m1m:g} um: Metal1 pad edge {m1m:g} um, via edge {m1m + _enc:g} um from the Metal2 "
              f"pad's left/top edge -> via_offset_y {ROUTING['via_offset_y']:g}, lower vias dx "
              f"{ROUTING['via_rest_dx']:+g}, small top via dx {ROUTING['via_k0_dx']:+g}")
    elif ROUTING.get("via_k0_where", "all") == "needed":
        raise SystemExit("via_k0_where='needed' needs m1_margin (the margin the full top via must keep)")

    #FINAL PI ETCH. "square": one pad_side_extra square per pad on EtchingPad (Rev2). "vias": via-sized
    #circles around the pad perimeter on Polyimide_Negative (Rev3), etch_via_inset_extra further in from
    #the pad edge than the routing via. No circle sits in a corner: each corner gets two neighbours, one
    #on each adjacent side, etch_via_corner_gap clear of each other and of the routing via (top-left);
    #between them each side gets evenly spaced circles at a spacing inside etch_via_spacing (shared by
    #every design). Layout: etch_via_layout in lib/generate_helpers.py.
    if CONFIG["pad_etch"] not in ("square", "vias"):
        raise SystemExit(f"pad_etch must be 'square' or 'vias', got {CONFIG['pad_etch']!r}")
    if CONFIG["pad_etch"] == "vias":
        if ROUTING["via_shape"] != "circle":
            raise SystemExit("pad_etch='vias' copies the routing via's circle; set via_shape='circle'")
        etch_r = ROUTING["via_radius"]
        etch_inset = ROUTING["via_offset_x"] + CONFIG["etch_via_inset_extra"]
        #Rev4: the stabilization vias get their own layer (own mask); None = Polyimide_Negative (Rev3)
        etch_layer = CONFIG["etch_via_layer"] or POLYIMIDE["neg_layer"]
        etch_gap = CONFIG["etch_via_corner_gap"]
        #The routing via relative to its pad's lower-left corner -- the top-left corner here; each pad's
        #actual via is checked against its etch circles below.
        if S["k0_via"]:
            via_rel = (ROUTING["via_offset_x"] + max(ROUTING["via_rest_dx"], 0.0),
                       pad_side / 2.0 + ROUTING["via_offset_y"])
        else:
            via_rel = (ROUTING["via_offset_x"], pad_side / 2.0 + ROUTING["via_offset_y"])
        extra_vias = []
        if S["k0_via"] and ROUTING.get("via_k0_h"):
            #capsule top vias (wafer6): the etch vias must also clear its two end circles
            _rk = ROUTING["via_k0_radius"] if ROUTING["via_k0_radius"] is not None else ROUTING["via_radius"]
            _d = max(ROUTING["via_k0_h"] / 2.0 - _rk, 0.0)
            _x, _y = ROUTING["via_offset_x"] + ROUTING["via_k0_dx"], pad_side / 2.0 + ROUTING["via_offset_y"]
            extra_vias = [(_x, _y + _d, _rk), (_x, _y - _d, _rk)]
        etch_rel, etch_info = etch_via_layout(pad_side, etch_inset, etch_r, etch_gap, via_rel,
                                              CONFIG["etch_via_spacing"], extra_vias)

    m1_pad_kinds = {}      # Rev4: how many wire-end pads got a teardrop / only a circle
    k0_counts = {}         # wafer6 via_k0_where="needed": top vias kept small / made full

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
                    # Metal2's pad square, each side pulled in by metal3_inset (Rev4: 2 um); no wires
                    m3 = CONFIG["metal3_inset"]
                    m3_pts = convert_rectangle_to_polyline(
                        create_rectangle(x0 + m3, y0 + m3, pad_side - 2 * m3, pad_side - 2 * m3))
                    shapes.append((m3_pts, CONFIG["layer_top"]))
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
        #k0_via -- Rev4 (12-block lane geometry only; all 0/None = Rev2/Rev3): the pad next to a lane is
        #tight, so the top pad of every column gets a smaller via (via_k0_radius) centred between its two
        #neighbour lanes (via_k0_dx), and the lower pads' vias move via_rest_dx right of the lane
        #bundle's left edge, away from the previous column's last lane. Lanes below the top one stay put.
        if S["k0_via"]:
            k0_r = ROUTING["via_k0_radius"] if ROUTING["via_k0_radius"] is not None else ROUTING["via_radius"]
            k0_dx, rest_dx = ROUTING["via_k0_dx"], ROUTING["via_rest_dx"]
            k0_where, k0_h = ROUTING.get("via_k0_where", "all"), ROUTING.get("via_k0_h")
            top_uniform = ROUTING.get("top_lane_uniform", False)
            if k0_where not in ("all", "needed"):
                raise SystemExit(f"via_k0_where must be 'all' or 'needed', got {k0_where!r}")
            via_x, lane_x, via_r = {}, {}, {}
            via_h = {}                                   # elongated top vias: total height (um)
            top_jog = set()                              # top pads whose lane jogs into the via (top_lane_uniform)
            last_lane = {}                               # column -> x of its last (rightmost) lane
        else:
            via_x, lane_x = {}, {}
        clamped_cols = 0
        for col in range(CONFIG["n_cols"]):
            members = [i for i in range(n_pads) if pad_info[i][2] == col]
            vx0 = pad_info[members[0]][3] + ROUTING["via_offset_x"]   # this column's top-pad via x (k=0)
            prev_lane = None
            #k=0 -> top pad; iterate this column's pads by DESCENDING y.
            for k, i in enumerate(sorted(members, key=lambda j: pad_info[j][1], reverse=True)):
                nat_via = vx0 + k * via_pitch                # natural staircase position
                if S["k0_via"]:
                    via_r[i] = ROUTING["via_radius"]
                if k == 0:
                    col_shift = 0.0
                    full = False
                    if S["k0_via"] and k0_where == "needed":
                        #the full via + pad at m1_margin; this column's lanes move right by s_f so its next
                        #lane keeps wire_gap from that pad. Full only if the pad keeps wire_gap from the
                        #previous column's last lane AND the shifted bundle keeps it from the next column's
                        #top via (taken as the small one, which sits furthest left) -- the last column stays small
                        enc = ROUTING["m1_pad_enc"]
                        R_f, R_s = ROUTING["via_radius"] + enc, k0_r + enc
                        dx_f = m1m + R_f - ROUTING["via_offset_x"]
                        s_f = max(0.0, dx_f + R_f + wire_gap + wire_width / 2.0 - (via_pitch + jog0))
                        last_f = nat_via + jog0 + s_f + max(len(members) - 2, 0) * wire_pitch
                        prev = last_lane.get(col - 1)
                        ok_prev = prev is None or \
                            (nat_via + dx_f - R_f) - (prev + wire_width / 2.0) >= wire_gap - 1e-9
                        ok_next = col + 1 < CONFIG["n_cols"] and \
                            (nat_via + pitch + k0_dx - R_s) - (last_f + wire_width / 2.0) >= wire_gap - 1e-9
                        full = ok_prev and ok_next
                        if full:
                            via_x[i], col_shift = nat_via + dx_f, s_f
                            k0_counts["full"] = k0_counts.get("full", 0) + 1
                    if S["k0_via"] and not full:
                        via_x[i] = nat_via + k0_dx
                        via_r[i] = k0_r
                        if k0_h:
                            via_h[i] = k0_h
                        if k0_where == "needed":
                            k0_counts["small"] = k0_counts.get("small", 0) + 1
                    if S["k0_via"]:
                        #top_lane_uniform: the top pad's lane sits one wire_pitch left of the next lane, like
                        #every other lane, and jogs 45 deg into its via; else it runs straight down onto it (Rev4)
                        lane1 = nat_via + via_pitch + jog0 + col_shift
                        lane_x[i] = lane1 - wire_pitch if (top_uniform and len(members) > 1) else via_x[i]
                        if lane_x[i] != via_x[i]:
                            top_jog.add(i)
                    else:
                        lane_x[i] = nat_via                  # top pad: straight down over the via
                        via_x[i] = nat_via
                else:
                    lane_x[i] = max(nat_via + jog0 + col_shift, prev_lane + wire_pitch)
                    #Pull the via LEFT so the trace clears it by min_wire_via_margin; keep on the pad.
                    want = min(nat_via, lane_x[i] - min_jog)
                    via_x[i] = max(want, vx0)
                    if via_x[i] > lane_x[i] - min_jog + 1e-6:
                        clamped_cols += 1                    # couldn't reach the full margin on the pad
                    if S["k0_via"]:
                        via_x[i] += rest_dx
                prev_lane = lane_x[i]
            if S["k0_via"]:
                last_lane[col] = prev_lane
        if clamped_cols:
            size_knob = "via_radius" if ROUTING["via_shape"] == "circle" else "via_w"
            print(f"WARNING: {clamped_cols} vias hit the pad's left edge before reaching the "
                  f"{ROUTING['min_wire_via_margin']} um trace-via margin; increase pad_side/via_left_jog "
                  f"or reduce {size_knob}/min_wire_via_margin for full clearance.")
        y_top = max(pad_info[i][1] for i in range(n_pads)) + ROUTING["top_margin"]

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
            if ROUTING["via_shape"] == "circle" and S["k0_via"] and i in via_h:
                vpts = capsule_polyline(vx, vcy, 2 * via_r[i], via_h[i], ROUTING["via_arc"])
            elif ROUTING["via_shape"] == "circle":
                vpts = circle_polyline(vx, vcy, via_r[i] if S["k0_via"] else ROUTING["via_radius"],
                                       ROUTING["via_arc"])
            else:
                vrect = create_rectangle(vx - ROUTING["via_w"] / 2.0, vcy - ROUTING["via_h"] / 2.0,
                                         ROUTING["via_w"], ROUTING["via_h"])
                vpts = convert_rectangle_to_polyline(vrect)
            shapes.append((vpts, ROUTING["layer_via"]))

            #Rev3 etch vias around this pad's perimeter; none may come closer than etch_gap to the via.
            if CONFIG["pad_etch"] == "vias":
                x0 = pad_info[i][3]; y0 = cy - pad_side / 2.0
                for rx, ry in etch_rel:
                    ex, ey = x0 + rx, y0 + ry
                    _d = max(via_h.get(i, 0) / 2.0 - via_r[i], 0.0) if S["k0_via"] else 0.0
                    if np.hypot(ex - vx, max(abs(ey - vcy) - _d, 0.0)) < (etch_r + via_r[i] + etch_gap
                                                      - 1e-6 if S["k0_via"]
                                                      else 2 * etch_r + etch_gap - 1e-6):
                        raise SystemExit(f"etch via ({ex:.3f}, {ey:.3f}) closer than {etch_gap} um to the "
                                         f"routing via of pad {i} -- check via_offset_x/y")
                    shapes.append((circle_polyline(ex, ey, etch_r, ROUTING["via_arc"]), etch_layer))

            #Metal1 wire: down the lane from the top, then a 45-deg jog DOWN-LEFT into the via centre
            #(vx, vcy). The jog length = lane_x - via_x, so the diagonal stays exactly 45 deg for any
            #via_pitch; the bend sits (lane_x - vx) above vcy.
            if i in merged:
                continue                                 # folded-in bottom pad: emitted as a tie below
            centerline = [(lane_x[i], y_top), (lane_x[i], vcy + (lane_x[i] - vx)), (vx, vcy)]
            ring = stroke_centerline_to_polygon(centerline, [wire_width] * 3)
            if ring is not None:
                ring = np.asarray(ring, dtype=float)
                if ROUTING["m1_pad"] and S["k0_via"] and i in top_jog:
                    #top_lane_uniform (wafer6): the lane steps into its via by a 45-deg jog shorter than the
                    #pad radius, so a teardrop along it would overshoot the bend. Instead the wire and the pad
                    #(circle or capsule) are merged and their junction rounded by a closing of radius
                    #m1_tear_fillet_r, inside a window around the pad and within their convex hull -- it only
                    #fills the notches between wire and pad, never reaches past them
                    from shapely.geometry import Polygon, box
                    enc = ROUTING["m1_pad_enc"]
                    pad_r = via_r[i] + enc
                    half_h = (via_h[i] / 2.0 + enc) if i in via_h else pad_r
                    pad = (capsule_polyline(vx, vcy, 2 * pad_r, 2 * half_h, ROUTING["via_arc"]) if i in via_h
                           else circle_polyline(vx, vcy, pad_r, ROUTING["via_arc"]))
                    f = ROUTING["m1_tear_fillet_r"]
                    wire_p, pad_p = Polygon(ring).buffer(0), Polygon(pad).buffer(0)
                    win = box(vx - pad_r - 1.0, vcy - half_h - 1.0, lane_x[i] + wire_width, vcy + half_h + 3.0 * f)
                    local = wire_p.intersection(win).union(pad_p)
                    closed = local.buffer(f, quad_segs=32).buffer(-f, quad_segs=32).intersection(local.convex_hull)
                    merged_pad = wire_p.union(pad_p).union(closed)
                    if merged_pad.geom_type != "Polygon" or merged_pad.interiors:
                        raise ValueError("wire + top-via pad did not merge into one simple polygon")
                    ring = np.asarray(merged_pad.exterior.coords, dtype=float)
                    kind = ("capsule" if i in via_h else "circle") + " jog fillet"
                    m1_pad_kinds[kind] = m1_pad_kinds.get(kind, 0) + 1
                elif ROUTING["m1_pad"] and S["k0_via"] and i in via_h:
                    #elongated top via (wafer6): the wire comes straight down onto it, so the tear blends
                    #into the capsule pad's TOP half circle, and the capsule is merged in below
                    from shapely.geometry import Polygon
                    pad_r = via_r[i] + ROUTING["m1_pad_enc"]
                    off = max(via_h[i] - 2 * via_r[i], 0.0) / 2.0
                    vcy_c = vcy + off                    # the capsule pad's TOP half-circle centre
                    cl_c = [(lane_x[i], y_top), (lane_x[i], vcy_c + (lane_x[i] - vx)), (vx, vcy_c)]
                    ring = np.asarray(stroke_centerline_to_polygon(cl_c, [wire_width] * 3), dtype=float)
                    ring, kind = wire_with_end_pad(ring, cl_c, wire_width, pad_r, ROUTING["m1_tear_fillet_r"])
                    cap = capsule_polyline(vx, vcy, 2 * pad_r, via_h[i] + 2 * ROUTING["m1_pad_enc"],
                                           ROUTING["via_arc"])
                    merged_pad = Polygon(ring).buffer(0).union(Polygon(cap).buffer(0))
                    if merged_pad.geom_type != "Polygon" or merged_pad.interiors:
                        raise ValueError("wire + capsule pad did not merge into one simple polygon")
                    ring = np.asarray(merged_pad.exterior.coords, dtype=float)
                    m1_pad_kinds["capsule " + kind] = m1_pad_kinds.get("capsule " + kind, 0) + 1
                elif ROUTING["m1_pad"]:      #Rev4: round Metal1 landing pad + teardrop at the via
                    ring, kind = wire_with_end_pad(ring, centerline, wire_width,
                                                   (via_r[i] if S["k0_via"] else ROUTING["via_radius"])
                                                   + ROUTING["m1_pad_enc"],
                                                   ROUTING["m1_tear_fillet_r"])
                    m1_pad_kinds[kind] = m1_pad_kinds.get(kind, 0) + 1
                shapes.append((ring, ROUTING["layer_wire"]))

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
                ring = np.asarray(ring, dtype=float)
                if ROUTING["m1_pad"]:      #the tie starts at the left pad's via: pad + tear there
                    ring, kind = wire_with_end_pad(ring, centerline[::-1], wire_width,
                                                   ROUTING["via_radius"] + ROUTING["m1_pad_enc"],
                                                   ROUTING["m1_tear_fillet_r"])
                    m1_pad_kinds[kind] = m1_pad_kinds.get(kind, 0) + 1
                shapes.append((ring, ROUTING["layer_wire"]))
        return shapes, pad_info

    #Build both instances: A and B use the SAME drop pattern, so B is a plain 180-deg mirror of A
    #with an identical pad count (a hole remains in the middle of the shared row).
    shapes_A, pad_info_A = build_shapes(row_start_A, short_pairs)
    shapes_B, pad_info_B = build_shapes(row_start_A, short_pairs)

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
    A_xmax -= POLYIMIDE["trim_xmax"]                  # Rev4: keep the outline where Rev3 had it
    B_xmin += POLYIMIDE["trim_xmax"]                  # (B is A reflected: its left extent is A's right)

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
    if S["pair_shorts"]:
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
        e = etch_info
        print(f"PI etch: {e['n']} circles per pad on {etch_layer} (r{etch_r} um, centres {etch_inset} um "
              f"in from the pad edge): {e['per_side']} per side, none in a corner -- corner neighbours "
              f"{e['a']:.3f} um from the corner point ({etch_gap} um gap), {e['k']} between them "
              f"{e['step']:.3f} um apart (range {CONFIG['etch_via_spacing']} um)")
    else:
        print(f"PI etch: one {pad_side_extra} um square per pad on {CONFIG['pad_layers_extra']}")
    if S["k0_via"] and (ROUTING["via_k0_radius"] is not None or ROUTING["via_rest_dx"] or ROUTING["via_k0_dx"]):
        print(f"top-row vias: r{ROUTING['via_k0_radius']} um at dx {ROUTING['via_k0_dx']:+g}; lower rows dx "
              f"{ROUTING['via_rest_dx']:+g} um (vs the lane bundle, Rev4)")
        if ROUTING.get("via_k0_where", "all") != "all" or ROUTING.get("via_k0_h"):
            print(f"top-row vias (wafer6): where={ROUTING.get('via_k0_where', 'all')}, small-via height "
                  f"{ROUTING.get('via_k0_h')} um; top vias over both instances (A + B): {k0_counts}")
    if ROUTING["m1_pad"]:
        print(f"Metal1 landing pads: {2 * (ROUTING['via_radius'] + ROUTING['m1_pad_enc']):g} um circle at each "
              f"routing via, teardrop fillet r{ROUTING['m1_tear_fillet_r']:g} um (capped to the last straight "
              f"run): {m1_pad_kinds}")
    if CONFIG["metal3"]:
        print(f"{CONFIG['layer_top']}: {pad_side - 2 * CONFIG['metal3_inset']:g} um squares centred on Metal2's "
              f"{pad_side} um pads (inset {CONFIG['metal3_inset']:g} um per side, no wires)")
    print(f"wrote {CONFIG['out_path']}")
