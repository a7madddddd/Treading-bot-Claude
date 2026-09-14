# D-0026 — Phase 2 Data-Acquisition Pilot

**Status: PROPOSED / NOT APPROVED. Still Phase 2. Phase 3 NOT approved.**
No live implementation, no scheduler change, no strategy-mechanics
change, no live universe selection, no orders, no production dependency,
no production runtime behavior. This document is itself the pilot's
report.

**Headline result, stated immediately, not buried: the pilot could not
execute.** This environment's network egress policy blocks every
approved data source (Stooq, SEC EDGAR, Nasdaq Trader, FRED, Yahoo). Zero
real records were retrieved. **No data in this document is fabricated or
simulated.** Every finding that would normally require live retrieval is
marked **INCONCLUSIVE**, with the exact evidence of the blocker included.
Sections that are pure design work (pilot selection rationale, canonical
identity model, quality gates, point-in-time analysis) are completed in
full, since they don't depend on live retrieval.

---

## 0. What was actually attempted, and the exact evidence of the blocker

Before writing any part of this document, I tested direct retrieval
against the real data-serving endpoints (not just terms/documentation
pages) for every source in the Controller-approved architecture, using
`curl` via Bash — a different code path than the `WebFetch` tool, to rule
out a tool-specific restriction rather than an environment-wide one:

| Endpoint tested | Purpose | Result |
|---|---|---|
| `https://stooq.com/q/d/l/?s=aapl.us&i=d` | Stooq CSV download (AAPL daily) | `curl: (56) CONNECT tunnel failed, response 403` |
| `https://stooq.pl/q/d/l/?s=aapl.us&i=d` | Stooq alt domain | Same |
| `https://data.sec.gov/submissions/CIK0000320193.json` | SEC EDGAR company submissions | Same |
| `https://www.sec.gov/files/company_tickers.json` | SEC EDGAR ticker/CIK map | Same |
| `https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt` | Nasdaq Trader symbol directory | Same |
| `https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS` | FRED VIX series | Same |
| `https://query1.finance.yahoo.com/v8/finance/chart/AAPL` | Yahoo chart endpoint | Same |
| `https://raw.githubusercontent.com/BlackFalconData-org/delisted-stocks-list/main/README.md` | Community delisted-list repo | **HTTP 200 — reachable** |

The egress proxy's own status log records the precise, consistent cause
for every failure:

> `"kind": "connect_rejected", "detail": "gateway answered 403 to CONNECT
> (policy denial or upstream failure)"`

and confirms the proxy's allowlist (`noProxy`) is a small fixed set —
`api.anthropic.com`, `registry.npmjs.org`, `pypi.org`, a handful of
package registries, and internal/local addresses — **not** a list that
includes any financial-data domain. GitHub was reachable because
`raw.githubusercontent.com` happens to fall inside whatever the gateway
permits for code-hosting traffic, not because financial-data domains are
selectively blocked while GitHub is allowed for a data reason.

**Conclusion: this is a systemic, environment-level network policy, not a
per-source or per-page restriction.** No amount of retrying different
URLs, paths, or domains for the same providers would change this — I
tested two independent domains for Stooq and two independent subdomains
for SEC EDGAR, all with the identical failure signature. I did not
attempt to circumvent this policy (e.g., via a proxy-bypass technique) —
that would be an inappropriate response to a network security control I
don't have authority to override, separate from the question of whether
it's inconvenient for this task.

**What would actually be needed to run this pilot for real:** a session
or environment whose network egress allowlist includes the specific
approved data-source domains (`stooq.com`, `sec.gov`/`data.sec.gov`,
`nasdaqtrader.com`, `stlouisfed.org`, and optionally
`finance.yahoo.com`) — either the Controller's own machine, a
differently-configured execution environment, or an explicit allowlist
change for a future session. This is a **decision item**, not something
resolved in this document (§14).

---

## 1. Pilot objective

