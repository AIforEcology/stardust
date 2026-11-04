-- Data Center Resource Ledger: initial schema.
-- Tables mirror the Google Sheet's tabs; column comments name the sheet column each one comes from.
-- Applied once, in a transaction, by ledger.db.migrate(), and recorded in schema_migrations.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------------------------------------------------------------------------------------------
-- Helpers
-- ---------------------------------------------------------------------------------------------

-- True only inside a transaction the API opened for an admin caller (SET LOCAL ledger.admin = 'on').
CREATE FUNCTION ledger_is_admin() RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT coalesce(current_setting('ledger.admin', true), '') = 'on'
$$;

-- IDs never change and never repeat. The numeric part is counted per (prefix, country, state);
-- for sequence-style IDs (OBS-0001, CLM-0001 ...) country and state are ''. The UPSERT takes a row
-- lock, so concurrent callers queue; the counter lives in the caller's transaction, so a rollback
-- also gives the number back (no gaps).
CREATE TABLE id_counters (
  prefix      text NOT NULL,
  country     text NOT NULL DEFAULT '',
  state       text NOT NULL DEFAULT '',
  last_number integer NOT NULL CHECK (last_number >= 0),
  PRIMARY KEY (prefix, country, state)
);

CREATE FUNCTION ledger_next_number(p text, c text, s text) RETURNS integer LANGUAGE sql AS $$
  INSERT INTO id_counters AS ic (prefix, country, state, last_number) VALUES (p, c, s, 1)
  ON CONFLICT (prefix, country, state) DO UPDATE SET last_number = ic.last_number + 1
  RETURNING last_number
$$;

-- Rows with review status Reviewed or Published change only in an admin transaction.
CREATE FUNCTION ledger_protect_reviewed() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF ledger_is_admin() THEN
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
  END IF;
  IF TG_OP IN ('UPDATE', 'DELETE') AND OLD.review_status IN ('Reviewed', 'Published') THEN
    RAISE EXCEPTION 'this % row is %, so only an admin key may change it', TG_TABLE_NAME, OLD.review_status
      USING ERRCODE = 'LD001';
  END IF;
  IF TG_OP IN ('INSERT', 'UPDATE') AND NEW.review_status IN ('Reviewed', 'Published') THEN
    RAISE EXCEPTION 'only an admin key may mark a % row %', TG_TABLE_NAME, NEW.review_status
      USING ERRCODE = 'LD001';
  END IF;
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END
$$;

-- Every change made in an admin transaction is written to audit_log. TG_ARGV[0] names the key column.
CREATE FUNCTION ledger_audit() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  rid text;
BEGIN
  IF NOT ledger_is_admin() THEN
    RETURN NULL;
  END IF;
  rid := coalesce(to_jsonb(NEW) ->> TG_ARGV[0], to_jsonb(OLD) ->> TG_ARGV[0]);
  INSERT INTO audit_log (key_id, action, table_name, row_id, before, after)
  VALUES (coalesce(nullif(current_setting('ledger.key_id', true), ''), 'unknown'), lower(TG_OP), TG_TABLE_NAME, rid,
          CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE to_jsonb(OLD) END,
          CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE to_jsonb(NEW) END);
  RETURN NULL;
END
$$;

CREATE FUNCTION ledger_touch() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END
$$;

-- ---------------------------------------------------------------------------------------------
-- Keys and audit
-- ---------------------------------------------------------------------------------------------

-- Same pattern as Core's api_keys: only a SHA-256 hash of the key is stored.
CREATE TABLE api_keys (
  key_id     text PRIMARY KEY,
  key_hash   text NOT NULL,
  scopes     text[] NOT NULL CHECK (scopes <@ ARRAY['observations:write', 'facilities:write', 'tracker:write',
                                                    'runlog:write', 'admin']::text[]
                                    AND cardinality(scopes) > 0),
  label      text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  revoked_at timestamptz
);

