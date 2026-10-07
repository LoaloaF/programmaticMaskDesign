"""Phase 1: open + rotate the stage-01 interconnect, emit and check the band-to-hand-off transition.
"""
import math
import numpy as np
import ezdxf
from ezdxf.math import Matrix44

from lib import active

active.require()
from lib.active import *  # noqa: F401,F403  -- this design's knobs (see lib/active.py)

from lib.fanmath import min_adjacent_fan_gap
from lib.geometry import create_polygon_circle, entity_xy_points, stroke_centerline_to_polygon


def load_and_rotate_interconnect(path=NEW_IC_DXF):
    """Open the new interconnect DXF and rotate ALL geometry 180 deg (point reflection
    about the modelspace bbox center) so the attachment band faces DOWN."""
    doc = ezdxf.readfile(path)
    doc.dxfversion = "AC1015"
    msp = doc.modelspace()

    xs, ys = [], []
    for e in msp:
        for x, y in entity_xy_points(e):
            xs.append(x); ys.append(y)
    if not xs:
        raise RuntimeError(f"no polyline geometry found in {path}")
    cx = 0.5 * (min(xs) + max(xs))
    cy = 0.5 * (min(ys) + max(ys))

    m = Matrix44.chain(
        Matrix44.translate(-cx, -cy, 0),
        Matrix44.z_rotate(math.pi),
        Matrix44.translate(cx, cy, 0),
    )
    rotated, skipped = 0, 0
    for e in msp:
        try:
            e.transform(m)
            rotated += 1
        except Exception as exc:  # noqa: BLE001
            skipped += 1
            print(f"  warn: could not transform {e.dxftype()} on {e.dxf.layer}: {exc}")
    print(f"rotated 180 deg about ({cx:.1f}, {cy:.1f}) um: {rotated} entities"
          + (f", {skipped} skipped" if skipped else ""))
    return doc, msp, (cx, cy)


def emit_transition(msp, info):
    for poly in info["m1_polys"]:
        ring = stroke_centerline_to_polygon(poly, [WIRE_W] * len(poly))
        if ring:
            msp.add_lwpolyline(ring, close=True, dxfattribs={"layer": WIRE_LAYER})
    for poly in info["m2_polys"]:
        ring = stroke_centerline_to_polygon(poly, [WIRE_W] * len(poly))
        if ring:
            msp.add_lwpolyline(ring, close=True, dxfattribs={"layer": LIFT_LAYER})
    for (x, y) in info["m1_pads"]:
        msp.add_lwpolyline(create_polygon_circle(x, y, LAND_PAD_R, CIRCLE_RES),
                           close=True, dxfattribs={"layer": WIRE_LAYER})
    for (x, y) in info["m2_pads"]:
        msp.add_lwpolyline(create_polygon_circle(x, y, LAND_PAD_R, CIRCLE_RES),
                           close=True, dxfattribs={"layer": LIFT_LAYER})
    for (x, y) in info["vias"]:
        msp.add_lwpolyline(create_polygon_circle(x, y, VIA_R, CIRCLE_RES),
                           close=True, dxfattribs={"layer": VIA_LAYER})


def check_transition(info, lanes, target_pitch, fan_target_gap=None):
    msgs, ok = [], True
    enc = LAND_PAD_R - VIA_R
    msgs.append(f"via enclosure = {enc:.2f} um")
    if enc < 0.5 - 1e-9:
        ok = False; msgs.append("  FAIL: via enclosure < 0.5 um")
    odd_x = sorted(c[0] for c in info["m1_pads"])
    if len(odd_x) >= 2:
        min_odd_gap = min(odd_x[i + 1] - odd_x[i] for i in range(len(odd_x) - 1))
        msgs.append(f"odd pad edge-edge gap = {min_odd_gap - LAND_PAD_DIA:.2f} um")
        if min_odd_gap - LAND_PAD_DIA < 1.0:
            ok = False; msgs.append("  FAIL: odd landing pads < 1 um apart")
    even_lanes = [lanes[i] for i in range(len(lanes)) if i % 2 == 0]
    pad_centers = np.array(odd_x) if odd_x else np.array([])
    if pad_centers.size:
        worst = min(float(np.min(np.abs(pad_centers - lx))) - LAND_PAD_R - WIRE_W / 2.0
                    for lx in even_lanes)
        msgs.append(f"even-wire -> odd-pad clearance = {worst:.2f} um")
        if worst < 0.5:
            ok = False; msgs.append("  FAIL: even Metal1 wire within 0.5 um of an odd pad")
    fan_polys = info.get("fan_polys")
    if fan_polys and fan_target_gap is not None:
        gmin = min_adjacent_fan_gap(fan_polys, WIRE_W)   # all wires are on Metal1 through the fan
        msgs.append(f"eased fan min wire gap = {gmin:.3f} um (target {fan_target_gap:.2f} um)")
        if gmin < fan_target_gap - FAN_CHECK_TOL_UM:
            ok = False
            msgs.append(f"  FAIL: eased fan gap {gmin:.2f} < target {fan_target_gap:.2f} um "
                        f"(raise FAN_LEN_SLACK / lower FAN_TILT_CAP_DEG)")
    msgs.append(f"target pitch = {target_pitch} um; via transition: " + ("OK" if ok else "FAIL"))
    return ok, msgs
