# mea1k_interconnects — handover

> **Refactor Phase 1 (2026-11): generated files moved out of the repo.**
>
> Outputs now go under the output root: `$MASKGEN_OUT`, or the path written in `<repo>/.maskgen_out`
> (see the top-level README). Old paths map as follows:
>
> | old path | new path |
> |---|---|
> | `designs/rev<N>/` | `<root>/interconnect/rev<N>/` |
> | `wafer_nesting/runs/<tag>/` | `<root>/wafer/<tag>/` |
> | `64Ch_4Shankdesigns/shanks/` | `<root>/electrode/64Ch_4Shankdesigns/shanks/` |
> | `64Ch_4Shankdesigns/wafer/` | `<root>/wafer/64Ch_4Shankdesigns/` |
>
> - `mea1k_interconnects/designs/` keeps only the committed Rev2 inputs and is never written.
> - The Rev2 reproduction (`01c`, `01e`, `config_rev2`, `config_rev3`) moved to
>   `legacy/rev2_repro/`. Rev4 reads the committed placements
>   `mea1k_interconnects/wafer_nesting/data/wafer4_rev2_placements.json` instead.
> - Text below that still names an old path means the new one.
> - `python3 -m maskgen where` prints the folders.

Generates the MEA1K interconnect DXFs. Stage 01 builds the chip-side part (pad grid,
per-column Metal1 routing, vias, polyimide); stage 02 routes its wire band into a Molex
227044 connector. Output is the routed DXF in µm, which `wafer_nesting/` places on the wafer
(§1.1).

Designs, with defaults matching the fabricated wafer, which ships as
`reference/WaferWith12_ALLNEW.gds` (byte-identical to
`../MEA1K_wafer4_12x_8x_8x_breakoutboards_Rev2.gds`, which `wafer_nesting/config_rev2.py`
reads):
**12-block** `56_15` (768 wires, two metal layers), **8-block** `51_35` (512 wires, one
layer), and the **dummy** `61_10`, a 12-block pad grid whose wire band is shorted by a cap
(etch/release test structure, GDS output). The wafer's other two variants, 8-block `71_15`
and dummy `35_36`, are the same configs with a few values overridden (§4.4).

**Wafer revisions.** Rev2 is the fabricated wafer; its designs are in `designs/`. **Rev3**
changes only the pad stack (§4.5): the final PI etch becomes via-sized circles around each
pad's perimeter, and a new Metal3 layer repeats the pad squares. Its designs are in
`designs/rev3/`, its wafer is built at Rev2's exact placements (§1.1). `build_designs.py`
builds all five designs of either revision.
`old_interconnect/` holds the two old-interconnect routers, which append connector routing to a
fixed asset (`assets/mea1k_interconnect_only.dxf`) and cannot be re-parameterised.

Inputs are in `assets/`, outputs go to `designs/` (created on first run). `designs/` ships with
the fabricated (Rev2) set: the stage-01 and stage-02 files of all five wafer designs, generated
by `build_designs.py --rev 2` and proven against the wafer cell by cell (XOR = 0, by
`wafer_nesting/01c_extract_exact_placements.py`, §1.1). Rerunning them overwrites
these files with identical geometry. Paths are derived from file locations
(`HERE/ASSETS/DESIGNS` in `lib/active.py`; duplicated in `00_preflight.py` and `_HERE` in
each `old_interconnect/` script).

## 1. Running

