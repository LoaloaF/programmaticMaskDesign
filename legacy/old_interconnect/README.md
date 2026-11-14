# legacy/old_interconnect — the old-interconnect routers (frozen)

Two standalone scripts. They append connector routing to the OLD MEA1K interconnect
(`mea1k_interconnects/assets/mea1k_interconnect_only.dxf`). This is the design that came before
the circular-pad generator in `mea1k_interconnects/`. Nothing else reads their output, and no
wafer uses it.

```bash
python3 legacy/old_interconnect/02_route_old_12block.py           # minutes; ~99 MB final + debug
python3 legacy/old_interconnect/02_route_old_8block_doubled.py
```

They have no command-line options. Every setting is a constant at the top of each script.

**What changed in the move here (refactor Phase 8):** paths only. Inputs still come from
`mea1k_interconnects/assets/`. Outputs no longer go into the repo's `designs/`; they go to
`<output root>/interconnect/old/`. The output root is `$MASKGEN_OUT`, otherwise the path in
`<repo>/.maskgen_out`. These scripts do not import `maskgen`.
