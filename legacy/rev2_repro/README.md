# legacy/rev2_repro — reproducing the fabricated Rev2 wafer

This folder is frozen. It reproduces `MEA1K_wafer4_12x_8x_8x_breakoutboards_Rev2.gds` (repo root)
from the committed Rev2 designs (`mea1k_interconnects/designs/`), and it is the only place
where the hand-placed dummies are recovered from the fabricated wafer.

It was moved out of `mea1k_interconnects/wafer_nesting/` in refactor Phase 1 (from commit 667ba2d).
The main workflow no longer depends on it:

- Rev4 builds from the placements this folder measured once. They are committed as
  `mea1k_interconnects/wafer_nesting/data/wafer4_rev2_placements.json`, with `<ROOT>` written for
  the repo root.
- That data file is what `maskgen/wafer/rebuild.py` reads (config `config_rev4`, `python3 -m maskgen wafer wafer4_rev4`).

## Run

```bash
export MASKGEN_OUT=/path/outside/the/repo   # or: echo /path > <repo>/.maskgen_out
KL=/Applications/KLayout/klayout.app/Contents/MacOS/klayout
cd <repo>
WAFERNEST_CONFIG=config_rev2 $KL -b -r legacy/rev2_repro/01c_extract_exact_placements.py
WAFERNEST_CONFIG=config_rev2 $KL -b -r legacy/rev2_repro/01d_rebuild_wafer.py   # ends GEOMETRY IDENTICAL
```

Outputs go to `$MASKGEN_OUT/wafer/wafer4_rev2/`: `placements.json` and `wafer_wafer4_rev2.gds`.

**Rev3 wafer, and the Rev2/Rev3 overlay** (`01e`): run `build_designs.py --rev 3` first, then the
same commands with `config_rev3`.

## What changed against the originals

| file | change |
|---|---|
| `01c_extract_exact_placements.py`, `01e_overlay_wafers.py` | none (`git mv`) |
| `01d_rebuild_wafer.py`, `exact_io.py` | none (copies taken at 667ba2d) |
| `config_rev2.py` | the PATHS block only. Inputs are still read from `mea1k_interconnects/`; `OUT_DIR` is under the output root. `_out_root()` is a stdlib copy of the rule in `maskgen/core/paths.py`. |
| `config_rev3.py` | `REV3_DIR` and `OUT_DIR` are under the output root |
| all | folder names in comments/paths follow the repo renames (`InterconnectGeneration` → `mea1k_interconnects`, `WaferNesting` → `wafer_nesting`, `generative_probe_design` → `ufte_probes`) |

**Nothing here imports `maskgen`.** Later refactor phases therefore cannot change what this folder
builds.

**Proof it still works:** `tests/golden/golden.py check` runs 01c and 01d here and requires three
things:
- `GEOMETRY IDENTICAL`;
- the same wafer fingerprint as at 667ba2d;
- a `placements.json` equal to the frozen copy.
