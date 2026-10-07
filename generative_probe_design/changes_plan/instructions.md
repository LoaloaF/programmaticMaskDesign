Plan to change the current generation of probes.

1. Change the hook design. Save the old (current ) hook in a suitable format in a different file. then create a new file with a new hook spec. the hook should look like the image new_hook.png. With one small change the 6 claps on the side should be wider. right now they are 2um, but they should be e5um in the new design.

2. change the electrode shape. Right now it's a square with rounded edges. make it compeltely round and smooth the polyimide as in the image: new_circular_electrode.png 

3. We need to integrate the IONP patterns. it's not yet settles how we do it. generate a random design, should one design spec have different IONP patterns etc. 

4. Small one: get rid of lollipop side hook. while doing this, also fix the etching surrounding. right now the hook is partly etched away, and in other regions this distance is unecessarily big. 

5. Mario already had something lkie this called roof. we want something similar, but probably a nonlinear folding funciton where the length of fibers & where they start doesn't just change at some regular step interval. But a v1 can use a linear adjustment here like the roof.  For  v2, check the image bundling_v2_better_roof.png. THis is the nonlinear version. it's based on this github repo: https://github.com/Neurotechnology-at-ETH-Zurich/electrode2geometry/
Extract the important part from it. it could even be a hardcoded adjustment specifically for the 64 channels we have as well. This changes how the design gets gets translated into a physical design. The json file should inidcate the y location of an electrode on the wafer, and then when bundled up. 

6. Big one: we want to not have a normal recording electrode on the central fiber with the hook, but instead expose contacts bewteen the hook and last reocrding el. these wide reocrding sites will be used as a Ref electrode. This unfortuantely has deep triclkle down effects up to the PCB. because the whole routing needs to fleip where (hopefully possible?) In that the bottom two contact pads right now are not being used, bc they are the Ref and Gnd pads of the  flexPCB. to route them to the center everything needs to be flipped. but that's just an inversion in channel ordering, so can be recovered afterwards. in anycase, how those Ref istes exactly get exposed needs to be defined in some spec as well, buut will be shared across all probes.

---

## Status (2026-10-05) — all six items have landed

1. **Hook** — done. The old hook is archived as `electrode_bundle/hooks/legacy_barb.py`; the
   new one is the parametric `electrode_bundle/hooks/teardrop_clap.py` (5 µm claps). Picked by
   `BundleConfig.hook`. Brief: [`BRIEF_tip_geometry.md`](BRIEF_tip_geometry.md).
2. **Circular electrode** — done: round opening / coating / gold pad with a smooth polyimide
   swelling (`shapes.create_polyimide_outline`). Same brief.
3. **IONP patterns** — done, though not as first briefed: a frozen bank
   (`electrode_bundle/ionp_patterns.py`), four ids per geometry, every geometry built in four
   barcoded variants. Brief (superseded): [`BRIEF_ionp.md`](BRIEF_ionp.md).
4. **Lollipop hook removed, etch surround fixed** — done; the probe has one etch hole, the loop.
5. **Bundling ("roof")** — done as `electrode_bundle/bundling.py` (`TwoArcFold`, after
   electrode2geometry). It staggers each fiber's shoulder, and `<name>_electrodes.json` records
   every electrode's wafer **and** bundled y.
6. **Ref electrode on the centre fiber** — done: 65 fibers, Ref contacts in two bands, routed
   over the top of the pad field to flex pad 66. Brief: [`BRIEF_ref_electrode.md`](BRIEF_ref_electrode.md).

Current state of all of it: [`../electrode_bundle/HANDOVER.md`](../electrode_bundle/HANDOVER.md).

