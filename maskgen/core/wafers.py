"""EVERY wafer, by name -- the level ABOVE revisions.

A WAFER is one mask set with its own devices and its own placement: MEA1K_wafer4_12x_8x_8x_breakoutboards,
MEA1K_W5_64Ch_4Shankdesigns, MEA1K_wafer6_12x_8x_breakoutboards. Wafers are numbered across both projects
(interconnects and electrodes share the count).
A REVISION of a wafer is the same layout with only some masks changed (wafer4 Rev2 -> Rev3 -> Rev4). A new
wafer -- new devices, new placement -- starts again at Rev1. Rev1's files carry the bare wafer name (the
fab convention: the first version has no suffix), later ones append _Rev<N>.

    key      the short name used on the command line: wafer4, wafer5, wafer6
    name     the wafer's file / folder name
    project  the repo folder that makes its devices
    revs     {N: {layout, layerset, design_set, note}}
               layout     None -- designs only (or built elsewhere, see note), or
                          {"kind": "fixed", "config": ...}    every piece at a recorded position
                          {"kind": "nested", "config": ..., "nester": "skyline" | "sa"}
                          config = a module in <project>/wafers/
               layerset   maskgen/core/layers.py LAYERSETS key of the wafer's masks
               design_set PROBE wafers only: the electrode_bundle design set its devices come from (this
                          is also what marks a wafer as a probe wafer -- never set it on an interconnect wafer)
               devices    interconnect wafers: which catalog.DESIGNS go on it (default: all of them)

WHAT CHANGES in an interconnect wafer's designs from revision to revision is mea1k_interconnects/catalog.py,
under the same wafer key and revision number.

Output layout (maskgen/core/paths.py):
    <root>/<name>/Rev<N>/devices/<device>/   one device each: its DXF/GDS, device.json, logs/
    <root>/<name>/Rev<N>/                    the wafer layout: <name>[_Rev<N>].gds/.oas/.lyp, map; nest/, review/

Pure standard library: KLayout's Python imports this (through paths.py).
"""

# wafer4's five designs (catalog.DESIGNS also holds later wafers' devices)
WAFER4_DEVICES = ("12block_56_15", "8block_51_35", "8block_71_15", "dummy_61_10", "dummy_35_36")

WAFERS = {
    "wafer4": dict(
        name="MEA1K_wafer4_12x_8x_8x_breakoutboards", project="mea1k_interconnects",
        note="MEA1K interconnects: 4x 12-block, 4x 8-block 51_35, 4x 8-block 71_15, 26 dummies, hand-placed",
        revs={
            2: dict(devices=WAFER4_DEVICES, layout=None, layerset="interconnect_rev2",
                    note="FABRICATED; the wafer itself is reproduced by legacy/rev2_repro"),
            3: dict(devices=WAFER4_DEVICES, layout=None, layerset="interconnect_rev3", note="designs only, not fabricated"),
            4: dict(devices=WAFER4_DEVICES, layout=dict(kind="fixed", config="config_rev4"), layerset="interconnect_rev4",
                    note="the Rev4 pad stack at the fabricated Rev2 placements"),
        }),
    "wafer5": dict(
        name="MEA1K_W5_64Ch_4Shankdesigns", project="ufte_probes",
        note="UFTE 64-channel probes, 4 geometries x 4 barcodes",
        revs={
            1: dict(layout=dict(kind="nested", config="config_64ch_4shank", nester="skyline"),
                    layerset="probe_64ch", design_set="64Ch_4Shankdesigns",
                    note="shipped; count-maximising nest"),
        }),
    "wafer6": dict(
        name="MEA1K_wafer6_12x_8x_breakoutboards", project="mea1k_interconnects",
        note="MEA1K interconnects, nested",
        revs={
            1: dict(layout=dict(kind="nested", config="config_wafer6", nester="skyline"),
                    layerset="interconnect_rev4", devices=("12block_56_15", "8x64_56_15", "dummy_61_10"),
                    note="in design: wafer4 Rev4's 12-block + changes"),
        }),
}


def get(key):
    if key not in WAFERS:
        raise SystemExit("unknown wafer %r; known: %s" % (key, ", ".join(WAFERS)))
    return WAFERS[key]


def rev(key, n):
    w = get(key)
    if n not in w["revs"]:
        raise SystemExit("%s has no Rev%s; it has: %s" % (key, n, ", ".join("Rev%d" % r for r in sorted(w["revs"]))))
    return w["revs"][n]


def latest(key, with_layout=False):
    """The highest revision of `key` (with a wafer layout, if asked)."""
    rs = [n for n, r in get(key)["revs"].items() if r["layout"] or not with_layout]
    if not rs:
        raise SystemExit("%s has no revision with a wafer layout yet" % key)
    return max(rs)


def stem(key, n):
    """The wafer layout's file stem: the bare name for Rev1, <name>_Rev<N> after."""
    name = get(key)["name"]
    return name if n == 1 else "%s_Rev%d" % (name, n)


def label(key, n):
    return "%s Rev%d" % (key, n)


def parse(token, default=None):
    """'wafer4/4', 'wafer4:4' or 'wafer4' (its latest revision); a bare '4' needs `default` (a wafer key)."""
    t = str(token).replace(":", "/")
    if "/" in t:
        k, n = t.split("/", 1)
        return k, int(n.lstrip("Rrev"))
    if t.isdigit():
        if not default:
            raise SystemExit("revision %s: which wafer? give --wafer or write it as wafer<N>/%s" % (t, t))
        return default, int(t)
    return t, latest(t)


def of_design_set(design_set):
    """(key, rev) of the wafer revision whose devices are the electrode design set `design_set`, or None."""
    for k, w in WAFERS.items():
        for n, r in w["revs"].items():
            if r.get("design_set") == design_set:
                return k, n
    return None
