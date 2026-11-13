# programmaticMaskDesign

This repo generates the photomask layouts for the MEA1K flexible-electronics wafers: the devices
themselves, and how they are arranged on a 100 mm wafer. Everything is generated from parameters.
Nothing on a wafer is drawn by hand, except where noted.

```
programmaticMaskDesign/
  maskgen/                  SHARED code for both projects: output root, run manifests, CLI,
                            core/layers.py = EVERY mask layer (DXF names, fab numbers, .oas names,
                            KLayout colours); later also wafer nesting/export, review sheets
  mea1k_interconnects/      MEA1K chip <-> Molex connector interconnects
    HANDOVER.md
    build_designs.py        all five wafer designs of a revision (Rev2, Rev3, Rev4)
    review/                 Rev3-vs-Rev4 comparison figures
    wafer_nesting/          interconnect wafers: Rev4 at the Rev2 placements, nesting
      HANDOVER.md
  ufte_probes/              flexible 64-channel electrode probes (UFTE)
    README.md
    electrode_bundle/       the generator (+ HANDOVER.md, tests/)
    wafer_nesting/          probe wafers: count-maximising nest, mark inversion, wafer map
  legacy/rev2_repro/        FROZEN: reproduces the fabricated Rev2 wafer (README.md there)
  tests/golden/             regression harness: every refactor step must keep the geometry identical
  MEA1K_wafer4_12x_8x_8x_breakoutboards_Rev2.gds   the fabricated interconnect wafer (Rev2)
```

| Project | Makes | Start with |
|---|---|---|
| `mea1k_interconnects/` | the 12-block / 8-block interconnect DXFs and the dummy test structures | [`mea1k_interconnects/HANDOVER.md`](mea1k_interconnects/HANDOVER.md) |
| `mea1k_interconnects/wafer_nesting/` | interconnect wafers | [`mea1k_interconnects/wafer_nesting/HANDOVER.md`](mea1k_interconnects/wafer_nesting/HANDOVER.md) |
| `ufte_probes/` | electrode-probe DXFs from design specs, and their wafers | [`ufte_probes/README.md`](ufte_probes/README.md) |

## Where outputs go

Generated files **never** go inside the repo. Everything is written under one **output root**, set
once:

```bash
export MASKGEN_OUT=/path/outside/the/repo            # per shell, or once per checkout:
echo /path/outside/the/repo > .maskgen_out            # (git-ignored; KLayout started from the Dock sees this too)
```

| What | Folder | Written by |
|---|---|---|
| interconnect designs | `<root>/interconnect/rev<N>/` (+ `logs/`, `ref_rev3/`, `draft/` for `--stage01`) | `build_designs.py --rev N` |
| electrode designs | `<root>/electrode/<set>/shanks/` | `python3 -m electrode_bundle.batch` |
| wafers | `<root>/wafer/<run tag>/` (e.g. `wafer4_rev4`, `64Ch_4Shankdesigns`) | `01d_rebuild_wafer.py`, electrode `nest_tiler.py` / `03_export_wafer.py` |
| comparison figures | `<root>/review/<name>/` | `mea1k_interconnects/review/*.py` |

- Every run folder gets a `manifest.json` recording the git commit, the settings, and the md5 of
  every input and output.
- `<root>/index.jsonl` lists all runs.
- Every build ends by printing what it wrote, and where.

To find a folder and its last run:

```bash
python3 -m maskgen where                       # the root, the layout, the last runs
python3 -m maskgen where interconnect 4        # one folder + what its last run wrote
python3 -m maskgen where wafer wafer4_rev4
python3 -m maskgen runs
```

## Tests

```bash
cd ufte_probes && python3 -m pytest electrode_bundle/tests -q        # probe generator, ~3 min, all pass
python3 tests/golden/golden.py check                                  # everything vs 667ba2d, ~15 min
```

- **Probe suite.** The probe tests share one build per distinct configuration
  (`electrode_bundle/tests/_builds.py`), because a full probe build takes about 5 s.
