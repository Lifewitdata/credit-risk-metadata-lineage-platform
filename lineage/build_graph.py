"""
build_graph.py
---------------
Step 6: Load lineage_nodes/lineage_edges from MySQL into a NetworkX DiGraph,
and provide the traversal operations that are the actual point of building
a lineage platform:

  - upstream_lineage(graph, node_key)   : full ancestor trace ("where did
                                           this come from, all the way back?")
  - downstream_impact(graph, node_key)  : full descendant trace ("what
                                           breaks if this field changes?")
  - validate_dag(graph)                 : confirm no cycles exist -- a real
                                           lineage graph must be acyclic

Each graph node is keyed by "dataset_name::field_name" (a readable,
deterministic composite key) and carries the underlying node_id, node_type,
and dataset_name as attributes for anything that needs to trace back to
the relational tables.
"""

import sys
from pathlib import Path

import networkx as nx
from sqlalchemy import text
from sqlalchemy.engine import Engine

sys.path.append(str(Path(__file__).resolve().parent.parent))
from config.db_config import get_engine  # noqa: E402


def node_key(dataset_name: str, field_name: str) -> str:
    return f"{dataset_name}::{field_name}"


def build_graph(engine: Engine) -> nx.DiGraph:
    """Build a directed lineage graph from lineage_nodes / lineage_edges.
    Edge direction is source -> target, i.e. "flows into" -- matching how
    the tables were populated in Step 5."""
    graph = nx.DiGraph()

    with engine.connect() as conn:
        nodes = conn.execute(
            text(
                """
                SELECT n.node_id, d.dataset_name, n.field_name, n.node_type
                FROM lineage_nodes n
                JOIN datasets d ON d.dataset_id = n.dataset_id
                """
            )
        ).mappings().fetchall()

        edges = conn.execute(
            text(
                """
                SELECT e.source_node_id, e.target_node_id, tr.rule_name, tr.transformation_type
                FROM lineage_edges e
                LEFT JOIN transformation_rules tr ON tr.rule_id = e.rule_id
                """
            )
        ).mappings().fetchall()

    node_id_to_key = {}
    for n in nodes:
        key = node_key(n["dataset_name"], n["field_name"])
        node_id_to_key[n["node_id"]] = key
        graph.add_node(
            key,
            node_id=n["node_id"],
            dataset_name=n["dataset_name"],
            field_name=n["field_name"],
            node_type=n["node_type"],
        )

    for e in edges:
        source_key = node_id_to_key[e["source_node_id"]]
        target_key = node_id_to_key[e["target_node_id"]]
        graph.add_edge(
            source_key,
            target_key,
            rule_name=e["rule_name"],
            transformation_type=e["transformation_type"],
        )

    return graph


def validate_dag(graph: nx.DiGraph) -> None:
    """Raise if the lineage graph contains a cycle. A field transitively
    depending on itself means a transformation rule was defined
    incorrectly -- this should block the pipeline, not just warn."""
    if not nx.is_directed_acyclic_graph(graph):
        cycle = nx.find_cycle(graph)
        raise ValueError(f"Lineage graph contains a cycle, not a valid DAG: {cycle}")


def upstream_lineage(graph: nx.DiGraph, dataset_name: str, field_name: str) -> list[dict]:
    """All ancestors of a field -- every upstream field/feed that
    transitively contributes to it, regardless of hop count."""
    key = node_key(dataset_name, field_name)
    if key not in graph:
        raise KeyError(f"'{key}' not found in lineage graph")

    ancestors = nx.ancestors(graph, key)
    return sorted(
        ({**graph.nodes[a], "key": a} for a in ancestors),
        key=lambda n: (n["node_type"], n["field_name"]),
    )


def downstream_impact(graph: nx.DiGraph, dataset_name: str, field_name: str) -> list[dict]:
    """All descendants of a field -- everything that would be affected if
    this field's definition, values, or availability changed. This is the
    query you run before approving a breaking change like the
    commercial_credit_score scale redefinition from Step 4."""
    key = node_key(dataset_name, field_name)
    if key not in graph:
        raise KeyError(f"'{key}' not found in lineage graph")

    descendants = nx.descendants(graph, key)
    return sorted(
        ({**graph.nodes[d], "key": d} for d in descendants),
        key=lambda n: (n["node_type"], n["field_name"]),
    )


def graph_summary(graph: nx.DiGraph) -> dict:
    """High-level stats for a documentation header / sanity check."""
    node_types = {}
    for _, data in graph.nodes(data=True):
        node_types[data["node_type"]] = node_types.get(data["node_type"], 0) + 1

    return {
        "total_nodes": graph.number_of_nodes(),
        "total_edges": graph.number_of_edges(),
        "nodes_by_type": node_types,
        "root_nodes": [n for n in graph.nodes if graph.in_degree(n) == 0],
        "leaf_nodes": [n for n in graph.nodes if graph.out_degree(n) == 0],
        "is_dag": nx.is_directed_acyclic_graph(graph),
    }


if __name__ == "__main__":
    engine = get_engine()
    graph = build_graph(engine)
    validate_dag(graph)

    print("=" * 78)
    print("LINEAGE GRAPH SUMMARY")
    print("=" * 78)
    summary = graph_summary(graph)
    print(f"Nodes: {summary['total_nodes']}  |  Edges: {summary['total_edges']}  |  "
          f"DAG valid: {summary['is_dag']}")
    print(f"By type: {summary['nodes_by_type']}")
    print(f"Root nodes (no upstream): {summary['root_nodes']}")
    print(f"Leaf nodes (no downstream): {summary['leaf_nodes']}")

    print("\n" + "=" * 78)
    print("UPSTREAM TRACE: credit_datamart_sme_loans.interest_rate")
    print("=" * 78)
    for ancestor in upstream_lineage(graph, "credit_datamart_sme_loans", "interest_rate"):
        print(f"  [{ancestor['node_type']:14}] {ancestor['key']}")

    print("\n" + "=" * 78)
    print("DOWNSTREAM IMPACT: credit_datamart_sme_loans.commercial_credit_score")
    print("(what breaks if this field's definition or values change?)")
    print("=" * 78)
    for descendant in downstream_impact(graph, "credit_datamart_sme_loans", "commercial_credit_score"):
        print(f"  [{descendant['node_type']:14}] {descendant['key']}")
