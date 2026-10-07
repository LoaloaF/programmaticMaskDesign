# WaferNesting — how to nest a design set onto a wafer

Takes design DXFs, finds the densest legal arrangement on a 100 mm wafer, and writes the
combined GDS for fabrication. **One config file drives the whole run** — the stage scripts
hold no paths, clearances or layer names of their own.

> **Before anything is fabricated, read "Layer map" at the bottom.** The layer mapping is
> provisional and has one open question in it.

For the pipeline as a whole (generator → wafer), start at [`../README.md`](../README.md); the
generator that produces the DXFs nested here is documented in
[`../electrode_bundle/HANDOVER.md`](../electrode_bundle/HANDOVER.md).

---

## Layout

One target folder per design set, holding **both** halves of the pipeline:

```
generative_probe_design/
  electrode_bundle/            <- the design generator (code)
  WaferNesting/                <- this folder (code)
  64Ch_4Shankdesigns/          <- the target folder
    shanks/                    <- generator output: DXFs, PNGs, per-design JSON
    wafer/                     <- nesting output: footprints, poses, preview, wafer GDS
```

The folder name lives in exactly two places, and they must agree:

| File | Constant |
|---|---|
| `electrode_bundle/config.py` | `TARGET_SET = "64Ch_4Shankdesigns"` |
| `WaferNesting/config_64ch_4shank.py` | `TARGET_SET = "64Ch_4Shankdesigns"` |

Keeping both halves under one folder is the point: a wafer can never be built from shanks
that live somewhere else.

---

## Running it

```bash
cd generative_probe_design
export WAFERNEST_CONFIG=config_64ch_4shank
KL=/Applications/KLayout/klayout.app/Contents/MacOS/klayout

python3 -m electrode_bundle.batch            # 1. shanks  -> 64Ch_4Shankdesigns/shanks/
python3 WaferNesting/00_check_setup.py       # 2. resolve every path, check packages
python3 WaferNesting/01_extract_footprints.py#    DXF -> collision silhouette
python3 WaferNesting/nest_tiler.py --time 900#    silhouettes -> poses.json  (the nest)
# paste the SHANK_COUNTS line it prints into the config, then:
$KL -b -r WaferNesting/03_export_wafer.py    # 3. poses + DXFs -> wafer GDS
$KL -b -r WaferNesting/04_verify_wafer.py    #    re-check the GDS against the sources
python3 WaferNesting/05_map_wafer.py         # 4. the wafer map: what is where
```

**Start with `00_check_setup.py`.** It resolves every path the config depends on, checks
the Python packages and KLayout, lists the DXFs actually present, and on failure names the
exact setting to edit. It changes nothing.

| Stage | Does | Needs |
|---|---|---|
| `00_check_setup.py` | verifies the install and every input path | python3 |
| `01_extract_footprints.py` | design DXF → silhouette + centroid offset | python3 + shapely |
| `nest_tiler.py` | silhouettes → poses `[kind, angle, x, y]`, maximising the count | python3 + shapely |
| `03_export_wafer.py` | poses + **original DXFs** → wafer GDS | KLayout |
| `04_verify_wafer.py` | re-checks the wafer against the sources | KLayout |
| `05_map_wafer.py` | `placement.json` → labelled map PNG | python3 + matplotlib |

### Outputs — all land in `<TARGET_SET>/wafer/`

| File | What |
|---|---|
| `footprint_<key>.json` + `_offset.json` | per design variant: silhouette (centred) and the centroid to undo it |
| `envelope_<kind>.json` | per geometry: the union of its variants' silhouettes, which is what the nester packs |
| `footprints_overlay.png` | visual check of every silhouette |
| `poses.json` | `[kind, angle_deg, x_mm, y_mm]` per piece |
| `nest_report.json` | per-piece kind / pose / radial reach, plus run settings |
| `nest_preview.png` | the nest, with wafer rim, usable radius and marks |
| `wafer_<TARGET_SET>.gds` | **the deliverable** |
| `wafer_<TARGET_SET>.oas` | the same layout in OASIS, which stores the layer names in-file |
| `wafer_<TARGET_SET>.lyp` | layer names for KLayout (GDS2 cannot store them) |
| `placement.json` | which design variant stage 03 put at which pose |
| `wafer_map_<TARGET_SET>.png` | **the map** — every device coloured, numbered and named |

