"""Build every design on an interconnect wafer, for one wafer revision.

    python3 build_designs.py --rev 3                      # all five -> designs/rev3/
    python3 build_designs.py --rev 3 --only 8block_71_15  # one of them
    python3 build_designs.py --rev 2 --out-dir /tmp/rev2  # Rev2 settings, e.g. as a regression
    python3 build_designs.py --rev 3 --stage01 --out-dir designs/rev3/draft   # pad grids only, ~5 s

A design is a config plus a few overrides (VARIANTS -- the HANDOVER §4.4 table as data), and a
revision is a few more (REVISIONS). Nothing is edited on disk: each stage runs in its own
process, imports its config, applies the overrides in memory and runs the stage script, so
the configs keep their defaults and one design never leaks into the next.

Stages per design: stage 01 (pad grid -> DXF), then stage 02 (route into the connector ->
DXF, or cap the dummy -> GDS). Logs go to <out-dir>/logs/. The run FAILS (exit 1) if a stage
exits non-zero or a router reports crossings, pad hits or a FAIL line -- checks the routers
themselves only print.
"""
import argparse
import os
import re
import runpy
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))

# name -> config module, stage scripts, file stems (stage-01 output, final output), overrides.
_8B_71_15 = {"GEN_CONFIG": {"pad_side": 73.0, "pad_side_extra": 71.0, "gap": 13.0},
             "GEN_ROUTING": {"via_offset_y": 33.75}, "N_RIGHT_BULBS": 1}
_DUMMY_35_36 = {"GEN_CONFIG": {"pad_side": 37.0, "pad_side_extra": 35.0, "gap": 34.0},
                "GEN_ROUTING": {"via_offset_y": 15.75}}
VARIANTS = {
    "12block": dict(config="12block", gen="01_generate_12block.py", route="02_route_12block.py",
                    mid="new_interconnect_circular_12Block_56_15",
                    final="new_interconnect_with_connector_chamfered_12Block_56_15.dxf", set={}),
    "8block_51_35": dict(config="8block", gen="01_generate_8block.py", route="02_route_8block.py",
                         mid="new_interconnect_circular_8block_51_35",
                         final="new_interconnect_with_connector_8blocks_chamfered_1layer_51_35.dxf",
                         set={}),
    "8block_71_15": dict(config="8block", gen="01_generate_8block.py", route="02_route_8block.py",
                         mid="new_interconnect_circular_8block_71_15",
                         final="new_interconnect_with_connector_8blocks_chamfered_1layer_71_15.dxf",
                         set=_8B_71_15),
    "dummy_61_10": dict(config="dummy", gen="01_generate_12block.py", route="02_make_dummy.py",
                        mid="new_interconnect_circular_dummy_61_10",
                        final="dummy_new_interconnect_circular_dummy_61_10_padrows.gds", set={}),
    "dummy_35_36": dict(config="dummy", gen="01_generate_12block.py", route="02_make_dummy.py",
                        mid="new_interconnect_circular_dummy_35_36",
                        final="dummy_new_interconnect_circular_dummy_35_36_padrows.gds",
                        set=_DUMMY_35_36),
}