```bash
pip install ezdxf shapely numpy gdstk matplotlib   # gdstk: dummy; matplotlib: old_interconnect/
# (here: the base conda python, /opt/anaconda3/bin/python3 -- the `dxf` env lacks gdstk)

python3 build_designs.py --rev 3         # ALL five designs of a revision, stage 01 + 02 (~1 min)
python3 build_designs.py --rev 2 --out-dir /tmp/rev2   # Rev2 settings: regression vs designs/

# or one stage of one design, with the configs' defaults (the default variant of each family):
python3 00_preflight.py                  # env + pad-grid constraints (§4.2), no run
# (bare output names land in <out root>/interconnect/adhoc/)
python3 01_generate.py --config 12block  # -> new_interconnect_circular_12Block_56_15.dxf
python3 02_route.py --design 12block     # -> new_interconnect_with_connector_chamfered_12Block_56_15.dxf
python3 01_generate.py --config 8block   # -> new_interconnect_circular_8block_51_35.dxf
python3 02_route.py --design 8block      # -> new_interconnect_with_connector_8blocks_chamfered_1layer_51_35.dxf
python3 01_generate.py --config dummy    # -> new_interconnect_circular_dummy_61_10.dxf
python3 02_make_dummy.py                 # -> dummy_new_interconnect_circular_dummy_61_10_padrows.gds
python3 03_inspect.py [file.dxf]         # per-layer entity/vertex census + bbox

python3 old_interconnect/02_route_old_12block.py         # minutes; no CLI; ~99 MB final + debug
python3 old_interconnect/02_route_old_8block_doubled.py
```

- `build_designs.py` is the way to build a wafer's designs. WHAT it builds is `catalog.py`:
  each design is its base config plus the overrides in `DESIGNS` (§4.4); each revision is its
  parent revision plus the changes in `REVISIONS` (§4.5, §4.6). HOW they combine is
  `resolve.py`, and `python3 -m maskgen show --rev 4 --design 12block [--changed]` prints every
  resolved setting with the step that set it (`python3 -m maskgen show` lists the revisions). Every
  stage runs in its own process with the overrides applied in memory, so no config is edited
  and nothing leaks between designs. Output: `<root>/interconnect/rev<N>/` (`*_rev<N>.*`; Rev2
  keeps the shipped names), logs in `<out-dir>/logs/`. It **fails** (exit 1) when a stage
  errors or a router prints `crossings`/`pad_hits` ≠ 0 or a `FAIL` line — the routers
  themselves only print those.
- Pad parameters are changed in the config file. Pad size, gap and via position depend on
  each other and are edited together (§4.4).
- Options: generators `--out` (plus `--config 12block|dummy` on the 12-block one); routers and
  `02_make_dummy.py` `--in --out`. Defaults come from the config, and each generator's
  default output is its stage 02's default input. Bare filenames resolve to `designs/`,
  absolute paths are used as given.
- The routers take no pad parameters; they measure the band off the input DXF.
- Stage 01 ~1 s, stage 02 10–20 s. Each run writes exactly one DXF (the dummy: one GDS).
- A good stage-02 run prints `crossings=0, pad_hits=0`, `phase-2 fan … : OK`, the teardrop
  tally (§5), and the layer split `Metal1(even)=384, Metal2(odd)=384` (12-block) or
  `Metal1=512, Metal2=0 (single layer)` (8-block).

### 1.1 wafer_nesting

`wafer_nesting/` lives inside this folder (`ROOT` = `mea1k_interconnects/`, `DESIGN_DIR` =
`designs/`). It has two paths, each driven by one config:

**Reproduce the fabricated wafer** — `config_rev2.py`, target
`../MEA1K_wafer4_12x_8x_8x_breakoutboards_Rev2.gds` (byte-identical to
`reference/WaferWith12_ALLNEW.gds`):

```bash
export WAFERNEST_CONFIG=config_rev2              # LEGACY -- legacy/rev2_repro/README.md
KL=/Applications/KLayout/klayout.app/Contents/MacOS/klayout
cd ..                                            # the repo root
$KL -b -r legacy/rev2_repro/01c_extract_exact_placements.py   # wafer -> <root>/wafer/wafer4_rev2/placements.json
$KL -b -r legacy/rev2_repro/01d_rebuild_wafer.py              # -> <root>/wafer/wafer4_rev2/wafer_wafer4_rev2.gds, then checks it
```

