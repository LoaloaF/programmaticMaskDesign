r"""The probe designs to build, one DesignSpec each. `batch.py` builds every entry.

ALONG THE SHANK -- what each length parameter measures
======================================================
Tip at the LEFT, connector at the RIGHT. Every bar below is measured along the shank axis.
Numbers are design "C1", in um. Parameter names are exactly the DesignSpec fields;
(parenthesised) quantities are derived, not inputs.

        loop           deepest        shallowest      shoulder          first pad
         |               |               |               |                  |
         |---------------|---------------|---------------|------------------|
         |  loop_offset  |  sites.span   |  free fiber   |    (ribbon)      |
         |      445      |     5790      |     2765      |     5406.5       |
         |               |               |               |                  |
         |<------------ fiber_length = 9000 ------------>|                  |
         |               |               |               |                  |
         |<------------------- overall_length = 14406.5 ------------------->|

    loop_offset  +  sites.span  +  (free fiber)  =  fiber_length
        445      +     5790     +      2765      =      9000

                        fiber_length  +  (ribbon)  =  overall_length
                            9000      +   5406.5   =     14406.5

Only `loop_offset`, `sites.span`, `fiber_length` and `overall_length` are inputs. "free
fiber" is whatever is left above the shallowest site; "(ribbon)" is whatever is left above
the shoulder. So lengthening a design's fiber shortens its ribbon by the same amount and
the device still ends at `overall_length` -- which is the point: probes with different
fibers still meet the PCB at the same place. `overall_length=None` pins the ribbon at the
connector's default instead and lets the total float.

THE SHOULDER IS NOT A STRAIGHT CUT
==================================
"shoulder" above is one point only for the CENTRE fiber. Each fiber is freed from the
polyimide block at its own height, higher the further out it sits, so it has more free
length to swing into the bundle with:

      shoulder edge, looking across the shank (C1, exaggerated):

        ch63  __                                              __  ch62
                \__                                        __/
                   \___                                ___/          282 um of stagger
                       \_____                    _____/              between the outermost
                             \________     _____/                    fiber and ch0
                                      \___/
                                       ch0   <- lowest, barely turns

The rise for channel i is `BUNDLE_FOLD.offsets(x_i)` -- the arc length that fiber spends
travelling sideways into the bundle. `fiber_length` is measured to the TOP of that stagger
(the block's flat lower edge), which is where the ribbon actually begins.

Note what the stagger does not do: `bundling.offsets` is a function of lateral x alone, so
the model still puts the bundled contact at `drawn_y + offset` -- the implanted array comes
out ~282 um longer than the drawn one. `sites` are DRAWN positions. The output JSON carries
both, per channel, so the difference stays visible rather than assumed away.

ACROSS THE SHANK -- where each channel sits
===========================================
Looking along the axis. All n_channels sit in ONE row across x, alternating sides with the
index growing OUTWARD from the centre: ch0 takes the centre slot (it is the one carrying
the loop), odd channels go to +x, even channels to -x.

              -x  <---------------- x = 0 ----------------->  +x

                   ...  ch4   ch2   ch0 | ch1   ch3   ch5  ...
                         #     #     #  |  #     #     #
                         |<--->|        |
                         delta_x = 24   | ch0 is widened to carry the loop, so its two
                                        | neighbours sit 29 um out, not 24

     solder pads:  [o] [o] ... at x = -975      and  [o] [o] ... at x = +975
                   (pad_row_pitch/2)                 33 per column: 32 wired + 1 REF/GND

THE FIELDS
----------
    n_channels      how many recording sites
    sites           where they sit -- Uniform(span=...) or Segments([(ch, ch, pitch), ...])
    fiber_length    loop -> shoulder
    loop_offset     loop -> deepest site
    overall_length  loop -> first solder pad; None keeps the connector's default ribbon
    ionp_pattern_id which barcode from the ionp_patterns bank, or None for no barcode
    doc_id          the source doc's own row name, for provenance

`fiber_length` and `loop_offset` are solved for numerically (lengths.py); `sites` resolves
to delta_y/delta_y_overrides (spacing.py).

`ionp_pattern_id` indexes `ionp_patterns.IONP_PATTERNS`, the frozen bank of barcodes. One
spec carries ONE pattern, so a geometry that ships in several barcode variants is several
entries here. You do not write them out: `_BASE_DESIGNS` holds one entry per geometry and
`DESIGNS` expands it against `ionp_patterns.ids_for_design`, four variants each. The
geometry stays single-source and only the barcode differs.

Not every pattern fits every design: bands are normalized along the electrode array, so a
short array shrinks every stripe and every gap. An id is legal on the design it was solved
for and on every longer one, never on a shorter one. A spec whose pattern does not fit fails
the build loudly. `python3 -m electrode_bundle.ionp_patterns` prints the fit matrix.

THE DESIGNS BELOW
-----------------
Four uniform-pitch 64-channel arrays on the same 10 mm fiber, differing only in how far the
contacts are spread: 1.664, 2.56, 4 and 8 mm. They all end at the same connector, so the
ribbon absorbs nothing here -- `fiber_length` is identical and only `sites` changes.

The design-doc rows this file used to carry (C1-C4, H1, H2) are in git history, not here.

"""
from dataclasses import dataclass, replace
from typing import Optional, Union

