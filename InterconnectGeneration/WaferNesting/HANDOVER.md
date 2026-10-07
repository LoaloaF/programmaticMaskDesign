# WaferNesting (interconnect) — handover

Puts MEA1K interconnect designs onto a 100 mm wafer and writes the combined GDS. It has
**two paths**, and they answer different questions:

| Path | Question | Stages | Config |
|---|---|---|---|
| **nest** | "arrange these N pieces legally on a new wafer" | `00` → `01` → `02` → `03` → `04` | `config.py` |
| **reproduce** | "rebuild this existing wafer, exactly, from its sources" | `01c` → `01d` | `config_rev2.py` |
| **revise** | "the same wafer, with revised designs at the same placements" | `01c` → `01d` → `01e` | `config_rev3.py` |

The reproduce path is what the fabricated wafer is maintained with: the Rev2 wafer is
rebuilt from `../designs/` geometry-identically (§4.2). New wafer revisions that keep the
layout start there, not from a fresh nest: Rev3 (§4.3) is Rev2's placements with every cell
swapped for its Rev3 design, checked against Rev2 layer by layer.

Every stage is driven by one config file, chosen with `WAFERNEST_CONFIG`; the stage scripts
hold no paths, clearances or layer numbers of their own. The wafer template and alignment
marks ship inside this folder.

> This is one of **two** WaferNesting forks. The other, in
> `../../generative_probe_design/WaferNesting/`, nests electrode probes: it has a
> count-maximising skyline nester, dark-field mark inversion and a wafer map, but no
> reproduce path. They share 00/01/footprint code almost line for line. §6.3.

---

## 0. Where it sits

```
programmaticMaskDesign/
  MEA1K_wafer4_12x_8x_8x_breakoutboards_Rev2.gds   <- the fabricated wafer (reproduce target)
  InterconnectGeneration/                          <- ROOT for every config here
    designs/                                       <- DESIGN_DIR: the routed DXFs + dummy GDSs
    WaferNesting/                                  <- this folder
      00_check_setup.py … 04_verify_wafer.py       <- nest path
      01c_extract_exact_placements.py              <- reproduce path
      01d_rebuild_wafer.py
      01e_overlay_wafers.py                        <- old + new wafer in one GDS
      exact_io.py                                  <- shared by 01c and 01d
      config.py, config_rev2.py, config_rev3.py, config_common.py
      config_electrode_bundle.py                   <- legacy, see §6.4
      footprint_extract.py
      new_wafer_actually.GDS, marks_union.wkt      <- wafer template + marks
      reference_run_2026-07/                       <- the 2026-07 nest seed. Do not edit
      runs/<RUN_TAG>/                              <- all output
```

Run from `InterconnectGeneration/`. Paths are anchored to the files themselves, so the
working directory does not actually matter; that is just the one the commands below use.

## 1. Quickstart

```bash
pip install ezdxf shapely numpy matplotlib        # stages 00-02 (conda env `dxf`)
KL=/Applications/KLayout/klayout.app/Contents/MacOS/klayout   # stages 01c, 01d, 03, 04
```

KLayout's own Python runs the GDS stages (`$KL -b -r <script>`); the `klayout` pip module
is not used. Those scripts and every config they import must stay pure standard library +
`pya` (gotcha 8).

### 1.1 Reproduce the fabricated wafer

```bash
export WAFERNEST_CONFIG=config_rev2
$KL -b -r WaferNesting/01c_extract_exact_placements.py   # ~10 s
$KL -b -r WaferNesting/01d_rebuild_wafer.py              # ~2 min, mostly the final check
```

01d must end with `GEOMETRY IDENTICAL`. To put revised content at the same placements,
use a revision config (§1.2), not an edited `config_rev2.py`.

### 1.2 Build the Rev3 wafer

```bash
python3 build_designs.py --rev 3                                     # ../designs/rev3/
WAFERNEST_CONFIG=config_rev2 $KL -b -r WaferNesting/01c_extract_exact_placements.py
WAFERNEST_CONFIG=config_rev3 $KL -b -r WaferNesting/01d_rebuild_wafer.py    # ~2 min
WAFERNEST_CONFIG=config_rev3 $KL -b -r WaferNesting/01e_overlay_wafers.py   # ~4 min
```

