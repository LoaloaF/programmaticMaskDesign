"""EVERY mask layer, in one place: the DXF layers the generators draw on, the wafer (fab) layer each
one ends up on, its name, and how it looks in KLayout.

Two sides, kept apart on purpose:

  DESIGN side   the layer NAMES the generators write into their DXFs (IC, PROBE) and the GDS
                layers 02_make_dummy.py writes the dummies on (IC_DUMMY_GDS). These are the
                generators' own vocabulary and are the same for every revision.
  WAFER side    a LayerSet per wafer: which fab layer (GDS number on the mask set) each design
                layer goes to, the layer's name in the .oas, and its .lyp style. Fab numbers are
                per wafer -- the interconnect ones were renumbered by hand onto the template's
                alignment-mark layers (Rev2), and a new mask (Rev4's stabilization vias) takes a
                free mark slot.

    from maskgen.core import layers as L
    L.IC.METAL1                      -> "Metal1"           (DXF layer name)
    L.IC_REV4.dxf_map                -> {"metal1": (7, 0), ...}   lower-cased DXF name -> wafer
    L.IC_REV4.dummy_map              -> {(3, 0): (7, 0), ...}     dummy GDS layer -> wafer
    L.IC_REV4.names / .style / .order
    L.write_lyp(path, [(7, 0), ...], L.IC_REV4)

KLayout: the names travel in the .oas (LAYERNAME records); GDS2 cannot store them, and colours live
only in the .lyp sidecar (`klayout x.oas -l x.lyp`). KLayout draws entries further DOWN the layer
list IN FRONT, so a LayerSet lists its layers back -> front (Metal1 first: the back of the stack).

Pure standard library and Python-3.8 safe: KLayout's own Python imports this.
"""
from collections import namedtuple


class _Names(object):
    """A read-only bag of layer names: attribute access, iteration over the values."""

    def __init__(self, **names):
        self.__dict__.update(names)

    def __iter__(self):
        return iter(self.__dict__.values())

    def __setattr__(self, k, v):
        raise AttributeError("layer names are constants")


# =============================================================================
# DESIGN SIDE -- the generators' layer names
# =============================================================================
# mea1k_interconnects DXFs (stage 01 / 02).
IC = _Names(
    METAL1="Metal1",                  # wires, landing pads; the band stage 02 reads
    METAL2="Metal2",                  # pad squares (+ lifted odd wires, 12-block)
    METAL3="Metal3",                  # pad squares again, inset (Rev3+)
    VIA="Via",                        # layer-split vias (stage 02)
    ETCHING="Etching",                # routing vias + Metal2 release etching
    ETCHING_PAD="EtchingPad",         # Rev2 pad etch square (pad_etch="square")
    POLYIMIDE="Polyimide",            # positive device outline: build-only, never on a mask
    POLYIMIDE_NEG="Polyimide_Negative",   # PI outline etch, connector-pad openings, Rev3 etch circles
    PI_STAB_VIA="PI_Stab_Via",        # Rev4: pad-perimeter stabilization vias, own mask
)

# The GDS layers 02_make_dummy.py writes the dummies on (it converts from the DXF names above).
IC_DUMMY_GDS = {
    IC.ETCHING_PAD: (1, 0),
    IC.POLYIMIDE_NEG: (1, 0),
    IC.PI_STAB_VIA: (2, 0),
    IC.ETCHING: (5, 0),
    IC.METAL1: (3, 0),
    IC.METAL2: (6, 0),
    IC.METAL3: (8, 0),
}
IC_DUMMY_METAL = IC_DUMMY_GDS[IC.METAL1]      # bars, circles, extensions
IC_DUMMY_ETCH = IC_DUMMY_GDS[IC.ETCHING_PAD]  # circle etch windows + polyimide ring

# ufte_probes DXFs (electrode_bundle).
PROBE = _Names(
    METAL="Metal",                    # gold: traces, site pads, solder pads, ID labels
    ETCHING="Etching",                # device release: outline, loop hole, solder-pad openings
    PAD_ETCHING="pad_etching",        # site openings onto the gold (shallow etch)
    PEDOT_SIROF="PEDOT_SIROF",        # coating, slightly wider than the opening
    IONP="IONP",                      # IONP barcode wells
    ELECTRODES="Electrodes",          # build-only: recording contacts (dropped on save)
    REF_ELECTRODES="Ref_Electrodes",  # build-only: reference contacts (dropped on save)
    POLYIMIDE="Polyimide",            # build-only: positive body (dropped on save)
    LABELS="Labels",                  # side_by_side sheets only
)
PROBE_BUILD_ONLY = (PROBE.ELECTRODES, PROBE.REF_ELECTRODES, PROBE.POLYIMIDE)


