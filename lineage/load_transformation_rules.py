"""
load_transformation_rules.py
------------------------------
Step 5: Load transformation_rules.csv and materialize it into three tables:

  transformation_rules  -- one row per unique rule (deduplicated by rule_name)
  lineage_nodes          -- one row per distinct field (or external feed)
  lineage_edges           -- one row per (source_node -> target_node) pair,
                              linked back to the rule that explains it

Design notes:
- Upstream systems (Salesforce, Experian, Companies House, etc.) are
  registered as pseudo-datasets with physical_table_name='EXTERNAL_FEED'.
  This lets a bureau feed be a first-class lineage_node even though it has
  no physical table of its own in this platform -- lineage needs to show
  "where did this ultimately come from", not just "what's in our database".
- A field's node_type is inferred, not hand-labeled: if ANY transformation
  rule targets it with source_kind='Internal', it's a Derived Field
  (computed from other datamart fields). Otherwise, if it appears as a
  target at all, it's a Source Field (raw ingest, no in-datamart
  calculation). This keeps the classification consistent with the CSV
  as the single source of truth, rather than a second hand-maintained list.
"""

import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

sys.path.append(str(Path(__file__).resolve().parent.parent))
from config.db_config import get_engine  # noqa: E402

RULES_CSV = Path(__file__).resolve().parent.parent / "data" / "raw" / "transformation_rules.csv"
TARGET_DATASET_NAME = "credit_datamart_sme_loans"


def _get_or_create_dataset(conn, dataset_name: str, is_external: bool) -> int:
    row = conn.execute(
        text("SELECT dataset_id FROM datasets WHERE dataset_name = :name"),
        {"name": dataset_name},
    ).fetchone()
    if row:
        return row[0]

    if not is_external:
        raise ValueError(
            f"Internal dataset '{dataset_name}' does not exist yet -- it "
            f"should already be registered by etl/load_raw.py (Step 2)."
        )

    result = conn.execute(
        text(
            """
            INSERT INTO datasets (dataset_name, physical_table_name, description, refresh_frequency, business_owner)
            VALUES (:name, 'EXTERNAL_FEED', :desc, 'N/A', 'Upstream System Owner')
            """
        ),
        {
            "name": dataset_name,
            "desc": f"Pseudo-dataset representing the upstream '{dataset_name}' "
                    f"source system as a lineage boundary. No physical table -- "
                    f"registered for lineage traceability only.",
        },
    )
    print(f"[datasets] registered external feed pseudo-dataset: {dataset_name}")
    return result.lastrowid


def _get_or_create_node(conn, dataset_id: int, field_name: str, node_type: str) -> int:
    row = conn.execute(
        text(
            "SELECT node_id FROM lineage_nodes WHERE dataset_id = :ds AND field_name = :field"
        ),
        {"ds": dataset_id, "field": field_name},
    ).fetchone()
    if row:
        return row[0]

    result = conn.execute(
        text(
            """
            INSERT INTO lineage_nodes (dataset_id, field_name, node_type)
            VALUES (:ds, :field, :node_type)
            """
        ),
        {"ds": dataset_id, "field": field_name, "node_type": node_type},
    )
    return result.lastrowid


def load(engine: Engine) -> None:
    rules_df = pd.read_csv(RULES_CSV)

    # A field is Derived if ANY rule targeting it has source_kind == 'Internal'.
    derived_fields = set(
        rules_df.loc[rules_df["source_kind"] == "Internal", "target_field"].unique()
    )

    with engine.begin() as conn:
        target_dataset_id = _get_or_create_dataset(conn, TARGET_DATASET_NAME, is_external=False)

        rule_id_cache: dict[str, int] = {}
        edges_created = 0

        for _, row in rules_df.iterrows():
            rule_name = row["rule_name"]

            # 1. transformation_rules -- one row per unique rule_name (dedupe
            #    across the multiple CSV rows that share a fan-in rule).
            #    IMPORTANT: check the DATABASE, not just this run's in-memory
            #    cache -- otherwise re-running this script duplicates every
            #    rule (caught by actually re-running it: 11 rules became 22).
            if rule_name not in rule_id_cache:
                existing_rule = conn.execute(
                    text(
                        "SELECT rule_id FROM transformation_rules WHERE rule_name = :rule_name"
                    ),
                    {"rule_name": rule_name},
                ).fetchone()

                if existing_rule:
                    rule_id_cache[rule_name] = existing_rule[0]
                else:
                    result = conn.execute(
                        text(
                            """
                            INSERT INTO transformation_rules
                                (rule_name, target_dataset_id, target_field, transformation_type,
                                 transformation_logic, business_justification, created_by)
                            VALUES
                                (:rule_name, :target_dataset_id, :target_field, :ttype,
                                 :logic, :justification, :created_by)
                            """
                        ),
                        {
                            "rule_name": rule_name,
                            "target_dataset_id": target_dataset_id,
                            "target_field": row["target_field"],
                            "ttype": row["transformation_type"],
                            "logic": row["transformation_logic"],
                            "justification": row["business_justification"],
                            "created_by": row["created_by"],
                        },
                    )
                    rule_id_cache[rule_name] = result.lastrowid

            rule_id = rule_id_cache[rule_name]

            # 2. Resolve/create the target node.
            target_node_type = "Derived Field" if row["target_field"] in derived_fields else "Source Field"
            target_node_id = _get_or_create_node(
                conn, target_dataset_id, row["target_field"], target_node_type
            )

            # 3. Resolve/create the source node.
            if row["source_kind"] == "External":
                source_dataset_id = _get_or_create_dataset(conn, row["source_ref"], is_external=True)
                source_node_id = _get_or_create_node(
                    conn, source_dataset_id, row["source_ref"], "External Feed"
                )
            else:  # Internal -- source_ref is another field within the same datamart
                source_node_type = "Derived Field" if row["source_ref"] in derived_fields else "Source Field"
                source_node_id = _get_or_create_node(
                    conn, target_dataset_id, row["source_ref"], source_node_type
                )

            # 4. Create the edge (idempotent thanks to uq_edge on the table).
            existing_edge = conn.execute(
                text(
                    "SELECT edge_id FROM lineage_edges WHERE source_node_id = :s AND target_node_id = :t"
                ),
                {"s": source_node_id, "t": target_node_id},
            ).fetchone()
            if existing_edge:
                continue

            conn.execute(
                text(
                    """
                    INSERT INTO lineage_edges (source_node_id, target_node_id, rule_id)
                    VALUES (:source_node_id, :target_node_id, :rule_id)
                    """
                ),
                {"source_node_id": source_node_id, "target_node_id": target_node_id, "rule_id": rule_id},
            )
            edges_created += 1

        print(f"[transformation_rules] loaded {len(rule_id_cache)} unique rules")
        print(f"[lineage_edges] created {edges_created} edges")


if __name__ == "__main__":
    engine = get_engine()
    load(engine)
