# legacy/interconnect_nest_2026-07 — the July 2026 interconnect nesting (frozen)

This folder is frozen. It is the seeded simulated-annealing nest of the interconnect designs from
July 2026, run before the wafer was laid out by hand and fabricated. `HANDOVER.md` here is the
original documentation, also covering the Rev2 reproduction now in `../rev2_repro/`.

Why it is frozen rather than maintained:
- **It uses the layer numbers from BEFORE the fabricated wafer's hand renumbering.** The fabricated
  numbering is `maskgen/core/layers.py`.
- **It never shipped.** The fabricated wafer used hand-placed positions, which Rev4 reuses
  (`mea1k_interconnects/wafer_nesting/`).

The annealing nester itself is maintained in `maskgen/wafer/nest_sa.py` as a placement backend;
this folder keeps the copy it ran with (`02_nest.py`).

**What changed when it moved here (refactor Phase 6):** paths only.
- `ROOT`, the template and the marks file now point at `mea1k_interconnects/rev2_reference/` and
  `maskgen/wafer/data/`.
- `config_common` writes under the output root without importing `maskgen`.

Run it from the repo root with `WAFERNEST_CONFIG=config` and the scripts here, in the order
00 → 01 → 02 → 03 → 04.
