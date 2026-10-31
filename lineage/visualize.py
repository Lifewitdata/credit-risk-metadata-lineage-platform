"""
visualize.py
-------------
Step 8: Render the lineage graph two ways from the same networkx.DiGraph:

  - plot_static()      : matplotlib PNG, hierarchical layout, for the README
  - plot_interactive()  : pyvis HTML, for exploring the graph locally

Hierarchical layout note: NetworkX has no built-in hierarchical/layered
layout without Graphviz installed. We derive one ourselves using
topological generations -- each node's "layer" is its distance from the
nearest root in the DAG (Kahn's-algorithm style batching via
nx.topological_generations). This reads naturally left-to-right as data
flow, which a force-directed layout (spring_layout) would not guarantee.
"""

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless rendering -- no display server in this environment
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import networkx as nx
from pyvis.network import Network

sys.path.append(str(Path(__file__).resolve().parent.parent))
from config.db_config import get_engine  # noqa: E402
from lineage.build_graph import build_graph, validate_dag  # noqa: E402

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"

NODE_COLORS = {
    "External Feed": "#9e9e9e",   # grey  -- upstream system boundary
    "Source Field": "#4a90d9",    # blue  -- raw ingested field
    "Derived Field": "#e07b39",   # orange -- calculated field
}


def _layered_nodes(graph: nx.DiGraph) -> dict[int, list[str]]:
    """Nodes grouped by topological generation, each group in a fixed order.

    Root nodes (External Feed boundaries, in-degree 0) form layer 0 and each
    generation after that is one hop further along the DAG. Passing this dict to
    multipartite_layout is the only way to get a stable picture: given a node
    attribute it groups nodes with nx.utils.groups, which returns sets, so
    within-layer order -- and therefore node position -- would follow the
    interpreter's hash seed instead of the data, and a regenerated PNG would
    never match the committed one.
    """
    return {
        layer_idx: sorted(generation)
        for layer_idx, generation in enumerate(nx.topological_generations(graph))
    }


def plot_static(graph: nx.DiGraph, output_path: Path) -> Path:
    # Columns by topological layer, so the image reads left-to-right as data flow.
    pos = nx.multipartite_layout(graph, subset_key=_layered_nodes(graph), align="vertical")

    node_colors = [NODE_COLORS[graph.nodes[n]["node_type"]] for n in graph.nodes]
    labels = {n: graph.nodes[n]["field_name"] for n in graph.nodes}

    fig, ax = plt.subplots(figsize=(16, 9))
    nx.draw_networkx_edges(
        graph, pos, ax=ax, arrows=True, arrowsize=18, arrowstyle="-|>",
        edge_color="#9e9e9e", connectionstyle="arc3,rad=0.08", width=1.3,
        node_size=1800, min_source_margin=15, min_target_margin=15,
    )
    nx.draw_networkx_nodes(
        graph, pos, ax=ax, node_color=node_colors, node_size=1800,
        edgecolors="white", linewidths=1.5,
    )
    nx.draw_networkx_labels(
        graph, pos, labels=labels, ax=ax, font_size=8, font_weight="bold",
        font_color="white",
    )

    legend_handles = [
        mpatches.Patch(color=color, label=node_type)
        for node_type, color in NODE_COLORS.items()
    ]
    ax.legend(handles=legend_handles, loc="upper left", fontsize=10, frameon=True)
    ax.set_title(
        "Credit Risk Data Lineage — SME Loans Datamart\n"
        "(Left = external source systems, Right = derived features)",
        fontsize=13,
    )
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, facecolor="#1e1e1e")
    plt.close(fig)

    print(f"[visualize] wrote static PNG: {output_path}")
    return output_path


def plot_interactive(graph: nx.DiGraph, output_path: Path) -> Path:
    net = Network(
        directed=True, height="750px", width="100%",
        bgcolor="#1e1e1e", font_color="white", notebook=False,
        cdn_resources="remote",  # avoid writing a local lib/ folder relative
        # to the CWD -- pyvis's default ("local") references
        # "lib/bindings/utils.js" relative to wherever the script was RUN
        # from, not relative to the output HTML file's directory. That
        # breaks the file the moment it's opened from a different working
        # directory (exactly what happened here: docs/lineage_graph_
        # interactive.html referenced a lib/ folder that landed at the
        # project root instead). "remote" makes the HTML fully self-
        # contained via CDN, so it works regardless of where it's opened.
    )
    net.barnes_hut(gravity=-3000, central_gravity=0.2, spring_length=150)

    for node, data in graph.nodes(data=True):
        net.add_node(
            node,
            label=data["field_name"],
            title=f"{data['dataset_name']} · {data['node_type']}",
            color=NODE_COLORS[data["node_type"]],
            shape="dot",
            size=18,
        )

    for source, target, data in graph.edges(data=True):
        edge_title = data.get("rule_name") or "direct ingest"
        net.add_edge(source, target, title=edge_title, arrows="to", color="#777777")

    net.show_buttons(filter_=["physics"])
    net.write_html(str(output_path), open_browser=False, notebook=False)
    # pyvis writes with the platform newline, so on Windows the committed HTML
    # would differ from the file only by line endings. Generated reports are
    # compared byte-for-byte against the repository, so pin them to LF here.
    output_path.write_bytes(output_path.read_bytes().replace(b"\r\n", b"\n"))

    print(f"[visualize] wrote interactive HTML: {output_path}")
    return output_path


if __name__ == "__main__":
    engine = get_engine()
    graph = build_graph(engine)
    validate_dag(graph)

    DOCS_DIR.mkdir(exist_ok=True)
    plot_static(graph, DOCS_DIR / "lineage_graph.png")
    plot_interactive(graph, DOCS_DIR / "lineage_graph_interactive.html")
