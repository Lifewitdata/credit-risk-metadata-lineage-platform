"""Add governance controls without deleting existing data (MySQL 8+).
Back up the database first. DDL auto-commits in MySQL. Safe to resume.
"""
from sqlalchemy import inspect, text
from config.db_config import get_engine

ADDITIONS = {
    'data_dictionary': {
        'current_guard': 'TINYINT GENERATED ALWAYS AS (CASE WHEN is_current=1 THEN 1 ELSE NULL END) STORED',
    },
    'transformation_rules': {
        'is_active': 'TINYINT NOT NULL DEFAULT 1',
        'verification_status': "VARCHAR(16) NOT NULL DEFAULT 'draft'",
        'evidence_ref': 'TEXT', 'verified_at': 'DATE', 'verified_by': 'VARCHAR(150)',
    },
    'lineage_nodes': {'is_active': 'TINYINT NOT NULL DEFAULT 1'},
    'lineage_edges': {'is_active': 'TINYINT NOT NULL DEFAULT 1'},
    'physical_schema_snapshot': {'scan_id': 'VARCHAR(36)'},
}
TABLES = [
"""CREATE TABLE IF NOT EXISTS lineage_change_log (
 change_id BIGINT AUTO_INCREMENT PRIMARY KEY, entity_type VARCHAR(30) NOT NULL,
 entity_id INT NOT NULL, action VARCHAR(16) NOT NULL, old_json JSON, new_json JSON,
 changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, changed_by VARCHAR(150) NOT NULL
)""",
"""CREATE TABLE IF NOT EXISTS field_verification (
 dataset_id INT NOT NULL, field_name VARCHAR(100) NOT NULL,
 status VARCHAR(16) NOT NULL DEFAULT 'draft', evidence_ref TEXT,
 verified_at DATE, verified_by VARCHAR(150),
 PRIMARY KEY(dataset_id,field_name), FOREIGN KEY(dataset_id) REFERENCES datasets(dataset_id),
 CHECK(status IN ('draft','confirmed')),
 CHECK(status <> 'confirmed' OR (evidence_ref IS NOT NULL AND CHAR_LENGTH(TRIM(evidence_ref))>0
 AND verified_at IS NOT NULL AND verified_by IS NOT NULL AND CHAR_LENGTH(TRIM(verified_by))>0))
)""",
"""CREATE TABLE IF NOT EXISTS governance_issues (
 issue_key VARCHAR(180) PRIMARY KEY, dataset_id INT NOT NULL, field_name VARCHAR(100),
 category VARCHAR(40) NOT NULL, priority VARCHAR(10) NOT NULL,
 status VARCHAR(16) NOT NULL DEFAULT 'open', owner VARCHAR(100) NOT NULL,
 description TEXT NOT NULL, next_action TEXT NOT NULL, evidence_ref TEXT,
 closure_evidence TEXT, opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, resolved_at DATETIME,
 FOREIGN KEY(dataset_id) REFERENCES datasets(dataset_id),
 CHECK(status IN ('open','in_progress','resolved')),
 CHECK(status <> 'resolved' OR (closure_evidence IS NOT NULL AND CHAR_LENGTH(TRIM(closure_evidence))>0 AND resolved_at IS NOT NULL))
)""",
"""CREATE TABLE IF NOT EXISTS vendor_usage (
 vendor_name VARCHAR(150) PRIMARY KEY, dataset_scope TEXT NOT NULL,
 permitted_use TEXT NOT NULL, restrictions TEXT NOT NULL,
 customer_display VARCHAR(16) NOT NULL DEFAULT 'unconfirmed',
 redistribution VARCHAR(16) NOT NULL DEFAULT 'unconfirmed',
 agreement_ref TEXT, terms_url TEXT, reviewed_at DATE, renewal_date DATE,
 owner VARCHAR(100) NOT NULL, open_question TEXT NOT NULL,
 CHECK(customer_display IN ('unconfirmed','permitted','prohibited')),
 CHECK(redistribution IN ('unconfirmed','permitted','prohibited'))
)""",
]

def migrate(engine):
    with engine.begin() as conn:
        duplicate = conn.execute(text("SELECT dataset_id,field_name FROM data_dictionary WHERE is_current=1 GROUP BY dataset_id,field_name HAVING COUNT(*)>1")).first()
        if duplicate:
            raise ValueError('Duplicate current definitions; review them before migration.')
        for table, additions in ADDITIONS.items():
            columns = {c['name'] for c in inspect(conn).get_columns(table)}
            for name, ddl in additions.items():
                if name not in columns:
                    conn.exec_driver_sql(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}')
        indexes = {i['name'] for i in inspect(conn).get_indexes('data_dictionary')}
        if 'uq_dictionary_current' not in indexes:
            conn.exec_driver_sql('CREATE UNIQUE INDEX uq_dictionary_current ON data_dictionary(dataset_id,field_name,current_guard)')
        for ddl in TABLES:
            conn.exec_driver_sql(ddl)

if __name__ == '__main__':
    migrate(get_engine())