# revision -> pad-stack overrides (applied to every design), output folder, file-name suffix.
REVISIONS = {
    2: dict(set={"GEN_CONFIG": {"pad_etch": "square", "metal3": False}},
            out_dir=os.path.join(HERE, "designs"), suffix=""),
    3: dict(set={"GEN_CONFIG": {"pad_etch": "vias", "metal3": True}},
            out_dir=os.path.join(HERE, "designs", "rev3"), suffix="_rev3"),
    # Rev4: Rev3 with EVERY via -- the routing via (Etching) and the PI-etch circles -- having its
    # EDGE `via_edge_gap` um from the pad edge (centre = gap + via radius = 5 um in). The routing via
    # stays in the top-left corner, now inset the same way in x and y, so via_offset_y follows
    # pad_side (see _apply_via_edge_gap); the etch circles lose their extra inset and
    # etch_via_layout moves the corner neighbours out as far as the 3 um gaps need.
    # The 5 um inset leaves less room between corner neighbours, so the spacing range's lower bound
    # drops to 10.5 um: 51_35 gets 13.5 um (1 between its neighbours), dummy 35_36 gets 11 um (none).
    # It also gives every Metal1 wire a round 7 um landing pad on its routing via, blended in by a
    # teardrop (GEN_ROUTING m1_pad*).
    # The 12-block lanes (and the dummies') leave only 12.5 um between the two lanes beside a column's top
    # pad, so there the top pad gets a 3.5 um via on a 5.5 um Metal1 pad, centred between the lanes
    # (0.25 um left), and the lower pads' vias move 0.5 um right, away from the previous column's last
    # lane -- Metal1 then holds the 2.5 um wire gap everywhere (REV4_LANES, applied per design).
    # Rev4 also splits the pad-perimeter stabilization vias off the PI outline onto their own DXF layer
    # (etch_via_layer; wafer layer 1 = its own mask, etched BEFORE the PI outline) and pulls every
    # Metal3 square 2 um in from each side of Metal2's pad (metal3_inset).
    4: dict(set={"GEN_CONFIG": {"pad_etch": "vias", "metal3": True, "etch_via_spacing": (9.5, 21.0),
                                "etch_via_layer": "PI_Stab_Via", "metal3_inset": 2.0},
                 "GEN_ROUTING": {"m1_pad": True},
                 "GEN_POLYIMIDE": {"trim_xmax": 2.25}},     # polyimide outline stays where Rev3 had it
            per_design={n: {"GEN_ROUTING": {"via_k0_radius": 1.75, "via_k0_dx": -0.25, "via_rest_dx": 0.5}}
                        for n in ("12block", "dummy_61_10", "dummy_35_36")},
            via_edge_gap=2.5,
            # dummies also get a small polyimide handle on the top edge (02_make_dummy.py)
            dummy_set={"DUMMY": {"handle_radius": 300.0, "handle_fillet": 150.0, "handle_x_frac": 0.62}},
            band_ref_rev=3,       # stage 02 keeps Rev3's band: Metal2 / vias / connector stay put
            out_dir=os.path.join(HERE, "designs", "rev4"), suffix="_rev4"),
}


def _with_suffix(fname, suffix):
    stem, ext = os.path.splitext(fname)
    return stem + suffix + ext


def paths(name, rev, out_dir):
    """(stage-01 DXF, final file) for design `name` at revision `rev`."""
    v, r = VARIANTS[name], REVISIONS[rev]
    return (os.path.join(out_dir, v["mid"] + r["suffix"] + ".dxf"),
            os.path.join(out_dir, _with_suffix(v["final"], r["suffix"])))


def _apply(mod, overrides):
    for key, val in overrides.items():
        if isinstance(val, dict):
            getattr(mod, key).update(val)
        else:
            setattr(mod, key, val)


def _apply_via_edge_gap(cfg, gap):
    """Every via's edge `gap` um from the pad edge; the routing via stays in the top-left corner."""
    inset = gap + cfg.GEN_ROUTING["via_radius"]
    cfg.GEN_ROUTING["via_offset_x"] = inset
    cfg.GEN_ROUTING["via_offset_y"] = cfg.GEN_CONFIG["pad_side"] / 2.0 - inset
    cfg.GEN_CONFIG["etch_via_inset_extra"] = 0.0


