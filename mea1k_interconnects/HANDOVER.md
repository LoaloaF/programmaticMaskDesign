# mea1k_interconnects — handover

This folder generates the MEA1K interconnect DXFs in two stages:
- **Stage 01** builds the chip-side part: pad grid, per-column Metal1 routing, vias, polyimide.
- **Stage 02** routes its wire band into a Molex 227044 connector.

The output is the routed DXF in µm. The dummy's output is a GDS. `maskgen wafer wafer4_rev4` places
the outputs on the wafer (§1.1).

The five designs on the wafer:

| Design | What it is |
|---|---|
| 12-block `56_15` | 768 wires, two metal layers |
| 8-block `51_35` | 512 wires, one layer |
| 8-block `71_15` | the 8-block config with a few values overridden (§4.4) |
| dummy `61_10` | a 12-block pad grid whose wire band is shorted by a cap; an etch/release test structure, GDS output |
| dummy `35_36` | the dummy config with a few values overridden (§4.4) |

The defaults match the fabricated wafer, `../MEA1K_wafer4_12x_8x_8x_breakoutboards_Rev2.gds`.

**Where things are defined.**
- **Designs and revisions:** `catalog.py`.
- **How they combine:** `resolve.py`.
- **What a design and revision resolve to:** `python3 -m maskgen show --rev 4 --design 12block
  [--changed]` prints every setting and the step that set it.

**The revisions.**
- **Rev2:** the fabricated wafer. Its design files are committed in `designs/` and are read only by
  `legacy/rev2_repro/`.
- **Rev3:** changes only the pad stack (§4.5).
  - The final PI etch becomes via-sized circles around each pad's perimeter.
  - A new Metal3 layer repeats the pad squares.
- **Rev4:** the current revision (§4.6).

`build_designs.py` builds all five designs of any revision.

Inputs are in `assets/`, and outputs go under the maskgen output root (top-level README):
- `build_designs.py --rev N` writes to `<root>/interconnect/rev<N>/`.
- A single stage with a bare output name writes to `<root>/interconnect/adhoc/`.

Nothing is written into this folder.

## 1. Running

```bash
pip install ezdxf shapely numpy gdstk   # gdstk: the dummy (base conda python here; the `dxf` env lacks gdstk)

python3 build_designs.py --rev 4         # ALL five designs of a revision, stage 01 + 02 (~2 min)
python3 build_designs.py --rev 3 --stage01   # stage 01 only, into <root>/interconnect/rev3/draft/

# or one stage of one design, with the configs' defaults (the default variant of each family):
python3 00_preflight.py                  # env + pad-grid constraints (§4.2), no run
python3 01_generate.py --config 12block  # -> new_interconnect_circular_12Block_56_15.dxf
python3 02_route.py --design 12block     # -> new_interconnect_with_connector_chamfered_12Block_56_15.dxf
python3 01_generate.py --config 8block   # -> new_interconnect_circular_8block_51_35.dxf
python3 02_route.py --design 8block      # -> new_interconnect_with_connector_8blocks_chamfered_1layer_51_35.dxf
python3 01_generate.py --config dummy    # -> new_interconnect_circular_dummy_61_10.dxf
python3 02_make_dummy.py                 # -> dummy_new_interconnect_circular_dummy_61_10_padrows.gds
python3 03_inspect.py [file.dxf]         # per-layer entity/vertex census + bbox

python3 -m maskgen review --rev 3 4      # (from the repo root) Rev3 next to Rev4, feature by feature
```

**`build_designs.py`**
- It is the way to build a wafer's designs. WHAT it builds is `catalog.py`:
  - each design is its base config plus the overrides in `DESIGNS` (§4.4);
  - each revision is its parent revision plus the changes in `REVISIONS` (§4.5, §4.6).
- Every stage runs in its own process with the overrides applied in memory. No config is edited, and
  nothing leaks between designs.
- Output is in `<root>/interconnect/rev<N>/`. Files are named `*_rev<N>.*`, except that Rev2 keeps the
  shipped names. Logs go to `logs/`, and every run writes a `manifest.json`.
- It **fails** (exit 1) when a stage errors, when a router prints `crossings` or `pad_hits` ≠ 0, or
  when a router prints a `FAIL` line. The routers themselves only print those.

**Settings and options**
- Pad parameters are changed in the config file. Pad size, gap and via position depend on each
  other and are edited together (§4.4).
