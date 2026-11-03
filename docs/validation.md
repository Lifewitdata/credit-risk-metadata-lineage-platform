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

## Scale behaviour on synthetic volume

The ten-row fixture says nothing about volume, so `tests/test_scale.py` builds a
synthetic catalog in SQLite: two registered datasets, 1,000 rows x 100 fields in the
wide one, seeded with the defects an extract actually arrives with — a column that
never populated, a column roughly 5% missing, a duplicate `event_id`, and a partition
that stopped arriving 40 days ago. The narrow control dataset holds the same declared
`Daily` cadence and arrived yesterday.

The measured run is `[scale] 1000 rows x 100 fields over 2 datasets in 21 ms` on the
local Python 3.14.6 machine, averaged over the module's four evaluations. That number
is printed for the record and is not asserted: a wall-clock threshold would make CI a
report on runner load. What the test does assert is structural —

- the whole pass issues 9 statements: one registry read, then per dataset a schema-scan
  read, one aggregate, a dictionary read and a `MAX` for its single documented date
  field. 100 columns therefore cost 100 `SUM` expressions inside one query, not 100
  queries;
- exactly one aggregate statement touches the wide table;
- the never-populated and sparsely populated columns are the only `review` null
  findings, the 40-day-old partition is `stale` against `<= 2 weekdays`, and the
  one-day-old control partition is `current` under the same cadence — so `stale` is
  the file, not the tolerance table.

Two gaps are asserted as gaps rather than papered over. The duplicate `event_id` is
visible to a hand-written query but no check in this project looks for duplicates, and
nothing enforces uniqueness in the DDL. And the ETL, lineage and documentation loaders
are written for the one governed dataset; they were not run against these synthetic
tables, so "the pipeline scales" is not a claim this test supports. Only the
data-quality pass is.

The step runs in CI as its own `python -m pytest -q tests/test_scale.py` line, SQLite
only, so it costs seconds and does not need the MySQL service.

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

Not executed *from this clone*: `setup_database.py` against a live server, `main.py`,
and `CATALOG_TEST_MYSQL=1 pytest`. No MySQL server was reachable in the clean-clone
environment, so the documented fresh-install path is proven there only up to the
connection. The next section covers the same three commands against a throwaway
MySQL 8.0.46 from the working tree — that closes the behaviour gap, not the
"someone who did not write this followed the README" gap. Actions covers part of that
too, because the `engine` fixture creates an empty schema, runs `setup()` and then
`main.py`, but that is a service container rather than the README being followed by
hand.

## Executed against a real MySQL 8.0 server, by hand

On 2026-11-03 the documented path was run locally rather than only in a service
container: a throwaway MySQL 8.0.46 in its own data directory on port 3317, bound to
127.0.0.1, with no user database involved.

- `python scripts/setup_database.py` then `python main.py` against an empty schema:
  completed, 6 steps, pipeline in 1.90s.
- `CATALOG_TEST_MYSQL=1 python -m pytest -q tests/test_mysql.py` against a **second,
  empty** database on the same server: 7 passed in 32.25s. The suite refuses a schema
  that already exists, so it needs its own database.
- `python -m etl.load_market_prices`: downloaded 2,607 daily observations, registered
  `market_close_prices_daily` with 4 documented fields, recorded a schema scan, and
  wrote gitignored `reports/local/market_fetch.json`. The reconciliation reports that
  dataset 4/4 `DOCUMENTED` at 100% coverage, and `python -m metadata.data_quality`
  returns 26 checks for the two datasets, including `close_value` with 96 nulls.
- `python main.py` again afterwards: only the auto-generated timestamps in the two
  markdown reports moved. The reports are written per dataset for
  `credit_datamart_sme_loans`, so a second registered dataset does not leak into the
  committed documents — which is what makes it safe to keep the market loader out of
  the pipeline while still exercising the catalog with two datasets.

`sql/validation/03_market_freshness.sql` was run through the `mysql` client against
all three states it can report, using deliberately damaged copies of the loaded data
(re-restored by re-fetching):

| State | Output |
|---|---|
| as downloaded | `current`, `calendar_days_since_last_row = 4`, `trading_days_since_last_price = 2` |
| last close blanked and one weekday deleted | `stale`, `last_priced_day` one day earlier, `holiday_rows_in_window = 1`, `weekdays_without_row_in_window = 1` |
| table truncated | `no_data`, all window counts 0 |

Writing the query caught a real bug: `bounds.last_day` was `MIN(trading_day)` instead
of `MAX`, so the hole count silently measured the wrong end of the series and always
returned 0. It was found by running it, not by reading it, which is the reason this
section exists. The MySQL client run also fixed the earlier assumption that the
source drops holiday rows: it keeps them with a blank `close_value`, so 96 of 2,607
rows are exchange holidays and the verdict has to follow the last *priced* day.

The two `vendor_usage` and scorecard assertions in `tests/test_mysql.py` are now
scoped to the governed dataset by name, because they previously counted rows and so
would fail on any database where a second dataset or vendor had been onboarded —
including one where someone followed this runbook.

## Not yet claimed

SQLite regression tests are not proof of MySQL behaviour, and the generated reports
are portfolio fixtures, not evidence about a real vendor feed.

Worth a separate decision: `generate_docs.py` orders relationships by
`sn.node_type DESC`, which sorts by ENUM index on MySQL but alphabetically on the
SQLite fixtures, so the catalog tests exercise a different order from production.
