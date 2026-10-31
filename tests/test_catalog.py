from datetime import date, timedelta
import pandas as pd
import pytest
from sqlalchemy import text

from lineage.load_transformation_rules import load, validate_rules, RULES_CSV
from lineage.build_graph import build_graph, upstream_lineage
from metadata.dictionary_service import update_field_definition, FieldDefinition, get_definition_as_of
from metadata.calculation_validation import validate_ratio
from metadata.governance import set_verification

GOVERNED = 'credit_datamart_sme_loans'


def scalar(db, sql):
    with db.connect() as c:
        return c.execute(text(sql)).scalar_one()


# ---------------------------------------------------------------- snapshot rules


def test_repeat_run_no_duplicate_or_spurious_audit(db):
    load(db)
    n = scalar(db, 'SELECT COUNT(*) FROM lineage_change_log')
    load(db)
    assert scalar(db, 'SELECT COUNT(*) FROM lineage_change_log') == n
    assert scalar(db, 'SELECT COUNT(*) FROM transformation_rules WHERE is_active=1') == 11
    assert scalar(db, 'SELECT COUNT(*) FROM lineage_edges WHERE is_active=1') == 14


def test_revision_retirement_reactivation_preserve_history(db, tmp_path):
    load(db)
    frame = pd.read_csv(RULES_CSV, keep_default_na=False)
    frame.loc[frame.rule_name == 'price_interest_rate', 'transformation_logic'] = (
        'interest_rate = revised_grid[commercial_credit_score_band][industry_sector_band]'
        ' - tenure_discount[years_in_business]')
    path = tmp_path / 'rules.csv'
    frame.to_csv(path, index=False, lineterminator='\n')
    load(db, path)
    assert 'revised_grid' in scalar(
        db, "SELECT transformation_logic FROM transformation_rules WHERE rule_name='price_interest_rate'")
    assert scalar(db, "SELECT COUNT(*) FROM lineage_change_log WHERE action='update' AND entity_type='rule'") == 1

    frame = frame[frame.rule_name != 'price_interest_rate']
    frame.to_csv(path, index=False, lineterminator='\n')
    load(db, path)
    assert scalar(db, "SELECT is_active FROM transformation_rules WHERE rule_name='price_interest_rate'") == 0
    assert all(d['field_name'] != 'interest_rate' for _, d in build_graph(db).nodes(data=True))
    assert scalar(db, "SELECT COUNT(*) FROM lineage_change_log WHERE action='retire'") >= 4

    load(db)
    assert scalar(db, "SELECT is_active FROM transformation_rules WHERE rule_name='price_interest_rate'") == 1
    assert scalar(db, 'SELECT COUNT(*) FROM transformation_rules') == 11


def test_invalid_snapshot_does_not_mutate(db, tmp_path):
    load(db)
    before = scalar(db, 'SELECT COUNT(*) FROM lineage_change_log')
    f = pd.read_csv(RULES_CSV, keep_default_na=False)
    f.loc[f.index[-1], 'transformation_logic'] = 'Conflicting formula'
    path = tmp_path / 'bad.csv'
    f.to_csv(path, index=False, lineterminator='\n')
    with pytest.raises(ValueError, match='Conflicting'):
        load(db, path)
    assert scalar(db, 'SELECT COUNT(*) FROM lineage_change_log') == before


def test_cycles_rejected():
    f = pd.read_csv(RULES_CSV, keep_default_na=False)
    mask = (f.rule_name == 'calc_dti_ratio') & (f.source_ref == 'loan_amount_gbp')
    f.loc[mask, ['source_ref', 'source_field']] = 'dti_ratio'
    with pytest.raises(ValueError, match='cycle'):
        validate_rules(f)


def test_blank_snapshot_rejected():
    with pytest.raises(ValueError):
        validate_rules(pd.read_csv(RULES_CSV).iloc[:0])


# ------------------------------------------------- canonical formula consistency


def test_formula_must_name_every_declared_input():
    """The pricing gap: an input relationship existed but its term was missing
    from the stored formula, so generated docs silently dropped the discount."""
    f = pd.read_csv(RULES_CSV, keep_default_na=False)
    mask = f.rule_name == 'price_interest_rate'
    f.loc[mask, 'transformation_logic'] = (
        'interest_rate = pricing_grid[commercial_credit_score_band][industry_sector_band]')
    with pytest.raises(ValueError, match='omits declared input years_in_business'):
        validate_rules(f)


def test_duplicate_relationship_rows_rejected():
    f = pd.read_csv(RULES_CSV, keep_default_na=False)
    f = pd.concat([f, f.iloc[[9]]], ignore_index=True)
    with pytest.raises(ValueError, match='duplicate input relationship'):
        validate_rules(f)


# ------------------------------------------------------------ relationship evidence


