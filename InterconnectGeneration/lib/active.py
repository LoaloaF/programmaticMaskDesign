"""The ONE design this process is routing -- its knobs, as plain module globals.

Every lib module starts with `from lib.active import *`, so the functions in it read
`PADR`, `TEARDROP_CLEAR`, `TOTAL_WIRES`, ... exactly as they did when everything lived in one
2600-line script. That is deliberate: the function bodies were moved without a single edit,
which is what lets the output stay geometrically identical.

The price is an ORDER rule. A router script must call select() BEFORE it imports anything
else from lib/:

    from lib import active
    active.select("12block", in_dxf, out_dxf)     # loads config_12block.py
    from lib.active import *                       # now the knobs exist
    from lib.teardrop import teardrop_ring, ...    # and lib modules can see them

Importing a lib module first raises a clear error rather than silently using no config.
A process routes exactly one design; select() refuses to switch (module-level caches such as
the connector-pad cache in lib/connector.py would otherwise leak between designs).
"""
import importlib
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # the folder
ASSETS = os.path.join(HERE, "assets")
DESIGNS = os.path.join(HERE, "designs")

__all__ = []          # filled by select(); `from lib.active import *` exports exactly these
DESIGN = None


def in_designs(p):
    """Bare filename -> designs/ (created if missing); an absolute path is used as given."""
    if os.path.isabs(p):
        return p
    os.makedirs(DESIGNS, exist_ok=True)
    return os.path.join(DESIGNS, p)


def select(design, in_dxf=None, out_dxf=None):
    """Load config_<design>.py (+ lib/constants.py + derived paths) into this module."""
    global DESIGN
    if DESIGN is not None:
        if DESIGN != design:
            raise RuntimeError(f"design already selected as {DESIGN!r}; one design per process")
        return
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


def require():
    """Called at the top of every lib module: fail loudly if no design was selected yet."""
    if DESIGN is None:
        raise RuntimeError("no design selected. Call lib.active.select('12block' | '8block') "
                           "before importing lib modules.")


def __getattr__(name):
    # Reached only for names not (yet) set -- i.e. before select().
    if DESIGN is None and not name.startswith("__"):
        raise RuntimeError(f"lib.active.{name}: no design selected. Call "
                           f"lib.active.select('12block' | '8block') before importing lib modules.")
    raise AttributeError(name)
