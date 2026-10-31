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

## Verify a source mapping

Each input relationship in `data/raw/transformation_rules.csv` is verified on its
own, separate from the rule that consumes it:

- `source_dataset` and `source_field` must name the object and column the value
  actually comes from. `Experian Commercial` alone is a vendor name, not lineage.
- `source_transformation` says what happens to that field on the way in, so two
  inputs of the same rule can be described differently.
- `source_mapping_label` is `illustrative`, `vendor-spec` or `local-data`.
  Internal sources must use `local-data`; the loader checks their `source_field`
  against the latest physical schema scan and rejects a column that is not there.
- `source_verification_status` is `draft` or `confirmed`. An `illustrative` row
  cannot be confirmed — there is nothing to confirm it against. `vendor-spec` is the
  promotion path and needs `source_evidence_ref` (the specification, not a homepage)
  plus `source_verified_at`. A draft row may not cite evidence or a review date.
- A rule cannot be confirmed while any of its input relationships is unconfirmed,
  or while an open governance issue targets the field it produces.

So the honest starting position is what this fixture ships: internal derivations
confirmed against the schema, external feeds drafted and labelled illustrative.
Promoting one is a real review against a real document, and the register in
`docs/lineage_report.md` shows which rows still have no evidence on file.

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
4. Commit regenerated reports only from a database that has had a fresh setup and
   one pipeline run. The integration suite deliberately revises definitions and
   closes issues, so reports generated afterwards describe a mutated catalog.
5. For changes, retain the commit, run date, environment and tests in the handoff.

The GitHub Actions artifact includes generated reports from a disposable database.
SQLite is used only by fast regression fixtures; project execution remains MySQL.
The MySQL suite covers fresh setup and upgrade, concurrent version edits, audit
triggers, issue reopening and closure, whether relationship evidence survives a
round trip, and it regenerates `docs/data_dictionary.md`, `docs/lineage_report.md`
and `reports/calculation_validation.csv` and compares them with the committed
files. Editing a rule or a definition without regenerating its report therefore
fails the suite rather than shipping a stale document.
