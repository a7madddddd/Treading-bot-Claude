"""D-0026 universe-selection subsystem — Phase A scaffolding.

Status: D-0026 CALIBRATION = BLOCKED. See README.md in this directory
and docs/trading/historical-data-calibration-plan.md.

Public surface intentionally minimal: the only object a future Strategy
Engine should ever import from this package is ``ApprovedUniverseSnapshot``
(architecture boundary, docs/architecture/universe.md §1). Everything
else here is internal to the universe subsystem.
"""

from .snapshot import ApprovedUniverseSnapshot, SnapshotSymbolEntry

__all__ = ["ApprovedUniverseSnapshot", "SnapshotSymbolEntry"]
