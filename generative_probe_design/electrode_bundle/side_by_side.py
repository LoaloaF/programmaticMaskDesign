"""Lay generated bundle DXFs next to each other in ONE DXF, for eyeballing them together.

    python3 -m electrode_bundle.side_by_side                  # every GEOMETRY, barcode-free
    python3 -m electrode_bundle.side_by_side --ionp          # every barcoded VARIANT
    python3 -m electrode_bundle.side_by_side 1.6 2.5         # just these (substring match)

Deliberately NOT WaferNesting: this only translates, never rotates, and keeps the order you
asked for (DESIGNS order, left to right, each labelled). It is a comparison drawing, not a
wafer layout -- no wafer outline, no clearance checks, no alignment marks.

Two flavours, because `DESIGNS` carries each geometry once per barcode id. By default this
draws one column per GEOMETRY off the barcode-free DXFs -- the drawing compares shapes, and
four identical shanks differing only in their wells would say nothing. `--ionp` draws one
column per VARIANT instead, off the barcoded DXFs, which is the drawing for checking that
the 16 stripe patterns really are distinguishable from each other.

Designs are lined up on a shared datum in y (`--align loop`, the default, or `top`), since
each one's absolute y depends on its own pitch and hook drop and they would otherwise sit
visibly staggered. Labels go on their own `Labels` layer
(WaferNesting's LAYER_MAP has no entry for it, so it would be dropped on a fab export
rather than silently becoming a mask feature).
"""
import argparse

import ezdxf
from ezdxf import bbox
from ezdxf.addons import importer
from ezdxf.enums import TextEntityAlignment

from .config import LOOP_DATUM_VAR, bundle_dxf_path, ensure_out_dir, variant_tag
from .design_sets import DESIGNS

GAP_UM = 1000.0          # clear space between neighbouring designs
LABEL_HEIGHT_UM = 700.0
LABEL_LAYER = "Labels"


def select_specs(filters):
    """DESIGNS entries whose name contains any of `filters` (all of them if empty)."""
    if not filters:
        return list(DESIGNS)
    picked = [s for s in DESIGNS if any(f in s.name for f in filters)]
    missing = [f for f in filters if not any(f in s.name for s in DESIGNS)]
    if missing:
        raise SystemExit(f"no design name matches {missing}; have: "
                         + ", ".join(s.name for s in DESIGNS))
    return picked


def source_tag(spec, ionp: bool) -> str:
    """Which built DXF this spec contributes, and what to label the column.

    `ionp=False` -> the barcode-free geometry, one per NAME. `ionp=True` -> the barcoded
    build at the `variant_tag` path, one per (name, id). Both are written by batch.py.
    """
    return variant_tag(spec.name, spec.ionp_pattern_id) if ionp else spec.name


def dedupe_by_tag(specs, ionp: bool):
    """One spec per source DXF, keeping order. Without `--ionp` that collapses a geometry's
    four barcode variants into a single column; with it, nothing collapses."""
    return list({source_tag(s, ionp): s for s in specs}.values())


def loop_datum(doc):
    """The insertion loop's y from a DXF's header, or None if this build did not stamp it."""
    for key, value in doc.header.custom_vars.properties:
        if key == LOOP_DATUM_VAR:
            return float(value)
    return None


def datum_y(doc, align: str) -> float:
    """The y this design should be lined up by.

    Designs do NOT share an absolute y: the loop sits at -(delta_y + hook_drop + const), so
    a design's whole geometry is rigidly offset by its own pitch and hook drop (~200 um of
    spread across the current set). Laying them out without correcting for that leaves them
    visibly staggered, so pick a physical datum and put every design's on the same line.
    """
    if align == "loop":
        #The loop's y, stamped into the DXF header by build_bundle. It cannot be measured off
        #a layer any more: the loop hole is etched in the same step as the release, so it is
        #one ring among hundreds in Etching rather than a layer of its own.
        y = loop_datum(doc)
        if y is None:
            raise SystemExit(f"{LOOP_DATUM_VAR} is missing from this DXF -- it was written "
                             "by an older build; regenerate it, or use --align top")
        return y
    if align == "top":
        return bbox.extents(doc.modelspace()).extmax.y   # the connector end of the polyimide
    raise SystemExit(f"unknown --align {align!r}; use 'loop' or 'top'")


def build_side_by_side(specs, gap_um=GAP_UM, align="loop", ionp=False):
    target = ezdxf.new(setup=True)
    target.layers.add(LABEL_LAYER)
    tmsp = target.modelspace()

    if ionp:
        missing = [s.name for s in specs if s.ionp_pattern_id is None]
        if missing:
            raise SystemExit(f"--ionp needs a barcoded build, but {sorted(set(missing))} "
                             f"carry ionp_pattern_id=None -- there is no barcoded DXF for "
                             f"them. Drop them from the selection or give them an id.")

    x_cursor = 0.0
    for spec in specs:
        tag = source_tag(spec, ionp)
        src = ezdxf.readfile(bundle_dxf_path(tag))
        smsp = src.modelspace()
        ext = bbox.extents(smsp)

        #left edge to the cursor, and the chosen datum onto y = 0
        dx = x_cursor - ext.extmin.x
        dy = -datum_y(src, align)
        for entity in smsp:
            entity.translate(dx, dy, 0)
        ext = bbox.extents(smsp)

        imp = importer.Importer(src, target)
        imp.import_modelspace(target_layout=tmsp)
        imp.finalize()

        #labels run VERTICALLY, upward from the top of each design: a name is up to ~10 mm of text
        #on a 3.1 mm wide design, so horizontal labels would overlap their neighbours.
        tmsp.add_text(
            tag, height=LABEL_HEIGHT_UM,
            dxfattribs={"layer": LABEL_LAYER, "rotation": 90},
        ).set_placement((x_cursor + ext.size.x / 2, ext.extmax.y + LABEL_HEIGHT_UM),
                        align=TextEntityAlignment.MIDDLE_LEFT)

        print(f"  {tag:28s} x {x_cursor:9.1f} .. {x_cursor + ext.size.x:9.1f} um")
        x_cursor += ext.size.x + gap_um

    return target


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Put generated bundle DXFs side by side in one DXF (no rotation)")
    p.add_argument("names", nargs="*", help="name substrings to include; default all of DESIGNS")
    p.add_argument("--gap", type=float, default=GAP_UM, help="um between designs")
    p.add_argument("--align", choices=("loop", "top"), default="loop",
                   help="line the designs up by the insertion loop (default) or by their "
                        "connector end")
    p.add_argument("--ionp", action="store_true",
                   help="one column per BARCODED variant instead of one per geometry")
    p.add_argument("--out", help="output DXF path; default derived from the names")
    args = p.parse_args(argv)

    specs = dedupe_by_tag(select_specs(args.names), args.ionp)
    print(f"side by side, left to right: {len(specs)} "
          f"{'barcoded variants' if args.ionp else 'geometries'}")
    doc = build_side_by_side(specs, gap_um=args.gap, align=args.align, ionp=args.ionp)

    out = args.out or f"{ensure_out_dir()}/side_by_side_" + (
        "-".join(args.names) if args.names else "all") + (
        "_ionp" if args.ionp else "") + ".dxf"
    doc.saveas(out)
    print(f"Saved -> {out}")
    return out


if __name__ == "__main__":
    main()
