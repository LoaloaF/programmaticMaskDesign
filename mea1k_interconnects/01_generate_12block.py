"""Stage 01 for the 12-block (and, with --config dummy, the dummy pad grid).

Kept as an entry point only: the generator is 01_generate.py (refactor Phase 5 merged the 12-block
and 8-block generators; STRATEGIES there says what differs).

    python3 01_generate_12block.py [--config 12block|dummy] [--out X.dxf]
"""
import os
import runpy

runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "01_generate.py"), run_name="__main__")
