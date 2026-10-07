"""Derived settings shared by the config modules.

Holds no knobs of its own. It computes only the values that follow mechanically from the
knobs in a config_<design>.py, so each config states only what is specific to it -- and so a
derived value can never be frozen out of step with the knobs it comes from.

Call it after the knobs, at the bottom of a config:

    import config_common
    config_common.derive(globals())

The expressions are copied verbatim from the original routers; do not "simplify" them --
reordering float arithmetic can change the last bit of a coordinate on the mask.
"""
from lib.constants import N_WIRES_PER_BLOCK


def derive(c):
    """Add the shared derived values to the config namespace `c` (a dict, e.g. globals())."""
    c["LAND_PAD_R"] = c["LAND_PAD_DIA"] / 2.0
    c["VIA_R"] = c["VIA_DIA"] / 2.0       # enclosure = LAND_PAD_R - VIA_R = 0.5 um
    c["IC_TW"] = c["WIRE_W"] / 1000.0     # band/lead width (mm)
    c["CONNECTOR_PITCH"] = c["CONNECTOR_TW"] + c["GAP"]       # connector lane pitch (mm)
    c["PITCH"] = c["CONNECTOR_PITCH"]
    c["BAND_PITCH_MM"] = c["FAN_TARGET_PITCH"] / 1000.0      # neck-top (hand-off row) pitch (mm)
    c["WIRES_PER_COLUMN"] = c["N_BLOCKS_PER_COLUMN"] * N_WIRES_PER_BLOCK
    return c
