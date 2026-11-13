r"""Today's hook: a bulbous TEARDROP head with 6 rounded CLAPS, fully parametric.

Drawn from `changes_plan/new_hook.png`. Right half, attach point at the top:

        (stem_hw)              |<->|  stem_hw          y_top ---- attach, = the trace width
                               |   |
        flare_len   S-curve    |    \                  two tangent arcs, vertical at BOTH
                               |     \                 ends, so the trace flows into the
                               |      |                bulb with no visible corner
        side_len    straight   |      |
                               |      |     ---o---    claps: 3 a side, angled up and out,
        bulb_r      round tip  |     /       /  /      each a `clap_width` arm ending in a
                               |____/       /  /       circle of `clap_tip_d`
                                            \ (etch ellipse sits in the bulb)

WHY PARAMETRIC AND NOT ANOTHER POINT LIST. The user asked for exactly one dimension to
change -- claps 2 um -> 5 um -- which a hardcoded template cannot express. Everything the
drawing dimensions is a field here, so the next such request is a number, not a redraw.

WHERE THE NUMBERS COME FROM. Every one is a ROUNDED reading of the reference drawing --
its callouts are CAD readouts of a hand-drawn shape (2.1257413, 7.1331745, 5.885413,
45.029847), not intended dimensions, and a mask made to seven decimals of someone's mouse
is worse than one made to whole microns. What each callout was taken to mean, and what it
became here:

    clap arm width      2.1257413 -> 5   the one the user changed outright
    clap cap diameter   7.1331745 -> 7   the round end the arm finishes in
    clap slot           5.885413  -> 6   clear gap between neighbouring arms, measured
                                         across them. Held CONSTANT as the arms widen, so
                                         wider claps spread out rather than closing the
                                         etch slots up.
    etch ellipse        35 x 45.029847 -> 35 x 45

The flare, bulb, clap angle and clap reach carry no callouts at all: they were measured off
the drawing at ~5.7 px/um and then rounded, so treat them as the drawn shape to +/- 1 um.

`clap_fillet_r` is the one dimension that is NOT from the drawing. The drawing joins each
clap to the bulb, and to its own cap, at a crease; a clap is a flap of 12 um polyimide that
the insertion tool pulls on, and a crease is where it tears. Every inner corner the claps
make is therefore rounded (see `polygon`). It costs nothing in the etch: the slots keep
their full `clap_gap` along their length and only their closed ends round off.
"""
from dataclasses import dataclass

import numpy as np

from ..geometry import _arc, create_polygon_circle, s_transition


def _s_flare(x0, y0, x1, y1, n=24):
    """The trace -> bulb flare: two tangent arcs, vertical at both ends, running DOWNWARD.

    Thin wrapper on `geometry.s_transition` -- the same S the polyimide body uses to flare
    into the solder-pad block. Tangent-continuous is the whole point here: a chamfer at the
    top of the bulb reads as a crease in the polyimide.
    """
    if y0 - y1 <= 0:
        raise ValueError("_s_flare runs downward: y1 must be below y0")
    return s_transition(x0, y0, x1, y1, n)