- Options:
  - `01_generate.py`: `--config 12block|dummy|8block` and `--out`;
  - `02_route.py`: `--design 12block|8block`, `--in` and `--out`;
  - `02_make_dummy.py`: `--in` and `--out`.
- Defaults come from the config, and each generator's default output is its stage 02's default
  input.
- Bare file names resolve to `<root>/interconnect/adhoc/`; absolute paths are used as given.
- The routers take no pad parameters; they measure the band off the input DXF.

**What a run looks like**
- Stage 01 takes ~1 s and stage 02 10–20 s. Each run writes exactly one DXF (the dummy: one GDS).
- A good stage-02 run prints:
  - `crossings=0, pad_hits=0`;
  - `phase-2 fan … : OK`;
  - the teardrop tally (§5);
  - the layer split: `Metal1(even)=384, Metal2(odd)=384` (12-block) or
    `Metal1=512, Metal2=0 (single layer)` (8-block).

### 1.1 The wafer

```bash
python3 -m maskgen wafer wafer4_rev4      # (repo root) Rev4 designs if missing, then the wafer
```

It places every Rev4 design at the Rev2 placements. Those were measured once off the fabricated
wafer and committed as `wafer_nesting/data/wafer4_rev2_placements.json`: the 12 connector devices and
the 26 dummies, which were placed by hand into the gaps and are never nested.
- **Writes:** `<root>/wafer/wafer4_rev4/wafer_wafer4_rev4.{gds,oas,lyp}`.
- **Then checks the wafer against Rev2.** The only allowed differences are the pad-stack layers,
  inside the pads, and the new mark.
- **Config:** `wafer_nesting/config_rev4.py`.
- **Code:** the shared `maskgen/wafer/rebuild.py`.
- **More:** `wafer_nesting/README.md`.

Frozen in `legacy/` (each with a README):
- **`legacy/rev2_repro/`** measures the placements off the fabricated wafer (01c) and rebuilds it
  `GEOMETRY IDENTICAL` (01d). It also holds the Rev3 wafer config and the Rev2/Rev3 overlay (01e).
  These scripts proved that `designs/` and the wafer agree cell by cell (XOR = 0), and that the
  wafer's connector cells sit −90 µm in y from their DXFs.
- **`legacy/interconnect_nest_2026-07/`** is the annealing nest of the connector designs. It was
  never shipped and uses the pre-renumbering layer numbers.

**Wafer layer numbers were changed by hand.** On the fabricated wafer the GDS layers were
renumbered manually to match the negative-etching markers. For the dummy the mapping is 1 → 3 and
3 → 7, with 5 and 6 unchanged. With it, both committed `designs/*_padrows.gds` are
polygon-for-polygon identical to the wafer's `dummy_61_10_NEW` / `dummy_35_36_NEW` cells.

Every revision's numbering, mark slots and colours are now one table, `maskgen/core/layers.py`:
- `IC_REV2`: the fabricated wafer;
- `IC_REV3`: + Metal3 on 8;
- `IC_REV4`: the stabilization vias on 1;
- `IC_DUMMY_GDS`: the dummy's own GDS layers.

The mark row sits at y = 0, x ≈ ±41…±46 mm:
- Layer 7 carries a reference vernier in each of five slots (`TEMPLATE_MARK_SLOTS`), from
  x ≈ ∓41.2 mm to ∓44.0 mm.
- Every other layer puts its own mark in one slot, and repeats the coarse marks at x ≈ ∓45.9 and
  ∓44.7 mm.
- `maskgen review --wafers wafer4_rev4 rev2_fabricated` shows every slot side by side.

## 2. Code layout

