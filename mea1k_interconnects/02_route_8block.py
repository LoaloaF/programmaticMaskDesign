"""Stage 02 for the 8block.

Kept as an entry point only: the router is 02_route.py --design 8block (refactor Phase 5 merged the
12-block and 8-block routers; ROUTE_STRATEGIES there says what differs).

    python3 02_route_8block.py [--in X.dxf] [--out Y.dxf]
"""
import os
import runpy
import sys

if "--design" not in sys.argv:
    sys.argv[1:1] = ["--design", "8block"]
runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "02_route.py"), run_name="__main__")
