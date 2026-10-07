# generative_probe_design — electrode bundles and wafer nesting

Two folders, one pipeline. `electrode_bundle/` computes a flexible electrode probe from
parameters and emits its CAD; `WaferNesting/` takes designs like that one, arranges as
many copies as fit on a 100 mm wafer, and writes the combined GDS for fab.

They are independent — you can nest any DXF, not just a bundle — but this is the path
they were built for:

```
electrode_bundle/            WaferNesting/
  design_sets.py               config_64ch_4shank.py
      │                              │
      ▼                              ▼
  <TARGET_SET>/shanks/  ─────►  <TARGET_SET>/wafer/
   the probe geometry            as many as fit, nested, fab-ready
```

Full detail lives in [`electrode_bundle/HANDOVER.md`](electrode_bundle/HANDOVER.md) (the
generator) and [`WaferNesting/README.md`](WaferNesting/README.md) (the nester). Read those;
this page is only the map and the order to do things in.

> **Before anything is fabricated**, read the layer map at the bottom of
> [`WaferNesting/README.md`](WaferNesting/README.md). The DXF-layer → fab-layer mapping is
> provisional and has one open decision in it (`ionp`).

---

## Layout

Two code folders side by side, and one **target folder per design set** holding both
halves of the output. The target folder's name lives in `electrode_bundle/config.py` and
`WaferNesting/config_64ch_4shank.py` as `TARGET_SET`; the two must agree.

```
generative_probe_design/      <- run everything from HERE
  README.md                   <- this page
  electrode_bundle/           <- the design generator (code)
    HANDOVER.md
    batch.py, design_sets.py, config.py, bundle.py, ...
    hooks/                    <- named insertion-hook specs
    designs/                  <- shipped ...Example sample files (not output)
    tests/
  WaferNesting/               <- the nester (code)
    README.md
    00_check_setup.py, 01_extract_footprints.py, nest_tiler.py,
    03_export_wafer.py, 04_verify_wafer.py, 05_map_wafer.py
    config_64ch_4shank.py, config_common.py, footprint_extract.py
    new_wafer_actually.GDS    <- wafer template, included
    marks_union.wkt           <- alignment marks, included
  changes_plan/               <- the design change list and the per-session briefs
  64Ch_4Shankdesigns/         <- the target folder (all generated output)
    shanks/                   <- DXFs, PNGs, per-design JSON
    wafer/                    <- footprints, poses, preview, wafer GDS, wafer map
```

**Run every command from this folder**, not from inside either subfolder. Both tools
depend on it: `python3 -m electrode_bundle.batch` needs the package's parent on the path,
and the stage scripts are invoked as `WaferNesting/<stage>.py`.

---

## Prerequisites

```bash
pip install ezdxf shapely numpy matplotlib pytest
```

(On the dev machine these live in the `dxf` conda env:
`source /opt/anaconda3/etc/profile.d/conda.sh && conda activate dxf`.)

Plus **KLayout** for nesting stages 03 and 04 (GDS work). `00_check_setup.py` looks for it
at `/Applications/klayout.app/Contents/MacOS/klayout` and
`/Applications/KLayout/klayout.app/Contents/MacOS/klayout`.

Check both halves before starting:

```bash
python3 -m pytest electrode_bundle/tests -q                       # 240 tests; slow, minutes
WAFERNEST_CONFIG=config_64ch_4shank \
  python3 WaferNesting/00_check_setup.py                          # resolves every path
```

`00_check_setup.py` changes nothing. If an input is missing it names the exact setting in
the exact file to edit, and lists the DXFs it can actually see.

---

## 1. Generate the designs

**The usual command.** Builds every design listed in `electrode_bundle/design_sets.py` —
four geometries (U1.6, U2.5, U4, U8) × four IONP barcodes each = 16 variants:

```bash
python3 -m electrode_bundle.batch
```

Everything lands in **`<TARGET_SET>/shanks/`**. Per geometry (`<name>`, e.g. `U4`):
`electrode_bundle_<name>.dxf` + `.png` (barcode-free), `electrode_to_flex_mapping_<name>.json`
and `<name>_electrodes.json`. Per barcoded variant (`<tag>`, e.g. `U4C08`):
`electrode_bundle_<tag>.dxf` + `.png` and `<tag>_info.json`. Plus two comparison drawings:
**`all_designs_side_by_side.dxf`** (every geometry in a row) and
**`all_ionp_patterns_side_by_side.dxf`** (all 16 barcoded variants in a row). Add
`--no-render` to skip the PNGs (much faster) or `--no-side-by-side` to skip the combined
DXFs.

To change what gets built, edit `_BASE_DESIGNS` in `electrode_bundle/design_sets.py`; its
docstring has a labelled diagram of every length parameter.

Rebuild a comparison drawing on its own, optionally for a subset:

```bash
python3 -m electrode_bundle.side_by_side              # all geometries
python3 -m electrode_bundle.side_by_side --ionp       # all barcoded variants
python3 -m electrode_bundle.side_by_side 1.6 U8       # name substrings
```

**One-off, scratch probe.** `main.py` builds a single probe from a bare `BundleConfig()`
(not one of the `DESIGNS`), with a barcode:

```bash
python3 -m electrode_bundle.main all --pattern-i 11
```

Writes `64Ch_4Shankdesigns/shanks/electrode_bundle_C11.dxf` plus its preview and
`64ch_C11_info.json`. Details: [`electrode_bundle/HANDOVER.md`](electrode_bundle/HANDOVER.md) §1.

> The files in `electrode_bundle/designs/` carry an **`Example`** suffix and are shipped
> samples from an earlier generation of the generator. They are not reproduced by any
> command and nothing reads them — `config_64ch_4shank.py` nests the real generated designs
> in `<TARGET_SET>/shanks/`, so run the generator first.

## 2. Nest it onto a wafer

```bash
export WAFERNEST_CONFIG=config_64ch_4shank
KL=/Applications/KLayout/klayout.app/Contents/MacOS/klayout

python3 WaferNesting/00_check_setup.py
python3 WaferNesting/01_extract_footprints.py
python3 WaferNesting/nest_tiler.py --time 900
# paste the SHANK_COUNTS line it prints into the config, then:
$KL -b -r WaferNesting/03_export_wafer.py
$KL -b -r WaferNesting/04_verify_wafer.py
python3 WaferNesting/05_map_wafer.py
```

Which designs and in what ratio is set at the top of
`WaferNesting/config_64ch_4shank.py`. Output lands in `64Ch_4Shankdesigns/wafer/`; the
deliverable is `wafer_64Ch_4Shankdesigns.gds`, `nest_preview.png` shows the arrangement
(refreshed live while the search runs), and `wafer_map_64Ch_4Shankdesigns.png` labels every
device with its variant.

Stage 04 should end with `ALL CHECKS PASSED`. Stage 03 refuses to overwrite an existing
wafer GDS — delete it (or change `RUN_TAG`) to re-export.

---

## Two things worth knowing up front

**The piece count is an output, not an input.** The config's `SHANK_RATIO` is read only as
a *ratio*; `nest_tiler.py` fills the wafer and reports how many fit — 59 at 74 % density
for the current 64-channel set. Because of that, `SHANK_COUNTS` in the config has to be
updated from the nester's printed line before exporting, or stage 03's count assertion
trips.

**A DXF layer missing from `LAYER_MAP` is silently deleted on export.** Stage 03 prints
what it mapped and what it dropped on every run — read that output. Stage 04's per-layer
shape-count check is the backstop.
