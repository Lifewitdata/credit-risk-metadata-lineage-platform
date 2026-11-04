"""
dashboard.py
-------------
The governance scorecard and the issue queue as one dashboard-ready table, plus a
rendered preview of what that table is meant to become.

Why a CSV and not a hosted BI asset: Power BI, Tableau and Looker Studio each need an
account, a data-source credential and a refresh path, and this project has none of
them. A screenshot of a service nobody can reproduce would be a claim, not an
artifact. So the pipeline writes the tidy extract those tools are meant to read
(`reports/governance_dashboard.csv`), docs/dashboard.md is the layout and connection
spec, and the image is rendered here from the same rows, in the same run, on the same
machine as the rest of the reports.

The table is long (section, subject, metric, value) rather than wide because a BI tool
pivots rows: one column per KPI is a report, and a report is not a data source.

No timestamps, deliberately. The committed extract has to be byte-comparable with what
the pipeline regenerates on another machine on another day, and `opened_at` /
`updated_at` make that impossible. Queue timing stays in the gitignored
`reports/local/` exports, which nothing compares.
"""

import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

sys.path.append(str(Path(__file__).resolve().parent.parent))
from config.db_config import get_engine  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EXTRACT_PATH = ROOT / "reports" / "governance_dashboard.csv"
PREVIEW_PATH = ROOT / "docs" / "governance_dashboard.png"

COLUMNS = ["section", "subject", "metric", "value"]

# A status that is not a problem should not be coloured as one, and a colour a reader
# cannot look up is decoration. This is the whole vocabulary the preview uses.
STATUS_COLOURS = {
    "confirmed": "#2e7d32",
    "permitted": "#2e7d32",
    "current": "#2e7d32",
    "ok": "#2e7d32",
    "documented": "#2e7d32",
    "draft": "#b0700c",
    "unknown": "#b0700c",
    "in_progress": "#b0700c",
    "high": "#b0700c",
    "unconfirmed": "#8a8a8a",
    "prohibited": "#b3261e",
    "stale": "#b3261e",
    "open": "#b3261e",
}

# (subject in the extract, label on the axis)
VERIFICATION_OBJECTS = [
    ("field_definition", "Field definitions"),
    ("transformation_rule", "Active rules"),
    ("source_relationship", "Source relationships"),
]

ISSUE_METRICS = ["field_name", "category", "priority", "status", "owner"]
VENDOR_METRICS = ["customer_display", "redistribution", "reviewed"]


def extract(engine: Engine) -> pd.DataFrame:
    """One row per (section, subject, metric). Reads the catalog, writes nothing."""
    rows = []
    with engine.connect() as conn:
        for record in conn.execute(
            text(
                "SELECT dataset_name, total_fields, documented_count, undocumented_count,"
                " deprecated_count, type_mismatch_count, documentation_coverage_pct"
                " FROM vw_governance_scorecard ORDER BY dataset_id"
            )
        ).mappings():
            values = {key: record[key] for key in record if key != "dataset_name"}
            rows.extend(_rows("scorecard", record["dataset_name"], values))

        for subject, query in [
            ("field_definition", "SELECT status FROM field_verification"),
            (
                "transformation_rule",
                "SELECT verification_status AS status"
                " FROM transformation_rules WHERE is_active = 1",
            ),
            (
                "source_relationship",
                "SELECT source_verification_status AS status"
                " FROM lineage_edges WHERE is_active = 1",
            ),
        ]:
            counts = {}
            for record in conn.execute(text(query)).mappings():
                counts[record["status"]] = counts.get(record["status"], 0) + 1
            rows.extend(_rows("verification", subject, counts))

        for record in conn.execute(
            text(
                "SELECT issue_key, field_name, category, priority, status, owner,"
                " next_action FROM governance_issues ORDER BY issue_key"
            )
        ).mappings():
            values = dict(record)
            rows.extend(_rows("issues", values.pop("issue_key"), values))

        for record in conn.execute(
            text(
                "SELECT vendor_name, customer_display, redistribution, reviewed_at"
                " FROM vendor_usage ORDER BY vendor_name"
            )
        ).mappings():
            rows.extend(
                _rows(
                    "vendors",
                    record["vendor_name"],
                    {
                        "customer_display": record["customer_display"],
                        "redistribution": record["redistribution"],
                        "reviewed": record["reviewed_at"] or "",
                    },
                )
            )

    frame = pd.DataFrame(rows, columns=COLUMNS)
    frame["value"] = frame["value"].fillna("").astype(str)
    return frame.sort_values(COLUMNS[:3], kind="stable").reset_index(drop=True)


def _rows(section, subject, values):
    return [
        {"section": section, "subject": subject, "metric": metric, "value": value}
        for metric, value in values.items()
    ]


def _subjects(frame, section):
    return list(dict.fromkeys(frame[frame.section == section].subject))


def _metric(frame, section, subject, name):
    found = frame[
        (frame.section == section) & (frame.subject == subject) & (frame.metric == name)
    ]["value"]
    return found.iloc[0] if not found.empty else ""


def _count(frame, section, subject, name):
    value = _metric(frame, section, subject, name)
    return float(value) if value not in ("", None) else 0.0


