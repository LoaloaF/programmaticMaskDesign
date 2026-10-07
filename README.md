# programmaticMaskDesign

Code that generates photomask layouts for the MEA1K flexible-electronics wafers: the
devices themselves, and their arrangement on a 100 mm wafer. Everything is generated from
parameters; nothing on a wafer is drawn by hand except where noted.

```
programmaticMaskDesign/
  MEA1K_wafer4_12x_8x_8x_breakoutboards_Rev2.gds   the fabricated interconnect wafer (Rev2)
  InterconnectGeneration/      MEA1K chip <-> Molex connector interconnects
    HANDOVER.md
    WaferNesting/              interconnect wafers: nest, or reproduce Rev2 exactly
      HANDOVER.md
  generative_probe_design/     flexible 64-channel electrode probes
    README.md
    electrode_bundle/HANDOVER.md
    WaferNesting/README.md     probe wafers: count-maximising nest, mark inversion, wafer map
```

| Project | Makes | Start with |
|---|---|---|
| `InterconnectGeneration/` | the 12-block / 8-block interconnect DXFs and the dummy test structures | [`InterconnectGeneration/HANDOVER.md`](InterconnectGeneration/HANDOVER.md) |
| `InterconnectGeneration/WaferNesting/` | interconnect wafers; rebuilds the Rev2 wafer geometry-identically | [`InterconnectGeneration/WaferNesting/HANDOVER.md`](InterconnectGeneration/WaferNesting/HANDOVER.md) |
| `generative_probe_design/` | electrode-probe DXFs from design specs, and their wafers | [`generative_probe_design/README.md`](generative_probe_design/README.md) |

## The fabricated wafer

`MEA1K_wafer4_12x_8x_8x_breakoutboards_Rev2.gds` — 4 × 12-block, 4 × 8-block `51_35`,
4 × 8-block `71_15`, 26 dummies. It is reproduced from `InterconnectGeneration/designs/`:

```bash
cd InterconnectGeneration
export WAFERNEST_CONFIG=config_rev2
KL=/Applications/KLayout/klayout.app/Contents/MacOS/klayout
$KL -b -r WaferNesting/01c_extract_exact_placements.py
$KL -b -r WaferNesting/01d_rebuild_wafer.py      # ends with GEOMETRY IDENTICAL
```

The GDS layer numbers on it are the hand-renumbered ones (Metal1 7, Polyimide_Negative +
EtchingPad 3, Etching + Via 5, Metal2 6); see `InterconnectGeneration/WaferNesting/HANDOVER.md` §4.2.

## Status: two projects, being merged

The two projects grew separately and overlap:

- **two WaferNesting forks** — near-identical setup and footprint stages; the interconnect
  fork has the seeded annealer and the exact-reproduce path, the probe fork the
  count-maximising nester, mark inversion and wafer map. They also disagree on alignment-mark
  polarity, which a merged version has to make a per-wafer setting.
- **duplicated geometry helpers** (`stroke_centerline_to_polygon`, circles, rectangles,
  DXF I/O) copied from a common ancestor, and two implementations of the same tangent-fillet
  **teardrop** pad (InterconnectGeneration's `lib/teardrop.py` is the more complete one).
- **different config styles**: dataclasses (`generative_probe_design`) vs module globals
  loaded by `lib/active.select()` (`InterconnectGeneration`).

Environment: Python packages in the `dxf` conda env
(`source /opt/anaconda3/etc/profile.d/conda.sh && conda activate dxf`); KLayout 0.29 app for
every GDS stage. The folder is a git repository; generated output (`WaferNesting/runs/`, `64Ch_4Shankdesigns/`) is ignored.
