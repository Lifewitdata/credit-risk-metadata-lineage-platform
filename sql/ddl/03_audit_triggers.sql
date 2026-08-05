-- ============================================================================
-- 03_audit_triggers.sql
--
-- Purpose: Guarantee that every change to data_dictionary is captured in
--          metadata_audit_log, at the DATABASE layer -- not the application
--          layer. This matters because application-level logging can be
--          skipped by a bug, a bypassed code path, or someone running SQL
--          by hand; a trigger cannot be bypassed by anything that goes
--          through MySQL. This is the standard for anything touching
--          credit risk metadata in a regulated environment.
--
-- Two triggers cover the two ways a row in data_dictionary changes:
--   AFTER INSERT -> a brand-new field OR a new SCD2 version was added
--   AFTER UPDATE -> an existing version was closed (valid_to / is_current set)
-- We deliberately do not allow DELETE on this table (see REVOKE below) --
-- history must never be erased, only superseded.
--
-- IMPORTANT: MySQL triggers do NOT support a SQL SECURITY INVOKER clause
-- (that only exists for views and stored procedures). Triggers always
-- execute with the privileges -- and CURRENT_USER() identity -- of whoever
-- CREATED the trigger. If we used CURRENT_USER() inside the trigger body,
-- every audit entry would be attributed to the DBA who ran this script,
-- not the application user who actually changed the data. The fix: use
-- MySQL's USER() function instead, which always reflects the actual
-- connected client regardless of definer/invoker security context.
-- ============================================================================

USE credit_metadata_platform;

DELIMITER $$

DROP TRIGGER IF EXISTS trg_data_dictionary_after_insert$$
CREATE TRIGGER trg_data_dictionary_after_insert
AFTER INSERT ON data_dictionary
FOR EACH ROW
BEGIN
    INSERT INTO metadata_audit_log (table_name, record_pk, action_type, changed_by, new_value_json)
    VALUES (
        'data_dictionary',
        NEW.dictionary_id,
        'INSERT',
        USER(),
        JSON_OBJECT(
            'dataset_id', NEW.dataset_id,
            'field_name', NEW.field_name,
            'description', NEW.description,
            'data_type', NEW.data_type,
            'valid_range', NEW.valid_range,
            'business_owner', NEW.business_owner,
            'valid_from', NEW.valid_from,
            'valid_to', NEW.valid_to,
            'is_current', NEW.is_current
        )
    );
END$$

DROP TRIGGER IF EXISTS trg_data_dictionary_after_update$$
CREATE TRIGGER trg_data_dictionary_after_update
AFTER UPDATE ON data_dictionary
FOR EACH ROW
BEGIN
    INSERT INTO metadata_audit_log (table_name, record_pk, action_type, changed_by, old_value_json, new_value_json)
    VALUES (
        'data_dictionary',
        NEW.dictionary_id,
        'CLOSE',
        USER(),
        JSON_OBJECT(
            'description', OLD.description,
            'data_type', OLD.data_type,
            'valid_to', OLD.valid_to,
            'is_current', OLD.is_current
        ),
        JSON_OBJECT(
            'description', NEW.description,
            'data_type', NEW.data_type,
            'valid_to', NEW.valid_to,
            'is_current', NEW.is_current
        )
    );
END$$

DELIMITER ;

-- History must only ever be superseded, never erased. Revoke DELETE from the
-- application role. Scope must match how the privilege was originally
-- granted in Step 2 (database-level GRANT ALL ON credit_metadata_platform.*)
-- -- MySQL will not let you revoke a table-level privilege that was granted
-- at the database level, so the revoke has to happen at that same level.
-- In this project no workflow ever needs to delete a row from ANY governed
-- table (dictionary history is superseded, not erased; lineage/audit tables
-- are append-only), so a schema-wide revoke is the correct, safe scope here.
REVOKE DELETE ON credit_metadata_platform.* FROM 'metadata_platform_user'@'localhost';
