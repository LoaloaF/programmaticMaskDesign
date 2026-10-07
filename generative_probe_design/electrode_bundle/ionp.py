"""IONP (iron-oxide nanoparticle) well patterns on the electrode bundle.

For each of the 64 electrode contacts, a vertical column of small circular wells is laid
down the straight-shank region above the contact (from just above the contact up to where
the fanout begins ~4990um), keeping only the wells that fall inside the normalized stripe
bands in `cfg.pattern`. Wells go onto a new `IONP` layer.

`add_ionp_wells` takes the electrode contact locations in memory (from BundleResult), so in
the `all` pipeline there is no DXF round-trip. Standalone, `electrode_locs_from_dxf` reads
them back off an existing DXF's `Electrodes` layer instead.

The stripe bands themselves are NOT computed here -- they are frozen data in
`ionp_patterns.IONP_PATTERNS`, indexed by pattern id, so one id means one physical barcode
on every probe that carries it. This module only stamps a pattern onto a geometry and
CHECKS that it fits: `check_design_rules` raises when the design's electrode array is too
short to hold the pattern's stripes at the required spacing and well count.
"""
import json
from typing import Any, Dict, Tuple

import numpy as np

from . import dxf_io as io
from .config import IonpConfig
from .ionp_patterns import pattern_bands, validate_pattern


def filter_to_pattern(ylocs: np.ndarray, pattern: np.ndarray,
                      y_top: float, y_bottom: float) -> np.ndarray:
    """Keep only y positions inside the normalized stripe intervals."""
    yrange = y_top - y_bottom
    mask = np.zeros_like(ylocs, dtype=bool)
    for start, end in pattern:
        mask |= (ylocs >= start * yrange + y_bottom) & (ylocs <= end * yrange + y_bottom)
    return ylocs[mask]


def calc_ionp_well_locs(el_locs, pattern, y_top, y_bottom,
                        well_distance, first_well_distance) -> Tuple[np.ndarray, np.ndarray]:
    """One vertical column of wells above each electrode, clipped to the pattern."""
    xs, ys = [], []
    for el_x, el_y in el_locs:
        col_y = np.arange(el_y + first_well_distance, y_top, well_distance)
        col_y = filter_to_pattern(col_y, pattern, y_top, y_bottom)
        xs.append(np.full_like(col_y, el_x))
        ys.append(col_y)
    return np.concatenate(xs), np.concatenate(ys)


def _column_tolerance(el_locs: np.ndarray) -> float:
    """Half the gap between neighbouring fibers -- how close in x counts as "same column"."""
    xs = np.unique(np.asarray(el_locs, dtype=float)[:, 0])
    if len(xs) < 2:
        return np.inf
    return float(np.min(np.diff(xs))) / 2.0


def drop_wells_near_contacts(xs: np.ndarray, ys: np.ndarray,
                             el_locs: np.ndarray, radius: float
                             ) -> Tuple[np.ndarray, np.ndarray]:
    """Remove wells within `radius` um of a contact ON THE SAME FIBER. radius<=0 -> no-op.

    The keep-out is measured ALONG the fiber, not as a circle in the plane. Each well column
    sits on one fiber, and the fibers are separate structures once the bundle is folded, so a
    contact has no business clearing wells off its neighbours. Doing it as a Euclidean circle
    did exactly that: `keepout_radius` (30 um) exceeds `delta_x` (24 um), so every contact
    also punched a +-18 um hole in the columns either side of it -- visible as gaps in the
    stripes on fibers that carry no contact there at all.
    """
    if radius <= 0 or len(xs) == 0:
        return xs, ys
    el = np.asarray(el_locs, dtype=float)
    x_tol = _column_tolerance(el)
    keep = np.ones(len(xs), dtype=bool)
    for cx, cy in el:
        same_column = np.abs(xs - cx) < x_tol
        keep &= ~(same_column & (np.abs(ys - cy) <= radius))
    return xs[keep], ys[keep]


def effective_y_top(el_locs, cfg: IonpConfig) -> float:
    """Top of the ID region (norm 1.0). `id_top_margin` um above the highest contact, or
    `cfg.y_top` (the fan) when `id_top_margin is None`."""
    if cfg.id_top_margin is None:
        return cfg.y_top
    return float(np.asarray(el_locs, dtype=float)[:, 1].max()) + cfg.id_top_margin


class DesignRuleError(ValueError):
    """A pattern does not fit this geometry: stripes too close, or too few/many wells."""


def well_locs_for_pattern(el_locs, pattern, cfg: IonpConfig):
    """The well (x, y) this pattern puts on this geometry, keep-out already applied."""
    y_top = effective_y_top(el_locs, cfg)
    xs, ys = calc_ionp_well_locs(el_locs, pattern, y_top, cfg.y_bottom,
                                 cfg.ionp_well_distance, cfg.ionp_firstwell_distance)
    return drop_wells_near_contacts(xs, ys, el_locs, cfg.keepout_radius)


def resolve_pattern(cfg: IonpConfig) -> np.ndarray:
    """`cfg.pattern` if one was given explicitly, else the bank's `cfg.pattern_i`."""
    if cfg.pattern is not None:
        return validate_pattern(cfg.pattern, what="IonpConfig.pattern")
    return pattern_bands(cfg.pattern_i)


