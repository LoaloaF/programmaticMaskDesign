"""The wafers `python3 -m maskgen wafer <tag>` knows: which config, which designs it needs, which stages.

A wafer is one of two kinds:
    fixed    every piece at a recorded position (a placements.json measured off a fabricated wafer):
             rebuild.py, checked against that wafer
    nested   positions found by a nester: extract_footprints -> nest (skyline | sa) -> export -> verify
             -> map. Nesting is slow (minutes) and its result is the input of export, so it only runs
             when there are no poses yet or when asked (--nest).
"""
import os

from maskgen.core.paths import REPO

WAFERS = {
    "wafer4_rev4": dict(
        kind="fixed", config="config_rev4", layerset="interconnect_rev4",
        project="mea1k_interconnects",
        needs=("interconnect", 4),                 # build_designs.py --rev 4 if its outputs are missing
        note="MEA1K interconnect wafer, Rev4 pad stack at the fabricated Rev2 placements"),
    "64Ch_4Shankdesigns": dict(
        kind="nested", config="config_64ch_4shank", nester="skyline", layerset="probe_64ch",
        project="ufte_probes",
        needs=("electrode", "64Ch_4Shankdesigns"),  # electrode_bundle.batch if its outputs are missing
        note="UFTE 64-channel probes, 4 geometries x 4 barcodes, count-maximising nest"),
}

STAGE = {s: os.path.join(REPO, "maskgen", "wafer", s + ".py")
         for s in ("check_setup", "extract_footprints", "nest_skyline", "nest_sa", "export", "verify",
                   "wafer_map", "rebuild")}
KLAYOUT_STAGES = ("export", "verify", "rebuild")
