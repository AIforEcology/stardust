-- The corrections triage agent gets its own scope, corrections:write, instead of needing admin
-- (models.CORRECTIONS_WRITE). Widen the list of scopes a key may hold.

ALTER TABLE api_keys DROP CONSTRAINT api_keys_scopes_check;
ALTER TABLE api_keys ADD CONSTRAINT api_keys_scopes_check
  CHECK (scopes <@ ARRAY['observations:write', 'facilities:write', 'tracker:write', 'runlog:write',
                         'corrections:write', 'admin']::text[]
         AND cardinality(scopes) > 0);
