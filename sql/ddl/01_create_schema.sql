-- ============================================================================
-- Credit Risk Metadata & Data Lineage Management Platform
-- 01_create_schema.sql
--
-- Purpose : Normalized (3NF) MySQL schema for metadata governance,
--           data dictionary version control, and field-level lineage.
-- Engine  : InnoDB (required for FK integrity + transactional audit triggers)
-- Charset : utf8mb4 (safe default for free-text business descriptions)
-- ============================================================================

CREATE DATABASE IF NOT EXISTS credit_metadata_platform
    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE credit_metadata_platform;

-- ----------------------------------------------------------------------------
-- 1. source_systems
--    Registry of every upstream system that supplies data into any dataset.
--    Business purpose: ownership & escalation contact when a field breaks.
-- ----------------------------------------------------------------------------
CREATE TABLE source_systems (
    source_system_id   INT AUTO_INCREMENT PRIMARY KEY,
    system_name         VARCHAR(100) NOT NULL UNIQUE,
    system_type          ENUM('Internal','Bureau','ThirdParty','Manual') NOT NULL,
    owner_team           VARCHAR(100),
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- ----------------------------------------------------------------------------
-- 2. datasets
--    Registry of data assets (tables/datamarts) governed by this platform.
-- ----------------------------------------------------------------------------
CREATE TABLE datasets (
    dataset_id            INT AUTO_INCREMENT PRIMARY KEY,
    dataset_name           VARCHAR(150) NOT NULL UNIQUE,
    physical_table_name    VARCHAR(150) NOT NULL COMMENT 'Actual MySQL table name holding the data',
    description             TEXT,
    source_system_id        INT,
    refresh_frequency        VARCHAR(50) COMMENT 'e.g. Monthly, Daily, On-Demand',
    business_owner            VARCHAR(100),
    created_at                 TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_dataset_source
        FOREIGN KEY (source_system_id) REFERENCES source_systems(source_system_id)
) ENGINE=InnoDB;

-- ----------------------------------------------------------------------------
-- 3. data_dictionary  (SLOWLY CHANGING DIMENSION - TYPE 2)
--    The *business-approved* definition of each field, versioned over time.
--    Never UPDATE a row's definition in place -> close it and insert a new one.
--    This preserves "what did we say this field meant on date X" for audit.
--
--    current_guard is what makes "exactly one current version per field" a
--    database rule rather than an application promise: it is 1 only while a row
--    is current and NULL otherwise, and NULLs never collide in a unique index,
--    so closed history is unconstrained while a second current row is rejected
--    even by two writers that both passed the read-then-write check.
-- ----------------------------------------------------------------------------
CREATE TABLE data_dictionary (
    dictionary_id        INT AUTO_INCREMENT PRIMARY KEY,
    dataset_id             INT NOT NULL,
    field_name              VARCHAR(100) NOT NULL,
    description               TEXT,
    data_type                  VARCHAR(50),
    valid_range                  VARCHAR(255),
    known_issues                   TEXT,
    business_owner                    VARCHAR(100),
    valid_from                         DATE NOT NULL,
    valid_to                            DATE DEFAULT NULL COMMENT 'NULL = still active',
    is_current                           TINYINT(1) NOT NULL DEFAULT 1,
    current_guard                        TINYINT GENERATED ALWAYS AS (CASE WHEN is_current = 1 THEN 1 ELSE NULL END) STORED,
    created_at                            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_dict_dataset
        FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id),
    INDEX idx_field_current (dataset_id, field_name, is_current),
    UNIQUE KEY uq_dictionary_current (dataset_id, field_name, current_guard)
) ENGINE=InnoDB;

-- ----------------------------------------------------------------------------
-- 4. physical_schema_snapshot
--    Point-in-time capture of what columns ACTUALLY exist in the physical
--    table, scanned from INFORMATION_SCHEMA.COLUMNS. This is the ground
--    truth used to detect undocumented / deprecated / newly-added columns
--    by diffing against data_dictionary (see Step 3).
-- ----------------------------------------------------------------------------
CREATE TABLE physical_schema_snapshot (
    snapshot_id         BIGINT AUTO_INCREMENT PRIMARY KEY,
    dataset_id            INT NOT NULL,
    column_name             VARCHAR(100) NOT NULL,
    physical_data_type        VARCHAR(50),
    ordinal_position             INT,
    scan_timestamp                 TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_snapshot_dataset
        FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id),
    INDEX idx_snapshot_scan (dataset_id, scan_timestamp)
) ENGINE=InnoDB;

