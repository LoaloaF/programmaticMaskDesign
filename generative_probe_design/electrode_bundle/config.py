"""Tuning knobs for the whole pipeline, as dataclasses with today's values as defaults.

One place to change parameters instead of the three separate module-level constant
blocks that used to live in hook_bundle_generator.py, gen_ionp_bundle.py and
build_electrode_flex_mapping.py. To tweak the design, either edit a default here or
construct a config with overrides, e.g. ``BundleConfig(wide_channel=None, hook_drop=300)``.

`num_channels` / `n_pads_per_column` are shared here rather than re-declared per script.
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np

#One target folder per design set, next to the package rather than inside it, holding BOTH
#halves of the pipeline:
#
#    <TARGET_SET>/
#      shanks/    <- this generator's output: DXFs, PNGs, per-design JSON
#      wafer/     <- WaferNesting's output: footprints, poses, preview, wafer GDS
#
#WaferNesting/config_64ch_4shank.py derives its two paths from the SAME constant, so a new
#design set is one edit here and one there -- and the shanks a wafer was nested from can
#never drift into a different folder than the wafer built from them.
#Resolved from the module location, so nothing needs editing on a new machine.
#NOTE: the shipped `...Example` DXFs stay in electrode_bundle/designs/ -- they are inputs,
#not output.
TARGET_SET = "64Ch_4Shankdesigns"
_OUT_DIR = str(Path(__file__).resolve().parent.parent / TARGET_SET / "shanks")


def ensure_out_dir() -> str:
    """Create the shanks output folder if it doesn't exist yet. Call before writing."""
    Path(_OUT_DIR).mkdir(parents=True, exist_ok=True)
    return _OUT_DIR


#DXF header property carrying the insertion loop's y. The loop hole is etched in the same
#step that frees the device, so it has no layer of its own to be measured off any more --
#build_bundle stamps the datum here instead, and side_by_side lines designs up from it.
#A header property, so no fab layer export can pick it up as a feature.
LOOP_DATUM_VAR = "LOOP_Y_UM"


#Layers the build uses to describe itself, which are STRIPPED from every DXF it writes.
#
#None of them is a process step, and each one duplicates geometry that a real mask layer
#already carries, so shipping them means shipping three ways to be wrong about the same
#feature:
#
#  Electrodes       the 64 recording openings. Each site lies entirely inside `Metal` (a 14
#                   um exposed circle concentric in a 17 um gold pad) and the opening that
#                   actually gets cut is in `pad_etching`, which holds the same circles.
#  Ref_Electrodes   the reference's openings on the centre fiber -- same story, and they are
#                   in `pad_etching` too: one mask opens every site, recording or reference.
#  Polyimide        the POSITIVE body. Fab patterns the negative (`Etching`); this is the
#                   complement of it, kept only so the build can reason about the body.
#
#They are still built, because the build and its tests read contact centres and the body
#outline off them, and `dxf_io.save_dxf` drops them on the way out. That is the whole
#reason to strip at SAVE rather than never emit: in-memory they are useful, in a file they
#are a second, unversioned copy of the mask that some tool downstream will eventually
#believe. `WaferNesting`'s LAYER_MAP omits them too -- belt and braces, since a DXF from an
#older build may still carry them.
EXPORT_DROP_LAYERS = ('Electrodes', 'Ref_Electrodes', 'Polyimide')


def bundle_dxf_path(tag: str) -> str:
    """Where a tagged, IONP-free bundle DXF lives: written by batch.py, read by
    side_by_side.py. One definition so the two cannot drift apart."""
    return f"{_OUT_DIR}/electrode_bundle_{tag}.dxf"


def variant_tag(name: str, ionp_pattern_id: Optional[int] = None) -> str:
    """The output tag for a design: its name, plus the IONP pattern code when it carries one.

    One token, no separator -- `U1.6C03`. The name says what the probe is (U = uniform site
    spacing, then its length in mm) and `Cnn` says which barcode it wears, and `nn` is the
    BANK id 00-15 straight out of `ionp_patterns.IONP_PATTERNS`, not a per-design counter.
    So a code identifies a pattern on its own: U1.6 ships C00-C03, U2.5 C04-C07, U4 C08-C11,
    U8 C12-C15, and no code appears on two designs.

    This is exactly `design_label`'s text with the newline taken out, deliberately -- what is
    etched into the gold and what the file is called should be the same string, so a piece
    read under a scope can be matched to its artefacts without a lookup.

    Every artefact of a design (DXF, PNG, info JSON, mapping JSON) is named off this, so a
    design and its barcoded variants never overwrite each other. batch.py ALSO writes the
    barcode-free geometry at `bundle_dxf_path(name)` for every design, barcoded or not,
    because side_by_side.py reads that path and used to crash on id-carrying designs.
    """
    return name if ionp_pattern_id is None else f"{name}C{ionp_pattern_id:02d}"