Determine, empirically, whether Stooq (as the Controller-approved primary
candidate root source) can support the D-0026 calibration's data
requirements — specifically: historical depth, OHLCV completeness,
delisted-security price-history availability, and identity-mapping
consistency — **before** authorizing any full-market acquisition, per
the Controller's explicit staged-authorization requirement.

**This objective could not be measured in this session**, per §0. The
objective itself, and the methodology below, remain valid and unchanged
for whenever retrieval becomes possible.

---

## 2. Pilot instrument selection

Selected **before** knowing whether retrieval would succeed, so the
selection is not biased by which symbols happened to be reachable —
consistent with good pilot-design practice generally, and specifically
so this list remains valid and reusable once retrieval is authorized in
a capable environment.

| Case | Symbol(s) | Why chosen |
|---|---|---|
| A. Currently active large-cap equity | **AAPL** (Apple Inc.) | Maximum-liquidity, maximum-history reference case; if a source fails here, it fails everywhere |
| B. Currently active smaller equity | **one mid/small-cap name to be selected at execution time from a pre-agreed list, e.g. a Russell 2000 constituent** — left as a class rather than a single fixed symbol here, since the specific best test case depends on what the pilot environment's reachable reference lists (Nasdaq Trader, SEC EDGAR) actually return at execution time, not on something decidable in advance without live data | Tests whether Stooq's coverage depth/quality degrades for less-followed names — a real, documented risk pattern in free-data sources generally |
| C. ETF | **SPY** (SPDR S&P 500 ETF Trust) | Maximum-liquidity broad-index ETF; also the natural candidate for the `MarketRegimeData` drawdown series (§13 of the free-architecture document) |
| D. Known ticker/name change | **META** (formerly **FB**, Facebook Inc. → Meta Platforms Inc., name/ticker change in 2022) | A real, well-documented, unambiguous ticker-change case — tests whether Stooq's historical series is continuous across the change or fragments into two separate symbol histories |
| E. Known delisted security, historical data may exist | **LEH** (Lehman Brothers Holdings, delisted 2008) or **BBBY** (Bed Bath & Beyond, delisted 2023) | Both are extremely well-documented, high-profile delistings — if any free source retains delisted price history at all, these maximally-covered, high-profile cases are the most likely to succeed; a failure here is strong negative evidence, not an edge case being unfairly hard |
| F. Long historical record | **KO** (Coca-Cola Co.) or **GE** (General Electric) | Multi-decade continuous listing; tests historical depth ceiling |
| G. Recently listed security | **a 2025-IPO'd name, to be selected at execution time from SEC EDGAR's most-recent registration statements** — again left as a class, since "most recent" is inherently a moving target that shouldn't be hand-picked in advance of execution | Tests whether Stooq's coverage-start lag (an open question from the prior document) is real and how large |
| H. D-0026-relevant edge case | **a leveraged/inverse ETF, e.g. TQQQ** (for the structural-exclusion identification test — do we correctly flag it, not whether we'd trade it) **and** **a symbol with a known corporate-action event (a stock split), e.g. a recent split-adjusted name** | Tests the exclusion-list mechanism (§10 of the free-architecture document) and split-adjustment handling (§9 of the same) with concrete, checkable cases |

**Deliberately not TSLA-only, and TSLA does not appear in this list at
all** — consistent with the Controller's standing instruction that TSLA
is test-only for the *live routine* and must never become a calibration
baseline. Nothing above uses TSLA.

**Documented reasons, restated as a single sentence per case:** each
symbol was chosen because it is the **most favorable, most-documented,
least-ambiguous** representative of its category — so that a **failure**
on any of these cases is strong, conservative evidence of a real
limitation (not an artifact of picking an unusually hard example), while
a **success** is a reasonable, though not certain, signal that
less-favorable cases in the same category might also work.

---

## 3. Retrieval methodology (design — not executed)

For whenever this pilot can actually run:

1. For each symbol in §2, attempt a Stooq CSV pull
   (`stooq.com/q/d/l/?s={ticker}.us&i=d`) for daily bars across the
   symbol's full available history.
2. For SEC-registered entities among the pilot set, cross-reference via
   SEC EDGAR's `company_tickers.json` and per-CIK `submissions` endpoint
   to confirm CIK, former names, and (where applicable) delisting-adjacent
   filing dates.
3. For the community delisted-stocks GitHub dataset (confirmed reachable,
   §0), cross-reference the delisted pilot symbols (case E) against that
   list to confirm they appear, and note the delisting date/exchange it
   records.
4. For each retrieval, record every field specified in §4 below — success
   or failure, not just successes.
5. Do not retry indefinitely on failure — a small, fixed retry budget
   (e.g., 3 attempts with backoff) per symbol, to distinguish a genuine
   "source doesn't have this" from a transient network hiccup, without
   risking triggering Stooq's observed (undocumented) rate-limit behavior
   from the prior research pass.
6. Record wall-clock timing per request, to characterize whether Stooq's
   informal rate-limiting becomes noticeable at this small (roughly
   10-15 symbol) pilot scale.

**None of this was executed in this session** (§0).

---

## 4. DataCoverageLog

The schema this table would populate, per the Controller's exact field
list — **shown here as the intended structure, populated with real
results only where retrieval actually succeeded (GitHub, case E's
delisting-list cross-reference), and explicitly marked NOT RETRIEVED
elsewhere:**

| Field | Case A (AAPL) | Case D (META/FB) | Case E (LEH/BBBY) | ... |
|---|---|---|---|---|
| Source | Stooq | Stooq | Stooq | — |
| Identifier (CIK) | **NOT RETRIEVED** (SEC EDGAR blocked, §0) | **NOT RETRIEVED** | **NOT RETRIEVED** | — |
| Ticker | AAPL | META (formerly FB) | LEH / BBBY | — |
| First available date | **NOT RETRIEVED** (Stooq blocked, §0) | **NOT RETRIEVED** | **NOT RETRIEVED** | — |
| Last available date | **NOT RETRIEVED** | **NOT RETRIEVED** | **NOT RETRIEVED** | — |
| Number of rows | **NOT RETRIEVED** | **NOT RETRIEVED** | **NOT RETRIEVED** | — |
| Missing dates | **NOT RETRIEVED** | **NOT RETRIEVED** | **NOT RETRIEVED** | — |
| OHLCV completeness | **NOT RETRIEVED** | **NOT RETRIEVED** | **NOT RETRIEVED** | — |
| Volume availability | **NOT RETRIEVED** | **NOT RETRIEVED** | **NOT RETRIEVED** | — |
| Corporate-action implications | **NOT RETRIEVED** | **NOT RETRIEVED** (this is precisely the case that would test it) | **NOT RETRIEVED** | — |
| Symbol-change information | **NOT RETRIEVED** | **NOT RETRIEVED** (this is precisely the case that would test it) | **NOT RETRIEVED** | — |
| Delisting information | N/A (active) | N/A (active, renamed not delisted) | **Confirmed present on the community GitHub delisted-list** (reachable, §0) — but the list gives identity/delisting-date evidence, not price data | — |
| Existed before delisting? | N/A | N/A | **Yes** (both LEH and BBBY are unambiguous, well-documented real companies with long trading histories before their respective delistings — this is general public knowledge, not something requiring a data-source query to establish) | — |
| Price history available before delisting? | N/A | N/A | **NOT RETRIEVED — the central unresolved question, unanswered by this pilot** | — |
| Data anomalies | **NOT RETRIEVED** | **NOT RETRIEVED** | **NOT RETRIEVED** | — |
| Retrieval failures | **100% — every Stooq request failed at the network layer before reaching Stooq's server at all (§0)** | Same | Same | — |
| Rate-limit behavior | **Not observable** — failures occurred at the proxy, before any request reached Stooq, so Stooq's own rate-limiting behavior (previously observed in web research as an "exceeded daily hits limit" message) could not be triggered or characterized in this pilot | Same | Same | — |
| Source-specific limitations | Network-layer block, this environment only (§0) | Same | Same | — |

**Every row that could theoretically only be answered by SEC EDGAR or
Stooq retrieval is "NOT RETRIEVED," honestly, with the reason given once
in §0 rather than repeated as if it varied by symbol** — it didn't; the
block was uniform and total.

**The one genuinely new, real, non-fabricated data point this pilot
produced:** confirmation that the community delisted-stocks GitHub
dataset **is reachable and does list the case-E delisted symbols** —
this is real evidence, just not the price-history evidence the pilot
primarily needed.

---

## 5. Delisted-security results

Per the Controller's explicit six questions:

1. **Can we identify the security using SEC/CIK/reference information?**
   **Partially demonstrated** — the community GitHub delisted-list
   (SEC-EDGAR-sourced) is reachable and lists delisted names; direct SEC
   EDGAR API cross-reference (for CIK-level confirmation) was **not
   retrievable** in this session (§0). So: identity is achievable via one
   of the two intended channels, not both.
2. **Can we establish approximately when it was active?** For the
   specific pilot names (LEH, BBBY), this is **general public knowledge**
   independent of any data source — both are widely documented historical
   events. This does not generalize to less-famous delisted names without
   an actual data-source query, which was not possible here.
3. **Can Stooq provide historical OHLCV before delisting?** **Not
   determined — the pilot could not reach Stooq at all (§0).** This
   remains the single most important open question, exactly as it was
   before this pilot, because the pilot could not run.
4. **How much of that history is available?** **Not determined**, same
   reason.
5. **Can we associate the historical price series with our internal
   InstrumentId?** Not testable without step 3's data existing first;
   the **design** for how this association would work is specified in
   §6 below, independent of whether real data was available to test it
   against.
6. **Does this work consistently across multiple delisted examples?**
   **Not testable** — zero examples were successfully retrieved, so
   "consistency across multiple examples" cannot be assessed at all, let
   alone generalized from one case (which the Controller explicitly
   warned against doing anyway).

### Required summary — reported honestly as zero, not omitted

- **Delisted securities tested (price-history retrieval attempted):** 2
  (LEH, BBBY) — attempts made, **0 succeeded**, both due to the network
  block, not a Stooq-side rejection or absence finding.
- **Delisted securities with price history confirmed available:** 0
- **Delisted securities without price history:** 0 confirmed absent — the
  correct statement is 2 **untested**, not 2 confirmed-absent; this
  distinction matters and is preserved deliberately.
- **Coverage percentage:** **not computable** — 0 of 2 attempts reached
  the data source at all; a percentage computed from a 100%-blocked
  sample would be meaningless, not just imprecise, and is not reported as
  a number for that reason.

---

## 6. Identity-mapping results

**Design completed in full (this does not require live data); empirical
validation not possible this session (§0).**

Per the Controller's explicit instruction to separate identity concepts
rather than conflate CIK with "every tradable instrument," the canonical
model distinguishes:

- **Company identity** — the legal entity, anchored by **SEC CIK** where
  the entity is an SEC registrant. Persists across nearly all corporate
  changes (name changes, most restructurings). Does **not**, by itself,
  identify a specific tradable security — a company can have multiple
  securities (e.g., common stock and preferred stock, or multiple share
  classes) under one CIK.
- **Security/instrument identity** — a specific tradable instrument
  (e.g., one share class), modeled with its **own internally-generated
  surrogate key**, linked to (not equated with) its issuing company's
  CIK. This is the level at which our `Instrument` table (per the
  free-architecture document §14) should actually be keyed — **not**
  directly by CIK, correcting a simplification in the earlier document
  that risked conflating "company" and "security" as if always
  one-to-one, which the Controller's instruction correctly flags as an
  unsafe assumption.
- **Ticker history** — a time-versioned mapping from a security's
  surrogate key to the ticker symbol(s) it has traded under, with
  effective date ranges (the `InstrumentHistory` table, unchanged in
  concept from the free-architecture document, now explicitly scoped to
  the *security* level, not the *company* level).
- **Listing/exchange identity** — which exchange(s) a security has traded
  on over time, and under what listing status (also time-versioned) —
  called out as its **own** dimension per the Controller's instruction,
  distinct from ticker history (a security can change exchanges without
  changing ticker, or vice versa).
- **Trading history** — the actual `DailyBar` price/volume series,
  linked to the security-level surrogate key, **not** directly to the
  ticker string (so a ticker reused years later for an unrelated company
  — a known real-world edge case — cannot silently corrupt the trading
  history of the original security).

**This five-way separation is a refinement of, and supersedes, the
simpler CIK-anchored design in `free-root-data-source-recommendation.md
§8`, which the Controller correctly identified as risking an unsafe
"CIK alone represents every tradable instrument" assumption.** The
refined model is the one to carry forward into any future implementation.

**What remains unverified:** whether this design actually works cleanly
against Stooq's real ticker conventions (e.g., how Stooq represents the
META/FB transition, whether as one continuous series or two) — **this is
exactly case D's purpose, and it could not be tested this session (§0).**

