"""Rover health: battery, heaters, Earth contact, faults, and when to go home.

The mission asks this module three questions before every leg:
  can I afford it?   energy for the drive, the science stop, the drive home,
                     and the reserve ([PB] p. 6: "return with at least 25 %
                     battery in reserve");
  can I talk?        is Earth in view ([PB] p. 6: the region "can lose
                     contact with Earth for up to 11 days"), so a find is
                     queued until the link returns;
  is anything broken? a drill that stalls, retried once, then given up.

Sources:
  [PB] PROSPECT Team Brief, RASC-AL 2027 Theme 3, 13 Oct 2026
       (docs/sources/PROSPECT-Team-Brief-RASC-AL-2027-Theme-3-1.pdf)
Every other number is a named parameter marked ASSUMED.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# [PB] p. 6, Scout: "4 swappable Li-ion cartridges, ~7.9 kWh".
BATTERY_WH = 7900.0
# [PB] p. 6: a sortie returns "with at least 25 % battery in reserve".
RESERVE_FRAC = 0.25
# [PB] p. 3: "VIPER's wheels turn at 10 cm/s". Used as the driving speed.
DRIVE_SPEED_M_S = 0.10
# [PB] p. 6: a Scout sortie is "22 h".
SORTIE_LIMIT_S = 22 * 3600.0
# [PB] p. 3: the Scout must average "at least ~7.4 cm/s". Reported, not enforced.
TARGET_SPEED_MADE_GOOD_M_S = 0.074

# ASSUMED. Power draws, watts. Chosen so a [PB] p. 6 sortie (1.5 km descent,
# 4 km survey, 2 drill sites, return) fits inside 75% of the battery.
DRIVE_W = 180.0
HOTEL_W = 45.0          # avionics, cameras, lamp, radio standby
HEATER_W = 35.0         # keeping batteries and electronics warm in shadow
DRILL_W = 250.0
ANALYZE_W = 60.0
# ASSUMED. Routes home are this much longer than a straight line, for the
# energy estimate before the real route is planned.
DETOUR_FACTOR = 1.2

# ASSUMED. Earth contact, compressed for the demo: in view for COMMS_ON_S,
# out of view for COMMS_OFF_S, repeating. [PB] p. 6 gives up to 11 days
# out; the demo cannot wait that long, so the cycle is hours, not days.
COMMS_ON_S = 3 * 3600.0
COMMS_OFF_S = 2 * 3600.0
# ASSUMED. Each rover's cycle starts at its own offset, so they differ.
COMMS_OFFSET_S = {"A": 0.0, "B": 4000.0}

# ASSUMED. Chance a drill attempt stalls (rock, binding), and tries allowed.
DRILL_STALL_P = 0.15
DRILL_TRIES = 2


@dataclass
class Health:
    agency: str
    battery_wh: float = BATTERY_WH
    log: list = field(default_factory=list)

    # ---- energy ------------------------------------------------------
    @property
    def soc(self):
        return self.battery_wh / BATTERY_WH

    @property
    def reserve_wh(self):
        return RESERVE_FRAC * BATTERY_WH

    @staticmethod
    def drive_wh(metres):
        secs = metres / DRIVE_SPEED_M_S
        return (DRIVE_W + HOTEL_W + HEATER_W) * secs / 3600.0

    @staticmethod
    def idle_wh(secs):
        return (HOTEL_W + HEATER_W) * secs / 3600.0

    @staticmethod
    def science_wh(phases):
        wh = 0.0
        for p in phases:
            extra = DRILL_W if p["name"] in ("drill", "sample_lift") else \
                ANALYZE_W if p["name"] == "analyze" else 0.0
            wh += (HOTEL_W + HEATER_W + extra) * p["dur"] / 3600.0
        return wh

    def spend(self, wh):
        self.battery_wh = max(0.0, self.battery_wh - wh)

    def can_afford(self, leg_m, science_wh, home_m):
        """Energy check before a leg. Returns (ok, need_wh, have_wh)."""
        need = self.drive_wh(leg_m) + science_wh + self.drive_wh(home_m * DETOUR_FACTOR) \
            + self.reserve_wh
        return need <= self.battery_wh, need, self.battery_wh

    def must_return(self, home_m):
        """True when the drive home plus the reserve uses what is left."""
        return self.battery_wh <= self.drive_wh(home_m * DETOUR_FACTOR) + self.reserve_wh

    # ---- comms -------------------------------------------------------
    def in_contact(self, t):
        cyc = COMMS_ON_S + COMMS_OFF_S
        return ((t + COMMS_OFFSET_S.get(self.agency, 0.0)) % cyc) < COMMS_ON_S

    def next_contact(self, t):
        """Earliest time >= t with Earth in view."""
        if self.in_contact(t):
            return t
        cyc = COMMS_ON_S + COMMS_OFF_S
        phase = (t + COMMS_OFFSET_S.get(self.agency, 0.0)) % cyc
        return t + (cyc - phase)

    # ---- telemetry ---------------------------------------------------
    def snapshot(self, t, mode):
        return {"battery_wh": round(self.battery_wh, 1), "soc": round(self.soc, 4),
                "heater_w": HEATER_W, "comms": "earth" if self.in_contact(t) else "no contact",
                "mode": mode}
