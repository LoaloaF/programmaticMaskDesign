# electrode_bundle — handover

Parametric generator for the 64-channel flexible electrode probe (MEA1K connector project).
You give it numbers, it emits the CAD. **Nothing is drawn by hand and no input file is required**
— the entire device geometry is computed from `design_sets.py` + `config.py`.

All generated output goes to **`<TARGET_SET>/shanks/`** (`TARGET_SET` in `config.py`;
currently `64Ch_4Shankdesigns`) — a sibling of this folder, not inside it. The path is
resolved from the module's own location, so nothing needs editing on a new machine. The
only things inside `designs/` are shipped `…Example` sample files from an earlier
generation; nothing reads them.

For the pipeline as a whole (generator → wafer), start at [`../README.md`](../README.md).

---

## 1. Quickstart

```bash
pip install ezdxf shapely matplotlib numpy pytest

cd <the folder CONTAINING electrode_bundle/>     # not into electrode_bundle itself
python3 -m electrode_bundle.batch                # every design in design_sets.DESIGNS
python3 -m pytest electrode_bundle/tests -q      # 240 tests; ~15 min (every build solves)
```

> Run from the **parent** of `electrode_bundle/` — that's how `python3 -m electrode_bundle.<x>`
> resolves the package. There is nothing to install and no path to edit.

### Entry points

| Command | Does |
|---|---|
| `python3 -m electrode_bundle.batch` | **the usual one.** Builds all 16 `DESIGNS` variants + the two comparison DXFs. `--no-render`, `--no-side-by-side` |
| `python3 -m electrode_bundle.side_by_side [names] [--ionp]` | rebuilds a comparison DXF from already-generated files. `--align loop\|top`, `--gap`, `--out` |
| `python3 -m electrode_bundle.plot_bundling [names] [--theta] [--bundle-ratio]` | per design: flat vs bundled contact positions → `<name>_bundling.png` |
| `python3 -m electrode_bundle.ionp_patterns` | prints the design × IONP-pattern fit matrix |
| `python3 -m electrode_bundle.main <cmd>` | single scratch probe from a bare `BundleConfig()` — see below |

`main.py` subcommands, all on the bare default config (which is **not** one of the
`DESIGNS`: it carries the dense `{33..48: 30.0}` spacing band and no design label):

| Subcommand | Does | Reads a file? |
|---|---|---|
| `bundle`  | electrode bundle DXF + preview PNG (`electrode_bundle.dxf`) | no |
| `mapping` | channel → flex-PCB pad JSON (`electrode_to_flex_mapping.json`) | no |
| `ionp`    | stamps IONP wells onto an **existing** `electrode_bundle_C{NN}.dxf` (in place) | **yes** — see §7 |
| `all`     | all three in a single in-memory pass | no |
| `legend`  | decode reference card for all 16 IONP ids (`ionp_id_legend.png`) | no |

Flags: `--pattern-i 0-15` (IONP id, sets the output filenames `C{NN}`), `--no-render`.

### Outputs of `batch` — all land in `<TARGET_SET>/shanks/`

Created automatically on first run. `<name>` is the geometry (`U1.6`, `U2.5`, `U4`, `U8`);
`<tag>` is `config.variant_tag(name, id)`, e.g. `U4C08` — the same string that is etched
into the gold as the design label.

| File | What |
|---|---|
| `electrode_bundle_<name>.dxf` + `.png` | the geometry, **no** IONP layer — never nest this |
| `electrode_to_flex_mapping_<name>.json` | channel → flex-PCB pad number |
| `<name>_electrodes.json` | per-channel wafer **and** bundled positions, lengths, fold model (`probe_json.py`) |
| `electrode_bundle_<tag>.dxf` + `.png` | the geometry **plus** its IONP barcode — what WaferNesting nests |
| `<tag>_info.json` | per-stripe IONP metadata, with the flex mapping embedded |
| `all_designs_side_by_side.dxf` | every geometry in a row, barcode-free, aligned on the loop |
| `all_ionp_patterns_side_by_side.dxf` | all 16 barcoded variants in a row |