---

## 7. Point-in-time universe findings

Per the Controller's explicit instruction: separate what we **know**,
what we can **infer**, and what we **cannot know**, without turning
inference into fact.

### WHAT WE KNOW

- SEC EDGAR (when reachable) provides an authoritative, official record
  of which entities have filed with the SEC and when (filing-date
  ranges) — this is a **fact-level** data point about **regulatory
  registration**, not about trading-day-level tradability.
- The community GitHub delisted-list (reachable, §0) provides a
  **fact-level** record of which symbols are known to have been
  delisted, sourced from SEC EDGAR.
- Nasdaq Trader's Symbol Directory (when reachable) provides a
  **fact-level, current-day-only** snapshot of what is listed today.

### WHAT WE CAN INFER (explicitly labeled as inference, not fact)

- A company's approximate window of **public listing** can be
  **inferred** from the span between its earliest and latest SEC filing
  dates — this is a **reasonable approximation**, not a guarantee, since
  a company can file with the SEC before or after being actively
  tradable on a specific exchange on a specific day (e.g., during initial
  registration processing, or during a trading halt).
- A company's approximate **delisting date** can similarly be
  **inferred** from filing-date patterns and the community GitHub list's
  recorded delisting month/year — again, an approximation, since the
  actual last-trading-day and the last-SEC-filing-date are related but
  not identical events.

