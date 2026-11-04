"""Operational governance records. Nothing is confirmed merely by loading it."""

import argparse
from datetime import date, datetime, timezone
from pathlib import Path
import pandas as pd
from sqlalchemy import text
from config.db_config import get_engine

ROOT = Path(__file__).resolve().parents[1]


def seed_records(engine):
    with engine.begin() as conn:
        fields = (
            conn.execute(
                text(
                    "SELECT dataset_id,field_name FROM data_dictionary WHERE is_current=1"
                )
            )
            .mappings()
            .all()
        )
        for row in fields:
            params = dict(row)
            if not conn.execute(
                text(
                    "SELECT 1 FROM field_verification WHERE dataset_id=:dataset_id AND field_name=:field_name"
                ),
                params,
            ).first():
                conn.execute(
                    text(
                        "INSERT INTO field_verification(dataset_id,field_name,status) VALUES (:dataset_id,:field_name,'draft')"
                    ),
                    params,
                )
        for row in pd.read_csv(
            ROOT / "data/raw/vendor_usage.csv", keep_default_na=False
        ).to_dict("records"):
            if not conn.execute(
                text("SELECT 1 FROM vendor_usage WHERE vendor_name=:vendor_name"), row
            ).first():
                row = {k: (v or None) for k, v in row.items()}
                conn.execute(
                    text(
                        "INSERT INTO vendor_usage ("
                        + ",".join(row)
                        + ") VALUES ("
                        + ",".join(":" + k for k in row)
                        + ")"
                    ),
                    row,
                )
        ds = conn.execute(
            text(
                "SELECT dataset_id FROM datasets WHERE dataset_name='credit_datamart_sme_loans'"
            )
        ).scalar_one()
        for key, field, description, action in [
            (
                "CALC_RATIO",
                "dti_ratio",
                "Stored values disagree with the documented loan/revenue formula; some denominators are missing or zero.",
                "Review reports/calculation_validation.csv; obtain business definition before changing values.",
            ),
            (
                "PRICING_GRID",
                "interest_rate",
                "Pricing grid and tenure-discount schedule are not supplied.",
                "Obtain the approved grid and version; keep calculation unverified.",
            ),
            (
                "SCORE_TIMING",
                "commercial_credit_score",
                "April 1 sample scores precede the illustrative April 2 definition change.",
                "Resolve observation date versus effective date with the source owner; do not infer a vendor change.",
            ),
            (
                "SOURCE_EVIDENCE",
                None,
                "External source dataset/field identities and usage rights are illustrative and unverified.",
                "Obtain source specifications and agreement evidence; review draft mappings.",
            ),
        ]:
            if not conn.execute(
                text("SELECT 1 FROM governance_issues WHERE issue_key=:key"),
                {"key": key},
            ).first():
                conn.execute(
                    text(
                        """INSERT INTO governance_issues(issue_key,dataset_id,field_name,category,priority,owner,description,next_action,evidence_ref)
                    VALUES (:key,:ds,:field,'manual_review','high','Portfolio maintainer',:description,:action,'docs/investigation.md')"""
                    ),
                    dict(
                        key=key,
                        ds=ds,
                        field=field,
                        description=description,
                        action=action,
                    ),
                )


def sync_schema_issues(engine):
    """Reopen recurring schema findings, close only those proven absent this run."""
    with engine.begin() as conn:
        rows = (
            conn.execute(text("SELECT * FROM vw_data_dictionary_reconciliation"))
            .mappings()
            .all()
        )
        observed = set()
        for row in rows:
            if row["governance_status"] == "DOCUMENTED":
                continue
            key = f"schema:{row['dataset_id']}:{row['field_name']}:{row['governance_status']}"
            observed.add(key)
            exists = conn.execute(
                text("SELECT status FROM governance_issues WHERE issue_key=:key"),
                {"key": key},
            ).scalar()
            if exists:
                if exists == "resolved":
                    conn.execute(
                        text(
                            "UPDATE governance_issues SET status='open',closure_evidence=NULL,resolved_at=NULL WHERE issue_key=:key"
                        ),
                        {"key": key},
                    )
            else:
                conn.execute(
                    text(
                        """INSERT INTO governance_issues(issue_key,dataset_id,field_name,category,priority,owner,description,next_action,evidence_ref)
                    VALUES (:key,:ds,:field,'schema','high','Portfolio maintainer',:description,'Review current schema and version the approved definition.','vw_data_dictionary_reconciliation')"""
                    ),
                    dict(
                        key=key,
                        ds=row["dataset_id"],
                        field=row["field_name"],
                        description=row["governance_status"],
                    ),
                )
        for row in conn.execute(
            text(
                "SELECT issue_key FROM governance_issues WHERE category='schema' AND status<>'resolved'"
            )
        ).mappings():
            if row["issue_key"] not in observed:
                conn.execute(
                    text(
                        "UPDATE governance_issues SET status='resolved',closure_evidence='No longer present in current schema reconciliation',resolved_at=CURRENT_TIMESTAMP WHERE issue_key=:key"
                    ),
                    {"key": row["issue_key"]},
                )