Re-running **overwrites in place**. Every DXF goes through `dxf_io.save_dxf`, which strips
the build-only layers (`config.EXPORT_DROP_LAYERS`: `Electrodes`, `Ref_Electrodes`,
`Polyimide`) — see §2.2.

---

## 2. Architecture

### 2.1 Data flow — one producer, several consumers

```
design_sets.DESIGNS                       DesignSpec: name, n_channels, sites, fiber_length,
   │                                      loop_offset, overall_length, ionp_pattern_id
   ▼
batch.config_for(spec) ──► BundleConfig
   │  spacing.Uniform/Segments  → delta_y / delta_y_overrides
   │  design_sets.BUNDLE_FOLD    → shoulder_offsets_profile (per-fiber shoulder stagger)
   │  lengths.solve_lengths      → hook_drop, then bottom_elec, to hit loop_offset / fiber_length
   │  overall_length             → ribbon_length
   ▼
bundle.build_bundle(cfg) ──► BundleResult (doc, electrode_locs, ref_locs, chan_pad, loop_xy, …)
   ├──► mapping.build_mapping     channel → flex pad
   ├──► ionp.add_ionp_wells        IONP barcode wells onto the same doc
   └──► probe_json.build_record    <name>_electrodes.json
```

The bundle is built **once** per variant and its in-memory result handed to every consumer,
so the mapping never re-derives the routing order and IONP never re-reads the DXF.

| Module | Role |
|---|---|
| `design_sets.py` | **What to build.** `_BASE_DESIGNS` (one per geometry) × `ionp_patterns.DESIGN_IDS` → `DESIGNS`. Also `BUNDLE_FOLD` and `FAMILY_OVERALL_LENGTH`. Its docstring has the labelled length diagram |
| `config.py` | **How it is built.** `BundleConfig`, `IonpConfig`, `MappingConfig`, `RefSpec`/`REF_SITES`, `TARGET_SET`, `variant_tag`, `EXPORT_DROP_LAYERS`, `LOOP_DATUM_VAR` |
| `batch.py` | spec → config → build → write; the main entry point |
| `spacing.py` | `Uniform(span)` / `Segments(runs)` site layouts → `delta_y` kwargs |
| `lengths.py` | `measure` and `solve_lengths` — the doc's lengths ↔ config knobs (§3.2) |
| `bundling.py` | flat → bundled positions: `TwoArcFold` (from electrode2geometry), `NoFold` |
| `bundle.py` | `build_bundle(cfg)` assembles the DXF; `render_bundle` draws the preview |
| `shapes.py` | device shapes: site outlines, fanout body, pad columns, `assign_channels_to_pads`, `build_pad_routes` (incl. the Ref wrap route), `fiber_x_positions`, text outlines |
| `geometry.py` | generic primitives (arcs, S-transition, stroked centrelines) — knows nothing about electrodes |
| `hooks/` | insertion-hook specs, one named spec each (`teardrop_clap` = current, `legacy_barb` = archived). Each owns its outline **and** its etch hole |
| `mapping.py` | channel → flex-PCB pad assignment |
| `ionp_patterns.py` | the frozen IONP barcode bank `IONP_PATTERNS` + `DESIGN_IDS` + validation |
| `ionp.py` | stamps a pattern onto a geometry and enforces the design rules (§5) |
| `probe_json.py` | the per-design electrode record |
| `side_by_side.py`, `plot_bundling.py`, `id_legend.py` | inspection drawings |
| `dxf_io.py` | DXF readers/writers; `save_dxf` strips build-only layers |
| `main.py` | single-probe CLI (§1) |
| `tests/` | pytest suite (§6) |

To change a design, edit its `DesignSpec`. To change how every design is built, edit a
default in `config.py` **or** pass overrides: `BundleConfig(hook_drop=300)`.

### 2.2 Layers

