# Support runbook: the closing price has not updated

**Who this is for.** Someone was told the daily market dataset looks out of date and
has to say whether that is an outage, a weekend, or nobody having run the fetch.
This is a local demonstration catalog: there is no scheduler, no alerting and no
service owner to page. "Running the fetch" means a person running one command.

The dataset is `market_close_prices_daily`, physically
`raw_market_close_prices_daily`, declared `Daily` in `datasets.refresh_frequency`.
It is downloaded at run time by `etl/load_market_prices.py` and **is not committed to
this repository** — the redistribution right is recorded as unconfirmed in
`vendor_usage`, so the values live in a database and in gitignored
`reports/local/market_fetch.json`, never in the repo.

## The one command

```bash
python -m etl.load_market_prices
```

It ends with a line like the one below — this is the real output on 2026-11-03, a
Tuesday, against a Friday close:

```
[freshness] current: 2 trading days since the last price on 2026-10-30 (tolerance 2); last row 2026-10-30, 0 holiday rows and 0 weekdays with no row in the window
```

## Is it actually stale?

A closing price only exists for days the exchange traded, and the source publishes
with its own lag. So the last observation being *Friday* while today is *Tuesday* is
`calendar_days_since_last_row = 4` and `trading_days_since_last_price = 2`, which is
the normal state of the world on a Monday and most Tuesday mornings. The verdict uses
the trading-day count, so it does not fire on a weekend; it does fire on a long
weekend, because the approximation is Monday-to-Friday (see the last section).

| `verdict` | Meaning | What to do |
|---|---|---|
| `current` | Within two trading days of the run date | Nothing. Confirm the requester is not comparing against today's close, which does not exist yet. |
| `stale` | Three or more trading days with no priced row | Work down the steps below. |
| `no_data` | The table has no rows at all | The fetch has never succeeded in this database. Run the command above and read its failure. |

`last_row` and `last_priced_day` are different columns on purpose. The source keeps a
row for every day it covers and leaves `close_value` blank on an exchange holiday, so
on a holiday Monday `last_row` is that Monday while `last_priced_day` is the Friday
before it. The verdict follows `last_priced_day`: a row is not a price.

## Step 1 — is the dataset registered at all?

```sql
SELECT dataset_name, physical_table_name, refresh_frequency, business_owner
FROM datasets WHERE dataset_name = 'market_close_prices_daily';
SHOW TABLES LIKE 'raw_market_close_prices_daily';
```

No `datasets` row means the loader has never been run against this database: the row
is created by the loader, not by setup. That is deliberate — a registered dataset
whose table only exists after a network call would make every pipeline run depend on
a third party's uptime.

## Step 2 — run the freshness query directly

```
mysql> SOURCE sql/validation/03_market_freshness.sql;
```

Same query the loader prints, with more columns. The four that matter:

- `trading_days_since_last_price` — Mondays to Fridays strictly after the last priced
  row. Above 2 is late.
- `calendar_days_since_last_row` — there for comparison, so you can see why a Monday
  ticket that says "four days" is not four days.
- `holiday_rows_in_window` — rows inside the last 42 days the source published with a
  blank value. These are exchange holidays (2026-09-07, 2016-11-24, and so on) and are
  expected. Not a missing price.
- `weekdays_without_row_in_window` — Mondays to Fridays inside the window with **no row
  at all**. This is the one to chase: it catches a feed that resumed after an outage
  and never backfilled. The source's own holiday rows do not appear here, because
  those days do have a row.

## Step 3 — what did the last fetch actually return?

`reports/local/market_fetch.json` records the URL, the moment of the fetch, the row
count and the first and last trading day in the file. It is written only after a
successful load, so an old timestamp there with no new one means the command failed
or nobody ran it. That distinction matters more than the data: a feed that was never
asked for is not a feed that broke.

## Step 4 — the catalog's own report

```bash
python -m metadata.data_quality
```

Writes `reports/local/data_quality.csv` for every registered dataset. For this one
you get two freshness rows and they mean different things:

| subject | It measures | On a healthy feed |
|---|---|---|
| `trading_day` | the feed: the latest day the source published a row for | `current` |
| `retrieved_at` | the job: when the loader last ran | `current` **by construction** — the loader stamps it at fetch time, so it can only be stale if the loader has not succeeded. Read it as a timestamp, not a signal. |

The generic check is coarser than the query in Step 2, and that is worth knowing
before you contradict it: it reads `MAX(trading_day)`, so on an exchange holiday its
freshness row points at a day with no price, while `03_market_freshness.sql` follows
`last_priced_day`. On the loaded ten-year window, 96 of 2,607 rows carry a blank
`close_value` for exactly this reason (2026-09-07 Labor Day is one of them). They stay
NULL instead of becoming a price, so `close_value` shows a non-zero null count and
`review` — the source saying it had no value, which a check that quietly turned it into
zero would hide.

## Step 5 — when the command itself fails

| Message | What happened | Do |
|---|---|---|
| `HTTP Error 403/404/...` | The endpoint moved, rate-limited you, or is behind something | Retry once. If it repeats, the source changed: do not add a fallback source without deciding its own terms of use. |
| `answered with a page rather than the CSV download` | You got HTML, not data (interstitial, proxy, or a moved endpoint) | Open the URL in a browser. If it downloads there, the request needs what the browser sends — and that is a decision about scraping, not a bug to patch quietly. |
| `unexpected header [...]` | The source renamed a column or the series | The loader refuses to guess which column is the price. Check the series id against the source before changing the constant. |
| `the download contained no observations` | A syntactically valid file with no rows | Do not load it. An empty table would make every downstream freshness row `no_data`, which is a worse signal than a failure you can see. |

A failed fetch never writes the provenance file and never truncates the table
(`if_exists="replace"` only runs on the successful path). A half-loaded dataset is the
one outcome that would make the freshness signal lie.

## What is out of scope here

- **The exchange calendar, second-hand.** Trading days are approximated by
  Monday–Friday, and the holidays themselves come from the source's own blank rows.
  If the source drops a holiday entirely instead of publishing a blank, that day moves
  from `holiday_rows_in_window` to `weekdays_without_row_in_window` and looks like a
  hole. There is no independent calendar in this project to tell the two apart, so
  check the dates against the exchange before escalating a non-zero hole count.
- **Publication lag.** `retrieved_at` is when *we* asked. The source's own delay is
  not measured, so "the price is wrong for the day" is not answerable from here.
- **Redistribution.** The closing levels are the index owner's numbers, reached
  through a public source whose terms of use have **not been reviewed**. Until
  `vendor_usage.FRED ...open_question` is closed by the agreement owner, do not put
  these values in a published chart, a portfolio screenshot, or anything that leaves
  this machine.
- **Correction.** Never edit `close_value` by hand to make a check pass. Re-fetch, or
  open a governance issue against the field and leave the discrepancy visible — which
  is what this catalog is for.