# =============================================================================
# WAFER SIDE -- fab layers
# =============================================================================
# gds      the wafer GDS layer (datatype 0)
# name     its name in the .oas / .lyp
# dxf      DXF layer names (as drawn; matched case-insensitively) that go onto it
# dummy    dummy-GDS layers (02_make_dummy) that go onto it
# color, pattern, width   KLayout look (.lyp): metals red / orange / purple, etch masks blue /
#                         green; small features (vias) solid, large areas hatched
FabLayer = namedtuple("FabLayer", "gds name dxf dummy color pattern width")
_GREY = dict(color="#808080", pattern="I1", width=1)       # write_lyp's default for unknown layers


class LayerSet(object):
    """The fab layers of one wafer, listed BACK -> FRONT (= top -> bottom of KLayout's panel)."""

    def __init__(self, key, layers, note=""):
        self.key, self.layers, self.note = key, tuple(layers), note
        nums = [l.gds for l in self.layers]
        if len(set(nums)) != len(nums):
            raise ValueError("%s: a fab layer number is used twice" % key)

    def __getitem__(self, gds):
        for l in self.layers:
            if l.gds == gds:
                return l
        raise KeyError(gds)

    @property
    def dxf_map(self):
        """lower-cased DXF layer name -> (gds, 0)"""
        return {d.lower(): (l.gds, 0) for l in self.layers for d in l.dxf}

    @property
    def dummy_map(self):
        """dummy GDS (layer, datatype) -> (gds, 0)"""
        return {tuple(s): (l.gds, 0) for l in self.layers for s in l.dummy}

    @property
    def names(self):
        return {l.gds: l.name for l in self.layers}

    @property
    def style(self):
        return {l.gds: dict(color=l.color, pattern=l.pattern, width=l.width)
                for l in self.layers if l.color}

    @property
    def order(self):
        return [l.gds for l in self.layers]

    def __repr__(self):
        return "LayerSet(%s: %s)" % (self.key, ", ".join("%d %s" % (l.gds, l.name) for l in self.layers))


_OUTLINE = FabLayer(10, "WaferOutline", (), (), "#808080", "I1", 2)   # template only

# --- the MEA1K interconnect wafer ---------------------------------------------------------
# Rev2 = the fabricated wafer (MEA1K_wafer4_12x_8x_8x_breakoutboards_Rev2.gds). Its fab numbers were
# set by hand on the wafer, not by any generator; legacy/rev2_repro/config_rev2.py reproduces them and
# does not import this module. Rev2 had no layer names on the wafer; these are for reading only.
IC_REV2 = LayerSet("interconnect_rev2", [
    FabLayer(7, "Metal1", (IC.METAL1,), (IC_DUMMY_GDS[IC.METAL1],), "#e01818", "I0", 2),
    _OUTLINE,
    FabLayer(6, "Metal2", (IC.METAL2,), (IC_DUMMY_GDS[IC.METAL2],), "#ff9020", "I5", 1),
    FabLayer(3, "PI_Etch", (IC.POLYIMIDE_NEG, IC.ETCHING_PAD), (IC_DUMMY_GDS[IC.POLYIMIDE_NEG],),
             "#00b8c8", "I5", 1),
    FabLayer(5, "Etching_Via", (IC.ETCHING, IC.VIA), (IC_DUMMY_GDS[IC.ETCHING],), "#1060e0", "I0", 1),
], note="fabricated; pad etch = one square per pad on 3")

# Rev3: + Metal3 (pad squares again) on 8, the template's free-in-Rev2 mark slot; the pad etch becomes
# perimeter circles, still on 3.
IC_REV3 = LayerSet("interconnect_rev3", [
    IC_REV2[7], _OUTLINE, IC_REV2[6],
    FabLayer(8, "Metal3", (IC.METAL3,), (IC_DUMMY_GDS[IC.METAL3],), "#8020d0", "I9", 2),
    IC_REV2[3], IC_REV2[5],
], note="not fabricated")

