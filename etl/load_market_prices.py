"""
load_market_prices.py
----------------------
Onboard a second, genuinely external dataset -- daily index closing prices --
through the same catalog steps the loan datamart uses.

Run by hand:  python -m etl.load_market_prices

Two things this deliberately does not do.

* It is not a step in main.py. A pipeline that reaches the network can fail for
  reasons the catalog has nothing to do with, and CI would then report on a third
  party's uptime. Fetch it on the cadence you actually want, separately.
* It commits no market data. The number is the index owner's, redistributed by a
  public source, and the redistribution right is recorded as unconfirmed in
  vendor_usage. So the download lands in gitignored reports/local/ and in a
  database -- never in the repository. See docs/support_runbook.md.

The source is FRED's keyless CSV download endpoint for one series. It was
reachable on 2026-11-03; that is a fact about that day, not a service guarantee.
"""

import csv
import io
import json
import math
import sys
import urllib.request
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import Date, text
from sqlalchemy.engine import Engine

sys.path.append(str(Path(__file__).resolve().parent.parent))
from config.db_config import get_engine  # noqa: E402
from etl.load_raw import snapshot_physical_schema  # noqa: E402
from scripts.setup_database import statements  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

SERIES_ID = "SP500"
SOURCE_URL = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={SERIES_ID}"
SOURCE_NAME = "FRED (Federal Reserve Bank of St. Louis)"
DATASET_NAME = "market_close_prices_daily"
PHYSICAL_TABLE_NAME = "raw_market_close_prices_daily"
BUSINESS_OWNER = "Credit Risk Analytics"
PROVENANCE_PATH = ROOT / "reports" / "local" / "market_fetch.json"
FRESHNESS_SQL = ROOT / "sql" / "validation" / "03_market_freshness.sql"

# (field_name, description, data_type, valid_range, known_issues)
# The three provenance columns are documented because the reconciliation counts an
# undocumented physical column as a governance issue -- which is the right answer
# for a column nobody can explain, and the wrong thing to leave in the queue when
# the loader itself knows what the column is for.
DICTIONARY = [
    ("trading_day",
     "Trading day the closing level belongs to, as dated by the source.",
     "date", "ISO date",
     "Weekends, exchange holidays and days the source had no value for are absent "
     "rows, not zero rows. A gap is not a price of zero."),
    ("close_value",
     "Closing level of the index for that trading day, as published by the source.",
     "float", "Index level, positive",
     "An index level, not a currency amount and not a return. Stored as the source "
     "printed it; nothing here rounds or recomputes it."),
    ("source_series",
     "Identifier of the series the row was downloaded under.",
     "string", "One value for the whole dataset",
     "Constant. Kept so a row cannot be read without saying which series it came "
     "from if a second series is ever loaded into the same database."),
    ("retrieved_at",
     "When this loader fetched the file.",
     "datetime", "Local time of the fetch",
     "NOT the observation time. The source publishes with its own lag, so this is "
     "always later than the trading day. Freshness is measured on trading_day; "
     "measuring it here would hide every publication lag instead of reporting it."),
]


