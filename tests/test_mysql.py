"""Opt-in tests against a disposable MySQL schema; never a user production database."""
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import pytest
from sqlalchemy import text

pytestmark=pytest.mark.skipif(os.getenv('CATALOG_TEST_MYSQL')!='1',reason='Disposable MySQL integration environment not enabled')


def test_mysql_fresh_pipeline_upgrade_and_concurrent_versions():
    from config.db_config import get_engine
    from scripts.setup_database import setup
    from metadata.dictionary_service import update_field_definition,FieldDefinition
    from metadata.governance import sync_schema_issues
    engine=get_engine()
    setup(engine)
    subprocess.run([sys.executable,'main.py'],check=True)
    setup(engine,upgrade=True)
    subprocess.run([sys.executable,'main.py'],check=True)
    with engine.connect() as c:
        assert c.execute(text('SELECT COUNT(*) FROM data_dictionary WHERE is_current=1')).scalar_one()==11
        assert c.execute(text("SELECT COUNT(*) FROM field_verification WHERE status='draft'")).scalar_one()==11
        assert c.execute(text("SELECT COUNT(*) FROM vendor_usage WHERE customer_display='unconfirmed'")).scalar_one()==6
        ds=c.execute(text("SELECT dataset_id FROM datasets WHERE dataset_name='credit_datamart_sme_loans'")).scalar_one()
        assert c.execute(text('SELECT documentation_coverage_pct FROM vw_governance_scorecard')).scalar_one()==100
    def revise(label):
        try:
            update_field_definition(engine,ds,'loan_id',FieldDefinition(description=label),date(2026,10,30),label)
            return 'ok'
        except ValueError:return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(revise,['a','b']))==['conflict','ok']
    # Reconciliation creates a queue entry; fixing metadata closes it with evidence.
    with engine.begin() as c:c.execute(text("UPDATE data_dictionary SET data_type='integer' WHERE field_name='loan_id' AND is_current=1"))
    sync_schema_issues(engine)
    with engine.connect() as c:assert c.execute(text("SELECT COUNT(*) FROM governance_issues WHERE category='schema' AND status='open'")).scalar_one()==1
    with engine.begin() as c:c.execute(text("UPDATE data_dictionary SET data_type='string' WHERE field_name='loan_id' AND is_current=1"))
    sync_schema_issues(engine)
    with engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM governance_issues WHERE category='schema' AND status='resolved' AND closure_evidence IS NOT NULL")).scalar_one()==1
        assert c.execute(text("SELECT COUNT(*) FROM metadata_audit_log WHERE action_type='CLOSE'")).scalar_one()>=2
    engine.dispose()