### WHAT WE CANNOT KNOW (from the approved free architecture, stated plainly)

- The **exact set of securities tradable on any single specific
  historical date** — no free source in either research pass, nor this
  pilot, produced or confirmed a ready-made feed for this. Filing-date
  inference (above) is the closest available approximation, and it is
  **explicitly not the same thing** — it can be off by days, weeks, or in
  unusual cases longer, around the actual listing/delisting boundary.
- Whether a **currently-inferred-active** security was, on some specific
  historical date, actually **halted, suspended, or otherwise untradable**
  despite still being formally listed — this level of granularity was
  not found to be available from any free source researched.

**No inference from this section is elevated to "fact" anywhere else in
this document or in any future evidence package built on this pilot's
design** — this is a standing discipline, not a one-time note.

---

## 8. Data-quality findings

**Not assessable this session** — no data was retrieved to assess (§0).
The **methodology** for assessing data quality once retrieval is possible
is unchanged from `historical-data-calibration-plan.md`'s validation
design (gap detection, outlier flags, cross-source consistency checks)
and is not re-specified here since nothing about this pilot's blocker
changes that methodology.

---

## 9. Rate-limit/reliability findings

**Not assessable this session**, for the specific reason given in §4's
table: every request failed at the network-proxy layer, before reaching
Stooq's (or any source's) actual server — so Stooq's own behavior under
load (previously observed in background web research, not this pilot, as
an "exceeded daily hits limit" message) could not be triggered,
confirmed, or characterized empirically here.