```
config_12block.py, config_8block.py   all design knobs of one design, in physical order:
                                        1 pad grid (GEN_*), 2 connector, 3 Phase 1,
                                        4 Phase 2, 5 teardrops, 6 polyimide, 7 derived
config_dummy.py                       dummy: 1 pad grid (GEN_*), 2 cap (DUMMY), layer map
config_common.py                      derived values shared by both designs
catalog.py                            DESIGNS (§4.4) and REVISIONS (§4.5, §4.6): what is built
resolve.py                            (design, revision) -> settings; `maskgen show` prints them
build_designs.py                      builds all five designs of a revision
00_preflight.py                       checks §4.2 against the configs
01_generate.py                        stage 01 for every design (--config 12block|dummy|8block);
                                        what differs: STRATEGIES at the top
02_route.py                           stage 02 for both connector designs (--design 12block|8block);
                                        what differs: ROUTE_STRATEGIES at the top
02_make_dummy.py                      stage 02 for the dummy: band cap + DXF -> GDS
03_inspect.py                         layer census
lib/active.py                         select() -> the design's settings as a Design object
lib/constants.py                      layer names, sampling resolutions
lib/teardrop.py                       teardrops, pad approach, angled entry, clearance clamp
lib/connector.py                      connector pads (CSV) and routing ranks
lib/board.py                          polyimide: board outline, neck / flare, bulbs
lib/phase1.py, lib/fanmath.py         band -> hand-off transition; eased fan profile + gap check
lib/geometry.py, lib/checks.py        helpers; crossings / pad_hits
lib/generate_helpers.py               stage-01 helpers
wafer_nesting/                        the Rev4 wafer config + the committed Rev2 placements (§1.1)
designs/                              the committed Rev2 design files (read by legacy/ only)
assets/                               inputs: connector pad CSV, board outlines, old interconnect
electrode_bundle_U4C08.dxf            reference for the teardrop profile (§5); not read
reference/WaferWith12_ALLNEW.gds      byte-identical copy of the fabricated Rev2 wafer; not read
```

**Config mechanism.** Each router calls `DSG = lib.active.select(design, in, out)`. `select`
loads `config_<design>.py`, `lib/constants.py` and the paths, and returns them as a read-only
`Design` object.
- **`lib` functions** take that object explicitly as their first argument and read
  `dsg.PADR`, `dsg.TEARDROP_CLEAR`, …. Fixed constants (resolutions, layer names) they import
  from `lib/constants.py`.
- **Caches** live on the design (`dsg.cache`, e.g. the connector pads).
- **Imports:** `lib` modules can be imported before `select`, and no `lib` module holds design
  state.
- **Router:** its own top-level code still reads the settings as globals through
  `from lib.active import *` (same values), so a process routes one design.

**One generator, one router.** `lib/` holds the helpers. `01_generate.py` and
`02_route.py` are each one script for all designs. Every difference is a named per-design switch at
the top (`STRATEGIES` / `ROUTE_STRATEGIES`), and each switch selects the former per-design script's
code unchanged, so the output is geometry-identical. Stage 01 differs in three ways:
- `drop_bottom_left`: the 12-block/dummy grid drops bottom pads; the 8-block keeps every row.
- `pair_shorts`: the 8-block's folded edge pads.
- `k0_via`: the 12-block/dummy Rev4 top-row vias.

Stage 02:

| switch | 12-block | 8-block |
|---|---|---|
| `fanout`, `two_layer` | fans the band out to `FAN_TARGET_PITCH`, lifts odd wires to Metal2 through vias (layer `Via`) | keeps native band x, stays on Metal1 |
| `band_ties` | — | drops the 16 pair-short ties at the band |
| (config) | 8 blocks from the CSV + 4 synthesised (`N_NEW_BLOCKS_PER_COLUMN`) | the 8 CSV blocks |
| `in_block` | `"rows"`: `route_single_*` | `"ckt"`: `route_blue_terminal*`, `route_green_single*` |
| `taper` | `"below_fan"`: widens below the fan (`taper_widths_along_path`) | `"neck"`: narrows 3.5 → 2 µm in the neck, above the fan-in |
| `polyimide_flare` | neck flares over the fan (`fanout_*`) | no flare |

Derived values (config section 7, `config_common.py`) keep the original expressions, so
float results are bit-identical to the pre-refactor code. Don't reorder that arithmetic.

## 3. New vs old interconnect

| | stage 01 | stage 02 |
|---|---|---|
| new (12/8-block) | pad grid + routing → `*_circular_*.dxf` | fan-in, optional via split, connector routing |
| old | none (opaque 31 MB asset derived from the wafer GDS) | appends connector routing |

## 4. Parameters

### 4.1 Primary knobs

Stage 01, `GEN_CONFIG` (config section 1):

