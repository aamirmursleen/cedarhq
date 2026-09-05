CREATE TABLE users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  name TEXT,
  password_hash TEXT NOT NULL,
  auth_provider TEXT NOT NULL DEFAULT 'password',
  role TEXT NOT NULL DEFAULT 'founder',
  email_verified INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE sessions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash TEXT NOT NULL UNIQUE,
  csrf_token TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  user_agent TEXT,
  ip_hash TEXT
);

CREATE TABLE email_tokens (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_type TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  expires_at TEXT NOT NULL,
  used_at TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE outbox_emails (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  external_id TEXT NOT NULL,
  to_email TEXT NOT NULL,
  subject TEXT NOT NULL,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE companies (
  id TEXT PRIMARY KEY,
  owner_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  legal_name TEXT,
  entity_type TEXT,
  state_code TEXT,
  status TEXT NOT NULL DEFAULT 'draft',
  business_purpose TEXT,
  industry TEXT,
  address_line1 TEXT,
  address_line2 TEXT,
  city TEXT,
  region TEXT,
  postal_code TEXT,
  country TEXT,
  name_choice_1 TEXT,
  name_choice_2 TEXT,
  name_choice_3 TEXT,
  share_count INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE company_members (
  id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(company_id, user_id)
);

CREATE TABLE company_founders (
  id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  full_name TEXT NOT NULL,
  email TEXT NOT NULL,
  ownership_percent REAL NOT NULL,
  shares INTEGER,
  address_line1 TEXT,
  city TEXT,
  region TEXT,
  postal_code TEXT,
  country TEXT,
  identity_status TEXT NOT NULL DEFAULT 'not_started',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE onboarding_progress (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  company_id TEXT REFERENCES companies(id) ON DELETE SET NULL,
  current_step TEXT NOT NULL DEFAULT 'quiz',
  data_json TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL,
  UNIQUE(user_id)
);

CREATE TABLE plans (
  id TEXT PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  description TEXT NOT NULL,
  service_fee_cents INTEGER NOT NULL,
  renewal_fee_cents INTEGER NOT NULL,
  registered_agent_included INTEGER NOT NULL DEFAULT 0,
  mailroom_included INTEGER NOT NULL DEFAULT 0,
  bookkeeping_included INTEGER NOT NULL DEFAULT 0,
  tax_included INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE payments (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  company_id TEXT REFERENCES companies(id) ON DELETE SET NULL,
  provider TEXT NOT NULL,
  status TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  currency TEXT NOT NULL,
  receipt_id TEXT NOT NULL,
  external_id TEXT NOT NULL,
  is_sandbox INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE formation_orders (
  id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE RESTRICT,
  payment_id TEXT REFERENCES payments(id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'draft',
  entity_type TEXT NOT NULL,
  state_code TEXT NOT NULL,
  total_first_year_cents INTEGER NOT NULL,
  total_renewal_cents INTEGER NOT NULL,
  state_fee_cents INTEGER NOT NULL,
  service_fee_cents INTEGER NOT NULL,
  renewal_fee_cents INTEGER NOT NULL,
  sandbox INTEGER NOT NULL DEFAULT 1,
  blocked_reason TEXT,
  assigned_staff_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL,
  submitted_at TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE evidence_files (
  id TEXT PRIMARY KEY,
  company_id TEXT REFERENCES companies(id) ON DELETE CASCADE,
  order_id TEXT REFERENCES formation_orders(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  storage_key TEXT NOT NULL,
  content TEXT NOT NULL,
  mime_type TEXT NOT NULL,
  receipt_id TEXT NOT NULL,
  provider TEXT NOT NULL,
  is_simulated INTEGER NOT NULL DEFAULT 1,
  created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE formation_steps (
  id TEXT PRIMARY KEY,
  order_id TEXT NOT NULL REFERENCES formation_orders(id) ON DELETE CASCADE,
  step_key TEXT NOT NULL,
  label TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  responsible_party TEXT NOT NULL,
  completed_at TEXT,
  actor_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  receipt_id TEXT,
  evidence_id TEXT REFERENCES evidence_files(id) ON DELETE SET NULL,
  blocked_reason TEXT,
  sort_order INTEGER NOT NULL,
  UNIQUE(order_id, step_key)
);

CREATE TABLE documents (
  id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  order_id TEXT REFERENCES formation_orders(id) ON DELETE SET NULL,
  title TEXT NOT NULL,
  category TEXT NOT NULL,
  status TEXT NOT NULL,
  current_version INTEGER NOT NULL DEFAULT 1,
  source TEXT NOT NULL,
  evidence_id TEXT REFERENCES evidence_files(id) ON DELETE SET NULL,
  is_simulated INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE document_versions (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  version INTEGER NOT NULL,
  content TEXT NOT NULL,
  mime_type TEXT NOT NULL,
  created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL,
  UNIQUE(document_id, version)
);

CREATE TABLE compliance_items (
  id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  category TEXT NOT NULL,
  status TEXT NOT NULL,
  due_date TEXT NOT NULL,
  responsible_party TEXT NOT NULL,
  description TEXT NOT NULL,
  source_rule TEXT NOT NULL,
  next_escalation_at TEXT,
  submitted_at TEXT,
  accepted_at TEXT,
  rejected_at TEXT,
  receipt_id TEXT,
  evidence_id TEXT REFERENCES evidence_files(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE compliance_events (
  id TEXT PRIMARY KEY,
  compliance_item_id TEXT NOT NULL REFERENCES compliance_items(id) ON DELETE CASCADE,
  actor_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  event_type TEXT NOT NULL,
  from_status TEXT,
  to_status TEXT,
  note TEXT,
  evidence_id TEXT REFERENCES evidence_files(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE provider_events (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  service TEXT NOT NULL,
  event_type TEXT NOT NULL,
  external_id TEXT NOT NULL,
  status TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  is_simulated INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);

CREATE TABLE support_tickets (
  id TEXT PRIMARY KEY,
  company_id TEXT REFERENCES companies(id) ON DELETE SET NULL,
  opened_by_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  assigned_staff_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  subject TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  priority TEXT NOT NULL DEFAULT 'normal',
  deadline_critical INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE ticket_messages (
  id TEXT PRIMARY KEY,
  ticket_id TEXT NOT NULL REFERENCES support_tickets(id) ON DELETE CASCADE,
  author_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  body TEXT NOT NULL,
  attachment_document_id TEXT REFERENCES documents(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE mail_items (
  id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'received',
  sender TEXT,
  envelope_preview_document_id TEXT REFERENCES documents(id) ON DELETE SET NULL,
  tracking_number TEXT,
  forwarding_cost_cents INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE banking_applications (
  id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  partner_name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'checklist',
  checklist_json TEXT NOT NULL DEFAULT '[]',
  disclaimer TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE activity_logs (
  id TEXT PRIMARY KEY,
  actor_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  company_id TEXT REFERENCES companies(id) ON DELETE SET NULL,
  order_id TEXT REFERENCES formation_orders(id) ON DELETE SET NULL,
  event_type TEXT NOT NULL,
  summary TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE INDEX idx_sessions_token ON sessions(token_hash);
CREATE INDEX idx_companies_owner ON companies(owner_user_id);
CREATE INDEX idx_orders_user ON formation_orders(user_id);
CREATE INDEX idx_orders_status ON formation_orders(status);
CREATE INDEX idx_steps_order ON formation_steps(order_id, sort_order);
CREATE INDEX idx_documents_company ON documents(company_id, category);
CREATE INDEX idx_compliance_company_due ON compliance_items(company_id, due_date);
CREATE INDEX idx_activity_company ON activity_logs(company_id, created_at);

