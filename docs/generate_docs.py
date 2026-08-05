"""
generate_docs.py
------------------
Step 7: Generate docs/data_dictionary.md and docs/lineage_report.md directly
from the live database. This is meant to be re-run as part of the pipeline
(see main.py orchestrator in Step 9) so documentation can never drift from
reality -- it IS the reality, rendered.
"""

import sys
from datetime import datetime
from pathlib import Path

import networkx as nx
import pandas as pd
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import text
from sqlalchemy.engine import Engine

sys.path.append(str(Path(__file__).resolve().parent.parent))
from config.db_config import get_engine  # noqa: E402
from lineage.build_graph import build_graph, node_key, validate_dag, graph_summary  # noqa: E402

DOCS_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = DOCS_DIR / "templates"
TARGET_DATASET_NAME = "credit_datamart_sme_loans"


# ---------------------------------------------------------------------------
# Data dictionary document
# ---------------------------------------------------------------------------

def _fetch_dictionary_context(engine: Engine, dataset_name: str) -> dict:
    with engine.connect() as conn:
        dataset_id = conn.execute(
            text("SELECT dataset_id FROM datasets WHERE dataset_name = :name"),
            {"name": dataset_name},
        ).scalar()

        scorecard = conn.execute(
            text("SELECT * FROM vw_governance_scorecard WHERE dataset_id = :id"),
            {"id": dataset_id},
        ).mappings().fetchone()

        reconciliation = conn.execute(
            text(
                """
                SELECT field_name, documented_data_type, actual_data_type, governance_status
                FROM vw_data_dictionary_reconciliation
                WHERE dataset_id = :id
                ORDER BY field_name
                """
            ),
            {"id": dataset_id},
        ).mappings().fetchall()

        current_fields = conn.execute(
            text(
                """
                SELECT field_name, description, data_type, valid_range,
                       known_issues, business_owner, valid_from
                FROM data_dictionary
                WHERE dataset_id = :id AND is_current = 1
                ORDER BY field_name
                """
            ),
            {"id": dataset_id},
        ).mappings().fetchall()

        history_rows = conn.execute(
            text(
                """
                SELECT field_name, description, data_type, valid_from, valid_to
                FROM data_dictionary
                WHERE dataset_id = :id
                ORDER BY field_name, valid_from
                """
            ),
            {"id": dataset_id},
        ).mappings().fetchall()

    # Group full version history by field_name for the "Version history" panels.
    history_by_field: dict[str, list[dict]] = {}
    for row in history_rows:
        history_by_field.setdefault(row["field_name"], []).append(dict(row))

    reconciliation_by_field = {row["field_name"]: row["governance_status"] for row in reconciliation}

    fields = []
    for f in current_fields:
        field_dict = dict(f)
        field_dict["governance_status"] = reconciliation_by_field.get(f["field_name"], "UNKNOWN")
        field_dict["history"] = history_by_field.get(f["field_name"], [])
        fields.append(field_dict)

    issues = [dict(r) for r in reconciliation if r["governance_status"] != "DOCUMENTED"]

    return {
        "dataset_name": dataset_name,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scorecard": dict(scorecard),
        "issues": issues,
        "fields": fields,
    }


def generate_data_dictionary_doc(engine: Engine) -> Path:
    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), trim_blocks=True, lstrip_blocks=True)
    template = env.get_template("data_dictionary.md.j2")

    context = _fetch_dictionary_context(engine, TARGET_DATASET_NAME)
    rendered = template.render(**context)

    output_path = DOCS_DIR / "data_dictionary.md"
    output_path.write_text(rendered)
    print(f"[docs] wrote {output_path}")
    return output_path


# ---------------------------------------------------------------------------
# Lineage report document
# ---------------------------------------------------------------------------

def _fetch_lineage_context(engine: Engine, dataset_name: str) -> dict:
    graph = build_graph(engine)
    validate_dag(graph)
    summary = graph_summary(graph)

    with engine.connect() as conn:
        all_rules = conn.execute(
            text(
                """
                SELECT rule_name, transformation_type, target_field,
                       transformation_logic, created_by
                FROM transformation_rules
                ORDER BY target_field, rule_name
                """
            )
        ).mappings().fetchall()

    rules_by_target: dict[str, list[dict]] = {}
    for r in all_rules:
        rules_by_target.setdefault(r["target_field"], []).append(dict(r))

    derived_fields = [
        n for n, data in graph.nodes(data=True)
        if data["node_type"] == "Derived Field" and data["dataset_name"] == dataset_name
    ]

    derived_field_contexts = []
    for key in sorted(derived_fields):
        field_name = graph.nodes[key]["field_name"]
        ancestors = nx.ancestors(graph, key)
        upstream = []
        for a in ancestors:
            # Depth = shortest directed hop count from the ancestor to this
            # field, used purely to indent the ASCII tree for readability.
            depth = nx.shortest_path_length(graph, a, key) - 1
            upstream.append({**graph.nodes[a], "depth": depth})
        upstream.sort(key=lambda n: (n["depth"], n["field_name"]))

        derived_field_contexts.append(
            {
                "field_name": field_name,
                "rules": rules_by_target.get(field_name, []),
                "upstream": upstream,
            }
        )

    source_fields = [
        n for n, data in graph.nodes(data=True)
        if data["node_type"] == "Source Field" and data["dataset_name"] == dataset_name
    ]
    source_impact = []
    for key in sorted(source_fields):
        descendants = nx.descendants(graph, key)
        field_descendants = [graph.nodes[d]["field_name"] for d in descendants]
        source_impact.append(
            {
                "field_name": graph.nodes[key]["field_name"],
                "downstream_list": ", ".join(f"`{d}`" for d in sorted(field_descendants)) or "*(none)*",
            }
        )

    return {
        "dataset_name": dataset_name,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": summary,
        "derived_fields": derived_field_contexts,
        "source_impact": source_impact,
        "all_rules": [dict(r) for r in all_rules],
    }


def generate_lineage_doc(engine: Engine) -> Path:
    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), trim_blocks=True, lstrip_blocks=True)
    template = env.get_template("lineage_report.md.j2")

    context = _fetch_lineage_context(engine, TARGET_DATASET_NAME)
    rendered = template.render(**context)

    output_path = DOCS_DIR / "lineage_report.md"
    output_path.write_text(rendered)
    print(f"[docs] wrote {output_path}")
    return output_path


if __name__ == "__main__":
    engine = get_engine()
    generate_data_dictionary_doc(engine)
    generate_lineage_doc(engine)
