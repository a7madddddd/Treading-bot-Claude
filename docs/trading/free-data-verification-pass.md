# D-0026 — Final Verification Pass Before Phase 2 Data Acquisition

**Status: PROPOSED / NOT APPROVED. Phase 2 remains research/design only.**
No data downloaded, no code written, no dependency added, no VM created,
nothing purchased, no live system touched, no strategy mechanics changed.
This document is the final gate-check the Controller requested before
authorizing actual data acquisition — it does **not** itself authorize
acquisition; that remains a separate, explicit future decision.

Builds on, and does not replace, `free-root-data-source-recommendation.md`
(the Controller-approved directional architecture) and
`root-data-source-recommendation.md` (REJECTED/superseded, Norgate).

---

## 0. A constraint I must disclose before anything else

I attempted to verify Stooq's and Yahoo's terms of use **directly from
primary sources**, per the Controller's explicit instruction not to rely
on third-party summaries for the final conclusion. I made genuine attempts
via two independent tools:

- `WebFetch` on `stooq.com/regulamin.aspx`, `stooq.com/` (root),
  `stooq.pl/regulamin.aspx`, `legal.yahoo.com/us/en/yahoo/terms/otos/`,
  `guce.yahoo.com/terms`, and an `web.archive.org` mirror attempt.
- Direct `curl` (via Bash, bypassing the WebFetch tool entirely) against
  `stooq.com` and `legal.yahoo.com`/`policies.yahoo.com`.

**All attempts failed with the same root cause**: this session's network
egress proxy returns `EGRESS_BLOCKED` / HTTP 403 for these specific
domains. This is a **technical constraint of my current tool access in
this environment**, not a refusal to look, not a shortcut, and not
something I can route around by trying yet more domain variants — I
tested six distinct URLs across two independent fetch mechanisms and got
the same block every time.

**What this means for the rest of this document:** wherever I cannot
reach a primary source, I say so explicitly and classify the item
**UNCLEAR — VERIFY DIRECTLY (primary source unreachable in this
environment; requires a session/device with unrestricted web access, or
manual verification by the Controller)** rather than inferring a
conclusion from secondary sources and presenting it as verified. Where I
found genuine primary-source-adjacent evidence via search (e.g., an
actual quoted clause from Yahoo's ToS that a search index had crawled and
indexed), I present it as exactly that — a **search-surfaced quotation**,
not a self-performed fetch — with the distinction stated plainly.

---

## 1. Stooq terms — verification attempt

**Direct primary-source access: blocked, as described in §0.** I could
not reach `stooq.com/regulamin.aspx` or any mirror. Web search for the
actual clause text also came back empty — every search attempt returned
only third-party tool documentation (scrapers, data-download guides)
describing *how* to pull Stooq data, never *quoting or summarizing
Stooq's own terms language*. This is itself informative: it suggests
Stooq's terms are not widely discussed or indexed in the same way
Yahoo's are, likely because Stooq is a smaller, community-relied-upon
service rather than a major platform with a well-trafficked legal
department page.

**Classification of every requested item — all UNCLEAR, honestly, with no exceptions:**

| Item | Classification | Basis |
|---|---|---|
| Automated retrieval allowed? | **UNCLEAR — VERIFY DIRECTLY** | No primary source reached; no search-indexed clause found |
| Bulk historical data retrieval allowed? | **UNCLEAR — VERIFY DIRECTLY** | Same |
| Local storage allowed? | **UNCLEAR — VERIFY DIRECTLY** | Same |
| Repeated internal research/backtesting allowed? | **UNCLEAR — VERIFY DIRECTLY** | Same |
| Commercial use prohibited? | **UNCLEAR — VERIFY DIRECTLY** | Same |
| Internal use by a private trading/research system allowed? | **UNCLEAR — VERIFY DIRECTLY** | Same |
| Can data be transformed into derived metrics? | **UNCLEAR — VERIFY DIRECTLY** | Same |
| Can derived metrics remain locally after retrieval? | **UNCLEAR — VERIFY DIRECTLY** | Same |
| Redistribution prohibited? | **UNCLEAR — VERIFY DIRECTLY** | Same |
| Rate limits or technical restrictions? | **PARTIALLY CONFIRMED (behavioral, not legal)** | A search result independently found in the prior research pass described Stooq returning an "Exceeded the daily hits limit" message under heavy load — this is **observed technical behavior**, not a documented published limit, but it is at least a real, corroborated data point (found again independently in this pass's search, not merely carried over). Treat as: a request-throttling mechanism exists in practice; its exact threshold is undocumented. |
| API/bulk download mechanism suitable for bulk acquisition? | **CONFIRMED (technical, not legal)** | Carried forward from the prior document's research: Stooq offers a CSV download interface reachable via HTTP requests per-symbol; there is no official documented bulk/batch API. This is a technical-capability finding, independent of the legal-terms question, and remains accurate regardless of the ToS block. |

**Bottom line on Stooq: every legal/licensing question remains genuinely
open.** I am not willing to infer "probably fine, it's widely used by the
quant community" as a substitute for actual verification — that would be
exactly the kind of assumption the Controller told me not to make. This
must be resolved by an actual human visit to Stooq's terms page (or a
session with unblocked web access) **before** any bulk acquisition begins,
not inferred from community practice.

