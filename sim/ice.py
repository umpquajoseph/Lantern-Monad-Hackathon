"""Hidden ice map and the rover's ice sensor (a stand-in neutron spectrometer).

Ice is under the surface. Nothing here is drawn as visible ground; the run
log carries the deposits as `truth`, which the viewer may show only as a
labelled "hidden truth" reveal (docs/handoffs.md, Run log).

Units: water-equivalent hydrogen in weight percent (wt%), the quantity a
neutron spectrometer reports.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict

import numpy as np

# PROSPECT brief p.9: "the ~1 wt% ice threshold we assumed". The team's own
# assumption, not yet sourced; a reading at or above it counts as a find.
FIND_THRESHOLD_WT = 1.0
# PROSPECT brief p.12: LCROSS measured "~5.6 % water by mass at Cabeus".
# Used as the peak of a rich deposit.
RICH_PEAK_WT = 5.6
# PROSPECT brief p.5: the Scout's neutron spectrometer finds hydrogen "down
# to ~1 m". The horizontal footprint below is assumed comparable.
SENSE_DEPTH_M = 1.0

FOOTPRINT_RADIUS_M = 1.0   # assumed: sensor averages over a disc this wide on the ground
SENSOR_NOISE_WT = 0.15     # assumed: 1-sigma reading noise
DEPOSIT_RADIUS_M = 6.0     # assumed: radius of a deposit's 1 wt% contour


@dataclass
class Deposit:
    x: float
    y: float
    radius: float   # metres: where concentration falls to FIND_THRESHOLD_WT
    peak: float     # wt% at the centre

    def concentration(self, x, y):
        """Gaussian in r, scaled so it equals the find threshold at `radius`."""
        r2 = ((np.asarray(x) - self.x) ** 2 + (np.asarray(y) - self.y) ** 2) / self.radius ** 2
        return self.peak * (FIND_THRESHOLD_WT / self.peak) ** r2


class IceField:
    """The hidden truth: a few deposits under the drive area."""

    def __init__(self, deposits):
        self.deposits = list(deposits)

    def concentration(self, x, y):
        c = np.zeros(np.shape(x), dtype=np.float64)
        for d in self.deposits:
            c = c + d.concentration(x, y)
        return c

    def to_log(self):
        return [{k: round(v, 3) for k, v in asdict(d).items()} for d in self.deposits]


class IceSensor:
    """Reads the field under the rover with footprint averaging and noise."""

    STANDIN = "ice sensor: footprint average of the hidden field plus Gaussian noise (sim/ice.py)"

    def __init__(self, field: IceField, rng: np.random.Generator):
        self.field = field
        self.rng = rng
        a = np.linspace(0, 2 * math.pi, 6, endpoint=False)
        r = FOOTPRINT_RADIUS_M / 2
        self._ox = np.concatenate([[0.0], r * np.cos(a)])
        self._oy = np.concatenate([[0.0], r * np.sin(a)])

    def read(self, x, y):
        c = self.field.concentration(x + self._ox, y + self._oy).mean()
        return max(0.0, float(c + self.rng.normal(0.0, SENSOR_NOISE_WT)))
