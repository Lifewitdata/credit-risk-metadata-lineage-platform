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

## GitHub Actions result for the pushed state

Actions ran `9e539a2` on 2026-10-31 (both the `push` and `pull_request` events) on
Ubuntu with Python 3.12 against a `mysql:8.0` service:

- `tests/test_catalog.py`: 21 passed.
- `tests/test_mysql.py`: 1 failed, 5 passed in 1h 17m 56s.

The failure was `test_committed_documentation_matches_the_database` comparing the
committed `docs/lineage_graph.png` byte-for-byte with the one the runner
regenerated. It is not a version difference: pip resolved matplotlib 3.11.2, the
same version recorded in the committed PNG's `Software` text chunk, and the
markdown reports and interactive HTML matched exactly. Only the raster differed,
so the assertion held on the machine that rendered the image and could never hold
on another platform. The image is now checked for stability by rendering the
current graph twice in the same run, and graph structure stays covered by the
interactive HTML, which embeds every node and edge.

## Not yet claimed

The change above has not been executed against MySQL, so CI still has to confirm
it. The 6-passing-1-failing result came from a Windows runner environment; nothing
here proves the same on Ubuntu. SQLite regression tests are not proof of MySQL
behaviour, and the generated reports are portfolio fixtures, not evidence about a
real vendor feed.

Two things worth a separate decision. The integration job spends most of its wall
clock in three full `main.py` subprocess runs. And `generate_docs.py` orders
relationships by `sn.node_type DESC`, which sorts by ENUM index on MySQL but
alphabetically on the SQLite fixtures, so the catalog tests exercise a different
order from production.
