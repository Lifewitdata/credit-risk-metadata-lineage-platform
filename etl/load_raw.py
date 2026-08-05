"""
load_raw.py
-----------
Step 2 ETL: onboard a new data asset into the metadata platform.

Pipeline stages (idempotent -- safe to re-run):
    1. register_source_systems  -> populate source_systems from the dictionary CSV
    2. register_dataset         -> populate datasets with the SME loans datamart
    3. load_data_dictionary     -> insert the CSV as the *first* SCD2 version
    4. load_physical_table      -> load the raw CSV into a real MySQL table
    5. snapshot_physical_schema -> read INFORMATION_SCHEMA.COLUMNS and log it

Design notes:
- Every insert step checks for an existing row first (SELECT before INSERT)
  so the script can be re-run without creating duplicates -- this matters
  because in production this ETL is scheduled, not run by hand once.
- Step 4 loads the CSV EXACTLY as-is, including borrower_id and
  snapshot_date, which are NOT in the data dictionary. This is deliberate:
  we want the physical table to reflect ground truth so Step 3's
  validation can detect the gap. We are not "fixing" the mismatch here.
"""

import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

sys.path.append(str(Path(__file__).resolve().parent.parent))
from config.db_config import get_engine  # noqa: E402

RAW_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
DICTIONARY_CSV = RAW_DATA_DIR / "data_dictionary_control.csv"
DATAMART_CSV = RAW_DATA_DIR / "credit_datamart_sme_loans.csv"

DATASET_NAME = "credit_datamart_sme_loans"
PHYSICAL_TABLE_NAME = "raw_credit_datamart_sme_loans"

# Maps each source_system value found in the dictionary CSV to a governance
# system_type. In a real onboarding this classification comes from the
# source system registry / vendor contract, not guesswork -- captured here
# explicitly so the mapping is auditable and easy to extend.
SOURCE_SYSTEM_TYPE_MAP = {
    "Salesforce": "Internal",
    "Companies House": "ThirdParty",
    "Xero/Quickbooks/Bureau": "Bureau",
    "Experian Commercial": "Bureau",
    "Internal Core Banking": "Internal",
    "Internal Pricing Engine": "Internal",
    "Internal Calculated": "Internal",
}


def register_source_systems(engine: Engine) -> None:
    """Insert any source system referenced in the dictionary that we
    haven't seen before."""
    dict_df = pd.read_csv(DICTIONARY_CSV)
    unique_systems = dict_df["source_system"].dropna().unique()

    with engine.begin() as conn:
        for system_name in unique_systems:
            exists = conn.execute(
                text("SELECT 1 FROM source_systems WHERE system_name = :name"),
                {"name": system_name},
            ).fetchone()
            if exists:
                continue

            system_type = SOURCE_SYSTEM_TYPE_MAP.get(system_name, "ThirdParty")
            conn.execute(
                text(
                    """
                    INSERT INTO source_systems (system_name, system_type, owner_team)
                    VALUES (:name, :type, :owner)
                    """
                ),
                {"name": system_name, "type": system_type, "owner": "Data Platform Team"},
            )
            print(f"[source_systems] registered: {system_name} ({system_type})")


def register_dataset(engine: Engine) -> int:
    """Insert the datamart into the dataset registry if not already present.
    Returns the dataset_id either way."""
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT dataset_id FROM datasets WHERE dataset_name = :name"),
            {"name": DATASET_NAME},
        ).fetchone()
        if row:
            print(f"[datasets] already registered: {DATASET_NAME} (id={row[0]})")
            return row[0]

        # For this dataset, the "source system" of record for the composite
        # datamart is Internal Core Banking -- individual fields may trace to
        # bureaus, but the datamart itself is engineered internally.
        source_id = conn.execute(
            text("SELECT source_system_id FROM source_systems WHERE system_name = 'Internal Core Banking'")
        ).fetchone()[0]

        result = conn.execute(
            text(
                """
                INSERT INTO datasets
                    (dataset_name, physical_table_name, description,
                     source_system_id, refresh_frequency, business_owner)
                VALUES
                    (:name, :table, :desc, :source_id, :freq, :owner)
                """
            ),
            {
                "name": DATASET_NAME,
                "table": PHYSICAL_TABLE_NAME,
                "desc": "SME loans credit risk datamart: origination and bureau "
                        "attributes joined with loan performance status.",
                "source_id": source_id,
                "freq": "Monthly",
                "owner": "Credit Risk Analytics",
            },
        )
        dataset_id = result.lastrowid
        print(f"[datasets] registered: {DATASET_NAME} (id={dataset_id})")
        return dataset_id


