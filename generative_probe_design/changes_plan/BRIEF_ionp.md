# Brief: IONP barcode integration (plan item 3)

> **Status: superseded (historical record).** Written for the earlier 8-design set (C1–C4, H1,
> H2, 3140-8970, 980-6500), none of which exist any more. What landed instead: a frozen pattern
> bank in `electrode_bundle/ionp_patterns.py`, `DesignSpec.ionp_pattern_id` (one id per spec,
> not a `magnetic_ids` tuple), and `design_sets.DESIGNS` expanding each of the four current
> geometries into four barcoded variants via `ionp_patterns.DESIGN_IDS`. Step 2's two bugs were
> fixed (`config.variant_tag`; batch always writes the barcode-free DXF). For the current
> scheme read [`../electrode_bundle/HANDOVER.md`](../electrode_bundle/HANDOVER.md) §3.5 + §5.

Separate Claude Code session, run from `generative_probe_design/`.
Env: `source /opt/anaconda3/etc/profile.d/conda.sh && conda activate dxf`

**Stay out of** `shapes.py`, `bundle.py`, `mapping.py`, `bundling.py`, `lengths.py`,
`spacing.py` — other sessions own those. You own `ionp.py`, the IONP constants in
`config.py`, `magnetic_ids` on `DesignSpec`, and the id-expansion loop in `batch.py`.

Read `electrode_bundle/HANDOVER.md` §3.5 + §5 first, then `ionp.py`.

## Measured starting point

Every design × every id 0–15 was run. **The 700-well floor is never the problem** — min well
count across all 128 combinations is 700. Every failure is `min_strip_distance`:

| design | array span | ids passing |
|---|---|---|
| C1, C2, C3, H1, H2 | 5590–6655 | **16/16** |
| C4 | 4888 | **8/16** |
| 3140-8970 | 3140 | **2/16** |
| 980-6500 | 980 | **0** (id 0 "passes" only because its codeword is all zeros = no data stripes) |

## Do these four, in order

1. **`ID_TOP_ANCHOR_CENTER = 0.96 → 0.965`** in `config.py`. C4 goes **8/16 → 16/16**, and
   no other design regresses (margins improve on four of them). C4's failures were all the
   same single violation: top anchor 292.7 µm above the `0.86` data slot, needing 300. This
   is a tuning bug, not a short-probe problem. Do it first, independent of everything else.

2. **Fix two bugs that fire the moment any id is set** (both reproduced):
   - `batch.py` writes `electrode_bundle_{name}C{NN}.dxf` for id designs and never the
     plain path, but `side_by_side.py` reads `bundle_dxf_path(spec.name)` → `FileNotFoundError`,
     breaking the default `python3 -m electrode_bundle.batch`. Fix with a shared
     `variant_tag(name, magnetic_id)` helper in `config.py` next to `bundle_dxf_path`, and/or
     always write the barcode-free geometry DXF too.
   - Grown anchor bands are not clamped to `y_top`. On 980-6500 the top anchor reports height
     350.0 µm while `y_top` cuts it at 222 µm — `check_design_rules` and the info JSON both
     record geometry that is not what gets fabricated. Clamp, and make the report match.

3. **`DesignSpec.magnetic_id` → `magnetic_ids: Tuple[int, ...] = ()`.** One spec expands into
   N built artefacts rather than duplicating the geometry row per id. Keeps `DESIGNS` 1:1 with
   design-doc rows so `doc_id` provenance and single-source geometry survive. Validate range
   0–15 and reject duplicates in `__post_init__`. Add a `--ids` CLI override for exploration
   only — the committed spec is what records real hardware.
   **Leave all 8 real designs at `()`.** Do not invent ids for real hardware.

4. **Add a synthetic demo** so the barcode can be judged from pictures — plus run the existing
   `python3 -m electrode_bundle.main legend` (zero code, renders all 16 ids):
   ```python
   #NOT a doc row -- scratch geometry for eyeballing the barcode. Never fabricate.
   DesignSpec(name="DEMO", n_channels=64, sites=Uniform(span=6000),
              fiber_length=9000, loop_offset=445,
              overall_length=FAMILY_OVERALL_LENGTH,
              magnetic_ids=(1, 7, 11), doc_id="synthetic demo, not a doc row")
   ```

## Short probes — do NOT decide alone

`980-6500` (980 µm array) has capacity for **0** data slots and cannot carry a
distinguishable barcode under any combination tested. `magnetic_ids=()` with a comment
stating the number is the correct answer.

`3140-8970` fits 5 slots → a shortened A(5,3) code = **4 ids, still single-error-correcting**.
That is the principled degradation: you lose id space, not error correction.

**Ask the user before touching `min_strip_distance` (300 µm) or `ionp_well_distance` (5 µm).**
Relaxing 300 → 200 makes short probes pass but raises the stripe-misread rate the Hamming
code was sized against — it weakens the correction and the thing it corrects at once.
Denser wells are a fab/litho question, not a free knob.

## Done when

- all 8 designs still build; `python3 -m electrode_bundle.batch` (no flags) does not crash
- `python3 -m pytest electrode_bundle/tests -q` green (52 currently)
- a table of design × id pass/fail, before and after step 1
- DEMO renders shown to the user
