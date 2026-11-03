-- Dictionary change evidence. Administrators can alter triggers/logs; this is not tamper-proof storage.
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
            'known_issues', NEW.known_issues,
            'data_type', NEW.data_type,
            'valid_range', NEW.valid_range,
            'business_owner', NEW.business_owner,
            'valid_from', NEW.valid_from,
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
        IF(OLD.is_current=1 AND NEW.is_current=0, 'CLOSE', 'UPDATE'),
        USER(),
        JSON_OBJECT(
            'description', OLD.description,
            'known_issues', OLD.known_issues,
            'data_type', OLD.data_type,
            'valid_from', OLD.valid_from,
            'valid_to', OLD.valid_to,
            'is_current', OLD.is_current
        ),
        JSON_OBJECT(
            'description', NEW.description,
            'known_issues', NEW.known_issues,
            'data_type', NEW.data_type,
            'valid_from', NEW.valid_from,
            'valid_to', NEW.valid_to,
            'is_current', NEW.is_current
        )
    );
END$$

DELIMITER ;
