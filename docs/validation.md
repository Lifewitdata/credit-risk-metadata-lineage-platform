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

## GitHub Actions results

All runs are Ubuntu, Python 3.12, against a `mysql:8.0` service container.

| Date | Commit | Runs | `test_catalog.py` | `test_mysql.py` |
|---|---|---|---|---|
| 2026-10-31 | `9e539a2` | 41177043907 (push), 41177046485 (PR) | 21 passed | 1 failed, 5 passed in 1h 17m 56s |
| 2026-11-03 | `488be1e` | 41594927365 (push), 41594932903 (PR) | 21 passed | **7 passed in 8.08s** |

The 2026-10-31 failure was `test_committed_documentation_matches_the_database`
comparing the committed `docs/lineage_graph.png` byte-for-byte with the one the
runner regenerated. It was not a version difference: pip resolved matplotlib
3.11.2, the version recorded in the committed PNG's `Software` text chunk. The
markdown reports matched; only the raster differed, so the assertion could hold
only on the Windows machine that rendered the image. Almost all of the 78 minutes
was pytest building a readable diff of two ~260 KB byte strings, not the pipeline.

Since `488be1e` the image is checked by rendering the current graph twice in the
same run and comparing both with this run's output. Graph structure stays covered
by `docs/lineage_graph_interactive.html`, which embeds every node and edge and is
still compared with the committed file. That comparison sat after the PNG in the
old loop and so never executed on 2026-10-31; it passes on 2026-11-03.

## Not yet claimed

SQLite regression tests are not proof of MySQL behaviour, and the generated reports
are portfolio fixtures, not evidence about a real vendor feed.

Worth a separate decision: `generate_docs.py` orders relationships by
`sn.node_type DESC`, which sorts by ENUM index on MySQL but alphabetically on the
SQLite fixtures, so the catalog tests exercise a different order from production.