@dataclass(frozen=True)
class TeardropClapHook:
    """The teardrop hook. All lengths in um, at scale 1; `scale` multiplies every one."""
    # --- head ---
    flare_len: float = 65.0        # trace -> bulb, over which the S-curve widens
    bulb_r: float = 30.0           # half-width of the bulb AND the radius of its round tip
    side_len: float = 30.0         # straight side between the flare and the round tip
    # --- claps ---
    n_claps_per_side: int = 3
    clap_width: float = 5.0        # was 2.1257413 in the drawing; widened on request
    clap_tip_d: float = 7.0        # diameter of the round cap
    clap_gap: float = 6.0          # clear slot between neighbouring arms
    clap_angle_deg: float = 45.0   # arm heading above horizontal, pointing outward
    clap_tip_x: float = 48.0       # |x| of the cap centres (all claps reach equally far)
    clap_top_y: float = -50.0      # y of the TOP cap centre, below the attach point
    clap_root_x: float = 22.0      # |x| where the arm ends inside the bulb (it is unioned
                                   # in, so this only has to be comfortably inside)
    clap_fillet_r: float = 2.0     # radius rounding every INNER corner the claps make: where
                                   # an arm leaves the bulb, and the notch where it meets its
                                   # own round cap. A clap is a flap of polyimide that gets
                                   # pulled on; a sharp re-entrant corner is where such a
                                   # flap tears off, so no junction here is left as a crease.
                                   # It must stay under HALF `clap_gap` or the rounding
                                   # closes the etch slots between the arms outright.
    # --- etch hole ---
    etch_rx: float = 17.5          # the drawing's 35 um across
    etch_ry: float = 22.5          # the drawing's 45.029847 um tall, rounded to 45
    etch_dy: float = -85.0         # below the attach point, i.e. 10 um above the bulb
                                   # centre, where the head is at its widest

    def _profile(self, stem_hw, scale, stem_top=0.0):
        """Right half of the head, top -> bottom, in hook-local um (attach point at 0, 0).

        `stem_top` extends the straight stem above the flare -- that is the `drop` riser,
        drawn as part of the outline so the ring stays simple and valid.
        """
        R = self.bulb_r * scale
        if stem_hw >= R:
            raise ValueError(f"hook stem half-width {stem_hw} must be < bulb_r {R}")
        y_flare = -self.flare_len * scale
        y_bulb = y_flare - self.side_len * scale
        fx, fy = _s_flare(stem_hw, 0.0, R, y_flare)
        return [stem_hw] + fx + [R, R], [stem_top] + fy + [y_flare, y_bulb]

    def _claps(self, scale):
        """One (x, y) polygon per clap, in hook-local um, both sides."""
        th = np.radians(self.clap_angle_deg)
        hw = 0.5 * self.clap_width * scale
        cap_r = 0.5 * self.clap_tip_d * scale
        #arms are parallel, so stepping them by the perpendicular pitch keeps the etch slot
        #between them at exactly clap_gap however wide the arms get
        pitch = (self.clap_width + self.clap_gap) * scale / np.cos(th)
        out = []
        for side in (-1, 1):
            for k in range(self.n_claps_per_side):
                tip = np.array([side * self.clap_tip_x * scale,
                                self.clap_top_y * scale - k * pitch])
                #inward and downward, to a root safely buried in the bulb
                root = np.array([side * self.clap_root_x * scale,
                                 tip[1] - (self.clap_tip_x - self.clap_root_x) * scale * np.tan(th)])
                d = (root - tip) / np.linalg.norm(root - tip)
                n = np.array([-d[1], d[0]]) * hw
                arm = np.array([tip + n, root + n, root - n, tip - n])
                out.append((arm[:, 0], arm[:, 1]))
                cap = create_polygon_circle(tip[0], tip[1], cap_r, 32)
                out.append((cap[:-1, 0], cap[:-1, 1]))
        return out

    def _min_clap_gap(self, scale):
        """The narrowest clear gap anywhere in the clap field, in um.

        Three gaps compete, and which one wins depends on how the arm width compares to the
        cap diameter -- so it is computed, not assumed:

            arm to arm      `clap_gap`, by construction (`_claps` steps the arms by
                            width + gap measured across them)
            cap to arm      a cap overhangs its own arm by (clap_tip_d - clap_width)/2, and
                            that overhang eats into the slot beside it
            cap to cap      the pitch along the column, less one cap diameter

        It caps `clap_fillet_r`: a closing bridges anything narrower than twice its radius,
        and bridging any of these fuses the claps into one flap.
        """
        th = np.radians(self.clap_angle_deg)
        pitch = (self.clap_width + self.clap_gap) / np.cos(th)
        return scale * min(self.clap_gap,
                           self.clap_gap - 0.5 * (self.clap_tip_d - self.clap_width),
                           pitch - self.clap_tip_d)

    def polygon(self, cx, y_top, stem_hw=10.0, scale=1.0, drop=0.0):
        """Closed (x, y) outline, stem centred at (cx, y_top), head hanging toward -y."""
        from shapely.geometry import Polygon
        from shapely.ops import unary_union

        #`drop` lowers the head and lengthens the straight stem back up to y_top, so the
        #hook reaches deeper without changing shape. A NEGATIVE drop needs no riser: it just
        #lifts the head into the trace, which the polyimide union absorbs (see the package
        #docstring), which is how the solver reaches loop offsets below this hook's floor.
        px, py = self._profile(stem_hw, scale, stem_top=max(drop, 0.0))
        R = self.bulb_r * scale
        bx, by = _arc(0.0, py[-1], R, 0.0, -np.pi, 48)          # the round tip, right -> left
        #right side down, round the tip, mirrored left side back up
        x = px + bx + [-v for v in reversed(px)]
        y = py + by + list(reversed(py))

        head = Polygon(zip(x, y)).buffer(0)
        head = unary_union([head] + [Polygon(zip(ax, ay)).buffer(0) for ax, ay in self._claps(scale)])
        #ROUND THE INNER CORNERS the union just made. Arms meet the bulb in a sharp V and
        #meet their own caps in a notch, and both are creases in a flap that the insertion
        #tool pulls on. A morphological closing (dilate R, erode R, both round) fillets every
        #concave corner at radius R and leaves every convex one -- the caps, the bulb tip --
        #exactly as drawn; it is the same trick `shapes.merge_polyimide_with_fillets` uses on
        #the trace<->body junctions. Closing also bridges any gap narrower than 2R, and the
        #gaps here are the etch slots between the arms, hence the cap on R.
        R = self.clap_fillet_r * scale
        if R > 0:
            gap = self._min_clap_gap(scale)
            assert 2 * R < gap, (
                f"clap_fillet_r {self.clap_fillet_r} is too big for this clap field: the "
                f"narrowest clear gap in it is {gap / scale:.2f} um, and rounding bridges "
                f"anything under twice the radius -- the claps would fuse into one flap. "
                f"Use under {0.5 * gap / scale:.2f}, or widen clap_gap")
            #unioned back with the original, never used on its own: the erode half of a
            #closing works on the DILATION'S POLYGON, whose arcs are chords, so it comes
            #back a hair inside the straight edges it should have restored exactly (~0.02 um
            #off the stem, which is the width the trace above has to meet). Closing may only
            #ADD material here, and the union says exactly that.
            head = unary_union([head, head.buffer(R, join_style=1, quad_segs=32)
                                          .buffer(-R, join_style=1, quad_segs=32)])
        if head.geom_type == "MultiPolygon":                    # defensive: keep the head
            head = max(head.geoms, key=lambda g: g.area)
        hx, hy = (np.asarray(a) for a in head.exterior.coords.xy)
        return hx + cx, hy + (y_top - drop)

    def etch(self, cx, y_top, stem_hw=10.0, scale=1.0, drop=0.0):
        """(ecx, ecy, rx, ry) of the etch ellipse: fixed to the head, so slope 1 in `drop`."""
        return (cx, y_top - drop + self.etch_dy * scale,
                self.etch_rx * scale, self.etch_ry * scale)


TEARDROP_CLAP = TeardropClapHook()
