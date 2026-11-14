"""The ONE design this process is routing: its resolved settings, as a `Design` object.

    from lib import active
    DSG = active.select("12block", in_dxf, out_dxf)   # loads config_12block.py (+ lib/constants.py)
    from lib.active import *                          # the ROUTER's own code still reads PADR, ...
    from lib.teardrop import teardrop_ring, ...       # lib functions take the design explicitly:
    teardrop_len_for(DSG, row, col)                   #   def teardrop_len_for(dsg, ...): dsg.PADR

The lib modules no longer read the design from globals (refactor Phase 4): every lib function that
needs a setting takes `dsg` as its first argument, and their caches live on it (`dsg.cache`). They can
be imported before select(), and two designs cannot leak into each other through them. The router's
own top-level code (02_route.py) still uses the star import.

Values are the same objects the star import exposes, so `dsg.X` and the router's `X` agree. The
arithmetic in the lib functions is unchanged -- only where a name is looked up moved.
A process still routes exactly one design; select() refuses to switch.
"""
import importlib
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # the folder
ASSETS = os.path.join(HERE, "assets")
DESIGNS = os.path.join(HERE, "rev2_reference")     # the committed Rev2 reference designs -- inputs, never written
if os.path.dirname(HERE) not in sys.path:
    sys.path.insert(0, os.path.dirname(HERE))   # the repo root: the maskgen package

__all__ = []          # filled by select(); `from lib.active import *` exports exactly these
DESIGN = None
DSG = None            # the Design select() built


class Design(object):
    """The resolved settings of one design. Read-only attributes (dsg.PADR, dsg.GEN_ROUTING, ...);
    `key` = the design ("12block" | "8block"), `cache` = a dict for per-design memoised data."""

    def __init__(self, key, ns):
        self.__dict__.update(ns)
        self.__dict__["key"] = key
        self.__dict__["cache"] = {}

    def __setattr__(self, k, v):
        raise AttributeError("Design settings are read-only (%s.%s)" % (self.key, k))

    def __repr__(self):
        return "Design(%s, %d settings)" % (self.key, len(self.__dict__) - 2)


def in_designs(p):
    """Bare filename -> <out root>/interconnect/adhoc/ (a stage run by hand); an absolute path is used
    as given (build_designs.py always passes absolute paths)."""
    if os.path.isabs(p):
        return p
    from maskgen.core import paths
    return os.path.join(paths.interconnect_dir("adhoc"), p)


def select(design, in_dxf=None, out_dxf=None):
    """Load config_<design>.py (+ lib/constants.py + derived paths); return it as a Design (and expose
    the same values as this module's globals, for the routers' star import)."""
    global DESIGN, DSG
    if DESIGN is not None:
        if DESIGN != design:
            raise RuntimeError(f"design already selected as {DESIGN!r}; one design per process")
        return DSG
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    from lib import constants
    cfg = importlib.import_module("config_" + design)

    ns = {}
    for mod in (constants, cfg):
        ns.update({k: v for k, v in vars(mod).items()
                   if not k.startswith("__") and not callable(v)
                   and type(v).__name__ != "module"})

    # Paths. The names are the ones the routers have always used.
    in_dxf = in_dxf or cfg.DEFAULT_IN
    out_dxf = out_dxf or cfg.DEFAULT_OUT
    ns.update(
        _HERE=HERE, _ASSETS=ASSETS, _DESIGNS=DESIGNS,
        NEW_IC_DXF=in_designs(in_dxf),
        CONNECTOR_CSV=os.path.join(ASSETS, "connector_signal_pads_exact_mm.csv"),
        BOARD_OUTLINE_DXF=os.path.join(ASSETS, cfg.BOARD_OUTLINE),
        FINAL_DXF=in_designs(out_dxf),
    )
    globals().update(ns)
    __all__[:] = sorted(ns)
    DESIGN = design
    DSG = Design(design, ns)
    return DSG


def __getattr__(name):
    # Reached only for names not (yet) set -- i.e. before select().
    if DESIGN is None and not name.startswith("__"):
        raise RuntimeError(f"lib.active.{name}: no design selected. Call "
                           f"lib.active.select('12block' | '8block') before importing lib modules.")
    raise AttributeError(name)