CREATE TABLE audit_log (
  id         bigserial PRIMARY KEY,
  at         timestamptz NOT NULL DEFAULT now(),
  key_id     text NOT NULL,            -- api_keys.key_id, or 'admin-token' for the server's bootstrap token
  action     text NOT NULL,            -- insert / update / delete / import / create_key / revoke_key
  table_name text NOT NULL,
  row_id     text,
  before     jsonb,
  after      jsonb
);
CREATE INDEX audit_log_row ON audit_log (table_name, row_id);

-- ---------------------------------------------------------------------------------------------
-- Facilities (sheet tabs: Facilities, US Master List)
-- ---------------------------------------------------------------------------------------------

CREATE TABLE facilities (
  facility_id        text PRIMARY KEY CHECK (facility_id ~ '^(DC|PWR)-[A-Z]{2}-[A-Z0-9]{1,3}-[0-9]{4,}$'),
  name               text NOT NULL,                 -- Name
  facility_type      text,                          -- Facility type / Type
  operator           text,                          -- Operator / developer / Operator
  tenant             text,                          -- Tenant / Offtaker / tenant
  status             text,                          -- Latest status / Status
  city               text,
  county             text,
  state              text NOT NULL CHECK (state ~ '^[A-Z]{2}-[A-Z0-9]{1,3}$'),  -- State (ISO 3166-2), e.g. US-TX
  country            char(2) NOT NULL CHECK (country ~ '^[A-Z]{2}$'),          -- Country (ISO)
  county_fips        char(5) CHECK (county_fips ~ '^[0-9]{5}$'),
  geom               geography(Point, 4326),        -- Latitude / Longitude (WGS84)
  location_precision text,                          -- Exact site, Parcel, Approximate, City, County, State
  coordinate_source  text,
  parcel_id          text,
  epa_frs_id         text,
  eia_plant_ids      text[] NOT NULL DEFAULT '{}',  -- EIA plant ID(s)
  utility            text,
  grid_region        text,
  huc8               text,                          -- Watershed (HUC-8)
  pwsid              text,                          -- Water supplier (PWSID)
  compute_atlas_id   text,
  peeringdb_ids      text[] NOT NULL DEFAULT '{}',
  epoch_name         text,                          -- Epoch AI name
  operational_mw     numeric CHECK (operational_mw >= 0),
  planned_mw         numeric CHECK (planned_mw >= 0),
  epoch_power_mw     numeric CHECK (epoch_power_mw >= 0),
  source_confidence  text,                          -- Confidence (source), US Master List
  sources            text[] NOT NULL DEFAULT '{}',
  source_record_url  text,
  first_seen         date,
  review_status      text NOT NULL DEFAULT 'Needs review',
  notes              text,
  idempotency_key    text UNIQUE,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  CHECK (substr(state, 1, 2) = country)
);
CREATE INDEX facilities_geom ON facilities USING gist (geom);
CREATE INDEX facilities_state ON facilities (state);
CREATE INDEX facilities_name_trgm ON facilities USING gin (name gin_trgm_ops);
CREATE INDEX facilities_operator_trgm ON facilities USING gin (operator gin_trgm_ops);
CREATE INDEX facilities_compute_atlas_id ON facilities (compute_atlas_id);
CREATE INDEX facilities_peeringdb_ids ON facilities USING gin (peeringdb_ids);
CREATE INDEX facilities_epoch_name ON facilities (epoch_name);
CREATE INDEX facilities_epa_frs_id ON facilities (epa_frs_id);
CREATE INDEX facilities_eia_plant_ids ON facilities USING gin (eia_plant_ids);
CREATE INDEX facilities_pwsid ON facilities (pwsid);
CREATE INDEX facilities_parcel_id ON facilities (parcel_id);
CREATE INDEX facilities_county_fips ON facilities (county_fips);