def set_verification(
    engine, dataset_id, field, status, evidence="", reviewer="", reviewed=None
):
    if status not in {"draft", "confirmed"}:
        raise ValueError("Invalid status")
    if status == "confirmed" and not (
        evidence.strip() and reviewer.strip() and reviewed
    ):
        raise ValueError("Confirmation requires evidence, reviewer and review date")
    if reviewed:
        reviewed = date.fromisoformat(reviewed)
        if reviewed > date.today():
            raise ValueError("Review date cannot be in the future")
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """UPDATE field_verification SET status=:status,evidence_ref=:evidence,
            verified_by=:reviewer,verified_at=:reviewed WHERE dataset_id=:ds AND field_name=:field"""
            ),
            dict(
                status=status,
                evidence=evidence or None,
                reviewer=reviewer or None,
                reviewed=reviewed,
                ds=dataset_id,
                field=field,
            ),
        )
        if result.rowcount != 1:
            raise ValueError("Unknown catalog field")


def export_records(engine):
    target = ROOT / "reports/local"
    target.mkdir(parents=True, exist_ok=True)
    for table in [
        "field_verification",
        "governance_issues",
        "vendor_usage",
        "lineage_change_log",
    ]:
        pd.read_sql(text(f"SELECT * FROM {table}"), engine).to_csv(
            target / f"{table}.csv", index=False, encoding="utf-8", lineterminator="\n"
        )


def transition_issue(engine, key, status, evidence=""):
    if status not in {"open", "in_progress", "resolved"}:
        raise ValueError("Invalid issue status")
    if status == "resolved" and not evidence.strip():
        raise ValueError("Resolution needs evidence")
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "UPDATE governance_issues SET status=:status,closure_evidence=:evidence,resolved_at=:resolved WHERE issue_key=:key"
            ),
            dict(
                status=status,
                evidence=evidence or None,
                resolved=(
                    datetime.now(timezone.utc).replace(tzinfo=None)
                    if status == "resolved"
                    else None
                ),
                key=key,
            ),
        )
        if result.rowcount != 1:
            raise ValueError("Unknown issue")


def update_vendor(engine, vendor, **changes):
    allowed = {
        "dataset_scope",
        "permitted_use",
        "restrictions",
        "customer_display",
        "redistribution",
        "agreement_ref",
        "terms_url",
        "reviewed_at",
        "renewal_date",
        "owner",
        "open_question",
    }
    if not changes or not set(changes) <= allowed:
        raise ValueError("Unknown vendor fields")
    with engine.begin() as conn:
        old = (
            conn.execute(
                text("SELECT * FROM vendor_usage WHERE vendor_name=:name"),
                {"name": vendor},
            )
            .mappings()
            .one()
        )
        merged = {**dict(old), **changes}
        for field in ["customer_display", "redistribution"]:
            if merged[field] not in {"unconfirmed", "permitted", "prohibited"}:
                raise ValueError("Invalid permission status")
        if any(
            merged[f] != "unconfirmed" for f in ["customer_display", "redistribution"]
        ):
            if not (merged["agreement_ref"] and merged["reviewed_at"]):
                raise ValueError(
                    "Reviewed rights require agreement evidence and review date"
                )
        for field in ["reviewed_at", "renewal_date"]:
            if merged[field]:
                date.fromisoformat(str(merged[field]))
        if (
            merged["reviewed_at"]
            and date.fromisoformat(str(merged["reviewed_at"])) > date.today()
        ):
            raise ValueError("Review date cannot be future")
        conn.execute(
            text(
                "UPDATE vendor_usage SET "
                + ",".join(k + "=:" + k for k in changes)
                + " WHERE vendor_name=:name"
            ),
            {**changes, "name": vendor},
        )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--field", required=True)
    p.add_argument("--dataset-id", type=int, required=True)
    p.add_argument("--status", choices=["draft", "confirmed"], required=True)
    p.add_argument("--evidence", default="")
    p.add_argument("--reviewer", default="")
    p.add_argument("--reviewed")
    a = p.parse_args()
    set_verification(
        get_engine(),
        a.dataset_id,
        a.field,
        a.status,
        a.evidence,
        a.reviewer,
        a.reviewed,
    )
