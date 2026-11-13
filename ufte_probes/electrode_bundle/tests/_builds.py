"""Shared, memoised probe builds for the whole test session.

A full 64-channel `build_bundle` takes ~5 s, and `config_for(spec)` costs several more (it
runs `solve_lengths`, which iterates builds). The suite used to rebuild the same probes over
and over -- ~200 builds, 16+ minutes. Builds are pure functions of their config and no test
mutates a result (tests copy arrays before editing them; `saveas` does not touch the doc),
so one build per distinct config is shared by every test that asks for it.

    build(cfg)          build_bundle(cfg), memoised on the config's repr
    cfg_for(spec)       config_for(spec), memoised per (geometry, barcode id)
    GEOMETRIES          one DesignSpec per geometry: DESIGNS is 4 geometries x 4 barcode ids,
                        and geometry tests need each probe once, not four times
"""
from electrode_bundle.batch import config_for as _config_for
from electrode_bundle.bundle import build_bundle as _build_bundle
from electrode_bundle.design_sets import DESIGNS

_BUILDS = {}
_CFGS = {}


def build(cfg):
    key = repr(cfg)
    if key not in _BUILDS:
        _BUILDS[key] = _build_bundle(cfg)
    return _BUILDS[key]


def cfg_for(spec):
    key = (spec.name, spec.ionp_pattern_id)
    if key not in _CFGS:
        _CFGS[key] = _config_for(spec)
    return _CFGS[key]


def build_for(spec):
    """The probe of `spec`'s geometry. Contacts, lengths and outline do not depend on the
    barcode id (it only changes the label text), so all four variants share one build."""
    first = next(s for s in DESIGNS if s.name == spec.name)
    return build(cfg_for(first))


GEOMETRIES = [s for i, s in enumerate(DESIGNS) if s.name not in {t.name for t in DESIGNS[:i]}]