-- A Master List row that was matched to a curated facility keeps its own ID here, so the ID still
-- resolves and is never handed out again.
CREATE TABLE facility_aliases (
  alias_id    text PRIMARY KEY CHECK (alias_id ~ '^(DC|PWR)-[A-Z]{2}-[A-Z0-9]{1,3}-[0-9]{4,}$'),
  facility_id text NOT NULL REFERENCES facilities ON UPDATE RESTRICT ON DELETE RESTRICT,
  source      text NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- Other names: alternative names, code names and LLCs.
CREATE TABLE facility_names (
  id              bigserial PRIMARY KEY,
  facility_id     text NOT NULL REFERENCES facilities ON UPDATE RESTRICT ON DELETE RESTRICT,
  name            text NOT NULL,
  name_type       text,                              -- e.g. Other name, Code name, LLC
  source_url      text,
  source_title    text,
  review_status   text NOT NULL DEFAULT 'Needs review',
  idempotency_key text UNIQUE,
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (facility_id, name)
);
CREATE INDEX facility_names_trgm ON facility_names USING gin (name gin_trgm_ops);

-- Serves / served by: e.g. a power plant serving a campus.
CREATE TABLE facility_links (
  id               bigserial PRIMARY KEY,
  from_facility_id text NOT NULL REFERENCES facilities ON UPDATE RESTRICT ON DELETE RESTRICT,
  to_facility_id   text NOT NULL REFERENCES facilities ON UPDATE RESTRICT ON DELETE RESTRICT,
  relation         text NOT NULL DEFAULT 'serves',
  link_status      text NOT NULL DEFAULT 'unconfirmed' CHECK (link_status IN ('confirmed', 'unconfirmed')),
  confidence       numeric CHECK (confidence BETWEEN 0 AND 1),
  source_url       text,
  source_title     text,
  notes            text,
  review_status    text NOT NULL DEFAULT 'Needs review',
  idempotency_key  text UNIQUE,
  created_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (from_facility_id, to_facility_id, relation),
  CHECK (from_facility_id <> to_facility_id)
);
CREATE INDEX facility_links_to ON facility_links (to_facility_id);

-- ---------------------------------------------------------------------------------------------
-- Observations (sheet tab: Observations, columns A-T). Append-only.
-- ---------------------------------------------------------------------------------------------

CREATE TABLE observations (
  obs_id          text PRIMARY KEY CHECK (obs_id ~ '^OBS-[0-9]{4,}$'),       -- A  ID
  date_found      date NOT NULL,                                              -- B  Date found
  facility_label  text,                                                       -- C  Facility
  operator_tenant text,                                                       -- D  Operator / tenant
  city_county     text,                                                       -- E  City / county
  state_region    text,                                                       -- F  State / region
  country         text,                                                       -- G  Country
  field_group     text NOT NULL CHECK (field_group IN (
                    'Status & size', 'Power: grid', 'Power: sources', 'Power: on-site', 'Water: supply',
                    'Water: use & discharge', 'Emissions & air', 'Mitigation', 'Economics & community')),  -- H
  field           text NOT NULL,                                              -- I  Field
  value           text,                                                       -- J  Value (NULL when not stated)
  unit            text,                                                       -- K  Unit
  source_title    text,                                                       -- L  Source title
  source_url      text NOT NULL,                                              -- M  Source URL
  source_type     text,                                                       -- N  Source type
  source_date     date,                                                       -- O  Source date
  method          text,                                                       -- P  Method
  confidence      numeric CHECK (confidence BETWEEN 0 AND 1),                 -- Q  Confidence (0-1)
  review_status   text NOT NULL DEFAULT 'Needs review',                       -- R  Review status
  notes           text,                                                       -- S  Notes
  facility_id     text REFERENCES facilities ON UPDATE RESTRICT ON DELETE RESTRICT,  -- T  Facility ID
  supersedes      text REFERENCES observations ON UPDATE RESTRICT ON DELETE RESTRICT,
  idempotency_key text UNIQUE,
  natural_key     text GENERATED ALWAYS AS (md5(coalesce(facility_id, '') || chr(31) || field || chr(31)
                                               || coalesce(value, '') || chr(31) || source_url)) STORED,
  created_at      timestamptz NOT NULL DEFAULT now(),
  CHECK (supersedes IS NULL OR supersedes <> obs_id)
);
CREATE INDEX observations_facility ON observations (facility_id, field_group, field);
CREATE INDEX observations_date_found ON observations (date_found);
CREATE INDEX observations_natural_key ON observations (natural_key);
CREATE INDEX observations_supersedes ON observations (supersedes);

-- Never deleted; changed only by admin, and then only review_status and notes. A correction is a
-- new row whose supersedes points at the old one.
CREATE FUNCTION observations_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'observations are append-only; add a new row with supersedes instead' USING ERRCODE = 'LD002';
  END IF;
  IF NOT ledger_is_admin() THEN
    RAISE EXCEPTION 'observations are append-only; add a new row with supersedes instead' USING ERRCODE = 'LD002';
  END IF;
  IF (to_jsonb(NEW) - 'review_status' - 'notes' - 'natural_key')
     IS DISTINCT FROM (to_jsonb(OLD) - 'review_status' - 'notes' - 'natural_key') THEN
    RAISE EXCEPTION 'only review_status and notes of an observation may change' USING ERRCODE = 'LD002';
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER observations_append_only BEFORE UPDATE OR DELETE ON observations
  FOR EACH ROW EXECUTE FUNCTION observations_append_only();

-- ---------------------------------------------------------------------------------------------
-- Trackers. Each row carries the full provenance set: source_url, source_title, source_type,
-- source_date, method, confidence, review_status, date_found.
-- ---------------------------------------------------------------------------------------------

-- Claims
CREATE TABLE claims (
  claim_id                 text PRIMARY KEY CHECK (claim_id ~ '^CLM-[0-9]{4,}$'),  -- Claim ID
  facility_id              text REFERENCES facilities ON UPDATE RESTRICT ON DELETE RESTRICT,
  facility_label           text,                   -- Facility
  claimant                 text,
  claim_text               text NOT NULL,          -- Claim (exact words)
  claim_type               text,
  claim_date               date,
  record_checked           text,
  evidence_supporting      text,
  evidence_against         text,                   -- Evidence against or complicating
  evidence_observation_ids text[] NOT NULL DEFAULT '{}',
  verdict                  text CHECK (verdict IN ('Verified', 'Consistent', 'Partly verified / missing context',
                                                   'Unverified', 'Contradicted by record')),
  reasoning                text,
  operator_response        text,
  next_step                text,
  last_checked             date,
  source_url               text,                   -- Claim source URL
  source_title             text,                   -- Claim source title
  source_type              text,
  source_date              date,
  method                   text,
  confidence               numeric CHECK (confidence BETWEEN 0 AND 1),
  review_status            text NOT NULL DEFAULT 'Needs review',
  date_found               date,
  idempotency_key          text UNIQUE,
  created_at               timestamptz NOT NULL DEFAULT now(),
  updated_at               timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX claims_facility ON claims (facility_id);

-- Claim log: one row per verdict change.
CREATE TABLE claim_log (
  id              bigserial PRIMARY KEY,
  log_date        date NOT NULL,                   -- Date
  claim_id        text NOT NULL REFERENCES claims ON UPDATE RESTRICT ON DELETE RESTRICT,
  change          text NOT NULL,
  old_verdict     text CHECK (old_verdict IN ('Verified', 'Consistent', 'Partly verified / missing context',
                                              'Unverified', 'Contradicted by record')),
  new_verdict     text CHECK (new_verdict IN ('Verified', 'Consistent', 'Partly verified / missing context',
                                              'Unverified', 'Contradicted by record')),
  evidence_added  text,
  idempotency_key text UNIQUE,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX claim_log_claim ON claim_log (claim_id);

-- Permitted vs actual
CREATE TABLE permit_checks (
  check_id                 text PRIMARY KEY CHECK (check_id ~ '^PVA-[0-9]{4,}$'),
  facility_id              text REFERENCES facilities ON UPDATE RESTRICT ON DELETE RESTRICT,
  facility_label           text,
  metric                   text NOT NULL,
  limit_value              numeric,                -- Limit
  unit                     text,
  limit_type               text,
  limit_source_title       text,
  limit_source_url         text,
  actual_value             numeric,
  actual_as_of             date,
  gap                      numeric GENERATED ALWAYS AS (actual_value - limit_value) STORED,  -- Gap (actual − limit)
  status                   text,
  enforcement_disputes     text,                   -- Enforcement / disputes
  evidence_observation_ids text[] NOT NULL DEFAULT '{}',
  next_step                text,
  last_checked             date,
  source_url               text,                   -- Actual source URL
  source_title             text,                   -- Actual source title
  source_type              text,
  source_date              date,
  method                   text,                   -- Actual method
  confidence               numeric CHECK (confidence BETWEEN 0 AND 1),
  review_status            text NOT NULL DEFAULT 'Needs review',
  date_found               date,
  idempotency_key          text UNIQUE,
  created_at               timestamptz NOT NULL DEFAULT now(),
  updated_at               timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX permit_checks_facility ON permit_checks (facility_id);

-- Water ledger: one row per facility.
CREATE TABLE water_ledger (
  facility_id               text PRIMARY KEY REFERENCES facilities ON UPDATE RESTRICT ON DELETE RESTRICT,
  facility_label            text,
  state                     text,
  cooling_type              text,
  cooling_notes             text,
  supply_source_type        text,
  supplier_name             text,                  -- Supplier / source name
  pwsid                     text,                  -- Water system ID (PWSID)
  allocation                numeric,               -- Allocation or cap
  allocation_unit           text,
  allocation_basis          text,
  projected_use             numeric,               -- Projected or design use
  projected_unit            text,
  actual_use                numeric,               -- Reported actual use
  actual_unit               text,
  actual_period             text,
  actual_basis              text,
  withdrawal_vs_consumption text,
  discharge                 text,                  -- Discharge / wastewater
  reclaimed_water           text,                  -- Reclaimed or alternative water
  drought_terms             text,                  -- Drought / curtailment terms
  groundwater_wells         text,
  basin_water_stress        text,                  -- Basin water stress (WRI Aqueduct)
  -- Completeness (of 6): cooling type known, supply type known, supplier named, a cap or projection,
  -- a reported actual, and discharge details.
  completeness              smallint GENERATED ALWAYS AS (
      (CASE WHEN coalesce(cooling_type, '') NOT IN ('', 'Unknown') THEN 1 ELSE 0 END)
    + (CASE WHEN coalesce(supply_source_type, '') NOT IN ('', 'Unknown') THEN 1 ELSE 0 END)
    + (CASE WHEN coalesce(supplier_name, '') <> '' THEN 1 ELSE 0 END)
    + (CASE WHEN allocation IS NOT NULL OR projected_use IS NOT NULL THEN 1 ELSE 0 END)
    + (CASE WHEN actual_use IS NOT NULL THEN 1 ELSE 0 END)
    + (CASE WHEN coalesce(discharge, '') <> '' THEN 1 ELSE 0 END)) STORED,
  key_sources               text,
  evidence_observation_ids  text[] NOT NULL DEFAULT '{}',
  next_step                 text,
  last_checked              date,
  source_url                text,
  source_title              text,
  source_type               text,
  source_date               date,
  method                    text,
  confidence                numeric CHECK (confidence BETWEEN 0 AND 1),
  review_status             text NOT NULL DEFAULT 'Needs review',
  date_found                date,
  idempotency_key           text UNIQUE,
  created_at                timestamptz NOT NULL DEFAULT now(),
  updated_at                timestamptz NOT NULL DEFAULT now()
);

-- Who pays. value_usd is NULL when not quantified (never zero for "unknown").
CREATE TABLE who_pays (
  item_id                  text PRIMARY KEY CHECK (item_id ~ '^WP-[0-9]{4,}$'),
  facility_id              text REFERENCES facilities ON UPDATE RESTRICT ON DELETE RESTRICT,
  facility_label           text,
  category                 text,
  who_pays                 text,
  counterparty             text,                   -- Granting body or counterparty
  description              text,
  value_usd                numeric,                -- Value (USD)
  value_basis              text,
  term                     text,
  conditions               text,                   -- Conditions, clawbacks or caveats
  year                     integer CHECK (year BETWEEN 1900 AND 2200),
  evidence_observation_ids text[] NOT NULL DEFAULT '{}',
  next_step                text,
  last_checked             date,
  source_url               text,
  source_title             text,
  source_type              text,
  source_date              date,
  method                   text,
  confidence               numeric CHECK (confidence BETWEEN 0 AND 1),
  review_status            text NOT NULL DEFAULT 'Needs review',
  date_found               date,
  idempotency_key          text UNIQUE,
  created_at               timestamptz NOT NULL DEFAULT now(),
  updated_at               timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX who_pays_facility ON who_pays (facility_id);

-- Load reality: one row per facility. Score = sum of the six factors / 12 x 100, rounded; NULL if any
-- factor is unscored (unknown is not zero).
CREATE TABLE load_reality (
  facility_id          text PRIMARY KEY REFERENCES facilities ON UPDATE RESTRICT ON DELETE RESTRICT,
  facility_label       text,
  state                text,
  stated_load_mw       numeric CHECK (stated_load_mw >= 0),
  load_basis           text,
  load_source          text,
  operating_load_mw    numeric CHECK (operating_load_mw >= 0),
  operating_basis      text,
  status               text,
  land_control         smallint CHECK (land_control BETWEEN 0 AND 2),
  local_approvals      smallint CHECK (local_approvals BETWEEN 0 AND 2),
  power_secured        smallint CHECK (power_secured BETWEEN 0 AND 2),
  equipment_financing  smallint CHECK (equipment_financing BETWEEN 0 AND 2),
  construction         smallint CHECK (construction BETWEEN 0 AND 2),
  sponsor_tenant       smallint CHECK (sponsor_tenant BETWEEN 0 AND 2),
  deliverability_score smallint GENERATED ALWAYS AS (
    round((land_control + local_approvals + power_secured + equipment_financing + construction
           + sponsor_tenant) * 100 / 12.0)) STORED,
  tier                 text GENERATED ALWAYS AS (
    CASE
      WHEN round((land_control + local_approvals + power_secured + equipment_financing + construction
                  + sponsor_tenant) * 100 / 12.0) >= 90 THEN 'Built or near-certain'
      WHEN round((land_control + local_approvals + power_secured + equipment_financing + construction
                  + sponsor_tenant) * 100 / 12.0) >= 70 THEN 'Highly likely'
      WHEN round((land_control + local_approvals + power_secured + equipment_financing + construction
                  + sponsor_tenant) * 100 / 12.0) >= 45 THEN 'Plausible'
      WHEN round((land_control + local_approvals + power_secured + equipment_financing + construction
                  + sponsor_tenant) * 100 / 12.0) >= 25 THEN 'Early stage'
      WHEN round((land_control + local_approvals + power_secured + equipment_financing + construction
                  + sponsor_tenant) * 100 / 12.0) >= 0 THEN 'Speculative'
    END) STORED,
  -- Score-weighted MW (illustrative, not a forecast).
  score_weighted_mw    numeric GENERATED ALWAYS AS (
    stated_load_mw * round((land_control + local_approvals + power_secured + equipment_financing + construction
                            + sponsor_tenant) * 100 / 12.0) / 100) STORED,
  evidence_for_scores  text,
  red_flags            text,
  sources              text,
  next_step            text,
  last_checked         date,
  source_url           text,
  source_title         text,
  source_type          text,
  source_date          date,
  method               text,
  confidence           numeric CHECK (confidence BETWEEN 0 AND 1),
  review_status        text NOT NULL DEFAULT 'Needs review',
  date_found           date,
  idempotency_key      text UNIQUE,
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now()
);

-- Load reality, "Market context" block.
CREATE TABLE market_context (
  id              bigserial PRIMARY KEY,
  measure         text NOT NULL,
  value           text,                            -- text: some values are pairs, e.g. "331 / 195"
  unit            text,
  as_of           date,
  note            text,
  source_url      text,                            -- Source
  source_title    text,
  source_type     text,
  source_date     date,
  method          text,
  confidence      numeric CHECK (confidence BETWEEN 0 AND 1),
  review_status   text NOT NULL DEFAULT 'Needs review',
  date_found      date,
  idempotency_key text UNIQUE,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (measure, as_of)
);

-- Corrections. Personal contact details are never stored: there is no column for them, and the
-- importer drops any extra columns.
CREATE TABLE corrections (
  correction_id       text PRIMARY KEY,
  received            date,
  submitter_role      text CHECK (submitter_role !~ '@'),
  record_id           text,
  facility_id         text,
  issue               text,
  proposed            text,
  evidence_urls       text[] NOT NULL DEFAULT '{}',
  proposed_assessment text,                        -- Proposed assessment (agent)
  decision            text,
  decision_date       date,
  reviewer_note       text,
  ledger_change_made  text,
  public_log_id       text,
  review_status       text NOT NULL DEFAULT 'Needs review',
  idempotency_key     text UNIQUE,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now()
);

-- Sources & credits
CREATE TABLE source_registry (
  source        text PRIMARY KEY,
  what_we_use   text,
  licence       text,                              -- Licence / terms
  credit_line   text,                              -- Required credit line
  pulled        text,
  records_used  text,
  notes         text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

-- Run log: one row per agent run.
CREATE TABLE run_log (
  id               bigserial PRIMARY KEY,
  run_date         date NOT NULL,
  region           text,
  searches_run     integer CHECK (searches_run >= 0),
  new_observations integer CHECK (new_observations >= 0),
  changes_flagged  integer CHECK (changes_flagged >= 0),
  notes            text,                           -- Notes / errors
  agent            text,                           -- which scheduled agent ran (not in the sheet)
  idempotency_key  text UNIQUE,
  created_at       timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------------------------
-- Triggers
-- ---------------------------------------------------------------------------------------------

DO $$
DECLARE
  t record;
BEGIN
  FOR t IN SELECT * FROM (VALUES
      ('facilities', 'facility_id'), ('claims', 'claim_id'), ('permit_checks', 'check_id'),
      ('water_ledger', 'facility_id'), ('who_pays', 'item_id'), ('load_reality', 'facility_id'),
      ('market_context', 'id'), ('corrections', 'correction_id'),
      ('facility_names', 'id'), ('facility_links', 'id')) AS v(tbl, pk)
  LOOP
    EXECUTE format('CREATE TRIGGER %I BEFORE INSERT OR UPDATE OR DELETE ON %I
                    FOR EACH ROW EXECUTE FUNCTION ledger_protect_reviewed()', t.tbl || '_protect', t.tbl);
  END LOOP;

  FOR t IN SELECT * FROM (VALUES
      ('facilities', 'facility_id'), ('claims', 'claim_id'), ('permit_checks', 'check_id'),
      ('water_ledger', 'facility_id'), ('who_pays', 'item_id'), ('load_reality', 'facility_id'),
      ('market_context', 'id'), ('corrections', 'correction_id'), ('source_registry', 'source'),
      ('facility_names', 'id'), ('facility_links', 'id'), ('facility_aliases', 'alias_id'),
      ('observations', 'obs_id'), ('claim_log', 'id'), ('run_log', 'id'), ('api_keys', 'key_id')) AS v(tbl, pk)
  LOOP
    EXECUTE format('CREATE TRIGGER %I AFTER UPDATE OR DELETE ON %I
                    FOR EACH ROW EXECUTE FUNCTION ledger_audit(%L)', t.tbl || '_audit', t.tbl, t.pk);
  END LOOP;

  FOR t IN SELECT * FROM (VALUES
      ('facilities'), ('claims'), ('permit_checks'), ('water_ledger'), ('who_pays'), ('load_reality'),
      ('market_context'), ('corrections'), ('source_registry')) AS v(tbl)
  LOOP
    EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION ledger_touch()',
                   t.tbl || '_touch', t.tbl);
  END LOOP;
END
$$;
