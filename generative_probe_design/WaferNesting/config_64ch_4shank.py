"""Config -- the 64-channel, 4-shank-design wafer.

Four geometries from electrode_bundle/design_sets.py (U1.6, U2.5, U4, U8), 64 channels
each, nested at the ratio below. See README.md for how to run this and how to make a
config for a different design set.

    export WAFERNEST_CONFIG=config_64ch_4shank
    python3 WaferNesting/00_check_setup.py
    python3 WaferNesting/01_extract_footprints.py
    python3 WaferNesting/nest_tiler.py --time 900
"""
import os, sys

# =============================================================================
# PATHS -- one target folder holds both halves of the pipeline
# =============================================================================
#     <TARGET_SET>/
#       shanks/    <- electrode_bundle's output; the DXFs nested here
#       wafer/     <- this stage's output; footprints, poses, preview, wafer GDS
#
# TARGET_SET must match electrode_bundle/config.py's constant of the same name. Keeping
# both halves under one folder is the point: a wafer can never be built from shanks that
# live somewhere else.
TARGET_SET = "64Ch_4Shankdesigns"

HERE = os.path.dirname(os.path.abspath(__file__))     # .../WaferNesting
ROOT = os.path.dirname(HERE)                          # the common folder above it

DESIGN_DIR = os.path.join(ROOT, TARGET_SET, "shanks")
OUT_DIR    = os.path.join(ROOT, TARGET_SET, "wafer")

# Wafer outline + alignment marks. Both ship inside WaferNesting/, so these never change.
WAFER_TEMPLATE = os.path.join(HERE, "new_wafer_actually.GDS")
MARKS_WKT      = os.path.join(HERE, "marks_union.wkt")


# =============================================================================
# WHAT TO NEST
# =============================================================================
# THE MIX nest_tiler.py aims for. Only the proportions matter -- it fills the wafer and
# reports how many fit. This is the number to edit when you want a different mix.
SHANK_RATIO = {"U4": 6, "U1.6": 4, "U2.5": 4, "U8": 2}

# THE RESULT of the last nest, pasted from nest_tiler.py's printed line. 03_export_wafer.py
# asserts len(poses) == sum of these, so it must match the nest you intend to export.
# Nothing else reads it -- in particular the nester does NOT take its mix from here, or the
# ratio would drift a little every time a result was pasted back.
SHANK_COUNTS = {"U4": 22, "U1.6": 15, "U2.5": 14, "U8": 8}

# Each geometry exists as FOUR magnetic-ID variants (the IONP barcode). The plain
# electrode_bundle_<kind>.dxf carries no IONP layer at all, so it must not be used here --
# nest the barcoded ones and spread the four evenly, which 03_export_wafer.py does by
# dealing them out across that kind's poses.
IONP_VARIANTS = {
    "U1.6": ["C00", "C01", "C02", "C03"],
    "U2.5": ["C04", "C05", "C06", "C07"],
    "U4":   ["C08", "C09", "C10", "C11"],
    "U8":   ["C12", "C13", "C14", "C15"],
}

RUN_TAG = TARGET_SET