-- ----------------------------------------------------------------------------
-- 5. transformation_rules
--    How a target field is derived from one or more source fields.
--    Populates the edges of the lineage graph (Step 5-6).
-- ----------------------------------------------------------------------------
CREATE TABLE transformation_rules (
    rule_id                INT AUTO_INCREMENT PRIMARY KEY,
    rule_name                VARCHAR(150) NOT NULL UNIQUE,
    target_dataset_id           INT NOT NULL,
    target_field                  VARCHAR(100) NOT NULL,
    transformation_type              ENUM('Direct Copy','Calculated','Aggregated','Mapped/Coded','Bureau Feed') NOT NULL,
    transformation_logic                TEXT NOT NULL COMMENT 'Human-readable rule, e.g. dti_ratio = loan_amount_gbp / annual_revenue_gbp',
    business_justification                 TEXT,
    created_by                                VARCHAR(100),
    created_at                                 TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_rule_target_dataset
        FOREIGN KEY (target_dataset_id) REFERENCES datasets(dataset_id)
) ENGINE=InnoDB;

-- ----------------------------------------------------------------------------
-- 6. lineage_nodes
--    Every (dataset, field) pair that participates in lineage, as a graph node.
-- ----------------------------------------------------------------------------
CREATE TABLE lineage_nodes (
    node_id               INT AUTO_INCREMENT PRIMARY KEY,
    dataset_id              INT NOT NULL,
    field_name                VARCHAR(100) NOT NULL,
    node_type                    ENUM('Source Field','Derived Field','External Feed') NOT NULL,
    CONSTRAINT fk_node_dataset
        FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id),
    UNIQUE KEY uq_node (dataset_id, field_name)
) ENGINE=InnoDB;

-- ----------------------------------------------------------------------------
-- 7. lineage_edges
--    Directed edges: source_node -> target_node, optionally tied to the
--    transformation_rule that explains *why* the edge exists.
-- ----------------------------------------------------------------------------
CREATE TABLE lineage_edges (
    edge_id                 INT AUTO_INCREMENT PRIMARY KEY,
    source_node_id             INT NOT NULL,
    target_node_id                INT NOT NULL,
    rule_id                          INT,
    CONSTRAINT fk_edge_source
        FOREIGN KEY (source_node_id) REFERENCES lineage_nodes(node_id),
    CONSTRAINT fk_edge_target
        FOREIGN KEY (target_node_id) REFERENCES lineage_nodes(node_id),
    CONSTRAINT fk_edge_rule
        FOREIGN KEY (rule_id) REFERENCES transformation_rules(rule_id),
    UNIQUE KEY uq_edge (source_node_id, target_node_id)
) ENGINE=InnoDB;

-- ----------------------------------------------------------------------------
-- 8. metadata_audit_log
--    Append-only audit trail. Populated by triggers on data_dictionary
--    (Step 4). Regulators expect this to be immutable at the application
--    layer -- only INSERTs are ever performed against this table.
-- ----------------------------------------------------------------------------
CREATE TABLE metadata_audit_log (
    log_id             BIGINT AUTO_INCREMENT PRIMARY KEY,
    table_name           VARCHAR(100) NOT NULL,
    record_pk               INT NOT NULL,
    action_type                ENUM('INSERT','UPDATE','CLOSE') NOT NULL,
    changed_by                    VARCHAR(100) DEFAULT (USER()) COMMENT 'USER(), not CURRENT_USER() -- USER() reflects the actual connecting client even inside a definer-context trigger; CURRENT_USER() would return the trigger creator instead',
    changed_at                       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    old_value_json                      JSON,
    new_value_json                         JSON
) ENGINE=InnoDB;
