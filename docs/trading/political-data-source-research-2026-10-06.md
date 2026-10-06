# Political-trade data: three options, tested for real

**Date:** 2026-10-06
**Status:** RESEARCH COMPLETE — no code written, no decision taken.
**Why:** the paid provider's key expired and will not be renewed
(P-079), so the 15-point political component is 0.0 for every
candidate. The Controller asked for a deep check of the three
replacement paths before anything is built.

**Everything below was executed, not assumed.** Real HTTP calls, a real
ZIP downloaded and extracted, a real filing fetched, and a real match
against our own approved whitelist. Where a check could not be
completed in this container, that is stated rather than guessed.

---

## 0. Reachability, measured first

```
https://www.capitoltrades.com            403
https://efdsearch.senate.gov/search/     302  -> 200 after following
https://disclosures-clerk.house.gov      200
https://raw.githubusercontent.com        200
```

**The 403 is this container's egress policy, not the site being down.**
The VM is a different network: today's engine log shows it reached
Alpaca, Telegram, Perplexity, Finnhub, AlphaVantage, Tiingo, Polygon and
FRED successfully, so the VM can be assumed to reach all four. Any
conclusion below that depends on the 403 is marked as unverified from
here.

---

## 1. Option 1 — the official government sources

### What was verified

The House Clerk's annual index downloads and extracts cleanly:

```
https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2026FD.zip
http 200, 62,380 bytes
-> 2026FD.xml  (467,945 bytes)  +  2026FD.txt
file timestamp: 2026-10-06 09:00  (same day)
```

The XML is a real filing index, one record per filing:

```xml
<Member>
  <Last>Yakym</Last><First>Rudy C.</First>
  <FilingType>P</FilingType><StateDst>IN02</StateDst>
  <Year>2026</Year><FilingDate>7/13/2026</FilingDate>
  <DocID>20034984</DocID>
</Member>
```

Counts for 2026, computed from the file:

```
total filings 1739
by type:  C 850 | P 411 | X 247 | W 104 | D 83 | A 40 | H 2 | T 2
P = Periodic Transaction Report = the trades: 411
```

A real PTR fetched by DocID:

```
https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20034984.pdf
http 200, 65,322 bytes, PDF 1.4, 1 page
```

### The blocker, stated honestly

**The index gives the filing, not the ticker.** The ticker lives inside
the PDF. Whether that PDF carries an extractable text layer **could not
be determined in this container**: `pypdf` installs but fails to import
because the system `cryptography` binding is broken here
(`_cffi_backend` missing, a pyo3 panic), and no `pdftotext` binary is
available. A raw inspection found both `/Font` and `/Image` with
`DCTDecode` present, which is consistent with either a text PDF
carrying a signature image or a scan — it does not settle the question.

**This must be settled on the VM, where a clean install is possible. It
is a 10-minute check, and it decides whether option 1 costs days or
weeks.**

### The Senate half

Our current code does a plain GET of the Senate search page. The real
endpoint redirects (302) to an agreement page and issues a CSRF token:

```
csrfmiddlewaretoken  value="AbVHblJW...0WDh"
```

So the Senate side needs the token round-trip before any search returns
results. Our implementation does not do it, which is why it yields
nearly nothing — consistent with P-074.

### Verdict

```
cost      days, and unknown until the PDF text question is settled
value     official, free forever, no key to expire, no rate limit,
          and the earliest possible view of a filing
```

---

## 2. Option 2 — our own CapitolTrades scraper

Already written and deterministic — no LLM in it, and it raises an
explicit parser-broken error rather than fabricating a record:

```
src/research/capitol_trades_scraper.py
```

Two real gaps, both read from the code:

1. It handles **one politician per instance** (default slug
   `ro-khanna`). Nothing in the repo builds the 15-instance dict the
   aggregator already expects — the aggregator's `capitol_scrapers`
   argument is simply never passed (P-073).
2. The site returned **403 from this container**, so the parser could
   not be tested against today's live HTML from here. On a site that has
   changed its markup since the parser was written, the parser is
   designed to raise rather than silently return nothing — which is the
   right failure, but it is still a failure.

### Verdict

```
cost      hours, if the live HTML still matches the parser
value     the signal returns quickly
risk      scraping a site we do not control; breaks on a redesign
          and must be re-verified on the VM before being trusted
```

---

## 3. Option 3 — a community dataset on GitHub — VERIFIED WORKING

`kadoa-org/congress-trading-monitor`, served as static JSON from
`raw.githubusercontent.com`, which is reachable even from this
restricted container.

### What was fetched and checked