def _split(total, n):
    """total shared over n variants, remainder going to the first few."""
    return [total // n + (1 if i < total % n else 0) for i in range(n)]


DESIGNS = [
    {"key": f"{kind}{v}", "dxf": f"electrode_bundle_{kind}{v}.dxf",
     "kind": kind, "count": n}
    for kind, count in SHANK_COUNTS.items()
    for v, n in zip(IONP_VARIANTS[kind], _split(count, len(IONP_VARIANTS[kind])))
]


# =============================================================================
# FOOTPRINT EXTRACTION (stage 01)
# =============================================================================
CLOSE_GAP         = 0.08   # mm; closes hairline splits between adjacent shapes
SIMPLIFY          = 0.03   # mm; silhouette simplification tolerance
ENVELOPE_PAD      = 0.01   # only used when a kind groups >1 design
ENVELOPE_SIMPLIFY = 0.01   # MUST stay <= ENVELOPE_PAD (README.md, gotchas)


# =============================================================================
# WAFER GEOMETRY AND CLEARANCES
# =============================================================================
WAFER_R      = 50.0     # mm, physical wafer radius
WAFER_FLAT_Y = -47.285  # mm, y of the primary flat

# Measurements off the fabricated 2026-07 wafer, not preferences -- see README.md.
EDGE_EXCL = 3.0
GAP       = 0.05        # nest_tiler.py defaults to 0 (devices may touch); --gap overrides
MARK_BUF  = 0.7


# =============================================================================
# FAB LAYER MAP (stages 03 and 04)
# =============================================================================
# DXF layer name (lower-cased) -> (GDS layer, datatype).
# ANY LAYER NOT LISTED IS SILENTLY DELETED ON EXPORT. Stage 03 prints what it mapped and
# what it dropped -- read that output.
#
# Each target layer is one the WAFER TEMPLATE already carries alignment marks on, so the
# device geometry lands ON TOP of its own alignment mark and the pair ships as one mask.
# The template's mark cluster holds five slots, each pairing one layer's mark with a 7/0
# companion (|x| measured from the wafer centre):
#
#   40.94-41.47  6/0  fine cross   <- Etching
#   41.62-42.17  3/0  solid box    <- pad_etching
#   42.34-42.87  8/0  fine cross   <- PEDOT_SIROF
#   43.04-43.57  1/0  fine cross   <- IONP
#   43.72-44.27  5/0  solid box    <- unused, see MARK_LAYERS_UNUSED
#
# `pad_etching` took the FIRST of the two spare slots. It is a solid box rather than a fine
# cross, which is the coarser of the two mark styles the template carries -- worth knowing,
# because this mask opens a 13.5 um circle inside a 17 um gold pad and has only ~1.75 um of
# alignment budget to the metal underneath. If that proves too tight, the other fine-cross
# layers are the ones to trade with, not the remaining box at 5/0.
#
# 7/0 has a companion mark in EVERY slot: it is the reference everything else aligns to,
# which is why metal -- the first mask -- goes there.
#
# !!! PROVISIONAL -- NOT CONFIRMED FOR FABRICATION, see README.md !!!
LAYER_MAP = {
    "metal":              (7, 0),   # first mask; carries the alignment reference
    "etching":            (6, 0),   # device release: outline, loop hole, solder pads
    "pad_etching":        (3, 0),   # the site openings, 13.5 um circles onto the gold
    "pedot_sirof":        (8, 0),   # coating, 15 um circles in the 13.5 um openings
    "ionp":               (1, 0),   # IONP wells -- CONFIRM these are etched features
}

# GDS layer names, taken from the DXF layer names so the mask set reads like its source.
LAYER_NAMES = {7: "Metal", 6: "Etching", 3: "pad_etching", 8: "PEDOT_SIROF", 1: "IONP",
               10: "WaferOutline"}

# Template mark layers no mask uses -- the one spare left after `pad_etching` took 3/0.
# Dropped on export so the mask set contains exactly the layers that get made. It donates
# its geometry first, though: see MARK_STENCIL below.
MARK_LAYERS_UNUSED = [(5, 0)]

# =============================================================================
# MASK POLARITY AND THE ALIGNMENT MARKS (stage 03)
# =============================================================================
# METAL IS THE FIRST MASK AND THE ONLY BRIGHT-FIELD ONE. Its plate is chrome where the
# drawn shapes are, and it is what puts the physical marks on the wafer; every later mask
# is aligned to those. Every later mask is DARK FIELD: chrome everywhere except the drawn
# shapes, which are the openings. That is already right for the device geometry -- what we
# draw is what gets opened -- but it is NOT automatically right for an alignment mark.
#
# The template ships each slot's vernier in one of two polarities:
#
#   POSITIVE   129 separate bars (610 x 610 um, 4.9% filled)  -- 6/0, 8/0, 1/0
#   INVERTED   ONE polygon with those 129 bars as HOLES
#              (649.2 x 618.6 um, 69.8% filled)               -- 3/0, 5/0
#
# On a dark-field plate the positive form comes out as 129 slits in a chrome field: the
# metal scale it has to be read against is underneath that chrome and cannot be seen. The
# inverted form comes out as a clear window with the scale in chrome, and the metal mark
# below shows through. So every DARK-FIELD mask must carry the INVERTED form, and stage 03
# puts it there: it copies the 5/0 stencil into each slot listed below.
#
# THE COPY IS REGISTERED ON THE METAL COMPANION, not on the mark's own bounding box. Every
# slot carries an identical 7/0 companion (232 polys, 34,586 um^2) at its centre, and that
# companion is the feature already on the wafer when the later mask is aligned -- so the
# translation is (target companion centre - stencil companion centre), applied inside one
# band at a time. Slots sit 700 um apart at |x| = 41.2, 41.9, 42.6, 43.3 and 44.0 mm.
# Stage 03 proves the transform every run on 3/0, whose inverted mark the template already
# holds: rebuilding it from the 5/0 stencil must reproduce it exactly.
MARK_STENCIL = (5, 0)                      # the inverted vernier every dark-field mask gets
MARK_COMPANION = (7, 0)                    # metal: the datum the copy is registered on
MARK_INVERT_LAYERS = [(6, 0), (8, 0), (1, 0)]   # dark-field masks still holding the positive
MARK_SELFTEST_LAYER = (3, 0)               # already inverted -- the transform is checked on it
# A vernier cluster is picked out of the mark band by size: nothing else in there is this
# big (the shared coarse marks are 395 and 230 um, metal's outer box 980 um).
MARK_VERNIER_W_UM = (550.0, 700.0)
MARK_BAND_Y_UM = 1000.0                    # marks sit within this much of y = 0

# The template carries a THIRD copy of the whole mark cluster near x = +96 mm, far outside
# the 100 mm wafer. Everything beyond this radius is deleted on export.
TEMPLATE_KEEP_RADIUS_MM = 50.0

# Which design variant lands on which pose. Seeded so a re-run reproduces the same wafer.
VARIANT_SEED = 12345

import config_common; config_common.derive(sys.modules[__name__])