01d must end with `IDENTICAL to MEA1K_wafer4_…_Rev2.gds except the expected changes on
3/0, 8/0`. 01e writes `runs/wafer4_rev3/overlay_wafer4_rev3.gds` + `.lyp`: Rev2 on
datatype 0, Rev3 on 1, their flattened XOR on 2 (open with `klayout <gds> -l <lyp>`).

### 1.3 Nest a new wafer

```bash
export WAFERNEST_CONFIG=config        # the default when unset
python3 WaferNesting/00_check_setup.py          # resolves every path, changes nothing
python3 WaferNesting/01_extract_footprints.py
python3 WaferNesting/02_nest.py                 # ~80 s from the seed
$KL -b -r WaferNesting/03_export_wafer.py
$KL -b -r WaferNesting/04_verify_wafer.py       # must end ALL CHECKS PASSED
```

`config.py` nests the three connector designs only — no dummies — and writes the
pre-renumbering layer numbers (§6.1). Read §6 before fabricating anything from it.

### 1.4 Stages and outputs

| Stage | Does | Runs in |
|---|---|---|
| `00_check_setup.py` | verifies packages, KLayout and every input path; names the setting to fix | python3 |
| `01_extract_footprints.py` | design DXF → silhouette + centroid offset (+ per-kind envelopes) | python3 |
| `02_nest.py` | silhouettes → poses `[kind, angle, x, y]`, simulated annealing to feasibility | python3 |
| `03_export_wafer.py` | poses + **original DXFs** → wafer GDS | KLayout |
| `04_verify_wafer.py` | counts, per-layer shape totals, max radius vs the sources | KLayout |
| `01c_extract_exact_placements.py` | existing wafer → `placements.json`, every part proven against its source | KLayout |
| `01d_rebuild_wafer.py` | `placements.json` + sources + template → wafer GDS, checked against the original | KLayout |
| `01e_overlay_wafers.py` | the original + the rebuilt wafer → one GDS on datatypes 0 / 1, XOR on 2, + `.lyp` | KLayout |

All output goes to `runs/<RUN_TAG>/`:

| File | From | What |
|---|---|---|
| `footprint_<key>.json` + `_offset.json` | 01 | silhouette (centred) and the centroid to undo it |
| `envelope_<kind>.json` | 01 | union envelope, only for kinds with > 1 design |
| `footprints_overlay.png` | 01 | visual check of every silhouette |
| `poses.json`, `nest_preview.png` | 02 | the nest |
| `wafer_<RUN_TAG>.gds` | 03 / 01d | the wafer |
| `placements.json` | 01c | target identity, template layers, per-cell source + offset + XOR proof, every placement |
| `overlay_<RUN_TAG>.gds` + `.lyp` | 01e | old and new wafer overlaid, with their XOR |

Runs present today: `interconnect_4x3` (config.py, 2026-10-07), `wafer4_rev2`
(config_rev2), `wafer4_rev3` (config_rev3), and two older ones, `example_4x3` and `electrode_bundle_IONP_id11`, kept
for comparison. Stage 03 refuses to overwrite its GDS; 01d overwrites its own (it is fully
determined by `placements.json` and the sources).

---

## 2. Architecture

### 2.1 Nest path — nest a proxy, place the original

```
  design DXFs
       │
  01 ──┴─► footprint polygon + centroid offset       a COLLISION PROXY, nothing else
  02 ──┴─► poses: [kind, angle, x, y] x N            simulated annealing to feasibility
  03 ──┴─► FULL geometry from the original DXFs,     layer-remapped, placed at those poses
  04 ──┴─► independent verification                  pass / fail
```

The footprint never appears in the output. Placement is `t_place * t_rot * t_center`:
undo stage 01's centring with that design's own offset, rotate, translate.

### 2.2 Reproduce path — prove, then rebuild

```
  existing wafer GDS ──► 01c ──► placements.json ──► 01d ──► wafer GDS ──► check vs original
        + SOURCES          │   per cell: source, offset, XOR proof        (same 01d run)
        + template         │   TOP: which template layers survive
                           └── every placement: integer-dbu position, angle, mirror, mag
```