- **Golden check.** Rebuilds every interconnect, probe and wafer output and requires per-layer
  XOR = 0 against commit 667ba2d.

## Rev4 (current)

Rev4 is Rev3 plus the following:
- every via sits 2.5 µm from the pad edge;
- every routing via gets a round Metal1 landing pad;
- the 12-block and the dummies get a 3.5 µm top-row via;
- the Metal3 squares are 2 µm smaller per side;
- the dummies get a small PI handle;
- the pad-perimeter stabilization vias move onto their own mask: wafer layer 1, with its alignment
  mark in the template's free slot.

The connector side stays identical to Rev3 / Rev2. Details:
`mea1k_interconnects/HANDOVER.md` §4.6 and `mea1k_interconnects/wafer_nesting/HANDOVER.md` §4.4.

Every design and every revision is defined in `mea1k_interconnects/catalog.py`. Each revision
lists only what it changes against its parent (Rev4 = Rev3 + its list). To see a revision or one
design's resolved settings:

```bash
python3 -m maskgen show                                   # Rev2 / Rev3 / Rev4: chain, status, changes
python3 -m maskgen show --rev 4 --design 12block --changed   # what the design + revisions change, and who
```

```bash
cd mea1k_interconnects
python3 build_designs.py --rev 4                 # <root>/interconnect/rev4/
KL=/Applications/KLayout/klayout.app/Contents/MacOS/klayout
$KL -b -r wafer_nesting/01d_rebuild_wafer.py      # config_rev4 (default); checked against Rev2
```

`build_designs.py --rev 4` regenerates the Rev3 band reference it needs into `rev4/ref_rev3/`
itself, so no separate Rev3 build is needed.

- **Output:** `<root>/wafer/wafer4_rev4/wafer_wafer4_rev4.{gds,oas,lyp}`. The layer names are stored
  in the OAS; the colours are in the `.lyp` (open with `klayout x.oas -l x.lyp`).
- **Placements:** Rev4 uses the Rev2 placements, measured once off the fabricated wafer and committed
  as `mea1k_interconnects/wafer_nesting/data/wafer4_rev2_placements.json`.

## The fabricated wafer (Rev2), legacy

`MEA1K_wafer4_12x_8x_8x_breakoutboards_Rev2.gds` holds:
- 4 × 12-block;
- 4 × 8-block `51_35`;
- 4 × 8-block `71_15`;
- 26 dummies.

It is reproduced, geometry-identically, from `mea1k_interconnects/designs/` by the frozen scripts
in `legacy/rev2_repro/` (see the README there). That folder is also where the hand-placed dummies are
recovered from the wafer. Rev3 (perimeter etch vias + Metal3) and the Rev2/Rev3 overlay live there
too.

The GDS layer numbers on the wafer are the hand-renumbered ones: Metal1 7, Polyimide_Negative +
EtchingPad 3, Etching + Via 5, Metal2 6. See `mea1k_interconnects/wafer_nesting/HANDOVER.md` §4.2.

## Status: being merged into one package

A refactor into one `maskgen` package is under way, phase by phase. Each phase must pass
`python3 tests/golden/golden.py check`, which rebuilds everything and requires per-layer XOR = 0
against commit 667ba2d.

The overlaps it removes:
- **Two wafer_nesting forks.** Their setup and footprint stages are near-identical. The interconnect
  fork has the seeded annealer and the exact-reproduce path; the probe fork has the count-maximising
  nester, mark inversion and the wafer map.
- **Duplicated geometry helpers, and two teardrop implementations.**
- **Two config styles:** dataclasses in `ufte_probes`, module globals loaded by
  `lib/active.select()` in `mea1k_interconnects`.

**Environment**
- Python packages are in the base conda python (`/opt/anaconda3/bin/python3`, which has gdstk for
  the dummy; the `dxf` env lacks it).
- The KLayout 0.29 app runs every GDS stage.
- Pinned versions are in `pyproject.toml`.
