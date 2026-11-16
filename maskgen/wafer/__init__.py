"""Wafer layout, shared by both projects (refactor Phase 6).

    data/                    the wafer template (outline + alignment marks) and the marks keep-out
    configs.py               load(): the wafer config named by $WAFERNEST_CONFIG (each project keeps
                             its wafer configs: mea1k_interconnects/wafers/, ufte_probes/wafers/)
    config_common.py         derive(): the output paths + DESIGNS helpers every nested wafer config gets
    footprints.py            DXF -> footprint polygon (+ offset), for the nesters
    marks.py                 alignment marks: give dark-field masks the inverted vernier (pya)
    exact_io.py              exact (integer-dbu) reading/writing of placements, sources, templates (pya)

Stages (scripts; each reads its config through configs.load()):
    check_setup.py           python3   inputs present, counts sane
    extract_footprints.py    python3   design DXFs -> footprints, offsets, kind envelopes
    nest_skyline.py          python3   placement backend: count-maximising skyline tiler -> poses.json
    nest_sa.py               python3   placement backend: seeded simulated annealing, fixed counts -> poses.json
    export.py                KLayout   poses.json + designs + template -> wafer GDS/OAS/LYP + placement.json
    verify.py                KLayout   the exported wafer against its sources
    wafer_map.py             python3   labelled map of what sits where
    rebuild.py               KLayout   FIXED placements (a placements.json measured off a fabricated
                                       wafer) + new sources -> wafer, checked against the fabricated one

`python3 -m maskgen wafer <run tag>` runs the right stages for a wafer.
Pure standard library at import (KLayout's Python imports this package).
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
TEMPLATE_GDS = os.path.join(DATA, "new_wafer_actually.GDS")   # MEA1K wafer template: outline + marks
MARKS_WKT = os.path.join(DATA, "marks_union.wkt")             # keep-out around the alignment marks
