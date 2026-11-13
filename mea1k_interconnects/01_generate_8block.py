"""Stage 01 for the 8-block.

Kept as an entry point only: the generator is 01_generate.py --config 8block (refactor Phase 5 merged
the 12-block and 8-block generators; STRATEGIES there says what differs).

    python3 01_generate_8block.py [--out X.dxf]
"""
import os
import runpy
import sys

if "--config" not in sys.argv:
    sys.argv[1:1] = ["--config", "8block"]
runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "01_generate.py"), run_name="__main__")
