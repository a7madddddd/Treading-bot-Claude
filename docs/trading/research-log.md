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

## 2026-09-14 — Can multiple free sources be combined into a defensible D-0026 composite dataset?

- **Question:** After D-0027 found no single free GitHub-hosted dataset
  sufficient as a D-0026 ROOT, can several free sources be combined
  (identity, point-in-time universe, OHLCV, delisted securities, regime,
  corporate actions) into a composite that is?
- **Summary:** Investigated each required layer separately, empirically
  verifying every serious candidate (cloned repositories and inspected
  actual files, not README claims). Found a genuinely new, valuable
  point-in-time universe-membership source and an improved identity
  layer, but the OHLCV recency gap remains unresolved and one dedicated
  delisted-securities candidate was disqualified outright.
- **Key findings:**
  - `fja05680/sp500` (cloned, inspected in full): real daily S&P 500
    point-in-time constituent membership, 1996-01-02 to 2026-08-18, MIT,
    actively maintained. Verified against three known delistings (Family
    Dollar, H.J. Heinz, RadioShack) — all matched the historically
    correct period via embedded ticker-departure suffixes. Scoped to
    S&P 500 only.
  - `jadchaar/sec-cik-mapper` and `JerBouma/FinanceDatabase`: real,
    free, current-snapshot identity/reference data, but no source found
    anywhere provides ticker-history-over-time.
  - `EpicSaber/delisted-stocks-list` and
    `BlackFalconData-org/delisted-stocks-list` (both cloned): each
    contains only a README — both are fronts for the same paid/metered
    Apify actor, not free datasets. Disqualified.
  - `piekstra/market-data` (README + direct file probes): no data
    committed at all — a downloader tool requiring a paid/user-supplied
    API. `vijinho/sp500` (cloned): real but index-level only, frozen at
    2018-12-21. Both disqualified for Layer C.
  - Three Hugging Face-hosted OHLCV candidates surfaced by search
    (`elkassabgi/hfdatalibrary`, `mito0o852/OHLCV-1m`,
    `paperswithbacktest/Stocks-Daily-Price`) look potentially promising
    by description (2018-2026 coverage, delisted names) but
    `huggingface.co` is blocked from this environment with the same
    network-policy signature as Stooq/SEC/Yahoo/FRED — genuinely
    unverified, not disqualified on merits.
  - New finding for `eliangcs/pystock-data`: raw and split/dividend-
    adjusted prices are both present as separate, labeled columns,
    resolving part of the corporate-actions question for its 2009-2017
    window.
- **Evidence / sources:** Direct `git clone` + file inspection of six
  repositories; full detail, comparison tables, and the required
  point-in-time and survivorship-bias test tables in
  `docs/trading/github-native-data-sources.md` §6.
- **Risks / limitations:** The Hugging Face access gap means this
  verdict could change if the Controller verifies those candidates from
  an unblocked network — this is explicitly flagged as the most useful
  next step, not a closed question.
- **Confidence:** HIGH for every candidate actually tested; MEDIUM for
  the overall "no composite solution" verdict, specifically because of
  the unverified Hugging Face candidates.
- **Status:** INPUT-TO-DECISION
- **Recommendation:** Treat the point-in-time S&P 500 membership and
  improved identity data as real, usable additions to the eventual
  identity/universe design — but do not treat S&P 500 membership as a
  stand-in for D-0026's full dynamic universe. The OHLCV recency gap is
  still the blocking issue; verifying the Hugging Face candidates from a
  network this environment cannot reach is the highest-value next step.
- **What changed as a result:** `docs/trading/decisions.md` D-0028;
  `docs/trading/pre-apply-checklist.md` B16 updated.

## 2026-09-14 — Do any of the three Hugging Face OHLCV candidates actually close the D-0026 gap?

- **Question:** D-0028 flagged three Hugging Face-hosted OHLCV datasets
  as promising by description but unverifiable (`huggingface.co`
  blocked). Do any of them, empirically, close the 2018-2026 OHLCV gap?
- **Summary:** Re-confirmed `huggingface.co` and every HF subdomain
  tested (`hf.co`, `datasets-server`, `cdn-lfs`, `cdn-lfs-us-1`) are
  blocked with the same policy signature as Stooq/SEC/Yahoo/FRED,
  verified via both `curl` and `WebFetch` independently. One candidate,
  `elkassabgi/hfdatalibrary`, had a public GitHub mirror of its pipeline
  and metadata (not its price data) that this environment could reach —
  used to verify real metadata, license text, and ticker-list membership
  without registering for anything.
- **Key findings:**
  - `elkassabgi/hfdatalibrary`: real, active pipeline (1,391 tickers,
    daily-updated through 2026-09-11, per its own committed
    `metadata.json`), but actual bars require registration at a blocked
    domain, and its own documentation — confirmed by a direct
    ticker-list test (0 of 4 pre-2021 delisted names present:
    FDO/RSH/HNZ/BBI all absent; AAPL/MSFT/TSLA/DELL present) — discloses
    that pre-~2021 delistings are excluded. License is CC BY 4.0 plus a
    mandatory IEX-terms carve-out for 2022+ data. **Verdict: CONDITIONAL.**
  - `mito0o852/OHLCV-1m`: no GitHub mirror exists; entirely unverified.
    **Verdict: UNVERIFIED.**
  - `paperswithbacktest/Stocks-Daily-Price`: its own client library
    (`pwb-toolbox`, verified directly) requires either a paid API key or
    an authenticated HF token — no anonymous access exists. **Verdict:
    REJECT — paid data.**
