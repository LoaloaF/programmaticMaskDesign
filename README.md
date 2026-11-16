# programmaticMaskDesign

This repo generates the photomask layouts for the MEA1K flexible-electronics wafers: the devices
themselves, and how they are arranged on a 100 mm wafer. Everything is generated from parameters.
Nothing on a wafer is drawn by hand, except where noted.

```
programmaticMaskDesign/
  maskgen/                  SHARED code, and the `maskgen` command
    core/                   wafers.py (EVERY wafer: name, revisions, layout config),
                            paths.py (output root + folder layout), manifest.py (run records),
                            layers.py (EVERY mask layer: DXF names, fab numbers, names, colours)
    wafer/                  wafer layout for both projects: nesters, export, marks, verify, rebuild
    review/                 review sheets: named features side by side
  mea1k_interconnects/      MEA1K chip <-> Molex connector interconnects (HANDOVER.md)
    catalog.py              every design and every wafer revision
    build_designs.py        all five designs of a revision
    rev2_reference/         the fabricated Rev2 wafer GDS, its design files and its measured placements
    wafers/                 the interconnect wafer config (Rev4 at the Rev2 placements)
  ufte_probes/              flexible 64-channel electrode probes (README.md)
    electrode_bundle/       the generator (HANDOVER.md, tests/)
    wafers/                 the probe wafer config
  legacy/                   FROZEN, runnable, never imports maskgen (see each README)
    rev2_repro/             reproduces the fabricated Rev2 wafer; the Rev2/Rev3 overlay
    interconnect_nest_2026-07/   the July 2026 annealing nest of the interconnects
    old_interconnect/       routers for the pre-circular-pad interconnect
  tests/golden/             regression harness: geometry must stay identical to commit 667ba2d
```

## Setup

```bash
pip install ezdxf shapely numpy gdstk matplotlib pytest      # or: pip install -e .  (adds a `maskgen` command)
echo /path/outside/the/repo > .maskgen_out                   # the output root, once per checkout
```

- **KLayout:** the 0.29 app runs every GDS stage. It is found at
  `/Applications/KLayout/klayout.app`, or set `$KLAYOUT`.
- **Python:** on the dev machine, the base conda Python (`/opt/anaconda3/bin/python3`) has every
  package. The `dxf` env lacks gdstk.
- **Pinned versions:** in `pyproject.toml`.

Every command below runs from the repo root as `python3 -m maskgen ...`, or as `maskgen ...` after
`pip install -e .`.

## Where outputs go

Generated files **never** go inside the repo. They go under one **output root**. The root is the
`--out` option if given, otherwise `$MASKGEN_OUT`, otherwise the path written in `.maskgen_out`
(git-ignored; KLayout started from the Dock sees this file too). A root inside the repo is refused.

Under the root, everything is organised by **wafer**, then **revision** (see "Wafers and revisions"
below):

| What | Folder | Written by |
|---|---|---|
A wafer is made of single **devices** (an interconnect, a dummy, a probe geometry), each placed many
times. Every device has its own folder with a `device.json` that says what it is and how it was made.

```
<root>/
  MEA1K_wafer4_12x_8x_8x_breakoutboards/        a wafer
    Rev4/                                       one revision of it
      devices/
        12block_56_15/                          one device
          12block_56_15.dxf                     what the wafer places
          12block_56_15_padgrid.dxf             stage-01 intermediate
          device.json                           what it is, revision chain, changed settings, pad grid
          logs/  ref_rev3/
        8block_51_35/ ...  dummy_61_10/ ...
        manifest.json                           the build: git commit, md5 of every file
      MEA1K_wafer4_12x_8x_8x_breakoutboards_Rev4.gds/.oas/.lyp    the wafer layout
      nest/                                     footprints, poses (nested wafers)
      review/<sheet>/                           review sheets of this revision
  MEA1K_W5_64Ch_4Shankdesigns/Rev1/devices/U4/  a probe device: U4 + its barcoded variants U4C08..C11
  _golden/                                      the regression harness's reference + scratch; not for reading
  index.jsonl                                   every run
```