def internal_relationships(db):
    return db.connect().execute(text(
        """SELECT s.field_name AS source_field, n.field_name AS target_field,
                  d.rule_name, e.source_mapping_label, e.source_verification_status,
                  e.source_evidence_ref, e.source_transformation
           FROM lineage_edges e
           JOIN lineage_nodes s ON s.node_id=e.source_node_id
           JOIN datasets sd ON sd.dataset_id=s.dataset_id
           JOIN lineage_nodes n ON n.node_id=e.target_node_id
           JOIN transformation_rules d ON d.rule_id=e.rule_id
           WHERE e.is_active=1 AND sd.dataset_name=:d
           ORDER BY d.rule_name, s.field_name"""), {'d': GOVERNED}).mappings().all()


def test_internal_relationships_are_verified_against_the_schema(db):
    load(db)
    rows = internal_relationships(db)
    assert len(rows) == 5
    assert all(r['source_mapping_label'] == 'local-data' for r in rows)
    assert all(r['source_verification_status'] == 'confirmed' and r['source_evidence_ref'] for r in rows)
    dti = [r for r in rows if r['target_field'] == 'dti_ratio']
    assert [r['source_field'] for r in dti] == ['annual_revenue_gbp', 'loan_amount_gbp']
    assert [r['source_transformation'] for r in dti] == [
        'denominator of the documented division', 'numerator of the documented division']
    assert [r['source_field'] for r in rows if r['target_field'] == 'interest_rate'] == [
        'commercial_credit_score', 'industry_sector', 'years_in_business']


def test_external_lineage_reaches_a_named_source_field(db):
    load(db)
    graph = build_graph(db)
    ancestors = upstream_lineage(graph, GOVERNED, 'commercial_credit_score')
    assert [a['field_name'] for a in ancestors] == ['risk_score']
    assert ancestors[0]['dataset_name'] == 'Experian Commercial / RiskReport'
    assert ancestors[0]['node_type'] == 'External Feed'
    label = scalar(db, """SELECT e.source_mapping_label FROM lineage_edges e
        JOIN lineage_nodes s ON s.node_id=e.source_node_id
        WHERE s.field_name='risk_score'""")
    assert label == 'illustrative'
    assert scalar(db, """SELECT COUNT(*) FROM lineage_edges e
        WHERE e.is_active=1 AND e.source_mapping_label='illustrative'
        AND e.source_verification_status='draft' AND e.source_evidence_ref IS NULL""") == 9


def test_illustrative_mapping_cannot_self_certify():
    f = pd.read_csv(RULES_CSV, keep_default_na=False)
    f.loc[f.source_mapping_label == 'illustrative',
          ['source_verification_status', 'source_evidence_ref', 'source_verified_at']] = (
        'confirmed', 'reviewed verbally', '2026-10-30')
    with pytest.raises(ValueError, match='illustrative mapping cannot be confirmed'):
        validate_rules(f)


def test_draft_relationship_cannot_cite_evidence():
    f = pd.read_csv(RULES_CSV, keep_default_na=False)
    f.loc[f.source_mapping_label == 'illustrative', 'source_evidence_ref'] = 'some spec'
    with pytest.raises(ValueError, match='draft relationships cannot cite evidence'):
        validate_rules(f)


def test_a_reviewed_vendor_spec_can_be_confirmed():
    f = pd.read_csv(RULES_CSV, keep_default_na=False)
    mask = f.source_ref == 'Experian Commercial'
    f.loc[mask, ['source_mapping_label', 'source_verification_status', 'source_evidence_ref',
                 'source_verified_at']] = ('vendor-spec', 'confirmed', 'experian-field-spec-v3', '2026-10-30')
    assert (validate_rules(f).shape == f.shape)


def test_future_review_date_rejected():
    f = pd.read_csv(RULES_CSV, keep_default_na=False)
    future = (date.today() + timedelta(days=1)).isoformat()
    f.loc[f.source_kind == 'Internal', 'source_verified_at'] = future
    with pytest.raises(ValueError, match='cannot be in the future'):
        validate_rules(f)


def test_rule_confirmation_requires_every_relationship_confirmed():
    f = pd.read_csv(RULES_CSV, keep_default_na=False)
    f.loc[f.rule_name == 'ingest_commercial_credit_score',
          ['verification_status', 'evidence_ref', 'verified_at', 'verified_by']] = (
        'confirmed', 'spec-1', '2026-10-30', 'reviewer')
    with pytest.raises(ValueError, match='input relationship'):
        validate_rules(f)


