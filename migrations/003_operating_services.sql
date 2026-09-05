CREATE TABLE mail_addresses (
  id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  label TEXT NOT NULL,
  address_line1 TEXT NOT NULL,
  city TEXT NOT NULL,
  state_code TEXT NOT NULL,
  postal_code TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'available',
  monthly_fee_cents INTEGER NOT NULL DEFAULT 3500,
  form_1583_status TEXT NOT NULL DEFAULT 'not_started',
  selected_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(company_id, label)
);

ALTER TABLE mail_items ADD COLUMN mail_address_id TEXT REFERENCES mail_addresses(id) ON DELETE SET NULL;
ALTER TABLE mail_items ADD COLUMN mail_type TEXT NOT NULL DEFAULT 'letter';
ALTER TABLE mail_items ADD COLUMN recipient_name TEXT;
ALTER TABLE mail_items ADD COLUMN action_requested_at TEXT;
ALTER TABLE mail_items ADD COLUMN processed_by_user_id TEXT REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE mail_items ADD COLUMN scan_document_id TEXT REFERENCES documents(id) ON DELETE SET NULL;
ALTER TABLE mail_items ADD COLUMN archived_at TEXT;

CREATE TABLE mail_events (
  id TEXT PRIMARY KEY,
  mail_item_id TEXT NOT NULL REFERENCES mail_items(id) ON DELETE CASCADE,
  actor_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  from_status TEXT,
  to_status TEXT NOT NULL,
  note TEXT,
  evidence_id TEXT REFERENCES evidence_files(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE registered_agent_services (
  id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  state_code TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  annual_fee_cents INTEGER NOT NULL DEFAULT 29900,
  renewal_date TEXT NOT NULL,
  evidence_id TEXT REFERENCES evidence_files(id) ON DELETE SET NULL,
  provider TEXT NOT NULL DEFAULT 'sandbox_registered_agent',
  is_simulated INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(company_id, state_code)
);

CREATE TABLE registered_agent_notices (
  id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  service_id TEXT REFERENCES registered_agent_services(id) ON DELETE SET NULL,
  state_code TEXT NOT NULL,
  title TEXT NOT NULL,
  category TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'received',
  received_at TEXT NOT NULL,
  due_date TEXT,
  document_id TEXT REFERENCES documents(id) ON DELETE SET NULL,
  evidence_id TEXT REFERENCES evidence_files(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE foreign_qualifications (
  id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  state_code TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'questionnaire',
  estimated_state_fee_cents INTEGER NOT NULL DEFAULT 25000,
  reason TEXT NOT NULL DEFAULT '',
  receipt_id TEXT,
  evidence_id TEXT REFERENCES evidence_files(id) ON DELETE SET NULL,
  submitted_at TEXT,
  approved_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(company_id, state_code)
);

CREATE TABLE partner_applications (
  id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  partner_type TEXT NOT NULL,
  partner_name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'checklist',
  checklist_json TEXT NOT NULL DEFAULT '[]',
  disclaimer TEXT NOT NULL,
  receipt_id TEXT,
  evidence_id TEXT REFERENCES evidence_files(id) ON DELETE SET NULL,
  sent_at TEXT,
  decided_at TEXT,
  is_simulated INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(company_id, partner_type, partner_name)
);

CREATE TABLE discovery_profiles (
  id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'draft',
  founder_headline TEXT NOT NULL DEFAULT '',
  target_investor TEXT NOT NULL DEFAULT '',
  permission_to_share INTEGER NOT NULL DEFAULT 0,
  updated_by_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(company_id)
);

CREATE TABLE partner_rewards (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  category TEXT NOT NULL,
  estimated_value_cents INTEGER NOT NULL DEFAULT 0,
  eligibility TEXT NOT NULL,
  redemption_status TEXT NOT NULL DEFAULT 'available',
  is_simulated INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);

CREATE TABLE sales_tax_accounts (
  id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  provider TEXT NOT NULL DEFAULT 'sandbox_sales_tax',
  status TEXT NOT NULL DEFAULT 'not_connected',
  external_id TEXT,
  last_synced_at TEXT,
  is_simulated INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(company_id, provider)
);

CREATE TABLE sales_tax_nexus (
  id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  state_code TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'monitoring',
  threshold_cents INTEGER NOT NULL,
  trailing_revenue_cents INTEGER NOT NULL DEFAULT 0,
  trailing_orders INTEGER NOT NULL DEFAULT 0,
  next_review_date TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(company_id, state_code)
);

CREATE TABLE sales_tax_returns (
  id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  state_code TEXT NOT NULL,
  period TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'nexus_review',
  due_date TEXT NOT NULL,
  tax_collected_cents INTEGER NOT NULL DEFAULT 0,
  receipt_id TEXT,
  evidence_id TEXT REFERENCES evidence_files(id) ON DELETE SET NULL,
  submitted_at TEXT,
  accepted_at TEXT,
  rejected_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(company_id, state_code, period)
);

CREATE TABLE sales_tax_products (
  id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  sku TEXT NOT NULL,
  name TEXT NOT NULL,
  tax_code TEXT NOT NULL DEFAULT 'general_tangible_goods',
  status TEXT NOT NULL DEFAULT 'mapped',
  updated_at TEXT NOT NULL,
  UNIQUE(company_id, sku)
);

CREATE INDEX idx_mail_items_company_status ON mail_items(company_id, status);
CREATE INDEX idx_ra_notices_company_status ON registered_agent_notices(company_id, status);
CREATE INDEX idx_foreign_qualifications_company ON foreign_qualifications(company_id, status);
CREATE INDEX idx_partner_applications_company ON partner_applications(company_id, status);
CREATE INDEX idx_sales_tax_returns_company_due ON sales_tax_returns(company_id, due_date);