---

## 10. Licensing status

**Unchanged from `free-data-verification-pass.md` — still UNCLEAR, still
unresolved, and this pilot could not advance it**, since resolving it
requires reaching Stooq's terms page directly, which is blocked by the
same network policy that blocked the data-retrieval attempts (§0).

**Per the Controller's explicit instruction for this pilot:**

- No data was downloaded, so no redistribution question arose in
  practice.
- The unresolved licensing status is recorded here, again, as required.
- No claim is made anywhere in this document that Stooq's licensing is
  confirmed.
- **The "STOP immediately if terms explicitly prohibit our use" condition
  did not trigger, because no terms were ever actually encountered** — a
  distinct state from "terms were checked and found acceptable." This
  document does not claim the latter.

---

## 11. PASS/FAIL gates (defined — not evaluated, since no data exists to evaluate them against)

Per the Controller's request, objective gates for the **future**
full-market authorization decision, to be evaluated once real pilot data
exists:

| Gate | PASS condition | FAIL condition |
|---|---|---|
| Historical depth | Pilot symbols (esp. case F, long-history names) show ≥ several years of continuous daily bars, matching the general order of magnitude found in background research (community precedent suggested multi-year to multi-decade coverage for major names) | Depth is materially shorter than background research suggested, or wildly inconsistent across pilot symbols with no explainable pattern |
| OHLCV completeness | No unexplained gaps in the pilot symbols' daily series beyond expected non-trading days (weekends/holidays) | Systematic gaps beyond calendar non-trading days, unexplained by any known corporate event |
| Delisted price coverage | **At least one of the two delisted pilot cases (E) returns usable pre-delisting price history** | **Neither delisted case returns any price history** — this is the single most important gate, directly testing §21's "biggest remaining data gap" from the prior document |
| Symbol identity mapping | Case D (META/FB) resolves to a single, continuous, correctly-linked security-level history under the refined identity model (§6) | Case D fragments into two unrelated series with no clean linkage, or the ticker-change is silently mishandled |
| Corporate-action handling | Case H's split-adjustment case shows a price series consistent with known split-adjusted conventions | Unadjusted or incorrectly-adjusted prices around the known split date |
| Retrieval reliability | Pilot-scale (≈10-15 symbol) retrieval completes without triggering blocking/throttling that would make full-market-scale retrieval impractical | Pilot-scale retrieval alone triggers blocking, suggesting full-scale retrieval is infeasible without a materially different pacing strategy |
| Bulk acquisition feasibility | The scripted per-symbol-loop pattern (per the free-architecture document §7, §15) completes the pilot set in a reasonable, extrapolatable time budget | Per-symbol overhead is so large that full-market extrapolation implies an impractical multi-week/month acquisition window |
| Reproducibility | Re-running the same pilot retrieval on a different day returns materially the same historical values for the non-recent portion of each series (data shouldn't silently change after the fact, except for legitimate late corrections) | Re-running produces materially different historical values with no explainable cause |
| Rate limits | Pilot-scale retrieval stays comfortably under whatever throttling threshold is empirically observed | Pilot-scale retrieval alone approaches or triggers the observed threshold |
| Local storage integrity | Pilot data, once written to the canonical local format, round-trips without corruption or loss (a straightforward file-integrity check) | Any data loss/corruption observed in the local round-trip |

**None of these gates have been evaluated. This is a defined checklist
for the next execution attempt, not a scored result.**

---

## 12. Whether Stooq is suitable for full acquisition

**Cannot be determined from this pilot.** The pilot did not fail because
of anything about Stooq's actual data quality, coverage, or terms — it
failed because this execution environment cannot reach Stooq (or any
other approved source) at the network level. This is a **materially
different finding from "Stooq failed the pilot"** and must not be
conflated with it. Per the Controller's own instruction ("if Stooq fails
a critical gate: STOP, do not silently substitute another provider,
report the failure and ask for a decision") — **that instruction doesn't
directly apply here either, because no gate was actually evaluated
against real Stooq behavior.** This is a distinct third case: **the test
itself could not run**, which the original instructions didn't
explicitly anticipate, and which is reported here as its own category
rather than forced into "PASS," "FAIL," or silently glossed over.

---

## 13. What remains unresolved

Everything that was unresolved before this pilot **remains exactly as
unresolved**, plus one new procedural finding:

- Stooq's licensing terms (§10, unchanged).
- Whether Stooq provides pre-delisting price history at all (§5, still
  entirely untested).
- Point-in-time universe membership at the specific-date level (§7,
  unchanged — this was always a "cannot know" item, not something the
  pilot was expected to resolve).
- **New:** this execution environment's network egress policy does not
  permit reaching any of the approved data sources, meaning **the pilot
  itself cannot be executed from within a session shaped like this one**,
  regardless of how it's designed or how many times it's attempted.

---

## 14. Recommendation for the next Phase-2 step

**Do not re-attempt this pilot in an identically-configured session —
the design is sound, but the environment cannot execute it.** The
concrete next step is an **infrastructure/access decision**, not a
data-source or methodology decision:

1. **Identify an execution environment whose network egress allowlist
   permits reaching `stooq.com`, `sec.gov`/`data.sec.gov`,
   `nasdaqtrader.com`, and `stlouisfed.org`** (Yahoo optional, per the
   Controller's downgrade of its requirement). This could be:
   - The Controller's own machine, running the retrieval manually or
     via a small script the Controller executes locally.
   - A differently-configured Claude Code environment/session with a
     broader or explicitly-allowlisted network policy, if such a
     configuration is available and the Controller authorizes requesting
     it.
   - Any other execution context the Controller controls that can reach
     these specific, already-approved, free, public data sources.
2. Once such an environment is available, **re-run exactly this pilot
   design** (§2-§3, unchanged) — the symbol selection and methodology
   don't need rework, only an environment capable of executing them.
3. Populate the real `DataCoverageLog` (§4's structure, now with real
   values), answer the six delisted-security questions (§5) with actual
   evidence, and evaluate the PASS/FAIL gates (§11) against real results.
4. **Only then** does the "full acquisition authorized?" and "numeric
   D-0026 calibration authorized?" question become answerable with real
   evidence — not before.

**This document does not request, recommend, or attempt any workaround
to this environment's network policy.** It reports the constraint
factually and asks the Controller to decide how to proceed, consistent
with treating this as a genuine, disclosed blocker rather than a problem
to route around unilaterally.

---

## Final decision block

```
STOOQ PILOT:
INCONCLUSIVE
  (the pilot could not execute — this environment's network egress
  policy blocks stooq.com entirely, confirmed via direct curl testing
  against the actual CSV-download endpoint, not just the terms page.
  This is NOT a finding about Stooq's data quality or suitability.)

DELISTED PRICE HISTORY:
INCONCLUSIVE
  (zero retrieval attempts reached Stooq; 0 of 2 delisted pilot cases
  produced a result either way; "not available" would be an
  overstatement of what was actually learned)

POINT-IN-TIME UNIVERSE:
PARTIAL
  (this finding does NOT depend on live retrieval -- it is answerable
  from the WHAT WE KNOW / CAN INFER / CANNOT KNOW analysis in §7, which
  draws on prior research plus general reasoning about the nature of
  SEC filing-date data vs. true point-in-time tradability. Identity-level
  and approximate-window information: available. Exact-date tradability:
  not available from any approved free source.)

CANONICAL IDENTITY:
INCONCLUSIVE
  (the refined five-way identity design in §6 is complete and, in this
  document's assessment, sound -- but "PASS" would overstate what can
  be claimed without empirical validation against real Stooq ticker
  conventions, e.g. the META/FB case, which could not be tested)

FULL ACQUISITION:
NOT AUTHORIZED

NUMERIC D-0026 CALIBRATION:
NOT AUTHORIZED
```

---

No production code, dependency, scheduler change, live routine change,
live universe selection, or order was created while producing this
document. No strategy mechanics were changed. TSLA was not used,
referenced, or implied as any calibration baseline or fallback anywhere
in this document or in the pilot design. D-0026 remains PROPOSED / NOT
APPROVED. Phase 3 remains NOT approved. No data was fabricated to fill
gaps this environment's network policy prevented from being measured
empirically.