def render(frame, path):
    """Draw the preview the README shows, from the extract in the same run."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(2, 2, figsize=(13.5, 8.0), constrained_layout=True)
    figure.suptitle("Governance scorecard and issue queue", fontsize=15, weight="bold")

    _verification_panel(axes[0][0], frame)
    _coverage_panel(axes[0][1], frame)
    _table_panel(
        axes[1][0],
        "Issue queue",
        frame,
        "issues",
        ISSUE_METRICS + ["next_action"],
        note="one row per open issue",
    )
    _table_panel(
        axes[1][1],
        "Vendor display and redistribution rights",
        frame,
        "vendors",
        VENDOR_METRICS,
        note="unconfirmed means not reviewed, not refused",
    )

    figure.text(
        0.5,
        0.005,
        "Rendered by metadata/dashboard.py from reports/governance_dashboard.csv. "
        "Catalog fixtures, not a hosted BI asset -- connection steps in docs/dashboard.md.",
        ha="center",
        fontsize=8,
        color="#666666",
    )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=140)
    plt.close(figure)
    return path


def _verification_panel(ax, frame):
    """The point of the whole project: coverage says documented, not verified."""
    labels, confirmed, drafted, totals = [], [], [], []
    for subject, label in VERIFICATION_OBJECTS:
        yes = _count(frame, "verification", subject, "confirmed")
        no = _count(frame, "verification", subject, "draft")
        unknown = _count(frame, "verification", subject, "unknown")
        labels.append(label)
        confirmed.append(yes)
        drafted.append(no + unknown)
        totals.append(yes + no + unknown)
    ax.barh(
        range(len(labels)),
        confirmed,
        height=0.5,
        color=STATUS_COLOURS["confirmed"],
        label="confirmed",
    )
    ax.barh(
        range(len(labels)),
        drafted,
        height=0.5,
        left=confirmed,
        color=STATUS_COLOURS["draft"],
        label="draft",
    )
    for position, (yes, total) in enumerate(zip(confirmed, totals)):
        ax.text(total + 0.4, position, f"{yes:g} of {total:g}", va="center", fontsize=9)
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=9)
    ax.invert_yaxis()
    ax.set_xlim(0, max(totals or [1]) * 1.25)
    ax.set_title(
        "What has actually been checked", fontsize=11, weight="bold", loc="left"
    )
    ax.legend(fontsize=8, frameon=False, loc="lower right")
    _clean(ax)


def _coverage_panel(ax, frame):
    """Physical conformity per dataset -- the number that is easy to misread."""
    datasets = _subjects(frame, "scorecard")
    documented = [
        _count(frame, "scorecard", name, "documented_count") for name in datasets
    ]
    flagged = [
        _count(frame, "scorecard", name, "undocumented_count")
        + _count(frame, "scorecard", name, "type_mismatch_count")
        + _count(frame, "scorecard", name, "deprecated_count")
        for name in datasets
    ]
    ax.barh(
        range(len(datasets)),
        documented,
        height=0.5,
        color="#3f6d9e",
        label="documented",
    )
    ax.barh(
        range(len(datasets)),
        flagged,
        height=0.5,
        left=documented,
        color="#b3261e",
        label="needs attention",
    )
    for position, (good, bad) in enumerate(zip(documented, flagged)):
        share = 100 * good / (good + bad) if good + bad else 0
        ax.text(good + bad + 0.4, position, f"{share:.0f}%", va="center", fontsize=9)
    ax.set_yticks(range(len(datasets)))
    ax.set_yticklabels(datasets, fontsize=9)
    ax.invert_yaxis()
    ax.set_xlim(0, max([sum(pair) for pair in zip(documented, flagged)] or [1]) * 1.15)
    ax.set_title("Schema coverage per dataset", fontsize=11, weight="bold", loc="left")
    ax.legend(fontsize=8, frameon=False, loc="lower right")
    _clean(ax)


def _table_panel(ax, title, frame, section, metrics, note=""):
    ax.axis("off")
    ax.set_title(title, fontsize=11, weight="bold", loc="left")
    subjects = _subjects(frame, section)
    if not subjects:
        ax.text(0.0, 0.5, "nothing in this section", fontsize=9, color="#666666")
        return
    cells = [
        [_metric(frame, section, subject, metric) for metric in metrics]
        for subject in subjects
    ]
    table = ax.table(
        cellText=cells,
        rowLabels=subjects,
        colLabels=metrics,
        loc="upper left",
        fontsize=7.5,
        cellLoc="left",
    )
    table.auto_set_font_size(False)
    for (row, column), cell in table.get_celld().items():
        cell.set_edgecolor("#dddddd")
        if row == 0:
            cell.set_facecolor("#eeeeee")
            cell.set_text_props(weight="bold")
        elif 0 < column < len(metrics) and row <= len(cells):
            text_value = str(cell.get_text().get_text())
            if text_value in STATUS_COLOURS:
                cell.set_text_props(color=STATUS_COLOURS[text_value], weight="bold")
    if note:
        ax.text(0.0, -0.06, note, fontsize=7.5, color="#666666")


def _clean(ax):
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)


def run(engine=None):
    engine = engine or get_engine()
    frame = extract(engine)
    EXTRACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(EXTRACT_PATH, index=False, encoding="utf-8", lineterminator="\n")
    render(frame, PREVIEW_PATH)
    print(
        f"[dashboard] {len(frame)} rows -> {EXTRACT_PATH.relative_to(ROOT)} and "
        f"{PREVIEW_PATH.relative_to(ROOT)}"
    )
    return frame


if __name__ == "__main__":
    run()