| DXF layer | Exported? | What |
|---|---|---|
| `Metal` | yes | all gold: site pads + wires, the Ref trace and contacts, routes, solder pads, design labels |
| `Etching` | yes | **device release**, through the whole stack: the moat, the tip wedge, the insertion loop's hole, and every solder-pad opening (a 200 µm circle in the 220 µm gold, `pad_opening_inset` of collar) |
| `pad_etching` | yes | **site openings**, through the top polyimide only: the 13.5 µm circle over every recording site and every Ref contact. Never anything else |
| `PEDOT_SIROF` | yes | the 15 µm coating circles, concentric with the openings |
| `IONP` | yes | the barcode wells (barcoded variants only) |
| `Electrodes` | **dropped on save** | the 64 recording openings again — the build and tests read contact centres off it |
| `Ref_Electrodes` | **dropped on save** | the Ref openings again, kept OFF `Electrodes` so they never redefine the array |
| `Polyimide` | **dropped on save** | the positive body; the fab patterns the negative (`Etching`) |

### 2.3 Device geometry, tip to connector

- **Electrode site** — four concentric circles: `l_contact` 13.5 (opening), `l_pedot` 15
  (coating), `l` 17 (gold pad), `2·polyimide_pad_r` 25 (polyimide swelling). Each blends
  into its trace with tangent fillets. Every recording fiber ends `polyimide_tip_len`
  (100 µm) below its own site in a tapered tip.
- **Fibers across the shank** — there are **65**: fiber 0 is the **Ref**, on the centre
  slot (x = 0), widened to `wide_wire_hw`, and carrying the hook; recording channel `c` is
  fiber `c+1` and the channels alternate outward (ch0 on +x next to the Ref, ch1 on −x, …).
  `shapes.fiber_x_positions` is the layout; `batch._fiber_x` must agree with it.
- **Insertion hook** (`hooks/teardrop_clap.py`, from `changes_plan/new_hook.png`) — a
  teardrop head with 6 claps (5 µm arms) and one 35 × 45 µm elliptical etch hole: the
  **loop**, the probe's depth datum (§3.2). `build_bundle` asserts ≥ `hook_etch_margin`
  (8 µm) of polyimide around it.
- **Shoulder** — each fiber is freed from the polyimide block at its own height,
  `BUNDLE_FOLD.offsets(x)` above the base, so outer fibers get the extra free length they
  need to swing into the bundle. The block's lower edge is therefore a staggered curve.
- **Fanout / routes** — every channel runs a neck, a gather fan into a centred 25 µm-pitch
  bundle, a vertical riser and one 50° diagonal into its pad (`build_pad_routes`).
- **Solder-pad block** — `polyimide_width` 2.85 mm, entered through an S-transition
  (`polyimide_curve_height`, clamped to the available stem). Two half-disc alignment tabs
  (0.6 mm left, 0.3 mm right) are the orientation indicator: the **big** tab marks the
  edge **away** from the Ref's column.
- **Solder pads** — 2 columns × 33 at x = ±975, `pad_pitch` 300, `pad_diam` 220. Each
  wired pad is a **teardrop** aimed back along its route (`pad_teardrop_fillet_radius`
  275; **295 is the wall** — beyond it adjacent lanes close up, at 400 they short, which
  `test_pad_teardrops_keep_the_metal_spacing` pins). Row 0 of each column is a flex REF/GND
  pad: the Ref takes the **right** one (flex 66, `shapes.REF_PAD`); the left one is the
  device's single spare, an unrouted plain circle.
- **Design label** — `variant_tag` text in `Metal`, twice: small (`label_tip_height`) beside
  the Ref's bottom contact band (on the carrier, for processing), and large
  (`label_pad_height`) above the pad field, **mirrored** because the finished device is read
  from the other face. `label_pad_x/y` place it by hand; there is no fit check.

---

## 3. The ideas worth understanding

### 3.1 Electrode depths are a cumulative-gap staircase

The gap *before* channel `i` is `delta_y`, overridable per channel via
`delta_y_overrides = {i: µm}`. Positions are the **cumulative sum** of gaps, so editing one
gap shifts every electrode above it and leaves everything below untouched.