def test_open_issue_blocks_rule_confirmation(db, tmp_path):
    load(db)
    with db.begin() as c:
        c.execute(text("""INSERT INTO governance_issues(issue_key,dataset_id,field_name,category,priority,
            status,owner,description,next_action) VALUES ('X',1,'dti_ratio','manual_review','high',
            'open','owner','desc','action')"""))
    f = pd.read_csv(RULES_CSV, keep_default_na=False)
    f.loc[f.rule_name == 'calc_dti_ratio', ['verification_status', 'evidence_ref', 'verified_at',
                                            'verified_by']] = ('confirmed', 'spec-1', '2026-10-30', 'reviewer')
    path = tmp_path / 'rules.csv'
    f.to_csv(path, index=False, lineterminator='\n')
    before = scalar(db, 'SELECT COUNT(*) FROM lineage_change_log')
    with pytest.raises(ValueError, match='open governance issue'):
        load(db, path)
    assert scalar(db, 'SELECT COUNT(*) FROM lineage_change_log') == before


def test_snapshot_cannot_claim_a_field_that_is_not_in_the_schema(db, tmp_path):
    f = pd.read_csv(RULES_CSV, keep_default_na=False)
    rule = f.rule_name == 'calc_dti_ratio'
    source = rule & (f.source_ref == 'loan_amount_gbp')
    f.loc[source, ['source_ref', 'source_field', 'source_transformation']] = [
        'loan_amount_eur', 'loan_amount_eur', 'numerator of the documented division']
    f.loc[rule, 'transformation_logic'] = 'dti_ratio = loan_amount_eur / annual_revenue_gbp'
    path = tmp_path / 'rules.csv'
    f.to_csv(path, index=False, lineterminator='\n')
    with pytest.raises(ValueError, match='latest schema scan'):
        load(db, path)


# ---------------------------------------------------------------- version history


def seed_definition(db):
    with db.begin() as c:
        c.execute(text("""INSERT INTO data_dictionary(dataset_id,field_name,description,data_type,valid_range,
            known_issues,business_owner,valid_from,is_current)
            VALUES (1,'score','Old score','integer','0-100','old issue','owner','2024-01-15',1)"""))


def test_scd_history_boundaries_and_explicit_clear(db):
    seed_definition(db)
    update_field_definition(db, 1, 'score', FieldDefinition(description='New score', known_issues=''),
                            date(2024, 4, 2), 'reviewer')
    assert get_definition_as_of(db, 1, 'score', date(2024, 4, 1))['description'] == 'Old score'
    assert get_definition_as_of(db, 1, 'score', date(2024, 4, 2))['description'] == 'New score'
    assert scalar(db, "SELECT known_issues FROM data_dictionary WHERE is_current=1") == ''
    assert scalar(db, 'SELECT COUNT(*) FROM data_dictionary WHERE is_current=1') == 1
    with pytest.raises(ValueError):
        update_field_definition(db, 1, 'score', FieldDefinition(description='Bad'), date(2024, 4, 2), 'reviewer')
    assert scalar(db, 'SELECT COUNT(*) FROM data_dictionary') == 2


# ------------------------------------------------------- calculations and controls


def test_ratio_checks_preserve_input_and_block_bad_denominators():
    f = pd.DataFrame({'loan_id': ['a', 'b', 'c', 'd'], 'loan_amount_gbp': [20, 20, 20, 20],
                      'annual_revenue_gbp': [100, 100, 0, float('nan')], 'dti_ratio': [.2, .3, .5, .6]})
    before = f.copy(deep=True)
    out = validate_ratio(f)
    assert out.status.tolist() == ['pass', 'mismatch', 'blocked_input', 'blocked_input']
    pd.testing.assert_frame_equal(f, before)


def test_confirmation_requires_evidence(db):
    with pytest.raises(ValueError, match='evidence'):
        set_verification(db, 1, 'score', 'confirmed')


def test_import_entrypoint_and_configuration(monkeypatch):
    import main
    import config.db_config as db_config
    # A developer checkout normally has a real config/.env, which by design fills
    # in the variable when the environment does not set it. Silence that layer so
    # this checks the guard itself rather than the machine running the test.
    monkeypatch.delenv('METADATA_DATABASE_URL', raising=False)
    monkeypatch.setattr(db_config, 'load_dotenv', lambda *args, **kwargs: False)
    with pytest.raises(RuntimeError, match='METADATA_DATABASE_URL'):
        db_config.get_engine()
    monkeypatch.setenv('METADATA_DATABASE_URL', 'sqlite:///catalog.db')
    with pytest.raises(ValueError, match='mysql'):
        db_config.get_engine()


def test_sql_script_parser_handles_triggers():
    from scripts.setup_database import statements, ROOT
    commands = list(statements((ROOT / 'sql/ddl/03_audit_triggers.sql').read_text(encoding='utf-8')))
    assert len([x for x in commands if x.startswith('CREATE TRIGGER')]) == 2
    assert not any('DELIMITER' in x for x in commands)
