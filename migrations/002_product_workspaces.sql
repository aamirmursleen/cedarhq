CREATE TABLE financial_accounts (
  id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  provider TEXT NOT NULL,
  external_id TEXT NOT NULL,
  institution_name TEXT NOT NULL,
  account_name TEXT NOT NULL,
  account_type TEXT NOT NULL,
  mask TEXT,
  currency TEXT NOT NULL DEFAULT 'USD',
  status TEXT NOT NULL DEFAULT 'pending',
  balance_cents INTEGER NOT NULL DEFAULT 0,
  is_sandbox INTEGER NOT NULL DEFAULT 1,
  last_synced_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(company_id, provider, external_id)
);

CREATE TABLE bookkeeping_transactions (
  id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  account_id TEXT NOT NULL REFERENCES financial_accounts(id) ON DELETE CASCADE,
  external_id TEXT NOT NULL,
  posted_at TEXT NOT NULL,
  description TEXT NOT NULL,
  merchant TEXT,
  amount_cents INTEGER NOT NULL,
  category TEXT,
  status TEXT NOT NULL DEFAULT 'uncategorized',
  receipt_document_id TEXT REFERENCES documents(id) ON DELETE SET NULL,
  notes TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(account_id, external_id)
);

CREATE TABLE categorization_rules (
  id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  match_text TEXT NOT NULL,
  category TEXT NOT NULL,
  created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE bookkeeping_invoices (
  id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  customer_name TEXT NOT NULL,
  invoice_number TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft',
  due_date TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(company_id, invoice_number)
);

CREATE TABLE monthly_closes (
  id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  month TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'not_started',
  assigned_accountant_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  completed_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(company_id, month)
);

CREATE TABLE tax_filings (
  id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  tax_year INTEGER NOT NULL,
  filing_type TEXT NOT NULL,
  jurisdiction TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'questionnaire',
  due_date TEXT NOT NULL,
  extension_requested INTEGER NOT NULL DEFAULT 0,
  responsible_party TEXT NOT NULL DEFAULT 'Founder and CedarHQ tax team',
  receipt_id TEXT,
  evidence_id TEXT REFERENCES evidence_files(id) ON DELETE SET NULL,
  is_sandbox INTEGER NOT NULL DEFAULT 1,
  submitted_at TEXT,
  accepted_at TEXT,
  rejected_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(company_id, tax_year, filing_type, jurisdiction)
);

CREATE TABLE tax_questionnaire_answers (
  id TEXT PRIMARY KEY,
  filing_id TEXT NOT NULL REFERENCES tax_filings(id) ON DELETE CASCADE,
  question_key TEXT NOT NULL,
  question_label TEXT NOT NULL,
  answer TEXT NOT NULL DEFAULT '',
  required INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL,
  UNIQUE(filing_id, question_key)
);

CREATE TABLE tax_required_documents (
  id TEXT PRIMARY KEY,
  filing_id TEXT NOT NULL REFERENCES tax_filings(id) ON DELETE CASCADE,
  label TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'required',
  document_id TEXT REFERENCES documents(id) ON DELETE SET NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(filing_id, label)
);

CREATE TABLE commerce_connections (
  id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  provider TEXT NOT NULL,
  external_shop_id TEXT NOT NULL,
  display_name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  is_sandbox INTEGER NOT NULL DEFAULT 1,
  last_synced_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(company_id, provider)
);

CREATE TABLE commerce_daily_metrics (
  id TEXT PRIMARY KEY,
  connection_id TEXT NOT NULL REFERENCES commerce_connections(id) ON DELETE CASCADE,
  metric_date TEXT NOT NULL,
  revenue_cents INTEGER NOT NULL DEFAULT 0,
  orders_count INTEGER NOT NULL DEFAULT 0,
  fees_cents INTEGER NOT NULL DEFAULT 0,
  refunds_cents INTEGER NOT NULL DEFAULT 0,
  ad_spend_cents INTEGER NOT NULL DEFAULT 0,
  cogs_cents INTEGER NOT NULL DEFAULT 0,
  payouts_cents INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  UNIQUE(connection_id, metric_date)
);

CREATE TABLE assistant_threads (
  id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title TEXT NOT NULL DEFAULT 'Business assistant',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE assistant_messages (
  id TEXT PRIMARY KEY,
  thread_id TEXT NOT NULL REFERENCES assistant_threads(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  citations_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL
);

CREATE TABLE assistant_actions (
  id TEXT PRIMARY KEY,
  thread_id TEXT NOT NULL REFERENCES assistant_threads(id) ON DELETE CASCADE,
  requested_by_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  action_type TEXT NOT NULL,
  summary TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'pending_approval',
  approved_at TEXT,
  rejected_at TEXT,
  executed_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX idx_financial_accounts_company ON financial_accounts(company_id, status);
CREATE INDEX idx_bookkeeping_transactions_company_date ON bookkeeping_transactions(company_id, posted_at);
CREATE INDEX idx_tax_filings_company ON tax_filings(company_id, tax_year, status);
CREATE INDEX idx_commerce_metrics_date ON commerce_daily_metrics(connection_id, metric_date);
CREATE INDEX idx_assistant_threads_company ON assistant_threads(company_id, updated_at);