Designs normally don't touch these directly: `spacing.Uniform(span)` sets
`delta_y = span/(n-1)` with no overrides, and `spacing.Segments([(start, end, pitch), …])`
sets every gap explicitly in the 1-indexed, inclusive notation design docs use. The bare
`BundleConfig()` default (what `main.py` builds) carries a dense band `{33..48: 30.0}`.

**Anchor — which end stays put.** The staircase is built bottom-up, so the **deep tip
(channel 0) is pinned** and the shallow end absorbs every change. With the default band the
array spans 6448.25 µm instead of 8000 and the shallow end sits 1551.75 µm lower. There is
deliberately no `spacing_anchor` option: pinning the shallow end instead is a rigid offset,
reachable with `bottom_elec -= sum(delta_y - new_gap)`. Within `batch` the question is moot
anyway — `solve_lengths` re-places everything off the loop.

### 3.2 Lengths are solved, not computed

`DesignSpec` states the lengths as the design doc does (diagram in `design_sets.py`):

```
loop_offset + sites.span + (free fiber) = fiber_length       (loop -> shoulder)
fiber_length + (ribbon)                 = overall_length     (loop -> first solder pad)
```

`lengths.solve_lengths` hits `loop_offset` by moving `hook_drop`, then `fiber_length` by
moving `bottom_elec`: build once, measure, add the residual, rebuild, assert within 0.05 µm.
That works only because both relations are **linear with slope 1** in their knob — so a hook
spec must move its etch hole down by **exactly** `drop` (the `drop` contract in
`hooks/__init__.py`). `overall_length` then sets `ribbon_length`, so probes with different
fibers still end at the same connector position.

The datum is the loop centre, `BundleResult.loop_xy`, taken from the hook spec (the hole now
lives inside the single `Etching` mask, so no layer holds it alone). `build_bundle` also
stamps it into the DXF header as `LOOP_Y_UM` (`config.LOOP_DATUM_VAR`); `side_by_side`
aligns saved designs on it.

A `loop_offset` shorter than the fiber's own overhang below the deepest site
(`polyimide_pad_r + 250` = 262.5 µm) drives `hook_drop` **negative** — the head is pulled up
into the trace. That is legal (U2.5 asks for 250).

### 3.3 Bundling: drawn vs implanted positions

