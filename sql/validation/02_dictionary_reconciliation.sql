-- ============================================================================
-- 02_dictionary_reconciliation.sql
--
-- Purpose: Reconcile business-documented fields (data_dictionary) against
--          the physically observed schema (physical_schema_snapshot) to
--          classify every field as DOCUMENTED / UNDOCUMENTED / DEPRECATED,
--          and separately flag TYPE_MISMATCH for fields present in both
--          but described inconsistently.
--
-- MySQL has no FULL OUTER JOIN -- this is emulated as a UNION of a
-- dictionary-anchored LEFT JOIN and a snapshot-anchored LEFT JOIN
-- (with the overlap excluded from the second branch via WHERE ... IS NULL).
-- ============================================================================

USE credit_metadata_platform;

-- ----------------------------------------------------------------------------
-- View 1: latest physical schema snapshot per dataset.
-- physical_schema_snapshot accumulates a new set of rows every time the ETL
-- scans INFORMATION_SCHEMA (Step 2, stage 5) -- this view always resolves to
-- the most recent scan per dataset, so validation reflects current reality.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW vw_latest_schema_snapshot AS
SELECT s.dataset_id, s.column_name, s.physical_data_type, s.ordinal_position, s.scan_timestamp
FROM physical_schema_snapshot s
INNER JOIN (
    SELECT dataset_id, MAX(scan_timestamp) AS max_scan
    FROM physical_schema_snapshot
    GROUP BY dataset_id
) latest
    ON s.dataset_id = latest.dataset_id
   AND s.scan_timestamp = latest.max_scan;


-- ----------------------------------------------------------------------------
-- View 2: dictionary <-> physical schema reconciliation.
--
-- normalized_documented_type / normalized_physical_type collapse both type
-- systems into a shared vocabulary (TEXT / NUMERIC_INT / NUMERIC_FLOAT /
-- DATETIME) so a documented "integer" can be fairly compared against a
-- physical "bigint" without a brittle exact-string match.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW vw_data_dictionary_reconciliation AS

-- Branch A: every currently-documented field, LEFT JOINed to the physical
-- snapshot. Covers DOCUMENTED and DEPRECATED cases.
SELECT
    dd.dataset_id,
    dd.field_name,
    dd.data_type                                   AS documented_data_type,
    ps.physical_data_type                          AS actual_data_type,
    dd.valid_from                                  AS documented_since,
    CASE
        WHEN ps.column_name IS NULL THEN 'DEPRECATED'
        WHEN LOWER(dd.data_type) IN ('string')
             AND LOWER(ps.physical_data_type) NOT IN ('text','varchar','char') THEN 'TYPE_MISMATCH'
        WHEN LOWER(dd.data_type) IN ('integer')
             AND LOWER(ps.physical_data_type) NOT IN ('int','bigint','smallint','tinyint','mediumint') THEN 'TYPE_MISMATCH'
        WHEN LOWER(dd.data_type) IN ('float','decimal')
             AND LOWER(ps.physical_data_type) NOT IN ('float','double','decimal') THEN 'TYPE_MISMATCH'
        WHEN LOWER(dd.data_type) IN ('date','datetime')
             AND LOWER(ps.physical_data_type) NOT IN ('date','datetime','timestamp') THEN 'TYPE_MISMATCH'
        ELSE 'DOCUMENTED'
    END AS governance_status
FROM data_dictionary dd
LEFT JOIN vw_latest_schema_snapshot ps
    ON ps.dataset_id = dd.dataset_id AND ps.column_name = dd.field_name
WHERE dd.is_current = 1

UNION ALL

-- Branch B: every physical column with no current dictionary match.
-- Covers UNDOCUMENTED. The WHERE dd.field_name IS NULL clause is what
-- prevents double-counting rows already returned by Branch A.
SELECT
    ps.dataset_id,
    ps.column_name                                 AS field_name,
    NULL                                            AS documented_data_type,
    ps.physical_data_type                           AS actual_data_type,
    NULL                                             AS documented_since,
    'UNDOCUMENTED'                                    AS governance_status
FROM vw_latest_schema_snapshot ps
LEFT JOIN data_dictionary dd
    ON dd.dataset_id = ps.dataset_id
   AND dd.field_name = ps.column_name
   AND dd.is_current = 1
WHERE dd.field_name IS NULL;


-- ----------------------------------------------------------------------------
-- View 3: dataset-level governance scorecard.
-- Rolls the reconciliation up into a single "documentation coverage %"
-- number per dataset -- the kind of KPI that goes on a data governance
-- dashboard for a Chief Data Officer.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW vw_governance_scorecard AS
SELECT
    d.dataset_id,
    d.dataset_name,
    COUNT(*)                                                                   AS total_fields,
    SUM(r.governance_status = 'DOCUMENTED')                                    AS documented_count,
    SUM(r.governance_status = 'UNDOCUMENTED')                                  AS undocumented_count,
    SUM(r.governance_status = 'DEPRECATED')                                    AS deprecated_count,
    SUM(r.governance_status = 'TYPE_MISMATCH')                                 AS type_mismatch_count,
    ROUND(100 * SUM(r.governance_status = 'DOCUMENTED') / COUNT(*), 1)         AS documentation_coverage_pct
FROM vw_data_dictionary_reconciliation r
JOIN datasets d ON d.dataset_id = r.dataset_id
GROUP BY d.dataset_id, d.dataset_name;
