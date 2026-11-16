"""(design, revision) -> the settings a stage runs with. The order is catalog.py's docstring.

    apply(cfg, design, wafer, rev, stage, band_ref=None, prov=None)   mutate an imported config module
    run_stage(design, wafer, rev, stage, mid, final, band_ref=None)  in a FRESH process: import, apply, run
    show(design, wafer, rev)                                         every setting + where it came from

A revision is (wafer, rev): wafer a maskgen/core/wafers.py key ("wafer4"), rev its revision number.
A config module is imported once per process and mutated in place, so a process resolves ONE
(design, revision): build_designs.py runs every stage in its own process.
"""
import importlib
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import catalog  # noqa: E402

BAND_REF = "BAND_REF_DXF"


def _apply(mod, overrides, label, prov):
    for key, val in overrides.items():
        if isinstance(val, dict):
            getattr(mod, key).update(val)
            for k in val:
                prov["%s.%s" % (key, k)] = label
        else:
            setattr(mod, key, val)
            prov[key] = label


def _apply_via_edge_gap(cfg, gap, label, prov):
    """Every via's edge `gap` um from the pad edge; the routing via stays in the top-left corner."""
    inset = gap + cfg.GEN_ROUTING["via_radius"]
    cfg.GEN_ROUTING["via_offset_x"] = inset
    cfg.GEN_ROUTING["via_offset_y"] = cfg.GEN_CONFIG["pad_side"] / 2.0 - inset
    cfg.GEN_CONFIG["etch_via_inset_extra"] = 0.0
    for k in ("GEN_ROUTING.via_offset_x", "GEN_ROUTING.via_offset_y", "GEN_CONFIG.etch_via_inset_extra"):
        prov[k] = label


def apply(cfg, design, wafer, rev, stage, band_ref=None, prov=None):
    """Apply design + revision chain to the imported config module `cfg`, in catalog order.
    `prov` (a dict) collects, per setting, the step that set it last."""
    prov = {} if prov is None else prov
    d = catalog.DESIGNS[design]
    _apply(cfg, d.set, "design %s" % design, prov)
    revs = catalog.chain(wafer, rev)
    for w, r in revs:
        _apply(cfg, r.set, catalog.label(w, r.n), prov)
    for w, r in revs:
        _apply(cfg, r.per_design.get(design, {}), catalog.label(w, r.n) + " per_design", prov)
    if d.base == "dummy":
        for w, r in revs:
            _apply(cfg, r.dummy_set, catalog.label(w, r.n) + " dummy_set", prov)
    gaps = [(w, r) for w, r in revs if r.via_edge_gap is not None]
    if gaps:
        w, r = gaps[-1]
        _apply_via_edge_gap(cfg, r.via_edge_gap, "%s via_edge_gap=%g" % (catalog.label(w, r.n), r.via_edge_gap),
                            prov)
    if band_ref and stage == "route":
        cfg.BAND_REF_DXF = band_ref
        prov[BAND_REF] = "%s band_ref_rev=%s" % (catalog.label(wafer, rev), catalog.label(*catalog.band_ref(wafer, rev)))
    return prov


def needs_band_ref(design, wafer, rev):
    """(wafer, n) whose stage-01 band stage 02 routes around, or None (dummies have no router)."""
    r = catalog.band_ref(wafer, rev)
    return r if r and catalog.DESIGNS[design].route != "02_make_dummy.py" else None


def run_stage(design, wafer, rev, stage, mid, final, band_ref=None):
    """In a fresh process: import the config, apply the overrides, run one stage script."""
    os.chdir(HERE)
    d = catalog.DESIGNS[design]
    cfg = importlib.import_module("config_" + d.base)
    apply(cfg, design, wafer, rev, stage, band_ref)
    script = getattr(d, stage)
    argv = {"01_generate.py": ["--config", d.base], "02_route.py": ["--design", d.base]}.get(script, [])
    argv += ["--out", mid] if stage == "gen" else ["--in", mid, "--out", final]
    sys.argv = [script] + argv
    runpy.run_path(script, run_name="__main__")


# ----------------------------------------------------------------------------- show
def _flat(mod):
    out = {}
    for k, v in vars(mod).items():
        if k.startswith("_") or callable(v) or type(v).__name__ == "module" or not k.isupper():
            continue
        if isinstance(v, dict):
            for kk, vv in v.items():
                out["%s.%s" % (k, kk)] = vv
        else:
            out[k] = v
    return out


