# Research Log

Append summaries of research that materially changes our understanding.

Each entry:

- Date
- Question
- Summary
- Key findings
- Evidence / sources (URLs, papers, docs)
- Risks / limitations
- Confidence: LOW / MEDIUM / HIGH
- Status: EXPERIMENTAL / INFORMATIONAL / INPUT-TO-DECISION
- Recommendation
- What changed as a result (link to `decisions.md` or `experiments.md`)

Do not treat internet opinions as facts. Distinguish clearly between
FACT / ASSUMPTION / HYPOTHESIS / RECOMMENDATION / EXPERIMENTAL IDEA.

---

## 2026-09-14 — Is there a free, GitHub-hosted US equity/ETF OHLCV dataset covering 2018-2026?

- **Question:** After rejecting `eliangcs/pystock-data` (2009-2017) as a
  sole D-0026 calibration root for lacking modern-regime coverage, is
  there any genuinely free, legally usable, GitHub-hosted historical US
  equity/ETF OHLCV dataset that is materially better on recency?
- **Summary:** Searched and empirically tested four new candidates
  (`irachex/open-stock-data`, `hanurd25/stock-data-collector`,
  `blumenty/stock-data-automation`, plus two rejected on fit without
  cloning). Verified actual files/refs, not just descriptions, per the
  Controller's requirement.
- **Key findings:**
  - `irachex/open-stock-data` documents a Releases-based bars pipeline
    (MIT license) but `git ls-remote --tags` shows **zero tags** — no
    Release has ever been published. Disqualified on data-existence
    grounds.
  - `hanurd25/stock-data-collector` has real, verified 1-minute bars
    (`AAPL.csv`, 20.6 MB) but only ~10 weeks deep (2026-07-02 to
    present), tiny ad hoc ticker list. Disqualified for lack of depth.
  - `blumenty/stock-data-automation` explicitly retains only a rolling
    50-day window and depends on the paid Polygon.io API. Disqualified
    as non-historical and paid-provider-dependent.
  - No candidate found in this pass or any prior pass in this thread
    combines real historical depth with 2018-2026 recency, free of
    charge, within this environment's reachable surface.
- **Evidence / sources:** Direct `curl`/`git ls-remote` tests against
  each repository; full detail and comparison table in
  `docs/trading/github-native-data-sources.md` §5.
- **Risks / limitations:** Search was bounded to this environment's
  reachable hosts (`raw.githubusercontent.com`, anonymous git clone via
  `add_repo`); a dataset reachable only from an unblocked network was
  not and cannot be tested from here.
- **Confidence:** HIGH for the specific candidates tested (empirically
  verified, not inferred); MEDIUM for "no such dataset exists anywhere
  on GitHub" (a negative claim over an unbounded search space — only
  states that none was found among the candidates search surfaced).
- **Status:** INPUT-TO-DECISION
- **Recommendation:** Treat `eliangcs/pystock-data` as a disclosed
  pre-2018 historical control only. The 2018-2026 recency gap remains
  open; closing it requires either direct access to a blocked
  financial-data domain from a non-restricted network, a future explicit
  Controller decision on a paid provider, or a GitHub-native source not
  yet found. No path is recommended by this research — only the gap is
  reported.
- **What changed as a result:** `docs/trading/decisions.md` D-0027;
  `docs/trading/pre-apply-checklist.md` B16 updated.
