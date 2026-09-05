# CedarHQ Product Plan

## Repository Audit

- `/root` was not a Git repository.
- Existing worktrees under `/root` were unrelated products or dirty worktrees.
- CedarHQ was created as a clean Git worktree at `/root/cedarhq`.

## Architecture

- Runtime: Python 3.12 standard library HTTP server.
- Persistence: SQLite with checked-in migrations.
- Frontend: mobile-first server-rendered HTML plus vanilla JavaScript autosave.
- Auth: email/password, sandbox Google sign-in adapter, email verification, password reset, signed session cookies, CSRF tokens, and role-gated routes.
- Providers: checkout, formation, document, compliance reminder, email, banking, mail, accounting, tax, e-commerce, and AI are adapter boundaries. MVP uses sandbox adapters.
- Operations: separate founder and operations/admin experiences.
- Evidence policy: no timeline step may be displayed as complete without timestamp, responsible party, receipt, and downloadable evidence.

## Feature Matrix

| Area | MVP | Later |
| --- | --- | --- |
| Auth | Email/password, sandbox Google, verification/reset, sessions, roles | SSO, MFA, account recovery reviews |
| Formation | New company flow, entity quiz, state selection, founder details, checkout, order | Add existing company import, multi-founder cap table, real filing provider |
| Timeline | Evidence-backed completed steps, blockers, staff transitions | Customer notifications, provider webhooks |
| Documents | Vault, categories, preview, download, versions, generated docs | Encrypted object storage, real upload preview, e-sign provider |
| Compliance | Calendar, due dates, statuses, receipts, audit events | Jurisdiction rule engine, escalation queue, email/SMS |
| Registered Agent | State coverage, renewal evidence, notices, foreign qualification intake | Real RA provider, real scanned notices, annual/franchise filing APIs |
| Mailroom | Address selection, Form 1583 status, incoming mail, scan/forward/recycle/archive, staff queue | Real mailroom provider, carrier labels, postage billing |
| Banking | Partner banking/payment/payroll checklists, sandbox status evidence, approval caveats | Real partner referral/API credentials and webhooks |
| Bookkeeping | Sandbox multi-account connection, transaction feed, categorization, reconciliation, P&L/balance/cash summary, monthly close, CSV | Real aggregation provider, receipt OCR, accountant messaging |
| Taxes | Questionnaires, document checklist, founder/staff workflow, sandbox evidence, Form 1120/5472/1099/state/city/extension records | CPA scheduling and real e-file provider |
| Sales Tax | Sandbox connector, nexus monitor, product tax-code mapping, approval-gated returns | Real sales-tax provider, registrations, filings, payments |
| E-commerce | Shopify/Amazon sandbox adapters, date filters, revenue/orders/costs/margin/payout trends, CSV | Real Shopify and Amazon credentials/webhooks |
| Rewards/Discovery | Partner reward catalog and explicit investor-discovery opt-in | Real partner redemption and investor CRM integrations |
| AI assistant | Context-aware database answers, visible sources, approval-gated consequential requests | Curated source corpus and production AI provider |
| Billing | Plans, transparent first-year/renewal cost, sandbox checkout | Stripe subscriptions/webhooks |
| Support | Schema and route stubs | SLA timers, shared case status |
| Admin/Ops | Review queue, status transition actions, audit trail | Assignment queues, risk dashboards |

## Status Machines

### Formation Order

`draft -> checkout_pending -> paid -> information_received -> operations_review -> state_submission_ready -> state_submitted -> state_approved -> ein_submitted -> ein_received -> bank_ready`

Side states: `blocked`, `cancelled`.

Every completed operational step requires an evidence record before status transition.

### Compliance

`upcoming -> action_required -> submitted -> accepted`

Alternative states: `rejected`, `overdue`, `waived`.

### Payment

`created -> sandbox_paid -> failed -> refunded`

### Document

`draft -> generated -> pending_signature -> signed -> archived`

### Tax Filing

`questionnaire -> documents_pending -> preparation -> founder_review -> signature_required -> ready_to_submit -> submitted -> accepted`

Alternative states: `blocked`, `rejected`. Submission/acceptance in the MVP is sandbox-only and creates evidence.

### Bookkeeping

Transactions: `uncategorized -> categorized -> reconciled`.

Monthly close: `not_started -> in_progress -> review_ready -> closed`.

### Assistant Action

`pending_approval -> approved | rejected`. Approval does not execute an external action.

### Mailroom

`received -> scan_requested -> scanned`

Alternative actions: `forward_requested -> forwarded`, `archive_requested -> archived`, `recycle_requested -> recycled`.

### Partner Application

`checklist -> ready_to_send -> sent_to_partner -> partner_review -> approved | declined`

Alternative state: `more_info_required`. Approval is always recorded as a partner-controlled decision.

### Sales Tax Return

`nexus_review -> registration_required -> registered -> return_preparation -> ready_for_approval -> approved_to_file -> submitted -> accepted`

Alternative states: `not_required`, `blocked`, `rejected`.

## Route Map

- `/`, `/signup`, `/login`, `/logout`, `/verify-email`, `/forgot-password`, `/reset-password`, `/auth/google`
- `/app`, `/app/onboarding`, `/app/orders/<id>`, `/app/documents`, `/app/compliance`, `/app/billing`, `/app/support`
- `/app/assistant`, `/app/bookkeeping`, `/app/taxes`, `/app/analytics`
- `/app/registered-agent`, `/app/mailroom`, `/app/partners`, `/app/equity`, `/app/rewards`, `/app/sales-tax`
- `/ops`, `/ops/orders`, `/ops/orders/<id>`, `/ops/compliance`, `/ops/mailroom`, `/ops/taxes`, `/ops/audit`
- `/api/onboarding/save`, `/api/checkout/sandbox`, `/api/ops/orders/<id>/transition`, `/api/documents/<id>/download`, `/api/jobs/reminders`
- `/api/assistant/message`, `/api/assistant/actions/<id>`
- `/api/bookkeeping/connect-sandbox`, `/api/bookkeeping/transactions/<id>`, `/api/bookkeeping/closes/<id>`, `/api/bookkeeping/export.csv`
- `/api/taxes/start`, `/api/taxes/<id>/save`, `/api/taxes/<id>/action`, `/api/ops/taxes/<id>/action`
- `/api/commerce/connect-sandbox`, `/api/analytics/export.csv`
- `/api/mailroom/address`, `/api/mailroom/items/<id>/action`, `/api/ops/mailroom/<id>/process`
- `/api/registered-agent/foreign-qualification`
- `/api/partners/applications/<id>/action`
- `/api/rewards/discovery`
- `/api/sales-tax/connect-sandbox`, `/api/sales-tax/returns/<id>/action`

## External Credentials Eventually Required

- Google OAuth client id/secret
- Stripe secret key, webhook secret, products/prices
- Transactional email provider credentials
- Secure object storage and KMS
- Government/legal filing provider credentials
- Registered-agent provider credentials
- Mailroom scanning/forwarding provider credentials
- E-signature provider credentials
- Banking partner referral/API credentials
- Accounting aggregation provider credentials
- Shopify app credentials and Amazon SP-API credentials
- AI provider key and curated legal/tax/compliance source corpus