01c proves every part of the wafer comes from a source (each cell XOR = 0 against its file
in `designs/`, TOP's marks against the wafer template) and records all 38 placements
exactly — the 12 connectors and the 26 dummies, which were placed by hand into the gaps and
are never nested. 01d builds the wafer from that and ends with `GEOMETRY IDENTICAL`.
It is not byte-identical (GDS timestamps, writer record order); placement angles agree to
~1e-13 deg, i.e. KLayout's sine/cosine round trip, below anything the 1 nm grid can hold.
To put revised content at the same placements, point the cell's entry in `SOURCES`
(`config_rev2.py`) at the new file and run 01d alone. 01c also reports the wafer's one
duplicate placement: two `dummy_61_10_NEW` at (42.779, −9.325) mm, 31°.

**Build the Rev3 wafer** — `config_rev3.py`: the Rev2 placements, every cell swapped for its
Rev3 design, plus the template's layer-8 alignment marks for Metal3:

```bash
python3 build_designs.py --rev 3                                    # <root>/interconnect/rev3/
cd ..                                                               # LEGACY, from the repo root:
WAFERNEST_CONFIG=config_rev2 $KL -b -r legacy/rev2_repro/01c_extract_exact_placements.py
WAFERNEST_CONFIG=config_rev3 $KL -b -r legacy/rev2_repro/01d_rebuild_wafer.py   # -> <root>/wafer/wafer4_rev3/
WAFERNEST_CONFIG=config_rev3 $KL -b -r legacy/rev2_repro/01e_overlay_wafers.py  # Rev2 + Rev3 in one GDS
```

**Build the Rev4 wafer** — `wafer_nesting/config_rev4.py` (01d's default), from the committed Rev2
placements; §4.6 and `wafer_nesting/HANDOVER.md` §4.4:

```bash
python3 build_designs.py --rev 4                    # <root>/interconnect/rev4/
$KL -b -r wafer_nesting/01d_rebuild_wafer.py         # -> <root>/wafer/wafer4_rev4/
```

01d checks the Rev3 wafer against Rev2 and passes only if layers 3 and 8 are the only ones
that changed, each change inside the Metal2 pads (apart from TOP's new marks). 01e writes
both wafers into one GDS (Rev2 on datatype 0, Rev3 on 1, their XOR on 2) with a `.lyp`
naming the layers, for checking alignment by eye.

**Nest a new wafer** — `config.py`, the three routed connector DXFs, 4 copies each, seeded
from `wafer_nesting/reference_run_2026-07/`: `00_check_setup.py`, `01_extract_footprints.py`,
`02_nest.py`, then `03_export_wafer.py` / `04_verify_wafer.py` in KLayout. It nests the
connectors only — no dummies — and its `LAYER_MAP` is the pre-renumbering one (see below),
so its GDS does not land on the fabricated wafer's layers.

The wafer's sources in `designs/`: the three routed connector DXFs and the two
`dummy_*_padrows.gds`. The connector cells sit −90 µm in y from their DXFs; 01c measures
that. wafer_nesting reads only `LWPOLYLINE`/`HATCH` from a DXF, which is all the new
interconnect emits.

**Wafer layer numbers were changed by hand.** In `reference/WaferWith12_ALLNEW.gds` the GDS
layers were renumbered manually to match the negative-etching markers, so the numbering this
folder's scripts write is not the wafer's. For the dummy the mapping is 1 → 3 and 3 → 7
(5 and 6 unchanged): with it, both `*_padrows.gds` files in `designs/` are polygon-for-polygon
identical to the wafer's `dummy_61_10_NEW` / `dummy_35_36_NEW` cells. Check the numbering
against the reference wafer before placing anything on a new one.

The fabricated numbering, as `config_rev2.py` / `config_rev3.py` write it:

| Wafer layer | DXF layers | Dummy GDS layer | Alignment mark (template) |
|---|---|---|---|
| 7 | Metal1 | 3 | reference marks, all slots |
| 3 | Polyimide_Negative, EtchingPad (Rev3 etch circles are on Polyimide_Negative) | 1 | inverted, slot 4 |
| 5 | Etching, Via | 5 | inverted, slot 1 |
| 6 | Metal2 | 6 | small, slot 5 |
| 8 | Metal3 (Rev3 only) | 8 | small, slot 3 (Rev3 only) |
| 10 | — | — | wafer outline |

The mark row sits at y = 0, x ≈ ±41…±46 mm. Layer 7 carries a reference vernier in each of
five slots (slot 1 outermost, x ≈ ∓44.0 mm, to slot 5, x ≈ ∓41.2 mm); every other layer
puts its own mark in one slot and repeats the coarse marks at x ≈ ∓45.9 and ∓44.7 mm. The
template's layer 1 holds one more small mark (slot 2), still unused.

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
01_generate_{12block,8block}.py,      the former per-design scripts, now entry points that call the
02_route_{12block,8block}.py            merged ones (build_designs / catalog still name them)
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
old_interconnect/                     old-interconnect routers, standalone
wafer_nesting/                         wafer layout: nest a new wafer, or reproduce the
                                        fabricated one exactly (§1.1, wafer_nesting/HANDOVER.md)
electrode_bundle_U4C08.dxf            reference for the teardrop profile (§5); not read
reference/WaferWith12_ALLNEW.gds      the fabricated wafer the defaults match; not read
```

**Config mechanism.** Each router calls `DSG = lib.active.select(design, in, out)`. `select`
loads `config_<design>.py`, `lib/constants.py` and the paths, and returns them as a read-only
`Design` object.
- **`lib` functions** take that object explicitly as their first argument and read
  `dsg.PADR`, `dsg.TEARDROP_CLEAR`, …. Fixed constants (resolutions, layer names) they import
  from `lib/constants.py`.
- **Caches** live on the design (`dsg.cache`, e.g. the connector pads).
- **Imports:** `lib` modules can be imported before `select`, and no `lib` module holds design
  state (refactor Phase 4).
- **Routers:** their own top-level code still reads the settings as globals through
  `from lib.active import *` (same values); Phase 5 replaces that. Until then a process still
  routes one design.

**One generator, one router (refactor Phase 5).** `lib/` holds the helpers. `01_generate.py` and
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
so the shipped `51_35` DXF was missing a bulb against the wafer (found by 01c, §1.1).

This table is `DESIGNS` in `catalog.py`: each design is its base config plus those
overrides, applied in memory, so the configs keep their defaults and nothing has to be edited
and reverted. `python3 build_designs.py --rev 2 --out-dir <dir>` rebuilds all five with Rev2
settings, geometry-identical to the files shipped in `designs/` (checked 2026-10-07), and
those match the wafer (proven by `wafer_nesting/01c_extract_exact_placements.py`: XOR = 0 per
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
| output | `designs/` | `designs/rev3/*_rev3.*` |

**Rev3 etch vias** (`01_generate_*.py`; the layout is `etch_via_layout` in
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
only by the teardrop clamp. There is no DRC and no test suite. The closest thing to a
regression test: `python3 build_designs.py --rev 2` (rewrites `designs/`) and then
`wafer_nesting/01c_extract_exact_placements.py` with `WAFERNEST_CONFIG=config_rev2` — it XORs
every design against the fabricated wafer and fails on any change. For changes that are *meant* to alter geometry, compare DXFs before and
after (KLayout XOR, or the `03_inspect.py` census).

**Two runs never match byte for byte.** ezdxf stamps `$TDCREATE`/`$TDUPDATE`. Compare
geometry.

**ezdxf can't read a DXF back after KLayout saved it.** KLayout writes AC1006, and its
LWPOLYLINE mode produces files ezdxf can't parse. Go DXF → GDS one way only.

**wafer_nesting under-covers old-interconnect designs.** Both old-interconnect outputs contain 4957 `POLYLINE`
entities inherited from `mea1k_interconnect_only.dxf` (`Metal` 1898, `Metal2` 1813,
`L3D0_etching2` 844, `L1D0_etching1` 402); wafer_nesting reads only `LWPOLYLINE`/`HATCH`.
Convert before nesting an old-interconnect design. The new interconnect emits only `LWPOLYLINE`/`HATCH`.

**The old-interconnect 8-block debug DXF differs between runs.** Pads are drawn by iterating a `set`
of layer names, so their order depends on the hash seed. Geometry is unchanged and
`_final.dxf` is unaffected; `PYTHONHASHSEED=0` makes it repeatable.