#THE REFERENCE ELECTRODE, on the centre fiber.
#
#The centre fiber is not a recording channel: it is the Ref. It is the fiber that carries
#the insertion hook, it is widened already, and putting the Ref there is what makes the
#bundle symmetric -- 64 recording fibers split 32/32 either side of it, rather than the
#off-by-half-a-pitch layout an even fiber count gives.
#
#It carries MANY contacts, not the single site every recording fiber ends in, because a
#reference wants area: every contact on it is the same node, so they parallel up. They sit
#in TWO bands, and the two are anchored at OPPOSITE ENDS of the fiber on purpose:
#
#      shoulder (fiber top)
#         |
#         |<-- top_offset
#       [ === ]  top_len          in the free fiber, above every recording site
#         |
#         |     (the recording array lives here -- no Ref contacts)
#         |
#       [ === ]  no length knob   fills every pitch down to where the gold ends
#         |<-- tip_offset
#       trace start (metal_loop_clearance above the loop hole, down the hook)
#
#WHY THE BOTTOM BAND IS MEASURED FROM THE TRACE START AND NOT FROM THE LOOP. The loop is
#the obvious datum -- it is what every design's lengths are quoted against -- but it does
#not work here, and the failure is not subtle. `lengths.solve_lengths` drives `hook_drop`
#to put the loop where each design asks, so the distance from the loop up to where the
#reference's trace actually begins is a SOLVED quantity: 285 um on an unsolved config,
#87.5 um once converged. A band measured from the loop therefore moves while the solver is
#moving the loop, and part way through the solve it sits below the trace entirely, on the
#hook head, where there is no metal to be a contact. The build refuses it and the solve
#dies.
#
#The trace start has no such problem: it is `polyimide_pad_r + 250` below the deepest
#recording site on EVERY config, solved or not, by construction. So the bottom band is
#stable, and the loop -> first contact distance is reported as an output instead of being
#an input.
#
#Module-level and shared by every design -- this is a property of how the reference is
#made, not something a DesignSpec tunes, so it is not a BundleConfig field.