Both stages load sources through `exact_io.py`, so the stage that proves a source
reproduces a cell and the stage that builds the cell from it cannot disagree about what
"the source, layer-mapped" means.

| Module | Role |
|---|---|
| `config.py` | nest path: the 3 connector DXFs, counts, clearances, seed, `LAYER_MAP` |
| `config_rev2.py` | reproduce path: target wafer, template, one `SOURCES` entry per wafer cell, the fabricated layer maps |
| `config_rev3.py` | revise path: Rev2's `placements.json`, Rev3 `SOURCES`, `EXTRA_TOP_LAYERS`, `EXPECTED_CHANGED_LAYERS`, `CHANGES_WITHIN` |
| `config_common.py` | derived settings for the nest configs (paths, `DESIGNS` helpers) |
| `footprint_extract.py` | DXF → silhouette, for stage 01 |
| `exact_io.py` | source → polygons, template → polygons, GDS header, transform records |
| `new_wafer_actually.GDS` | wafer outline + alignment marks (third mark cluster at x ≈ +96 mm is off-wafer) |
| `marks_union.wkt` | the marks as a shapely geometry, mm — the nester's keep-out |
| `reference_run_2026-07/` | footprints and poses of the 2026-07 nest; `config.py`'s seed. **Do not edit** |

---

## 3. The ideas worth understanding

### 3.1 The count is an input; there is no density objective

`count` in `DESIGNS` **is** N. Stage 02 answers *"can these N pieces be placed legally?"* —
never *"how many fit?"* The cost is

```
4*(area outside usable) + 4*(area on marks) + 1*(pairwise overlap area)
```

and every term is zero for *any* legal layout, so the solver stops at the first zero it
reaches. That is right for the connectors — N is fixed by what is needed, the pieces are
bottle-shaped at ~70 % fill, and feasibility is the hard part. It is wrong when you want a
maximum count: measured on a 3.1 × 27 mm probe strip, a plain column nest seats 66 where
this solver, asked for 24, placed exactly 24 in a loose layout. It also scales badly (full
N × N overlap matrix; painful past ~40 pieces). For count-maximising, use the other fork's
`nest_tiler.py`.

### 3.2 What the footprint is, and why it must not under-cover

The silhouette is the union of **every** layer, then its outer shape. `extract()`:

1. read every LWPOLYLINE and HATCH on every layer, µm → mm;
2. `unary_union` of all of it;
3. `buffer(+CLOSE_GAP).buffer(-CLOSE_GAP)` — closes hairline splits;
4. **drop interior holes** — a design with a hole through it nests as solid;
5. if still disjoint, **keep the largest part only** (stage 01 warns);
6. `simplify(SIMPLIFY)`, then centre on the centroid.

Union of every layer means the footprint can only **over**-cover, which is wasteful and
harmless. Under-covering is the dangerous direction: the nester's `GAP` is the *whole*
clearance mechanism and nothing downstream re-checks the true shapes against each other, so
a footprint smaller than the body yields a "feasible" nest whose pieces collide on the mask.

### 3.3 The clearances are measurements, not preferences

`EDGE_EXCL 3.0` / `GAP 0.05` / `MARK_BUF 0.7` are what the fabricated 2026-07 nest honours
(edge 2.993 mm, inter-piece 0.000 mm, marks 0.701 mm measured against its own footprints).
Older scripts used `GAP 0.3` / `MARK_BUF 1.0`, which no real layout ever achieved: seeded at
those values the search plateaus around 0.35 mm² residual and looks exactly like a slow
annealer. If a run will not converge, check the clearances before adding iterations —
stage 02 prints the seed cost first.

### 3.4 Reproduction is proven, not assumed

01c does not trust the config. For each wafer cell it loads the configured source, maps its
layers, measures where it sits relative to the cell (bounding-box alignment), and then
requires **XOR = 0 on every layer** before accepting it. For TOP's own shapes it finds the
template layer that XORs to zero against each wafer layer. Anything it cannot trace stops
it, and `placements.json` is not written. That is how it found the one design in
`../designs/` that did not match the wafer (§4.2).