```
public/data/stats.json    http 200
public/data/trades.json   http 200, 4,367,990 bytes, 5,000 rows
public/data/filers.json   http 200, 449 filers
public/data/filer/house_nancy_pelosi.json         http 200, 186 rows
public/data/filer/senate_thomash_tuberville.json  http 200, 1.4 MB
```

Its own stats file, generated the same day:

```json
"totalTrades": 69546, "totalFilers": 449,
"dateRange": {"from": "2011-06-20", "to": "2026-09-28"},
"bySource": {"house_clerk": 45394, "oge_executive": 14934,
             "senate_efd": 9218},
"generatedAt": "2026-10-06T16:06:08.167Z"
```

Every field we need is present per row:

```
transaction_date, filing_date, ticker, asset_name, asset_type,
transaction_type, amount_range_low/high/label, filer_name, filer_id,
chamber, party, state, days_to_file, is_late, doc_url
```

Updates are automated daily — commits to `public/data` by `kadoa-bot`
on 2026-10-01 through 2026-10-06, messages of the form
`Daily refresh: 2026-10-06`.

### Our own whitelist, matched against its 449-filer index

```
Nancy Pelosi               house_nancy_pelosi              132 purchases
Daniel Crenshaw            house_daniel_crenshaw            23
Josh Gottheimer            house_josh_gottheimer          1460
Michael T. McCaul          house_michaelt_mccaul           295
Rohit Khanna               house_rohit_khanna              682
Mark Green                 house_markdr_green              464
Markwayne Mullin           senate_markwayne_mullin         364
Daniel S Sullivan          senate_daniels_sullivan          30
Thomas H Tuberville        senate_thomash_tuberville       587
A. Mitchell McConnell      senate_amitchell_mcconnelljr     37
Patrick Fallon             house_patrick_fallon             67
Shelley M Capito           senate_shelleym_capito          365
John Boozman               senate_john_boozman             252
Debbie Wasserman Schultz   house_debbie_wassermanschultz    41

14 of our 15 are present. Chuck Schumer is absent.
```

Note the names differ from ours (`Rohit` vs `Ro`, `Daniel` vs `Dan`,
`Thomas H` vs `Tommy`), so a join needs the `filer_id` slug, not the
display name. Our own lookup is name-based.

### Verdict

```
cost      hours -- fetch one JSON per politician, map to our record shape
value     14 of 15 covered, tickers present, daily refresh, free,
          reachable from BOTH networks, and it parses the official
          filings itself rather than reselling an aggregator
risk      a third party's pipeline: it can go stale or disappear, and
          the DATA is offered "for research and educational purposes"
          (MIT covers the code only), which is a licence question for
          the Controller, not a technical one
```

---

## 4. The finding that matters more than the choice

While measuring option 3, the real disclosure lag was computed from the
5,000 most recent rows:

```
median lag between the trade and its filing      71 days
filed within 14 days of the trade                 5.3%
filed within 30 days of the trade                21.2%
filed LATER than 30 days                         78.8%
filed LATER than 14 days                         94.7%
```

The dataset's own figure over all 69,546 rows is a 32-day median with a
230-day 90th percentile. Both numbers say the same thing.

**Now compare that with our own political logic**, read from
`src/research/political_cluster.py`:

- trades are dropped entirely when `trade_date` is older than **30
  days**,
- and the cluster score — the ONLY component that can reach the entry
  threshold of 5 — counts distinct buyers within **14 days** of
  `trade_date`.

Both windows are measured from the TRADE date. We only ever learn a
trade exists on its FILING date, which is a median of 32 to 71 days
later.

```
So roughly 79% of trades are already outside our 30-day window on the
first day we could possibly see them, and about 95% are outside the
14-day cluster window.
```

**This is a design defect, not a data problem, and no data source fixes
it.** Even with the paid provider working perfectly, the political
component would have been near zero almost always — which is consistent
with never having seen it fire.

Recorded as **P-083**. It must be decided before any source is wired,
because wiring a source into windows that cannot see its data would
produce a working integration that still scores zero.

---

## 5. Recommendation

```
1. Settle P-083 first -- the windows. A source feeding windows that
   cannot see it is wasted work.
2. Then wire option 3. It is the cheapest working path, covers 14 of
   15, refreshes daily, and is reachable from both networks.
3. Spend 10 minutes on the VM settling whether a House PTR carries
   extractable text. If it does, option 1 becomes the durable base and
   option 3 becomes the fast path that no longer depends on a third
   party.
4. Option 2 only if the live HTML still matches our parser. Verify on
   the VM before trusting it.
```

**What would change this recommendation:** if the House PTR turns out to
be plain text, option 1 jumps ahead of option 3 on everything except
speed. If the Controller rejects the "research and educational purposes"
wording on option 3's data, options 1 and 2 are the only paths left.