def load_data_dictionary(engine: Engine, dataset_id: int) -> None:
    """Insert the dictionary CSV as the first SCD2 version of each field.

    valid_from is taken from the CSV's own `last_updated` column rather than
    today's date -- this preserves the real business timeline (e.g. the
    commercial_credit_score redefinition on 2024-04-02) instead of collapsing
    it to "whenever this script happened to run".
    """
    dict_df = pd.read_csv(DICTIONARY_CSV)

    def clean(value):
        """pandas represents blank CSV cells as float NaN, which PyMySQL
        cannot bind to a text column -- convert any NaN/blank to a real
        Python None so MySQL stores a proper NULL."""
        if pd.isna(value):
            return None
        if isinstance(value, str) and value.strip().lower() in ("", "none", "nan"):
            return None
        return value

    with engine.begin() as conn:
        for _, row in dict_df.iterrows():
            exists = conn.execute(
                text(
                    """
                    SELECT 1 FROM data_dictionary
                    WHERE dataset_id = :dataset_id AND field_name = :field_name
                      AND is_current = 1
                    """
                ),
                {"dataset_id": dataset_id, "field_name": row["field_name"]},
            ).fetchone()
            if exists:
                continue

            conn.execute(
                text(
                    """
                    INSERT INTO data_dictionary
                        (dataset_id, field_name, description, data_type,
                         valid_range, known_issues, business_owner,
                         valid_from, valid_to, is_current)
                    VALUES
                        (:dataset_id, :field_name, :description, :data_type,
                         :valid_range, :known_issues, :business_owner,
                         :valid_from, NULL, 1)
                    """
                ),
                {
                    "dataset_id": dataset_id,
                    "field_name": clean(row["field_name"]),
                    "description": clean(row["description"]),
                    "data_type": clean(row["data_type"]),
                    "valid_range": clean(row["valid_range"]),
                    "known_issues": clean(row["known_issues"]),
                    "business_owner": "Credit Risk Analytics",
                    "valid_from": clean(row["last_updated"]),
                },
            )
        print(f"[data_dictionary] loaded {len(dict_df)} field definitions "
              f"for dataset_id={dataset_id}")


def load_physical_table(engine: Engine) -> None:
    """Load the raw datamart CSV into MySQL exactly as-is (including
    undocumented columns) using pandas.to_sql via the shared engine."""
    datamart_df = pd.read_csv(DATAMART_CSV, parse_dates=["snapshot_date"])

    datamart_df.to_sql(
        name=PHYSICAL_TABLE_NAME,
        con=engine,
        if_exists="replace",  # full refresh load -- see docstring note below
        index=False,
        dtype=None,
    )
    print(f"[{PHYSICAL_TABLE_NAME}] loaded {len(datamart_df)} rows into MySQL")
    # NOTE: if_exists="replace" is fine for a monthly full-snapshot datamart
    # like this one. For an append-only fact table you would instead load
    # into a staging table and MERGE/UPSERT on a business key -- flag this
    # as a design decision worth calling out in an interview.


def snapshot_physical_schema(engine: Engine, dataset_id: int) -> None:
    """Read INFORMATION_SCHEMA.COLUMNS for the physical table and record a
    timestamped snapshot. This is the ground-truth input for Step 3's
    documented / undocumented / deprecated / new-column reconciliation."""
    with engine.begin() as conn:
        db_name = conn.execute(text("SELECT DATABASE()")).scalar()

        columns = conn.execute(
            text(
                """
                SELECT COLUMN_NAME, DATA_TYPE, ORDINAL_POSITION
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = :table
                ORDER BY ORDINAL_POSITION
                """
            ),
            {"schema": db_name, "table": PHYSICAL_TABLE_NAME},
        ).fetchall()

        for col_name, data_type, position in columns:
            conn.execute(
                text(
                    """
                    INSERT INTO physical_schema_snapshot
                        (dataset_id, column_name, physical_data_type, ordinal_position)
                    VALUES
                        (:dataset_id, :col, :dtype, :pos)
                    """
                ),
                {"dataset_id": dataset_id, "col": col_name, "dtype": data_type, "pos": position},
            )
        print(f"[physical_schema_snapshot] recorded {len(columns)} columns "
              f"for {PHYSICAL_TABLE_NAME}")


def run() -> None:
    engine = get_engine(echo=False)
    register_source_systems(engine)
    dataset_id = register_dataset(engine)
    load_data_dictionary(engine, dataset_id)
    load_physical_table(engine)
    snapshot_physical_schema(engine, dataset_id)
    print("\nETL complete.")


if __name__ == "__main__":
    run()
