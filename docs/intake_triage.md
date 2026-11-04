# Intake and triage

`data/raw/intake_requests.csv` is the register of things people asked for. It exists
because a governance project that only records what the pipeline found looks like a
tool nobody has to answer to. Twelve real-looking requests are here, and the point of
the register is that most of them end in `blocked_on_evidence` or `new`, not in a tidy
green tick.

## Two queues, deliberately different

| | `intake_requests.csv` | `governance_issues` table |
|---|---|---|
| Comes from | a person, with a channel and a date | the reconciliation run, the calculation check, seeded review items |
| Can close itself | no | only schema findings, and only when the latest run stops reporting them |
| Lives in | a committed CSV | MySQL, with owner, next action and closure evidence |
| Answers | "who asked, and what did we tell them" | "what is known to be wrong, and what proves it" |

A request that turns into a defect gets a `linked_object` pointing at the issue key
(`CALC_RATIO`, `PRICING_GRID`, `SCORE_TIMING`, `SOURCE_EVIDENCE`) or at the document
that carries the answer. The register is a file, not a governed table: nothing loads it
into MySQL and nothing validates it, so it cannot promise an audit trail.

## Request types

`new_field`, `new_source`, `definition_change`, `definition_question`,
`calculation_change`, `extract_request`, `evidence_request`, `usage_rights`,
`access_request`, `documentation`, `incident`, `new_check`.

The type decides the route, not the priority:

- **definition_change** goes through `metadata/dictionary_service.py` as a new version
  with an effective date. Descriptions are not edited in place.
- **calculation_change** first has to survive `metadata/calculation_validation.py`. If
  the values and the formula disagree, the disagreement is the finding.
- **evidence_request** and **usage_rights** cannot be answered from the catalog alone.
  They need a specification or an agreement on file, which is why `illustrative` rows
  stay `draft`.
- **access_request** is out of scope: this project has no account authorisation, and
  reading the catalog is not permission to read the data.

## Priority

`high` if a published number, a legal/contractual claim, or a downstream decision
depends on the answer. `medium` if the work is blocked on something that exists but is
not on file. `low` otherwise. Nothing here encodes an SLA, because no SLA has been
agreed with the requesters — that is INT-011's neighbourhood, not a claim.

## Status

`new` (logged, not yet understood) → `triaged` (owner and next action agreed) →
`in_progress` → `blocked_on_evidence` (a named document or decision is missing) →
`closed` (with the answer recorded in `next_action` or the linked document).

`blocked_on_evidence` is the honest state for most of this register. A request is not
closed because someone replied; it closes when the evidence arrived or the ask was
refused and the refusal is written down — INT-001 is closed on a refusal.

## Running the triage

1. Append a row. Do not overwrite history; request ids are never reused.
2. Choose the route by type, then set owner, priority and `next_action` as one
   imperative sentence with a named object.
3. If it reveals a defect, open the matching governance issue and reference it in
   `linked_object`.
4. If it can only be answered by a document nobody has filed, set
   `blocked_on_evidence` and name the document.
5. Anything dated after the latest `snapshot_date` in the extract is a freshness
   question: check `reports/local/data_quality.csv` before promising an answer.

## What this register does not do

It does not prove the requests happened, does not schedule anything, and does not
notify anyone. It is a portfolio-scale intake log: enough structure to show the
routing decisions and the blockers, not a ticketing system.
