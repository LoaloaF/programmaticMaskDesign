# ufte_probes: electrode bundles and their wafer

`electrode_bundle/` computes a flexible 64-channel electrode probe from parameters and emits its CAD.
The probe wafer then arranges as many copies as fit on a 100 mm wafer and writes the combined GDS for
fab:
- the wafer stages are shared with the interconnect project, in `maskgen/wafer/`;
- this project's wafer config is in `wafer_nesting/`.

```
electrode_bundle/design_sets.py        wafer_nesting/config_64ch_4shank.py
        │                                        │
        ▼                                        ▼
<root>/electrode/<set>/shanks/  ─────►  <root>/wafer/<set>/
  the probe geometry                      as many as fit, nested, fab-ready
```

`<root>` is the maskgen output root (top-level README; `python3 -m maskgen where` prints it).
Nothing is written into the repo.

Full detail:
- the generator: [`electrode_bundle/HANDOVER.md`](electrode_bundle/HANDOVER.md);
- the wafer: [`wafer_nesting/README.md`](wafer_nesting/README.md).

This page is only the map and the order to do things in.

> **Before anything is fabricated**, read the layer map at the bottom of
> [`wafer_nesting/README.md`](wafer_nesting/README.md). The DXF-layer → fab-layer mapping is
> provisional and has one open decision in it (`ionp`).

## Layout

```
ufte_probes/                 run the generator from HERE
  electrode_bundle/          the design generator
    HANDOVER.md
    batch.py, design_sets.py, config.py, bundle.py, ...
    hooks/                   named insertion-hook specs
    designs/                 shipped ...Example sample files (not output)
    tests/
  wafer_nesting/             the probe wafer's config (config_64ch_4shank.py) + README
  changes_plan/              the design change list and the per-session briefs
  mario_code/                the earlier standalone generator (Mario Togni & Baran Yasar), reference only
```

## Prerequisites

```bash
pip install ezdxf shapely numpy matplotlib pytest
```

- On the dev machine these live in the `dxf` conda env, and also in the base conda Python.
- **KLayout** runs the wafer export and verify stages.

Check the generator before starting:

```bash
python3 -m pytest electrode_bundle/tests -q -p no:cacheprovider     # 176 tests, ~4 min
```

## 1. Generate the designs

**The usual command.** It builds every design listed in `electrode_bundle/design_sets.py`: four
geometries (U1.6, U2.5, U4, U8) × four IONP barcodes each, so 16 variants.

```bash
python3 -m electrode_bundle.batch [--jobs N]
```

Everything lands in **`<root>/electrode/<TARGET_SET>/shanks/`**:
- **Per geometry** (`<name>`, e.g. `U4`):
  - `electrode_bundle_<name>.dxf` + `.png` (barcode-free);
  - `electrode_to_flex_mapping_<name>.json`;
  - `<name>_electrodes.json`.
- **Per barcoded variant** (`<tag>`, e.g. `U4C08`):
  - `electrode_bundle_<tag>.dxf` + `.png`;
  - `<tag>_info.json`.
- **Two comparison drawings:**
  - **`all_designs_side_by_side.dxf`**: every geometry in a row;
  - **`all_ionp_patterns_side_by_side.dxf`**: all 16 barcoded variants in a row.

Options:
- `--no-render` skips the PNGs (much faster).
- `--no-side-by-side` skips the combined DXFs.

To change what gets built, edit `_BASE_DESIGNS` in `electrode_bundle/design_sets.py`. Its docstring
has a labelled diagram of every length parameter.

**Rebuild a comparison drawing on its own**, optionally for a subset:

```bash
python3 -m electrode_bundle.side_by_side              # all geometries
python3 -m electrode_bundle.side_by_side --ionp       # all barcoded variants
python3 -m electrode_bundle.side_by_side 1.6 U8       # name substrings
```

**Compare features side by side.** These run from the repo root and write a PNG/PDF grid with one
feature per row, at one scale per row:

```bash
python3 -m maskgen review --probes                    # tip, first sites, solder pads, whole device
python3 -m maskgen review --probes U1.6C00 U8C15      # barcoded variants: + the IONP rows
```

**One-off, scratch probe.** `main.py` builds a single probe from a bare `BundleConfig()`, with a
barcode. It is not one of the `DESIGNS`.

```bash
python3 -m electrode_bundle.main all --pattern-i 11
```

It writes `electrode_bundle_C11.dxf`, its preview and `64ch_C11_info.json` into the shanks folder.
Details: [`electrode_bundle/HANDOVER.md`](electrode_bundle/HANDOVER.md) §1.

> The files in `electrode_bundle/designs/` carry an **`Example`** suffix. They are shipped samples
> from an earlier generation of the generator: no command reproduces them, and nothing reads them.

## 2. Put it on a wafer

```bash
python3 -m maskgen wafer 64Ch_4Shankdesigns      # from the repo root
```

The command:
- builds the shanks if they are missing;
- nests if there is no nest yet;
- exports, verifies and draws the map.

The deliverable is `<root>/wafer/64Ch_4Shankdesigns/wafer_64Ch_4Shankdesigns.gds`.
`wafer_map_64Ch_4Shankdesigns.png` labels every device with its variant. The stages one by one, and
the options, are in [`wafer_nesting/README.md`](wafer_nesting/README.md).

**The shipped wafer is final.** It holds 59 devices, and its nest is verified. Run `--nest` only
for a new design set.

## Two things worth knowing up front

**The piece count is an output, not an input.**
- The config's `SHANK_RATIO` is read only as a *ratio*.
- The nester fills the wafer and reports how many fit: 59 at 74 % density for the current set.
- So `SHANK_COUNTS` in the config has to be updated from the nester's printed line before
  exporting, or the export's count assertion trips.

**A DXF layer missing from the layer map is silently deleted on export.** The map is
`PROBE_64CH` in `maskgen/core/layers.py`.
- The export prints what it mapped and what it dropped on every run: read that output.
- The verify stage's per-layer shape-count check is the backstop.