---

## 2. Yahoo terms — verification attempt

**Direct primary-source access: also blocked (§0).** However, this pass's
research surfaced **two materially important, more concrete pieces of
evidence** than the prior document had — one a search-indexed quotation
of actual ToS language, one a significant, previously-undiscovered
finding about a 2025 policy change.

### 2a. Search-surfaced quotation of Yahoo's actual terms language

A search result quoted the following from Yahoo's Terms of Service
(sourced from `guce.yahoo.com/terms` / `legal.yahoo.com`, which I could
not fetch directly myself, but which a search index had crawled and
quoted):

> "must not reproduce, modify, rent, lease, sell, trade, distribute,
> transmit, broadcast, publicly perform, create derivative works based
> on, or exploit for any commercial purposes, any portion or use of, or
> access to, the Services (including content, advertisements, APIs, and
> software)" without explicit written permission.

And separately:

> Yahoo grants users "a personal, royalty-free, non-transferable,
> non-assignable, revocable, and non-exclusive license to use the
> software and APIs."

**How to weigh this evidence honestly:** this is **stronger** than a
third-party paraphrase (it is an actual quoted clause, apparently
verbatim), but it is **not** a fetch I performed myself, so I cannot
personally confirm the quote is currently accurate, unmodified, or
complete context. I am presenting it as "the best available evidence
found," not as "directly verified by me," per the distinction the
Controller asked me to maintain.

### 2b. New, more significant finding: a 2025 policy change gating historical data behind a paid plan