def check_design_rules(pattern, y_top, y_bottom, ionp_ylocs, cfg: IonpConfig,
                       strict: bool = True, verbose: bool = True) -> Dict[str, Any]:
    """Check a stamped pattern against the fab/MRI rules; raise (or report) on violation.

    Bands are fixed but their ABSOLUTE size scales with the electrode array, so a short
    array shrinks every stripe and every gap -- a pattern that is fine on C3 can be
    unreadable on a shorter probe. That is the expected failure mode of a fixed bank, so by
    default it raises `DesignRuleError` rather than stamping a barcode that will not decode.
    `strict=False` reports instead, for the fit-table tooling that wants the whole matrix.
    """
    yrange = y_top - y_bottom
    prev_y_end = None
    problems, counts, gaps = [], [], []
    for i, (start, end) in enumerate(pattern):
        y_start = start * yrange + y_bottom
        y_end = end * yrange + y_bottom
        n = int(np.sum((ionp_ylocs >= y_start) & (ionp_ylocs <= y_end)))
        counts.append(n)
        if verbose:
            print(f"strip {i}: y {y_start:7.1f}-{y_end:7.1f} um  ({y_end-y_start:6.1f} um)  {n:6d} wells")
        if prev_y_end is not None:
            gap = y_start - prev_y_end
            gaps.append(gap)
            if gap < cfg.min_strip_distance:
                problems.append(f"strip {i} is {gap:.0f}um from strip {i-1} "
                                f"(min_strip_distance {cfg.min_strip_distance})")
        if n < cfg.min_n_wells or n > cfg.max_n_wells:
            problems.append(f"strip {i} holds {n} wells, outside "
                            f"{cfg.min_n_wells}-{cfg.max_n_wells}")
        prev_y_end = y_end

    report = {
        "ok": not problems,
        "problems": problems,
        "min_gap_um": min(gaps) if gaps else float("inf"),
        "min_wells": min(counts) if counts else 0,
        "max_wells": max(counts) if counts else 0,
    }
    if problems:
        msg = (f"IONP pattern does not fit this geometry (array y-range {yrange:.0f} um):\n  "
               + "\n  ".join(problems)
               + "\n\nBands are fixed data, so they scale with the electrode array -- a "
                 "shorter array shrinks every stripe and gap. Either give this design a "
                 "pattern it fits, or leave ionp_pattern_id=None. Run "
                 "`python3 -m electrode_bundle.ionp_patterns` for the design x pattern "
                 "fit table.")
        if strict:
            raise DesignRuleError(msg)
        if verbose:
            print(f"design rules: FAIL\n{msg}")
    elif verbose:
        print("design rules: OK")
    return report


def write_pattern_summary_to_json(pattern, y_top, y_bottom, el_locs,
                                  ionp_x, ionp_y, cfg: IonpConfig, out_fname,
                                  flex_mapping: Dict[str, Any] = None) -> Dict[str, Any]:
    """Write per-strip IONP metadata to JSON, matching the shanks/*_info.json schema.

    `flex_mapping`, if given, is the channel->flex-pad summary from mapping.build_mapping;
    it is embedded as a top-level `flex_mapping` block (right after `electrodes`)."""
    yrange = y_top - y_bottom
    strips = {}
    for i, (start, end) in enumerate(pattern):
        y_start = start * yrange + y_bottom
        y_end = end * yrange + y_bottom
        n = int(np.sum((ionp_y >= y_start) & (ionp_y <= y_end)))
        strips[str(i)] = {
            "name": f"ionp_pattern_{i}",
            "normalized": {"start": float(start), "end": float(end)},
            "real_y_um": {"start": float(y_start), "end": float(y_end),
                          "height": float(y_end - y_start)},
            "n_wells": n,
        }
    summary = {
        "electrodes": {
            "n_electrodes": int(len(el_locs)),
            "locations_um": [{"x": float(x), "y": float(y)} for x, y in el_locs],
        },
    }
    if flex_mapping is not None:
        summary["flex_mapping"] = flex_mapping
    summary.update({
        "ionp_pattern": {
            "name": f"ionp_pattern_{cfg.pattern_i}",
            "n_strips": int(len(pattern)),
            "total_wells": int(len(ionp_x)),
            "normalized_range": {"bottom": 0.0, "top": 1.0},
            "real_y_range_um": {"bottom": float(y_bottom), "top": float(y_top),
                                "height": float(yrange)},
            "strips": strips,
        },
        "metadata": {
            "ionp_pattern_i": int(cfg.pattern_i),
            "ionp_well_d_um": float(cfg.ionp_well_d),
            "ionp_well_distance_um": float(cfg.ionp_well_distance),
            "ionp_firstwell_distance_um": float(cfg.ionp_firstwell_distance),
        },
    })
    with open(out_fname, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"Saved summary -> {out_fname}")
    return summary


def electrode_locs_from_dxf(msp, cfg: IonpConfig) -> np.ndarray:
    """Read the contact centres off an existing DXF's Electrodes layer (standalone mode)."""
    el_locs = io.extract_electrode_centroids_from_msp(msp, cfg.el_layer)
    print(f"Found {len(el_locs)} electrodes")
    return el_locs


def add_ionp_wells(doc, msp, electrode_locs, cfg: IonpConfig = None):
    """
    Stamp IONP wells onto the open document from in-memory contact centres. Returns
    (ionp_x, ionp_y, el_locs) so the caller can also render / write the summary JSON.
    """
    cfg = cfg or IonpConfig()
    el_locs = np.asarray(electrode_locs, dtype=float)
    y_top = effective_y_top(el_locs, cfg)
    pattern = resolve_pattern(cfg)
    ionp_x, ionp_y = well_locs_for_pattern(el_locs, pattern, cfg)
    print(f"Total wells: {len(ionp_x)}")
    check_design_rules(pattern, y_top, cfg.y_bottom, ionp_y, cfg)
    io.write_wells_into_dxf(doc, msp, ionp_x, ionp_y,
                            cfg.ionp_well_d, cfg.ionp_layer, cfg.well_resolution)
    return ionp_x, ionp_y, el_locs, pattern
