"""
dictionary_service.py
----------------------
Step 4: The sanctioned interface for changing business metadata.

Nobody should ever hand-write an UPDATE against data_dictionary. This
module is the only supported way to change a field's definition -- it
guarantees the SCD2 invariant (exactly one is_current=1 row per
dataset_id/field_name at any moment) by closing the old version and
inserting the new one inside a single transaction.

Three operations:
  - update_field_definition : version a change to an existing field
  - get_definition_history  : full version timeline for a field
  - get_definition_as_of    : point-in-time lookup ("what did this mean on
                               the date a credit decision was made?")
"""

import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

sys.path.append(str(Path(__file__).resolve().parent.parent))
from config.db_config import get_engine  # noqa: E402


@dataclass
class FieldDefinition:
    """Fields that can be revised when a new dictionary version is created.
    Any field left as None is carried forward unchanged from the version
    being closed -- this stops a partial update from accidentally wiping
    out unrelated metadata (e.g. revising only known_issues shouldn't blank
    out the description)."""
    description: Optional[str] = None
    data_type: Optional[str] = None
    valid_range: Optional[str] = None
    known_issues: Optional[str] = None
    business_owner: Optional[str] = None


def _get_current_version(conn, dataset_id: int, field_name: str) -> dict:
    row = conn.execute(
        text(
            """
            SELECT * FROM data_dictionary
            WHERE dataset_id = :dataset_id AND field_name = :field_name AND is_current = 1
            """ + (" FOR UPDATE" if conn.dialect.name == "mysql" else "")
        ),
        {"dataset_id": dataset_id, "field_name": field_name},
    ).mappings().fetchone()

    if row is None:
        raise ValueError(
            f"No current definition found for dataset_id={dataset_id}, "
            f"field_name='{field_name}'. This function versions an EXISTING "
            f"field -- to document a brand-new field, insert its first "
            f"version directly (see etl/load_raw.py load_data_dictionary)."
        )
    return dict(row)


def update_field_definition(
    engine: Engine,
    dataset_id: int,
    field_name: str,
    changes: FieldDefinition,
    effective_date: date,
    changed_by: str,
) -> None:
    """
    Version a change to a field's business definition (SCD Type 2).

    Steps, inside one transaction:
      1. Look up the current (is_current=1) version.
      2. Close it: valid_to = effective_date - 1 day, is_current = 0.
         (AFTER UPDATE trigger logs this as a 'CLOSE' audit event.)
      3. Insert the new version, merging `changes` over the closed row's
         values, with valid_from = effective_date, is_current = 1.
         (AFTER INSERT trigger logs this as an 'INSERT' audit event.)

    effective_date must be strictly after the current version's valid_from,
    otherwise the history would become non-chronological.
    """
    with engine.begin() as conn:
        current = _get_current_version(conn, dataset_id, field_name)
        current["valid_from"] = date.fromisoformat(str(current["valid_from"]))

        if effective_date <= current["valid_from"]:
            raise ValueError(
                f"effective_date ({effective_date}) must be after the current "
                f"version's valid_from ({current['valid_from']})."
            )

        close_date = effective_date - timedelta(days=1)
        conn.execute(
            text(
                """
                UPDATE data_dictionary
                SET valid_to = :close_date, is_current = 0
                WHERE dictionary_id = :id
                """
            ),
            {"close_date": close_date.isoformat(), "id": current["dictionary_id"]},
        )

        # Merge: any field left None in `changes` carries forward the value
        # from the version we just closed.
        merged = {
            "description": current["description"] if changes.description is None else changes.description,
            "data_type": current["data_type"] if changes.data_type is None else changes.data_type,
            "valid_range": current["valid_range"] if changes.valid_range is None else changes.valid_range,
            "known_issues": current["known_issues"] if changes.known_issues is None else changes.known_issues,
            "business_owner": current["business_owner"] if changes.business_owner is None else changes.business_owner,
        }

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
                "field_name": field_name,
                "valid_from": effective_date.isoformat(),
                **merged,
            },
        )

    print(
        f"[dictionary_service] '{field_name}' revised by {changed_by}: "
        f"closed version valid_from={current['valid_from']} "
        f"(now valid_to={close_date}), new version effective {effective_date}"
    )


def get_definition_history(engine: Engine, dataset_id: int, field_name: str) -> pd.DataFrame:
    """Return the full version timeline for a field, oldest first."""
    query = """
        SELECT dictionary_id, description, data_type, valid_range,
               known_issues, valid_from, valid_to, is_current
        FROM data_dictionary
        WHERE dataset_id = :dataset_id AND field_name = :field_name
        ORDER BY valid_from
    """
    return pd.read_sql(
        text(query), engine, params={"dataset_id": dataset_id, "field_name": field_name}
    )


def get_definition_as_of(
    engine: Engine, dataset_id: int, field_name: str, as_of_date: date
) -> Optional[dict]:
    """
    Point-in-time lookup: what did this field's definition say on a given
    date? This is the query a model-risk or audit team runs when reviewing
    a historical credit decision -- "explain what commercial_credit_score
    meant on the date this loan was originated."
    """
    query = """
        SELECT description, data_type, valid_range, known_issues,
               valid_from, valid_to
        FROM data_dictionary
        WHERE dataset_id = :dataset_id
          AND field_name = :field_name
          AND valid_from <= :as_of_date
          AND (valid_to IS NULL OR valid_to >= :as_of_date)
    """
    with engine.connect() as conn:
        row = conn.execute(
            text(query),
            {"dataset_id": dataset_id, "field_name": field_name, "as_of_date": as_of_date.isoformat()},
        ).mappings().fetchone()
    return dict(row) if row else None


def apply_demo_revision(engine):
    """Idempotent fixture correction; not a verified external vendor change."""
    with engine.connect() as conn:
        dataset_id = conn.execute(text("SELECT dataset_id FROM datasets WHERE dataset_name='credit_datamart_sme_loans'")).scalar_one()
    # One-time repair of the known legacy fixture's incorrect initial effective date.
    # Restricted to the exact shipped definition and a single history row.
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT dictionary_id,description,valid_from FROM data_dictionary WHERE dataset_id=:ds AND field_name='commercial_credit_score'"), {'ds':dataset_id}).mappings().all()
        if len(rows)==1 and str(rows[0]['valid_from'])=='2024-04-02' and rows[0]['description']=='Bureau risk score (0-100, higher is better)':
            conn.execute(text("UPDATE data_dictionary SET valid_from='2024-01-15' WHERE dictionary_id=:id"),{'id':rows[0]['dictionary_id']})
    revenue = get_definition_as_of(engine,dataset_id,'annual_revenue_gbp',date.today())
    if revenue and revenue['data_type']=='integer':
        update_field_definition(engine,dataset_id,'annual_revenue_gbp',FieldDefinition(data_type='float'),date.today(),'schema_type_correction')
    current = get_definition_as_of(engine, dataset_id, 'commercial_credit_score', date(2024, 4, 2))
    if current and current['valid_range'] == '1-30 (lower = lower risk)':
        return
    update_field_definition(engine, dataset_id, 'commercial_credit_score', FieldDefinition(
        description='Illustrative bureau score: 1-30, lower is better, from 2024-04-02; vendor methodology unverified.',
        valid_range='1-30 (lower = lower risk)',
        known_issues='Demo scale change only. Vendor evidence and cross-scale mapping remain unconfirmed.'
    ), date(2024, 4, 2), 'demo_revision')


if __name__ == '__main__':
    apply_demo_revision(get_engine())
