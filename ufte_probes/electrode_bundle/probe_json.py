"""The per-design record: where every channel physically is, and what it connects to.

One JSON per design, `<name>_electrodes.json`. It answers the two questions the DXF cannot:

  * where is channel i on the WAFER (as drawn / as fabricated flat), and
  * where does it end up once the bundle is folded (what an implanted probe actually has)

Those differ -- folding is not a rigid shift, it stretches the array (see bundling.py) --
so both are recorded per channel rather than one being derived downstream from the other.

The bundling model and its parameters are recorded ALONGSIDE the numbers, with an explicit
`calibrated` flag, because the bundled positions are only as good as `max_theta_deg`, which
is a property of the fabrication process and not derivable from the drawing.

Frame: y as drawn in the DXF, the same numbers the geometry uses. `loop_y_um` is recorded
so the frame is unambiguous, and each channel also carries `depth_from_loop_um` -- the
physically meaningful one, how far below the insertion loop the contact sits.
"""
from dataclasses import asdict, is_dataclass
from typing import Optional

from .bundling import Fold, bundled_positions
from .lengths import loop_y, measure, measure_overall


def build_record(spec, cfg, result, fold: Fold, flex_mapping: Optional[dict] = None,
                 fiber_x=None) -> dict:
    """Everything known about one built design, as a plain JSON-able dict.

    `fiber_x` is the lateral position of each FIBER, which is what the fold cost depends on.
    It is not quite the contact centroid (`electrode_locs[:, 0]`) once the contact is round,
    so pass the same array the shoulder stagger used; falling back to the centroid costs ~0.1 um.
    """
    x = result.electrode_locs[:, 0]
    y = result.electrode_locs[:, 1]
    lateral, y_bundled = bundled_positions(x if fiber_x is None else fiber_x, y, fold)
    span, fiber, loop_offset = measure(result)
    ly = loop_y(result)

    pads = (flex_mapping or {}).get("mapping", {})

    return {
        "design": spec.name,
        "doc_id": spec.doc_id,
        "n_channels": int(result.num_channels),
        "units": "um",
        "lengths": {
            "fiber_length": round(fiber, 3),
            "loop_offset": round(loop_offset, 3),
            "ribbon_length": round(cfg.ribbon_length, 3),
            "overall_length": round(measure_overall(result, cfg), 3),
            "site_span_wafer": round(span, 3),
            "site_span_bundled": round(float(y_bundled.max() - y_bundled.min()), 3),
        },
        "bundling": {
            "model": type(fold).__name__,
            "params": asdict(fold) if is_dataclass(fold) else {},
            #the bundled numbers inherit this assumption -- say so in the file itself
            "calibrated": False,
            "note": "max_theta_deg is not calibrated against a measured bundled device; "
                    "it dominates the bundled positions (~60 um per 10 deg).",
        },
        "loop_y_um": round(ly, 3),
        "channels": [
            {
                "channel": i,
                "flex_pad": pads.get(str(i)),
                "wafer": {"x": round(float(x[i]), 3), "y": round(float(y[i]), 3)},
                "bundled": {
                    "lateral": round(float(lateral[i]), 3),
                    "y": round(float(y_bundled[i]), 3),
                    "depth_from_loop_um": round(float(y_bundled[i] - ly), 3),
                },
            }
            for i in range(int(result.num_channels))
        ],
    }
