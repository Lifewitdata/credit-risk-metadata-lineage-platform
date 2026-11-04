# Extract specification — `credit_datamart_sme_loans`

One page for anyone building, receiving or reconciling this extract. Everything here
comes from `data/raw/data_dictionary_control.csv`, the registered dataset row, and the
generated scorecard; where the catalog cannot answer, the row says **unconfirmed**
rather than assuming a convention.

## The file

| | |
|---|---|
| Dataset | `credit_datamart_sme_loans` (one dataset, one physical table) |
| Grain | one row per loan facility per observation date |
| Format | UTF-8 CSV, `\n` line endings, header row, comma-delimited, no thousands separators |
| Identifier | `loan_id` + `snapshot_date` is the intended business key — **unconfirmed**, no uniqueness test runs against it today |
| Volume | the shipped fixture is 10 rows; there is no measured production volume |
| Refresh | declared `Monthly` in `datasets.refresh_frequency`; a specific calendar day and cut-off time are **not agreed** |
| Load behaviour | full replace of `raw_credit_datamart_sme_loans` on every load, not an append |
| Delivery location, retention, and who signs off a late file | **not specified anywhere in this project** |

## Columns

Types are as declared in the dictionary. Every run reads the physical schema from
`INFORMATION_SCHEMA` and the committed scorecard reports 0 type mismatches across all
11 fields, so declared and stored types currently agree.

| # | Column | Type | Unit | Allowed / range | Nulls | Documented source | Known issue |
|---|---|---|---|---|---|---|---|
| 1 | `loan_id` | string | — | non-blank | not observed | Salesforce | — |
| 2 | `borrower_id` | string | — | non-blank identifier | not observed | Salesforce | upstream CRM mapping unverified; label is `illustrative` |
| 3 | `snapshot_date` | date | ISO-8601 `YYYY-MM-DD` | ISO date | none | Internal Core Banking | April observation date conflicts with the illustrative score-change timing; extraction vs effective date not agreed |
| 4 | `industry_sector` | string | — | Retail, Construction, Hospitality, Manufacturing, Healthcare | not observed | Companies House | closed list is from the dictionary, not a validated reference table |
| 5 | `years_in_business` | integer | completed years | 1 – 50 | present in fixture | Companies House | outliers above 50 are capped at 50, so the column cannot measure age beyond 50 |
| 6 | `annual_revenue_gbp` | float | GBP, per year, latest reported | 0 – 10,000,000 | **1 of 10 rows is NULL** | Xero / QuickBooks / bureau | April 2024 rows carry `0` where data was missing. `0` and NULL mean different things to the ratio; do not silently convert either way |
| 7 | `commercial_credit_score` | integer | bureau points, higher is better | 0 – 100 | none | Experian Commercial | April 2024 scores shifted drastically; the scale redefinition on 2024-04-02 is under review and the mapping is labelled `illustrative` |
| 8 | `loan_amount_gbp` | integer | GBP, originated principal | 5,000 – 500,000 | none | Internal Core Banking | — |
| 9 | `interest_rate` | float | annual rate, decimal fraction (0.08 = 8%) | 0.05 – 0.20 | none | Internal Pricing Engine | produced by a pricing grid whose bands are not on file |
| 10 | `dti_ratio` | float | ratio, `loan_amount_gbp / annual_revenue_gbp` | 0.0 – 1.0 | none | Internal Calculated | name is legacy: this is **not** debt-service-to-income. 7 of 10 fixture values disagree with the documented formula; 3 are blocked by a missing or zero denominator |
| 11 | `payment_status` | string | — | Current, Late_30, Late_60, Default | not observed | Internal Core Banking | arrears buckets are labels only; the day thresholds behind them are not defined |

## What a receiver must not infer

- A source system named here is a claim about intent, not evidence of an integration.
  Only internal derivations are confirmed against the physical schema; every external
  feed ships as `draft` / `illustrative` (see `docs/lineage_report.md`).
- A row that types cleanly is not a verified definition. Schema conformity and
  verification are separate columns in the scorecard.
- `dti_ratio` must not be compared with any real DTI metric, and `annual_revenue_gbp = 0`
  must not be treated as "no revenue" until the April gap is resolved.

## Checks that actually run

| Check | Where | Effect |
|---|---|---|
| Undocumented / deprecated / type-mismatched columns vs the latest scan | `sql/validation/02_dictionary_reconciliation.sql` | opens and reopens governance issues |
| Null count per scanned column | `metadata/data_quality.py`, step 3 of `main.py` | reports `review` where any null exists; does not judge acceptability |
| Freshness of documented date columns vs declared cadence | same module | `current`, `stale`, `review`, or `unknown` when no cadence is mapped |
| `loan_amount_gbp / annual_revenue_gbp` against stored `dti_ratio` | `metadata/calculation_validation.py` | `pass`, `mismatch` or `blocked_input`; disputed source values are left untouched |

## Open items before this is a contract

1. Confirm the business key and whether one loan may appear under two `snapshot_date` values.
2. Get a written decision on `0` vs NULL for missing revenue and on observation-date semantics.
3. Attach the pricing grid and the bureau score specification, or restate both as unverified.
4. Agree a delivery day, cut-off time and who is paged when the file is late.
5. State the volume and growth the extract must sustain; nothing here measures it.
