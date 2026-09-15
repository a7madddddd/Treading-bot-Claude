# D-0026 universe subsystem — Phase A scaffolding

**Status: Phase A (architecture/interfaces) only. D-0026 CALIBRATION = BLOCKED.**

This package contains interfaces, domain models, and test scaffolding for
the D-0026 dynamic universe-selection subsystem. It contains **no real
selection logic, no numeric parameters, and no production data source**.

- Design rationale: `docs/architecture/universe.md`,
  `docs/trading/universe-selection-analysis.md`.
- Data contract and calibration methodology (not yet satisfied):
  `docs/trading/historical-data-calibration-plan.md`.
- Free-data research and why calibration remains blocked:
  `docs/trading/github-native-data-sources.md`, `docs/trading/decisions.md`
  (D-0027, D-0028).

## What exists here

Interfaces and abstractions only:

- `identity.py` — `SecurityIdentity` / `TickerAlias`: a ticker is never a
  permanent security identifier. Ticker reuse (e.g. the DELL case — see
  `github-native-data-sources.md` §9.4) is represented explicitly, never
  silently merged.
- `models.py` — the conceptual domain objects the pipeline operates on.
- `snapshot.py` — `ApprovedUniverseSnapshot`: immutable, versioned,
  deterministically hashed from its own content.
- `provider.py` — `UniverseSourceProvider`: an interface only. **No
  concrete implementation is wired to any real data source.**
- `pipeline.py` — the nine-stage pipeline contract
  (Tradability → Data Quality → Execution Quality → Strategy-Mechanics
  Fit → Regime Adaptation → Ranking → Concentration → Top-N → Persist).
  Every stage's real decision logic is a `NotCalibratedStageEvaluator`
  stub that refuses to run — this is deliberate, not an oversight: it
  makes it structurally impossible to produce a real-looking universe
  selection before D-0026's calibration gate is legitimately open.
- `failure.py` — `CrashOutcome` / `SnapshotOutcome`: CRASH and EMPTY are
  distinct, enforced by the type system, not by convention.
- `repository.py`, `observability.py` — abstractions plus in-memory,
  test-only reference implementations. Neither touches a real database,
  file, or network.

## What does not exist here, on purpose

No numeric threshold of any kind (liquidity floor, spread cap, ATR band,
regime thresholds, ranking weights, sector cap, correlation threshold,
Top-N, warm-up/staleness periods). No production `UniverseSourceProvider`
implementation. No live scheduler wiring. No Strategy Engine — this
package has no dependency on one, and nothing here should ever be
imported by one except `snapshot.ApprovedUniverseSnapshot` itself, per
the architectural boundary in `docs/architecture/universe.md §1`.

## Running the tests

```
PYTHONPATH=src python -m unittest discover -s tests/d0026 -v
```

Standard library only — no dependencies to install.
