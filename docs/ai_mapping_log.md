# AI-assisted mapping log

What an assistant produced when asked to draft source mappings for this dataset, what
was accepted, and what was refused. It follows the workflow in
[operations.md](operations.md): a model drafts, a human decides, the rule validator
has the last word.

**Nothing in the catalog changed because of this session.** All nine ingest rows are still
`illustrative` / `draft`, because a confident mapping is not evidence.

## Inputs given

- The 11 field names, descriptions, declared types, valid ranges and known-issues text
  from `data/raw/data_dictionary_control.csv`.
- The names of the source systems listed in that file.
- Instruction: propose `source_dataset.source_field` and the transformation for each
  field, quote the evidence you are relying on, and write `UNKNOWN` instead of
  guessing. No extract of `transformation_rules.csv` was provided — the existing
  register was the thing being checked.

## Drafts and verdicts

| Field | Draft mapping | Verdict | Why |
|---|---|---|---|
| `loan_id` | `Opportunity.Id` | accept | Matches the register. `Opportunity.Name` is a label, not a key — the draft had to be told which. |
| `borrower_id` | `Account.Id` | accept | Matches. Kept `draft`: the Salesforce object is asserted by the dictionary, not by a connection. |
| `industry_sector` | `CompanyProfile.sic_codes` → sector label | accept with a question | The crosswalk from a SIC code to five sector names is a transformation with no document behind it. No intake row covers it yet, so it is recorded here as an open question rather than resolved. |
| `years_in_business` | `CompanyProfile.date_of_creation` → years at observation date | accept | Matches. The dictionary's "cap at 50" is a rule the draft did not know, so the register stayed authoritative. |
| `annual_revenue_gbp` | `FiledAccounts.turnover`, latest filed | accept, one refusal | Accepted. The draft also proposed `COALESCE(turnover, 0)`; refused — April 2024 already shows `0` where data was missing, and that coercion is the bug, not the fix. |
| `commercial_credit_score` | `RiskReport.risk_score`, "0-999" scale | refuse the scale, keep the field | A 0-100 band is what the dictionary declares. The assistant supplied a plausible bureau scale from general knowledge; no specification in this project says so. That is exactly the confident-but-unevidenced claim the `illustrative` label exists to stop. |
| `loan_amount_gbp` | `LoanMaster.principal_amount` | accept | Matches; still `draft` because the core-banking extract is not represented by a schema scan. |
| `interest_rate` | pricing grid on score band × sector, minus tenure discount | accept | Matches the register's formula and its three inputs. `PRICING_GRID` stays open: the grid itself is not on file, so the rule cannot be confirmed. |
| `payment_status` | `CollectionsStatus.arrears_code` | accept with a question | Matches. Draft inferred `Late_30` means 31-60 days past due — refused, since the thresholds are a business definition, not a naming convention. That is INT-008, already open. |
| `snapshot_date` | `ETLBatch.batch_date` | accept, flagged | Matches. Whether this is an extraction or an effective date is `SCORE_TIMING` / INT-005, and a model cannot settle it. |
| `dti_ratio` | "debt service ÷ income, from instalment amount" | refuse | The legacy name is a trap the draft walked straight into. The catalog defines principal ÷ annual revenue, and `docs/investigation.md` is open because the stored values disagree with that. Following the name would have rewritten the definition to match a wrong guess. |

## Failure modes observed

1. **Naming beats evidence.** The worst draft came from the field name, not the
   dictionary — `dti_ratio`. Anything derived from a name alone is refused.
2. **Real-world specificity the project cannot support.** A 0-999 bureau scale sounds
   right and is not documented anywhere here. Plausible numbers are the most expensive
   kind of wrong.
3. **Helpful coercion.** `COALESCE(..., 0)` on revenue would have made a check go green
   by destroying the distinction between zero revenue and unknown revenue.
4. **It does not know what is missing.** The draft never flagged that the pricing grid
   or the SIC crosswalk is absent, because it had no way to look. The catalog's open
   issues did that, which is the reason to run this against a governed register rather
   than a spreadsheet.

## What was kept

The accepted drafts agree with the register the pipeline already loads, so the useful
output is the four refusals and the two open questions — recorded above and pointed at
`CALC_RATIO`, `PRICING_GRID`, `SCORE_TIMING` and `SOURCE_EVIDENCE`. Promoting an
external row to `vendor-spec` still needs a real specification reference and review
date, and `lineage/load_transformation_rules.py` rejects an `illustrative` row marked
confirmed. An assistant cannot clear that bar on its own.
