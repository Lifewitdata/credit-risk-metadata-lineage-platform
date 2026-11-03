"""In-memory SQLite fixture mirroring the MySQL tables the catalog writes to.

Only the objects the loaders touch are created here; MySQL-specific behaviour
(row locking, generated-column uniqueness, audit triggers) is covered by
tests/test_mysql.py against a disposable database.
"""
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

GOVERNED = 'credit_datamart_sme_loans'
# The columns the ETL scans out of the physical table, so lineage claims can be
# checked against a schema snapshot exactly as they are on MySQL.
PHYSICAL_COLUMNS = ['loan_id', 'borrower_id', 'snapshot_date', 'industry_sector',
                    'years_in_business', 'annual_revenue_gbp', 'commercial_credit_score',
                    'loan_amount_gbp', 'interest_rate', 'dti_ratio', 'payment_status']

DDL = [
    "CREATE TABLE datasets(dataset_id INTEGER PRIMARY KEY,dataset_name TEXT UNIQUE,"
    "physical_table_name TEXT,description TEXT,business_owner TEXT)",
    "CREATE TABLE transformation_rules(rule_id INTEGER PRIMARY KEY,rule_name TEXT UNIQUE,"
    "target_dataset_id INTEGER,target_field TEXT,transformation_type TEXT,transformation_logic TEXT,"
    "business_justification TEXT,created_by TEXT,is_active INTEGER,verification_status TEXT,"
    "evidence_ref TEXT,verified_at TEXT,verified_by TEXT)",
    "CREATE TABLE lineage_nodes(node_id INTEGER PRIMARY KEY,dataset_id INTEGER,field_name TEXT,"
    "node_type TEXT,is_active INTEGER,UNIQUE(dataset_id,field_name))",
    "CREATE TABLE lineage_edges(edge_id INTEGER PRIMARY KEY,source_node_id INTEGER,target_node_id INTEGER,"
    "rule_id INTEGER,is_active INTEGER,source_mapping_label TEXT,source_transformation TEXT,"
    "source_verification_status TEXT,source_evidence_ref TEXT,source_verified_at TEXT,"
    "UNIQUE(source_node_id,target_node_id))",
    "CREATE TABLE lineage_change_log(change_id INTEGER PRIMARY KEY,entity_type TEXT,entity_id INTEGER,"
    "action TEXT,old_json TEXT,new_json TEXT,changed_by TEXT)",
    "CREATE TABLE data_dictionary(dictionary_id INTEGER PRIMARY KEY,dataset_id INTEGER,field_name TEXT,"
    "description TEXT,data_type TEXT,valid_range TEXT,known_issues TEXT,business_owner TEXT,"
    "valid_from DATE,valid_to DATE,is_current INTEGER)",
    "CREATE UNIQUE INDEX uq_current ON data_dictionary(dataset_id,field_name) WHERE is_current=1",
    "CREATE TABLE physical_schema_snapshot(snapshot_id INTEGER PRIMARY KEY,dataset_id INTEGER,"
    "column_name TEXT,physical_data_type TEXT,ordinal_position INTEGER,scan_id TEXT,"
    "scan_timestamp TEXT)",
    # Same 'latest scan' resolution as sql/validation/02_dictionary_reconciliation.sql.
    "CREATE VIEW vw_latest_schema_snapshot AS SELECT s.dataset_id,s.column_name,s.physical_data_type,"
    "s.ordinal_position FROM physical_schema_snapshot s JOIN physical_schema_snapshot latest"
    " ON latest.snapshot_id=(SELECT MAX(x.snapshot_id) FROM physical_schema_snapshot x"
    " WHERE x.dataset_id=s.dataset_id) WHERE s.scan_id=latest.scan_id",
    "CREATE TABLE governance_issues(issue_key TEXT PRIMARY KEY,dataset_id INTEGER,field_name TEXT,"
    "category TEXT,priority TEXT,status TEXT,owner TEXT,description TEXT,next_action TEXT,"
    "evidence_ref TEXT,closure_evidence TEXT)",
]


@pytest.fixture
def db():
    engine = create_engine('sqlite://')
    with engine.begin() as c:
        for ddl in DDL:
            c.exec_driver_sql(ddl)
        c.exec_driver_sql("INSERT INTO datasets(dataset_id,dataset_name) VALUES (1,'credit_datamart_sme_loans')")
        for position, column in enumerate(PHYSICAL_COLUMNS, start=1):
            c.exec_driver_sql(
                "INSERT INTO physical_schema_snapshot(dataset_id,column_name,physical_data_type,"
                "ordinal_position,scan_id,scan_timestamp) VALUES "
                f"(1,'{column}','text',{position},'seed-scan','2026-10-31 00:00:00')")
    yield engine
    engine.dispose()