| Knob | 12-block | 8-block | |
|---|---|---|---|
| `pad_side` | 58 µm | 53 µm | metal pad (Metal2), the bigger square; pitch = `pad_side + gap` |
| `pad_side_extra` | 56 µm | 51 µm | Rev2 etch opening (EtchingPad), the smaller square on the same centre; 1 µm metal rim. Only used with `pad_etch="square"` |
| `pad_etch` | `"vias"` | `"vias"` | final PI etch: `"square"` (Rev2) or perimeter circles (Rev3), §4.5 |
| `metal3` | `True` | `True` | also draw the pad squares on Metal3 (Rev3), §4.5 |
| `gap` | 13 µm | 33 µm | metal-to-metal gap between pads |
| `n_rows` × `n_cols` | 15 × 53 | 12 × 44 | both at the fit limit (§4.2) |
| `n_shorts_left/right` | — | 8 / 8 | each folds one pad into a neighbour's exit |

Stage 02 (config sections 2 and 5):

| Knob | 12-block | 8-block | |
|---|---|---|---|
| `TOTAL_WIRES` | 768 | 512 | connector capacity, 64 × blocks |
| `PADR` / `CONN_PAD_R` | 0.105 / 0.095 mm | same | metal pad + collision radius / `Polyimide_Negative` circle |
| `CONNECTOR_TW` / `GAP` | 3.5 / 0.5 µm | 2.0 / 3.0 µm | 12-block lanes alternate layers, so its same-layer gap is 2 × pitch − TW |
| `TEARDROP_FILLET_R_VERTICAL` / `_ANGLED` | 0.275 / 0.10 mm | same | §5 |
| `TEARDROP_CLEAR` | 20 µm | same | §5 |
| `PAD_APPROACH_TW_*` / `_LEN_*` | 30 µm / `None` (auto) | same | trace width at the pad = tear width |

The remaining knobs are tuned: Phase-1 fan and via geometry, `FAN2_*`, `FAN_LEN_SLACK`,
`CONN_TOP_BELOW_VIA_MM`, `ANGLED_ENTRY_*`, `CHAMFER_*`, board and bulb geometry, and stage 01's
`GEN_ROUTING` (wire/via geometry, tuned against fabrication limits). Layer names, sampling
resolutions, tolerances and the fixed stage-01 entries (layers, via shape) are in
`lib/constants.py`; each config merges the latter into its `GEN_*` dicts.

### 4.2 Constraints between stages

- **Wire count:** emitted wires = `TOTAL_WIRES`. 8-block: `n_rows·n_cols − shorts` =
  528 − 16. 12-block: `Σ_cols (n_rows − row_start(col))` = 795 − 27; the bottom pad is
  dropped on columns `0..n_cols//2`.
- **Fit limit:** a column's lanes must fit in one pad pitch,
  `max_rows = (pitch − w − g) // (w + g) + 1` with `w`, `g` from `GEN_ROUTING`.
- **Via staircase:** `via_offset_x + (n_rows − 1)·via_pitch + via_r ≤ pad_side`.

`00_preflight.py [--design 12block|8block|dummy]` evaluates all three from the configs
without generating anything, and warns if `via_offset_y` no longer puts the via in the pad's
top-left corner (`pad_side/2 − via_offset_x`).

### 4.3 Design names

Names describe the **etch openings**, not the metal:
`<pad_side_extra>_<gap + pad_side − pad_side_extra>` = opening side and the gap between
neighbouring openings. 12-block 58/56/13 → `56_15`;
8-block 53/51/33 → `51_35`; 8-block 73/71/13 → `71_15`; dummy 63/61/8 → `61_10`;
dummy 37/35/34 → `35_36`.

### 4.4 Wafer variants

The configs default to one variant per family. The wafer's other two differ only in the pad
grid, the via position (`via_offset_y` = `pad_side/2 − 2.75`, i.e. the via sits in the pad's
top-left corner) and the file names:

| Variant | `pad_side` | `pad_side_extra` | `gap` | `via_offset_y` | Pitch | `N_RIGHT_BULBS` |
|---|---|---|---|---|---|---|
| 8-block `51_35` (default) | 53 | 51 | 33 | 23.75 | 86 | 2 |
| 8-block `71_15` | 73 | 71 | 13 | 33.75 | 86 | 1 |
| dummy `61_10` (default) | 63 | 61 | 8 | 28.75 | 71 | — |
| dummy `35_36` | 37 | 35 | 34 | 15.75 | 71 | — |

The two 8-block variants also differ in the extraction-tab bulbs on the neck's right wall:
the fabricated `51_35` carries two, `71_15` one. Until 2026-10-07 the config had 1 for both,
so the shipped `51_35` DXF was missing a bulb against the wafer (found by legacy 01c, §1.1).

