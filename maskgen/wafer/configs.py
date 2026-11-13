"""Load the wafer config a stage runs with.

    $WAFERNEST_CONFIG   a config module name, looked up in each project's wafer folder
                        (mea1k_interconnects/wafer_nesting/, ufte_probes/wafer_nesting/), or a path to one

Pure standard library: KLayout's Python runs the stages that call this.
"""
import importlib
import importlib.util
import os
import sys

from maskgen.core.paths import REPO

PROJECT_DIRS = [os.path.join(REPO, "mea1k_interconnects", "wafer_nesting"),
                os.path.join(REPO, "ufte_probes", "wafer_nesting")]


def load(default=None):
    name = os.environ.get("WAFERNEST_CONFIG") or default
    if not name:
        raise SystemExit("set WAFERNEST_CONFIG to a wafer config (e.g. config_rev4, config_64ch_4shank)")
    if name.endswith(".py") or os.sep in name:
        path = os.path.abspath(name)
        sys.path.insert(0, os.path.dirname(path))
        spec = importlib.util.spec_from_file_location(os.path.splitext(os.path.basename(path))[0], path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod
    for d in PROJECT_DIRS:
        if d not in sys.path:
            sys.path.insert(0, d)
    return importlib.import_module(name)
