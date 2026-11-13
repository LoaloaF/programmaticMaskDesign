# mea1k_interconnects/wafer_nesting — the interconnect wafer configs

The wafer **stages** are shared with the probe project and live in `maskgen/wafer/`; see
`maskgen/wafer/__init__.py` for the list. This folder holds only what is specific to the MEA1K
interconnect wafer.

| file | what |
|---|---|
| `config_rev4.py` | **the current wafer**: the Rev4 designs at the fabricated Rev2 wafer's exact positions |
| `data/wafer4_rev2_placements.json` | those positions, measured once off the fabricated wafer (`legacy/rev2_repro`, 01c); `<ROOT>` = the repo root |

Build it:

```bash
python3 -m maskgen wafer wafer4_rev4
```

That builds the Rev4 designs if they are missing (`build_designs.py --rev 4`), then runs
`maskgen/wafer/rebuild.py`. The rebuild places every cell exactly as recorded and checks the result
against the fabricated Rev2 wafer. It passes only if every difference is on an expected layer
(1, 3, 5, 7, 8) and lies inside the pads.

- **Output:** `<root>/wafer/wafer4_rev4/wafer_wafer4_rev4.{gds,oas,lyp}`. Layer names, colours and
  order come from `maskgen/core/layers.py`, layer set `IC_REV4`.
- **Background:**
  - what each revision changes: `../HANDOVER.md` §4.6;
  - the alignment marks: `maskgen/wafer/marks.py`;
  - the history of this wafer (the Rev2 reproduction, and the July 2026 nesting that preceded the
    fabricated layout): `legacy/rev2_repro/` and `legacy/interconnect_nest_2026-07/HANDOVER.md`.