This table is `DESIGNS` in `catalog.py`: each design is its base config plus those
overrides, applied in memory, so the configs keep their defaults and nothing has to be edited
and reverted. `python3 build_designs.py --rev 2 --out-dir <dir>` rebuilds all five with Rev2
settings, geometry-identical to the files shipped in `designs/` (checked 2026-10-07, and by the
golden check since), and those match the wafer (proven by `legacy/rev2_repro/01c`: XOR = 0 per
cell). The `71_15` vias sit 33.75 µm from their pad centres in x and y, as in the wafer's
`CONN_SMALL_71_15_NEW` cell, and the dummy GDS files equal the wafer's dummy cells after the
layer remap (§1.1). Forgetting `via_offset_y` leaves the via off its corner: 23.75 on
`71_15` puts every via 10 µm low. Preflight warns about exactly that.

### 4.5 Pad stack per revision

`REVISIONS` in `catalog.py` sets the pad-stack knobs of `GEN_CONFIG` for every design (each revision
lists only what it changes against its parent: Rev3 = Rev2 + its list, Rev4 = Rev3 + its list):

| | Rev2 (fabricated) | Rev3 |
|---|---|---|
| `pad_etch` | `"square"`: one `pad_side_extra` square per pad on EtchingPad | `"vias"`: circles around the pad perimeter on Polyimide_Negative |
| `metal3` | `False` | `True`: the pad squares again on Metal3, no wires |
| output | `<root>/interconnect/rev2/` | `<root>/interconnect/rev3/*_rev3.*` |

**Rev3 etch vias** (`01_generate.py`; the layout is `etch_via_layout` in
`lib/generate_helpers.py`, the knobs are in `lib/constants.py`, `GEN_CONFIG_FIXED`).

- **Size and inset.** Each circle has the routing via's diameter (5 µm) and sits
  `etch_via_inset_extra` = 1 µm further in from the pad edge than the routing via: centre
  3.75 µm from the edge, 1.25 µm of metal between circle and edge. The routing via stays at
  2.75 µm (0.25 µm to the edge) and is not touched.
- **No via in a corner.** Each corner instead gets two neighbours, one on each adjacent side,
  the same distance `a` along the side from the corner point (3.75, 3.75 µm in). `a` is the
  smallest distance that keeps `etch_via_corner_gap` = 3 µm (edge to edge) between the
  neighbours and the routing via in the top-left corner, and between the two neighbours of a
  corner; it is the same at all four corners: **a = 6.938 µm** (the neighbours of one corner
  are then 4.81 µm apart, edge to edge).
- **Between the corner neighbours** each side gets k evenly spaced circles (k may be 0), the
  k whose spacing lies in `etch_via_spacing` = (15, 21) µm and is nearest its middle. One
  range for all designs. The upper bound is 21, not 20, only for the 63 µm dummy pad: one via
  between its neighbours gives 20.81 µm, two give 13.87 µm.

| Design | Pad (Metal2 = Metal3) | Per side | Between neighbours | Spacing (centres) | Circles per pad |
|---|---|---|---|---|---|
| 12-block | 58 µm | 3 | 1 | 18.31 µm | 12 |
| 8-block `51_35` | 53 µm | 3 | 1 | 15.81 µm | 12 |
| 8-block `71_15` | 73 µm | 4 | 2 | 17.21 µm | 16 |
| dummy `61_10` | 63 µm | 3 | 1 | 20.81 µm | 12 |
| dummy `35_36` | 37 µm | 2 | 0 | 15.62 µm | 8 |

Spacing = (`pad_side` − 7.5 − 2a) / (k + 1); circles per pad = 4 × (k + 2).
The generator stops if no k fits the range, if the corner neighbours leave no room, or if
any circle comes closer than the corner gap to its pad's routing via. The routers pass
Metal3 and the circles through untouched (they read Polyimide_Negative only as HATCH); the
dummy picks the circles up through `"Polyimide_Negative": (1, 0)` in `DUMMY_LAYER_MAP`, which
reads its LWPOLYLINEs only, not the band hatch. Against Rev2, every Rev3 design differs on
exactly these layers and nothing else.

To review a pad change before building everything:
`python3 build_designs.py --rev 3 --stage01` (stage 01 only, into `<root>/interconnect/rev3/draft/`).

