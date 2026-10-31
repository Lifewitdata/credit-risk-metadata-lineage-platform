# Validation record

## What has been executed locally

On 2026-10-31, on Windows with Python 3.11.16 against a throwaway MySQL 8.0.46
instance (separate data directory, port 3317, no user database involved):

- `python scripts/setup_database.py` then `python main.py` completed from an empty
  schema, and `--upgrade` followed by a second `main.py` run also completed.
- `python -m pytest -q tests/test_catalog.py`: 21 passed on SQLite fixtures.
- `CATALOG_TEST_MYSQL=1 python -m pytest -q tests/test_mysql.py`: 6 passed against
  a freshly created empty MySQL schema.

Those checks cover idempotent rule loads, changed formulas, relationship retirement
and reactivation, conflicting or duplicate input rows, cycles, a formula that omits a
declared input, snapshot fields absent from the latest schema scan, mapping-label and
relationship-evidence rules, date boundaries, explicit text clearing, concurrent
definition edits, audit triggers, issue reopening and closure, calculation exceptions,
configuration import, SQL script parsing, and byte equality between the committed
reports, both rendered graphs, the calculation CSV and what the pipeline regenerates.

Three real defects surfaced during those runs and are now fixed. The report writer
used the platform codec, so generating `docs/lineage_report.md` on Windows crashed on
non-ASCII text after already truncating the file to zero bytes; writers are now
explicit UTF-8 and `.gitattributes` pins `\n` endings, which also stopped committed
CSVs looking modified purely because of CRLF. Second, `docs/lineage_graph.png` was
rewritten with different bytes on every run: node order inside a layout layer came
from `nx.utils.groups`, which returns sets, so the image followed the interpreter's
hash seed instead of the data, and the graph queries had no `ORDER BY`. Layers are
sorted now and the PNG and HTML are byte-stable across runs and hash seeds. Third,
the committed reports had been generated from a database the integration suite had
already edited, so they showed a demo revision of `loan_id`; they are regenerated
from a fresh schema and one pipeline run.

## Calculation reconciliation

The reconciliation ran against the committed ten-row fixture: seven mismatches, three
blocked inputs, zero passes. This is a finding to investigate, not a failed attempt to
make the data look clean.

## Not yet claimed

GitHub Actions has not run the commits above; the workflow changed only by dropping a
now-redundant manual `cryptography` install. Check the actual run before saying CI is
green for this state. SQLite regression tests are not proof of MySQL behaviour, and
the generated reports are portfolio fixtures, not evidence about a real vendor feed.
