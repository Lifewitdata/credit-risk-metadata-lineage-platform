import sys
from pathlib import Path
import pytest
from sqlalchemy import create_engine
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

@pytest.fixture
def db():
    engine=create_engine('sqlite://')
    with engine.begin() as c:
        for ddl in [
            'CREATE TABLE datasets(dataset_id INTEGER PRIMARY KEY,dataset_name TEXT UNIQUE,physical_table_name TEXT,description TEXT,business_owner TEXT)',
            "CREATE TABLE transformation_rules(rule_id INTEGER PRIMARY KEY,rule_name TEXT UNIQUE,target_dataset_id INTEGER,target_field TEXT,transformation_type TEXT,transformation_logic TEXT,business_justification TEXT,created_by TEXT,is_active INTEGER,verification_status TEXT,evidence_ref TEXT,verified_at TEXT,verified_by TEXT)",
            'CREATE TABLE lineage_nodes(node_id INTEGER PRIMARY KEY,dataset_id INTEGER,field_name TEXT,node_type TEXT,is_active INTEGER,UNIQUE(dataset_id,field_name))',
            'CREATE TABLE lineage_edges(edge_id INTEGER PRIMARY KEY,source_node_id INTEGER,target_node_id INTEGER,rule_id INTEGER,is_active INTEGER,UNIQUE(source_node_id,target_node_id))',
            'CREATE TABLE lineage_change_log(change_id INTEGER PRIMARY KEY,entity_type TEXT,entity_id INTEGER,action TEXT,old_json TEXT,new_json TEXT,changed_by TEXT)',
            'CREATE TABLE data_dictionary(dictionary_id INTEGER PRIMARY KEY,dataset_id INTEGER,field_name TEXT,description TEXT,data_type TEXT,valid_range TEXT,known_issues TEXT,business_owner TEXT,valid_from DATE,valid_to DATE,is_current INTEGER)',
            'CREATE UNIQUE INDEX uq_current ON data_dictionary(dataset_id,field_name) WHERE is_current=1',
            "INSERT INTO datasets(dataset_id,dataset_name) VALUES (1,'credit_datamart_sme_loans')",
        ]:c.exec_driver_sql(ddl)
    yield engine
    engine.dispose()
