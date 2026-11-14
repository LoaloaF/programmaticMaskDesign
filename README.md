# programmaticMaskDesign

This repo generates the photomask layouts for the MEA1K flexible-electronics wafers: the devices
themselves, and how they are arranged on a 100 mm wafer. Everything is generated from parameters.
Nothing on a wafer is drawn by hand, except where noted.

```
programmaticMaskDesign/
  maskgen/                  SHARED code, and the `maskgen` command
    core/                   paths.py (output root), manifest.py (run records),
                            layers.py (EVERY mask layer: DXF names, fab numbers, names, colours)
    wafer/                  wafer layout for both projects: nesters, export, marks, verify, rebuild
    review/                 review sheets: named features side by side
  mea1k_interconnects/      MEA1K chip <-> Molex connector interconnects (HANDOVER.md)
    catalog.py              every design and every wafer revision
    build_designs.py        all five designs of a revision
    wafer_nesting/          the interconnect wafer config (Rev4 at the Rev2 placements)
  ufte_probes/              flexible 64-channel electrode probes (README.md)
    electrode_bundle/       the generator (HANDOVER.md, tests/)
    wafer_nesting/          the probe wafer config
  legacy/                   FROZEN, runnable, never imports maskgen (see each README)
    rev2_repro/             reproduces the fabricated Rev2 wafer; the Rev2/Rev3 overlay
    interconnect_nest_2026-07/   the July 2026 annealing nest of the interconnects
    old_interconnect/       routers for the pre-circular-pad interconnect
  tests/golden/             regression harness: geometry must stay identical to commit 667ba2d
  MEA1K_wafer4_12x_8x_8x_breakoutboards_Rev2.gds   the fabricated interconnect wafer (Rev2)
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

| What | Folder | Written by |
|---|---|---|
| interconnect designs | `<root>/interconnect/rev<N>/` (+ `logs/`, `ref_rev3/`, `draft/` for `--stage01`) | `mea1k_interconnects/build_designs.py --rev N` |
| electrode designs | `<root>/electrode/<set>/shanks/` | `cd ufte_probes && python3 -m electrode_bundle.batch` |
| wafers | `<root>/wafer/<tag>/` | `maskgen wafer <tag>` |
| review sheets | `<root>/review/<name>/` | `maskgen review ...` |

Every run folder gets a `manifest.json` with:
- the git commit;
- the settings;
- the md5 of every input and output.

`<root>/index.jsonl` lists all runs, and every command ends by printing what it wrote, and where.

## The commands

```bash
maskgen where                          # the root, its layout, the last runs
maskgen where interconnect 4           # one folder + what its last run wrote (also: electrode, wafer, review)
maskgen runs                           # the run index

maskgen show                           # interconnect revisions: chain, status, what each changes
maskgen show --rev 4 --design 12block --changed   # one design's resolved settings, and who set them

maskgen wafer                          # the wafers it knows
maskgen wafer wafer4_rev4              # Rev4 designs if missing, then the wafer, checked against Rev2
maskgen wafer 64Ch_4Shankdesigns       # probe designs if missing, nest if none yet, export, verify, map

maskgen review --rev 3 4               # every interconnect design, Rev3 next to Rev4, feature by feature
maskgen review --probes                # the electrode geometries side by side
maskgen review --wafers wafer4_rev4 rev2_fabricated 64Ch_4Shankdesigns   # alignment-mark slots
maskgen review --rois                  # the named features
```

**Designs.**
- Interconnect designs: `cd mea1k_interconnects && python3 build_designs.py --rev 4`. It takes
  about 2 minutes and builds all five designs.
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
| `maskgen/wafer/` | wafer layout for both | [`ufte_probes/wafer_nesting/README.md`](ufte_probes/wafer_nesting/README.md) (nested wafers), [`mea1k_interconnects/wafer_nesting/README.md`](mea1k_interconnects/wafer_nesting/README.md) (fixed placements) |

**Where things are defined.**
- **Interconnect designs and revisions:** `mea1k_interconnects/catalog.py`. A revision lists only
  what it changes against its parent: Rev4 is Rev3 plus its own list.
- **Probe designs:** `ufte_probes/electrode_bundle/design_sets.py`.
- **Mask layers:** `maskgen/core/layers.py`, one table for both projects.
- **Wafers:** `maskgen/wafer/specs.py`.

## Rev4 (current interconnect wafer)

Rev4 is Rev3 plus the following:
- every via sits 2.5 µm from the pad edge;
- every routing via gets a round Metal1 landing pad;
- the 12-block and the dummies get a 3.5 µm top-row via;
- the Metal3 squares are 2 µm smaller per side;
- the dummies get a small PI handle;
- the pad-perimeter stabilization vias move onto their own mask: wafer layer 1, with its alignment
  mark in the template's free slot.

The connector side stays identical to Rev3 / Rev2. Details: `mea1k_interconnects/HANDOVER.md` §4.6.

- **Rev3 band:** `build_designs.py --rev 4` regenerates the Rev3 band reference it needs, into
  `rev4/ref_rev3/`.
- **Output:** `<root>/wafer/wafer4_rev4/wafer_wafer4_rev4.{gds,oas,lyp}`. The layer names are
  stored in the OAS and the colours in the `.lyp` (`klayout x.oas -l x.lyp`).
- **Placements:** the Rev2 placements, measured once off the fabricated wafer and committed as
  `mea1k_interconnects/wafer_nesting/data/wafer4_rev2_placements.json`.

## The fabricated wafer (Rev2)

`MEA1K_wafer4_12x_8x_8x_breakoutboards_Rev2.gds` holds:
- 4 × 12-block;
- 4 × 8-block `51_35`;
- 4 × 8-block `71_15`;
- 26 dummies.

It is reproduced, geometry-identically, from `mea1k_interconnects/rev2_reference/` (the committed Rev2
design files) by the frozen scripts in `legacy/rev2_repro/`. That folder also recovers the
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
  - The reference files live in `<root>/golden/667ba2d/`. Do not delete them.
  - Run it after any change that could touch geometry. A change that is meant to change geometry
    becomes a new revision in the catalog, and the golden check is captured again from that commit.