The design names (§4.3) still describe the Rev2 etch squares.

### 4.6 Rev4

`REVISIONS[4]` in `catalog.py` (output `<root>/interconnect/rev4/*_rev4.*`). Rev4 is Rev3 plus the changes below.
Stage 02 builds around Rev3's wire band (`band_ref_rev=3`); `build_designs.py` regenerates that Rev3 stage-01 reference
itself (into `rev4/ref_rev3/`), so no separate Rev3 build is needed.

- **Every via 2.5 µm from the pad edge** (`via_edge_gap`): routing via and etch circles alike (centre 5 µm in). The routing
  via stays in the top-left corner; the etch circles lose their extra 1 µm inset and `etch_via_spacing` becomes (9.5, 21).
- **Metal1 landing pad** on every routing via (`m1_pad`): a 7 µm circle blended into the wire by a teardrop
  (`m1_tear_fillet_r` 14 µm, capped to the last straight run).
- **12-block and dummies:** the top pad of each column gets a 3.5 µm via on a 5.5 µm Metal1 pad, centred between the two
  lanes beside it (0.25 µm left); the lower pads' vias move 0.5 µm right. Metal1 then holds the 2.5 µm wire gap everywhere.
  (`per_design` in `REVISIONS[4]`.) The polyimide outline is trimmed 2.25 µm (`trim_xmax`) to stay where Rev3 had it.
- **Connector side unchanged.** The vias move the lane bundle by 2.0-2.25 µm; stage 02 therefore builds fan, layer-split
  vias, Metal2, connector pads and the polyimide neck around Rev3's band and jogs each wire onto it over `BAND_JOG_LEN` =
  300 µm. Below the jog the connector side is Rev3's, point for point; Metal2 does not change at all.
- **Metal3 squares 2 µm smaller per side** (`metal3_inset`; e.g. 12-block 58 µm Metal2 pad -> 54 µm Metal3).
- **Stabilization vias on their own layer** (`etch_via_layer` = `PI_Stab_Via`): the pad-perimeter circles leave
  Polyimide_Negative, so they are their own mask (wafer layer 1, etched first); the PI outline and the connector-pad
  openings stay on wafer layer 3. In the dummy GDS they are GDS layer 2 (`config_dummy.DUMMY_LAYER_MAP` = `maskgen/core/layers.py` `IC_DUMMY_GDS`).
- **Dummy handle** (dummies only, `REVISIONS[4]["dummy_set"]`): a 300 µm semicircular PI tab, 150 µm blended roots,
  at 62 % along the top edge (`config_dummy.handle_*`). 62 %, not the centre, because the free space above the top edge is
  smallest toward the left; checked on all 26 dummy placements (no overlap with another device's PI body, etch ring
  >= 125 µm from foreign device layers). Rev2/Rev3 builds have `handle_radius` = 0.

Rev2 and Rev3 builds are unchanged: the new knobs (`metal3_inset`, `etch_via_layer`, handle, ...) default to off.

## 5. Teardrops and angled entry (`lib/teardrop.py`)

Each connector pad is a teardrop matching the mating electrode bundle
(`electrode_bundle_U4C08.dxf`: pad r 110 µm, trace 10 µm, fillet r 275 µm). The outline is
the pad circle plus two concave fillet arcs, tangent externally to the pad and to the trace
edges; `teardrop_ring` reproduces the reference to 0.09 µm. The tip distance is derived:

```
L = sqrt((r + F)^2 - (w/2 + F)^2)     r = PADR, F = fillet radius, w = PAD_APPROACH_TW_*
```

- **Width coupling.** `teardrop_tip_width()` returns the same `PAD_APPROACH_TW_*` that
  `widen_pad_approach()` applies to the trace, so the tear and the trace share one width knob.
- **Hold extension.** `PAD_APPROACH_HOLD_*` is raised per pad to `max(hold, L / ramp_len)`,
  so the trace is at full width over the whole tear. Tip width and trace width agree to
  ≤ 1 µm on the emitted DXF.
- **Clearance clamp.** Each tear is shrunk by bisection on `L` until the metal it adds holds
  `TEARDROP_CLEAR` from foreign stroked copper (the emitted polygons, not centrelines). It is
  also capped at the trace's last straight run. With `TEARDROP_CLEAR_FLOOR_FROM_PAD`, a pad
  whose bare clearance is already below `TEARDROP_CLEAR` is held to that existing gap
  instead of being denied a tear (12-block: 192 such pads, tightest 9.2 µm; 8-block: 144,
  tightest 3.5 µm).
