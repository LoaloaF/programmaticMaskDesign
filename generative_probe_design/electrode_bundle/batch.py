"""Build every design in design_sets.DESIGNS.

    python3 -m electrode_bundle.batch
    python3 -m electrode_bundle.batch --no-render

Each DesignSpec becomes one BundleConfig: `sites` resolves to the spacing knobs, then
`fiber_length`/`loop_offset` are solved for (lengths.py).

EVERY design writes the barcode-free geometry DXF at `bundle_dxf_path(name)` plus its
flex-pad mapping JSON and its `<name>_electrodes.json` record. A design that also carries an `ionp_pattern_id` then runs the same
in-memory `all` pipeline main.py uses (build once, mapping + IONP off the same result) and
writes a SECOND, barcoded DXF at the `variant_tag` path. Writing the plain DXF
unconditionally is deliberate: side_by_side.py reads exactly that path for every design and
used to crash with FileNotFoundError as soon as any design carried an id.

An id whose pattern does not fit the design's electrode array raises out of
`ionp.check_design_rules` -- run `python3 -m electrode_bundle.ionp_patterns` for the fit
table before adding one.

Entries share a `name` by design -- `DESIGNS` is four geometries x four barcode ids, so each
geometry appears four times. The barcode-free artefacts are written once per NAME, by the
first entry that uses it; the other three only build their barcoded DXF.

Finishes by writing TWO comparison drawings: `all_designs_side_by_side.dxf`, every distinct
GEOMETRY in a row barcode-free, and `all_ionp_patterns_side_by_side.dxf`, every barcoded
VARIANT in a row. The first says whether the shank shapes are right; the second is where you
check that the 16 stripe patterns are distinguishable from one another.
"""
import argparse
from dataclasses import replace

import numpy as np

from . import bundle as bundle_mod
from . import dxf_io as io
from . import shapes as sh
from .config import (BundleConfig, IonpConfig, MappingConfig, bundle_dxf_path,
                     ensure_out_dir, variant_tag)
from .design_sets import BUNDLE_FOLD, DESIGNS
from .lengths import solve_lengths
from .main import _save_png, _write_json, cmd_all, cmd_mapping
from .probe_json import build_record
from .side_by_side import build_side_by_side, dedupe_by_tag


def _target_positions(spec):
    """The contact y positions the spec asks for, as DRAWN on the wafer.

    Same frame and convention as the plain staircase: contact 0 sits at -(its own first
    gap), the rest accumulate upward. `config_for` draws exactly these -- the fold cost is
    absorbed by the staggered shoulder, not by moving contacts.
    """
    n = spec.n_channels
    if hasattr(spec.sites, "span"):
        delta_y = spec.sites.span / (n - 1)
        return np.array([(i - 1) * delta_y for i in range(n)], dtype=float)
    gaps = spec.sites.overrides(n)
    ys = [-gaps[1]]
    for i in range(1, n):
        ys.append(ys[-1] + gaps[i])
    return np.array(ys, dtype=float)


def _fiber_x(cfg, n):
    """Signed lateral offset of every FIBER, which is what the fold cost depends on.

    Fiber-indexed to match `BundleConfig.shoulder_offsets_profile`: index 0 is the
    reference on the centre slot, recording channel c is index c+1. Keep this in step with
    `shapes.fiber_x_positions` -- it feeds the shoulder stagger, which feeds the length
    solve, so a layout that disagrees here ships designs whose fibers are the wrong length.
    """
    chan_x, ref_x = sh.fiber_x_positions(n, cfg.delta_x, cfg.wire_hw, cfg.wide_wire_hw)
    return np.array([ref_x] + [chan_x[i] for i in range(n)], dtype=float)


def _channel_x(cfg, n):
    """Signed lateral offset of each RECORDING channel (the reference dropped)."""
    return _fiber_x(cfg, n)[1:]


def design_label(spec) -> str:
    """What gets stamped into the gold layer, e.g. "U8" or "U1.6\\nC01".

    The same string `variant_tag` builds ("U1.6C01"), broken over two lines: this one is
    read off a wafer under a scope, and the name stays legible on its own line above the
    IONP code. Keep the two in step -- matching a piece under the scope to its DXF is
    supposed to be reading, not decoding.
    """
    return (spec.name if spec.ionp_pattern_id is None
            else f"{spec.name}\nC{spec.ionp_pattern_id:02d}")


