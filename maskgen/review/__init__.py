"""Review sheets: designs side by side, one named feature per row.

    python3 -m maskgen review --wafer wafer4 --rev 3 4         # every interconnect design, Rev3 vs Rev4
    python3 -m maskgen review --rev wafer4/4 wafer6/1 --design 12block_56_15 --xor   # across wafers
    python3 -m maskgen review --wafer wafer4 --rev 4 --design 12block_56_15 8block_51_35 --roi "top pad" "connector pad"
    python3 -m maskgen review --probes U1.6 U8                 # electrode geometries side by side
    python3 -m maskgen review --wafers wafer4/4 rev2_fabricated wafer5   # alignment-mark slots
    python3 -m maskgen review --rois                           # list the named features

A sheet is a grid:
- ROWS are named features (rois.py), found in each design's geometry, never at hard-coded coordinates.
- COLUMNS are revisions or designs (sources.py loads each one from the output root).
- Every cell in a row shows a window of the same physical size, centred on that column's feature, so a
  row is at one scale and the columns compare directly.
- Colours are the fab-layer styles of maskgen/core/layers.py.
- `--xor` outlines, in every column after the first, where its geometry differs from the first
  column's in the same window.
- Each cell is titled "<column> -- <feature>", so a feature can be named in discussion.

Output, under <root>/<wafer name>/Rev<N>/review/<name>/ -- the wafer revision of the LAST column (sheet.py):
- sheet.png: the whole grid;
- sheet.pdf: one page per feature, larger;
- index.md: the rows, the columns, their source files and the per-cell measurements;
- manifest.json.

Generalises the former mea1k_interconnects/review/compare_rev3_rev4.py and check_pads.py (removed in
refactor Phase 7). The whole-wafer overlay is legacy/rev2_repro/01e_overlay_wafers.py.
"""