from .bundling import TwoArcFold
from .config import variant_tag
from .ionp_patterns import IONP_PATTERNS, ids_for_design
from .spacing import Segments, Uniform


@dataclass
class DesignSpec:
    name: str                                 # short handle; names the output files
    n_channels: int
    sites: Union[Uniform, Segments]
    fiber_length: float                        # um, loop -> shoulder
    loop_offset: float                         # um, loop -> deepest site
    #um, loop -> first solder pad, i.e. the whole device. The ribbon (shoulder -> first
    #pad) is whatever is left over, so probes with different fiber lengths still end at the
    #same connector. None = keep the connector's default ribbon and let the total float.
    overall_length: Optional[float] = None
    #which barcode from the ionp_patterns bank to stamp; None = no barcode at all. One
    #pattern per spec -- two barcodes on one geometry means two entries in DESIGNS.
    ionp_pattern_id: Optional[int] = None
    doc_id: str = ""                           # the source doc's own row name

    def __post_init__(self):
        if self.ionp_pattern_id is not None and self.ionp_pattern_id not in IONP_PATTERNS:
            raise ValueError(
                f"{self.name}: ionp_pattern_id {self.ionp_pattern_id} is not in the bank "
                f"{sorted(IONP_PATTERNS)}; add it to ionp_patterns.IONP_PATTERNS first")


#How the flat wafer design folds into the bundle. Shared by every design -- this is a
#property of how the bundle is MADE, not of an individual probe.
#!! `max_theta_deg` is NOT calibrated against a measured bundled device. It dominates the
#result (~60 um per 10 deg), and it now drives DRAWN GEOMETRY, not just reported numbers:
#`config_for` staggers each fiber's shoulder by this model's per-channel offset, so a wrong
#value ships a wrong wafer. The output JSON records the model, its parameters and
#`calibrated: false` next to the numbers. NoFold() gives a flat shoulder and no elongation.
BUNDLE_FOLD = TwoArcFold(max_theta_deg=50.0, bundle_ratio=0.07)


#Every design in this family ends at the same connector position, so the ribbon absorbs the
#differing fiber lengths instead of the whole device changing length. Value = the longest
#fiber here (H1/H2, 10600) + the connector's default 3806.5 um ribbon, so those two keep
#exactly the default ribbon and every shorter probe gets a longer one.
FAMILY_OVERALL_LENGTH = 30_000

#One entry per GEOMETRY. The barcode variants are generated below -- do not put
#`ionp_pattern_id` here.
_BASE_DESIGNS = [
    DesignSpec(name="U1.6", n_channels=64, sites=Uniform(span=1664),
               fiber_length=10_000 - 3000, loop_offset=500,
               overall_length=FAMILY_OVERALL_LENGTH - 3000,
               ),
    DesignSpec(name="U2.5", n_channels=64, sites=Uniform(span=2560),
               fiber_length=10_000, loop_offset=250,
               overall_length=FAMILY_OVERALL_LENGTH,
               ),
    DesignSpec(name="U4", n_channels=64, sites=Uniform(span=4000),
               fiber_length=10_000, loop_offset=500,
               overall_length=FAMILY_OVERALL_LENGTH,
               ),
    DesignSpec(name="U8", n_channels=64, sites=Uniform(span=8000),
               fiber_length=10_000 + 2000, loop_offset=500,
               overall_length=FAMILY_OVERALL_LENGTH - 2000,
               ),
]

#Each geometry ships in four barcode variants, so four probes of the same design are still
#told apart in MRI. `ids_for_design` holds the assignment because it is the BANK that knows
#which ids a given array length can carry -- ids 0-3 are the only ones the 1.6 mm array
#fits, and it is at capacity with them. Four entries per geometry is exactly the shape
#`_check_variants_unique` expects: same `name`, different `ionp_pattern_id`.
DESIGNS = [replace(base, ionp_pattern_id=pid)
           for base in _BASE_DESIGNS
           for pid in ids_for_design(base.name)]


def _check_variants_unique(designs):
    """Artefacts are named off `variant_tag(name, ionp_pattern_id)`, so that pair is the key.

    Two entries MAY share a `name`: that is how one geometry carries several barcodes. They
    then write the same barcode-free DXF and the same electrode record (identical content,
    so the repeat is harmless) and differ only in their barcoded artefacts. What must never
    repeat is the pair, which would silently overwrite.
    """
    seen = set()
    for spec in designs:
        key = variant_tag(spec.name, spec.ionp_pattern_id)
        if key in seen:
            raise ValueError(f"duplicate design variant {key!r} -- two entries with the "
                             f"same name need different ionp_pattern_id values")
        seen.add(key)


_check_variants_unique(DESIGNS)
