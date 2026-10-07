# Brief: tip geometry (plan items 1, 4, then 2)

> **Status: landed (historical record).** The lollipop/rescue hook is gone, the old hook lives
> on as `electrode_bundle/hooks/legacy_barb.py`, the new one is `hooks/teardrop_clap.py`, and
> the site is round. The loop datum is now `BundleResult.loop_xy`, stamped into the DXF header
> as `LOOP_Y_UM` — not "the lowest Etching entity". Line numbers and function names below
> (`shapes.HOOK_TEMPLATE`, `create_hook_polygon`, `rescue_hook_*`, `l = 13`, `l_contact = 11`)
> describe the code *before* this work. Current state:
> [`../electrode_bundle/HANDOVER.md`](../electrode_bundle/HANDOVER.md).

For a **separate Claude Code session**, run from `generative_probe_design/`. These items are
grouped because they all edit the same region of the same files and must not be done in
parallel with each other. Item 5 (bundling/roof) and the IONP + Ref-electrode analyses are
being handled elsewhere — don't touch `spacing.py`, `lengths.py`, `design_sets.py`,
`ionp.py` or `mapping.py`.

Start by reading `electrode_bundle/design_sets.py`'s module docstring — it has a labelled
diagram of the whole device and names every length parameter.

Environment: `source /opt/anaconda3/etc/profile.d/conda.sh && conda activate dxf`
Build + look: `python3 -m electrode_bundle.batch --no-render` then open
`64Ch_4Shankdesigns/shanks/all_designs_side_by_side.dxf`. Tests: `python3 -m pytest electrode_bundle/tests -q`
(44 currently pass — keep them passing).

---

## Do these in order: 4 → 1 → 2

Item 4 first because removing the lollipop simplifies the tip before item 1 reshapes it.

### Item 4 — remove the lollipop hook, fix the etch surround

The "rescue" hook is a lollipop on channel 1. Remove it and fix the etching:
> "right now the hook is partly etched away, and in other regions this distance is
> unnecessarily big."

Code:
- `shapes.create_rescue_hook_polygon()` (~line 448) and `shapes.rescue_hook_circle_centre()`
  (~line 498) — the shape and its etch-hole centre
- `bundle.py` ~lines 216-233 — where it is placed, and `rescue_etch`
- `config.BundleConfig` — `second_channel`, `hook_on_second_channel`, `rescue_hook_drop`,
  `rescue_hook_angle_deg`, `rescue_hook_arm_len`, `rescue_hook_circle_width`,
  `rescue_hook_circle_r`, `rescue_hook_etch_r`

**⚠ This breaks two things — they are not obvious and they are load-bearing:**

1. `lengths.loop_y()` finds the insertion loop as *"the LOWER of the two etch holes (the
   other is the rescue hook)"*. With only one etch hole left that comment is wrong and the
   `sorted(...)[0]` is fragile. `lengths.solve_lengths()` uses it to hit `fiber_length` and
   `loop_offset` exactly, and **every design's geometry is solved through it** — if the
   datum shifts, all 8 designs silently change length.
2. `side_by_side.datum_y()` uses the same "lower of two etch holes" assumption to align
   designs in the combined DXF.

Update both, and re-run the batch to confirm each design still reports its spec'd
`fiber_length` / `loop_offset` (there is a test, `test_lengths.py`, that pins this).

The etch geometry itself is `cfg.hook_etch_rx/ry/dx/dy` for the main hook — an ellipse
placed in `bundle.py` ~line 211.

### Item 1 — new hook

> "Save the old (current) hook in a suitable format in a different file. then create a new
> file with a new hook spec. the hook should look like `new_hook.png`. With one small change
> the 6 claps on the side should be wider. right now they are 2um, but they should be 5um."

Current hook is a **hardcoded 38-point coordinate template**, `shapes.HOOK_TEMPLATE`
(~line 411), normalised and placed by `shapes.create_hook_polygon()` (~line 427). It has
2 barbs. The new one (see `new_hook.png`) is different in kind:

- a bulbous **teardrop tip** rather than the current barbed arrowhead
- an **elliptical etch hole**, dimensioned 35 wide × 45.03 tall in the image
- **6 claps, 3 per side**, angled up and outward with rounded ends
- clap width **2 µm → 5 µm** (the `2.1257413` callout in the image is the current width)
- other callouts in the image: `7.1331745` and `5.885413`

"Save the old hook in a suitable format in a different file" maps naturally onto: move
`HOOK_TEMPLATE` out of `shapes.py` into a hook-spec module where each hook is a **named
spec** (template points or a parametric builder), so old and new coexist and a design can
choose. Consider whether the new hook should be parametric (clap count, clap width, tip
radii, etch ellipse) rather than another hardcoded point list — the user explicitly wants
one dimension tunable, which argues for parametric.

`hook_drop` must keep working: `lengths.solve_lengths()` tunes it to hit `loop_offset`, and
the relation is currently `loop_offset = hook_drop + 340.1` (linear, slope 1). The solve is
numeric so the constant may change freely, **but the relation must stay monotonic in
`hook_drop` with slope 1**, or `solve_lengths` will fail its assert. Also note the current
hook floor: `hook_drop=0` gives `loop_offset = 340.1 µm`, and the design doc has rows
wanting 280 and 320, which the current hook cannot reach. A smaller new hook may unlock
those — worth checking and reporting.

### Item 2 — circular electrode

> "Right now it's a square with rounded edges. make it completely round and smooth the
> polyimide as in `new_circular_electrode.png`."

- contact pad: `l_contact = 11` square → circle; `bundle.py` ~line 168
- metal pad under it: `l = 13` square → circle; `shapes.create_metal_pad_wire_outline()`
- polyimide around it: `shapes.create_polyimide_outline()` — the image shows a smooth
  lens/teardrop swelling rather than today's rounded square
- `config`: `metal_pad_arc_radius`, `contact_pad_arc_radius`, `polyimide_arc_radius` become
  meaningless for a circle — decide whether to keep them for other users or retire them
- `shapes.create_polygon_circle()` already exists (used for bond pads) — reuse it

Watch out: `ionp.drop_wells_near_contacts()` uses `keepout_radius` around contact centres,
and `lengths.measure()` derives the site span from `electrode_locs`. Neither should care
about pad *shape*, but confirm the span is unchanged after the switch.

---

## Definition of done

- `python3 -m pytest electrode_bundle/tests -q` still green
- `python3 -m electrode_bundle.batch --no-render` builds all 8, and each still reports its
  spec'd `fiber_length` / `loop_offset` / span
- the tip renders as in the reference images — **show the user a render and get sign-off
  before committing**, they asked to be in the loop on polyimide detail
- note anything that changed the loop datum, since that shifts every design's geometry