Independent GitHub issue tracking on the `yfinance` library (a widely-used
open-source wrapper, not Yahoo's own documentation, but a direct,
first-hand technical account from the library's own maintainers/users)
surfaced a **March 2025 report** stating that **"current price data
remains free, but historical data retrieval now requires a paid plan"** —
attributed to a Yahoo Finance policy change.

**This is a significant, previously-undiscovered finding that materially
changes the risk picture from the prior document.** If accurate and
current, it means:

- Yahoo/yfinance **may no longer reliably serve free historical OHLCV
  data at all** — which is precisely the role this architecture assigned
  it (cross-validation and gap-filling for Stooq, §11 of the prior
  document).
- This would **not** be a licensing/legal problem (the Controller's
  original question) but a **capability** problem — the free tier this
  document's architecture depends on may have been withdrawn or
  restricted since the earlier research pass.

### 2c. Additional reliability finding: widespread, unrelated-to-delisting errors in 2025

The same GitHub issue search surfaced **multiple, recent (2025) reports**
of `yfinance` throwing "possibly delisted; no price data found" errors
for **clearly still-listed, major symbols** (AAPL, TSLA, the S&P 500
index itself) — not actually delisted names. Reported causes include
user-agent/header handling issues on Yahoo's backend and general endpoint
instability. This corroborates, with fresh 2025 evidence, the prior
document's general caveat that yfinance is "prone to breakage" — but now
with concrete, recent examples rather than a general risk statement.

### 2d. Directly relevant to survivorship bias: an explicit statement that Yahoo does not serve delisted-firm data

A separate search result, in the context of comparing free vs. paid
delisted-data sources, stated plainly: **"Yahoo Finance doesn't provide
info for delisted firms."** This is a direct, explicit, third-party
statement (not my inference) that closes an open question from the prior
document — Yahoo was listed there as "unconfirmed" for delisted-symbol
retention (§5 coverage matrix, `free-root-data-source-recommendation.md`).
**This pass upgrades that from "unconfirmed" to "confirmed absent" for
Yahoo specifically.**

### Classification of every requested item for Yahoo

| Item | Classification | Basis |
|---|---|---|
| Safe for cross-checking (currently-listed names, if the service still works)? | **UNCLEAR — VERIFY DIRECTLY**, and now also **CAPABILITY-UNCERTAIN** | Legal terms unreachable directly (§0); additionally, §2b's finding raises doubt about whether free historical retrieval still functions at all as of this research |
| Safe for gap-filling? | **UNCLEAR — VERIFY DIRECTLY**, same capability caveat | Same |
| Safe for validation (non-commercial internal research)? | **LIKELY ALLOWED, per the search-surfaced quoted clause (§2a), but NOT independently confirmed by me** | The quoted "personal... non-exclusive license" language is consistent with internal research use; "not independently confirmed by me" because I could not fetch the source myself |
| Commercial use prohibited? | **CONFIRMED PROHIBITED, per the search-surfaced quoted clause** | The quoted clause explicitly bars commercial exploitation without written permission — this reading is consistent across multiple independent search results in both this pass and the prior one, which increases (without making certain) confidence in it |
| Delisted-security historical data available at all? | **CONFIRMED ABSENT** (§2d) | Direct, explicit third-party statement found; consistent with the "unconfirmed" status in the prior document now being resolved in the negative |
| Free historical-data retrieval still functioning as of this research? | **UNCLEAR — NEWLY FLAGGED RISK** | §2b's March 2025 paid-plan-gating report was not known to the prior document; must be resolved before relying on Yahoo/yfinance for anything in the architecture |

**Bottom line on Yahoo: worse than the prior document assumed, on two
separate axes.** Not only are the legal terms still not independently
verified by me (§0), but there is now genuine, fresh evidence that the
**free historical-data capability itself may have been withdrawn or
restricted** since early 2025, and **confirmed evidence that delisted-firm
data was never available from Yahoo in the first place**. Both findings
push toward **reducing reliance on Yahoo**, not merely treating it as a
"secondary, lower-priority" source as the prior document did.

---

## 3. Critical survivorship-bias test — the four-part decomposition requested

The Controller is right that "we know which companies were delisted" is
not sufficient. Decomposing exactly as requested:

### A. Historical identity coverage

**Can we know that a security existed at some point in the past, with a
name/ticker/CIK, even if it's gone today?**

**YES, reasonably well, for free.** SEC EDGAR's CIK-based registrant
records, plus the community-maintained SEC-EDGAR-derived GitHub dataset
of 36,000+ delisted stocks (unchanged from the prior document's finding,
not re-litigated here), give us **identity** — the existence fact. This
answer is unchanged by this verification pass.

### B. Historical universe membership

**Can we know, for a specific historical date D, the complete set of
securities that were actually tradable/listed on that date — not just
that they existed at some point?**

**NO — this remains a confirmed gap, and this pass found nothing to
change that finding.** Nasdaq Trader's Symbol Directory is a
**current-day snapshot only** (unchanged finding). No free source was
found, in either research pass, that provides a ready-made, point-in-time
"as of date D, here is the full tradable universe" feed. This is
**weaker** than identity coverage (A) — knowing a security existed
somewhere in a multi-year window is not the same as knowing it was
tradable on a specific day within that window.

### C. Historical price coverage

**Can we obtain OHLCV price history for securities, in general, going
back far enough for multi-regime calibration?**

**YES, for currently-active/currently-known securities, with real but
now-heightened uncertainty about Yahoo specifically (§2b).** Stooq
remains untested in this pass (§1) for both legal terms and actual
historical depth per symbol. This item is about **currently-listed**
names' price depth — a distinct question from (D) below.

### D. Historical price coverage AFTER identifying delisted securities — the decisive question

**Can we take the delisted-security list from (A) and actually retrieve
their pre-delisting price history, for free, in bulk?**

**This is where this verification pass produces its most important
confirmed finding: NO, not confirmed possible, and if anything the
evidence weakened since the prior pass, not strengthened.**

- Yahoo: **confirmed to not serve delisted-firm data at all** (§2d) — a
  definitive "no" for this specific source, upgraded from "unconfirmed"
  in the prior document.
- Stooq: **still entirely unverified**, both legally (§1) and
  behaviorally — this pass's searches for Stooq-specific delisted-symbol
  retention evidence came back empty, same as the prior pass. No new
  information, positive or negative, was found.
- **No other free source in either research pass was found to close this
  gap.**

### Explicit answer to the Controller's framing question

> "The set of securities that existed at each historical date AND their
> available price history at that date."

**We cannot reconstruct this from free sources, based on all research
performed across both passes.** We can reconstruct a reasonable
approximation of "securities that existed at some point in a multi-year
window" (A), a weak-to-nonexistent reconstruction of "the exact
point-in-time tradable set on date D" (B), reasonably good price coverage
for **currently-known/currently-listed** securities (C), and **an
unconfirmed, likely materially incomplete** price coverage specifically
for the delisted subset (D) — with Yahoo now definitively ruled out as a
source for (D) and Stooq's status for (D) remaining exactly as uncertain
as before this pass.

### Quantifying what remains biased

Precisely, in the same terms the earlier calibration methodology document
used: **any universe reconstructed from this free architecture, for any
historical date, is very likely to under-represent securities that were
later delisted** — not because we don't know they existed (A is fine),
but because even if we correctly include them in our target candidate
list, we may not be able to retrieve enough of their actual price history
to run the frozen strategy simulation against them (D is the unresolved
gap). **The magnitude of this under-representation is currently unknown
and unmeasured** — it can only be quantified empirically, by attempting
retrieval and counting successes vs. failures (exactly the
`DataCoverageLog` mechanism the prior document already proposed, §14
there) — **not before that measurement is actually run**, which requires
data acquisition the Controller has explicitly said not to begin yet.

---

## 4. Free-data bias control design

Since complete survivorship-free history is **not confirmed possible for
free** (§3D), here is the strongest honest control design, elaborating
the Controller's four suggested controls:

### CONTROL A — Current-survivor universe calibration

Run the full walk-forward calibration methodology
(`historical-data-calibration-plan.md §5`) using **only** securities that
are tradable today and have full historical price depth available. This
is the **baseline, most reliable, least biased-in-a-hidden-way** control,
precisely because its bias (survivorship bias) is **explicit and named**,
not hidden. Every metric from this control should be labeled explicitly:
**"Current-survivor universe; known survivorship bias; results likely
overstate historical opportunity quality and understate historical
downside-tail risk."**

### CONTROL B — Expanded historical identity universe using SEC delisted/security history

Run the same calibration on the **broader** candidate set that includes
identified-but-possibly-price-incomplete delisted securities from §3A,
**using whatever partial price history is actually retrievable per
symbol** (not skipping a delisted symbol just because it's delisted —
attempting retrieval and recording the outcome either way, per the
`DataCoverageLog` design). This control's results will be **influenced by
which delisted symbols happen to have retrievable price history** — which
is itself likely non-random (e.g., a company that delisted via a
well-covered merger might have better retained price data than one that
delisted via obscure bankruptcy) — so Control B's results carry their own,
different, and equally-important-to-disclose bias: **"Expanded universe;
delisted-symbol inclusion is incomplete and possibly non-random in which
symbols are covered; do not treat as fully survivorship-bias-free."**

### CONTROL C — Compare results where historical prices exist

For the **subset of delisted symbols where price history IS successfully
retrieved** (identified via the coverage measurement, not assumed), run a
**direct comparison**: does including this successfully-retrieved
delisted subset change the calibration's conclusions (e.g., a different
ATR% band, a different sense of the wasted-slot rate) relative to Control
A? If the inclusion of even a partial, imperfect delisted sample
meaningfully shifts conclusions, that is itself important evidence that
Control A alone (survivors-only) is materially misleading — a finding
worth surfacing to the Controller regardless of whether the full
gap can ever be closed.