@dataclass(frozen=True)
class RefSpec:
    """Where the reference contacts sit on the centre fiber, and how wide its trace is."""
    #The bottom band lives in a SMALL window, between the two walls below:
    #
    #    trace start  -------- ~457 um -------->  deepest recording site
    #
    #`trace start` is where the GOLD ends, which is NOT where the fiber ends. The fiber
    #stops `polyimide_pad_r + 250` = 262.5 um below the deepest site, but the trace carries
    #on down the hook's stem and stops `metal_loop_clearance` above the loop hole -- and the
    #stem is the same 20 um wide the fiber is, so contacts sit on it just the same. That is
    #the number to budget against, and it MOVES WITH `loop_offset`: a design with a longer
    #loop offset has a longer hook, hence more trace, hence more room here.
    #
    #Below the trace start there is no gold, so there is nothing to put a contact on;
    #above the deepest site the Ref would be running alongside the recording array and
    #inside the IONP barcode region. The two walls do NOT take the same clearance. Against
    #the recording array a contact carries its full drawn extent, the metal fillet's 13.1 um
    #(`shapes.inline_pad_reach`). Against the trace's end it carries only its own gold
    #circle, `l/2` = 8.5: a fillet is a blend INTO the trace, there is nothing below the end
    #to blend into, and `bundle.py` clips the gold flat there -- so the fillet's lower half
    #is never drawn and budgeting for it only shortened the band.
    #
    #THE BOTTOM BAND HAS NO LENGTH KNOB. It is not a band of a chosen length, it is "the
    #rest of the wire": it starts `tip_offset` below the deepest site and keeps stepping
    #`pitch` down for as long as the next contact still lands on gold. A length parameter
    #here would have to be re-tuned for every design, because the wall it runs into MOVES
    #WITH `loop_offset` -- the gold ends relative to the loop, and the solver puts the loop
    #where the design asks. Setting it to fill instead means a longer loop offset simply
    #buys more reference area, with no knob to chase. The only thing that has to fit is the
    #ANCHOR contact:
    #
    #    tip_offset  <  (deepest site - trace start) - 8.5
    #
    #which is ~449 um at loop_offset 500, not the 262.5 the FIBER alone would allow. The
    #build reports the exact number, and the contact count, either way.
    #
    #It cuts the other way too, and that is not a fault. A `loop_offset` SHORTER than the
    #fiber's own 262.5 um overhang drives `hook_drop` negative, pulling the head up until
    #the hole sits above where the fiber ends; the gold then stops above the hole and the
    #band gets what little is left. U2.5, at loop_offset 250, fits three contacts where the
    #500 um designs fit fourteen. Reaching further down is not an option -- the hole is 35
    #um across on a 20 um stem, so below it the trace is severed, and drawing through it
    #puts reference pads inside the loop.
    tip_offset: float = 150.0     # deepest recording site -> the HIGHEST contact, DOWNWARD
    top_offset: float = 230.0     # shallowest recording site -> LOWEST contact, UPWARD
    top_len: float = 1000.0       # how much further up the band runs from there
    #Contact-to-contact within a band. The COUNT follows from the band length, it is not
    #set directly -- a band fits floor(len/pitch)+1.
    #
    #The real floor is `l_contact`: below that the OPENINGS touch and the band stops being
    #separate sites at all. That one is asserted. A second, softer number is 26.25 um,
    #where neighbouring metal fillets meet back to back (2 * _pad_fillet(l/2,
    #wire_width/2, metal fillet R)) -- below it the gold bulges stop being discrete and the
    #trace becomes a scalloped ribbon, necking to ~5 um between contacts at pitch 20 rather
    #than back to the bare 4 um. That is cosmetic, not a fault: every opening still sits
    #fully on gold (checked down to pitch 14), and for a reference the extra metal is free
    #area.
    pitch: float = 23.0
    #The Ref trace is WIDER than the 2 um every recording channel gets. Its path is roughly
    #twice as long (it climbs past the whole pad column and wraps back down the outside to
    #reach pad 65), and every contact on it is in parallel, so the trace itself is what sets
    #the reference impedance. The fiber is already widened for the hook, so this costs no
    #lateral room. The contact stack itself is unchanged -- `l`, `l_contact` and `l_pedot`
    #off BundleConfig, the same circles every recording site gets.
    wire_width: float = 4.0
    #How close the gold may get to the LOOP HOLE, edge to edge. The reference's trace does
    #not stop where the fiber does -- it carries on down the hook's stem and into the head,
    #stopping this far above the hole. That is what ties it to `loop_offset`: the hook's
    #own length is `hook_drop`, which `lengths.solve_lengths` drives to place the loop, so
    #a trace that ends relative to the LOOP follows the loop wherever a design puts it. An
    #end fixed relative to the fiber does not, and leaves bare stem behind whenever
    #loop_offset grows -- 237.5 um of it at loop_offset 500.
    metal_loop_clearance: float = 20.0

    def band_ys(self, deepest_site: float, shallowest_site: float,
                trace_start: float, reach: float, floor_reach: float = None):
        """(bottom_ys, top_ys): the contact centres of the two bands, each ascending.

        Both bands hang off one end of the RECORDING ARRAY and step `pitch` away from it:
        the bottom band's HIGHEST contact is exactly `tip_offset` below the deepest site
        and the rest run down toward the hook; the top band's LOWEST is exactly
        `top_offset` above the shallowest site and the rest run up into the free fiber.

        So both offsets say the same kind of thing -- how far clear of the recording array
        that band starts. Neither depends on how long a particular design's fiber happens
        to be, which is why the top band is measured from the shallowest site and not from
        the shoulder.

        `reach` is the contact's full drawn extent, the metal fillet's tangency height, and
        it is what the ARRAY-side and fiber-top walls are measured against. The trace's end
        takes `floor_reach` instead, which is smaller: the fillet is a blend INTO the trace,
        and at the trace's end there is nothing below to blend into, so the build clips the
        gold flat there. What has to fit above the end is the pad's own gold CIRCLE, `l/2` --
        that is what keeps the 1 um gold rim around the coating. Budgeting the fillet there
        as well threw away ~4.6 um for nothing, which on U2.5 was the whole difference
        between two reference contacts and three.

        They differ in where they STOP. The top band runs `top_len`, a plain number,
        because the free fiber above the array is kilometres of room and the band is
        choosing how much of it to use. The bottom band has no such knob: it fills, taking
        every pitch that still lands on gold above `trace_start` (with `reach` for the
        contact's own fillet). Its floor moves with `loop_offset`, so a fixed length there
        would need re-tuning per design; filling means a longer hook just yields more
        reference area. `check` is what rejects a `tip_offset` that leaves room for none.
        """
        import numpy as _np
        room = deepest_site - self.tip_offset - self._floor(reach, floor_reach) - trace_start
        n_bot = max(1, int(room // self.pitch) + 1)
        n_top = int(self.top_len // self.pitch) + 1
        bottom = deepest_site - self.tip_offset - _np.arange(n_bot)[::-1] * self.pitch
        top = shallowest_site + self.top_offset + _np.arange(n_top) * self.pitch
        return bottom, top

    @staticmethod
    def _floor(reach, floor_reach):
        """Clearance the TRACE'S END needs; defaults to the full drawn `reach`."""
        return reach if floor_reach is None else floor_reach

    def check(self, trace_start, fiber_top, deepest_site, shallowest_site, reach,
              contact_d, floor_reach: float = None) -> None:
        """Both bands must land on trace that exists, and clear of the recording array.

        `reach` is how far a contact's geometry extends beyond its own centre (the metal
        fillet's tangency height), so the walls are checked against the drawn extent rather
        than the centre point. Raises rather than warns: every one of these failures draws a
        contact somewhere that is not a reference site.
        """
        if self.pitch <= contact_d:
            raise ValueError(
                f"the Ref's contact pitch ({self.pitch}) is at or below the opening "
                f"diameter ({contact_d}), so neighbouring openings touch and the band is "
                f"one long contact rather than separate sites -- raise pitch")
        bottom, top = self.band_ys(deepest_site, shallowest_site, trace_start, reach,
                                   floor_reach)
        _floor = self._floor(reach, floor_reach)
        #`<` and not `<=`: the band fills to exactly this line, so the last contact TOUCHING
        #it is the normal outcome whenever `room` divides by `pitch`, not a fault -- the
        #pad's gold circle ends flush with the end of the wire and the coating still has its
        #1 um rim. Only a contact genuinely PAST the end is wrong. (1 nm of slack because
        #both sides are computed, not measured.)
        if bottom[0] - _floor < trace_start - 1e-6:
            #The bottom band FILLS, so it cannot overrun on its own -- reaching here means
            #even its single anchor contact is below the end of the gold.
            raise ValueError(
                f"the Ref's bottom band has nowhere to go: its anchor contact would reach "
                f"down to y={bottom[0] - _floor:.1f}, at or below where the trace begins "
                f"({trace_start:.1f}) -- there is nothing but hook head down there. "
                f"tip_offset is {self.tip_offset}, and must stay under "
                f"{deepest_site - trace_start - _floor:.1f}")
        if bottom[-1] + reach >= deepest_site:
            raise ValueError(
                f"the Ref's bottom band reaches y={bottom[-1] + reach:.1f}, at or above the "
                f"deepest recording site ({deepest_site:.1f}) -- it would run alongside the "
                f"array and into the IONP barcode region. Raise tip_offset (it is "
                f"{self.tip_offset}, and needs > {reach:.1f})")
        if top[0] - reach <= shallowest_site:
            raise ValueError(
                f"the Ref's top band reaches down to y={top[0] - reach:.1f}, at or below the "
                f"shallowest recording site ({shallowest_site:.1f}) -- raise top_offset "
                f"(it is {self.top_offset}, and needs > {reach:.1f})")
        if top[-1] + reach >= fiber_top:
            raise ValueError(
                f"the Ref's highest contact reaches y={top[-1] + reach:.1f}, past the top "
                f"of its own fiber ({fiber_top:.1f}). top_offset + top_len is "
                f"{self.top_offset + self.top_len:.1f}, and must stay under "
                f"{fiber_top - shallowest_site - reach:.1f}")


REF_SITES = RefSpec()


@dataclass
class BundleConfig:
    """Everything the electrode-bundle generator needs (hook_bundle_generator knobs)."""
    # --- core geometry (micron) ---
    wire_width: float = 2          # width of the wire
    delta_y: float = 8000 / 63     # default staircase step between electrode depths
    # THE ELECTRODE SITE, four concentric circles (see changes_plan/new_circular_electrode.png
    # and the dimensioned tip drawing). Sizes are the drawing's, rounded:
    #     l_contact  13.5   the EXPOSED electrode -- the opening etched in the polyimide,
    #                       cut by `pad_etching`, its own mask: that etch goes through the
    #                       top polyimide onto the gold, not through the stack like `Etching`
    #     l_pedot    15     the PEDOT/SIROF coating dropped into that opening. WIDER than the
    #                       opening on purpose: it must reach the rim of the exposed gold, so
    #                       its alignment budget is overlap onto polyimide, not a bare ring.
    #                       It overlaps by (15 - 13.5)/2 = 0.75 um all round.
    #     l          17     the gold pad under it, wider so the opening always lands on
    #                       metal and the coating has 1 um of gold outside it all round
    #     2 * polyimide_pad_r  25   the polyimide swelling around all of them; it covers the
    #                       gold rim by 4 um, which is what holds the pad down
    l: float = 17                  # DIAMETER of the round gold electrode pad
    l_contact: float = 13.5        # DIAMETER of the exposed electrode (the etched opening)
    l_pedot: float = 15            # DIAMETER of the PEDOT/SIROF coating in that opening
    num_channels: int = 64         # channels per shank
    bottom_elec: float = 12000      # depth of the bottom-most electrode
    padding: float = 4
    delta_x: float = 24            # channel pitch (> wire_width + 2*padding)

    # --- per-electrode wire-length control ---
    # channel_index -> wire_length_micron; anything unlisted keeps the default staircase.
    # length_overrides: Dict[int, float] = field(default_factory=lambda: {2: 1000})
    length_overrides: Dict[int, float] = None
    # per-gap spacing overrides (parametric mode only): channel_index -> distance (um) BEFORE
    # that electrode, i.e. the gap between channel i-1 and i. Unlisted channels use `delta_y`.
    # Positions are the cumulative sum of gaps, so changing one gap shifts every electrode above
    # it. Valid keys are 1..num_channels-1 (channel 0 has no gap before it). Ignored when
    # electrode_lengths_profile is set (the profile replaces the whole base staircase).
    delta_y_overrides: Optional[Dict[int, float]] = field(default_factory=lambda:{i: 30.0 for i in range(33, 49)})
    # delta_y_overrides: Optional[Dict[int, float]] = None
    #
    # ANCHOR -- which end stays put when you retune gaps. The staircase is built BOTTOM-UP
    # (bundle.py seeds cy_stack = -delta_y and accumulates upward), so the DEEP TIP (channel 0,
    # at `bottom_elec`) is pinned and the shallow end absorbs every change: tightening gaps pulls
    # the electrodes ABOVE the edited band DOWNWARD, so their wires get LONGER. There is no
    # spacing_anchor option, and none is needed -- pinning the shallow end instead differs by a
    # pure rigid vertical offset (identical spacings), so it is reachable with the existing knob:
    #
    #     span   = 63*delta_y - sum(delta_y - new_gap)   over every overridden gap
    #     shift  = sum(delta_y - new_gap)                # how far the shallow end drops
    #     to pin the shallow end instead: bottom_elec -= shift
    #
    # With today's values (delta_y = 8000/63 = 126.98, 16 gaps tightened to 30.0) the shallow end
    # sits 1551.75 um lower than a uniform staircase, and the span is 6448.25 um (not 8000).
    #
    # INVARIANT either way: the fanout, the bond pads and the common `wire_top` (= bottom_elec -
    # l/2) do NOT depend on delta_y or on the gaps, and never move. Only the electrode contact
    # positions and their own vertical wire lengths change.
    # Optional full formula-driven profile i -> length (replaces the base for ALL channels);
    # length_overrides still applies on top. None -> use the staircase.
    electrode_lengths_profile: Optional[Callable[[int], float]] = None
    # Optional per-FIBER shoulder displacement above the parametric shoulder. Indexed by
    # FIBER, not by channel: fiber 0 is the reference on the centre slot and recording
    # channel c is fiber c+1, matching shapes.fiber_x_positions. The electrode lengths
    # profile must use the matching channel-specific shoulder when this is set; None
    # preserves the shared shoulder geometry.
    shoulder_offsets_profile: Optional[Callable[[int], float]] = None

    # --- electrode-site radii ---
    # The pads are CIRCLES (contact, metal and the polyimide swelling around them), so there
    # are no corners to round any more -- what is left is the two TANGENT FILLET radii that
    # blend each circle into the straight trace above it. Bigger radius = longer, gentler
    # flare; see shapes.create_polyimide_outline for the geometry.
    polyimide_pad_r: float = 12.5       # radius of the polyimide swelling around the site
    polyimide_lens_radius: float = 12   # polyimide trace <-> polyimide swelling
    metal_pad_fillet_radius: float = 8  # metal wire <-> metal pad; a long, gentle flare
    circle_resolution: int = 64         # vertices per full circle (pads and bond pads)

    # --- polyimide tip, below each electrode ---
    # Every channel except the hook's ends in a long taper below its site: straight sides
    # from where the swelling closes, down to a small round cap. `polyimide_tip_len` is
    # measured from the ELECTRODE CENTRE (not from the end of the swelling), because that is
    # the length the drawing dimensions and the thing that matters -- how far past its own
    # site each fiber reaches.
    polyimide_tip_len: float = 100      # electrode centre -> the very end of the tip
    polyimide_tip_r: float = 1.0        # radius of the rounding at that end

    # --- fanout / routing knobs ---
    fan_top_tw: float = 10         # trace width up the riser/diagonal
    fan_neck_len: float = 200      # vertical neck above wire_top, at source pitch
    fan_fan_len: float = 2000      # vertical rise of the gather fan
    bundle_pitch: float = 25       # lane pitch of the centred bundle
    pad_approach_deg: float = 140  # interior bend angle riser->diagonal into the pad

    # --- the reference's wrap-around route to its solder pad ---
    # The reference is the centre fiber, so it is the INNERMOST lane, and row 0 of the pad
    # column is the pad NEAREST the bundle. That pair is exactly the crossing case the pad
    # ordering exists to avoid, so it cannot take an ordinary diagonal: it climbs past the
    # whole pad field, crosses over above it, comes back down the empty corridor OUTSIDE
    # the pad column and enters its pad from the side. `shapes._build_ref_wrap_route`.
    #
    # These are the knobs for the shape of the top of that route. Every one of them is
    # asserted against the geometry it has to clear, so a value that does not fit fails the
    # build with the number it needed rather than drawing a short.
    ref_wrap_clearance: float = 100   # demanded edge-to-edge at every pad and both edges
    # HOW TO TUNE THE TOP OF THE ROUTE. The two diagonals and the flat bar between them
    # share a fixed span, so the three lengths are ONE equation:
    #
    #     ref_wrap_exit_run  +  bar  +  ref_wrap_corner_chamfer  =  1250 um
    #
    # The 1250 is the gap between the centre lane (x = 0) and the corridor, and it is not a
    # knob -- it falls out of pad_row_pitch, pad_diam, polyimide_width and
    # ref_wrap_clearance. So set the two below and THE BAR IS WHATEVER IS LEFT: to make the
    # bar longer, SHORTEN exit_run.
    #
    #                        <-- bar -->
    #                       ____________
    #                      /            \
    #                     /              \  <- corner_chamfer, 45 deg
    #        exit_run -> /                |
    #                   /                 |  corridor, down to the pad
    #         centre lane
    #
    # How far ACROSS the diagonal out of the centre lane runs. It climbs at
    # `pad_approach_deg`, the same angle every channel's peel-off uses, so it sits parallel
    # to the diagonals beside it. Note it is the HORIZONTAL extent: the vertical rise is
    # that times tan(50 deg), i.e. ~1.19x larger.
    #
    # Both knobs are bounded by the TOPMOST PAD, and each in its own way -- exit_run
    # because a long enough diagonal reaches over that pad and closes on it from above,
    # corner_chamfer because it turns down right beside it. Measured limits:
    #
    #     corner_chamfer    50     150     300     380
    #     max exit_run    1038    1038     949     869
    #     bar left         162      62       1       1
    #
    # Every one of these is asserted against the real pad positions at build time, so a
    # value that does not fit fails with the clearance it actually got -- there is no need
    # to work off this table, it is here to save a round trip.
    ref_wrap_exit_run: float = 1000
    # The turn down into the corridor, at 45 deg. Kept small: it buys almost no visible
    # diagonal and eats the budget the exit run wants.
    ref_wrap_corner_chamfer: float = 50
    ref_wrap_pad_chamfer: float = 10     # into the pad; self-clamps to half the 280 um run

    # --- insertion hook on the reference fiber ---
    # The hook's SHAPE and its etch hole live together in a named spec (hooks/), not here --
    # `hook` picks one ('teardrop_clap' or the archived 'legacy_barb'). It carries the only
    # etch hole on the probe, which is the datum `lengths.solve_lengths` drives `hook_drop`
    # against, so the two cannot be allowed to drift apart in separate config knobs.
    hook_on_ref_fiber: bool = True
    hook: str = "teardrop_clap"
    hook_scale: float = 1.0
    hook_drop: float = 200
    hook_etch_margin: float = 8.0   # um of polyimide that must survive around the etch hole

    # --- design ID marking, written into the GOLD (Metal) layer ---
    # Which design a piece of wafer is, readable under the scope. Stamped twice, both
    # upright, both in Metal, neither mirrored: beside the reference's bottom contact band
    # (the open wedge the recording fibers leave clear as they fan out from the hook --
    # etched carrier, not the finished probe) and centred above the solder pads, sized to
    # fill that band (on the device, survives). Set by batch.py from the DesignSpec; empty
    # means no marking (a bare BundleConfig has no design to name).
    label: str = ""
    label_tip_height: float = 40     # font size beside the reference's bottom contact band
    #font size above the solder-pad field.
    label_pad_height: float = 325
    label_gap: float = 40            # clear space between a marking and what it sits next to
    #Explicit override for the pad-side stamp's position -- None picks the default: centred
    #on the shank (x = 0), with the TOP edge label_gap under the polyimide edge, so growing
    #label_pad_height extends the label DOWNWARD toward the pads. Set either to place it by
    #hand instead; there is no fit check any more, so an oversized or off-target label can
    #run into the pads or off the polyimide without the build complaining.
    #
    #label_pad_x is the stamp's CENTRE, in um: negative moves it left, 1:1. At the shipped
    #height of 325 a label like "U1.6C00" spans +/-868, against a polyimide edge at +/-1425,
    #so there is ~557 um of travel either way before the text leaves the block -- less for a
    #longer string. NOTE the stamp is drawn MIRRORED (the device is read from the opposite
    #face), so left here is the viewer's right on the finished probe.
    label_pad_x: Optional[float] = -150
    label_pad_y: Optional[float] = None

    # --- bond pads ---
    pad_pitch: float = 300         # vertical stacking pitch within a column
    pad_diam: float = 220          # the GOLD pad; the opening in the polyimide is 200
    # The solder pads are opened by the SAME etch that frees the device, so each one is an
    # island in `Etching` too -- a circle concentric with the pad and inset this far from
    # its rim, leaving a polyimide collar over the pad edge that absorbs mask misalignment
    # (same logic as `l_contact` < the electrode pad).
    pad_opening_inset: float = 10   # -> a 200 um opening in a 220 um pad
    # Solder-pad teardrop: the fillet radius blending the incoming route into the pad, the
    # `metal_pad_fillet_radius` of the connector end. It sets how LONG the flare is, not how
    # wide -- the fillets land sqrt((pad_r+R)^2 - (tw/2+R)^2) from the pad centre, so a big
    # R reaches further back up the diagonal toward the neighbouring pads.
    #
    # THERE IS A CEILING ON IT, and it is nearer than it looks. The two lowest connected
    # pads in a column are fed by adjacent lanes whose diagonals run PARALLEL, one
    # `bundle_pitch` apart, so the flare eats straight into that 25 um centre-to-centre and
    # nothing else has to move for the two to meet. Measured on the 64-channel build: the
    # design's minimum metal gap of 15 um (the bundle's own lane gap) survives untouched to
    # R = 295, is down to 14.4 at 300, 2.5 at 350, and at 400 the two nets SHORT. 275 is a
    # deliberately pronounced flare with ~20 um of R left in hand; treat 295 as the wall.
    # `test_pad_teardrops_keep_the_metal_spacing` holds this, so the wall is checked, not
    # remembered -- raise this and that test tells you what it costs.
    pad_teardrop_fillet_radius: float = 275
    pad_row_pitch: float = 1950    # between the two columns -> columns at x = +/-975
    # RIBBON -- the run from the SHOULDER (where the fibers end and the fanout begins, i.e.
    # wire_top) up to the first solder pad. The connector end is fixed geometry, so this is
    # what stays constant when the fiber gets longer or shorter: `pad_first_y` is DERIVED
    # from it rather than set directly, so changing `bottom_elec` slides the whole
    # fanout+pad assembly instead of stretching the ribbon. The default reproduces the
    # historical pad_first_y = 15800 exactly (l_max 11993.5 + 3806.5).
    ribbon_length: float = 3806.5
    n_pads_per_column: int = 33    # 1 REF/GND + 32 connected -> 66 total, 64 connected

    # --- polyimide body / negative ---
    # THE SOLDER-PAD AREA (the wide block over the two pad columns). The drawing dimensions
    # it off the "solder pad area start" -- the ledge where the fanout stops flaring and the
    # block's parallel sides begin, i.e. `y_curve` in build_polyimide_fanout_body.
    polyimide_width: float = 2850       # across the block, the drawing's 2.85 mm
    # Alignment tabs: one half-disc standing proud of each side of the block. Both are
    # aligned by their upper END -- the drawing dimensions that, not their centres -- and
    # the arc runs DOWNWARD from there, so the two sides can be (and are) different
    # lengths. Each is `length` along the edge and half that deep.
    pad_block_tab_top: float = 3500       # upper end of both tabs, above the pad-area start
    # They are the device's ORIENTATION INDICATOR -- unequal on purpose, so that which way
    # round a released probe is lying reads at a glance -- so which side is long is not a
    # free choice. The BIG tab marks the edge AWAY from the reference, and the two therefore
    # move together: both were mirrored when the reference moved to the right column
    # (shapes.REF_PAD), leaving the big tab on the left. Flipping one without the other
    # makes the indicator point at the wrong column, which is the one thing it exists to say.
    pad_block_tab_len_left: float = 600   # the drawing's 0.6 mm
    pad_block_tab_len_right: float = 300  # the drawing's 0.3 mm
    polyimide_bundle_margin: float = 0
    polyimide_fan_hw: float = 950
    polyimide_fan_height: float = 2000
    polyimide_curve_offset: float = 550
    # The shank -> solder-pad-block transition: an S of two tangent arcs, vertical at both
    # ends, drawn over THIS much height and landing on the block at the pad-area start. It is
    # an aim, not a promise -- it is clamped to whatever straight stem the design's ribbon
    # actually leaves between the top of the fan-out and that landing. Taller is gentler: at
    # 3000 um the steepest tangent is ~18 deg off vertical, at 300 it is ~115 deg, i.e. a
    # shelf. It replaced a fillet-ledge-fillet step that left a literal 175 um horizontal
    # shelf on each side.
    polyimide_curve_height: float = 3000
    polyimide_curve_min_stem: float = 50    # straight stem kept below the S when clamping
    polyimide_join_r: float = 5
    polyimide_negative_margin: float = 80
    # Clearance the TIP's release moat is drawn to, and only that -- everything at the tip
    # has to be ringed by etched negative or it stays welded to the polyimide sheet, but it
    # does not need the body's 200 um to do it. At 200 the moat around the hook head grew
    # into a nose wide enough to swallow the spear's point; this keeps the silhouette.
    polyimide_release_margin: float = 20

    # --- optional reference-DXF electrode source (None = parametric grid) ---
    ref_dxf: Optional[str] = None
    ref_layer: str = 'electrode'

    # --- output ---
    out_dxf: str = f"{_OUT_DIR}/electrode_bundle.dxf"

    # --- derived helpers ---
    @property
    def wire_hw(self) -> float:
        """Default polyimide-trace half-width."""
        return self.wire_width / 2 + self.padding

    @property
    def wide_wire_hw(self) -> float:
        """The REFERENCE fiber's polyimide half-width (= 2*(wire_width/2 + padding)).

        The centre fiber is widened because it carries the insertion hook, and it is the
        reference -- no recording channel is ever widened. Its two neighbours therefore sit
        29 um out rather than delta_x = 24.
        """
        return 2 * (self.wire_width / 2 + self.padding)

    @property
    def fan_neck_tw(self) -> float:
        """Neck/fan trace width at the electrode (narrow) end."""
        return self.wire_width

    @property
    def l_max(self) -> float:
        return self.bottom_elec - self.l / 2

    @property
    def pad_first_y(self) -> float:
        """y of the bottom (REF/GND) pad = the shoulder plus the ribbon run.

        Derived, not a knob -- set `ribbon_length` instead. Uses `l_max`, the PARAMETRIC
        shoulder; under `ref_dxf` build_bundle can raise wire_top above l_max, and the
        ribbon then runs short by that difference.
        """
        return self.l_max + self.ribbon_length


# The 16 IONP barcodes are FROZEN DATA in `ionp_patterns.IONP_PATTERNS`, indexed by pattern
# id -- normalized 0 -> 1 along the ELECTRODE ARRAY (not the whole shank): 0 = the tip, where
# the deepest electrode sits; 1 = `id_top_margin` um above the SHALLOWEST electrode. Above 1
# there are no wells.
# Stripe positions and heights used to be grown per-design from the real electrode positions;
# they are fixed now, so one pattern id means one normalized barcode on every probe that
# carries it. The cost is that a pattern does not fit every probe -- bands scale with the
# array, so a short array shrinks stripes and gaps below the fab/MRI floors. That is checked,
# loudly, by `ionp.check_design_rules`.
# Two always-on anchors bracket 7 DATA slots on a shared grid, and a pattern lights a subset
# of them. The slot count a design can light is set by its array length: the 1.6 mm array
# holds four stripes in total, so its four ids are one-hot on a single slot, while the 8 mm
# array can light adjacent ones. `ionp_patterns.DESIGN_IDS` maps design -> its four ids.
# There is no closed-form code behind the ids any more (the old Hamming[7,4,3] generator
# needed 7 lightable slots, which only the two longest designs have) -- the bank IS the
# definition, and `ionp_bank_solve.py` re-derives it.


@dataclass
class IonpConfig:
    """IONP well pattern knobs (gen_ionp_bundle CONFIG + PATTERN + design rules)."""
    dxf_file: Optional[str] = None   # None -> per-ID f"{_OUT_DIR}/electrode_bundle_C{NN}.dxf"
    el_layer: str = 'Electrodes'                          # where the 64 contacts live
    ionp_layer: str = 'IONP'                      # layer the wells are written to
    # vertical extent of the well region (straight-shank only; fanout starts ~4990)
    y_bottom: float = 0.0
    y_top: float = 4990.0
    # well geometry (um)
    ionp_well_d: float = 1.5
    ionp_well_distance: float = 5.0
    ionp_firstwell_distance: float = 15.0
    well_resolution: int = 12
    n_els: int = 64
    pattern_i: int = 0                # which pattern in ionp_patterns.IONP_PATTERNS to stamp
    json_file: Optional[str] = None   # None -> per-ID f"{_OUT_DIR}/64ch_C{NN}_info.json"
    # None -> look `pattern_i` up in the bank. An explicit Nx2 array of normalized bands
    # overrides it, for one-off exploration; it goes through the same validation.
    pattern: Optional[np.ndarray] = None
    # um; drop wells within this of a contact ALONG ITS OWN FIBER. Not a radius in the
    # plane: this exceeds delta_x (24), so a circle would also clear the neighbouring
    # fibers' columns and leave gaps in stripes on fibers with no contact there.
    keepout_radius: float = 30.0
    # ID region top: sits `id_top_margin` um above the HIGHEST electrode contact (so the
    # barcode lands just above the electrodes, not up by the fan). None -> use y_top (fan).
    id_top_margin: Optional[float] = 120.0
    # design-rule limits, enforced by ionp.check_design_rules. min_n_wells is the HARD MRI
    # floor; the bank's band heights were sized so every stripe clears it on every shipped
    # design. min_strip_distance is what binds first on a short array.
    min_n_wells: int = 700
    max_n_wells: int = 10_000
    min_strip_distance: float = 300   # um

    def __post_init__(self):
        if self.dxf_file is None:
            self.dxf_file = f"{_OUT_DIR}/electrode_bundle_C{self.pattern_i:02d}.dxf"
        if self.json_file is None:
            self.json_file = f"{_OUT_DIR}/64ch_C{self.pattern_i:02d}_info.json"


@dataclass
class MappingConfig:
    """Electrode-channel -> flex-pad mapping knobs (build_electrode_flex_mapping)."""
    # 33 ints each, left->right as in 64ch_flex.PNG; entry 0 = REF/GND (65 top / 66 bottom).
    top_row: List[int] = field(default_factory=lambda: [
        65, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 23, 25, 27, 29, 31,
        32, 30, 28, 26, 24, 22, 20, 18, 16, 14, 12, 10, 8, 6, 4, 2])
    bottom_row: List[int] = field(default_factory=lambda: [
        66, 34, 36, 38, 40, 42, 44, 46, 48, 50, 52, 54, 56, 58, 60, 62, 64,
        63, 61, 59, 57, 55, 53, 51, 49, 47, 45, 43, 41, 39, 37, 35, 33])
    n_channels: int = 64
    n_per_column: int = 33   # 1 REF/GND + 32 connected
    out_file: str = f"{_OUT_DIR}/electrode_to_flex_mapping.json"
