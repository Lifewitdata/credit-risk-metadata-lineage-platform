"""Opt-in tests against a disposable MySQL schema; never a user production database.

Tests are ordered: read-only checks first, then the run that mutates metadata.
"""
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import pytest
import pandas as pd
from sqlalchemy import text

pytestmark = pytest.mark.skipif(os.getenv('CATALOG_TEST_MYSQL') != '1',
                                reason='Disposable MySQL integration environment not enabled')
ROOT = Path(__file__).resolve().parents[1]
GEN_TIMESTAMP = re.compile(rb'^> \*\*Auto-generated\*\* on .*$', re.MULTILINE)


def run_pipeline():
    subprocess.run([sys.executable, 'main.py'], check=True, cwd=ROOT,
                   env={**os.environ, 'MPLBACKEND': 'Agg'})


@pytest.fixture(scope='module')
def engine():
    """Start from an empty schema, then run the documented fresh-install path."""
    from config.db_config import get_engine
    from scripts.setup_database import setup
    e = get_engine()
    setup(e)
    run_pipeline()
    yield e
    e.dispose()


def _committed(name):
    return subprocess.run(['git', 'show', f'HEAD:{name}'], cwd=ROOT, check=True,
                          capture_output=True).stdout


def test_committed_documentation_matches_the_database(engine):
    """Generated docs are committed, so they can silently disagree with the code.

    Regenerate and compare byte-for-byte, ignoring only the run timestamp in the
    markdown reports. Text output is reproducible everywhere. The PNG is not: the
    committed pixels only match the machine that rendered them, because the
    rasterizer behind the text labels is built per platform even when the
    matplotlib version is identical. The image is checked for stability in the
    next test instead, and its structure is still covered here because the
    interactive HTML embeds every node and edge.
    """
    run_pipeline()
    for name in ['docs/data_dictionary.md', 'docs/lineage_report.md']:
        assert GEN_TIMESTAMP.sub(b'> **Auto-generated** on <timestamp>', _committed(name)) == \
               GEN_TIMESTAMP.sub(b'> **Auto-generated** on <timestamp>',
                                 (ROOT / name).read_bytes()), name
    assert _committed('docs/lineage_graph_interactive.html') == \
           (ROOT / 'docs' / 'lineage_graph_interactive.html').read_bytes()


def test_rendered_graph_is_byte_stable_here(engine, tmp_path):
    """Two renders of the current graph must be identical to this run's output.

    This is the ordering guard that the committed-image check was standing in
    for: node order inside a layout layer used to come from nx.utils.groups,
    which returns sets, so the image followed the interpreter's hash seed.
    """
    from lineage.build_graph import build_graph
    from lineage.visualize import plot_static

    graph = build_graph(engine)
    first, second = tmp_path / 'first.png', tmp_path / 'second.png'
    plot_static(graph, first)
    plot_static(graph, second)
    assert first.read_bytes() == second.read_bytes() == \
           (ROOT / 'docs' / 'lineage_graph.png').read_bytes()


def test_committed_calculation_report_matches_the_fixture(engine):
    committed = subprocess.run(['git', 'show', 'HEAD:reports/calculation_validation.csv'],
                               cwd=ROOT, check=True, capture_output=True).stdout
    assert (ROOT / 'reports' / 'calculation_validation.csv').read_bytes() == committed


def test_committed_dashboard_extract_matches_the_catalog(engine):
    """The BI extract is the source a hosted dashboard would read, so it is
    committed and compared like the other reports. It carries no timestamps for
    exactly this reason; queue timing stays in gitignored reports/local/."""
    committed = subprocess.run(['git', 'show', 'HEAD:reports/governance_dashboard.csv'],
                               cwd=ROOT, check=True, capture_output=True).stdout
    assert (ROOT / 'reports' / 'governance_dashboard.csv').read_bytes() == committed


def test_rendered_dashboard_is_byte_stable_here(engine, tmp_path):
    """Two renders of the extract must be identical, like the lineage PNG: the
    committed pixels only ever match the machine that drew them."""
    from metadata.dashboard import extract, render

    frame = extract(engine)
    first, second = tmp_path / 'first.png', tmp_path / 'second.png'
    render(frame, first)
    render(frame, second)
    assert first.read_bytes() == second.read_bytes() == \
           (ROOT / 'docs' / 'governance_dashboard.png').read_bytes()