| What | Written by |
|---|---|
| `devices/` of an interconnect wafer | `mea1k_interconnects/build_designs.py --wafer W --rev N` (`--stage01`: `Rev<N>/draft/`) |
| `devices/` of a probe wafer | `cd ufte_probes && python3 -m electrode_bundle.batch` |
| the wafer layout, `nest/` | `maskgen wafer W --rev N` |
| `review/` | `maskgen review ...` |

The folder layout is `maskgen/core/paths.py`.

Every run folder gets a `manifest.json` with:
- the git commit;
- the settings;
- the md5 of every input and output.

`<root>/index.jsonl` lists all runs, and every command ends by printing what it wrote, and where.

## The commands

```bash
maskgen where                          # the root, every wafer's folder, the last runs
maskgen where wafer4 4                 # one wafer revision: its devices, its layout, its review sheets
maskgen runs                           # the run index

maskgen show                           # interconnect revisions of every wafer: parent, status, changes
maskgen show --wafer wafer4 --rev 4 --design 12block_56_15 --changed   # one design's resolved settings + who set them

maskgen wafer                          # every wafer, its revisions, which have a layout
maskgen wafer wafer4 --rev 4           # Rev4 designs if missing, then the wafer, checked against Rev2
maskgen wafer wafer5                   # (latest revision) probe designs if missing, nest if none yet, export, verify, map

maskgen review --wafer wafer4 --rev 3 4          # every interconnect design, Rev3 next to Rev4, feature by feature
maskgen review --rev wafer4/4 wafer6/1 --xor     # across wafers
maskgen review --probes                # the electrode geometries side by side
maskgen review --wafers wafer4/4 rev2_fabricated wafer5   # alignment-mark slots
maskgen review --rois                  # the named features
```

**Designs.**
- Interconnect designs: `cd mea1k_interconnects && python3 build_designs.py --wafer wafer4 --rev 4`.
  It takes about 2 minutes and builds all five designs (`--only 12block_56_15` for one).
- Probe designs: `cd ufte_probes && python3 -m electrode_bundle.batch`.
- `maskgen wafer` and `maskgen review` run these themselves when the designs are missing.

**Review sheets.** A review sheet replaces the whole-wafer overlay. It is a grid:
- **Rows** are named features: top pad, top-pad via, band above the column, connector pad, dummy
  bars, probe tip, first sites, IONP wells, solder pads, mark slots.
- **Columns** are revisions or designs.
- Each feature is found in each design's own geometry, and every cell of a row is drawn at the same
  scale, in the colours of `maskgen/core/layers.py`.
- `--design` and `--roi` select columns and rows.
- `--xor` outlines where a revision differs from the first revision of the same design.

Each sheet writes four files:
- `sheet.png`: the whole grid;
- `sheet.pdf`: one page per feature;
- `index.md`: the sources, the feature positions, the smallest metal gap in each interconnect cell,
  and the XOR areas;
- `manifest.json`.

## The two projects

| Project | Makes | Start with |
|---|---|---|
| `mea1k_interconnects/` | the 12-block / 8-block interconnect DXFs and the dummy test structures | [`mea1k_interconnects/HANDOVER.md`](mea1k_interconnects/HANDOVER.md) |
| `ufte_probes/` | electrode-probe DXFs from design specs | [`ufte_probes/README.md`](ufte_probes/README.md) |
| `maskgen/wafer/` | wafer layout for both | [`ufte_probes/wafers/README.md`](ufte_probes/wafers/README.md) (nested wafers), [`mea1k_interconnects/wafers/README.md`](mea1k_interconnects/wafers/README.md) (fixed placements) |

**Where things are defined.**
- **Wafers:** `maskgen/core/wafers.py` (names, revisions, which config lays each revision out); the
  layout configs are `<project>/wafers/config_*.py`.
- **Interconnect designs and revisions:** `mea1k_interconnects/catalog.py`. A revision lists only
  what it changes against its parent: wafer4 Rev4 is wafer4 Rev3 plus its own list.
- **Probe designs:** `ufte_probes/electrode_bundle/design_sets.py`.
- **Mask layers:** `maskgen/core/layers.py`, one table for both projects.

## Wafers and revisions

A **wafer** is one mask set with its own devices and its own placement. Wafers are numbered across
both projects:

