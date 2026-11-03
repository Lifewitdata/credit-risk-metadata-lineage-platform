"""
main.py
--------
Orchestrates the repeatable data pipeline: ETL -> governance validation ->
lineage extraction -> documentation generation -> visualization.

NOT included here: schema creation, trigger installation, and user
provisioning (sql/ddl/01_create_schema.sql, sql/ddl/03_audit_triggers.sql).
Those are one-time infrastructure setup a DBA runs once per environment --
see README.md "One-time setup". Mixing schema DDL into an application
orchestrator that runs repeatedly is an anti-pattern in a real banking
environment, where schema changes go through separate change control.

Run: python3 main.py
"""

import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))

from config.db_config import get_engine
from etl import load_raw
from sql.validation import run_validation
from lineage import load_transformation_rules, build_graph, visualize
from docs import generate_docs
from metadata import governance, calculation_validation, data_quality, excel_report
from metadata.dictionary_service import apply_demo_revision


def step(name: str):
    def decorator(fn):
        def wrapper(*args, **kwargs):
            print(f"\n{'=' * 78}\nSTEP: {name}\n{'=' * 78}")
            start = time.time()
            result = fn(*args, **kwargs)
            print(f"[{name}] completed in {time.time() - start:.2f}s")
            return result

        return wrapper

    return decorator


@step("1/6  ETL: load raw data into MySQL")
def run_etl():
    load_raw.run()


@step("2/6  Governance validation: dictionary <-> physical schema reconciliation")
def run_validation_step():
    return run_validation.run()


@step("3/6  Data quality: null counts and freshness against the declared cadence")
def run_data_quality(engine):
    frame = data_quality.run(engine)
    flagged = frame[frame.status != "ok"]
    print(f"[data_quality] {len(frame)} checks, {len(flagged)} to review")
    return frame


@step("4/6  Lineage extraction: transformation_rules.csv -> graph tables")
def run_lineage_load(engine):
    load_transformation_rules.load(engine)


@step("5/6  Documentation generation: dictionary + lineage markdown reports")
def run_docs(engine):
    generate_docs.generate_data_dictionary_doc(engine)
    generate_docs.generate_lineage_doc(engine)


@step("6/6  Visualization: static PNG + interactive HTML lineage graphs")
def run_visualization(engine):
    graph = build_graph.build_graph(engine)
    build_graph.validate_dag(graph)  # hard stop if lineage isn't a valid DAG
    docs_dir = Path(__file__).resolve().parent / "docs"
    docs_dir.mkdir(exist_ok=True)
    visualize.plot_static(graph, docs_dir / "lineage_graph.png")
    visualize.plot_interactive(graph, docs_dir / "lineage_graph_interactive.html")


def main():
    pipeline_start = time.time()
    print("Credit Risk Metadata & Data Lineage Platform -- pipeline run")

    try:
        run_etl()
        engine = get_engine()
        apply_demo_revision(engine)
        governance.seed_records(engine)
        run_validation_step()
        governance.sync_schema_issues(engine)
        run_data_quality(engine)
        calculation_validation.run()
        excel_report.run()
        run_lineage_load(engine)
        run_docs(engine)
        run_visualization(engine)
        governance.export_records(engine)
    except Exception as exc:
        print(f"\nPIPELINE FAILED: {exc}", file=sys.stderr)
        sys.exit(1)

    print(
        f"\n{'=' * 78}\nPipeline completed successfully in {time.time() - pipeline_start:.2f}s"
    )
    print("=" * 78)
    print("Outputs:")
    print("  docs/data_dictionary.md")
    print("  docs/lineage_report.md")
    print("  docs/lineage_graph.png")
    print("  docs/lineage_graph_interactive.html")
    print("  reports/calculation_validation.xlsx")


if __name__ == "__main__":
    main()