`nest_tiler.py` refreshes `nest_preview.png` **every time the record improves**, so it can
be watched live while the search runs. Useful flags:

```
--time 900     wall-clock budget in seconds (the search plateaus; more time stops helping)
--trials 400   cap on restarts
--gap 0.05     mm between devices (default 0 -- they may touch)
--edge 2.0     mm edge exclusion (default: the config's EDGE_EXCL)
--stride 1     finer candidate x positions (slower)
--seed 7       change this if a run looks unlucky
```

---

## Nesting a *new* design set

1. **Generate the shanks.** Set `TARGET_SET` in `electrode_bundle/config.py`, add your
   geometries to `electrode_bundle/design_sets.py`, run `python3 -m electrode_bundle.batch`.
2. **Copy `config_64ch_4shank.py`** to `config_<your_set>.py` and edit, in order:
   - `TARGET_SET` — must match step 1.
   - `SHANK_RATIO` — one entry per design, keyed by the DXF's name. **This is the mix the
     nester aims for**; only the proportions matter.
   - `SHANK_COUNTS` — the *result* of the last nest, pasted from the nester's printed
     line. Only `03_export_wafer.py`'s count assertion reads it.
   - `IONP_VARIANTS` — the barcoded variants of each geometry. `DESIGNS` is built from
     `SHANK_COUNTS` × these, so the filenames must match what the generator wrote.
   - `LAYER_MAP` / `LAYER_NAMES` / `MARK_LAYERS_UNUSED` — see the layer map below. These
     depend on which alignment slots your wafer template carries.
3. `export WAFERNEST_CONFIG=config_<your_set>` and run the pipeline above.

Nothing is searched for. Each config states its input locations explicitly in its `PATHS`
block, and `00_check_setup.py` names the line to edit when something is missing.

---

## How the nester works

The piece count is the **objective**, not an input: the config's counts are read only as a
ratio, and the nester fills the wafer and reports how many fit. On the 64-channel 4-shank
set it seats **59 pieces at 74 % density** (22 × U4, 15 × U1.6, 14 × U2.5, 8 × U8).

**Flip 180°.** Each design is a "sword" — a 3.01 mm handle ~13 mm long, a 2.06 mm shank, a
1.72 mm neck, then a taper to a point. A copy rotated 180° puts a handle against a tip, so
a flipped stack packs at **2.66 mm pitch instead of 3.46 mm, 23 % tighter**. This is
measured from your actual footprints and printed at the top of every run — if a redesign
changes the silhouette, the number moves with it.

**The lattice is not hard-coded.** Pieces are dropped one at a time onto an exact skyline;
the flipped orientation simply *falls lower* into its neighbour's taper, so the interlock
emerges by itself. Unlike a fixed lattice it also follows the wafer's curvature and mixes
designs of different lengths freely — worth ~5 pieces over a flat-row lattice at the same
pitch.

Everything written has passed a re-check against the true polygons: zero overlap, inside
the usable radius, clear of the alignment marks.

---

## The wafer map

`05_map_wafer.py` draws the wafer with every device coloured by geometry, shaded by barcode
variant, and labelled with its number and variant code. The legend lists each variant's
device numbers, so it reads in both directions: point at a device to find out what it is,
or look up a variant to find where its copies are.

It draws from **`placement.json`, which stage 03 wrote** — not from a recomputed
assignment. The variant→pose deal is a seeded shuffle, and a second implementation of it
would be free to disagree with the GDS that actually shipped; a map that mislabels a device
is worse than no map. Stage 04 cross-checks the two, comparing every instance's cell, angle
and position, and fails if they differ. So the order matters: **03, then 04, then 05.**

Labels sit on each device's wide handle rather than its centre. The centre is in the narrow
shank, where the flipped neighbour's handle is interlocked right against it — the most
crowded part of the picture. Handles of adjacent pieces point in opposite directions, so
anchoring there spreads the labels apart.

---

## Things that will bite you

1. **Any DXF layer missing from `LAYER_MAP` is silently deleted on export.** No warning,
   no error — the geometry simply is not in the GDS. This is the pipeline's most likely and
   least visible failure, and it is how `EtchingPad` was nearly lost on the 2026-07 wafer.
   Stage 03 prints the mapped and dropped lists per design; **read them.** Stage 04's
   per-layer count check is the backstop.
