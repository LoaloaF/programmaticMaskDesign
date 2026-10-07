"""The generator: build the whole electrode-bundle DXF in memory.

`build_bundle(cfg)` is the old hook_bundle_generator ``__main__`` turned into a function.
It returns a `BundleResult` carrying the ezdxf doc AND the in-memory data the two
consumers need -- the per-channel contact centres (`electrode_locs`, for IONP) and the
channel->pad assignment (`chan_pad`, for the flex mapping) -- so neither consumer has to
re-read the DXF or re-derive the routing order.

Rendering is decoupled: geometry emission collects (xs, ys, colour) polylines into
`result.render`, and `render_bundle(result, ax)` draws them, replacing the inline
matplotlib calls that used to be interleaved with the geometry in __main__.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from . import dxf_io as io
from . import shapes as sh
from .config import LOOP_DATUM_VAR, REF_SITES, BundleConfig
from .geometry import convert_rectangle_to_polyline, create_ellipse_polygon
from .hooks import etch_wall, get_hook


@dataclass
class BundleResult:
    doc: object
    msp: object
    #Nx2 contact centres of the 64 RECORDING sites, in channel order. Recording ONLY -- the
    #reference's contacts are in `ref_locs` and never in here. Everything downstream treats
    #this array as "the electrode array": `lengths.measure` reads the design's site span and
    #loop offset straight off it, IONP normalizes the barcode against its ends, and
    #`probe_json` reports it as the implanted array. A reference contact leaking into it
    #would silently redefine all three.
    electrode_locs: np.ndarray
    top_points: List[Tuple[float, float]]  # (x_src, wire_top) per channel
    chan_pad: Dict[int, tuple]            # channel -> (column, pad_row) (for the mapping)
    wire_top: float
    pads_left: List[Tuple[float, float]]
    pads_right: List[Tuple[float, float]]
    num_channels: int
    #Centre of the insertion loop, the probe's depth datum -- see lengths.loop_y(). Taken
    #from the hook spec rather than measured back off a layer: the loop hole is now part of
    #the single `Etching` mask, so there is no layer holding it alone.
    loop_xy: Optional[Tuple[float, float]] = None
    #Mx2 contact centres of the REFERENCE electrode on the centre fiber, ascending y. They
    #are all one electrical node, so there is no channel order to preserve -- and they are
    #deliberately a separate array on a separate DXF layer, see `electrode_locs` above.
    #Empty when the build has no reference fiber.
    ref_locs: np.ndarray = field(default_factory=lambda: np.empty((0, 2), dtype=float))
    render: List[tuple] = field(default_factory=list)  # (xs, ys, colour) debug polylines


def _closed(xy):
    """(x_arr, y_arr) -> closed (xs, ys) lists for plotting (first point repeated)."""
    xs, ys = list(xy[0]), list(xy[1])
    return xs + [xs[0]], ys + [ys[0]]


def _union_outlines(outlines, clip_below=None):
    """Merge overlapping (x, y) outlines into the fewest closed rings that cover them.

    `clip_below` cuts everything under that y away, so a run of inline pads ends flat at a
    stated line instead of wherever the last pad's fillet happens to reach.
    """
    from shapely.geometry import Polygon as _P, box as _box
    from shapely.ops import unary_union as _u
    merged = _u([_P(zip(*o)).buffer(0) for o in outlines])
    if clip_below is not None:
        b = merged.bounds
        merged = merged.intersection(_box(b[0] - 1, clip_below, b[2] + 1, b[3] + 1))
    geoms = merged.geoms if merged.geom_type == "MultiPolygon" else [merged]
    return [(np.array([p[0] for p in g.exterior.coords]),
             np.array([p[1] for p in g.exterior.coords])) for g in geoms]


def build_bundle(cfg: BundleConfig) -> BundleResult:
    import ezdxf

    #If a reference DXF is given, drive the per-channel centres from the real electrode
    #centroids; otherwise fall back to the parametric grid.
    centroids = None
    num_channels = cfg.num_channels
    if cfg.ref_dxf is not None:
        centroids = io.extract_centroids_from_dxf(cfg.ref_dxf, cfg.ref_layer)
        num_channels = len(centroids)
        print(f"Loaded {num_channels} electrode centroids from {cfg.ref_dxf} (layer '{cfg.ref_layer}')")

    doc = ezdxf.new('R2010')
    msp = doc.modelspace()
    io.ensure_layer(doc, 'Metal', color=4)
    io.ensure_layer(doc, 'Polyimide', color=3)
    io.ensure_layer(doc, 'Electrodes', color=2)
    io.ensure_layer(doc, 'PEDOT_SIROF', color=6)
    io.ensure_layer(doc, 'Etching', color=5)
    #THE SITE OPENINGS, and only those: the small circle that bares the gold at every
    #recording site and every reference contact. It is its OWN mask, not part of `Etching`:
    #`Etching` cuts the full polyimide stack to free the device, while this one only opens
    #the top layer over a pad, and one mask cannot do both depths. The solder pads at the
    #connector end are NOT here -- see the etch mask below for where they are cut.
    io.ensure_layer(doc, 'pad_etching', color=1)
    #the reference's openings, kept OFF 'Electrodes' -- see the reference contacts below
    io.ensure_layer(doc, 'Ref_Electrodes', color=1)

    render: List[tuple] = []
    l = cfg.l

    #Per-FIBER, and there are num_channels + 1 of them: fiber 0 is the reference on the
    #centre slot, recording channel c is fiber c+1 (shapes.fiber_x_positions). Split back
    #out so the rest of the file can stay channel-indexed.
    n_fibers = num_channels + 1
    fiber_offsets = np.array(
        [float(cfg.shoulder_offsets_profile(f)) for f in range(n_fibers)], dtype=float
    ) if cfg.shoulder_offsets_profile is not None else np.zeros(n_fibers)
    ref_shoulder_offset = float(fiber_offsets[0])
    shoulder_offsets = fiber_offsets[1:]
    shoulder_max = float(fiber_offsets.max())
    connector_pad_first_y = cfg.pad_first_y + shoulder_max

    #Two columns of bond pads on the Metal layer. The bottom pad of each column is REF/GND
    #(left unconnected); the other 32 per column are wired to the channels below.
    pads_left, pads_right = sh.build_pad_columns(
        cfg.pad_row_pitch, connector_pad_first_y, cfg.pad_pitch, cfg.n_pads_per_column)
    #Each pad also has to be OPENED in the polyimide, by the same etch that frees the device
    #-- collected here and unioned into the `Etching` mask below with the electrode openings.
    #The pad METAL is not drawn yet: it is a teardrop aimed at whatever route arrives, so it
    #cannot be drawn until `build_pad_routes` has said where each one comes in from.
    pad_opening_r = cfg.pad_diam / 2 - cfg.pad_opening_inset
    assert pad_opening_r > 0, \
        f"pad_opening_inset ({cfg.pad_opening_inset}) eats the whole {cfg.pad_diam} um pad"
    pad_openings = [sh.create_circle_outline(cx, cy, pad_opening_r, cfg.circle_resolution)
                    for cx, cy in pads_left + pads_right]

    #Wires run upward from each pad to a common top. In parametric mode this is l_max (so
    #output is unchanged); in reference mode it must clear the deepest electrode.
    wire_top = cfg.l_max
    if centroids is not None and len(centroids):
        wire_top = max(cfg.l_max, float(centroids[:, 1].max()) + cfg.l_max)
    channel_wire_tops = wire_top + shoulder_offsets
    ref_wire_top = wire_top + ref_shoulder_offset
    wire_top = float(max(channel_wire_tops.max(), ref_wire_top))

    #Resolve per-channel wire lengths (parametric mode only; reference mode gets cy from the
    #DXF centroids). Base = a cumulative per-gap staircase: gap before channel i defaults to
    #delta_y (overridable via delta_y_overrides), positions accumulate from cy_0 = -delta_y, so
    #uniform gaps reproduce the original cy_i = (i-1)*delta_y exactly.
    electrode_lengths = None
    if centroids is None:
        if cfg.electrode_lengths_profile is None:
            if not cfg.delta_y_overrides:
                #Uniform staircase: keep the exact single-multiply form so the default output is
                #bit-identical (repeated addition would round differently in the last digit).
                electrode_lengths = [channel_wire_tops[i] - (i - 1) * cfg.delta_y
                                     for i in range(num_channels)]
            else:
                gaps = {i: cfg.delta_y for i in range(num_channels)}
                for ch, g in cfg.delta_y_overrides.items():
                    assert 1 <= ch < num_channels, \
                        f"delta_y_overrides channel {ch} out of range 1..{num_channels - 1}"
                    gaps[ch] = float(g)
                electrode_lengths, cy_stack = [], -cfg.delta_y
                for i in range(num_channels):
                    if i > 0:
                        cy_stack += gaps[i]
                    electrode_lengths.append(channel_wire_tops[i] - cy_stack)
        else:
            electrode_lengths = [float(cfg.electrode_lengths_profile(i)) for i in range(num_channels)]
        for ch, length in (cfg.length_overrides or {}).items():
            assert 0 <= ch < num_channels, \
                f"length_overrides channel {ch} out of range 0..{num_channels - 1}"
            electrode_lengths[ch] = float(length)
        if any(length <= l for length in electrode_lengths):
            print(f"WARNING: some electrode lengths <= pad size l={l}; "
                  "those wires will have non-positive height (degenerate geometry).")

    #Parametric x of every channel, placed so the gap between neighbouring polyimide traces is
    #constant even with a widened channel (the wide trace pushes only the channels beyond it).
    wire_hw = cfg.wire_hw
    chan_x, ref_x = sh.fiber_x_positions(num_channels, cfg.delta_x, wire_hw,
                                         cfg.wide_wire_hw)

    #One continuous polyimide body behind the bond pads AND the fanout, drawn once (NOT per
    #channel): narrow bottom on the wire bundle -> diagonal fan-out -> vertical stem ->
    #tangent-fillet flare into the wide block over the pad columns.
    poly_block_top = pads_left[-1][1] + 500
    poly_top_hw = cfg.polyimide_width / 2
    poly_bot_hw = (max(max(abs(x) for x in chan_x.values()) + wire_hw,
                       abs(ref_x) + cfg.wide_wire_hw)
                   + cfg.polyimide_bundle_margin)
    y_body_bottom = float(max(channel_wire_tops.max(), ref_wire_top))
    y_fan_top = y_body_bottom + cfg.polyimide_fan_height
    y_curve = connector_pad_first_y - cfg.polyimide_curve_offset
    #How tall the shank -> block S may be. The design's ribbon decides how much straight stem
    #there is between the top of the fan-out and the block, and a long-fiber design leaves far
    #more of it than a short one, so the configured height is an aim clamped to what is there.
    curve_h = min(cfg.polyimide_curve_height,
                  y_curve - y_fan_top - cfg.polyimide_curve_min_stem)
    polyimide_body = sh.build_polyimide_fanout_body(
        poly_bot_hw, cfg.polyimide_fan_hw, poly_top_hw,
        y_bottom=y_body_bottom, y_fan_top=y_fan_top, y_curve=y_curve,
        y_top=poly_block_top, curve_h=curve_h,
    )

    electrode_pads: Dict[int, tuple] = {}
    contact_pads: Dict[int, tuple] = {}
    pedot_pads: Dict[int, tuple] = {}
    polyimide_outlines: Dict[int, tuple] = {}
    polyimide_parts = []                # (x,y) per polyimide piece (traces + hooks) to merge

    #Alignment tabs on the two edges of the solder-pad block. They are unioned in as ordinary
    #polyimide parts rather than built into the body: the body is one profile mirrored, and
    #these two are different lengths. Both hang DOWN from the same height, which is how the
    #drawing dimensions them -- by their upper end, measured off the start of the pad area.
    _tab_top = y_curve + cfg.pad_block_tab_top
    for _side, _len in ((-1, cfg.pad_block_tab_len_left), (1, cfg.pad_block_tab_len_right)):
        assert y_curve < _tab_top - _len and _tab_top < poly_block_top, \
            f"the {_len} um alignment tab ending at y={_tab_top:.1f} does not fit on the " \
            f"block edge: it has to clear the pad-area start below ({y_curve:.1f}) and the " \
            f"top edge above ({poly_block_top:.1f})"
        polyimide_parts.append(sh.pad_block_tab(_side * poly_top_hw, _tab_top, _len))
    if np.any(shoulder_offsets):
        #The lower edge of the fanout follows the staggered fiber starts. This is the
        #variable-shoulder surface; it replaces a visually hidden set of narrow bridges.
        fiber_edge = sorted([(chan_x[i], channel_wire_tops[i]) for i in range(num_channels)]
                            + [(ref_x, ref_wire_top)])
        shoulder_edge = list(fiber_edge)
        shoulder_edge += [(x, y_body_bottom) for x, _ in reversed(fiber_edge)]
        polyimide_parts.append((np.array([p[0] for p in shoulder_edge]),
                                np.array([p[1] for p in shoulder_edge])))
    polyimide_tip_ys = []               # per-channel tip y; shallowest = negative-wedge start
    top_points = []                     # (x_src, wire_top) per channel, fed to the fanout
    electrode_locs = []                 # (x, y) contact centre per channel (for IONP)
    ref_locs = []                       # (x, y) per REFERENCE contact, ascending y
    hook_etch = None
    loop_xy = None
    hook_x_max = 0.0
    hook = get_hook(cfg.hook)

    for i in range(num_channels):
        #Per-channel centre: (bx = unsigned x magnitude, cy = y, side = +/-1).
        if centroids is not None:
            cx, cy = float(centroids[i, 0]), float(centroids[i, 1])
            side = -1 if cx < 0 else 1
            bx = abs(cx)
        else:
            xc_signed = chan_x[i]
            side = -1 if xc_signed < 0 else 1
            bx = abs(xc_signed)
            cy = channel_wire_tops[i] - electrode_lengths[i]

        xc = side * bx
        top_points.append((xc, channel_wire_tops[i]))

        #Electrode PAD + WIRE as ONE merged metal polygon.
        electrode_pads[i] = sh.create_metal_pad_wire_outline(
            xc, cy, pad_r=l / 2, wire_hw=cfg.wire_width / 2, wire_top_y=wire_top,
            R=cfg.metal_pad_fillet_radius)
        render.append((*_closed(electrode_pads[i]), 'b'))

        #Etched contact area on the electrode pad: a plain circle, concentric with it.
        contact_pads[i] = sh.create_circle_outline(
            xc, cy, cfg.l_contact / 2, cfg.circle_resolution)
        render.append((*_closed(contact_pads[i]), 'r'))

        #The PEDOT/SIROF coating deposited into that opening, concentric and slightly WIDER
        #than it (`l_pedot` > `l_contact`): the coating has to cover the exposed metal to
        #the rim, so its alignment tolerance is the overlap onto the polyimide, not a gap.
        pedot_pads[i] = sh.create_circle_outline(
            xc, cy, cfg.l_pedot / 2, cfg.circle_resolution)
        render.append((*_closed(pedot_pads[i]), 'y'))

        #Contact centre: use the true center (xc, cy) rather than averaging the polygon vertices.
        #Averaging would double-weight the first vertex due to polyline closure, shifting the
        #center rightward by ~radius/(resolution+1). The true center is already computed above.
        electrode_locs.append((xc, cy))

        #Polyimide as ONE merged polygon: flat-topped upper trace, a smooth lens-shaped
        #swelling around the round site, then the channel's ending. The HOOK's channel ends
        #flat where the hook attaches; every other channel ends in a long tapered tip
        #`polyimide_tip_len` below its own electrode centre.
        #Every recording fiber is the same now: a plain-width trace ending in a long taper
        #below its own site. The widened, hook-carrying, flat-bottomed fiber used to be
        #channel 0; it is the REFERENCE fiber now and is built after this loop.
        pad_r = cfg.polyimide_pad_r
        this_boty = cy - cfg.polyimide_tip_len
        polyimide_tip_ys.append(this_boty)
        polyimide_outlines[i] = sh.create_polyimide_outline(
            xc, cy, pad_r, wire_hw,
            wire_top_y=channel_wire_tops[i],
            bot_y=this_boty,
            R=cfg.polyimide_lens_radius,
            tip_r=cfg.polyimide_tip_r,
        )
        polyimide_parts.append(polyimide_outlines[i])

    #THE REFERENCE FIBER, on the centre slot. Not a recording channel: it carries no
    #staircase position of its own and never appears in `electrode_locs`. What it does
    #carry is the insertion hook -- and therefore the one etch hole on the probe, the loop,
    #which is every design's depth datum.
    #
    #Where it ENDS is deliberately still measured off the deepest recording site, at the
    #same `polyimide_pad_r + 250` the hook's old host fiber used. That fiber (channel 0) is
    #not here any more, but `lengths.solve_lengths` drives `hook_drop` so that loop -> the
    #deepest site hits each design's `loop_offset`; keeping the distance identical keeps
    #every design's solved geometry, and the meaning of loop_offset, exactly as it was.
    deepest_cy = min(y for _, y in electrode_locs)
    ref_attach_y = deepest_cy - cfg.polyimide_pad_r - 250
    ref_polyimide = (np.array([ref_x - cfg.wide_wire_hw, ref_x + cfg.wide_wire_hw,
                               ref_x + cfg.wide_wire_hw, ref_x - cfg.wide_wire_hw]),
                     np.array([ref_attach_y, ref_attach_y, ref_wire_top, ref_wire_top]))
    polyimide_parts.append(ref_polyimide)

    #Its gold is one straight trace the whole length of the fiber, `REF_SITES.wire_width`
    #wide rather than the channels' `wire_width`: it is a single node feeding many contacts
    #in parallel and its run to the pad is about twice a channel's, so the trace is what
    #sets the reference impedance. The fiber is already widened for the hook, so the extra
    #metal costs no lateral room.

    hook_outline = None
    if cfg.hook_on_ref_fiber:
        hx, hy = hook.polygon(ref_x, ref_attach_y, stem_hw=cfg.wide_wire_hw,
                              scale=cfg.hook_scale, drop=cfg.hook_drop)
        ecx, ecy, erx, ery = hook.etch(ref_x, ref_attach_y, stem_hw=cfg.wide_wire_hw,
                                       scale=cfg.hook_scale, drop=cfg.hook_drop)
        hook_etch = create_ellipse_polygon(ecx, ecy, erx, ery)
        wall = etch_wall((hx, hy), hook_etch)
        assert wall >= cfg.hook_etch_margin, \
            f"hook {cfg.hook!r}: only {wall:.2f} um of polyimide around the etch hole " \
            f"(hook_etch_margin is {cfg.hook_etch_margin}) -- the head would tear off"
        polyimide_parts.append((hx, hy))
        hook_outline = (hx, hy)
        loop_ry = float(ery)
        loop_xy = (float(ecx), float(ecy))
        hook_x_max = float(np.max(hx))
        render.append((hook_etch[:, 0], hook_etch[:, 1], 'r'))

    #THE REFERENCE'S TRACE runs from the fanout all the way DOWN THE HOOK, stopping
    #`metal_loop_clearance` above the loop hole rather than where the fiber ends.
    #
    #That is the difference between a trace that tracks the loop and one that does not. The
    #fiber ends a fixed `polyimide_pad_r + 250` below the deepest site, but the hook below
    #it is `hook_drop` long, and `lengths.solve_lengths` drives hook_drop to put the loop
    #where each design's `loop_offset` asks. Ending the gold relative to the FIBER therefore
    #leaves bare stem behind the moment loop_offset grows -- 237.5 um of it at loop_offset
    #500. Ending it relative to the LOOP follows the solve.
    ref_hw = REF_SITES.wire_width / 2
    #The gold stops `metal_loop_clearance` above the TOP OF THE HOLE, wherever the solve put
    #it. There is deliberately no min() with the fiber's end here.
    #
    #There used to be one, to stop a short hook pulling the trace up and leaving the bottom
    #of the fiber bare. It assumed the hole is always BELOW the fiber end -- true only while
    #`hook_drop` is positive. A design whose `loop_offset` is shorter than the fiber's own
    #overhang (`polyimide_pad_r + 250` = 262.5; U2.5 asks for 250) makes
    #`lengths.solve_lengths` drive hook_drop NEGATIVE, pulling the head up until the hole
    #sits above where the fiber ends. The clamp then ran the trace from above the hole down
    #to the fiber end -- straight ACROSS it -- and the bottom band, which fills every pitch
    #down to the end of the gold, put contacts inside the loop.
    #
    #Below the hole there is nothing to reach anyway: the stem is 20 um wide and the hole is
    #35 across, so it severs the trace completely. Ending above it leaves a short stretch of
    #bare fiber, which is the correct outcome for a loop that high, not a regression.
    ref_metal_bottom = (loop_xy[1] + loop_ry + REF_SITES.metal_loop_clearance
                        if loop_xy is not None else ref_attach_y)
    ref_metal = (np.array([ref_x - ref_hw, ref_x + ref_hw, ref_x + ref_hw, ref_x - ref_hw]),
                 np.array([ref_metal_bottom, ref_metal_bottom, wire_top, wire_top]))
    if hook_outline is not None:
        #The trace must clear the loop hole outright. This is the cheap, direct statement of
        #what the arithmetic above is for, and it is unconditional because the failure it
        #catches -- gold drawn over an etched hole, with the bottom band's pads following it
        #in -- is silent in the DXF and only shows up as a dead reference on the bench.
        from shapely.geometry import Polygon as _HolePoly, box as _hole_box
        _loop_hole = _HolePoly(hook_etch[:-1])
        assert not _hole_box(ref_x - ref_hw, ref_metal_bottom,
                             ref_x + ref_hw, wire_top).intersects(_loop_hole), (
            f"the reference's trace crosses the insertion loop: it ends at "
            f"y={ref_metal_bottom:.1f} with the hole at y={loop_xy[1]:.1f} "
            f"+/- {loop_ry:.1f}")
    if hook_outline is not None and ref_metal_bottom < ref_attach_y:
        #Only the stretch BELOW the fiber needs checking, and it is the only stretch that is
        #new. Above the fiber the trace runs up into the polyimide body, which is off this
        #outline entirely -- `wire_top` is the tallest fiber's shoulder and the reference,
        #sitting at x = 0, has the SHORTEST one, so its gold legitimately continues past the
        #top of its own fiber.
        #
        #Down here it does need checking rather than trusting the arithmetic: the hook's
        #stem is only 20 um wide and its head is pierced by the loop itself.
        from shapely.geometry import Polygon as _Poly, box as _box
        _hookp = _Poly(zip(*hook_outline))
        _hole = _Poly(hook_etch[:-1])
        _support = _hookp.difference(_hole)
        _on_hook = _box(ref_x - ref_hw, ref_metal_bottom, ref_x + ref_hw, ref_attach_y)
        assert _support.contains(_on_hook.buffer(-1e-9)), (
            f"the reference's trace runs off the polyimide on its way down the hook: it "
            f"ends at y={ref_metal_bottom:.1f} with the loop hole at "
            f"y={loop_xy[1]:.1f} +/- {loop_ry:.1f}. Raise REF_SITES.metal_loop_clearance "
            f"(it is {REF_SITES.metal_loop_clearance})")

    #THE REFERENCE'S CONTACTS. Many, not one: they are all the same electrical node, so
    #they parallel up and the reference wants area. They sit in two bands, `REF_SITES`.
    #
    #Each is an INLINE pad -- flared into the trace from above AND below, because unlike a
    #recording site it does not terminate its fiber. Same stack as a recording site
    #otherwise: `l` of gold, `l_pedot` of coating, `l_contact` of opening.
    #
    #The openings go on their OWN layer, deliberately. `Electrodes` means "the 64 recording
    #sites" to everything downstream -- IONP normalizes its barcode against that layer's
    #extent and `dxf_io.extract_electrode_centroids_from_msp` reads contact centres off it
    #-- so a reference contact appearing there would silently redefine the array.
    ref_contact_rings = []
    if loop_xy is not None:
        ref_reach = sh.inline_pad_reach(l / 2, REF_SITES.wire_width / 2,
                                        cfg.metal_pad_fillet_radius)
        #What the band needs at the TRACE'S END is smaller than `ref_reach`: the pad's own
        #gold circle, and nothing more. `ref_reach` is the metal FILLET's tangency height,
        #and a fillet is a blend into the trace -- past the end of the trace there is
        #nothing to blend into, so the union is clipped flat there (see `ref_metal` below)
        #and the fillet's last ~4.6 um simply is not drawn. Budgeting for it anyway cost
        #U2.5 its third reference contact for no gain in gold.
        ref_floor = l / 2
        shallowest_cy = max(y for _, y in electrode_locs)
        #The wall below is where the GOLD ends, not where the fiber ends. The trace runs
        #on down the hook now, and the hook's stem is the same 20 um the fiber is, so a
        #contact sits on it exactly as it would higher up. Passing the fiber end here
        #instead would refuse bands that are perfectly buildable.
        REF_SITES.check(ref_metal_bottom, ref_wire_top, deepest_cy, shallowest_cy,
                        ref_reach, cfg.l_contact, floor_reach=ref_floor)
        #the SAME arguments `check` validates against. Passing a different anchor -- or a
        #different floor, now that the bottom band's length is decided by where the gold
        #ends -- silently draws a band the check never saw.
        bottom_ys, top_ys = REF_SITES.band_ys(deepest_cy, shallowest_cy,
                                              ref_metal_bottom, ref_reach,
                                              floor_reach=ref_floor)
        ref_metal_bulges, ref_poly_bulges = [], []
        for cy_ref in list(bottom_ys) + list(top_ys):
            ref_metal_bulges.append(sh.create_inline_pad_outline(
                ref_x, cy_ref, l / 2, REF_SITES.wire_width / 2,
                cfg.metal_pad_fillet_radius))
            ref_poly_bulges.append(sh.create_inline_pad_outline(
                ref_x, cy_ref, cfg.polyimide_pad_r, cfg.wide_wire_hw,
                cfg.polyimide_lens_radius))
            opening = sh.create_circle_outline(ref_x, cy_ref, cfg.l_contact / 2,
                                               cfg.circle_resolution)
            coating = sh.create_circle_outline(ref_x, cy_ref, cfg.l_pedot / 2,
                                               cfg.circle_resolution)
            msp.add_lwpolyline(convert_rectangle_to_polyline(opening), close=True,
                               dxfattribs={'layer': 'Ref_Electrodes'})
            msp.add_lwpolyline(convert_rectangle_to_polyline(opening), close=True,
                               dxfattribs={'layer': 'pad_etching'})
            msp.add_lwpolyline(convert_rectangle_to_polyline(coating), close=True,
                               dxfattribs={'layer': 'PEDOT_SIROF'})
            render.append((*_closed(opening), 'r'))
            render.append((*_closed(coating), 'y'))
            ref_contact_rings.append(opening)
            #same convention as electrode_locs: the mean of the ring AS WRITTEN
            ref_locs.append(tuple(convert_rectangle_to_polyline(opening).mean(axis=0)))
        polyimide_parts.extend(ref_poly_bulges)
        #Clipped at `ref_metal_bottom`, which is what lets the band budget `ref_floor`
        #rather than the full fillet: the lowest contact's lower fillet would otherwise
        #hang a few um past the end the clearance to the loop hole was computed for. The
        #clip is flat and the gold ends exactly where it was meant to.
        ref_metal = _union_outlines(
            [ref_metal] + ref_metal_bulges,
            clip_below=ref_metal_bottom if loop_xy is not None else None)

    #The trace and its bulges as ONE piece of gold. Unioned rather than drawn as separate
    #overlapping polygons so the fab sees one conductor, and so the fillets read as the
    #continuous flare they are meant to be.
    for _ring in (ref_metal if isinstance(ref_metal, list) else [ref_metal]):
        msp.add_lwpolyline(convert_rectangle_to_polyline(_ring), close=True,
                           dxfattribs={'layer': 'Metal'})
        render.append((*_closed(_ring), 'b'))

    #Metal pad+wire and etched contact area per channel.
    for i in range(num_channels):
        msp.add_lwpolyline(convert_rectangle_to_polyline(electrode_pads[i]),
                           close=True, dxfattribs={'layer': 'Metal'})
        msp.add_lwpolyline(convert_rectangle_to_polyline(contact_pads[i]),
                           close=True, dxfattribs={'layer': 'Electrodes'})
        #the same circle as a MASK feature. `Electrodes` is build-only and dropped on save;
        #`pad_etching` is what the fab opens the site with.
        msp.add_lwpolyline(convert_rectangle_to_polyline(contact_pads[i]),
                           close=True, dxfattribs={'layer': 'pad_etching'})
        msp.add_lwpolyline(convert_rectangle_to_polyline(pedot_pads[i]),
                           close=True, dxfattribs={'layer': 'PEDOT_SIROF'})

    #Merge the fanout body + every per-channel trace + the hooks into ONE polygon with rounded
    #trace<->body junctions (band-restricted, radius polyimide_join_r). One Polyimide ring each.
    polyimide_merged = sh.merge_polyimide_with_fillets(
        polyimide_body, polyimide_parts,
        band_ylo=float(min(channel_wire_tops.min(), ref_wire_top)) - 3 * cfg.polyimide_join_r,
        band_yhi=float(max(channel_wire_tops.max(), ref_wire_top)) + cfg.polyimide_pad_r
                 + 3 * cfg.polyimide_join_r,
        R=cfg.polyimide_join_r,
    )
    for ring in polyimide_merged:
        io.add_ring(msp, ring, 'Polyimide')
        rx = [p[0] for p in ring]; ry = [p[1] for p in ring]
        render.append((rx + [rx[0]], ry + [ry[0]], 'g'))

    #THE ETCH MASK. `Etching` is the only layer the fab actually patterns here --
    #the positive Polyimide layer is dropped on export (WaferNesting's LAYER_MAP), because
    #the device is not deposited, it is FREED from a full sheet of polyimide. So everything
    #removed in that one step has to be in here: the moat around the device, the spear that
    #frees the tip, and the insertion loop's hole.
    #
    #The moat hugs the polyimide at `polyimide_negative_margin`; below the shoulder that is
    #replaced by a clean silhouette -- a full-width block down to the shallowest tip, then a
    #straight WEDGE toward a point under the hook. A silhouette is not a guarantee though:
    #anything reaching outside it (the hook's claps do, by ~15 um) would be left joined to
    #the unetched sheet, so the true moat is unioned back in below the shallowest tip. It
    #only shows where the silhouette actually falls short.
    #
    #That moat is drawn to `polyimide_release_margin`, NOT to the body's
    #`polyimide_negative_margin`: it only has to guarantee the claps come free, and at the
    #body's 200 um it swelled into a nose wide enough to swallow the spear's point.
    from shapely.geometry import Polygon as _Polygon, box as _box
    from shapely.ops import unary_union as _unary_union
    _poly = _unary_union([_Polygon(r).buffer(0) for r in polyimide_merged])
    _outer = _poly.buffer(cfg.polyimide_negative_margin, join_style=2)
    _y_sh = max(polyimide_tip_ys)
    _xs = (max(max(abs(x) for x in chan_x.values()) + wire_hw,
               abs(ref_x) + cfg.wide_wire_hw) + cfg.polyimide_negative_margin)
    _ob = _outer.bounds
    _apex_y = _poly.bounds[1] - cfg.polyimide_negative_margin
    _apex_x = ref_x                      # the wedge points at the hook, i.e. the ref fiber
    _body = _outer.difference(_box(_ob[0] - 1, _ob[1] - 1, _ob[2] + 1, y_body_bottom))
    _rect = _box(-_xs, _y_sh, _xs, y_body_bottom)
    _wedge = _Polygon([(-_xs, _y_sh), (_xs, _y_sh), (_apex_x, _apex_y)])
    _release = _poly.intersection(
        _box(_ob[0] - 1, _ob[1] - 1, _ob[2] + 1, _y_sh)
    ).buffer(cfg.polyimide_release_margin, join_style=2)
    _outer = _unary_union([_body, _rect, _wedge, _release])
    _negative = _outer.difference(_poly)
    if hook_etch is not None:
        #The loop is etched in the SAME step, so it is a hole in this mask, not a layer of
        #its own. It lands as an island inside the hook head -- its own ring below.
        _negative = _unary_union([_negative, _Polygon(hook_etch[:-1])])
    #The SITE openings are NOT in here. They are a different etch -- through the top
    #polyimide only, down onto the gold -- and they have their own mask, `pad_etching`,
    #written where the contacts are built. What IS in here is the SOLDER pads: the whole
    #stack over them comes away with the device release, or there is nothing to solder to.
    _negative = _unary_union(
        [_negative] + [_Polygon(zip(*opening)) for opening in pad_openings])
    for _g in (_negative.geoms if _negative.geom_type == "MultiPolygon" else [_negative]):
        _coords = sh.polygon_to_seamed_ring(_g)
        io.add_ring(msp, _coords, 'Etching')
        render.append(([p[0] for p in _coords], [p[1] for p in _coords], 'm'))

    #Route every electrode wire up into its bond pad: one tapered Metal polygon per channel.
    route_polys, ref_route, pad_tails = sh.build_pad_routes(
        top_points, pads_left, pads_right,
        neck_tw=cfg.fan_neck_tw, top_tw=cfg.fan_top_tw,
        neck_len=cfg.fan_neck_len, fan_len=cfg.fan_fan_len,
        bundle_pitch=cfg.bundle_pitch, approach_deg=cfg.pad_approach_deg,
        ref_top_point=(ref_x, ref_wire_top), ref_tw=REF_SITES.wire_width,
        pad_r=cfg.pad_diam / 2, poly_hw=poly_top_hw, poly_top_y=poly_block_top,
        wrap_clearance=cfg.ref_wrap_clearance, wrap_exit_run=cfg.ref_wrap_exit_run,
        wrap_corner_chamfer=cfg.ref_wrap_corner_chamfer,
        wrap_pad_chamfer=cfg.ref_wrap_pad_chamfer,
    )
    for i in range(num_channels):
        ring = route_polys.get(i)
        if ring is None:
            continue
        io.add_ring(msp, ring, 'Metal')
        rx = [p[0] for p in ring]; ry = [p[1] for p in ring]
        render.append((rx + [rx[0]], ry + [ry[0]], 'b'))
    if ref_route is not None:
        io.add_ring(msp, ref_route, 'Metal')
        render.append(([p[0] for p in ref_route], [p[1] for p in ref_route], 'b'))

    #THE SOLDER PADS, drawn last of the gold at this end because each one is a TEARDROP
    #aimed back along the route that feeds it -- the same circle+tangent-fillet blend the
    #electrode sites use (`create_metal_pad_wire_outline`), just rotated onto the diagonal.
    #The spare pad has no route, so it stays a plain circle.
    for _col, _pads in (('left', pads_left), ('right', pads_right)):
        for _row, (cx, cy) in enumerate(_pads):
            _tail = pad_tails.get((_col, _row))
            if _tail is None:
                pad_poly = sh.create_circle_outline(cx, cy, cfg.pad_diam / 2,
                                                    cfg.circle_resolution)
            else:
                pad_poly = sh.create_metal_pad_teardrop(
                    cx, cy, pad_r=cfg.pad_diam / 2, wire_hw=cfg.fan_top_tw / 2,
                    R=cfg.pad_teardrop_fillet_radius, tail_dir=_tail)
            msp.add_lwpolyline(convert_rectangle_to_polyline(pad_poly), close=True,
                               dxfattribs={'layer': 'Metal'})
            render.append((*_closed(pad_poly), 'k'))

    #The design's ID, stamped twice, both upright, both in Metal:
    #
    #  beside the ref's bottom contact band   in the open wedge the recording fibers leave
    #                    clear as they fan out from the hook -- NOT beside the hook itself,
    #                    which is too narrow (claps either side) for upright text. Anchored
    #                    off `bottom_ys` (the reference's own contacts), clear of their
    #                    polyimide swelling. Etched carrier, not the finished probe -- it
    #                    identifies the piece during processing, not afterwards. Read from
    #                    the same face it is drawn on, so it is NOT mirrored.
    #  above the pads    centred in the clear band above the top of the solder-pad field,
    #                    sized to fill it. This one is on the device and survives, and the
    #                    device is looked at from the face OPPOSITE the one it is drawn on
    #                    here, so this one IS mirrored left-right to read correctly there.
    if cfg.label:
        #beside the ref band: upright, left edge just clear of the contacts' polyimide
        #swelling, bottom at the topmost (shallowest) contact of the bottom band
        _hook_y0 = (bottom_ys[-1] if loop_xy is not None else _apex_y)
        _hook_rings = sh.text_outlines(
            cfg.label,
            (ref_x + cfg.polyimide_pad_r + cfg.label_gap, _hook_y0),
            cfg.label_tip_height, rotation_deg=0.0, align="left", mirror=False)
        for _ring in _hook_rings:
            io.add_ring(msp, _ring, 'Metal')
            render.append(([q[0] for q in _ring], [q[1] for q in _ring], 'k'))

        #above the pads: upright, position by `label_pad_x`/`label_pad_y` when set, else
        #centred on the shank with the TOP edge just under the polyimide edge (so growing
        #label_pad_height extends the label DOWNWARD, toward the pads). No fit check --
        #an oversized label or an explicit override can run into the pads or off the
        #polyimide; that is on whoever set it.
        _pad_x = cfg.label_pad_x if cfg.label_pad_x is not None else 0.0
        _pad_y = cfg.label_pad_y if cfg.label_pad_y is not None else poly_block_top - cfg.label_gap
        _pad_rings = sh.text_outlines(
            cfg.label, (_pad_x, _pad_y),
            cfg.label_pad_height, rotation_deg=0.0, align="center", valign="top",
            mirror=True)
        for _ring in _pad_rings:
            io.add_ring(msp, _ring, 'Metal')
            render.append(([q[0] for q in _ring], [q[1] for q in _ring], 'k'))

    #Stamp the loop datum into the header so side_by_side can line designs up from a saved
    #file. It is a header property, not geometry, so no fab layer can pick it up.
    if loop_xy is not None:
        doc.header.custom_vars.append(LOOP_DATUM_VAR, repr(float(loop_xy[1])))

    chan_pad = sh.assign_channels_to_pads(top_points)
    return BundleResult(
        doc=doc, msp=msp,
        electrode_locs=np.asarray(electrode_locs, dtype=float),
        ref_locs=np.asarray(ref_locs, dtype=float).reshape(-1, 2),
        top_points=top_points, chan_pad=chan_pad, wire_top=wire_top, loop_xy=loop_xy,
        pads_left=pads_left, pads_right=pads_right,
        num_channels=num_channels, render=render,
    )


def render_bundle(result: BundleResult, ax) -> None:
    """Draw the collected debug polylines onto a matplotlib axis (equal aspect)."""
    for xs, ys, colour in result.render:
        ax.plot(xs, ys, colour)
    ax.set_aspect('equal')