def test_external_relationship_evidence_survives_a_mysql_round_trip(engine):
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT s.field_name, d.dataset_name AS feed, e.source_mapping_label,
                   e.source_verification_status, e.source_evidence_ref, e.source_verified_at
            FROM lineage_edges e
            JOIN lineage_nodes s ON s.node_id = e.source_node_id
            JOIN datasets d ON d.dataset_id = s.dataset_id
            WHERE e.is_active = 1 AND s.node_type = 'External Feed'""")).mappings().all()
    assert len(rows) == 9
    assert all(r['source_mapping_label'] == 'illustrative' for r in rows)
    assert all(r['source_verification_status'] == 'draft' for r in rows)
    assert all(r['source_evidence_ref'] is None and r['source_verified_at'] is None for r in rows)
    assert {'RiskReport', 'CompanyProfile', 'FiledAccounts'} <= {r['feed'].split(' / ')[1] for r in rows}
    with engine.connect() as c:
        dates = c.execute(text("""SELECT DISTINCT source_verified_at FROM lineage_edges e
            JOIN lineage_nodes s ON s.node_id = e.source_node_id
            WHERE e.is_active = 1 AND e.source_mapping_label = 'local-data'""")).scalars().all()
    assert dates == [date(2026, 10, 31)]


def test_repeat_pipeline_run_changes_nothing(engine):
    from scripts.setup_database import setup
    with engine.connect() as c:
        before = {t: c.execute(text(f'SELECT COUNT(*) FROM {t}')).scalar_one()
                  for t in ['transformation_rules', 'lineage_nodes', 'lineage_edges',
                            'lineage_change_log', 'data_dictionary', 'governance_issues']}
    setup(engine, upgrade=True)
    run_pipeline()
    with engine.connect() as c:
        after = {t: c.execute(text(f'SELECT COUNT(*) FROM {t}')).scalar_one()
                 for t in ['transformation_rules', 'lineage_nodes', 'lineage_edges',
                           'lineage_change_log', 'data_dictionary', 'governance_issues']}
    assert after == before


def test_current_version_guard_and_issue_closure(engine):
    from metadata.dictionary_service import update_field_definition, FieldDefinition
    from metadata.governance import sync_schema_issues
    shipped_vendors = pd.read_csv(ROOT / 'data/raw/vendor_usage.csv',
                                  keep_default_na=False)['vendor_name'].tolist()
    assert len(shipped_vendors) == 6
    with engine.connect() as c:
        ds = c.execute(text("SELECT dataset_id FROM datasets WHERE dataset_name='credit_datamart_sme_loans'")).scalar_one()
        # Scoped to the governed dataset: onboarding a second one through
        # etl/load_market_prices.py adds a row to the scorecard and to vendor_usage,
        # and this assertion is about the fixture, not about how many datasets a
        # given database happens to have.
        assert c.execute(text("SELECT documentation_coverage_pct FROM vw_governance_scorecard "
                              "WHERE dataset_name='credit_datamart_sme_loans'")).scalar_one() == 100
        unconfirmed = set(c.execute(text(
            "SELECT vendor_name FROM vendor_usage WHERE customer_display='unconfirmed'")).scalars())
        assert set(shipped_vendors) <= unconfirmed
    def revise(label):
        try:
            update_field_definition(engine, ds, 'loan_id', FieldDefinition(description=label),
                                    date(2026, 10, 30), label)
            return 'ok'
        except ValueError:
            return 'conflict'

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(revise, ['a', 'b'])) == ['conflict', 'ok']
    with engine.connect() as c:
        assert c.execute(text('SELECT COUNT(*) FROM data_dictionary WHERE is_current=1 AND field_name=\'loan_id\'')).scalar_one() == 1

    # Reconciliation creates a queue entry; fixing the metadata closes it with evidence.
    with engine.begin() as c:
        c.execute(text("UPDATE data_dictionary SET data_type='integer' WHERE field_name='loan_id' AND is_current=1"))
    sync_schema_issues(engine)
    with engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM governance_issues WHERE category='schema' AND status='open'")).scalar_one() == 1
    with engine.begin() as c:
        c.execute(text("UPDATE data_dictionary SET data_type='string' WHERE field_name='loan_id' AND is_current=1"))
    sync_schema_issues(engine)
    with engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM governance_issues WHERE category='schema' AND status='resolved' AND closure_evidence IS NOT NULL")).scalar_one() == 1
        assert c.execute(text("SELECT COUNT(*) FROM metadata_audit_log WHERE action_type='CLOSE'")).scalar_one() >= 2


def test_rule_revision_and_relationship_retirement_in_mysql(engine, tmp_path):
    import pandas as pd
    from lineage.load_transformation_rules import load, RULES_CSV
    with engine.connect() as c:
        before = c.execute(text('SELECT COUNT(*) FROM lineage_change_log')).scalar_one()
    frame = pd.read_csv(RULES_CSV, keep_default_na=False)
    path = tmp_path / 'rules.csv'

    frame.loc[frame.rule_name == 'price_interest_rate', 'transformation_logic'] = (
        'interest_rate = revised_grid[commercial_credit_score_band][industry_sector_band] '
        '- tenure_discount[years_in_business]')
    frame.to_csv(path, index=False, lineterminator='\n')
    load(engine, path)
    with engine.connect() as c:
        assert c.execute(text("""SELECT transformation_logic FROM transformation_rules
            WHERE rule_name='price_interest_rate'""")).scalar_one().startswith('interest_rate = revised_grid')
        assert c.execute(text("SELECT COUNT(*) FROM lineage_change_log WHERE action='update'")).scalar_one() >= 1

    dropped = frame[frame.rule_name != 'price_interest_rate']
    dropped.to_csv(path, index=False, lineterminator='\n')
    load(engine, path)
    with engine.connect() as c:
        assert c.execute(text("""SELECT is_active FROM transformation_rules
            WHERE rule_name='price_interest_rate'""")).scalar_one() == 0
        assert c.execute(text("""SELECT COUNT(*) FROM lineage_edges e
            JOIN transformation_rules r ON r.rule_id=e.rule_id
            WHERE r.rule_name='price_interest_rate' AND e.is_active=1""")).scalar_one() == 0
        assert c.execute(text("SELECT COUNT(*) FROM transformation_rules")).scalar_one() == 11

    load(engine)
    with engine.connect() as c:
        assert c.execute(text("""SELECT is_active FROM transformation_rules
            WHERE rule_name='price_interest_rate'""")).scalar_one() == 1