def config_for(spec) -> BundleConfig:
    """The BundleConfig a DesignSpec describes: sites at spec, shoulders staggered.

    `spec.sites` states where the contacts are DRAWN, and they are drawn exactly there --
    the staircase is the plain one `spacing.py` produces, untouched.

    The extra fiber each channel needs to reach the bundle is added at the TOP instead, by
    freeing that fiber from the polyimide block higher up: channel i's shoulder sits
    `BUNDLE_FOLD.offsets(x_i)` above the base, so its free fiber is that much longer. The
    centre fiber gains ~0 and the outermost gains the most (~282 um on C1), which is what
    makes the block's lower edge a staggered curve rather than a straight cut -- see the
    variable-shoulder surface in `bundle.py`.

    Note what this does NOT do: `bundling.offsets` is a function of lateral x alone, so under
    that model the bundled contact still lands at `drawn_y + offset` and the implanted array
    still comes out ~282 um longer than the drawn one. The stagger buys the outer fibers a
    longer, gentler run into the bundle; it is not a depth correction. `probe_json` records
    the bundled positions alongside the drawn ones so the difference stays visible.

    `shoulder_offsets_profile` is set BEFORE `solve_lengths` runs, so the solve measures the
    staggered build and `fiber_length` still lands on spec (measured loop -> block bottom,
    i.e. the top of the stagger, which is where the ribbon actually begins).
    """
    n = spec.n_channels
    probe = BundleConfig(num_channels=n)
    #each channel's fiber is freed this much higher up; index by channel, not by x
    rise = BUNDLE_FOLD.offsets(_fiber_x(probe, n))

    cfg = BundleConfig(
        num_channels=n,
        **spec.sites.delta_y_kwargs(n),
        shoulder_offsets_profile=lambda i, _r=rise: float(_r[i]),
        label=design_label(spec),
    )
    cfg = solve_lengths(cfg, fiber_length=spec.fiber_length, loop_offset=spec.loop_offset)

    if spec.overall_length is not None:
        #the ribbon absorbs whatever the fiber does not use, so designs with different
        #fiber lengths still end at the same connector position
        ribbon = spec.overall_length - spec.fiber_length
        if ribbon <= cfg.polyimide_fan_height:
            raise ValueError(
                f"{spec.name}: overall_length {spec.overall_length} leaves only {ribbon} um "
                f"of ribbon for a {cfg.polyimide_fan_height} um fanout -- it must exceed "
                f"fiber_length ({spec.fiber_length}) by more than that")
        cfg = replace(cfg, ribbon_length=ribbon)
    return cfg


def _write_geometry_and_records(bcfg, result, spec, out_dir, render):
    """The barcode-free geometry DXF/PNG, the flex-pad mapping JSON and the per-design
    electrode record. Written for EVERY design, including barcoded ones -- side_by_side.py
    reads `bundle_dxf_path` unconditionally, and the electrode record describes the
    geometry, which the barcode does not change."""
    dxf_path = bundle_dxf_path(spec.name)
    io.save_dxf(result.doc, dxf_path)
    print(f"Saved -> {dxf_path}")
    if render:
        _save_png(result, dxf_path.replace(".dxf", ".png"),
                  title=f"{spec.name} ({result.num_channels} ch) - {spec.doc_id}")

    mcfg = MappingConfig(out_file=f"{out_dir}/electrode_to_flex_mapping_{spec.name}.json")
    flex_mapping = cmd_mapping(bcfg, mcfg, result=result)
    #the fold cost is set by the FIBER's lateral position, not the contact centroid -- pass
    #the same array the shoulder stagger used, or the bundled span lands 0.05 um off
    _write_json(build_record(spec, bcfg, result, BUNDLE_FOLD, flex_mapping,
                             fiber_x=_channel_x(bcfg, result.num_channels)),
                f"{out_dir}/{spec.name}_electrodes.json")


def build_design(spec, out_dir: str, render: bool = True, geometry: bool = True):
    """Build one DesignSpec: geometry always, plus the barcoded variant when it has an id.

    `geometry=False` skips the barcode-free artefacts because an earlier entry with the same
    `name` already wrote them. Every design ships four barcode variants, so without that the
    run would build and save each geometry four times over for byte-identical output."""
    bcfg = config_for(spec)
    tag = variant_tag(spec.name, spec.ionp_pattern_id)
    print(f"\n=== {tag} ({spec.doc_id}) ===")

    if geometry:
        #the barcode-free artefacts first, so they exist even if the IONP pass rejects the id
        result = bundle_mod.build_bundle(bcfg)
        _write_geometry_and_records(bcfg, result, spec, out_dir, render)

    if spec.ionp_pattern_id is None:
        return
    icfg = IonpConfig(
        pattern_i=spec.ionp_pattern_id,
        dxf_file=f"{out_dir}/electrode_bundle_{tag}.dxf",
        json_file=f"{out_dir}/{tag}_info.json",
    )
    #a fresh build: the geometry doc above has already been saved, and add_ionp_wells
    #mutates the doc it is handed
    cmd_all(bcfg, MappingConfig(), icfg, render=render)


def run_batch(render: bool = True, side_by_side: bool = True):
    out_dir = ensure_out_dir()
    written = set()
    for spec in DESIGNS:
        build_design(spec, out_dir, render, geometry=spec.name not in written)
        written.add(spec.name)

    if side_by_side:
        #TWO comparison drawings, because DESIGNS is geometries x barcode ids and the two
        #questions are different. One entry per GEOMETRY, barcode-free: does the set of
        #shank shapes look right. Then one entry per VARIANT, barcoded: are the 16 stripe
        #patterns actually distinguishable from each other.
        geometries = dedupe_by_tag(DESIGNS, ionp=False)
        print(f"\n=== all {len(geometries)} geometries side by side ===")
        out = f"{out_dir}/all_designs_side_by_side.dxf"
        build_side_by_side(geometries).saveas(out)
        print(f"Saved -> {out}")

        variants = dedupe_by_tag([s for s in DESIGNS if s.ionp_pattern_id is not None],
                                 ionp=True)
        if variants:
            print(f"\n=== all {len(variants)} IONP variants side by side ===")
            out = f"{out_dir}/all_ionp_patterns_side_by_side.dxf"
            build_side_by_side(variants, ionp=True).saveas(out)
            print(f"Saved -> {out}")


def _build_parser():
    p = argparse.ArgumentParser(
        description="Build every electrode_bundle design in design_sets.DESIGNS")
    p.add_argument("--no-render", action="store_true", help="skip PNG rendering")
    p.add_argument("--no-side-by-side", action="store_true",
                   help="skip the combined all-designs-in-one DXF")
    return p


def main(argv=None):
    args = _build_parser().parse_args(argv)
    run_batch(render=not args.no_render, side_by_side=not args.no_side_by_side)


if __name__ == "__main__":
    main()