| Key | Name | Project | Revisions |
|---|---|---|---|
| `wafer4` | `MEA1K_wafer4_12x_8x_8x_breakoutboards` | interconnects | Rev2 (fabricated), Rev3, Rev4 (current) |
| `wafer5` | `MEA1K_W5_64Ch_4Shankdesigns` | probes | Rev1 (shipped) |
| `wafer6` | `MEA1K_wafer6_12x_8x_breakoutboards` | interconnects | Rev1 (in design) |

A **revision** keeps the wafer's layout and changes only some masks (wafer4 Rev2 → Rev3 → Rev4).
A new wafer starts again at Rev1. Its files carry the bare name; later revisions append `_Rev<N>`.
An interconnect wafer's first revision names the revision of another wafer it starts from: wafer6
Rev1 = wafer4 Rev4 + its own changes (`catalog.py`).

To start a new wafer:
1. add it to `maskgen/core/wafers.py`;
2. add its revisions to `catalog.REVISIONS` (interconnects) or its design set to `design_sets.py`
   (probes);
3. add a layout config in `<project>/wafers/` and name it in `wafers.py`.

## wafer4 Rev4 (current interconnect wafer)

Rev4 is Rev3 plus the following:
- every via sits 2.5 µm from the pad edge;
- every routing via gets a round Metal1 landing pad;
- the 12-block and the dummies get a 3.5 µm top-row via;
- the Metal3 squares are 2 µm smaller per side;
- the dummies get a small PI handle;
- the pad-perimeter stabilization vias move onto their own mask: wafer layer 1, with its alignment
  mark in the template's free slot.

The connector side stays identical to Rev3 / Rev2. Details: `mea1k_interconnects/HANDOVER.md` §4.6.

- **Rev3 band:** `build_designs.py --wafer wafer4 --rev 4` regenerates the Rev3 band reference it
  needs, into each device's `ref_rev3/`.
- **Output:** `<root>/MEA1K_wafer4_12x_8x_8x_breakoutboards/Rev4/MEA1K_wafer4_12x_8x_8x_breakoutboards_Rev4.{gds,oas,lyp}`.
  The layer names are stored in the OAS and the colours in the `.lyp` (`klayout x.oas -l x.lyp`).
- **Placements:** the Rev2 placements, measured once off the fabricated wafer and committed as
  `mea1k_interconnects/rev2_reference/wafer4_rev2_placements.json`.

## The fabricated wafer (Rev2)

`mea1k_interconnects/rev2_reference/MEA1K_wafer4_12x_8x_8x_breakoutboards_Rev2.gds` holds:
- 4 × 12-block;
- 4 × 8-block `51_35`;
- 4 × 8-block `71_15`;
- 26 dummies.

It is reproduced, geometry-identically, from the committed Rev2 design files next to it by the frozen scripts in `legacy/rev2_repro/`. That folder also recovers the
hand-placed dummies from the wafer.

The GDS layer numbers on the wafer are the hand-renumbered ones: Metal1 7, Polyimide_Negative +
EtchingPad 3, Etching + Via 5, Metal2 6. They are `IC_REV2` in `maskgen/core/layers.py`.

## Tests

```bash
cd ufte_probes && python3 -m pytest electrode_bundle/tests -q -p no:cacheprovider   # probe generator, ~4 min
MASKGEN_OUT=<root> python3 tests/golden/golden.py check                    # geometry vs 667ba2d, ~7 min
MASKGEN_OUT=<root> python3 tests/golden/golden.py check --only interconnect   # ~3 min
MASKGEN_OUT=<root> python3 tests/golden/golden.py check --only all            # + the legacy Rev2 reproduction
```

- **Probe suite.** The tests share one build per distinct configuration
  (`electrode_bundle/tests/_builds.py`), because a full probe build takes about 5 s.
- **Golden check.**
  - It rebuilds every interconnect, probe and wafer output in a copy of the working tree. It
    requires identical fingerprints, or per-layer XOR = 0, against commit 667ba2d.
  - The reference files live in `<root>/_golden/667ba2d/`. Do not delete them.
  - Run it after any change that could touch geometry. A change that is meant to change geometry
    becomes a new revision in the catalog, and the golden check is captured again from that commit.