2. **`03_export_wafer.py` asserts `len(poses) == sum(SHANK_COUNTS.values())`.** The nester
   does not know your config's counts, so paste the `SHANK_COUNTS = {...}` line it prints
   into the config before exporting. `SHANK_RATIO` is what it aims for and is deliberately
   separate — if the nester took its mix from `SHANK_COUNTS`, pasting each result back
   would drift the mix a little further every run.
3. **Never reuse poses because the footprint "did not change".** Regenerated designs once
   differed by 0.2–1.0 mm² — visually identical — and that was enough to drop the old poses
   to a 0.0002 mm gap. Re-run `01` and the nester whenever a DXF changes.
4. **The footprint must not under-cover.** It is a collision proxy: the union of *every*
   layer, closed by `CLOSE_GAP` and simplified by `SIMPLIFY`. Simplification that cuts
   inside the true outline lets pieces overlap in the GDS while the nest reports success.
   Check `footprints_overlay.png`.
5. **`ENVELOPE_SIMPLIFY` must stay ≤ `ENVELOPE_PAD`**, or the envelope shrinks inside the
   designs it is supposed to contain.
6. **Never nest the plain `electrode_bundle_<kind>.dxf`.** It carries no IONP layer, so the
   magnetic ID would be missing from every device and layer 1/0 would ship as marks only.
   The config nests the barcoded `C00`–`C15` variants for exactly this reason.
7. **The clearances are measurements, not preferences.** `EDGE_EXCL 3.0` / `GAP 0.05` /
   `MARK_BUF 0.7` are what the fabricated 2026-07 wafer actually honours (measured: edge
   2.993 mm, inter-piece 0.000 mm, mark 0.701 mm). `EDGE_EXCL` in particular is *precedent,
   not a fab spec* — relaxing it packs more (2.0 mm → 62, 1.0 mm → 64), but that is a yield
   question for the fab, not a geometry one, and the electrodes sit 0–4 mm from the tapered
   tip with the bond pads at the far end, so no part of a shank is sacrificial.
   Note that `nest_tiler.py` does **not** read `GAP`: it defaults to 0 (pieces may touch)
   and takes `--gap` on the command line.
8. **`03_export_wafer.py` refuses to overwrite an existing wafer GDS.** Delete
   `wafer_<TARGET_SET>.gds` (or change `RUN_TAG` in the config) before re-exporting — and
   then re-run 04 and 05, since both read what 03 wrote.

---

## Layer map — CONFIRM BEFORE FABRICATION

**The wafer template is not a backdrop.** `new_wafer_actually.GDS` already carries the
alignment marks, and every target layer in `LAYER_MAP` is one of those mark layers — so a
device layer and its own alignment mark ship as a **single mask**. Stage 03 merges them.

The mark cluster (mirrored at both |x| ends) holds five slots. Each slot pairs one layer's
mark with a `7/0` companion, and `7/0` appears in *every* slot: it is the reference
everything else aligns to, which is why metal — the first mask — lives there.

| slot, \|x\| from centre | template layer | mark | used for |
|---|---|---|---|
| 40.94 – 41.47 mm | `6/0` | fine cross, 129 polys → **inverted on export** | **Etching** |
| 41.62 – 42.17 mm | `3/0` | **inverted** vernier, 1 poly + 129 holes | **pad_etching** (already the right polarity) |
| 42.34 – 42.87 mm | `8/0` | fine cross, 129 polys → **inverted on export** | **PEDOT_SIROF** |
| 43.04 – 43.57 mm | `1/0` | fine cross, 129 polys → **inverted on export** | **IONP** |
| 43.72 – 44.27 mm | `5/0` | **inverted** vernier, 1 poly + 129 holes | the **stencil** every dark-field mask's mark is copied from — dropped on export |

### Mask polarity, and why the marks are inverted

**Metal is the first mask and the only bright-field one.** Its plate is chrome where the
drawn shapes are, and it is what puts the alignment marks on the wafer — every later mask is
aligned to those. Every later mask is **dark field**: chrome everywhere except the drawn
shapes, which are the openings. That is already right for the device geometry (what we draw
is what gets opened), but it is *not* right for a mark drawn as 129 separate bars: those
print as 129 slits in a chrome field, and the metal scale they have to be read against is
under that chrome, invisible.

