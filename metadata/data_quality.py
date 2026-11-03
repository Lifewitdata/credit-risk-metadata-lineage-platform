"""Null and freshness checks driven by the catalog rather than a column list.

Checked columns come from the latest physical schema scan and date columns are
chosen by their documented type, so a new field is covered without editing this
file. Results go to `reports/local/`, which is gitignored: staleness is measured
against the run date, so unlike the markdown reports this cannot be a committed
artifact that the integration suite compares byte-for-byte.

A missing or unmapped refresh cadence is reported as `unknown` instead of being
guessed at. Nothing here decides whether NULLs are acceptable for a field; that
is the data owner's call, and this only surfaces the count.
"""

import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from config.db_config import get_engine

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Declared cadence to tolerated age, with one interval of slack. Keys are
# compared lower-cased because refresh_frequency is free text in the registry.
MAX_AGE_DAYS = {"daily": 2, "weekly": 9, "monthly": 33}

COLUMNS = ["dataset_name", "check", "subject", "value", "expected", "status"]


def _quote(name):
    """Escape an identifier that came from INFORMATION_SCHEMA, not from a user."""
    if not IDENTIFIER.match(str(name)):
        raise ValueError(f"Unusable identifier from the schema scan: {name!r}")
    return f"`{name}`"


def _scan_columns(conn, dataset_id):
    return [
        row["column_name"]
        for row in conn.execute(
            text(
                "SELECT column_name FROM vw_latest_schema_snapshot"
                " WHERE dataset_id = :dataset_id ORDER BY ordinal_position"
            ),
            {"dataset_id": dataset_id},
        ).mappings()
    ]


def _date_columns(conn, dataset_id, physical_columns):
    """Documented fields whose declared type is a date, and that still exist."""
    rows = conn.execute(
        text(
            "SELECT field_name, data_type FROM data_dictionary"
            " WHERE dataset_id = :dataset_id AND is_current = 1"
            " ORDER BY field_name"
        ),
        {"dataset_id": dataset_id},
    ).mappings()
    return [
        row["field_name"]
        for row in rows
        if str(row["data_type"] or "").strip().lower() in ("date", "datetime")
        and row["field_name"] in physical_columns
    ]


def _null_counts(conn, table, columns):
    projections = ", ".join(f"SUM({_quote(c)} IS NULL)" for c in columns)
    row = conn.exec_driver_sql(
        f"SELECT COUNT(*) AS n, {projections} FROM {_quote(table)}"
    ).fetchone()
    return int(row[0]), [int(value or 0) for value in row[1:]]


def _as_date(value):
    """MySQL returns a date object for a DATE column, SQLite the stored text."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _freshness(conn, table, column, cadence, as_of):
    raw = conn.exec_driver_sql(
        f"SELECT MAX({_quote(column)}) FROM {_quote(table)}"
    ).scalar()
    if raw is None:
        return "no dated row", "review", "a non-null maximum"
    try:
        latest = _as_date(raw)
    except ValueError:
        return str(raw), "review", "an ISO date"
    age = (as_of - latest).days
    if age < 0:
        return str(latest), "review", "not dated after the run"
    tolerated = MAX_AGE_DAYS.get(str(cadence or "").strip().lower())
    if tolerated is None:
        return str(latest), "unknown", f"cadence {cadence or 'unset'}"
    return (
        str(latest),
        ("stale" if age > tolerated else "current"),
        f"<= {tolerated} days",
    )


def evaluate(conn, as_of=None):
    """Run every check the catalog describes. Returns finding dicts, writes nothing."""
    as_of = as_of or date.today()
    findings = []
    datasets = (
        conn.execute(
            text(
                "SELECT dataset_id, dataset_name, physical_table_name, refresh_frequency"
                " FROM datasets ORDER BY dataset_id"
            )
        )
        .mappings()
        .all()
    )

    for dataset in datasets:
        name = dataset["dataset_name"]
        table = dataset["physical_table_name"]
        cadence = dataset["refresh_frequency"]
        columns = _scan_columns(conn, dataset["dataset_id"])
        if not columns:
            findings.append(
                {
                    "dataset_name": name,
                    "check": "schema_scan",
                    "subject": table,
                    "value": 0,
                    "expected": "at least one column",
                    "status": "review",
                }
            )
            continue

        _, nulls = _null_counts(conn, table, columns)
        for column, null_count in zip(columns, nulls):
            findings.append(
                {
                    "dataset_name": name,
                    "check": "null_count",
                    "subject": column,
                    "value": null_count,
                    "expected": "counted, not judged",
                    "status": "review" if null_count else "ok",
                }
            )

        for column in _date_columns(conn, dataset["dataset_id"], set(columns)):
            value, status, expected = _freshness(conn, table, column, cadence, as_of)
            findings.append(
                {
                    "dataset_name": name,
                    "check": "freshness",
                    "subject": column,
                    "value": value,
                    "expected": expected,
                    "status": status,
                }
            )

    return findings


def run(engine, as_of=None):
    with engine.connect() as conn:
        frame = pd.DataFrame(evaluate(conn, as_of), columns=COLUMNS)
    frame = frame.sort_values(["dataset_name", "check", "subject"]).reset_index(
        drop=True
    )

    target = ROOT / "reports" / "local"
    target.mkdir(parents=True, exist_ok=True)
    frame.to_csv(
        target / "data_quality.csv", index=False, encoding="utf-8", lineterminator="\n"
    )
    return frame


if __name__ == "__main__":
    result = run(get_engine())
    print(result.to_string(index=False))
    print(
        f"[data_quality] {len(result)} checks written to reports/local/data_quality.csv"
    )