def fetch(url=SOURCE_URL, timeout=30):
    request = urllib.request.Request(
        url, headers={"User-Agent": "credit-risk-metadata-catalog/1.0 (portfolio project)"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8-sig", errors="replace")
    if body.lstrip().startswith("<"):
        raise RuntimeError(
            f"{url} answered with a page rather than the CSV download -- the endpoint "
            "moved or is behind an interstitial. See docs/support_runbook.md")
    return body


def parse(body):
    """(trading_day, close_value) pairs. A missing observation stays NULL.

    A row that is neither blank nor date-plus-value is a refusal rather than a
    skip: silently dropping unreadable lines is how a loader ends up with a file
    that looks complete and is not.
    """
    reader = csv.reader(io.StringIO(body))
    header = next(reader, None)
    if not header or len(header) != 2 or [column.strip().lower() for column in header] != ["observation_date",
                                                                           SERIES_ID.lower()]:
        raise ValueError(f"unexpected header {header!r}; the source changed shape")
    rows = []
    seen = set()
    for line in reader:
        if not line or not any(column.strip() for column in line):
            continue                                   # blank separator line
        if len(line) != 2:
            raise ValueError(f"unexpected row {line!r}; the source changed shape")
        value = line[1].strip()
        day = date.fromisoformat(line[0].strip())
        if day in seen:
            raise ValueError(f"duplicate trading date {day}; refusing ambiguous source data")
        seen.add(day)
        close = None if value in ("", ".", "NA") else float(value)
        if close is not None and (not math.isfinite(close) or close <= 0):
            raise ValueError(f"invalid close_value on {day}: expected a finite positive index level")
        rows.append((day, close))
    if not rows:
        raise ValueError("the download contained no observations")
    if not any(value is not None for _, value in rows):
        raise ValueError("the download contained no priced observations; retaining the previous dataset")
    # Provenance first/last dates must not depend on the provider's row order.
    return sorted(rows, key=lambda row: row[0])


def to_frame(rows, retrieved_at):
    return pd.DataFrame({
        "trading_day": [pd.Timestamp(day) for day, _ in rows],
        "close_value": pd.Series([value for _, value in rows], dtype="float64"),
        "source_series": [SERIES_ID] * len(rows),
        "retrieved_at": pd.Series([retrieved_at] * len(rows), dtype="datetime64[ns]"),
    })


def register(engine, row_count):
    """Register source, dataset, field definitions and the vendor row.

    Returns the dataset id. The schema scan happens after this transaction
    commits: snapshot_physical_schema opens its own connection, and an
    uncommitted dataset_id would fail the snapshot's foreign key.
    """
    with engine.begin() as conn:
        source = conn.execute(
            text("SELECT source_system_id FROM source_systems WHERE system_name=:name"),
            {"name": SOURCE_NAME}).fetchone()
        if source:
            source_id = source[0]
        else:
            source_id = conn.execute(
                text("INSERT INTO source_systems (system_name, system_type, owner_team) "
                     "VALUES (:name, 'ThirdParty', :owner)"),
                {"name": SOURCE_NAME, "owner": "Data Platform Team"}).lastrowid

        dataset = conn.execute(
            text("SELECT dataset_id FROM datasets WHERE dataset_name=:name"),
            {"name": DATASET_NAME}).fetchone()
        if dataset:
            dataset_id = dataset[0]
        else:
            dataset_id = conn.execute(
                text("INSERT INTO datasets (dataset_name, physical_table_name, description, "
                     "source_system_id, refresh_frequency, business_owner) VALUES "
                     "(:name, :table, :desc, :source_id, 'Daily', :owner)"),
                {"name": DATASET_NAME, "table": PHYSICAL_TABLE_NAME,
                 "desc": f"Daily closing index level for series {SERIES_ID}, downloaded from "
                         "a public statistical release. No downstream rule consumes it.",
                 "source_id": source_id, "owner": BUSINESS_OWNER}).lastrowid

        for field_name, description, data_type, valid_range, known_issues in DICTIONARY:
            if conn.execute(text("SELECT 1 FROM data_dictionary WHERE dataset_id=:dataset_id "
                                 "AND field_name=:field_name AND is_current=1"),
                            {"dataset_id": dataset_id, "field_name": field_name}).fetchone():
                continue
            conn.execute(
                text("INSERT INTO data_dictionary (dataset_id, field_name, description, "
                     "data_type, valid_range, known_issues, business_owner, valid_from, "
                     "valid_to, is_current) VALUES (:dataset_id, :field_name, :description, "
                     ":data_type, :valid_range, :known_issues, :business_owner, :valid_from, "
                     "NULL, 1)"),
                {"dataset_id": dataset_id, "field_name": field_name,
                 "description": description, "data_type": data_type,
                 "valid_range": valid_range, "known_issues": known_issues,
                 "business_owner": BUSINESS_OWNER, "valid_from": date.today()})

        # The vendor row is the point, not a formality: a technical ability to
        # download something says nothing about the right to store or publish it.
        if not conn.execute(text("SELECT 1 FROM vendor_usage WHERE vendor_name=:name"),
                            {"name": SOURCE_NAME}).fetchone():
            conn.execute(
                text("INSERT INTO vendor_usage (vendor_name, dataset_scope, permitted_use, "
                     "restrictions, customer_display, redistribution, agreement_ref, terms_url, "
                     "reviewed_at, renewal_date, owner, open_question) VALUES "
                     "(:vendor_name, :dataset_scope, :permitted_use, :restrictions, "
                     "'unconfirmed', 'unconfirmed', NULL, :terms_url, NULL, NULL, :owner, "
                     ":open_question)"),
                {"vendor_name": SOURCE_NAME,
                 "dataset_scope": f"Daily closing level of series {SERIES_ID} only.",
                 "permitted_use": "Local analysis in this demonstration catalog. Nothing "
                                  "derived from it is shipped.",
                 "restrictions": "Terms of use not reviewed. Do not publish these values "
                                 "from this repository or from any report built on it.",
                 "terms_url": "https://fred.stlouisfed.org/",
                 "owner": "Data Platform Team",
                 "open_question": "Does the index owner's licence, as passed through by this "
                                  "source, allow storing the closing levels in a public "
                                  "repository and republishing them in a portfolio report? "
                                  "Attribution wording to be confirmed by the agreement owner."})

    snapshot_physical_schema(engine, dataset_id, PHYSICAL_TABLE_NAME)
    print(f"[{DATASET_NAME}] registered with {row_count} observations")
    return dataset_id


def report_freshness(conn):
    """Run the hand-run diagnostic query so the file has one real executor."""
    for statement in statements(FRESHNESS_SQL.read_text(encoding="utf-8")):
        row = conn.exec_driver_sql(statement).mappings().first()
    return row


def run():
    engine = get_engine()
    body = fetch()
    rows = parse(body)
    retrieved_at = datetime.now()
    to_frame(rows, retrieved_at).to_sql(
        PHYSICAL_TABLE_NAME, engine, if_exists="replace", index=False,
        dtype={"trading_day": Date()})
    register(engine, len(rows))

    days = [day for day, _ in rows]
    missing = sum(1 for _, value in rows if value is None)
    PROVENANCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROVENANCE_PATH.write_text(json.dumps({
        "url": SOURCE_URL, "retrieved_at": retrieved_at.isoformat(timespec="seconds"),
        "rows": len(rows), "first_trading_day": days[0].isoformat(),
        "last_trading_day": days[-1].isoformat(), "null_close_values": missing,
    }, indent=2) + "\n", encoding="utf-8")
    with engine.connect() as conn:
        verdict = report_freshness(conn)
    print(f"[freshness] {verdict['verdict']}: {verdict['trading_days_since_last_price']} "
          f"trading days since the last price on {verdict['last_priced_day']} "
          f"(tolerance {verdict['tolerance_trading_days']}); last row "
          f"{verdict['last_row']}, {verdict['holiday_rows_in_window']} holiday rows and "
          f"{verdict['weekdays_without_row_in_window']} weekdays with no row in the window")
    print(f"[provenance] {PROVENANCE_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    run()
