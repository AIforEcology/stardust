-- Agents may re-assess some fields of rows that are not Reviewed or Published (models.AGENT_REFRESH).
-- Every such update is audited like an admin one: the API sets ledger.key_id in every write
-- transaction, and ledger.change_source to the source the agent gave for the change.

ALTER TABLE audit_log ADD COLUMN source_url text;  -- the source given for the change, if any

CREATE OR REPLACE FUNCTION ledger_audit() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  rid text;
  who text := nullif(current_setting('ledger.key_id', true), '');
BEGIN
  IF NOT ledger_is_admin() AND who IS NULL THEN
    RETURN NULL;
  END IF;
  rid := coalesce(to_jsonb(NEW) ->> TG_ARGV[0], to_jsonb(OLD) ->> TG_ARGV[0]);
  INSERT INTO audit_log (key_id, action, table_name, row_id, before, after, source_url)
  VALUES (coalesce(who, 'unknown'), lower(TG_OP), TG_TABLE_NAME, rid,
          CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE to_jsonb(OLD) END,
          CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE to_jsonb(NEW) END,
          nullif(current_setting('ledger.change_source', true), ''));
  RETURN NULL;
END
$$;