### CONTROL D — Report missing-price/delisted coverage explicitly

**Not a calibration run — a mandatory reporting requirement attached to
every calibration result produced under this architecture.** Every
evidence package (per `historical-data-calibration-plan.md §13`) must
state, as a first-class, prominent figure — not a footnote:

- Total number of identified historical candidates (from SEC/GitHub
  delisted list + currently-active universe).
- Number/percentage with full price history successfully retrieved.
- Number/percentage delisted, with price history successfully retrieved.
- Number/percentage delisted, with price history **not** retrieved (the
  actual, measured hole).

### The objective restated, per the Controller's own framing

**These controls do not remove survivorship bias.** They convert an
unmeasured, silent assumption into a **measured, disclosed, quantified
uncertainty** that the Controller can weigh explicitly when deciding
whether any resulting numeric-parameter evidence meets the bar for
approval. That is the most this free architecture can honestly claim to
do.

---

## 5. Strategy mechanics — confirmed unchanged

Explicitly confirmed, as a checklist, that nothing in this verification
pass touches, proposes changing, or implies changing any of the following
(all remain exactly as previously approved):

- ✅ D-0011 debounce (state machine + asymmetric re-arm) — unchanged.
- ✅ D-0012 Last Trade as the live trigger source — unchanged.
- ✅ D-0007 Controller approval / 5-minute + ±0.5% re-check — unchanged.
- ✅ Ladder spacing (0%, −5%, −8%, −10% Floor) — unchanged.
- ✅ D-0026 architecture (symbol-agnostic engine, `ApprovedUniverseSnapshot`
  boundary, 9-stage selection pipeline) — unchanged.