The probe is made flat and then gathered into a bundle. `bundling.TwoArcFold` (after
Gombkoto's electrode2geometry, MIT) models each fiber's sideways run as two arcs at constant
arc length; the loss of reach depends only on |x| — zero on the Ref, largest on the
outermost fibers (~287 µm with today's settings).

It is used in two places:

- **in the drawing** — as the per-fiber shoulder stagger (§2.3), so outer fibers get the
  free length they need. Contacts are still drawn exactly where `sites` says.
- **in the record** — `<name>_electrodes.json` carries each channel's `wafer` position and
  its modelled `bundled` position, and the bundled site span (array stretches by ~287 µm).

`max_theta_deg` (50°) is **not calibrated** against a real bundled device and dominates the
result (~60 µm per 10°). The JSON says `"calibrated": false` next to the numbers.

### 3.4 The reference electrode on the centre fiber

The centre fiber is not a recording channel: it is the Ref, carrying many parallel
contacts (`config.RefSpec`, shared by every design as `REF_SITES`):

- a **bottom band** starting `tip_offset` (150 µm) below the deepest recording site and
  stepping `pitch` (23 µm) down for as long as the next contact still lands on gold. The
  Ref's gold runs down the hook stem to `metal_loop_clearance` (20 µm) above the loop hole,
  so a longer `loop_offset` simply buys more contacts (U2.5 at 250 µm gets ~3; the 500 µm
  designs ~14);
- a **top band** starting `top_offset` (230 µm) above the shallowest site and running
  `top_len` (1000 µm) up the free fiber.

`RefSpec.check` raises on a band that would leave the gold, touch the recording array or
pass the fiber's top. The Ref trace is 4 µm wide (recording traces: 2 µm).

**Routing.** Outer-lane → lowest-pad is the **only** crossing-free ordering for this
riser-plus-diagonal topology on one metal layer, and the Ref (innermost lane) needs the
lowest pad (row 0). So it goes over the top: up the centre lane past the whole pad field,
across, down the empty corridor outside the right column, into row 0 from the side
(`shapes._build_ref_wrap_route`, shape knobs `BundleConfig.ref_wrap_*`, every clearance
asserted against `ref_wrap_clearance` = 100 µm).

**Kept apart on purpose.** Ref contacts live in `BundleResult.ref_locs` and on
`Ref_Electrodes`, never in `electrode_locs` / `Electrodes`. Everything downstream treats
`electrode_locs` as "the array" — `lengths.measure` reads the span and loop offset off it,
IONP normalizes against it, `probe_json` reports it — so a Ref contact in there would
silently redefine all three. `assign_channels_to_pads` gets recording channels only.

### 3.5 The IONP barcode is a fixed bank on a shared slot grid

Each barcoded variant carries an MRI-readable stripe pattern of 1.5 µm iron-oxide wells
encoding a **pattern id 0–15**. The patterns are **frozen data** in
`ionp_patterns.IONP_PATTERNS`, not computed per design.

- Coordinates are normalized **along the electrode array, not the whole shank**: `0` = the
  deepest site (ch0), `1` = `id_top_margin` (**120 µm**) above the shallowest site. On the
  current designs that range is **1758 / 2639 / 4057 / 7993 µm** (U1.6 / U2.5 / U4 / U8).
- Every pattern has a **tip anchor** (`0 → 0.2438`, always the fattest stripe, so it
  doubles as the orientation cue) and a **top anchor** near `1`. The anchors bracket the
  array, so their separation identifies the design; the data stripes between them give the
  id within it.
- Data stripes started out on 7 shared `SLOT_BANDS`, but the current bank has been
  **hand-tuned**: ids 3, 6, 8, 10–15 use data bands shifted off that grid,
  and ids 0, 2, 4, 5 and 8 use a lower top-anchor start than `TOP_ANCHOR_BAND`. Treat
  `IONP_PATTERNS` itself as the definition.
- **Four ids per design, and an id belongs to a design**: `DESIGN_IDS` maps U1.6 → 0–3,
  U2.5 → 4–7, U4 → 8–11, U8 → 12–15. Within a design the four ids differ in ≥ 2 stripes, so
  one misread stripe can't turn one id into another. U1.6's ids are one-hot (one data
  stripe each): its array has room for no more.
- An id fits the design it was made for and every **longer** one, never a shorter one;
  `ionp.check_design_rules` **raises** `DesignRuleError` rather than stamping a barcode
  that won't decode. `python3 -m electrode_bundle.ionp_patterns` prints the fit matrix.
- Wells within `keepout_radius` (30 µm) of a contact **along its own fiber** are dropped —
  deliberately not a radius in the plane, which at `delta_x` = 24 µm would also clear the
  neighbouring fibers' columns.
- Band heights were sized for ~800 wells, not the 700 floor, because well count is
  quantized: one 5 µm step across 64 columns is ~64 wells.

Check by eye in `all_ionp_patterns_side_by_side.dxf` and with
`python3 -m electrode_bundle.main legend`.

---

## 4. The designs

`design_sets._BASE_DESIGNS` — four uniform-pitch 64-channel arrays:

| name | `sites` span | `fiber_length` | `loop_offset` | `overall_length` | IONP ids |
|---|---|---|---|---|---|
| U1.6 | 1664 | 7000 | 500 | 27 000 | 0–3 |
| U2.5 | 2560 | 10 000 | 250 | 30 000 | 4–7 |
| U4 | 4000 | 10 000 | 500 | 30 000 | 8–11 |
| U8 | 8000 | 12 000 | 500 | 28 000 | 12–15 |

All µm. `DESIGNS` expands each against `DESIGN_IDS` (16 entries); `batch` writes the
barcode-free artefacts once per name and a barcoded DXF per variant.
`_check_variants_unique` rejects two entries with the same `variant_tag`.

---

## 5. Design rules

`ionp.check_design_rules` runs on every IONP build. It enforces `min_n_wells = 700` (the
MRI floor), `max_n_wells = 10_000`, and `min_strip_distance = 300 µm` between stripes, and
**raises** on a violation. Pass `strict=False` for a report dict instead.

`ionp_patterns.validate_pattern` rejects bands outside `[0, 1]`, inverted bands and
overlapping ones.

Geometry asserts inside `build_bundle` (each fails with the number it needed): the hook's
etch wall, the Ref trace clearing the loop hole and staying on the hook, `RefSpec.check`,
the alignment tabs fitting the block edge, every `ref_wrap_*` clearance, and
`solve_lengths` landing within 0.05 µm.

---

## 6. Tests

```bash
python3 -m pytest electrode_bundle/tests -q     # 240 tests, ~15 min
```

| File | Pins |
|---|---|
| `test_electrode_spacing.py` | the staircase: locality (nothing below an edited gap moves) and accumulation. Baselines come from `_base_cfg()`, **not** a bare `BundleConfig()` — the default carries the dense band. Keep it that way |
| `test_spacing.py` | `Uniform` / `Segments` → real electrode positions |
| `test_lengths.py` | `solve_lengths` lands on spec; the ribbon is a fixed connector run |
| `test_hooks.py` | the `drop` contract and the etch wall, per hook |
| `test_electrode_site.py` | round site shapes, tangent joins, contact centres unchanged |
| `test_etch_mask.py` | release moat surrounds everything at the tip; loop and solder-pad openings are in `Etching`; site openings only in `pad_etching`; coating/label placement |
| `test_metal_routing.py` | every channel actually reaches its pad in the drawn gold; teardrop spacing wall |
| `test_pad_assignment.py` | today's channel → pad assignment, and no route crossings |
| `test_bundling.py` | `TwoArcFold` / `NoFold`, `precompensate`; the JSON record; per design: contacts drawn on spec, shoulder staggered by exactly the fold cost, solved lengths undisturbed |
| `test_ionp_id.py` | bank structure (anchors, ≥ 2-stripe distance, `DESIGN_IDS`), every design fits its ids with margin, design-rule raising, keep-out, config defaults, legend, CLI |

---

## 7. Gotchas

1. **`main.py ionp` needs its input DXF to already exist** at
   `<TARGET_SET>/shanks/electrode_bundle_C{NN}.dxf`; it stamps wells onto that file in place.
   `all` and `batch` sidestep this.
2. **Run from the parent of `electrode_bundle/`** with the module form
   `python3 -m electrode_bundle.<name>`. Every module uses relative imports, so running one
   by file path fails with `ImportError: attempted relative import with no known parent
   package` — except `id_legend.py`, which carries a `__package__` bootstrap.
3. **`<TARGET_SET>/shanks/` is disposable** — regenerated from `design_sets.py` +
   `config.py`. Copy a design out to keep it.
4. **Never nest the plain `electrode_bundle_<name>.dxf`** — it has no `IONP` layer. The
   WaferNesting config nests the `<tag>` variants.
5. **`save_dxf` mutates the doc**: the build-only layers are deleted from it, not just from
   the file. Read contact centres before saving (everything in the package does).
6. **`ref_dxf` is optional and off.** Setting `BundleConfig.ref_dxf` drives contact centres
   from a reference DXF instead of the parametric grid. That file is not part of this
   folder; you would have to supply it.
7. **`mapping.build_mapping` asserts no recording channel lands on flex 65/66.** That is
   correct: the Ref is not in the channel mapping. It reports which REF/GND pad the Ref
   reaches as `ref_gnd_connected`.
8. **`max_theta_deg` drives drawn geometry** (the shoulder stagger), not just the reported
   bundled positions. Calibrate it before trusting a wafer's fiber lengths.
9. **Units are µm** throughout.
