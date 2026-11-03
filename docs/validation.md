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

## Data quality on the same fixture

`metadata/data_quality.py` reports one null on the shipped ten rows
(`annual_revenue_gbp` on `L-1003`, one of the three records whose ratio is blocked)
and nothing else missing, so 10 of the 11 scanned columns come back `ok`. The other
two blocked records hold a literal `0.0`, which a null count does not flag: that is
the reconciliation's job, and the two checks are deliberately separate. Its freshness check
compares the latest `snapshot_date`, 2024-04-01, with the run date: the dataset
declares a `Monthly` cadence with a 33-day tolerance, so any run in 2026 reports
that column `stale`. That is the intended reading of a 2024 snapshot, and one
reason the output is written to gitignored `reports/local/` instead of being a
committed file the suite compares byte-for-byte.

## GitHub Actions results

All runs are Ubuntu, Python 3.12, against a `mysql:8.0` service container.

| Date | Commit | Runs | `test_catalog.py` | `test_mysql.py` |
|---|---|---|---|---|
| 2026-10-31 | `9e539a2` | 41177043907 (push), 41177046485 (PR) | 21 passed | 1 failed, 5 passed in 1h 17m 56s |
| 2026-11-03 | `488be1e` | 41594927365 (push), 41594932903 (PR) | 21 passed in 2.35s | 7 passed in 8.08s |
| 2026-11-03 | `d28d247` | 41595336044 (push), 41595340827 (PR) | success | success |
| 2026-11-03 | `e6c0dbd` (merged `main`) | 41595577622 (push) | success | success |
| 2026-11-03 | `b168cc6` | 41598277880 (push) | not reached | not reached |
| 2026-11-03 | `a8274fc` | 41598586313 (push) | success in 3s | success in 7s |

`b168cc6` never ran a test. Rewriting `requirements-dev.txt` to add `black` dropped
the `-r requirements.txt` include, pip reported success having installed only
pytest, and the job died collecting `conftest.py` with `ModuleNotFoundError: No
module named 'sqlalchemy'`. A requirements file that pip accepts is not a
requirements file that pip resolved, so the include line is now covered by the
install step's own duration: 17s on `a8274fc`, 3s on the broken commit.

`a8274fc` is the head of the branch that adds `metadata/data_quality.py`. Both
suites exit 0 in CI, and the `engine` fixture runs `main.py`, whose step `3/6`
is that check, so the null and freshness pass has executed against MySQL 8.0 on
every push since. The test counts on that commit are 25 collected in
`tests/test_catalog.py` and 7 in `tests/test_mysql.py`; the job log lines were not
read, so the CI column records the step result and duration rather than a
transcribed count.

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

## Fresh clone, as someone who did not write it

On 2026-11-03, from merged `main` (`e6c0dbd`) into a clean directory with a new
`.venv` on Python 3.14.6:

- `python -m pip install -r requirements-dev.txt` succeeded; no manual
  `cryptography` install and no system package was needed.
- `python -m pytest -q`: 21 passed, 7 skipped. The skipped set is
  `tests/test_mysql.py`, opt-in behind `CATALOG_TEST_MYSQL`. A clone of the branch
  that adds the data-quality check collects four more, so the same command reports
  25 passed and 7 skipped; that count has not been re-run from a hand-followed
  fresh clone, only from this working tree.
- `python scripts/setup_database.py` with no configuration exits with the
  instruction to copy `config/.env.example`.
- The same command with `config/.env.example` copied to `config/.env` reaches a real
  MySQL handshake and returns `OperationalError (1045) Access denied for user
  'metadata_platform_user'@'localhost'`, which is what a placeholder password should
  produce.

Not executed: `setup_database.py` against a live server, `main.py`, and
`CATALOG_TEST_MYSQL=1 pytest`. No MySQL server was reachable from this environment, so
the documented fresh-install path is proven up to the connection, not end to end.
Actions covers part of that gap, because the `engine` fixture creates an empty schema,
runs `setup()` and then `main.py`, but that is a service container rather than the
README being followed by hand.

## Not yet claimed

SQLite regression tests are not proof of MySQL behaviour, and the generated reports
are portfolio fixtures, not evidence about a real vendor feed.

Worth a separate decision: `generate_docs.py` orders relationships by
`sn.node_type DESC`, which sorts by ENUM index on MySQL but alphabetically on the
SQLite fixtures, so the catalog tests exercise a different order from production.