- ✅ CRASH-regime handling (tighten thresholds, do not empty the universe)
  — unchanged.
- ✅ EMPTY-universe handling (no TSLA fallback, no fabricated symbols, no
  reuse of a stale snapshot) — unchanged.

**Missing or imperfect historical data is not "solved" anywhere in this
document by adjusting any of the above.** Where data is insufficient, the
correct response (per `historical-data-calibration-plan.md §14`'s
failure/insufficient-data policy, unchanged and reaffirmed here) is: the
affected parameter, or the affected calibration finding, remains
PROPOSED/TBD or is labeled with its measured uncertainty — never resolved
by quietly changing the strategy to make the data problem go away.

---

## 6. Final decision

```
FREE ROOT:                 Stooq
                            (legal terms: UNCLEAR — VERIFY DIRECTLY, not
                            yet independently confirmed by me or anyone
                            in this session, per §0 and §1)

FREE SECONDARY:             SEC EDGAR (identity/SIC/delisted-list —
                              CONFIRMED usable, official government data)
                             Nasdaq Trader (current symbol/ETF reference —
                              CONFIRMED usable, official exchange data)
                             Yahoo Finance / yfinance (DOWNGRADED this
                              pass — legal terms still UNCLEAR; delisted
                              data CONFIRMED ABSENT; free historical-data
                              capability itself now CAPABILITY-UNCERTAIN
                              per the March 2025 paid-gating report, §2b)
                             FRED VIXCLS (CONFIRMED usable, official
                              government data, no new concerns found)
                             Curated leveraged/inverse ETF list
                              (CONFIRMED usable — a manually-compiled
                              list, not a licensing question)

LOCAL DATASET:              Canonical normalized dataset, CIK-anchored
                            identity, unchanged design from
                            free-root-data-source-recommendation.md §14,
                            now explicitly required to include the
                            DataCoverageLog measurement (§4, Control D
                            above) as a first-class, not optional,
                            component

TOTAL SUBSCRIPTION COST:    $0 — confirmed; no paid provider proposed,
                            authorized, or used anywhere in this pass

SURVIVORSHIP-BIAS STATUS:   NOT SOLVED. Partially, honestly approximated
                            at the identity level (§3A); NOT solved at
                            the point-in-time universe level (§3B) or the
                            delisted-price-history level (§3D). Magnitude
                            currently UNMEASURED — can only be quantified
                            once actual data acquisition and the
                            DataCoverageLog measurement run, which has
                            NOT happened yet (Phase 2 boundary respected)

BIGGEST REMAINING DATA GAP: Free, verified, bulk pre-delisting OHLCV
                            price history for delisted US equities/ETFs.
                            Confirmed NOT available from Yahoo (§2d).
                            Still entirely UNVERIFIED, one way or the
                            other, for Stooq (§1) — the single most
                            important open question before any
                            acquisition begins.

IS FREE DATA GOOD ENOUGH FOR PHASE-2 CALIBRATION?
                            YES, WITH LIMITATIONS — unchanged from the
                            prior document's conclusion, but the
                            limitations are now more precisely
                            characterized and, on the Yahoo side,
                            somewhat more serious than previously known.
```

