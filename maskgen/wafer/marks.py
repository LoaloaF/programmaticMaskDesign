"""Alignment marks: give dark-field masks the INVERTED vernier. One implementation for every wafer.

The template (data/new_wafer_actually.GDS) ships each mark slot's vernier in one of two polarities:

    POSITIVE   129 separate bars (610 x 610 um, 4.9% filled)                 6/0, 8/0, 1/0
    INVERTED   ONE polygon with those 129 bars as holes (649.2 x 618.6 um)   3/0, 5/0

The first mask (metal, bright field) puts the physical marks on the wafer; every later mask is DARK
FIELD, where the drawn shapes are the openings. There the positive form comes out as 129 slits in a
chrome field and hides the metal scale it must be read against; the inverted form comes out as a clear
window with the scale in chrome. So every dark-field mask must carry the INVERTED form: it is copied
from the `stencil` layer's slot, registered on the `companion` layer (metal: an identical cluster sits
at the centre of every slot and is the feature already on the wafer when the later mask is aligned),
one band -- left / right of the wafer centre -- at a time. The transform is proven first on `selftest`,
a layer whose inverted mark the template already carries: rebuilding it from the stencil must
reproduce it exactly, or nothing is written.

Used by export.py (nested wafers, the layer lives in a pya cell) and exact_io.invert_marks (rebuild.py,
the layer is a polygon list). KLayout's Python only.
"""
import pya


def invert_marks(get, put, layers, stencil, companion, selftest, keep_radius_mm, dbu,
                 vernier_w_um=(550.0, 700.0), band_y_um=1000.0):
    """`get(ld)` -> the template's shapes on (layer, datatype) `ld` as a pya.Region (empty if absent);
    `put(ld, region)` replaces them. Each layer in `layers` gets the inverted vernier in its slot."""
    um = lambda v: int(round(v / dbu))

    def verniers(ld, xlo, xhi):
        """Every vernier-sized cluster of a layer inside one mark band, as (bbox, Region)."""
        reg = get(tuple(ld)) & pya.Region(pya.Box(um(xlo), -um(band_y_um), um(xhi), um(band_y_um)))
        reg.merge()
        grouped = reg.dup()
        grouped.size(um(40.0))
        grouped.merge()
        out = [(c.bbox(), reg & pya.Region(c.bbox())) for c in grouped.each()
               if vernier_w_um[0] < c.bbox().width() * dbu < vernier_w_um[1]]
        return sorted(out, key=lambda t: t[0].center().x)

    def nearest(comps, bbox):
        """The metal companion this mark belongs to -- slots are 700 um apart, so nearest wins."""
        return min(comps, key=lambda t: abs(t[0].center().x - bbox.center().x))[0]

    r = keep_radius_mm * 1000.0
    # The two bands are handled SEPARATELY: a band's stencil serves that band's slots, so whatever the two
    # ends do differently (they are not mirror images, and the mark sits a fraction off its companion by a
    # different amount on each side) never enters the transform.
    for xlo, xhi in ((-r, 0.0), (0.0, r)):
        comps = verniers(companion, xlo, xhi)
        stencils = verniers(stencil, xlo, xhi)
        if len(stencils) != 1 or not comps:
            raise SystemExit("band [%.1f, %.1f] mm: found %d stencil marks on %s and %d companions on %s"
                             % (xlo / 1000, xhi / 1000, len(stencils), stencil, len(comps), companion))
        sbb, sten = stencils[0]
        scomp = nearest(comps, sbb)

        def stencil_on(ld):
            """(slot bbox, the mark there now, the inverted mark for it). Writes nothing."""
            found = verniers(ld, xlo, xhi)
            if len(found) != 1:
                raise SystemExit("expected one vernier on %s in band [%.1f, %.1f] mm, found %d"
                                 % (ld, xlo / 1000, xhi / 1000, len(found)))
            bbox, present = found[0]
            comp = nearest(comps, bbox)
            made = sten.dup()
            made.transform(pya.Trans(comp.center().x - scomp.center().x, comp.center().y - scomp.center().y))
            return bbox, present, made

        # PROVE THE TRANSFORM BEFORE USING IT. If this ever fails, every copied mark below would be in the
        # wrong place and the wafer would be scrap.
        _, real, rebuilt = stencil_on(selftest)
        if not (rebuilt ^ real).is_empty():
            raise SystemExit("mark self-test FAILED on %s in band [%.1f, %.1f] mm: rebuilding it from %s "
                             "differs by %.4f um2 -- the companion registration is wrong, do NOT ship"
                             % (selftest, xlo / 1000, xhi / 1000, stencil, (rebuilt ^ real).area() * dbu * dbu))
        print("  marks [%+.1f, %+.1f] mm: self-test OK (%d/%d rebuilt from %d/%d exactly)"
              % (xlo / 1000, xhi / 1000, selftest[0], selftest[1], stencil[0], stencil[1]))
        for ld in layers:
            bbox, old, new = stencil_on(ld)
            put(tuple(ld), (get(tuple(ld)) - pya.Region(bbox)) + new)
            print("    %d/%d: positive vernier (%d polys, %.0f um^2) -> inverted (%d polys, %.0f um^2) at x = %+.4f mm"
                  % (ld[0], ld[1], old.count(), old.area() * dbu * dbu, new.count(), new.area() * dbu * dbu,
                     new.bbox().center().x * dbu / 1000))
