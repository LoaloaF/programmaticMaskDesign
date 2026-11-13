"""Interconnect generator library. Importing it puts the repo root on sys.path, so every module
here (and every config_*.py, which all import lib.constants) can import the shared `maskgen`
package -- e.g. the layer names in maskgen/core/layers.py."""
import os as _os
import sys as _sys

_root = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _root not in _sys.path:
    _sys.path.insert(0, _root)
