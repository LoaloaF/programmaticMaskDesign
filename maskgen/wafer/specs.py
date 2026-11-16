"""How `python3 -m maskgen wafer <wafer> [--rev N]` builds a wafer layout: which stages, which designs first.

The wafers themselves -- names, revisions, which config lays each revision out -- are maskgen/core/wafers.py.
A layout is one of two kinds:
    fixed    every piece at a recorded position (a placements.json measured off a fabricated wafer):
             rebuild.py, checked against that wafer
    nested   positions found by a nester: extract_footprints -> nest (skyline | sa) -> export -> verify
             -> map. Nesting is slow (minutes) and its result is the input of export, so it only runs
             when there are no poses yet or when asked (--nest).
"""
import os

from maskgen.core import wafers
from maskgen.core.paths import REPO


def layout(key, rev):
    """Everything `maskgen wafer` needs for (key, rev): the layout dict + project, layerset, design kind."""
    w, r = wafers.get(key), wafers.rev(key, rev)
    if not r["layout"]:
        raise SystemExit("%s has no wafer layout yet (%s) -- add one in maskgen/core/wafers.py"
                         % (wafers.label(key, rev), r["note"]))
    return dict(r["layout"], project=w["project"], layerset=r["layerset"],
                designs="electrode" if r.get("design_set") else "interconnect",
                design_set=r.get("design_set"))


STAGE = {s: os.path.join(REPO, "maskgen", "wafer", s + ".py")
         for s in ("check_setup", "extract_footprints", "nest_skyline", "nest_sa", "export", "verify",
                   "wafer_map", "rebuild")}
KLAYOUT_STAGES = ("export", "verify", "rebuild")