def _run_stage(name, rev, stage, mid, final, band_ref=None):
    """In a fresh process: import the config, apply the overrides, run one stage script."""
    import importlib
    os.chdir(HERE)
    sys.path.insert(0, HERE)
    v = VARIANTS[name]
    cfg = importlib.import_module("config_" + v["config"])
    _apply(cfg, v["set"])
    _apply(cfg, REVISIONS[rev]["set"])
    _apply(cfg, REVISIONS[rev].get("per_design", {}).get(name, {}))
    if v["config"] == "dummy":
        _apply(cfg, REVISIONS[rev].get("dummy_set", {}))
    if "via_edge_gap" in REVISIONS[rev]:
        _apply_via_edge_gap(cfg, REVISIONS[rev]["via_edge_gap"])
    if band_ref and stage == "route":
        cfg.BAND_REF_DXF = band_ref
    script = v[stage]
    argv = ["--config", v["config"]] if script == "01_generate_12block.py" else []
    argv += ["--out", mid] if stage == "gen" else ["--in", mid, "--out", final]
    sys.argv = [script] + argv
    runpy.run_path(script, run_name="__main__")


def _check_log(text):
    """Problems a stage printed without failing on them."""
    probs = []
    for m in re.finditer(r"crossings=(\d+), pad_hits=(\d+)", text):
        if m.group(1) != "0" or m.group(2) != "0":
            probs.append(m.group(0))
    probs += [ln.strip() for ln in text.splitlines() if re.search(r"\bFAIL\b", ln)]
    return probs


def build(name, rev, out_dir, stages=("gen", "route")):
    mid, final = paths(name, rev, out_dir)
    logs = os.path.join(out_dir, "logs")
    os.makedirs(logs, exist_ok=True)
    ref = None
    ref_rev = REVISIONS[rev].get("band_ref_rev")
    if ref_rev and "route" in stages and VARIANTS[name]["route"] != "02_make_dummy.py":
        # the earlier revision's stage-01 DXF, regenerated here so the reference always matches the code
        ref_dir = os.path.join(out_dir, "ref_rev%d" % ref_rev)
        os.makedirs(ref_dir, exist_ok=True)
        ref = paths(name, ref_rev, ref_dir)[0]
        n, probs = build(name, ref_rev, ref_dir, stages=("gen",))
        if probs:
            return name, ["band reference (Rev%d) failed: %s" % (ref_rev, p_) for p_ in probs]
    for stage in stages:
        log = os.path.join(logs, "%s_%s.log" % (name, stage))
        cmd = [sys.executable, os.path.abspath(__file__), "--_stage", name, str(rev), stage, mid, final]
        if ref and stage == "route":
            cmd.append(ref)
        p = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True)
        open(log, "w").write(p.stdout + p.stderr)
        if p.returncode != 0:
            return name, ["%s exited %d -- see %s" % (stage, p.returncode, log)]
        probs = _check_log(p.stdout)
        if probs:
            return name, ["%s: %s -- see %s" % (stage, pr, log) for pr in probs]
    return name, []


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--_stage":
        name, rev, stage, mid, final = sys.argv[2:7]
        _run_stage(name, int(rev), stage, mid, final, sys.argv[7] if len(sys.argv) > 7 else None)
        return
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rev", type=int, required=True, choices=sorted(REVISIONS))
    ap.add_argument("--only", nargs="+", choices=sorted(VARIANTS), default=sorted(VARIANTS))
    ap.add_argument("--out-dir", default=None,
                    help="default: designs/ for Rev2, designs/rev3/ for Rev3")
    ap.add_argument("--stage01", action="store_true",
                    help="stage 01 only (the pad grids), e.g. to review a pad change quickly")
    a = ap.parse_args()
    stages = ("gen",) if a.stage01 else ("gen", "route")
    out_dir = os.path.abspath(a.out_dir or REVISIONS[a.rev]["out_dir"])
    os.makedirs(out_dir, exist_ok=True)
    print("Rev%d -> %s: %s" % (a.rev, out_dir, ", ".join(a.only)))
    with ThreadPoolExecutor(max_workers=len(a.only)) as ex:
        results = list(ex.map(lambda n: build(n, a.rev, out_dir, stages), a.only))
    bad = 0
    for name, probs in results:
        print("  %-14s %s  %s" % (name, "FAIL" if probs else "ok",
                                  os.path.basename(paths(name, a.rev, out_dir)[0 if a.stage01 else 1])))
        for pr in probs:
            print("      " + pr)
        bad += bool(probs)
    if bad:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
