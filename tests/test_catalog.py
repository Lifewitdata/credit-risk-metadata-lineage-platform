from datetime import date
import pandas as pd
import pytest
from sqlalchemy import text
from lineage.load_transformation_rules import load,validate_rules,RULES_CSV
from lineage.build_graph import build_graph,upstream_lineage
from metadata.dictionary_service import update_field_definition,FieldDefinition,get_definition_as_of
from metadata.calculation_validation import validate_ratio
from metadata.governance import set_verification


def scalar(db,sql):
    with db.connect() as c:return c.execute(text(sql)).scalar_one()


def test_repeat_run_no_duplicate_or_spurious_audit(db):
    load(db);n=scalar(db,'SELECT COUNT(*) FROM lineage_change_log');load(db)
    assert scalar(db,'SELECT COUNT(*) FROM lineage_change_log')==n
    assert scalar(db,'SELECT COUNT(*) FROM transformation_rules WHERE is_active=1')==11
    assert scalar(db,'SELECT COUNT(*) FROM lineage_edges WHERE is_active=1')==14


def test_revision_retirement_reactivation_preserve_history(db,tmp_path):
    load(db)
    frame=pd.read_csv(RULES_CSV,keep_default_na=False)
    frame.loc[frame.rule_name=='price_interest_rate','transformation_logic']='Revised illustrative pricing formula'
    path=tmp_path/'rules.csv';frame.to_csv(path,index=False);load(db,path)
    assert scalar(db,"SELECT transformation_logic FROM transformation_rules WHERE rule_name='price_interest_rate'")=='Revised illustrative pricing formula'
    frame=frame[frame.rule_name!='price_interest_rate'];frame.to_csv(path,index=False);load(db,path)
    assert scalar(db,"SELECT is_active FROM transformation_rules WHERE rule_name='price_interest_rate'")==0
    assert all(d['field_name']!='interest_rate' for _,d in build_graph(db).nodes(data=True))
    assert scalar(db,"SELECT COUNT(*) FROM lineage_change_log WHERE action='retire'")>=4
    load(db)
    assert scalar(db,"SELECT is_active FROM transformation_rules WHERE rule_name='price_interest_rate'")==1
    assert scalar(db,'SELECT COUNT(*) FROM transformation_rules')==11


def test_invalid_snapshot_does_not_mutate(db,tmp_path):
    load(db);before=scalar(db,'SELECT COUNT(*) FROM lineage_change_log')
    f=pd.read_csv(RULES_CSV,keep_default_na=False)
    f.loc[f.index[-1],'transformation_logic']='Conflicting formula'
    path=tmp_path/'bad.csv';f.to_csv(path,index=False)
    with pytest.raises(ValueError,match='Conflicting'):load(db,path)
    assert scalar(db,'SELECT COUNT(*) FROM lineage_change_log')==before


def test_cycles_rejected():
    f=pd.read_csv(RULES_CSV,keep_default_na=False)
    mask=(f.rule_name=='calc_dti_ratio')&(f.source_ref=='loan_amount_gbp')
    f.loc[mask,['source_ref','source_field']]='dti_ratio'
    with pytest.raises(ValueError,match='cycle'):validate_rules(f)


def test_blank_snapshot_rejected():
    with pytest.raises(ValueError):validate_rules(pd.read_csv(RULES_CSV).iloc[:0])


def test_unknown_source_cannot_be_confirmed():
    f=pd.read_csv(RULES_CSV,keep_default_na=False)
    f['verification_status']='confirmed';f['verified_by']='reviewer';f['verified_at']='2026-01-01'
    with pytest.raises(ValueError,match='unknown source'):validate_rules(f)


def test_external_identity_is_explicitly_unverified(db):
    load(db)
    upstream=upstream_lineage(build_graph(db),'credit_datamart_sme_loans','interest_rate')
    assert any(x['field_name']=='years_in_business' for x in upstream)
    assert any('UNVERIFIED' in x['field_name'] for x in upstream)
    assert 'tenure_discount' in scalar(db,"SELECT transformation_logic FROM transformation_rules WHERE rule_name='price_interest_rate'")


def seed_definition(db):
    with db.begin() as c:
        c.execute(text("""INSERT INTO data_dictionary(dataset_id,field_name,description,data_type,valid_range,known_issues,business_owner,valid_from,is_current)
            VALUES (1,'score','Old score','integer','0-100','old issue','owner','2024-01-15',1)"""))


def test_scd_history_boundaries_and_explicit_clear(db):
    seed_definition(db)
    update_field_definition(db,1,'score',FieldDefinition(description='New score',known_issues=''),date(2024,4,2),'reviewer')
    assert get_definition_as_of(db,1,'score',date(2024,4,1))['description']=='Old score'
    assert get_definition_as_of(db,1,'score',date(2024,4,2))['description']=='New score'
    assert scalar(db,"SELECT known_issues FROM data_dictionary WHERE is_current=1")==''
    assert scalar(db,'SELECT COUNT(*) FROM data_dictionary WHERE is_current=1')==1
    with pytest.raises(ValueError):update_field_definition(db,1,'score',FieldDefinition(description='Bad'),date(2024,4,2),'reviewer')
    assert scalar(db,'SELECT COUNT(*) FROM data_dictionary')==2


def test_ratio_checks_preserve_input_and_block_bad_denominators():
    f=pd.DataFrame({'loan_id':['a','b','c','d'],'loan_amount_gbp':[20,20,20,20],'annual_revenue_gbp':[100,100,0,float('nan')],'dti_ratio':[.2,.3,.5,.6]})
    before=f.copy(deep=True);out=validate_ratio(f)
    assert out.status.tolist()==['pass','mismatch','blocked_input','blocked_input']
    pd.testing.assert_frame_equal(f,before)


def test_confirmation_requires_evidence(db):
    with pytest.raises(ValueError,match='evidence'):set_verification(db,1,'score','confirmed')


def test_import_entrypoint_and_configuration(monkeypatch):
    import main
    from config.db_config import get_engine
    monkeypatch.delenv('METADATA_DATABASE_URL',raising=False)
    with pytest.raises(RuntimeError,match='METADATA_DATABASE_URL'):get_engine()


def test_sql_script_parser_handles_triggers():
    from scripts.setup_database import statements,ROOT
    commands=list(statements((ROOT/'sql/ddl/03_audit_triggers.sql').read_text()))
    assert len([x for x in commands if x.startswith('CREATE TRIGGER')])==2
    assert not any('DELIMITER' in x for x in commands)