def show(design, wafer, rev, stage="route", out=sys.stdout, changed_only=False):
    """Print every setting of (design, wafer, rev) with its source. Imports the config: one call per process."""
    d = catalog.DESIGNS[design]
    cfg = importlib.import_module("config_" + d.base)
    before = _flat(cfg)
    ref = needs_band_ref(design, wafer, rev)
    prov = apply(cfg, design, wafer, rev, stage, "<%s stage-01 DXF>" % catalog.label(*ref) if ref else None)
    after = _flat(cfg)

    # Derived values are computed at import from the BASE config and not recomputed after the overrides
    # (that is what the fabricated designs were built with). Recompute them here to show where that matters.
    import config_common

    class _Rec(dict):                        # records which keys derive() writes
        def __setitem__(self, k, v):
            written.add(k)
            dict.__setitem__(self, k, v)
    written = set()
    try:
        redo = config_common.derive(_Rec({k: getattr(cfg, k) for k in dir(cfg) if k.isupper()}))
        derived = {k: redo[k] for k in sorted(written)}
    except KeyError:                         # this base config does not use config_common (dummy)
        derived = {}
    stale = {k: v for k, v in derived.items() if v != getattr(cfg, k)}

    r = catalog.get(wafer, rev)
    w = out.write
    w("%s  at  %s  (%s)\n" % (design, catalog.label(wafer, rev), r.status))
    w("  base config     config_%s.py\n" % d.base)
    w("  stages          %s -> %s\n" % (d.gen, d.route))
    w("  files           devices/%s/: %s, %s\n" % ((design,) + catalog.file_names(design)))
    w("  revision chain  %s\n" % " -> ".join(catalog.label(x, y.n) for x, y in catalog.chain(wafer, rev)))
    w("  wafer layers    %s\n" % r.layerset)
    w("  design note     %s\n\n" % d.note)
    w("  %-44s %-28s %s\n" % ("setting", "value", "set by"))
    for k in sorted(after):
        if changed_only and k not in prov and k not in stale:
            continue
        v = after[k]
        src = prov.get(k)
        if src is None:
            src = "config_%s.py" % d.base if k in before else "?"
            if k.split(".")[0] in derived:
                src = "derived at import" + ("  ** STALE: would be %r **" % stale[k] if k in stale else "")
        mark = "*" if k in prov else " "
        w("%s %-44s %-28s %s\n" % (mark, k, _short(v), src))
    w("\n  * = changed by the design or a revision (%d settings)\n" % len(prov))
    if stale:
        w("  STALE derived values (kept on purpose -- the fabricated designs used them): %s\n" % ", ".join(sorted(stale)))
    elif derived:
        w("  derived values (%s): recomputing them on these settings gives the same -- none stale\n"
          % ", ".join(sorted(derived)))
    w("  (values the config file computes itself after its knobs -- FAN2_G, FAN_LEN_MM, ... -- are also\n"
      "   fixed at import; none of the overrides above feeds them today)\n")


def summary(design, wafer, rev):
    """What device.json says about (design, wafer, rev): identity, revision chain, every setting a design
    or revision changed (value + who set it), and the base geometry knobs. Imports the config: one call
    per process."""
    from maskgen.core import wafers
    d = catalog.DESIGNS[design]
    cfg = importlib.import_module("config_" + d.base)
    ref = needs_band_ref(design, wafer, rev)
    prov = apply(cfg, design, wafer, rev, "route", "ref" if ref else None)
    flat = _flat(cfg)
    r = catalog.get(wafer, rev)
    gen, final = catalog.file_names(design)
    out = {
        "device": design, "note": d.note,
        "wafer": wafer, "wafer_name": wafers.get(wafer)["name"], "rev": rev, "status": r.status,
        "base_config": "config_%s.py" % d.base, "stages": [d.gen, d.route],
        "files": {"device": final, "pad_grid": gen},
        "revision_chain": [catalog.label(w, x.n) for w, x in catalog.chain(wafer, rev)],
        "changes_this_revision": list(r.changes),
        "wafer_layers": r.layerset,
        "pad_grid": {k: flat.get("GEN_CONFIG." + k) for k in
                     ("pad_side", "pad_side_extra", "gap", "n_rows", "n_cols", "pad_etch", "metal3")},
        "changed_settings": {k: {"value": flat.get(k, getattr(cfg, k, None)), "set_by": v}
                             for k, v in sorted(prov.items()) if k != BAND_REF},
    }
    for k in ("TOTAL_WIRES", "N_BLOCKS_PER_COLUMN"):
        if hasattr(cfg, k):
            out[k.lower()] = getattr(cfg, k)
    if ref:
        out["band_reference"] = {"revision": catalog.label(*ref),
                                 "file": "ref_%s/%s" % (("rev%d" % ref[1]) if ref[0] == wafer else "%s_rev%d" % ref, gen)}
    return out


def _short(v):
    s = repr(v)
    return s if len(s) <= 28 else s[:25] + "..."


def revisions(wafer=None, out=sys.stdout):
    """Every revision (of `wafer`, or of every wafer): its parent, status, wafer layers and what it changes."""
    for wk in ([wafer] if wafer else list(catalog.REVISIONS)):
        for n in sorted(catalog.REVISIONS[wk]):
            r = catalog.get(wk, n)
            p = catalog._ref(wk, r.parent)
            out.write("%s  %s\n  built from: %s   wafer layers: %s\n"
                      % (catalog.label(wk, n), r.status, "the base configs" if p is None else catalog.label(*p),
                         r.layerset))
            for c in r.changes:
                out.write("  - %s\n" % c)
            out.write("\n")
    out.write("designs: %s\n" % ", ".join("%s (config_%s.py)" % (k, d.base) for k, d in catalog.DESIGNS.items()))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="every resolved setting of one design at one revision")
    ap.add_argument("--wafer", choices=sorted(catalog.REVISIONS))
    ap.add_argument("--rev", type=int)
    ap.add_argument("--design", choices=sorted(catalog.DESIGNS))
    ap.add_argument("--stage", default="route", choices=("gen", "route"))
    ap.add_argument("--changed", action="store_true", help="only what the design / revisions change")
    a = ap.parse_args()
    if a.rev is None or a.design is None or a.wafer is None:
        revisions(a.wafer)
    else:
        show(a.design, a.wafer, a.rev, a.stage, changed_only=a.changed)
