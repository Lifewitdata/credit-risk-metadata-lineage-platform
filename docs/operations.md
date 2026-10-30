# Catalog operations

## Verify a field

Run `main.py` first to seed draft verification records. Check the source evidence,
units, calculation, date semantics and data usage before confirming a definition.
The evidence must support this field, not merely identify a vendor homepage.

```bash
python -m metadata.governance --dataset-id 1 --field loan_id --status confirmed --evidence "review-record-123" --reviewer "your-name" --reviewed 2026-10-30
```

Use the actual dataset ID from `datasets` and a real review reference. The command
rejects confirmations without evidence, reviewer and a valid nonfuture date.
A reviewed local CSV definition does not verify an illustrative external mapping.
Rule confirmation separately requires real source dataset/field names in the CSV.

## Version definitions

Call `update_field_definition(engine, dataset_id, field_name, FieldDefinition(...),
effective_date, changed_by)`. The effective date must follow the current version's
start. The row is locked on MySQL; a unique generated-column index independently
prevents two current versions. An empty string explicitly clears a text field;
`None` leaves it unchanged. The trigger records the authenticated database user;
`changed_by` is the operator label in the console, not verified identity.

The new date starts an inclusive interval; the previous definition ends the day
before. This date-granular example supports one version per field per day.

## Maintain the issue queue

`reports/local/governance_issues.csv` is the engineering handoff: issue key, field,
category, owner, priority, next action, evidence and status. Schema findings are
reconciled on every run. They close only when absent from the latest reconciliation
and reopen if they recur. Manual business-definition questions never auto-close.
Use `transition_issue` in `metadata/governance.py` to move a manual issue to
`in_progress` or `resolved`; resolution requires closure evidence.

## Vendor records

`vendor_usage` stores actual working records; `data/raw/vendor_usage.csv` only seeds
missing providers. All sample permissions are unconfirmed. There are no actual
agreements or renewal dates in this project, so those fields remain blank.
Use `update_vendor` to record reviewed terms, evidence and dates. Do not infer
redistribution permission from technical access. Escalate unresolved display,
derived-data, renewal and scope questions to the agreement owner.

## AI-assisted documentation

An optional manual workflow: give an AI assistant the local field list and an
approved source specification; ask for proposed mappings with quoted evidence and
explicit unknowns. Keep outputs in a separate draft CSV. A human must compare each
proposal to the source and run the rule validator before importing it. Never place
credentials or confidential agreements in a prompt. This project does not claim a
live AI integration or automatic verification.

## Reproduce and hand off

1. Run setup or upgrade against the dedicated database.
2. Run `python main.py` twice and inspect lineage change history: an unchanged
   second snapshot creates no new lineage changes.
3. Review the generated dictionary, lineage report, issue queue, vendor register
   and calculation report together. A schema pass is not a business-definition pass.
4. For changes, retain the commit, run date, environment and tests in the handoff.

The GitHub Actions artifact includes generated reports from a disposable database.
SQLite is used only by fast regression fixtures; production project execution
remains MySQL. The MySQL workflow covers setup, upgrades, concurrent version edits,
audit triggers and issue reopening/closure behaviour.
