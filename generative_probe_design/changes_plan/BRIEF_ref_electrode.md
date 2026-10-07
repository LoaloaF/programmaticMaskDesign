# Brief: Ref electrode on the centre fiber (plan item 6)

> **Status: landed (historical record).** Closest to reading **B** below, plus a second band: the Ref has its own
> (centre, widened) fiber — a 65th, so all 64 recording channels are kept — with contacts in two bands (`config.RefSpec` / `REF_SITES`) — one
> below the deepest recording site running down the hook stem, one above the shallowest — on
> their own `Ref_Electrodes` layer, kept out of `BundleResult.electrode_locs` (they are in
> `ref_locs`). It is routed over the top to the **right** column's row 0, flex pad **66**
> (`shapes.REF_PAD`). The `mapping.py` assert on 65/66 still holds because the Ref is not a
> channel in the mapping. Current state:
> [`../electrode_bundle/HANDOVER.md`](../electrode_bundle/HANDOVER.md) §3.4.

**DO NOT START THIS UNTIL THE TIP-GEOMETRY SESSION (items 4/1/2) HAS LANDED.** It rewrites
`shapes.py` and `bundle.py` in the same places that session is editing.

Separate Claude Code session, run from `generative_probe_design/`.
Env: `source /opt/anaconda3/etc/profile.d/conda.sh && conda activate dxf`

Read `electrode_bundle/design_sets.py`'s module docstring first (labelled geometry diagram),
then `shapes.py` (`assign_channels_to_pads`, `build_pad_routes`, `build_pad_columns`),
`mapping.py`, and `MappingConfig` in `config.py`.

## The decisions, already made — do not relitigate

- **65 fibers**: 64 recording + 1 Ref. All 64 recording channels are kept.
- The Ref is the **centre fiber**, the one carrying the insertion hook.
- The flex PCB is **fixed** — REF/GND stay at the near end of each pad row. No re-spin.
- **Routing (the user's own words):** "route it to the left, up, next to other solder pads
  (but maximal distance) then enter from the top into the center."

## Why the obvious approach is impossible — do not try it

The plan originally assumed the routing could simply be inverted. It cannot. Each trace is a
vertical riser plus one diagonal at a fixed angle, so trace B's diagonal crosses trace A's
riser exactly when B goes to a lower pad than A. **Outer-lane → lower-pad is the ONLY
crossing-free ordering for this topology**, and this is a single-metal design with no
crossover layer.

Measured on the real route geometry:

```
CURRENT  assignment (outer lane -> lowest pad):    0 crossings
REVERSED assignment (inner lane -> lowest pad):  992 crossings   (= 2 x 32x31/2)
```

Also note: the centre fiber currently routes to **flex pad 2, the far end of the column from
REF/GND** — the worst-placed lane for this change.

## The route that does work

Planarity only forbids reaching the bottom pad *from the bundle side*. The Ref lane rises
past the top pad, jogs outward, descends **outside** the pad column, and enters REF/GND from
the top. It crosses nothing, because every other trace terminates at x = ±975 approaching
from the inside, and the region beyond is empty.

Verified there is room:

```
polyimide half-width  1350
pad centre             975  + pad radius 100  -> pad outer edge 1075
free corridor          275 um
headroom above top pad 400 um   (poly_block_top 25900 vs top pad edge 25500)
```

A 10 µm trace at 25 µm pitch fits ~10 such corridors, so one Ref lane is comfortable. Cost:
the Ref trace roughly doubles in length (~10.8 mm extra). Sanity-check series resistance
against your metal stack before committing — nobody has.

## Order of work — each step leaves the tree buildable

1. **Pin current behaviour first.** Add `tests/test_pad_assignment.py` asserting today's
   `assign_channels_to_pads` result (ch0→left/32, ch62→left/1, …) and the resulting flex pad
   numbers, plus a geometric no-crossing test over the routed centerlines. **Nothing guards
   this today** and it is the single source of truth for both geometry and mapping.
2. **Fix the n>64 crash.** `assign_channels_to_pads` splits at `n // 2`, so 65 lanes give one
   column 33 → `pad_row = 33` → `IndexError` on a 33-element list. Required for 65 fibers.
3. **Split `electrode_locs` into recording vs ref** in `BundleResult`, ref list empty for
   now. Update `lengths.measure`, `ionp.py`, `id_legend.py` and `probe_json.py` to use the
   recording list explicitly. **Output must stay bit-identical** — this is the de-risking
   step, do it before anything visible changes.
4. **Add the Ref spec.** The user said it is shared across probes, so a module-level constant
   (`REF_SITES = RefSpec(...)`), not a per-design field. Plus a `ref_channel` knob defaulting
   to off.
5. **Generalise `assign_channels_to_pads`** to take a set of ref channels and return
   `pad_row = 0` for them, keeping the monotone rule for the rest. With an empty ref set the
   output must be identical — assert that with the test from step 1.
6. **Add the wrap-around route** in `build_pad_routes` for `pad_row == 0`. Render and eyeball
   the corridor; assert clearance ≥100 µm from every pad edge and from the polyimide edge.
7. **Draw the Ref contacts.** Put them on their **own DXF layer**, not `Electrodes`, so
   `dxf_io.extract_electrode_centroids_from_msp` and the IONP path stay honest.
8. **Turn it on**, update `MappingConfig`, and extend the mapping JSON with `ref_channels` /
   `n_recording_channels` / `spare_pads`.

## The sleeper that will bite you

**`lengths.measure()` derives `sites.span` and `loop_offset` from `electrode_locs`.** If a Ref
contact stays in that array, every design's spec'd lengths silently change meaning and
`solve_lengths` will keep asserting success while solving for the wrong thing. This is why
step 3 comes before everything else.

Related: `mapping.py`'s `assert 65 not in used and 66 not in used, "REF/GND pad appeared in
mapping"` **will fire on every build** once the Ref is routed. It is currently correct; it
becomes wrong.

Also check with the tip-geometry session before starting: they changed the loop datum to a
DXF header variable (`LOOP_DATUM_VAR`), which `lengths.loop_y` and `side_by_side.datum_y`
both read. Do not reintroduce the old "lowest Etching entity" assumption — Ref contacts may
add etch openings.

## Where the Ref sites go — ask the user before writing geometry

"Expose contacts between the hook and the last recording el." Two readings, very different
cost:

- **A — in the gap ABOVE the centre contact.** Metal already runs there; you need extra
  `Electrodes`-layer openings plus a locally widened metal segment. Cheap.
- **B — BELOW the contact, toward the hook.** There is **no metal below the electrode pad
  today** (`create_metal_pad_wire_outline` only draws upward), and the polyimide there is
  only 20 µm wide, capping the site width unless `wide_wire_hw` grows — which pushes every
  other channel outward and widens the bundle.

The phrasing reads like B. Confirm which, and how wide/how many, before coding.

## Done when

- `python3 -m pytest electrode_bundle/tests -q` green
- all designs build; `python3 -m electrode_bundle.batch` does not crash
- a render showing the wrap-around, signed off by the user
- mapping JSON shows the Ref routed to pad 65, and 64 recording channels intact
  *(superseded 2026-09-17: the Ref was moved to the RIGHT column, so this is now pad 66 —
  `shapes.REF_PAD`. The rest of the brief still describes the wrap as built.)*