# Rev4: the pad-perimeter stabilization vias leave layer 3 for their own mask on 1 (etched FIRST,
# aligned in the template's free mark slot |x| = 43.3 mm, inverted vernier); 3 keeps the PI outline and
# the connector-pad openings (etched after). Stage-01 draws them on IC.PI_STAB_VIA, the dummies on 2/0.
IC_REV4 = LayerSet("interconnect_rev4", [
    IC_REV2[7], _OUTLINE, IC_REV2[6], IC_REV3[8],
    FabLayer(3, "PI_Outline_Etch", (IC.POLYIMIDE_NEG, IC.ETCHING_PAD), (IC_DUMMY_GDS[IC.POLYIMIDE_NEG],),
             "#00b8c8", "I5", 1),
    IC_REV2[5],
    FabLayer(1, "PI_Stab_Via_Etch", (IC.PI_STAB_VIA,), (IC_DUMMY_GDS[IC.PI_STAB_VIA],), "#00c060", "I0", 1),
], note="current")

# --- the UFTE probe wafer -----------------------------------------------------------------
# Metal is the first mask (7/0 carries the alignment reference); every later mask is dark field.
# !!! PROVISIONAL -- NOT CONFIRMED FOR FABRICATION (ufte_probes/wafer_nesting/README.md) !!!
# The probe .lyp is still written by ufte_probes/../maskgen/wafer/export.py's own colour cycle
# (no style here yet: the shipped .lyp is part of the golden reference); refactor Phase 6 moves it here.
PROBE_64CH = LayerSet("probe_64ch", [
    FabLayer(7, "Metal", (PROBE.METAL,), (), None, None, None),
    FabLayer(6, "Etching", (PROBE.ETCHING,), (), None, None, None),
    FabLayer(3, "pad_etching", (PROBE.PAD_ETCHING,), (), None, None, None),
    FabLayer(8, "PEDOT_SIROF", (PROBE.PEDOT_SIROF,), (), None, None, None),
    FabLayer(1, "IONP", (PROBE.IONP,), (), None, None, None),
    FabLayer(10, "WaferOutline", (), (), None, None, None),
], note="provisional")

LAYERSETS = {s.key: s for s in (IC_REV2, IC_REV3, IC_REV4, PROBE_64CH)}

# --- the wafer template's alignment-mark slots (new_wafer_actually.GDS) --------------------
# Mark row slots at |x| (mm), and the vernier polarity the template ships in each. Dark-field masks
# need the INVERTED vernier (maskgen/wafer/marks.py copies the 5/0 stencil, registered on 7/0).
TEMPLATE_MARK_SLOTS = {6: 41.2, 3: 41.9, 8: 42.6, 1: 43.3, 5: 44.0}
TEMPLATE_MARK_POLARITY = {6: "positive", 8: "positive", 1: "positive", 3: "inverted", 5: "inverted"}
TEMPLATE_MARK_COMPANION = 7          # every slot has an identical 7/0 companion: the datum


# =============================================================================
# KLayout layer properties
# =============================================================================
def write_lyp(path, layers, layerset):
    """KLayout layer-properties file for the (layer, datatype) pairs in `layers`, named and styled by
    `layerset`. Entries are ordered by layerset.order (back -> front); layers not in it go after, in
    layer-number order (so in front), grey, "unnamed"."""
    names, style, order = layerset.names, layerset.style, layerset.order
    rank = {ln: i for i, ln in enumerate(order)}
    ordered = sorted(layers, key=lambda ld: (rank.get(ld[0], len(order)), ld))
    out = ['<?xml version="1.0" encoding="utf-8"?>', "<layer-properties>"]
    for ld in ordered:
        st = style.get(ld[0], _GREY)
        out += ["  <properties>",
                "    <frame-color>%s</frame-color>" % st["color"],
                "    <fill-color>%s</fill-color>" % st["color"],
                "    <frame-brightness>0</frame-brightness>",
                "    <fill-brightness>0</fill-brightness>",
                "    <dither-pattern>%s</dither-pattern>" % st["pattern"],
                "    <visible>true</visible>", "    <transparent>false</transparent>",
                "    <width>%d</width>" % st["width"], "    <marked>false</marked>",
                "    <name>%s (%d/%d)</name>" % (names.get(ld[0], "unnamed"), ld[0], ld[1]),
                "    <source>%d/%d@1</source>" % tuple(ld),
                "  </properties>"]
    out.append("</layer-properties>")
    with open(path, "w") as f:
        f.write("\n".join(out) + "\n")
