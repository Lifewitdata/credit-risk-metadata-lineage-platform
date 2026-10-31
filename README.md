# Credit Risk Metadata & Lineage Platform

A hands-on Python/MySQL portfolio project for catalog maintenance, field lineage,
calculation reconciliation and governance issue tracking. It answers **where a
field comes from, whether its definition matches the database, what is verified,
and which questions still need an owner**.

The ten-row loan CSV and vendor mappings are **illustrative portfolio fixtures**.
Named systems are not connected integrations, licensed feeds, or verified vendor
methodologies. A source name alone is not evidence. Unknown source fields and
usage permissions remain explicitly unconfirmed.

## Review the work

- [Calculation reconciliation](reports/calculation_validation.csv): seven mismatches and three blocked inputs in the supplied fixture; no source values overwritten.
- [Source field register](docs/lineage_report.md): every input relationship with its source dataset, source field, transformation note, mapping label and verification state.
- [Investigation and recommended action](docs/investigation.md).
- [Catalog operations and verification workflow](docs/operations.md).
- [Rule snapshot with evidence/status columns](data/raw/transformation_rules.csv).
- [Vendor usage register seed](data/raw/vendor_usage.csv).
- [Validation record](docs/validation.md).

The pipeline generates `docs/data_dictionary.md`, `docs/lineage_report.md`, lineage
visualizations and `reports/local/` CSV extracts from MySQL. The generated reports
are committed, and the MySQL integration test regenerates them and compares the
result byte-for-byte (apart from the run timestamp in the two markdown headers), so
a committed report cannot quietly disagree with the code that produced it. The
rendered graph is compared too, which is only possible because layout ordering is
now fixed rather than taken from set iteration. The CI artifact
`catalog-validation-evidence` contains outputs from a disposable MySQL run.

## Implemented controls

| Area | Behaviour |
|---|---|
| Setup | Environment-based credentials and a fresh-install/upgrade helper |
| Schema reconciliation | Detect undocumented, deprecated and type-mismatched fields against the latest scan ID |
| Versioned definitions | Transactional history, row locking and a `data_dictionary` unique index that admits only one current version per field |
| Lineage maintenance | Canonical formula validation, atomic snapshot reconciliation, cycle rejection, updates, retirement and reactivation |
| Source identity | Each input relationship names a source dataset and field, its own transformation note, a mapping label and its own evidence and review date |
| Change evidence | Before/after lineage change records; dictionary insert/update triggers |
| Verification | Draft/confirmed status with evidence, reviewer and review date, at rule, relationship and field level |
| Issue queue | Ownership, next action, status and closure evidence; schema findings reopen if they recur; an open issue blocks confirming a rule on that field |
| Vendor records | Dataset scope, use restrictions, customer display, redistribution, review/renewal dates and open questions |
| Calculation QA | Independent loan/revenue reconciliation; missing/zero denominators remain blocked |
| Tests | 21 SQLite regression tests plus a disposable MySQL integration suite covering fresh setup, upgrade, concurrent edits, audit triggers, issue closure and report consistency |

Schema conformity and verification are separate: a correctly typed field can
still have an unconfirmed business definition. The scorecard's coverage metric
means **documented and type-aligned physical fields**, not verified vendor lineage.

## Fresh setup (MySQL 8.0.16+; Python 3.11 or 3.12)

Create a dedicated `credit_metadata_platform` database using your local MySQL
administrator. Never point this demonstration at production data. Bootstrap uses
DDL and triggers; use a database administrator or dedicated maintenance account.
The repeatable loader replaces only its local `raw_credit_datamart_sme_loans` table.

```bash
git clone https://github.com/Lifewitdata/credit-risk-metadata-lineage-platform.git
cd credit-risk-metadata-lineage-platform
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

Copy `config/.env.example` to `config/.env`; replace the example connection string
with your database credentials. Environment variables override this file.
Passwords containing URL-special characters must be URL-encoded in the connection
string. The real `.env` is ignored by Git.

```bash
python scripts/setup_database.py
python main.py
python -m pytest -q
```

Local default tests skip the disposable MySQL integration suite. Those tests create
fixtures and intentionally change metadata, and setup refuses a schema that already
exists; run them only in a fresh test database with `CATALOG_TEST_MYSQL=1`, as
GitHub Actions does. Everything the pipeline writes is UTF-8 with `\n` endings, so
generated reports compare equal on Windows and Linux; `.gitattributes` pins that.

## Upgrade an existing installation

Back up your existing database. MySQL DDL auto-commits, so a schema migration cannot
be rolled back as one transaction. The helper is resumable and rejects duplicate
current dictionary versions instead of choosing one arbitrarily.

```bash
python scripts/setup_database.py --upgrade
python main.py
```

The known legacy demo score definition is repaired only if it exactly matches the
shipped single-row fixture: its initial effective date becomes 2024-01-15, and the
illustrative new definition starts 2024-04-02. The repair is logged by the trigger.
An existing integer revenue definition is versioned to match the floating-point
physical column. Customized conflicting histories require manual review.

## Keep the catalog current

`data/raw/transformation_rules.csv` is an authoritative snapshot for this one
managed dataset. Every input row belonging to one rule must carry the same complete
formula and verification metadata, and that formula has to name every input the row
declares — this is what previously let the tenure discount disappear from the
pricing rule while its input relationship stayed in the graph. Removing a rule or
input relationship retires it from the current graph while retaining its history.
Blank snapshots are rejected.

Mapping labels decide what can be claimed. `local-data` is for internal sources and
those relationships are checked against the latest physical schema scan, so a
snapshot cannot reference a column that is not there. `illustrative` is what this
fixture ships for every external feed and the loader refuses to mark those rows
confirmed. `vendor-spec` is the promotion path: it requires a real specification
reference and review date, and a rule stays unconfirmable while any of its input
relationships is unconfirmed. See
[operations](docs/operations.md) for the review workflow.

Field definitions are seeded only when absent; subsequent changes use
`metadata/dictionary_service.py`. Vendor CSV rows are also initial seeds and do not
overwrite reviewed database records.

## Limits

This project demonstrates a small governed datamart, not an enterprise catalog or
commercial licensing system. External dataset/field identities, pricing grids,
source agreements and observation-date semantics still require evidence. The
April sample dates do not prove the illustrative score-change date. The supplied
ratio values are disputed, not silently repaired. Audit tables are useful history,
not tamper-proof storage against database administrators. Account authorization,
contract interpretation, and real vendor renewal decisions remain outside this demo.
