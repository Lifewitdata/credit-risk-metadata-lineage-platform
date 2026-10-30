"""
run_validation.py
------------------
Step 3: Execute the dictionary <-> physical schema reconciliation and
produce a human-readable governance report.

This does NOT duplicate data-quality checks (nulls, drift, duplicates --
that's a separate project). It answers a metadata-governance question only:
"does our documentation match reality?"

Findings are also written to metadata_audit_log so there is a permanent,
queryable record of what governance issues were known on which date --
this is what lets a bank answer "when did we become aware of this gap?"
during an audit.
"""

import sys
import json
from pathlib import Path

import pandas as pd
from sqlalchemy import text

sys.path.append(str(Path(__file__).resolve().parent.parent.parent))
from config.db_config import get_engine  # noqa: E402


STATUS_SEVERITY = {
    "UNDOCUMENTED": "HIGH",
    "TYPE_MISMATCH": "MEDIUM",
    "DEPRECATED": "LOW",
    "DOCUMENTED": "INFO",
}


def fetch_reconciliation(engine) -> pd.DataFrame:
    query = """
        SELECT r.dataset_id, d.dataset_name, r.field_name,
               r.documented_data_type, r.actual_data_type, r.governance_status
        FROM vw_data_dictionary_reconciliation r
        JOIN datasets d ON d.dataset_id = r.dataset_id
        ORDER BY d.dataset_name,
                 FIELD(r.governance_status, 'UNDOCUMENTED','TYPE_MISMATCH','DEPRECATED','DOCUMENTED'),
                 r.field_name
    """
    return pd.read_sql(text(query), engine)


def fetch_scorecard(engine) -> pd.DataFrame:
    return pd.read_sql(text("SELECT * FROM vw_governance_scorecard"), engine)


def log_findings(engine, findings: pd.DataFrame) -> None:
    """Persist every non-DOCUMENTED finding to the audit log so governance
    issues are traceable to the exact date they were first detected."""
    issues = findings[findings["governance_status"] != "DOCUMENTED"]
    if issues.empty:
        return

    with engine.begin() as conn:
        for _, row in issues.iterrows():
            # pd.read_sql returns SQL NULL as float NaN in mixed-type columns.
            # json.dumps(NaN) produces the literal token `NaN`, which is not
            # valid JSON -- MySQL's JSON column type correctly rejects it.
            # Convert any NaN back to None before serializing.
            def n(v):
                return None if pd.isna(v) else v

            conn.execute(
                text(
                    """
                    INSERT INTO metadata_audit_log
                        (table_name, record_pk, action_type, new_value_json)
                    VALUES
                        ('vw_data_dictionary_reconciliation', :dataset_id, 'INSERT', :payload)
                    """
                ),
                {
                    "dataset_id": int(row["dataset_id"]),
                    "payload": json.dumps(
                        {
                            "field_name": n(row["field_name"]),
                            "governance_status": n(row["governance_status"]),
                            "documented_data_type": n(row["documented_data_type"]),
                            "actual_data_type": n(row["actual_data_type"]),
                            "severity": STATUS_SEVERITY[row["governance_status"]],
                        }
                    ),
                },
            )
    print(f"[audit log] recorded {len(issues)} governance finding(s)")


def print_report(reconciliation: pd.DataFrame, scorecard: pd.DataFrame) -> None:
    print("=" * 78)
    print("DATA DICTIONARY GOVERNANCE REPORT")
    print("=" * 78)

    for _, ds in scorecard.iterrows():
        print(f"\nDataset: {ds['dataset_name']}")
        print(f"  Documented/type-aligned : {ds['documentation_coverage_pct']}%"
              f"  ({int(ds['documented_count'])}/{int(ds['total_fields'])} fields)")
        print(f"  Undocumented           : {int(ds['undocumented_count'])}")
        print(f"  Deprecated              : {int(ds['deprecated_count'])}")
        print(f"  Type mismatches         : {int(ds['type_mismatch_count'])}")

    issues = reconciliation[reconciliation["governance_status"] != "DOCUMENTED"]
    if issues.empty:
        print("\nNo governance issues found.")
        return

    print("\nFindings requiring attention:")
    print("-" * 78)
    for _, row in issues.iterrows():
        severity = STATUS_SEVERITY[row["governance_status"]]
        documented = "-" if pd.isna(row["documented_data_type"]) else row["documented_data_type"]
        actual = "-" if pd.isna(row["actual_data_type"]) else row["actual_data_type"]
        print(f"  [{severity:6}] {row['governance_status']:15} "
              f"{row['field_name']:28} "
              f"documented={documented:10} "
              f"actual={actual}")
    print("-" * 78)


def run() -> pd.DataFrame:
    engine = get_engine()
    reconciliation = fetch_reconciliation(engine)
    scorecard = fetch_scorecard(engine)
    print_report(reconciliation, scorecard)
    log_findings(engine, reconciliation)
    return reconciliation


if __name__ == "__main__":
    run()