- **Evidence / sources:** Direct proxy-status log entries; `WebFetch`
  `EGRESS_BLOCKED` results; direct clone and file inspection of
  `github.com/elkassabgi/hfdatalibrary` and
  `github.com/paperswithbacktest/pwb-toolbox`. Full detail in
  `docs/trading/github-native-data-sources.md` §7.
- **Risks / limitations:** `mito0o852/OHLCV-1m` remains a genuine open
  question — it could not be ruled in or out.
- **Confidence:** HIGH for Candidates 1 and 3 (direct evidence);
  Candidate 2 has no evidence either way.
- **Status:** INPUT-TO-DECISION
- **Recommendation:** Treat the free-GitHub/Hugging-Face-hosted search
  for D-0026 OHLCV as concluded per the Controller's own instruction not
  to search indefinitely. The 2018-2026 OHLCV gap remains open; next
  steps are Controller-side verification of `mito0o852/OHLCV-1m` from an
  unblocked network, or proceeding to design D-0026 calibration bounded
  by the documented limitations.
- **What changed as a result:** `docs/trading/github-native-data-sources.md`
  §7 added; D-0028 in `docs/trading/decisions.md` annotated with this
  finding (no new decision number — the overall verdict, "no free
  composite solution," is unchanged); `docs/trading/pre-apply-checklist.md`
  B16 updated.

## 2026-09-14 — Can the gathered components support defensible D-0026 calibration?

- **Question:** With data-sourcing stopped by explicit Controller
  instruction, can pystock-data + `fja05680/sp500` + `finance-vix` +
  Hugging Face Candidates A (`mito0o852/OHLCV-1m`) and B
  (`elkassabgi/hfdatalibrary`) actually be combined into a defensible
  D-0026 calibration dataset?
- **Summary:** Synthesis/gap-analysis only, no new dataset search
  performed. Ran the identity-join, point-in-time, delisted, modern-
  regime-breadth, daily-feasibility, provenance, corporate-action, and
  survivorship tests the Controller specified against evidence already
  on hand from D-0027/D-0028 and the Hugging Face verification pass.
- **Key findings:**
  - DELL is an unresolved ticker-identity risk: the symbol has referred
    to two unrelated companies (Dell Inc., private 2013; Dell
    Technologies, relisted 2018) and neither Hugging Face candidate's
    per-ticker inception date has been checked.
  - Re-examining Candidate B's own `metadata.json` (previously fetched
    but not fully analyzed) surfaced a quintile liquidity breakdown
    showing a **51.8% average gap rate** in its bottom quintile (278 of
    1,391 tickers) — real, disclosed evidence of uneven coverage.
  - **Zero actual price rows have ever been inspected from either
    Hugging Face candidate in this entire research thread** — only
    metadata, pipeline documentation, and a ticker list (Candidate B),
    and Controller-supplied summary facts (Candidate A).
  - Joining `fja05680/sp500`'s real point-in-time S&P 500 membership to
    either OHLCV candidate does not reduce survivorship bias — it
    produces historical membership paired with a current/limited OHLCV
    universe, silently dropping most pre-2021 delistings while
    appearing survivorship-corrected.
  - Delisted test (FDO/RSH/HNZ/BBI): 3 of 4 remain PARTIAL (identity/
    date known via fja05680/sp500, no verified price anywhere); BBI
    remains FAILED (never even confirmed as a former S&P 500 member).
- **Evidence / sources:** Re-analysis of previously-gathered evidence
  (D-0027, D-0028, and this thread's Hugging Face verification pass);
  full detail in `docs/trading/github-native-data-sources.md` §8.
- **Risks / limitations:** This is a synthesis pass, not new empirical
  verification — its conclusions are only as strong as the underlying
  D-0027/D-0028 evidence, which itself has the access-gap limitations
  already documented (Hugging Face domains blocked).
- **Confidence:** HIGH — this is analysis of already-verified facts
  plus explicit identification of what remains unverified, not a new
  empirical claim.
- **Status:** INPUT-TO-DECISION
- **Recommendation:** Accept verdict C (data not sufficient for D-0026
  calibration) rather than overstate a partial composite as adequate.
  The only calibration-grade resource with actual verified data remains
  pystock-data (2009-2017) + fja05680/sp500 (S&P 500 point-in-time
  membership) — unchanged from D-0028. Per the Controller's explicit
  instruction, no further dataset search was performed or is
  recommended; next steps are Controller-side row-level verification of
  the Hugging Face candidates from an unblocked network, or proceeding
  to design the D-0026 calibration strategy bounded by these documented
  limitations.
- **What changed as a result:** second addendum to D-0028 in
  `docs/trading/decisions.md` (no new decision number — verdict
  unchanged); `docs/trading/pre-apply-checklist.md` B16 updated.