- **Angled fillet.** The angled rows use 0.10 instead of 0.275 because their entry angle
  drifts across a row (135° → 120°), and a 0.275 fillet (2.6 × `PADR`) flares far beyond a
  30 µm wire. 0.10 keeps the flare proportionate and the row uniform.

Result: 12-block 708 full / 60 shrunk / 0 plain; 8-block 468 / 44 / 0.

**Angled entry** (`ANGLED_ENTRY_*`). On the flanked rows — left column rows 1, 3, right
column rows 2, 4 (the right blocks are rotated 180°) — the final approach is one straight
segment from the feed lane into the pad. Each route tries 45° first and steepens only as far
as needed to clear every foreign pad and trace, so `crossings = 0, pad_hits = 0` holds by
construction. About a third reach 45° (12-block 120/384, 8-block 80/256); the rest are
steeper, medians 52–63°, max 68°. Four 8-block entries on L row 3 stay perpendicular.

## 6. Common problems

**Stage 02 raises in `extract_band_endpoints` after parsing the whole input.**
The wire count (§4.2) doesn't match `TOTAL_WIRES`. Changing `n_rows`/`n_cols` without
rebalancing the shorts (8-block) or `row_start` (12-block) causes it. `00_preflight.py`
catches it up front.

**Adding a row makes the column bundles overlap.** Both designs are at the fit limit
(15 ≤ 15, 12 ≤ 12); one more row needs a larger `gap` or `pad_side`.

**The 8-block Phase-2 fan flips to FAIL.** Its same-layer gap is +2.999 µm against a
3.00 µm target (`FAN_CHECK_TOL_UM` covers the discretisation); the 12-block has +3.458
against 2.50. Any change to `FAN2_*`, `FAN_LEN_SLACK`, `CONNECTOR_TW`, `GAP` or the wire
count can break it, and the failure is real — widen the fan.

**Tear and trace widths don't match.** `PAD_APPROACH_LEN_* = 0` (any falsy value) skips the
widening (`if _tw is not None and _rl:`), while the tear is still built at
`PAD_APPROACH_TW_*`. Keep it `None`.

**Editing `angled_pad_entry`.** The diagonal has to be tested against foreign *traces*, not
just pads: the sweep crosses the unmodified feed lanes of rows 2/4. Testing pads only gave
157 crossings; indexing every route's full geometry gives 0.

**Spacing violations the checks don't report.** `crossings`/`pad_hits` are centreline-only.
Tears are drawn after them and are invisible to them. Edge-to-edge copper spacing is checked
only by the teardrop clamp; `maskgen review` reports the smallest metal gap in each feature
window, which is a spot check, not a DRC.

- **Regression test:** the golden check (top-level README). It rebuilds every revision and
  requires per-layer XOR = 0 against commit 667ba2d. `--only all` also re-measures the fabricated
  wafer (legacy 01c/01d).
- **Changes that are *meant* to alter geometry:** make a new revision in `catalog.py` and compare
  it with `maskgen review --rev <old> <new> --xor`.

**Two runs never match byte for byte.** ezdxf stamps `$TDCREATE`/`$TDUPDATE`. Compare
geometry.

**ezdxf can't read a DXF back after KLayout saved it.** KLayout writes AC1006, and its
LWPOLYLINE mode produces files ezdxf can't parse. Go DXF → GDS one way only.

**The wafer stages under-cover old-interconnect designs** (`legacy/old_interconnect/`). Both old-interconnect outputs contain 4957 `POLYLINE`
entities inherited from `mea1k_interconnect_only.dxf` (`Metal` 1898, `Metal2` 1813,
`L3D0_etching2` 844, `L1D0_etching1` 402); the wafer stages read only `LWPOLYLINE`/`HATCH`.
Convert before nesting an old-interconnect design. The new interconnect emits only `LWPOLYLINE`/`HATCH`.

**The old-interconnect 8-block debug DXF differs between runs.** Pads are drawn by iterating a `set`
of layer names, so their order depends on the hash seed. Geometry is unchanged and
`_final.dxf` is unaffected; `PYTHONHASHSEED=0` makes it repeatable.