### A. What we can safely calibrate

- The core **strategy-mechanics behavior** (Ladder1/Ladder2/Floor/
  Trailing trigger frequency and sequencing, the no-warning-Floor rate,
  the wasted-slot rate) against **Control A's current-survivor universe**
  — this is the most defensible, cleanly-labeled piece of evidence this
  architecture can produce, precisely because its one bias (survivorship)
  is explicit and named, not hidden.
- **Market-regime classification** using FRED VIXCLS — unaffected by any
  finding in this pass, remains a strong, free, low-risk component.
- **Sector approximation** (SIC-derived) and **ETF/leveraged-ETF
  exclusion** — unaffected by this pass's findings.

### B. What we cannot safely calibrate

- **Any parameter whose approval hinges on genuine point-in-time universe
  membership** (§3B) — e.g., precise concentration/correlation
  measurements across the *true* historical universe, as opposed to
  today's survivors.
- **Any parameter whose approval requires confidence that the delisted-
  security subset is adequately represented** — e.g., the ATR% upper
  bound's "no-warning-Floor rate" measurement will be **understated** if
  delisted (often more volatile, often failure-prone) names are
  systematically under-covered, and we do not yet know by how much.
- **True historical spread/execution-quality calibration** — unchanged
  from the prior document, still relies entirely on the labeled range
  proxy; no new finding in this pass changes this.

### C. What must be disclosed in the final calibration report

Non-negotiable, per Control D (§4) and this pass's findings:

1. The measured `DataCoverageLog` statistics — total identified
   candidates, % with full price history, % delisted with price history
   retrieved, % delisted without.
2. Explicit statement of which control (A, B, or C from §4) produced each
   reported metric — never blended without attribution.
3. **Legal-terms status of Stooq and Yahoo at time of acquisition** —
   whichever party (Controller, or a future session with unblocked web
   access) ultimately confirms these, the confirmation itself (not just
   an inference) must be part of the record before large-scale bulk
   ingestion begins.
4. **Yahoo's confirmed absence of delisted-firm data** (§2d) and its
   **uncertain free-tier historical-data capability** (§2b) as of this
   research date — so future readers of the calibration report understand
   why Yahoo's role, if any, is narrower than originally scoped.

### D. What evidence would be required before approving numeric D-0026 parameters

**Unchanged in structure from `historical-data-calibration-plan.md §13`'s
eight-point checklist**, with **two amendments arising from this
verification pass**:

9. **(New)** Stooq's terms of use independently confirmed — by direct
   human visit to the primary source, or a technical environment capable
   of reaching it — **before** relying on Stooq-sourced data in any
   evidence package presented for parameter approval.
10. **(New)** Yahoo's role in the architecture explicitly re-scoped (or
    removed) based on a **current, re-verified** check of whether its
    free historical-data capability still functions at all — not assumed
    to still work as originally researched, given §2b's finding.

**No numeric parameter may be proposed for APPROVED status until both of
these, plus the original eight-point checklist, plus the Control-D
disclosure (§4, §C above), are satisfied.**

---

## Closing statement — no further provider-shopping

Per the Controller's explicit instruction, this document does **not**
propose evaluating any additional new provider. The architecture remains
exactly as approved directionally (Stooq root; SEC EDGAR, Nasdaq Trader,
FRED, and a curated leveraged/inverse list as secondary; Yahoo retained
but downgraded in scope pending re-verification). The **only** action this
document recommends before data acquisition begins is: **resolve the two
UNCLEAR legal-terms items (Stooq entirely, Yahoo's exact current text)
via a channel that can actually reach the primary sources** — something
this environment's network access could not do, across every attempt
made.

No data was downloaded, no code written, no dependency added, no VM
created, nothing purchased, no scheduler/cron/live routine touched, no
orders created, and no D-0026 implementation performed while producing
this document. TSLA was not used, referenced, or implied as any form of
fallback, baseline, or shortcut anywhere in this document. Strategy
mechanics (§5) confirmed unchanged. D-0026 remains PROPOSED / NOT
APPROVED. Phase 3 remains NOT approved.