What "identical" means in 01d's check, and why:

- **cells, TOP's shapes, the flattened wafer**: XOR = 0 per layer, same polygon counts.
- **placements**: cell, mirror, magnification and integer-dbu displacement exact; angle
  within 1e-9°. KLayout stores a rotation as sine/cosine and recomputes the GDS angle on
  write, which can move it by one unit in the last place (276.751004645 →
  276.7510046450001): ~1e-13 µm at the rim, invisible on a 1 nm grid, and check 4 proves it.
- **bytes**: reported, not required. GDS carries timestamps and the writer's record order.

**Revising content.** A revision config points `SOURCES` at the new files and reuses the old
wafer's `placements.json`. 01d notes every swapped source, builds with the new ones and still
checks against the old wafer, with three settings that make the check mean something:

- `EXPECTED_CHANGED_LAYERS` — differences on these layers are reported, not failed; any other
  difference still fails.
- `CHANGES_WITHIN` — per device cell, the change on a layer must lie inside another layer
  (Rev3: the changes on 3 and 8 inside the Metal2 pads, layer 6). A revision meant to touch
  only the pads therefore cannot touch anything else unnoticed.
- `EXTRA_TOP_LAYERS` — template layers added to TOP (Rev3: layer 8, Metal3's marks).

Placements must still match exactly.

---

## 4. Worked examples

### 4.1 `config.py` — 3 connector designs, seeded (re-run 2026-10-07)

4 × `12Block_56_15` + 8 smalls (4 × `51_35`, 4 × `71_15`, sharing one slot), seeded from
`reference_run_2026-07/`.

| | |
|---|---|
| Footprints | **no longer** match `reference_run_2026-07/`: the designs were revised after that nest (8-block 26 mm² smaller, 0.8 mm shorter at the board end). The current DXFs do match the fabricated cells |
| Nest | feasible on the first polish from the seed, 77 s |
| Clearances achieved | gap 0.057 mm, edge 3.027 mm, marks 3.162 mm |
| Wafer | 12 instances; layers 1/0, 3/0, 5/0, 6/0 (pre-renumbering, §6.1); max radius 46.958 mm; all checks passed |

Output: `runs/interconnect_4x3/`. (Run before the 51_35 bulb fix in §4.2; re-run 01–04 to
pick it up — gotcha 2.)

### 4.2 `config_rev2.py` — the fabricated Rev2 wafer

Target `MEA1K_wafer4_12x_8x_8x_breakoutboards_Rev2.gds` (byte-identical to
`../reference/WaferWith12_ALLNEW.gds`): 4 + 4 + 4 connectors and 26 dummies, 38 placements.

What 01c established, each by XOR = 0:

| Wafer part | Source | How |
|---|---|---|
| `CONN_BIG_NEW`, `CONN_SMALL_51_35_NEW`, `CONN_SMALL_71_15_NEW` | `../designs/*_chamfered_*.dxf` | DXF map metal1 → 7, polyimide_negative + etchingpad → 3, etching + via → 5, metal2 → 6; cell sits **−90 µm in y** from the DXF |
| `dummy_61_10_NEW`, `dummy_35_36_NEW` | `../designs/dummy_*_padrows.gds` | layers 1 → 3, 3 → 7 (5, 6 unchanged); no offset |
| TOP's own shapes | `new_wafer_actually.GDS` | template layers 3, 5, 6, 7, 10 kept as-is; 1/0, 8/0 and the off-wafer cluster dropped |

Found along the way: the shipped `51_35` DXF had **one** right-neck bulb where the
fabricated cell has **two** — `N_RIGHT_BULBS` is now 2 in `../config_8block.py` (1 for the
`71_15` variant; `../HANDOVER.md` §4.4). And the wafer carries one **duplicate placement**:
two `dummy_61_10_NEW` at (42.779, −9.325) mm, 31°. 01c warns, and 01d keeps it, since the
job is to reproduce the wafer.

01d result: all cells and TOP OK, 38/38 placements with positions exact and worst angle
deviation 5.7e-14°, flattened XOR 0 on 3/0, 5/0, 6/0, 7/0, 10/0 → `GEOMETRY IDENTICAL`.
Not byte-identical. Negative test: rebuilding with the old one-bulb DXF fails on exactly
that cell (574 095 µm² on 3/0) and on the flattened 3/0 (4 × that).

Output: `runs/wafer4_rev2/`.

### 4.3 `config_rev3.py` — the Rev3 wafer (built 2026-10-07)

The Rev2 wafer with the Rev3 pad stack (`../HANDOVER.md` §4.5): the PI etch squares become
perimeter etch vias on layer 3, and a new Metal3 layer (the pad squares) goes on layer 8.
Sources: `../designs/rev3/*_rev3.*` from `build_designs.py --rev 3`. Layer maps: Rev2's plus
`metal3 → 8` (DXF) and `8 → 8` (dummy GDS); the etch circles need no entry, since they are on
Polyimide_Negative / dummy layer 1, already mapped to 3.

**Metal3's alignment mark.** Template layer 8 holds a small (non-inverted) vernier in the
third of the five mark slots, between Etching's (slot 1) and PI's (slot 4) inverted marks,
plus the coarse marks every layer repeats. Rev2 dropped template layers 1 and 8, so both of
their slots were free; Rev3 keeps layer 8 as-is (`EXTRA_TOP_LAYERS`). Slot 2 (template
layer 1) is still free. The nearest device is as far from the new marks as from Rev2's
(0.5–1 mm on the right, 1–1.5 mm on the left).

01d result: all cells and TOP OK, changed as expected on 3/0 and 8/0 with every change
inside the Metal2 pads; 38/38 placements exact; flattened XOR 0 on 5/0, 6/0, 7/0, 10/0;
68.45 mm² on 3/0, 78.62 mm² on 8/0. 01e: the same XOR areas in the overlay.

Output: `runs/wafer4_rev3/` (`wafer_wafer4_rev3.gds`, `overlay_wafer4_rev3.gds` + `.lyp`).

---

## 5. Adapting it

### 5.1 Inspect a DXF first

Two reader assumptions fail **silently** — a plausible wrong footprint, no error:

- **Units must be micrometres.** A 50 × 15 mm part reads ~50000 × 15000. Ignore
  `$INSUNITS`; it is frequently wrong.
- **Only LWPOLYLINE and HATCH are read** by stage 01. CIRCLE, ARC, SPLINE and INSERT are
  invisible and must be exploded first. (KLayout, which 03/01c/01d use, reads everything.)

`../03_inspect.py <file.dxf>` prints the per-layer census and bbox.

### 5.2 A new nest config

Copy `config.py`, then set `RUN_TAG`, `DESIGNS` (`key`, `dxf`, `kind`, `count`), `LAYER_MAP`
and `SEED_MODE`. Designs sharing a `kind` are nested against their union envelope and are
interchangeable at export — group only near-identical shapes; stage 01 prints the waste.
**Any layer not in `LAYER_MAP` is silently deleted on export.**

Seeding dominates runtime (free nesting was ~20× worse on the 4 + 8 case). `"seeded"` also
puts a piece the seed has no pose for into the slot of a seed piece not placed this time.

### 5.3 A new wafer revision that keeps the layout

Copy `config_rev3.py`: point `PLACEMENTS_PATH` at the 01c output of the wafer to start from,
`SOURCES` at the revised designs, and list the layers the revision is meant to change in
`EXPECTED_CHANGED_LAYERS` (and where, in `CHANGES_WITHIN`). Run 01d, then 01e to look at it.
To start from a wafer other than Rev2, first write a reproduce config for it like
`config_rev2.py` and run 01c. To *move* or *add* pieces, edit `placements.json` (or the
script that writes it) — 01d builds whatever it says.

### 5.4 Check each stage before moving on

| After | Confirm |
|---|---|
| 01 | area and bbox as expected; overlay shows one closed outline; no "disjoint parts" warning |
| 02 | feasible; achieved gap / edge as asked; preview sane |
| 03 | `DROPPED` lists only layers you meant to drop |
| 04 | `ALL CHECKS PASSED` |
| 01c | every cell `OK`, every TOP layer matched, `wrote placements.json` |
| 01d | `GEOMETRY IDENTICAL`; for a revision, `IDENTICAL … except the expected changes on …` |
| 01e | XOR (datatype 2) empty on every layer the revision did not touch |

---

## 6. Open items

### 6.1 `config.py`'s `LAYER_MAP` is not the fabricated numbering

It writes polyimide_negative/etchingpad → 1, metal1 → 3, etching/via → 5, metal2 → 6. The
fabricated wafer has them on 3, 7, 5, 6 (renumbered by hand to sit on the matching mark
layers; `config_rev2.DXF_LAYER_MAP` is the fabricated one). A wafer nested with `config.py`
lands on the wrong masks until this is reconciled.

### 6.2 The nest path has no dummies

Dummies are GDS and stages 01/03 read DXF, and the Rev2 dummies were hand-placed in the gaps
after nesting. For a *new* layout they need a fill stage (pack into the leftover area under
the same clearances); for the Rev2 layout the reproduce path already has them.

### 6.3 Two WaferNesting forks

| | here | `generative_probe_design/WaferNesting/` |
|---|---|---|
| 00, 01, `footprint_extract.py` | — | same code (88–99 % of lines identical) |
| 03, 04 | basic | superset: mark inversion, OASIS/.lyp, placement record, mark checks |
| nester | `02_nest.py` (SA, fixed N, seeded) | `nest_tiler.py` (skyline, maximises N) |
| reproduce path | 01c, 01d, `exact_io.py` | — |
| map | — | `05_map_wafer.py` |

They also disagree on **mark polarity**: the probe fork copies an inverted vernier into
every dark-field mask; the fabricated interconnect wafer carries the template's marks
unmodified. A merged WaferNesting has to make that a per-wafer setting, not a default.

### 6.4 `config_electrode_bundle.py` is legacy

It expects `../electrode_bundle/designs/`, which does not exist here, and nests a single
probe with this folder's annealer. Probe wafers are made with the other fork. Kept only
because `runs/electrode_bundle_IONP_id11/` came from it.

---

## 7. Gotchas

1. **Any DXF layer missing from `LAYER_MAP` is silently deleted on export** (nest path).
   Stage 03 prints mapped and dropped layers per design; read them. Stage 04's per-layer
   count check is the backstop. This is how `EtchingPad` was nearly lost on the 2026-07 wafer.
2. **Never reuse poses because a footprint "did not change".** Regenerated designs once
   differed by 0.2–1.0 mm², visually identical, enough to drop old poses to a 0.0002 mm gap.
   Re-run 01 and 02 whenever a DXF changes.
3. **Never radius-check these pieces by bounding box.** They are bottle-shaped; a rotated
   piece's bbox overstates its reach by > 10 mm. Stage 04 uses the convex hull.
4. **`ENVELOPE_SIMPLIFY` must stay ≤ `ENVELOPE_PAD`**, or the envelope cuts inside its own
   members; stage 01's coverage assertion catches it.
5. **`extract()` is lossy on purpose** — it bridges splits up to 2 × `CLOSE_GAP` and keeps
   only the largest part of a disjoint result (§3.2).
6. **`runs/` is disposable; `reference_run_2026-07/` is not.** Nothing regenerates the seed.
   `runs/wafer4_rev2/` is regenerated by 01c + 01d, `runs/wafer4_rev3/` by
   `build_designs.py --rev 3` + 01c + 01d + 01e.
7. **SA is stochastic.** If a run stalls, change `RNG_SEED` or raise `RESTARTS` / `ITERS` —
   after checking the clearances (§3.3).
8. **Stages 01c, 01d, 01e, 03, 04 run under KLayout's Python** (no shapely / numpy / matplotlib).
   Keep configs and `exact_io.py` pure standard library + `pya`.
9. **Units:** µm in the DXFs, mm in the nest path, dbu (1 nm) in KLayout and in
   `placements.json`.
10. **01c measures offsets by bounding box.** A source whose bbox differs from the cell's
    (an extra or missing feature at the edge) gets a wrong offset and then fails its XOR —
    the failure is reported per layer, so look at which layer and where before suspecting
    the offset.
