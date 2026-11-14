"""(design, revision) -> the settings a stage runs with. The order is catalog.py's docstring.

    apply(cfg, design, rev, stage, band_ref=None, prov=None)   mutate an imported config module
    run_stage(design, rev, stage, mid, final, band_ref=None)  in a FRESH process: import, apply, run
    show(design, rev)                                         every setting + where it came from

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


def apply(cfg, design, rev, stage, band_ref=None, prov=None):
    """Apply design + revision chain to the imported config module `cfg`, in catalog order.
    `prov` (a dict) collects, per setting, the step that set it last."""
    prov = {} if prov is None else prov
    d = catalog.DESIGNS[design]
    _apply(cfg, d.set, "design %s" % design, prov)
    revs = catalog.chain(rev)
    for r in revs:
        _apply(cfg, r.set, "Rev%d" % r.n, prov)
    for r in revs:
        _apply(cfg, r.per_design.get(design, {}), "Rev%d per_design" % r.n, prov)
    if d.base == "dummy":
        for r in revs:
            _apply(cfg, r.dummy_set, "Rev%d dummy_set" % r.n, prov)
    gaps = [r for r in revs if r.via_edge_gap is not None]
    if gaps:
        _apply_via_edge_gap(cfg, gaps[-1].via_edge_gap, "Rev%d via_edge_gap=%g" % (gaps[-1].n, gaps[-1].via_edge_gap),
                            prov)
    if band_ref and stage == "route":
        cfg.BAND_REF_DXF = band_ref
        prov[BAND_REF] = "Rev%d band_ref_rev=%d" % (rev, catalog.REVISIONS[rev].band_ref_rev)
    return prov


def needs_band_ref(design, rev):
    """The revision whose stage-01 band stage 02 routes around, or None (dummies have no router)."""
    r = catalog.REVISIONS[rev].band_ref_rev
    return r if r and catalog.DESIGNS[design].route != "02_make_dummy.py" else None


def run_stage(design, rev, stage, mid, final, band_ref=None):
    """In a fresh process: import the config, apply the overrides, run one stage script."""
    os.chdir(HERE)
    d = catalog.DESIGNS[design]
    cfg = importlib.import_module("config_" + d.base)
    apply(cfg, design, rev, stage, band_ref)
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


def show(design, rev, stage="route", out=sys.stdout, changed_only=False):
    """Print every setting of (design, rev) with its source. Imports the config: one call per process."""
    d = catalog.DESIGNS[design]
    cfg = importlib.import_module("config_" + d.base)
    before = _flat(cfg)
    ref = needs_band_ref(design, rev)
    prov = apply(cfg, design, rev, stage, "<rev%d stage-01 DXF>" % ref if ref else None)
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

    r = catalog.REVISIONS[rev]
    w = out.write
    w("%s  at  Rev%d  (%s)\n" % (design, rev, r.status))
    w("  base config     config_%s.py\n" % d.base)
    w("  stages          %s -> %s\n" % (d.gen, d.route))
    w("  files           %s\n                  %s\n" % catalog.file_names(design, rev))
    w("  revision chain  %s\n" % " -> ".join("Rev%d" % x.n for x in catalog.chain(rev)))
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


def _short(v):
    s = repr(v)
    return s if len(s) <= 28 else s[:25] + "..."


def revisions(out=sys.stdout):
    """Every revision: its chain, status, wafer layers and what it changes."""
    for n in sorted(catalog.REVISIONS):
        r = catalog.REVISIONS[n]
        out.write("Rev%d  %s\n  built from: %s   files *%s   wafer layers: %s\n"
                  % (n, r.status, "the base configs" if r.parent is None else "Rev%d" % r.parent,
                     r.suffix or " (fabricated names)", r.layerset))
        for c in r.changes:
            out.write("  - %s\n" % c)
        out.write("\n")
    out.write("designs: %s\n" % ", ".join("%s (config_%s.py)" % (k, d.base) for k, d in catalog.DESIGNS.items()))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="every resolved setting of one design at one revision")
    ap.add_argument("--rev", type=int, choices=sorted(catalog.REVISIONS))
    ap.add_argument("--design", choices=sorted(catalog.DESIGNS))
    ap.add_argument("--stage", default="route", choices=("gen", "route"))
    ap.add_argument("--changed", action="store_true", help="only what the design / revisions change")
    a = ap.parse_args()
    if a.rev is None or a.design is None:
        revisions()
    else:
        show(a.design, a.rev, a.stage, changed_only=a.changed)