So every dark-field mask needs the **inverted** vernier — one polygon with the bars as holes,
which prints as a clear window with the scale in chrome and the metal mark showing through.
The template ships that form on `3/0` and `5/0` only. Stage 03 therefore copies the `5/0`
mark into the `6/0`, `8/0` and `1/0` slots (`MARK_INVERT_LAYERS`), **registered on the `7/0`
metal companion** that sits in every slot — not on the mark's own bounding box, because the
two ends of the wafer are not mirror images and the mark sits a different fraction off its
companion on each side. Each band is handled with its own band's stencil.

The transform is proved on every run before it is used: `3/0`'s inverted mark is rebuilt from
the `5/0` stencil and must reproduce the template's own geometry exactly (XOR = 0), or stage
03 aborts. Stage 04 then re-checks the finished GDS by area, per band, and fails if any
dark-field mark is not inverted or if a metal mark *is*.

`LAYER_MAP`, DXF layer name (lower-cased) → (GDS layer, datatype):

| DXF layer | GDS | What |
|---|---|---|
| `metal` | 7/0 | single-metal design + the design-ID markings; **the alignment reference** |
| `etching` | 6/0 | device release: outline, the insertion loop's hole, the solder pads |
| `pad_etching` | 3/0 | the site openings — 13.5 µm circles onto the gold, recording and reference. A **solid-box** mark, the coarser of the template's two styles, against a ~1.75 µm alignment budget to the metal (13.5 µm opening in a 17 µm pad): if that proves too tight, trade with a fine-cross layer, not with the remaining box at `5/0` |
| `pedot_sirof` | 8/0 | the PEDOT/SIROF coating, 15 µm circles in the 13.5 µm openings |
| `ionp` | 1/0 | the IONP wells — **assumed etched features. This is the one to confirm.** |

Dropped on purpose, because they are documentation geometry rather than process steps:
`polyimide` (the positive body; fab uses the negative `etching`), `electrodes` and
`ref_electrodes` (the contact centres — already cut by `pad_etching`; they must not become
mask features), plus the DXFs' empty CAD layers `0` and `defpoints`.

Stage 03 also cleans the template: it deletes the **third copy of the whole mark cluster**
that sits near x = +96 mm, far outside the 100 mm wafer, and the one remaining unused mark
layer (`5/0`, since `pad_etching` took `3/0`) — but only *after* `5/0` has donated its
inverted vernier to the dark-field masks, see below. The delivered GDS contains exactly six
layers — five masks plus the wafer outline.

### Layer names, and the three files stage 03 writes

The layers are named after the DXF layers they came from — `Metal`, `Etching`,
`PEDOT_SIROF`, `IONP`, plus `WaferOutline`. How far a name travels depends on the format,
not on KLayout, so stage 03 writes three files:

| file | what it is |
|---|---|
| `wafer_<TARGET_SET>.gds` | the deliverable. **GDS2 has no layer-name record** — there is no writer option for one, and a layout written with names and read back comes out unnamed. Layers here are `7/0`, `6/0`, … only. |
| `wafer_<TARGET_SET>.oas` | the same layout in OASIS, which *does* have a `LAYERNAME` record. Open this if you want the names in the file itself. |
| `wafer_<TARGET_SET>.lyp` | KLayout layer-properties sidecar that names the GDS's layers. *File → Load Layer Properties*. |

The GDS is written with `gds2_max_vertex_count = 4000`. The merged `Etching` mask is one
~20 000-vertex polygon per design, and GDS2 caps a `BOUNDARY` at 8191 points with a record
length held in a **signed** 16-bit int, i.e. 32 KB. Unsplit, KLayout reads its own file back
complaining that the record length must be reinterpreted as unsigned, and a stricter reader
at the fab may simply reject it. The cap splits those polygons into abutting pieces; the
union is unchanged (XOR against the OASIS twin is entirely sub-nanometre seam slivers that
vanish under a 1 nm shrink).

### The magnetic-ID (IONP) variants

Each geometry exists as **four** barcoded variants — `U1.6C00–C03`, `U2.5C04–C07`,
`U4C08–C11`, `U8C12–C15`. The plain `electrode_bundle_<kind>.dxf` has **no IONP layer at
all**, so it must never be nested for a real wafer. `IONP_VARIANTS` in the config lists
them; the config splits each kind's count evenly across its four, and stage 03 deals them
onto that kind's poses in a seeded shuffle so a variant is spread over the wafer rather
than correlating with the order the nester happened to seat them. `VARIANT_SEED` makes a
re-run reproduce the same wafer.
