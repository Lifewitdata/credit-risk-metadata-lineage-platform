"""Scale behaviour of the null and freshness check on synthetic data.

The committed reports and the ten-row fixture say nothing about volume, so this
builds a wide dataset with the defects an extract actually arrives with -- a column
that never populated, a sparsely filled one, a duplicate key and a file that stopped
arriving -- and runs the check over 1,000 rows x 100 fields.

Assertions are on structure and on the number of statements issued, never on wall
clock: a timing threshold would turn CI into a report on the runner's load. The
measured duration is printed so docs/validation.md can quote a real number.

Not covered: the ETL and lineage loaders are written for the one governed dataset and
do not run against these tables, and nothing in this project tests uniqueness. The
duplicate key is seeded so the gap is visible, not because a check catches it.
"""

from datetime import date, timedelta
import time

import pandas as pd
import pytest
from sqlalchemy import create_engine, event

from conftest import DDL  # the same catalog tables the fast suite builds
from metadata.data_quality import evaluate

WIDE_FIELDS = 100
ROWS = 1000
AS_OF = date.today()
WIDE_PARTITION = AS_OF - timedelta(days=40)   # the file stopped arriving
NARROW_PARTITION = AS_OF - timedelta(days=1)  # arrived inside the Daily tolerance
COLUMNS = ['dataset_name', 'check', 'subject', 'value', 'expected', 'status']


@pytest.fixture(scope='module')
def scaled():
    """One synthetic catalog for the module: 1,000 rows x 100 fields plus a narrow
    control dataset that arrived on time."""
    engine = create_engine('sqlite://')
    with engine.begin() as conn:
        for statement in DDL:
            conn.exec_driver_sql(statement)
    build_dataset(engine, 2, 'synthetic_narrow_datamart', 'raw_synthetic_narrow',
                  ['event_id', 'snapshot_date'], NARROW_PARTITION, 50)
    build_dataset(engine, 3, 'synthetic_wide_datamart', 'raw_synthetic_wide',
                  wide_columns(), WIDE_PARTITION, ROWS)
    yield engine
    engine.dispose()


def wide_columns():
    return (['event_id', 'snapshot_date']
            + [f'metric_{index:03d}' for index in range(3, WIDE_FIELDS + 1)])


def build_dataset(engine, dataset_id, name, table, columns, partition, rows):
    """Register a dataset the way load_raw does: catalog row, schema scan, dictionary, table."""
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO datasets(dataset_id,dataset_name,physical_table_name,refresh_frequency)"
            f" VALUES ({dataset_id},'{name}','{table}','Daily')")
        for position, column in enumerate(columns, start=1):
            conn.exec_driver_sql(
                "INSERT INTO physical_schema_snapshot(dataset_id,column_name,physical_data_type,"
                "ordinal_position,scan_id,scan_timestamp)"
                f" VALUES ({dataset_id},'{column}','text',{position},'scan-{dataset_id}',"
                f"'{partition.isoformat()} 06:00:00')")
        conn.exec_driver_sql(
            "INSERT INTO data_dictionary(dataset_id,field_name,data_type,is_current)"
            f" VALUES ({dataset_id},'snapshot_date','date',1)")
        conn.exec_driver_sql('CREATE TABLE ' + table + ' ('
                             + ', '.join(f'{column} TEXT' for column in columns) + ')')

        index = {column: position for position, column in enumerate(columns)}
        records = []
        for row in range(rows):
            record = [f'{name}-{row:06d}' if column == 'event_id' else partition.isoformat()
                      for column in columns]
            if 'metric_003' in index:
                record[index['metric_003']] = None      # a column that never arrived
            if 'metric_004' in index and row % 20 == 0:
                record[index['metric_004']] = None      # roughly 5% missing
            records.append(record)
        records[7][index['event_id']] = records[6][index['event_id']]   # duplicate key
        placeholders = ', '.join('?' * len(columns))
        conn.exec_driver_sql(f'INSERT INTO {table} VALUES ({placeholders})',
                                [tuple(record) for record in records])


def run_check(engine):
    """Evaluate the catalog and record every statement it issues, plus the elapsed time."""
    seen = []

    @event.listens_for(engine, 'before_cursor_execute')
    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    try:
        with engine.connect() as conn:
            started = time.perf_counter()
            frame = pd.DataFrame(evaluate(conn), columns=COLUMNS)
            elapsed = time.perf_counter() - started
    finally:
        event.remove(engine, 'before_cursor_execute', record)
    return frame, seen, elapsed


def test_wide_table_is_one_aggregate_query_and_reads_current(scaled):
    """100 fields must not cost 100 queries: the null pass issues one SELECT with a
    SUM per column, so the statement count tracks datasets and documented date
    fields, never the width of a table."""
    engine = scaled
    frame, statements, elapsed = run_check(engine)
    assert len(statements) == 1 + 2 * 4      # registry + per dataset: scan, aggregate, dictionary, MAX
    aggregates = [s for s in statements if 'SUM(' in s and 'raw_synthetic_wide' in s]
    assert len(aggregates) == 1
    assert len(frame) == (WIDE_FIELDS + 1) + (2 + 1)
    print(f"\n[scale] {ROWS} rows x {WIDE_FIELDS} fields over 2 datasets "
          f"in {elapsed * 1000:.0f} ms")


def test_seeded_defects_are_reported_and_the_late_file_is_stale(scaled):
    engine = scaled
    frame, _, _ = run_check(engine)
    wide = frame[frame.dataset_name == 'synthetic_wide_datamart']
    assert set(wide[wide.status == 'review'].subject) == {'metric_003', 'metric_004'}
    assert wide[wide.subject == 'metric_003'].value.item() == ROWS
    assert wide[wide.subject == 'metric_004'].value.item() == 50
    freshness = wide[wide.check == 'freshness']
    assert freshness.status.item() == 'stale'
    assert freshness.expected.item() == '<= 2 days'
    assert freshness.value.item() == WIDE_PARTITION.isoformat()
    narrow = frame[(frame.dataset_name == 'synthetic_narrow_datamart')
                   & (frame.check == 'freshness')]
    assert narrow.status.item() == 'current'   # same cadence, arrived yesterday


def test_every_other_field_in_the_wide_table_is_clean(scaled):
    engine = scaled
    frame, _, _ = run_check(engine)
    wide = frame[frame.check == 'null_count']
    assert wide[wide.status == 'ok'].shape[0] == (WIDE_FIELDS - 2) + 2


def test_the_duplicate_key_is_visible_but_untested(scaled):
    """The honest gap: no uniqueness assertion exists anywhere in this project."""
    engine = scaled
    with engine.connect() as conn:
        duplicated = conn.exec_driver_sql(
            'SELECT COUNT(*) FROM (SELECT event_id FROM raw_synthetic_wide'
            ' GROUP BY event_id HAVING COUNT(*) > 1)').scalar()
    assert duplicated == 1
